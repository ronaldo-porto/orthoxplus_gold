/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Distribution.hpp"

#include <boost/math/distributions/rayleigh.hpp>
#include <pugixml.hpp>

#include <memory>
#include <random>

//-------------------------------------------------------------------------

namespace taosim::stats
{

//-------------------------------------------------------------------------

class RayleighDistribution : public Distribution
{
public:
    RayleighDistribution(double scale, double percentile = 1.0);

    virtual double sample(std::mt19937& rng) noexcept override;
    virtual double quantile(double p) noexcept override;

    [[nodiscard]] static std::unique_ptr<RayleighDistribution> fromXML(pugi::xml_node node);

private:
    std::uniform_real_distribution<double> m_samplingDistribution;
    boost::math::rayleigh_distribution<double> m_distribution;
};

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------
