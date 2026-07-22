/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <stdexcept>

//-------------------------------------------------------------------------

namespace taosim::simulation
{

//-------------------------------------------------------------------------

class SimulationException : public std::runtime_error
{
public:
    SimulationException(const std::string& message) : std::runtime_error(message) {}
    SimulationException(const SimulationException& exception) = default;
    SimulationException(SimulationException&& exception) = default;
};

//-------------------------------------------------------------------------

}  // namespace taosim::simulation

//-------------------------------------------------------------------------
