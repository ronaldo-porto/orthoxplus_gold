/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Order.hpp"

//-------------------------------------------------------------------------

namespace taosim::event
{

//-------------------------------------------------------------------------

struct OrderEvent : public JsonSerializable
{
    OrderID id;
    Timestamp timestamp;
    taosim::decimal_t volume;
    taosim::decimal_t leverage;
    OrderDirection direction;
    STPFlag stpFlag;
    std::optional<taosim::decimal_t> price{};
    OrderContext ctx;
    std::optional<bool> postOnly{};
    std::optional<taosim::TimeInForce> timeInForce{};
    std::optional<std::optional<Timestamp>> expiryPeriod{};
    Currency currency{Currency::BASE};

    OrderEvent() noexcept = default;

    OrderEvent(Order::Ptr order, OrderContext ctx) noexcept
        : id{order->id()},
          timestamp{order->timestamp()},
          volume{order->volume()},
          leverage{order->leverage()},
          direction{order->direction()},
          stpFlag{order->stpFlag()},
          ctx{ctx}
    {
        if (auto limitOrder = std::dynamic_pointer_cast<LimitOrder>(order)) {
            price = limitOrder->price();
            postOnly = std::make_optional(limitOrder->postOnly());
            timeInForce = std::make_optional(limitOrder->timeInForce());
            expiryPeriod = std::make_optional(limitOrder->expiryPeriod());
        }
    }

    virtual void jsonSerialize(
        rapidjson::Document& json, const std::string& key = {}) const override;
};

//-------------------------------------------------------------------------

}  // namespace taosim::event

//-------------------------------------------------------------------------