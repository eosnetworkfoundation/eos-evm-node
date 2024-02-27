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

from TestHarness import Cluster, TestHelper, Utils, WalletMgr, CORE_SYMBOL
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
        return 1
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

try:
    TestHelper.printSystemInfo("BEGIN")

    w3 = Web3(Web3.HTTPProvider("http://localhost:8881"))

    cluster.setWalletMgr(walletMgr)

    specificExtraNodeosArgs={}
    shipNodeNum = total_nodes - 1
    specificExtraNodeosArgs[shipNodeNum]="--plugin eosio::state_history_plugin --state-history-endpoint 127.0.0.1:8999 --trace-history --chain-state-history --disable-replay-opts  "

    extraNodeosArgs="--contracts-console --resource-monitor-not-shutdown-on-threshold-exceeded"

    Print("Stand up cluster")
    if cluster.launch(pnodes=pnodes, totalNodes=total_nodes, extraNodeosArgs=extraNodeosArgs, specificExtraNodeosArgs=specificExtraNodeosArgs) is False:
        errorExit("Failed to stand up eos cluster.")

    Print ("Wait for Cluster stabilization")
    # wait for cluster to start producing blocks
    if not cluster.waitOnClusterBlockNumSync(3):
        errorExit("Cluster never stabilized")
    Print ("Cluster stabilized")

    prodNode = cluster.getNode(0)
    nonProdNode = cluster.getNode(1)

    accounts=cluster.createAccountKeys(5)
    if accounts is None:
        Utils.errorExit("FAILURE - create keys")

    evmAcc = accounts[0]
    evmAcc.name = "eosio.evm"
    testAcc = accounts[1]
    minerAcc = accounts[2]
    defertestAcc = accounts[3]
    defertest2Acc = accounts[4]

    testWalletName="test"

    Print("Creating wallet \"%s\"." % (testWalletName))
    testWallet=walletMgr.create(testWalletName, [cluster.eosioAccount,accounts[0],accounts[1],accounts[2],accounts[3],accounts[4]])

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

    # add eosio.code permission to defertest2 account
    cmd="set account permission " + defertest2Acc.name + " active --add-code -p " + defertest2Acc.name + "@active"
    prodNode.processCleosCmd(cmd, cmd, silentErrors=False, returnType=ReturnType.raw)

    trans = prodNode.pushMessage(evmAcc.name, "init", '{{"chainid":15555, "fee_params": {{"gas_price": "10000000000", "miner_cut": 100000, "ingress_bridge_fee": "0.0000 {0}"}}}}'.format(CORE_SYMBOL), '-p eosio.evm')
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
        prodNode.transferFunds(cluster.eosioAccount, evmAcc, transferAmount, "0x" + k[2:].lower(), waitForTransBlock=True)
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

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
    retValue = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    assert retValue[0], f"push trx should have succeeded: {retValue}"
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
    prodNode.transferFunds(testAcc, evmAcc, transferAmount, "0xF0cE7BaB13C99bA0565f426508a7CD8f4C247E5a", waitForTransBlock=True)

    row0=prodNode.getTableRow(evmAcc.name, evmAcc.name, "balances", 0)
    Utils.Print("\tAfter transfer table row:", row0)
    assert(row0["balance"]["balance"] == "1.0100 {0}".format(CORE_SYMBOL)) # should have fee at end of transaction
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
    prodNode.transferFunds(testAcc, evmAcc, transferAmount, "0xF0cE7BaB13C99bA0565f426508a7CD8f4C247E5a", waitForTransBlock=True)
    row0=prodNode.getTableRow(evmAcc.name, evmAcc.name, "balances", 0)
    Utils.Print("\tAfter transfer table row:", row0)
    assert(row0["balance"]["balance"] == "1.0200 {0}".format(CORE_SYMBOL)) # should have fee from both transfers
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
    prodNode.transferFunds(testAcc, evmAcc, transferAmount, "0x9E126C57330FA71556628e0aabd6B6B6783d99fA", waitForTransBlock=True)
    row0=prodNode.getTableRow(evmAcc.name, evmAcc.name, "balances", 0)
    Utils.Print("\tAfter transfer table row:", row0)
    assert(row0["balance"]["balance"] == "1.0300 {0}".format(CORE_SYMBOL)) # should have fee from all three transfers
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
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
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
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
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

    prodNode.transferFunds(testAcc, evmAcc, "10.0000 EOS", "0x0290ffefa58ee84a3641770ab910c48d3441752d", waitForTransBlock=True)
    prodNode.transferFunds(testAcc, evmAcc, "1000.0000 EOS", accSpecialAdd, waitForTransBlock=True)

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
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
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

    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
    trans = prodNode.pushMessage(evmAcc.name, "pushtx", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);

    # Test special signature: Call from miner account
    actData = {"from":minerAcc.name, "to":increment_contract[2:], "value":"0000000000000000000000000000000000000000000000000000000000000000", "data":"d09de08a", "gas_limit":"100000"}
    trans = prodNode.pushMessage(evmAcc.name, "call", json.dumps(actData), '-p {0}'.format(minerAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);

    # Test special signature: Call from `0x0290ffefa58ee84a3641770ab910c48d3441752d`
    prodNode.transferFunds(testAcc, evmAcc, "10.0000 EOS", "0x0290ffefa58ee84a3641770ab910c48d3441752d", waitForTransBlock=True)
    actData = {"from":"0290ffefa58ee84a3641770ab910c48d3441752d", "to":increment_contract[2:], "value":"0000000000000000000000000000000000000000000000000000000000000000", "data":"d09de08a", "gas_limit":"100000"}
    trans = prodNode.pushMessage(evmAcc.name, "admincall", json.dumps(actData), '-p {0}'.format(evmAcc.name), silentErrors=True)
    prodNode.waitForTransBlockIfNeeded(trans[1], True);

    ### Special signature test (end)

    # test hard-failed transaction (with --delay-sec)
    amount=7.0000
    transferAmount="7.000 {0}".format(CORE_SYMBOL)
    Print("Test hard-failed EVM transaction (with delay)")
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce + 1000, # Wrong nonce -> should hard fails
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(amount*10000*szabo*100),
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    actData = {"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:]}
    cmd="push action " + evmAcc.name + " pushtx \"" + json.dumps(actData).replace("\"","\\\"") + "\" --delay-sec 2 -p " + minerAcc.name
    trans=prodNode.processCleosCmd(cmd, cmd, silentErrors=False, returnType=ReturnType.raw)
    #Utils.Print(f"trans is {trans}");
    time.sleep(4) # sleep enough time to get hard fails.
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)
    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["balance"] == "000000000000000000000000000000000000000000000001887f8db687170000") #
    assert(row4["nonce"] == 2)


    # test hard-failed transaction (with defer)
    amount=9.0000
    transferAmount="9.000 {0}".format(CORE_SYMBOL)
    Print("Test hard-failed EVM transaction (with defer)")
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce + 1000, # Wrong nonce -> should hard fails
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(amount*10000*szabo*100),
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    actData = {"id":1,"account":evmAcc.name,"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:], "rlptx2":""}
    trans = prodNode.pushMessage(defertestAcc.name, "pushdefer", json.dumps(actData), '-p {0}'.format(defertestAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    #Utils.Print(f"trans is {trans}");
    time.sleep(4) # sleep enough time to get hard fails.
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)
    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["balance"] == "000000000000000000000000000000000000000000000001887f8db687170000") #
    assert(row4["nonce"] == 2) # nothing should execute


    # test soft-failed transaction (with defer)
    amount=11.0000
    transferAmount="11.000 {0}".format(CORE_SYMBOL)
    Print("Test soft-failed EVM transaction (with defer)")
    signed_trx = w3.eth.account.sign_transaction(dict(
        nonce=nonce + 1000, # Wrong nonce -> should hard fails
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(amount*10000*szabo*100),
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    amount=13.0000
    transferAmount="13.000 {0}".format(CORE_SYMBOL)
    nonce = nonce + 1
    signed_trx2 = w3.eth.account.sign_transaction(dict(
        nonce=nonce, # right nonce
        gas=100000,       #100k Gas
        gasPrice=gasP,
        to=Web3.to_checksum_address(toAdd),
        value=int(amount*10000*szabo*100),
        data=b'',
        chainId=evmChainId
    ), evmSendKey)
    actData = {"id":2,"account":evmAcc.name,"miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:], "rlptx2":Web3.to_hex(signed_trx2.rawTransaction)[2:]}
    trans = prodNode.pushMessage(defertestAcc.name, "pushdefer", json.dumps(actData), '-p {0}'.format(defertestAcc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    #Utils.Print(f"trans is {trans}");
    time.sleep(4) # sleep enough time to get soft fails.
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)
    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["nonce"] == 3) # rlptx2 is executed by onerror handler
    assert(row4["balance"] == "000000000000000000000000000000000000000000000000d4158798979be000")


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
    actData = {"recipient":defertestAcc.name, "account":evmAcc.name, "miner":minerAcc.name, "rlptx":Web3.to_hex(signed_trx.rawTransaction)[2:], "rlptx2":Web3.to_hex(signed_trx2.rawTransaction)[2:]}
    trans = prodNode.pushMessage(defertest2Acc.name, "notifytest", json.dumps(actData), '-p {0}'.format(defertest2Acc.name), silentErrors=False)
    prodNode.waitForTransBlockIfNeeded(trans[1], True)
    row4=prodNode.getTableRow(evmAcc.name, evmAcc.name, "account", 4) # 4th balance of this integration test
    Utils.Print("\taccount row4: ", row4)
    assert(row4["nonce"] == 5) 
    assert(row4["eth_address"] == "9e126c57330fa71556628e0aabd6b6b6783d99fa")
    assert(row4["balance"] == "00000000000000000000000000000000000000000000000080cfc165cc75a000")


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
    prodNode.transferFunds(cluster.eosioAccount, evmAcc, "111.0000 EOS", "0xB106D2C286183FFC3D1F0C4A6f0753bB20B407c2", waitForTransBlock=True)
    time.sleep(2)

    Utils.Print("Verify evm_version==1")
    # Verify header.nonce == 1 (evmversion=1)
    b = get_block("latest")
    assert(b["nonce"] == "0x0000000000000001")

    Utils.Print("Transfer funds to trigger evmtx event on contract")
    # Transfer funds (now using version=1)
    prodNode.transferFunds(cluster.eosioAccount, evmAcc, "111.0000 EOS", "0xB106D2C286183FFC3D1F0C4A6f0753bB20B407c2", waitForTransBlock=True)
    time.sleep(2)

    Utils.Print("Validate all balances (check evmtx event processing)")
    # Validate all balances (check evmtx event)
    validate_all_balances()

    foundErr = False
    stdErrFile = open(nodeStdErrDir, "r")
    lines = stdErrFile.readlines()
    for line in lines:
        if line.find("ERROR") != -1 or line.find("CRIT") != -1:
            Utils.Print("  Found ERROR in EOS EVM NODE log: ", line)
            foundErr = True

    stdErrFile = open(rpcStdErrDir, "r")
    lines = stdErrFile.readlines()
    for line in lines:
        if line.find("ERROR") != -1 or line.find("CRIT") != -1:
            Utils.Print("  Found ERROR in EOS EVM RPC log: ", line)
            foundErr = True

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
        

exitCode = 0 if testSuccessful else 1
exit(exitCode)
