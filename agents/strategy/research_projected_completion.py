# SPDX-License-Identifier: MIT
"""V4.15.2 projected qualification quality for ONE_AWAY / TWO_AWAY books.

This is not a score authority. TOTAL_SCORE_FRONTIER still owns due/value.
Projected quality only answers: would the expected next RT(s) produce a
healthy 3-observation Kappa/PnL state, or would they lock in a bad book?
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

PROJECTED_COMPLETION_VERSION = "projected_completion_v4_15_2"
REASON_HEALTHY = "PROJECTED_HEALTHY"
REASON_UNHEALTHY = "PROJECTED_UNHEALTHY"
REASON_UNCERTAIN = "PROJECTED_UNCERTAIN"
REASON_NOT_INCOMPLETE = "NOT_INCOMPLETE"

_REQUIRED = 3


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class ProjectedCompletion:
    remaining: int
    projected_completion_pnl: float
    projected_completion_downside: float
    projected_completion_quality: float
    projected_completion_healthy: bool | None
    projected_completion_reason: str

    @property
    def uncertain(self) -> bool:
        return self.projected_completion_healthy is None

    def as_log(self) -> dict[str, Any]:
        healthy = self.projected_completion_healthy
        return {
            "projected_completion_version": PROJECTED_COMPLETION_VERSION,
            "projected_completion_remaining": int(self.remaining),
            "projected_completion_pnl": float(self.projected_completion_pnl),
            "projected_completion_downside": float(self.projected_completion_downside),
            "projected_completion_quality": float(self.projected_completion_quality),
            "projected_completion_healthy": (
                None if healthy is None else int(bool(healthy))
            ),
            "projected_completion_reason": str(self.projected_completion_reason),
        }


def expected_next_rt_pnl(
    *,
    lifecycle_ev: float,
    p_fill: float,
    scale: float = 1.0,
) -> float:
    """Fill-weighted next-RT contribution in the same units as LifecycleEV."""
    p = _clip01(_finite(p_fill, 0.0))
    return p * _finite(lifecycle_ev) * max(1e-9, _finite(scale, 1.0))


def project_completion_quality(
    *,
    observations_remaining: int,
    realized_sum: float = 0.0,
    realized_count: int = 0,
    rolling_downside_m3: float = 0.0,
    expected_next_rt_pnl: float = 0.0,
    lifecycle_ev: float = 0.0,
    p_fill: float = 0.0,
    required: int = _REQUIRED,
) -> ProjectedCompletion:
    remaining = max(0, int(observations_remaining or 0))
    need = max(1, int(required or _REQUIRED))
    if remaining <= 0 or remaining >= need:
        return ProjectedCompletion(
            remaining=remaining,
            projected_completion_pnl=_finite(realized_sum),
            projected_completion_downside=max(0.0, _finite(rolling_downside_m3)),
            projected_completion_quality=0.0,
            projected_completion_healthy=None,
            projected_completion_reason=REASON_NOT_INCOMPLETE,
        )

    nxt = _finite(expected_next_rt_pnl)
    if abs(nxt) <= 1e-15:
        nxt = expected_next_rt_pnl_from_parts(
            lifecycle_ev=lifecycle_ev, p_fill=p_fill,
        )
    existing = _finite(realized_sum)
    n_have = max(0, int(realized_count or 0))
    projected_sum = existing + nxt * float(remaining)
    projected_mean = projected_sum / float(need)
    downside = max(0.0, _finite(rolling_downside_m3))
    if nxt < 0.0:
        downside = downside + abs(nxt) * float(remaining)
    p = _clip01(_finite(p_fill, 0.0))
    ev = _finite(lifecycle_ev)

    evidence = n_have > 0 or p >= 0.08
    known_bad = (
        (n_have > 0 and existing < -1e-12 and nxt <= 1e-12)
        or (ev < -1e-12 and p >= 0.05)
        or projected_mean < -1e-12
    )
    known_good = (
        projected_mean >= 0.0
        and ev > 1e-12
        and (p >= 0.08 or nxt > 1e-12)
        and downside <= max(0.08, 0.35 * max(1e-9, abs(projected_sum) + 0.05))
    )

    if remaining == 1:
        quality = 0.55 + 0.35 * math.tanh(projected_mean * 8.0) + 0.10 * p
    else:
        quality = 0.40 + 0.30 * math.tanh(projected_mean * 8.0) + 0.10 * p
    quality = _clip01(quality - 0.20 * math.tanh(downside * 6.0))

    if known_bad:
        return ProjectedCompletion(
            remaining=remaining,
            projected_completion_pnl=projected_sum,
            projected_completion_downside=downside,
            projected_completion_quality=min(quality, 0.24),
            projected_completion_healthy=False,
            projected_completion_reason=REASON_UNHEALTHY,
        )
    if known_good and evidence:
        return ProjectedCompletion(
            remaining=remaining,
            projected_completion_pnl=projected_sum,
            projected_completion_downside=downside,
            projected_completion_quality=max(quality, 0.56),
            projected_completion_healthy=True,
            projected_completion_reason=REASON_HEALTHY,
        )
    return ProjectedCompletion(
        remaining=remaining,
        projected_completion_pnl=projected_sum,
        projected_completion_downside=downside,
        projected_completion_quality=quality,
        projected_completion_healthy=None,
        projected_completion_reason=REASON_UNCERTAIN,
    )


def expected_next_rt_pnl_from_parts(*, lifecycle_ev: float, p_fill: float) -> float:
    return expected_next_rt_pnl(lifecycle_ev=lifecycle_ev, p_fill=p_fill)
