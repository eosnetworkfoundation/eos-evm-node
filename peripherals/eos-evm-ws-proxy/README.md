# eos-evm-ws-proxy

### Installation

1. **Clone the Repository**
   ```
   git clone https://github.com/eosnetworkfoundation/eos-evm-node
   cd eos-evm-node/peripherals/eos-evm-ws-proxy
   ```

2. **Install Dependencies**
   ```
   npm install
   ```

3. **Setup Configuration**
 
   Copy the `.env-sample` file and rename it to `.env`.
   ```
   cp .env-sample .env
   ```

   Modify the `.env` file to adjust the configuration to your setup.

### Running the Proxy

Execute the `main.js` script to start the proxy server:

```
node main.js
```

## Configuration

The following environment variables are available for configuration in the `.env` file:

- `WEB3_RPC_ENDPOINT`: The endpoint for the eos-evm-rpc
- `NODEOS_RPC_ENDPOINT`: The endpoint for the nodeos RPC
- `POLL_INTERVAL`: The interval (in milliseconds) at which the blockchain is polled
- `WS_LISTENING_PORT`: The port on which the WebSocket server listens
- `WS_LISTENING_HOST`: The host address on which the WebSocket server listens
- `MAX_LOGS_SUBS_PER_CONNECTION`: The maximum number of `logs`` subscriptions per connection.
- `MAX_MINEDTX_SUBS_PER_CONNECTION`: The maximum number of `minedTransactions` subscriptions per connection.
- `LOG_LEVEL`: Logging level (e.g., `debug`).
- `GENESIS_JSON`: full file path of evm genesis.json, defaults to 'eos-evm-genesis.json'. For EOS EVM mainnet, you can download a copy of genesis.json from https://github.com/eosnetworkfoundation/evm-public-docs/blob/main/mainnet-genesis.json.

