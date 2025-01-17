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

import requests
import json
import threading

import brownie

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
appArgs.add(flag="--miner-cmd", type=str, help="command line to start EVM miner", default="node dist/index.js")
appArgs.add(flag="--flask-proxy-root", type=str, help="command line to start EVM miner", default=".")

args=TestHelper.parse_args({"--keep-logs","--dump-error-details","-v","--leave-running"}, applicationSpecificArgs=appArgs)
debug=args.v
killEosInstances= not args.leave_running
dumpErrorDetails=args.dump_error_details
eosEvmContractRoot=args.eos_evm_contract_root
eosEvmBuildRoot=args.eos_evm_build_root
genesisJson=args.genesis_json
useMiner=args.use_miner
minerCmd=args.miner_cmd
flaskProxyRoot=args.flask_proxy_root

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
flaskProcessPopen = None

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

def setEosEvmMinerEnv(eosnode):
    os.environ["PRIVATE_KEY"]=f"{minerAcc.activePrivateKey}"
    os.environ["MINER_ACCOUNT"]=f"{minerAcc.name}"
    os.environ["RPC_ENDPOINTS"]="http://127.0.0.1:" + str(eosnode.port)
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
    result = processUrllibRequest("http://127.0.0.1:18888", payload={"method":"eth_gasPrice","params":[],"id":1,"jsonrpc":"2.0"})
    Utils.Print("getGasPrice from miner, result: ", result)
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

    specificExtraNodeosArgs[1]="--transaction-retry-max-storage-size-gb 1 "

    extraNodeosArgs="--contracts-console --resource-monitor-not-shutdown-on-threshold-exceeded "

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

    trans = prodNode.pushMessage(evmAcc.name, "init", '{{"chainid":15555, "fee_params": {{"gas_price": "300000000000", "miner_cut": 10000, "ingress_bridge_fee": "0.0000 {0}"}}}}'.format(CORE_SYMBOL), '-p eosio.evm')
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
        setEosEvmMinerEnv(nonProdNode)
        dataDir = Utils.DataDir + "eos-evm-miner"
        outDir = dataDir + "/eos-evm-miner.stdout"
        errDir = dataDir + "/eos-evm-miner.stderr"
        shutil.rmtree(dataDir, ignore_errors=True)
        os.makedirs(dataDir)
        outFile = open(outDir, "w")
        errFile = open(errDir, "w")
        cmd = minerCmd
        Utils.Print("Launching: %s" % cmd)
        cmdArr=shlex.split(cmd)
        eosEvmMinerPOpen=subprocess.Popen(cmdArr, cwd=useMiner)
        time.sleep(10.0) # wait for miner's gasprice updated
    else:
        assert False, "useMiner must be set"

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

    gasP = None
    gasP = getGasPrice()
    assert gasP is not None, "failed to get gas price from miner"

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

    # Test some failure cases: incorrect nonce
    Utils.Print("Send balance again, should fail with wrong nonce")
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    assert not retValue[0], f"push trx should have failed: {retValue}"

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

    # set ingress bridge fee
    Utils.Print("Set ingress bridge fee")
    data='[{{"gas_price": null, "miner_cut": null, "ingress_bridge_fee": "0.0100 {}"}}]'.format(CORE_SYMBOL)
    trans=prodNode.pushMessage(evmAcc.name, "setfeeparams", data, '-p {0}'.format(evmAcc.name))

    rows=prodNode.getTable(evmAcc.name, evmAcc.name, "balances")
    Utils.Print("\tBefore transfer table rows:", rows)

    # Launch eos-evm-node
    Utils.Print("===== laucnhing eos-evm-node =====")
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

    # Switch to version 1
    Utils.Print("Switch to evm_version 1")
    actData = {"version":1}
    trans = prodNode.pushMessage(evmAcc.name, "setversion", json.dumps(actData), '-p {0}'.format(evmAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(1.5)

    # Transfer funds to trigger version change
    nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, "111.0000 EOS", "0xB106D2C286183FFC3D1F0C4A6f0753bB20B407c2", waitForTransBlock=True)
    time.sleep(1.5)

    # # update gas parameter 
    Utils.Print("Update gas parameter: ram price = 100 EOS per MB, gas price = 300Gwei")
    trans = prodNode.pushMessage(evmAcc.name, "updtgasparam", json.dumps({"ram_price_mb":"100.0000 EOS","gas_price":300000000000}), '-p {0}'.format(evmAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);
    time.sleep(1.5)

    Utils.Print("Transfer funds to trigger config change event on contract")
    # Transfer funds (now using version=1)
    nonProdNode.transferFunds(cluster.eosioAccount, evmAcc, "112.0000 EOS", "0xB106D2C286183FFC3D1F0C4A6f0753bB20B407c2", waitForTransBlock=True)

    # Launch eos-evm-rpc
    Utils.Print("===== laucnhing eos-evm-rpc =====")
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

    Utils.Print("start Flask server to separate read/write requests")
    flaskProcessPopen=subprocess.Popen(["python3", flaskProxyRoot + "/flask_proxy.py"])
    time.sleep(1.0)

    # Validate all balances are the same on both sides
    validate_all_balances()

    Utils.Print("test brownie connection")
    brownie.network.connect('localhost5000')
    brownie.accounts.add("ac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
    brownie.accounts.add("a3f1b69da92a0233ce29485d3049a4ace39e8d384bbc2557e3fc60940ce4e954") # account not exist
    Utils.Print("chain id is:" + str(brownie.chain.id))
    Utils.Print("number of blocks:" + str(len(brownie.chain)))

    for i in range(0, len(brownie.accounts)):
        Utils.Print("brownie.accounts[{0}] {1} balance: {2}".format(i, brownie.accounts[i].address, brownie.accounts[i].balance()))

    assert brownie.accounts[1].balance() == 0, "incorrect brownie.accounts[i].balance()"

    # transfer EOS from existing EVM address to new EVM address & existing EVM address
    for k in range(0,2):
        acct0_bal = brownie.accounts[0].balance()
        acct1_bal = brownie.accounts[1].balance()
        amount = 100000000 * (k + 1)
        if k == 0:
            expected_gas_used = 131346
        else:
            expected_gas_used = 21000
        estimate_tx = {
            'from': brownie.accounts[0].address,
            'to': brownie.accounts[1].address,
            'gas': 1000000,
            'gasPrice': gasP,
            'nonce': brownie.accounts[1].nonce,
            'chainId': brownie.chain.id,
            'data': '',
            'value': amount
        }
        estimated_gas = w3.eth.estimate_gas(estimate_tx)
        Utils.Print("estimated gas of transfer to new addr:" + str(estimated_gas))
        receipt = brownie.accounts[0].transfer(brownie.accounts[1], amount, gas_price=gasP)
        time.sleep(1.5)
        assert receipt.gas_used == expected_gas_used, "expected gas used is " + str(expected_gas_used)
        assert estimated_gas >= receipt.gas_used
        assert estimated_gas < int(expected_gas_used * 1.5) # allow up to 50% more
        for i in range(0, len(brownie.accounts)):
            Utils.Print("brownie.accounts[{0}] {1} balance: {2}".format(i, brownie.accounts[i].address, brownie.accounts[i].balance()))
        assert brownie.accounts[0].balance() == acct0_bal - amount - receipt.gas_used * 300 * 1000000000, "incorrect brownie.accounts[0].balance()"
        assert brownie.accounts[1].balance() == acct1_bal + amount, "incorrect brownie.accounts[1].balance()"

    # transfer EOS from existing EVM address to reserved address
    acct0_bal = brownie.accounts[0].balance()
    amount = 300000000
    expected_gas_used = 21000
    estimate_tx = {
        'from': brownie.accounts[0].address,
        'to': w3.to_checksum_address("0xbbbbbbbbbbbbbbbbbbbbbbbb5530ea0000000000"), #eosio
        'gas': 1000000,
        'gasPrice': gasP,
        'nonce': brownie.accounts[1].nonce,
        'chainId': brownie.chain.id,
        'data': '',
        'value': amount
    }
    estimated_gas = w3.eth.estimate_gas(estimate_tx)
    Utils.Print("EVM->reserved addr, estimated gas:" + str(estimated_gas))
    receipt = brownie.accounts[0].transfer(brownie.accounts[1], amount, gas_price=gasP)
    time.sleep(1.5)
    Utils.Print("EVM->reserved addr, gas used: " + str(receipt.gas_used))
    assert receipt.gas_used == expected_gas_used, "expected gas used is " + str(expected_gas_used)
    assert estimated_gas >= receipt.gas_used
    assert estimated_gas < int(expected_gas_used * 1.5) # allow up to 50% more
    for i in range(0, len(brownie.accounts)):
        Utils.Print("brownie.accounts[{0}] {1} balance: {2}".format(i, brownie.accounts[i].address, brownie.accounts[i].balance()))
    assert brownie.accounts[0].balance() == acct0_bal - amount - receipt.gas_used * 300 * 1000000000, "incorrect brownie.accounts[0].balance()"

    validate_all_balances()

    Utils.Print("test success, ready to shut down cluster")
    testSuccessful = True

except Exception as e: print("Exception:" + str(e))
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
    if flaskProcessPopen is not None:
        flaskProcessPopen.kill()

exitCode = 0 if testSuccessful else 1
exit(exitCode)
