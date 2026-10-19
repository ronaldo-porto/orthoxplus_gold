# SPDX-License-Identifier: MIT
"""v6.36: the pre-step withdrawal and the stale-side hold -- research_v636_step_withdrawal.

Why (10-18 JST; memory fill-quality-research-1018, scratchpad k101):

* The fundamental steps every 30 sim-s (the simulation's fp_update_period), and the state at 30n is the first to carry
  step n (OBSERVED k101, 591 / 591).  Miner takers put 73 % of their notional into the 2 s after a step; our quotes left
  resting across the step are picked off by them -- the only negative-realized cell of our class-0 fills (13.5 % of
  C - R, k101 round 1) -- because our cancels land ~48 ms after the step state while those takers print at 13-42 ms.
* Replay k101 round 2 on the calibrated engine (vm711 with the validator's delay model for every instruction, cancels
  included: cdl=1 idl=1), pre-registered Q1-Q5 PASS for "A_r1+D" (vm711 pst=1 ssd=1): E +31.4 / +31.2 / +37.3 % at
  d48 / d40 / d55, every third up (+42 / +25 / +17 %), class-0 R +32 %, beta -3.8 %, volume x0.966, the four older
  tapes +0.6 / +4.9 / -0.2 / -4.4 % (mean +0.2).

The rule (one switch, ``research_v636_step_withdrawal``), mirroring vm711's pst=1 / ssd=1 code:

* The grid: the step period P is the configuration's fp_update_period and the states come every publish_interval I.
  taos builds fp_update_period as the XML updatePeriod + 1 ns (models.py from_xml), so the simulator steps every
  K = (P - 1) / I states (a P that is itself a whole number of states is taken as is); a state's index is ts // I, its
  block index // K and its phase % K (vm711: sec // 30, sec % 30).  No readable P or I, or a P off the state grid: the
  switch does nothing and the state row says why (fail closed).  A class whose own configuration gives another grid
  keeps v6.35 on its books.
* The step record (``StepRecord``, vm711 _k101_steps): from the agent's own states since its start -- the state's own
  fundamental per book (v6.32 P1) -- at the first state of block n whose previous state lay in block n - 1, each book's
  step |ln(f_n / f_n-1)| (bps) joins its trailing record of WINDOW_STEPS (one 10,800-s making window of steps: 360) and
  its sign is the block's direction on the book; a state after a gap of blocks records no step for that block; a state
  without any fundamental breaks the record.  Zero steps count in the record and give no direction.  A clock that ran
  back (a new run) starts the record over.
* A (pre-step withdrawal, vm711 pst=1): on the last state before each step (phase K - 1), every order of ours on the
  book -- touch quote, deep level 0, ladder, exit -- with |p - mid| <= r x mid is removed (resting ones cancelled, new
  ones not sent); r = the median of the book's record once it holds MIN_OWN_STEPS, else of every book's record pooled
  (none before the first step: no withdrawal); mid = the book's last two-sided L1 mid (bid < ask).
* D (stale-side hold, vm711 ssd=1): on the step state and the next (HOLD_STATES), when this block's step was seen on the
  book, every order on the side the step moved against (fp up: the asks; fp down: the bids), reducing orders included,
  is removed the same way.  From the third state the policy quotes as usual.
* The book's withdrawal cancels go first in its instructions (the validator lands the first instruction per book at the
  base delay, later ones + U(5, 25) ms) and never exceed max_instructions_per_book: the cancel wins over a placement.

STRUCTURAL: the simulation's own step period and publish interval, the validator's making window and per-book
instruction delays, the state's own fundamental, the replay's rule as coded (vm711 pst=1 ssd=1).  PRE-REGISTERED (k101
SPEC_R2, before any result): the warm-up -- pooled over books until a book has MIN_OWN_STEPS steps -- and the two hold
states.  No constant here is sized from an observed metric.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any, Iterable

from research_v611_wire import venue_placed_price, wire_price
from research_v633_deep_layer import SIDE_BUY, SIDE_SELL
from research_v6_32_fee_making import WINDOW_NS
from research_v6_32_state_fp import state_map

V6_36_STEP_WITHDRAWAL_VERSION = "step_withdrawal_v6_36"
MAKING_WINDOW_NS = WINDOW_NS     # the validator's making window, 10,800 sim-s: the radius record's length in time
MIN_OWN_STEPS = 30               # PRE-REGISTERED (k101 SPEC_R2; vm711 pstm=30): a book's own radius from 30 steps on
HOLD_STATES = 2                  # PRE-REGISTERED (k101 SPEC_R2 D; vm711 ssd: phases 0, 1): the step state and the next
PRICE_EPS = 1e-12                # vm711's own comparison slack: |p - mid| <= r x mid + 1e-12
PROTOCOL_PERIOD_PAD_NS = 1       # taos models.py: fp_update_period = XML updatePeriod + 1

GRID_OK = "ok"                   # the step grid read from the configuration
GRID_NO_PERIOD = "no_period"     # no readable fp_update_period
GRID_NO_INTERVAL = "no_publish_interval"   # no readable publish_interval
GRID_OFF = "off_grid"            # a period that is not a whole number of states (with or without the protocol's 1 ns)

PULL = "PULL"                    # A: in the radius on the pre-step state
HOLD = "HOLD"                    # D: the stale side on the step state or the next

_EPS = 1e-9


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


def _whole(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return int(value)
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


# ---- the grid --------------------------------------------------------------------------------------------------------

def states_per_step(period_ns: Any, interval_ns: Any) -> tuple[str, int | None]:
    """(status, K): K states per fundamental step -- (P - 1) / I when that is whole (taos adds 1 ns to the XML period),
    else P / I when that is whole; GRID_OFF when neither is, GRID_NO_PERIOD / GRID_NO_INTERVAL when one is unreadable or
    not positive.  K must be at least HOLD_STATES + 1 (a pre-step state apart from the held ones)."""
    p, i = _whole(period_ns), _whole(interval_ns)
    if p is None or p <= 0:
        return GRID_NO_PERIOD, None
    if i is None or i <= 0:
        return GRID_NO_INTERVAL, None
    for whole in (p - PROTOCOL_PERIOD_PAD_NS, p):
        if whole > 0 and whole % i == 0 and whole // i >= HOLD_STATES + 1:
            return GRID_OK, whole // i
    return GRID_OFF, None


def step_grid(config: Any) -> dict[str, Any]:
    """The step grid of a state's configuration: {status, source, period_ns, interval_ns, k, step_ns, window_steps,
    excluded} -- the top level's fp_update_period and publish_interval (the first background's, with the run's step);
    ``excluded`` = the books of a class whose own configuration gives another grid (they keep v6.35).  Never raises."""
    out: dict[str, Any] = {"status": GRID_NO_PERIOD, "source": "config.fp_update_period", "period_ns": None,
                           "interval_ns": None, "k": None, "step_ns": None, "window_steps": None,
                           "excluded": frozenset()}
    try:
        p, i = _get(config, "fp_update_period"), _get(config, "publish_interval")
        out["period_ns"], out["interval_ns"] = _whole(p), _whole(i)
        status, k = states_per_step(p, i)
        out["status"] = status
        if status != GRID_OK:
            return out
        step_ns = int(k) * int(out["interval_ns"])
        out.update(k=int(k), step_ns=step_ns, window_steps=max(1, int(MAKING_WINDOW_NS) // step_ns))
        classes = _get(config, "classes")
        if classes and not isinstance(classes, (str, bytes, dict)):
            excluded: set[int] = set()
            for entry in list(classes):
                cfg = _get(entry, "config")
                cp, ci = _get(cfg, "fp_update_period"), _get(cfg, "publish_interval")
                if cp is None and ci is None:
                    continue                         # the class carries no grid of its own: the top level's
                st, kc = states_per_step(cp if cp is not None else p, ci if ci is not None else i)
                ic = _whole(ci if ci is not None else i)
                if st != GRID_OK or kc != k or ic != out["interval_ns"]:
                    excluded.update(int(b) for b in (_get(entry, "books") or ()))
            out["excluded"] = frozenset(excluded)
    except Exception:
        out["status"] = GRID_NO_PERIOD
        return out
    return out


def block_phase(ts_ns: Any, grid: Any) -> tuple[int, int] | None:
    """(block, phase) of a state on the grid: index = ts // I, block = index // K, phase = index % K (vm711:
    sec // 30, sec % 30); None without a usable grid or time."""
    try:
        if not grid or grid.get("status") != GRID_OK:
            return None
        ts = _whole(ts_ns)
        if ts is None or ts < 0:
            return None
        index = ts // int(grid["interval_ns"])
        k = int(grid["k"])
        return int(index // k), int(index % k)
    except Exception:
        return None


def pre_step(phase: Any, grid: Any) -> bool:
    """A's state: the last state before a step (phase K - 1)."""
    try:
        return phase is not None and grid is not None and int(phase) == int(grid["k"]) - 1
    except Exception:
        return False


def hold_phase(phase: Any) -> bool:
    """D's states: the step state and the next (phase < HOLD_STATES)."""
    try:
        return phase is not None and 0 <= int(phase) < HOLD_STATES
    except Exception:
        return False


# ---- the step record -------------------------------------------------------------------------------------------------

def _median(values: Iterable[float]) -> float | None:
    xs = sorted(values)
    n = len(xs)
    if n == 0:
        return None
    mid = n // 2
    return float(xs[mid]) if n % 2 else float((xs[mid - 1] + xs[mid]) / 2.0)


class StepRecord:
    """The agent's own record of the fundamental's steps since its start (vm711 _k101_steps / _k101_radius).

    ``observe`` takes every state in order: its time, its block on the grid (None: no grid this state) and its
    fundamentals {book: f > 0} (None or empty: the state carries none).  At the first state of a new block whose
    previous state lay in the block before, each book with a fundamental at both states records |ln(f / f_prev)| in
    bps (its trailing record keeps ``window_steps``) and the step's sign (0 for a zero step or a book without both);
    the block is then the record's step block.  A jump of more than one block records nothing (no step block); a state
    without a fundamental or a grid breaks the chain (the next new block records nothing).  A time earlier than the
    last one is a new run: the record starts over."""

    def __init__(self, window_steps: int = 360, min_own: int = MIN_OWN_STEPS) -> None:
        self.window_steps = max(1, int(window_steps))
        self.min_own = int(min_own)
        self.steps: dict[int, deque] = {}
        self.direction: dict[int, int] = {}
        self.step_block: int | None = None
        self.last_block: int | None = None
        self.last_fp: dict[int, float] = {}
        self.last_ts: int | None = None
        self.counts = {"states": 0, "steps_seen": 0, "step_gaps": 0, "breaks": 0, "resets": 0}

    def reset(self) -> None:
        self.steps.clear()
        self.direction.clear()
        self.step_block = None
        self.last_block = None
        self.last_fp = {}

    def resize(self, window_steps: Any) -> None:
        """A new window length (a new grid): every book's record keeps its newest ``window_steps``."""
        w = _whole(window_steps)
        if w is None or w < 1 or w == self.window_steps:
            return
        self.window_steps = w
        for b, q in list(self.steps.items()):
            self.steps[b] = deque(q, maxlen=w)

    def observe(self, ts_ns: Any, block: Any, fps: Any) -> str:
        """One state; returns what it did: "reset+..." prefix after a rewind, then "step", "gap", "same", "first" or
        "break"."""
        tag = ""
        ts = _whole(ts_ns)
        if ts is not None:
            if self.last_ts is not None and ts < self.last_ts:
                self.reset()
                self.counts["resets"] += 1
                tag = "reset+"
            self.last_ts = ts
        self.counts["states"] += 1
        blk = _whole(block)
        if blk is None or not fps:
            self.last_block, self.step_block, self.last_fp = None, None, {}
            self.counts["breaks"] += 1
            return tag + "break"
        what = "same"
        if self.last_block is None:
            what = "first"
        elif blk != self.last_block:
            if blk == self.last_block + 1:
                self.direction = {}
                for b, f in fps.items():
                    b = int(b)
                    f0 = self.last_fp.get(b)
                    try:
                        st = math.log(float(f) / float(f0)) * 1e4 if f0 is not None else float("nan")
                    except (TypeError, ValueError, ZeroDivisionError, OverflowError):
                        st = float("nan")
                    if not math.isfinite(st):
                        continue
                    q = self.steps.get(b)
                    if q is None:
                        q = deque(maxlen=self.window_steps)
                        self.steps[b] = q
                    q.append(abs(st))
                    self.direction[b] = (st > 0.0) - (st < 0.0)
                self.step_block = blk
                self.counts["steps_seen"] += 1
                what = "step"
            else:
                self.step_block = None
                self.counts["step_gaps"] += 1
                what = "gap"
        self.last_block = blk
        self.last_fp = {int(b): float(f) for b, f in fps.items()}
        return tag + what

    def radius(self, book: Any, pooled: Any = None) -> tuple[float | None, bool]:
        """(r, own): A's radius as a fraction of the mid -- the median of the book's record once it holds ``min_own``
        steps (own True), else the median of every book's record pooled (``pooled``: that median, when the caller has it
        already), None before the first step."""
        q = self.steps.get(_whole(book))
        if q is not None and len(q) >= self.min_own:
            return _median(q) / 1e4, True
        if pooled is None:
            pooled = self.pooled_bps()
        if pooled is None:
            return None, False
        return float(pooled) / 1e4, False

    def pooled_bps(self) -> float | None:
        """The median of every book's record pooled, in bps (None before the first step)."""
        return _median(x for q in self.steps.values() for x in q)

    def stale_side(self, book: Any, block: Any) -> str | None:
        """D's side on the book at a state of ``block``: the side the step moved against (up: the asks / SIDE_SELL,
        down: the bids / SIDE_BUY) when that block's step was seen and moved the book; else None."""
        blk = _whole(block)
        if blk is None or self.step_block is None or blk != self.step_block:
            return None
        d = self.direction.get(_whole(book), 0)
        return SIDE_SELL if d > 0 else SIDE_BUY if d < 0 else None

    def snapshot(self) -> dict[str, Any]:
        own = [_median(q) for q in self.steps.values() if len(q) >= self.min_own]
        own = sorted(x for x in own if x is not None)
        return {"books_with_steps": sum(1 for q in self.steps.values() if q),
                "books_own_radius": len(own), "window_steps": self.window_steps, "min_own_steps": self.min_own,
                "radius_pooled_bps": (None if not self.steps else _round(self.pooled_bps())),
                "radius_own_p50_bps": _round(_median(own)) if own else None,
                "step_block": self.step_block, **self.counts}


def _round(value: Any, digits: int = 4) -> float | None:
    v = _finite(value)
    return None if v is None else round(v, digits)


# ---- the decisions ---------------------------------------------------------------------------------------------------

def mid_of(bid: Any, ask: Any) -> float | None:
    """The L1 mid when the book is two-sided and not crossed (bid < ask), else None (vm711: B.bb[0] < B.ba[0])."""
    b, a = _finite(bid), _finite(ask)
    if b is None or a is None or not b < a:
        return None
    return 0.5 * (b + a)


def l1_of(book: Any) -> tuple[float | None, float | None]:
    """(best bid, best ask) of a state's book -- the lazy raw wire dict's first levels ("b" / "a", price "p") when it
    carries the side (nothing parsed), else the book object's bids / asks -- each None when that side is empty or
    unreadable."""
    raw = book if isinstance(book, dict) else getattr(book, "_raw", None)
    out = []
    for key, attr in (("b", "bids"), ("a", "asks")):
        price = None
        try:
            if isinstance(raw, dict) and (key in raw or isinstance(book, dict)):
                levels = raw.get(key) or ()
                first = levels[0] if levels else None
                price = first.get("p") if isinstance(first, dict) else getattr(first, "price", None)
            else:
                levels = getattr(book, attr, None) or ()
                price = getattr(levels[0], "price", None) if len(levels) else None
        except Exception:
            price = None
        v = _finite(price)
        out.append(v if v is not None and v > 0.0 else None)
    return out[0], out[1]


def placed_price(price: Any, decimals: Any, lift: bool) -> float | None:
    """The price a limit order rests at on the venue: v6.1.1's wire price (when its lift is on) truncated to the grid
    as the simulator does (research_v611_wire.venue_placed_price); the raw price when the grid is unreadable."""
    p = _finite(price)
    if p is None or p <= 0.0:
        return None
    try:
        wired = wire_price(p, decimals) if lift else p
        return float(venue_placed_price(wired, decimals))
    except Exception:
        return p


def in_radius(price: Any, mid: Any, radius: Any) -> bool:
    """vm711's test: |p - mid| <= r x mid + 1e-12."""
    p, m, r = _finite(price), _finite(mid), _finite(radius)
    if p is None or m is None or r is None:
        return False
    return abs(p - m) <= r * m + PRICE_EPS


def withdrawal(side: Any, price: Any, *, mid: Any = None, radius: Any = None, hold_side: Any = None) -> str | None:
    """Why A / D remove an order of ours this state -- PULL (A: a radius and a mid this state, the price inside it),
    HOLD (D: the order on the stale side) -- or None (it stays / is sent)."""
    if radius is not None and mid is not None and in_radius(price, mid, radius):
        return PULL
    if hold_side is not None and side == hold_side:
        return HOLD
    return None


def side_of(value: Any) -> str | None:
    """SIDE_BUY / SIDE_SELL from an order side (0 / 1), a direction (BUY / SELL: an enum or its name) or a token; None
    for anything else."""
    if value is None or isinstance(value, bool):
        return None
    name = getattr(value, "name", None)
    token = str(name if isinstance(name, str) else int(value) if isinstance(value, int) else value).strip().upper()
    if token in ("0", "BUY", "BID", "B", "ORDERDIRECTION.BUY"):
        return SIDE_BUY
    if token in ("1", "SELL", "ASK", "S", "ORDERDIRECTION.SELL"):
        return SIDE_SELL
    return None


def p50(values: Iterable[Any]) -> float | None:
    xs = [v for v in (_finite(x) for x in values or ()) if v is not None]
    return _median(xs)


__all__ = ["GRID_NO_INTERVAL", "GRID_NO_PERIOD", "GRID_OFF", "GRID_OK", "HOLD", "HOLD_STATES", "MAKING_WINDOW_NS",
           "MIN_OWN_STEPS", "PRICE_EPS", "PROTOCOL_PERIOD_PAD_NS", "PULL", "StepRecord",
           "V6_36_STEP_WITHDRAWAL_VERSION", "block_phase", "hold_phase", "in_radius", "l1_of", "mid_of", "p50",
           "placed_price", "pre_step", "side_of", "state_map", "states_per_step", "step_grid", "withdrawal"]
