/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "DiracDeltaDistribution.hpp"

#include <fmt/format.h>

#include <source_location>

//-------------------------------------------------------------------------

namespace taosim::stats
{

//-------------------------------------------------------------------------

std::unique_ptr<DiracDeltaDistribution> DiracDeltaDistribution::fromXML(pugi::xml_node node)
{
    static constexpr auto ctx = std::source_location::current().function_name();

    return std::make_unique<DiracDeltaDistribution>(
        [&] {
            static constexpr const char* name = "loc";
            if (pugi::xml_attribute attr = node.attribute(name)) {
                return attr.as_double();
            }
            throw std::invalid_argument{fmt::format(
                "{}: missing required attribute '{}'", ctx, name)};
        }());
}

//-------------------------------------------------------------------------

double DiracDeltaDistribution::sample(std::mt19937& rng) noexcept
{
    rng.discard(1);
    return m_loc;
}

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------