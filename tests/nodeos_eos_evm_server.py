#!/usr/bin/env python3
import random
import os
import sys
import json
import time
import signal
import calendar
from datetime import datetime
import tempfile

from flask import Flask, request, jsonify
from flask_cors import CORS
from eth_hash.auto import keccak
import requests
import json

from binascii import unhexlify

sys.path.append(os.getcwd())
sys.path.append(os.path.join(os.getcwd(), "tests"))

from TestHarness import Cluster, TestHelper, Utils, WalletMgr, createAccountKeys
from TestHarness.TestHelper import AppArgs
from TestHarness.testUtils import ReturnType
from TestHarness.core_symbol import CORE_SYMBOL

from antelope_name import convert_name_to_value

###############################################################
# nodeos_eos_evm_server
#
# Set up a EOS EVM env
#
# This test sets up 2 producing nodes and one "bridge" node using test_control_api_plugin.
#   One producing node has 3 of the elected producers and the other has 1 of the elected producers.
#   All the producers are named in alphabetical order, so that the 3 producers, in the one production node, are
#       scheduled first, followed by the 1 producer in the other producer node. Each producing node is only connected
#       to the other producing node via the "bridge" node.
#   The bridge node has the test_control_api_plugin, which exposes a restful interface that the test script uses to kill
#       the "bridge" node when /fork endpoint called.
#
# --eos-evm-contract-root should point to root of EOS EVM Contract build dir
# --genesis-json file to save generated EVM genesis json
# --read-endpoint eos-evm-rpc endpoint (read endpoint)
#
# Example:
#  cd ~/ext/leap/build
#  ~/ext/eos-evm/tests/leap/nodeos_eos_evm_server.py --eos-evm-contract-root ~/ext/eos-evm/contract/build --leave-running
#
#  Launches wallet at port: 9899
#    Example: bin/cleos --wallet-url http://127.0.0.1:9899 ...
#
#  Sets up endpoint on port 5000
#    /            - for req['method'] == "eth_sendRawTransaction"
#    /fork        - create forked chain, does not return until a fork has started
#    /restore     - resolve fork and stabilize chain
#
# Dependencies:
#    pip install eth-hash requests flask flask-cors
###############################################################

Print=Utils.Print
errorExit=Utils.errorExit

appArgs=AppArgs()
appArgs.add(flag="--eos-evm-contract-root", type=str, help="EOS EVM Contract build dir", default=None)
appArgs.add(flag="--eos-evm-bridge-contracts-root", type=str, help="EOS EVM Bridge contracts build dir", default=None)
appArgs.add(flag="--genesis-json", type=str, help="File to save generated genesis json", default="eos-evm-genesis.json")
appArgs.add(flag="--read-endpoint", type=str, help="EVM read endpoint (eos-evm-rpc)", default="http://localhost:8881")
appArgs.add(flag="--use-eos-vm-oc", type=bool, help="EOS EVM Contract build dir", default=False)

args=TestHelper.parse_args({"--keep-logs","--dump-error-details","-v","--leave-running" }, applicationSpecificArgs=appArgs)
debug=args.v
killEosInstances= not args.leave_running
dumpErrorDetails=args.dump_error_details
keepLogs=args.keep_logs
eosEvmContractRoot=args.eos_evm_contract_root
eosEvmBridgeContractsRoot=args.eos_evm_bridge_contracts_root
gensisJson=args.genesis_json
readEndpoint=args.read_endpoint
useEosVmOC=args.use_eos_vm_oc
assert eosEvmContractRoot is not None, "--eos-evm-contract-root is required"

totalProducerNodes=2
totalNonProducerNodes=1
totalNodes=totalProducerNodes+totalNonProducerNodes
maxActiveProducers=21
totalProducers=maxActiveProducers

seed=1
Utils.Debug=debug
testSuccessful=False

random.seed(seed) # Use a fixed seed for repeatability.
cluster=Cluster(keepRunning=args.leave_running, keepLogs=args.keep_logs)
walletMgr=WalletMgr(True)


try:
    TestHelper.printSystemInfo("BEGIN")

    cluster.setWalletMgr(walletMgr)

    # ***   setup topogrophy   ***

    # "bridge" shape connects defprocera through defproducerc (3 in node0) to each other and defproduceru (1 in node01)
    # and the only connection between those 2 groups is through the bridge node

    specificExtraNodeosArgs={}
    # Connect SHiP to node01 so it will switch forks as they are resolved
    specificExtraNodeosArgs[1]="--plugin eosio::state_history_plugin --state-history-endpoint 127.0.0.1:8999 --trace-history --chain-state-history --disable-replay-opts  "
    # producer nodes will be mapped to 0 through totalProducerNodes-1, so the number totalProducerNodes will be the non-producing node
    specificExtraNodeosArgs[totalProducerNodes]="--plugin eosio::test_control_api_plugin  "
    extraNodeosArgs="--contracts-console --resource-monitor-not-shutdown-on-threshold-exceeded"
    if useEosVmOC:
        extraNodeosArgs += " --wasm-runtime eos-vm-jit --eos-vm-oc-enable"

    Print("Stand up cluster")
    if cluster.launch(topo="bridge", pnodes=totalProducerNodes,
                      totalNodes=totalNodes, totalProducers=totalProducers,
                      extraNodeosArgs=extraNodeosArgs, specificExtraNodeosArgs=specificExtraNodeosArgs) is False:
        Utils.cmdError("launcher")
        Utils.errorExit("Failed to stand up eos cluster.")

    Print("Validating system accounts after bootstrap")
    cluster.validateAccounts(None)

    Print ("Wait for Cluster stabilization")
    # wait for cluster to start producing blocks
    if not cluster.waitOnClusterBlockNumSync(3):
        errorExit("Cluster never stabilized")
    Print ("Cluster stabilized")

    prodNode = cluster.getNode(0)
    prodNode0 = prodNode
    prodNode1 = cluster.getNode(1)
    nonProdNode = cluster.getNode(2)

    total_accounts_to_create = 6
    if eosEvmBridgeContractsRoot:
        total_accounts_to_create += 3

    accounts=createAccountKeys(total_accounts_to_create)
    if accounts is None:
        Utils.errorExit("FAILURE - create keys")

    evmAcc = accounts[0]
    evmAcc.name = "eosio.evm"
    evmAcc.activePrivateKey="5KQwrPbwdL6PhXujxW37FSSQZ1JiwsST4cqQzDeyXtP79zkvFD3"
    evmAcc.activePublicKey="EOS6MRyAjQq8ud7hVNYcfnVPJqcVpscN5So8BhtHuGYqET5GDW5CV"
        
    accounts[1].name="tester111111" # needed for voting
    accounts[2].name="tester222222" # needed for voting
    accounts[3].name="tester333333" # needed for voting
    accounts[4].name="tester444444" # needed for voting
    accounts[5].name="tester555555" # needed for voting

    if eosEvmBridgeContractsRoot:
        evmIn = accounts[6]
        evmIn.name = "eosio.evmin"
        evmIn.activePrivateKey="5KQwrPbwdL6PhXujxW37FSSQZ1JiwsST4cqQzDeyXtP79zkvFD3"
        evmIn.activePublicKey="EOS6MRyAjQq8ud7hVNYcfnVPJqcVpscN5So8BhtHuGYqET5GDW5CV"

        evmErc20 = accounts[7]
        evmErc20.name = "eosio.erc2o"
        evmErc20.activePrivateKey="5KQwrPbwdL6PhXujxW37FSSQZ1JiwsST4cqQzDeyXtP79zkvFD3"
        evmErc20.activePublicKey="EOS6MRyAjQq8ud7hVNYcfnVPJqcVpscN5So8BhtHuGYqET5GDW5CV"

        simpleToken = accounts[8]
        simpleToken.name = "simpletoken"
        simpleToken.activePrivateKey="5KQwrPbwdL6PhXujxW37FSSQZ1JiwsST4cqQzDeyXtP79zkvFD3"
        simpleToken.activePublicKey="EOS6MRyAjQq8ud7hVNYcfnVPJqcVpscN5So8BhtHuGYqET5GDW5CV"

    testWalletName="test"
    Print("Creating wallet \"%s\"." % (testWalletName))
    tmp=[cluster.eosioAccount,accounts[0],accounts[1],accounts[2],accounts[3],accounts[4],accounts[5]]
    if eosEvmBridgeContractsRoot: tmp += [accounts[6],accounts[7],accounts[8]]
    testWallet=walletMgr.create(testWalletName, tmp)

    for _, account in cluster.defProducerAccounts.items():
        walletMgr.importKey(account, testWallet, ignoreDupKeyWarning=True)

    for i in range(0, totalNodes):
        node=cluster.getNode(i)
        node.producers=Cluster.parseProducers(i)
        numProducers=len(node.producers)
        for prod in node.producers:
            prodName = cluster.defProducerAccounts[prod].name
            if prodName == "defproducera" or prodName == "defproducerb" or prodName == "defproducerc" or prodName == "defproduceru":
                Print("Register producer %s" % cluster.defProducerAccounts[prod].name)
                trans=node.regproducer(cluster.defProducerAccounts[prod], "http::/mysite.com", 0, waitForTransBlock=False, exitOnError=True)

    # create accounts via eosio as otherwise a bid is needed
    for account in accounts:
        Print("Create new account %s via %s with private key: %s" % (account.name, cluster.eosioAccount.name, account.activePrivateKey))
        trans=nonProdNode.createInitializeAccount(account, cluster.eosioAccount, stakedDeposit=0, waitForTransBlock=True, stakeNet=10000, stakeCPU=10000, buyRAM=10000000, exitOnError=True)
        #   max supply 1000000000.0000 (1 Billion)
        transferAmount="50000000.0000 {0}".format(CORE_SYMBOL)
        Print("Transfer funds %s from account %s to %s" % (transferAmount, cluster.eosioAccount.name, account.name))
        nonProdNode.transferFunds(cluster.eosioAccount, account, transferAmount, "test transfer", waitForTransBlock=True)
        trans=nonProdNode.delegatebw(account, 20000000.0000, 20000000.0000, waitForTransBlock=False, exitOnError=True)

    # ***   vote using accounts   ***

    cluster.waitOnClusterSync(blockAdvancing=3)

    # vote a,b,c  u
    voteProducers=[]
    voteProducers.append("defproducera")
    voteProducers.append("defproducerb")
    voteProducers.append("defproducerc")
    voteProducers.append("defproduceru")
    for account in accounts:
        Print("Account %s vote for producers=%s" % (account.name, voteProducers))
        trans=prodNode.vote(account, voteProducers, exitOnError=True, waitForTransBlock=False)

    #verify nodes are in sync and advancing
    cluster.waitOnClusterSync(blockAdvancing=3)
    Print("Shutdown unneeded bios node")
    cluster.biosNode.kill(signal.SIGTERM)

    # setup evm

    contractDir=eosEvmContractRoot + "/evm_runtime"
    wasmFile="evm_runtime.wasm"
    abiFile="evm_runtime.abi"
    Utils.Print("Publish evm_runtime contract")
    prodNode.publishContract(evmAcc, contractDir, wasmFile, abiFile, waitForTransBlock=True)

    # add eosio.code permission
    cmd="set account permission eosio.evm active --add-code -p eosio.evm@active"
    prodNode.processCleosCmd(cmd, cmd, silentErrors=True, returnType=ReturnType.raw)

    trans = prodNode.pushMessage(evmAcc.name, "init", '{"chainid":15555, "fee_params": {"gas_price": "150000000000", "miner_cut": 10000, "ingress_bridge_fee": null}}', '-p eosio.evm')

    prodNode.waitForTransBlockIfNeeded(trans[1], True)

    transId=prodNode.getTransId(trans[1])
    blockNum = prodNode.getBlockNumByTransId(transId)
    block = prodNode.getBlock(blockNum)
    Utils.Print("Block Id: ", block["id"])
    Utils.Print("Block timestamp: ", block["timestamp"])

    genesis_info = {
        "alloc": {
            "0x0000000000000000000000000000000000000000" : {"balance":"0x00"}
        },
        "coinbase": "0x0000000000000000000000000000000000000000",
        "config": {
            "chainId": 15555,
            "homesteadBlock": 0,
            "eip150Block": 0,
            "eip155Block": 0,
            "byzantiumBlock": 0,
            "constantinopleBlock": 0,
            "petersburgBlock": 0,
            "istanbulBlock": 0,
            "trust": {}
        },
        "difficulty": "0x01",
        "extraData": "EOSEVM",
        "gasLimit": "0x7ffffffffff",
        "mixHash": "0x"+block["id"],
        "nonce": f'{convert_name_to_value(evmAcc.name):#0x}',
        "timestamp": hex(int(calendar.timegm(datetime.strptime(block["timestamp"].split(".")[0], '%Y-%m-%dT%H:%M:%S').timetuple())))
    }

    if eosEvmBridgeContractsRoot:
        Utils.Print("Set eosio.evm as privileged account")
        trans=prodNode.pushMessage("eosio", "setpriv", json.dumps({"account":"eosio.evm", "is_priv":1}), '-p eosio@active')

        Utils.Print("Set eosio.erc2o as privileged account")
        trans=prodNode.pushMessage("eosio", "setpriv", json.dumps({"account":"eosio.erc2o", "is_priv":1}), '-p eosio@active')

        Utils.Print("Set eosio.evmin as privileged account")
        trans=prodNode.pushMessage("eosio", "setpriv", json.dumps({"account":"eosio.evmin", "is_priv":1}), '-p eosio@active')

        # Open eosio.erc2o balance on eosio.evm
        Utils.Print("Open eosio.erc2o balance in eosio.evm")
        data="{\"owner\": \"eosio.erc2o\"}"
        trans=prodNode.pushMessage("eosio.evm", "open", data, '-p eosio.erc2o@active')
        prodNode.waitForTransBlockIfNeeded(trans[1], True)

        Utils.Print("Voy a transfer 100 EOS")
        # Fund with 100.0000 EOS eosio.erc2o
        data={"from":"eosio.erc2o", "to":"eosio.evm", "quantity":"100.0000 {0}".format(CORE_SYMBOL), "memo":"eosio.erc2o"}
        trans=prodNode.pushMessage("eosio.token", "transfer", json.dumps(data), '-p eosio.erc2o@active')

        Utils.Print("Set Code")
        # Set eosio.evmin code
        contractDir=eosEvmBridgeContractsRoot + "/antelope_contracts/contracts/deposit_proxy"
        wasmFile="deposit_proxy.wasm"
        tempabi = tempfile.NamedTemporaryFile(delete=True)
        tempabi.write(b"{}")
        tempabi.flush()
        abiFile=tempabi.name
        Utils.Print("Publish deposit_proxy contract")
        prodNode.publishContract(evmIn, contractDir, wasmFile, abiFile, waitForTransBlock=True)
        tempabi.close()

        # add eosio.code permission
        cmd="set account permission eosio.evmin active --add-code -p eosio.evmin@active"
        prodNode.processCleosCmd(cmd, cmd, silentErrors=True, returnType=ReturnType.raw)

        # Set eosio.erc2o code
        contractDir=eosEvmBridgeContractsRoot + "/antelope_contracts/contracts/erc20"
        wasmFile="erc20.wasm"
        abiFile="erc20.abi"
        Utils.Print("Publish erc20 contract")
        prodNode.publishContract(evmErc20, contractDir, wasmFile, abiFile, waitForTransBlock=True)

        # add eosio.code permission
        cmd="set account permission eosio.erc2o active --add-code -p eosio.erc2o@active"
        prodNode.processCleosCmd(cmd, cmd, silentErrors=True, returnType=ReturnType.raw)

        # Call upgradeto
        Utils.Print("Call upgradeto on eosio.erc2o")
        #data="{\"impl_address\": \"0x8ac75488C3B376e13d36CcA6110f985bb65A23c2\"}"
        data="{}"
        opts="-p eosio.erc2o@active"
        trans=prodNode.pushMessage("eosio.erc2o", "upgrade", data, opts)
        prodNode.waitForTransBlockIfNeeded(trans[1], True)

        # Publish simple token
        contract="eosio.token"
        contractDir=str(cluster.unittestsContractsPath / contract)
        wasmFile="%s.wasm" % (contract)
        abiFile="%s.abi" % (contract)
        Utils.Print("Publish simple contract token")
        prodNode.publishContract(simpleToken, contractDir, wasmFile, abiFile, waitForTransBlock=True)

        # Create SIM token
        contract=simpleToken.name
        Utils.Print("Create SIM token")
        action="create"
        data="{\"issuer\":\"%s\",\"maximum_supply\":\"1000000000.0000 %s\"}" % (simpleToken.name, "SIM")
        opts="--permission %s@active" % (simpleToken.name)
        trans=prodNode.pushMessage(contract, action, data, opts)
        prodNode.waitForTransBlockIfNeeded(trans[1], True)

        #Issue SIM token
        Utils.Print("Issue SIM token")
        action="issue"
        data="{\"to\":\"%s\",\"quantity\":\"1000000000.0000 %s\",\"memo\":\"initial issue\"}" % (simpleToken.name, "SIM")
        opts="--permission %s@active" % (simpleToken.name)
        trans=prodNode.pushMessage(contract, action, data, opts)
        prodNode.waitForTransBlockIfNeeded(trans[1], True)

        # Register eosio.erc2o in trustless bridge
        contract="eosio.evm"
        Utils.Print("Register eosio.erc2o")
        action="bridgereg"
        data="{\"handler\": \"eosio.erc2o\", \"min_fee\": \"0.0100 %s\", \"receiver\": \"eosio.erc2o\"}" % CORE_SYMBOL
        opts="-p eosio.evm@active -p eosio.erc2o@active"
        trans=prodNode.pushMessage(contract, action, data, opts)
        prodNode.waitForTransBlockIfNeeded(trans[1], True)

        # Call regtoken on eosio.erc2o
        Utils.Print("Register SIM token on eosio.erc2o")
        data="{\"egress_fee\": \"0.0100 %s\", \"eos_contract_name\": \"simpletoken\", \"erc20_precision\": 6, \"evm_token_name\": \"SIM\", \"evm_token_symbol\": \"SIM\", \"ingress_fee\": \"0.0100 SIM\"}" % CORE_SYMBOL
        opts="-p eosio.erc2o@active"
        trans=prodNode.pushMessage("eosio.erc2o", "regtoken", data, opts)
        prodNode.waitForTransBlockIfNeeded(trans[1], True)
        Utils.Print("SE CAE?")

        # Add egress account
        Utils.Print("Add egress accounts on eosio.erc2o")
        data="{\"accounts\": [\"binancecleos\",\"huobideposit\",\"okbtothemoon\"]}"
        opts="-p eosio.erc2o@active"
        trans=prodNode.pushMessage("eosio.erc2o", "addegress", data, opts)

    Utils.Print("Send small balance to special balance to allow the bridge to work")
    transferAmount="1.0000 {0}".format(CORE_SYMBOL)
    Print("Transfer funds %s from account %s to %s" % (transferAmount, cluster.eosioAccount.name, evmAcc.name))
    nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, transferAmount, evmAcc.name, waitForTransBlock=True)

    # accounts: {
    #    mnemonic: "test test test test test test test test test test test junk",
    #    path: "m/44'/60'/0'/0",
    #    initialIndex: 0,
    #    count: 10,
    #    passphrase: "",
    # }

    addys = {
        "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266":"0x038318535b54105d4a7aae60c08fc45f9687181b4fdfc625bd1a753fa7397fed75,0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80",
        "0x70997970C51812dc3A010C7d01b50e0d17dc79C8":"0x02ba5734d8f7091719471e7f7ed6b9df170dc70cc661ca05e688601ad984f068b0,0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d",
        "0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC":"0x039d9031e97dd78ff8c15aa86939de9b1e791066a0224e331bc962a2099a7b1f04,0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a",
        "0x90F79bf6EB2c4f870365E785982E1f101E93b906":"0x0220b871f3ced029e14472ec4ebc3c0448164942b123aa6af91a3386c1c403e0eb,0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6",
        "0x15d34AAf54267DB7D7c367839AAf71A00a2C6A65":"0x03bf6ee64a8d2fdc551ec8bb9ef862ef6b4bcb1805cdc520c3aa5866c0575fd3b5,0x47e179ec197488593b187f80a00eb0da91f1b9d0b13f8733639f19c30a34926a",
        "0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc":"0x0337b84de6947b243626cc8b977bb1f1632610614842468dfa8f35dcbbc55a515e,0x8b3a350cf5c34c9194ca85829a2df0ec3153be0318b5e2d3348e872092edffba",
        "0x976EA74026E726554dB657fA54763abd0C3a0aa9":"0x029a4ab212cb92775d227af4237c20b81f4221e9361d29007dfc16c79186b577cb,0x92db14e403b83dfe3df233f83dfa3a0d7096f21ca9b0d6d6b8d88b2b4ec1564e",
        "0x14dC79964da2C08b23698B3D3cc7Ca32193d9955":"0x0201f2bf1fa920e77a43c7aec2587d0b3814093420cc59a9b3ad66dd5734dda7be,0x4bbbf85ce3377467afe5d46f804f221813b2bb87f24d81f60f1fcdbf7cbf4356",
        "0x23618e81E3f5cdF7f54C3d65f7FBc0aBf5B21E8f":"0x03931e7fda8da226f799f791eefc9afebcd7ae2b1b19a03c5eaa8d72122d9fe74d,0xdbda1821b80551c9d65939329250298aa3472ba22feea921c0cf5d620ea67b97",
        "0xa0Ee7A142d267C1f36714E4a8F75612F20a79720":"0x023255458e24278e31d5940f304b16300fdff3f6efd3e2a030b5818310ac67af45,0x2a871d0798f97d79848a013d4936a73bf4cc922c825d33c1cf7073dff6d409c6",
    }

    # init with 1,000,000 EOS each
    for i,k in enumerate(addys):
        print("addys: [{0}] [{1}] [{2}]".format(i,k[2:].lower(), len(k[2:])))
        transferAmount="1000000.0000 {0}".format(CORE_SYMBOL)
        Print("Transfer funds %s from account %s to %s" % (transferAmount, cluster.eosioAccount.name, evmAcc.name))
        trans = prodNode.transferFunds(cluster.eosioAccount, evmAcc, transferAmount, "0x" + k[2:].lower(), waitForTransBlock=False)
        if not (i+1) % 20: time.sleep(1)
    prodNode.waitForTransBlockIfNeeded(trans, True)

    if gensisJson[0] != '/': gensisJson = os.path.realpath(gensisJson)
    f=open(gensisJson,"w")
    f.write(json.dumps(genesis_info))
    f.close()
    Utils.Print("#####################################################")
    Utils.Print("Generated EVM json genesis file in: %s" % gensisJson)
    Utils.Print("")
    Utils.Print("You can now run:")
    Utils.Print("  eos-evm-node --plugin=blockchain_plugin --ship-endpoint=127.0.0.1:8999 --genesis-json=%s --chain-data=/tmp --verbosity=4" % gensisJson)
    Utils.Print("  eos-evm-rpc --eos-evm-node=127.0.0.1:8080 --http-port=0.0.0.0:8881 --chaindata=/tmp --api-spec=eth,debug,net,trace")
    Utils.Print("")
    Utils.Print("Web3 endpoint:")
    Utils.Print("  http://localhost:5000")

    app = Flask(__name__)
    CORS(app)

    @app.route("/fork", methods=["POST"])
    def fork():
        Print("Sending command to kill bridge node to separate the 2 producer groups.")
        forkAtProducer="defproducera"
        prodNode1Prod="defproduceru"
        preKillBlockNum=nonProdNode.getBlockNum()
        preKillBlockProducer=nonProdNode.getBlockProducerByNum(preKillBlockNum)
        nonProdNode.killNodeOnProducer(producer=forkAtProducer, whereInSequence=1)
        Print("Current block producer %s fork will be at producer %s" % (preKillBlockProducer, forkAtProducer))
        prodNode0.waitForProducer(forkAtProducer)
        prodNode1.waitForProducer(prodNode1Prod)
        if nonProdNode.verifyAlive(): # if on defproducera, need to wait again
            prodNode0.waitForProducer(forkAtProducer)
            prodNode1.waitForProducer(prodNode1Prod)

        if nonProdNode.verifyAlive():
            Print("Bridge did not shutdown")
            return "Bridge did not shutdown"

        Print("Fork started")
        return "Fork started"

    @app.route("/restore", methods=["POST"])
    def restore():
        Print("Relaunching the non-producing bridge node to connect the producing nodes again")

        if nonProdNode.verifyAlive():
            return "bridge is already running"

        if not nonProdNode.relaunch():
            Utils.errorExit("Failure - (non-production) node %d should have restarted" % (nonProdNode.nodeNum))

        return "restored fork should resolve"

    @app.route("/", methods=["POST"])
    def default():
        def forward_request(req):
            print("METHOD: ", req['method'])
            if req['method'] == "eth_sendRawTransaction":
                actData = {"miner":"eosio.evm", "rlptx":req['params'][0][2:]}
                prodNode1.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p eosio.evm')
                return {
                    "id": req['id'],
                    "jsonrpc": "2.0",
                    "result": '0x'+keccak(unhexlify(req['params'][0][2:])).hex()
                }

            if req['method'] == "eth_gasPrice":
                gas_price=int(prodNode1.getTable(evmAcc.name, evmAcc.name, "config")['rows'][0]['gas_price'])
                return {
                    "id": req['id'],
                    "jsonrpc": "2.0",
                    "result": f'{gas_price:#0x}'
                }

            return requests.post(readEndpoint, json.dumps(req), headers={"Content-Type":"application/json"}).json()

        request_data = request.get_json()
        if type(request_data) == dict:
            return jsonify(forward_request(request_data))

        res = []
        for r in request_data:
            res.append(forward_request(r))

        return jsonify(res)

    app.run(host='0.0.0.0', port=5000)

finally:
    TestHelper.shutdown(cluster, walletMgr, testSuccessful=testSuccessful, dumpErrorDetails=dumpErrorDetails)

exitCode = 0 if testSuccessful else 1
exit(exitCode)
