/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include <benchmark/benchmark.h>
#include <fmt/format.h>

#include "MultiBookExchangeAgent.hpp"
#include "ParameterStorage.hpp"
#include "PayloadFactory.hpp"
#include "Simulation.hpp"
#include "server.hpp"
#include "util.hpp"

#include <bit>

//-------------------------------------------------------------------------

static const fs::path kTestDataPath{
    fs::path{__FILE__}.parent_path().parent_path() / "test" / "cpp-tests" / "data"};

static const fs::path kConfigPaths[]{kTestDataPath / "MultiAgentThreeBooksReplay.xml"};

static const fs::path kResponseFilePaths[]{
    kTestDataPath / "MultiAgentMoreFullBookWithCancels.orders0.json",
    kTestDataPath / "MultiAgentMoreFullBookWithCancels.orders1.json",
    kTestDataPath / "MultiAgentMoreFullBookWithCancels.cancels.json"};

template<typename... Args>
requires std::constructible_from<PlaceOrderMarketPayload, Args...>
std::pair<MarketOrder::Ptr, OrderErrorCode> placeMarketOrder(
    MultiBookExchangeAgent& exchange, AgentId agentId, BookId bookId, Args&&... args)
{
    auto& account = exchange.accounts()[agentId];
    const auto payload =
        MessagePayload::create<PlaceOrderMarketPayload>(std::forward<Args>(args)...);
    const auto ec = exchange.checkPlaceMarketOrder(account, bookId, payload);
    auto marketOrderPtr = exchange.books()[bookId].ptr->placeMarketOrder(
        payload->direction,
        Timestamp{},
        payload->volume,
        std::make_optional<OrderContext>(agentId, bookId));
    return {marketOrderPtr, ec};
}

template<typename... Args>
requires std::constructible_from<PlaceOrderLimitPayload, Args...>
std::pair<LimitOrder::Ptr, OrderErrorCode> placeLimitOrder(
    MultiBookExchangeAgent& exchange, AgentId agentId, BookId bookId, Args&&... args)
{
    auto& account = exchange.accounts()[agentId];
    const auto payload =
        MessagePayload::create<PlaceOrderLimitPayload>(std::forward<Args>(args)...);
    const auto ec = exchange.checkPlaceLimitOrder(account, bookId, payload);
    auto limitOrderPtr = exchange.books()[bookId].ptr->placeLimitOrder(
        payload->direction,
        Timestamp{},
        payload->volume,
        payload->price,
        std::make_optional<OrderContext>(agentId, bookId));
    return {limitOrderPtr, ec};
}

//-------------------------------------------------------------------------

struct RunFixture : benchmark::Fixture
{
    void SetUp(benchmark::State& state) override
    {
        const auto kStepSize = state.range(0);
        const auto& kConfigPath = kConfigPaths[state.range(1)];

        [[maybe_unused]] const auto [doc, simulationNode, exchangeNode] =
            util::parseSimulationFile(kConfigPath);

        auto params = std::make_shared<ParameterStorage>();
        params->set("step", std::to_string(kStepSize));
        simulation = std::make_unique<Simulation>(params);
        simulation->configure(simulationNode);

        exchange = std::make_unique<MultiBookExchangeAgent>(simulation.get());
        exchange->configure(exchangeNode);
    }

    std::unique_ptr<Simulation> simulation;
    std::unique_ptr<MultiBookExchangeAgent> exchange;
};

struct MemoryManager : benchmark::MemoryManager
{
    benchmark::MemoryManager::Result stats;

    void Start() override
    {
        stats.num_allocs = 0;
        stats.max_bytes_used = 0;
        stats.total_allocated_bytes = 0;
        stats.net_heap_growth = 0;
    }

    void Stop(benchmark::MemoryManager::Result& result) override { result = stats; }
};

static MemoryManager s_mngr;

void* operator new(size_t size)
{
    void* ptr = malloc(size);
    if (ptr == nullptr) {
        throw std::bad_alloc();
    }
    auto& [num_allocs, max_bytes_used, total_allocated_bytes, net_heap_growth] = s_mngr.stats;
    num_allocs++;
    total_allocated_bytes += size;
    net_heap_growth += size;
    max_bytes_used = std::min(max_bytes_used, net_heap_growth);
    return ptr;
}

void operator delete(void* ptr, size_t size)
{
    auto& [_1, max_bytes_used, _2, net_heap_growth] = s_mngr.stats;
    net_heap_growth -= static_cast<int64_t>(size);
    max_bytes_used = std::max(max_bytes_used, net_heap_growth);
    free(ptr);
}

//-------------------------------------------------------------------------

BENCHMARK_DEFINE_F(RunFixture, SimpleRun)(benchmark::State& state)
{
    auto placeOrders = [&](const Json::Value& orderPlacementResponsesJson) -> void {
        for (const auto& response : orderPlacementResponsesJson["responses"]) {
            const AgentId agentId = util::AgentIdFromJson(response["agentId"]);
            const auto genericPayload = PayloadFactory::createFromJsonMessage(response);

            if (response["type"] == "PLACE_ORDER_MARKET") {
                const auto payload =
                    std::dynamic_pointer_cast<PlaceOrderMarketPayload>(genericPayload);
                placeMarketOrder(
                    *exchange,
                    agentId,
                    //payload->bookId.value(),
                    payload->bookId,
                    payload->direction,
                    payload->currency,
                    payload->volume);
            }
            else if (response["type"] == "PLACE_ORDER_LIMIT") {
                const auto payload =
                    std::dynamic_pointer_cast<PlaceOrderLimitPayload>(genericPayload);
                placeLimitOrder(
                    *exchange,
                    agentId,
                    //payload->bookId.value(),
                    payload->bookId,
                    payload->direction,
                    payload->currency,
                    payload->volume,
                    payload->price);
            }
            else if (response["type"] == "CANCEL_ORDERS") {
                const auto payload = std::dynamic_pointer_cast<CancelOrdersPayload>(genericPayload);
                auto book = exchange->books()[payload->bookId].ptr;
                for (const auto& cancellation : payload->cancellations) {
                    book->cancelOrderOpt(cancellation.id, cancellation.volume);
                }
            }
        }
    };

    for (auto _ : state) {
        placeOrders(util::readJson(kResponseFilePaths[state.range(2)]));
        placeOrders(util::readJson(kResponseFilePaths[state.range(3)]));
        placeOrders(util::readJson(kResponseFilePaths[state.range(4)]));
    }
}
BENCHMARK_REGISTER_F(RunFixture, SimpleRun)->Args({10, 0, 0, 1, 2});

//-------------------------------------------------------------------------

int main(int argc, char* argv[])
{
    benchmark::RegisterMemoryManager(&s_mngr);
    benchmark::Initialize(&argc, argv);
    benchmark::RunSpecifiedBenchmarks();
    benchmark::RegisterMemoryManager(nullptr);
}

//-------------------------------------------------------------------------
