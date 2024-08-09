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
# nodeos_eos_evm_ws_test_basic
#
# Set up a EOS EVM env and run simple tests with websocket support
#
# Need to install:
#   web3      - pip install web3
#             - pip install otree
#
# --eos-evm-build-root should point to the root of EOS EVM build dir
# --eos-evm-contract-root should point to root of EOS EVM contract build dir
#
#  cd build/tests
# ./nodeos_eos_evm_ws_test_basic.py --eos-evm-contract-root ~/workspaces/TrustEVM/build --eos-evm-build-root ~/workspaces/eos-evm-node/build -v
#
#
###############################################################

Print=Utils.Print
errorExit=Utils.errorExit

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

assert eosEvmContractRoot is not None, "--eos-evm-contract-root is required"
assert eosEvmBuildRoot is not None, "--eos-evm-build-root is required"

szabo = 1000000000000
seed=1
Utils.Debug=debug
testSuccessful=False

random.seed(seed) # Use a fixed seed for repeatability.
cluster=Cluster(keepRunning=args.leave_running, keepLogs=args.keep_logs)
walletMgr=WalletMgr(True)

pnodes=1
total_nodes=pnodes + 2
evmNodePOpen = None
evmRPCPOpen = None
eosEvmMinerPOpen = None
wsproxy = None

def get_raw_transaction(signed_trx):
    if hasattr(signed_trx, 'raw_transaction'):
        return signed_trx.raw_transaction
    else:
        return signed_trx.rawTransaction

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

        actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
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
    shipNodeNum = total_nodes - 1
    specificExtraNodeosArgs[shipNodeNum]="--plugin eosio::state_history_plugin --state-history-endpoint 127.0.0.1:8999 --trace-history --chain-state-history --disable-replay-opts  "

    extraNodeosArgs="--contracts-console"

    Print("Stand up cluster")
    if cluster.launch(pnodes=pnodes, totalNodes=total_nodes, extraNodeosArgs=extraNodeosArgs, specificExtraNodeosArgs=specificExtraNodeosArgs, delay=5) is False:
        errorExit("Failed to stand up eos cluster.")

    Print ("Wait for Cluster stabilization")
    # wait for cluster to start producing blocks
    if not cluster.waitOnClusterBlockNumSync(3):
        errorExit("Cluster never stabilized")
    Print ("Cluster stabilized")

    prodNode = cluster.getNode(0)
    nonProdNode = cluster.getNode(1)

    accounts=createAccountKeys(3)
    if accounts is None:
        Utils.errorExit("FAILURE - create keys")

    evmAcc = accounts[0]
    evmAcc.name = "eosio.evm"
    testAcc = accounts[1]
    minerAcc = accounts[2]

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

    trans = prodNode.pushMessage(evmAcc.name, "init", '{{"chainid":15555, "fee_params": {{"gas_price": "10000000000", "miner_cut": 10000, "ingress_bridge_fee": "0.0000 {0}"}}}}'.format(CORE_SYMBOL), '-p eosio.evm')

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

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
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

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    Utils.Print("Send balance again, with correct nonce")
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
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

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    Utils.Print("Send balance again, with invalid chainid")
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
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

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
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

    rows=prodNode.getTable(evmAcc.name, evmAcc.name, "balances")
    Utils.Print("\tBefore transfer table rows:", rows)

    # EOS -> EVM
    transferAmount="97.5321 {0}".format(CORE_SYMBOL)
    Print("Transfer funds %s from account %s to %s" % (transferAmount, testAcc.name, evmAcc.name))
    prodNode.transferFunds(testAcc, evmAcc, transferAmount, "0xF0cE7BaB13C99bA0565f426508a7CD8f4C247E5a", waitForTransBlock=False)

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

    # EVM -> EOS
    #   0x9E126C57330FA71556628e0aabd6B6B6783d99fA private key: 0xba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0
    toAdd = makeReservedEvmAddress(convert_name_to_value(testAcc.name))
    evmSendKey = "ba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0"
    amount=13.1313
    transferAmount="13.1313 {0}".format(CORE_SYMBOL)
    Print("Transfer EVM->EOS funds %s from account %s to %s" % (transferAmount, evmAcc.name, testAcc.name))
    nonce = 0
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(amount*10000*szabo*100), # .0001 EOS is 100 szabos
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)
    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")

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

    time.sleep(10) # allow time to sync trxs

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

    Utils.Print("starting websocket proxy")
    cmd = f"node {eosEvmBuildRoot}/peripherals/eos-evm-ws-proxy/main.js"
    cmdArr=shlex.split(cmd)
    wsStdOutDir = dataDir + "/ws.stdout"
    wsStdErrDir = dataDir + "/ws.stderr"
    wsoutFile = open(wsStdOutDir, "w")
    wserrFile = open(wsStdErrDir, "w")
    wsproxy=Utils.delayedCheckOutput(cmdArr, stdout=wsoutFile, stderr=wserrFile)
    time.sleep(3.0)

    stdErrFile = open(wsStdErrDir, "r")
    lines = stdErrFile.readlines()
    for line in lines:
        Utils.Print("wsStdErrlog:", line)

    stdOutFile = open(wsStdOutDir, "r")
    lines = stdOutFile.readlines()
    for line in lines:
        Utils.Print("wsStdOutlog:", line)

    time.sleep(5.0)

    ws = websocket.WebSocket()
    Utils.Print("start to connect ws://localhost:3333")
    ws.connect("ws://localhost:3333")
    ws.send("{\"method\":\"eth_blockNumber\",\"params\":[\"0x1\",false],\"id\":123}")
    Utils.Print("send eth_blockNumber to websocket proxy")

    recevied_msg=ws.recv()
    res=json.loads(recevied_msg)
    Utils.Print("eth_blockNumber response from websocket:" + recevied_msg)
    assert(res["id"] == 123)
    assert(res["jsonrpc"] == "2.0")

    # try eth subscrible to new heads
    Utils.Print("send eth_subscribe for newHeads")
    ws.send("{\"jsonrpc\":  \"2.0\", \"id\": 124, \"method\":  \"eth_subscribe\", \"params\":[\"newHeads\"]}")
    recevied_msg=ws.recv()
    res=json.loads(recevied_msg)
    Utils.Print("eth_subscribe response from websocket:" + recevied_msg)
    assert(res["id"] == 124)
    sub_id=res["result"]
    assert(len(sub_id) > 0)

    # try to receive some blocks
    block_count = 0
    prev_hash=""
    while block_count < 10:
        time.sleep(0.5)
        recevied_msg=ws.recv()
        res=json.loads(recevied_msg)
        if block_count == 0:
            Utils.Print("recevied block message from websocket:" + recevied_msg)
        block_json=res["params"]["result"]
        num=block_json["number"] # number can be decimal or hex (with 0x prefix)
        hash=block_json["hash"]
        parent_hash=block_json["parentHash"]
        Utils.Print("received block {0} from websocket, hash={1}..., parent={2}...".format(num, hash[0:8], parent_hash[0:8]))
        if block_count > 0:
            assert(len(parent_hash) > 0 and parent_hash == prev_hash)
        prev_hash=hash
        block_count = block_count + 1

    # try to unsubscribe of newheads
    Utils.Print("send eth_unsubscribe")
    ws.send("{\"jsonrpc\":  \"2.0\", \"id\": 125, \"method\":  \"eth_unsubscribe\", \"params\":[\""+sub_id+"\"]}")
    try_count = 10
    while (try_count > 0):
        recevied_msg=ws.recv()
        res=json.loads(recevied_msg)
        if ("id" in res and res["id"] == 125 and res["result"] == True):
            break;
        try_count = try_count - 1
    if (try_count == 0):
        Utils.errorExit("failed to unsubscribe, last websocket recevied msg:" + recevied_msg);

    # test eth subscribe for minedTransactions
    Utils.Print("send eth_subscribe for minedTransactions")
    ws.send("{\"jsonrpc\":  \"2.0\", \"id\": 126, \"method\":  \"eth_subscribe\", \"params\":[\"minedTransactions\"]}")
    recevied_msg=ws.recv()
    res=json.loads(recevied_msg)
    Utils.Print("eth_subscribe response from websocket:" + recevied_msg)
    assert(res["id"] == 126)
    sub_id=res["result"]
    assert(len(sub_id) > 0)

    time.sleep(2.0)
    # now transafer from EOS->EVM
    transferAmount="0.1030 {0}".format(CORE_SYMBOL)
    Print("Transfer funds %s from account %s to %s" % (transferAmount, testAcc.name, evmAcc.name))
    prodNode.transferFunds(testAcc, evmAcc, transferAmount, "0x9E126C57330FA71556628e0aabd6B6B6783d99fA", waitForTransBlock=False)

    # try to receive the EOS->EVM transaction
    try_count = 3
    while (try_count > 0):
        recevied_msg=ws.recv()
        res=json.loads(recevied_msg)
        Utils.Print("last ws msg is:" + recevied_msg)
        if ("method" in res and res["method"] == "eth_subscription"):
            assert(res["params"]["result"]["transaction"]["value"] == "93000000000000000" or \
                res["params"]["result"]["transaction"]["value"] == "0x14a6701dc1c8000") # 0.103 - 0.01(fee)=0.093
            break
        try_count = try_count - 1
    if (try_count == 0):
        Utils.errorExit("failed to get transaction of minedTransactions subscription, last websocket recevied msg:" + recevied_msg);

    # try to unsubscribe for minedTransactions
    Utils.Print("send eth_unsubscribe")
    ws.send("{\"jsonrpc\":  \"2.0\", \"id\": 126, \"method\":  \"eth_unsubscribe\", \"params\":[\""+sub_id+"\"]}")
    try_count = 10
    while (try_count > 0):
        recevied_msg=ws.recv()
        res=json.loads(recevied_msg)
        if ("id" in res and res["id"] == 126 and res["result"] == True):
            break;
        try_count = try_count - 1
    if (try_count == 0):
        Utils.errorExit("failed to unsubscribe, last websocket recevied msg:" + recevied_msg);
        
    time.sleep(1.0)

    # test eth subscribe for logs
    Utils.Print("send eth_subscribe for logs")
    ws.send("{\"jsonrpc\":  \"2.0\", \"id\": 127, \"method\":  \"eth_subscribe\", \"params\":[\"logs\"]}")
    recevied_msg=ws.recv()
    res=json.loads(recevied_msg)
    Utils.Print("eth_subscribe response from websocket:" + recevied_msg)
    assert(res["id"] == 127)
    sub_id=res["result"]
    assert(len(sub_id) > 0)

    time.sleep(1.0)
    # now interact with contract that emits logs
    evmSendKey = "ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    nonce = nonce_1
    nonce = interact_with_storage_contract(contract_addr, nonce)

    # receive logs from web-socket server
    recevied_msg=ws.recv()
    res=json.loads(recevied_msg)
    Utils.Print("last ws msg is:" + recevied_msg)
    assert(res["params"]["result"]["data"] == "0x0000000000000000000000000000000000000000000000000000000000000007")
    assert(res["params"]["result"]["topics"] == ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef","0x0000000000000000000000000000000000000000000000000000000000000000","0x000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"])
    recevied_msg=ws.recv()
    res=json.loads(recevied_msg)
    Utils.Print("last ws msg is:" + recevied_msg)
    assert(res["params"]["result"]["data"] == "0x0000000000000000000000000000000000000000000000000000000000000008")
    assert(res["params"]["result"]["topics"] == ["0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef","0x0000000000000000000000000000000000000000000000000000000000000000","0x000000000000000000000000f39fd6e51aad88f6f4ce6ab8827279cfffb92266"])

    ws.close()

    testSuccessful= not foundErr
except Exception as ex:
    Utils.Print("Exception:" + str(ex))
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
