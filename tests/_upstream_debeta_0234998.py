# SPDX-License-Identifier: MIT
"""The validator's de-beta capture functions, VERBATIM from taos-im/sn-79 main commit 0234998
("20260918 - 0.6.1 rung 2", taos/im/validator/debeta.py).  The oracle the v6.2 making mirror is proven against.
"""
from collections import defaultdict

CAPTURE_FLUSH_NS = 60_000_000_000

def _hadd2(hist, k1, k2, ts, val):
    d = hist.setdefault(k1, {}).setdefault(k2, {})
    d[ts] = d.get(ts, 0.0) + val


def prune_hist_2level(hist, running, threshold):
    """hist {k1:{k2:{ts:val}}}, running {k1:{k2:val}}. Drop ts<threshold, subtract pruned mass from
    running. Keeps running == sum(kept). A pair whose history empties is removed from both maps: it has
    no fills inside the window, and a key left behind would carry a running total of a floating-point
    residue into every pool built from the map (the skill floor's median, kappa's book count), where
    such residues accumulate until they outnumber the live pairs.

    Args:
        hist: ``{k1: {k2: {ts: val}}}`` history, pruned in place.
        running: ``{k1: {k2: val}}`` running totals, reduced by the pruned mass.
        threshold: Timestamps strictly below this are dropped.
    """
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


def caps_from_fills(fills):
    """fills: iterable of (buyer, seller, price, mid, volume). Returns {agent: [buy_cap, sell_cap]}.
    Per-trade buyer capture (mid-price)*v + seller capture (price-mid)*v == 0 (zero-sum: no making
    is created ex nihilo; a ring can only TRANSFER it, hence H1)."""
    caps = defaultdict(lambda: [0.0, 0.0])
    for (buyer, seller, price, mid, vol) in fills:
        if buyer is not None:
            caps[buyer][0] += (mid - price) * vol
        if seller is not None:
            caps[seller][1] += (price - mid) * vol
    return caps


def _attribute_capture(buy_sums, sell_sums, book_id, t, mid, buy_hist, sell_hist, ts):
    """Book one fill's capture against `mid`: buyer gets (mid-price)*q, seller the negation
    (zero-sum per fill under ANY mid, so a degraded mid can shift capture between counterparties
    but never mint it)."""
    buy_cap = (mid - float(t["p"])) * float(t["q"])
    ma = t.get("Ma", -1)
    ta = t.get("Ta", -1)
    buyer, seller = (ta, ma) if int(t["s"]) == 0 else (ma, ta)
    if buyer is not None and buyer >= 0:
        buy_sums[buyer][book_id] = buy_sums[buyer].get(book_id, 0.0) + buy_cap
        if buy_hist is not None:
            _hadd2(buy_hist, buyer, book_id, ts, buy_cap)
    if seller is not None and seller >= 0:
        sell_sums[seller][book_id] = sell_sums[seller].get(book_id, 0.0) - buy_cap
        if sell_hist is not None:
            _hadd2(sell_hist, seller, book_id, ts, -buy_cap)


def _finalize_ready(st, book_id, buy_sums, sell_sums, W, buy_hist, sell_hist, ts,
                    now_ns, flush_ns, force=False):
    """Finalize every pending fill whose forward window is complete (W prints arrived after it),
    stale (older than flush_ns of sim time, so a quiet book cannot hold capture hostage), or
    force-flushed. The window truncates only at genuine stream edges, never at batch edges."""
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
                           buy_hist, sell_hist, ts)
        pend.pop(0)
    new_base = max(base, (pend[0][0] if pend else n) - W)
    if new_base > base:
        del prices[:new_base - base]
        st["base"] = new_base


def accumulate_book_capture(buy_sums, sell_sums, book_id, trades, W, *,
                            buy_hist=None, sell_hist=None, ts=None,
                            mid_state=None, flush_ns=CAPTURE_FLUSH_NS):
    """Accumulate per-uid two-sided spread capture for ONE book's ordered trade batch into
    buy_sums / sell_sums ({uid: {book: cap}}), in place. Each `trade` is dict-like with keys
    p (price), q (quantity), s (side), Ma (maker uid), Ta (taker uid). side==0 => taker buys /
    maker sells; side==1 => maker buys / taker sells. buyer captures (mid-price)*q, seller
    (price-mid)*q, vs a non-lagging centered mid. Self-trades (Ma==Ta) earn nothing but their
    prints still shape the mid (first-line wash guard; cross-uid rings are netted by P11).

    mid_state=None (offline callers passing a whole run as one batch): the centered mid is computed
    over THIS batch, truncating at its edges. At live print density that truncation is the rule,
    not the exception (on a live board: median 4 prints per state update,
    90.9% of batches degrade to the plain batch mean, 12.8% of prints see a full window), so live
    callers pass mid_state ({book: st}) to carry the print window ACROSS batches: each fill is held
    (bounded: at most W pending fills and 2W+1 prices per book) until W forward prints arrive, then
    booked against its full centered window at the finalizing call's ts. A fill with no W forward
    prints inside flush_ns of sim time finalizes with a truncated forward side, so the lag is
    bounded and a quiet book still settles. Batch shape then cannot move capture: only the print
    stream itself can.

    When buy_hist/sell_hist ({uid:{book:{ts:incr}}}) + ts are given, increments are also recorded
    at ts so the sums can be windowed (pruned/shifted)."""
    if mid_state is None:
        prices = [float(t["p"]) for t in trades]
        mids = centered_mid(prices, W)
        for t, mid in zip(trades, mids):
            if t.get("Ma", -1) == t.get("Ta", -1):
                continue
            _attribute_capture(buy_sums, sell_sums, book_id, t, mid, buy_hist, sell_hist, ts)
        return
    st = mid_state.setdefault(book_id, {"prices": [], "pend": [], "base": 0, "n": 0})
    for t in trades:
        st["prices"].append(float(t["p"]))
        if t.get("Ma", -1) != t.get("Ta", -1):
            st["pend"].append((st["n"], ts, t))
        st["n"] += 1
    _finalize_ready(st, book_id, buy_sums, sell_sums, W, buy_hist, sell_hist, ts, ts, flush_ns)


def flush_capture_state(mid_state, buy_sums, sell_sums, W, *,
                        buy_hist=None, sell_hist=None, ts=None,
                        flush_ns=CAPTURE_FLUSH_NS, force=False):
    """Finalize stale pending fills on EVERY book (books with no new trades never reach
    accumulate_book_capture, so the live loop calls this each cycle; force=True drains everything,
    for offline end-of-run and the sim-boundary re-base)."""
    for book_id, st in mid_state.items():
        _finalize_ready(st, book_id, buy_sums, sell_sums, W, buy_hist, sell_hist, ts,
                        ts, flush_ns, force=force)


def balanced_reward_per_book(capture_buy_sums, capture_sell_sums, uids):
    """Two-sided capture summed PER BOOK: sum_b 2*min(buy_b, sell_b), clamped at 0.

    Two-sidedness must hold on each book separately: capture is a fill-level quantity, so summing
    the sides before taking the minimum does not measure it. Strictly tighter than balanced_reward,
    since min is subadditive and the clamp is on the total, so this can only lower a score.
    """
    out = {}
    for u in uids:
        cb = capture_buy_sums.get(u) or {}
        cs = capture_sell_sums.get(u) or {}
        tot = 0.0
        for b in set(cb) | set(cs):
            # The clamp belongs on the total, not here. Capture is (mid - p) * q and is genuinely
            # negative on 43% of per-book values, so clamping inside the loop would drop a miner's
            # loss-making books instead of counting them, breaking the subadditivity the docstring
            # relies on.
            tot += 2.0 * min(cb.get(b, 0.0), cs.get(b, 0.0))
        out[u] = max(0.0, tot)
    return out

