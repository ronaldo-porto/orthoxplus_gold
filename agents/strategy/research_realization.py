# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 1.4: ExitUrgency and selective realization.

Pure functions so unit tests do not import Strategy1 / bittensor.

Goal: turn profitable inventory into completed round trips earlier
(UID27-style throughput) without oversized inventory or burst taker dumps.

Avellaneda-Stoikov / GLFT: inventory risk grows with q^2 * sigma^2 and
remaining hold time. ExitUrgency V2 is continuous and named-component.
Urgency selects a hybrid realization ladder rung. Crossing still
requires hybrid economics or hard safety; high urgency is only
taker-eligible.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from research_exit_urgency import (
    EXIT_URGENCY_VERSION,
    ExitUrgencyBreakdown,
    compute_exit_urgency_v1,
    compute_exit_urgency_v2,
)
from research_fill_hazard import HazardPrediction
from research_hybrid import (
    REASON_MAKER,
    REASON_REJECT_COST,
    REASON_REJECT_LADDER,
    REASON_REJECT_TRANSITION,
    hybrid_taker_decision,
    taker_crossing_cost_bps,
)
from research_exit_hazard_ev import MakerTakerExitEV
from research_action_utility import SN79ActionUtilityDecision
from research_taker_economics import (
    REASON_REJECT_ECONOMICS,
    REASON_REJECT_NET_FLOOR,
    TakerEconomicsDecision,
    evaluate_taker_economics,
)
from research_realization_ladder import (
    DEFAULT_LADDER_AGGRESSIVE_MAX,
    DEFAULT_LADDER_COMPETITIVE_MAX,
    DEFAULT_LADDER_PASSIVE_MAX,
    RealizationLadderBands,
    RealizationRung,
    apply_realization_ladder,
    clamp_ladder_bands,
    classify_realization_rung,
)
from research_inventory_state import (
    INVENTORY_STATES,
    apply_exit_action_for_state,
    classify_inventory_state,
)
from research_kappa_realization import kappa_realization_boost

ACTION_PASSIVE = "PASSIVE_MAKER_EXIT"
ACTION_COMPETITIVE = "COMPETITIVE_MAKER_EXIT"
ACTION_AGGRESSIVE = "AGGRESSIVE_MAKER_EXIT"
ACTION_TAKER = "SELECTIVE_TAKER_EXIT"

MAKER_ACTIONS = (ACTION_PASSIVE, ACTION_COMPETITIVE, ACTION_AGGRESSIVE)

URGENCY_PASSIVE_MAX = DEFAULT_LADDER_PASSIVE_MAX
URGENCY_COMPETITIVE_MAX = DEFAULT_LADDER_COMPETITIVE_MAX
URGENCY_AGGRESSIVE_MAX = DEFAULT_LADDER_AGGRESSIVE_MAX
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
    """Profitable one/two-away pressure. Zero when qualified or clearly bad."""
    return kappa_realization_boost(
        observations_remaining=observations_remaining,
        unrealized_pnl_bps=unrealized_pnl_bps,
    ).boost


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


def exit_urgency_breakdown(
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
    realization_failed: bool | None = None,
    size_ref: float = 0.50,
    age_ref: float = 20.0,
    use_v2: bool = True,
) -> ExitUrgencyBreakdown:
    """Named-component ExitUrgency V2, or the V1 size/age/drawdown mix."""
    fn = compute_exit_urgency_v2 if bool(use_v2) else compute_exit_urgency_v1
    return fn(
        inventory_size=inventory_size,
        inventory_ratio=inventory_ratio,
        inventory_age=inventory_age,
        unrealized_pnl=unrealized_pnl,
        expected_markout=expected_markout,
        volatility=volatility,
        imbalance=imbalance,
        ofi=ofi,
        inventory_sign=inventory_sign,
        kappa_need=kappa_need,
        volume_cap_headroom=volume_cap_headroom,
        recent_realized_pnl=recent_realized_pnl,
        adverse_selection_risk=adverse_selection_risk,
        realization_failed=realization_failed,
        size_ref=size_ref,
        age_ref=age_ref,
    )


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
    realization_failed: bool | None = None,
    size_ref: float = 0.50,
    age_ref: float = 20.0,
    use_v2: bool = True,
) -> float:
    """Continuous [0, 1] realization pressure. High score is not a taker."""
    return exit_urgency_breakdown(
        inventory_size=inventory_size,
        inventory_ratio=inventory_ratio,
        inventory_age=inventory_age,
        unrealized_pnl=unrealized_pnl,
        expected_markout=expected_markout,
        volatility=volatility,
        imbalance=imbalance,
        ofi=ofi,
        inventory_sign=inventory_sign,
        kappa_need=kappa_need,
        volume_cap_headroom=volume_cap_headroom,
        recent_realized_pnl=recent_realized_pnl,
        adverse_selection_risk=adverse_selection_risk,
        realization_failed=realization_failed,
        size_ref=size_ref,
        age_ref=age_ref,
        use_v2=use_v2,
    ).urgency


def classify_exit_action(
    urgency: float,
    bands: RealizationLadderBands | None = None,
) -> str:
    """Map urgency onto the Research ladder. Top rung is taker-eligible."""
    return classify_realization_rung(urgency, bands).proposed_action


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
    hybrid_reason: str = REASON_MAKER
    taker_lock_pnl_bps: float = 0.0
    taker_crossing_cost_bps: float = 0.0
    taker_qty_frac: float = 0.0
    maker_fill_hazard: float | None = None
    urgency_breakdown: ExitUrgencyBreakdown | None = None
    ladder_rung: RealizationRung | None = None
    taker_eligible: bool = False
    proposed_rung: str = ACTION_PASSIVE
    taker_economics: TakerEconomicsDecision | None = None
    kappa_realization: Any = None
    maker_taker_ev: MakerTakerExitEV | None = None
    action_utility: SN79ActionUtilityDecision | None = None
    economic_taker_authorized: bool = False
    score_taker_authorized: bool = False
    risk_taker_authorized: bool = False
    aggressive_positive_ev_taker_authorized: bool = False
    aggressive_positive_ev_trigger: str = ""
    aggressive_positive_ev_advantage_bps: float = 0.0
    aggressive_positive_ev_switch_margin_bps: float = 0.0
    aggressive_positive_ev_floor_bps: float = 0.0
    direct_taker_authorized: bool = False
    taker_authority: str = "NONE"
    allowed_loss_floor_bps: float = -2.0
    economic_direct_max_loss_bps: float = -20.0
    failed_exit_count: int = 0
    time_since_first_exit_attempt: float = 0.0
    unified_exit: Any = None

    def as_log(self) -> dict[str, Any]:
        payload = {
            "book": int(self.book),
            "inventory": self.inventory,
            "inventory_age": self.inventory_age,
            "exit_urgency": self.exit_urgency,
            "exit_urgency_version": EXIT_URGENCY_VERSION,
            "proposed_rung": self.proposed_rung,
            "taker_eligible": int(bool(self.taker_eligible)),
            "economic_taker_authorized": int(bool(self.economic_taker_authorized)),
            "score_taker_authorized": int(bool(self.score_taker_authorized)),
            "risk_taker_authorized": int(bool(self.risk_taker_authorized)),
            "aggressive_positive_ev_taker_authorized": int(bool(self.aggressive_positive_ev_taker_authorized)),
            "aggressive_positive_ev_trigger": self.aggressive_positive_ev_trigger,
            "aggressive_positive_ev_advantage_bps": self.aggressive_positive_ev_advantage_bps,
            "aggressive_positive_ev_switch_margin_bps": self.aggressive_positive_ev_switch_margin_bps,
            "aggressive_positive_ev_floor_bps": self.aggressive_positive_ev_floor_bps,
            "direct_taker_authorized": int(bool(self.direct_taker_authorized)),
            "taker_authority": self.taker_authority,
            "allowed_loss_floor_bps": self.allowed_loss_floor_bps,
            "economic_direct_max_loss_bps": self.economic_direct_max_loss_bps,
            "failed_exit_count": int(self.failed_exit_count),
            "time_since_first_exit_attempt": self.time_since_first_exit_attempt,
            "state": self.state,
            "maker_exit_ev": self.maker_exit_ev,
            "taker_exit_cost": self.taker_exit_cost,
            "selected_action": self.selected_action,
            "taker_allowed": int(bool(self.taker_allowed)),
            "taker_risk": self.taker_risk,
            "trigger": self.trigger,
            "hybrid_reason": self.hybrid_reason,
            "taker_lock_pnl_bps": self.taker_lock_pnl_bps,
            "taker_crossing_cost_bps": self.taker_crossing_cost_bps,
            "taker_qty_frac": self.taker_qty_frac,
            "maker_fill_hazard": self.maker_fill_hazard,
        }
        if self.urgency_breakdown is not None:
            payload.update(self.urgency_breakdown.as_log())
        if self.ladder_rung is not None:
            payload.update(self.ladder_rung.as_log())
        if self.taker_economics is not None:
            payload.update(self.taker_economics.as_log())
        if self.kappa_realization is not None:
            payload.update(self.kappa_realization.as_log())
        if self.maker_taker_ev is not None:
            payload.update(self.maker_taker_ev.as_log())
        if self.action_utility is not None:
            payload.update(self.action_utility.as_log())
        if self.unified_exit is not None and hasattr(self.unified_exit, "as_log"):
            payload.update(self.unified_exit.as_log())
        return payload


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
    required_observations: int = 3,
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
    maker_fill_hazard: float | None = None,
    hazard: HazardPrediction | None = None,
    enable_hybrid: bool = True,
    min_lock_bps: float = 1.0,
    maker_ev_gap_bps: float = 0.50,
    stale_age_ticks: float = 16.0,
    min_maker_fill: float = 0.15,
    volume_capped: bool = False,
    dust: bool = False,
    transition_quarantine: bool = False,
    realization_failed: bool | None = None,
    ladder_bands: RealizationLadderBands | None = None,
    net_floor_bps: float = 0.0,
    use_exit_urgency_v2: bool = True,
    use_fill_hazard_ev: bool = True,
    allow_economic_taker: bool = True,
    enable_sn79_action_utility: bool = True,
    allow_score_taker_direct: bool = True,
    allow_economic_taker_direct: bool = True,
    economic_direct_max_loss_bps: float = -20.0,
    allow_risk_taker_direct: bool = True,
    risk_direct_max_loss_bps: float = -25.0,
    risk_direct_min_age_ticks: float = 24.0,
    risk_direct_failed_exit_count: int = 3,
    risk_direct_min_ev_advantage_bps: float = 1.0,
    allow_aggressive_positive_ev_taker: bool = True,
    aggressive_positive_ev_min_net_bps: float = 0.0,
    aggressive_positive_ev_switch_margin_bps: float = 0.50,
    aggressive_positive_ev_one_away_margin_bps: float = 0.0,
    aggressive_positive_ev_failed_exit_count: int = 8,
    aggressive_positive_ev_min_age_ticks: float = 16.0,
    aggressive_positive_ev_max_maker_fill: float = 0.05,
    aggressive_positive_ev_min_urgency: float = 0.30,
    failed_exit_count: int = 0,
    time_since_first_exit_attempt: float = 0.0,
    maker_escalate_failed_exit_count: int = 8,
    one_away_maker_escalate_failed_exit_count: int = 3,
    failed_exit_penalty_bps: float = 0.75,
    exit_age_penalty_bps_per_tick: float = 0.03,
    sn79_pnl_scale_bps: float = 8.0,
    sn79_pnl_weight: float = 1.0,
    sn79_round_trip_weight: float = 0.30,
    sn79_kappa_weight: float = 0.35,
    sn79_coverage_weight: float = 0.15,
    sn79_capital_release_weight: float = 0.15,
    sn79_risk_reduction_weight: float = 0.20,
    sn79_velocity_weight: float = 0.25,
    sn79_downside_weight: float = 0.45,
    sn79_min_utility_margin: float = 0.03,
    sn79_max_score_subsidy_loss_bps: float = -2.0,
    sn79_one_away_loss_floor_bps: float = -8.0,
    sn79_two_away_loss_floor_bps: float = -6.0,
    sn79_uncovered_loss_floor_bps: float = -5.0,
) -> RealizationDecision:
    sign = _finite(inventory_sign, 0.0)
    if sign == 0.0:
        sign = 1.0 if _finite(inventory_size) >= 0.0 else -1.0
        if _finite(inventory_ratio) < 0.0:
            sign = -1.0
    crossing = taker_crossing_cost_bps(
        fee_bps=fee_bps,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
    )
    policy = kappa_realization_boost(
        observations_remaining=observations_remaining,
        unrealized_pnl_bps=unrealized_pnl,
        crossing_cost_bps=crossing,
    )
    need = policy.boost
    taker_need = policy.taker_boost
    del kappa_need
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
    breakdown = exit_urgency_breakdown(
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
        realization_failed=realization_failed,
        use_v2=bool(use_exit_urgency_v2),
    )
    urgency = breakdown.urgency
    # V4.12 simple execution pressure: repeated Maker failures directly move
    # the quote toward the touch. This replaces waiting for a complicated
    # weighted urgency mix to eventually cross the AGGRESSIVE threshold.
    bands = ladder_bands if ladder_bands is not None else clamp_ladder_bands()
    failed_n = max(0, int(failed_exit_count))
    general_escalate = max(1, int(maker_escalate_failed_exit_count))
    one_away_escalate = max(1, int(one_away_maker_escalate_failed_exit_count))
    if max(0, int(observations_remaining)) == 1 and failed_n >= one_away_escalate:
        urgency = max(urgency, float(bands.competitive_max))
    elif failed_n >= general_escalate:
        urgency = max(urgency, float(bands.competitive_max))
    elif max(0, int(observations_remaining)) == 1 and failed_n > 0:
        urgency = max(urgency, float(bands.passive_max))
    elif failed_n >= max(1, general_escalate // 2):
        urgency = max(urgency, float(bands.passive_max))
    if urgency > breakdown.urgency + 1e-12:
        breakdown = replace(
            breakdown,
            urgency=urgency,
            realization_failure_pressure=max(
                float(breakdown.realization_failure_pressure), 0.75
            ),
        )
    state = classify_inventory_state(
        urgency=urgency,
        inventory_ratio=inventory_ratio,
        band=band,
        stop_loss_hit=stop_loss_hit,
        hard_emergency=hard_emergency,
        inventory_size=abs(_finite(inventory_size)),
        inventory_age=inventory_age,
        unrealized_pnl=unrealized_pnl,
        volatility=volatility,
        ofi=ofi,
        expected_markout=expected_markout,
        kappa_need=need,
        volume_cap_headroom=volume_cap_headroom,
        recent_realized_pnl=recent_realized_pnl,
        adverse_selection_risk=adverse_selection_risk,
        realization_failed=realization_failed,
        inventory_sign=sign,
    )
    rung = classify_realization_rung(urgency, bands)
    proposed = rung.proposed_action
    economics = evaluate_taker_economics(
        inventory_ratio=inventory_ratio,
        inventory_size=abs(_finite(inventory_size)),
        volatility=volatility,
        inventory_age=inventory_age,
        expected_markout=expected_markout,
        ofi=ofi,
        inventory_sign=sign,
        kappa_need=taker_need,
        volume_cap_headroom=volume_cap_headroom,
        fee_bps=fee_bps,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        unrealized_pnl=unrealized_pnl,
        stop_loss_hit=stop_loss_hit,
        band=band,
        net_floor_bps=net_floor_bps,
    )
    risk = economics.holding.expected_holding_cost
    cost = economics.taker.expected_taker_cost
    maker_ev = maker_exit_ev(
        spread_bps=spread_bps,
        fee_bps=fee_bps,
        expected_adverse_bps=max(0.0, -_finite(expected_markout)),
        urgency=urgency,
    )
    hybrid = hybrid_taker_decision(
        hard_emergency=False,
        unrealized_pnl_bps=unrealized_pnl,
        maker_exit_ev=maker_ev,
        crossing_cost_bps=crossing,
        maker_fill_hazard=maker_fill_hazard,
        hazard=hazard,
        observations_remaining=observations_remaining,
        inventory_age=inventory_age,
        urgency=urgency,
        volume_capped=bool(volume_capped) or _clip01(volume_cap_headroom) <= 0.0,
        dust=bool(dust),
        transition_quarantine=bool(transition_quarantine),
        enable_hybrid=bool(enable_hybrid),
        min_lock_bps=min_lock_bps,
        maker_ev_gap_bps=maker_ev_gap_bps,
        stale_age_ticks=stale_age_ticks,
        min_maker_fill=min_maker_fill,
        economics=economics,
        inventory_size=abs(_finite(inventory_size)),
        inventory_ratio=inventory_ratio,
        volatility=volatility,
        ofi=ofi,
        expected_markout=expected_markout,
        kappa_need=taker_need,
        volume_cap_headroom=volume_cap_headroom,
        inventory_sign=sign,
        fee_bps=fee_bps,
        spread_bps=spread_bps,
        slippage_bps=slippage_bps,
        stop_loss_hit=stop_loss_hit,
        band=band,
        net_floor_bps=net_floor_bps,
        use_fill_hazard_ev=bool(use_fill_hazard_ev),
        allow_economic_taker=bool(allow_economic_taker),
        enable_sn79_action_utility=bool(enable_sn79_action_utility),
        allow_score_taker_direct=bool(allow_score_taker_direct),
        allow_economic_taker_direct=bool(allow_economic_taker_direct),
        economic_direct_max_loss_bps=economic_direct_max_loss_bps,
        allow_risk_taker_direct=bool(allow_risk_taker_direct),
        risk_direct_max_loss_bps=risk_direct_max_loss_bps,
        risk_direct_min_age_ticks=risk_direct_min_age_ticks,
        risk_direct_failed_exit_count=risk_direct_failed_exit_count,
        risk_direct_min_ev_advantage_bps=risk_direct_min_ev_advantage_bps,
        allow_aggressive_positive_ev_taker=bool(allow_aggressive_positive_ev_taker),
        aggressive_positive_ev_min_net_bps=aggressive_positive_ev_min_net_bps,
        aggressive_positive_ev_switch_margin_bps=aggressive_positive_ev_switch_margin_bps,
        aggressive_positive_ev_one_away_margin_bps=aggressive_positive_ev_one_away_margin_bps,
        aggressive_positive_ev_failed_exit_count=aggressive_positive_ev_failed_exit_count,
        aggressive_positive_ev_min_age_ticks=aggressive_positive_ev_min_age_ticks,
        aggressive_positive_ev_max_maker_fill=aggressive_positive_ev_max_maker_fill,
        aggressive_positive_ev_min_urgency=aggressive_positive_ev_min_urgency,
        inventory_state=state,
        failed_exit_count=max(0, int(failed_exit_count)),
        time_since_first_exit_attempt=max(0.0, _finite(time_since_first_exit_attempt)),
        failed_exit_penalty_bps=failed_exit_penalty_bps,
        exit_age_penalty_bps_per_tick=exit_age_penalty_bps_per_tick,
        required_observations=max(1, int(required_observations)),
        sn79_pnl_scale_bps=sn79_pnl_scale_bps,
        sn79_pnl_weight=sn79_pnl_weight,
        sn79_round_trip_weight=sn79_round_trip_weight,
        sn79_kappa_weight=sn79_kappa_weight,
        sn79_coverage_weight=sn79_coverage_weight,
        sn79_capital_release_weight=sn79_capital_release_weight,
        sn79_risk_reduction_weight=sn79_risk_reduction_weight,
        sn79_velocity_weight=sn79_velocity_weight,
        sn79_downside_weight=sn79_downside_weight,
        sn79_min_utility_margin=sn79_min_utility_margin,
        sn79_max_score_subsidy_loss_bps=sn79_max_score_subsidy_loss_bps,
        sn79_one_away_loss_floor_bps=sn79_one_away_loss_floor_bps,
        sn79_two_away_loss_floor_bps=sn79_two_away_loss_floor_bps,
        sn79_uncovered_loss_floor_bps=sn79_uncovered_loss_floor_bps,
    )
    # V4.8: bounded SCORE / ECONOMIC / RISK authorities are independent of
    # maker-rung urgency. The ladder is now fallback maker aggressiveness.
    direct_taker_authorized = bool(getattr(hybrid, "direct_authorized", False))
    action, trigger = apply_realization_ladder(
        rung=rung,
        hybrid_take=bool(hybrid.take),
        hybrid_reason=hybrid.reason,
        hard_safety=bool(economics.catastrophic),
        direct_taker_authorized=direct_taker_authorized,
        transition_quarantine=bool(transition_quarantine),
        cost=cost,
        risk=risk,
        state=state,
    )
    if not (bool(transition_quarantine) or hybrid.reason == REASON_REJECT_TRANSITION):
        action, state_trigger = apply_exit_action_for_state(
            state=state,
            selected_action=action,
            hard_safety=bool(economics.catastrophic),
            economic_ok=bool(hybrid.take),
        )
        if state_trigger and trigger not in {
            REASON_REJECT_LADDER,
            REASON_REJECT_COST,
            REASON_REJECT_TRANSITION,
            REASON_REJECT_ECONOMICS,
            REASON_REJECT_NET_FLOOR,
        }:
            trigger = state_trigger
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
        taker_allowed=bool(action == ACTION_TAKER),
        holding_risk=holding,
        adverse_move=adverse,
        opportunity_cost=opportunity,
        trigger=trigger,
        hybrid_reason=hybrid.reason,
        taker_lock_pnl_bps=hybrid.lock_pnl_bps,
        taker_crossing_cost_bps=hybrid.crossing_cost_bps,
        taker_qty_frac=hybrid.qty_frac if action == ACTION_TAKER else 0.0,
        maker_fill_hazard=maker_fill_hazard,
        urgency_breakdown=breakdown,
        ladder_rung=rung,
        taker_eligible=bool(rung.taker_eligible),
        proposed_rung=proposed,
        taker_economics=economics,
        kappa_realization=policy,
        maker_taker_ev=hybrid.maker_taker_ev,
        action_utility=hybrid.action_utility,
        economic_taker_authorized=bool(getattr(hybrid, "economic_authorized", False)),
        score_taker_authorized=bool(getattr(hybrid, "score_authorized", False)),
        risk_taker_authorized=bool(getattr(hybrid, "risk_authorized", False)),
        aggressive_positive_ev_taker_authorized=bool(
            getattr(hybrid, "aggressive_positive_ev_authorized", False)
        ),
        aggressive_positive_ev_trigger=str(
            getattr(hybrid, "aggressive_positive_ev_trigger", "") or ""
        ),
        aggressive_positive_ev_advantage_bps=float(
            getattr(hybrid, "aggressive_positive_ev_advantage_bps", 0.0) or 0.0
        ),
        aggressive_positive_ev_switch_margin_bps=float(
            getattr(hybrid, "aggressive_positive_ev_switch_margin_bps", 0.0) or 0.0
        ),
        aggressive_positive_ev_floor_bps=float(
            getattr(hybrid, "aggressive_positive_ev_floor_bps", 0.0) or 0.0
        ),
        direct_taker_authorized=direct_taker_authorized,
        taker_authority=str(getattr(hybrid, "taker_authority", "NONE") or "NONE"),
        allowed_loss_floor_bps=float(getattr(hybrid, "allowed_loss_floor_bps", -2.0)),
        economic_direct_max_loss_bps=float(
            getattr(hybrid, "economic_direct_max_loss_bps", economic_direct_max_loss_bps)
        ),
        failed_exit_count=max(0, int(failed_exit_count)),
        time_since_first_exit_attempt=max(0.0, _finite(time_since_first_exit_attempt)),
    )
