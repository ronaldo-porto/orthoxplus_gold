/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Distribution.hpp"

#include <pugixml.hpp>

#include <memory>

//-------------------------------------------------------------------------

namespace taosim::stats
{

//-------------------------------------------------------------------------

class DiracDeltaDistribution : public Distribution
{
public:
    explicit DiracDeltaDistribution(double loc) noexcept : m_loc{loc} {}

    virtual double sample(std::mt19937& rng) noexcept override;
    virtual double quantile([[maybe_unused]] double p) noexcept override { return m_loc; }

    [[nodiscard]] static std::unique_ptr<DiracDeltaDistribution> fromXML(pugi::xml_node node);

private:
    double m_loc;
};

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------