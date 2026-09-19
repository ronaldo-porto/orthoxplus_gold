# SPDX-License-Identifier: MIT
"""v6.2.3: the no-loss floor protects the validator's no-loss premium, and nothing else.

Measured on testnet UID 82 (v6.2.1, 2026-09-19 00:22-10:53, 7,737 ticks, 2.15 sim-h): quoted books
51 -> 10 while books holding a lot rose to ~40; 87 lots reached their v6.1 floor, 51 recovered
(wait p50 220 s) and 36 never did.  The 38 books still floored at the end first hit their floor a
p50 8 bps past break-even and ended p50 1,723 bps away.  A driftless walk returns to a level with a
heavy-tailed first-passage time, so a strict floor locks books permanently.  v6.2.2 repeated it:
quoted 24 -> 15 and held 41 -> 52 in 500 ticks.

What the floor buys is set by the validator's per-book Kappa-3 (``taos/im/utils/kappa.py``
``kappa_3``; live 2026-09-19: tau 0.0, min_realized_observations 3, lookback 10,800 sim-s):

* kappa = (mean - tau) / cbrt(LPM3 + reg) over every state of the window.  With no period below
  tau, LPM3 = 0 and the denominator is the regularization floor: the clean-record premium
  (normalized 0.53 at 3 closes, 0.62 at 45, 0.72 at 180 in a full window).
* One large loss makes LPM3 dominate: normalized ~0.4996 whatever its size.  Three losses alone
  give 0.4993.  So a loss costs a book its premium, almost independent of the loss.
* Fewer than 3 non-zero periods: kappa None -- the book is not scored.
* kappa_score = median over books, with up to 48 (0.375 x 128) unscored books ignored and the rest
  counted as 0.0.  v6.2.1's locked books left ~21 of 128 scored: a median of 0.

The PnL leg is a per-book median of windowed PnL over 50,000 x 0.125 = 6,250 quote per book, so our
losses move it by < 1e-3.  The floor's only payoff is therefore the premium on a book that has one:
>= 3 non-zero periods in the window and none below tau.  Everywhere else (no scored kappa, or a
window already in the downside branch) the floor protects nothing and locks the book.

This module is that classification on the validator's own arithmetic (``trade.py`` realized-PnL
bookkeeping: raw PnL summed per reporting state, the sum rounded to the volume decimals and stored
only when non-zero).  Pure functions only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

V623_PREMIUM_FLOOR_VERSION = "premium_floor_v6_2_3"

# The validator's constants (taos/im/config/__init__.py defaults; live values checked on mainnet
# UID 0's /metrics/validator 2026-09-19 11:08).
KAPPA_TAU = 0.0                          # --scoring.kappa.tau
KAPPA_MIN_REALIZED_OBSERVATIONS = 3      # --scoring.kappa.min_realized_observations
PUBLISH_STEP_NS = 1_000_000_000          # simulation publish_interval: one realized-PnL row per state
VOLUME_DECIMALS = 4                      # simulation volumeDecimals: trade.py rounds realized PnL to it

BOOK_PREMIUM = "PREMIUM"                 # >= 3 non-zero periods, none below tau: the floor protects it
BOOK_LOSS = "LOSS_IN_WINDOW"             # >= 3 non-zero periods, one or more below tau
BOOK_THIN = "THIN_WINDOW"                # 1-2 non-zero periods: kappa None
BOOK_EMPTY = "EMPTY_WINDOW"              # no non-zero period: kappa None
BOOK_STATUSES = (BOOK_PREMIUM, BOOK_LOSS, BOOK_THIN, BOOK_EMPTY)


@dataclass(frozen=True)
class BookWindow:
    """One book's realized PnL in the kappa window, bucketed the way the validator stores it."""

    observations: int = 0     # states with a non-zero realized sum
    negatives: int = 0        # of those, states whose sum is below tau
    realized_sum: float = 0.0

    def as_log(self) -> dict[str, Any]:
        return {
            "observations": int(self.observations), "negatives": int(self.negatives),
            "realized_sum": round(float(self.realized_sum), 6),
        }


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def state_bucket(ts: Any, step_ns: Any = PUBLISH_STEP_NS) -> int:
    """The state timestamp a trade at ``ts`` is reported in: the first publish-grid point at or after it."""
    t = int(ts)
    try:
        step = int(step_ns)
    except (TypeError, ValueError):
        step = PUBLISH_STEP_NS
    if step <= 0:
        return t
    return -(-t // step) * step


def window_census(
    events_by_book: Mapping[Any, Iterable[Any]] | None,
    *,
    now: Any,
    lookback_ns: Any,
    step_ns: Any = PUBLISH_STEP_NS,
    decimals: Any = VOLUME_DECIMALS,
    tau: float = KAPPA_TAU,
) -> dict[int, BookWindow]:
    """Per book: the validator's non-zero realized periods in ``(now - lookback, now]``.

    ``events_by_book`` is ``{book: [(timestamp_ns, realized_pnl), ...]}`` (the agent's
    session-persisted rolling store, keyed by trade time).  ``trade.py`` sums the raw realized PnL
    of every trade reported in one state under that state's timestamp, then stores the sum rounded
    to ``decimals`` and only when that is non-zero.  Here each trade goes to the state that reports
    it (``state_bucket``), and the same rounding decides the observation; a negative stored sum is
    a downside observation.
    """
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
    for raw_book, rows in dict(events_by_book or {}).items():
        try:
            book = int(raw_book)
        except (TypeError, ValueError):
            continue
        buckets: dict[int, float] = {}
        for item in rows or ():
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
            continue
        out[book] = BookWindow(
            observations=len(values),
            negatives=sum(1 for v in values if v < tau),
            realized_sum=sum(values),
        )
    return out


def book_status(window: BookWindow | None, *, min_observations: Any = KAPPA_MIN_REALIZED_OBSERVATIONS) -> str:
    """Which branch of Kappa-3 the book's window is in."""
    try:
        need = max(1, int(min_observations))
    except (TypeError, ValueError):
        need = KAPPA_MIN_REALIZED_OBSERVATIONS
    if window is None or window.observations <= 0:
        return BOOK_EMPTY
    if window.observations < need:
        return BOOK_THIN
    if window.negatives > 0:
        return BOOK_LOSS
    return BOOK_PREMIUM


def floor_applies(status: Any) -> bool:
    """The no-loss floor binds only where it protects the clean-record premium."""
    return str(status) == BOOK_PREMIUM


def census_counts(
    census: Mapping[int, BookWindow] | None,
    book_ids: Iterable[Any],
    *,
    min_observations: Any = KAPPA_MIN_REALIZED_OBSERVATIONS,
) -> dict[str, int]:
    """How many books of the universe sit in each branch (the validator's coverage and median inputs)."""
    counts = {status: 0 for status in BOOK_STATUSES}
    table = dict(census or {})
    for raw in book_ids:
        try:
            book = int(raw)
        except (TypeError, ValueError):
            continue
        counts[book_status(table.get(book), min_observations=min_observations)] += 1
    return counts
