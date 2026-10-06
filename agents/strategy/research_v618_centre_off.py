# SPDX-License-Identifier: MIT
"""v6.18: the v6.16 fundamental centre is switched off; the v6.17 touch quote keeps the published fundamental.

Why (mainnet 10-06 JST; scratchpad k25/: replays vm620 / vm625 with the validator's arithmetic and the true pay rule,
score S = 0.5 making share + 0.5 alpha share against the field, every arm against the live v6.14 build on the same tape):

* v6.17 = v6.14 + v6.15 launch + v6.16 centre + v6.17 touch quote.  Taken apart, the touch quote carries the whole gain
  and the centre is a net drag wherever the rolling volume cap binds:
    previous simulation (quiet trend, 7 full windows)          v6.17 +27.7% (ahead 6/7)   without the centre +57.3% (7/7)
                                                               centre alone +16.4% (6/7)
    this simulation, an aged UID switched from v6.14            v6.17  -7.3% (2/4)         without the centre +41.0% (4/4)
    an aged UID entering a new simulation after running the     v6.17 -27.2%               without the centre  -4.5%
      build through the whole previous one
    a fresh UID (empty cap, no launch)                          v6.17 +212%                without the centre +171%
    a fresh UID with launch mode, against v6.17 with it         -9.2% in its first making windows, +3.3% after
* Mechanism: the centre moves the toward side's ladder toward the fundamental; its deepest level then fills about twice
  as often at 56-67 capture per 1M volume instead of 88-103.  While a book's cap has room that is extra volume; once the
  cap binds it spends the book's 500k per assessment period on its lowest-capture fills (cap-dark book-states 52% vs 35%
  in the previous simulation) and doubles the order traffic (67.7 vs 37.5 orders per state on a fresh UID).
* Gating the centre on cap room instead (on while the room covers one more making window at the book's own pace) kept
  the fresh gain but stayed 12-14 points behind on aged UIDs and needs per-book window accounting across restarts.

The rule (one switch, ``research_v618_centre_off``): the deep ladder is centred on the mid on every book, as v6.15.  The
v6.16 feed keeps running for the v6.17 touch quote, which reads it directly; without the switch the build is v6.17.

STRUCTURAL: removes the component measured to spend the validator's volume cap on its lowest-capture fills.
"""
from __future__ import annotations

from typing import Any

V618_CENTRE_OFF_VERSION = "centre_off_v6_18"


def centre_on(fair_value_on: Any, centre_off: Any) -> bool:
    """Whether the v6.16 centre applies on a book: its own switch is on and v6.18's is not."""
    return bool(fair_value_on) and not bool(centre_off)
