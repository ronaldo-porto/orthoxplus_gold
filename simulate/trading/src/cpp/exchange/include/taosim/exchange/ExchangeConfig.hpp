/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <pugixml.hpp>

#include <taosim/accounting/common.hpp>
#include <taosim/decimal/decimal.hpp>

#include <source_location>

//-------------------------------------------------------------------------

namespace taosim::exchange
{

struct ExchangeConfig
{
    uint32_t priceDecimals;
    uint32_t volumeDecimals;
    uint32_t baseDecimals;
    uint32_t quoteDecimals;
    decimal_t maxLeverage;
    decimal_t maxLoan;
    decimal_t maintenanceMargin;
    decimal_t initialPrice;
    size_t maxOpenOrders;
    decimal_t minOrderSize;
};

[[nodiscard]] ExchangeConfig makeExchangeConfig(pugi::xml_node node);

}  // namespace taosim::exchange

//-------------------------------------------------------------------------