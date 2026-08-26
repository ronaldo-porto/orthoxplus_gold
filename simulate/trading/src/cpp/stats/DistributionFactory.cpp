/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "DistributionFactory.hpp"

#include "DiracDeltaDistribution.hpp"
#include "GammaDistribution.hpp"
#include "LognormalDistribution.hpp"
#include "RayleighDistribution.hpp"

#include <fmt/format.h>

#include <source_location>

//-------------------------------------------------------------------------

namespace taosim::stats
{

//-------------------------------------------------------------------------

std::unique_ptr<Distribution> DistributionFactory::createFromXML(pugi::xml_node node)
{
    std::string_view type = node.attribute("type").as_string();

    if (type == "dirac") {
        return DiracDeltaDistribution::fromXML(node);
    }
    else if (type == "gamma") {
        return GammaDistribution::fromXML(node);
    }
    else if (type == "lognormal") {
        return LognormalDistribution::fromXML(node);
    }
    else if (type == "rayleigh") {
        return RayleighDistribution::fromXML(node);
    }

    throw std::invalid_argument{fmt::format(
        "{}: Unknown distribution type '{}'",
        std::source_location::current().function_name(),
        type)};
}

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------
