/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "IMessageable.hpp"

#include "Simulation.hpp"

#include <fmt/format.h>

//-------------------------------------------------------------------------

void IMessageable::respondToMessage(
    Message::Ptr msg,
    const std::string& typePrefix,
    MessagePayload::Ptr payload,
    Timestamp processingDelay) const
{
    const Timestamp diff = msg->arrival - msg->occurrence;
    const Timestamp replyTime = msg->arrival + processingDelay;
    m_simulation->dispatchMessage(
        replyTime,
        diff,
        m_name,
        msg->source,
        fmt::format(
            "{}RESPONSE_{}", !typePrefix.empty() ? fmt::format("{}_", typePrefix) : "", msg->type),
        payload);
}

//-------------------------------------------------------------------------

void IMessageable::respondToMessage(
    Message::Ptr msg, MessagePayload::Ptr payload, Timestamp processingDelay) const
{
    respondToMessage(msg, "", payload, processingDelay);
}

//-------------------------------------------------------------------------

void IMessageable::fastRespondToMessage(
    Message::Ptr msg,
    const std::string& typePrefix,
    MessagePayload::Ptr payload,
    Timestamp processingDelay) const
{
    const Timestamp replyTime = msg->arrival + processingDelay;
    m_simulation->dispatchMessage(
        replyTime,
        0,
        m_name,
        msg->source,
        fmt::format(
            "{}RESPONSE_{}", !typePrefix.empty() ? fmt::format("{}_", typePrefix) : "", msg->type),
        payload);
}

//-------------------------------------------------------------------------

void IMessageable::fastRespondToMessage(
    Message::Ptr msg, MessagePayload::Ptr payload, Timestamp processingDelay) const
{
    fastRespondToMessage(msg, "", payload, processingDelay);
}

//-------------------------------------------------------------------------
