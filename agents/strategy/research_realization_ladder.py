# SPDX-License-Identifier: MIT
"""Research hybrid realization ladder.

Urgency selects a rung:

    PASSIVE_MAKER_EXIT
            ↓
    COMPETITIVE_MAKER_EXIT
            ↓
    AGGRESSIVE_MAKER_EXIT
            ↓
    SELECTIVE_TAKER_EXIT

The top rung is *eligible*, not automatic. Hybrid economics still decide
whether we cross. Hard safety may skip the maker rungs.

Initial Research bands are configurable and are not production constants.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_hybrid import (
    REASON_MAKER,
    REASON_REJECT_COST,
    REASON_REJECT_LADDER,
    REASON_REJECT_TRANSITION,
)

ACTION_PASSIVE = "PASSIVE_MAKER_EXIT"
ACTION_COMPETITIVE = "COMPETITIVE_MAKER_EXIT"
ACTION_AGGRESSIVE = "AGGRESSIVE_MAKER_EXIT"
ACTION_TAKER = "SELECTIVE_TAKER_EXIT"

LADDER_RUNGS = (
    ACTION_PASSIVE,
    ACTION_COMPETITIVE,
    ACTION_AGGRESSIVE,
    ACTION_TAKER,
)

BAND_PASSIVE = "PASSIVE"
BAND_COMPETITIVE = "COMPETITIVE"
BAND_AGGRESSIVE = "AGGRESSIVE"
BAND_TAKER_ELIGIBLE = "TAKER_ELIGIBLE"

LADDER_VERSION = "realization_ladder_v2"

# Research defaults only. Tune via config; do not freeze as production constants.
DEFAULT_LADDER_PASSIVE_MAX = 0.25
DEFAULT_LADDER_COMPETITIVE_MAX = 0.50
DEFAULT_LADDER_AGGRESSIVE_MAX = 0.70


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _clip01(value: Any, default: float = 0.0) -> float:
    return max(0.0, min(1.0, _finite(value, default)))


@dataclass(frozen=True)
class RealizationLadderBands:
    passive_max: float = DEFAULT_LADDER_PASSIVE_MAX
    competitive_max: float = DEFAULT_LADDER_COMPETITIVE_MAX
    aggressive_max: float = DEFAULT_LADDER_AGGRESSIVE_MAX

    def as_log(self) -> dict[str, Any]:
        return {
            "ladder_version": LADDER_VERSION,
            "ladder_passive_max": self.passive_max,
            "ladder_competitive_max": self.competitive_max,
            "ladder_aggressive_max": self.aggressive_max,
        }


def clamp_ladder_bands(
    passive_max: Any = None,
    competitive_max: Any = None,
    aggressive_max: Any = None,
) -> RealizationLadderBands:
    """Order and clip Research ladder bands. Gaps may be retuned later."""
    raw = (
        _clip01(passive_max, DEFAULT_LADDER_PASSIVE_MAX)
        if passive_max is not None
        else DEFAULT_LADDER_PASSIVE_MAX
    )
    mid = (
        _clip01(competitive_max, DEFAULT_LADDER_COMPETITIVE_MAX)
        if competitive_max is not None
        else DEFAULT_LADDER_COMPETITIVE_MAX
    )
    top = (
        _clip01(aggressive_max, DEFAULT_LADDER_AGGRESSIVE_MAX)
        if aggressive_max is not None
        else DEFAULT_LADDER_AGGRESSIVE_MAX
    )
    ordered = sorted((raw, mid, top))
    # Keep a taker-eligible band open at the top.
    if ordered[2] >= 1.0 - 1e-12:
        ordered[2] = 0.99
    if ordered[1] + 1e-12 >= ordered[2]:
        ordered[1] = max(0.0, ordered[2] - 0.01)
    if ordered[0] + 1e-12 >= ordered[1]:
        ordered[0] = max(0.0, ordered[1] - 0.01)
    return RealizationLadderBands(
        passive_max=ordered[0],
        competitive_max=ordered[1],
        aggressive_max=ordered[2],
    )


@dataclass(frozen=True)
class RealizationRung:
    urgency: float
    band: str
    proposed_action: str
    maker_action: str
    taker_eligible: bool
    bands: RealizationLadderBands

    def as_log(self) -> dict[str, Any]:
        payload = {
            "exit_urgency": self.urgency,
            "proposed_rung": self.proposed_action,
            "ladder_band": self.band,
            "taker_eligible": int(bool(self.taker_eligible)),
            "maker_rung": self.maker_action,
        }
        payload.update(self.bands.as_log())
        return payload


def classify_realization_rung(
    urgency: float,
    bands: RealizationLadderBands | None = None,
) -> RealizationRung:
    """Map ExitUrgency onto the current Research ladder bands."""
    limits = bands if bands is not None else clamp_ladder_bands()
    score = _clip01(urgency)
    if score > limits.aggressive_max + 1e-12:
        return RealizationRung(
            urgency=score,
            band=BAND_TAKER_ELIGIBLE,
            proposed_action=ACTION_TAKER,
            maker_action=ACTION_AGGRESSIVE,
            taker_eligible=True,
            bands=limits,
        )
    if score + 1e-12 >= limits.competitive_max:
        return RealizationRung(
            urgency=score,
            band=BAND_AGGRESSIVE,
            proposed_action=ACTION_AGGRESSIVE,
            maker_action=ACTION_AGGRESSIVE,
            taker_eligible=False,
            bands=limits,
        )
    if score + 1e-12 >= limits.passive_max:
        return RealizationRung(
            urgency=score,
            band=BAND_COMPETITIVE,
            proposed_action=ACTION_COMPETITIVE,
            maker_action=ACTION_COMPETITIVE,
            taker_eligible=False,
            bands=limits,
        )
    return RealizationRung(
        urgency=score,
        band=BAND_PASSIVE,
        proposed_action=ACTION_PASSIVE,
        maker_action=ACTION_PASSIVE,
        taker_eligible=False,
        bands=limits,
    )


def apply_realization_ladder(
    *,
    rung: RealizationRung,
    hybrid_take: bool,
    hybrid_reason: str,
    hard_safety: bool,
    direct_taker_authorized: bool = False,
    transition_quarantine: bool,
    cost: float,
    risk: float,
    state: str,
) -> tuple[str, str]:
    """Select the live action.

    V4.8 decouples taker *authorization* from maker-rung urgency. Bounded
    SCORE, ECONOMIC, or RISK authority may cross immediately even when the
    urgency ladder still prefers a maker rung. The ladder chooses maker
    aggressiveness only when no direct taker authority exists.
    """
    if bool(transition_quarantine) or hybrid_reason == REASON_REJECT_TRANSITION:
        return ACTION_PASSIVE, REASON_REJECT_TRANSITION
    if hybrid_take and (bool(direct_taker_authorized) or rung.taker_eligible or bool(hard_safety)):
        return ACTION_TAKER, hybrid_reason
    if hybrid_take:
        return rung.maker_action, REASON_REJECT_LADDER
    if rung.taker_eligible:
        return ACTION_AGGRESSIVE, REASON_REJECT_COST
    if (
        rung.proposed_action == ACTION_AGGRESSIVE
        and _finite(cost) > _finite(risk) + 1e-12
    ):
        return ACTION_AGGRESSIVE, REASON_REJECT_COST
    if str(state or "").upper() == "EMERGENCY":
        return ACTION_AGGRESSIVE, "EMERGENCY_MAKER"
    return rung.maker_action, REASON_MAKER
