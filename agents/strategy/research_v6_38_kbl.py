# SPDX-License-Identifier: MIT
"""v6.38 layer 3, piece 1: KBL -- the post-step window touch quote keyed to the book's capture balance, with the
balance-keyed deep ladder -- research_v638_kbl (ships 0).

Why (scratchpads k102 round 2, k105; memory research-plan-1019, l2-prereg-and-v637-plan-1019): the fundamental steps
every 30 sim-s and the validator realizes our fills over 20 s, so a fill in the first 10 s after a step has its horizon
closed before the next step.  Quoting the touch on the newest step's side in that window is the one rule that moved our
fill mix toward the leaders' (front-with-move 17 % -> 28-44 % of notional); keyed to the book's own buy / sell capture
balance it keeps the per-book two-sided making (2 x min(buy, sell)).  KBL is the pre-registered L2 / L2B candidate
(k105 PRE-REGISTRATION 11:28:46 JST 10-19; k121/L2B_PREREG.md, sha256 7543429d...): engine k105/vm715.py with
"x1=1,x2=2,x3=1,x5=1,xb=1,xl=1".  K5 ("x1=1,x2=2,x3=1,x4=1,x5=1") is its comparison arm (never auto-shipped).

The rules (each the engine's switch, read from its code -- k102/R/mk_vm712.py, k105/mk_vm715.py):

* X1 cleanup: the v6.17 touch quote is exempt from the A1.9.1 exit-reprice cancel and from the A1.7.3 partial-remainder
  / dust cancel (the v6.3.3.1 / v6.3.3.2 deep-layer exemptions extended to the touch slot), and on every book the touch
  quote's NEW placement is the book's FIRST instruction of the response (the validator lands the first instruction per
  book at the base delay, later ones + U(5, 25) ms); the book's other instructions keep their order after it.
* X2b step window: on the states with ts in [step, step + W) of a block whose step the agent saw (v6.36's own step
  record: the first state of block n against the last state of block n - 1, the state fundamentals), on a book that
  step moved, the touch quote's side is the step's side (up -> buy, down -> sell) instead of the toward-fp side; S9 is
  not applied to it; the fp-edge hurdle is not applied to it at placement or while resting; a resting quote on that side
  is cancelled once the touch is one tick better than it (REST_TICKS_WINDOW).  Every other gate stays (fresh fp, cap,
  pacing, the bound / S7 band, v6.19 S2m, S14, D4, yield-to-deep, the cancel-seen wait).  From step + W the v6.36 rules
  apply again, so a window quote they do not want is cancelled there.  W = the step period - the validator's realized
  horizon (30 s - 20 s = 10 s on mainnet: the engine's xw=10).
* X3a size: the window quote is sized at the unscaled clip (the v6.20 lead scale not applied); bound and cap kept.
* X5 improve: the window quote is placed one tick inside under the S12b geometry, else joined (v6.31 S12b's own
  geometry; for the window quote without the fp-edge test).
* xb (KBL only) the presence key: a window book-state whose step side is the book's capture-SURPLUS side over the
  current making window is treated as outside the window (v6.36's touch rules there).
* xl (KBL only) the balance-keyed ladder: the v6.9 ladder levels L1 / L2 are not placed -- resting ones cancelled -- on
  the book's capture-surplus side, every state, every book; the deficit side and a balanced book keep v6.36's ladder.
* X4 (K5 only): the ladder levels L1 / L2 off on both sides.
The balance (xb, xl): the agent's own making mirror (research_v62_making_mirror: our fills' capture against the
31-print centred mid, the validator's 10,800-s window -- v6.19 S2m's estimator, mirror.book_capture); surplus side =
v6.19's leading_side (buy capture > sell capture + 1e-9 -> buy; the reverse -> sell; else none).

STRUCTURAL: the step period and the validator's horizon (W), v6.36's step record, v6.2.14 S1's one increment, v6.31's
geometry, the making mirror, the pre-registered arm strings.  No constant here is sized from an observed metric.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from research_v617_touch_quote import CANCEL_FLIPPED, CANCEL_LEFT, SIDE_BUY, SIDE_SELL, client_ids, join_price
from research_v619_surplus_hold import leading_side
from research_v62_breadth import entry_client_ids
from research_v6210_touch_improve import touch_view
from research_v6214_touch_life import exit_client_ids
from research_v631_touch_improve import improved_touch_price
from research_v633_deep_layer import own_client_ids as deep_client_ids
from research_v63_trend_target import own_client_ids as v63_client_ids
from research_v6_32_fee_making import HORIZON_NS

V6_38_KBL_VERSION = "kbl_v6_38_l3"
MODE_OFF, MODE_KBL, MODE_K5 = 0, 1, 2       # research_v638_kbl: off | KBL (the candidate) | K5 (the comparison arm)
MODES = (MODE_OFF, MODE_KBL, MODE_K5)
REST_TICKS_WINDOW = 1                       # STRUCTURAL: v6.2.14 S1's one increment (k102 X2 "re-join when overtaken")
LADDER_LEVELS = (1, 2)                      # v6.9's ladder levels (research_v69_deep_ladder.DEEPEST_LEVEL = 2)
CANCEL_SURPLUS_LADDER = "KBL_SURPLUS_LADDER"    # xl: a ladder level on the book's capture-surplus side
CANCEL_LADDER_OFF = "K5_LADDER_OFF"             # X4: a ladder level of K5's ladder-off arm

__all__ = ["CANCEL_LADDER_OFF", "CANCEL_SURPLUS_LADDER", "LADDER_LEVELS", "MODES", "MODE_K5", "MODE_KBL", "MODE_OFF",
           "REST_TICKS_WINDOW", "V6_38_KBL_VERSION", "components", "improve", "in_window", "join", "ladder_pairs",
           "mode_of", "own_ids_of", "surplus_side", "touch_first", "verdict", "window_ns", "window_side"]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def mode_of(value: Any) -> int:
    """research_v638_kbl's mode: 0, 1 or 2 (an integer, its text or a whole float); anything else is 0 (off)."""
    if isinstance(value, bool):
        return MODE_OFF
    v = _finite(value)
    if v is None or v != int(v) or int(v) not in MODES:
        return MODE_OFF
    return int(v)


def components(mode: Any) -> dict[str, bool]:
    """The engine switches a mode turns on: KBL = x1, x2=2, x3=1, x5=1, xb, xl; K5 = x1, x2=2, x3=1, x4, x5."""
    m = mode_of(mode)
    on = m in (MODE_KBL, MODE_K5)
    return {"x1": on, "x2b": on, "x3a": on, "x5": on, "xb": m == MODE_KBL, "xl": m == MODE_KBL, "x4": m == MODE_K5}


def window_ns(step_ns: Any, horizon_ns: Any = HORIZON_NS) -> int | None:
    """W: the step period minus the validator's realized horizon (10 s on mainnet's 30-s step and 20-s horizon); None
    when that is not positive or unreadable."""
    s, h = _finite(step_ns), _finite(horizon_ns)
    if s is None or h is None:
        return None
    w = int(s) - int(h)
    return w if w > 0 else None


def in_window(block: Any, phase: Any, step_block: Any, interval_ns: Any, win_ns: Any) -> bool:
    """Is a state of (block, phase) inside [step, step + W) of a block whose step the agent saw: the record's step block
    is this block and phase x interval < W (vm712: STEP_BLK == ts // 30 s and ts % 30 s < xw)."""
    try:
        if block is None or phase is None or step_block is None or win_ns is None:
            return False
        if int(step_block) != int(block):
            return False
        return int(phase) * int(interval_ns) < int(win_ns)
    except (TypeError, ValueError):
        return False


def surplus_side(buy_capture: Any, sell_capture: Any) -> str | None:
    """The book's capture-surplus side over the making window: v6.19's leading_side (vm715 _k105_sur)."""
    return leading_side(buy_capture, sell_capture)


def window_side(direction: Any, *, buy_capture: Any = 0.0, sell_capture: Any = 0.0, keyed: bool = False) -> str | None:
    """The window quote's side on a book the step moved: up -> buy, down -> sell (vm712 STEPDIR); None for a book the
    step did not move.  ``keyed`` (xb): None as well when that side is the book's capture-surplus side."""
    try:
        d = int(direction)
    except (TypeError, ValueError):
        return None
    side = SIDE_BUY if d > 0 else SIDE_SELL if d < 0 else None
    if side is not None and keyed and surplus_side(buy_capture, sell_capture) == side:
        return None
    return side


def verdict(side: str, price: Any, *, window: str, bid: Any, ask: Any, tick: Any,
            rest_ticks: Any = REST_TICKS_WINDOW) -> str | None:
    """A resting touch quote's verdict on a window book-state: None when it stays; CANCEL_FLIPPED off the window side;
    CANCEL_LEFT once the touch is ``rest_ticks`` better than it (v6.17's own test at one tick).  No fp-edge test (X2b)."""
    if side != window:
        return CANCEL_FLIPPED
    p, b, a, t = _finite(price), _finite(bid), _finite(ask), _finite(tick)
    if p is None or b is None or a is None or t is None or t <= 0.0:
        return CANCEL_LEFT
    ahead = (b - p) / t if side == SIDE_BUY else (p - a) / t      # ticks the touch is better than the quote
    if ahead > float(rest_ticks) - 0.5:
        return CANCEL_LEFT
    return None


def join(side: str, bid: Any, ask: Any, tick: Any, safety_ticks: Any = 1) -> float | None:
    """The window quote's join price: v6.17's postable touch, without the fp-edge hurdle (X2b)."""
    return join_price(side, bid, ask, tick, safety_ticks)


def own_ids_of(book_id: int) -> set:
    """Every client id of ours on a book (v6.17's touch slot, v6.3.3's deep layer, v6.2.14's exits, v6.2's entries, v6.3's
    roles) -- the ids S12b's view of the others' touch leaves out (v6.31's _v6_31_price reads the same five)."""
    return set(client_ids(book_id)) | deep_client_ids(book_id) | set(exit_client_ids(book_id)) \
        | set(entry_client_ids(book_id)) | v63_client_ids(book_id)


def improve(side: str, book: Any, own_order_ids: Any, book_id: int, fundamental: Any, tick: Any,
            join_px: Any) -> tuple[float | None, str]:
    """X5 + X2b: S12b's improve (v6.31 improved_touch_price on v6.2.10's view of the others' touch, every order of ours
    left out by id and client id) WITHOUT the fp-edge test; (None, why) keeps the join."""
    view = touch_view(book, own_ids=own_order_ids, own_cids=own_ids_of(book_id))
    return improved_touch_price(side, view, fundamental, tick, join_price=join_px, threshold_bps=float("-inf"))


def ladder_pairs(sides: Iterable[str], levels: Iterable[int]) -> frozenset:
    """The (side, level) pairs that hold no ladder order this request."""
    return frozenset((s, int(k)) for s in sides for k in levels)


def touch_first(instructions: list, book_of, is_touch_placement) -> tuple[list, int]:
    """X1 (b): the instructions in their order, each book's touch-quote placement moved right before that book's first
    instruction (the rest of the book keeps its order).  ``book_of(ix)`` -> book id or None; ``is_touch_placement(ix)``.
    Returns (the new list, how many placements moved ahead of another instruction of their book).  Pure."""
    touch: dict[Any, list] = {}
    first: dict[Any, Any] = {}
    for ix in instructions:
        b = book_of(ix)
        first.setdefault(b, ix)
        if is_touch_placement(ix):
            touch.setdefault(b, []).append(ix)
    if not touch:
        return list(instructions), 0
    moved = {id(ix) for group in touch.values() for ix in group}
    out, seen = [], set()
    for ix in instructions:
        if id(ix) in moved:
            continue
        b = book_of(ix)
        if b in touch and b not in seen:
            out.extend(touch[b])
            seen.add(b)
        out.append(ix)
    for b, group in touch.items():
        if b not in seen:
            out.extend(group)
    ahead = sum(1 for b, group in touch.items() for ix in group if first.get(b) is not ix)
    return out, ahead
