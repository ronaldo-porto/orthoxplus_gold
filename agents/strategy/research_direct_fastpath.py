# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.6.0 observable FastPath utilities.

Design goal: preserve A1.5's latency win without letting score-completion state
hide current executable opportunities.

The pre-screen is intentionally cheap and observable-only:
* current top-of-book spread;
* current signed Maker fee;
* current top-of-book liquidity proxy;
* current Kappa observations remaining;
* deterministic fairness / short cooldown after repeated economic skips.

No learned quality, realized-PnL posterior, future exit probability, markout
posterior, or latency penalty is allowed to decide FastPath admission.
"""
from __future__ import annotations

from dataclasses import dataclass
import heapq
import math
from typing import Any, Iterable

DIRECT_FASTPATH_VERSION = "direct_fastpath_v4_16_2_a1_6_1"
DIRECT_FASTPATH_CANDIDATE_COUNT = 20
DIRECT_FASTPATH_MIN_CANDIDATES = 16
DIRECT_FASTPATH_MAX_CANDIDATES = 24
DIRECT_FASTPATH_DEEP_COUNT = 16
DIRECT_QUALIFIED_CADENCE = 1
DIRECT_MAX_QUALIFIED_SHARE = 0.25
DIRECT_MAX_PRE_SUBMIT_AGE_MS = 100.0
DIRECT_TELEMETRY_SAMPLE_TICKS = 25
DIRECT_EDGE_FAIL_STREAK = 3
DIRECT_EDGE_COOLDOWN_TICKS = 4


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


def observable_maker_edge_bps(*, spread_bps: float, maker_fee_bps: float) -> float:
    """Current gross half-spread capture less the signed entry Maker fee."""
    return 0.5 * max(0.0, _finite(spread_bps)) - _finite(maker_fee_bps)


@dataclass(frozen=True)
class FastPathRow:
    book_id: int
    priority: float
    observations_remaining: int
    qualified: bool
    has_inventory: bool = False
    is_dust: bool = False
    observable_edge_bps: float = 0.0
    maker_fee_bps: float = 0.0
    liquidity_quality: float = 0.0
    cooled: bool = False


def cheap_priority(
    *,
    observations_remaining: int,
    qualified: bool,
    spread_bps: float,
    maker_fee_bps: float = 0.0,
    liquidity_quality: float = 0.0,
    ticks_since_selected: int = 0,
    score_deficit: int = 0,
) -> float:
    """Observable score used only to choose who gets expensive prediction.

    Economics is primary. Kappa need decides which *good* opportunities matter
    more; it does not turn a currently negative-fee/spread opportunity into a
    preferred candidate.
    """
    rem = max(0, int(observations_remaining or 0))
    if rem == 1:
        completion = 4.0
    elif rem == 2:
        completion = 3.0
    elif rem > 2:
        completion = 2.0
    else:
        completion = 0.0

    edge = observable_maker_edge_bps(spread_bps=spread_bps, maker_fee_bps=maker_fee_bps)
    # Keep current economics dominant but bounded against pathological spreads.
    economic = max(-5.0, min(10.0, edge / 2.0))
    liquidity = max(0.0, min(1.0, _finite(liquidity_quality)))
    fairness = min(1.5, max(0, int(ticks_since_selected or 0)) / 12.0)
    qualified_penalty = 1.0 if bool(qualified) and int(score_deficit or 0) > 0 else 0.0

    return economic + completion + 0.75 * liquidity + fairness - qualified_penalty


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
    """Return bounded top-K ids while reserving room for economic qualified books.

    Inventory is always forced. Cooled acquisition rows are skipped. During a
    score deficit, most acquisition slots go to incomplete books but up to 25%
    remain available to already-qualified books when their current economics are
    stronger. This prevents completion pressure from destroying productivity.
    """
    cap = clamp_candidate_count(candidate_count)
    forced: list[FastPathRow] = []
    incomplete: list[FastPathRow] = []
    qualified: list[FastPathRow] = []
    for row in rows:
        # A1.6.1 liveness: true sub-minimum dust is maintained by the dedicated
        # dust lane.  It must never consume productive FastPath/deep slots.
        if row.is_dust:
            continue
        if row.has_inventory:
            forced.append(row)
            continue
        if row.cooled:
            continue
        # Known negative current Maker economics do not deserve a deep slot.
        if _finite(row.observable_edge_bps) < 0.0:
            continue
        if not row.qualified:
            incomplete.append(row)
        elif int(score_deficit or 0) <= 0 or qualified_cadence_allows(
            tick=tick, book_id=row.book_id, cadence=qualified_cadence
        ):
            qualified.append(row)

    forced_ids = list(dict.fromkeys(r.book_id for r in forced))
    room = max(0, cap - len(forced_ids))
    selected: list[int] = list(forced_ids)

    if room <= 0:
        return selected[:cap]

    if int(score_deficit or 0) > 0:
        qcap = max(1, int(math.floor(room * max(0.0, min(1.0, max_qualified_share)))))
        icap = max(0, room - qcap)
        chosen_i = heapq.nlargest(icap, incomplete, key=lambda r: (r.priority, -r.book_id))
        chosen_q = heapq.nlargest(qcap, qualified, key=lambda r: (r.priority, -r.book_id))
        selected.extend(r.book_id for r in chosen_i)
        selected.extend(r.book_id for r in chosen_q)

        # Fill any unused quota from the best remaining current opportunities.
        if len(selected) < cap:
            used = set(selected)
            leftovers = [r for r in incomplete + qualified if r.book_id not in used]
            extra = heapq.nlargest(cap - len(selected), leftovers, key=lambda r: (r.priority, -r.book_id))
            selected.extend(r.book_id for r in extra)
    else:
        candidates = incomplete + qualified
        chosen = heapq.nlargest(room, candidates, key=lambda r: (r.priority, -r.book_id))
        selected.extend(r.book_id for r in chosen)

    return list(dict.fromkeys(int(x) for x in selected))[:cap]
