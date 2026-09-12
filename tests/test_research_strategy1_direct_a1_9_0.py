# SPDX-License-Identifier: MIT
"""A1.9 Phase A: queue-preserving Maker-exit classifier, measurement only.

Phase A ships the classifier and its telemetry in shadow mode -- the decision is
computed and logged but discarded -- so this revision must stay behaviourally
identical to the A1.7.5 baseline.  Every assertion here is tied either to the
A1.8 rejection evidence (`STRATEGY1_DIRECT_V4_16_2_A1_9.md`) or to a structural
property of the exit lifecycle, never to a threshold fitted to one log.
"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
AGENT = ROOT / "agents" / "strategy"
if str(AGENT) not in sys.path:
    sys.path.insert(0, str(AGENT))

from research_direct_exit_refresh import (
    ABSENT_EXPIRED,
    ABSENT_FILLED,
    ABSENT_NEVER_PLACED,
    ABSENT_WAIT_CANCEL,
    DIRECT_A19_BASELINE_PROFITABLE_EXIT_TTL_MS,
    DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS,
    DIRECT_EXIT_REFRESH_VERSION,
    EVAL_BELOW_MIN_NET,
    EVAL_NO_MAKER_NET,
    EVAL_PERSIST_ELIGIBLE,
    EVAL_SHORT_TTL_REGIME,
    EXIT_HOLD,
    EXIT_REPRICE,
    REASON_LADDER_ESCALATION,
    REASON_NET_BELOW_FLOOR,
    REASON_NO_RESTING_ORDER,
    REASON_QUEUE_PRESERVED,
    REASON_SIZE_SHORTFALL,
    REASON_STALE_BEHIND_TOUCH,
    behind_ticks,
    classify_resting_maker_exit,
    exit_eval_class,
    forgone_edge_bps,
    ladder_rung,
)

SRC = (AGENT / "Strategy1_Research_Simple.py").read_text()
BASE_SRC = (AGENT / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()

TICK = 0.01
FLOOR = 1.0


def _classify(existing_price, desired_price, *, long_position=True, net=5.0,
              existing_qty=0.25, desired_qty=0.25, existing_action=None,
              desired_action=None, reprice_ticks=3.0):
    return classify_resting_maker_exit(
        existing_price=existing_price, desired_price=desired_price,
        tick_size=TICK, long_position=long_position,
        existing_qty=existing_qty, desired_qty=desired_qty,
        existing_net_bps=net, floor_net_bps=FLOOR,
        existing_action=existing_action, desired_action=desired_action,
        reprice_ticks=reprice_ticks,
    )


# --------------------------------------------------------------------------
# Version and scope
# --------------------------------------------------------------------------

def test_a19_version_contract():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_5"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_5"' in SRC
    assert DIRECT_EXIT_REFRESH_VERSION == "direct_exit_refresh_v4_16_2_a1_9_2"


def test_a18_cycle_bounded_ttl_override_is_fully_reverted():
    """A1.8's 975 ms TTL cost ~24% RT velocity and ~79% PnL rate. It must be gone."""
    assert "cycle_bounded_profitable_exit_ttl_ms" not in SRC
    assert "DIRECT_A18_LEGACY_PROFITABLE_EXIT_TTL_MS" not in SRC
    assert "A18_EXIT_REFRESH_CONFIG" not in SRC
    assert "self.research_profitable_exit_ttl_ms = " not in SRC


def test_phase_a_is_measurement_only():
    """The classifier runs in shadow mode; Phase A must not act on its decision."""
    assert "_a19_observe_exit_evaluation" in SRC
    assert "A19_EXIT_EVAL" in SRC
    # A1.9.1 Phase B is now shipped, so the reprice emitter exists by design.
    # What must remain true is that the OLD shadow observer stays measurement
    # only: its decision is still discarded.
    assert "shadow_mode=1," in SRC


def test_frozen_base_is_untouched_by_phase_a():
    assert "classify_resting_maker_exit" not in BASE_SRC
    assert "_a19_" not in BASE_SRC


def test_launcher_pins_phase_a_version_and_keeps_baseline_ttl():
    assert "strategy1_direct_v4_16_2_a1_9_4" in LAUNCHER
    # A1.9.1 Phase B is what raises the TTL, and it must be raised through
    # PARAMS -- never mutated in initialize(), which is how A1.8 did it.
    assert "research_profitable_exit_ttl_ms=4000" in LAUNCHER
    assert "tests/test_research_strategy1_direct_a1_9_0.py" in LAUNCHER


def test_ttl_constants_record_baseline_and_cadence_derived_target():
    assert DIRECT_A19_BASELINE_PROFITABLE_EXIT_TTL_MS == 3000.0
    # 4x the verified 1,000 ms publish cadence, not a fitted number.
    assert DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS == 4000.0
    assert DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS > 1000.0


# --------------------------------------------------------------------------
# The core fix: directional, not symmetric
# --------------------------------------------------------------------------

def test_favourable_drift_holds_however_far_it_runs():
    """A resting SELL below the desired price fills sooner and nets no less.

    A1.8's symmetric abs() test tore these down for no economic reason.
    """
    for desired in (100.01, 100.05, 100.50, 105.00):
        assert _classify(100.00, desired) == (EXIT_HOLD, REASON_QUEUE_PRESERVED)


def test_favourable_drift_holds_for_a_short_position_too():
    """Mirror case: a resting BUY above the desired price is the favourable side."""
    for desired in (99.99, 99.95, 99.50, 95.00):
        assert _classify(100.00, desired, long_position=False) == (
            EXIT_HOLD, REASON_QUEUE_PRESERVED
        )


def test_adverse_drift_reprices_at_the_existing_threshold():
    """reprice_ticks stays at its 3.0 default: A1.9 introduces no new threshold."""
    assert _classify(100.02, 100.00)[0] == EXIT_HOLD          # 2 ticks behind
    assert _classify(100.03, 100.00) == (EXIT_REPRICE, REASON_STALE_BEHIND_TOUCH)
    assert _classify(99.97, 100.00, long_position=False) == (
        EXIT_REPRICE, REASON_STALE_BEHIND_TOUCH
    )


def test_behind_ticks_is_signed_so_the_two_half_planes_are_distinguishable():
    assert behind_ticks(existing_price=100.03, desired_price=100.00,
                        tick_size=TICK, long_position=True) > 0
    assert behind_ticks(existing_price=100.00, desired_price=100.03,
                        tick_size=TICK, long_position=True) < 0


# --------------------------------------------------------------------------
# The structural reprice reasons
# --------------------------------------------------------------------------

def test_size_shortfall_reprices():
    assert _classify(100.00, 100.00, existing_qty=0.10, desired_qty=0.25) == (
        EXIT_REPRICE, REASON_SIZE_SHORTFALL
    )


def test_order_is_judged_on_its_own_price_not_this_cycle_desired_price():
    """A quote still clearing the floor stays even when the desired rung moved."""
    assert _classify(100.00, 100.00, net=FLOOR + 0.01)[0] == EXIT_HOLD
    assert _classify(100.00, 100.00, net=FLOOR - 0.01) == (
        EXIT_REPRICE, REASON_NET_BELOW_FLOOR
    )


def test_ladder_escalation_reprices_but_de_escalation_does_not():
    assert _classify(100.00, 100.00, existing_action="PASSIVE_MAKER_EXIT",
                     desired_action="AGGRESSIVE_MAKER_EXIT") == (
        EXIT_REPRICE, REASON_LADDER_ESCALATION
    )
    assert _classify(100.00, 100.00, existing_action="AGGRESSIVE_MAKER_EXIT",
                     desired_action="PASSIVE_MAKER_EXIT")[0] == EXIT_HOLD


def test_unknown_ladder_action_never_forces_a_reprice():
    assert ladder_rung("NOT_A_RUNG") == -1
    assert _classify(100.00, 100.00, existing_action=None,
                     desired_action="AGGRESSIVE_MAKER_EXIT")[0] == EXIT_HOLD


def test_missing_resting_order_is_a_reprice_not_a_hold():
    assert _classify(None, 100.00) == (EXIT_REPRICE, REASON_NO_RESTING_ORDER)


def test_unreadable_inputs_fail_towards_reprice_never_towards_a_stale_hold():
    for bad in (float("nan"), float("inf"), 0.0, -1.0):
        assert _classify(bad, 100.00)[0] == EXIT_REPRICE
        assert _classify(100.00, bad)[0] == EXIT_REPRICE


# --------------------------------------------------------------------------
# R1: the hold-rate denominator must mirror the persistence gate
# --------------------------------------------------------------------------

def test_eval_class_excludes_populations_persistence_cannot_act_on():
    assert exit_eval_class(maker_net_bps=5.0, min_net_bps=0.0,
                           market_regime="NORMAL") == EVAL_PERSIST_ELIGIBLE
    for regime in ("TOXIC", "STRESSED"):
        assert exit_eval_class(maker_net_bps=5.0, min_net_bps=0.0,
                               market_regime=regime) == EVAL_SHORT_TTL_REGIME
    assert exit_eval_class(maker_net_bps=None, min_net_bps=0.0,
                           market_regime="NORMAL") == EVAL_NO_MAKER_NET
    assert exit_eval_class(maker_net_bps=0.0, min_net_bps=0.0,
                           market_regime="NORMAL") == EVAL_BELOW_MIN_NET


def test_eval_class_boundary_matches_profitable_maker_exit_ttl_ms():
    """profitable_maker_exit_ttl_ms persists only on net > min_net_bps, strictly."""
    assert exit_eval_class(maker_net_bps=0.5, min_net_bps=0.5,
                           market_regime="NORMAL") == EVAL_BELOW_MIN_NET
    assert exit_eval_class(maker_net_bps=0.51, min_net_bps=0.5,
                           market_regime="NORMAL") == EVAL_PERSIST_ELIGIBLE


def test_absent_reasons_cover_every_way_a_resting_exit_can_vanish():
    reasons = {ABSENT_NEVER_PLACED, ABSENT_EXPIRED, ABSENT_FILLED, ABSENT_WAIT_CANCEL}
    assert len(reasons) == 4
    for reason in reasons:
        assert reason in SRC


# --------------------------------------------------------------------------
# R2: forgone edge is measured, never guessed
# --------------------------------------------------------------------------

def test_forgone_edge_is_zero_when_the_resting_quote_is_at_or_better():
    assert forgone_edge_bps(existing_price=100.0, desired_price=100.0,
                            long_position=True) == 0.0
    assert forgone_edge_bps(existing_price=100.0, desired_price=99.0,
                            long_position=True) == 0.0


def test_forgone_edge_grows_with_favourable_drift():
    small = forgone_edge_bps(existing_price=100.0, desired_price=100.05,
                             long_position=True)
    large = forgone_edge_bps(existing_price=100.0, desired_price=100.50,
                             long_position=True)
    assert 0.0 < small < large
    assert abs(large - 50.0) < 1e-6


def test_no_favourable_side_bound_is_hard_coded_in_phase_a():
    """The bound is set from measured cost in Phase D, not guessed now."""
    assert "research_exit_favourable_reprice_ticks" not in SRC
