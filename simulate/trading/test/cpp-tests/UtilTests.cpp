/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "util.hpp"

#include <gmock/gmock.h>
#include <gtest/gtest.h>

#include <iostream>

//-------------------------------------------------------------------------

using namespace testing;

//-------------------------------------------------------------------------

TEST(UtilTest, Split)
{
    static constexpr std::string_view kTestStr1{"foo|bar|baz"};
    static constexpr char delim = '|';

    EXPECT_THAT(taosim::util::split(kTestStr1, delim), ElementsAre("foo", "bar", "baz"));

    static constexpr std::string_view kTestStr2{"foo,bar,baz"};

    EXPECT_THAT(taosim::util::split(kTestStr2, delim), ElementsAre(kTestStr2));
}

//-------------------------------------------------------------------------

TEST(UtilTest, CaptureOutput)
{
    auto printer = [](std::string_view str) -> void { std::cout << str; };

    static constexpr std::string_view kTestStr1{"foo"};
    static constexpr std::string_view kTestStr2{"bar baz"};
    const auto defaultBuffer = std::cout.rdbuf();

    EXPECT_THAT(taosim::util::captureOutput(printer, kTestStr1), StrEq(kTestStr1));
    EXPECT_THAT(taosim::util::captureOutput(printer, kTestStr2), StrEq(kTestStr2));
    EXPECT_THAT(std::cout.rdbuf(), Eq(defaultBuffer));
}

//-------------------------------------------------------------------------
