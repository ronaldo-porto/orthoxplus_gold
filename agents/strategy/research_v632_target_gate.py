# SPDX-License-Identifier: MIT
"""v6.3.2 S2: the trend target trades a book only while its own paper record would earn skill there, and a gated or
stopped book keeps its two-sided making layer.

Why (mainnet UID 94 on v6.3.1, sim 20260924_1653, ticks 400-13,600, measured 09-25):

* v6.3's premise was measured on the previous simulation: consecutive returns correlated +0.22 at 10 s up to +0.64 at
  300 s.  In the new one (validator 0.6.2, "auto-calibrated simulation config") the median book's 120-s return
  autocorrelation is negative in every 1,000-state block (-0.04 to -0.29).  Ideal +/-2 positions set by the 120-s
  move's sign and reached 60 states late earn +89 over a 3-h window on mids (the previous simulation: +25,883).
* So the target lost ~-880 in its first 1,600 states and the book stop then held 70.7% of all book-time paused
  (90.5 of 128 books on average) for ~13 wall-h -- and a paused book quoted no making layer either.
* Cost-inclusive replays of the new simulation on the validator's 0.6.2 arithmetic: 11 of 11 directional variants lose;
  a two-sided maker quoting one tick inside scores making 331 and alpha -67, the target with the stop off 81 and -1,000.

The rules (one switch, ``research_v632_target_gate``):

* Paper record.  Per book, the position the target rule would hold had each new target been reached PAPER_LAG_NS
  after the rule set it, marked on the mid at every state and scored on the validator's alpha arithmetic --
  sum(position x mid change) - mean(position) x sum(mid change) -- over the validator's window: the kappa lookback on
  600-s sampled keys, pruned at the threshold now - lookback.  PAPER_LAG_NS is the order backstop (v6.2.15 S1): a
  target order rests at most that long before it is re-placed at the new touch.  The record runs whether or not the
  gate lets the target trade.
* Gate.  A book's target is open while its own paper alpha clears the skill floor (a book's alpha counts toward skill
  only above it; once open it stays until under half the floor) OR the board's mean paper alpha does (same
  fractions): the simulation sets the regime for every book, and one book's 3-h record is noisy where the board's
  128 are not.  The v6.3 realized-alpha stop (R5) closes a book whatever the records say.  Every book starts closed
  (no evidence yet); a new simulation clears every record and gate.
  Replayed on both simulations (validator arithmetic, costs, 0.6.2 skill rules, last 3-h window): the new one --
  alpha +58 and making 318 against v6.3.1's -178 and 0 (the board's record never opens; 17 books open on their own);
  the old one -- skill +2.84 on 101 books over the floor (v6.3.1 +4.64; the field's best today ~1.9).  Own records
  alone: +58 / +1.50; the board alone: -67 / +2.14.
* Never idle.  A closed or stopped book targets zero and keeps the making layer's two sides around it.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

V632_TARGET_GATE_VERSION = "target_gate_v6_3_2"

PAPER_LAG_NS = 50_000_000_000          # = V6215_BACKSTOP_MS: the longest a target order rests before it is re-placed
SAMPLE_NS = 600_000_000_000            # scoring.activity.trade_volume_sampling_interval: the validator's history keys
LOOKBACK_NS = 10_800_000_000_000       # scoring.kappa.lookback
REBASE_MIN_JUMP_NS = 3_600_000_000_000  # a clock that goes back more than this is a new simulation (the mirrors' rule)
OPEN_FLOOR_FRACTION = 1.0              # open at the skill floor: below it a book's alpha does not count toward skill
CLOSE_FLOOR_FRACTION = 0.5             # close under half the floor (the v6.3 stop's own fraction)


def sampled_key(ts: int, sample_ns: int = SAMPLE_NS) -> int:
    """The validator's history key for a state timestamp (``update_trade_volumes``'s sampled_timestamp)."""
    s = max(1, int(sample_ns))
    return (int(ts) // s) * s


class PaperBook:
    """One book's paper record: the lagged rule position and its windowed alpha sums, bucketed like the validator."""

    __slots__ = ("targets", "last_rule", "position", "last_mid", "buckets")

    def __init__(self) -> None:
        self.targets: deque = deque()          # (ts, rule target) changes not yet effective, oldest first
        self.last_rule: float | None = None    # the rule's latest target
        self.position = 0.0                    # the paper position held since the last state
        self.last_mid: float | None = None
        self.buckets: dict[int, list[float]] = {}   # key -> [sum pos*dmid, sum pos, n, sum dmid]

    def _position_at(self, ts: int, lag_ns: int) -> float:
        """The rule target set at or before ts - lag (0 before the first one); older changes are dropped."""
        cutoff = int(ts) - int(lag_ns)
        pos = self.position
        while self.targets and self.targets[0][0] <= cutoff:
            pos = self.targets.popleft()[1]
        return pos

    def observe(self, ts: int, mid: float | None, rule_target: float, *, lag_ns: int = PAPER_LAG_NS,
                sample_ns: int = SAMPLE_NS) -> None:
        """One state: mark the position held since the last state on the mid's change, then take the new target."""
        ts = int(ts)
        if mid is not None and math.isfinite(float(mid)):
            m = float(mid)
            if self.last_mid is not None:
                dm = m - self.last_mid
                pos = self.position
                b = self.buckets.get(sampled_key(ts, sample_ns))
                if b is None:
                    b = [0.0, 0.0, 0.0, 0.0]
                    self.buckets[sampled_key(ts, sample_ns)] = b
                b[0] += pos * dm
                b[1] += pos
                b[2] += 1.0
                b[3] += dm
            self.last_mid = m
        rule = float(rule_target)
        if self.last_rule is None or rule != self.last_rule:
            self.targets.append((ts, rule))
            self.last_rule = rule
        self.position = self._position_at(ts, lag_ns)

    def prune(self, now: int, *, lookback_ns: int = LOOKBACK_NS) -> None:
        threshold = int(now) - int(lookback_ns)
        for key in [k for k in self.buckets if k < threshold]:
            del self.buckets[key]

    def alpha(self) -> float:
        """sum(pos x dmid) - mean(pos) x sum(dmid) over the kept buckets (0 before any mark)."""
        s_pdm = s_p = n = s_dm = 0.0
        for b in self.buckets.values():
            s_pdm += b[0]; s_p += b[1]; n += b[2]; s_dm += b[3]
        if n <= 0:
            return 0.0
        return s_pdm - (s_p / n) * s_dm


def gate_step(is_open: bool, paper_alpha: float, floor: float, *, stopped: bool,
              open_fraction: float = OPEN_FLOOR_FRACTION, close_fraction: float = CLOSE_FLOOR_FRACTION) -> bool:
    """The gate after this state: opens at open_fraction x floor, closes under close_fraction x floor or when stopped."""
    f = float(floor)
    a = float(paper_alpha)
    if stopped or not (math.isfinite(a) and math.isfinite(f)) or f <= 0.0:
        return False
    if is_open:
        return a >= float(close_fraction) * f
    return a >= float(open_fraction) * f


class TargetGate:
    """Every book's paper record and gate state."""

    def __init__(self, *, lag_ns: int = PAPER_LAG_NS, sample_ns: int = SAMPLE_NS, lookback_ns: int = LOOKBACK_NS,
                 prune_every_ns: int = 60_000_000_000):
        self.lag_ns = int(lag_ns)
        self.sample_ns = int(sample_ns)
        self.lookback_ns = int(lookback_ns)
        self.prune_every_ns = int(prune_every_ns)
        self.rebases = 0
        self.reset()

    def reset(self) -> None:
        self.books: dict[int, PaperBook] = {}
        self.open: set[int] = set()
        self.last_prune_ts: int | None = None
        self.opens = 0
        self.closes = 0
        self.pool_open = False
        self.pool_alpha = 0.0
        self.pool_opens = 0

    def update_pool(self, floor: float) -> bool:
        """Once per request, before the books: the board's mean paper alpha (every book with a record so far) through
        the same open/close fractions as one book's."""
        if not self.books:
            return self.pool_open
        mean = sum(p.alpha() for p in self.books.values()) / len(self.books)
        self.pool_alpha = mean
        was = self.pool_open
        self.pool_open = gate_step(was, mean, floor, stopped=False)
        if self.pool_open and not was:
            self.pool_opens += 1
        return self.pool_open

    def step(self, book_id: int, ts: int, mid: float | None, rule_target: float, floor: float, *,
             stopped: bool) -> bool:
        """Record this state for one book and return whether its target may trade now."""
        b = int(book_id)
        paper = self.books.get(b)
        if paper is None:
            paper = PaperBook()
            self.books[b] = paper
        paper.observe(ts, mid, rule_target, lag_ns=self.lag_ns, sample_ns=self.sample_ns)
        was = b in self.open
        own = gate_step(was, paper.alpha(), floor, stopped=stopped)
        now_open = bool((own or self.pool_open) and not stopped)
        if now_open and not was:
            self.open.add(b)
            self.opens += 1
        elif was and not now_open:
            self.open.discard(b)
            self.closes += 1
        return now_open

    def maybe_prune(self, now: int) -> None:
        """The validator's cadence: prune when prune_every_ns has passed since the last prune.  A clock that went back
        by more than the mirrors' rebase jump is a new simulation: every record and gate starts over (on its own, so
        the gate never marks one simulation's position on the next one's prices even with the v6.3.1 reset off)."""
        now = int(now)
        if self.last_prune_ts is not None and now < self.last_prune_ts - REBASE_MIN_JUMP_NS:
            rebases = self.rebases
            self.reset()
            self.rebases = rebases + 1
        if self.last_prune_ts is not None and now - self.last_prune_ts < self.prune_every_ns:
            return
        for paper in self.books.values():
            paper.prune(now, lookback_ns=self.lookback_ns)
        self.last_prune_ts = now

    def alphas(self) -> dict[int, float]:
        return {b: p.alpha() for b, p in self.books.items()}

    def snapshot(self, *, top: int = 3) -> dict[str, Any]:
        al = self.alphas()
        vals = sorted(al.values())
        out: dict[str, Any] = {
            "version": V632_TARGET_GATE_VERSION, "books": len(al), "open_books": len(self.open),
            "opens": self.opens, "closes": self.closes, "lag_s": self.lag_ns / 1e9,
            "pool_open": int(self.pool_open), "pool_alpha": round(self.pool_alpha, 3), "pool_opens": self.pool_opens,
            "rebases": self.rebases,
        }
        if vals:
            out.update({
                "paper_alpha_sum": round(sum(vals), 3), "paper_positive": sum(1 for v in vals if v > 0),
                "paper_p50": round(vals[len(vals) // 2], 3),
                "paper_best": [[int(b), round(a, 2)] for b, a in sorted(al.items(), key=lambda kv: -kv[1])[:top]],
            })
        return out
