/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "taosim/accounting/Collateral.hpp"
#include "formatting.hpp"

#include <gmock/gmock.h>
#include <gtest/gtest.h>

//-------------------------------------------------------------------------

using namespace taosim;
using namespace taosim::accounting;
using namespace taosim::literals;

using namespace testing;

//-------------------------------------------------------------------------

static constexpr uint32_t s_precision = 8;

struct CollateralTestParams
{
    decimal_t base;
    decimal_t quote;
    decimal_t price;
    decimal_t refValue;
};

void PrintTo(const CollateralTestParams& params, std::ostream* os)
{
    *os << fmt::format(
        "{{.base = {}, .quote = {}, .price = {}, .refValue = {}}}",
        params.base, params.quote, params.price, params.refValue);
}

//-------------------------------------------------------------------------

struct ValueInBaseTest : TestWithParam<CollateralTestParams>
{
    virtual void SetUp() override
    {
        const auto [base, quote, price, refValue] = GetParam();
        this->coll = Collateral({.base = base, .quote = quote});
        this->price = price;
        this->refValue = refValue;
    }

    Collateral coll;
    decimal_t price;
    decimal_t refValue;
};

TEST_P(ValueInBaseTest, WorksCorrectly)
{
    EXPECT_EQ(util::round(coll.valueInBase(price), s_precision), refValue);
}

INSTANTIATE_TEST_SUITE_P(
    CollateralTests,
    ValueInBaseTest,
    Values(
        CollateralTestParams{
            .base = 1_dec, .quote = 1_dec, .price = 2_dec, .refValue = DEC(1.5)
        },
        CollateralTestParams{
            .base = 5_dec, .quote = 2_dec, .price = 5_dec, .refValue = DEC(5.4)
        },
        CollateralTestParams{
            .base = 10_dec, .quote = 10_dec, .price = 10_dec, .refValue = DEC(11.0)
        },
        CollateralTestParams{
            .base = 420_dec, .quote = 10_dec, .price = DEC(0.1), .refValue = 520_dec
        }));

//-------------------------------------------------------------------------

struct ValueInQuoteTest : TestWithParam<CollateralTestParams>
{
    virtual void SetUp() override
    {
        const auto [base, quote, price, refValue] = GetParam();
        this->coll = Collateral({.base = base, .quote = quote});
        this->price = price;
        this->refValue = refValue;
    }

    Collateral coll;
    decimal_t price;
    decimal_t refValue;
};

TEST_P(ValueInQuoteTest, WorksCorrectly)
{
    EXPECT_EQ(util::round(coll.valueInQuote(price), s_precision), refValue);
}

INSTANTIATE_TEST_SUITE_P(
    CollateralTests,
    ValueInQuoteTest,
    Values(
        CollateralTestParams{
            .base = 0_dec, .quote = 3_dec, .price = 5_dec, .refValue = 3_dec
        },
        CollateralTestParams{
            .base = 2_dec, .quote = 2_dec, .price = 5_dec, .refValue = 12_dec
        },
        CollateralTestParams{
            .base = DEC(0.77), .quote = 0_dec, .price = 2_dec, .refValue = DEC(1.54)
        },
        CollateralTestParams{
            .base = DEC(1.337), .quote = DEC(3.22), .price = DEC(4.20), .refValue = DEC(8.8354)
        }));

//-------------------------------------------------------------------------
