# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.5 execution economics overlay.

A1.5 preserves the A1.1 lifecycle dead-gate correction and separate Maker/Taker
telemetry, but disables Direct directional Taker *entry* after A1.3+A1.4 produced
2 positive versus 32 negative Taker-origin round trips. Taker EXIT remains owned
by PositionExitController. Kappa/coverage cannot re-enable Taker acquisition.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any

DIRECT_ECONOMICS_VERSION = "direct_economics_v4_16_2_a1_5"
DIRECT_EXECUTION_CONTROLLER_VERSION = "direct_execution_controller_v4_16_2_a1_5"

ACTION_MAKER = "MAKER"
ACTION_TAKER = "TAKER"
ACTION_SKIP = "SKIP"

# Retained only for counterfactual telemetry. A1.5 does not authorize Taker entry.
TAKER_ALPHA_SCALE_BPS = 4.0
TAKER_EDGE_SCALE_BPS = 8.0
DIRECT_TAKER_MIN_EV = 0.20
DIRECT_TAKER_MIN_EDGE_BPS = 2.0
DIRECT_TAKER_ENTRY_ENABLED = False
NEGATIVE_UTILITY = -1.0e9
DIRECT_MAKER_MIN_EV = 0.030

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
    bps = _finite(fee_bps)
    return 0.02 * math.tanh(bps / TAKER_EDGE_SCALE_BPS)


def direct_lifecycle_breakdown(base: Any, *, min_trading_ev: float = 0.0):
    """Keep A1.1's corrected Direct LifecycleEV authority.

    Strategy-wide latency and duplicate adverse-selection telemetry are not hard
    per-book deductions.  ``base.trading_ev`` already uses the Direct A1.5
    learned lifecycle fee input supplied by the strategy overlay.
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
    taker_net_edge_bps: float
    maker_min_ev: float
    maker_ev_margin: float
    taker_min_ev: float
    taker_ev_margin: float
    taker_min_edge_bps: float
    taker_edge_margin_bps: float

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
            "taker_net_edge_bps": self.taker_net_edge_bps,
            "maker_min_ev": self.maker_min_ev,
            "maker_ev_margin": self.maker_ev_margin,
            "taker_min_ev": self.taker_min_ev,
            "taker_ev_margin": self.taker_ev_margin,
            "taker_min_edge_bps": self.taker_min_edge_bps,
            "taker_edge_margin_bps": self.taker_edge_margin_bps,
        }


def maker_economic_ev(*, lifecycle_ev: float, maker_fee_bps: float = 0.0) -> float:
    """Maker economics without a second actionable-fill multiplication."""
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
    """Independent immediate Taker-entry economics.

    Returns ``(ev, expected_directional_move_bps, total_cost_bps)``.  Negative
    dynamic fees remain clipped exactly as in A1.3; UID239 fee hypotheses are
    observation-only and are intentionally not introduced into this branch.
    """
    signal = _clip01(abs(_finite(directional_score)))
    expected_move = signal * max(0.0, _finite(alpha_scale_bps, TAKER_ALPHA_SCALE_BPS))
    crossing = max(0.0, _finite(crossing_bps))
    fee = max(0.0, _finite(taker_fee_bps))
    slip = max(0.0, _finite(slippage_bps))
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
    taker_min_ev: float = DIRECT_TAKER_MIN_EV,
    taker_min_edge_bps: float = DIRECT_TAKER_MIN_EDGE_BPS,
) -> DirectExecutionDecision:
    """Choose Maker / Taker / Skip from separate positive economics."""
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
    taker_floor = max(0.0, _finite(taker_min_ev, DIRECT_TAKER_MIN_EV))
    edge_floor = max(0.0, _finite(taker_min_edge_bps, DIRECT_TAKER_MIN_EDGE_BPS))
    net_edge = expected_move - total_cost
    maker_u = maker_ev if maker_ev + 1e-12 >= maker_floor else NEGATIVE_UTILITY
    taker_pass = (
        DIRECT_TAKER_ENTRY_ENABLED
        and (not neutral_fallback)
        and taker_ev + 1e-12 >= taker_floor
        and net_edge + 1e-12 >= edge_floor
    )
    taker_u = taker_ev if taker_pass else NEGATIVE_UTILITY

    common = dict(
        maker_economic_ev=maker_ev,
        taker_economic_ev=taker_ev,
        directional_score=signal,
        expected_directional_move_bps=expected_move,
        taker_crossing_bps=max(0.0, _finite(crossing_bps)),
        taker_total_cost_bps=total_cost,
        taker_net_edge_bps=net_edge,
        maker_min_ev=maker_floor,
        maker_ev_margin=maker_ev - maker_floor,
        taker_min_ev=taker_floor,
        taker_ev_margin=taker_ev - taker_floor,
        taker_min_edge_bps=edge_floor,
        taker_edge_margin_bps=net_edge - edge_floor,
    )

    if maker_u >= taker_u and maker_u > skip_u:
        return DirectExecutionDecision(
            action=ACTION_MAKER, maker_utility=maker_u, taker_utility=taker_u,
            skip_utility=skip_u, maker_size=float(maker_size), taker_size=0.0,
            reason="MAKER_MARGIN_EV", **common,
        )
    if taker_u > maker_u and taker_u > skip_u:
        return DirectExecutionDecision(
            action=ACTION_TAKER, maker_utility=maker_u, taker_utility=taker_u,
            skip_utility=skip_u, maker_size=0.0, taker_size=float(taker_clip),
            reason="TAKER_STRONG_DIRECTIONAL_EV", **common,
        )
    return DirectExecutionDecision(
        action=ACTION_SKIP, maker_utility=maker_u, taker_utility=taker_u,
        skip_utility=skip_u, maker_size=0.0, taker_size=0.0,
        reason="NO_POSITIVE_EXECUTION_EV", **common,
    )
