require('dotenv').config();
const ws_listening_port = parseInt(process.env.WS_LISTENING_PORT, 10) || 3333;
const ws_listening_host = process.env.WS_LISTENING_HOST || "localhost";
const web3_rpc_endpoint = process.env.WEB3_RPC_ENDPOINT || "http://127.0.0.1:8881/";
const web3_rpc_test_endpoint = process.env.WEB3_RPC_TEST_ENDPOINT || "http://127.0.0.1:8882/";
const nodeos_rpc_endpoint = process.env.NODEOS_RPC_ENDPOINT || "http://127.0.0.1:8888/";
const miner_rpc_endpoint = process.env.MINER_RPC_ENDPOINT || "http://127.0.0.1:18888/";
const whitelist_methods = process.env.WHITELIST_METHODS || "net_version,eth_blockNumber,eth_chainId,eth_protocolVersion,eth_getBlockByHash,eth_getBlockByNumber,eth_getBlockTransactionCountByHash,eth_getBlockTransactionCountByNumber,eth_getUncleByBlockHashAndIndex,eth_getUncleByBlockNumberAndIndex,eth_getUncleCountByBlockHash,eth_getUncleCountByBlockNumber,eth_getTransactionByHash,eth_getRawTransactionByHash,eth_getTransactionByBlockHashAndIndex,eth_getRawTransactionByBlockHashAndIndex,eth_getTransactionByBlockNumberAndIndex,eth_getRawTransactionByBlockNumberAndIndex,eth_getTransactionReceipt,eth_getBlockReceipts,eth_estimateGas,eth_getBalance,eth_getCode,eth_getTransactionCount,eth_getStorageAt,eth_call,eth_callBundle,eth_createAccessList";
const poll_interval = parseInt(process.env.POLL_INTERVAL, 10) || 1000;
const max_logs_subs_per_connection = parseInt(process.env.MAX_LOGS_SUBS_PER_CONNECTION, 10) || 1;
const max_minedtx_subs_per_connection = parseInt(process.env.MAX_MINEDTX_SUBS_PER_CONNECTION, 10) || 1;
const log_level = process.env.LOG_LEVEL || 'info';
const genesis_json = process.env.GENESIS_JSON || 'eos-evm-genesis.json';

module.exports = {
  ws_listening_port,
  ws_listening_host,
  web3_rpc_endpoint,
  web3_rpc_test_endpoint,
  nodeos_rpc_endpoint,
  miner_rpc_endpoint,
  poll_interval,
  max_logs_subs_per_connection,
  max_minedtx_subs_per_connection,
  log_level,
  whitelist_methods,
  genesis_json
};
