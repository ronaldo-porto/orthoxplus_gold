# SPDX-License-Identifier: MIT
"""V4.16 entry ExecutionController: Maker / Taker / Skip.

A candidate that is SAFE and has LifecycleEV >= 0 is executable. This module
only chooses how. Taker is a normal option; it does not require a risk state.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_action_utility import kappa_completion_value

EXECUTION_CONTROLLER_VERSION = "execution_controller_v4_16_0"

ACTION_MAKER = "MAKER"
ACTION_TAKER = "TAKER"
ACTION_SKIP = "SKIP"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value)))


@dataclass(frozen=True)
class ExecutionDecision:
    action: str
    maker_utility: float
    taker_utility: float
    skip_utility: float
    maker_size: float
    taker_size: float
    reason: str

    def as_log(self) -> dict[str, Any]:
        return {
            "execution_controller_version": EXECUTION_CONTROLLER_VERSION,
            "selected_action": self.action,
            "maker_utility": self.maker_utility,
            "taker_utility": self.taker_utility,
            "skip_utility": self.skip_utility,
            "maker_size": self.maker_size,
            "taker_size": self.taker_size,
            "reason": self.reason,
        }


def maker_utility(
    *,
    lifecycle_ev: float,
    p_fill: float,
    spread_capture_bps: float = 0.0,
    score_value: float = 0.0,
    capital_efficiency: float = 0.0,
) -> float:
    p = _clip01(p_fill)
    edge = _finite(lifecycle_ev) + 0.02 * math.tanh(_finite(spread_capture_bps) / 8.0)
    return p * edge + 0.15 * max(0.0, _finite(score_value)) + 0.10 * _clip01(capital_efficiency)


def taker_utility(
    *,
    lifecycle_ev: float,
    crossing_cost: float,
    observations_remaining: int = 3,
    required_observations: int = 3,
    expiry_urgency: float = 0.0,
    capital_release: float = 0.0,
) -> float:
    """Immediate execution EV. Crossing is a cost, not a veto."""
    kappa = kappa_completion_value(observations_remaining, required_observations)
    immediate = _finite(lifecycle_ev) - max(0.0, _finite(crossing_cost))
    return (
        immediate
        + 0.25 * kappa
        + 0.20 * _clip01(expiry_urgency)
        + 0.15 * _clip01(capital_release)
    )


def choose_execution(
    *,
    lifecycle_ev: float,
    p_fill: float = 0.50,
    spread_capture_bps: float = 0.0,
    score_value: float = 0.0,
    capital_efficiency: float = 0.0,
    crossing_cost: float = 0.0,
    observations_remaining: int = 3,
    required_observations: int = 3,
    expiry_urgency: float = 0.0,
    capital_release: float = 0.0,
    maker_size: float = 0.25,
    taker_clip: float = 0.25,
    skip_utility: float = 0.0,
) -> ExecutionDecision:
    maker_u = maker_utility(
        lifecycle_ev=lifecycle_ev,
        p_fill=p_fill,
        spread_capture_bps=spread_capture_bps,
        score_value=score_value,
        capital_efficiency=capital_efficiency,
    )
    taker_u = taker_utility(
        lifecycle_ev=lifecycle_ev,
        crossing_cost=crossing_cost,
        observations_remaining=observations_remaining,
        required_observations=required_observations,
        expiry_urgency=expiry_urgency,
        capital_release=capital_release,
    )
    skip_u = _finite(skip_utility)
    if maker_u > taker_u and maker_u > skip_u and maker_u > 0.0:
        return ExecutionDecision(
            ACTION_MAKER, maker_u, taker_u, skip_u,
            float(maker_size), 0.0, "MAKER_UTILITY",
        )
    if taker_u > maker_u and taker_u > skip_u and taker_u > 0.0:
        return ExecutionDecision(
            ACTION_TAKER, maker_u, taker_u, skip_u,
            0.0, float(taker_clip), "TAKER_UTILITY",
        )
    return ExecutionDecision(
        ACTION_SKIP, maker_u, taker_u, skip_u, 0.0, 0.0, "SKIP_UTILITY",
    )
