#pragma once

#include <appbase/application.hpp>
#include "sys_plugin.hpp"
#include "ship_receiver_plugin.hpp"

#include <eosio/name.hpp>

struct pushtx {
   eosio::name          miner;
   std::vector<uint8_t> rlpx;
};
EOSIO_REFLECT(pushtx, miner, rlpx)

struct evmtx_base {
   uint64_t eos_evm_version;
   std::vector<uint8_t> rlpx;
};

struct evmtx_v1 : evmtx_base {
   uint64_t base_fee_per_gas;
};

struct evmtx_v3 : evmtx_base {
   uint64_t overhead_price;
   uint64_t storage_price;
};

using evmtx_type = std::variant<evmtx_v1, evmtx_v3>;

EOSIO_REFLECT(evmtx_base, eos_evm_version, rlpx);
EOSIO_REFLECT(evmtx_v1, base evmtx_base, base_fee_per_gas);
EOSIO_REFLECT(evmtx_v3, base evmtx_base, overhead_price, storage_price);

struct gas_parameter_type {
    uint64_t gas_txnewaccount = 0;
    uint64_t gas_newaccount = 25000;
    uint64_t gas_txcreate = 32000;
    uint64_t gas_codedeposit = 200;
    uint64_t gas_sset = 20000;
};
EOSIO_REFLECT(gas_parameter_type, gas_txnewaccount, gas_newaccount, gas_txcreate, gas_codedeposit, gas_sset)

struct consensus_parameter_data_v0 {
    gas_parameter_type gas_parameter;
};
using consensus_parameter_data_type = std::variant<consensus_parameter_data_v0>;
EOSIO_REFLECT(consensus_parameter_data_v0, gas_parameter)

class block_conversion_plugin : public appbase::plugin<block_conversion_plugin> {
   public:
      APPBASE_PLUGIN_REQUIRES((sys_plugin)(ship_receiver_plugin)(engine_plugin));
      block_conversion_plugin();
      virtual ~block_conversion_plugin();
      virtual void set_program_options(appbase::options_description& cli, appbase::options_description& cfg) override;
      void plugin_initialize(const appbase::variables_map& options);
      void plugin_startup();
      void plugin_shutdown();

      uint32_t get_block_stride() const;
      uint64_t get_evm_lib();

   private:
      std::unique_ptr<class block_conversion_plugin_impl> my;
};