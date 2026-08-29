# SPDX-License-Identifier: MIT
"""Research hybrid maker + taker realization.

Hybrid maker + taker realization for SN79.

Maker edge, immediate realized PnL, round-trip completion, Kappa breadth,
coverage rotation, inventory release, and downside are evaluated together.
Taker remains an inventory-reducing exit tool only; it must never open or
increase inventory.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_taker_economics import (
    REASON_CATASTROPHIC,
    REASON_HOLDING_EXCEEDS_COST,
    TakerEconomicsDecision,
    evaluate_taker_economics,
)
from research_fill_hazard import HazardPrediction
from research_action_utility import (
    REASON_SN79_TAKER,
    SN79ActionUtilityDecision,
    evaluate_sn79_action_utility,
)
from research_exit_hazard_ev import (
    EXIT_HAZARD_EV_VERSION,
    MakerTakerExitEV,
    REASON_MAKER_EV,
    REASON_TAKER_EV,
    compare_maker_taker_exit,
)

REASON_MAKER = "MAKER_LADDER"
REASON_LOCK_PROFIT = "TAKER_LOCK_PROFIT"
REASON_AVOID_ADVERSE = "TAKER_RISK_EXCEEDS_COST"
REASON_KAPPA = "TAKER_KAPPA_COMPLETE"
REASON_STALE = "TAKER_STALE_MAKER"
REASON_EMERGENCY_HARD = "EMERGENCY_HARD"
REASON_EMERGENCY_REDUCTION = "EMERGENCY_REDUCTION"
REASON_REJECT_COST = "TAKER_REJECTED_COST"
REASON_REJECT_CAP = "TAKER_REJECTED_VOLUME_CAP"
REASON_REJECT_DUST = "TAKER_REJECTED_DUST"
REASON_REJECT_TRANSITION = "TAKER_REJECTED_TRANSITION"
REASON_REJECT_LADDER = "TAKER_REJECTED_LADDER"
REASON_AGGRESSIVE_POSITIVE_EV = "TAKER_AGGRESSIVE_POSITIVE_EV"

TAKER_AUTH_NONE = "NONE"
TAKER_AUTH_SCORE = "SCORE"
TAKER_AUTH_ECONOMIC = "ECONOMIC"
TAKER_AUTH_RISK = "RISK"

TAKER_REASONS = (
    REASON_HOLDING_EXCEEDS_COST,
    REASON_CATASTROPHIC,
    REASON_LOCK_PROFIT,
    REASON_AVOID_ADVERSE,
    REASON_KAPPA,
    REASON_STALE,
    REASON_EMERGENCY_HARD,
    REASON_EMERGENCY_REDUCTION,
    REASON_TAKER_EV,
    REASON_SN79_TAKER,
    REASON_AGGRESSIVE_POSITIVE_EV,
)


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


def taker_crossing_cost_bps(
    *,
    fee_bps: float,
    spread_bps: float,
    slippage_bps: float,
) -> float:
    """One-way close cost: fee + half-spread + slippage."""
    return max(
        0.0,
        _finite(fee_bps) + 0.5 * max(0.0, _finite(spread_bps)) + _finite(slippage_bps),
    )


def taker_lock_pnl_bps(
    *,
    unrealized_pnl_bps: float | None,
    crossing_cost_bps: float,
) -> float:
    """Net bps locked by taking the current mark, after crossing cost."""
    if unrealized_pnl_bps is None:
        return -abs(_finite(crossing_cost_bps))
    return _finite(unrealized_pnl_bps) - max(0.0, _finite(crossing_cost_bps))


def maker_fill_unreliable(
    maker_fill_hazard: float | None,
    *,
    min_fill: float = 0.15,
) -> bool:
    """True only when a usable hazard says the maker exit is unlikely."""
    if maker_fill_hazard is None:
        return False
    fill = _finite(maker_fill_hazard, default=-1.0)
    if fill < 0.0:
        return False
    return fill + 1e-12 < max(0.0, float(min_fill))


def hybrid_taker_qty_frac(
    *,
    reason: str,
    urgency: float,
    lock_pnl_bps: float,
    emergency: bool = False,
) -> float:
    """Partial take by default. Full size only for catastrophic override."""
    token = str(reason or "")
    if emergency or token in {
        REASON_EMERGENCY_HARD, REASON_EMERGENCY_REDUCTION, REASON_CATASTROPHIC,
    }:
        return 1.0
    if token == REASON_AGGRESSIVE_POSITIVE_EV:
        return 0.90
    if token == REASON_HOLDING_EXCEEDS_COST or token == REASON_TAKER_EV:
        extra = 0.25 * math.tanh(max(0.0, _finite(lock_pnl_bps)) / 8.0)
        return _clip01(0.50 + extra)
    if token == REASON_KAPPA:
        return 0.50
    if token == REASON_STALE:
        return 0.55
    if token == REASON_LOCK_PROFIT:
        extra = 0.25 * math.tanh(max(0.0, _finite(lock_pnl_bps)) / 8.0)
        return _clip01(0.45 + extra)
    if token == REASON_AVOID_ADVERSE:
        return _clip01(0.55 + 0.35 * _clip01(urgency))
    return 0.0


@dataclass(frozen=True)
class HybridTakerDecision:
    take: bool
    reason: str
    lock_pnl_bps: float
    crossing_cost_bps: float
    maker_exit_ev: float
    qty_frac: float
    maker_fill_hazard: float | None
    economics: TakerEconomicsDecision | None = None
    maker_taker_ev: MakerTakerExitEV | None = None
    action_utility: SN79ActionUtilityDecision | None = None
    economic_authorized: bool = False
    score_authorized: bool = False
    risk_authorized: bool = False
    aggressive_positive_ev_authorized: bool = False
    aggressive_positive_ev_trigger: str = ""
    aggressive_positive_ev_advantage_bps: float = 0.0
    aggressive_positive_ev_switch_margin_bps: float = 0.0
    aggressive_positive_ev_floor_bps: float = 0.0
    direct_authorized: bool = False
    taker_authority: str = TAKER_AUTH_NONE
    allowed_loss_floor_bps: float = -2.0
    economic_direct_max_loss_bps: float = -20.0
    failed_exit_count: int = 0
    time_since_first_exit_attempt: float = 0.0

    def as_log(self) -> dict[str, Any]:
        payload = {
            "hybrid_take": int(bool(self.take)),
            "hybrid_reason": self.reason,
            "taker_lock_pnl_bps": self.lock_pnl_bps,
            "taker_crossing_cost_bps": self.crossing_cost_bps,
            "maker_exit_ev": self.maker_exit_ev,
            "taker_qty_frac": self.qty_frac,
            "maker_fill_hazard": self.maker_fill_hazard,
            "exit_hazard_ev_version": EXIT_HAZARD_EV_VERSION,
            "economic_taker_authorized": int(bool(self.economic_authorized)),
            "score_taker_authorized": int(bool(self.score_authorized)),
            "risk_taker_authorized": int(bool(self.risk_authorized)),
            "aggressive_positive_ev_taker_authorized": int(bool(self.aggressive_positive_ev_authorized)),
            "aggressive_positive_ev_trigger": self.aggressive_positive_ev_trigger,
            "aggressive_positive_ev_advantage_bps": self.aggressive_positive_ev_advantage_bps,
            "aggressive_positive_ev_switch_margin_bps": self.aggressive_positive_ev_switch_margin_bps,
            "aggressive_positive_ev_floor_bps": self.aggressive_positive_ev_floor_bps,
            "direct_taker_authorized": int(bool(self.direct_authorized)),
            "taker_authority": self.taker_authority,
            "allowed_loss_floor_bps": self.allowed_loss_floor_bps,
            "economic_direct_max_loss_bps": self.economic_direct_max_loss_bps,
            "failed_exit_count": int(self.failed_exit_count),
            "time_since_first_exit_attempt": self.time_since_first_exit_attempt,
            "wait_ev": (
                self.maker_taker_ev.expected_maker_exit_value
                if self.maker_taker_ev is not None else 0.0
            ),
            "taker_ev": (
                self.maker_taker_ev.expected_taker_exit_value
                if self.maker_taker_ev is not None else 0.0
            ),
            "aggressive_maker_ev": (
                self.maker_taker_ev.expected_maker_exit_value
                if self.maker_taker_ev is not None else self.maker_exit_ev
            ),
        }
        if self.economics is not None:
            payload.update(self.economics.as_log())
        if self.maker_taker_ev is not None:
            payload.update(self.maker_taker_ev.as_log())
        if self.action_utility is not None:
            payload.update(self.action_utility.as_log())
        return payload


def _reject(
    reason: str,
    lock: float,
    crossing: float,
    maker_ev: float,
    maker_fill_hazard: float | None,
    economics: TakerEconomicsDecision | None = None,
    maker_taker_ev: MakerTakerExitEV | None = None,
    action_utility: SN79ActionUtilityDecision | None = None,
) -> HybridTakerDecision:
    return HybridTakerDecision(
        False, reason, lock, crossing, maker_ev, 0.0, maker_fill_hazard, economics,
        maker_taker_ev, action_utility,
    )


def hybrid_taker_decision(
    *,
    hard_emergency: bool = False,
    adverse_allowed: bool = False,
    unrealized_pnl_bps: float | None = None,
    maker_exit_ev: float = 0.0,
    crossing_cost_bps: float = 0.0,
    maker_fill_hazard: float | None = None,
    observations_remaining: int = 0,
    inventory_age: float = 0.0,
    urgency: float = 0.0,
    volume_capped: bool = False,
    dust: bool = False,
    transition_quarantine: bool = False,
    enable_hybrid: bool = True,
    min_lock_bps: float = 1.0,
    maker_ev_gap_bps: float = 0.50,
    stale_age_ticks: float = 16.0,
    min_maker_fill: float = 0.15,
    economics: TakerEconomicsDecision | None = None,
    inventory_size: float = 0.0,
    inventory_ratio: float = 0.0,
    volatility: float = 0.0,
    ofi: float | None = None,
    expected_markout: float = 0.0,
    kappa_need: float = 0.0,
    volume_cap_headroom: float = 1.0,
    inventory_sign: float = 0.0,
    fee_bps: float = 0.0,
    spread_bps: float = 0.0,
    slippage_bps: float = 0.0,
    stop_loss_hit: bool = False,
    band: str | None = None,
    net_floor_bps: float = 0.0,
    hazard: HazardPrediction | None = None,
    use_fill_hazard_ev: bool = True,
    allow_economic_taker: bool = True,
    enable_sn79_action_utility: bool = True,
    required_observations: int = 3,
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
    allow_score_taker_direct: bool = True,
    allow_economic_taker_direct: bool = True,
    economic_direct_max_loss_bps: float = -20.0,
    risk_direct_max_loss_bps: float = -25.0,
    allow_aggressive_positive_ev_taker: bool = True,
    aggressive_positive_ev_min_net_bps: float = 0.0,
    aggressive_positive_ev_switch_margin_bps: float = 0.50,
    aggressive_positive_ev_one_away_margin_bps: float = 0.0,
    aggressive_positive_ev_failed_exit_count: int = 8,
    aggressive_positive_ev_min_age_ticks: float = 16.0,
    aggressive_positive_ev_max_maker_fill: float = 0.08,
    aggressive_positive_ev_min_urgency: float = 0.30,
    inventory_state: str = "NORMAL",
    failed_exit_count: int = 0,
    time_since_first_exit_attempt: float = 0.0,
    failed_exit_penalty_bps: float = 0.75,
    exit_age_penalty_bps_per_tick: float = 0.03,
) -> HybridTakerDecision:
    """Evaluate bounded SCORE / ECONOMIC Taker authorities plus catastrophic hard risk.

    Legacy direct RISK_TAKER authority was permanently disabled and is removed.
    Current risk exits are owned by the unified exit / inventory-liveness layer;
    catastrophic hard-risk reduction remains here as an unconditional safety path.
    """
    del adverse_allowed, min_lock_bps, maker_ev_gap_bps, stale_age_ticks
    del min_maker_fill, hard_emergency
    crossing = max(0.0, _finite(crossing_cost_bps))
    lock = taker_lock_pnl_bps(
        unrealized_pnl_bps=unrealized_pnl_bps,
        crossing_cost_bps=crossing,
    )
    maker_ev = _finite(maker_exit_ev)
    econ = economics
    if econ is None:
        econ = evaluate_taker_economics(
            inventory_ratio=inventory_ratio,
            inventory_size=inventory_size,
            volatility=volatility,
            inventory_age=inventory_age,
            expected_markout=expected_markout,
            ofi=ofi,
            inventory_sign=inventory_sign,
            kappa_need=kappa_need,
            volume_cap_headroom=volume_cap_headroom,
            fee_bps=fee_bps,
            spread_bps=spread_bps,
            slippage_bps=slippage_bps,
            unrealized_pnl=unrealized_pnl_bps,
            stop_loss_hit=stop_loss_hit,
            band=band,
            net_floor_bps=net_floor_bps,
            min_taker_cost_bps=crossing,
        )
    catastrophic = bool(econ.catastrophic)
    holding_cost = 0.0
    taker_cost = crossing
    if econ.holding is not None:
        holding_cost = float(econ.holding.expected_holding_cost)
    if econ.taker is not None:
        taker_cost = max(taker_cost, float(econ.taker.expected_taker_cost))
    immediate = 0.0 if unrealized_pnl_bps is None else _finite(unrealized_pnl_bps)
    comparison = compare_maker_taker_exit(
        prediction=hazard,
        scalar_fill=maker_fill_hazard,
        maker_profit=maker_ev,
        holding_cost=holding_cost,
        immediate_realization_value=immediate,
        taker_cost=taker_cost,
        failed_exit_count=max(0, int(failed_exit_count)),
        inventory_age=max(0.0, _finite(time_since_first_exit_attempt, inventory_age)),
        failed_exit_penalty_bps=failed_exit_penalty_bps,
        age_penalty_bps_per_tick=exit_age_penalty_bps_per_tick,
    )

    score_floor = 0.0
    action_utility = None
    if enable_sn79_action_utility:
        action_utility = evaluate_sn79_action_utility(
            taker_net_pnl_bps=comparison.expected_taker_exit_value,
            maker_expected_pnl_bps=comparison.expected_maker_exit_value,
            p_maker_fill_horizon=comparison.p_fill_horizon,
            observations_remaining=observations_remaining,
            required_observations=required_observations,
            inventory_ratio=inventory_ratio,
            holding_cost_bps=holding_cost,
            exit_urgency=urgency,
            volume_cap_headroom=volume_cap_headroom,
            pnl_scale_bps=sn79_pnl_scale_bps,
            pnl_weight=sn79_pnl_weight,
            round_trip_weight=sn79_round_trip_weight,
            kappa_weight=sn79_kappa_weight,
            coverage_weight=sn79_coverage_weight,
            capital_release_weight=sn79_capital_release_weight,
            risk_reduction_weight=sn79_risk_reduction_weight,
            velocity_weight=sn79_velocity_weight,
            downside_weight=sn79_downside_weight,
            min_utility_margin=sn79_min_utility_margin,
            max_score_subsidy_loss_bps=score_floor,
        )

    common = dict(
        failed_exit_count=max(0, int(failed_exit_count)),
        time_since_first_exit_attempt=max(0.0, _finite(time_since_first_exit_attempt)),
        economic_direct_max_loss_bps=min(0.0, _finite(economic_direct_max_loss_bps, -20.0)),
    )

    def reject(reason: str) -> HybridTakerDecision:
        decision = _reject(
            reason, lock, crossing, maker_ev, maker_fill_hazard,
            econ, comparison, action_utility,
        )
        return HybridTakerDecision(
            **{**decision.__dict__, **common, "allowed_loss_floor_bps": score_floor}
        )

    if transition_quarantine:
        return reject(REASON_REJECT_TRANSITION)
    if dust and not catastrophic:
        return reject(REASON_REJECT_DUST)
    if volume_capped and not catastrophic:
        return reject(REASON_REJECT_CAP)
    if not enable_hybrid and not catastrophic:
        return reject(REASON_MAKER)

    if catastrophic:
        frac = hybrid_taker_qty_frac(
            reason=REASON_CATASTROPHIC, urgency=urgency, lock_pnl_bps=lock,
            emergency=True,
        )
        return HybridTakerDecision(
            True, REASON_CATASTROPHIC, lock, crossing, maker_ev, frac,
            maker_fill_hazard, econ, comparison, action_utility,
            economic_authorized=True, score_authorized=False,
            risk_authorized=True, direct_authorized=True,
            taker_authority=TAKER_AUTH_RISK,
            allowed_loss_floor_bps=min(0.0, _finite(risk_direct_max_loss_bps, -25.0)),
            **common,
        )

    legacy_economic_ok = bool(allow_economic_taker and econ.take) and (
        (not use_fill_hazard_ev) or bool(comparison.prefer_taker)
    )
    economic_floor = min(0.0, _finite(economic_direct_max_loss_bps, -20.0))
    legacy_economic_authorized = bool(
        legacy_economic_ok
        and comparison.expected_taker_exit_value + 1e-12 >= economic_floor
    )
    economic_authorized = legacy_economic_authorized

    # V4.12.9 / St6.4 final: SCORE authority is a completion authority, not
    # a general-purpose shortcut around the Maker ladder.  Require genuine
    # in-progress Kappa work (ONE_AWAY/TWO_AWAY) plus real Maker-fill evidence.
    # A missing hazard must not be interpreted as a dead Maker.  ECONOMIC and
    # RISK authorities remain independent below.
    remaining_obs = max(0, int(observations_remaining))
    required_obs = max(1, int(required_observations))
    score_progress_needed = 0 < remaining_obs < required_obs
    maker_fill_evidence = bool(
        hazard is not None
        or maker_fill_hazard is not None
        or max(0, int(failed_exit_count)) > 0
    )
    score_authorized = bool(
        enable_sn79_action_utility
        and action_utility is not None
        and action_utility.take
        and score_progress_needed
        and maker_fill_evidence
    )

    # Direct RISK_TAKER was permanently disabled.  Unified exit / inventory
    # liveness owns non-catastrophic risk exits, so legacy risk authority is
    # intentionally false here.
    risk_authorized = False

    # V4.11.2: aggressive positive-EV Taker authority.  This path deliberately
    # does NOT depend on the legacy ``econ.take`` gate, which was tuned for
    # defensive holding-cost exits and can remain false even when taking now is
    # both profitable and economically superior to waiting.  It is still hard
    # bounded: net Taker EV must be non-negative (or a stricter configured
    # positive floor), it must beat maker WAIT EV, and at least one explicit
    # realization trigger must be present. ONE_AWAY is the strongest trigger.
    positive_floor = max(0.0, _finite(aggressive_positive_ev_min_net_bps, 0.0))
    one_away = max(0, int(observations_remaining)) == 1
    failed_trigger = max(0, int(failed_exit_count)) >= max(1, int(aggressive_positive_ev_failed_exit_count))
    age_trigger = max(
        max(0.0, _finite(inventory_age)),
        max(0.0, _finite(time_since_first_exit_attempt)),
    ) + 1e-12 >= max(0.0, _finite(aggressive_positive_ev_min_age_ticks, 16.0))
    # Use the actual hazard prediction when available; the prior V4.12.8 path
    # looked only at the optional scalar and could miss a clearly dead Maker
    # even though compare_maker_taker_exit had a valid hazard estimate.
    effective_maker_fill = (
        maker_fill_hazard
        if maker_fill_hazard is not None
        else (comparison.p_fill_horizon if hazard is not None else None)
    )
    low_fill_trigger = maker_fill_unreliable(
        effective_maker_fill,
        min_fill=max(0.0, _finite(aggressive_positive_ev_max_maker_fill, 0.08)),
    )
    urgency_trigger = _clip01(urgency) + 1e-12 >= _clip01(aggressive_positive_ev_min_urgency)
    aggressive_trigger = bool(one_away or failed_trigger or age_trigger or low_fill_trigger or urgency_trigger)
    if one_away:
        aggressive_trigger_reason = "ONE_AWAY"
    elif failed_trigger:
        aggressive_trigger_reason = "FAILED_EXITS"
    elif low_fill_trigger:
        aggressive_trigger_reason = "LOW_MAKER_FILL"
    elif age_trigger:
        aggressive_trigger_reason = "INVENTORY_AGE"
    elif urgency_trigger:
        aggressive_trigger_reason = "URGENCY"
    else:
        aggressive_trigger_reason = "NONE"
    switch_margin = (
        max(0.0, _finite(aggressive_positive_ev_one_away_margin_bps, 0.0))
        if one_away
        else max(0.0, _finite(aggressive_positive_ev_switch_margin_bps, 0.50))
    )
    aggressive_positive_ev_authorized = bool(
        allow_aggressive_positive_ev_taker
        and allow_economic_taker
        and comparison.expected_taker_exit_value + 1e-12 >= positive_floor
        and comparison.expected_taker_exit_value
            > comparison.expected_maker_exit_value + switch_margin + 1e-12
        and aggressive_trigger
    )
    economic_authorized = bool(economic_authorized or aggressive_positive_ev_authorized)

    direct_economic = bool(allow_economic_taker_direct and economic_authorized)
    direct_score = bool(allow_score_taker_direct and score_authorized)
    direct = bool(direct_economic or direct_score)

    # Preserve an already-valid SCORE authority when the new aggressive
    # positive-EV path also fires.  The aggressive ECONOMIC authority exists
    # to unlock profitable realization that legacy economics/score utility did
    # not authorize; it should not relabel an existing SCORE_TAKER decision.
    if direct_score and aggressive_positive_ev_authorized and not legacy_economic_authorized:
        authority = TAKER_AUTH_SCORE
        reason = REASON_SN79_TAKER
        floor = score_floor
    elif direct_economic:
        authority = TAKER_AUTH_ECONOMIC
        reason = (
            REASON_AGGRESSIVE_POSITIVE_EV
            if aggressive_positive_ev_authorized and not legacy_economic_authorized
            else (comparison.reason if use_fill_hazard_ev else econ.reason)
        )
        floor = (
            positive_floor
            if aggressive_positive_ev_authorized and not legacy_economic_authorized
            else economic_floor
        )
    elif direct_score:
        authority = TAKER_AUTH_SCORE
        reason = REASON_SN79_TAKER
        floor = score_floor
    else:
        authority = TAKER_AUTH_NONE
        reason = econ.reason
        floor = score_floor

    # V4.10: a Taker may execute only through an explicit bounded authority.
    # Previously ``legacy_economic_ok`` could keep ``take=True`` even when the
    # configured loss floor rejected ECONOMIC authority, producing
    # taker_authority=NONE market exits. Catastrophic hard-risk is handled
    # above and remains the only authority-free emergency path.
    if direct:
        if authority == TAKER_AUTH_SCORE and action_utility is not None:
            frac = max(0.0, min(1.0, float(action_utility.recommended_qty_frac)))
        else:
            econ_reason = comparison.reason if use_fill_hazard_ev else econ.reason
            frac = hybrid_taker_qty_frac(
                reason=(REASON_AGGRESSIVE_POSITIVE_EV if aggressive_positive_ev_authorized and not legacy_economic_authorized else econ_reason),
                urgency=urgency, lock_pnl_bps=lock, emergency=False,
            )
            if score_authorized and action_utility is not None:
                frac = max(frac, float(action_utility.recommended_qty_frac))
        if aggressive_positive_ev_authorized:
            # Positive-EV authority is a completion/rotation accelerator.  Aim
            # for 90% reduction; the exit-quantity layer will flatten instead
            # when a partial clip would leave unsizable dust.
            frac = max(frac, 0.90)
        if frac <= 0.0:
            frac = 0.55
        return HybridTakerDecision(
            True, reason, lock, crossing, maker_ev, max(0.0, min(1.0, frac)),
            maker_fill_hazard, econ, comparison, action_utility,
            economic_authorized=economic_authorized,
            score_authorized=score_authorized,
            risk_authorized=risk_authorized,
            aggressive_positive_ev_authorized=aggressive_positive_ev_authorized,
            aggressive_positive_ev_trigger=(aggressive_trigger_reason if aggressive_positive_ev_authorized else "NONE"),
            aggressive_positive_ev_advantage_bps=(
                comparison.expected_taker_exit_value - comparison.expected_maker_exit_value
            ),
            aggressive_positive_ev_switch_margin_bps=switch_margin,
            aggressive_positive_ev_floor_bps=positive_floor,
            direct_authorized=direct,
            taker_authority=authority,
            allowed_loss_floor_bps=floor,
            **common,
        )

    # No direct authority fired. Preserve *latent* authority state in telemetry
    # even when its direct feature gate is disabled; otherwise logs/tests report
    # score/economic eligibility as false simply because execution was gated.
    # This is important for distinguishing "eligible but gated" from "not
    # eligible", and prevents authority diagnostics from lying.
    reject_reason = econ.reason
    if bool(econ.take) and use_fill_hazard_ev and not bool(comparison.prefer_taker):
        reject_reason = REASON_MAKER_EV
    elif not allow_economic_taker and not score_authorized:
        reject_reason = REASON_MAKER
    rejected = reject(reject_reason)
    return HybridTakerDecision(
        **{
            **rejected.__dict__,
            "economic_authorized": bool(economic_authorized),
            "score_authorized": bool(score_authorized),
            "risk_authorized": bool(risk_authorized),
            "aggressive_positive_ev_authorized": bool(aggressive_positive_ev_authorized),
            "aggressive_positive_ev_trigger": (
                aggressive_trigger_reason if aggressive_positive_ev_authorized else "NONE"
            ),
            "aggressive_positive_ev_advantage_bps": (
                comparison.expected_taker_exit_value - comparison.expected_maker_exit_value
            ),
            "aggressive_positive_ev_switch_margin_bps": switch_margin,
            "aggressive_positive_ev_floor_bps": positive_floor,
            "direct_authorized": False,
            "taker_authority": TAKER_AUTH_NONE,
        }
    )
