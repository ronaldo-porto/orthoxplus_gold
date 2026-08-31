# SPDX-License-Identifier: MIT
"""V4.15.1 lean-authority helpers.

Centralizes two cross-cutting behaviors that were previously scattered across
scheduler/execution code:
  * short cooldowns for repeated downstream execution vetoes, so impossible
    books do not repeatedly consume score-acquisition prediction/attempt budget;
  * empirical lifecycle Taker-exit probability with Bayesian shrinkage to the
    configured prior, so entry EV prices the observed realization path.

Pure helpers; no Strategy imports.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Any

CLEAN_AUTHORITY_VERSION = "clean_authority_v4_15_1"
LIFECYCLE_TAKER_PRIOR_STRENGTH = 8.0
LIFECYCLE_TAKER_MIN_SAMPLES = 4
LIFECYCLE_TAKER_PROB_CAP = 0.90

EXECUTION_REJECT_COOLDOWNS = {
    "TTL_STALE": 4,
    "ZERO_ORDER_SIZE": 8,
    "LOW_FILL_PROBABILITY": 4,
    "NON_POSITIVE_EDGE": 6,
    "NEGATIVE_EXPECTED_PNL": 8,
    "ADVERSE_SELECTION": 6,
}

@dataclass(frozen=True)
class RejectCooldown:
    blocked: bool
    reason: str = ""
    until_tick: int = 0


def execution_reject_cooldown(
    record: dict[str, Any] | None,
    *,
    tick: int,
    enabled: bool = True,
    cooldowns: dict[str, int] | None = None,
) -> RejectCooldown:
    if not enabled or not record:
        return RejectCooldown(False)
    reason = str(record.get("reason", "") or "").upper()
    table = cooldowns or EXECUTION_REJECT_COOLDOWNS
    span = max(0, int(table.get(reason, 0) or 0))
    if span <= 0:
        return RejectCooldown(False)
    last_tick = int(record.get("tick", -10**9) or -10**9)
    until = last_tick + span
    return RejectCooldown(int(tick) < until, reason=reason, until_tick=until)


def posterior_taker_exit_probability(
    *,
    maker_exits: int,
    taker_exits: int,
    prior: float = 0.30,
    prior_strength: float = 8.0,
    min_samples: int = 4,
    floor: float | None = None,
    cap: float = 0.90,
) -> float:
    """Bayesian empirical Taker-exit probability for lifecycle entry costing."""
    m = max(0, int(maker_exits or 0))
    t = max(0, int(taker_exits or 0))
    n = m + t
    p0 = max(0.0, min(1.0, float(prior)))
    if n < max(0, int(min_samples or 0)):
        p = p0
    else:
        strength = max(0.0, float(prior_strength or 0.0))
        p = (p0 * strength + float(t)) / max(1e-12, strength + float(n))
    lo = p0 if floor is None else max(0.0, min(1.0, float(floor)))
    hi = max(lo, min(1.0, float(cap)))
    return max(lo, min(hi, p))
