#!/usr/bin/env python3
import os
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import json

app = Flask(__name__)
CORS(app)
writemethods = {"eth_sendRawTransaction","eth_gasPrice"}
readEndpoint = "http://127.0.0.1:8881"
writeEndpoint = os.getenv("WRITE_RPC_ENDPOINT", "http://127.0.0.1:18888")
flaskListenPort = os.getenv("FLASK_SERVER_PORT", 5000)

@app.route("/", methods=["POST"])
def default():
    def forward_request(req):
        if type(req) == dict and ("method" in req) and (req["method"] in writemethods):
            print("send req to miner:" + str(req))
            resp = requests.post(writeEndpoint, json.dumps(req), headers={"Accept":"application/json","Content-Type":"application/json"}).json()
            print("got resp from miner:" + str(resp))
            return resp
        else:
            print("send req to eos-evm-rpc:" + str(req))
            resp = requests.post(readEndpoint, json.dumps(req), headers={"Accept":"application/json","Content-Type":"application/json"}).json()
            print("got from eos-evm-rpc:" + str(resp))
            return resp

    request_data = request.get_json()
    if type(request_data) == dict:
        return jsonify(forward_request(request_data))

    res = []
    for r in request_data:
        res.append(forward_request(r))

    return jsonify(res)

app.run(host='0.0.0.0', port=flaskListenPort)
