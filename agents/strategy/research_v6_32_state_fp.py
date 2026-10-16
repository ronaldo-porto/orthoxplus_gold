# SPDX-License-Identifier: MIT
"""v6.32 P1: each book's fundamental from the state itself -- research_v632_state_fundamental.

Why (taos 0.6.4, upstream test 19f9fbd, read 10-16 JST):

* ``validator/fundamental.py`` (BOOK_KEY = "f", ``stamp``): the simulator writes every book's latest fundamental into
  the book itself -- key ``f`` -- before the state goes to the miners, and the reporting path reads the published
  page's fundamental_price back from the same values (``reported``).  0.6.4's protocol Book carries it as
  ``f: float | None = Field(alias="fundamental", default=None)`` with a ``fundamental`` property, and its LazyBook reads
  the raw ``f``.  Our vendored 0.5.5 Book had only i / r / b / a / e: pydantic dropped the key.
* Since v6.16 the agent reads the same number from the validator's public metrics page through a host-wide feed thread:
  ~8-14 sim-s old when fetched, a page step of 10 sim-s and a fetch every 30 wall-s, with v6.26's feed clock to call it
  stale (10.7% of requests before S6, 0.1-0.4% after).  The state's own value is the current one, comes with every
  state, and needs no thread, file or network.

The rule (``research_v632_state_fundamental``): per request, ``{book: f}`` for the books whose raw ``f`` is a finite
number > 0 (``state_map`` / ``fundamental_value``).  Every reader of v6.16's feed rule -- v6.16's centre, the v6.17
touch quote, v6.23 S1 and v6.25 S5 -- takes that value, after v6.16's own book sanity (a bid > 0 below the ask), with
reason ``state``; else the page path exactly as before (``book_value``).  S9's look-back history (v6.29 / v6.30 D2)
records a snapshot built from the state's map (one entry per request, at the state's time) instead of the page's
(``s9_snapshot``).  A 0.6.3 state carries no ``f``: nothing changes.  The page feed keeps running as the fallback.

STRUCTURAL: the validator's own published value and v6.16's sanity rule; no constant is sized from a sample.
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

V632_STATE_FUNDAMENTAL_VERSION = "state_fundamental_v6_32"
BOOK_KEY = "f"                     # taos 0.6.4 validator/fundamental.py BOOK_KEY: the book's fundamental in the state
REASON_STATE = "state"             # the reason a reader tallies when it used the state's own value

__all__ = ["BOOK_KEY", "REASON_STATE", "V632_STATE_FUNDAMENTAL_VERSION", "book_value", "fundamental_value",
           "s9_snapshot", "state_map"]


def _positive(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) and v > 0.0 else None


def fundamental_value(book: Any) -> float | None:
    """One book's fundamental as the state carries it: the raw wire dict's ``f``, or the book object's ``fundamental``
    (0.6.4's Book / LazyBook accessor) -- a finite number > 0, else None.  Never raises."""
    try:
        if isinstance(book, Mapping):
            return _positive(book.get(BOOK_KEY))
        return _positive(getattr(book, "fundamental", None))
    except Exception:
        return None


def state_map(books: Any) -> dict[int, float]:
    """{book id: fundamental} for every book of the state that carries a usable one.  Reads the lazy raw mapping
    (LazyBooks._raw_books: no LazyBook built, nothing parsed) when the books have one, else each book.  Never raises:
    an unreadable book is left out."""
    out: dict[int, float] = {}
    try:
        raw = getattr(books, "_raw_books", None)
        items = raw.items() if isinstance(raw, Mapping) else (books.items() if books is not None else ())
        for raw_id, book in items:
            try:
                f = fundamental_value(book)
                if f is not None:
                    out[int(raw_id)] = f
            except Exception:
                continue
    except Exception:
        return out
    return out


def book_value(fp_map: Any, book_id: Any, bid: Any, ask: Any) -> tuple[float, str] | None:
    """(fundamental, REASON_STATE) for one book from this request's state map, after v6.16's book sanity (a bid > 0
    strictly below the ask; research_v616_fair_value.book_fundamental's ``no_book``); None -- the reader takes the page
    path -- when the map has no usable value for the book or the book is not sane."""
    if not isinstance(fp_map, Mapping) or not fp_map:
        return None
    f = fp_map.get(book_id)
    if f is None:
        try:
            f = fp_map.get(int(book_id))
        except (TypeError, ValueError, OverflowError):
            f = None
    f = _positive(f)
    if f is None:
        return None
    try:
        b, a = float(bid), float(ask)
    except (TypeError, ValueError, OverflowError):
        return None
    if not (math.isfinite(b) and math.isfinite(a)) or b <= 0.0 or a <= b:
        return None
    return f, REASON_STATE


def s9_snapshot(fp_map: Any, sim_id: Any, state_ts_ns: Any) -> dict[str, Any] | None:
    """The snapshot S9's page history records for this request when the state carries fundamentals -- the page's own
    shape: {"fp": the state's map, "sim_id": this state's simulation id, "sim_ts_ns": the state's time} -- or None
    (no map: the page's snapshot as before)."""
    if not isinstance(fp_map, Mapping) or not fp_map:
        return None
    if isinstance(state_ts_ns, bool):
        return None
    try:
        ts = int(state_ts_ns)
    except (TypeError, ValueError, OverflowError):
        return None
    if ts <= 0:
        return None
    return {"fp": fp_map, "sim_id": sim_id, "sim_ts_ns": ts}
