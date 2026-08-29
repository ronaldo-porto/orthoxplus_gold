from pathlib import Path

from research_execution_lanes import LaneBook, apply_kappa_conversion_pressure_gate
from research_kappa_productivity import (
    KAPPA_PRODUCTIVITY_VERSION,
    TIER_INEFFICIENT,
    wide_kappa_density_due,
)

ROOT = Path(__file__).parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text()


def _fresh(book_id: int) -> LaneBook:
    return LaneBook(
        book_id=book_id,
        observations_remaining=3,
        entry_feasible=True,
        economics_ok=True,
        maker_ev=0.05,
        maker_ev_known=True,
        is_uncovered=True,
    )


def test_v4142_policy_and_two_exploration_paths_are_wired():
    assert KAPPA_PRODUCTIVITY_VERSION == "wide_kappa_productivity_v4_14_2"
    assert 'RESEARCH_POLICY_VERSION = "realnet_authority_rotation_v4_14_4"' in SRC
    assert "research_cohort_exploration_slots=2" in LAUNCHER
    assert "research_kappa_exploration_slots=2" in LAUNCHER
    assert "research_wide_kappa_min_density_observations=6" in LAUNCHER
    assert "research_wide_kappa_preferred_density_observations=8" in LAUNCHER
    assert "research_wide_kappa_raw_target=0.35" in LAUNCHER


def test_parked_labels_alone_no_longer_zero_fresh_coverage():
    rows, suppressed, productive, reason = apply_kappa_conversion_pressure_gate(
        [_fresh(1), _fresh(2), _fresh(3)],
        parked_open_books=4,
        max_parked_open_books=4,
        total_open_books=2,
        max_total_open_books=8,
        reserve_total_slots=3,
        exploration_slots=2,
        enabled=True,
    )
    assert reason == "DISABLED_OR_NO_PROGRESS" or reason == "NO_PRESSURE"
    assert not suppressed
    assert all(row.entry_feasible for row in rows)
    assert 'pressure_reason = "PARKED_RECYCLE"' not in SRC


def test_true_headroom_pressure_preserves_two_fresh_coverage_books():
    # Add an actual conversion candidate so the pressure gate is active, then
    # verify two fresh books survive while total headroom is tight.
    progress = LaneBook(
        book_id=9,
        observations_remaining=1,
        entry_feasible=True,
        economics_ok=True,
    )
    fresh = [_fresh(1), _fresh(2), _fresh(3), _fresh(4)]
    rows, suppressed, productive, reason = apply_kappa_conversion_pressure_gate(
        [progress, *fresh],
        parked_open_books=4,
        max_parked_open_books=4,
        total_open_books=6,
        max_total_open_books=8,
        reserve_total_slots=3,
        exploration_slots=2,
        enabled=True,
    )
    assert reason == "TOTAL_HEADROOM"
    survivors = [r.book_id for r in rows if r.book_id in {1,2,3,4} and r.entry_feasible]
    assert len(survivors) == 2
    assert len(suppressed & {1,2,3,4}) == 2


def test_three_observations_is_eligibility_not_density_finish_line():
    assert wide_kappa_density_due(observations=3, raw_kappa=0.16, kappa_eligible=True)
    assert wide_kappa_density_due(observations=5, raw_kappa=0.20, kappa_eligible=True)
    # Six observations reaches the minimum density floor, but a positive weak
    # raw Kappa may continue to the preferred cap of eight.
    assert wide_kappa_density_due(observations=6, raw_kappa=0.16, kappa_eligible=True)
    assert wide_kappa_density_due(observations=7, raw_kappa=0.34, kappa_eligible=True)
    assert not wide_kappa_density_due(observations=8, raw_kappa=0.16, kappa_eligible=True)


def test_weak_quality_extension_is_bounded_and_does_not_feed_bad_books():
    assert not wide_kappa_density_due(observations=6, raw_kappa=0.40, kappa_eligible=True)
    assert not wide_kappa_density_due(observations=6, raw_kappa=-0.01, kappa_eligible=True)
    assert not wide_kappa_density_due(
        observations=4,
        raw_kappa=0.15,
        kappa_eligible=True,
        execution_tier=TIER_INEFFICIENT,
    )
    assert not wide_kappa_density_due(observations=6, raw_kappa=None, kappa_eligible=True)


def test_density_and_balanced_phases_keep_at_least_two_coverage_slots():
    # Static source contract keeps the change O(1) and guards against reverting
    # to the old one-coverage density choke point.
    density = SRC.split('if phase == PRODUCTIVITY_PHASE_DENSITY:', 1)[1].split(
        'if phase == PRODUCTIVITY_PHASE_BALANCED:', 1
    )[0]
    balanced = SRC.split('if phase == PRODUCTIVITY_PHASE_BALANCED:', 1)[1].split(
        'return normalize_lane_budgets(', 2
    )[1]
    assert "coverage_slots=2" in density
    assert "coverage_slots=3" in SRC.split('if phase == PRODUCTIVITY_PHASE_BALANCED:', 1)[1][:250]
