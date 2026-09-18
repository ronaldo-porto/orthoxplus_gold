# SPDX-License-Identifier: MIT
"""v6.1: a state the validator never sent takes its fills with it -- repair from venue truth.

Measured on mainnet UID 34 (v6.0.2, log 20260917_214432, 2026-09-18):

* Ticks 1-3,794: every state exactly 1 s after the previous one; the A1.9.5 reconcile agreed with
  the venue on every book (1-3 books off by at most 0.00026 BASE, the fee charged in base).
* From tick 3,795 (09-18 06:35:39 wall): 147 states delivered twice (a 0 s step), each followed by
  a skipped state (a 2 s step).  The wall gap into a skip is 10.5 s against 5.2 s normally.
* Every trade notice that arrived -- 9,934 of them, keyed by (book, trade id); trade ids are
  per-book and collide across books -- was applied exactly once.  The fills lost were the ones
  inside the skipped states.  Book 72: BUY 0.25 @ 202.90 (order 5843308) placed at sim 77,854.08,
  state 77,856 never sent, our cancel at 77,856 answered "Order IDs 5843308 do not exist.", no
  trade notice for it anywhere, venue position +0.25.
* By tick 8,075 the venue held 15.4 BASE on 39 books that the tracker did not know about -- the
  validator's own per-book balances agree with the venue to the last unit -- while the reconcile
  reported "0 unresolved" (it classifies a divergence; it repairs one only after a clock rewind).
* Not the miner: latency on the request before a skip 93.3 ms, the run median 92.5 ms; requests
  100/100 successful.  Testnet (UID 68, 11,162 states) never skipped a state.

The fix reuses the A1.9.9 reseed, which rebuilt six books cleanly after UID 68's live -31 s rewind:

* a forward gap in the state clock opens the A1.9.9 resync for exactly one pass -- every book is
  planned against venue truth and a diverged one is rebuilt -- and closes before any decision, so
  no entry is blocked;
* independently, a book whose venue and tracker disagree by at least one lot at two consecutive
  checks is handed to the A1.9.9 deferred reseed, whatever the cause.

A repeated state changes nothing here: its notices are the previous state's (the de-duplicator
drops them) and nothing is lost until the gap that follows it.

Pure functions only; the strategy owns the state.
"""
from __future__ import annotations

import math
from typing import Any, Iterable, Mapping

V61_STATE_GAP_VERSION = "state_gap_repair_v6_1_0"
V61_GAP_STATE_EVERY_TICKS = 100
# The reconcile's own cadence: a divergence is judged on the samples it is reported on.
V61_DIVERGENCE_CHECK_EVERY_TICKS = 25
# One sample can straddle a fill whose notice is in flight; two cannot.
V61_DIVERGENCE_CONFIRMATIONS = 2

STEP_FIRST = "FIRST"
STEP_NORMAL = "NORMAL"
STEP_GAP = "GAP"          # the clock moved more than one publish interval: states were skipped
STEP_REPEAT = "REPEAT"    # the same state again
STEP_REWIND = "REWIND"    # the clock went back: A1.9.9's case, not this module's

DEFAULT_STEP_NS = 1_000_000_000


def _int(value: Any) -> int | None:
    try:
        v = int(value)
    except (TypeError, ValueError):
        return None
    return v


def classify_state_step(prev_ts: Any, ts: Any, *, step_ns: Any = DEFAULT_STEP_NS) -> tuple[str, int]:
    """(step class, states missing between the two) for consecutive state timestamps."""
    now = _int(ts)
    last = _int(prev_ts)
    if now is None or now <= 0:
        return STEP_NORMAL, 0
    if last is None or last <= 0:
        return STEP_FIRST, 0
    step = _int(step_ns) or DEFAULT_STEP_NS
    if step <= 0:
        step = DEFAULT_STEP_NS
    delta = now - last
    if delta == 0:
        return STEP_REPEAT, 0
    if delta < 0:
        return STEP_REWIND, 0
    if delta > step:
        # Round, not floor: a timestamp a few ns off the grid is still one state.
        missing = max(1, int(round(delta / step)) - 1)
        return STEP_GAP, missing
    return STEP_NORMAL, 0


def diverged_books(
    venue_by_book: Mapping[int, Any],
    tracker_by_book: Mapping[int, Any],
    *,
    min_order: float,
    tolerance: float = 1e-9,
) -> set[int]:
    """Books where venue and tracker disagree by at least one lot."""
    out: set[int] = set()
    lot = abs(float(min_order or 0.0))
    if lot <= 0.0:
        return out
    for raw_id, venue in venue_by_book.items():
        try:
            book = int(raw_id)
            v = float(venue)
            t = float(tracker_by_book.get(book, 0.0) or 0.0)
        except (TypeError, ValueError):
            continue
        if not (math.isfinite(v) and math.isfinite(t)):
            continue
        if abs(v - t) + tolerance >= lot:
            out.add(book)
    return out


def confirmed_divergence(previous: Iterable[int] | None, current: Iterable[int]) -> set[int]:
    """Books diverged at this check AND the one before it."""
    prev = {int(b) for b in (previous or ())}
    return {int(b) for b in current if int(b) in prev}
