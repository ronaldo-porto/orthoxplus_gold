# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.6.0 observable execution economics.

Maker acquisition is authorized by current observable half-spread capture minus
the signed current Maker entry fee. Learned lifecycle quality and future-exit
forecasts are not entry authority. Directional Taker entry remains disabled.
Legacy helper APIs are retained for regression compatibility and telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
import math
from typing import Any

DIRECT_ECONOMICS_VERSION = "direct_economics_v4_16_2_a1_6_0"
DIRECT_EXECUTION_CONTROLLER_VERSION = "direct_execution_controller_v4_16_2_a1_6_0"

ACTION_MAKER = "MAKER"
ACTION_TAKER = "TAKER"
ACTION_SKIP = "SKIP"

# Retained only for counterfactual telemetry. A1.6 does not authorize Taker entry.
TAKER_ALPHA_SCALE_BPS = 4.0
TAKER_EDGE_SCALE_BPS = 8.0
DIRECT_TAKER_MIN_EV = 0.20
DIRECT_TAKER_MIN_EDGE_BPS = 2.0
DIRECT_TAKER_ENTRY_ENABLED = False
NEGATIVE_UTILITY = -1.0e9
DIRECT_MAKER_MIN_EV = 0.0
DIRECT_MAKER_MIN_EDGE_BPS = 2.5

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


@dataclass(frozen=True)
class DirectMakerLifecycleFeeCost:
    maker_entry_fee_bps: float
    expected_maker_exit_fee_bps: float
    expected_taker_exit_fee_bps: float
    expected_exit_fee_bps: float
    explicit_fee_cost_bps: float
    expected_taker_path_explicit_fee_bps: float
    learned_net_shortfall_bps: float
    residual_downside_bps: float
    holding_risk_bps: float
    total_cost_bps: float
    taker_exit_probability: float

    def as_log(self) -> dict[str, float]:
        return {
            "direct_maker_entry_fee_bps": self.maker_entry_fee_bps,
            "direct_expected_maker_exit_fee_bps": self.expected_maker_exit_fee_bps,
            "direct_expected_taker_exit_fee_bps": self.expected_taker_exit_fee_bps,
            "direct_expected_exit_fee_bps": self.expected_exit_fee_bps,
            "direct_explicit_fee_cost_bps": self.explicit_fee_cost_bps,
            "direct_expected_taker_path_explicit_fee_bps": self.expected_taker_path_explicit_fee_bps,
            "direct_learned_net_shortfall_bps": self.learned_net_shortfall_bps,
            "direct_residual_downside_bps": self.residual_downside_bps,
            "direct_holding_risk_bps": self.holding_risk_bps,
            "direct_lifecycle_total_cost_bps": self.total_cost_bps,
            "direct_taker_exit_probability": self.taker_exit_probability,
        }


def maker_lifecycle_fee_cost_bps(
    *,
    maker_entry_fee_bps: float,
    maker_exit_fee_bps: float,
    taker_exit_fee_bps: float,
    taker_exit_probability: float,
    learned_net_shortfall_bps: float = 0.0,
    holding_risk_bps: float = 0.0,
) -> DirectMakerLifecycleFeeCost:
    """True lifecycle fee budget for a Maker acquisition.

    A1.5 compressed the live Maker fee into a tiny utility term after the
    lifecycle score.  That let a +11 bps price opportunity pass even when the
    entry+expected-exit Maker fees exceeded 24 bps.  A1.5.1 prices the signed
    Maker entry fee and role-weighted expected exit fee directly in bps.

    Learned downside is based on *net* realized bps, so the explicit positive
    fee budget is removed from that learned shortfall before adding the
    residual downside.  This prevents double counting fees while retaining
    adverse realized shortfall beyond the known execution costs.
    """
    p_taker = _clip01(taker_exit_probability)
    entry_fee = _finite(maker_entry_fee_bps)
    maker_exit_fee = _finite(maker_exit_fee_bps)
    # Keep A1.5's conservative Taker-fee treatment: negative Taker rebates are
    # observation-only for now and do not subsidize this branch.
    taker_exit_fee = max(0.0, _finite(taker_exit_fee_bps))
    expected_maker_exit_fee = (1.0 - p_taker) * maker_exit_fee
    expected_taker_exit_fee = p_taker * taker_exit_fee
    expected_exit_fee = expected_maker_exit_fee + expected_taker_exit_fee
    explicit_fee_cost = entry_fee + expected_exit_fee
    learned_shortfall = max(0.0, _finite(learned_net_shortfall_bps))
    # The learned shortfall is conditional on Taker-exit lifecycles, so remove
    # only the explicit fees expected on that same Taker path.  Maker-exit fees
    # belong to the complementary path and must not erase adverse Taker drift.
    expected_taker_path_explicit_fee = p_taker * max(0.0, entry_fee + taker_exit_fee)
    residual_downside = max(0.0, learned_shortfall - expected_taker_path_explicit_fee)
    holding = max(0.0, _finite(holding_risk_bps))
    total = explicit_fee_cost + residual_downside + holding
    return DirectMakerLifecycleFeeCost(
        maker_entry_fee_bps=float(entry_fee),
        expected_maker_exit_fee_bps=float(expected_maker_exit_fee),
        expected_taker_exit_fee_bps=float(expected_taker_exit_fee),
        expected_exit_fee_bps=float(expected_exit_fee),
        explicit_fee_cost_bps=float(explicit_fee_cost),
        expected_taker_path_explicit_fee_bps=float(expected_taker_path_explicit_fee),
        learned_net_shortfall_bps=float(learned_shortfall),
        residual_downside_bps=float(residual_downside),
        holding_risk_bps=float(holding),
        total_cost_bps=float(total),
        taker_exit_probability=float(p_taker),
    )


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
    maker_current_edge_bps: float = 0.0
    maker_min_edge_bps: float = DIRECT_MAKER_MIN_EDGE_BPS
    maker_edge_margin_bps: float = 0.0

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
            "maker_current_edge_bps": self.maker_current_edge_bps,
            "maker_min_edge_bps": self.maker_min_edge_bps,
            "maker_edge_margin_bps": self.maker_edge_margin_bps,
        }


def maker_economic_ev(*, lifecycle_ev: float, maker_fee_bps: float = 0.0) -> float:
    """Maker economics after lifecycle fees are already priced upstream.

    ``maker_fee_bps`` is retained in the call signature for telemetry/backward
    compatibility, but A1.5.1 does not subtract a second compressed fee term.
    """
    _ = maker_fee_bps
    return _finite(lifecycle_ev)


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
    maker_current_edge_bps: float | None = None,
    maker_min_edge_bps: float = DIRECT_MAKER_MIN_EDGE_BPS,
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
    maker_edge = _finite(maker_current_edge_bps, maker_ev)
    maker_edge_floor = max(0.0, _finite(maker_min_edge_bps, DIRECT_MAKER_MIN_EDGE_BPS))
    taker_floor = max(0.0, _finite(taker_min_ev, DIRECT_TAKER_MIN_EV))
    edge_floor = max(0.0, _finite(taker_min_edge_bps, DIRECT_TAKER_MIN_EDGE_BPS))
    net_edge = expected_move - total_cost
    # A1.6 entry authority is current observable Maker edge in bps.  Lifecycle
    # EV remains telemetry/sizing only and cannot veto an otherwise good current
    # spread/fee opportunity.
    maker_pass = maker_edge + 1e-12 >= maker_edge_floor
    maker_u = maker_ev if maker_pass else NEGATIVE_UTILITY
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
        maker_current_edge_bps=maker_edge,
        maker_min_edge_bps=maker_edge_floor,
        maker_edge_margin_bps=maker_edge - maker_edge_floor,
        taker_min_ev=taker_floor,
        taker_ev_margin=taker_ev - taker_floor,
        taker_min_edge_bps=edge_floor,
        taker_edge_margin_bps=net_edge - edge_floor,
    )

    if maker_u >= taker_u and maker_u > skip_u:
        return DirectExecutionDecision(
            action=ACTION_MAKER, maker_utility=maker_u, taker_utility=taker_u,
            skip_utility=skip_u, maker_size=float(maker_size), taker_size=0.0,
            reason="MAKER_CURRENT_EDGE", **common,
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
