/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "taosim/accounting/Balance.hpp"

#include <gmock/gmock.h>
#include <gtest/gtest.h>

//-------------------------------------------------------------------------

using namespace taosim;
using namespace taosim::accounting;
using namespace taosim::literals;

using namespace testing;

//-------------------------------------------------------------------------

struct InitTest : TestWithParam<decimal_t>
{
    virtual void SetUp() override
    {
        initAmount = GetParam();
    }

    decimal_t initAmount;
};

TEST_P(InitTest, WorksCorrectly)
{
    if (initAmount < 0_dec) {
        EXPECT_THROW(Balance{initAmount}, std::invalid_argument);
        return;
    }
    const auto balance = Balance{initAmount};
    EXPECT_EQ(balance.getTotal(), initAmount);
    EXPECT_EQ(balance.getFree(), initAmount);
    EXPECT_EQ(balance.getReserved(), 0_dec);
}

INSTANTIATE_TEST_SUITE_P(
    BalanceTest, InitTest, Values(0_dec, 42_dec, -322_dec, 420_dec, -1337_dec, 9000_dec));

//-------------------------------------------------------------------------

struct ReserveTestParams
{
    decimal_t totalBalance;
    OrderID orderId{};
    decimal_t reservationAmount;
};

void PrintTo(const ReserveTestParams& params, std::ostream* os)
{
    *os << fmt::format(
        "{{.totalBalance = {}, .orderId = {}, .reservationAmount = {}}}",
        params.totalBalance,
        params.orderId,
        params.reservationAmount);
}

struct ReserveTest : TestWithParam<ReserveTestParams>
{
    virtual void SetUp() override
    {
        params = GetParam();
        balance = Balance{params.totalBalance};
    }

    ReserveTestParams params;
    Balance balance;
};

TEST_P(ReserveTest, WorksCorrectly)
{
    const auto [totalBalance, orderId, reservationAmount] = params;

    if (reservationAmount < 0_dec || reservationAmount > balance.getFree()) {
        EXPECT_FALSE(balance.canReserve(reservationAmount));
        EXPECT_THROW(balance.makeReservation(orderId, reservationAmount, 0), std::invalid_argument);
        return;
    }

    EXPECT_TRUE(balance.canReserve(reservationAmount));
    EXPECT_NO_THROW(balance.makeReservation(orderId, reservationAmount, 0));
    EXPECT_EQ(balance.getTotal(), totalBalance);
    EXPECT_EQ(balance.getFree(), totalBalance - reservationAmount);
    EXPECT_EQ(balance.getReserved(), reservationAmount);
}

INSTANTIATE_TEST_SUITE_P(
    BalanceTest,
    ReserveTest,
    Values(
        ReserveTestParams{.totalBalance = 100_dec, .reservationAmount = 42_dec},
        ReserveTestParams{.totalBalance = 500_dec, .reservationAmount = 322_dec},
        ReserveTestParams{.totalBalance = 1000_dec, .reservationAmount = 420_dec},
        ReserveTestParams{.totalBalance = 5000_dec, .reservationAmount = 1337_dec},
        ReserveTestParams{.totalBalance = 10'000_dec, .reservationAmount = 9000_dec},
        ReserveTestParams{.totalBalance = 100_dec, .reservationAmount = -42_dec},
        ReserveTestParams{.totalBalance = 300_dec, .reservationAmount = 322_dec},
        ReserveTestParams{.totalBalance = 500_dec, .reservationAmount = -420_dec},
        ReserveTestParams{.totalBalance = 8999_dec, .reservationAmount = 9000_dec}));

//-------------------------------------------------------------------------

struct FreeTestParams
{
    decimal_t totalBalance;
    OrderID orderId{};
    decimal_t reservationAmount;
    decimal_t freeAmount;
};

void PrintTo(const FreeTestParams& params, std::ostream* os)
{
    *os << fmt::format(
        "{{.totalBalance = {}, .orderId = {}, .reservationAmount = {}, .freeAmount = {}}}",
        params.totalBalance,
        params.orderId,
        params.reservationAmount,
        params.freeAmount);
}

struct FreeTest : TestWithParam<FreeTestParams>
{
    virtual void SetUp() override
    {
        params = GetParam();
        balance = Balance{params.totalBalance};
        balance.makeReservation(params.orderId, params.reservationAmount, 0);
    }

    FreeTestParams params;
    Balance balance;
};

TEST_P(FreeTest, WorksCorrectly)
{
    const auto [totalBalance, orderId, reservationAmount, freeAmount] = params;

    if (freeAmount < 0_dec) {
        EXPECT_EQ(balance.canFree(orderId, freeAmount).status, FreeStatus::NEGATIVE_AMOUNT);
        EXPECT_THROW(balance.freeReservation(orderId, 0, freeAmount), FreeException);
        EXPECT_EQ(balance.getTotal(), totalBalance);
        EXPECT_EQ(balance.getFree(), totalBalance - reservationAmount);
        EXPECT_EQ(balance.getReserved(), reservationAmount);
        EXPECT_THAT(balance.getReservation(orderId), Optional(reservationAmount));
    }
    else if (freeAmount > reservationAmount) {
        EXPECT_EQ(
            balance.canFree(orderId, freeAmount).status, FreeStatus::AMOUNT_EXCEEDS_RESERVATION);
        EXPECT_THROW(balance.freeReservation(orderId, 0, freeAmount), FreeException);
        EXPECT_EQ(balance.getTotal(), totalBalance);
        EXPECT_EQ(balance.getFree(), totalBalance - reservationAmount);
        EXPECT_EQ(balance.getReserved(), reservationAmount);
        EXPECT_THAT(balance.getReservation(orderId), Optional(reservationAmount));
    }
    else if (freeAmount == reservationAmount) {
        EXPECT_EQ(balance.canFree(orderId, freeAmount).status, FreeStatus::FREEABLE);
        EXPECT_NO_THROW(balance.freeReservation(orderId, 0, freeAmount));
        EXPECT_EQ(balance.getTotal(), totalBalance);
        EXPECT_EQ(balance.getFree(), totalBalance);
        EXPECT_EQ(balance.getReserved(), 0_dec);
        EXPECT_THAT(balance.getReservation(orderId), std::nullopt);
    }
    else {
        EXPECT_EQ(balance.canFree(orderId, freeAmount).status, FreeStatus::FREEABLE);
        EXPECT_NO_THROW(balance.freeReservation(orderId, 0, freeAmount));
        EXPECT_EQ(balance.getTotal(), totalBalance);
        EXPECT_EQ(balance.getFree(), totalBalance - reservationAmount + freeAmount);
        EXPECT_EQ(balance.getReserved(), reservationAmount - freeAmount);
        EXPECT_THAT(balance.getReservation(orderId), Optional(reservationAmount - freeAmount));
    }
}

INSTANTIATE_TEST_SUITE_P(
    BalanceTest,
    FreeTest,
    Values(
        FreeTestParams{
            .totalBalance = 100_dec,
            .reservationAmount = 50_dec,
            .freeAmount = 42_dec
        },
        FreeTestParams{
            .totalBalance = 500_dec,
            .reservationAmount = 350_dec,
            .freeAmount = 322_dec
        },
        FreeTestParams{
            .totalBalance = 1000_dec,
            .reservationAmount = 500_dec,
            .freeAmount = 500_dec
        },
        FreeTestParams{
            .totalBalance = 10'000_dec,
            .reservationAmount = 9000_dec,
            .freeAmount = 1000_dec
        },
        // FreeTestParams{
        //     .totalBalance = 100_dec,
        //     .reservationAmount = 50_dec,
        //     .freeAmount = 52_dec
        // },
        FreeTestParams{
            .totalBalance = 500_dec,
            .reservationAmount = 350_dec,
            .freeAmount = -400_dec
        },
        FreeTestParams{
            .totalBalance = 5_dec,
            .reservationAmount = DEC(2.92903307),
            .freeAmount = DEC(2.92903307)
        }));

//-------------------------------------------------------------------------

struct DepositTestParams
{
    decimal_t totalBalance;
    decimal_t depositAmount;
};

void PrintTo(const DepositTestParams& params, std::ostream* os)
{
    *os << fmt::format(
        "{{.totalBalance = {}, .depositAmount = {}}}",
        params.totalBalance,
        params.depositAmount);
}

struct DepositTest : TestWithParam<DepositTestParams>
{
    virtual void SetUp() override
    {
        params = GetParam();
        balance = Balance{params.totalBalance};
    }

    DepositTestParams params;
    Balance balance;
};

TEST_P(DepositTest, WorksCorrectly)
{
    const auto endAmount = params.totalBalance + params.depositAmount;

    if (endAmount < 0_dec) {
        EXPECT_THROW({
            balance.deposit(params.depositAmount, 0);
        }, std::runtime_error);
        return;
    }

    balance.deposit(params.depositAmount, 0);

    EXPECT_EQ(balance.getTotal(), endAmount);
    EXPECT_EQ(balance.getFree(), endAmount);
    EXPECT_EQ(balance.getReserved(), 0_dec);
}

INSTANTIATE_TEST_SUITE_P(
    BalanceTest,
    DepositTest,
    Values(
        DepositTestParams{.totalBalance = 0_dec, .depositAmount = 100_dec},
        DepositTestParams{.totalBalance = 100_dec, .depositAmount = 500_dec},
        DepositTestParams{.totalBalance = 1000_dec, .depositAmount = 2500_dec},
        DepositTestParams{.totalBalance = 0_dec, .depositAmount = -50_dec},
        DepositTestParams{.totalBalance = 10_dec, .depositAmount = -100_dec},
        DepositTestParams{.totalBalance = 100_dec, .depositAmount = -100_dec},
        DepositTestParams{.totalBalance = 1000_dec, .depositAmount = -1500_dec}));

//-------------------------------------------------------------------------

struct MoveTestParams
{
    decimal_t totalBalance;
    OrderID orderIdFirst{};
    decimal_t reservationAmountFirst;
    OrderID orderIdSecond{1};
    decimal_t reservationAmountSecond;
};

void PrintTo(const MoveTestParams& params, std::ostream* os)
{
    *os << fmt::format(
        "{{.totalBalance = {}, .orderIdFirst = {}, .reservationAmountFirst = {}, "
        ".orderIdSecond = {}, .reservationAmountSecond = {}}}",
        params.totalBalance,
        params.orderIdFirst,
        params.reservationAmountFirst,
        params.orderIdSecond,
        params.reservationAmountSecond);
}

struct MoveTest : TestWithParam<MoveTestParams>
{
    virtual void SetUp() override
    {
        params = GetParam();
        balance = Balance{params.totalBalance};
        balance.makeReservation(params.orderIdFirst, params.reservationAmountFirst, 0);
        balance.makeReservation(params.orderIdSecond, params.reservationAmountSecond, 0);
    }

    MoveTestParams params;
    Balance balance;
};

TEST_P(MoveTest, WorksCorrectly)
{
    const auto [
        totalBalance,
        orderIdFirst,
        reservationAmountFirst,
        orderIdSecond,
        reservationAmountSecond] = params;

    Balance movedBalance{std::move(balance)};

    EXPECT_EQ(balance.getTotal(), 0_dec);
    EXPECT_EQ(balance.getFree(), 0_dec);
    EXPECT_EQ(balance.getReserved(), 0_dec);
    EXPECT_THAT(balance.getReservation(orderIdFirst), Eq(std::nullopt));
    EXPECT_THAT(balance.getReservation(orderIdSecond), Eq(std::nullopt));

    EXPECT_EQ(movedBalance.getTotal(), totalBalance);
    EXPECT_EQ(movedBalance.getFree(), totalBalance - reservationAmountFirst - reservationAmountSecond);
    EXPECT_EQ(movedBalance.getReserved(), reservationAmountFirst + reservationAmountSecond);
    EXPECT_THAT(movedBalance.getReservation(orderIdFirst), Optional(reservationAmountFirst));
    EXPECT_THAT(movedBalance.getReservation(orderIdSecond), Optional(reservationAmountSecond));
}

INSTANTIATE_TEST_SUITE_P(
    BalanceTest,
    MoveTest,
    Values(
        MoveTestParams{
            .totalBalance = 100_dec,
            .reservationAmountFirst = 50_dec,
            .reservationAmountSecond = 42_dec
        },
        MoveTestParams{
            .totalBalance = 500_dec,
            .reservationAmountFirst = 350_dec,
            .reservationAmountSecond = 100_dec
        },
        MoveTestParams{
            .totalBalance = 1000_dec,
            .reservationAmountFirst = 500_dec,
            .reservationAmountSecond = 500_dec
        },
        MoveTestParams{
            .totalBalance = 10'000_dec,
            .reservationAmountFirst = 9000_dec,
            .reservationAmountSecond = 1000_dec
        }));

//-------------------------------------------------------------------------
