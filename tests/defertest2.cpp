#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <type_traits>
#include <tuple>
#include <eosio/eosio.hpp>
#include <eosio/contract.hpp>
#include <eosio/system.hpp>
#include <eosio/transaction.hpp>
#include <eosio/serialize.hpp>
#include <eosio/print.hpp>
#include <eosio/name.hpp>

using namespace eosio;
using namespace std;

class [[eosio::contract("defertest2")]] defertest2 : public eosio::contract {

public:
    using contract::contract;

    // recipient is the contract account of contract "defertest.cpp"
    [[eosio::action]] void notifytest(eosio::name recipient, eosio::name account, eosio::name miner, const std::vector<char> &rlptx, const std::vector<char> &rlptx2) {

        action act({_self, "active"_n}, recipient, "pushtxinline"_n, 
        std::tuple<eosio::name, eosio::name, std::vector<char> >(account, miner, rlptx));
        act.send();

        require_recipient(recipient);
    }

};
