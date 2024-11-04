#include <vector>
#include <variant>
#include <eosio/eosio.hpp>
#include <eosio/contract.hpp>

using namespace eosio;
using namespace std;

extern "C" {
__attribute__((eosio_wasm_import))
void set_action_return_value(void*, size_t);

__attribute__((eosio_wasm_import))
uint64_t get_sender();
}

typedef std::vector<char> bytes;

struct bridge_message_v0 {
   name       receiver;
   bytes      sender;
   time_point timestamp;
   bytes      value;
   bytes      data;

   EOSLIB_SERIALIZE(bridge_message_v0, (receiver)(sender)(timestamp)(value)(data));
};

using bridge_message = std::variant<bridge_message_v0>;

class [[eosio::contract("evmbridge")]] evmbridge : public contract {
public:
    using contract::contract;
    [[eosio::action]] void onbridgemsg(const bridge_message& message);


    class contract_actions {
        public:
        void call(eosio::name from, const bytes &to, const bytes& value, const bytes &data, uint64_t gas_limit);
        void assertnonce(eosio::name account, uint64_t next_nonce);
    };

    using call_action = action_wrapper<"call"_n, &contract_actions::call>;
};

void evmbridge::onbridgemsg(const bridge_message& message) {
    const bridge_message_v0 &msg = std::get<bridge_message_v0>(message);
    const char method[4] = {'\x00','\x8f','\xcf','\x3e'};  // function assertdata(uint256)

    uint8_t value_buffer[32] = {};
    value_buffer[31] = 42; // big endian

    bytes call_data;
    call_data.reserve(4 + 32);
    call_data.insert(call_data.end(), method, method + 4);
    call_data.insert(call_data.end(), value_buffer, value_buffer + 32);

    call_action call_act("eosio.evm"_n, {{get_self(), "active"_n}});

    bytes value_zero;
    value_zero.resize(32, 0);

    call_act.send(get_self() /*from*/, msg.sender /*to*/, value_zero /*value*/, call_data /*data*/, 100000 /*gas_limit*/);
}

