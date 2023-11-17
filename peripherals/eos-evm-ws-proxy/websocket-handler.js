const EventEmitter = require('events');
const http = require('http');
const WebSocket = require('ws');
const { v4: uuidv4 } = require('uuid');
const axios = require('axios');
const { is_plain_object } = require('./utils');

/**
 * WebSocketHandler Class
 * 
 * The WebSocketHandler class is responsible for handling incoming WebSocket connections
 * and forwarding specific Ethereum-based JSON-RPC method calls to an underlying
 * Web3 endpoint. The class also emits events for subscriptions and other activities.
 * 
 * Events:
 *  - 'newHeads': Emitted when a new 'eth_subscribe' request with 'newHeads' is received.
 *      Payload: { subid: String, ws: WebSocket }
 * 
 *  - 'logs': Emitted when a new 'eth_subscribe' request with 'logs' is received.
 *      Payload: { subid: String, ws: WebSocket, filter: Object }
 * 
 *  - 'unsubscribe': Emitted when an 'eth_unsubscribe' request is received.
 *      Payload: { subid: String, ws: WebSocket }
 * 
 *  - 'disconnect': Emitted when a WebSocket connection is closed.
 *      Payload: { ws: WebSocket }
 */

class WebSocketHandler extends EventEmitter {

  constructor({ ws_listening_host, ws_listening_port, web3_rpc_endpoint, miner_rpc_endpoint, logger }) {
    super();

    this.host = ws_listening_host;
    this.port = ws_listening_port;
    this.web3_rpc_endpoint = web3_rpc_endpoint;
    this.miner_rpc_endpoint = miner_rpc_endpoint;
    this.logger = logger;

    this.server = http.createServer((req, res) => {
      this.handle_http_request(req, res);
    });

    this.wss = new WebSocket.Server({ server: this.server });
    this.wss.on('connection', (ws) => {
      this.handle_new_ws_connection(ws);
    });

  }

  handle_http_request(req, res) {
    res.writeHead(404, { 'Content-Type': 'text/plain' });
    res.end('Not Found');
  }

  handle_new_ws_connection(ws) {
    ws.on('message', async (message) => {
      await this.handle_message(ws, message);
    });

    ws.on('close', () => {
      this.handle_close(ws);
    });
  }
  
  handle_eth_subscribe(ws, data) {

    if (!data.hasOwnProperty('params') || !Array.isArray(data.params) || data.params.length == 0) {
      throw new Error("No params");
    }

    const subscription_type = data.params[0];
    if( subscription_type == "newHeads") {
      const subid = uuidv4();
      this.emit('newHeads', {subid, ws});
      return { jsonrpc: "2.0", result: subid, id: data.id };
    } else if (subscription_type == "logs") {
      const subid = uuidv4();
      let filter = {};
      if(data.params.length > 1) {
        filter = data.params[1];
        if (!(is_plain_object(filter) && filter != null)) {
          throw new Error("Invalid filter");
        }
        if (filter.hasOwnProperty('address') && !(typeof filter.address === 'string' || Array.isArray(filter.address))) {
          throw new Error("Invalid address filter");
        }
        if (filter.hasOwnProperty('topics') && !Array.isArray(filter.topics)) {
          throw new Error("Invalid topics filter");
        }
      }
      this.emit('logs', {subid, ws, filter});
      return { jsonrpc: "2.0", result: subid, id: data.id };
    } else if (subscription_type == "minedTransactions") {
      const subid = uuidv4();
      let filter = {};
      if(data.params.length > 1) {
        filter = data.params[1];
        if (!(is_plain_object(filter) && filter != null)) {
          throw new Error("Invalid filter");
        }
        if (filter.hasOwnProperty('addresses')) {
          if(!Array.isArray(filter.addresses)) {
            throw new Error("Invalid addresses filter");
          }
          for(const ofilter of filter.addresses) {
            this.logger.debug("ELEMENT: ", ofilter);
            if(!is_plain_object(ofilter) || (typeof(ofilter.to) != 'string' && typeof(ofilter.from) != 'string') ) {
              throw new Error("Invalid addresses filter element");
            }
            if(typeof(ofilter.to) == 'string') {
              ofilter.to = ofilter.to.toLowerCase();
            }
            if(typeof(ofilter.from) == 'string') {
              ofilter.from = ofilter.from.toLowerCase();
            }
          }
        }
        if (filter.hasOwnProperty('includeRemoved') && typeof(filter.includeRemoved) != 'boolean') {
          throw new Error("Invalid includeRemoved filter");
        }
        if (filter.hasOwnProperty('hashesOnly') && typeof(filter.hashesOnly) != 'boolean') {
          throw new Error("Invalid hashesOnly filter");
        }
      }
      this.emit('minedTransactions', {subid, ws, filter});
      return { jsonrpc: "2.0", result: subid, id: data.id };
    } else {
      throw new Error(`${data.params[0]} not supported`);
    }
  }
 
  handle_eth_unsubscribe(ws, data) {
    if (!data.hasOwnProperty('params') || !Array.isArray(data.params) || data.params.length == 0) {
      throw new Error("Invalid params");
    }
    const subid = data.params[0];
    this.emit('unsubscribe', {subid, ws});
    return { jsonrpc: "2.0", result: true, id: data.id };
  }

  async handle_miner_methods(data) {
    const response = await axios.post(this.miner_rpc_endpoint, data);
    return response.data;
  }

  async handle_other_methods(data) {
    const response = await axios.post(this.web3_rpc_endpoint, data);
    return response.data;
  }

  send_json_rpc_error(ws, id, code, message) {
    ws.send(JSON.stringify({ 
        id      : id,
        jsonrpc : "2.0",
        error   : { code: code, message: message }
    }));
  } 

  async handle_message(ws, message) {

    let data;
    try {
        data = JSON.parse(message);
    } catch (e) {
        ws.send(JSON.stringify({ error: "Invalid JSON" }));
        return;
    }

    if (Array.isArray(data)) {
      let data2 = [];
      for (let i = 0; i < data.length; ++i) {
        switch(data[i].method) {
          case 'eth_subscribe':
          case 'eth_unsubscribe':
          case 'eth_gasPrice':
          case 'eth_sendRawTransaction':
            break;
          default:
            data2.push(data[i]);
        }
      }
      let rpc_response_data = [];
      let rpc_error_message = null;
      if (data2.length > 0) {
        try {
          const rpc_response = await this.handle_other_methods(data2);
          if (Array.isArray(rpc_response)) {
            rpc_response_data = rpc_response;
          } else {
            rpc_error_message = "RPC Server Error:" + JSON.stringify({message: rpc_response});
          }
        } catch (error) {
          rpc_error_message = "RPC Server Error:" + error.message;
        }
      }
      let rpc_index = 0;
      let batch_response = [];
      for (let i = 0; i < data.length; ++i) {
        switch(data[i].method) {
          case 'eth_subscribe':
            try {
              batch_response.push(this.handle_eth_subscribe(ws, data[i]));
            } catch (error) {
              batch_response.push({ 
                id      : data[i].id,
                jsonrpc : "2.0",
                error   : { code: -32000, message: error.message }
              });
            }
            break;
          case 'eth_unsubscribe':
            try {
              batch_response.push(this.handle_eth_unsubscribe(ws, data[i]));
            } catch (error) {
              batch_response.push({ 
                id      : data[i].id,
                jsonrpc : "2.0",
                error   : { code: -32000, message: error.message }
              });
            }
            break;
          case 'eth_gasPrice':
          case 'eth_sendRawTransaction':
            try {
              const response_json = await this.handle_miner_methods(data[i]);
              batch_response.push(response_json);
            } catch (error) {
              batch_response.push({ 
                id      : data[i].id,
                jsonrpc : "2.0",
                error   : { code: -32000, message: error.message }
              });
            }
            break;
          default:
            if (rpc_index < rpc_response_data.length) {
              batch_response.push(rpc_response_data[rpc_index]);
              rpc_index++;
            } else {
              batch_response.push({ 
                id      : data[i].id,
                jsonrpc : "2.0",
                error   : { code: -32000, message: rpc_error_message }
              });
            }
        }
      }
      ws.send(JSON.stringify(batch_response));
    }
    else {
      switch (data.method) {
        case 'eth_subscribe':
          try {
            const response_json = this.handle_eth_subscribe(ws, data);
            ws.send(JSON.stringify(response_json));
          } catch (error) {
            this.send_json_rpc_error(ws, data.id, -32000, error.message);
          }
          break;

        case 'eth_unsubscribe':
          try {
            const response_json = this.handle_eth_unsubscribe(ws, data);  
            ws.send(JSON.stringify(response_json));
          } catch (error) {
            this.send_json_rpc_error(ws, data.id, -32000, error.message);
          }
          break;
        case 'eth_gasPrice':
        case 'eth_sendRawTransaction':
          try {
            const response_json = await this.handle_miner_methods(data);
            ws.send(JSON.stringify(response_json));
          } catch (error) {
            this.send_json_rpc_error(ws, data.id, -32000, "RPC Miner Error:" + error.message);
          }
          break;
        default:
          try {
            const response_json = await this.handle_other_methods(data);
            ws.send(JSON.stringify(response_json));
          } catch (error) {
            this.send_json_rpc_error(ws, data.id, -32000, "RPC Server Error:" + error.message);
          }
          break;
      }
    }
  }

  handle_close(ws) {
    this.emit('disconnect', {ws});
  }

  start() {
    this.server.listen(this.port, this.host, () => {
      console.log(`Websocket listening on ws://${this.host}:${this.port}`);
    });
  }
}

module.exports = WebSocketHandler;