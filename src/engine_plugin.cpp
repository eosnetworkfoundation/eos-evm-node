#include "engine_plugin.hpp"
#include "channels.hpp"

#include <filesystem>
#include <iostream>
#include <string>
#include <fstream>

#include <boost/process/environment.hpp>
#include <boost/filesystem.hpp>
#include <boost/filesystem/path.hpp>

#include <nlohmann/json.hpp>

#include <silkworm/infra/common/stopwatch.hpp>
#include <silkworm/infra/concurrency/signal_handler.hpp>
#include <silkworm/node/db/stages.hpp>
#include <silkworm/node/db/access_layer.hpp>
#include <silkworm/node/db/genesis.hpp>
#include <silkworm/node/backend/remote/backend_kv_server.hpp>
#include <silkworm/infra/grpc/server/server_settings.hpp>
#include <silkworm/sentry/api/api_common/sentry_client.hpp>

//#include <silkworm/rpc/util.hpp>

using sys = sys_plugin;
using namespace silkworm::sentry;
using namespace silkworm::sentry::api::api_common;

struct nopservice : silkworm::sentry::api::api_common::Service {
    boost::asio::awaitable<void> set_status(eth::StatusData status_data) override { co_return; }
    boost::asio::awaitable<uint8_t> handshake() override { co_return 0; };
    boost::asio::awaitable<NodeInfos> node_infos() override { co_return NodeInfos{}; };
    boost::asio::awaitable<Service::PeerKeys> send_message_by_id(common::Message message, common::EccPublicKey public_key) override { co_return Service::PeerKeys{}; };
    boost::asio::awaitable<Service::PeerKeys> send_message_to_random_peers(common::Message message, size_t max_peers) override { co_return Service::PeerKeys{}; };
    boost::asio::awaitable<Service::PeerKeys> send_message_to_all(common::Message message) override { co_return Service::PeerKeys{}; };
    boost::asio::awaitable<Service::PeerKeys> send_message_by_min_block(common::Message message, size_t max_peers) override { co_return Service::PeerKeys{}; };
    boost::asio::awaitable<void> peer_min_block(common::EccPublicKey public_key) override { co_return; };
    boost::asio::awaitable<void> messages( MessageIdSet message_id_filter, std::function<boost::asio::awaitable<void>(MessageFromPeer)> consumer) override { co_return; };
    boost::asio::awaitable<PeerInfos> peers() override { co_return PeerInfos{}; };
    boost::asio::awaitable<size_t> peer_count() { co_return 0; };
    boost::asio::awaitable<std::optional<PeerInfo>> peer_by_id(common::EccPublicKey public_key) override { co_return std::optional<PeerInfo>{}; };
    boost::asio::awaitable<void> penalize_peer(common::EccPublicKey public_key) override { co_return; };
    boost::asio::awaitable<void> peer_events(std::function<boost::asio::awaitable<void>(PeerEvent)> consumer) override { co_return; };
};

struct nopsentry : SentryClient {
    boost::asio::awaitable<std::shared_ptr<Service>> service() override {
      co_return std::make_shared<nopservice>();
    }
   
   //! Connected or just created an ready to handle calls. service() is unlikely to block for long.
    bool is_ready() override { return false; }
    void on_disconnect(std::function<boost::asio::awaitable<void>()> callback) override {};
    boost::asio::awaitable<void> reconnect() override { co_return; };
};

class engine_plugin_impl : std::enable_shared_from_this<engine_plugin_impl> {
   public:
      engine_plugin_impl(const std::string& data_dir, uint32_t num_of_threads, uint32_t max_readers, std::string address, std::optional<std::string> genesis_json) {

         node_settings.data_directory = std::make_unique<silkworm::DataDirectory>(data_dir, false);
         node_settings.etherbase  = silkworm::to_evmc_address(silkworm::from_hex("").value()); // TODO determine etherbase name
         node_settings.chaindata_env_config = {node_settings.data_directory->chaindata().path().string(), false, false};
         node_settings.chaindata_env_config.max_readers = max_readers;
         node_settings.chaindata_env_config.exclusive = false;
         node_settings.prune_mode = std::make_unique<silkworm::db::PruneMode>();

         server_settings.address_uri = address;
         server_settings.context_pool_settings.num_contexts = num_of_threads;

         pid = boost::this_process::get_id();
         tid = std::this_thread::get_id();

         const auto data_path = std::filesystem::path(node_settings.data_directory->chaindata().path().string());
         if ( std::filesystem::exists(data_path) ) {
            node_settings.chaindata_env_config.shared = true;
         } else {
            node_settings.chaindata_env_config.create = true;
         }

         db_env = silkworm::db::open_env(node_settings.chaindata_env_config);
         SILK_INFO << "Created DB environment at location : " << node_settings.data_directory->chaindata().path().string();

         silkworm::db::RWTxn txn(db_env);
         silkworm::db::table::check_or_create_chaindata_tables(txn);

         auto existing_config{silkworm::db::read_chain_config(txn)};
         if (!existing_config.has_value()) {
            if(!genesis_json) {
               sys::error("Genesis state not provided");
               return;
            }

            boost::filesystem::path genesis_file = *genesis_json;
            if(!boost::filesystem::is_regular_file(genesis_file)) {
               sys::error("Specified genesis file does not exist");
               return;
            }

            std::ifstream genesis_f(genesis_file);
            silkworm::db::initialize_genesis(txn, nlohmann::json::parse(genesis_f), /*allow_exceptions=*/true);
         }

         txn.commit();
         node_settings.chain_config = silkworm::db::read_chain_config(txn);
         node_settings.network_id = node_settings.chain_config->chain_id;

         // Load genesis_hash
         node_settings.chain_config->genesis_hash = silkworm::db::read_canonical_header_hash(txn, 0);
         if (!node_settings.chain_config->genesis_hash.has_value())
               throw std::runtime_error("Could not load genesis hash");

         auto sentry = std::make_shared<nopsentry>();
         eth.reset(new silkworm::EthereumBackEnd(node_settings, &db_env, sentry));
         eth->set_node_name("EOS EVM Node");
         SILK_INFO << "Created Ethereum Backend with network id <" << node_settings.network_id << ">, etherbase <" << node_settings.etherbase->bytes << ">";

         server.reset(new silkworm::rpc::BackEndKvServer(server_settings, *eth.get()));
      }

      inline void startup() {
         server->build_and_start();
         SILK_INFO << "Started Engine Server";
      }

      inline void shutdown() {
         eth->close();
         server->shutdown();
         SILK_INFO << "Stopped Engine Server";
      }

      std::optional<silkworm::BlockHeader> get_head_canonical_header() {
         silkworm::db::ROTxn txn(db_env);
         auto head_num = silkworm::db::stages::read_stage_progress(txn, silkworm::db::stages::kHeadersKey);
         return silkworm::db::read_canonical_header(txn, head_num);
      }

      std::optional<silkworm::Block> get_canonical_block_at_height(std::optional<uint64_t> height) {
         uint64_t target = 0;
         SILK_INFO << "Determining effective canonical header.";
         if (!height) {
            auto lib = get_evm_lib();
            auto header = get_head_canonical_header();
            if (lib) {
               SILK_INFO << "Stored LIB at: " << "#" << *lib;
               target = *lib;
               // Make sure we start from number smaller than or equal to head if possible.
               // But ignore the case where head is not avaiable
               if (header && target > header->number) {
                  target = header->number;
                  SILK_INFO << "Canonical header is at lower height, set the target height to: " << "#" << target;
               }
            }
            else {
               SILK_INFO << "Stored LIB not available.";
               // no lib, might be the first run from an old db.
               // Use the old logic.
               if (!header) {
                  SILK_INFO << "Failed to read canonical header";
                  return {};
               }
               else {
                  target = header->number;
                  SILK_INFO << "Canonical header at: " << "#" << target;
               }
            }
         }
         else {
            // Do not check canonical header or lib.
            // If there's anything wrong, overriding here has some chance to fix it.
            target = *height;
            SILK_INFO << "Command line options set the canonical height as " << "#" << target;
         }

         silkworm::db::ROTxn txn(db_env);
         silkworm::Block block;
         auto res = read_block_by_number(txn, target, false, block);
         if(!res) return {};
         return block;
      }

      void record_evm_lib(uint64_t height) {
         SILK_INFO << "Saving EVM LIB " << "#" << height;
         try {
         silkworm::db::RWTxn txn(db_env);
         write_runtime_states_u64(txn, height, silkworm::db::RuntimeState::kLibProcessed);
         txn.commit_and_stop();
         }
         catch (const std::exception& e) {
            SILK_ERROR << "exception: " << e.what();
         }
         catch(...) {
            SILK_INFO << "Unknown exception";
         }
         SILK_INFO << "Finished EVM LIB " << "#" << height;
      }

      std::optional<uint64_t> get_evm_lib() {
         silkworm::db::ROTxn txn(db_env);
         return read_runtime_states_u64(txn, silkworm::db::RuntimeState::kLibProcessed);
      }

      std::optional<silkworm::BlockHeader> get_genesis_header() {
         silkworm::db::ROTxn txn(db_env);
         return silkworm::db::read_canonical_header(txn, 0);
      }

      silkworm::NodeSettings                          node_settings;
      silkworm::rpc::ServerSettings                   server_settings;
      mdbx::env_managed                               db_env;
      std::unique_ptr<silkworm::EthereumBackEnd>      eth;
      std::unique_ptr<silkworm::rpc::BackEndKvServer> server;
      int                                             pid;
      std::thread::id                                 tid;
      std::optional<std::string>                      genesis_json;
};

engine_plugin::engine_plugin() {}
engine_plugin::~engine_plugin() {}

void engine_plugin::set_program_options( appbase::options_description& cli, appbase::options_description& cfg ) {
   cfg.add_options()
      ("chain-data", boost::program_options::value<std::string>()->default_value("."),
        "chain data path as a string")
      ("engine-port", boost::program_options::value<std::string>()->default_value("127.0.0.1:8080"),
        "engine address of the form <address>:<port>")
      ("threads", boost::program_options::value<std::uint32_t>()->default_value(16),
        "number of worker threads")
      ("genesis-json", boost::program_options::value<std::string>(),
        "file to read EVM genesis state from")
      ("max-readers", boost::program_options::value<std::uint32_t>()->default_value(1024),
        "maximum number of database readers")
   ;
}

void engine_plugin::plugin_initialize( const appbase::variables_map& options ) {
   const auto& chain_data = options.at("chain-data").as<std::string>();
   const auto& address    = options.at("engine-port").as<std::string>();
   const auto& threads    = options.at("threads").as<uint32_t>();
   const auto& max_readers = options.at("max-readers").as<uint32_t>();

   std::optional<std::string> genesis_json;
   if(options.count("genesis-json"))
      genesis_json = options.at("genesis-json").as<std::string>();

   my.reset(new engine_plugin_impl(chain_data, threads, max_readers, address, genesis_json));
   SILK_INFO << "Initializing Engine Plugin";
}

void engine_plugin::plugin_startup() {
   my->startup();
}

void engine_plugin::plugin_shutdown() {
   my->shutdown();
   SILK_INFO << "Shutdown engine plugin";
}

mdbx::env* engine_plugin::get_db() {
   return &my->db_env;
}

std::optional<silkworm::BlockHeader> engine_plugin::get_head_canonical_header() {
   return my->get_head_canonical_header();
}

std::optional<silkworm::Block> engine_plugin::get_canonical_block_at_height(std::optional<uint64_t> height) {
   return my->get_canonical_block_at_height(height);
}

void engine_plugin::record_evm_lib(uint64_t height) {
   return my->record_evm_lib(height);
}

std::optional<uint64_t> engine_plugin::get_evm_lib() {
   return my->get_evm_lib();
}

std::optional<silkworm::BlockHeader> engine_plugin::get_genesis_header() {
   return my->get_genesis_header();
}

silkworm::NodeSettings* engine_plugin::get_node_settings() {
   return &my->node_settings;
}

std::string engine_plugin::get_address() const {
   return my->server_settings.address_uri;
}

uint32_t engine_plugin::get_threads() const {
   return my->server_settings.context_pool_settings.num_contexts;
}

std::string engine_plugin::get_chain_data_dir() const {
   return my->node_settings.data_directory->chaindata().path().string();
}