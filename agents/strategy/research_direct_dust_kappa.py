# SPDX-License-Identifier: MIT
"""A1.7.4.2 Kappa-safe Direct dust-compaction guard.

The A1.7.3.1 theorem-safe moderate-dust compactor guarantees that a full
minimum-size fill cannot increase absolute exposure.  That mechanical theorem
is necessary for liveness, but it is not sufficient for Kappa safety: a stale
residual can be crossed through zero at a catastrophically bad price and lock
in a very large realized loss even though |inventory| decreases.

A1.7.4.2 therefore adds a second, purely economic invariant *only* to the
Direct moderate-dust sign-cross compactor.  It does not change ordinary Maker
exits, A1.7.4 tail recovery, partial-remainder ownership, tiny-dust
normalization, acquisition, or risk bands.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

DIRECT_DUST_KAPPA_VERSION = "direct_dust_kappa_v4_16_2_a1_7_5"

# Observation-driven safety budget.  Recovery Maker authority is already
# bounded around -25/-35 bps.  Dust compaction is allowed a wider concession
# for liveness, but not an unbounded one.  The triggering Book53 event was
# approximately -920 bps, so -60 bps remains deliberately permissive while
# removing the cubic-downside tail.
DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS = -60.0

# A1.7.5 age escalation.  The A1.7.4.5 runtime showed the static floor is
# fail-closed *forever*: Book 80 was refused at -60.18 bps on tick 1411 (missing
# the floor by 0.18 bps), then decayed to -320 bps and stayed trapped for 2,460
# ticks while holding 0.3902 BASE -- 20% of the 2.0 BASE cap.  Refusing a bounded
# -60 bps exit to create an unbounded frozen position is strictly worse, so the
# floor widens with age once the guard has already refused a residual for
# hundreds of ticks.  Fresh dust keeps the unchanged -60 bps budget.
DIRECT_A175_DUST_PATIENCE_TICKS = 600
DIRECT_A175_DUST_ESCALATION_TICKS = 2000
DIRECT_A175_DUST_MAX_FLOOR_BPS = -250.0


def a175_age_escalated_floor_bps(
    age_ticks: int,
    *,
    base_floor_bps: float = DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS,
    patience_ticks: int = DIRECT_A175_DUST_PATIENCE_TICKS,
    escalation_ticks: int = DIRECT_A175_DUST_ESCALATION_TICKS,
    max_floor_bps: float = DIRECT_A175_DUST_MAX_FLOOR_BPS,
) -> float:
    """Return the effective dust floor for a residual of the given age.

    Flat at ``base_floor_bps`` through ``patience_ticks``, then interpolated
    linearly to ``max_floor_bps`` at ``escalation_ticks`` and flat beyond.
    """
    base = _finite(base_floor_bps)
    if base is None:
        base = DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS
    widest = _finite(max_floor_bps)
    if widest is None:
        widest = DIRECT_A175_DUST_MAX_FLOOR_BPS
    try:
        age = max(0, int(age_ticks or 0))
    except (TypeError, ValueError):
        age = 0
    start = max(0, int(patience_ticks or 0))
    end = max(start + 1, int(escalation_ticks or 0))

    # Escalation only ever widens the budget.
    if widest >= base:
        return float(base)
    if age <= start:
        return float(base)
    if age >= end:
        return float(widest)
    span = float(end - start)
    return float(base + (widest - base) * ((age - start) / span))

REASON_NON_CROSS = "NON_CROSS_COMPACTION"
REASON_WITHIN_BUDGET = "WITHIN_KAPPA_LOSS_BUDGET"
REASON_AGE_ESCALATED = "AGE_ESCALATED_KAPPA_BUDGET"
REASON_LOSS_BUDGET = "KAPPA_LOSS_BUDGET_EXCEEDED"
REASON_UNKNOWN_COST = "UNKNOWN_COST_BASIS"
REASON_INVALID = "INVALID_COMPACTION_INPUT"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


@dataclass(frozen=True)
class DustKappaDecision:
    allow: bool
    reason: str
    cross_dust: bool
    net_base: float
    min_order: float
    projected_net: float
    vwap_entry: float | None
    maker_close_price: float | None
    maker_realization_bps: float | None
    projected_realized_quote_pnl: float | None
    loss_floor_bps: float
    age_ticks: int
    effective_floor_bps: float = DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS
    age_escalated: bool = False
    best_realization_bps: float | None = None

    def as_log(self) -> dict[str, Any]:
        return {
            "direct_dust_kappa_version": DIRECT_DUST_KAPPA_VERSION,
            "dust_kappa_allow": int(bool(self.allow)),
            "dust_kappa_reason": self.reason,
            "dust_kappa_cross": int(bool(self.cross_dust)),
            "net_base": self.net_base,
            "min_order_size": self.min_order,
            "projected_full_fill_net": self.projected_net,
            "vwap_entry": self.vwap_entry,
            "maker_close_price": self.maker_close_price,
            "maker_realization_bps": self.maker_realization_bps,
            "projected_realized_quote_pnl": self.projected_realized_quote_pnl,
            "dust_kappa_loss_floor_bps": self.loss_floor_bps,
            "dust_kappa_effective_floor_bps": self.effective_floor_bps,
            "dust_kappa_age_escalated": int(bool(self.age_escalated)),
            "dust_kappa_best_realization_bps": self.best_realization_bps,
            "dust_age_ticks": self.age_ticks,
        }


def decide_kappa_safe_dust_compaction(
    *,
    net_base: float,
    min_order: float,
    vwap_entry: float | None,
    maker_close_price: float | None,
    age_ticks: int = 0,
    loss_floor_bps: float = DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS,
    best_realization_bps: float | None = None,
    eps: float = 1e-12,
) -> DustKappaDecision:
    """Gate one minimum-size moderate-dust Maker sign-cross.

    For an existing long residual, the compactor sells ``min_order``; for a
    short residual it buys ``min_order``.  Only ``abs(net_base)`` closes the old
    lot and realizes PnL; the excess opens the opposite residual.  The guard
    therefore estimates realized bps from the tracked VWAP to the exact passive
    touch that the Direct compactor will publish.

    Unknown cost basis fails closed for sign-cross compaction.  This is
    intentional: a mechanical liveness action must not be allowed to create an
    unbounded Kappa tail when its economic cost cannot be measured.
    """
    net = _finite(net_base)
    floor = _finite(min_order)
    px = _finite(maker_close_price)
    vwap = _finite(vwap_entry)
    budget = _finite(loss_floor_bps)
    if budget is None:
        budget = DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS
    try:
        age = max(0, int(age_ticks or 0))
    except (TypeError, ValueError):
        age = 0

    # A1.7.5: widen the budget once the guard has already refused this residual
    # for hundreds of ticks, so a bounded loss cannot become a frozen position.
    effective = a175_age_escalated_floor_bps(age, base_floor_bps=budget)
    escalated = bool(effective < budget - 1e-12)
    best_seen = _finite(best_realization_bps)

    if net is None or floor is None or floor <= 0.0 or abs(net) <= max(eps, 1e-12):
        return DustKappaDecision(
            False, REASON_INVALID, False, float(net or 0.0), float(floor or 0.0),
            float(net or 0.0), vwap, px, None, None, float(budget), age,
            float(effective), escalated, best_seen,
        )

    signed_reduce = floor if net > 0.0 else -floor
    projected = net - signed_reduce
    cross = bool(net * projected < -max(eps, 1e-12) ** 2)

    if not cross:
        return DustKappaDecision(
            True, REASON_NON_CROSS, False, net, floor, projected,
            vwap, px, None, None, float(budget), age,
            float(effective), escalated, best_seen,
        )

    if vwap is None or vwap <= 0.0 or px is None or px <= 0.0:
        # Unknown cost basis still fails closed, at any age.
        return DustKappaDecision(
            False, REASON_UNKNOWN_COST, True, net, floor, projected,
            vwap, px, None, None, float(budget), age,
            float(effective), escalated, best_seen,
        )

    if net > 0.0:
        realization_bps = ((px - vwap) / vwap) * 10_000.0
        quote_pnl = abs(net) * (px - vwap)
    else:
        realization_bps = ((vwap - px) / vwap) * 10_000.0
        quote_pnl = abs(net) * (vwap - px)

    allow = realization_bps + 1e-12 >= float(effective)
    if allow and escalated and realization_bps + 1e-12 < float(budget):
        reason = REASON_AGE_ESCALATED
    elif allow:
        reason = REASON_WITHIN_BUDGET
    else:
        reason = REASON_LOSS_BUDGET
    if best_seen is None or realization_bps > best_seen:
        best_seen = realization_bps
    return DustKappaDecision(
        allow,
        reason,
        True,
        net,
        floor,
        projected,
        vwap,
        px,
        realization_bps,
        quote_pnl,
        float(budget),
        age,
        float(effective),
        escalated,
        best_seen,
    )
