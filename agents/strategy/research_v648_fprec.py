# SPDX-License-Identifier: MIT
"""v6.48 FPREC (USER 11-02 ~22:5x JST "1. Go"): the observatory records each book's state fundamental -- research_v648_fprec.

Why (Round 6, scratchpad round6/RESULT.md): a replay of the testnet market cannot be calibrated because the recorded states
carry no fundamental (the TM replay is blind to the state fp: the class-0 touch quote, the k48 against side, the lean,
v6.36 and R11SAG never act in it), while the live agent reads every book's ``f`` from the state itself (v6.32 P1).

The rule (``research_v648_fprec``, 0 / 1, code default 0): each recorded state's book row carries the raw book's
fundamental under the short key ``fv`` -- the wire dict's ``f`` (research_v6_32_state_fp.BOOK_KEY, the very key the v6.32
state-fp reader takes), copied VERBATIM: nothing parsed, renamed or rounded, as research_v5_observatory promises for every
other field.  Only when present: a book whose raw dict has no ``f`` (or ``f`` null) gets no ``fv``; only on a row the frozen
shape already recorded (a book with no trade and no touch in a state has no row, and none is added -- the readers' book
iteration is unchanged).  Every other byte of the row is the frozen shape's.

Where: research_v5_observatory is a frozen module (the v6.39 M-FROZEN_2 / M2 text pins of tests/test_research_v6_39.py),
so it is not edited.  ``install()`` puts this module's ``shape_state`` -- the frozen ``shape_state`` itself, then the
``fv`` keys -- in its place in that module's namespace, where the recorder's writer thread (StateRecorder._write) looks
it up per state; the agent installs it once at start with the switch on (switch off: nothing is installed, the states
are byte-identical to v6.47's).  The request path pays nothing (the recorder hands the raw mapping to its writer thread
by reference); the writer thread pays one dict get per book.  An FPREC fault never raises: the state is written without
any ``fv`` (all of a state's keys are added at once) and the fault is counted.

STRUCTURAL: the validator's own per-book value and the recorder's own verbatim rule; no constant is sized from a sample."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import research_v5_observatory as _obs
from research_v6_32_state_fp import BOOK_KEY

V648_FPREC_VERSION = "fprec_v6_48"
FV_KEY = "fv"

_ORIGINAL = _obs.shape_state                     # the frozen shape (research_v5_observatory.shape_state as imported)

# written by the writer thread only (one recorder per agent process); read by the agent's V6_40_STATE row
COUNTS = {"states": 0, "books_fv": 0, "books_no_fv": 0, "books_fv_no_row": 0, "errors": 0}

__all__ = ["BOOK_KEY", "COUNTS", "FV_KEY", "V648_FPREC_VERSION", "add_fv", "install", "installed", "shape_state",
           "uninstall"]


def add_fv(row: Any, raw_books: Any) -> tuple[int, int, int]:
    """Adds ``fv`` = the raw book's ``f`` (verbatim) to each book row of ``row`` (the frozen shape's output) whose raw
    wire dict carries one (not None); every key of the state is added at once, after the scan, so a fault leaves the row
    as the frozen shape built it.  Returns (books with fv, books without f, books with f but no row)."""
    books = row.get("books") if isinstance(row, dict) else None
    if not isinstance(books, dict):
        return 0, 0, 0
    add = {}
    n_no = n_no_row = 0
    for raw_id, raw_book in (raw_books or {}).items():
        if not isinstance(raw_book, Mapping):
            continue                              # the frozen shape skips it too
        value = raw_book.get(BOOK_KEY)
        if value is None:
            n_no += 1
            continue
        key = str(raw_id)                         # the frozen shape's book key
        if not isinstance(books.get(key), dict):
            n_no_row += 1
            continue
        add[key] = value
    for key, value in add.items():
        books[key][FV_KEY] = value
    return len(add), n_no, n_no_row


def shape_state(raw_books: Mapping[Any, Any] | None, *, tick: int, ts: Any, depth: bool) -> dict[str, Any]:
    """The frozen research_v5_observatory.shape_state, then ``fv`` on each recorded book that carries ``f``."""
    row = _ORIGINAL(raw_books, tick=tick, ts=ts, depth=depth)
    try:
        n_fv, n_no, n_no_row = add_fv(row, raw_books)
        COUNTS["states"] += 1
        COUNTS["books_fv"] += n_fv
        COUNTS["books_no_fv"] += n_no
        COUNTS["books_fv_no_row"] += n_no_row
    except Exception:
        COUNTS["errors"] += 1
    return row


shape_state.__wrapped__ = _ORIGINAL
shape_state._v6_48_fprec = True


def installed() -> bool:
    return bool(getattr(getattr(_obs, "shape_state", None), "_v6_48_fprec", False))


def install() -> bool:
    """Puts ``shape_state`` in research_v5_observatory's namespace (idempotent); True when it is there."""
    if not installed():
        _obs.shape_state = shape_state
    return installed()


def uninstall() -> bool:
    """The frozen shape back (tests); True when it is there."""
    _obs.shape_state = _ORIGINAL
    return not installed()
