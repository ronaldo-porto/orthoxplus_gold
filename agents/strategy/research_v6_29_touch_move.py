# SPDX-License-Identifier: MIT
"""v6.29: S9 the touch on the fundamental's move -- research_v629_touch_fp_move.

Why (mainnet 10-13 JST; scratchpad k71: UID 115's tape, replay vm671 = k70's vm670 + the fundamental-move gate, the
validator's arithmetic and the true pay rule; memory fill-quality-touch-gate-1013):

* The anchored makers (UIDs 255 / 248 / 216) fill at q ~0.9 by trading on the side the fundamental just MOVED toward:
  80-84% of their notional sits on the side of its recent change (its change, not the gap's level).  Ours: 46-55%, and
  23-25% of our notional sat against the gap AND against the move (q -0.16..-0.21).
* Replayed (vm671 fmg=4: the v6.17 / v6.27 touch quote toward the fundamental held while the fundamental's change over
  the look-back is against its side, everything else v6.27) on 115's payback window: E +7.3% with the look-back at the
  feed's freshness horizon (30 sim-s), +10.2% at 60 s, +2.0% at 120 s; the touch's q 0.84 -> 0.96 at 60 s, volume
  -4%.  78's launch +27.8%, 161's +18.9%; positive in 9 / 9 order-delay x third cells; out of sample (the previous
  simulation) +0.8..+2.2%.  Every deep-layer variant (both sides, the against-gap side, deep + touch, fair-value
  limits) one-sided the book: -8..-70% (refuted) -- so only the touch quote is gated.

The rule (one switch, ``research_v629_touch_fp_move``, default on):

* The touch quote (v6.17; S7's centre rule unchanged) rests only while the fundamental's move over the look-back is not
  against its side: a BUY touch (the fundamental above the mid) is withheld -- not placed, and a resting one cancelled
  by the quote's own checks (CANCEL_FP_MOVE) -- while the fundamental FELL over the look-back (delta < 0), a SELL touch
  while it ROSE (delta > 0).  delta == 0, no page old enough, a stale or missing page, or any error: no gate (v6.28
  exactly).  The deep layer, S1, S5, S6, S7 and S8 are unchanged.
* delta = this request's fundamental for the book (v6.17's reading of its page, fresh by S6's limit) minus the book's
  fundamental on the REFERENCE page: the page that was current W sim-s ago -- the newest page this agent first saw at or
  before this request's state time minus W.  The agent keeps a short history of the distinct pages (by their own sim
  time) its v6.16 feed snapshot showed, each with the state time of the first request that saw it, and only what W needs
  (the reference and every newer page).  A page of another simulation is not kept; a new simulation (another page sim
  id, or a state time that runs back) starts the history over.
* A page whose own sim time is AHEAD of this request's state time is not kept; it is recorded at the first request
  whose state time reaches it.  STRUCTURAL: the validator publishes a books page only at a scoring-interval timestamp
  and only after forward() has sent that state to the miners (neurons/validator.py), so in normal operation a page
  never leads the state clock (k61: lag at fetch >= 0).  A page ahead of the clock is one the clock has not reached:
  after a checkpoint rewind (same sim id; engines/simulation.py restart_simulator resumes from the latest checkpoint)
  it is the abandoned run's last page, which the shared cache still shows -- kept, it would refuse every page the
  resumed run publishes up to its own time and stay the reference for about rewind + W.
* W = this request's S6 freshness limit (research_v626_feed_clock: two page steps plus the feed's wall cycle at the
  measured sim rate, 30.0 sim-s at 10-13's rate); v6.16's MAX_STALE_SIM_S while there is none.

This is k71's vm671 definition (fmh, fplag 0, fpown): its reference FP_H is the newest page whose USABLE time T -- the
first state that could see it (the recorder's cache write plus half the agents' re-read, mapped onto the request clock)
-- is at or before t - H; its current page the newest usable one, kept while its own sim time is within the stale limit;
dfp = ln(fp_now / fp_H).  The agent's first sighting is that T measured instead of estimated.  On the k70 pages and
115's request times ([50,104, 57,104), W = 30) the rule takes vm671's decision on 100% of book-states with vm671 at any
change and on 93.75% against the H=30 run itself (its 0.5-bps threshold); anchoring the reference on the pages' own sim
time instead agrees 89.7% / 83.7%: the fundamental changes for every book at once every ~30 sim-s (p10 23 / p90 37; a
page every ~9), so a reference W of page time back nearly always reaches past the last change -- that gate is never off
(0% zero moves, vm671 8.7%) (scratchpad k73/build/cmp_lookback.out).  Left different from the H=30 run: W is S6's limit,
not a fixed 30 (30.0 at 10-13's rate; the run's stale limit was 31); any change gates (the run's 0.5 bps -- the
threshold barely mattered in k71: at 60 s, 0.5 bps +10.2%, any change +9.0%); a restarted agent has no reference for
its first W sim-s (the engine had every page).

STRUCTURAL: the look-back is the feed's own freshness horizon (S6's measured limit; v6.16's while it is unknown) and the
side is the sign of the fundamental's move -- the module states no number.  OBSERVED: the evidence (k71: this simulation
and the previous one).
"""
from __future__ import annotations

import math
from typing import Any

from research_v616_fair_value import MAX_STALE_SIM_S, same_simulation
from research_v633_deep_layer import SIDE_BUY, SIDE_SELL

V6_29_TOUCH_MOVE_VERSION = "touch_fp_move_v6_29"
CANCEL_FP_MOVE = "TOUCH_FP_MOVE"      # a resting touch quote the fundamental moved against over the look-back

# One book's touch decision: no gate and why (no reference page, no fresh fundamental now, no move), or the move's sign.
NO_HISTORY, STALE, ZERO_MOVE, FELL, ROSE = "no_history", "stale", "zero_move", "fp_fell", "fp_rose"

__all__ = ["CANCEL_FP_MOVE", "FELL", "NO_HISTORY", "PageHistory", "ROSE", "STALE", "V6_29_TOUCH_MOVE_VERSION",
           "ZERO_MOVE", "held_side", "launch_records", "lookback_sim_s"]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _ns(value: Any) -> int | None:
    """A state or page time in ns: an int (not a bool), or None."""
    if isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _book_value(fp: Any, book_id: Any) -> float | None:
    """The book's fundamental on a page's ``fp`` map (keyed as v6.16 reads it: the id, its int, its text), or None."""
    if not isinstance(fp, dict):
        return None
    v = fp.get(book_id)
    if v is None:
        try:
            v = fp.get(int(book_id))
        except (TypeError, ValueError):
            v = None
    if v is None:
        try:
            v = fp.get(str(int(book_id)))
        except (TypeError, ValueError):
            v = None
    return _finite(v)


def lookback_sim_s(limit: Any) -> float:
    """W, the look-back in sim-s: this request's S6 freshness limit, or v6.16's MAX_STALE_SIM_S when there is none or
    it is not a positive number.  Never raises."""
    w = _finite(limit)
    return w if w is not None and w > 0.0 else MAX_STALE_SIM_S


def held_side(delta: Any) -> str | None:
    """The side whose touch quote the fundamental's move ``delta`` is against: BUY when it fell (delta < 0), SELL when
    it rose (delta > 0); None for no move or anything that is not a number."""
    d = _finite(delta)
    if d is None or d == 0.0:
        return None
    return SIDE_BUY if d < 0.0 else SIDE_SELL


def launch_records(line: Any) -> int | None:
    """The launch-rate records on S8's line (research_v628_flat_launch's FlatLaunchLine.launch_rates).  After the first
    request's restore only S8's own flat launches add one -- a book first seen on an empty window -- so a larger number
    than at the last look is a flat launch recorded since.  None for a line without them."""
    rates = getattr(line, "launch_rates", None)
    return len(rates) if isinstance(rates, dict) else None


class PageHistory:
    """The distinct pages (by their own sim time) this agent's feed snapshot showed, oldest first, each with the state
    time of the first request that saw it: (first seen ns, page sim time ns, the page's fundamentals).  Only what the
    look-back needs is kept.  Inputs are checked here; the agent counts anything else that goes wrong and gives the book
    no gate."""

    def __init__(self) -> None:
        self.pages: list[tuple[int, int, Any]] = []
        self.sim_id: Any = None
        self.resets = 0

    def reset(self) -> None:
        if self.pages or self.sim_id is not None:
            self.resets += 1
        self.pages = []
        self.sim_id = None

    def observe(self, snapshot: Any, now_ns: Any, sim_id: Any) -> bool:
        """Records the snapshot's page as first seen at this request's state time ``now_ns`` when it is a page of the
        state's simulation (``sim_id``, v6.16's rule) newer, by its own sim time, than every page kept, and not ahead of
        ``now_ns``.  A state time behind the newest sighting (a checkpoint rewind, a new simulation), or a page of
        another simulation than the kept ones, starts the history over first.  A page ahead of the state clock is not
        recorded (STRUCTURAL: the validator publishes a page only after it sent that state to the miners, so it never
        leads the clock in normal operation; after a rewind it is the abandoned run's) -- it is at the first request
        whose state time reaches it.  True when the page was recorded."""
        now = _ns(now_ns)
        if now is None or now <= 0:
            return False
        if self.pages and now < self.pages[-1][0]:
            self.reset()                                      # the state clock ran back: a rewind, a new run
        if not isinstance(snapshot, dict) or not isinstance(snapshot.get("fp"), dict):
            return False
        page_sim = snapshot.get("sim_id")
        if not same_simulation(page_sim, sim_id):
            return False                                      # v6.16 reads no page of another simulation
        page_ts = _ns(snapshot.get("sim_ts_ns"))
        if page_ts is None or page_ts <= 0:
            return False
        if page_ts > now:
            return False                                      # ahead of the state clock: not reached (yet)
        if self.pages and not same_simulation(page_sim, self.sim_id):
            self.reset()
        if self.pages and page_ts <= self.pages[-1][1]:
            return False                                      # the same page again, or an older one
        self.pages.append((now, page_ts, snapshot["fp"]))
        if page_sim:
            self.sim_id = page_sim
        return True

    def reference(self, now_ns: Any, lookback_s: Any) -> tuple[int, int, Any] | None:
        """The page that was current ``lookback_s`` sim-s before ``now_ns``: the newest kept page first seen at or
        before then, or None.  Every older page is dropped -- no later request can need it at the same look-back."""
        now, w = _ns(now_ns), _finite(lookback_s)
        if now is None or w is None or w < 0.0:
            return None
        cut = now - w * 1e9
        for i in range(len(self.pages) - 1, -1, -1):
            if self.pages[i][0] <= cut:
                del self.pages[:i]
                return self.pages[0]
        return None

    def decide(self, book_id: Any, fp: Any, now_ns: Any, lookback_s: Any) -> tuple[str | None, str]:
        """One book's touch decision this request: (the side whose touch quote is withheld, why).  ``fp`` is this
        request's fundamental for the book (v6.17's, None when its page is stale or missing).  The side is held_side of
        fp minus the book's fundamental on the reference page (``lookback_s`` back); None with STALE (no fundamental
        now), NO_HISTORY (no reference page, or it has no fundamental for the book) or ZERO_MOVE.  The reference is
        looked up first, so a run of stale pages still drops what the look-back no longer needs."""
        ref = self.reference(now_ns, lookback_s)
        f1 = _finite(fp)
        if f1 is None or f1 <= 0.0:
            return None, STALE
        f0 = None
        if ref is not None:
            _first_seen, _page_ts, fps = ref
            f0 = _book_value(fps, book_id)
        if f0 is None or f0 <= 0.0:
            return None, NO_HISTORY
        side = held_side(f1 - f0)
        if side is None:
            return None, ZERO_MOVE
        return side, (FELL if side == SIDE_BUY else ROSE)
