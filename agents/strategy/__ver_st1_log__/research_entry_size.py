# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 1.5: dynamic per-book maximum entry size.

Pure functions so unit tests do not import Strategy1 / bittensor.

AllowedEntrySize =
  BaseSize
  × InventoryFactor
  × LiquidityFactor
  × ExitCapacityFactor
  × VolumeHeadroomFactor
  × RiskFactor

Hard inventory / clip caps always win. A large maker entry is rejected
when expected exit capacity cannot absorb it.

Minimum-order admission is discrete: SAFE allows, NEAR_SAFE may promote
one exchange-minimum maker clip when EV / inventory / exit / headroom
gates pass, and UNSAFE rejects. Sub-minimum size is never blindly
rounded up.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_realization import inventory_holding_risk


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


ADMISSION_SAFE = "SAFE"
ADMISSION_NEAR_SAFE = "NEAR_SAFE"
ADMISSION_UNSAFE = "UNSAFE"

DEFAULT_NEAR_SAFE_TOLERANCE = 0.20
DEFAULT_NEAR_SAFE_MAX_INVENTORY_RISK = 0.35
DEFAULT_NEAR_SAFE_MIN_HEADROOM = 0.25
DEFAULT_NEAR_SAFE_MIN_EV = 0.0


def clamp_min_order_tolerance(
    value: Any,
    *,
    default: float = DEFAULT_NEAR_SAFE_TOLERANCE,
) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = float(default)
    if not math.isfinite(number):
        number = float(default)
    return max(0.0, min(0.95, number))


def classify_min_order_band(
    safe_size: float,
    min_order: float,
    tolerance: float = DEFAULT_NEAR_SAFE_TOLERANCE,
) -> str:
    """SAFE / NEAR_SAFE / UNSAFE from calculated size vs exchange minimum."""
    safe = max(0.0, _finite(safe_size))
    floor = max(0.0, _finite(min_order))
    if floor <= 1e-12:
        return ADMISSION_SAFE if safe > 1e-12 else ADMISSION_UNSAFE
    if safe + 1e-12 >= floor:
        return ADMISSION_SAFE
    band = floor * (1.0 - clamp_min_order_tolerance(tolerance))
    if safe + 1e-12 >= band:
        return ADMISSION_NEAR_SAFE
    return ADMISSION_UNSAFE


@dataclass(frozen=True)
class MinOrderAdmission:
    band: str
    allow: bool
    size: float
    safe_size: float
    min_order: float
    tolerance: float
    trigger: str
    promoted: bool

    def as_log(self, *, book: int | None = None) -> dict[str, Any]:
        payload = {
            "admission": self.band,
            "admission_allow": int(bool(self.allow)),
            "admission_size": self.size,
            "safe_size": self.safe_size,
            "min_order": self.min_order,
            "tolerance": self.tolerance,
            "admission_trigger": self.trigger,
            "promoted": int(bool(self.promoted)),
        }
        if book is not None:
            payload["book"] = int(book)
        return payload


def admit_minimum_order(
    *,
    safe_size: float,
    min_order: float,
    tolerance: float = DEFAULT_NEAR_SAFE_TOLERANCE,
    trading_ev: float = 0.0,
    inventory_risk: float = 0.0,
    exit_capacity: float = 0.0,
    volume_headroom: float = 1.0,
    remaining_inventory: float | None = None,
    enable_near_safe: bool = True,
    min_trading_ev: float = DEFAULT_NEAR_SAFE_MIN_EV,
    max_inventory_risk: float = DEFAULT_NEAR_SAFE_MAX_INVENTORY_RISK,
    min_headroom: float = DEFAULT_NEAR_SAFE_MIN_HEADROOM,
    enable_positive_ev_override: bool = False,
    positive_ev_min_safe_fraction: float = 0.35,
    positive_ev_min_exit_fraction: float = 0.45,
    positive_ev_min_trading_ev: float = 0.05,
    observations_remaining: int | None = None,
    enable_one_away_exact_min: bool = False,
    one_away_min_trading_ev: float = 0.0,
    one_away_min_safe_fraction: float = 0.50,
    one_away_min_exit_fraction: float = 0.90,
    enable_two_away_exact_min: bool = False,
    two_away_min_trading_ev: float = 0.0,
    two_away_max_inventory_risk: float = 0.35,
    two_away_min_exit_fraction: float = 0.20,
    two_away_min_headroom: float = 0.25,
) -> MinOrderAdmission:
    """Discrete minimum-order admission. Never blindly promote every clip."""
    safe = max(0.0, _finite(safe_size))
    floor = max(0.0, _finite(min_order))
    tol = clamp_min_order_tolerance(tolerance)
    band = classify_min_order_band(safe, floor, tol)
    room = None if remaining_inventory is None else max(0.0, _finite(remaining_inventory))

    def _reject(trigger: str) -> MinOrderAdmission:
        return MinOrderAdmission(
            band=band,
            allow=False,
            size=0.0,
            safe_size=safe,
            min_order=floor,
            tolerance=tol,
            trigger=trigger,
            promoted=False,
        )

    if room is not None and floor > 1e-12 and room + 1e-12 < floor:
        return _reject("INVENTORY_ROOM")

    if band == ADMISSION_UNSAFE:
        # V4.11.2 ONE_AWAY exact-minimum completion override.  ONE_AWAY is an
        # authoritative rolling-Kappa state (2/3 observations).  Soft sizing
        # factors may not suppress the third observation when an exact exchange
        # minimum clip is hard-safe, lifecycle EV is positive, volume headroom
        # is healthy, and modeled exit capacity is already near the minimum.
        one_away = observations_remaining is not None and max(0, int(observations_remaining)) == 1
        one_away_safe_fraction = max(0.0, min(1.0, _finite(one_away_min_safe_fraction, 0.50)))
        one_away_exit_fraction = max(0.0, min(1.0, _finite(one_away_min_exit_fraction, 0.90)))
        one_away_ok = (
            bool(enable_one_away_exact_min)
            and one_away
            and floor > 1e-12
            and safe + 1e-12 >= floor * one_away_safe_fraction
            and _finite(trading_ev) > _finite(one_away_min_trading_ev, 0.0) + 1e-12
            and _finite(inventory_risk) <= _finite(max_inventory_risk) + 1e-12
            and _finite(volume_headroom) + 1e-12 >= _finite(min_headroom)
            and _finite(exit_capacity) + 1e-12 >= floor * one_away_exit_fraction
        )
        if one_away_ok:
            size = floor if room is None else min(floor, room)
            if size + 1e-12 < floor:
                return _reject("INVENTORY_ROOM")
            return MinOrderAdmission(
                band=ADMISSION_NEAR_SAFE,
                allow=True,
                size=size,
                safe_size=safe,
                min_order=floor,
                tolerance=tol,
                trigger="ONE_AWAY_EXACT_MIN",
                promoted=True,
            )

        # V4.12.4 TWO_AWAY exact-minimum completion path.  This deliberately
        # does NOT require safe_size to be close to the exchange minimum.  The
        # live failure mode was exactly that a continuous risk model returned
        # ~0.05 while the venue only accepts 0.25, permanently blocking books
        # at 1/3 observations.  Admission is therefore binary at the venue
        # quantum, with hard full-clip inventory risk, positive trading EV,
        # volume headroom, and non-trivial exit-capacity gates.  ONE_AWAY keeps
        # its stricter 50% safe / 90% exit-capacity path above.
        two_away = observations_remaining is not None and max(0, int(observations_remaining)) == 2
        two_away_exit_fraction = max(0.0, min(1.0, _finite(two_away_min_exit_fraction, 0.20)))
        two_away_ok = (
            bool(enable_two_away_exact_min)
            and two_away
            and floor > 1e-12
            and safe > 1e-12
            and _finite(trading_ev) > _finite(two_away_min_trading_ev, 0.0) + 1e-12
            and _finite(inventory_risk) <= _finite(two_away_max_inventory_risk, 0.35) + 1e-12
            and _finite(volume_headroom) + 1e-12 >= _finite(two_away_min_headroom, 0.25)
            and _finite(exit_capacity) + 1e-12 >= floor * two_away_exit_fraction
        )
        if two_away_ok:
            size = floor if room is None else min(floor, room)
            if size + 1e-12 < floor:
                return _reject("INVENTORY_ROOM")
            return MinOrderAdmission(
                band=ADMISSION_NEAR_SAFE,
                allow=True,
                size=size,
                safe_size=safe,
                min_order=floor,
                tolerance=tol,
                trigger="TWO_AWAY_EXACT_MIN",
                promoted=True,
            )

        # V4.11 performance override: the multiplicative risk model can shrink
        # a fundamentally executable 0.25 clip to ~0.09-0.15.  Permit exactly
        # one exchange-minimum clip only when lifecycle EV is strongly positive,
        # inventory risk/headroom remain healthy, and modeled exit capacity is
        # still a meaningful fraction of the minimum. Hard inventory room wins.
        safe_frac = 0.0 if floor <= 1e-12 else safe / floor
        exit_frac = 0.0 if floor <= 1e-12 else max(0.0, _finite(exit_capacity)) / floor
        override_ok = (
            bool(enable_positive_ev_override)
            and floor > 1e-12
            and safe_frac + 1e-12 >= max(0.0, _finite(positive_ev_min_safe_fraction, 0.35))
            and exit_frac + 1e-12 >= max(0.0, _finite(positive_ev_min_exit_fraction, 0.45))
            and _finite(trading_ev) >= _finite(positive_ev_min_trading_ev, 0.05)
            and _finite(inventory_risk) <= _finite(max_inventory_risk) + 1e-12
            and _finite(volume_headroom) + 1e-12 >= _finite(min_headroom)
        )
        if not override_ok:
            return _reject("UNSAFE")
        size = floor if room is None else min(floor, room)
        if size + 1e-12 < floor:
            return _reject("INVENTORY_ROOM")
        return MinOrderAdmission(
            band=ADMISSION_NEAR_SAFE,
            allow=True,
            size=size,
            safe_size=safe,
            min_order=floor,
            tolerance=tol,
            trigger="POSITIVE_EV_OVERRIDE",
            promoted=True,
        )

    if band == ADMISSION_SAFE:
        size = safe
        if room is not None:
            size = min(size, room)
        allow = size > 1e-12 and (floor <= 1e-12 or size + 1e-12 >= floor)
        if not allow:
            return _reject("INVENTORY_ROOM")
        return MinOrderAdmission(
            band=ADMISSION_SAFE,
            allow=True,
            size=size,
            safe_size=safe,
            min_order=floor,
            tolerance=tol,
            trigger="SAFE",
            promoted=False,
        )

    if not enable_near_safe:
        return _reject("NEAR_SAFE_DISABLED")
    if _finite(trading_ev) <= _finite(min_trading_ev):
        return _reject("NEAR_SAFE_EV")
    if _finite(inventory_risk) > _finite(max_inventory_risk) + 1e-12:
        return _reject("NEAR_SAFE_INVENTORY_RISK")
    # Exit capacity is an empirical/forecast quantity, not an exchange hard limit.
    # Requiring it to be >= the exact minimum order creates a deadlock when the
    # risk-adjusted safe size is deliberately just below the discrete exchange
    # quantum (e.g. 0.22 vs a 0.25 minimum).  Apply the same near-safe tolerance
    # used to classify safe_size, while the *actual* min-order inventory risk is
    # checked separately above by the caller.
    required_exit_capacity = floor * (1.0 - tol)
    if _finite(exit_capacity) + 1e-12 < required_exit_capacity:
        return _reject("NEAR_SAFE_EXIT")
    if _finite(volume_headroom) + 1e-12 < _finite(min_headroom):
        return _reject("NEAR_SAFE_HEADROOM")
    size = floor
    if room is not None:
        size = min(size, room)
    if size + 1e-12 < floor:
        return _reject("INVENTORY_ROOM")
    return MinOrderAdmission(
        band=ADMISSION_NEAR_SAFE,
        allow=True,
        size=size,
        safe_size=safe,
        min_order=floor,
        tolerance=tol,
        trigger="NEAR_SAFE",
        promoted=True,
    )
