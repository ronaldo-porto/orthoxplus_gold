# SPDX-License-Identifier: MIT
"""v6.2.0: an agent-side mirror of the validator's de-beta MAKING term, from the state's own prints.

The validator (taos/im/validator/debeta.py at upstream commit 0234998, "0.6.1 rung 2") scores making
as, per uid, the sum over books of 2·min(buy capture, sell capture), clamped at 0 on the total, where
every fill on a book earns its buyer (mid − price)·qty and its seller the negation, against the mean of
the 31 prints centred on the fill (15 before, 15 after).  A fill is held until its 15 forward prints
arrive, or 60 sim-s pass, then booked at the finalising state's timestamp; sums are windowed over the
kappa lookback (3 sim-h).  The validator reads exactly the trade events the miner also receives in
each state (`book.events` with type 't', carrying price, quantity, side, maker and taker agent ids).

The capture arithmetic below is the validator's, function for function (`centered_mid`,
`_attribute_capture`, `_finalize_ready`, `accumulate_book_capture`, `flush_capture_state`,
`balanced_reward_per_book`, `_hadd2`, `prune_hist_2level`), with ONE deliberate narrowing: capture is
recorded only for the uids in `track`, because the validator keeps a per-timestamp history for every
(uid, book) pair on the board and this agent needs its own.  The print window and the finalisation
rule are untouched, so the mirrored number is the validator's for the tracked uid.  Tests prove it
against the upstream functions on randomised batches.

The mirror is telemetry: it changes no decision.  It exists so a testnet read can place the run on the
field's making ladder without the validator's 183 MB metrics scrape.
"""
from __future__ import annotations

import math
import time
from collections import defaultdict
from typing import Any, Iterable

V62_MAKING_VERSION = "making_mirror_v6_2_0"
CAPTURE_W = 15                          # scoring.debeta.centered_window
CAPTURE_FLUSH_NS = 60_000_000_000       # force-finalize a pending fill after 60 sim-s without W forward prints
DEFAULT_LOOKBACK_NS = 10_800_000_000_000  # scoring.kappa.lookback
REBASE_MIN_JUMP_NS = 3_600_000_000_000  # a clock that goes back more than this is a new simulation


# ---- the validator's functions ------------------------------------------------------------------

def _hadd2(hist, k1, k2, ts, val):
    d = hist.setdefault(k1, {}).setdefault(k2, {})
    d[ts] = d.get(ts, 0.0) + val


def prune_hist_2level(hist, running, threshold):
    """hist {k1:{k2:{ts:val}}}, running {k1:{k2:val}}. Drop ts<threshold, subtract pruned mass from
    running. Keeps running == sum(kept). A pair whose history empties is removed from both maps."""
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


def centered_mid(prices, W):
    """Symmetric (non-lagging) benchmark mid. In a pure trend it equals the current price, so capture
    measures only deviation from the trend line (drift removed). W = half-window in trades."""
    n = len(prices)
    if n == 0:
        return []
    csum = [0.0]
    for p in prices:
        csum.append(csum[-1] + p)
    mid = []
    for i in range(n):
        lo = max(0, i - W)
        hi = min(n, i + W + 1)
        mid.append((csum[hi] - csum[lo]) / (hi - lo))
    return mid


def _attribute_capture(buy_sums, sell_sums, book_id, t, mid, buy_hist, sell_hist, ts, track=None):
    """Book one fill's capture against `mid`: buyer gets (mid-price)*q, seller the negation.
    `track`: the uids recorded (None = every uid, the validator's rule)."""
    buy_cap = (mid - float(t["p"])) * float(t["q"])
    ma = t.get("Ma", -1)
    ta = t.get("Ta", -1)
    buyer, seller = (ta, ma) if int(t["s"]) == 0 else (ma, ta)
    if buyer is not None and buyer >= 0 and (track is None or buyer in track):
        buy_sums[buyer][book_id] = buy_sums[buyer].get(book_id, 0.0) + buy_cap
        if buy_hist is not None:
            _hadd2(buy_hist, buyer, book_id, ts, buy_cap)
    if seller is not None and seller >= 0 and (track is None or seller in track):
        sell_sums[seller][book_id] = sell_sums[seller].get(book_id, 0.0) - buy_cap
        if sell_hist is not None:
            _hadd2(sell_hist, seller, book_id, ts, -buy_cap)


def _finalize_ready(st, book_id, buy_sums, sell_sums, W, buy_hist, sell_hist, ts,
                    now_ns, flush_ns, force=False, track=None):
    """Finalize every pending fill whose forward window is complete (W prints arrived after it),
    stale (older than flush_ns of sim time), or force-flushed."""
    prices, pend = st["prices"], st["pend"]
    base, n = st["base"], st["n"]
    while pend:
        idx, arrive_ns, t = pend[0]
        if not (force or n - 1 >= idx + W
                or (now_ns is not None and arrive_ns is not None and now_ns - arrive_ns >= flush_ns)):
            break
        lo = max(0, idx - W) - base
        hi = min(n, idx + W + 1) - base
        window = prices[lo:hi]
        _attribute_capture(buy_sums, sell_sums, book_id, t, sum(window) / len(window),
                           buy_hist, sell_hist, ts, track=track)
        pend.pop(0)
    new_base = max(base, (pend[0][0] if pend else n) - W)
    if new_base > base:
        del prices[:new_base - base]
        st["base"] = new_base


def accumulate_book_capture(buy_sums, sell_sums, book_id, trades, W, *,
                            buy_hist=None, sell_hist=None, ts=None,
                            mid_state=None, flush_ns=CAPTURE_FLUSH_NS, track=None):
    """Accumulate per-uid two-sided spread capture for ONE book's ordered trade batch.  Each trade is
    dict-like with keys p, q, s, Ma, Ta.  side==0 => taker buys / maker sells; side==1 => maker buys /
    taker sells.  Self-trades (Ma==Ta) earn nothing but their prints still shape the mid.  With
    mid_state the print window is carried ACROSS batches: each fill is held until W forward prints
    arrive, then booked against its full centred window at the finalising call's ts."""
    if mid_state is None:
        prices = [float(t["p"]) for t in trades]
        mids = centered_mid(prices, W)
        for t, mid in zip(trades, mids):
            if t.get("Ma", -1) == t.get("Ta", -1):
                continue
            _attribute_capture(buy_sums, sell_sums, book_id, t, mid, buy_hist, sell_hist, ts, track=track)
        return
    st = mid_state.setdefault(book_id, {"prices": [], "pend": [], "base": 0, "n": 0})
    for t in trades:
        st["prices"].append(float(t["p"]))
        if t.get("Ma", -1) != t.get("Ta", -1):
            st["pend"].append((st["n"], ts, t))
        st["n"] += 1
    _finalize_ready(st, book_id, buy_sums, sell_sums, W, buy_hist, sell_hist, ts, ts, flush_ns, track=track)


def flush_capture_state(mid_state, buy_sums, sell_sums, W, *,
                        buy_hist=None, sell_hist=None, ts=None,
                        flush_ns=CAPTURE_FLUSH_NS, force=False, track=None):
    """Finalize stale pending fills on EVERY book (a book with no new trades never reaches
    accumulate_book_capture, so the live loop calls this each cycle)."""
    for book_id, st in list(mid_state.items()):
        _finalize_ready(st, book_id, buy_sums, sell_sums, W, buy_hist, sell_hist, ts, ts, flush_ns,
                        force=force, track=track)


def balanced_reward_per_book(capture_buy_sums, capture_sell_sums, uids):
    """Two-sided capture summed PER BOOK: sum_b 2*min(buy_b, sell_b), clamped at 0 on the total."""
    out = {}
    for u in uids:
        cb = capture_buy_sums.get(u) or {}
        cs = capture_sell_sums.get(u) or {}
        tot = 0.0
        for b in set(cb) | set(cs):
            tot += 2.0 * min(cb.get(b, 0.0), cs.get(b, 0.0))
        out[u] = max(0.0, tot)
    return out


# ---- the agent's mirror --------------------------------------------------------------------------

def trade_dict(event: Any) -> dict[str, Any] | None:
    """A state trade event (pydantic TradeInfo or dict) as the validator's dict shape, or None."""
    if isinstance(event, dict):
        if event.get("y", "t") != "t":
            return None
        p, q, s = event.get("p"), event.get("q"), event.get("s")
        ma, ta = event.get("Ma"), event.get("Ta")
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


class MakingMirror:
    """Windowed two-sided capture for the tracked uid, fed one state at a time."""

    def __init__(self, uid: int, *, lookback_ns: int = DEFAULT_LOOKBACK_NS, W: int = CAPTURE_W,
                 flush_ns: int = CAPTURE_FLUSH_NS, sample_ns: int = 0, prune_every_ns: int = 0,
                 keep_seam: bool = False):
        self.uid = int(uid)
        self.track = {self.uid}
        self.lookback_ns = int(lookback_ns)
        self.W = int(W)
        self.flush_ns = int(flush_ns)
        # v6.3.2 S1: the validator keys every capture on its sampled clock (600 s), finalises a pending fill on that
        # clock and prunes every 60 s; 0 keeps the v6.2.0 behaviour (raw timestamps, pruned every state).
        self.sample_ns = max(0, int(sample_ns))
        self.prune_every_ns = max(0, int(prune_every_ns))
        self.keep_seam = bool(keep_seam)
        self.seam_saved: dict[str, Any] | None = None
        self.reset()

    def reset(self) -> None:
        self.buy_sums = defaultdict(dict)
        self.sell_sums = defaultdict(dict)
        self.buy_hist = {}
        self.sell_hist = {}
        self.mid_state = {}
        self.last_ts: int | None = None
        self.last_prune_ts: int | None = None
        self.states = 0
        self.prints = 0
        self.fills = 0
        self.rebases = 0
        self.last_ms = 0.0

    def ingest_state(self, ts: int, books: Iterable[tuple[int, Iterable[Any]]]) -> None:
        """One state: every book's trade events, in the order the state carries them."""
        started = time.perf_counter()
        ts = int(ts)
        if self.last_ts is not None and ts < self.last_ts - REBASE_MIN_JUMP_NS:
            # A new simulation restarts the clock; the validator rebases its histories onto it.  The
            # mirror starts over: its window is the new sim's anyway within 3 sim-h.
            saved = None
            if self.keep_seam:
                saved = {"buy": dict(self.buy_sums.get(self.uid) or {}), "sell": dict(self.sell_sums.get(self.uid) or {}),
                         "ts": self.last_ts}
            rebases = self.rebases
            self.reset()
            self.rebases = rebases + 1
            if saved is not None:
                self.seam_saved = saved
        self.last_ts = ts
        self.states += 1
        key = ts - ts % self.sample_ns if self.sample_ns else ts
        for book_id, events in books:
            trades = []
            for ev in events or ():
                t = trade_dict(ev)
                if t is not None:
                    trades.append(t)
            if not trades:
                continue
            self.prints += len(trades)
            self.fills += sum(1 for t in trades if t["Ma"] == self.uid or t["Ta"] == self.uid)
            accumulate_book_capture(
                self.buy_sums, self.sell_sums, int(book_id), trades, self.W,
                buy_hist=self.buy_hist, sell_hist=self.sell_hist, ts=key,
                mid_state=self.mid_state, flush_ns=self.flush_ns, track=self.track,
            )
        flush_capture_state(self.mid_state, self.buy_sums, self.sell_sums, self.W,
                            buy_hist=self.buy_hist, sell_hist=self.sell_hist, ts=key,
                            flush_ns=self.flush_ns, track=self.track)
        if not self.prune_every_ns or self.last_prune_ts is None or ts - self.last_prune_ts >= self.prune_every_ns:
            threshold = ts - self.lookback_ns
            prune_hist_2level(self.buy_hist, self.buy_sums, threshold)
            prune_hist_2level(self.sell_hist, self.sell_sums, threshold)
            self.last_prune_ts = ts
        self.last_ms = (time.perf_counter() - started) * 1000.0

    def making(self) -> float:
        return balanced_reward_per_book(self.buy_sums, self.sell_sums, [self.uid]).get(self.uid, 0.0)

    def book_capture(self, book_id: Any) -> tuple[float, float]:
        """This book's windowed (buy, sell) capture for the tracked uid -- the validator's own terms."""
        try:
            b = int(book_id)
        except (TypeError, ValueError):
            return 0.0, 0.0
        cb = self.buy_sums.get(self.uid) or {}
        cs = self.sell_sums.get(self.uid) or {}
        return float(cb.get(b, 0.0)), float(cs.get(b, 0.0))

    def snapshot(self) -> dict[str, Any]:
        cb = self.buy_sums.get(self.uid) or {}
        cs = self.sell_sums.get(self.uid) or {}
        books = set(cb) | set(cs)
        per_book = {b: 2.0 * min(cb.get(b, 0.0), cs.get(b, 0.0)) for b in books}
        return {
            "v62_making_version": V62_MAKING_VERSION,
            "making": round(self.making(), 4),
            "books_with_fills": len(books),
            "books_two_sided": sum(1 for b in books if cb.get(b, 0.0) > 0.0 and cs.get(b, 0.0) > 0.0),
            "books_positive": sum(1 for v in per_book.values() if v > 0.0),
            "buy_capture": round(sum(cb.values()), 4),
            "sell_capture": round(sum(cs.values()), 4),
            "pending_fills": sum(len(st["pend"]) for st in self.mid_state.values()),
            "states": self.states, "prints": self.prints, "fills": self.fills, "rebases": self.rebases,
            "mirror_ms": round(self.last_ms, 3),
        }
