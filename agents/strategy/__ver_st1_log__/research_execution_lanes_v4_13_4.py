# SPDX-License-Identifier: MIT
"""Research execution lanes: COVERAGE / KAPPA_COMPLETION / REALIZATION.

Inventory books must not consume nearly every candidate slot. Each lane
receives a reserved budget. Unused reserved slots plus shared overflow
may spill into other lanes.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Iterable

LANE_COVERAGE = "COVERAGE"
LANE_COMPLETION = "KAPPA_COMPLETION"
LANE_REALIZATION = "REALIZATION"
LANES = (LANE_COVERAGE, LANE_COMPLETION, LANE_REALIZATION)
SPILL_ORDER = (LANE_REALIZATION, LANE_COMPLETION, LANE_COVERAGE)
SCORE_ACQUISITION_REGIMES = frozenset({"COVERAGE", "COMPLETION"})


def execution_completion_candidate(
    *,
    inventory_flat: bool,
    core_probe_candidate: bool,
    legacy_completion_candidate: bool,
) -> bool:
    """Preserve CORE_PROBE completion-lane authority at execution.

    V4.13.2 screening could reserve a KAPPA_COMPLETION slot for an already
    Kappa-eligible CORE_PROBE, while the later legacy completion predicate
    correctly returned False for that same eligible book.  Execution then
    recomputed the lane as COVERAGE and rejected the probe as LANE_NOT_GRANTED.
    This helper makes the screening probe flag authoritative for lane identity.
    """
    return bool(
        inventory_flat
        and (bool(core_probe_candidate) or bool(legacy_completion_candidate))
    )



def authoritative_execution_lane(
    book_id: Any,
    *,
    inventory_flat: bool,
    allocation: Any = None,
    fallback_lane: str = LANE_COVERAGE,
) -> str:
    """Resolve the execution lane from the authoritative screen allocation.

    V4.13.3 preserved allocation identity only for CORE_PROBE by rebuilding a
    completion predicate at execution.  That still lost CORE, RECYCLING,
    density_due and other qualified completion grants because those books are
    already Kappa-eligible.  Candidate screening owns lane allocation; execution
    must consume that decision rather than re-derive scheduler policy.

    Non-flat inventory always has REALIZATION priority.  For a flat book, an
    explicit KAPPA_COMPLETION or COVERAGE grant is authoritative.  The caller's
    legacy lane is used only when no current grant exists.
    """
    if not bool(inventory_flat):
        return LANE_REALIZATION

    try:
        bid = int(book_id)
    except (TypeError, ValueError):
        return str(fallback_lane or LANE_COVERAGE)

    if allocation is not None:
        by_lane = getattr(allocation, "by_lane", None) or {}
        completion_grants = set(
            int(x) for x in (by_lane.get(LANE_COMPLETION, []) or [])
        )
        if bid in completion_grants:
            return LANE_COMPLETION
        coverage_grants = set(
            int(x) for x in (by_lane.get(LANE_COVERAGE, []) or [])
        )
        if bid in coverage_grants:
            return LANE_COVERAGE

    return str(fallback_lane or LANE_COVERAGE)

def score_acquisition_mode(*, score_regime: Any = None, scoring_overlay: Any = None) -> bool:
    """Return whether score acquisition may bridge inactive-book gating.

    COVERAGE historically exposed ``SCORING_PRESSURE`` through the inherited
    overlay.  The independent ScoreRegime added COMPLETION without that overlay,
    which left selected Kappa-completion books permanently blocked as INACTIVE.
    Treat either explicit overlay pressure or the independent COVERAGE/COMPLETION
    score states as acquisition demand.
    """
    overlay = str(scoring_overlay or "").upper()
    regime = str(score_regime or "").upper()
    return overlay == "SCORING_PRESSURE" or regime in SCORE_ACQUISITION_REGIMES


def score_acquisition_granted(
    book_id: Any,
    *,
    allocation: Any = None,
    score_regime: Any = None,
    scoring_overlay: Any = None,
) -> bool:
    """Fail-closed selected-only inactive bootstrap authorization.

    When execution lanes are available, only books actually granted COVERAGE or
    KAPPA_COMPLETION capacity may bypass the inactive/dead-book bootstrap gate.
    With no lane allocation we preserve the legacy explicit SCORING_PRESSURE
    behavior, but an independent COMPLETION state alone is not enough to open the
    entire universe.
    """
    if not score_acquisition_mode(
        score_regime=score_regime, scoring_overlay=scoring_overlay,
    ):
        return False
    overlay = str(scoring_overlay or "").upper()
    if allocation is None:
        return overlay == "SCORING_PRESSURE"
    by_lane = getattr(allocation, "by_lane", None) or {}
    try:
        bid = int(book_id)
    except (TypeError, ValueError):
        return False
    granted = set(int(x) for x in (by_lane.get(LANE_COMPLETION, []) or []))
    granted.update(int(x) for x in (by_lane.get(LANE_COVERAGE, []) or []))
    return bid in granted


def score_acquisition_grants(allocation: Any = None) -> set[int]:
    """Return current COVERAGE + COMPLETION lane grants."""
    if allocation is None:
        return set()
    by_lane = getattr(allocation, "by_lane", None) or {}
    out = set(int(x) for x in (by_lane.get(LANE_COMPLETION, []) or []))
    out.update(int(x) for x in (by_lane.get(LANE_COVERAGE, []) or []))
    return out

DEFAULT_COVERAGE_SLOTS = 6
DEFAULT_COMPLETION_SLOTS = 6
DEFAULT_REALIZATION_SLOTS = 4
DEFAULT_SHARED_OVERFLOW_SLOTS = 4
MAX_LANE_SLOTS = 64


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if number != number or number in {float("inf"), float("-inf")}:
        return default
    return number


def clamp_slots(
    value: Any,
    *,
    default: int,
    minimum: int = 0,
    maximum: int = MAX_LANE_SLOTS,
) -> int:
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
        token = str(lane or "")
        if token == LANE_COVERAGE:
            return int(self.coverage_slots)
        if token == LANE_COMPLETION:
            return int(self.completion_slots)
        if token == LANE_REALIZATION:
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
        return (
            int(self.coverage_slots)
            + int(self.completion_slots)
            + int(self.realization_slots)
        )

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
    economics_ok: bool = True
    entry_feasible: bool = True
    # V4.11: qualification/refresh pressure must be visible before deep ranking.
    needs_refresh: bool = False
    refresh_urgency: float = 0.0
    # V4.12.8: exact rolling-progress deadline.  Unlike refresh_urgency this
    # also applies to ONE_AWAY/TWO_AWAY books whose existing observations can
    # expire before the next completion.
    deadline_urgency: float = 0.0
    deadline_critical: bool = False
    time_to_deadline_ns: int | None = None
    cohort_member: bool = False
    score_qualified: bool = False
    kappa_eligible: bool = False
    pnl_confidence: str = "FULL"
    pnl_confidence_mult: float = 1.0
    # V4.12.18 Kappa flywheel/migration state. ``recent_realized_pnl`` is retained as a
    # compatibility field but now carries authoritative rolling realized PnL
    # when the caller has that evidence.
    score_pnl_ready: bool = True
    recent_realized_pnl: float = 0.0
    rolling_observation_count: int = 0
    rolling_positive_count: int = 0
    rolling_negative_count: int = 0
    rolling_downside_m3: float = 0.0
    raw_kappa: float | None = None
    density_state: str = ""
    density_due: bool = False
    flywheel_priority: float = 0.0
    maker_fee_bps: float = 0.0
    # V4.13 simplified Kappa-productivity authority.  These are cheap scheduler
    # outputs; they never replace the risk/contract/exit authorities.
    kappa_productivity_score: float = 0.0
    kappa_productivity_tier: str = "UNKNOWN"
    kappa_productivity_state: str = "NEW"
    core_candidate: bool = False
    recycling_candidate: bool = False
    # V4.13.2: one qualified-but-fresh-UNKNOWN book may receive a single
    # minimum-size Maker probe so restored/good books can earn fresh evidence.
    core_probe_candidate: bool = False
    placements_per_rt: float = 0.0
    maker_fill_conversion: float = 0.0
    contract_reject_rate: float = 0.0




def apply_breadth_rotation_gate(
    books: Iterable[LaneBook],
    *,
    enabled: bool = True,
    min_productive_incomplete: int = 1,
) -> tuple[list[LaneBook], set[int], int]:
    """Suppress stable score-qualified *new acquisition* while progress exists.

    This is a rotation gate, not a risk gate. Existing inventory/dust/hard-risk
    books are never suppressed.  V4.12.8 allows score-qualified refresh to bypass
    the gate only at a critical rolling deadline; early warning-window maintenance
    is suppressed while productive incomplete books are waiting. The goal is to
    recycle scarce acquisition slots into
    ONE_AWAY/TWO_AWAY books instead of repeatedly reopening already-qualified books.
    """
    rows = list(books)
    productive = [
        row for row in rows
        if bool(getattr(row, "entry_feasible", True))
        and bool(getattr(row, "economics_ok", True))
        and not bool(getattr(row, "has_inventory", False))
        and not bool(getattr(row, "is_dust", False))
        and not bool(getattr(row, "is_hard_risk", False))
        and max(0, int(getattr(row, "observations_remaining", 0) or 0)) in {1, 2}
    ]
    productive_count = len(productive)
    threshold = max(1, int(min_productive_incomplete or 1))
    if not enabled or productive_count < threshold:
        return rows, set(), productive_count

    suppressed: set[int] = set()
    out: list[LaneBook] = []
    for row in rows:
        # V4.12.8: a broad warning horizon must not turn every qualified book
        # into maintenance traffic while incomplete books are waiting.  Only a
        # *critical* rolling deadline can bypass breadth rotation.
        qualified = bool(getattr(row, "score_qualified", False))
        critical_refresh = bool(
            getattr(row, "needs_refresh", False)
            and getattr(row, "deadline_critical", False)
        )
        block = bool(
            qualified
            and not bool(getattr(row, "density_due", False))
            and not critical_refresh
            and not bool(getattr(row, "has_inventory", False))
            and not bool(getattr(row, "is_dust", False))
            and not bool(getattr(row, "is_hard_risk", False))
        )
        if block:
            suppressed.add(int(row.book_id))
            out.append(replace(row, entry_feasible=False))
        else:
            out.append(row)
    return out, suppressed, productive_count


def apply_kappa_conversion_pressure_gate(
    books: Iterable[LaneBook],
    *,
    parked_open_books: int,
    max_parked_open_books: int,
    total_open_books: int,
    max_total_open_books: int,
    reserve_total_slots: int = 3,
    exploration_slots: int = 1,
    enabled: bool = True,
) -> tuple[list[LaneBook], set[int], int, str]:
    """Reserve scarce *total exposure* for Kappa conversion, never park labels.

    V4.12.16 incorrectly treated ``max_parked_open_books`` as a second scarce
    resource and could suppress all fresh breadth once six books were merely
    classified PARKED.  V4.12.17 makes parking a state classification.  Only
    real total-position headroom creates pressure, and at least one productive
    exploration/coverage book remains fail-open while capacity exists.

    The parked arguments remain in the signature for backward compatibility and
    telemetry, but they do not gate acquisition.
    """
    rows = list(books)
    productive = [
        row for row in rows
        if bool(getattr(row, "entry_feasible", True))
        and bool(getattr(row, "economics_ok", True))
        and not bool(getattr(row, "has_inventory", False))
        and not bool(getattr(row, "is_dust", False))
        and not bool(getattr(row, "is_hard_risk", False))
        and (
            max(0, int(getattr(row, "observations_remaining", 0) or 0)) in {1, 2}
            or bool(getattr(row, "density_due", False))
        )
    ]
    productive_count = len(productive)
    if not enabled or productive_count <= 0:
        return rows, set(), productive_count, "DISABLED_OR_NO_PROGRESS"

    total_open = max(0, int(total_open_books or 0))
    total_cap = max(0, int(max_total_open_books or 0))
    headroom = max(0, total_cap - total_open)
    reserve = max(1, int(reserve_total_slots or 1))
    headroom_tight = total_cap > 0 and headroom <= reserve
    if not headroom_tight:
        return rows, set(), productive_count, "NO_PRESSURE"

    # Preserve an exploration escape slot. Pick the best fresh coverage rows by
    # their normal coverage ranking instead of making the scheduler permanently
    # depend on an unfillable ONE_AWAY/TWO_AWAY backlog.
    fresh = [
        row for row in rows
        if max(0, int(getattr(row, "observations_remaining", 0) or 0)) >= 3
        and bool(getattr(row, "entry_feasible", True))
        and bool(getattr(row, "economics_ok", True))
        and not bool(getattr(row, "has_inventory", False))
        and not bool(getattr(row, "is_dust", False))
        and not bool(getattr(row, "is_hard_risk", False))
    ]
    keep_n = max(1, int(exploration_slots or 1)) if headroom > 0 else 0
    # V4.13.2: CORE_PROBE is the single bootstrap escape hatch for qualified
    # UNKNOWN books. Preserve that probe under tight-but-nonzero total headroom;
    # fill any remaining exploration slots with normal coverage ranking.
    probe_ids = [
        int(row.book_id) for row in fresh
        if bool(getattr(row, "core_probe_candidate", False))
    ]
    keep_ids: set[int] = set(probe_ids[:1]) if headroom > 0 else set()
    for row in sorted(fresh, key=coverage_sort_key):
        if len(keep_ids) >= keep_n:
            break
        keep_ids.add(int(row.book_id))
    suppressed: set[int] = set()
    out: list[LaneBook] = []
    for row in rows:
        remaining = max(0, int(getattr(row, "observations_remaining", 0) or 0))
        block = bool(
            remaining >= 3
            and int(row.book_id) not in keep_ids
            and bool(getattr(row, "entry_feasible", True))
            and not bool(getattr(row, "has_inventory", False))
            and not bool(getattr(row, "is_dust", False))
            and not bool(getattr(row, "is_hard_risk", False))
        )
        if block:
            suppressed.add(int(row.book_id))
            out.append(replace(row, entry_feasible=False))
        else:
            out.append(row)
    return out, suppressed, productive_count, "TOTAL_HEADROOM"


def classify_execution_lane(book: LaneBook) -> str:
    """One book belongs to exactly one lane."""
    if book.has_inventory or book.is_dust:
        return LANE_REALIZATION
    remaining = max(0, int(book.observations_remaining or 0))
    if bool(book.needs_refresh) and bool(book.economics_ok):
        return LANE_COMPLETION
    if (
        bool(getattr(book, "core_candidate", False))
        or bool(getattr(book, "recycling_candidate", False))
        or bool(getattr(book, "core_probe_candidate", False))
        or bool(getattr(book, "density_due", False))
    ) and bool(book.economics_ok):
        return LANE_COMPLETION
    if remaining in {1, 2} and bool(book.economics_ok):
        return LANE_COMPLETION
    return LANE_COVERAGE


def completion_sort_key(book: LaneBook) -> tuple:
    """Kappa completion + density ordered by urgency *and* productivity.

    V4.13 keeps one simple principle: a productive qualified book may continue
    cycling, while an order-hungry book is demoted even when it is ONE_AWAY.
    Critical rolling deadlines remain absolute completion priority.
    """
    remaining = max(0, int(book.observations_remaining or 0))
    deadline = max(
        0.0, min(1.0, _finite(getattr(book, "deadline_urgency", 0.0)))
    )
    critical = bool(getattr(book, "deadline_critical", False))
    refresh = bool(getattr(book, "needs_refresh", False))
    density_due = bool(getattr(book, "density_due", False))
    core = bool(getattr(book, "core_candidate", False))
    recycling = bool(getattr(book, "recycling_candidate", False))
    core_probe = bool(getattr(book, "core_probe_candidate", False))

    # Recycling/bootstrap probes compete with ONE_AWAY rather than waiting behind
    # every incomplete book. Full CORE remains the stronger persistent state.
    # incomplete book.  This is the Book115 fix.
    if remaining == 1:
        completion_cost = 1
        type_rank = 0
    elif core_probe:
        completion_cost = 1
        type_rank = 1
    elif recycling:
        completion_cost = 1
        type_rank = 2
    elif core:
        completion_cost = 1
        type_rank = 3
    elif refresh:
        completion_cost = 1
        type_rank = 4
    elif remaining == 2:
        completion_cost = 2
        type_rank = 5
    elif density_due:
        completion_cost = 2
        type_rank = 6
    else:
        completion_cost = 4
        type_rank = 7

    tier = str(getattr(book, "kappa_productivity_tier", "UNKNOWN") or "UNKNOWN").upper()
    efficiency_rank = 0 if tier == "PRODUCTIVE" else (1 if tier == "UNKNOWN" else 2)
    productivity = _finite(getattr(book, "kappa_productivity_score", 0.0))
    score_pnl_rank = 0 if bool(getattr(book, "score_pnl_ready", True)) else 1
    recent_pnl = _finite(getattr(book, "recent_realized_pnl", 0.0))
    maker_fee = _finite(getattr(book, "maker_fee_bps", 0.0))
    return (
        0 if critical else 1,
        completion_cost,
        0 if critical else efficiency_rank,
        0 if critical else type_rank,
        -deadline,
        -productivity,
        score_pnl_rank,
        -recent_pnl,
        maker_fee,
        0 if book.cohort_member else 1,
        -_finite(book.cheap_score),
        -_finite(book.maker_ev),
        int(book.book_id),
    )


def realization_sort_key(book: LaneBook) -> tuple:
    """Open inventory ordered by ExitUrgency; hard-risk first."""
    hard = 0 if book.is_hard_risk else 1
    return (hard, -_finite(book.exit_urgency), int(book.book_id))


def coverage_sort_key(book: LaneBook) -> tuple:
    """Breadth rotation with execution-efficiency demotion.

    Unknown new books retain an exploration path; known Book98-like order sinks
    lose priority instead of monopolizing the coverage lane.
    """
    maker_ev = _finite(book.maker_ev)
    known = bool(book.maker_ev_known) or abs(maker_ev) > 1e-15
    positive = bool(book.economics_ok and known and maker_ev > 0.0)
    if positive and book.is_uncovered:
        tier = 0
    elif positive and book.is_stale:
        tier = 1
    elif positive and book.is_inactive:
        tier = 2
    elif positive:
        tier = 3
    elif (not known) and book.economics_ok and book.is_uncovered:
        tier = 4
    elif (not known) and book.economics_ok and (book.is_stale or book.is_inactive):
        tier = 5
    else:
        tier = 6
    prod_tier = str(getattr(book, "kappa_productivity_tier", "UNKNOWN") or "UNKNOWN").upper()
    efficiency_rank = 0 if prod_tier == "PRODUCTIVE" else (1 if prod_tier == "UNKNOWN" else 2)
    productivity = _finite(getattr(book, "kappa_productivity_score", 0.0))
    return (
        efficiency_rank,
        0 if book.cohort_member else 1,
        tier,
        -productivity,
        -maker_ev,
        _finite(getattr(book, "maker_fee_bps", 0.0)),
        -_finite(book.cheap_score),
        int(book.book_id),
    )


def lane_sort_key(lane: str, book: LaneBook) -> tuple:
    token = str(lane or "")
    if token == LANE_REALIZATION:
        return realization_sort_key(book)
    if token == LANE_COMPLETION:
        return completion_sort_key(book)
    return coverage_sort_key(book)


def allocate_lane_slots(
    demand: dict[str, int],
    budgets: LaneBudgets,
) -> dict[str, Any]:
    """Reserve each lane, then spill unused reserved + overflow."""
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
    *,
    lane: str,
    used: dict[str, int] | None,
    overflow_used: int,
    budgets: LaneBudgets,
) -> tuple[bool, str | None]:
    """Sequential quote/manage admission using reserved slots then overflow."""
    token = str(lane or "")
    if token not in LANES:
        return False, "UNKNOWN_LANE"
    taken = max(0, int((used or {}).get(token, 0) or 0))
    reserved = budgets.reserved(token)
    if taken < reserved:
        return True, None
    if max(0, int(overflow_used)) < int(budgets.shared_overflow_slots):
        return True, None
    return False, f"{token}_SLOT_CAP"


@dataclass
class LaneAllocation:
    selected: list[int] = field(default_factory=list)
    by_lane: dict[str, list[int]] = field(default_factory=dict)
    demand: dict[str, int] = field(default_factory=dict)
    reserved: dict[str, int] = field(default_factory=dict)
    used: dict[str, int] = field(default_factory=dict)
    spilled: dict[str, int] = field(default_factory=dict)
    unused_reserved: dict[str, int] = field(default_factory=dict)
    overflow_used: int = 0
    budgets: LaneBudgets = field(default_factory=LaneBudgets)

    def as_log(self) -> dict[str, int]:
        used = self.used
        demand = self.demand
        spilled = self.spilled
        return {
            **self.budgets.as_dict(),
            "coverage_demand": int(demand.get(LANE_COVERAGE, 0)),
            "completion_demand": int(demand.get(LANE_COMPLETION, 0)),
            "realization_demand": int(demand.get(LANE_REALIZATION, 0)),
            "coverage_used": int(used.get(LANE_COVERAGE, 0)),
            "completion_used": int(used.get(LANE_COMPLETION, 0)),
            "realization_used": int(used.get(LANE_REALIZATION, 0)),
            "coverage_spilled": int(spilled.get(LANE_COVERAGE, 0)),
            "completion_spilled": int(spilled.get(LANE_COMPLETION, 0)),
            "realization_spilled": int(spilled.get(LANE_REALIZATION, 0)),
            "overflow_used": int(self.overflow_used),
            "selected_count": len(self.selected),
        }


def select_lane_candidates(
    books: Iterable[LaneBook],
    budgets: LaneBudgets | None = None,
    *,
    max_candidates: int | None = None,
) -> LaneAllocation:
    """Pick candidates by lane budgets with an optional hard global cap.

    V4.10 let the sum of lane reserves override ``research_candidate_count``.
    V4.11 makes the global cap authoritative while preserving priority:
    REALIZATION -> KAPPA_COMPLETION -> COVERAGE.
    """
    cfg = budgets or LaneBudgets()
    pools: dict[str, list[LaneBook]] = {lane: [] for lane in LANES}
    seen: set[int] = set()
    for book in books:
        bid = int(book.book_id)
        if bid in seen:
            continue
        seen.add(bid)
        # V4.10: a recent hard minimum-order admission failure should not
        # consume COVERAGE/COMPLETION candidate capacity. Inventory/risk books
        # remain eligible because exits must never be suppressed by entry sizing.
        if (
            not bool(getattr(book, "entry_feasible", True))
            and not book.has_inventory
            and not book.is_dust
            and not book.is_hard_risk
        ):
            continue
        pools[classify_execution_lane(book)].append(book)
    for lane in LANES:
        pools[lane].sort(key=lambda row: lane_sort_key(lane, row))
    demand = {lane: len(pools[lane]) for lane in LANES}
    plan = allocate_lane_slots(demand, cfg)
    granted: dict[str, int] = plan["granted"]
    selected: list[int] = []
    by_lane: dict[str, list[int]] = {lane: [] for lane in LANES}
    remaining_cap = None if max_candidates is None else max(0, int(max_candidates))
    for lane in (LANE_REALIZATION, LANE_COMPLETION, LANE_COVERAGE):
        take = max(0, int(granted.get(lane, 0) or 0))
        if remaining_cap is not None:
            take = min(take, remaining_cap)
        chosen_rows = list(pools[lane][:take])
        if lane == LANE_COMPLETION and take > 0:
            # V4.13.2 reserves exactly one CORE_PROBE slot before the existing
            # recycling bridge. A probe is a qualified/eligible fresh-UNKNOWN
            # book selected by Strategy1_Research; it gets one minimum-size Maker
            # cycle to earn evidence. Existing recycling remains the second
            # bootstrap priority when completion capacity permits.
            probe = next(
                (row for row in pools[lane] if bool(getattr(row, "core_probe_candidate", False))),
                None,
            )
            bridge = next(
                (row for row in pools[lane] if bool(getattr(row, "recycling_candidate", False))),
                None,
            )
            specials = []
            if probe is not None:
                specials.append(probe)
            if bridge is not None and (probe is None or int(bridge.book_id) != int(probe.book_id)):
                specials.append(bridge)
            if specials:
                special_ids = {int(row.book_id) for row in specials}
                chosen_rows = specials[:take] + [
                    row for row in chosen_rows
                    if int(row.book_id) not in special_ids
                ][: max(0, take - min(take, len(specials)))]
        chosen = [int(row.book_id) for row in chosen_rows]
        by_lane[lane] = chosen
        selected.extend(chosen)
        if remaining_cap is not None:
            remaining_cap = max(0, remaining_cap - len(chosen))
    return LaneAllocation(
        selected=selected,
        by_lane=by_lane,
        demand=demand,
        reserved={lane: cfg.reserved(lane) for lane in LANES},
        used={lane: len(by_lane[lane]) for lane in LANES},
        spilled=dict(plan["spilled"]),
        unused_reserved=dict(plan["unused_reserved"]),
        overflow_used=int(plan["overflow_used"]),
        budgets=cfg,
    )
