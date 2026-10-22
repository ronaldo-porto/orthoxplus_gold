# SPDX-License-Identifier: MIT
"""Production ExitUrgency and selective realization.

Standalone copy inlined into BaseStrategy. No Strategy1 / Research runtime
imports.

Goal: turn profitable inventory into completed round trips earlier
(UID27-style throughput) without oversized inventory or burst taker dumps.

Avellaneda-Stoikov / GLFT: inventory risk grows with q^2 * sigma^2 and
remaining hold time. Realization urgency is continuous; taker exit is a
selective last rung, not a routine liquidation path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

INVENTORY_STATES = (
    "NORMAL",
    "CAUTION",
    "DEFENSIVE",
    "EXIT_ONLY",
    "EMERGENCY",
)

ACTION_PASSIVE = "PASSIVE_MAKER_EXIT"
ACTION_COMPETITIVE = "COMPETITIVE_MAKER_EXIT"
ACTION_AGGRESSIVE = "AGGRESSIVE_MAKER_EXIT"
ACTION_TAKER = "SELECTIVE_TAKER_EXIT"

MAKER_ACTIONS = (ACTION_PASSIVE, ACTION_COMPETITIVE, ACTION_AGGRESSIVE)

URGENCY_PASSIVE_MAX = 0.25
URGENCY_COMPETITIVE_MAX = 0.50
URGENCY_AGGRESSIVE_MAX = 0.78
URGENCY_CAUTION = 0.22
URGENCY_DEFENSIVE = 0.48
URGENCY_EXIT_ONLY = 0.72
URGENCY_EMERGENCY = 0.90


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value)))


def _tanh01(value: Any, scale: float) -> float:
    return math.tanh(max(0.0, _finite(value)) / max(1e-9, float(scale)))


def kappa_completion_need(
    observations_remaining: int,
    unrealized_pnl_bps: float | None = None,
) -> float:
    """Finish a near-qualified book when the position is not toxic."""
    remaining = max(0, int(observations_remaining or 0))
    upnl = _finite(unrealized_pnl_bps, 0.0)
    if remaining <= 0:
        return 0.0
    if remaining == 1:
        return 0.85 if upnl > 0.0 else 0.40
    if remaining == 2:
        return 0.25 if upnl > 0.0 else 0.08
    return 0.0


def inventory_holding_risk(
    *,
    inventory_ratio: float,
    volatility: float,
    inventory_age: float,
    age_ref: float = 20.0,
    gamma: float = 1.0,
) -> float:
    """AS / GLFT inventory risk: gamma * q^2 * f(sigma, hold time)."""
    qty2 = _clip01(abs(_finite(inventory_ratio))) ** 2
    vol_term = math.tanh((_finite(volatility) / 0.004) ** 2)
    time_term = 1.0 + _tanh01(inventory_age, age_ref)
    return _clip01(
        _finite(gamma, 1.0) * qty2 * (0.35 + 0.65 * vol_term) * min(2.0, time_term) / 2.0
    )


def expected_adverse_move(
    *,
    expected_markout_bps: float,
    imbalance: float = 0.0,
    ofi: float | None = None,
    inventory_sign: float,
    volatility: float,
    scale_bps: float = 8.0,
) -> float:
    """Markout plus real OFI flowing against the open position.

    Static top-of-book imbalance is not OFI. When ``ofi`` is missing the
    flow term is zero rather than a renamed imbalance.
    """
    del imbalance
    adverse_markout = max(0.0, -_finite(expected_markout_bps))
    ofi_against = 0.0
    sign = _finite(inventory_sign)
    if ofi is not None:
        flow = _finite(ofi)
        if sign > 0.0:
            ofi_against = max(0.0, -flow)
        elif sign < 0.0:
            ofi_against = max(0.0, flow)
        else:
            ofi_against = abs(flow)
    vol = _tanh01(volatility, 0.006)
    return _clip01(
        0.65 * math.tanh(adverse_markout / max(1e-6, float(scale_bps)))
        + 0.35 * ofi_against * (0.5 + 0.5 * vol)
    )


def inventory_opportunity_cost(
    *,
    volume_cap_headroom: float,
    kappa_need: float,
    recent_realized_pnl: float | None = None,
) -> float:
    cap_cost = 1.0 - _clip01(volume_cap_headroom)
    recent_loss = _tanh01(max(0.0, -_finite(recent_realized_pnl)), 0.05)
    return _clip01(0.50 * cap_cost + 0.35 * _clip01(kappa_need) + 0.15 * recent_loss)


def taker_exit_cost(
    *,
    fee_bps: float,
    spread_bps: float,
    slippage_bps: float,
) -> float:
    return max(0.0, _finite(fee_bps) + _finite(spread_bps) + _finite(slippage_bps))


def taker_risk_bps(
    *,
    holding_risk: float,
    adverse_move: float,
    opportunity_cost: float,
    risk_scale_bps: float = 12.0,
) -> float:
    return max(0.0, _finite(risk_scale_bps)) * (
        0.45 * _clip01(holding_risk)
        + 0.35 * _clip01(adverse_move)
        + 0.20 * _clip01(opportunity_cost)
    )


def selective_taker_allowed(
    *,
    holding_risk: float,
    adverse_move: float,
    opportunity_cost: float,
    fee_bps: float,
    spread_bps: float,
    slippage_bps: float,
    risk_scale_bps: float = 12.0,
) -> tuple[bool, float, float]:
    """Taker only when holding/adverse/opportunity risk exceeds take cost."""
    risk = taker_risk_bps(
        holding_risk=holding_risk,
        adverse_move=adverse_move,
        opportunity_cost=opportunity_cost,
        risk_scale_bps=risk_scale_bps,
    )
    cost = taker_exit_cost(
        fee_bps=fee_bps, spread_bps=spread_bps, slippage_bps=slippage_bps,
    )
    return risk > cost + 1e-12, risk, cost


def exit_urgency(
    *,
    inventory_size: float,
    inventory_ratio: float,
    inventory_age: float,
    unrealized_pnl: float | None,
    expected_markout: float,
    volatility: float,
    imbalance: float = 0.0,
    ofi: float | None = None,
    inventory_sign: float,
    kappa_need: float,
    volume_cap_headroom: float,
    recent_realized_pnl: float | None,
    adverse_selection_risk: float,
    size_ref: float = 0.50,
    age_ref: float = 20.0,
) -> float:
    """Continuous [0, 1] realization pressure from scoring/inventory state."""
    size_term = _tanh01(abs(_finite(inventory_size)), size_ref)
    ratio_term = _clip01(abs(_finite(inventory_ratio))) ** 2
    age_term = _tanh01(inventory_age, age_ref)
    upnl = _finite(unrealized_pnl, 0.0)
    profit_term = _tanh01(max(0.0, upnl), 8.0) * (0.40 + 0.60 * age_term)
    loss_term = _tanh01(max(0.0, -upnl), 12.0)
    markout_term = _tanh01(max(0.0, -_finite(expected_markout)), 8.0)
    vol_term = _tanh01(volatility, 0.006)
    ofi_term = expected_adverse_move(
        expected_markout_bps=0.0,
        ofi=ofi,
        imbalance=imbalance,
        inventory_sign=inventory_sign,
        volatility=volatility,
    )
    as_term = _clip01(adverse_selection_risk)
    recent_term = _tanh01(max(0.0, -_finite(recent_realized_pnl)), 0.05)
    cap_term = 1.0 - _clip01(volume_cap_headroom)

    urgency = (
        0.16 * size_term
        + 0.16 * ratio_term
        + 0.14 * age_term
        + 0.10 * profit_term
        + 0.12 * loss_term
        + 0.08 * markout_term
        + 0.06 * vol_term
        + 0.06 * ofi_term
        + 0.04 * _clip01(kappa_need)
        + 0.03 * cap_term
        + 0.03 * recent_term
        + 0.02 * as_term
    )
    toxic = max(loss_term, markout_term, as_term)
    if toxic >= 0.55:
        urgency = max(urgency, 0.48 + 0.40 * toxic)
    return _clip01(urgency)


def classify_inventory_state(
    *,
    urgency: float,
    inventory_ratio: float,
    band: str | None = None,
    stop_loss_hit: bool = False,
    hard_emergency: bool = False,
) -> str:
    token = str(band or "").upper()
    ratio = abs(_finite(inventory_ratio))
    score = _clip01(urgency)
    if hard_emergency or stop_loss_hit or token in {"MAX_LONG", "MAX_SHORT"}:
        return "EMERGENCY"
    if score + 1e-12 >= URGENCY_EMERGENCY or ratio + 1e-12 >= 0.95:
        return "EMERGENCY"
    if score + 1e-12 >= URGENCY_EXIT_ONLY or ratio + 1e-12 >= 0.70:
        return "EXIT_ONLY"
    if score + 1e-12 >= URGENCY_DEFENSIVE or ratio + 1e-12 >= 0.45:
        return "DEFENSIVE"
    if score + 1e-12 >= URGENCY_CAUTION:
        return "CAUTION"
    return "NORMAL"


def classify_exit_action(urgency: float) -> str:
    score = _clip01(urgency)
    if score + 1e-12 >= URGENCY_AGGRESSIVE_MAX:
        return ACTION_TAKER
    if score + 1e-12 >= URGENCY_COMPETITIVE_MAX:
        return ACTION_AGGRESSIVE
    if score + 1e-12 >= URGENCY_PASSIVE_MAX:
        return ACTION_COMPETITIVE
    return ACTION_PASSIVE


def maker_exit_ev(
    *,
    spread_bps: float,
    fee_bps: float,
    expected_adverse_bps: float,
    urgency: float,
) -> float:
    """Expected maker-exit edge. Higher urgency waits less and captures less."""
    capture = max(0.0, _finite(spread_bps)) * (0.35 + 0.45 * (1.0 - _clip01(urgency)))
    wait_adverse = max(0.0, _finite(expected_adverse_bps)) * (0.40 + 0.80 * _clip01(urgency))
    return capture - 0.5 * _finite(fee_bps) - wait_adverse


def maker_exit_price(
    *,
    bid: float,
    ask: float,
    long_position: bool,
    action: str,
    tick_size: float,
) -> float:
    """Passive joins deeper, competitive joins touch, aggressive improves inside."""
    tick = max(_finite(tick_size, 1e-9), 1e-9)
    bid_px = _finite(bid)
    ask_px = _finite(ask)
    token = str(action or ACTION_COMPETITIVE).upper()
    if long_position:
        if token == ACTION_PASSIVE:
            return ask_px + 2.0 * tick
        if token == ACTION_AGGRESSIVE:
            return max(bid_px + tick, bid_px)
        return ask_px
    if token == ACTION_PASSIVE:
        return max(tick, bid_px - 2.0 * tick)
    if token == ACTION_AGGRESSIVE:
        return max(tick, ask_px - tick)
    return bid_px


def inventory_should_manage(
    *,
    inventory_ratio: float,
    inventory_age: float,
    unrealized_pnl: float | None,
    band: str | None = None,
    close_threshold: float = 0.95,
    realize_age_ticks: float = 8.0,
    profit_realize_bps: float = 2.0,
    toxic_realize_bps: float = 10.0,
) -> bool:
    token = str(band or "").upper()
    if token in {"FLAT"}:
        return False
    if token in {"MAX_LONG", "MAX_SHORT"}:
        return True
    if abs(_finite(inventory_ratio)) + 1e-12 >= max(0.0, float(close_threshold)):
        return True
    if _finite(inventory_age) + 1e-12 >= max(1.0, float(realize_age_ticks)):
        return True
    upnl = unrealized_pnl
    if upnl is None:
        return False
    pnl = _finite(upnl)
    if pnl + 1e-12 >= float(profit_realize_bps):
        return True
    if pnl - 1e-12 <= -abs(float(toxic_realize_bps)):
        return True
    return False


@dataclass(frozen=True)
class RealizationDecision:
    book: int
    inventory: float
    inventory_age: float
    exit_urgency: float
    state: str
    action: str
    selected_action: str
    maker_exit_ev: float
    taker_exit_cost: float
    taker_risk: float
    taker_allowed: bool
    holding_risk: float
    adverse_move: float
    opportunity_cost: float
    trigger: str

    def as_log(self) -> dict[str, Any]:
        return {
            "book": int(self.book),
            "inventory": self.inventory,
            "inventory_age": self.inventory_age,
            "exit_urgency": self.exit_urgency,
            "state": self.state,
            "maker_exit_ev": self.maker_exit_ev,
            "taker_exit_cost": self.taker_exit_cost,
            "selected_action": self.selected_action,
            "taker_allowed": int(bool(self.taker_allowed)),
            "taker_risk": self.taker_risk,
            "trigger": self.trigger,
        }


def evaluate_realization(
    *,
    book: int = 0,
    inventory_size: float,
    inventory_ratio: float,
    inventory_age: float,
    unrealized_pnl: float | None = None,
    expected_markout: float = 0.0,
    volatility: float = 0.0,
    imbalance: float = 0.0,
    ofi: float | None = None,
    kappa_need: float | None = None,
    observations_remaining: int = 0,
    volume_cap_headroom: float = 1.0,
    recent_realized_pnl: float | None = None,
    adverse_selection_risk: float = 0.0,
    fee_bps: float = 1.0,
    spread_bps: float = 2.0,
    slippage_bps: float = 3.0,
    band: str | None = None,
    stop_loss_hit: bool = False,
    hard_emergency: bool = False,
    inventory_sign: float | None = None,
    risk_scale_bps: float = 12.0,
) -> RealizationDecision:
    sign = _finite(inventory_sign, 0.0)
    if sign == 0.0:
        sign = 1.0 if _finite(inventory_size) >= 0.0 else -1.0
        if _finite(inventory_ratio) < 0.0:
            sign = -1.0
    need = (
        _clip01(kappa_need)
        if kappa_need is not None
        else kappa_completion_need(observations_remaining, unrealized_pnl)
    )
    holding = inventory_holding_risk(
        inventory_ratio=inventory_ratio,
        volatility=volatility,
        inventory_age=inventory_age,
    )
    adverse = expected_adverse_move(
        expected_markout_bps=expected_markout,
        ofi=ofi,
        imbalance=imbalance,
        inventory_sign=sign,
        volatility=volatility,
    )
    opportunity = inventory_opportunity_cost(
        volume_cap_headroom=volume_cap_headroom,
        kappa_need=need,
        recent_realized_pnl=recent_realized_pnl,
    )
    urgency = exit_urgency(
        inventory_size=abs(_finite(inventory_size)),
        inventory_ratio=inventory_ratio,
        inventory_age=inventory_age,
        unrealized_pnl=unrealized_pnl,
        expected_markout=expected_markout,
        volatility=volatility,
        imbalance=imbalance,
        ofi=ofi,
        inventory_sign=sign,
        kappa_need=need,
        volume_cap_headroom=volume_cap_headroom,
        recent_realized_pnl=recent_realized_pnl,
        adverse_selection_risk=adverse_selection_risk,
    )
    state = classify_inventory_state(
        urgency=urgency,
        inventory_ratio=inventory_ratio,
        band=band,
        stop_loss_hit=stop_loss_hit,
        hard_emergency=hard_emergency,
    )
    proposed = classify_exit_action(urgency)
    taker_ok, risk, cost = selective_taker_allowed(
        holding_risk=holding,
        adverse_move=adverse,
        opportunity_cost=opportunity,
        fee_bps=fee_bps,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        risk_scale_bps=risk_scale_bps,
    )
    hard_taker = bool(stop_loss_hit or hard_emergency or str(band or "").upper() in {
        "MAX_LONG", "MAX_SHORT",
    })
    if proposed == ACTION_TAKER:
        if taker_ok or hard_taker:
            action = ACTION_TAKER
            trigger = "EMERGENCY_HARD" if hard_taker and not taker_ok else "TAKER_RISK_EXCEEDS_COST"
        else:
            action = ACTION_AGGRESSIVE
            trigger = "TAKER_REJECTED_COST"
    elif state == "EMERGENCY":
        if taker_ok or hard_taker:
            action = ACTION_TAKER
            trigger = "EMERGENCY_REDUCTION"
        else:
            action = ACTION_AGGRESSIVE
            trigger = "EMERGENCY_MAKER"
    else:
        action = proposed
        trigger = "MAKER_LADDER"

    maker_ev = maker_exit_ev(
        spread_bps=spread_bps,
        fee_bps=fee_bps,
        expected_adverse_bps=max(0.0, -_finite(expected_markout)),
        urgency=urgency,
    )
    return RealizationDecision(
        book=int(book),
        inventory=abs(_finite(inventory_size)),
        inventory_age=_finite(inventory_age),
        exit_urgency=urgency,
        state=state,
        action=action,
        selected_action=action,
        maker_exit_ev=maker_ev,
        taker_exit_cost=cost,
        taker_risk=risk,
        taker_allowed=bool(taker_ok or (hard_taker and action == ACTION_TAKER)),
        holding_risk=holding,
        adverse_move=adverse,
        opportunity_cost=opportunity,
        trigger=trigger,
    )
