# SPDX-License-Identifier: MIT
"""V4.12.18 inventory-state decoupling policy for Strategy1 Research.

This module is intentionally independent of the existing Kappa/alpha/Taker
scheduler.  It answers one narrow question: when an uncovered/TWO_AWAY
position has failed to realize, may we move its Maker exit toward touch, take a
small bounded loss, or park it so stale inventory does not consume acquisition
capacity?

Hard invariants:
- Loss authority and parking/capacity authority are independent.
- QUALIFIED and ONE_AWAY positions remain protected from bounded-loss subsidy,
  but they may PARK when their safe executable Maker exit is unavailable.
- Every non-flat score state is park-eligible; PARK never implies a synthetic flatten.
- The normal bounded rescue floor is -8 bps.
- The absolute hard rescue floor is -12 bps and cannot be widened by config.
- Positions already worse than the hard floor are parked, never dumped.
- Crossing from the soft -8 bps window into the still-bounded -12 bps window
  activates hard rescue immediately; it does not wait for an age counter.
- Parking changes scheduling/capacity only; it is not a synthetic flatten.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

INVENTORY_LIVENESS_VERSION = "inventory_state_decoupling_v4_12_18"
FRESH_MAKER_GRACE_VERSION = "fresh_maker_grace_v4_13_2"
POSITIVE_MAKER_VETO_VERSION = "positive_maker_veto_v4_13_5"
BOUNDED_LOSS_ESCAPE_VERSION = "bounded_loss_escape_v4_14_0"

SCORE_QUALIFIED = "QUALIFIED"
SCORE_ONE_AWAY = "ONE_AWAY"
SCORE_TWO_AWAY = "TWO_AWAY"
SCORE_UNCOVERED = "UNCOVERED"

LIVENESS_ELIGIBLE_STATES = frozenset({SCORE_TWO_AWAY, SCORE_UNCOVERED})
PROTECTED_SCORE_STATES = frozenset({SCORE_QUALIFIED, SCORE_ONE_AWAY})


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default

def bounded_loss_escape_applies(
    *,
    taker_net_bps: float,
    peak_taker_net_bps: float,
    inventory_age: float,
    enabled: bool = True,
    min_age_ticks: float = 2.0,
    soft_floor_bps: float = -8.0,
    hard_floor_bps: float = -25.0,
    min_drawdown_bps: float = 2.0,
) -> bool:
    """Recycle a deteriorating small loss before it becomes parked tail risk.

    No new history/state is required: ``peak_taker_net_bps`` is already tracked
    by Strategy1_Research for the current inventory lifecycle.
    """
    if not bool(enabled):
        return False
    age = max(0.0, _finite(inventory_age))
    if age + 1e-12 < max(1.0, _finite(min_age_ticks, 2.0)):
        return False
    soft = min(0.0, _finite(soft_floor_bps, -8.0))
    hard = min(soft, _finite(hard_floor_bps, -25.0))
    taker = _finite(taker_net_bps, default=-1e9)
    if taker > soft + 1e-12 or taker < hard - 1e-12:
        return False
    peak = _finite(peak_taker_net_bps, default=taker)
    drawdown = max(0.0, peak - taker)
    return drawdown + 1e-12 >= max(0.0, _finite(min_drawdown_bps, 2.0))


def classify_score_state(observations_remaining: int, required_observations: int = 3) -> str:
    remaining = max(0, int(observations_remaining))
    required = max(1, int(required_observations))
    if remaining <= 0:
        return SCORE_QUALIFIED
    if remaining == 1:
        return SCORE_ONE_AWAY
    if remaining == 2:
        return SCORE_TWO_AWAY
    # For the current SN79 threshold of 3 this is the zero-observation/cold case.
    # Keep the generic >= required behavior so the helper remains correct if the
    # threshold changes.
    if remaining >= required:
        return SCORE_UNCOVERED
    return SCORE_UNCOVERED


@dataclass(frozen=True)
class InventoryLivenessStage:
    score_state: str
    # ``eligible`` is retained as a backwards-compatible alias for bounded-loss
    # rescue eligibility. V4.12.18 makes parking authority independent.
    eligible: bool
    protected: bool
    loss_rescue_eligible: bool
    park_eligible: bool
    protected_park_armed: bool
    maker_rescue_armed: bool
    taker_rescue_armed: bool
    hard_window: bool
    maker_floor_bps: float
    taker_floor_bps: float
    hard_floor_bps: float
    failed_exit_count: int
    inventory_age: float

    def as_log(self) -> dict[str, Any]:
        return {
            "inventory_liveness_version": INVENTORY_LIVENESS_VERSION,
            "liveness_score_state": self.score_state,
            "liveness_eligible": int(bool(self.eligible)),
            "liveness_protected": int(bool(self.protected)),
            "loss_rescue_eligible": int(bool(self.loss_rescue_eligible)),
            "park_eligible": int(bool(self.park_eligible)),
            "protected_park_armed": int(bool(self.protected_park_armed)),
            "liveness_maker_armed": int(bool(self.maker_rescue_armed)),
            "liveness_taker_armed": int(bool(self.taker_rescue_armed)),
            "liveness_hard_window": int(bool(self.hard_window)),
            "liveness_maker_floor_bps": self.maker_floor_bps,
            "liveness_taker_floor_bps": self.taker_floor_bps,
            "liveness_hard_floor_bps": self.hard_floor_bps,
            "liveness_failed_exits": self.failed_exit_count,
            "liveness_inventory_age": self.inventory_age,
        }


def classify_liveness_stage(
    *,
    observations_remaining: int,
    required_observations: int = 3,
    failed_exit_count: int,
    inventory_age: float,
    inventory_state: str,
    stop_loss_hit: bool,
    maker_failed_exits: int = 3,
    maker_min_age_ticks: float = 8.0,
    taker_failed_exits: int = 8,
    taker_min_age_ticks: float = 16.0,
    hard_failed_exits: int = 12,
    hard_min_age_ticks: float = 24.0,
    protected_park_failed_exits: int = 4,
    protected_park_min_age_ticks: float = 8.0,
    maker_floor_bps: float = -4.0,
    soft_taker_floor_bps: float = -8.0,
    hard_taker_floor_bps: float = -12.0,
) -> InventoryLivenessStage:
    score_state = classify_score_state(observations_remaining, required_observations)
    loss_rescue_eligible = score_state in LIVENESS_ELIGIBLE_STATES
    eligible = loss_rescue_eligible
    protected = score_state in PROTECTED_SCORE_STATES
    # Capacity authority is independent from loss authority. Any real non-flat
    # score state may park; the caller still enforces total-book/base risk caps.
    park_eligible = score_state in {
        SCORE_QUALIFIED, SCORE_ONE_AWAY, SCORE_TWO_AWAY, SCORE_UNCOVERED
    }
    failures = max(0, int(failed_exit_count))
    age = max(0.0, _finite(inventory_age))
    state = str(inventory_state or "NORMAL").upper()
    protected_park_armed = bool(
        protected
        and (
            bool(stop_loss_hit)
            or state in {"EXIT_ONLY", "EMERGENCY"}
            or failures >= max(1, int(protected_park_failed_exits))
            or age + 1e-12 >= max(1.0, _finite(protected_park_min_age_ticks, 8.0))
        )
    )

    maker_armed = bool(
        loss_rescue_eligible
        and (
            failures >= max(1, int(maker_failed_exits))
            or age + 1e-12 >= max(1.0, _finite(maker_min_age_ticks, 8.0))
        )
    )
    taker_armed = bool(
        loss_rescue_eligible
        and (
            failures >= max(1, int(taker_failed_exits))
            or age + 1e-12 >= max(1.0, _finite(taker_min_age_ticks, 16.0))
        )
    )
    hard_window = bool(
        loss_rescue_eligible
        and (
            bool(stop_loss_hit)
            or state in {"EXIT_ONLY", "EMERGENCY"}
            or failures >= max(1, int(hard_failed_exits))
            or age + 1e-12 >= max(1.0, _finite(hard_min_age_ticks, 24.0))
        )
    )

    # Config may tighten floors toward zero, but never widen the hard rescue
    # beyond -12 bps.  Maker rescue is also bounded above that hard floor.
    hard_floor = max(-12.0, min(0.0, _finite(hard_taker_floor_bps, -12.0)))
    soft_floor = max(hard_floor, min(0.0, _finite(soft_taker_floor_bps, -8.0)))
    maker_floor = max(soft_floor, min(0.0, _finite(maker_floor_bps, -4.0)))
    taker_floor = hard_floor if hard_window else soft_floor

    return InventoryLivenessStage(
        score_state=score_state,
        eligible=eligible,
        protected=protected,
        loss_rescue_eligible=loss_rescue_eligible,
        park_eligible=park_eligible,
        protected_park_armed=protected_park_armed,
        maker_rescue_armed=maker_armed,
        taker_rescue_armed=taker_armed,
        hard_window=hard_window,
        maker_floor_bps=maker_floor,
        taker_floor_bps=taker_floor,
        hard_floor_bps=hard_floor,
        failed_exit_count=failures,
        inventory_age=age,
    )


@dataclass(frozen=True)
class ProtectedParkDecision:
    park: bool
    reason: str
    executable_maker_net_bps: float
    protected_floor_bps: float

    def as_log(self) -> dict[str, Any]:
        return {
            "protected_park": int(bool(self.park)),
            "protected_park_reason": self.reason,
            "candidate_touch_exit_bps": self.executable_maker_net_bps,
            "protected_floor_bps": self.protected_floor_bps,
        }


def evaluate_protected_parking(
    stage: InventoryLivenessStage,
    *,
    executable_maker_net_bps: float,
    protected_floor_bps: float,
) -> ProtectedParkDecision:
    """Decouple Kappa loss protection from active-capacity consumption.

    QUALIFIED/ONE_AWAY may park once stale/EMERGENCY if the closest legal
    post-only Maker exit would violate their existing protected floor. Parking
    never grants Taker authority and never relaxes that floor.
    """
    maker_net = _finite(executable_maker_net_bps, default=-1e9)
    floor = min(0.0, _finite(protected_floor_bps, 0.0))
    if not stage.protected or not stage.park_eligible:
        return ProtectedParkDecision(False, "NOT_PROTECTED_PARK_STATE", maker_net, floor)
    if not stage.protected_park_armed:
        return ProtectedParkDecision(False, "PROTECTED_PARK_NOT_ARMED", maker_net, floor)
    if maker_net + 1e-12 >= floor:
        return ProtectedParkDecision(False, "PROTECTED_TOUCH_EXECUTABLE", maker_net, floor)
    return ProtectedParkDecision(True, "PROTECTED_STALE_NON_EXECUTABLE", maker_net, floor)


@dataclass(frozen=True)
class InventoryRescueDecision:
    authorized: bool
    park: bool
    reason: str
    adverse_evidence: bool
    taker_net_bps: float
    wait_ev_bps: float
    ev_advantage_bps: float
    allowed_loss_floor_bps: float
    hard_floor_bps: float

    def as_log(self) -> dict[str, Any]:
        return {
            "rescue_authorized": int(bool(self.authorized)),
            "rescue_park": int(bool(self.park)),
            "rescue_reason": self.reason,
            "rescue_adverse_evidence": int(bool(self.adverse_evidence)),
            "rescue_taker_net_bps": self.taker_net_bps,
            "rescue_wait_ev_bps": self.wait_ev_bps,
            "rescue_ev_advantage_bps": self.ev_advantage_bps,
            "rescue_loss_floor_bps": self.allowed_loss_floor_bps,
            "rescue_hard_floor_bps": self.hard_floor_bps,
        }


def evaluate_bounded_rescue(
    stage: InventoryLivenessStage,
    *,
    taker_net_bps: float,
    wait_ev_bps: float,
    expected_markout_bps: float,
    adverse_selection_risk: float,
    stop_loss_hit: bool,
    inventory_state: str,
    min_ev_advantage_bps: float = 0.5,
    adverse_markout_bps: float = 1.0,
    adverse_risk_floor: float = 0.25,
) -> InventoryRescueDecision:
    taker = _finite(taker_net_bps, default=-1e9)
    wait = _finite(wait_ev_bps, default=1e9)
    advantage = taker - wait
    state = str(inventory_state or "NORMAL").upper()
    adverse = bool(
        bool(stop_loss_hit)
        or state in {"DEFENSIVE", "EXIT_ONLY", "EMERGENCY"}
        or _finite(expected_markout_bps) <= -max(0.0, _finite(adverse_markout_bps, 1.0))
        or _finite(adverse_selection_risk) >= max(0.0, _finite(adverse_risk_floor, 0.25))
    )

    if not stage.eligible or stage.protected:
        return InventoryRescueDecision(
            False, False, "SCORE_STATE_PROTECTED", adverse, taker, wait, advantage,
            stage.taker_floor_bps, stage.hard_floor_bps,
        )
    # Once the executable loss is already beyond the absolute -12 bps ceiling,
    # do not chase it with Taker. Park it and release acquisition capacity.
    if taker < stage.hard_floor_bps - 1e-12:
        return InventoryRescueDecision(
            False, True, "LOSS_BEYOND_HARD_FLOOR", adverse, taker, wait, advantage,
            stage.taker_floor_bps, stage.hard_floor_bps,
        )

    # V4.12.17 event-driven hard window.  A fast book can move from the soft
    # -8 bps floor to -9/-11 bps before the age/failed-exit counters reach their
    # old hard-window threshold.  That price crossing *is itself* the emergency
    # event.  Authorize evaluation against the absolute -12 bps ceiling now,
    # rather than parking a still-recoverable position and waiting until the
    # window has disappeared. QUALIFIED/ONE_AWAY were already rejected above.
    price_hard_window = bool(
        taker < stage.taker_floor_bps - 1e-12
        and taker >= stage.hard_floor_bps - 1e-12
    )
    effective_floor = stage.hard_floor_bps if price_hard_window else stage.taker_floor_bps
    effective_armed = bool(stage.taker_rescue_armed or price_hard_window)
    if not effective_armed:
        return InventoryRescueDecision(
            False, False, "RESCUE_NOT_ARMED", adverse, taker, wait, advantage,
            effective_floor, stage.hard_floor_bps,
        )
    if not adverse:
        return InventoryRescueDecision(
            False, False, "NO_ADVERSE_EVIDENCE", adverse, taker, wait, advantage,
            effective_floor, stage.hard_floor_bps,
        )
    if taker < effective_floor - 1e-12:
        return InventoryRescueDecision(
            False, True, "LOSS_BEYOND_CURRENT_RESCUE_FLOOR", adverse, taker, wait, advantage,
            effective_floor, stage.hard_floor_bps,
        )
    margin = max(0.0, _finite(min_ev_advantage_bps, 0.5))
    if advantage <= margin + 1e-12:
        return InventoryRescueDecision(
            False, False, "WAIT_EV_BETTER", adverse, taker, wait, advantage,
            effective_floor, stage.hard_floor_bps,
        )
    return InventoryRescueDecision(
        True, False,
        "PRICE_HARD_WINDOW_RESCUE" if price_hard_window else "BOUNDED_RESCUE_GT_WAIT",
        adverse, taker, wait, advantage, effective_floor, stage.hard_floor_bps,
    )


def fresh_maker_grace_applies(
    stage: InventoryLivenessStage,
    rescue: InventoryRescueDecision | None,
    *,
    maker_net_bps: float,
    maker_executable: bool,
    stop_loss_hit: bool,
    inventory_state: str,
    hard_risk: bool = False,
    enabled: bool = True,
    grace_ticks: float = 3.0,
) -> bool:
    """V4.13.2 short Maker-first guard for event-driven hard-window rescue.

    This does not change V4.12.18 rescue floors or arming. It only prevents an
    age-0/1/2/3 position with zero failed exits and a profitable executable Maker
    close from being immediately converted into a PRICE_HARD_WINDOW_RESCUE Taker
    loss. Explicit stop/emergency/RISK authority bypasses the grace.
    """
    if not bool(enabled) or rescue is None:
        return False
    if not bool(rescue.authorized) or str(rescue.reason or "") != "PRICE_HARD_WINDOW_RESCUE":
        return False
    if bool(stop_loss_hit) or bool(hard_risk):
        return False
    state = str(inventory_state or "NORMAL").upper()
    if state in {"EXIT_ONLY", "EMERGENCY"}:
        return False
    if int(stage.failed_exit_count) != 0:
        return False
    grace = max(1.0, min(3.0, _finite(grace_ticks, 3.0)))
    if float(stage.inventory_age) > grace + 1e-12:
        return False
    if not bool(maker_executable):
        return False
    return _finite(maker_net_bps, default=-1e9) > 0.0


def positive_maker_rescue_veto_applies(
    *,
    maker_net_bps: float,
    taker_net_bps: float,
    maker_executable: bool,
    failed_exit_count: int,
    stop_loss_hit: bool,
    inventory_state: str,
    hard_risk: bool = False,
    enabled: bool = True,
    maker_positive_floor_bps: float = 1.0,
    max_failed_exits: int = 3,
) -> bool:
    """V4.13.5 authority veto: positive executable Maker beats negative Taker.

    This is deliberately *not* another age grace.  It protects a meaningful
    positive Maker close while ordinary rescue logic would realize a negative
    Taker, but only for the first few failed Maker attempts.  Genuine hard-risk,
    stop-loss, EXIT_ONLY/EMERGENCY authority remains senior.
    """
    if not bool(enabled):
        return False
    if bool(stop_loss_hit) or bool(hard_risk):
        return False
    state = str(inventory_state or "NORMAL").upper()
    if state in {"EXIT_ONLY", "EMERGENCY"}:
        return False
    if not bool(maker_executable):
        return False
    fail_limit = max(1, int(max_failed_exits))
    if max(0, int(failed_exit_count)) >= fail_limit:
        return False
    maker_floor = max(0.0, _finite(maker_positive_floor_bps, 1.0))
    maker = _finite(maker_net_bps, default=-1e9)
    taker = _finite(taker_net_bps, default=1e9)
    return bool(maker + 1e-12 >= maker_floor and taker < -1e-12)


def parked_refresh_due(
    *,
    current_tick: int,
    last_refresh_tick: int | None,
    current_mid: float | None,
    last_mid: float | None,
    refresh_interval_ticks: int = 25,
    material_touch_move_bps: float = 8.0,
    hard_risk: bool = False,
) -> tuple[bool, str]:
    if hard_risk:
        return True, "HARD_RISK"
    now = max(0, int(current_tick))
    if last_refresh_tick is None:
        return True, "FIRST_REFRESH"
    if now - int(last_refresh_tick) >= max(1, int(refresh_interval_ticks)):
        return True, "INTERVAL"
    mid = _finite(current_mid, default=0.0)
    prev = _finite(last_mid, default=0.0)
    if mid > 0.0 and prev > 0.0:
        move = abs(mid - prev) / prev * 10_000.0
        if move + 1e-12 >= max(0.0, _finite(material_touch_move_bps, 8.0)):
            return True, "TOUCH_MOVE"
    return False, "PARKED_COOLDOWN"
