from pathlib import Path

from research_execution_lanes import (
    LANE_COMPLETION,
    LaneBook,
    LaneBudgets,
    apply_kappa_conversion_pressure_gate,
    authoritative_execution_lane,
    execution_completion_candidate,
    select_lane_candidates,
)
from research_inventory_liveness import (
    FRESH_MAKER_GRACE_VERSION,
    classify_liveness_stage,
    evaluate_bounded_rescue,
    fresh_maker_grace_applies,
)
from research_kappa_productivity import (
    KAPPA_PRODUCTIVITY_VERSION,
    ProductivitySnapshot,
    core_probe_eligible,
)

ROOT = Path(__file__).parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def _fresh_stage(*, failed=0, age=1.0, state="NORMAL", stop=False):
    return classify_liveness_stage(
        observations_remaining=3,
        failed_exit_count=failed,
        inventory_age=age,
        inventory_state=state,
        stop_loss_hit=stop,
        maker_failed_exits=3,
        maker_min_age_ticks=8,
        taker_failed_exits=8,
        taker_min_age_ticks=16,
        hard_failed_exits=12,
        hard_min_age_ticks=24,
        maker_floor_bps=-4.0,
        soft_taker_floor_bps=-8.0,
        hard_taker_floor_bps=-12.0,
    )


def _price_window_rescue(stage):
    decision = evaluate_bounded_rescue(
        stage,
        taker_net_bps=-9.0,
        wait_ev_bps=-10.0,
        expected_markout_bps=-2.0,
        adverse_selection_risk=0.10,
        stop_loss_hit=False,
        inventory_state="NORMAL",
        min_ev_advantage_bps=0.5,
    )
    assert decision.authorized
    assert decision.reason == "PRICE_HARD_WINDOW_RESCUE"
    return decision


def _unknown_snapshot(*, fresh_rt=0, fresh_pos=0, fresh_neg=0):
    return ProductivitySnapshot(
        book_id=83,
        observations=4,
        round_trips=4,
        maker_quotes=0,
        maker_fills=0,
        contract_rejects=0,
        realized_pnl=0.1916,
        positive_count=3,
        negative_count=1,
        maker_fee_bps=-10.0,
        fill_rate_hint=0.20,
        raw_kappa=0.8,
        ticks_since_last_rt=20,
        fresh_round_trips=fresh_rt,
        fresh_positive_round_trips=fresh_pos,
        fresh_negative_round_trips=fresh_neg,
    )


def test_v4134_version_contract():
    assert KAPPA_PRODUCTIVITY_VERSION == "wide_kappa_productivity_v4_14_2"
    assert FRESH_MAKER_GRACE_VERSION == "fresh_maker_grace_v4_13_2"
    assert 'RESEARCH_POLICY_VERSION = "wide_kappa_wave_v4_14_2"' in SRC
    assert "research_core_probe_enabled" in SRC
    assert "research_fresh_maker_grace_enabled" in SRC


def test_age1_price_hard_window_is_blocked_by_profitable_maker_grace():
    stage = _fresh_stage(failed=0, age=1.0)
    rescue = _price_window_rescue(stage)
    assert fresh_maker_grace_applies(
        stage,
        rescue,
        maker_net_bps=17.9,
        maker_executable=True,
        stop_loss_hit=False,
        inventory_state="NORMAL",
        hard_risk=False,
        grace_ticks=3.0,
    )


def test_maker_grace_releases_on_failed_exit_age_or_unprofitable_maker():
    stage_failed = _fresh_stage(failed=1, age=1.0)
    rescue_failed = _price_window_rescue(stage_failed)
    assert not fresh_maker_grace_applies(
        stage_failed, rescue_failed,
        maker_net_bps=10.0, maker_executable=True,
        stop_loss_hit=False, inventory_state="NORMAL", hard_risk=False,
    )

    stage_old = _fresh_stage(failed=0, age=4.0)
    rescue_old = _price_window_rescue(stage_old)
    assert not fresh_maker_grace_applies(
        stage_old, rescue_old,
        maker_net_bps=10.0, maker_executable=True,
        stop_loss_hit=False, inventory_state="NORMAL", hard_risk=False,
        grace_ticks=3.0,
    )

    stage = _fresh_stage(failed=0, age=1.0)
    rescue = _price_window_rescue(stage)
    assert not fresh_maker_grace_applies(
        stage, rescue,
        maker_net_bps=-0.1, maker_executable=True,
        stop_loss_hit=False, inventory_state="NORMAL", hard_risk=False,
    )
    assert not fresh_maker_grace_applies(
        stage, rescue,
        maker_net_bps=10.0, maker_executable=False,
        stop_loss_hit=False, inventory_state="NORMAL", hard_risk=False,
    )


def test_maker_grace_never_blocks_real_hard_risk_authority():
    stage = _fresh_stage(failed=0, age=1.0)
    rescue = _price_window_rescue(stage)
    assert not fresh_maker_grace_applies(
        stage, rescue,
        maker_net_bps=20.0, maker_executable=True,
        stop_loss_hit=False, inventory_state="NORMAL", hard_risk=True,
    )
    assert not fresh_maker_grace_applies(
        stage, rescue,
        maker_net_bps=20.0, maker_executable=True,
        stop_loss_hit=True, inventory_state="NORMAL", hard_risk=False,
    )
    assert not fresh_maker_grace_applies(
        stage, rescue,
        maker_net_bps=20.0, maker_executable=True,
        stop_loss_hit=False, inventory_state="EMERGENCY", hard_risk=False,
    )


def test_book83_like_unknown_qualified_book_is_core_probe_eligible():
    snap = _unknown_snapshot()
    assert snap.execution_tier == "UNKNOWN"
    assert core_probe_eligible(
        snap,
        kappa_eligible=True,
        maker_ev=0.035,
        maker_ev_known=True,
        flat_and_safe=True,
        entry_feasible=True,
        economics_ok=True,
        pnl_confidence="FULL",
        recent_realized_pnl=0.1916,
        raw_kappa=0.8,
    )


def test_core_probe_rejects_negative_or_existing_fresh_evidence():
    snap = _unknown_snapshot()
    common = dict(
        kappa_eligible=True,
        maker_ev=0.035,
        maker_ev_known=True,
        flat_and_safe=True,
        entry_feasible=True,
        economics_ok=True,
        pnl_confidence="FULL",
        recent_realized_pnl=0.1916,
        raw_kappa=0.8,
    )
    assert not core_probe_eligible(snap, **{**common, "maker_ev": -0.01})
    assert not core_probe_eligible(snap, **{**common, "recent_realized_pnl": -0.01})
    assert not core_probe_eligible(snap, **{**common, "raw_kappa": -0.1})
    assert not core_probe_eligible(_unknown_snapshot(fresh_rt=1, fresh_pos=1), **common)


def test_first_fresh_probe_loss_is_marked_for_demotion():
    bad = _unknown_snapshot(fresh_rt=1, fresh_pos=0, fresh_neg=1)
    assert bad.fresh_probe_failed
    good = _unknown_snapshot(fresh_rt=1, fresh_pos=1, fresh_neg=0)
    assert not good.fresh_probe_failed


def test_core_probe_owns_one_completion_slot_before_recycling_bridge():
    one_away = LaneBook(
        book_id=1,
        observations_remaining=1,
        kappa_productivity_tier="PRODUCTIVE",
        kappa_productivity_score=0.9,
    )
    probe = LaneBook(
        book_id=83,
        observations_remaining=0,
        density_due=True,
        core_probe_candidate=True,
        kappa_productivity_tier="UNKNOWN",
        kappa_productivity_score=0.60,
    )
    bridge = LaneBook(
        book_id=115,
        observations_remaining=0,
        density_due=True,
        recycling_candidate=True,
        kappa_productivity_tier="PRODUCTIVE",
        kappa_productivity_score=0.70,
    )
    allocation = select_lane_candidates(
        [one_away, probe, bridge],
        LaneBudgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
        max_candidates=1,
    )
    assert allocation.by_lane[LANE_COMPLETION] == [83]


def test_core_probe_survives_tight_conversion_pressure_when_headroom_exists():
    probe = LaneBook(
        book_id=83,
        observations_remaining=0,
        density_due=True,
        core_probe_candidate=True,
        economics_ok=True,
        entry_feasible=True,
        maker_ev=0.03,
        maker_ev_known=True,
    )
    # Simulate a qualified fresh book (remaining>=3 is the pressure gate's
    # suppressible class); the core_probe flag must be the protected escape slot.
    probe = LaneBook(**{**probe.__dict__, "observations_remaining": 3})
    other = LaneBook(
        book_id=2,
        observations_remaining=3,
        is_uncovered=True,
        economics_ok=True,
        entry_feasible=True,
        maker_ev=5.0,
        maker_ev_known=True,
    )
    gated, suppressed, _, reason = apply_kappa_conversion_pressure_gate(
        [other, probe],
        parked_open_books=0,
        max_parked_open_books=6,
        total_open_books=11,
        max_total_open_books=12,
        reserve_total_slots=3,
        exploration_slots=1,
        enabled=True,
    )
    by_id = {row.book_id: row for row in gated}
    assert reason == "TOTAL_HEADROOM"
    assert by_id[83].entry_feasible
    assert 83 not in suppressed


def test_core_probe_lane_identity_survives_execution_reclassification():
    # Exact V4.13.2 runtime failure: a CORE_PROBE is already Kappa eligible, so
    # the legacy completion predicate is False.  The probe flag must still keep
    # the execution lane as KAPPA_COMPLETION.
    assert execution_completion_candidate(
        inventory_flat=True,
        core_probe_candidate=True,
        legacy_completion_candidate=False,
    )
    assert not execution_completion_candidate(
        inventory_flat=True,
        core_probe_candidate=False,
        legacy_completion_candidate=False,
    )
    assert execution_completion_candidate(
        inventory_flat=True,
        core_probe_candidate=False,
        legacy_completion_candidate=True,
    )
    assert not execution_completion_candidate(
        inventory_flat=False,
        core_probe_candidate=True,
        legacy_completion_candidate=False,
    )


def test_launcher_explicitly_enables_v4134_patch():
    assert 'wide_kappa_productivity_v4_14_2' in LAUNCHER
    assert 'research_fresh_maker_grace_enabled=1' in LAUNCHER
    assert 'research_fresh_maker_grace_ticks=3' in LAUNCHER
    assert 'research_core_probe_enabled=1' in LAUNCHER


def test_v4134_authoritative_completion_grant_survives_eligible_core_reclassification():
    # Exact Book122 failure: screening granted COMPLETION to a profitable CORE
    # book, but the legacy predicate is False because the book is already Kappa
    # eligible.  Execution must keep the granted lane.
    allocation = select_lane_candidates(
        [
            LaneBook(
                book_id=122,
                observations_remaining=0,
                core_candidate=True,
                economics_ok=True,
                entry_feasible=True,
            )
        ],
        LaneBudgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
        max_candidates=1,
    )
    assert allocation.by_lane[LANE_COMPLETION] == [122]
    lane = authoritative_execution_lane(
        122,
        inventory_flat=True,
        allocation=allocation,
        fallback_lane="COVERAGE",  # legacy recomputation in V4.13.3
    )
    assert lane == LANE_COMPLETION


def test_v4134_authoritative_lane_covers_recycling_density_probe_and_coverage():
    rows = [
        LaneBook(book_id=10, observations_remaining=0, recycling_candidate=True, economics_ok=True),
        LaneBook(book_id=11, observations_remaining=0, density_due=True, economics_ok=True),
        LaneBook(book_id=12, observations_remaining=0, core_probe_candidate=True, economics_ok=True),
        LaneBook(book_id=14, observations_remaining=1, economics_ok=True),
        LaneBook(book_id=13, observations_remaining=3, economics_ok=True),
    ]
    allocation = select_lane_candidates(
        rows,
        LaneBudgets(coverage_slots=1, completion_slots=4, realization_slots=0, shared_overflow_slots=0),
        max_candidates=5,
    )
    for bid in (10, 11, 12, 14):
        assert authoritative_execution_lane(
            bid, inventory_flat=True, allocation=allocation, fallback_lane="COVERAGE"
        ) == LANE_COMPLETION
    assert authoritative_execution_lane(
        13, inventory_flat=True, allocation=allocation, fallback_lane=LANE_COMPLETION
    ) == "COVERAGE"


def test_v4134_nonflat_inventory_keeps_realization_priority_over_stale_grant():
    allocation = select_lane_candidates(
        [LaneBook(book_id=122, observations_remaining=0, core_candidate=True, economics_ok=True)],
        LaneBudgets(coverage_slots=0, completion_slots=1, realization_slots=0, shared_overflow_slots=0),
        max_candidates=1,
    )
    assert authoritative_execution_lane(
        122, inventory_flat=False, allocation=allocation, fallback_lane="COVERAGE"
    ) == "REALIZATION"


def test_v4134_falls_back_only_when_book_has_no_current_lane_grant():
    allocation = select_lane_candidates(
        [LaneBook(book_id=1, observations_remaining=3, economics_ok=True)],
        LaneBudgets(coverage_slots=1, completion_slots=0, realization_slots=0, shared_overflow_slots=0),
        max_candidates=1,
    )
    assert authoritative_execution_lane(
        999, inventory_flat=True, allocation=allocation, fallback_lane=LANE_COMPLETION
    ) == LANE_COMPLETION
