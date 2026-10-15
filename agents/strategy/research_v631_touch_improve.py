# SPDX-License-Identifier: MIT
"""v6.31 S12b: the v6.17 touch quote improves the OTHERS' best by one tick -- strictly on its side of their mid.

Why (scratchpad k87, 10-15 JST, both simulations seen):

* The leaders take 14-19% of their notional by improving the best price by exactly one tick inside wide spreads
  (UID 255: 18.2% of its notional in the new simulation at a markout quality q of 0.90, 19.2% in the old one; the
  top five of the old one 14-15% at q 0.78-0.96).  They improve only into wide spreads: the spread before their
  improve has a median of 6 ticks, 5-9 ticks hold 78-89% of their improve notional and 2-4 ticks about 1%.  We take
  0.3-2.9% of our notional that way -- and those fills are stale deep orders left inside 25-29-tick spreads, not a
  placement.  A new price level puts an order first in its queue; joining puts it last (our queue position at the
  join: p50 1-2, theirs 0-1).
* Replayed with the validator's arithmetic (k87/vm687.py: v6.30's book as live -- S9, the A1.9 size-shortfall cancel of
  a reducing touch quote, the deep layer placing first -- over 3 regimes x 5 UID cells x delays 30/40/50, the
  pre-registered rule R1-R5: E up in 2 of 3 thirds, total above the d39/d41 floor, volume >= 0.9x, balance >= base -
  0.03, skill > 0 on >= 20 kept books):
  S12  (one tick inside whenever the spread is >= 2 ticks): E +4..+24% in all 15 results, but FAIL by the rule in one
       cell (the 10-14 tape, UID 161: balance -0.035 / -0.036 at d30 / d40); at a 2-tick spread the improved price
       sits AT the mid and captures ~nothing (C/N 0.06-0.19 bps).
  S12b (this rule: never at or past the others' mid, so a spread under 3 ticks is joined): PASS all 5 cells at all 3
       delays -- E +3.6..+19.8%, making per 1M notional -3.2..+17.0%, balance worst -0.017, q +0.014..+0.049.
  S12c (improve only when the spread is wider than the book's running median): PASS as well, E +4.0..+20.9%; the
       fallback if the canary shows a balance or competition problem.
* Caveat: the replay cannot model a faster maker re-improving over our improved quote, so it is optimistic on queue
  priority; and live, v6.2.14's touch life cancels any non-deep order once its side's best is a tick better than it,
  so an overbid quote is re-placed one tick over the overbidder while that is still legal (strictly our side of the
  others' mid, edge >= THRESHOLD_BPS) -- bounded by the mid, as v6.2.10 designed.  Hence a canary first.
* Refuted before (10-06, k21): improving by a tick was -33..-47% making in the old quiet-trend simulation -- under the
  CAPTURED pay, with no markout quality, on a v6.16 base.  The validator pays REALIZED making since 10-10 and the base
  is S9; k87 re-tested it on both.

The rule (``research_v631_touch_improve``), for a NEW touch-quote placement only -- its side, size, gates (S7, S9,
v6.19, pacing, the bound) and the rest / cancel verdict are v6.17's, unchanged:

* The candidate is one tick better than the others' best on the quote's side -- the book's best excluding every order
  we own (v6.2.10's touch view: by the ledger's order ids and by our client ids, the touch quote's own included).
* It is used only when v6.2.10's geometry accepts it (``improved_bid`` / ``improved_ask``): strictly inside every
  opposite order, ours included, AND strictly on our side of the others' mid -- never at or past it.
* It must be strictly better than the join price v6.17 chose (else one of our own orders already holds a better
  price: the join is kept and we never walk over ourselves), and its edge against the fresh fundamental must still be
  ``THRESHOLD_BPS`` (v6.17's hurdle, the same as the join's).  Otherwise the price is v6.30's join, unchanged.

STRUCTURAL: the venue tick, the others' best and mid (v6.2.10's guards against walking over ourselves and against a
war through the mid), v6.17's alpha hurdle; nothing is sized from the sample.  The candidate rule (one tick, the mid) is
the replay's imp3=1 exactly.

The build's second switch, ``research_v631_single_write_save`` [STRUCTURAL bug fix], has no code here, only its
constants: the agent runs v6.30's whole save on ``path + STAGE_SUFFIX`` and replaces the session file with it in one
os.replace only when it is complete, so a stop between v6.30's two writes can no longer leave an owner-less file.
"""
from __future__ import annotations

from typing import Any

from research_v617_touch_quote import SIDE_BUY, SIDE_SELL, THRESHOLD_BPS, edge_bps
from research_v6210_touch_improve import improved_ask, improved_bid

V631_TOUCH_IMPROVE_VERSION = "touch_improve_v6_31"
V631_SINGLE_WRITE_SAVE_VERSION = "single_write_save_v6_31"
STAGE_SUFFIX = ".v631.stage"            # the staged session file: <session file> + this, replaced into place when whole

IMPROVED_BUY = "improved_buy"           # one tick above the others' best bid
IMPROVED_SELL = "improved_sell"         # one tick below the others' best ask
JOIN_NOT_INSIDE = "join_not_inside"     # the candidate would reach the others' mid or an opposite order of ours
JOIN_OWN_BEST = "join_own_best"         # the candidate is not better than the join: our own order holds the best
JOIN_EDGE = "join_edge"                 # the candidate's edge vs the fundamental is short of THRESHOLD_BPS
JOIN_NO_VIEW = "join_no_view"           # no touch view of this book this request (none noted, crossed, empty, ours)
REASONS = (IMPROVED_BUY, IMPROVED_SELL, JOIN_NOT_INSIDE, JOIN_OWN_BEST, JOIN_EDGE, JOIN_NO_VIEW)

__all__ = ["IMPROVED_BUY", "IMPROVED_SELL", "JOIN_EDGE", "JOIN_NOT_INSIDE", "JOIN_NO_VIEW", "JOIN_OWN_BEST", "REASONS",
           "STAGE_SUFFIX", "THRESHOLD_BPS", "V631_SINGLE_WRITE_SAVE_VERSION", "V631_TOUCH_IMPROVE_VERSION",
           "improved_touch_price"]


def _num(value: Any) -> float | None:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return None
    return x if x == x and x not in (float("inf"), float("-inf")) else None


def improved_touch_price(side: str, view: Any, fundamental: Any, tick: Any, *, join_price: Any,
                         threshold_bps: float = THRESHOLD_BPS) -> tuple[float | None, str]:
    """(price, reason) for a new touch quote on ``side``: the others' best one tick better (IMPROVED_BUY /
    IMPROVED_SELL) when v6.2.10's geometry accepts it, it beats ``join_price`` and its edge vs ``fundamental`` is
    ``threshold_bps``; else (None, why) -- the caller keeps ``join_price``.  ``view`` is v6.2.10's TouchView of the book
    this request (None: JOIN_NO_VIEW).  Pure: reads its arguments only, never raises on unreadable input."""
    if view is None:
        return None, JOIN_NO_VIEW
    t, join = _num(tick), _num(join_price)
    if t is None or t <= 0.0 or join is None or join <= 0.0:
        return None, JOIN_NO_VIEW
    ob, oa = getattr(view, "others_bid", None), getattr(view, "others_ask", None)
    if side == SIDE_BUY:
        cand = improved_bid(others_bid=ob, others_ask=oa, raw_ask=getattr(view, "raw_ask", None), tick=t)
    elif side == SIDE_SELL:
        cand = improved_ask(others_bid=ob, others_ask=oa, raw_bid=getattr(view, "raw_bid", None), tick=t)
    else:
        return None, JOIN_NO_VIEW
    if cand is None:
        return None, JOIN_NOT_INSIDE
    better = cand > join + 0.5 * t if side == SIDE_BUY else cand < join - 0.5 * t
    if not better:
        return None, JOIN_OWN_BEST
    e = edge_bps(side, cand, fundamental)
    if e is None or e < float(threshold_bps) - 1e-9:
        return None, JOIN_EDGE
    return cand, (IMPROVED_BUY if side == SIDE_BUY else IMPROVED_SELL)
