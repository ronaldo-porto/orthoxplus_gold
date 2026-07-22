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

struct Cancellation : public JsonSerializable
{
    using Ptr = std::shared_ptr<Cancellation>;

    OrderID id;
    std::optional<taosim::decimal_t> volume;

    Cancellation() = default;

    Cancellation(OrderID id, std::optional<taosim::decimal_t> volume = {}) noexcept
        : id{id}, volume{volume}
    {}

    void L3Serialize(rapidjson::Document& json, const std::string& key = {}) const;

    virtual void jsonSerialize(
        rapidjson::Document& json, const std::string& key = {}) const override;

    [[nodiscard]] static Ptr fromJson(const rapidjson::Value& json);
};

//-------------------------------------------------------------------------

}  // namespace taosim::event

//-------------------------------------------------------------------------
