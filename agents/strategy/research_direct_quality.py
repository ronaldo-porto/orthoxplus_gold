# SPDX-License-Identifier: MIT
"""Maker lifecycle learning for Strategy1-Direct A1.5.1.

A1.5.1 keeps the A1.4 principle that Taker-exit frequency is not intrinsically
bad, but fixes the learned target: downside is learned from *net realized bps*
(including fees/partial reductions) rather than gross entry-to-final-price drift.
A Kappa-3-like cubic downside proxy increases the penalty for rare large losses
without turning every Taker exit into a veto.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

DIRECT_QUALITY_VERSION = "direct_maker_quality_v4_16_2_a1_5_1"

DRIFT_MAX_PENALTY = 0.015
PRODUCTIVITY_MAX_PENALTY = 0.020
TOTAL_MAX_PENALTY = 0.030
DRIFT_SCALE_BPS = 10.0
PNL_MEAN_SCALE = 0.10
EWMA_ALPHA = 0.25

COLD_START_TAKER_RATE = 0.55
COLD_START_PRIOR_STRENGTH = 4.0
COLD_START_TAKER_SHORTFALL_BPS = 3.0
SHORTFALL_PRIOR_STRENGTH = 1.0
DOWNSIDE_CUBIC_WEIGHT = 0.55
MIGRATED_QUALITY_INITIAL_WEIGHT = 0.20
MIGRATED_QUALITY_FULL_WEIGHT_SAMPLES = 8
MIGRATED_QUALITY_GLOBAL_FULL_WEIGHT_SAMPLES = 64


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
    # A1.4 compatibility fields.  In A1.5 these are still persisted, but the
    # economics authority reads the explicit net fields below.
    gross_bps_ewma: float = 0.0
    taker_gross_bps_ewma: float = 0.0
    taker_shortfall_bps_ewma: float = 0.0
    taker_negative_count: int = 0
    # A1.5 net-realized learning.
    net_bps_ewma: float = 0.0
    taker_net_bps_ewma: float = 0.0
    taker_net_shortfall_bps_ewma: float = 0.0
    taker_net_shortfall_cube_ewma: float = 0.0

    def observe(
        self,
        *,
        exit_is_taker: bool,
        net_bps: float | None = None,
        gross_bps: float | None = None,
    ) -> None:
        # Backward-compatible call shape: if only gross is supplied, use it as
        # the net initializer.  Runtime A1.5 supplies actual net realized bps.
        gross = _finite(gross_bps if gross_bps is not None else net_bps)
        net = _finite(net_bps if net_bps is not None else gross_bps)
        self.count += 1
        if net > 0.0:
            self.positive_count += 1
        elif net < 0.0:
            self.negative_count += 1

        if self.count == 1:
            self.gross_bps_ewma = gross
            self.net_bps_ewma = net
        else:
            self.gross_bps_ewma = (1.0 - EWMA_ALPHA) * self.gross_bps_ewma + EWMA_ALPHA * gross
            self.net_bps_ewma = (1.0 - EWMA_ALPHA) * self.net_bps_ewma + EWMA_ALPHA * net

        if exit_is_taker:
            self.taker_exit_count += 1
            gross_shortfall = max(0.0, -gross)
            net_shortfall = max(0.0, -net)
            net_cube = net_shortfall ** 3
            if net < 0.0:
                self.taker_negative_count += 1
            if self.taker_exit_count == 1:
                self.taker_gross_bps_ewma = gross
                self.taker_shortfall_bps_ewma = gross_shortfall
                self.taker_net_bps_ewma = net
                self.taker_net_shortfall_bps_ewma = net_shortfall
                self.taker_net_shortfall_cube_ewma = net_cube
            else:
                self.taker_gross_bps_ewma = (
                    (1.0 - EWMA_ALPHA) * self.taker_gross_bps_ewma + EWMA_ALPHA * gross
                )
                self.taker_shortfall_bps_ewma = (
                    (1.0 - EWMA_ALPHA) * self.taker_shortfall_bps_ewma
                    + EWMA_ALPHA * gross_shortfall
                )
                self.taker_net_bps_ewma = (
                    (1.0 - EWMA_ALPHA) * self.taker_net_bps_ewma + EWMA_ALPHA * net
                )
                self.taker_net_shortfall_bps_ewma = (
                    (1.0 - EWMA_ALPHA) * self.taker_net_shortfall_bps_ewma
                    + EWMA_ALPHA * net_shortfall
                )
                self.taker_net_shortfall_cube_ewma = (
                    (1.0 - EWMA_ALPHA) * self.taker_net_shortfall_cube_ewma
                    + EWMA_ALPHA * net_cube
                )
        else:
            self.maker_exit_count += 1

    @property
    def taker_exit_rate(self) -> float:
        return 0.0 if self.count <= 0 else self.taker_exit_count / float(self.count)

    @property
    def taker_loss_rate(self) -> float:
        return 0.0 if self.taker_exit_count <= 0 else self.taker_negative_count / float(self.taker_exit_count)

    @property
    def win_rate(self) -> float:
        return 0.0 if self.count <= 0 else self.positive_count / float(self.count)

    @property
    def taker_downside_lpm3_bps(self) -> float:
        return max(0.0, _finite(self.taker_net_shortfall_cube_ewma)) ** (1.0 / 3.0)

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
            "net_bps_ewma": float(self.net_bps_ewma),
            "taker_net_bps_ewma": float(self.taker_net_bps_ewma),
            "taker_net_shortfall_bps_ewma": float(self.taker_net_shortfall_bps_ewma),
            "taker_net_shortfall_cube_ewma": float(self.taker_net_shortfall_cube_ewma),
        }

    @classmethod
    def from_state(cls, raw: Any) -> "MakerLifecycleStats":
        if not isinstance(raw, dict):
            return cls()
        taker_exit_count = max(0, int(_finite(raw.get("taker_exit_count", 0))))
        gross = _finite(raw.get("gross_bps_ewma", 0.0))
        taker_gross = _finite(raw.get("taker_gross_bps_ewma", 0.0))
        gross_shortfall = max(0.0, _finite(raw.get("taker_shortfall_bps_ewma", max(0.0, -taker_gross))))
        # Migrate A1.4 gross state conservatively into the A1.5 net fields.
        net = _finite(raw.get("net_bps_ewma", gross))
        taker_net = _finite(raw.get("taker_net_bps_ewma", taker_gross))
        net_shortfall = max(0.0, _finite(raw.get("taker_net_shortfall_bps_ewma", gross_shortfall)))
        net_cube = max(0.0, _finite(raw.get("taker_net_shortfall_cube_ewma", net_shortfall ** 3)))
        if "taker_negative_count" in raw:
            neg = max(0, int(_finite(raw.get("taker_negative_count", 0))))
        else:
            neg = 1 if taker_exit_count > 0 and taker_net < 0.0 else 0
        return cls(
            count=max(0, int(_finite(raw.get("count", 0)))),
            maker_exit_count=max(0, int(_finite(raw.get("maker_exit_count", 0)))),
            taker_exit_count=taker_exit_count,
            positive_count=max(0, int(_finite(raw.get("positive_count", 0)))),
            negative_count=max(0, int(_finite(raw.get("negative_count", 0)))),
            gross_bps_ewma=gross,
            taker_gross_bps_ewma=taker_gross,
            taker_shortfall_bps_ewma=gross_shortfall,
            taker_negative_count=neg,
            net_bps_ewma=net,
            taker_net_bps_ewma=taker_net,
            taker_net_shortfall_bps_ewma=net_shortfall,
            taker_net_shortfall_cube_ewma=net_cube,
        )


@dataclass(frozen=True)
class MakerRealizationCostEstimate:
    lifecycle_samples: int
    taker_exit_samples: int
    taker_exit_rate: float
    effective_taker_exit_rate: float
    prior_taker_exit_rate: float
    conditional_shortfall_bps: float
    downside_lpm3_bps: float
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
            "maker_taker_downside_lpm3_bps": self.downside_lpm3_bps,
            "maker_expected_negative_shortfall_bps": self.expected_negative_shortfall_bps,
            "maker_expected_taker_fee_bps": self.expected_taker_fee_bps,
            "maker_holding_risk_bps": self.holding_risk_bps,
            "maker_learned_lifecycle_cost_bps": self.total_cost_bps,
            "maker_taker_loss_rate": self.taker_loss_rate,
        }


def _effective_taker_rate(*, stats: MakerLifecycleStats, global_stats: MakerLifecycleStats,
                          prior_taker_exit_rate: float, prior_strength: float) -> tuple[float, float]:
    p0 = _clip01(prior_taker_exit_rate)
    g_n = max(0, int(global_stats.count or 0))
    if g_n > 0:
        global_conf = min(1.0, g_n / 16.0)
        p0 = (1.0 - global_conf) * p0 + global_conf * _clip01(global_stats.taker_exit_rate)
    strength = max(0.0, _finite(prior_strength, COLD_START_PRIOR_STRENGTH))
    n = max(0, int(stats.count or 0))
    taker_n = max(0, int(stats.taker_exit_count or 0))
    effective = (strength * p0 + float(taker_n)) / max(1e-12, strength + float(n))
    return _clip01(effective), _clip01(p0)


def maker_realization_cost_estimate(
    *, stats: MakerLifecycleStats | None, global_stats: MakerLifecycleStats | None = None,
    taker_fee_bps: float = 0.0, holding_risk_bps: float = 0.50,
    prior_taker_exit_rate: float = COLD_START_TAKER_RATE,
    prior_strength: float = COLD_START_PRIOR_STRENGTH,
    prior_shortfall_bps: float = COLD_START_TAKER_SHORTFALL_BPS,
    shortfall_prior_strength: float = SHORTFALL_PRIOR_STRENGTH,
    authority_scale: float = 1.0,
) -> MakerRealizationCostEstimate:
    s = stats or MakerLifecycleStats()
    g = global_stats or MakerLifecycleStats()
    effective_rate, p0 = _effective_taker_rate(
        stats=s, global_stats=g, prior_taker_exit_rate=prior_taker_exit_rate,
        prior_strength=prior_strength,
    )
    conditional_prior = max(0.0, _finite(prior_shortfall_bps, COLD_START_TAKER_SHORTFALL_BPS))
    global_lpm3 = g.taker_downside_lpm3_bps
    g_taker_n = max(0, int(g.taker_exit_count or 0))
    if g_taker_n > 0:
        global_conf = min(1.0, g_taker_n / 8.0)
        global_severity = (
            (1.0 - DOWNSIDE_CUBIC_WEIGHT) * max(0.0, g.taker_net_shortfall_bps_ewma)
            + DOWNSIDE_CUBIC_WEIGHT * global_lpm3
        )
        conditional_prior = (1.0 - global_conf) * conditional_prior + global_conf * global_severity

    book_taker_n = max(0, int(s.taker_exit_count or 0))
    book_severity = (
        (1.0 - DOWNSIDE_CUBIC_WEIGHT) * max(0.0, s.taker_net_shortfall_bps_ewma)
        + DOWNSIDE_CUBIC_WEIGHT * s.taker_downside_lpm3_bps
    )
    short_strength = max(0.0, _finite(shortfall_prior_strength, SHORTFALL_PRIOR_STRENGTH))
    conditional_shortfall = (
        short_strength * conditional_prior + float(book_taker_n) * book_severity
    ) / max(1e-12, short_strength + float(book_taker_n))

    authority = _clip01(authority_scale)
    expected_shortfall = authority * effective_rate * conditional_shortfall
    expected_taker_fee = effective_rate * max(0.0, _finite(taker_fee_bps))
    holding = max(0.0, _finite(holding_risk_bps))
    total = expected_shortfall + expected_taker_fee + holding
    return MakerRealizationCostEstimate(
        lifecycle_samples=max(0, int(s.count or 0)), taker_exit_samples=book_taker_n,
        taker_exit_rate=float(s.taker_exit_rate), effective_taker_exit_rate=float(effective_rate),
        prior_taker_exit_rate=float(p0), conditional_shortfall_bps=float(conditional_shortfall),
        downside_lpm3_bps=float(s.taker_downside_lpm3_bps),
        expected_negative_shortfall_bps=float(expected_shortfall),
        expected_taker_fee_bps=float(expected_taker_fee), holding_risk_bps=float(holding),
        total_cost_bps=float(total), taker_loss_rate=float(s.taker_loss_rate),
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
    net_bps_ewma: float = 0.0
    taker_net_bps_ewma: float = 0.0
    taker_net_shortfall_bps_ewma: float = 0.0
    taker_downside_lpm3_bps: float = 0.0

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
            "maker_net_bps_ewma": self.net_bps_ewma,
            "maker_taker_net_bps_ewma": self.taker_net_bps_ewma,
            "maker_taker_net_shortfall_bps_ewma": self.taker_net_shortfall_bps_ewma,
            "maker_taker_downside_lpm3_bps": self.taker_downside_lpm3_bps,
            "maker_taker_loss_rate": self.taker_loss_rate,
            "rolling_samples": self.rolling_samples,
            "rolling_loss_rate": self.rolling_loss_rate,
            "rolling_realized_mean": self.rolling_realized_mean,
        }


def maker_quality_adjustment(
    *, stats: MakerLifecycleStats | None, global_stats: MakerLifecycleStats | None = None,
    rolling_samples: int = 0, rolling_loss_rate: float = 0.0,
    rolling_realized_mean: float = 0.0,
    prior_taker_exit_rate: float = COLD_START_TAKER_RATE,
    prior_strength: float = COLD_START_PRIOR_STRENGTH,
    authority_scale: float = 1.0,
) -> MakerQualityAdjustment:
    s = stats or MakerLifecycleStats()
    g = global_stats or MakerLifecycleStats()
    effective_rate, p0 = _effective_taker_rate(
        stats=s, global_stats=g, prior_taker_exit_rate=prior_taker_exit_rate,
        prior_strength=prior_strength,
    )
    n = max(0, int(s.count or 0))
    adverse_bps = max(0.0, -_finite(s.net_bps_ewma))
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
    authority = _clip01(authority_scale)
    drift_penalty *= authority
    productivity_penalty *= authority
    total = min(TOTAL_MAX_PENALTY, max(0.0, drift_penalty + productivity_penalty))
    return MakerQualityAdjustment(
        realization_drift_penalty=float(drift_penalty), productivity_penalty=float(productivity_penalty),
        total_penalty=float(total), lifecycle_samples=n, taker_exit_rate=float(s.taker_exit_rate),
        effective_taker_exit_rate=float(effective_rate), prior_taker_exit_rate=float(p0),
        gross_bps_ewma=float(s.gross_bps_ewma), taker_gross_bps_ewma=float(s.taker_gross_bps_ewma),
        taker_shortfall_bps_ewma=float(s.taker_shortfall_bps_ewma), taker_loss_rate=float(s.taker_loss_rate),
        rolling_samples=roll_n, rolling_loss_rate=float(loss_rate), rolling_realized_mean=float(mean_pnl),
        net_bps_ewma=float(s.net_bps_ewma), taker_net_bps_ewma=float(s.taker_net_bps_ewma),
        taker_net_shortfall_bps_ewma=float(s.taker_net_shortfall_bps_ewma),
        taker_downside_lpm3_bps=float(s.taker_downside_lpm3_bps),
    )
