#include <trx_generator.hpp>
#include <iostream>
#include <fc/log/logger.hpp>
#include <boost/algorithm/string.hpp>
#include <eosio/chain/chain_id_type.hpp>
#include <boost/program_options.hpp>
#include <eosio/chain/name.hpp>
#include <fc/bitutil.hpp>
#include <fc/io/raw.hpp>
#include <regex>

namespace eosio::testing {
   namespace chain = eosio::chain;
   using mvo = fc::mutable_variant_object;

   void trx_generator_base::set_transaction_headers(chain::transaction& trx, const chain::block_id_type& last_irr_block_id, const fc::microseconds& expiration, uint32_t delay_sec) {
      trx.expiration = fc::time_point_sec{fc::time_point::now() + expiration};
      trx.set_reference_block(last_irr_block_id);

      trx.max_net_usage_words = 0;// No limit
      trx.max_cpu_usage_ms = 0;   // No limit
      trx.delay_sec = delay_sec;
   }

   void trx_generator_base::push_transaction(evm_and_signed_transactions_w_signers& trx) {
      update_resign_transaction(trx);
      if (_txcount == 0) {
         log_first_trx(_config._log_dir, trx._trx);
      }
      _provider.send(trx._trx);
   }

   void trx_generator_base::stop_generation() {
      ilog("Stopping transaction generation");

      if (_txcount) {
         ilog("${d} transactions executed, ${t}us / transaction", ("d", _txcount)("t", _total_us / (double) _txcount));
         _txcount = _total_us = 0;
      }
   }

   bool trx_generator_base::stop_on_trx_fail() {
      return _config._stop_on_trx_failed;
   }

   trx_generator_base::trx_generator_base(const trx_generator_base_config& trx_gen_base_config, const provider_base_config& provider_config)
       : _config(trx_gen_base_config), _provider(provider_config) {}

   transfer_trx_generator::transfer_trx_generator(const trx_generator_base_config& trx_gen_base_config, const provider_base_config& provider_config,
                                                  const accounts_config& accts_config)
       : trx_generator_base(trx_gen_base_config, provider_config), _accts_config(accts_config) {}

   bool transfer_trx_generator::setup() {

      ilog("Stop Generation (form potential ongoing generation in preparation for starting new generation run).");
      stop_generation();

      ilog("Create All Initial Swap Transactions/Reaction Pairs (acct 1 -> acct 2, acct 2 -> acct 1) between all provided accounts.");
      create_initial_swap_transactions();

      ilog("Setup p2p transaction provider");

      ilog("Update each trx to qualify as unique and fresh timestamps, re-sign trx, and send each updated transactions via p2p transaction provider");

      _provider.setup();
      return true;
   }

   bool trx_generator_base::tear_down() {
      _provider.teardown();
      _provider.log_trxs(_config._log_dir);

      ilog("Sent transactions: ${cnt}", ("cnt", _txcount));
      ilog("Tear down p2p transaction provider");

      //Stop & Cleanup
      ilog("Stop Generation.");
      stop_generation();
      return true;
   }

   bool trx_generator_base::generate_and_send() {
      try {
         if (_trxs.size()) {
            size_t index_to_send = _txcount % _trxs.size();
            push_transaction(_trxs.at(index_to_send));
            ++_txcount;
         } else {
            elog("no transactions available to send");
            return false;
         }
      } catch (const std::exception &e) {
         elog("${e}", ("e", e.what()));
         return false;
      } catch (...) {
         elog("unknown exception");
         return false;
      }

      return true;
   }

   void trx_generator_base::log_first_trx(const std::string& log_dir, const chain::signed_transaction& trx) {
      std::ostringstream fileName;
      fileName << log_dir << "/first_trx_" << getpid() << ".txt";
      std::ofstream out(fileName.str());

      out << std::string(trx.id()) << "\n";
      out.close();
   }

   uint64_t trx_generator_base::next_nonce(size_t nonce_index) {
      return _nonce++;
   }

   uint64_t transfer_trx_generator::next_nonce(size_t nonce_index) {
      return _accts_config._nonces.at(nonce_index)++;
   }

   void trx_generator_base::update_resign_transaction(evm_and_signed_transactions_w_signers& trx) {
      sign_evm_trx(trx._evm_trx, next_nonce(trx._evm_nonce_index), trx._evm_signer);

      silkworm::Bytes rlp;
      silkworm::rlp::encode(rlp, trx._evm_trx);

      eosio::chain::bytes rlp_bytes;
      rlp_bytes.resize(rlp.size());
      memcpy(rlp_bytes.data(), rlp.data(), rlp.size());

      chain::action act = chain::action(std::vector<chain::permission_level>{{_config._miner_account, chain::config::active_name}}, _config._evm_account_name, "pushtx"_n, fc::raw::pack<chain::name>(_config._miner_account, rlp_bytes));

      trx._trx.actions.clear();
      trx._trx.actions.emplace_back( std::move(act) );
      set_transaction_headers( trx._trx, _config._last_irr_block_id, _config._trx_expiration_us );
      trx._trx.context_free_actions.clear();
      trx._trx.context_free_actions.emplace_back(std::vector<chain::permission_level>(), chain::config::null_account_name, chain::name("nonce"),
                                                 fc::raw::pack(std::to_string(_config._generator_id) + ":" + std::to_string(fc::time_point::now().time_since_epoch().count())));

      trx._trx.signatures.clear();
      trx._trx.sign(_config._miner_p_key, _config._chain_id);
   }

   chain::action trx_generator_base::get_action(eosio::chain::name code, eosio::chain::name acttype, std::vector<chain::permission_level> auths,
                                                const chain::bytes &data) const
   {
      chain::action act;
      act.account = code;
      act.name = acttype;
      act.authorization = auths;
      act.data = data;
      return act;
   }

   void transfer_trx_generator::create_evm_and_signed_transactions_w_signers(evmc::address& from, evmc::address& to,
       size_t from_nonce_index, std::array<uint8_t, 32>& from_priv_key) {
            //create the actions here
      ilog("create_initial_swap_transactions: creating swap from ${acctA} to ${acctB}",
            ("acctA", evmc::hex(static_cast<evmc::bytes_view>(from)))("acctB", evmc::hex(static_cast<evmc::bytes_view>(to))));
      silkworm::Transaction a_to_b = silkworm::Transaction();
      a_to_b.type = silkworm::TransactionType::kLegacy;
      a_to_b.max_priority_fee_per_gas = _config._gas_price;
      a_to_b.max_fee_per_gas = _config._gas_price;
      a_to_b.gas_limit = _config._gas_limit;
      a_to_b.to = to;
      a_to_b.value = _config._transfer_value;

      sign_evm_trx(a_to_b, _accts_config._nonces.at(from_nonce_index), from_priv_key);

      silkworm::Bytes rlp;
      silkworm::rlp::encode(rlp, a_to_b);

      eosio::chain::bytes rlp_bytes;
      rlp_bytes.resize(rlp.size());
      memcpy(rlp_bytes.data(), rlp.data(), rlp.size());

      chain::action act = get_action(_config._evm_account_name, "pushtx"_n, std::vector<chain::permission_level>{{_config._miner_account, chain::config::active_name}}, fc::raw::pack<chain::name>(_config._miner_account, rlp_bytes));

      eosio::chain::signed_transaction trx;
      trx.actions.emplace_back( std::move(act) );
      set_transaction_headers( trx, _config._last_irr_block_id, _config._trx_expiration_us );
      trx.context_free_actions.emplace_back(std::vector<chain::permission_level>(), chain::config::null_account_name, chain::name("nonce"), fc::raw::pack(std::to_string(_config._generator_id) + ":" + std::to_string(fc::time_point::now().time_since_epoch().count())));

      trx.sign(_config._miner_p_key, _config._chain_id);
      _trxs.emplace_back(a_to_b, from_priv_key, from_nonce_index, trx, _config._miner_p_key);
   }

   void transfer_trx_generator::create_initial_swap_transactions() {

      for (size_t i = 0; i < _accts_config._acct_name_vec.size(); ++i) {
         for (size_t j = i + 1; j < _accts_config._acct_name_vec.size(); ++j) {
            //create the actions here
            ilog("create_initial_swap_transactions: creating swap from ${acctA} to ${acctB}",
                 ("acctA", evmc::hex(static_cast<evmc::bytes_view>(_accts_config._acct_name_vec.at(i))))("acctB", evmc::hex(static_cast<evmc::bytes_view>(_accts_config._acct_name_vec.at(j)))));
            create_evm_and_signed_transactions_w_signers(_accts_config._acct_name_vec.at(i), _accts_config._acct_name_vec.at(j), i, _accts_config._priv_keys_vec.at(i));

            ilog("create_initial_swap_transactions: creating swap from ${acctB} to ${acctA}",
                 ("acctB", evmc::hex(static_cast<evmc::bytes_view>(_accts_config._acct_name_vec.at(j))))("acctA", evmc::hex(static_cast<evmc::bytes_view>(_accts_config._acct_name_vec.at(i)))));
            create_evm_and_signed_transactions_w_signers(_accts_config._acct_name_vec.at(j), _accts_config._acct_name_vec.at(i), j, _accts_config._priv_keys_vec.at(j));
         }
      }
      ilog("create_initial_swap_transactions: total action pairs created: ${pairs}", ("pairs", _trxs.size()));
   }

   void transfer_trx_generator::sign_evm_trx(silkworm::Transaction &trx, uint64_t nonce, std::array<uint8_t, 32> &private_key)
   {
      silkworm::Bytes rlp;
      trx.chain_id = _config._evm_chain_id;
      trx.nonce = nonce;
      silkworm::rlp::encode(rlp, trx, false);
      ethash::hash256 hash{silkworm::keccak256(rlp)};

      secp256k1_ecdsa_recoverable_signature sig;
      secp256k1_context* ctx = secp256k1_context_create(SECP256K1_CONTEXT_SIGN);
      if(!secp256k1_ecdsa_sign_recoverable(ctx, &sig, hash.bytes, private_key.data(), NULL, NULL)) {
         elog("sign_evm_trx -- secp256k1_ecdsa_sign_recoverable FAILED");
      }
      uint8_t r_and_s[64];
      int recid;
      secp256k1_ecdsa_recoverable_signature_serialize_compact(ctx, r_and_s, &recid, &sig);

      trx.r = intx::be::unsafe::load<intx::uint256>(r_and_s);
      trx.s = intx::be::unsafe::load<intx::uint256>(r_and_s + 32);
      trx.odd_y_parity = recid;
   }
}
