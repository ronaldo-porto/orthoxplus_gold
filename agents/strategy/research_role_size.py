# SPDX-License-Identifier: MIT
"""V4.16 role-specific sizing. Few tiers, hard caps always win."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_risk_guard import clip_size_to_caps

ROLE_SIZE_VERSION = "role_size_v4_16_0"

MIN_MAKER = 0.25
NORMAL_MAKER = 0.50
STRONG_MAKER = 1.00
COMPLETION_SIZE = 0.25
TAKER_CLIP = 0.25


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


@dataclass(frozen=True)
class RoleSizeDecision:
    size: float
    role: str
    tier: str

    def as_log(self) -> dict[str, Any]:
        return {
            "role_size_version": ROLE_SIZE_VERSION,
            "size": self.size,
            "role": self.role,
            "tier": self.tier,
        }


def maker_entry_size(
    *,
    lifecycle_ev: float,
    p_fill: float = 0.50,
    observations_remaining: int = 3,
    min_order: float = MIN_MAKER,
    inventory_headroom: float = 1e9,
    exposure_headroom: float = 1e9,
    volume_headroom: float = 1e9,
    balance_headroom: float = 1e9,
) -> RoleSizeDecision:
    remaining = max(0, int(observations_remaining))
    if remaining in {1, 2}:
        raw, tier = COMPLETION_SIZE, "COMPLETION_MIN"
    elif _finite(lifecycle_ev) >= 0.20 and _finite(p_fill) >= 0.45:
        raw, tier = STRONG_MAKER, "STRONG"
    elif _finite(lifecycle_ev) >= 0.05:
        raw, tier = NORMAL_MAKER, "NORMAL"
    else:
        raw, tier = MIN_MAKER, "MINIMUM"
    size = clip_size_to_caps(
        raw,
        min_order=min_order,
        inventory_headroom=inventory_headroom,
        exposure_headroom=exposure_headroom,
        volume_headroom=volume_headroom,
        balance_headroom=balance_headroom,
    )
    return RoleSizeDecision(size, "MAKER", tier)


def taker_clip_size(
    *,
    inventory_qty: float,
    min_order: float = TAKER_CLIP,
    hard_flatten: bool = False,
) -> RoleSizeDecision:
    qty = abs(_finite(inventory_qty))
    clip = qty if hard_flatten else min(qty, TAKER_CLIP)
    if 0.0 < clip + 1e-12 < max(0.0, _finite(min_order, TAKER_CLIP)):
        clip = min(qty, max(0.0, _finite(min_order, TAKER_CLIP)))
    if clip + 1e-12 < max(0.0, _finite(min_order, TAKER_CLIP)) and not hard_flatten:
        clip = 0.0
    return RoleSizeDecision(clip, "TAKER", "FLATTEN" if hard_flatten else "CLIP")
