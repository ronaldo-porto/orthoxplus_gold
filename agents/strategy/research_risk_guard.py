# SPDX-License-Identifier: MIT
"""V4.16 hard-safety RiskGuard.

The only strict blocking layer. Economic preference, Kappa urgency, fill
attractiveness, and Taker probability do not belong here.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

RISK_GUARD_VERSION = "risk_guard_v4_16_0"

REASON_SAFE = "SAFE"
REASON_MAX_INVENTORY = "MAX_INVENTORY"
REASON_INVALID_PRICE = "INVALID_QUOTE_PRICES"
REASON_ZERO_SIZE = "ZERO_ORDER_SIZE"
REASON_VOLUME_CAP = "VOLUME_CAP"
REASON_BALANCE = "INSUFFICIENT_BALANCE"
REASON_INSTRUCTION_LIMIT = "INSTRUCTION_LIMIT"
REASON_EXPOSURE_CAP = "TOTAL_EXPOSURE_CAP"
REASON_OPEN_BOOK_CAP = "ACTIVE_OPEN_BOOK_CAP"
REASON_CONTRACT = "CONTRACT_REJECT"
REASON_TOXIC = "TOXIC"
REASON_UNSAFE = "UNSAFE"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


@dataclass(frozen=True)
class RiskGuardDecision:
    safe: bool
    reason: str | None

    def as_log(self) -> dict[str, Any]:
        return {
            "risk_guard_version": RISK_GUARD_VERSION,
            "safe": bool(self.safe),
            "hard_reject_reason": self.reason,
        }


def evaluate_risk_guard(
    *,
    inventory_blocked: bool = False,
    invalid_price: bool = False,
    invalid_size: bool = False,
    volume_capped: bool = False,
    insufficient_balance: bool = False,
    instruction_limited: bool = False,
    exposure_capped: bool = False,
    open_book_capped: bool = False,
    contract_blocked: bool = False,
    toxic: bool = False,
    unsafe: bool = False,
) -> RiskGuardDecision:
    if toxic:
        return RiskGuardDecision(False, REASON_TOXIC)
    if unsafe:
        return RiskGuardDecision(False, REASON_UNSAFE)
    if inventory_blocked:
        return RiskGuardDecision(False, REASON_MAX_INVENTORY)
    if invalid_price:
        return RiskGuardDecision(False, REASON_INVALID_PRICE)
    if invalid_size:
        return RiskGuardDecision(False, REASON_ZERO_SIZE)
    if volume_capped:
        return RiskGuardDecision(False, REASON_VOLUME_CAP)
    if exposure_capped:
        return RiskGuardDecision(False, REASON_EXPOSURE_CAP)
    if open_book_capped:
        return RiskGuardDecision(False, REASON_OPEN_BOOK_CAP)
    if contract_blocked:
        return RiskGuardDecision(False, REASON_CONTRACT)
    if instruction_limited:
        return RiskGuardDecision(False, REASON_INSTRUCTION_LIMIT)
    if insufficient_balance:
        return RiskGuardDecision(False, REASON_BALANCE)
    return RiskGuardDecision(True, None)


def clip_size_to_caps(
    size: float,
    *,
    min_order: float = 0.25,
    inventory_headroom: float = 1e9,
    exposure_headroom: float = 1e9,
    volume_headroom: float = 1e9,
    balance_headroom: float = 1e9,
) -> float:
    """Hard caps always win. Sub-minimum after clipping is rejected as 0."""
    qty = max(0.0, _finite(size))
    qty = min(
        qty,
        max(0.0, _finite(inventory_headroom, 0.0)),
        max(0.0, _finite(exposure_headroom, 0.0)),
        max(0.0, _finite(volume_headroom, 0.0)),
        max(0.0, _finite(balance_headroom, 0.0)),
    )
    floor = max(0.0, _finite(min_order, 0.25))
    if qty + 1e-12 < floor:
        return 0.0
    return qty
