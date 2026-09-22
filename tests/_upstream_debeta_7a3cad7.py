# SPDX-License-Identifier: MIT
"""The validator's de-beta SKILL functions, VERBATIM from taos-im/sn-79 main commit 7a3cad7
("20260921 - 0.6.1 final rung", taos/im/validator/debeta.py; the file is byte-identical at 0234998).
The oracle the v6.2.11 own-alpha mirror is proven against.  Do not edit: copy again from upstream.
"""
import statistics
from bisect import bisect_left, insort
from collections import defaultdict, deque

SKILL_RANK_SCOPES = ("positives", "whole")


def _hadd2(hist, k1, k2, ts, val):
    d = hist.setdefault(k1, {}).setdefault(k2, {})
    d[ts] = d.get(ts, 0.0) + val


def _hadd1(hist, k, ts, val):
    d = hist.setdefault(k, {})
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


def prune_hist_1level(hist, running, threshold):
    """hist {k:{ts:val}}, running {k:val}. A key whose history empties is removed from both maps, for
    the reason given on prune_hist_2level.

    Args:
        hist: ``{k: {ts: val}}`` history, pruned in place.
        running: ``{k: val}`` running totals, reduced by the pruned mass.
        threshold: Timestamps strictly below this are dropped.
    """
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


def kappa_of_alpha(book_alphas):
    """DIRECTIONAL SKILL. book_alphas = per-book benchmark-relative (excess) PnL for one agent
    (each = book_total_pnl - mean_inventory*book_drift). Consistency across independent books
    (MAD-normalised downside-adjusted mean): a skilled forecaster is consistently positive; a
    drift-rider is positive only where drift helped (inconsistent) -> low. Needs >=4 books."""
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
    """Magnitude floor on directional skill.

    kappa measures consistency and is blind to magnitude, so a book only counts toward it once its
    |alpha| clears `floor`, and skill is assessed across several qualifying books rather than one.
    Consistency at negligible size is not skill. `floor` is scaled to real per-book traded notional and
    calibrated offline against the observed per-book alpha distribution."""
    q = [float(x) for x in book_alphas if abs(float(x)) >= floor]
    return kappa_of_alpha(q) if len(q) >= 4 else 0.0


def _rank01(vals):
    """Ties share their block's MINIMUM rank: equal raw values must map to equal ranks (uid order
    must never decide emissions; 88% of miners share making_raw==0.0 on a live board), and a
    zero-making mass earns zero relative making credit rather than a positional lottery."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    rank = [0.0] * len(vals)
    denom = max(len(vals) - 1, 1)
    pos = 0
    for k, i in enumerate(order):
        if k and vals[i] != vals[order[k - 1]]:
            pos = k
        rank[i] = pos / denom
    return rank


def _rank_positive_leg(values, scope, dial):
    if scope not in SKILL_RANK_SCOPES:
        raise ValueError(f"{dial} must be one of {SKILL_RANK_SCOPES}, got {scope!r}")
    if scope == "whole":
        return _rank01([max(0.0, float(v)) for v in values])
    idx = [i for i, v in enumerate(values) if v > 0]
    out = [0.0] * len(values)
    if len(idx) == 1:
        # A lone positive is the best there is: _rank01 of one value is 0, which would pay the only
        # maker or the only skilled trader nothing on that leg (seen in the warm-up gate).
        out[idx[0]] = 1.0
    elif idx:
        for i, r in zip(idx, _rank01([float(values[i]) for i in idx])):
            out[i] = r
    return out


def _uid(x):
    """Coerce a maker/taker id to int, mapping None (a pool/AMM side with no miner) to -1 so the >=0 miner
    guards skip it. Sim 't' events carry int ids (identity); exchange ET notices may carry None."""
    return -1 if x is None else int(x)


def accumulate_book_mtm(mtm, invsum, invn, inv, p_first, p_last, book_id, trades, *,
                        mtm_hist=None, invsum_hist=None, invn_hist=None,
                        drift=None, drift_hist=None, ts=None,
                        mark_state=None, mark_mode="last", mark_window=0):
    """Accumulate the drift-strip inputs for kappa-of-alpha over ONE book's ordered trade batch,
    IN PLACE and CARRIED ACROSS BATCHES (inv + p_last persist between publish intervals).
    Reconstructs, per miner, the MTM PnL path-integral (sum inv*dp), the inventory time-sum, and the
    price drift (sum dp), from the SAME fill stream as accumulate_book_capture. Each `trade` is dict-like
    with p (price), q (quantity), s (side), Ma (maker uid), Ta (taker uid): s==0 => taker buys / maker
    sells, s==1 => maker buys / taker sells. Only miner uids (>=0) are tracked. Faithful to the offline
    L3 reconstruction (build_debeta_report.process_l3): the alpha numerator is the MTM path integral,
    NOT realized PnL (realized double-subtracts drift for a holder).

    Drift: `drift` ({book: sum dp}) telescopes to p_last-p_first over the window, so it REPLACES the
    p_first/p_last extent for the windowed drift-strip (book_alphas_from_drift). p_first/p_last are still
    maintained for the offline full-run finalizer (book_alphas_from_mtm). When the *_hist ({...:{ts}}) +
    ts are given, per-batch increments are recorded at ts for windowing. A boundary re-base of p_last to

    Args:
        mtm: ``{uid: {book: pnl}}`` MTM path-integral, accumulated in place.
        invsum: ``{uid: {book: sum}}`` inventory time-sums, accumulated in place.
        invn: ``{book: n}`` trade counts, accumulated in place.
        inv: ``{uid: {book: inventory}}`` current inventories, carried across batches.
        p_first: ``{book: price}`` first seen price per book, carried across batches.
        p_last: ``{book: price}`` last seen price per book, carried across batches.
        book_id: The book this batch belongs to.
        trades: Ordered trade batch, each dict-like with ``p``, ``q``, ``s``, ``Ma``, ``Ta``.
        mark_state: ``{book: state}`` rolling settlement-mark state, carried across batches (M1).
        mark_mode: ``"last"`` (default, byte-identical to the pre-M1 path), ``"vwap"`` (rolling
            volume-weighted mean of the last mark_window prints) or ``"median"`` (rolling window
            median, robust to both price and volume outliers).
        mark_window: Window length in prints for vwap/median marking; <=0 disables.
    None means the first new-sim trade emits no dp, so the boundary jump never enters drift.

    M1 settlement-style marking (mark_mode != "last"): the marked series is a rolling reference
    over the last mark_window prints, so ONE print at an extreme price cannot revalue a holder's
    whole position - the same reason real venues settle on a window, not the last trade. "vwap"
    is the naive settlement analogue but is itself movable by a single large wash print (the
    unload leg drags the volume-weighted mean toward the manufactured price - measured in
    mark_impl_check); "median" needs a sustained majority of window prints to move, fusing the
    window mark (M1) with erroneous-print exclusion (M2). The drift accumulator and
    p_first/p_last then track the SAME marked series, so the drift-strip finalizers stay
    consistent (alpha = mtm - mean_inv * drift telescopes over the marked series either way).
    mark_state is NOT persisted: after a restart the window re-warms and the first trade emits
    no dp - it can miss a marking move, never invent one."""
    binv = inv[book_id]  # {uid: current signed inventory in this book}, carried across batches
    use_mark = mark_mode != "last" and mark_window > 0 and mark_state is not None
    if use_mark:
        st = mark_state.get(book_id)
        if st is None:
            st = mark_state[book_id] = {"pv": deque(), "vv": deque(), "pvs": 0.0, "vvs": 0.0,
                                        "pq": deque(), "sorted": [], "prev": None}
        prev = st["prev"]
    else:
        prev = p_last.get(book_id)
    for t in trades:
        p = float(t["p"])
        q = float(t["q"])
        if use_mark:
            if mark_mode == "vwap":
                st["pv"].append(p * q)
                st["vv"].append(q)
                st["pvs"] += p * q
                st["vvs"] += q
                if len(st["pv"]) > mark_window:
                    st["pvs"] -= st["pv"].popleft()
                    st["vvs"] -= st["vv"].popleft()
                p = st["pvs"] / st["vvs"] if st["vvs"] > 0 else p
            else:  # median
                sl = st["sorted"]
                insort(sl, p)
                st["pq"].append(p)
                if len(st["pq"]) > mark_window:
                    old = st["pq"].popleft()
                    del sl[bisect_left(sl, old)]
                m = len(sl) // 2
                p = sl[m] if len(sl) % 2 else 0.5 * (sl[m - 1] + sl[m])
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
        if buyer >= 0:
            binv[buyer] = binv.get(buyer, 0.0) + q
        if seller >= 0:
            binv[seller] = binv.get(seller, 0.0) - q
        for uid, iv in binv.items():
            invsum[uid][book_id] = invsum[uid].get(book_id, 0.0) + iv
            if invsum_hist is not None:
                _hadd2(invsum_hist, uid, book_id, ts, iv)
        invn[book_id] = invn.get(book_id, 0) + 1
        if invn_hist is not None:
            _hadd1(invn_hist, book_id, ts, 1)
        if p_first.get(book_id) is None:
            p_first[book_id] = p
        prev = p
    p_last[book_id] = prev
    if use_mark:
        st["prev"] = prev


def book_alphas_by_book(mtm, invsum, invn, drift):
    """Same arithmetic as book_alphas_from_drift, but BOOK IDENTITY IS PRESERVED: {uid: {book: alpha}}.

    The list form loses it. Each miner's list is built from the set of books THAT MINER traded, so
    position i is a different book for different miners (measured: 66 distinct list lengths across
    256 miners on one window). Kappa does not care, being order-blind. Anything CROSS-SECTIONAL does:
    comparing miners at the same list index silently compares different books, which invalidates any
    per-book aggregate computed that way."""
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


def traded_book_alphas(alphas_by_book, capture_buy_sums, capture_sell_sums):
    """Keep, per uid, the books on which the uid FILLED inside the window: {uid: {book: alpha}}.

    The mark-to-market and inventory accumulators run for every miner holding a position on a book,
    on every trade in that book, so a miner that merely holds a static position through the window
    carries an alpha entry for it, and by the defining invariant that alpha is exactly zero. On a
    long-running validator those held-not-traded pairs come to be half of all pairs, so a floor taken
    over every pair collapses to a rounding residue and kappa counts books the miner never traded. The
    capture maps hold exactly the (uid, book) pairs with fills inside the window (they are pruned on
    the same clock), so they define the pool for both the floor and the skill leg.

    Args:
        alphas_by_book: ``{uid: {book: alpha}}`` from book_alphas_by_book.
        capture_buy_sums: ``{uid: {book: capture}}`` buyer-side capture, windowed.
        capture_sell_sums: ``{uid: {book: capture}}`` seller-side capture, windowed.

    Returns:
        dict: ``{uid: {book: alpha}}`` restricted to books with fills; a uid with none keeps an empty map.
    """
    out = {}
    for uid, by_book in alphas_by_book.items():
        traded = set((capture_buy_sums.get(uid) or {})) | set((capture_sell_sums.get(uid) or {}))
        out[uid] = {b: a for b, a in by_book.items() if b in traded}
    return out


def median_abs_floor(book_alphas_by_uid, scale=0.5):
    """E5 magnitude floor for kappa_floored: scale * median(|alpha|) over the per-book alphas passed
    in. Kappa is magnitude-blind, so a tiny-consistent spammer ranks high without a floor. The caller
    passes the pool of (uid, book) pairs with fills inside the window (traded_book_alphas): a held but
    untraded book has an alpha of exactly zero by the invariant and would drag the median to nothing."""
    mags = [abs(a) for al in book_alphas_by_uid.values() for a in al if a is not None]
    if not mags:
        return 0.0
    return scale * statistics.median(mags)
