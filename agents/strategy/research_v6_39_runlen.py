"""v6.39 (research_v639_runlen): U2 stage 2 / S3 -- the run-length growth and the off-path CPU of k115 (H6 / H7 / H14),
behind ONE launcher switch, every decision as v6.38's.  STRUCTURAL throughout; no constant is fitted to a measurement.

  STORE  the session-persisted rolling realized-PnL store and the v6.23 census.  v6.38 copies, re-validates and
         re-sorts every book's list on every realized fill (research_rolling_economics.note_realized_pnl_event) and the
         census re-walks every stored event on every state (research_v623_premium_floor.window_census): O(store) per
         request, ~11 ms a reply at the live plateau (k115 2.5).  Here the lists prune / note produce are marked
         CleanRows (sorted by timestamp, items (int, float), finite, non-zero: exactly what v6.38's prune leaves) and
         the window edges are found by bisection; a book's census is recomputed only when its list object or its
         window edges changed, by the ORIGINAL per-book loop over the same items in the same order, so every bucket
         sum, every rounded value and the window's realized_sum are the same floats.  The original functions are not
         touched; Simple reaches these through its own overrides (Strategy1_Research.py is frozen).
  SWEEP  the v6.33 deep layer sorts a book's sweep record on every depth() call (~220 a state; k115: up to 7 ms a
         state once the records are full).  DeepBook's record is a deque subclass that keeps its sorted copy keyed on
         its own content (tuple equality), so any mutation path (observe, the v6.8 restore, a test) is seen.
  IDLE   the A1.9.9.2 idle full collection runs on every gap (59 ms CPU a request, ~0 objects found; k115 4).  With
         deferred_full the collector runs the full pass only when the interpreter's own trigger would (generation-1
         passes since the last full pass >= the saved threshold[2]) and a young-generation pass otherwise; the F2 hold
         is released either way.
  OBS    the v5.0.3 observatory recorder compresses at gzip level 9 (17 ms CPU a request; k115 4.1); the library's
         default level (zlib.Z_DEFAULT_COMPRESSION) is used instead.  The decompressed data are the same bytes; the
         on-disk size, and so the budget's stop point, differ -- which is why it sits behind the switch.
  (the recorder thread's retained previous state, k115 H14, is freed unconditionally: memory only, no row, no byte)

Off (research_v639_runlen=0): every path is v6.38's code.
"""
from __future__ import annotations

import gzip
import importlib
import math
import zlib
from bisect import bisect_left, bisect_right
from typing import Any, Mapping

from research_rolling_economics import _finite as _roll_finite
from research_v623_premium_floor import BookWindow, PUBLISH_STEP_NS, VOLUME_DECIMALS, KAPPA_TAU, _finite, state_bucket

V6_39_RUNLEN_VERSION = "runlen_v6_39"
SWITCH = "research_v639_runlen"

# SWEEP: the deep layer's in-code switch as (module, name) -- written by apply() at construction (k123's P5 pattern)
SWITCHES = (
    ("research_v633_deep_layer", "_V639_SORTED_CACHE"),
)

_INF = math.inf


def apply(on) -> dict:
    """Writes ``on`` into the deep layer's sorted-cache switch; returns {switch: value} (None where the module lacks it)."""
    value = bool(on)
    held = {}
    for module_name, switch in SWITCHES:
        module = importlib.import_module(module_name)
        if hasattr(module, switch):
            setattr(module, switch, value)
            held[switch] = bool(getattr(module, switch))
        else:
            held[switch] = None
    return held


# ---- STORE --------------------------------------------------------------------------------------------------------

class CleanRows(list):
    """A book's rows as v6.39's prune leaves them: [(int ts, float pnl), ...] sorted by ts (stable), every pnl finite
    and non-zero.  A marker only: no behaviour of list changes (json writes it as a list)."""
    __slots__ = ()


def _window(current, lookback_ns):
    """(current, cutoff) as research_rolling_economics.prune_realized_pnl_events computes them."""
    lookback = max(0, int(lookback_ns or 0))
    cutoff = None if current is None or lookback <= 0 else current - lookback
    return current, cutoff


def _edges(rows: list, cutoff, current, *, upper_if_positive: bool) -> tuple[int, int]:
    """[lo, hi) = the items with cutoff <= ts <= current of a ts-sorted list (tuple comparison: (cutoff,) sorts before
    every (cutoff, pnl); (current, inf) after every (current, pnl))."""
    n = len(rows)
    lo = 0 if cutoff is None else bisect_left(rows, (cutoff,))
    if current is None or (upper_if_positive and current <= 0):
        hi = n
    else:
        hi = bisect_right(rows, (current, _INF))
    return lo, hi


def prune_events(events: Mapping[Any, Any] | None, *, now: int | None, lookback_ns: int) -> dict[int, list]:
    """== research_rolling_economics.prune_realized_pnl_events by content; a CleanRows book is cut by bisection and
    kept as the SAME object when nothing is cut."""
    current = None if now is None else int(now)
    current, cutoff = _window(current, lookback_ns)
    out: dict[int, list] = {}
    for raw_book, raw_rows in dict(events or {}).items():
        try:
            bid = int(raw_book)
        except (TypeError, ValueError):
            continue
        if isinstance(raw_rows, CleanRows):
            lo, hi = _edges(raw_rows, cutoff, current, upper_if_positive=True)
            if lo < hi:
                out[bid] = raw_rows if (lo == 0 and hi == len(raw_rows)) else CleanRows(raw_rows[lo:hi])
            continue
        kept: list[tuple[int, float]] = []
        for ts, pnl in raw_rows or ():
            try:
                its, fpnl = int(ts), float(pnl)
            except (TypeError, ValueError):
                continue
            if not math.isfinite(fpnl) or abs(fpnl) <= 1e-15:
                continue
            if cutoff is not None and its < cutoff:
                continue
            if current is not None and current > 0 and its > current:
                continue
            kept.append((its, fpnl))
        if kept:
            kept.sort(key=lambda x: x[0])
            out[bid] = CleanRows(kept)
    return out


def note_event(events: Mapping[Any, Any] | None, *, book_id: int, timestamp: int, realized_pnl: float,
               now: int | None, lookback_ns: int) -> dict[int, list]:
    """== research_rolling_economics.note_realized_pnl_event by content; the untouched books keep their list objects."""
    out = {int(k): (v if isinstance(v, CleanRows) else list(v)) for k, v in dict(events or {}).items()}
    pnl = _roll_finite(realized_pnl)
    if abs(pnl) <= 1e-15:
        return prune_events(out, now=now, lookback_ns=lookback_ns)
    bid, ts = int(book_id), max(0, int(timestamp))
    rows = out.get(bid)
    if isinstance(rows, CleanRows):
        new = CleanRows(rows)
        new.insert(bisect_right(rows, (ts, _INF)), (ts, pnl))      # == append + stable sort by ts
        out[bid] = new
    else:
        out.setdefault(bid, []).append((ts, pnl))
    return prune_events(out, now=now, lookback_ns=lookback_ns)


def _book_window(rows, lo, hi, current, cutoff, step_ns, dec, tau) -> BookWindow | None:
    """research_v623_premium_floor.window_census's per-book loop, over rows[lo:hi]."""
    buckets: dict[int, float] = {}
    for item in rows[lo:hi]:
        try:
            ts, pnl = int(item[0]), _finite(item[1])
        except (TypeError, ValueError, IndexError):
            continue
        if ts > current or (cutoff is not None and ts < cutoff):
            continue
        key = state_bucket(ts, step_ns)
        buckets[key] = buckets.get(key, 0.0) + pnl
    values = [v for v in (round(raw, dec) for raw in buckets.values()) if v != 0.0]
    if not values:
        return None
    return BookWindow(
        observations=len(values),
        negatives=sum(1 for v in values if v < tau),
        realized_sum=sum(values),
    )


def window_census_cached(cache: dict, events_by_book: Mapping[Any, Any] | None, *, now: Any, lookback_ns: Any,
                         step_ns: Any = PUBLISH_STEP_NS, decimals: Any = VOLUME_DECIMALS,
                         tau: float = KAPPA_TAU) -> dict[int, BookWindow]:
    """== research_v623_premium_floor.window_census by content.  ``cache`` (the agent's, per book: (rows object, key,
    window)) is reused while the book's list object and its window edges are the same."""
    try:
        current = int(now)
    except (TypeError, ValueError):
        return {}
    try:
        lookback = max(0, int(lookback_ns))
    except (TypeError, ValueError):
        lookback = 0
    try:
        dec = max(0, int(decimals))
    except (TypeError, ValueError):
        dec = VOLUME_DECIMALS
    cutoff = current - lookback if lookback > 0 else None
    out: dict[int, BookWindow] = {}
    seen = set()
    for raw_book, rows in dict(events_by_book or {}).items():
        try:
            book = int(raw_book)
        except (TypeError, ValueError):
            continue
        seen.add(book)
        if isinstance(rows, CleanRows):
            lo, hi = _edges(rows, cutoff, current, upper_if_positive=False)
            key = (lo, hi, step_ns, dec, tau)
            ent = cache.get(book)
            if ent is not None and ent[0] is rows and ent[1] == key:
                win = ent[2]
            else:
                win = _book_window(rows, lo, hi, current, cutoff, step_ns, dec, tau)
                cache[book] = (rows, key, win)
        else:
            win = _book_window(rows or (), 0, len(rows or ()), current, cutoff, step_ns, dec, tau)
        if win is not None:
            out[book] = win
    for book in [b for b in cache if b not in seen]:
        del cache[book]
    return out


# ---- OBS ------------------------------------------------------------------------------------------------------------

GZIP_LEVEL = zlib.Z_DEFAULT_COMPRESSION      # -1: zlib's own default (level 6 in zlib's deflate)


def observatory_open(path, mode="ab"):
    """gzip.open at the library's default level (v6.38: gzip.open's own default, level 9)."""
    return gzip.open(path, mode, compresslevel=GZIP_LEVEL)
