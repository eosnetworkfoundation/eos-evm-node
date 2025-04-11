#include "blockchain_plugin.hpp"

#include <iostream>
#include <limits>
#include <map>
#include <string>

#include <boost/asio/io_context.hpp>

#include <silkworm/node/stagedsync/types.hpp>
#include <silkworm/node/stagedsync/execution_engine.hpp>

class ExecutionEngineEx : public silkworm::stagedsync::ExecutionEngine {
   public :
   
   ExecutionEngineEx(boost::asio::io_context& io, silkworm::NodeSettings& settings, silkworm::db::RWAccess dba) : ExecutionEngine(io, settings, dba) {

   }
   silkworm::db::RWTxn& get_tx() {
      return main_chain_.tx();
   }
};

using sys = sys_plugin;
class blockchain_plugin_impl : std::enable_shared_from_this<blockchain_plugin_impl> {
   public:
      blockchain_plugin_impl() = default;

      silkworm::db::RWTxn& get_tx() {
         return exec_engine->get_tx();
      }

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
                     exec_engine = std::make_unique<ExecutionEngineEx>(appbase::app().get_io_context(), *node_settings, silkworm::db::RWAccess{*db_env});
                     exec_engine->open();
                  }
                  
                  exec_engine->insert_block(new_block);
                  if(!(++block_count % 5000) || !new_block->irreversible) {
                     // Get the last complete EVM block from irreversible EOS blocks.
                     // The height is uint64_t so we can get it as a whole without worrying about atomicity.
                     // Even some data races happen, it's fine in our scenario to read old data as starting from earlier blocks is always safer.
                     uint64_t evm_lib = appbase::app().get_plugin<block_conversion_plugin>().get_evm_lib();
                     SILK_INFO << "Storing EVM Lib: " << "#" << evm_lib;

                     // Storing the EVM block height of the last complete block from irreversible EOS blocks.
                     // We have to do this in this thread with the tx instance stored in exec_engine due to the lmitation of MDBX.
                     // Note there's no need to commit here as the tx is borrowed. ExecutionEngine will manange the commits.
                     // There's some other advantage to save this height in this way: 
                     // If the system is shut down during catching up irreversible blocks, i.e. in the middle of the 5000 block run,
                     // saving the height in this way can minimize the possibility having a stored height that is higher than the canonical header.
                     write_runtime_states_u64(exec_engine->get_tx(), silkworm::db::RuntimeState::kLibProcessed, evm_lib);

                     exec_engine->verify_chain(new_block->header.hash());
                     block_count=0;
                  }
               } catch (const mdbx::exception& ex) {
                  sys::error("evm_blocks_subscription: mdbx::exception, " + std::string(ex.what()));
               } catch (const std::exception& ex) {
                  sys::error("evm_blocks_subscription: std::exception:" + std::string(ex.what()));
               } catch (...) {
                  sys::error("evm_blocks_subscription: unknown exception");
               }
            }
         );
      }

      void shutdown() {
         exec_engine->stop();
         exec_engine->close();      
      }

      using txn_t = std::unique_ptr<silkworm::db::RWTxn>;

      silkworm::NodeSettings*                                 node_settings;
      mdbx::env*                                              db_env;
      channels::evm_blocks::channel_type::handle              evm_blocks_subscription;
      std::unique_ptr<ExecutionEngineEx>  exec_engine;
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

silkworm::db::RWTxn& blockchain_plugin::get_tx() {
   return my->get_tx();
}