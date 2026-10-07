# SPDX-License-Identifier: MIT
"""v6.19 S1: a cap-bound book's leading side stops adding -- research_v619_surplus_hold.

Why (mainnet 10-07 JST; scratchpad k28/: replays vm628 / vm628b with the validator's arithmetic and the true pay rule,
score S = 0.5 making share + 0.5 alpha share against the field, every arm against the live v6.18 build on the same tape):

* Since v6.18 every aged UID trades at its cap: the venue's rolling volume sits at 93-98% of the cap, the volume per
  making window fell to the sustainable rate and the making per unit of volume decides the making share.
* The validator pays 2 x min(buy capture, sell capture) per book and making window.  On a book whose buy capture in the
  window already exceeds its sell capture another buy adds no making -- it only spends cap room that a sell could use.
* Replays (cap-bound = room below one making window of sustainable volume):
    aged UID, the current simulation's cap-bound hours (sim 12,688-19,351)   +40.1% (4 of 4 windows ahead)
    the previous simulation, whole (7 full validator windows)                 +23.8% (7 of 7)
    a fresh UID with or without launch mode                                    0.0% (the cap never binds: never fires)
    the same rule without the cap condition, on a fresh UID                   -7.3% (blocks) / -11.0% (validator windows)
    without the window-maturity condition, the first making window of a
      simulation (the opening burst: a near-empty window names the leading
      side from single fills)                                                 -39.8% in the burst; with it +2.4%
  Order traffic x1.08; cap-dark book-states -6.5 points.  Alternatives refuted on the same tapes: withholding the touch
  quote and/or level 0 while cap-bound (-22..-57%), withholding the whole leading side (-35%).

The rule (one switch): while a book is cap-bound -- cap minus the venue's rolling volume below the cap's share of one
making window (cap x 10,800 / 86,400 sim-s) -- the side whose capture in the making mirror's window exceeds the other
side's places nothing and keeps nothing while it would add to the inventory (its level 0, every ladder level, its touch
quote); the side that reduces is untouched.  The mirror must have watched one whole making window since it started or
since the simulation changed: before that its window is not the validator's.

STRUCTURAL: the validator's making formula (2 x min per book and window), its rolling volume cap and its making window;
no fitted constant.
"""
from __future__ import annotations

import math
from typing import Any

from research_v641_pace import ASSESSMENT_NS, SIDE_BUY, SIDE_SELL, adds

V619_SURPLUS_HOLD_VERSION = "surplus_hold_v6_19"
MAKING_WINDOW_NS = 10_800_000_000_000      # scoring.kappa.lookback: the validator's making window (sim-ns)
CANCEL_SURPLUS = "V619_SURPLUS_HOLD"
EPS = 1e-9


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def cap_bound(cap: Any, used: Any, *, making_ns: int = MAKING_WINDOW_NS, assessment_ns: int = ASSESSMENT_NS) -> bool:
    """Is the book's room under the cap below the cap's share of one making window?  False without a cap or a reading."""
    c = _finite(cap)
    u = _finite(used)
    if c is None or c <= 0.0 or u is None:
        return False
    return (c - max(0.0, u)) < c * float(making_ns) / float(max(1, int(assessment_ns)))


def leading_side(buy_capture: Any, sell_capture: Any) -> str | None:
    """The side whose window capture exceeds the other side's, or None on a tie / missing reading."""
    b = _finite(buy_capture)
    s = _finite(sell_capture)
    if b is None or s is None:
        return None
    if b > s + EPS:
        return SIDE_BUY
    if s > b + EPS:
        return SIDE_SELL
    return None


def held_side(cap: Any, used: Any, buy_capture: Any, sell_capture: Any, inventory: Any, *, mature: bool) -> str | None:
    """The side this book withholds now: the leading side of a cap-bound book while it would add, else None."""
    if not mature or not cap_bound(cap, used):
        return None
    side = leading_side(buy_capture, sell_capture)
    inv = _finite(inventory)
    if side is None or inv is None or not adds(side, inv):
        return None
    return side


class MirrorAge:
    """How long the making mirror has watched the current simulation: restarts with the mirror and at every rebase."""

    def __init__(self) -> None:
        self.t0: int | None = None
        self.rebases: int | None = None

    def observe(self, ts: Any, rebases: Any) -> None:
        try:
            t = int(ts)
        except (TypeError, ValueError):
            return
        r = int(rebases or 0)
        if self.t0 is None or r != self.rebases or t < self.t0:
            self.t0, self.rebases = t, r

    def mature(self, now_ns: Any, *, making_ns: int = MAKING_WINDOW_NS) -> bool:
        try:
            now = int(now_ns)
        except (TypeError, ValueError):
            return False
        return self.t0 is not None and now - int(self.t0) >= int(making_ns)

    def snapshot(self) -> dict[str, Any]:
        return {"t0": self.t0, "rebases": self.rebases}
