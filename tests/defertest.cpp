#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <type_traits>
#include <tuple>
#include <eosio/eosio.hpp>
#include <eosio/asset.hpp>
#include <eosio/contract.hpp>
#include <eosio/system.hpp>
#include <eosio/transaction.hpp>
#include <eosio/serialize.hpp>
#include <eosio/print.hpp>
#include <eosio/name.hpp>

using namespace eosio;
using namespace std;

extern "C" {
   __attribute__((eosio_wasm_import))
   uint64_t current_time();
}

class [[eosio::contract("defertest")]] defertest : public eosio::contract {

public:
    using contract::contract;

    int32_t now() {
        return current_time() / 1000000;
    }

    struct [[eosio::table]] pending {
        uint64_t            id;
        eosio::name         account;
        eosio::name         miner;
        std::vector<char>   rlptx;

        uint64_t primary_key() const { return id; }
    };
    typedef eosio::multi_index<"pending"_n, pending>  pending_table_t;

    // notify by the contract account of contract "defertest2.cpp"
    [[eosio::on_notify("*::notifytest")]] void notifytest(eosio::name recipient, eosio::name account, eosio::name miner, const std::vector<char> &rlptx, const std::vector<char> &rlptx2) {
        action act({_self, "active"_n}, account, "pushtx"_n, 
        std::tuple<eosio::name, std::vector<char> >(miner, rlptx2));
        act.send();
    }

    [[eosio::action]] void pushtxinline(eosio::name account, eosio::name miner, const std::vector<char> &rlptx) {
        action act({_self, "active"_n}, account, "pushtx"_n, 
        std::tuple<eosio::name, std::vector<char> >(miner, rlptx));
        act.send();
    }

    [[eosio::action]] void pushdefer(uint64_t id, eosio::name account, eosio::name miner, const std::vector<char> &rlptx, const std::vector<char> &rlptx2) {

        printf("enter defertest.cpp::pushdefer:%d %d", (int)rlptx.size(), (int)rlptx2.size());

        action act(permission_level{_self, "active"_n}, account, "pushtx"_n, std::tuple<eosio::name, std::vector<char> >(miner, rlptx));
        transaction txn(time_point_sec(current_time() + 3590));
        txn.actions.push_back(act);
        auto serialize = pack(txn);
        ::send_deferred((uint128_t)(id), _self, serialize.data(), serialize.size(), true);

        if (rlptx2.size()) {
            pending_table_t pending_table(_self, _self.value);
            pending_table.emplace(_self, [&](auto &v) {
                v.id = id;
                v.account = account;
                v.miner = miner;
                v.rlptx = rlptx2;
            });
        }
    }

    [[eosio::on_notify("eosio::onerror")]] void onerror() {
        
        pending_table_t pending_table(_self, _self.value);
        if (pending_table.begin() != pending_table.end()) {

            printf("defertest.cpp::onerror() 1");
            auto itr = pending_table.end();
            --itr;
            action act(permission_level{_self, "active"_n}, itr->account, "pushtx"_n, 
            std::tuple<eosio::name, std::vector<char> >(itr->miner, itr->rlptx));
            act.send();
            pending_table.erase(itr);
        }
        else {
            printf("defertest.cpp::onerror() 2");
            check(false, "hard-fail");
        }
    }
};
