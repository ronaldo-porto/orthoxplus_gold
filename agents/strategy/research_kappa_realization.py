# SPDX-License-Identifier: MIT
"""Connect Kappa remaining to realization pressure.

Open inventory:

    1 observation remaining → strong realization boost
    2 remaining             → moderate boost
    already qualified       → normal economics

Kappa accelerates a profitable close. It must not accept a clearly
bad realization solely to complete an observation.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

KAPPA_REALIZATION_VERSION = "kappa_realization_v1"

MODE_ONE_AWAY = "ONE_AWAY"
MODE_TWO_AWAY = "TWO_AWAY"
MODE_QUALIFIED = "QUALIFIED"
MODE_UNCOVERED = "UNCOVERED"

REASON_ONE_AWAY = "KAPPA_ONE_AWAY_PROFIT"
REASON_TWO_AWAY = "KAPPA_TWO_AWAY_PROFIT"
REASON_QUALIFIED = "KAPPA_QUALIFIED_NORMAL"
REASON_UNCOVERED = "KAPPA_UNCOVERED_NORMAL"
REASON_BLOCKED_LOSS = "KAPPA_BLOCKED_LOSS"

ONE_AWAY_BOOST = 0.85
TWO_AWAY_BOOST = 0.40
QUALIFIED_BOOST = 0.0


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


def kappa_close_is_clearly_bad(
    unrealized_pnl_bps: float | None,
    *,
    crossing_cost_bps: float | None = None,
) -> bool:
    """True when realizing would lock a loss.

    Missing PnL is not treated as profit. When a crossing cost is
    supplied, a close that cannot cover that cost is also bad.
    """
    if unrealized_pnl_bps is None:
        return True
    upnl = _finite(unrealized_pnl_bps)
    if upnl <= 0.0:
        return True
    if crossing_cost_bps is None:
        return False
    return upnl - max(0.0, _finite(crossing_cost_bps)) <= 0.0


@dataclass(frozen=True)
class KappaRealizationBoost:
    observations_remaining: int
    eligible: bool
    profitable: bool
    clearly_bad: bool
    boost: float
    taker_boost: float
    mode: str
    reason: str

    @property
    def allow_pressure(self) -> bool:
        return self.boost > 0.0 and not self.clearly_bad

    def as_log(self) -> dict[str, Any]:
        return {
            "kappa_realization_version": KAPPA_REALIZATION_VERSION,
            "kappa_remaining": self.observations_remaining,
            "kappa_eligible": int(bool(self.eligible)),
            "kappa_profitable": int(bool(self.profitable)),
            "kappa_clearly_bad": int(bool(self.clearly_bad)),
            "kappa_boost": self.boost,
            "kappa_taker_boost": self.taker_boost,
            "kappa_realization_mode": self.mode,
            "kappa_realization_reason": self.reason,
        }


def kappa_realization_boost(
    *,
    observations_remaining: int,
    unrealized_pnl_bps: float | None,
    eligible: bool | None = None,
    crossing_cost_bps: float | None = None,
) -> KappaRealizationBoost:
    remaining = max(0, int(observations_remaining or 0))
    is_eligible = bool(eligible) if eligible is not None else remaining <= 0
    maker_bad = kappa_close_is_clearly_bad(unrealized_pnl_bps)
    taker_bad = kappa_close_is_clearly_bad(
        unrealized_pnl_bps, crossing_cost_bps=crossing_cost_bps,
    )
    profitable = not maker_bad

    if is_eligible or remaining <= 0:
        mode = MODE_QUALIFIED
        reason = REASON_QUALIFIED
        boost = QUALIFIED_BOOST
    elif remaining >= 3:
        mode = MODE_UNCOVERED
        reason = REASON_UNCOVERED
        boost = QUALIFIED_BOOST
    elif remaining == 1:
        mode = MODE_ONE_AWAY
        boost = ONE_AWAY_BOOST if profitable else QUALIFIED_BOOST
        reason = REASON_ONE_AWAY if profitable else REASON_BLOCKED_LOSS
    elif remaining == 2:
        mode = MODE_TWO_AWAY
        boost = TWO_AWAY_BOOST if profitable else QUALIFIED_BOOST
        reason = REASON_TWO_AWAY if profitable else REASON_BLOCKED_LOSS
    else:
        mode = MODE_UNCOVERED
        reason = REASON_UNCOVERED
        boost = QUALIFIED_BOOST

    taker_boost = 0.0 if taker_bad else boost
    return KappaRealizationBoost(
        observations_remaining=remaining,
        eligible=is_eligible or remaining <= 0,
        profitable=profitable,
        clearly_bad=maker_bad,
        boost=_clip01(boost),
        taker_boost=_clip01(taker_boost),
        mode=mode,
        reason=reason,
    )
