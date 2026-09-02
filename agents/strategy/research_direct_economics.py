# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.2 economics overlay.

This module intentionally does not modify the frozen V4.16.2 Research baseline.
It corrects only the Direct candidate's two runtime defects observed on Agent 68:

1. global strategy latency and a second adverse-selection charge were acting as
   hard per-book LifecycleEV vetoes even though expected markout is already in
   TradingEV; and
2. Taker reused Maker LifecycleEV and could be rescued by Kappa completion
   value instead of requiring positive directional crossing economics.

Maker lifecycle remains fill-weighted upstream in ``research_score_ev``.
Taker economics are independent and require expected directional move to beat
crossing + taker fee + slippage + a conservative markout buffer.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any

DIRECT_ECONOMICS_VERSION = "direct_economics_v4_16_2_a1_2"
DIRECT_EXECUTION_CONTROLLER_VERSION = "direct_execution_controller_v4_16_2_a1_2"

ACTION_MAKER = "MAKER"
ACTION_TAKER = "TAKER"
ACTION_SKIP = "SKIP"

# DirectionForecast.score is a dimensionless directional signal.  A full-scale
# signal is conservatively mapped to one Score-EV edge scale (8 bps).  This is
# deliberately bounded; Kappa/coverage never increases it.
TAKER_ALPHA_SCALE_BPS = 8.0
TAKER_EDGE_SCALE_BPS = 8.0
NEGATIVE_UTILITY = -1.0e9
# A1.1 accepted any MakerEV > 0.  Agent-68 showed the 0-0.04 region was
# negative in aggregate.  A1.2 keeps a small model-error margin while leaving
# Taker economics independent.
DIRECT_MAKER_MIN_EV = 0.030

# These reasons are mechanical/risk authorities and may never be rescued by the
# Direct overlay.  NEGATIVE_EV is deliberately absent because A1.2 recomputes
# Direct LifecycleEV without global latency and duplicate adverse penalties.
HARD_REJECT_REASONS = {
    "TOXIC",
    "INVENTORY_BLOCKED",
    "UNSAFE",
    "INVALID_SIZE",
    "VOLUME_CAP",
}


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value)))


def _maker_fee_term_ev(fee_bps: Any) -> float:
    """Small signed Maker fee/rebate term in the existing controller scale."""
    bps = _finite(fee_bps)
    return 0.02 * math.tanh(bps / TAKER_EDGE_SCALE_BPS)


def direct_lifecycle_breakdown(base: Any, *, min_trading_ev: float = 0.0):
    """Return the A1.1 Direct LifecycleEV view of a ScoreEVBreakdown.

    ``base.trading_ev`` already contains:
      actionable fill probability × (spread capture + expected markout
      - expected future Taker realization cost - holding risk).

    Therefore Direct A1.2 preserves A1.1 and does *not* subtract global strategy latency from per-book
    economics and does *not* charge adverse selection a second time after
    expected markout.  Both remain logged telemetry for diagnosis.

    Dust and current inventory costs remain economic because they are distinct
    lifecycle risks.  Existing mechanical hard rejects remain authoritative.
    """
    reason = str(getattr(base, "reject_reason", "") or "").upper()
    hard_reject = reason in HARD_REJECT_REASONS

    trading = _finite(getattr(base, "trading_ev", 0.0))
    dust = max(0.0, _finite(getattr(base, "dust_cost", 0.0)))
    inventory = max(0.0, _finite(getattr(base, "inventory_cost", 0.0)))
    lifecycle = trading - dust - inventory
    floor = _finite(min_trading_ev, 0.0)
    eligible = (not hard_reject) and lifecycle >= floor

    total_score = max(0.0, _finite(getattr(base, "total_score_component", 0.0)))
    final_score = lifecycle + total_score if eligible else float("-inf")
    reject_reason = None if eligible else (reason if hard_reject else "NEGATIVE_EV")

    # Keep latency/adverse raw telemetry in their original fields, but make the
    # A1.1 economic penalties explicit as zero so logs prove they are not gates.
    return replace(
        base,
        final_score=final_score,
        eligible=eligible,
        reject_reason=reject_reason,
        lifecycle_ev=lifecycle if eligible else float("-inf"),
        base_lifecycle_value=lifecycle,
        adverse_penalty=0.0,
        latency_penalty=0.0,
        required_entry_ev=floor,
        entry_ev_margin=lifecycle - floor,
        entry_ev_pass=eligible,
    )


@dataclass(frozen=True)
class DirectExecutionDecision:
    action: str
    maker_utility: float
    taker_utility: float
    skip_utility: float
    maker_size: float
    taker_size: float
    reason: str
    maker_economic_ev: float
    taker_economic_ev: float
    directional_score: float
    expected_directional_move_bps: float
    taker_crossing_bps: float
    taker_total_cost_bps: float
    maker_min_ev: float
    maker_ev_margin: float

    def as_log(self) -> dict[str, Any]:
        return {
            "execution_controller_version": DIRECT_EXECUTION_CONTROLLER_VERSION,
            "direct_economics_version": DIRECT_ECONOMICS_VERSION,
            "selected_action": self.action,
            "maker_utility": self.maker_utility,
            "taker_utility": self.taker_utility,
            "skip_utility": self.skip_utility,
            "maker_size": self.maker_size,
            "taker_size": self.taker_size,
            "reason": self.reason,
            "maker_economic_ev": self.maker_economic_ev,
            "taker_economic_ev": self.taker_economic_ev,
            "directional_score": self.directional_score,
            "expected_directional_move_bps": self.expected_directional_move_bps,
            "taker_crossing_bps": self.taker_crossing_bps,
            "taker_total_cost_bps": self.taker_total_cost_bps,
            "maker_min_ev": self.maker_min_ev,
            "maker_ev_margin": self.maker_ev_margin,
        }


def maker_economic_ev(*, lifecycle_ev: float, maker_fee_bps: float = 0.0) -> float:
    """Maker economics without a second actionable-fill multiplication.

    LifecycleEV is already fill-weighted by TradingEV.  A1 multiplied it by
    ``p_fill`` again inside MakerUtility, effectively applying P(fill)^2.
    """
    return _finite(lifecycle_ev) - _maker_fee_term_ev(maker_fee_bps)


def taker_economic_ev(
    *,
    directional_score: float,
    crossing_bps: float,
    taker_fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    expected_markout_bps: float = 0.0,
    alpha_scale_bps: float = TAKER_ALPHA_SCALE_BPS,
    edge_scale_bps: float = TAKER_EDGE_SCALE_BPS,
) -> tuple[float, float, float]:
    """Independent immediate Taker economics.

    Returns ``(ev, expected_directional_move_bps, total_cost_bps)``.
    Kappa/coverage is intentionally absent: score pressure can rank an already
    economic opportunity, but it cannot make a negative Taker entry positive.
    """
    signal = _clip01(abs(_finite(directional_score)))
    expected_move = signal * max(0.0, _finite(alpha_scale_bps, TAKER_ALPHA_SCALE_BPS))
    crossing = max(0.0, _finite(crossing_bps))
    fee = max(0.0, _finite(taker_fee_bps))
    slip = max(0.0, _finite(slippage_bps))
    # The conservative Maker markout prior is not charged to Maker twice, but it
    # is useful as a toxicity buffer for an immediate directional Taker entry.
    adverse_buffer = max(0.0, -_finite(expected_markout_bps))
    total_cost = crossing + fee + slip + adverse_buffer
    scale = max(1e-6, _finite(edge_scale_bps, TAKER_EDGE_SCALE_BPS))
    return math.tanh((expected_move - total_cost) / scale), expected_move, total_cost


def choose_direct_execution(
    *,
    maker_lifecycle_ev: float,
    directional_score: float,
    crossing_bps: float,
    maker_size: float,
    taker_clip: float,
    maker_fee_bps: float = 0.0,
    taker_fee_bps: float = 0.0,
    slippage_bps: float = 0.0,
    expected_markout_bps: float = 0.0,
    neutral_fallback: bool = False,
    skip_utility: float = 0.0,
    maker_min_ev: float = DIRECT_MAKER_MIN_EV,
) -> DirectExecutionDecision:
    """Choose Maker / Taker / Skip from separate positive economics.

    TotalScore/Kappa is deliberately not an argument.  It already selected the
    candidate upstream; it may never subsidize a negative execution mode.
    """
    maker_ev = maker_economic_ev(
        lifecycle_ev=maker_lifecycle_ev,
        maker_fee_bps=maker_fee_bps,
    )
    if neutral_fallback:
        taker_ev, expected_move, total_cost = NEGATIVE_UTILITY, 0.0, (
            max(0.0, _finite(crossing_bps))
            + max(0.0, _finite(taker_fee_bps))
            + max(0.0, _finite(slippage_bps))
            + max(0.0, -_finite(expected_markout_bps))
        )
        signal = 0.0
    else:
        signal = _clip01(abs(_finite(directional_score)))
        taker_ev, expected_move, total_cost = taker_economic_ev(
            directional_score=signal,
            crossing_bps=crossing_bps,
            taker_fee_bps=taker_fee_bps,
            slippage_bps=slippage_bps,
            expected_markout_bps=expected_markout_bps,
        )

    skip_u = _finite(skip_utility)
    maker_floor = max(0.0, _finite(maker_min_ev, DIRECT_MAKER_MIN_EV))
    maker_u = maker_ev if maker_ev + 1e-12 >= maker_floor else NEGATIVE_UTILITY
    taker_u = taker_ev if taker_ev > 0.0 else NEGATIVE_UTILITY

    # Prefer Maker on an exact tie: it avoids crossing and preserves optionality.
    if maker_u >= taker_u and maker_u > skip_u:
        return DirectExecutionDecision(
            ACTION_MAKER, maker_u, taker_u, skip_u,
            float(maker_size), 0.0, "MAKER_MARGIN_EV",
            maker_ev, taker_ev, signal, expected_move,
            max(0.0, _finite(crossing_bps)), total_cost,
            maker_floor, maker_ev - maker_floor,
        )
    if taker_u > maker_u and taker_u > skip_u:
        return DirectExecutionDecision(
            ACTION_TAKER, maker_u, taker_u, skip_u,
            0.0, float(taker_clip), "TAKER_POSITIVE_DIRECTIONAL_EV",
            maker_ev, taker_ev, signal, expected_move,
            max(0.0, _finite(crossing_bps)), total_cost,
            maker_floor, maker_ev - maker_floor,
        )
    return DirectExecutionDecision(
        ACTION_SKIP, maker_u, taker_u, skip_u,
        0.0, 0.0, "NO_POSITIVE_EXECUTION_EV",
        maker_ev, taker_ev, signal, expected_move,
        max(0.0, _finite(crossing_bps)), total_cost,
        maker_floor, maker_ev - maker_floor,
    )
