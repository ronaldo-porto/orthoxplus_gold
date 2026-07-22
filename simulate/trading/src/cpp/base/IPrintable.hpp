/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

class IPrintable
{
public:
    virtual void print() const = 0;

protected:
    IPrintable() = default;
};
