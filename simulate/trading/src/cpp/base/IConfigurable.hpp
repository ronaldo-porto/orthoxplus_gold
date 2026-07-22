/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <pugixml.hpp>

#include <cstdint>
#include <string>

//------------------------------------------------------------------------

using ConfigurationIndex = uint32_t;

//------------------------------------------------------------------------

class IConfigurable
{
public:
    virtual ~IConfigurable() = default;

    virtual void configure(const pugi::xml_node& node) = 0;

protected:
    IConfigurable() = default;
};

//------------------------------------------------------------------------
