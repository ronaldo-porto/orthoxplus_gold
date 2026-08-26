/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "taosim/decimal/decimal.hpp"

//-------------------------------------------------------------------------

namespace taosim
{

inline void PrintTo(const decimal_t& val, std::ostream* os)
{
    *os << fmt::format("{}", val);
}

}  // namespace taosim

//-------------------------------------------------------------------------
