# SPDX-License-Identifier: MIT
"""v6.2.11: the agent's score logic, rebuilt for the validator's final rung.

What the validator scores (taos-im/sn-79 main 7a3cad7, "20260921 - 0.6.1 final rung"; ``debeta.py`` is
byte-identical at 0234998).  kappa.weight 0, pnl.weight 0, debeta.weight 1:

    trading = w_make * making_rank + (1 - w_make) * skill_rank        (w_make 0.5 default; testnet runs 0.3)

* making = sum over books of 2*min(buy capture, sell capture), each fill booked against the mean of the 31
  prints centred on it, windowed over 3 sim-h; a negative book subtracts, only the total is clamped.
* skill = kappa_floored(per-book alpha, floor): alpha = MTM path integral - (inventory time-sum / prints)
  * drift, marked at the last print, windowed over 3 sim-h, only books the uid FILLED inside the window;
  floor = 0.5 * median |alpha| over every miner's traded books; >= 4 books; mean / cbrt(LPM3 + reg).
* ranks: among the POSITIVE values only, position / (n - 1): the smallest positive gets 0, a lone
  positive 1.  A zero or negative leg earns nothing.
* then a track-record EMA (half-life 3 sim-h; alpha = max(1 - 0.5^(dt/H), 1/(k+1)), seeded at the first
  nonzero score, so for ~H/interval rounds the standing is the plain mean of every score since), a soft
  floor at the median positive standing (softness 0.5), seeded sorted Pareto multipliers matched by
  rank, the slow post-Pareto moving average, and the weights.

Consequences for this agent, each a STRUCTURAL rule here (evidence in the v6.2.8 / v6.2.9 / v6.2.10 reads):

* R1 (Simple ``_v623_lifted``): the v6.1 no-loss floor protected the Kappa-3 clean-record premium.  Kappa
  weight is 0, so the premium buys nothing; on a PREMIUM book the floor forbids the one losing close that
  would lift it, so the book locks and its oldest lots ride the trend (testnet: UID 68's 43 floored books
  held 307 of 373 base; UID 82, lifted on 123 of 128 books by history, held 83 and scored skill +0.55).
  Every book is lifted.
* R2 (Simple ``_v625_clip``, ``held_book`` below): the cap pacer doubles a book's clip every sample the
  book trades below the cap pace, and a held book trades little because its exit is held -- the deficit
  is the exit, not the clip -- while the band (2 x clip) grows with it, so the adding side doubled into
  trends (0.25 -> 4 base on one book).  A held book gets the minimum order and its pace sample is
  re-seeded, so held time never reads as "below pace".
* R4 (``OwnAlphaMirror``): this uid's per-book alpha on the validator's own arithmetic, windowed, from
  the prints every state carries.  The book print count and drift are the whole board's (the validator
  counts every print), which reproduces the newcomer dilution exactly once the process has seen a full
  window.  Telemetry in v6.2.11: nothing decides on it yet.

The field-relative parts (the floor, both ranks) need every miner's legs.  On mainnet that is ~640 prints
and ~840 miner fill-sides per state, ~74 miners per book: an in-process all-miner mirror would hold
millions of history entries, so it is deliberately not built here.  Pure functions and one class.
"""
from __future__ import annotations

import math
import statistics
import time
from typing import Any, Iterable

V6211_SCORE_LOGIC_VERSION = "score_logic_v6_2_11"
LIFT_ALL_STATUS = "LIFT_ALL"                 # the window status reported for a book R1 lifted without a census
DEFAULT_LOOKBACK_NS = 10_800_000_000_000     # scoring.kappa.lookback (both de-beta legs share it)
BUCKET_NS = 60_000_000_000                   # history granularity: the window edge is exact to one bucket
PRUNE_EVERY_NS = 60_000_000_000              # the validator prunes on its own cadence; so does the mirror
REBASE_MIN_JUMP_NS = 3_600_000_000_000       # a clock that goes back more than this is a new simulation
FLOOR_SCALE = 0.5                            # scoring.debeta.floor_scale
W_MAKE_DEFAULT = 0.5                         # scoring.debeta.w_make at 7a3cad7
SCORE_EMA_HALFLIFE_NS = 10_800_000_000_000   # scoring.score_ema_halflife
FLOOR_PERCENTILE = 50.0                      # rewarding.floor.percentile
FLOOR_SOFTNESS = 0.5                         # rewarding.floor.softness


# ---- the validator's skill functions, verbatim (taos/im/validator/debeta.py @ 7a3cad7) --------------

def kappa_of_alpha(book_alphas):
    """Kappa-of-alpha: the per-uid DIRECTIONAL SKILL score from per-book drift-stripped alphas
    (each = book_total_pnl - mean_inventory*book_drift). Consistency across independent books
    (a drift-rider is positive only where drift helped (inconsistent) -> low. Needs >=4 books."""
    v = [float(x) for x in book_alphas]
    if len(v) < 4:
        return 0.0
    med = statistics.median(v)
    mad = max(statistics.median([abs(x - med) for x in v]), 1e-9)
    r = [x / mad for x in v]
    mean = sum(r) / len(r)
    sd = (sum((x - mean) ** 2 for x in r) / len(r)) ** 0.5
    lpm3 = sum(max(-x, 0.0) ** 3 for x in r) / len(r)
    reg = (abs(mean) + sd) ** 3 * 1e-3 + 1e-9
    return mean / (lpm3 + reg) ** (1.0 / 3.0)


def kappa_floored(book_alphas, floor):
    """kappa_of_alpha over only the books whose |alpha| clears the E5 magnitude floor (>= 4 required)."""
    q = [float(x) for x in book_alphas if abs(float(x)) >= floor]
    return kappa_of_alpha(q) if len(q) >= 4 else 0.0


def _rank01(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    rank = [0.0] * len(vals)
    denom = max(len(vals) - 1, 1)
    pos = 0
    for k, i in enumerate(order):
        if k and vals[i] != vals[order[k - 1]]:
            pos = k
        rank[i] = pos / denom
    return rank


def rank_positives(values):
    """The validator's default ("positives") scope of ``_rank_positive_leg``: rank among the positives."""
    idx = [i for i, v in enumerate(values) if v > 0]
    out = [0.0] * len(values)
    if len(idx) == 1:
        out[idx[0]] = 1.0
    elif idx:
        for i, r in zip(idx, _rank01([float(values[i]) for i in idx])):
            out[i] = r
    return out


# ---- the reward pipeline, for projections (taos/im/validator/reward.py @ 7a3cad7) -------------------

def trading_score(making_rank: float, skill_rank: float, w_make: float = W_MAKE_DEFAULT) -> float:
    return float(w_make) * float(making_rank) + (1.0 - float(w_make)) * float(skill_rank)


def percentile_linear(values: Iterable[float], pct: float) -> float:
    """numpy.percentile's default ("linear") rule without numpy."""
    s = sorted(float(v) for v in values)
    if not s:
        raise ValueError("percentile of an empty sequence")
    pos = (len(s) - 1) * float(pct) / 100.0
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def soft_floor_factor(score: float, scores: Iterable[float], *, percentile: float = FLOOR_PERCENTILE,
                      softness: float = FLOOR_SOFTNESS) -> float:
    """``apply_reward_floor``'s factor for one score against the field (1.0 when the floor is a no-op)."""
    active = [float(v) for v in scores if float(v) > 0]
    if len(active) < 2:
        return 1.0
    soft = min(max(float(softness), 1e-6), 1.0)
    thr = percentile_linear(active, percentile)
    if thr <= 0:
        return 1.0
    lo = thr * (1.0 - soft)
    s = float(score)
    if thr > lo:
        return min(max((s - lo) / (thr - lo), 0.0), 1.0)
    return 1.0 if s >= thr else 0.0


def track_record_step(ema: float | None, n: int, cur: float, *, ts: int, last_ts: int | None,
                      halflife_ns: int = SCORE_EMA_HALFLIFE_NS) -> tuple[float | None, int, int | None]:
    """One uid's ``apply_track_record_ema`` round: (standing, rounds counted, clock).

    Zero before the first nonzero score does not count (the newcomer guard); the first nonzero seeds
    the standing; after that alpha = max(1 - 0.5^(dt/H), 1/(k+1)).  A clock that went back is the sim
    seam: the time term drops to 0 and the annealing term alone applies.
    """
    seam = last_ts is not None and ts < last_ts
    if not (last_ts is None or ts > last_ts or seam):
        return ema, n, last_ts
    if seam or last_ts is None:
        alpha_dt = 0.0 if seam else 1.0
    else:
        alpha_dt = 1.0 - 0.5 ** ((ts - last_ts) / halflife_ns)
    if not (cur == 0 and n == 0):
        alpha = max(alpha_dt, 1.0 / (n + 1.0))
        ema = alpha * cur + (1.0 - alpha) * (cur if ema is None else ema)
        n += 1
    return ema, n, ts


# ---- v6.2.11.1: R1's startup seed ------------------------------------------------------------------

V62111_SEED_ALL_VERSION = "seed_all_v6_2_11_1"


def seed_all_bounds(venue_net_by_book: Any) -> tuple[int, float]:
    """The A1.9.5 startup seed's bounds when every book is lifted: every inherited position is tracked.

    The bound was sized for dust and single lots; after a restart it comes from the 0.25-lot universe
    caps (~32 base), so a restart holding more imports the largest books and orphans the rest -- traded
    as flat, never exited (UID 67, 2026-09-22: 3 of 128 books seeded, ~380 of 414 base orphaned).
    Tracking is not new exposure: the exposure caps still block new adds until releases bring it under.
    Finite on purpose -- ``build_seed_plan`` reads a non-finite bound as its 24-base default.
    """
    nets = []
    for value in dict(venue_net_by_book or {}).values():
        try:
            number = abs(float(value))
        except (TypeError, ValueError):
            continue
        if math.isfinite(number):
            nets.append(number)
    return max(1, len(nets)) + 1, sum(nets) + 1.0


# ---- R2 ---------------------------------------------------------------------------------------------

def held_book(net_base: Any, flat_eps: Any) -> bool:
    """A book holding inventory: its volume shortfall is its held exit, not its clip."""
    try:
        return abs(float(net_base)) > float(flat_eps)
    except (TypeError, ValueError):
        return False


# ---- R4: this uid's per-book alpha, on the validator's arithmetic ------------------------------------

def _trade(event: Any) -> dict[str, Any] | None:
    """A state trade event (pydantic TradeInfo or dict) as the validator's dict shape, or None."""
    if isinstance(event, dict):
        if event.get("y", "t") != "t":
            return None
        p, q, s, ma, ta = event.get("p"), event.get("q"), event.get("s"), event.get("Ma"), event.get("Ta")
    else:
        if getattr(event, "y", "t") != "t":
            return None
        p, q, s = getattr(event, "p", None), getattr(event, "q", None), getattr(event, "s", None)
        ma, ta = getattr(event, "Ma", None), getattr(event, "Ta", None)
    try:
        pf, qf, si = float(p), float(q), int(s)
    except (TypeError, ValueError):
        return None
    if not (math.isfinite(pf) and math.isfinite(qf)) or qf <= 0.0:
        return None
    return {"p": pf, "q": qf, "s": si, "Ma": -1 if ma is None else int(ma), "Ta": -1 if ta is None else int(ta)}


class OwnAlphaMirror:
    """Windowed de-beta alpha for ONE uid, per book, fed one state at a time.

    ``accumulate_book_mtm`` restricted to the tracked uid: its MTM path integral and inventory time-sum
    are the validator's for that uid, and the book's print count and drift are the whole board's (every
    print), because the validator's are.  Histories are kept per bucket and pruned on a fixed cadence;
    the window's first bucket is dropped whole, so the edge is exact to ``bucket_ns``.
    """

    def __init__(self, uid: int, *, lookback_ns: int = DEFAULT_LOOKBACK_NS, bucket_ns: int = BUCKET_NS,
                 prune_every_ns: int = PRUNE_EVERY_NS):
        self.uid = int(uid)
        self.lookback_ns = int(lookback_ns)
        self.bucket_ns = max(1, int(bucket_ns))
        self.prune_every_ns = max(0, int(prune_every_ns))
        self.reset()

    def reset(self) -> None:
        self.inv: dict[int, float] = {}          # the tracked uid's inventory; a key = in the validator's binv
        self.p_last: dict[int, float] = {}
        self.mtm: dict[int, float] = {}
        self.invsum: dict[int, float] = {}
        self.invn: dict[int, float] = {}
        self.drift: dict[int, float] = {}
        self.fills: dict[int, float] = {}
        self.hist: dict[str, dict[int, dict[int, float]]] = {k: {} for k in ("mtm", "invsum", "invn", "drift", "fills")}
        self.first_ts: int | None = None
        self.last_ts: int | None = None
        self.last_prune_ts: int | None = None
        self.states = 0
        self.prints = 0
        self.own_fills = 0
        self.rebases = 0
        self.last_ms = 0.0
        self.prune_ms = 0.0

    def _add(self, name: str, book: int, bucket: int, value: float) -> None:
        running = getattr(self, name)
        running[book] = running.get(book, 0.0) + value
        by_book = self.hist[name].setdefault(book, {})
        by_book[bucket] = by_book.get(bucket, 0.0) + value

    def ingest_state(self, ts: int, books: Iterable[tuple[int, Iterable[Any]]]) -> None:
        """One state: every book's trade events, in the order the state carries them."""
        started = time.perf_counter()
        ts = int(ts)
        if self.last_ts is not None and ts < self.last_ts - REBASE_MIN_JUMP_NS:
            self.reset()
            self.rebases += 1
        if self.first_ts is None:
            self.first_ts = ts
        self.last_ts = ts
        self.states += 1
        bucket = ts - ts % self.bucket_ns
        uid = self.uid
        for book_id, events in books:
            b = int(book_id)
            prev = self.p_last.get(b)
            inv = self.inv.get(b)
            n_trades = 0
            for ev in events or ():
                t = _trade(ev)
                if t is None:
                    continue
                n_trades += 1
                p = t["p"]
                if prev is not None and p != prev:
                    dp = p - prev
                    if inv:
                        self._add("mtm", b, bucket, inv * dp)
                    self._add("drift", b, bucket, dp)
                ma, ta = t["Ma"], t["Ta"]
                buyer, seller = (ta, ma) if t["s"] == 0 else (ma, ta)
                if buyer == uid:
                    inv = (inv or 0.0) + t["q"]
                if seller == uid:
                    inv = (inv or 0.0) - t["q"]
                if (ma == uid) != (ta == uid):
                    self._add("fills", b, bucket, 1.0)
                    self.own_fills += 1
                if inv is not None:
                    self._add("invsum", b, bucket, inv)
                self._add("invn", b, bucket, 1.0)
                prev = p
            if n_trades:
                self.prints += n_trades
                self.p_last[b] = prev
                if inv is not None:
                    self.inv[b] = inv
        if self.prune_every_ns == 0 or self.last_prune_ts is None or ts - self.last_prune_ts >= self.prune_every_ns:
            self.prune(ts)
        self.last_ms = (time.perf_counter() - started) * 1000.0

    def prune(self, now: int) -> None:
        """Drop every bucket older than the window and subtract its mass from the running sums."""
        started = time.perf_counter()
        threshold = int(now) - self.lookback_ns
        for name, hist in self.hist.items():
            running = getattr(self, name)
            for b in list(hist):
                by_bucket = hist[b]
                old = [k for k in by_bucket if k < threshold]
                if not old:
                    continue
                running[b] = running.get(b, 0.0) - sum(by_bucket.pop(k) for k in old)
                if not by_bucket:
                    del hist[b]
                    running.pop(b, None)
        self.last_prune_ts = int(now)
        self.prune_ms = (time.perf_counter() - started) * 1000.0

    def book_alphas(self) -> dict[int, float]:
        """{book: alpha} over the books the uid filled on inside the window (the validator's traded pool)."""
        out = {}
        for b, n_fills in self.fills.items():
            if n_fills <= 0 or b not in self.hist["fills"]:
                continue
            n = self.invn.get(b, 0.0)
            if n <= 0:
                continue
            out[b] = self.mtm.get(b, 0.0) - self.invsum.get(b, 0.0) / n * self.drift.get(b, 0.0)
        return out

    def coverage(self) -> float:
        """How much of the window the mirror has seen (1.0 once a full window has passed)."""
        if self.first_ts is None or self.last_ts is None or self.lookback_ns <= 0:
            return 0.0
        return max(0.0, min(1.0, (self.last_ts - self.first_ts) / self.lookback_ns))

    def snapshot(self, *, top: int = 5) -> dict[str, Any]:
        alphas = self.book_alphas()
        vals = sorted(alphas.values())
        out: dict[str, Any] = {
            "version": V6211_SCORE_LOGIC_VERSION, "states": self.states, "prints": self.prints,
            "own_fills": self.own_fills, "rebases": self.rebases, "coverage": round(self.coverage(), 4),
            "last_ms": round(self.last_ms, 3), "prune_ms": round(self.prune_ms, 3),
            "books": len(vals), "inventory_abs": round(sum(abs(v) for v in self.inv.values()), 4),
        }
        if not vals:
            return out
        abs_med = statistics.median(abs(v) for v in vals)
        own_floor = FLOOR_SCALE * abs_med
        out.update({
            "alpha_sum": round(sum(vals), 3), "positive": sum(1 for v in vals if v > 0),
            "negative": sum(1 for v in vals if v < 0),
            "alpha_p05": round(vals[int(0.05 * (len(vals) - 1))], 3), "alpha_p50": round(vals[len(vals) // 2], 3),
            "alpha_p95": round(vals[int(0.95 * (len(vals) - 1))], 3), "abs_median": round(abs_med, 3),
            # The validator's floor is the FIELD's median; this is the floor if we were the field.
            "skill_own_floor": round(kappa_floored(vals, own_floor), 4),
            "worst": [[int(b), round(a, 2), round(self.inv.get(b, 0.0), 4)]
                      for b, a in sorted(alphas.items(), key=lambda kv: kv[1])[:top]],
            "best": [[int(b), round(a, 2)] for b, a in sorted(alphas.items(), key=lambda kv: -kv[1])[:3]],
        })
        return out
