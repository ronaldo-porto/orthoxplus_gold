/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

//-------------------------------------------------------------------------

class CSVPrintable
{
public:
    virtual ~CSVPrintable() noexcept = default;

    virtual void printCSV() const = 0;

protected:
    CSVPrintable() noexcept = default;
};

//-------------------------------------------------------------------------