from pathlib import Path
import ast
import sys

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(STRATEGY_DIR))

PATH = STRATEGY_DIR / "Strategy1_Research_Simple.py"
SRC = PATH.read_text(encoding="utf-8")
TREE = ast.parse(SRC)
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
METHODS = {n.name: n for n in CLASS.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

from research_direct_exit import DIRECT_OBSERVABLE_EXIT_VERSION
from research_direct_fastpath import DIRECT_FASTPATH_CANDIDATE_COUNT, DIRECT_FASTPATH_DEEP_COUNT
from research_direct_economics import DIRECT_MAKER_MIN_EDGE_BPS, DIRECT_TAKER_ENTRY_ENABLED
from research_direct_liveness import (
    DIRECT_LIVENESS_VERSION,
    admission_slots,
    normalization_allowed,
    partial_recovery_plan,
    recovery_expiry_ns,
    bound_remainder_hold_active,
    partition_bound_remainder_orders,
)


def test_a173_version_and_frozen_strategy_contract():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_0_3"' in SRC
    assert DIRECT_LIVENESS_VERSION == "direct_partial_liveness_v4_16_2_a1_7_3_1"
    # Exit economics/authority are intentionally frozen from A1.7.2.
    assert DIRECT_OBSERVABLE_EXIT_VERSION == "direct_observable_exit_v4_16_2_a1_7_2"
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 20
    assert DIRECT_FASTPATH_DEEP_COUNT == 16
    assert DIRECT_MAKER_MIN_EDGE_BPS == 2.5
    assert DIRECT_TAKER_ENTRY_ENABLED is False


def test_partial_remainder_ttl_survives_publish_cycle():
    assert recovery_expiry_ns(baseline_ns=750_000_000, publish_interval_ns=1_000_000_000) == 3_000_000_000
    assert recovery_expiry_ns(baseline_ns=500_000_000, publish_interval_ns=0) == 2_500_000_000


def test_entry_partial_preserves_original_same_side_remainder():
    p = partial_recovery_plan(before=0.0, after=0.0498, min_order=0.25, eps=5e-5)
    assert p is not None
    assert p.mode == "ENTRY_COMPLETE"
    assert p.target_inventory == 0.25
    assert p.desired_side == "buy"
    assert p.preserve_existing_remainder is True


def test_exit_partial_preserves_original_flattening_remainder():
    p = partial_recovery_plan(before=0.25, after=0.0498, min_order=0.25, eps=5e-5)
    assert p is not None
    assert p.mode == "EXIT_COMPLETE"
    assert p.target_inventory == 0.0
    assert p.desired_side == "sell"
    assert p.preserve_existing_remainder is True


def test_crossed_dust_cancels_old_remainder_and_normalizes_current_sign():
    p = partial_recovery_plan(before=0.25, after=-0.0498, min_order=0.25, eps=5e-5)
    assert p is not None
    assert p.mode == "NORMALIZE"
    assert p.desired_side == "sell"
    assert p.preserve_existing_remainder is False


def test_dust_reserves_one_abs_and_active_slot_from_new_entries():
    slots = admission_slots(
        effective_abs=1.50,
        active_books=4,
        effective_open_books=4,
        dust_count=1,
        max_abs=2.0,
        max_active=6,
        max_open=8,
        min_order=0.25,
    )
    assert slots == 1
    without_dust = admission_slots(
        effective_abs=1.50,
        active_books=4,
        effective_open_books=4,
        dust_count=0,
        max_abs=2.0,
        max_active=6,
        max_open=8,
        min_order=0.25,
    )
    assert without_dust == 2


def test_saturated_dust_state_admits_no_fresh_entry_but_preserves_recovery_headroom():
    slots = admission_slots(
        effective_abs=1.7756,
        active_books=4,
        effective_open_books=4,
        dust_count=12,
        max_abs=2.0,
        max_active=6,
        max_open=8,
        min_order=0.25,
    )
    assert slots == 0


def test_irreducible_dust_normalization_requires_real_headroom():
    assert normalization_allowed(
        net=0.0498,
        total_effective_abs=1.70,
        active_books=5,
        effective_open_books=5,
        max_abs=2.0,
        max_active=6,
        max_open=8,
        min_order=0.25,
        eps=5e-5,
    )
    assert not normalization_allowed(
        net=0.0498,
        total_effective_abs=1.80,
        active_books=5,
        effective_open_books=5,
        max_abs=2.0,
        max_active=6,
        max_open=8,
        min_order=0.25,
        eps=5e-5,
    )
    # >= half-minimum dust belongs to the old exposure-nonincreasing compactor.
    assert not normalization_allowed(
        net=0.13,
        total_effective_abs=1.0,
        active_books=2,
        effective_open_books=2,
        max_abs=2.0,
        max_active=6,
        max_open=8,
        min_order=0.25,
        eps=5e-5,
    )


def test_overlay_tracks_and_services_actual_partial_fills():
    fill_method = ast.get_source_segment(SRC, METHODS["_research_on_own_fill"])
    assert "_direct_note_partial_fill_recovery" in fill_method
    note = ast.get_source_segment(SRC, METHODS["_direct_note_partial_fill_recovery"])
    assert '"A173_PARTIAL_FILL_RECOVERY"' in note
    assert "_research_partial_fill_hold_candidates" in note
    service = ast.get_source_segment(SRC, METHODS["_direct_service_partial_fill_recovery"])
    assert '"A173_PARTIAL_REMAINDER_HOLD"' in service
    assert "new_subminimum_order=0" in service
    assert "_research_partial_fill_hold_quoted" in service


def test_irreducible_dust_uses_same_sign_minimum_maker_normalization():
    method = ast.get_source_segment(SRC, METHODS["_direct_place_dust_normalizer"])
    assert "OrderDirection.BUY if long_dust else OrderDirection.SELL" in method
    assert "quantity=float(qty)" in method
    assert "clientOrderId=91000" in method
    assert '"A173_DUST_NORMALIZE"' in method


def test_build_reserves_recovery_capacity_and_prioritizes_normalization():
    method = ast.get_source_segment(SRC, METHODS["build_mm_strategy_instructions"])
    assert "dust_recovery_reserve_abs(" in method
    assert "direct_liveness_admission_slots(" in method
    assert "_direct_liveness_blocked_ticks" in method
    assert "_direct_normalize_irreducible_dust(" in method
    normalize_pos = method.index("_direct_normalize_irreducible_dust(")
    candidate_pos = method.index("if portfolio_slots > 0:", normalize_pos)
    assert normalize_pos < candidate_pos


def test_frozen_research_base_and_exit_module_have_no_a173_patch():
    base_src = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
    exit_src = (STRATEGY_DIR / "research_direct_exit.py").read_text(encoding="utf-8")
    assert "A173_" not in base_src
    assert "a1_7_3" not in base_src
    assert "A173_" not in exit_src
    assert "a1_7_3" not in exit_src


def test_forced_liveness_can_unlock_observed_legacy_saturation_with_bounded_overflow():
    # Observed long-run failure state: 1.7756 / 2.0 BASE leaves < one 0.25 clip.
    assert not normalization_allowed(
        net=0.0999,
        total_effective_abs=1.7756,
        active_books=4,
        effective_open_books=4,
        max_abs=2.0,
        max_active=6,
        max_open=8,
        min_order=0.25,
        eps=5e-5,
    )
    assert normalization_allowed(
        net=0.0999,
        total_effective_abs=1.7756,
        active_books=4,
        effective_open_books=4,
        max_abs=2.0,
        max_active=6,
        max_open=8,
        min_order=0.25,
        eps=5e-5,
        recovery_overflow_abs=0.125,
    )


def test_forced_recovery_liveness_signal_remains_but_a1743_final_cap_is_strict():
    method = ast.get_source_segment(SRC, METHODS["_research_final_validate_instructions"])
    assert "STRICT_EXPOSURE_HEADROOM" in method
    assert "DIRECT_DUST_RECOVERY_MAX_OVERFLOW_CLIPS" not in method
    normalize = ast.get_source_segment(SRC, METHODS["_direct_normalize_irreducible_dust"])
    assert '"A173_LIVENESS_RECOVERY"' in normalize
    assert "recovery_overflow_abs=0.0" in normalize


def test_a1731_hold_window_uses_simulator_time_without_parameter_tuning():
    assert bound_remainder_hold_active(
        fill_timestamp_ns=10_000_000_000, now_timestamp_ns=13_999_999_999
    )
    assert not bound_remainder_hold_active(
        fill_timestamp_ns=10_000_000_000, now_timestamp_ns=14_000_000_001
    )


def test_a1731_binds_maker_partial_to_exact_order_and_blocks_replacement_paths():
    fill = ast.get_source_segment(SRC, METHODS["_research_on_own_fill"])
    note = ast.get_source_segment(SRC, METHODS["_direct_note_partial_fill_recovery"])
    service = ast.get_source_segment(SRC, METHODS["_direct_service_partial_fill_recovery"])
    assert "event=event" in fill
    compact = ast.get_source_segment(SRC, METHODS["_direct_compact_selected_dust"])
    maker_exit = ast.get_source_segment(SRC, METHODS["_research_place_maker_exit"])
    assert "bound_order_id" in note
    assert "hold_start_timestamp_ns" in note
    assert "_direct_partial_fill_bound_order_id" in note
    assert "direct_partition_bound_remainder_orders" in service
    assert '"A1731_PARTIAL_REMAINDER_PENDING"' in service
    assert "_direct_partial_hold_active(book_id, state)" in compact
    assert 'path="DUST_COMPACTOR"' in compact
    assert 'path="MAKER_EXIT"' in maker_exit


def test_a1731_book111_regression_keeps_bound_754818_and_cancels_only_sibling():
    kept, conflicts = partition_bound_remainder_orders(
        [(754818, "sell"), (754900, "buy")],
        bound_order_id=754818, desired_side="sell",
    )
    assert kept == [754818]
    assert conflicts == [754900]
    # A same-side replacement is also conflicting: only the exact original
    # partially-filled order owns the book during the hold window.
    kept, conflicts = partition_bound_remainder_orders(
        [(754818, "sell"), (754901, "sell")],
        bound_order_id=754818, desired_side="sell",
    )
    assert kept == [754818]
    assert conflicts == [754901]

def test_a1731_keeps_all_liveness_and_trading_parameters_frozen():
    import research_direct_liveness as live
    assert live.DIRECT_PARTIAL_HOLD_MAX_NS == 4_000_000_000
    assert live.DIRECT_PARTIAL_HOLD_PUBLISH_MULT == 3
    assert live.DIRECT_DUST_RECOVERY_RESERVE_CLIPS == 1
    assert live.DIRECT_DUST_RECOVERY_MAX_OVERFLOW_CLIPS == 0.5
    assert live.DIRECT_LIVENESS_TRIGGER_TICKS == 12
    assert DIRECT_FASTPATH_CANDIDATE_COUNT == 20
    assert DIRECT_FASTPATH_DEEP_COUNT == 16
    assert DIRECT_MAKER_MIN_EDGE_BPS == 2.5
    assert DIRECT_TAKER_ENTRY_ENABLED is False


def test_a1731_crossing_recovery_target_invalidates_old_bound_remainder():
    note = ast.get_source_segment(SRC, METHODS["_direct_note_partial_fill_recovery"])
    assert "crossed_recovery_target" in note
    assert "bound_order_id = None" in note
