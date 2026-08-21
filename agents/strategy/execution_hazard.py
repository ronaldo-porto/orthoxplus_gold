# SPDX-License-Identifier: MIT
"""Production frozen fill-hazard model.

Promoted from verified Research V4.3. BaseStrategy uses frozen priors, a
minimum-sample usable gate, and shrinkage. Extra-feature logit adaptation is
off by default so the production path is not uncontrolled online learning.
No Strategy1 / Research runtime imports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Literal

AGE_EDGES_MS = (100, 250, 500, 1000)
N_AGE_BINS = len(AGE_EDGES_MS) + 1
CAL_BUCKETS = (
    ("0.00_0.05", 0.00, 0.05),
    ("0.05_0.10", 0.05, 0.10),
    ("0.10_0.20", 0.10, 0.20),
    ("0.20_0.40", 0.20, 0.40),
    ("0.40_1.00", 0.40, 1.01),
)
DIST_EDGES_BPS = (0.5, 2.0)
SPREAD_EDGES = (5.0, 12.0)
VOL_EDGES = (0.002, 0.006)
TRADE_EDGES = (0.2, 1.0)
IMB_EDGES = (-0.15, 0.15)
TTL_EDGES_MS = (200.0, 600.0)
REGIME_GROUPS = {
    "QUIET": "QUIET",
    "NORMAL": "NORMAL",
    "LIQUID": "NORMAL",
    "TREND_UP": "TREND",
    "TREND_DOWN": "TREND",
    "STRESSED": "STRESS",
    "TOXIC": "STRESS",
    "MIXED": "NORMAL",
    "BROAD_LIQUID": "NORMAL",
    "TRENDING_UP": "TREND",
    "TRENDING_DOWN": "TREND",
    "CHOP": "QUIET",
    "DISPERSED": "NORMAL",
}


FROZEN_MIN_SAMPLES = 12
FROZEN_PRIOR_STRENGTH = 8.0
FROZEN_PRIOR_ANY = 0.12
FROZEN_PRIOR_ACTIONABLE_GIVEN_FILL = 0.55
FROZEN_P_MIN = 0.01
FROZEN_P_MAX = 0.95
FROZEN_FEATURE_LOGIT_WEIGHT = 0.0


def _clip(p: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(p)))


def _logit(p: float) -> float:
    x = _clip(p, 1e-6, 1.0 - 1e-6)
    return math.log(x / (1.0 - x))


def _sigmoid(z: float) -> float:
    if z >= 30.0:
        return 1.0
    if z <= -30.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def _bucket(value: float | None, edges: tuple[float, ...]) -> int:
    if value is None:
        return 1
    x = float(value)
    for i, edge in enumerate(edges):
        if x < edge:
            return i
    return len(edges)


def age_bin(age_ms: float) -> int:
    age = max(0.0, float(age_ms))
    for i, edge in enumerate(AGE_EDGES_MS):
        if age < float(edge):
            return i
    return len(AGE_EDGES_MS)


def bins_for_ttl(ttl_ms: float) -> range:
    """Bins whose left edge is strictly before TTL (P(T < TTL) via discrete hazard)."""
    ttl = max(0.0, float(ttl_ms))
    last = 0
    left = 0.0
    for i, edge in enumerate(AGE_EDGES_MS):
        if left < ttl:
            last = i
        left = float(edge)
    if left < ttl:
        last = len(AGE_EDGES_MS)
    return range(0, last + 1)


def cal_bucket(p: float) -> str:
    x = max(0.0, min(1.0, float(p)))
    for name, lo, hi in CAL_BUCKETS:
        if lo <= x < hi:
            return name
    return CAL_BUCKETS[-1][0]


def outcome_from_fill_class(fill_class: str | None) -> Literal["actionable", "dust", "other"]:
    token = str(fill_class or "").upper()
    if token in {"DUST_PARTIAL", "CROSS_DUST", "DUST"}:
        return "dust"
    if token in {"FULL", "ACTIONABLE_PARTIAL", "FLAT", "ACTIONABLE"}:
        return "actionable"
    return "other"


@dataclass
class HazardFeatures:
    side: str
    dist_bucket: int
    spread_bucket: int
    vol_bucket: int
    trade_bucket: int
    imb_bucket: int
    regime_group: str
    ttl_bucket: int
    ttl_ms: float

    @classmethod
    def from_snapshot(
        cls,
        *,
        side: str,
        distance_from_touch_bps: float | None,
        spread_bps: float | None,
        volatility: float | None,
        trade_rate: float | None,
        imbalance: float | None,
        market_regime: str | None,
        ttl_ms: float | None,
    ) -> "HazardFeatures":
        ttl = 500.0 if ttl_ms is None else max(1.0, float(ttl_ms))
        regime = REGIME_GROUPS.get(str(market_regime or "NORMAL").upper(), "NORMAL")
        return cls(
            side="buy" if str(side).lower() in {"buy", "bid", "b", "0"} else "sell",
            dist_bucket=_bucket(distance_from_touch_bps, DIST_EDGES_BPS),
            spread_bucket=_bucket(spread_bps, SPREAD_EDGES),
            vol_bucket=_bucket(volatility, VOL_EDGES),
            trade_bucket=_bucket(trade_rate, TRADE_EDGES),
            imb_bucket=_bucket(imbalance, IMB_EDGES),
            regime_group=regime,
            ttl_bucket=_bucket(ttl, TTL_EDGES_MS),
            ttl_ms=ttl,
        )


@dataclass
class _Counts:
    at_risk: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)
    fills: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)
    censored: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)
    actionable: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)
    dust: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)

    def observe(self, bin_idx: int, filled: bool, outcome: str | None) -> None:
        idx = max(0, min(N_AGE_BINS - 1, int(bin_idx)))
        for k in range(0, idx):
            self.at_risk[k] += 1
        self.at_risk[idx] += 1
        if filled:
            self.fills[idx] += 1
            if outcome == "actionable":
                self.actionable[idx] += 1
            elif outcome == "dust":
                self.dust[idx] += 1
        else:
            self.censored[idx] += 1

    def n0(self) -> int:
        return int(self.at_risk[0])


@dataclass
class HazardPrediction:
    any_fill: float
    actionable_fill: float
    dust: float
    source: str
    usable: bool
    n_at_risk: int
    ttl_ms: float


@dataclass
class CalBucket:
    predicted_sum: float = 0.0
    observed_sum: float = 0.0
    brier_sum: float = 0.0
    sample_count: int = 0

    def add(self, predicted: float, observed: float) -> None:
        p = _clip(predicted, 0.0, 1.0)
        y = 1.0 if float(observed) >= 0.5 else 0.0
        self.predicted_sum += p
        self.observed_sum += y
        self.brier_sum += (p - y) ** 2
        self.sample_count += 1

    def snapshot(self) -> dict[str, float | int]:
        n = max(1, self.sample_count)
        return {
            "predicted_mean": self.predicted_sum / n if self.sample_count else 0.0,
            "observed_rate": self.observed_sum / n if self.sample_count else 0.0,
            "sample_count": self.sample_count,
            "brier_component": self.brier_sum / n if self.sample_count else 0.0,
        }


class FillHazardModel:
    """Bounded empirical hazard: primary (side, dist) plus shrunk global/side priors."""

    def __init__(
        self,
        *,
        min_samples: int = FROZEN_MIN_SAMPLES,
        prior_strength: float = FROZEN_PRIOR_STRENGTH,
        prior_any: float = FROZEN_PRIOR_ANY,
        prior_actionable_given_fill: float = FROZEN_PRIOR_ACTIONABLE_GIVEN_FILL,
        p_min: float = FROZEN_P_MIN,
        p_max: float = FROZEN_P_MAX,
        feature_logit_weight: float = FROZEN_FEATURE_LOGIT_WEIGHT,
    ) -> None:
        self.min_samples = max(1, int(min_samples))
        self.prior_strength = max(0.0, float(prior_strength))
        self.prior_any = _clip(prior_any, 0.01, 0.5)
        self.prior_actionable_given_fill = _clip(prior_actionable_given_fill, 0.05, 0.95)
        self.p_min = max(0.0, float(p_min))
        self.p_max = min(1.0, float(p_max))
        self.feature_logit_weight = max(0.0, min(1.0, float(feature_logit_weight)))
        self.global_counts = _Counts()
        self.side_counts: dict[str, _Counts] = {"buy": _Counts(), "sell": _Counts()}
        self.cells: dict[tuple[str, int], _Counts] = {}
        self.feature_counts: dict[tuple[str, str | int], _Counts] = {}
        self.calibration: dict[tuple[str, str, str], CalBucket] = {}
        self.brier_any_sum = 0.0
        self.brier_any_n = 0
        self.brier_act_sum = 0.0
        self.brier_act_n = 0
        self.brier_dust_sum = 0.0
        self.brier_dust_n = 0
        self.observations = 0
        self.events = 0
        self.censored = 0

    def _cell(self, side: str, dist_bucket: int) -> _Counts:
        key = (side, int(dist_bucket))
        return self.cells.setdefault(key, _Counts())

    def _feat(self, name: str, bucket: str | int) -> _Counts:
        return self.feature_counts.setdefault((name, bucket), _Counts())

    def observe(
        self,
        features: HazardFeatures,
        *,
        age_ms: float,
        filled: bool,
        fill_class: str | None = None,
        predicted: HazardPrediction | None = None,
        include_in_calibration: bool = True,
    ) -> None:
        idx = age_bin(age_ms)
        outcome = outcome_from_fill_class(fill_class) if filled else None
        self.global_counts.observe(idx, filled, outcome)
        self.side_counts[features.side].observe(idx, filled, outcome)
        self._cell(features.side, features.dist_bucket).observe(idx, filled, outcome)
        self._feat("spread", features.spread_bucket).observe(idx, filled, outcome)
        self._feat("vol", features.vol_bucket).observe(idx, filled, outcome)
        self._feat("trade", features.trade_bucket).observe(idx, filled, outcome)
        self._feat("imb", features.imb_bucket).observe(idx, filled, outcome)
        self._feat("regime", features.regime_group).observe(idx, filled, outcome)
        self._feat("ttl", features.ttl_bucket).observe(idx, filled, outcome)
        self.observations += 1
        if filled:
            self.events += 1
        else:
            self.censored += 1
        if include_in_calibration and predicted is not None:
            self._calibrate(features, filled, outcome, predicted, age_ms)

    def _calibrate(
        self,
        features: HazardFeatures,
        filled: bool,
        outcome: str | None,
        predicted: HazardPrediction,
        age_ms: float,
    ) -> None:
        ttl = max(1.0, float(features.ttl_ms))
        if (not filled) and age_ms + 1e-9 < ttl:
            return
        y_any = 1.0 if filled and age_ms <= ttl + 1e-9 else 0.0
        y_act = 1.0 if y_any >= 0.5 and outcome == "actionable" else 0.0
        y_dust = 1.0 if y_any >= 0.5 and outcome == "dust" else 0.0
        self._add_cal("ANY", features.side, predicted.any_fill, y_any)
        self._add_cal("ACTIONABLE", features.side, predicted.actionable_fill, y_act)
        self._add_cal("DUST", features.side, predicted.dust, y_dust)
        self.brier_any_sum += (predicted.any_fill - y_any) ** 2
        self.brier_any_n += 1
        self.brier_act_sum += (predicted.actionable_fill - y_act) ** 2
        self.brier_act_n += 1
        self.brier_dust_sum += (predicted.dust - y_dust) ** 2
        self.brier_dust_n += 1

    def _add_cal(self, kind: str, side: str, predicted: float, observed: float) -> None:
        key = (kind, side.upper(), cal_bucket(predicted))
        bucket = self.calibration.setdefault(key, CalBucket())
        bucket.add(predicted, observed)

    def _hazard_path(self, counts: _Counts, ttl_ms: float) -> tuple[float, float, float, int]:
        alpha = self.prior_strength
        n_bins = max(1, len(tuple(bins_for_ttl(ttl_ms))))
        h0 = 1.0 - (1.0 - self.prior_any) ** (1.0 / n_bins)
        surv = 1.0
        act_cif = 0.0
        dust_cif = 0.0
        for k in bins_for_ttl(ttl_ms):
            n = counts.at_risk[k]
            d = counts.fills[k]
            h = (d + alpha * h0) / (n + alpha) if (n + alpha) > 0 else h0
            h = _clip(h, 0.0, 0.999)
            fills = max(d, 0)
            p_act_g = (
                (counts.actionable[k] + alpha * self.prior_actionable_given_fill)
                / (fills + alpha)
                if (fills + alpha) > 0
                else self.prior_actionable_given_fill
            )
            p_dust_g = (
                (counts.dust[k] + alpha * (1.0 - self.prior_actionable_given_fill))
                / (fills + alpha)
                if (fills + alpha) > 0
                else (1.0 - self.prior_actionable_given_fill)
            )
            act_cif += surv * h * p_act_g
            dust_cif += surv * h * p_dust_g
            surv *= (1.0 - h)
        p_any = 1.0 - surv
        return p_any, act_cif, dust_cif, counts.n0()

    def predict(self, features: HazardFeatures) -> HazardPrediction:
        ttl = features.ttl_ms
        p_g, a_g, d_g, n_g = self._hazard_path(self.global_counts, ttl)
        p_s, a_s, d_s, n_s = self._hazard_path(self.side_counts[features.side], ttl)
        p_c, a_c, d_c, n_c = self._hazard_path(
            self._cell(features.side, features.dist_bucket), ttl,
        )
        k = self.prior_strength
        p0 = self.prior_any
        a0 = p0 * self.prior_actionable_given_fill
        d0 = p0 * (1.0 - self.prior_actionable_given_fill)
        denom = n_c + n_s + n_g + k
        p_any = (n_c * p_c + n_s * p_s + n_g * p_g + k * p0) / denom
        p_act = (n_c * a_c + n_s * a_s + n_g * a_g + k * a0) / denom
        p_dust = (n_c * d_c + n_s * d_s + n_g * d_g + k * d0) / denom
        if n_g >= self.min_samples and self.feature_logit_weight > 0.0:
            adj = 0.0
            extras = (
                ("spread", features.spread_bucket),
                ("vol", features.vol_bucket),
                ("trade", features.trade_bucket),
                ("imb", features.imb_bucket),
                ("regime", features.regime_group),
                ("ttl", features.ttl_bucket),
            )
            used = 0
            for name, bucket in extras:
                counts = self.feature_counts.get((name, bucket))
                if counts is None or counts.n0() < max(4, self.min_samples // 2):
                    continue
                p_f, _, _, _ = self._hazard_path(counts, ttl)
                adj += _logit(p_f) - _logit(max(p_g, 1e-6))
                used += 1
            if used:
                p_any = _sigmoid(_logit(p_any) + self.feature_logit_weight * adj / used)

        usable = n_g >= self.min_samples or n_c >= max(4, self.min_samples // 2)
        if n_c >= self.min_samples:
            source = "cell"
        elif n_s >= self.min_samples:
            source = "side"
        elif n_g >= self.min_samples:
            source = "global"
        else:
            source = "fallback"
            usable = False

        p_any = _clip(p_any, self.p_min, self.p_max)
        p_act = _clip(p_act, 0.0, self.p_max)
        p_dust = _clip(p_dust, 0.0, self.p_max)
        cap = max(p_any, 1e-9)
        if p_act + p_dust > cap:
            scale = cap / (p_act + p_dust)
            p_act *= scale
            p_dust *= scale
        return HazardPrediction(
            any_fill=p_any,
            actionable_fill=p_act,
            dust=p_dust,
            source=source,
            usable=usable,
            n_at_risk=n_c if n_c > 0 else n_s if n_s > 0 else n_g,
            ttl_ms=ttl,
        )

    def select_policy_probability(
        self,
        old_prob: float,
        predicted: HazardPrediction,
        *,
        use_for_policy: bool,
    ) -> float:
        prob, _, _ = self.apply_policy_fill(old_prob, predicted, use_for_policy=use_for_policy)
        return prob

    def model_confidence(self, predicted: HazardPrediction) -> float:
        if predicted.source == "global":
            n = self.global_counts.n0()
        else:
            n = max(0, int(predicted.n_at_risk))
        return _clip(float(n) / max(1, self.min_samples), 0.0, 1.0)

    def calibration_fallback_reason(self) -> str:
        min_cal = max(self.min_samples * 2, 24)
        if self.brier_any_n < min_cal:
            return ""
        pred_sum = 0.0
        obs_sum = 0.0
        n = 0
        for bucket in self.calibration.values():
            if bucket.sample_count <= 0:
                continue
            pred_sum += bucket.predicted_sum
            obs_sum += bucket.observed_sum
            n += int(bucket.sample_count)
        if n < min_cal:
            return ""
        if abs(pred_sum / n - obs_sum / n) > 0.40:
            return "LOW_CONFIDENCE"
        return ""

    def apply_policy_fill(
        self,
        old_prob: float,
        predicted: HazardPrediction | None,
        *,
        use_for_policy: bool,
    ) -> tuple[float, str, float]:
        """Return (probability, fallback_reason, confidence).

        fallback_reason is '' when the frozen hazard is used for policy.
        """
        legacy = _clip(old_prob, 0.0, 1.0)
        if not use_for_policy:
            return legacy, "POLICY_DISABLED", 0.0
        if predicted is None:
            return legacy, "UNSUPPORTED_FEATURES", 0.0
        conf = self.model_confidence(predicted)
        any_fill = predicted.any_fill
        if any_fill is None or any_fill != any_fill:
            return legacy, "INVALID_OUTPUT", conf
        try:
            any_fill = float(any_fill)
        except (TypeError, ValueError):
            return legacy, "INVALID_OUTPUT", conf
        if any_fill < 0.0 or any_fill > 1.0:
            return legacy, "INVALID_OUTPUT", conf
        if predicted.source == "fallback" or not predicted.usable:
            return legacy, "INSUFFICIENT_SAMPLES", conf
        if conf + 1e-12 < 0.5:
            return legacy, "LOW_CONFIDENCE", conf
        cal_reason = self.calibration_fallback_reason()
        if cal_reason:
            return legacy, cal_reason, conf
        return _clip(any_fill, 0.0, 1.0), "", conf

    def brier_overall(self) -> dict[str, float | int]:
        return {
            "ANY": (self.brier_any_sum / self.brier_any_n) if self.brier_any_n else 0.0,
            "ACTIONABLE": (self.brier_act_sum / self.brier_act_n) if self.brier_act_n else 0.0,
            "DUST": (self.brier_dust_sum / self.brier_dust_n) if self.brier_dust_n else 0.0,
            "n": self.brier_any_n,
        }

    def calibration_rows(self, kind: str, side: str) -> list[dict[str, Any]]:
        rows = []
        overall = self.brier_overall()
        for name, _, _ in CAL_BUCKETS:
            key = (kind, side.upper(), name)
            snap = self.calibration.get(key, CalBucket()).snapshot()
            rows.append(
                {
                    "kind": kind,
                    "side": side.upper(),
                    "bucket": name,
                    **snap,
                    "brier_overall": overall.get(kind, 0.0),
                }
            )
        return rows
