#include "block_conversion_plugin.hpp"
#include "blockchain_plugin.hpp"
#include "channels.hpp"
#include "abi_utils.hpp"
#include "utils.hpp"
#include <eosevm/block_mapping.hpp>
#include <eosevm/consensus_parameters.hpp>
#include <eosevm/version.hpp>

#include <fstream>

#include <silkworm/core/types/transaction.hpp>
#include <silkworm/core/trie/vector_root.hpp>
#include <silkworm/core/common/endian.hpp>
#include <silkworm/node/db/access_layer.hpp>

using sys = sys_plugin;

silkworm::log::BufferBase& operator << ( silkworm::log::BufferBase& ss, const eosio::checksum256& id ) {
   ss << silkworm::to_hex(id.extract_as_byte_array());
   return ss;
}

silkworm::log::BufferBase& operator << ( silkworm::log::BufferBase& ss, const channels::native_block& block ) {
   ss << "#" << block.block_num << " ("
      << "id:" << block.id << ","
      << "prev:" << block.prev
      << ")";
   return ss;
}

silkworm::log::BufferBase& operator << ( silkworm::log::BufferBase& ss, const silkworm::Block& block ) {
   ss << "#" << block.header.number << ", txs:" << block.transactions.size() << ", hash:" << silkworm::to_hex(block.header.hash());
   return ss;
}

class block_conversion_plugin_impl : std::enable_shared_from_this<block_conversion_plugin_impl> {
   public:
      block_conversion_plugin_impl()
        : evm_blocks_channel(appbase::app().get_channel<channels::evm_blocks>()){}


      uint32_t timestamp_to_evm_block_num(uint64_t timestamp_us) const {
         uint64_t timestamp_s = timestamp_us / 1e6;
         if (timestamp_s < bm.value().genesis_timestamp) {
            SILK_CRIT << "Invalid timestamp " << timestamp_s << ", genesis: " << bm->genesis_timestamp;
            assert(timestamp_s >= bm->genesis_timestamp);
         }

         return bm->timestamp_to_evm_block_num(timestamp_us);
      }

      void load_head() {
         auto start_from_canonical_height = appbase::app().get_plugin<ship_receiver_plugin>().get_start_from_canonical_height();
         auto start_block = appbase::app().get_plugin<engine_plugin>().get_canonical_block_at_height(start_from_canonical_height);
         if (!start_block) {
            sys::error("Unable to read head block");
            return;
         }
         SILK_INFO << "load_head: " << *start_block;
         evm_blocks.push_back(*start_block);

         channels::native_block nb;

         nb.id = eosio::checksum256(start_block->header.prev_randao.bytes);
         nb.block_num = utils::to_block_num(start_block->header.prev_randao.bytes);
         nb.timestamp = start_block->header.timestamp*1e6;

         SILK_INFO << "Loaded native block: [" << start_block->header.number << "][" << nb.block_num << "],[" << nb.timestamp << "]";
         native_blocks.push_back(nb);

         auto genesis_header = appbase::app().get_plugin<engine_plugin>().get_genesis_header();
         if (!genesis_header) {
            sys::error("Unable to read genesis header");
            return;
         }

         bm.emplace(genesis_header->timestamp, 1); // Hardcoded to 1 second block interval

         SILK_INFO << "Block interval (in seconds): " << bm->block_interval;
         SILK_INFO << "Genesis timestamp (in seconds since Unix epoch): " << bm->genesis_timestamp;

         // The nonce in the genesis header encodes the name of the Antelope account on which the EVM contract has been deployed.
         // This name is necessary to determine which reserved address to use as the beneficiary of the blocks.
         evm_contract_name = silkworm::endian::load_big_u64(genesis_header->nonce.data());

         SILK_INFO << "Genesis nonce (as hex): " << silkworm::to_hex(evm_contract_name, true);
         SILK_INFO << "Genesis nonce (as Antelope name): " << eosio::name{evm_contract_name}.to_string();

         if (evm_contract_name == 0  || (evm_contract_name == 1000)) {
            // TODO: Remove the (evm_contract_name == 1000) condition once we feel comfortable other tests and scripts have been 
            //       updated to reflect this new meaning of the nonce (used to be block interval in milliseconds).

            SILK_CRIT << "Genesis nonce does not represent a valid Antelope account name. "
                         "It must be the name of the account on which the EVM contract is deployed";
            sys::error("Invalid genesis nonce");
            return;
         }
      }

      evmc::bytes32 compute_transaction_root(const silkworm::BlockBody& body) {
         static constexpr auto kEncoder = [](silkworm::Bytes& to, const silkworm::Transaction& txn) {
            silkworm::rlp::encode(to, txn, /*wrap_eip2718_into_string=*/false);
         };
         return silkworm::trie::root_hash(body.transactions, kEncoder);
      }

      void log_internal_status(const std::string& label) {
         SILK_INFO << "internal_status(" << label << "): nb:" << native_blocks.size() << ", evmb:" << evm_blocks.size();
      }

      silkworm::Block generate_new_evm_block(const silkworm::Block& last_evm_block) {
         silkworm::Block new_block;
         uint64_t eos_evm_version = 0;
         if( last_evm_block.header.number == 0 ) {
            auto existing_config{appbase::app().get_plugin<engine_plugin>().get_chain_config()};
            if(existing_config.has_value() && existing_config.value()._version.has_value())
               eos_evm_version = existing_config.value()._version.value();
         } else {
            eos_evm_version = eosevm::nonce_to_version(last_evm_block.header.nonce);
         }

         std::optional<uint64_t> base_fee_per_gas;
         if(last_evm_block.header.base_fee_per_gas.has_value()) {
            base_fee_per_gas = static_cast<uint64_t>(last_evm_block.header.base_fee_per_gas.value());
         }
         eosevm::prepare_block_header(new_block.header, bm.value(), evm_contract_name, last_evm_block.header.number+1, eos_evm_version, base_fee_per_gas);

         new_block.header.parent_hash = last_evm_block.header.hash();
         new_block.header.transactions_root = silkworm::kEmptyRoot;
         // Note: can be null
         new_block.set_consensus_parameter_index(last_evm_block.get_consensus_parameter_index());
         return new_block;
      }

      // Set the prev_randao header field of the `evm_block` to the `native_block` id
      void set_upper_bound(silkworm::Block& evm_block, const channels::native_block& native_block) {
         auto id = native_block.id.extract_as_byte_array();
         static_assert(sizeof(decltype(id)) == sizeof(decltype(evm_block.header.prev_randao.bytes)));
         std::copy(id.begin(), id.end(), evm_block.header.prev_randao.bytes);
         evm_block.irreversible = native_block.block_num <= native_block.lib;
      }

      template <typename F>
      void for_each_action(const channels::native_block& block, F&& f) {
         for(const auto& trx: block.transactions) {
            for(const auto& act: trx.actions) {
               f(act);
            }
         }
      }

      template <typename F>
      void for_each_reverse_action(const channels::native_block& block, F&& f) {
         for(auto trx=block.transactions.rbegin(); trx != block.transactions.rend(); ++trx) {
            for(auto act=trx->actions.rbegin(); act != trx->actions.rend(); ++act) {
               f(*act);
            }
         }
      }

      inline void init() {
         SILK_DEBUG << "block_conversion_plugin_impl INIT";
         load_head();
         
         native_blocks_subscription = appbase::app().get_channel<channels::native_blocks>().subscribe(
            [this](auto new_block) {
               static size_t count = 0;

               // Keep the last block before genesis timestamp
               if (new_block->timestamp <= bm.value().genesis_timestamp) {
                  SILK_WARN << "Before genesis: " << bm->genesis_timestamp <<  " Block #" << new_block->block_num << " timestamp: " << new_block->timestamp;
                  native_blocks.clear();
                  native_blocks.push_back(*new_block);
                  return;
               }

               // Check if received native block can't be linked
               if( !native_blocks.empty() && native_blocks.back().id != new_block->prev ) {

                  SILK_WARN << "Can't link new block " << *new_block;

                  // Double check if it's duplicated ones.
                  // We do not need to import block again during reconnection if it's in this cache.
                  auto dup_block = std::find_if(native_blocks.begin(), native_blocks.end(), [&new_block](const auto& nb){ return nb.id == new_block->id; });
                  if( dup_block != native_blocks.end() ) {
                     SILK_WARN << "Receiving duplicated blocks " << new_block->id <<  " It's normal if it's caused by reconnection to SHiP.";
                     return;
                  }

                  // Find fork block
                  auto fork_block = std::find_if(native_blocks.begin(), native_blocks.end(), [&new_block](const auto& nb){ return nb.id == new_block->prev; });
                  if( fork_block == native_blocks.end() ) {
                     SILK_CRIT << "Unable to find fork block " << new_block->prev;
                     throw std::runtime_error("Unable to find fork block");
                  }

                  SILK_WARN << "Fork at Block " << *fork_block;

                  // Remove EVM blocks after the fork
                  while( !evm_blocks.empty() && timestamp_to_evm_block_num(fork_block->timestamp) < evm_blocks.back().header.number ) {
                     SILK_WARN << "Removing forked EVM block " << evm_blocks.back();
                     evm_blocks.pop_back();
                  }

                  // Remove forked native blocks up until the fork point
                  while( !native_blocks.empty() && native_blocks.back().id != fork_block->id ) {

                     // Check if the native block to be removed has transactions
                     // and they belong to the EVM block of the fork point
                     if( native_blocks.back().transactions.size() > 0 && timestamp_to_evm_block_num(native_blocks.back().timestamp) == timestamp_to_evm_block_num(fork_block->timestamp) ) {

                        // Check that we can remove transactions contained in the forked native block
                        if (evm_blocks.empty() || timestamp_to_evm_block_num(native_blocks.back().timestamp) != evm_blocks.back().header.number) {
                           SILK_CRIT << "Unable to remove transactions"
                                       << "(empty: " << evm_blocks.empty()
                                       << ", evmblock(native):" << timestamp_to_evm_block_num(native_blocks.back().timestamp) <<")"
                                       << ", evm number:" << evm_blocks.back().header.number <<")";
                           throw std::runtime_error("Unable to remove transactions");
                        }

                        // Remove transactions in forked native block
                        SILK_WARN << "Removing transactions in forked native block  " << native_blocks.back();
                        for_each_reverse_action(native_blocks.back(), [this](const auto& act){
                              auto dtx = deserialize_tx(act);
                              auto& rlpx_ref = std::visit([](auto&& arg) -> auto& { return arg.rlpx; }, dtx);
                              auto txid_a = ethash::keccak256(rlpx_ref.data(), rlpx_ref.size());

                              silkworm::Bytes transaction_rlp{};
                              silkworm::rlp::encode(transaction_rlp, evm_blocks.back().transactions.back());
                              auto txid_b = ethash::keccak256(transaction_rlp.data(), transaction_rlp.size());

                              // Ensure that the transaction to be removed is the correct one
                              if( std::memcmp(txid_a.bytes, txid_b.bytes, sizeof(txid_a.bytes)) != 0) {
                                 SILK_CRIT << "Unable to remove transaction "
                                             << ",txid_a:" << silkworm::to_hex(txid_a.bytes)
                                             << ",txid_b:" << silkworm::to_hex(txid_b.bytes);
                                 throw std::runtime_error("Unable to remove transaction");
                              }

                              SILK_WARN << "Removing trx: " << silkworm::to_hex(txid_a.bytes);
                              evm_blocks.back().transactions.pop_back();
                        });
                     }

                     // Remove forked native block
                     SILK_WARN << "Removing forked native block " << native_blocks.back();
                     native_blocks.pop_back();
                  }

                  // Ensure upper bound native block correspond to this EVM block
                  if( evm_blocks.empty() || timestamp_to_evm_block_num(native_blocks.back().timestamp) != evm_blocks.back().header.number ) {
                     SILK_CRIT << "Unable to set upper bound "
                                 << "(empty: " << evm_blocks.empty()
                                 << ", evmblock(native):" << timestamp_to_evm_block_num(native_blocks.back().timestamp) <<")"
                                 << ", evm number:" << evm_blocks.back().header.number <<")";
                     throw std::runtime_error("Unable to set upper bound");
                  }

                  // Reset upper bound
                  SILK_WARN << "Reset upper bound for EVM Block " << evm_blocks.back() << " to: " << native_blocks.back();
                  set_upper_bound(evm_blocks.back(), native_blocks.back());
               }

               // Enqueue received block
               native_blocks.push_back(*new_block);

               // Extend the EVM chain if necessary up until the block where the received block belongs
               auto evm_num = timestamp_to_evm_block_num(new_block->timestamp);

               while(evm_blocks.back().header.number < evm_num) {
                  auto& last_evm_block = evm_blocks.back();
                  last_evm_block.header.transactions_root = compute_transaction_root(last_evm_block);
                  evm_blocks_channel.publish(80, std::make_shared<silkworm::Block>(last_evm_block));
                  evm_blocks.push_back(generate_new_evm_block(last_evm_block));
               }

               // Add transactions to the evm block
               auto& curr = evm_blocks.back();
               auto block_version = eosevm::nonce_to_version(curr.header.nonce);
               if (new_block->new_config.has_value()){
                  if (!curr.transactions.empty()) {
                     SILK_CRIT << "new config comes in the middle of an evm block";
                     throw std::runtime_error("new config comes in the middle of an evm block");
                  }
                  auto new_config = deserialize_config(new_block->new_config.value());
                  auto consensus_param = eosevm::ConsensusParameters {
                     .gas_fee_parameters = eosevm::GasFeeParameters {
                        .gas_txnewaccount = std::visit([](auto&& arg) -> auto& { return arg.gas_parameter.gas_txnewaccount; }, new_config),
                        .gas_newaccount = std::visit([](auto&& arg) -> auto& { return arg.gas_parameter.gas_newaccount; }, new_config),
                        .gas_txcreate = std::visit([](auto&& arg) -> auto& { return arg.gas_parameter.gas_txcreate; }, new_config),
                        .gas_codedeposit = std::visit([](auto&& arg) -> auto& { return arg.gas_parameter.gas_codedeposit; }, new_config),
                        .gas_sset = std::visit([](auto&& arg) -> auto& { return arg.gas_parameter.gas_sset; }, new_config),
                     }
                  };
                  curr.set_consensus_parameter_index(consensus_param.hash());

                  silkworm::db::update_consensus_parameters(appbase::app().get_plugin<blockchain_plugin>().get_tx(), *curr.get_consensus_parameter_index(), consensus_param);
               }

               for_each_action(*new_block, [this, &curr, &block_version](const auto& act){
                     auto dtx = deserialize_tx(act);
                     auto& rlpx_ref = std::visit([](auto&& arg) -> auto& { return arg.rlpx; }, dtx);

                     silkworm::ByteView bv = {(const uint8_t*)rlpx_ref.data(), rlpx_ref.size()};
                     silkworm::Transaction evm_tx;
                     if (!silkworm::rlp::decode_transaction(bv, evm_tx, silkworm::rlp::Eip2718Wrapping::kBoth, silkworm::rlp::Leftover::kProhibit)) {
                        SILK_CRIT << "Failed to decode transaction in block: " << curr.header.number;
                        throw std::runtime_error("Failed to decode transaction");
                     }
                     auto tx_version = std::visit([](auto&& arg) -> auto { return arg.eos_evm_version; }, dtx);

                     if(tx_version < block_version) {
                        SILK_CRIT << "tx_version < block_version";
                        throw std::runtime_error("tx_version < block_version");
                     } else if (tx_version > block_version) {
                        if(curr.transactions.empty()) {
                           curr.header.nonce = eosevm::version_to_nonce(tx_version);
                           block_version = tx_version;
                        } else {
                           SILK_CRIT << "tx_version > block_version";
                           throw std::runtime_error("tx_version > block_version");
                        }
                     }

                     if(block_version >= 1) {
                        auto tx_base_fee = std::visit([](auto&& arg) -> auto { return arg.base_fee_per_gas; }, dtx);
                        if(!curr.header.base_fee_per_gas.has_value()) {
                           curr.header.base_fee_per_gas = tx_base_fee;
                        } else if (curr.header.base_fee_per_gas.value() != tx_base_fee) {
                           if(curr.transactions.empty()) {
                              curr.header.base_fee_per_gas = tx_base_fee;
                           } else {
                              SILK_CRIT << "curr.base_fee_per_gas != tx_base_fee";
                              throw std::runtime_error("curr.base_fee_per_gas != tx_base_fee");
                           }
                        }
                     }

                     curr.transactions.emplace_back(std::move(evm_tx));
               });
               set_upper_bound(curr, *new_block);

               // Calculate last irreversible EVM block
               std::optional<uint64_t> lib_timestamp;
               auto it = std::upper_bound(native_blocks.begin(), native_blocks.end(), new_block->lib, [](uint32_t lib, const auto& nb) { return lib < nb.block_num; });
               if(it != native_blocks.begin()) {
                  --it;
                  lib_timestamp = it->timestamp;
               }

               if( lib_timestamp ) {
                  // Minus 1 to make sure the resulting block is a completed EVM block built from irreversible EOS blocks.
                  auto evm_lib = timestamp_to_evm_block_num(*lib_timestamp) - 1;

                  // Remove irreversible native blocks
                  while(timestamp_to_evm_block_num(native_blocks.front().timestamp) < evm_lib) {
                     native_blocks.pop_front();
                  }

                  // Remove irreversible evm blocks
                  // The block at height evm_lib is actually irreversible as well.
                  // We want to keep at least one irreversible block in the array to deal with forks.

                  while(evm_blocks.front().header.number < evm_lib) {
                     evm_blocks.pop_front();
                  }

                  // Record the height of this complete EVM block from irreversible EOS blocks.
                  evm_lib_ = evm_lib;
               }
            }
         );
      }

      inline evmtx_type deserialize_tx(const channels::native_action& na) const {
         if( na.name == pushtx_n ) {
            pushtx tx;
            eosio::convert_from_bin(tx, na.data);
            return evmtx_type{evmtx_v0{0, std::move(tx.rlpx), 0}};
         }
         evmtx_type evmtx;
         eosio::convert_from_bin(evmtx, na.data);
         return evmtx;
      }

      inline consensus_parameter_data_type deserialize_config(const channels::native_action& na) const {
         consensus_parameter_data_type new_configs;
         eosio::convert_from_bin(new_configs, na.data);
         return new_configs;
      }

      uint64_t get_evm_lib() {
         return evm_lib_;
      }

      void shutdown() {}

      std::list<channels::native_block>             native_blocks;
      std::list<silkworm::Block>                    evm_blocks;
      channels::evm_blocks::channel_type&           evm_blocks_channel;
      channels::native_blocks::channel_type::handle native_blocks_subscription;
      std::optional<eosevm::block_mapping>          bm;
      uint64_t                                      evm_contract_name = 0;
      uint64_t                                      evm_lib_;
};

block_conversion_plugin::block_conversion_plugin() : my(new block_conversion_plugin_impl()) {}
block_conversion_plugin::~block_conversion_plugin() {}

void block_conversion_plugin::set_program_options( appbase::options_description& cli, appbase::options_description& cfg ) {

}

void block_conversion_plugin::plugin_initialize( const appbase::variables_map& options ) {
   my->init();
   SILK_INFO << "Initialized block_conversion Plugin";
}

void block_conversion_plugin::plugin_startup() {
}

void block_conversion_plugin::plugin_shutdown() {
   my->shutdown();
   SILK_INFO << "Shutdown block_conversion plugin";
}

uint64_t block_conversion_plugin::get_evm_lib() {
   return my->get_evm_lib();
}