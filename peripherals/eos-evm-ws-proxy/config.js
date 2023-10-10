require('dotenv').config();
const ws_listening_port = parseInt(process.env.WS_LISTENING_PORT, 10) || 3333;
const ws_listening_host = process.env.WS_LISTENING_HOST || "localhost";
const web3_rpc_endpoint = process.env.WEB3_RPC_ENDPOINT || "http://localhost:5000/";
const nodeos_rpc_endpoint = process.env.NODEOS_RPC_ENDPOINT || "http://127.0.0.1:8888/";
const poll_interval = parseInt(process.env.POLL_INTERVAL, 10) || 1000;
const max_logs_subs_per_connection = parseInt(process.env.MAX_LOGS_SUBS_PER_CONNECTION, 10) || 1;
const max_minedtx_subs_per_connection = parseInt(process.env.MAX_MINEDTX_SUBS_PER_CONNECTION, 10) || 1;
const log_level = process.env.LOG_LEVEL || 'info';

module.exports = {
  ws_listening_port,
  ws_listening_host,
  web3_rpc_endpoint,
  nodeos_rpc_endpoint,
  poll_interval,
  max_logs_subs_per_connection,
  max_minedtx_subs_per_connection,
  log_level
};
