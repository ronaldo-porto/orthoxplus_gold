/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "IConfigurable.hpp"
#include "IMessageable.hpp"
#include "Timestamp.hpp"

#include <string>

//-------------------------------------------------------------------------

class Agent : public IConfigurable, public IMessageable
{
public:
    virtual ~Agent() = default;

    virtual void configure(const pugi::xml_node& node) override;

    [[nodiscard]] const std::string& type() const noexcept { return m_type; }

protected:
    Agent(Simulation* simulation, const std::string& name = {}) noexcept;
    
    std::string m_type;
};

//-------------------------------------------------------------------------
