# SPDX-License-Identifier: MIT
"""v6.27 S7: the touch quote holds to the lean centre -- research_v627_touch_centre.

Why (UID 115 on v6.25 / v6.26, 10-12 / 10-13 JST; scratchpad k65, replay vm665 calibrated against the live tape):

* v6.25 S5 makes every making decision of the pass read (inventory - tau), tau the book's fundamental lean.  The v6.17
  touch quote -- ONE post-only order joining the touch on the side that trades toward the fundamental -- adds while a
  fill of one touch clip keeps |inventory - tau| inside v6.10's bound.  For a book PACED (ahead of its volume line)
  v6.4.1 already places no adding touch quote, so it never adds past the centre; for a book BEHIND its line that bound
  is 24 deep clips (48 base), so the touch kept buying up to tau + 48: on book 95 the touch alone bought +57.5 base
  while tau was +2..+8.  The excess was adversely selected -- books 95 / 73 / 28 carry 99% of the skill (kappa)
  downside term -- and 115's skill fell 0.85 -> 0.17.  In the exact live decomposition (alpha = lean leg + residual of
  inv - tau) the lean leg earned +7,713 and the residual cost -6,524; the touch's fills that grew |inv - tau| cost
  -2,060 of it.
* Replayed (vm665 = the live-calibrated engine, $V626 + ltbt=2 = this rule: the touch's band on inv - tau is one touch
  clip while the lean is armed; window [39,998, 50,798) sim-s, the validator's own pay arithmetic, against v6.26):
  E +15.6% at the live order delay (39 ms), +17.7% / +12.3% at 29 / 49 ms, +12.8% with S6 off, +13.6% with pages
  uninterpolated -- every third of the window ahead (min +2.2%); skill 0.42-0.54 -> 0.92-1.32 with the kept books
  unchanged; net alpha +25-30%; |inventory| max 94 -> 53 base; 90-min windows with skill <= 0: none (min +0.53).
  Making RISES: the touch's volume -18% but its capture +18%, the deep layer's making +19% -- a book held near its
  centre trades both sides instead of buying one-sided at the touch.

The rule (one switch, ``research_v627_touch_centre``, default on):

* While S5 leans the book THIS request (its tau != 0), the touch quote's bound is ONE touch clip -- the clip the quote
  is sized with -- instead of v6.10's bound (never above it): the side toward the fundamental adds only while
  (inventory - tau) + clip <= clip, i.e. never past the lean centre (the rule a paced book already follows), and a
  resting touch quote whose fill would add past the centre is cancelled by the quote's own room check.  The side that
  reduces |inventory - tau| is untouched (v6.17's room never limits it).
* The lean is the one S5 set for the book in the same request: the pass notes it right after S5 sets it, from S5's own
  (tau, state time) record and only at this request's state time, and starts every request's notes empty -- a lean of
  an earlier request never arms the rule.
* Without a lean (inside S1's 2.3-bps gap, no fresh page, S5 off, a book of another asset class, an error) the touch
  quote is v6.26's exactly; the deep layer, S1, S5 and S6 are unchanged.

STRUCTURAL: the bound is the touch quote's own clip (V617 CLIP_DEEP_CLIPS x the deep clip: 2.0 base live) and the
centre is S5's tau -- no new number; capping it by v6.10's bound keeps S7 a tightening only.  OBSERVED: the evidence,
which is the fresh UID's young phase only (tape 39,304-51,272 sim-s, 37% of book-states behind the line); on aged or
paced books the rule was already in force.
"""
from __future__ import annotations

import math
from typing import Any

V627_TOUCH_CENTRE_VERSION = "touch_centre_v6_27"

__all__ = ["V627_TOUCH_CENTRE_VERSION", "request_lean", "touch_bound"]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def request_lean(entry: Any, now_ts: Any) -> float:
    """S5's lean for a book THIS request: tau from S5's (tau, state time) record when the record carries this request's
    state time ``now_ts`` and tau is a finite number other than 0; 0.0 otherwise -- no lean, a record of an earlier
    request, or anything unreadable.  Never raises."""
    try:
        tau, ts = entry
        if isinstance(ts, bool) or isinstance(now_ts, bool) or int(ts) != int(now_ts):
            return 0.0
    except Exception:
        return 0.0
    t = _finite(tau)
    return t if t is not None and t != 0.0 else 0.0


def touch_bound(bound: Any, clip: Any) -> Any:
    """The touch quote's bound while the lean is armed: one touch clip, never above v6.10's ``bound``; ``bound`` itself
    when either is not a usable number (a clip that is not a positive number, a bound that is not a number).  Never
    raises."""
    b, c = _finite(bound), _finite(clip)
    if b is None or c is None or c <= 0.0:
        return bound
    return min(b, c)
