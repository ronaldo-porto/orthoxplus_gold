# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""STEP 17 contract: named Research behaviors remain covered."""
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text(
    encoding="utf-8"
)

from research_dust_economics import evaluate_dust_action
from research_entry_size import ADMISSION_NEAR_SAFE, ADMISSION_UNSAFE, admit_minimum_order
from research_execution_lanes import (
    LANE_COVERAGE,
    LaneBook,
    normalize_lane_budgets,
    select_lane_candidates,
)
from research_exit_hazard_ev import REASON_MAKER_EV, REASON_TAKER_EV, compare_maker_taker_exit
from research_exit_quantity import REASON_EXACT, REASON_REJECT_LARGER_OPPOSITE, choose_reduce_quantity
from research_fill_hazard import HazardPrediction
from research_hybrid import hybrid_taker_decision
from research_inventory_state import (
    STATE_DEFENSIVE,
    apply_exit_action_for_state,
    classify_inventory_state,
    inventory_state_policy,
    side_size_multiplier,
)
from research_kappa_state import build_kappa_universe, kappa_book_state
from research_markout import MidHistory, ms_to_ns
from research_realization import ACTION_AGGRESSIVE, ACTION_TAKER, evaluate_realization
from research_same_side import apply_fill_priority, same_side_suppression
from research_session_state import ACTION_RESET, decide_session, SessionIdentity
from research_taker_economics import is_catastrophic_hard_risk


def test_simulation_transition_precedes_trading():
    handle = RESEARCH_SRC.split("def handle(")[1].split("def respond(")[0]
    assert handle.index("self._research_sync_session(state)") < handle.index(
        "response = super().handle(state)"
    )


def test_no_stale_taker_after_transition():
    blocked = hybrid_taker_decision(
        hard_emergency=True,
        unrealized_pnl_bps=-40.0,
        crossing_cost_bps=8.0,
        transition_quarantine=True,
    )
    assert blocked.take is False
    current = SessionIdentity(simulation_id="b", network="test", netuid=1, schema=1)
    bound = SessionIdentity(simulation_id="a", network="test", netuid=1, schema=1)
    decision = decide_session(
        current=current, bound=bound, disk=None,
        live_observations={1: 2}, live_round_trip_samples={}, live_round_trip_closes=0,
    )
    assert decision.action == ACTION_RESET


def test_coverage_lane_receives_reserved_slots_and_inventory_cannot_consume_all():
    books = [
        LaneBook(book_id=i, has_inventory=True, exit_urgency=0.20)
        for i in range(16)
    ]
    books.extend(LaneBook(book_id=100 + i, is_uncovered=True, cheap_score=0.80) for i in range(8))
    result = select_lane_candidates(
        books,
        normalize_lane_budgets(
            coverage_slots=8, completion_slots=4, realization_slots=4, shared_overflow_slots=0,
        ),
    )
    assert len(result.by_lane[LANE_COVERAGE]) == 8
    assert set(result.by_lane[LANE_COVERAGE]) <= set(range(100, 108))
    log = result.as_log()
    assert log["coverage_used"] == 8
    assert log["realization_used"] < 16
    assert log["realization_used"] + log["coverage_used"] < 16 + 8


def test_safe_uncovered_book_can_enter():
    books = [
        LaneBook(book_id=1, is_uncovered=True, maker_ev=0.5, cheap_score=0.01),
        LaneBook(book_id=2, maker_ev=2.0, cheap_score=0.99),
    ]
    result = select_lane_candidates(
        books, normalize_lane_budgets(
            coverage_slots=1, completion_slots=0, realization_slots=0, shared_overflow_slots=0,
        ),
    )
    assert 1 in result.selected
    assert result.by_lane[LANE_COVERAGE] == [1]


def test_near_safe_min_order_admission_and_unsafe_reject():
    near = admit_minimum_order(
        safe_size=0.22, min_order=0.25, tolerance=0.20, trading_ev=0.04,
        inventory_risk=0.12, exit_capacity=0.30, volume_headroom=0.80,
        remaining_inventory=1.20, enable_near_safe=True,
    )
    unsafe = admit_minimum_order(
        safe_size=0.10, min_order=0.25, tolerance=0.20, trading_ev=1.0,
        inventory_risk=0.0, exit_capacity=1.0, volume_headroom=1.0,
        remaining_inventory=1.20, enable_near_safe=True,
    )
    assert near.band == ADMISSION_NEAR_SAFE
    assert near.allow is True
    assert unsafe.band == ADMISSION_UNSAFE
    assert unsafe.allow is False


def test_long_inventory_suppresses_buy_and_improves_sell():
    policy = inventory_state_policy("CAUTION")
    supp = same_side_suppression("CAUTION")
    assert side_size_multiplier(side="buy", inventory_sign=1.0, policy=policy) < 1.0
    assert side_size_multiplier(side="sell", inventory_sign=1.0, policy=policy) >= 1.0
    buy, sell = apply_fill_priority(
        buy_fill=0.40, sell_fill=0.40, inventory_sign=1.0, suppression=supp,
    )
    assert buy < 0.40
    assert sell > 0.40


def test_defensive_prefers_aggressive_maker_before_taker():
    state = classify_inventory_state(
        inventory_ratio=0.50, inventory_size=0.60, band="LONG",
    )
    assert state == STATE_DEFENSIVE
    policy = inventory_state_policy(STATE_DEFENSIVE)
    assert policy.allow_aggressive_maker is True
    action, reason = apply_exit_action_for_state(
        state=STATE_DEFENSIVE, selected_action="PASSIVE_MAKER_EXIT",
    )
    assert action == ACTION_AGGRESSIVE
    assert action != ACTION_TAKER


def test_urgency_alone_cannot_force_taker():
    decision = hybrid_taker_decision(
        unrealized_pnl_bps=-2.0,
        maker_exit_ev=1.2,
        crossing_cost_bps=6.0,
        inventory_age=40.0,
        urgency=0.90,
        maker_fill_hazard=0.40,
    )
    assert decision.take is False


def test_positive_economic_taker_allowed_negative_rejected():
    pred = HazardPrediction(
        any_fill=0.06, actionable_fill=0.03, dust=0.02, source="cell",
        usable=True, n_at_risk=40, ttl_ms=500.0, remaining_any_fill=0.06,
    )
    take = compare_maker_taker_exit(
        prediction=pred, maker_profit=2.0, holding_cost=8.0,
        immediate_realization_value=7.0, taker_cost=2.0,
    )
    stay = compare_maker_taker_exit(
        prediction=HazardPrediction(
            any_fill=0.90, actionable_fill=0.85, dust=0.02, source="cell",
            usable=True, n_at_risk=40, ttl_ms=500.0, remaining_any_fill=0.90,
        ),
        maker_profit=8.0, holding_cost=10.0,
        immediate_realization_value=6.0, taker_cost=5.0,
    )
    assert take.prefer_taker is True
    assert take.reason == REASON_TAKER_EV
    assert stay.prefer_taker is False
    assert stay.reason == REASON_MAKER_EV


def test_hard_risk_override_remains_available():
    assert is_catastrophic_hard_risk(
        stop_loss_hit=True, band="MAX_LONG", inventory_ratio=0.98, unrealized_pnl=-40.0,
    ) is True
    decision = evaluate_realization(
        book=2, inventory_size=1.10, inventory_ratio=0.98, inventory_age=12.0,
        unrealized_pnl=-40.0, fee_bps=8.0, spread_bps=10.0, slippage_bps=6.0,
        band="MAX_LONG", stop_loss_hit=True, hard_emergency=True,
    )
    assert decision.selected_action == ACTION_TAKER


def test_precise_legal_reduce_and_no_unnecessary_increase():
    exact = choose_reduce_quantity(
        inventory=0.40, desired=0.40, min_order=0.25, volume_decimals=4,
    )
    blocked = choose_reduce_quantity(
        inventory=0.10, desired=0.10, min_order=0.25, volume_decimals=4,
    )
    assert exact.quantity == 0.40
    assert exact.reason == REASON_EXACT
    assert blocked.quantity == 0.0
    assert blocked.reason == REASON_REJECT_LARGER_OPPOSITE


def test_loss_making_cross_dust_blocked():
    decision = evaluate_dust_action(
        inventory=0.18, min_order=0.25, reduce_qty=0.25, spread_bps=1.0, fee_bps=8.0,
        slippage_bps=6.0, expected_markout=-12.0, unrealized_pnl=-20.0,
        age_ticks=80.0, volatility=0.008, inventory_ratio=0.15,
    )
    assert decision.allow is False


def test_kappa_one_away_and_authoritative_counts_match():
    universe = build_kappa_universe({1: 2, 2: 3, 3: 0}, 3)
    one = kappa_book_state(1, 2, 3)
    same = [row for row in universe.books if row.book == 1][0]
    assert one.observations_remaining == 1
    assert one.eligible is False
    assert same.realized_observation_count == one.realized_observation_count
    assert same.observations_remaining == one.observations_remaining
    assert same.eligible == one.eligible


def test_markout_future_lookup_works():
    history = MidHistory()
    history.record(3, ms_to_ns(0), 100.0)
    history.record(3, ms_to_ns(1000), 101.0)
    hit = history.nearest_future_mid(3, ms_to_ns(100))
    assert hit is not None
    assert hit[1] == 101.0


def test_fill_hazard_maker_taker_comparison_wired():
    assert "_research_exit_hazard_prediction" in RESEARCH_SRC
    assert "research_enable_fill_hazard_exit_compare" in RESEARCH_SRC
    assert "use_fill_hazard_ev" in RESEARCH_SRC


def test_place_skewed_quotes_does_not_assign_pydantic_limit_order():
    quotes = RESEARCH_SRC.split("def _place_skewed_quotes(")[1].split(
        "def _place_directional_round_trip("
    )[0]
    assert "response.limit_order =" not in quotes
    assert "_research_bind_response_method" in quotes
    assert "_research_unbind_response_method" in quotes
    assert "object.__setattr__" in RESEARCH_SRC


def test_response_method_bind_shadows_and_unbinds():
    class Dummy:
        def limit_order(self, book_id, qty):
            return ("orig", book_id, qty)

    dummy = Dummy()
    orig = dummy.limit_order

    def gated(book_id, qty, *args, **kwargs):
        return ("gated", book_id, qty)

    object.__setattr__(dummy, "limit_order", gated)
    assert dummy.limit_order(7, 0.25)[0] == "gated"
    dummy.__dict__.pop("limit_order", None)
    assert dummy.limit_order(7, 0.25)[0] == "orig"
    assert dummy.limit_order(7, 0.25) == orig(7, 0.25)


def test_pydantic_limit_order_assignment_needs_object_setattr():
    pydantic = pytest.importorskip("pydantic")

    class FinanceAgentResponse(pydantic.BaseModel):
        instructions: list = []

        def limit_order(self, book_id, qty):
            return ("orig", book_id, qty)

    response = FinanceAgentResponse()
    with pytest.raises(ValueError, match="no field"):
        response.limit_order = lambda *args, **kwargs: ("gated",)

    def gated(book_id, qty, *args, **kwargs):
        return ("gated", book_id, qty)

    object.__setattr__(response, "limit_order", gated)
    assert response.limit_order(3, 0.25)[0] == "gated"
    response.__dict__.pop("limit_order", None)
    assert response.limit_order(3, 0.25)[0] == "orig"
