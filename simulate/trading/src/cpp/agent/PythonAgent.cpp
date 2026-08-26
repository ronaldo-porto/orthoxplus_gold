/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#include "PythonAgent.hpp"

#include "MultiBookExchangeAgent.hpp"
#include "Simulation.hpp"

#include <pybind11/stl.h>

#include <iostream>

//------------------------------------------------------------------------

PythonAgent::PythonAgent(
    Simulation* simulation, const std::string& pythonClass, const fs::path& file)
    : Agent{simulation}, m_class{pythonClass}, m_file{file}
{}

//------------------------------------------------------------------------

PythonAgent::PythonAgent(Simulation* simulation, const std::string& name)
    : Agent{simulation, name}
{}

//------------------------------------------------------------------------

void PythonAgent::configure(const pugi::xml_node& node)
{
    Agent::configure(node);

    for (const pugi::xml_attribute& attr : node.attributes()) {
        const std::string name{attr.name()};
        if (name != "file") {
            m_parameters[name] = attr.as_string();
        }
    }

    py::object agentClass;
    if (m_file.empty()) {
        py::module m = py::module::import(m_class.c_str());
        agentClass = m.attr(m_class.c_str());
    }
    else {
        py::object result = py::eval_file(m_file.c_str());
        agentClass = result.attr(m_class.c_str());
    }

    m_instance = agentClass();
    py::cpp_function nameStringFunction = [this] { return name(); };
    m_instance.attr("name") = nameStringFunction;

    py::function fun = py::reinterpret_borrow<py::function>(m_instance.attr("configure"));
    py::object _ret = fun(simulation(), m_parameters);
}

//------------------------------------------------------------------------

void PythonAgent::receiveMessage(Message::Ptr msg)
{
    py::function receiveMessageFunction =
        py::reinterpret_borrow<py::function>(m_instance.attr("receiveMessage"));
    py::object _ret = receiveMessageFunction(simulation(), msg->type, msg->payload);
}

//------------------------------------------------------------------------
