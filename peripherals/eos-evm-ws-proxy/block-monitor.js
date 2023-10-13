const EventEmitter = require('events');
const axios = require('axios');
const {Web3} = require('web3');
const Deque = require('collections/deque');
const {num_from_id} = require('./utils');
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

  append_new_block(block) {
    this.reversible_blocks.add(block);
    this.emit('block_appended', {block});
  }

  async poll() {
    try {
      let last = this.reversible_blocks.peekBack();
      if( last == undefined ) {
        last = await this.web3.eth.getBlock("latest", true);
        this.append_new_block(last);
      }

      let next_block = await this.web3.eth.getBlock(last.number+BigInt(1), true);
      let found_next_block = false;
      while(last != null && next_block != null) {
        found_next_block = true;
        if(next_block.parentHash == last.hash) {
          this.append_new_block(next_block);
          last = next_block;
          next_block = await this.web3.eth.getBlock(last.number+BigInt(1), true);
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
      this.timer_id = setTimeout(() => this.poll(), this.poll_interval || 5000);
    } else {
      this.reversible_blocks.clear();
      this.logger.info("BlockMonitor stopped");
    }
  }

  start() {
    this.logger.info("BlockMonitor start");
    this.run = true;
    setTimeout(() => this.poll(), 0);
  }

  stop() {
    clearTimeout(this.timer_id);
    this.logger.info("BlockMonitor stopping");
    this.run = false;
  }

  is_running() {
    return this.run;
  }
}

module.exports = BlockMonitor;
