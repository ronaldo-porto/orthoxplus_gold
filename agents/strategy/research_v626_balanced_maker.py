# SPDX-License-Identifier: MIT
"""v6.2.6: clean closes and balanced capture -- the two legs of the trading score, at the cap pace.

Why (v6.2.5 on UID 82 to tick 3,026, 2026-09-20; the validator's own per-book gauges and our mirror):

* The pace works: books sit at pace ratio 1.07, volume 6.4M per 3 sim-h window against 1.1M for v6.2.4,
  128 books with fills, 158 closes per book per window -- the cadence a kappa of ~0.72 needs.
* But kappa is still 0.4996, because only 16 of 128 books have a window free of losing closes.  Kappa is
  the MEDIAN over scored books, so what matters is not how many losses there are but on how many books:
  a book that has already realised one loss in the window is at ~0.4996 whatever it does next, while the
  first loss on a clean book is what moves the median.  Rule A spends clean books only when they are
  stuck, and only while the median book stays clean.
* And balance fell to 37% from 50%: making counts 2·min(buy capture, sell capture) per book, v6.2.5 adds
  volume on the side the book is already long or short, and an entry fill captures about nothing (+0.6 /
  -2.3 bps against the centred mid, where exits capture +2 to +19).  So the dominant side grew and the
  scoring side did not; 30 books ended with a negative smaller side, -136.7 against +511.8.  Rule B sizes
  each side by which side's capture is behind, and stops adding entirely on a book whose smaller side has
  gone negative.
* Entry quotes live 0.5 s and fill 3.6% of the time; exits rest 4 s and fill 11.1%.  Rule C gives every
  touch quote the exit's life: more fills per placement, which lets the pacing controller hold a SMALLER
  clip at the same volume -- and a smaller clip is more closes per book, which is the kappa leg again.

Two defects the same read found, fixed here without a switch because they only remove placements the
frozen guard was already rejecting (7,260 of them):

* ``OPEN_BOOK_CAP`` (4,517): the portfolio's open-book cap is the universe count, but the guard counts a
  book that is dust on its own measure and flat on ours both as already open and as being opened by this
  request.  The caps are therefore taken at twice the universe, which can never admit more than the books
  that exist.  It hit the highest book ids -- the ones the request reaches last.
* ``SAME_REQUEST_BOOK_SIDE_OWNED`` (2,743): a touch quote went out on a side this response had already
  instructed (the exit path's own order on a book our inventory view reads as flat).  The placement now
  checks the response first.
"""
from __future__ import annotations

import math
from typing import Any

V626_BALANCED_MAKER_VERSION = "balanced_maker_v6_2_6"

# The guard's open-book counter can count one book twice (held on its measure, opened on ours).
CAPS_UNIVERSE_MULTIPLE = 2

SIDE_BUY = "buy"
SIDE_SELL = "sell"

REASON_OK = "OK"
REASON_SURPLUS = "CAPTURE_SURPLUS"        # this side's capture already leads; the other side is behind
REASON_SIDE_OWNED = "SIDE_OWNED"          # this response already instructed this book side


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


# ---- rule A: the loss budget under the median ----------------------------------------------------

def median_budget(*, premium: int, loss: int) -> int:
    """How many more clean books may take their first loss while the median book stays clean.

    ``kappa_score`` is the median over scored books, and a book with a loss in the window is at ~0.4996.
    So the median is clean while the books carrying a loss are fewer than half of the scored ones.
    """
    scored = max(0, int(premium)) + max(0, int(loss))
    if scored <= 0:
        return 0
    return max(0, (scored - 1) // 2 - max(0, int(loss)))


def spend_allowed(*, status_is_loss: bool, blocked: bool, budget: int) -> bool:
    """May this book's exit go out below break-even?

    A book already carrying a loss is free: its kappa is spent for this window.  A clean book is spent
    only when it is stuck -- at the inventory band, unable to add -- and only while the median holds.
    """
    if status_is_loss:
        return True
    return bool(blocked) and int(budget) > 0


def book_blocked(*, net_base: Any, clip: Any, band: Any) -> bool:
    """True when the book cannot add: one more clip would take it past the band."""
    return (abs(_finite(net_base)) + max(0.0, _finite(clip))) > max(0.0, _finite(band)) + 1e-12


# ---- rule B: capture balancing -------------------------------------------------------------------

def balance_ratio(buy: Any, sell: Any) -> float:
    """(buy − sell) / (|buy| + |sell|) in [-1, 1].  0 when the book has no capture yet."""
    b, s = _finite(buy), _finite(sell)
    denom = abs(b) + abs(s)
    if denom <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, (b - s) / denom))


def side_clips(
    *,
    clip: Any,
    buy_capture: Any,
    sell_capture: Any,
    min_order: Any,
    lots_of,
) -> dict[str, float]:
    """The clip for each side: the deficit side larger, the surplus side smaller, and 0 = do not quote.

    The objective is the validator's own 2·min(buy, sell) per book, so size goes where the smaller side
    is.  On a book whose smaller side has gone negative, the leading side stops entirely until the other
    one recovers -- adding there cannot raise the minimum and the book is already subtracting from the
    total.
    """
    base = max(0.0, _finite(clip))
    unit = max(1e-12, _finite(min_order, 0.25))
    b, s = _finite(buy_capture), _finite(sell_capture)
    r = balance_ratio(b, s)
    out = {SIDE_BUY: base, SIDE_SELL: base}
    if base <= 0.0:
        return {SIDE_BUY: 0.0, SIDE_SELL: 0.0}
    if max(b, s) < 0.0:
        # both sides are losing capture on this book: adding cannot raise the minimum, so the book
        # is left to its exit until one side recovers.
        return {SIDE_BUY: 0.0, SIDE_SELL: 0.0}
    if abs(r) > 1e-12:
        deficit, surplus = (SIDE_SELL, SIDE_BUY) if r > 0.0 else (SIDE_BUY, SIDE_SELL)
        out[deficit] = lots_of(base * (1.0 + abs(r)), unit)
        out[surplus] = lots_of(base * (1.0 - abs(r)), unit)
        if min(b, s) < 0.0:
            out[surplus] = 0.0
    return out


# ---- the two defect fixes -------------------------------------------------------------------------

def _direction_token(value: Any) -> str:
    """'buy' / 'sell' from an OrderDirection, an int, or a string."""
    name = getattr(value, "name", None)
    if isinstance(name, str) and name:
        return name.strip().lower()
    if isinstance(value, str):
        return value.strip().lower()
    try:
        return SIDE_BUY if int(value) == 0 else SIDE_SELL
    except (TypeError, ValueError):
        return ""


def side_already_instructed(response: Any, book_id: Any, side: Any) -> bool:
    """True when this response already carries an instruction for this book on this side."""
    want = _direction_token(side)
    if not want:
        return False
    try:
        book = int(book_id)
    except (TypeError, ValueError):
        return False
    for ix in (getattr(response, "instructions", None) or []):
        try:
            if int(getattr(ix, "bookId", -1)) != book:
                continue
        except (TypeError, ValueError):
            continue
        other = _direction_token(getattr(ix, "direction", None))
        if other and other == want:
            return True
    return False


def open_book_caps(caps: dict[str, Any], *, multiple: int = CAPS_UNIVERSE_MULTIPLE) -> dict[str, Any]:
    """The universe caps with the book COUNTS widened for the guard's double count.

    The BASE cap is untouched -- it is a real exposure bound.  The counts are not: they cannot admit more
    books than the universe holds, they only stop the guard refusing the book this request is opening.
    """
    out = dict(caps)
    m = max(1, int(multiple))
    for key in ("research_max_total_open_books", "research_max_active_open_books",
                "research_max_open_books", "max_managed_books_per_tick", "max_mm_books_per_tick"):
        if key in out:
            try:
                out[key] = int(out[key]) * m
            except (TypeError, ValueError):
                pass
    return out


def skip_reason(sides: dict[str, str], *, exit_side_token: str) -> str:
    """The reason to report when no side was quoted: the binding one, not 'the exit owns that side'."""
    for side in (SIDE_BUY, SIDE_SELL):
        why = sides.get(side)
        if why and why != exit_side_token:
            return why
    return exit_side_token
