# SPDX-License-Identifier: MIT
"""v6.0.3: the partial-fill recovery handler stops owning a short lot after its hold.

Measured on UID 68 (v6.0.2 at 8 books, testnet, ticks 0 to 5,123, 2026-09-17):

* 19 short-lot episodes, 9 of them longer than 50 ticks (books 107: 305, 127: 212, 74: 189).
* 121 ``A173_PARTIAL_REMAINDER_CANCEL`` rows on those positions, every one with
  ``mode: EXIT_COMPLETE`` and ``bound_order_id: null`` -- the hold had already expired.
* 9 of the 19 ended in a forced exit (aggressive maker, hard escape clip, taker or ABSOLUTE
  protection); only 4 went flat cleanly.

The cause is one predicate.  ``_direct_service_partial_fill_recovery`` releases a recovery row
only when the position is not ``research_direct_liveness.is_dust_inventory`` -- the pre-v6.0.0
rule ``eps < |q| < min_order``.  v6.0.0 made a position of ``boundary <= |q| < min_order`` a
**short lot** exited like a full lot, so the row survives, and with no bound order left every
order on the book counts as conflicting: the v6.0.0 lot exit placed in the previous request is
cancelled at the next one, forever.

The bound hold itself is right and does not move: while an exact partially-filled remainder may
still be live (at most ``DIRECT_PARTIAL_HOLD_MAX_NS``), only that order may rest.  The short lots
it protected cleared in 0 to 3 ticks.  Only the unbound branch is wrong for a short lot, and the
fix is to own nothing there: release the row and cancel nothing, because v6.0.0 already gave that
position to the lot exit.

Dust under the v6.0.0 boundary keeps the v6.0.2 path exactly, including the normalizer cancel
(that is v6.0.4's subject).  Parked inherited lots count as dust, so they are unaffected.

Pure functions only; the strategy owns the state.
"""
from __future__ import annotations

from typing import Any

V603_RECOVERY_VERSION = "short_lot_release_v6_0_3"
V603_STATE_EVERY_TICKS = 100

# What the handler does with one recovery row this request.
DISPOSITION_HOLD = "HOLD"          # a bound remainder may still be live: v6.0.2 behaviour.
DISPOSITION_RELEASE = "RELEASE"    # v6.0.3: a short lot with no bound order.  Pop, cancel nothing.
DISPOSITION_LEGACY = "LEGACY"      # dust with no bound order: v6.0.2 behaviour.


def row_disposition(*, enabled: bool, hold_active: bool, bound: bool, counts_as_dust: bool) -> str:
    """Decide one recovery row.

    ``bound`` means the hold is active *and* the row names an order that may still be resting;
    that is the only case the hold protects, so an active-but-unbound row is not a hold.
    ``counts_as_dust`` is the v6.0.0 predicate, which the caller owns (it knows parked lots).
    """
    if hold_active and bound:
        return DISPOSITION_HOLD
    if enabled and not counts_as_dust:
        return DISPOSITION_RELEASE
    return DISPOSITION_LEGACY


def release_is_safe(*, disposition: str, cancelled_orders: Any) -> bool:
    """A release must send no cancel.  Used by the tests and the state row's invariant."""
    if disposition != DISPOSITION_RELEASE:
        return True
    try:
        return int(cancelled_orders or 0) == 0
    except (TypeError, ValueError):
        return False
