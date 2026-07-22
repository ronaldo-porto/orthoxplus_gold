/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "common.hpp"

#include <cstdint>
#include <map>
#include <random>
#include <string>

//-------------------------------------------------------------------------

class LocalAgentRoster
{
public:
    explicit LocalAgentRoster(std::map<std::string, uint32_t> baseNamesToCounts) noexcept;

    [[nodiscard]] uint32_t at(const std::string& baseName) const;

    [[nodiscard]] uint32_t totalCount() const noexcept { return m_totalCount; }
    [[nodiscard]] const auto& baseNamesToCounts() const noexcept { return m_baseNamesToCounts; }

private:
    uint32_t m_totalCount;
    std::map<std::string, uint32_t> m_baseNamesToCounts;
};

//-------------------------------------------------------------------------