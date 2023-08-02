# EOS EVM NODE

## Overview

The EOS EVM Node consumes Antelope (EOS) blocks from a Leap node via state history (SHiP) endpoint and builds the virtual EVM blockchain in a deterministic way.
The EOS EVM RPC will talk with the EOS EVM node, and provide read-only Ethereum compatible RPC services for clients (such as MetaMask).

Clients can also push Ethereum compatible transactions (aka EVM transactions) to the EOS blockchain, via proxy and Transaction Wrapper (TX-Wrapper), which encapsulates EVM transactions into Antelope transactions. All EVM transactions will be validated and executed by the EOS EVM Contract deployed on the EOS blockchain.

```
         |                                                 
         |                     WRITE              +-----------------+
         |             +------------------------->|    TX-Wrapper   |
         |             |                          +-------v---------+
         |             |                          |    Leap node    | ---> connect to the other nodes in the blockchain network
 client  |             |                          +-------+---------+
 request |       +-----+-----+                            |
---------+------>|   Proxy   |                            |
         |       +-----------+                            v       
         |             |                          +-----------------+
         |        READ |     +--------------+     |                 |
         |             +---->|  EOS EVM RPC |---->|   EOS EVM Node  +
         |                   +--------------+     |                 |
         |                                        +-----------------+
```
         
## Compilation

### checkout the source code:
```
git clone https://github.com/eosnetworkfoundation/eos-evm-node.git
cd eos-evm-node
git submodule update --init --recursive
```

### compile eos-evm-node, eos-evm-rpc

Prerequisites:
- Ubuntu 22 or later or other compatible Linux
- gcc 11 or later

Easy Steps:
```
mkdir build
cd build
cmake ..
make -j8
```
You'll get the list of binaries with other tools:
```
src/eos-evm-node
src/eos-evm-rpc
```

Alternatively, to build with specific compiler:
```
mkdir build
cd build
cmake -DCMAKE_BUILD_TYPE=Debug -DCMAKE_C_COMPILER=gcc -DCMAKE_CXX_COMPILER=g++ ..
make -j8
```

## Deployments

For local testnet deployment and testings, please refer to 
https://github.com/eosnetworkfoundation/eos-evm/blob/main/docs/local_testnet_deployment_plan.md

For public testnet deployment, please refer to 
https://github.com/eosnetworkfoundation/eos-evm/blob/main/docs/public_testnet_deployment_plan.md

## CI
This repo contains the following GitHub Actions workflows for CI:
- EOS EVM Node CI - build the EOS EVM node
    - [Pipeline](https://github.com/eosnetworkfoundation/eos-evm-node/actions/workflows/node.yml)
    - [Documentation](./.github/workflows/node.md)

See the pipeline documentation for more information.
