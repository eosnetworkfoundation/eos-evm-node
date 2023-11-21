const EventEmitter = require('events');
const axios = require('axios');
const {Web3} = require('web3');
const Deque = require('collections/deque');
const {num_from_id, convert_to_epoch} = require('./utils');
const { clearTimeout } = require('timers');
class BlockMonitor extends EventEmitter {

  constructor({ web3_rpc_endpoint, nodeos_rpc_endpoint, poll_interval, genesis, logger}) {
    super();
    this.web3_rpc_endpoint = web3_rpc_endpoint;
    this.nodeos_rpc_endpoint = nodeos_rpc_endpoint;
    this.poll_interval = poll_interval;
    this.web3 = new Web3(web3_rpc_endpoint);
    this.logger = logger;
    this.genesis = genesis;

    if(this.genesis == undefined || this.genesis.timestamp == undefined) throw ("invalid genesis timestamp");
    this.genesis_timestamp = parseInt(this.genesis.timestamp, 16);
    this.logger.debug(`Using genesis timestamp: ${this.genesis_timestamp}`);

    this.reversible_blocks = new Deque();
    this.run = false;
    this.timer_id = null;
  }

  async get_evm_lib() {
    let response = await axios.post(this.nodeos_rpc_endpoint+'/v1/chain/get_info', {});
    response = await axios.post(this.nodeos_rpc_endpoint+'/v1/chain/get_block', {block_num_or_id:response.data.last_irreversible_block_num});
    let lib = this.timestamp_to_evm_block_num(convert_to_epoch(response.data.timestamp));
    this.logger.debug(`BlockMonitor::get_evm_lib ${lib}`);
    return lib;
  }

  timestamp_to_evm_block_num(timestamp) {
    const block_interval = 1;
    if (timestamp < this.genesis_timestamp) {
       return 0;
    }
    return 1 + Math.floor((timestamp - this.genesis_timestamp) / block_interval);
 }


  remove_front_block() {
    const block = this.reversible_blocks.shift();
    this.logger.debug(`BlockMonitor::remove_front_block ${block.number}`);
    this.emit('block_removed', {block});
  }

  fork_last_block() {
    const block = this.reversible_blocks.pop();
    this.logger.debug(`BlockMonitor::fork_last_block ${block.number}`);
    this.emit('block_forked', {block});
    return this.reversible_blocks.peekBack();
  }

  append_new_block(block) {
    this.reversible_blocks.add(block);
    this.logger.debug(`BlockMonitor::append_new_block ${block.number} ${block.hash}`);
    this.emit('block_appended', {block});
    return block;
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

    block.logs = logs;
    //console.log("RPC batch result:" + JSON.stringify(block));
    return block;
  }

  async get_next_block(block) {
    // need to be conservative, sometimes getLogs return empty result for head block
    let head_block = await this.web3.eth.getBlock("latest", true);
    if( head_block == null ) return null;

    if(block == null)
      return await this.getBlockWithLogs(Number(head_block.number) - 1);

    let next_block_num = Number(block.number) + 1;

    let max_block_num = Number(head_block.number) - 1;
    if (next_block_num >= max_block_num) {
      return null;
    }

    return await this.getBlockWithLogs(next_block_num);
  }

  async poll() {
    try {

      let last = this.reversible_blocks.peekBack();
      if( last == undefined ) {
        this.logger.debug("BlockMonitor::poll last not defined");
        last = await this.get_next_block()
        if (last != null) {
          this.logger.debug(`BlockMonitor::poll Obtained ${last.number}`);
          this.append_new_block(last);
        } else {
          throw new Error("BlockMonitor::poll Unable to get block");
        }
      }

      let next_block = await this.get_next_block(last);
      let found_next_block = false;
      while(last != null && next_block != null) {
        found_next_block = true;
        if(next_block.parentHash == last.hash) {
          last = this.append_new_block(next_block);
          next_block = await this.get_next_block(last);
        } else {
          this.logger.debug(`BlockMonitor::poll next: ${next_block.number} ${next_block.parentHash} != last: ${last.number} ${last.hash}`);
          last = this.fork_last_block();
          next_block = await this.get_next_block(last);
        }
      }

      while(this.reversible_blocks.length > 1024) { // protection of memory grow in case leap rpc not working
        this.remove_front_block();
      }

      if( found_next_block == true && this.reversible_blocks.length > 180 + 30) { // reduce frequency of calling get_evm_lib
        const evm_lib = await this.get_evm_lib();
        // keep at least 180 blocks in case evm-node is out of sync with leap
        while(this.reversible_blocks.length > 180 && this.reversible_blocks.peek().number < evm_lib) {
          this.remove_front_block();
        }
      }

    } catch (error) {
      this.logger.error(`BlockMonitor::poll => ERR: ${error.message}`);
    }

    if(this.run == true) {
      if (this.timer_id != null) clearTimeout(this.timer_id);
      this.timer_id = setTimeout(() => this.poll(), this.poll_interval || 5000);
    } else {
      this.reversible_blocks.clear();
      this.logger.info("BlockMonitor::poll => Stopped");
      if (this.timer_id != null) clearTimeout(this.timer_id);
    }
  }

  start() {
    if( this.run == true ) return;
    this.logger.info("BlockMonitor::start => BlockMonitor starting");
    this.run = true;
    if (this.timer_id != null) clearTimeout(this.timer_id);
    this.timer_id = setTimeout(() => this.poll(), 0);
  }

  stop() {
    this.logger.info("BlockMonitor::stop => BlockMonitor stopping");
    this.run = false;
  }

  is_running() {
    return this.run;
  }
}

module.exports = BlockMonitor;
