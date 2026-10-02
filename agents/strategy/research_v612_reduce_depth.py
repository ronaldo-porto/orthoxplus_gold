# SPDX-License-Identifier: MIT
"""v6.12: the side that reduces a book's position rests its level-0 deep order at half the book's depth.

Why (mainnet, sim 20260929_2015, measured 10-02 JST on UID 2's observatory and all ten agents' fill logs, validator
47932d4; scratchpad k3/, v612/vm612.py):

* The validator pays making per book as 2 x min(buy capture, sell capture) (debeta.balanced_reward_per_book): on a
  book, capture on the side already ahead pays nothing until the other side catches up, and a unit on the side behind
  pays twice.  Over v6.11's first 2,512 ticks our ten uids kept 0.52-0.68 of their gross capture as making (the field's
  top makers 0.59-0.72); on every uid the side ahead held 2-3.5x the capture of the side behind.
* The side behind is, on a held book, the side that unwinds the position.  A deep fill adds inventory on the side the
  sweep hit; the order that unwinds it rests on the other side at the SAME depth, where only a sweep of the same reach
  the other way fills it, so the position waits and the book stays one-sided.  Its capture per unit at shallower depths
  is still positive (the side behind, 10-02: +0.37 bps at <= 3 ticks from the touch, +0.25..+0.38 at 4-15).
* Replayed with the validator's arithmetic on the recorded prints (vm612 = the calibrated v6.10 deep-ladder replay,
  v6.11 settings), making over the window, baseline -> this rule (with the minimum-order guard below):
  this sim 10-02 (thin), the volume line on the remaining cap   32.1 -> 75.2 (+134%), per unit of volume +43%;
  this sim's first hours                                        290 -> 384 (+32%), per unit of volume -10%;
  previous sim, burst/drought                                   1,377 -> 1,703 (+24%), per unit of volume -25%;
  previous sim, paying hours (mean of three windows)            17.7k -> 19.2k (+9%), per unit of volume -11%.
  Volume rises 22-64%; where it pays less per unit (the richer regimes) the volume line already holds the books.
  Net alpha falls in the thin and burst spans (10-02 -95 -> -545; burst 3,139 -> 1,692) and holds in the paying
  hours (24.6k -> 24.4k); every uid's alpha share is 0 today (kappa < 0), so the pay is the making half.  Refuted
  beside it on the same prints: a sweep record per side (-9..-13% making), withholding the side ahead (-5..-58%), a
  larger order on the side behind (-9%), and adding level 0 at p90 (+16% today at -8% per unit of volume, -8% in the
  paying hours at +12% volume).

The rule (one switch, ``research_v612_reduce_depth``):

* On a book holding at least the venue's minimum order, the level-0 deep order on the side that reduces the position
  rests at REDUCE_DEPTH_FACTOR of the book's depth and is judged for repricing against that distance; deep_price still
  holds it strictly behind the touch.  The adding side, the ladder's levels (filtered against the side's own level 0),
  an order inside a blown-out spread (v6.4 vacuum) and the paper record that gates the book are unchanged.
* STRUCTURAL: the reducing side's capture is the binding side of the book's min(buy, sell), so it is the side whose
  fills the pay rule needs; a position below the venue's minimum order is dust, not a position to unwind.
* OBSERVED: the factor 0.5 (the replay grid 0.25 / 0.5 / 0.75 on 10-02's prints; 0.5 made the most making on all four
  spans above).
"""
from __future__ import annotations

import math
from typing import Any

from research_v633_deep_layer import SIDE_BUY, SIDE_SELL

V612_REDUCE_DEPTH_VERSION = "reduce_depth_v6_12"

REDUCE_DEPTH_FACTOR = 0.5           # OBSERVED: the 10-02 replay grid (0.25 / 0.5 / 0.75), confirmed on three more spans


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def reducing_side(inventory: Any, min_order: Any) -> str | None:
    """The side whose fill shrinks the position: SELL when long, BUY when short, by at least the venue's minimum order;
    None for a flat or dust position, or one that cannot be read."""
    inv = _finite(inventory)
    if inv is None:
        return None
    floor = max(_finite(min_order) or 0.0, 1e-9)
    if inv >= floor:
        return SIDE_SELL
    if inv <= -floor:
        return SIDE_BUY
    return None


def level0_depth(side: Any, depth: Any, inventory: Any, min_order: Any, *, factor: Any = REDUCE_DEPTH_FACTOR) -> Any:
    """The distance from the mid (ticks) the side's level-0 deep order rests at: ``depth x factor`` on the side that
    reduces the position, ``depth`` on any other side.  A depth, factor or side that cannot be read leaves the depth
    as it is (a bad read never moves an order)."""
    d = _finite(depth)
    f = _finite(factor)
    if d is None or f is None or not (0.0 < f <= 1.0):
        return depth
    if side not in (SIDE_BUY, SIDE_SELL) or reducing_side(inventory, min_order) != side:
        return depth
    return d * f
