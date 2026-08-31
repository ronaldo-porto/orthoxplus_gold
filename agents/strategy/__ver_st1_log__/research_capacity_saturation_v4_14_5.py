# SPDX-License-Identifier: MIT
"""Concurrency-cap saturation accounting.

``research_rt_phase_timing`` measures the concurrency actually achieved. This
measures what stopped it going higher, which is a different question with a
different answer:

    saturated  -> a cap is binding; more throughput needs a bigger cap
    unsaturated -> the pipeline is starved; raising caps changes nothing

The three caps are independent and are tracked separately, because they imply
different fixes:

    active   research_max_active_open_books   acquisition slots
    total    research_max_total_open_books    all non-flat books, parked included
    exposure research_max_total_abs_base      aggregate absolute base

Dust is tracked alongside them: parked dust releases the *active* slot but still
consumes a *total* slot, so stuck dust silently shrinks the usable book budget.

Pure functions and plain dataclasses so unit tests do not import Strategy1 /
bittensor.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

CAPACITY_SATURATION_VERSION = "capacity_saturation_v1"

CAP_ACTIVE = "ACTIVE_SLOTS"
CAP_TOTAL = "TOTAL_SLOTS"
CAP_EXPOSURE = "ABS_BASE_EXPOSURE"
CAP_NONE = "NONE_STARVED"


def _num(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= 0.0:
        return None
    return numerator / denominator


@dataclass
class CapacitySaturationState:
    ticks: int = 0
    active_saturated: int = 0
    total_saturated: int = 0
    exposure_saturated: int = 0
    any_saturated: int = 0

    sum_active_open: float = 0.0
    sum_total_open: float = 0.0
    sum_dust_open: float = 0.0
    sum_parked_open: float = 0.0
    sum_abs_base: float = 0.0

    max_active_open_books: int = 0
    max_total_open_books: int = 0
    max_total_abs_base: float = 0.0

    def observe(
        self,
        *,
        active_saturated: bool,
        total_saturated: bool,
        exposure_saturated: bool,
        active_open: Any = 0,
        total_open: Any = 0,
        dust_open: Any = 0,
        parked_open: Any = 0,
        abs_base: Any = 0.0,
        max_active_open_books: Any = 0,
        max_total_open_books: Any = 0,
        max_total_abs_base: Any = 0.0,
    ) -> None:
        self.ticks += 1
        if active_saturated:
            self.active_saturated += 1
        if total_saturated:
            self.total_saturated += 1
        if exposure_saturated:
            self.exposure_saturated += 1
        if active_saturated or total_saturated or exposure_saturated:
            self.any_saturated += 1

        self.sum_active_open += max(0.0, _num(active_open))
        self.sum_total_open += max(0.0, _num(total_open))
        self.sum_dust_open += max(0.0, _num(dust_open))
        self.sum_parked_open += max(0.0, _num(parked_open))
        self.sum_abs_base += max(0.0, _num(abs_base))

        # Configured limits are constant per run; last value wins.
        self.max_active_open_books = int(_num(max_active_open_books))
        self.max_total_open_books = int(_num(max_total_open_books))
        self.max_total_abs_base = _num(max_total_abs_base)

    def binding_cap(self) -> str:
        """The cap that binds most often, or NONE_STARVED if none dominates."""
        if self.ticks <= 0:
            return CAP_NONE
        ranked = [
            (self.total_saturated, CAP_TOTAL),
            (self.exposure_saturated, CAP_EXPOSURE),
            (self.active_saturated, CAP_ACTIVE),
        ]
        count, name = max(ranked, key=lambda pair: pair[0])
        # A cap that binds on a minority of ticks is not the constraint; the
        # pipeline simply is not producing enough work to reach it.
        if count * 2 <= self.ticks:
            return CAP_NONE
        return name

    def snapshot(self) -> dict[str, Any]:
        ticks = float(self.ticks)
        mean_total = _ratio(self.sum_total_open, ticks)
        mean_dust = _ratio(self.sum_dust_open, ticks)
        total_cap = float(self.max_total_open_books)

        return {
            "cap_version": CAPACITY_SATURATION_VERSION,
            "cap_ticks": int(self.ticks),
            "cap_active_saturated_rate": _ratio(self.active_saturated, ticks),
            "cap_total_saturated_rate": _ratio(self.total_saturated, ticks),
            "cap_exposure_saturated_rate": _ratio(self.exposure_saturated, ticks),
            "cap_any_saturated_rate": _ratio(self.any_saturated, ticks),
            "cap_binding": self.binding_cap(),
            "cap_mean_active_open": _ratio(self.sum_active_open, ticks),
            "cap_mean_total_open": mean_total,
            "cap_mean_dust_open": mean_dust,
            "cap_mean_parked_open": _ratio(self.sum_parked_open, ticks),
            "cap_mean_abs_base": _ratio(self.sum_abs_base, ticks),
            # Fraction of the hard total-slot budget permanently held by dust.
            "cap_dust_slot_share": (
                mean_dust / total_cap
                if mean_dust is not None and total_cap > 0.0
                else None
            ),
            # Slots idle on average. Large headroom alongside a low saturation
            # rate is the signature of a starved pipeline rather than a cap.
            "cap_total_headroom_mean": (
                total_cap - mean_total
                if mean_total is not None and total_cap > 0.0
                else None
            ),
            "cap_max_active_open_books": int(self.max_active_open_books),
            "cap_max_total_open_books": int(self.max_total_open_books),
            "cap_max_total_abs_base": float(self.max_total_abs_base),
        }
