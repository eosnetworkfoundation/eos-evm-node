#pragma once

#include <appbase/application.hpp>

#include <silkworm/core/types/block.hpp>
#include <eosio/chain_conversions.hpp>
#include <eosio/chain_types.hpp>
#include <eosio/crypto.hpp>
#include <eosio/ship_protocol.hpp>

#include <boost/beast/core/flat_buffer.hpp>

namespace channels {

   struct native_action {
      uint32_t            ordinal;
      eosio::name         receiver;
      eosio::name         account;
      eosio::name         name;
      std::vector<char>   data;
   };
   
   struct native_trx {
      inline native_trx(eosio::checksum256 id, uint32_t cpu, int64_t elapsed)
         : id(id), cpu_usage_us(cpu), elapsed(elapsed) {}
      eosio::checksum256         id;
      uint32_t                   cpu_usage_us;
      int64_t                    elapsed;
      std::vector<native_action> actions;
   };

   struct native_block {
      native_block() = default;
      inline native_block(uint32_t bn, int64_t tm)
        : block_num(bn), timestamp(tm) {}
      eosio::checksum256      id;
      eosio::checksum256      prev;
      uint32_t                block_num = 0;
      int64_t                 timestamp = 0;
      uint32_t                lib = 0;
      std::optional<native_action>  new_config = std::nullopt;
      std::vector<native_trx> transactions;
   };

   struct consensus_parameter_event {
      intx::uint256 min_gas_fee = 0;
      uint64_t gas_txnewaccount = 0;
      uint64_t gas_newaccount = 0;
      uint64_t gas_txcreate = 0;
      uint64_t gas_codedeposit = 0;
      uint64_t gas_sset = 0;
   };
   
   using native_blocks = appbase::channel_decl<struct native_blocks_tag, std::shared_ptr<native_block>>;
   using evm_blocks = appbase::channel_decl<struct evm_blocks_tag, std::shared_ptr<silkworm::Block>>;
} // ns channels