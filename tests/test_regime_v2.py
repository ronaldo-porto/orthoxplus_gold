# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Production MarketRegime V2 + ScoreRegime tests (promoted from Research)."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from regime_v2 import (
    DebounceState,
    RegimeV2Thresholds,
    apply_debounce,
    classify_regime_v2,
    parent_trigger_cannot_force_stressed,
    propose_market_regime,
    propose_score_regime,
    score_regime_metrics,
)

INSTANT = RegimeV2Thresholds(debounce_ticks=1)


def _liquid_stats(**overrides):
    stats = {
        "book_count": 20,
        "stressed_ratio": 0.05,
        "liquid_ratio": 0.80,
        "trend_up_ratio": 0.20,
        "trend_down_ratio": 0.15,
        "trade_rate_med": 1.2,
        "spread_med": 2.4,
        "vol_med": 0.001,
        "stress_spread_bps": 8.0,
        "toxic_spread_bps": 12.0,
        "inactive_frac": 0.10,
        "pending_kappa_frac": 0.05,
        "books_0_obs": 1,
        "books_1_remaining": 1,
        "books_2_remaining": 1,
        "books_eligible": 15,
        "activity_deficit": 0.08,
        "round_trip_velocity": 0.40,
    }
    stats.update(overrides)
    return stats


def test_empty_cross_section_is_normal_not_stressed():
    market, trigger, _ = propose_market_regime({"book_count": 0}, "NORMAL", INSTANT)
    assert market == "NORMAL"
    assert trigger == "EMPTY_CROSS_SECTION"


def test_mean_spread_below_parent_5bps_is_not_stressed():
    market, trigger, _ = propose_market_regime(
        _liquid_stats(spread_med=6.0, stressed_ratio=0.10, liquid_ratio=0.70),
        "NORMAL",
        INSTANT,
    )
    assert market != "STRESSED"
    assert market != "TOXIC"
    assert trigger != "UNEXPOSED_BY_PARENT"


def test_unexposed_by_parent_does_not_force_stressed():
    stats = _liquid_stats()
    market = parent_trigger_cannot_force_stressed(
        stats,
        parent_mode="STRESSED",
        parent_trigger=None,
    )
    decision = classify_regime_v2(
        {**stats, "parent_mode": "STRESSED", "parent_trigger": None, "trigger": None},
        thresholds=INSTANT,
    )
    assert market != "STRESSED"
    assert decision.market_regime == "LIQUID"
    assert decision.market_trigger != "UNEXPOSED_BY_PARENT"
    assert decision.score_regime == "BALANCED"
    assert decision.parent_mode == "BROAD_LIQUID"


def test_missing_parent_trigger_string_unexposed_is_ignored():
    decision = classify_regime_v2(
        {
            **_liquid_stats(),
            "parent_mode": "STRESSED",
            "parent_trigger": "UNEXPOSED_BY_PARENT",
            "trigger": "UNEXPOSED_BY_PARENT",
            "threshold": "UNEXPOSED_BY_PARENT",
        },
        thresholds=INSTANT,
    )
    assert decision.market_regime != "STRESSED"
    assert decision.market_trigger != "UNEXPOSED_BY_PARENT"
    assert decision.market_threshold != "UNEXPOSED_BY_PARENT"


def test_stressed_ratio_and_toxic_classification():
    stressed, trigger, _ = propose_market_regime(
        _liquid_stats(stressed_ratio=0.40, liquid_ratio=0.20, spread_med=9.0),
        "NORMAL",
        INSTANT,
    )
    assert stressed == "STRESSED"
    assert trigger == "STRESSED_RATIO"

    toxic, toxic_trigger, _ = propose_market_regime(
        _liquid_stats(
            stressed_ratio=0.60,
            liquid_ratio=0.10,
            spread_med=13.0,
            vol_med=0.01,
        ),
        "NORMAL",
        INSTANT,
    )
    assert toxic == "TOXIC"
    assert toxic_trigger == "TOXIC_STRESSED_RATIO"


def test_quiet_liquid_and_trend_modes():
    quiet, _, _ = propose_market_regime(
        _liquid_stats(trade_rate_med=0.02, liquid_ratio=0.20, stressed_ratio=0.05),
        "NORMAL",
        INSTANT,
    )
    assert quiet == "QUIET"

    liquid, _, _ = propose_market_regime(_liquid_stats(), "NORMAL", INSTANT)
    assert liquid == "LIQUID"

    up, up_trig, _ = propose_market_regime(
        _liquid_stats(trend_up_ratio=0.60, trend_down_ratio=0.10, liquid_ratio=0.40),
        "NORMAL",
        INSTANT,
    )
    assert up == "TREND_UP"
    assert up_trig == "TREND_UP_RATIO"

    down, _, _ = propose_market_regime(
        _liquid_stats(trend_up_ratio=0.10, trend_down_ratio=0.62, liquid_ratio=0.40),
        "NORMAL",
        INSTANT,
    )
    assert down == "TREND_DOWN"


def _early_coverage_stats(**overrides):
    return _liquid_stats(
        books_0_obs=16,
        books_1_remaining=2,
        books_2_remaining=2,
        books_eligible=0,
        activity_deficit=0.80,
        round_trip_velocity=0.0,
        **overrides,
    )


def _mature_completion_stats(**overrides):
    return _liquid_stats(
        books_0_obs=2,
        books_1_remaining=8,
        books_2_remaining=6,
        books_eligible=4,
        activity_deficit=0.10,
        round_trip_velocity=0.02,
        **overrides,
    )


def _mature_balanced_stats(**overrides):
    return _liquid_stats(
        books_0_obs=1,
        books_1_remaining=2,
        books_2_remaining=2,
        books_eligible=15,
        activity_deficit=0.08,
        round_trip_velocity=0.40,
        **overrides,
    )


def test_score_regime_independent_of_market():
    coverage, _, _ = propose_score_regime(_early_coverage_stats(), "COVERAGE", INSTANT)
    assert coverage == "COVERAGE"

    completion, _, _ = propose_score_regime(
        _mature_completion_stats(), "COMPLETION", INSTANT,
    )
    assert completion == "COMPLETION"

    mixed = classify_regime_v2(
        _mature_completion_stats(
            stressed_ratio=0.42,
            liquid_ratio=0.20,
            spread_med=9.0,
            inactive_frac=0.05,
            pending_kappa_frac=0.30,
        ),
        score_state=DebounceState("COMPLETION", "COMPLETION", 0),
        thresholds=INSTANT,
    )
    assert mixed.market_regime == "STRESSED"
    assert mixed.score_regime == "COMPLETION"
    assert mixed.scoring_overlay is None

    coverage_liquid = classify_regime_v2(
        _early_coverage_stats(inactive_frac=0.10),
        score_state=DebounceState("COVERAGE", "COVERAGE", 0),
        thresholds=INSTANT,
    )
    assert coverage_liquid.market_regime == "LIQUID"
    assert coverage_liquid.score_regime == "COVERAGE"
    assert coverage_liquid.scoring_overlay == "SCORING_PRESSURE"


def test_debounce_requires_consecutive_ticks():
    thr = RegimeV2Thresholds(debounce_ticks=3)
    state = DebounceState("NORMAL", "NORMAL", 0)
    stressed_stats = _liquid_stats(stressed_ratio=0.45, liquid_ratio=0.15, spread_med=9.0)

    first = classify_regime_v2(stressed_stats, market_state=state, thresholds=thr)
    assert first.market_regime == "NORMAL"
    assert first.market_debounce.hold == 1

    second = classify_regime_v2(
        stressed_stats, market_state=first.market_debounce, thresholds=thr,
    )
    assert second.market_regime == "NORMAL"
    assert second.market_debounce.hold == 2

    third = classify_regime_v2(
        stressed_stats, market_state=second.market_debounce, thresholds=thr,
    )
    assert third.market_regime == "STRESSED"
    assert third.market_debounce.hold == 0


def test_hysteresis_holds_stressed_between_enter_and_exit():
    mid = _liquid_stats(stressed_ratio=0.30, liquid_ratio=0.20, spread_med=4.0)
    from_normal, _, _ = propose_market_regime(mid, "NORMAL", INSTANT)
    assert from_normal != "STRESSED"
    from_stressed, trigger, _ = propose_market_regime(mid, "STRESSED", INSTANT)
    assert from_stressed == "STRESSED"
    assert trigger == "STRESSED_HYSTERESIS"


def test_hysteresis_exit_then_debounce_back_to_normal():
    thr = RegimeV2Thresholds(debounce_ticks=3)
    state = DebounceState("STRESSED", "STRESSED", 0)
    calm = _liquid_stats()

    first = classify_regime_v2(calm, market_state=state, thresholds=thr)
    assert first.market_regime == "STRESSED"
    second = classify_regime_v2(calm, market_state=first.market_debounce, thresholds=thr)
    assert second.market_regime == "STRESSED"
    third = classify_regime_v2(calm, market_state=second.market_debounce, thresholds=thr)
    assert third.market_regime == "LIQUID"


def test_score_coverage_hysteresis():
    mid = _liquid_stats(
        books_0_obs=6,
        books_1_remaining=8,
        books_2_remaining=4,
        books_eligible=2,
        activity_deficit=0.30,
        round_trip_velocity=0.01,
    )
    from_coverage, cov_trig, _ = propose_score_regime(mid, "COVERAGE", INSTANT)
    assert from_coverage == "COVERAGE"
    assert cov_trig == "ZERO_OBS_HYSTERESIS"
    from_completion, comp_trig, _ = propose_score_regime(mid, "COMPLETION", INSTANT)
    assert from_completion == "COMPLETION"
    assert comp_trig == "NEAR_QUAL_HYSTERESIS"
    assert score_regime_metrics(mid)["coverage_ratio"] == 0.3


def test_apply_debounce_resets_on_candidate_change():
    state = DebounceState("NORMAL", "STRESSED", 2)
    flipped = apply_debounce(state, "LIQUID", 3)
    assert flipped.current == "NORMAL"
    assert flipped.pending == "LIQUID"
    assert flipped.hold == 1


def test_parent_mode_mapping():
    assert classify_regime_v2(_liquid_stats(), thresholds=INSTANT).parent_mode == "BROAD_LIQUID"
    quiet = classify_regime_v2(
        _liquid_stats(trade_rate_med=0.01, liquid_ratio=0.10),
        thresholds=INSTANT,
    )
    assert quiet.parent_mode == "QUIET"
    stressed = classify_regime_v2(
        _liquid_stats(stressed_ratio=0.45, liquid_ratio=0.10, spread_med=9.0),
        thresholds=INSTANT,
    )
    assert stressed.parent_mode == "STRESSED"
