#!/bin/bash
if [ ! -f .env ]; then
  echo ".env file not found"
  exit 1
fi

source .env

# check required files / folder
if [ ! -d $LEAP_ROOT/build/tests/TestHarness ]; then
  echo $LEAP_ROOT/build/tests/TestHarness not found
  exit 1
fi

if [[ ! -f "$EVM_CONTRACT_ROOT/build/evm_runtime/evm_runtime.wasm" || ! -f "$EVM_CONTRACT_ROOT/build/evm_runtime/evm_runtime.abi" ]]; then
  echo $EVM_CONTRACT_ROOT/build/evm_runtime/evm_runtime wasm/abi not found
  exit 1
fi

if [[ "$CORE_SYMBOL_NAME" != "EOS" ]]; then
  echo "CORE_SYMBOL_NAME is not 'EOS' (warning)"
fi

CLEAN=0
if [ "$1" == "--clean" ]; then
    CLEAN=1
fi

if [ $CLEAN -eq 1 ]; then
    rm -rf venv
fi

if [ ! -d "venv" ]; then
  python3 -m venv venv
  ./venv/bin/pip install web3 flask flask_cors
  ln -s $LEAP_ROOT/build/tests/TestHarness venv/lib/python3.10/site-packages/TestHarness
fi

CORE_SYMBOL_NAME=$CORE_SYMBOL_NAME ./venv/bin/python3 $EVM_NODE_ROOT/tests/nodeos_eos_evm_server.py --eos-evm-contract-root $EVM_CONTRACT_ROOT/build --eos-evm-bridge-contracts-root $EVM_BRIDGE_ROOT/build
mv eos-evm-genesis.json eos-evm-genesis.json.last &> /dev/null
