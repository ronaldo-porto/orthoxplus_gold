# SPDX-License-Identifier: MIT
"""Strategy1-Direct V4.16.2 A1.9 Phase A queue-preserving exit measurement.

This module intentionally does *not* add another strategy layer.  It reuses the
existing V4.16.2 Research state/learning/persistence infrastructure but replaces
its hot orchestration path with the shortest useful authority chain:

    128-book observable scan -> current spread/fee/Kappa rank -> deep top-K
                  -> hard safety -> current Maker edge -> Maker/Skip -> final validation

A1.9 keeps A1.7.5 relative tail authority and A1.7.4.4 positive-Maker Kappa veto, A1.7.4.3.2 identity-safe ownership and strict aggregate in-flight exposure reservation,
A1.7.4.2 Kappa-safe dust compaction, A1.7.4.1 replay de-duplication, A1.7.4
tail recovery, A1.7.2 TRUE-WAIT, and A1.7.3.1 partial-remainder/liveness
frozen. A1.7.4.4 keeps its narrow Kappa-tail correction. A1.7.4.5 adds one
regime-specific acquisition correction: only in QUIET + no meaningful Maker
rebate + very-low trade activity + wide spread, the current observable Maker
edge floor rises from 2.5 bps to 15 bps. Normal/rebate regimes, size, portfolio
limits, ownership, exit pricing/authority, recovery thresholds, and FastPath remain unchanged.

A1.8's cycle-bounded 975 ms exit TTL is fully reverted: it cost ~24% of RT
velocity, ~44% of positive-RT production and ~79% of the PnL rate.  Shortening
the TTL below one publish cycle guaranteed the order was dead before the next
evaluation, which structurally disabled the queue-preservation path it was
meant to improve.

A1.9 Phase A is measurement only.  It exercises the queue-preserving exit
classifier in shadow mode -- computing and logging a HOLD/REPRICE decision that
is deliberately discarded -- and records exit tenure, why a resting quote
vanished, forgone edge, and cancel-acknowledgement latency.  Runtime behaviour
is identical to the A1.7.5 baseline.  Phase B is what acts on the classifier.
Strategy1_Research.py remains unchanged.

The original Strategy1_Research.py is left untouched so this candidate can be
A/B tested against the V4.16.2 baseline.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import replace
import json
import math
from contextlib import contextmanager
import os
import sys
import time
from typing import Any

# TAOS loads this agent dynamically by file path, so the sibling strategy
# directory is not guaranteed to be on sys.path. Make sibling imports robust
# for both the miner runtime and direct/preflight imports.
_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from taos.common.agents import launch
from taos.im.protocol import FinanceAgentResponse, MarketSimulationStateUpdate
from taos.im.protocol.models import LoanSettlementOption, OrderDirection, STP, TimeInForce

from Strategy1 import Strategy1
from Strategy1_Research import Strategy1_Research
from DetailedTemplateAgent import BookSelection
from research_candidate_screen import ScreenResult
from research_direct_economics import (
    ACTION_MAKER as EXEC_ACTION_MAKER,
    ACTION_SKIP as EXEC_ACTION_SKIP,
    ACTION_TAKER as EXEC_ACTION_TAKER,
    DIRECT_ECONOMICS_VERSION,
    DIRECT_EXECUTION_CONTROLLER_VERSION,
    DIRECT_MAKER_MIN_EV,
    DIRECT_MAKER_MIN_EDGE_BPS,
    DIRECT_TAKER_MIN_EV,
    DIRECT_TAKER_MIN_EDGE_BPS,
    DIRECT_TAKER_ENTRY_ENABLED,
    choose_direct_execution,
)
from research_direct_quality import (
    COLD_START_TAKER_RATE,
    DIRECT_QUALITY_VERSION,
    MIGRATED_QUALITY_INITIAL_WEIGHT,
    MIGRATED_QUALITY_FULL_WEIGHT_SAMPLES,
    MIGRATED_QUALITY_GLOBAL_FULL_WEIGHT_SAMPLES,
    MakerLifecycleStats,
    maker_quality_adjustment,
    maker_realization_cost_estimate,
)
from research_direct_execution_quality import (
    DIRECT_DUST_EXEMPT_CAP,
    DIRECT_EXECUTION_QUALITY_VERSION,
    DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS,
    cap_maker_quote_geometry,
    dust_exempt_count,
    effective_total_open_books,
)
from research_direct_fastpath import (
    DIRECT_FASTPATH_VERSION,
    DIRECT_FASTPATH_CANDIDATE_COUNT,
    DIRECT_FASTPATH_DEEP_COUNT,
    DIRECT_EDGE_FAIL_STREAK,
    DIRECT_EDGE_COOLDOWN_TICKS,
    DIRECT_MAX_PRE_SUBMIT_AGE_MS,
    DIRECT_TELEMETRY_SAMPLE_TICKS,
    FastPathRow,
    cheap_priority,
    clamp_candidate_count as direct_fastpath_candidate_count,
    select_fastpath_rows,
    observable_maker_edge_bps,
)
from research_neutral_prediction import is_neutral_forecast, prediction_source_of
from research_score_ev import ScoreEVBreakdown
from research_direct_exit import (
    DIRECT_OBSERVABLE_EXIT_VERSION,
    DIRECT_MAKER_EXIT_TARGET_BPS,
    choose_observable_position_exit,
)
import importlib
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_PARK_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    BAND_ABSOLUTE,
    new_exposure_allowed,
)
from research_unified_exit import completion_net_bps as unified_completion_net_bps
from research_risk_guard import evaluate_risk_guard
from research_exit_quantity import round_volume
from research_contract_guard import resolve_book_from_state_mapping, sanitize_post_only_limit_price
from research_direct_exposure import (
    DIRECT_EXPOSURE_VERSION,
    add_order_to_batch,
    outstanding_reservation,
    worst_case_abs_inventory,
)
from research_direct_quote_manager import (
    DIRECT_QUOTE_MANAGER_VERSION,
    DIRECT_QUOTE_MAX_TTL_MS,
    ACTION_KEEP as QUOTE_ACTION_KEEP,
    decide_quote_batch,
    keep_unselected_quote,
    maker_expiry_ns_for_regime,
)
from research_direct_tail_recovery import (
    DIRECT_TAIL_RECOVERY_VERSION,
    DIRECT_RECOVERY_TRIGGER_BPS,
    DIRECT_RECOVERY_FORCE_BPS,
    DIRECT_RECOVERY_MIN_AGE_TICKS,
    DIRECT_RECOVERY_WORSENING_BPS_PER_TICK,
    DIRECT_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_RECOVERY_MAKER_ADVANTAGE_BPS,
    DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS,
    DIRECT_RECOVERY_TAKER_FLOOR_BPS,
    DIRECT_RECOVERY_TAKER_MIN_FAILED_EXITS,
    DIRECT_EXPECTED_HARD_TAKER_LOSS_BPS,
    DIRECT_TAIL_HISTORY_MAX,
    choose_tail_recovery_override,
    is_recovery_maker_reason,
    is_recovery_taker_reason,
    recovery_maker_floor_for_reason,
    risk_velocity_bps_per_tick as direct_tail_risk_velocity,
)
from research_direct_positive_maker_kappa import (
    DIRECT_POSITIVE_MAKER_KAPPA_VERSION,
    DIRECT_A1744_STRONG_MAKER_FLOOR_BPS,
    DIRECT_A175_MAKER_ADVANTAGE_BPS,
    apply_positive_maker_kappa_veto,
    classify_a1744_outcome,
)
from research_direct_quiet_entry import (
    DIRECT_QUIET_ENTRY_VERSION,
    DIRECT_A1745_QUIET_ZERO_REBATE_MIN_EDGE_BPS,
    DIRECT_A1745_ZERO_REBATE_FLOOR_BPS,
    DIRECT_A1745_LOW_TRADE_RATE_MAX,
    DIRECT_A1745_WIDE_SPREAD_MIN_BPS,
    quiet_zero_rebate_entry_gate,
)
from research_direct_exit_refresh import (
    ABSENT_ENTRY_QUOTE_CANCEL,
    ABSENT_REPRICE_CANCEL,
    ABSENT_EXPIRED,
    ABSENT_FILLED,
    ABSENT_LEDGER_SWEEP,
    ABSENT_NEG_AGGRESSIVE_CANCEL,
    ABSENT_NEVER_PLACED,
    ABSENT_ORPHAN_CANCEL,
    ABSENT_PARTIAL_REMAINDER_CANCEL,
    ABSENT_WAIT_CANCEL,
    AGENT_CANCEL_DISPOSITIONS,
    DIRECT_A19_BASELINE_PROFITABLE_EXIT_TTL_MS,
    DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS,
    DIRECT_EXIT_REFRESH_VERSION,
    EVAL_PERSIST_ELIGIBLE,
    EXIT_HOLD,
    EXIT_REPRICE,
    REASON_QUEUE_PRESERVED,
    behind_ticks,
    classify_resting_maker_exit,
    exit_eval_class,
    forgone_edge_bps,
)
from research_direct_exit_ledger import (
    DIRECT_EXIT_LEDGER_VERSION,
    LEDGER_REMOVED_CANCELLED,
    LEDGER_REMOVED_FILLED,
    LEDGER_REMOVED_TTL_SWEEP,
    DirectExitLedger,
    RestingInventoryView,
    close_side_for,
)
from research_direct_trade_dedup import (
    DIRECT_TRADE_DEDUP_VERSION,
    DIRECT_TRADE_DEDUP_MAX_EVENTS,
    DirectTradeEventDeduper,
)
from research_direct_inflight_reservation import (
    DIRECT_INFLIGHT_RESERVATION_VERSION,
    PendingExposureOrder,
    pending_order_live,
    reduce_pending_quantity,
)
from research_direct_book_ownership import (
    DIRECT_BOOK_OWNERSHIP_VERSION,
    canonical_order_side,
    ownership_key,
    reserve_pending_order,
    ExchangeOrderIdentity,
    register_exchange_identity,
    reduce_identity_quantity,
    cancellation_identity_decision,
)
from research_direct_dust_kappa import (
    DIRECT_DUST_KAPPA_VERSION,
    DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS,
    DIRECT_A175_DUST_PATIENCE_TICKS,
    DIRECT_A175_DUST_ESCALATION_TICKS,
    DIRECT_A175_DUST_MAX_FLOOR_BPS,
    REASON_AGE_ESCALATED,
    decide_kappa_safe_dust_compaction,
)
from research_direct_reconcile import (
    A195_RECONCILE_VERSION,
    A195_DEFAULT_TOLERANCE_BASE,
    A195_MAX_DETAIL_ROWS,
    reconcile_books,
    venue_net_base,
)
from research_direct_dust_capacity import (
    A195_DUST_CAPACITY_VERSION,
    A195_DUST_CLASS_MAX_CLIPS,
    dust_capacity_report,
    dust_class_exempt_abs,
)
from research_direct_taker_bound import (
    A195_DEFAULT_TAKER_FLOOR_BPS,
    A195_TAKER_BOUND_VERSION,
    slippage_fraction_for_floor,
    taker_bound_report,
)
from research_direct_inventory_truth import (
    A195_INVENTORY_TRUTH_VERSION,
    A195_MAX_SEED_ABS_BASE,
    A195_MAX_SEED_BOOKS,
    SEED_LEGACY_DUST,
    SEED_REAL,
    build_seed_plan,
    legacy_dust_ceiling_bonus,
)
from research_direct_breadth_lane import (
    A195_BREADTH_LANE_VERSION,
    A195_BREADTH_RELIEF_COOLDOWN_TICKS,
    A195_MAX_BREADTH_RELIEF_PER_TICK,
    evaluate_breadth_relief,
)
from research_direct_legacy_baseline import (
    A196_INHERITED_PARKED_MAX_FRACTION,
    A196_LEGACY_BASELINE_VERSION,
    NO_INHERITED_EXEMPTION,
    admission_decomposition as a196_admission_decomposition,
    inherited_parked_exemption,
    ledger_abs as a196_ledger_total_abs,
    snap_instruction_quantities,
    snap_quantity,
    split_seed_plan,
)
from research_direct_venue_integrity import (
    A1961_VENUE_INTEGRITY_VERSION,
    SEED_DROP,
    SEED_NOW,
    apply_fee_residue,
    base_fee_units,
    pending_seed_step,
    route_unpriced_books,
    taker_outcome,
    touch_mid,
    valid_touch,
)
from research_direct_postfill_protection import (
    A197_FLIP_WATCH_TICKS,
    A197_POSTFILL_PROTECTION_VERSION,
    SKIP_INVENTORY_OPENED,
    SKIP_LIVE_ORDER,
    SKIP_NO_PROFILE,
    SKIP_NOT_MANAGED,
    book_cancelled_order_ids,
    book_instruction_kinds,
    close_gap,
    note_gap_skip,
    open_gap,
    position_mark_bps,
    postfill_protect_eligible,
    sign_flipped,
    strip_book_limit_orders,
)
from research_direct_absolute_authority import (
    A198_ABSOLUTE_AUTHORITY_VERSION,
    ARM_RECOVERY_MAKER,
    ARM_RELATIVE_VETO,
    restore_absolute_taker,
)
from research_direct_idle_gc import (
    A1992_IDLE_DELAY_MS,
    A1992_IDLE_GC_VERSION,
    IdleCollector,
    running_loop,
)
from research_v5_analytics import (
    V500_ANALYTICS_VERSION,
    V500_TAPPED_ROWS,
    TradeAnalytics,
)
from research_v5_score_mirror import (
    V500_SCORE_MIRROR_VERSION,
    VALIDATOR_SCORING_DEFAULTS,
    mirror_score,
)
from research_v5_activity import (
    EVIDENCE_FIRST_STATE,
    EVIDENCE_OBSERVATION,
    KAPPA_MIN_LOOKBACK_NS,
    REBASE_MIN_JUMP_NS,
    SCORING_INTERVAL_NS,
    STATE_ACTIVATED,
    STATE_COLD,
    STATE_GATE_CLOSED,
    STATE_INCOMPLETE,
    V501_ACTIVITY_VERSION,
    ActivationScoreEV,
    ActivityBelief,
    activity_view,
    cliff_needed,
)
from research_v5_dust_liveness import (
    RESIDUE_FLAT,
    RESIDUE_NONE,
    V502_DUST_LIVENESS_VERSION,
    V502_STATE_EVERY_TICKS,
    clip_tolerance_base,
    flat_residue,
    refusal_cooldown_ticks,
    unique_market_reservation,
)
from research_v5_newcomer_gate import (
    OPEN_EXPOSURE,
    OPEN_GATE_REACHED,
    OPEN_RESTORED,
    V503_NEWCOMER_GATE_VERSION,
    arm_decision,
    effective_anchor,
    exposure_abs,
    gate_state,
    gate_timestamp,
    prior_evidence,
    restored_session,
    seconds_to_gate,
    session_state,
    should_open,
)
from research_v5_observatory import (
    BUDGET_DISK,
    BUDGET_PAYLOAD,
    V503_DEPTH_EVERY,
    V503_OBSERVATORY_VERSION,
    V504_DISK_BUDGET_MB,
    V504_DISK_BUDGET_VERSION,
    StateRecorder,
)
from research_v603_recovery import (
    DISPOSITION_HOLD as V603_DISPOSITION_HOLD,
    DISPOSITION_LEGACY as V603_DISPOSITION_LEGACY,
    DISPOSITION_RELEASE as V603_DISPOSITION_RELEASE,
    V603_RECOVERY_VERSION,
    V603_STATE_EVERY_TICKS,
    row_disposition as v603_row_disposition,
)
from research_v61_lot_floor import (
    REWRITE_IDLE_TO_MAKER as V61_REWRITE_IDLE,
    fifo_close_net_bps as v61_fifo_close_net_bps,
    REWRITE_NONE as V61_REWRITE_NONE,
    REWRITE_TAKER_TO_MAKER as V61_REWRITE_TAKER,
    V61_LOT_FLOOR_VERSION,
    V61_STATE_EVERY_TICKS,
    floor_price as v61_floor_price,
    floored_close_price as v61_floored_close_price,
    head_lot as v61_head_lot,
    rewrite_exit_action as v61_rewrite_exit_action,
    taker_close_pnl as v61_taker_close_pnl,
)
from research_v61_state_gap import (
    DEFAULT_STEP_NS as V61_DEFAULT_STEP_NS,
    STEP_GAP as V61_STEP_GAP,
    STEP_REPEAT as V61_STEP_REPEAT,
    V61_DIVERGENCE_CHECK_EVERY_TICKS,
    V61_GAP_STATE_EVERY_TICKS,
    V61_STATE_GAP_VERSION,
    classify_state_step as v61_classify_state_step,
    confirmed_divergence as v61_confirmed_divergence,
    diverged_books as v61_diverged_books,
)
from research_v611_wire import (
    V611_STATE_EVERY_TICKS,
    V611_VERSION,
    lift_instruction_prices as v611_lift_instruction_prices,
)
from research_v62_breadth import (
    REASON_OK as V62_REASON_OK,
    SKIP_REASONS as V62_SKIP_REASONS,
    V62_STATE_EVERY_TICKS,
    V62_VERSION,
    BookFacts as V62BookFacts,
    entry_client_ids as v62_entry_client_ids,
    lot_quantity as v62_lot_quantity,
    touch_prices as v62_touch_prices,
    universe_caps as v62_universe_caps,
    universe_verdict as v62_universe_verdict,
)
from research_v62_making_mirror import (
    DEFAULT_LOOKBACK_NS as V62_DEFAULT_LOOKBACK_NS,
    MakingMirror as V62MakingMirror,
)
from research_v623_premium_floor import (
    BOOK_PREMIUM as V623_BOOK_PREMIUM,
    KAPPA_MIN_REALIZED_OBSERVATIONS as V623_MIN_OBSERVATIONS,
    PUBLISH_STEP_NS as V623_PUBLISH_STEP_NS,
    V623_PREMIUM_FLOOR_VERSION,
    VOLUME_DECIMALS as V623_VOLUME_DECIMALS,
    book_status as v623_book_status,
    census_counts as v623_census_counts,
    window_census as v623_window_census,
)
from research_v625_cap_paced import (
    BAND_CLIPS as V625_BAND_CLIPS,
    PACE_SAMPLE_NS as V625_PACE_SAMPLE_NS,
    REASON_BAND as V625_REASON_BAND,
    REASON_CAP_RESERVE as V625_REASON_CAP_RESERVE,
    REASON_EXIT_SIDE as V625_REASON_EXIT_SIDE,
    REASON_OK as V625_REASON_OK,
    SIDE_BUY as V625_SIDE_BUY,
    SIDE_SELL as V625_SIDE_SELL,
    V625_CAP_PACED_VERSION,
    BookPace as V625BookPace,
    band_for as v625_band_for,
    cap_reserve_ok as v625_cap_reserve_ok,
    clip_ceiling as v625_clip_ceiling,
    lots_of as v625_lots_of,
    with_cap_ok as v625_with_cap_ok,
    observed_rate as v625_observed_rate,
    pace_snapshot as v625_pace_snapshot,
    pace_target_rate as v625_pace_target_rate,
    paced_clip as v625_paced_clip,
    sides_verdict as v625_sides_verdict,
)
from research_v626_balanced_maker import (
    REASON_SIDE_OWNED as V626_REASON_SIDE_OWNED,
    REASON_SURPLUS as V626_REASON_SURPLUS,
    V626_BALANCED_MAKER_VERSION,
    balance_ratio as v626_balance_ratio,
    book_blocked as v626_book_blocked,
    median_budget as v626_median_budget,
    open_book_caps as v626_open_book_caps,
    side_already_instructed as v626_side_already_instructed,
    side_clips as v626_side_clips,
    skip_reason as v626_skip_reason,
    spend_allowed as v626_spend_allowed,
)
from research_v601_capacity import (
    LIVE_BLEND_WEIGHTS as V601_LIVE_BLEND_WEIGHTS,
    V601_CAPACITY_VERSION,
    V601_STATE_EVERY_TICKS,
    is_workable_dust as v601_is_workable_dust,
    live_trading_ex_debeta as v601_live_trading_ex_debeta,
    reserve_dust_count as v601_reserve_dust_count,
)
from research_v600_short_lots import (
    CLASS_DUST as V600_CLASS_DUST,
    CLASS_FLAT as V600_CLASS_FLAT,
    CLASS_PARKED as V600_CLASS_PARKED,
    CLASS_SHORT_LOT as V600_CLASS_SHORT_LOT,
    INHERITED_EXIT as V600_INHERITED_EXIT,
    INHERITED_PARK as V600_INHERITED_PARK,
    SOURCE_INHERITED as V600_SOURCE_INHERITED,
    SOURCE_LIVE as V600_SOURCE_LIVE,
    SOURCE_OFF_GRID as V600_SOURCE_OFF_GRID,
    V600_SHORT_LOT_FRACTION_DEFAULT,
    V600_SHORT_LOTS_VERSION,
    V600_STATE_EVERY_TICKS,
    classify_position as v600_classify_position,
    exit_from_fill as v600_exit_from_fill,
    inherited_mode as v600_inherited_mode,
    leftover_to_residue as v600_leftover_to_residue,
    leftover_tolerance as v600_leftover_tolerance,
    median as v600_median,
    parse_fraction as v600_parse_fraction,
    short_lot_boundary as v600_short_lot_boundary,
)
from research_v5_session_identity import (
    ANCHOR_AUTO,
    KAPPA_LOOKBACK_NS,
    LEGACY_ADOPT,
    LEGACY_IGNORE,
    MODE_INVALID,
    OWNER_KEY,
    V504_REGISTRATION_IDENTITY_VERSION,
    choose_session,
    legacy_mode,
    owner_record,
    parse_history_anchor,
    resolve_history_pin,
    uid_session_path,
)
from research_v5_mirror_rounds import (
    BASIS_OBSERVED,
    BASIS_VALIDATOR_GRID,
    SESSION_KEY as V504_MIRROR_SESSION_KEY,
    V504_MIRROR_ROUNDS_VERSION,
    merge_history,
    new_mirror_state,
    positive_step,
    rebase_keys,
    rebuild_history,
    resolve_start,
    restored_start as mirror_restored_start,
    session_state as mirror_session_state,
    validator_rounds,
)
from research_v5_validator_fifo import (
    V503_VALIDATOR_FIFO_VERSION,
    match_trade_fifo as v503_match_trade_fifo,
)
from research_direct_risk_state import (
    A1991_PENDING_OWNER_VERSION,
    A199_RISK_STATE_VERSION,
    CLEAR_EPOCH,
    CLEAR_FLAT,
    CLEAR_NEW_POSITION,
    ENTER,
    RULE_LOSS_MAKER,
    RULE_NOT_EXITING,
    RULE_POSITIVE_MAKER,
    authorize_exit,
    end_exit_stall,
    note_exit_stall,
    step_exit_pending,
    two_sided_touch,
)
from research_direct_session_epoch import (
    A199_RESYNC_MAX_TICKS,
    A199_RESYNC_MIN_TICKS,
    A199_SESSION_EPOCH_VERSION,
    RESEED_CLIP,
    RESEED_DEFER,
    RESEED_DUST,
    RESEED_KEEP,
    RESEED_REAL,
    clear_book_runtime,
    clear_epoch_registries,
    detect_rewind,
    plan_book_reseed,
    strip_exposure_increasing,
)
# Only for the balance-vs-position comparison in A195_RECONCILE: this returns
# Balance.total, not a net position, which is exactly what the observer exists
# to make visible.  Nothing in this file treats its output as inventory.
from research_session_state import extract_simulation_id, reconcile_account_base
from research_direct_liveness import (
    DIRECT_LIVENESS_VERSION,
    DIRECT_DUST_NORMALIZE_MIN_AGE_TICKS,
        DIRECT_STALE_DUST_NORMALIZE_AGE_TICKS,
    DIRECT_LIVENESS_TRIGGER_TICKS,
    DIRECT_PARTIAL_HOLD_MAX_NS,
    DIRECT_PARTIAL_HOLD_PUBLISH_MULT,
    admission_slots as direct_liveness_admission_slots,
    dust_recovery_reserve_abs,
    is_dust_inventory as direct_is_dust_inventory,
    normalization_allowed as direct_normalization_allowed,
    partial_recovery_plan,
    recovery_expiry_ns as direct_recovery_expiry_ns,
    bound_remainder_hold_active as direct_bound_remainder_hold_active,
    partition_bound_remainder_orders as direct_partition_bound_remainder_orders,
)


# A1.9.1.1: phase and behaviour-change are derived from the runtime enable flag
# and reported from ONE place.  The A1.9.1 run shipped engine_version=a1_9_1
# while three separate hardcoded sites still reported Phase A shadow mode, which
# is exactly the silent hybrid the preflight now refuses to launch.
DIRECT_A19_PHASE_BEHAVIOURAL = "B_QUEUE_PRESERVING_EXIT"
DIRECT_A19_PHASE_SHADOW = "A_SHADOW_MEASUREMENT"
# The events a valid Phase B run must produce.  Named here so an analysis can
# grep the config row rather than guess: A191_*, not A19_*.
DIRECT_A19_PHASE_B_EVENTS = (
    "A19_QUEUE_HOLD", "A19_EXIT_REPRICE_CANCEL", "A19_REPRICE_BUDGET_BLOCK",
)

# ---- A1.9.2 fee-conditioned book risk admission -----------------------------
# A1.9.1.2 fixed exit realization but the 6,607-tick run still lost: 99.9% of
# cubic downside came from Taker endings and 93.3% from ABSOLUTE_PROTECTION
# alone, while Maker endings contributed 0.1%.  More exit repricing cannot
# reach that, because 36 of 47 ABSOLUTE lifecycles never had a strong Maker
# exit to preserve in the first place.
#
# The measured precursor is NOT bad book history on its own.  Reconstructing
# 736 round trips and building the control group the first analysis lacked:
#
#   prior RT count            AUC 0.502   <- no signal whatsoever
#   prior cumulative PnL      AUC 0.629
#   entry Maker fee           AUC 0.669
#
#   fee <= 0 & good history   n=142   bad 7.7%   PnL +25.08   cubic  1.23
#   fee <= 0 & poor history   n= 21   bad 0.0%   PnL  +4.95   cubic  0.00
#   fee >  0 & good history   n=323   bad 18.9%  PnL -14.94   cubic  2.15
#   fee >  0 & poor history   n=250   bad 30.4%  PnL -30.61   cubic 23.57
#
# A poor-history book entered at a rebate produced zero bad round trips.  The
# harm lives in the conjunction, so quarantining on history alone would have
# suppressed 21 profitable trips to no purpose.  One cell -- 34% of round
# trips -- carries 87% of all cubic downside.
DIRECT_A192_PHASE_BEHAVIOURAL = "C_FEE_CONDITIONED_BOOK_ADMISSION"
DIRECT_A192_PHASE_DISABLED = "C_DISABLED"
# The events a valid A1.9.2 run must produce.  Named here so an analysis greps
# the config row instead of guessing, the way two A1.9.1 runs were lost.
DIRECT_A192_EVENTS = (
    "A192_ENTRY_SUPPRESSED", "A192_CAP_BLOCK", "A192_BOOK_RECOVERED",
    "A192_SEVERITY_DEFER", "A192_COLDSTART_SHRINK", "A192_SEVERITY_SEED",
)
# Admission verdicts.
A192_ALLOW_DISABLED = "ALLOW_DISABLED"
A192_ALLOW_REBATE = "ALLOW_REBATE_ENTRY"
A192_ALLOW_NO_HISTORY = "ALLOW_INSUFFICIENT_HISTORY"
A192_ALLOW_BOOK_OK = "ALLOW_BOOK_QUALITY_OK"
A192_ALLOW_CAP = "ALLOW_SUPPRESSION_CAP"
A192_SUPPRESS_FLAGGED = "SUPPRESS_FEE_AND_BOOK_RISK"
A192_SUPPRESS_DWELL = "SUPPRESS_DWELL_ACTIVE"
# A1.9.2.1: the budget was spent on a worse candidate, so this one is admitted
# even though it is flagged.  Distinct from ALLOW_SUPPRESSION_CAP, which means
# the budget was already exhausted for the window.
A192_ALLOW_SEVERITY_RANK = "ALLOW_SEVERITY_RANK"

# The events a valid A1.9.3 run must produce.
DIRECT_A193_EVENTS = ("A193_BREADTH_ADMIT", "A193_BREADTH_DENY")
# A1.9.3 breadth-critical admission verdicts.  COMPLETION means one round trip
# qualifies the book; REFRESH means one round trip stops it de-qualifying.
A193_ALLOW_COMPLETION = "ALLOW_BREADTH_COMPLETION"
A193_ALLOW_REFRESH = "ALLOW_BREADTH_REFRESH"

# The events a valid A1.9.4 run must produce.  A1.9.3 shipped inert and cost a
# whole run because nothing announced it; every phase since names its events
# here so an analysis greps the banner instead of guessing.
DIRECT_A194_EVENTS = ("A194_REBATE_COVERED", "A194_REBATE_WAIVER_WITHDRAWN")
# A1.9.4 admission verdicts.  COVERED means the rebate exceeds the realized
# harm the book has actually done, so being paid genuinely offsets it.
A194_ALLOW_REBATE_COVERED = "ALLOW_REBATE_COVERED"

SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_6"
SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_6"

# v5.0.0 analytics cadence, in requests.  The score mirror took under 3 ms at 8,400 rounds.
V500_SCORE_EVERY_TICKS = 100
V500_ROLLUP_EVERY_TICKS = 500
# v5.0.1.  A cold book -- Kappa-eligible, activity factor 0.0 -- is one round trip from counting,
# as a one-away book is, so the rank values it at the one-away completion value of
# _research_score_ev_for_book.  The state row goes out on the score mirror's cadence.
V501_ACTIVATION_VALUE = 0.20
V501_STATE_EVERY_TICKS = 100
# v5.0.3 G1.  While the quiet gate holds instructions it reports on this cadence, and on every
# change of state, so a held agent is never silent about why.
V503_GATE_EVERY_TICKS = 100
# v5.0.3 G2/G4.  The observatory's own telemetry row, and the per-book Kappa table the offline
# comparison against the validator's published per-book gauges reads.
V503_OBSERVATORY_EVERY_TICKS = 500
V503_BOOK_KAPPA_EVERY_TICKS = 500
# v5.0.4.  The identity state row: on the first request, then on this cadence.
V504_STATE_EVERY_TICKS = 500
# v5.0.4 H2.  The gate's reason for not arming when the operator declares an established UID.
V504_EVIDENCE_DECLARED_ESTABLISHED = "DECLARED_ESTABLISHED"

# A1.7.5 bounded hold.  Consecutive vetoed ticks allowed per book before the
# base risk decision is restored.  Sized from the A1.7.4.5 runtime, where an
# unbounded veto produced inventory_age_p90 = 1,329 ticks.
DIRECT_A175_TAIL_BUDGET_TICKS = 60

# A1.7.5 QUIET shadow measurement.  Diagnostic only.
DIRECT_A175_SHADOW_HORIZON_TICKS = 200
DIRECT_A175_SHADOW_LEDGER_MAX = 256


class Strategy1_Research_Simple(Strategy1_Research):
    """V4.16.2 A1.9.2 fee-conditioned book risk admission on A1.9.1.2.

    What is deliberately removed from the hot entry path:
      * maintenance as a separate economic authority;
      * separate alpha-entry lane;
      * quote-attempt/success lane caps;
      * stale/rescue/TTL/hysteresis entry authorities;
      * duplicate PnL/fill gates after LifecycleEV;
      * old avoid-list economics as a hard gate.

    What remains authoritative:
      * Research fast screen / Kappa workload selection;
      * hard mechanical risk checks;
      * A1.6 observable spread/fee/Kappa FastPath;
      * current Maker edge in bps is the only economic entry authority;
      * Maker-only acquisition; directional Taker entry stays disabled;
      * A1.7.1 true-MTM Maker/Wait/Taker exit authority for non-flat inventory;
      * A1.7.2 hard execution invariant: WAIT cannot place a new Maker exit;
      * A1.7.3.1 exact-order partial-remainder ownership before dust normalization;
      * A1.7.4 pre-HARD genuine tail-risk recovery with bounded concessions;
      * A1.7.4.1 exact own-TradeEvent replay de-dup before FIFO/PnL/fill learning;
      * A1.7.4.2 Kappa loss-budget guard on moderate-dust sign-cross compaction;
      * A1.7.4.3 local pending-order reservation so submitted-but-unacknowledged orders cannot race the hard portfolio cap;
      * A1.7.4.3.1 same-book/side ownership across both current-response and pending placement gaps;
      * A1.7.4.3.2 exact exchange-order identity release; stale cancellation cannot release a newer owner;
      * A1.7.4.4 strongly-positive Maker veto over negative non-catastrophic HARD/ABSOLUTE Taker authority;
      * A1.7.4.5 15 bps entry floor only in QUIET/no-rebate/low-trade/wide-spread books;
      * A1.9.1 queue-preserving Maker exit: hold by queue position, cancel only on structural staleness;
      * A1.9.1.2 exact-identity ownership release on a confirmed reprice cancellation;
      * A1.9.2 fee-conditioned book risk admission: fresh Maker entry only, bounded and decaying;
      * one-clip exposure/active-slot reserve while dust exists;
      * final authoritative contract validation;
      * existing Research learning/session state.
    """

    RESEARCH_POLICY_VERSION = SIMPLE_POLICY_VERSION
    RESEARCH_ENGINE_VERSION = SIMPLE_ENGINE_VERSION
    RESEARCH_ENGINE_REVISION = SIMPLE_ENGINE_VERSION

    def initialize(self) -> None:
        super().initialize()
        # Marker only.  Do not mutate strategy thresholds or risk limits here.
        self._simple_direct_mode = True
        # A1.9 observation state.  The exit TTL is NOT mutated here: A1.8 set
        # research_profitable_exit_ttl_ms in initialize() and that is exactly
        # what must not happen again.  A1.9.1 raises it to 4000 ms through
        # PARAMS, where the frozen base clamps it to [1000, 5000] and the value
        # is visible in the run manifest.
        self._a19_reset_exit_observation()
        self._a192_reset_admission()
        self._init_build_switches()
        self._init_direct_overlay_state()

    def _init_build_switches(self) -> None:
        """Read every research_* switch this overlay owns, one contiguous block per build.

        Split out of initialize() verbatim (v6.0.3 refactor).  It reads nothing but ``self`` and
        no local crosses its boundary, so adding the next build's switch touches this method and
        nothing else, instead of a 698-line constructor every build edits in the same place.
        """
        # A1.9.1 Phase B master switch, so the behavioural half can be turned
        # off without reverting to an older build during an abort.
        self.research_a191_queue_preservation_enabled = self._as_bool(
            getattr(self.config, "research_a191_queue_preservation_enabled", True)
        )
        # A1.9.2 master switch, so an abort can disable the admission gate
        # without reverting to an older build mid-run.
        self.research_a192_book_risk_admission_enabled = self._as_bool(
            getattr(self.config, "research_a192_book_risk_admission_enabled", True)
        )
        # A1.9.2.1 severity-prioritised budget.  Separate switch so the
        # allocation change can be disabled without turning the A1.9.2 risk
        # detector off, which is what makes the two attributable apart.
        self.research_a1921_severity_priority_enabled = self._as_bool(
            getattr(self.config, "research_a1921_severity_priority_enabled", True)
        )
        # A1.9.4 rebate conjunction.  Separate switch so the waiver change can
        # be reverted mid-run without disabling the A1.9.2 detector underneath
        # it, which is what keeps the two attributable apart.
        self.research_a194_rebate_conjunction_enabled = self._as_bool(
            getattr(self.config, "research_a194_rebate_conjunction_enabled", True)
        )
        # A1.9.5 step 1.  Measurement only: compares the local position tracker
        # against the venue accounts and emits A195_RECONCILE.  It places no
        # orders and mutates no state, so unlike the other a19x switches it is
        # safe to leave on -- but it is still a switch, so a noisy universe can
        # be silenced without a rebuild.
        self.research_a195_reconcile_observe = self._as_bool(
            getattr(self.config, "research_a195_reconcile_observe", True)
        )
        self.research_a195_reconcile_tolerance_base = max(
            0.0,
            float(getattr(
                self.config,
                "research_a195_reconcile_tolerance_base",
                A195_DEFAULT_TOLERANCE_BASE,
            )),
        )
        self._a195_reconcile_emits = 0
        self._a195_reconcile_diverged_books: set[int] = set()
        self._a195_reconcile_unresolved_books: set[int] = set()
        self._a195_reconcile_max_abs_divergence = 0.0
        self._a195_reconcile_last: dict[str, Any] = {}
        # A1.9.5 step 2 (F3): parked dust is its own capacity class.  A1.6.1
        # excused dust from the open-BOOK count but never from the aggregate
        # absolute-BASE budget, and the BASE budget is what actually closed the
        # agent -- at tick 10,000 dust held 1.3257 of a 2.0 cap, leaving
        # abs_slots = 0 while book slots sat at active 1/6, open 1/8.
        self.research_a195_dust_capacity_class = self._as_bool(
            getattr(self.config, "research_a195_dust_capacity_class", True)
        )
        self.research_a195_dust_class_max_clips = max(0.0, float(getattr(
            self.config,
            "research_a195_dust_class_max_clips",
            A195_DUST_CLASS_MAX_CLIPS,
        )))
        self._a195_dust_capacity_emits = 0
        self._a195_dust_capacity_slots_recovered = 0
        self._a195_dust_capacity_max_exempt_abs = 0.0
        self._a195_dust_capacity_overflow_ticks = 0
        self._a195_dust_capacity_last: dict[str, Any] = {}

        # A1.9.5 step 2.5 (F8): make the declared taker loss floor bind.
        self.research_a195_taker_floor_enforce = self._as_bool(
            getattr(self.config, "research_a195_taker_floor_enforce", True)
        )
        self.research_a195_taker_floor_bps = -abs(float(getattr(
            self.config, "research_a195_taker_floor_bps",
            A195_DEFAULT_TAKER_FLOOR_BPS,
        )))
        self._a195_taker_bound_applied = 0
        self._a195_taker_bound_zero_floor = 0
        self._a195_taker_bound_min_fraction = 0.0
        self._a195_taker_bound_last: dict[str, Any] = {}

        # A1.9.5 step 3 (F2 behaviour): local inventory seeded from venue truth.
        self.research_a195_inventory_truth_enabled = self._as_bool(
            getattr(self.config, "research_a195_inventory_truth_enabled", True)
        )
        self.research_a195_startup_orphan_cancel = self._as_bool(
            getattr(self.config, "research_a195_startup_orphan_cancel", True)
        )
        self.research_a195_max_seed_books = max(0, int(getattr(
            self.config, "research_a195_max_seed_books", A195_MAX_SEED_BOOKS,
        )))
        self.research_a195_max_seed_abs_base = max(0.0, float(getattr(
            self.config, "research_a195_max_seed_abs_base", A195_MAX_SEED_ABS_BASE,
        )))
        self._a195_seed_done = False
        self._a195_seed_books = 0
        self._a195_seed_real_books = 0
        self._a195_seed_real_abs = 0.0
        self._a195_seed_dust_books = 0
        self._a195_seed_dust_abs = 0.0
        self._a195_seed_legacy_ceiling_bonus = 0.0
        self._a195_seed_last: dict[str, Any] = {}
        self._a195_orphan_cancel_done = False
        self._a195_orphan_orders_cancelled = 0
        self._a195_orphan_books = 0

        # A1.9.5 step 4: breadth authority moved to the A1.7.4.5 boundary.
        self.research_a195_breadth_lane_enabled = self._as_bool(
            getattr(self.config, "research_a195_breadth_lane_enabled", True)
        )
        self.research_a195_breadth_relief_per_tick = max(0, int(getattr(
            self.config, "research_a195_breadth_relief_per_tick",
            A195_MAX_BREADTH_RELIEF_PER_TICK,
        )))
        self.research_a195_breadth_relief_cooldown = max(0, int(getattr(
            self.config, "research_a195_breadth_relief_cooldown",
            A195_BREADTH_RELIEF_COOLDOWN_TICKS,
        )))
        self._a195_breadth_grants = 0
        self._a195_breadth_denies = 0
        self._a195_breadth_deny_reasons: dict[str, int] = {}
        self._a195_breadth_relief_bps_total = 0.0
        self._a195_breadth_last_relief_tick: dict[int, int] = {}
        self._a195_breadth_tick = -1
        self._a195_breadth_granted_this_tick = 0

        # A1.9.6 F9/F10/F11.  Each switch set to 0 restores the A1.9.5
        # behaviour of that one mechanism and nothing else.
        self.research_a196_legacy_dust_ledger = self._as_bool(
            getattr(self.config, "research_a196_legacy_dust_ledger", True)
        )
        self.research_a196_inherited_parked_allowance = self._as_bool(
            getattr(self.config, "research_a196_inherited_parked_allowance", True)
        )
        self.research_a196_quantity_grid_snap = self._as_bool(
            getattr(self.config, "research_a196_quantity_grid_snap", True)
        )
        self.research_a196_inherited_parked_max_fraction = min(1.0, max(0.0, float(getattr(
            self.config, "research_a196_inherited_parked_max_fraction",
            A196_INHERITED_PARKED_MAX_FRACTION,
        ))))
        self._a196_legacy_dust_ledger: dict[int, float] = {}
        self._a196_inherited_real: dict[int, float] = {}
        self._a196_inherited_retired: list[int] = []
        self._a196_inherited_last = NO_INHERITED_EXEMPTION
        self._a196_inherited_exempt_max = 0.0
        self._a196_inherited_capped_samples = 0
        self._a196_seed_last: dict[str, Any] = {}
        self._a196_wire_quantities_moved = 0
        self._a196_admission_samples = 0
        self._a196_admission_zero_samples = 0
        self._a196_admission_last: dict[str, Any] = {}

        # A1.9.6.1: seed only from a quote that is a market, mirror the BASE the
        # venue takes as fees, and judge taker exits against their own decision.
        self.research_a1961_seed_quote_guard = self._as_bool(
            getattr(self.config, "research_a1961_seed_quote_guard", True)
        )
        self.research_a1961_fee_residue_ledger = self._as_bool(
            getattr(self.config, "research_a1961_fee_residue_ledger", True)
        )
        self._a1961_pending_seed: dict[int, dict[str, Any]] = {}
        self._a1961_seed_unpriced_books = 0
        self._a1961_pending_resolved = 0
        self._a1961_pending_dropped = 0
        self._a1961_pending_seeded_abs = 0.0
        self._a1961_base_decimals: int | None = None
        self._a1961_fee_residue: dict[int, float] = {}
        self._a1961_fee_residue_units = 0
        self._a1961_fee_residue_fills = 0
        self._a1961_fee_residue_skipped = 0
        self._a1961_taker_decision: dict[int, dict[str, Any]] = {}
        self._a1961_taker_outcomes = 0
        self._a1961_taker_unmatched = 0
        self._a1961_slippage_breaches = 0
        self._a1961_late_triggers = 0
        self._a1961_worst_slippage_bps = 0.0

        # A1.9.7: evaluate an ABSOLUTE position on the tick after its entry fill
        # (P1), and record the ticks between every opening fill and its first
        # exit evaluation (P3).  P2 is the order-side fix in book ownership.
        self.research_a197_postfill_protect = self._as_bool(
            getattr(self.config, "research_a197_postfill_protect", True)
        )
        self._a197_exit_gap: dict[int, Any] = {}
        self._a197_postfill_book: int | None = None
        self._a197_postfill_watch: dict[int, int] = {}
        self._a197_postfill_acts = 0
        self._a197_postfill_market = 0
        self._a197_postfill_fallback_cancels = 0
        self._a197_postfill_limits_stripped = 0
        self._a197_postfill_maker_refused = 0
        self._a197_postfill_sign_flips = 0
        self._a197_postfill_truncated = 0
        self._a197_exit_gap_rows = 0
        self._a197_exit_gap_ticks_total = 0

        # A1.9.8: ABSOLUTE_PROTECTION keeps its taker against the two loss-recovery
        # maker arms (the A1.7.4 recovery maker and the A1.7.5 relative veto).
        self.research_a198_absolute_taker_authority = self._as_bool(
            getattr(self.config, "research_a198_absolute_taker_authority", True)
        )
        self._a198_restores = 0
        self._a198_restored_recovery = 0
        self._a198_restored_relative = 0
        self._a198_restored_postfill = 0

        # A1.9.9: two explicit controllers.  The position risk state machine owns
        # the exit once a position has reached ABSOLUTE_PROTECTION; the session
        # epoch controller owns venue-coupled state across a simulation clock rewind.
        self.research_a199_exit_pending_authority = self._as_bool(
            getattr(self.config, "research_a199_exit_pending_authority", True)
        )
        self.research_a199_epoch_resync = self._as_bool(
            getattr(self.config, "research_a199_epoch_resync", True)
        )
        self._a199_exit_pending: dict[int, Any] = {}
        self._a199_exit_stalls: dict[int, dict[str, int]] = {}
        self._a199_pending_entered = 0
        self._a199_pending_cleared = 0
        self._a199_rule_loss_maker = 0
        self._a199_rule_not_exiting = 0
        self._a199_stall_rows = 0
        self._a199_stall_max_ticks = 0
        self._a199_last_state_ts: int | None = None
        self._a199_last_sim_id: str | None = None
        self._a199_resync: dict[str, Any] = {}
        self._a199_deferred: dict[int, dict[str, Any]] = {}
        self._a199_epoch_rewinds = 0
        self._a199_epoch_registry_rows_cleared = 0
        self._a199_epoch_reseeds = 0
        self._a199_epoch_entries_blocked = 0
        self._a199_epoch_placements_stripped = 0
        self._a199_epoch_resyncs_closed = 0
        # A1.9.9.1: a pending ABSOLUTE position owns its book -- no maker exit rests
        # at any price.  Off restores A1.9.9, which let a positive maker rest.
        self.research_a1991_pending_owns_book = self._as_bool(
            getattr(self.config, "research_a1991_pending_owns_book", True)
        )
        self._a1991_rule_positive_maker = 0
        # A1.9.9.2: CPython's full collection runs in the idle gap after a response,
        # not inside whichever request allocated.  Off restores A1.9.9.1.
        self.research_a1992_idle_gc = self._as_bool(
            getattr(self.config, "research_a1992_idle_gc", True)
        )
        try:
            a1992_delay_ms = float(getattr(self.config, "research_a1992_idle_gc_delay_ms", A1992_IDLE_DELAY_MS))
        except (TypeError, ValueError):
            a1992_delay_ms = A1992_IDLE_DELAY_MS
        self._a1992_idle_gc = (
            IdleCollector(delay_s=max(0.0, a1992_delay_ms) / 1000.0)
            if self.research_a1992_idle_gc else None
        )
        self._a1992_reported: tuple[int, str, str] | None = None
        # v5.0.0: analytics only -- a ledger fed by the rows this strategy already writes, and
        # a local copy of the validator's score.  Off restores A1.9.9.2's output exactly.
        self.research_v500_analytics = self._as_bool(
            getattr(self.config, "research_v500_analytics", True)
        )
        self._v500_analytics = TradeAnalytics() if self.research_v500_analytics else None
        self._v500_universe_key: tuple | None = None
        self._v500_last_score: dict[str, Any] | None = None
        self._v500_service_errors = 0
        # v5.0.1: a Kappa-eligible book counts in the validator's median only once its activity
        # factor is 1.0.  The fast screen and the rank treat a cold book as one round trip away,
        # as they treat a one-away book.  Off restores v5.0.0's screen and rank exactly.
        self.research_v501_activity_alignment = self._as_bool(
            getattr(self.config, "research_v501_activity_alignment", True)
        )
        self._v501_belief = ActivityBelief() if self.research_v501_activity_alignment else None
        self._v501_tick: int | None = None
        self._v501_last_now: int | None = None
        self._v501_view = None
        self._v501_seen_activated: set[int] = set()
        self._v501_cold_since: dict[int, int] = {}
        self._v501_pending_rows: list[dict[str, Any]] = []
        self._v501_window_reported: bool | None = None
        self._v501_errors = 0
        # v5.0.2: dust liveness from inventory and terminal-order truth.  Each switch off restores
        # v5.0.1 for its own mechanism: F1 flat residue, F2 clip recognition, F3 compactor turn,
        # F4 market terminal.
        self.research_v502_flat_residue = self._as_bool(
            getattr(self.config, "research_v502_flat_residue", True)
        )
        self.research_v502_clip_recognition = self._as_bool(
            getattr(self.config, "research_v502_clip_recognition", True)
        )
        self.research_v502_compactor_turn = self._as_bool(
            getattr(self.config, "research_v502_compactor_turn", True)
        )
        self.research_v502_market_terminal = self._as_bool(
            getattr(self.config, "research_v502_market_terminal", True)
        )
        self._v502_residue: dict[int, float] = {}
        self._v502_refusal_streak: dict[int, int] = {}
        self._v502_market_notices: set[tuple[int, str]] = set()
        self._v502_compaction_seen: set[int] = set()
        self._v502_counts: dict[str, int] = {}
        self._v502_errors = 0
        # v5.0.3 G1: the validator seeds a uid's track-record standing at its FIRST NON-ZERO trading
        # score, and the PnL leg goes non-zero ~5,140 sim-s before the Kappa leg can.  A newcomer
        # therefore sends no instructions until its Kappa gate is open, while still answering every
        # request.  Off restores v5.0.2: trade at once and seed the standing on a PnL-only score.
        self.research_v503_newcomer_gate = self._as_bool(
            getattr(self.config, "research_v503_newcomer_gate", True)
        )
        self._v503_gate_armed: bool | None = None
        self._v503_gate_open_reason: str | None = None
        self._v503_gate_evidence: str | None = None
        self._v503_gate_anchor_ts: int | None = None
        self._v503_gate_ts: int | None = None
        self._v503_gate_quiet_requests = 0
        self._v503_gate_reported: tuple | None = None
        self._v503_gate_last_now: int | None = None
        self._v503_gate_rebases = 0
        self._v503_gate_errors = 0
        # v5.0.3 G2: record every state's per-book trades (with both counterparties' uids) and its
        # depth, from the raw lazy dicts, on a writer thread.  Telemetry only; nothing reads it.
        self.research_v503_state_recorder = self._as_bool(
            getattr(self.config, "research_v503_state_recorder", True)
        )
        try:
            v503_depth_every = int(getattr(self.config, "research_v503_recorder_depth_every", V503_DEPTH_EVERY))
        except (TypeError, ValueError):
            v503_depth_every = V503_DEPTH_EVERY
        # v5.0.4 H3: the budget counts compressed bytes on disk, and its default is sized for that.
        # Off restores v5.0.3: the uncompressed payload is counted against a 2,048 MB default.
        self.research_v504_disk_budget = self._as_bool(
            getattr(self.config, "research_v504_disk_budget", True)
        )
        v503_default_mb = V504_DISK_BUDGET_MB if self.research_v504_disk_budget else 2048
        try:
            v503_max_mb = int(getattr(self.config, "research_v503_recorder_max_mb", v503_default_mb))
        except (TypeError, ValueError):
            v503_max_mb = v503_default_mb
        self._v503_recorder_depth_every = max(0, v503_depth_every)
        self._v503_recorder_max_bytes = max(0, v503_max_mb) * 1024 * 1024
        self._v503_recorder = None
        self._v503_recorder_started = False
        self._v503_recorder_errors = 0
        # v5.0.3 G3: the validator prorates a fill's fee to the part of it that closes a lot; this
        # agent's copy charged the whole fill's fee there, which at a maker rebate overstates
        # realized PnL on every partial close.  Off restores the agent's own arithmetic.
        self.research_v503_fifo_fee_exact = self._as_bool(
            getattr(self.config, "research_v503_fifo_fee_exact", True)
        )
        self._v503_fifo_calls = 0
        # v5.0.3 G4: the mirror's per-book Kappa table, for an exact offline comparison against the
        # validator's published per-book gauges.  Telemetry only.
        self.research_v503_book_kappa_rows = self._as_bool(
            getattr(self.config, "research_v503_book_kappa_rows", True)
        )
        # v5.0.4 H1: the session file carries the miner UID in its name and an owner record inside,
        # and a payload another UID wrote is refused.  A legacy file (no owner) is read only when the
        # operator says it is this UID's (``adopt``).  Off restores v5.0.3's shared file.
        self.research_v504_session_per_uid = self._as_bool(
            getattr(self.config, "research_v504_session_per_uid", True)
        )
        v504_legacy = legacy_mode(getattr(self.config, "research_v504_legacy_session", LEGACY_IGNORE))
        self._v504_legacy_invalid = v504_legacy is None
        self.research_v504_legacy_session = v504_legacy or LEGACY_IGNORE
        # v5.0.4 H2: the start of this UID's validator history, declared by the operator.  ``auto``
        # keeps v5.0.3's inference from evidence.
        self._v504_history_anchor = parse_history_anchor(
            getattr(self.config, "research_v504_history_anchor", ANCHOR_AUTO)
        )
        self.research_v504_history_anchor = self._v504_history_anchor.raw or ANCHOR_AUTO
        self._v504_history_pin = None
        # v5.0.4 H4: the copy of the score runs over the validator's round grid, from the start of this
        # UID's history, with the PnL of rounds before this process rebuilt from the session file.
        # Off restores v5.0.3: only the rounds this process was sent.
        self.research_v504_mirror_rounds = self._as_bool(
            getattr(self.config, "research_v504_mirror_rounds", True)
        )
        self._v504_mirror = new_mirror_state()
        self._v504_session_choice = None
        self._v504_session_reported = None
        self._v504_state_reported = False
        self._v504_stop_reported = False
        self._v504_errors = 0
        # v6.0.0 S1: a position between the boundary and the minimum order is a short lot and is
        # exited like a full lot instead of being parked as dust.  Off restores v5.0.4.
        self.research_v600_short_lots = self._as_bool(
            getattr(self.config, "research_v600_short_lots", True)
        )
        v600_fraction = v600_parse_fraction(
            getattr(self.config, "research_v600_short_lot_min_fraction", V600_SHORT_LOT_FRACTION_DEFAULT)
        )
        self._v600_fraction_invalid = v600_fraction is None
        self.research_v600_short_lot_min_fraction = (
            V600_SHORT_LOT_FRACTION_DEFAULT if v600_fraction is None else v600_fraction
        )
        v600_inherited = v600_inherited_mode(
            getattr(self.config, "research_v600_inherited_short_lots", V600_INHERITED_PARK)
        )
        self._v600_inherited_invalid = v600_inherited is None
        self.research_v600_inherited_short_lots = v600_inherited or V600_INHERITED_PARK
        self._v600_inherited_parked: dict[int, float] = {}
        self._v600_class: dict[int, str] = {}
        self._v600_fill_before: dict[int, float] = {}
        self._v600_exit_bps: list[float] = []
        self._v600_counts: dict[str, int] = {}
        self._v600_realized_quote = 0.0
        self._v600_state_reported = False
        self._v600_errors = 0
        # v6.0.1 C1: the dust recovery reserve is held only for dust the normalizer can work
        # (under half a lot, not a parked inherited lot).  Off restores v6.0.0: any dust holds it.
        self.research_v601_workable_dust_reserve = self._as_bool(
            getattr(self.config, "research_v601_workable_dust_reserve", True)
        )
        self._v601_counts: dict[str, int] = {}
        self._v601_last: dict[str, int] = {}
        self._v601_state_reported = False
        self._v601_errors = 0
        # v6.0.3 D1: once its bounded hold is gone, the partial-fill recovery releases a short lot
        # instead of cancelling the v6.0.0 lot exit at every request.  Off restores v6.0.2.
        self.research_v603_short_lot_release = self._as_bool(
            getattr(self.config, "research_v603_short_lot_release", True)
        )
        self._v603_counts: dict[str, int] = {}
        self._v603_last: dict[str, float | int] = {}
        self._v603_cancels_reported = 0
        self._v603_state_reported = False
        self._v603_errors = 0
        # v6.1: no order may realize a negative FIFO PnL by the validator's arithmetic.  A market
        # close is refused below its fee-inclusive break-even; the dust-compaction clip likewise;
        # the lot deques persist across a restart so the floor is the validator's, not a reseed's.
        # Off restores v6.0.3 exactly.
        self.research_v61_no_loss = self._as_bool(
            getattr(self.config, "research_v61_no_loss", True)
        )
        self._v61_counts: dict[str, int] = {}
        self._v61_last: dict[str, Any] = {}
        self._v61_state_reported = False
        self._v61_restored_lots: dict[int, dict[str, list]] = {}
        self._v61_errors = 0
        # v6.1 gap repair: a state the validator skipped took its fills with it.  A forward gap in
        # the state clock opens the A1.9.9 resync for one pass (every book planned against venue
        # truth), and a book diverged by a lot at two consecutive checks goes to the deferred
        # reseed.  Off restores v6.0.3 exactly; the step counters run either way.
        self.research_v61_state_gap_repair = self._as_bool(
            getattr(self.config, "research_v61_state_gap_repair", True)
        )
        self._v61_gap_counts: dict[str, int] = {}
        self._v61_last_state_ts: int | None = None
        self._v61_gap_pending: dict[str, int] | None = None
        self._v61_gap_window: dict[str, int] | None = None
        self._v61_diverged_prev: set[int] = set()
        self._v61_standing_new: list[int] = []
        self._v61_standing_before = 0
        self._v61_last_gap: dict[str, int] = {}
        self._v61_gap_state_reported = False
        self._v61_gap_errors = 0
        # v6.1 request memo (behaviour-neutral): the rolling-kappa refresh and the two realized-PnL
        # scans in build_book_profile run once per request instead of once per book.
        self.research_v61_request_memo = self._as_bool(
            getattr(self.config, "research_v61_request_memo", True)
        )
        self._v61_kappa_memo = None
        self._v61_pnl_memo = None
        self._v61_memo_hits = 0
        self._v61_memo_misses = 0
        self._v61_pnl_memo_builds = 0
        # v6.1.1 floor-aware reprice: the A1.9.1.1 seed judges a resting exit against the passive
        # touch floored at the lot's break-even -- the price the placement path itself sends -- so a
        # held lot's floored exit is no longer cancelled as STALE_BEHIND_TOUCH one request after it
        # is placed.  Off restores v6.1.0 exactly.
        self.research_v611_floor_reprice = self._as_bool(
            getattr(self.config, "research_v611_floor_reprice", True)
        )
        # v6.1.1 price lift: the venue truncates a price's binary expansion, so ~half of all limit
        # orders were placed one tick below the price decided.  Each outgoing limit price is lifted
        # one ulp when its double sits below its decimal, so it lands on its own tick.  Off
        # restores v6.1.0 exactly.
        self.research_v611_price_lift = self._as_bool(
            getattr(self.config, "research_v611_price_lift", True)
        )
        self._v611_counts: dict[str, int] = {}
        self._v611_state_reported = False
        self._v611_errors = 0
        # v6.2.0 breadth: every valid flat book gets a symmetric bid + ask at the touch, one lot each.
        # The ranker, the score-EV eligibility and the 8-slot admission no longer gate acquisition,
        # and the portfolio caps are the universe's (books × lot).  Off restores v6.1.1 exactly.
        self.research_v62_breadth = self._as_bool(
            getattr(self.config, "research_v62_breadth", True)
        )
        self._v62_counts: dict[str, int] = {}
        self._v62_request: dict[str, int] = {}
        self._v62_caps_applied = False
        self._v62_state_reported = False
        self._v62_errors = 0
        self._v62_mirror = None
        # v6.2.1: at breadth every book with inventory reaches the managed set.  The A1.6.1 fast-path
        # screen truncates its forced-inventory list to the candidate clamp (20, hard limit 24), so
        # once more than ~20 books held a lot the rest had no profile and therefore no exit (tick-500
        # read of v6.2.0 on UID 82: 118 books holding, 20 managed, exit liveness 14%, making 0).
        # Off restores v6.2.0 exactly.
        self.research_v621_managed_universe = self._as_bool(
            getattr(self.config, "research_v621_managed_universe", True)
        )
        self._v621_last: dict[str, int] = {}
        # v6.2.2: the startup seed at breadth.  (1) Its size bound is the universe's (set with the
        # caps from update(), before the first seed), so no venue position is left untracked (a
        # restart on UID 82 skipped 36 books over the 24 BASE bound).  (2) A position whose lots the
        # session remembers is OURS: the seed writes those lots (the validator's entry prices and
        # fees) instead of a synthetic lot at the quote, and does not park it (the same restart parked
        # 77 of our own held lots as inherited).  Off restores v6.2.1 exactly.
        self.research_v622_seed_at_breadth = self._as_bool(
            getattr(self.config, "research_v622_seed_at_breadth", True)
        )
        self._v622_counts: dict[str, int] = {}
        # v6.2.3: the v6.1 floor binds only on a book whose validator Kappa-3 carries the clean-record
        # premium (>= 3 non-zero periods in the window, none below tau 0).  A book with fewer periods
        # is not scored, and one with a loss in the window is at ~0.50 whatever its next close does,
        # so the floor protected nothing there and locked the book (v6.2.1: quoted 51 -> 10; v6.2.2:
        # 24 -> 15 in 500 ticks).  Maker exits only: the taker verdict stays strict.  Off restores
        # v6.2.2 exactly.
        self.research_v623_premium_floor = self._as_bool(
            getattr(self.config, "research_v623_premium_floor", True)
        )
        self._v623_counts: dict[str, int] = {}
        self._v623_memo = None
        self._v623_noted: dict[int, Any] = {}
        self._v623_errors = 0
        # v6.2.4: a release is the book's exit and gets the exit order life.  The frozen placement gives
        # the persistent exit TTL (research_profitable_exit_ttl_ms) and the V4.13.8 queue hold only to an
        # exit whose net clears research_profitable_exit_min_net_bps -- the no-loss floor restated.  v6.2.3
        # lifted that floor at five sites but not this sixth, so its releases (negative net by design)
        # went out with the base TTL and rested 35% of the holding time.  Off restores v6.2.3 exactly.
        self.research_v624_release_life = self._as_bool(
            getattr(self.config, "research_v624_release_life", True)
        )
        self._v624_counts: dict[str, int] = {}
        # v6.2.5, rule A: a book holding inventory also quotes its ADDING side, while |net| + clip
        # stays inside the band (2 clips: the held one plus one opposite).  Making counts
        # 2·min(buy, sell) per book, so a book that only exits scores its smaller side -- measured on
        # the validator's per-book gauges: balance 50% against 86% for the median mainnet maker, and
        # 121 of 128 books quoting one side at v6.2.4 tick 3,000.  Off restores v6.2.4 exactly.
        self.research_v625_two_sided = self._as_bool(
            getattr(self.config, "research_v625_two_sided", True)
        )
        # v6.2.5, rule B: each book's clip is the smallest whole number of minimum orders that keeps
        # the book on the validator's turnover-cap pace (10 × miner_wealth per 24 sim-h = 62,500
        # quote per book per 3 sim-h).  Measured: 1.1M per window against a field median of 8.0M,
        # which is that pace over 128 books.  The clip also sets the closing cadence kappa counts, so
        # the smallest clip that reaches the pace serves both legs.  Off restores one minimum order.
        self.research_v625_cap_pace = self._as_bool(
            getattr(self.config, "research_v625_cap_pace", True)
        )
        self._v625_counts: dict[str, int] = {}
        self._v625_pace: dict[int, Any] = {}
        self._v625_caps_lot = None
        self._v625_errors = 0
        # v6.2.6 rule A: kappa is the MEDIAN over scored books, and a book that has already realised a
        # loss in the window is at ~0.4996 whatever it does next.  So a release may take a CLEAN book's
        # first loss only when that book is stuck at the inventory band, and only while the books
        # carrying a loss stay under half of the scored ones (v6.2.5 tick 3,000: 16 clean of 128, kappa
        # 0.4996, while the closing cadence was already 158 per book per window).  Off restores v6.2.5.
        self.research_v626_loss_budget = self._as_bool(
            getattr(self.config, "research_v626_loss_budget", True)
        )
        # v6.2.6 rule B: making counts 2*min(buy capture, sell capture) per book, so the clip goes to
        # whichever side is behind, and a book whose smaller side has gone negative stops adding on the
        # leading side (v6.2.5: balance 37%, 30 books at -136.7 against +511.8).  Off restores v6.2.5.
        self.research_v626_capture_balance = self._as_bool(
            getattr(self.config, "research_v626_capture_balance", True)
        )
        # v6.2.6 rule C: a touch quote gets the exit's persistent life instead of 0.5 s -- entries fill
        # 3.6% against 11.1% for exits.  More fills per placement lets the pacing controller hold a
        # SMALLER clip at the same volume, and a smaller clip is more closes per book.  Off = v6.2.5.
        self.research_v626_quote_life = self._as_bool(
            getattr(self.config, "research_v626_quote_life", True)
        )
        self._v626_counts: dict[str, int] = {}
        self._v626_spent: tuple[Any, set[int]] | None = None
        self._v626_errors = 0

    def _init_direct_overlay_state(self) -> None:
        """Create the Direct overlay's per-run state: caches, ledgers, counters and timers.

        Split out of initialize() verbatim (v6.0.3 refactor) at the point where switch parsing
        ends and state construction begins.
        """
        # A1.7.4.1 correctness guard. This cache is intentionally owned by the
        # Direct overlay and is NOT session-scoped: simulator timestamp/session
        # rebases must not make a just-delivered TradeEvent process twice.
        self._direct_trade_deduper = DirectTradeEventDeduper(
            max_events=DIRECT_TRADE_DEDUP_MAX_EVENTS
        )
        self._direct_duplicate_trade_events_skipped = 0
        # Overlay-only learning.  It intentionally starts sparse and bounded;
        # restart-safe rolling PnL below supplies historical productivity context.
        self._direct_maker_open: dict[int, dict[str, float | int]] = {}
        self._direct_maker_quality_by_book: dict[int, MakerLifecycleStats] = {}
        self._direct_maker_quality_global = MakerLifecycleStats()
        self._direct_quality_migration_baseline: dict[int, int] = {}
        self._direct_quality_migration_global_baseline: int = 0
        self._direct_quality_last: dict[int, Any] = {}
        self._direct_realization_cost_last: dict[int, Any] = {}
        self._direct_lifecycle_fee_last: dict[int, Any] = {}
        self._direct_quote_geometry_last: dict[int, dict[str, float]] = {}
        self._direct_fastpath_last_selected_tick: dict[int, int] = {}
        self._direct_fastpath_profile_cache: dict[int, Any] = {}
        self._direct_request_wall_started: float | None = None
        self._direct_event_pnl_before: dict[int, float] = {}
        self._direct_fastpath_screen_calls = 0
        self._direct_freshness_budget_skips = 0
        self._direct_edge_fail_streak: dict[int, int] = {}
        self._direct_edge_cooldown_until: dict[int, int] = {}
        self._direct_fastpath_priority_by_book: dict[int, float] = {}
        # A1.7 execution-only telemetry.  These counters are not learned
        # authority and never influence entry economics.
        self._direct_quote_keeps = 0
        self._direct_quote_cancels = 0
        self._direct_quote_reprices = 0
        self._direct_quote_new_batches = 0
        self._direct_quote_unselected_keeps = 0
        # A1.7.2 execution authority snapshot.  This is ephemeral per tick and
        # is used only to enforce the Direct chooser's action at the final Maker
        # placement boundary; it is not learned state.
        self._direct_exit_authority_last: dict[int, dict[str, Any]] = {}
        self._direct_a1744_veto_active: dict[int, dict[str, Any]] = {}
        self._direct_a1744_veto_count = 0
        self._direct_a1744_catastrophic_bypass_count = 0
        self._direct_a1744_maker_not_strong_bypass_count = 0
        # A1.7.5 relative-arm and bounded-hold telemetry.  The A1.7.4.4 veto had
        # no time limit, which let 160 vetoes accumulate on 11 books and drove
        # inventory_age_p90 to 1,329 ticks.  The budget counts consecutive
        # vetoed ticks per book and releases the hold once it is spent.
        self._direct_a175_relative_veto_count = 0
        self._direct_a175_tail_budget_spent: dict[int, int] = {}
        self._direct_a175_tail_budget_exhausted: set[int] = set()
        self._direct_a175_tail_budget_releases = 0
        # A1.7.4.5 entry-quality telemetry only; no learned state or exit authority.
        self._direct_a1745_gate_active = 0
        self._direct_a1745_entry_blocks = 0
        self._direct_a1745_entry_allows = 0
        self._direct_a1745_regime_bypass = 0
        # A1.7.5 QUIET shadow ledger.  Strictly diagnostic: it records entries the
        # frozen 15 bps floor blocked so a later version can recalibrate from
        # measured outcomes instead of a retrospective filter.  It must never
        # influence effective_maker_min_edge_bps or any execution path.
        self._direct_a175_shadow_ledger: "OrderedDict[tuple[int, int], dict[str, Any]]" = OrderedDict()
        self._direct_a175_shadow_recorded = 0
        self._direct_a175_shadow_resolved = 0
        self._direct_a175_shadow_adverse = 0
        self._direct_wait_holds = 0
        self._direct_wait_cancel_batches = 0
        self._direct_negative_aggressive_blocks = 0
        # A1.7.4 tail-recovery state. This is bounded per-lifecycle telemetry and
        # decision context; it does not modify the frozen A1.7.1 MTM bands.
        self._direct_tail_history: dict[int, list[dict[str, float | int]]] = {}
        self._direct_tail_recovery_active: dict[int, dict[str, Any]] = {}
        self._direct_tail_recovery_maker_attempts = 0
        self._direct_tail_recovery_taker_reductions = 0
        self._direct_tail_counterfactual_events = 0
        # A1.7.3.1 liveness-only state.  This never changes alpha/risk economics.
        self._direct_partial_recovery: dict[int, dict[str, Any]] = {}
        self._direct_partial_hold_live = 0
        self._direct_partial_hold_releases = 0
        # v6.0.3: short lots handed back to the v6.0.0 lot exit, cancelling nothing.
        self._direct_v603_short_lot_releases = 0
        self._direct_v61_taker_refusals = 0
        self._direct_v61_compaction_refusals = 0
        self._direct_v61_state_gaps = 0
        self._direct_v61_state_repeats = 0
        self._direct_v61_gap_reseeds = 0
        self._direct_v61_standing_reseeds = 0
        self._direct_partial_wrong_side_cancels = 0
        self._direct_partial_bound_holds = 0
        self._direct_partial_bound_pending = 0
        self._direct_partial_bound_expired = 0
        self._direct_partial_replacement_blocks = 0
        self._direct_current_state_timestamp_ns = 0
        self._direct_dust_normalize_orders = 0
        self._direct_dust_normalize_fills = 0
        # A1.7.4.2 Kappa-safe moderate-dust sign-cross telemetry.
        self._direct_dust_kappa_allows = 0
        self._direct_dust_kappa_blocks = 0
        # A1.7.4.3 local bridge for submitted placements that are not yet
        # visible in account.orders.  The ledger is process-local and strictly
        # mechanical; it does not alter trading economics.
        self._direct_pending_exposure_orders: dict[tuple[int, str, str], PendingExposureOrder] = {}
        self._direct_pending_exposure_recorded = 0
        self._direct_pending_exposure_acked = 0
        self._direct_pending_exposure_expired = 0
        self._direct_pending_exposure_fill_reductions = 0
        self._direct_strict_exposure_blocks = 0
        # A1.7.4.3.1 same-book/side ownership telemetry. The state itself is
        # represented by acknowledged account.orders + the A1.7.4.3 pending
        # ledger + a per-response ownership set in final validation.
        self._direct_book_ownership_reserves = 0
        self._direct_book_ownership_blocks = 0
        self._direct_book_ownership_releases = 0
        # A1.7.4.3.2 exact exchange-order identity registry. This registry is
        # mechanical and bounded; missing/unknown identities fail safe by keeping
        # ownership until authoritative acknowledgement/fill/local expiry.
        self._direct_exchange_order_ownership: dict[int, ExchangeOrderIdentity] = {}
        self._direct_identity_releases = 0
        self._direct_stale_cancels_ignored = 0
        self._direct_release_mismatch_blocks = 0
        self._direct_liveness_blocked_ticks = 0
        self._direct_liveness_triggers = 0
        self._direct_forced_recovery_books_this_tick: set[int] = set()
        try:
            self._emit(
                "SIMPLE_CONFIG",
                force=True,
                simple_policy_version=SIMPLE_POLICY_VERSION,
                authority="OBSERVABLE_FASTPATH>HARD_SAFETY>CURRENT_MAKER_EDGE>MAKER_OR_SKIP",
                direct_economics_version=DIRECT_ECONOMICS_VERSION,
                execution_controller_version=DIRECT_EXECUTION_CONTROLLER_VERSION,
                exit_authority="DIRECT_OBSERVABLE_MAKER_WAIT_RISK_TAKER",
                separate_maintenance_authority=0,
                separate_alpha_authority=0,
                lane_execution_caps=0,
                latency_hard_gate=0,
                duplicate_adverse_hard_gate=0,
                taker_kappa_subsidy=0,
                direct_quality_version=DIRECT_QUALITY_VERSION,
                direct_execution_quality_version=DIRECT_EXECUTION_QUALITY_VERSION,
                direct_dust_kappa_version=DIRECT_DUST_KAPPA_VERSION,
                direct_dust_kappa_maker_floor_bps=DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS,
                maker_min_ev=DIRECT_MAKER_MIN_EV,
                maker_min_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS,
                maker_quality_max_penalty=0.0,
                migrated_quality_initial_weight=MIGRATED_QUALITY_INITIAL_WEIGHT,
                migrated_quality_full_weight_samples=MIGRATED_QUALITY_FULL_WEIGHT_SAMPLES,
                migrated_quality_global_full_weight_samples=MIGRATED_QUALITY_GLOBAL_FULL_WEIGHT_SAMPLES,
                maker_max_touch_improvement_bps=DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS,
                maker_max_ttl_ms=DIRECT_QUOTE_MAX_TTL_MS,
                legacy_dust_exempt_cap=DIRECT_DUST_EXEMPT_CAP,
                direct_dust_open_slot_exempt_all=1,
                cold_start_taker_rate=COLD_START_TAKER_RATE,
                taker_entry_min_ev=DIRECT_TAKER_MIN_EV,
                taker_entry_min_edge_bps=DIRECT_TAKER_MIN_EDGE_BPS,
                learned_taker_shortfall_cost=0,
                net_realized_shortfall_cost=0,
                signed_maker_lifecycle_fees=0,
                expected_exit_fee_model=0,
                kappa_lpm3_downside_cost=0,
                taker_frequency_is_badness=0,
                taker_entry_enabled=int(DIRECT_TAKER_ENTRY_ENABLED),
                direct_fastpath_version=DIRECT_FASTPATH_VERSION,
                direct_fastpath_candidate_count=DIRECT_FASTPATH_CANDIDATE_COUNT,
                direct_fastpath_deep_count=DIRECT_FASTPATH_DEEP_COUNT,
                observable_exit_version=DIRECT_OBSERVABLE_EXIT_VERSION,
                maker_exit_target_bps=DIRECT_MAKER_EXIT_TARGET_BPS,
                position_risk_source="MID_MTM_EXCLUDES_CROSSING_COST",
                hard_escape_min_age_ticks=float(getattr(self, "research_bounded_loss_escape_min_age_ticks", 2.0)),
                positive_maker_veto_enabled=int(bool(getattr(self, "research_positive_maker_veto_enabled", True))),
                positive_maker_veto_floor_bps=float(getattr(self, "research_positive_maker_veto_floor_bps", 1.0)),
                positive_maker_veto_max_failed_exits=int(getattr(self, "research_positive_maker_veto_max_failed_exits", 4)),
                absolute_positive_maker_veto_enabled=1,
                absolute_positive_maker_veto_floor_bps=float(getattr(self, "research_positive_maker_veto_floor_bps", 1.0)),
                absolute_positive_maker_veto_max_failed_exits=1,
                direct_positive_maker_kappa_version=DIRECT_POSITIVE_MAKER_KAPPA_VERSION,
                a1744_strong_maker_floor_bps=float(DIRECT_A1744_STRONG_MAKER_FLOOR_BPS),
                a1744_failed_exit_escalation_can_override_strong_maker=0,
                a1744_catastrophic_bypass=1,
                direct_quiet_entry_version=DIRECT_QUIET_ENTRY_VERSION,
                a1745_quiet_zero_rebate_min_edge_bps=float(DIRECT_A1745_QUIET_ZERO_REBATE_MIN_EDGE_BPS),
                a1745_zero_rebate_floor_bps=float(DIRECT_A1745_ZERO_REBATE_FLOOR_BPS),
                a1745_low_trade_rate_max=float(DIRECT_A1745_LOW_TRADE_RATE_MAX),
                a1745_wide_spread_min_bps=float(DIRECT_A1745_WIDE_SPREAD_MIN_BPS),
                a1745_global_maker_edge_retune=0,
                direct_exit_refresh_version=DIRECT_EXIT_REFRESH_VERSION,
                a19_phase=self._a19_runtime_phase(),
                a19_profitable_exit_ttl_ms=float(
                    getattr(self, "research_profitable_exit_ttl_ms", DIRECT_A19_BASELINE_PROFITABLE_EXIT_TTL_MS)
                    or DIRECT_A19_BASELINE_PROFITABLE_EXIT_TTL_MS
                ),
                a19_target_profitable_exit_ttl_ms=float(DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS),
                a19_behaviour_change=self._a19_behaviour_change(),
                a19_size_change=0,
                a19_active_book_change=0,
                a19_taker_logic_change=0,
                a19_entry_gate_change=0,
                true_wait_execution=1,
                wait_falls_through_to_legacy_maker=0,
                negative_aggressive_maker_block=1,
                direct_trade_dedup_version=DIRECT_TRADE_DEDUP_VERSION,
                direct_trade_dedup_max_events=DIRECT_TRADE_DEDUP_MAX_EVENTS,
                duplicate_trade_event_accounting_guard=1,
                direct_tail_recovery_version=DIRECT_TAIL_RECOVERY_VERSION,
                recovery_trigger_bps=DIRECT_RECOVERY_TRIGGER_BPS,
                recovery_force_bps=DIRECT_RECOVERY_FORCE_BPS,
                recovery_min_age_ticks=DIRECT_RECOVERY_MIN_AGE_TICKS,
                recovery_worsening_bps_per_tick=DIRECT_RECOVERY_WORSENING_BPS_PER_TICK,
                recovery_maker_floor_bps=DIRECT_RECOVERY_MAKER_FLOOR_BPS,
                hard_recovery_maker_floor_bps=DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS,
                absolute_recovery_maker_floor_bps=DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS,
                recovery_maker_advantage_bps=DIRECT_RECOVERY_MAKER_ADVANTAGE_BPS,
                recovery_maker_max_failed_exits=DIRECT_RECOVERY_MAKER_MAX_FAILED_EXITS,
                recovery_taker_floor_bps=DIRECT_RECOVERY_TAKER_FLOOR_BPS,
                recovery_taker_min_failed_exits=DIRECT_RECOVERY_TAKER_MIN_FAILED_EXITS,
                observed_expected_hard_taker_loss_bps=DIRECT_EXPECTED_HARD_TAKER_LOSS_BPS,
                wait_resting_exit_floor_bps=DIRECT_MAKER_EXIT_TARGET_BPS,
                direct_max_pre_submit_age_ms=DIRECT_MAX_PRE_SUBMIT_AGE_MS,
                direct_exposure_liveness_version="direct_liveness_v4_16_2_a1_6_3",
                direct_quote_manager_version=DIRECT_QUOTE_MANAGER_VERSION,
                persistent_maker_execution=1,
                learned_quote_authority=0,
                persistent_maker_max_ttl_ms=DIRECT_QUOTE_MAX_TTL_MS,
                directional_exposure_validation=1,
                inflight_exposure_reservation=1,
                direct_inflight_reservation_version=DIRECT_INFLIGHT_RESERVATION_VERSION,
                local_pending_exposure_reservation=1,
                strict_max_total_abs_base=1,
                liveness_overflow_abs=0.0,
                direct_book_ownership_version=DIRECT_BOOK_OWNERSHIP_VERSION,
                same_book_pending_ownership=1,
                same_request_same_side_ownership=1,
                pending_duplicate_key_aggregates_quantity=1,
                identity_safe_ownership_release=1,
                stale_cancel_book_only_release=0,
                exact_exchange_order_release=1,
                one_live_order_batch_per_book=1,
                isolated_dust_compaction=1,
                direct_liveness_version=DIRECT_LIVENESS_VERSION,
                direct_partial_remainder_hold=1,
                direct_partial_bound_order_guard=1,
                direct_partial_replacement_block=1,
                direct_partial_remainder_hard_ttl_ms=float(DIRECT_PARTIAL_HOLD_MAX_NS) / 1_000_000.0,
                direct_partial_remainder_publish_mult=int(DIRECT_PARTIAL_HOLD_PUBLISH_MULT),
                direct_dust_recovery_reserve_clips=1,
                direct_irreducible_dust_normalization=1,
                direct_exposure_version=DIRECT_EXPOSURE_VERSION,
                dust_fastpath_forced=0,
                direct_dust_compaction=1,
                placement_only_final_validation=1,
            )
        except Exception:
            pass
        try:
            effective_ttl = float(
                getattr(self, "research_profitable_exit_ttl_ms", DIRECT_A19_BASELINE_PROFITABLE_EXIT_TTL_MS)
                or DIRECT_A19_BASELINE_PROFITABLE_EXIT_TTL_MS
            )
            publish_ms = float(getattr(self, "_a19_publish_interval_ms", 0.0) or 0.0)
            self._emit(
                "A19_EXIT_REFRESH_CONFIG", force=True,
                tick=int(getattr(self, "_tick", 0) or 0),
                exit_refresh_version=DIRECT_EXIT_REFRESH_VERSION,
                exit_ledger_version=DIRECT_EXIT_LEDGER_VERSION,
                phase=self._a19_runtime_phase(),
                effective_profitable_exit_ttl_ms=effective_ttl,
                baseline_profitable_exit_ttl_ms=float(DIRECT_A19_BASELINE_PROFITABLE_EXIT_TTL_MS),
                target_profitable_exit_ttl_ms=float(DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS),
                reprice_ticks=float(getattr(self, "research_profitable_exit_reprice_ticks", 3.0) or 3.0),
                observed_publish_interval_ms=publish_ms,
                behaviour_change=self._a19_behaviour_change(),
                phase_b_events=",".join(DIRECT_A19_PHASE_B_EVENTS),
                maker_only=1, taker_logic_change=0,
                entry_gate_change=0, size_change=0, active_book_change=0,
            )
        except Exception:
            pass

    def _console_allowed(self, record: dict[str, Any]) -> bool:
        if str(record.get("type", "")) == "DUPLICATE_TRADE_EVENT_SKIPPED":
            return True
        return super()._console_allowed(record)

    def _format_human(self, record: dict[str, Any]) -> str | None:
        if str(record.get("type", "")) == "DUPLICATE_TRADE_EVENT_SKIPPED":
            return (
                f"[S1R_DUP_TRADE_SKIP] tick={record.get('tick')} "
                f"book={record.get('book')} trade_id={record.get('trade_id')} "
                f"maker_order={record.get('maker_order_id')} "
                f"taker_order={record.get('taker_order_id')} "
                f"qty={record.get('quantity')} px={record.get('price')} "
                f"total={record.get('skipped_total')}"
            )
        return super()._format_human(record)

    def update(self, state) -> None:
        # A1.9.6.1: the framework ingests this state's trades inside update(),
        # before respond().  The venue's BASE precision must be known first, or
        # the fee residue of the first fills after a restart would be skipped.
        try:
            self._a1961_note_base_decimals(state)
        except Exception:
            pass
        # v6.2.2: the universe's caps, including the seed bound, before the first seed (respond()).
        if self._v622_on():
            try:
                self._v62_apply_caps(state)
            except Exception:
                self._v62_errors = int(getattr(self, "_v62_errors", 0) or 0) + 1
        # A1.9.9: a clock rewind is detected before this state's trades are
        # ingested, so every order registry is cleared before a replayed or
        # resurrected event can be matched against it.
        # v6.1: classify this state's clock step first; a gap arms a one-pass venue-truth repair.
        try:
            self._v61_observe_state_step(state)
        except Exception:
            self._v61_gap_errors = int(getattr(self, "_v61_gap_errors", 0) or 0) + 1
        try:
            self._a199_observe_epoch(state)
        except Exception:
            pass
        # v6.2 telemetry: the validator's making term, from this state's prints.
        try:
            self._v62_feed_mirror(state)
        except Exception:
            self._v62_errors = int(getattr(self, "_v62_errors", 0) or 0) + 1
        return super().update(state)

    # ------------------------------------------------------------------
    # A1.5.1 Maker lifecycle learning.  Learn NET realized downside, including
    # partial reductions and fees, rather than gross entry-to-final-price drift.
    # ------------------------------------------------------------------
    def onTrade(self, event, validator: str | None = None) -> None:
        book_id = getattr(event, "bookId", None)
        own = (
            getattr(event, "takerAgentId", None) == getattr(self, "uid", None)
            or getattr(event, "makerAgentId", None) == getattr(self, "uid", None)
        )
        if own:
            deduper = getattr(self, "_direct_trade_deduper", None)
            if not isinstance(deduper, DirectTradeEventDeduper):
                # Lazy fallback protects hot-reload/test objects without changing
                # the normal initialize() lifecycle.
                deduper = DirectTradeEventDeduper(max_events=DIRECT_TRADE_DEDUP_MAX_EVENTS)
                self._direct_trade_deduper = deduper
            duplicate, identity = deduper.check_and_note(event)
            if duplicate:
                self._direct_duplicate_trade_events_skipped = int(
                    getattr(self, "_direct_duplicate_trade_events_skipped", 0) or 0
                ) + 1
                try:
                    self._emit(
                        "DUPLICATE_TRADE_EVENT_SKIPPED",
                        force=True,
                        tick=int(getattr(self, "_tick", 0) or 0),
                        timestamp=getattr(event, "timestamp", None),
                        book=book_id,
                        trade_id=getattr(event, "tradeId", None),
                        client_order_id=getattr(event, "clientOrderId", None),
                        maker_agent_id=getattr(event, "makerAgentId", None),
                        maker_order_id=getattr(event, "makerOrderId", None),
                        taker_agent_id=getattr(event, "takerAgentId", None),
                        taker_order_id=getattr(event, "takerOrderId", None),
                        side=getattr(event, "side", None),
                        quantity=getattr(event, "quantity", None),
                        price=getattr(event, "price", None),
                        identity_hash=deduper.identity_hash(identity),
                        dedup_version=DIRECT_TRADE_DEDUP_VERSION,
                        cache_size=len(deduper),
                        skipped_total=int(self._direct_duplicate_trade_events_skipped),
                    )
                except Exception:
                    pass
                return

        if own:
            # A1.7.4.3: consume the local bridge reservation only after the
            # replay guard accepts this as a new TradeEvent. Partial fills reduce
            # the reservation by the exact observed quantity.
            try:
                self._direct_pending_note_fill(event)
            except Exception:
                pass
            # A1.9.0.1: retire the ledger row this fill consumed.  A partial
            # fill only decrements, so the remainder stays visible as resting.
            try:
                ledger = self._a19_ledger_ref()
                qty = getattr(event, "quantity", None)
                for attr, agent_attr in (
                    ("makerOrderId", "makerAgentId"), ("takerOrderId", "takerAgentId"),
                ):
                    if getattr(event, agent_attr, None) == getattr(self, "uid", None):
                        ledger.note_removed(
                            getattr(event, attr, None),
                            cause=LEDGER_REMOVED_FILLED, filled_qty=qty,
                        )
            except Exception:
                pass
            # A1.9.6.1: the venue takes a buy's positive fee in BASE, rounded up
            # to a whole unit, and the tracker never sees it.  Mirror it into the
            # fee-residue ledger -- once per trade, after the replay guard.
            try:
                self._a1961_note_fee_residue(event)
            except Exception:
                pass

        if book_id is not None:
            try:
                self._direct_event_pnl_before[int(book_id)] = float(
                    self._pnl_tick_buffer.get(int(book_id), 0.0)
                )
            except Exception:
                pass
        try:
            super().onTrade(event, validator)
        finally:
            if book_id is not None:
                self._direct_event_pnl_before.pop(int(book_id), None)
        # v5.0.2 F1: a flat book's tracker is exactly zero; what it still held joins the residue ledger.
        if own and book_id is not None:
            try:
                self._v502_settle_flat_residue(int(book_id))
            except Exception:
                self._v502_errors = int(getattr(self, "_v502_errors", 0) or 0) + 1
            # v6.0.0: a short-lot exit's overshoot of two base units or less is residue, not dust.
            try:
                self._v600_settle_leftover(int(book_id))
            except Exception:
                self._v600_errors = int(getattr(self, "_v600_errors", 0) or 0) + 1
        # v5.0.0: who took the other side of our own trade, for concentration telemetry.
        if own:
            analytics = getattr(self, "_v500_analytics", None)
            if analytics is not None:
                try:
                    analytics.note_trade(
                        uid=getattr(self, "uid", None), maker_agent=getattr(event, "makerAgentId", None),
                        taker_agent=getattr(event, "takerAgentId", None), ts=getattr(event, "timestamp", None),
                    )
                except Exception:
                    pass


    def _log_notices(self, state, tick: int) -> None:
        # A1.9.2: the only correct clock for a notice.  `self._tick` has not yet
        # been advanced for this state when notices are ingested, so anything
        # measured against it is one tick early.
        self._a19_notice_tick = int(tick)
        super()._log_notices(state, tick)
        try:
            notices = (getattr(state, "notices", None) or {}).get(self.uid, []) or []
        except Exception:
            notices = []
        for notice in notices:
            phase = type(notice).__name__.upper()
            try:
                if "PLACEMENTEVENT" in phase:
                    self._direct_note_placement_identity_notice(notice, phase=phase)
                    # v5.0.2 F4: the venue has processed this market order; decided after update().
                    self._v502_note_market_notice(notice, phase=phase)
                    continue
                if "ORDERCANCELLATIONSEVENT" in phase:
                    self._direct_note_cancellation_identity_notice(notice, phase=phase)
                    continue
                # Other terminal notices may release only by an exact client id.
                # Never fall back to book-only matching.
                if any(token in phase for token in ("EXPIRE", "REJECT", "FAIL")):
                    cid = self._direct_notice_client_id(notice)
                    raw_book = getattr(notice, "bookId", getattr(notice, "book_id", None))
                    if cid is None or raw_book is None:
                        continue
                    try:
                        bid = int(raw_book)
                    except (TypeError, ValueError):
                        continue
                    side = canonical_order_side(getattr(notice, "side", ""))
                    self._direct_release_pending_exact(
                        book_id=bid, client_order_id=cid, side=side or None,
                        reason=f"NOTICE_{phase}", exchange_order_id=getattr(notice, "orderId", None),
                    )
            except Exception:
                # Correctness guard must never make the miner fail the request.
                continue

    def _research_on_own_fill(
        self, *, event, book_id: int, before: float, after: float,
        kappa_before: int, kappa_after: int, is_maker: bool,
    ) -> None:
        super()._research_on_own_fill(
            event=event, book_id=book_id, before=before, after=after,
            kappa_before=kappa_before, kappa_after=kappa_after, is_maker=is_maker,
        )
        self._direct_note_partial_fill_recovery(
            book_id=int(book_id), before=float(before), after=float(after), is_maker=bool(is_maker), event=event,
        )
        # A1.9.7: open the exit-gap record on an opening fill, and watch a
        # post-fill protective exit for a sign flip.
        try:
            self._a197_note_own_fill(book_id=int(book_id), before=float(before), after=float(after))
        except Exception:
            pass
        # A1.9.9: a position that went flat or flipped ends its ABSOLUTE exit state.
        try:
            self._a199_note_own_fill(book_id=int(book_id), before=float(before), after=float(after))
        except Exception:
            pass
        # v6.0.0: a short-lot exit is counted, and its leftover is settled once the trade is booked.
        try:
            self._v600_note_own_fill(event, book_id=int(book_id), before=float(before), after=float(after))
        except Exception:
            self._v600_errors = int(getattr(self, "_v600_errors", 0) or 0) + 1
        try:
            bid = int(book_id)
            eps = float(self._execution_flat_epsilon())
            px = float(getattr(event, "price", 0.0) or 0.0)
            if px <= 0.0:
                return
            was_flat = abs(float(before)) <= eps
            is_flat = abs(float(after)) <= eps
            crossed = float(before) * float(after) < -(eps * eps)
            row = self._direct_maker_open.get(bid)

            pnl_before = float((getattr(self, "_direct_event_pnl_before", {}) or {}).get(bid, 0.0) or 0.0)
            pnl_after = float((getattr(self, "_pnl_tick_buffer", {}) or {}).get(bid, 0.0) or 0.0)
            realized_delta = pnl_after - pnl_before

            if row is not None:
                # Accumulate every realized reduction belonging to this Maker-opened lifecycle.
                closed_qty = 0.0
                if crossed or is_flat:
                    closed_qty = abs(float(before))
                elif abs(float(after)) + eps < abs(float(before)):
                    closed_qty = max(0.0, abs(float(before)) - abs(float(after)))
                if closed_qty > eps or abs(realized_delta) > 1e-12:
                    row["realized_pnl"] = float(row.get("realized_pnl", 0.0) or 0.0) + float(realized_delta)
                    entry_px = float(row.get("entry_price", 0.0) or 0.0)
                    if entry_px > 0.0 and closed_qty > eps:
                        row["realized_notional"] = float(row.get("realized_notional", 0.0) or 0.0) + closed_qty * entry_px
                    if not bool(is_maker):
                        row["used_taker_exit"] = 1

            if row is not None and (is_flat or crossed):
                entry_px = float(row.get("entry_price", 0.0) or 0.0)
                sign = 1.0 if float(row.get("sign", 1.0) or 1.0) >= 0.0 else -1.0
                gross_bps = sign * (px - entry_px) / entry_px * 10_000.0 if entry_px > 0.0 else 0.0
                realized_notional = max(0.0, float(row.get("realized_notional", 0.0) or 0.0))
                realized_pnl = float(row.get("realized_pnl", 0.0) or 0.0)
                net_bps = (realized_pnl / realized_notional * 10_000.0) if realized_notional > 1e-12 else gross_bps
                exit_is_taker = bool(row.get("used_taker_exit", 0)) or (not bool(is_maker))
                stats = self._direct_maker_quality_by_book.setdefault(bid, MakerLifecycleStats())
                stats.observe(net_bps=net_bps, gross_bps=gross_bps, exit_is_taker=exit_is_taker)
                global_stats = getattr(self, "_direct_maker_quality_global", None)
                if not isinstance(global_stats, MakerLifecycleStats):
                    global_stats = MakerLifecycleStats()
                    self._direct_maker_quality_global = global_stats
                global_stats.observe(net_bps=net_bps, gross_bps=gross_bps, exit_is_taker=exit_is_taker)
                # A1.9.2: a clean Maker-ending round trip retires quarantine, so
                # a recovered book is re-tested instead of starved forever.
                try:
                    self._a192_note_round_trip(
                        bid, net_bps=float(net_bps), exit_is_taker=bool(exit_is_taker),
                    )
                except Exception:
                    pass
                recovery_row = (getattr(self, "_direct_tail_recovery_active", {}) or {}).pop(bid, None)
                if isinstance(recovery_row, dict):
                    try:
                        self._emit(
                            "A174_RECOVERY_OUTCOME", force=True,
                            tick=int(getattr(self, "_tick", 0) or 0), book=bid,
                            recovery_version=DIRECT_TAIL_RECOVERY_VERSION,
                            first_recovery_tick=int(recovery_row.get("first_tick", -1) or -1),
                            last_recovery_tick=int(recovery_row.get("last_tick", -1) or -1),
                            last_recovery_reason=str(recovery_row.get("last_reason", "") or ""),
                            recovery_actions=int(recovery_row.get("actions", 0) or 0),
                            best_recovery_maker_net_bps=float(recovery_row.get("best_maker_net_bps", 0.0) or 0.0),
                            best_recovery_maker_tick=int(recovery_row.get("best_maker_tick", -1) or -1),
                            net_realized_bps=float(net_bps), realized_pnl=float(realized_pnl),
                            exit_style=("TAKER" if exit_is_taker else "MAKER"),
                            positive=int(float(net_bps) > 0.0),
                        )
                    except Exception:
                        pass
                tail_history = getattr(self, "_direct_tail_history", None)
                if isinstance(tail_history, dict):
                    tail_history.pop(bid, None)
                try:
                    self._emit(
                        "DIRECT_MAKER_LIFECYCLE", force=True,
                        tick=getattr(self, "_tick", None), book=bid,
                        gross_bps=float(gross_bps), net_realized_bps=float(net_bps),
                        realized_pnl=float(realized_pnl), realized_notional=float(realized_notional),
                        exit_style=("TAKER" if exit_is_taker else "MAKER"),
                        lifecycle_samples=int(stats.count), maker_exit_count=int(stats.maker_exit_count),
                        taker_exit_count=int(stats.taker_exit_count), taker_exit_rate=float(stats.taker_exit_rate),
                        taker_loss_rate=float(stats.taker_loss_rate), net_bps_ewma=float(stats.net_bps_ewma),
                        taker_net_bps_ewma=float(stats.taker_net_bps_ewma),
                        taker_net_shortfall_bps_ewma=float(stats.taker_net_shortfall_bps_ewma),
                        taker_downside_lpm3_bps=float(stats.taker_downside_lpm3_bps),
                    )
                except Exception:
                    pass
                # A1.9.6.1: judge a taker exit against its own decision, and say
                # separately when the loss was already past the floor at the decision.
                if exit_is_taker:
                    try:
                        self._a1961_emit_taker_outcome(book_id=bid, net_bps=float(net_bps))
                    except Exception:
                        pass
                self._direct_maker_open.pop(bid, None)
                row = None

            # Only Maker fills may open a new Direct lifecycle.
            if is_maker and (was_flat or crossed) and not is_flat:
                self._direct_maker_open[bid] = {
                    "entry_price": float(px),
                    "sign": (1.0 if float(after) > 0.0 else -1.0),
                    "tick": int(getattr(self, "_tick", 0) or 0),
                    "realized_pnl": 0.0,
                    "realized_notional": 0.0,
                    "used_taker_exit": 0,
                }
            if is_flat or crossed:
                tail_history = getattr(self, "_direct_tail_history", None)
                if isinstance(tail_history, dict):
                    tail_history.pop(bid, None)
                recovery_table = getattr(self, "_direct_tail_recovery_active", None)
                if isinstance(recovery_table, dict):
                    recovery_table.pop(bid, None)
        except Exception:
            return

    def _direct_quality_authority_scale(self, book_id: int) -> float:
        """Downweight legacy A1.5/A1.4 quality until A1.5.1 earns fresh evidence.

        Legacy global state affected even books with no book-specific samples, so
        migration authority must be tempered globally as well as per book.
        """
        bid = int(book_id)
        initial = float(MIGRATED_QUALITY_INITIAL_WEIGHT)
        global_baseline = max(0, int(getattr(self, "_direct_quality_migration_global_baseline", 0) or 0))
        global_stats = getattr(self, "_direct_maker_quality_global", None)
        global_current = int(getattr(global_stats, "count", 0) or 0) if global_stats is not None else 0
        if global_baseline > 0:
            global_fresh = max(0, global_current - global_baseline)
            global_full = max(1, int(MIGRATED_QUALITY_GLOBAL_FULL_WEIGHT_SAMPLES))
            global_progress = min(1.0, global_fresh / float(global_full))
            global_scale = initial + (1.0 - initial) * global_progress
        else:
            global_scale = 1.0

        baseline = int((getattr(self, "_direct_quality_migration_baseline", {}) or {}).get(bid, 0) or 0)
        if baseline <= 0:
            return float(global_scale)
        stats = (getattr(self, "_direct_maker_quality_by_book", {}) or {}).get(bid)
        current = int(getattr(stats, "count", 0) or 0) if stats is not None else 0
        fresh = max(0, current - baseline)
        full = max(1, int(MIGRATED_QUALITY_FULL_WEIGHT_SAMPLES))
        progress = min(1.0, fresh / float(full))
        book_scale = initial + (1.0 - initial) * progress
        return float(max(global_scale, book_scale))

    def _direct_quality_for_book(self, book_id: int):
        stats = (getattr(self, "_direct_maker_quality_by_book", {}) or {}).get(int(book_id))
        rolling_n = 0
        rolling_loss = 0.0
        rolling_mean = 0.0
        try:
            roll = self._research_rolling_book_economics(int(book_id))
            rolling_n = int(getattr(roll, "nonzero_count", 0) or 0)
            rolling_loss = float(getattr(roll, "loss_rate", 0.0) or 0.0)
            rolling_mean = float(getattr(roll, "realized_mean", 0.0) or 0.0)
        except Exception:
            pass
        authority_scale = self._direct_quality_authority_scale(int(book_id))
        quality = maker_quality_adjustment(
            stats=stats,
            global_stats=getattr(self, "_direct_maker_quality_global", None),
            rolling_samples=rolling_n,
            rolling_loss_rate=rolling_loss,
            rolling_realized_mean=rolling_mean,
            authority_scale=authority_scale,
        )
        self._direct_quality_last[int(book_id)] = quality
        return quality

    # ------------------------------------------------------------------
    # A1.5 LifecycleEV: A1.1 latency/adverse correction stays intact.  Maker
    # quality is a bounded rank deduction, not a new hard lifecycle veto.
        # ------------------------------------------------------------------
    def _research_lifecycle_entry_cost_bps(self, book_id: int, spread_bps: float) -> float:
        """A1.6 compatibility hook: current signed Maker entry fee only.

        Future exit fees, learned Taker probabilities, learned shortfall, holding
        forecasts and migrated quality state are deliberately not entry authority.
        """
        del spread_bps
        maker_fee = float(self._research_live_fee_bps(int(book_id), is_maker=True))
        self._research_lifecycle_cost_last.pop(int(book_id), None)
        self._direct_realization_cost_last.pop(int(book_id), None)
        self._direct_lifecycle_fee_last.pop(int(book_id), None)
        return maker_fee

    def _research_score_ev_for_book(self, book_id: int, expected_alpha: float, mem):
        """A1.6 observable rank: current half-spread - signed Maker fee + Kappa need.

        This method intentionally does not consult learned fill probability,
        markout posterior, rolling realized PnL, Maker quality, future Taker
        probability, realization-time models, or strategy latency.
        """
        del mem
        bid = int(book_id)
        profile = self._research_profile_for_book(bid)
        spread_bps = max(0.0, float(getattr(profile, "spread_bps", 0.0) or 0.0))
        capture_bps = 0.5 * spread_bps
        maker_fee = float(self._research_live_fee_bps(bid, is_maker=True))
        taker_fee = float(self._research_live_fee_bps(bid, is_maker=False))
        current_edge_bps = capture_bps - maker_fee
        edge_signal = math.tanh(current_edge_bps / 8.0)

        obs = int(self._completion_observation_count(bid))
        required = int(self._research_required_observation_count())
        remaining = max(0, required - obs)
        if remaining == 1:
            completion = 0.20
        elif remaining == 2:
            completion = 0.10
        elif remaining > 2:
            completion = 0.05
        else:
            completion = 0.0

        qty = abs(float(self._research_abs_inventory(bid)))
        eps = float(self._execution_flat_epsilon())
        inventory_blocked = qty > eps
        toxic = bid in getattr(self, "_research_parked_dust", {})
        unsafe = str(getattr(self, "_research_market_regime", "") or "").upper() == "TOXIC"
        headroom = float(self._research_volume_cap_headroom(
            getattr(self, "_research_volume_cap_state", None), bid
        ))
        volume_capped = headroom <= 0.0

        reject = None
        if toxic:
            reject = "TOXIC"
        elif inventory_blocked:
            reject = "INVENTORY_BLOCKED"
        elif unsafe:
            reject = "UNSAFE"
        elif volume_capped:
            reject = "VOLUME_CAP"
        elif current_edge_bps < 0.0:
            reject = "NEGATIVE_CURRENT_EDGE"
        # A1.9.3 hooked `_a193_breadth_override` here, on NEGATIVE_CURRENT_EDGE.
        # RETIRED in A1.9.5 step 4 -- measured, not assumed.  Across two runs
        # (9,738 and 4,233 ticks) it produced zero admits and zero denies,
        # because one-away books do not reach this reject: all 1,320 one-away
        # RANK rows were already `eligible=True` with `reject_reason=None`.
        # Their edge is positive; what refuses them is the A1.7.4.5 floor one
        # stage later, where `_a195_breadth_relief` now sits.  The override and
        # its budget helpers are kept and still own the budget arithmetic that
        # step 4 draws on -- only this dead call site is gone.
        a193 = None
        # v5.0.1: a Kappa-eligible book the validator has not activated is scored 0.0 inside the
        # median, and one round trip activates it -- the distance a one-away book is from counting.
        activation_value, score_state = self._v501_activation_value(bid, remaining)
        eligible = reject is None
        total_score = completion + activation_value
        final_score = edge_signal + total_score if eligible else float("-inf")
        lane = "NORMAL" if remaining <= 0 else ("COVERAGE" if obs <= 0 else "COMPLETION")
        breakdown = ScoreEVBreakdown if score_state is None else ActivationScoreEV
        extra = {} if score_state is None else {
            "activation_value": activation_value, "score_state": score_state,
        }

        return breakdown(
            book=bid, side="MM", alpha=float(expected_alpha or 0.0),
            fill_prob_old=0.50, fill_prob_hazard=None, actionable_fill_prob=0.50,
            dust_prob=0.0, spread_capture_bps=capture_bps, expected_markout_bps=0.0,
            fees_bps=maker_fee, trading_ev=edge_signal, observation_count=obs,
            required_observation_count=required, observations_remaining=remaining,
            completion_value=completion, dust_cost=0.0, inventory_cost=0.0,
            latency_cost=0.0, activity_deficit_value=activation_value, adverse_selection_risk=0.0,
            last_realization_time=None, recent_realized_pnl=None,
            inventory_state="FLAT" if not inventory_blocked else "OPEN", lane=lane,
            volume_cap_headroom=headroom, final_score=final_score, eligible=eligible,
            reject_reason=reject, score_velocity_value=0.0,
            expected_realization_time=None, realization_time_reference=None,
            lifecycle_ev=edge_signal, total_score_component=total_score,
            required_entry_ev=0.0, taker_prob_live=0.0, taker_prob_prior=0.0,
            taker_prob_effective=0.0, taker_prob_excess=0.0,
            expected_taker_cost=0.0, expected_future_taker_cost_bps=0.0,
            expected_taker_exit_fee_bps=0.0, expected_crossing_bps=capture_bps,
            expected_slippage_bps=0.0, maker_fee_bps=maker_fee, taker_fee_bps=taker_fee,
            lifecycle_exit_samples=0, base_lifecycle_value=edge_signal,
            raw_taker_penalty=0.0, capped_taker_penalty=0.0, adverse_penalty=0.0,
            holding_penalty=0.0, latency_penalty=0.0, crossing_penalty=0.0,
            completion_multiplier=1.0, entry_ev_margin=current_edge_bps,
            entry_ev_pass=eligible, **extra,
        )

    def _direct_tail_history_rows(self, book_id: int) -> list[dict[str, float | int]]:
        table = getattr(self, "_direct_tail_history", None)
        if not isinstance(table, dict):
            table = {}
            self._direct_tail_history = table
        rows = table.get(int(book_id))
        if not isinstance(rows, list):
            rows = []
            table[int(book_id)] = rows
        return rows

    def _direct_tail_note_observation(
        self, *, book_id: int, tick: int, risk_bps: float, maker_net_bps: float,
        taker_net_bps: float, failed_exit_count: int, action: str, reason: str,
    ) -> None:
        rows = self._direct_tail_history_rows(int(book_id))
        row = {
            "tick": int(tick), "risk_bps": float(risk_bps),
            "maker_net_bps": float(maker_net_bps), "taker_net_bps": float(taker_net_bps),
            "failed_exit_count": int(failed_exit_count), "action": str(action or ""),
            "reason": str(reason or ""),
        }
        if rows and int(rows[-1].get("tick", -1)) == int(tick):
            rows[-1] = row
        else:
            rows.append(row)
        if len(rows) > int(DIRECT_TAIL_HISTORY_MAX):
            del rows[:-int(DIRECT_TAIL_HISTORY_MAX)]

    def _direct_tail_best_recovery_snapshot(self, book_id: int) -> dict[str, Any] | None:
        rows = self._direct_tail_history_rows(int(book_id))
        eligible = [r for r in rows if float(r.get("risk_bps", 0.0) or 0.0) <= DIRECT_RECOVERY_TRIGGER_BPS]
        if not eligible:
            return None
        best = max(eligible, key=lambda r: float(r.get("maker_net_bps", -1e9) or -1e9))
        return dict(best)

    def _direct_tail_mark_recovery(self, book_id: int, *, reason: str, decision, captured: dict[str, Any]) -> None:
        table = getattr(self, "_direct_tail_recovery_active", None)
        if not isinstance(table, dict):
            table = {}
            self._direct_tail_recovery_active = table
        bid = int(book_id)
        now = int(getattr(self, "_tick", 0) or 0)
        row = table.get(bid)
        if not isinstance(row, dict):
            row = {
                "first_tick": now,
                "best_maker_net_bps": float(captured.get("maker_net_bps", 0.0) or 0.0),
                "best_maker_tick": now,
                "actions": 0,
            }
            table[bid] = row
        maker = float(captured.get("maker_net_bps", 0.0) or 0.0)
        if maker > float(row.get("best_maker_net_bps", -1e9) or -1e9):
            row["best_maker_net_bps"] = maker
            row["best_maker_tick"] = now
        row["last_tick"] = now
        row["last_reason"] = str(reason or "")
        row["last_action"] = str(getattr(decision, "action", "") or "")
        row["actions"] = int(row.get("actions", 0) or 0) + 1

    def _direct_tail_emit_counterfactual(self, *, book_id: int, decision, captured: dict[str, Any]) -> None:
        reason = str(getattr(decision, "reason", "") or "")
        if reason not in {"HARD_ESCAPE_CLIP", "ABSOLUTE_PROTECTION_REDUCE"}:
            return
        best = self._direct_tail_best_recovery_snapshot(int(book_id))
        if best is None:
            return
        final_taker = float(captured.get("taker_net_bps", 0.0) or 0.0)
        best_maker = float(best.get("maker_net_bps", 0.0) or 0.0)
        avoided = best_maker - final_taker
        self._direct_tail_counterfactual_events = int(
            getattr(self, "_direct_tail_counterfactual_events", 0) or 0
        ) + 1
        try:
            self._emit(
                "A174_TAIL_COUNTERFACTUAL", force=True,
                tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                final_reason=reason,
                final_position_risk_bps=float(captured.get("position_risk_bps", 0.0) or 0.0),
                final_taker_net_bps=final_taker,
                best_prior_maker_net_bps=best_maker,
                best_prior_maker_tick=int(best.get("tick", -1) or -1),
                best_prior_risk_bps=float(best.get("risk_bps", 0.0) or 0.0),
                potential_avoided_loss_bps=float(avoided),
                observed_expected_hard_taker_loss_bps=DIRECT_EXPECTED_HARD_TAKER_LOSS_BPS,
                recovery_version=DIRECT_TAIL_RECOVERY_VERSION,
            )
        except Exception:
            pass

    def _research_apply_unified_exit(self, legacy, **kwargs):
        """Apply A1.7.2 Direct exit semantics without mutating frozen Research.

        A1.7.1's true-MTM risk correction remains unchanged.  A1.7.2 additionally
        records the Direct action for the final placement boundary and rewrites a
        Direct WAIT into an explicit WAIT realization token so telemetry and
        execution agree.  The imported base symbol is restored immediately.
        """
        module = importlib.import_module("Strategy1_Research")
        original = getattr(module, "choose_position_exit")

        inventory = kwargs.get("inventory")
        true_unrealized = getattr(inventory, "unrealized_bps", None)
        book_id_outer = int(kwargs.get("book_id", -1))
        tick_outer = int(getattr(self, "_tick", 0) or 0)
        captured: dict[str, Any] = {}

        def a172_direct_chooser(**exit_kwargs):
            caller_unrealized = exit_kwargs.get("unrealized_bps")
            exit_kwargs["unrealized_bps"] = true_unrealized
            # v6.0.0: a short lot exits with a minimum-order clip; the frozen caller judged it by its size.
            self._v600_chooser_kwargs(exit_kwargs)
            exit_kwargs["hard_escape_min_age_ticks"] = float(
                getattr(self, "research_bounded_loss_escape_min_age_ticks", 2.0)
            )
            exit_kwargs["positive_maker_veto_enabled"] = bool(
                getattr(self, "research_positive_maker_veto_enabled", True)
            )
            exit_kwargs["positive_maker_veto_floor_bps"] = float(
                getattr(self, "research_positive_maker_veto_floor_bps", 1.0)
            )
            exit_kwargs["positive_maker_veto_max_failed_exits"] = int(
                getattr(self, "research_positive_maker_veto_max_failed_exits", 4)
            )
            exit_kwargs["absolute_positive_maker_veto_enabled"] = True
            exit_kwargs["absolute_positive_maker_veto_floor_bps"] = float(
                getattr(self, "research_positive_maker_veto_floor_bps", 1.0)
            )
            exit_kwargs["absolute_positive_maker_veto_max_failed_exits"] = 1
            base_decision = choose_observable_position_exit(**exit_kwargs)
            position_risk_bps = float(true_unrealized or 0.0)
            history = self._direct_tail_history_rows(book_id_outer) if book_id_outer >= 0 else []
            risk_velocity = direct_tail_risk_velocity(
                history, tick=tick_outer, current_risk_bps=position_risk_bps,
            )
            decision = choose_tail_recovery_override(
                base_decision=base_decision,
                maker_net_bps=float(exit_kwargs.get("maker_net_bps", 0.0) or 0.0),
                taker_net_bps=float(exit_kwargs.get("taker_net_bps", 0.0) or 0.0),
                position_risk_bps=position_risk_bps,
                risk_velocity_bps_per_tick_value=float(risk_velocity),
                inventory_qty=float(exit_kwargs.get("inventory_qty", 0.0) or 0.0),
                inventory_age=float(exit_kwargs.get("inventory_age", 0.0) or 0.0),
                failed_exit_count=int(exit_kwargs.get("failed_exit_count", 0) or 0),
                catastrophic_hard_risk=bool(exit_kwargs.get("catastrophic_hard_risk", False)),
                reduction_executable=bool(exit_kwargs.get("reduction_executable", False)),
            )
            pre_a1744_decision = decision
            # A1.7.5: a book that has been vetoed for DIRECT_A175_TAIL_BUDGET_TICKS
            # consecutive ticks has proven the Maker is not completing. Restore the
            # base decision and take the bounded loss rather than holding forever.
            budget_exhausted = bool(
                book_id_outer >= 0
                and int(book_id_outer) in getattr(self, "_direct_a175_tail_budget_exhausted", set())
            )
            if budget_exhausted:
                # The budget is spent per position, not per book: once the book
                # is flat the next position starts with a full hold budget.
                try:
                    if float(self._research_abs_inventory(int(book_id_outer))) <= float(
                        self._execution_flat_epsilon()
                    ):
                        self._direct_a175_tail_budget_exhausted.discard(int(book_id_outer))
                        getattr(self, "_direct_a175_tail_budget_spent", {}).pop(
                            int(book_id_outer), None
                        )
                        budget_exhausted = False
                except Exception:
                    pass
            decision = apply_positive_maker_kappa_veto(
                base_decision=pre_a1744_decision,
                maker_net_bps=float(exit_kwargs.get("maker_net_bps", 0.0) or 0.0),
                taker_net_bps=float(exit_kwargs.get("taker_net_bps", 0.0) or 0.0),
                maker_executable=bool(exit_kwargs.get("maker_executable", True)),
                catastrophic_hard_risk=bool(exit_kwargs.get("catastrophic_hard_risk", False)),
                inventory_qty=float(exit_kwargs.get("inventory_qty", 0.0) or 0.0),
                tail_budget_exhausted=budget_exhausted,
            )
            # A1.9.8: in ABSOLUTE_PROTECTION neither loss-recovery arm may swap the
            # frozen taker for a resting maker exit priced at a loss.  Book 95 went
            # from -40 to -110..-130 bps in the two ticks one held the book.
            a198_replaced = decision
            a199_rule = None
            if self._a199_exit_pending_enabled():
                # A1.9.9: once a position has reached ABSOLUTE its risk state owns
                # the exit.  A1.9.8 runs first, unchanged; then a loss maker, WAIT
                # or PARK is refused while the position is pending.  Book 49 parked
                # 216 evaluations on a crossed touch and exited at -1,211 bps.
                decision, a199_rule, a198_arm = self._a199_authorize_exit(
                    book_id_outer, base_decision=base_decision, decision=a198_replaced,
                    exit_kwargs=exit_kwargs, inventory=inventory, book=kwargs.get("book"),
                    position_risk_bps=position_risk_bps,
                )
            else:
                decision, a198_arm = restore_absolute_taker(
                    base_decision=base_decision, decision=a198_replaced,
                    enabled=self._a198_enabled(),
                )
            captured["a198_arm"] = a198_arm
            captured["a198_replaced"] = a198_replaced if a198_arm else None
            captured["a199_rule"] = a199_rule
            captured["a199_replaced"] = (
                a198_replaced if a199_rule in (RULE_LOSS_MAKER, RULE_NOT_EXITING, RULE_POSITIVE_MAKER) else None
            )
            # v6.1: last word on the action.  A taker the validator would book at a loss is
            # refused at the executor, so leaving it as TAKER rests nothing and the lot just ages;
            # WAIT and PARK on a full lot are idle for the same reason.  Both become the floor.
            try:
                decision, v61_rewrite = self._v61_rewrite_exit(
                    book_id_outer, decision, book=kwargs.get("book"),
                    inventory=inventory, exit_kwargs=exit_kwargs,
                )
            except Exception:
                self._v61_errors = int(getattr(self, "_v61_errors", 0) or 0) + 1
                v61_rewrite = V61_REWRITE_NONE
            captured["v61_rewrite"] = v61_rewrite
            captured["pre_a1744_decision"] = pre_a1744_decision
            captured["a175_tail_budget_exhausted"] = budget_exhausted
            captured.update(exit_kwargs)
            captured["caller_unrealized_bps"] = caller_unrealized
            captured["position_risk_bps"] = position_risk_bps
            captured["risk_velocity_bps_per_tick"] = float(risk_velocity)
            captured["base_decision"] = base_decision
            captured["decision"] = decision
            return decision

        setattr(module, "choose_position_exit", a172_direct_chooser)
        try:
            result = super()._research_apply_unified_exit(legacy, **kwargs)
        finally:
            setattr(module, "choose_position_exit", original)

        # A1.9.8: count and log every ABSOLUTE taker restored over a loss-recovery maker.
        try:
            if captured.get("a198_arm") and book_id_outer >= 0:
                self._a198_note_restore(book_id_outer, captured)
        except Exception:
            pass
        # A1.9.9: count and log every exit the pending state refused to leave resting.
        try:
            if captured.get("a199_replaced") is not None and book_id_outer >= 0:
                self._a199_note_override(book_id_outer, captured)
        except Exception:
            pass

        # A1.7.4.4: explicit Kappa-risk-veto telemetry. This state is diagnostic
        # only and never changes execution after the chooser has returned.
        try:
            pre_a1744 = captured.get("pre_a1744_decision")
            decision_now = captured.get("decision")
            if pre_a1744 is not None and decision_now is not None and book_id_outer >= 0:
                maker_now = float(captured.get("maker_net_bps", 0.0) or 0.0)
                taker_now = float(captured.get("taker_net_bps", 0.0) or 0.0)
                catastrophic_now = bool(captured.get("catastrophic_hard_risk", False))
                budget_now = bool(captured.get("a175_tail_budget_exhausted", False))
                label = classify_a1744_outcome(
                    base_decision=pre_a1744, final_decision=decision_now,
                    maker_net_bps=maker_now, taker_net_bps=taker_now,
                    maker_executable=bool(captured.get("maker_executable", True)),
                    catastrophic_hard_risk=catastrophic_now,
                    tail_budget_exhausted=budget_now,
                )
                active = getattr(self, "_direct_a1744_veto_active", {})
                was_active = int(book_id_outer) in active
                if label in ("A1744_POSITIVE_MAKER_RISK_VETO", "A175_RELATIVE_MAKER_RISK_VETO"):
                    relative = label == "A175_RELATIVE_MAKER_RISK_VETO"
                    self._direct_a1744_veto_count = int(getattr(self, "_direct_a1744_veto_count", 0) or 0) + 1
                    if relative:
                        self._direct_a175_relative_veto_count = int(
                            getattr(self, "_direct_a175_relative_veto_count", 0) or 0
                        ) + 1
                    # A1.7.5: spend one tick of this book's bounded hold budget.
                    spent = getattr(self, "_direct_a175_tail_budget_spent", None)
                    if spent is None:
                        spent = {}
                        self._direct_a175_tail_budget_spent = spent
                    used = int(spent.get(int(book_id_outer), 0) or 0) + 1
                    spent[int(book_id_outer)] = used
                    if used >= DIRECT_A175_TAIL_BUDGET_TICKS:
                        self._direct_a175_tail_budget_exhausted.add(int(book_id_outer))
                        self._direct_a175_tail_budget_releases = int(
                            getattr(self, "_direct_a175_tail_budget_releases", 0) or 0
                        ) + 1
                        self._emit(
                            "A175_TAIL_BUDGET_EXHAUSTED", force=True,
                            tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id_outer),
                            budget_ticks=int(DIRECT_A175_TAIL_BUDGET_TICKS),
                            vetoed_ticks=int(used),
                            maker_net_bps=maker_now, taker_net_bps=taker_now,
                            base_reason=str(getattr(pre_a1744, "reason", "") or ""),
                            version=DIRECT_POSITIVE_MAKER_KAPPA_VERSION,
                        )
                    active[int(book_id_outer)] = {
                        "tick": int(getattr(self, "_tick", 0) or 0),
                        "maker_net_bps": maker_now, "taker_net_bps": taker_now,
                        "base_reason": str(getattr(pre_a1744, "reason", "") or ""),
                    }
                    self._emit(
                        label, force=True,
                        tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id_outer),
                        base_reason=str(getattr(pre_a1744, "reason", "") or ""),
                        maker_net_bps=maker_now, taker_net_bps=taker_now,
                        maker_advantage_bps=float(maker_now - taker_now),
                        veto_arm="RELATIVE" if relative else "ABSOLUTE",
                        strong_maker_floor_bps=float(DIRECT_A1744_STRONG_MAKER_FLOOR_BPS),
                        advantage_floor_bps=float(DIRECT_A175_MAKER_ADVANTAGE_BPS),
                        risk_band=str(getattr(pre_a1744, "risk_band", "") or ""),
                        tail_budget_ticks=int(DIRECT_A175_TAIL_BUDGET_TICKS),
                        tail_budget_used=int(used),
                        failed_exit_count=int(captured.get("failed_exit_count", 0) or 0),
                        position_risk_bps=float(captured.get("position_risk_bps", 0.0) or 0.0),
                        catastrophic=0, version=DIRECT_POSITIVE_MAKER_KAPPA_VERSION,
                    )
                else:
                    if label == "A1744_TAKER_ALLOWED_CATASTROPHIC":
                        self._direct_a1744_catastrophic_bypass_count = int(getattr(self, "_direct_a1744_catastrophic_bypass_count", 0) or 0) + 1
                        self._emit(
                            label, force=True, tick=int(getattr(self, "_tick", 0) or 0),
                            book=int(book_id_outer), maker_net_bps=maker_now, taker_net_bps=taker_now,
                            base_reason=str(getattr(pre_a1744, "reason", "") or ""),
                            version=DIRECT_POSITIVE_MAKER_KAPPA_VERSION,
                        )
                    elif label == "A1744_TAKER_ALLOWED_MAKER_NOT_STRONG":
                        self._direct_a1744_maker_not_strong_bypass_count = int(getattr(self, "_direct_a1744_maker_not_strong_bypass_count", 0) or 0) + 1
                        self._emit(
                            label, force=True, tick=int(getattr(self, "_tick", 0) or 0),
                            book=int(book_id_outer), maker_net_bps=maker_now, taker_net_bps=taker_now,
                            base_reason=str(getattr(pre_a1744, "reason", "") or ""),
                            strong_maker_floor_bps=float(DIRECT_A1744_STRONG_MAKER_FLOOR_BPS),
                            version=DIRECT_POSITIVE_MAKER_KAPPA_VERSION,
                        )
                    if was_active:
                        prior = active.pop(int(book_id_outer), {})
                        # A1.7.5: the veto run ended.  An exhausted budget is
                        # deliberately *not* cleared here -- it must survive
                        # until the book goes flat, otherwise the hold would
                        # immediately re-arm and oscillate against a Taker that
                        # is not completing.
                        if label == "A175_TAIL_BUDGET_EXHAUSTED":
                            prior_used = int(
                                getattr(self, "_direct_a175_tail_budget_spent", {}).get(
                                    int(book_id_outer), 0
                                ) or 0
                            )
                        else:
                            prior_used = int(
                                getattr(self, "_direct_a175_tail_budget_spent", {}).pop(
                                    int(book_id_outer), 0
                                ) or 0
                            )
                            self._direct_a175_tail_budget_exhausted.discard(int(book_id_outer))
                        self._emit(
                            "A1744_VETO_RELEASE", force=True,
                            tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id_outer),
                            release_label=str(label or "RISK_TAKER_NO_LONGER_IN_SCOPE"),
                            prior_tick=int(prior.get("tick", -1) or -1),
                            tail_budget_used=prior_used,
                            maker_net_bps=maker_now, taker_net_bps=taker_now,
                            catastrophic=int(catastrophic_now),
                            version=DIRECT_POSITIVE_MAKER_KAPPA_VERSION,
                        )
        except Exception:
            pass

        # A1.7.2: persist only the current-tick Direct authority so the final
        # Maker-placement boundary can enforce WAIT as a real hold.
        try:
            decision = captured.get("decision")
            book_id = int(kwargs.get("book_id", -1))
            if decision is not None and book_id >= 0:
                reason_token = str(getattr(decision, "reason", "") or "")
                recovery_floor = recovery_maker_floor_for_reason(reason_token)
                self._direct_exit_authority_last[book_id] = {
                    "tick": int(getattr(self, "_tick", 0) or 0),
                    "action": str(getattr(decision, "action", "") or ""),
                    "reason": reason_token,
                    "risk_band": str(getattr(decision, "risk_band", "") or ""),
                    "maker_net_bps": float(captured.get("maker_net_bps", 0.0) or 0.0),
                    "taker_net_bps": float(captured.get("taker_net_bps", 0.0) or 0.0),
                    "position_risk_bps": float(captured.get("position_risk_bps", 0.0) or 0.0),
                    "risk_velocity_bps_per_tick": float(captured.get("risk_velocity_bps_per_tick", 0.0) or 0.0),
                    "recovery_maker_authorized": int(is_recovery_maker_reason(reason_token)),
                    "recovery_maker_floor_bps": recovery_floor,
                    "recovery_taker_authorized": int(is_recovery_taker_reason(reason_token)),
                }
                if is_recovery_taker_reason(reason_token):
                    # The frozen base sees DEFENSIVE as a non-hard band and would
                    # label this as ordinary economic Taker with a zero loss
                    # floor. Reclassify only the A1.7.4 bounded recovery Taker.
                    result = replace(
                        result, taker_allowed=True, direct_taker_authorized=True,
                        economic_taker_authorized=False, score_taker_authorized=False,
                        risk_taker_authorized=True, aggressive_positive_ev_taker_authorized=False,
                        taker_authority="RECOVERY", allowed_loss_floor_bps=DIRECT_RECOVERY_TAKER_FLOOR_BPS,
                        trigger=reason_token, hybrid_reason=reason_token,
                    )
                if str(getattr(decision, "action", "") or "") == ACTION_WAIT:
                    # The frozen base maps every non-Taker decision back to the
                    # legacy Maker rung.  Rewrite only the outward token; the
                    # placement override below enforces the actual no-new-order
                    # behavior and optionally cancels stale negative exits.
                    result = replace(
                        result, action=ACTION_WAIT, selected_action=ACTION_WAIT,
                        taker_allowed=False, direct_taker_authorized=False,
                        economic_taker_authorized=False, score_taker_authorized=False,
                        risk_taker_authorized=False,
                        aggressive_positive_ev_taker_authorized=False,
                        taker_authority="NONE", trigger=str(getattr(decision, "reason", "WAIT")),
                        hybrid_reason=str(getattr(decision, "reason", "WAIT")),
                    )
                # A1.9.5 F8: carry the FINAL floor -- after the recovery and
                # WAIT reclassifications above have had their say -- so the
                # execution path can bound the order it actually sends.  The
                # record is written before those `replace` calls, so reading
                # `result` here rather than there is what makes the recorded
                # floor match the decision that ships.
                self._direct_exit_authority_last[book_id]["allowed_loss_floor_bps"] = float(
                    getattr(result, "allowed_loss_floor_bps", 0.0) or 0.0
                )
                self._direct_exit_authority_last[book_id]["taker_authority"] = str(
                    getattr(result, "taker_authority", "") or ""
                )
        except Exception:
            pass

        # Dedicated A1.7.1 diagnostics remain for cross-version log continuity.
        # explicit in runtime logs without changing the frozen base logger.
        try:
            decision = captured.get("decision")
            if decision is not None:
                self._emit(
                    "A171_EXIT_DIAGNOSTIC",
                    force=True,
                    tick=int(getattr(self, "_tick", 0) or 0),
                    book=int(kwargs.get("book_id", -1)),
                    position_risk_bps=(
                        None if true_unrealized is None else float(true_unrealized)
                    ),
                    maker_net_bps=float(captured.get("maker_net_bps", 0.0) or 0.0),
                    taker_net_bps=float(captured.get("taker_net_bps", 0.0) or 0.0),
                    caller_unrealized_bps=float(captured.get("caller_unrealized_bps", 0.0) or 0.0),
                    inventory_age=float(captured.get("inventory_age", 0.0) or 0.0),
                    failed_exit_count=int(captured.get("failed_exit_count", 0) or 0),
                    risk_band=str(getattr(decision, "risk_band", "")),
                    selected_action=str(getattr(decision, "action", "")),
                    exit_reason=str(getattr(decision, "reason", "")),
                    hard_escape_min_age_ticks=float(
                        captured.get("hard_escape_min_age_ticks", 2.0) or 2.0
                    ),
                    positive_maker_veto_floor_bps=float(
                        captured.get("positive_maker_veto_floor_bps", 1.0) or 1.0
                    ),
                    positive_maker_veto_max_failed_exits=int(
                        captured.get("positive_maker_veto_max_failed_exits", 4) or 4
                    ),
                    risk_source="MID_MTM_EXCLUDES_CROSSING_COST",
                )
        except Exception:
            pass
        try:
            decision = captured.get("decision")
            if decision is not None:
                self._emit(
                    "A172_EXIT_AUTHORITY", force=True,
                    tick=int(getattr(self, "_tick", 0) or 0),
                    book=int(kwargs.get("book_id", -1)),
                    direct_action=str(getattr(decision, "action", "")),
                    realization_action=str(getattr(result, "selected_action", "")),
                    reason=str(getattr(decision, "reason", "")),
                    maker_net_bps=float(captured.get("maker_net_bps", 0.0) or 0.0),
                    taker_net_bps=float(captured.get("taker_net_bps", 0.0) or 0.0),
                    wait_is_terminal=int(str(getattr(decision, "action", "")) == ACTION_WAIT),
                )
                if str(getattr(decision, "action", "") or "") == ACTION_TAKER_EXIT:
                    self._a1961_note_taker_decision(
                        book_id=int(kwargs.get("book_id", -1)),
                        tick=int(getattr(self, "_tick", 0) or 0),
                        taker_net_bps=float(captured.get("taker_net_bps", 0.0) or 0.0),
                        reason=str(getattr(decision, "reason", "") or ""),
                    )
        except Exception:
            pass

        # A1.7.4 recovery/counterfactual telemetry and per-book MTM history.
        try:
            decision = captured.get("decision")
            if decision is not None and book_id_outer >= 0:
                reason_token = str(getattr(decision, "reason", "") or "")
                base_decision = captured.get("base_decision")
                risk_bps = float(captured.get("position_risk_bps", 0.0) or 0.0)
                maker_bps = float(captured.get("maker_net_bps", 0.0) or 0.0)
                taker_bps = float(captured.get("taker_net_bps", 0.0) or 0.0)
                velocity = float(captured.get("risk_velocity_bps_per_tick", 0.0) or 0.0)
                failed = int(captured.get("failed_exit_count", 0) or 0)
                age = float(captured.get("inventory_age", 0.0) or 0.0)
                is_recovery = (
                    reason_token.startswith("RECOVERY_")
                    or reason_token.startswith("HARD_RECOVERY_")
                    or reason_token.startswith("ABSOLUTE_RECOVERY_")
                )
                if is_recovery:
                    self._direct_tail_mark_recovery(
                        book_id_outer, reason=reason_token, decision=decision, captured=captured,
                    )
                    if is_recovery_maker_reason(reason_token):
                        self._direct_tail_recovery_maker_attempts = int(
                            getattr(self, "_direct_tail_recovery_maker_attempts", 0) or 0
                        ) + 1
                    if is_recovery_taker_reason(reason_token):
                        self._direct_tail_recovery_taker_reductions = int(
                            getattr(self, "_direct_tail_recovery_taker_reductions", 0) or 0
                        ) + 1
                    self._emit(
                        "A174_RECOVERY_DECISION", force=True,
                        tick=tick_outer, book=book_id_outer,
                        recovery_version=DIRECT_TAIL_RECOVERY_VERSION,
                        base_action=str(getattr(base_decision, "action", "") or ""),
                        base_reason=str(getattr(base_decision, "reason", "") or ""),
                        selected_action=str(getattr(decision, "action", "") or ""),
                        recovery_reason=reason_token,
                        risk_band=str(getattr(decision, "risk_band", "") or ""),
                        position_risk_bps=risk_bps,
                        risk_velocity_bps_per_tick=velocity,
                        maker_net_bps=maker_bps, taker_net_bps=taker_bps,
                        inventory_age=age, failed_exit_count=failed,
                        maker_floor_bps=recovery_maker_floor_for_reason(reason_token),
                        maker_advantage_bps=DIRECT_RECOVERY_MAKER_ADVANTAGE_BPS,
                        recovery_taker_floor_bps=DIRECT_RECOVERY_TAKER_FLOOR_BPS,
                    )
                self._direct_tail_emit_counterfactual(
                    book_id=book_id_outer, decision=decision, captured=captured,
                )
                self._direct_tail_note_observation(
                    book_id=book_id_outer, tick=tick_outer, risk_bps=risk_bps,
                    maker_net_bps=maker_bps, taker_net_bps=taker_bps,
                    failed_exit_count=failed, action=str(getattr(decision, "action", "") or ""),
                    reason=reason_token,
                )
        except Exception:
            pass
        return result

    def _direct_current_exit_authority(self, book_id: int) -> dict[str, Any] | None:
        row = (getattr(self, "_direct_exit_authority_last", {}) or {}).get(int(book_id))
        if not isinstance(row, dict):
            return None
        if int(row.get("tick", -1)) != int(getattr(self, "_tick", 0) or 0):
            return None
        return row

    # ------------------------------------------------------------------
    # A1.9 Phase A: queue-preserving Maker-exit measurement (shadow mode).
    #
    # A1.8 cut the profitable exit TTL to one publish cycle and lost ~24% of RT
    # velocity.  The real defect is that TTL, not the hysteresis rule, is the
    # repricing clock: final validation drops any placement on a book that
    # still holds a live order, and nothing cancels the stale exit first, so a
    # declined hold cannot actually reprice.  Phase A measures the exit
    # lifecycle -- hold eligibility, tenure, why a resting quote vanished,
    # forgone edge, and cancel-acknowledgement latency -- while changing no
    # decision at all.  Phase B is what acts on the classifier.
    # ------------------------------------------------------------------

    A19_CANCEL_WATCH_MAX_TICKS = 10
    A19_CANCEL_ACK_BUDGET_TICKS = 2
    # Cancel reasons outlive their watch entries; bounded so a long run cannot
    # grow the memo without limit.
    A19_CANCEL_MEMO_MAX = 2048

    # ---- A1.9.1 Phase B: queue-preserving Maker exit -------------------
    # Cancelling an order that is about to expire anyway spends an instruction
    # and a tick to achieve exactly what expiry achieves for free.  One publish
    # cycle is the horizon below which the two outcomes are identical.
    A191_MIN_REMAINING_TTL_MS = 1000.0
    # Activation watchdog.  Two consecutive runs were invalidated by a build
    # that reported A1.9.1 while the behavioural path was inert, and both were
    # only caught by after-the-fact log analysis.  If this many REPRICE verdicts
    # accumulate with zero cancels emitted, the run says so itself.
    A191_ACTIVATION_ALARM_CANDIDATES = 20

    # ---- A1.9.2 fee-conditioned book risk admission --------------------
    # A book needs a real track record before its history may deny it work.
    A192_MIN_BOOK_SAMPLES = 5
    # Recency-weighted mean net realized bps per round trip.  Below zero the
    # book loses money on average; `net_bps_ewma` is already persisted per book.
    A192_NET_BPS_FLOOR = 0.0
    # Cubic Taker shortfall, expressed back in bps by taking the cube root so
    # the threshold stays interpretable and scale-stable.  Kappa's downside is
    # cubic, so this is the term that actually tracks scoring harm rather than
    # a PnL proxy.
    A192_TAIL_SHORTFALL_FLOOR_BPS = 5.0
    # Hard ceiling on how much entry flow this gate may remove.  A fee-only
    # rule scored better in the counterfactual but suppressed 78% of round
    # trips, which would collapse RT velocity from 0.128 to ~0.028 and take
    # Kappa qualification breadth with it.  The cap is what keeps a tail-risk
    # gate from becoming a volume gate.
    A192_MAX_SUPPRESSION_PCT = 35.0
    A192_WINDOW_TICKS = 200
    # Escalating dwell: a book that keeps qualifying is suppressed for longer,
    # but never permanently -- quarantine decays and a rebate always re-admits.
    A192_DWELL_BASE_TICKS = 40
    A192_DWELL_MAX_TICKS = 400
    # Clean Maker-ending round trips that retire one escalation strike.
    A192_RECOVERY_CLEAN_RTS = 2
    # Activation watchdog, mirroring A1.9.1: if this many books are flagged and
    # nothing is ever suppressed, the run says so itself instead of costing a
    # full 4,000 ticks to discover afterwards.
    A192_ACTIVATION_ALARM_CANDIDATES = 20

    # ---- A1.9.3 breadth-critical admission -----------------------------
    # Kappa observations expire on a rolling window (research_kappa_lookback_ns,
    # 3h by default), so qualification breadth is a FLOW, not a stock: a book
    # holds its place only while it keeps producing round trips.  Measured over
    # 1,713 ticks, qualified books track rt_velocity * window / required almost
    # exactly (0.0885 * 2286 / 3 = 67.4 predicted, 64 observed).
    #
    # The defect this fixes: NEGATIVE_CURRENT_EDGE makes the book ineligible,
    # and BOTH breadth mechanisms sit downstream of that flag -- the completion
    # ladder (final_score = edge + completion if eligible) and the expiry /
    # deadline rank bonuses in the frozen base, which return -1e9 before the
    # bonus is applied.  So the machinery built to rescue breadth-critical
    # books is unreachable exactly when the maker fee is positive, which is
    # when breadth is hardest to hold.
    #
    # This overrides that ONE reject, and only for books where a single round
    # trip changes qualification state.  TOXIC / INVENTORY_BLOCKED / UNSAFE /
    # VOLUME_CAP stay hard, and the A1.9.2 tail-risk gate still runs downstream
    # untouched, so extreme-tail books remain blocked even under deficit.
    #
    # Cost bound, two anchors, tighter wins.  Both already exist in this file;
    # neither is fitted to a log.
    #
    #   (a) A192_TAIL_SHORTFALL_FLOOR_BPS -- the project's established scale for
    #       "materially harmful bps".  An observation is worth the SAME whatever
    #       the book's spread is, so the cost ceiling must not scale with spread.
    #       Replaying 4,442 RANK records, a spread-scaled bound alone admitted
    #       book 16 at fee +54.60 for a -26.30 bps entry, which is exactly the
    #       wide-spread Taker-exit trade the A1.9.2 gate exists to stop.
    #   (b) the book's own half-spread -- a thin book cannot fund an observation
    #       out of a spread it does not have.
    A193_MAX_COST_SPREADS = 1.0
    # Ceiling on distinct books holding override status per A192 window.
    # Anchored to research_max_open_books: we cannot hold more than that many
    # positions at once anyway, so a larger budget could not be spent.
    A193_MAX_BREADTH_BOOKS_FALLBACK = 6

    # ---- A1.9.4 rebate conjunction -------------------------------------
    # A1.9.2 short-circuited admission on `fee <= 0` before reading any
    # history, on the finding that a poor-history book entered at a rebate
    # produced 0 bad round trips in 21 observations.  The A1.9.3 run falsified
    # that at scale: books 97/39/61 (mean entry fee -48.9/-10.7/-7.8 bps) were
    # admitted on all 116 of their entries through that branch and produced
    # 1,874.8 of the run's 2,189.0 bps of A174_TAIL_COUNTERFACTUAL avoidable
    # loss -- 85.6% -- ranked in exactly the order of their rebate depth.
    #
    # The structural reason the waiver cannot hold: a rebate is the venue's
    # compensation for expected adverse selection, so its size measures how
    # dangerous the venue thinks quoting there is.  And `book_net_bps_ewma` is
    # realized net per round trip, which ALREADY INCLUDES the rebate.  A rebate
    # book with negative net has been paid and still lost -- the waiver's
    # premise has been tested on that book and failed.  That is an accounting
    # identity, not a threshold fitted to the A1.9.3 log.
    #
    # So the rebate becomes ONE bounded thing: an exemption that holds only
    # while it exceeds the harm the book has actually done
    # (`rebate_bps >= severity_bps`, both already in bps, nothing to calibrate).
    #
    # It deliberately does NOT become a severity credit.  A1.9.2.1 measured
    # Spearman(fee, pnl) = +0.024 within the already-flagged set -- fee decides
    # WHETHER an entry is dangerous, never how dangerous -- and the A1.9.3 run
    # points the other way again: damage ranked by rebate DEPTH (97 > 39 > 61
    # at -48.9 > -10.7 > -7.8 bps), so crediting the rebate would rank the
    # worst books safest.  Severity therefore stays fee-blind, exactly as
    # A1.9.2.1 left it, and the 35% volume cap and severity quantile are
    # untouched.
    A194_REBATE_CONJUNCTION = True

    # ---- A1.9.2.1 severity-prioritised suppression budget --------------
    # A1.9.2 spent its 35% budget in arrival order.  Measured over 500 ticks,
    # the books that GOT budget and the books DENIED by the cap had identical
    # severity (median 40.73 both), i.e. the cap selected at random with
    # respect to risk, and the three largest losses of the run were all
    # cap-admitted Book 114 entries at severity 59-88.  Holding the budget
    # fixed and spending it severity-first covers 33 of the 46 cap-blocks and
    # removes 91% of the cap-bucket damage at zero volume cost, so the cap
    # itself is NOT raised here.
    #
    # Severity deliberately excludes the maker fee.  Within the already
    # flagged set, Spearman(fee, pnl) = +0.024 -- fee decides WHETHER an entry
    # is dangerous and is already the first gate; it carries no ordering
    # information about HOW dangerous.  Tail is the best single ranker
    # (-0.380), net_bps_ewma second (-0.240).
    A1921_SEVERITY_HISTORY = 256
    # Below this many observed candidates the empirical quantile is noise, so
    # the gate keeps A1.9.2 arrival-order behaviour instead of ranking badly.
    A1921_MIN_SEVERITY_SAMPLES = 24
    # Cold start.  ALLOW_INSUFFICIENT_HISTORY admitted 10 of 10 entries at a
    # positive fee, 9 of 10 ended Taker, for 22.1% of the run's cubic downside:
    # "no history" was being read as "no risk".  A book with n samples is
    # shrunk toward the cross-book pool with weight n/(n+K); K is the sample
    # floor, so a book at the threshold is already half its own evidence.
    A1921_COLDSTART_PRIOR_SAMPLES = 5.0
    # Pooling needs a pool.  Below this many qualified books the prior is not
    # meaningful and the old ALLOW_INSUFFICIENT_HISTORY path is kept.
    A1921_MIN_POOL_BOOKS = 3
    # Warm-up is where this gate is worth the most and knows the least.  In the
    # A1.9.2 sample, 81% of the cap-bucket damage landed in the first 50 ticks
    # -- before 24 candidates had been observed, so an empirical quantile did
    # not exist yet and allocation fell back to arrival order.  Book quality
    # survives restarts (every one of the 46 cap-blocks already had >=5
    # samples, median 30), so the candidate severity distribution can be seeded
    # from state the strategy already carries instead of being relearned.
    A1921_SEED_FROM_BOOK_HISTORY = True

    def _a19_reset_exit_observation(self) -> None:
        """Initialise Phase A measurement state.  Touches no strategy threshold."""
        self._a19_exit_seen: dict[int, dict[str, Any]] = {}
        self._a19_pending_action: dict[int, str] = {}
        self._a19_cancel_watch: dict[tuple[int, int], tuple[int, str]] = {}
        self._a19_last_disposition: dict[int, str] = {}
        self._a19_publish_interval_ms = 0.0
        self._a19_exit_evals = 0
        self._a19_eligible_evals = 0
        self._a19_eligible_with_resting = 0
        self._a19_shadow_holds = 0
        self._a19_shadow_reprices = 0
        self._a19_cancel_acks = 0
        self._a19_cancel_ack_ticks_total = 0
        self._a19_cancel_not_acked = 0
        # A1.9.0.1 lifecycle ledger.  The account snapshot never carries the
        # resting exit at decision time, so the live-order view is rebuilt from
        # acknowledged exchange notices instead.  Measurement only.
        self._a19_ledger = DirectExitLedger()
        self._a19_ledger_resting_hits = 0
        self._a19_account_resting_hits = 0
        self._a19_ledger_only_hits = 0
        self._a19_ledger_expiry_lag_hits = 0
        self._a19_last_state_ns = 0
        # A1.9.0.2 live observer.  Separate lifecycle state from the placement
        # path's `_a19_exit_seen`: the two observers watch the same book at
        # different moments, and one dict written by both would interleave into
        # nonsense.  Keeping A1.9.0.1's measurement intact also preserves the
        # A/B that proves the placement path was the blind one.
        self._a19_tick_seen: dict[int, dict[str, Any]] = {}
        self._a19_tick_passes = 0
        self._a19_tick_observations = 0
        self._a19_tick_resting_hits = 0
        self._a19_tick_expiry_lag_hits = 0
        self._a19_tick_eligible = 0
        self._a19_tick_eligible_with_resting = 0
        self._a19_tick_shadow_holds = 0
        self._a19_tick_shadow_reprices = 0
        self._a19_tick_first_sightings = 0
        self._a19_tick_lifecycles = 0
        self._a19_tick_max_observed_ticks = 0
        self._a19_tick_observed_ticks_total = 0
        self._a19_tick_untimed_rows = 0
        # A1.9.0.3: why WE cancelled an order, keyed by order id and outliving
        # the cancel watch.  `_a19_settle_cancel_watch` pops watch entries from
        # the placement path, so a memo the tick observer owns is the only way
        # its lifecycle row can still name the reason one or two states later.
        self._a19_cancel_reason: dict[int, tuple[int, str]] = {}
        self._a19_tick_disposition_counts: dict[str, int] = {}
        self._a19_tick_entry_quote_rows = 0
        # A1.9.1 Phase B authority.  One verdict per book per tick, computed by
        # whichever path reaches the book first and reused by the other, so the
        # placement suppression and the reprice cancel can never disagree.
        self._a191_verdicts: dict[int, dict[str, Any]] = {}
        self._a191_verdict_tick = -1
        self._a191_holds = 0
        self._a191_reprice_cancels = 0
        self._a191_reprice_deferred_ttl = 0
        self._a191_reprice_deferred_budget = 0
        self._a191_placements_suppressed = 0
        self._a191_cancel_emit_failures = 0
        self._a191_postpass_cancels = 0
        self._a191_reprice_candidates = 0
        self._a191_activation_banner_emitted = False
        self._a191_activation_alarm_emitted = False
        # A1.9.1.2: exchange order ids A1.9 has explicitly cancelled, with the
        # identity needed to release their ownership reservation the moment the
        # exchange confirms.  Without this the reservation survives to
        # LOCAL_EXPIRY and the book sits unquoted for ~4 ticks after a cancel.
        self._a191_reprice_release: dict[int, dict[str, Any]] = {}
        self._a191_ownership_releases = 0
        self._a191_ownership_release_blocked = 0
        self._a191_ownership_release_ticks_total = 0
        self._a191_exchange_ack_ticks_total = 0
        self._a191_exchange_acks = 0
        # A1.9.2 telemetry repair: order ids already acked from the exchange
        # notice, and the notice-ingest clock those acks are measured against.
        self._a19_identity_acked: set[int] = set()
        self._a19_duplicate_acks_suppressed = 0
        self._a19_notice_tick = 0

    def _a192_reset_admission(self) -> None:
        """A1.9.2 admission state.  Touches no threshold owned by another phase."""
        # book -> tick at which the current suppression dwell expires
        self._a192_dwell_until: dict[int, int] = {}
        # book -> consecutive escalation strikes (decayed by clean round trips)
        self._a192_strikes: dict[int, int] = {}
        # book -> clean Maker-ending round trips accumulated toward a decay
        self._a192_clean_rts: dict[int, int] = {}
        self._a192_window_start_tick = 0
        self._a192_window_opportunities = 0
        self._a192_window_suppressed = 0
        # A1.9.3: books granted breadth override this window, deduped so the
        # per-tick re-evaluation of the same book cannot drain the budget.
        self._a193_window_books: set[int] = set()
        self._a193_window_denied: set[tuple[int, str]] = set()
        self._a193_admits = 0
        self._a193_completion_admits = 0
        self._a193_refresh_admits = 0
        self._a193_cost_denies = 0
        self._a193_budget_denies = 0
        self._a193_deficit_denies = 0
        self._a192_suppressions = 0
        self._a192_dwell_suppressions = 0
        self._a192_admissions = 0
        self._a192_cap_blocks = 0
        self._a192_rebate_admits = 0
        self._a192_no_history_admits = 0
        self._a192_book_ok_admits = 0
        self._a192_flagged_books: set[int] = set()
        self._a192_recoveries = 0
        self._a192_candidates = 0
        self._a192_activation_banner_emitted = False
        self._a192_activation_alarm_emitted = False
        # A1.9.2.1 allocation state.
        self._a1921_sev_hist: deque[float] = deque(maxlen=int(self.A1921_SEVERITY_HISTORY))
        self._a192_window_candidates = 0
        self._a1921_rank_defers = 0
        self._a1921_coldstart_shrinks = 0
        self._a1921_coldstart_flagged = 0
        self._a1921_pool_tick = -1
        self._a1921_pool: dict[str, float] | None = None
        self._a1921_seeded = False
        self._a1921_seed_count = 0
        # A1.9.4 rebate conjunction state.
        self._a194_rebate_covered_admits = 0
        self._a194_waiver_withdrawn = 0
        self._a194_uncovered_bps_total = 0.0
        self._a194_withdrawn_books: set[int] = set()
        self._a194_covered_books: set[int] = set()

    def _a192_enabled(self) -> bool:
        return bool(getattr(self, "research_a192_book_risk_admission_enabled", True))

    def _a192_runtime_phase(self) -> str:
        """The phase this process is ACTUALLY running, not the one it shipped as."""
        return (
            DIRECT_A192_PHASE_BEHAVIOURAL if self._a192_enabled()
            else DIRECT_A192_PHASE_DISABLED
        )

    def _a192_behaviour_change(self) -> int:
        return int(self._a192_enabled())

    # ------------------------------------------------------------------
    # A1.9.2 fee-conditioned book risk admission.  Fresh entry only: this gate
    # is consulted from `_place_skewed_quotes`, which already returns before
    # any economics when the book is not FLAT, so inventory reduction can never
    # reach it.  Exits keep their full A1.7.x authority.
    # ------------------------------------------------------------------

    def _a192_book_risk(self, book_id: int) -> dict[str, Any]:
        """Realized-quality read built only from state the strategy already keeps."""
        stats = (getattr(self, "_direct_maker_quality_by_book", {}) or {}).get(int(book_id))
        samples = int(getattr(stats, "count", 0) or 0) if stats is not None else 0
        net_bps = float(getattr(stats, "net_bps_ewma", 0.0) or 0.0) if stats is not None else 0.0
        cube = float(getattr(stats, "taker_net_shortfall_cube_ewma", 0.0) or 0.0) if stats is not None else 0.0
        cube = max(0.0, cube)
        # Cube root returns the EWMA to bps so the floor stays interpretable and
        # does not have to be recalibrated when shortfall magnitudes move.
        tail_bps = cube ** (1.0 / 3.0) if cube > 0.0 else 0.0
        return {
            "book_samples": samples,
            "book_net_bps_ewma": round(net_bps, 4),
            "book_tail_shortfall_bps": round(tail_bps, 4),
        }

    def _a192_window_roll(self, tick: int) -> None:
        """Restart the suppression-rate window so the cap is rolling, not lifetime."""
        if int(tick) - int(getattr(self, "_a192_window_start_tick", 0) or 0) >= int(self.A192_WINDOW_TICKS):
            self._a192_window_start_tick = int(tick)
            self._a192_window_opportunities = 0
            self._a192_window_suppressed = 0
            self._a192_window_candidates = 0
            self._a193_window_books = set()
            self._a193_window_denied = set()

    def _a1921_enabled(self) -> bool:
        return bool(getattr(self, "research_a1921_severity_priority_enabled", True))

    # ---- A1.9.4 rebate conjunction -------------------------------------
    def _a194_enabled(self) -> bool:
        return bool(
            getattr(self, "research_a194_rebate_conjunction_enabled", True)
        ) and bool(self.A194_REBATE_CONJUNCTION)

    @staticmethod
    def _a194_rebate_bps(maker_fee_bps: float) -> float:
        """The rebate this book pays, as a positive bps figure (0 if a cost)."""
        try:
            return max(0.0, -float(maker_fee_bps or 0.0))
        except (TypeError, ValueError):
            return 0.0

    # ---- A1.9.5 step 1: inventory reconciliation observer ---------------
    def _a195_reconcile_enabled(self) -> bool:
        return bool(getattr(self, "research_a195_reconcile_observe", True))

    def _a195_local_base_by_book(self, books: Any) -> dict[int, float]:
        """Signed net base per book, from the pure tracker snapshot.

        ``_net_inventory`` is deliberately not used: it advances
        ``_position_ticks``, which drives the exit escalation ladder, so
        calling it from an observer would be a live behaviour change.
        """
        out: dict[int, float] = {}
        ledger = getattr(self, "_a196_legacy_dust_ledger", None) or {}
        fee_residue = getattr(self, "_a1961_fee_residue", None) or {}
        pending = getattr(self, "_a1961_pending_seed", None) or {}
        v502_residue = getattr(self, "_v502_residue", None) or {}
        for raw_id in (books or {}):
            try:
                book_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            try:
                snap = self._position_tracker_snapshot(book_id)
                out[book_id] = float(getattr(snap, "net_qty", 0.0) or 0.0)
            except Exception:
                out[book_id] = 0.0
            # A1.9.6 F9: the legacy ledger is local inventory too.  Without it
            # every ledger book would read as diverged by exactly its dust.
            out[book_id] += float(ledger.get(book_id, 0.0) or 0.0)
            # A1.9.6.1: BASE the venue took as fees, and inherited lots still
            # waiting for a quote, are local inventory as well.
            out[book_id] += float(fee_residue.get(book_id, 0.0) or 0.0)
            out[book_id] += float((pending.get(book_id) or {}).get("net", 0.0) or 0.0)
            # v5.0.2: BASE a flat lifecycle, or a clip's settlement, left behind.
            out[book_id] += float(v502_residue.get(book_id, 0.0) or 0.0)
        return out

    def _a195_emit_reconcile(self, state, tick: int) -> None:
        """Measure venue-vs-local inventory divergence.  Never mutates."""
        if not self._a195_reconcile_enabled():
            return
        books = getattr(state, "books", None) or {}
        if not books:
            return
        local = self._a195_local_base_by_book(books)
        accounts = getattr(self, "accounts", None)
        try:
            legacy = reconcile_account_base(accounts)
        except Exception:
            legacy = {}
        report = reconcile_books(
            local,
            accounts,
            tolerance=float(getattr(
                self, "research_a195_reconcile_tolerance_base", A195_DEFAULT_TOLERANCE_BASE
            )),
            legacy_base_by_book=legacy,
            max_detail_rows=A195_MAX_DETAIL_ROWS,
        )

        self._a195_reconcile_emits = int(getattr(self, "_a195_reconcile_emits", 0) or 0) + 1
        self._a195_reconcile_max_abs_divergence = max(
            float(getattr(self, "_a195_reconcile_max_abs_divergence", 0.0) or 0.0),
            float(report.max_abs_divergence),
        )
        for row in report.diverged_rows:
            if row.resolved:
                self._a195_reconcile_diverged_books.add(int(row.book_id))
            else:
                self._a195_reconcile_unresolved_books.add(int(row.book_id))

        payload = report.as_log()
        self._a195_reconcile_last = payload
        # Step 1 is observation, so nothing acts on this.  The interlock that
        # blocks quoting on a diverged book lands with F2 in step 3.
        self._emit("A195_RECONCILE", force=True, tick=int(tick), **payload)

    # ---- A1.9.5 step 2 (F3): parked-dust capacity class ----------------
    def _a195_dust_capacity_enabled(self) -> bool:
        return bool(getattr(self, "research_a195_dust_capacity_class", True))

    def _a195_legacy_ceiling_bonus(self) -> float:
        """Extra parked-dust headroom bought by the A1.9.5 step-3 seed.

        Zero until a seed actually imports legacy dust, so a build running
        without F2 behaviour keeps exactly the F3 ceiling it had.
        """
        return max(0.0, float(
            getattr(self, "_a195_seed_legacy_ceiling_bonus", 0.0) or 0.0
        ))

    def _a195_dust_exempt_abs(
        self, *, total_abs: float, dust_abs: float, min_order: float, max_abs: float,
    ) -> float:
        """Parked BASE excused from the acquisition budget, 0.0 when disabled.

        Clamped to ``total_abs`` so a stale diag can never hand back more
        exemption than there is inventory, which would manufacture headroom.
        """
        if not self._a195_dust_capacity_enabled():
            return 0.0
        try:
            return dust_class_exempt_abs(
                dust_abs=min(float(dust_abs), float(total_abs)),
                min_order=float(min_order),
                max_abs=float(max_abs),
                max_clips=float(getattr(
                    self, "research_a195_dust_class_max_clips",
                    A195_DUST_CLASS_MAX_CLIPS,
                )),
                legacy_bonus_abs=self._a195_legacy_ceiling_bonus(),
            )
        except Exception:
            # Capacity accounting must fail closed: no exemption is exactly
            # the pre-A1.9.5 behaviour.
            return 0.0

    # ---- A1.9.6: legacy baseline, inherited parked allowance, grid ------
    def _a196_ledger_enabled(self) -> bool:
        return bool(getattr(self, "research_a196_legacy_dust_ledger", True))

    def _a196_inherited_parked_enabled(self) -> bool:
        return bool(getattr(self, "research_a196_inherited_parked_allowance", True))

    def _a196_grid_snap_enabled(self) -> bool:
        return bool(getattr(self, "research_a196_quantity_grid_snap", True))

    def _a196_volume_decimals(self, state=None) -> int:
        """The venue quantity grid, read from the state first.

        The seed runs ahead of the frozen chain, i.e. before the chain copies
        ``volumeDecimals`` into ``_research_volume_decimals`` on the first tick.
        """
        cfg = getattr(state, "config", None) if state is not None else None
        for source in (
            getattr(cfg, "volumeDecimals", None),
            getattr(self, "_research_volume_decimals", None),
        ):
            if source is None:
                continue
            try:
                return max(0, int(source))
            except (TypeError, ValueError):
                continue
        return 8

    def _a196_ledger_abs(self) -> float:
        """BASE held outside the tracker's lifecycle: exposure, not a position.

        The legacy part is written once by the startup seed.  A1.9.6.1 adds the
        BASE the venue takes as fees on buys, which accrues per fill and is never
        a position the agent could exit.  Dust a lifecycle leaves behind still
        lives in the tracker, where F3's overflow signal can see it.
        """
        return (
            float(a196_ledger_total_abs(getattr(self, "_a196_legacy_dust_ledger", None)))
            + self._a1961_fee_residue_abs()
            # v5.0.2: residue a flat lifecycle or a clip's settlement left behind.
            + self._v502_residue_abs()
        )

    def _a196_inherited_parked_report(self, *, max_abs: float):
        """The inherited-parked exemption, with retirement applied.  Fails closed.

        Any fault returns the empty exemption, which is exactly the A1.9.5
        charge.  Retirement is permanent: once an inherited lot's lifecycle has
        ended -- flat, or crossed to the other side -- the book is ordinary.
        """
        if not self._a196_inherited_parked_enabled():
            return NO_INHERITED_EXEMPTION
        inherited = getattr(self, "_a196_inherited_real", None)
        pending_abs = self._a1961_pending_abs()
        if not inherited and pending_abs <= 0.0:
            return NO_INHERITED_EXEMPTION
        if not isinstance(inherited, dict):
            inherited = {}
        try:
            nets: dict[int, float] = {}
            for book_id in list(inherited):
                try:
                    nets[int(book_id)] = float(
                        self._position_tracker_snapshot(int(book_id)).net_qty
                    )
                except Exception:
                    continue          # no reading: neither exempt nor retired
            report = inherited_parked_exemption(
                inherited_real=inherited,
                net_by_book=nets,
                parked_books=list((getattr(self, "_research_parked_inventory", {}) or {}).keys()),
                cap_abs=float(getattr(
                    self, "research_a196_inherited_parked_max_fraction",
                    A196_INHERITED_PARKED_MAX_FRACTION,
                )) * float(max_abs),
                eps=float(self._execution_flat_epsilon()),
                extra_abs=pending_abs,
            )
        except Exception:
            return NO_INHERITED_EXEMPTION
        for book_id in report.retired:
            if inherited.pop(int(book_id), None) is not None:
                retired = getattr(self, "_a196_inherited_retired", None)
                if isinstance(retired, list):
                    retired.append(int(book_id))
        self._a196_inherited_last = report
        self._a196_inherited_exempt_max = max(
            float(getattr(self, "_a196_inherited_exempt_max", 0.0) or 0.0),
            float(report.exempt_abs),
        )
        return report

    def _a196_inherited_parked_exempt(self, *, max_abs: float) -> float:
        """BASE excused from acquisition for parked inherited lots; 0.0 when off."""
        return max(0.0, float(self._a196_inherited_parked_report(max_abs=max_abs).exempt_abs))

    def _a196_emit_admission(
        self, *, tick: int, gate_slots: int, final_slots: int, selected: int,
        normalize_override: bool, **terms: Any,
    ) -> None:
        """One row with every term of the admission formula.  Never mutates state.

        ``gate_slots`` is what the gate itself returned, so a decomposition that
        disagreed with it would be visible on the same row.
        """
        decomposition = a196_admission_decomposition(**terms)
        last = getattr(self, "_a196_inherited_last", NO_INHERITED_EXEMPTION)
        payload = decomposition.as_log()
        payload.update(
            gate_slots=int(gate_slots),
            final_slots=int(final_slots),
            decomposition_matches_gate=int(int(decomposition.portfolio_slots) == int(gate_slots)),
            selected_candidates=int(selected),
            normalize_override=int(bool(normalize_override)),
            ledger_books=len(getattr(self, "_a196_legacy_dust_ledger", {}) or {}),
            inherited_tracked_books=len(getattr(self, "_a196_inherited_real", {}) or {}),
            inherited_exempt_books=list(last.books),
            inherited_uncapped_abs=round(float(last.uncapped_abs), 6),
            inherited_capped=int(bool(last.capped)),
        )
        # A1.9.6.1 counterfactual, telemetry only: the slots admission would have
        # if a parked inherited lot stopped occupying an active book as well as
        # BASE.  ACTIVE bound 76 of 95 rows in the first A1.9.6 run.
        try:
            eps = float(self._execution_flat_epsilon())
            min_order = float(terms.get("min_order", 0.25) or 0.25)
            parked_active = 0
            for parked_book in last.books:
                try:
                    net = float(self._position_tracker_snapshot(int(parked_book)).net_qty)
                except Exception:
                    continue
                if abs(net) + eps >= min_order:
                    parked_active += 1
            reserve = 1 if int(terms.get("dust_count", 0) or 0) > 0 else 0
            active_if = max(0, int(terms.get("max_active", 6)) - (int(terms.get("active_books", 0)) - parked_active) - reserve)
            open_if = max(0, int(terms.get("max_open", 8)) - (int(terms.get("effective_open_books", 0)) - parked_active) - reserve)
            payload.update(
                parked_inherited_active_books=int(parked_active),
                active_slots_if_parked_excused=int(active_if),
                portfolio_slots_if_parked_excused=int(max(0, min(int(decomposition.abs_slots), active_if, open_if))),
                pending_seed_books=len(getattr(self, "_a1961_pending_seed", {}) or {}),
                pending_seed_abs=round(self._a1961_pending_abs(), 6),
                fee_residue_abs=round(self._a1961_fee_residue_abs(), 6),
            )
        except Exception:
            pass
        self._a196_admission_samples = int(getattr(self, "_a196_admission_samples", 0) or 0) + 1
        if int(final_slots) <= 0:
            self._a196_admission_zero_samples = int(
                getattr(self, "_a196_admission_zero_samples", 0) or 0
            ) + 1
        if last.capped:
            self._a196_inherited_capped_samples = int(
                getattr(self, "_a196_inherited_capped_samples", 0) or 0
            ) + 1
        payload.update(getattr(self, "_v601_last", None) or {})
        # v6.0.2: the caps this row was judged against, so a cap change is read, never inferred.
        payload.update(
            cap_max_active=int(terms.get("max_active", 0) or 0),
            cap_max_open=int(terms.get("max_open", 0) or 0),
            cap_max_abs=float(terms.get("max_abs", 0.0) or 0.0),
        )
        self._a196_admission_last = payload
        self._emit("A196_ADMISSION", force=True, tick=int(tick), **payload)

    def _a196_snap_outgoing_quantities(self, response: Any, state) -> int:
        """F11 on the wire: every queued placement executes the unit it names."""
        if not self._a196_grid_snap_enabled():
            return 0
        instructions = getattr(response, "instructions", None)
        if not instructions:
            return 0
        moved = snap_instruction_quantities(instructions, self._a196_volume_decimals(state))
        if moved:
            self._a196_wire_quantities_moved = int(
                getattr(self, "_a196_wire_quantities_moved", 0) or 0
            ) + int(moved)
        return int(moved)

    # ---- A1.9.6.1: seed quote guard, fee residue, taker outcome ---------
    # ---- A1.9.7: the ticks between an entry fill and its first exit evaluation ----
    def _a197_postfill_enabled(self) -> bool:
        return bool(getattr(self, "research_a197_postfill_protect", True))

    def _a197_gap_records(self) -> dict:
        records = getattr(self, "_a197_exit_gap", None)
        if not isinstance(records, dict):
            records = {}
            self._a197_exit_gap = records
        return records

    def _a197_mark(self, inventory, mid) -> float | None:
        return position_mark_bps(
            net_base=getattr(inventory, "net_base", 0.0),
            vwap_entry=getattr(inventory, "vwap_entry", None),
            mid=mid,
            fallback=getattr(inventory, "unrealized_bps", None),
        )

    def _a197_postfill_candidate(self, book_id: int, inventory, mid) -> bool:
        """P1: may this book be evaluated on the tick after its entry fill?"""
        if not self._a197_postfill_enabled():
            return False
        return postfill_protect_eligible(self._a197_mark(inventory, mid))

    def _a197_note_gap_skip(self, book_id: int, reason: str, inventory, mid) -> None:
        try:
            note_gap_skip(
                self._a197_gap_records(), int(book_id),
                tick=int(getattr(self, "_tick", 0) or 0), reason=str(reason),
                mark_bps=self._a197_mark(inventory, mid),
            )
        except Exception:
            pass

    def _a197_close_gap(self, book_id: int, *, p1_acted: bool) -> None:
        """P3: the first managed evaluation ends the gap and logs it."""
        try:
            row = close_gap(
                self._a197_gap_records(), int(book_id),
                eval_tick=int(getattr(self, "_tick", 0) or 0), p1_acted=bool(p1_acted),
            )
        except Exception:
            return
        if row is None:
            return
        self._a197_exit_gap_rows = int(getattr(self, "_a197_exit_gap_rows", 0) or 0) + 1
        self._a197_exit_gap_ticks_total = int(
            getattr(self, "_a197_exit_gap_ticks_total", 0) or 0
        ) + int(row["gap_ticks"])
        try:
            self._emit("A197_EXIT_GAP", force=True, tick=int(row["eval_tick"]), **row)
        except Exception:
            pass

    def _a197_note_own_fill(self, *, book_id: int, before: float, after: float) -> None:
        """P3 opens a gap on an opening fill; P1 watches its exits for a sign flip."""
        eps = float(self._execution_flat_epsilon())
        tick = int(getattr(self, "_tick", 0) or 0)
        bid = int(book_id)
        flipped = sign_flipped(before, after, eps)
        watch = getattr(self, "_a197_postfill_watch", None)
        if isinstance(watch, dict) and bid in watch:
            protect_tick = int(watch.get(bid, tick))
            if flipped:
                watch.pop(bid, None)
                self._a197_postfill_sign_flips = int(
                    getattr(self, "_a197_postfill_sign_flips", 0) or 0
                ) + 1
                try:
                    self._emit(
                        "A197_POSTFILL_FLIP", force=True, tick=tick, book=bid,
                        before=float(before), after=float(after), protect_tick=protect_tick,
                        a197_postfill_protection_version=A197_POSTFILL_PROTECTION_VERSION,
                    )
                except Exception:
                    pass
            elif abs(float(after)) <= eps or tick - protect_tick > A197_FLIP_WATCH_TICKS:
                watch.pop(bid, None)
        records = self._a197_gap_records()
        if abs(float(after)) <= eps:
            records.pop(bid, None)
        elif abs(float(before)) <= eps or flipped:
            open_gap(records, bid, fill_tick=tick)

    def _a197_manage_postfill(
        self, response, state, book_id, book, inventory, params, regime, archetype,
    ) -> int:
        """P1: evaluate an ABSOLUTE position on the tick after its entry fill.

        The exit decision runs exactly as on any other tick.  A taker exit goes
        through the frozen `_execute_aggressive_close`, which cancels every
        resting order on the book -- the entry quotes included -- before the
        market order.  A maker exit is refused (see `_research_place_maker_exit`)
        and any other limit placement for this book is removed, because a
        cancel and a replacement never share a response.  When no cancel covered
        the entry quotes they are cancelled here, as on any post-fill tick.
        """
        bid = int(book_id)
        tick = int(getattr(self, "_tick", 0) or 0)
        entry_ids: list[int] = []
        for order in self._direct_entry_quote_orders(bid):
            try:
                entry_ids.append(int(getattr(order, "id", None)))
            except (TypeError, ValueError):
                continue
        try:
            mid = 0.5 * (float(book.bids[0].price) + float(book.asks[0].price))
        except Exception:
            mid = None
        mark = self._a197_mark(inventory, mid)
        record = self._a197_gap_records().get(bid)
        fill_tick = getattr(record, "fill_tick", None)
        first_new = len(getattr(response, "instructions", None) or ())
        self._a197_close_gap(bid, p1_acted=True)
        self._a197_postfill_book = bid
        try:
            n = int(self._manage_inventory(
                response, state, book_id, book, inventory, params, regime, archetype,
            ) or 0)
        finally:
            self._a197_postfill_book = None
        instructions = getattr(response, "instructions", None)
        stripped = strip_book_limit_orders(instructions, book_id=bid, first_new=first_new)
        kinds = book_instruction_kinds(instructions, book_id=bid, first_new=first_new)
        market = "PLACE_ORDER_MARKET" in kinds
        covered = sorted(set(entry_ids) & book_cancelled_order_ids(
            instructions, book_id=bid, first_new=first_new,
        ))
        fallback = 0
        if covered:
            # The frozen cancel-before-taker cancelled them: register it, so the
            # lifecycle reads an agent cancel rather than an expiry (A1.9.0.3).
            self._a19_note_exit_cancel(bid, covered, ABSENT_ENTRY_QUOTE_CANCEL)
        if len(covered) < len(set(entry_ids)):
            fallback = int(self._direct_cancel_entry_quotes(
                response, bid, reason="INVENTORY_OPENED",
            ))
        if market:
            watch = getattr(self, "_a197_postfill_watch", None)
            if not isinstance(watch, dict):
                watch = {}
                self._a197_postfill_watch = watch
            watch[bid] = tick
        self._a197_postfill_acts = int(getattr(self, "_a197_postfill_acts", 0) or 0) + 1
        self._a197_postfill_market = int(getattr(self, "_a197_postfill_market", 0) or 0) + int(market)
        self._a197_postfill_fallback_cancels = int(
            getattr(self, "_a197_postfill_fallback_cancels", 0) or 0
        ) + int(bool(fallback))
        self._a197_postfill_limits_stripped = int(
            getattr(self, "_a197_postfill_limits_stripped", 0) or 0
        ) + int(stripped)
        try:
            self._emit(
                "A197_POSTFILL_PROTECT", force=True, tick=tick, book=bid,
                fill_tick=fill_tick, mark_bps=None if mark is None else round(float(mark), 3),
                entry_quotes=len(entry_ids), entry_quotes_cancelled_by_exit=len(covered),
                market_queued=int(market), fallback_cancel=int(bool(fallback)),
                limits_stripped=int(stripped), managed_instructions=int(n),
                a197_postfill_protection_version=A197_POSTFILL_PROTECTION_VERSION,
            )
        except Exception:
            pass
        return max(0, n - int(stripped)) + int(fallback)

    # ---- A1.9.8: ABSOLUTE_PROTECTION keeps its taker ------------------------------
    def _a198_enabled(self) -> bool:
        return bool(getattr(self, "research_a198_absolute_taker_authority", True))

    def _a198_note_restore(self, book_id: int, captured: dict) -> None:
        """Count and log one ABSOLUTE taker restored over a loss-recovery maker exit."""
        bid = int(book_id)
        arm = str(captured.get("a198_arm") or "")
        replaced = captured.get("a198_replaced")
        postfill = getattr(self, "_a197_postfill_book", None) == bid
        self._a198_restores = int(getattr(self, "_a198_restores", 0) or 0) + 1
        if arm == ARM_RECOVERY_MAKER:
            self._a198_restored_recovery = int(getattr(self, "_a198_restored_recovery", 0) or 0) + 1
        elif arm == ARM_RELATIVE_VETO:
            self._a198_restored_relative = int(getattr(self, "_a198_restored_relative", 0) or 0) + 1
        if postfill:
            self._a198_restored_postfill = int(getattr(self, "_a198_restored_postfill", 0) or 0) + 1
        self._emit(
            "A198_ABSOLUTE_TAKER", force=True,
            tick=int(getattr(self, "_tick", 0) or 0), book=bid, arm=arm,
            replaced_reason=str(getattr(replaced, "reason", "") or ""),
            position_risk_bps=float(captured.get("position_risk_bps", 0.0) or 0.0),
            maker_net_bps=float(captured.get("maker_net_bps", 0.0) or 0.0),
            taker_net_bps=float(captured.get("taker_net_bps", 0.0) or 0.0),
            inventory_age=float(captured.get("inventory_age", 0.0) or 0.0),
            failed_exit_count=int(captured.get("failed_exit_count", 0) or 0),
            postfill=int(postfill),
            a198_absolute_authority_version=A198_ABSOLUTE_AUTHORITY_VERSION,
        )

    # ---- A1.9.9: position risk state machine --------------------------------------
    def _a199_exit_pending_enabled(self) -> bool:
        return bool(getattr(self, "research_a199_exit_pending_authority", True))

    def _a199_pending_table(self) -> dict:
        table = getattr(self, "_a199_exit_pending", None)
        if not isinstance(table, dict):
            table = {}
            self._a199_exit_pending = table
        return table

    def _a199_authorize_exit(
        self, book_id: int, *, base_decision, decision, exit_kwargs: dict,
        inventory, book, position_risk_bps: float,
    ):
        """Advance the book's position risk state, then let it authorize the exit."""
        bid = int(book_id)
        tick = int(getattr(self, "_tick", 0) or 0)
        net_base = float(getattr(inventory, "net_base", 0.0) or 0.0)
        pending, transitions = step_exit_pending(
            self._a199_pending_table(), bid,
            base_band=str(getattr(base_decision, "risk_band", "") or ""),
            net_base=net_base, vwap_entry=getattr(inventory, "vwap_entry", None),
            tick=tick, eps=float(self._execution_flat_epsilon()),
        )
        for transition, state in transitions:
            self._a199_note_transition(
                bid, transition, state, position_risk_bps=position_risk_bps, net_base=net_base,
            )
        inventory_qty = exit_kwargs.get("inventory_qty", 0.0)
        # v6.0.0: a short lot exits at the minimum order, so the risk state sees an executable size.
        inventory_qty = self._v600_executable_qty(inventory_qty, exit_kwargs.get("min_order", 0.25))
        final, rule, arm = authorize_exit(
            pending=pending, base_decision=base_decision, decision=decision,
            maker_net_bps=exit_kwargs.get("maker_net_bps", 0.0),
            taker_net_bps=exit_kwargs.get("taker_net_bps", 0.0),
            inventory_qty=inventory_qty,
            min_order=exit_kwargs.get("min_order", 0.25),
            taker_clip=exit_kwargs.get("taker_clip", 0.25),
            is_dust=bool(exit_kwargs.get("is_dust", False)),
            touch_two_sided=two_sided_touch(
                getattr(book, "bids", None), getattr(book, "asks", None),
            ),
            a198_enabled=self._a198_enabled(),
            allow_positive_maker=not bool(getattr(self, "research_a1991_pending_owns_book", True)),
        )
        if pending is not None and rule in (RULE_LOSS_MAKER, RULE_NOT_EXITING, RULE_POSITIVE_MAKER):
            pending.overrides += 1
        return final, rule, arm

    def _a199_note_transition(
        self, book_id: int, transition: str, state, *, position_risk_bps=None, net_base=None,
    ) -> None:
        """Count and log one ABSOLUTE exit state entering or ending."""
        if transition == ENTER:
            self._a199_pending_entered = int(getattr(self, "_a199_pending_entered", 0) or 0) + 1
        else:
            self._a199_pending_cleared = int(getattr(self, "_a199_pending_cleared", 0) or 0) + 1
        self._emit(
            "A199_EXIT_PENDING", force=True,
            tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
            transition=str(transition),
            since_tick=int(getattr(state, "since_tick", -1)) if state is not None else -1,
            evaluations=int(getattr(state, "evaluations", 0)) if state is not None else 0,
            overrides=int(getattr(state, "overrides", 0)) if state is not None else 0,
            position_risk_bps=None if position_risk_bps is None else float(position_risk_bps),
            net_base=None if net_base is None else float(net_base),
            a199_risk_state_version=A199_RISK_STATE_VERSION,
        )

    def _a199_note_override(self, book_id: int, captured: dict) -> None:
        """Count and log one exit the pending state refused: a maker exit, WAIT or PARK."""
        bid = int(book_id)
        rule = str(captured.get("a199_rule") or "")
        replaced = captured.get("a199_replaced")
        base = captured.get("base_decision")
        state = self._a199_pending_table().get(bid)
        if rule == RULE_LOSS_MAKER:
            self._a199_rule_loss_maker = int(getattr(self, "_a199_rule_loss_maker", 0) or 0) + 1
        elif rule == RULE_NOT_EXITING:
            self._a199_rule_not_exiting = int(getattr(self, "_a199_rule_not_exiting", 0) or 0) + 1
        elif rule == RULE_POSITIVE_MAKER:
            self._a1991_rule_positive_maker = int(getattr(self, "_a1991_rule_positive_maker", 0) or 0) + 1
        self._emit(
            "A199_EXIT_AUTHORITY", force=True,
            tick=int(getattr(self, "_tick", 0) or 0), book=bid, rule=rule,
            replaced_action=str(getattr(replaced, "action", "") or ""),
            replaced_reason=str(getattr(replaced, "reason", "") or ""),
            base_band=str(getattr(base, "risk_band", "") or ""),
            base_reason=str(getattr(base, "reason", "") or ""),
            position_risk_bps=float(captured.get("position_risk_bps", 0.0) or 0.0),
            maker_net_bps=float(captured.get("maker_net_bps", 0.0) or 0.0),
            taker_net_bps=float(captured.get("taker_net_bps", 0.0) or 0.0),
            inventory_qty=float(captured.get("inventory_qty", 0.0) or 0.0),
            inventory_age=float(captured.get("inventory_age", 0.0) or 0.0),
            failed_exit_count=int(captured.get("failed_exit_count", 0) or 0),
            valid_opposite_touch=int(bool(captured.get("valid_opposite_touch", True))),
            pending_since_tick=int(getattr(state, "since_tick", -1)) if state is not None else -1,
            pending_evaluations=int(getattr(state, "evaluations", 0)) if state is not None else 0,
            postfill=int(getattr(self, "_a197_postfill_book", None) == bid),
            a199_risk_state_version=A199_RISK_STATE_VERSION,
        )

    def _a199_note_own_fill(self, *, book_id: int, before: float, after: float) -> None:
        """A position that went flat or flipped ends its pending exit."""
        table = getattr(self, "_a199_exit_pending", None)
        if not isinstance(table, dict) or int(book_id) not in table:
            return
        eps = float(self._execution_flat_epsilon())
        flat = abs(float(after)) <= eps
        flipped = float(before) * float(after) < -(eps * eps)
        if not (flat or flipped):
            return
        state = table.pop(int(book_id), None)
        self._a199_note_transition(
            int(book_id), CLEAR_FLAT if flat else CLEAR_NEW_POSITION, state, net_base=float(after),
        )

    def _a199_note_exit_stalls(self, response) -> int:
        """Measurement: pending exits with an executable quantity that sent nothing this tick."""
        table = getattr(self, "_a199_exit_pending", None) or {}
        stalls = getattr(self, "_a199_exit_stalls", None)
        if not isinstance(stalls, dict):
            stalls = {}
            self._a199_exit_stalls = stalls
        if not table and not stalls:
            return 0
        tick = int(getattr(self, "_tick", 0) or 0)
        touched: set[int] = set()
        for instruction in getattr(response, "instructions", None) or ():
            raw = getattr(instruction, "bookId", getattr(instruction, "book_id", None))
            try:
                touched.add(int(raw))
            except (TypeError, ValueError):
                continue
        min_order = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        rows = []
        for bid in list(table):
            try:
                net = float(self._position_tracker_snapshot(int(bid)).net_qty)
            except Exception:
                net = 0.0
            not_executable = abs(net) + 1e-12 < min_order and self._v600_counts_as_dust(
                int(bid), abs(net), eps=float(self._execution_flat_epsilon()), min_order=min_order,
            )
            if not_executable:
                rows.append(end_exit_stall(stalls, bid, tick=tick, ended_by="NOT_EXECUTABLE"))
            else:
                rows.append(note_exit_stall(stalls, bid, tick=tick, has_instruction=int(bid) in touched))
        for bid in [b for b in stalls if b not in table]:
            rows.append(end_exit_stall(stalls, bid, tick=tick, ended_by="CLEARED"))
        emitted = 0
        for row in rows:
            if not row:
                continue
            emitted += 1
            self._a199_stall_rows = int(getattr(self, "_a199_stall_rows", 0) or 0) + 1
            self._a199_stall_max_ticks = max(
                int(getattr(self, "_a199_stall_max_ticks", 0) or 0), int(row["ticks"]),
            )
            self._emit(
                "A199_EXIT_STALL", force=True, tick=tick,
                a199_risk_state_version=A199_RISK_STATE_VERSION, **row,
            )
        return emitted

    # ---- A1.9.9: session epoch controller ----------------------------------------
    def _a199_epoch_enabled(self) -> bool:
        return bool(getattr(self, "research_a199_epoch_resync", True))

    def _a199_observe_epoch(self, state):
        """Detect a clock rewind on the state about to be ingested, and open a resync."""
        try:
            ts = int(getattr(state, "timestamp", 0) or 0)
        except (TypeError, ValueError):
            return None
        try:
            sim_id = extract_simulation_id(state)
        except Exception:
            sim_id = None
        event = detect_rewind(
            last_ts=getattr(self, "_a199_last_state_ts", None), ts=ts,
            last_sim_id=getattr(self, "_a199_last_sim_id", None), sim_id=sim_id,
        )
        if ts > 0:
            self._a199_last_state_ts = ts
        if sim_id:
            self._a199_last_sim_id = sim_id
        if event is None or not self._a199_epoch_enabled():
            return event
        tick = int(getattr(self, "_tick", 0) or 0)
        dropped = clear_epoch_registries(self)
        ledger_rows = 0
        try:
            ledger = self._a19_ledger_ref()
            if ledger is not None:
                ledger_rows = int(ledger.reset() or 0)
        except Exception:
            ledger_rows = 0
        self._a197_postfill_book = None
        rows = int(sum(dropped.values())) + ledger_rows
        self._a199_epoch_rewinds = int(getattr(self, "_a199_epoch_rewinds", 0) or 0) + 1
        self._a199_epoch_registry_rows_cleared = int(
            getattr(self, "_a199_epoch_registry_rows_cleared", 0) or 0
        ) + rows
        # `Strategy1.respond` increments _tick first, so this state is tick + 1.
        start = tick + 1
        self._a199_resync = {
            "since_tick": start,
            "min_until_tick": start + int(A199_RESYNC_MIN_TICKS),
            "max_until_tick": start + int(A199_RESYNC_MAX_TICKS),
            "old_ts": int(event["old_ts"]),
            "new_ts": int(event["new_ts"]),
            "reseeds": 0,
            "entries_blocked": 0,
            "placements_stripped": 0,
        }
        self._emit(
            "A199_EPOCH_REWIND", force=True, tick=tick, state_tick=start,
            old_ts=int(event["old_ts"]), new_ts=int(event["new_ts"]),
            rewind_s=float(event["rewind_s"]), simulation_id=sim_id,
            registries={name: n for name, n in dropped.items() if n},
            exit_ledger_rows=ledger_rows, registry_rows_cleared=rows,
            pending_exit_books=len(self._a199_pending_table()),
            resync_min_ticks=int(A199_RESYNC_MIN_TICKS),
            resync_max_ticks=int(A199_RESYNC_MAX_TICKS),
            a199_session_epoch_version=A199_SESSION_EPOCH_VERSION,
        )
        return event

    def _a199_resync_active(self) -> bool:
        return bool(getattr(self, "_a199_resync", None))

    def _a199_entry_blocked(self, book_id) -> bool:
        """No new exposure during a resync, nor on a book still waiting to be reseeded."""
        resync = getattr(self, "_a199_resync", None)
        deferred = getattr(self, "_a199_deferred", None) or {}
        if not resync and int(book_id) not in deferred:
            return False
        self._a199_epoch_entries_blocked = int(getattr(self, "_a199_epoch_entries_blocked", 0) or 0) + 1
        if resync:
            resync["entries_blocked"] = int(resync.get("entries_blocked", 0) or 0) + 1
        return True

    def _a199_service_resync(self, state) -> int:
        """Rebuild every diverged book from venue truth while a resync is open."""
        resync = getattr(self, "_a199_resync", None) or {}
        deferred = getattr(self, "_a199_deferred", None)
        if not isinstance(deferred, dict):
            deferred = {}
            self._a199_deferred = deferred
        if not resync and not deferred:
            return 0
        books = getattr(state, "books", None) or {}
        if not books:
            return 0
        if resync:
            scope = books
        else:
            scope = {bid: books[bid] for bid in list(deferred) if bid in books}
        # `Strategy1.respond` has not incremented _tick yet: this state is tick + 1.
        tick = int(getattr(self, "_tick", 0) or 0) + 1
        venue = self._a195_venue_net_by_book(scope)
        mids = self._a195_mid_by_book(scope)
        min_order = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        decimals = self._a196_volume_decimals(state)
        ledger = getattr(self, "_a196_legacy_dust_ledger", None) or {}
        residue = getattr(self, "_a1961_fee_residue", None) or {}
        pending_seed = getattr(self, "_a1961_pending_seed", None) or {}
        v502_residue = getattr(self, "_v502_residue", None) or {}
        clip_tolerance = self._v502_clip_tolerance(state)
        changed = 0
        for book_id in sorted(venue):
            try:
                tracker = float(self._position_tracker_snapshot(int(book_id)).net_qty)
            except Exception:
                tracker = 0.0
            plan = plan_book_reseed(
                book_id=book_id, venue_net=venue[book_id], tracker_net=tracker,
                ledger=ledger.get(book_id, 0.0),
                fee_residue=residue.get(book_id, 0.0) + v502_residue.get(book_id, 0.0),
                pending=(pending_seed.get(book_id) or {}).get("net", 0.0),
                min_order=min_order, volume_decimals=decimals, mid=mids.get(book_id),
                ledger_enabled=self._a196_ledger_enabled(),
                clip_tolerance=clip_tolerance,
            )
            if plan.action == RESEED_KEEP:
                deferred.pop(int(book_id), None)
                continue
            if self._a199_apply_reseed(plan, tick=tick):
                changed += 1
        if resync:
            resync["reseeds"] = int(resync.get("reseeds", 0) or 0) + changed
            clean = changed == 0
            if (clean and tick >= int(resync["min_until_tick"])) or tick >= int(resync["max_until_tick"]):
                self._a199_close_resync(tick, clean=clean)
        return changed

    def _a199_apply_reseed(self, plan, *, tick: int) -> bool:
        """Write one book's reseed into the tracker and the ledger.  False when nothing changed."""
        bid = int(plan.book_id)
        deferred = getattr(self, "_a199_deferred", None)
        if not isinstance(deferred, dict):
            deferred = {}
            self._a199_deferred = deferred
        waiting = deferred.get(bid)
        if (
            plan.action == RESEED_DEFER and waiting is not None
            and abs(float(waiting.get("target", 0.0)) - float(plan.target)) <= 1e-12
            and abs(float(plan.tracker_before)) <= 1e-12
        ):
            return False
        positions = self._open_positions[bid]
        positions["longs"].clear()
        positions["shorts"].clear()
        runtime = clear_book_runtime(self, bid)
        folded = None
        pending_seed = getattr(self, "_a1961_pending_seed", None)
        if isinstance(pending_seed, dict):
            folded = pending_seed.pop(bid, None)
        state = self._a199_pending_table().pop(bid, None)
        if state is not None:
            self._a199_note_transition(bid, CLEAR_EPOCH, state, net_base=float(plan.venue_net))
        stalls = getattr(self, "_a199_exit_stalls", None)
        if isinstance(stalls, dict):
            stalls.pop(bid, None)
        if plan.action == RESEED_REAL:
            side = "longs" if float(plan.target) > 0.0 else "shorts"
            # v6.1: the saved lots, when they still add up to the venue's position.
            restored = self._v61_restored_side(bid, side, abs(float(plan.target)))
            if restored:
                positions[side].extend(restored)
            else:
                positions[side].append((int(tick), abs(float(plan.target)), float(plan.price), 0.0))
            deferred.pop(bid, None)
        elif plan.action == RESEED_CLIP:
            # v5.0.2 F2: one clip of exactly min_order; the venue's shortfall joins the residue ledger.
            side = "longs" if float(plan.tracker_after) > 0.0 else "shorts"
            positions[side].append((int(tick), abs(float(plan.tracker_after)), float(plan.price), 0.0))
            self._v502_add_residue(bid, float(plan.ledger_delta))
            self._v502_count("clip_reseed")
            deferred.pop(bid, None)
        elif plan.action == RESEED_DUST:
            ledger = getattr(self, "_a196_legacy_dust_ledger", None)
            if not isinstance(ledger, dict):
                ledger = {}
                self._a196_legacy_dust_ledger = ledger
            ledger[bid] = float(ledger.get(bid, 0.0) or 0.0) + float(plan.ledger_delta)
            deferred.pop(bid, None)
        elif plan.action == RESEED_DEFER:
            since = int((waiting or {}).get("since_tick", tick))
            deferred[bid] = {
                "since_tick": since, "target": float(plan.target), "venue_net": float(plan.venue_net),
            }
        else:
            deferred.pop(bid, None)
        self._a199_epoch_reseeds = int(getattr(self, "_a199_epoch_reseeds", 0) or 0) + 1
        payload = plan.as_log()
        payload.update(
            runtime_cleared=runtime, pending_exit_cleared=int(state is not None),
            pending_seed_folded=None if not folded else float((folded or {}).get("net", 0.0) or 0.0),
            in_resync=int(bool(getattr(self, "_a199_resync", None))),
        )
        self._emit("A199_EPOCH_RESEED", force=True, tick=int(tick), **payload)
        return True

    def _a199_close_resync(self, tick: int, *, clean: bool) -> None:
        resync = getattr(self, "_a199_resync", None) or {}
        self._a199_resync = {}
        self._a199_epoch_resyncs_closed = int(getattr(self, "_a199_epoch_resyncs_closed", 0) or 0) + 1
        since = int(resync.get("since_tick", tick) or tick)
        self._emit(
            "A199_EPOCH_RESUME", force=True, tick=int(tick), since_tick=since,
            resync_ticks=max(0, int(tick) - since + 1),
            reseeds=int(resync.get("reseeds", 0) or 0), clean=int(bool(clean)),
            entries_blocked=int(resync.get("entries_blocked", 0) or 0),
            placements_stripped=int(resync.get("placements_stripped", 0) or 0),
            deferred_books=sorted(int(b) for b in (getattr(self, "_a199_deferred", None) or {})),
            a199_session_epoch_version=A199_SESSION_EPOCH_VERSION,
        )

    def _a199_strip_resync_exposure(self, response) -> int:
        """The invariant: nothing opens or adds exposure during a resync, whatever built it."""
        resync = getattr(self, "_a199_resync", None) or {}
        deferred = getattr(self, "_a199_deferred", None) or {}
        if not resync and not deferred:
            return 0
        instructions = getattr(response, "instructions", None)
        if not isinstance(instructions, list) or not instructions:
            return 0
        nets: dict[int, float] = {}
        for instruction in instructions:
            raw = getattr(instruction, "bookId", getattr(instruction, "book_id", None))
            try:
                bid = int(raw)
            except (TypeError, ValueError):
                continue
            if bid not in nets:
                try:
                    nets[bid] = float(self._position_tracker_snapshot(bid).net_qty)
                except Exception:
                    nets[bid] = 0.0
        removed = strip_exposure_increasing(
            instructions, net_by_book=nets, deferred=list(deferred),
            eps=float(self._execution_flat_epsilon()),
            only_books=None if resync else list(deferred),
        )
        if not removed:
            return 0
        self._a199_epoch_placements_stripped = int(
            getattr(self, "_a199_epoch_placements_stripped", 0) or 0
        ) + len(removed)
        if resync:
            resync["placements_stripped"] = int(resync.get("placements_stripped", 0) or 0) + len(removed)
        self._emit(
            "A199_EPOCH_EXPOSURE_STRIP", force=True, tick=int(getattr(self, "_tick", 0) or 0),
            removed=len(removed), books=sorted({book for book, _ in removed}),
            market_orders=sum(1 for _, kind in removed if kind == "PLACE_ORDER_MARKET"),
            in_resync=int(bool(resync)),
            a199_session_epoch_version=A199_SESSION_EPOCH_VERSION,
        )
        return len(removed)

    def _a1961_seed_quote_guard_enabled(self) -> bool:
        return bool(getattr(self, "research_a1961_seed_quote_guard", True))

    def _a1961_fee_residue_enabled(self) -> bool:
        return bool(getattr(self, "research_a1961_fee_residue_ledger", True))

    def _a1961_pending_abs(self) -> float:
        """Inherited lots still waiting for a believable quote.  Real exposure."""
        pending = getattr(self, "_a1961_pending_seed", None) or {}
        return float(sum(abs(float((row or {}).get("net", 0.0) or 0.0)) for row in pending.values()))

    def _a1961_fee_residue_abs(self) -> float:
        residue = getattr(self, "_a1961_fee_residue", None) or {}
        return float(sum(abs(float(value or 0.0)) for value in residue.values()))

    def _a1961_note_base_decimals(self, state) -> None:
        """Cache the venue's BASE increment; the fee round-up depends on it."""
        cfg = getattr(state, "config", None)
        value = getattr(cfg, "baseDecimals", None) if cfg is not None else None
        try:
            if value is not None:
                self._a1961_base_decimals = max(0, int(value))
        except (TypeError, ValueError):
            pass

    def _a1961_note_fee_residue(self, event) -> int:
        """Mirror the BASE the venue took for this fill's fee.  Returns units charged."""
        if not self._a1961_fee_residue_enabled():
            return 0
        uid = getattr(self, "uid", None)
        is_taker = getattr(event, "takerAgentId", None) == uid
        is_maker = getattr(event, "makerAgentId", None) == uid
        if is_taker == is_maker:
            return 0
        try:
            side = int(getattr(event, "side", -1))
            book_id = int(getattr(event, "bookId"))
        except (TypeError, ValueError):
            return 0
        if not ((is_taker and side == 0) or (is_maker and side == 1)):
            return 0
        decimals = getattr(self, "_a1961_base_decimals", None)
        if decimals is None:
            # Never guess the increment: the round-up IS the effect, and it is
            # taken at the venue's BASE precision, not the order-volume one.
            self._a1961_fee_residue_skipped = int(
                getattr(self, "_a1961_fee_residue_skipped", 0) or 0
            ) + 1
            return 0
        units = base_fee_units(
            agent_buy=True,
            fee=getattr(event, "takerFee" if is_taker else "makerFee", None),
            price=getattr(event, "price", None),
            base_decimals=decimals,
        )
        if units <= 0:
            return 0
        residue = getattr(self, "_a1961_fee_residue", None)
        if not isinstance(residue, dict):
            residue = {}
            self._a1961_fee_residue = residue
        apply_fee_residue(residue, book_id=book_id, units=units, base_decimals=decimals)
        self._a1961_fee_residue_units = int(getattr(self, "_a1961_fee_residue_units", 0) or 0) + int(units)
        self._a1961_fee_residue_fills = int(getattr(self, "_a1961_fee_residue_fills", 0) or 0) + 1
        return int(units)

    def _a1961_service_pending_seed(self, state) -> int:
        """Seed deferred inherited lots once their quote has been a market long enough.

        Never ages positions: reads the pure tracker snapshot, like the seed.
        """
        pending = getattr(self, "_a1961_pending_seed", None)
        if not pending:
            return 0
        books = getattr(state, "books", None) or {}
        eps = float(self._execution_flat_epsilon())
        min_order = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        tick = int(getattr(self, "_tick", 0) or 0)
        decimals = self._a196_volume_decimals(state)
        seeded = 0
        for book_id in list(pending):
            row = pending.get(book_id) or {}
            try:
                book = books.get(book_id)
            except Exception:
                book = None
            touch = None
            if book is not None:
                try:
                    touch = valid_touch(getattr(book, "bids", None), getattr(book, "asks", None))
                except Exception:
                    touch = None
            venue_net = self._a195_venue_net_by_book({book_id: None}).get(book_id)
            try:
                local = float(self._position_tracker_snapshot(int(book_id)).net_qty)
            except Exception:
                local = 0.0
            step = pending_seed_step(
                streak=row.get("streak", 0), touch=touch, venue_net=venue_net,
                local_net=local, eps=eps,
            )
            row["streak"] = int(step.streak)
            row["last_reason"] = step.reason
            if step.action not in (SEED_NOW, SEED_DROP):
                continue
            pending.pop(book_id, None)
            since = int(row.get("since_tick", tick) or 0)
            outcome: dict[str, Any] = {
                "book": int(book_id), "action": step.action, "reason": step.reason,
                "waited_ticks": max(0, tick - since),
                "pending_net": float(row.get("net", 0.0) or 0.0),
            }
            if step.action == SEED_DROP:
                self._a1961_pending_dropped = int(getattr(self, "_a1961_pending_dropped", 0) or 0) + 1
            else:
                net = float(venue_net)
                if self._a196_grid_snap_enabled():
                    net = snap_quantity(net, decimals)
                outcome.update(net_base=net, mid=float(step.mid), bid=float(touch[0]), ask=float(touch[1]))
                if abs(net) >= min_order or not self._a196_ledger_enabled():
                    pos = self._open_positions[int(book_id)]
                    pos["longs" if net > 0 else "shorts"].append((tick, abs(net), float(step.mid), 0.0))
                    if abs(net) >= min_order:
                        inherited = getattr(self, "_a196_inherited_real", None)
                        if not isinstance(inherited, dict):
                            inherited = {}
                            self._a196_inherited_real = inherited
                        inherited[int(book_id)] = net
                        if abs(net) + 1e-12 < 2.0 * min_order:
                            self._v600_note_inherited_clip(int(book_id), abs(net))
                    outcome["routed"] = "TRACKER"
                else:
                    ledger = getattr(self, "_a196_legacy_dust_ledger", None)
                    if not isinstance(ledger, dict):
                        ledger = {}
                        self._a196_legacy_dust_ledger = ledger
                    ledger[int(book_id)] = net
                    self._a195_seed_legacy_ceiling_bonus = float(
                        getattr(self, "_a195_seed_legacy_ceiling_bonus", 0.0) or 0.0
                    ) + abs(net)
                    outcome["routed"] = "LEDGER"
                self._a1961_pending_resolved = int(getattr(self, "_a1961_pending_resolved", 0) or 0) + 1
                self._a1961_pending_seeded_abs = float(
                    getattr(self, "_a1961_pending_seeded_abs", 0.0) or 0.0
                ) + abs(net)
                seeded += 1
            try:
                self._emit("A1961_PENDING_SEED", force=True, tick=tick, **outcome)
            except Exception:
                pass
        return seeded

    def _a1961_note_taker_decision(self, *, book_id: int, tick: int, taker_net_bps: float, reason: str) -> None:
        cache = getattr(self, "_a1961_taker_decision", None)
        if not isinstance(cache, dict):
            cache = {}
            self._a1961_taker_decision = cache
        cache[int(book_id)] = {
            "tick": int(tick), "taker_net_bps": float(taker_net_bps), "reason": str(reason),
        }

    def _a1961_emit_taker_outcome(self, *, book_id: int, net_bps: float) -> None:
        """Judge a completed taker round trip against its own decision-time estimate."""
        cache = getattr(self, "_a1961_taker_decision", None)
        decision = cache.pop(int(book_id), None) if isinstance(cache, dict) else None
        tick = int(getattr(self, "_tick", 0) or 0)
        outcome = taker_outcome(book=int(book_id), tick=tick, realized_net_bps=float(net_bps), decision=decision)
        self._a1961_taker_outcomes = int(getattr(self, "_a1961_taker_outcomes", 0) or 0) + 1
        if outcome.decision_net_bps is None:
            self._a1961_taker_unmatched = int(getattr(self, "_a1961_taker_unmatched", 0) or 0) + 1
        else:
            if outcome.slippage_breach:
                self._a1961_slippage_breaches = int(getattr(self, "_a1961_slippage_breaches", 0) or 0) + 1
            if outcome.late_trigger:
                self._a1961_late_triggers = int(getattr(self, "_a1961_late_triggers", 0) or 0) + 1
            self._a1961_worst_slippage_bps = min(
                float(getattr(self, "_a1961_worst_slippage_bps", 0.0) or 0.0),
                float(outcome.slippage_bps),
            )
        self._emit("A1961_TAKER_OUTCOME", force=True, tick=tick, **outcome.as_log())

    def _a195_emit_dust_capacity(
        self, *, tick: int, total_abs: float, dust_abs: float, min_order: float,
        max_abs: float, slots_before: int, slots_after: int,
    ) -> None:
        if not self._a195_dust_capacity_enabled():
            return
        try:
            payload = dust_capacity_report(
                total_abs=total_abs, dust_abs=dust_abs, min_order=min_order,
                max_abs=max_abs,
                max_clips=float(getattr(
                    self, "research_a195_dust_class_max_clips",
                    A195_DUST_CLASS_MAX_CLIPS,
                )),
                legacy_bonus_abs=self._a195_legacy_ceiling_bonus(),
            )
        except Exception:
            return
        recovered = max(0, int(slots_after) - int(slots_before))
        payload["slots_without_class"] = int(slots_before)
        payload["slots_with_class"] = int(slots_after)
        payload["slots_recovered"] = int(recovered)
        self._a195_dust_capacity_emits = int(
            getattr(self, "_a195_dust_capacity_emits", 0) or 0
        ) + 1
        self._a195_dust_capacity_slots_recovered = int(
            getattr(self, "_a195_dust_capacity_slots_recovered", 0) or 0
        ) + recovered
        self._a195_dust_capacity_max_exempt_abs = max(
            float(getattr(self, "_a195_dust_capacity_max_exempt_abs", 0.0) or 0.0),
            float(payload.get("dust_class_exempt_abs", 0.0) or 0.0),
        )
        if float(payload.get("dust_class_overflow_abs", 0.0) or 0.0) > 0.0:
            self._a195_dust_capacity_overflow_ticks = int(
                getattr(self, "_a195_dust_capacity_overflow_ticks", 0) or 0
            ) + 1
        self._a195_dust_capacity_last = payload
        self._emit("A195_DUST_CAPACITY", force=True, tick=int(tick), **payload)

    # ---- A1.9.5 F8: bind the declared taker loss floor ------------------
    def _a195_taker_floor_enabled(self) -> bool:
        return bool(getattr(self, "research_a195_taker_floor_enforce", True))

    def _a195_declared_floor_bps(self, book_id: int) -> float:
        """The loss floor the exit decision declared for this book, this tick.

        Falls back to the configured default rather than to zero: zero is the
        wire value for "unbounded", so a missing record must not disarm the
        bound it exists to apply.
        """
        default = float(getattr(
            self, "research_a195_taker_floor_bps", A195_DEFAULT_TAKER_FLOOR_BPS,
        ))
        row = (getattr(self, "_direct_exit_authority_last", {}) or {}).get(int(book_id))
        if not isinstance(row, dict):
            return default
        if int(row.get("tick", -1) or -1) != int(getattr(self, "_tick", 0) or 0):
            # A stale row is a different decision; do not bound this order by it.
            return default
        if "allowed_loss_floor_bps" not in row:
            return default
        return float(row.get("allowed_loss_floor_bps") or 0.0)

    def _a195_bind_taker_slippage(
        self, response: Any, book_id: int, first_new: int,
    ) -> dict[str, Any]:
        """Attach `max_slippage` to market orders the base just queued.

        The frozen base builds the order; this only tightens it.  Rewriting the
        queued instruction rather than reimplementing `_execute_aggressive_close`
        keeps the fee gate, volume cap, balance checks and cancel-before-taker
        sequencing exactly as the base defines them.
        """
        applied: dict[str, Any] = {}
        instructions = list(getattr(response, "instructions", None) or ())
        floor_bps = self._a195_declared_floor_bps(book_id)
        fraction = slippage_fraction_for_floor(
            floor_bps,
            fallback_bps=float(getattr(
                self, "research_a195_taker_floor_bps", A195_DEFAULT_TAKER_FLOOR_BPS,
            )),
        )
        row = (getattr(self, "_direct_exit_authority_last", {}) or {}).get(int(book_id))
        for instruction in instructions[max(0, int(first_new)):]:
            if str(getattr(instruction, "type", "")) != "PLACE_ORDER_MARKET":
                continue
            if int(getattr(instruction, "bookId", -1) or -1) != int(book_id):
                continue
            if getattr(instruction, "max_slippage", None) is not None:
                continue
            instruction.max_slippage = fraction
            applied = taker_bound_report(
                book_id=book_id, floor_bps=floor_bps,
                authority=(row or {}).get("taker_authority"),
                trigger=(row or {}).get("reason"),
                fallback_bps=float(getattr(
                    self, "research_a195_taker_floor_bps",
                    A195_DEFAULT_TAKER_FLOOR_BPS,
                )),
            )
            self._a195_taker_bound_applied = int(
                getattr(self, "_a195_taker_bound_applied", 0) or 0
            ) + 1
            if applied.get("floor_was_zero"):
                self._a195_taker_bound_zero_floor = int(
                    getattr(self, "_a195_taker_bound_zero_floor", 0) or 0
                ) + 1
            prior = float(getattr(self, "_a195_taker_bound_min_fraction", 0.0) or 0.0)
            self._a195_taker_bound_min_fraction = (
                fraction if prior <= 0.0 else min(prior, fraction)
            )
        if applied:
            self._a195_taker_bound_last = applied
            try:
                self._emit(
                    "A195_TAKER_BOUND", force=True,
                    tick=int(getattr(self, "_tick", 0) or 0), **applied,
                )
            except Exception:
                pass
        return applied

    def _execute_aggressive_close(
        self,
        response: Any,
        book_id: int,
        book: Any,
        qty: float,
        long_pos: bool,
    ) -> bool:
        """A1.9.5 F8: the frozen base sends this market order unbounded.

        `allowed_loss_floor_bps` has been computed on every risk-authorized
        taker exit since A1.6 and read by nothing.  Measured over 4,233 ticks,
        100% of ABSOLUTE_PROTECTION_REDUCE exits breached their own declared
        -25 bps floor, worst -213.9 bps, and that trigger alone carried 97.2%
        of the run's cubic downside.
        """
        # v6.1: a market close may leave only if the validator realizes >= 0 on it (FIFO, both
        # legs' fees at the close).  On mainnet 148 of 149 ABSOLUTE takers realized a loss while
        # a fee-inclusive positive maker close was available on 128 of them.
        if self._v61_on():
            try:
                ok, detail = self._v61_taker_verdict(int(book_id), book, float(qty), bool(long_pos))
            except Exception:
                self._v61_errors = int(getattr(self, "_v61_errors", 0) or 0) + 1
                ok, detail = True, {}
            if not ok:
                self._v61_note_refusal(int(book_id), float(qty), bool(long_pos), detail)
                return False
        before = len(getattr(response, "instructions", None) or ())
        placed = super()._execute_aggressive_close(
            response, book_id, book, qty, long_pos,
        )
        if not placed or not self._a195_taker_floor_enabled():
            return placed
        try:
            self._a195_bind_taker_slippage(response, int(book_id), before)
        except Exception:
            # A bound that cannot be attached must not cancel the exit; the
            # pre-A1.9.5 behaviour (unbounded) is the fail-open path here.
            pass
        return placed

    # ---- A1.9.5 step 3 (F2 behaviour): venue truth seeds the tracker ----
    def _a195_inventory_truth_enabled(self) -> bool:
        return bool(getattr(self, "research_a195_inventory_truth_enabled", True))

    def _a195_mid_by_book(self, books: Any) -> dict[int, float]:
        """Current mid per book, used only to price seeded lots at zero PnL."""
        out: dict[int, float] = {}
        for raw_id, book in (books or {}).items():
            try:
                book_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            if self._a1961_seed_quote_guard_enabled():
                # A1.9.6.1: a crossed or out-of-order touch is not a price.  On
                # tick 1 of the first A1.9.6 run book 99 read bid 411.37 / ask
                # 285.28, and the lot seeded at their midpoint carried +1,805 bps
                # of profit that never existed for the rest of the run.
                try:
                    mid = touch_mid(getattr(book, "bids", None), getattr(book, "asks", None))
                except Exception:
                    mid = None
                if mid is not None:
                    out[book_id] = mid
                continue
            try:
                bid = float(book.bids[0].price)
                ask = float(book.asks[0].price)
            except (TypeError, ValueError, IndexError, AttributeError):
                continue
            if bid > 0.0 and ask > 0.0:
                out[book_id] = (bid + ask) / 2.0
        return out

    def _a195_venue_net_by_book(self, books: Any) -> dict[int, float]:
        """Signed venue net base per book: `total - initial`, never `total`."""
        out: dict[int, float] = {}
        accounts = getattr(self, "accounts", None)
        if accounts is None:
            return out
        for raw_id in (books or {}):
            try:
                book_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            try:
                account = accounts[book_id]
            except Exception:
                continue
            net, _components, reason = venue_net_base(account)
            if reason is None and net is not None:
                out[book_id] = float(net)
        return out

    def _a195_seed_inventory_from_venue(self, state) -> None:
        """One-shot import of venue inventory into the local position tracker.

        Runs before the frozen chain so every downstream consumer -- exit
        controller, capacity accounting, ownership -- sees the true book on the
        first tick it could possibly act on.
        """
        if self._a195_seed_done or not self._a195_inventory_truth_enabled():
            return
        books = getattr(state, "books", None) or {}
        if not books:
            return
        # Mark done before any mutation: a partial seed must not be retried on
        # the next tick, which would double-count whatever landed first time.
        self._a195_seed_done = True
        venue = self._a195_venue_net_by_book(books)
        if not venue:
            return
        min_order = float(
            getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25
        )
        plan = build_seed_plan(
            venue_net_by_book=venue,
            local_net_by_book=self._a195_local_base_by_book(books),
            mid_by_book=self._a195_mid_by_book(books),
            min_order=min_order,
            tick=int(getattr(self, "_tick", 0) or 0),
            max_books=int(getattr(self, "research_a195_max_seed_books", A195_MAX_SEED_BOOKS)),
            max_abs_base=float(getattr(
                self, "research_a195_max_seed_abs_base", A195_MAX_SEED_ABS_BASE,
            )),
        )
        # A1.9.6 F9/F11.  REAL lots to the tracker, legacy dust to the ledger,
        # every quantity on the venue grid.  A1.9.5 appended plan.lots whole:
        # 109 dust books then read non-FLAT, the entry builder skipped every
        # one, and round trips fell from 442 to 10.  Ledger off == A1.9.5.
        split = split_seed_plan(
            plan,
            volume_decimals=self._a196_volume_decimals(state),
            min_order=min_order,
            ledger_enabled=self._a196_ledger_enabled(),
            grid_snap=self._a196_grid_snap_enabled(),
            clip_tolerance=self._v502_clip_tolerance(state),
        )
        seeded = 0
        inherited: dict[int, float] = {}
        restored_books: set[int] = set()
        for lot in split.tracker_lots:
            try:
                pos = self._open_positions[int(lot.book_id)]
                side = "longs" if lot.is_long else "shorts"
                # v6.2.2: the session's own lots when they still add up to the venue's position.
                seed_lots, restored = self._v622_seed_lots(int(lot.book_id), side, lot)
                pos[side].extend(seed_lots)
                if restored:
                    restored_books.add(int(lot.book_id))
                seeded += 1
            except Exception:
                continue
            if lot.residue_class == SEED_REAL and int(lot.book_id) not in restored_books:
                inherited[int(lot.book_id)] = float(lot.net_base)
        self._a196_legacy_dust_ledger = dict(split.ledger)
        # v5.0.2 F2: an inherited clip a unit or two short is seeded whole; the shortfall is residue.
        for clip_book, clip_rest in sorted(split.clip_residue.items()):
            self._v502_add_residue(int(clip_book), float(clip_rest))
            self._v502_count("clip_seed")
            # v6.0.0: a rebuilt clip is priced at today's quote, so its real loss is invisible; park it
            # unless the operator asked for inherited lots to exit.
            self._v600_note_inherited_clip(int(clip_book), float(min_order))
        self._a196_inherited_real = inherited
        # v6.0.0: so is every single lot the venue still holds (the dry run on UID 125 seeded books 76
        # and 116 whole at -0.2716 and -0.2515, because buy fees are charged in base).
        for inherited_book, inherited_net in sorted(inherited.items()):
            if abs(float(inherited_net)) + 1e-12 < 2.0 * float(min_order):
                self._v600_note_inherited_clip(int(inherited_book), abs(float(inherited_net)))
        # A1.9.6.1: books the seed could not price.  Dust needs no price, so it
        # joins the ledger; a REAL lot needs a cost basis, so it waits for a quote
        # that is a market -- charged to exposure, covered by F10, meanwhile.
        route = None
        if self._a1961_seed_quote_guard_enabled() and plan.skipped_no_price:
            route = route_unpriced_books(
                books=plan.skipped_no_price,
                venue_net_by_book=venue,
                min_order=min_order,
                volume_decimals=self._a196_volume_decimals(state),
                ledger_enabled=self._a196_ledger_enabled(),
                grid_snap=self._a196_grid_snap_enabled(),
                remaining_books=int(getattr(
                    self, "research_a195_max_seed_books", A195_MAX_SEED_BOOKS,
                )) - len(plan.lots),
                remaining_abs=float(getattr(
                    self, "research_a195_max_seed_abs_base", A195_MAX_SEED_ABS_BASE,
                )) - float(plan.total_abs_base),
            )
            self._a196_legacy_dust_ledger.update(route.ledger)
            seed_tick = int(getattr(self, "_tick", 0) or 0)
            self._a1961_pending_seed = {
                int(book_id): {"net": float(net), "since_tick": seed_tick, "streak": 0}
                for book_id, net in route.pending.items()
            }
            self._a1961_seed_unpriced_books = len(plan.skipped_no_price)
        # F3 headroom for the legacy dust only, raised by exactly what was
        # imported -- into the ledger or, with the ledger off, the tracker.
        # Dust created after startup still competes for the original ceiling,
        # so the overflow signal that says "a purge is overdue" keeps working.
        self._a195_seed_legacy_ceiling_bonus = float(split.legacy_dust_abs)
        if route is not None:
            self._a195_seed_legacy_ceiling_bonus += float(route.ledger_abs)
        self._a195_seed_books = seeded
        self._a195_seed_real_books = int(plan.real_books)
        self._a195_seed_real_abs = float(plan.real_abs_base)
        self._a195_seed_dust_books = int(plan.dust_books)
        self._a195_seed_dust_abs = float(plan.dust_abs_base)
        payload = plan.as_log()
        payload["seeded_applied"] = int(seeded)
        payload["v622_restored_books"] = int(len(restored_books))
        payload["v622_seed_abs_bound"] = float(getattr(self, "research_a195_max_seed_abs_base", 0.0) or 0.0)
        payload["legacy_ceiling_bonus_abs"] = float(self._a195_seed_legacy_ceiling_bonus)
        self._a196_seed_last = split.as_log()
        payload.update(self._a196_seed_last)
        if route is not None:
            payload.update(route.as_log())
        self._a195_seed_last = payload
        try:
            self._emit(
                "A195_INVENTORY_SEED", force=True,
                tick=int(getattr(self, "_tick", 0) or 0), **payload,
            )
        except Exception:
            pass

    def _a195_cancel_orphan_orders(self, response: Any, state) -> None:
        """Cancel resting orders inherited from a previous process.

        The spec called this "shutdown cancel", but the only shutdown hook is
        an `atexit` handler with no response object and no live simulation, so
        nothing can be sent from it -- and a crash would skip it regardless.
        Cancelling on the way IN covers both, and is what actually protects the
        new session: after a restart these orders are in no identity registry,
        so the agent can neither reprice nor release them, and they can fill
        into inventory it has no record of requesting.
        """
        if self._a195_orphan_cancel_done:
            return
        if not bool(getattr(self, "research_a195_startup_orphan_cancel", True)):
            return
        self._a195_orphan_cancel_done = True
        accounts = getattr(self, "accounts", None)
        if accounts is None:
            return
        cancelled = books_touched = 0
        for raw_id in (getattr(state, "books", None) or {}):
            try:
                book_id = int(raw_id)
                resting = getattr(accounts[book_id], "orders", None) or []
            except Exception:
                continue
            order_ids = [
                getattr(order, "id", None) for order in resting
                if getattr(order, "id", None) is not None
            ]
            if not order_ids:
                continue
            try:
                if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
                    continue
                response.cancel_orders(book_id=book_id, order_ids=order_ids, delay=0)
                # A1.9.0.3 invariant: an unregistered cancel falls through to
                # EXPIRED and overstates exchange-side expiry.  These are ours.
                self._a19_note_exit_cancel(
                    int(book_id), order_ids, ABSENT_ORPHAN_CANCEL,
                )
            except Exception:
                continue
            cancelled += len(order_ids)
            books_touched += 1
        self._a195_orphan_orders_cancelled = cancelled
        self._a195_orphan_books = books_touched
        if cancelled:
            try:
                self._emit(
                    "A195_ORPHAN_CANCEL", force=True,
                    tick=int(getattr(self, "_tick", 0) or 0),
                    a195_inventory_truth_version=A195_INVENTORY_TRUTH_VERSION,
                    orders_cancelled=int(cancelled), books=int(books_touched),
                )
            except Exception:
                pass

    # ---- A1.9.5 step 4: breadth relief at the A1.7.4.5 boundary ---------
    def _a195_breadth_lane_enabled(self) -> bool:
        return bool(getattr(self, "research_a195_breadth_lane_enabled", True))

    def _a195_breadth_relief(
        self, *, book_id: int, observations_remaining: int,
        current_edge_bps: float, base_min_edge_bps: float,
        effective_min_edge_bps: float,
    ) -> float:
        """Effective entry floor for this book, after breadth relief.

        Returns the floor to use.  A1.9.3 put this authority on the
        NEGATIVE_CURRENT_EDGE reject, which two runs measured at zero events --
        one-away books are 100% eligible with no reject at all.  The block is
        here instead: the quiet-entry conjunction raises the floor from 2.5 to
        15.0 bps, and 545 one-away blocks land on it with median edge +8.40.
        """
        if not self._a195_breadth_lane_enabled():
            return float(effective_min_edge_bps)
        tick = int(getattr(self, "_tick", 0) or 0)
        if tick != int(getattr(self, "_a195_breadth_tick", -1)):
            self._a195_breadth_tick = tick
            self._a195_breadth_granted_this_tick = 0
        last = self._a195_breadth_last_relief_tick.get(int(book_id))
        try:
            verdict = evaluate_breadth_relief(
                enabled=True,
                observations_remaining=int(observations_remaining),
                current_edge_bps=float(current_edge_bps),
                base_min_edge_bps=float(base_min_edge_bps),
                effective_min_edge_bps=float(effective_min_edge_bps),
                # Shares A1.9.3's budget rather than inventing a second one, so
                # relief can never exceed the books the agent could hold.
                score_deficit=self._a193_breadth_budget(self._a193_score_deficit()),
                granted_this_tick=int(getattr(self, "_a195_breadth_granted_this_tick", 0)),
                ticks_since_last_relief=None if last is None else max(0, tick - int(last)),
                max_per_tick=int(getattr(
                    self, "research_a195_breadth_relief_per_tick",
                    A195_MAX_BREADTH_RELIEF_PER_TICK,
                )),
                cooldown_ticks=int(getattr(
                    self, "research_a195_breadth_relief_cooldown",
                    A195_BREADTH_RELIEF_COOLDOWN_TICKS,
                )),
            )
        except Exception:
            # Relief must fail closed: the unrelieved floor is the pre-A1.9.5
            # behaviour, which is strictly the more conservative one.
            return float(effective_min_edge_bps)

        if not verdict.allow:
            # Only count a denial where relief was actually on the table; the
            # other reasons fire on every ordinary book and would drown the
            # signal that breadth-critical books are being refused.
            if verdict.reason not in ("NOT_ONE_AWAY", "GATE_INACTIVE", "DISABLED"):
                self._a195_breadth_denies += 1
                self._a195_breadth_deny_reasons[verdict.reason] = (
                    self._a195_breadth_deny_reasons.get(verdict.reason, 0) + 1
                )
                try:
                    self._emit(
                        "A195_BREADTH_DENY", force=True, tick=tick,
                        book=int(book_id), **verdict.as_log(),
                    )
                except Exception:
                    pass
            return float(effective_min_edge_bps)

        self._a195_breadth_granted_this_tick = int(
            getattr(self, "_a195_breadth_granted_this_tick", 0)
        ) + 1
        self._a195_breadth_grants += 1
        self._a195_breadth_relief_bps_total += float(verdict.relief_bps)
        self._a195_breadth_last_relief_tick[int(book_id)] = tick
        try:
            self._emit(
                "A195_BREADTH_ADMIT", force=True, tick=tick,
                book=int(book_id), **verdict.as_log(),
            )
        except Exception:
            pass
        return float(verdict.relieved_min_edge_bps)

    # ---- A1.9.3 breadth-critical admission -----------------------------
    def _a193_enabled(self) -> bool:
        return bool(getattr(self, "research_a193_breadth_admission_enabled", True))

    def _a193_score_deficit(self) -> int:
        """Qualified-book shortfall against the score target, from the screen."""
        diag = getattr(self, "_research_inventory_lane_diag", None) or {}
        try:
            return max(0, int(diag.get("direct_score_deficit", 0) or 0))
        except Exception:
            return 0

    def _a193_breadth_budget(self, deficit: int) -> int:
        """Never grant more books than the deficit, nor more than we can hold."""
        try:
            cap = int(getattr(self, "research_max_open_books", 0) or 0)
        except Exception:
            cap = 0
        if cap <= 0:
            cap = int(self.A193_MAX_BREADTH_BOOKS_FALLBACK)
        return max(0, min(int(deficit), cap))

    def _a193_breadth_lane(self, bid: int, remaining: int):
        """The two states where one round trip changes qualification.

        Books two or more observations away are deliberately excluded: a single
        round trip does not change their state, so paying negative edge for one
        buys volume rather than breadth.  That exclusion is what keeps this a
        breadth controller instead of an activity controller.
        """
        if int(remaining) == 1:
            return A193_ALLOW_COMPLETION, 0.0
        if int(remaining) <= 0:
            try:
                expiry = self._research_kappa_expiry(int(bid))
            except Exception:
                return None, 0.0
            if not bool(getattr(expiry, "qualified", False)):
                return None, 0.0
            urgency = float(getattr(expiry, "expiry_urgency", 0.0) or 0.0)
            # Reuse the base's own definition of a critical deadline rather than
            # introducing a second, differently-tuned notion of urgency.
            floor = float(getattr(self, "research_deadline_critical_urgency", 0.50))
            if urgency >= floor:
                return A193_ALLOW_REFRESH, urgency
        return None, 0.0

    def _a193_deny(self, bid, lane, bound, **kw) -> dict:
        key = (int(bid), str(bound))
        if key not in getattr(self, "_a193_window_denied", set()):
            self._a193_window_denied.add(key)
            try:
                self._emit(
                    "A193_BREADTH_DENY", force=True,
                    tick=getattr(self, "_tick", None), book=int(bid),
                    lane=str(lane), bound=str(bound), **kw,
                )
            except Exception:
                pass
        return {"allow": False, "lane": lane, "bound": bound}

    def _a193_breadth_override(
        self, bid: int, remaining: int, capture_bps: float,
        maker_fee_bps: float, current_edge_bps: float,
    ):
        """Bounded override of NEGATIVE_CURRENT_EDGE for breadth-critical books."""
        if not self._a193_enabled():
            return None
        bid = int(bid)
        lane, urgency = self._a193_breadth_lane(bid, remaining)
        if lane is None:
            return None
        info = dict(
            capture_bps=round(float(capture_bps), 4),
            maker_fee_bps=round(float(maker_fee_bps), 4),
            current_edge_bps=round(float(current_edge_bps), 4),
            urgency=round(float(urgency), 4),
            observations_remaining=int(remaining),
        )
        # Structural cost bound: the tighter of the material-harm floor and the
        # book's own half-spread.  Capped in absolute bps so a wide spread cannot
        # licence an arbitrarily expensive observation.
        limit = -min(
            float(self.A192_TAIL_SHORTFALL_FLOOR_BPS),
            float(self.A193_MAX_COST_SPREADS) * float(capture_bps),
        )
        if float(current_edge_bps) < limit:
            self._a193_cost_denies += 1
            return self._a193_deny(bid, lane, "COST", cost_limit_bps=round(limit, 4), **info)
        deficit = self._a193_score_deficit()
        if deficit <= 0:
            self._a193_deficit_denies += 1
            return self._a193_deny(bid, lane, "NO_DEFICIT", score_deficit=0, **info)
        granted = getattr(self, "_a193_window_books", None)
        if granted is None:
            granted = self._a193_window_books = set()
        if bid not in granted:
            budget = self._a193_breadth_budget(deficit)
            if len(granted) >= budget:
                self._a193_budget_denies += 1
                return self._a193_deny(
                    bid, lane, "BUDGET", score_deficit=int(deficit),
                    budget=int(budget), granted=len(granted), **info,
                )
            granted.add(bid)
            self._a193_admits += 1
            if lane == A193_ALLOW_COMPLETION:
                self._a193_completion_admits += 1
            else:
                self._a193_refresh_admits += 1
            try:
                self._emit(
                    "A193_BREADTH_ADMIT", force=True,
                    tick=getattr(self, "_tick", None), book=bid, lane=str(lane),
                    score_deficit=int(deficit), budget=int(self._a193_breadth_budget(deficit)),
                    granted=len(granted), **info,
                )
            except Exception:
                pass
        return {"allow": True, "lane": lane, "urgency": urgency}

    @staticmethod
    def _a1921_severity(risk: dict[str, Any]) -> float:
        """How dangerous this book is, in bps.  Fee is excluded on purpose.

        Both terms are already computed by `_a192_book_risk` and already logged
        on every A192_CAP_BLOCK, so prioritisation needs no new market signal.
        """
        tail = max(0.0, float(risk.get("book_tail_shortfall_bps") or 0.0))
        net = float(risk.get("book_net_bps_ewma") or 0.0)
        return round(tail + abs(min(0.0, net)), 4)

    def _a1921_pooled_risk(self, tick: int) -> dict[str, float] | None:
        """Cross-book prior for cold-start shrinkage, cached per tick."""
        if int(getattr(self, "_a1921_pool_tick", -1)) == int(tick):
            return getattr(self, "_a1921_pool", None)
        nets: list[float] = []
        tails: list[float] = []
        for _bid, st in (getattr(self, "_direct_maker_quality_by_book", {}) or {}).items():
            if int(getattr(st, "count", 0) or 0) < int(self.A192_MIN_BOOK_SAMPLES):
                continue
            nets.append(float(getattr(st, "net_bps_ewma", 0.0) or 0.0))
            cube = max(0.0, float(getattr(st, "taker_net_shortfall_cube_ewma", 0.0) or 0.0))
            tails.append(cube ** (1.0 / 3.0) if cube > 0.0 else 0.0)
        pool = None
        if len(nets) >= int(self.A1921_MIN_POOL_BOOKS):
            pool = {
                "pool_books": float(len(nets)),
                "pool_net_bps_ewma": sum(nets) / len(nets),
                "pool_tail_shortfall_bps": sum(tails) / len(tails),
            }
        self._a1921_pool_tick = int(tick)
        self._a1921_pool = pool
        return pool

    def _a1921_shrink_risk(self, risk: dict[str, Any], pool: dict[str, float]) -> dict[str, Any]:
        """Hierarchical shrinkage toward the pool for a thin-history book."""
        n = float(int(risk.get("book_samples", 0) or 0))
        k = float(self.A1921_COLDSTART_PRIOR_SAMPLES)
        w = n / (n + k) if (n + k) > 0.0 else 0.0
        net = w * float(risk.get("book_net_bps_ewma") or 0.0) + (1.0 - w) * float(pool["pool_net_bps_ewma"])
        tail = w * float(risk.get("book_tail_shortfall_bps") or 0.0) + (1.0 - w) * float(pool["pool_tail_shortfall_bps"])
        out = dict(risk)
        out["book_net_bps_ewma"] = round(net, 4)
        out["book_tail_shortfall_bps"] = round(tail, 4)
        out["a1921_coldstart_shrunk"] = 1
        out["a1921_shrink_weight"] = round(w, 4)
        out["a1921_pool_books"] = int(pool["pool_books"])
        return out

    def _a1921_seed_severity_history(self) -> int:
        """Seed the severity quantile from persisted per-book quality.

        Without this the first ~50 ticks of every run allocate by arrival
        order, which is the behaviour A1.9.2.1 exists to remove.  Uses only
        `_direct_maker_quality_by_book`, which the session already persists --
        no new signal, and no constant fitted to any run.
        """
        if getattr(self, "_a1921_seeded", False):
            return 0
        self._a1921_seeded = True
        if not bool(self.A1921_SEED_FROM_BOOK_HISTORY):
            return 0
        seeded = 0
        for _bid, stx in (getattr(self, "_direct_maker_quality_by_book", {}) or {}).items():
            if int(getattr(stx, "count", 0) or 0) < int(self.A192_MIN_BOOK_SAMPLES):
                continue
            cube = max(0.0, float(getattr(stx, "taker_net_shortfall_cube_ewma", 0.0) or 0.0))
            tail = cube ** (1.0 / 3.0) if cube > 0.0 else 0.0
            net = float(getattr(stx, "net_bps_ewma", 0.0) or 0.0)
            self._a1921_sev_hist.append(round(max(0.0, tail) + abs(min(0.0, net)), 4))
            seeded += 1
        self._a1921_seed_count = seeded
        return seeded

    def _a1921_severity_threshold(self) -> float | None:
        """Severity a candidate must reach to be worth spending budget on.

        Ranking is an offline idea: candidates arrive one at a time and cannot
        be compared against arrivals that have not happened yet.  The online
        equivalent is a quantile of the recent candidate severity distribution,
        placed so that the share of candidates above it is exactly the share
        the 35% volume budget can pay for.  It self-calibrates to whatever the
        severity distribution is and fits no constant to any single run.

        Returns None when ranking should not apply -- too little history, or
        the budget can already afford every candidate.
        """
        hist = getattr(self, "_a1921_sev_hist", None)
        if not hist or len(hist) < int(self.A1921_MIN_SEVERITY_SAMPLES):
            return None
        opp = int(getattr(self, "_a192_window_opportunities", 0) or 0)
        cand = int(getattr(self, "_a192_window_candidates", 0) or 0)
        if opp <= 0 or cand <= 0:
            return None
        cand_rate = float(cand) / float(opp)
        if cand_rate <= 0.0:
            return None
        # Share of CANDIDATES the volume budget can cover.
        frac = (float(self.A192_MAX_SUPPRESSION_PCT) / 100.0) / cand_rate
        if frac >= 1.0:
            return None
        ordered = sorted(hist)
        idx = int(round((1.0 - frac) * (len(ordered) - 1)))
        return float(ordered[max(0, min(idx, len(ordered) - 1))])

    def _a192_admission_verdict(self, book_id: int, maker_fee_bps: float, tick: int) -> dict[str, Any]:
        """Decide whether fresh Maker entry on this book is admitted.

        The order of these checks is the finding, not a style choice.  A1.9.2
        put fee sign FIRST, short-circuiting on `fee <= 0` before reading any
        history, because poor-history books entered at a rebate produced 0 bad
        round trips in 21 observations while the same books at a positive fee
        produced a 30.4% bad rate and 87% of all cubic downside.

        A1.9.4 keeps the discriminator and drops the immunity.  The A1.9.3 run
        put 85.6% of all avoidable loss through that short-circuit, from three
        books whose damage ranked exactly by rebate depth -- a rebate is the
        venue's price for expected adverse selection, so its size cannot be
        evidence of safety.  Decisively: `book_net_bps_ewma` already includes
        the rebate, so a rebate book with negative net has been paid and still
        lost.  Fee sign is therefore read AFTER history, as an exemption
        bounded by the harm the book has done (`A194_ALLOW_REBATE_COVERED`)
        and otherwise as a face-value severity credit.

        `prior_n` is still not consulted at all -- it measured AUC 0.502.
        """
        bid = int(book_id)
        if not self._a192_enabled():
            return {"suppress": False, "reason": A192_ALLOW_DISABLED}
        self._a192_window_roll(int(tick))
        self._a192_window_opportunities = int(getattr(self, "_a192_window_opportunities", 0) or 0) + 1

        fee = float(maker_fee_bps or 0.0)
        rebate = self._a194_rebate_bps(fee)
        # A1.9.4 moves this test after the history read.  Behind the switch the
        # A1.9.2 short-circuit is preserved byte-for-byte so an abort can
        # restore it mid-run without reverting the build.
        if fee <= 0.0 and not self._a194_enabled():
            self._a192_rebate_admits = int(getattr(self, "_a192_rebate_admits", 0) or 0) + 1
            self._a192_admissions = int(getattr(self, "_a192_admissions", 0) or 0) + 1
            return {"suppress": False, "reason": A192_ALLOW_REBATE, "maker_fee_bps": fee}

        risk = self._a192_book_risk(bid)
        dwell_until = int((getattr(self, "_a192_dwell_until", {}) or {}).get(bid, 0) or 0)
        dwell_active = int(tick) < dwell_until

        if not dwell_active:
            if int(risk["book_samples"]) < int(self.A192_MIN_BOOK_SAMPLES):
                # A1.9.2.1: shrink toward the cross-book pool instead of
                # admitting unconditionally.  "No history" is not evidence of
                # low risk, and treating it as such leaked 22.1% of the run's
                # cubic downside through this branch.  Falls back to the
                # A1.9.2 behaviour when there is no pool to shrink toward.
                pool = self._a1921_pooled_risk(int(tick)) if self._a1921_enabled() else None
                if pool is None:
                    self._a192_no_history_admits = int(getattr(self, "_a192_no_history_admits", 0) or 0) + 1
                    self._a192_admissions = int(getattr(self, "_a192_admissions", 0) or 0) + 1
                    return {"suppress": False, "reason": A192_ALLOW_NO_HISTORY,
                            "maker_fee_bps": fee, **risk}
                risk = self._a1921_shrink_risk(risk, pool)
                self._a1921_coldstart_shrinks = int(getattr(self, "_a1921_coldstart_shrinks", 0) or 0) + 1
            flagged = (
                float(risk["book_net_bps_ewma"]) < float(self.A192_NET_BPS_FLOOR)
                and float(risk["book_tail_shortfall_bps"]) > float(self.A192_TAIL_SHORTFALL_FLOOR_BPS)
            )
            if not flagged:
                self._a192_book_ok_admits = int(getattr(self, "_a192_book_ok_admits", 0) or 0) + 1
                self._a192_admissions = int(getattr(self, "_a192_admissions", 0) or 0) + 1
                return {"suppress": False, "reason": A192_ALLOW_BOOK_OK,
                        "maker_fee_bps": fee, **risk}

        # A1.9.4.  The book is flagged (or serving dwell) and pays a rebate.
        # Being paid is an exemption only while it exceeds the harm the book
        # has actually done -- both sides are bps, so this is a direct
        # comparison with nothing to calibrate.  Deliberately outside the
        # `not dwell_active` branch above: under A1.9.2 a rebate re-admitted a
        # quarantined book unconditionally, which is the same immunity by
        # another route.
        if self._a194_enabled() and rebate > 0.0:
            severity_probe = self._a1921_severity(risk)
            if rebate + 1e-12 >= severity_probe:
                self._a194_rebate_covered_admits = int(
                    getattr(self, "_a194_rebate_covered_admits", 0) or 0
                ) + 1
                self._a192_rebate_admits = int(getattr(self, "_a192_rebate_admits", 0) or 0) + 1
                self._a192_admissions = int(getattr(self, "_a192_admissions", 0) or 0) + 1
                if bid not in getattr(self, "_a194_covered_books", set()):
                    try:
                        self._a194_covered_books.add(bid)
                        self._emit(
                            "A194_REBATE_COVERED", force=True, tick=int(tick), book=bid,
                            maker_fee_bps=fee, rebate_bps=round(rebate, 4),
                            severity=float(severity_probe),
                            dwell_active=int(bool(dwell_active)), **risk,
                        )
                    except Exception:
                        pass
                return {"suppress": False, "reason": A194_ALLOW_REBATE_COVERED,
                        "maker_fee_bps": fee, "rebate_bps": round(rebate, 4),
                        "severity": float(severity_probe), **risk}
            # Paid, and still losing more than the rebate covers.  A1.9.2 would
            # have admitted this book without reading a single one of these
            # numbers; from here it is ranked like any other candidate.
            self._a194_waiver_withdrawn = int(getattr(self, "_a194_waiver_withdrawn", 0) or 0) + 1
            self._a194_uncovered_bps_total = float(
                getattr(self, "_a194_uncovered_bps_total", 0.0) or 0.0
            ) + max(0.0, float(severity_probe) - rebate)
            if bid not in getattr(self, "_a194_withdrawn_books", set()):
                try:
                    self._a194_withdrawn_books.add(bid)
                    self._emit(
                        "A194_REBATE_WAIVER_WITHDRAWN", force=True, tick=int(tick), book=bid,
                        maker_fee_bps=fee, rebate_bps=round(rebate, 4),
                        severity=float(severity_probe),
                        severity_shortfall_bps=round(float(severity_probe) - rebate, 4),
                        dwell_active=int(bool(dwell_active)), **risk,
                    )
                except Exception:
                    pass

        self._a192_candidates = int(getattr(self, "_a192_candidates", 0) or 0) + 1
        self._a192_window_candidates = int(getattr(self, "_a192_window_candidates", 0) or 0) + 1
        try:
            self._a192_flagged_books.add(bid)
        except AttributeError:
            self._a192_flagged_books = {bid}

        # A1.9.2.1.  Severity is recorded for EVERY candidate, whatever the
        # outcome, because the quantile has to describe the candidate
        # population -- recording only the suppressed ones would censor the
        # distribution at exactly the point being measured.
        # A1.9.4 changes admission only.  Severity stays fee-blind: A1.9.2.1
        # measured no ordering information in fee within this set, and the
        # A1.9.3 damage ranked by rebate depth, so a credit would invert it.
        severity = self._a1921_severity(risk)
        if self._a1921_enabled():
            try:
                if not getattr(self, "_a1921_seeded", False):
                    n_seed = self._a1921_seed_severity_history()
                    try:
                        self._emit(
                            "A192_SEVERITY_SEED", force=True, tick=int(tick),
                            seeded_books=int(n_seed),
                            min_severity_samples=int(self.A1921_MIN_SEVERITY_SAMPLES),
                            armed=int(n_seed >= int(self.A1921_MIN_SEVERITY_SAMPLES)),
                        )
                    except Exception:
                        pass
                self._a1921_sev_hist.append(severity)
            except AttributeError:
                self._a1921_sev_hist = deque([severity], maxlen=int(self.A1921_SEVERITY_HISTORY))
            if int(risk.get("a1921_coldstart_shrunk", 0) or 0):
                self._a1921_coldstart_flagged = int(getattr(self, "_a1921_coldstart_flagged", 0) or 0) + 1
                try:
                    self._emit(
                        "A192_COLDSTART_SHRINK", force=True, tick=int(tick), book=bid,
                        maker_fee_bps=fee, severity=float(severity), **risk,
                    )
                except Exception:
                    pass

        # Bounded by construction.  A fee-only rule scored better on PnL in the
        # counterfactual but suppressed 78% of round trips; a tail-risk gate
        # that becomes a volume gate fails Kappa on breadth instead of downside.
        opp = int(getattr(self, "_a192_window_opportunities", 0) or 0)
        sup = int(getattr(self, "_a192_window_suppressed", 0) or 0)
        # Rolling budget, not an instantaneous ratio.  Comparing (sup+1)/opp
        # directly deadlocks the gate: the first opportunity in every window is
        # 1/1 = 100%, so it always exceeds the cap and nothing is ever
        # suppressed.  A budget converges to the cap over the window instead,
        # and the max(1.0, ...) floor only frees the first suppression of a
        # 200-tick window.
        allowance = max(1.0, opp * float(self.A192_MAX_SUPPRESSION_PCT) / 100.0)
        threshold = self._a1921_severity_threshold() if self._a1921_enabled() else None
        if (sup + 1) > allowance:
            self._a192_cap_blocks = int(getattr(self, "_a192_cap_blocks", 0) or 0) + 1
            self._a192_admissions = int(getattr(self, "_a192_admissions", 0) or 0) + 1
            return {"suppress": False, "reason": A192_ALLOW_CAP, "maker_fee_bps": fee,
                    "window_suppression_pct": round(100.0 * sup / opp, 2),
                    "severity": float(severity),
                    "severity_threshold": (float(threshold) if threshold is not None else None),
                    **risk}

        # A1.9.2.1.  The budget is intact, but it is not spent on this
        # candidate: less severe than the share of the candidate distribution
        # the budget can cover, so it is reserved for a worse one.  This is the
        # only new way an entry can be admitted, and it strictly REDUCES
        # suppression, so the <=35% volume guarantee is unchanged.  Dwell
        # re-suppressions reach this test on the same terms as fresh
        # candidates: a quarantine no longer holds a standing claim on budget
        # that newly arriving, more severe books cannot outbid.
        if threshold is not None and float(severity) < float(threshold):
            self._a1921_rank_defers = int(getattr(self, "_a1921_rank_defers", 0) or 0) + 1
            self._a192_admissions = int(getattr(self, "_a192_admissions", 0) or 0) + 1
            return {"suppress": False, "reason": A192_ALLOW_SEVERITY_RANK, "maker_fee_bps": fee,
                    "window_suppression_pct": round(100.0 * sup / opp, 2),
                    "severity": float(severity), "severity_threshold": float(threshold),
                    "dwell_active": int(bool(dwell_active)), **risk}

        self._a192_window_suppressed = sup + 1
        self._a192_suppressions = int(getattr(self, "_a192_suppressions", 0) or 0) + 1
        if dwell_active:
            self._a192_dwell_suppressions = int(getattr(self, "_a192_dwell_suppressions", 0) or 0) + 1
            reason = A192_SUPPRESS_DWELL
        else:
            reason = A192_SUPPRESS_FLAGGED
            strikes = int((getattr(self, "_a192_strikes", {}) or {}).get(bid, 0) or 0) + 1
            self._a192_strikes[bid] = strikes
            dwell_until = int(tick) + min(
                int(self.A192_DWELL_MAX_TICKS),
                int(self.A192_DWELL_BASE_TICKS) * strikes,
            )
            self._a192_dwell_until[bid] = dwell_until
            self._a192_clean_rts[bid] = 0
        return {
            "suppress": True, "reason": reason, "maker_fee_bps": fee,
            "dwell_until": int(dwell_until),
            "dwell_remaining_ticks": max(0, int(dwell_until) - int(tick)),
            "strikes": int((getattr(self, "_a192_strikes", {}) or {}).get(bid, 0) or 0),
            "window_suppression_pct": round(100.0 * (sup + 1) / max(1, opp), 2),
            "severity": float(severity),
            "severity_threshold": (float(threshold) if threshold is not None else None),
            **risk,
        }

    def _a192_note_round_trip(self, book_id: int, *, net_bps: float, exit_is_taker: bool) -> None:
        """Retire quarantine on clean Maker-ending round trips.

        Suppression must decay or a book that recovers is never re-tested, and
        the gate slowly starves the portfolio.  Only a positive Maker-ending
        round trip counts: a positive Taker exit does not prove the book stopped
        producing tails.
        """
        if not self._a192_enabled():
            return
        bid = int(book_id)
        strikes = int((getattr(self, "_a192_strikes", {}) or {}).get(bid, 0) or 0)
        if strikes <= 0:
            return
        if bool(exit_is_taker) or float(net_bps or 0.0) <= 0.0:
            return
        clean = int((getattr(self, "_a192_clean_rts", {}) or {}).get(bid, 0) or 0) + 1
        if clean < int(self.A192_RECOVERY_CLEAN_RTS):
            self._a192_clean_rts[bid] = clean
            return
        self._a192_clean_rts[bid] = 0
        remaining = strikes - 1
        self._a192_strikes[bid] = remaining
        self._a192_recoveries = int(getattr(self, "_a192_recoveries", 0) or 0) + 1
        if remaining <= 0:
            self._a192_dwell_until.pop(bid, None)
            try:
                self._a192_flagged_books.discard(bid)
            except AttributeError:
                pass
        try:
            self._emit(
                "A192_BOOK_RECOVERED", force=True,
                tick=int(getattr(self, "_tick", 0) or 0), book=bid,
                strikes_remaining=int(remaining), net_bps=float(net_bps or 0.0),
            )
        except Exception:
            pass

    def _a192_check_activation(self, tick: int) -> None:
        """Make an inert admission gate announce itself instead of costing a run.

        A1.9.1 shipped twice with a correct mechanism that was never reached,
        and both runs were only diagnosed by after-the-fact log analysis.
        """
        if not getattr(self, "_a192_activation_banner_emitted", False):
            self._a192_activation_banner_emitted = True
            try:
                self._emit(
                    "A192_ACTIVATION_BANNER", force=True, tick=int(tick),
                    engine_version=SIMPLE_ENGINE_VERSION,
                    a192_phase=self._a192_runtime_phase(),
                    a192_behaviour_change=self._a192_behaviour_change(),
                    admission_enabled=int(self._a192_enabled()),
                    min_book_samples=int(self.A192_MIN_BOOK_SAMPLES),
                    net_bps_floor=float(self.A192_NET_BPS_FLOOR),
                    tail_shortfall_floor_bps=float(self.A192_TAIL_SHORTFALL_FLOOR_BPS),
                    max_suppression_pct=float(self.A192_MAX_SUPPRESSION_PCT),
                    window_ticks=int(self.A192_WINDOW_TICKS),
                    dwell_base_ticks=int(self.A192_DWELL_BASE_TICKS),
                    dwell_max_ticks=int(self.A192_DWELL_MAX_TICKS),
                    recovery_clean_rts=int(self.A192_RECOVERY_CLEAN_RTS),
                    a1921_severity_priority_enabled=int(self._a1921_enabled()),
                    a1921_severity_history=int(self.A1921_SEVERITY_HISTORY),
                    a1921_min_severity_samples=int(self.A1921_MIN_SEVERITY_SAMPLES),
                    a1921_coldstart_prior_samples=float(self.A1921_COLDSTART_PRIOR_SAMPLES),
                    a1921_min_pool_books=int(self.A1921_MIN_POOL_BOOKS),
                    a192_events=",".join(DIRECT_A192_EVENTS),
                    a193_events=",".join(DIRECT_A193_EVENTS),
                    a193_breadth_admission_enabled=int(bool(self._a193_enabled())),
                    a193_max_cost_spreads=float(self.A193_MAX_COST_SPREADS),
                    a194_events=",".join(DIRECT_A194_EVENTS),
                    a194_rebate_conjunction_enabled=int(bool(self._a194_enabled())),
                )
            except Exception:
                pass
        if getattr(self, "_a192_activation_alarm_emitted", False) or not self._a192_enabled():
            return
        if (
            int(getattr(self, "_a192_candidates", 0) or 0) >= int(self.A192_ACTIVATION_ALARM_CANDIDATES)
            and int(getattr(self, "_a192_suppressions", 0) or 0) == 0
        ):
            self._a192_activation_alarm_emitted = True
            try:
                self._emit(
                    "A192_ACTIVATION_ALARM", force=True, tick=int(tick),
                    candidates=int(getattr(self, "_a192_candidates", 0) or 0),
                    suppressions=0,
                    cap_blocks=int(getattr(self, "_a192_cap_blocks", 0) or 0),
                    verdict="A192_INERT_STOP_THE_RUN",
                    detail=(
                        "books are qualifying as fee-and-risk flagged but no entry "
                        "has been suppressed; the admission gate is not wired"
                    ),
                )
            except Exception:
                pass

    def _a19_ledger_ref(self) -> DirectExitLedger | None:
        """Ledger handle that tolerates hot-reload and bare test objects."""
        ledger = getattr(self, "_a19_ledger", None)
        if not isinstance(ledger, DirectExitLedger):
            ledger = DirectExitLedger()
            self._a19_ledger = ledger
        return ledger

    def onOrderAccepted(self, event) -> None:
        """Record the exchange's acknowledgement of one of our limit orders."""
        super().onOrderAccepted(event)
        try:
            etype = str(getattr(event, "type", "") or "").upper()
            if etype and not ("RDPOL" in etype or "LIMIT" in etype):
                return
            price = getattr(event, "price", None)
            if price is None:
                return
            if not bool(getattr(event, "success", True)):
                return
            book_id = getattr(event, "bookId", None)
            self._a19_ledger_ref().note_accepted(
                order_id=getattr(event, "orderId", None),
                book_id=book_id,
                side=getattr(event, "side", None),
                price=price,
                quantity=getattr(event, "quantity", None),
                timestamp_ns=getattr(event, "timestamp", 0),
                tick=int(getattr(self, "_tick", 0) or 0),
                client_id=getattr(event, "clientOrderId", None),
                action=self._a19_pending_action.get(int(book_id), "") if book_id is not None else "",
            )
        except Exception:
            pass

    def onOrderCancelled(self, event) -> None:
        """Retire a ledger row the exchange has cancelled or expired."""
        super().onOrderCancelled(event)
        try:
            self._a19_ledger_ref().note_removed(
                getattr(event, "orderId", None), cause=LEDGER_REMOVED_CANCELLED,
            )
        except Exception:
            pass

    @staticmethod
    def _a19_order_id(order) -> int | None:
        try:
            return int(getattr(order, "id", None))
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _a19_order_price(order) -> float | None:
        price = getattr(order, "price", None)
        if price is None:
            price = getattr(order, "limit_price", None)
        try:
            return float(price)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _a19_tick_size(state) -> float:
        try:
            price_dec = int(getattr(getattr(state, "config", None), "priceDecimals", 2) or 2)
        except (TypeError, ValueError):
            price_dec = 2
        return 10.0 ** (-max(0, price_dec))

    def _a19_close_side_orders(self, book_id: int, long_pos: bool) -> list:
        """Resting close-side orders for this book in the current snapshot."""
        close_side = 1 if long_pos else 0
        rows = []
        for order in self._direct_account_orders(int(book_id)):
            try:
                if int(getattr(order, "side", -1)) == close_side:
                    rows.append(order)
            except (TypeError, ValueError):
                continue
        return rows

    def _a19_resting_net_bps(self, book_id: int, inventory, price) -> float:
        """Lifecycle net of a resting exit at its OWN price, at current fees."""
        entry = float(getattr(inventory, "vwap_entry", 0.0) or 0.0)
        if entry <= 0.0 or price is None:
            return float("nan")
        fee = float(self._research_live_fee_bps(int(book_id), is_maker=True) or 0.0)
        # v6.1: the lot the close would actually hit carries its own entry price and the opening
        # fee the validator will charge at the close; vwap_entry with today's fee on both legs is
        # a different number, and NET_BELOW_FLOOR reprices against it.
        if self._v61_on():
            long_pos = float(getattr(inventory, "net_base", 0.0) or 0.0) > 0.0
            lot = v61_head_lot(self._v61_positions(int(book_id)), long_position=long_pos)
            if lot is not None:
                net = v61_fifo_close_net_bps(
                    lot, close_price=float(price), close_fee_bps=fee, long_position=long_pos,
                )
                if net == net:
                    return float(net)
        try:
            return float(unified_completion_net_bps(
                entry_price=entry, exit_price=float(price),
                long_position=float(getattr(inventory, "net_base", 0.0) or 0.0) > 0.0,
                entry_fee_bps=fee, exit_fee_bps=fee,
            ))
        except (TypeError, ValueError):
            return float("nan")

    def _a19_note_exit_cancel(self, book_id: int, order_ids, disposition: str) -> None:
        """Start the acknowledgement clock for a cancel Phase B will depend on."""
        watch = getattr(self, "_a19_cancel_watch", None)
        if watch is None:
            return
        tick = int(getattr(self, "_tick", 0) or 0)
        memo = getattr(self, "_a19_cancel_reason", None)
        if memo is None:
            memo = {}
            self._a19_cancel_reason = memo
        for oid in (order_ids or ()):
            try:
                watch[(int(book_id), int(oid))] = (tick, str(disposition))
                memo[int(oid)] = (tick, str(disposition))
            except (TypeError, ValueError):
                continue
        if len(memo) > self.A19_CANCEL_MEMO_MAX:
            for stale in sorted(memo, key=lambda k: memo[k][0])[: self.A19_CANCEL_MEMO_MAX // 4]:
                memo.pop(stale, None)
        stale = [
            key for key, (sent, _) in watch.items()
            if tick - int(sent) > self.A19_CANCEL_WATCH_MAX_TICKS
        ]
        for key in stale:
            watch.pop(key, None)

    def _a19_settle_cancel_watch(self, book_id: int, live_ids: set) -> str | None:
        """Resolve pending cancels for this book and measure the ack latency.

        Phase B's cancel-then-replace assumes a cancel sent at T is gone by
        T+1.  This turns that assumption into a measurement.
        """
        watch = getattr(self, "_a19_cancel_watch", None) or {}
        tick = int(getattr(self, "_tick", 0) or 0)
        disposition = None
        for key in [k for k in list(watch) if int(k[0]) == int(book_id)]:
            sent, reason = watch[key]
            age = max(0, tick - int(sent))
            if int(key[1]) not in live_ids:
                watch.pop(key, None)
                self._a19_cancel_acks += 1
                self._a19_cancel_ack_ticks_total += age
                disposition = str(reason)
                if int(key[1]) in (getattr(self, "_a19_identity_acked", None) or set()):
                    # A1.9.1.2 already reported this cancellation from the
                    # exchange notice; a second row would double-count it.
                    self._a19_identity_acked.discard(int(key[1]))
                    self._a19_duplicate_acks_suppressed = int(
                        getattr(self, "_a19_duplicate_acks_suppressed", 0) or 0
                    ) + 1
                    continue
                self._emit(
                    "A19_CANCEL_ACK", force=True, tick=tick, book=int(book_id),
                    order_id=int(key[1]), cancel_reason=str(reason),
                    # A1.9.1.2: this path observes the order leaving the LEDGER,
                    # which is internal settlement, not the exchange's answer.
                    # Reporting it as ack_ticks read 4 ticks where the exchange
                    # was answering at T+1 on 42 of 42 cancels.
                    ownership_release_ticks=age, exchange_ack_ticks=None,
                    release_path="LEDGER_SETTLE",
                )
            elif age == self.A19_CANCEL_ACK_BUDGET_TICKS:
                self._a19_cancel_not_acked += 1
                self._emit(
                    "A19_CANCEL_NOT_ACKED", force=True, tick=tick, book=int(book_id),
                    order_id=int(key[1]), ticks_pending=age, cancel_reason=str(reason),
                )
        return disposition

    def _a19_is_entry_quote_row(self, book_id: int, row) -> bool:
        """True when this ledger row is one of the book's two entry quotes."""
        cid = getattr(row, "client_id", None)
        if cid is None:
            return False
        try:
            return int(cid) in self._direct_entry_quote_client_ids(int(book_id))
        except (TypeError, ValueError):
            return False

    def _a19_resolve_disposition(self, order_id, *, ledger, shrank: bool) -> str:
        """Name why a tracked exit left the book, from evidence not inference.

        A1.9.0.2 asked "is any cancel pending on this book?", which mislabelled
        real agent cancels as EXPIRED whenever the watch entry had already been
        consumed by the placement path, and could borrow another order's reason.
        The evidence is ranked instead:

        1. the ledger's own removal cause -- a fill is a fill
        2. a cancel WE registered for this exact order id
        3. only with no notice at all, fall back to the position-shrank guess
        """
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            return ABSENT_EXPIRED
        cause = ledger.removal_cause(oid)
        if cause == LEDGER_REMOVED_FILLED:
            return ABSENT_FILLED
        if cause == LEDGER_REMOVED_TTL_SWEEP:
            # Bounded memory, not a measured expiry.  Keep it distinct so a
            # sweep can never inflate the exchange-side expiry rate.
            return ABSENT_LEDGER_SWEEP
        memo = (getattr(self, "_a19_cancel_reason", None) or {}).get(oid)
        if cause == LEDGER_REMOVED_CANCELLED:
            # A cancellation notice we did not ask for is a real expiry: the
            # exchange retires an order at TTL through the same notice.
            return str(memo[1]) if memo is not None else ABSENT_EXPIRED
        if memo is not None:
            return str(memo[1])
        return ABSENT_FILLED if shrank else ABSENT_EXPIRED

    def _a191_enabled(self) -> bool:
        return bool(getattr(self, "research_a191_queue_preservation_enabled", True))

    def _a19_runtime_phase(self) -> str:
        """The phase this process is ACTUALLY running, not the one it shipped as."""
        return (
            DIRECT_A19_PHASE_BEHAVIOURAL if self._a191_enabled()
            else DIRECT_A19_PHASE_SHADOW
        )

    def _a19_behaviour_change(self) -> int:
        """1 when a decision can change an instruction; 0 in pure measurement."""
        return int(self._a191_enabled())

    def _a191_verdict_store(self) -> dict:
        """Per-tick verdict cache, cleared when the tick advances."""
        tick = int(getattr(self, "_tick", 0) or 0)
        if int(getattr(self, "_a191_verdict_tick", -1)) != tick:
            self._a191_verdict_tick = tick
            self._a191_verdicts = {}
        return self._a191_verdicts

    def _a191_live_exit_row(self, book_id: int, net_base: float, now_ns: int):
        """The live, in-TTL, non-entry-quote close-side order for this book."""
        ledger = self._a19_ledger_ref()
        ttl_ms = float(getattr(self, "research_profitable_exit_ttl_ms", 3000.0) or 3000.0)
        rows = ledger.live_orders(
            int(book_id), side=close_side_for(net_base),
            max_age_ms=ttl_ms, now_ns=now_ns,
        )
        for row in rows:
            if not self._a19_is_entry_quote_row(int(book_id), row):
                return row
        return None

    def _a191_decide(
        self, state, book_id: int, inventory, qty: float,
        desired_price, desired_action,
    ) -> dict[str, Any] | None:
        """Decide HOLD / REPRICE for the live resting exit on this book.

        Returns None when there is nothing resting to preserve, in which case
        the caller must fall through to the frozen placement path unchanged.

        The verdict is cached per book per tick: the placement path computes it
        against the real desired rung, and the post-pass reuses that rather than
        recomputing against the passive touch and possibly disagreeing.
        """
        bid = int(book_id)
        store = self._a191_verdict_store()
        cached = store.get(bid)
        if cached is not None:
            return cached
        if not self._a191_enabled():
            return None
        try:
            now_ns = int(getattr(state, "timestamp", 0) or 0)
        except (TypeError, ValueError):
            now_ns = 0
        net_base = float(getattr(inventory, "net_base", 0.0) or 0.0)
        if net_base == 0.0:
            return None
        row = self._a191_live_exit_row(bid, net_base, now_ns)
        if row is None:
            return None
        if desired_price is None:
            book = (getattr(state, "books", None) or {}).get(bid)
            try:
                levels = getattr(book, "asks" if net_base > 0.0 else "bids", None)
                desired_price = float(levels[0].price) if levels else None
            except (AttributeError, IndexError, TypeError, ValueError):
                desired_price = None
            # v6.1.1: the comparand is the price the placement path would send, and v6.1 floors
            # every maker exit at its lot's break-even.  Against the bare touch a held lot's floored
            # exit read ~1,100 ticks stale and was cancelled one request after it was placed.
            if desired_price is not None:
                desired_price = self._v611_seed_comparand(
                    bid, state, desired_price, long_position=net_base > 0.0,
                )
        if desired_price is None:
            return None

        long_pos = net_base > 0.0
        ttl_ms = float(getattr(self, "research_profitable_exit_ttl_ms", 3000.0) or 3000.0)
        age_ms = row.age_ms(now_ns)
        remaining_ms = max(0.0, ttl_ms - age_ms)
        decision, reason = classify_resting_maker_exit(
            existing_price=row.price, desired_price=float(desired_price),
            tick_size=self._a19_tick_size(state), long_position=long_pos,
            existing_qty=float(row.remaining or row.quantity),
            desired_qty=abs(float(qty)) if qty else abs(net_base),
            existing_net_bps=self._a19_resting_net_bps(bid, inventory, row.price),
            floor_net_bps=self._v623_resting_floor_bps(bid, state),
            existing_action=row.action, desired_action=desired_action,
            reprice_ticks=float(getattr(self, "research_profitable_exit_reprice_ticks", 3.0) or 3.0),
        )
        deferred = ""
        if decision == EXIT_REPRICE and remaining_ms < self.A191_MIN_REMAINING_TTL_MS:
            # Structural, not fitted: inside one publish cycle of expiry the
            # cancel buys nothing the exchange is not about to do for free, and
            # it still costs an instruction from a budget of five.
            decision, deferred = EXIT_HOLD, "REMAINING_TTL"
            reason = REASON_QUEUE_PRESERVED
            self._a191_reprice_deferred_ttl += 1

        verdict = {
            "decision": decision, "reason": reason, "deferred": deferred,
            "order_id": int(row.order_id), "order_price": float(row.price),
            "order_action": str(row.action or ""),
            "age_ms": round(age_ms, 1), "remaining_ms": round(remaining_ms, 1),
            "desired_price": float(desired_price),
            "drift_ticks": behind_ticks(
                existing_price=row.price, desired_price=float(desired_price),
                tick_size=self._a19_tick_size(state), long_position=long_pos,
            ),
            "forgone_edge_bps": forgone_edge_bps(
                existing_price=row.price, desired_price=float(desired_price),
                long_position=long_pos,
            ),
            "cancelled": False,
        }
        if decision == EXIT_REPRICE:
            self._a191_reprice_candidates += 1
        store[bid] = verdict
        return verdict

    def _a191_service_reprice_cancels(self, response, state) -> int:
        """Emit the cancel for every REPRICE verdict, after the frozen chain.

        The placement path is not a reliable place to do this: measured on the
        A1.9.0.1 build it saw a live resting exit on 0 of 492 calls, while the
        tick observer saw 960 in 260 ticks.  A book whose exit is resting may
        simply never reach a placement decision, so a cancel emitted only from
        there would never fire.  This pass runs unconditionally.

        No replacement is placed here: the cancellation must be visible in a
        later state before anything re-enters that book (the A1.7.4.3.1
        ownership rule).  The next tick's normal placement path does that.
        """
        # The banner must be emitted BEFORE any early return.  It matters most
        # when the switch is off: that is the state that silently invalidated
        # two runs, and a log with no banner tells you nothing.
        self._a191_check_activation(int(getattr(self, "_tick", 0) or 0))
        if not self._a191_enabled():
            return 0
        books = getattr(state, "books", None) or {}
        if not books:
            return 0
        try:
            now_ns = int(getattr(state, "timestamp", 0) or 0)
        except (TypeError, ValueError):
            now_ns = 0
        tick = int(getattr(self, "_tick", 0) or 0)
        emitted = 0

        # A1.9.1.1: seed a verdict for every open-inventory book BEFORE reading
        # the cache.  A1.9.1 iterated only cached verdicts, and the sole writer
        # of that cache is `_a191_decide` called from `_research_place_maker_exit`
        # -- the path measured at 0 of 492 sightings of a live resting exit.  So
        # the cache was empty on exactly the books that needed a cancel, and the
        # 500-tick run produced 0 reprice cancels while the shadow classifier was
        # asking for 756.  This pass now enumerates books the way the observer
        # does, which is the only view proven to see resting exits.
        for raw_bid in list(books):
            try:
                bid_seed = int(raw_bid)
            except (TypeError, ValueError):
                continue
            if bid_seed in self._a191_verdict_store():
                continue
            try:
                seed_inventory = RestingInventoryView.from_tracker(
                    self._position_tracker_snapshot(bid_seed)
                )
            except Exception:
                continue
            if seed_inventory.net_base == 0.0:
                continue
            try:
                # No desired price/action outside the placement path: the passive
                # touch is the comparand, exactly as the observer uses.
                self._a191_decide(
                    state, bid_seed, seed_inventory,
                    abs(float(seed_inventory.net_base)), None, None,
                )
            except Exception:
                continue

        for bid, verdict in list(self._a191_verdict_store().items()):
            if verdict.get("decision") != EXIT_REPRICE or verdict.get("cancelled"):
                continue
            order_id = int(verdict.get("order_id", -1))
            try:
                net_base = float(
                    RestingInventoryView.from_tracker(
                        self._position_tracker_snapshot(int(bid))
                    ).net_base
                )
            except Exception:
                continue
            row = self._a191_live_exit_row(int(bid), net_base, now_ns)
            if row is None or int(row.order_id) != order_id:
                # It filled or expired on its own between the decision and here.
                continue
            if self._count_book_instructions(response, int(bid)) >= self.max_instructions_per_book:
                # R3: the budget is shared with placements and cancels count.
                # Falling back to HOLD keeps the queue position rather than
                # spending the last slot on a teardown we cannot replace.
                self._a191_reprice_deferred_budget += 1
                self._emit(
                    "A19_REPRICE_BUDGET_BLOCK", force=True, tick=tick, book=int(bid),
                    order_id=order_id, deferred="INSTRUCTION_BUDGET",
                    drift_ticks=float(verdict.get("drift_ticks", 0.0)),
                )
                continue
            try:
                response.cancel_orders(book_id=int(bid), order_ids=[order_id], delay=0)
            except Exception:
                self._a191_cancel_emit_failures += 1
                continue
            verdict["cancelled"] = True
            emitted += 1
            self._a191_reprice_cancels += 1
            # Capture the identity now: after the exchange confirms, the ledger
            # row is gone and there is nothing left to match the notice against.
            self._a191_reprice_release[order_id] = {
                "book_id": int(bid),
                "side": "sell" if int(getattr(row, "side", 1)) == 1 else "buy",
                "client_id": getattr(row, "client_id", None),
                "tick": int(tick),
            }
            if len(self._a191_reprice_release) > self.A19_CANCEL_MEMO_MAX:
                for stale in sorted(
                    self._a191_reprice_release,
                    key=lambda k: self._a191_reprice_release[k]["tick"],
                )[: self.A19_CANCEL_MEMO_MAX // 4]:
                    self._a191_reprice_release.pop(stale, None)
            try:
                self._a19_note_exit_cancel(int(bid), [order_id], ABSENT_REPRICE_CANCEL)
            except Exception:
                pass
            self._emit(
                "A19_EXIT_REPRICE_CANCEL", force=True, tick=tick, book=int(bid),
                order_id=order_id, reason=str(verdict.get("reason", "")),
                order_price=float(verdict.get("order_price", 0.0)),
                desired_price=float(verdict.get("desired_price", 0.0)),
                drift_ticks=float(verdict.get("drift_ticks", 0.0)),
                age_ms=float(verdict.get("age_ms", 0.0)),
                remaining_ms=float(verdict.get("remaining_ms", 0.0)),
                order_action=str(verdict.get("order_action", "")),
            )
        self._a191_postpass_cancels += emitted
        self._a191_check_activation(tick)
        return emitted

    def _a191_check_activation(self, tick: int) -> None:
        """Make a silent hybrid announce itself instead of wasting a run.

        The A1.9.1 build reported an a1_9_1 engine with a live 4,000 ms TTL and
        emitted 0 reprice cancels for 2,961 ticks while the classifier asked for
        2,863.  Nothing in the run said so; it took log analysis afterwards.
        A 4,000 ms TTL without the stale-cancel path is the one combination this
        design forbids, so the run itself now reports both halves at startup and
        raises an alarm if the behavioural half never fires.
        """
        if not self._a191_activation_banner_emitted:
            self._a191_activation_banner_emitted = True
            self._emit(
                "A19_ACTIVATION_BANNER", force=True, tick=int(tick),
                engine_version=SIMPLE_ENGINE_VERSION,
                exit_refresh_version=DIRECT_EXIT_REFRESH_VERSION,
                exit_ledger_version=DIRECT_EXIT_LEDGER_VERSION,
                a19_phase=self._a19_runtime_phase(),
                a19_behaviour_change=self._a19_behaviour_change(),
                profitable_exit_ttl_ms=float(
                    getattr(self, "research_profitable_exit_ttl_ms", 0.0) or 0.0
                ),
                min_remaining_ttl_ms=float(self.A191_MIN_REMAINING_TTL_MS),
                phase_b_events=",".join(DIRECT_A19_PHASE_B_EVENTS),
                shadow_mode=int(not self._a191_enabled()),
            )
        if self._a191_activation_alarm_emitted or not self._a191_enabled():
            return
        if (
            int(self._a191_reprice_candidates) >= self.A191_ACTIVATION_ALARM_CANDIDATES
            and int(self._a191_reprice_cancels) == 0
        ):
            self._a191_activation_alarm_emitted = True
            self._emit(
                "A19_ACTIVATION_ALARM", force=True, tick=int(tick),
                reprice_candidates=int(self._a191_reprice_candidates),
                reprice_cancels=0,
                deferred_ttl=int(self._a191_reprice_deferred_ttl),
                deferred_budget=int(self._a191_reprice_deferred_budget),
                cancel_emit_failures=int(self._a191_cancel_emit_failures),
                verdict="PHASE_B_INERT_STOP_THE_RUN",
                detail=(
                    "TTL raise is active but no stale exit has been cancelled; "
                    "this is the forbidden 4000ms-TTL-without-cancels hybrid"
                ),
            )

    def _a19_observe_tick_resting_exits(self, state) -> None:
        """Observe every open-inventory book's resting Maker exit, each tick.

        A1.9.0 and A1.9.0.1 hung the observer on ``_research_place_maker_exit``,
        which only runs once the strategy has already decided to place a NEW
        exit.  That moment is structurally too late.  The frozen final validator
        drops a placement onto a book that still holds a live order, and the
        3,000 ms exit TTL expires a full second before the 4,000 ms re-quote
        cycle comes back round, so by the time the placement path ran, the order
        it wanted to measure was always already dead.  That is why
        ``resting_present`` was 0 on all 492 evaluations and why no shadow
        HOLD/REPRICE decision was ever produced -- twice.

        This pass runs at the top of ``respond``, before the frozen base has
        touched a single live-order or ownership gate, and it looks at a book
        because the book carries a position, not because a placement is pending.
        The exchange's notices for this state are already applied -- the SDK
        calls ``update`` (which drives ``onOrderAccepted``/``onOrderCancelled``)
        before ``respond`` -- so the ledger read here is this tick's truth, and
        it is the window in which a HOLD/REPRICE decision is still actionable.

        Measurement only.  This method returns nothing, writes no threshold, and
        appends no instruction; A1.9.1 is what acts on the decision.
        """
        ledger = self._a19_ledger_ref()
        try:
            now_ns = int(getattr(state, "timestamp", 0) or 0)
        except (TypeError, ValueError):
            now_ns = 0
        if now_ns and now_ns != int(getattr(self, "_a19_last_state_ns", 0) or 0):
            self._a19_last_state_ns = now_ns
            ledger.sweep(now_ns)

        # ``Strategy1.respond`` increments ``_tick`` as its first action and this
        # pass runs before that call, so the tick being observed is one ahead of
        # the counter.  Labelling it correctly is what lets an A19_TICK_OBSERVE
        # row join the A19_EXIT_EVAL row emitted later in the same tick.
        tick = int(getattr(self, "_tick", 0) or 0) + 1
        self._a19_tick_passes += 1

        books = getattr(state, "books", None) or {}
        if not books:
            return
        try:
            flat_eps = float(self._execution_flat_epsilon())
        except Exception:
            flat_eps = 1e-9
        tick_size = self._a19_tick_size(state)
        exit_ttl_ms = float(getattr(self, "research_profitable_exit_ttl_ms", 3000.0) or 3000.0)
        floor_bps = float(DIRECT_MAKER_EXIT_TARGET_BPS)
        min_net_bps = float(getattr(self, "research_profitable_exit_min_net_bps", 0.0) or 0.0)
        reprice_ticks = float(getattr(self, "research_profitable_exit_reprice_ticks", 3.0) or 3.0)
        # Previous tick's regime: this pass runs before the base reclassifies.
        # For a resting order that is the correct regime anyway -- it is the one
        # in force when the order was placed and priced.
        regime = getattr(self, "_research_market_regime", "NORMAL")
        watch = getattr(self, "_a19_cancel_watch", None) or {}

        for raw_bid, book in books.items():
            try:
                bid = int(raw_bid)
            except (TypeError, ValueError):
                continue
            # Pure position read.  `_net_inventory` must NOT be used here: it
            # ages `_position_ticks` once per `_tick`, and running it before the
            # increment would double-age any book that was idle last tick.
            # Position age drives the exit escalation ladder, so that would be a
            # live behaviour change in a measurement-only revision.
            try:
                inventory = RestingInventoryView.from_tracker(
                    self._position_tracker_snapshot(bid)
                )
            except Exception:
                continue
            net_base = float(inventory.net_base)
            if abs(net_base) < flat_eps:
                # Position closed while an exit row may still be tracked; retire
                # the lifecycle so the next position on this book starts clean.
                if bid in self._a19_tick_seen:
                    stale_seen = self._a19_tick_seen.pop(bid)
                    # The position is gone, but say WHY the order went: a WAIT
                    # cancel on a book that then flattened is not a fill.
                    self._a19_emit_tick_lifecycle(
                        bid, tick,
                        self._a19_resolve_disposition(
                            stale_seen.get("order_id"), ledger=ledger, shrank=True,
                        ),
                        stale_seen,
                    )
                continue

            self._a19_tick_observations += 1
            long_pos = net_base > 0.0
            close_side = close_side_for(net_base)
            resting = ledger.live_orders(
                bid, side=close_side, max_age_ms=exit_ttl_ms, now_ns=now_ns,
            )
            lagged = ledger.live_orders(bid, side=close_side)
            # A1.9.0.3: on a long book the entry ASK rests on the close side and
            # is indistinguishable from a Maker exit by book+side alone.  Adopting
            # one inflates resting_present and the hold rate, and reports EXPIRED
            # when the quote manager cancels it.  Exclude by the client-id
            # convention the cancel path itself uses.
            entry_quote_rows = sum(1 for r in lagged if self._a19_is_entry_quote_row(bid, r))
            if entry_quote_rows:
                self._a19_tick_entry_quote_rows += entry_quote_rows
                resting = [r for r in resting if not self._a19_is_entry_quote_row(bid, r)]
                lagged = [r for r in lagged if not self._a19_is_entry_quote_row(bid, r)]
            live_ids = {int(row.order_id) for row in lagged}
            # A row whose acknowledgement carried no usable timestamp has an
            # unknowable age, so the TTL-filtered read drops it silently while
            # the unfiltered read still shows it.  That is indistinguishable
            # from expiry lag unless it is counted, and it would read as a
            # permanently blind observer -- the exact failure A1.9.0.2 exists
            # to rule out.  Measure it rather than trusting it to be zero.
            untimed = sum(1 for row in lagged if not row.placed_ns > 0)
            if untimed:
                self._a19_tick_untimed_rows += untimed
            if resting:
                self._a19_tick_resting_hits += 1
            elif lagged:
                self._a19_tick_expiry_lag_hits += 1

            # Whether a cancel we sent is still awaiting acknowledgement, for
            # reporting only.  Settlement stays in the placement path, where
            # `_tick` has been incremented and ack latency is on the right clock.
            pending_cancel = int(any(
                int(key[0]) == bid and int(key[1]) in live_ids for key in watch
            ))

            seen = self._a19_tick_seen.get(bid)
            if seen is not None and int(seen.get("order_id", -1)) not in live_ids:
                prior_abs = abs(float(seen.get("net_base", net_base)))
                disposition = self._a19_resolve_disposition(
                    seen.get("order_id"), ledger=ledger,
                    shrank=abs(net_base) + 1e-12 < prior_abs,
                )
                self._a19_emit_tick_lifecycle(bid, tick, disposition, seen)
                self._a19_tick_seen.pop(bid, None)
                seen = None

            if seen is None and resting:
                row = resting[0]
                seen = {
                    "order_id": int(row.order_id),
                    "price": float(row.price),
                    "qty": float(row.remaining or row.quantity),
                    "action": str(row.action or "") or self._a19_pending_action.get(bid, ""),
                    "first_tick": int(row.placed_tick or tick),
                    "placed_ns": int(row.placed_ns or 0),
                    "net_base": net_base,
                    "observed_ticks": 0,
                    "peak_age_ms": 0.0,
                }
                self._a19_tick_seen[bid] = seen
                self._a19_tick_first_sightings += 1

            age_ms = 0.0
            if seen is not None:
                placed_ns = float(seen.get("placed_ns", 0) or 0)
                if placed_ns > 0.0 and now_ns:
                    age_ms = max(0.0, (float(now_ns) - placed_ns) / 1e6)
                seen["observed_ticks"] = int(seen.get("observed_ticks", 0)) + 1
                seen["peak_age_ms"] = max(float(seen.get("peak_age_ms", 0.0)), age_ms)
                self._a19_tick_max_observed_ticks = max(
                    int(self._a19_tick_max_observed_ticks), int(seen["observed_ticks"]),
                )

            # The price this book would quote if it repriced right now, at the
            # passive touch.  That is precisely the alternative a HOLD gives up,
            # so it is the honest comparand for the shadow decision.
            desired = None
            try:
                side_levels = getattr(book, "asks" if long_pos else "bids", None)
                if side_levels:
                    desired = float(side_levels[0].price)
            except (AttributeError, IndexError, TypeError, ValueError):
                desired = None

            touch_net_bps = (
                float("nan") if desired is None
                else self._a19_resting_net_bps(bid, inventory, desired)
            )
            eval_class = exit_eval_class(
                maker_net_bps=touch_net_bps,
                min_net_bps=min_net_bps,
                market_regime=regime,
            )
            if eval_class == EVAL_PERSIST_ELIGIBLE:
                self._a19_tick_eligible += 1
                if seen is not None:
                    self._a19_tick_eligible_with_resting += 1

            decision = ""
            reason = ""
            drift = 0.0
            forgone = 0.0
            resting_net_bps = float("nan")
            if seen is not None and desired is not None:
                resting_net_bps = self._a19_resting_net_bps(bid, inventory, seen.get("price"))
                decision, reason = classify_resting_maker_exit(
                    existing_price=seen.get("price"), desired_price=desired,
                    tick_size=tick_size, long_position=long_pos,
                    existing_qty=seen.get("qty"), desired_qty=abs(net_base),
                    existing_net_bps=resting_net_bps,
                    floor_net_bps=floor_bps,
                    existing_action=seen.get("action"),
                    # No desired rung exists outside the placement path, and
                    # `ladder_rung(None)` is -1, so escalation cannot fire on a
                    # missing comparand rather than on a real escalation.
                    desired_action=None,
                    reprice_ticks=reprice_ticks,
                )
                drift = behind_ticks(
                    existing_price=seen.get("price"), desired_price=desired,
                    tick_size=tick_size, long_position=long_pos,
                )
                forgone = forgone_edge_bps(
                    existing_price=seen.get("price"), desired_price=desired,
                    long_position=long_pos,
                )
                if decision == EXIT_HOLD:
                    self._a19_tick_shadow_holds += 1
                else:
                    self._a19_tick_shadow_reprices += 1

            self._emit(
                "A19_TICK_OBSERVE", force=True, tick=tick, book=bid,
                eval_class=str(eval_class),
                resting_present=int(seen is not None),
                # A1.9.1: without this an observation cannot be joined to the
                # order it describes, so a per-order (rather than per-
                # observation) hold rate cannot be computed after the fact.
                order_id=(-1 if seen is None else int(seen.get("order_id", -1))),
                resting_present_account=int(bool(self._a19_close_side_orders(bid, long_pos))),
                resting_age_ms=round(age_ms, 1),
                resting_observed_ticks=(
                    0 if seen is None else int(seen.get("observed_ticks", 0))
                ),
                ledger_live=int(ledger.live_count(bid)),
                ledger_expiry_lagged=int(len(lagged) - len(resting)),
                ledger_untimed=int(untimed),
                exit_ttl_ms=float(exit_ttl_ms),
                net_base=float(net_base),
                touch_price=(None if desired is None else float(desired)),
                touch_net_bps=(
                    None if not math.isfinite(touch_net_bps) else round(touch_net_bps, 3)
                ),
                resting_net_bps=(
                    None if not math.isfinite(resting_net_bps) else round(resting_net_bps, 3)
                ),
                shadow_decision=str(decision), shadow_reason=str(reason),
                drift_ticks=float(drift), forgone_edge_bps=float(forgone),
                tenure_ticks=(
                    0 if seen is None else max(0, tick - int(seen.get("first_tick", tick)))
                ),
                pending_cancel=int(pending_cancel),
                # 1 only while the decision is discarded.  Phase B acts on it.
                shadow_mode=int(not self._a191_enabled()),
            )

    def _a19_emit_tick_lifecycle(
        self, book_id: int, tick: int, disposition: str, seen: dict,
    ) -> None:
        """Close out one observed resting exit and record how long it lived."""
        observed = int(seen.get("observed_ticks", 0) or 0)
        self._a19_tick_lifecycles += 1
        self._a19_tick_observed_ticks_total += observed
        counts = getattr(self, "_a19_tick_disposition_counts", None)
        if counts is None:
            counts = {}
            self._a19_tick_disposition_counts = counts
        counts[str(disposition)] = int(counts.get(str(disposition), 0)) + 1
        self._emit(
            "A19_TICK_LIFECYCLE", force=True, tick=int(tick), book=int(book_id),
            disposition=str(disposition),
            agent_cancelled=int(str(disposition) in AGENT_CANCEL_DISPOSITIONS),
            tenure_ticks=max(0, int(tick) - int(seen.get("first_tick", tick))),
            observed_ticks=observed,
            peak_age_ms=round(float(seen.get("peak_age_ms", 0.0) or 0.0), 1),
            order_price=seen.get("price"),
            order_action=str(seen.get("action", "") or ""),
        )

    def _a19_observe_exit_evaluation(
        self, state, book_id: int, inventory, qty: float, action: str,
        *, close_price, maker_net_bps,
    ) -> None:
        """Measure one exit evaluation.  Never changes a decision."""
        bid = int(book_id)
        tick = int(getattr(self, "_tick", 0) or 0)
        try:
            publish_ns = float(getattr(getattr(state, "config", None), "publish_interval", 0) or 0)
            if publish_ns > 0.0:
                self._a19_publish_interval_ms = publish_ns / 1e6
        except (TypeError, ValueError):
            pass

        net_base = float(getattr(inventory, "net_base", 0.0) or 0.0)
        long_pos = net_base > 0.0

        # A1.9.0.1: the account snapshot is kept only as a control measurement.
        # Phase A proved it never carries the resting exit here, so the ledger
        # rebuilt from acknowledged notices is the operative view.
        account_resting = self._a19_close_side_orders(bid, long_pos)
        ledger = self._a19_ledger_ref()
        try:
            now_ns = int(getattr(state, "timestamp", 0) or 0)
        except (TypeError, ValueError):
            now_ns = 0
        if now_ns and now_ns != int(getattr(self, "_a19_last_state_ns", 0) or 0):
            # Any clock movement sweeps.  A backwards move means the simulation
            # restarted and reused order ids, which the ledger answers by
            # discarding the previous session outright.
            self._a19_last_state_ns = now_ns
            ledger.sweep(now_ns)
        close_side = close_side_for(net_base)
        exit_ttl_ms = float(getattr(self, "research_profitable_exit_ttl_ms", 3000.0) or 3000.0)
        # Rows still inside the TTL they were placed under.  Anything at or past
        # it is treated as gone even if its cancellation notice has not arrived,
        # because Phase B must never hold on an order the exchange has retired.
        resting = ledger.live_orders(
            bid, side=close_side, max_age_ms=exit_ttl_ms, now_ns=now_ns,
        )
        lagged = ledger.live_orders(bid, side=close_side)
        live_ids = {int(row.order_id) for row in lagged}
        if resting:
            self._a19_ledger_resting_hits += 1
            if account_resting:
                self._a19_account_resting_hits += 1
            else:
                self._a19_ledger_only_hits += 1
        elif lagged:
            self._a19_ledger_expiry_lag_hits += 1
        cancel_disposition = self._a19_settle_cancel_watch(bid, live_ids)

        seen = self._a19_exit_seen.get(bid)
        if seen is not None and int(seen.get("order_id", -1)) not in live_ids:
            # The tracked exit is gone.  A cancel we sent explains it; otherwise
            # a shrunken position means it filled, and anything else expired.
            prior_abs = abs(float(seen.get("net_base", net_base)))
            disposition = cancel_disposition or (
                ABSENT_FILLED if abs(net_base) + 1e-12 < prior_abs else ABSENT_EXPIRED
            )
            self._a19_last_disposition[bid] = disposition
            self._emit(
                "A19_EXIT_LIFECYCLE", force=True, tick=tick, book=bid,
                disposition=disposition,
                tenure_ticks=max(0, tick - int(seen.get("first_tick", tick))),
                order_price=seen.get("price"),
                order_action=str(seen.get("action", "") or ""),
            )
            self._a19_exit_seen.pop(bid, None)
            seen = None

        if seen is None and resting:
            row = resting[0]
            seen = {
                "order_id": int(row.order_id),
                "price": float(row.price),
                "qty": float(row.remaining or row.quantity),
                "action": str(row.action or "") or self._a19_pending_action.get(bid, ""),
                # Tenure runs from the tick the exchange acknowledged the order,
                # not from the tick this observer first noticed it.
                "first_tick": int(row.placed_tick or tick),
                "placed_ns": int(row.placed_ns or 0),
                "net_base": net_base,
            }
            self._a19_exit_seen[bid] = seen

        eval_class = exit_eval_class(
            maker_net_bps=maker_net_bps,
            min_net_bps=float(getattr(self, "research_profitable_exit_min_net_bps", 0.0) or 0.0),
            market_regime=getattr(self, "_research_market_regime", "NORMAL"),
        )
        self._a19_exit_evals += 1
        if eval_class == EVAL_PERSIST_ELIGIBLE:
            self._a19_eligible_evals += 1
            if seen is not None:
                self._a19_eligible_with_resting += 1

        decision = ""
        reason = ""
        drift = 0.0
        forgone = 0.0
        if seen is not None and close_price is not None:
            tick_size = self._a19_tick_size(state)
            desired = float(close_price)
            decision, reason = classify_resting_maker_exit(
                existing_price=seen.get("price"), desired_price=desired,
                tick_size=tick_size, long_position=long_pos,
                existing_qty=seen.get("qty"), desired_qty=float(qty),
                existing_net_bps=self._a19_resting_net_bps(bid, inventory, seen.get("price")),
                floor_net_bps=float(DIRECT_MAKER_EXIT_TARGET_BPS),
                existing_action=seen.get("action"), desired_action=action,
                reprice_ticks=float(getattr(self, "research_profitable_exit_reprice_ticks", 3.0) or 3.0),
            )
            drift = behind_ticks(
                existing_price=seen.get("price"), desired_price=desired,
                tick_size=tick_size, long_position=long_pos,
            )
            forgone = forgone_edge_bps(
                existing_price=seen.get("price"), desired_price=desired,
                long_position=long_pos,
            )
            if decision == EXIT_HOLD:
                self._a19_shadow_holds += 1
            else:
                self._a19_shadow_reprices += 1

        self._emit(
            "A19_EXIT_EVAL", force=True, tick=tick, book=bid,
            eval_class=str(eval_class),
            resting_present=int(seen is not None),
            resting_present_account=int(bool(account_resting)),
            resting_age_ms=(
                0.0 if seen is None
                else round(max(0.0, (now_ns - float(seen.get("placed_ns", 0) or 0)) / 1e6), 1)
                if seen.get("placed_ns") else 0.0
            ),
            ledger_live=int(ledger.live_count(bid)),
            ledger_expiry_lagged=int(len(lagged) - len(resting)),
            exit_ttl_ms=float(exit_ttl_ms),
            absent_reason=(
                "" if seen is not None
                else str(self._a19_last_disposition.get(bid, ABSENT_NEVER_PLACED))
            ),
            shadow_decision=str(decision), shadow_reason=str(reason),
            drift_ticks=float(drift), forgone_edge_bps=float(forgone),
            tenure_ticks=(
                0 if seen is None else max(0, tick - int(seen.get("first_tick", tick)))
            ),
            action=str(action or ""),
            maker_net_bps=(None if maker_net_bps is None else float(maker_net_bps)),
            shadow_mode=1,
        )

        # Record the rung this evaluation would place at, after adoption above,
        # so an order first seen next tick inherits the action that placed it.
        self._a19_pending_action[bid] = str(action or "")

    def _direct_cancel_unsafe_wait_exits(self, response, book_id: int, inventory, *, reason: str) -> tuple[int, int]:
        """Cancel only close-side resting orders whose current lifecycle net is unsafe.

        A profitable resting Maker exit is allowed to keep its queue position while
        Direct WAIT blocks *new* realization.  Negative/stale close-side orders are
        cancelled so WAIT cannot be bypassed by an older legacy ladder order.
        Returns ``(cancelled_orders, kept_profitable_orders)``.
        """
        account = (getattr(self, "accounts", {}) or {}).get(int(book_id))
        orders = list(getattr(account, "orders", None) or []) if account is not None else []
        if not orders:
            return 0, 0
        long_pos = float(getattr(inventory, "net_base", 0.0) or 0.0) > 0.0
        close_side = 1 if long_pos else 0
        entry = float(getattr(inventory, "vwap_entry", 0.0) or 0.0)
        if entry <= 0.0:
            return 0, 0
        maker_fee = float(self._research_live_fee_bps(int(book_id), is_maker=True) or 0.0)
        floor = float(DIRECT_MAKER_EXIT_TARGET_BPS)
        cancel_ids = []
        kept = 0
        for order in orders:
            try:
                if int(getattr(order, "side", -1)) != close_side:
                    continue
                px = getattr(order, "price", None)
                if px is None:
                    px = getattr(order, "limit_price", None)
                px = float(px)
                order_net = unified_completion_net_bps(
                    entry_price=entry, exit_price=px, long_position=long_pos,
                    entry_fee_bps=maker_fee, exit_fee_bps=maker_fee,
                )
                if float(order_net) + 1e-12 >= floor:
                    kept += 1
                    continue
                oid = getattr(order, "id", None)
                protected_id = self._direct_protected_partial_order_id(int(book_id))
                if oid is not None and (protected_id is None or int(oid) != int(protected_id)):
                    cancel_ids.append(oid)
            except (TypeError, ValueError):
                continue
        if not cancel_ids:
            return 0, kept
        if self._count_book_instructions(response, int(book_id)) >= self.max_instructions_per_book:
            return 0, kept
        try:
            response.cancel_orders(book_id=int(book_id), order_ids=cancel_ids, delay=0)
        except Exception:
            return 0, kept
        self._direct_wait_cancel_batches = int(getattr(self, "_direct_wait_cancel_batches", 0) or 0) + 1
        # A1.9 Phase A: start the acknowledgement clock on a real cancel.  Phase
        # B's cancel-then-replace assumes the cancel is visible one state later;
        # this measures that assumption on cancels that already happen today.
        try:
            self._a19_note_exit_cancel(
                int(book_id), cancel_ids,
                ABSENT_NEG_AGGRESSIVE_CANCEL
                if str(reason or "") == "NEGATIVE_AGGRESSIVE_BLOCK"
                else ABSENT_WAIT_CANCEL,
            )
        except Exception:
            pass
        try:
            self._emit(
                "A172_WAIT_CANCEL", force=True,
                tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                reason=str(reason or "WAIT"), cancelled_orders=len(cancel_ids),
                kept_profitable_orders=int(kept), maker_floor_bps=float(floor),
            )
        except Exception:
            pass
        return len(cancel_ids), kept

    def _research_place_maker_exit(
        self, response, state, book_id: int, book, inventory, qty: float, action: str,
        close_price: float | None = None, maker_net_bps: float | None = None,
    ) -> int:
        """A1.7.2 final execution invariant for Direct inventory realization."""
        # A1.9.7 P1: on the post-fill tick the entry quotes are cancelled in this
        # same response, so a maker exit here would be the cancel-and-replace
        # A1.7.4.3.1 forbids.  Refuse it; the ordinary one-tick wait applies.
        if getattr(self, "_a197_postfill_book", None) == int(book_id):
            self._a197_postfill_maker_refused = int(
                getattr(self, "_a197_postfill_maker_refused", 0) or 0
            ) + 1
            return 0
        if self._direct_partial_hold_active(int(book_id), state):
            self._direct_emit_partial_replacement_block(int(book_id), path="MAKER_EXIT")
            return 0

        # v6.1: the price this exit may rest at is the lot's fee-inclusive FIFO break-even, and
        # everything below -- the classifier's desired_price, the A1.7.4 guards, the placement --
        # reads the floored price, not the requested one.
        if self._v61_on():
            try:
                close_price, v61_net, v61_floored = self._v61_apply_floor(
                    int(book_id), state, inventory, qty, close_price, action,
                )
                if v61_floored and v61_net is not None:
                    maker_net_bps = v61_net
            except Exception:
                self._v61_errors = int(getattr(self, "_v61_errors", 0) or 0) + 1

        # A1.9 Phase A shadow measurement.  The classifier's decision is
        # computed, logged, and deliberately discarded: nothing below reads it.
        try:
            self._a19_observe_exit_evaluation(
                state, int(book_id), inventory, float(qty), str(action or ""),
                close_price=close_price, maker_net_bps=maker_net_bps,
            )
        except Exception:
            pass

        # A1.9.1 Phase B.  A live, in-TTL, profitable resting exit keeps its
        # queue position: place nothing this response.  On REPRICE we also place
        # nothing -- the cancel goes out in the post-pass and the replacement
        # lands on a later state, never in the same response as the cancel.
        verdict = None
        try:
            verdict = self._a191_decide(
                state, int(book_id), inventory, float(qty),
                close_price, str(action or ""),
            )
        except Exception:
            verdict = None
        if verdict is not None:
            self._a191_placements_suppressed += 1
            if verdict.get("decision") == EXIT_HOLD:
                self._a191_holds += 1
                self._emit(
                    "A19_QUEUE_HOLD", force=True,
                    tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                    order_id=int(verdict.get("order_id", -1)),
                    reason=str(verdict.get("reason", "")),
                    deferred=str(verdict.get("deferred", "")),
                    order_price=float(verdict.get("order_price", 0.0)),
                    desired_price=float(verdict.get("desired_price", 0.0)),
                    drift_ticks=float(verdict.get("drift_ticks", 0.0)),
                    forgone_edge_bps=float(verdict.get("forgone_edge_bps", 0.0)),
                    age_ms=float(verdict.get("age_ms", 0.0)),
                    remaining_ms=float(verdict.get("remaining_ms", 0.0)),
                    action=str(action or ""),
                )
            # Returning 0 is load-bearing: `_research_note_exit_attempt`
            # early-returns on placed=False, so a preserved order stops counting
            # as a failed exit and stops driving the AGGRESSIVE ladder.
            return 0

        authority = self._direct_current_exit_authority(int(book_id))
        if authority is not None and str(authority.get("action") or "") == ACTION_WAIT:
            cancelled, kept = self._direct_cancel_unsafe_wait_exits(
                response, int(book_id), inventory, reason=str(authority.get("reason") or "WAIT"),
            )
            self._direct_wait_holds = int(getattr(self, "_direct_wait_holds", 0) or 0) + 1
            try:
                self._emit(
                    "A172_WAIT_HOLD", force=True,
                    tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                    reason=str(authority.get("reason") or "WAIT"),
                    maker_net_bps=float(authority.get("maker_net_bps", 0.0) or 0.0),
                    taker_net_bps=float(authority.get("taker_net_bps", 0.0) or 0.0),
                    cancelled_orders=int(cancelled), kept_profitable_orders=int(kept),
                    new_maker_order=0,
                )
            except Exception:
                pass
            # Deliberately return zero even if a cancel instruction was emitted:
            # the frozen caller treats any positive return as a newly placed exit
            # attempt and increments failed-exit state.  WAIT cancellation is not
            # a failed realization attempt.
            return 0

        # A1.7.4 bounded concession: negative Maker is legal only when the
        # current-tick recovery authority explicitly approved it and the final
        # executable net still respects the reason-specific floor.
        recovery_maker_ok = False
        if authority is not None and bool(authority.get("recovery_maker_authorized", 0)):
            floor = authority.get("recovery_maker_floor_bps")
            try:
                floor_f = float(floor) if floor is not None else 0.0
                maker_f = float(maker_net_bps) if maker_net_bps is not None else -1e9
                recovery_maker_ok = maker_f + 1e-12 >= floor_f
            except (TypeError, ValueError):
                recovery_maker_ok = False
            if not recovery_maker_ok:
                try:
                    self._emit(
                        "A174_RECOVERY_MAKER_BLOCK", force=True,
                        tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                        reason=str(authority.get("reason") or "RECOVERY_MAKER"),
                        maker_net_bps=(None if maker_net_bps is None else float(maker_net_bps)),
                        authorized_floor_bps=floor, action=str(action or ""),
                    )
                except Exception:
                    pass
                return 0
            try:
                self._emit(
                    "A174_RECOVERY_MAKER_PLACE", force=True,
                    tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                    reason=str(authority.get("reason") or "RECOVERY_MAKER"),
                    maker_net_bps=float(maker_net_bps),
                    authorized_floor_bps=floor_f, action=str(action or ""),
                    position_risk_bps=float(authority.get("position_risk_bps", 0.0) or 0.0),
                    risk_velocity_bps_per_tick=float(authority.get("risk_velocity_bps_per_tick", 0.0) or 0.0),
                )
            except Exception:
                pass

        # Independent belt-and-suspenders guard: a legacy path may still request
        # AGGRESSIVE_MAKER_EXIT. Never allow that rung to realize a negative
        # lifecycle merely because Taker authority was denied. A1.7.4 recovery
        # is the only bounded exception.
        # v6.2.3: on a book outside the Kappa-3 premium branch a negative maker is the release itself.
        if (
            str(action or "") == "AGGRESSIVE_MAKER_EXIT"
            and maker_net_bps is not None
            and not recovery_maker_ok
            and not self._v623_lifted(int(book_id), state)[0]
        ):
            try:
                negative_aggressive = float(maker_net_bps) < -1e-12
            except (TypeError, ValueError):
                negative_aggressive = False
            if negative_aggressive:
                cancelled, kept = self._direct_cancel_unsafe_wait_exits(
                    response, int(book_id), inventory, reason="NEGATIVE_AGGRESSIVE_BLOCK",
                )
                self._direct_negative_aggressive_blocks = int(
                    getattr(self, "_direct_negative_aggressive_blocks", 0) or 0
                ) + 1
                try:
                    self._emit(
                        "A172_NEGATIVE_AGGRESSIVE_BLOCK", force=True,
                        tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                        maker_net_bps=float(maker_net_bps), cancelled_orders=int(cancelled),
                        kept_profitable_orders=int(kept), new_maker_order=0,
                    )
                except Exception:
                    pass
                return 0

        # v6.2.4: a v6.2.3 release takes the exit order life inside this one frozen call.
        with self._v624_release_life(int(book_id), state):
            return super()._research_place_maker_exit(
                response, state, book_id, book, inventory, qty, action,
                close_price=close_price, maker_net_bps=maker_net_bps,
            )

    def _emit(self, event_type: str, force: bool = False, **payload: Any) -> None:
        # v5.0.0: the analytics ledger reads a row as it is written.  The row goes on unchanged,
        # and nothing the ledger holds is read by a decision.
        analytics = getattr(self, "_v500_analytics", None)
        if analytics is not None and event_type in V500_TAPPED_ROWS:
            try:
                analytics.observe(event_type, payload)
            except Exception:
                pass
        return super()._emit(event_type, force=force, **payload)

    def _v500_service(self, state) -> None:
        """v5.0.0, once per request: closed round trips, taker counterfactuals, score mirror, rollups."""
        analytics = getattr(self, "_v500_analytics", None)
        if analytics is None:
            return
        tick = int(getattr(self, "_tick", 0) or 0)
        now_ts = int(getattr(state, "timestamp", 0) or 0)
        analytics.note_round(now_ts)
        self._v500_note_universe(state, tick)
        for event_type, row in analytics.flush(tick=tick, now_ts=now_ts, books=getattr(state, "books", None) or {}):
            self._emit(event_type, force=True, tick=tick, **row)
        if tick > 0 and tick % V500_SCORE_EVERY_TICKS == 0:
            self._v500_emit_score(state, tick, now_ts)
        if tick > 0 and tick % V500_ROLLUP_EVERY_TICKS == 0:
            for event_type, row in analytics.rollup(now_ts=now_ts):
                self._emit(event_type, force=True, tick=tick, **row)

    def _v500_note_universe(self, state, tick: int) -> None:
        """The books the validator scores: in simulation mode, every book 0..book_count-1."""
        cfg = getattr(state, "config", None)
        books = getattr(state, "books", None) or {}
        book_count = int(getattr(cfg, "book_count", 0) or 0) or len(books)
        key = (book_count, len(books), getattr(cfg, "miner_wealth", None), getattr(cfg, "grace_period", None))
        if key == getattr(self, "_v500_universe_key", None):
            return
        self._v500_universe_key = key
        ids = set()
        for raw in books.keys():
            try:
                ids.add(int(raw))
            except (TypeError, ValueError):
                continue
        max_inactive = int(float(VALIDATOR_SCORING_DEFAULTS["max_inactive_books_ratio"]) * book_count)
        self._emit(
            "V500_UNIVERSE", force=True, tick=tick,
            v500_analytics_version=V500_ANALYTICS_VERSION,
            v500_score_mirror_version=V500_SCORE_MIRROR_VERSION,
            engine_mode="simulation", book_count=book_count, books_in_state=len(books),
            missing_book_ids=[b for b in range(book_count) if b not in ids][:64],
            extra_book_ids=sorted(b for b in ids if b >= book_count)[:64],
            miner_wealth=getattr(cfg, "miner_wealth", None),
            publish_interval=getattr(cfg, "publish_interval", None),
            grace_period=getattr(cfg, "grace_period", None),
            volume_decimals=getattr(cfg, "volumeDecimals", None),
            max_inactive_books=max_inactive, min_scored_books=book_count - max_inactive,
            scoring_defaults=dict(VALIDATOR_SCORING_DEFAULTS),
        )

    def _v500_emit_score(self, state, tick: int, now_ts: int) -> None:
        analytics = self._v500_analytics
        cfg = getattr(state, "config", None)
        books = getattr(state, "books", None) or {}
        book_count = int(getattr(cfg, "book_count", 0) or 0) or len(books)
        if book_count <= 0:
            return
        decimals = getattr(cfg, "volumeDecimals", None)
        # v5.0.1: score with the activity factor the validator holds, not an assumed 1.0.
        belief = getattr(self, "_v501_belief", None)
        factors = None
        if belief is not None:
            factors = belief.factors(
                getattr(self, "_research_kappa_roll_ts_cache", {}) or {}, book_count=book_count, now=now_ts,
            )
        # v5.0.4 H4: the rounds the validator holds for this UID, when they are known.
        history = getattr(self, "realized_pnl_history", {}) or {}
        rounds = analytics.rounds
        extra: dict[str, Any] = {}
        try:
            chosen = self._v504_mirror_inputs(now_ts)
        except Exception:
            chosen = None
            self._v504_errors = int(getattr(self, "_v504_errors", 0) or 0) + 1
        if chosen is not None:
            grid_history, grid_rounds, extra = chosen
            if grid_rounds:
                history, rounds = grid_history, grid_rounds
        started = time.perf_counter()
        mirror = mirror_score(
            history, rounds, now_ts=now_ts,
            book_count=book_count, miner_wealth=float(getattr(cfg, "miner_wealth", 0.0) or 0.0),
            grace_period_ns=int(getattr(cfg, "grace_period", 0) or 0),
            volume_decimals=None if decimals is None else int(decimals),
            activity_factors=factors,
        )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        lookback = int(VALIDATOR_SCORING_DEFAULTS["kappa_lookback_ns"])
        min_obs = int(VALIDATOR_SCORING_DEFAULTS["kappa_min_realized_observations"])
        rows = mirror.books
        expiring = sum(
            1 for r in rows.values()
            if r.get("status") == "SCORED" and r.get("obs") == min_obs and r.get("oldest_ts") is not None
            and int(r["oldest_ts"]) + lookback - now_ts <= 600_000_000_000
        )
        marginal = sorted((r["marginal_kappa"], b) for b, r in rows.items() if "marginal_kappa" in r)
        pnl_books = sorted(r["pnl_window"] for r in rows.values() if r.get("pnl_window") is not None)
        mid = len(pnl_books) // 2
        median_pnl = (None if not pnl_books else
                      pnl_books[mid] if len(pnl_books) % 2 else 0.5 * (pnl_books[mid - 1] + pnl_books[mid]))
        first_round = min(rounds) if rounds else now_ts
        row = mirror.as_log()
        row.update(
            mirror_ms=round(elapsed_ms, 3),
            history_complete=int(now_ts - first_round >= lookback),
            one_away_books=sum(1 for r in rows.values() if r.get("obs") == min_obs - 1),
            expiring_scored_books_10m=expiring,
            median_book_pnl=None if median_pnl is None else round(median_pnl, 6),
            lowest_marginal=[[b, round(v, 6)] for v, b in marginal[:5]],
            highest_marginal=[[b, round(v, 6)] for v, b in marginal[-5:]],
        )
        row.update(extra)
        # v6.0.1 T1: the score on the validator's live blend, without the de-beta leg the agent
        # cannot compute.  trading_score above stays on the v5.0.0 blend for comparison.
        try:
            live = v601_live_trading_ex_debeta(mirror.kappa_score, mirror.pnl_score)
            row["trading_score_live_ex_debeta"] = None if live is None else round(live, 6)
            row["live_blend"] = dict(V601_LIVE_BLEND_WEIGHTS)
        except Exception:
            self._v601_errors = int(getattr(self, "_v601_errors", 0) or 0) + 1
        self._v500_last_score = row
        # v5.0.3 G4: the per-book rows, kept for the comparison row against the validator's own
        # per-book gauges.  The score row itself stays exactly as v5.0.0 wrote it.
        self._v500_last_books = rows
        self._emit("V500_SCORE", force=True, tick=tick, **row)

    def _v501_refresh(self) -> None:
        """v5.0.1, once per request: each book's standing with the validator's activity factor."""
        belief = getattr(self, "_v501_belief", None)
        tick = int(getattr(self, "_tick", 0) or 0)
        if belief is None or getattr(self, "_v501_tick", None) == tick:
            return
        self._v501_tick = tick
        now = getattr(self, "_research_last_sim_ts", None)
        if now is None:
            return
        now = int(now)
        last = getattr(self, "_v501_last_now", None)
        if last is not None and now <= last - REBASE_MIN_JUMP_NS:
            # A new simulation.  The validator rebases the rounds it keeps onto the new clock and the
            # rolling Kappa evidence is rebased the same way, so the estimated start moves with them.
            belief.rebase(now - last)
        self._v501_last_now = now
        start = getattr(self, "_research_sim_start_ts", None)
        if start is not None:
            belief.note_evidence(start, EVIDENCE_FIRST_STATE)
        self._research_refresh_rolling_kappa_cache()
        rolling = getattr(self, "_research_kappa_roll_ts_cache", {}) or {}
        earliest = min((rows[0] for rows in rolling.values() if rows), default=None)
        if earliest is not None:
            belief.note_evidence(earliest, EVIDENCE_OBSERVATION)
        view = activity_view(
            belief, rolling, required=int(self._research_required_observation_count()), now=now,
        )
        previous = getattr(self, "_v501_view", None)
        cold_since = self._v501_cold_since
        for book in view.cold:
            cold_since.setdefault(book, tick)
        for book, book_state in view.states.items():
            if book_state != STATE_ACTIVATED or book in self._v501_seen_activated:
                continue
            self._v501_seen_activated.add(book)
            since = cold_since.get(book)
            self._v501_pending_rows.append({
                "book": int(book), "ts": now,
                "was_cold": int(previous is not None and book in previous.cold),
                "cold_ticks": None if since is None else tick - since,
                "observations": len(rolling.get(book, ())),
            })
        for book in [b for b in cold_since if b not in view.cold]:
            del cold_since[book]
        self._v501_view = view

    def _v501_cold_books(self) -> frozenset[int]:
        """Kappa-eligible books the validator still scores 0.0.  Empty with the switch off."""
        if getattr(self, "_v501_belief", None) is None:
            return frozenset()
        try:
            self._v501_refresh()
        except Exception:
            self._v501_errors = int(getattr(self, "_v501_errors", 0) or 0) + 1
            return frozenset()
        view = getattr(self, "_v501_view", None)
        return view.cold if view is not None else frozenset()

    def _v501_activation_value(self, book_id: int, remaining: int) -> tuple[float, str | None]:
        """The rank's activation term and the book's score state; (0.0, None) with the switch off."""
        if getattr(self, "_v501_belief", None) is None:
            return 0.0, None
        try:
            self._v501_refresh()
        except Exception:
            self._v501_errors = int(getattr(self, "_v501_errors", 0) or 0) + 1
            return 0.0, None
        view = getattr(self, "_v501_view", None)
        if view is None or not view.window_open:
            return 0.0, STATE_GATE_CLOSED
        score_state = view.states.get(int(book_id), STATE_INCOMPLETE)
        if score_state == STATE_COLD and int(remaining) <= 0:
            return V501_ACTIVATION_VALUE, score_state
        return 0.0, score_state

    def _v501_service(self, state) -> None:
        """v5.0.1 telemetry, after the post-passes: each activation as it happens, and the state row."""
        belief = getattr(self, "_v501_belief", None)
        if belief is None:
            return
        self._v501_refresh()
        tick = int(getattr(self, "_tick", 0) or 0)
        rows, self._v501_pending_rows = list(self._v501_pending_rows), []
        for row in rows:
            self._emit("V501_ACTIVATION", force=True, tick=tick, **row)
        view = getattr(self, "_v501_view", None)
        if view is None:
            return
        flipped = view.window_open != self._v501_window_reported
        if not flipped and not (tick > 0 and tick % V501_STATE_EVERY_TICKS == 0):
            return
        self._v501_window_reported = view.window_open
        cfg = getattr(state, "config", None)
        book_count = int(getattr(cfg, "book_count", 0) or 0) or len(getattr(state, "books", None) or {})
        now = int(self._v501_last_now or 0)
        gate = belief.gate_ts
        seen = len(self._v501_seen_activated)
        self._emit(
            "V501_ACTIVITY_STATE", force=True, tick=tick,
            v501_activity_version=V501_ACTIVITY_VERSION,
            history_start_ts=belief.history_start_ts, history_start_source=belief.history_start_source,
            gate_ts=gate, floor_ts=belief.floor_ts, rebases=belief.rebases,
            gate_open=int(view.gate_open), window_open=int(view.window_open),
            s_to_gate=None if gate is None else round((gate - now) / 1e9, 1),
            eligible_books=view.eligible, activated_eligible=view.activated_eligible,
            cold_eligible=len(view.cold),
            cliff_needed=cliff_needed(view.activated_eligible, view.eligible),
            activated_books_seen=seen,
            activity_mean_seen=round(seen / book_count, 4) if book_count > 0 else None,
            cold_books=sorted(view.cold)[:64], activation_value=V501_ACTIVATION_VALUE,
        )

    # ---- v5.0.2: dust liveness from inventory and terminal-order truth -------------------------------
    def _v502_count(self, name: str, n: int = 1) -> None:
        counts = getattr(self, "_v502_counts", None)
        if not isinstance(counts, dict):
            counts = {}
            self._v502_counts = counts
        counts[str(name)] = int(counts.get(str(name), 0) or 0) + int(n)

    def _v502_add_residue(self, book_id: int, amount: float) -> float:
        """Add BASE no lot carries to one book's residue.  Returns the book's new residue."""
        table = getattr(self, "_v502_residue", None)
        if not isinstance(table, dict):
            table = {}
            self._v502_residue = table
        bid = int(book_id)
        value = float(table.get(bid, 0.0) or 0.0) + float(amount)
        if abs(value) <= 1e-12:
            table.pop(bid, None)
            return 0.0
        table[bid] = value
        return value

    def _v502_residue_abs(self) -> float:
        table = getattr(self, "_v502_residue", None) or {}
        return float(sum(abs(float(value or 0.0)) for value in table.values()))

    def _v502_clip_tolerance(self, state=None) -> float:
        """F2: how far below the minimum order a venue position is still one clip; 0.0 when off."""
        if not bool(getattr(self, "research_v502_clip_recognition", True)):
            return 0.0
        decimals = getattr(self, "_a1961_base_decimals", None)
        if decimals is None:
            decimals = self._a196_volume_decimals(state)
        return float(clip_tolerance_base(decimals))

    def _v502_settle_flat_residue(self, book_id: int) -> str:
        """F1: a flat lifecycle ends with the tracker at exactly zero.

        The round trip has already closed inside the execution flat epsilon.  What the tracker still
        holds is not on the venue's grid, and a full entry on top of an opposite residue reads as
        dust.  It moves to the residue ledger, which every reader of local base adds back, so
        reconciliation is unchanged.  Float noise is dropped.  Returns the residue class.
        """
        if not bool(getattr(self, "research_v502_flat_residue", True)):
            return RESIDUE_NONE
        bid = int(book_id)
        table = getattr(self, "_open_positions", None)
        positions = table.get(bid) if hasattr(table, "get") else None
        if not positions:
            return RESIDUE_NONE
        net = float(self._position_tracker_snapshot(bid).net_qty)
        kind, amount = flat_residue(net, flat_eps=float(self._execution_flat_epsilon()))
        if kind == RESIDUE_NONE:
            return kind
        positions["longs"].clear()
        positions["shorts"].clear()
        if kind != RESIDUE_FLAT:
            self._v502_count("flat_noise_zeroed")
            return kind
        total = self._v502_add_residue(bid, amount)
        self._v502_count("flat_residue_moved")
        self._emit(
            "V502_FLAT_RESIDUE", force=True, tick=int(getattr(self, "_tick", 0) or 0),
            book=bid, residue=float(amount), book_residue=float(total),
            residue_abs=round(self._v502_residue_abs(), 8),
            v502_dust_liveness_version=V502_DUST_LIVENESS_VERSION,
        )
        return kind

    def _v502_note_compaction_refusal(self, book_id: int) -> int:
        """F3: the loss floor refused this book, so it waits a backoff like a failed attempt.

        The floor is untouched: the book is refused again when its turn comes back.  What changes is
        that a refusal no longer keeps the book first in line ahead of every book the floor would
        allow.  Returns the cooldown set, 0 when none.
        """
        try:
            bid = int(book_id)
            seen = getattr(self, "_v502_compaction_seen", None)
            if isinstance(seen, set):
                seen.add(bid)
            if not bool(getattr(self, "research_v502_compactor_turn", True)):
                return 0
            if not bool(getattr(self, "research_dust_compact_adaptive", True)):
                return 0
            learning = getattr(self, "_research_dust_compact_learning", None)
            if not isinstance(learning, dict):
                return 0
            streaks = getattr(self, "_v502_refusal_streak", None)
            if not isinstance(streaks, dict):
                streaks = {}
                self._v502_refusal_streak = streaks
            streak = int(streaks.get(bid, 0) or 0) + 1
            streaks[bid] = streak
            wait = refusal_cooldown_ticks(
                streak,
                base=int(getattr(self, "research_dust_compact_cooldown_ticks", 8) or 8),
                maximum=int(getattr(self, "research_dust_compact_max_cooldown_ticks", 40) or 40),
            )
            row = learning.setdefault(bid, {
                "attempts": 0, "successful_attempts": 0, "failure_streak": 0,
                "last_attempt_tick": -1, "last_success_attempt_tick": -1,
                "next_allowed_tick": 0,
            })
            tick = int(getattr(self, "_tick", 0) or 0)
            row["next_allowed_tick"] = max(int(row.get("next_allowed_tick", 0) or 0), tick + int(wait))
            self._v502_count("compactor_refusal_turns")
            return int(wait)
        except Exception:
            self._v502_errors = int(getattr(self, "_v502_errors", 0) or 0) + 1
            return 0

    def _v502_note_compaction_allowed(self, book_id: int) -> None:
        """F3: an allowed evaluation ends the book's refusal streak."""
        try:
            bid = int(book_id)
            seen = getattr(self, "_v502_compaction_seen", None)
            if isinstance(seen, set):
                seen.add(bid)
            streaks = getattr(self, "_v502_refusal_streak", None)
            if isinstance(streaks, dict):
                streaks.pop(bid, None)
        except Exception:
            self._v502_errors = int(getattr(self, "_v502_errors", 0) or 0) + 1

    def _v502_note_market_notice(self, notice, *, phase: str) -> bool:
        """F4: the venue has processed one of our market orders.  Acted on after update()."""
        if not bool(getattr(self, "research_v502_market_terminal", True)):
            return False
        if "MARKETORDERPLACEMENTEVENT" not in str(phase or "").upper():
            return False
        try:
            bid = int(getattr(notice, "bookId"))
        except (TypeError, ValueError, AttributeError):
            return False
        notes = getattr(self, "_v502_market_notices", None)
        if not isinstance(notes, set):
            notes = set()
            self._v502_market_notices = notes
        notes.add((bid, canonical_order_side(getattr(notice, "side", ""))))
        return True

    def _v502_release_market_terminal(self) -> int:
        """F4: free the book a processed market order still reserves while its position is open.

        Runs at the top of respond(), after update() has applied the state's fills, so the tracker
        already says whether the order closed the position.  A filled exit keeps the hold it had.
        Returns the reservations released.
        """
        notes = getattr(self, "_v502_market_notices", None)
        if not notes:
            return 0
        self._v502_market_notices = set()
        if not bool(getattr(self, "research_v502_market_terminal", True)):
            return 0
        ledger = self._direct_pending_ledger()
        eps = float(self._execution_flat_epsilon())
        # respond() has not advanced _tick yet: this state is tick + 1.
        tick = int(getattr(self, "_tick", 0) or 0) + 1
        released = 0
        for bid, side in sorted(notes):
            key, matches = unique_market_reservation(ledger, book_id=bid, side=side)
            if key is None:
                self._v502_count("market_notice_unmatched" if matches == 0 else "market_notice_ambiguous")
                continue
            net = float(self._position_tracker_snapshot(int(bid)).net_qty)
            if abs(net) < eps:
                self._v502_count("market_filled_kept")
                continue
            row = ledger.pop(key, None)
            if row is None:
                continue
            self._direct_emit_book_ownership_release(row=row, reason="V502_MARKET_TERMINAL")
            self._v502_count("market_terminal_released")
            released += 1
            self._emit(
                "V502_MARKET_TERMINAL", force=True, tick=tick, book=int(bid), side=side,
                net_base=net, submitted_tick=int(row.submitted_tick),
                held_ticks=max(0, tick - int(row.submitted_tick)),
                v502_dust_liveness_version=V502_DUST_LIVENESS_VERSION,
            )
        return released

    def _v502_service(self, state) -> None:
        """v5.0.2 telemetry: V502_DUST_STATE once per V502_STATE_EVERY_TICKS requests."""
        tick = int(getattr(self, "_tick", 0) or 0)
        if tick <= 0 or tick % V502_STATE_EVERY_TICKS != 0:
            return
        min_order = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        decimals = getattr(self, "_a1961_base_decimals", None)
        if decimals is None:
            decimals = self._a196_volume_decimals(state)
        tolerance = float(clip_tolerance_base(decimals))
        parked = getattr(self, "_research_parked_dust", None) or {}
        parked_abs = 0.0
        near_full = []
        for raw_id, info in parked.items():
            qty = abs(float((info or {}).get("net_base", 0.0) or 0.0))
            parked_abs += qty
            if min_order - qty <= tolerance + 1e-12:
                near_full.append(int(raw_id))
        seen = getattr(self, "_v502_compaction_seen", None)
        evaluated = sorted(seen) if isinstance(seen, set) else []
        if isinstance(seen, set):
            seen.clear()
        learning = getattr(self, "_research_dust_compact_learning", None) or {}
        cooling = sorted(
            int(book) for book in (getattr(self, "_v502_refusal_streak", None) or {})
            if int((learning.get(book) or {}).get("next_allowed_tick", 0) or 0) > tick
        )
        admission = getattr(self, "_a196_admission_last", None) or {}
        residue = getattr(self, "_v502_residue", None) or {}
        self._emit(
            "V502_DUST_STATE", force=True, tick=tick,
            v502_dust_liveness_version=V502_DUST_LIVENESS_VERSION,
            flat_residue=int(bool(getattr(self, "research_v502_flat_residue", True))),
            clip_recognition=int(bool(getattr(self, "research_v502_clip_recognition", True))),
            compactor_turn=int(bool(getattr(self, "research_v502_compactor_turn", True))),
            market_terminal=int(bool(getattr(self, "research_v502_market_terminal", True))),
            counts=dict(sorted((getattr(self, "_v502_counts", None) or {}).items())),
            residue_books=len(residue), residue_abs=round(self._v502_residue_abs(), 8),
            clip_tolerance=tolerance,
            parked_dust=len(parked), parked_dust_abs=round(parked_abs, 6),
            near_full_parked=len(near_full), near_full_books=sorted(near_full)[:32],
            compaction_books_evaluated=len(evaluated), compaction_books=evaluated[:32],
            refusal_cooling_books=cooling[:32],
            admission_final_slots=admission.get("final_slots"),
            admission_binding=admission.get("binding"),
            admission_dust_abs=admission.get("dust_abs"),
            admission_dust_exempt_abs=admission.get("dust_exempt_abs"),
            errors=int(getattr(self, "_v502_errors", 0) or 0),
        )

    # ---- v5.0.3 G1: the newcomer quiet gate ---------------------------------------------------

    def _v503_gate_evaluate(self, state) -> None:
        """Decide ONCE whether this registration is a newcomer whose first score must be a full one."""
        try:
            self._v501_refresh()
        except Exception:
            pass
        belief = getattr(self, "_v501_belief", None)
        opened, anchor_restored = False, None
        identity = getattr(self, "_research_session_identity", None)
        if identity is not None:
            try:
                opened, anchor_restored = restored_session(
                    (self._research_read_session(identity) or {}).get(V503_NEWCOMER_GATE_VERSION)
                )
            except Exception:
                opened, anchor_restored = False, None
        # v5.0.4 H2: a declared start is the operator's statement, not a sign of a track record, so it
        # is not passed as the belief's evidence; it replaces the inferred anchor instead.
        pin = getattr(self, "_v504_history_pin", None)
        pinned = pin is not None and getattr(pin, "start_ns", None) is not None
        evidence = prior_evidence(
            observations=getattr(self, "_research_realized_observations_by_book", {}),
            pnl_history=getattr(self, "realized_pnl_history", {}),
            pnl_events=getattr(self, "_research_realized_pnl_events_by_book", {}),
            round_trip_closes=getattr(self, "_research_round_trip_closes", 0),
            start_source=(
                None if belief is None or pinned else getattr(belief, "history_start_source", None)
            ),
            first_state_source=EVIDENCE_FIRST_STATE,
        )
        if pinned and bool(getattr(pin, "established", False)) and evidence is None:
            evidence = V504_EVIDENCE_DECLARED_ESTABLISHED
        if pinned:
            anchor = int(pin.start_ns)
        else:
            anchor = effective_anchor(
                anchor_restored,
                getattr(self, "_research_sim_start_ts", None) or int(getattr(state, "timestamp", 0) or 0),
            )
        self._v503_gate_anchor_ts = anchor
        self._v503_gate_ts = gate_timestamp(
            anchor, min_lookback_ns=KAPPA_MIN_LOOKBACK_NS, scoring_interval_ns=SCORING_INTERVAL_NS,
        )
        armed, reason = arm_decision(
            switch_on=bool(getattr(self, "research_v503_newcomer_gate", True)),
            restored_open=opened, evidence=evidence, gate_ts=self._v503_gate_ts,
        )
        self._v503_gate_armed = bool(armed)
        self._v503_gate_evidence = evidence
        self._v503_gate_open_reason = reason

    def _v503_gate_exposure(self, state) -> float:
        """Venue truth, not the tracker: a held agent must never sit on an unmanaged position."""
        try:
            nets = self._a195_venue_net_by_book(getattr(state, "books", None) or {})
        except Exception:
            return 0.0
        return exposure_abs(nets, eps=self._execution_flat_epsilon())

    def _v503_gate_response(self, state):
        """An empty response while the gate holds instructions, or None to run the frozen chain."""
        if getattr(self, "_v503_gate_armed", None) is None:
            self._v503_gate_evaluate(state)
        if not self._v503_gate_armed:
            self._v503_gate_report(state, held=False)
            return None
        now = int(getattr(state, "timestamp", 0) or 0)
        # A new simulation resets the clock to ~0 while the validator rebases the rounds it keeps
        # onto that clock (trade.shift_simulation_histories), so the gate moves by the same shift,
        # as the activity belief does.  A checkpoint rewind is shorter than REBASE_MIN_JUMP_NS and
        # simply delays the gate by its length.
        last = getattr(self, "_v503_gate_last_now", None)
        if last is not None and now <= last - REBASE_MIN_JUMP_NS:
            shift = now - last
            if self._v503_gate_ts is not None:
                self._v503_gate_ts += shift
            if self._v503_gate_anchor_ts is not None:
                self._v503_gate_anchor_ts += shift
            self._v503_gate_rebases = int(getattr(self, "_v503_gate_rebases", 0) or 0) + 1
        self._v503_gate_last_now = now
        reason = should_open(
            now=now, gate_ts=self._v503_gate_ts, exposure=self._v503_gate_exposure(state),
        )
        if reason is not None:
            self._v503_gate_armed = False
            self._v503_gate_open_reason = reason
            try:
                self._research_save_session(force=True)
            except Exception:
                pass
            self._v503_gate_report(state, held=False)
            return None
        self._v503_gate_quiet_requests = int(getattr(self, "_v503_gate_quiet_requests", 0) or 0) + 1
        self._v503_gate_report(state, held=True)
        return FinanceAgentResponse(agent_id=int(getattr(self, "uid", 0) or 0))

    def _v503_gate_report(self, state, *, held: bool) -> None:
        tick = int(getattr(self, "_tick", 0) or 0)
        armed = bool(getattr(self, "_v503_gate_armed", False))
        state_name = gate_state(armed=armed, open_reason=getattr(self, "_v503_gate_open_reason", None))
        key = (state_name, str(getattr(self, "_v503_gate_open_reason", "") or ""))
        due = key != getattr(self, "_v503_gate_reported", None)
        if not due and not (held and tick > 0 and tick % V503_GATE_EVERY_TICKS == 0):
            return
        self._v503_gate_reported = key
        now = int(getattr(state, "timestamp", 0) or 0)
        self._emit(
            "V503_QUIET_GATE", force=True, tick=tick,
            v503_newcomer_gate_version=V503_NEWCOMER_GATE_VERSION,
            newcomer_gate=int(bool(getattr(self, "research_v503_newcomer_gate", True))),
            gate_state=state_name, held=int(bool(held)),
            open_reason=getattr(self, "_v503_gate_open_reason", None),
            prior_evidence=getattr(self, "_v503_gate_evidence", None),
            anchor_ts=getattr(self, "_v503_gate_anchor_ts", None),
            gate_ts=getattr(self, "_v503_gate_ts", None),
            s_to_gate=seconds_to_gate(now=now, gate_ts=getattr(self, "_v503_gate_ts", None)),
            quiet_requests=int(getattr(self, "_v503_gate_quiet_requests", 0) or 0),
            rebases=int(getattr(self, "_v503_gate_rebases", 0) or 0),
            errors=int(getattr(self, "_v503_gate_errors", 0) or 0),
        )

    def _v503_gate_session_state(self) -> dict[str, Any]:
        """What the session file carries across a restart.

        Only an EVALUATED gate that genuinely opened -- on its clock, on exposure, or because it
        was restored open -- is persisted as open.  A save that lands before the first request has
        evaluated the gate, or a gate held off by the switch or a missing estimate, is persisted as
        not open, so a later restart can still arm it.  Prior evidence needs no persistence: the
        evidence itself persists.
        """
        armed = getattr(self, "_v503_gate_armed", None)
        reason = getattr(self, "_v503_gate_open_reason", None)
        opened = armed is False and reason in (OPEN_GATE_REACHED, OPEN_EXPOSURE, OPEN_RESTORED)
        return session_state(
            opened=opened,
            anchor=None if armed is None else getattr(self, "_v503_gate_anchor_ts", None),
            open_reason=reason,
        )

    # ---- v5.0.3 G3: the validator's FIFO -------------------------------------------------------

    def _match_trade_fifo(self, book_id, is_buy, quantity, price, fee, timestamp):
        """v5.0.3 G3: the validator's matcher, which prorates a fill's fee on a PARTIAL close.

        The inherited copy charged the whole fill's fee to the closing part, which at this venue's
        maker rebate overstates realized PnL -- the number Kappa, the PnL score and this agent's own
        rolling Kappa authority are all built on.  Off restores the inherited arithmetic.
        """
        if not bool(getattr(self, "research_v503_fifo_fee_exact", True)):
            return super()._match_trade_fifo(book_id, is_buy, quantity, price, fee, timestamp)
        self._v503_fifo_calls = int(getattr(self, "_v503_fifo_calls", 0) or 0) + 1
        return v503_match_trade_fifo(
            self._open_positions[int(book_id)],
            is_buy=bool(is_buy), quantity=float(quantity), price=float(price),
            fee=float(fee), timestamp=timestamp,
        )

    # ---- v5.0.3 G2/G4: the observatory ---------------------------------------------------------

    def _v503_recorder_handle(self):
        """The recorder, built on first use so it can name its files after the run."""
        if not bool(getattr(self, "research_v503_state_recorder", True)):
            return None
        recorder = getattr(self, "_v503_recorder", None)
        if recorder is None and not getattr(self, "_v503_recorder_started", False):
            self._v503_recorder_started = True
            directory = os.path.join(
                str(getattr(self, "research_output_dir", None) or self.output_dir), "observatory",
            )
            recorder = StateRecorder(
                directory, uid=getattr(self, "uid", None),
                depth_every=int(getattr(self, "_v503_recorder_depth_every", V503_DEPTH_EVERY)),
                max_bytes=int(getattr(self, "_v503_recorder_max_bytes", 0) or 0),
                budget_basis=(
                    BUDGET_DISK if bool(getattr(self, "research_v504_disk_budget", True)) else BUDGET_PAYLOAD
                ),
            )
            self._v503_recorder = recorder
        return recorder

    def _v503_service(self, state) -> None:
        """v5.0.3, once per request: capture the state, then the observatory and Kappa rows."""
        tick = int(getattr(self, "_tick", 0) or 0)
        recorder = self._v503_recorder_handle()
        if recorder is not None:
            recorder.capture(
                getattr(state, "books", None), tick=tick,
                ts=int(getattr(state, "timestamp", 0) or 0),
            )
            if tick > 0 and tick % V503_OBSERVATORY_EVERY_TICKS == 0:
                self._emit("V503_OBSERVATORY", force=True, tick=tick, **recorder.snapshot())
        if (
            bool(getattr(self, "research_v503_book_kappa_rows", True))
            and tick > 0 and tick % V503_BOOK_KAPPA_EVERY_TICKS == 0
        ):
            self._v503_emit_book_kappa(tick)

    def _v503_emit_book_kappa(self, tick: int) -> None:
        """Every scored book's Kappa, normalization and activity, as the mirror computed them.

        The validator publishes the same per-book numbers on its own metrics page, so this row is
        what makes the comparison exact instead of aggregate.
        """
        row = getattr(self, "_v500_last_score", None)
        if not row:
            return
        mirror = getattr(self, "_v500_last_books", None) or {}
        if not mirror:
            return
        books = []
        for book, values in sorted(mirror.items()):
            kappa = values.get("kappa")
            if kappa is None:
                continue
            books.append([
                int(book), round(float(kappa), 6),
                None if values.get("norm") is None else round(float(values["norm"]), 6),
                float(values.get("activity", 0.0) or 0.0), int(values.get("obs", 0) or 0),
            ])
        if not books:
            return
        self._emit(
            "V503_BOOK_KAPPA", force=True, tick=tick,
            v503_observatory_version=V503_OBSERVATORY_VERSION,
            kappa_score=row.get("kappa_score"), kappa_median=row.get("kappa_median"),
            scored_books=row.get("scored_books"), n_rounds=row.get("n_rounds"),
            books=books,
        )

    # ---- v5.0.4 H1: one session file per UID ---------------------------------------------------

    def _research_session_path(self, identity) -> str:
        path = super()._research_session_path(identity)
        if not bool(getattr(self, "research_v504_session_per_uid", True)):
            return path
        return uid_session_path(path, getattr(self, "uid", None))

    def _v504_read_session(self, identity):
        """The payload this UID restores from: its own file, or a legacy one the operator adopted."""
        if not bool(getattr(self, "research_v504_session_per_uid", True)):
            return super()._research_read_session(identity)
        uid = getattr(self, "uid", None)
        own = super()._research_read_session(identity)
        legacy_path = super()._research_session_path(identity)
        legacy_exists = os.path.isfile(legacy_path)
        mode = str(getattr(self, "research_v504_legacy_session", LEGACY_IGNORE) or LEGACY_IGNORE)
        legacy = None
        if mode == LEGACY_ADOPT and legacy_exists:
            try:
                with open(legacy_path, encoding="utf-8") as handle:
                    legacy = json.loads(handle.read())
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                legacy = {"schema": "invalid"}
        choice = choose_session(own, uid=uid, legacy=legacy, legacy_exists=legacy_exists, mode=mode)
        payload = choice.payload
        state = getattr(self, "_v504_mirror", None)
        if state is None:
            state = self._v504_mirror = new_mirror_state()
        if isinstance(payload, dict):
            kept = [
                value for value in (
                    mirror_restored_start(payload.get(V504_MIRROR_SESSION_KEY)),
                    restored_session(payload.get(V503_NEWCOMER_GATE_VERSION))[1],
                ) if value is not None
            ]
            state["restored_start"] = min(kept) if kept else None
        self._v504_session_choice = choice
        key = (choice.source, choice.own_verdict, choice.legacy_verdict, choice.owner_uid)
        if key != getattr(self, "_v504_session_reported", None):
            self._v504_session_reported = key
            self._emit(
                "V504_SESSION_OWNER", force=True, tick=int(getattr(self, "_tick", 0) or 0),
                v504_registration_identity_version=V504_REGISTRATION_IDENTITY_VERSION,
                uid=uid, source=choice.source, own_verdict=choice.own_verdict,
                legacy_verdict=choice.legacy_verdict, owner_uid=choice.owner_uid,
                own_file=os.path.basename(self._research_session_path(identity)),
                legacy_file=os.path.basename(legacy_path), legacy_exists=int(legacy_exists),
                legacy_mode=mode, legacy_mode_invalid=int(bool(getattr(self, "_v504_legacy_invalid", False))),
                restored_start_ts=state.get("restored_start"),
            )
        return payload

    # ---- v5.0.4 H2/H4: the history start and the score copy's rounds ---------------------------

    def _v504_resolve_pin(self, state, now: int) -> None:
        anchor = getattr(self, "_v504_history_anchor", None)
        if anchor is None or anchor.mode == MODE_INVALID:
            return
        pin = resolve_history_pin(
            anchor, simulation_id=extract_simulation_id(state), first_state_ns=now,
        )
        self._v504_history_pin = pin
        belief = getattr(self, "_v501_belief", None)
        if pin is not None and pin.start_ns is not None and belief is not None:
            belief.pin(pin.start_ns, pin.source)

    def _v504_service(self, state) -> None:
        """Once per request, before the gate: pin the declared start, then follow the state clock."""
        raw_now = getattr(state, "timestamp", None)
        if raw_now is None:
            return
        now = int(raw_now)
        mirror = self._v504_mirror
        if mirror["live_since"] is None:
            mirror["live_since"] = now
            mirror["last_now"] = now
            self._v504_resolve_pin(state, now)
            pin = getattr(self, "_v504_history_pin", None)
            start, source = resolve_start(
                declared=None if pin is None else pin.start_ns,
                restored=mirror["restored_start"], first_state=now,
            )
            mirror["start_ts"], mirror["start_source"] = start, source
            step = positive_step(getattr(getattr(state, "config", None), "publish_interval", None))
            mirror["step_ns"] = step
            # PnL is known from this process's first state, or from where this UID's own session file
            # began when it restored fills.  Rounds before that are scored as flat, and the row says so.
            mirror["pnl_known_from"] = now
            if bool(getattr(self, "research_v504_mirror_rounds", True)) and step is not None:
                events = getattr(self, "_research_realized_pnl_events_by_book", {}) or {}
                restored = rebuild_history(events, step_ns=step, phase_ns=now, before_ns=now)
                mirror["history"] = {
                    ts: books for ts, books in restored.items() if start is None or ts >= start
                }
                if any(events.values()) and mirror["restored_start"] is not None:
                    mirror["pnl_known_from"] = min(now, int(mirror["restored_start"]))
            return
        last = mirror["last_now"]
        if last is not None and now <= last - REBASE_MIN_JUMP_NS:
            # A new simulation: the validator moves this UID's rounds onto the new clock and keeps them.
            shift = now - last
            for key in ("start_ts", "live_since", "pnl_known_from"):
                if mirror[key] is not None:
                    mirror[key] += shift
            if mirror["history"]:
                mirror["history"] = rebase_keys(mirror["history"], shift, keep_from=now - KAPPA_LOOKBACK_NS)
            mirror["rebases"] += 1
        mirror["last_now"] = now

    def _v504_mirror_inputs(self, now_ts: int):
        """``(history, rounds, row fields)`` for the score copy; None with the switch off."""
        if not bool(getattr(self, "research_v504_mirror_rounds", True)):
            return None
        mirror = self._v504_mirror
        step, start = mirror.get("step_ns"), mirror.get("start_ts")
        if step is None or start is None:
            mirror["fallbacks"] += 1
            return None, None, {"mirror_rounds_basis": BASIS_OBSERVED}
        rounds = validator_rounds(start, now_ts, step_ns=step, lookback_ns=KAPPA_LOOKBACK_NS)
        restored = mirror.get("history") or {}
        if restored:
            floor = int(now_ts) - KAPPA_LOOKBACK_NS
            if min(restored) < floor:
                restored = {ts: books for ts, books in restored.items() if ts >= floor}
                mirror["history"] = restored
        history = merge_history(restored, getattr(self, "realized_pnl_history", {}) or {})
        return history, rounds, {
            "mirror_rounds_basis": BASIS_VALIDATOR_GRID,
            "mirror_start_ts": start,
            "mirror_start_source": mirror.get("start_source"),
            "mirror_restored_states": len(restored),
            "mirror_pnl_known_from": mirror.get("pnl_known_from"),
            "mirror_pnl_complete": int(mirror.get("pnl_known_from") is not None
                                       and int(mirror["pnl_known_from"]) <= max(int(start), int(rounds[0]))),
        }

    # ---- v6.0.0: short lots --------------------------------------------------------------------------
    def _v600_on(self) -> bool:
        return bool(getattr(self, "research_v600_short_lots", True))

    def _v600_tolerance(self) -> float:
        decimals = getattr(self, "_a1961_base_decimals", None)
        if decimals is None:
            decimals = getattr(self, "_research_volume_decimals", None)
        return float(v600_leftover_tolerance(decimals))

    def _v600_classify_qty(self, qty: float, *, eps: float, min_order: float) -> str:
        return v600_classify_position(
            qty, min_order=min_order, eps=eps,
            fraction=getattr(self, "research_v600_short_lot_min_fraction", V600_SHORT_LOT_FRACTION_DEFAULT),
            tolerance=self._v600_tolerance(), enabled=self._v600_on(),
        )

    def _v600_is_inherited_parked(self, book_id: int, qty: float) -> bool:
        """An inherited clip stays parked while the position is the one the seed rebuilt."""
        table = getattr(self, "_v600_inherited_parked", None) or {}
        size = table.get(int(book_id))
        if size is None:
            return False
        if abs(abs(float(qty)) - float(size)) <= self._v600_tolerance() + 1e-12:
            return True
        table.pop(int(book_id), None)
        self._v600_count("inherited_released")
        return False

    def _v600_counts_as_dust(self, book_id: int, qty: float, *, eps: float, min_order: float) -> bool:
        """The one dust test: admission, the live validator and the build loop all use it."""
        q = abs(float(qty))
        if not self._v600_on():
            return q > float(eps) and q + 1e-12 < float(min_order)
        if self._v600_is_inherited_parked(int(book_id), q):
            return True
        return self._v600_classify_qty(q, eps=float(eps), min_order=float(min_order)) == V600_CLASS_DUST

    def _v600_skip_management(self, book_id: int, qty_abs: float, *, eps: float, min_order: float) -> bool:
        skip = qty_abs > eps and self._v600_counts_as_dust(book_id, qty_abs, eps=eps, min_order=min_order)
        if self._v600_on() and not skip and qty_abs + 1e-12 < float(min_order) and qty_abs > eps:
            self._v600_count("short_lot_evaluations")
        return skip

    def _is_dust_qty(self, net_base: float) -> bool:
        if not self._v600_on():
            return super()._is_dust_qty(net_base)
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        abs_base = abs(float(net_base))
        eps = float(self._execution_flat_epsilon())
        return (
            bool(self.research_dust_safe_close)
            and min_size > 0.0
            and abs_base >= eps
            and abs_base + 1e-12 < min_size
            and self._v600_classify_qty(abs_base, eps=0.0, min_order=min_size) == V600_CLASS_DUST
        )

    def _dust_compaction_safe_for_any_fill(self, net_base: float) -> bool:
        """A short lot is never compacted; the proof condition is unchanged below the boundary."""
        if not super()._dust_compaction_safe_for_any_fill(net_base):
            return False
        if not self._v600_on():
            return True
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        return self._v600_classify_qty(abs(float(net_base)), eps=0.0, min_order=min_size) == V600_CLASS_DUST

    def _v600_executable_qty(self, qty, min_order):
        """A short lot's exit clip is the minimum order, so the risk state sees an executable size."""
        try:
            q = abs(float(qty))
            m = abs(float(min_order))
        except (TypeError, ValueError):
            return qty
        if not self._v600_on() or q + 1e-12 >= m:
            return qty
        if self._v600_classify_qty(q, eps=float(self._execution_flat_epsilon()), min_order=m) != V600_CLASS_SHORT_LOT:
            return qty
        return m

    def _v600_chooser_kwargs(self, exit_kwargs: dict) -> None:
        """Size a short lot as its exit clip for the exit chooser, in place.

        The frozen caller sets ``reduction_executable`` from ``qty >= min_order`` and the chooser
        refuses any size under the minimum, so a short lot would never get a taker exit or
        protection.  Its real exit is one minimum-order clip (``choose_reduce_quantity``).
        """
        if not self._v600_on():
            return
        try:
            qty = abs(float(exit_kwargs.get("inventory_qty", 0.0) or 0.0))
            min_order = abs(float(exit_kwargs.get("min_order", 0.25) or 0.25))
        except (TypeError, ValueError):
            return
        if qty + 1e-12 >= min_order:
            return
        cls = self._v600_classify_qty(qty, eps=float(self._execution_flat_epsilon()), min_order=min_order)
        if cls != V600_CLASS_SHORT_LOT:
            return
        exit_kwargs["inventory_qty"] = min_order
        exit_kwargs["is_dust"] = False
        exit_kwargs["reduction_executable"] = bool(exit_kwargs.get("valid_opposite_touch", True))
        self._v600_count("chooser_short_lot")

    def _v600_count(self, name: str, n: int = 1) -> None:
        counts = getattr(self, "_v600_counts", None)
        if not isinstance(counts, dict):
            counts = {}
            self._v600_counts = counts
        counts[str(name)] = int(counts.get(str(name), 0) or 0) + int(n)

    def _v600_note_inherited_clip(self, book_id: int, size: float) -> None:
        if not self._v600_on():
            return
        if getattr(self, "research_v600_inherited_short_lots", V600_INHERITED_PARK) == V600_INHERITED_EXIT:
            self._v600_count("inherited_exit")
            return
        table = getattr(self, "_v600_inherited_parked", None)
        if not isinstance(table, dict):
            table = {}
            self._v600_inherited_parked = table
        table[int(book_id)] = abs(float(size))
        self._v600_count("inherited_parked")
        self._v600_note_class(int(book_id), abs(float(size)), force_class=V600_CLASS_PARKED,
                              source=V600_SOURCE_INHERITED)

    def _v600_note_class(self, book_id: int, qty: float, *, force_class: str | None = None,
                         source: str | None = None) -> None:
        """Log a book's classification when it changes (sub-minimum positions only)."""
        if not self._v600_on():
            return
        bid = int(book_id)
        q = abs(float(qty))
        min_size = max(1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25))
        eps = float(self._execution_flat_epsilon())
        parked = getattr(self, "_v600_inherited_parked", None) or {}
        if force_class is not None:
            cls = force_class
        elif q <= eps:
            cls = V600_CLASS_FLAT
            # A flat book ends an inherited position (a new simulation resets every balance).
            if bid in parked:
                parked.pop(bid, None)
                self._v600_count("inherited_released")
        elif self._v600_is_inherited_parked(bid, q):
            cls = V600_CLASS_PARKED
        elif q + 1e-12 >= min_size:
            cls = V600_CLASS_FLAT
        else:
            cls = self._v600_classify_qty(q, eps=eps, min_order=min_size)
        table = getattr(self, "_v600_class", None)
        if not isinstance(table, dict):
            table = {}
            self._v600_class = table
        prior = table.get(bid, V600_CLASS_FLAT)
        if cls == prior:
            return
        if cls == V600_CLASS_FLAT:
            table.pop(bid, None)
        else:
            table[bid] = cls
        if cls == V600_CLASS_SHORT_LOT:
            self._v600_count("short_lot_births")
        if source is None:
            source = (V600_SOURCE_OFF_GRID if cls == V600_CLASS_SHORT_LOT
                      and min_size - q <= self._v600_tolerance() + 1e-12 else V600_SOURCE_LIVE)
        action = {V600_CLASS_SHORT_LOT: "EXIT_AS_LOT", V600_CLASS_DUST: "DUST_PATH",
                  V600_CLASS_PARKED: "PARKED", V600_CLASS_FLAT: "NONE"}.get(cls, "NONE")
        self._emit(
            "V600_SHORT_LOT", force=True, tick=int(getattr(self, "_tick", 0) or 0), book=bid,
            qty=round(q, 10), fraction=round(q / min_size, 6), prior=prior, cls=cls, source=source,
            action=action, v600_short_lots_version=V600_SHORT_LOTS_VERSION,
        )

    def _v600_note_own_fill(self, event, *, book_id: int, before: float, after: float) -> None:
        if not self._v600_on():
            return
        bid = int(book_id)
        fill_before = getattr(self, "_v600_fill_before", None)
        if not isinstance(fill_before, dict):
            fill_before = {}
            self._v600_fill_before = fill_before
        fill_before[bid] = float(before)
        min_size = max(1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25))
        pnl_before = float((getattr(self, "_direct_event_pnl_before", {}) or {}).get(bid, 0.0) or 0.0)
        pnl_after = float((getattr(self, "_pnl_tick_buffer", {}) or {}).get(bid, 0.0) or 0.0)
        done = v600_exit_from_fill(
            bid, before=before, after=after, price=getattr(event, "price", 0.0),
            realized_quote=pnl_after - pnl_before, min_order=min_size,
            eps=float(self._execution_flat_epsilon()),
            fraction=getattr(self, "research_v600_short_lot_min_fraction", V600_SHORT_LOT_FRACTION_DEFAULT),
            tolerance=self._v600_tolerance(),
        )
        if done is None:
            return
        self._v600_count("exits_filled")
        self._v600_realized_quote = float(getattr(self, "_v600_realized_quote", 0.0) or 0.0) + done.realized_quote
        bps = done.realized_bps
        if bps is not None:
            exit_bps = getattr(self, "_v600_exit_bps", None)
            if not isinstance(exit_bps, list):
                exit_bps = []
                self._v600_exit_bps = exit_bps
            exit_bps.append(float(bps))
            del exit_bps[:-500]

    def _v600_settle_leftover(self, book_id: int) -> None:
        """Move a short-lot exit's overshoot of at most two base units to the v5.0.2 residue ledger."""
        bid = int(book_id)
        before = (getattr(self, "_v600_fill_before", {}) or {}).pop(bid, None)
        if before is None or not self._v600_on():
            return
        table = getattr(self, "_open_positions", None)
        positions = table.get(bid) if hasattr(table, "get") else None
        if not positions:
            return
        net = float(self._position_tracker_snapshot(bid).net_qty)
        min_size = max(1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25))
        eps = float(self._execution_flat_epsilon())
        if self._v600_classify_qty(before, eps=eps, min_order=min_size) != V600_CLASS_SHORT_LOT:
            return
        amount = v600_leftover_to_residue(net, before=before, flat_eps=eps, tolerance=self._v600_tolerance())
        if amount is None:
            if abs(net) > eps and float(before) * net < 0.0:
                self._v600_count("leftover_dust")
            return
        positions["longs"].clear()
        positions["shorts"].clear()
        total = self._v502_add_residue(bid, amount)
        self._v600_count("leftover_residue")
        self._emit(
            "V600_LEFTOVER_RESIDUE", force=True, tick=int(getattr(self, "_tick", 0) or 0), book=bid,
            residue=float(amount), book_residue=float(total), before=float(before),
            v600_short_lots_version=V600_SHORT_LOTS_VERSION,
        )

    def _v600_telemetry(self, state) -> None:
        if not self._v600_on():
            return
        tick = int(getattr(self, "_tick", 0) or 0)
        if getattr(self, "_v600_state_reported", False) and not (tick > 0 and tick % V600_STATE_EVERY_TICKS == 0):
            return
        self._v600_state_reported = True
        classes = list((getattr(self, "_v600_class", {}) or {}).values())
        counts = dict(getattr(self, "_v600_counts", {}) or {})
        bps = list(getattr(self, "_v600_exit_bps", []) or [])
        min_size = max(1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25))
        self._emit(
            "V600_SHORT_LOT_STATE", force=True, tick=tick,
            v600_short_lots_version=V600_SHORT_LOTS_VERSION,
            fraction=float(getattr(self, "research_v600_short_lot_min_fraction", V600_SHORT_LOT_FRACTION_DEFAULT)),
            boundary=round(v600_short_lot_boundary(
                min_size, getattr(self, "research_v600_short_lot_min_fraction", V600_SHORT_LOT_FRACTION_DEFAULT),
            ), 8),
            inherited_mode=getattr(self, "research_v600_inherited_short_lots", V600_INHERITED_PARK),
            short_lots=sum(1 for c in classes if c == V600_CLASS_SHORT_LOT),
            dust_books=sum(1 for c in classes if c == V600_CLASS_DUST),
            inherited_parked=len(getattr(self, "_v600_inherited_parked", {}) or {}),
            counts=counts,
            exits_filled=int(counts.get("exits_filled", 0)),
            realized_quote=round(float(getattr(self, "_v600_realized_quote", 0.0) or 0.0), 6),
            exit_bps_median=None if not bps else round(v600_median(bps), 3),
            exit_bps_worst=None if not bps else round(min(bps), 3),
            invalid_fraction=int(bool(getattr(self, "_v600_fraction_invalid", False))),
            invalid_inherited=int(bool(getattr(self, "_v600_inherited_invalid", False))),
            errors=int(getattr(self, "_v600_errors", 0) or 0),
        )

    # ---- v6.0.1: workable-dust reserve ------------------------------------------------------
    def _v601_on(self) -> bool:
        return bool(getattr(self, "research_v601_workable_dust_reserve", True))

    def _v601_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v601_counts", None)
        if counts is None:
            counts = {}
            self._v601_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v601_is_workable(self, book_id: int, qty: float, *, min_order: float) -> bool:
        """A dust book the normalizer can work.  Called only for books already counted as dust."""
        try:
            parked = int(book_id) in (getattr(self, "_v600_inherited_parked", None) or {})
            return v601_is_workable_dust(qty, is_dust=True, parked=parked, min_order=min_order)
        except Exception:
            self._v601_errors = int(getattr(self, "_v601_errors", 0) or 0) + 1
            return True

    def _v601_reserve_dust(self, diag, dust_count: int) -> int:
        """The dust count the admission reserve uses this request."""
        dust = max(0, int(dust_count or 0))
        workable = (diag or {}).get("v601_workable_dust_inventory")
        if workable is None:
            # No count from the screen: hold the reserve as v6.0.0 did.
            workable = dust
        n = v601_reserve_dust_count(enabled=self._v601_on(), dust_count=dust, workable_count=workable)
        self._v601_count("samples")
        if dust > 0:
            self._v601_count("dust_present")
        if n > 0:
            self._v601_count("reserve_held")
        elif dust > 0:
            self._v601_count("reserve_released")
        self._v601_last = {
            "v601_dust_books": int(dust),
            "v601_workable_dust_books": int(workable),
            "v601_reserve_dust_books": int(n),
        }
        return int(n)

    def _v601_telemetry(self, state) -> None:
        tick = int(getattr(self, "_tick", 0) or 0)
        if getattr(self, "_v601_state_reported", False) and not (tick > 0 and tick % V601_STATE_EVERY_TICKS == 0):
            return
        self._v601_state_reported = True
        counts = dict(getattr(self, "_v601_counts", {}) or {})
        last = dict(getattr(self, "_v601_last", {}) or {})
        self._emit(
            "V601_RESERVE_STATE", force=True, tick=tick,
            v601_capacity_version=V601_CAPACITY_VERSION,
            enabled=int(self._v601_on()),
            dust_books=int(last.get("v601_dust_books", 0)),
            workable_dust_books=int(last.get("v601_workable_dust_books", 0)),
            reserve_dust_books=int(last.get("v601_reserve_dust_books", 0)),
            inherited_parked=len(getattr(self, "_v600_inherited_parked", {}) or {}),
            counts=counts,
            errors=int(getattr(self, "_v601_errors", 0) or 0),
            # v6.0.2: the effective caps after the frozen Research clamps.
            max_active_books=int(getattr(self, "research_max_active_open_books", 0) or 0),
            max_open_books=int(getattr(self, "research_max_total_open_books", 0) or 0),
            max_abs_base=float(getattr(self, "research_max_total_abs_base", 0.0) or 0.0),
        )

    # ---- v6.0.3: the recovery handler releases a short lot after its hold -------------------
    def _v603_on(self) -> bool:
        return bool(getattr(self, "research_v603_short_lot_release", True))

    def _v603_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v603_counts", None)
        if counts is None:
            counts = {}
            self._v603_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v603_is_short_lot(self, book_id: int, qty: float, *, eps: float, min_order: float) -> bool:
        """The v6.0.0 predicate, read the way this handler needs it: a lot, not dust."""
        return not self._v600_counts_as_dust(
            int(book_id), abs(float(qty)), eps=float(eps), min_order=float(min_order)
        )

    def _v603_disposition(
        self, book_id: int, net: float, *, eps: float, min_order: float,
        hold_active: bool, bound: bool,
    ) -> str:
        """What this request does with one recovery row.  Any failure keeps v6.0.2 behaviour."""
        try:
            counts_as_dust = self._v600_counts_as_dust(
                int(book_id), abs(float(net)), eps=float(eps), min_order=float(min_order)
            )
            disposition = v603_row_disposition(
                enabled=self._v603_on(), hold_active=bool(hold_active), bound=bool(bound),
                counts_as_dust=bool(counts_as_dust),
            )
        except Exception:
            self._v603_errors = int(getattr(self, "_v603_errors", 0) or 0) + 1
            return V603_DISPOSITION_LEGACY
        self._v603_count("rows_seen")
        self._v603_count("disposition_%s" % str(disposition).lower())
        return disposition

    def _v603_note_release(
        self, book_id: int, net: float, *, mode: str, desired_side: str, hold_active: bool,
    ) -> None:
        """One short lot handed back to the v6.0.0 lot exit.  No cancel is sent with it."""
        try:
            self._direct_partial_hold_releases = int(
                getattr(self, "_direct_partial_hold_releases", 0) or 0
            ) + 1
            self._direct_v603_short_lot_releases = int(
                getattr(self, "_direct_v603_short_lot_releases", 0) or 0
            ) + 1
            self._v603_count("short_lot_releases")
            self._v603_last = {
                "book": int(book_id),
                "net_base": float(net),
                "tick": int(getattr(self, "_tick", 0) or 0),
            }
            self._emit(
                "V603_PARTIAL_RELEASE", force=True,
                tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                mode=str(mode), desired_side=str(desired_side), net_base=float(net),
                hold_expired=int(not bool(hold_active)), cancelled_orders=0,
            )
        except Exception:
            self._v603_errors = int(getattr(self, "_v603_errors", 0) or 0) + 1

    def _v603_note_cancel(
        self, book_id: int, net: float, *, eps: float, min_order: float, disposition: str,
    ) -> None:
        """Count a remainder cancel that landed on a short lot (gate R1 reads this as 0)."""
        try:
            if str(disposition) == V603_DISPOSITION_HOLD:
                return
            if self._v603_is_short_lot(int(book_id), net, eps=eps, min_order=min_order):
                self._v603_count("short_lot_cancels")
        except Exception:
            self._v603_errors = int(getattr(self, "_v603_errors", 0) or 0) + 1

    def _v603_open_short_lot_rows(self) -> int:
        """Recovery rows still owning a short lot.  Outside a live bound hold this must be 0."""
        rows = getattr(self, "_direct_partial_recovery", None) or {}
        if not rows:
            return 0
        eps = float(self._execution_flat_epsilon())
        min_size = max(
            1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        )
        held = 0
        for book_id in list(rows.keys()):
            net = float(self._direct_signed_inventory(int(book_id)))
            if abs(net) <= eps:
                continue
            if self._v603_is_short_lot(int(book_id), net, eps=eps, min_order=min_size):
                held += 1
        return int(held)

    def _v603_telemetry(self, state) -> None:
        tick = int(getattr(self, "_tick", 0) or 0)
        if getattr(self, "_v603_state_reported", False) and not (tick > 0 and tick % V603_STATE_EVERY_TICKS == 0):
            return
        self._v603_state_reported = True
        counts = dict(getattr(self, "_v603_counts", {}) or {})
        cancels_total = int(counts.get("short_lot_cancels", 0) or 0)
        since = cancels_total - int(getattr(self, "_v603_cancels_reported", 0) or 0)
        self._v603_cancels_reported = cancels_total
        self._emit(
            "V603_STATE", force=True, tick=tick,
            v603_recovery_version=V603_RECOVERY_VERSION,
            enabled=int(self._v603_on()),
            releases=int(getattr(self, "_direct_v603_short_lot_releases", 0) or 0),
            short_lot_rows_open=self._v603_open_short_lot_rows(),
            recovery_rows_open=len(getattr(self, "_direct_partial_recovery", {}) or {}),
            short_lot_cancels_since=int(max(0, since)),
            short_lot_cancels_total=cancels_total,
            last_release=dict(getattr(self, "_v603_last", {}) or {}),
            counts=counts,
            errors=int(getattr(self, "_v603_errors", 0) or 0),
        )

    # ---- v6.1: no realized loss ----------------------------------------------------------------
    def _v61_on(self) -> bool:
        return bool(getattr(self, "research_v61_no_loss", True))

    def _v61_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v61_counts", None)
        if counts is None:
            counts = {}
            self._v61_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v61_price_decimals(self, state) -> int:
        return int(getattr(getattr(state, "config", None), "priceDecimals", 2) or 2)

    def _v61_positions(self, book_id: int):
        """This book's FIFO deques, the validator's own structure; None when there are none."""
        table = getattr(self, "_open_positions", None)
        if table is None:
            return None
        try:
            return table.get(int(book_id))
        except (AttributeError, TypeError, ValueError):
            return None

    def _v61_taker_verdict(self, book_id: int, book, qty: float, long_pos: bool) -> tuple[bool, dict]:
        """Whether a market close of ``qty`` at the touch the frozen path crosses realizes >= 0."""
        try:
            touch = float(book.bids[0].price if long_pos else book.asks[0].price)
        except (TypeError, ValueError, IndexError, AttributeError):
            return True, {}
        if touch <= 0.0:
            return True, {}
        fee = float(self._research_live_fee_bps(int(book_id), is_maker=False) or 0.0)
        pnl, closed = v61_taker_close_pnl(
            self._v61_positions(book_id), qty=float(qty), touch_price=touch,
            taker_fee_bps=fee, long_position=bool(long_pos),
        )
        ok = closed <= 0.0 or pnl >= 0.0
        return ok, {
            "touch": touch, "taker_fee_bps": fee, "fifo_pnl": round(float(pnl), 8),
            "closed_qty": round(float(closed), 8),
        }

    def _v61_note_refusal(self, book_id: int, qty: float, long_pos: bool, detail: dict) -> None:
        self._v61_count("taker_refused")
        self._direct_v61_taker_refusals = int(getattr(self, "_direct_v61_taker_refusals", 0) or 0) + 1
        tick = int(getattr(self, "_tick", 0) or 0)
        row = dict(detail)
        row.update(book=int(book_id), qty=float(qty), long_position=int(bool(long_pos)), tick=tick)
        self._v61_last = row
        self._emit(
            "V61_TAKER_REFUSED", force=True, tick=tick, book=int(book_id),
            v61_lot_floor_version=V61_LOT_FLOOR_VERSION,
            qty=float(qty), long_position=int(bool(long_pos)),
            **{k: v for k, v in detail.items()},
        )

    def _v61_compaction_price_ok(self, book_id: int, net_base: float, price: float, state) -> bool:
        """The compaction clip FIFO-closes the head lot: refuse it below the break-even."""
        # v6.2.3: outside the Kappa-3 premium branch the zero floor is lifted (the frozen A1.7.4.2
        # dust budget still applies upstream).
        if self._v623_lifted(int(book_id), state)[0]:
            self._v623_count("lifted_compaction")
            return True
        long_pos = float(net_base) > 0.0
        lot = v61_head_lot(self._v61_positions(book_id), long_position=long_pos)
        if lot is None:
            return True
        fee = float(self._research_live_fee_bps(int(book_id), is_maker=True) or 0.0)
        floor = v61_floor_price(
            lot, close_fee_bps=fee, long_position=long_pos, target_bps=0.0,
            price_decimals=self._v61_price_decimals(state),
        )
        if floor is None:
            return True
        ok = (float(price) + 1e-12 >= floor) if long_pos else (float(price) - 1e-12 <= floor)
        if not ok:
            self._v61_count("compaction_refused")
            self._direct_v61_compaction_refusals = int(
                getattr(self, "_direct_v61_compaction_refusals", 0) or 0
            ) + 1
            self._emit(
                "V61_COMPACTION_REFUSED", force=True, tick=int(getattr(self, "_tick", 0) or 0),
                book=int(book_id), v61_lot_floor_version=V61_LOT_FLOOR_VERSION,
                net_base=float(net_base), clip_price=float(price), floor_price=float(floor),
                maker_fee_bps=fee, lot_price=float(lot[2]), lot_qty=float(lot[1]), lot_fee=float(lot[3]),
            )
        return ok

    def _v61_floor_for(self, book_id: int, long_pos: bool, *, state=None, target_bps=None):
        """The fee-inclusive FIFO break-even of the lot a close would hit, on the price grid."""
        lot = v61_head_lot(self._v61_positions(book_id), long_position=bool(long_pos))
        if lot is None:
            return None
        fee = float(self._research_live_fee_bps(int(book_id), is_maker=True) or 0.0)
        decimals = 2 if state is None else self._v61_price_decimals(state)
        target = DIRECT_MAKER_EXIT_TARGET_BPS if target_bps is None else float(target_bps)
        return v61_floor_price(
            lot, close_fee_bps=fee, long_position=bool(long_pos), target_bps=target,
            price_decimals=decimals,
        )

    def _v61_apply_floor(self, book_id: int, state, inventory, qty, close_price, action):
        """Floor a maker exit at the lot's fee-inclusive break-even; return (price, net_bps).

        The A1.9.1 classifier compares a resting order against `desired_price` and cancels it as
        STALE_BEHIND_TOUCH once it drifts three ticks, so the floor has to be applied here --
        before the classifier sees the price -- or a floored order is cancelled every request
        (1,998 such cancels in one mainnet run).  `maker_net_bps` is recomputed at the floored
        price so the A1.7.4 negative-aggressive guard judges the order actually being sent.
        """
        long_pos = float(getattr(inventory, "net_base", 0.0) or 0.0) > 0.0
        lot = v61_head_lot(self._v61_positions(book_id), long_position=long_pos)
        if lot is None:
            return close_price, None, False
        floor = self._v61_floor_for(int(book_id), long_pos, state=state)
        if floor is None:
            return close_price, None, False
        priced = v61_floored_close_price(close_price, floor, long_position=long_pos)
        if close_price is not None and abs(float(priced) - float(close_price)) <= 1e-12:
            return close_price, None, False
        # v6.2.3: the floor binds here.  On a book outside the Kappa-3 premium branch it is lifted and
        # the exit keeps the price the chooser asked for.
        if self._v623_release(
            int(book_id), state, lot=lot, floor=floor, close_price=close_price,
            long_pos=long_pos, qty=qty, action=action,
        ):
            return close_price, None, False
        fee = float(self._research_live_fee_bps(int(book_id), is_maker=True) or 0.0)
        net = v61_fifo_close_net_bps(
            lot, close_price=priced, close_fee_bps=fee, long_position=long_pos,
            qty=abs(float(qty or 0.0)) or None,
        )
        self._v61_count("floored_placements")
        self._emit(
            "V61_EXIT_FLOORED", force=True, tick=int(getattr(self, "_tick", 0) or 0),
            book=int(book_id), v61_lot_floor_version=V61_LOT_FLOOR_VERSION,
            action=str(action or ""), requested_price=(None if close_price is None else float(close_price)),
            floor_price=float(floor), placed_price=float(priced),
            maker_fee_bps=fee, floored_net_bps=(None if net != net else round(float(net), 4)),
            lot_price=float(lot[2]), lot_qty=float(lot[1]), lot_open_fee=float(lot[3]),
            long_position=int(bool(long_pos)),
        )
        return float(priced), (None if net != net else float(net)), True

    def _v61_rewrite_exit(self, book_id: int, decision, *, book, inventory, exit_kwargs):
        """v6.1: nothing that would realize a FIFO loss, and never an idle full lot."""
        if not self._v61_on() or decision is None or book_id < 0:
            return decision, V61_REWRITE_NONE
        qty = float(exit_kwargs.get("inventory_qty", 0.0) or 0.0)
        if qty == 0.0:
            qty = float(getattr(inventory, "net_base", 0.0) or 0.0)
        long_pos = float(getattr(inventory, "net_base", qty) or qty) > 0.0
        floor = self._v61_floor_for(int(book_id), long_pos)
        action = str(getattr(decision, "action", "") or "")
        acceptable = True
        if action == ACTION_TAKER_EXIT and book is not None:
            acceptable, _detail = self._v61_taker_verdict(
                int(book_id), book, abs(float(getattr(decision, "selected_qty", qty) or qty)), long_pos,
            )
        rewrite = v61_rewrite_exit_action(
            enabled=True, action=action, taker_acceptable=bool(acceptable),
            floor_available=floor is not None, inventory_qty=qty,
            min_order=float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25),
        )
        if rewrite == V61_REWRITE_NONE:
            return decision, rewrite
        self._v61_count("rewrite_" + rewrite.lower())
        rewritten = replace(
            decision, action=ACTION_MAKER_EXIT,
            selected_qty=abs(float(qty)) or float(getattr(decision, "selected_qty", 0.0) or 0.0),
            reason="V61_FLOOR_MAKER",
        )
        self._emit(
            "V61_EXIT_REWRITE", force=True, tick=int(getattr(self, "_tick", 0) or 0),
            book=int(book_id), v61_lot_floor_version=V61_LOT_FLOOR_VERSION,
            rewrite=rewrite, from_action=action, to_action=ACTION_MAKER_EXIT,
            floor_price=float(floor), inventory_qty=float(qty),
            taker_acceptable=int(bool(acceptable)),
            maker_net_bps=float(exit_kwargs.get("maker_net_bps", 0.0) or 0.0),
            taker_net_bps=float(exit_kwargs.get("taker_net_bps", 0.0) or 0.0),
            prior_reason=str(getattr(decision, "reason", "") or ""),
        )
        return rewritten, rewrite

    def _v61_lots_session_state(self) -> dict:
        """The FIFO deques as the session file stores them: ``{book: {side: [[ts, qty, price, fee]]}}``."""
        table = getattr(self, "_open_positions", None) or {}
        books: dict[str, dict[str, list]] = {}
        for raw_id, pos in table.items():
            row: dict[str, list] = {}
            for side in ("longs", "shorts"):
                lots = []
                for lot in (pos or {}).get(side, ()) or ():
                    try:
                        ts, qty, price, fee = lot
                    except (TypeError, ValueError):
                        continue
                    if float(qty) > 0.0:
                        lots.append([int(ts), float(qty), float(price), float(fee)])
                if lots:
                    row[side] = lots
            if row:
                try:
                    books[str(int(raw_id))] = row
                except (TypeError, ValueError):
                    continue
        return {
            "version": V61_LOT_FLOOR_VERSION, "tick": int(getattr(self, "_tick", 0) or 0), "books": books,
        }

    def _v61_lots_from_session(self, raw) -> dict[int, dict[str, list]]:
        out: dict[int, dict[str, list]] = {}
        books = raw.get("books") if isinstance(raw, dict) else None
        if not isinstance(books, dict):
            return out
        for key, row in books.items():
            try:
                bid = int(key)
            except (TypeError, ValueError):
                continue
            sides: dict[str, list] = {}
            for side in ("longs", "shorts"):
                lots = []
                for lot in (row or {}).get(side) or ():
                    try:
                        ts, qty, price, fee = lot
                        lots.append((int(ts), float(qty), float(price), float(fee)))
                    except (TypeError, ValueError):
                        continue
                if lots:
                    sides[side] = lots
            if sides:
                out[bid] = sides
        return out

    def _v61_restored_side(self, book_id: int, side: str, target_qty: float) -> list:
        """The restored lots for this side while they still add up to the venue's position; else []."""
        if not self._v61_on():
            return []
        table = getattr(self, "_v61_restored_lots", None)
        if not isinstance(table, dict):
            return []
        lots = (table.get(int(book_id)) or {}).get(side) or []
        if not lots:
            return []
        table.pop(int(book_id), None)
        total = sum(float(q) for _ts, q, _p, _f in lots)
        tolerance = max(float(self._v600_tolerance()), 1e-9)
        if abs(total - float(target_qty)) > tolerance:
            self._v61_count("lots_restore_mismatch")
            return []
        self._v61_count("lots_restored", len(lots))
        return [tuple(lot) for lot in lots]

    def _v61_telemetry(self, state) -> None:
        tick = int(getattr(self, "_tick", 0) or 0)
        if getattr(self, "_v61_state_reported", False) and not (tick > 0 and tick % V61_STATE_EVERY_TICKS == 0):
            return
        self._v61_state_reported = True
        counts = dict(getattr(self, "_v61_counts", {}) or {})
        self._emit(
            "V61_STATE", force=True, tick=tick,
            v61_lot_floor_version=V61_LOT_FLOOR_VERSION,
            enabled=int(self._v61_on()),
            taker_refusals=int(getattr(self, "_direct_v61_taker_refusals", 0) or 0),
            compaction_refusals=int(getattr(self, "_direct_v61_compaction_refusals", 0) or 0),
            restored_lots_pending=len(getattr(self, "_v61_restored_lots", {}) or {}),
            last_refusal=dict(getattr(self, "_v61_last", {}) or {}),
            counts=counts,
            errors=int(getattr(self, "_v61_errors", 0) or 0),
        )

    # ---- v6.1: a skipped state lost its fills ------------------------------------------------------
    def _v61_gap_on(self) -> bool:
        return bool(getattr(self, "research_v61_state_gap_repair", True))

    def _v61_gap_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v61_gap_counts", None)
        if counts is None:
            counts = {}
            self._v61_gap_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v61_observe_state_step(self, state) -> None:
        """Classify this state's clock step against the last state's, before it is ingested.

        A gap arms the repair; a repeat is only counted -- its notices are the last state's, which
        the de-duplicator drops, and nothing is lost until the gap that follows it.  A rewind is
        A1.9.9's (it clears the registries and opens its own resync).
        """
        try:
            ts = int(getattr(state, "timestamp", 0) or 0)
        except (TypeError, ValueError):
            return
        if ts <= 0:
            return
        step_ns = getattr(getattr(state, "config", None), "publish_interval", None) or V61_DEFAULT_STEP_NS
        prev = getattr(self, "_v61_last_state_ts", None)
        step, missing = v61_classify_state_step(prev, ts, step_ns=step_ns)
        self._v61_last_state_ts = ts
        if step not in (V61_STEP_GAP, V61_STEP_REPEAT):
            return
        # `Strategy1.respond` increments _tick first: this state is tick + 1.
        tick = int(getattr(self, "_tick", 0) or 0) + 1
        armed = 0
        if step == V61_STEP_REPEAT:
            self._v61_gap_count("repeats")
            self._direct_v61_state_repeats = int(getattr(self, "_direct_v61_state_repeats", 0) or 0) + 1
        else:
            self._v61_gap_count("gaps")
            self._v61_gap_count("missing_states", int(missing))
            self._direct_v61_state_gaps = int(getattr(self, "_direct_v61_state_gaps", 0) or 0) + 1
            if self._v61_gap_on():
                self._v61_gap_pending = {
                    "tick": tick, "prev_ts": int(prev), "ts": int(ts), "missing": int(missing),
                }
                armed = 1
            self._v61_last_gap = {"tick": tick, "prev_ts": int(prev), "ts": int(ts), "missing": int(missing)}
        self._emit(
            "V61_STATE_STEP", force=True, tick=tick, v61_state_gap_version=V61_STATE_GAP_VERSION,
            step=step, prev_ts=int(prev), ts=int(ts), missing_states=int(missing), repair_armed=armed,
        )

    def _v61_service_gap_repair(self, state) -> None:
        """Before the A1.9.9 resync is serviced: arm it for one pass, confirm standing divergences."""
        self._v61_gap_window = None
        self._v61_standing_new = []
        if not self._v61_gap_on():
            self._v61_gap_pending = None
            return
        # The same convention `_a199_service_resync` uses: this state is tick + 1.
        tick = int(getattr(self, "_tick", 0) or 0) + 1
        before = int(getattr(self, "_a199_epoch_reseeds", 0) or 0)
        pending = getattr(self, "_v61_gap_pending", None)
        if pending:
            self._v61_gap_pending = None
            if getattr(self, "_a199_resync", None):
                # A rewind resync is already open and replans every book on its own.
                self._v61_gap_count("gap_inside_resync")
            else:
                # min == max == this state: `_a199_service_resync` plans every book against venue
                # truth, rebuilds the diverged ones and closes the window before any decision
                # reads it -- a gap never blocks an entry.
                self._a199_resync = {
                    "since_tick": tick, "min_until_tick": tick, "max_until_tick": tick,
                    "old_ts": int(pending["prev_ts"]), "new_ts": int(pending["ts"]),
                    "reseeds": 0, "entries_blocked": 0, "placements_stripped": 0,
                    "cause": "V61_STATE_GAP",
                }
                self._v61_gap_window = dict(pending, opened_tick=tick, reseeds_before=before)
                self._v61_gap_count("repairs")
        if tick % V61_DIVERGENCE_CHECK_EVERY_TICKS != 0:
            return
        books = getattr(state, "books", None) or {}
        venue = self._a195_venue_net_by_book(books)
        tracker: dict[int, float] = {}
        for book_id in venue:
            try:
                tracker[int(book_id)] = float(self._position_tracker_snapshot(int(book_id)).net_qty)
            except Exception:
                continue
        min_order = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        current = v61_diverged_books(venue, tracker, min_order=min_order)
        confirmed = v61_confirmed_divergence(getattr(self, "_v61_diverged_prev", None), current)
        self._v61_diverged_prev = current
        self._v61_gap_count("checks")
        if not confirmed:
            return
        deferred = getattr(self, "_a199_deferred", None)
        if not isinstance(deferred, dict):
            deferred = {}
            self._a199_deferred = deferred
        added = []
        for book_id in sorted(confirmed):
            if book_id in deferred:
                continue
            deferred[book_id] = {
                "since_tick": tick, "target": float(venue[book_id]), "venue_net": float(venue[book_id]),
                "cause": "V61_PERSISTENT_DIVERGENCE",
            }
            added.append(int(book_id))
        if added:
            self._v61_standing_new = added
            self._v61_gap_count("standing_books", len(added))
            self._v61_standing_before = before
            self._emit(
                "V61_STANDING_DIVERGENCE", force=True, tick=tick,
                v61_state_gap_version=V61_STATE_GAP_VERSION, books=added,
                venue={int(b): round(float(venue[b]), 8) for b in added},
                tracker={int(b): round(float(tracker.get(b, 0.0)), 8) for b in added},
            )

    def _v61_note_gap_repair(self) -> None:
        """After the A1.9.9 service: how many books the gap pass or the standing check rebuilt."""
        window = getattr(self, "_v61_gap_window", None)
        added = getattr(self, "_v61_standing_new", None) or []
        if not window and not added:
            return
        after = int(getattr(self, "_a199_epoch_reseeds", 0) or 0)
        tick = int(getattr(self, "_tick", 0) or 0) + 1
        if window:
            reseeds = max(0, after - int(window.get("reseeds_before", after)))
            self._direct_v61_gap_reseeds = int(getattr(self, "_direct_v61_gap_reseeds", 0) or 0) + reseeds
            self._v61_gap_count("gap_reseeds", reseeds)
            self._emit(
                "V61_GAP_REPAIR", force=True, tick=tick, v61_state_gap_version=V61_STATE_GAP_VERSION,
                prev_ts=int(window.get("prev_ts", 0)), ts=int(window.get("ts", 0)),
                missing_states=int(window.get("missing", 0)), reseeds=int(reseeds),
                window_closed=int(not bool(getattr(self, "_a199_resync", None))),
            )
        elif added:
            # Not `before or after`: a count of 0 is a count, not a missing value.
            before = getattr(self, "_v61_standing_before", None)
            reseeds = max(0, after - int(before)) if before is not None else 0
            self._direct_v61_standing_reseeds = int(
                getattr(self, "_direct_v61_standing_reseeds", 0) or 0
            ) + reseeds
            self._v61_gap_count("standing_reseeds", reseeds)
        self._v61_gap_window = None
        self._v61_standing_new = []

    def _v61_gap_telemetry(self, state) -> None:
        tick = int(getattr(self, "_tick", 0) or 0)
        if getattr(self, "_v61_gap_state_reported", False) and not (tick > 0 and tick % V61_GAP_STATE_EVERY_TICKS == 0):
            return
        self._v61_gap_state_reported = True
        self._emit(
            "V61_GAP_STATE", force=True, tick=tick, v61_state_gap_version=V61_STATE_GAP_VERSION,
            enabled=int(self._v61_gap_on()),
            gaps=int(getattr(self, "_direct_v61_state_gaps", 0) or 0),
            repeats=int(getattr(self, "_direct_v61_state_repeats", 0) or 0),
            gap_reseeds=int(getattr(self, "_direct_v61_gap_reseeds", 0) or 0),
            standing_reseeds=int(getattr(self, "_direct_v61_standing_reseeds", 0) or 0),
            deferred_books=len(getattr(self, "_a199_deferred", None) or {}),
            last_gap=dict(getattr(self, "_v61_last_gap", {}) or {}),
            counts=dict(getattr(self, "_v61_gap_counts", {}) or {}),
            request_memo=int(self._v61_memo_on()),
            memo_hits=int(getattr(self, "_v61_memo_hits", 0) or 0),
            memo_misses=int(getattr(self, "_v61_memo_misses", 0) or 0),
            pnl_memo_builds=int(getattr(self, "_v61_pnl_memo_builds", 0) or 0),
            errors=int(getattr(self, "_v61_gap_errors", 0) or 0),
        )

    # ---- v6.1: request memo (behaviour-neutral) ---------------------------------------------------
    def _v61_memo_on(self) -> bool:
        return bool(getattr(self, "research_v61_request_memo", True))

    def _research_refresh_rolling_kappa_cache(self) -> None:
        """v6.1 request memo: the frozen refresh, once per request instead of once per book.

        The frozen method rebuilds its cache key by scanning the whole realized history -- a sum
        over every timestamp and a max over every key -- on EVERY call, hit or miss, and the fast
        screen calls it for all 128 books on every request (the expiry and universe reads add
        more).  Measured on UID 34 (v6.0.2): 2.4 ms per request at tick 1,000, 16.9 ms at tick
        8,000, growing until the 3 sim-h window fills; screen time 7.4 -> 40.7 ms over the run.

        Within one request neither object it reads can change.  `update()` replaces
        `realized_pnl_history` wholesale on every state (the prune rebuilds the dict right after
        the flush), and the persisted observation timestamps are only ever replaced, never
        mutated in place.  So a second call with the same two objects, clock and tick is the first
        call's early return, exactly.
        """
        if not self._v61_memo_on():
            return super()._research_refresh_rolling_kappa_cache()
        history = getattr(self, "realized_pnl_history", None)
        persisted = getattr(self, "_research_persisted_observation_timestamps", None)
        stamp = (
            int(getattr(self, "_tick", 0) or 0),
            getattr(self, "_research_last_sim_ts", None),
            getattr(self, "research_kappa_lookback_ns", None),
            len(history) if history is not None else -1,
        )
        memo = getattr(self, "_v61_kappa_memo", None)
        if memo is not None and memo[0] == stamp and memo[1] is history and memo[2] is persisted:
            self._v61_memo_hits = int(getattr(self, "_v61_memo_hits", 0) or 0) + 1
            return None
        super()._research_refresh_rolling_kappa_cache()
        self._v61_memo_misses = int(getattr(self, "_v61_memo_misses", 0) or 0) + 1
        self._v61_kappa_memo = (
            stamp, history, getattr(self, "_research_persisted_observation_timestamps", None),
        )
        return None

    def _v61_pnl_window(self, current_ts):
        """Per-book (non-zero bucket count, realized sum) over the PnL lookback, one pass per request.

        Same arithmetic as the two frozen scans: for each book the additions happen in the same
        timestamp order, and the frozen sum's `+ 0.0` for an absent book is an identity.
        """
        history = getattr(self, "realized_pnl_history", None)
        if history is None:
            history = {}
        lookback = getattr(self, "pnl_lookback_ns", 0)
        key = (current_ts, lookback, len(history))
        memo = getattr(self, "_v61_pnl_memo", None)
        if memo is not None and memo[0] == key and memo[1] is history:
            return memo[2], memo[3]
        threshold = current_ts - lookback
        counts: dict = {}
        sums: dict = {}
        for ts, books in history.items():
            if ts < threshold:
                continue
            for book_id, pnl in books.items():
                sums[book_id] = sums.get(book_id, 0.0) + pnl
                if pnl != 0.0:
                    counts[book_id] = counts.get(book_id, 0) + 1
        self._v61_pnl_memo = (key, history, counts, sums)
        self._v61_pnl_memo_builds = int(getattr(self, "_v61_pnl_memo_builds", 0) or 0) + 1
        return counts, sums

    def _pnl_observation_count(self, book_id: int, current_ts: int) -> int:
        if not self._v61_memo_on():
            return super()._pnl_observation_count(book_id, current_ts)
        counts, _sums = self._v61_pnl_window(current_ts)
        return int(counts.get(book_id, 0))

    def _realized_pnl_lookback(self, book_id: int, current_ts: int) -> float:
        if not self._v61_memo_on():
            return super()._realized_pnl_lookback(book_id, current_ts)
        _counts, sums = self._v61_pnl_window(current_ts)
        return sums.get(book_id, 0.0)

    # ---- v6.1.1: the reprice seed sees the floor; every price lands on its own tick ----------------
    def _v611_floor_reprice_on(self) -> bool:
        return bool(getattr(self, "research_v611_floor_reprice", True))

    def _v611_price_lift_on(self) -> bool:
        return bool(getattr(self, "research_v611_price_lift", True))

    def _v611_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v611_counts", None)
        if counts is None:
            counts = {}
            self._v611_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v611_seed_comparand(self, book_id: int, state, touch: float, *, long_position: bool) -> float:
        """The passive touch, floored at the head lot's break-even when v6.1 floors this book's exits.

        ``_v61_apply_floor`` floors every maker exit the placement path sends, so the A1.9.1.1 seed
        must judge a resting exit against that same price.  Against the bare touch, UID 82's held
        lots had their floored exits cancelled as STALE_BEHIND_TOUCH 2,265 times in 1,014 ticks.
        """
        if not (self._v611_floor_reprice_on() and self._v61_on()):
            return touch
        # v6.2.3: a lifted book's exit rests at the touch, so that is what the classifier compares.
        if self._v623_lifted(int(book_id), state)[0]:
            self._v623_count("lifted_comparand")
            return touch
        floor = self._v61_floor_for(int(book_id), bool(long_position), state=state)
        if floor is None:
            return touch
        placed = float(v61_floored_close_price(touch, floor, long_position=bool(long_position)))
        if placed != float(touch):
            self._v611_count("seed_floored")
        return placed

    def _v611_lift_outgoing_prices(self, response, state) -> int:
        """Lift each queued limit price whose double sits below its decimal by one ulp."""
        if not self._v611_price_lift_on():
            return 0
        instructions = getattr(response, "instructions", None)
        if not instructions:
            return 0
        moved = v611_lift_instruction_prices(instructions, self._v61_price_decimals(state))
        total = 0
        for side, n in moved.items():
            if n:
                self._v611_count(f"lifted_{side}", n)
                total += int(n)
        return total

    def _v611_telemetry(self, state) -> None:
        tick = int(getattr(self, "_tick", 0) or 0)
        if getattr(self, "_v611_state_reported", False) and not (tick > 0 and tick % V611_STATE_EVERY_TICKS == 0):
            return
        self._v611_state_reported = True
        self._emit(
            "V611_STATE", force=True, tick=tick, v611_version=V611_VERSION,
            floor_reprice=int(self._v611_floor_reprice_on()),
            price_lift=int(self._v611_price_lift_on()),
            counts=dict(getattr(self, "_v611_counts", {}) or {}),
            errors=int(getattr(self, "_v611_errors", 0) or 0),
        )

    # ---- v6.2.0: symmetric touch quotes on every valid flat book --------------------------------------
    def _v62_on(self) -> bool:
        return bool(getattr(self, "research_v62_breadth", True))

    def _v62_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v62_counts", None)
        if counts is None:
            counts = {}
            self._v62_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v621_on(self) -> bool:
        return bool(self._v62_on() and getattr(self, "research_v621_managed_universe", True))

    def _v621_cap_override(self, universe):
        """The fast-path screen's bound at breadth: the universe, so no inventory book is dropped.

        None (the frozen clamp) whenever the switch is off or the universe is not a positive count.
        """
        if not self._v621_on():
            return None
        try:
            n = int(universe)
        except (TypeError, ValueError):
            return None
        return n if n > 0 else None

    def _v622_on(self) -> bool:
        return bool(self._v62_on() and getattr(self, "research_v622_seed_at_breadth", True))

    def _v622_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v622_counts", None)
        if counts is None:
            counts = {}
            self._v622_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v622_seed_lots(self, book_id: int, side: str, lot) -> tuple[list, bool]:
        """The tracker lots for one seeded position.

        The session's own lots when they still add up to the venue's position (their entry prices
        and fees are the ones the validator's FIFO holds), else the plan's synthetic lot at the quote
        with fee 0, which is an inherited position and stays under the v6.0.0 park policy.
        Returns (lots, restored).
        """
        default = [lot.as_tuple()]
        if not self._v622_on():
            return default, False
        try:
            restored = self._v61_restored_side(int(book_id), str(side), abs(float(lot.net_base)))
        except Exception:
            restored = []
        if restored:
            self._v622_count("restored_books")
            return [tuple(x) for x in restored], True
        self._v622_count("synthetic_books")
        return default, False

    # ---- v6.2.3: the floor protects the validator's no-loss premium, and nothing else ------------
    def _v623_on(self) -> bool:
        return bool(
            self._v62_on() and self._v61_on() and getattr(self, "research_v623_premium_floor", True)
        )

    def _v623_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v623_counts", None)
        if counts is None:
            counts = {}
            self._v623_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v623_census(self, state=None):
        """Every book's Kappa-3 window this state, from the session-persisted realized-PnL events.

        The events survive restarts (``rolling_realized_pnl_events``), so a restart does not read
        every book as empty.  One pass per request: memoized on the state timestamp, the realized
        generation (bumped on every own realized fill) and the events object.  None when the state
        carries no clock, which keeps every floor.
        """
        now = getattr(state, "timestamp", None) if state is not None else None
        if now is None:
            now = getattr(self, "_research_last_sim_ts", None)
        try:
            now = int(now or 0)
        except (TypeError, ValueError):
            now = 0
        if now <= 0:
            return None
        events = getattr(self, "_research_realized_pnl_events_by_book", None) or {}
        key = (now, int(getattr(self, "_research_realized_generation", 0) or 0), id(events))
        memo = getattr(self, "_v623_memo", None)
        if memo is not None and memo[0] == key:
            return memo[1]
        cfg = getattr(state, "config", None) if state is not None else None
        step = getattr(cfg, "publish_interval", None) or V623_PUBLISH_STEP_NS
        decimals = getattr(cfg, "volumeDecimals", None)
        census = v623_window_census(
            events, now=now,
            lookback_ns=int(getattr(self, "research_kappa_lookback_ns", 10_800_000_000_000) or 10_800_000_000_000),
            step_ns=step,
            decimals=(V623_VOLUME_DECIMALS if decimals is None else decimals),
        )
        self._v623_memo = (key, census)
        return census

    def _v623_lifted(self, book_id: int, state=None) -> tuple[bool, str]:
        """(lifted, window status).  The floor stays on a PREMIUM book and on any error."""
        if not self._v623_on():
            return False, ""
        try:
            census = self._v623_census(state)
        except Exception:
            self._v623_errors = int(getattr(self, "_v623_errors", 0) or 0) + 1
            return False, ""
        if census is None:
            return False, ""
        status = v623_book_status(census.get(int(book_id)), min_observations=V623_MIN_OBSERVATIONS)
        if not self._v626_loss_budget_on():
            return status != V623_BOOK_PREMIUM, status
        # v6.2.6 rule A: a book already carrying a loss is spent and free; a clean one (PREMIUM, THIN or
        # EMPTY) is spent only when it is stuck at the band and the median still holds.
        if status == V623_BOOK_LOSS:
            return True, status
        if v626_spend_allowed(
            status_is_loss=False, blocked=self._v626_blocked(int(book_id), state),
            budget=self._v626_budget(state, census),
        ):
            self._v626_spend(int(book_id))
            return True, status
        self._v626_count("budget_held")
        return False, status

    def _v623_release(
        self, book_id: int, state, *, lot, floor, close_price, long_pos: bool, qty, action,
    ) -> bool:
        """At the placement floor: True when the book is lifted and the exit keeps its own price.

        One V623_RELEASE row per (book, head lot), so the read can tie every realized loss to the
        window status that allowed it.
        """
        lifted, status = self._v623_lifted(int(book_id), state)
        if not lifted:
            return False
        self._v623_count("lifted_placements")
        self._v623_count("lifted_" + status.lower())
        noted = getattr(self, "_v623_noted", None)
        if noted is None:
            noted = {}
            self._v623_noted = noted
        lot_key = (lot[0], round(float(lot[1]), 8), round(float(lot[2]), 8))
        if noted.get(int(book_id)) != lot_key:
            noted[int(book_id)] = lot_key
            self._v623_count("releases")
            window = (self._v623_census(state) or {}).get(int(book_id))
            fee = float(self._research_live_fee_bps(int(book_id), is_maker=True) or 0.0)
            net = float("nan")
            if close_price is not None:
                net = v61_fifo_close_net_bps(
                    lot, close_price=float(close_price), close_fee_bps=fee, long_position=long_pos,
                    qty=abs(float(qty or 0.0)) or None,
                )
            self._emit(
                "V623_RELEASE", force=True, tick=int(getattr(self, "_tick", 0) or 0),
                book=int(book_id), v623_version=V623_PREMIUM_FLOOR_VERSION, status=status,
                action=str(action or ""), long_position=int(bool(long_pos)),
                requested_price=(None if close_price is None else float(close_price)),
                floor_price=float(floor), lot_price=float(lot[2]), lot_qty=float(lot[1]),
                lot_open_fee=float(lot[3]), maker_fee_bps=fee,
                requested_net_bps=(None if net != net else round(float(net), 4)),
                window=(window.as_log() if window is not None else {}),
            )
        return True

    def _v623_resting_floor_bps(self, book_id: int, state) -> float:
        """The A1.9.1 classifier's NET_BELOW_FLOOR bound: the maker target, none on a lifted book.

        A release rests below break-even by design; judged against the target it would be cancelled
        one request after it was placed (the v6.1.1 churn).  It still reprices when the touch leaves.
        """
        if self._v623_lifted(int(book_id), state)[0]:
            self._v623_count("lifted_classifier")
            return float("-inf")
        return float(DIRECT_MAKER_EXIT_TARGET_BPS)

    def _v623_snapshot(self, state) -> dict:
        """Counters plus the universe's Kappa-3 branches (coverage = PREMIUM + LOSS_IN_WINDOW)."""
        out: dict[str, Any] = dict(getattr(self, "_v623_counts", {}) or {})
        out["errors"] = int(getattr(self, "_v623_errors", 0) or 0)
        if not self._v623_on():
            return out
        try:
            census = self._v623_census(state)
            books = list((getattr(state, "books", None) or {}).keys())
            if census is not None and books:
                out["books"] = v623_census_counts(census, books, min_observations=V623_MIN_OBSERVATIONS)
        except Exception:
            out["errors"] += 1
        return out


    # ---- v6.2.4: a release takes the exit order life ----------------------------------------------
    def _v624_on(self) -> bool:
        return bool(self._v623_on() and getattr(self, "research_v624_release_life", True))

    def _v624_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v624_counts", None)
        if counts is None:
            counts = {}
            self._v624_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    @contextmanager
    def _v624_release_life(self, book_id: int, state):
        """The persistence threshold of the frozen placement, on a book v6.2.3 lifts.

        The frozen base reads ``research_profitable_exit_min_net_bps`` twice, both in the call this
        wraps: ``profitable_maker_exit_ttl_ms`` (persistent TTL) and
        ``hold_existing_profitable_maker_exit`` (keep the queue).  On a book outside the Kappa-3
        premium branch there is no floor for either to restate, so the threshold is -inf for the call
        and restored after it, also on an exception.  The TTL value, its 5 s cap and the TOXIC /
        STRESSED exclusions are the frozen ones.  Yields whether the release life applied.
        """
        if not (self._v624_on() and self._v623_lifted(int(book_id), state)[0]):
            yield False
            return
        had = "research_profitable_exit_min_net_bps" in self.__dict__
        saved = self.__dict__.get("research_profitable_exit_min_net_bps")
        self.research_profitable_exit_min_net_bps = float("-inf")
        self._v624_count("release_life")
        try:
            yield True
        finally:
            if had:
                self.research_profitable_exit_min_net_bps = saved
            else:
                self.__dict__.pop("research_profitable_exit_min_net_bps", None)
    # ---- v6.2.5: two sides on every book, the clip paced to the validator's turnover cap --------

    def _v625_two_sided_on(self) -> bool:
        return bool(self._v62_on() and getattr(self, "research_v625_two_sided", True))

    def _v625_cap_pace_on(self) -> bool:
        return bool(self._v62_on() and getattr(self, "research_v625_cap_pace", True))

    def _v625_on(self) -> bool:
        return bool(self._v625_two_sided_on() or self._v625_cap_pace_on())

    def _v625_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v625_counts", None)
        if counts is None:
            counts = {}
            self._v625_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v625_now_ns(self, state) -> int:
        now = getattr(state, "timestamp", None) if state is not None else None
        if now is None:
            now = getattr(self, "_research_last_sim_ts", None)
        try:
            return int(now or 0)
        except (TypeError, ValueError):
            return 0

    def _v625_clip(self, book_id: int, state, facts, *, mid: float) -> float:
        """This book's clip this request: one minimum order, or the cap-paced one.

        The target is the validator's own cap over its own period, the sample is its own volume
        sampling interval, and the measurement is the venue's ``account.traded_volume`` -- the very
        number the cap is applied to.  Between samples the clip is held.
        """
        min_order = float(getattr(self, "mm_base_size", 0.25) or 0.25)
        if not self._v625_cap_pace_on():
            return min_order
        try:
            cap = float(self._research_volume_cap_quote(state))
            used = float(self._research_book_traded_volume(int(book_id)))
            remaining = float(self._research_volume_cap_remaining(state, int(book_id)))
        except Exception:
            self._v625_errors = int(getattr(self, "_v625_errors", 0) or 0) + 1
            return min_order
        now_ns = self._v625_now_ns(state)
        target = v625_pace_target_rate(cap)
        paces = getattr(self, "_v625_pace", None)
        if paces is None:
            paces = {}
            self._v625_pace = paces
        pace = paces.get(int(book_id))
        if pace is None:
            paces[int(book_id)] = V625BookPace(
                clip=min_order, obs_rate=None, target_rate=target, sampled_ns=now_ns, volume=used,
            )
            return min_order
        obs = v625_observed_rate(
            prev_ns=pace.sampled_ns, prev_volume=pace.volume, now_ns=now_ns, volume=used,
        )
        if obs is None:
            if pace.sampled_ns is None or (now_ns - int(pace.sampled_ns)) >= V625_PACE_SAMPLE_NS:
                pace.sampled_ns, pace.volume = now_ns, used     # the venue's window rolled
            return float(pace.clip)
        ceiling = v625_clip_ceiling(
            min_order=min_order, base_free=facts.base_free, quote_free=facts.quote_free,
            price=mid, cap_remaining=remaining,
        )
        clip = v625_paced_clip(
            clip_now=pace.clip, min_order=min_order, target_rate=target, obs_rate=obs,
            ceiling=ceiling,
        )
        if clip > float(pace.clip):
            self._v625_count("clip_up")
        elif clip < float(pace.clip):
            self._v625_count("clip_down")
        pace.clip, pace.obs_rate, pace.target_rate = float(clip), float(obs), float(target)
        pace.sampled_ns, pace.volume = now_ns, used
        return float(clip)

    def _v625_sides(self, facts, *, clip: float, flat_eps: float, state, mid: float) -> dict:
        """Which sides this book gets this request, and why the others do not."""
        sides = v625_sides_verdict(
            facts, clip=clip, flat_eps=flat_eps, two_sided=self._v625_two_sided_on(),
        )
        if not self._v625_cap_pace_on():
            return sides
        try:
            cap = float(self._research_volume_cap_quote(state))
            used = float(self._research_book_traded_volume(int(facts.book_id)))
        except Exception:
            self._v625_errors = int(getattr(self, "_v625_errors", 0) or 0) + 1
            return sides
        if v625_cap_reserve_ok(
            cap_quote=cap, used=used, net_base=facts.net_base, mid=mid, clip=clip,
        ):
            return sides
        self._v625_count("cap_reserve")
        return {
            side: (V625_REASON_CAP_RESERVE if reason == V625_REASON_OK else reason)
            for side, reason in sides.items()
        }

    def _v625_apply_caps(self, state) -> None:
        """Re-derive the portfolio caps when the paced band grows: books × band, still the universe's."""
        if not self._v625_cap_pace_on():
            return
        paces = getattr(self, "_v625_pace", None) or {}
        if not paces:
            return
        books = getattr(state, "books", None) or {}
        n = len(books)
        if n <= 0:
            return
        min_order = float(getattr(self, "mm_base_size", 0.25) or 0.25)
        lot = max(min_order, v625_band_for(max(float(p.clip) for p in paces.values())) / V625_BAND_CLIPS)
        prev = getattr(self, "_v625_caps_lot", None)
        if prev is not None and lot <= float(prev) + 1e-12:
            return
        caps = v626_open_book_caps(v62_universe_caps(n, V625_BAND_CLIPS * lot))
        for key, value in caps.items():
            setattr(self, key, value)
        self._v625_caps_lot = lot
        self._v625_count("caps_raised")
        self._emit(
            "V625_CAPS", force=True, tick=int(getattr(self, "_tick", 0) or 0),
            v625_version=V625_CAP_PACED_VERSION, universe=int(n), clip=lot,
            band=V625_BAND_CLIPS * lot, after=dict(caps),
        )

    def _v625_snapshot(self) -> dict:
        """The pacing state for V62_STATE."""
        out = dict(getattr(self, "_v625_counts", {}) or {})
        out["errors"] = int(getattr(self, "_v625_errors", 0) or 0)
        try:
            out.update(v625_pace_snapshot(
                getattr(self, "_v625_pace", {}) or {},
                min_order=float(getattr(self, "mm_base_size", 0.25) or 0.25),
            ))
        except Exception:
            out["errors"] = int(out.get("errors", 0)) + 1
        return out

    # ---- v6.2.6: clean closes and balanced capture ---------------------------------------------

    def _v626_loss_budget_on(self) -> bool:
        return bool(self._v623_on() and getattr(self, "research_v626_loss_budget", True))

    def _v626_capture_balance_on(self) -> bool:
        return bool(self._v62_on() and getattr(self, "research_v626_capture_balance", True))

    def _v626_quote_life_on(self) -> bool:
        return bool(self._v62_on() and getattr(self, "research_v626_quote_life", True))

    def _v626_on(self) -> bool:
        return bool(self._v626_loss_budget_on() or self._v626_capture_balance_on()
                    or self._v626_quote_life_on())

    def _v626_count(self, key: str, n: int = 1) -> None:
        counts = getattr(self, "_v626_counts", None)
        if counts is None:
            counts = {}
            self._v626_counts = counts
        counts[key] = int(counts.get(key, 0)) + int(n)

    def _v626_budget(self, state, census) -> int:
        """How many more clean books may be spent this state while the median book stays clean."""
        premium = loss = 0
        for window in (census or {}).values():
            status = v623_book_status(window, min_observations=V623_MIN_OBSERVATIONS)
            if status == V623_BOOK_PREMIUM:
                premium += 1
            elif status == V623_BOOK_LOSS:
                loss += 1
        budget = v626_median_budget(premium=premium, loss=loss)
        key = (int(getattr(self, "_tick", 0) or 0), premium, loss)
        spent = getattr(self, "_v626_spent", None)
        if spent is None or spent[0] != key:
            spent = (key, set())
            self._v626_spent = spent
        return max(0, budget - len(spent[1]))

    def _v626_spend(self, book_id: int) -> None:
        spent = getattr(self, "_v626_spent", None)
        if spent is not None:
            spent[1].add(int(book_id))
        self._v626_count("budget_spent")

    def _v626_blocked(self, book_id: int, state=None) -> bool:
        """True when the book is stuck: one more clip would take it past the inventory band."""
        min_order = float(getattr(self, "mm_base_size", 0.25) or 0.25)
        pace = (getattr(self, "_v625_pace", None) or {}).get(int(book_id))
        clip = float(getattr(pace, "clip", min_order) or min_order)
        try:
            net = float(self._direct_signed_inventory(int(book_id)))
        except Exception:
            self._v626_errors = int(getattr(self, "_v626_errors", 0) or 0) + 1
            return False
        return v626_book_blocked(net_base=net, clip=clip, band=v625_band_for(clip))

    def _v626_book_capture(self, book_id: int) -> tuple:
        mirror = getattr(self, "_v62_mirror", None)
        if mirror is None:
            return 0.0, 0.0
        try:
            return mirror.book_capture(int(book_id))
        except Exception:
            self._v626_errors = int(getattr(self, "_v626_errors", 0) or 0) + 1
            return 0.0, 0.0

    def _v626_side_clips(self, book_id: int, clip: float) -> dict:
        """The clip for each side, sized by which side's capture is behind (rule B)."""
        if not self._v626_capture_balance_on():
            return {V625_SIDE_BUY: float(clip), V625_SIDE_SELL: float(clip)}
        buy, sell = self._v626_book_capture(int(book_id))
        out = v626_side_clips(
            clip=clip, buy_capture=buy, sell_capture=sell,
            min_order=float(getattr(self, "mm_base_size", 0.25) or 0.25), lots_of=v625_lots_of,
        )
        if min(buy, sell) < 0.0:
            self._v626_count("surplus_paused")
        elif out.get(V625_SIDE_BUY) != out.get(V625_SIDE_SELL):
            self._v626_count("side_skewed")
        return out

    def _v626_snapshot(self) -> dict:
        out = dict(getattr(self, "_v626_counts", {}) or {})
        out["errors"] = int(getattr(self, "_v626_errors", 0) or 0)
        return out

    def _v62_apply_caps(self, state) -> None:
        """The portfolio caps are the universe's: every book may hold its one lot in flight.

        The frozen init clamps total open books at 8 and the launcher carries 2.0 BASE; both were
        the slot model.  Derived here from the state's book count and the lot, once per run, so the
        number is the universe's and not an observed one.
        """
        if not self._v62_on() or getattr(self, "_v62_caps_applied", False):
            return
        books = getattr(state, "books", None) or {}
        n = len(books)
        if n <= 0:
            return
        lot = float(getattr(self, "mm_base_size", 0.25) or 0.25)
        caps = v626_open_book_caps(v62_universe_caps(n, lot))
        before = {key: getattr(self, key, None) for key in caps}
        for key, value in caps.items():
            setattr(self, key, value)
        self._v62_caps_applied = True
        self._emit(
            "V62_CAPS", force=True, tick=int(getattr(self, "_tick", 0) or 0),
            v62_version=V62_VERSION, universe=int(n), lot=lot, before=before, after=dict(caps),
        )

    def _v62_entry_ttl_ns(self, book_id: int, state) -> int:
        """The entry TTL the frozen placement path would choose for this book (parity with v6.1.1)."""
        baseline = int(getattr(self, "mm_expiry_period", 500_000_000) or 500_000_000)
        if self._v626_quote_life_on():
            # v6.2.6 rule C: the exit's persistent life, with the frozen 5 s cap.
            persistent = float(getattr(self, "research_profitable_exit_ttl_ms", 4000.0) or 4000.0)
            self._v626_count("quote_life")
            return int(max(0.0, min(5000.0, persistent)) * 1_000_000)
        if not bool(getattr(self, "research_enable_adaptive_ttl", False)):
            return baseline
        try:
            chosen, _reason, _hazard = self._research_choose_ttl(
                int(book_id), None, state, baseline_ns=baseline,
            )
        except Exception:
            return baseline
        if chosen is None:
            return baseline
        lo = float(getattr(self, "research_ttl_min_ms", 250.0) or 250.0)
        hi = float(getattr(self, "research_ttl_max_ms", 3000.0) or 3000.0)
        return int(max(lo, min(hi, float(chosen))) * 1_000_000)

    def _v62_book_facts(self, book_id: int, book, state, response, lot: float):
        """Gather the per-book facts from the frozen views; no decision here."""
        best_bid = best_ask = None
        try:
            bids = getattr(book, "bids", None) or []
            asks = getattr(book, "asks", None) or []
            if bids:
                best_bid = float(bids[0].price)
            if asks:
                best_ask = float(asks[0].price)
        except (TypeError, ValueError, AttributeError, IndexError):
            best_bid = best_ask = None
        try:
            net = float(self._direct_signed_inventory(int(book_id)))
        except Exception:
            net = 0.0
        try:
            live = bool(self._direct_book_has_live_order(int(book_id)))
        except Exception:
            live = True
        cap_ok = False
        if best_bid is not None and best_ask is not None and best_bid > 0.0 and best_ask > 0.0:
            mid = 0.5 * (best_bid + best_ask)
            try:
                cap_ok = bool(self._research_can_add_volume(state, int(book_id), float(lot) * mid * 2.0))
            except Exception:
                cap_ok = False
        quote_free = base_free = 0.0
        acct = (getattr(self, "accounts", None) or {}).get(int(book_id))
        if acct is not None:
            try:
                quote_free = float(acct.quote_balance.free)
                base_free = float(acct.base_balance.free)
            except (AttributeError, TypeError, ValueError):
                quote_free = base_free = 0.0
        return V62BookFacts(
            book_id=int(book_id), best_bid=best_bid, best_ask=best_ask, net_base=net,
            live_order=live, cap_ok=cap_ok, quote_free=quote_free, base_free=base_free,
            instructions_used=int(self._count_book_instructions(response, int(book_id))),
            max_instructions=int(getattr(self, "max_instructions_per_book", 5) or 5),
        )

    def _v62_place_touch_quotes(
        self, response, state, book_id: int, book, lot: float, sides: dict | None = None,
        side_qty: dict | None = None,
    ) -> int:
        """One lot at the best bid and one at the best ask, post-only, the frozen client ids.

        ``sides`` (v6.2.5) names the sides this book may quote this request; None is both, which is
        v6.2.4 exactly.
        """
        try:
            best_bid = float(book.bids[0].price)
            best_ask = float(book.asks[0].price)
        except (TypeError, ValueError, AttributeError, IndexError):
            return 0
        prices = v62_touch_prices(best_bid, best_ask, self._v61_price_decimals(state))
        if prices is None:
            return 0
        bid_px, ask_px = prices
        cfg = getattr(state, "config", None)
        qty = v62_lot_quantity(lot, getattr(cfg, "volumeDecimals", 4))
        if qty <= 0.0:
            return 0
        expiry = self._v62_entry_ttl_ns(int(book_id), state)
        buy_cid, sell_cid = v62_entry_client_ids(int(book_id))
        post_only = bool(self._prefer_maker(int(book_id)))
        mem = self._mem(int(book_id))
        budget = int(getattr(self, "max_instructions_per_book", 5) or 5)
        placed = 0
        for direction, price, client_id, side in (
            (OrderDirection.BUY, bid_px, buy_cid, "buy"),
            (OrderDirection.SELL, ask_px, sell_cid, "sell"),
        ):
            if sides is not None and sides.get(side) != V625_REASON_OK:
                continue
            side_q = qty if side_qty is None else v62_lot_quantity(
                side_qty.get(side, qty), getattr(cfg, "volumeDecimals", 4),
            )
            if side_q <= 0.0:
                self._v626_count("surplus_skipped")
                continue
            if v626_side_already_instructed(response, int(book_id), direction):
                self._v626_count("side_owned")
                continue
            if self._count_book_instructions(response, int(book_id)) >= budget:
                break
            response.limit_order(
                book_id=int(book_id),
                direction=direction,
                quantity=side_q,
                price=price,
                clientOrderId=client_id,
                stp=STP.CANCEL_BOTH,
                postOnly=post_only,
                timeInForce=TimeInForce.GTT,
                expiryPeriod=expiry,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )
            try:
                self._record_fill_quote(mem, side, 0.0)
                mem.quote_count += 1
            except Exception:
                pass
            placed += 1
        return placed

    def _v62_acquire(self, response, state, stats: dict) -> int:
        """v6.2: the acquisition pass over EVERY book.  Returns the instructions it added."""
        books = getattr(state, "books", None) or {}
        lot = float(getattr(self, "mm_base_size", 0.25) or 0.25)
        eps = float(self._execution_flat_epsilon())
        request: dict[str, int] = {"books": len(books), "eligible": 0, "quoted_books": 0, "placements": 0}
        for reason in V62_SKIP_REASONS:
            request[reason] = 0
        placed_total = 0
        for raw_id in sorted(books, key=lambda x: int(x)):
            book_id = int(raw_id)
            book = books[raw_id]
            facts = self._v62_book_facts(book_id, book, state, response, lot)
            clip, sides, side_qty = lot, None, None
            if self._v625_on():
                mid = 0.0
                if facts.best_bid is not None and facts.best_ask is not None:
                    mid = 0.5 * (float(facts.best_bid) + float(facts.best_ask))
                clip = self._v625_clip(book_id, state, facts, mid=mid)
                if clip != lot and mid > 0.0:
                    try:
                        facts = v625_with_cap_ok(facts, self._research_can_add_volume(
                            state, book_id, float(clip) * mid * 2.0,
                        ))
                    except Exception:
                        self._v625_errors = int(getattr(self, "_v625_errors", 0) or 0) + 1
                sides = self._v625_sides(facts, clip=clip, flat_eps=eps, state=state, mid=mid)
                side_qty = self._v626_side_clips(book_id, clip)
                for side_token in (V625_SIDE_BUY, V625_SIDE_SELL):
                    if sides.get(side_token) == V625_REASON_OK and side_qty.get(side_token, 0.0) <= 0.0:
                        sides[side_token] = V626_REASON_SURPLUS
                allowed = [s for s, why in sides.items() if why == V625_REASON_OK]
                verdict = V62_REASON_OK if allowed else v626_skip_reason(
                    sides, exit_side_token=V625_REASON_EXIT_SIDE,
                )
                if allowed and len(allowed) == 1:
                    self._v625_count("one_side")
            else:
                verdict = v62_universe_verdict(facts, lot=lot, flat_eps=eps)
            if verdict != V62_REASON_OK:
                request[verdict] = request.get(verdict, 0) + 1
                continue
            request["eligible"] += 1
            n = self._v62_place_touch_quotes(
                response, state, book_id, book, clip, sides, side_qty,
            )
            if n:
                request["quoted_books"] += 1
                request["placements"] += int(n)
                placed_total += int(n)
                if sides is not None and abs(float(facts.net_base)) > eps:
                    self._v625_count("adding_on_held_book")
        for key, value in request.items():
            if key != "books":
                self._v62_count(key, value)
        self._v62_count("requests")
        self._v62_request = request
        stats["candidates"] = int(request["eligible"])
        stats["quoted"] = int(request["quoted_books"])
        return placed_total

    def _v62_feed_mirror(self, state) -> None:
        """Telemetry: the validator's making term from this state's prints, for our uid."""
        if not self._v62_on():
            return
        mirror = getattr(self, "_v62_mirror", None)
        if mirror is None:
            lookback = int(getattr(self, "research_kappa_lookback_ns", 0) or 0) or V62_DEFAULT_LOOKBACK_NS
            mirror = V62MakingMirror(int(getattr(self, "uid", 0) or 0), lookback_ns=lookback)
            self._v62_mirror = mirror
        books = getattr(state, "books", None) or {}
        mirror.ingest_state(
            int(getattr(state, "timestamp", 0) or 0),
            ((int(raw_id), getattr(book, "events", None)) for raw_id, book in books.items()),
        )

    def _v62_telemetry(self, state) -> None:
        tick = int(getattr(self, "_tick", 0) or 0)
        if getattr(self, "_v62_state_reported", False) and not (tick > 0 and tick % V62_STATE_EVERY_TICKS == 0):
            return
        self._v62_state_reported = True
        mirror = getattr(self, "_v62_mirror", None)
        self._emit(
            "V62_STATE", force=True, tick=tick, v62_version=V62_VERSION,
            enabled=int(self._v62_on()),
            caps_applied=int(bool(getattr(self, "_v62_caps_applied", False))),
            managed_universe_on=int(self._v621_on()),
            managed_universe=dict(getattr(self, "_v621_last", {}) or {}),
            seed_at_breadth_on=int(self._v622_on()),
            seed_at_breadth=dict(getattr(self, "_v622_counts", {}) or {}),
            premium_floor_on=int(self._v623_on()),
            premium_floor=self._v623_snapshot(state),
            release_life_on=int(self._v624_on()),
            release_life=dict(getattr(self, "_v624_counts", {}) or {}),
            two_sided_on=int(self._v625_two_sided_on()),
            cap_pace_on=int(self._v625_cap_pace_on()),
            cap_paced=self._v625_snapshot(),
            loss_budget_on=int(self._v626_loss_budget_on()),
            capture_balance_on=int(self._v626_capture_balance_on()),
            quote_life_on=int(self._v626_quote_life_on()),
            balanced_maker=self._v626_snapshot(),
            counts=dict(getattr(self, "_v62_counts", {}) or {}),
            last_request=dict(getattr(self, "_v62_request", {}) or {}),
            making=(mirror.snapshot() if mirror is not None else {}),
            errors=int(getattr(self, "_v62_errors", 0) or 0),
        )

    def _v504_telemetry(self, state) -> None:
        tick = int(getattr(self, "_tick", 0) or 0)
        recorder = getattr(self, "_v503_recorder", None)
        if recorder is not None and recorder.stopped_reason and not self._v504_stop_reported:
            self._v504_stop_reported = True
            self._emit(
                "V504_RECORDER_STOP", force=True, tick=tick,
                v504_disk_budget_version=V504_DISK_BUDGET_VERSION, **recorder.snapshot(),
            )
        if self._v504_state_reported and not (tick > 0 and tick % V504_STATE_EVERY_TICKS == 0):
            return
        self._v504_state_reported = True
        anchor = getattr(self, "_v504_history_anchor", None)
        pin = getattr(self, "_v504_history_pin", None)
        choice = getattr(self, "_v504_session_choice", None)
        mirror = self._v504_mirror
        belief = getattr(self, "_v501_belief", None)
        self._emit(
            "V504_IDENTITY_STATE", force=True, tick=tick,
            v504_registration_identity_version=V504_REGISTRATION_IDENTITY_VERSION,
            v504_mirror_rounds_version=V504_MIRROR_ROUNDS_VERSION,
            uid=getattr(self, "uid", None),
            session_per_uid=int(bool(getattr(self, "research_v504_session_per_uid", True))),
            session_source=None if choice is None else choice.source,
            legacy_mode=getattr(self, "research_v504_legacy_session", LEGACY_IGNORE),
            anchor_mode=None if anchor is None else anchor.mode,
            anchor_raw=None if anchor is None else anchor.raw,
            pin_source=None if pin is None else pin.source,
            pin_start_ts=None if pin is None else pin.start_ns,
            pin_established=None if pin is None else int(bool(pin.established)),
            belief_start_ts=None if belief is None else belief.history_start_ts,
            belief_start_source=None if belief is None else belief.history_start_source,
            mirror_rounds=int(bool(getattr(self, "research_v504_mirror_rounds", True))),
            mirror_start_ts=mirror.get("start_ts"), mirror_start_source=mirror.get("start_source"),
            mirror_restored_states=len(mirror.get("history") or {}),
            mirror_pnl_known_from=mirror.get("pnl_known_from"),
            mirror_step_ns=mirror.get("step_ns"), mirror_rebases=mirror.get("rebases"),
            mirror_fallbacks=mirror.get("fallbacks"),
            disk_budget=int(bool(getattr(self, "research_v504_disk_budget", True))),
            recorder_max_bytes=int(getattr(self, "_v503_recorder_max_bytes", 0) or 0),
            errors=int(getattr(self, "_v504_errors", 0) or 0),
        )

    def handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        # A1.9.9.2: the collector's full pass is scheduled on the miner's event loop
        # after this request returns, so it runs in the gap before the next one.
        # Nothing here reads or changes what the strategy decides.
        collector = getattr(self, "_a1992_idle_gc", None)
        loop = running_loop() if collector is not None else None
        if collector is not None:
            try:
                collector.begin_request(loop)
            except Exception:
                pass
        try:
            return super().handle(state)
        finally:
            if collector is not None:
                try:
                    collector.end_request(loop)
                    self._a1992_note_request(collector)
                except Exception:
                    pass

    def _a1992_note_request(self, collector) -> None:
        """One A1992_GC_TICK row per request, and A1992_IDLE_GC_STATE when the mode changes."""
        snap = collector.snapshot()
        tick = int(getattr(self, "_tick", 0) or 0)
        mode = (int(snap["installed"]), str(snap["fallback_reason"]), str(snap["skip_reason"]))
        if mode != getattr(self, "_a1992_reported", None):
            self._a1992_reported = mode
            self._emit(
                "A1992_IDLE_GC_STATE", force=True, tick=tick,
                a1992_idle_gc_version=A1992_IDLE_GC_VERSION,
                installed=mode[0], fallback_reason=mode[1], skip_reason=mode[2],
                saved_threshold=snap["saved_threshold"], delay_ms=snap["delay_ms"],
                python=snap["python"],
            )
        self._emit(
            "A1992_GC_TICK", force=True, tick=tick, installed=mode[0],
            request_gen0=snap["request_gen0"], request_gen1=snap["request_gen1"],
            request_gen2=snap["request_gen2"], idle_passes=snap["idle_passes"],
            idle_last_ms=snap["idle_last_ms"], idle_last_unreachable=snap["idle_last_unreachable"],
            idle_cancelled=snap["idle_cancelled"], responses_since_idle=snap["responses_since_idle"],
            tracked_objects=snap["tracked_objects"],
        )

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        # A1.7 preserves A1.6.3 freshness protection.  Slow-request telemetry is
        # diagnostic only and does not gate trading.
        self._direct_current_state_timestamp_ns = int(getattr(state, "timestamp", 0) or 0)
        self._direct_request_wall_started = time.perf_counter()
        try:
            self._a1961_note_base_decimals(state)
        except Exception:
            pass
        # A1.9.0.2 live resting-exit observer.  Runs before super().respond so
        # it sees every open-inventory book while its Maker exit is still alive,
        # ahead of every live-order and ownership gate that would skip the book.
        # Measurement only, and fully contained: an observer fault must never
        # cost a trading tick.
        try:
            self._a19_observe_tick_resting_exits(state)
        except Exception:
            pass
        # A1.9.2 activation report.  Hoisted above super().respond so a build
        # whose admission gate never fires still says so at tick 1 -- the exact
        # failure that invalidated two A1.9.1 runs.
        try:
            self._a192_check_activation(int(getattr(self, "_tick", 0) or 0) + 1)
        except Exception:
            pass
        # A1.9.5 step 3 (F2 behaviour).  Must run BEFORE the frozen chain: a
        # restart leaves the tracker empty while the venue still holds real
        # inventory (measured: 8.85 BASE across 120 books at tick 1), and every
        # capacity, exit and ownership decision this tick reads that tracker.
        try:
            self._a195_seed_inventory_from_venue(state)
        except Exception:
            pass
        # A1.9.6.1: seed the lots the startup seed could not price, once their
        # quote has been a market for long enough to believe.
        try:
            self._a1961_service_pending_seed(state)
        except Exception:
            pass
        # v6.1: after a skipped state, open the A1.9.9 resync for exactly one pass; every 25 ticks,
        # hand a book diverged by a lot at two consecutive checks to the deferred reseed.
        try:
            self._v61_service_gap_repair(state)
        except Exception:
            self._v61_gap_errors = int(getattr(self, "_v61_gap_errors", 0) or 0) + 1
        # A1.9.9 session epoch: while a rewind resync is open, rebuild every
        # diverged book from venue truth before any exit or capacity decision
        # reads the tracker.
        try:
            self._a199_service_resync(state)
        except Exception:
            pass
        try:
            self._v61_note_gap_repair()
        except Exception:
            self._v61_gap_errors = int(getattr(self, "_v61_gap_errors", 0) or 0) + 1
        # v5.0.2 F4: a market order the venue processed without closing its position frees the
        # book now -- update() has applied its fills -- so a pending exit resends on this request.
        try:
            self._v502_release_market_terminal()
        except Exception:
            self._v502_errors = int(getattr(self, "_v502_errors", 0) or 0) + 1
        # v5.0.3 G1: while the newcomer quiet gate holds, this request is answered with no
        # instructions.  Every observer, the venue seed and the epoch resync above have already
        # run, so the agent's own state stays current; the frozen chain below -- which is what
        # reserves ownership and places orders -- does not run at all.  The post-passes still run
        # on the empty response: they only ever add cancels, which realize nothing.
        # v5.0.4 H2/H4: the declared history start is pinned before the gate and the activity belief
        # read it, and the score copy's clock follows every state (a new simulation shifts it).
        try:
            self._v504_service(state)
        except Exception:
            self._v504_errors = int(getattr(self, "_v504_errors", 0) or 0) + 1
        quiet = None
        try:
            quiet = self._v503_gate_response(state)
        except Exception:
            self._v503_gate_errors = int(getattr(self, "_v503_gate_errors", 0) or 0) + 1
        response = super().respond(state) if quiet is None else quiet
        # A1.9.6 F11.  The venue truncates volume to its grid and the final
        # validator leaves sub-1e-12 noise in place, so 0.25009999999999827
        # shipped and executed as 0.2500, leaving one unit behind on every
        # clip.  Runs first, so every placement this tick is on the grid.
        try:
            self._a196_snap_outgoing_quantities(response, state)
        except Exception:
            pass
        # v6.1.1: the venue truncates prices the same way, so a price whose double sits below its
        # decimal was placed one tick low -- ~half of all limit orders on both nets.  Every pass
        # after this one removes placements or adds cancels, never a price, so it runs once here.
        try:
            self._v611_lift_outgoing_prices(response, state)
        except Exception:
            self._v611_errors = int(getattr(self, "_v611_errors", 0) or 0) + 1
        # A1.9.9: nothing opens or adds exposure during a resync, whichever path
        # built the order.  Runs on the wire quantities, after the grid snap.
        try:
            self._a199_strip_resync_exposure(response)
        except Exception:
            pass
        # Orphan cancels go out AFTER the chain has built its instructions, so
        # the shared per-book budget is known and a cancel can never displace a
        # placement the strategy already decided on.
        try:
            self._a195_cancel_orphan_orders(response, state)
        except Exception:
            pass
        # A1.9.1 Phase B: emit reprice cancels after the frozen chain has built
        # its instructions, so the shared per-book budget is known and a cancel
        # can never displace a placement the strategy already decided on.
        try:
            self._a191_service_reprice_cancels(response, state)
        except Exception:
            pass
        # A1.9.9: measure pending ABSOLUTE exits that sent nothing this tick.
        try:
            self._a199_note_exit_stalls(response)
        except Exception:
            pass
        # v5.0.0 analytics: after every A1.x post-pass, so the rows it reads are final.
        try:
            self._v500_service(state)
        except Exception:
            self._v500_service_errors = int(getattr(self, "_v500_service_errors", 0) or 0) + 1
        # v5.0.1 telemetry: activations and the activity state.
        try:
            self._v501_service(state)
        except Exception:
            self._v501_errors = int(getattr(self, "_v501_errors", 0) or 0) + 1
        # v5.0.2 telemetry: the dust state row.
        try:
            self._v502_service(state)
        except Exception:
            self._v502_errors = int(getattr(self, "_v502_errors", 0) or 0) + 1
        # v5.0.3 telemetry: the observatory capture and its rows.  Runs while the gate holds too --
        # the quiet window is the most valuable one to record, and the least costly to record in.
        try:
            self._v503_service(state)
        except Exception:
            self._v503_recorder_errors = int(getattr(self, "_v503_recorder_errors", 0) or 0) + 1
        # v5.0.4 telemetry: the identity row and the recorder's stop, the request it is seen.
        try:
            self._v504_telemetry(state)
        except Exception:
            self._v504_errors = int(getattr(self, "_v504_errors", 0) or 0) + 1
        # v6.0.0 telemetry: the short-lot state row.
        try:
            self._v600_telemetry(state)
        except Exception:
            self._v600_errors = int(getattr(self, "_v600_errors", 0) or 0) + 1
        # v6.0.1 telemetry: the reserve state row.
        try:
            self._v601_telemetry(state)
        except Exception:
            self._v601_errors = int(getattr(self, "_v601_errors", 0) or 0) + 1
        # v6.0.3 telemetry: the short-lot release row.
        try:
            self._v603_telemetry(state)
        except Exception:
            self._v603_errors = int(getattr(self, "_v603_errors", 0) or 0) + 1
        # v6.1 telemetry: the no-loss state row.
        try:
            self._v61_telemetry(state)
        except Exception:
            self._v61_errors = int(getattr(self, "_v61_errors", 0) or 0) + 1
        # v6.1 telemetry: the gap-repair and request-memo row.
        try:
            self._v61_gap_telemetry(state)
        except Exception:
            self._v61_gap_errors = int(getattr(self, "_v61_gap_errors", 0) or 0) + 1
        # v6.1.1 telemetry: the reprice seed and the price lift.
        try:
            self._v611_telemetry(state)
        except Exception:
            self._v611_errors = int(getattr(self, "_v611_errors", 0) or 0) + 1
        # v6.2 telemetry: the breadth state and the making mirror.
        try:
            self._v62_telemetry(state)
        except Exception:
            self._v62_errors = int(getattr(self, "_v62_errors", 0) or 0) + 1
        elapsed_ms = (time.perf_counter() - float(self._direct_request_wall_started)) * 1000.0
        if elapsed_ms > 100.0:
            try:
                timing = dict(getattr(self, "_research_timing", {}) or {})
                self._emit(
                    "DIRECT_SLOW_REQUEST", force=True,
                    tick=int(getattr(self, "_tick", 0) or 0),
                    total_ms=float(elapsed_ms),
                    screen_ms=float(timing.get("screen_ms", 0.0) or 0.0),
                    ranking_ms=float(timing.get("ranking_ms", 0.0) or 0.0),
                    full_predict_ms=float(timing.get("full_predict_ms", 0.0) or 0.0),
                    build_orders_ms=float(timing.get("build_orders_ms", 0.0) or 0.0),
                    logging_ms=float(timing.get("logging_ms", 0.0) or 0.0),
                )
            except Exception:
                pass
        return response

    # ------------------------------------------------------------------
    # Inventory: one owner.  Any real position goes to PositionExitController.
    # ------------------------------------------------------------------
    def _inventory_needs_management(self, inventory) -> bool:
        band = str(getattr(inventory, "band", "FLAT") or "FLAT").upper()
        if band == "FLAT":
            return False
        qty = abs(float(getattr(inventory, "net_base", 0.0) or 0.0))
        eps = float(self._execution_flat_epsilon())
        min_size = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        # A1.3: sub-minimum residuals are real absolute exposure but cannot be
        # legally reduced.  Do not repeatedly send them to PositionExitController.
        # v6.0.0: a short lot can (one minimum-order clip), so only dust stays out.
        book_id = getattr(inventory, "_research_book_id", None)
        return not (qty > eps and self._v600_counts_as_dust(
            -1 if book_id is None else int(book_id), qty, eps=eps, min_order=min_size))

    # ------------------------------------------------------------------
    # A1.7 deterministic persistent-Maker quote ownership.
    # ------------------------------------------------------------------
    @staticmethod
    def _direct_entry_quote_client_ids(book_id: int) -> set[int]:
        """The two client ids the skewed entry quotes are placed under.

        A1.9.0.3 factored this out so the exit observer and the entry-quote
        cancel path cannot drift apart: on a long book the entry ASK rests on
        the close side, and an observer that cannot tell it from a Maker exit
        adopts it, counts it in the hold rate, and reports EXPIRED when the
        quote manager cancels it.
        """
        base = 70000 + int(book_id) * 10
        return {base + 1, base + 2}

    def _direct_is_entry_quote_order(self, book_id: int, order) -> bool:
        cid = self._direct_order_client_id(order)
        return cid in self._direct_entry_quote_client_ids(book_id)

    def _direct_entry_quote_orders(self, book_id: int) -> list:
        return [
            order for order in self._direct_account_orders(int(book_id))
            if self._direct_is_entry_quote_order(int(book_id), order)
        ]

    def _direct_order_side_price(self, order) -> tuple[str, float] | None:
        try:
            side = "buy" if int(getattr(order, "side", -1)) == 0 else "sell"
        except (TypeError, ValueError):
            return None
        price = getattr(order, "price", None)
        if price is None:
            price = getattr(order, "limit_price", None)
        try:
            px = float(price)
        except (TypeError, ValueError):
            return None
        return side, px

    def _direct_cancel_entry_quotes(self, response, book_id: int, *, reason: str) -> int:
        orders = self._direct_entry_quote_orders(int(book_id))
        protected_id = self._direct_protected_partial_order_id(int(book_id))
        order_ids = [getattr(order, "id", None) for order in orders]
        order_ids = [
            oid for oid in order_ids
            if oid is not None and (protected_id is None or int(oid) != int(protected_id))
        ]
        if not order_ids:
            return 0
        if self._count_book_instructions(response, int(book_id)) >= self.max_instructions_per_book:
            return 0
        try:
            response.cancel_orders(book_id=int(book_id), order_ids=order_ids, delay=0)
        except Exception:
            return 0
        self._direct_quote_cancels = int(getattr(self, "_direct_quote_cancels", 0) or 0) + 1
        # A1.9.0.3: register the cancel.  An entry quote on a book that is long
        # rests on the close side, so before A1.9.0.3 killing one retired a row
        # the observer was tracking and the lifecycle read EXPIRED.
        try:
            self._a19_note_exit_cancel(
                int(book_id), order_ids, ABSENT_ENTRY_QUOTE_CANCEL,
            )
        except Exception:
            pass
        if "REPRICE" in str(reason or "").upper():
            self._direct_quote_reprices = int(getattr(self, "_direct_quote_reprices", 0) or 0) + 1
        try:
            self._emit(
                "DIRECT_QUOTE_LIFECYCLE", force=True,
                tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                action="CANCEL", reason=str(reason or "CANCEL"), resting_orders=len(order_ids),
                quote_manager_version=DIRECT_QUOTE_MANAGER_VERSION,
            )
        except Exception:
            pass
        return 1

    def _direct_maintain_unselected_entry_quotes(self, response, state, selected_ids: set[int]) -> int:
        """Keep valid flat Maker batches through top-K rotation; cancel invalid ones.

        This is deliberately cheap/current-only.  GTT expiry provides the hard age
        ceiling; no historical win/loss data is consulted.
        """
        placed = 0
        books = getattr(state, "books", None) or {}
        eps = float(self._execution_flat_epsilon())
        for raw_id, book in books.items():
            book_id = int(raw_id)
            if book_id in selected_ids:
                continue
            orders = self._direct_entry_quote_orders(book_id)
            if not orders:
                continue
            try:
                net = abs(float(self._direct_signed_inventory(book_id)))
            except Exception:
                net = 0.0
            # A1.7.3: if this is a tracked partial fill, the original legal
            # remainder is the only sub-minimum order we are allowed to keep.
            # The recovery service cancels the wrong-side sibling separately.
            recovery = (getattr(self, "_direct_partial_recovery", {}) or {}).get(book_id)
            if net > eps and recovery is not None and direct_is_dust_inventory(
                net, min_order=float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25), eps=eps,
            ):
                continue
            # Once ordinary inventory appears, entry quotes no longer own the book.
            if net > eps:
                placed += self._direct_cancel_entry_quotes(
                    response, book_id, reason="INVENTORY_OPENED",
                )
                continue
            if not getattr(book, "bids", None) or not getattr(book, "asks", None):
                placed += self._direct_cancel_entry_quotes(response, book_id, reason="BAD_BOOK")
                continue
            bid = float(book.bids[0].price)
            ask = float(book.asks[0].price)
            mid = 0.5 * (bid + ask) if bid > 0.0 and ask > bid else 0.0
            if mid <= 0.0:
                placed += self._direct_cancel_entry_quotes(response, book_id, reason="BAD_BOOK")
                continue
            spread_bps = (ask - bid) / mid * 10_000.0
            maker_fee_bps = float(self._research_live_fee_bps(book_id, is_maker=True))
            edge_bps = 0.5 * max(0.0, spread_bps) - maker_fee_bps
            pairs = [self._direct_order_side_price(order) for order in orders]
            pairs = [row for row in pairs if row is not None]
            if keep_unselected_quote(
                existing=pairs, best_bid=bid, best_ask=ask, current_edge_bps=edge_bps,
                min_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS,
            ):
                self._direct_quote_keeps = int(getattr(self, "_direct_quote_keeps", 0) or 0) + 1
                self._direct_quote_unselected_keeps = int(
                    getattr(self, "_direct_quote_unselected_keeps", 0) or 0
                ) + 1
                continue
            placed += self._direct_cancel_entry_quotes(
                response, book_id, reason="UNSELECTED_INVALID",
            )
        return placed

    # ------------------------------------------------------------------
    # Direct Maker placement.  LifecycleEV/ExecutionController has already
    # decided that Maker is the winning execution mode, so do not re-run old
    # expected-PnL/fill gates here.
    # ------------------------------------------------------------------
    def _simple_place_maker(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        book,
        profile,
        prediction,
        inventory,
        regime_params,
        size: float,
        edge_bias: float,
    ) -> int:
        if size <= 0.0 or not getattr(book, "bids", None) or not getattr(book, "asks", None):
            return 0

        bid = float(book.bids[0].price)
        ask = float(book.asks[0].price)
        if bid <= 0.0 or ask <= bid:
            return 0
        mid = 0.5 * (bid + ask)
        spread = ask - bid

        # Explicitly use Strategy1's quote geometry.  V4.16 economics decides
        # whether to trade; quote geometry should stay simple and predictable.
        prices = Strategy1.skewed_quote_prices(
            self,
            bid,
            ask,
            float(getattr(prediction, "score", 0.0) or 0.0),
            float(getattr(inventory, "inventory_ratio", 0.0) or 0.0),
            regime_params,
            int(state.config.priceDecimals),
            edge_bias=edge_bias,
        )
        if not prices:
            return 0
        raw_bid_px, raw_ask_px = prices
        bid_px, ask_px, geometry = cap_maker_quote_geometry(
            bid=bid, ask=ask, bid_px=raw_bid_px, ask_px=raw_ask_px,
            price_decimals=int(state.config.priceDecimals),
        )
        self._direct_quote_geometry_last[int(book_id)] = dict(geometry)

        spread_bps = (spread / mid * 10_000.0) if mid > 0.0 else 0.0
        maker_fee_bps = float(self._research_live_fee_bps(int(book_id), is_maker=True))
        current_edge_bps = 0.5 * max(0.0, spread_bps) - maker_fee_bps
        live_entry_orders = self._direct_entry_quote_orders(int(book_id))
        if live_entry_orders:
            pairs = [self._direct_order_side_price(order) for order in live_entry_orders]
            pairs = [row for row in pairs if row is not None]
            tick_size = 10.0 ** (-max(0, int(state.config.priceDecimals)))
            decision = decide_quote_batch(
                existing=pairs, desired_bid=float(bid_px), desired_ask=float(ask_px),
                best_bid=bid, best_ask=ask, current_edge_bps=current_edge_bps,
                min_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS, tick_size=tick_size,
            )
            if decision.action == QUOTE_ACTION_KEEP:
                self._direct_quote_keeps = int(getattr(self, "_direct_quote_keeps", 0) or 0) + 1
                try:
                    self._emit(
                        "DIRECT_QUOTE_LIFECYCLE", force=True,
                        tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                        action="KEEP", reason=decision.reason,
                        current_edge_bps=float(current_edge_bps),
                        max_price_delta=float(decision.max_price_delta),
                        max_touch_drift_bps=float(decision.max_touch_drift_bps),
                        quote_manager_version=DIRECT_QUOTE_MANAGER_VERSION,
                    )
                except Exception:
                    pass
                return 0
            return self._direct_cancel_entry_quotes(
                response, int(book_id), reason=str(decision.action),
            )
        if self._direct_book_has_live_order(int(book_id)):
            # A non-entry order owns this flat book until the next state snapshot.
            return 0

        # Freshness protects only NEW exposure. KEEP/CANCEL decisions above must
        # remain available even on a slow request.
        request_started = getattr(self, "_direct_request_wall_started", None)
        if request_started is not None:
            pre_submit_age_ms = (time.perf_counter() - float(request_started)) * 1000.0
            if pre_submit_age_ms > DIRECT_MAX_PRE_SUBMIT_AGE_MS:
                self._direct_freshness_budget_skips = int(
                    getattr(self, "_direct_freshness_budget_skips", 0) or 0
                ) + 1
                tick_now = int(getattr(self, "_tick", 0) or 0)
                if tick_now <= 2 or tick_now % DIRECT_TELEMETRY_SAMPLE_TICKS == 0:
                    try:
                        self._emit(
                            "DIRECT_FRESHNESS_SKIP", force=True, tick=tick_now, book=int(book_id),
                            pre_submit_age_ms=float(pre_submit_age_ms),
                            max_pre_submit_age_ms=DIRECT_MAX_PRE_SUBMIT_AGE_MS,
                        )
                    except Exception:
                        pass
                return 0

        baseline_expiry_ns = maker_expiry_ns_for_regime(
            getattr(self, "_research_market_regime", "NORMAL")
        )
        publish_ns = int(getattr(getattr(state, "config", None), "publish_interval", 0) or 0)
        expiry_ns = direct_recovery_expiry_ns(
            baseline_ns=int(baseline_expiry_ns), publish_interval_ns=publish_ns,
        )
        tick = int(getattr(self, "_tick", 0) or 0)
        if tick <= 2 or tick % DIRECT_TELEMETRY_SAMPLE_TICKS == 0:
            try:
                self._emit(
                    "DIRECT_MAKER_GEOMETRY", force=True,
                    tick=tick, book=int(book_id),
                    direct_execution_quality_version=DIRECT_EXECUTION_QUALITY_VERSION,
                    best_bid=bid, best_ask=ask, raw_bid_px=float(raw_bid_px),
                    raw_ask_px=float(raw_ask_px), bid_px=float(bid_px), ask_px=float(ask_px),
                    maker_ttl_ms=float(expiry_ns) / 1_000_000.0, **geometry,
                )
            except Exception:
                pass

        qty = self._round_order_size(float(size), int(state.config.volumeDecimals))
        min_size = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        if qty + 1e-12 < min_size:
            return 0

        # A symmetric Maker pair is a single Strategy1-style opportunity.  The
        # volume cap is checked before construction and again by final validation.
        if not self._research_can_add_volume(state, int(book_id), qty * mid * 2.0):
            return 0

        acct = self.accounts.get(book_id)
        if acct is None:
            return 0

        placed = 0
        mem = self._mem(book_id)
        buy_touch_dist = max(0.0, (mid - bid_px) / max(spread, 1e-12))
        sell_touch_dist = max(0.0, (ask_px - mid) / max(spread, 1e-12))

        if (
            float(getattr(acct.quote_balance, "free", 0.0) or 0.0) >= bid_px * qty
            and self._count_book_instructions(response, book_id) < self.max_instructions_per_book
        ):
            self._record_fill_quote(mem, "buy", buy_touch_dist)
            response.limit_order(
                book_id=book_id,
                direction=OrderDirection.BUY,
                quantity=qty,
                price=bid_px,
                clientOrderId=70000 + int(book_id) * 10 + 1,
                stp=STP.CANCEL_BOTH,
                postOnly=True,
                timeInForce=TimeInForce.GTT,
                expiryPeriod=expiry_ns,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )
            placed += 1
            mem.quote_count += 1

        if (
            float(getattr(acct.base_balance, "free", 0.0) or 0.0) >= qty
            and self._count_book_instructions(response, book_id) < self.max_instructions_per_book
        ):
            self._record_fill_quote(mem, "sell", sell_touch_dist)
            response.limit_order(
                book_id=book_id,
                direction=OrderDirection.SELL,
                quantity=qty,
                price=ask_px,
                clientOrderId=70000 + int(book_id) * 10 + 2,
                stp=STP.CANCEL_BOTH,
                postOnly=True,
                timeInForce=TimeInForce.GTT,
                expiryPeriod=expiry_ns,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )
            placed += 1
            mem.quote_count += 1

        if placed:
            self._direct_quote_new_batches = int(
                getattr(self, "_direct_quote_new_batches", 0) or 0
            ) + 1
        return placed

    # ------------------------------------------------------------------
    # A1.7.5 QUIET shadow measurement.  Diagnostic only: nothing in this
    # section may influence an entry, an exit, or a published order.
    # ------------------------------------------------------------------
    @staticmethod
    def _direct_a175_mid(book) -> float | None:
        try:
            if not getattr(book, "bids", None) or not getattr(book, "asks", None):
                return None
            mid = 0.5 * (float(book.bids[0].price) + float(book.asks[0].price))
        except (AttributeError, IndexError, TypeError, ValueError):
            return None
        return mid if math.isfinite(mid) and mid > 0.0 else None

    def _direct_a175_shadow_record(
        self, *, book_id: int, book, tick: int,
        blocked_edge_bps: float, effective_floor_bps: float,
    ) -> None:
        """Remember one entry the frozen A1.7.4.5 floor blocked."""
        try:
            mid = self._direct_a175_mid(book)
            if mid is None:
                return
            ledger = getattr(self, "_direct_a175_shadow_ledger", None)
            if ledger is None:
                ledger = OrderedDict()
                self._direct_a175_shadow_ledger = ledger
            key = (int(book_id), int(tick))
            if key in ledger:
                return
            ledger[key] = {
                "book": int(book_id),
                "tick": int(tick),
                "mid": float(mid),
                "blocked_edge_bps": float(blocked_edge_bps),
                "effective_floor_bps": float(effective_floor_bps),
            }
            self._direct_a175_shadow_recorded = int(
                getattr(self, "_direct_a175_shadow_recorded", 0) or 0
            ) + 1
            while len(ledger) > DIRECT_A175_SHADOW_LEDGER_MAX:
                ledger.popitem(last=False)
        except Exception:
            pass

    def _direct_a175_shadow_resolve(self, book_id: int, book) -> None:
        """Emit forward mid-markout for matured shadow entries on one book.

        Forward mid-markout measures *adverse selection*, not fill probability:
        it says whether the blocked entry would have been entered into a market
        moving against it.  It therefore bounds the upside of relaxing the
        floor rather than proving it.
        """
        try:
            ledger = getattr(self, "_direct_a175_shadow_ledger", None)
            if not ledger:
                return
            mid = self._direct_a175_mid(book)
            if mid is None:
                return
            tick = int(getattr(self, "_tick", 0) or 0)
            matured = [
                k for k in ledger
                if k[0] == int(book_id)
                and tick - k[1] >= DIRECT_A175_SHADOW_HORIZON_TICKS
            ]
            for key in matured:
                row = ledger.pop(key, None)
                if not row:
                    continue
                entry_mid = float(row.get("mid", 0.0) or 0.0)
                if entry_mid <= 0.0:
                    continue
                markout_bps = ((mid - entry_mid) / entry_mid) * 10_000.0
                # A Maker entry is two-sided, so adverse selection is the
                # magnitude of the move away from the entry mark.
                adverse = abs(markout_bps) > float(row.get("blocked_edge_bps", 0.0) or 0.0)
                self._direct_a175_shadow_resolved = int(
                    getattr(self, "_direct_a175_shadow_resolved", 0) or 0
                ) + 1
                if adverse:
                    self._direct_a175_shadow_adverse = int(
                        getattr(self, "_direct_a175_shadow_adverse", 0) or 0
                    ) + 1
                self._emit(
                    "A175_QUIET_SHADOW_OUTCOME", force=True,
                    tick=tick, book=int(book_id),
                    blocked_tick=int(row.get("tick", -1) or -1),
                    horizon_ticks=int(tick - int(row.get("tick", tick) or tick)),
                    blocked_edge_bps=float(row.get("blocked_edge_bps", 0.0) or 0.0),
                    effective_floor_bps=float(row.get("effective_floor_bps", 0.0) or 0.0),
                    entry_mid=entry_mid, forward_mid=float(mid),
                    forward_mid_markout_bps=float(markout_bps),
                    would_have_been_adverse=int(bool(adverse)),
                    version=DIRECT_QUIET_ENTRY_VERSION,
                )
        except Exception:
            pass

    def _place_skewed_quotes(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        book,
        profile,
        prediction,
        inventory,
        regime_params,
        size: float,
        edge_bias: float,
        stats: dict | None = None,
    ) -> int:
        """Single entry authority: hard safety -> current Maker edge -> Maker/Skip."""
        if self._research_in_transition_quarantine():
            return 0
        # A1.9.9: no new exposure while a rewind resync is open, nor on a book
        # whose venue position is still waiting for a price to be reseeded at.
        if self._a199_entry_blocked(book_id):
            return 0
        if str(getattr(inventory, "band", "FLAT") or "FLAT").upper() != "FLAT":
            return 0

        self._research_volume_cap_bind_book(book_id)
        cap = self._research_volume_cap_quote(state)
        volume_capped = cap > 0.0 and self._research_volume_cap_remaining(state, book_id) <= 0.0
        market_toxic = str(getattr(self, "_research_market_regime", "") or "").upper() == "TOXIC"
        guard = evaluate_risk_guard(
            inventory_blocked=False,
            volume_capped=volume_capped,
            toxic=market_toxic,
            unsafe=False,
        )
        if not guard.safe:
            return 0
        if getattr(self, "_research_absolute_protection_active", False) and not new_exposure_allowed(BAND_ABSOLUTE):
            return 0

        ev = (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id))
        if ev is None:
            mem = self._mem(book_id)
            expected_alpha = self.expected_alpha_score(
                profile,
                prediction,
                self.estimate_fill_probability(
                    book,
                    0.5 * (float(book.bids[0].price) + float(book.asks[0].price)),
                    float(book.asks[0].price) - float(book.bids[0].price),
                    float(getattr(profile, "trade_rate", 0.0) or 0.0),
                    float(book.bids[0].price),
                    float(book.asks[0].price),
                    book_id=book_id,
                ),
                mem,
                book_id,
                state.timestamp,
            )
            ev = self._research_score_ev_for_book(int(book_id), expected_alpha, mem)
            self._research_score_ev_last[int(book_id)] = ev

        # A1.6 does not let learned/forecast lifecycle state veto entry.
        # Hard safety was already checked above; current observable edge owns the
        # economic decision.
        life = float(getattr(ev, "lifecycle_ev", getattr(ev, "trading_ev", 0.0)) or 0.0)
        capture_bps = max(0.0, float(getattr(ev, "spread_capture_bps", 0.0) or 0.0))
        maker_fee_bps = float(getattr(ev, "maker_fee_bps", 0.0) or 0.0)
        current_edge_bps = capture_bps - maker_fee_bps
        spread_bps = 2.0 * capture_bps
        trade_rate = max(0.0, float(getattr(profile, "trade_rate", 0.0) or 0.0))
        a1745_gate = quiet_zero_rebate_entry_gate(
            regime=str(getattr(self, "_research_market_regime", "") or ""),
            maker_fee_bps=maker_fee_bps,
            spread_bps=spread_bps,
            trade_rate=trade_rate,
            base_min_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS,
        )
        effective_maker_min_edge_bps = float(a1745_gate.effective_min_edge_bps)
        # A1.9.5 step 4: breadth relief, applied to the floor BEFORE
        # `choose_direct_execution` reads it.  This is the stage that actually
        # refuses one-away books (545 blocks, median edge +8.40 against a
        # floor raised from 2.5 to 15.0); A1.9.3's hook one stage earlier
        # measured zero events across two runs.
        effective_maker_min_edge_bps = self._a195_breadth_relief(
            book_id=int(book_id),
            observations_remaining=int(getattr(ev, "observations_remaining", 3) or 3),
            current_edge_bps=float(current_edge_bps),
            base_min_edge_bps=float(DIRECT_MAKER_MIN_EDGE_BPS),
            effective_min_edge_bps=effective_maker_min_edge_bps,
        )
        # A1.7.5: resolve any matured shadow entries for this book. Strictly
        # diagnostic and deliberately placed after the floor is already fixed.
        self._direct_a175_shadow_resolve(int(book_id), book)

        remaining_obs = int(getattr(ev, "observations_remaining", 3) or 3)
        required_obs = int(getattr(ev, "required_observation_count", 3) or 3)
        min_size = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        # Keep acquisition size intentionally simple and conservative. Throughput
        # comes from better opportunity recall, not bigger individual positions.
        maker_size = min_size
        decision = choose_direct_execution(
            maker_lifecycle_ev=life,
            maker_current_edge_bps=current_edge_bps,
            maker_min_edge_bps=effective_maker_min_edge_bps,
            directional_score=float(getattr(prediction, "score", 0.0) or 0.0),
            crossing_bps=capture_bps,
            maker_size=maker_size,
            taker_clip=min_size,
            neutral_fallback=is_neutral_forecast(prediction),
            maker_fee_bps=maker_fee_bps,
            taker_fee_bps=float(getattr(ev, "taker_fee_bps", 0.0) or 0.0),
            slippage_bps=float(getattr(self, "research_lifecycle_slippage_bps", 0.75) or 0.75),
            expected_markout_bps=0.0,
        )

        tick = int(getattr(self, "_tick", 0) or 0)
        if a1745_gate.active:
            self._direct_a1745_gate_active = int(getattr(self, "_direct_a1745_gate_active", 0) or 0) + 1
            base_would_pass = current_edge_bps + 1e-12 >= DIRECT_MAKER_MIN_EDGE_BPS
            blocked_by_a1745 = bool(
                decision.action == EXEC_ACTION_SKIP
                and base_would_pass
                and current_edge_bps + 1e-12 < effective_maker_min_edge_bps
            )
            if blocked_by_a1745:
                self._direct_a1745_entry_blocks = int(getattr(self, "_direct_a1745_entry_blocks", 0) or 0) + 1
                a1745_event = "A1745_ENTRY_BLOCK_LOW_EDGE"
                # A1.7.5: record the blocked opportunity for later measurement.
                # Diagnostic only -- the floor above already made the decision.
                self._direct_a175_shadow_record(
                    book_id=int(book_id), book=book, tick=tick,
                    blocked_edge_bps=float(current_edge_bps),
                    effective_floor_bps=float(effective_maker_min_edge_bps),
                )
            elif decision.action == EXEC_ACTION_MAKER:
                self._direct_a1745_entry_allows = int(getattr(self, "_direct_a1745_entry_allows", 0) or 0) + 1
                a1745_event = "A1745_ENTRY_ALLOWED"
            else:
                a1745_event = "A1745_QUIET_ZERO_REBATE_GATE"
            try:
                self._emit(
                    a1745_event, force=True, tick=tick, book=int(book_id),
                    current_maker_edge_bps=float(current_edge_bps),
                    selected_action=str(decision.action),
                    **a1745_gate.as_log(),
                )
            except Exception:
                pass
        elif decision.action == EXEC_ACTION_MAKER and (tick <= 2 or tick % DIRECT_TELEMETRY_SAMPLE_TICKS == 0):
            self._direct_a1745_regime_bypass = int(getattr(self, "_direct_a1745_regime_bypass", 0) or 0) + 1
            try:
                self._emit(
                    "A1745_REGIME_BYPASS", force=True, tick=tick, book=int(book_id),
                    current_maker_edge_bps=float(current_edge_bps),
                    selected_action=str(decision.action),
                    **a1745_gate.as_log(),
                )
            except Exception:
                pass

        # A1.9.2 fee-conditioned book risk admission.  Placed after the A1.7.4.5
        # accounting so that gate's block/allow counts stay comparable across
        # versions, and before ENTRY_DECISION so the emitted action is the one
        # actually taken.  Only a MAKER acquisition can be suppressed; SKIP and
        # TAKER are left exactly as the economics decided them.
        a192_verdict: dict[str, Any] = {}
        if decision.action == EXEC_ACTION_MAKER:
            try:
                a192_verdict = self._a192_admission_verdict(
                    int(book_id), float(maker_fee_bps), int(tick),
                )
            except Exception:
                a192_verdict = {}
            if a192_verdict.get("suppress"):
                decision = replace(
                    decision, action=EXEC_ACTION_SKIP,
                    reason=str(a192_verdict.get("reason", A192_SUPPRESS_FLAGGED)),
                )
                try:
                    self._emit(
                        "A192_ENTRY_SUPPRESSED", force=True, tick=int(tick),
                        book=int(book_id),
                        current_maker_edge_bps=float(current_edge_bps),
                        **{k: v for k, v in a192_verdict.items() if k != "suppress"},
                    )
                except Exception:
                    pass
            elif str(a192_verdict.get("reason", "")) == A192_ALLOW_CAP:
                try:
                    self._emit(
                        "A192_CAP_BLOCK", force=True, tick=int(tick),
                        book=int(book_id),
                        max_suppression_pct=float(self.A192_MAX_SUPPRESSION_PCT),
                        **{k: v for k, v in a192_verdict.items() if k != "suppress"},
                    )
                except Exception:
                    pass
            elif str(a192_verdict.get("reason", "")) == A192_ALLOW_SEVERITY_RANK:
                # A1.9.2.1.  Force-emitted like the other two so the allocation
                # can be audited without the ENTRY_DECISION sampling rate: the
                # A1.9.2 review could not measure true suppression share
                # because ENTRY_DECISION is written every Nth tick while the
                # A192 events are not.
                try:
                    self._emit(
                        "A192_SEVERITY_DEFER", force=True, tick=int(tick),
                        book=int(book_id),
                        current_maker_edge_bps=float(current_edge_bps),
                        max_suppression_pct=float(self.A192_MAX_SUPPRESSION_PCT),
                        **{k: v for k, v in a192_verdict.items() if k != "suppress"},
                    )
                except Exception:
                    pass

        if decision.action != EXEC_ACTION_SKIP or tick <= 2 or tick % DIRECT_TELEMETRY_SAMPLE_TICKS == 0:
            try:
                self._emit(
                    "ENTRY_DECISION",
                    force=True,
                    tick=tick,
                    book=int(book_id),
                    lane="DIRECT_OBSERVABLE",
                    safe=1,
                    lifecycle_ev=life,
                    current_spread_capture_bps=capture_bps,
                    current_maker_fee_bps=maker_fee_bps,
                    current_maker_edge_bps=current_edge_bps,
                    observations_remaining=remaining_obs,
                    required_observations=required_obs,
                    total_score_value=float(getattr(ev, "total_score_component", 0.0) or 0.0),
                    prediction_source=prediction_source_of(prediction),
                    neutral_fallback_used=int(is_neutral_forecast(prediction)),
                    learned_entry_authority=0,
                    direct_mode=1,
                    a192_admission=str(a192_verdict.get("reason", "")) or "NOT_EVALUATED",
                    a192_suppressed=int(bool(a192_verdict.get("suppress"))),
                    **a1745_gate.as_log(),
                    **decision.as_log(),
                )
            except Exception:
                pass

        fail_streak = getattr(self, "_direct_edge_fail_streak", {}) or {}
        cooldown = getattr(self, "_direct_edge_cooldown_until", {}) or {}
        if decision.action == EXEC_ACTION_SKIP:
            n = int(fail_streak.get(int(book_id), 0) or 0) + 1
            if n >= DIRECT_EDGE_FAIL_STREAK:
                cooldown[int(book_id)] = tick + DIRECT_EDGE_COOLDOWN_TICKS
                n = 0
            fail_streak[int(book_id)] = n
        else:
            fail_streak[int(book_id)] = 0
            cooldown.pop(int(book_id), None)
        self._direct_edge_fail_streak = fail_streak
        self._direct_edge_cooldown_until = cooldown

        if decision.action == EXEC_ACTION_SKIP:
            return 0
        if decision.action == EXEC_ACTION_TAKER:
            qty = float(decision.taker_size or min_size)
            if qty <= 0.0:
                return 0
            if self._research_execute_entry_taker(response, book_id, book, qty, prediction):
                self._research_note_entry_submit_if_flat(
                    book_id,
                    getattr(state, "timestamp", None),
                    inventory_before=0.0,
                )
                return 1
            return 0

        # Maker is the only remaining acquisition action. No learned/forecast
        # economics are allowed to veto it after current-edge authority passed.
        placed = self._simple_place_maker(
            response,
            state,
            book_id,
            book,
            profile,
            prediction,
            inventory,
            regime_params,
            float(decision.maker_size or min_size),
            edge_bias,
        )
        if placed:
            self._research_note_entry_submit_if_flat(
                book_id,
                getattr(state, "timestamp", None),
                inventory_before=0.0,
            )
        return placed

    # ------------------------------------------------------------------
    # A1.5 keeps A1.3 dust liveness + session-persistent Direct quality.
    # ------------------------------------------------------------------
    def _direct_dust_count(self, state) -> int:
        min_size = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        eps = float(self._execution_flat_epsilon())
        count = 0
        for raw_id in (getattr(state, "books", None) or {}).keys():
            try:
                qty = float(self._research_abs_inventory(int(raw_id)))
            except Exception:
                continue
            is_dust = qty > eps and self._v600_counts_as_dust(int(raw_id), qty, eps=eps, min_order=min_size)
            if is_dust:
                count += 1
        return int(count)

    def _research_fast_screen(self, state):
        """A1.5 Direct FastPath: cheap 128-book pass -> bounded top-K.

        This intentionally bypasses the inherited heavy lane/rolling-economics
        screen. Inventory management is still full-universe and authoritative in
        ``build_mm_strategy_instructions``.
        """
        self._direct_fastpath_screen_calls = int(
            getattr(self, "_direct_fastpath_screen_calls", 0) or 0
        ) + 1
        books = getattr(state, "books", None) or {}
        tick = int(getattr(self, "_tick", 0) or 0)
        eps = float(self._execution_flat_epsilon())
        min_size = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        profile_cache = getattr(self, "_direct_fastpath_profile_cache", {}) or {}
        last_selected = getattr(self, "_direct_fastpath_last_selected_tick", {}) or {}
        cooldown_until = getattr(self, "_direct_edge_cooldown_until", {}) or {}

        cold = self._v501_cold_books()
        raw_rows = []
        qualified_count = 0
        actual_nonflat = 0
        active_nonflat = 0
        dust_nonflat = 0
        # v6.0.1 C1: the dust the normalizer can work; only these hold the recovery reserve.
        workable_dust = 0
        total_abs_base = 0.0
        # A1.9.5 F3 needs dust BASE separated from productive BASE, and this
        # is the only loop that already classifies every book.
        dust_abs_base = 0.0
        for raw_id, book in books.items():
            bid = int(raw_id)
            try:
                qty = abs(float(self._research_abs_inventory(bid)))
            except Exception:
                qty = 0.0
            has_inv = qty > eps
            is_dust = bool(has_inv and self._v600_counts_as_dust(bid, qty, eps=eps, min_order=min_size))
            if (has_inv or bid in (getattr(self, "_v600_class", None) or {})
                    or bid in (getattr(self, "_v600_inherited_parked", None) or {})):
                try:
                    self._v600_note_class(bid, qty)
                except Exception:
                    self._v600_errors = int(getattr(self, "_v600_errors", 0) or 0) + 1
            if has_inv:
                actual_nonflat += 1
                total_abs_base += qty
                if is_dust:
                    dust_nonflat += 1
                    dust_abs_base += qty
                    if self._v601_is_workable(bid, qty, min_order=min_size):
                        workable_dust += 1
                else:
                    active_nonflat += 1

            try:
                kappa = self._research_kappa_book(bid)
                remaining = max(0, int(getattr(kappa, "observations_remaining", 3) or 0))
                qualified = bool(getattr(kappa, "eligible", False))
            except Exception:
                remaining, qualified = 3, False
            if bid in cold:
                # v5.0.1: eligible, but scored 0.0 until one more round trip activates it.
                remaining, qualified = 1, False
            if qualified:
                qualified_count += 1

            bpx = float(book.bids[0].price) if getattr(book, "bids", None) else 0.0
            apx = float(book.asks[0].price) if getattr(book, "asks", None) else 0.0
            mid = 0.5 * (bpx + apx) if bpx > 0.0 and apx > bpx else 0.0
            spread_bps = ((apx - bpx) / mid * 10_000.0) if mid > 0.0 else 0.0
            maker_fee_bps = float(self._research_live_fee_bps(bid, is_maker=True))
            edge_bps = observable_maker_edge_bps(
                spread_bps=spread_bps, maker_fee_bps=maker_fee_bps,
            )
            def _top_qty(order):
                for name in ("quantity", "remainingQuantity", "remaining_quantity", "size", "qty"):
                    try:
                        value = float(getattr(order, name, 0.0) or 0.0)
                    except Exception:
                        continue
                    if value > 0.0:
                        return value
                return 0.0
            bid_qty = _top_qty(book.bids[0]) if getattr(book, "bids", None) else 0.0
            ask_qty = _top_qty(book.asks[0]) if getattr(book, "asks", None) else 0.0
            top_min = min(bid_qty, ask_qty) if bid_qty > 0.0 and ask_qty > 0.0 else 0.0
            liquidity_quality = min(1.0, top_min / max(min_size, 1e-9))
            raw_rows.append((
                bid, remaining, qualified, has_inv, is_dust, spread_bps,
                maker_fee_bps, edge_bps, liquidity_quality,
            ))

        # Breadth target is number of qualified books (normally 80).
        # ``research_kappa_completion_target`` is observations *per book* (3)
        # and must never be used here.
        target = max(1, int(
            getattr(
                self,
                "research_score_target_books",
                getattr(self, "research_total_score_full_breadth_books", 80),
            )
            or 80
        ))
        score_deficit = max(0, target - qualified_count)
        rows: list[FastPathRow] = []
        priority_map: dict[int, float] = {}
        for (bid, remaining, qualified, has_inv, is_dust, spread_bps,
             maker_fee_bps, edge_bps, liquidity_quality) in raw_rows:
            stale_ticks = max(0, tick - int(last_selected.get(bid, 0) or 0))
            priority = cheap_priority(
                observations_remaining=remaining, qualified=qualified,
                spread_bps=spread_bps, maker_fee_bps=maker_fee_bps,
                liquidity_quality=liquidity_quality,
                ticks_since_selected=stale_ticks, score_deficit=score_deficit,
            )
            cooled = bool(int(cooldown_until.get(bid, 0) or 0) > tick)
            priority_map[bid] = float(priority)
            rows.append(FastPathRow(
                book_id=bid, priority=priority, observations_remaining=remaining,
                qualified=qualified, has_inventory=has_inv, is_dust=is_dust,
                observable_edge_bps=edge_bps, maker_fee_bps=maker_fee_bps,
                liquidity_quality=liquidity_quality, cooled=cooled,
            ))
        self._direct_fastpath_priority_by_book = priority_map

        configured = direct_fastpath_candidate_count(max(
            DIRECT_FASTPATH_CANDIDATE_COUNT,
            int(getattr(self, "research_candidate_count", DIRECT_FASTPATH_CANDIDATE_COUNT) or DIRECT_FASTPATH_CANDIDATE_COUNT),
        ))
        v621_cap = self._v621_cap_override(len(rows))
        selected = select_fastpath_rows(
            rows, candidate_count=configured, score_deficit=score_deficit, tick=tick,
            cap_override=v621_cap,
        )
        selected_set = {int(x) for x in selected}
        for bid in selected_set:
            last_selected[bid] = tick
        self._direct_fastpath_last_selected_tick = last_selected

        forced_inventory = [r.book_id for r in rows if r.has_inventory and r.book_id in selected_set]
        self._v621_last = {
            "universe": int(len(rows)), "cap": int(v621_cap or 0), "selected": int(len(selected)),
            "inventory_rows": int(sum(1 for r in rows if r.has_inventory)),
            "forced_inventory": int(len(forced_inventory)),
        }
        forced_dust = [r.book_id for r in rows if r.is_dust and r.book_id in selected_set]
        forced_kappa = [
            r.book_id for r in rows
            if (not r.qualified and r.observations_remaining in (1, 2) and r.book_id in selected_set)
        ]
        screened_extra = [
            r.book_id for r in rows
            if r.book_id in selected_set and r.book_id not in set(forced_inventory)
            and r.book_id not in set(forced_kappa)
        ]
        result = ScreenResult(
            selected=list(selected),
            forced=list(dict.fromkeys(forced_inventory + forced_kappa)),
            forced_inventory=forced_inventory, forced_dust=forced_dust,
            forced_kappa=forced_kappa, forced_hard_risk=[], forced_live=[],
            screened_extra=screened_extra, candidate_count=len(selected), universe=len(rows),
        )
        self._research_last_screen = result
        self._research_inventory_lane_diag = {
            **(getattr(self, "_research_inventory_lane_diag", {}) or {}),
            "actual_nonflat_inventory": int(actual_nonflat),
            "active_nonflat_inventory": int(active_nonflat),
            "dust_nonflat_inventory": int(dust_nonflat),
            "v601_workable_dust_inventory": int(workable_dust),
            "total_abs_base_inventory": float(total_abs_base),
            "dust_abs_base_inventory": float(dust_abs_base),
            # A1.6.1: every sub-minimum dust book is excluded from productive
            # open-book capacity. Exact BASE remains in total_abs_base_inventory.
            "direct_effective_open_books": int(active_nonflat),
            "direct_qualified_count": int(qualified_count),
            "direct_score_target_books": int(target),
            "direct_score_deficit": int(score_deficit),
        }
        if tick <= 2 or tick % DIRECT_TELEMETRY_SAMPLE_TICKS == 0:
            try:
                self._emit(
                    "DIRECT_FASTPATH", force=True, tick=tick,
                    direct_fastpath_version=DIRECT_FASTPATH_VERSION, universe=len(rows),
                    selected=len(selected), qualified=qualified_count, score_target=target, score_deficit=score_deficit,
                    inventory_books=actual_nonflat, dust_books=dust_nonflat,
                    cooled_books=sum(1 for r in rows if r.cooled),
                    nonnegative_edge_books=sum(1 for r in rows if r.observable_edge_bps >= 0.0),
                )
            except Exception:
                pass
            # A1.9.5 step 1, same sampled cadence.  Observation only.
            try:
                self._a195_emit_reconcile(state, tick)
            except Exception:
                pass
        return result

    def select_books_for_trading(self, state, predictions):
        """Build expensive profiles only for the bounded Direct FastPath set."""
        started = time.perf_counter()
        screen = getattr(self, "_research_last_screen", None)
        selected_all = [int(x) for x in (getattr(screen, "selected", None) or [])]
        if not selected_all:
            selected_all = [int(x) for x in (predictions or {}).keys()]
        forced_inventory = [int(x) for x in (getattr(screen, "forced_inventory", None) or [])]
        forced_dust = {int(x) for x in (getattr(screen, "forced_dust", None) or [])}
        # A1.6.1: parked dust has its own lightweight maintenance lane and is
        # never allowed to monopolize expensive deep-evaluation capacity.
        forced_inventory = [bid for bid in forced_inventory if bid not in forced_dust]
        priority_map = getattr(self, "_direct_fastpath_priority_by_book", {}) or {}
        forced_set = set(forced_inventory)
        ranked = sorted(
            (bid for bid in selected_all if bid not in forced_set),
            key=lambda bid: (float(priority_map.get(bid, -1e9)), -int(bid)),
            reverse=True,
        )
        selected_ids = list(dict.fromkeys(forced_inventory + ranked))[:max(DIRECT_FASTPATH_DEEP_COUNT, len(forced_inventory))]
        profiles = []
        cache = getattr(self, "_direct_fastpath_profile_cache", {}) or {}
        for bid in selected_ids:
            book = (getattr(state, "books", None) or {}).get(bid)
            if book is None:
                continue
            try:
                profile = self.build_book_profile(
                    bid, book, state, (predictions or {}).get(bid),
                    getattr(cache.get(bid), "raw_kappa", None),
                )
            except Exception:
                continue
            cache[bid] = profile
            profiles.append(profile)
        self._direct_fastpath_profile_cache = cache

        tier_counts: dict[str, int] = {}
        for p in profiles:
            tier = str(getattr(p, "tier", "INACTIVE") or "INACTIVE")
            tier_counts[tier] = int(tier_counts.get(tier, 0)) + 1
        selection = BookSelection(
            alpha_books=[int(p.book_id) for p in profiles if str(getattr(p, "tier", "")) != "RED"],
            maintenance_books=[int(p.book_id) for p in profiles if str(getattr(p, "tier", "")) == "INACTIVE"],
            avoid_books=[int(p.book_id) for p in profiles if str(getattr(p, "tier", "")) == "RED"],
            tier_counts=tier_counts, profiles=profiles,
        )
        self._last_selection = selection
        elapsed = (time.perf_counter() - started) * 1000.0
        self._research_timing["selection_ms"] = elapsed
        self._research_timing["ranking_ms"] = elapsed
        return selection

    # NOTE: this definition is SHADOWED.  A second
    # `_research_final_validate_instructions` is defined later in this same
    # class body (the A1.6.3 directional validator), so Python binds that one
    # and this A1.6.2 wrapper never executes.  Left byte-identical to HEAD --
    # A1.9.5 F3 puts its capacity accounting in the live validator instead.
    def _research_final_validate_instructions(self, response, state) -> None:
        # Preserve the authoritative validator, changing only total-open capacity
        # accounting for legal-uncloseable dust.  Absolute BASE risk is untouched.
        base_cap = int(getattr(self, "research_max_total_open_books", 8) or 8)
        dust = self._direct_dust_count(state)
        self.research_max_total_open_books = base_cap + dust_exempt_count(dust)
        try:
            super()._research_final_validate_instructions(response, state)
        finally:
            self.research_max_total_open_books = base_cap

    def _research_read_session(self, identity):
        # v5.0.4 H1: only this UID's own payload, or a legacy one the operator adopted.
        raw = self._v504_read_session(identity)
        if not isinstance(raw, dict):
            return raw
        # v6.1: the lot deques this UID saved; the A1.9.5 seed consumes them when they still match.
        self._v61_restored_lots = self._v61_lots_from_session(raw.get("direct_v61_lots"))
        direct = raw.get("direct_maker_quality_a1_5_1")
        same_version = isinstance(direct, dict)
        if not isinstance(direct, dict):
            direct = raw.get("direct_maker_quality_a1_5")  # migrate A1.5 session state
        if not isinstance(direct, dict):
            direct = raw.get("direct_maker_quality_a1_4")  # migrate A1.4 session state
        if not isinstance(direct, dict):
            direct = raw.get("direct_maker_quality_a1_3")  # migrate A1.3 session state
        if isinstance(direct, dict):
            by_book = direct.get("books")
            restored: dict[int, MakerLifecycleStats] = {}
            if isinstance(by_book, dict):
                for key, row in by_book.items():
                    try:
                        bid = int(key)
                    except (TypeError, ValueError):
                        continue
                    restored[bid] = MakerLifecycleStats.from_state(row)
            self._direct_maker_quality_by_book = restored
            self._direct_maker_quality_global = MakerLifecycleStats.from_state(
                direct.get("global")
            )
            if same_version:
                raw_baseline = direct.get("migration_baseline")
                baseline: dict[int, int] = {}
                if isinstance(raw_baseline, dict):
                    for key, value in raw_baseline.items():
                        try:
                            baseline[int(key)] = max(0, int(value or 0))
                        except (TypeError, ValueError):
                            continue
                self._direct_quality_migration_baseline = baseline
                self._direct_quality_migration_global_baseline = max(
                    0, int(direct.get("migration_global_baseline", 0) or 0)
                )
            else:
                # Legacy quality came from a different fee/execution regime.
                # Keep it as a weak prior and let 8 fresh A1.5.1 lifecycles per
                # book ramp its authority back to full strength.
                self._direct_quality_migration_baseline = {
                    bid: int(stats.count) for bid, stats in restored.items() if int(stats.count) > 0
                }
                self._direct_quality_migration_global_baseline = int(
                    getattr(self._direct_maker_quality_global, "count", 0) or 0
                )
            try:
                self._emit(
                    "DIRECT_QUALITY_RESTORE", force=True,
                    tick=getattr(self, "_tick", None), books=len(restored),
                    global_samples=int(self._direct_maker_quality_global.count),
                    direct_quality_version=DIRECT_QUALITY_VERSION,
                    migrated_books=len(getattr(self, "_direct_quality_migration_baseline", {}) or {}),
                    migration_global_baseline=int(getattr(self, "_direct_quality_migration_global_baseline", 0) or 0),
                )
            except Exception:
                pass
        return raw

    def _research_save_session(self, force: bool = False) -> None:
        super()._research_save_session(force=force)
        identity = getattr(self, "_research_session_identity", None)
        if identity is None or not getattr(identity, "simulation_id", None):
            return
        tick = int(getattr(self, "_tick", 0) or 0)
        if int(getattr(self, "_research_session_last_save_tick", -1)) != tick:
            return
        path = self._research_session_path(identity)
        try:
            with open(path, encoding="utf-8") as handle:
                payload = json.load(handle)
            # v5.0.3 G1: once the quiet gate has opened it stays open for this registration, and
            # its anchor is the earliest first state seen, so a restart cannot restart the clock.
            payload[V503_NEWCOMER_GATE_VERSION] = self._v503_gate_session_state()
            # v5.0.4 H1: whose evidence this is.  H4: where this UID's rounds begin.
            if bool(getattr(self, "research_v504_session_per_uid", True)):
                payload[OWNER_KEY] = owner_record(getattr(self, "uid", None))
            v504_start = (getattr(self, "_v504_mirror", None) or {}).get("start_ts")
            if bool(getattr(self, "research_v504_mirror_rounds", True)) and v504_start is not None:
                payload[V504_MIRROR_SESSION_KEY] = mirror_session_state(v504_start)
            # v6.1: the FIFO deques, so a restart floors against the validator's lots, not a reseed's.
            if self._v61_on():
                payload["direct_v61_lots"] = self._v61_lots_session_state()
            payload["direct_maker_quality_a1_5_1"] = {
                "version": DIRECT_QUALITY_VERSION,
                "global": getattr(self, "_direct_maker_quality_global", MakerLifecycleStats()).as_state(),
                "migration_baseline": {
                    str(book): int(count)
                    for book, count in sorted(
                        (getattr(self, "_direct_quality_migration_baseline", {}) or {}).items()
                    )
                },
                "migration_global_baseline": int(
                    getattr(self, "_direct_quality_migration_global_baseline", 0) or 0
                ),
                "books": {
                    str(book): stats.as_state()
                    for book, stats in sorted(
                        (getattr(self, "_direct_maker_quality_by_book", {}) or {}).items()
                    )
                },
            }
            tmp = path + f".direct.tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            pass

    def _research_clear_session_observations(self) -> None:
        super()._research_clear_session_observations()
        self._direct_maker_open = {}
        self._direct_maker_quality_by_book = {}
        self._direct_maker_quality_global = MakerLifecycleStats()
        self._direct_quality_migration_baseline = {}
        self._direct_quality_migration_global_baseline = 0
        self._direct_quality_last = {}
        self._direct_realization_cost_last = {}
        self._direct_lifecycle_fee_last = {}

    # ------------------------------------------------------------------
    # A1.7.3 partial-fill completion + irreducible-dust liveness.
    # ------------------------------------------------------------------
    def _direct_partial_fill_bound_order_id(self, event, *, is_maker: bool) -> int | None:
        """Return the exact resting order id that produced our Maker partial fill."""
        if not bool(is_maker) or event is None:
            return None
        for name in ("makerOrderId", "Mi", "maker_order_id"):
            value = getattr(event, name, None)
            try:
                if value is not None and int(value) > 0:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return None

    def _direct_partial_fill_timestamp_ns(self, event) -> int:
        if event is None:
            return 0
        for name in ("timestamp", "t"):
            value = getattr(event, name, None)
            try:
                if value is not None and int(value) > 0:
                    return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    def _direct_partial_hold_active(self, book_id: int, state=None, *, expire: bool = True) -> bool:
        """True while the exact legal partial remainder owns this book."""
        row = (getattr(self, "_direct_partial_recovery", {}) or {}).get(int(book_id))
        if not isinstance(row, dict):
            return False
        bound = row.get("bound_order_id")
        if not bool(row.get("preserve_existing_remainder", False)) or bound is None:
            return False
        try:
            bound = int(bound)
        except (TypeError, ValueError):
            return False
        now_ts = getattr(state, "timestamp", None) if state is not None else None
        if now_ts is None:
            now_ts = getattr(self, "_direct_current_state_timestamp_ns", 0)
        active = direct_bound_remainder_hold_active(
            fill_timestamp_ns=int(row.get("hold_start_timestamp_ns", 0) or 0),
            now_timestamp_ns=int(now_ts or 0),
            hard_ttl_ns=DIRECT_PARTIAL_HOLD_MAX_NS,
        )
        if active or not expire:
            return bool(active)
        if not bool(row.get("hold_expired", False)):
            row["hold_expired"] = True
            row["preserve_existing_remainder"] = False
            self._direct_partial_bound_expired = int(
                getattr(self, "_direct_partial_bound_expired", 0) or 0
            ) + 1
            try:
                self._emit(
                    "A1731_PARTIAL_REMAINDER_EXPIRE", force=True,
                    tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                    bound_order_id=int(bound),
                    hold_start_timestamp_ns=int(row.get("hold_start_timestamp_ns", 0) or 0),
                    now_timestamp_ns=int(now_ts or 0),
                    hard_ttl_ns=int(DIRECT_PARTIAL_HOLD_MAX_NS),
                )
            except Exception:
                pass
        return False

    def _direct_protected_partial_order_id(self, book_id: int, state=None) -> int | None:
        if not self._direct_partial_hold_active(int(book_id), state):
            return None
        row = (getattr(self, "_direct_partial_recovery", {}) or {}).get(int(book_id)) or {}
        try:
            return int(row.get("bound_order_id"))
        except (TypeError, ValueError):
            return None

    def _direct_emit_partial_replacement_block(self, book_id: int, *, path: str) -> None:
        self._direct_partial_replacement_blocks = int(
            getattr(self, "_direct_partial_replacement_blocks", 0) or 0
        ) + 1
        try:
            row = (getattr(self, "_direct_partial_recovery", {}) or {}).get(int(book_id)) or {}
            self._emit(
                "A1731_PARTIAL_REPLACEMENT_BLOCK", force=True,
                tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                path=str(path), bound_order_id=row.get("bound_order_id"),
                mode=str(row.get("mode") or "UNKNOWN"), new_full_clip_order=0,
            )
        except Exception:
            pass

    def _direct_note_partial_fill_recovery(
        self, *, book_id: int, before: float, after: float, is_maker: bool, event=None,
    ) -> None:
        """Track a legal order remainder whenever a fill leaves dust.

        No sub-minimum order is ever created here.  The state only tells the next
        request which *already legal* remainder may be preserved.  If that
        remainder later expires, bounded same-sign normalization can convert the
        dust into an actionable >= min-order position using reserved headroom.
        """
        try:
            bid = int(book_id)
            eps = float(self._execution_flat_epsilon())
            min_size = max(
                1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
            )
            plan = partial_recovery_plan(
                before=float(before), after=float(after), min_order=min_size, eps=eps,
            )
            current = (getattr(self, "_direct_partial_recovery", {}) or {}).get(bid)
            fill_order_id = self._direct_partial_fill_bound_order_id(event, is_maker=bool(is_maker))
            fill_timestamp_ns = self._direct_partial_fill_timestamp_ns(event)
            if plan is None:
                if current is not None and not direct_is_dust_inventory(
                    float(after), min_order=min_size, eps=eps,
                ):
                    mode = str(current.get("mode") or "UNKNOWN")
                    self._direct_partial_recovery.pop(bid, None)
                    self._direct_partial_hold_releases = int(
                        getattr(self, "_direct_partial_hold_releases", 0) or 0
                    ) + 1
                    if mode == "NORMALIZE" and abs(float(after)) + eps >= min_size:
                        self._direct_dust_normalize_fills = int(
                            getattr(self, "_direct_dust_normalize_fills", 0) or 0
                        ) + 1
                    try:
                        self._emit(
                            "A173_PARTIAL_FILL_RELEASE", force=True,
                            tick=int(getattr(self, "_tick", 0) or 0), book=bid,
                            mode=mode, net_before=float(before), net_after=float(after),
                            actionable=int(abs(float(after)) + eps >= min_size),
                            flat=int(abs(float(after)) <= eps),
                        )
                    except Exception:
                        pass
                return

            tick = int(getattr(self, "_tick", 0) or 0)
            # If an existing recovery is already making progress toward its
            # target, preserve its intent rather than reclassifying every dust fill.
            progressed_existing = False
            if current is not None:
                desired_side = str(current.get("desired_side") or plan.desired_side)
                mode = str(current.get("mode") or plan.mode)
                target = float(current.get("target_inventory", plan.target_inventory) or 0.0)
                preserve = bool(current.get("preserve_existing_remainder", plan.preserve_existing_remainder))
                first_tick = int(current.get("first_tick", tick) or tick)
                bound_order_id = current.get("bound_order_id")
                hold_start_timestamp_ns = int(current.get("hold_start_timestamp_ns", 0) or 0)
                progressed_existing = (
                    abs(target - float(after)) <= abs(target - float(before)) + eps
                )
            else:
                desired_side = plan.desired_side
                mode = plan.mode
                target = float(plan.target_inventory)
                preserve = bool(plan.preserve_existing_remainder)
                first_tick = tick
                bound_order_id = None
                hold_start_timestamp_ns = 0
                self._research_partial_fill_hold_candidates = int(
                    getattr(self, "_research_partial_fill_hold_candidates", 0) or 0
                ) + 1

            # A1.7.3.1: preservation is meaningful only for the exact resting
            # Maker order that generated the partial.  Rebind on a later legal
            # Maker partial; never claim that a Taker fill has a live remainder.
            if fill_order_id is not None and bool(plan.preserve_existing_remainder):
                if bound_order_id is None or int(bound_order_id) != int(fill_order_id):
                    hold_start_timestamp_ns = int(fill_timestamp_ns or 0)
                bound_order_id = int(fill_order_id)
                preserve = True
            elif bound_order_id is None:
                preserve = False

            # Crossing the recovery target invalidates the old remainder even
            # if the fill reduced distance to target: continuing the same order
            # would now increase opposite-side exposure.
            crossed_recovery_target = (
                (float(before) - float(target)) * (float(after) - float(target)) < -(eps * eps)
            )
            if (crossed_recovery_target or not progressed_existing) and not bool(plan.preserve_existing_remainder):
                desired_side = plan.desired_side
                mode = plan.mode
                target = float(plan.target_inventory)
                preserve = False
                bound_order_id = None
                hold_start_timestamp_ns = 0

            row = {
                "mode": mode,
                "target_inventory": float(target),
                "desired_side": str(desired_side),
                "preserve_existing_remainder": bool(preserve),
                "first_tick": int(first_tick),
                "last_progress_tick": tick,
                "net_base": float(after),
                "maker_origin": int(bool(is_maker)),
                "bound_order_id": (None if bound_order_id is None else int(bound_order_id)),
                "hold_start_timestamp_ns": int(hold_start_timestamp_ns or fill_timestamp_ns or 0),
                "last_progress_timestamp_ns": int(fill_timestamp_ns or 0),
                "hold_expired": False,
            }
            self._direct_partial_recovery[bid] = row
            try:
                self._emit(
                    "A173_PARTIAL_FILL_RECOVERY", force=True,
                    tick=tick, book=bid, mode=str(row["mode"]),
                    net_before=float(before), net_after=float(after),
                    target_inventory=float(row["target_inventory"]),
                    desired_side=str(row["desired_side"]),
                    preserve_existing_remainder=int(bool(row["preserve_existing_remainder"])),
                    min_order_size=float(min_size), maker_origin=int(bool(is_maker)),
                    bound_order_id=row.get("bound_order_id"),
                    hold_start_timestamp_ns=int(row.get("hold_start_timestamp_ns", 0) or 0),
                    liveness_version=DIRECT_LIVENESS_VERSION,
                )
            except Exception:
                pass
        except Exception:
            return

    def _direct_service_partial_fill_recovery(self, response, state) -> tuple[int, int]:
        """Protect the exact legal partial remainder before any generic publisher path."""
        registry = getattr(self, "_direct_partial_recovery", {}) or {}
        if not registry:
            return 0, 0
        eps = float(self._execution_flat_epsilon())
        min_size = max(
            1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        )
        instructions = 0
        holds = 0
        for book_id, row in list(registry.items()):
            net = float(self._direct_signed_inventory(int(book_id)))
            if not direct_is_dust_inventory(net, min_order=min_size, eps=eps):
                self._direct_partial_recovery.pop(int(book_id), None)
                self._direct_partial_hold_releases = int(
                    getattr(self, "_direct_partial_hold_releases", 0) or 0
                ) + 1
                continue

            desired = str(row.get("desired_side") or ("buy" if net > 0.0 else "sell")).lower()
            active = self._direct_partial_hold_active(int(book_id), state)
            bound_id = row.get("bound_order_id") if active else None
            try:
                bound_id = int(bound_id) if bound_id is not None else None
            except (TypeError, ValueError):
                bound_id = None

            # v6.0.3 D1: once the bounded hold is gone, a short lot is not this handler's to own.
            # v6.0.0 exits it like a full lot, and keeping the row here cancels that exit at the
            # next request -- 121 such cancels in the v6.0.2 run, 9 of 19 episodes forced out.
            disposition = self._v603_disposition(
                int(book_id), net, eps=eps, min_order=min_size,
                hold_active=bool(active), bound=bool(active and bound_id is not None),
            )
            if disposition == V603_DISPOSITION_RELEASE:
                self._direct_partial_recovery.pop(int(book_id), None)
                self._v603_note_release(
                    int(book_id), net, mode=str(row.get("mode") or "UNKNOWN"),
                    desired_side=desired, hold_active=bool(active),
                )
                continue

            account_orders = []
            order_rows = []
            order_by_id = {}
            for order in self._direct_account_orders(int(book_id)):
                parsed = self._direct_order_side_price(order)
                if parsed is None:
                    continue
                side, _price = parsed
                oid = getattr(order, "id", None)
                try:
                    oid_int = int(oid) if oid is not None else None
                except (TypeError, ValueError):
                    oid_int = None
                if oid_int is None:
                    continue
                account_orders.append(order)
                order_rows.append((oid_int, side))
                order_by_id[oid_int] = order
            kept_ids, conflicting_ids = (
                direct_partition_bound_remainder_orders(
                    order_rows, bound_order_id=int(bound_id), desired_side=desired,
                )
                if active and bound_id is not None else ([], [oid for oid, _side in order_rows])
            )
            matching = [order_by_id[oid] for oid in kept_ids if oid in order_by_id]

            if conflicting_ids and self._count_book_instructions(response, int(book_id)) < self.max_instructions_per_book:
                try:
                    response.cancel_orders(book_id=int(book_id), order_ids=conflicting_ids, delay=0)
                    instructions += 1
                    self._direct_partial_wrong_side_cancels = int(
                        getattr(self, "_direct_partial_wrong_side_cancels", 0) or 0
                    ) + 1
                    # A1.9.0.3: third cancel path, previously unregistered.
                    try:
                        self._a19_note_exit_cancel(
                            int(book_id), conflicting_ids,
                            ABSENT_PARTIAL_REMAINDER_CANCEL,
                        )
                    except Exception:
                        pass
                    # v6.0.3: the disposition says which branch sent this cancel, so a short-lot
                    # cancel (gate R1) is read from the row rather than inferred from a null id.
                    self._v603_note_cancel(
                        int(book_id), net, eps=eps, min_order=min_size, disposition=disposition,
                    )
                    self._emit(
                        "A173_PARTIAL_REMAINDER_CANCEL", force=True,
                        tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                        mode=str(row.get("mode") or "UNKNOWN"), desired_side=desired,
                        bound_order_id=bound_id, cancelled_orders=len(conflicting_ids),
                        net_base=float(net), v603_disposition=str(disposition),
                    )
                except Exception:
                    pass

            if active and bound_id is not None:
                # Even if the account snapshot lags the trade event, block every
                # replacement path until the bounded hold expires.  This avoids
                # the observed Book111 +0.2002 -> fresh SELL 0.25 over-close.
                holds += 1
                if matching:
                    self._research_partial_fill_hold_quoted = int(
                        getattr(self, "_research_partial_fill_hold_quoted", 0) or 0
                    ) + 1
                    self._direct_partial_hold_live = int(
                        getattr(self, "_direct_partial_hold_live", 0) or 0
                    ) + 1
                    self._direct_partial_bound_holds = int(
                        getattr(self, "_direct_partial_bound_holds", 0) or 0
                    ) + 1
                    remaining = getattr(matching[0], "quantity", None)
                    try:
                        remaining = float(remaining) if remaining is not None else None
                    except (TypeError, ValueError):
                        remaining = None
                    try:
                        self._emit(
                            "A173_PARTIAL_REMAINDER_HOLD", force=True,
                            tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                            mode=str(row.get("mode") or "UNKNOWN"), desired_side=desired,
                            bound_order_id=int(bound_id), live_remainder_orders=1,
                            remaining_quantity=remaining, net_base=float(net),
                            target_inventory=float(row.get("target_inventory", 0.0) or 0.0),
                            new_subminimum_order=0, new_full_clip_order=0,
                        )
                    except Exception:
                        pass
                else:
                    self._direct_partial_bound_pending = int(
                        getattr(self, "_direct_partial_bound_pending", 0) or 0
                    ) + 1
                    try:
                        self._emit(
                            "A1731_PARTIAL_REMAINDER_PENDING", force=True,
                            tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                            mode=str(row.get("mode") or "UNKNOWN"), desired_side=desired,
                            bound_order_id=int(bound_id), net_base=float(net),
                            replacement_blocked=1,
                        )
                    except Exception:
                        pass
        return int(instructions), int(holds)

    def _direct_place_dust_normalizer(self, response, state, book_id: int, net_base: float) -> int:
        """Add one same-sign minimum Maker clip so irreducible dust becomes actionable."""
        if self._direct_partial_hold_active(int(book_id), state):
            self._direct_emit_partial_replacement_block(int(book_id), path="DUST_NORMALIZER")
            return 0
        books = getattr(state, "books", None) or {}
        book = books.get(int(book_id))
        if book is None or not getattr(book, "bids", None) or not getattr(book, "asks", None):
            return 0
        if self._direct_book_has_live_order(int(book_id)):
            return 0
        if self._count_book_instructions(response, int(book_id)) >= self.max_instructions_per_book:
            return 0
        min_size = max(
            1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        )
        vol_dec = int(getattr(getattr(state, "config", None), "volumeDecimals", 8) or 8)
        qty = self._round_order_size(min_size, vol_dec)
        if qty + 1e-12 < min_size:
            return 0
        bid = float(book.bids[0].price)
        ask = float(book.asks[0].price)
        if bid <= 0.0 or ask <= bid:
            return 0
        long_dust = float(net_base) > 0.0
        direction = OrderDirection.BUY if long_dust else OrderDirection.SELL
        price = bid if long_dust else ask
        account = self.accounts.get(int(book_id))
        if account is None:
            return 0
        if long_dust:
            if float(getattr(account.quote_balance, "free", 0.0) or 0.0) + 1e-12 < price * qty:
                return 0
            side_code = 1
        else:
            if float(getattr(account.base_balance, "free", 0.0) or 0.0) + 1e-12 < qty:
                return 0
            side_code = 2
        baseline = maker_expiry_ns_for_regime(getattr(self, "_research_market_regime", "NORMAL"))
        publish = int(getattr(getattr(state, "config", None), "publish_interval", 0) or 0)
        expiry = direct_recovery_expiry_ns(
            baseline_ns=int(baseline), publish_interval_ns=publish,
        )
        response.limit_order(
            book_id=int(book_id), direction=direction, quantity=float(qty), price=float(price),
            clientOrderId=91000 + int(book_id) * 10 + side_code,
            stp=STP.CANCEL_BOTH, postOnly=True, timeInForce=TimeInForce.GTT,
            expiryPeriod=int(expiry), leverage=0.0,
            settlement_option=LoanSettlementOption.NONE, delay=0,
        )
        tick = int(getattr(self, "_tick", 0) or 0)
        self._direct_partial_recovery[int(book_id)] = {
            "mode": "NORMALIZE",
            "target_inventory": float(net_base + (qty if long_dust else -qty)),
            "desired_side": "buy" if long_dust else "sell",
            "preserve_existing_remainder": True,
            "first_tick": tick,
            "last_progress_tick": tick,
            "net_base": float(net_base),
            "maker_origin": 1,
        }
        self._direct_dust_normalize_orders = int(
            getattr(self, "_direct_dust_normalize_orders", 0) or 0
        ) + 1
        try:
            self._emit(
                "A173_DUST_NORMALIZE", force=True, tick=tick, book=int(book_id),
                net_base=float(net_base), normalize_side=("buy" if long_dust else "sell"),
                quantity=float(qty), price=float(price), expiry_ns=int(expiry),
                projected_abs_after_full_fill=abs(float(net_base)) + float(qty),
                liveness_version=DIRECT_LIVENESS_VERSION,
            )
        except Exception:
            pass
        return 1

    def _direct_normalize_irreducible_dust(
        self, response, state, *, effective_abs: float, effective_active: int,
        effective_open: int, force_liveness: bool,
    ) -> int:
        """Normalize at most one old/blocked irreducible dust book per request."""
        parked = getattr(self, "_research_parked_dust", {}) or {}
        if not parked:
            return 0
        tick = int(getattr(self, "_tick", 0) or 0)
        eps = float(self._execution_flat_epsilon())
        min_size = max(
            1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        )
        max_abs = float(getattr(self, "research_max_total_abs_base", 2.0) or 2.0)
        max_active = int(getattr(self, "research_max_active_open_books", 6) or 6)
        max_open = int(getattr(self, "research_max_total_open_books", 8) or 8)
        registry = getattr(self, "_direct_partial_recovery", {}) or {}
        rows = []
        for raw_id, info in parked.items():
            book_id = int(raw_id)
            if self._direct_partial_hold_active(book_id, state):
                continue
            if self._direct_book_has_live_order(book_id):
                continue
            net = float(self._direct_signed_inventory(book_id))
            if not direct_is_dust_inventory(net, min_order=min_size, eps=eps):
                continue
            if abs(net) + 1e-12 >= 0.5 * min_size:
                continue
            first_tick = int((info or {}).get("first_tick", tick) or tick)
            age = max(0, tick - first_tick)
            recovery = registry.get(book_id)
            recovery_age = max(0, tick - int((recovery or {}).get("first_tick", tick) or tick))
            from_partial = recovery is not None
            ready = (
                (from_partial and recovery_age >= DIRECT_DUST_NORMALIZE_MIN_AGE_TICKS)
                or age >= DIRECT_STALE_DUST_NORMALIZE_AGE_TICKS
                or bool(force_liveness)
            )
            if not ready:
                continue
            # A1.7.4.3: the one-clip liveness reserve must supply recovery
            # capacity; normalization itself receives no temporary cap overflow.
            if not direct_normalization_allowed(
                net=net, total_effective_abs=float(effective_abs),
                active_books=int(effective_active), effective_open_books=int(effective_open),
                max_abs=max_abs, max_active=max_active, max_open=max_open,
                min_order=min_size, eps=eps, recovery_overflow_abs=0.0,
            ):
                continue
            rows.append((0 if from_partial else 1, -age, book_id, net))
        if not rows:
            return 0
        rows.sort()
        _source, _neg_age, book_id, net = rows[0]
        if force_liveness:
            self._direct_liveness_triggers = int(
                getattr(self, "_direct_liveness_triggers", 0) or 0
            ) + 1
            self._direct_forced_recovery_books_this_tick.add(int(book_id))
            try:
                self._emit(
                    "A173_LIVENESS_RECOVERY", force=True,
                    tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                    blocked_ticks=int(getattr(self, "_direct_liveness_blocked_ticks", 0) or 0),
                    temporary_recovery_overflow_abs=0.0,
                    strict_cap=1,
                    max_total_abs_base=float(max_abs), net_base=float(net),
                )
            except Exception:
                pass
        return self._direct_place_dust_normalizer(response, state, book_id, net)

    # ------------------------------------------------------------------
    # A1.6.1 liveness repair.
    # ------------------------------------------------------------------
    def _direct_compact_selected_dust(self, response, state) -> int:
        """Execute theorem-safe passive compaction for selected parked dust.

        A1.6.0 inherited the Research dust selector, but its Direct inventory
        loop skipped dust before the inherited compaction placement path could
        run.  A1.6.1 explicitly services only selector-approved dust whose
        quantity is in (0.5 * min_order, min_order).  A full min-order fill can
        cross zero, but the absolute residual cannot increase.
        """
        selected = {
            int(x) for x in (getattr(self, "_research_dust_compact_ids_this_tick", None) or set())
        }
        if not selected:
            return 0
        books = getattr(state, "books", None) or {}
        min_size = max(
            0.0, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        )
        if min_size <= 0.0:
            return 0

        placed = 0
        for book_id in sorted(selected):
            if self._direct_partial_hold_active(book_id, state):
                self._direct_emit_partial_replacement_block(book_id, path="DUST_COMPACTOR")
                continue
            # Dust compaction owns the book only after every older order has
            # disappeared from the account snapshot.
            if self._direct_book_has_live_order(book_id):
                continue
            book = books.get(book_id)
            if book is None or not getattr(book, "bids", None) or not getattr(book, "asks", None):
                continue
            mid = 0.5 * (float(book.bids[0].price) + float(book.asks[0].price))
            inventory = self._net_inventory(book_id, mid)
            net_base = float(getattr(inventory, "net_base", 0.0) or 0.0)
            self._refresh_dust_state(book_id, net_base, emit=True)
            if not self._is_dust_qty(net_base):
                continue
            if not self._dust_compaction_safe_for_any_fill(net_base):
                continue

            # A1.7.4.2: theorem-safe exposure reduction is necessary but no
            # longer sufficient.  A min-size fill crosses moderate dust through
            # zero, so bound the realized loss at the exact passive touch.
            maker_close_price = (
                float(book.asks[0].price) if net_base > 0.0 else float(book.bids[0].price)
            )
            best_seen = getattr(self, "_direct_a175_dust_best_realization", None)
            if best_seen is None:
                best_seen = {}
                self._direct_a175_dust_best_realization = best_seen
            kappa_decision = decide_kappa_safe_dust_compaction(
                net_base=net_base, min_order=min_size,
                vwap_entry=getattr(inventory, "vwap_entry", None),
                maker_close_price=maker_close_price,
                age_ticks=int(getattr(inventory, "position_ticks", 0) or 0),
                loss_floor_bps=DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS,
                best_realization_bps=best_seen.get(int(book_id)),
                eps=float(self._execution_flat_epsilon()),
            )
            # A1.7.5: remember the best realization ever reachable for this
            # residual.  Book 80 was refused at -60.18 bps and then decayed to
            # -320 bps; without this the reachable value is not recorded.
            if kappa_decision.best_realization_bps is not None:
                best_seen[int(book_id)] = float(kappa_decision.best_realization_bps)
            if not kappa_decision.allow:
                self._direct_dust_kappa_blocks = int(
                    getattr(self, "_direct_dust_kappa_blocks", 0) or 0
                ) + 1
                try:
                    self._emit(
                        "A1742_DUST_KAPPA_BLOCK", force=True,
                        tick=getattr(self, "_tick", None), book_id=int(book_id),
                        **kappa_decision.as_log(),
                    )
                except Exception:
                    pass
                # v5.0.2 F3: a refusal costs the book its turn, as a failed attempt does.
                self._v502_note_compaction_refusal(int(book_id))
                continue
            self._direct_dust_kappa_allows = int(
                getattr(self, "_direct_dust_kappa_allows", 0) or 0
            ) + 1
            self._v502_note_compaction_allowed(int(book_id))
            if kappa_decision.reason == REASON_AGE_ESCALATED:
                self._direct_a175_dust_escalated_allows = int(
                    getattr(self, "_direct_a175_dust_escalated_allows", 0) or 0
                ) + 1
            try:
                self._emit(
                    "A1742_DUST_KAPPA_ALLOW", force=True,
                    tick=getattr(self, "_tick", None), book_id=int(book_id),
                    **kappa_decision.as_log(),
                )
            except Exception:
                pass

            # v6.1: the clip FIFO-closes the head lot, so refuse it below the fee-inclusive
            # break-even.  The kappa gate above is a -60 bps loss budget; this one is zero.
            if self._v61_on() and not self._v61_compaction_price_ok(
                int(book_id), net_base, maker_close_price, state,
            ):
                self._v502_note_compaction_refusal(int(book_id))
                continue
            self._research_dust_compact_attempts = int(
                getattr(self, "_research_dust_compact_attempts", 0) or 0
            ) + 1
            self._research_note_dust_compact(book_id, success=False)
            before_ix = len(getattr(response, "instructions", None) or [])
            n = super()._place_passive_inventory_exit(
                response, state, book_id, book, inventory, min_size,
            )
            if not n:
                try:
                    self._emit(
                        "POSITION_GUARD", force=True, tick=getattr(self, "_tick", None),
                        book_id=book_id, reason="DIRECT_DUST_COMPACT_BLOCKED",
                        net_base=net_base, min_order_size=min_size,
                        exposure_nonincreasing=True,
                    )
                except Exception:
                    pass
                continue

            self._research_dust_compact_orders = int(
                getattr(self, "_research_dust_compact_orders", 0) or 0
            ) + 1
            self._research_dust_compact_active[book_id] = int(getattr(self, "_tick", 0) or 0)
            if bool(getattr(self, "research_dust_compact_adaptive", False)):
                self._record_dust_compaction_attempt(book_id)
            self._inventory_reason[book_id] = "DIRECT_DUST_COMPACT"
            placed += int(n)
            try:
                self._emit(
                    "POSITION_GUARD", force=True, tick=getattr(self, "_tick", None),
                    book_id=book_id, reason="DIRECT_DUST_COMPACT",
                    net_base=net_base, min_order_size=min_size,
                    projected_full_fill_net=(
                        net_base - (min_size if net_base > 0.0 else -min_size)
                    ),
                    exposure_nonincreasing=True,
                    dust_kappa_guard=1,
                    maker_realization_bps=kappa_decision.maker_realization_bps,
                    projected_realized_quote_pnl=kappa_decision.projected_realized_quote_pnl,
                    dust_kappa_loss_floor_bps=kappa_decision.loss_floor_bps,
                    instructions=len(getattr(response, "instructions", None) or []) - before_ix,
                )
            except Exception:
                pass
        return placed

    # ------------------------------------------------------------------
    # A1.6.3 exposure-liveness helpers.
    # ------------------------------------------------------------------
    def _direct_signed_inventory(self, book_id: int) -> float:
        try:
            return float(self._position_tracker_snapshot(int(book_id)).net_qty)
        except Exception:
            try:
                return float(self._research_signed_inventory(int(book_id)))
            except Exception:
                qty = float(self._research_abs_inventory(int(book_id)) or 0.0)
                return qty

    def _direct_account_orders(self, book_id: int) -> list:
        account = (getattr(self, "accounts", {}) or {}).get(int(book_id))
        return list(getattr(account, "orders", None) or []) if account is not None else []

    def _direct_pending_ledger(self) -> dict[tuple[int, str, str], PendingExposureOrder]:
        ledger = getattr(self, "_direct_pending_exposure_orders", None)
        if not isinstance(ledger, dict):
            ledger = {}
            self._direct_pending_exposure_orders = ledger
        return ledger

    def _direct_order_client_id(self, order):
        for name in ("clientOrderId", "client_order_id", "clientId", "client_id"):
            value = getattr(order, name, None)
            if value is not None:
                return value
        return None

    def _direct_order_remaining_qty(self, order) -> float:
        for name in ("remainingQuantity", "remaining_quantity", "quantity", "qty", "size"):
            try:
                value = float(getattr(order, name, 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if value > 0.0:
                return value
        return 0.0

    def _direct_emit_book_ownership_block(self, *, book_id: int, side: str, reason: str, quantity: float = 0.0) -> None:
        self._direct_book_ownership_blocks = int(
            getattr(self, "_direct_book_ownership_blocks", 0) or 0
        ) + 1
        try:
            self._emit(
                "A17431_BOOK_OWNERSHIP_BLOCK", force=True,
                tick=int(getattr(self, "_tick", 0) or 0),
                book=int(book_id), side=canonical_order_side(side),
                reason=str(reason), quantity=float(quantity or 0.0),
                pending_orders=len(self._direct_pending_ledger()),
                ownership_version=DIRECT_BOOK_OWNERSHIP_VERSION,
            )
        except Exception:
            pass

    def _direct_emit_book_ownership_release(self, *, row: PendingExposureOrder, reason: str) -> None:
        self._direct_book_ownership_releases = int(
            getattr(self, "_direct_book_ownership_releases", 0) or 0
        ) + 1
        try:
            self._emit(
                "A17431_BOOK_OWNERSHIP_RELEASE", force=True,
                tick=int(getattr(self, "_tick", 0) or 0),
                book=int(row.book_id), side=canonical_order_side(row.side),
                client_order_id=row.client_order_id, reason=str(reason),
                remaining_quantity=float(row.quantity or 0.0),
                ownership_version=DIRECT_BOOK_OWNERSHIP_VERSION,
            )
        except Exception:
            pass

    def _direct_exchange_identity_registry(self) -> dict[int, ExchangeOrderIdentity]:
        registry = getattr(self, "_direct_exchange_order_ownership", None)
        if not isinstance(registry, dict):
            registry = {}
            self._direct_exchange_order_ownership = registry
        return registry

    def _direct_notice_client_id(self, notice):
        for name in ("clientOrderId", "client_order_id", "clientId", "client_id"):
            value = getattr(notice, name, None)
            if value is not None:
                return value
        return None

    def _direct_emit_identity_diag(self, event_type: str, **fields) -> None:
        try:
            self._emit(
                str(event_type), force=True,
                tick=int(getattr(self, "_tick", 0) or 0),
                ownership_version=DIRECT_BOOK_OWNERSHIP_VERSION,
                **fields,
            )
        except Exception:
            pass

    def _direct_register_exchange_identity(
        self, *, exchange_order_id, book_id, client_order_id, side, remaining_quantity: float = 0.0,
    ) -> ExchangeOrderIdentity | None:
        return register_exchange_identity(
            self._direct_exchange_identity_registry(),
            exchange_order_id=exchange_order_id, book_id=book_id,
            client_order_id=client_order_id, side=side,
            remaining_quantity=remaining_quantity,
        )

    def _direct_release_pending_exact(
        self, *, book_id: int, client_order_id, side: str | None, reason: str, exchange_order_id=None,
    ) -> bool:
        ledger = self._direct_pending_ledger()
        bid = int(book_id)
        cid = str(client_order_id)
        side_token = canonical_order_side(side) if side else ""
        matches = [
            key for key in ledger
            if int(key[0]) == bid and str(key[1]) == cid
            and (not side_token or canonical_order_side(key[2]) == side_token)
        ]
        if len(matches) != 1:
            if len(matches) > 1:
                self._direct_release_mismatch_blocks = int(getattr(self, "_direct_release_mismatch_blocks", 0) or 0) + 1
                self._direct_emit_identity_diag(
                    "A17432_RELEASE_MISMATCH_BLOCK", book=bid, client_order_id=cid,
                    exchange_order_id=exchange_order_id, side=side_token, reason="AMBIGUOUS_PENDING_IDENTITY",
                    matches=len(matches),
                )
            return False
        key = matches[0]
        row = ledger.pop(key, None)
        if row is None:
            return False
        self._direct_emit_book_ownership_release(row=row, reason=str(reason))
        self._direct_identity_releases = int(getattr(self, "_direct_identity_releases", 0) or 0) + 1
        self._direct_emit_identity_diag(
            "A17432_IDENTITY_RELEASE", book=bid, client_order_id=cid,
            exchange_order_id=exchange_order_id, side=canonical_order_side(row.side), reason=str(reason),
            remaining_quantity=float(row.quantity or 0.0),
        )
        return True

    def _direct_note_placement_identity_notice(self, notice, *, phase: str) -> None:
        bid = getattr(notice, "bookId", None)
        cid = self._direct_notice_client_id(notice)
        oid = getattr(notice, "orderId", None)
        side = canonical_order_side(getattr(notice, "side", ""))
        success = bool(getattr(notice, "success", False))
        qty = getattr(notice, "quantity", 0.0)
        if success:
            self._direct_register_exchange_identity(
                exchange_order_id=oid, book_id=bid, client_order_id=cid,
                side=side, remaining_quantity=qty,
            )
            return
        # Placement failure has an exact client id and is safe to release.
        if bid is not None and cid is not None:
            self._direct_release_pending_exact(
                book_id=int(bid), client_order_id=cid, side=side or None,
                reason=f"NOTICE_{phase}", exchange_order_id=oid,
            )

    def _a191_release_reprice_ownership(self, *, exchange_order_id, notice_book_id, success) -> bool:
        """Release the reservation held by an exit A1.9 itself cancelled.

        The A1.7.4.3.2 identity gate refuses to release on a cancellation whose
        exchange order id it never registered (`UNKNOWN_EXCHANGE_ORDER_ID`), and
        Maker exits are not in that registry -- the placement notice does not
        carry a client order id to key them by.  So before A1.9.1.2 an explicit
        reprice cancel was acknowledged by the exchange at T+1 and then sat on a
        live local reservation until LOCAL_EXPIRY, leaving the book unquoted for
        a measured median of 4 ticks.  That removed queue liquidity early and
        bought no faster repricing, which is the opposite of the intent.

        This narrows rather than weakens the rule.  Release requires ALL of:
        an order A1.9 explicitly cancelled itself, an exact exchange-order-id
        match, a successful cancellation, a matching book, and an unambiguous
        pending row.  A stale or unrelated cancellation still cannot release
        anything, and ambiguity is refused exactly as `_direct_release_pending_exact`
        refuses it.
        """
        pending = getattr(self, "_a191_reprice_release", None) or {}
        try:
            oid = int(exchange_order_id)
        except (TypeError, ValueError):
            return False
        row = pending.get(oid)
        if row is None:
            return False
        if not bool(success):
            # A failed cancellation is not proof the order is gone.
            return False
        try:
            if notice_book_id is not None and int(notice_book_id) != int(row["book_id"]):
                self._a191_ownership_release_blocked += 1
                self._direct_emit_identity_diag(
                    "A1912_REPRICE_RELEASE_BLOCKED", book=notice_book_id,
                    exchange_order_id=oid, mapped_book=int(row["book_id"]),
                    reason="CANCEL_BOOK_ID_MISMATCH",
                )
                return False
        except (TypeError, ValueError):
            return False

        pending.pop(oid, None)
        bid = int(row["book_id"])
        side = str(row["side"])
        # Notice-ingest clock, not `self._tick`: see `_log_notices`.
        tick = int(getattr(self, "_a19_notice_tick", 0) or 0) or int(getattr(self, "_tick", 0) or 0)
        ack_ticks = max(0, tick - int(row.get("tick", tick)))
        self._a191_exchange_acks += 1
        self._a191_exchange_ack_ticks_total += ack_ticks

        cid = row.get("client_id")
        released = False
        if cid is not None:
            released = self._direct_release_pending_exact(
                book_id=bid, client_order_id=cid, side=side,
                reason="A1912_REPRICE_CANCEL_EXACT", exchange_order_id=oid,
            )
        if not released:
            # No client id on the notice, so fall back to the reservation for
            # this exact book and side -- and only when there is exactly one.
            # One match is identification, not a guess; more than one is refused.
            ledger = self._direct_pending_ledger()
            matches = [
                key for key in ledger
                if int(key[0]) == bid and canonical_order_side(key[2]) == side
            ]
            if len(matches) != 1:
                self._a191_ownership_release_blocked += 1
                self._direct_emit_identity_diag(
                    "A1912_REPRICE_RELEASE_BLOCKED", book=bid, exchange_order_id=oid,
                    side=side, matches=len(matches),
                    reason="NO_UNIQUE_PENDING_RESERVATION",
                )
                return False
            released = self._direct_release_pending_exact(
                book_id=bid, client_order_id=matches[0][1], side=side,
                reason="A1912_REPRICE_CANCEL_BOOK_SIDE_EXACT", exchange_order_id=oid,
            )
        if not released:
            self._a191_ownership_release_blocked += 1
            return False

        self._a191_ownership_releases += 1
        self._a191_ownership_release_ticks_total += ack_ticks
        # Claim the ack so the ledger-settle path does not report the same
        # cancellation a second time one tick later.
        try:
            self._a19_identity_acked.add(oid)
        except AttributeError:
            self._a19_identity_acked = {oid}
        self._emit(
            "A19_CANCEL_ACK", force=True, tick=tick, book=bid,
            order_id=oid, cancel_reason=ABSENT_REPRICE_CANCEL,
            # These were conflated before: ack_ticks reported the internal watch
            # settling, not the exchange, and read 4 ticks where the exchange
            # was answering at T+1 on 42 of 42 cancels.
            exchange_ack_ticks=ack_ticks,
            ownership_release_ticks=ack_ticks,
            release_path="A1912_IDENTITY",
        )
        return True

    def _direct_note_cancellation_identity_notice(self, notice, *, phase: str) -> None:
        registry = self._direct_exchange_identity_registry()
        notice_book = getattr(notice, "bookId", None)
        cancellations = list(getattr(notice, "cancellations", None) or [])
        for cancellation in cancellations:
            oid = getattr(cancellation, "orderId", None)
            try:
                oid_int = int(oid)
            except (TypeError, ValueError):
                oid_int = None
            decision, identity = cancellation_identity_decision(
                registry, exchange_order_id=oid_int, notice_book_id=notice_book,
                success=bool(getattr(cancellation, "success", False)),
            )
            if decision == "STALE_UNKNOWN" and self._a191_release_reprice_ownership(
                exchange_order_id=oid_int, notice_book_id=notice_book,
                success=bool(getattr(cancellation, "success", False)),
            ):
                # An exit A1.9 cancelled itself, confirmed by the exchange and
                # matched by exact id: released above, not a stale cancel.
                continue
            if decision == "STALE_UNKNOWN":
                self._direct_stale_cancels_ignored = int(getattr(self, "_direct_stale_cancels_ignored", 0) or 0) + 1
                self._direct_emit_identity_diag(
                    "A17432_STALE_CANCEL_IGNORED", book=notice_book, exchange_order_id=oid,
                    reason="UNKNOWN_EXCHANGE_ORDER_ID", success=int(bool(getattr(cancellation, "success", False))),
                )
                continue
            if decision == "BOOK_MISMATCH":
                self._direct_release_mismatch_blocks = int(getattr(self, "_direct_release_mismatch_blocks", 0) or 0) + 1
                self._direct_emit_identity_diag(
                    "A17432_RELEASE_MISMATCH_BLOCK", book=notice_book, exchange_order_id=oid_int,
                    mapped_book=int(identity.book_id), client_order_id=identity.client_order_id,
                    side=identity.side, reason="CANCEL_BOOK_ID_MISMATCH",
                )
                continue
            if decision == "FAILED_KEEP":
                # A failed cancellation (e.g. old order no longer exists) is not
                # proof that the *current* local owner is terminal. Keep ownership
                # until exact fill/ack/local expiry and never guess by book/side.
                self._direct_stale_cancels_ignored = int(getattr(self, "_direct_stale_cancels_ignored", 0) or 0) + 1
                self._direct_emit_identity_diag(
                    "A17432_STALE_CANCEL_IGNORED", book=int(identity.book_id),
                    exchange_order_id=oid_int, client_order_id=identity.client_order_id,
                    side=identity.side, reason="CANCEL_FAILED_NOT_RELEASE_AUTHORITY", success=0,
                )
                continue
            self._direct_release_pending_exact(
                book_id=int(identity.book_id), client_order_id=identity.client_order_id,
                side=identity.side, reason=f"NOTICE_{phase}_EXACT", exchange_order_id=oid_int,
            )
            registry.pop(oid_int, None)
            self._direct_identity_releases = int(getattr(self, "_direct_identity_releases", 0) or 0) + 1
            self._direct_emit_identity_diag(
                "A17432_IDENTITY_RELEASE", book=int(identity.book_id),
                exchange_order_id=oid_int, client_order_id=identity.client_order_id,
                side=identity.side, reason="EXCHANGE_CANCELLATION_EXACT", remaining_quantity=0.0,
            )

    def _direct_reconcile_pending_exposure(self, state) -> None:
        """Hand local bridge reservations to the authoritative account snapshot.

        A submitted placement is locally reserved immediately. Once the same
        client order appears in ``account.orders``, the account-side A1.6.3
        reservation becomes authoritative and the local copy is removed.
        """
        ledger = self._direct_pending_ledger()
        if not ledger:
            return
        tick = int(getattr(self, "_tick", 0) or 0)
        now_ts = int(getattr(state, "timestamp", 0) or 0)
        visible: set[tuple[int, str, str]] = set()
        books = getattr(state, "books", None) or {}
        for raw_id in books.keys():
            bid = int(raw_id)
            for order in self._direct_account_orders(bid):
                cid = self._direct_order_client_id(order)
                if cid is None:
                    continue
                try:
                    side = "buy" if int(getattr(order, "side", -1)) == 0 else "sell"
                except (TypeError, ValueError):
                    side = ""
                visible.add((bid, str(cid), side))
                self._direct_register_exchange_identity(
                    exchange_order_id=getattr(order, "id", None), book_id=bid,
                    client_order_id=cid, side=side,
                    remaining_quantity=self._direct_order_remaining_qty(order),
                )
        for key, row in list(ledger.items()):
            if key in visible:
                ledger.pop(key, None)
                self._direct_emit_book_ownership_release(row=row, reason="ACKNOWLEDGED")
                self._direct_pending_exposure_acked = int(
                    getattr(self, "_direct_pending_exposure_acked", 0) or 0
                ) + 1
                continue
            if not pending_order_live(
                row, current_tick=tick, current_timestamp_ns=now_ts,
            ):
                ledger.pop(key, None)
                self._direct_emit_book_ownership_release(row=row, reason="LOCAL_EXPIRY")
                self._direct_pending_exposure_expired = int(
                    getattr(self, "_direct_pending_exposure_expired", 0) or 0
                ) + 1

    def _direct_record_pending_placements(self, response, state) -> int:
        """Reserve final emitted placements before the validator can acknowledge them."""
        ledger = self._direct_pending_ledger()
        tick = int(getattr(self, "_tick", 0) or 0)
        now_ts = int(getattr(state, "timestamp", 0) or 0)
        recorded = 0
        for instruction in list(getattr(response, "instructions", None) or []):
            kind = str(getattr(instruction, "type", "") or "").upper()
            if kind not in {"PLACE_ORDER_LIMIT", "PLACE_ORDER_MARKET"}:
                continue
            raw_book = self._get(instruction, "bookId", "book_id")
            try:
                book_id = int(raw_book)
                qty = max(0.0, float(self._get(instruction, "quantity", "qty", "size") or 0.0))
            except (TypeError, ValueError):
                continue
            if qty <= 0.0:
                continue
            side = str(self._research_instruction_side(instruction) or "").lower()
            cid = self._get(instruction, "clientOrderId", "client_order_id")
            expiry = self._get(instruction, "expiryPeriod", "expiry_period")
            try:
                expiry_ns = max(0, int(expiry or 0))
            except (TypeError, ValueError):
                expiry_ns = 0
            row = PendingExposureOrder(
                book_id=book_id, side=canonical_order_side(side), quantity=qty, client_order_id=cid,
                submitted_tick=tick, submitted_timestamp_ns=now_ts,
                expiry_period_ns=expiry_ns, order_kind=kind,
            )
            reserved_row, merged = reserve_pending_order(ledger, row)
            recorded += 1
            self._direct_book_ownership_reserves = int(
                getattr(self, "_direct_book_ownership_reserves", 0) or 0
            ) + 1
            try:
                self._emit(
                    "A17431_BOOK_OWNERSHIP_RESERVE", force=True, tick=tick,
                    book=int(book_id), side=canonical_order_side(side),
                    client_order_id=cid, quantity=float(qty),
                    reserved_quantity=float(reserved_row.quantity),
                    merged_duplicate_key=int(bool(merged)),
                    ownership_version=DIRECT_BOOK_OWNERSHIP_VERSION,
                )
            except Exception:
                pass
        if recorded:
            self._direct_pending_exposure_recorded = int(
                getattr(self, "_direct_pending_exposure_recorded", 0) or 0
            ) + recorded
            try:
                self._emit(
                    "A1743_INFLIGHT_RESERVE", force=True, tick=tick,
                    placements=int(recorded), pending_orders=len(ledger),
                    inflight_version=DIRECT_INFLIGHT_RESERVATION_VERSION,
                )
            except Exception:
                pass
        return int(recorded)

    def _direct_pending_note_fill(self, event) -> None:
        """Reduce local ownership only for the exact own exchange order when known."""
        ledger = self._direct_pending_ledger()
        registry = self._direct_exchange_identity_registry()
        book = getattr(event, "bookId", None)
        qty = getattr(event, "quantity", None)
        if book is None or qty is None:
            return
        try:
            bid = int(book)
            q = max(0.0, float(qty or 0.0))
        except (TypeError, ValueError):
            return
        if q <= 0.0:
            return
        own_oid = None
        try:
            if int(getattr(event, "makerAgentId", -1)) == int(self.uid):
                own_oid = int(getattr(event, "makerOrderId", 0) or 0)
            elif int(getattr(event, "takerAgentId", -1)) == int(self.uid):
                own_oid = int(getattr(event, "takerOrderId", 0) or 0)
        except (TypeError, ValueError):
            own_oid = None
        identity = registry.get(own_oid) if own_oid else None
        matches = []
        if identity is not None:
            key = identity.pending_key()
            if key in ledger:
                matches = [key]
            identity_remaining = reduce_identity_quantity(
                identity, q, eps=float(self._execution_flat_epsilon())
            )
            if identity_remaining <= 0.0:
                registry.pop(int(identity.exchange_order_id), None)
        else:
            # A fill can race the placement acknowledgement. Fall back only to
            # one unambiguous exact client id; never match by book alone.
            cid = getattr(event, "clientOrderId", None)
            if cid is None:
                return
            matches = [k for k in ledger if int(k[0]) == bid and str(k[1]) == str(cid)]
            if len(matches) != 1:
                if len(matches) > 1:
                    self._direct_release_mismatch_blocks = int(getattr(self, "_direct_release_mismatch_blocks", 0) or 0) + 1
                    self._direct_emit_identity_diag(
                        "A17432_RELEASE_MISMATCH_BLOCK", book=bid, client_order_id=str(cid),
                        exchange_order_id=own_oid, reason="AMBIGUOUS_FILL_CLIENT_ID", matches=len(matches),
                    )
                return
        for key in matches:
            row = ledger.get(key)
            if row is None:
                continue
            remaining = reduce_pending_quantity(row, q, eps=float(self._execution_flat_epsilon()))
            self._direct_pending_exposure_fill_reductions = int(
                getattr(self, "_direct_pending_exposure_fill_reductions", 0) or 0
            ) + 1
            if remaining <= 0.0:
                released = ledger.pop(key, None)
                if released is not None:
                    self._direct_emit_book_ownership_release(row=released, reason="FILL_COMPLETE_EXACT")
                    self._direct_identity_releases = int(getattr(self, "_direct_identity_releases", 0) or 0) + 1
                    self._direct_emit_identity_diag(
                        "A17432_IDENTITY_RELEASE", book=int(released.book_id),
                        client_order_id=released.client_order_id, exchange_order_id=own_oid,
                        side=canonical_order_side(released.side), reason="FILL_COMPLETE_EXACT",
                        remaining_quantity=0.0,
                    )

    def _direct_book_has_live_order(self, book_id: int) -> bool:
        """True for acknowledged or locally pending placement ownership."""
        bid = int(book_id)
        if self._direct_account_orders(bid):
            return True
        return any(int(key[0]) == bid for key in self._direct_pending_ledger())

    def _direct_outstanding_exposure_reservation(self, state) -> tuple[float, int]:
        """Reserve worst-case BASE/open-book capacity for live + pending orders."""
        self._direct_reconcile_pending_exposure(state)
        eps = float(self._execution_flat_epsilon())
        min_size = max(
            0.0, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        )
        reserved_abs = 0.0
        reserved_open = 0
        books = getattr(state, "books", None) or {}
        ledger = self._direct_pending_ledger()
        for raw_id in books.keys():
            bid = int(raw_id)
            net = self._direct_signed_inventory(bid)
            buy = 0.0
            sell = 0.0
            for order in self._direct_account_orders(bid):
                try:
                    q = max(0.0, float(self._direct_order_remaining_qty(order)))
                    if int(getattr(order, "side", -1)) == 0:
                        buy += q
                    else:
                        sell += q
                except (TypeError, ValueError):
                    continue
            for key, row in ledger.items():
                if int(key[0]) != bid:
                    continue
                if str(row.side).lower() == "buy":
                    buy += max(0.0, float(row.quantity))
                else:
                    sell += max(0.0, float(row.quantity))
            if buy <= 0.0 and sell <= 0.0:
                continue
            reserve_abs, reserve_open = outstanding_reservation(
                net, buy, sell, min_order=min_size, eps=eps,
            )
            reserved_abs += reserve_abs
            reserved_open += reserve_open
        return float(reserved_abs), int(reserved_open)

    def _direct_instruction_signed_qty(self, instruction, qty: float) -> float:
        side = str(self._research_instruction_side(instruction) or "").lower()
        return float(qty) if side in {"buy", "bid", "b", "0"} else -float(qty)

    def _research_final_validate_instructions(self, response, state) -> None:
        """A1.6.3 final placement validator with directional exposure semantics.

        Key invariants:
        * existing open orders reserve their worst-case exposure before admission;
        * a book with an unresolved prior order cannot receive another order batch;
        * risk-reducing orders are allowed even when the portfolio is already over cap;
        * dust is exact BASE risk but does not consume a productive open-book slot;
        * non-placement instructions pass through untouched.
        """
        original = list(getattr(response, "instructions", None) or [])
        if not original:
            return

        def _kind(instruction) -> str:
            value = getattr(instruction, "type", None)
            if value is None and isinstance(instruction, dict):
                value = instruction.get("type")
            return str(value or "").upper()

        placement_types = {"PLACE_ORDER_LIMIT", "PLACE_ORDER_MARKET"}
        placements = [item for item in original if _kind(item) in placement_types]
        if not placements:
            return

        books = getattr(state, "books", None) or {}
        try:
            price_dec = int(getattr(getattr(state, "config", None), "priceDecimals", 2) or 2)
        except (TypeError, ValueError):
            price_dec = 2
        try:
            vol_dec = int(getattr(getattr(state, "config", None), "volumeDecimals", 8) or 8)
        except (TypeError, ValueError):
            vol_dec = 8
        tick_size = 10.0 ** (-max(0, price_dec))
        min_size = max(
            0.0, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        )
        safety = int(getattr(self, "research_post_only_safety_ticks", 2) or 2)
        eps = float(self._execution_flat_epsilon())
        max_abs = float(getattr(self, "research_max_total_abs_base", 2.0) or 2.0)
        max_open = int(getattr(self, "research_max_total_open_books", 8) or 8)
        max_active = int(getattr(self, "research_max_active_open_books", 6) or 6)

        shadow_net = {int(bid): self._direct_signed_inventory(int(bid)) for bid in books.keys()}
        filled_abs = sum(abs(float(net)) for net in shadow_net.values())
        if self._v600_on():
            # v6.0.0: the same predicate admission uses; a short lot is an active book.
            filled_active = sum(
                1 for bid, net in shadow_net.items()
                if abs(float(net)) > eps
                and not self._v600_counts_as_dust(bid, abs(float(net)), eps=eps, min_order=min_size)
            )
        else:
            filled_active = sum(1 for net in shadow_net.values() if abs(float(net)) + eps >= min_size)
        # A1.9.5 F3, gate B.  `filled_active` already excludes dust from the
        # BOOK count; `filled_abs` still charges it to the BASE budget, so this
        # validator becomes the next binding gate as soon as admission is
        # relaxed.  EXPOSURE_HEADROOM fired 0 times across the whole A1.9.4 run
        # only because admission never let anything reach here -- that silence
        # was not headroom, and relaxing one gate without the other would move
        # the block rather than remove it.
        if self._v600_on():
            filled_dust_abs = sum(
                abs(float(net)) for bid, net in shadow_net.items()
                if abs(float(net)) > eps
                and self._v600_counts_as_dust(bid, abs(float(net)), eps=eps, min_order=min_size)
            )
        else:
            filled_dust_abs = sum(
                abs(float(net)) for net in shadow_net.values()
                if eps < abs(float(net)) + 1e-12 < min_size
            )
        # A1.9.6 F9: charge the legacy ledger here exactly as admission does.
        a196_ledger_abs_now = self._a196_ledger_abs()
        filled_abs += a196_ledger_abs_now
        filled_dust_abs += a196_ledger_abs_now
        a1961_pending_abs_now = self._a1961_pending_abs()
        filled_abs += a1961_pending_abs_now
        filled_abs = max(0.0, filled_abs - self._a195_dust_exempt_abs(
            total_abs=filled_abs, dust_abs=filled_dust_abs,
            min_order=min_size, max_abs=max_abs,
        ))
        # A1.9.6 F10: the same inherited-parked exemption admission applied.
        filled_abs = max(0.0, filled_abs - self._a196_inherited_parked_exempt(max_abs=max_abs))
        reserved_abs, reserved_open = self._direct_outstanding_exposure_reservation(state)
        shadow_abs = filled_abs + reserved_abs
        shadow_open = filled_active + reserved_open
        shadow_active = filled_active + reserved_open
        preexisting_order_books = {
            int(bid) for bid in books.keys() if self._direct_book_has_live_order(int(bid))
        }
        same_request_buy: dict[int, float] = {}
        same_request_sell: dict[int, float] = {}
        # A1.7.4.3.1 closes the same-response gap that exists before final
        # placements are copied into the A1.7.4.3 pending ledger. One BUY and
        # one SELL may coexist as a two-sided Maker batch, but a second order on
        # the same book/side is never allowed in the same response.
        same_request_owned_sides: set[tuple[int, str]] = set()
        same_request_worst_abs: dict[int, float] = {
            bid: abs(float(net)) for bid, net in shadow_net.items()
        }

        validated_ids: set[int] = set()
        for instruction in placements:
            raw_book = self._get(instruction, "bookId", "book_id")
            try:
                book_id = int(raw_book)
            except (TypeError, ValueError):
                continue
            side = self._research_instruction_side(instruction)
            qty = self._get(instruction, "quantity", "qty", "size")
            try:
                qty_f = round_volume(float(qty), vol_dec)
            except (TypeError, ValueError):
                self._research_log_final_contract_reject(
                    book_id, side, None, None, None, "INVALID_QTY", False,
                )
                continue
            if qty_f + 1e-12 < min_size:
                self._research_log_final_contract_reject(
                    book_id, side, None, None, None, "MIN_QUANTITY", False,
                )
                continue
            if abs(qty_f - float(qty)) > 1e-12:
                if not self._research_set_instruction_attr(instruction, "quantity", qty_f):
                    self._research_log_final_contract_reject(
                        book_id, side, None, None, None, "QTY_PRECISION", False,
                    )
                    continue

            # Cancellation acknowledgement must be visible in a later state before
            # a new placement can own this book.  This prevents stale entry/exit/
            # compaction orders from racing a newer authority.
            if book_id in preexisting_order_books:
                self._direct_emit_book_ownership_block(
                    book_id=book_id, side=str(side), reason="PREEXISTING_BOOK_ORDER", quantity=qty_f,
                )
                self._research_log_final_contract_reject(
                    book_id, side, None, None, None, "INFLIGHT_BOOK_ORDER", False,
                )
                continue

            side_token = canonical_order_side(side)
            owner_key = ownership_key(book_id, side_token)
            if owner_key in same_request_owned_sides:
                self._direct_emit_book_ownership_block(
                    book_id=book_id, side=side_token, reason="SAME_REQUEST_BOOK_SIDE_OWNED", quantity=qty_f,
                )
                self._research_log_final_contract_reject(
                    book_id, side, None, None, None, "SAME_REQUEST_BOOK_SIDE_OWNED", False,
                )
                continue

            book = resolve_book_from_state_mapping(books, book_id)
            bids = getattr(book, "bids", None) if book is not None else None
            asks = getattr(book, "asks", None) if book is not None else None
            best_bid = best_ask = None
            if bids and asks:
                try:
                    best_bid = float(bids[0].price)
                    best_ask = float(asks[0].price)
                except (TypeError, ValueError, IndexError, AttributeError):
                    best_bid = best_ask = None
            is_maker = self._research_instruction_is_maker(instruction)
            old_price = self._get(instruction, "price", "limitPrice", "limit_price")
            try:
                old_price_f = float(old_price) if old_price is not None else None
            except (TypeError, ValueError):
                old_price_f = None
            if is_maker:
                if best_bid is None or best_ask is None:
                    self._research_log_final_contract_reject(
                        book_id, side, old_price_f, best_bid, best_ask, "NO_L1", False,
                    )
                    continue
                new_price = sanitize_post_only_limit_price(
                    side=side,
                    original_price=float(old_price_f or 0.0),
                    best_bid=best_bid,
                    best_ask=best_ask,
                    tick_size=tick_size,
                    safety_ticks=safety,
                    price_decimals=price_dec,
                )
                if new_price is None:
                    self._research_log_final_contract_reject(
                        book_id, side, old_price_f, best_bid, best_ask, "POST_ONLY_CROSS", True,
                    )
                    continue
                if abs(new_price - float(old_price_f or 0.0)) > max(1e-12, tick_size * 0.25):
                    if not self._research_set_instruction_attr(instruction, "price", new_price):
                        self._research_log_final_contract_reject(
                            book_id, side, old_price_f, best_bid, best_ask, "REPRICE_FAILED", False,
                        )
                        continue

            current_net = float(shadow_net.get(book_id, 0.0) or 0.0)
            token = canonical_order_side(side)
            buy_before = float(same_request_buy.get(book_id, 0.0) or 0.0)
            sell_before = float(same_request_sell.get(book_id, 0.0) or 0.0)
            buy_after = buy_before + (qty_f if token in {"buy", "bid", "b", "0"} else 0.0)
            sell_after = sell_before + (qty_f if token not in {"buy", "bid", "b", "0"} else 0.0)

            # Orders can fill in any subset/order. Reserve the worst reachable
            # absolute inventory rather than netting a symmetric Maker pair.
            batch = add_order_to_batch(
                net=current_net, buy_before=buy_before, sell_before=sell_before,
                side=token, quantity=qty_f, min_order=min_size, eps=eps,
            )
            previous_worst = batch.previous_worst_abs
            new_worst = batch.new_worst_abs
            delta_worst = batch.delta_worst_abs
            risk_reducing_batch = batch.risk_reducing
            open_delta = 1 if batch.opens_productive_slot else 0

            headroom = 1.0
            try:
                headroom = float(self._research_volume_cap_headroom(state, book_id))
            except Exception:
                headroom = 1.0

            if not risk_reducing_batch:
                projected_total_abs = shadow_abs + delta_worst
                projected_open = shadow_open + open_delta
                projected_active = shadow_active + open_delta
                reason = None
                if headroom <= 0.0:
                    reason = "VOLUME_CAP"
                elif projected_total_abs > max_abs + 1e-12:
                    # A1.7.4.3 makes the configured aggregate cap absolute. The
                    # old A1.7.3 emergency overflow is intentionally disabled:
                    # recovery may consume reserved headroom, but may never make
                    # 2.0 BASE mean 2.125+ BASE.
                    reason = "STRICT_EXPOSURE_HEADROOM"
                elif projected_open > max_open:
                    reason = "OPEN_BOOK_CAP"
                elif projected_active > max_active:
                    reason = "ACTIVE_BOOK_CAP"
                if reason is not None:
                    if reason == "STRICT_EXPOSURE_HEADROOM":
                        self._direct_strict_exposure_blocks = int(
                            getattr(self, "_direct_strict_exposure_blocks", 0) or 0
                        ) + 1
                        try:
                            self._emit(
                                "A1743_STRICT_EXPOSURE_BLOCK", force=True,
                                tick=int(getattr(self, "_tick", 0) or 0),
                                book=int(book_id), side=str(side), quantity=float(qty_f),
                                filled_abs_base=float(filled_abs),
                                reserved_abs_base=float(reserved_abs),
                                shadow_abs_base=float(shadow_abs),
                                projected_total_abs_base=float(projected_total_abs),
                                max_total_abs_base=float(max_abs),
                                pending_orders=len(self._direct_pending_ledger()),
                                inflight_version=DIRECT_INFLIGHT_RESERVATION_VERSION,
                            )
                        except Exception:
                            pass
                    self._research_log_final_contract_reject(
                        book_id, side, old_price_f, best_bid, best_ask, reason, False,
                    )
                    continue

            # Over-cap reductions are legal. They reserve no new risk, but we do
            # not assume headroom is restored until a later state confirms fill.
            shadow_abs += delta_worst
            shadow_open += open_delta
            shadow_active += open_delta
            same_request_buy[book_id] = buy_after
            same_request_sell[book_id] = sell_after
            same_request_worst_abs[book_id] = new_worst
            same_request_owned_sides.add(owner_key)
            validated_ids.add(id(instruction))

        merged = []
        for item in original:
            if _kind(item) in placement_types:
                if id(item) in validated_ids:
                    merged.append(item)
            else:
                merged.append(item)
        try:
            response.instructions[:] = merged
        except Exception:
            object.__setattr__(response, "instructions", merged)

    # ------------------------------------------------------------------
    # Direct orchestration: no separate maintenance/alpha economic authority.
    # The fast screen still supplies workload/Kappa priority; TotalScore is the
    # final rank among economically valid flat candidates.
    # ------------------------------------------------------------------
    def build_mm_strategy_instructions(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        selection,
        predictions,
        regime,
        collect_archetypes: bool = True,
    ) -> dict:
        started = time.perf_counter()
        self._research_last_selection = selection
        self._research_last_predictions = predictions
        self._sync_exchange_constraints(state)
        self._research_bind_volume_state(state)
        self._research_score_ev_last = {}
        self._direct_forced_recovery_books_this_tick = set()
        # v6.2: the caps are the universe's, applied once the universe is known.
        try:
            self._v62_apply_caps(state)
        except Exception:
            self._v62_errors = int(getattr(self, "_v62_errors", 0) or 0) + 1

        stats: dict[str, Any] = {
            "direct_mode": 1,
            "managed": 0,
            "candidates": 0,
            "quoted": 0,
            "taker_entries": 0,
            "instructions": 0,
            "skipped_negative_lifecycle": 0,
            "skipped_hard_safety": 0,
            "portfolio_open_slots": 0,
            "portfolio_headroom_stop": 0,
            "direct_dust_nonflat": 0,
            "direct_effective_open_books": 0,
            "direct_dust_skipped_management": 0,
            "direct_partial_recovery_holds": 0,
            "direct_dust_normalize_instructions": 0,
            "direct_liveness_blocked_ticks": 0,
            "direct_dust_recovery_reserve_abs": 0.0,
            "direct_v601_reserve_dust_books": 0,
            "direct_v603_short_lot_releases": 0,
            "direct_v61_no_loss": 0,
            "direct_v61_taker_refusals": 0,
            "direct_v61_compaction_refusals": 0,
            "direct_v61_state_gap_repair": 0,
            "direct_v61_state_gaps": 0,
            "direct_v61_state_repeats": 0,
            "direct_v61_gap_reseeds": 0,
            "direct_v61_standing_reseeds": 0,
            "direct_v61_request_memo": 0,
            "direct_v61_memo_hits": 0,
            "direct_v611_floor_reprice": 0,
            "direct_v611_price_lift": 0,
            "direct_v611_seed_floored": 0,
            "direct_v611_prices_lifted": 0,
            "direct_v62_breadth": 0,
            "direct_v62_quoted_books": 0,
            "direct_v62_placements": 0,
            "direct_v62_making": 0.0,
            "direct_v621_managed_universe": 0,
            "direct_v621_forced_inventory": 0,
            "direct_v622_seed_at_breadth": 0,
            "direct_v622_restored_books": 0,
            "direct_v623_premium_floor": 0,
            "direct_v623_releases": 0,
            "direct_v624_release_life": 0,
            "direct_v625_two_sided": 0,
            "direct_v625_cap_pace": 0,
            "direct_v626_loss_budget": 0,
            "direct_v626_capture_balance": 0,
            "direct_v626_quote_life": 0,
        }

        profile_by_id = {int(p.book_id): p for p in (getattr(selection, "profiles", None) or [])}
        screen = getattr(self, "_research_last_screen", None)
        selected_ids = {int(x) for x in (getattr(screen, "selected", None) or [])}
        if not selected_ids:
            selected_ids = {int(x) for x in (predictions or {}).keys()}
        # v6.2: every book is selected, so no valid entry quote is torn down as UNSELECTED.
        if self._v62_on():
            selected_ids = {int(x) for x in (getattr(state, "books", None) or {})}

        # A1.7.3.1: service the exact bound partial-order remainder before generic quote
        # maintenance can cancel them. This path places no new sub-minimum order.
        partial_hold_instructions, partial_holds = self._direct_service_partial_fill_recovery(
            response, state,
        )
        stats["instructions"] += int(partial_hold_instructions)
        stats["direct_partial_recovery_holds"] = int(partial_holds)

        # A1.7: a valid resting entry quote can survive a temporary top-K miss.
        # Invalid/unprofitable resting entry quotes are canceled immediately.
        quote_maintenance = self._direct_maintain_unselected_entry_quotes(
            response, state, selected_ids,
        )
        stats_quote_maintenance = int(quote_maintenance)
        stats["direct_quote_maintenance_instructions"] = stats_quote_maintenance
        stats["instructions"] += stats_quote_maintenance

        regime_params = self.get_regime_params(regime)
        manage_queue = []
        candidates = []

        # A1.6.2: refresh the selector in Direct orchestration before servicing
        # parked dust. A1.6.1 added the compaction executor but never populated
        # ``_research_dust_compact_ids_this_tick`` in this overridden build path.
        self._research_dust_compact_ids_this_tick = self._select_dust_compaction_books(state)
        stats["direct_dust_compact_selected"] = len(self._research_dust_compact_ids_this_tick)
        compact_before = int(getattr(self, "_research_dust_compact_orders", 0) or 0)
        compact_instructions = self._direct_compact_selected_dust(response, state)
        stats["direct_dust_compact_instructions"] = int(compact_instructions)
        stats["direct_dust_compact_orders_delta"] = max(
            0, int(getattr(self, "_research_dust_compact_orders", 0) or 0) - compact_before
        )
        stats["instructions"] += int(compact_instructions)

        # A1.9.7 P1: books evaluated on the tick after their entry fill.
        a197_postfill_books: set[int] = set()
        # Inventory is never dependent on acquisition shortlist membership.
        for raw_id, book in (getattr(state, "books", None) or {}).items():
            book_id = int(raw_id)
            if not getattr(book, "bids", None) or not getattr(book, "asks", None):
                continue
            mid = 0.5 * (float(book.bids[0].price) + float(book.asks[0].price))
            inventory = self._net_inventory(book_id, mid)
            if str(getattr(inventory, "band", "FLAT") or "FLAT").upper() != "FLAT":
                qty_abs = abs(float(getattr(inventory, "net_base", 0.0) or 0.0))
                eps = float(self._execution_flat_epsilon())
                min_size_local = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
                if self._v600_on():
                    # v6.0.0: only dust (and a parked inherited clip) skips; a short lot is managed.
                    dust_skip = self._v600_skip_management(book_id, qty_abs, eps=eps, min_order=min_size_local)
                else:
                    dust_skip = qty_abs > eps and qty_abs + 1e-12 < min_size_local
                if dust_skip:
                    stats["direct_dust_skipped_management"] += 1
                    continue
                # Persistent entry quotes must be canceled as soon as inventory
                # opens.  Legitimate inventory-exit orders remain authoritative.
                # A1.9.7 P1: unless the position is already inside ABSOLUTE
                # protection.  Then it is evaluated on this tick, and a taker exit
                # cancels the entry quotes itself (cancel-before-taker).
                a197_postfill = False
                if self._direct_entry_quote_orders(book_id):
                    a197_postfill = self._a197_postfill_candidate(book_id, inventory, mid)
                    if not a197_postfill:
                        self._a197_note_gap_skip(book_id, SKIP_INVENTORY_OPENED, inventory, mid)
                        n_cancel = self._direct_cancel_entry_quotes(
                            response, book_id, reason="INVENTORY_OPENED",
                        )
                        stats["instructions"] += int(n_cancel)
                        continue
                if not a197_postfill and self._direct_book_has_live_order(book_id):
                    self._a197_note_gap_skip(book_id, SKIP_LIVE_ORDER, inventory, mid)
                    continue
                profile = profile_by_id.get(book_id)
                prediction = (predictions or {}).get(book_id)
                if profile is None:
                    # A forced inventory book should normally have a profile.
                    # If it does not, avoid creating new exposure; the next tick
                    # can retry once the profile is available.
                    self._a197_note_gap_skip(book_id, SKIP_NO_PROFILE, inventory, mid)
                    if a197_postfill:
                        stats["instructions"] += int(self._direct_cancel_entry_quotes(
                            response, book_id, reason="INVENTORY_OPENED",
                        ))
                    continue
                archetype = self.classify_book_archetype(profile, regime)
                params = self.merge_regime_and_archetype_params(regime_params, archetype)
                urgency = self._inventory_urgency(inventory, params, regime, archetype)
                manage_queue.append((urgency, book_id, book, inventory, params, archetype))
                if a197_postfill:
                    a197_postfill_books.add(book_id)

        manage_queue.sort(key=lambda row: row[0], reverse=True)
        for _urg, book_id, book, inventory, params, archetype in manage_queue[: self.max_managed_books_per_tick]:
            if book_id in a197_postfill_books:
                n = self._a197_manage_postfill(
                    response, state, book_id, book, inventory, params, regime, archetype,
                )
            else:
                self._a197_close_gap(book_id, p1_acted=False)
                n = self._manage_inventory(
                    response,
                    state,
                    book_id,
                    book,
                    inventory,
                    params,
                    regime,
                    archetype,
                )
            if n:
                stats["managed"] += 1
                stats["instructions"] += int(n)
        # A1.9.7: books the per-tick management cap left out wait one more tick.
        # A post-fill book among them still has its entry quotes cancelled.
        for _urg, book_id, _book, inventory, _params, _arch in manage_queue[self.max_managed_books_per_tick:]:
            self._a197_note_gap_skip(book_id, SKIP_NOT_MANAGED, inventory, None)
            if book_id in a197_postfill_books:
                self._a197_postfill_truncated = int(getattr(self, "_a197_postfill_truncated", 0) or 0) + 1
                stats["instructions"] += int(self._direct_cancel_entry_quotes(
                    response, book_id, reason="INVENTORY_OPENED",
                ))

        # A1.5 keeps A1.3 early portfolio admission.  Final contract validation remains the
        # last authority, but do not build more new-exposure books than the
        # current portfolio can possibly admit in this request.
        diag = getattr(self, "_research_inventory_lane_diag", {}) or {}
        abs_now = float(diag.get("total_abs_base_inventory", 0.0) or 0.0)
        open_now = int(diag.get("actual_nonflat_inventory", 0) or 0)
        dust_now = int(diag.get("dust_nonflat_inventory", 0) or 0)
        active_now = int(diag.get("active_nonflat_inventory", 0) or 0)
        # Dust is exact BASE risk but not a productive open-book slot. This is
        # the liveness repair exposed by the 4,229-tick A1.6.0 run.
        effective_open_now = int(active_now)
        stats["direct_dust_nonflat"] = int(dust_now)
        stats["direct_effective_open_books"] = int(effective_open_now)
        max_abs = float(getattr(self, "research_max_total_abs_base", 2.0) or 2.0)
        max_open = int(getattr(self, "research_max_total_open_books", 8) or 8)
        max_active = int(getattr(self, "research_max_active_open_books", 6) or 6)
        min_size = max(1e-12, float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25))
        reserved_abs, reserved_open = self._direct_outstanding_exposure_reservation(state)
        # A1.9.5 F3: charge acquisition against PRODUCTIVE base only.  Parked
        # dust is sub-min_order residue the Kappa loss floor correctly refuses
        # to realize, so it is permanent; billing it to the acquisition budget
        # decays productive capacity to zero.  Bounded by the dust class
        # ceiling, and the overflow past that ceiling still counts.
        dust_abs_now = float(diag.get("dust_abs_base_inventory", 0.0) or 0.0)
        # A1.9.6 F9: the legacy ledger is outside the tracker, so the inventory
        # scan no longer sees it.  It is still exposure and still dust, and the
        # F3 legacy bonus exempts exactly this amount -- charged, then excused.
        a196_ledger_abs_now = self._a196_ledger_abs()
        abs_now += a196_ledger_abs_now
        dust_abs_now += a196_ledger_abs_now
        # A1.9.6.1: inherited lots waiting for a believable quote are exposure;
        # the F10 allowance below covers them inside its cap.
        a1961_pending_abs_now = self._a1961_pending_abs()
        abs_now += a1961_pending_abs_now
        dust_exempt_abs = self._a195_dust_exempt_abs(
            total_abs=abs_now, dust_abs=dust_abs_now,
            min_order=min_size, max_abs=max_abs,
        )
        stats["direct_a195_dust_exempt_abs"] = float(dust_exempt_abs)
        effective_abs_now = abs_now - dust_exempt_abs + float(reserved_abs)
        # A1.9.6 F10: an inherited lot the loss floor has parked is excused,
        # bounded, and the live validator applies the identical exemption --
        # relaxing one gate alone would only move the block downstream.
        a196_inherited = self._a196_inherited_parked_report(max_abs=max_abs)
        effective_abs_now -= float(a196_inherited.exempt_abs)
        stats["direct_a196_inherited_exempt_abs"] = float(a196_inherited.exempt_abs)
        effective_open_now += int(reserved_open)
        effective_active_now = active_now + int(reserved_open)
        stats["direct_reserved_abs_base"] = float(reserved_abs)
        stats["direct_reserved_open_books"] = int(reserved_open)
        # v6.0.1 C1: the reserve's dust count is the dust the normalizer can work.  The dust
        # count itself still drives open-book and BASE accounting unchanged.
        reserve_dust_now = int(self._v601_reserve_dust(diag, dust_now))
        stats["direct_v601_reserve_dust_books"] = int(reserve_dust_now)
        stats["direct_v603_short_lot_releases"] = int(
            getattr(self, "_direct_v603_short_lot_releases", 0) or 0
        )
        stats["direct_v61_no_loss"] = int(self._v61_on())
        stats["direct_v61_taker_refusals"] = int(getattr(self, "_direct_v61_taker_refusals", 0) or 0)
        stats["direct_v61_compaction_refusals"] = int(
            getattr(self, "_direct_v61_compaction_refusals", 0) or 0
        )
        stats["direct_v61_state_gap_repair"] = int(self._v61_gap_on())
        stats["direct_v61_state_gaps"] = int(getattr(self, "_direct_v61_state_gaps", 0) or 0)
        stats["direct_v61_state_repeats"] = int(getattr(self, "_direct_v61_state_repeats", 0) or 0)
        stats["direct_v61_gap_reseeds"] = int(getattr(self, "_direct_v61_gap_reseeds", 0) or 0)
        stats["direct_v61_standing_reseeds"] = int(getattr(self, "_direct_v61_standing_reseeds", 0) or 0)
        stats["direct_v61_request_memo"] = int(self._v61_memo_on())
        stats["direct_v61_memo_hits"] = int(getattr(self, "_v61_memo_hits", 0) or 0)
        v611_counts = dict(getattr(self, "_v611_counts", {}) or {})
        stats["direct_v611_floor_reprice"] = int(self._v611_floor_reprice_on())
        stats["direct_v611_price_lift"] = int(self._v611_price_lift_on())
        stats["direct_v611_seed_floored"] = int(v611_counts.get("seed_floored", 0))
        stats["direct_v611_prices_lifted"] = int(sum(
            n for key, n in v611_counts.items() if key.startswith("lifted_")
        ))
        v62_request = dict(getattr(self, "_v62_request", {}) or {})
        stats["direct_v62_breadth"] = int(self._v62_on())
        stats["direct_v62_quoted_books"] = int(v62_request.get("quoted_books", 0))
        stats["direct_v62_placements"] = int(v62_request.get("placements", 0))
        v62_mirror = getattr(self, "_v62_mirror", None)
        stats["direct_v62_making"] = float(v62_mirror.making()) if v62_mirror is not None else 0.0
        v621_last = dict(getattr(self, "_v621_last", {}) or {})
        stats["direct_v621_managed_universe"] = int(self._v621_on())
        stats["direct_v621_forced_inventory"] = int(v621_last.get("forced_inventory", 0))
        v622_counts = dict(getattr(self, "_v622_counts", {}) or {})
        stats["direct_v622_seed_at_breadth"] = int(self._v622_on())
        stats["direct_v622_restored_books"] = int(v622_counts.get("restored_books", 0))
        stats["direct_v623_premium_floor"] = int(self._v623_on())
        stats["direct_v623_releases"] = int(dict(getattr(self, "_v623_counts", {}) or {}).get("releases", 0))
        stats["direct_v624_release_life"] = int(self._v624_on())
        stats["direct_v625_two_sided"] = int(self._v625_two_sided_on())
        stats["direct_v625_cap_pace"] = int(self._v625_cap_pace_on())
        stats["direct_v626_loss_budget"] = int(self._v626_loss_budget_on())
        stats["direct_v626_capture_balance"] = int(self._v626_capture_balance_on())
        stats["direct_v626_quote_life"] = int(self._v626_quote_life_on())
        recovery_reserve_abs = dust_recovery_reserve_abs(
            dust_count=reserve_dust_now, min_order=min_size,
        )
        stats["direct_dust_recovery_reserve_abs"] = float(recovery_reserve_abs)
        portfolio_slots = direct_liveness_admission_slots(
            effective_abs=effective_abs_now,
            active_books=effective_active_now,
            effective_open_books=effective_open_now,
            dust_count=reserve_dust_now,
            max_abs=max_abs,
            max_active=max_active,
            max_open=max_open,
            min_order=min_size,
        )
        a196_gate_slots = int(portfolio_slots)
        # A1.9.5 F3 telemetry: the counterfactual slot count, so the class's
        # effect is a measured number rather than an inference.  Sampled on the
        # existing cadence -- this recomputes admission, so it is not free.
        a195_tick = int(getattr(self, "_tick", 0) or 0)
        if (
            self._a195_dust_capacity_enabled()
            and (a195_tick <= 2 or a195_tick % DIRECT_TELEMETRY_SAMPLE_TICKS == 0)
        ):
            try:
                slots_without = direct_liveness_admission_slots(
                    effective_abs=abs_now - float(a196_inherited.exempt_abs) + float(reserved_abs),
                    active_books=effective_active_now,
                    effective_open_books=effective_open_now,
                    dust_count=reserve_dust_now,
                    max_abs=max_abs,
                    max_active=max_active,
                    max_open=max_open,
                    min_order=min_size,
                )
                self._a195_emit_dust_capacity(
                    tick=a195_tick, total_abs=abs_now, dust_abs=dust_abs_now,
                    min_order=min_size, max_abs=max_abs,
                    slots_before=slots_without, slots_after=portfolio_slots,
                )
            except Exception:
                pass

        if selected_ids and portfolio_slots <= 0:
            self._direct_liveness_blocked_ticks = int(
                getattr(self, "_direct_liveness_blocked_ticks", 0) or 0
            ) + 1
        else:
            self._direct_liveness_blocked_ticks = 0
        stats["direct_liveness_blocked_ticks"] = int(self._direct_liveness_blocked_ticks)

        normalize_n = self._direct_normalize_irreducible_dust(
            response,
            state,
            effective_abs=effective_abs_now,
            effective_active=effective_active_now,
            effective_open=effective_open_now,
            force_liveness=bool(
                selected_ids and self._direct_liveness_blocked_ticks >= DIRECT_LIVENESS_TRIGGER_TICKS
            ),
        )
        if normalize_n:
            stats["direct_dust_normalize_instructions"] = int(normalize_n)
            stats["instructions"] += int(normalize_n)
            # Recovery owns new exposure this request; do not compete with it by
            # admitting a fresh acquisition in the same batch.
            portfolio_slots = 0
        stats["portfolio_open_slots"] = int(portfolio_slots)
        # A1.9.6 telemetry: every term of the admission formula on one row.
        # The A1.9.5 run needed an offline replay to learn which constraint was
        # binding and why; from here a zero is read, never inferred.
        if a195_tick <= 2 or a195_tick % DIRECT_TELEMETRY_SAMPLE_TICKS == 0:
            try:
                self._a196_emit_admission(
                    tick=a195_tick,
                    gate_slots=a196_gate_slots,
                    final_slots=int(portfolio_slots),
                    selected=len(selected_ids or ()),
                    normalize_override=bool(normalize_n),
                    raw_total_abs=abs_now,
                    ledger_abs=a196_ledger_abs_now,
                    dust_abs=dust_abs_now,
                    dust_exempt_abs=dust_exempt_abs,
                    inherited_exempt_abs=float(a196_inherited.exempt_abs),
                    reserved_abs=float(reserved_abs),
                    active_books=effective_active_now,
                    effective_open_books=effective_open_now,
                    dust_count=reserve_dust_now,
                    max_abs=max_abs,
                    max_active=max_active,
                    max_open=max_open,
                    min_order=min_size,
                    enterable_books=max(0, len(getattr(state, "books", None) or {}) - open_now),
                )
            except Exception:
                pass

        # v6.2: every valid flat book gets its two touch quotes; the ranker, the score-EV eligibility
        # and the slot count no longer gate acquisition.  Off, the frozen path below runs unchanged.
        if self._v62_on():
            try:
                v62_placed = self._v62_acquire(response, state, stats)
                self._v625_apply_caps(state)
            except Exception:
                self._v62_errors = int(getattr(self, "_v62_errors", 0) or 0) + 1
                v62_placed = 0
            stats["instructions"] += int(v62_placed)
        else:
            # One flat-entry path. No maintenance branch and no separate alpha branch.
            if portfolio_slots > 0:
                candidate_ids = selected_ids
            else:
                candidate_ids = set()
                stats["portfolio_headroom_stop"] = 1
            for book_id in candidate_ids:
                book = (getattr(state, "books", None) or {}).get(book_id)
                profile = profile_by_id.get(book_id)
                prediction = (predictions or {}).get(book_id)
                if book is None or profile is None or prediction is None:
                    continue
                if not getattr(book, "bids", None) or not getattr(book, "asks", None):
                    continue
                mid = 0.5 * (float(book.bids[0].price) + float(book.asks[0].price))
                inventory = self._net_inventory(book_id, mid)
                if str(getattr(inventory, "band", "FLAT") or "FLAT").upper() != "FLAT":
                    continue

                archetype = self.classify_book_archetype(profile, regime)
                params = self.merge_regime_and_archetype_params(regime_params, archetype)
                edge_bias = self.get_archetype_edge_bias(archetype)
                fill_est = self.estimate_fill_probability(
                    book,
                    mid,
                    float(book.asks[0].price) - float(book.bids[0].price),
                    float(getattr(profile, "trade_rate", 0.0) or 0.0),
                    float(book.bids[0].price),
                    float(book.asks[0].price),
                    book_id=book_id,
                )
                mem = self._mem(book_id)
                expected_alpha = self.expected_alpha_score(
                    profile, prediction, fill_est, mem, book_id, state.timestamp,
                )
                rank = self._global_book_rank(expected_alpha, mem)
                ev = (getattr(self, "_research_score_ev_last", {}) or {}).get(book_id)
                if ev is None or not bool(getattr(ev, "eligible", False)) or rank <= -1e8:
                    stats["skipped_negative_lifecycle"] += 1
                    continue
                candidates.append(
                    (
                        float(rank),
                        book_id,
                        book,
                        profile,
                        prediction,
                        inventory,
                        params,
                        edge_bias,
                    )
                )

            candidates.sort(key=lambda row: row[0], reverse=True)
            stats["candidates"] = len(candidates)
            attempt_cap = max(
                int(getattr(self, "max_mm_books_per_tick", 4) or 4),
                int(getattr(self, "research_candidate_count", 11) or 11),
            )
            success_cap = min(
                max(1, int(getattr(self, "max_mm_books_per_tick", 4) or 4)),
                int(portfolio_slots),
            ) if portfolio_slots > 0 else 0
            successful_books = 0

            for row in candidates[:attempt_cap]:
                if successful_books >= success_cap:
                    break
                _rank, book_id, book, profile, prediction, inventory, params, edge_bias = row
                before = len(getattr(response, "instructions", None) or [])
                n = self._place_skewed_quotes(
                    response,
                    state,
                    book_id,
                    book,
                    profile,
                    prediction,
                    inventory,
                    params,
                    float(getattr(self, "mm_base_size", 0.25) or 0.25),
                    edge_bias,
                    stats=stats,
                )
                after = len(getattr(response, "instructions", None) or [])
                if n or after > before:
                    successful_books += 1
                    stats["quoted"] += int(bool(n))
                    stats["instructions"] += max(int(n or 0), after - before)

        # Only contract/risk safety may veto the already-decided actions here.
        self._research_sanitize_maker_instructions(response, state)
        self._research_final_validate_instructions(response, state)
        # A1.7.4.3: bridge the validator/account snapshot gap immediately after
        # the final authoritative placement set has been frozen.
        self._direct_record_pending_placements(response, state)

        self._direct_build_mm_stats(stats)
        self._last_mm_stats = stats
        self._research_timing["build_orders_ms"] = (time.perf_counter() - started) * 1000.0
        return stats

    def _direct_build_mm_stats(self, stats: dict[str, Any]) -> None:
        """Fill the request's telemetry dict, after every placement decision is frozen.

        Moved out of build_mm_strategy_instructions verbatim (v6.0.3 refactor): 472 of that
        method's 893 lines were this block, so a build that added a telemetry key and a build that
        changed admission produced the same shape of diff.  It runs after
        _direct_record_pending_placements, reads only ``self`` and ``stats``, and mutates ``stats``
        in place, so the split is a move and nothing more.
        """
        stats["direct_quote_keeps"] = int(getattr(self, "_direct_quote_keeps", 0) or 0)
        stats["direct_quote_cancels"] = int(getattr(self, "_direct_quote_cancels", 0) or 0)
        stats["direct_quote_reprices"] = int(getattr(self, "_direct_quote_reprices", 0) or 0)
        stats["direct_quote_new_batches"] = int(getattr(self, "_direct_quote_new_batches", 0) or 0)
        stats["direct_quote_unselected_keeps"] = int(
            getattr(self, "_direct_quote_unselected_keeps", 0) or 0
        )
        stats["direct_partial_bound_holds"] = int(getattr(self, "_direct_partial_bound_holds", 0) or 0)
        stats["direct_partial_bound_pending"] = int(getattr(self, "_direct_partial_bound_pending", 0) or 0)
        stats["direct_partial_bound_expired"] = int(getattr(self, "_direct_partial_bound_expired", 0) or 0)
        stats["direct_partial_replacement_blocks"] = int(getattr(self, "_direct_partial_replacement_blocks", 0) or 0)
        stats["direct_dust_kappa_version"] = DIRECT_DUST_KAPPA_VERSION
        stats["direct_dust_kappa_maker_floor_bps"] = float(DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS)
        stats["direct_dust_kappa_allows"] = int(getattr(self, "_direct_dust_kappa_allows", 0) or 0)
        stats["direct_dust_kappa_blocks"] = int(getattr(self, "_direct_dust_kappa_blocks", 0) or 0)
        stats["direct_a175_dust_patience_ticks"] = int(DIRECT_A175_DUST_PATIENCE_TICKS)
        stats["direct_a175_dust_escalation_ticks"] = int(DIRECT_A175_DUST_ESCALATION_TICKS)
        stats["direct_a175_dust_max_floor_bps"] = float(DIRECT_A175_DUST_MAX_FLOOR_BPS)
        stats["direct_a175_dust_age_escalated_allows"] = int(getattr(self, "_direct_a175_dust_escalated_allows", 0) or 0)
        stats["direct_inflight_reservation_version"] = DIRECT_INFLIGHT_RESERVATION_VERSION
        stats["direct_pending_exposure_orders"] = len(self._direct_pending_ledger())
        stats["direct_pending_exposure_recorded"] = int(getattr(self, "_direct_pending_exposure_recorded", 0) or 0)
        stats["direct_pending_exposure_acked"] = int(getattr(self, "_direct_pending_exposure_acked", 0) or 0)
        stats["direct_pending_exposure_expired"] = int(getattr(self, "_direct_pending_exposure_expired", 0) or 0)
        stats["direct_pending_exposure_fill_reductions"] = int(getattr(self, "_direct_pending_exposure_fill_reductions", 0) or 0)
        stats["direct_strict_exposure_blocks"] = int(getattr(self, "_direct_strict_exposure_blocks", 0) or 0)
        stats["direct_book_ownership_version"] = DIRECT_BOOK_OWNERSHIP_VERSION
        stats["direct_book_ownership_reserves"] = int(getattr(self, "_direct_book_ownership_reserves", 0) or 0)
        stats["direct_book_ownership_blocks"] = int(getattr(self, "_direct_book_ownership_blocks", 0) or 0)
        stats["direct_book_ownership_releases"] = int(getattr(self, "_direct_book_ownership_releases", 0) or 0)
        stats["direct_exchange_order_identities"] = len(self._direct_exchange_identity_registry())
        stats["direct_identity_releases"] = int(getattr(self, "_direct_identity_releases", 0) or 0)
        stats["direct_stale_cancels_ignored"] = int(getattr(self, "_direct_stale_cancels_ignored", 0) or 0)
        stats["direct_release_mismatch_blocks"] = int(getattr(self, "_direct_release_mismatch_blocks", 0) or 0)
        stats["direct_a197_version"] = A197_POSTFILL_PROTECTION_VERSION
        stats["direct_a197_postfill_acts"] = int(getattr(self, "_a197_postfill_acts", 0) or 0)
        stats["direct_a197_postfill_market"] = int(getattr(self, "_a197_postfill_market", 0) or 0)
        stats["direct_a197_postfill_fallback_cancels"] = int(getattr(self, "_a197_postfill_fallback_cancels", 0) or 0)
        stats["direct_a197_postfill_limits_stripped"] = int(getattr(self, "_a197_postfill_limits_stripped", 0) or 0)
        stats["direct_a197_postfill_maker_refused"] = int(getattr(self, "_a197_postfill_maker_refused", 0) or 0)
        stats["direct_a197_postfill_sign_flips"] = int(getattr(self, "_a197_postfill_sign_flips", 0) or 0)
        stats["direct_a197_postfill_truncated"] = int(getattr(self, "_a197_postfill_truncated", 0) or 0)
        stats["direct_a197_exit_gap_rows"] = int(getattr(self, "_a197_exit_gap_rows", 0) or 0)
        stats["direct_a197_exit_gap_ticks_total"] = int(getattr(self, "_a197_exit_gap_ticks_total", 0) or 0)
        stats["direct_a198_version"] = A198_ABSOLUTE_AUTHORITY_VERSION
        stats["direct_a198_absolute_taker_authority"] = int(bool(getattr(self, "research_a198_absolute_taker_authority", True)))
        stats["direct_a198_restores"] = int(getattr(self, "_a198_restores", 0) or 0)
        stats["direct_a198_restored_recovery"] = int(getattr(self, "_a198_restored_recovery", 0) or 0)
        stats["direct_a198_restored_relative"] = int(getattr(self, "_a198_restored_relative", 0) or 0)
        stats["direct_a198_restored_postfill"] = int(getattr(self, "_a198_restored_postfill", 0) or 0)
        stats["direct_a199_version"] = A199_RISK_STATE_VERSION
        stats["direct_a199_session_epoch_version"] = A199_SESSION_EPOCH_VERSION
        stats["direct_a199_exit_pending_authority"] = int(bool(getattr(self, "research_a199_exit_pending_authority", True)))
        stats["direct_a199_epoch_resync"] = int(bool(getattr(self, "research_a199_epoch_resync", True)))
        stats["direct_a199_pending_books"] = len(getattr(self, "_a199_exit_pending", {}) or {})
        stats["direct_a199_pending_entered"] = int(getattr(self, "_a199_pending_entered", 0) or 0)
        stats["direct_a199_pending_cleared"] = int(getattr(self, "_a199_pending_cleared", 0) or 0)
        stats["direct_a199_rule_loss_maker"] = int(getattr(self, "_a199_rule_loss_maker", 0) or 0)
        stats["direct_a199_rule_not_exiting"] = int(getattr(self, "_a199_rule_not_exiting", 0) or 0)
        stats["direct_a199_stall_rows"] = int(getattr(self, "_a199_stall_rows", 0) or 0)
        stats["direct_a199_stall_max_ticks"] = int(getattr(self, "_a199_stall_max_ticks", 0) or 0)
        stats["direct_a199_epoch_rewinds"] = int(getattr(self, "_a199_epoch_rewinds", 0) or 0)
        stats["direct_a199_epoch_registry_rows_cleared"] = int(getattr(self, "_a199_epoch_registry_rows_cleared", 0) or 0)
        stats["direct_a199_epoch_reseeds"] = int(getattr(self, "_a199_epoch_reseeds", 0) or 0)
        stats["direct_a199_epoch_deferred_books"] = len(getattr(self, "_a199_deferred", {}) or {})
        stats["direct_a199_epoch_entries_blocked"] = int(getattr(self, "_a199_epoch_entries_blocked", 0) or 0)
        stats["direct_a199_epoch_placements_stripped"] = int(getattr(self, "_a199_epoch_placements_stripped", 0) or 0)
        stats["direct_a199_epoch_resync_active"] = int(bool(getattr(self, "_a199_resync", None)))
        stats["direct_a199_epoch_resyncs_closed"] = int(getattr(self, "_a199_epoch_resyncs_closed", 0) or 0)
        stats["direct_a1991_version"] = A1991_PENDING_OWNER_VERSION
        stats["direct_a1991_pending_owns_book"] = int(bool(getattr(self, "research_a1991_pending_owns_book", True)))
        stats["direct_a1991_rule_positive_maker"] = int(getattr(self, "_a1991_rule_positive_maker", 0) or 0)
        stats["direct_a1992_version"] = A1992_IDLE_GC_VERSION
        stats["direct_a1992_idle_gc"] = int(bool(getattr(self, "research_a1992_idle_gc", True)))
        a1992 = getattr(self, "_a1992_idle_gc", None)
        a1992_snap = a1992.snapshot() if a1992 is not None else {}
        stats["direct_a1992_installed"] = int(a1992_snap.get("installed", 0) or 0)
        stats["direct_a1992_fallback_reason"] = str(a1992_snap.get("fallback_reason", "") or "")
        stats["direct_a1992_request_full_passes"] = int(a1992_snap.get("request_full_total", 0) or 0)
        stats["direct_a1992_idle_passes"] = int(a1992_snap.get("idle_passes", 0) or 0)
        stats["direct_a1992_idle_cancelled"] = int(a1992_snap.get("idle_cancelled", 0) or 0)
        stats["direct_a1992_idle_max_ms"] = float(a1992_snap.get("idle_max_ms", 0.0) or 0.0)
        stats["direct_v500_analytics_version"] = V500_ANALYTICS_VERSION
        stats["direct_v500_score_mirror_version"] = V500_SCORE_MIRROR_VERSION
        stats["direct_v500_analytics"] = int(bool(getattr(self, "research_v500_analytics", True)))
        v500 = getattr(self, "_v500_analytics", None)
        stats["direct_v500_rt_rows"] = int(getattr(v500, "rt_rows", 0) or 0)
        stats["direct_v500_counterfactual_rows"] = int(getattr(v500, "counterfactual_rows", 0) or 0)
        stats["direct_v500_observe_errors"] = int(getattr(v500, "observe_errors", 0) or 0)
        stats["direct_v500_service_errors"] = int(getattr(self, "_v500_service_errors", 0) or 0)
        v500_score = getattr(self, "_v500_last_score", None) or {}
        stats["direct_v500_trading_score"] = v500_score.get("trading_score")
        stats["direct_v500_scored_books"] = v500_score.get("scored_books")
        stats["direct_v500_kappa_score"] = v500_score.get("kappa_score")
        v501_view = getattr(self, "_v501_view", None)
        stats["direct_v501_activity_version"] = V501_ACTIVITY_VERSION
        stats["direct_v501_activity_alignment"] = int(bool(getattr(self, "research_v501_activity_alignment", True)))
        stats["direct_v501_window_open"] = int(bool(getattr(v501_view, "window_open", False)))
        stats["direct_v501_eligible_books"] = int(getattr(v501_view, "eligible", 0) or 0)
        stats["direct_v501_activated_eligible"] = int(getattr(v501_view, "activated_eligible", 0) or 0)
        stats["direct_v501_cold_eligible"] = len(getattr(v501_view, "cold", ()) or ())
        stats["direct_v501_cliff_needed"] = cliff_needed(
            stats["direct_v501_activated_eligible"], stats["direct_v501_eligible_books"],
        )
        stats["direct_v501_errors"] = int(getattr(self, "_v501_errors", 0) or 0)
        v502_counts = getattr(self, "_v502_counts", None) or {}
        stats["direct_v502_dust_liveness_version"] = V502_DUST_LIVENESS_VERSION
        stats["direct_v502_flat_residue"] = int(bool(getattr(self, "research_v502_flat_residue", True)))
        stats["direct_v502_clip_recognition"] = int(bool(getattr(self, "research_v502_clip_recognition", True)))
        stats["direct_v502_compactor_turn"] = int(bool(getattr(self, "research_v502_compactor_turn", True)))
        stats["direct_v502_market_terminal"] = int(bool(getattr(self, "research_v502_market_terminal", True)))
        stats["direct_v502_residue_abs"] = round(self._v502_residue_abs(), 8)
        stats["direct_v502_flat_residue_moved"] = int(v502_counts.get("flat_residue_moved", 0) or 0)
        stats["direct_v502_clips"] = (
            int(v502_counts.get("clip_seed", 0) or 0) + int(v502_counts.get("clip_reseed", 0) or 0)
        )
        stats["direct_v502_refusal_turns"] = int(v502_counts.get("compactor_refusal_turns", 0) or 0)
        stats["direct_v502_market_released"] = int(v502_counts.get("market_terminal_released", 0) or 0)
        stats["direct_v502_errors"] = int(getattr(self, "_v502_errors", 0) or 0)
        stats["direct_v503_newcomer_gate_version"] = V503_NEWCOMER_GATE_VERSION
        stats["direct_v503_newcomer_gate"] = int(bool(getattr(self, "research_v503_newcomer_gate", True)))
        stats["direct_v503_gate_state"] = gate_state(
            armed=bool(getattr(self, "_v503_gate_armed", False)),
            open_reason=getattr(self, "_v503_gate_open_reason", None),
        )
        stats["direct_v503_gate_open_reason"] = getattr(self, "_v503_gate_open_reason", None)
        stats["direct_v503_gate_ts"] = getattr(self, "_v503_gate_ts", None)
        stats["direct_v503_gate_quiet_requests"] = int(getattr(self, "_v503_gate_quiet_requests", 0) or 0)
        stats["direct_v503_gate_errors"] = int(getattr(self, "_v503_gate_errors", 0) or 0)
        stats["direct_v503_validator_fifo_version"] = V503_VALIDATOR_FIFO_VERSION
        stats["direct_v503_fifo_fee_exact"] = int(bool(getattr(self, "research_v503_fifo_fee_exact", True)))
        stats["direct_v503_fifo_calls"] = int(getattr(self, "_v503_fifo_calls", 0) or 0)
        stats["direct_v503_observatory_version"] = V503_OBSERVATORY_VERSION
        stats["direct_v503_state_recorder"] = int(bool(getattr(self, "research_v503_state_recorder", True)))
        stats["direct_v503_book_kappa_rows"] = int(bool(getattr(self, "research_v503_book_kappa_rows", True)))
        v503_recorder = getattr(self, "_v503_recorder", None)
        v503_snap = v503_recorder.snapshot() if v503_recorder is not None else {}
        stats["direct_v503_states_written"] = int(v503_snap.get("states_written", 0) or 0)
        stats["direct_v503_trades_written"] = int(v503_snap.get("trades_written", 0) or 0)
        stats["direct_v503_bytes_written"] = int(v503_snap.get("bytes_written", 0) or 0)
        stats["direct_v503_recorder_dropped"] = int(v503_snap.get("dropped", 0) or 0)
        stats["direct_v503_recorder_stopped"] = str(v503_snap.get("stopped_reason", "") or "")
        stats["direct_v503_recorder_errors"] = int(getattr(self, "_v503_recorder_errors", 0) or 0)
        stats["direct_v504_registration_identity_version"] = V504_REGISTRATION_IDENTITY_VERSION
        stats["direct_v504_session_per_uid"] = int(bool(getattr(self, "research_v504_session_per_uid", True)))
        v504_choice = getattr(self, "_v504_session_choice", None)
        stats["direct_v504_session_source"] = None if v504_choice is None else v504_choice.source
        stats["direct_v504_legacy_session"] = getattr(self, "research_v504_legacy_session", LEGACY_IGNORE)
        stats["direct_v504_history_anchor"] = getattr(self, "research_v504_history_anchor", ANCHOR_AUTO)
        v504_pin = getattr(self, "_v504_history_pin", None)
        stats["direct_v504_pin_source"] = None if v504_pin is None else v504_pin.source
        stats["direct_v504_disk_budget_version"] = V504_DISK_BUDGET_VERSION
        stats["direct_v504_disk_budget"] = int(bool(getattr(self, "research_v504_disk_budget", True)))
        stats["direct_v504_recorder_disk_bytes"] = int(v503_snap.get("disk_bytes", 0) or 0)
        stats["direct_v504_mirror_rounds_version"] = V504_MIRROR_ROUNDS_VERSION
        stats["direct_v504_mirror_rounds"] = int(bool(getattr(self, "research_v504_mirror_rounds", True)))
        v504_mirror = getattr(self, "_v504_mirror", None) or {}
        stats["direct_v504_mirror_start_source"] = v504_mirror.get("start_source")
        stats["direct_v504_mirror_restored_states"] = len(v504_mirror.get("history") or {})
        stats["direct_v504_errors"] = int(getattr(self, "_v504_errors", 0) or 0)
        stats["direct_v600_short_lots_version"] = V600_SHORT_LOTS_VERSION
        stats["direct_v600_short_lots"] = int(self._v600_on())
        stats["direct_v600_short_lot_min_fraction"] = float(
            getattr(self, "research_v600_short_lot_min_fraction", V600_SHORT_LOT_FRACTION_DEFAULT)
        )
        stats["direct_v600_inherited_short_lots"] = getattr(
            self, "research_v600_inherited_short_lots", V600_INHERITED_PARK
        )
        stats["direct_v600_short_lot_books"] = sum(
            1 for c in (getattr(self, "_v600_class", {}) or {}).values() if c == V600_CLASS_SHORT_LOT
        )
        stats["direct_v600_inherited_parked"] = len(getattr(self, "_v600_inherited_parked", {}) or {})
        stats["direct_v600_errors"] = int(getattr(self, "_v600_errors", 0) or 0)
        stats["direct_positive_maker_kappa_version"] = DIRECT_POSITIVE_MAKER_KAPPA_VERSION
        stats["direct_a1744_strong_maker_floor_bps"] = float(DIRECT_A1744_STRONG_MAKER_FLOOR_BPS)
        stats["direct_a1744_veto_count"] = int(getattr(self, "_direct_a1744_veto_count", 0) or 0)
        stats["direct_a1744_active_veto_books"] = len(getattr(self, "_direct_a1744_veto_active", {}) or {})
        stats["direct_a1744_catastrophic_bypass_count"] = int(getattr(self, "_direct_a1744_catastrophic_bypass_count", 0) or 0)
        stats["direct_a1744_maker_not_strong_bypass_count"] = int(getattr(self, "_direct_a1744_maker_not_strong_bypass_count", 0) or 0)
        stats["direct_a175_maker_advantage_bps"] = float(DIRECT_A175_MAKER_ADVANTAGE_BPS)
        stats["direct_a175_relative_veto_count"] = int(getattr(self, "_direct_a175_relative_veto_count", 0) or 0)
        stats["direct_a175_tail_budget_ticks"] = int(DIRECT_A175_TAIL_BUDGET_TICKS)
        stats["direct_a175_tail_budget_releases"] = int(getattr(self, "_direct_a175_tail_budget_releases", 0) or 0)
        stats["direct_a175_tail_budget_books"] = len(getattr(self, "_direct_a175_tail_budget_exhausted", set()) or set())
        stats["direct_a175_shadow_horizon_ticks"] = int(DIRECT_A175_SHADOW_HORIZON_TICKS)
        stats["direct_a175_shadow_recorded"] = int(getattr(self, "_direct_a175_shadow_recorded", 0) or 0)
        stats["direct_a175_shadow_resolved"] = int(getattr(self, "_direct_a175_shadow_resolved", 0) or 0)
        stats["direct_a175_shadow_adverse"] = int(getattr(self, "_direct_a175_shadow_adverse", 0) or 0)
        stats["direct_a175_shadow_pending"] = len(getattr(self, "_direct_a175_shadow_ledger", {}) or {})
        stats["direct_exit_refresh_version"] = DIRECT_EXIT_REFRESH_VERSION
        stats["direct_a19_phase"] = self._a19_runtime_phase()
        stats["direct_a19_behaviour_change"] = self._a19_behaviour_change()
        stats["direct_a19_profitable_exit_ttl_ms"] = float(getattr(self, "research_profitable_exit_ttl_ms", 0.0) or 0.0)
        stats["direct_a19_exit_evals"] = int(getattr(self, "_a19_exit_evals", 0) or 0)
        stats["direct_a19_eligible_evals"] = int(getattr(self, "_a19_eligible_evals", 0) or 0)
        stats["direct_a19_eligible_with_resting"] = int(getattr(self, "_a19_eligible_with_resting", 0) or 0)
        stats["direct_a19_shadow_holds"] = int(getattr(self, "_a19_shadow_holds", 0) or 0)
        stats["direct_a19_shadow_reprices"] = int(getattr(self, "_a19_shadow_reprices", 0) or 0)
        stats["direct_a19_cancel_acks"] = int(getattr(self, "_a19_cancel_acks", 0) or 0)
        stats["direct_a19_cancel_ack_ticks_total"] = int(getattr(self, "_a19_cancel_ack_ticks_total", 0) or 0)
        stats["direct_a19_cancel_not_acked"] = int(getattr(self, "_a19_cancel_not_acked", 0) or 0)
        # A1.9.0.1 observability repair: ledger view vs the account snapshot the
        # frozen hold path reads.  ledger_only_hits is the size of the blind spot.
        stats["direct_a1901_ledger_resting_hits"] = int(getattr(self, "_a19_ledger_resting_hits", 0) or 0)
        stats["direct_a1901_account_resting_hits"] = int(getattr(self, "_a19_account_resting_hits", 0) or 0)
        stats["direct_a1901_ledger_only_hits"] = int(getattr(self, "_a19_ledger_only_hits", 0) or 0)
        stats["direct_a1901_ledger_expiry_lag_hits"] = int(getattr(self, "_a19_ledger_expiry_lag_hits", 0) or 0)
        # A1.9.0.2 live observer.  These are the gate metrics: the A1.9.0.1
        # counters above stay as the control that shows the placement path was
        # blind, and direct_a1902_* is what must now be non-zero.
        stats["direct_a1902_tick_passes"] = int(getattr(self, "_a19_tick_passes", 0) or 0)
        stats["direct_a1902_tick_observations"] = int(getattr(self, "_a19_tick_observations", 0) or 0)
        stats["direct_a1902_tick_resting_hits"] = int(getattr(self, "_a19_tick_resting_hits", 0) or 0)
        stats["direct_a1902_tick_expiry_lag_hits"] = int(getattr(self, "_a19_tick_expiry_lag_hits", 0) or 0)
        stats["direct_a1902_tick_eligible"] = int(getattr(self, "_a19_tick_eligible", 0) or 0)
        stats["direct_a1902_tick_eligible_with_resting"] = int(getattr(self, "_a19_tick_eligible_with_resting", 0) or 0)
        stats["direct_a1902_tick_shadow_holds"] = int(getattr(self, "_a19_tick_shadow_holds", 0) or 0)
        stats["direct_a1902_tick_shadow_reprices"] = int(getattr(self, "_a19_tick_shadow_reprices", 0) or 0)
        stats["direct_a1902_tick_first_sightings"] = int(getattr(self, "_a19_tick_first_sightings", 0) or 0)
        stats["direct_a1902_tick_lifecycles"] = int(getattr(self, "_a19_tick_lifecycles", 0) or 0)
        stats["direct_a1902_tick_max_observed_ticks"] = int(getattr(self, "_a19_tick_max_observed_ticks", 0) or 0)
        stats["direct_a1902_tick_observed_ticks_total"] = int(getattr(self, "_a19_tick_observed_ticks_total", 0) or 0)
        stats["direct_a1902_tick_tracked_books"] = len(getattr(self, "_a19_tick_seen", {}) or {})
        stats["direct_a1902_tick_untimed_rows"] = int(getattr(self, "_a19_tick_untimed_rows", 0) or 0)
        # A1.9.0.3 lifecycle-attribution repair.  Every disposition is now named
        # from evidence, so EXPIRED means the exchange retired the order rather
        # than "we could not tell".
        counts = dict(getattr(self, "_a19_tick_disposition_counts", {}) or {})
        for name, value in sorted(counts.items()):
            stats[f"direct_a1903_disposition_{str(name).lower()}"] = int(value)
        stats["direct_a1903_agent_cancelled_lifecycles"] = int(sum(
            int(v) for k, v in counts.items() if k in AGENT_CANCEL_DISPOSITIONS
        ))
        stats["direct_a1903_entry_quote_rows_excluded"] = int(
            getattr(self, "_a19_tick_entry_quote_rows", 0) or 0
        )
        # A1.9.1 Phase B gate metrics.  HOLD is the absence of an action, so the
        # behavioural delta lives entirely in the reprice cancels below.
        stats["direct_a191_queue_preservation_enabled"] = int(self._a191_enabled())
        stats["direct_a191_holds"] = int(getattr(self, "_a191_holds", 0) or 0)
        stats["direct_a191_reprice_cancels"] = int(getattr(self, "_a191_reprice_cancels", 0) or 0)
        stats["direct_a191_placements_suppressed"] = int(getattr(self, "_a191_placements_suppressed", 0) or 0)
        stats["direct_a191_reprice_deferred_ttl"] = int(getattr(self, "_a191_reprice_deferred_ttl", 0) or 0)
        stats["direct_a191_reprice_deferred_budget"] = int(getattr(self, "_a191_reprice_deferred_budget", 0) or 0)
        stats["direct_a191_reprice_candidates"] = int(getattr(self, "_a191_reprice_candidates", 0) or 0)
        stats["direct_a191_ownership_releases"] = int(getattr(self, "_a191_ownership_releases", 0) or 0)
        stats["direct_a191_ownership_release_blocked"] = int(getattr(self, "_a191_ownership_release_blocked", 0) or 0)
        stats["direct_a191_exchange_acks"] = int(getattr(self, "_a191_exchange_acks", 0) or 0)
        _acks = int(getattr(self, "_a191_exchange_acks", 0) or 0)
        stats["direct_a191_mean_exchange_ack_ticks"] = round(
            int(getattr(self, "_a191_exchange_ack_ticks_total", 0) or 0) / _acks, 3
        ) if _acks else 0.0
        stats["direct_a191_pending_reprice_releases"] = len(getattr(self, "_a191_reprice_release", {}) or {})
        stats["direct_a191_activation_alarm"] = int(bool(getattr(self, "_a191_activation_alarm_emitted", False)))
        stats["direct_a191_cancel_emit_failures"] = int(getattr(self, "_a191_cancel_emit_failures", 0) or 0)
        stats["direct_a191_min_remaining_ttl_ms"] = float(self.A191_MIN_REMAINING_TTL_MS)
        _a191_acted = (
            int(getattr(self, "_a191_holds", 0) or 0)
            + int(getattr(self, "_a191_reprice_cancels", 0) or 0)
        )
        stats["direct_a191_hold_share_pct"] = round(
            100.0 * int(getattr(self, "_a191_holds", 0) or 0) / _a191_acted, 2
        ) if _a191_acted else 0.0
        stats["direct_a19_duplicate_acks_suppressed"] = int(
            getattr(self, "_a19_duplicate_acks_suppressed", 0) or 0
        )
        # ---- A1.9.2 fee-conditioned book risk admission ----
        stats["direct_a192_admission_enabled"] = int(self._a192_enabled())
        stats["direct_a192_phase"] = self._a192_runtime_phase()
        stats["direct_a192_behaviour_change"] = int(self._a192_behaviour_change())
        _sup = int(getattr(self, "_a192_suppressions", 0) or 0)
        _adm = int(getattr(self, "_a192_admissions", 0) or 0)
        stats["direct_a192_entries_suppressed"] = _sup
        stats["direct_a192_entries_admitted"] = _adm
        stats["direct_a192_dwell_suppressions"] = int(getattr(self, "_a192_dwell_suppressions", 0) or 0)
        stats["direct_a192_candidates"] = int(getattr(self, "_a192_candidates", 0) or 0)
        stats["direct_a192_cap_blocks"] = int(getattr(self, "_a192_cap_blocks", 0) or 0)
        stats["direct_a192_rebate_admits"] = int(getattr(self, "_a192_rebate_admits", 0) or 0)
        stats["direct_a192_no_history_admits"] = int(getattr(self, "_a192_no_history_admits", 0) or 0)
        stats["direct_a192_book_ok_admits"] = int(getattr(self, "_a192_book_ok_admits", 0) or 0)
        stats["direct_a192_recoveries"] = int(getattr(self, "_a192_recoveries", 0) or 0)
        stats["direct_a192_books_flagged"] = len(getattr(self, "_a192_flagged_books", None) or set())
        stats["direct_a192_books_quarantined"] = sum(
            1 for _b, _u in (getattr(self, "_a192_dwell_until", {}) or {}).items()
            if int(_u) > int(getattr(self, "_tick", 0) or 0)
        )
        # The headline safety number: how much entry flow this gate removed.
        # Above A192_MAX_SUPPRESSION_PCT would mean the cap failed.
        stats["direct_a192_suppression_pct"] = round(
            100.0 * _sup / (_sup + _adm), 2
        ) if (_sup + _adm) else 0.0
        stats["direct_a192_window_suppression_pct"] = round(
            100.0 * int(getattr(self, "_a192_window_suppressed", 0) or 0)
            / int(getattr(self, "_a192_window_opportunities", 0) or 0), 2
        ) if int(getattr(self, "_a192_window_opportunities", 0) or 0) else 0.0
        stats["direct_a1921_severity_priority_enabled"] = int(self._a1921_enabled())
        stats["direct_a1921_rank_defers"] = int(getattr(self, "_a1921_rank_defers", 0) or 0)
        stats["direct_a1921_coldstart_shrinks"] = int(getattr(self, "_a1921_coldstart_shrinks", 0) or 0)
        stats["direct_a1921_coldstart_flagged"] = int(getattr(self, "_a1921_coldstart_flagged", 0) or 0)
        stats["direct_a1921_window_candidates"] = int(getattr(self, "_a192_window_candidates", 0) or 0)
        _thr = self._a1921_severity_threshold() if self._a1921_enabled() else None
        stats["direct_a1921_severity_threshold"] = round(float(_thr), 4) if _thr is not None else None
        stats["direct_a1921_severity_samples"] = len(getattr(self, "_a1921_sev_hist", None) or ())
        stats["direct_a1921_seed_count"] = int(getattr(self, "_a1921_seed_count", 0) or 0)
        # Budget utilisation.  Severity deferral can only lower suppression, so
        # this is the number that says whether prioritisation is leaving volume
        # protection unused rather than reallocating it.
        _alw = int(getattr(self, "_a192_window_opportunities", 0) or 0) * float(self.A192_MAX_SUPPRESSION_PCT) / 100.0
        stats["direct_a1921_budget_utilization_pct"] = round(
            100.0 * int(getattr(self, "_a192_window_suppressed", 0) or 0) / _alw, 2
        ) if _alw > 0.0 else 0.0
        # ---- A1.9.3 breadth-critical admission ----
        stats["direct_a193_breadth_admission_enabled"] = int(bool(self._a193_enabled()))
        stats["direct_a193_admits"] = int(getattr(self, "_a193_admits", 0) or 0)
        stats["direct_a193_completion_admits"] = int(getattr(self, "_a193_completion_admits", 0) or 0)
        stats["direct_a193_refresh_admits"] = int(getattr(self, "_a193_refresh_admits", 0) or 0)
        stats["direct_a193_cost_denies"] = int(getattr(self, "_a193_cost_denies", 0) or 0)
        stats["direct_a193_budget_denies"] = int(getattr(self, "_a193_budget_denies", 0) or 0)
        stats["direct_a193_deficit_denies"] = int(getattr(self, "_a193_deficit_denies", 0) or 0)
        stats["direct_a193_window_books"] = len(getattr(self, "_a193_window_books", set()) or set())
        stats["direct_a193_score_deficit"] = int(self._a193_score_deficit())
        stats["direct_a193_max_cost_spreads"] = float(self.A193_MAX_COST_SPREADS)
        stats["direct_a193_breadth_budget"] = int(self._a193_breadth_budget(self._a193_score_deficit()))
        # ---- A1.9.4 rebate conjunction ----
        stats["direct_a194_rebate_conjunction_enabled"] = int(bool(self._a194_enabled()))
        stats["direct_a194_rebate_covered_admits"] = int(
            getattr(self, "_a194_rebate_covered_admits", 0) or 0
        )
        stats["direct_a194_waiver_withdrawn"] = int(getattr(self, "_a194_waiver_withdrawn", 0) or 0)
        stats["direct_a194_withdrawn_books"] = len(getattr(self, "_a194_withdrawn_books", set()) or set())
        stats["direct_a194_covered_books"] = len(getattr(self, "_a194_covered_books", set()) or set())
        stats["direct_a194_uncovered_bps_total"] = round(
            float(getattr(self, "_a194_uncovered_bps_total", 0.0) or 0.0), 4
        )
        # ---- A1.9.5 step 1 reconciliation observer ----
        stats["direct_a195_reconcile_observe"] = int(bool(self._a195_reconcile_enabled()))
        stats["direct_a195_reconcile_version"] = A195_RECONCILE_VERSION
        stats["direct_a195_reconcile_emits"] = int(getattr(self, "_a195_reconcile_emits", 0) or 0)
        stats["direct_a195_reconcile_diverged_books"] = len(
            getattr(self, "_a195_reconcile_diverged_books", set()) or set()
        )
        stats["direct_a195_reconcile_unresolved_books"] = len(
            getattr(self, "_a195_reconcile_unresolved_books", set()) or set()
        )
        stats["direct_a195_reconcile_max_abs_divergence"] = round(
            float(getattr(self, "_a195_reconcile_max_abs_divergence", 0.0) or 0.0), 6
        )
        stats["direct_a195_dust_capacity_class"] = int(
            bool(self._a195_dust_capacity_enabled())
        )
        stats["direct_a195_dust_capacity_version"] = A195_DUST_CAPACITY_VERSION
        stats["direct_a195_dust_capacity_emits"] = int(
            getattr(self, "_a195_dust_capacity_emits", 0) or 0
        )
        stats["direct_a195_dust_slots_recovered"] = int(
            getattr(self, "_a195_dust_capacity_slots_recovered", 0) or 0
        )
        stats["direct_a195_dust_max_exempt_abs"] = round(
            float(getattr(self, "_a195_dust_capacity_max_exempt_abs", 0.0) or 0.0), 6
        )
        # Ticks where dust exceeded its class ceiling and started counting
        # against acquisition again -- the signal that a purge is overdue.
        stats["direct_a195_dust_overflow_ticks"] = int(
            getattr(self, "_a195_dust_capacity_overflow_ticks", 0) or 0
        )
        # A1.9.5 F8: the declared taker loss floor, now actually on the wire.
        stats["direct_a195_taker_floor_enforce"] = int(self._a195_taker_floor_enabled())
        stats["direct_a195_taker_bound_version"] = A195_TAKER_BOUND_VERSION
        stats["direct_a195_taker_bound_applied"] = int(
            getattr(self, "_a195_taker_bound_applied", 0) or 0
        )
        # Exits whose declared floor was exactly 0.0 -- these are the ones that
        # would have been sent as "unbounded" by a naive forward of the floor.
        stats["direct_a195_taker_bound_zero_floor"] = int(
            getattr(self, "_a195_taker_bound_zero_floor", 0) or 0
        )
        stats["direct_a195_taker_bound_min_fraction"] = float(
            getattr(self, "_a195_taker_bound_min_fraction", 0.0) or 0.0
        )
        # A1.9.5 step 3: what the startup seed actually imported.
        stats["direct_a195_inventory_truth"] = int(self._a195_inventory_truth_enabled())
        stats["direct_a195_inventory_truth_version"] = A195_INVENTORY_TRUTH_VERSION
        stats["direct_a195_seed_books"] = int(getattr(self, "_a195_seed_books", 0) or 0)
        stats["direct_a195_seed_real_books"] = int(
            getattr(self, "_a195_seed_real_books", 0) or 0
        )
        stats["direct_a195_seed_real_abs"] = float(
            getattr(self, "_a195_seed_real_abs", 0.0) or 0.0
        )
        stats["direct_a195_seed_dust_books"] = int(
            getattr(self, "_a195_seed_dust_books", 0) or 0
        )
        stats["direct_a195_seed_dust_abs"] = float(
            getattr(self, "_a195_seed_dust_abs", 0.0) or 0.0
        )
        stats["direct_a195_legacy_ceiling_bonus"] = float(
            self._a195_legacy_ceiling_bonus()
        )
        stats["direct_a195_orphan_orders_cancelled"] = int(
            getattr(self, "_a195_orphan_orders_cancelled", 0) or 0
        )
        stats["direct_a195_orphan_books"] = int(
            getattr(self, "_a195_orphan_books", 0) or 0
        )
        # A1.9.5 step 4: breadth relief where the block actually is.
        stats["direct_a195_breadth_lane"] = int(self._a195_breadth_lane_enabled())
        stats["direct_a195_breadth_lane_version"] = A195_BREADTH_LANE_VERSION
        stats["direct_a195_breadth_grants"] = int(
            getattr(self, "_a195_breadth_grants", 0) or 0
        )
        stats["direct_a195_breadth_denies"] = int(
            getattr(self, "_a195_breadth_denies", 0) or 0
        )
        stats["direct_a195_breadth_relief_bps_total"] = float(
            getattr(self, "_a195_breadth_relief_bps_total", 0.0) or 0.0
        )
        stats["direct_a195_breadth_deny_reasons"] = dict(
            getattr(self, "_a195_breadth_deny_reasons", {}) or {}
        )
        # A1.9.6: legacy baseline, inherited parked allowance, quantity grid.
        stats["direct_a196_legacy_baseline_version"] = A196_LEGACY_BASELINE_VERSION
        stats["direct_a196_legacy_dust_ledger"] = int(self._a196_ledger_enabled())
        stats["direct_a196_ledger_books"] = len(getattr(self, "_a196_legacy_dust_ledger", {}) or {})
        stats["direct_a196_ledger_abs"] = float(self._a196_ledger_abs())
        stats["direct_a196_inherited_parked_allowance"] = int(self._a196_inherited_parked_enabled())
        stats["direct_a196_inherited_tracked_books"] = len(getattr(self, "_a196_inherited_real", {}) or {})
        stats["direct_a196_inherited_retired_books"] = len(getattr(self, "_a196_inherited_retired", []) or [])
        stats["direct_a196_inherited_exempt_max"] = float(getattr(self, "_a196_inherited_exempt_max", 0.0) or 0.0)
        stats["direct_a196_inherited_capped_samples"] = int(getattr(self, "_a196_inherited_capped_samples", 0) or 0)
        stats["direct_a196_quantity_grid_snap"] = int(self._a196_grid_snap_enabled())
        stats["direct_a196_wire_quantities_moved"] = int(getattr(self, "_a196_wire_quantities_moved", 0) or 0)
        stats["direct_a196_admission_samples"] = int(getattr(self, "_a196_admission_samples", 0) or 0)
        stats["direct_a196_admission_zero_samples"] = int(getattr(self, "_a196_admission_zero_samples", 0) or 0)
        # A1.9.6.1: seed quote guard, fee residue, taker outcome.
        stats["direct_a1961_venue_integrity_version"] = A1961_VENUE_INTEGRITY_VERSION
        stats["direct_a1961_seed_quote_guard"] = int(self._a1961_seed_quote_guard_enabled())
        stats["direct_a1961_seed_unpriced_books"] = int(getattr(self, "_a1961_seed_unpriced_books", 0) or 0)
        stats["direct_a1961_pending_seed_books"] = len(getattr(self, "_a1961_pending_seed", {}) or {})
        stats["direct_a1961_pending_seed_abs"] = float(self._a1961_pending_abs())
        stats["direct_a1961_pending_resolved"] = int(getattr(self, "_a1961_pending_resolved", 0) or 0)
        stats["direct_a1961_pending_dropped"] = int(getattr(self, "_a1961_pending_dropped", 0) or 0)
        stats["direct_a1961_fee_residue_ledger"] = int(self._a1961_fee_residue_enabled())
        stats["direct_a1961_fee_residue_books"] = len(getattr(self, "_a1961_fee_residue", {}) or {})
        stats["direct_a1961_fee_residue_abs"] = float(self._a1961_fee_residue_abs())
        stats["direct_a1961_fee_residue_units"] = int(getattr(self, "_a1961_fee_residue_units", 0) or 0)
        stats["direct_a1961_fee_residue_skipped"] = int(getattr(self, "_a1961_fee_residue_skipped", 0) or 0)
        stats["direct_a1961_taker_outcomes"] = int(getattr(self, "_a1961_taker_outcomes", 0) or 0)
        stats["direct_a1961_taker_unmatched"] = int(getattr(self, "_a1961_taker_unmatched", 0) or 0)
        stats["direct_a1961_slippage_breaches"] = int(getattr(self, "_a1961_slippage_breaches", 0) or 0)
        stats["direct_a1961_late_triggers"] = int(getattr(self, "_a1961_late_triggers", 0) or 0)
        stats["direct_a1961_worst_slippage_bps"] = float(getattr(self, "_a1961_worst_slippage_bps", 0.0) or 0.0)
        stats["direct_a192_max_suppression_pct"] = float(self.A192_MAX_SUPPRESSION_PCT)
        stats["direct_a192_net_bps_floor"] = float(self.A192_NET_BPS_FLOOR)
        stats["direct_a192_tail_shortfall_floor_bps"] = float(self.A192_TAIL_SHORTFALL_FLOOR_BPS)
        stats["direct_a192_activation_alarm"] = int(
            bool(getattr(self, "_a192_activation_alarm_emitted", False))
        )
        stats["direct_a1903_cancel_reasons_tracked"] = len(
            getattr(self, "_a19_cancel_reason", {}) or {}
        )
        try:
            stats.update({
                f"direct_a1901_{k}": v for k, v in self._a19_ledger_ref().stats().items()
            })
        except Exception:
            pass
        stats["direct_trade_dedup_version"] = DIRECT_TRADE_DEDUP_VERSION
        stats["direct_duplicate_trade_events_skipped"] = int(
            getattr(self, "_direct_duplicate_trade_events_skipped", 0) or 0
        )
        deduper = getattr(self, "_direct_trade_deduper", None)
        stats["direct_trade_dedup_cache_size"] = len(deduper) if isinstance(deduper, DirectTradeEventDeduper) else 0




if __name__ == "__main__":
    launch(Strategy1_Research_Simple)
