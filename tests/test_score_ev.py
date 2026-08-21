# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Production Score-EV ranking and Kappa completion scheduler."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from score_ev import (
    compute_score_ev,
    completion_value,
    hard_safety_blocks,
    legacy_global_rank,
    required_observation_count,
    scheduler_bucket_counts,
    select_rank,
    trading_ev,
)


def _base(**kwargs):
    defaults = dict(
        book=1,
        side="BUY",
        alpha=0.30,
        fill_prob_old=0.40,
        spread_capture_bps=6.0,
        fees_bps=0.5,
        realized_observation_count=0,
        required=3,
        dust_prob=0.10,
        min_fill_samples=8,
        min_markout_samples=8,
    )
    defaults.update(kwargs)
    return compute_score_ev(**defaults)


def test_one_away_outranks_new_book_under_equal_economics():
    one_away = _base(realized_observation_count=2, required=3)
    new_book = _base(book=2, realized_observation_count=0, required=3)
    assert one_away.observations_remaining == 1
    assert new_book.observations_remaining == 3
    assert one_away.eligible and new_book.eligible
    assert one_away.completion_value > new_book.completion_value
    assert one_away.final_score > new_book.final_score
    two_away = _base(book=3, realized_observation_count=1, required=3)
    assert one_away.final_score > two_away.final_score > new_book.final_score
    assert one_away.completion_value == 0.18
    assert two_away.completion_value == 0.06
    assert new_book.completion_value == 0.0


def test_negative_ev_one_away_book_is_rejected():
    result = _base(
        realized_observation_count=2,
        required=3,
        spread_capture_bps=0.2,
        markout_mean_bps=-8.0,
        markout_samples=20,
        fees_bps=2.0,
        fill_prob_old=0.80,
        learned_actionable_p=0.80,
        learned_actionable_samples=20,
        min_trading_ev=0.0,
    )
    assert result.trading_ev < 0.0
    assert result.eligible is False
    assert result.reject_reason == "NEGATIVE_EV"
    assert result.observations_remaining == 1
    assert result.final_score == float("-inf")


def test_toxic_book_remains_blocked():
    result = _base(
        realized_observation_count=2,
        required=3,
        toxic=True,
        spread_capture_bps=10.0,
    )
    assert result.eligible is False
    assert result.reject_reason == "TOXIC"
    assert hard_safety_blocks(toxic=True, trading_ev_value=1.0) == "TOXIC"


def test_configurable_observation_threshold():
    assert required_observation_count(research_target=4) == 4
    assert required_observation_count(kappa_min_observations=5) == 5
    assert required_observation_count(research_target=2, kappa_min_observations=5) == 2
    assert required_observation_count() == 3
    a = _base(realized_observation_count=3, required=4)
    b = _base(book=2, realized_observation_count=0, required=4)
    assert a.observations_remaining == 1
    assert a.required_observation_count == 4
    assert a.observation_count == 3
    assert a.final_score > b.final_score


def test_high_dust_probability_lowers_priority():
    clean = _base(book=1, dust_prob=0.05)
    dusty = _base(book=2, dust_prob=0.55)
    assert dusty.dust_cost > clean.dust_cost
    assert dusty.final_score < clean.final_score


def test_hard_safety_always_wins():
    huge_completion = completion_value(observations_remaining=1, required_observation_count=3)
    assert huge_completion > 0.0
    blocked = _base(
        realized_observation_count=2,
        inventory_blocked=True,
        spread_capture_bps=12.0,
    )
    unsafe = _base(realized_observation_count=2, unsafe=True)
    assert blocked.reject_reason == "INVENTORY_BLOCKED"
    assert unsafe.reject_reason == "UNSAFE"
    assert blocked.final_score == float("-inf")
    assert select_rank(
        enable_score_ev=True, score_ev=blocked, legacy_rank=9.0,
    ) is None


def test_phase1_trading_ev_formula_is_unchanged():
    p = 0.55
    spread = 6.0
    markout = 0.0
    fees = 0.5
    expected = trading_ev(
        actionable_fill_prob=p,
        spread_capture_bps=spread,
        expected_markout_bps=markout,
        fees_bps=fees,
        edge_scale_bps=8.0,
    )
    result = _base(
        fill_prob_old=p,
        learned_actionable_p=p,
        learned_actionable_samples=20,
        spread_capture_bps=spread,
        fees_bps=fees,
        realized_observation_count=3,
        required=3,
    )
    assert abs(result.trading_ev - expected) < 1e-12
    # Alpha is recorded for telemetry; it is not an extra untested coefficient.
    assert result.alpha == 0.30
    assert abs(
        result.final_score
        - (result.trading_ev + result.completion_value - result.dust_cost
           - result.inventory_cost - result.latency_cost)
    ) < 1e-12


def test_feature_flag_restores_legacy_ranking():
    score = _base(realized_observation_count=2, required=3)
    legacy = legacy_global_rank(0.30, 0.0)
    assert select_rank(
        enable_score_ev=False, score_ev=score, legacy_rank=legacy,
    ) == legacy
    assert select_rank(
        enable_score_ev=True, score_ev=score, legacy_rank=legacy,
    ) == score.final_score


def test_scheduler_bucket_counts():
    counts = scheduler_bucket_counts(
        {1: 0, 2: 0, 3: 1, 4: 2, 5: 3, 6: 2},
        required=3,
        eligible_ids={3, 4, 6},
    )
    assert counts["books_zero_obs"] == 2
    assert counts["books_one_remaining"] == 2
    assert counts["books_two_remaining"] == 1
    assert counts["eligible_books"] == 3
    assert counts["required_observation_count"] == 3
