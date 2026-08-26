# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 3: fill-hazard learning, censoring, calibration."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_fill_hazard import (
    CAL_BUCKET_LABELS,
    FillHazardModel,
    HazardFeatures,
    HazardPrediction,
    brier_score,
    cal_bucket,
    cal_bucket_label,
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
    defaults = dict(
        min_samples=12,
        prior_strength=8.0,
        prior_any=0.12,
        p_min=0.01,
        p_max=0.95,
        feature_logit_weight=0.0,
    )
    defaults.update(kwargs)
    return FillHazardModel(**defaults)


def test_filled_quote_is_an_event():
    model = _model()
    feat = _feat()
    pred0 = model.predict(feat)
    model.observe(feat, age_ms=80.0, filled=True, fill_class="FULL", predicted=pred0)
    assert model.events == 1
    assert model.censored == 0
    assert model.global_counts.fills[0] == 1
    assert model.global_counts.at_risk[0] == 1


def test_cancelled_censoring():
    model = _model()
    feat = _feat()
    model.observe(feat, age_ms=120.0, filled=False)
    assert model.events == 0
    assert model.censored == 1
    assert model.global_counts.censored[1] == 1
    assert model.global_counts.fills[1] == 0
    assert model.global_counts.at_risk[0] == 1
    assert model.global_counts.at_risk[1] == 1


def test_expiry_censoring():
    model = _model()
    feat = _feat(ttl=500.0)
    model.observe(feat, age_ms=500.0, filled=False)
    assert model.censored == 1
    assert sum(model.global_counts.fills) == 0
    assert sum(model.global_counts.censored) == 1


def test_replacement_censoring():
    model = _model()
    feat = _feat()
    model.observe(feat, age_ms=40.0, filled=False)
    assert model.censored == 1
    assert model.events == 0
    assert model.global_counts.at_risk[0] == 1
    assert model.global_counts.at_risk[1] == 0


def test_insufficient_data_fallback():
    model = _model(min_samples=12)
    pred = model.predict(_feat())
    assert pred.source == "fallback"
    assert pred.usable is False
    assert 0.01 <= pred.any_fill <= 0.95
    old = 0.42
    assert model.select_policy_probability(old, pred, use_for_policy=True) == old
    assert model.select_policy_probability(old, pred, use_for_policy=False) == old


def test_probability_bounds():
    model = _model(p_min=0.01, p_max=0.95, min_samples=1, prior_strength=0.0)
    feat = _feat()
    for _ in range(30):
        model.observe(feat, age_ms=10.0, filled=True, fill_class="FULL")
    pred = model.predict(feat)
    assert 0.01 <= pred.any_fill <= 0.95
    assert 0.0 <= pred.actionable_fill <= 0.95
    assert 0.0 <= pred.dust <= 0.95
    assert pred.actionable_fill + pred.dust <= pred.any_fill + 1e-9


def test_shrinkage_toward_prior():
    model = _model(prior_any=0.12, prior_strength=8.0, min_samples=1)
    feat = _feat()
    model.observe(feat, age_ms=10.0, filled=True, fill_class="FULL")
    pred = model.predict(feat)
    assert pred.any_fill < 0.55
    assert pred.any_fill > 0.12


def test_side_specific_learning():
    model = _model(min_samples=8, prior_strength=4.0)
    buy = _feat(side="buy")
    sell = _feat(side="sell")
    for _ in range(20):
        model.observe(buy, age_ms=40.0, filled=True, fill_class="FULL")
    for _ in range(20):
        model.observe(sell, age_ms=40.0, filled=False)
    p_buy = model.predict(buy)
    p_sell = model.predict(sell)
    assert p_buy.any_fill > p_sell.any_fill + 0.15
    assert p_buy.source in {"cell", "side", "global"}


def test_actionable_fill_estimate():
    model = _model(min_samples=8, prior_strength=2.0)
    feat = _feat()
    for _ in range(16):
        model.observe(feat, age_ms=40.0, filled=True, fill_class="FULL")
    pred = model.predict(feat)
    assert pred.actionable_fill > pred.dust
    assert pred.actionable_fill > 0.2


def test_dust_estimate():
    model = _model(min_samples=8, prior_strength=2.0)
    feat = _feat()
    for _ in range(16):
        model.observe(feat, age_ms=40.0, filled=True, fill_class="DUST_PARTIAL")
    pred = model.predict(feat)
    assert pred.dust > pred.actionable_fill
    assert pred.dust > 0.2


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
    model.observe(
        feat, age_ms=80.0, filled=True, fill_class="FULL",
        predicted=pred, include_in_calibration=True,
    )
    model.observe(
        feat, age_ms=40.0, filled=False, predicted=pred, include_in_calibration=True,
    )
    model.observe(
        feat, age_ms=500.0, filled=False, predicted=pred, include_in_calibration=True,
    )
    rows = {row["bucket"]: row for row in model.calibration_rows("ACTIONABLE", "BUY")}
    hit = rows["0.10_0.20"]
    assert hit["sample_count"] == 2
    assert hit["observed_rate"] == 0.5
    assert abs(hit["predicted_mean"] - 0.10) < 1e-9
    assert hit["brier_component"] > 0.0
    overall = model.brier_overall()
    assert overall["n"] == 2
    assert overall["ACTIONABLE"] == hit["brier_component"]


def test_policy_flag_keeps_old_estimator_when_off():
    model = _model()
    feat = _feat()
    for _ in range(20):
        model.observe(feat, age_ms=20.0, filled=True, fill_class="FULL")
    pred = model.predict(feat)
    old = 0.77
    assert model.select_policy_probability(old, pred, use_for_policy=False) == old
    assert pred.usable is True
    assert model.select_policy_probability(old, pred, use_for_policy=True) == pred.any_fill


def test_outcome_mapping():
    assert outcome_from_fill_class("FULL") == "actionable"
    assert outcome_from_fill_class("FLAT") == "actionable"
    assert outcome_from_fill_class("DUST_PARTIAL") == "dust"
    assert outcome_from_fill_class("CROSS_DUST") == "dust"


def test_feature_snapshot_does_not_build_huge_key():
    feat = HazardFeatures.from_snapshot(
        side="sell",
        distance_from_touch_bps=0.2,
        spread_bps=3.0,
        volatility=0.001,
        trade_rate=1.5,
        imbalance=-0.4,
        market_regime="TREND_UP",
        ttl_ms=500.0,
    )
    assert feat.side == "sell"
    assert feat.dist_bucket == 0
    assert feat.regime_group == "TREND"
    assert feat.quote_age_bucket == 0
    model = _model()
    model.observe(feat, age_ms=10.0, filled=True, fill_class="FULL")
    assert len(model.cells) == 1
    assert ("sell", 0) in model.cells


def test_calibration_bucket_labels():
    assert cal_bucket(0.03) == "0.00_0.05"
    assert cal_bucket_label(0.03) == "0-5%"
    assert cal_bucket_label(0.07) == "5-10%"
    assert cal_bucket_label(0.15) == "10-20%"
    assert cal_bucket_label(0.30) == "20-40%"
    assert cal_bucket_label(0.55) == "40%+"
    assert set(CAL_BUCKET_LABELS.values()) == {"0-5%", "5-10%", "10-20%", "20-40%", "40%+"}


def test_brier_score_predicted_vs_observed():
    perfect = brier_score([0.0, 1.0, 0.0, 1.0], [0, 1, 0, 1])
    assert perfect == 0.0
    miss = brier_score([0.9, 0.1], [0, 1])
    assert miss > 0.6
    model = _model(min_samples=1, prior_strength=4.0)
    feat = _feat()
    preds = []
    ys = []
    for filled in (True, False, True, False):
        pred = model.predict(feat)
        preds.append(pred.any_fill)
        ys.append(1.0 if filled else 0.0)
        model.observe(
            feat, age_ms=80.0 if filled else 500.0, filled=filled,
            fill_class="FULL" if filled else None, predicted=pred,
        )
    overall = model.brier_overall()
    assert overall["n"] == 4
    assert overall["ANY"] == brier_score(preds, ys)
    rows = {row["bucket_label"]: row for row in model.calibration_rows("ANY", "BUY")}
    assert "10-20%" in rows or "0-5%" in rows or "5-10%" in rows or "20-40%" in rows or "40%+" in rows


def test_remaining_hazard_uses_quote_age():
    model = _model(min_samples=4, prior_strength=1.0)
    feat0 = _feat(ttl=500.0)
    for _ in range(12):
        model.observe(feat0, age_ms=40.0, filled=True, fill_class="FULL")
    young = model.predict(feat0)
    aged = model.predict(
        HazardFeatures(
            side="buy",
            dist_bucket=0,
            spread_bucket=1,
            vol_bucket=1,
            trade_bucket=1,
            imb_bucket=1,
            regime_group="NORMAL",
            ttl_bucket=1,
            ttl_ms=500.0,
            quote_age_bucket=2,
            quote_age_ms=300.0,
        )
    )
    assert young.time_to_fill_hazard > 0.0
    assert 0.0 <= aged.time_to_fill_hazard <= 0.999
    assert aged.remaining_any_fill <= young.any_fill + 1e-9
    assert young.remaining_any_fill == young.any_fill
    assert len(young.hazard_rates) >= 1


def test_legacy_estimator_remains_fallback():
    model = _model(min_samples=12)
    feat = _feat()
    pred = model.predict(feat)
    assert pred.source == "fallback"
    assert pred.usable is False
    old = 0.33
    assert model.select_policy_probability(old, pred, use_for_policy=True) == old
    for _ in range(20):
        model.observe(feat, age_ms=30.0, filled=True, fill_class="FULL")
    learned = model.predict(feat)
    assert learned.usable is True
    assert model.select_policy_probability(old, learned, use_for_policy=False) == old
    assert model.select_policy_probability(old, learned, use_for_policy=True) == learned.any_fill
