# SPDX-License-Identifier: MIT
"""Research inventory state machine V2.

Per-book states: NORMAL, CAUTION, DEFENSIVE, EXIT_ONLY, EMERGENCY.

Classification uses size/ratio, age, drawdown, volatility, OFI/adverse
flow, markout, Kappa need, per-book volume headroom, and recent
realization success/failure.

Entry/exit policy is discrete. Taker remains economically gated unless
true hard safety requires immediate reduction.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

STATE_NORMAL = "NORMAL"
STATE_CAUTION = "CAUTION"
STATE_DEFENSIVE = "DEFENSIVE"
STATE_EXIT_ONLY = "EXIT_ONLY"
STATE_EMERGENCY = "EMERGENCY"

INVENTORY_STATES = (
    STATE_NORMAL,
    STATE_CAUTION,
    STATE_DEFENSIVE,
    STATE_EXIT_ONLY,
    STATE_EMERGENCY,
)

PRESSURE_CAUTION = 0.22
PRESSURE_DEFENSIVE = 0.48
PRESSURE_EXIT_ONLY = 0.72
PRESSURE_EMERGENCY = 0.90

RATIO_DEFENSIVE = 0.45
RATIO_EXIT_ONLY = 0.70
RATIO_EMERGENCY = 0.95

CAUTION_ENTRY_MULT = 0.50
DEFENSIVE_ENTRY_MULT = 0.0
CAUTION_EXIT_MULT = 1.15
DEFENSIVE_EXIT_MULT = 1.25


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value)))


def _tanh01(value: Any, scale: float) -> float:
    return math.tanh(max(0.0, _finite(value)) / max(1e-9, float(scale)))


def ofi_against_inventory(ofi: float | None, inventory_sign: float) -> float:
    if ofi is None:
        return 0.0
    flow = _finite(ofi)
    sign = _finite(inventory_sign)
    if sign > 0.0:
        return max(0.0, -flow)
    if sign < 0.0:
        return max(0.0, flow)
    return abs(flow)


def recent_realization_failure(
    recent_realized_pnl: float | None,
    realization_failed: bool | None = None,
) -> float:
    if realization_failed is True:
        fail = 1.0
    elif realization_failed is False:
        fail = 0.0
    else:
        fail = 1.0 if _finite(recent_realized_pnl) < 0.0 else 0.0
    loss = _tanh01(max(0.0, -_finite(recent_realized_pnl)), 0.05)
    return _clip01(0.65 * loss + 0.35 * fail)


def inventory_pressure_v2(
    *,
    inventory_size: float,
    inventory_ratio: float,
    inventory_age: float,
    unrealized_pnl: float | None,
    volatility: float,
    ofi: float | None,
    expected_markout: float,
    kappa_need: float,
    volume_cap_headroom: float,
    recent_realized_pnl: float | None,
    adverse_selection_risk: float = 0.0,
    realization_failed: bool | None = None,
    inventory_sign: float = 0.0,
    size_ref: float = 0.50,
    age_ref: float = 20.0,
) -> float:
    """Continuous [0, 1] inventory-state pressure from the V2 feature set."""
    size_term = _tanh01(abs(_finite(inventory_size)), size_ref)
    ratio_term = _clip01(abs(_finite(inventory_ratio)))
    age_term = _tanh01(inventory_age, age_ref)
    drawdown = _tanh01(max(0.0, -_finite(unrealized_pnl)), 12.0)
    vol_term = _tanh01(volatility, 0.006)
    ofi_term = _clip01(ofi_against_inventory(ofi, inventory_sign))
    markout_term = _tanh01(max(0.0, -_finite(expected_markout)), 8.0)
    kappa_term = _clip01(kappa_need)
    headroom_term = 1.0 - _clip01(volume_cap_headroom)
    recent_term = recent_realization_failure(recent_realized_pnl, realization_failed)
    as_term = _clip01(adverse_selection_risk)
    pressure = (
        0.16 * ratio_term
        + 0.10 * size_term
        + 0.12 * age_term
        + 0.16 * drawdown
        + 0.08 * vol_term
        + 0.10 * ofi_term
        + 0.08 * markout_term
        + 0.06 * kappa_term
        + 0.06 * headroom_term
        + 0.08 * recent_term
    )
    pressure = _clip01(pressure + 0.04 * as_term)
    if _finite(recent_realized_pnl) > 0.0 and realization_failed is not True:
        pressure = _clip01(pressure * 0.92)
    return pressure


def classify_inventory_state(
    *,
    urgency: float = 0.0,
    inventory_ratio: float = 0.0,
    band: str | None = None,
    stop_loss_hit: bool = False,
    hard_emergency: bool = False,
    inventory_size: float = 0.0,
    inventory_age: float = 0.0,
    unrealized_pnl: float | None = None,
    volatility: float = 0.0,
    ofi: float | None = None,
    expected_markout: float = 0.0,
    kappa_need: float = 0.0,
    volume_cap_headroom: float = 1.0,
    recent_realized_pnl: float | None = None,
    adverse_selection_risk: float = 0.0,
    realization_failed: bool | None = None,
    inventory_sign: float = 0.0,
) -> str:
    """V2 discrete state. Hard safety and MAX bands always win."""
    token = str(band or "").upper()
    if token in {"FLAT"} and abs(_finite(inventory_ratio)) <= 1e-12:
        return STATE_NORMAL
    if hard_emergency or stop_loss_hit or token in {"MAX_LONG", "MAX_SHORT"}:
        return STATE_EMERGENCY
    ratio = abs(_finite(inventory_ratio))
    pressure = max(
        _clip01(urgency),
        inventory_pressure_v2(
            inventory_size=inventory_size,
            inventory_ratio=ratio,
            inventory_age=inventory_age,
            unrealized_pnl=unrealized_pnl,
            volatility=volatility,
            ofi=ofi,
            expected_markout=expected_markout,
            kappa_need=kappa_need,
            volume_cap_headroom=volume_cap_headroom,
            recent_realized_pnl=recent_realized_pnl,
            adverse_selection_risk=adverse_selection_risk,
            realization_failed=realization_failed,
            inventory_sign=inventory_sign,
        ),
    )
    if pressure + 1e-12 >= PRESSURE_EMERGENCY or ratio + 1e-12 >= RATIO_EMERGENCY:
        return STATE_EMERGENCY
    if pressure + 1e-12 >= PRESSURE_EXIT_ONLY or ratio + 1e-12 >= RATIO_EXIT_ONLY:
        return STATE_EXIT_ONLY
    if (
        _finite(unrealized_pnl) <= -25.0
        and _finite(inventory_age) >= 12.0
        and ratio + 1e-12 >= 0.20
    ):
        return STATE_DEFENSIVE
    if pressure + 1e-12 >= PRESSURE_DEFENSIVE or ratio + 1e-12 >= RATIO_DEFENSIVE:
        return STATE_DEFENSIVE
    if pressure + 1e-12 >= PRESSURE_CAUTION:
        return STATE_CAUTION
    return STATE_NORMAL


@dataclass(frozen=True)
class InventoryStatePolicy:
    state: str
    same_side_entry_mult: float
    exit_side_mult: float
    allow_same_side_entry: bool
    allow_inventory_increase: bool
    allow_maker_entry: bool
    allow_maker_exit: bool
    improve_exit: bool
    allow_aggressive_maker: bool
    taker_eligible: bool
    hard_taker_requires_safety: bool

    def as_log(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "same_side_entry_mult": self.same_side_entry_mult,
            "exit_side_mult": self.exit_side_mult,
            "allow_same_side_entry": int(bool(self.allow_same_side_entry)),
            "allow_inventory_increase": int(bool(self.allow_inventory_increase)),
            "allow_maker_entry": int(bool(self.allow_maker_entry)),
            "allow_maker_exit": int(bool(self.allow_maker_exit)),
            "improve_exit": int(bool(self.improve_exit)),
            "allow_aggressive_maker": int(bool(self.allow_aggressive_maker)),
            "taker_eligible": int(bool(self.taker_eligible)),
        }


def inventory_state_policy(
    state: str,
    *,
    hard_safety: bool = False,
) -> InventoryStatePolicy:
    token = str(state or STATE_NORMAL).upper()
    if token == STATE_EMERGENCY:
        return InventoryStatePolicy(
            state=STATE_EMERGENCY,
            same_side_entry_mult=0.0,
            exit_side_mult=1.0,
            allow_same_side_entry=False,
            allow_inventory_increase=False,
            allow_maker_entry=False,
            allow_maker_exit=True,
            improve_exit=True,
            allow_aggressive_maker=True,
            taker_eligible=True,
            hard_taker_requires_safety=not bool(hard_safety),
        )
    if token == STATE_EXIT_ONLY:
        return InventoryStatePolicy(
            state=STATE_EXIT_ONLY,
            same_side_entry_mult=0.0,
            exit_side_mult=1.0,
            allow_same_side_entry=False,
            allow_inventory_increase=False,
            allow_maker_entry=False,
            allow_maker_exit=True,
            improve_exit=True,
            allow_aggressive_maker=True,
            taker_eligible=False,
            hard_taker_requires_safety=True,
        )
    if token == STATE_DEFENSIVE:
        return InventoryStatePolicy(
            state=STATE_DEFENSIVE,
            same_side_entry_mult=DEFENSIVE_ENTRY_MULT,
            exit_side_mult=DEFENSIVE_EXIT_MULT,
            allow_same_side_entry=False,
            allow_inventory_increase=False,
            allow_maker_entry=True,
            allow_maker_exit=True,
            improve_exit=True,
            allow_aggressive_maker=True,
            taker_eligible=False,
            hard_taker_requires_safety=True,
        )
    if token == STATE_CAUTION:
        return InventoryStatePolicy(
            state=STATE_CAUTION,
            same_side_entry_mult=CAUTION_ENTRY_MULT,
            exit_side_mult=CAUTION_EXIT_MULT,
            allow_same_side_entry=True,
            allow_inventory_increase=True,
            allow_maker_entry=True,
            allow_maker_exit=True,
            improve_exit=True,
            allow_aggressive_maker=False,
            taker_eligible=False,
            hard_taker_requires_safety=True,
        )
    return InventoryStatePolicy(
        state=STATE_NORMAL,
        same_side_entry_mult=1.0,
        exit_side_mult=1.0,
        allow_same_side_entry=True,
        allow_inventory_increase=True,
        allow_maker_entry=True,
        allow_maker_exit=True,
        improve_exit=False,
        allow_aggressive_maker=False,
        taker_eligible=False,
        hard_taker_requires_safety=True,
    )


def is_same_side_entry(side: str, inventory_sign: float) -> bool:
    token = str(side or "").upper()
    sign = _finite(inventory_sign)
    if sign > 0.0:
        return token in {"BUY", "BID", "0"}
    if sign < 0.0:
        return token in {"SELL", "ASK", "1"}
    return False


def side_size_multiplier(
    *,
    side: str,
    inventory_sign: float,
    policy: InventoryStatePolicy,
) -> float:
    if abs(_finite(inventory_sign)) <= 1e-12:
        return 1.0 if policy.allow_maker_entry else 0.0
    if is_same_side_entry(side, inventory_sign):
        if not policy.allow_same_side_entry or not policy.allow_inventory_increase:
            return 0.0
        return max(0.0, float(policy.same_side_entry_mult))
    return max(0.0, float(policy.exit_side_mult))


ACTION_PASSIVE = "PASSIVE_MAKER_EXIT"
ACTION_COMPETITIVE = "COMPETITIVE_MAKER_EXIT"
ACTION_AGGRESSIVE = "AGGRESSIVE_MAKER_EXIT"
ACTION_TAKER = "SELECTIVE_TAKER_EXIT"


def apply_exit_action_for_state(
    *,
    state: str,
    selected_action: str,
    hard_safety: bool = False,
    economic_ok: bool = False,
) -> tuple[str, str | None]:
    """Adjust a proposed exit after hybrid. Does not invent a taker."""
    policy = inventory_state_policy(state, hard_safety=hard_safety)
    action = str(selected_action or ACTION_PASSIVE)
    override = None
    token = str(state or STATE_NORMAL).upper()
    if (
        action == ACTION_TAKER
        and token == STATE_EXIT_ONLY
        and not hard_safety
        and not economic_ok
        and not policy.taker_eligible
    ):
        action = ACTION_AGGRESSIVE
        override = "STATE_TAKER_BLOCKED"
    if state == STATE_CAUTION and action == ACTION_PASSIVE:
        action = ACTION_COMPETITIVE
        override = override or "STATE_EXIT_IMPROVED"
    if state == STATE_DEFENSIVE and action in {ACTION_PASSIVE, ACTION_COMPETITIVE}:
        action = ACTION_AGGRESSIVE
        override = override or "STATE_DEFENSIVE_MAKER"
    if state == STATE_EXIT_ONLY and action == ACTION_PASSIVE:
        action = ACTION_COMPETITIVE
        override = override or "STATE_EXIT_ONLY"
    if state == STATE_EMERGENCY and action != ACTION_TAKER:
        action = ACTION_AGGRESSIVE
        override = override or "EMERGENCY_MAKER"
    return action, override
