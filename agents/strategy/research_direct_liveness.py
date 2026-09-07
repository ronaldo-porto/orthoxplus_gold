# SPDX-License-Identifier: MIT
"""A1.7.3 partial-fill and portfolio-liveness helpers.

The exchange minimum-order rule creates a one-way failure mode: once a valid
0.25 order partially fills and its legal remainder expires, a sub-minimum
position cannot necessarily be flattened by submitting a fresh order.  These
helpers keep the recovery policy explicit and independently testable.
"""
from __future__ import annotations

from dataclasses import dataclass
import math

DIRECT_LIVENESS_VERSION = "direct_partial_liveness_v4_16_2_a1_7_3"
DIRECT_PARTIAL_HOLD_MIN_NS = 2_500_000_000
DIRECT_PARTIAL_HOLD_PUBLISH_MULT = 3
DIRECT_PARTIAL_HOLD_MAX_NS = 4_000_000_000
DIRECT_DUST_RECOVERY_RESERVE_CLIPS = 1
DIRECT_DUST_RECOVERY_MAX_OVERFLOW_CLIPS = 0.5
DIRECT_DUST_NORMALIZE_MIN_AGE_TICKS = 4
DIRECT_STALE_DUST_NORMALIZE_AGE_TICKS = 24
DIRECT_LIVENESS_TRIGGER_TICKS = 12


@dataclass(frozen=True)
class PartialRecoveryPlan:
    mode: str
    target_inventory: float
    desired_side: str
    preserve_existing_remainder: bool


def is_dust_inventory(qty: float, *, min_order: float, eps: float) -> bool:
    q = abs(float(qty))
    return q > float(eps) and q + 1e-12 < float(min_order)


def recovery_expiry_ns(*, baseline_ns: int, publish_interval_ns: int) -> int:
    """Return a TTL long enough for a partial remainder to survive a publish.

    Ordinary quote management can still cancel/reprice at the next request, so
    extending the hard GTT ceiling does not make stale quotes authoritative.
    """
    baseline = max(1, int(baseline_ns))
    publish = max(0, int(publish_interval_ns))
    desired = max(
        baseline,
        DIRECT_PARTIAL_HOLD_MIN_NS,
        publish * DIRECT_PARTIAL_HOLD_PUBLISH_MULT if publish else 0,
    )
    return min(max(baseline, desired), DIRECT_PARTIAL_HOLD_MAX_NS)


def partial_recovery_plan(
    *, before: float, after: float, min_order: float, eps: float
) -> PartialRecoveryPlan | None:
    """Classify a fill that leaves sub-minimum inventory.

    ENTRY_COMPLETE keeps the legal remainder of the original entry order so the
    position reaches an actionable clip. EXIT_COMPLETE keeps the legal remainder
    of the original reduction so the position reaches flat. If an exit overfills
    through zero into opposite-side dust, the old remainder must *not* be kept;
    it would increase the new opposite exposure, so normalization is required.
    """
    before = float(before)
    after = float(after)
    min_order = float(min_order)
    eps = float(eps)
    if not is_dust_inventory(after, min_order=min_order, eps=eps):
        return None
    if abs(before) <= eps:
        sign = 1.0 if after > 0.0 else -1.0
        return PartialRecoveryPlan(
            mode="ENTRY_COMPLETE",
            target_inventory=sign * min_order,
            desired_side="buy" if sign > 0.0 else "sell",
            preserve_existing_remainder=True,
        )
    same_sign = before * after > 0.0
    reduced = abs(after) + eps < abs(before)
    if same_sign and reduced:
        return PartialRecoveryPlan(
            mode="EXIT_COMPLETE",
            target_inventory=0.0,
            desired_side="sell" if after > 0.0 else "buy",
            preserve_existing_remainder=True,
        )
    # Crossed through flat, or a dust position moved in an unexpected direction.
    # The only safe generic recovery is to normalize the *current* dust sign.
    return PartialRecoveryPlan(
        mode="NORMALIZE",
        target_inventory=(min_order if after > 0.0 else -min_order),
        desired_side="buy" if after > 0.0 else "sell",
        preserve_existing_remainder=False,
    )


def dust_recovery_reserve_abs(*, dust_count: int, min_order: float) -> float:
    if int(dust_count) <= 0:
        return 0.0
    return float(min_order) * DIRECT_DUST_RECOVERY_RESERVE_CLIPS


def admission_slots(
    *,
    effective_abs: float,
    active_books: int,
    effective_open_books: int,
    dust_count: int,
    max_abs: float,
    max_active: int,
    max_open: int,
    min_order: float,
) -> int:
    """New-entry slots after reserving one clip/active slot for dust recovery."""
    min_order = max(1e-12, float(min_order))
    reserve_abs = dust_recovery_reserve_abs(dust_count=dust_count, min_order=min_order)
    abs_headroom = float(max_abs) - float(effective_abs) - reserve_abs
    abs_slots = max(0, int(math.floor((abs_headroom + 1e-12) / min_order)))
    active_reserve = 1 if int(dust_count) > 0 else 0
    active_slots = max(0, int(max_active) - int(active_books) - active_reserve)
    open_slots = max(0, int(max_open) - int(effective_open_books) - active_reserve)
    return max(0, min(abs_slots, active_slots, open_slots))


def normalization_allowed(
    *,
    net: float,
    total_effective_abs: float,
    active_books: int,
    effective_open_books: int,
    max_abs: float,
    max_active: int,
    max_open: int,
    min_order: float,
    eps: float,
    recovery_overflow_abs: float = 0.0,
) -> bool:
    """Whether one same-sign minimum Maker clip can normalize irreducible dust."""
    if not is_dust_inventory(net, min_order=min_order, eps=eps):
        return False
    # The theorem-safe opposite-side compactor already owns dust >= min/2.
    if abs(float(net)) + 1e-12 >= 0.5 * float(min_order):
        return False
    allowed_abs = float(max_abs) + max(0.0, float(recovery_overflow_abs))
    if float(total_effective_abs) + float(min_order) > allowed_abs + 1e-12:
        return False
    if int(active_books) + 1 > int(max_active):
        return False
    if int(effective_open_books) + 1 > int(max_open):
        return False
    return True
