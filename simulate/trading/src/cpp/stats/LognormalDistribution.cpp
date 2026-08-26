/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "LognormalDistribution.hpp"

#include <fmt/format.h>

#include <source_location>

//-------------------------------------------------------------------------

namespace taosim::stats
{

//-------------------------------------------------------------------------

LognormalDistribution::LognormalDistribution(double mu, double sigma)
    : m_samplingDistribution{mu, sigma},
      m_distribution{mu, sigma}
{}

//-------------------------------------------------------------------------

double LognormalDistribution::sample(std::mt19937& rng) noexcept
{
    return m_samplingDistribution(rng);
}

//-------------------------------------------------------------------------

double LognormalDistribution::quantile(double p) noexcept
{
    return boost::math::quantile(m_distribution, p);
}

//-------------------------------------------------------------------------

std::unique_ptr<LognormalDistribution> LognormalDistribution::fromXML(pugi::xml_node node)
{
    static constexpr auto ctx = std::source_location::current().function_name();

    return std::make_unique<LognormalDistribution>(
        [&] {
            if (pugi::xml_attribute attr = node.attribute("mu")) {
                return attr.as_double();
            }
            throw std::invalid_argument{fmt::format(
                "{}: missing required attribute 'mu'", ctx)};
        }(),
        [&] {
            if (pugi::xml_attribute attr = node.attribute("sigma")) {
                return attr.as_double();
            }
            throw std::invalid_argument{fmt::format(
                "{}: missing required attribute 'sigma'", ctx)};
        }());
}

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------
