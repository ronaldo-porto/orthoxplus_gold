# SPDX-License-Identifier: MIT
"""A1.9.5 step 4: put the breadth authority where the block actually is.

A1.9.3 attached breadth authority to the ``NEGATIVE_CURRENT_EDGE`` reject in
the scoring pass.  Two independent runs now show it fires zero times:

    log 20260911_224141 (9,738 ticks)   A193 admits 0   denies 0
    log 20260912_121832 (4,233 ticks)   A193 admits 0   denies 0

The reason is visible in the same logs.  Of 1,320 one-away RANK rows,
**100% were already `eligible=True` with `reject_reason=None`** -- these books
are not being rejected for negative edge, because their edge is positive.  The
override guards a condition breadth-critical books do not meet.

The real block is one stage later, at the A1.7.4.5 quiet-entry gate:

    A1745_ENTRY_BLOCK_LOW_EDGE   1,021 total, 77 books
      of which on one-away books   545, across 30 of the 50 one-away books
      their edge_bps: median +8.40, min +2.52, max +14.93

    a1745_base_min_edge_bps        2.5
    a1745_effective_min_edge_bps  15.0     <- QUIET_ZERO_REBATE_LOW_TRADE_WIDE_SPREAD

So a book one fill from qualifying, showing a healthy +8.4 bps maker edge, is
refused because a regime conjunction raised the bar six-fold.  That is the
mechanism by which qualification stalls below the 80-book target while the
agent reports every breadth-critical book as eligible.

WHAT THIS RELIEF IS, AND IS NOT.  It restores the *base* floor for a one-away
book -- the threshold the strategy already considers economic in every other
regime -- and never goes below it.  A book with edge under the base floor stays
blocked, and a book with negative edge is never touched: paying to trade for
score is how you buy breadth by destroying kappa, which is the trade A1.9.2 and
A1.9.4 exist to prevent.  The relief is bounded per tick and drawn from the
same breadth budget A1.9.3 already computes from the score deficit, so it
cannot exceed the number of books the agent could actually hold.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

A195_BREADTH_LANE_VERSION = "direct_breadth_lane_v4_16_2_a1_9_5"

# Books relieved in one tick.  Breadth is a flow, not a burst: granting many at
# once converts the whole deficit into simultaneous inventory.
A195_MAX_BREADTH_RELIEF_PER_TICK = 2

# Ticks a book waits before it may be relieved again, so one stubborn book
# cannot consume the budget every tick.
A195_BREADTH_RELIEF_COOLDOWN_TICKS = 50

RELIEF_GRANTED = "GRANTED"
DENY_DISABLED = "DISABLED"
DENY_NOT_ONE_AWAY = "NOT_ONE_AWAY"
DENY_GATE_INACTIVE = "GATE_INACTIVE"
DENY_EDGE_BELOW_BASE = "EDGE_BELOW_BASE"
DENY_NO_DEFICIT = "NO_DEFICIT"
DENY_TICK_BUDGET = "TICK_BUDGET"
DENY_COOLDOWN = "COOLDOWN"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


@dataclass(frozen=True)
class BreadthRelief:
    """One relief decision at the A1.7.4.5 boundary."""

    allow: bool
    reason: str
    base_min_edge_bps: float
    effective_min_edge_bps: float
    relieved_min_edge_bps: float
    current_edge_bps: float
    observations_remaining: int

    @property
    def relief_bps(self) -> float:
        return max(0.0, self.effective_min_edge_bps - self.relieved_min_edge_bps)

    def as_log(self) -> dict[str, Any]:
        return {
            "a195_breadth_lane_version": A195_BREADTH_LANE_VERSION,
            "allow": int(self.allow),
            "reason": self.reason,
            "base_min_edge_bps": self.base_min_edge_bps,
            "effective_min_edge_bps": self.effective_min_edge_bps,
            "relieved_min_edge_bps": self.relieved_min_edge_bps,
            "relief_bps": self.relief_bps,
            "current_edge_bps": self.current_edge_bps,
            "observations_remaining": self.observations_remaining,
        }


def evaluate_breadth_relief(
    *,
    enabled: bool,
    observations_remaining: Any,
    current_edge_bps: Any,
    base_min_edge_bps: Any,
    effective_min_edge_bps: Any,
    score_deficit: Any,
    granted_this_tick: Any,
    ticks_since_last_relief: Any = None,
    max_per_tick: int = A195_MAX_BREADTH_RELIEF_PER_TICK,
    cooldown_ticks: int = A195_BREADTH_RELIEF_COOLDOWN_TICKS,
) -> BreadthRelief:
    """Decide whether a one-away book may use the base entry floor.

    ``ticks_since_last_relief`` is ``None`` for a book never relieved.
    """
    base = _finite(base_min_edge_bps)
    effective = _finite(effective_min_edge_bps)
    edge = _finite(current_edge_bps)
    remaining = int(_finite(observations_remaining, 3))

    def deny(reason: str) -> BreadthRelief:
        return BreadthRelief(
            allow=False, reason=reason, base_min_edge_bps=base,
            effective_min_edge_bps=effective, relieved_min_edge_bps=effective,
            current_edge_bps=edge, observations_remaining=remaining,
        )

    if not enabled:
        return deny(DENY_DISABLED)
    # Only one-away books.  Two or more observations out, a single round trip
    # does not change qualification, so relief would buy volume, not breadth.
    if remaining != 1:
        return deny(DENY_NOT_ONE_AWAY)
    # Nothing to relieve unless the regime gate actually raised the bar.
    if effective <= base:
        return deny(DENY_GATE_INACTIVE)
    # The hard line: the base floor still applies.  A book that cannot clear
    # the threshold the strategy calls economic everywhere else is not a
    # breadth opportunity, it is a loss with a score attached.
    if edge + 1e-12 < base or edge <= 0.0:
        return deny(DENY_EDGE_BELOW_BASE)
    if int(_finite(score_deficit, 0)) <= 0:
        return deny(DENY_NO_DEFICIT)
    if int(_finite(granted_this_tick, 0)) >= max(0, int(max_per_tick)):
        return deny(DENY_TICK_BUDGET)
    if ticks_since_last_relief is not None:
        if int(_finite(ticks_since_last_relief, 0)) < max(0, int(cooldown_ticks)):
            return deny(DENY_COOLDOWN)
    return BreadthRelief(
        allow=True, reason=RELIEF_GRANTED, base_min_edge_bps=base,
        effective_min_edge_bps=effective, relieved_min_edge_bps=base,
        current_edge_bps=edge, observations_remaining=remaining,
    )
