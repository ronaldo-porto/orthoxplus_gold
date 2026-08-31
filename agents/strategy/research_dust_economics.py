# SPDX-License-Identifier: MIT
"""Research dust economics.

Previous CROSS_DUST cleanup paid taker cost on a min-size clip larger
than the residual and often flipped into a new dusty book. That realized
a loss instead of parking an unsizable leftover.

Primary objective: prevent dust rather than clean it.

Cleanup ladder:
    tiny dust              → quarantine
    profitable maker       → competitive maker at touch (passive never fills)
    older moderate dust    → competitive maker at touch even if maker EV is slightly negative
    taker / aggressive     → only if holding risk > cleanup cost

Loss-making CROSS_DUST is blocked unless true catastrophic hard-risk
justifies it. Never create a larger opposite position to satisfy min size.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_exit_quantity import inventory_after_reduce
from research_quote_lifecycle import classify_fill
from research_realization import maker_exit_ev
from research_taker_economics import (
    expected_holding_cost,
    expected_taker_cost,
    is_catastrophic_hard_risk,
)

DUST_ECON_VERSION = "dust_economics_v2"

BAND_NONE = "NONE"
BAND_TINY = "TINY"
BAND_MODERATE = "MODERATE"

ACTION_QUARANTINE = "DUST_QUARANTINE"
ACTION_PASSIVE_MAKER = "DUST_PASSIVE_MAKER"
ACTION_COMPETITIVE_MAKER = "DUST_COMPETITIVE_MAKER"
ACTION_TAKER = "DUST_TAKER"
ACTION_REJECT_CROSS = "DUST_REJECT_CROSS"

MAKER_ACTIONS = (ACTION_PASSIVE_MAKER, ACTION_COMPETITIVE_MAKER)

REASON_NOT_DUST = "NOT_DUST"
REASON_TINY = "TINY_QUARANTINE"
REASON_MAKER_PROFITABLE = "MAKER_CLEANUP"
REASON_OLDER_COMPETITIVE = "OLDER_COMPETITIVE_MAKER"
REASON_SLOT_RELEASE = "OLDER_SLOT_RELEASE_MAKER"
REASON_HOLDING_EXCEEDS_COST = "DUST_HOLDING_EXCEEDS_COST"
REASON_REJECT_UNECONOMIC_CROSS = "REJECT_LOSS_MAKING_CROSS_DUST"
REASON_REJECT_UNSIZABLE = "REJECT_UNSIZABLE_DUST"
REASON_CATASTROPHIC = "DUST_CATASTROPHIC"
REASON_QUARANTINE = "DUST_QUARANTINE"

DEFAULT_TINY_FRACTION = 0.50
DEFAULT_MODERATE_AGE_TICKS = 16.0
DEFAULT_MAKER_EV_FLOOR_BPS = 0.0
DEFAULT_NET_FLOOR_BPS = 0.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def is_dust_qty(
    inventory: float,
    min_order: float,
    *,
    eps: float = 1e-12,
) -> bool:
    abs_qty = abs(_finite(inventory))
    floor = max(0.0, _finite(min_order))
    edge = max(float(eps), 1e-12)
    if floor <= 0.0:
        return False
    return abs_qty >= edge and abs_qty + edge < floor


def classify_dust_band(
    inventory: float,
    min_order: float,
    *,
    tiny_fraction: float = DEFAULT_TINY_FRACTION,
    eps: float = 1e-12,
) -> str:
    """TINY is theorem-unsafe for a min-size fill: |q| <= 0.5 * min."""
    if not is_dust_qty(inventory, min_order, eps=eps):
        return BAND_NONE
    abs_qty = abs(_finite(inventory))
    floor = max(0.0, _finite(min_order))
    frac = min(1.0, max(0.0, _finite(tiny_fraction, DEFAULT_TINY_FRACTION)))
    if abs_qty <= frac * floor + max(float(eps), 1e-12):
        return BAND_TINY
    return BAND_MODERATE


def quote_would_create_dust(
    *,
    inventory_before: float,
    signed_fill_qty: float,
    min_order_size: float,
    eps: float = 1e-12,
) -> bool:
    """True when a fill would create dust or fail to reduce existing dust."""
    before = _finite(inventory_before)
    after = before + _finite(signed_fill_qty)
    edge = max(float(eps), 1e-12)
    if not is_dust_qty(after, min_order_size, eps=edge):
        return False
    if abs(before) < edge:
        return True
    return abs(after) + edge >= abs(before)


def predicted_dust_blocks_increase(
    *,
    dust_prob: float,
    dust_target: float,
    inventory_before: float,
    signed_qty: float,
    usable: bool,
    eps: float = 1e-12,
) -> bool:
    """Skip exposure-increasing quotes when predicted dust exceeds target."""
    if not usable:
        return False
    if _finite(dust_prob) <= _finite(dust_target):
        return False
    after = _finite(inventory_before) + _finite(signed_qty)
    return abs(after) > abs(_finite(inventory_before)) + max(float(eps), 1e-12)


def cleanup_fill_class(
    *,
    inventory: float,
    reduce_qty: float,
    min_order: float,
    eps: float = 1e-12,
) -> str:
    after = inventory_after_reduce(inventory, reduce_qty)
    return classify_fill(
        inventory_before=_finite(inventory),
        inventory_after=after,
        fill_quantity=max(0.0, _finite(reduce_qty)),
        requested_quantity=max(0.0, _finite(reduce_qty)),
        filled_quantity=max(0.0, _finite(reduce_qty)),
        min_order_size=max(0.0, _finite(min_order)),
        flat_eps=eps,
    )


def is_cross_dust_cleanup(
    *,
    inventory: float,
    reduce_qty: float,
    min_order: float,
    eps: float = 1e-12,
) -> bool:
    return cleanup_fill_class(
        inventory=inventory,
        reduce_qty=reduce_qty,
        min_order=min_order,
        eps=eps,
    ) == "CROSS_DUST"


def clip_ratio(inventory: float, reduce_qty: float) -> float:
    inv = abs(_finite(inventory))
    qty = max(0.0, _finite(reduce_qty))
    if inv <= 1e-18:
        return 1.0
    return max(1.0, qty / inv)


def cross_dust_expected_net_bps(
    *,
    unrealized_pnl: float | None,
    cleanup_cost_bps: float,
    inventory: float,
    reduce_qty: float,
) -> float:
    """Unknown mark is treated as zero so a free CROSS profit is not invented."""
    upnl = 0.0 if unrealized_pnl is None else _finite(unrealized_pnl)
    cost = max(0.0, _finite(cleanup_cost_bps))
    return upnl - cost * clip_ratio(inventory, reduce_qty)


@dataclass(frozen=True)
class DustActionDecision:
    allow: bool
    action: str
    reason: str
    band: str
    inventory: float
    reduce_qty: float
    inventory_after: float
    maker_ev_bps: float
    holding_cost_bps: float
    cleanup_cost_bps: float
    expected_net_bps: float
    cross_dust: bool
    catastrophic: bool
    age_ticks: float

    def as_log(self) -> dict[str, Any]:
        return {
            "dust_econ_version": DUST_ECON_VERSION,
            "dust_allow": int(bool(self.allow)),
            "dust_action": self.action,
            "dust_reason": self.reason,
            "dust_band": self.band,
            "dust_qty": abs(self.inventory),
            "dust_reduce_qty": self.reduce_qty,
            "dust_inventory_after": self.inventory_after,
            "dust_maker_ev_bps": self.maker_ev_bps,
            "dust_holding_cost_bps": self.holding_cost_bps,
            "dust_cleanup_cost_bps": self.cleanup_cost_bps,
            "dust_expected_net_bps": self.expected_net_bps,
            "dust_cross": int(bool(self.cross_dust)),
            "dust_catastrophic": int(bool(self.catastrophic)),
            "dust_age_ticks": self.age_ticks,
        }


def _reject(
    *,
    action: str,
    reason: str,
    band: str,
    inventory: float,
    reduce_qty: float,
    maker_ev: float,
    holding: float,
    cleanup: float,
    expected_net: float,
    cross_dust: bool,
    catastrophic: bool,
    age_ticks: float,
) -> DustActionDecision:
    return DustActionDecision(
        allow=False,
        action=action,
        reason=reason,
        band=band,
        inventory=_finite(inventory),
        reduce_qty=max(0.0, _finite(reduce_qty)),
        inventory_after=inventory_after_reduce(inventory, reduce_qty),
        maker_ev_bps=maker_ev,
        holding_cost_bps=holding,
        cleanup_cost_bps=cleanup,
        expected_net_bps=expected_net,
        cross_dust=bool(cross_dust),
        catastrophic=bool(catastrophic),
        age_ticks=_finite(age_ticks),
    )


def evaluate_dust_action(
    *,
    inventory: float,
    min_order: float,
    reduce_qty: float,
    age_ticks: float = 0.0,
    unrealized_pnl: float | None = None,
    spread_bps: float = 0.0,
    fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    expected_markout: float = 0.0,
    volatility: float = 0.0,
    ofi: float | None = None,
    inventory_ratio: float = 0.0,
    kappa_need: float = 0.0,
    volume_cap_headroom: float = 1.0,
    stop_loss_hit: bool = False,
    band: str | None = None,
    tiny_fraction: float = DEFAULT_TINY_FRACTION,
    moderate_age_ticks: float = DEFAULT_MODERATE_AGE_TICKS,
    maker_ev_floor_bps: float = DEFAULT_MAKER_EV_FLOOR_BPS,
    net_floor_bps: float = DEFAULT_NET_FLOOR_BPS,
    eps: float = 1e-12,
) -> DustActionDecision:
    """Decide whether a dusty residual should be parked, made, or taken."""
    inv = _finite(inventory)
    qty = max(0.0, _finite(reduce_qty))
    dust_band = classify_dust_band(
        inv, min_order, tiny_fraction=tiny_fraction, eps=eps,
    )
    maker_ev = maker_exit_ev(
        spread_bps=spread_bps,
        fee_bps=fee_bps,
        expected_adverse_bps=max(0.0, -_finite(expected_markout)),
        urgency=0.0,
    )
    holding = expected_holding_cost(
        inventory_ratio=inventory_ratio,
        inventory_size=abs(inv),
        volatility=volatility,
        inventory_age=age_ticks,
        expected_markout=expected_markout,
        ofi=ofi,
        inventory_sign=inv,
        kappa_need=kappa_need,
        volume_cap_headroom=volume_cap_headroom,
    ).expected_holding_cost
    taker = expected_taker_cost(
        fee_bps=fee_bps,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        inventory_size=max(abs(inv), qty),
        volatility=volatility,
    ).expected_taker_cost
    cleanup = taker * clip_ratio(inv, qty)
    expected_net = cross_dust_expected_net_bps(
        unrealized_pnl=unrealized_pnl,
        cleanup_cost_bps=taker,
        inventory=inv,
        reduce_qty=qty,
    )
    cross = is_cross_dust_cleanup(
        inventory=inv, reduce_qty=qty, min_order=min_order, eps=eps,
    )
    catastrophic = is_catastrophic_hard_risk(
        stop_loss_hit=stop_loss_hit,
        inventory_ratio=inventory_ratio,
        unrealized_pnl=unrealized_pnl,
        band=band,
    )
    after = inventory_after_reduce(inv, qty)
    larger_opposite = after * inv < 0.0 and abs(after) + 1e-12 >= abs(inv)
    age = _finite(age_ticks)
    older = age + 1e-12 >= max(1.0, _finite(moderate_age_ticks, DEFAULT_MODERATE_AGE_TICKS))
    floor = _finite(maker_ev_floor_bps, DEFAULT_MAKER_EV_FLOOR_BPS)
    net_floor = _finite(net_floor_bps, DEFAULT_NET_FLOOR_BPS)

    def reject(action: str, reason: str) -> DustActionDecision:
        return _reject(
            action=action,
            reason=reason,
            band=dust_band,
            inventory=inv,
            reduce_qty=qty,
            maker_ev=maker_ev,
            holding=holding,
            cleanup=cleanup,
            expected_net=expected_net,
            cross_dust=cross,
            catastrophic=catastrophic,
            age_ticks=age,
        )

    def accept(action: str, reason: str) -> DustActionDecision:
        return DustActionDecision(
            allow=True,
            action=action,
            reason=reason,
            band=dust_band,
            inventory=inv,
            reduce_qty=qty,
            inventory_after=after,
            maker_ev_bps=maker_ev,
            holding_cost_bps=holding,
            cleanup_cost_bps=cleanup,
            expected_net_bps=expected_net,
            cross_dust=cross,
            catastrophic=catastrophic,
            age_ticks=age,
        )

    if dust_band == BAND_NONE:
        return reject(ACTION_QUARANTINE, REASON_NOT_DUST)
    if dust_band == BAND_TINY:
        return reject(ACTION_QUARANTINE, REASON_TINY)
    if qty <= 1e-18 or larger_opposite:
        return reject(ACTION_QUARANTINE, REASON_REJECT_UNSIZABLE)
    if catastrophic:
        return accept(ACTION_TAKER, REASON_CATASTROPHIC)
    if maker_ev + 1e-12 >= floor:
        # PASSIVE sits two ticks behind touch and does not fill on Testnet.
        # Moderate dust that is economic enough to make must quote at touch.
        if older:
            return accept(ACTION_COMPETITIVE_MAKER, REASON_OLDER_COMPETITIVE)
        return accept(ACTION_COMPETITIVE_MAKER, REASON_MAKER_PROFITABLE)
    if holding + 1e-12 > cleanup:
        if cross and expected_net + 1e-12 < net_floor:
            if older:
                return accept(ACTION_COMPETITIVE_MAKER, REASON_SLOT_RELEASE)
            return reject(ACTION_REJECT_CROSS, REASON_REJECT_UNECONOMIC_CROSS)
        return accept(ACTION_TAKER, REASON_HOLDING_EXCEEDS_COST)
    if older:
        # Slot-release: quarantine never recycles the total-open slot. A touch
        # maker quote can; a loss-making CROSS taker still must not.
        return accept(ACTION_COMPETITIVE_MAKER, REASON_SLOT_RELEASE)
    return reject(ACTION_QUARANTINE, REASON_QUARANTINE)
