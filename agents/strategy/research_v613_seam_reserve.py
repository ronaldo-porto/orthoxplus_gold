# SPDX-License-Identifier: MIT
"""v6.13: the seam reserve -- in a simulation's last RESERVE_HORIZON_S a book trades no faster than the volume cap's
sustainable rate, so the busiest books start the next simulation with room instead of at the cap.

Why (mainnet, sim 20260929_2015, measured 10-03 ~22:00 JST; scratchpad k13/ fills.py, seam2.py, ratecap3.py):

* The validator's volume cap is a rolling 86,400-sim-s window that continues across a seam (``trade.py``
  ``shift_simulation_histories`` re-keys each entry by age), so what a book trades in this simulation's last hours is
  still on its cap in the next simulation's first hours.
* Our per-book window volume rebuilt from our own fills matches the validator's gauge within +-8%; at sim 56,676 the
  five held UIDs had 193 books at 450k or more of the 500k cap.  At the v6.12 per-book rates, the next simulation's
  first 30,000 sim-s would be 22% of book-time dark and 39% of our volume demand unfilled (UID 94 33% dark, 104 26%).
* A per-book ceiling at the sustainable rate from sim ~62,000: this simulation -15.9% volume, the next simulation's
  first 30,000 sim-s +20.6% (regained / given 0.95) -- a ~1:1 swap of volume from a thin trend's last hours into the
  next simulation's opening hours, which paid 1.0x (S2, a burst) to 3.9x (S1, active-volatile) of today's making
  share per unit of volume.

The rule (one switch, ``research_v613_seam_reserve``):

* When the state's simulation time is within RESERVE_HORIZON_S of the simulation's end (the config's duration), each
  book's own new volume -- our fills in the prints of every state since the reserve began, or since this agent began
  if that is later -- may not exceed ``rate x elapsed + rate x one sampling interval``, where ``rate`` is the venue's
  cap over the assessment period.  A book over it is held: its orders are cancelled and nothing is placed on either
  side (a hard ceiling; the v6.4.1 / v6.6 / v6.8 pacers only stop orders that add to a position, so the reducing side
  and the kept deepest level go on filling and the busiest books still reach the cap).
* A new simulation (the clock runs back) clears the reserve; outside the horizon nothing is held.
* STRUCTURAL: the rate (cap / assessment period) and the burst (one sampling interval) are the validator's own
  arithmetic.  OBSERVED: RESERVE_HORIZON_S 30,000 -- the span of the next simulation over which the carried volume
  darkens our books in the projection.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from research_v641_pace import ASSESSMENT_NS, PACE_WINDOW_NS, REBASE_MIN_JUMP_NS

V613_SEAM_RESERVE_VERSION = "seam_reserve_v6_13"

RESERVE_HORIZON_S = 30_000          # OBSERVED: the next simulation's span that the carried volume darkens


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def in_horizon(now_ns: Any, duration_ns: Any, *, horizon_s: Any = RESERVE_HORIZON_S) -> bool:
    """Is the state within ``horizon_s`` of the simulation's end?  An unread clock or duration is never."""
    now, dur, h = _finite(now_ns), _finite(duration_ns), _finite(horizon_s)
    if now is None or dur is None or h is None or dur <= 0.0 or h <= 0.0 or now < 0.0:
        return False
    return dur - now <= h * 1e9


def allowance(cap: Any, elapsed_ns: Any, *, assessment_ns: int = ASSESSMENT_NS, window_ns: int = PACE_WINDOW_NS) -> float | None:
    """The volume a book may trade ``elapsed_ns`` after the reserve began: the cap's sustainable rate over the elapsed
    time plus one sampling interval of it as the burst.  None without a cap."""
    c, e = _finite(cap), _finite(elapsed_ns)
    if c is None or e is None or c <= 0.0:
        return None
    rate = c / (float(assessment_ns) / 1e9)
    return rate * (max(0.0, e) + float(window_ns)) / 1e9


def fill_notional(fills: Iterable[Any]) -> float:
    """Price x quantity over (side, price, quantity) fills; unreadable rows count nothing."""
    total = 0.0
    for row in fills or ():
        try:
            _side, p, q = row
            v = float(p) * float(q)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v) and v > 0.0:
            total += v
    return total


class SeamReserve:
    """Each book's own volume since the reserve began this simulation, and whether it is over the allowance."""

    def __init__(self, *, horizon_s: int = RESERVE_HORIZON_S) -> None:
        self.horizon_s = int(horizon_s)
        self.start_ns: int | None = None
        self.spent: dict[int, float] = {}
        self.last_ts: int | None = None
        self.rebases = 0
        self.held_now: set[int] = set()

    def observe(self, book_id: int, now_ns: int, fills: Iterable[Any], duration_ns: Any) -> None:
        """One state's fills of ours on one book."""
        now = int(now_ns)
        if self.last_ts is not None and now < self.last_ts - REBASE_MIN_JUMP_NS:
            # a new simulation: its clock starts again, and so does every book's reserve
            self.start_ns, self.spent, self.held_now = None, {}, set()
            self.rebases += 1
            self.last_ts = now
        self.last_ts = now if self.last_ts is None else max(self.last_ts, now)
        if not in_horizon(now, duration_ns, horizon_s=self.horizon_s):
            return
        if self.start_ns is None:
            self.start_ns = now
        b = int(book_id)
        self.spent[b] = self.spent.get(b, 0.0) + fill_notional(fills)

    def held(self, book_id: int, now_ns: int, cap: Any, duration_ns: Any) -> bool:
        """Is the book over its allowance (within the horizon, with a cap)?"""
        b, now = int(book_id), int(now_ns)
        if self.start_ns is None or not in_horizon(now, duration_ns, horizon_s=self.horizon_s):
            self.held_now.discard(b)
            return False
        allowed = allowance(cap, now - self.start_ns)
        over = allowed is not None and self.spent.get(b, 0.0) > allowed
        if over:
            self.held_now.add(b)
        else:
            self.held_now.discard(b)
        return over

    def snapshot(self) -> dict[str, Any]:
        spent = sorted(self.spent.values())
        return {
            "version": V613_SEAM_RESERVE_VERSION, "horizon_s": self.horizon_s, "start_ns": self.start_ns,
            "books": len(self.spent), "held_now": len(self.held_now), "rebases": self.rebases,
            "spent_p50": (round(spent[len(spent) // 2], 1) if spent else None),
            "spent_max": (round(spent[-1], 1) if spent else None),
        }
