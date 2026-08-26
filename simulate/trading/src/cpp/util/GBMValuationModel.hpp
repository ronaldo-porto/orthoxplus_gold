/*
 * SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
 * SPDX-License-Identifier: MIT
 */
#pragma once

#include "Timestamp.hpp"

#include <cmath>
#include <random>
#include <valarray>

//-------------------------------------------------------------------------

template<std::floating_point T = double>
class GBMValuationModel
{
public:
    GBMValuationModel(T S0, T mu, T sigma, uint64_t seed) noexcept
        : m_S0{S0}, m_mu{mu}, m_sigma{sigma}, m_rng{seed}
    {}

    [[nodiscard]] std::valarray<T> generatePriceSeries(Timestamp capT, uint32_t N)
    {
        auto linspace = [](Timestamp start, Timestamp end, uint32_t num) {
            std::valarray<T> t(num);
            const T dt = static_cast<T>(end - start) / (num - 1);
            for (uint32_t i = 0; i < num; ++i) {
                t[i] = i * dt;
            }
            t += start;
            return t;
        };
        const std::valarray<T> t = linspace(0, capT, N + 1);
        const std::valarray<T> W = generateTrajectory(capT, N);
        return m_S0 * std::exp((m_mu - T{0.5} * m_sigma * m_sigma) * t + m_sigma * W);
    }

private:
    [[nodiscard]] std::valarray<T> generateTrajectory(Timestamp capT, uint32_t N)
    {
        const T dt = static_cast<T>(capT) / N;
        std::valarray<T> W = [&] {
            std::normal_distribution<T> normal{{}, std::sqrt(dt)};
            std::valarray<T> dW(N);
            for (uint32_t i = 0; i < N; ++i) {
                dW[i] = normal(m_rng);
            }
            std::valarray<T> W(N + 1);
            W[0] = {};
            std::partial_sum(std::begin(dW), std::end(dW), std::begin(W) + 1);
            return W;
        }();
        return W;
    }

    T m_S0, m_mu, m_sigma;
    std::mt19937 m_rng;
};

//-------------------------------------------------------------------------
