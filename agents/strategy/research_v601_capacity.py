# SPDX-License-Identifier: MIT
"""v6.0.1: the dust recovery reserve is held only for dust the normalizer can work.

Measured on UID 67 (v6.0.0, testnet, ticks 0 to 3,500, 2026-09-17):

* ``admission_slots`` withholds one active slot, one open book and one clip of BASE whenever the
  dust count is above zero.  The reserve has one consumer, the dust normalizer, and
  ``normalization_allowed`` refuses every position of half a minimum order or more.
* v6.0.0 counts a parked inherited lot as dust.  Five parked lots (1.26 BASE, about one lot each)
  held the reserve in 100% of 142 admission rows; in 25 of 40 state rows they were the only dust.
  Zero entry slots in 71% of rows; the same rows without the reserve give 3%.

**Workable dust** is a book that counts as dust, is not a parked inherited lot, and is under half
the minimum order: exactly the set the normalizer accepts.  The reserve counts only those books.
Every other use of the dust count is unchanged.

Also here, telemetry only: the validator's live 0.6.1 blend, so the agent's own score estimate
is on the scale it is paid on.

Pure functions only; the strategy owns the state.
"""
from __future__ import annotations

import math
from typing import Any

V601_CAPACITY_VERSION = "workable_dust_reserve_v6_0_1"
V601_STATE_EVERY_TICKS = 100

# The normalizer's own bound (research_direct_liveness.normalization_allowed).
V601_WORKABLE_FRACTION = 0.5

# taos 0.6.1 live scoring: trading = kappa_w * kappa + pnl_w * pnl + debeta_w * de-beta.
LIVE_BLEND_WEIGHTS: dict[str, float] = {"kappa": 0.5925, "pnl": 0.1575, "debeta": 0.25}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def is_workable_dust(qty: Any, *, is_dust: bool, parked: bool, min_order: Any) -> bool:
    """Dust the normalizer may bring up to a lot: dust, not parked, under half a lot."""
    if not is_dust or parked:
        return False
    m = abs(_finite(min_order))
    if m <= 0.0:
        return False
    return abs(_finite(qty)) + 1e-12 < V601_WORKABLE_FRACTION * m


def reserve_dust_count(*, enabled: bool, dust_count: Any, workable_count: Any) -> int:
    """The dust count the admission reserve uses.  Off is v6.0.0: every dust book."""
    try:
        dust = max(0, int(dust_count))
    except (TypeError, ValueError):
        dust = 0
    if not enabled:
        return dust
    try:
        workable = max(0, int(workable_count))
    except (TypeError, ValueError):
        return dust
    # Workable dust is a subset of dust; never report more than the whole.
    return min(dust, workable)


def live_trading_ex_debeta(kappa_score: Any, pnl_score: Any) -> float | None:
    """The live trading score without its de-beta leg, clamped like the validator's blend."""
    k = _finite(kappa_score, float("nan"))
    p = _finite(pnl_score, float("nan"))
    if not (math.isfinite(k) and math.isfinite(p)):
        return None
    value = LIVE_BLEND_WEIGHTS["kappa"] * k + LIVE_BLEND_WEIGHTS["pnl"] * p
    return max(0.0, min(1.0, value))
