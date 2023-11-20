const EventEmitter = require('events');
const WebSocketHandler = require('./websocket-handler');
const BlockMonitor = require('./block-monitor');
const {Web3} = require('web3');
const {bigint_replacer} = require('./utils');

class SubscriptionServer extends EventEmitter {

  constructor({web3_rpc_endpoint, web3_rpc_test_endpoint, nodeos_rpc_endpoint, miner_rpc_endpoint, ws_listening_host, ws_listening_port, poll_interval, max_logs_subs_per_connection, max_minedtx_subs_per_connection, logger, whitelist_methods}) {
    super();

    this.block_monitor = new BlockMonitor({web3_rpc_endpoint, nodeos_rpc_endpoint, poll_interval, logger});
    this.web_socket_handler = new WebSocketHandler({ws_listening_host, ws_listening_port, web3_rpc_endpoint, web3_rpc_test_endpoint, miner_rpc_endpoint, logger, whitelist_methods});
    this.max_logs_subs_per_connection = max_logs_subs_per_connection;
    this.max_minedtx_subs_per_connection = max_minedtx_subs_per_connection;
    this.web3 = new Web3(web3_rpc_endpoint);
    this.logger = logger

    this.new_head_subs = new Map();
    this.logs_subs = new Map();
    this.mined_transactions_subs = new Map();
    this.logs_sent = new Map();

    this.web_socket_handler.on('newHeads', ({subid, ws}) => {
      this.handle_new_heads({subid, ws});
    });

    this.web_socket_handler.on('logs', ({subid, ws, filter}) => {
      this.handle_logs({subid, ws, filter});
    });

    this.web_socket_handler.on('minedTransactions', ({subid, ws, filter}) => {
      this.handle_minedTransactions({subid, ws, filter});
    });

    this.web_socket_handler.on('unsubscribe', ({subid, ws}) => {
      this.handle_unsubscribe({subid, ws});
    });

    this.web_socket_handler.on('disconnect', ({ws}) => {
      this.handle_disconnect({ws});
    });

    this.block_monitor.on('block_removed', ({block}) => {
      this.handle_block_removed({block});
    });

    this.block_monitor.on('block_forked', async ({block}) => {
      await this.handle_block_forked({block});
    });

    this.block_monitor.on('block_appended', async ({block}) => {
      await this.handle_block_appended({block});
    });
  }

  handle_block_removed({block}) {
    this.logger.debug(`handle_block_removed: ${block.number}`);
    if(this.logs_sent.has(block.number)) {
      this.logs_sent.delete(block.number);
    }
  }

  async handle_block_forked({block}) {
    this.logger.debug(`handle_block_forked: ${block.number}`);
    const events_sent_at_block = this.logs_sent.get(block.number);
    if (events_sent_at_block != undefined) {
        for(const event_sent of events_sent_at_block) {
          event_sent.msg.params.result.removed = true;
          event_sent.client.ws.send(JSON.stringify(event_sent.msg, bigint_replacer));
        }
        this.logger.debug(`REMOVING ${block.number}`);  
        this.logs_sent.delete(block.number);
    }
    await this.process_mined_transactions_subscriptions(block, true);
  }

  logs_filter_match(filter, log) {
    let address_match = undefined;
    if (filter.hasOwnProperty('addresses')) {
      filter.addresses
      address_match = addresses.includes(log.address);
    }

    let topic_match = undefined;
    if (filter.hasOwnProperty('topics')) {
      topic_match = filter.topics.some(element => log.topics.includes(element));
    }

    return (address_match === undefined && topic_match === undefined) || topic_match || address_match;
  }

  tx_filter_match(filter, tx, removed) {
    if(removed === true && filter.includeRemoved != true) return;
    if (filter.hasOwnProperty('addresses')) {
      for(const ofilter of filter.addresses) {
        if (typeof(ofilter.from) == 'string' && tx.from == ofilter.from) {
          return true;
        }
        if (typeof(ofilter.to) == 'string' && tx.to == ofilter.to) {
          return true;
        }
      }
      return false;
    }
    return true;
  }

  construct_subscription_response(subscription, result) {
    return {
      jsonrpc:"2.0",
      method: "eth_subscription",
      params:{
        subscription:subscription,
        result:result
      }
    };
  }

  async process_new_heads_subscriptions(block) {
    // Process all `newHeads` subscriptions
    for (const [subid, client] of this.new_head_subs) {
      client.ws.send(JSON.stringify(this.construct_subscription_response(subid, block), bigint_replacer));
    }
  }

  send_logs_response_and_save(block, client, subid, log) {
    const msg = this.construct_subscription_response(subid, log);
    client.ws.send(JSON.stringify(msg, bigint_replacer));
    if(!this.logs_sent.has(block.number)) {
      this.logs_sent.set(block.number, []);
    }
    const events_sent_at_block = this.logs_sent.get(block.number);
    events_sent_at_block.push({msg, client});
  }

  send_tx_response(block, client, subid, transaction, removed) {
    if(client.filter.hashesOnly) { transaction = {hash:transaction.hash}; }
    const msg = this.construct_subscription_response(subid, {removed, transaction});
    client.ws.send(JSON.stringify(msg, bigint_replacer));
  }

  async process_logs_subscriptions(block) {
    // Process all `logs` subscriptions
    // Get logs from the recently appended block 
    if(this.logs_subs.size > 0) {
      this.logger.debug("LOG => ", JSON.stringify(block.logs, bigint_replacer));
      for(const log of block.logs) {
        for(const [subid, client] of this.logs_subs) {
          if(this.logs_filter_match(client.filter, log)) {
            this.send_logs_response_and_save(block, client, subid, log);
          }
        }
      }
    }
  }

  async process_mined_transactions_subscriptions(block, removed) {
    // Process all `minedTransactions` subscriptions
    if(this.mined_transactions_subs.size > 0) {
      let transactions = block.transactions || [];
      for(const tx of transactions) {
        this.logger.debug("process_mined_transactions_subscriptions: ", tx);
        for(const [subid, client] of this.mined_transactions_subs) {
          if(this.tx_filter_match(client.filter, tx, removed)) {
            this.send_tx_response(block, client, subid, tx, removed);
          }
        }
      }
    }
  }

  async handle_block_appended({block}) {
    this.logger.debug(`handle_block_appended: ${block.number}`);
    await this.process_new_heads_subscriptions(block);
    await this.process_logs_subscriptions(block);
    await this.process_mined_transactions_subscriptions(block, false);
  }

  check_start_monitor() {
    if(!this.block_monitor.is_running() && (this.new_head_subs.size > 0 || this.logs_subs.size > 0 || this.mined_transactions_subs.size > 0)) {
      this.block_monitor.start();
    }
  }

  check_stop_monitor() {
    if(this.block_monitor.is_running() && this.new_head_subs.size == 0 && this.logs_subs.size == 0 && this.mined_transactions_subs.size == 0) {
      this.block_monitor.stop();
    }
  }

  handle_new_heads({subid, ws}) {
    for (const [_, client] of this.new_head_subs) {
      if(ws === client.ws) { throw new Error('Already subscribed');}
    }
    this.logger.debug(`Adding newHeads subscription ${subid}`);
    this.new_head_subs.set(subid, {ws});
    this.check_start_monitor();
  }

  handle_logs({subid, ws, filter}) {
    let total_logs_subs = 0;
    for (const [_, client] of this.logs_subs) {
      if(ws === client.ws) { ++total_logs_subs;}
    }
    if( total_logs_subs >= this.max_logs_subs_per_connection ) {
      throw new Error('Max logs subscriptions reached');
    }
    this.logger.debug(`Adding logs subscription ${subid}`);
    this.logs_subs.set(subid, {ws, filter});
    this.check_start_monitor();
  }

  handle_minedTransactions({subid, ws, filter}) {
    let total_minedtx_subs = 0;
    for (const [_, client] of this.mined_transactions_subs) {
      if(ws === client.ws) { ++total_minedtx_subs;}
    }
    if( total_minedtx_subs >= this.max_minedtx_subs_per_connection ) {
      throw new Error("Max minedTransactions subscriptions reached");
    }
    this.logger.debug(`Adding minedTransactions subscription ${subid}`);
    this.mined_transactions_subs.set(subid, {ws, filter});
    this.check_start_monitor();
  }

  handle_unsubscribe({subid, ws}) {
    let subscription_map = null;
    if (this.new_head_subs.has(subid)) {
      subscription_map = this.new_head_subs;
    } else if (this.logs_subs.has(subid)) {
      subscription_map = this.logs_subs;
    } else if (this.mined_transactions_subs.has(subid)) {
      subscription_map = this.mined_transactions_subs;
    }

    if (!subscription_map || subscription_map.get(subid).ws !== ws) {
      throw new Error('Not found');
    }

    this.logger.debug(`Unsubscribing ${subid}`);
    subscription_map.delete(subid);
    this.check_stop_monitor();
  }

  handle_disconnect({ws}) {

    for (const [subid, client] of this.new_head_subs) {
      if(ws === client.ws) {
        this.logger.debug(`Removing new_head_subs ${subid}`);
        this.new_head_subs.delete(subid);
      }
    }

    for (const [subid, client] of this.logs_subs) {
      if(ws === client.ws) {
        this.logger.debug(`Removing logs_subs ${subid}`);
        this.logs_subs.delete(subid);
      }
    }

    for (const [subid, client] of this.mined_transactions_subs) {
      if(ws === client.ws) {
        this.logger.debug(`Removing mined_transactions_subs ${subid}`);
        this.mined_transactions_subs.delete(subid);
      }
    }

    this.check_stop_monitor();
  }

  start() {
    this.web_socket_handler.start();
  }

}

module.exports = SubscriptionServer;
