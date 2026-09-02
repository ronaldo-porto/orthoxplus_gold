from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "agents" / "strategy"))

from research_execution_controller import (
    ACTION_MAKER,
    ACTION_SKIP,
    ACTION_TAKER,
    choose_execution,
)
from research_lifecycle_ev import lifecycle_is_executable
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_PARK_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    BAND_ABSOLUTE,
    BAND_DEFENSIVE,
    BAND_HARD_ESCAPE,
    BAND_NORMAL,
    TAKER_CLIP,
    choose_position_exit,
    classify_risk_band,
    wait_utility,
)
from research_realnet_exit_authority import REALNET_EXIT_AUTHORITY_VERSION
from research_risk_guard import evaluate_risk_guard, clip_size_to_caps
from research_role_size import (
    COMPLETION_SIZE,
    MIN_MAKER,
    STRONG_MAKER,
    maker_entry_size,
    taker_clip_size,
)
from research_score_ev import compute_score_ev
from research_total_score_frontier import apply_total_score_frontier
from research_execution_lanes import LaneBook, LANE_COMPLETION, classify_execution_lane

STRATEGY = (ROOT / "agents/strategy/Strategy1_Research.py").read_text()
BASE = ROOT / "agents/strategy/BaseStrategy.py"
ADAPTIVE = ROOT / "agents/strategy/AdaptiveAgent.py"
VALIDATOR_TRADE = ROOT / "taos/im/validator/trade.py"
VALIDATOR_TRADE_SHA256 = "137a4a7f26de9395a0028539a95411992c6ed0fa16ddd21682c04838121af0b8"


def _ev(**kwargs):
    defaults = dict(
        book=1, side="BUY", alpha=0.30, fill_prob_old=0.80,
        learned_actionable_p=0.50, learned_actionable_samples=20,
        spread_capture_bps=2.0, fees_bps=0.5, markout_mean_bps=0.0,
        markout_samples=20, realized_observation_count=0, required=3,
        min_trading_ev=0.0,
    )
    defaults.update(kwargs)
    return compute_score_ev(**defaults)


def test_hard_risk_blocks_entry():
    blocked = evaluate_risk_guard(inventory_blocked=True)
    assert blocked.safe is False
    assert blocked.reason == "MAX_INVENTORY"
    toxic = _ev(toxic=True, spread_capture_bps=12.0)
    assert toxic.eligible is False
    assert toxic.reject_reason == "TOXIC"


def test_positive_lifecycle_is_executable_negative_is_not():
    good = _ev(spread_capture_bps=6.0)
    bad = _ev(spread_capture_bps=0.0, expected_markout_override=-20.0)
    assert good.lifecycle_ev > 0.0
    assert good.eligible is True
    assert lifecycle_is_executable(good.lifecycle_ev) is True
    assert bad.lifecycle_ev < 0.0
    assert bad.eligible is False
    assert bad.reject_reason == "NEGATIVE_EV"


def test_total_score_ranks_positive_ev_and_cannot_rescue_negative():
    one = _ev(book=1, realized_observation_count=2, spread_capture_bps=6.0)
    fresh = _ev(book=2, realized_observation_count=0, spread_capture_bps=6.0)
    assert one.eligible and fresh.eligible
    assert one.total_score_component > fresh.total_score_component
    assert abs(one.lifecycle_ev - fresh.lifecycle_ev) < 1e-12
    negative = _ev(
        book=3, realized_observation_count=2, spread_capture_bps=0.0,
        expected_markout_override=-20.0, one_away_weight=10.0,
    )
    assert negative.eligible is False
    assert negative.final_score == float("-inf")


def test_maker_taker_skip_utilities():
    maker = choose_execution(lifecycle_ev=0.25, p_fill=0.70, crossing_cost=0.40)
    assert maker.action == ACTION_MAKER
    taker = choose_execution(
        lifecycle_ev=0.05, p_fill=0.05, crossing_cost=0.0,
        observations_remaining=1, expiry_urgency=1.0, capital_release=1.0,
    )
    assert taker.action == ACTION_TAKER
    skip = choose_execution(lifecycle_ev=-0.20, p_fill=0.90, crossing_cost=0.40)
    assert skip.action == ACTION_SKIP


def test_healthy_one_away_has_high_priority():
    row = LaneBook(
        book_id=1, rolling_observation_count=2, observations_remaining=1,
        economics_ok=True, completion_ev_ok=True, lifecycle_ev=0.20,
        projected_completion_healthy=True, projected_completion_quality=0.80,
    )
    planned, _ = apply_total_score_frontier([row], qualified_books=10)
    assert planned[0].total_score_due is True
    assert classify_execution_lane(planned[0]) == LANE_COMPLETION


def test_several_valid_books_keep_independent_eligibility():
    rows = [_ev(book=i, spread_capture_bps=6.0) for i in range(1, 5)]
    assert all(r.eligible for r in rows)
    assert len({r.book for r in rows}) == 4


def test_maker_and_taker_sizing_caps():
    minimum = maker_entry_size(lifecycle_ev=0.01, p_fill=0.20)
    assert minimum.size == MIN_MAKER
    strong = maker_entry_size(lifecycle_ev=0.30, p_fill=0.60, inventory_headroom=0.40)
    assert strong.size <= 0.40
    assert strong.size >= MIN_MAKER or strong.size == 0.0
    completion = maker_entry_size(lifecycle_ev=0.50, p_fill=0.80, observations_remaining=1)
    assert completion.size == COMPLETION_SIZE
    capped = clip_size_to_caps(1.0, min_order=0.25, inventory_headroom=0.10)
    assert capped == 0.0
    clip = taker_clip_size(inventory_qty=1.50)
    assert abs(clip.size - TAKER_CLIP) < 1e-12
    again = taker_clip_size(inventory_qty=1.25)
    assert again.size == TAKER_CLIP
    exact = taker_clip_size(inventory_qty=0.25)
    assert exact.size == 0.25


def test_position_exit_utility_and_corridor():
    maker = choose_position_exit(
        maker_net_bps=6.0, taker_net_bps=-1.0, p_maker_fill=0.80,
        unrealized_bps=3.0, inventory_age=1.0,
    )
    assert maker.risk_band == BAND_NORMAL
    assert maker.action in {ACTION_MAKER_EXIT, ACTION_WAIT}
    taker = choose_position_exit(
        maker_net_bps=-2.0, taker_net_bps=3.0, p_maker_fill=0.05,
        unrealized_bps=1.0, expiry_urgency=1.0, capital_release=1.0,
        inventory_age=20.0, failed_exit_count=6,
    )
    assert taker.action == ACTION_TAKER_EXIT
    assert taker.risk_band == BAND_NORMAL
    wait = choose_position_exit(
        maker_net_bps=2.0, taker_net_bps=-1.0, p_maker_fill=0.70,
        unrealized_bps=1.0, inventory_age=0.0, failed_exit_count=0,
    )
    assert wait.action in {ACTION_WAIT, ACTION_MAKER_EXIT}
    young = wait_utility(maker_net_bps=2.0, p_maker_fill=0.70, inventory_age=0.0)
    old = wait_utility(maker_net_bps=2.0, p_maker_fill=0.70, inventory_age=40.0)
    assert old < young


def test_loss_corridor_bands():
    assert classify_risk_band(-4.0) == BAND_NORMAL
    assert classify_risk_band(-12.0) == BAND_DEFENSIVE
    assert classify_risk_band(-20.0) == BAND_HARD_ESCAPE
    assert classify_risk_band(-26.0) == BAND_ABSOLUTE
    defensive = choose_position_exit(
        maker_net_bps=2.0, taker_net_bps=-12.0, p_maker_fill=0.60,
        unrealized_bps=-12.0, inventory_age=10.0, failed_exit_count=4,
        maker_executable=True,
    )
    assert defensive.risk_band == BAND_DEFENSIVE
    hard = choose_position_exit(
        maker_net_bps=-4.0, taker_net_bps=-20.0, p_maker_fill=0.20,
        unrealized_bps=-20.0, inventory_age=2.0,
    )
    assert hard.action == ACTION_TAKER_EXIT
    assert hard.selected_qty <= TAKER_CLIP + 1e-12
    park = choose_position_exit(
        maker_net_bps=-8.0, taker_net_bps=-26.0, p_maker_fill=0.10,
        unrealized_bps=-26.0, is_dust=True, inventory_qty=0.01,
        valid_opposite_touch=True,
    )
    assert park.action == ACTION_PARK_EXIT
    reduce = choose_position_exit(
        maker_net_bps=-8.0, taker_net_bps=-26.0, p_maker_fill=0.10,
        unrealized_bps=-26.0, inventory_qty=0.25,
    )
    assert reduce.action == ACTION_TAKER_EXIT
    assert reduce.selected_qty <= TAKER_CLIP + 1e-12
    score_cannot_bypass = choose_position_exit(
        maker_net_bps=50.0, taker_net_bps=-26.0, p_maker_fill=1.0,
        unrealized_bps=-26.0, observations_remaining=1, inventory_qty=0.25,
    )
    assert score_cannot_bypass.action == ACTION_TAKER_EXIT


def test_regression_frozen_surfaces():
    assert REALNET_EXIT_AUTHORITY_VERSION == "realnet_exit_authority_v4_14_4"
    assert "choose_position_exit" in STRATEGY
    assert "choose_execution" in STRATEGY
    assert "evaluate_risk_guard" in STRATEGY
    assert "_research_execute_entry_taker" in STRATEGY
    assert "_passes_expected_pnl_gate" in STRATEGY
    assert "choose_unified_exit(" not in STRATEGY
    assert "positive_maker_rescue_veto_applies(" not in STRATEGY
    assert "evaluate_bounded_rescue(" not in STRATEGY
    assert "evaluate_protected_parking(" not in STRATEGY
    digest = sha256(VALIDATOR_TRADE.read_bytes()).hexdigest()
    assert digest == VALIDATOR_TRADE_SHA256
    base_src = BASE.read_text(encoding="utf-8")
    adaptive_src = ADAPTIVE.read_text(encoding="utf-8")
    assert "simplified_hybrid_authority_v4_16_0" not in base_src
    assert "simplified_hybrid_authority_v4_16_0" not in adaptive_src
    assert "simplified_hybrid_authority_v4_16_2" not in base_src
    assert "simplified_hybrid_authority_v4_16_2" not in adaptive_src
    assert "choose_position_exit" not in adaptive_src
