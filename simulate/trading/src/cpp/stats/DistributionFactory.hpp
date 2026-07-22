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

struct DistributionFactory
{
    [[nodiscard]] static std::unique_ptr<Distribution> createFromXML(pugi::xml_node);
};

//-------------------------------------------------------------------------

}  // namespace taosim::stats

//-------------------------------------------------------------------------
