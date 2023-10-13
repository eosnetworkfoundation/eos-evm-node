find_package(eosio)

set(SILKWORM_LIBRARIES silkworm_core silkworm_infra silkworm_node)

add_executable(trx_generator main.cpp trx_generator.cpp trx_provider.cpp)

target_include_directories(trx_generator PUBLIC ${CMAKE_CURRENT_BINARY_DIR} ${CMAKE_CURRENT_SOURCE_DIR})

target_link_libraries(trx_generator PRIVATE EosioChain Boost::program_options Boost::beast ${SILKWORM_LIBRARIES} ${CMAKE_DL_LIBS} ${PLATFORM_SPECIFIC_LIBS})
