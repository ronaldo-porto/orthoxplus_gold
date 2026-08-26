from research_contract_guard import (
    CONTRACT_GUARD_VERSION,
    HARD_LIFETIME_TICKS,
    ContractRejectState,
    guard_is_active,
    guard_should_skip,
    guarded_post_only_price,
    register_contract_reject,
)


def test_first_reject_blocks_immediate_retry_then_allows_retry():
    state = register_contract_reject(None, current_tick=100)
    assert state.streak == 1
    assert state.first_reject_tick == 100
    assert state.blocked_until_tick == 101
    assert guard_should_skip(state, current_tick=101)
    assert not guard_should_skip(state, current_tick=102)


def test_repeated_reject_backoff_is_bounded():
    state = None
    now = 10
    cooldowns = []
    for _ in range(8):
        state = register_contract_reject(state, current_tick=now)
        cooldowns.append(state.blocked_until_tick - now)
        now += 1
    assert cooldowns[:4] == [1, 2, 4, 8]
    assert max(cooldowns) == 8


def test_pending_guard_survives_old_32_tick_gap():
    state = register_contract_reject(None, current_tick=44)
    assert guard_is_active(state, current_tick=77)
    state2 = register_contract_reject(state, current_tick=77)
    assert state2.streak == 2
    assert state2.first_reject_tick == 44
    assert state2.blocked_until_tick == 79


def test_guard_uses_separate_hard_lifetime():
    state = ContractRejectState(
        streak=3,
        first_reject_tick=10,
        last_reject_tick=100,
        blocked_until_tick=108,
    )
    assert HARD_LIFETIME_TICKS == 512
    assert guard_is_active(state, current_tick=522)
    assert not guard_is_active(state, current_tick=523)


def test_sell_retry_moves_above_current_ask_and_preserves_more_passive_price():
    p = guarded_post_only_price(
        side="sell", original_price=100.01, best_bid=100.00, best_ask=100.05,
        tick_size=0.01, reject_streak=1,
    )
    assert abs(p - 100.06) < 1e-12
    p2 = guarded_post_only_price(
        side="sell", original_price=100.20, best_bid=100.00, best_ask=100.05,
        tick_size=0.01, reject_streak=3,
    )
    assert abs(p2 - 100.20) < 1e-12


def test_buy_retry_moves_below_current_bid():
    p = guarded_post_only_price(
        side="buy", original_price=99.99, best_bid=99.95, best_ask=100.00,
        tick_size=0.01, reject_streak=2,
    )
    assert abs(p - 99.93) < 1e-12


def test_market_and_normal_maker_logic_not_part_of_pure_guard_contract():
    assert CONTRACT_GUARD_VERSION == "authoritative_l1_contract_guard_v4_12_14"


def test_strategy_source_wires_pending_guard_and_lifecycle_clear():
    from pathlib import Path
    source = (Path(__file__).parents[1] / "agents" / "strategy" / "Strategy1_Research.py").read_text()
    assert 'RESEARCH_POLICY_VERSION = "kappa_flywheel_v4_12_17"' in source
    assert 'def onOrderRejected(self, event)' in source
    assert 'reason != "CONTRACT_VIOLATION"' in source
    assert 'contract_guard = self._research_apply_contract_reject_guard(response, state)' in source
    assert 'if not self._research_instruction_is_maker(instruction)' in source
    assert 'action="NO_TOUCH_SKIP"' in source
    assert 'pending_reprice=1' in source
    assert 'action="HARD_EXPIRE_CLEAR"' in source
    assert 'def _research_clear_contract_guards_for_book' in source
    assert 'transition in {"FLAT", "CROSS"}' in source
    assert 'self._research_contract_reject_state.clear()' in source
