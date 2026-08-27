# SPDX-License-Identifier: MIT
"""V4.12.18 Kappa flywheel primitives with migration confidence.

The validator rewards cross-book Kappa breadth and sustained realized-outcome
quality.  This helper keeps a restart-safe rolling realized-PnL evidence set and
classifies qualified books by density without inventing a second alpha model.

The helper deliberately does not reimplement the validator's MAD-normalized
Kappa formula.  ``raw_kappa`` remains sourced from the existing profile/score
path; this module supplies authoritative rolling realized-PnL and density state
for scheduling and PnL-readiness decisions.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

KAPPA_FLYWHEEL_VERSION = "kappa_flywheel_v4_12_18"


PNL_CONFIDENCE_FULL = "FULL"
PNL_CONFIDENCE_PARTIAL = "PARTIAL"
PNL_CONFIDENCE_UNKNOWN = "UNKNOWN"


def pnl_confidence(observation_count: int, pnl_event_count: int) -> str:
    """Confidence that restart-safe realized-PnL evidence covers Kappa observations.

    Legacy sessions can restore valid Kappa observation timestamps without the
    newer realized-PnL event ledger. V4.12.18 never fabricates that PnL; it marks
    the evidence UNKNOWN/PARTIAL and keeps the book eligible for density work at
    a lower ranking confidence.
    """
    obs = max(0, int(observation_count or 0))
    pnl = max(0, int(pnl_event_count or 0))
    if obs <= 0 or pnl >= obs:
        return PNL_CONFIDENCE_FULL
    if pnl > 0:
        return PNL_CONFIDENCE_PARTIAL
    return PNL_CONFIDENCE_UNKNOWN


def pnl_confidence_multiplier(confidence: str) -> float:
    token = str(confidence or PNL_CONFIDENCE_UNKNOWN).upper()
    if token == PNL_CONFIDENCE_FULL:
        return 1.0
    if token == PNL_CONFIDENCE_PARTIAL:
        return 0.85
    return 0.70

PHASE_BOOTSTRAP = "BOOTSTRAP"
PHASE_BREADTH = "BREADTH"
PHASE_DENSITY = "DENSITY"

STATE_UNCOVERED = "UNCOVERED"
STATE_ONE_AWAY = "ONE_AWAY"
STATE_TWO_AWAY = "TWO_AWAY"
STATE_QUALIFIED_LOW_DENSITY = "QUALIFIED_LOW_DENSITY"
STATE_QUALIFIED_DEVELOPING = "QUALIFIED_DEVELOPING"
STATE_QUALIFIED_CORE = "QUALIFIED_CORE"
STATE_REFRESH_DUE = "REFRESH_DUE"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def flywheel_phase(score_qualified_books: int) -> str:
    n = max(0, int(score_qualified_books or 0))
    if n < 41:
        return PHASE_BOOTSTRAP
    if n < 80:
        return PHASE_BREADTH
    return PHASE_DENSITY


def phase_density_target(phase: str) -> int:
    token = str(phase or PHASE_BOOTSTRAP).upper()
    if token == PHASE_DENSITY:
        return 50
    if token == PHASE_BREADTH:
        return 12
    return 6


def phase_core_limit(phase: str) -> int:
    token = str(phase or PHASE_BOOTSTRAP).upper()
    if token == PHASE_DENSITY:
        return 48
    if token == PHASE_BREADTH:
        return 24
    return 8


def density_state(
    *,
    realized_observations: int,
    required_observations: int,
    needs_refresh: bool = False,
) -> str:
    n = max(0, int(realized_observations or 0))
    required = max(1, int(required_observations or 1))
    remaining = max(0, required - n)
    if remaining >= required:
        return STATE_UNCOVERED
    if remaining == 2:
        return STATE_TWO_AWAY
    if remaining == 1:
        return STATE_ONE_AWAY
    if needs_refresh:
        return STATE_REFRESH_DUE
    if n < 10:
        return STATE_QUALIFIED_LOW_DENSITY
    if n < 50:
        return STATE_QUALIFIED_DEVELOPING
    return STATE_QUALIFIED_CORE


@dataclass(frozen=True)
class RollingBookEconomics:
    book: int
    nonzero_count: int
    positive_count: int
    negative_count: int
    realized_sum: float
    realized_mean: float
    downside_m3: float
    oldest_timestamp: int | None
    newest_timestamp: int | None

    @property
    def pnl_ready(self) -> bool:
        return self.nonzero_count > 0 and self.realized_sum >= 0.0

    @property
    def loss_rate(self) -> float:
        if self.nonzero_count <= 0:
            return 0.0
        return self.negative_count / float(self.nonzero_count)

    def as_log(self) -> dict[str, Any]:
        return {
            "kappa_flywheel_version": KAPPA_FLYWHEEL_VERSION,
            "rolling_realized_count": int(self.nonzero_count),
            "rolling_positive_count": int(self.positive_count),
            "rolling_negative_count": int(self.negative_count),
            "rolling_realized_pnl": float(self.realized_sum),
            "rolling_realized_mean": float(self.realized_mean),
            "rolling_downside_m3": float(self.downside_m3),
            "rolling_loss_rate": float(self.loss_rate),
            "rolling_oldest_pnl_ts": self.oldest_timestamp,
            "rolling_newest_pnl_ts": self.newest_timestamp,
        }


def sanitize_realized_pnl_events(raw: Any) -> dict[int, list[tuple[int, float]]]:
    if not isinstance(raw, Mapping):
        return {}
    out: dict[int, list[tuple[int, float]]] = {}
    for raw_book, raw_rows in raw.items():
        try:
            bid = int(raw_book)
        except (TypeError, ValueError):
            continue
        if not isinstance(raw_rows, (list, tuple)):
            continue
        rows: list[tuple[int, float]] = []
        for item in raw_rows:
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            try:
                ts = int(item[0])
                pnl = float(item[1])
            except (TypeError, ValueError):
                continue
            if ts < 0 or not math.isfinite(pnl) or abs(pnl) <= 1e-15:
                continue
            rows.append((ts, pnl))
        if rows:
            rows.sort(key=lambda x: x[0])
            out[bid] = rows
    return out


def prune_realized_pnl_events(
    events: Mapping[int, list[tuple[int, float]]] | None,
    *,
    now: int | None,
    lookback_ns: int,
) -> dict[int, list[tuple[int, float]]]:
    current = None if now is None else int(now)
    lookback = max(0, int(lookback_ns or 0))
    cutoff = None if current is None or lookback <= 0 else current - lookback
    out: dict[int, list[tuple[int, float]]] = {}
    for raw_book, raw_rows in dict(events or {}).items():
        try:
            bid = int(raw_book)
        except (TypeError, ValueError):
            continue
        kept: list[tuple[int, float]] = []
        for ts, pnl in raw_rows or ():
            try:
                its = int(ts)
                fpnl = float(pnl)
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
            out[bid] = kept
    return out


def note_realized_pnl_event(
    events: Mapping[int, list[tuple[int, float]]] | None,
    *,
    book_id: int,
    timestamp: int,
    realized_pnl: float,
    now: int | None,
    lookback_ns: int,
) -> dict[int, list[tuple[int, float]]]:
    out = {int(k): list(v) for k, v in dict(events or {}).items()}
    pnl = _finite(realized_pnl)
    if abs(pnl) <= 1e-15:
        return prune_realized_pnl_events(out, now=now, lookback_ns=lookback_ns)
    bid = int(book_id)
    ts = max(0, int(timestamp))
    out.setdefault(bid, []).append((ts, pnl))
    return prune_realized_pnl_events(out, now=now, lookback_ns=lookback_ns)


def rolling_book_economics(
    events: Mapping[int, list[tuple[int, float]]] | None,
    book_id: int,
    *,
    now: int | None,
    lookback_ns: int,
) -> RollingBookEconomics:
    pruned = prune_realized_pnl_events(events, now=now, lookback_ns=lookback_ns)
    rows = list(pruned.get(int(book_id), ()) or ())
    pnls = [float(pnl) for _, pnl in rows if abs(float(pnl)) > 1e-15]
    n = len(pnls)
    positive = sum(1 for pnl in pnls if pnl > 0.0)
    negative = sum(1 for pnl in pnls if pnl < 0.0)
    total = sum(pnls)
    mean = total / n if n else 0.0
    downside_m3 = sum(max(-pnl, 0.0) ** 3 for pnl in pnls) / n if n else 0.0
    return RollingBookEconomics(
        book=int(book_id),
        nonzero_count=n,
        positive_count=positive,
        negative_count=negative,
        realized_sum=total,
        realized_mean=mean,
        downside_m3=downside_m3,
        oldest_timestamp=(rows[0][0] if rows else None),
        newest_timestamp=(rows[-1][0] if rows else None),
    )
