# SPDX-License-Identifier: MIT
"""v6.22: aged books near and at the cap -- research_v622_aged_cap.

Why (mainnet 10-08 JST; scratchpad k44: the calibrated replay vm644, which reproduces UID 237's live making, alpha and
capture per order role on its own tape, and its R2 runs; the live V62_STATE of UIDs 237 and 251 on v6.21):

* S1.  An aged book (its window rolls off, so v6.20 / v6.21 hand it back to the v6.15 / v6.8 line) follows
  research_v68_deep_owns.rolling_line = min(cap, its first report rising at the sustainable rate).  That line reaches
  the cap within hours of a start, and from then on PaceLine.paced is "volume above the cap" -- false whenever roll-off
  frees room -- so the book trades unpaced however little room it has left.  Its adding orders pass v6.4.1's consumer,
  the v6.10 bound gives it the 48 base of a book behind its line instead of v6.9's 8, and the v6.17 touch quote adds:
  inventory ratchets, touch adds reach 34-46% of its volume and making per unit of capture collapses.  Replayed from
  UID 237's v6.21 restart (k44 R2b, sim 44,100-54,000): making 130.6 -> 223.7 (+71%), alpha -1,159 -> +311, gross
  capture per 1M 39.8 -> 75.3, touch adds 45.3% -> 4.7% of notional, mean |inventory| 31.4 -> 10.4 base, notional
  -16%, cap used at the end 99.0% -> 97.6%; making ahead in 4 of 6 1,800-s blocks (the first two -1% / -8%, then
  +12..+31%), alpha in 5 of 6.  From UID 237's earlier restart (R2a) +25% / +131% in its two later windows.  A young
  book (v6.21's even line) is untouched: UID 22's replay is byte-identical.  The v6.20 line aimed at T for the same
  books (k31 P5) is no substitute: +8.9%, touch adds stay at 37%.
* S2.  On a UID whose window started before its own logs (v6.21 UNKNOWN_NO_EMPTY_START: every start of the simulation
  it can read already shows volume -- the aged UIDs 237, 251, 187, 104), v6.21 keeps a book young on v6.20's room line
  until its volume falls by at least one minimum fill.  A book first seen at or above T sits on a flat line at that
  reading and trades at the size factor of its lead, and one whose oldest buckets are empty (it traded nothing an
  assessment period ago) does not fall: 8-12 books per UID on 237 and 251 were still young 14-17 h after the 10-08
  restart, at a size factor ~0.23.
* S3.  The validator refuses every non-cancel instruction for a book at its volume cap, and the agent's final validator
  already drops each one (v6.6 S4, research_v66_cap_closed) with a FINAL_CONTRACT_REJECT row: on the aged UIDs ~95
  placements a request were built and dropped against ~19 sent, ~35% of their log bytes.

The rules (three switches; without them the build is v6.21):

* S1 ``research_v622_cap_window_pace``: a book on the v6.8 rolling line -- aged (v6.20's young() is False), or any book
  of a rolling line without young() while v6.20's room line is off -- whose room under the cap is below one making
  window of it (v6.19's cap_bound: cap - volume < cap x 10,800 / 86,400) is paced.  The rest follows from the paced
  state as it always has: v6.4.1 holds its adding orders, v6.10 gives it v6.9's bound (4 deep clips = 8 base), v6.9 S3
  keeps only the deepest ladder level on its adding side and the v6.17 touch quote places no adding quote.  Order sizes
  are untouched (v6.20's size factor is 1 for an aged book).
* S2 ``research_v622_unknown_aged``: on such a UID, once its window is seen to roll, every book without a window start
  of its own is aged at its next report (a book the session file brought back, at its first state).  Seen to roll: a
  book of the UID is aged while v6.21's roll-off guard is on -- a fall of at least one minimum fill on a state whose
  clock moved on, here or in the saved state (UIDs 237 / 251 restore ~120 aged marks, so their young books age at the
  first state).  The status alone is no proof: v6.21 reports it too when it cannot read a launch (a first report that
  saw fewer books than the state, an unreadable head, a missing log) and treats every unknown status as v6.20, which
  is safe; a young window cannot roll, so it is never aged this way.  The aged marks ride in the session file (v6.21
  S2).  A book first seen on an empty window has its own start (v6.15 / v6.21) and keeps v6.21's line; a known window
  start is unchanged.
* S3 ``research_v622_cap_skip``: while v6.6 S4 is on, a book at its cap (headroom 0: volume >= cap) builds no new order
  in the deep layer or the touch quote -- the final validator would drop every one, from the same state -- and keeps
  every cancel (a resting order still fills at the cap; a cancel is accepted).  One count per book-state instead of one
  row per order; the final validator stays the backstop.  The touch quote's cancel comes after the deep layer's
  placements and goes out only while the book's instructions in the response are below the per-book budget; leaving
  out the deep layer's new orders (it issues at most one cancel instruction and one new order per side) can change
  what that cancel sees only when count + 3 >= budget, so then the book builds as v6.21 does.  Nothing keeps a record
  of a placement the final validator drops: the pending ledger records only what passes it, and every per-request
  check is per book.

STRUCTURAL: the validator's volume cap and making window, v6.21's window start, the validator's refusal at the cap and
the per-book instruction budget.  No fitted constant.
"""
from __future__ import annotations

import math
from typing import Any

from research_v619_surplus_hold import cap_bound
from research_v621_window_start import UNKNOWN_NO_EMPTY_START
from research_v621_young_window import YoungWindowLine
from research_v68_deep_owns import RollingPaceLine

V622_AGED_CAP_VERSION = "aged_cap_v6_22"
DEEP_MAX_INSTRUCTIONS = 3       # the deep layer on one book and request: one cancel instruction, one new order per side


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ---- S1: pacing within one making window of the cap ----------------------------------------------------------------

def on_rolling_line(pace: Any, book_id: int, *, room_on: Any) -> bool:
    """Does this book follow the v6.8 rolling line?  A started book of a line that tells young from aged (v6.20+) once
    it is aged; every started book of a rolling line without young() while v6.20's room line is off."""
    if not isinstance(pace, RollingPaceLine):
        return False
    b = int(book_id)
    if b not in (getattr(pace, "start", None) or {}):
        return False
    young = getattr(pace, "young", None)
    if callable(young):
        return not bool(young(b))
    return not bool(room_on)


def cap_window_paced(pace: Any, book_id: int, cap: Any, used: Any, *, room_on: Any) -> bool:
    """S1: is this book paced for its cap -- on the v6.8 rolling line with less than one making window of the cap left
    (v6.19's cap_bound)?  False without a cap or a reading."""
    return on_rolling_line(pace, book_id, room_on=room_on) and cap_bound(cap, used)


# ---- S2: the books of a window that started before the logs ----------------------------------------------------------

def window_rolls(pace: Any) -> bool:
    """Has this UID's window been seen to roll?  A book of it is aged while v6.21's roll-off guard is on (only a fall of
    at least one minimum fill on a moving clock ages one, here or in the saved state); a young window cannot roll."""
    return bool(getattr(pace, "guard_on", False)) and bool(getattr(pace, "aged", None))


def ages_unknown(pace: Any, book_id: int) -> bool:
    """S2: does this book age now?  Its UID's window started before its own logs (v6.21 UNKNOWN_NO_EMPTY_START) and has
    been seen to roll, and the book has a line, no window start of its own (it was not first seen empty) and no aged
    mark yet."""
    if not isinstance(pace, YoungWindowLine) or getattr(pace, "window_status", None) != UNKNOWN_NO_EMPTY_START:
        return False
    if not window_rolls(pace):
        return False                    # no proof yet: the status alone is also what an unreadable launch leaves
    b = int(book_id)
    return b in pace.start and b not in pace.aged and pace.window_start_ns(b) is None


# ---- S3: no new order at the cap -------------------------------------------------------------------------------------

def at_cap(headroom: Any, cap_closed: Any) -> bool:
    """S3: does the final validator drop every placement on this book?  No headroom left under the volume cap, and
    v6.6 S4 (research_v66_cap_closed) drops a reducing order there too.  No reading is room, as the final validator
    reads it."""
    h = _finite(headroom)
    return bool(cap_closed) and h is not None and h <= 0.0


def skip_keeps_budget(count: Any, budget: Any) -> bool:
    """S3: can the deep layer's new orders be left out without changing what the touch quote's cancel sees?  That
    cancel goes out only while the book's instructions in the response are below the budget, and it comes after the
    deep layer's (at most DEEP_MAX_INSTRUCTIONS)."""
    try:
        return int(count) + DEEP_MAX_INSTRUCTIONS < int(budget)
    except (TypeError, ValueError, OverflowError):
        return False


__all__ = ["DEEP_MAX_INSTRUCTIONS", "V622_AGED_CAP_VERSION", "ages_unknown", "at_cap", "cap_window_paced",
           "on_rolling_line", "skip_keeps_budget", "window_rolls"]
