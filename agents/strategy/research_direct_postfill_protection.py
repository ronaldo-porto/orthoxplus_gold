# SPDX-License-Identifier: MIT
"""A1.9.7: the ticks between an entry fill and the first exit evaluation.

Measured on the A1.9.6.1 run, log 20260913_053425, ticks 1-8,000 (600 round
trips).  579 of them were first evaluated two or more ticks after the fill that
opened them:

    first EXIT_DECISION after the opening fill   +1: 21  +2: 393  +3: 17  +4: 151  later: 18

Two skips in the inventory loop stack up.

POST-FILL CANCEL (P1).  On the tick after an entry fill the other entry quote is
still resting, so the loop cancels it and `continue`s past exit management: the
A1.7.4.3.1 rule that a cancel and a replacement never share a response.  Book 81
filled short at 276.01 on tick 102, marked -137 bps on tick 103 with no
evaluation, and exited at -597 bps on tick 104 -- 35% of the run's cubic
downside.  A taker exit is not a replacement: the frozen `_execute_aggressive_close`
already cancels every resting order on the book before it sends the market
order.  So a position whose mid mark is already inside ABSOLUTE_PROTECTION is
evaluated on that tick.  Every other band keeps the one-tick wait: HARD_ESCAPE
cannot clip before age 2 and would place a maker exit, which is exactly the
replacement the rule forbids.

ORDER IDENTITY (P2).  `canonical_order_side` read `str(side or "")`, and the
venue sends a buy as the integer 0.  Buy identities were registered with side "",
their pending key never matched the "buy" reservation, and a buy fill released
nothing: 161 of 161 same-state buy fills waited three ticks for LOCAL_EXPIRY,
while every sell released on the fill.  Those are the +4 rows.  The fix lives in
research_direct_book_ownership.py.

EXIT GAP (P3).  `A197_EXIT_GAP` records, per opened position, the fill tick, the
first managed tick, why each tick in between was skipped and the mark on it.  It
replaces `late_trigger` as the timing measure: that flag is true by construction
for every ABSOLUTE exit, because the band itself is a mark below -25 bps.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any, Iterable

from research_position_exit import BAND_ABSOLUTE, classify_risk_band

A197_POSTFILL_PROTECTION_VERSION = "direct_postfill_protection_v4_16_2_a1_9_7"

SKIP_INVENTORY_OPENED = "INVENTORY_OPENED"
SKIP_LIVE_ORDER = "LIVE_ORDER"
SKIP_NO_PROFILE = "NO_PROFILE"
SKIP_NOT_MANAGED = "NOT_MANAGED"

# Marks kept per gap row.  The measured gaps were at most three skipped ticks in
# 582 of 600 round trips; the rest are long holds, and their count still reports.
A197_MAX_GAP_MARKS = 8
# Open gap records are bounded; the oldest goes first.
A197_MAX_OPEN_RECORDS = 256
# A protective exit's position is watched this many ticks for a sign flip.
A197_FLIP_WATCH_TICKS = 5

LIMIT = "PLACE_ORDER_LIMIT"
MARKET = "PLACE_ORDER_MARKET"
CANCEL = "CANCEL_ORDERS"


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


# ---- P1: which positions the post-fill tick may evaluate ---------------------------

def position_mark_bps(*, net_base: Any, vwap_entry: Any, mid: Any, fallback: Any = None) -> float | None:
    """Mid mark-to-market of an open position in bps; `fallback` when it cannot be priced."""
    net = _finite(net_base, 0.0) or 0.0
    entry = _finite(vwap_entry)
    px = _finite(mid)
    if net == 0.0 or entry is None or entry <= 0.0 or px is None or px <= 0.0:
        return _finite(fallback)
    if net > 0.0:
        return (px - entry) / entry * 10_000.0
    return (entry - px) / entry * 10_000.0


def postfill_protect_eligible(mark_bps: Any) -> bool:
    """True only when the mid mark alone is already inside ABSOLUTE_PROTECTION.

    The exit decision bands on the taker-executable net, which is never better
    than the mid mark, so such a position is ABSOLUTE there too.  Whatever the
    decision then does, a maker exit is refused on this tick and the ordinary
    one-tick wait applies to it.
    """
    m = _finite(mark_bps)
    return m is not None and classify_risk_band(m) == BAND_ABSOLUTE


# ---- P1: the instructions one managed book produced ----------------------------------

def _book_of(instruction: Any) -> int | None:
    for name in ("bookId", "book_id"):
        raw = getattr(instruction, name, None)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None


def _new_for_book(instructions: Iterable[Any] | None, book_id: int, first_new: int) -> list[Any]:
    rows = list(instructions or ())
    return [i for i in rows[max(0, int(first_new)):] if _book_of(i) == int(book_id)]


def book_instruction_kinds(instructions: Iterable[Any] | None, *, book_id: int, first_new: int) -> set[str]:
    """Instruction types queued for one book at or after `first_new`."""
    return {str(getattr(i, "type", "") or "") for i in _new_for_book(instructions, book_id, first_new)}


def book_cancelled_order_ids(instructions: Iterable[Any] | None, *, book_id: int, first_new: int) -> set[int]:
    """Order ids named by cancel instructions queued for one book at or after `first_new`."""
    out: set[int] = set()
    for instruction in _new_for_book(instructions, book_id, first_new):
        if str(getattr(instruction, "type", "") or "") != CANCEL:
            continue
        for cancellation in getattr(instruction, "cancellations", None) or ():
            raw = getattr(cancellation, "orderId", getattr(cancellation, "order_id", None))
            try:
                out.add(int(raw))
            except (TypeError, ValueError):
                continue
    return out


def strip_book_limit_orders(instructions: list[Any] | None, *, book_id: int, first_new: int) -> int:
    """Remove limit placements queued for one book at or after `first_new`, in place.

    Cancels and market orders stay, and so does everything queued earlier or for
    another book.  Returns how many were removed.
    """
    if not isinstance(instructions, list):
        return 0
    start = max(0, int(first_new))
    kept = instructions[:start]
    removed = 0
    for instruction in instructions[start:]:
        if _book_of(instruction) == int(book_id) and str(getattr(instruction, "type", "") or "") == LIMIT:
            removed += 1
            continue
        kept.append(instruction)
    if removed:
        instructions[:] = kept
    return removed


def sign_flipped(before: Any, after: Any, eps: Any) -> bool:
    b = _finite(before, 0.0) or 0.0
    a = _finite(after, 0.0) or 0.0
    e = abs(_finite(eps, 0.0) or 0.0)
    return b * a < -(e * e)


# ---- P3: the exit gap -----------------------------------------------------------

@dataclass
class ExitGap:
    fill_tick: int
    skips: list[str] = field(default_factory=list)
    skip_ticks: list[int] = field(default_factory=list)
    marks: list[float | None] = field(default_factory=list)
    skipped: int = 0


def open_gap(records: dict[int, ExitGap], book_id: Any, *, fill_tick: Any,
             max_records: int = A197_MAX_OPEN_RECORDS) -> ExitGap:
    """Start a gap at an opening fill.  A newer fill on the same book replaces the record."""
    key = int(book_id)
    records.pop(key, None)
    row = ExitGap(fill_tick=int(_finite(fill_tick, 0.0) or 0))
    records[key] = row
    limit = max(1, int(max_records))
    while len(records) > limit:
        records.pop(next(iter(records)), None)
    return row


def note_gap_skip(records: dict[int, ExitGap], book_id: Any, *, tick: Any, reason: str,
                  mark_bps: Any, max_marks: int = A197_MAX_GAP_MARKS) -> bool:
    """Record one skipped tick for an open gap.  At most one skip per tick counts."""
    row = records.get(int(book_id))
    if row is None:
        return False
    now = int(_finite(tick, 0.0) or 0)
    if row.skip_ticks and row.skip_ticks[-1] == now:
        return False
    if now <= row.fill_tick:
        return False
    row.skipped += 1
    if len(row.skip_ticks) < max(0, int(max_marks)):
        row.skips.append(str(reason))
        row.skip_ticks.append(now)
        mark = _finite(mark_bps)
        row.marks.append(None if mark is None else round(mark, 1))
    else:
        # Keep the last tick even when the detail is full, so repeats are still refused.
        row.skip_ticks[-1] = now
    return True


def close_gap(records: dict[int, ExitGap], book_id: Any, *, eval_tick: Any, p1_acted: bool) -> dict[str, Any] | None:
    """End the gap at the first managed evaluation and return its log row."""
    row = records.pop(int(book_id), None)
    if row is None:
        return None
    now = int(_finite(eval_tick, 0.0) or 0)
    mark_t1 = None
    for tick, mark in zip(row.skip_ticks, row.marks):
        if tick == row.fill_tick + 1:
            mark_t1 = mark
            break
    return {
        "a197_postfill_protection_version": A197_POSTFILL_PROTECTION_VERSION,
        "book": int(book_id),
        "fill_tick": int(row.fill_tick),
        "eval_tick": now,
        "gap_ticks": max(0, now - int(row.fill_tick)),
        "skipped_ticks": int(row.skipped),
        "skips": list(row.skips),
        "skip_ticks": list(row.skip_ticks),
        "marks_bps": list(row.marks),
        "mark_t1_bps": mark_t1,
        "p1_acted": int(bool(p1_acted)),
    }
