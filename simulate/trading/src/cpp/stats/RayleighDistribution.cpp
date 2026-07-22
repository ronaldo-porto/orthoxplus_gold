/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "RayleighDistribution.hpp"

#include <fmt/format.h>

#include <source_location>

//-------------------------------------------------------------------------

namespace taosim::stats
{

//-------------------------------------------------------------------------

RayleighDistribution::RayleighDistribution(double scale, double percentile)
    : m_samplingDistribution{0.0, percentile},
      m_distribution{scale}
{}

//-------------------------------------------------------------------------

double RayleighDistribution::sample(std::mt19937& rng) noexcept
{
    return quantile(m_samplingDistribution(rng));
}

//-------------------------------------------------------------------------

double RayleighDistribution::quantile(double p) noexcept
{
    return boost::math::quantile(m_distribution, p);
}

//-------------------------------------------------------------------------

std::unique_ptr<RayleighDistribution> RayleighDistribution::fromXML(pugi::xml_node node)
{
    static constexpr auto ctx = std::source_location::current().function_name();

    return std::make_unique<RayleighDistribution>(
        [&] {
            if (pugi::xml_attribute attr = node.attribute("scale")) {
                return attr.as_double();
            }
            throw std::invalid_argument{fmt::format(
                "{}: missing required attribute 'scale'", ctx)};
        }(), 1.0);
}

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------
