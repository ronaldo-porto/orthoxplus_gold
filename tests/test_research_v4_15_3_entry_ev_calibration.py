from hashlib import sha256
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "agents" / "strategy"))

from research_lane_funnel import (
    LANE_FUNNEL_VERSION,
    bump,
    bump_reject,
    compact_log,
    empty_funnel,
)
from research_lifecycle_ev import (
    ADVERSE_PENALTY_CAP,
    HOLDING_PENALTY_CAP,
    ONE_AWAY_ENTRY_MULT,
    RESEARCH_LIFECYCLE_ENTRY_VERSION,
    TAKER_ENTRY_PENALTY_CAP,
    TOTAL_ENTRY_EV_CAP,
    TWO_AWAY_ENTRY_MULT,
    completion_entry_multiplier,
    compute_required_entry_ev,
    required_entry_ev,
)
from research_realnet_exit_authority import (
    ACTION_PARK,
    ACTION_TAKER_ESCAPE,
    REALNET_EXIT_AUTHORITY_VERSION,
    arbitrate_realnet_exit,
)
from research_score_ev import SCORE_EV_VERSION, compute_score_ev

STRATEGY = (ROOT / "agents/strategy/Strategy1_Research.py").read_text()
BASE = ROOT / "agents/strategy/BaseStrategy.py"
ADAPTIVE = ROOT / "agents/strategy/AdaptiveAgent.py"
VALIDATOR_TRADE = ROOT / "taos/im/validator/trade.py"
VALIDATOR_TRADE_SHA256 = "137a4a7f26de9395a0028539a95411992c6ed0fa16ddd21682c04838121af0b8"


def _ev(**kwargs):
    defaults = dict(
        book=1,
        side="BUY",
        alpha=0.30,
        fill_prob_old=0.80,
        learned_actionable_p=0.50,
        learned_actionable_samples=20,
        spread_capture_bps=2.0,
        fees_bps=0.5,
        markout_mean_bps=0.0,
        markout_samples=20,
        realized_observation_count=0,
        required=3,
        min_trading_ev=0.0,
    )
    defaults.update(kwargs)
    return compute_score_ev(**defaults)


def test_v4153_release_versions():
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in STRATEGY
    assert RESEARCH_LIFECYCLE_ENTRY_VERSION == "lifecycle_ev_v4_16_2"
    assert SCORE_EV_VERSION == "simplified_hybrid_authority_v4_16_0"
    assert LANE_FUNNEL_VERSION == "lane_funnel_v4_16_0"
    assert REALNET_EXIT_AUTHORITY_VERSION == "realnet_exit_authority_v4_14_4"


def test_healthy_one_away_can_clear_calibrated_entry_bar():
    row = _ev(
        realized_observation_count=2,
        required=3,
        learned_actionable_p=0.22,
        taker_exit_probability=0.78,
        expected_cross_bps=1.6,
        holding_risk_bps=0.50,
        projected_completion_healthy=True,
        recent_realized_pnl=0.04,
    )
    assert row.trading_ev > 0.0
    assert row.observations_remaining == 1
    assert row.eligible is True
    assert row.reject_reason is None
    assert row.entry_ev_pass is True


def test_negative_trading_ev_is_rejected_without_completion_subsidy():
    row = _ev(
        realized_observation_count=2,
        required=3,
        spread_capture_bps=0.0,
        expected_markout_override=-20.0,
        learned_actionable_p=0.80,
        taker_exit_probability=0.78,
        projected_completion_healthy=True,
        recent_realized_pnl=0.04,
    )
    assert row.trading_ev < 0.0
    assert row.completion_multiplier == 1.0
    assert row.eligible is False
    assert row.reject_reason == "NEGATIVE_EV"
    assert row.entry_ev_pass is False


def test_high_taker_probability_raises_a_bounded_bar():
    low = compute_required_entry_ev(taker_exit_probability=0.30)
    high = compute_required_entry_ev(taker_exit_probability=0.80)
    assert high.required_entry_ev > low.required_entry_ev
    assert high.required_entry_ev <= TOTAL_ENTRY_EV_CAP
    assert high.capped_taker_penalty <= TAKER_ENTRY_PENALTY_CAP
    assert high.taker_prob_excess == 0.50
    assert abs(required_entry_ev(taker_exit_probability=0.80) - high.required_entry_ev) < 1e-12


def test_extreme_cross_and_adverse_hit_caps_not_old_explosion():
    ext = compute_required_entry_ev(
        taker_exit_probability=0.90,
        expected_cross_bps=1000.0,
        holding_risk_bps=1000.0,
        adverse_selection_cost=10.0,
    )
    assert abs(ext.capped_taker_penalty - TAKER_ENTRY_PENALTY_CAP) < 1e-12
    assert abs(ext.holding_penalty - HOLDING_PENALTY_CAP) < 1e-12
    assert abs(ext.adverse_penalty - ADVERSE_PENALTY_CAP) < 1e-12
    assert ext.required_entry_ev <= TOTAL_ENTRY_EV_CAP + 1e-12
    assert ext.required_entry_ev < 0.20
    assert ext.raw_taker_penalty > ext.capped_taker_penalty


def test_healthy_two_away_discount_is_weaker_than_one_away():
    one = _ev(
        realized_observation_count=2,
        taker_exit_probability=0.78,
        projected_completion_healthy=True,
        recent_realized_pnl=0.04,
    )
    two = _ev(
        realized_observation_count=1,
        taker_exit_probability=0.78,
        projected_completion_healthy=True,
        recent_realized_pnl=0.04,
    )
    assert one.completion_value > two.completion_value
    assert one.eligible is True
    assert two.eligible is True


def test_coverage_and_qualified_books_keep_full_entry_bar():
    coverage = _ev(
        realized_observation_count=0,
        taker_exit_probability=0.78,
        projected_completion_healthy=True,
        recent_realized_pnl=0.04,
    )
    qualified = _ev(
        realized_observation_count=3,
        taker_exit_probability=0.78,
        projected_completion_healthy=True,
        recent_realized_pnl=0.04,
    )
    assert coverage.observations_remaining == 3
    assert qualified.observations_remaining == 0
    assert coverage.completion_multiplier == 1.0
    assert qualified.completion_multiplier == 1.0
    assert abs(coverage.required_entry_ev - qualified.required_entry_ev) < 1e-12
    assert completion_entry_multiplier(
        observations_remaining=3,
        projected_completion_healthy=True,
        trading_ev=0.05,
        recent_realized_pnl=0.04,
    ) == 1.0


def test_funnel_rank_reject_splits_lifecycle_and_required_entry():
    funnel = empty_funnel()
    bump(funnel, "COMPLETION", "lane_total_score_selected")
    bump_reject(funnel, "NEGATIVE_EV")
    bump_reject(funnel, "REQUIRED_ENTRY_EV")
    rec = compact_log(funnel, tick=7, lane="COMPLETION")
    assert rec["reject_negative_ev"] == 1
    assert rec["reject_required_entry_ev"] == 1
    assert rec["reject_lifecycle_ev"] == 0
    bump_reject(funnel, "LIFECYCLE_EV")
    rec = compact_log(funnel, lane="COMPLETION")
    assert rec["reject_lifecycle_ev"] == 1
    bump(funnel, "COMPLETION", "lane_lifecycle_ev_pass")
    bump(funnel, "COMPLETION", "lane_required_entry_ev_pass")
    rec = compact_log(funnel, lane="COMPLETION")
    assert rec["lifecycle_ev_pass"] == 1
    assert rec["required_entry_ev_pass"] == 1
    assert rec["ev_pass"] == 1
    assert rec["completion_lifecycle_ev_pass"] == 1
    assert rec["completion_required_entry_ev_pass"] == 1


def test_hard_risk_v4144_exit_authority_unchanged():
    assert REALNET_EXIT_AUTHORITY_VERSION == "realnet_exit_authority_v4_14_4"
    assert "arbitrate_realnet_exit" in STRATEGY
    hard = arbitrate_realnet_exit(
        taker_net_bps=-20.0, maker_net_bps=3.0, maker_executable=True,
        failed_exit_count=0, inventory_age=0, liveness_park=True, liveness_floor_bps=-12.0,
    )
    assert hard.action == ACTION_TAKER_ESCAPE
    park = arbitrate_realnet_exit(
        taker_net_bps=-25.01, maker_net_bps=-5.0, maker_executable=True,
        failed_exit_count=100, inventory_age=100, adverse_evidence=True,
    )
    assert park.action == ACTION_PARK


def test_validator_and_frozen_agents_are_unchanged():
    digest = sha256(VALIDATOR_TRADE.read_bytes()).hexdigest()
    assert digest == VALIDATOR_TRADE_SHA256
    base_src = BASE.read_text(encoding="utf-8")
    adaptive_src = ADAPTIVE.read_text(encoding="utf-8")
    assert "simplified_hybrid_authority_v4_16_0" not in base_src
    assert "simplified_hybrid_authority_v4_16_0" not in adaptive_src
    assert "simplified_hybrid_authority_v4_16_2" not in base_src
    assert "simplified_hybrid_authority_v4_16_2" not in adaptive_src
    assert "lifecycle_ev_v4_16_2" not in base_src
    assert "lane_funnel_v4_16_0" not in adaptive_src
