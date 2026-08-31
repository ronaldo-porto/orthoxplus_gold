# SPDX-License-Identifier: MIT
"""SN79 Research V4.15.1 total-score scheduling authority.

This module intentionally owns only *score-acquisition prioritisation* for flat
books. Risk, inventory exits, FIFO/PnL accounting, contract safety, lifecycle
EV, and the V4.14.4 retry quarantine remain separate authorities.

Design goal
-----------
Maximise the miner's sustainable TOTAL validator score, not observation count
for its own sake.  The current legacy validator path tolerates 48 inactive books
out of 128, therefore 80 Kappa-eligible books removes inactivity zeros.  Before
that point the scheduler must efficiently create high-quality 3-observation
books; after that point additional weak breadth is not automatically valuable.

The live scheduler has exactly three score states:
  * IGNITION  : < 41 Kappa-eligible books
  * SURVIVAL  : 41..79 Kappa-eligible books
  * FRONTIER  : >= 80 Kappa-eligible books

There is no 6/12/50 observation densification target and no CORE/RECYCLING
special scheduling authority in V4.15.1.  Execution quality remains an execution-
efficiency signal only.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Iterable

TOTAL_SCORE_FRONTIER_VERSION = "total_score_frontier_v4_15_1"

PHASE_IGNITION = "IGNITION"
PHASE_SURVIVAL = "SURVIVAL"
PHASE_FRONTIER = "FRONTIER"

REASON_ONE_AWAY = "QUALIFY_ONE_AWAY"
REASON_TWO_AWAY = "QUALIFY_TWO_AWAY"
REASON_COVERAGE = "FRESH_COVERAGE"
REASON_EXPIRY = "EXPIRY_DEFENSE"
REASON_FRONTIER = "MEDIAN_FRONTIER"
REASON_ECONOMIC = "ECONOMIC_ONLY"
REASON_ROTATE = "ROTATE_INEFFICIENT"

DEFAULT_IGNITION_BOOKS = 41
DEFAULT_FULL_BREADTH_BOOKS = 80


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def normalized_kappa(raw_kappa: Any) -> float:
    """Validator-compatible [-2.5,+2.5] -> [0,1] normalization proxy."""
    if raw_kappa is None:
        return 0.50
    x = max(-2.5, min(2.5, _finite(raw_kappa, 0.0)))
    return _clip01((x + 2.5) / 5.0)


def phase_for_qualified(
    qualified_books: int,
    *,
    ignition_books: int = DEFAULT_IGNITION_BOOKS,
    full_breadth_books: int = DEFAULT_FULL_BREADTH_BOOKS,
) -> str:
    n = max(0, int(qualified_books or 0))
    ignition = max(1, int(ignition_books or DEFAULT_IGNITION_BOOKS))
    full = max(ignition, int(full_breadth_books or DEFAULT_FULL_BREADTH_BOOKS))
    if n < ignition:
        return PHASE_IGNITION
    if n < full:
        return PHASE_SURVIVAL
    return PHASE_FRONTIER


def phase_budget_tuple(phase: str) -> tuple[int, int, int, int]:
    """Return COVERAGE, COMPLETION, REALIZATION, OVERFLOW slots.

    The important invariant is that completion demand can never dynamically
    collapse fresh coverage from four slots to one during IGNITION.
    """
    token = str(phase or PHASE_IGNITION).upper()
    if token == PHASE_SURVIVAL:
        return 2, 5, 3, 1
    if token == PHASE_FRONTIER:
        return 2, 4, 3, 1
    return 4, 3, 3, 1


def scoring_pivot_indices(qualified_books: int, *, effective_min_books: int = 80) -> tuple[int, int] | None:
    """Approximate valid-book ranks controlling the Kappa median.

    For q < 80, the validator scores q valid books plus (80-q) inactivity zeros.
    At q>=41, the median positions map to valid indices q-41 and q-40.
    For q>=80 there are no inactivity zeros and the median is the normal median
    of all valid books.
    """
    q = max(0, int(qualified_books or 0))
    effective = max(1, int(effective_min_books or 80))
    half_lo = (effective - 1) // 2
    half_hi = effective // 2
    if q < (effective // 2 + 1):
        return None
    if q < effective:
        zeros = effective - q
        return max(0, half_lo - zeros), max(0, half_hi - zeros)
    # Once inactivity zeros disappear, all valid books participate.
    return (q - 1) // 2, q // 2


def _quality_proxy(row: Any) -> float:
    """Small lower-tail quality proxy; not a second scoring model.

    Current published validator defaults have activity impact=0 and Kappa PnL
    impact=0, so normalized raw Kappa is the dominant per-book Kappa input.  PnL
    and loss-rate are used only as conservative tie/quality adjustments.
    """
    kappa = normalized_kappa(getattr(row, "raw_kappa", None))
    pos = max(0, int(getattr(row, "rolling_positive_count", 0) or 0))
    neg = max(0, int(getattr(row, "rolling_negative_count", 0) or 0))
    n = pos + neg
    loss_rate = (float(neg) / float(n)) if n > 0 else 0.0
    pnl = _finite(getattr(row, "recent_realized_pnl", 0.0), 0.0)
    pnl_term = 0.02 if pnl > 0.0 else (-0.04 if pnl < 0.0 else 0.0)
    return max(0.0, min(1.0, kappa + pnl_term - 0.10 * loss_rate))


@dataclass(frozen=True)
class FrontierPlan:
    phase: str
    qualified_books: int
    ignition_books: int
    full_breadth_books: int
    pivot_low: int | None
    pivot_high: int | None
    frontier_ids: frozenset[int]
    one_away: int
    two_away: int
    fresh: int
    inefficient_rotated: int

    def as_log(self) -> dict[str, Any]:
        return {
            "total_score_frontier_version": TOTAL_SCORE_FRONTIER_VERSION,
            "total_score_phase": self.phase,
            "total_score_qualified": int(self.qualified_books),
            "total_score_ignition_target": int(self.ignition_books),
            "total_score_full_breadth_target": int(self.full_breadth_books),
            "total_score_pivot_low": self.pivot_low,
            "total_score_pivot_high": self.pivot_high,
            "total_score_frontier_books": len(self.frontier_ids),
            "total_score_one_away": int(self.one_away),
            "total_score_two_away": int(self.two_away),
            "total_score_fresh": int(self.fresh),
            "total_score_inefficient_rotated": int(self.inefficient_rotated),
        }


def apply_total_score_frontier(
    books: Iterable[Any],
    *,
    qualified_books: int,
    required_observations: int = 3,
    ignition_books: int = DEFAULT_IGNITION_BOOKS,
    full_breadth_books: int = DEFAULT_FULL_BREADTH_BOOKS,
    frontier_band: int = 2,
) -> tuple[list[Any], FrontierPlan]:
    """Annotate LaneBook rows with the sole V4.15.1 score-scheduling authority.

    The function does not bypass hard economics.  It only supplies a marginal
    score priority. Known non-positive completion EV, toxicity, inventory/risk,
    and entry feasibility remain authoritative in their existing layers.
    """
    rows = list(books or [])
    required = max(1, int(required_observations or 3))
    q = max(0, int(qualified_books or 0))
    phase = phase_for_qualified(
        q, ignition_books=ignition_books, full_breadth_books=full_breadth_books,
    )

    # Rank only Kappa-eligible books.  The narrow band around the exact current
    # median pivot is the only qualified-book score repair admitted by this
    # authority (apart from critical expiry defense).
    qualified_rows = [row for row in rows if bool(getattr(row, "kappa_eligible", False))]
    qualified_rows.sort(key=lambda row: (_quality_proxy(row), int(getattr(row, "book_id", 0))))
    rank_by_id = {int(getattr(row, "book_id")): i for i, row in enumerate(qualified_rows)}
    pivot = scoring_pivot_indices(q, effective_min_books=full_breadth_books)
    band = max(0, min(8, int(frontier_band or 0)))
    frontier_ids: set[int] = set()
    if pivot is not None and qualified_rows:
        lo, hi = pivot
        start = max(0, lo - band)
        stop = min(len(qualified_rows) - 1, hi + band)
        frontier_ids = {
            int(getattr(qualified_rows[i], "book_id")) for i in range(start, stop + 1)
        }

    one_away = two_away = fresh = rotated = 0
    out: list[Any] = []
    for row in rows:
        bid = int(getattr(row, "book_id", 0))
        obs = max(0, int(getattr(row, "rolling_observation_count", 0) or 0))
        remaining = max(0, required - obs)
        eligible = bool(getattr(row, "kappa_eligible", False)) or remaining <= 0
        critical_expiry = bool(
            getattr(row, "needs_refresh", False)
            and getattr(row, "deadline_critical", False)
        )
        # Entry feasibility is a hard gate. A candidate the V4.14.4 retry
        # quarantine or minimum-order sizing has already rejected must not become
        # total_score_due, otherwise it still collects the exact-min admission
        # and stale-TTL privileges that now key off the due set.
        econ_ok = (
            bool(getattr(row, "economics_ok", True))
            and bool(getattr(row, "completion_ev_ok", True))
            and bool(getattr(row, "entry_feasible", True))
        )
        tier = str(getattr(row, "execution_quality_tier", "UNKNOWN") or "UNKNOWN").upper()
        inefficient = tier == "INEFFICIENT"
        realization_row = bool(
            getattr(row, "has_inventory", False)
            or getattr(row, "is_dust", False)
            or getattr(row, "is_hard_risk", False)
        )

        due = False
        value = 0.0
        reason = REASON_ECONOMIC

        if realization_row:
            # Realization/risk is deliberately outside the score scheduler.
            due = False
            value = 0.0
        elif critical_expiry and eligible and econ_ok:
            due = True
            value = 0.96
            reason = REASON_EXPIRY
        elif not eligible:
            if remaining == 1:
                one_away += 1
                # V4.15.1: execution quality is a soft efficiency signal, not
                # a second score authority. Hard economics/feasibility decide due.
                due = bool(econ_ok)
                value = (1.00 if not inefficient else 0.88) if due else 0.08
                reason = REASON_ONE_AWAY if due else REASON_ROTATE
            elif remaining == 2:
                two_away += 1
                due = bool(econ_ok)
                value = (0.72 if not inefficient else 0.60) if due else 0.06
                reason = REASON_TWO_AWAY if due else REASON_ROTATE
            else:
                fresh += 1
                # Fresh books live in COVERAGE, not COMPLETION.  Their score
                # value is phase-sensitive so >=80 does not blindly add weak breadth.
                if phase == PHASE_IGNITION:
                    value = 0.55
                elif phase == PHASE_SURVIVAL:
                    value = 0.38
                else:
                    value = 0.06
                if inefficient:
                    value *= 0.10
                    reason = REASON_ROTATE
                else:
                    reason = REASON_COVERAGE
        else:
            # Already qualified: only the current score frontier gets explicit
            # score pressure. Strong qualified books remain tradable through the
            # ordinary economic COVERAGE path, but cannot steal COMPLETION slots.
            if bid in frontier_ids and phase in {PHASE_SURVIVAL, PHASE_FRONTIER} and econ_ok:
                quality = _quality_proxy(row)
                idx = rank_by_id.get(bid, 0)
                if pivot is not None:
                    distance = min(abs(idx - pivot[0]), abs(idx - pivot[1]))
                else:
                    distance = 99
                proximity = max(0.0, 1.0 - 0.15 * float(distance))
                # Keep frontier repair below a healthy ONE_AWAY and roughly near
                # TWO_AWAY priority. Poor quality raises potential gain, but the
                # hard EV gates still decide whether a trade may be placed.
                weakness = max(0.0, 0.60 - quality)
                value = min(0.78, 0.48 + 0.20 * proximity + 0.20 * weakness)
                if inefficient:
                    value *= 0.82
                due = True
                reason = REASON_FRONTIER
            else:
                # Economic-only qualified trading stays possible, especially at
                # >=80, but receives no artificial densification bonus.
                value = 0.20 if phase == PHASE_FRONTIER else 0.04
                reason = REASON_ECONOMIC
                if inefficient:
                    value *= 0.10
                    rotated += 1
                    reason = REASON_ROTATE

        # Count only books the score scheduler actually rotated. Inventory,
        # dust and hard-risk rows never competed for score capacity, so folding
        # them in here would make the operator warning signal in S14.2 unusable.
        if inefficient and not eligible and not realization_row:
            rotated += 1

        out.append(
            replace(
                row,
                total_score_phase=phase,
                total_score_due=bool(due),
                total_score_value=float(value),
                total_score_reason=str(reason),
            )
        )

    plan = FrontierPlan(
        phase=phase,
        qualified_books=q,
        ignition_books=max(1, int(ignition_books)),
        full_breadth_books=max(1, int(full_breadth_books)),
        pivot_low=(pivot[0] if pivot is not None else None),
        pivot_high=(pivot[1] if pivot is not None else None),
        frontier_ids=frozenset(frontier_ids),
        one_away=one_away,
        two_away=two_away,
        fresh=fresh,
        inefficient_rotated=rotated,
    )
    return out, plan
