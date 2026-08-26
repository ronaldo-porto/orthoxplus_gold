/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Timestamp.hpp"
#include "common.hpp"

#include <pugixml.hpp>

#include <cstdint>
#include <filesystem>
#include <functional>
#include <iostream>
#include <ranges>
#include <sstream>
#include <string>
#include <string_view>
#include <type_traits>

//-------------------------------------------------------------------------

namespace taosim::util
{

//-------------------------------------------------------------------------

template<typename... Args>
[[nodiscard]] std::string captureOutput(std::invocable<Args...> auto fn, Args&&... args) noexcept
{
    std::streambuf* coutBuffer = std::cout.rdbuf();
    std::stringstream sstream;
    std::cout.rdbuf(sstream.rdbuf());
    fn(std::forward<Args>(args)...);
    std::cout.rdbuf(coutBuffer);
    return sstream.str();
}

[[nodiscard]] std::vector<std::string> split(std::string_view str, char delim) noexcept;

std::vector<std::string> getLastLines(const std::string& filename, int lineCount) noexcept;
double getClosestPreviousEntry(const std::string& filename, long long currentTimestamp);

struct Nodes
{
    pugi::xml_document doc;
    pugi::xml_node simulation;
    pugi::xml_node exchange;
};

[[nodiscard]] Nodes parseSimulationFile(const fs::path& path);

//-------------------------------------------------------------------------

}  // namespace util

//-------------------------------------------------------------------------
