# SPDX-License-Identifier: MIT
"""v6.32 S14: make only where the realized spread covers the weighted maker fee -- research_v632_fee_making.

Why (scratchpad k90 / k91, 10-16 JST; SN79 0.6.4 = upstream ``test`` 19f9fbd "0.6.4 fee-aware market making", live on
mainnet Friday 16 Oct 14:00 JST, applied inside the running simulation):

* 0.6.4's ``debeta._attribute_capture(..., fee_weight)`` books every MAKER fill net of ``fee_weight x Mf`` (Mf the maker
  fee the fill paid; a rebate is negative and adds) in BOTH the captured and the realized sums
  (``_finalize_ready`` and ``_finalize_realized_ready`` both pass it); the taker side is unchanged.
  ``--scoring.debeta.making_fee_weight`` defaults to 0.005 ("Stepped: 0.005 first, raised at later boundaries") and is
  published in neither the state nor the report: the agent takes it from a launcher param.
* The making pool pays making62 x q, q = clamp(sum R / sum C, 0, 1) over ALL of a uid's fills and books.  With the
  netting a fill helps the score iff its realized spread per notional at the validator's horizon exceeds
  fee_weight x the book's maker fee RATE.  On the live tape (sim 9000-16347, class 0) our maker fee is 31.7 bps of
  notional against a realized spread of 0.14-0.15 bps: W x fee = 0.16 bps per notional, so q -> 0.000 on all nine
  UIDs (making63 320 -> 0) while the leaders keep 70-80%.  By the fill's own fee rate our NET realized R/N is rebate
  +0.152, [0, 10) +0.091, [10, 25) +0.045, [25, 50) -0.026, >= 50 -0.173 bps.

The rule (the announcement's own: "quotes whose spread covers the weighted fee keep their credit"): the agent measures,
on its own maker fills and with the validator's own arithmetic (vendored below), the realized spread per notional it
keeps at the validator's horizon over the validator's making window -- ONE estimate pooling every maker fill of ours,
the touch quote's and the deep layer's alike (the k91 replay's pooled mode, fg=2, chosen 10-16 02:53 JST) -- and on each
book whose current maker fee rate makes ``fee_gated`` true (the estimate does not exceed fee_weight x that rate) it
withholds the ADDING side of the deep layer and of the v6.17 touch quote.  An order whose fill would reduce |inventory|
is never withheld (v6.23 S1's ``adding`` precedent: an order that unwinds a position stays); nor is an A1.9 exit-layer
order or any cancel.  Active only on a state from a validator at FEE_SPEC or later (``gate_active``).  The estimate
counts our fills only while the gate is active and starts empty at each inactive -> active transition
(``RealizedTracker.set_active``; coordinator 10-16 03:40 JST): fills measured before 0.6.4 quoted off the stale page
fundamental, and from 14:00 the state's own fundamental arrives with version 64.  So after an activation nothing is
gated until MIN_FILLS fills of the new period are counted; the print windows and the pending fills run from the start,
so release timing never depends on the gate.

Units (k91 open check): the account's ``maker_fee_rate`` (``Fees.m``) is a FRACTION on the wire -- the simulator packs
``f.m = feePolicy->getRates(book, agent).maker`` (ValidatorRequest.hpp) and books ``Mf = getRates(book,
restingAgent).maker x volume x price`` (DynamicFeePolicy::calculateFees): the same rate.  Our live maker prints show
Mf / (p q) = 34.8 bps mean (UID 78, 1013 prints), i.e. a rate ~0.0035; the frozen base turns it into bps x 10,000.  It
is compared as it comes.  The maker side of a print: s == 0 is buy-initiated, the maker SOLD; s == 1 the maker BOUGHT
(debeta ``_attribute_capture``: buyer, seller = (Ta, Ma) if s == 0 else (Ma, Ta)) -- verified on 1013 of our own fills
against the agent's FILL rows (470 / 470 sells, 543 / 543 buys).

Vendored from taos 0.6.4 ``taos/im/validator/debeta.py`` (MIT, Rayleigh Research; upstream test 19f9fbd), byte for byte
with their docstrings: ``_background_sided``, ``_push_floor_reference``, ``_clears``, ``_floored_mean_at``,
``_floored_mid_at``, ``_forward_complete``, ``_mark_weight``, ``_weighted_mark``, ``_mark_near``, ``_capped_mark``,
``_print_time`` and their constants.  ``RealizedTracker`` keeps, per book, the validator's own print-window shape (the
``st`` dict ``accumulate_book_capture`` builds: prices, times, weights, sizes, floors fixed on arrival, background flags,
base / n) for OUR maker fills only, with ``market=False`` (``--scoring.debeta.capture_market_votes`` default 0), and
releases each fill exactly as ``_finalize_realized_ready`` does (its capture side settled AND the stream holding a print
at/after ts + HORIZON followed by HALF_WINDOW more, or HORIZON + CAPTURE_FLUSH of sim time later: marked against the
latest window), booking the gross realized spread ``(capped - p) q`` for a maker buy, ``(p - capped) q`` for a maker
sell, with its notional ``p q``, into the window.  The gate compares sum R / sum N (a fraction) with W x the book's rate:
R - W x rate x N is exactly the validator's netted realized sum (per fill identical to 0.6.4's on synthetic streams).

STRUCTURAL: every constant below is the validator's own (cited), except the two marked as design choices (a
statistical minimum, a memory bound), neither sized from an observed metric.
"""
from __future__ import annotations

import bisect
import math
from collections import deque
from typing import Any, Iterable

from research_v633_deep_layer import DEEP_LEVELS, SIDE_BUY, SIDE_SELL
from research_v641_pace import adds as _v641_adds

V632_FEE_MAKING_VERSION = "fee_making_v6_32"

# ---- the validator's own numbers (taos 0.6.4, upstream test 19f9fbd) --------------------------------------------------
FEE_WEIGHT_DEFAULT = 0.005         # config/__init__.py: --scoring.debeta.making_fee_weight default ("0.005 first")
FEE_SPEC = 64                      # taos/__init__.py: 0.6.4's __spec_version__ = 1000 x 0 + 10 x 6 + 4 (0.6.3 sends 63)
HORIZON_NS = 20_000_000_000        # debeta.py REALIZED_HORIZON_NS_DEFAULT = --scoring.debeta.making_horizon_s 20.0
HALF_WINDOW = 15                   # trade.py CAPTURE_W = 15 = --scoring.debeta.centered_window default
CAPTURE_REACH_WINDOWS = 8          # debeta.py: a capture window reaches 8 x W prints a side past dust
CAPTURE_MARKET_MIN = 5             # debeta.py: with market votes on (off here, as the validator's default)
MARK_REACH_WINDOWS = 4             # debeta.py: a mark reaches 4 x W prints a side for a background-sided print
FLOOR_REFERENCE_PRINTS = 256       # debeta.py: the trailing voters a book's dust floor is read from
DUST_FLOOR_FRAC = 0.1              # debeta.py: a print below 0.1 x the reference median takes no part in the mid
CAPTURE_FLUSH_NS = 60_000_000_000  # debeta.py: a pending fill is released stale HORIZON + 60 sim-s after it
WINDOW_NS = 10_800 * 10**9         # trade.py prunes the realized sums at timestamp - scoring.kappa.lookback (10800e9)

# ---- design choices (a statistical minimum / a memory bound; neither sized from an observed metric) --------------------
MIN_FILLS = 256                    # fills the pooled window needs before its estimate gates anything
RING_MAX = max(4 * FLOOR_REFERENCE_PRINTS, 2048)   # prints a book's window may hold (the reaches need 8W + the horizon's)

CANCEL_FEE_GATE = "V632_FEE_GATE"  # a resting adding order on a book whose fills would not cover W x the maker fee

__all__ = ["CANCEL_FEE_GATE", "CAPTURE_FLUSH_NS", "FEE_SPEC", "FEE_WEIGHT_DEFAULT", "HALF_WINDOW", "HORIZON_NS",
           "MIN_FILLS", "RING_MAX", "RealizedTracker", "V632_FEE_MAKING_VERSION", "WINDOW_NS", "adds", "deep_pairs",
           "fee_gated", "gate_active", "maker_fee_rate", "min_sizes", "parse_fee_weight"]


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


# ---- vendored from taos 0.6.4 taos/im/validator/debeta.py (19f9fbd), byte for byte -------------------------------------

def _background_sided(t):
    ma, ta = t.get("Ma", -1), t.get("Ta", -1)
    return (ma is None or ma < 0) or (ta is None or ta < 0)


def _push_floor_reference(sorted_sizes, fifo, size, bg, min_size, n=FLOOR_REFERENCE_PRINTS):
    """Admit a print to a book's floor reference, the trailing n background-sided prints at or above the minimum
    order size, kept as two plain lists (sorted, and in arrival order for eviction) so the state snapshots and
    persists as data; return the floor as of this print. Fixed per print on arrival, so the batch and the live path
    read the same floor for the same print whatever the batching."""
    if bg and size > 0.0 and size >= min_size:
        bisect.insort(sorted_sizes, size)
        fifo.append(size)
        if len(fifo) > n:
            old = fifo.pop(0)
            del sorted_sizes[bisect.bisect_left(sorted_sizes, old)]
    return DUST_FLOOR_FRAC * sorted_sizes[len(sorted_sizes) // 2] if sorted_sizes else 0.0


def _clears(size, floor):
    return size > 0.0 and size >= floor


def _floored_mean_at(prices, sizes, floors, bgs, i, W, reach, market=False):
    """The capture mid of print i, the unweighted mean of its voters. The span is print i and the W nearest prints on
    each side that clear its dust floor, looking at most `reach` prints a side, so prints below the floor neither vote
    nor take a voter's place. With `market` (scoring.debeta.capture_market_votes) only the background-sided prints of
    that span vote, and a side holding fewer than CAPTURE_MARKET_MIN of them reaches on, within the reach, until it
    does: a print between two miners then moves no one's mid at any size. None when nothing votes."""
    n = len(prices)
    floor = floors[i]

    def votes(j):
        return _clears(sizes[j], floor) and (not market or bgs[j])
    tot, cnt = (prices[i], 1) if votes(i) else (0.0, 0)
    need = CAPTURE_MARKET_MIN if market else 0
    for step in (-1, 1):
        span = mkt = 0
        j, stop = i + step, (max(-1, i - reach - 1) if step < 0 else min(n, i + reach + 1))
        while j != stop and (span < W or mkt < need):
            if _clears(sizes[j], floor):
                span += 1
                if not market or bgs[j]:
                    mkt += 1
                    tot += prices[j]
                    cnt += 1
            j += step
    return tot / cnt if cnt else None


def _floored_mid_at(st, idx, W):
    """The capture mid of the live window's print idx, over the prints the window still holds."""
    return _floored_mean_at(st["prices"], st["sizes"], st["floors"], st["bgs"], idx - st["base"], W,
                            CAPTURE_REACH_WINDOWS * W, bool(st.get("market")))


def _forward_complete(st, idx, W):
    """Whether print idx's forward side is settled: W later prints clear its floor (and, with market votes on,
    CAPTURE_MARKET_MIN of them are background-sided), or its whole reach has arrived."""
    reach = CAPTURE_REACH_WINDOWS * W
    if st["n"] - 1 >= idx + reach:
        return True
    sizes, floors, bgs, fi = st["sizes"], st["floors"], st["bgs"], idx - st["base"]
    need = CAPTURE_MARKET_MIN if st.get("market") else 0
    floor, span, mkt = floors[fi], 0, 0
    for j in range(fi + 1, len(sizes)):
        if _clears(sizes[j], floor):
            span += 1
            mkt += bool(bgs[j])
            if span >= W and mkt >= need:
                return True
    return False


def _mark_weight(t):
    """A print's weight in the realized mark: its size when the background market is on at least one side, else 0."""
    ma, ta = t.get("Ma", -1), t.get("Ta", -1)
    bg = (ma is None or ma < 0) or (ta is None or ta < 0)
    return float(t.get("q") or 0.0) if bg else 0.0


def _weighted_mark(prices, weights):
    """Size-weighted average over the background-sided prints of a window; None when the window holds none.

    There is no plain-average fallback: a window without a background-sided print sets no mark."""
    tw = sum(weights)
    if tw > 0:
        return sum(p * w for p, w in zip(prices, weights)) / tw
    return None


def _mark_near(prices, weights, i, W, reach):
    """The mark at print i: the weighted mark of the centered window, widened outward (doubling) to the nearest
    background-sided prints up to `reach` prints each side; None when none is in reach."""
    n = len(prices)
    h = W
    while True:
        lo, hi = max(0, i - h), min(n, i + h + 1)
        m = _weighted_mark(prices[lo:hi], weights[lo:hi])
        if m is not None or h >= reach or (lo == 0 and hi == n):
            return m
        h = min(reach, h * 2)


def _capped_mark(t, mark, cap_mid):
    """The mark that books the maker's realized spread on this fill clamped to plus or minus its captured spread, so
    one fill moves a uid's markout quality by at most its own weight (the quality sums over fills before the ratio).
    A fill with no mark in reach books nothing: its mark is its own price."""
    p, q = float(t["p"]), float(t["q"])
    if mark is None or q <= 0.0:
        return p
    cap = abs((float(cap_mid) - p) * q)
    realized_buy = (float(mark) - p) * q
    if abs(realized_buy) <= cap:
        return float(mark)
    return p + (cap if realized_buy > 0 else -cap) / q


def _print_time(t, ts):
    """A print's own simulation time: the wire event's `t`, an offline record's `ts`, else the batch stamp. The
    horizon is measured on this clock; `ts` as passed by the caller is the SAMPLED timestamp that keys the windowed
    histories, and on it every print of a sampling bucket reads alike, so a fill could only mature at the next bucket
    (found on the 0.6.3 testnet, 6 October 2026)."""
    v = t.get("t")
    if v is None:
        v = t.get("ts")
    if v is None:
        return int(ts) if ts is not None else -1
    return int(v)


# ---- the gate ---------------------------------------------------------------------------------------------------------

def parse_fee_weight(value: Any) -> float:
    """research_v632_fee_weight: a finite weight >= 0 (0: the gate is off); anything else is FEE_WEIGHT_DEFAULT."""
    w = _finite(value)
    return w if w is not None and w >= 0.0 else FEE_WEIGHT_DEFAULT


def gate_active(switch: Any, spec: Any, fee_weight: Any) -> bool:
    """Does the fee gate act on this state?  The switch on, a state from a validator at FEE_SPEC (0.6.4) or later, and a
    positive fee weight."""
    s, w = _finite(spec), _finite(fee_weight)
    return bool(switch) and s is not None and s >= FEE_SPEC and w is not None and w > 0.0


def fee_gated(estimate: Any, maker_fee_rate: Any, fee_weight: Any) -> bool:
    """True iff the estimate (our realized spread per notional, a fraction) is known and does not exceed fee_weight x
    the book's maker fee rate (a fraction): our fills there would book a non-positive netted realized spread.  A rebate
    book (rate < 0) is gated only for an estimate at or below that negative bar; an unknown estimate, rate or weight
    gates nothing."""
    e, r, w = _finite(estimate), _finite(maker_fee_rate), _finite(fee_weight)
    if e is None or r is None or w is None:
        return False
    return e <= w * r


def adds(side: Any, inventory: Any) -> bool:
    """Would a fill on ``side`` not reduce |inventory|?  v6.23 S1's rule (research_v641_pace.adds): a buy adds at
    inventory >= 0, a sell at inventory <= 0 -- a flat book adds on both sides; an unreadable inventory is flat.  Any
    other side: False (never gated)."""
    if side not in (SIDE_BUY, SIDE_SELL):
        return False
    inv = _finite(inventory)
    return bool(_v641_adds(side, 0.0 if inv is None else inv))


def deep_pairs(sides: Iterable[str]) -> frozenset:
    """The deep layer's (side, level) pairs that hold no order: every level of each side (v6.23 S1's ``deep`` shape)."""
    return frozenset((s, k) for s in sides if s in (SIDE_BUY, SIDE_SELL) for k in range(DEEP_LEVELS))


def maker_fee_rate(account: Any) -> float | None:
    """The account's current maker fee rate on its book (``fees.maker_fee_rate``; a fraction on the wire), or None
    when the account, its fees or the rate is missing or not a finite number."""
    try:
        fees = getattr(account, "fees", None) if account is not None else None
        rate = getattr(fees, "maker_fee_rate", None) if fees is not None else None
    except Exception:
        return None
    return _finite(rate)


def min_sizes(config: Any) -> tuple[float, dict[int, float]]:
    """(the top-level minimum order size, {book: its asset class's minimum}) -- the dust floor's voting bar the
    validator reads per book (trade.py ``_min_order_size``: the book's class configuration, 0.0 when it cannot say).
    A configuration without classes, or one that cannot be read: every book the top-level value."""
    def get(obj, name):
        if obj is None:
            return None
        return obj.get(name) if isinstance(obj, dict) else getattr(obj, name, None)
    top = _finite(get(config, "min_order_size"))
    top = top if top is not None and top >= 0.0 else 0.0
    out: dict[int, float] = {}
    try:
        classes = get(config, "classes")
        if classes and not isinstance(classes, (str, bytes, dict)):
            for cls in classes:
                m = _finite(get(get(cls, "config"), "min_order_size"))
                if m is None or m < 0.0:
                    continue
                for b in get(cls, "books") or ():
                    out[int(b)] = m
    except Exception:
        out = {}
    return top, out


# ---- the tracker ------------------------------------------------------------------------------------------------------

class _Window:
    """Our released fills over the making window: (fill ts, realized, notional), oldest first, with running sums;
    entries whose fill ts is before now - WINDOW_NS leave on expiry."""

    __slots__ = ("entries", "realized", "notional")

    def __init__(self) -> None:
        self.entries: deque = deque()
        self.realized = 0.0
        self.notional = 0.0

    def add(self, ts: int, realized: float, notional: float) -> None:
        self.entries.append((int(ts), float(realized), float(notional)))
        self.realized += float(realized)
        self.notional += float(notional)

    def expire(self, cutoff: int) -> int:
        n = 0
        entries = self.entries
        while entries and entries[0][0] < cutoff:
            _ts, r, q = entries.popleft()
            self.realized -= r
            self.notional -= q
            n += 1
        if not entries:
            self.realized = self.notional = 0.0
        return n

    def shift(self, delta: int) -> None:
        self.entries = deque((ts + int(delta), r, q) for ts, r, q in self.entries)

    def estimate(self, min_fills: int) -> float | None:
        if len(self.entries) < int(min_fills) or not self.notional > 0.0:
            return None
        return self.realized / self.notional


class RealizedTracker:
    """Our maker fills' realized spread at the validator's horizon, pooled over every fill of ours, over the making
    window.  Pure: no I/O, no clock of its own (every time is the caller's sim ns).  Each request: ``begin(state ts)``
    (a clock that ran back flushes and starts over), ``set_active(the gate's activity, state ts)``, then ``observe``
    each book's trade prints of the state in arrival order, then ``release(state ts)``.

    The window counts our fills ONLY while the gate is active, and starts EMPTY at each inactive -> active transition
    (coordinator 10-16 03:40 JST: fills measured before 0.6.4 quoted off the stale page fundamental; at 14:00 the state's
    fundamental arrives with version 64 and the realized quality changes -- the k94 replay's R/N about doubles -- so a
    10,800-sim-s window carrying pre-switch fills would hold the estimate low for ~15 h and over-gate; the replay of the
    post-switch configuration starts its window empty).  Each queued fill carries the active period its print arrived in
    (None while inactive) and is counted at release only in that same period while still active; the print windows and
    the pending queue run regardless, so release timing never depends on the gate.  MIN_FILLS applies to the period's
    own count.  Never raises on malformed input: it is skipped and counted."""

    def __init__(self, *, horizon_ns: int = HORIZON_NS, half_window: int = HALF_WINDOW,
                 flush_ns: int = CAPTURE_FLUSH_NS, window_ns: int = WINDOW_NS, min_fills: int = MIN_FILLS,
                 ring_max: int = RING_MAX) -> None:
        self.horizon_ns = int(horizon_ns)
        self.W = int(half_window)
        self.flush_ns = int(flush_ns)
        self.window_ns = int(window_ns)
        self.min_fills = int(min_fills)
        self.ring_max = max(int(ring_max), 2 * CAPTURE_REACH_WINDOWS * int(half_window) + 1)
        self.books: dict[int, dict] = {}
        self.window = _Window()
        self.last_now: int | None = None
        self.active = False                     # the gate's activity as of this request (set_active)
        self.period = 0                         # the current active period's number (0: never active)
        self.window_start_ns: int | None = None   # the state time the current period's window started, empty
        self.counts = {"prints": 0, "own_fills": 0, "self_trades": 0, "malformed": 0, "released": 0, "settled": 0,
                       "stale": 0, "forced": 0, "evicted": 0, "expired": 0, "resets": 0, "counted": 0,
                       "dropped_inactive": 0, "activations": 0, "deactivations": 0}

    # -- the stream ----------------------------------------------------------------------------------------------------

    def _book(self, book_id: int) -> dict:
        st = self.books.get(book_id)
        if st is None:
            # the validator's own window shape (debeta.accumulate_book_capture's ``st``), our fills' queue in ``rpend``
            st = {"prices": [], "times": [], "wts": [], "sizes": [], "floors": [], "bgs": [], "base": 0, "n": 0,
                  "market": False, "ref_sorted": [], "ref_fifo": [], "rpend": [], "min_size": 0.0, "seen_n": -1,
                  "due": None}
            self.books[book_id] = st
        return st

    def begin(self, now_ns: Any) -> bool:
        """At each request before its prints: a state clock that ran back (a new simulation, a checkpoint rewind)
        releases every pending fill against the prints held -- the validator's forced flush at a sim boundary --
        starts every book's stream over and shifts the window so its entries keep their age (the validator shifts its
        sums).  True when it did."""
        now = _finite(now_ns)
        if now is None:
            return False
        now = int(now)
        last = self.last_now
        if last is not None and now < last:
            self.release(last, force=True)
            self.window.shift(now - last)
            self.books = {}
            self.counts["resets"] += 1
            self.last_now = now
            return True
        self.last_now = now
        return False

    def set_active(self, active: Any, now_ns: Any) -> bool:
        """The gate's activity for this request, set before its prints are observed.  An inactive -> active transition
        starts a new period whose window starts EMPTY at ``now_ns`` (the state's time); active -> inactive stops the
        counting (the window is left as it is and cleared at the next activation).  True on a transition to active."""
        on = bool(active)
        if on and not self.active:
            self.window = _Window()
            self.period += 1
            now = _finite(now_ns)
            self.window_start_ns = int(now) if now is not None else None
            self.active = True
            self.counts["activations"] += 1
            return True
        if not on and self.active:
            self.counts["deactivations"] += 1
        self.active = on
        return False

    def observe(self, book_id: Any, prints: Iterable[Any], own_uid: Any, min_size: Any = 0.0, ts: Any = None) -> int:
        """Appends one book's trade prints of this state (arrival order) to its window -- price, print time, mark
        weight, size, background flag and the dust floor fixed on arrival -- and queues each of OUR maker fills (maker
        uid == ``own_uid``, not a self-trade) with its side (s == 1: we bought).  ``prints``: wire dicts (the book's raw
        't' events) or objects with the same field names.  ``min_size``: the book's minimum order size (the floor
        reference's voting bar).  ``ts``: the state's time (a print without its own).  Returns our fills queued."""
        try:
            b = int(book_id)
        except (TypeError, ValueError, OverflowError):
            self.counts["malformed"] += 1
            return 0
        st = self._book(b)
        ms = _finite(min_size)
        st["min_size"] = ms if ms is not None and ms >= 0.0 else 0.0
        try:
            uid = None if own_uid is None or isinstance(own_uid, bool) else int(own_uid)
        except (TypeError, ValueError, OverflowError):
            uid = None
        prices, times, wts, sizes, floors, bgs = st["prices"], st["times"], st["wts"], st["sizes"], st["floors"], \
            st["bgs"]
        ref_sorted, ref_fifo, rpend, mins = st["ref_sorted"], st["ref_fifo"], st["rpend"], st["min_size"]
        mine = 0
        for e in prints or ():
            try:
                if not isinstance(e, dict):
                    e = {k: getattr(e, k, None) for k in ("p", "q", "s", "t", "Ma", "Ta")}
                    for k in ("Ma", "Ta"):
                        if e[k] is None:
                            e[k] = -1
                p = float(e["p"])
                if not math.isfinite(p) or p <= 0.0:
                    raise ValueError("price")
                q = float(e.get("q") or 0.0)
                if not math.isfinite(q):
                    raise ValueError("size")
                pt = _print_time(e, ts)
                bg = _background_sided(e)
                w = _mark_weight(e)
            except Exception:
                self.counts["malformed"] += 1
                continue
            prices.append(p)
            times.append(pt)
            wts.append(w)
            sizes.append(q)
            bgs.append(bg)
            floors.append(_push_floor_reference(ref_sorted, ref_fifo, q, bg, mins))
            ma, ta = e.get("Ma", -1), e.get("Ta", -1)
            if uid is not None and ma == uid:
                if ma == ta:
                    self.counts["self_trades"] += 1            # the validator books nothing on a self-trade
                elif (ma or -1) >= 0 or (ta or -1) >= 0:       # debeta: only fills with a miner on a side are queued
                    s = e.get("s")
                    if s in (0, 1) and not isinstance(s, bool) and q > 0.0:
                        rpend.append((st["n"], pt, {"p": p, "q": q}, s == 1, self.period if self.active else None))
                        mine += 1
                        self.counts["own_fills"] += 1
                    else:
                        self.counts["malformed"] += 1
            st["n"] += 1
            self.counts["prints"] += 1
        if st["n"] - st["base"] > self.ring_max:
            cut = st["n"] - self.ring_max
            while rpend and rpend[0][0] < cut:                 # its print leaves the window: released now, stale
                try:
                    self._release_head(st, None, forced=True)
                except Exception:
                    self.counts["malformed"] += 1
                    rpend.pop(0)
                self.counts["evicted"] += 1
            self._trim(st, cut)
        elif not rpend:
            self._trim(st)                                     # no fill of ours waits here: the last reach's prints
        return mine

    def _trim(self, st: dict, floor_base: int = 0) -> None:
        """The validator's _trim_window on our queue: drop the prints no pending fill of ours can still need --
        everything before the oldest one's backward reach (with none, the last reach's prints stay for the next fill)
        -- and, past ``ring_max``, the oldest prints."""
        rpend, base, n = st["rpend"], st["base"], st["n"]
        oldest = rpend[0][0] if rpend else n
        new_base = max(base, oldest - CAPTURE_REACH_WINDOWS * self.W, int(floor_base))
        if new_base > base:
            k = new_base - base
            for key in ("prices", "times", "wts", "sizes", "floors", "bgs"):
                del st[key][:k]
            st["base"] = new_base

    # -- release -------------------------------------------------------------------------------------------------------

    def _release_head(self, st: dict, now: int | None, *, forced: bool = False) -> bool:
        """debeta._finalize_realized_ready for the head of our queue: False when it is not ready."""
        rpend, prices, times = st["rpend"], st["prices"], st["times"]
        base, n, W = st["base"], st["n"], self.W
        idx, arrive_ns, t, bought, period = rpend[0]
        j = None
        if arrive_ns is not None and arrive_ns >= 0:
            k = bisect.bisect_left(times, arrive_ns + self.horizon_ns, max(0, idx - base))
            if k < len(times):
                j = k + base
        settled = j is not None and n - 1 >= j + W and _forward_complete(st, idx, W)
        stale = now is not None and arrive_ns is not None and now - arrive_ns >= self.horizon_ns + self.flush_ns
        if not (forced or settled or stale):
            return False
        if j is None:
            j = n - 1
        mark = _mark_near(prices, st["wts"], j - base, W, MARK_REACH_WINDOWS * W)
        cap_mid = _floored_mid_at(st, max(base, idx), W)
        if cap_mid is None:
            cap_mid = float(t["p"])
        capped = _capped_mark(t, mark, cap_mid)
        p, q = float(t["p"]), float(t["q"])
        realized = (capped - p) * q if bought else (p - capped) * q
        if self.active and period is not None and period == self.period:
            self.window.add(arrive_ns, realized, p * q)      # its print arrived in this active period
            self.counts["counted"] += 1
        else:
            self.counts["dropped_inactive"] += 1               # it arrived while inactive, or in an earlier period
        rpend.pop(0)
        self.counts["released"] += 1
        self.counts["settled" if settled else "stale" if stale else "forced"] += 1
        return True

    def release(self, now_ns: Any, *, force: bool = False) -> int:
        """Releases, book by book in queue order, every pending fill of ours that is ready (settled at the horizon, or
        stale: HORIZON + CAPTURE_FLUSH of sim time old; ``force``: all), then expires the window at now - WINDOW_NS.  A
        book with no new print since its head was found not ready, and before that head's stale time, is not looked at
        again.  Returns the fills released."""
        now = _finite(now_ns)
        if now is None:
            return 0
        now = int(now)
        released = 0
        for st in self.books.values():
            rpend = st["rpend"]
            if not rpend:
                continue
            if not force and st["seen_n"] == st["n"] and st["due"] is not None and now < st["due"]:
                continue
            while rpend:
                try:
                    done = self._release_head(st, now, forced=force)
                except Exception:
                    self.counts["malformed"] += 1
                    rpend.pop(0)                                 # an unreadable fill never blocks the queue
                    continue
                if not done:
                    head = rpend[0]
                    st["seen_n"] = st["n"]
                    st["due"] = (head[1] + self.horizon_ns + self.flush_ns) if head[1] is not None else None
                    break
                released += 1
            self._trim(st)
        self.counts["expired"] += self.window.expire(now - self.window_ns)
        return released

    # -- reads ---------------------------------------------------------------------------------------------------------

    def estimate(self) -> float | None:
        """Our realized spread per notional over the window (a fraction: 2e-5 = 0.2 bps), pooled over every maker fill of
        ours counted in the current active period, or None while the window holds fewer than MIN_FILLS fills."""
        return self.window.estimate(self.min_fills)

    def window_fills(self) -> int:
        return len(self.window.entries)

    def pending(self) -> int:
        return sum(len(st["rpend"]) for st in self.books.values())

    def snapshot(self) -> dict[str, Any]:
        e = self.estimate()
        out: dict[str, Any] = {"estimate_bps": None if e is None else round(e * 1e4, 6),
                               "window_fills": len(self.window.entries),
                               "window_notional": round(self.window.notional, 4),
                               "window_start_ns": self.window_start_ns, "tracker_active": int(self.active),
                               "period": self.period, "pending": self.pending(), "books": len(self.books)}
        out.update(self.counts)
        return out
