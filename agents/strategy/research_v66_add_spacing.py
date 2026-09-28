# SPDX-License-Identifier: MIT
"""v6.6 S1: add spacing -- a book that already holds inventory adds to it only a full depth beyond its last add.

Why (mainnet, 09-27 23:30 JST, UID 104 on v6.5; validator per-book alpha read 09-28 10:34 JST):

* Book 7 cost UID 104 alpha -175.6 and, alone, its skill leg: 1.92 (rank 0.475) with it, 4.92 without it.  As the price
  rose 287 -> 294, four 2-base deep sells filled within 32 s -- the second one 1 s after the first and 13 cents LOWER --
  until the book held its 6-base bound; the price then ran to 322 and the reducing buys, re-centred on the new mid,
  covered at 318-320.  Every re-placed sell sat one depth from the moved mid, so each new sweep hit the stack again at
  about the price of the last one.
* Replayed with the validator's arithmetic on six 3-hour windows (the latest three contain that spike; the other three
  are 09-26/27), the one rule that held the skill leg in every window: an adding order may rest only a full depth
  beyond the last fill that added on its side.  At UID 104's allowance the lowest window skill went 0.99 -> 3.85 (with
  S2 and S3), its worst book -251 -> -36..-66; making rose, not fell.  A 60-s cool-down and an anchored ("no-chase")
  reducing order were tested too and were weaker or mixed.

The rule (STRUCTURAL -- its only unit is the layer's own depth, the distance its first order already keeps from the
mid; no tuned constant):

* An own fill that grew |inventory| (or opened it) records its price as the book's last add on that side.  A flat book
  forgets both; a book on one side forgets the other side's record.
* While the book holds inventory, an order on the side that would grow it rests only if its price is at least one depth
  beyond that record -- a buy <= last buy - depth, a sell >= last sell + depth.  An adding order that no longer
  qualifies is cancelled; the reducing side is never touched.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

V66_ADD_SPACING_VERSION = "add_spacing_v6_6"
CANCEL_SPACING = "V66_SPACING"
SIDE_BUY = "buy"
SIDE_SELL = "sell"
REBASE_MIN_JUMP_NS = 3_600_000_000_000        # a clock that runs back this far is a new simulation (v6.3.3's rule)


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def adds_to_position(side: str, inventory: Any, eps: float = 1e-9) -> bool:
    """True when the book already holds inventory and a fill on ``side`` would grow it (a flat book: False)."""
    inv = _finite(inventory)
    if inv is None:
        return False
    if side == SIDE_BUY:
        return inv > eps
    if side == SIDE_SELL:
        return inv < -eps
    return False


def spaced(side: str, price: Any, last_add: Any, depth_ticks: Any, tick: Any, eps: float = 1e-9) -> bool:
    """May an adding order on ``side`` rest at ``price``?  Yes without a record; else a full depth beyond it."""
    last = _finite(last_add)
    if last is None:
        return True
    p, d, t = _finite(price), _finite(depth_ticks), _finite(tick)
    if p is None or d is None or t is None or d < 0.0 or t <= 0.0:
        return False                                   # an unreadable price never adds past a record
    gap = d * t
    if side == SIDE_BUY:
        return p <= last - gap + eps
    if side == SIDE_SELL:
        return p >= last + gap - eps
    return False


def own_fills(trades: Iterable[dict], own_uid: Any) -> list[tuple[str, float, float]]:
    """Our fills in one state's prints, in order, as (side, price, quantity) -- maker or taker."""
    try:
        uid = int(own_uid)
    except (TypeError, ValueError):
        return []
    out: list[tuple[str, float, float]] = []
    for t in trades or ():
        try:
            p, q, s = float(t["p"]), float(t["q"]), int(t["s"])
            ma, ta = int(t.get("Ma", -1)), int(t.get("Ta", -1))
        except (KeyError, TypeError, ValueError):
            continue
        if not (math.isfinite(p) and math.isfinite(q)) or q <= 0.0 or ma == ta:
            continue
        if ma == uid:
            out.append((SIDE_BUY if s == 1 else SIDE_SELL, p, q))      # s == 1: the taker sold into our bid
        elif ta == uid:
            out.append((SIDE_BUY if s == 0 else SIDE_SELL, p, q))      # s == 0: we took the ask
    return out


class AddSpacing:
    """Each book's last adding fill per side."""

    def __init__(self) -> None:
        self.rebases = 0
        self.reset()

    def reset(self) -> None:
        self.last: dict[int, dict[str, float]] = {}
        self.last_ts: int | None = None
        self.adds_recorded = 0

    def maybe_rebase(self, now_ns: Any) -> None:
        """A clock that runs back an hour or more is a new simulation: its prices owe nothing to the old records."""
        try:
            now = int(now_ns)
        except (TypeError, ValueError):
            return
        if self.last_ts is not None and now < self.last_ts - REBASE_MIN_JUMP_NS:
            rebases = self.rebases
            self.reset()
            self.rebases = rebases + 1
        self.last_ts = now if self.last_ts is None else max(self.last_ts, now)

    def note(self, book_id: int, trades: Iterable[dict], own_uid: Any, inventory_after: Any, eps: float = 1e-9) -> int:
        """One state's prints for one book and our inventory after them; returns the adds recorded."""
        b = int(book_id)
        inv_after = _finite(inventory_after)
        fills = own_fills(trades, own_uid)
        recorded = 0
        if fills and inv_after is not None:
            inv = inv_after - sum(q if side == SIDE_BUY else -q for side, _p, q in fills)
            rec = self.last.setdefault(b, {})
            for side, p, q in fills:
                grows = (side == SIDE_BUY and inv >= -eps) or (side == SIDE_SELL and inv <= eps)
                if grows:
                    rec[side] = p
                    recorded += 1
                inv += q if side == SIDE_BUY else -q
        if inv_after is not None:
            rec = self.last.get(b)
            if rec is not None:
                if abs(inv_after) <= eps:
                    rec.clear()
                elif inv_after > 0.0:
                    rec.pop(SIDE_SELL, None)
                else:
                    rec.pop(SIDE_BUY, None)
                if not rec:
                    self.last.pop(b, None)
        self.adds_recorded += recorded
        return recorded

    def last_add(self, book_id: int, side: str) -> float | None:
        rec = self.last.get(int(book_id))
        return None if rec is None else rec.get(side)

    def allows(self, book_id: int, side: str, price: Any, inventory: Any, depth_ticks: Any, tick: Any) -> bool:
        """False only for an order that would grow a held position less than a depth beyond the last add."""
        if not adds_to_position(side, inventory):
            return True
        return spaced(side, price, self.last_add(book_id, side), depth_ticks, tick)

    def snapshot(self) -> dict[str, Any]:
        return {"version": V66_ADD_SPACING_VERSION, "books_recorded": len(self.last),
                "adds_recorded": int(self.adds_recorded), "rebases": int(self.rebases)}
