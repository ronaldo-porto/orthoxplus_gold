# SPDX-License-Identifier: MIT
"""v5.0.2: dust liveness from inventory truth and terminal-order truth.

Measured on UID 18's RealNet run (log 20260915_063311, v5.0.0).  Admission read zero slots on 199
of 423 A196_ADMISSION samples, and on 50 of the 53 from tick 9,225, where round trips stopped.  The
binding term was the BASE budget: 3.54 BASE counted as dust against a 2.0 BASE dust class, 1.54 of
overflow charged as productive exposure, 0.213 of headroom under the 0.25 minimum order.  Three
defects put full positions in that class, and the compactor that should drain the rest never
reached it.  A static replay with only the first two removed leaves zero slots on 34 samples, all
before tick 2,500, where the active-book cap binds.  Nothing here is a threshold fitted to that log.

F1  A LIFECYCLE ENDS AT ZERO.  A round trip closes once the tracker is inside the execution flat
    epsilon (half a volume unit), and what the tracker still held rode into the next entry.  The
    venue settles on its own grid, so the residue is not there -- but a full 0.25 entry on top of
    an opposite residue reads 0.24997, below the minimum order, and is parked as dust with no exit
    logic.  Book 121: a 0.2501 exit left +0.0000276, the next 0.25 sell read -0.2499724 against a
    venue -0.25.  46 of the run's 52 near-full dust positions began that way, 35% of all dust
    BASE x ticks, and books 116 and 85 held both compactor slots for ~4,000 ticks.  The residue now
    moves to a residue ledger that every reader of local base adds back (reconciliation, the rewind
    reseed, exposure), so local base is unchanged and the tracker is exactly zero.

F2  A CLIP IS A CLIP.  A rewind reseed rebuilds each diverged book from venue truth, and A1.9.9 sent
    every target below 0.25 to the ledger, where nothing exits it.  At tick 7,715 that froze five
    clips one or two base units short (books 36, 37, 85, 94 and 116; 1.2494 BASE) for the rest of
    the run, charged as dust.  The startup seed draws the same boundary.  A venue position at most
    V502_CLIP_RESIDUE_UNITS base units below the minimum order is now one clip of exactly min_order
    in the tracker, exited by the normal exit logic, and the shortfall goes to the residue ledger.
    Two units: one the venue's truncation takes from an off-grid fill, one a positive maker fee
    takes when it is rounded up to a whole unit.

F3  A REFUSAL COSTS THE TURN.  The compactor evaluates the two best-scoring parked books each tick
    and puts a book in cooldown only after an attempt.  A book the Kappa loss floor refuses is not
    an attempt, so it kept its place: two books were evaluated on 10,083 of 10,408 ticks and they
    were refused ones throughout (48 and 58, then 116 and 85, then 21 and 76), while books 25, 44,
    77 and 80 were never evaluated in 6,700-10,000 ticks.  A refusal now takes the same backoff as
    a failed attempt.  The floor itself is unchanged.

F4  A MARKET ORDER ENDS WHEN THE VENUE PROCESSES IT.  The venue matches a market order on arrival,
    and its placement notice and any fills arrive in the same state.  The local reservation still
    held the book until DIRECT_PENDING_MARKET_FALLBACK_TICKS ran out, so an unfilled exit was resent
    every 4 ticks: book 82 sent 5 market orders over 16 ticks while the loss at its taker estimate
    ran from -119 to -332 bps, and 4 over 12 ticks.  After the notice, a book whose position is
    still open is released so the exit can resend on that request.  A filled exit keeps the hold.
"""
from __future__ import annotations

import math
from typing import Any

from research_direct_book_ownership import canonical_order_side

V502_DUST_LIVENESS_VERSION = "direct_dust_liveness_v5_0_2"
V502_STATE_EVERY_TICKS = 100
# Below this a residue is arithmetic, not a quantity: zeroed, not recorded.
V502_FLOAT_NOISE_BASE = 1e-9
# F2: base units a maker-filled clip can lose at settlement -- one truncated from an off-grid fill,
# one a positive maker fee takes when rounded up to a whole unit.
V502_CLIP_RESIDUE_UNITS = 2

RESIDUE_NONE = "NONE"
RESIDUE_NOISE = "NOISE"
RESIDUE_FLAT = "FLAT_RESIDUE"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def flat_residue(net: Any, *, flat_eps: Any, noise: float = V502_FLOAT_NOISE_BASE) -> tuple[str, float]:
    """F1: what a flat book's tracker still holds, and what to do with it.

    NONE when the tracker is exactly zero or not flat (the round-trip test is ``abs(after) < eps``);
    NOISE for float arithmetic, which is zeroed and not recorded; FLAT_RESIDUE otherwise, which
    moves to the residue ledger.
    """
    q = _finite(net)
    eps = abs(_finite(flat_eps))
    if q == 0.0 or abs(q) >= eps:
        return RESIDUE_NONE, 0.0
    if abs(q) <= abs(_finite(noise, V502_FLOAT_NOISE_BASE)):
        return RESIDUE_NOISE, q
    return RESIDUE_FLAT, q


def clip_tolerance_base(decimals: Any, units: int = V502_CLIP_RESIDUE_UNITS) -> float:
    """F2: how far below the minimum order a venue position is still one clip, in BASE."""
    try:
        d = int(decimals)
    except (TypeError, ValueError):
        return 0.0
    if d < 0:
        return 0.0
    return max(0, int(units)) * 10.0 ** (-d)


def clip_split(net: Any, *, min_order: Any, tolerance: Any) -> tuple[float, float] | None:
    """F2: split a near-full venue position into one clip and its settlement shortfall.

    Returns ``(lot, residue)`` with ``lot = +/-min_order`` and ``residue = net - lot`` (opposite
    sign, at most ``tolerance``), or None when ``net`` is not within ``tolerance`` below a clip.
    """
    q = _finite(net)
    m = abs(_finite(min_order))
    tol = abs(_finite(tolerance))
    if m <= 0.0 or tol <= 0.0 or q == 0.0:
        return None
    short = m - abs(q)
    if short <= 1e-12 or short > tol + 1e-12:
        return None
    lot = m if q > 0.0 else -m
    return lot, q - lot


def refusal_cooldown_ticks(streak: Any, *, base: Any, maximum: Any) -> int:
    """F3: the compactor's own backoff, applied to a refusal streak."""
    b = max(1, int(base))
    top = max(b, int(maximum))
    return int(min(top, b * max(1, int(streak))))


def unique_market_reservation(ledger: dict | None, *, book_id: Any, side: Any) -> tuple[Any, int]:
    """F4: the one local market-order reservation on a book and side.

    Returns ``(key, matches)``; the key is None unless exactly one reservation matches.  More than
    one is refused, as every other identity release refuses ambiguity.
    """
    token = canonical_order_side(side)
    matches = []
    for key, row in (ledger or {}).items():
        try:
            if int(key[0]) != int(book_id):
                continue
            if canonical_order_side(key[2]) != token:
                continue
        except (TypeError, ValueError, IndexError):
            continue
        if "MARKET" not in str(getattr(row, "order_kind", "") or "").upper():
            continue
        matches.append(key)
    return (matches[0] if len(matches) == 1 else None), len(matches)
