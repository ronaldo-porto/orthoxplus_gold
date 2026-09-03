# SPDX-License-Identifier: MIT
"""Bounded Maker lifecycle quality learning for Strategy1-Direct A1.3.

The Direct strategy intentionally keeps one economic authority.  This module
therefore does not create a toxicity/blacklist lane.  It learns two small,
bounded deductions that can be applied to Maker economics/ranking:

* realization drift: actual gross price drift observed from a Maker fill until
  the position is flat, emphasizing Maker entries that ultimately require a
  Taker exit; and
* productivity: restart-safe rolling PnL consistency plus the learned share of
  Maker lifecycles that require a Taker exit.

Sparse/new books receive little or no penalty.  Evidence decays through EWMAs
and every deduction is capped, so a bad early fill cannot permanently kill a
book or recreate the A1 dead-gate failure.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

DIRECT_QUALITY_VERSION = "direct_maker_quality_v4_16_2_a1_3"

DRIFT_MAX_PENALTY = 0.030
PRODUCTIVITY_MAX_PENALTY = 0.020
TOTAL_MAX_PENALTY = 0.040
DRIFT_SCALE_BPS = 10.0
PNL_MEAN_SCALE = 0.10
EWMA_ALPHA = 0.25
# Agent-68 A1.2 cold-start history: zero-sample books were the worst cohort,
# while the realized Maker->Taker share was ~57%.  Use a weak hierarchical
# prior rather than pretending a new/restarted book has zero exit hazard.
COLD_START_TAKER_RATE = 0.55
COLD_START_PRIOR_STRENGTH = 4.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _clip01(value: Any) -> float:
    return max(0.0, min(1.0, _finite(value)))


@dataclass
class MakerLifecycleStats:
    count: int = 0
    maker_exit_count: int = 0
    taker_exit_count: int = 0
    positive_count: int = 0
    negative_count: int = 0
    gross_bps_ewma: float = 0.0
    taker_gross_bps_ewma: float = 0.0

    def observe(self, *, gross_bps: float, exit_is_taker: bool) -> None:
        gross = _finite(gross_bps)
        self.count += 1
        if gross > 0.0:
            self.positive_count += 1
        elif gross < 0.0:
            self.negative_count += 1
        if self.count == 1:
            self.gross_bps_ewma = gross
        else:
            self.gross_bps_ewma = (
                (1.0 - EWMA_ALPHA) * self.gross_bps_ewma + EWMA_ALPHA * gross
            )
        if exit_is_taker:
            self.taker_exit_count += 1
            if self.taker_exit_count == 1:
                self.taker_gross_bps_ewma = gross
            else:
                self.taker_gross_bps_ewma = (
                    (1.0 - EWMA_ALPHA) * self.taker_gross_bps_ewma
                    + EWMA_ALPHA * gross
                )
        else:
            self.maker_exit_count += 1

    @property
    def taker_exit_rate(self) -> float:
        return 0.0 if self.count <= 0 else self.taker_exit_count / float(self.count)

    @property
    def win_rate(self) -> float:
        return 0.0 if self.count <= 0 else self.positive_count / float(self.count)

    def as_state(self) -> dict[str, float | int]:
        return {
            "count": int(self.count),
            "maker_exit_count": int(self.maker_exit_count),
            "taker_exit_count": int(self.taker_exit_count),
            "positive_count": int(self.positive_count),
            "negative_count": int(self.negative_count),
            "gross_bps_ewma": float(self.gross_bps_ewma),
            "taker_gross_bps_ewma": float(self.taker_gross_bps_ewma),
        }

    @classmethod
    def from_state(cls, raw: Any) -> "MakerLifecycleStats":
        if not isinstance(raw, dict):
            return cls()
        return cls(
            count=max(0, int(_finite(raw.get("count", 0)))),
            maker_exit_count=max(0, int(_finite(raw.get("maker_exit_count", 0)))),
            taker_exit_count=max(0, int(_finite(raw.get("taker_exit_count", 0)))),
            positive_count=max(0, int(_finite(raw.get("positive_count", 0)))),
            negative_count=max(0, int(_finite(raw.get("negative_count", 0)))),
            gross_bps_ewma=_finite(raw.get("gross_bps_ewma", 0.0)),
            taker_gross_bps_ewma=_finite(raw.get("taker_gross_bps_ewma", 0.0)),
        )


@dataclass(frozen=True)
class MakerQualityAdjustment:
    realization_drift_penalty: float
    productivity_penalty: float
    total_penalty: float
    lifecycle_samples: int
    taker_exit_rate: float
    effective_taker_exit_rate: float
    prior_taker_exit_rate: float
    gross_bps_ewma: float
    taker_gross_bps_ewma: float
    rolling_samples: int
    rolling_loss_rate: float
    rolling_realized_mean: float

    def as_log(self) -> dict[str, Any]:
        return {
            "direct_quality_version": DIRECT_QUALITY_VERSION,
            "maker_realization_drift_penalty": self.realization_drift_penalty,
            "maker_productivity_penalty": self.productivity_penalty,
            "maker_quality_penalty": self.total_penalty,
            "maker_lifecycle_samples": self.lifecycle_samples,
            "maker_taker_exit_rate": self.taker_exit_rate,
            "maker_effective_taker_exit_rate": self.effective_taker_exit_rate,
            "maker_prior_taker_exit_rate": self.prior_taker_exit_rate,
            "maker_gross_bps_ewma": self.gross_bps_ewma,
            "maker_taker_gross_bps_ewma": self.taker_gross_bps_ewma,
            "rolling_samples": self.rolling_samples,
            "rolling_loss_rate": self.rolling_loss_rate,
            "rolling_realized_mean": self.rolling_realized_mean,
        }


def maker_quality_adjustment(
    *,
    stats: MakerLifecycleStats | None,
    global_stats: MakerLifecycleStats | None = None,
    rolling_samples: int = 0,
    rolling_loss_rate: float = 0.0,
    rolling_realized_mean: float = 0.0,
    prior_taker_exit_rate: float = COLD_START_TAKER_RATE,
    prior_strength: float = COLD_START_PRIOR_STRENGTH,
) -> MakerQualityAdjustment:
    """Return one bounded Maker-quality deduction.

    Realization drift uses *actual completed Maker lifecycles*.  Taker-exit
    outcomes are emphasized because Agent-68 A1.1 showed that Maker->Taker was
    the dominant losing path.  The productivity component is deliberately
    small and confidence weighted; it downranks repeated bad books rather than
    blacklisting them.
    """
    s = stats or MakerLifecycleStats()
    g = global_stats or MakerLifecycleStats()
    n = max(0, int(s.count or 0))
    taker_n = max(0, int(s.taker_exit_count or 0))

    # Hierarchical Taker-exit prior.  Current-run global evidence may move the
    # cold-start prior, while per-book evidence gradually takes over.
    p0 = _clip01(prior_taker_exit_rate)
    g_n = max(0, int(g.count or 0))
    if g_n > 0:
        global_conf = min(1.0, g_n / 16.0)
        p0 = (1.0 - global_conf) * p0 + global_conf * _clip01(g.taker_exit_rate)
    strength = max(0.0, _finite(prior_strength, COLD_START_PRIOR_STRENGTH))
    effective_taker_rate = (strength * p0 + float(taker_n)) / max(1e-12, strength + float(n))

    # Prefer the realized drift of Maker->Taker lifecycles once available.
    drift_bps = _finite(s.taker_gross_bps_ewma if taker_n > 0 else s.gross_bps_ewma)
    adverse_bps = max(0.0, -drift_bps)
    drift_samples = taker_n if taker_n > 0 else n
    drift_conf = min(1.0, drift_samples / 4.0)
    # All-Maker fallback is intentionally half strength until a Taker exit has
    # actually demonstrated the failure mode we are trying to learn.
    drift_strength = 1.0 if taker_n > 0 else 0.5
    drift_penalty = (
        DRIFT_MAX_PENALTY
        * drift_strength
        * drift_conf
        * math.tanh(adverse_bps / DRIFT_SCALE_BPS)
    )

    roll_n = max(0, int(rolling_samples or 0))
    loss_rate = _clip01(rolling_loss_rate)
    mean_pnl = _finite(rolling_realized_mean)
    roll_conf = min(1.0, roll_n / 6.0)
    loss_bad = _clip01((loss_rate - 0.50) / 0.50)
    pnl_bad = math.tanh(max(0.0, -mean_pnl) / PNL_MEAN_SCALE)

    # The prior itself is weak evidence; book evidence increases confidence.
    lifecycle_conf = min(1.0, (strength + n) / 10.0)
    taker_bad = _clip01((effective_taker_rate - 0.50) / 0.50) * lifecycle_conf
    productivity_score = 0.45 * loss_bad + 0.35 * pnl_bad + 0.20 * taker_bad
    productivity_penalty = PRODUCTIVITY_MAX_PENALTY * roll_conf * productivity_score

    total = min(TOTAL_MAX_PENALTY, max(0.0, drift_penalty + productivity_penalty))
    return MakerQualityAdjustment(
        realization_drift_penalty=float(drift_penalty),
        productivity_penalty=float(productivity_penalty),
        total_penalty=float(total),
        lifecycle_samples=n,
        taker_exit_rate=float(s.taker_exit_rate),
        effective_taker_exit_rate=float(effective_taker_rate),
        prior_taker_exit_rate=float(p0),
        gross_bps_ewma=float(s.gross_bps_ewma),
        taker_gross_bps_ewma=float(s.taker_gross_bps_ewma),
        rolling_samples=roll_n,
        rolling_loss_rate=float(loss_rate),
        rolling_realized_mean=float(mean_pnl),
    )
