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

SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_2_1"
SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_2_1"

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
        eligible = reject is None
        final_score = edge_signal + completion if eligible else float("-inf")
        lane = "NORMAL" if remaining <= 0 else ("COVERAGE" if obs <= 0 else "COMPLETION")

        return ScoreEVBreakdown(
            book=bid, side="MM", alpha=float(expected_alpha or 0.0),
            fill_prob_old=0.50, fill_prob_hazard=None, actionable_fill_prob=0.50,
            dust_prob=0.0, spread_capture_bps=capture_bps, expected_markout_bps=0.0,
            fees_bps=maker_fee, trading_ev=edge_signal, observation_count=obs,
            required_observation_count=required, observations_remaining=remaining,
            completion_value=completion, dust_cost=0.0, inventory_cost=0.0,
            latency_cost=0.0, activity_deficit_value=0.0, adverse_selection_risk=0.0,
            last_realization_time=None, recent_realized_pnl=None,
            inventory_state="FLAT" if not inventory_blocked else "OPEN", lane=lane,
            volume_cap_headroom=headroom, final_score=final_score, eligible=eligible,
            reject_reason=reject, score_velocity_value=0.0,
            expected_realization_time=None, realization_time_reference=None,
            lifecycle_ev=edge_signal, total_score_component=completion,
            required_entry_ev=0.0, taker_prob_live=0.0, taker_prob_prior=0.0,
            taker_prob_effective=0.0, taker_prob_excess=0.0,
            expected_taker_cost=0.0, expected_future_taker_cost_bps=0.0,
            expected_taker_exit_fee_bps=0.0, expected_crossing_bps=capture_bps,
            expected_slippage_bps=0.0, maker_fee_bps=maker_fee, taker_fee_bps=taker_fee,
            lifecycle_exit_samples=0, base_lifecycle_value=edge_signal,
            raw_taker_penalty=0.0, capped_taker_penalty=0.0, adverse_penalty=0.0,
            holding_penalty=0.0, latency_penalty=0.0, crossing_penalty=0.0,
            completion_multiplier=1.0, entry_ev_margin=current_edge_bps,
            entry_ev_pass=eligible,
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

    def _a1921_enabled(self) -> bool:
        return bool(getattr(self, "research_a1921_severity_priority_enabled", True))

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

        The order of these checks is the finding, not a style choice.  Fee sign
        is evaluated BEFORE any book history, because a poor-history book
        entered at a rebate produced 0 bad round trips in 21 observations while
        the same books at a positive fee produced a 30.4% bad rate and 87% of
        all cubic downside.  History alone is not the discriminator and
        `prior_n` is not consulted at all -- it measured AUC 0.502.
        """
        bid = int(book_id)
        if not self._a192_enabled():
            return {"suppress": False, "reason": A192_ALLOW_DISABLED}
        self._a192_window_roll(int(tick))
        self._a192_window_opportunities = int(getattr(self, "_a192_window_opportunities", 0) or 0) + 1

        fee = float(maker_fee_bps or 0.0)
        if fee <= 0.0:
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
            floor_net_bps=float(DIRECT_MAKER_EXIT_TARGET_BPS),
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
        if self._direct_partial_hold_active(int(book_id), state):
            self._direct_emit_partial_replacement_block(int(book_id), path="MAKER_EXIT")
            return 0

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
        if (
            str(action or "") == "AGGRESSIVE_MAKER_EXIT"
            and maker_net_bps is not None
            and not recovery_maker_ok
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

        return super()._research_place_maker_exit(
            response, state, book_id, book, inventory, qty, action,
            close_price=close_price, maker_net_bps=maker_net_bps,
        )

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        # A1.7 preserves A1.6.3 freshness protection.  Slow-request telemetry is
        # diagnostic only and does not gate trading.
        self._direct_current_state_timestamp_ns = int(getattr(state, "timestamp", 0) or 0)
        self._direct_request_wall_started = time.perf_counter()
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
        response = super().respond(state)
        # A1.9.1 Phase B: emit reprice cancels after the frozen chain has built
        # its instructions, so the shared per-book budget is known and a cancel
        # can never displace a placement the strategy already decided on.
        try:
            self._a191_service_reprice_cancels(response, state)
        except Exception:
            pass
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
        return not (qty > eps and qty + 1e-12 < min_size)

    # ------------------------------------------------------------------
    # A1.7 deterministic persistent-Maker quote ownership.
    # ------------------------------------------------------------------
    def _direct_order_client_id(self, order):
        value = getattr(order, "clientOrderId", None)
        if value is None:
            value = getattr(order, "client_order_id", None)
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

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
            if qty > eps and qty + 1e-12 < min_size:
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

        raw_rows = []
        qualified_count = 0
        actual_nonflat = 0
        active_nonflat = 0
        dust_nonflat = 0
        total_abs_base = 0.0
        for raw_id, book in books.items():
            bid = int(raw_id)
            try:
                qty = abs(float(self._research_abs_inventory(bid)))
            except Exception:
                qty = 0.0
            has_inv = qty > eps
            is_dust = bool(has_inv and qty + 1e-12 < min_size)
            if has_inv:
                actual_nonflat += 1
                total_abs_base += qty
                if is_dust:
                    dust_nonflat += 1
                else:
                    active_nonflat += 1

            try:
                kappa = self._research_kappa_book(bid)
                remaining = max(0, int(getattr(kappa, "observations_remaining", 3) or 0))
                qualified = bool(getattr(kappa, "eligible", False))
            except Exception:
                remaining, qualified = 3, False
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
        selected = select_fastpath_rows(
            rows, candidate_count=configured, score_deficit=score_deficit, tick=tick,
        )
        selected_set = {int(x) for x in selected}
        for bid in selected_set:
            last_selected[bid] = tick
        self._direct_fastpath_last_selected_tick = last_selected

        forced_inventory = [r.book_id for r in rows if r.has_inventory and r.book_id in selected_set]
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
            "total_abs_base_inventory": float(total_abs_base),
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
        raw = super()._research_read_session(identity)
        if not isinstance(raw, dict):
            return raw
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
                    self._emit(
                        "A173_PARTIAL_REMAINDER_CANCEL", force=True,
                        tick=int(getattr(self, "_tick", 0) or 0), book=int(book_id),
                        mode=str(row.get("mode") or "UNKNOWN"), desired_side=desired,
                        bound_order_id=bound_id, cancelled_orders=len(conflicting_ids),
                        net_base=float(net),
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
                continue
            self._direct_dust_kappa_allows = int(
                getattr(self, "_direct_dust_kappa_allows", 0) or 0
            ) + 1
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
        filled_active = sum(1 for net in shadow_net.values() if abs(float(net)) + eps >= min_size)
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
        }

        profile_by_id = {int(p.book_id): p for p in (getattr(selection, "profiles", None) or [])}
        screen = getattr(self, "_research_last_screen", None)
        selected_ids = {int(x) for x in (getattr(screen, "selected", None) or [])}
        if not selected_ids:
            selected_ids = {int(x) for x in (predictions or {}).keys()}

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
                if qty_abs > eps and qty_abs + 1e-12 < min_size_local:
                    stats["direct_dust_skipped_management"] += 1
                    continue
                # Persistent entry quotes must be canceled as soon as inventory
                # opens.  Legitimate inventory-exit orders remain authoritative.
                if self._direct_entry_quote_orders(book_id):
                    n_cancel = self._direct_cancel_entry_quotes(
                        response, book_id, reason="INVENTORY_OPENED",
                    )
                    stats["instructions"] += int(n_cancel)
                    continue
                if self._direct_book_has_live_order(book_id):
                    continue
                profile = profile_by_id.get(book_id)
                prediction = (predictions or {}).get(book_id)
                if profile is None:
                    # A forced inventory book should normally have a profile.
                    # If it does not, avoid creating new exposure; the next tick
                    # can retry once the profile is available.
                    continue
                archetype = self.classify_book_archetype(profile, regime)
                params = self.merge_regime_and_archetype_params(regime_params, archetype)
                urgency = self._inventory_urgency(inventory, params, regime, archetype)
                manage_queue.append((urgency, book_id, book, inventory, params, archetype))

        manage_queue.sort(key=lambda row: row[0], reverse=True)
        for _urg, book_id, book, inventory, params, archetype in manage_queue[: self.max_managed_books_per_tick]:
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
        effective_abs_now = abs_now + float(reserved_abs)
        effective_open_now += int(reserved_open)
        effective_active_now = active_now + int(reserved_open)
        stats["direct_reserved_abs_base"] = float(reserved_abs)
        stats["direct_reserved_open_books"] = int(reserved_open)
        recovery_reserve_abs = dust_recovery_reserve_abs(
            dust_count=dust_now, min_order=min_size,
        )
        stats["direct_dust_recovery_reserve_abs"] = float(recovery_reserve_abs)
        portfolio_slots = direct_liveness_admission_slots(
            effective_abs=effective_abs_now,
            active_books=effective_active_now,
            effective_open_books=effective_open_now,
            dust_count=dust_now,
            max_abs=max_abs,
            max_active=max_active,
            max_open=max_open,
            min_order=min_size,
        )
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
        self._last_mm_stats = stats
        self._research_timing["build_orders_ms"] = (time.perf_counter() - started) * 1000.0
        return stats


if __name__ == "__main__":
    launch(Strategy1_Research_Simple)
