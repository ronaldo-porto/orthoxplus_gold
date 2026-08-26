/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "common.hpp"

//-------------------------------------------------------------------------

namespace taosim::simulation
{

//-------------------------------------------------------------------------

struct SimulationSignals
{
    UnsyncSignal<void()> start;
    UnsyncSignal<void()> step;
    UnsyncSignal<void()> stop;
    UnsyncSignal<void(Timespan)> time;
    UnsyncSignal<void(Timespan)> timeAboutToProgress;
    UnsyncSignal<void()> agentsCreated;
};

//-------------------------------------------------------------------------

}  // namespace taosim::simulation

//-------------------------------------------------------------------------
