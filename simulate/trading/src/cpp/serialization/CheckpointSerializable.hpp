/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "json_util.hpp"

//-------------------------------------------------------------------------
// TODO: Make this a C++20 concept instead.

class CheckpointSerializable
{
public:
    virtual ~CheckpointSerializable() noexcept = default;

    virtual void checkpointSerialize(
        rapidjson::Document& json, const std::string& key = {}) const = 0;

protected:
    CheckpointSerializable() noexcept = default;
};

//-------------------------------------------------------------------------
