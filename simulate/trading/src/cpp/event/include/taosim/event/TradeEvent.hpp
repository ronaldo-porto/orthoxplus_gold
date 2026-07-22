/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Trade.hpp"

//-------------------------------------------------------------------------

namespace taosim::event
{

struct TradeEvent : public JsonSerializable
{
    Trade::Ptr trade;
    TradeContext ctx;

    TradeEvent() noexcept = default;

    TradeEvent(Trade::Ptr trade, TradeContext ctx) noexcept : trade{trade}, ctx{ctx} {}

    virtual void jsonSerialize(
        rapidjson::Document& json, const std::string& key = {}) const override;
};

}  // namespace taosim::event

//-------------------------------------------------------------------------
