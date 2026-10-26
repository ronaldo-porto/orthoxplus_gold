"""v6.42 (scratchpad k150): k149's L1T rule as a live port -- research_v642_l1t in {0, 1, 2, 3}, default 0 (off: v6.41).

Why (k149 PREREG sections 1, 4; OBSERVED premise, STRUCTURAL rule): class-0 making is 93 % of the UID's pay and its
lever is level-0 SIZE (k145 / k149 S0a: a level 0 that carries L1's size raises class-0 making +21..+33 % on today's
base).  k145's ungated fold (L1D) lost q in one cell: level-0 fills that unwind inventory BEYOND the lean target tau
against the 30-s block's fp step (k149 S0b).  L1T keeps the fold where its added fills hold q and drops it there.

The rule (vm749 = k148 vm748 + l1t, scratchpad k149/mk_vm749.py L38-L52; per CLASS-0 book side, deep ladder on):

* The gate, on this request's lean tau (v6.25 S5) and the book's TRUE position inv (not v6.25's inv - tau):
  toward = the side tau leans to (buy when tau > 0, sell when tau < 0, none when tau = 0); reducing = the side's fill
  moves inv toward tau (buy while inv - tau < -eps, sell while inv - tau > eps: not research_v641_pace.adds on inv - tau,
  vm749's reducing_); step = the sign of the fp step the agent recorded for the CURRENT block (v6.36 A_r1's step
  record; 0 when this block's step was not seen):
    l1t = 1  L1T   reducing AND toward
    l1t = 2  L1TS  reducing AND (toward OR the side trades WITH the step: buy when step > 0, sell when step < 0)
    l1t = 3  L1TI  toward AND (reducing OR the step is with the lean: sign(step) == sign(tau))
  closed when tau = 0, on a vacuum order (v6.4 S1) and on every book that is not class 0.
* Gate open and the side's ladder has its L1 level (deeper than this side's level 0 by the ladder's own gap): a NEW
  level-0 order carries L1's size -- the size the ladder would give L1 this request (v6.9 level_quantity, v6.20's
  scale, the volume grid) when that size is at least the venue minimum -- and L1 is not placed (a resting one is
  cancelled) while that order rests.  vm749: no = Order(..., q0_ + _l1f_q); `if lk == 0 and B.deep[side].l1f:
  cancel L1; continue`.
* Gate closed (or no L1 this request) while a carrying level 0 rests: it is cancelled (re-placed at its own size by the
  ladder rule; L1 returns).  vm749: `if _l1f_q <= 0.0 and o is not None and o.l1f: cancel`.  No forced re-fold: a
  resting level 0 that does not carry keeps resting (the next level-0 placement carries).
* Total resting size per side is unchanged: q0 + q1 at level 0 instead of q0 at level 0 and q1 at L1; L2, depths,
  prices, the bound, pacing, tau, D4, A_r1 + D, the touch quote and every class-1 / class-2 book are not this module's.
  No new constant: the side, the sign of tau, the engine's reducing test (v6.41 pace's eps), the v6.36 step grid and
  the step's sign.  Pure: no I/O, never raises.

Built for later rounds (coordinator 10-26): the gate is one pure function per mode (GATES), apart from the fold's size
(fold_quantity, level0_quantity), the carried size's bound (research_v642_l1c: one pure function per mode, CARRIED) and the
unfold / re-place (unfolds, resizes); the agent passes every gate the same inputs (side, true inventory, tau, the block's
step) plus a context for more.

research_v642_l1c (k151 round 5, vm751 = vm749 + l1c, scratchpad k151/mk_vm751.py; default 0; only with l1t = 1 or 2):
* where the l1t gate folds, the carried size is bounded by the gap to tau on the TRUE position: CAP (1) carried =
  min(q1, max(0, |inv - tau| - q0)) on every folded side; FAR-CAP (3) the same on the far side only (a toward-side fold
  carries q1).  A bounded size below the venue minimum = no fold (L1 at its own size; a resting carrying level 0 unfolds).
* on a bounded side a resting carrying level 0 whose remaining size exceeds q0 + the current carried size is re-placed.
* AG-CAP (4, k153 L1TSXA, vm753 l1c=4; with l1t = 2): the far side as FAR-CAP; the toward side capped the same way only
  while it trades AGAINST the block's step (a recorded step and not with_step), else it carries q1.
* the bounded size is floored to the volume grid (vm751 is exact; a grid size never rounds past tau).  No new constant."""
from __future__ import annotations

import math
from typing import Any

from research_v62_breadth import lot_quantity as _lot
from research_v620_room_pace import scaled_quantity as _scaled
from research_v633_deep_layer import SIDE_BUY, SIDE_SELL
from research_v641_pace import adds as _adds
from research_v69_deep_ladder import level_quantity as _level_quantity

V6_42_L1T_VERSION = "l1t_v6_42"
MODE_OFF, MODE_L1T, MODE_L1TS, MODE_L1TI = 0, 1, 2, 3
MODES = (MODE_OFF, MODE_L1T, MODE_L1TS, MODE_L1TI)
MODE_NAMES = {MODE_OFF: "off", MODE_L1T: "L1T", MODE_L1TS: "L1TS", MODE_L1TI: "L1TI"}
STEP_MODES = (MODE_L1TS, MODE_L1TI)        # the modes that read the block's step (vm749: needs the step record)
L1C_OFF, L1C_CAP, L1C_FAR_CAP, L1C_AG_CAP = 0, 1, 3, 4   # research_v642_l1c (k151 vm751 l1c 1 / 3, k153 vm753 l1c 4;
#                                          the engines' FIT (2), ONE-CLIP (5) and AG-UNFOLD (6) are not arms: not ported)
L1C_MODES = (L1C_OFF, L1C_CAP, L1C_FAR_CAP, L1C_AG_CAP)
L1C_NAMES = {L1C_OFF: "off", L1C_CAP: "CAP", L1C_FAR_CAP: "FAR_CAP", L1C_AG_CAP: "AG_CAP"}
L1C_GATES = (MODE_L1T, MODE_L1TS)          # l1c bounds the reducing folds only (vm751: needs l1t = 1 or 2)
L1C_STEP_GATES = {L1C_AG_CAP: (MODE_L1TS,)}   # a mode that reads the block's step needs l1t = 2 (it reads the step record)
CANCEL_RESIZE = "L1C_RESIZE"               # a carrying level 0 larger than q0 + the current bound: re-placed
FOLD_LEVEL = 1                             # STRUCTURAL: the ladder level whose size level 0 carries (vm749 LADDER[0])
CANCEL_UNFOLD = "L1T_UNFOLD"               # a carrying level 0 whose gate closed: re-placed at its own size
CANCEL_L1_HELD = "L1T_L1_HELD"             # a resting L1 while our level 0 carries its size
SIDES = (SIDE_BUY, SIDE_SELL)

__all__ = ["CANCEL_L1_HELD", "CANCEL_RESIZE", "CANCEL_UNFOLD", "CARRIED", "FOLD_LEVEL", "GATES", "L1C_CAP", "L1C_FAR_CAP",
           "L1C_AG_CAP", "L1C_GATES", "L1C_MODES", "L1C_NAMES", "L1C_OFF", "L1C_STEP_GATES", "against_step", "carried_ag_cap", "MODES", "MODE_L1T", "MODE_L1TI", "MODE_L1TS", "MODE_NAMES",
           "MODE_OFF", "STEP_MODES", "SIDES", "V6_42_L1T_VERSION", "bounded", "bounded_fold", "carried_cap",
           "carried_far_cap", "carried_quantity", "carried_size", "fold_quantity", "gate", "gate_l1t", "gate_l1ti",
           "gate_l1ts", "grid_floor", "level0_quantity", "parse_l1c", "parse_mode", "reducing", "resizes", "step_sign",
           "toward", "unfolds", "with_step"]


def _parse_in(value: Any, allowed) -> int | None:
    """An integral value in ``allowed`` -- an int, an integral float (taos ParseKwargs hands every PARAMS number over as a
    float: research_v642_l1t=1 arrives as 1.0) or its text; else None (bool, fractional, unreadable, out of range)."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value if value in allowed else None
    try:
        f = float(str(value).strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f != int(f):
        return None
    return int(f) if int(f) in allowed else None


def parse_mode(value: Any) -> int | None:
    """The mode of a research_v642_l1t value: 0 / 1 / 2 / 3 (an int, an integral float -- the launcher's PARAMS reach the
    agent as floats -- or its text), else None (unreadable -- the agent then runs off and counts an error; the launcher
    refuses such a build)."""
    return _parse_in(value, MODES)


def parse_l1c(value: Any) -> int | None:
    """The mode of a research_v642_l1c value: 0 / 1 / 3 / 4 (as parse_mode; the engines' 2 / 5 / 6 -- not arms -- refused)."""
    return _parse_in(value, L1C_MODES)


def _num(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def toward(side: Any, tau: Any) -> bool:
    """The side the lean target tau leans to: buy when tau > 0, sell when tau < 0, none when tau = 0 (vm749:
    (side == "bid") == (_tau > 0.0), only evaluated with _tau != 0.0)."""
    t = _num(tau)
    if t is None or t == 0.0 or side not in SIDES:
        return False
    return (side == SIDE_BUY) == (t > 0.0)


def reducing(side: Any, inventory: Any, tau: Any) -> bool:
    """The side's fill moves the TRUE inventory toward tau: buy while inv - tau < -eps, sell while inv - tau > eps
    (vm749 reducing_ on _INV = B.inv - _tau; = not v6.41 pace's adds on inv - tau, its own eps)."""
    inv, t = _num(inventory), _num(tau)
    if inv is None or t is None or side not in SIDES:
        return False
    return not _adds(side, inv - t)


def step_sign(direction: Any, step_block: Any, block: Any, book: Any) -> int:
    """The sign of the fp step the agent recorded on ``book`` for the CURRENT block: v6.36's StepRecord direction when
    its step block is this request's block, else 0 (vm749: STEPDIR[b] if STEP_BLK[0] == the state's block else 0.0)."""
    try:
        if step_block is None or block is None or int(step_block) != int(block):
            return 0
        d = (direction or {}).get(int(book), 0)
        d = int(d)
    except (TypeError, ValueError, AttributeError):
        return 0
    return (d > 0) - (d < 0)


def with_step(side: Any, step: Any) -> bool:
    """The side trades WITH the block's step: buy when the step is up, sell when it is down (vm749:
    _st_t != 0.0 and (_st_t > 0.0) == (side == "bid"))."""
    s = _num(step)
    if s is None or s == 0.0 or side not in SIDES:
        return False
    return (s > 0.0) == (side == SIDE_BUY)


# ---- the gate: one pure function per mode (a later mode is one more function and one more GATES entry) ------------------

def gate_l1t(side: Any, inventory: Any, tau: float, step: Any = 0, **_context: Any) -> bool:
    """Mode 1, L1T: reducing AND toward (vm749: reducing_ and _tw_t)."""
    return bool(reducing(side, inventory, tau) and toward(side, tau))


def gate_l1ts(side: Any, inventory: Any, tau: float, step: Any = 0, **_context: Any) -> bool:
    """Mode 2, L1TS: reducing AND (toward OR the side trades with the block's step) (vm749: reducing_ and (_tw_t or
    (_st_t != 0.0 and (_st_t > 0.0) == (side == "bid"))))."""
    return bool(reducing(side, inventory, tau) and (toward(side, tau) or with_step(side, step)))


def gate_l1ti(side: Any, inventory: Any, tau: float, step: Any = 0, **_context: Any) -> bool:
    """Mode 3, L1TI: toward AND (reducing OR the step is with the lean) (vm749: _tw_t and (reducing_ or (_st_t != 0.0 and
    (_st_t > 0.0) == (_tau > 0.0))))."""
    s = _num(step) or 0.0
    return bool(toward(side, tau) and (reducing(side, inventory, tau) or (s != 0.0 and (s > 0.0) == (tau > 0.0))))


GATES = {MODE_L1T: gate_l1t, MODE_L1TS: gate_l1ts, MODE_L1TI: gate_l1ti}


def gate(mode: Any, side: Any, inventory: Any, tau: Any, step: Any = 0, **context: Any) -> bool:
    """Is the L1T gate open on this class-0 book side (the caller checked the class and the vacuum)?  Closed with tau = 0
    in every mode (vm749: only evaluated with _tau != 0.0); else the mode's own function (GATES).  ``context`` carries
    any further per-side input a mode reads (none for modes 1-3)."""
    m = parse_mode(mode)
    t = _num(tau)
    if not m or t is None or t == 0.0 or side not in SIDES:
        return False
    fn = GATES.get(m)
    return bool(fn(side, inventory, t, step, **context)) if fn is not None else False


def fold_quantity(levels: Any, clip: Any, size_scale: Any, min_order: Any, volume_decimals: Any) -> float:
    """L1's size this request -- what a new level-0 order carries while the gate is open: 0.0 when the side has no L1
    level (``levels``: the side's v6.9 ladder_levels rows, (level, depth, clips)) or when that size is below the venue
    minimum; else the ladder's own size for it after its caps (v6.9 level_quantity of the clip, v6.20's scale when the
    book runs ahead of its line, the volume grid) -- vm749 _q1_ (lsize x dz x the launch clip, _psf, MINQ)."""
    try:
        if not any(int(row[0]) == FOLD_LEVEL for row in (levels or ())):
            return 0.0
        q = _level_quantity(clip, FOLD_LEVEL)
        s = _num(size_scale)
        if s is not None and s < 1.0:
            q = _scaled(q, s, min_order)
        q = _lot(q, volume_decimals)
        m = max(0.0, _num(min_order) or 0.0)
        return float(q) if q > 0.0 and q + 1e-12 >= m else 0.0
    except Exception:
        return 0.0


def level0_quantity(clip: Any, size_scale: Any, min_order: Any, volume_decimals: Any) -> float:
    """Level 0's own size this request, as the ladder places it: the clip, v6.20's scale when the book runs ahead of its
    line, the volume grid (vm749 q0_); 0.0 below the venue minimum (no level 0 is placed then)."""
    try:
        q = max(0.0, _num(clip) or 0.0)
        s = _num(size_scale)
        if s is not None and s < 1.0:
            q = _scaled(q, s, min_order)
        q = _lot(q, volume_decimals)
        m = max(0.0, _num(min_order) or 0.0)
        return float(q) if q > 0.0 and q + 1e-12 >= m else 0.0
    except Exception:
        return 0.0


# ---- the carried size: one pure function per research_v642_l1c mode (k151 vm751 l1c; a later bound is one more entry) -------

def carried_cap(q0: Any, q1: Any, gap: Any, toward: Any = False, against: Any = False) -> float:
    """l1c = 1, CAP (k145 L1DC's cap): min(q1, max(0, gap - q0)) -- a filled carrying level 0 never takes the TRUE inventory
    past tau (gap = |inv - tau|).  vm751: _q1c_ = min(_q1_, max(0.0, abs(_INV) - q0_))."""
    a, b, g = max(0.0, _num(q0) or 0.0), max(0.0, _num(q1) or 0.0), _num(gap)
    if g is None:
        return 0.0
    return min(b, max(0.0, abs(g) - a))


def carried_far_cap(q0: Any, q1: Any, gap: Any, toward: Any = False, against: Any = False) -> float:
    """l1c = 3, FAR-CAP: the CAP on the far side only (the side that is not the lean's toward side); a toward-side fold
    carries L1's whole size.  vm751: the bound applies when L1C != 3 or not _tw_t."""
    if toward:
        return max(0.0, _num(q1) or 0.0)
    return carried_cap(q0, q1, gap, toward)


def carried_ag_cap(q0: Any, q1: Any, gap: Any, toward: Any = False, against: Any = False) -> float:
    """l1c = 4, AG-CAP (k153 L1TSXA): the far side as FAR-CAP; the toward side capped at the gap -- no crossing of tau --
    only while it trades AGAINST the block's step, with the step (or no step) it carries q1.  vm753: L1C == 4 and _ag_t."""
    if toward and not against:
        return max(0.0, _num(q1) or 0.0)
    return carried_cap(q0, q1, gap, toward)


CARRIED = {L1C_CAP: carried_cap, L1C_FAR_CAP: carried_far_cap, L1C_AG_CAP: carried_ag_cap}


def against_step(side: Any, step: Any) -> bool:
    """The side trades AGAINST the block's step: a step was recorded for this block and the side does not trade with it
    (vm753 _ag_t: _st_t != 0.0 and (_st_t > 0.0) != (side == "bid"))."""
    s = _num(step)
    return bool(s is not None and s != 0.0 and side in SIDES and not with_step(side, s))


def carried_size(mode: Any, q0: Any, q1: Any, gap: Any, toward: Any, against: Any = False) -> float:
    """The size a folding level 0 carries from L1 under research_v642_l1c ``mode`` (0 or unknown: q1, k149's l1t)."""
    fn = CARRIED.get(parse_l1c(mode))
    q = max(0.0, _num(q1) or 0.0)
    return q if fn is None else float(fn(q0, q, gap, toward, against))


def bounded(mode: Any, toward: Any, against: Any = False) -> bool:
    """Is this folded side bounded under ``mode`` (vm751 / vm753 _l1c_here)?  CAP: every side; FAR-CAP: the far side;
    AG-CAP: the far side, and the toward side while it trades against the block's step."""
    m = parse_l1c(mode)
    if m == L1C_CAP:
        return True
    if m == L1C_FAR_CAP:
        return not toward
    if m == L1C_AG_CAP:
        return (not toward) or bool(against)
    return False


def grid_floor(q: Any, volume_decimals: Any) -> float:
    """``q`` floored to the volume grid (a bounded carried size never rounds up past tau)."""
    v = max(0.0, _num(q) or 0.0)
    try:
        d = max(0, int(volume_decimals))
    except (TypeError, ValueError):
        d = 4
    scale = 10.0 ** d
    return math.floor(v * scale + 1e-6) / scale


def bounded_fold(mode: Any, q0: Any, q1: Any, gap: Any, toward: Any, min_order: Any, volume_decimals: Any,
                 against: Any = False) -> float:
    """The carried size after the l1c bound, on the volume grid (floored); 0.0 below the venue minimum -- no fold
    (vm751: _q1_ < MINQ -> _l1f_q stays 0: L1 at its own size, a resting carrying level 0 unfolds)."""
    q = max(0.0, _num(q1) or 0.0)
    if q <= 0.0:
        return 0.0
    if not bounded(mode, toward, against):
        return q
    c = grid_floor(carried_size(mode, q0, q, gap, toward, against), volume_decimals)
    m = max(0.0, _num(min_order) or 0.0)
    return c if c > 0.0 and c + 1e-12 >= m else 0.0


def resizes(remaining: Any, q0: Any, carried: Any) -> bool:
    """Is a resting carrying level 0 on a bounded side re-placed?  Its remaining size exceeds q0 + the current carried size
    (vm751: o.rem > q0_ + _l1f_q + EPS -> cancel l1c_l0_resize)."""
    r, a, c = _num(remaining), max(0.0, _num(q0) or 0.0), max(0.0, _num(carried) or 0.0)
    return r is not None and c > 0.0 and r > a + c + 1e-9


def carried_quantity(q0: Any, q1: Any, volume_decimals: Any) -> float:
    """A carrying level 0's size: its own size plus L1's, on the volume grid (both already are: the sum is exact)."""
    a, b = max(0.0, _num(q0) or 0.0), max(0.0, _num(q1) or 0.0)
    return float(_lot(a + b, volume_decimals))


def unfolds(carrying: Any, q1: Any) -> bool:
    """Is a resting level 0 re-placed at its own size this request?  Only one that carries, when nothing is carried now
    (gate closed, no L1 level, or L1's size below the minimum) -- vm749: _l1f_q <= 0.0 and o.l1f."""
    return bool(carrying) and not ((_num(q1) or 0.0) > 0.0)
