# SPDX-License-Identifier: MIT
"""A1.9.5 step 2 (F3): parked dust is its own capacity class.

A1.6.1 already excused parked dust from the open-BOOK count
(``dust_exempt_count`` / ``effective_total_open_books``).  It never excused it
from the aggregate absolute-BASE budget, and that budget is what actually
closed the agent.  Measured at tick 10,000 of the A1.9.4 run::

    max_abs         2.0000     (= max_total_open_books * mm_base_size)
    total_abs       1.5758
    parked dust     1.3257     <- 84.1% of everything held
    reserve         0.2500     (one recovery clip, because dust_count > 0)
    -> abs_headroom 0.1742  ->  abs_slots = floor(0.1742/0.25) = 0

Book slots were fine at the same moment: active 1/6, open 1/8.  ``abs_slots``
alone drove ``portfolio_slots`` to zero, which empties the candidate set, which
is why the COMPLETION lane ranked 2,961 books and selected none.

Parked dust is sub-``min_order`` residue that the Kappa loss floor correctly
refuses to realize (16,959 blocks against 157 allows), so it is effectively
permanent.  Charging permanent, unexitable residue against the ACQUISITION
budget makes productive capacity decay monotonically to zero -- it is a
misclassification, not a risk control.  It is also barely directional: across
the books still reporting at tick ~10,044 the dust summed to -0.0085 signed
against 0.8319 absolute, i.e. 1.0% net.

So dust gets its own class, with its own ceiling.  Two properties matter:

* **Bounded.**  The exemption is capped, so dust can never buy unlimited
  exposure.  The cap is needed: ``_research_final_validate_instructions``
  already raises the book cap by up to ``DIRECT_DUST_EXEMPT_CAP`` (8), so the
  structural bound on dust is 16 books * min_order, not 8.
* **Graceful.**  Past the ceiling the overflow counts against the productive
  budget again, tightening entry progressively rather than at a cliff, which
  is the pressure that should eventually be relieved by an explicit purge.

Nothing here decides anything; it returns numbers.  The caller owns the switch.
"""
from __future__ import annotations

import math

A195_DUST_CAPACITY_VERSION = "direct_dust_capacity_v4_16_2_a1_9_5"

# Dust ceiling in ``min_order`` units: one clip per total book slot.  Combined
# with the `min(..., max_abs)` term in `dust_class_ceiling_abs`, worst-case
# aggregate exposure is at most twice the productive cap, never more.
A195_DUST_CLASS_MAX_CLIPS = 8.0


def _finite(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def dust_class_ceiling_abs(
    *,
    min_order: float,
    max_abs: float,
    max_clips: float = A195_DUST_CLASS_MAX_CLIPS,
) -> float:
    """Most parked BASE the dust class may ever excuse.

    Bounded twice over: by one clip per book slot, and by the productive cap
    itself, so total exposure cannot exceed ``2 * max_abs``.
    """
    clips = max(0.0, _finite(max_clips)) * max(1e-12, _finite(min_order, 0.25))
    return max(0.0, min(clips, max(0.0, _finite(max_abs))))


def dust_class_exempt_abs(
    *,
    dust_abs: float,
    min_order: float,
    max_abs: float,
    max_clips: float = A195_DUST_CLASS_MAX_CLIPS,
) -> float:
    """Parked-dust BASE excused from the productive acquisition budget."""
    ceiling = dust_class_ceiling_abs(
        min_order=min_order, max_abs=max_abs, max_clips=max_clips,
    )
    return min(max(0.0, _finite(dust_abs)), ceiling)


def productive_abs_base(
    *,
    total_abs: float,
    dust_abs: float,
    min_order: float,
    max_abs: float,
    max_clips: float = A195_DUST_CLASS_MAX_CLIPS,
) -> float:
    """Aggregate absolute BASE that should be charged to new acquisition.

    Overflow past the dust ceiling stays charged, so the budget tightens
    progressively once dust exceeds its class.
    """
    total = max(0.0, _finite(total_abs))
    exempt = dust_class_exempt_abs(
        dust_abs=min(max(0.0, _finite(dust_abs)), total),
        min_order=min_order, max_abs=max_abs, max_clips=max_clips,
    )
    return max(0.0, total - exempt)


def dust_capacity_report(
    *,
    total_abs: float,
    dust_abs: float,
    min_order: float,
    max_abs: float,
    max_clips: float = A195_DUST_CLASS_MAX_CLIPS,
) -> dict:
    """Telemetry payload for one admission decision."""
    total = max(0.0, _finite(total_abs))
    dust = min(max(0.0, _finite(dust_abs)), total)
    ceiling = dust_class_ceiling_abs(
        min_order=min_order, max_abs=max_abs, max_clips=max_clips,
    )
    exempt = min(dust, ceiling)
    return {
        "a195_dust_capacity_version": A195_DUST_CAPACITY_VERSION,
        "total_abs_base": total,
        "dust_abs_base": dust,
        "dust_class_ceiling_abs": ceiling,
        "dust_class_exempt_abs": exempt,
        # What still counts against acquisition once the class is applied.
        "dust_class_overflow_abs": max(0.0, dust - ceiling),
        "productive_abs_base": max(0.0, total - exempt),
        "dust_class_headroom_abs": max(0.0, ceiling - dust),
    }
