#!/bin/bash
if [ ! -f .env ]; then
  echo ".env file not found"
  exit 1
fi

source .env
trap "kill 0" SIGINT

CLEAN=0
if [ "$1" == "--clean" ]; then
    CLEAN=1
fi

if [ $CLEAN -eq 1 ]; then
    rm -rf data-evm-node
fi

mkdir -p data-evm-node/snapshots

if [ ! -f $EVM_NODE_ROOT/build/src/eos-evm-node ]; then
  echo $EVM_NODE_ROOT/build/src/eos-evm-node not found
  exit 1
fi

if [ ! -f $EVM_NODE_ROOT/build/src/eos-evm-rpc ]; then
  echo $EVM_NODE_ROOT/build/src/eos-evm-rpc not found
  exit 1
fi

if [ ! -f $EVM_NODE_ROOT/peripherals/eos-evm-ws-proxy/main.js ]; then
  echo $EVM_NODE_ROOT/peripherals/eos-evm-ws-proxy/main.js not found
  exit 1
fi

if [ ! -f eos-evm-genesis.json ]; then
  echo "Waiting for eos-evm-genesis.json ..."
  while [ ! -f eos-evm-genesis.json ]; do
    sleep 1
  done
fi

echo "Launching EOS EVM Node"
$EVM_NODE_ROOT/build/src/eos-evm-node \
  --plugin=blockchain_plugin \
  --ship-endpoint=127.0.0.1:8999 \
  --genesis-json=eos-evm-genesis.json \
  --ship-core-account=eosio.evm \
  --chain-data=data-evm-node \
  --ship-max-retry=1000000 \
  --ship-delay-second=2 \
  --stdout=1 \
  --nocolor=1 \
  --verbosity=5 > node.log &

sleep 2

echo "Launching EOS EVM Rpc"
$EVM_NODE_ROOT/build/src/eos-evm-rpc \
  --eos-evm-node=127.0.0.1:8080 \
  --http-port=0.0.0.0:8881 \
  --chaindata=data-evm-node \
  --stdout=1 \
  --nocolor=1 \
  --verbosity=10 \
  --api-spec=eth,debug,net,trace > rpc.log &

sleep 2

echo "Launching EOS EVM WS proxy"
if [ ! -d "node_modules" ]; then
  cp $EVM_NODE_ROOT/peripherals/eos-evm-ws-proxy/package.json .
  npm install
fi

node $EVM_NODE_ROOT/peripherals/eos-evm-ws-proxy/main.js > ws.log &

wait
