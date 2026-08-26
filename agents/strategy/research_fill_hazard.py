# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 1.6: execution probabilities with censored hazard.

Pure functions so unit tests do not import Strategy1 / bittensor.

Estimates P(any fill), P(actionable fill), P(dust), and a discrete
time-to-fill hazard. This is not a raw P(fill) optimizer.

Filled quotes are events. Cancel / expire / replace without a fill are
right-censored (Cox-style discrete time-to-event). Extra LOB features
enter through shrunk logit offsets (Huang/Lehalle/Rosenbaum queue-
reactive buckets). Calibration is Brier + predicted-vs-observed buckets.
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
CAL_BUCKET_LABELS = {
    "0.00_0.05": "0-5%",
    "0.05_0.10": "5-10%",
    "0.10_0.20": "10-20%",
    "0.20_0.40": "20-40%",
    "0.40_1.00": "40%+",
}
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


def cal_bucket_label(p: float | str) -> str:
    name = p if isinstance(p, str) else cal_bucket(p)
    return CAL_BUCKET_LABELS.get(str(name), CAL_BUCKET_LABELS[CAL_BUCKETS[-1][0]])


def brier_score(predicted: list[float], observed: list[float]) -> float:
    """Mean squared error between probabilities and binary outcomes."""
    if not predicted or len(predicted) != len(observed):
        return 0.0
    total = 0.0
    for pred, y in zip(predicted, observed):
        p = _clip(float(pred), 0.0, 1.0)
        o = 1.0 if float(y) >= 0.5 else 0.0
        total += (p - o) ** 2
    return total / float(len(predicted))


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
    distance_from_touch_bps: float | None = None
    quote_age_bucket: int = 0
    quote_age_ms: float = 0.0

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
        quote_age_ms: float | None = None,
    ) -> "HazardFeatures":
        ttl = 500.0 if ttl_ms is None else max(1.0, float(ttl_ms))
        age = 0.0 if quote_age_ms is None else max(0.0, float(quote_age_ms))
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
            distance_from_touch_bps=(
                None if distance_from_touch_bps is None
                else max(0.0, float(distance_from_touch_bps))
            ),
            quote_age_bucket=age_bin(age),
            quote_age_ms=age,
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
    time_to_fill_hazard: float = 0.0
    remaining_any_fill: float = 0.0
    hazard_rates: tuple[float, ...] = ()


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
        min_samples: int = 12,
        prior_strength: float = 8.0,
        prior_any: float = 0.12,
        prior_actionable_given_fill: float = 0.55,
        p_min: float = 0.01,
        p_max: float = 0.95,
        feature_logit_weight: float = 0.20,
        distance_decay_bps: float = 6.0,
        distance_near_boost: float = 1.35,
        distance_floor_mult: float = 0.10,
        fallback_policy_weight: float = 0.0,
    ) -> None:
        self.min_samples = max(1, int(min_samples))
        self.prior_strength = max(0.0, float(prior_strength))
        self.prior_any = _clip(prior_any, 0.01, 0.5)
        self.prior_actionable_given_fill = _clip(prior_actionable_given_fill, 0.05, 0.95)
        self.p_min = max(0.0, float(p_min))
        self.p_max = min(1.0, float(p_max))
        self.feature_logit_weight = max(0.0, min(1.0, float(feature_logit_weight)))
        # V4.12.2: use continuous distance-to-touch even before empirical cells
        # have enough samples. The old three distance buckets were too coarse:
        # a 2 bps and a 25 bps quote could share nearly the same sparse prior.
        self.distance_decay_bps = max(0.25, float(distance_decay_bps))
        self.distance_near_boost = max(0.25, min(3.0, float(distance_near_boost)))
        self.distance_floor_mult = max(0.01, min(1.0, float(distance_floor_mult)))
        # Default zero preserves the standalone model's legacy fallback contract.
        # Research runtime can opt into a bounded blend with the old estimator.
        self.fallback_policy_weight = max(0.0, min(1.0, float(fallback_policy_weight)))
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
        self._feat("age", features.quote_age_bucket).observe(idx, filled, outcome)
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

    def _hazard_path(
        self,
        counts: _Counts,
        ttl_ms: float,
        start_bin: int = 0,
    ) -> tuple[float, float, float, int, tuple[float, ...]]:
        alpha = self.prior_strength
        start = max(0, min(N_AGE_BINS - 1, int(start_bin)))
        bins = [k for k in bins_for_ttl(ttl_ms) if k >= start]
        n_bins = max(1, len(bins))
        h0 = 1.0 - (1.0 - self.prior_any) ** (1.0 / n_bins)
        surv = 1.0
        act_cif = 0.0
        dust_cif = 0.0
        rates: list[float] = []
        for k in bins:
            n = counts.at_risk[k]
            d = counts.fills[k]
            h = (d + alpha * h0) / (n + alpha) if (n + alpha) > 0 else h0
            h = _clip(h, 0.0, 0.999)
            rates.append(h)
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
        n_ref = counts.at_risk[start] if start < len(counts.at_risk) else counts.n0()
        return p_any, act_cif, dust_cif, int(n_ref), tuple(rates)

    def _blend_paths(
        self,
        ttl: float,
        side: str,
        dist_bucket: int,
        start: int,
    ) -> tuple[float, float, float, int, tuple[float, ...], int, int]:
        p_g, a_g, d_g, n_g, r_g = self._hazard_path(self.global_counts, ttl, start)
        p_s, a_s, d_s, n_s, r_s = self._hazard_path(self.side_counts[side], ttl, start)
        p_c, a_c, d_c, n_c, r_c = self._hazard_path(
            self._cell(side, dist_bucket), ttl, start,
        )
        k = self.prior_strength
        p0 = self.prior_any
        a0 = p0 * self.prior_actionable_given_fill
        d0 = p0 * (1.0 - self.prior_actionable_given_fill)
        denom = n_c + n_s + n_g + k
        p_any = (n_c * p_c + n_s * p_s + n_g * p_g + k * p0) / denom
        p_act = (n_c * a_c + n_s * a_s + n_g * a_g + k * a0) / denom
        p_dust = (n_c * d_c + n_s * d_s + n_g * d_g + k * d0) / denom
        rates = r_c or r_s or r_g
        return p_any, p_act, p_dust, n_c, rates, n_s, n_g

    def _distance_multiplier(self, distance_bps: float | None) -> float:
        """Monotone execution prior: near-touch quotes should fill more often.

        This is deliberately simple and bounded. Empirical cell/side/global hazard
        still learns on top of it; the curve mainly fixes sparse QUIET books where
        the previous fallback treated very different quote distances similarly.
        """
        if distance_bps is None:
            return 1.0
        try:
            distance = max(0.0, float(distance_bps))
        except (TypeError, ValueError):
            return 1.0
        mult = self.distance_near_boost * math.exp(-distance / self.distance_decay_bps)
        return max(self.distance_floor_mult, min(self.distance_near_boost, mult))

    def predict(self, features: HazardFeatures) -> HazardPrediction:
        ttl = features.ttl_ms
        start = int(getattr(features, "quote_age_bucket", 0) or 0)
        p_any, p_act, p_dust, n_c, rates, n_s, n_g = self._blend_paths(
            ttl, features.side, features.dist_bucket, start,
        )
        remaining = p_any
        if start > 0:
            birth_any, birth_act, birth_dust, n_c0, _, n_s0, n_g0 = self._blend_paths(
                ttl, features.side, features.dist_bucket, 0,
            )
            p_any, p_act, p_dust = birth_any, birth_act, birth_dust
            n_c, n_s, n_g = n_c0, n_s0, n_g0
        p_g, _, _, _, _ = self._hazard_path(self.global_counts, ttl, 0)
        if n_g >= self.min_samples and self.feature_logit_weight > 0.0:
            adj = 0.0
            extras = (
                ("spread", features.spread_bucket),
                ("vol", features.vol_bucket),
                ("trade", features.trade_bucket),
                ("imb", features.imb_bucket),
                ("regime", features.regime_group),
                ("ttl", features.ttl_bucket),
                ("age", features.quote_age_bucket),
            )
            used = 0
            for name, bucket in extras:
                counts = self.feature_counts.get((name, bucket))
                if counts is None or counts.n0() < max(4, self.min_samples // 2):
                    continue
                p_f, _, _, _, _ = self._hazard_path(counts, ttl, start)
                adj += _logit(p_f) - _logit(max(p_g, 1e-6))
                used += 1
            if used:
                p_any = _sigmoid(_logit(p_any) + self.feature_logit_weight * adj / used)
                remaining = _sigmoid(
                    _logit(remaining) + self.feature_logit_weight * adj / used
                )

        # Continuous distance-to-touch calibration. Preserve the learned
        # actionable/dust composition while scaling the probability that *any*
        # fill occurs. This makes sparse fallback estimates materially different
        # for 1 bps versus 25 bps quotes without inventing extra state machines.
        dist_mult = self._distance_multiplier(
            getattr(features, "distance_from_touch_bps", None)
        )
        if abs(dist_mult - 1.0) > 1e-12:
            base_any = max(p_any, 1e-9)
            act_share = max(0.0, min(1.0, p_act / base_any))
            dust_share = max(0.0, min(1.0, p_dust / base_any))
            p_any = _clip(p_any * dist_mult, self.p_min, self.p_max)
            remaining = _clip(remaining * dist_mult, self.p_min, self.p_max)
            p_act = p_any * act_share
            p_dust = p_any * dust_share

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
        remaining = _clip(remaining, self.p_min, self.p_max)
        p_act = _clip(p_act, 0.0, self.p_max)
        p_dust = _clip(p_dust, 0.0, self.p_max)
        cap = max(p_any, 1e-9)
        if p_act + p_dust > cap:
            scale = cap / (p_act + p_dust)
            p_act *= scale
            p_dust *= scale
        current_h = rates[0] if rates else self.prior_any
        return HazardPrediction(
            any_fill=p_any,
            actionable_fill=p_act,
            dust=p_dust,
            source=source,
            usable=usable,
            n_at_risk=n_c if n_c > 0 else n_s if n_s > 0 else n_g,
            ttl_ms=ttl,
            time_to_fill_hazard=_clip(current_h, 0.0, 0.999),
            remaining_any_fill=remaining,
            hazard_rates=rates,
        )

    def select_policy_probability(
        self,
        old_prob: float,
        predicted: HazardPrediction,
        *,
        use_for_policy: bool,
    ) -> float:
        old = _clip(old_prob, 0.0, 1.0)
        if not use_for_policy:
            return old
        if predicted.usable:
            return predicted.any_fill
        # Sparse data: blend rather than replacing the legacy estimator. This
        # allows the continuous distance prior to influence policy immediately
        # while keeping most weight on the existing Strategy1 estimator.
        w = self.fallback_policy_weight
        if w <= 0.0:
            return old
        return _clip((1.0 - w) * old + w * predicted.any_fill, 0.0, 1.0)

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
                    "bucket_label": CAL_BUCKET_LABELS[name],
                    **snap,
                    "brier_overall": overall.get(kind, 0.0),
                }
            )
        return rows
