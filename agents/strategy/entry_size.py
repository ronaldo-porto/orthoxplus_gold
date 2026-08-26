# SPDX-License-Identifier: MIT
"""Production dynamic per-book maximum entry size.

Standalone copy inlined into BaseStrategy. No Strategy1 / Research runtime
imports. Coefficients are frozen. Missing signals fall back conservatively
(OFI/markout 0, volume headroom 1) and hard inventory / clip caps always win.

AllowedEntrySize =
  BaseSize
  × InventoryFactor
  × LiquidityFactor
  × ExitCapacityFactor
  × VolumeHeadroomFactor
  × RiskFactor
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from realization import inventory_holding_risk


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _clip(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(float(lo), min(float(hi), _finite(value)))


def inventory_factor(
    existing_inventory: float,
    max_inventory: float,
    inventory_age: float,
    *,
    age_ref: float = 20.0,
) -> float:
    """Remaining room after current inventory, reduced further as the position ages."""
    cap = max(_finite(max_inventory), 1e-9)
    room = max(0.0, 1.0 - abs(_finite(existing_inventory)) / cap)
    age_scale = 1.0 - 0.40 * math.tanh(max(0.0, _finite(inventory_age)) / max(1e-9, age_ref))
    return _clip(room * age_scale)


def liquidity_factor(volatility: float, *, vol_floor: float = 0.0015, vol_ref: float = 0.004) -> float:
    """Quiet books stay near 1. Elevated vol shrinks entry size."""
    excess = max(0.0, _finite(volatility) - max(0.0, float(vol_floor)))
    return _clip(1.0 - 0.60 * math.tanh(excess / max(1e-9, float(vol_ref))), 0.15, 1.0)


def volume_headroom_factor(volume_cap_headroom: float) -> float:
    return _clip(0.15 + 0.85 * _clip(volume_cap_headroom))


def risk_factor(
    *,
    toxicity: float,
    expected_markout: float,
    recent_drawdown: float,
    ofi_against: float = 0.0,
    markout_ref: float = 8.0,
    drawdown_ref: float = 0.05,
) -> float:
    tox = _clip(toxicity)
    markout_pen = math.tanh(max(0.0, -_finite(expected_markout)) / max(1e-9, float(markout_ref)))
    drawdown_pen = math.tanh(max(0.0, -_finite(recent_drawdown)) / max(1e-9, float(drawdown_ref)))
    ofi_pen = math.tanh(max(0.0, _finite(ofi_against)))
    return _clip(
        1.0 - 0.40 * tox - 0.35 * markout_pen - 0.30 * drawdown_pen - 0.20 * ofi_pen,
        0.10,
        1.0,
    )


def expected_exit_capacity(
    *,
    base_size: float,
    exit_rate: float | None,
    volatility: float,
    toxicity: float,
    exit_rate_ref: float = 0.05,
) -> float:
    """Near-horizon size we can realize without a burst liquidation."""
    base = max(0.0, _finite(base_size))
    if exit_rate is None:
        rate_norm = 1.0
    else:
        rate_norm = _clip(_finite(exit_rate) / max(1e-9, float(exit_rate_ref)))
    slow = 0.25 + 0.75 * rate_norm
    liq = liquidity_factor(volatility)
    tox_pen = 1.0 - 0.50 * _clip(toxicity)
    return max(0.0, base * slow * (0.35 + 0.65 * liq) * tox_pen)


def exit_capacity_factor(capacity: float, base_size: float) -> float:
    base = max(_finite(base_size), 1e-9)
    return _clip(_finite(capacity) / base, 0.10, 1.0)


@dataclass(frozen=True)
class EntrySizeDecision:
    entry_size: float
    expected_exit_capacity: float
    inventory_after_full_fill: float
    inventory_risk_after_fill: float
    inventory_factor: float
    liquidity_factor: float
    exit_capacity_factor: float
    volume_headroom_factor: float
    risk_factor: float
    uncapped_size: float
    hard_cap: float
    trigger: str

    def as_log(self, *, book: int | None = None) -> dict[str, Any]:
        payload = {
            "entry_size": self.entry_size,
            "expected_exit_capacity": self.expected_exit_capacity,
            "inventory_after_full_fill": self.inventory_after_full_fill,
            "inventory_risk_after_fill": self.inventory_risk_after_fill,
            "inventory_factor": self.inventory_factor,
            "liquidity_factor": self.liquidity_factor,
            "exit_capacity_factor": self.exit_capacity_factor,
            "volume_headroom_factor": self.volume_headroom_factor,
            "risk_factor": self.risk_factor,
            "uncapped_size": self.uncapped_size,
            "hard_cap": self.hard_cap,
            "trigger": self.trigger,
        }
        if book is not None:
            payload["book"] = int(book)
        return payload


def allowed_entry_size(
    *,
    base_size: float,
    existing_inventory: float = 0.0,
    max_inventory: float = 1.2,
    inventory_age: float = 0.0,
    volatility: float = 0.0,
    toxicity: float = 0.0,
    expected_markout: float = 0.0,
    ofi_against: float = 0.0,
    volume_cap_headroom: float = 1.0,
    exit_rate: float | None = None,
    recent_drawdown: float = 0.0,
    hard_max_entry: float | None = None,
    exit_rate_ref: float = 0.05,
) -> EntrySizeDecision:
    base = max(0.0, _finite(base_size))
    existing = abs(_finite(existing_inventory))
    inv_cap = max(_finite(max_inventory), 1e-9)
    remaining = max(0.0, inv_cap - existing)
    clip_max = base if hard_max_entry is None else min(base, max(0.0, _finite(hard_max_entry)))
    hard = min(remaining, clip_max)

    inv_f = inventory_factor(existing, inv_cap, inventory_age)
    liq_f = liquidity_factor(volatility)
    capacity = expected_exit_capacity(
        base_size=base,
        exit_rate=exit_rate,
        volatility=volatility,
        toxicity=toxicity,
        exit_rate_ref=exit_rate_ref,
    )
    exit_f = exit_capacity_factor(capacity, base)
    head_f = volume_headroom_factor(volume_cap_headroom)
    risk_f = risk_factor(
        toxicity=toxicity,
        expected_markout=expected_markout,
        recent_drawdown=recent_drawdown,
        ofi_against=ofi_against,
    )
    uncapped = base * inv_f * liq_f * exit_f * head_f * risk_f
    entry = min(uncapped, hard)
    trigger = "FACTORS"
    if capacity + 1e-12 < entry:
        entry = max(0.0, min(entry, capacity))
        trigger = "EXIT_CAPACITY"
    if entry + 1e-12 >= hard and hard + 1e-12 < uncapped:
        trigger = "HARD_CAP"
    if remaining <= 1e-12:
        entry = 0.0
        trigger = "HARD_CAP"

    after = existing + entry
    risk_after = inventory_holding_risk(
        inventory_ratio=after / inv_cap,
        volatility=volatility,
        inventory_age=inventory_age,
    )
    return EntrySizeDecision(
        entry_size=max(0.0, entry),
        expected_exit_capacity=capacity,
        inventory_after_full_fill=after,
        inventory_risk_after_fill=risk_after,
        inventory_factor=inv_f,
        liquidity_factor=liq_f,
        exit_capacity_factor=exit_f,
        volume_headroom_factor=head_f,
        risk_factor=risk_f,
        uncapped_size=uncapped,
        hard_cap=hard,
        trigger=trigger,
    )
