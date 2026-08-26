/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <filesystem>

//-------------------------------------------------------------------------

namespace taosim::simulation
{

class ISimulation
{
public:
    virtual ~ISimulation() noexcept = default;

    virtual const std::filesystem::path& logDir() const noexcept = 0;
};

}  // namespace taosim::simulation

//-------------------------------------------------------------------------