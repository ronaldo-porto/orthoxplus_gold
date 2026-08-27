from pathlib import Path
from types import SimpleNamespace

from research_candidate_screen import book_touch_fingerprint
from research_execution_lanes import LaneBook, completion_sort_key, coverage_sort_key
from research_kappa_productivity import (
    KAPPA_PRODUCTIVITY_VERSION,
    PHASE_BALANCED,
    PHASE_BOOTSTRAP,
    PHASE_DENSITY,
    ProductivitySnapshot,
    STATE_BUILDING,
    STATE_CORE,
    STATE_NEW,
    STATE_QUALIFIED,
    TIER_INEFFICIENT,
    kappa_state,
    priority_for_state,
    scheduler_phase,
)
from research_quote_hysteresis import should_replace_quote

ROOT = Path(__file__).parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()
LIVENESS_SRC = (ROOT / "agents" / "strategy" / "research_inventory_liveness.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def _book115():
    return ProductivitySnapshot(
        book_id=115,
        observations=3,
        round_trips=3,
        maker_quotes=12,
        maker_fills=6,
        contract_rejects=0,
        realized_pnl=0.7644,
        positive_count=3,
        negative_count=0,
        maker_fee_bps=-12.49,
        fill_rate_hint=0.20,
        raw_kappa=0.5,
        ticks_since_last_rt=1,
    )


def _book98():
    return ProductivitySnapshot(
        book_id=98,
        observations=2,
        round_trips=2,
        maker_quotes=194,
        maker_fills=5,
        contract_rejects=22,
        realized_pnl=0.10,
        positive_count=2,
        negative_count=0,
        maker_fee_bps=-29.9,
        fill_rate_hint=0.03,
        raw_kappa=0.2,
        ticks_since_last_rt=20,
    )


def test_v413_version_and_phase_contract():
    assert KAPPA_PRODUCTIVITY_VERSION == "simplified_kappa_productivity_v4_13"
    assert 'RESEARCH_POLICY_VERSION = "simplified_kappa_productivity_v4_13"' in SRC
    assert scheduler_phase(0) == PHASE_BOOTSTRAP
    assert scheduler_phase(40) == PHASE_BOOTSTRAP
    assert scheduler_phase(41) == PHASE_BALANCED
    assert scheduler_phase(79) == PHASE_BALANCED
    assert scheduler_phase(80) == PHASE_DENSITY


def test_kappa_states_are_only_new_building_qualified_core():
    assert kappa_state(observations=0) == STATE_NEW
    assert kappa_state(observations=1) == STATE_BUILDING
    assert kappa_state(observations=2) == STATE_BUILDING
    assert kappa_state(observations=3) == STATE_QUALIFIED
    assert kappa_state(observations=3, core=True) == STATE_CORE


def test_book115_becomes_core_and_book98_is_demoted():
    good = _book115()
    bad = _book98()
    assert good.core_candidate
    assert bad.execution_tier == TIER_INEFFICIENT
    assert not bad.core_candidate
    assert good.placements_per_rt == 4.0
    assert bad.placements_per_rt == 97.0
    assert priority_for_state(good, phase=PHASE_DENSITY) > priority_for_state(
        bad, phase=PHASE_DENSITY
    )


def test_completion_lane_core_recycling_outranks_inefficient_one_away():
    core = LaneBook(
        book_id=115,
        observations_remaining=0,
        density_due=True,
        core_candidate=True,
        kappa_productivity_tier="PRODUCTIVE",
        kappa_productivity_score=0.9,
        recent_realized_pnl=0.7,
    )
    inefficient = LaneBook(
        book_id=98,
        observations_remaining=1,
        kappa_productivity_tier="INEFFICIENT",
        kappa_productivity_score=0.1,
        recent_realized_pnl=0.1,
    )
    assert completion_sort_key(core) < completion_sort_key(inefficient)


def test_coverage_lane_demotes_known_order_sink():
    normal = LaneBook(
        book_id=1,
        is_uncovered=True,
        maker_ev=0.5,
        maker_ev_known=True,
        economics_ok=True,
        kappa_productivity_tier="UNKNOWN",
        kappa_productivity_score=0.4,
    )
    sink = LaneBook(
        book_id=98,
        is_uncovered=True,
        maker_ev=2.0,
        maker_ev_known=True,
        economics_ok=True,
        kappa_productivity_tier="INEFFICIENT",
        kappa_productivity_score=0.1,
    )
    assert coverage_sort_key(normal) < coverage_sort_key(sink)



def test_legacy_restored_rt_history_without_v413_quote_ledger_is_not_false_core():
    legacy = ProductivitySnapshot(
        book_id=42,
        observations=6,
        round_trips=6,
        maker_quotes=0,
        maker_fills=0,
        contract_rejects=0,
        realized_pnl=0.5,
        positive_count=5,
        negative_count=1,
        maker_fee_bps=-10.0,
        fill_rate_hint=0.20,
        raw_kappa=0.8,
        ticks_since_last_rt=20,
    )
    assert legacy.execution_tier == "UNKNOWN"
    assert legacy.rt_efficiency == 0.45
    assert not legacy.core_candidate


def test_coverage_lane_demotes_cohort_order_sink_below_unknown_explorer():
    explorer = LaneBook(
        book_id=1,
        cohort_member=False,
        is_uncovered=True,
        maker_ev=0.4,
        maker_ev_known=True,
        economics_ok=True,
        kappa_productivity_tier="UNKNOWN",
        kappa_productivity_score=0.35,
    )
    cohort_sink = LaneBook(
        book_id=98,
        cohort_member=True,
        is_uncovered=True,
        maker_ev=3.0,
        maker_ev_known=True,
        economics_ok=True,
        kappa_productivity_tier="INEFFICIENT",
        kappa_productivity_score=0.05,
    )
    assert coverage_sort_key(explorer) < coverage_sort_key(cohort_sink)

def test_hot_path_fingerprint_does_not_touch_events():
    class EventTrapBook:
        bids = [SimpleNamespace(price=100.0, quantity=2.0)]
        asks = [SimpleNamespace(price=100.1, quantity=3.0)]

        @property
        def events(self):
            raise AssertionError("all-book screen must not inspect events")

    assert book_touch_fingerprint(EventTrapBook()) == (100.0, 2.0, 100.1, 3.0)
    assert "full event parsing is Stage-2 work only" in SRC


def test_persistent_maker_ignores_signal_only_churn_but_not_price_or_safety():
    hold = should_replace_quote(
        old_price=100.0,
        new_price=100.0,
        tick_size=0.01,
        min_price_ticks=3.0,
        old_alpha=0.5,
        new_alpha=-0.5,
        old_ofi=0.5,
        new_ofi=-0.5,
        old_regime="QUIET",
        new_regime="TREND",
        persistent_maker=True,
    )
    assert not hold.cancel
    assert hold.reason == "HOLD"

    price = should_replace_quote(
        old_price=100.0,
        new_price=100.03,
        tick_size=0.01,
        min_price_ticks=3.0,
        persistent_maker=True,
    )
    assert price.cancel and price.reason == "PRICE"

    safety = should_replace_quote(
        old_price=100.0,
        new_price=100.0,
        tick_size=0.01,
        persistent_maker=True,
        hard_safety=True,
    )
    assert safety.cancel and safety.reason == "HARD_SAFETY"


def test_v413_source_wiring_keeps_v41218_safety_and_adds_two_tick_guard():
    assert "evaluate_protected_parking" in SRC
    assert "PRICE_HARD_WINDOW_RESCUE" in LIVENESS_SRC
    assert 'research_post_only_safety_ticks", 2' in SRC
    assert "post_only_gap = safety_ticks * tick_size" in SRC
    assert "research_persistent_maker_enabled" in SRC
    assert "research_kappa_productivity_enabled" in SRC


def test_launcher_enables_v413_without_removing_v41218_contracts():
    assert "V4.12.18 Inventory-State Decoupling API OK" in LAUNCHER
    assert "V4.13 simplified Kappa productivity API OK" in LAUNCHER
    assert "research_kappa_productivity_enabled=1" in LAUNCHER
    assert "research_persistent_maker_enabled=1" in LAUNCHER
    assert "research_hysteresis_min_price_ticks=3" in LAUNCHER
    assert "research_post_only_safety_ticks=2" in LAUNCHER
