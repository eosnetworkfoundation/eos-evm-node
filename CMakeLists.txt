cmake_minimum_required(VERSION 3.16.2)

list(APPEND CMAKE_MODULE_PATH "${CMAKE_CURRENT_SOURCE_DIR}/CMakeModules")

include(cmake/cable/bootstrap.cmake)
include(CableBuildType)
include(utils)
cable_set_build_type(DEFAULT Release CONFIGURATION_TYPES Release Debug)

if(NOT CMAKE_TOOLCHAIN_FILE)
set(CMAKE_TOOLCHAIN_FILE
    ${CMAKE_CURRENT_SOURCE_DIR}/cmake/toolchain/cxx20.cmake
    CACHE FILEPATH "" FORCE
)
endif()

include(cmake/conan.cmake)

project(eos-evm-node)

set(VERSION_MAJOR 0)
set(VERSION_MINOR 7)
set(VERSION_PATCH 0)
set(VERSION_SUFFIX rc2)

if(VERSION_SUFFIX)
    set(VERSION_FULL "${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}-${VERSION_SUFFIX}")
else()
    set(VERSION_FULL "${VERSION_MAJOR}.${VERSION_MINOR}.${VERSION_PATCH}")
endif()

add_subdirectory(external)
add_subdirectory(version)
add_subdirectory(src)
add_subdirectory(peripherals)
add_subdirectory(tests)
