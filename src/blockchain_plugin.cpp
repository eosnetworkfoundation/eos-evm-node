#include "blockchain_plugin.hpp"

#include <iostream>
#include <limits>
#include <map>
#include <string>

#include <silkworm/node/stagedsync/types.hpp>
#include <silkworm/node/stagedsync/execution_engine.hpp>

using sys = sys_plugin;
class blockchain_plugin_impl : std::enable_shared_from_this<blockchain_plugin_impl> {
   public:
      blockchain_plugin_impl() = default;

      inline void init() {
         SILK_DEBUG << "blockchain_plugin_impl INIT";
         db_env = appbase::app().get_plugin<engine_plugin>().get_db();
         node_settings = appbase::app().get_plugin<engine_plugin>().get_node_settings();
         SILK_INFO << "Using DB environment at location : " << node_settings->data_directory->chaindata().path().string();

         evm_blocks_subscription = appbase::app().get_channel<channels::evm_blocks>().subscribe(
            [this](auto new_block) {
               try {
                  static size_t block_count{0};

                  SILK_DEBUG << "EVM Block " << new_block->header.number;
                  if(!exec_engine) {
                     exec_engine = std::make_unique<silkworm::stagedsync::ExecutionEngine>(appbase::app().get_io_context(), *node_settings, silkworm::db::RWAccess{*db_env});
                     exec_engine->open();
                  }

                  exec_engine->insert_block(new_block);
                  if(!(++block_count % 5000) || !new_block->irreversible) {
                     exec_engine->verify_chain(new_block->header.hash());
                     block_count=0;
                  }
               } catch (const mdbx::exception& ex) {
                  SILK_CRIT << "CALLBACK ERR1" << std::string(ex.what());
               } catch (const std::exception& ex) {
                  SILK_CRIT << "CALLBACK ERR2" << std::string(ex.what());
               } catch (...) {
                  SILK_CRIT << "CALLBACK ERR3";
               }
            }
         );
      }

      void shutdown() {
         exec_engine->close();
         exec_engine->stop();
      }

      using txn_t = std::unique_ptr<silkworm::db::RWTxn>;

      silkworm::NodeSettings*                                 node_settings;
      mdbx::env*                                              db_env;
      channels::evm_blocks::channel_type::handle              evm_blocks_subscription;
      std::unique_ptr<silkworm::stagedsync::ExecutionEngine>  exec_engine;
};

blockchain_plugin::blockchain_plugin() : my(new blockchain_plugin_impl()) {}
blockchain_plugin::~blockchain_plugin() {}

void blockchain_plugin::set_program_options( appbase::options_description& cli, appbase::options_description& cfg ) {
}

void blockchain_plugin::plugin_initialize( const appbase::variables_map& options ) {
   my->init();
   SILK_INFO << "Initialized Blockchain Plugin";
}

void blockchain_plugin::plugin_startup() {
   SILK_INFO << "Starting Blockchain Plugin";
}

void blockchain_plugin::plugin_shutdown() {
   my->shutdown();
   SILK_INFO << "Shutdown Blockchain plugin";
}
