# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 4: Score-EV ranking and Kappa completion scheduler."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_score_ev import (
    LANE_COMPLETION,
    LANE_COVERAGE,
    LANE_NORMAL,
    admit_scheduler_candidate,
    book_observation_state,
    classify_scheduler_lane,
    compute_score_ev,
    completion_value,
    hard_safety_blocks,
    required_observation_count,
    round_trip_velocity,
    scheduler_bucket_counts,
    score_velocity_priority,
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


def test_score_velocity_priority_one_away_beats_two_away_and_coverage():
    econ = dict(alpha=0.30, fill_prob_old=0.40, spread_capture_bps=6.0, fees_bps=0.5)
    one = score_velocity_priority(book=1, realized_observation_count=2, required=3, **econ)
    two = score_velocity_priority(book=2, realized_observation_count=1, required=3, **econ)
    cover = score_velocity_priority(book=3, realized_observation_count=0, required=3, **econ)
    assert one.lane == LANE_COMPLETION
    assert two.lane == LANE_COMPLETION
    assert cover.lane == LANE_COVERAGE
    assert one.completion_value > two.completion_value > cover.completion_value
    assert one.final_priority > two.final_priority > cover.final_priority
    state = book_observation_state(
        realized_observation_count=2,
        required_observations=3,
        expected_trade_ev=one.trading_ev,
        inventory_state="FLAT",
    )
    assert state["observations_remaining"] == 1
    assert state["required_observations"] == 3
    assert state["eligible"] is False
    qualified = book_observation_state(
        realized_observation_count=3,
        required_observations=3,
    )
    assert qualified["observations_remaining"] == 0
    assert qualified["eligible"] is True


def test_unsafe_and_invalid_size_reject_completion():
    unsafe = _base(realized_observation_count=2, unsafe=True)
    bad_size = _base(realized_observation_count=2, invalid_size=True)
    capped = _base(realized_observation_count=2, volume_capped=True)
    assert unsafe.reject_reason == "UNSAFE"
    assert bad_size.reject_reason == "INVALID_SIZE"
    assert capped.reject_reason == "VOLUME_CAP"
    assert hard_safety_blocks(invalid_size=True, trading_ev_value=1.0) == "INVALID_SIZE"
    assert hard_safety_blocks(volume_capped=True, trading_ev_value=1.0) == "VOLUME_CAP"


def test_completion_quota_not_starved_by_normal_limits():
    """Normal books filling the global MM cap must not consume reserved completion slots."""
    admit, reason = admit_scheduler_candidate(
        lane=LANE_COMPLETION,
        quote_successes=4,
        quote_success_cap=4,
        completion_attempts=0,
        completion_attempt_cap=4,
        completion_successes=0,
        completion_success_cap=2,
        normal_attempts=8,
        normal_attempt_cap=8,
    )
    assert admit is True
    assert reason is None

    blocked_normal, normal_reason = admit_scheduler_candidate(
        lane=LANE_NORMAL,
        quote_successes=2,
        quote_success_cap=4,
        completion_attempts=0,
        completion_attempt_cap=4,
        completion_successes=0,
        completion_success_cap=2,
        normal_attempts=0,
        normal_attempt_cap=8,
    )
    assert blocked_normal is False
    assert normal_reason == "MM_SUCCESS_CAP"

    coverage, _ = admit_scheduler_candidate(
        lane=LANE_COVERAGE,
        quote_successes=2,
        quote_success_cap=4,
        completion_attempts=0,
        completion_attempt_cap=4,
        completion_successes=0,
        completion_success_cap=2,
        normal_attempts=0,
        normal_attempt_cap=8,
    )
    assert coverage is False

    after_reserve, after_reason = admit_scheduler_candidate(
        lane=LANE_COMPLETION,
        quote_successes=4,
        quote_success_cap=4,
        completion_attempts=0,
        completion_attempt_cap=4,
        completion_successes=2,
        completion_success_cap=2,
        normal_attempts=8,
        normal_attempt_cap=8,
    )
    assert after_reserve is False
    assert after_reason == "KAPPA_COMPLETION_SUCCESS_CAP"


def test_round_trip_velocity_and_lane_helpers():
    assert round_trip_velocity(6, 2.0) == 3.0
    assert round_trip_velocity(6, 0.0) == 0.0
    assert classify_scheduler_lane(0, 3) == LANE_COVERAGE
    assert classify_scheduler_lane(1, 3) == LANE_COMPLETION
    assert classify_scheduler_lane(3, 3) == LANE_NORMAL
    assert classify_scheduler_lane(2, 4) == LANE_COMPLETION


def test_scheduler_bucket_counts():
    counts = scheduler_bucket_counts(
        {1: 0, 2: 0, 3: 1, 4: 2, 5: 3, 6: 2},
        required=3,
        eligible_ids={3, 4, 6},
    )
    assert counts["books_zero_obs"] == 2
    assert counts["books_0_obs"] == 2
    assert counts["books_one_remaining"] == 2
    assert counts["books_1_remaining"] == 2
    assert counts["books_two_remaining"] == 1
    assert counts["books_2_remaining"] == 1
    assert counts["eligible_books"] == 1
    assert counts["books_eligible"] == 1
    assert counts["required_observation_count"] == 3


def test_volume_headroom_is_per_book_and_does_not_block_healthy_kappa():
    one_away = _base(
        book=1,
        realized_observation_count=2,
        required=3,
        volume_cap_headroom=1.0,
    )
    capped = _base(
        book=2,
        realized_observation_count=0,
        required=3,
        volume_cap_headroom=0.0,
    )
    half = _base(
        book=3,
        realized_observation_count=0,
        required=3,
        volume_cap_headroom=0.50,
    )
    assert one_away.volume_cap_headroom == 1.0
    assert half.volume_cap_headroom == 0.50
    assert one_away.eligible is True
    assert half.eligible is True
    assert capped.eligible is False
    assert capped.reject_reason == "VOLUME_CAP"
    assert one_away.final_score > capped.final_score


def test_true_score_velocity_prefers_faster_empirical_realization():
    econ = dict(
        alpha=0.30,
        fill_prob_old=0.40,
        spread_capture_bps=6.0,
        fees_bps=0.5,
        realized_observation_count=2,
        required=3,
        realization_time_reference=100.0,
        score_velocity_weight=0.10,
        enable_score_velocity=True,
    )
    fast = score_velocity_priority(
        book=101, expected_realization_time=50.0, **econ
    )
    slow = score_velocity_priority(
        book=102, expected_realization_time=200.0, **econ
    )
    assert fast.score_velocity_value > slow.score_velocity_value
    assert fast.final_priority > slow.final_priority
    assert fast.expected_realization_time == 50.0
    assert slow.expected_realization_time == 200.0


def test_score_velocity_flag_can_disable_bonus():
    base = score_velocity_priority(
        book=103,
        realized_observation_count=2,
        required=3,
        alpha=0.30,
        fill_prob_old=0.40,
        spread_capture_bps=6.0,
        fees_bps=0.5,
        expected_realization_time=50.0,
        realization_time_reference=100.0,
        score_velocity_weight=0.20,
        enable_score_velocity=False,
    )
    assert base.score_velocity_value == 0.0
