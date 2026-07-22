/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Agent.hpp"
#include "util.hpp"

#include <pybind11/embed.h>
namespace py = pybind11;

#include <map>

//------------------------------------------------------------------------

class PythonAgent : public Agent
{
public:
    PythonAgent(Simulation* simulation, const std::string& pythonClass, const fs::path& file);
    PythonAgent(Simulation* simulation, const std::string& name);

    void configure(const pugi::xml_node& node);

    // Inherited via Agent
    void receiveMessage(Message::Ptr msg) override;

private:
    std::string m_class;
    fs::path m_file;
    std::map<std::string, std::string> m_parameters;

    py::object m_instance;
};

//------------------------------------------------------------------------
