set(SILKWORM_LIBRARIES silkworm_core silkworm_infra silkworm_node)

add_library(sys_plugin sys_plugin.cpp)
target_include_directories(sys_plugin PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(sys_plugin PUBLIC ${SILKWORM_LIBRARIES} appbase )

add_library(engine_plugin engine_plugin.cpp)
target_include_directories(engine_plugin PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
target_link_libraries(engine_plugin PUBLIC ${SILKWORM_LIBRARIES} appbase abieos )

add_library(ship_receiver_plugin ship_receiver_plugin.cpp)
target_include_directories(ship_receiver_plugin PUBLIC ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/external/abieos/src)
target_link_libraries(ship_receiver_plugin PUBLIC ${SILKWORM_LIBRARIES} appbase abieos)

add_library(block_conversion_plugin block_conversion_plugin.cpp)
target_include_directories(block_conversion_plugin PUBLIC ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_SOURCE_DIR}/external/abieos/src)
target_link_libraries(block_conversion_plugin PUBLIC ${SILKWORM_LIBRARIES} appbase abieos)

add_library(blockchain_plugin blockchain_plugin.cpp)
target_include_directories(blockchain_plugin PUBLIC ${CMAKE_CURRENT_SOURCE_DIR} )
target_link_libraries(blockchain_plugin PUBLIC ${SILKWORM_LIBRARIES} appbase abieos )

add_library(rpc_plugin rpc_plugin.cpp)
target_include_directories(rpc_plugin PUBLIC ${CMAKE_CURRENT_SOURCE_DIR} )
target_link_libraries(rpc_plugin PUBLIC ${SILKWORM_LIBRARIES} appbase abieos silkrpc )

add_executable(eos-evm-node eos-evm-node.cpp)
target_include_directories(eos-evm-node PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR})
target_link_libraries(eos-evm-node PRIVATE appbase abieos sys_plugin engine_plugin ship_receiver_plugin block_conversion_plugin blockchain_plugin boost_filesystem version)

add_executable(eos-evm-rpc eos-evm-rpc.cpp)
target_include_directories(eos-evm-rpc PRIVATE ${CMAKE_CURRENT_SOURCE_DIR} ${CMAKE_CURRENT_BINARY_DIR})
target_link_libraries(eos-evm-rpc PRIVATE appbase abieos sys_plugin rpc_plugin boost_filesystem version)

copy_bin( eos-evm-node )
copy_bin( eos-evm-rpc )
