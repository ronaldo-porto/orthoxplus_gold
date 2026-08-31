# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Research per-book SN79 volume cap. Isolation from the inherited global sum."""
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from research_entry_size import allowed_entry_size
from research_realization import evaluate_realization, exit_urgency
from research_score_ev import compute_score_ev
from research_volume_cap import (
    CANCEL_INSTRUCTION,
    REASON_CAP_REACHED,
    REASON_INSUFFICIENT_HEADROOM,
    REASON_NO_ACCOUNT,
    REASON_NO_CONFIG,
    REASON_OK,
    agent_book_traded_volume,
    agent_can_add_volume,
    agent_volume_cap_headroom,
    agent_volume_cap_quote,
    agent_volume_cap_reason,
    agent_volume_cap_remaining,
    agent_volume_cap_snapshot,
    aggregate_volume_cap_metrics,
    book_traded_volume,
    can_add_volume,
    validator_admits_instruction,
    volume_cap_headroom,
    volume_cap_quote,
    volume_cap_remaining,
    volume_cap_reason,
)

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)
HELPER_SRC = (ROOT / "agents" / "strategy" / "research_volume_cap.py").read_text(
    encoding="utf-8"
)
CAP = 10.0
WEALTH = 1_000.0
ONE_BOOK_CAP = CAP * WEALTH  # 10_000


def _state(wealth=WEALTH, decimals=2):
    return SimpleNamespace(
        config=SimpleNamespace(miner_wealth=wealth, volumeDecimals=decimals)
    )


def _agent(volumes, wealth=WEALTH, cap=CAP, decimals=2):
    accounts = {
        int(book): SimpleNamespace(traded_volume=used)
        for book, used in volumes.items()
    }
    return SimpleNamespace(
        accounts=accounts,
        capital_turnover_cap=cap,
        _research_volume_decimals=decimals,
        _research_last_miner_wealth=wealth,
    )


def _entry(headroom: float):
    return allowed_entry_size(
        base_size=0.25,
        existing_inventory=0.0,
        max_inventory=1.20,
        inventory_age=0.0,
        volatility=0.001,
        toxicity=0.0,
        expected_markout=0.5,
        volume_cap_headroom=headroom,
        exit_rate=None,
        recent_drawdown=0.0,
        hard_max_entry=0.25,
    )


def test_research_source_uses_explicit_book_id_helpers():
    assert "def _research_volume_cap_quote(self, state)" in RESEARCH_SRC
    assert "def _research_book_traded_volume(self, book_id)" in RESEARCH_SRC
    assert "def _research_volume_cap_remaining(self, state, book_id)" in RESEARCH_SRC
    assert "def _research_volume_cap_headroom(self, state, book_id)" in RESEARCH_SRC
    assert "def _research_can_add_volume(self, state, book_id, quote_notional)" in RESEARCH_SRC
    assert "def _research_volume_cap_headroom(self, state) -> float:" not in RESEARCH_SRC
    assert (
        "_research_volume_headroom_last = self._research_volume_cap_headroom(state)"
        not in RESEARCH_SRC
    )
    assert "sum(account.traded_volume" not in RESEARCH_SRC
    assert "sum(account.traded_volume" not in HELPER_SRC


def test_book_a_near_cap_does_not_block_unused_book_b():
    state = _state()
    agent = _agent({1: 0.99 * ONE_BOOK_CAP, 2: 0.0})
    assert agent_can_add_volume(agent, state, 1, 200.0) is False
    assert agent_can_add_volume(agent, state, 2, 200.0) is True
    assert agent_volume_cap_reason(agent, state, 1, 200.0) == REASON_INSUFFICIENT_HEADROOM
    assert agent_volume_cap_reason(agent, state, 2, 200.0) == REASON_OK


def test_book_a_at_cap_book_b_at_half_still_allowed():
    state = _state()
    agent = _agent({1: ONE_BOOK_CAP, 2: 0.50 * ONE_BOOK_CAP})
    assert agent_can_add_volume(agent, state, 1, 1.0) is False
    assert agent_can_add_volume(agent, state, 2, 100.0) is True
    assert agent_volume_cap_reason(agent, state, 1, 1.0) == REASON_CAP_REACHED
    assert agent_volume_cap_headroom(agent, state, 1) == 0.0
    assert abs(agent_volume_cap_headroom(agent, state, 2) - 0.50) < 1e-12


def test_total_volume_above_one_book_cap_does_not_block_healthy_books():
    """Guard the inherited global-sum bug: used_A + used_B + used_C > cap, each < cap."""
    state = _state()
    used = 0.40 * ONE_BOOK_CAP
    agent = _agent({1: used, 2: used, 3: used})
    assert sum(used for used in (used, used, used)) > ONE_BOOK_CAP
    for book in (1, 2, 3):
        assert agent_book_traded_volume(agent, book) < ONE_BOOK_CAP
        assert agent_can_add_volume(agent, state, book, 100.0) is True
        assert agent_volume_cap_headroom(agent, state, book) > 0.50


def test_requested_notional_above_remaining_blocks_only_that_book():
    state = _state()
    agent = _agent({1: 0.80 * ONE_BOOK_CAP, 2: 0.0})
    remaining_a = agent_volume_cap_remaining(agent, state, 1)
    assert remaining_a == 0.20 * ONE_BOOK_CAP
    assert agent_can_add_volume(agent, state, 1, remaining_a + 1.0) is False
    assert agent_can_add_volume(agent, state, 1, remaining_a) is True
    assert agent_can_add_volume(agent, state, 2, remaining_a + 1.0) is True
    assert agent_volume_cap_reason(agent, state, 1, remaining_a + 1.0) == (
        REASON_INSUFFICIENT_HEADROOM
    )


def test_cancellation_remains_allowed_when_book_is_capped():
    assert validator_admits_instruction(
        capital_turnover_cap=CAP,
        miner_wealth=WEALTH,
        traded_volume=ONE_BOOK_CAP,
        instruction_type=CANCEL_INSTRUCTION,
        volume_decimals=2,
    ) is True
    assert validator_admits_instruction(
        capital_turnover_cap=CAP,
        miner_wealth=WEALTH,
        traded_volume=ONE_BOOK_CAP,
        instruction_type="PLACE_ORDER_LIMIT",
        volume_decimals=2,
    ) is False
    assert validator_admits_instruction(
        capital_turnover_cap=CAP,
        miner_wealth=WEALTH,
        traded_volume=ONE_BOOK_CAP,
        instruction_type="PLACE_ORDER_MARKET",
        volume_decimals=2,
    ) is False


def test_missing_traded_volume_is_treated_as_zero():
    assert book_traded_volume(None) == 0.0
    assert book_traded_volume(SimpleNamespace(traded_volume=None)) == 0.0
    assert book_traded_volume(SimpleNamespace()) == 0.0
    assert book_traded_volume(SimpleNamespace(traded_volume="bad")) == 0.0
    assert book_traded_volume(SimpleNamespace(traded_volume=-12.0)) == 0.0
    agent = _agent({7: None})
    state = _state()
    assert agent_book_traded_volume(agent, 7) == 0.0
    assert agent_can_add_volume(agent, state, 7, 50.0) is True
    assert agent_volume_cap_headroom(agent, state, 99) == 1.0
    assert agent_volume_cap_reason(agent, state, 99) == REASON_NO_ACCOUNT


def test_cap_and_headroom_are_bounded():
    assert volume_cap_quote(capital_turnover_cap=None, miner_wealth=WEALTH) == 0.0
    assert volume_cap_quote(capital_turnover_cap=-1.0, miner_wealth=WEALTH) == 0.0
    assert volume_cap_quote(capital_turnover_cap=CAP, miner_wealth=None) == 0.0
    assert volume_cap_headroom(0.0, 100.0) == 1.0
    assert volume_cap_headroom(-5.0, 100.0) == 1.0
    assert volume_cap_headroom(100.0, -20.0) == 1.0
    assert volume_cap_headroom(100.0, 0.0) == 1.0
    assert volume_cap_headroom(100.0, 150.0) == 0.0
    assert volume_cap_remaining(100.0, 150.0) == 0.0
    assert 0.0 <= volume_cap_headroom(100.0, 25.0) <= 1.0
    assert abs(volume_cap_headroom(100.0, 25.0) - 0.75) < 1e-12
    assert can_add_volume(cap_quote=0.0, used=0.0, quote_notional=1e9) is True
    assert volume_cap_reason(
        cap_quote=0.0, used=0.0, has_config=False,
    ) == REASON_NO_CONFIG


def test_entry_size_uses_book_specific_headroom():
    state = _state()
    agent = _agent({1: 0.90 * ONE_BOOK_CAP, 2: 0.0})
    size_a = _entry(agent_volume_cap_headroom(agent, state, 1))
    size_b = _entry(agent_volume_cap_headroom(agent, state, 2))
    assert size_a.entry_size < size_b.entry_size
    assert size_a.volume_headroom_factor < size_b.volume_headroom_factor
    assert abs(size_b.volume_headroom_factor - 1.0) < 1e-12


def test_score_ev_receives_different_headroom_per_book():
    healthy = compute_score_ev(
        book=1,
        alpha=0.30,
        fill_prob_old=0.40,
        spread_capture_bps=6.0,
        realized_observation_count=2,
        required=3,
        volume_cap_headroom=1.0,
    )
    tight = compute_score_ev(
        book=2,
        alpha=0.30,
        fill_prob_old=0.40,
        spread_capture_bps=6.0,
        realized_observation_count=2,
        required=3,
        volume_cap_headroom=0.10,
    )
    dead = compute_score_ev(
        book=3,
        alpha=0.30,
        fill_prob_old=0.40,
        spread_capture_bps=6.0,
        realized_observation_count=2,
        required=3,
        volume_cap_headroom=0.0,
    )
    assert healthy.volume_cap_headroom != tight.volume_cap_headroom
    assert healthy.eligible and tight.eligible
    assert dead.eligible is False
    assert dead.reject_reason == "VOLUME_CAP"
    assert healthy.final_score > dead.final_score


def test_kappa_completion_candidate_not_blocked_by_other_book_cap():
    """One-away book with unused cap stays eligible while another book is capped."""
    completion = compute_score_ev(
        book=4,
        alpha=0.30,
        fill_prob_old=0.40,
        spread_capture_bps=6.0,
        realized_observation_count=2,
        required=3,
        volume_cap_headroom=1.0,
    )
    other_capped = compute_score_ev(
        book=5,
        alpha=0.30,
        fill_prob_old=0.40,
        spread_capture_bps=6.0,
        realized_observation_count=0,
        required=3,
        volume_cap_headroom=0.0,
    )
    assert completion.observations_remaining == 1
    assert completion.eligible is True
    assert completion.lane == "COMPLETION"
    assert other_capped.eligible is False
    assert completion.final_score > other_capped.final_score
    state = _state()
    agent = _agent({4: 0.0, 5: ONE_BOOK_CAP})
    assert agent_can_add_volume(agent, state, 4, 250.0) is True
    assert agent_can_add_volume(agent, state, 5, 250.0) is False
    assert "volume_cap" in RESEARCH_SRC


def test_validator_parity_per_book_cap():
    """Research admission agrees with taos/im/validator/query.py per-book semantics."""
    decimals = 2
    cap = volume_cap_quote(
        capital_turnover_cap=CAP,
        miner_wealth=WEALTH,
        volume_decimals=decimals,
    )
    assert cap == round(CAP * WEALTH, decimals)
    volumes = {1: cap, 2: 0.50 * cap, 3: 0.0}
    for book, used in volumes.items():
        place = validator_admits_instruction(
            capital_turnover_cap=CAP,
            miner_wealth=WEALTH,
            traded_volume=used,
            instruction_type="PLACE_ORDER_LIMIT",
            volume_decimals=decimals,
        )
        cancel = validator_admits_instruction(
            capital_turnover_cap=CAP,
            miner_wealth=WEALTH,
            traded_volume=used,
            instruction_type=CANCEL_INSTRUCTION,
            volume_decimals=decimals,
        )
        miner = can_add_volume(cap_quote=cap, used=used, quote_notional=1.0)
        assert cancel is True
        if used >= cap:
            assert place is False
            assert miner is False
        else:
            assert place is True
            assert miner is True
    agent = _agent(volumes, decimals=decimals)
    state = _state(decimals=decimals)
    assert agent_volume_cap_quote(agent, state) == cap
    assert agent_can_add_volume(agent, state, 1, 1.0) is False
    assert agent_can_add_volume(agent, state, 2, 1.0) is True
    assert agent_can_add_volume(agent, state, 3, 1.0) is True


def test_realization_uses_the_same_book_headroom():
    low = evaluate_realization(
        book=1,
        inventory_size=0.20,
        inventory_ratio=0.18,
        inventory_age=8.0,
        unrealized_pnl=2.0,
        expected_markout=0.4,
        volatility=0.001,
        observations_remaining=0,
        volume_cap_headroom=0.05,
        fee_bps=1.0,
        spread_bps=2.5,
        slippage_bps=3.0,
        band="LONG",
    )
    high = evaluate_realization(
        book=2,
        inventory_size=0.20,
        inventory_ratio=0.18,
        inventory_age=8.0,
        unrealized_pnl=2.0,
        expected_markout=0.4,
        volatility=0.001,
        observations_remaining=0,
        volume_cap_headroom=0.90,
        fee_bps=1.0,
        spread_bps=2.5,
        slippage_bps=3.0,
        band="LONG",
    )
    assert low.exit_urgency > high.exit_urgency
    assert exit_urgency(
        inventory_size=0.20,
        inventory_ratio=0.18,
        inventory_age=8.0,
        unrealized_pnl=2.0,
        expected_markout=0.4,
        volatility=0.001,
        inventory_sign=1.0,
        kappa_need=0.0,
        volume_cap_headroom=0.05,
        recent_realized_pnl=0.01,
        adverse_selection_risk=0.02,
    ) != exit_urgency(
        inventory_size=0.20,
        inventory_ratio=0.18,
        inventory_age=8.0,
        unrealized_pnl=2.0,
        expected_markout=0.4,
        volatility=0.001,
        inventory_sign=1.0,
        kappa_need=0.0,
        volume_cap_headroom=0.90,
        recent_realized_pnl=0.01,
        adverse_selection_risk=0.02,
    )


def test_aggregate_headroom_metrics():
    metrics = aggregate_volume_cap_metrics([0.0, 0.08, 0.20, 1.0])
    assert metrics["books_cap_reached"] == 1
    assert metrics["books_headroom_lt_10pct"] == 2
    assert metrics["books_headroom_lt_25pct"] == 3
    assert metrics["min_headroom"] == 0.0
    assert metrics["median_headroom"] == 0.14
    snap = agent_volume_cap_snapshot(
        _agent({1: ONE_BOOK_CAP, 2: 0.0, 3: 0.50 * ONE_BOOK_CAP}),
        _state(),
    )
    assert snap["books_cap_reached"] == 1
    assert snap["min_headroom"] == 0.0
    assert snap["book_count"] == 3
