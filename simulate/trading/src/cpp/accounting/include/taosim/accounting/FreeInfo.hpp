/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Order.hpp"
#include "common.hpp"

//-------------------------------------------------------------------------

namespace taosim::accounting
{

//-------------------------------------------------------------------------

enum class FreeStatus
{
    FREEABLE,
    NEGATIVE_AMOUNT,
    AMOUNT_EXCEEDS_RESERVATION,
    NONEXISTENT_RESERVATION,
    NONEXISTENT_RESERVATION_AND_AMOUNT,
    NONEXISTENT_RESERVATION_AND_NEGATIVE_AMOUNT
};

//-------------------------------------------------------------------------

struct FreeInfo
{
    OrderID orderId;
    std::optional<decimal_t> amount;
    std::optional<decimal_t> reservation;
    FreeStatus status;

    [[nodiscard]] std::string toString() const noexcept;
};

//-------------------------------------------------------------------------

class FreeException : public std::exception
{
public:
    explicit FreeException(std::string msg) noexcept;

    virtual const char* what() const noexcept override;

private:
    std::string m_msg;
};

//-------------------------------------------------------------------------

}  // namespace taosim::accounting

//-------------------------------------------------------------------------
