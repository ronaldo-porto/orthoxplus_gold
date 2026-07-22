/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <random>

//-------------------------------------------------------------------------

namespace taosim::stats
{

//-------------------------------------------------------------------------

struct Distribution
{
    virtual ~Distribution() noexcept = default;

    [[nodiscard]] virtual double sample(std::mt19937& rng) noexcept = 0;
    [[nodiscard]] virtual double quantile(double p) noexcept = 0;
};

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------
