const EventEmitter = require('events');
const axios = require('axios');
const {Web3} = require('web3');
const Deque = require('collections/deque');
const {num_from_id} = require('./utils');
const { clearTimeout } = require('timers');
class BlockMonitor extends EventEmitter {

  constructor({ web3_rpc_endpoint, nodeos_rpc_endpoint, poll_interval, logger}) {
    super();
    this.web3_rpc_endpoint = web3_rpc_endpoint;
    this.nodeos_rpc_endpoint = nodeos_rpc_endpoint;
    this.poll_interval = poll_interval;
    this.web3 = new Web3(web3_rpc_endpoint);
    this.logger = logger;

    this.reversible_blocks = new Deque();
    this.run = false;
    this.timer_id = null;
  }

  async get_eos_lib() {
    const response = await axios.post(this.nodeos_rpc_endpoint+'/v1/chain/get_info', {});
    return response.data.last_irreversible_block_num;
  }

  remove_front_block() {
    const block = this.reversible_blocks.shift();
    this.emit('block_removed', {block});
  }

  fork_last_block() {
    const block = this.reversible_blocks.pop();
    this.logger.debug(`FORK_LAST_BLOCK ${block}`);
    this.emit('block_forked', {block});
    return this.reversible_blocks.peekBack();
  }

  append_new_block(block, logs) {
    this.reversible_blocks.add(block);
    this.emit('block_appended', {block, logs});
  }

  async getBlockWithLogs(number_) {
    let number = Number(number_);
    
    let id1 = "get_block_" + number;
    let id2 = "get_logs_" + number;
    let requests = [
      {jsonrpc:"2.0",method:"eth_getBlockByNumber",params:["0x" + number.toString(16), true], id: id1},
      {jsonrpc:"2.0",method:"eth_getLogs",params:[{fromBlock: "0x" + number.toString(16), toBlock: "0x" + number.toString(16)}], id: id2}
    ]
    const results = await axios.post(this.web3_rpc_endpoint, requests);

    if (!Array.isArray(results.data) || results.data.length != 2) {
      throw new Error("invalid RPC response of [getBlock, GetPastLogs] batch request");
    }
    const block = results.data[0].result;
    const logs = results.data[1].result;

    //console.log("RPC batch result:" + JSON.stringify(block));
    return {block, logs};
  }

  async poll() {
    let res = null;
    let next_block = null;
    let next_logs = null;
    try {
      // need to be conservative, sometimes getLogs return empty result for head block
      let head_block = await this.web3.eth.getBlock("latest", true);
      let max_block_num = Number(head_block.number) - 1;

      let last = this.reversible_blocks.peekBack();
      if( last == undefined || last == null) {
        res = await this.getBlockWithLogs(max_block_num);
        last = res.block;
        if (last != null) {
          this.append_new_block(last, res.logs);
        }
      }

      if (last != null && Number(last.number) + 1 < max_block_num) {
        res = await this.getBlockWithLogs(Number(last.number) + 1);
        next_block = res.block;
        next_logs = res.logs;
      } else {
        next_block = null;
        next_logs = null;
      }

      let found_next_block = false;

      while(last != null && next_block != null) {
        found_next_block = true;
        if(next_block.parentHash == last.hash) {
          this.append_new_block(next_block, next_logs);
          last = next_block;

          if (Number(last.number) + 1 < max_block_num) {
            res = await this.getBlockWithLogs(Number(last.number) + 1);
            next_block = res.block;
            next_logs = res.logs;
          } else {
            next_block = null;
            next_logs = null;
          }

        } else {
          last = this.fork_last_block();
        }
      }

      if( found_next_block == true ) {
        const eos_lib = await this.get_eos_lib();
        while(this.reversible_blocks.length > 0 && num_from_id(this.reversible_blocks.peek().mixHash) <= eos_lib) {
          this.logger.debug(`eoslib: ${eos_lib} ${num_from_id(this.reversible_blocks.peek().mixHash)} ${this.reversible_blocks.peek().number} ${this.reversible_blocks.peek().mixHash}`);
          this.remove_front_block();
        }
      }

    } catch (error) {
      this.logger.error(error.message);
    }

    if(this.run == true) {
      if (this.timer_id != null) clearTimeout(this.timer_id);
      this.timer_id = setTimeout(() => this.poll(), this.poll_interval || 5000);
    } else {
      this.reversible_blocks.clear();
      this.logger.info("BlockMonitor stopped");
    }
  }

  start() {
    this.logger.info("BlockMonitor start");
    this.run = true;
    if (this.timer_id != null) clearTimeout(this.timer_id);
    this.timer_id = setTimeout(() => this.poll(), 0);
  }

  stop() {
    this.logger.info("BlockMonitor stopping");
    this.run = false; 
    // don't clean up timeout. let poll() cleanup reversible_blocks
  }

  is_running() {
    return this.run;
  }
}

module.exports = BlockMonitor;
