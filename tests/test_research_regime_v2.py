# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 1: MarketRegime V2 + ScoreRegime unit tests."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_regime_v2 import (
    DebounceState,
    RegimeV2Thresholds,
    apply_debounce,
    classify_regime_v2,
    parent_trigger_cannot_force_stressed,
    propose_market_regime,
    propose_score_regime,
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
    }
    stats.update(overrides)
    return stats


def test_empty_cross_section_is_normal_not_stressed():
    market, trigger, _ = propose_market_regime({"book_count": 0}, "NORMAL", INSTANT)
    assert market == "NORMAL"
    assert trigger == "EMPTY_CROSS_SECTION"


def test_mean_spread_below_parent_5bps_is_not_stressed():
    # Parent classify_market_regime latched STRESSED at mean spread >= 5 bps.
    # V2 uses median vs adaptive P95 floor (8 bps default) plus stressed_ratio.
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
    # Instant debounce so the liquid snapshot is applied on this call.
    decision = classify_regime_v2(
        {**stats, "parent_mode": "STRESSED", "parent_trigger": None, "trigger": None},
        thresholds=INSTANT,
    )
    assert market != "STRESSED"
    assert decision.market_regime == "LIQUID"
    assert decision.market_trigger != "UNEXPOSED_BY_PARENT"
    assert decision.score_regime == "NORMAL"
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


def test_score_regime_independent_of_market():
    coverage, _, _ = propose_score_regime(
        _liquid_stats(inactive=10, inactive_frac=0.50, pending_kappa_frac=0.40),
        "NORMAL",
        INSTANT,
    )
    assert coverage == "COVERAGE_PRESSURE"

    completion, _, _ = propose_score_regime(
        _liquid_stats(inactive_frac=0.05, pending_kappa_frac=0.30),
        "NORMAL",
        INSTANT,
    )
    assert completion == "COMPLETION_PRESSURE"

    mixed = classify_regime_v2(
        _liquid_stats(
            stressed_ratio=0.42,
            liquid_ratio=0.20,
            spread_med=9.0,
            inactive_frac=0.05,
            pending_kappa_frac=0.30,
        ),
        thresholds=INSTANT,
    )
    assert mixed.market_regime == "STRESSED"
    assert mixed.score_regime == "COMPLETION_PRESSURE"
    assert mixed.scoring_overlay is None

    coverage_liquid = classify_regime_v2(
        _liquid_stats(inactive_frac=0.50),
        thresholds=INSTANT,
    )
    assert coverage_liquid.market_regime == "LIQUID"
    assert coverage_liquid.score_regime == "COVERAGE_PRESSURE"
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
    # 0.30 is below enter (0.35) so a NORMAL book stays NORMAL.
    from_normal, _, _ = propose_market_regime(mid, "NORMAL", INSTANT)
    assert from_normal != "STRESSED"
    # Once STRESSED, 0.30 is above exit (0.25) so it stays STRESSED.
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
    # n=20, ratio=0.375 → parent enter at max(int(7.5)-1,1)=6 books (0.30).
    # 5/20 is below enter but at exit, so only a live COVERAGE_PRESSURE holds.
    mid = _liquid_stats(inactive=5, inactive_frac=0.25, pending_kappa_frac=0.0)
    from_normal, _, _ = propose_score_regime(mid, "NORMAL", INSTANT)
    assert from_normal == "NORMAL"
    from_cov, _, _ = propose_score_regime(mid, "COVERAGE_PRESSURE", INSTANT)
    assert from_cov == "COVERAGE_PRESSURE"


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
