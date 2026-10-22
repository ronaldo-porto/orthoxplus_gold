# SPDX-License-Identifier: MIT
"""v6.2.9: flow selection -- the skill leg, from the one signal that predicts our adverse selection.

(The rule set and its evidence are documented once the design is final; this module currently holds the
alpha mirror and the flow classifier.)
"""
from __future__ import annotations

import math
import statistics
import time
from collections import defaultdict
from typing import Any, Iterable

V629_FLOW_SELECTION_VERSION = "flow_selection_v6_2_9"

DEFAULT_LOOKBACK_NS = 10_800_000_000_000   # scoring.kappa.lookback: the de-beta window, 3 sim-h
REBASE_MIN_JUMP_NS = 3_600_000_000_000     # a clock that goes back more than this is a new simulation
HIST_BUCKET_NS = 60_000_000_000            # history granularity; the window edge moves in 60 sim-s steps
MIN_SKILL_BOOKS = 4                        # kappa_of_alpha needs >= 4 books

FLOW_AGAINST = "AGAINST"
FLOW_WITH = "WITH"
FLOW_NEUTRAL = "NEUTRAL"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


# ---- the flow classifier -----------------------------------------------------------------------------

def flow_threshold(window: Any) -> float:
    """The noise level of a mean of ``window`` trade signs: 1/sqrt(N).  From the estimator, not fitted."""
    n = max(1.0, _finite(window, 20.0))
    return 1.0 / math.sqrt(n)


def side_sign(side: Any) -> int:
    """+1 for the buy side (a long position), -1 for the sell side (a short), 0 otherwise."""
    token = str(side or "").lower()
    if token in ("buy", "bid", "long"):
        return 1
    if token in ("sell", "ask", "short"):
        return -1
    return 0


def flow_relation(sign: Any, persistence: Any, threshold: Any) -> str:
    """How net taker flow stands against a position (or an entry) of ``sign``.

    ``persistence`` is the mean of the last N taker signs (+1 a taker buy, -1 a taker sell).  AGAINST when
    the flow runs against the position by more than the noise level, WITH when it runs with it by at least
    the noise level, NEUTRAL otherwise.  A missing signal is NEUTRAL: no rule fires on no information.
    """
    s = int(_finite(sign))
    if s == 0 or persistence is None:
        return FLOW_NEUTRAL
    p = _finite(persistence, float("nan"))
    if not math.isfinite(p):
        return FLOW_NEUTRAL
    thr = max(0.0, _finite(threshold))
    x = s * p
    if x < -thr:
        return FLOW_AGAINST
    if x >= thr:
        return FLOW_WITH
    return FLOW_NEUTRAL


# ---- the validator's alpha arithmetic, narrowed to the tracked uid -----------------------------------
#
# taos/im/validator/debeta.py, accumulate_book_mtm (mark_mode "last", the default and what both
# validators run) and book_alphas_by_book.  Per print: every tracked holder's MTM gains inv*dp and the
# book's drift gains dp; after the print the buyer's inventory rises and the seller's falls, then every
# tracked holder's inventory time-sum gains its inventory and the book's print count gains one.  alpha =
# MTM - (invsum / invn) * drift, i.e. sum_t (q_{t-1} - qbar) * dp_t over the window.  ONE narrowing, the
# same one the making mirror makes: only the uids in ``track`` are carried.  Alpha is invariant to a
# constant inventory offset, so starting the tracked inventory at zero reproduces the validator's number.

def _uid(x: Any) -> int:
    return -1 if x is None else int(x)


def _hadd2(hist, k1, k2, ts, val):
    d = hist.setdefault(k1, {}).setdefault(k2, {})
    d[ts] = d.get(ts, 0.0) + val


def _hadd1(hist, k, ts, val):
    d = hist.setdefault(k, {})
    d[ts] = d.get(ts, 0.0) + val


def prune_hist_2level(hist, running, threshold):
    """hist {k1:{k2:{ts:val}}}, running {k1:{k2:val}}: drop ts < threshold, keep running == sum(kept)."""
    for k1 in list(hist):
        d2 = hist[k1]
        for k2 in list(d2):
            tsd = d2[k2]
            pruned = 0.0
            keep = {}
            for ts, v in tsd.items():
                if ts >= threshold:
                    keep[ts] = v
                else:
                    pruned += v
            if len(keep) != len(tsd):
                d2[k2] = keep
                if pruned and k1 in running and k2 in running.get(k1, {}):
                    running[k1][k2] = running[k1][k2] - pruned
            if not keep:
                del d2[k2]
                if k1 in running:
                    running[k1].pop(k2, None)
        if not d2:
            del hist[k1]
            if k1 in running and not running[k1]:
                del running[k1]


def prune_hist_1level(hist, running, threshold):
    """hist {k:{ts:val}}, running {k:val}: drop ts < threshold, keep running == sum(kept)."""
    for k in list(hist):
        tsd = hist[k]
        pruned = 0.0
        keep = {}
        for ts, v in tsd.items():
            if ts >= threshold:
                keep[ts] = v
            else:
                pruned += v
        if len(keep) != len(tsd):
            hist[k] = keep
            if pruned and k in running:
                running[k] = running[k] - pruned
        if not keep:
            del hist[k]
            running.pop(k, None)


def accumulate_book_mtm(mtm, invsum, invn, inv, p_last, book_id, trades, *, track,
                        mtm_hist=None, invsum_hist=None, invn_hist=None,
                        drift=None, drift_hist=None, ts=None):
    """The validator's accumulate_book_mtm (mark "last") for the uids in ``track`` only."""
    binv = inv.setdefault(book_id, {})
    prev = p_last.get(book_id)
    for t in trades:
        p = float(t["p"])
        q = float(t["q"])
        if prev is not None and p != prev:
            dp = p - prev
            for uid, iv in binv.items():
                if iv:
                    mtm[uid][book_id] = mtm[uid].get(book_id, 0.0) + iv * dp
                    if mtm_hist is not None:
                        _hadd2(mtm_hist, uid, book_id, ts, iv * dp)
            if drift is not None:
                drift[book_id] = drift.get(book_id, 0.0) + dp
                if drift_hist is not None:
                    _hadd1(drift_hist, book_id, ts, dp)
        ma = _uid(t.get("Ma", -1))
        ta = _uid(t.get("Ta", -1))
        buyer, seller = (ta, ma) if int(t["s"]) == 0 else (ma, ta)
        if buyer >= 0 and buyer in track:
            binv[buyer] = binv.get(buyer, 0.0) + q
        if seller >= 0 and seller in track:
            binv[seller] = binv.get(seller, 0.0) - q
        for uid, iv in binv.items():
            invsum[uid][book_id] = invsum[uid].get(book_id, 0.0) + iv
            if invsum_hist is not None:
                _hadd2(invsum_hist, uid, book_id, ts, iv)
        invn[book_id] = invn.get(book_id, 0) + 1
        if invn_hist is not None:
            _hadd1(invn_hist, book_id, ts, 1)
        prev = p
    p_last[book_id] = prev


def book_alphas_by_book(mtm, invsum, invn, drift):
    """{uid: {book: MTM - (invsum/invn) * drift}} -- the validator's windowed finalizer."""
    out = {}
    for uid in set(mtm) | set(invsum):
        by_book = {}
        for b in set(mtm.get(uid, {})) | set(invsum.get(uid, {})):
            n = invn.get(b, 0)
            if n <= 0:
                continue
            mi = invsum.get(uid, {}).get(b, 0.0) / n
            tb = mtm.get(uid, {}).get(b, 0.0)
            by_book[b] = tb - mi * drift.get(b, 0.0)
        out[uid] = by_book
    return out


def kappa_of_alpha(book_alphas) -> float:
    """The validator's directional skill: MAD-normalised mean over a cube-root lower partial moment."""
    v = [float(x) for x in book_alphas]
    if len(v) < MIN_SKILL_BOOKS:
        return 0.0
    med = statistics.median(v)
    mad = max(statistics.median([abs(x - med) for x in v]), 1e-9)
    r = [x / mad for x in v]
    mean = sum(r) / len(r)
    sd = (sum((x - mean) ** 2 for x in r) / len(r)) ** 0.5
    lpm3 = sum(max(-x, 0.0) ** 3 for x in r) / len(r)
    reg = (abs(mean) + sd) ** 3 * 1e-3 + 1e-9
    return mean / (lpm3 + reg) ** (1.0 / 3.0)


def kappa_floored(book_alphas, floor) -> float:
    q = [float(x) for x in book_alphas if abs(float(x)) >= float(floor)]
    return kappa_of_alpha(q) if len(q) >= MIN_SKILL_BOOKS else 0.0


class AlphaMirror:
    """Windowed per-book alpha for the tracked uid, fed one state at a time.  Telemetry only.

    The validator keeps a per-timestamp history for every (uid, book); this mirror keeps it for the tracked
    uid in 60 sim-s buckets, so the window edge moves in 60 s steps against the validator's per-state
    steps (a 0.6% edge on a 3 h window) and memory stays at ~180 buckets per book.
    """

    def __init__(self, uid: int, *, lookback_ns: int = DEFAULT_LOOKBACK_NS, bucket_ns: int = HIST_BUCKET_NS):
        self.uid = int(uid)
        self.track = {self.uid}
        self.lookback_ns = int(lookback_ns)
        self.bucket_ns = max(1, int(bucket_ns))
        self.reset()

    def reset(self) -> None:
        self.mtm = defaultdict(dict)
        self.invsum = defaultdict(dict)
        self.invn: dict = {}
        self.inv: dict = {}
        self.p_last: dict = {}
        self.drift: dict = {}
        self.mtm_hist: dict = {}
        self.invsum_hist: dict = {}
        self.invn_hist: dict = {}
        self.drift_hist: dict = {}
        self.last_ts: int | None = None
        self.last_prune_ts: int | None = None
        self.states = 0
        self.prints = 0
        self.rebases = 0
        self.last_ms = 0.0

    def ingest_state(self, ts: int, books: Iterable[tuple[int, Iterable[Any]]], trade_dict) -> None:
        """One state: every book's trade events, in the order the state carries them.

        ``trade_dict`` converts one event to the validator's dict shape (or None); the making mirror's
        converter is passed in so both mirrors read the prints identically.
        """
        started = time.perf_counter()
        ts = int(ts)
        if self.last_ts is not None and ts < self.last_ts - REBASE_MIN_JUMP_NS:
            self.reset()
            self.rebases += 1
        self.last_ts = ts
        self.states += 1
        bucket = ts - (ts % self.bucket_ns)
        for book_id, events in books:
            trades = []
            for ev in events or ():
                t = trade_dict(ev)
                if t is not None:
                    trades.append(t)
            if not trades:
                continue
            self.prints += len(trades)
            accumulate_book_mtm(
                self.mtm, self.invsum, self.invn, self.inv, self.p_last, int(book_id), trades,
                track=self.track, mtm_hist=self.mtm_hist, invsum_hist=self.invsum_hist,
                invn_hist=self.invn_hist, drift=self.drift, drift_hist=self.drift_hist, ts=bucket,
            )
        if self.last_prune_ts is None or ts - self.last_prune_ts >= self.bucket_ns:
            threshold = ts - self.lookback_ns
            prune_hist_2level(self.mtm_hist, self.mtm, threshold)
            prune_hist_2level(self.invsum_hist, self.invsum, threshold)
            prune_hist_1level(self.invn_hist, self.invn, threshold)
            prune_hist_1level(self.drift_hist, self.drift, threshold)
            self.last_prune_ts = ts
        self.last_ms = (time.perf_counter() - started) * 1000.0

    def alphas(self, traded: Iterable[Any] | None = None) -> dict[int, float]:
        """This uid's per-book alpha; restricted to ``traded`` (books with fills in the window) if given."""
        by_book = book_alphas_by_book(self.mtm, self.invsum, self.invn, self.drift).get(self.uid, {})
        if traded is None:
            return dict(by_book)
        keep = {int(b) for b in traded}
        return {b: a for b, a in by_book.items() if int(b) in keep}

    def snapshot(self, traded: Iterable[Any] | None = None) -> dict[str, Any]:
        al = self.alphas(traded)
        vals = list(al.values())
        mags = sorted(abs(v) for v in vals)
        return {
            "v629_flow_selection_version": V629_FLOW_SELECTION_VERSION,
            "books": len(vals),
            "books_positive": sum(1 for v in vals if v > 0.0),
            "alpha_sum": round(sum(vals), 4),
            "alpha_median_abs": round(statistics.median(mags), 4) if mags else 0.0,
            "skill_all_books": round(kappa_of_alpha(vals), 4),
            "alphas": {int(b): round(a, 3) for b, a in sorted(al.items())},
            "states": self.states, "prints": self.prints, "rebases": self.rebases,
            "mirror_ms": round(self.last_ms, 3),
        }
