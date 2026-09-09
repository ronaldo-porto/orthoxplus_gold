# SPDX-License-Identifier: MIT
"""A1.7.4.4 positive-Maker Kappa risk veto.

This overlay is intentionally narrow. Runtime evidence from A1.7.4.3.2 showed
that every negative Taker-ending round trip was authorized by the MTM risk path
(`HARD_ESCAPE_CLIP` or `ABSOLUTE_PROTECTION_REDUCE`) while an executable Maker
completion remained strongly positive. Those risk reductions were not
catastrophic/MAX-exposure events.

A1.7.4.4 therefore gives a *strongly positive* executable Maker completion
priority over a negative risk-authority Taker, independent of failed-exit
count. Genuine catastrophic/MAX-exposure authority is never vetoed. Marginal
Maker economics are also left unchanged so this patch does not create a new
liveness policy.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_TAKER_EXIT,
    PositionExitDecision,
)

DIRECT_POSITIVE_MAKER_KAPPA_VERSION = "direct_positive_maker_kappa_v4_16_2_a1_7_4_4"

# The bad A1.7.4.3.2 Taker sample had Maker completion values roughly +54 to
# +221 bps. Use a deliberately stronger threshold than the legacy +1 bps veto
# so A1.7.4.4 blocks only unambiguous positive-Maker opportunities and leaves
# marginal/liveness cases to the frozen A1.7.2/A1.7.4 authority.
DIRECT_A1744_STRONG_MAKER_FLOOR_BPS = 10.0
DIRECT_A1744_RISK_TAKER_REASONS = frozenset({
    "HARD_ESCAPE_CLIP",
    "ABSOLUTE_PROTECTION_REDUCE",
})


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if v == v and abs(v) != float("inf") else default


def apply_positive_maker_kappa_veto(
    *,
    base_decision: PositionExitDecision,
    maker_net_bps: float,
    taker_net_bps: float,
    maker_executable: bool,
    catastrophic_hard_risk: bool,
    inventory_qty: float,
    strong_maker_floor_bps: float = DIRECT_A1744_STRONG_MAKER_FLOOR_BPS,
) -> PositionExitDecision:
    """Veto only a negative non-catastrophic risk Taker with strong Maker EV.

    The failed-exit counter is intentionally absent. A1.7.4.3.2 runtime showed
    that failed-exit escalation itself was the path that eventually overrode a
    +54..+221 bps Maker completion. A truly hard mechanical emergency is already
    represented by ``catastrophic_hard_risk`` and bypasses this overlay.
    """
    reason = str(getattr(base_decision, "reason", "") or "")
    action = str(getattr(base_decision, "action", "") or "")
    maker = _finite(maker_net_bps)
    taker = _finite(taker_net_bps)
    floor = max(0.0, _finite(strong_maker_floor_bps, DIRECT_A1744_STRONG_MAKER_FLOOR_BPS))

    if action != ACTION_TAKER_EXIT or reason not in DIRECT_A1744_RISK_TAKER_REASONS:
        return base_decision
    if bool(catastrophic_hard_risk):
        return base_decision
    if not bool(maker_executable):
        return base_decision
    if taker >= -1e-12:
        # A non-negative executable Taker is not the observed Kappa-tail defect.
        return base_decision
    if maker + 1e-12 < floor:
        return base_decision

    return replace(
        base_decision,
        action=ACTION_MAKER_EXIT,
        selected_qty=max(0.0, abs(_finite(inventory_qty))),
        reason="A1744_POSITIVE_MAKER_RISK_VETO",
        corridor_action="DIRECT_POSITIVE_MAKER_KAPPA_A1744",
        corridor_stage="POSITIVE_MAKER_KAPPA_VETO",
    )


def classify_a1744_outcome(
    *,
    base_decision: PositionExitDecision,
    final_decision: PositionExitDecision,
    maker_net_bps: float,
    taker_net_bps: float,
    maker_executable: bool,
    catastrophic_hard_risk: bool,
    strong_maker_floor_bps: float = DIRECT_A1744_STRONG_MAKER_FLOOR_BPS,
) -> str | None:
    """Return a stable telemetry label for decisions in the A1.7.4.4 scope."""
    base_reason = str(getattr(base_decision, "reason", "") or "")
    base_action = str(getattr(base_decision, "action", "") or "")
    if base_action != ACTION_TAKER_EXIT or base_reason not in DIRECT_A1744_RISK_TAKER_REASONS:
        return None
    if str(getattr(final_decision, "reason", "") or "") == "A1744_POSITIVE_MAKER_RISK_VETO":
        return "A1744_POSITIVE_MAKER_RISK_VETO"
    if bool(catastrophic_hard_risk):
        return "A1744_TAKER_ALLOWED_CATASTROPHIC"
    maker = _finite(maker_net_bps)
    taker = _finite(taker_net_bps)
    floor = max(0.0, _finite(strong_maker_floor_bps, DIRECT_A1744_STRONG_MAKER_FLOOR_BPS))
    if (not bool(maker_executable)) or maker + 1e-12 < floor:
        return "A1744_TAKER_ALLOWED_MAKER_NOT_STRONG"
    if taker >= -1e-12:
        return "A1744_TAKER_ALLOWED_NONNEGATIVE"
    return None
