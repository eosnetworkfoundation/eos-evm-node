#include <silkworm/infra/common/log.hpp>

#include <iostream>
#include <string>

#include <appbase/application.hpp>

#include "engine_plugin.hpp"
#include "ship_receiver_plugin.hpp"
#include "sys_plugin.hpp"
#include "blockchain_plugin.hpp"
#include "block_conversion_plugin.hpp"
#include <eosevm/version/version.hpp>

int main(int argc, char* argv[]) {
    try {
        appbase::app().set_version_string(eosevm::version::version_client());
        appbase::app().set_full_version_string(eosevm::version::version_full());
        appbase::app().register_plugin<blockchain_plugin>();
        appbase::app().register_plugin<engine_plugin>();
        appbase::app().register_plugin<ship_receiver_plugin>();
        appbase::app().register_plugin<sys_plugin>();
        appbase::app().register_plugin<block_conversion_plugin>();

        if (!appbase::app().initialize<sys_plugin>(argc, argv))
           return -1;

        appbase::app().startup();
        appbase::app().set_thread_priority_max();
        appbase::app().exec();
    } catch( const std::runtime_error& err) {
        SILK_CRIT << "Error " << err.what();
    } catch( std::exception err ) {
        SILK_CRIT << "Error " << err.what();
    }
}
