# SPDX-License-Identifier: MIT
"""v6.2.7: the making leg, taken as far as a maker can take it.

Why (testnet UID 82 v6.2.6 at tick 3,000 read against the live mainnet field, 2026-09-20):

The field says the score is ``0.395*kappa + 0.25*making_rank + 0.25*skill_rank`` -- fitted across 255
mainnet agents the residual is a median of -0.0001 (p10 -0.0010, p90 +0.0056), so the PnL term at
weight 0.105 contributes nothing in practice.  Two facts follow, and they set this build's scope:

* **Kappa is not available to a maker.**  0 of 255 agents hold kappa > 0.65 together with making rank
  > 0.5; corr(kappa, making_rank) = -0.286; every one of the top 11 agents by trading score sits at
  kappa 0.4980-0.4998, pinned exactly as we are.  The 43 agents that do hold kappa > 0.70 are takers
  -- making 0.0, maker volume 1.2M against taker volume 20.5M.  Chasing kappa as a maker is what
  v6.2.6 rule A tried; it cost 25% of our releases and 24% of our fills and returned five FEWER clean
  books (16 -> 11).  Retired here.
* **So the maker ceiling is 0.395*0.4996 + 0.25*1.0 = 0.447**, and the best score actually achieved in
  the field with skill_rank < 0.10 is 0.4138.  Everything this build can win is in making_rank.

The two rules are therefore the two things still holding making_rank down:

* **Rule B was proportional where it needed to be corrective.**  v6.2.6 split the clip
  ``base*(1 +/- |r|)`` between the deficit and surplus sides, which only slows an imbalance -- and with
  ``lots_of`` flooring to whole minimum orders a 0.5 clip can only take the values 0.25 or 0.5, far too
  coarse to correct a book sitting at 0.66.  Balance moved 37% -> 60.9% and stalled there, median book
  0.663, only 30 of 120 books at 0.80.  Since making is ``2*min(buy, sell)`` per book, a unit of size
  on the leading side earns exactly zero while the book is unbalanced, so the surplus side simply
  stops until the book clears the target.
* **The caps could not admit what the band promised.**  ``universe_caps()`` sets the portfolio
  exposure bound to ``n * lot`` on the comment "every book may hold its one lot in flight" while
  setting the startup seed bound to ``2.0 * n * lot`` eleven lines below -- the seed may import twice
  what the exposure guard admits.  The v6.2.6 launch arrived holding 63.97 BASE against a 64.0 cap and
  refused 125,681 placements (99.93%) before tick 1,500.  Both bounds are reconciled to one band here:
  the guard admits ``BAND_CLIPS`` clips per book, the seed may restore one of them, so a relaunch
  always keeps a full clip per book of headroom and the lockout cannot recur.
"""
from __future__ import annotations

import math
from typing import Any

V627_MAKER_CEILING_VERSION = "maker_ceiling_v6_2_7"

# The per-book balance a book must reach before its leading side is worth quoting again.  Not an
# observed number: making is 2*min(buy, sell), so below a balanced book every unit on the leading
# side scores zero.  The target is one minus a single clip's worth of tolerance on a two-clip band.
BALANCE_TARGET = 0.85

SIDE_BUY = "buy"
SIDE_SELL = "sell"

REASON_UNBALANCED = "CAPTURE_SURPLUS"   # the skip reason stays the one the v6.2.6 read is keyed to


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


# ---- rule B, corrective: the surplus side waits until the book is balanced -----------------------

def book_balance(buy: Any, sell: Any) -> float | None:
    """The validator's own per-book balance, ``2*min(buy, sell) / (buy + sell)``.

    ``None`` when the book has no capture yet -- nothing to correct, so both sides may quote.  A pair
    that sums negative is as unbalanced as a book can be and reports -1.0 rather than the positive
    number the raw ratio would give for two negatives.
    """
    b, s = _finite(buy), _finite(sell)
    total = b + s
    if abs(total) <= 1e-12:
        return None
    if total < 0.0:
        return -1.0
    return max(-1.0, min(1.0, 2.0 * min(b, s) / total))


def balance_gate_sides(
    *,
    clip: Any,
    buy_capture: Any,
    sell_capture: Any,
    min_order: Any,
    lots_of,
    target: Any = BALANCE_TARGET,
) -> dict[str, float]:
    """The clip for each side: both when the book is balanced, else the deficit side alone.

    The objective is the validator's ``2*min(buy, sell)`` per book.  Size on the side that already
    leads cannot raise the minimum, so it earns nothing until the other side catches up.  Both sides
    stop only when neither is positive -- there adding cannot raise the minimum either, and the book
    is left to its exit until one side recovers.
    """
    base = max(0.0, _finite(clip))
    unit = max(1e-12, _finite(min_order, 0.25))
    if base <= 0.0:
        return {SIDE_BUY: 0.0, SIDE_SELL: 0.0}
    b, s = _finite(buy_capture), _finite(sell_capture)
    if max(b, s) < 0.0:
        return {SIDE_BUY: 0.0, SIDE_SELL: 0.0}
    qty = lots_of(base, unit)
    balance = book_balance(b, s)
    if balance is None or balance >= _finite(target, BALANCE_TARGET):
        return {SIDE_BUY: qty, SIDE_SELL: qty}
    out = {SIDE_BUY: 0.0, SIDE_SELL: 0.0}
    out[SIDE_BUY if b < s else SIDE_SELL] = qty
    return out


# ---- the caps, reconciled to one band ------------------------------------------------------------

def band_caps(caps: dict[str, Any], *, band_clips: Any) -> dict[str, Any]:
    """Reconcile the portfolio exposure bound and the startup seed bound to the same band.

    ``caps`` arrives carrying ``research_max_total_abs_base = n * clip`` -- one clip per book.  The
    band is what a book may actually hold (one clip plus one opposite), so that is what the exposure
    guard admits; the seed restores the held clip only, which leaves a full clip per book of headroom
    on every relaunch.  Deriving both from the one number is the point: v6.2.6 shipped with them set
    independently, the seed bound at twice the exposure bound, and the run could not quote at all.
    """
    out = dict(caps)
    per_book_total = _finite(out.get("research_max_total_abs_base"))
    if per_book_total <= 0.0:
        return out
    band = max(1.0, _finite(band_clips, 2.0))
    out["research_max_total_abs_base"] = per_book_total * band
    out["research_a195_max_seed_abs_base"] = per_book_total
    return out
