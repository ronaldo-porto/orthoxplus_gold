# SPDX-License-Identifier: MIT
"""Bounded Adaptive expected-value overlay.

BaseStrategy remains the safe baseline. This module only scores small quote
corrections and accepts one when AdaptiveUtility improves.

AdaptiveUtility =
    TradingEV + CompletionValue
    - InventoryRisk - DustRisk - AdverseSelectionRisk - LatencyRisk

Tightening is allowed only when that inequality holds. Size scale is never
greater than 1. No Strategy1 / Research / score_ev imports.
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


@dataclass(frozen=True)
class QuoteProposal:
    spread_scale: float
    size_scale: float
    buy_bias_scale: float
    sell_bias_scale: float
    reason: str


@dataclass(frozen=True)
class EvDecision:
    proposal: QuoteProposal
    base_ev: float
    adaptive_ev: float
    spread_delta: float
    fill_hazard_delta: float
    markout_delta: float
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
            "reason": self.reason,
            "confidence": self.confidence,
            "accepted": int(self.accepted),
            "spread_scale": self.proposal.spread_scale,
            "size_scale": self.proposal.size_scale,
            "buy_bias_scale": self.proposal.buy_bias_scale,
            "sell_bias_scale": self.proposal.sell_bias_scale,
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
    inv = float(snap.inventory_cost) * (float(proposal.size_scale) ** 2)
    tev = trading_ev(p, capture, markout, snap.fees_bps) * float(proposal.size_scale)
    util = adaptive_utility(
        trading=tev,
        completion=float(snap.completion_value),
        inventory=inv,
        dust=dust_risk(dust_p, target=snap.dust_target, weight=snap.dust_weight),
        adverse=adverse_selection_risk(markout),
        latency=float(snap.latency_cost),
    )
    return util, p, markout, capture


def hold_proposal() -> QuoteProposal:
    return QuoteProposal(1.0, 1.0, 1.0, 1.0, "HOLD")


def candidate_proposals(
    snap: EvSnapshot,
    *,
    phase: Phase,
    max_tighten: float,
    max_widen: float,
    max_size_cut: float,
) -> list[QuoteProposal]:
    tighten = max(0.0, min(0.15, float(max_tighten)))
    widen = max(0.0, min(0.50, float(max_widen)))
    cut = max(0.0, min(0.70, float(max_size_cut)))
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
        and abs(float(buy) - float(sell)) >= 0.08
    ):
        if float(buy) > float(sell):
            out.append(QuoteProposal(1.0, 1.0, 1.12, 0.88, "SIDE_BUY"))
        else:
            out.append(QuoteProposal(1.0, 1.0, 0.88, 1.12, "SIDE_SELL"))

    if snap.specialization < 0.20 and widen > 0.0 and phase != "OBSERVE":
        out.append(QuoteProposal(1.0 + 0.5 * widen, max(0.70, 1.0 - 0.5 * cut), 1.0, 1.0, "SPECIALIZATION"))
    return out


def choose_overlay(
    snap: EvSnapshot,
    *,
    phase: Phase,
    max_tighten: float,
    max_widen: float,
    max_size_cut: float,
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
            reason="HOLD",
            confidence=float(snap.confidence),
            accepted=False,
        )

    for proposal in candidate_proposals(
        snap,
        phase=phase,
        max_tighten=max_tighten,
        max_widen=max_widen,
        max_size_cut=max_size_cut,
    ):
        if proposal.reason == "HOLD":
            continue
        if proposal.size_scale > 1.0 + 1e-12:
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
        reason=best.reason,
        confidence=float(snap.confidence),
        accepted=accepted,
    )
