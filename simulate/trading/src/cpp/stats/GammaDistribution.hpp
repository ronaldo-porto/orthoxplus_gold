/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Distribution.hpp"

#include <boost/math/distributions/gamma.hpp>
#include <pugixml.hpp>

#include <memory>
#include <random>

//-------------------------------------------------------------------------

namespace taosim::stats
{

//-------------------------------------------------------------------------

class GammaDistribution : public Distribution
{
public:
    GammaDistribution(double shape, double scale);

    virtual double sample(std::mt19937& rng) noexcept override;
    virtual double quantile(double p) noexcept override;

    [[nodiscard]] static std::unique_ptr<GammaDistribution> fromXML(pugi::xml_node node);

private:
    std::gamma_distribution<double> m_samplingDistribution;
    boost::math::gamma_distribution<double> m_distribution{1.0};
};

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------