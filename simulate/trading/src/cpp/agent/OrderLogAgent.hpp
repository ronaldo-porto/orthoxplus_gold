/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include "Agent.hpp"

class OrderLogAgent : public Agent
{
public:
    OrderLogAgent(Simulation* simulation);
    OrderLogAgent(Simulation* simulation, const std::string& name);

    void configure(const pugi::xml_node& node);

    // Inherited via Agent
    void receiveMessage(Message::Ptr msg) override;

private:
    std::string m_exchange;
};
