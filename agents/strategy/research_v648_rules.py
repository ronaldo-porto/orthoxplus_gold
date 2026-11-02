# SPDX-License-Identifier: MIT
"""v6.48: v6.47 (commit 94d2eb0) + the Round 6 arm SBH as a book-scoped in-agent A/B (USER 11-02 ~22:5x JST "2. Live
testnet A/B of the sweep hold") + FPREC (USER the same reply "1. Go": the observatory records each book's state
fundamental) -- the switches and the class-0 rule SBH, function for function from the replay engine (scratchpad
round6/vm_r6.py c6b13b83, ``r6sb``: the switch read l.722, the marks l.1965-1976, the hold l.3108-3116; PLAN.md sec. 5),
each behind its own research_v648_* switch, code default 0 (off: v6.47 on every path).  FPREC's shape is
research_v648_fprec's.

Class 0 -- SBH (research_v648_sbh, engine r6sb; SBR = value 2):
* the marks: on every state, for every class-0 book whose PREVIOUS state's touch had both sides (the engine's R6PT), each
  taker print of THIS state is measured against that touch in the book's ticks -- a sell print (s == 1) by
  (prev bid - price) / tick, a buy print by (price - prev ask) / tick -- and one at >= 1 tick (the engine's
  ``>= 1.0 - 1e-9``) marks the side it swept (sell print -> the bid / buy side, buy print -> the ask / sell side) with
  this state's 30-s block ``int((ts_ns // 10**9) // 30)``; then the book's touch of this state is kept for the next one
  (a side the state's book lacks: None, so the next state marks nothing);
* the hold: while a side's mark is this state's block, the class-0 book's deep side (level 0 + the ladder) holds its
  orders (a resting one cancelled, none placed) -- value 1 (SBH) either way, value 2 (SBR) only while that side REDUCES
  the TRUE inventory (a bid while inv < -EPS, an ask while inv > EPS).  The touch quote is never held;
* the A/B scope (research_v648_sbh_scope, fixed by the coordinator before any A/B number; a design split, not a strategy
  constant): 0 = every class-0 book (the engine's form), 1 = the EVEN class-0 books only (0, 2, ..., 62), 2 = the class-0
  books listed in research_v648_sbh_books (exactly BOOKS_N distinct ids in 0..63; the coordinator's balanced split, 11-02
  23:3x JST: Round 7's r7_split.py list for UID 240, 16 even, 8 per id mod 4) -- every other class-0 book keeps v6.47's
  behaviour (the in-agent control; its marks and would-be holds are counted, never acted on).  The list's digest is
  r7_split.py's: sha256 of the ascending ids joined by "," (``books_sha``).

STRUCTURAL: the engine's arithmetic (EPS 1e-9, the 1-tick sweep with its 1e-9 tolerance, the sim's 30-s block on the
state's own clock), the deep layer's own sweep definition (research_v633_deep_layer.sweep_depths: prev touch, the side
convention s == 1 -> the bid) and the agent's own inventory; no constant here is sized from a sample."""
from __future__ import annotations

import hashlib
import math
from typing import Any, Iterable

from research_v63_trend_target import SIDE_BUY, SIDE_SELL
from research_v647_rules import parse_value

V648_RULES_VERSION = "rules_v6_48"
EPS = 1e-9                                   # the engine's EPS (vm_r6 / vm_r5c12)
SWEEP_TICKS = 1.0                            # the engine: a print >= 1 tick past the previous touch (r6sb, sweep_depths)
SWEEP_TOL = 1e-9                             # the engine's ``_d6 >= 1.0 - 1e-9``
BLOCK_S = 30                                 # the engine's ``int((int(ts) // 10**9) // 30)`` (the sim's 30-s fp block)
NS_PER_S = 10 ** 9
PRINT_SELL = 1                               # a trade's ``s``: 1 = the taker sold (it hit the bid), as sweep_depths reads it

SWITCHES = ("research_v648_fprec", "research_v648_sbh", "research_v648_sbh_scope")
VALUES = {"research_v648_fprec": (0, 1), "research_v648_sbh": (0, 1, 2), "research_v648_sbh_scope": (0, 1, 2)}
SCOPE_ALL, SCOPE_EVEN, SCOPE_LIST = 0, 1, 2
BOOKS_KEY = "research_v648_sbh_books"        # scope 2's treated class-0 books (a comma list; absent under scope 0 / 1)
BOOKS_N = 32                                 # the coordinator's design: exactly half of class 0's 64 books
BOOK_LIMIT = 64                              # class 0 = books 0..63 (the 0.6.5 layout; the class map still decides class 0)

__all__ = ["BLOCK_LIMIT_NOTE", "books_valid", "BLOCK_S", "BOOKS_KEY", "BOOKS_N", "BOOK_LIMIT", "EPS", "SCOPE_ALL", "SCOPE_EVEN",
           "SCOPE_LIST", "SWEEP_TICKS", "SWEEP_TOL", "SWITCHES", "V648_RULES_VERSION", "VALUES", "block_of", "books_sha",
           "group_tag", "hold_decision", "in_scope", "parity_tag", "parse_books", "parse_value", "reducing", "swept_sides"]
BLOCK_LIMIT_NOTE = "scope 2: exactly %d distinct class-0 ids in 0..%d" % (BOOKS_N, BOOK_LIMIT - 1)


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def block_of(ts_ns: Any) -> int | None:
    """The state's 30-s block on its own clock, the engine's ``int((int(ts) // 10**9) // 30)``; None for no usable time."""
    if ts_ns is None or isinstance(ts_ns, bool):
        return None
    try:
        ts = int(ts_ns)
    except (TypeError, ValueError):
        return None
    if ts < 0:
        return None
    return int((ts // NS_PER_S) // BLOCK_S)


def swept_sides(trades: Iterable[Any], prev_bid: Any, prev_ask: Any, tick: Any) -> frozenset:
    """The sides this state's prints swept >= 1 tick past the PREVIOUS state's touch (the engine's r6sb marks): a sell
    print at <= prev bid - 1 tick sweeps the buy (bid) side, a buy print at >= prev ask + 1 tick the sell (ask) side;
    nothing without both previous sides or a tick > 0.  ``trades``: dicts {"p", "s", ...} (trade_of / trade_dict).
    Never raises: an unreadable print is skipped."""
    pb, pa, t_ = _finite(prev_bid), _finite(prev_ask), _finite(tick)
    if pb is None or pa is None or t_ is None or not (t_ > 0.0):
        return frozenset()
    out = set()
    for tr in trades or ():
        try:
            px = float(tr["p"])
            sd = int(tr["s"])
        except (KeyError, TypeError, ValueError, IndexError):
            continue
        if not math.isfinite(px):
            continue
        d = ((pb - px) if sd == PRINT_SELL else (px - pa)) / t_
        if d >= SWEEP_TICKS - SWEEP_TOL:
            out.add(SIDE_BUY if sd == PRINT_SELL else SIDE_SELL)
            if len(out) == 2:
                break
    return frozenset(out)


def parse_books(value: Any) -> tuple | None:
    """research_v648_sbh_books: the ascending distinct ids of a comma list ("0,3,5" -- the launcher's parser hands a
    list as text and a lone id as a float), a list / tuple of ids, or one id; () when missing or empty; None when any
    entry is not a whole number >= 0 or an id repeats."""
    if value is None or (isinstance(value, str) and value.strip() == ""):
        return ()
    if isinstance(value, bool):
        return None
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
    elif isinstance(value, (int, float)):
        items = [value]
    else:
        items = [x.strip() for x in str(value).split(",")]
    out = []
    for x in items:
        if isinstance(x, bool):
            return None
        if isinstance(x, str):
            if not (x.isascii() and x.isdigit()):
                return None
            v = int(x)
        else:
            f = _finite(x)
            if f is None or f != int(f) or f < 0:
                return None
            v = int(f)
        out.append(v)
    if len(set(out)) != len(out):
        return None
    return tuple(sorted(out))


def books_valid(books: Any) -> bool:
    """Scope 2's list: exactly BOOKS_N distinct ids, each in 0..BOOK_LIMIT - 1."""
    return (isinstance(books, tuple) and len(books) == BOOKS_N and len(set(books)) == BOOKS_N
            and all(isinstance(b, int) and 0 <= b < BOOK_LIMIT for b in books))


def books_sha(books: Any) -> str | None:
    """r7_split.py's digest of a treated list: sha256 of the ascending ids joined by "," (no spaces, no newline)."""
    try:
        ids = sorted(int(b) for b in books)
    except (TypeError, ValueError):
        return None
    return hashlib.sha256(",".join(map(str, ids)).encode()).hexdigest()


def in_scope(book_id: Any, scope: Any, books: Any = ()) -> bool:
    """The A/B scope: 0 = every class-0 book (the engine's form); 1 = the even book ids only (0, 2, ..., 62); 2 = the
    ids in ``books`` (scope 2's list) only."""
    try:
        s, b = int(scope), int(book_id)
    except (TypeError, ValueError):
        return False
    if s == SCOPE_ALL:
        return True
    if s == SCOPE_EVEN:
        return b % 2 == 0
    if s == SCOPE_LIST:
        try:
            return b in books
        except TypeError:
            return False
    return False


def group_tag(book_id: Any, scope: Any, books: Any = ()) -> str:
    """'treated' / 'control' of a class-0 book under the scope (the counters' A/B split)."""
    return "treated" if in_scope(book_id, scope, books) else "control"


def parity_tag(book_id: Any) -> str:
    """'even' / 'odd' of a book id (the counters' split)."""
    try:
        return "even" if int(book_id) % 2 == 0 else "odd"
    except (TypeError, ValueError):
        return "odd"


def reducing(side: str, true_inventory: Any) -> bool:
    """The side reduces the TRUE inventory (the engine's ``_red6``): a bid while inv < -EPS, an ask while inv > EPS."""
    inv = _finite(true_inventory)
    inv = 0.0 if inv is None else inv
    return (side == SIDE_BUY and inv < -EPS) or (side == SIDE_SELL and inv > EPS)


def hold_decision(mark_block: Any, block: Any, side: str, true_inventory: Any, mode: Any) -> tuple[bool, bool, bool]:
    """(evaluated, held, reducing) of one deep side this state: evaluated while the side's mark is this state's block
    (the engine's ``R6SBH.get(b, {}).get(side) == int((int(ts) // 10**9) // 30)``); held when evaluated and mode 1 (SBH)
    or mode 2 (SBR) with the side reducing the TRUE inventory; mode 0 never evaluates."""
    try:
        m = int(mode or 0)
    except (TypeError, ValueError):
        return False, False, False
    if m not in (1, 2) or mark_block is None or block is None or side not in (SIDE_BUY, SIDE_SELL):
        return False, False, False
    try:
        if int(mark_block) != int(block):
            return False, False, False
    except (TypeError, ValueError):
        return False, False, False
    red = reducing(side, true_inventory)
    return True, bool(m == 1 or red), red
