# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Frozen production fill-hazard: prediction, fallback, calibration."""
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from execution_hazard import (
    FROZEN_FEATURE_LOGIT_WEIGHT,
    FROZEN_MIN_SAMPLES,
    FROZEN_P_MAX,
    FROZEN_P_MIN,
    FROZEN_PRIOR_ACTIONABLE_GIVEN_FILL,
    FROZEN_PRIOR_ANY,
    FROZEN_PRIOR_STRENGTH,
    FillHazardModel,
    HazardFeatures,
    HazardPrediction,
    cal_bucket,
    outcome_from_fill_class,
)


def _feat(side="buy", dist=0, ttl=500.0, **kwargs):
    return HazardFeatures(
        side=side,
        dist_bucket=dist,
        spread_bucket=kwargs.get("spread_bucket", 1),
        vol_bucket=kwargs.get("vol_bucket", 1),
        trade_bucket=kwargs.get("trade_bucket", 1),
        imb_bucket=kwargs.get("imb_bucket", 1),
        regime_group=kwargs.get("regime_group", "NORMAL"),
        ttl_bucket=kwargs.get("ttl_bucket", 1),
        ttl_ms=ttl,
    )


def _model(**kwargs):
    return FillHazardModel(**kwargs)


def _legacy_any_fill(spread, mid, trade_rate, quote_price, side, trade_rate_ref=1.0):
    """Stable Strategy1 estimator without book-memory blend."""
    if spread <= 0 or mid <= 0:
        return 0.0
    trade_factor = min(1.0, trade_rate / max(trade_rate_ref, 1e-9))
    if side == "buy":
        dist = (mid - quote_price) / spread
    else:
        dist = (quote_price - mid) / spread
    dist_term = max(0.0, 1.0 - dist)
    depth_term = 0.5
    p = trade_factor * (0.25 * 0.5 + 0.35 * dist_term + 0.4 * depth_term)
    return max(0.0, min(1.0, p))


def test_frozen_defaults():
    model = FillHazardModel()
    assert model.min_samples == FROZEN_MIN_SAMPLES == 12
    assert model.prior_strength == FROZEN_PRIOR_STRENGTH == 8.0
    assert model.prior_any == FROZEN_PRIOR_ANY == 0.12
    assert model.prior_actionable_given_fill == FROZEN_PRIOR_ACTIONABLE_GIVEN_FILL == 0.55
    assert model.p_min == FROZEN_P_MIN == 0.01
    assert model.p_max == FROZEN_P_MAX == 0.95
    assert model.feature_logit_weight == FROZEN_FEATURE_LOGIT_WEIGHT == 0.0


def test_filled_quote_is_an_event():
    model = _model()
    feat = _feat()
    pred0 = model.predict(feat)
    model.observe(feat, age_ms=80.0, filled=True, fill_class="FULL", predicted=pred0)
    assert model.events == 1
    assert model.censored == 0


def test_cancel_and_replace_are_censored():
    model = _model()
    feat = _feat()
    model.observe(feat, age_ms=120.0, filled=False)
    model.observe(feat, age_ms=40.0, filled=False)
    assert model.events == 0
    assert model.censored == 2


def test_insufficient_samples_falls_back_to_legacy():
    model = _model()
    pred = model.predict(_feat())
    assert pred.source == "fallback"
    assert pred.usable is False
    old = 0.42
    used, reason, conf = model.apply_policy_fill(old, pred, use_for_policy=True)
    assert used == old
    assert reason == "INSUFFICIENT_SAMPLES"
    assert conf >= 0.0


def test_invalid_output_falls_back():
    model = _model()
    pred = HazardPrediction(
        any_fill=float("nan"),
        actionable_fill=0.1,
        dust=0.1,
        source="cell",
        usable=True,
        n_at_risk=20,
        ttl_ms=500.0,
    )
    used, reason, _ = model.apply_policy_fill(0.33, pred, use_for_policy=True)
    assert used == 0.33
    assert reason == "INVALID_OUTPUT"


def test_unsupported_features_falls_back():
    model = _model()
    used, reason, conf = model.apply_policy_fill(0.21, None, use_for_policy=True)
    assert used == 0.21
    assert reason == "UNSUPPORTED_FEATURES"
    assert conf == 0.0


def test_usable_after_minimum_samples_uses_hazard():
    model = _model(min_samples=8, prior_strength=4.0)
    feat = _feat()
    for _ in range(16):
        pred = model.predict(feat)
        model.observe(feat, age_ms=40.0, filled=True, fill_class="FULL", predicted=pred)
    pred = model.predict(feat)
    assert pred.usable
    used, reason, conf = model.apply_policy_fill(0.20, pred, use_for_policy=True)
    assert reason == ""
    assert used == pred.any_fill
    assert conf >= 0.5


def test_actionable_and_dust_probabilities():
    model = _model(min_samples=8, prior_strength=2.0)
    feat = _feat()
    for _ in range(16):
        model.observe(feat, age_ms=40.0, filled=True, fill_class="FULL")
    pred = model.predict(feat)
    assert pred.actionable_fill > pred.dust
    dust_model = _model(min_samples=8, prior_strength=2.0)
    for _ in range(16):
        dust_model.observe(feat, age_ms=40.0, filled=True, fill_class="DUST_PARTIAL")
    dust_pred = dust_model.predict(feat)
    assert dust_pred.dust > dust_pred.actionable_fill


def test_outcome_from_fill_class_tokens():
    assert outcome_from_fill_class("FULL") == "actionable"
    assert outcome_from_fill_class("ACTIONABLE_PARTIAL") == "actionable"
    assert outcome_from_fill_class("FLAT") == "actionable"
    assert outcome_from_fill_class("DUST_PARTIAL") == "dust"
    assert outcome_from_fill_class("CROSS_DUST") == "dust"


def test_low_calibration_confidence_falls_back():
    model = _model(min_samples=4)
    feat = _feat()
    for _ in range(8):
        model.observe(
            feat, age_ms=40.0, filled=True, fill_class="FULL",
            include_in_calibration=False,
        )
    pred = model.predict(feat)
    assert pred.usable
    for _ in range(24):
        model._add_cal("ANY", "BUY", 0.90, 0.0)
    model.brier_any_n = 24
    used, reason, _ = model.apply_policy_fill(0.31, pred, use_for_policy=True)
    assert used == 0.31
    assert reason == "LOW_CONFIDENCE"


def test_calibration_bucket_accounting():
    model = _model(min_samples=1, prior_strength=8.0)
    feat = _feat(ttl=500.0)
    pred = HazardPrediction(
        any_fill=0.15,
        actionable_fill=0.10,
        dust=0.05,
        source="cell",
        usable=True,
        n_at_risk=10,
        ttl_ms=500.0,
    )
    assert cal_bucket(0.15) == "0.10_0.20"
    model.observe(feat, age_ms=80.0, filled=True, fill_class="FULL", predicted=pred)
    model.observe(feat, age_ms=500.0, filled=False, predicted=pred)
    rows = {row["bucket"]: row for row in model.calibration_rows("ACTIONABLE", "BUY")}
    hit = rows["0.10_0.20"]
    assert hit["sample_count"] == 2
    assert hit["observed_rate"] == 0.5


def test_legacy_vs_hazard_brier_on_synthetic_quotes():
    """Compare the frozen hazard to the stable legacy estimator on a known fill process."""
    model = _model()
    feat = _feat()
    mid, spread, trade_rate = 100.0, 0.20, 0.8
    buy_px = 99.96
    outcomes = [1] * 36 + [0] * 12
    legacy_preds = []
    hazard_preds = []
    for y in outcomes:
        legacy_p = _legacy_any_fill(spread, mid, trade_rate, buy_px, "buy")
        pred = model.predict(feat)
        policy_p, reason, _ = model.apply_policy_fill(legacy_p, pred, use_for_policy=True)
        used = policy_p if reason == "" else legacy_p
        legacy_preds.append(legacy_p)
        hazard_preds.append(used)
        model.observe(feat, age_ms=80.0 if y else 400.0, filled=bool(y), fill_class="FULL" if y else None, predicted=pred)

    def brier(preds):
        return sum((p - y) ** 2 for p, y in zip(preds, outcomes)) / len(outcomes)

    later = slice(24, None)
    hazard_brier = brier(hazard_preds[later])
    legacy_brier = brier(legacy_preds[later])
    final = model.predict(feat)
    assert final.usable
    assert 0.0 <= hazard_brier <= 1.0
    assert 0.0 <= legacy_brier <= 1.0
    # After enough events the shrunk KM estimate should move toward the 75% fill rate.
    assert final.any_fill > FROZEN_PRIOR_ANY
    assert abs(final.any_fill - 0.75) < abs(legacy_preds[-1] - 0.75)


def test_predict_overhead_is_bounded():
    model = _model()
    feat = _feat()
    for _ in range(FROZEN_MIN_SAMPLES):
        model.observe(feat, age_ms=40.0, filled=True, fill_class="FULL")
    started = time.perf_counter()
    n = 4000
    for _ in range(n):
        model.predict(feat)
    elapsed_ms = (time.perf_counter() - started) * 1000.0
    per_call_us = (elapsed_ms * 1000.0) / n
    assert elapsed_ms < 250.0
    assert per_call_us < 100.0
