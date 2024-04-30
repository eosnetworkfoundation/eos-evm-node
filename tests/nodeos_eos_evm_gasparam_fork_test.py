#!/usr/bin/env python3

import random
import os
import json
import shutil
import shlex
import signal
import subprocess
import sys
import time
import calendar
import websocket
from datetime import datetime
from ctypes import c_uint8

import urllib.request
import urllib.parse
import urllib.error

import sys
from binascii import unhexlify
from web3 import Web3
import rlp

sys.path.append(os.path.join(os.getcwd(), "tests"))

os.environ["CORE_SYMBOL_NAME"]='EOS'
print(f"CORE_SYMBOL_NAME: {os.environ.get('CORE_SYMBOL_NAME')}")

from TestHarness import Cluster, TestHelper, Utils, WalletMgr, CORE_SYMBOL, createAccountKeys
from TestHarness.TestHelper import AppArgs
from TestHarness.testUtils import ReturnType
from TestHarness.testUtils import unhandledEnumType

from antelope_name import convert_name_to_value

###############################################################
# nodeos_eos_evm_ws_test_fork
#
# Set up a EOS EVM env and run leap fork tests with websocket support
# This test is based on both nodeos_eos_evm_ws_test_basic & nodeos_short_fork_take_over_test
#
# Need to install:
#   web3      - pip install web3
#             - pip install otree
#
# --eos-evm-build-root should point to the root of EOS EVM build dir
# --eos-evm-contract-root should point to root of EOS EVM contract build dir
#
#  cd build/tests
# ./nodeos_eos_gasparam_fork_test.py --eos-evm-contract-root ~/workspaces/TrustEVM/build --eos-evm-build-root ~/workspaces/eos-evm-node/build -v
#
#
###############################################################

Print=Utils.Print
errorExit=Utils.errorExit

def analyzeBPs(bps0, bps1, expectDivergence):
    start=0
    index=None
    length=len(bps0)
    firstDivergence=None
    errorInDivergence=False
    analysysPass=0
    bpsStr=None
    bpsStr0=None
    bpsStr1=None
    while start < length:
        analysysPass+=1
        bpsStr=None
        for i in range(start,length):
            bp0=bps0[i]
            bp1=bps1[i]
            if bpsStr is None:
                bpsStr=""
            else:
                bpsStr+=", "
            blockNum0=bp0["blockNum"]
            prod0=bp0["prod"]
            blockNum1=bp1["blockNum"]
            prod1=bp1["prod"]
            numDiff=True if blockNum0!=blockNum1 else False
            prodDiff=True if prod0!=prod1 else False
            if numDiff or prodDiff:
                index=i
                if firstDivergence is None:
                    firstDivergence=min(blockNum0, blockNum1)
                if not expectDivergence:
                    errorInDivergence=True
                break
            bpsStr+=str(blockNum0)+"->"+prod0

        if index is None:
            if expectDivergence:
                errorInDivergence=True
                break
            return None

        bpsStr0=None
        bpsStr2=None
        start=length
        for i in range(index,length):
            if bpsStr0 is None:
                bpsStr0=""
                bpsStr1=""
            else:
                bpsStr0+=", "
                bpsStr1+=", "
            bp0=bps0[i]
            bp1=bps1[i]
            blockNum0=bp0["blockNum"]
            prod0=bp0["prod"]
            blockNum1=bp1["blockNum"]
            prod1=bp1["prod"]
            numDiff="*" if blockNum0!=blockNum1 else ""
            prodDiff="*" if prod0!=prod1 else ""
            if not numDiff and not prodDiff:
                start=i
                index=None
                if expectDivergence:
                    errorInDivergence=True
                break
            bpsStr0+=str(blockNum0)+numDiff+"->"+prod0+prodDiff
            bpsStr1+=str(blockNum1)+numDiff+"->"+prod1+prodDiff
        if errorInDivergence:
            break

    if errorInDivergence:
        msg="Failed analyzing block producers - "
        if expectDivergence:
            msg+="nodes do not indicate different block producers for the same blocks, but they are expected to diverge at some point."
        else:
            msg+="did not expect nodes to indicate different block producers for the same blocks."
        msg+="\n  Matching Blocks= %s \n  Diverging branch node0= %s \n  Diverging branch node1= %s" % (bpsStr,bpsStr0,bpsStr1)
        Utils.errorExit(msg)

    return firstDivergence

def getMinHeadAndLib(prodNodes):
    info0=prodNodes[0].getInfo(exitOnError=True)
    info1=prodNodes[1].getInfo(exitOnError=True)
    headBlockNum=min(int(info0["head_block_num"]),int(info1["head_block_num"]))
    libNum=min(int(info0["last_irreversible_block_num"]), int(info1["last_irreversible_block_num"]))
    return (headBlockNum, libNum)

appArgs=AppArgs()
appArgs.add(flag="--eos-evm-contract-root", type=str, help="EOS EVM contract build dir", default=None)
appArgs.add(flag="--eos-evm-build-root", type=str, help="EOS EVM build dir", default=None)
appArgs.add(flag="--genesis-json", type=str, help="File to save generated genesis json", default="eos-evm-genesis.json")

args=TestHelper.parse_args({"--keep-logs","--dump-error-details","-v","--leave-running" }, applicationSpecificArgs=appArgs)
debug=args.v
killEosInstances= not args.leave_running
dumpErrorDetails=args.dump_error_details
keepLogs=args.keep_logs
eosEvmContractRoot=args.eos_evm_contract_root
eosEvmBuildRoot=args.eos_evm_build_root
genesisJson=args.genesis_json

totalProducerNodes=2
totalNonProducerNodes=1
totalNodes=totalProducerNodes+totalNonProducerNodes
maxActiveProducers=3
totalProducers=maxActiveProducers

assert eosEvmContractRoot is not None, "--eos-evm-contract-root is required"
assert eosEvmBuildRoot is not None, "--eos-evm-build-root is required"

szabo = 1000000000000
seed=1
Utils.Debug=debug
testSuccessful=False

random.seed(seed) # Use a fixed seed for repeatability.
cluster=Cluster(keepRunning=args.leave_running, keepLogs=args.keep_logs)
walletMgr=WalletMgr(True)

evmNodePOpen = None
evmRPCPOpen = None
eosEvmMinerPOpen = None
wsproxy = None

def interact_with_storage_contract(dest, nonce):
    for i in range(1, 5): # execute a few
        Utils.Print("Execute ETH contract")
        nonce += 1
        amount = 0
        gasP=getGasPrice()
        signed_trx = w3.eth.account.sign_transaction(dict(
            nonce=nonce,
            gas=100000,       #100k Gas
            gasPrice=gasP,
            to=Web3.to_checksum_address(dest),
            value=amount,
            data=unhexlify("6057361d00000000000000000000000000000000000000000000000000000000000000%02x" % nonce),
            chainId=evmChainId
        ), evmSendKey)

        actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
        retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name))
        assert retValue[0], "pushtx to ETH contract failed."
        Utils.Print("\tBlock#", retValue[1]["processed"]["block_num"])
        row0=prodNode.getTableRow(evmAcc.name, 3, "storage", 0)
        Utils.Print("\tTable row:", row0)
        time.sleep(1)

    return nonce

def processUrllibRequest(endpoint, payload={}, silentErrors=False, exitOnError=False, exitMsg=None, returnType=ReturnType.json):
    cmd = f"{endpoint}"
    req = urllib.request.Request(cmd, method="POST")
    req.add_header('Content-Type', 'application/json')
    req.add_header('Accept', 'application/json')
    data = payload
    data = json.dumps(data)
    data = data.encode()
    if Utils.Debug: Utils.Print("cmd: %s" % (cmd))
    rtn=None
    start=time.perf_counter()
    try:
        response = urllib.request.urlopen(req, data=data)
        if returnType==ReturnType.json:
            rtn = {}
            rtn["code"] = response.getcode()
            rtn["payload"] = json.load(response)
        elif returnType==ReturnType.raw:
            rtn = response.read()
        else:
            unhandledEnumType(returnType)

        if Utils.Debug:
            end=time.perf_counter()
            Utils.Print("cmd Duration: %.3f sec" % (end-start))
            printReturn=json.dumps(rtn) if returnType==ReturnType.json else rtn
            Utils.Print("cmd returned: %s" % (printReturn[:1024]))
    except urllib.error.HTTPError as ex:
        if not silentErrors:
            end=time.perf_counter()
            msg=ex.msg
            errorMsg="Exception during \"%s\". %s.  cmd Duration=%.3f sec." % (cmd, msg, end-start)
            if exitOnError:
                Utils.cmdError(errorMsg)
                Utils.errorExit(errorMsg)
            else:
                Utils.Print("ERROR: %s" % (errorMsg))
                if returnType==ReturnType.json:
                    rtn = json.load(ex)
                elif returnType==ReturnType.raw:
                    rtn = ex.read()
                else:
                    unhandledEnumType(returnType)
        else:
            return None
    except:
        Utils.Print("Unknown exception occurred during processUrllibRequest")
        raise

    if exitMsg is not None:
        exitMsg=": " + exitMsg
    else:
        exitMsg=""
    if exitOnError and rtn is None:
        Utils.cmdError("could not \"%s\" - %s" % (cmd,exitMsg))
        Utils.errorExit("Failed to \"%s\"" % (cmd))

    return rtn

def getGasPrice():
    return 15000000000

def normalize_address(x, allow_blank=False):
    if allow_blank and x == '':
        return ''
    if len(x) in (42, 50) and x[:2] == '0x':
        x = x[2:]
    if len(x) in (40, 48):
        x = unhexlify(x)
    if len(x) == 24:
        assert len(x) == 24 and sha3(x[:20])[:4] == x[-4:]
        x = x[:20]
    if len(x) != 20:
        raise Exception("Invalid address format: %r" % x)
    return x

def makeContractAddress(sender, nonce):
    return Web3.to_hex(Web3.keccak(rlp.encode([normalize_address(sender), nonce]))[12:])

def makeReservedEvmAddress(account):
    bytearr = [0xbb, 0xbb, 0xbb, 0xbb,
               0xbb, 0xbb, 0xbb, 0xbb,
               0xbb, 0xbb, 0xbb, 0xbb,
               c_uint8(account >> 56).value,
               c_uint8(account >> 48).value,
               c_uint8(account >> 40).value,
               c_uint8(account >> 32).value,
               c_uint8(account >> 24).value,
               c_uint8(account >> 16).value,
               c_uint8(account >>  8).value,
               c_uint8(account >>  0).value]
    return "0x" + bytes(bytearr).hex()

try:
    TestHelper.printSystemInfo("BEGIN")

    w3 = Web3(Web3.HTTPProvider("http://localhost:8881"))

    cluster.setWalletMgr(walletMgr)

    specificExtraNodeosArgs={}
    shipNodeNum = 0
    specificExtraNodeosArgs[shipNodeNum]="--plugin eosio::state_history_plugin --state-history-endpoint 127.0.0.1:8999 --trace-history --chain-state-history --disable-replay-opts  "

    specificExtraNodeosArgs[2]="--plugin eosio::test_control_api_plugin"

    extraNodeosArgs="--contracts-console --resource-monitor-not-shutdown-on-threshold-exceeded"

    Print("Stand up cluster")
    if cluster.launch(prodCount=2, pnodes=2, topo="bridge", totalNodes=3, extraNodeosArgs=extraNodeosArgs, totalProducers=3, specificExtraNodeosArgs=specificExtraNodeosArgs,delay=5) is False:
        errorExit("Failed to stand up eos cluster.")

    Print ("Wait for Cluster stabilization")
    # wait for cluster to start producing blocks
    if not cluster.waitOnClusterBlockNumSync(3):
        errorExit("Cluster never stabilized")
    Print("Cluster stabilized")

    # ***   identify each node (producers and non-producing node)   ***
    Print("Identify each node (producers and non-producing node)")
    nonProdNode=None
    prodNodes=[]
    producers=[]
    for i in range(0, 3):
        node=cluster.getNode(i)
        node.producers=Cluster.parseProducers(i)
        numProducers=len(node.producers)
        Print("node %d has producers=%s" % (i, node.producers))
        if numProducers==0:
            if nonProdNode is None:
                nonProdNode=node
                nonProdNode.nodeNum=i
            else:
                Utils.errorExit("More than one non-producing nodes")
        else:
            prodNodes.append(node)
            producers.extend(node.producers)

    node=prodNodes[0] # producers: defproducera, defproducerb
    node1=prodNodes[1] # producers: defproducerc
    # node2 is the nonProdnode (the bridge node)
    prodNode = prodNodes[0]

    # ***   Identify a block where production is stable   ***
    #verify nodes are in sync and advancing
    cluster.waitOnClusterSync(blockAdvancing=5)
    cluster.biosNode.kill(signal.SIGTERM)

    accounts=createAccountKeys(5)
    if accounts is None:
        Utils.errorExit("FAILURE - create keys")

    evmAcc = accounts[0]
    evmAcc.name = "eosio.evm"
    testAcc = accounts[1]
    minerAcc = accounts[2]
    aliceAcc = accounts[3]
    bobAcc = accounts[4]

    testWalletName="test"

    Print("Creating wallet \"%s\"." % (testWalletName))
    testWallet=walletMgr.create(testWalletName, [cluster.eosioAccount,accounts[0],accounts[1],accounts[2]])

    addys = {
        "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266":"0x038318535b54105d4a7aae60c08fc45f9687181b4fdfc625bd1a753fa7397fed75,0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    }

    numAddys = len(addys)

    # create accounts via eosio as otherwise a bid is needed
    for account in accounts:
        Print("Create new account %s via %s" % (account.name, cluster.eosioAccount.name))
        trans=nonProdNode.createInitializeAccount(account, cluster.eosioAccount, stakedDeposit=0, waitForTransBlock=True, stakeNet=10000, stakeCPU=10000, buyRAM=10000000, exitOnError=True)
        #   max supply 1000000000.0000 (1 Billion)
        transferAmount="100000000.0000 {0}".format(CORE_SYMBOL) # 100 Million
        Print("Transfer funds %s from account %s to %s" % (transferAmount, cluster.eosioAccount.name, account.name))
        nonProdNode.transferFunds(cluster.eosioAccount, account, transferAmount, "test transfer", waitForTransBlock=True)
        if account.name == evmAcc.name:
            # stake more for evmAcc so it has a smaller balance, during setup of addys below the difference will be transferred in
            trans=nonProdNode.delegatebw(account, 20000000.0000 + numAddys*1000000.0000, 20000001.0000, waitForTransBlock=True, exitOnError=True)
        else:
            trans=nonProdNode.delegatebw(account, 20000000.0000, 20000000.0000, waitForTransBlock=True, exitOnError=True)
        time.sleep(1.0) # avoid transaction retry which is unsupported

    contractDir=eosEvmContractRoot + "/evm_runtime"
    wasmFile="evm_runtime.wasm"
    abiFile="evm_runtime.abi"
    Utils.Print("Publish evm_runtime contract")
    prodNode.publishContract(evmAcc, contractDir, wasmFile, abiFile, waitForTransBlock=True)
    time.sleep(1.0)

    # add eosio.code permission
    cmd="set account permission eosio.evm active --add-code -p eosio.evm@active"
    prodNode.processCleosCmd(cmd, cmd, silentErrors=True, returnType=ReturnType.raw)
    time.sleep(1.0)

    trans = prodNode.pushMessage(evmAcc.name, "init", '{{"chainid":15555, "fee_params": {{"gas_price": "10000000000", "miner_cut": 10000, "ingress_bridge_fee": "0.0000 {0}"}}}}'.format(CORE_SYMBOL), '-p eosio.evm')
    time.sleep(1.0)

    Utils.Print("EVM init action pushed:" + str(trans))
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

    Utils.Print("Send small balance to special balance to allow the bridge to work")
    transferAmount="1.0000 {0}".format(CORE_SYMBOL)
    Print("Transfer funds %s from account %s to %s" % (transferAmount, cluster.eosioAccount.name, evmAcc.name))
    nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, transferAmount, evmAcc.name, waitForTransBlock=True)

    Utils.Print("Open balance for miner")
    trans=prodNode.pushMessage(evmAcc.name, "open", '[{0}]'.format(minerAcc.name), '-p {0}'.format(minerAcc.name))
    time.sleep(1.0)

    Utils.Print("Transfer initial balances")

    # init with 1 Million EOS
    time.sleep(2)
    #killEosInstances = False
    #cluster.keepRunning = True
    #walletMgr.keepRunning = True

    for i,k in enumerate(addys):
        Utils.Print("addys: [{0}] [{1}] [{2}]".format(i,k[2:].lower(), len(k[2:])))
        transferAmount="1000000.0000 {0}".format(CORE_SYMBOL)
        Print("Transfer funds %s from account %s to %s" % (transferAmount, cluster.eosioAccount.name, "0x" + k[2:].lower()))
        #"0x" + k[2:].lower(),
        time.sleep(1)
        # waitForTransBlock must not be True
        prodNode.transferFunds(cluster.eosioAccount, evmAcc, transferAmount, "0x" + k[2:].lower())

    time.sleep(1)
    Utils.Print("Send balance")
    evmChainId = 15555
    fromAdd = "f39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    toAdd = '0x9edf022004846bc987799d552d1b8485b317b7ed'
    amount = 100
    nonce = 0
    evmSendKey = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"

    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=amount,
        data=b'',
        chainId=evmChainId
    ), evmSendKey)

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name))
    prodNode.waitForTransBlockIfNeeded(trans[1], True)

    #
    # Test some failure cases
    #

    # incorrect nonce
    Utils.Print("Send balance again, should fail with wrong nonce")
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    assert not retValue[0], f"push trx should have failed: {retValue}"

    # correct nonce
    nonce += 1
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=amount,
        data=b'',
        chainId=evmChainId
    ), evmSendKey)

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
    Utils.Print("Send balance again, with correct nonce")
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    time.sleep(1.0)
    assert retValue[0], f"push trx should have succeeded: {retValue}"

    # incorrect chainid
    nonce += 1
    evmChainId = 8888
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=amount,
        data=b'',
        chainId=evmChainId
    ), evmSendKey)

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
    Utils.Print("Send balance again, with invalid chainid")
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    time.sleep(1.0)
    assert not retValue[0], f"push trx should have failed: {retValue}"

    # correct values for continuing
    nonce -= 1
    evmChainId = 15555

    Utils.Print("Simple Solidity contract")
# pragma solidity >=0.7.0 <0.9.0;
# contract Storage {
#     uint256 number;
#     function store(uint256 num) public {
#         number = num;
#         emit Transfer(address(0), msg.sender, num);
#     }
#     function retrieve() public view returns (uint256){
#         return number;
#     }
#     event Transfer(address indexed from, address indexed to, uint value);
# }
    nonce += 1
    evmChainId = 15555
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=1000000,       #5M Gas
        gasPrice=gasP,
        data=Web3.to_bytes(hexstr='608060405234801561001057600080fd5b506101b6806100206000396000f3fe608060405234801561001057600080fd5b50600436106100365760003560e01c80632e64cec11461003b5780636057361d14610059575b600080fd5b610043610075565b604051610050919061013f565b60405180910390f35b610073600480360381019061006e9190610103565b61007e565b005b60008054905090565b806000819055503373ffffffffffffffffffffffffffffffffffffffff16600073ffffffffffffffffffffffffffffffffffffffff167fddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef836040516100e3919061013f565b60405180910390a350565b6000813590506100fd81610169565b92915050565b60006020828403121561011957610118610164565b5b6000610127848285016100ee565b91505092915050565b6101398161015a565b82525050565b60006020820190506101546000830184610130565b92915050565b6000819050919050565b600080fd5b6101728161015a565b811461017d57600080fd5b5056fea264697066735822122061ba78daf70a6edb2db7cbb1dbac434da1ba14ec0e009d4df8907b8c6ee4d63264736f6c63430008070033'),
        chainId=evmChainId
    ), evmSendKey)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    time.sleep(1.0)
    assert retValue[0], f"push trx should have succeeded: {retValue}"
    contract_addr = makeContractAddress(fromAdd, nonce)
    nonce = interact_with_storage_contract(contract_addr, nonce)
    nonce_1 = nonce

    if genesisJson[0] != '/': genesisJson = os.path.realpath(genesisJson)
    f=open(genesisJson,"w")
    f.write(json.dumps(genesis_info))
    f.close()

    Utils.Print("#####################################################")
    Utils.Print("Generated EVM json genesis file in: %s" % genesisJson)
    Utils.Print("")
    Utils.Print("You can now run:")
    Utils.Print("  eos-evm-node --plugin=blockchain_plugin --ship-core-account=eosio.evm --ship-endpoint=127.0.0.1:8999 --genesis-json=%s --chain-data=/tmp/data --verbosity=5" % genesisJson)
    Utils.Print("  eos-evm-rpc --eos-evm-node=127.0.0.1:8080 --http-port=0.0.0.0:8881 --chaindata=/tmp/data --api-spec=eth,debug,net,trace")
    Utils.Print("")

    #
    # Test EOS/EVM Bridge
    #
    Utils.Print("Test EOS/EVM Bridge")

    # Verify starting values
    expectedAmount="60000000.0000 {0}".format(CORE_SYMBOL)
    evmAccActualAmount=prodNode.getAccountEosBalanceStr(evmAcc.name)
    testAccActualAmount=prodNode.getAccountEosBalanceStr(testAcc.name)
    Utils.Print("\tAccount balances: EVM %s, Test %s" % (evmAccActualAmount, testAccActualAmount))
    if expectedAmount != evmAccActualAmount or expectedAmount != testAccActualAmount:
        Utils.errorExit("Unexpected starting conditions. Excepted %s, evm actual: %s, test actual %s" % (expectedAmount, evmAccActualAmount, testAccActualAmount))

    # set ingress bridge fee
    Utils.Print("Set ingress bridge fee")
    data='[{{"gas_price": null, "miner_cut": null, "ingress_bridge_fee": "0.0100 {}"}}]'.format(CORE_SYMBOL)
    trans=prodNode.pushMessage(evmAcc.name, "setfeeparams", data, '-p {0}'.format(evmAcc.name))
    time.sleep(1.0)

    rows=prodNode.getTable(evmAcc.name, evmAcc.name, "balances")
    Utils.Print("\tBefore transfer table rows:", rows)

    # EOS -> EVM
    transferAmount="97.5321 {0}".format(CORE_SYMBOL)
    Print("Transfer funds %s from account %s to %s" % (transferAmount, testAcc.name, evmAcc.name))
    prodNode.transferFunds(testAcc, evmAcc, transferAmount, "0xF0cE7BaB13C99bA0565f426508a7CD8f4C247E5a", waitForTransBlock=False)
    time.sleep(1.0)

    row0=prodNode.getTableRow(evmAcc.name, evmAcc.name, "balances", 0)
    Utils.Print("\tAfter transfer table row:", row0)
    assert(row0["balance"]["balance"] == "1.0143 {0}".format(CORE_SYMBOL)) # should have fee at end of transaction
    testAccActualAmount=prodNode.getAccountEosBalanceStr(evmAcc.name)
    Utils.Print("\tEVM  Account balance %s" % testAccActualAmount)
    expectedAmount="60000097.5321 {0}".format(CORE_SYMBOL)
    if expectedAmount != testAccActualAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, testAccActualAmount))
    expectedAmount="59999902.4679 {0}".format(CORE_SYMBOL)
    testAccActualAmount=prodNode.getAccountEosBalanceStr(testAcc.name)
    Utils.Print("\tTest Account balance %s" % testAccActualAmount)
    if testAccActualAmount != expectedAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, testAccActualAmount))
    row3=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 3) # 3rd balance of this integration test
    assert(row3["eth_address"] == "f0ce7bab13c99ba0565f426508a7cd8f4c247e5a")
    assert(row3["balance"] == "000000000000000000000000000000000000000000000005496419417a1f4000") # 0x5496419417a1f4000 => 97522100000000000000 (97.5321 - 0.0100)

    # EOS -> EVM to the same address
    transferAmount="10.0000 {0}".format(CORE_SYMBOL)
    Print("Transfer funds %s from account %s to %s" % (transferAmount, testAcc.name, evmAcc.name))
    prodNode.transferFunds(testAcc, evmAcc, transferAmount, "0xF0cE7BaB13C99bA0565f426508a7CD8f4C247E5a", waitForTransBlock=False)
    time.sleep(1.0)
    row0=prodNode.getTableRow(evmAcc.name, evmAcc.name, "balances", 0)
    Utils.Print("\tAfter transfer table row:", row0)
    assert(row0["balance"]["balance"] == "1.0243 {0}".format(CORE_SYMBOL)) # should have fee from both transfers
    evmAccActualAmount=prodNode.getAccountEosBalanceStr(evmAcc.name)
    Utils.Print("\tEVM  Account balance %s" % evmAccActualAmount)
    expectedAmount="60000107.5321 {0}".format(CORE_SYMBOL)
    if expectedAmount != evmAccActualAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, evmAccActualAmount))
    expectedAmount="59999892.4679 {0}".format(CORE_SYMBOL)
    testAccActualAmount=prodNode.getAccountEosBalanceStr(testAcc.name)
    Utils.Print("\tTest Account balance %s" % testAccActualAmount)
    if testAccActualAmount != expectedAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, testAccActualAmount))
    row3=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 3) # 3rd balance of this integration test
    assert(row3["eth_address"] == "f0ce7bab13c99ba0565f426508a7cd8f4c247e5a")
    assert(row3["balance"] == "000000000000000000000000000000000000000000000005d407b55394464000") # 0x5d407b55394464000 => 107512100000000000000 (97.5321 + 10.000 - 0.0100 - 0.0100)

    # EOS -> EVM to diff address
    transferAmount="42.4242 {0}".format(CORE_SYMBOL)
    Print("Transfer funds %s from account %s to %s" % (transferAmount, testAcc.name, evmAcc.name))
    prodNode.transferFunds(testAcc, evmAcc, transferAmount, "0x9E126C57330FA71556628e0aabd6B6B6783d99fA", waitForTransBlock=False)
    time.sleep(1.0)
    row0=prodNode.getTableRow(evmAcc.name, evmAcc.name, "balances", 0)
    Utils.Print("\tAfter transfer table row:", row0)
    assert(row0["balance"]["balance"] == "1.0343 {0}".format(CORE_SYMBOL)) # should have fee from all three transfers
    evmAccActualAmount=prodNode.getAccountEosBalanceStr(evmAcc.name)
    Utils.Print("\tEVM  Account balance %s" % evmAccActualAmount)
    expectedAmount="60000149.9563 {0}".format(CORE_SYMBOL)
    if expectedAmount != evmAccActualAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, evmAccActualAmount))
    expectedAmount="59999850.0437 {0}".format(CORE_SYMBOL)
    testAccActualAmount=prodNode.getAccountEosBalanceStr(testAcc.name)
    Utils.Print("\tTest Account balance %s" % testAccActualAmount)
    if testAccActualAmount != expectedAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, testAccActualAmount))
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["balance"] == "0000000000000000000000000000000000000000000000024c9d822e105f8000") # 0x24c9d822e105f8000 => 42414200000000000000 (42.4242 - 0.0100)

    # Switch to version 1
    Utils.Print("Switch to evm_version 1")
    actData = {"version":1}
    trans = prodNode.pushMessage(evmAcc.name, "setversion", json.dumps(actData), '-p {0}'.format(evmAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(2)

    # update gas parameter 
    Utils.Print("Update gas parameter: ram price = 5 EOS per MB, gas price = 10Gwei")
    trans = prodNode.pushMessage(evmAcc.name, "updtgasparam", json.dumps({"ram_price_mb":"5.0000 EOS","gas_price":10000000000}), '-p {0}'.format(evmAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(2)

    Utils.Print("Transfer funds to trigger version change")
    # EVM -> EOS
    #   0x9E126C57330FA71556628e0aabd6B6B6783d99fA private key: 0xba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0
    toAdd = makeReservedEvmAddress(convert_name_to_value(aliceAcc.name))
    evmSendKey = "ba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0"
    Print("Transfer EVM->EOS funds 1Gwei from account %s to %s" % (evmAcc.name, aliceAcc.name))
    nonce = 0
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=300000,       #300k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(100000000000000), # 0.0001 EOS = 100,000 Gwei
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)
    time.sleep(1.0)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)

    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["balance"] == "0000000000000000000000000000000000000000000000024c9336a8ead00600")
    # diff = 2,897,785,000,000,000 = 2,897,785 (Gwei) = (100,000 + (165519 + 21000) * 15) (Gwei)
    # {"ram_price_mb":"5.0000 EOS","gas_price":10000000000}
    # {'consensusParameter': AttributeDict({'gasFeeParameters': AttributeDict({'gasCodedeposit': 477, 'gasNewaccount': 165519, 'gasSset': 167942, 'gasTxcreate': 289062, 'gasTxnewaccount': 165519}

    # Launch eos-evm-node
    dataDir = Utils.DataDir + "eos_evm"
    nodeStdOutDir = dataDir + "/eos-evm-node.stdout"
    nodeStdErrDir = dataDir + "/eos-evm-node.stderr"
    shutil.rmtree(dataDir, ignore_errors=True)
    os.makedirs(dataDir)
    outFile = open(nodeStdOutDir, "w")
    errFile = open(nodeStdErrDir, "w")
    cmd = f"{eosEvmBuildRoot}/bin/eos-evm-node --plugin=blockchain_plugin --ship-core-account=eosio.evm --ship-endpoint=127.0.0.1:8999 --genesis-json={genesisJson} --verbosity=5 --nocolor=1 --chain-data={dataDir}"
    Utils.Print(f"Launching: {cmd}")
    cmdArr=shlex.split(cmd)
    evmNodePOpen=Utils.delayedCheckOutput(cmdArr, stdout=outFile, stderr=errFile)

    time.sleep(4.0) # allow time to sync trxs

    # Launch eos-evm-rpc
    rpcStdOutDir = dataDir + "/eos-evm-rpc.stdout"
    rpcStdErrDir = dataDir + "/eos-evm-rpc.stderr"
    outFile = open(rpcStdOutDir, "w")
    errFile = open(rpcStdErrDir, "w")
    cmd = f"{eosEvmBuildRoot}/bin/eos-evm-rpc --eos-evm-node=127.0.0.1:8080 --http-port=0.0.0.0:8881 --chaindata={dataDir} --api-spec=eth,debug,net,trace"
    Utils.Print(f"Launching: {cmd}")
    cmdArr=shlex.split(cmd)
    os.environ["WEB3_RPC_ENDPOINT"] = "http://127.0.0.1:8881/"
    os.environ["NODEOS_RPC_ENDPOINT"] = "http://127.0.0.1:8881/"
    evmRPCPOpen=Utils.delayedCheckOutput(cmdArr, stdout=outFile, stderr=errFile)
    time.sleep(2.0)

    # ==== gas parameter before the fork ===
    # verify version 1
    Utils.Print("Verify evm_version==1 from eos-evm-node")
    # Verify header.nonce == 1 (evmversion=1)
    evm_block = w3.eth.get_block('latest')
    Utils.Print("before fork, the latest evm block is:" + str(evm_block))
    assert(evm_block["nonce"].hex() == "0x0000000000000001")
    assert("consensusParameter" in evm_block)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasCodedeposit"] == 477)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasNewaccount"] == 165519)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasSset"] == 167942)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasTxcreate"] == 289062)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasTxnewaccount"] == 165519)

    # Validate all balances are the same on both sides
    rows=prodNode.getTable(evmAcc.name, evmAcc.name, "account")
    for row in rows['rows']:
        Utils.Print("Checking 0x{0} balance".format(row['eth_address']))
        r = -1
        try:
            r = w3.eth.get_balance(Web3.to_checksum_address('0x'+row['eth_address']))
        except:
            Utils.Print("ERROR - RPC endpoint not available - Exception thrown - Checking 0x{0} balance".format(row['eth_address']))
            raise
        assert r == int(row['balance'],16), f"{row['eth_address']} {r} != {int(row['balance'],16)}"

    Utils.Print("checking if any error in eos-evm-node")
    foundErr = False
    stdErrFile = open(nodeStdErrDir, "r")
    lines = stdErrFile.readlines()
    for line in lines:
        if line.find("ERROR") != -1 or line.find("CRIT") != -1:
            Utils.Print("  Found ERROR in EOS EVM NODE log: ", line)
            foundErr = True

    Utils.Print("checking if any error in eos-evm-rpc")
    stdErrFile = open(rpcStdErrDir, "r")
    lines = stdErrFile.readlines()
    for line in lines:
        if line.find("ERROR") != -1 or line.find("CRIT") != -1:
            Utils.Print("  Found ERROR in EOS EVM RPC log: ", line)
            foundErr = True

    tries = 120
    blockNum = node.getHeadBlockNum()
    Utils.Print("testing forking behavior: catching defproducera, current native block number is {0}".format(blockNum))
    last_ws_native_blocknum = blockNum
    blockProducer=node.getBlockProducerByNum(blockNum)
    while blockProducer != "defproducera" and tries > 0:
        time.sleep(0.5)
        info = node.getInfo()
        blockNum = int(info["head_block_num"])
        blockProducer = info["head_block_producer"]
        tries = tries - 1

    if tries == 0:
        Utils.errorExit("failed to catch a block produced by defproducera")

    Utils.Print("catching the start of defproducerb")
    tries = 30
    while blockProducer != "defproducerb" and tries > 0:
        time.sleep(0.5)
        info = node.getInfo()
        blockNum = int(info["head_block_num"])
        blockProducer = info["head_block_producer"]
        tries = tries - 1

    if tries == 0:
        Utils.errorExit("failed to catch a block produced by defproducerb")

    blockProducer1=node1.getBlockProducerByNum(blockNum)
    Utils.Print("block number %d is producer by %s in node0" % (blockNum, blockProducer))
    Utils.Print("block number %d is producer by %s in node1" % (blockNum, blockProducer1))

    # ===== start to make a fork, killing the "bridge" node ====
    Print("Sending command to kill \"bridge\" node to separate the 2 producer groups.")
    # # block number to start expecting node killed after
    preKillBlockNum=blockNum
    preKillBlockProducer=blockProducer
    # # kill at last block before defproducerl, since the block it is killed on will get propagated
    killAtProducer="defproducerb"
    inRowCountPerProducer=12
    # #nonProdNode.killNodeOnProducer(producer=killAtProducer, whereInSequence=(inRowCountPerProducer-1))
    nonProdNode.kill(killSignal=9)
    nonProdNode.pid=None
    nonProdNode.killed=True # false killed

    # ***   Identify a highest block number to check while we are trying to identify where the divergence will occur   ***

    # will search full cycle after the current block, since we don't know how many blocks were produced since retrieving
    # block number and issuing kill command
    postKillBlockNum=prodNodes[1].getBlockNum()
    blockProducers0=[]
    blockProducers1=[]
    libs0=[]
    libs1=[]
    lastBlockNum=max([blockNum,postKillBlockNum])+2*maxActiveProducers*inRowCountPerProducer
    actualLastBlockNum=None
    prodChanged=False
    nextProdChange=False
    #identify the earliest LIB to start identify the earliest block to check if divergent branches eventually reach concensus
    (headBlockNum, libNumAroundDivergence)=getMinHeadAndLib(prodNodes)
    Print("Tracking block producers from %d till divergence or %d. Head block is %d and lowest LIB is %d" % (preKillBlockNum, lastBlockNum, headBlockNum, libNumAroundDivergence))
    transitionCount=0
    missedTransitionBlock=None
    for blockNum in range(preKillBlockNum,lastBlockNum + 1):
        #avoiding getting LIB until my current block passes the head from the last time I checked
        if blockNum>headBlockNum:
            (headBlockNum, libNumAroundDivergence)=getMinHeadAndLib(prodNodes)

        blockProducer0=prodNodes[0].getBlockProducerByNum(blockNum, timeout=70)
        blockProducer1=prodNodes[1].getBlockProducerByNum(blockNum, timeout=70)
        Print("blockNum = {} blockProducer0 = {} blockProducer1 = {}".format(blockNum, blockProducer0, blockProducer1))
        blockProducers0.append({"blockNum":blockNum, "prod":blockProducer0})
        blockProducers1.append({"blockNum":blockNum, "prod":blockProducer1})

        if blockProducer0!=blockProducer1:
            Print("Divergence identified at block %s, node_00 producer: %s, node_01 producer: %s" % (blockNum, blockProducer0, blockProducer1))
            actualLastBlockNum=blockNum
            break

    if blockProducer0==blockProducer1:
        errorExit("Divergence not found")

    #verify that the non producing node is not alive (and populate the producer nodes with current getInfo data to report if
    #an error occurs)
    time.sleep(2.0) # give sometime for the nonProdNode to shutdown
    if nonProdNode.verifyAlive():
        Utils.errorExit("Expected the non-producing node to have shutdown.")

    Print("Analyzing the producers leading up to the block after killing the non-producing node, expecting divergence at %d" % (blockNum))

    firstDivergence=analyzeBPs(blockProducers0, blockProducers1, expectDivergence=True)
    # Nodes should not have diverged till the last block
    if firstDivergence!=blockNum:
        Utils.errorExit("Expected to diverge at %s, but diverged at %s." % (firstDivergence, blockNum))
    blockProducers0=[]
    blockProducers1=[]

    killBlockNum=blockNum
    lastBlockNum=killBlockNum+(maxActiveProducers - 1)*inRowCountPerProducer+1  # allow 1st testnet group to produce just 1 more block than the 2nd

    # update gas parameter in minor fork (node0), but not node1
    Utils.Print("Update gas parameter in minor fork: ram price = 6 EOS per MB, gas price = 10Gwei")
    trans = prodNode.pushMessage(evmAcc.name, "updtgasparam", json.dumps({"ram_price_mb":"6.0000 EOS","gas_price":10000000000}), '-p {0}'.format(evmAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(2)

    Utils.Print("Transfer funds to trigger gas parameter in minor fork")
    # EVM -> EOS
    #   0x9E126C57330FA71556628e0aabd6B6B6783d99fA private key: 0xba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0
    toAdd = makeReservedEvmAddress(convert_name_to_value(bobAcc.name))
    evmSendKey = "ba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0"
    Print("Transfer EVM->EOS funds 1Gwei from account %s to %s" % (evmAcc.name, bobAcc.name))
    nonce = 1
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=300000,       #300k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(100000000000000), # 0.0001 EOS = 100,000 Gwei
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}

    # push transaction to node0's minor fork (proda, prodb)
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)
    time.sleep(1.0)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) 
    Utils.Print("\taccount row4 in node0: ", row4)

    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["balance"] == "0000000000000000000000000000000000000000000000024c8724aef459cc00")

    # push the same transaction to node1's minor fork (prodc)
    trans = node1.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)
    time.sleep(1.0)
    node1.waitForTransBlockIfNeeded(trans[1], True)
    row4_node1=node1.getTableRow(evmAcc.name, evmAcc.name, "account", 4)
    Utils.Print("\taccount row4 in node1: ", row4_node1)
    assert(row4_node1["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4_node1["balance"] == "0000000000000000000000000000000000000000000000024c88eb23c5408c00")
    assert(row4["balance"] != row4_node1["balance"])

    # verify eos-evm-node get the new gas parameter from the minor fork
    evm_block = w3.eth.get_block('latest')
    Utils.Print("in minor fork, the latest evm block is:" + str(evm_block))
    assert(evm_block["nonce"].hex() == "0x0000000000000001")
    assert("consensusParameter" in evm_block)

    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasCodedeposit"] == 573)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasNewaccount"] == 198831)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasSset"] == 201158)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasTxcreate"] == 347238)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasTxnewaccount"] == 198831)

    # Validate all balances are the same between node0(prodNode) and eos-evm-node
    Utils.Print("Validate all balances are the same between node0(minor-fork) and eos-evm-node")
    rows=prodNode.getTable(evmAcc.name, evmAcc.name, "account")
    for row in rows['rows']:
        Utils.Print("0x{0} balance is {1} in leap".format(row['eth_address'], int(row['balance'],16)))
        r = -1
        try:
            r = w3.eth.get_balance(Web3.to_checksum_address('0x'+row['eth_address']))
        except:
            Utils.Print("ERROR - RPC endpoint not available - Exception thrown - Checking 0x{0} balance".format(row['eth_address']))
            raise
        Utils.Print("0x{0} balance is {1} in eos-evm-rpc".format(row['eth_address'], r))
        assert r == int(row['balance'],16), f"{row['eth_address']} {r} != {int(row['balance'],16)}"

    Print("Tracking the blocks from the divergence till there are 10*12 blocks on one chain and 10*12+1 on the other, from block %d to %d" % (killBlockNum, lastBlockNum))

    for blockNum in range(killBlockNum,lastBlockNum):
        blockProducer0=prodNodes[0].getBlockProducerByNum(blockNum)
        blockProducer1=prodNodes[1].getBlockProducerByNum(blockNum)
        blockProducers0.append({"blockNum":blockNum, "prod":blockProducer0})
        blockProducers1.append({"blockNum":blockNum, "prod":blockProducer1})

    Print("Analyzing the producers from the divergence to the lastBlockNum and verify they stay diverged, expecting divergence at block %d" % (killBlockNum))

    firstDivergence=analyzeBPs(blockProducers0, blockProducers1, expectDivergence=True)
    if firstDivergence!=killBlockNum:
        Utils.errorExit("Expected to diverge at %s, but diverged at %s." % (firstDivergence, killBlockNum))
    blockProducers0=[]
    blockProducers1=[]

    for prodNode in prodNodes:
        info=prodNode.getInfo()
        Print("node info: %s" % (info))

    # Print("killing node1(defproducerc) so that bridge node will frist connect to node0 (defproducera, defproducerb)")
    # node1.kill(killSignal=9)
    # node1.killed=True
    # time.sleep(2)
    # if node1.verifyAlive():
    #     Utils.errorExit("Expected the node 1 to have shutdown.")

    Print("Relaunching the non-producing bridge node to connect the node 0 (defproducera, defproducerb)")
    if not nonProdNode.relaunch(chainArg=" --hard-replay "):
        errorExit("Failure - (non-production) node %d should have restarted" % (nonProdNode.nodeNum))

    # Print("Relaunch node 1 (defproducerc) and let it connect to brigde node that already synced up with node 0")
    # time.sleep(10)
    # if not node1.relaunch(chainArg=" --enable-stale-production "):
    #     errorExit("Failure - (non-production) node 1 should have restarted")

    Print("Waiting to allow forks to resolve")
    time.sleep(3)

    for prodNode in prodNodes:
        info=prodNode.getInfo()
        Print("node info: %s" % (info))

    #ensure that the nodes have enough time to get in concensus, so wait for 3 producers to produce their complete round
    time.sleep(inRowCountPerProducer * 3 / 2)
    remainingChecks=60
    match=False
    checkHead=False
    checkMatchBlock=killBlockNum
    forkResolved=False
    while remainingChecks>0:
        if checkMatchBlock == killBlockNum and checkHead:
            checkMatchBlock = prodNodes[0].getBlockNum()
        blockProducer0=prodNodes[0].getBlockProducerByNum(checkMatchBlock)
        blockProducer1=prodNodes[1].getBlockProducerByNum(checkMatchBlock)
        match=blockProducer0==blockProducer1
        if match:
            if checkHead:
                forkResolved=True
                Print("Great! fork resolved!!!")
                break
            else:
                checkHead=True
                continue
        Print("Fork has not resolved yet, wait a little more. Block %s has producer %s for node_00 and %s for node_01.  Original divergence was at block %s. Wait time remaining: %d" % (checkMatchBlock, blockProducer0, blockProducer1, killBlockNum, remainingChecks))
        time.sleep(1)
        remainingChecks-=1
    
    assert forkResolved, "fork was not resolved in a reasonable time. node_00 lib {} head {}, node_01 lib {} head {}".format(\
        prodNodes[0].getIrreversibleBlockNum(), prodNodes[0].getHeadBlockNum(), \
        prodNodes[1].getIrreversibleBlockNum(), prodNodes[1].getHeadBlockNum())

    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) 
    Utils.Print("\taccount row4 in node0: ", row4)

    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["balance"] == "0000000000000000000000000000000000000000000000024c88eb23c5408c00")
    assert(row4["balance"] == row4_node1["balance"])

    evm_block = w3.eth.get_block('latest')
    Utils.Print("after fork resolved, the latest evm block is:" + str(evm_block))
    assert(evm_block["nonce"].hex() == "0x0000000000000001")
    assert("consensusParameter" in evm_block)

    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasCodedeposit"] == 477)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasNewaccount"] == 165519)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasSset"] == 167942)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasTxcreate"] == 289062)
    assert(evm_block["consensusParameter"]["gasFeeParameters"]["gasTxnewaccount"] == 165519)

    # Validate all balances are the same between node0(prodNode) and eos-evm-node
    Utils.Print("Validate all balances are the same between node0 and eos-evm-node after fork resolved")
    time.sleep(1.0)
    rows=prodNode.getTable(evmAcc.name, evmAcc.name, "account")
    for row in rows['rows']:
        Utils.Print("0x{0} balance is {1} in leap".format(row['eth_address'], int(row['balance'],16)))
        r = -1
        try:
            r = w3.eth.get_balance(Web3.to_checksum_address('0x'+row['eth_address']))
        except:
            Utils.Print("ERROR - RPC endpoint not available - Exception thrown - Checking 0x{0} balance".format(row['eth_address']))
            raise
        Utils.Print("0x{0} balance is {1} in eos-evm-rpc".format(row['eth_address'], r))
        assert r == int(row['balance'],16), f"{row['eth_address']} {r} != {int(row['balance'],16)}"

    # ensure all blocks from the lib before divergence till the current head are now in consensus
    endBlockNum=max(prodNodes[0].getBlockNum(), prodNodes[1].getBlockNum())

    Print("Identifying the producers from the saved LIB to the current highest head, from block %d to %d" % (libNumAroundDivergence, endBlockNum))

    for blockNum in range(libNumAroundDivergence,endBlockNum):
        blockProducer0=prodNodes[0].getBlockProducerByNum(blockNum)
        blockProducer1=prodNodes[1].getBlockProducerByNum(blockNum)
        blockProducers0.append({"blockNum":blockNum, "prod":blockProducer0})
        blockProducers1.append({"blockNum":blockNum, "prod":blockProducer1})

    Print("Analyzing the producers from the saved LIB to the current highest head and verify they match now")

    analyzeBPs(blockProducers0, blockProducers1, expectDivergence=False)

    resolvedKillBlockProducer=None
    for prod in blockProducers0:
        if prod["blockNum"]==killBlockNum:
            resolvedKillBlockProducer = prod["prod"]
    if resolvedKillBlockProducer is None:
        Utils.errorExit("Did not find find block %s (the original divergent block) in blockProducers0, test setup is wrong." % (killBlockNum))
    Print("Fork resolved and determined producer %s for block %s" % (resolvedKillBlockProducer, killBlockNum))

    testSuccessful= not foundErr
finally:
    TestHelper.shutdown(cluster, walletMgr, testSuccessful=testSuccessful, dumpErrorDetails=dumpErrorDetails)
    if killEosInstances:
        if evmNodePOpen is not None:
            evmNodePOpen.kill()
        if evmRPCPOpen is not None:
            evmRPCPOpen.kill()
        if eosEvmMinerPOpen is not None:
            eosEvmMinerPOpen.kill()
        if wsproxy is not None:
            wsproxy.kill()

exitCode = 0 if testSuccessful else 1
exit(exitCode)
