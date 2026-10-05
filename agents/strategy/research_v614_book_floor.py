# SPDX-License-Identifier: MIT
"""v6.14: the deep layer opens a book on its depth and its inventory, not on its paper alpha.

Why (mainnet, sims 20260924_1653 and 20260929_2015, replayed 10-04 JST with the validator's arithmetic; scratchpad v614/
vm614d.py, gate.sh, gate_an.py):

* Since 09-29 the validator pays 0.5 x the making share (captured spread, 2 x min(buy, sell) per book) + 0.5 x the
  share of positive net alpha among uids with positive skill on >= 20 books.  Negative alpha is unpaid, not charged
  against the making half: a book's making pays whatever its alpha.
* v6.3.3's per-book rule (kept by v6.8 S2 / v6.11) closes a book whose own paper alpha is under -OWN_FLOOR_FRACTION x
  the floor (0.5 x 30 = -15).  The 30 is the 0.6.2 pool-relative skill floor the validator removed on 09-29.  In this
  simulation's quiet trend the rule kept 24% of book-states closed (deep_book_open_share 0.758; 12% in the previous,
  richer simulation), the main source of book-states with no order besides the volume cap.
* Replayed on both whole simulations, v6.12 -> v6.12 without the paper-alpha condition (making share per validator
  window, the field from every print):  this simulation +16.6% (quiet trend 0.493 -> 0.574%, book-states with no order
  26.7% -> 9.5%); previous simulation +8.1% (rich hours 3.540 -> 3.876%), eligible positive alpha x p11 +5% (20 of 23
  windows eligible against 22); worse only in the previous simulation's last, cap-exhausted drought windows
  (1.054 -> 0.744%), where the extra volume reached the cap sooner.

The rule (one switch, ``research_v614_book_floor_off``): the deep layer's ``book_open`` keeps its other conditions (the
book's depth is known; its inventory within the layer's bound where the caller asks for it) and drops the paper-alpha
condition.  STRUCTURAL: the pay rule has no per-book alpha floor for the making half; no number is fitted.
"""
from __future__ import annotations

from typing import Any

V614_BOOK_FLOOR_VERSION = "book_floor_off_v6_14"


def paper_floor_on(switch_on: Any) -> bool:
    """Does the deep layer's per-book paper-alpha floor still apply?  Only with the v6.14 switch off."""
    return not bool(switch_on)
