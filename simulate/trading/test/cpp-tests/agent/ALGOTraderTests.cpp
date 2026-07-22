/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "ALGOTraderAgent.hpp"

#include <gmock/gmock.h>
#include <gtest/gtest.h>

//-------------------------------------------------------------------------

using taosim::decimal_t;
using namespace taosim::agent;
using namespace testing;

//-------------------------------------------------------------------------

using VolumeStatsFixture =
    TestWithParam<std::tuple<size_t, std::vector<TimestampedVolume>, decimal_t>>;

struct VolumeStatsTest : public VolumeStatsFixture
{
    virtual void SetUp() override
    {
        const auto& [period, timestampedVolumes, referenceSum] = GetParam();
        volumeStats = ALGOTraderVolumeStats{period};
        for (const auto& item : timestampedVolumes) {
            volumeStats.push(item);
        }
        this->referenceSum = referenceSum;
    }

    ALGOTraderVolumeStats volumeStats;
    decimal_t referenceSum;
};

INSTANTIATE_TEST_SUITE_P(
    ALGOTraderTest,
    VolumeStatsTest,
    Values(
        std::tuple{
            5,
            std::vector<TimestampedVolume>{{.timestamp = 0, .volume = 1_dec}},
            1_dec},
        std::tuple{
            5,
            std::vector<TimestampedVolume>{
                {.timestamp = 0, .volume = 1_dec},
                {.timestamp = 0, .volume = DEC(2.5)},
                {.timestamp = 4, .volume = DEC(3.75)},
                {.timestamp = 5, .volume = 10_dec}},
            DEC(13.75)},
        std::tuple{
            10,
            std::vector<TimestampedVolume>{
                {.timestamp = 0, .volume = 1_dec},
                {.timestamp = 0, .volume = DEC(2.5)},
                {.timestamp = 4, .volume = DEC(3.75)},
                {.timestamp = 5, .volume = 10_dec},
                {.timestamp = 10, .volume = DEC(4.2)},
                {.timestamp = 15, .volume = 20_dec},
                {.timestamp = 18, .volume = 2_dec},},
            DEC(26.2)}));

TEST_P(VolumeStatsTest, WorksCorrectly)
{
    EXPECT_EQ(volumeStats.rollingSum(), referenceSum);
}

//-------------------------------------------------------------------------

TEST(ALGOTraderTest, ThrowsCorrectly)
{
    EXPECT_THROW(ALGOTraderVolumeStats{0}, std::invalid_argument);
}

//-------------------------------------------------------------------------
