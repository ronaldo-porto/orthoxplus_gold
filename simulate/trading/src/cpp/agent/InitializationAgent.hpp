/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Agent.hpp"

#include <random>

//-------------------------------------------------------------------------

class InitializationAgent : public Agent
{
public:
    InitializationAgent(Simulation* simulation) noexcept;

    virtual void configure(const pugi::xml_node& node) override;
    virtual void receiveMessage(Message::Ptr msg) override;
    
    void handleLimitOrderPlacementResponse(Message::Ptr msg);

private:
    void placeBuyOrders();
    void placeSellOrders();

    std::mt19937* m_rng;
    std::string m_exchange;
    uint32_t m_bookCount;
    double m_price;
    Timestamp m_tau;
    double m_priceIncrement;
    double m_volumeIncrement;
};

//-------------------------------------------------------------------------
