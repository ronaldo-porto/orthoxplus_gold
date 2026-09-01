# SPDX-License-Identifier: MIT
"""V4.15.2 cheap current-tick feasibility evaluated before lane allocation.

Uses only facts already available on the 128-book screen row. Deep prediction
is not required. Hard safety is unchanged: this layer can only *withdraw*
primary-lane priority from a currently infeasible flat book.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

FRESH_FEASIBILITY_VERSION = "fresh_feasibility_v4_15_2"
DEFAULT_CHEAP_SHORTLIST = 22
MIN_CHEAP_SHORTLIST = 16
MAX_CHEAP_SHORTLIST = 24


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def clamp_cheap_shortlist(value: Any, *, default: int = DEFAULT_CHEAP_SHORTLIST) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        n = int(default)
    return max(MIN_CHEAP_SHORTLIST, min(MAX_CHEAP_SHORTLIST, n))


@dataclass(frozen=True)
class FreshFeasibility:
    feasible: bool
    reason: str
    p_fill: float
    min_order_ok: bool
    headroom_ok: bool
    lifecycle_ok: bool

    def as_log(self) -> dict[str, Any]:
        return {
            "fresh_feasibility_version": FRESH_FEASIBILITY_VERSION,
            "fresh_feasible": int(self.feasible),
            "fresh_feasible_reason": str(self.reason),
            "fresh_p_fill": float(self.p_fill),
            "fresh_min_order_ok": int(self.min_order_ok),
            "fresh_headroom_ok": int(self.headroom_ok),
            "fresh_lifecycle_ok": int(self.lifecycle_ok),
        }


def evaluate_fresh_feasibility(
    *,
    has_inventory: bool = False,
    is_dust: bool = False,
    is_hard_risk: bool = False,
    entry_feasible: bool = True,
    economics_ok: bool = True,
    completion_ev_ok: bool = True,
    p_fill: float | None = None,
    p_actionable: float | None = None,
    min_order: float = 0.25,
    safe_size: float | None = None,
    volume_headroom: float = 1.0,
    inventory_headroom: float = 1.0,
    lifecycle_ev: float | None = None,
    fill_floor: float = 0.04,
) -> FreshFeasibility:
    if has_inventory or is_dust or is_hard_risk:
        return FreshFeasibility(True, "REALIZATION", 1.0, True, True, True)
    p = _clip01(_finite(p_actionable if p_actionable is not None else p_fill, 0.0))
    size_ok = True
    if safe_size is not None:
        size_ok = _finite(safe_size) + 1e-12 >= max(0.0, _finite(min_order)) * 0.15
    headroom_ok = (
        _finite(volume_headroom, 1.0) > 1e-12
        and _finite(inventory_headroom, 1.0) > 1e-12
    )
    life_ok = lifecycle_ev is None or _finite(lifecycle_ev) >= -1e-12
    if not bool(entry_feasible):
        return FreshFeasibility(False, "ENTRY_INFEASIBLE", p, size_ok, headroom_ok, life_ok)
    if not bool(economics_ok) or not bool(completion_ev_ok):
        return FreshFeasibility(False, "ECONOMICS", p, size_ok, headroom_ok, life_ok)
    if not headroom_ok:
        return FreshFeasibility(False, "HEADROOM", p, size_ok, False, life_ok)
    if not size_ok:
        return FreshFeasibility(False, "MIN_ORDER", p, False, headroom_ok, life_ok)
    if p + 1e-15 < max(0.0, float(fill_floor)) and p_fill is not None:
        return FreshFeasibility(False, "FILL_PROB", p, size_ok, headroom_ok, life_ok)
    if not life_ok:
        return FreshFeasibility(False, "LIFECYCLE_EV", p, size_ok, headroom_ok, False)
    return FreshFeasibility(True, "OK", p, size_ok, headroom_ok, life_ok)


def cheap_priority_key(book: Any) -> tuple:
    remaining = max(0, int(getattr(book, "observations_remaining", 0) or 0))
    healthy = getattr(book, "projected_completion_healthy", None)
    health_rank = 0 if healthy is True else (1 if healthy is None else 2)
    completion_cost = 1 if remaining == 1 else (2 if remaining == 2 else 3)
    return (
        0 if bool(getattr(book, "has_inventory", False) or getattr(book, "is_dust", False) or getattr(book, "is_hard_risk", False)) else 1,
        health_rank,
        completion_cost,
        -_finite(getattr(book, "projected_completion_quality", 0.0)),
        -_finite(getattr(book, "lifecycle_ev", 0.0)),
        -_finite(getattr(book, "total_score_value", 0.0)),
        -_finite(getattr(book, "cheap_score", 0.0)),
        int(getattr(book, "book_id", 0) or 0),
    )


def shortlist_fresh_candidates(
    books: Iterable[Any],
    *,
    cheap_shortlist: int = DEFAULT_CHEAP_SHORTLIST,
) -> list[Any]:
    """Keep realization-forced books plus the top cheap-feasible flat books."""
    cap = clamp_cheap_shortlist(cheap_shortlist)
    rows = list(books or [])
    forced = [
        row for row in rows
        if bool(getattr(row, "has_inventory", False)
                or getattr(row, "is_dust", False)
                or getattr(row, "is_hard_risk", False))
    ]
    flat = [row for row in rows if row not in forced]
    feasible_flat = []
    for row in flat:
        remaining = max(0, int(getattr(row, "observations_remaining", 0) or 0))
        healthy = getattr(row, "projected_completion_healthy", None)
        completion_keep = (
            remaining in {1, 2}
            and healthy is not False
            and bool(getattr(row, "economics_ok", True))
            and bool(getattr(row, "completion_ev_ok", True))
        )
        if completion_keep:
            feasible_flat.append(row)
            continue
        if bool(getattr(row, "fresh_feasible", True)) and bool(getattr(row, "entry_feasible", True)):
            feasible_flat.append(row)
    feasible_flat.sort(key=cheap_priority_key)
    room = max(0, cap - len(forced))
    kept_flat = feasible_flat[:room]
    return forced + kept_flat


def reserve_rank_score(book: Any) -> float:
    """fresh executability × lifecycle EV × projected quality × Total Score."""
    feasible = 1.0 if bool(getattr(book, "fresh_feasible", True)) else 0.0
    life = max(0.0, _finite(getattr(book, "lifecycle_ev", 0.0)))
    remaining = max(0, int(getattr(book, "observations_remaining", 0) or 0))
    proj = _finite(getattr(book, "projected_completion_quality", 0.0))
    if remaining not in {1, 2}:
        proj = max(proj, 0.50)
    score = max(0.0, _finite(getattr(book, "total_score_value", 0.0)))
    return feasible * (1.0 + life) * (0.25 + max(0.0, proj)) * (0.25 + score)
