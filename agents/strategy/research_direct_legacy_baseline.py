# SPDX-License-Identifier: MIT
"""A1.9.6: legacy dust as a baseline, inherited parked capacity, quantity grid.

A1.9.5 step 3 made inventory true and broke throughput.  Measured on log
20260912_203501 (2,060 ticks) against the 4,688-tick steps-1+2 run:

    completed round trips          442  ->   10  (last new one at tick 247)
    RANK rows                   11,771  ->   39
    admission slots == 0         82 of 87 samples
    non-FLAT books in tracker     ~10  ->  113  (entry skips every one)

Three mechanisms, each measured, each fixed here.  All three are STRUCTURAL;
none is a threshold fitted to that log.

F9 -- LEGACY DUST IS A BASELINE, NOT A POSITION.  The seed wrote 109 books of
sub-minimum legacy dust into the position tracker.  The entry builder skips
every book whose inventory band is not FLAT, so all 109 left the enterable
universe -- and that universe is where the agent trades: 397 of the 442 round
trips of the steps-1+2 run (89.8%, on 75 of its 84 books) were on books that
carry legacy dust today.  Dust now goes to a ledger.  The ledger counts for
exposure, for the F3 exemption and for reconciliation, so nothing is hidden;
the tracker -- and with it FLAT, entry eligibility and round-trip accounting --
sees only the lifecycle the agent is actually running on top of the residue.

F10 -- INHERITED PARKED EXPOSURE.  A REAL inherited lot the loss floor refuses
to realize is parked indefinitely, and was charged in full to acquisition.
Book 39, -0.7139 BASE, parked from tick 3 to the end of the run, was 52% of the
final productive BASE and the reason admission read zero.  This is D6 one class
up: a correct refusal allowed to cost capacity.  The allowance covers only
inherited lots, only while parked, never more than was inherited, never across
a flatten or a sign change, and never beyond a ceiling.

F11 -- THE QUANTITY GRID.  `total - initial` is a float subtraction, so 0.35
came back as 0.3499999999999943.  The simulator truncates volume to its grid
(decimal.hpp: `util::round` is `DecimalUtil::trunc`), and the final validator
writes a rounded quantity back only when it differs by more than 1e-12, so the
noise shipped: the exit filled 0.3499 and left 0.0001, and every later 0.25
clip on that book went out as 0.25009999999999827, truncated to 0.2500, and
left the same unit.  22 round trips on 3 books never read FLAT.  Snap to the
grid -- but only across float noise; a genuinely off-grid quantity is never
moved.  Then lift a grid value whose double sits below its decimal by one ulp.
The venue builds a Decimal128 from the double before truncating, and the
library that does it is not vendored here, so whether it keeps the exact binary
expansion (0.2501 -> 0.25009999999999998...) cannot be read from source; the
lift is correct under either conversion, and one ulp is far below every
tolerance on the Python side.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_DOWN, Decimal
import math
from typing import Any, Iterable

from research_direct_inventory_truth import SEED_LEGACY_DUST, SEED_REAL, SeedLot, SeedPlan
from research_direct_liveness import admission_slots, dust_recovery_reserve_abs

A196_LEGACY_BASELINE_VERSION = "direct_legacy_baseline_v4_16_2_a1_9_6"

# Float noise from one subtraction is ~1e-14; one grid unit at 4 decimals is
# 1e-4.  Anything inside this tolerance is representation error, not intent.
A196_SNAP_TOLERANCE = 1e-9

# Inherited parked exposure may not exceed this share of the acquisition cap.
# A risk-policy ceiling, not derived from any sample: it keeps the exempted
# inherited exposure strictly below the budget new positions compete for.
A196_INHERITED_PARKED_MAX_FRACTION = 0.5

A196_MAX_DECIMALS = 12

PLACE_ORDER_TYPES = ("PLACE_ORDER_LIMIT", "PLACE_ORDER_MARKET")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _decimals(value: Any) -> int | None:
    try:
        d = int(value)
    except (TypeError, ValueError):
        return None
    return d if 0 <= d <= A196_MAX_DECIMALS else None


# ---- F11: the grid ----------------------------------------------------------

def snap_quantity(quantity: Any, decimals: Any, *, tolerance: float = A196_SNAP_TOLERANCE) -> float:
    """Round onto the venue quantity grid only when the gap is float noise."""
    q = _finite(quantity)
    d = _decimals(decimals)
    if d is None:
        return q
    snapped = round(q, d)
    if abs(q - snapped) <= abs(_finite(tolerance, A196_SNAP_TOLERANCE)):
        return float(snapped) + 0.0          # + 0.0 normalises -0.0
    return q


def wire_quantity(quantity: Any, decimals: Any, *, tolerance: float = A196_SNAP_TOLERANCE) -> float:
    """The double to send so the venue's truncation lands on the intended unit."""
    q = snap_quantity(quantity, decimals, tolerance=tolerance)
    d = _decimals(decimals)
    if d is None or q <= 0.0 or round(q, d) != q:
        return q                              # off the grid: truncates as before
    target = Decimal(format(q, f".{d}f"))
    if Decimal(q) < target:
        lifted = math.nextafter(q, math.inf)
        if Decimal(lifted) >= target:
            return lifted
    return q


def venue_executed_quantity(quantity: Any, decimals: Any, *, exact_binary: bool = True) -> float:
    """Model of what the simulator books: a Decimal128 from the double, truncated.

    Both candidate conversions are modelled -- the exact binary expansion and
    the shortest round-trip decimal -- because the library is not vendored.
    """
    q = _finite(quantity)
    d = _decimals(decimals)
    if d is None:
        return q
    value = Decimal(q) if exact_binary else Decimal(repr(q))
    return float(value.quantize(Decimal(1).scaleb(-d), rounding=ROUND_DOWN))


def snap_instruction_quantities(
    instructions: Iterable[Any], decimals: Any, *, tolerance: float = A196_SNAP_TOLERANCE,
) -> int:
    """Put queued order quantities on the wire grid, in place.  Returns how many moved."""
    moved = 0
    for instruction in list(instructions or ()):
        is_dict = isinstance(instruction, dict)
        kind = instruction.get("type") if is_dict else getattr(instruction, "type", "")
        if str(kind or "").upper() not in PLACE_ORDER_TYPES:
            continue
        raw = instruction.get("quantity") if is_dict else getattr(instruction, "quantity", None)
        if raw is None:
            continue
        wired = wire_quantity(raw, decimals, tolerance=tolerance)
        if wired <= 0.0 or wired == _finite(raw):
            continue
        try:
            if is_dict:
                instruction["quantity"] = wired
            else:
                instruction.quantity = wired
        except Exception:
            continue
        moved += 1
    return moved


# ---- F9: route the seed -----------------------------------------------------

@dataclass(frozen=True)
class SeedSplit:
    tracker_lots: tuple[SeedLot, ...]
    ledger: dict[int, float]
    inherited_real: dict[int, float]
    snapped_lots: int
    reclassified_lots: int
    legacy_dust_abs: float
    ledger_enabled: bool
    grid_snap: bool

    @property
    def ledger_abs(self) -> float:
        return ledger_abs(self.ledger)

    @property
    def inherited_abs(self) -> float:
        return sum(abs(v) for v in self.inherited_real.values())

    def as_log(self) -> dict[str, Any]:
        return {
            "a196_legacy_baseline_version": A196_LEGACY_BASELINE_VERSION,
            "a196_ledger_enabled": int(self.ledger_enabled),
            "a196_grid_snap": int(self.grid_snap),
            "a196_tracker_lots": len(self.tracker_lots),
            "a196_ledger_books": len(self.ledger),
            "a196_ledger_abs": round(self.ledger_abs, 6),
            "a196_legacy_dust_abs": round(self.legacy_dust_abs, 6),
            "a196_inherited_real_books": len(self.inherited_real),
            "a196_inherited_real_abs": round(self.inherited_abs, 6),
            "a196_snapped_lots": int(self.snapped_lots),
            "a196_reclassified_lots": int(self.reclassified_lots),
        }


def split_seed_plan(
    plan: SeedPlan,
    *,
    volume_decimals: Any,
    min_order: Any = None,
    ledger_enabled: bool = True,
    grid_snap: bool = True,
) -> SeedSplit:
    """REAL lots to the tracker, legacy dust to the ledger, all on the grid.

    With the ledger disabled every lot goes to the tracker, which is exactly
    the A1.9.5 behaviour -- the switch is a clean rollback, not a new mode.
    With the grid on, the REAL/DUST boundary is re-read on the grid value:
    0.24999999999999994 is an exitable 0.25, not dust.
    """
    floor = None if min_order is None else max(1e-12, abs(_finite(min_order, 0.25)))
    tracker: list[SeedLot] = []
    ledger: dict[int, float] = {}
    inherited: dict[int, float] = {}
    snapped = reclassified = 0
    dust_abs = 0.0
    for lot in plan.lots:
        net = snap_quantity(lot.net_base, volume_decimals) if grid_snap else _finite(lot.net_base)
        if net != lot.net_base:
            snapped += 1
        if net == 0.0:
            continue                          # it was only ever float noise
        residue = lot.residue_class
        if grid_snap and floor is not None:
            on_grid = SEED_REAL if abs(net) >= floor else SEED_LEGACY_DUST
            if on_grid != residue:
                reclassified += 1
                residue = on_grid
        fixed = replace(lot, net_base=net, residue_class=residue)
        if residue == SEED_REAL:
            tracker.append(fixed)
            inherited[int(lot.book_id)] = net
            continue
        dust_abs += abs(net)
        if ledger_enabled:
            ledger[int(lot.book_id)] = net
        else:
            tracker.append(fixed)
    return SeedSplit(
        tracker_lots=tuple(tracker), ledger=ledger, inherited_real=inherited,
        snapped_lots=snapped, reclassified_lots=reclassified, legacy_dust_abs=dust_abs,
        ledger_enabled=bool(ledger_enabled), grid_snap=bool(grid_snap),
    )


def ledger_abs(ledger: dict[int, float] | None) -> float:
    return sum(abs(_finite(v)) for v in (ledger or {}).values())


# ---- F10: inherited parked allowance ----------------------------------------

@dataclass(frozen=True)
class InheritedExemption:
    exempt_abs: float
    uncapped_abs: float
    books: tuple[int, ...]
    retired: tuple[int, ...]
    capped: bool


NO_INHERITED_EXEMPTION = InheritedExemption(
    exempt_abs=0.0, uncapped_abs=0.0, books=(), retired=(), capped=False,
)


def inherited_parked_exemption(
    *,
    inherited_real: dict[int, float],
    net_by_book: dict[int, float],
    parked_books: Iterable[int],
    cap_abs: float,
    eps: float,
) -> InheritedExemption:
    """Acquisition BASE excused for inherited lots the loss floor has parked.

    A book is RETIRED -- permanently, the caller drops it -- once its lifecycle
    ends: it goes flat, or it crosses to the other side.  A book with no net
    reading is left alone rather than retired on missing data.  Session
    add-ons are never excused: only ``min(|net|, |inherited|)`` counts.
    """
    tol = abs(_finite(eps, 5e-5))
    parked = {int(b) for b in (parked_books or ())}
    total = 0.0
    books: list[int] = []
    retired: list[int] = []
    for raw, seeded in (inherited_real or {}).items():
        book = int(raw)
        if book not in net_by_book:
            continue
        net = _finite(net_by_book.get(book))
        seeded = _finite(seeded)
        if abs(net) <= tol or net * seeded < 0.0:
            retired.append(book)
            continue
        if book not in parked:
            continue
        total += min(abs(net), abs(seeded))
        books.append(book)
    cap = max(0.0, _finite(cap_abs))
    return InheritedExemption(
        exempt_abs=min(total, cap), uncapped_abs=total,
        books=tuple(books), retired=tuple(retired), capped=total > cap + 1e-12,
    )


# ---- the admission decomposition: every term, so a zero is read, not inferred --

@dataclass(frozen=True)
class AdmissionDecomposition:
    raw_total_abs: float
    ledger_abs: float
    dust_abs: float
    dust_exempt_abs: float
    inherited_exempt_abs: float
    reserved_abs: float
    effective_abs: float
    recovery_reserve_abs: float
    abs_headroom: float
    abs_slots: int
    active_books: int
    active_slots: int
    effective_open_books: int
    open_slots: int
    portfolio_slots: int
    binding: str
    enterable_books: int

    def as_log(self) -> dict[str, Any]:
        out: dict[str, Any] = {"a196_legacy_baseline_version": A196_LEGACY_BASELINE_VERSION}
        for key, value in self.__dict__.items():
            out[key] = round(value, 6) if isinstance(value, float) else value
        return out


def admission_decomposition(
    *,
    raw_total_abs: float,
    ledger_abs: float,
    dust_abs: float,
    dust_exempt_abs: float,
    inherited_exempt_abs: float,
    reserved_abs: float,
    active_books: int,
    effective_open_books: int,
    dust_count: int,
    max_abs: float,
    max_active: int,
    max_open: int,
    min_order: float,
    enterable_books: int = -1,
) -> AdmissionDecomposition:
    """Every term of ``admission_slots``, so a zero is never an inference again.

    ``portfolio_slots`` is taken from ``admission_slots`` itself, not
    recomputed, so this report cannot drift from the gate it describes.
    """
    clip = max(1e-12, _finite(min_order, 0.25))
    # Same float operations in the same order as the gate in
    # build_mm_strategy_instructions, so the slot count is bit-identical.
    effective = _finite(raw_total_abs) - _finite(dust_exempt_abs) + _finite(reserved_abs)
    effective -= _finite(inherited_exempt_abs)
    reserve = dust_recovery_reserve_abs(dust_count=int(dust_count), min_order=clip)
    headroom = _finite(max_abs) - effective - reserve
    abs_slots = max(0, int(math.floor((headroom + 1e-12) / clip)))
    active_reserve = 1 if int(dust_count) > 0 else 0
    active_slots = max(0, int(max_active) - int(active_books) - active_reserve)
    open_slots = max(0, int(max_open) - int(effective_open_books) - active_reserve)
    portfolio = admission_slots(
        effective_abs=effective, active_books=int(active_books),
        effective_open_books=int(effective_open_books), dust_count=int(dust_count),
        max_abs=_finite(max_abs), max_active=int(max_active), max_open=int(max_open),
        min_order=clip,
    )
    tightest = min(abs_slots, active_slots, open_slots)
    binding = next(
        name for name, slots in (("ABS", abs_slots), ("ACTIVE", active_slots), ("OPEN", open_slots))
        if slots == tightest
    )
    return AdmissionDecomposition(
        raw_total_abs=_finite(raw_total_abs), ledger_abs=_finite(ledger_abs),
        dust_abs=_finite(dust_abs), dust_exempt_abs=_finite(dust_exempt_abs),
        inherited_exempt_abs=_finite(inherited_exempt_abs), reserved_abs=_finite(reserved_abs),
        effective_abs=effective, recovery_reserve_abs=reserve, abs_headroom=headroom,
        abs_slots=abs_slots, active_books=int(active_books), active_slots=active_slots,
        effective_open_books=int(effective_open_books), open_slots=open_slots,
        portfolio_slots=int(portfolio), binding=binding, enterable_books=int(enterable_books),
    )
