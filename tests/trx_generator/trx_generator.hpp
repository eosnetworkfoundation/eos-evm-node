#pragma once
#include <trx_provider.hpp>
#include <string>
#include <vector>
#include <boost/program_options.hpp>
#include <eosio/chain/transaction.hpp>
#include <eosio/chain/name.hpp>
#include <eosio/chain/asset.hpp>
#include <eosio/chain/abi_serializer.hpp>
#include <fc/io/json.hpp>

#include <silkworm/core/types/transaction.hpp>
#include <silkworm/core/common/util.hpp>
#include <intx/intx.hpp>
#include <secp256k1.h>
#include <secp256k1_recovery.h>

namespace eosio::testing {
   using namespace chain::literals;
   using namespace evmc::literals;
   using namespace intx;

   inline constexpr intx::uint256 operator"" _gwei(const char *s)
   {
      return intx::from_string<intx::uint256>(s) * intx::exp(10_u256, 9_u256);
   }

   struct evm_and_signed_transactions_w_signers {
      evm_and_signed_transactions_w_signers(silkworm::Transaction evm_trx, std::array<uint8_t, 32> evm_signer, size_t evm_nonce_index, eosio::chain::signed_transaction trx, fc::crypto::private_key key)
       : _evm_trx(std::move(evm_trx)), _evm_signer(evm_signer), _evm_nonce_index(evm_nonce_index), _trx(std::move(trx)), _signer(key) {}

      silkworm::Transaction _evm_trx;
      std::array<uint8_t, 32> _evm_signer;
      size_t _evm_nonce_index;
      eosio::chain::signed_transaction _trx;
      fc::crypto::private_key _signer;
   };

   struct trx_generator_base_config {
      uint16_t _generator_id = 0;
      eosio::chain::chain_id_type _chain_id = eosio::chain::chain_id_type::empty_chain_id();
      uint64_t _evm_chain_id = 15555;
      static constexpr eosio::chain::name _evm_account_name = "evm"_n;
      eosio::chain::name _contract_owner_account = eosio::chain::name();
      eosio::chain::name _miner_account = eosio::chain::name();
      fc::crypto::private_key _miner_p_key;
      fc::microseconds _trx_expiration_us = fc::seconds(3600);
      eosio::chain::block_id_type _last_irr_block_id = eosio::chain::block_id_type();
      std::string _log_dir = ".";
      bool _stop_on_trx_failed = true;
      uint64_t _gas_price = 150'000'000'000;
      intx::uint256 _transfer_value = 1_gwei;
      uint64_t _gas_limit = 21000;


      std::string to_string() const {
         std::ostringstream ss;
         ss << " generator id: " << _generator_id << " chain id: " << std::string(_chain_id) << " contract owner account: " 
            << _contract_owner_account << " trx expiration seconds: " << _trx_expiration_us.to_seconds() << " lib id: " << std::string(_last_irr_block_id)
            << " log dir: " << _log_dir << " stop on trx failed: " << _stop_on_trx_failed << " base gas price " << _gas_price ;
         return std::move(ss).str();
      };
   };

   struct accounts_config {
      std::vector<evmc::address> _acct_name_vec;
      std::vector<std::array<uint8_t, 32>> _priv_keys_vec;
      std::vector<uint64_t> _nonces;

      std::string to_string() const {
         std::ostringstream ss;
         ss << "Accounts Specified: accounts: [ ";
         for(size_t i = 0; i < _acct_name_vec.size(); ++i) {
               ss << evmc::hex(static_cast<evmc::bytes_view>(_acct_name_vec.at(i)));
               if(i < _acct_name_vec.size() - 1) {
                  ss << ", ";
               }
         }
         ss << " ] keys: [ ";
         for(size_t i = 0; i < _priv_keys_vec.size(); ++i) {
               for (auto c : _priv_keys_vec.at(i)) {
                  ss << c;
               }
               if(i < _priv_keys_vec.size() - 1) {
                  ss << ", ";
               }
         }
         ss << " ] nonces: [ ";
         for(size_t i = 0; i < _nonces.size(); ++i) {
               ss << _nonces.at(i);
               if(i < _nonces.size() - 1) {
                  ss << ", ";
               }
         }
         ss << " ]";

         return std::move(ss).str();
      };
   };

   struct trx_generator_base {
      const trx_generator_base_config& _config;
      trx_provider _provider;

      uint64_t _total_us = 0;
      uint64_t _txcount = 0;
      uint64_t _nonce = 0;

      std::vector<evm_and_signed_transactions_w_signers> _trxs;

      trx_generator_base(const trx_generator_base_config& trx_gen_base_config, const provider_base_config& provider_config);

      virtual ~trx_generator_base() = default;

      virtual uint64_t next_nonce(size_t nonce_index);
      virtual void update_resign_transaction(evm_and_signed_transactions_w_signers& trx);
      virtual void sign_evm_trx(silkworm::Transaction &trx, uint64_t nonce, std::array<uint8_t, 32> &private_key);
      
      chain::action get_action(eosio::chain::name code, eosio::chain::name acttype, std::vector<chain::permission_level> auths, const chain::bytes &data) const;
      void push_transaction(evm_and_signed_transactions_w_signers& trx);

      void set_transaction_headers(eosio::chain::transaction& trx, const eosio::chain::block_id_type& last_irr_block_id, const fc::microseconds& expiration, uint32_t delay_sec = 0);

      void log_first_trx(const std::string& log_dir, const eosio::chain::signed_transaction& trx);

      bool generate_and_send();
      bool tear_down();
      void stop_generation();
      bool stop_on_trx_fail();
   };

   struct transfer_trx_generator : public trx_generator_base {
      accounts_config _accts_config;

      transfer_trx_generator(const trx_generator_base_config& trx_gen_base_config, const provider_base_config& provider_config, const accounts_config& accts_config);

      virtual uint64_t next_nonce(size_t nonce_index);
      void create_initial_swap_transactions();

      void create_evm_and_signed_transactions_w_signers(evmc::address &from, evmc::address &to, size_t from_nonce_index, std::array<uint8_t, 32> &from_priv_key);
      void sign_swap(silkworm::Transaction &trx, uint64_t evm_chain_id, std::array<uint8_t, 32> &private_key);

      bool setup();
   };
}
