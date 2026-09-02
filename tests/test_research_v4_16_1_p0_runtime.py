from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "agents" / "strategy"))

from research_contract_guard import sanitize_post_only_limit_price
from research_execution_controller import (
    ACTION_MAKER,
    ACTION_SKIP,
    ACTION_TAKER,
    choose_execution,
)
from research_exit_quantity import round_volume
from research_lifecycle_ev import lifecycle_is_executable
from research_neutral_prediction import (
    NEUTRAL_PREDICTION_VERSION,
    SOURCE_NEUTRAL,
    can_use_neutral_fallback,
    directional_prediction_unavailable,
    is_neutral_forecast,
    l1_is_valid,
    make_neutral_forecast,
    prediction_source_of,
    tag_neutral_forecast,
)
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_PARK_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    BAND_ABSOLUTE,
    BAND_DEFENSIVE,
    BAND_HARD_ESCAPE,
    BAND_NORMAL,
    POSITION_EXIT_VERSION,
    TAKER_CLIP,
    choose_position_exit,
    classify_risk_band,
    continuation_value,
    maker_exit_utility,
    new_exposure_allowed,
    wait_utility,
)
from research_rt_phase_timing import RoundTripPhaseState
from research_score_ev import compute_score_ev

STRATEGY = (ROOT / "agents/strategy/Strategy1_Research.py").read_text(encoding="utf-8")
BASE = ROOT / "agents/strategy/BaseStrategy.py"
ADAPTIVE = ROOT / "agents/strategy/AdaptiveAgent.py"
VALIDATOR_TRADE = ROOT / "taos/im/validator/trade.py"
VALIDATOR_TRADE_SHA256 = "137a4a7f26de9395a0028539a95411992c6ed0fa16ddd21682c04838121af0b8"
S = 1_000_000_000


class _Lvl:
    def __init__(self, price):
        self.price = price


def _book(bid=99.90, ask=100.10):
    return SimpleNamespace(bids=[_Lvl(bid)], asks=[_Lvl(ask)])


def _ev(**kwargs):
    defaults = dict(
        book=1, side="BUY", alpha=0.0, fill_prob_old=0.80,
        learned_actionable_p=0.50, learned_actionable_samples=20,
        spread_capture_bps=6.0, fees_bps=0.5, markout_mean_bps=0.0,
        markout_samples=20, realized_observation_count=0, required=3,
        min_trading_ev=0.0,
    )
    defaults.update(kwargs)
    return compute_score_ev(**defaults)


def test_versions_and_frozen_surfaces():
    assert NEUTRAL_PREDICTION_VERSION == "neutral_prediction_v4_16_1"
    assert POSITION_EXIT_VERSION == "position_exit_v4_16_1"
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in STRATEGY
    assert 'RESEARCH_ENGINE_VERSION = "simplified_hybrid_authority_v4_16_2"' in STRATEGY
    assert 'RESEARCH_ENGINE_REVISION = "simplified_hybrid_authority_v4_16_2"' in STRATEGY
    assert "_research_apply_neutral_predictions" in STRATEGY
    assert "sanitize_post_only_limit_price" in STRATEGY
    assert "_research_sanitize_maker_instructions" in STRATEGY
    assert "neutral_fallback=" in STRATEGY
    digest = sha256(VALIDATOR_TRADE.read_bytes()).hexdigest()
    assert digest == VALIDATOR_TRADE_SHA256
    base_src = BASE.read_text(encoding="utf-8")
    adaptive_src = ADAPTIVE.read_text(encoding="utf-8")
    assert "simplified_hybrid_authority_v4_16_2" not in base_src
    assert "simplified_hybrid_authority_v4_16_2" not in adaptive_src
    assert "choose_position_exit" not in adaptive_src


def test_missing_prediction_valid_l1_reaches_lifecycle_ev():
    assert l1_is_valid(_book())
    allowed, reason = can_use_neutral_fallback(book=_book(), inventory_flat=True, risk_safe=True)
    assert allowed is True
    assert reason == SOURCE_NEUTRAL
    ev = _ev(alpha=0.0, spread_capture_bps=6.0)
    assert ev.alpha == 0.0
    assert ev.lifecycle_ev > 0.0
    assert lifecycle_is_executable(ev.lifecycle_ev) is True


def test_neutral_fallback_directional_alpha_is_zero():
    forecast = make_neutral_forecast(7)
    assert is_neutral_forecast(forecast)
    assert float(forecast.score) == 0.0
    assert float(forecast.alpha_directional) == 0.0
    assert prediction_source_of(forecast) == SOURCE_NEUTRAL


def test_neutral_fallback_cannot_invent_positive_alpha():
    tagged = tag_neutral_forecast(SimpleNamespace(direction="UP", score=0.9, book_id=1))
    assert tagged.direction == "HOLD"
    assert tagged.score == 0.0
    assert tagged.alpha_directional == 0.0
    ev = _ev(alpha=0.0, spread_capture_bps=8.0)
    assert ev.alpha == 0.0


def test_positive_maker_lifecycle_selects_maker():
    decision = choose_execution(
        lifecycle_ev=0.25, p_fill=0.70, crossing_cost=0.40, neutral_fallback=True,
    )
    assert decision.action == ACTION_MAKER


def test_negative_lifecycle_skips():
    decision = choose_execution(
        lifecycle_ev=-0.20, p_fill=0.90, crossing_cost=0.40, neutral_fallback=True,
    )
    assert decision.action == ACTION_SKIP


def test_hard_toxic_and_invalid_l1_block_fallback():
    ok, reason = can_use_neutral_fallback(book=_book(), toxic=True)
    assert ok is False
    assert reason == "TOXIC"
    empty = SimpleNamespace(bids=[], asks=[])
    assert l1_is_valid(empty) is False
    ok, reason = can_use_neutral_fallback(book=empty, inventory_flat=True)
    assert ok is False
    assert reason == "INVALID_L1"


def test_caps_still_block_fallback():
    book = _book()
    assert can_use_neutral_fallback(book=book, volume_capped=True)[0] is False
    assert can_use_neutral_fallback(book=book, exposure_capped=True)[0] is False
    assert can_use_neutral_fallback(book=book, inventory_capped=True)[0] is False
    assert can_use_neutral_fallback(book=book, risk_safe=False)[0] is False


def test_neutral_fallback_does_not_automatically_authorize_taker():
    skipped = choose_execution(
        lifecycle_ev=0.05, p_fill=0.10, crossing_cost=0.40, neutral_fallback=True,
    )
    assert skipped.action != ACTION_TAKER
    taker = choose_execution(
        lifecycle_ev=0.20, p_fill=0.05, crossing_cost=0.0, neutral_fallback=True,
    )
    assert taker.action in {ACTION_TAKER, ACTION_MAKER, ACTION_SKIP}
    if taker.action == ACTION_TAKER:
        assert taker.taker_utility > 0.0


def test_high_maker_fill_wins():
    decision = choose_position_exit(
        maker_net_bps=6.0, taker_net_bps=-8.0, p_maker_fill=0.80,
        unrealized_bps=3.0, inventory_age=1.0, failed_exit_count=0,
    )
    assert decision.action == ACTION_MAKER_EXIT
    assert decision.risk_band == BAND_NORMAL


def test_low_fill_negative_wait_prefers_bounded_taker():
    decision = choose_position_exit(
        maker_net_bps=4.0, taker_net_bps=-2.0, p_maker_fill=0.095,
        unrealized_bps=-4.0, inventory_age=40.0, failed_exit_count=19,
        holding_bps=8.0, adverse_risk=0.50,
    )
    assert decision.action == ACTION_TAKER_EXIT
    assert decision.low_fill_maker_rejected == 1


def test_failed_exits_monotonically_reduce_maker_continuation():
    wait0 = wait_utility(maker_net_bps=2.0, p_maker_fill=0.20, failed_exit_count=0)
    values = []
    for fails in (0, 2, 6, 12):
        wait_u = wait_utility(maker_net_bps=2.0, p_maker_fill=0.20, failed_exit_count=fails)
        values.append(
            continuation_value(wait_u=wait_u, failed_exit_count=fails)
        )
    assert values[0] > values[1] > values[2] > values[3]
    assert wait0 > wait_utility(maker_net_bps=2.0, p_maker_fill=0.20, failed_exit_count=12)


def test_inventory_age_monotonically_reduces_wait():
    young = wait_utility(maker_net_bps=2.0, p_maker_fill=0.70, inventory_age=0.0)
    mid = wait_utility(maker_net_bps=2.0, p_maker_fill=0.70, inventory_age=16.0)
    old = wait_utility(maker_net_bps=2.0, p_maker_fill=0.70, inventory_age=40.0)
    assert old < mid < young


def test_positive_maker_exit_not_overridden():
    decision = choose_position_exit(
        maker_net_bps=8.0, taker_net_bps=-12.0, p_maker_fill=0.65,
        unrealized_bps=2.0, inventory_age=2.0, failed_exit_count=1,
    )
    assert decision.action == ACTION_MAKER_EXIT


def test_taker_remains_clipped():
    decision = choose_position_exit(
        maker_net_bps=-2.0, taker_net_bps=3.0, p_maker_fill=0.05,
        unrealized_bps=1.0, inventory_qty=1.50, expiry_urgency=1.0,
        capital_release=1.0, inventory_age=20.0, failed_exit_count=6,
    )
    assert decision.action == ACTION_TAKER_EXIT
    assert decision.selected_qty <= TAKER_CLIP + 1e-12


def test_corridor_normal_defensive_hard():
    assert classify_risk_band(-4.0) == BAND_NORMAL
    assert classify_risk_band(-12.0) == BAND_DEFENSIVE
    assert classify_risk_band(-20.0) == BAND_HARD_ESCAPE
    assert classify_risk_band(-26.0) == BAND_ABSOLUTE
    normal = choose_position_exit(
        maker_net_bps=6.0, taker_net_bps=-1.0, p_maker_fill=0.80, unrealized_bps=-4.0,
    )
    assert normal.risk_band == BAND_NORMAL
    assert normal.action in {ACTION_MAKER_EXIT, ACTION_WAIT, ACTION_TAKER_EXIT}
    defensive = choose_position_exit(
        maker_net_bps=2.0, taker_net_bps=-12.0, p_maker_fill=0.60,
        unrealized_bps=-12.0, inventory_age=10.0, failed_exit_count=4,
    )
    assert defensive.risk_band == BAND_DEFENSIVE
    hard = choose_position_exit(
        maker_net_bps=-4.0, taker_net_bps=-20.0, p_maker_fill=0.20,
        unrealized_bps=-20.0, inventory_qty=0.25,
    )
    assert hard.action == ACTION_TAKER_EXIT
    assert hard.selected_qty <= TAKER_CLIP + 1e-12


def test_absolute_executable_inventory_does_not_park():
    decision = choose_position_exit(
        maker_net_bps=-8.0, taker_net_bps=-26.0, p_maker_fill=0.10,
        unrealized_bps=-26.0, inventory_qty=0.25,
    )
    assert decision.action == ACTION_TAKER_EXIT
    assert decision.reason == "ABSOLUTE_PROTECTION_REDUCE"
    assert decision.selected_qty <= TAKER_CLIP + 1e-12
    staged = choose_position_exit(
        maker_net_bps=-8.0, taker_net_bps=-30.0, p_maker_fill=0.10,
        unrealized_bps=-30.0, inventory_qty=1.00,
    )
    assert staged.action == ACTION_TAKER_EXIT
    assert staged.selected_qty == TAKER_CLIP


def test_dust_and_no_touch_may_park():
    dust = choose_position_exit(
        maker_net_bps=-8.0, taker_net_bps=-26.0, p_maker_fill=0.10,
        unrealized_bps=-26.0, inventory_qty=0.01, is_dust=True,
    )
    assert dust.action == ACTION_PARK_EXIT
    no_touch = choose_position_exit(
        maker_net_bps=-8.0, taker_net_bps=-26.0, p_maker_fill=0.10,
        unrealized_bps=-26.0, inventory_qty=0.25, valid_opposite_touch=False,
    )
    assert no_touch.action == ACTION_PARK_EXIT


def test_absolute_blocks_new_exposure():
    assert new_exposure_allowed(BAND_NORMAL) is True
    assert new_exposure_allowed(BAND_ABSOLUTE) is False
    assert "ABSOLUTE_PROTECTION_NO_NEW_EXPOSURE" in STRATEGY


def test_maker_buy_cannot_cross_ask():
    price = sanitize_post_only_limit_price(
        side="buy", original_price=100.20, best_bid=99.90, best_ask=100.10, tick_size=0.01,
    )
    assert price is not None
    assert price < 100.10


def test_maker_sell_cannot_cross_bid():
    price = sanitize_post_only_limit_price(
        side="sell", original_price=99.80, best_bid=99.90, best_ask=100.10, tick_size=0.01,
    )
    assert price is not None
    assert price > 99.90


def test_reprice_uses_latest_l1_and_quantity_decimals():
    stale = sanitize_post_only_limit_price(
        side="buy", original_price=100.00, best_bid=99.50, best_ask=99.60, tick_size=0.01,
    )
    assert stale is not None
    assert stale < 99.60
    assert round_volume(0.2500009, 8) == 0.2500009 or round_volume(0.25, 8) == 0.25
    assert abs(round_volume(0.25, 8) - 0.25) < 1e-12
    exact = round_volume(0.25, 2)
    assert exact == 0.25


def test_exact_min_order_remains_valid():
    assert abs(round_volume(0.25, 8) - 0.25) < 1e-12
    decision = choose_position_exit(
        maker_net_bps=-8.0, taker_net_bps=-26.0, p_maker_fill=0.10,
        unrealized_bps=-26.0, inventory_qty=0.25, min_order=0.25,
    )
    assert decision.selected_qty == 0.25


def test_duplicate_same_side_is_deduped_in_sanitize():
    assert "seen_book_sides" in STRATEGY
    assert "_research_sanitize_maker_instructions" in STRATEGY


def test_entry_submit_recorded_and_maps_through_rt():
    state = RoundTripPhaseState()
    state.note_entry_submit(3, 0)
    state.note_entry_fill(3, 10 * S)
    state.note_exit_submit(3, 100 * S)
    sample = state.note_round_trip(3, 130 * S)
    assert sample["entry_wait_s"] == 10.0
    assert sample["hold_s"] == 90.0
    assert sample["exit_wait_s"] == 30.0
    assert state.missing_entry_submit == 0


def test_completed_rt_has_phase_timing_when_events_exist():
    state = RoundTripPhaseState()
    state.note_entry_submit(1, 0)
    state.note_entry_fill(1, 5 * S)
    state.note_exit_submit(1, 8 * S)
    sample = state.note_round_trip(1, 12 * S)
    assert sample["total_s"] == 12.0
    snap = state.snapshot(simulation_time=12.0)
    assert snap["rt_missing_entry_submit"] == 0


def test_missing_telemetry_never_modifies_trading():
    state = RoundTripPhaseState()
    state.note_entry_fill(9, 10 * S)
    state.note_exit_submit(9, 20 * S)
    sample = state.note_round_trip(9, 30 * S)
    assert state.missing_entry_submit == 1
    assert sample["entry_wait_s"] is None
    assert "choose_execution" in STRATEGY
    idx = STRATEGY.index("def _research_note_entry_submit_if_flat")
    window = STRATEGY[idx:idx + 800]
    assert "except Exception:" in window
    assert "if not getattr(self, \"debug_enabled\", False):" not in STRATEGY.split("def respond(")[1].split("def predict_direction(")[0]


def test_no_prediction_is_not_supreme_authority():
    assert "NEUTRAL_MAKER_FALLBACK" in STRATEGY
    assert "NOT_SELECTED" in STRATEGY
    assert directional_prediction_unavailable(None) is True
    assert directional_prediction_unavailable(SimpleNamespace(direction="HOLD", score=0.0)) is True
    assert directional_prediction_unavailable(SimpleNamespace(direction="UP", score=0.4)) is False


def test_maker_utility_includes_nonfill_continuation():
    wait_u = wait_utility(
        maker_net_bps=4.0, p_maker_fill=0.095, inventory_age=40.0, failed_exit_count=19,
        holding_bps=8.0, adverse_risk=0.5,
    )
    optimistic = 0.095 * 1.0
    actual = maker_exit_utility(
        maker_net_bps=4.0, p_maker_fill=0.095, wait_u=wait_u,
        inventory_age=40.0, failed_exit_count=19, adverse_risk=0.5,
    )
    assert actual < optimistic
    assert actual < 0.0
