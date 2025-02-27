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
# nodeos_eos_evm_test
#
# Set up a EOS EVM env and run simple tests.
#
# Need to install:
#   web3      - pip install web3
#
# --use-miner path to eos-evm-miner. if specified then uses eos-evm-miner to get gas price.
# --eos-evm-build-root should point to the root of EOS EVM build dir
# --eos-evm-contract-root should point to root of EOS EVM contract build dir
#
# Example (Running with leap src build):
#  cd ~/leap/build
#  ~/eos-evm-node/build/tests/nodeos_eos_evm_test.py --eos-evm-contract-root ~/eos-evm/build --eos-evm-build-root ~/eos-evm-node/build --use-miner ~/eos-evm-miner --leave-running
#
# Example (Running with leap dev-install):
#  ln -s /usr/share/leap_testing/tests/TestHarness /usr/lib/python3/dist-packages/TestHarness
#  ~/eos-evm-node/build/tests/nodeos_eos_evm_test.py --eos-evm-contract-root ~/eos-evm/build --eos-evm-build-root ~/eos-evm-node/build --use-miner ~/eos-evm-miner --leave-running
#
#  Launches wallet at port: 9899
#    Example: bin/cleos --wallet-url http://127.0.0.1:9899 ...
#
###############################################################

Print=Utils.Print
errorExit=Utils.errorExit

def get_raw_transaction(signed_trx):
    if hasattr(signed_trx, 'raw_transaction'):
        return signed_trx.raw_transaction
    else:
        return signed_trx.rawTransaction

def prefix_0x(hexstr):
    if not hexstr[:2] == '0x':
        return "0x" + hexstr
    else:
        return hexstr

appArgs=AppArgs()
appArgs.add(flag="--eos-evm-contract-root", type=str, help="EOS EVM contract build dir", default=None)
appArgs.add(flag="--eos-evm-build-root", type=str, help="EOS EVM build dir", default=None)
appArgs.add(flag="--genesis-json", type=str, help="File to save generated genesis json", default="eos-evm-genesis.json")
appArgs.add(flag="--use-miner", type=str, help="EOS EVM miner to use to send trx to nodeos", default=None)

args=TestHelper.parse_args({"--keep-logs","--dump-error-details","-v","--leave-running"}, applicationSpecificArgs=appArgs)
debug=args.v
killEosInstances= not args.leave_running
dumpErrorDetails=args.dump_error_details
eosEvmContractRoot=args.eos_evm_contract_root
eosEvmBuildRoot=args.eos_evm_build_root
genesisJson=args.genesis_json
useMiner=args.use_miner

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

def assert_contract_exist(contract_addr):
    Utils.Print("ensure contract {0} exist".format(contract_addr))
    if contract_addr[:2] == '0x':
        contract_addr = contract_addr[2:]
    rows=prodNode.getTable(evmAcc.name, evmAcc.name, "account")
    for row in rows['rows']:
        if (str(contract_addr) == str(row['eth_address'])):
            assert row['code_id'] is not None, "contract {0} should exist".format(contract_addr)
            return True
    Utils.Print("evm account table rows: " + json.dumps(rows))
    assert False, "contract {0} should exist".format(contract_addr)

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

def setEosEvmMinerEnv():
    os.environ["PRIVATE_KEY"]=f"{minerAcc.activePrivateKey}"
    os.environ["MINER_ACCOUNT"]=f"{minerAcc.name}"
    os.environ["RPC_ENDPOINTS"]="http://127.0.0.1:8888"
    os.environ["PORT"]="18888"
    os.environ["LOCK_GAS_PRICE"]="true"
    os.environ["MINER_PERMISSION"]="active"
    os.environ["EXPIRE_SEC"]="60"

    Utils.Print(f"Set up configuration of eos-evm-miner via environment variables.")
    Utils.Print(f"PRIVATE_KEY: {os.environ.get('PRIVATE_KEY')}")
    Utils.Print(f"MINER_ACCOUNT: {os.environ.get('MINER_ACCOUNT')}")
    Utils.Print(f"RPC_ENDPOINTS: {os.environ.get('RPC_ENDPOINTS')}")
    Utils.Print(f"PORT: {os.environ.get('PORT')}")
    Utils.Print(f"LOCK_GAS_PRICE: {os.environ.get('LOCK_GAS_PRICE')}")
    Utils.Print(f"MINER_PERMISSION: {os.environ.get('MINER_PERMISSION')}")
    Utils.Print(f"EXPIRE_SEC: {os.environ.get('EXPIRE_SEC')}")

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
    if useMiner is None:
        return 10000000000
    else:
        result = processUrllibRequest("http://127.0.0.1:18888", payload={"method":"eth_gasPrice","params":[],"id":1,"jsonrpc":"2.0"})
        Utils.Print("result: ", result)
        return result["payload"]["result"]

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

def toDict(dictToParse):
    # convert any 'AttributeDict' type found to 'dict'
    parsedDict = dict(dictToParse)
    for key, val in parsedDict.items():
        # check for nested dict structures to iterate through
        if  'dict' in str(type(val)).lower():
            parsedDict[key] = toDict(val)
        # convert 'HexBytes' type to 'str'
        elif 'HexBytes' in str(type(val)):
            parsedDict[key] = val.hex()
    return parsedDict

try:
    TestHelper.printSystemInfo("BEGIN")

    w3 = Web3(Web3.HTTPProvider("http://localhost:8881"))

    cluster.setWalletMgr(walletMgr)

    specificExtraNodeosArgs={}
    shipNodeNum = total_nodes - 1
    specificExtraNodeosArgs[shipNodeNum]="--plugin eosio::state_history_plugin --state-history-endpoint 127.0.0.1:8999 --trace-history --chain-state-history --disable-replay-opts "

    extraNodeosArgs="--contracts-console --resource-monitor-not-shutdown-on-threshold-exceeded"

    Print("Stand up cluster")
    if cluster.launch(pnodes=pnodes, totalNodes=total_nodes, extraNodeosArgs=extraNodeosArgs, specificExtraNodeosArgs=specificExtraNodeosArgs,loadSystemContract=False,activateIF=True,delay=5) is False:
        errorExit("Failed to stand up eos cluster.")

    Print ("Wait for Cluster stabilization")
    # wait for cluster to start producing blocks
    if not cluster.waitOnClusterBlockNumSync(3):
        errorExit("Cluster never stabilized")
    Print ("Cluster stabilized")

    Utils.Print("make sure instant finality is switched")
    info = cluster.biosNode.getInfo(exitOnError=True)
    assert (info["head_block_num"] - info["last_irreversible_block_num"]) < 9, "Instant finality enabled LIB diff should be small"

    prodNode = cluster.getNode(0)
    nonProdNode = cluster.getNode(1)

    accounts=createAccountKeys(7)
    if accounts is None:
        Utils.errorExit("FAILURE - create keys")

    evmAcc = accounts[0]
    evmAcc.name = "eosio.evm"
    testAcc = accounts[1]
    minerAcc = accounts[2]
    defertestAcc = accounts[3]
    defertest2Acc = accounts[4]
    aliceAcc = accounts[5]

    accounts[6].name = "evmbridge"
    evmbridgeAcc = accounts[6]

    testWalletName="test"

    Print("Creating wallet \"%s\"." % (testWalletName))
    testWallet=walletMgr.create(testWalletName, [cluster.eosioAccount,accounts[0],accounts[1],accounts[2],accounts[3],accounts[4],accounts[5],accounts[6]])

    addys = {
        "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266":"0x038318535b54105d4a7aae60c08fc45f9687181b4fdfc625bd1a753fa7397fed75,0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
    }

    numAddys = len(addys)

    # create accounts via eosio as otherwise a bid is needed
    for account in accounts:
        Print("Create new account %s via %s" % (account.name, cluster.eosioAccount.name))
        
        trans=nonProdNode.createAccount(account, cluster.eosioAccount,0,waitForTransBlock=True)

        #   max supply 1000000000.0000 (1 Billion)
        transferAmount="60000000.0000 {0}".format(CORE_SYMBOL)
        if account.name == evmAcc.name:
            transferAmount="58999999.0000 {0}".format(CORE_SYMBOL)

        Print("Transfer funds %s from account %s to %s" % (transferAmount, cluster.eosioAccount.name, account.name))
        nonProdNode.transferFunds(cluster.eosioAccount, account, transferAmount, "test transfer", waitForTransBlock=True)

    contractDir=eosEvmContractRoot + "/evm_runtime"
    wasmFile="evm_runtime.wasm"
    abiFile="evm_runtime.abi"
    Utils.Print(f"Publish evm_runtime contract {contractDir}/{wasmFile} to account {evmAcc}")
    prodNode.publishContract(evmAcc, contractDir, wasmFile, abiFile, waitForTransBlock=True)

    # add eosio.code permission
    cmd="set account permission eosio.evm active --add-code -p eosio.evm@active"
    prodNode.processCleosCmd(cmd, cmd, silentErrors=True, returnType=ReturnType.raw)

    # set defertest contract
    contractDir=eosEvmBuildRoot + "/tests"
    wasmFile="defertest.wasm"
    abiFile="defertest.abi"
    Utils.Print(f"Publish defertest contract {contractDir}/{wasmFile} to account {defertestAcc}")
    prodNode.publishContract(defertestAcc, contractDir, wasmFile, abiFile, waitForTransBlock=True)

    # add eosio.code permission to defertest account
    cmd="set account permission " + defertestAcc.name + " active --add-code -p " + defertestAcc.name + "@active"
    prodNode.processCleosCmd(cmd, cmd, silentErrors=False, returnType=ReturnType.raw)

    # set defertest2 contract
    contractDir=eosEvmBuildRoot + "/tests"
    wasmFile="defertest2.wasm"
    abiFile="defertest2.abi"
    Utils.Print(f"Publish defertest2 contract {contractDir}/{wasmFile} to account {defertest2Acc}")
    prodNode.publishContract(defertest2Acc, contractDir, wasmFile, abiFile, waitForTransBlock=True)

    contractDir=eosEvmBuildRoot + "/tests"
    wasmFile="evmbridge.wasm"
    abiFile="evmbridge.abi"
    Utils.Print(f"Publish evmbridge contract {contractDir}/{wasmFile} to account {evmbridgeAcc}")
    prodNode.publishContract(evmbridgeAcc, contractDir, wasmFile, abiFile, waitForTransBlock=True)
    # add eosio.code evmbridge
    cmd="set account permission evmbridge active --add-code -p evmbridge@active"
    prodNode.processCleosCmd(cmd, cmd, silentErrors=True, returnType=ReturnType.raw)

    # add eosio.code permission to defertest2 account
    cmd="set account permission " + defertest2Acc.name + " active --add-code -p " + defertest2Acc.name + "@active"
    prodNode.processCleosCmd(cmd, cmd, silentErrors=False, returnType=ReturnType.raw)

    trans = prodNode.pushMessage(evmAcc.name, "init", '{{"chainid":15555, "fee_params": {{"gas_price": "10000000000", "miner_cut": 10000, "ingress_bridge_fee": "0.0000 {0}"}}}}'.format(CORE_SYMBOL), '-p eosio.evm')
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

    #
    # Setup eos-evm-miner
    #
    if useMiner is not None:
        setEosEvmMinerEnv()
        dataDir = Utils.DataDir + "eos-evm-miner"
        outDir = dataDir + "/eos-evm-miner.stdout"
        errDir = dataDir + "/eos-evm-miner.stderr"
        shutil.rmtree(dataDir, ignore_errors=True)
        os.makedirs(dataDir)
        outFile = open(outDir, "w")
        errFile = open(errDir, "w")
        cmd = "node dist/index.js"
        Utils.Print("Launching: %s" % cmd)
        cmdArr=shlex.split(cmd)
        eosEvmMinerPOpen=subprocess.Popen(cmdArr, stdout=outFile, stderr=errFile, cwd=useMiner)
        time.sleep(10) # let miner start up

    Utils.Print("Transfer initial balances")

    # init with 1 Million EOS
    for i,k in enumerate(addys):
        Utils.Print("addys: [{0}] [{1}] [{2}]".format(i,k[2:].lower(), len(k[2:])))
        transferAmount="1000000.0000 {0}".format(CORE_SYMBOL)
        Print("Transfer funds %s from account %s to %s" % (transferAmount, cluster.eosioAccount.name, evmAcc.name))
        nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, transferAmount, "0x" + k[2:].lower(), waitForTransBlock=True)
        if not (i+1) % 20: time.sleep(1)

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
    # // SPDX-License-Identifier: GPL-3.0
    # pragma solidity >=0.7.0 <0.9.0;
    # contract Storage {
    #     uint256 number;
    #     function store(uint256 num) public {
    #         number = num;
    #     }
    #     function retrieve() public view returns (uint256){
    #         return number;
    #     }
    # }
    nonce += 1
    evmChainId = 15555
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=1000000,       #5M Gas
        gasPrice=gasP,
        data=Web3.to_bytes(hexstr='608060405234801561001057600080fd5b50610150806100206000396000f3fe608060405234801561001057600080fd5b50600436106100365760003560e01c80632e64cec11461003b5780636057361d14610059575b600080fd5b610043610075565b60405161005091906100a1565b60405180910390f35b610073600480360381019061006e91906100ed565b61007e565b005b60008054905090565b8060008190555050565b6000819050919050565b61009b81610088565b82525050565b60006020820190506100b66000830184610092565b92915050565b600080fd5b6100ca81610088565b81146100d557600080fd5b50565b6000813590506100e7816100c1565b92915050565b600060208284031215610103576101026100bc565b5b6000610111848285016100d8565b9150509291505056fea2646970667358fe12209ffe32fe5779018f7ee58886c856a4cfdf550f2df32cec944f57716a3abf4a5964736f6c63430008110033'),
        chainId=evmChainId
    ), evmSendKey)

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    assert retValue[0], f"push trx should have succeeded: {retValue}"
    assert_contract_exist(makeContractAddress(fromAdd, nonce))
    nonce = interact_with_storage_contract(makeContractAddress(fromAdd, nonce), nonce)

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
    nonProdNode.transferFunds(testAcc, evmAcc, transferAmount, "0xF0cE7BaB13C99bA0565f426508a7CD8f4C247E5a", waitForTransBlock=True)

    row0=prodNode.getTableRow(evmAcc.name, evmAcc.name, "balances", 0)
    Utils.Print("\tAfter transfer table row:", row0)
    assert(row0["balance"]["balance"] == "1.0126 {0}".format(CORE_SYMBOL)) # should have fee at end of transaction
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

    Utils.Print("\tcurrent evm account balance:", row3)
    assert(row3["eth_address"] == "f0ce7bab13c99ba0565f426508a7cd8f4c247e5a")
    assert(row3["balance"] == "000000000000000000000000000000000000000000000005496419417a1f4000") # 0x5496419417a1f4000 => 97522100000000000000 (97.5321 - 0.0100)

    # EOS -> EVM to the same address
    transferAmount="10.0000 {0}".format(CORE_SYMBOL)
    Print("Transfer funds %s from account %s to %s" % (transferAmount, testAcc.name, evmAcc.name))
    nonProdNode.transferFunds(testAcc, evmAcc, transferAmount, "0xF0cE7BaB13C99bA0565f426508a7CD8f4C247E5a", waitForTransBlock=True)
    row0=prodNode.getTableRow(evmAcc.name, evmAcc.name, "balances", 0)
    Utils.Print("\tcurrent evm account balance:", row0)
    assert(row0["balance"]["balance"] == "1.0226 {0}".format(CORE_SYMBOL)) # should have fee from both transfers
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

    Utils.Print("\tcurrent evm account balance:", row3)
    assert(row3["eth_address"] == "f0ce7bab13c99ba0565f426508a7cd8f4c247e5a")
    assert(row3["balance"] == "000000000000000000000000000000000000000000000005d407b55394464000") # 0x5d407b55394464000 => 107512100000000000000 (97.5321 + 10.000 - 0.0100 - 0.0100)

    # EOS -> EVM to diff address
    transferAmount="42.4242 {0}".format(CORE_SYMBOL)
    Print("Transfer funds %s from account %s to %s" % (transferAmount, testAcc.name, evmAcc.name))
    nonProdNode.transferFunds(testAcc, evmAcc, transferAmount, "0x9E126C57330FA71556628e0aabd6B6B6783d99fA", waitForTransBlock=True)
    row0=prodNode.getTableRow(evmAcc.name, evmAcc.name, "balances", 0)
    Utils.Print("\tAfter transfer table row:", row0)
    assert(row0["balance"]["balance"] == "1.0326 {0}".format(CORE_SYMBOL)) # should have fee from all three transfers
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
    assert(row4["balance"] == "000000000000000000000000000000000000000000000001966103689de22000") # 0x1966103689de22000 => 29282690000000000000 (42.4242 - 0.0100 - 13.1313 - 21000 * 10^10)
    expectedAmount="60000136.8250 {0}".format(CORE_SYMBOL)
    evmAccActualAmount=prodNode.getAccountEosBalanceStr(evmAcc.name)
    Utils.Print("\tEVM  Account balance %s" % evmAccActualAmount)
    if evmAccActualAmount != expectedAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, testAccActualAmount))
    expectedAmount="59999863.1750 {0}".format(CORE_SYMBOL)
    testAccActualAmount=prodNode.getAccountEosBalanceStr(testAcc.name)
    Utils.Print("\tTest Account balance %s" % testAccActualAmount)
    if testAccActualAmount != expectedAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, testAccActualAmount))

    # EVM->EOS from same address
    amount=1.0000
    transferAmount="1.000 {0}".format(CORE_SYMBOL)
    Print("Transfer EVM->EOS funds %s from account %s to %s" % (transferAmount, evmAcc.name, testAcc.name))
    nonce = nonce + 1
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(amount*10000*szabo*100),
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)
    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["balance"] == "000000000000000000000000000000000000000000000001887f8db687170000") # 0x1887f8db687170000 => 28282480000000000000 (42.4242 - 0.0100 - 13.1313 - 1.0000 - 2 * 21000 * 10^10)
    assert(row4["nonce"] == 2)
    expectedAmount="60000135.8250 {0}".format(CORE_SYMBOL)
    evmAccActualAmount=prodNode.getAccountEosBalanceStr(evmAcc.name)
    Utils.Print("\tEVM  Account balance %s" % evmAccActualAmount)
    if evmAccActualAmount != expectedAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, testAccActualAmount))
    expectedAmount="59999864.1750 {0}".format(CORE_SYMBOL)
    testAccActualAmount=prodNode.getAccountEosBalanceStr(testAcc.name)
    Utils.Print("\tTest Account balance %s" % testAccActualAmount)
    if testAccActualAmount != expectedAmount:
        Utils.errorExit("Transfer verification failed. Excepted %s, actual: %s" % (expectedAmount, testAccActualAmount))

    ### Special signature test (begin)
    # Increment contract
    '''
    // SPDX-License-Identifier: GPL-3.0
    pragma solidity >=0.8.2 <0.9.0;
    contract Increment {
        mapping (address => uint256) values;
        function increment() public {
            values[msg.sender]++;
        }
        function retrieve(address a) public view returns (uint256){
            return values[a];
        }
    }
    '''

    accSpecialKey = '344260572d5df010d70597386bfeeaecf863a8dbbe3c9a023f81d7056b28815f'
    accSpecialAdd = w3.eth.account.from_key(accSpecialKey).address

    nonProdNode.transferFunds(testAcc, evmAcc, "10.0000 EOS", "0x0290ffefa58ee84a3641770ab910c48d3441752d", waitForTransBlock=True)
    nonProdNode.transferFunds(testAcc, evmAcc, "1000.0000 EOS", accSpecialAdd, waitForTransBlock=True)

    # Test special signature handling (contract and evm-node)
    Print("Test special signature handling (both contract and evm-node)")
    special_nonce = 0
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=special_nonce,
        gas=1000000,
        gasPrice=getGasPrice(),
        data=Web3.to_bytes(hexstr='608060405234801561001057600080fd5b50610284806100206000396000f3fe608060405234801561001057600080fd5b50600436106100365760003560e01c80630a79309b1461003b578063d09de08a1461006b575b600080fd5b61005560048036038101906100509190610176565b610075565b60405161006291906101bc565b60405180910390f35b6100736100bd565b005b60008060008373ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff168152602001908152602001600020549050919050565b6000803373ffffffffffffffffffffffffffffffffffffffff1673ffffffffffffffffffffffffffffffffffffffff168152602001908152602001600020600081548092919061010c90610206565b9190505550565b600080fd5b600073ffffffffffffffffffffffffffffffffffffffff82169050919050565b600061014382610118565b9050919050565b61015381610138565b811461015e57600080fd5b50565b6000813590506101708161014a565b92915050565b60006020828403121561018c5761018b610113565b5b600061019a84828501610161565b91505092915050565b6000819050919050565b6101b6816101a3565b82525050565b60006020820190506101d160008301846101ad565b92915050565b7f4e487b7100000000000000000000000000000000000000000000000000000000600052601160045260246000fd5b6000610211826101a3565b91507fffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff8203610243576102426101d7565b5b60018201905091905056fea264697066735822122026d27f46966ee75c7a8b2a43923c8796438013de730eb9eec6c24ff581913d6864736f6c63430008120033'),
        chainId=15555
    ), accSpecialKey)

    # Deploy "Increment" contract
    increment_contract = makeContractAddress(accSpecialAdd, special_nonce)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);

    # Test special signature: Call from `accSpecialAdd`
    special_nonce += 1
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=special_nonce,
        gas=1000000,
        gasPrice=getGasPrice(),
        to=Web3.to_checksum_address(increment_contract),
        data=Web3.to_bytes(hexstr='D09DE08A'),  # sha3(increment())=0xD09DE08A
        chainId=15555
    ), accSpecialKey)

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);

    # Test special signature: Call from miner account
    nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, "10.0000 EOS", minerAcc.name, waitForTransBlock=True)
    actData = {"from":minerAcc.name, "to":increment_contract[2:], "value":"0000000000000000000000000000000000000000000000000000000000000000", "data":"d09de08a", "gas_limit":"100000"}
    trans = prodNode.pushMessage(evmAcc.name, "call", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)

    Utils.Print("eosio.evm::call trans result", trans)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);

    # Test special signature: Call from `0x0290ffefa58ee84a3641770ab910c48d3441752d`
    nonProdNode.transferFunds(testAcc, evmAcc, "10.0000 EOS", "0x0290ffefa58ee84a3641770ab910c48d3441752d", waitForTransBlock=True)
    actData = {"from":"0290ffefa58ee84a3641770ab910c48d3441752d", "to":increment_contract[2:], "value":"0000000000000000000000000000000000000000000000000000000000000000", "data":"d09de08a", "gas_limit":"100000"}
    trans = prodNode.pushMessage(evmAcc.name, "admincall", json.dumps(actData), '-p {0}'.format(evmAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);

    ### Special signature test (end)

    # test action trace execution order which is different than creation order
    #   defertest2::notifytest(defertest, evmevmevmevm, miner, rlptx, rlptx2) 
    #      -> 1. create inline action (a) defertest::pushtxinline(evmevmevmevm, miner, rlptx1)
    #      -> 2. require_recipient(defertest)
    #      -> 3. on_notify of defertest::notifytest, create inline action (b) evmevmevmevm::pushtx(miner, rlptx2)
    #      -> 4. inline action (a) executes: create inline action (c) evmevmevmevm::pushtx(rlptx1) 
    #      -> 5. action (c) executes: evmevmevmevm::pushtx(rlptx1)
    #      -> 6. action (b) executes: evmevmevmevm::pushtx(rlptx2)
    # in the above case, evmevmevmevm::pushtx(miner, rlptx2) will be created before evmevmevmevm::pushtx(rlptx1),
    # but evmevmevmevm::pushtx(rlptx1) will be executed before evmevmevmevm::pushtx(miner, rlptx2)
    amount=2.0000
    transferAmount="2.000 {0}".format(CORE_SYMBOL)
    Utils.Print("Test action ordering: action 1: transfer EVM->EOS funds %s from account %s to %s via inline action" % (transferAmount, evmAcc.name, testAcc.name))
    nonce = nonce + 1
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(amount*10000*szabo*100),
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    amount=4.0000
    transferAmount="4.000 {0}".format(CORE_SYMBOL)
    Utils.Print("Test action ordering: action 2: transfer EVM->EOS funds %s from account %s to %s via inline action" % (transferAmount, evmAcc.name, testAcc.name))
    nonce = nonce + 1
    gasP = getGasPrice()
    signed_trx2 = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(amount*10000*szabo*100),
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    actData = {"recipient":defertestAcc.name, "account":evmAcc.name, "miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:], "rlptx2":Web3.to_hex(get_raw_transaction(signed_trx2))[2:]}
    trans = prodNode.pushMessage(defertest2Acc.name, "notifytest", json.dumps(actData), '-p {0}'.format(defertest2Acc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)
    assert(row4["nonce"] == 4) 
    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["balance"] == "0000000000000000000000000000000000000000000000013539c783bbf0c000")


    # Launch eos-evm-node
    Utils.Print("===== laucnhing eos-evm-node & eos-evm-rpc =====")
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
    evmRPCPOpen=Utils.delayedCheckOutput(cmdArr, stdout=outFile, stderr=errFile)

    def validate_all_balances():
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

    # Validate all balances are the same on both sides
    validate_all_balances()

    # Validate special signatures handling
    def get_stored_value(address):
        result = processUrllibRequest("http://127.0.0.1:8881", payload={"method":"eth_call","params":[{"from":fromAdd, "to":increment_contract, "data":"0x0a79309b000000000000000000000000"+address}, "latest"],"id":1,"jsonrpc":"2.0"})
        return int(result["payload"]["result"], 16)

    assert(get_stored_value(accSpecialAdd[2:]) == 1) #pushtx
    assert(get_stored_value(makeReservedEvmAddress(convert_name_to_value(minerAcc.name))[2:]) == 1) #call
    assert(get_stored_value('0290ffefa58ee84a3641770ab910c48d3441752d') == 1) #admincall

    def get_block(num):
        result = processUrllibRequest("http://127.0.0.1:8881", payload={"method":"eth_getBlockByNumber","params":[num, False],"id":1,"jsonrpc":"2.0"})
        return result["payload"]["result"]

    Utils.Print("Verify evm_version==0")
    # Verify header.nonce == 0 (evmversion=0)
    b = get_block("latest")
    assert(b["nonce"] == "0x0000000000000000")

    # Switch to version 1
    Utils.Print("Switch to evm_version 1")
    actData = {"version":1}
    trans = prodNode.pushMessage(evmAcc.name, "setversion", json.dumps(actData), '-p {0}'.format(evmAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(2)

    # Transfer funds to trigger version change
    nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, "111.0000 EOS", "0xB106D2C286183FFC3D1F0C4A6f0753bB20B407c2", waitForTransBlock=True)
    time.sleep(2)

    Utils.Print("Verify evm_version==1 and base_fe_per_gas")
    # Verify header.nonce == 1 (evmversion=1)
    # Verify header.baseFeePerGas == 10GWei (0x2540be400)
    b = get_block("latest")

    assert(b["nonce"] == "0x0000000000000001")
    assert(b["baseFeePerGas"] == "0x2540be400")

    Utils.Print("Transfer funds to trigger evmtx event on contract")
    # Transfer funds (now using version=1)
    nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, "111.0000 EOS", "0xB106D2C286183FFC3D1F0C4A6f0753bB20B407c2", waitForTransBlock=True)
    time.sleep(2)

    ### evmtx event order test
    Utils.Print("Test evmtx event order via evmbridge contract")
    # // SPDX-License-Identifier: GPL-3.0
    # pragma solidity >=0.7.0 <0.9.0;
    # contract BridgeTest {
    #     uint256 number;
    #     constructor() {
    #          number = 41;
    #     }
    #     function assertdata(uint256 expect) payable public {
    #         require(number == expect, "assertdata failed");
    #         number = number + 1;
    #     }
    #     function sendbridgemsg() payable public {
    #         number = number + 1;
    #         bytes memory receiver_msg = abi.encodeWithSignature("test(uint256)", number);
    #         address evmaddr = 0xbBBBbBbbbBBBBbbbbbbBBbBB5530EA015b900000;//eosio.evm
    #         (bool success, ) = evmaddr.call{value: msg.value}(
    #             abi.encodeWithSignature("bridgeMsgV0(string,bool,bytes)", "evmbridge", true, receiver_msg ));
    #         if(!success) { revert(); }
    #     }
    # }
    #
    nonce += 1
    evmChainId = 15555
    gasP = getGasPrice()
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=5000000,       
        gasPrice=gasP,
        data=Web3.to_bytes(hexstr='608060405234801561001057600080fd5b5060296000819055506105b2806100286000396000f3fe6080604052600436106100285760003560e01c80628fcf3e1461002d57806386bf4eff14610049575b600080fd5b610047600480360381019061004291906102b8565b610053565b005b6100516100af565b005b8060005414610097576040517f08c379a000000000000000000000000000000000000000000000000000000000815260040161008e90610342565b60405180910390fd5b60016000546100a69190610391565b60008190555050565b60016000546100be9190610391565b600081905550600080546040516024016100d891906103d4565b6040516020818303038152906040527f29e99f07000000000000000000000000000000000000000000000000000000007bffffffffffffffffffffffffffffffffffffffffffffffffffffffff19166020820180517bffffffffffffffffffffffffffffffffffffffffffffffffffffffff83818316178352505050509050600073bbbbbbbbbbbbbbbbbbbbbbbb5530ea015b900000905060008173ffffffffffffffffffffffffffffffffffffffff163460018560405160240161019e9291906104e6565b6040516020818303038152906040527ff781185b000000000000000000000000000000000000000000000000000000007bffffffffffffffffffffffffffffffffffffffffffffffffffffffff19166020820180517bffffffffffffffffffffffffffffffffffffffffffffffffffffffff83818316178352505050506040516102289190610565565b60006040518083038185875af1925050503d8060008114610265576040519150601f19603f3d011682016040523d82523d6000602084013e61026a565b606091505b505090508061027857600080fd5b505050565b600080fd5b6000819050919050565b61029581610282565b81146102a057600080fd5b50565b6000813590506102b28161028c565b92915050565b6000602082840312156102ce576102cd61027d565b5b60006102dc848285016102a3565b91505092915050565b600082825260208201905092915050565b7f61737365727464617461206661696c6564000000000000000000000000000000600082015250565b600061032c6011836102e5565b9150610337826102f6565b602082019050919050565b6000602082019050818103600083015261035b8161031f565b9050919050565b7f4e487b7100000000000000000000000000000000000000000000000000000000600052601160045260246000fd5b600061039c82610282565b91506103a783610282565b92508282019050808211156103bf576103be610362565b5b92915050565b6103ce81610282565b82525050565b60006020820190506103e960008301846103c5565b92915050565b7f65766d6272696467650000000000000000000000000000000000000000000000600082015250565b60006104256009836102e5565b9150610430826103ef565b602082019050919050565b60008115159050919050565b6104508161043b565b82525050565b600081519050919050565b600082825260208201905092915050565b60005b83811015610490578082015181840152602081019050610475565b60008484015250505050565b6000601f19601f8301169050919050565b60006104b882610456565b6104c28185610461565b93506104d2818560208601610472565b6104db8161049c565b840191505092915050565b600060608201905081810360008301526104ff81610418565b905061050e6020830185610447565b818103604083015261052081846104ad565b90509392505050565b600081905092915050565b600061053f82610456565b6105498185610529565b9350610559818560208601610472565b80840191505092915050565b60006105718284610534565b91508190509291505056fea2646970667358221220ac18e6f72606f415174ea5fa2cf02da58e2ec7af6b59282e166efa50f79aef3164736f6c63430008120033'),
        chainId=evmChainId
    ), evmSendKey)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    assert retValue[0], f"push trx should have succeeded: {retValue}"
    bridgemsgcontractaddr = makeContractAddress("9E126C57330FA71556628e0aabd6B6B6783d99fA", nonce)
    assert_contract_exist(bridgemsgcontractaddr)
    Utils.Print("bridge msg contract addr is:" + str(bridgemsgcontractaddr))
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)

    Utils.Print("call bridgereg in evm runtime contract for account evmbridge")
    prodNode.pushMessage(evmAcc.name, "bridgereg", '["evmbridge","evmbridge","1.0000 EOS"]', '-p {0} -p evmbridge'.format(evmAcc.name), silentErrors=False)

    Utils.Print("push EVM trx to trigger bridgemsg from EVM to notify evmbridge account")
    amount=1.0000
    nonce += 1
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=100000,      
        gasPrice=gasP,
        to=Web3.to_checksum_address(bridgemsgcontractaddr),
        data=Web3.to_bytes(hexstr='86bf4eff'), #function sendbridgemsg() 
        value=int(amount*10000*szabo*100),
        chainId=evmChainId
    ), evmSendKey)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)
    assert retValue[0], f"push trx to bridge msg contract should have succeeded: {retValue}"
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)

    # update gas parameter 
    Utils.Print("Update gas parameter: ram price = 100 EOS per MB, gas price = 900Gwei")
    trans = prodNode.pushMessage(evmAcc.name, "updtgasparam", json.dumps({"ram_price_mb":"100.0000 EOS","gas_price":900000000000}), '-p {0}'.format(evmAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(2)

    Utils.Print("Transfer funds to trigger config change event on contract")
    # Transfer funds (now using version=1)
    nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, "112.0000 EOS", "0xB106D2C286183FFC3D1F0C4A6f0753bB20B407c2", waitForTransBlock=True)
    time.sleep(2)

    b = get_block("latest")
    Utils.Print("get_block_latest: " + json.dumps(b))
    # "consensusParameter": {"gasFeeParameters": {"gasCodedeposit": 106, "gasNewaccount": 36782, "gasSset": 39576, "gasTxcreate": 64236, "gasTxnewaccount": 36782}

    assert("consensusParameter" in b)
    assert(b["consensusParameter"]["gasFeeParameters"]["gasCodedeposit"] == 106)
    assert(b["consensusParameter"]["gasFeeParameters"]["gasNewaccount"] == 36782)
    assert(b["consensusParameter"]["gasFeeParameters"]["gasSset"] == 39576)
    assert(b["consensusParameter"]["gasFeeParameters"]["gasTxcreate"] == 64236)
    assert(b["consensusParameter"]["gasFeeParameters"]["gasTxnewaccount"] == 36782)
    gas_newAccount = b["consensusParameter"]["gasFeeParameters"]["gasNewaccount"]

    # Verify header.baseFeePerGas still 10GWei (0x2540be400) it will change in 3mins
    b = get_block("latest")
    assert(b["baseFeePerGas"] == "0x2540be400")

    # --------------------------------------------
    # EVM -> EOS
    #   0x9E126C57330FA71556628e0aabd6B6B6783d99fA private key: 0xba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0
    toAdd = makeReservedEvmAddress(convert_name_to_value(aliceAcc.name))
    evmSendKey = "ba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0"
    amount=1.0000
    transferAmount="1.0000 {0}".format(CORE_SYMBOL)
    bal1 = w3.eth.get_balance(Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fA"))
    Print("Using new gas param, transfer funds %s from account %s to reserved account (EVM->EOS)" % (transferAmount, evmAcc.name))
    nonce = nonce + 1
    estimate_tx = {
        'from': Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fA"),
        'gas':100000,
        'maxFeePerGas':900000000000,
        'maxPriorityFeePerGas': 900000000000,
        'to':Web3.to_checksum_address(toAdd),
        'value':int(amount*10000*szabo*100), # .0001 EOS is 100 szabos
        'data':b'',
        'chainId':evmChainId
    }
    assert(w3.eth.estimate_gas(estimate_tx) == 21000)
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce,
        gas=100000,       #100k Gas
        maxFeePerGas = 900000000000,
        maxPriorityFeePerGas = 900000000000,
        #gasPrice=900000000000,
        to=Web3.to_checksum_address(toAdd),
        value=int(amount*10000*szabo*100), # .0001 EOS is 100 szabos
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    Print("EVM transaction hash is: %s" % (Web3.to_hex(signed_trx.hash)))
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\tverify balance from evm-rpc, account row4: ", row4)
    bal2 = w3.eth.get_balance(Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fA"))

    # balance different = 1.0 EOS (val) + 900(Gwei) (21000(base gas))
    assert(bal1 == bal2 + 1000000000000000000 + 900000000000 * 21000)

    Utils.Print("try to get transaction %s from evm-rpc" % (Web3.to_hex(signed_trx.hash)))
    evm_tx = w3.eth.get_transaction(signed_trx.hash)
    tx_dict = toDict(evm_tx)
    Utils.Print("evm transaction is %s" % (json.dumps(tx_dict)))
    assert(prefix_0x(str(tx_dict["hash"])) == str(Web3.to_hex(signed_trx.hash)))

    Utils.Print("try to get transaction receipt %s from evm-rpc" % (Web3.to_hex(signed_trx.hash)))
    evm_tx = w3.eth.get_transaction_receipt(signed_trx.hash)
    tx_dict = toDict(evm_tx)
    Utils.Print("evm transaction receipt is %s" % (json.dumps(tx_dict)))
    assert(prefix_0x(str(tx_dict["transactionHash"])) == str(Web3.to_hex(signed_trx.hash)))

    validate_all_balances() # validate balances between native & EVM

    # --------------------------------------------
    # EVM -> EVM 
    # 1st time EVM->EVM (new address)
    # 2nd time EVM->EVM (existing address)
    for i in range(0,2):
        evmSendKey = "ba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0"
        amount=1.0000
        transferAmount="1.0000 {0}".format(CORE_SYMBOL)
        bal1 = w3.eth.get_balance(Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fA"))
        Print("Using new gas param, transfer %s from account %s to EVM account (EVM->EVM)" % (transferAmount, evmAcc.name))
        nonce = nonce + 1
        estimate_tx = {
            'from': Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fA"),
            'gas':100000,
            'maxFeePerGas':900000000000,
            'maxPriorityFeePerGas': 900000000000,
            'to':Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fB"),
            'value':int(amount*10000*szabo*100), # .0001 EOS is 100 szabos
            'data':b'',
            'chainId':evmChainId
        }
        Utils.Print("estimate_gas is {}".format(w3.eth.estimate_gas(estimate_tx)))
        assert(w3.eth.estimate_gas(estimate_tx) == 21000 + (1 - i) * 36782)
        signed_trx = w3.eth.account.sign_transaction(dict(
            nonce=nonce,
            gas=100000,       #100k Gas
            maxFeePerGas = 900000000000,
            maxPriorityFeePerGas = 900000000000,
            #gasPrice=900000000000,
            to=Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fB"),
            value=int(amount*10000*szabo*100), # .0001 EOS is 100 szabos
            data=b'',
            chainId=evmChainId
        ), evmSendKey)
        Print("EVM transaction hash is: %s" % (Web3.to_hex(signed_trx.hash)))
        actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
        trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)
        prodNode.waitForTransBlockIfNeeded(trans[1], True)
        row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
        Utils.Print("\tverify balance from evm-rpc, account row4: ", row4)
        bal2 = w3.eth.get_balance(Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fA"))

        # balance different = 1.0 EOS (val) + 900(Gwei) (21000(base gas) + 36782 or 0)
        assert(bal1 == bal2 + 1000000000000000000 + 900000000000 * (21000 + (1 - i) * 36782))

        Utils.Print("try to get transaction %s from evm-rpc" % (Web3.to_hex(signed_trx.hash)))
        evm_tx = w3.eth.get_transaction(signed_trx.hash)
        tx_dict = toDict(evm_tx)
        Utils.Print("evm transaction is %s" % (json.dumps(tx_dict)))
        assert(prefix_0x(str(tx_dict["hash"])) == str(Web3.to_hex(signed_trx.hash)))

        Utils.Print("try to get transaction receipt %s from evm-rpc" % (Web3.to_hex(signed_trx.hash)))
        evm_tx = w3.eth.get_transaction_receipt(signed_trx.hash)
        tx_dict = toDict(evm_tx)
        Utils.Print("evm transaction receipt is %s" % (json.dumps(tx_dict)))
        assert(prefix_0x(str(tx_dict["transactionHash"])) == str(Web3.to_hex(signed_trx.hash)))

        if i == 0:
            validate_all_balances() # validate balances between native & EVM

    # Wait 3 mins
    Utils.Print("Wait 3 mins")
    time.sleep(180)

    # Trigger change in base_fee_per_gas
    nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, "1.0000 EOS", "0xB106D2C286183FFC3D1F0C4A6f0753bB20B407c2", waitForTransBlock=True)
    time.sleep(2)

    # Verify header.baseFeePerGas is now 900GWei (0xd18c2e2800)
    b = get_block("latest")
    assert(b["baseFeePerGas"] == "0xd18c2e2800")

    ####### Test eth_call for simple contract

    # // SPDX-License-Identifier: GPL-3.0
    # pragma solidity >=0.7.6;

    # contract TimeShow {
    #     function getTimestamp() public view  returns (uint) {
    #         return block.timestamp;
    #     } 
    # }
    Print("Test eth_call")
    special_nonce += 1
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=special_nonce,
        gas=2000000,
        maxFeePerGas = 900000000000,
        maxPriorityFeePerGas = 900000000000,
        data=Web3.to_bytes(hexstr='6080604052348015600e575f80fd5b5060ae80601a5f395ff3fe6080604052348015600e575f80fd5b50600436106026575f3560e01c8063188ec35614602a575b5f80fd5b60306044565b604051603b91906061565b60405180910390f35b5f42905090565b5f819050919050565b605b81604b565b82525050565b5f60208201905060725f8301846054565b9291505056fea2646970667358221220bf024720b7909e9eff500e9bccd1672f220872e152d393c961d4998e57a4944a64736f6c634300081a0033'),
        chainId=15555
    ), accSpecialKey)

    # Deploy "Blocktime" contract
    blocktime_contract = makeContractAddress(accSpecialAdd, special_nonce)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(2)

    b = get_block("latest")

    call_msg = {
        "method": "eth_call",
        "params": [{
            "from": accSpecialAdd,
            "to": blocktime_contract,
            "data": "0x188ec356", #getTimestamp()
            "value": "0x0"
        }, b['number']],
        "jsonrpc": "2.0",
        "id": 1,
    }

    result = processUrllibRequest("http://localhost:8881", payload=call_msg)
    Utils.Print("Comparing: %s vs %s" % (b['timestamp'], result['payload']['result']))
    assert(int(b["timestamp"],16) == int(result['payload']['result'],16))
    ####### END Test eth_call for simple contract

    Utils.Print("Validate all balances (check evmtx event processing)")
    # Validate all balances (check evmtx event)
    validate_all_balances()

    ####### BEGIN Test eth_getLogs
    # // SPDX-License-Identifier: GPL-3.0
    # pragma solidity >=0.7.0 <0.9.0;
    # contract Eventor {
    #     event Deposit(address indexed _from, uint _value);
    #     function deposit(uint256 _value) public {
    #         emit Deposit(msg.sender, _value);
    #     }
    # }

    Print("Test eth_getLogs (deploy contract)")
    special_nonce += 1
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=special_nonce,
        gas=2000000,
        maxFeePerGas = 900000000000,
        maxPriorityFeePerGas = 900000000000,
        data=Web3.to_bytes(hexstr='608060405234801561001057600080fd5b50610165806100206000396000f3fe608060405234801561001057600080fd5b506004361061002b5760003560e01c8063b6b55f2514610030575b600080fd5b61004a600480360381019061004591906100d8565b61004c565b005b3373ffffffffffffffffffffffffffffffffffffffff167fe1fffcc4923d04b559f4d29a8bfc6cda04eb5b0d3c460751c2402c5c5cc9109c826040516100929190610114565b60405180910390a250565b600080fd5b6000819050919050565b6100b5816100a2565b81146100c057600080fd5b50565b6000813590506100d2816100ac565b92915050565b6000602082840312156100ee576100ed61009d565b5b60006100fc848285016100c3565b91505092915050565b61010e816100a2565b82525050565b60006020820190506101296000830184610105565b9291505056fea26469706673582212204e317ada7494f9d6291c2dc3071bb4892e3018729f4b94e5e6aa88bbf8224c3864736f6c634300080d0033'),
        chainId=15555
    ), accSpecialKey)

    # Deploy "Eventor" contract
    eventor_contract = makeContractAddress(accSpecialAdd, special_nonce)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(2)

    validate_all_balances()

    Print("Test eth_getLogs (call deposit)")
    special_nonce += 1
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=special_nonce,
        gas=2000000,
        maxFeePerGas = 900000000000,
        maxPriorityFeePerGas = 900000000000,
        to=Web3.to_checksum_address(eventor_contract),
        data=Web3.to_bytes(hexstr='b6b55f250000000000000000000000000000000000000000000000000000000000000016'),
        chainId=15555
    ), accSpecialKey)

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(4)

    deposit_tx = w3.eth.get_transaction_receipt(signed_trx.hash)
    logs = w3.eth.get_logs({
        'fromBlock': deposit_tx['blockNumber'],
        'toBlock': deposit_tx['blockNumber']
    })

    assert(len(logs) == 1)
    assert(str(logs[0]['address']).lower() == str(eventor_contract).lower())

    validate_all_balances()

    ####### END Test eth_getLogs

    overhead_price = 900000000000
    storage_price = 900000000000
    Utils.Print("Set gas prices: overhead_price:{0}, storage_price:{1}".format(overhead_price, storage_price))
    actData = {"prices":{"overhead_price":overhead_price,"storage_price":storage_price}}
    trans = prodNode.pushMessage(evmAcc.name, "setgasprices", json.dumps(actData), '-p {0}'.format(evmAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    time.sleep(1)

    # Switch to version 3, test overhead_price & storage_price 
    Utils.Print("Switch to evm_version 3, test overhead_price & storage_price")
    actData = {"version":3}
    trans = prodNode.pushMessage(evmAcc.name, "setversion", json.dumps(actData), '-p {0}'.format(evmAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(1)

    evmSendKey = "ba8c9ff38e4179748925335a9891b969214b37dc3723a1754b8b849d3eea9ac0"
    amount=1.0000
    transferAmount="1.0000 {0}".format(CORE_SYMBOL)

    for j in range(0,4):
        overhead_price = 900000000000
        storage_price = 450000000000 * (j + 1)
        inclusion_price = 0
        if j == 3:
            inclusion_price = 300000000000
        gas_price = overhead_price + inclusion_price
        if storage_price > overhead_price:
            gas_price = storage_price + inclusion_price
        Utils.Print("Test gas v3 round %s: overhead_price=%s, storage_price=%s, inclusion_price=%s" % (j, overhead_price, storage_price,inclusion_price))
        actData = {"prices":{"overhead_price":overhead_price,"storage_price":storage_price}}
        trans = prodNode.pushMessage(evmAcc.name, "setgasprices", json.dumps(actData), '-p {0}'.format(evmAcc.name), silentErrors=True)
        prodNode.waitForTransBlockIfNeeded(trans[1], True)

        # Wait 3 mins
        Utils.Print("Test gas v3 round %s: Wait 3 mins to ensure new gas prices become effective" % (j))
        time.sleep(185)

        # ensure EOS->EVM bridge still works
        Utils.Print("Test gas v3 round %s: ensure EOS->EVM bridge still works" %(j))
        nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, "1.0000 EOS", "0xB106D2C286183FFC3D1F0C4A6f0753bB20B407c2", waitForTransBlock=True)

        # ensure call action (special signature) still works
        Utils.Print("Test gas v3 round %s: ensure call action (special signature) still works" % (j))
        actData = {"from":minerAcc.name, "to":increment_contract[2:], "value":"0000000000000000000000000000000000000000000000000000000000000000", "data":"d09de08a", "gas_limit":"100000"}
        trans = prodNode.pushMessage(evmAcc.name, "call", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)
        prodNode.waitForTransBlockIfNeeded(trans[1], True)

        # ensure transaction fail if gas price is not enough
        Utils.Print("Test gas v3 round %s: ensure transaction fail if gas price is not enough" % (j))
        nonce = nonce + 1
        signed_trx = w3.eth.account.sign_transaction(dict(
            nonce=nonce,
            gas=21000 + gas_newAccount,
            maxFeePerGas = gas_price - inclusion_price - 1,
            maxPriorityFeePerGas = 0,
            to=Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fB"), #exising account
            value=int(amount*10000*szabo*100), # .0001 EOS is 100 szabos
            data=b'',
            chainId=evmChainId
        ), evmSendKey)
        actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
        trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
        assert not trans[0], f"push trx should have failed: {trans}"
        nonce = nonce - 1

        # test a success transfer under v3
        Utils.Print("Test gas v3 round %s: test a success transfer, gas_price=%s, inclusion_price=%s" % (j, gas_price, inclusion_price))
        bal1 = w3.eth.get_balance(Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fA"))
        nonce = nonce + 1
        signed_trx = w3.eth.account.sign_transaction(dict(
            nonce=nonce,
            gas=21000 + gas_newAccount,
            maxFeePerGas = gas_price,
            maxPriorityFeePerGas = inclusion_price,
            to=Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d000" + chr(ord('0') + j)), # new addresses
            value=int(amount*10000*szabo*100), # .0001 EOS is 100 szabos
            data=b'',
            chainId=evmChainId
        ), evmSendKey)
        actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(get_raw_transaction(signed_trx))[2:]}
        trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=False)
        time.sleep(2.0)
        bal2 = w3.eth.get_balance(Web3.to_checksum_address("0x9E126C57330FA71556628e0aabd6B6B6783d99fA"))
        Utils.Print("Test gas v3 round %s: balance difference is %s" % (j, bal1 - bal2))
        if j == 0: # overhead price 900 Gwei, storage price 450 Gwei, inclusion price 0 Gwei
            assert bal1 - bal2 == int(amount*10000*szabo*100) + (21000 + int(gas_newAccount * 450 / 900)) * gas_price, "Test gas v3 0:balance is not as expected"
        elif j == 1: # overhead price 900 Gwei, storage price 900 Gwei, inclusion price 0 Gwei
            assert bal1 - bal2 == int(amount*10000*szabo*100) + (21000 + gas_newAccount) * gas_price, "Test gas v3 1:balance is not as expected"
        elif j == 2: # overhead price 900 Gwei, storage price 1350 Gwei, inclusion price 0 Gwei
            assert bal1 - bal2 == int(amount*10000*szabo*100) + (21000 - int(21000 * (1350 - 900) / 1350) + gas_newAccount) * gas_price, "Test gas v3 2:balance is not as expected"
        elif j == 3: # overhead price 900 Gwei, storage price 1800 Gwei, inclusion price 300 Gwei
            assert bal1 - bal2 == int(amount*10000*szabo*100) + (21000 - int(21000 * (1800 - 900) / (1800 + 300)) + int(gas_newAccount * 1800 / (1800 + 300))) * gas_price, "Test gas v3 3:balance is not as expected"

    validate_all_balances()

    Utils.Print("checking %s for errors" % (nodeStdErrDir))
    foundErr = False
    stdErrFile = open(nodeStdErrDir, "r")
    lines = stdErrFile.readlines()
    for line in lines:
        if line.find("ERROR") != -1 or line.find("CRIT") != -1:
            Utils.Print("  Found ERROR in EOS EVM NODE log: ", line)
            foundErr = True

    Utils.Print("checking %s for errors" % (rpcStdErrDir))
    stdErrFile = open(rpcStdErrDir, "r")
    lines = stdErrFile.readlines()
    for line in lines:
        if line.find("ERROR") != -1 or line.find("CRIT") != -1:
            Utils.Print("  Found ERROR in EOS EVM RPC log: ", line)
            foundErr = True

    testSuccessful= not foundErr
    if testSuccessful:
        Utils.Print("test success, ready to shut down cluster")
    else:
        Utils.Print("test failed, ready to shut down cluster")

finally:
    Utils.Print("shutting down cluster")
    TestHelper.shutdown(cluster, walletMgr, testSuccessful=testSuccessful, dumpErrorDetails=dumpErrorDetails)
    if killEosInstances:
        Utils.Print("killing EOS instances")
        if evmNodePOpen is not None:
            evmNodePOpen.kill()
        if evmRPCPOpen is not None:
            evmRPCPOpen.kill()
        if eosEvmMinerPOpen is not None:
            eosEvmMinerPOpen.kill()
        

exitCode = 0 if testSuccessful else 1
exit(exitCode)
