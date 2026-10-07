# SPDX-License-Identifier: MIT
"""v6.21: the young window -- its roll-off guarded, its pace line kept across a restart, its room spent evenly to its own
roll-off -- research_v621_young_window.

Why (mainnet 10-07/08 JST; scratchpad k36/window and k36/controller: replays vm636 with the validator's arithmetic, the
k34 fleet model and Monte Carlo for emission and prune risk):

* S1.  v6.20 calls a book aged at the first fall of its venue volume.  UID 22 (registered 10-07 23:22 JST: nothing of it
  can roll off before sim ~123,600) had 0 -> 3 -> 6 -> 11 -> 14 of 128 books marked aged between 23:33 and 00:19 JST,
  three of four jumps between the validator's 600-s volume keys, while the aged UIDs 187/104 aged only in the first
  100 sim-s after a key boundary (genuine pruning).  The validator adds a state's fills to its volume sums unrounded and
  rounds them in a later pass while another thread copies the sums into the next state: a reading taken between the
  passes is followed by one up to 0.5 x 10^-4 quote lower.  v6.20's test (any fall above max(1e-6, 1e-9 x prev)) trips
  on that below 50,000 quote per book -- every book of a fresh UID; 172/10's books (300-450k) cannot.  An aged book of a
  fresh UID runs the launch line uncapped at full size, v6.18's leak.  Replayed on UID 22's tape (3 seeds): v6.20 aged
  15 / 20 / 26 books, the size floor alone or the guard alone 0; a genuine roll-off ages at the same state as v6.20.
* S2.  v6.20's line lives in memory: a restart anchors every young book again at its usage and spreads its room over a
  new assessment period, and a fresh UID's restart loses its launch line.  k34 reward study: one unplanned restart at
  +24 h cut a new UID's 14-day reward 161 -> 131 alpha and raised its prune risk 7% -> 32%.  Replayed (vm636), a restart
  with the saved line, or with the launch line rebuilt from the window start, gives output identical to no restart.
* S3.  172 and 10 (young windows, relaunched 10-07 20:17 JST at 66% / 58% of the cap) spread their room over a whole new
  period: 0.22x / 0.30x sustainable until their own roll-off (~10-11 JST), their emission below the prune line around
  10-10/10-11.  A 172-like book at 70% of the cap (tape L3, 34,640 sim-s, doubled to its roll-off), v6.20 -> this rule:
  lowest 3-h making 37.9 -> 53.6 (+41%), last third 50.7 -> 95.8 (+89%), burn by thirds .89/.22/.13 -> .40/.30/.30x,
  making per 1M quote and order traffic within 5%; cap used at the roll-off 92.5% -> 97.0% mean (98.8% max, no book at
  the cap; a line aimed at 0.985 of the cap put 118 of 128 books at the cap ~14,200 sim-s early).  An unplanned restart
  at +17,000 s: with the saved anchor identical to none; without it, the no-burst start +7% burn in that window (anchored
  at the usage: a 0.49x burst; one band below: -24% making).  k34 fleet model, deployed 08:00 JST 10-08: 172 lowest
  emission 0.181 -> 0.251 (survival to 10-18 0.00 -> 0.50), 10 0.204 -> 0.276 (0.00 -> 0.76).  Fresh UID (k34 Monte
  Carlo, 1,000 paths): 14-day alpha 160.0 -> 158.3, prune probability 0.075 -> 0.056, median lowest emission 0.377 ->
  0.403 (the payback aimed at the roll-off: no flat stretch before it).

The rules (three switches; a book whose window start is unknown, or an aged one, keeps v6.20's line and order sizes --
with S1 it ages only on a genuine fall of at least one minimum fill, and S2 brings its aged mark back after a restart):

* The window start t_w of a book is the latest time it is known to have been empty: its own first report on an empty
  window (v6.15's launching book), or the UID's -- a first report on which every book was empty, kept in the session file
  (S2) or read from the UID's own earliest log of the simulation (research_v621_window_start).  The validator keys a
  fill at its 600-s sample and prunes a key only once it is one assessment period old, so nothing of that window can
  roll off before t_roll = t_w + 86,400 s - 600 s (one sampling window early, as v6.8 / v6.15 run).
* S1 ``research_v621_rolloff_guard``: a fall of the venue's volume makes a book aged only if it is at least one minimum
  fill at the book's price (venue minimum order x mid), on a state whose clock moved on for that book, and -- when its
  t_w is known -- not before t_roll.  Every other fall is counted and ignored (noise / rewind / guarded, with the largest).
* S2 ``research_v621_pace_persist``: every book's pace state (its first report, last reading, launching, aged, t_w, and the
  even-spend anchor) rides in the UID's session file and comes back into a new process of the same UID and simulation;
  a state saved in the previous simulation's clock is re-keyed the validator's way.  A first state on which every book
  reads empty drops it (the window emptied: a new registration).  Without a saved state the UID's t_w is derived once.
* S3 ``research_v621_even_spend``: a young book with a known t_w spends its room evenly to its own roll-off.  On its launch
  line (first seen empty, or at a restart no further than one band above it): v6.15's launch line for one making window,
  then an even payback from LAUNCH_RATE windows of the cap to the aim at t_roll -- a function of t_w only, so a restart
  keeps it, with the launch clip until t_w + one making window.  Any other book: a straight line from its anchor
  (t_a, u_a) to the aim A at t_roll, flat at A after it, with rate s = (cap - u_a) / (t_roll - t_a + 10,800 s), band =
  s x 10,800 s (one making window at its own rate) and A = cap - band (above the sustainable rate: A = T, band = the
  cap's making-window share, as v6.20).  Without a saved anchor u_a = usage - lead0, lead0 = band x (1 - s / sustainable
  rate): the lead a book trading at the sustainable rate holds on that line, so no start bursts.  Every order of the
  book is scaled by 1 - (usage - line) / band, clamped to [0, 1], never below the venue's minimum (v6.20).

STRUCTURAL: the validator's 600-s volume keys, its 86,400-s assessment period, its 10,800-s making window and the cap;
the venue's minimum order; the agent's own first report on an empty window.  No fitted constant.
"""
from __future__ import annotations

import math
from typing import Any

from research_v615_launch import EMPTY_WINDOW_QUOTE, LAUNCH_RATE, MAKING_WINDOW_NS, LaunchPaceLine
from research_v620_room_pace import RoomPaceLine
from research_v641_pace import ASSESSMENT_NS, PACE_WINDOW_NS, REBASE_MIN_JUMP_NS

V621_YOUNG_WINDOW_VERSION = "young_window_v6_21"
PACE_SESSION_KEY = "pace_line_v6_21"

FALL_NOISE = "noise"            # smaller than one minimum fill: the validator's rounding, not a roll-off
FALL_REWIND = "rewind"          # on a state whose clock did not move on for the book
FALL_GUARDED = "guarded"        # before the window can roll off
FALL_ROLLOFF = "rolloff"

EVEN_LAUNCH = "L"               # the launch line from t_w (no anchor)
EVEN_ANCHORED = "A"             # a straight line from (t_a, u_a) to the aim at t_roll

WINDOW_OWN_EMPTY_START = "OWN_EMPTY_START"   # this process's first state read every book empty: each book's own start

RESTORE_NONE = "none"
RESTORE_LIVE = "live"
RESTORE_VERSION = "version"
RESTORE_FOREIGN = "foreign"
RESTORE_INVALID = "invalid"
RESTORE_OTHER_SIM = "other_sim"
RESTORE_EMPTY_WINDOW = "empty_window"
RESTORE_RESTORED = "restored"
RESTORE_REKEYED = "rekeyed"


def _finite(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


# ---- S1: the roll-off guard ---------------------------------------------------------------------------------------

def earliest_rolloff(window_start_ns: Any, *, assessment_ns: int = ASSESSMENT_NS, window_ns: int = PACE_WINDOW_NS) -> int | None:
    """t_roll: no volume of a window that started at ``window_start_ns`` can roll off before it (one sampling window
    early: the validator keys a fill at its 600-s sample and prunes a key once it is one assessment period old)."""
    t = _int(window_start_ns)
    return None if t is None else t + int(assessment_ns) - int(window_ns)


def fall_kind(prev: Any, used: Any, ts: Any, last_ts: Any, floor: Any, rolloff: Any) -> str | None:
    """What a new reading of a book's venue volume says: None (no fall -- v6.20's test, a zero is no reading), FALL_NOISE
    (less than ``floor``, one minimum fill), FALL_REWIND (the book's clock did not move on), FALL_GUARDED (before
    ``rolloff``) or FALL_ROLLOFF (the window rolls off)."""
    p, u = _finite(prev), _finite(used)
    if p is None or u is None or not 0.0 < u < p - max(1e-6, 1e-9 * p):
        return None
    if p - u < max(0.0, _finite(floor) or 0.0):
        return FALL_NOISE
    t, lt, r = _int(ts), _int(last_ts), _int(rolloff)
    if t is not None and lt is not None and t <= lt:
        return FALL_REWIND
    if t is not None and r is not None and t < r:
        return FALL_GUARDED
    return FALL_ROLLOFF


# ---- S3: the even spend -------------------------------------------------------------------------------------------

def even_aim(cap: Any, used_a: Any, horizon_ns: Any, *, making_ns: int = MAKING_WINDOW_NS,
             assessment_ns: int = ASSESSMENT_NS) -> tuple[float, float, float]:
    """(aim A, rate s per ns, band) of a line spending the room above ``used_a`` evenly over ``horizon_ns``: the room is
    spread over the horizon plus one making window, the band is one making window at that rate and A = cap - band; a
    rate above the sustainable one aims at T with the cap's making-window share as the band (v6.20), continuous there."""
    c = max(0.0, _finite(cap) or 0.0)
    ua = _finite(used_a) or 0.0
    w = float(max(0, int(making_ns)))
    p = float(max(1, int(assessment_ns)))
    bm = c * w / p
    h = max(1.0, _finite(horizon_ns) or 1.0)
    s = (c - ua) / (h + w)
    if s > c / p:
        a = c - bm
        s = max(0.0, (a - ua) / h)
        b = bm
    else:
        s = max(0.0, s)
        b = s * w
        a = c - b
    if a <= ua:
        a, s = ua, 0.0
    return a, s, max(b, 1e-6 * c)


def even_line(cap: Any, start_ns: int, used_a: Any, roll_ns: int, now_ns: int, *, making_ns: int = MAKING_WINDOW_NS,
              assessment_ns: int = ASSESSMENT_NS) -> tuple[float, float]:
    """(line, band) of an anchored young book: from ``used_a`` at ``start_ns`` straight to the aim at ``roll_ns``, flat
    at the aim from there."""
    t0, tr, now = int(start_ns), int(roll_ns), int(now_ns)
    h = max(1, tr - t0)
    a, _s, b = even_aim(cap, used_a, h, making_ns=making_ns, assessment_ns=assessment_ns)
    if now >= tr:
        return a, b
    ua = _finite(used_a) or 0.0
    return ua + (a - ua) * max(0, now - t0) / float(h), b


def no_burst_anchor(cap: Any, used: Any, now_ns: int, roll_ns: int, *, making_ns: int = MAKING_WINDOW_NS,
                    assessment_ns: int = ASSESSMENT_NS) -> float:
    """u_a for a book anchored now without a saved anchor: its usage less lead0 = band x (1 - s / sustainable rate), the
    lead a book trading at the sustainable rate holds on the line it starts (solved with that line by bisection)."""
    c = max(0.0, _finite(cap) or 0.0)
    u = max(0.0, _finite(used) or 0.0)
    if c <= 0.0:
        return u
    w = float(max(0, int(making_ns)))
    p = float(max(1, int(assessment_ns)))
    rs = c / p
    h = max(1, int(roll_ns) - int(now_ns))

    def lead(ua: float) -> float:
        _a, s, b = even_aim(c, ua, h, making_ns=making_ns, assessment_ns=assessment_ns)
        return b * max(0.0, 1.0 - s / rs)

    lo, hi = u - c * w / p, u
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if u - lead(mid) - mid > 0.0:
            lo = mid
        else:
            hi = mid
    return max(0.0, 0.5 * (lo + hi))


def launch_even_line(cap: Any, window_start_ns: int, now_ns: int, *, rate: float = LAUNCH_RATE,
                     making_ns: int = MAKING_WINDOW_NS, assessment_ns: int = ASSESSMENT_NS,
                     window_ns: int = PACE_WINDOW_NS) -> tuple[float, float]:
    """(line, band) of a book on its launch line from ``window_start_ns``: ``rate`` x the sustainable rate for one making
    window (v6.15, its band the cap's making-window share), then an even payback to the aim at t_roll, flat there; one
    sampling window early."""
    c = max(0.0, _finite(cap) or 0.0)
    w = float(max(0, int(making_ns)))
    p = float(max(1, int(assessment_ns)))
    k = float(rate)
    x = float(int(now_ns) - int(window_start_ns) + int(window_ns))
    if x <= w:
        return c * k * max(0.0, x) / p, c * w / p
    lw = c * k * w / p
    a, _s, b = even_aim(c, lw, p - w, making_ns=making_ns, assessment_ns=assessment_ns)
    return lw + (a - lw) * min(1.0, (x - w) / max(1.0, p - w)), b


def band_scale(used: Any, line: Any, band: Any) -> float:
    """The factor on every order's size: 1 at or behind the line, 0 one band ahead of it (1 without a reading)."""
    u, lv, b = _finite(used), _finite(line), _finite(band)
    if u is None or lv is None or b is None or b <= 0.0:
        return 1.0
    return min(1.0, max(0.0, 1.0 - max(0.0, u - lv) / b))


# ---- the line -----------------------------------------------------------------------------------------------------

class YoungWindowLine(RoomPaceLine):
    """v6.20's room line with v6.21: S1 a fall is a roll-off only past the window's earliest roll-off and by at least
    one minimum fill, S2 the state a restart restores (pace_state / restore_pace), S3 the even spend to the roll-off for
    a young book whose window start is known.  Unknown window start or aged: v6.20 exactly."""

    def __init__(self, *, launch: bool = True, guard: bool = True, even: bool = True, **kwargs: Any):
        self.guard_on = bool(guard)
        self.even_on = bool(even)
        super().__init__(launch=launch, **kwargs)

    def reset(self) -> None:
        super().reset()
        self.window_start: dict[int, int] = {}       # book -> its own first report on an empty window
        self.uid_window_start: int | None = None     # every book empty then (session file / the UID's logs)
        self.window_status: str | None = None
        self.book_ts: dict[int, int] = {}            # book -> the last state time it was observed at
        self.even: dict[int, tuple] = {}             # book -> (EVEN_LAUNCH,) or (EVEN_ANCHORED, t_a, u_a)
        self.first_seen = 0                          # books this process saw first (not restored) ...
        self.first_empty = 0                         # ... and of them, first seen on an empty window
        self.falls: dict[str, int] = {FALL_NOISE: 0, FALL_REWIND: 0, FALL_GUARDED: 0, FALL_ROLLOFF: 0}
        self.noise_max = 0.0
        self.guarded_max = 0.0
        self.unaged = 0                              # restored aged marks refused (the window cannot roll yet)
        self.rekeys = 0
        self.sim_id: str | None = None               # the simulation this line's clock belongs to (set by the agent)
        self.sim_duration_ns: int | None = None

    def set_window(self, window_start_ns: Any, status: Any) -> None:
        """The UID's window start (every book empty then), or None, and how it was found."""
        self.uid_window_start = _int(window_start_ns)
        self.window_status = None if status is None else str(status)

    def window_start_ns(self, book_id: int) -> int | None:
        """The latest time this book is known to have been empty, or None."""
        known = [t for t in (self.window_start.get(int(book_id)), self.uid_window_start) if t is not None]
        return max(known) if known else None

    def rolloff(self, book_id: int) -> int | None:
        """The book's t_roll, or None while its window start is unknown."""
        return earliest_rolloff(self.window_start_ns(book_id), assessment_ns=self.assessment_ns, window_ns=self.window_ns)

    def _rekey(self, old_end: int) -> None:
        # a new simulation: the validator re-keys its volume history new = 0 - (old_end - t); old_end is at least the
        # last state seen, so a re-keyed window start is never later than the validator's
        shift = int(old_end)
        self.window_start = {b: int(t) - shift for b, t in self.window_start.items()}
        if self.uid_window_start is not None:
            self.uid_window_start = int(self.uid_window_start) - shift
        self.even = {b: (st if st[0] != EVEN_ANCHORED else (EVEN_ANCHORED, int(st[1]) - shift, st[2]))
                     for b, st in self.even.items()}
        self.book_ts = {}
        self.rekeys += 1

    def observe(self, book_id: int, ts: int, used: Any, fall_floor: Any = 0.0) -> None:
        b, t = int(book_id), int(ts)
        if self.last_ts is not None and t < self.last_ts - REBASE_MIN_JUMP_NS:
            self._rekey(max(int(self.last_ts), int(self.sim_duration_ns or 0)))
        new = b not in self.start
        if self.guard_on:
            prev = self.last_used.get(b)
            kind = fall_kind(prev, used, t, self.book_ts.get(b), fall_floor, self.rolloff(b))
            if kind is not None:
                self.falls[kind] += 1
                drop = float(prev) - float(used)
                if kind == FALL_NOISE:
                    self.noise_max = max(self.noise_max, drop)
                elif kind == FALL_GUARDED:
                    self.guarded_max = max(self.guarded_max, drop)
                elif kind == FALL_ROLLOFF:
                    self.aged.add(b)
            LaunchPaceLine.observe(self, book_id, ts, used)        # v6.8 / v6.15 bookkeeping, not v6.20's fall test
            if not self.launch_on:
                self.launching.discard(b)
        else:
            super().observe(book_id, ts, used)                     # v6.20 exactly
        if new and b in self.start:
            self.first_seen += 1
            t0, u0 = self.start[b]
            if u0 <= EMPTY_WINDOW_QUOTE:
                self.first_empty += 1
                self.window_start[b] = int(t0)
        self.book_ts[b] = t

    def _even_rate(self) -> float:
        return float(self.rate) if self.launch_on else 1.0

    def _even_state(self, book_id: int, now_ns: int, cap: Any) -> tuple | None:
        """The book's even-spend state (decided at its first use), or None: v6.20's line."""
        b = int(book_id)
        if not self.even_on or b not in self.start or b in self.aged:
            return None
        tw = self.window_start_ns(b)
        c = _finite(cap)
        if tw is None or c is None or c <= 0.0:
            return None
        st = self.even.get(b)
        if st is None:
            used = float(self.last_used.get(b, self.start[b][1]))
            lv, band = launch_even_line(c, tw, now_ns, rate=self._even_rate(), making_ns=self.making_ns,
                                        assessment_ns=self.assessment_ns, window_ns=self.window_ns)
            launch_open = int(now_ns) - int(tw) < self.making_ns
            if used <= EMPTY_WINDOW_QUOTE or (used <= lv + band + 1e-9 and (launch_open or used >= lv - band - 1e-9)):
                st = (EVEN_LAUNCH,)                          # on (or catching up inside) its launch window, or near it
            else:
                roll = earliest_rolloff(tw, assessment_ns=self.assessment_ns, window_ns=self.window_ns)
                st = (EVEN_ANCHORED, int(now_ns), float(no_burst_anchor(c, used, now_ns, roll, making_ns=self.making_ns,
                                                                         assessment_ns=self.assessment_ns)))
            self.even[b] = st
        return st

    def _even_line(self, book_id: int, st: tuple, now_ns: int, cap: Any) -> tuple[float, float]:
        tw = self.window_start_ns(book_id)
        if st[0] == EVEN_LAUNCH:
            return launch_even_line(cap, tw, now_ns, rate=self._even_rate(), making_ns=self.making_ns,
                                    assessment_ns=self.assessment_ns, window_ns=self.window_ns)
        roll = earliest_rolloff(tw, assessment_ns=self.assessment_ns, window_ns=self.window_ns)
        return even_line(cap, st[1], st[2], roll, now_ns, making_ns=self.making_ns, assessment_ns=self.assessment_ns)

    def line(self, book_id: int, now_ns: int, cap: Any, sim_duration_ns: int | None = None) -> float | None:
        st = self._even_state(book_id, now_ns, cap)
        if st is None:
            return super().line(book_id, now_ns, cap, sim_duration_ns)
        return self._even_line(book_id, st, now_ns, cap)[0]

    def scale(self, book_id: int, now_ns: int, used: Any, cap: Any, sim_duration_ns: int | None = None) -> float:
        if not self.young(book_id):
            return 1.0
        st = self._even_state(book_id, now_ns, cap)
        if st is None:
            return super().scale(book_id, now_ns, used, cap, sim_duration_ns)
        lv, band = self._even_line(book_id, st, now_ns, cap)
        return band_scale(used, lv, band)

    def launch_open(self, book_id: int, now_ns: int) -> bool:
        """A book on its launch line from t_w keeps the launch clip until t_w + one making window, after a restart too."""
        b = int(book_id)
        st = self.even.get(b) if (self.even_on and b not in self.aged) else None
        if st is not None and st[0] == EVEN_LAUNCH:
            tw = self.window_start_ns(b)
            return bool(self.launch_on) and tw is not None and int(now_ns) - int(tw) < self.making_ns
        return super().launch_open(book_id, now_ns)

    def snapshot(self) -> dict[str, Any]:
        out = super().snapshot()
        out["room"].update({"first_seen": int(self.first_seen), "first_empty": int(self.first_empty)})
        out["young"] = {
            "version": V621_YOUNG_WINDOW_VERSION, "guard_on": int(self.guard_on), "even_on": int(self.even_on),
            "falls": dict(self.falls), "noise_max": self.noise_max, "guarded_max": self.guarded_max,
            "unaged": int(self.unaged), "rekeys": int(self.rekeys),
            "window_start": self.uid_window_start, "window_status": self.window_status,
            "window_known": sum(1 for b in self.start if self.window_start_ns(b) is not None),
            "even_launch": sum(1 for st in self.even.values() if st[0] == EVEN_LAUNCH),
            "even_anchored": sum(1 for st in self.even.values() if st[0] == EVEN_ANCHORED),
        }
        return out


# ---- S2: the state a restart restores -----------------------------------------------------------------------------

def empty_window(readings: Any, n_books: Any) -> bool:
    """Does every one of the state's books read an empty window (no volume of ours in the validator's window)?"""
    n = _int(n_books)
    if not isinstance(readings, dict) or not n or len(readings) != n:
        return False
    values = [_finite(v) for v in readings.values()]
    return all(v is not None and v <= EMPTY_WINDOW_QUOTE for v in values)


def pace_state(line: Any, *, uid: Any) -> dict[str, Any]:
    """Every book's pace state for the session file: first report (t0, u0), last reading and time, launching, aged, its
    own window start and its even-spend state; the UID's window start; the clock (simulation, last state time)."""
    books: dict[str, Any] = {}
    for b, (t0, u0) in sorted(line.start.items()):
        row: dict[str, Any] = {"t0": int(t0), "u0": float(u0), "last": float(line.last_used.get(b, u0))}
        if b in line.launching:
            row["launch"] = 1
        if b in line.aged:
            row["aged"] = 1
        tw = line.window_start.get(b)
        if tw is not None:
            row["tw"] = int(tw)
        ts = line.book_ts.get(b)
        if ts is not None:
            row["ts"] = int(ts)
        st = line.even.get(b)
        if st is not None:
            row["even"] = [EVEN_LAUNCH] if st[0] == EVEN_LAUNCH else [EVEN_ANCHORED, int(st[1]), float(st[2])]
        books[str(int(b))] = row
    return {"version": V621_YOUNG_WINDOW_VERSION, "uid": _int(uid), "sim_id": line.sim_id, "last_ts": _int(line.last_ts),
            "window": {"start": line.uid_window_start, "status": line.window_status}, "books": books}


def _row(row: Any) -> dict[str, Any] | None:
    if not isinstance(row, dict):
        return None
    t0, u0 = _int(row.get("t0")), _finite(row.get("u0"))
    if t0 is None or u0 is None:
        return None
    last = _finite(row.get("last"))
    out: dict[str, Any] = {"t0": t0, "u0": max(0.0, u0), "last": max(0.0, u0 if last is None else last),
                           "launch": row.get("launch") in (1, True), "aged": row.get("aged") in (1, True),
                           "tw": _int(row.get("tw")), "ts": _int(row.get("ts")), "even": None}
    ev = row.get("even")
    if isinstance(ev, (list, tuple)) and len(ev) == 1 and ev[0] == EVEN_LAUNCH:
        out["even"] = (EVEN_LAUNCH,)
    elif (isinstance(ev, (list, tuple)) and len(ev) == 3 and ev[0] == EVEN_ANCHORED and _int(ev[1]) is not None
          and _finite(ev[2]) is not None):
        out["even"] = (EVEN_ANCHORED, _int(ev[1]), float(_finite(ev[2])))
    return out


def restore_pace(line: Any, payload: Any, *, uid: Any, sim_id: Any, now_ns: Any, duration_ns: Any = None,
                 readings: Any = None) -> str:
    """Put a saved pace state back into a line that has seen nothing yet; returns what happened (RESTORE_*).

    Only this UID's state is taken.  One saved in another simulation's clock is taken only when that clock is ahead of
    now (the save that crossed a seam, before the next one) and re-keyed the validator's way (old_end = max(last state,
    the simulation's length)); otherwise it is left.  A first state on which every book reads empty while the saved
    state holds volume drops it: the window emptied (a new registration of the UID).  A saved aged mark the guard refuses
    (the window start known, its earliest roll-off still ahead) is not restored."""
    if not isinstance(payload, dict):
        return RESTORE_NONE
    if getattr(line, "start", None):
        return RESTORE_LIVE
    if payload.get("version") != V621_YOUNG_WINDOW_VERSION:
        return RESTORE_VERSION
    mine = _int(uid)
    if mine is None or _int(payload.get("uid")) != mine:
        return RESTORE_FOREIGN
    books = payload.get("books")
    if not isinstance(books, dict):
        return RESTORE_INVALID
    now = _int(now_ns)
    last = _int(payload.get("last_ts"))
    saved_sim = payload.get("sim_id")
    shift, how = 0, RESTORE_RESTORED
    if saved_sim != sim_id:
        if saved_sim is None or sim_id is None or last is None or now is None or last <= now:
            return RESTORE_OTHER_SIM
        shift, how = max(last, _int(duration_ns) or 0), RESTORE_REKEYED
    rows: dict[int, dict[str, Any]] = {}
    for key, raw in books.items():
        b, row = _int(key), _row(raw)
        if b is not None and row is not None:
            rows[b] = row
    if not rows:
        return RESTORE_INVALID
    seen = readings if isinstance(readings, dict) else {}
    values = [_finite(seen.get(b)) for b in rows]        # a book without an account has no reading: never "empty"
    if values and all(v is not None and v <= EMPTY_WINDOW_QUOTE for v in values) \
            and any(r["last"] > EMPTY_WINDOW_QUOTE for r in rows.values()):
        return RESTORE_EMPTY_WINDOW
    window = payload.get("window") if isinstance(payload.get("window"), dict) else {}
    start = _int(window.get("start"))
    line.uid_window_start = None if start is None else start - shift
    line.window_status = None if window.get("status") is None else str(window.get("status"))
    for b, r in rows.items():
        line.start[b] = (r["t0"] - shift, r["u0"])
        line.last_used[b] = r["last"]
        if r["launch"] and line.launch_on:
            line.launching.add(b)
        if r["tw"] is not None:
            line.window_start[b] = r["tw"] - shift
        if r["ts"] is not None and not shift:
            line.book_ts[b] = r["ts"]
        st = r["even"]
        if st is not None:
            line.even[b] = st if st[0] == EVEN_LAUNCH else (EVEN_ANCHORED, st[1] - shift, st[2])
    for b, r in rows.items():
        if not r["aged"]:
            continue
        roll = line.rolloff(b)
        if line.guard_on and roll is not None and now is not None and now < roll:
            line.unaged += 1                # nothing of a window that young can have rolled off
        else:
            line.aged.add(b)
    line.last_ts = None if last is None else last - shift
    return how


__all__ = ["EVEN_ANCHORED", "EVEN_LAUNCH", "FALL_GUARDED", "FALL_NOISE", "FALL_REWIND", "FALL_ROLLOFF", "PACE_SESSION_KEY",
           "V621_YOUNG_WINDOW_VERSION", "WINDOW_OWN_EMPTY_START", "YoungWindowLine", "band_scale", "earliest_rolloff",
           "empty_window", "even_aim", "even_line", "fall_kind", "launch_even_line", "no_burst_anchor", "pace_state",
           "restore_pace"]
