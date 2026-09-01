# SPDX-License-Identifier: MIT
"""Research Kappa Completion Scheduler V3 + Score-EV ranking.

Pure functions so unit tests do not import Strategy1 / bittensor.

Priority =
  TradingEV
  + CompletionValue
  + ActivityDeficitValue
  - InventoryRisk
  - DustRisk
  - AdverseSelectionRisk
  - LatencyRisk

Hard safety (toxic / negative-EV / inventory-blocked / unsafe / invalid size /
volume-cap) always wins over completion pressure. Sparse learned fill/markout
data is shrunk toward conservative fallbacks.

Qualification threshold comes from runtime config. The protocol default of 3
is used only when nothing is configured.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from research_kappa_state import build_kappa_universe, kappa_book_state, kappa_progress
from research_lifecycle_ev import LIFECYCLE_EV_MARGIN
from research_markout import (
    CONSERVATIVE_MARKOUT_FALLBACK_BPS,
    MIN_MARKOUT_SAMPLES,
    conservative_expected_markout_bps,
)

SCORE_EV_VERSION = "simplified_hybrid_authority_v4_16_0"
PROTOCOL_DEFAULT_MIN_REALIZED_OBSERVATIONS = 3
LANE_COVERAGE = "COVERAGE"
LANE_COMPLETION = "COMPLETION"
LANE_NORMAL = "NORMAL"


def required_observation_count(
    *,
    kappa_min_observations: int | None = None,
    research_target: int | None = None,
) -> int:
    """Runtime Kappa qualification threshold.

    Prefer an explicit Research scheduler target, else the miner-configured
    ``kappa_min_observations`` (mirrors validator
    ``scoring.kappa.min_realized_observations``). The protocol default of 3 is
    used only when nothing is configured.
    """
    for value in (research_target, kappa_min_observations):
        if value is None:
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n >= 1:
            return n
    return PROTOCOL_DEFAULT_MIN_REALIZED_OBSERVATIONS


def observation_progress(
    realized_observation_count: int,
    required: int,
) -> tuple[int, int, int]:
    return kappa_progress(realized_observation_count, required)


def completion_value(
    *,
    observations_remaining: int,
    required_observation_count: int,
    one_away_weight: float = 0.18,
    two_away_weight: float = 0.06,
    new_book_weight: float = 0.0,
) -> float:
    """Scheduler bonus: 1 remaining >> 2 remaining > new book.

    Already-qualified books (remaining 0) get no completion pressure.
    """
    remaining = max(0, int(observations_remaining))
    required = max(1, int(required_observation_count))
    if remaining <= 0:
        return 0.0
    if remaining == 1:
        return max(0.0, float(one_away_weight))
    if remaining == 2:
        return max(0.0, float(two_away_weight))
    if remaining >= required:
        return max(0.0, float(new_book_weight))
    decay = max(0.0, float(two_away_weight)) * (2.0 / float(remaining))
    return decay


def conservative_actionable_probability(
    *,
    hazard_p: float | None,
    hazard_usable: bool,
    learned_p: float | None,
    learned_samples: int,
    fill_prob_old: float,
    min_samples: int = 8,
    prior: float = 0.55,
    p_min: float = 0.05,
    p_max: float = 0.90,
) -> float:
    """Do not blindly trust sparse hazard or learned actionable-fill data."""
    if hazard_usable and hazard_p is not None:
        return max(p_min, min(p_max, float(hazard_p)))
    n = max(0, int(learned_samples))
    fallback = max(p_min, min(p_max, float(fill_prob_old) * float(prior)))
    if learned_p is None or n <= 0:
        return fallback
    learned = max(p_min, min(p_max, float(learned_p)))
    if n >= max(1, int(min_samples)):
        return learned
    strength = float(max(1, int(min_samples)))
    blended = (n * learned + strength * fallback) / (n + strength)
    return max(p_min, min(p_max, blended))


def conservative_markout_bps(
    *,
    mean_bps: float | None,
    samples: int,
    min_samples: int = MIN_MARKOUT_SAMPLES,
    fallback_bps: float = CONSERVATIVE_MARKOUT_FALLBACK_BPS,
    prior_strength: float = 8.0,
    clip_abs: float = 20.0,
) -> float:
    """Sparse / missing markout shrinks toward a slightly adverse prior.

    Missing markout is not zero adverse selection.
    """
    return conservative_expected_markout_bps(
        mean_bps=mean_bps,
        samples=samples,
        min_samples=min_samples,
        fallback_bps=fallback_bps,
        prior_strength=prior_strength,
        clip_abs=clip_abs,
    )


def trading_ev(
    *,
    actionable_fill_prob: float,
    spread_capture_bps: float,
    expected_markout_bps: float,
    fees_bps: float,
    edge_scale_bps: float = 8.0,
) -> float:
    """P(actionable fill) * tanh((spread capture + markout - fees) / scale)."""
    p = max(0.0, min(1.0, float(actionable_fill_prob)))
    edge = float(spread_capture_bps) + float(expected_markout_bps) - float(fees_bps)
    scale = max(1e-6, float(edge_scale_bps))
    return p * math.tanh(edge / scale)


def dust_cost(
    dust_prob: float,
    *,
    target: float = 0.15,
    weight: float = 0.25,
) -> float:
    return max(0.0, float(weight)) * max(0.0, float(dust_prob) - float(target))


def inventory_cost(inventory_util: float, *, weight: float = 0.08) -> float:
    util = max(0.0, min(1.0, float(inventory_util)))
    return max(0.0, float(weight)) * util * util


def latency_cost(latency_ms: float | None, *, weight: float = 0.04, ref_ms: float = 50.0) -> float:
    if latency_ms is None:
        return 0.0
    frac = max(0.0, min(1.0, float(latency_ms) / max(1.0, float(ref_ms))))
    return max(0.0, float(weight)) * frac


def hard_safety_blocks(
    *,
    toxic: bool = False,
    inventory_blocked: bool = False,
    unsafe: bool = False,
    invalid_size: bool = False,
    volume_capped: bool = False,
    trading_ev_value: float = 0.0,
    min_trading_ev: float = 0.0,
) -> str | None:
    """Hard gates beat completion value. Returns a reject reason or None."""
    if toxic:
        return "TOXIC"
    if inventory_blocked:
        return "INVENTORY_BLOCKED"
    if unsafe:
        return "UNSAFE"
    if invalid_size:
        return "INVALID_SIZE"
    if volume_capped:
        return "VOLUME_CAP"
    if float(trading_ev_value) < float(min_trading_ev):
        return "NEGATIVE_EV"
    return None


def activity_deficit_value(
    *,
    realized_observation_count: int,
    required_observation_count: int,
    last_realization_time: float | None = None,
    now: float | None = None,
    uncovered_weight: float = 0.04,
    stale_weight: float = 0.03,
    stale_ms: float = 30_000.0,
) -> float:
    """Small bonus for uncovered or stale books. Always below two-away completion."""
    realized, _req, remaining = observation_progress(
        realized_observation_count, required_observation_count,
    )
    if remaining <= 0:
        return 0.0
    value = 0.0
    if realized <= 0:
        value += max(0.0, float(uncovered_weight))
    if last_realization_time is not None and now is not None:
        try:
            age = float(now) - float(last_realization_time)
        except (TypeError, ValueError):
            age = 0.0
        if age >= max(0.0, float(stale_ms)):
            value += max(0.0, float(stale_weight))
    return value


def adverse_selection_risk(
    expected_markout_bps: float,
    *,
    weight: float = 0.05,
    scale_bps: float = 8.0,
    ofi_against: float = 0.0,
    ofi_weight: float = 0.04,
) -> float:
    """Penalize expected adverse markout and real OFI against the book.

    ``ofi_against`` is Cont–Kukanov–Stoikov flow, never static imbalance.
    """
    try:
        adverse = max(0.0, -float(expected_markout_bps))
    except (TypeError, ValueError):
        adverse = 0.0
    markout_term = max(0.0, float(weight)) * math.tanh(
        adverse / max(1e-6, float(scale_bps))
    )
    try:
        flow = max(0.0, float(ofi_against))
    except (TypeError, ValueError):
        flow = 0.0
    ofi_term = max(0.0, float(ofi_weight)) * math.tanh(flow)
    return max(0.0, min(1.0, markout_term + ofi_term))


def classify_scheduler_lane(
    realized_observation_count: int,
    required_observation_count: int,
) -> str:
    realized, _req, remaining = observation_progress(
        realized_observation_count, required_observation_count,
    )
    if remaining <= 0:
        return LANE_NORMAL
    if realized <= 0:
        return LANE_COVERAGE
    return LANE_COMPLETION


def book_observation_state(
    *,
    realized_observation_count: int,
    required_observations: int,
    last_realization_time: float | None = None,
    recent_realized_pnl: float | None = None,
    expected_trade_ev: float | None = None,
    inventory_state: str | None = None,
) -> dict[str, Any]:
    row = kappa_book_state(0, realized_observation_count, required_observations)
    return {
        "realized_observation_count": row.realized_observation_count,
        "required_observations": row.required_observations,
        "observations_remaining": row.observations_remaining,
        "eligible": row.eligible,
        "last_realization_time": last_realization_time,
        "recent_realized_pnl": recent_realized_pnl,
        "expected_trade_ev": expected_trade_ev,
        "inventory_state": inventory_state,
        "lane": classify_scheduler_lane(
            row.realized_observation_count, row.required_observations,
        ),
    }


def round_trip_velocity(
    completed_round_trips: int,
    simulation_time: float,
) -> float:
    """completed_round_trips / simulation_time. Zero when time is not positive."""
    try:
        trips = max(0, int(completed_round_trips))
        t = float(simulation_time)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(t) or t <= 0.0:
        return 0.0
    return trips / t


def admit_scheduler_candidate(
    *,
    lane: str,
    quote_successes: int,
    quote_success_cap: int,
    completion_attempts: int,
    completion_attempt_cap: int,
    completion_successes: int,
    completion_success_cap: int,
    normal_attempts: int,
    normal_attempt_cap: int,
) -> tuple[bool, str | None]:
    """Reserve completion slots. Normal attempt/success limits cannot starve them."""
    token = str(lane or LANE_NORMAL).upper()
    reserved = max(0, int(completion_success_cap))
    reserved_remaining = max(0, reserved - max(0, int(completion_successes)))
    global_cap = max(0, int(quote_success_cap))
    global_used = max(0, int(quote_successes))

    if token == LANE_COMPLETION:
        if max(0, int(completion_successes)) >= reserved:
            return False, "KAPPA_COMPLETION_SUCCESS_CAP"
        if max(0, int(completion_attempts)) >= max(0, int(completion_attempt_cap)):
            return False, "KAPPA_COMPLETION_ATTEMPT_CAP"
        if reserved_remaining <= 0 and global_used >= global_cap:
            return False, "MM_SUCCESS_CAP"
        return True, None

    normal_success_room = max(0, global_cap - reserved_remaining)
    if global_used >= normal_success_room:
        return False, "MM_SUCCESS_CAP"
    if max(0, int(normal_attempts)) >= max(0, int(normal_attempt_cap)):
        return False, "NORMAL_MM_ATTEMPT_CAP"
    return True, None




@dataclass(frozen=True)
class ScoreEVBreakdown:
    book: int
    side: str
    alpha: float
    fill_prob_old: float
    fill_prob_hazard: float | None
    actionable_fill_prob: float
    dust_prob: float
    spread_capture_bps: float
    expected_markout_bps: float
    fees_bps: float
    trading_ev: float
    observation_count: int
    required_observation_count: int
    observations_remaining: int
    completion_value: float
    dust_cost: float
    inventory_cost: float
    latency_cost: float
    activity_deficit_value: float
    adverse_selection_risk: float
    last_realization_time: float | None
    recent_realized_pnl: float | None
    inventory_state: str | None
    lane: str
    volume_cap_headroom: float
    final_score: float
    eligible: bool
    reject_reason: str | None
    score_velocity_value: float = 0.0
    expected_realization_time: float | None = None
    realization_time_reference: float | None = None
    lifecycle_ev: float = 0.0
    total_score_component: float = 0.0
    required_entry_ev: float = 0.0
    taker_prob_live: float = 0.0
    taker_prob_prior: float = 0.0
    taker_prob_excess: float = 0.0
    expected_taker_cost: float = 0.0
    raw_taker_penalty: float = 0.0
    capped_taker_penalty: float = 0.0
    adverse_penalty: float = 0.0
    holding_penalty: float = 0.0
    latency_penalty: float = 0.0
    crossing_penalty: float = 0.0
    completion_multiplier: float = 1.0
    entry_ev_margin: float = 0.0
    entry_ev_pass: bool = False

    @property
    def final_priority(self) -> float:
        return self.final_score

    def as_log(self) -> dict[str, Any]:
        return {
            "book": self.book,
            "side": self.side,
            "alpha": self.alpha,
            "fill_prob_old": self.fill_prob_old,
            "fill_prob_hazard": self.fill_prob_hazard,
            "actionable_fill_prob": self.actionable_fill_prob,
            "dust_prob": self.dust_prob,
            "spread_capture_bps": self.spread_capture_bps,
            "expected_markout_bps": self.expected_markout_bps,
            "fees_bps": self.fees_bps,
            "trading_ev": self.trading_ev,
            "observation_count": self.observation_count,
            "required_observation_count": self.required_observation_count,
            "observations_remaining": self.observations_remaining,
            "completion_value": self.completion_value,
            "dust_cost": self.dust_cost,
            "inventory_cost": self.inventory_cost,
            "latency_cost": self.latency_cost,
            "activity_deficit_value": self.activity_deficit_value,
            "adverse_selection_risk": self.adverse_selection_risk,
            "last_realization_time": self.last_realization_time,
            "recent_realized_pnl": self.recent_realized_pnl,
            "inventory_state": self.inventory_state,
            "lane": self.lane,
            "volume_cap_headroom": self.volume_cap_headroom,
            "final_score": (
                self.final_score if math.isfinite(self.final_score) else None
            ),
            "final_priority": (
                self.final_priority if math.isfinite(self.final_priority) else None
            ),
            "eligible": self.eligible,
            "reject_reason": self.reject_reason,
            "score_velocity_value": self.score_velocity_value,
            "expected_realization_time": self.expected_realization_time,
            "realization_time_reference": self.realization_time_reference,
            "lifecycle_ev": (
                self.lifecycle_ev if math.isfinite(self.lifecycle_ev) else None
            ),
            "total_score_component": (
                self.total_score_component
                if math.isfinite(self.total_score_component)
                else None
            ),
            "required_entry_ev": float(self.required_entry_ev),
            "taker_prob_live": float(self.taker_prob_live),
            "taker_prob_prior": float(self.taker_prob_prior),
            "taker_prob_excess": float(self.taker_prob_excess),
            "expected_taker_cost": float(self.expected_taker_cost),
            "raw_taker_penalty": float(self.raw_taker_penalty),
            "capped_taker_penalty": float(self.capped_taker_penalty),
            "adverse_penalty": float(self.adverse_penalty),
            "holding_penalty": float(self.holding_penalty),
            "latency_penalty": float(self.latency_penalty),
            "crossing_penalty": float(self.crossing_penalty),
            "completion_multiplier": float(self.completion_multiplier),
            "entry_ev_margin": (
                self.entry_ev_margin if math.isfinite(self.entry_ev_margin) else None
            ),
            "entry_ev_pass": bool(self.entry_ev_pass),
            "score_ev_version": SCORE_EV_VERSION,
        }


def compute_score_ev(
    *,
    book: int,
    side: str = "MM",
    alpha: float = 0.0,
    fill_prob_old: float = 0.0,
    fill_prob_hazard: float | None = None,
    actionable_fill_hazard: float | None = None,
    hazard_usable: bool = False,
    learned_actionable_p: float | None = None,
    learned_actionable_samples: int = 0,
    dust_prob: float = 0.0,
    spread_capture_bps: float = 0.0,
    markout_mean_bps: float | None = None,
    markout_samples: int = 0,
    expected_markout_override: float | None = None,
    ofi_against: float = 0.0,
    fees_bps: float = 0.5,
    realized_observation_count: int = 0,
    required: int = PROTOCOL_DEFAULT_MIN_REALIZED_OBSERVATIONS,
    inventory_util: float = 0.0,
    latency_ms: float | None = None,
    last_realization_time: float | None = None,
    now: float | None = None,
    recent_realized_pnl: float | None = None,
    inventory_state: str | None = None,
    toxic: bool = False,
    inventory_blocked: bool = False,
    unsafe: bool = False,
    invalid_size: bool = False,
    volume_capped: bool = False,
    volume_cap_headroom: float = 1.0,
    min_trading_ev: float = 0.0,
    min_fill_samples: int = 8,
    min_markout_samples: int = 8,
    one_away_weight: float = 0.18,
    two_away_weight: float = 0.06,
    new_book_weight: float = 0.0,
    dust_target: float = 0.15,
    dust_weight: float = 0.25,
    inventory_weight: float = 0.08,
    latency_weight: float = 0.04,
    uncovered_weight: float = 0.04,
    stale_weight: float = 0.03,
    stale_ms: float = 30_000.0,
    adverse_weight: float = 0.05,
    taker_exit_probability: float | None = None,
    expected_cross_bps: float = 0.0,
    holding_risk_bps: float = 0.0,
    projected_completion_healthy: bool | None = None,
    one_away_entry_mult: float = 0.60,
    two_away_entry_mult: float = 0.80,
) -> ScoreEVBreakdown:
    realized, req, remaining = observation_progress(realized_observation_count, required)
    p_act = conservative_actionable_probability(
        hazard_p=actionable_fill_hazard,
        hazard_usable=hazard_usable,
        learned_p=learned_actionable_p,
        learned_samples=learned_actionable_samples,
        fill_prob_old=fill_prob_old,
        min_samples=min_fill_samples,
    )
    if expected_markout_override is not None:
        try:
            markout = max(-20.0, min(20.0, float(expected_markout_override)))
        except (TypeError, ValueError):
            markout = conservative_markout_bps(
                mean_bps=markout_mean_bps,
                samples=markout_samples,
                min_samples=min_markout_samples,
            )
    else:
        markout = conservative_markout_bps(
            mean_bps=markout_mean_bps,
            samples=markout_samples,
            min_samples=min_markout_samples,
        )
    t_ev = trading_ev(
        actionable_fill_prob=p_act,
        spread_capture_bps=spread_capture_bps,
        expected_markout_bps=markout,
        fees_bps=fees_bps,
    )
    c_val = completion_value(
        observations_remaining=remaining,
        required_observation_count=req,
        one_away_weight=one_away_weight,
        two_away_weight=two_away_weight,
        new_book_weight=new_book_weight,
    )
    d_cost = dust_cost(dust_prob, target=dust_target, weight=dust_weight)
    i_cost = inventory_cost(inventory_util, weight=inventory_weight)
    l_cost = latency_cost(latency_ms, weight=latency_weight)
    a_def = activity_deficit_value(
        realized_observation_count=realized,
        required_observation_count=req,
        last_realization_time=last_realization_time,
        now=now,
        uncovered_weight=uncovered_weight,
        stale_weight=stale_weight,
        stale_ms=stale_ms,
    )
    a_risk = adverse_selection_risk(
        markout, weight=adverse_weight, ofi_against=ofi_against,
    )
    try:
        headroom = max(0.0, min(1.0, float(volume_cap_headroom)))
    except (TypeError, ValueError):
        headroom = 1.0
    if not math.isfinite(headroom):
        headroom = 1.0
    capped = bool(volume_capped) or headroom <= 0.0
    entry_bar = max(0.0, float(min_trading_ev), float(LIFECYCLE_EV_MARGIN))
    lifecycle = t_ev - d_cost - i_cost - l_cost - a_risk
    reason = hard_safety_blocks(
        toxic=toxic,
        inventory_blocked=inventory_blocked,
        unsafe=unsafe,
        invalid_size=invalid_size,
        volume_capped=capped,
        trading_ev_value=lifecycle,
        min_trading_ev=entry_bar,
    )
    eligible = reason is None
    total_score = c_val + a_def
    final = (lifecycle + total_score) if eligible else float("-inf")
    entry_ev_margin = lifecycle - entry_bar
    entry_ev_pass = bool(eligible)
    return ScoreEVBreakdown(
        book=int(book),
        side=str(side),
        alpha=float(alpha),
        fill_prob_old=float(fill_prob_old),
        fill_prob_hazard=None if fill_prob_hazard is None else float(fill_prob_hazard),
        actionable_fill_prob=p_act,
        dust_prob=max(0.0, min(1.0, float(dust_prob))),
        spread_capture_bps=float(spread_capture_bps),
        expected_markout_bps=markout,
        fees_bps=float(fees_bps),
        trading_ev=t_ev,
        observation_count=realized,
        required_observation_count=req,
        observations_remaining=remaining,
        completion_value=c_val,
        dust_cost=d_cost,
        inventory_cost=i_cost,
        latency_cost=l_cost,
        activity_deficit_value=a_def,
        adverse_selection_risk=a_risk,
        last_realization_time=last_realization_time,
        recent_realized_pnl=recent_realized_pnl,
        inventory_state=inventory_state,
        lane=classify_scheduler_lane(realized, req),
        volume_cap_headroom=headroom,
        final_score=final,
        eligible=eligible,
        reject_reason=reason,
        lifecycle_ev=lifecycle if eligible else float("-inf"),
        total_score_component=total_score,
        required_entry_ev=float(entry_bar),
        completion_multiplier=1.0,
        entry_ev_margin=entry_ev_margin,
        entry_ev_pass=entry_ev_pass,
    )


def score_velocity_priority(
    *,
    book: int,
    realized_observation_count: int,
    required: int,
    expected_realization_time: float | None = None,
    realization_time_reference: float | None = None,
    score_velocity_weight: float = 0.08,
    enable_score_velocity: bool = True,
    **kwargs: Any,
) -> ScoreEVBreakdown:
    """Rank expected Kappa/coverage contribution per relative realization time.

    The base Score-EV hard gates remain authoritative. Score velocity is a
    bounded bonus only: completion/activity value × actionable-fill probability
    × a shrinkage time factor. Book realization time is normalized by the
    agent-wide median, so timestamp units cancel and cold books use a neutral
    time factor rather than an invented absolute duration.
    """
    base = compute_score_ev(
        book=book,
        realized_observation_count=realized_observation_count,
        required=required,
        **kwargs,
    )
    if not enable_score_velocity or not base.eligible:
        return replace(
            base,
            score_velocity_value=0.0,
            expected_realization_time=expected_realization_time,
            realization_time_reference=realization_time_reference,
        )

    try:
        t_book = None if expected_realization_time is None else float(expected_realization_time)
    except (TypeError, ValueError):
        t_book = None
    try:
        t_ref = None if realization_time_reference is None else float(realization_time_reference)
    except (TypeError, ValueError):
        t_ref = None
    if t_book is not None and (not math.isfinite(t_book) or t_book <= 0.0):
        t_book = None
    if t_ref is not None and (not math.isfinite(t_ref) or t_ref <= 0.0):
        t_ref = None

    # Cold books shrink to the global median. If no empirical timing exists yet,
    # use a neutral factor so coverage can bootstrap without fake speed claims.
    if t_ref is None:
        time_factor = 1.0
    else:
        effective = t_ref if t_book is None else t_book
        time_factor = max(0.25, min(4.0, t_ref / max(effective, 1e-12)))

    expected_score_gain = max(0.0, base.completion_value + base.activity_deficit_value)
    completion_probability = max(0.0, min(1.0, base.actionable_fill_prob))
    velocity_value = expected_score_gain * completion_probability * time_factor
    bonus = max(0.0, float(score_velocity_weight)) * velocity_value
    return replace(
        base,
        final_score=base.final_score + bonus,
        total_score_component=base.total_score_component + bonus,
        score_velocity_value=velocity_value,
        expected_realization_time=t_book,
        realization_time_reference=t_ref,
    )




def scheduler_bucket_counts(
    observation_counts: dict[int, int],
    required: int,
    *,
    eligible_ids: set[int] | None = None,
) -> dict[str, int]:
    """Kappa-qualified counts. ``eligible_ids`` is ScoreEV ranking eligibility and is ignored."""
    del eligible_ids
    return build_kappa_universe(observation_counts, required).bucket_counts()
