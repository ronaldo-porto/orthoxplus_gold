# SPDX-License-Identifier: MIT
"""v6.5: sized deep clips -- the deep layer's orders are twice the touch clip, and a book holds three of them.

Why (mainnet, sim 20260924_1653, measured 09-27 on UID 94's recorder; scratchpad v63/r650, r652):

* The genuine skill leader (UID 50: trading 0.948, net alpha 10.5k on 104 books) runs the v6.4 architecture -- deep
  orders ~40-50 ticks from the mid in normal spreads, orders inside blown-out spreads -- at a fixed 10-base clip.  Ours
  rest 1 base: 0.18 alpha per fill against its 3.7.
* Replayed with the validator's arithmetic over the window it scores (13,988 states; partial fills by print size, the
  side-ownership delay, and the shipped v6.4.1 volume pacer with each UID's real remaining allowance): a clip twice
  the touch clip with three clips of room gives alpha +55..+93%, making +37..+59%, skill 3.72 -> 4.90 on UID 94's
  allowance and 2.83 -> 5.75 on UID 104's -- higher skill in 14 of 15 paired windows.  Three and four times the clip
  grow alpha but break the shape (9-12 books under zero); quoting the reducing side closer, and stopping the deep
  layer on a drawn-down book, both lose.

The rules (two switches, one causal change each):

* S1 ``research_v65_deep_clip_mult`` -- the deep layer's clip (its orders, its vacuum orders and its paper record)
  is the touch clip times this multiple.  1.0 is v6.4.1.  OBSERVED: 2.0 won the grid (1 / 2 / 3 / 4).
* S2 ``research_v65_deep_max_clips`` -- a book's deep inventory bound, in deep clips.  2.0 (v6.3.3's DEEP_MAX_CLIPS)
  is v6.4.1.  OBSERVED: 3.0 won the grid (2 / 3 / 8).
* The exposure cap follows (STRUCTURAL).  The final validator charges every order its worst-case fill against
  ``research_max_total_abs_base``, which v6.3 sets to three touch clips per book: the v6.4.1 deep bound (two clips) plus
  one order in flight, exactly.  A larger deep bound needs the same arithmetic on its own size -- the bound plus one
  deep order in flight per book -- or the validator refuses the new room as STRICT_EXPOSURE_HEADROOM.
"""
from __future__ import annotations

import math
from typing import Any

from research_v633_deep_layer import DEEP_MAX_CLIPS as V633_MAX_CLIPS

V65_DEEP_CLIPS_VERSION = "deep_clips_v6_5"

CLIP_MULT = 2.0                # OBSERVED: the r650/r652 replay grid (1 / 2 / 3 / 4)
MAX_CLIPS = 3.0                # OBSERVED: the r650 replay grid (2 / 3 / 8)
V641_CLIP_MULT = 1.0           # the multiple that reproduces v6.4.1


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def deep_clip(base_clip: Any, mult: Any, min_order: Any = 0.0) -> float:
    """The deep layer's order size: the touch clip times the multiple, never below the venue's minimum order.

    A multiple that is not a positive number is v6.4.1's (1.0): a bad parameter never stops the layer.
    """
    m = _finite(mult)
    if m is None or m <= 0.0:
        m = V641_CLIP_MULT
    base = max(0.0, _finite(base_clip) or 0.0)
    return max(max(0.0, _finite(min_order) or 0.0), base * m)


def max_clips(value: Any) -> float:
    """A book's deep inventory bound in deep clips; at least one (a smaller bound could never hold an order).

    A value that is not a number is v6.3.3's bound (2.0).
    """
    v = _finite(value)
    if v is None:
        return float(V633_MAX_CLIPS)
    return max(1.0, v)


def book_bound(clip: Any, clips: Any) -> float:
    """The most base one book's deep layer may hold."""
    return max(0.0, _finite(clip) or 0.0) * max_clips(clips)


def caps_for(book_count: int, *, clip: Any, max_clips: Any) -> dict[str, float]:
    """The exposure bound the deep layer implies: every book may hold its bound plus one deep order in flight.

    At v6.4.1's size (clip 1.0, 2 clips) this is v6.3's bound: three clips per book.
    """
    n = max(1, int(book_count))
    c = max(0.0, _finite(clip) or 0.0)
    per_book = book_bound(c, max_clips) + c
    total = float(n) * per_book
    return {"research_max_total_abs_base": total, "research_a195_max_seed_abs_base": total}
