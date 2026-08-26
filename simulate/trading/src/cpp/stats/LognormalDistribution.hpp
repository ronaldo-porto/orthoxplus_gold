/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Distribution.hpp"

#include <boost/math/distributions/lognormal.hpp>
#include <pugixml.hpp>

#include <memory>
#include <random>

//-------------------------------------------------------------------------

namespace taosim::stats
{

//-------------------------------------------------------------------------

class LognormalDistribution : public Distribution
{
public:
    LognormalDistribution(double mu, double sigma);

    virtual double sample(std::mt19937& rng) noexcept override;
    virtual double quantile(double p) noexcept override;

    [[nodiscard]] static std::unique_ptr<LognormalDistribution> fromXML(pugi::xml_node node);

private:
    std::lognormal_distribution<double> m_samplingDistribution;
    boost::math::lognormal_distribution<double> m_distribution;
};

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------
