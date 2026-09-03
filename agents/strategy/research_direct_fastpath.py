# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.5 low-latency candidate utilities.

The Direct fast path intentionally keeps score state cheap and bounded:
* all books receive only a lightweight priority pass;
* incomplete/near-qualified books dominate while score deficit remains;
* already-qualified books are throttled unless their cached economics are strong;
* only top-K books continue to full prediction/economics.

No latency value is subtracted from trade EV here.  Latency is an execution
engineering constraint, not a universal economics veto.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from typing import Any, Iterable

DIRECT_FASTPATH_VERSION = "direct_fastpath_v4_16_2_a1_5"
DIRECT_FASTPATH_CANDIDATE_COUNT = 12
DIRECT_FASTPATH_MIN_CANDIDATES = 8
DIRECT_FASTPATH_MAX_CANDIDATES = 16
DIRECT_QUALIFIED_CADENCE = 5
DIRECT_MAX_QUALIFIED_SHARE = 0.25
DIRECT_MAX_PRE_SUBMIT_AGE_MS = 100.0
DIRECT_TELEMETRY_SAMPLE_TICKS = 25


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def clamp_candidate_count(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = DIRECT_FASTPATH_CANDIDATE_COUNT
    return max(DIRECT_FASTPATH_MIN_CANDIDATES, min(DIRECT_FASTPATH_MAX_CANDIDATES, n))


@dataclass(frozen=True)
class FastPathRow:
    book_id: int
    priority: float
    observations_remaining: int
    qualified: bool
    has_inventory: bool = False
    is_dust: bool = False


def cheap_priority(
    *,
    observations_remaining: int,
    qualified: bool,
    spread_bps: float,
    cached_alpha_rank: float = 0.0,
    cached_realized_pnl: float = 0.0,
    quality_penalty: float = 0.0,
    ticks_since_selected: int = 0,
    score_deficit: int = 0,
) -> float:
    """Cheap score used only to decide who gets expensive prediction.

    The weights deliberately prioritize score completion and breadth, then use
    cached economic quality only as a tie-breaker.  This is not trade EV.
    """
    rem = max(0, int(observations_remaining or 0))
    if rem == 1:
        completion = 12.0
    elif rem == 2:
        completion = 9.0
    elif rem > 2:
        completion = 6.0
    else:
        completion = 0.0

    # While score breadth is incomplete, ordinary qualified books should not
    # consume most of the expensive prediction budget.
    qualified_penalty = 5.0 if bool(qualified) and int(score_deficit or 0) > 0 else 0.0

    spread = max(0.0, _finite(spread_bps))
    # Moderate/wide spread helps Maker economics, but cap it so stressed books
    # do not dominate this cheap pre-score.
    spread_term = min(spread, 20.0) / 20.0
    alpha_term = max(-1.0, min(1.0, _finite(cached_alpha_rank))) * 0.60
    pnl = _finite(cached_realized_pnl)
    pnl_term = max(-0.75, min(0.75, pnl / 0.5))
    quality_term = min(1.0, max(0.0, _finite(quality_penalty)) / 0.03)
    fairness = min(3.0, max(0, int(ticks_since_selected or 0)) / 10.0)

    return (
        completion
        - qualified_penalty
        + spread_term
        + alpha_term
        + 0.50 * pnl_term
        - 1.25 * quality_term
        + fairness
    )


def qualified_cadence_allows(*, tick: int, book_id: int, cadence: int = DIRECT_QUALIFIED_CADENCE) -> bool:
    c = max(1, int(cadence or 1))
    return (int(tick or 0) + int(book_id or 0)) % c == 0


def select_fastpath_rows(
    rows: Iterable[FastPathRow],
    *,
    candidate_count: int,
    score_deficit: int,
    tick: int,
    qualified_cadence: int = DIRECT_QUALIFIED_CADENCE,
    max_qualified_share: float = DIRECT_MAX_QUALIFIED_SHARE,
) -> list[int]:
    """Return bounded top-K ids without a full-universe sort."""
    cap = clamp_candidate_count(candidate_count)
    forced: list[FastPathRow] = []
    incomplete: list[FastPathRow] = []
    qualified: list[FastPathRow] = []
    for row in rows:
        if row.has_inventory:
            forced.append(row)
        elif not row.qualified:
            incomplete.append(row)
        elif int(score_deficit or 0) <= 0 or qualified_cadence_allows(
            tick=tick, book_id=row.book_id, cadence=qualified_cadence
        ):
            qualified.append(row)

    # Inventory is included for current profiles/management but does not need
    # to consume the acquisition quota in downstream Direct orchestration.
    forced_ids = list(dict.fromkeys(r.book_id for r in forced))
    room = max(0, cap - len(forced_ids))
    chosen_incomplete = heapq.nlargest(
        room, incomplete, key=lambda r: (r.priority, -r.book_id)
    )
    selected = forced_ids + [r.book_id for r in chosen_incomplete]
    room = max(0, cap - len(selected))
    if room > 0 and qualified:
        # During score deficit, keep qualified prediction share bounded.
        if int(score_deficit or 0) > 0:
            qcap = max(1, int(math.floor(cap * max(0.0, min(1.0, max_qualified_share)))))
            room = min(room, qcap)
        chosen_q = heapq.nlargest(room, qualified, key=lambda r: (r.priority, -r.book_id))
        selected.extend(r.book_id for r in chosen_q)
    return list(dict.fromkeys(int(x) for x in selected))
