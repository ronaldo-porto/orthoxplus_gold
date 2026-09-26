# SPDX-License-Identifier: MIT
"""v6.4.1: pace each book's traded volume so the validator's volume cap lasts until the simulation ends.

Why (mainnet UID 94 on v6.4, read 09-27 04:00 JST):

* The validator (``taos/im/validator/query.py``) drops every non-cancel instruction on a book once the uid's traded
  volume there reaches ``capital_turnover_cap x miner_wealth`` = 10 x 50,000 = 500,000 quote.  The volume is summed over
  ``scoring.activity.trade_volume_assessment_period`` (86,400 sim-s) and a simulation lasts 86,400 sim-s, so nothing a
  simulation trades rolls off before it ends: the cap is a budget for the whole simulation.
* UID 94 had used ~293k quote per book by sim-s ~34,000 (6 books already capped), and v6.4 trades ~11.5 quote per sim-s
  per book: most books would reach the cap ~18,000 sim-s later (~27 wall-h), well before the simulation's end, and a
  capped book takes no order at all (not even the one that would reduce its inventory) until the next simulation.

The rule (one switch, ``research_v641_volume_pace``), all of it the validator's own arithmetic:

* Per book, the remaining allowance is the cap minus the volume the venue reports for the account (the same number the
  agent's own VOLUME_CAP guard reads), spread evenly over the remaining horizon: until the simulation ends, and never
  longer than the assessment period.  That is the book's paced rate (quote per sim-s).
* The book's actual rate is the change in that reported volume over the validator's sampling interval (600 sim-s).
* While the actual rate is above the paced rate, the book places only orders that reduce its inventory; the orders that
  would add are cancelled.  The paced rate is recomputed every state from what is left, so a book that spent fast is
  held back and a book that spent slowly is let go -- every book keeps trading until the simulation ends.
"""
from __future__ import annotations

from collections import deque
from typing import Any

V641_PACE_VERSION = "volume_pace_v6_4_1"

PACE_WINDOW_NS = 600_000_000_000           # scoring.activity.trade_volume_sampling_interval
ASSESSMENT_NS = 86_400_000_000_000         # scoring.activity.trade_volume_assessment_period
MIN_SPAN_NS = 60_000_000_000               # a rate is read only over at least a minute of samples
MIN_HORIZON_NS = 1_000_000_000
REBASE_MIN_JUMP_NS = 3_600_000_000_000
CANCEL_PACED = "V641_PACED"
SIDE_BUY = "buy"
SIDE_SELL = "sell"

_UNIT_NS = {"ns": 1, "us": 1_000, "ms": 1_000_000, "s": 1_000_000_000}


def duration_ns(config: Any) -> int | None:
    """The simulation's length in ns from the state's config (``duration`` in ``time_unit``), or None."""
    if config is None:
        return None
    try:
        d = int(getattr(config, "duration", None))
    except (TypeError, ValueError):
        return None
    unit = _UNIT_NS.get(str(getattr(config, "time_unit", "ns") or "ns").lower())
    if unit is None or d <= 0:
        return None
    return d * unit


def horizon_ns(now_ns: int, sim_duration_ns: int | None, *, assessment_ns: int = ASSESSMENT_NS) -> int:
    """How long the remaining allowance must last: to the simulation's end, never longer than the assessment period."""
    left = int(assessment_ns) if not sim_duration_ns else int(sim_duration_ns) - int(now_ns)
    return max(MIN_HORIZON_NS, min(int(assessment_ns), left))


def paced_rate(cap: float, used: float, horizon: int) -> float:
    """Quote per sim-s that spends what is left of the cap evenly over the horizon (0 when nothing is left)."""
    left = float(cap) - max(0.0, float(used))
    if left <= 0.0:
        return 0.0
    return left / (max(int(horizon), MIN_HORIZON_NS) / 1e9)


def adds(side: str, inventory: float, eps: float = 1e-9) -> bool:
    """True for a side whose fill would not reduce |inventory| (both sides of a flat book)."""
    inv = float(inventory)
    if side == SIDE_BUY:
        return inv >= -eps
    return inv <= eps


class VolumePace:
    """Every book's reported volume over the last sampling interval, and whether it runs ahead of its paced rate."""

    def __init__(self, *, window_ns: int = PACE_WINDOW_NS):
        self.window_ns = int(window_ns)
        self.rebases = 0
        self.reset()

    def reset(self) -> None:
        self.books: dict[int, deque] = {}
        self.last_ts: int | None = None
        self.paced_now: set[int] = set()

    def observe(self, book_id: int, ts: int, used: float) -> None:
        """One state's reported volume for one book (samples older than the window are dropped, one kept as anchor)."""
        ts = int(ts)
        if self.last_ts is not None and ts < self.last_ts - REBASE_MIN_JUMP_NS:
            rebases = self.rebases
            self.reset()
            self.rebases = rebases + 1
        self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)
        q = self.books.get(int(book_id))
        if q is None:
            q = deque()
            self.books[int(book_id)] = q
        if q and ts < q[-1][0]:
            q.clear()                                  # a clock rewind on this book: start its samples over
        q.append((ts, max(0.0, float(used))))
        while len(q) >= 2 and q[1][0] <= ts - self.window_ns:
            q.popleft()

    def rate(self, book_id: int) -> float | None:
        """Quote per sim-s over the samples held (None under MIN_SPAN_NS of history)."""
        q = self.books.get(int(book_id))
        if not q or len(q) < 2:
            return None
        (t0, u0), (t1, u1) = q[0], q[-1]
        span = t1 - t0
        if span < MIN_SPAN_NS:
            return None
        return max(0.0, u1 - u0) / (span / 1e9)

    def paced(self, book_id: int, now_ns: int, used: float, cap: float, sim_duration_ns: int | None) -> bool:
        """Is this book spending faster than its paced rate?  Always False without a cap or a rate yet."""
        b = int(book_id)
        paced = False
        if cap and float(cap) > 0.0:
            r = self.rate(b)
            if r is not None:
                paced = r > paced_rate(cap, used, horizon_ns(now_ns, sim_duration_ns))
        (self.paced_now.add(b) if paced else self.paced_now.discard(b))
        return paced

    def snapshot(self) -> dict[str, Any]:
        return {"version": V641_PACE_VERSION, "books": len(self.books), "paced_now": len(self.paced_now),
                "rebases": self.rebases}
