# SPDX-License-Identifier: MIT
"""Bounded Adaptive expected-value overlay.

BaseStrategy remains the safe baseline. This module scores small learned
corrections and accepts one when AdaptiveUtility improves.

Learned correction dimensions:
    fill hazard, markout, spread, side preference, size,
    exit urgency, book specialization

AdaptiveUtility =
    TradingEV + CompletionValue
    - InventoryRisk - DustRisk - AdverseSelectionRisk - LatencyRisk

Allowed actions: tighten, widen, cut size, suppress a side, realize earlier.
Low fill rate never auto-tightens. Size scale is never greater than 1.
Exit urgency may only move earlier than Base. No Strategy1 / Research imports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

EDGE_SCALE_BPS = 8.0
ADVERSE_WEIGHT = 0.06
FILL_SPREAD_SENS = 1.25
MARKOUT_SPREAD_SENS = 2.0
DUST_TIGHTEN_SENS = 0.20
MIN_P = 0.01
MAX_P = 0.95
HOLD_EPS = 1e-9
SIDE_SUPPRESS_SCALE = 0.55
EXIT_INV_RELIEF = 0.55
EXIT_ADVERSE_RELIEF = 0.35
EXIT_COMPLETION_COST = 0.25
EXIT_TRADE_COST = 0.10
ACTION_PASSIVE = "PASSIVE_MAKER_EXIT"
ACTION_COMPETITIVE = "COMPETITIVE_MAKER_EXIT"
ACTION_AGGRESSIVE = "AGGRESSIVE_MAKER_EXIT"
ACTION_TAKER = "SELECTIVE_TAKER_EXIT"
URGENCY_PASSIVE_MAX = 0.25
URGENCY_COMPETITIVE_MAX = 0.50
URGENCY_AGGRESSIVE_MAX = 0.78
_ACTION_LADDER = {
    ACTION_PASSIVE: 0,
    ACTION_COMPETITIVE: 1,
    ACTION_AGGRESSIVE: 2,
    ACTION_TAKER: 3,
}

Phase = Literal["DISABLED", "OBSERVE", "BOOTSTRAP", "NORMAL", "DRIFT"]


@dataclass(frozen=True)
class EvSnapshot:
    actionable_p: float
    spread_capture_bps: float
    markout_bps: float
    fees_bps: float
    completion_value: float
    inventory_cost: float
    dust_prob: float
    latency_cost: float
    learned_fill: float | None
    learned_markout_bps: float | None
    buy_fill: float | None
    sell_fill: float | None
    confidence: float
    specialization: float
    dust_target: float = 0.15
    dust_weight: float = 0.25
    exit_urgency: float = 0.0
    inventory_ratio: float = 0.0


@dataclass(frozen=True)
class QuoteProposal:
    spread_scale: float
    size_scale: float
    buy_bias_scale: float
    sell_bias_scale: float
    reason: str
    exit_urgency_scale: float = 1.0


@dataclass(frozen=True)
class EvDecision:
    proposal: QuoteProposal
    base_ev: float
    adaptive_ev: float
    spread_delta: float
    fill_hazard_delta: float
    markout_delta: float
    exit_urgency_delta: float
    reason: str
    confidence: float
    accepted: bool

    def as_log(self) -> dict[str, Any]:
        return {
            "base_ev": self.base_ev,
            "adaptive_ev": self.adaptive_ev,
            "spread_delta": self.spread_delta,
            "fill_hazard_delta": self.fill_hazard_delta,
            "markout_delta": self.markout_delta,
            "exit_urgency_delta": self.exit_urgency_delta,
            "reason": self.reason,
            "confidence": self.confidence,
            "accepted": int(self.accepted),
            "spread_scale": self.proposal.spread_scale,
            "size_scale": self.proposal.size_scale,
            "buy_bias_scale": self.proposal.buy_bias_scale,
            "sell_bias_scale": self.proposal.sell_bias_scale,
            "exit_urgency_scale": self.proposal.exit_urgency_scale,
        }


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def trading_ev(p: float, capture_bps: float, markout_bps: float, fees_bps: float) -> float:
    edge = float(capture_bps) + float(markout_bps) - float(fees_bps)
    return _clip(p, 0.0, 1.0) * math.tanh(edge / EDGE_SCALE_BPS)


def dust_risk(dust_prob: float, *, target: float = 0.15, weight: float = 0.25) -> float:
    return max(0.0, float(weight)) * max(0.0, float(dust_prob) - float(target))


def adverse_selection_risk(markout_bps: float, *, weight: float = ADVERSE_WEIGHT) -> float:
    return max(0.0, float(weight)) * max(0.0, -float(markout_bps)) / EDGE_SCALE_BPS


def adaptive_utility(
    *,
    trading: float,
    completion: float,
    inventory: float,
    dust: float,
    adverse: float,
    latency: float,
) -> float:
    return (
        float(trading)
        + float(completion)
        - float(inventory)
        - float(dust)
        - float(adverse)
        - float(latency)
    )


def project_snapshot(snap: EvSnapshot, proposal: QuoteProposal) -> tuple[float, float, float, float]:
    """Return (utility, projected_p, projected_markout, projected_capture)."""
    d_spread = float(proposal.spread_scale) - 1.0
    p0 = float(snap.actionable_p)
    if snap.learned_fill is not None and snap.confidence > 0.0:
        blend = 0.35 * float(snap.confidence)
        p0 = (1.0 - blend) * p0 + blend * float(snap.learned_fill)
    scale = max(0.05, float(proposal.spread_scale))
    p = _clip(p0 * (scale ** (-FILL_SPREAD_SENS)), MIN_P, MAX_P)

    capture = max(0.0, float(snap.spread_capture_bps) * float(proposal.spread_scale))
    m0 = float(snap.markout_bps)
    if snap.learned_markout_bps is not None and snap.confidence > 0.0:
        blend = 0.30 * float(snap.confidence)
        m0 = (1.0 - blend) * m0 + blend * float(snap.learned_markout_bps)
    markout = m0 + MARKOUT_SPREAD_SENS * d_spread

    dust_p = float(snap.dust_prob) * (1.0 + DUST_TIGHTEN_SENS * max(0.0, -d_spread))
    dust_p *= float(proposal.size_scale)
    u_scale = max(1.0, float(proposal.exit_urgency_scale))
    earlier = max(0.0, u_scale - 1.0)
    inv = float(snap.inventory_cost) * (float(proposal.size_scale) ** 2)
    inv = inv / (1.0 + EXIT_INV_RELIEF * earlier)
    tev = trading_ev(p, capture, markout, snap.fees_bps) * float(proposal.size_scale)
    tev = tev / (1.0 + EXIT_TRADE_COST * earlier)
    if snap.buy_fill is not None and snap.sell_fill is not None:
        buy_s = max(0.0, float(proposal.buy_bias_scale))
        sell_s = max(0.0, float(proposal.sell_bias_scale))
        gap = float(snap.buy_fill) - float(snap.sell_fill)
        if gap > 0.0:
            tev += 0.15 * float(snap.confidence) * gap * max(0.0, 1.0 - sell_s)
        elif gap < 0.0:
            tev += 0.15 * float(snap.confidence) * (-gap) * max(0.0, 1.0 - buy_s)
    completion = float(snap.completion_value) / (1.0 + EXIT_COMPLETION_COST * earlier)
    adverse = adverse_selection_risk(markout) / (1.0 + EXIT_ADVERSE_RELIEF * earlier)
    util = adaptive_utility(
        trading=tev,
        completion=completion,
        inventory=inv,
        dust=dust_risk(dust_p, target=snap.dust_target, weight=snap.dust_weight),
        adverse=adverse,
        latency=float(snap.latency_cost),
    )
    return util, p, markout, capture


def hold_proposal() -> QuoteProposal:
    return QuoteProposal(1.0, 1.0, 1.0, 1.0, "HOLD")


def _classify_exit_action(urgency: float) -> str:
    score = max(0.0, min(1.0, float(urgency)))
    if score + 1e-12 >= URGENCY_AGGRESSIVE_MAX:
        return ACTION_TAKER
    if score + 1e-12 >= URGENCY_COMPETITIVE_MAX:
        return ACTION_AGGRESSIVE
    if score + 1e-12 >= URGENCY_PASSIVE_MAX:
        return ACTION_COMPETITIVE
    return ACTION_PASSIVE


def _later_action(base: str, proposed: str) -> str:
    if _ACTION_LADDER.get(proposed, 0) >= _ACTION_LADDER.get(base, 0):
        return proposed
    return base


def apply_drift_defensive_floors(
    *,
    spread_scale: float,
    size_scale: float,
    exit_urgency_scale: float,
    min_widen: float,
    max_widen: float,
    max_size: float,
    min_exit_boost: float,
    max_exit_boost: float,
) -> tuple[float, float, float]:
    """DRIFT floors: no tighten, wider spread, smaller size, earlier exit."""
    widen = max(0.0, min(float(max_widen), max(0.0, float(min_widen))))
    cap_widen = max(widen, max(0.0, float(max_widen)))
    spread = max(1.0 + widen, float(spread_scale))
    spread = _clip(spread, 1.0, 1.0 + cap_widen)
    size = min(1.0, float(size_scale), max(0.0, float(max_size)))
    boost = max(0.0, min(float(max_exit_boost), max(0.0, float(min_exit_boost))))
    cap_boost = max(boost, max(0.0, float(max_exit_boost)))
    exit_scale = max(1.0 + boost, float(exit_urgency_scale))
    exit_scale = _clip(exit_scale, 1.0, 1.0 + cap_boost)
    return spread, size, exit_scale


def apply_earlier_realization(
    *,
    base_urgency: float,
    scale: float,
    base_action: str,
    taker_allowed: bool,
    max_boost: float = 0.20,
) -> tuple[float, str]:
    """Raise Base urgency only. Never invent a Base-rejected taker exit."""
    boost = max(0.0, float(max_boost))
    scale = max(1.0, min(1.0 + boost, float(scale)))
    urgency = min(1.0, max(0.0, float(base_urgency)) * scale)
    proposed = _classify_exit_action(urgency)
    if proposed == ACTION_TAKER and not taker_allowed:
        proposed = ACTION_AGGRESSIVE
    return urgency, _later_action(str(base_action), proposed)


def candidate_proposals(
    snap: EvSnapshot,
    *,
    phase: Phase,
    max_tighten: float,
    max_widen: float,
    max_size_cut: float,
    max_exit_boost: float = 0.20,
) -> list[QuoteProposal]:
    tighten = max(0.0, min(0.15, float(max_tighten)))
    widen = max(0.0, min(0.50, float(max_widen)))
    cut = max(0.0, min(0.70, float(max_size_cut)))
    boost = max(0.0, min(0.35, float(max_exit_boost)))
    out = [hold_proposal()]
    allow_tighten = phase not in {"DRIFT", "OBSERVE", "DISABLED"}
    if allow_tighten and tighten > 0.0:
        out.append(QuoteProposal(1.0 - 0.5 * tighten, 1.0, 1.0, 1.0, "TIGHTEN_EV"))
        out.append(QuoteProposal(1.0 - tighten, 1.0, 1.0, 1.0, "TIGHTEN_EV"))
    if widen > 0.0:
        out.append(QuoteProposal(1.0 + 0.5 * widen, 1.0, 1.0, 1.0, "WIDEN_ADVERSE"))
        out.append(QuoteProposal(1.0 + widen, 1.0, 1.0, 1.0, "WIDEN_ADVERSE"))
    if cut > 0.0:
        size_scale = max(0.30, 1.0 - cut)
        out.append(QuoteProposal(1.0, size_scale, 1.0, 1.0, "CUT_SIZE"))
        if phase == "DRIFT":
            out.append(QuoteProposal(1.0 + 0.5 * widen, size_scale, 1.0, 1.0, "DRIFT_DEFENSIVE"))
        elif allow_tighten:
            out.append(QuoteProposal(1.0 - 0.5 * tighten, size_scale, 1.0, 1.0, "TIGHTEN_EV"))

    buy = snap.buy_fill
    sell = snap.sell_fill
    if (
        phase == "NORMAL"
        and snap.confidence >= 0.35
        and buy is not None
        and sell is not None
    ):
        gap = abs(float(buy) - float(sell))
        if gap >= 0.08:
            if float(buy) > float(sell):
                out.append(QuoteProposal(1.0, 1.0, 1.12, 0.88, "SIDE_BUY"))
            else:
                out.append(QuoteProposal(1.0, 1.0, 0.88, 1.12, "SIDE_SELL"))
        if gap >= 0.15:
            if float(buy) > float(sell):
                out.append(QuoteProposal(1.0, 1.0, 1.0, SIDE_SUPPRESS_SCALE, "SIDE_SUPPRESS"))
            else:
                out.append(QuoteProposal(1.0, 1.0, SIDE_SUPPRESS_SCALE, 1.0, "SIDE_SUPPRESS"))

    if snap.specialization < 0.20 and widen > 0.0 and phase != "OBSERVE":
        out.append(
            QuoteProposal(
                1.0 + 0.5 * widen,
                max(0.70, 1.0 - 0.5 * cut),
                1.0,
                1.0,
                "SPECIALIZATION",
            )
        )

    toxic = float(snap.markout_bps) <= -2.0 or float(snap.inventory_cost) >= 0.025
    aged = float(snap.exit_urgency) >= 0.22 or abs(float(snap.inventory_ratio)) >= 0.35
    if (
        boost > 0.0
        and phase not in {"OBSERVE", "DISABLED"}
        and (toxic or aged)
    ):
        out.append(
            QuoteProposal(
                1.0, 1.0, 1.0, 1.0, "EARLIER_EXIT", exit_urgency_scale=1.0 + 0.5 * boost
            )
        )
        out.append(
            QuoteProposal(
                1.0, 1.0, 1.0, 1.0, "EARLIER_EXIT", exit_urgency_scale=1.0 + boost
            )
        )
        if cut > 0.0:
            out.append(
                QuoteProposal(
                    1.0,
                    max(0.30, 1.0 - cut),
                    1.0,
                    1.0,
                    "EARLIER_EXIT",
                    exit_urgency_scale=1.0 + 0.5 * boost,
                )
            )
    return out


def choose_overlay(
    snap: EvSnapshot,
    *,
    phase: Phase,
    max_tighten: float,
    max_widen: float,
    max_size_cut: float,
    max_exit_boost: float = 0.20,
) -> EvDecision:
    hold = hold_proposal()
    base_util, base_p, base_markout, _capture = project_snapshot(snap, hold)
    best = hold
    best_util = base_util
    best_p = base_p
    best_markout = base_markout

    if phase in {"DISABLED", "OBSERVE"} or snap.confidence <= 0.0:
        return EvDecision(
            proposal=hold,
            base_ev=base_util,
            adaptive_ev=base_util,
            spread_delta=0.0,
            fill_hazard_delta=0.0,
            markout_delta=0.0,
            exit_urgency_delta=0.0,
            reason="HOLD",
            confidence=float(snap.confidence),
            accepted=False,
        )

    boost = max(0.0, min(0.35, float(max_exit_boost)))
    for proposal in candidate_proposals(
        snap,
        phase=phase,
        max_tighten=max_tighten,
        max_widen=max_widen,
        max_size_cut=max_size_cut,
        max_exit_boost=boost,
    ):
        if proposal.reason == "HOLD":
            continue
        if proposal.size_scale > 1.0 + 1e-12:
            continue
        if proposal.exit_urgency_scale < 1.0 - 1e-12:
            continue
        if proposal.exit_urgency_scale > 1.0 + boost + 1e-12:
            continue
        if phase == "DRIFT" and proposal.spread_scale < 1.0 - 1e-12:
            continue
        util, p, markout, _cap = project_snapshot(snap, proposal)
        if proposal.spread_scale < 1.0 - 1e-12 and util <= base_util + HOLD_EPS:
            continue
        if util > best_util + HOLD_EPS:
            best = proposal
            best_util = util
            best_p = p
            best_markout = markout

    accepted = best.reason != "HOLD"
    return EvDecision(
        proposal=best,
        base_ev=base_util,
        adaptive_ev=best_util,
        spread_delta=best.spread_scale - 1.0,
        fill_hazard_delta=best_p - base_p,
        markout_delta=best_markout - base_markout,
        exit_urgency_delta=best.exit_urgency_scale - 1.0,
        reason=best.reason,
        confidence=float(snap.confidence),
        accepted=accepted,
    )
