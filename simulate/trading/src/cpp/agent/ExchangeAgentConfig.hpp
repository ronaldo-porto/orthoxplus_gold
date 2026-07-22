/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include <pugixml.hpp>

#include <cstdint>
#include <exception>
#include <source_location>
#include <string>

//-------------------------------------------------------------------------

namespace taosim::config
{

//-------------------------------------------------------------------------

class ExchangeAgentConfig
{
public:
    struct Parameters
    {
        ///###
        static inline constexpr uint32_t kMinimumPriceIncrementDecimals = 2;
        static inline constexpr uint32_t kMinimumVolumeIncrementDecimals = 2;

        uint32_t priceIncrementDecimals = kMinimumPriceIncrementDecimals;
        uint32_t volumeIncrementDecimals = kMinimumVolumeIncrementDecimals;
        uint32_t baseIncrementDecimals = kMinimumVolumeIncrementDecimals;
        uint32_t quoteIncrementDecimals = kMinimumPriceIncrementDecimals;
        // ...
    };

    ExchangeAgentConfig() noexcept = default;

    void configure(pugi::xml_node node);

    [[nodiscard]] const Parameters& parameters() const noexcept;

private:
    void setPriceIncrement(pugi::xml_node node);
    void setVolumeIncrement(pugi::xml_node node);
    void setBaseDecimals(pugi::xml_node node);
    void setQuoteDecimals(pugi::xml_node node);
    void handleException();

    Parameters m_parameters;
};

//-------------------------------------------------------------------------

class ExchangeAgentConfigException : public std::exception
{
public:
    explicit ExchangeAgentConfigException(std::string msg) noexcept;

    virtual const char* what() const noexcept override;

private:
    std::string m_msg;
};

//-------------------------------------------------------------------------

}  // namespace taosim::config

//-------------------------------------------------------------------------
