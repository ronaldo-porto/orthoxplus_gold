# SPDX-License-Identifier: MIT
"""v6.8: the deep layer owns the book -- no touch fallback, a gate per book, its record kept across a restart, no
order that takes, and a volume line that follows the validator's rolling window.

Why (mainnet, sim 20260924_1653, measured 09-29 JST; scratchpad fleet8/).  The validator pays proportional_both
(0.5 x captured-spread share + 0.5 x net-alpha share of eligible uids); captured spread is each fill against the
31-print centred mid, 2 x min(buy, sell) per book.  Replayed on the recorded prints that arithmetic reproduces the
validator's making gauges (UID 94 4,308 raw vs 4,290; UID 104 5,388 vs 5,380).

* The touch pays nothing.  Every miner's maker fills at the touch captured +0.1..+2.7 bps in every sim-hour of this
  simulation and +0.0..+0.2 in the previous (trending) one, while fills 20+ ticks deep captured +9..+15.  Our own
  v6.3.2 layers (target 60k / making 65k ids, a 1.0-base two-sided touch maker) captured +0.0..+0.1 bps on 1.4-2.9M
  quote an hour per agent, 55% of UID 94's whole-simulation volume and 81-100% of the six newest uids' since 07:20.
* That volume is the cap.  The validator's 500k-per-book window is rolling: trade.shift_simulation_histories keeps
  (shifts) the old simulation's volume, so a unit traded at simulation time t frees at the same time of the next one.
* The fallback is what a shut board trades, and a restart shuts the board.  The v6.4 board opens on the MEAN paper
  alpha of every book, held in memory: UID 237's stayed open 04:35-09:12 JST (alpha +9..+11) into the quiet hours;
  its 09:36 restart and the 12:19 v6.7 launch left it shut (-0.1..-1.0) and the touch churn began.  The replay of
  the same prints without restarts kept it open 99.9% of the time.
* A few exits cross (the 80k family: ~1,000 non-post-only placements per agent-run).  Self-trade prevention acts
  within one agent only, so with eight uids a crossing order can meet another of ours.

The rules, one switch each (every one STRUCTURAL: the validator's and the venue's own arithmetic, no fitted number):

* S1 ``research_v68_no_touch_fallback``: a book the deep layer does not trade quotes nothing -- the v6.4 S2 idle
  book, shut board or open.  This is the model every v6.4-v6.7 replay ran (``notouch=1``).  A held position waits:
  the validator's alpha strips the drift a constant position is carried through, so holding scores exactly zero.
* S2 ``research_v68_book_gate``: each book's own record opens it (its depth known, its paper alpha not under -half
  the floor -- v6.3.3's per-book rule, unchanged); the pooled board no longer shuts every book at once.  Replay (sim
  66,000-76,200): making 3,969 / alpha 4,106 / skill 0.85 against 3,997 / 4,051 / 0.77 with the pooled gate.
* S3 ``research_v68_deep_persist``: the layer's per-book sweep depths and paper record ride in the per-uid session
  file (one per simulation) and come back into an empty layer on a restart; the paper orders and the previous touch
  are re-seen on the first state.  A new simulation has a new file, so nothing crosses a seam.
* S4 ``research_v68_post_only``: every limit order leaves post-only (the maker sanitizer then prices it at a legal
  resting price); an order that can only take -- market, IOC, FOK, close-positions -- is not sent.
* S5 ``research_v68_rolling_budget``: a book's budget line rises from the volume the venue reports when the book is
  first seen at the validator's sustainable rate, the cap per assessment period (86,400 sim-s), never above the cap;
  a new simulation shifts the line the way the validator shifts its volume history, and volume ageing out of the
  window is room, not a new budget.  (The v6.6 line ran to the cap at the simulation's end, so a book entered the
  next simulation capped: UID 104 at 347k/book with 11,550 sim-s left was allowed 17.3 quote/s, 3x the rate.)
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable

from research_v633_deep_layer import DeepBook
from research_v641_pace import ASSESSMENT_NS, PACE_WINDOW_NS, REBASE_MIN_JUMP_NS
from research_v66_pace_line import PaceLine

V68_DEEP_OWNS_VERSION = "deep_owns_v6_8"
DEEP_SESSION_KEY = "deep_layer_v6_8"
SWEEP_DECIMALS = 3                      # sweep depths are ticks; three decimals keep the float noise out of the file
TAKING_TYPES = frozenset({"PLACE_ORDER_MARKET", "CLOSE_POSITIONS"})
LIMIT_TYPE = "PLACE_ORDER_LIMIT"
TAKING_TIME_IN_FORCE = frozenset({2, 3})   # taos TimeInForce: GTC 0, GTT 1, IOC 2, FOK 3


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


# ---- S1: no touch fallback ----------------------------------------------------------------------------------------

def touch_fallback_retired(on: Any, deep_present: Any) -> bool:
    """A book the deep layer does not trade quotes nothing -- with the switch on and a deep layer to own the books.
    Without a layer (disabled, or failed to build) the v6.3.2 path stays the fallback."""
    return bool(on) and bool(deep_present)


# ---- S2: a gate per book ------------------------------------------------------------------------------------------

def per_book_gate(on: Any) -> bool:
    """The deep layer opens a book on that book's own record; the pooled board does not shut the others."""
    return bool(on)


# ---- S3: the layer's record across a restart ----------------------------------------------------------------------

def deep_state(layer: Any) -> dict[str, Any]:
    """The part of a DeepLayer a restart would lose: per book the sweep depths and the paper record's position,
    last print and windowed buckets; the layer's last prune."""
    books: dict[str, Any] = {}
    for book_id, db in sorted((getattr(layer, "books", None) or {}).items()):
        paper = db.paper
        books[str(int(book_id))] = {
            "sweeps": [round(float(x), SWEEP_DECIMALS) for x in db.sweeps],
            "inv": float(paper.inv),
            "p_last": None if paper.p_last is None else float(paper.p_last),
            "fills": int(paper.fills),
            "buckets": {str(int(k)): [float(v) for v in vals] for k, vals in sorted(paper.buckets.items())},
        }
    last = getattr(layer, "last_prune_ts", None)
    return {"version": V68_DEEP_OWNS_VERSION, "last_prune_ts": None if last is None else int(last), "books": books}


def _restored_book(row: Any) -> DeepBook | None:
    if not isinstance(row, dict):
        return None
    db = DeepBook()
    for x in row.get("sweeps") or ():
        v = _finite(x)
        if v is not None and v >= 1.0:              # sweep_depths keeps only depths of at least one tick
            db.sweeps.append(v)
    inv = _finite(row.get("inv"))
    db.paper.inv = inv if inv is not None else 0.0
    db.paper.p_last = _finite(row.get("p_last"))
    try:
        db.paper.fills = max(0, int(row.get("fills") or 0))
    except (TypeError, ValueError):
        db.paper.fills = 0
    buckets = row.get("buckets")
    if isinstance(buckets, dict):
        for key, vals in buckets.items():
            try:
                k = int(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(vals, (list, tuple)) or len(vals) != 4:
                continue
            fv = [_finite(v) for v in vals]
            if all(v is not None for v in fv):
                db.paper.buckets[k] = fv
    return db


def restore_deep(layer: Any, payload: Any) -> int:
    """Put a saved record back into a layer that has seen nothing yet; returns the books restored (0 = none).
    The paper orders and the previous touch are left unset: the first state re-places the one and re-reads the other,
    so a restart can neither book a paper fill through a stale price nor count the gap as a sweep."""
    if not isinstance(payload, dict) or getattr(layer, "books", None):
        return 0
    books = payload.get("books")
    if not isinstance(books, dict):
        return 0
    restored = 0
    for key, row in books.items():
        try:
            book_id = int(key)
        except (TypeError, ValueError):
            continue
        db = _restored_book(row)
        if db is None:
            continue
        layer.books[book_id] = db
        restored += 1
    if restored:
        last = payload.get("last_prune_ts")
        try:
            layer.last_prune_ts = None if last is None else int(last)
        except (TypeError, ValueError):
            layer.last_prune_ts = None
    return restored


# ---- S4: nothing that takes ---------------------------------------------------------------------------------------

def _kind(instruction: Any) -> str:
    value = instruction.get("type") if isinstance(instruction, dict) else getattr(instruction, "type", None)
    return str(value or "").upper()


def _field(instruction: Any, name: str) -> Any:
    return instruction.get(name) if isinstance(instruction, dict) else getattr(instruction, name, None)


def _time_in_force(instruction: Any) -> int | None:
    value = _field(instruction, "timeInForce")
    if value is None:
        return None
    name = str(getattr(value, "name", value)).upper()
    if name in ("IOC", "FOK"):
        return 2 if name == "IOC" else 3
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _is_post_only(instruction: Any) -> bool:
    flag = _field(instruction, "postOnly")
    if isinstance(flag, str):
        return flag.strip().lower() in {"true", "1", "yes"}
    return flag is True or flag == 1


def no_take(instructions: Iterable[Any], set_attr: Callable[[Any, str, Any], bool]) -> tuple[list, int, int]:
    """(kept, made post-only, dropped): every limit order post-only, every order that can only take dropped.
    ``set_attr(instruction, name, value)`` writes a field and returns whether it could; one it cannot make post-only
    is dropped rather than sent able to take."""
    kept: list = []
    converted = dropped = 0
    for instruction in instructions:
        kind = _kind(instruction)
        if kind in TAKING_TYPES:
            dropped += 1
            continue
        if kind == LIMIT_TYPE:
            if _time_in_force(instruction) in TAKING_TIME_IN_FORCE:
                dropped += 1
                continue
            if not _is_post_only(instruction):
                if not set_attr(instruction, "postOnly", True):
                    dropped += 1
                    continue
                converted += 1
        kept.append(instruction)
    return kept, converted, dropped


# ---- S5: the rolling budget line ----------------------------------------------------------------------------------

def rolling_line(cap: Any, used0: Any, start_ns: int, now_ns: int, *, assessment_ns: int = ASSESSMENT_NS,
                 window_ns: int = PACE_WINDOW_NS) -> float:
    """The volume a book may have in the validator's window by ``now``: ``used0`` rising at cap / assessment period
    (the rate a book can keep up for ever without being capped), one sampling window early, never above the cap."""
    c = max(0.0, _finite(cap) or 0.0)
    u0 = max(0.0, _finite(used0) or 0.0)
    span = max(1, int(assessment_ns))
    grown = u0 + c * (int(now_ns) - int(start_ns) + int(window_ns)) / float(span)
    return min(c, max(min(u0, c), grown))


class RollingPaceLine(PaceLine):
    """v6.6's PaceLine face (observe / paced / snapshot) with the S5 line: a sustainable rate from the first report,
    carried across a new simulation, never restarted by volume ageing out."""

    def observe(self, book_id: int, ts: int, used: Any) -> None:
        b, ts = int(book_id), int(ts)
        u = max(0.0, _finite(used) or 0.0)
        if self.last_ts is not None and ts < self.last_ts - REBASE_MIN_JUMP_NS:
            # a new simulation: the validator keeps the window and shifts it (new = new_ts - (old_ts - prev)),
            # so every line keeps its place instead of starting over at the carried volume
            shift = int(self.last_ts) - ts
            self.start = {k: (int(t0) - shift, u0) for k, (t0, u0) in self.start.items()}
            self.rebases += 1
            self.last_ts = ts
        self.last_ts = ts if self.last_ts is None else max(self.last_ts, ts)
        if b not in self.start:
            self.start[b] = (ts, u)
        self.last_used[b] = u

    def line(self, book_id: int, now_ns: int, cap: Any, sim_duration_ns: int | None = None) -> float | None:
        st = self.start.get(int(book_id))
        if st is None:
            return None
        t0, u0 = st
        return rolling_line(cap, u0, t0, now_ns, assessment_ns=self.assessment_ns, window_ns=self.window_ns)

    def snapshot(self) -> dict[str, Any]:
        out = super().snapshot()
        out["rolling"] = 1
        return out
