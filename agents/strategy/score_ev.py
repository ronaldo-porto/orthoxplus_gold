# SPDX-License-Identifier: MIT
"""Production Kappa completion value + Score-EV ranking.

Promoted from verified Research V4.3. Exact Phase 1 coefficients.

ScoreEV = TradingEV + KappaCompletionValue - DustCost - InventoryRisk - LatencyRisk

Hard safety (toxic / negative-EV / inventory-blocked / unsafe) always wins
over completion pressure. Sparse learned fill/markout data is shrunk toward
conservative fallbacks. No Strategy1 / Research runtime imports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

PROTOCOL_DEFAULT_MIN_REALIZED_OBSERVATIONS = 3


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
    req = max(1, int(required))
    realized = max(0, int(realized_observation_count))
    remaining = max(0, req - realized)
    return realized, req, remaining


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
    min_samples: int = 8,
    fallback_bps: float = 0.0,
    prior_strength: float = 8.0,
    clip_abs: float = 20.0,
) -> float:
    """Sparse markout shrinks toward 0 rather than an optimistic mean."""
    n = max(0, int(samples))
    fb = float(fallback_bps)
    if mean_bps is None or n <= 0:
        return fb
    mean = max(-clip_abs, min(clip_abs, float(mean_bps)))
    strength = max(0.0, float(prior_strength))
    if n >= max(1, int(min_samples)):
        return mean
    blended = (n * mean + strength * fb) / (n + strength)
    return max(-clip_abs, min(clip_abs, blended))


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
    if float(trading_ev_value) < float(min_trading_ev):
        return "NEGATIVE_EV"
    return None


def legacy_global_rank(expected_alpha: float, specialization: float = 0.0) -> float:
    """Parent Strategy1 rank, kept for A/B when Score-EV is off."""
    spec = max(0.0, min(1.0, float(specialization)))
    return float(expected_alpha) * (0.72 + 0.28 * spec) + 0.12 * spec


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
    final_score: float
    eligible: bool
    reject_reason: str | None

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
            "final_score": (
                self.final_score if math.isfinite(self.final_score) else None
            ),
            "eligible": self.eligible,
            "reject_reason": self.reject_reason,
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
    fees_bps: float = 0.5,
    realized_observation_count: int = 0,
    required: int = PROTOCOL_DEFAULT_MIN_REALIZED_OBSERVATIONS,
    inventory_util: float = 0.0,
    latency_ms: float | None = None,
    toxic: bool = False,
    inventory_blocked: bool = False,
    unsafe: bool = False,
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
    reason = hard_safety_blocks(
        toxic=toxic,
        inventory_blocked=inventory_blocked,
        unsafe=unsafe,
        trading_ev_value=t_ev,
        min_trading_ev=min_trading_ev,
    )
    eligible = reason is None
    final = t_ev + c_val - d_cost - i_cost - l_cost if eligible else float("-inf")
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
        final_score=final,
        eligible=eligible,
        reject_reason=reason,
    )


def select_rank(
    *,
    enable_score_ev: bool,
    score_ev: ScoreEVBreakdown | None,
    legacy_rank: float,
) -> float | None:
    """Feature flag: Score-EV ranking or inherited global rank. None = reject."""
    if not enable_score_ev:
        return float(legacy_rank)
    if score_ev is None or not score_ev.eligible:
        return None
    return float(score_ev.final_score)


def scheduler_bucket_counts(
    observation_counts: dict[int, int],
    required: int,
    *,
    eligible_ids: set[int] | None = None,
) -> dict[str, int]:
    req = max(1, int(required))
    zero = 0
    rem1 = 0
    rem2 = 0
    for _book, n in observation_counts.items():
        realized, _, remaining = observation_progress(int(n), req)
        if realized <= 0:
            zero += 1
        if remaining == 1:
            rem1 += 1
        elif remaining == 2:
            rem2 += 1
    return {
        "books_zero_obs": zero,
        "books_one_remaining": rem1,
        "books_two_remaining": rem2,
        "eligible_books": len(eligible_ids) if eligible_ids is not None else 0,
        "tracked_books": len(observation_counts),
        "required_observation_count": req,
    }
