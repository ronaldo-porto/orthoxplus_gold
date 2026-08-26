from research_contract_guard import (
    ContractRejectState,
    guard_is_active,
    guard_should_skip,
    guarded_post_only_price,
    register_contract_reject,
)


def test_book17_like_33_tick_gap_keeps_pending_episode():
    # Mirrors the runtime pattern 44 -> 77 where V4.12.12 reset to streak=1.
    first = register_contract_reject(None, current_tick=44)
    assert guard_is_active(first, current_tick=77)
    second = register_contract_reject(first, current_tick=77)
    assert second.streak == 2
    assert second.first_reject_tick == 44
    assert guard_should_skip(second, current_tick=78)
    assert guard_should_skip(second, current_tick=79)
    assert not guard_should_skip(second, current_tick=80)


def test_book54_like_long_gaps_escalate_without_reset():
    state = None
    for tick, expected_streak in zip((709, 742, 776, 809), (1, 2, 3, 4)):
        state = register_contract_reject(state, current_tick=tick)
        assert state.streak == expected_streak
        assert state.first_reject_tick == 709
    assert state.blocked_until_tick == 817  # 8-tick capped cooldown


def test_pending_state_does_not_depend_on_last_reject_age():
    state = ContractRejectState(
        streak=2,
        first_reject_tick=100,
        last_reject_tick=101,
        blocked_until_tick=103,
    )
    # 199 ticks since last reject, but still inside the episode hard lifetime.
    assert guard_is_active(state, current_tick=300)
    assert not guard_should_skip(state, current_tick=300)


def test_fresh_touch_retry_reprices_after_long_pending_gap():
    state = register_contract_reject(None, current_tick=10)
    assert guard_is_active(state, current_tick=80)
    assert not guard_should_skip(state, current_tick=80)
    price = guarded_post_only_price(
        side="sell",
        original_price=309.03,
        best_bid=309.02,
        best_ask=309.40,
        tick_size=0.01,
        reject_streak=state.streak,
    )
    assert abs(price - 309.41) < 1e-12


def test_hard_lifetime_eventually_fails_open_to_normal_policy():
    state = register_contract_reject(None, current_tick=10)
    assert guard_is_active(state, current_tick=522)
    assert not guard_is_active(state, current_tick=523)
