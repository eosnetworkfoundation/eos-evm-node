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

   // Dispatch Policy that exit on exception
   struct exit_on_exceptions {
      exit_on_exceptions() = default;
      using result_type = void;

      template<typename InputIterator>
      result_type operator()(InputIterator first, InputIterator last) {
         while (first != last) {
            try {
               *first;
            } catch (...) {
               SILK_CRIT << "Caught exception when processing channel callbacks.";
               appbase::app().quit();
            }
            ++first;
         }
      }
   };
   
   using native_blocks = appbase::channel_decl<struct native_blocks_tag, std::shared_ptr<native_block>, exit_on_exceptions>;
   using evm_blocks = appbase::channel_decl<struct evm_blocks_tag, std::shared_ptr<silkworm::Block>, exit_on_exceptions>;
} // ns channels