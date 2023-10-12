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
   using namespace chain::literals;
   namespace chain = eosio::chain;

   void trx_generator_base::set_transaction_headers(chain::transaction& trx, const chain::block_id_type& last_irr_block_id, const fc::microseconds& expiration, uint32_t delay_sec) {
      trx.expiration = fc::time_point_sec{fc::time_point::now() + expiration};
      trx.set_reference_block(last_irr_block_id);

      trx.max_net_usage_words = 0;// No limit
      trx.max_cpu_usage_ms = 0;   // No limit
      trx.delay_sec = delay_sec;
   }

   signed_transaction_w_signer trx_generator_base::create_trx_w_actions_and_signer(std::vector<chain::action>&& acts, const fc::crypto::private_key& priv_key,
                                                                                   uint64_t& nonce_prefix, uint64_t& nonce, const fc::microseconds& trx_expiration,
                                                                                   const chain::chain_id_type& chain_id, const chain::block_id_type& last_irr_block_id) {
      chain::signed_transaction trx;
      set_transaction_headers(trx, last_irr_block_id, trx_expiration);
      trx.actions = std::move(acts);
      trx.context_free_actions.emplace_back(std::vector<chain::permission_level>(), chain::config::null_account_name, chain::name("nonce"),
         fc::raw::pack(std::to_string(_config._generator_id) + ":" + std::to_string(nonce_prefix) + ":" + std::to_string(++nonce) + ":" + std::to_string(fc::time_point::now().time_since_epoch().count())));

      trx.sign(priv_key, chain_id);
      return signed_transaction_w_signer(trx, priv_key);
   }

   void transfer_trx_generator::create_initial_transfer_transactions(uint64_t& nonce_prefix, uint64_t& nonce) {
      std::vector<signed_transaction_w_signer> trxs;
      _trxs.reserve(2 * _action_pairs_vector.size());

      for(const action_pair_w_keys& ap: _action_pairs_vector) {
         _trxs.push_back(create_trx_w_actions_and_signer({ap._first_act}, ap._first_act_priv_key, nonce_prefix, nonce, _config._trx_expiration_us, _config._chain_id,
                                                         _config._last_irr_block_id));
         _trxs.push_back(create_trx_w_actions_and_signer({ap._second_act}, ap._second_act_priv_key, nonce_prefix, nonce, _config._trx_expiration_us, _config._chain_id,
                                                         _config._last_irr_block_id));
      }
   }

   void trx_generator_base::update_resign_transaction(chain::signed_transaction& trx, const fc::crypto::private_key& priv_key, uint64_t& nonce_prefix, uint64_t& nonce,
                                                      const fc::microseconds& trx_expiration, const chain::chain_id_type& chain_id, const chain::block_id_type& last_irr_block_id) {
      trx.context_free_actions.clear();
      trx.context_free_actions.emplace_back(std::vector<chain::permission_level>(), chain::config::null_account_name, chain::name("nonce"),
         fc::raw::pack(std::to_string(_config._generator_id) + ":" + std::to_string(nonce_prefix) + ":" + std::to_string(++nonce) + ":" + std::to_string(fc::time_point::now().time_since_epoch().count())));
      set_transaction_headers(trx, last_irr_block_id, trx_expiration);
      trx.signatures.clear();
      trx.sign(priv_key, chain_id);
   }

   chain::bytes transfer_trx_generator::make_transfer_data(const chain::name& from, const chain::name& to, const chain::asset& quantity, const std::string& memo) {
      return fc::raw::pack< chain::name>(from, to, quantity, memo);
   }

   auto transfer_trx_generator::make_transfer_action(chain::name account, chain::name from, chain::name to, chain::asset quantity, std::string memo) {
      return chain::action(std::vector<chain::permission_level>{{from, chain::config::active_name}},
                           account, "transfer"_n, make_transfer_data(from, to, quantity, std::move(memo)));
   }

   void transfer_trx_generator::create_initial_transfer_actions(const std::string& salt, const uint64_t& period) {

      for (size_t i = 0; i < _accts_config._acct_name_vec.size(); ++i) {
         for (size_t j = i + 1; j < _accts_config._acct_name_vec.size(); ++j) {
            //create the actions here
            ilog("create_initial_transfer_actions: creating transfer from ${acctA} to ${acctB}",
                 ("acctA", _accts_config._acct_name_vec.at(i))("acctB", _accts_config._acct_name_vec.at(j)));
            chain::action act_a_to_b = make_transfer_action(_config._contract_owner_account, _accts_config._acct_name_vec.at(i), _accts_config._acct_name_vec.at(j),
                                                            chain::asset::from_string("1.0000 CUR"), salt);

            ilog("create_initial_transfer_actions: creating transfer from ${acctB} to ${acctA}",
                 ("acctB", _accts_config._acct_name_vec.at(j))("acctA", _accts_config._acct_name_vec.at(i)));
            chain::action act_b_to_a = make_transfer_action(_config._contract_owner_account, _accts_config._acct_name_vec.at(j), _accts_config._acct_name_vec.at(i),
                                                            chain::asset::from_string("1.0000 CUR"), salt);

            _action_pairs_vector.emplace_back(act_a_to_b, act_b_to_a, _accts_config._priv_keys_vec.at(i), _accts_config._priv_keys_vec.at(j));
         }
      }
      ilog("create_initial_transfer_actions: total action pairs created: ${pairs}", ("pairs", _action_pairs_vector.size()));
   }

   trx_generator_base::trx_generator_base(const trx_generator_base_config& trx_gen_base_config, const provider_base_config& provider_config)
       : _config(trx_gen_base_config), _provider(provider_config) {}

   transfer_trx_generator::transfer_trx_generator(const trx_generator_base_config& trx_gen_base_config, const provider_base_config& provider_config,
                                                  const accounts_config& accts_config)
       : trx_generator_base(trx_gen_base_config, provider_config), _accts_config(accts_config) {}

   bool transfer_trx_generator::setup() {
      const std::string salt = std::to_string(getpid());
      const uint64_t &period = 20;
      _nonce_prefix = 0;
      _nonce = static_cast<uint64_t>(fc::time_point::now().sec_since_epoch()) << 32;

      ilog("Stop Generation (form potential ongoing generation in preparation for starting new generation run).");
      stop_generation();

      ilog("Create All Initial Transfer Action/Reaction Pairs (acct 1 -> acct 2, acct 2 -> acct 1) between all provided accounts.");
      create_initial_transfer_actions(salt, period);

      ilog("Create All Initial Transfer Transactions (one for each created action).");
      create_initial_transfer_transactions(++_nonce_prefix, _nonce);

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
            push_transaction(_trxs.at(index_to_send), ++_nonce_prefix, _nonce, _config._trx_expiration_us, _config._chain_id,
                             _config._last_irr_block_id);
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

   void trx_generator_base::push_transaction(signed_transaction_w_signer& trx, uint64_t& nonce_prefix, uint64_t& nonce,
                                             const fc::microseconds& trx_expiration, const chain::chain_id_type& chain_id, const chain::block_id_type& last_irr_block_id) {
      update_resign_transaction(trx._trx, trx._signer, ++nonce_prefix, nonce, trx_expiration, chain_id, last_irr_block_id);
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
}
