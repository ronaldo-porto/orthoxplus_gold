# SPDX-License-Identifier: MIT
"""SN79 Research V4.14.4 RealNet exit-authority arbitration.

This helper resolves the V4.14.3 overlap between:
- legacy inventory-liveness rescue (historically capped near -12 bps),
- V4.14.3 soft bounded-loss handling (-8 .. -18 bps), and
- V4.14.3 hard bounded-loss escape (-18 .. -25 bps).

One function is the final non-catastrophic loss authority.  Catastrophic hard
risk stays separate and must still obey the existing Strategy1_Research safety
path.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

REALNET_EXIT_AUTHORITY_VERSION = "realnet_exit_authority_v4_14_4"

ACTION_DEFER = "DEFER"
ACTION_KEEP_MAKER = "KEEP_MAKER"
ACTION_TAKER_ESCAPE = "TAKER_ESCAPE"
ACTION_PARK = "PARK"
ACTION_HARD_RISK = "HARD_RISK_DEFER"

STAGE_NONE = "NONE"
STAGE_SOFT_HOLD = "SOFT_HOLD"
STAGE_SOFT_ESCAPE = "SOFT_ESCAPE"
STAGE_HARD_ESCAPE = "HARD_ESCAPE"
STAGE_BELOW_FLOOR = "BELOW_FLOOR"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


@dataclass(frozen=True)
class RealNetExitDecision:
    action: str
    stage: str
    reason: str
    authorized: bool
    park: bool
    taker_net_bps: float
    maker_net_bps: float
    soft_floor_bps: float
    hard_trigger_bps: float
    absolute_floor_bps: float
    legacy_liveness_authorized: bool
    legacy_liveness_park: bool
    legacy_liveness_floor_bps: float | None
    legacy_conflict_resolved: bool

    def as_log(self) -> dict[str, Any]:
        return {
            "realnet_exit_authority_version": REALNET_EXIT_AUTHORITY_VERSION,
            "realnet_exit_action": self.action,
            "realnet_exit_stage": self.stage,
            "realnet_exit_reason": self.reason,
            "realnet_exit_authorized": int(bool(self.authorized)),
            "realnet_exit_park": int(bool(self.park)),
            "realnet_taker_net_bps": self.taker_net_bps,
            "realnet_maker_net_bps": self.maker_net_bps,
            "realnet_soft_floor_bps": self.soft_floor_bps,
            "realnet_hard_trigger_bps": self.hard_trigger_bps,
            "realnet_absolute_floor_bps": self.absolute_floor_bps,
            "legacy_liveness_authorized": int(bool(self.legacy_liveness_authorized)),
            "legacy_liveness_park": int(bool(self.legacy_liveness_park)),
            "legacy_liveness_floor_bps": self.legacy_liveness_floor_bps,
            "legacy_conflict_resolved": int(bool(self.legacy_conflict_resolved)),
        }


def arbitrate_realnet_exit(
    *,
    taker_net_bps: float,
    maker_net_bps: float | None,
    maker_executable: bool,
    failed_exit_count: int,
    inventory_age: float,
    stop_loss_hit: bool = False,
    catastrophic_hard_risk: bool = False,
    adverse_evidence: bool = False,
    wait_ev_bps: float | None = None,
    # Existing liveness result is advisory only.  It no longer owns the final
    # non-catastrophic loss floor.
    liveness_authorized: bool = False,
    liveness_park: bool = False,
    liveness_floor_bps: float | None = None,
    # V4.14.3 policy constants.
    soft_floor_bps: float = -8.0,
    hard_trigger_bps: float = -18.0,
    absolute_floor_bps: float = -25.0,
    bounded_loss_min_age_ticks: float = 2.0,
    positive_maker_floor_bps: float = 1.0,
    positive_maker_max_failed_exits: int = 4,
    positive_maker_max_age_ticks: float = 8.0,
    min_taker_vs_wait_advantage_bps: float = 0.0,
) -> RealNetExitDecision:
    """Return the single final non-catastrophic exit decision.

    Ordering is deliberate:
    1. Catastrophic authority remains outside this policy.
    2. Never force a normal Taker below the absolute -25 bps floor.
    3. <= -18 bps is the V4.14.3 hard escape window and has no age veto.
    4. -8 .. -18 bps keeps a profitable executable Maker while the bounded
       positive-Maker veto is still young; otherwise a bounded soft escape may
       recycle the position.
    5. Above -8 bps, normal unified-exit economics remain authoritative.

    This prevents a legacy -12 bps liveness floor from either blocking the
    V4.14.3 -18..-25 hard escape or authorizing a contradictory early dump.
    """
    taker = _finite(taker_net_bps, -1e9)
    maker = _finite(maker_net_bps, -1e9) if maker_net_bps is not None else -1e9
    failures = max(0, int(failed_exit_count))
    age = max(0.0, _finite(inventory_age, 0.0))

    soft = min(0.0, _finite(soft_floor_bps, -8.0))
    absolute = min(soft, _finite(absolute_floor_bps, -25.0))
    # Never widen V4.14.3's hard bound below -25 bps accidentally.
    absolute = max(-25.0, absolute)
    hard = _finite(hard_trigger_bps, -18.0)
    hard = min(soft, max(absolute, hard))

    legacy_floor = None
    if liveness_floor_bps is not None:
        legacy_floor = _finite(liveness_floor_bps, 0.0)

    def result(action: str, stage: str, reason: str, *, authorized: bool, park: bool) -> RealNetExitDecision:
        legacy_conflict = bool(
            (liveness_authorized and action != ACTION_TAKER_ESCAPE)
            or (liveness_park and action != ACTION_PARK)
            or (
                legacy_floor is not None
                and legacy_floor > absolute + 1e-12
                and hard - 1e-12 >= taker >= absolute - 1e-12
            )
        )
        return RealNetExitDecision(
            action=action,
            stage=stage,
            reason=reason,
            authorized=authorized,
            park=park,
            taker_net_bps=taker,
            maker_net_bps=maker,
            soft_floor_bps=soft,
            hard_trigger_bps=hard,
            absolute_floor_bps=absolute,
            legacy_liveness_authorized=bool(liveness_authorized),
            legacy_liveness_park=bool(liveness_park),
            legacy_liveness_floor_bps=legacy_floor,
            legacy_conflict_resolved=legacy_conflict,
        )

    if catastrophic_hard_risk:
        # Do not silently replace the pre-existing catastrophic safety path.
        return result(
            ACTION_HARD_RISK, STAGE_NONE, "CATASTROPHIC_AUTHORITY_SEPARATE",
            authorized=False, park=False,
        )

    if taker < absolute - 1e-12:
        return result(
            ACTION_PARK, STAGE_BELOW_FLOOR, "BELOW_ABSOLUTE_BOUNDED_LOSS_FLOOR",
            authorized=False, park=True,
        )

    # V4.14.3 hard stage: price itself is the event.  No legacy -12 bps floor,
    # minimum-age gate, or positive-Maker veto can block this bounded recycle.
    if taker <= hard + 1e-12:
        return result(
            ACTION_TAKER_ESCAPE, STAGE_HARD_ESCAPE, "HARD_ESCAPE_AUTHORITATIVE",
            authorized=True, park=False,
        )

    # V4.14.3 soft corridor.
    if taker <= soft + 1e-12:
        positive_maker_hold = bool(
            maker_executable
            and maker + 1e-12 >= _finite(positive_maker_floor_bps, 1.0)
            and failures < max(1, int(positive_maker_max_failed_exits))
            and age + 1e-12 < max(0.0, _finite(positive_maker_max_age_ticks, 8.0))
            and not stop_loss_hit
        )
        if positive_maker_hold:
            return result(
                ACTION_KEEP_MAKER, STAGE_SOFT_HOLD, "PROFITABLE_MAKER_VETO",
                authorized=False, park=False,
            )

        armed = bool(
            stop_loss_hit
            or adverse_evidence
            or failures >= max(1, int(positive_maker_max_failed_exits))
            or age + 1e-12 >= max(0.0, _finite(bounded_loss_min_age_ticks, 2.0))
            or liveness_authorized
        )
        if not armed:
            return result(
                ACTION_KEEP_MAKER, STAGE_SOFT_HOLD, "SOFT_ESCAPE_NOT_ARMED",
                authorized=False, park=False,
            )

        return result(
            ACTION_TAKER_ESCAPE, STAGE_SOFT_ESCAPE, "SOFT_ESCAPE_BOUNDED",
            authorized=True, park=False,
        )

    # No bounded-loss authority is needed above the -8 bps boundary.  Existing
    # profitable/normal unified-exit economics continue unchanged.
    return result(
        ACTION_DEFER, STAGE_NONE, "NORMAL_UNIFIED_EXIT",
        authorized=False, park=False,
    )
