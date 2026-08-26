# SPDX-License-Identifier: MIT
"""Research ExitUrgency V2.

Per-book ExitUrgency ∈ [0, 1] from named pressure components.

High ExitUrgency is not an automatic taker. Urgency only says how hard
we seek realization (maker rungs). Hybrid economics decide whether we
cross.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_inventory_state import ofi_against_inventory, recent_realization_failure

EXIT_URGENCY_VERSION = "exit_urgency_v2"

EXIT_URGENCY_COMPONENTS = (
    "inventory_pressure",
    "inventory_age_pressure",
    "drawdown_pressure",
    "volatility_pressure",
    "adverse_flow_pressure",
    "markout_pressure",
    "kappa_pressure",
    "volume_cap_pressure",
    "realization_failure_pressure",
)

# Weights sum to 1.0. Each multiplies a [0, 1] component.
EXIT_URGENCY_WEIGHTS = {
    "inventory_pressure": 0.16,
    "inventory_age_pressure": 0.14,
    "drawdown_pressure": 0.18,
    "volatility_pressure": 0.08,
    "adverse_flow_pressure": 0.12,
    "markout_pressure": 0.12,
    "kappa_pressure": 0.06,
    "volume_cap_pressure": 0.08,
    "realization_failure_pressure": 0.06,
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value)))


def _tanh01(value: Any, scale: float) -> float:
    return math.tanh(max(0.0, _finite(value)) / max(1e-9, float(scale)))


def inventory_pressure(
    *,
    inventory_size: float,
    inventory_ratio: float,
    size_ref: float = 0.50,
) -> float:
    size_term = _tanh01(abs(_finite(inventory_size)), size_ref)
    ratio_term = _clip01(abs(_finite(inventory_ratio)))
    return _clip01(0.50 * size_term + 0.50 * ratio_term)


def inventory_age_pressure(inventory_age: float, age_ref: float = 20.0) -> float:
    return _tanh01(inventory_age, age_ref)


def drawdown_pressure(unrealized_pnl: float | None) -> float:
    return _tanh01(max(0.0, -_finite(unrealized_pnl)), 12.0)


def volatility_pressure(volatility: float) -> float:
    return _tanh01(volatility, 0.006)


def adverse_flow_pressure(
    *,
    ofi: float | None,
    inventory_sign: float,
    adverse_selection_risk: float,
) -> float:
    flow = _clip01(ofi_against_inventory(ofi, inventory_sign))
    toxic = _clip01(adverse_selection_risk)
    return _clip01(0.40 * flow + 0.60 * toxic)


def markout_pressure(expected_markout: float) -> float:
    return _tanh01(max(0.0, -_finite(expected_markout)), 8.0)


def kappa_pressure(kappa_need: float) -> float:
    return _clip01(kappa_need)


def volume_cap_pressure(volume_cap_headroom: float) -> float:
    return 1.0 - _clip01(volume_cap_headroom)


def realization_failure_pressure(
    recent_realized_pnl: float | None,
    realization_failed: bool | None = None,
) -> float:
    return recent_realization_failure(recent_realized_pnl, realization_failed)


@dataclass(frozen=True)
class ExitUrgencyBreakdown:
    inventory_pressure: float
    inventory_age_pressure: float
    drawdown_pressure: float
    volatility_pressure: float
    adverse_flow_pressure: float
    markout_pressure: float
    kappa_pressure: float
    volume_cap_pressure: float
    realization_failure_pressure: float
    urgency: float

    def as_log(self) -> dict[str, Any]:
        return {
            "exit_urgency": self.urgency,
            "exit_urgency_version": EXIT_URGENCY_VERSION,
            "inventory_pressure": self.inventory_pressure,
            "inventory_age_pressure": self.inventory_age_pressure,
            "drawdown_pressure": self.drawdown_pressure,
            "volatility_pressure": self.volatility_pressure,
            "adverse_flow_pressure": self.adverse_flow_pressure,
            "markout_pressure": self.markout_pressure,
            "kappa_pressure": self.kappa_pressure,
            "volume_cap_pressure": self.volume_cap_pressure,
            "realization_failure_pressure": self.realization_failure_pressure,
        }


def compute_exit_urgency_v2(
    *,
    inventory_size: float,
    inventory_ratio: float,
    inventory_age: float,
    unrealized_pnl: float | None,
    expected_markout: float,
    volatility: float,
    ofi: float | None = None,
    imbalance: float = 0.0,
    inventory_sign: float = 0.0,
    kappa_need: float = 0.0,
    volume_cap_headroom: float = 1.0,
    recent_realized_pnl: float | None = None,
    adverse_selection_risk: float = 0.0,
    realization_failed: bool | None = None,
    size_ref: float = 0.50,
    age_ref: float = 20.0,
) -> ExitUrgencyBreakdown:
    """Named-component ExitUrgency. ``imbalance`` is accepted and ignored."""
    del imbalance
    components = {
        "inventory_pressure": inventory_pressure(
            inventory_size=inventory_size,
            inventory_ratio=inventory_ratio,
            size_ref=size_ref,
        ),
        "inventory_age_pressure": inventory_age_pressure(inventory_age, age_ref),
        "drawdown_pressure": drawdown_pressure(unrealized_pnl),
        "volatility_pressure": volatility_pressure(volatility),
        "adverse_flow_pressure": adverse_flow_pressure(
            ofi=ofi,
            inventory_sign=inventory_sign,
            adverse_selection_risk=adverse_selection_risk,
        ),
        "markout_pressure": markout_pressure(expected_markout),
        "kappa_pressure": kappa_pressure(kappa_need),
        "volume_cap_pressure": volume_cap_pressure(volume_cap_headroom),
        "realization_failure_pressure": realization_failure_pressure(
            recent_realized_pnl, realization_failed,
        ),
    }
    urgency = 0.0
    for name in EXIT_URGENCY_COMPONENTS:
        urgency += EXIT_URGENCY_WEIGHTS[name] * components[name]
    return ExitUrgencyBreakdown(
        inventory_pressure=components["inventory_pressure"],
        inventory_age_pressure=components["inventory_age_pressure"],
        drawdown_pressure=components["drawdown_pressure"],
        volatility_pressure=components["volatility_pressure"],
        adverse_flow_pressure=components["adverse_flow_pressure"],
        markout_pressure=components["markout_pressure"],
        kappa_pressure=components["kappa_pressure"],
        volume_cap_pressure=components["volume_cap_pressure"],
        realization_failure_pressure=components["realization_failure_pressure"],
        urgency=_clip01(urgency),
    )


def compute_exit_urgency_v1(
    *,
    inventory_size: float = 0.0,
    inventory_ratio: float = 0.0,
    inventory_age: float = 0.0,
    unrealized_pnl: float | None = None,
    **_ignored: Any,
) -> ExitUrgencyBreakdown:
    """Legacy size/age/drawdown mix kept for A/B when V2 is off."""
    del inventory_size
    size = _clip01(abs(_finite(inventory_ratio)))
    age = _tanh01(inventory_age, 20.0)
    draw = _tanh01(max(0.0, -_finite(unrealized_pnl)), 12.0)
    urgency = _clip01(0.50 * size + 0.30 * age + 0.20 * draw)
    return ExitUrgencyBreakdown(
        inventory_pressure=size,
        inventory_age_pressure=age,
        drawdown_pressure=draw,
        volatility_pressure=0.0,
        adverse_flow_pressure=0.0,
        markout_pressure=0.0,
        kappa_pressure=0.0,
        volume_cap_pressure=0.0,
        realization_failure_pressure=0.0,
        urgency=urgency,
    )
