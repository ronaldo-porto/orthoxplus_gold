# SPDX-License-Identifier: MIT
"""V4.11 full lifecycle entry-cost model."""
from __future__ import annotations

from dataclasses import dataclass
import math


def _finite(value: float, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


@dataclass(frozen=True)
class LifecycleCost:
    maker_entry_fee_bps: float
    expected_exit_fee_bps: float
    expected_cross_bps: float
    expected_slippage_bps: float
    holding_risk_bps: float
    taker_exit_probability: float

    @property
    def total_bps(self) -> float:
        return (
            self.maker_entry_fee_bps
            + self.expected_exit_fee_bps
            + self.expected_cross_bps
            + self.expected_slippage_bps
            + self.holding_risk_bps
        )

    def as_log(self) -> dict[str, float]:
        return {
            "lifecycle_entry_fee_bps": self.maker_entry_fee_bps,
            "lifecycle_exit_fee_bps": self.expected_exit_fee_bps,
            "lifecycle_cross_bps": self.expected_cross_bps,
            "lifecycle_slippage_bps": self.expected_slippage_bps,
            "lifecycle_holding_bps": self.holding_risk_bps,
            "lifecycle_taker_prob": self.taker_exit_probability,
            "lifecycle_cost_bps": self.total_bps,
        }


def lifecycle_entry_cost_bps(
    *,
    maker_fee_bps: float,
    taker_fee_bps: float,
    spread_bps: float,
    taker_exit_probability: float = 0.30,
    slippage_bps: float = 0.75,
    holding_risk_bps: float = 0.50,
) -> LifecycleCost:
    """Expected maker-entry + mixed maker/taker realization cost.

    Maker rebates are preserved. A taker exit pays its live fee and roughly half
    the spread plus slippage; a maker exit pays the live maker fee.  This is a
    bounded expectation, not a promise to cross the spread.
    """
    p = max(0.0, min(1.0, _finite(taker_exit_probability, 0.30)))
    maker = _finite(maker_fee_bps)
    taker = max(0.0, _finite(taker_fee_bps))
    spread = max(0.0, _finite(spread_bps))
    slip = max(0.0, _finite(slippage_bps))
    hold = max(0.0, _finite(holding_risk_bps))
    expected_exit_fee = (1.0 - p) * maker + p * taker
    expected_cross = p * 0.5 * spread
    expected_slip = p * slip
    return LifecycleCost(
        maker_entry_fee_bps=maker,
        expected_exit_fee_bps=expected_exit_fee,
        expected_cross_bps=expected_cross,
        expected_slippage_bps=expected_slip,
        holding_risk_bps=hold,
        taker_exit_probability=p,
    )
