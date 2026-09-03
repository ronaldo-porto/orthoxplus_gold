# SPDX-License-Identifier: MIT
"""Maker lifecycle learning for Strategy1-Direct A1.4.

A1.3 learned that Maker-origin trading could be profitable even when the final
realization used Taker.  Therefore A1.4 no longer treats Taker-exit frequency
as an economic failure by itself.

The learned lifecycle cost is now:

    P(Taker exit) * E[negative gross shortfall | Taker exit]
    + P(Taker exit) * positive Taker fee
    + holding risk

Profitable Taker exits contribute zero negative shortfall.  Book evidence is
hierarchically shrunk toward current-run global evidence and a weak cold-start
prior.  The separate quality adjustment remains bounded and uses overall Maker
lifecycle drift plus rolling realized productivity; it does not blacklist or
penalize a book merely because its exits are Taker.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

DIRECT_QUALITY_VERSION = "direct_maker_quality_v4_16_2_a1_4"

# Keep the A1.3 caps.  A1.4 changes the meaning of the learned signal rather
# than widening the authority surface.
DRIFT_MAX_PENALTY = 0.015
PRODUCTIVITY_MAX_PENALTY = 0.020
TOTAL_MAX_PENALTY = 0.030
DRIFT_SCALE_BPS = 10.0
PNL_MEAN_SCALE = 0.10
EWMA_ALPHA = 0.25

COLD_START_TAKER_RATE = 0.55
COLD_START_PRIOR_STRENGTH = 4.0
# Weak prior for loss severity conditional on a Taker exit.  It is deliberately
# much smaller than the old fixed crossing+slippage charge and is replaced by
# observed shortfall as soon as lifecycle evidence arrives.
COLD_START_TAKER_SHORTFALL_BPS = 3.0
SHORTFALL_PRIOR_STRENGTH = 1.0


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
    # EWMA of max(0, -gross_bps) over *all* Taker exits, including zero for
    # profitable Taker exits.  This directly estimates negative shortfall
    # conditional on using Taker without equating "Taker" with "bad".
    taker_shortfall_bps_ewma: float = 0.0
    taker_negative_count: int = 0

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
            shortfall = max(0.0, -gross)
            if gross < 0.0:
                self.taker_negative_count += 1
            if self.taker_exit_count == 1:
                self.taker_gross_bps_ewma = gross
                self.taker_shortfall_bps_ewma = shortfall
            else:
                self.taker_gross_bps_ewma = (
                    (1.0 - EWMA_ALPHA) * self.taker_gross_bps_ewma
                    + EWMA_ALPHA * gross
                )
                self.taker_shortfall_bps_ewma = (
                    (1.0 - EWMA_ALPHA) * self.taker_shortfall_bps_ewma
                    + EWMA_ALPHA * shortfall
                )
        else:
            self.maker_exit_count += 1

    @property
    def taker_exit_rate(self) -> float:
        return 0.0 if self.count <= 0 else self.taker_exit_count / float(self.count)

    @property
    def taker_loss_rate(self) -> float:
        return (
            0.0
            if self.taker_exit_count <= 0
            else self.taker_negative_count / float(self.taker_exit_count)
        )

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
            "taker_shortfall_bps_ewma": float(self.taker_shortfall_bps_ewma),
            "taker_negative_count": int(self.taker_negative_count),
        }

    @classmethod
    def from_state(cls, raw: Any) -> "MakerLifecycleStats":
        if not isinstance(raw, dict):
            return cls()
        taker_exit_count = max(0, int(_finite(raw.get("taker_exit_count", 0))))
        taker_gross = _finite(raw.get("taker_gross_bps_ewma", 0.0))
        # A1.3 migration: older state does not contain shortfall.  Use only the
        # adverse part of its Taker gross EWMA as a conservative initializer.
        if "taker_shortfall_bps_ewma" in raw:
            shortfall = max(0.0, _finite(raw.get("taker_shortfall_bps_ewma", 0.0)))
        else:
            shortfall = max(0.0, -taker_gross)
        if "taker_negative_count" in raw:
            taker_negative_count = max(0, int(_finite(raw.get("taker_negative_count", 0))))
        else:
            taker_negative_count = 1 if taker_exit_count > 0 and taker_gross < 0.0 else 0
        return cls(
            count=max(0, int(_finite(raw.get("count", 0)))),
            maker_exit_count=max(0, int(_finite(raw.get("maker_exit_count", 0)))),
            taker_exit_count=taker_exit_count,
            positive_count=max(0, int(_finite(raw.get("positive_count", 0)))),
            negative_count=max(0, int(_finite(raw.get("negative_count", 0)))),
            gross_bps_ewma=_finite(raw.get("gross_bps_ewma", 0.0)),
            taker_gross_bps_ewma=taker_gross,
            taker_shortfall_bps_ewma=shortfall,
            taker_negative_count=taker_negative_count,
        )


@dataclass(frozen=True)
class MakerRealizationCostEstimate:
    lifecycle_samples: int
    taker_exit_samples: int
    taker_exit_rate: float
    effective_taker_exit_rate: float
    prior_taker_exit_rate: float
    conditional_shortfall_bps: float
    expected_negative_shortfall_bps: float
    expected_taker_fee_bps: float
    holding_risk_bps: float
    total_cost_bps: float
    taker_loss_rate: float

    def as_log(self) -> dict[str, Any]:
        return {
            "maker_realization_cost_version": DIRECT_QUALITY_VERSION,
            "maker_realization_samples": self.lifecycle_samples,
            "maker_realization_taker_samples": self.taker_exit_samples,
            "maker_realization_taker_rate": self.taker_exit_rate,
            "maker_realization_effective_taker_rate": self.effective_taker_exit_rate,
            "maker_realization_prior_taker_rate": self.prior_taker_exit_rate,
            "maker_taker_conditional_shortfall_bps": self.conditional_shortfall_bps,
            "maker_expected_negative_shortfall_bps": self.expected_negative_shortfall_bps,
            "maker_expected_taker_fee_bps": self.expected_taker_fee_bps,
            "maker_holding_risk_bps": self.holding_risk_bps,
            "maker_learned_lifecycle_cost_bps": self.total_cost_bps,
            "maker_taker_loss_rate": self.taker_loss_rate,
        }


def _effective_taker_rate(
    *,
    stats: MakerLifecycleStats,
    global_stats: MakerLifecycleStats,
    prior_taker_exit_rate: float,
    prior_strength: float,
) -> tuple[float, float]:
    p0 = _clip01(prior_taker_exit_rate)
    g_n = max(0, int(global_stats.count or 0))
    if g_n > 0:
        global_conf = min(1.0, g_n / 16.0)
        p0 = (
            (1.0 - global_conf) * p0
            + global_conf * _clip01(global_stats.taker_exit_rate)
        )
    strength = max(0.0, _finite(prior_strength, COLD_START_PRIOR_STRENGTH))
    n = max(0, int(stats.count or 0))
    taker_n = max(0, int(stats.taker_exit_count or 0))
    effective = (strength * p0 + float(taker_n)) / max(1e-12, strength + float(n))
    return _clip01(effective), _clip01(p0)


def maker_realization_cost_estimate(
    *,
    stats: MakerLifecycleStats | None,
    global_stats: MakerLifecycleStats | None = None,
    taker_fee_bps: float = 0.0,
    holding_risk_bps: float = 0.50,
    prior_taker_exit_rate: float = COLD_START_TAKER_RATE,
    prior_strength: float = COLD_START_PRIOR_STRENGTH,
    prior_shortfall_bps: float = COLD_START_TAKER_SHORTFALL_BPS,
    shortfall_prior_strength: float = SHORTFALL_PRIOR_STRENGTH,
) -> MakerRealizationCostEstimate:
    """Estimate the actual downside of a future Maker lifecycle.

    Taker frequency only scales *observed negative shortfall* and positive
    Taker fees.  A profitable Taker exit contributes zero shortfall, so a book
    may use Taker frequently without being treated as intrinsically toxic.
    """
    s = stats or MakerLifecycleStats()
    g = global_stats or MakerLifecycleStats()
    effective_rate, p0 = _effective_taker_rate(
        stats=s,
        global_stats=g,
        prior_taker_exit_rate=prior_taker_exit_rate,
        prior_strength=prior_strength,
    )

    conditional_prior = max(0.0, _finite(prior_shortfall_bps, COLD_START_TAKER_SHORTFALL_BPS))
    g_taker_n = max(0, int(g.taker_exit_count or 0))
    if g_taker_n > 0:
        global_conf = min(1.0, g_taker_n / 8.0)
        conditional_prior = (
            (1.0 - global_conf) * conditional_prior
            + global_conf * max(0.0, _finite(g.taker_shortfall_bps_ewma))
        )

    book_taker_n = max(0, int(s.taker_exit_count or 0))
    short_strength = max(0.0, _finite(shortfall_prior_strength, SHORTFALL_PRIOR_STRENGTH))
    conditional_shortfall = (
        short_strength * conditional_prior
        + float(book_taker_n) * max(0.0, _finite(s.taker_shortfall_bps_ewma))
    ) / max(1e-12, short_strength + float(book_taker_n))

    expected_shortfall = effective_rate * conditional_shortfall
    expected_taker_fee = effective_rate * max(0.0, _finite(taker_fee_bps))
    holding = max(0.0, _finite(holding_risk_bps))
    total = expected_shortfall + expected_taker_fee + holding

    return MakerRealizationCostEstimate(
        lifecycle_samples=max(0, int(s.count or 0)),
        taker_exit_samples=book_taker_n,
        taker_exit_rate=float(s.taker_exit_rate),
        effective_taker_exit_rate=float(effective_rate),
        prior_taker_exit_rate=float(p0),
        conditional_shortfall_bps=float(conditional_shortfall),
        expected_negative_shortfall_bps=float(expected_shortfall),
        expected_taker_fee_bps=float(expected_taker_fee),
        holding_risk_bps=float(holding),
        total_cost_bps=float(total),
        taker_loss_rate=float(s.taker_loss_rate),
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
    taker_shortfall_bps_ewma: float
    taker_loss_rate: float
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
            "maker_taker_shortfall_bps_ewma": self.taker_shortfall_bps_ewma,
            "maker_taker_loss_rate": self.taker_loss_rate,
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
    """Return one bounded non-blacklist Maker quality adjustment.

    A1.4 explicitly removes Taker-exit frequency as a badness feature.  The
    small drift term uses overall Maker-lifecycle realized drift regardless of
    exit role.  Rolling productivity uses realized loss consistency and mean
    PnL only.  Taker shortfall is priced once in the lifecycle cost estimator.
    """
    s = stats or MakerLifecycleStats()
    g = global_stats or MakerLifecycleStats()
    effective_rate, p0 = _effective_taker_rate(
        stats=s,
        global_stats=g,
        prior_taker_exit_rate=prior_taker_exit_rate,
        prior_strength=prior_strength,
    )

    n = max(0, int(s.count or 0))
    adverse_bps = max(0.0, -_finite(s.gross_bps_ewma))
    drift_conf = min(1.0, n / 4.0)
    drift_penalty = DRIFT_MAX_PENALTY * drift_conf * math.tanh(adverse_bps / DRIFT_SCALE_BPS)

    roll_n = max(0, int(rolling_samples or 0))
    loss_rate = _clip01(rolling_loss_rate)
    mean_pnl = _finite(rolling_realized_mean)
    roll_conf = min(1.0, roll_n / 6.0)
    loss_bad = _clip01((loss_rate - 0.50) / 0.50)
    pnl_bad = math.tanh(max(0.0, -mean_pnl) / PNL_MEAN_SCALE)
    productivity_score = 0.55 * loss_bad + 0.45 * pnl_bad
    productivity_penalty = PRODUCTIVITY_MAX_PENALTY * roll_conf * productivity_score

    total = min(TOTAL_MAX_PENALTY, max(0.0, drift_penalty + productivity_penalty))
    return MakerQualityAdjustment(
        realization_drift_penalty=float(drift_penalty),
        productivity_penalty=float(productivity_penalty),
        total_penalty=float(total),
        lifecycle_samples=n,
        taker_exit_rate=float(s.taker_exit_rate),
        effective_taker_exit_rate=float(effective_rate),
        prior_taker_exit_rate=float(p0),
        gross_bps_ewma=float(s.gross_bps_ewma),
        taker_gross_bps_ewma=float(s.taker_gross_bps_ewma),
        taker_shortfall_bps_ewma=float(s.taker_shortfall_bps_ewma),
        taker_loss_rate=float(s.taker_loss_rate),
        rolling_samples=roll_n,
        rolling_loss_rate=float(loss_rate),
        rolling_realized_mean=float(mean_pnl),
    )
