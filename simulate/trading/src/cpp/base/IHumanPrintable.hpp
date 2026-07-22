/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "IPrintable.hpp"

class IHumanPrintable : public virtual IPrintable
{
public:
    IHumanPrintable() = default;

    virtual void printHuman() const = 0;
    void print() const override { printHuman(); };
};
