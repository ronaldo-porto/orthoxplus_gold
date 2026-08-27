# SPDX-License-Identifier: MIT
"""V4.13 simplified Kappa productivity scheduler primitives.

The Research agent should spend scarce deep-analysis/execution capacity on books
that create useful realized Kappa observations efficiently.  This module is
intentionally small: it converts cheap per-book execution statistics into one
Kappa state, one productivity score, and one scheduler tier.

It does *not* replace FIFO accounting, inventory liveness, contract safety, or
validator Kappa math.  Those remain authoritative utilities.  The only job here
is prioritization.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

KAPPA_PRODUCTIVITY_VERSION = "simplified_kappa_productivity_v4_13_7"

STATE_NEW = "NEW"
STATE_BUILDING = "BUILDING"
STATE_QUALIFIED = "QUALIFIED"
STATE_CORE = "CORE"

PHASE_BOOTSTRAP = "BOOTSTRAP"
PHASE_BALANCED = "BALANCED"
PHASE_DENSITY = "DENSITY"

TIER_PRODUCTIVE = "PRODUCTIVE"
TIER_UNKNOWN = "UNKNOWN"
TIER_INEFFICIENT = "INEFFICIENT"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def scheduler_phase(kappa_eligible_books: int) -> str:
    n = max(0, int(kappa_eligible_books or 0))
    if n < 41:
        return PHASE_BOOTSTRAP
    if n < 80:
        return PHASE_BALANCED
    return PHASE_DENSITY


def phase_weights(phase: str) -> tuple[float, float, float]:
    """Return (breadth, completion, density) allocation weights."""
    token = str(phase or PHASE_BOOTSTRAP).upper()
    if token == PHASE_DENSITY:
        return 0.15, 0.20, 0.65
    if token == PHASE_BALANCED:
        return 0.30, 0.35, 0.35
    return 0.60, 0.25, 0.15


def kappa_state(*, observations: int, core: bool = False) -> str:
    obs = max(0, int(observations or 0))
    if core and obs >= 3:
        return STATE_CORE
    if obs >= 3:
        return STATE_QUALIFIED
    if obs > 0:
        return STATE_BUILDING
    return STATE_NEW


@dataclass(frozen=True)
class ProductivitySnapshot:
    book_id: int
    observations: int
    round_trips: int
    maker_quotes: int
    maker_fills: int
    contract_rejects: int
    realized_pnl: float
    positive_count: int
    negative_count: int
    maker_fee_bps: float
    fill_rate_hint: float = 0.0
    raw_kappa: float | None = None
    ticks_since_last_rt: int | None = None
    fresh_round_trips: int = 0
    fresh_positive_round_trips: int = 0
    fresh_negative_round_trips: int = 0

    @property
    def placements_per_rt(self) -> float:
        # Maker quote burden is a V4.13+ runtime ledger, so compare it only with
        # fresh nonzero RTs observed by the same ledger. Mixing restored lifetime
        # RTs with fresh quotes can make a newly-stuck book look artificially
        # efficient after a restart.
        fresh = max(0, int(self.fresh_round_trips or 0))
        if fresh <= 0:
            return float(max(0, self.maker_quotes))
        return float(max(0, self.maker_quotes)) / float(fresh)

    @property
    def maker_fill_conversion(self) -> float:
        if self.maker_quotes <= 0:
            return _clip01(self.fill_rate_hint)
        empirical = float(max(0, self.maker_fills)) / float(max(1, self.maker_quotes))
        # Keep a small prior from the inherited fill model when empirical data is sparse.
        n = min(12, max(0, self.maker_quotes))
        w = n / 12.0
        return _clip01(w * empirical + (1.0 - w) * _clip01(self.fill_rate_hint))

    @property
    def contract_reject_rate(self) -> float:
        return float(max(0, self.contract_rejects)) / float(max(1, self.maker_quotes))

    @property
    def loss_rate(self) -> float:
        n = max(0, self.positive_count) + max(0, self.negative_count)
        if n <= 0:
            return 0.0
        return float(max(0, self.negative_count)) / float(n)

    @property
    def raw_kappa_norm(self) -> float:
        if self.raw_kappa is None:
            return 0.5
        value = _finite(self.raw_kappa, 0.0)
        return _clip01((max(-2.5, min(2.5, value)) + 2.5) / 5.0)

    @property
    def rebate_score(self) -> float:
        # -15 bps or better reaches 1.0. Positive Maker fees contribute no bonus.
        return _clip01(max(0.0, -_finite(self.maker_fee_bps)) / 15.0)

    @property
    def rt_efficiency(self) -> float:
        if self.round_trips <= 0:
            # Unknown/new books must retain exploration value rather than becoming
            # permanently dominated by already-sampled books.
            return 0.45 if self.maker_quotes < 12 else 0.20
        if self.maker_quotes <= 0:
            # Legacy/restored sessions can have historical RT counts without the
            # V4.13 quote-burden ledger.  Treat that evidence as unknown rather
            # than falsely perfect (0 placements/RT).
            return 0.45
        # <=12 placements/RT is excellent; 25 is acceptable; 100 is poor.
        return _clip01(12.0 / max(12.0, self.placements_per_rt))

    @property
    def pnl_quality(self) -> float:
        if self.positive_count + self.negative_count <= 0:
            return 0.50
        sign = 1.0 if _finite(self.realized_pnl) >= 0.0 else 0.0
        positive_ratio = float(max(0, self.positive_count)) / float(
            max(1, self.positive_count + self.negative_count)
        )
        return _clip01(0.55 * positive_ratio + 0.45 * sign)

    @property
    def freshness_score(self) -> float:
        age = self.ticks_since_last_rt
        if age is None:
            return 0.50
        age = max(0, int(age))
        # A productive book should be recycled, but the bonus gently decays so
        # neglected books rotate back into consideration.
        return _clip01(1.0 - min(age, 300) / 300.0)

    @property
    def execution_tier(self) -> str:
        # V4.13.1: hard order-sink evidence must be checked *before* the sparse-
        # evidence UNKNOWN gate.  V4.13 left a 70-quote/1-fill/0-fresh-RT book
        # UNKNOWN forever because it had fewer than two fills.
        fresh = max(0, int(self.fresh_round_trips or 0))
        if self.maker_quotes >= 20 and (
            fresh <= 0 or self.maker_fill_conversion < 0.05
        ):
            return TIER_INEFFICIENT
        if self.maker_quotes >= 12 and self.contract_reject_rate > 0.025:
            return TIER_INEFFICIENT
        if fresh > 0 and self.placements_per_rt > 45.0:
            return TIER_INEFFICIENT

        # Require coherent fresh execution evidence before PRODUCTIVE/CORE.
        if self.maker_quotes < 6 or self.maker_fills < 2 or fresh <= 0:
            return TIER_UNKNOWN
        return TIER_PRODUCTIVE

    @property
    def score(self) -> float:
        reject_penalty = _clip01(self.contract_reject_rate / 0.02)
        loss_penalty = _clip01(self.loss_rate / 0.50)
        score = (
            0.30 * self.rt_efficiency
            + 0.20 * self.maker_fill_conversion
            + 0.18 * self.pnl_quality
            + 0.10 * self.rebate_score
            + 0.12 * self.raw_kappa_norm
            + 0.10 * self.freshness_score
            - 0.16 * reject_penalty
            - 0.18 * loss_penalty
        )
        return max(-1.0, min(1.0, score))

    @property
    def fresh_probe_failed(self) -> bool:
        """A first fresh cycle produced loss evidence instead of bootstrap proof."""
        fresh = max(0, int(self.fresh_round_trips or 0))
        return bool(
            fresh > 0
            and int(self.fresh_positive_round_trips or 0) <= 0
            and int(self.fresh_negative_round_trips or 0) > 0
        )

    @property
    def recycling_candidate(self) -> bool:
        # Bootstrap bridge: one clean fresh Kappa-producing RT is enough to earn
        # one repeated-cycle slot. Full CORE still requires three fresh RTs.
        return bool(
            self.observations >= 3
            and self.fresh_round_trips >= 1
            and self.fresh_positive_round_trips >= 1
            and self.fresh_negative_round_trips == 0
            and self.maker_quotes >= 4
            and self.maker_fills >= 2
            and self.placements_per_rt <= 15.0
            and self.contract_reject_rate <= 0.02
            and self.maker_fill_conversion >= 0.08
            and self.score >= 0.30
        )

    @property
    def core_candidate(self) -> bool:
        fresh = max(0, int(self.fresh_round_trips or 0))
        fresh_loss_rate = float(max(0, self.fresh_negative_round_trips)) / float(max(1, fresh))
        return bool(
            self.observations >= 3
            and fresh >= 3
            and self.fresh_positive_round_trips >= 2
            and self.maker_quotes >= 6
            and self.maker_fills >= 2
            and self.realized_pnl >= -1e-12
            and fresh_loss_rate <= 0.34
            and self.placements_per_rt <= 30.0
            and self.contract_reject_rate <= 0.02
            and self.maker_fill_conversion >= 0.08
            and self.score >= 0.35
        )

    def as_log(self) -> dict[str, Any]:
        return {
            "kappa_productivity_version": KAPPA_PRODUCTIVITY_VERSION,
            "kappa_state": kappa_state(observations=self.observations, core=self.core_candidate),
            "productivity_score": float(self.score),
            "productivity_tier": self.execution_tier,
            "core_candidate": int(self.core_candidate),
            "recycling_candidate": int(self.recycling_candidate),
            "fresh_round_trips": int(self.fresh_round_trips),
            "fresh_positive_round_trips": int(self.fresh_positive_round_trips),
            "fresh_negative_round_trips": int(self.fresh_negative_round_trips),
            "placements_per_rt": float(self.placements_per_rt),
            "maker_fill_conversion": float(self.maker_fill_conversion),
            "contract_reject_rate": float(self.contract_reject_rate),
            "loss_rate": float(self.loss_rate),
            "maker_rebate_score": float(self.rebate_score),
            "ticks_since_last_rt": self.ticks_since_last_rt,
        }


def core_probe_eligible(
    snapshot: ProductivitySnapshot,
    *,
    kappa_eligible: bool,
    maker_ev: float,
    maker_ev_known: bool,
    flat_and_safe: bool,
    entry_feasible: bool,
    economics_ok: bool,
    pnl_confidence: str = "UNKNOWN",
    recent_realized_pnl: float = 0.0,
    raw_kappa: float | None = None,
) -> bool:
    """Return whether a qualified fresh-UNKNOWN book deserves one CORE_PROBE.

    Historical Kappa/PnL are vetoes only when available. Fresh evidence remains
    authoritative: a book with any fresh RT is no longer a probe candidate.
    """
    if not bool(kappa_eligible) or not bool(flat_and_safe):
        return False
    if not bool(entry_feasible) or not bool(economics_ok):
        return False
    if not bool(maker_ev_known) or _finite(maker_ev) <= 0.0:
        return False
    if str(snapshot.execution_tier or TIER_UNKNOWN).upper() != TIER_UNKNOWN:
        return False
    if max(0, int(snapshot.fresh_round_trips or 0)) != 0:
        return False
    conf = str(pnl_confidence or "UNKNOWN").upper()
    if conf != "UNKNOWN" and _finite(recent_realized_pnl) < -1e-12:
        return False
    if raw_kappa is not None and _finite(raw_kappa) < -1e-12:
        return False
    return True


def priority_for_state(
    snapshot: ProductivitySnapshot,
    *,
    phase: str,
    required_observations: int = 3,
) -> float:
    """One scheduler priority for flat-book work.

    Coverage determines *which book* should get capacity; productivity determines
    whether the book has earned repeated density work.  Alpha remains a later side
    bias, not a separate competing scheduler.
    """
    required = max(1, int(required_observations or 1))
    obs = max(0, int(snapshot.observations or 0))
    remaining = max(0, required - obs)
    breadth_w, completion_w, density_w = phase_weights(phase)

    breadth_need = 1.0 if obs == 0 else 0.0
    completion_need = _clip01(remaining / float(required)) if 0 < remaining < required else 0.0
    density_value = 0.0
    if obs >= required:
        density_value = _clip01(0.55 + 0.45 * max(0.0, snapshot.score))
        if snapshot.recycling_candidate:
            density_value = max(density_value, 0.92)
        if snapshot.core_candidate:
            density_value = 1.0

    efficiency = max(0.0, snapshot.score)
    # Known-inefficient books are not banned; they simply lose scheduler priority
    # so a Book98-like order sink cannot dominate a Book115-like RT factory.
    tier_mult = 0.35 if snapshot.execution_tier == TIER_INEFFICIENT else 1.0
    exploration_bonus = 0.12 if snapshot.execution_tier == TIER_UNKNOWN and obs == 0 else 0.0
    return tier_mult * (
        breadth_w * breadth_need
        + completion_w * completion_need
        + density_w * density_value
        + 0.30 * efficiency
        + exploration_bonus
    )


def sanitize_productivity_runtime(raw: Any) -> dict[int, dict[str, int]]:
    """Restart-safe counters. Never restores derived scores."""
    if not isinstance(raw, Mapping):
        return {}
    out: dict[int, dict[str, int]] = {}
    keys = (
        "maker_quotes", "maker_fills", "contract_rejects", "last_rt_tick",
        "fresh_round_trips", "fresh_positive_round_trips", "fresh_negative_round_trips",
    )
    for raw_book, raw_row in raw.items():
        try:
            bid = int(raw_book)
        except (TypeError, ValueError):
            continue
        if not isinstance(raw_row, Mapping):
            continue
        row: dict[str, int] = {}
        for key in keys:
            try:
                value = int(raw_row.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0
            row[key] = max(0, value)
        out[bid] = row
    return out
