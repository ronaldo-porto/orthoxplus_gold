# SPDX-License-Identifier: MIT
"""v6.23: the validator's 0.6.3 release -- research_v623_validator_063.

Why (10-09 JST; scratchpad k47: the 0.6.3 study, k48: its replay, verify063: the vendored-protocol checks):

* SN79 0.6.3 (upstream ``test`` 49e79d5, two commits over 0.6.2 = 47932d4; mainnet still runs 0.6.2) pays the making
  credit on the REALIZED basis by default: captured making x a per-uid markout quality q = clamp(R / C, 0, 1), R the
  spread our maker fills keep 20 s after they print (marked on the background's prints), C the spread they captured.
  On our live tape (k47) q is 0.00-0.08 for every UID: our share 5.2% -> 0.95% (-82%).  By role (R/C at 20 s) the touch
  quote keeps 0.78-0.92, level 0 of the deep ladder -0.05..-0.19, levels 1/2 -0.01..+0.12.  The level-0 fills that lose
  their spread are the ones on the side trading AGAINST the published fundamental: the background takers that trade
  toward it fill that side, and the mid follows them (v6.16: 0.20 x the gap in 60 sim-s).
* 0.6.3 also brings a multi-asset layout (eight markets of sixteen books: books 0-95 the calibrated ``simulation_0``
  class, books 96-127 ``simulation_1`` at a tenth of the price, minimum order 2.5364 against 0.25, both on 2 price and
  4 volume decimals; upstream agents/MIGRATION_0.6.3.md).  The request's configuration carries it as ``classes`` (a list
  of {name, books, priceDecimals, volumeDecimals, config}); the top-level fields are the first class's.  Every size,
  cap and bound of this agent is in the top-level class's units (0.25-lot clips, 1.0 / 2.0-base clips, 1.2 / 2.0 /
  48-base caps): almost every order it builds on the second class is below that class's minimum and refused, and the
  few that clear it (the ladder's level 2 at 4 base, a launch clip) trade there at the first class's sizes and bounds.
  The vendored 0.5.5 protocol dropped the field (pydantic ignores unknown keys); it now keeps it raw.
* The 0.6.3 engine delivers the simulation start / end notices (ESS / ESE) inside the state update, so ``onStart`` runs
  live for the first time: one ESS per realization (eight on the 96 + 32 layout) in the first state of a run, each
  resetting the realized-PnL state (_reset_pnl_state).  That first state carries no simulation id (0.6.3's on_start
  clears it and on_tick re-derives it from the run's log dir after the state was copied), so the session's
  simulation-id change -- its carry of the validator-aligned history and Strategy1_Research.onStart's preserve flag --
  comes one state later and stays armed until the NEXT run's first ESS.  That ESS restores the history the flag kept,
  clears the flag, and then calls ``bt.logging.info`` in a module that never imports ``bt`` (the known frozen gap of
  test_module_globals_resolve): NameError out of update(), so from the second run on, the request carrying each start
  fails and the rest of its notices (trades included) are never processed.  Had it not raised, the next ESS of the same
  start would reset the restored history again.

The rules (three switches; with them off, or with S1 inactive on a single market, the build is v6.22):

* S1 ``research_v623_realized_mode`` (auto | 1 | 0, default auto): auto is active iff the state's ``version`` -- the
  sending validator's taos spec version, 1000 x major + 10 x minor + patch: 62 from 0.6.2, 63 from 0.6.3 -- is at least
  63; a state without one is inactive; 1 is always active, 0 never.  While active, on a book with a fresh fundamental of
  this simulation (v6.16's feed and freshness rule, read as the v6.17 touch quote reads it) the deep side trading AGAINST
  it -- the sell side when the fundamental is above the mid, the buy side when it is below -- holds no deep order on
  the withheld levels: a resting one is cancelled and none is placed.  ``research_v623_against_levels`` (L0 | deep,
  default deep): level 0 only (the wide-spread vacuum order and v6.12's reducing-side level 0 are level 0 too; levels
  1/2 unchanged), or every level.  ``research_v623_against_adding`` (default 1): only while the side's fill would add to
  |inventory| (flat, or a position already against the fundamental) -- an against order that unwinds a position built
  toward it stays.  ``research_v623_against_gap_bps`` (default GAP_BPS_DEFAULT = the v6.17 touch quote's THRESHOLD_BPS,
  2.3): only while |log(fp / mid)| >= the gap in bps -- where the touch quote can rest on the toward side; nearer the
  fundamental the sides flip with the mid's ticks (a deep ladder cancelled and re-placed) and against fills keep their
  spread (k48's real-fp gap study: R/C -0.00 at 0-2 bps, +0.02 at 2-5).  The other side and the touch quote are
  unchanged.  A book without a fresh fundamental, a fundamental at the mid, inside
  the gap or (with adding) whose against side reduces its position is v6.22 exactly.
* S2 ``research_v623_class_guard`` (default 1): a book whose asset class's minimum order size, price decimals or volume
  decimals differ from the top-level configuration's is FOREIGN: the deep layer and the touch quote keep their cancels
  and build no new order for it (v6.22 S3's at-cap path), and the final validator drops any other placement on it
  (FOREIGN_CLASS), a reducing one too: a position v6.22 opened there is held, not unwound.  A single market, a
  configuration without classes, or a layout that cannot be read: no book is foreign (v6.22).
* S3 ``research_v623_start_once`` (default 1): the start reset runs at most once per state carrying start notices -- a
  start's notices all arrive in the first state of its run -- so every further ESS of that state is counted and skipped
  and what the first one restored survives; an exception out of the reset is counted instead of failing the request:
  the request and the rest of its notices go on, and the restore before the NameError has already run, so the state is
  the one the frozen chain intends.  A start notice repeated in a later state (a validator resuming its run) resets
  again, as in v6.22.

Shipped (agent defaults = run_strategy1_research_simple_multi.sh PARAMS): S1 auto with levels=deep, adding=1, gap 2.3
bps -- no deep order at any level on the side trading against the fundamental while its fill would open or grow a
position against it, on a book whose fundamental is at least the touch quote's threshold from the mid -- and S2, S3 on.  The calibrated 0.6.3 replay (scratchpad k48: the k44 engine, scored by the 0.6.3 rule as
validated on our live fills; 10-09 07:53 JST), making63 summed over six timeline-windows (aged from start A/B/C, aged
from a restart C, young B/C): deep + adding (k48 agdeep=2) +16.7%, 6 of 6 won, robustness 3 of 4 positive (latency
+-10 ms, self-trade); level 0 alone (L0, adding=0) -13.2% (1 of 6), every level regardless of inventory -16.0%, level 0
only while adding -0.8%.  The against level-0 fill is mostly the unwind of a position the toward touch quote built --
the two legs of one round trip -- so withholding it whatever the inventory removes both legs and piles the inventory
against the bound; what loses is the against fill that OPENS or GROWS a position (mostly the deepest, 4-base level, filled
by a large sweep toward the fundamental), and that is what deep + adding withholds.  Static what-if on our own live fills
(same rule): +29..+45%.  The replay's fundamental (the k44 proxy) only has a side where our touch quote rested at least
THRESHOLD_BPS from it, so the rule was only ever exercised beyond that gap: the shipped gate keeps it there.

STRUCTURAL: the validator's own release number and making basis, the simulator's reversion target, the configuration's
class grid and minimum, and the state that carries a run's start notices.  No fitted constant: the levels, the gap and
the adding qualifier are switch values the replay (k48) chooses between.
"""
from __future__ import annotations

import math
from typing import Any

from research_v633_deep_layer import DEEP_LEVELS, SIDE_BUY, SIDE_SELL
from research_v641_pace import adds as _adds
from research_v617_touch_quote import THRESHOLD_BPS as _TOUCH_THRESHOLD_BPS

V623_VALIDATOR_063_VERSION = "validator_063_v6_23"
REALIZED_SPEC = 63              # STRUCTURAL: taos 0.6.3's __spec_version__ (0.6.2 validators send 62)
MODE_AUTO, MODE_ON, MODE_OFF = "auto", "on", "off"
LEVELS_L0, LEVELS_DEEP = "L0", "deep"
GAP_BPS_DEFAULT = _TOUCH_THRESHOLD_BPS   # STRUCTURAL: the v6.17 touch quote's threshold, the validator's alpha hurdle (2.3)
CANCEL_AGAINST = "DEEP_AGAINST_FP"   # a resting deep order on the side trading against the fundamental
REJECT_FOREIGN = "FOREIGN_CLASS"     # the final validator: a placement on a book of another asset class
LAYOUT_SINGLE = "single"        # no classes: a single market, or a validator before 0.6.3
LAYOUT_SAME = "same"            # classes, every one on the top-level grid and minimum
LAYOUT_FOREIGN = "foreign"      # some class differs: its books are foreign
LAYOUT_UNREADABLE = "unreadable"  # classes that cannot be read: nothing is foreign (v6.22)


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _whole(value: Any) -> int | None:
    v = _finite(value)
    if v is None or v != math.floor(v):
        return None
    return int(v)


def _get(obj: Any, name: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(name)
    return getattr(obj, name, None)


# ---- the switches' values --------------------------------------------------------------------------------------------

def parse_mode(value: Any) -> str:
    """research_v623_realized_mode: 'auto' (the default), 1 / on (always active), 0 / off (never).  An unreadable value
    is the default."""
    if value is None:
        return MODE_AUTO
    if isinstance(value, bool):
        return MODE_ON if value else MODE_OFF
    if isinstance(value, (int, float)):
        v = _finite(value)
        return MODE_ON if v == 1.0 else MODE_OFF if v == 0.0 else MODE_AUTO
    s = str(value).strip().lower()
    if s in ("1", "1.0", "true", "yes", "on"):
        return MODE_ON
    if s in ("0", "0.0", "false", "no", "off"):
        return MODE_OFF
    return MODE_AUTO


def parse_levels(value: Any) -> str:
    """research_v623_against_levels: 'deep' (every level of the side) or 'L0' (the default: level 0 only)."""
    return LEVELS_DEEP if str(value).strip().lower() == "deep" else LEVELS_L0


def parse_gap_bps(value: Any) -> float:
    """research_v623_against_gap_bps: a finite gap >= 0 in bps; anything else is 0 (no gate)."""
    v = _finite(value)
    return v if v is not None and v > 0.0 else 0.0


# ---- S1: the realized basis -------------------------------------------------------------------------------------------

def validator_spec(version: Any) -> int | None:
    """The state's ``version``: the sending validator's taos spec version (1000 x major + 10 x minor + patch), or None
    when it is missing or not a whole number."""
    v = _whole(version)
    return v if v is not None and v >= 0 else None


def realized_active(mode: Any, spec: Any) -> bool:
    """S1: does the realized rule act on this state?  on: always; off: never; auto: from a 0.6.3 validator on."""
    if mode == MODE_ON:
        return True
    if mode != MODE_AUTO:
        return False
    s = validator_spec(spec)
    return s is not None and s >= REALIZED_SPEC


def against_side(mid: Any, fundamental: Any, gap_bps: Any = 0.0) -> str | None:
    """S1: the deep side that trades AGAINST the fundamental -- the sell side when it is above the mid, the buy side
    when it is below (the other side of the v6.17 touch quote's) -- or None at the mid, inside the gap, or without a
    usable input."""
    m, f = _finite(mid), _finite(fundamental)
    if m is None or f is None or m <= 0.0 or f <= 0.0 or f == m:
        return None
    gate = parse_gap_bps(gap_bps)
    if gate > 0.0 and abs(math.log(f / m)) * 1e4 < gate:
        return None
    return SIDE_SELL if f > m else SIDE_BUY


def withheld(side: Any, levels: Any, *, adding_only: Any = False, inventory: Any = None) -> frozenset:
    """S1: the (side, level) pairs of the deep ladder the against side holds no order on -- level 0, or with 'deep' every
    level (0 .. DEEP_LEVELS - 1).  With ``adding_only`` only while a fill of that side would not reduce |inventory|
    (v6.4.1's adds: a flat book, or a position already on that side): an against order that unwinds a position built
    toward the fundamental stays.  An unreadable inventory is no position (flat: both sides add)."""
    if side not in (SIDE_BUY, SIDE_SELL):
        return frozenset()
    if adding_only:
        inv = _finite(inventory)
        if not _adds(side, 0.0 if inv is None else inv):
            return frozenset()
    if levels == LEVELS_DEEP:
        return frozenset((side, k) for k in range(DEEP_LEVELS))
    return frozenset(((side, 0),))


# ---- S2: the asset classes ---------------------------------------------------------------------------------------------

def class_layout(config: Any) -> tuple[frozenset, str]:
    """S2: (foreign books, layout status) of a state's configuration.  A class is foreign when its minimum order size
    (its own configuration's), its price decimals or its volume decimals differ from the top-level configuration's; a
    value either side does not carry is no difference.  Status: single (no classes), same, foreign, unreadable (classes
    present but not readable -- nothing is foreign, as in v6.22)."""
    classes = _get(config, "classes")
    if not classes:
        return frozenset(), LAYOUT_SINGLE
    try:
        if isinstance(classes, (str, bytes, dict)):
            raise TypeError("classes is not a list")
        top_min = _finite(_get(config, "min_order_size"))
        top_pd, top_vd = _whole(_get(config, "priceDecimals")), _whole(_get(config, "volumeDecimals"))
        foreign: set[int] = set()
        for cls in classes:
            books = _get(cls, "books")
            if books is None or isinstance(books, (str, bytes, dict)):
                raise TypeError("a class without books")
            ids = [int(b) for b in books]
            cfg = _get(cls, "config")
            c_min = _finite(_get(cfg, "min_order_size"))
            c_pd = _whole(_get(cls, "priceDecimals"))
            if c_pd is None:
                c_pd = _whole(_get(cfg, "priceDecimals"))
            c_vd = _whole(_get(cls, "volumeDecimals"))
            if c_vd is None:
                c_vd = _whole(_get(cfg, "volumeDecimals"))
            differs = ((c_min is not None and top_min is not None
                        and not math.isclose(c_min, top_min, rel_tol=1e-9, abs_tol=1e-12))
                       or (c_pd is not None and top_pd is not None and c_pd != top_pd)
                       or (c_vd is not None and top_vd is not None and c_vd != top_vd))
            if differs:
                foreign.update(ids)
    except Exception:
        return frozenset(), LAYOUT_UNREADABLE
    return frozenset(foreign), (LAYOUT_FOREIGN if foreign else LAYOUT_SAME)


# ---- S3: one reset per state carrying start notices -------------------------------------------------------------------

def start_key(seq: Any, state_ts: Any) -> tuple | None:
    """S3: the state a start notice arrives in -- the agent's count of states (one per update()) and the state's
    timestamp, both fixed while one state's notices are dispatched; a later state has another count.  Every realization
    of a multi-asset layout sends its own notice for the same start, all in the first state of the run.  Not the request
    tick (it stands still while v5.0.3's quiet gate holds, and two runs' first states share their timestamp), not the
    simulation id (0.6.3 sends that first state without one, and a stale one could make a new run look like the last).
    None (an unreadable scope): no key, every notice resets (v6.22)."""
    t, ts = _whole(seq), _whole(state_ts)
    if t is None or ts is None:
        return None
    return ("state", t, ts)


__all__ = ["CANCEL_AGAINST", "GAP_BPS_DEFAULT", "LAYOUT_FOREIGN", "LAYOUT_SAME", "LAYOUT_SINGLE", "LAYOUT_UNREADABLE", "LEVELS_DEEP",
           "LEVELS_L0", "MODE_AUTO", "MODE_OFF", "MODE_ON", "REALIZED_SPEC", "REJECT_FOREIGN",
           "V623_VALIDATOR_063_VERSION", "against_side", "class_layout", "parse_gap_bps", "parse_levels", "parse_mode",
           "realized_active", "start_key", "validator_spec", "withheld"]
