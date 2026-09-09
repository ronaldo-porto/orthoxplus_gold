# SPDX-License-Identifier: MIT
"""A1.7.5 positive-Maker Kappa risk veto (A1.7.4.4 absolute arm + relative arm).

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

A1.7.5 adds a second, *relative* arm. The A1.7.4.5 runtime showed 84 forced
crossings where the absolute +10 bps arm could not fire but Maker was still
strictly better than Taker in 82 of 84 cases (median advantage +15.8 bps, mean
+21.1 bps; 27 cases exceeded 30 bps advantage with mean Maker -2.7 vs Taker
-48.7). The absolute arm asks "is Maker good?" when the decision is inherently
relative: "is Maker better than crossing?". The relative arm asks the latter,
bounded by the same per-band recovery floors A1.7.4 already uses so a genuinely
unbounded Maker can still cross.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

from research_direct_tail_recovery import (
    DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_RECOVERY_MAKER_FLOOR_BPS,
)
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_TAKER_EXIT,
    BAND_ABSOLUTE,
    BAND_DEFENSIVE,
    BAND_HARD_ESCAPE,
    PositionExitDecision,
)

DIRECT_POSITIVE_MAKER_KAPPA_VERSION = "direct_positive_maker_kappa_v4_16_2_a1_7_5"

# The bad A1.7.4.3.2 Taker sample had Maker completion values roughly +54 to
# +221 bps. Use a deliberately stronger threshold than the legacy +1 bps veto
# so A1.7.4.4 blocks only unambiguous positive-Maker opportunities and leaves
# marginal/liveness cases to the frozen A1.7.2/A1.7.4 authority.
DIRECT_A1744_STRONG_MAKER_FLOOR_BPS = 10.0
DIRECT_A1744_RISK_TAKER_REASONS = frozenset({
    "HARD_ESCAPE_CLIP",
    "ABSOLUTE_PROTECTION_REDUCE",
})

# A1.7.5 relative arm. 15.0 bps is the observed median Maker advantage across
# the 84 A1.7.4.5 forced crossings, so the arm fires on the median case
# (Maker -19.0 vs Taker -37.5) while leaving marginal cases to cross.
DIRECT_A175_MAKER_ADVANTAGE_BPS = 15.0

# The relative arm is bounded by the same per-band floors A1.7.4 recovery uses.
# A Maker below its band floor is not a recovery opportunity regardless of how
# much worse the Taker looks, so it still crosses.
DIRECT_A175_BAND_FLOOR_BPS = {
    BAND_DEFENSIVE: DIRECT_RECOVERY_MAKER_FLOOR_BPS,
    BAND_HARD_ESCAPE: DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS,
    BAND_ABSOLUTE: DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS,
}


def a175_band_floor_bps(risk_band: str) -> float | None:
    """Return the A1.7.5 relative-arm Maker floor for a risk band, if any."""
    return DIRECT_A175_BAND_FLOOR_BPS.get(str(risk_band or "").upper())


def a175_relative_arm_allows(
    *,
    maker_net_bps: float,
    taker_net_bps: float,
    risk_band: str,
    advantage_bps: float = DIRECT_A175_MAKER_ADVANTAGE_BPS,
) -> bool:
    """True when Maker is materially better than crossing and still bounded."""
    floor = a175_band_floor_bps(risk_band)
    if floor is None:
        return False
    maker = _finite(maker_net_bps)
    taker = _finite(taker_net_bps)
    advantage = max(0.0, _finite(advantage_bps, DIRECT_A175_MAKER_ADVANTAGE_BPS))
    if maker + 1e-12 < float(floor):
        return False
    return (maker - taker) + 1e-12 >= advantage


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
    advantage_bps: float = DIRECT_A175_MAKER_ADVANTAGE_BPS,
    tail_budget_exhausted: bool = False,
) -> PositionExitDecision:
    """Veto a negative non-catastrophic risk Taker when Maker is the better exit.

    Two independent arms authorize the veto:

    * the frozen A1.7.4.4 *absolute* arm, ``maker >= +10 bps``;
    * the A1.7.5 *relative* arm, ``maker - taker >= 15 bps`` while ``maker``
      remains at or above its per-band recovery floor.

    The failed-exit counter is intentionally absent. A1.7.4.3.2 runtime showed
    that failed-exit escalation itself was the path that eventually overrode a
    +54..+221 bps Maker completion. A truly hard mechanical emergency is already
    represented by ``catastrophic_hard_risk`` and bypasses this overlay.

    ``tail_budget_exhausted`` is the A1.7.5 hold bound. A1.7.4.4's veto had no
    time limit, which let 160 vetoes accumulate on 11 books and drove
    ``inventory_age_p90`` to 1,329 ticks. Once the caller's per-book budget is
    spent the base decision is restored and the bounded loss is taken.
    """
    reason = str(getattr(base_decision, "reason", "") or "")
    action = str(getattr(base_decision, "action", "") or "")
    band = str(getattr(base_decision, "risk_band", "") or "")
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
    if bool(tail_budget_exhausted):
        # A1.7.5: the hold is bounded. Take the base decision's bounded loss
        # rather than holding a non-completing book indefinitely.
        return base_decision

    absolute_arm = maker + 1e-12 >= floor
    relative_arm = a175_relative_arm_allows(
        maker_net_bps=maker,
        taker_net_bps=taker,
        risk_band=band,
        advantage_bps=advantage_bps,
    )
    if not (absolute_arm or relative_arm):
        return base_decision

    return replace(
        base_decision,
        action=ACTION_MAKER_EXIT,
        selected_qty=max(0.0, abs(_finite(inventory_qty))),
        reason="A1744_POSITIVE_MAKER_RISK_VETO",
        corridor_action=(
            "DIRECT_POSITIVE_MAKER_KAPPA_A1744"
            if absolute_arm
            else "DIRECT_POSITIVE_MAKER_KAPPA_A175_RELATIVE"
        ),
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
    advantage_bps: float = DIRECT_A175_MAKER_ADVANTAGE_BPS,
    tail_budget_exhausted: bool = False,
) -> str | None:
    """Return a stable telemetry label for decisions in the veto scope."""
    base_reason = str(getattr(base_decision, "reason", "") or "")
    base_action = str(getattr(base_decision, "action", "") or "")
    if base_action != ACTION_TAKER_EXIT or base_reason not in DIRECT_A1744_RISK_TAKER_REASONS:
        return None
    if str(getattr(final_decision, "reason", "") or "") == "A1744_POSITIVE_MAKER_RISK_VETO":
        corridor = str(getattr(final_decision, "corridor_action", "") or "")
        if corridor == "DIRECT_POSITIVE_MAKER_KAPPA_A175_RELATIVE":
            return "A175_RELATIVE_MAKER_RISK_VETO"
        return "A1744_POSITIVE_MAKER_RISK_VETO"
    if bool(catastrophic_hard_risk):
        return "A1744_TAKER_ALLOWED_CATASTROPHIC"
    if bool(tail_budget_exhausted):
        return "A175_TAIL_BUDGET_EXHAUSTED"
    maker = _finite(maker_net_bps)
    taker = _finite(taker_net_bps)
    floor = max(0.0, _finite(strong_maker_floor_bps, DIRECT_A1744_STRONG_MAKER_FLOOR_BPS))
    relative_ok = a175_relative_arm_allows(
        maker_net_bps=maker,
        taker_net_bps=taker,
        risk_band=str(getattr(base_decision, "risk_band", "") or ""),
        advantage_bps=advantage_bps,
    )
    if (not bool(maker_executable)) or (maker + 1e-12 < floor and not relative_ok):
        return "A1744_TAKER_ALLOWED_MAKER_NOT_STRONG"
    if taker >= -1e-12:
        return "A1744_TAKER_ALLOWED_NONNEGATIVE"
    return None
