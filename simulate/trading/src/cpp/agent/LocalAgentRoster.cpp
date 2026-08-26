/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "LocalAgentRoster.hpp"

//-------------------------------------------------------------------------

LocalAgentRoster::LocalAgentRoster(std::map<std::string, uint32_t> baseNamesToCounts) noexcept
    : m_baseNamesToCounts{std::move(baseNamesToCounts)}
{
    m_totalCount = ranges::accumulate(baseNamesToCounts | views::values, decltype(m_totalCount){});
}

//-------------------------------------------------------------------------

uint32_t LocalAgentRoster::at(const std::string& baseName) const
{
    return m_baseNamesToCounts.at(baseName);
}

//-------------------------------------------------------------------------
