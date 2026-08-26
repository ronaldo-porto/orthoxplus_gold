# SPDX-License-Identifier: MIT
"""Research taker economics.

Previous taker exits were systematically losing because labeled
emergencies auto-crossed. A take now requires:

    ExpectedHoldingCost > ExpectedTakerCost

and preferably:

    ExpectedNetRealizationPnL >= configured floor

Only true catastrophic hard-risk may override economics.
EMERGENCY_HARD / MAX band / stop-loss alone is not a take.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

TAKER_ECON_VERSION = "taker_economics_v2_live_fees"

REASON_HOLDING_EXCEEDS_COST = "TAKER_HOLDING_EXCEEDS_COST"
REASON_REJECT_ECONOMICS = "TAKER_REJECTED_ECONOMICS"
REASON_REJECT_NET_FLOOR = "TAKER_REJECTED_NET_FLOOR"
REASON_CATASTROPHIC = "TAKER_CATASTROPHIC"

DEFAULT_INVENTORY_RISK_SCALE_BPS = 10.0
DEFAULT_ADVERSE_SCALE_BPS = 8.0
DEFAULT_AGE_SCALE_BPS = 6.0
DEFAULT_KAPPA_SCALE_BPS = 5.0
DEFAULT_VOLUME_CAP_SCALE_BPS = 6.0
DEFAULT_IMPACT_SCALE_BPS = 4.0
DEFAULT_NET_FLOOR_BPS = 0.0
DEFAULT_CATASTROPHIC_DRAWDOWN_BPS = 25.0
DEFAULT_CATASTROPHIC_RATIO = 0.95
DEFAULT_AGE_REF = 20.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number




def fee_rate_to_bps(
    rate: float | None,
    *,
    fallback_bps: float = 0.0,
    allow_rebate: bool = True,
) -> float:
    """Convert the simulator decimal fee rate to basis points.

    Live SN79 fee tiers are per-book/per-agent and can change during a run.
    Maker rates may be negative (rebate); taker economics normally pass
    ``allow_rebate=False`` so malformed negative taker rates cannot subsidize a cross.
    """
    if rate is None:
        return _finite(fallback_bps)
    try:
        bps = float(rate) * 10_000.0
    except (TypeError, ValueError):
        return _finite(fallback_bps)
    if not math.isfinite(bps):
        return _finite(fallback_bps)
    if not allow_rebate:
        bps = max(0.0, bps)
    return bps

def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value)))


def _tanh01(value: Any, scale: float) -> float:
    return math.tanh(max(0.0, _finite(value)) / max(1e-9, float(scale)))


def ofi_against_position(ofi: float | None, inventory_sign: float) -> float:
    if ofi is None:
        return 0.0
    flow = _finite(ofi)
    sign = _finite(inventory_sign)
    if sign > 0.0:
        return max(0.0, -flow)
    if sign < 0.0:
        return max(0.0, flow)
    return abs(flow)


def inventory_risk_bps(
    *,
    inventory_ratio: float,
    inventory_size: float,
    volatility: float,
    inventory_age: float,
    scale_bps: float = DEFAULT_INVENTORY_RISK_SCALE_BPS,
    age_ref: float = DEFAULT_AGE_REF,
) -> float:
    """AS / GLFT inventory risk in bps: q^2, size, and sigma, grown by hold time."""
    qty2 = _clip01(abs(_finite(inventory_ratio))) ** 2
    size_term = _tanh01(abs(_finite(inventory_size)), 0.50)
    vol_term = math.tanh((_finite(volatility) / 0.004) ** 2)
    time_term = 1.0 + _tanh01(inventory_age, age_ref)
    pressure = _clip01(0.45 * qty2 + 0.25 * size_term + 0.30 * vol_term)
    return max(0.0, _finite(scale_bps)) * pressure * min(2.0, time_term) / 1.5


def expected_adverse_move_bps(
    *,
    expected_markout: float,
    ofi: float | None,
    inventory_sign: float,
    volatility: float,
    scale_bps: float = DEFAULT_ADVERSE_SCALE_BPS,
) -> float:
    """Expected further adverse move in bps from markout and OFI-against."""
    adverse_markout = max(0.0, -_finite(expected_markout))
    flow = ofi_against_position(ofi, inventory_sign)
    return max(
        0.0,
        adverse_markout
        + max(0.0, _finite(scale_bps)) * flow * (0.50 + 0.50 * _tanh01(volatility, 0.006)),
    )


def inventory_age_cost_bps(
    inventory_age: float,
    *,
    scale_bps: float = DEFAULT_AGE_SCALE_BPS,
    age_ref: float = DEFAULT_AGE_REF,
) -> float:
    return max(0.0, _finite(scale_bps)) * _tanh01(inventory_age, age_ref)


def kappa_opportunity_cost_bps(
    kappa_need: float,
    *,
    scale_bps: float = DEFAULT_KAPPA_SCALE_BPS,
) -> float:
    return max(0.0, _finite(scale_bps)) * _clip01(kappa_need)


def volume_cap_opportunity_cost_bps(
    volume_cap_headroom: float,
    *,
    scale_bps: float = DEFAULT_VOLUME_CAP_SCALE_BPS,
) -> float:
    return max(0.0, _finite(scale_bps)) * (1.0 - _clip01(volume_cap_headroom))


def market_impact_buffer_bps(
    *,
    inventory_size: float,
    volatility: float,
    scale_bps: float = DEFAULT_IMPACT_SCALE_BPS,
) -> float:
    size_term = _tanh01(abs(_finite(inventory_size)), 0.50)
    vol_term = 0.50 + 0.50 * _tanh01(volatility, 0.006)
    return max(0.0, _finite(scale_bps)) * size_term * vol_term


def is_catastrophic_hard_risk(
    *,
    stop_loss_hit: bool = False,
    inventory_ratio: float = 0.0,
    unrealized_pnl: float | None = None,
    band: str | None = None,
    drawdown_bps: float = DEFAULT_CATASTROPHIC_DRAWDOWN_BPS,
    ratio_min: float = DEFAULT_CATASTROPHIC_RATIO,
) -> bool:
    """True only when stop, maxed book, and a large drawdown all coincide.

    ``hard_emergency`` / MAX band / stop-loss alone is not catastrophic.
    """
    token = str(band or "").upper()
    maxed = token in {"MAX_LONG", "MAX_SHORT"} or (
        abs(_finite(inventory_ratio)) + 1e-12 >= max(0.0, float(ratio_min))
    )
    drawdown = -_finite(unrealized_pnl)
    return bool(
        stop_loss_hit
        and maxed
        and drawdown + 1e-12 >= max(0.0, float(drawdown_bps))
    )


@dataclass(frozen=True)
class HoldingCostBreakdown:
    inventory_risk: float
    expected_adverse_move: float
    inventory_age_cost: float
    kappa_opportunity_cost: float
    volume_cap_opportunity_cost: float
    expected_holding_cost: float

    def as_log(self) -> dict[str, Any]:
        return {
            "inventory_risk": self.inventory_risk,
            "expected_adverse_move": self.expected_adverse_move,
            "inventory_age_cost": self.inventory_age_cost,
            "kappa_opportunity_cost": self.kappa_opportunity_cost,
            "volume_cap_opportunity_cost": self.volume_cap_opportunity_cost,
            "expected_holding_cost": self.expected_holding_cost,
        }


@dataclass(frozen=True)
class TakerCostBreakdown:
    taker_fee: float
    spread_cross_cost: float
    slippage_buffer: float
    market_impact_buffer: float
    expected_taker_cost: float

    def as_log(self) -> dict[str, Any]:
        return {
            "taker_fee": self.taker_fee,
            "spread_cross_cost": self.spread_cross_cost,
            "slippage_buffer": self.slippage_buffer,
            "market_impact_buffer": self.market_impact_buffer,
            "expected_taker_cost": self.expected_taker_cost,
        }


@dataclass(frozen=True)
class TakerEconomicsDecision:
    take: bool
    reason: str
    holding: HoldingCostBreakdown
    taker: TakerCostBreakdown
    expected_net_realization_pnl: float
    net_floor_bps: float
    economic_ok: bool
    floor_ok: bool
    catastrophic: bool

    def as_log(self) -> dict[str, Any]:
        payload = {
            "taker_econ_version": TAKER_ECON_VERSION,
            "taker_take": int(bool(self.take)),
            "taker_reason": self.reason,
            "expected_net_realization_pnl": self.expected_net_realization_pnl,
            "net_floor_bps": self.net_floor_bps,
            "economic_ok": int(bool(self.economic_ok)),
            "floor_ok": int(bool(self.floor_ok)),
            "catastrophic": int(bool(self.catastrophic)),
        }
        payload.update(self.holding.as_log())
        payload.update(self.taker.as_log())
        return payload


def expected_holding_cost(
    *,
    inventory_ratio: float,
    inventory_size: float,
    volatility: float,
    inventory_age: float,
    expected_markout: float = 0.0,
    ofi: float | None = None,
    inventory_sign: float = 0.0,
    kappa_need: float = 0.0,
    volume_cap_headroom: float = 1.0,
    inventory_risk_scale_bps: float = DEFAULT_INVENTORY_RISK_SCALE_BPS,
    adverse_scale_bps: float = DEFAULT_ADVERSE_SCALE_BPS,
    age_scale_bps: float = DEFAULT_AGE_SCALE_BPS,
    kappa_scale_bps: float = DEFAULT_KAPPA_SCALE_BPS,
    volume_cap_scale_bps: float = DEFAULT_VOLUME_CAP_SCALE_BPS,
    age_ref: float = DEFAULT_AGE_REF,
) -> HoldingCostBreakdown:
    inventory_risk = inventory_risk_bps(
        inventory_ratio=inventory_ratio,
        inventory_size=inventory_size,
        volatility=volatility,
        inventory_age=inventory_age,
        scale_bps=inventory_risk_scale_bps,
        age_ref=age_ref,
    )
    adverse = expected_adverse_move_bps(
        expected_markout=expected_markout,
        ofi=ofi,
        inventory_sign=inventory_sign,
        volatility=volatility,
        scale_bps=adverse_scale_bps,
    )
    age_cost = inventory_age_cost_bps(
        inventory_age, scale_bps=age_scale_bps, age_ref=age_ref,
    )
    kappa_cost = kappa_opportunity_cost_bps(kappa_need, scale_bps=kappa_scale_bps)
    cap_cost = volume_cap_opportunity_cost_bps(
        volume_cap_headroom, scale_bps=volume_cap_scale_bps,
    )
    return HoldingCostBreakdown(
        inventory_risk=inventory_risk,
        expected_adverse_move=adverse,
        inventory_age_cost=age_cost,
        kappa_opportunity_cost=kappa_cost,
        volume_cap_opportunity_cost=cap_cost,
        expected_holding_cost=max(
            0.0, inventory_risk + adverse + age_cost + kappa_cost + cap_cost,
        ),
    )


def expected_taker_cost(
    *,
    fee_bps: float,
    spread_bps: float,
    slippage_bps: float,
    inventory_size: float = 0.0,
    volatility: float = 0.0,
    impact_scale_bps: float = DEFAULT_IMPACT_SCALE_BPS,
) -> TakerCostBreakdown:
    fee = max(0.0, _finite(fee_bps))
    spread_cross = 0.5 * max(0.0, _finite(spread_bps))
    slip = max(0.0, _finite(slippage_bps))
    impact = market_impact_buffer_bps(
        inventory_size=inventory_size,
        volatility=volatility,
        scale_bps=impact_scale_bps,
    )
    return TakerCostBreakdown(
        taker_fee=fee,
        spread_cross_cost=spread_cross,
        slippage_buffer=slip,
        market_impact_buffer=impact,
        expected_taker_cost=max(0.0, fee + spread_cross + slip + impact),
    )


def evaluate_taker_economics(
    *,
    inventory_ratio: float = 0.0,
    inventory_size: float = 0.0,
    volatility: float = 0.0,
    inventory_age: float = 0.0,
    expected_markout: float = 0.0,
    ofi: float | None = None,
    inventory_sign: float = 0.0,
    kappa_need: float = 0.0,
    volume_cap_headroom: float = 1.0,
    fee_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    unrealized_pnl: float | None = None,
    stop_loss_hit: bool = False,
    band: str | None = None,
    net_floor_bps: float = DEFAULT_NET_FLOOR_BPS,
    inventory_risk_scale_bps: float = DEFAULT_INVENTORY_RISK_SCALE_BPS,
    adverse_scale_bps: float = DEFAULT_ADVERSE_SCALE_BPS,
    age_scale_bps: float = DEFAULT_AGE_SCALE_BPS,
    kappa_scale_bps: float = DEFAULT_KAPPA_SCALE_BPS,
    volume_cap_scale_bps: float = DEFAULT_VOLUME_CAP_SCALE_BPS,
    impact_scale_bps: float = DEFAULT_IMPACT_SCALE_BPS,
    min_taker_cost_bps: float = 0.0,
    catastrophic_drawdown_bps: float = DEFAULT_CATASTROPHIC_DRAWDOWN_BPS,
    catastrophic_ratio: float = DEFAULT_CATASTROPHIC_RATIO,
) -> TakerEconomicsDecision:
    holding = expected_holding_cost(
        inventory_ratio=inventory_ratio,
        inventory_size=inventory_size,
        volatility=volatility,
        inventory_age=inventory_age,
        expected_markout=expected_markout,
        ofi=ofi,
        inventory_sign=inventory_sign,
        kappa_need=kappa_need,
        volume_cap_headroom=volume_cap_headroom,
        inventory_risk_scale_bps=inventory_risk_scale_bps,
        adverse_scale_bps=adverse_scale_bps,
        age_scale_bps=age_scale_bps,
        kappa_scale_bps=kappa_scale_bps,
        volume_cap_scale_bps=volume_cap_scale_bps,
    )
    taker = expected_taker_cost(
        fee_bps=fee_bps,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        inventory_size=inventory_size,
        volatility=volatility,
        impact_scale_bps=impact_scale_bps,
    )
    floor_cost = max(taker.expected_taker_cost, max(0.0, _finite(min_taker_cost_bps)))
    if floor_cost > taker.expected_taker_cost + 1e-12:
        taker = TakerCostBreakdown(
            taker_fee=taker.taker_fee,
            spread_cross_cost=taker.spread_cross_cost,
            slippage_buffer=taker.slippage_buffer,
            market_impact_buffer=taker.market_impact_buffer,
            expected_taker_cost=floor_cost,
        )
    net = holding.expected_holding_cost - taker.expected_taker_cost
    floor = _finite(net_floor_bps)
    economic_ok = holding.expected_holding_cost > taker.expected_taker_cost + 1e-12
    floor_ok = net + 1e-12 >= floor
    catastrophic = is_catastrophic_hard_risk(
        stop_loss_hit=stop_loss_hit,
        inventory_ratio=inventory_ratio,
        unrealized_pnl=unrealized_pnl,
        band=band,
        drawdown_bps=catastrophic_drawdown_bps,
        ratio_min=catastrophic_ratio,
    )
    if catastrophic:
        take = True
        reason = REASON_CATASTROPHIC
    elif economic_ok and floor_ok:
        take = True
        reason = REASON_HOLDING_EXCEEDS_COST
    elif economic_ok and not floor_ok:
        take = False
        reason = REASON_REJECT_NET_FLOOR
    else:
        take = False
        reason = REASON_REJECT_ECONOMICS
    return TakerEconomicsDecision(
        take=take,
        reason=reason,
        holding=holding,
        taker=taker,
        expected_net_realization_pnl=net,
        net_floor_bps=floor,
        economic_ok=economic_ok,
        floor_ok=floor_ok,
        catastrophic=catastrophic,
    )
