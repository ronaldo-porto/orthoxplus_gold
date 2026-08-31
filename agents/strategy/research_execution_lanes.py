# SPDX-License-Identifier: MIT
"""V4.15.1 execution lanes for the single TOTAL_SCORE authority.

The live Research scheduler has exactly three capacity lanes:
  * COVERAGE          - flat economic books without score-completion pressure
  * KAPPA_COMPLETION  - flat books explicitly marked ``total_score_due``
  * REALIZATION       - open inventory or dust

Historical CORE/RECYCLING/density/cohort scheduler fallbacks were removed in
V4.15.1.  Lane identity is allocated once by the current screen and consumed by
execution; it is not re-derived from older scheduler states.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

LANE_COVERAGE = "COVERAGE"
LANE_COMPLETION = "KAPPA_COMPLETION"
LANE_REALIZATION = "REALIZATION"
LANES = (LANE_COVERAGE, LANE_COMPLETION, LANE_REALIZATION)
SPILL_ORDER = (LANE_REALIZATION, LANE_COMPLETION, LANE_COVERAGE)
SCORE_ACQUISITION_REGIMES = frozenset({"COVERAGE", "COMPLETION"})
COMPLETION_ECONOMICS_VERSION = "total_score_completion_v4_15_1"

DEFAULT_COVERAGE_SLOTS = 4
DEFAULT_COMPLETION_SLOTS = 3
DEFAULT_REALIZATION_SLOTS = 3
DEFAULT_SHARED_OVERFLOW_SLOTS = 1
MAX_LANE_SLOTS = 64


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def clamp_slots(value: Any, *, default: int, minimum: int = 0, maximum: int = MAX_LANE_SLOTS) -> int:
    try:
        count = int(value)
    except (TypeError, ValueError):
        count = int(default)
    lo = max(0, int(minimum))
    hi = max(lo, int(maximum))
    return max(lo, min(hi, count))


@dataclass(frozen=True)
class LaneBudgets:
    coverage_slots: int = DEFAULT_COVERAGE_SLOTS
    completion_slots: int = DEFAULT_COMPLETION_SLOTS
    realization_slots: int = DEFAULT_REALIZATION_SLOTS
    shared_overflow_slots: int = DEFAULT_SHARED_OVERFLOW_SLOTS

    def reserved(self, lane: str) -> int:
        if lane == LANE_COVERAGE:
            return int(self.coverage_slots)
        if lane == LANE_COMPLETION:
            return int(self.completion_slots)
        if lane == LANE_REALIZATION:
            return int(self.realization_slots)
        return 0

    def as_dict(self) -> dict[str, int]:
        return {
            "coverage_slots": int(self.coverage_slots),
            "completion_slots": int(self.completion_slots),
            "realization_slots": int(self.realization_slots),
            "shared_overflow_slots": int(self.shared_overflow_slots),
        }

    @property
    def reserved_total(self) -> int:
        return int(self.coverage_slots) + int(self.completion_slots) + int(self.realization_slots)

    @property
    def total_cap(self) -> int:
        return self.reserved_total + int(self.shared_overflow_slots)


def normalize_lane_budgets(
    *,
    coverage_slots: Any = DEFAULT_COVERAGE_SLOTS,
    completion_slots: Any = DEFAULT_COMPLETION_SLOTS,
    realization_slots: Any = DEFAULT_REALIZATION_SLOTS,
    shared_overflow_slots: Any = DEFAULT_SHARED_OVERFLOW_SLOTS,
) -> LaneBudgets:
    return LaneBudgets(
        coverage_slots=clamp_slots(coverage_slots, default=DEFAULT_COVERAGE_SLOTS),
        completion_slots=clamp_slots(completion_slots, default=DEFAULT_COMPLETION_SLOTS),
        realization_slots=clamp_slots(realization_slots, default=DEFAULT_REALIZATION_SLOTS),
        shared_overflow_slots=clamp_slots(
            shared_overflow_slots, default=DEFAULT_SHARED_OVERFLOW_SLOTS,
        ),
    )


@dataclass(frozen=True)
class LaneBook:
    book_id: int
    has_inventory: bool = False
    is_dust: bool = False
    is_hard_risk: bool = False
    is_uncovered: bool = False
    is_stale: bool = False
    is_inactive: bool = False
    observations_remaining: int = 0
    exit_urgency: float = 0.0
    cheap_score: float = 0.0
    maker_ev: float = 0.0
    maker_ev_known: bool = False
    completion_ev_known: bool = False
    completion_ev_ok: bool = True
    economics_ok: bool = True
    entry_feasible: bool = True
    needs_refresh: bool = False
    refresh_urgency: float = 0.0
    deadline_urgency: float = 0.0
    deadline_critical: bool = False
    time_to_deadline_ns: int | None = None
    kappa_eligible: bool = False
    pnl_confidence: str = "FULL"
    pnl_confidence_mult: float = 1.0
    score_pnl_ready: bool = True
    recent_realized_pnl: float = 0.0
    rolling_observation_count: int = 0
    rolling_positive_count: int = 0
    rolling_negative_count: int = 0
    rolling_downside_m3: float = 0.0
    raw_kappa: float | None = None
    maker_fee_bps: float = 0.0
    execution_quality_score: float = 0.0
    execution_quality_tier: str = "UNKNOWN"
    total_score_phase: str = ""
    total_score_due: bool = False
    total_score_value: float = 0.0
    total_score_reason: str = ""
    placements_per_rt: float = 0.0
    maker_fill_conversion: float = 0.0
    contract_reject_rate: float = 0.0


def classify_execution_lane(book: LaneBook) -> str:
    """Map one current V4.15+ screen row to exactly one lane."""
    if book.has_inventory or book.is_dust or book.is_hard_risk:
        return LANE_REALIZATION
    if bool(book.total_score_due) and bool(book.economics_ok) and bool(book.completion_ev_ok):
        return LANE_COMPLETION
    return LANE_COVERAGE


def realization_sort_key(book: LaneBook) -> tuple:
    return (0 if book.is_hard_risk else 1, -_finite(book.exit_urgency), int(book.book_id))


def completion_sort_key(book: LaneBook) -> tuple:
    remaining = max(0, int(book.observations_remaining or 0))
    deadline = max(0.0, min(1.0, _finite(book.deadline_urgency)))
    critical = bool(book.deadline_critical)
    ev_known = bool(book.completion_ev_known)
    ev_ok = bool(book.completion_ev_ok)
    ev_rank = 0 if (ev_known and ev_ok) else (1 if not ev_known else 2)
    tier = str(book.execution_quality_tier or "UNKNOWN").upper()
    efficiency_rank = 0 if tier == "PRODUCTIVE" else (1 if tier == "UNKNOWN" else 2)
    completion_cost = 1 if remaining == 1 else (2 if remaining == 2 else 3)
    return (
        0 if critical else 1,
        0 if critical else ev_rank,
        -_finite(book.total_score_value),
        completion_cost,
        efficiency_rank,
        -deadline,
        0 if bool(book.score_pnl_ready) else 1,
        -_finite(book.recent_realized_pnl),
        _finite(book.maker_fee_bps),
        -_finite(book.cheap_score),
        -_finite(book.maker_ev),
        int(book.book_id),
    )


def coverage_sort_key(book: LaneBook) -> tuple:
    maker_ev = _finite(book.maker_ev)
    known = bool(book.maker_ev_known) or abs(maker_ev) > 1e-15
    positive = bool(book.economics_ok and known and maker_ev > 0.0)
    if positive and book.is_uncovered:
        market_rank = 0
    elif positive and book.is_stale:
        market_rank = 1
    elif positive and book.is_inactive:
        market_rank = 2
    elif positive:
        market_rank = 3
    elif (not known) and book.economics_ok and book.is_uncovered:
        market_rank = 4
    elif (not known) and book.economics_ok and (book.is_stale or book.is_inactive):
        market_rank = 5
    else:
        market_rank = 6
    tier = str(book.execution_quality_tier or "UNKNOWN").upper()
    efficiency_rank = 0 if tier == "PRODUCTIVE" else (1 if tier == "UNKNOWN" else 2)
    return (
        efficiency_rank,
        -_finite(book.total_score_value),
        market_rank,
        -_finite(book.execution_quality_score),
        -maker_ev,
        _finite(book.maker_fee_bps),
        -_finite(book.cheap_score),
        int(book.book_id),
    )


def lane_sort_key(lane: str, book: LaneBook) -> tuple:
    if lane == LANE_REALIZATION:
        return realization_sort_key(book)
    if lane == LANE_COMPLETION:
        return completion_sort_key(book)
    return coverage_sort_key(book)


def allocate_lane_slots(demand: dict[str, int], budgets: LaneBudgets) -> dict[str, Any]:
    """Reserve lane capacity, then spill unused capacity in risk-first order."""
    granted = {lane: 0 for lane in LANES}
    leftover = {lane: 0 for lane in LANES}
    unused_reserved = 0
    for lane in LANES:
        need = max(0, int(demand.get(lane, 0) or 0))
        reserved = budgets.reserved(lane)
        take = min(need, reserved)
        granted[lane] = take
        leftover[lane] = need - take
        unused_reserved += reserved - take

    pool = unused_reserved + int(budgets.shared_overflow_slots)
    spilled = {lane: 0 for lane in LANES}
    overflow_remaining = int(budgets.shared_overflow_slots)
    overflow_used = 0
    for lane in SPILL_ORDER:
        if leftover[lane] <= 0 or pool <= 0:
            continue
        extra = min(leftover[lane], pool)
        granted[lane] += extra
        spilled[lane] = extra
        leftover[lane] -= extra
        pool -= extra
        from_overflow = min(extra, overflow_remaining)
        overflow_used += from_overflow
        overflow_remaining -= from_overflow

    unused = {
        lane: max(0, budgets.reserved(lane) - (granted[lane] - spilled[lane]))
        for lane in LANES
    }
    return {
        "granted": granted,
        "spilled": spilled,
        "leftover": leftover,
        "unused_reserved": unused,
        "overflow_used": overflow_used,
        "pool_remaining": pool,
    }


def admit_lane_candidate(
    *, lane: str, used: dict[str, int] | None, overflow_used: int, budgets: LaneBudgets,
) -> tuple[bool, str | None]:
    """Admit a successful placement against reserved capacity then overflow."""
    if lane not in LANES:
        return False, "UNKNOWN_LANE"
    taken = max(0, int((used or {}).get(lane, 0) or 0))
    if taken < budgets.reserved(lane):
        return True, None
    if max(0, int(overflow_used)) < int(budgets.shared_overflow_slots):
        return True, None
    return False, f"{lane}_SLOT_CAP"


@dataclass
class LaneAllocation:
    selected: list[int] = field(default_factory=list)
    by_lane: dict[str, list[int]] = field(default_factory=dict)
    pool_by_lane: dict[str, list[int]] = field(default_factory=dict)
    demand: dict[str, int] = field(default_factory=dict)
    reserved: dict[str, int] = field(default_factory=dict)
    used: dict[str, int] = field(default_factory=dict)
    spilled: dict[str, int] = field(default_factory=dict)
    unused_reserved: dict[str, int] = field(default_factory=dict)
    overflow_used: int = 0
    budgets: LaneBudgets = field(default_factory=LaneBudgets)

    def as_log(self) -> dict[str, int]:
        return {
            **self.budgets.as_dict(),
            "coverage_demand": int(self.demand.get(LANE_COVERAGE, 0)),
            "completion_demand": int(self.demand.get(LANE_COMPLETION, 0)),
            "realization_demand": int(self.demand.get(LANE_REALIZATION, 0)),
            "coverage_used": int(self.used.get(LANE_COVERAGE, 0)),
            "completion_used": int(self.used.get(LANE_COMPLETION, 0)),
            "realization_used": int(self.used.get(LANE_REALIZATION, 0)),
            "coverage_spilled": int(self.spilled.get(LANE_COVERAGE, 0)),
            "completion_spilled": int(self.spilled.get(LANE_COMPLETION, 0)),
            "realization_spilled": int(self.spilled.get(LANE_REALIZATION, 0)),
            "overflow_used": int(self.overflow_used),
            "selected_count": len(self.selected),
            "coverage_pool": len((self.pool_by_lane or {}).get(LANE_COVERAGE, []) or []),
            "completion_pool": len((self.pool_by_lane or {}).get(LANE_COMPLETION, []) or []),
            "realization_pool": len((self.pool_by_lane or {}).get(LANE_REALIZATION, []) or []),
        }


def select_lane_candidates(
    books: Iterable[LaneBook], budgets: LaneBudgets | None = None, *, max_candidates: int | None = None,
) -> LaneAllocation:
    cfg = budgets or LaneBudgets()
    pools: dict[str, list[LaneBook]] = {lane: [] for lane in LANES}
    seen: set[int] = set()
    for book in books:
        bid = int(book.book_id)
        if bid in seen:
            continue
        seen.add(bid)
        if (
            not bool(book.entry_feasible)
            and not book.has_inventory
            and not book.is_dust
            and not book.is_hard_risk
        ):
            continue
        pools[classify_execution_lane(book)].append(book)

    for lane in LANES:
        pools[lane].sort(key=lambda row: lane_sort_key(lane, row))
    demand = {lane: len(pools[lane]) for lane in LANES}
    pool_by_lane = {lane: [int(row.book_id) for row in pools[lane]] for lane in LANES}
    plan = allocate_lane_slots(demand, cfg)

    selected: list[int] = []
    by_lane: dict[str, list[int]] = {lane: [] for lane in LANES}
    remaining_cap = None if max_candidates is None else max(0, int(max_candidates))
    for lane in (LANE_REALIZATION, LANE_COMPLETION, LANE_COVERAGE):
        take = max(0, int(plan["granted"].get(lane, 0) or 0))
        if remaining_cap is not None:
            take = min(take, remaining_cap)
        chosen = [int(row.book_id) for row in pools[lane][:take]]
        by_lane[lane] = chosen
        selected.extend(chosen)
        if remaining_cap is not None:
            remaining_cap = max(0, remaining_cap - len(chosen))

    return LaneAllocation(
        selected=selected,
        by_lane=by_lane,
        pool_by_lane=pool_by_lane,
        demand=demand,
        reserved={lane: cfg.reserved(lane) for lane in LANES},
        used={lane: len(by_lane[lane]) for lane in LANES},
        spilled=dict(plan["spilled"]),
        unused_reserved=dict(plan["unused_reserved"]),
        overflow_used=int(plan["overflow_used"]),
        budgets=cfg,
    )


def authoritative_execution_lane(
    book_id: Any, *, inventory_flat: bool, allocation: LaneAllocation | None, fallback_lane: str = LANE_COVERAGE,
) -> str:
    """Consume the screen's lane identity; do not re-run score policy at execution."""
    if not bool(inventory_flat):
        return LANE_REALIZATION
    try:
        bid = int(book_id)
    except (TypeError, ValueError):
        return fallback_lane
    if allocation is not None:
        for lane in (LANE_COMPLETION, LANE_COVERAGE):
            allowed = set(int(x) for x in ((allocation.by_lane or {}).get(lane, []) or []))
            allowed.update(int(x) for x in ((allocation.pool_by_lane or {}).get(lane, []) or []))
            if bid in allowed:
                return lane
    return fallback_lane


def score_acquisition_mode(*, score_regime: Any = None, scoring_overlay: Any = None) -> bool:
    return (
        str(score_regime or "").upper() in SCORE_ACQUISITION_REGIMES
        or str(scoring_overlay or "").upper() == "SCORING_PRESSURE"
    )


def score_acquisition_grants(allocation: LaneAllocation | None = None) -> set[int]:
    if allocation is None:
        return set()
    out: set[int] = set()
    for lane in (LANE_COMPLETION, LANE_COVERAGE):
        out.update(int(x) for x in ((allocation.by_lane or {}).get(lane, []) or []))
        out.update(int(x) for x in ((allocation.pool_by_lane or {}).get(lane, []) or []))
    return out


def score_acquisition_granted(
    book_id: Any, *, allocation: LaneAllocation | None = None,
    score_regime: Any = None, scoring_overlay: Any = None,
) -> bool:
    if not score_acquisition_mode(score_regime=score_regime, scoring_overlay=scoring_overlay):
        return False
    try:
        bid = int(book_id)
    except (TypeError, ValueError):
        return False
    return bid in score_acquisition_grants(allocation)
