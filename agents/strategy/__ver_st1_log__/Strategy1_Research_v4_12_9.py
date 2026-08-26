# SPDX-License-Identifier: MIT
"""Strategy1 research V4.3 Phase 1: independent MarketRegime / ScoreRegime.

Requires Strategy1_Debug.py beside this file.

The production Strategy1 parent remains untouched. This research subclass keeps the
existing async [S1R_*] telemetry and intentionally changes only the policy defects
identified from the testnet research log:

1. Global STRESSED no longer forces every individual book to STRESSED.
2. Neutral per-book archetype fallback is MM_BOOK rather than TOXIC_BOOK.
3. Spread stress/toxicity cutoffs adapt cross-sectionally (P95/P99 by default).
4. INACTIVE books may bootstrap only when selected by COVERAGE/KAPPA_COMPLETION during score acquisition.
5. Global STRESSED becomes conservative quote mode instead of a total market kill.
6. The simulator min order size is synchronized per request; sub-minimum dynamic
   sizes may be promoted only when the remaining base-inventory headroom permits it.
7. One [S1R_REGIME] record and detailed per-book toxicity decomposition are emitted.
8. Cold INACTIVE books are not mislabeled DEAD solely from bootstrap trade-rate.
9. Inventory is represented as signed base utilization (net_base / max_inventory_base).
10. Quote skew uses a reservation-price shift instead of the legacy sign-confused formula.
11. Any executable bootstrap position is managed immediately; an optional aggressive close
    after the age gate requires non-negative estimated close-at-touch economics after buffer.
12. Dust is quarantined before normal quote selection, exact flatness follows volume precision,
    and [S1R_POSITION] exposes OPEN/INCREASE/REDUCE/FLAT transitions.
13. Quote selection backfills failed top-ranked candidates up to a bounded attempt cap while
    preserving the original successful quote-book cap.
14. Recent-PnL toxicity requires enough completed samples unless loss crosses a hard floor.
15. Sparse-tape YELLOW and GREEN books retain a conservative active path instead of reverting
    to DEAD solely because recent trade_rate is near zero.
16. Sub-minimum exact inventory remains precisely tracked and parked outside the finite
    management pool when it is too small to reduce safely with one minimum-size order.
17. Large dust (> half the exchange minimum) gets bounded passive DUST_COMPACT treatment;
    every possible partial fill is non-increasing in absolute exposure.
18. Kappa-completion priority concentrates quote opportunities on books with 1-2 confirmed
    realized observations so they can reach the 3-observation score-eligibility threshold.
19. Kappa completion is isolated from normal MM through separate attempt/success
    budgets; failed completion candidates cannot consume the normal economic lane.
20. Maker opening fills are classified as ACTIONABLE versus DUST; candidate ranking learns
    post-fill actionability instead of treating every partial fill as equally useful.
21. One-away Kappa books receive an extra bounded priority only when their learned fill
    quality is acceptable; dust-prone books are penalized without adding a hard gate.
22. Incomplete-book quotes can keep the original bounded maker exposure alive slightly longer
    (750ms cap by default), reducing premature sub-minimum residuals without rescue top-ups.
23. Safe dust compaction keeps the V4 theorem but adds fill-posterior ranking and bounded
    retry cooldown so repeated 1%-quality attempts do not dominate execution capacity.
24. Normal MM and explicit maintenance quote pairs are forced maker-only inside their quote
    context; inventory-management exits retain the inherited risk behavior.
25. V4.3 Phase 1 splits MarketRegime (book/cross-section) from ScoreRegime (Kappa/coverage).
    Parent mean-spread STRESSED and UNEXPOSED_BY_PARENT no longer latch the market state.
26. V4.3 Phase 2 records maker-quote lifecycle through fill/cancel/expiry, classifies fills
    against the runtime min order size, and measures delayed maker markout off the request path.
27. V4.3 Phase 3 learns a discrete fill-hazard at quote TTL with shrinkage and calibration.
    The inherited fill estimator remains the live policy path unless explicitly enabled.
28. V4.3 Phase 4 ranks books by Score-EV (TradingEV + Kappa completion - dust/inventory/latency)
    using the runtime Kappa observation requirement. Hard safety still beats completion.
29. V4.3 Phase 5 holds maker quotes through tiny price noise, adapts TTL inside hard bounds,
    and allows experimental old-dust escape only when absolute exposure strictly falls.
30. V4.3 Phase 6 optionally screens books with a cheap score, then runs full prediction only
    on forced inventory/dust/Kappa/risk books plus a configurable candidate cap.
31. Session / Kappa observations persist for one simulation. Reset is allowed only on
    simulation-ID, network/netuid, or incompatible schema/invalid state. Timestamp rewind
    and miner reload of the same simulation must not wipe realized observations.
32. Kappa Completion Scheduler V3 ranks 1-remaining >> 2-remaining > uncovered books
    only when TradingEV stays non-negative. Hard gates still block toxic, unsafe,
    invalid-size, volume-cap, and negative-EV quotes. Completion slots are reserved
    so normal attempt/success limits cannot starve them.
33. V4.3 Phase 1.7 measures delayed maker markout at 100/250/500/1000 ms and
    computes Cont–Kukanov–Stoikov OFI only from consecutive touch price+size.
    Static imbalance is never labeled OFI. ExpectedMarkout and AdverseSelectionRisk
    feed ranking, entry gating, exit urgency, and quote width.
34. V4.3 Phase 1.8 holds maker quotes through tiny theoretical updates. Replace
    only on meaningful price, material alpha, real OFI reversal, inventory/toxicity/
    regime change, TTL expiry, or a material EV improvement. Hard safety cancels
    immediately. Adaptive TTL stretches when stable with good fill hazard, shortens
    under vol/toxicity, and skips stale state.
35. V4.3 Phase 1.9 screens ~128 books cheaply, always keeps inventory / one-away
    Kappa / risk / dust books, then runs full Strategy1 prediction on a
    configurable 16–24 candidate set. Unchanged TOB features are cached. Timing
    records screen_ms, full_predict_ms, ranking_ms, build_orders_ms, logging_ms,
    and total_response_ms. Full-universe predict remains the fallback.
36. V4.10 hardens Taker authority, uses live fees, rolling 3h Kappa observations,
    zero-loss score completion defaults, and entry-feasibility recheck caching.
37. V4.11 makes the candidate cap authoritative, concentrates acquisition into a
    sticky 8–12 book cohort, and moves Kappa-expiry pressure into cheap screening.
38. V4.11 entry ranking prices the full lifecycle (entry fee + expected maker/taker
    realization cost + crossing/slippage + holding risk) rather than maker entry only.
39. V4.11 allows one minimum executable clip for strongly positive lifecycle EV
    when the old multiplicative size falls below the near-safe band but hard risk,
    exit-capacity and headroom gates still pass.
40. V4.11 tightens safe cohort quotes and lengthens non-adverse QUIET maker TTL,
    while preserving toxic/adverse shortening and all V4.10 hard Taker safety.
41. V4.11.1 makes rolling timestamp-backed Kappa state the single authority for
    scheduler, completion lanes, realization, cohort state, and telemetry.
42. V4.11.2 adds an aggressive positive-EV Taker authority: take only when the
    live net Taker realization is non-negative, beats maker WAIT EV, and an
    explicit ONE_AWAY / failed-exit / age / low-fill / urgency trigger exists.
43. V4.11.2 adds a strict ONE_AWAY exact-minimum path: a 2/3-observation book
    may submit exactly 0.25 despite soft size shrinkage only when lifecycle EV is
    positive, safe-size >=50% of min, exit capacity >=90% of min, hard inventory
    risk/headroom pass, and enough inventory room exists.

The production Strategy1 parent remains untouched; this subclass intentionally changes the
above research-policy and correctness paths while retaining Strategy1 signals and ranking.
"""
from __future__ import annotations

import atexit
import json
import math
import os
import queue
import sys
import threading
import time
from dataclasses import replace
from typing import Any

_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

from taos.common.agents import launch
from taos.im.protocol import FinanceAgentResponse, MarketSimulationStateUpdate
from taos.im.protocol.models import (
    Book,
    LoanSettlementOption,
    OrderDirection,
    STP,
    TimeInForce,
)
from Strategy1 import (
    BookArchetype,
    BookProfile,
    BookSelection,
    DirectionForecast,
    FillProbabilityEstimate,
    InventorySnapshot,
    MarketRegime,
    RegimeParamSet,
)
from Strategy1_Debug import DebugReason, Strategy1_Debug
from research_regime_v2 import (
    DebounceState,
    RegimeV2Thresholds,
    classify_regime_v2,
    score_regime_metrics,
)
from research_quote_lifecycle import (
    MARKOUT_HORIZONS_MS,
    QuoteLifecycleStore,
    QuoteRecord,
    actual_microprice,
    classify_fill,
    optional_queue_metrics,
    sim_delta_ms,
    ms_to_ns,
    touch_distance,
)
from research_markout import (
    MARKOUT_VERSION,
    conservative_expected_markout_bps,
)
from research_adverse import (
    OfiTracker,
    UNSUPPORTED,
    composite_adverse_selection_risk,
    entry_adverse_blocked,
    expected_markout_bps,
    extract_touch,
    ofi_against_position,
    quote_width_multiplier,
)
from research_fill_hazard import (
    FillHazardModel,
    HazardFeatures,
    HazardPrediction,
    cal_bucket,
    cal_bucket_label,
)
from research_score_ev import (
    LANE_COMPLETION,
    LANE_COVERAGE,
    LANE_NORMAL,
    admit_scheduler_candidate,
    required_observation_count,
    round_trip_velocity,
    score_velocity_priority,
    select_rank,
)
from research_kappa_state import (
    KAPPA_STATE_VERSION,
    build_kappa_universe,
    kappa_expiry_from_timestamps,
    rolling_observation_timestamps,
    kappa_book_state,
    summary_kappa,
)
from research_kappa_realization import (
    KAPPA_REALIZATION_VERSION,
    kappa_realization_boost,
)
from research_taker_economics import fee_rate_to_bps
from research_lifecycle_ev import lifecycle_entry_cost_bps
from research_cohort import CohortCandidate, update_sticky_cohort
from research_quote_hysteresis import (
    choose_ttl_ms,
    dust_escape_allowed,
    should_replace_quote,
)
from research_inventory_state import (
    STATE_DEFENSIVE,
    STATE_EMERGENCY,
    STATE_EXIT_ONLY,
    classify_inventory_state,
    inventory_state_policy,
    side_size_multiplier,
)
from research_same_side import (
    apply_exit_competitiveness,
    apply_fill_priority,
    same_side_suppression,
    side_is_suppressed,
)
from research_realization import (
    ACTION_AGGRESSIVE,
    ACTION_COMPETITIVE,
    ACTION_PASSIVE,
    ACTION_TAKER,
    evaluate_realization,
    exit_urgency,
    inventory_holding_risk,
    inventory_should_manage,
    kappa_completion_need,
    maker_exit_price,
)
from research_realization_ladder import clamp_ladder_bands
from research_hybrid import taker_crossing_cost_bps
from research_exit_hazard_ev import EXIT_HAZARD_EV_VERSION
from research_unified_exit import (
    ACTION_HARD_RISK_TAKER as UNIFIED_HARD_TAKER,
    ACTION_KEEP_MAKER as UNIFIED_KEEP_MAKER,
    ACTION_TAKER_PROFIT_LOCK as UNIFIED_TAKER_PROFIT_LOCK,
    ACTION_TAKER_PROTECT as UNIFIED_TAKER_PROTECT,
    breakeven_price as unified_breakeven_price,
    choose_unified_exit,
    completion_net_bps as unified_completion_net_bps,
    wait_value_bps as unified_wait_value_bps,
)
from research_velocity import (
    VELOCITY_VERSION,
    VelocityState,
)
from research_exit_quantity import choose_reduce_quantity, exchange_min_order_size
from research_dust_economics import (
    ACTION_COMPETITIVE_MAKER as DUST_ACTION_COMPETITIVE,
    ACTION_PASSIVE_MAKER as DUST_ACTION_PASSIVE,
    ACTION_TAKER as DUST_ACTION_TAKER,
    evaluate_dust_action,
    predicted_dust_blocks_increase,
    quote_would_create_dust,
)
from research_entry_size import (
    ADMISSION_SAFE,
    admit_minimum_order,
    allowed_entry_size,
    clamp_min_order_tolerance,
)
from research_execution_lanes import (
    LANE_COMPLETION as EXEC_LANE_COMPLETION,
    LANE_COVERAGE as EXEC_LANE_COVERAGE,
    LANE_REALIZATION,
    LANES as EXEC_LANES,
    LaneBook,
    apply_breadth_rotation_gate,
    normalize_lane_budgets,
    score_acquisition_granted,
    score_acquisition_grants,
    score_acquisition_mode,
    select_lane_candidates,
)
from research_volume_cap import (
    REASON_OK,
    agent_book_traded_volume,
    agent_can_add_volume,
    agent_volume_cap_headroom,
    agent_volume_cap_quote,
    agent_volume_cap_reason,
    agent_volume_cap_remaining,
    agent_volume_cap_snapshot,
)
from research_candidate_screen import (
    DEFAULT_CANDIDATE_COUNT,
    FeatureCache,
    P95_TARGET_MS,
    ScreenBook,
    ScreenResult,
    book_touch_fingerprint,
    cheap_book_score,
    clamp_candidate_count,
    deep_book_fingerprint,
    select_fast_candidates,
    timing_payload,
)
from research_session_state import (
    ACTION_RESET,
    ACTION_RESTORE,
    CURRENT_SCHEMA as RESEARCH_SESSION_SCHEMA,
    DEFAULT_TRANSITION_QUARANTINE_TICKS,
    SessionIdentity,
    build_payload,
    clear_stale_session_runtime,
    decide_session,
    enforce_monotonic,
    extract_simulation_id,
    format_reset_fields,
    format_transition_fields,
    increment_observation,
    infer_network,
    observation_total,
    reconcile_account_base,
    resolve_netuid,
    session_requires_transition_quarantine,
    state_filename,
    taker_allowed_after_transition,
)


class Strategy1_Research(Strategy1_Debug):
    RESEARCH_POLICY_VERSION = "rolling_deadline_rescue_v4_12_9"
    RESEARCH_HYBRID_VERSION = "early_escape_guard_v4_12_6"
    RESEARCH_EXIT_HAZARD_EV_VERSION = EXIT_HAZARD_EV_VERSION
    RESEARCH_VELOCITY_VERSION = VELOCITY_VERSION
    RESEARCH_KAPPA_SCHEDULER_VERSION = "rolling_deadline_v4_12_8"
    RESEARCH_KAPPA_STATE_VERSION = KAPPA_STATE_VERSION
    RESEARCH_KAPPA_REALIZATION_VERSION = KAPPA_REALIZATION_VERSION
    RESEARCH_MARKOUT_VERSION = MARKOUT_VERSION
    RESEARCH_LANES_VERSION = "execution_lanes_v4_deadline"
    RESEARCH_SCORE_ACQUISITION_VERSION = "score_acquisition_v3_deadline_rotation"
    RESEARCH_INVENTORY_STATE_VERSION = "inventory_state_v2"
    RESEARCH_EXIT_URGENCY_VERSION = "exit_urgency_v2"
    RESEARCH_LADDER_VERSION = "realization_ladder_v2"
    RESEARCH_TAKER_ECON_VERSION = "taker_economics_v2_live_fees"
    RESEARCH_LIFECYCLE_ENTRY_VERSION = "lifecycle_entry_v1"
    RESEARCH_EXIT_QTY_VERSION = "exit_quantity_v1"
    RESEARCH_DUST_ECON_VERSION = "dust_economics_v1"
    RESEARCH_SAME_SIDE_VERSION = "same_side_v2_effective_exposure"
    RESEARCH_SESSION_SCHEMA = RESEARCH_SESSION_SCHEMA
    REASON_ALIAS = {
        "LOW_EXPECTED_ALPHA": "ALPHA",
        "ZERO_ORDER_SIZE": "SIZE_ZERO",
        "MAX_INVENTORY": "INVENTORY_MAX",
        "INVALID_QUOTE_PRICES": "BAD_PRICE",
        "VOLUME_CAP": "VOLUME_CAP",
        "NON_POSITIVE_EDGE": "EDGE",
        "NEGATIVE_EXPECTED_PNL": "NEG_PNL",
        "LOW_FILL_PROBABILITY": "FILL_PROB",
        "INSTRUCTION_LIMIT": "INSTR_LIMIT",
        "INSUFFICIENT_BALANCE": "BALANCE",
        "QUOTE_ORDER_GATE": "QUOTE_GATE",
        "QUOTE_DISABLED": "REGIME_DISABLED",
        "TOXIC_BOOK": "TOXIC",
        "TOXIC_REGIME": "TOXIC_REGIME",
        "INACTIVE_TIER": "INACTIVE",
        "MM_CANDIDATE_LIMIT": "MM_LIMIT",
        "MANAGEMENT_LIMIT": "MANAGEMENT_LIMIT",
        "MANAGE_ORDER_GATE": "MANAGE_GATE",
        "MAINT_INVENTORY_NONFLAT": "MAINT_INVENTORY",
        "MAINT_ARCHETYPE_BLOCK": "MAINT_ARCHETYPE",
        "MAINT_ORDER_GATE": "MAINT_GATE",
        "NO_BOOK_SIDES": "NO_BOOK_SIDES",
        "NO_PROFILE": "NO_PROFILE",
        "AVOID_LIST": "AVOID",
        "NO_PREDICTION": "NO_PREDICTION",
        "GRACE_PERIOD": "GRACE",
        "NO_ACTION": "NO_ACTION",
        # Reserved for descendants; base Strategy1 does not currently emit these.
        "HARD_CAP": "HARD_CAP",
        "STALE": "STALE",
        "DUST": "DUST",
        "DUST_POSITION": "DUST",
        "INACTIVE_DIAGNOSTIC_ONLY": "INACTIVE_DIAG",
        "MM_SUCCESS_CAP": "MM_CAP",
        "DUST_QUARANTINE": "DUST_PARK",
        "DUST_RELEASED": "DUST_RELEASE",
        "DUST_COMPACT": "DUST_COMPACT",
        "DUST_COMPACT_BLOCKED": "DUST_COMPACT_BLOCKED",
        "KAPPA_COMPLETION": "KAPPA_COMPLETE",
        "KAPPA_COMPLETION_ATTEMPT_CAP": "KAPPA_ATTEMPT_CAP",
        "KAPPA_COMPLETION_SUCCESS_CAP": "KAPPA_SUCCESS_CAP",
        "NORMAL_MM_ATTEMPT_CAP": "NORMAL_ATTEMPT_CAP",
    }

    def initialize(self) -> None:
        # Strategy1_Debug.initialize() calls self._emit(), so prepare an early buffer.
        self._research_ready = False
        self._research_early: list[dict[str, Any]] = []
        self._rq = None
        self._rstop = None
        self._rworker = None
        self._rfile = None
        self._rdropped = 0
        super().initialize()

        cfg = self.config

        # --------------------------------------------------------------
        # Research policy: break the STRESSED/TOXIC/INACTIVE deadlock.
        # Defaults are intentionally explicit and can be overridden via
        # --agent.params without editing Strategy1.
        # --------------------------------------------------------------
        self.research_fix_global_stress = self._as_bool(
            getattr(cfg, "research_fix_global_stress", True)
        )
        self.research_neutral_fallback = self._as_bool(
            getattr(cfg, "research_neutral_fallback", True)
        )
        self.research_adaptive_spread_thresholds = self._as_bool(
            getattr(cfg, "research_adaptive_spread_thresholds", True)
        )
        self.research_stress_percentile = min(
            0.999, max(0.50, float(getattr(cfg, "research_stress_percentile", 0.95)))
        )
        self.research_toxic_percentile = min(
            0.9999, max(
                self.research_stress_percentile,
                float(getattr(cfg, "research_toxic_percentile", 0.99)),
            )
        )
        self.research_stress_floor_bps = max(
            0.0, float(getattr(cfg, "research_stress_floor_bps", 8.0))
        )
        self.research_toxic_floor_bps = max(
            0.0, float(getattr(cfg, "research_toxic_floor_bps", 10.0))
        )
        self.research_stress_fallback_bps = max(
            self.research_stress_floor_bps,
            float(getattr(cfg, "research_stress_fallback_bps", 35.0)),
        )
        self.research_toxic_fallback_bps = max(
            self.research_toxic_floor_bps,
            float(getattr(cfg, "research_toxic_fallback_bps", 40.0)),
        )
        self.research_toxic_gap_bps = max(
            0.0, float(getattr(cfg, "research_toxic_gap_bps", 2.0))
        )
        self.research_min_profiles_for_adaptive = max(
            4, int(getattr(cfg, "research_min_profiles_for_adaptive", 16))
        )
        self.research_inactive_bootstrap = self._as_bool(
            getattr(cfg, "research_inactive_bootstrap", True)
        )
        self.research_trade_global_stress = self._as_bool(
            getattr(cfg, "research_trade_global_stress", True)
        )
        self.research_global_stress_size_mult = min(
            1.0, max(
                0.05,
                float(getattr(cfg, "research_global_stress_size_mult", 0.35)),
            )
        )
        self.research_sync_min_order = self._as_bool(
            getattr(cfg, "research_sync_min_order", True)
        )
        self.research_promote_min_order = self._as_bool(
            getattr(cfg, "research_promote_min_order", True)
        )
        self.research_bootstrap_maintenance_min_order = self._as_bool(
            getattr(cfg, "research_bootstrap_maintenance_min_order", True)
        )
        # V2: the latest testnet log showed 100% INACTIVE, ~89% DEAD_BOOK and
        # fills without any confirmed flat round-trip. These switches target
        # exactly that second-stage bootstrap deadlock.
        self.research_bootstrap_dead_as_mm = self._as_bool(
            getattr(cfg, "research_bootstrap_dead_as_mm", True)
        )
        self.research_bootstrap_extreme_vol_mult = max(
            1.0, float(getattr(cfg, "research_bootstrap_extreme_vol_mult", 1.75))
        )
        self.research_fix_inventory_util = self._as_bool(
            getattr(cfg, "research_fix_inventory_util", True)
        )
        self.research_fix_quote_reservation = self._as_bool(
            getattr(cfg, "research_fix_quote_reservation", True)
        )
        self.research_bootstrap_manage_min_clip = self._as_bool(
            getattr(cfg, "research_bootstrap_manage_min_clip", True)
        )
        self.research_bootstrap_allow_aggressive_close = self._as_bool(
            getattr(cfg, "research_bootstrap_allow_aggressive_close", True)
        )
        self.research_bootstrap_force_close_ticks = max(
            1, int(getattr(cfg, "research_bootstrap_force_close_ticks", 60))
        )
        self.research_bootstrap_force_close_min_bps = float(
            getattr(cfg, "research_bootstrap_force_close_min_bps", -5.0)
        )
        self.research_bootstrap_hard_close_ticks = max(
            self.research_bootstrap_force_close_ticks,
            int(getattr(cfg, "research_bootstrap_hard_close_ticks", 180)),
        )
        self.research_dust_safe_close = self._as_bool(
            getattr(cfg, "research_dust_safe_close", True)
        )
        self.research_rotate_jsonl = self._as_bool(
            getattr(cfg, "research_rotate_jsonl", True)
        )
        # --------------------------------------------------------------
        # V4.1 Strict execution-quality fixes, all derived from the completed
        # research run. Keep them independently switchable for A/B testing.
        # --------------------------------------------------------------
        self.research_candidate_backfill = self._as_bool(
            getattr(cfg, "research_candidate_backfill", True)
        )
        self.research_candidate_attempt_cap = max(
            1, int(getattr(cfg, "research_candidate_attempt_cap", 12))
        )
        self.research_aggressive_close_touch_gate = self._as_bool(
            getattr(cfg, "research_aggressive_close_touch_gate", True)
        )
        self.research_aggressive_close_fee_buffer_bps = max(
            0.0, float(getattr(cfg, "research_aggressive_close_fee_buffer_bps", 3.0))
        )
        self.research_aggressive_close_min_net_bps = float(
            getattr(cfg, "research_aggressive_close_min_net_bps", 0.0)
        )
        self.research_toxic_pnl_min_samples = max(
            0, int(getattr(cfg, "research_toxic_pnl_min_samples", 3))
        )
        self.research_toxic_pnl_hard_floor = float(
            getattr(cfg, "research_toxic_pnl_hard_floor", -0.05)
        )
        self.research_yellow_sparse_active = self._as_bool(
            getattr(cfg, "research_yellow_sparse_active", True)
        )
        self.research_green_sparse_active = self._as_bool(
            getattr(cfg, "research_green_sparse_active", True)
        )
        self.research_dust_park_enabled = self._as_bool(
            getattr(cfg, "research_dust_park_enabled", True)
        )
        self.research_dust_heartbeat_ticks = max(
            1, int(getattr(cfg, "research_dust_heartbeat_ticks", 250))
        )
        self.research_dust_warn_ticks = max(
            self.research_dust_heartbeat_ticks,
            int(getattr(cfg, "research_dust_warn_ticks", 1000)),
        )
        # V4.1 Strict: dust liveness. A minimum-size opposite order can only
        # reduce exposure for every possible partial fill when |dust| > 0.5*min.
        self.research_dust_compact_enabled = self._as_bool(
            getattr(cfg, "research_dust_compact_enabled", True)
        )
        self.research_dust_compact_min_fraction = max(
            0.500001,
            min(0.95, float(getattr(cfg, "research_dust_compact_min_fraction", 0.50))),
        )
        self.research_dust_compact_books_per_tick = max(
            1, int(getattr(cfg, "research_dust_compact_books_per_tick", 2))
        )

        # V4.1 Strict: concentrate realized observations so more individual books
        # cross the validator's per-book minimum observation requirement.
        # Do not hard-code 3: follow miner kappa_min_observations unless overridden.
        self.research_kappa_completion_enabled = self._as_bool(
            getattr(cfg, "research_kappa_completion_enabled", True)
        )
        _runtime_kappa_min = getattr(self, "kappa_min_observations", None)
        _cfg_target = getattr(cfg, "research_kappa_completion_target", None)
        self.research_kappa_completion_target = required_observation_count(
            kappa_min_observations=_runtime_kappa_min,
            research_target=_cfg_target,
        )
        self.research_kappa_completion_rank_bonus = max(
            0.0, float(getattr(cfg, "research_kappa_completion_rank_bonus", 0.30))
        )
        self.research_kappa_completion_fill_mult = max(
            0.50,
            min(1.0, float(getattr(cfg, "research_kappa_completion_fill_mult", 0.70))),
        )
        self.research_kappa_completion_fill_floor = max(
            0.0, float(getattr(cfg, "research_kappa_completion_fill_floor", 0.10))
        )
        self.research_kappa_completion_relaxed_success_cap = max(
            0, int(getattr(cfg, "research_kappa_completion_relaxed_success_cap", 2))
        )

        # V4.1 Strict: isolate Kappa completion from the normal economic lane.
        # The total attempt cap remains research_candidate_attempt_cap (12 by
        # default). Completion receives a hard sub-budget; the remainder is
        # reserved for normal candidates and cannot be stolen by completion
        # failures.
        requested_completion_attempt_cap = max(
            0, int(getattr(cfg, "research_kappa_completion_attempt_cap", 4))
        )
        self.research_kappa_completion_attempt_cap = min(
            self.research_candidate_attempt_cap,
            requested_completion_attempt_cap,
        )
        self.research_normal_attempt_cap = max(
            0,
            self.research_candidate_attempt_cap
            - self.research_kappa_completion_attempt_cap,
        )
        requested_completion_success_cap = max(
            0, int(getattr(cfg, "research_kappa_completion_success_cap", 2))
        )
        self.research_kappa_completion_success_cap = min(
            int(self.max_mm_books_per_tick),
            requested_completion_success_cap,
        )
        self.research_kappa_completion_relaxed_success_cap = min(
            self.research_kappa_completion_relaxed_success_cap,
            self.research_kappa_completion_success_cap,
        )

        self.research_kappa_completion_recent_pnl_floor = float(
            getattr(cfg, "research_kappa_completion_recent_pnl_floor", -0.01)
        )

        # --------------------------------------------------------------
        # V4.2 Strict: prevent the dominant long-run failure observed in the
        # research logs: maker partial fills create sub-minimum positions,
        # parked dust removes books from the usable completion pool, and Kappa
        # coverage stalls.  V4.2 learns fill QUALITY, not merely any-fill rate.
        # --------------------------------------------------------------
        self.research_actionable_fill_enabled = self._as_bool(
            getattr(cfg, "research_actionable_fill_enabled", True)
        )
        self.research_actionable_fill_min_samples = max(
            1, int(getattr(cfg, "research_actionable_fill_min_samples", 4))
        )
        self.research_actionable_fill_prior_strength = max(
            0.0, float(getattr(cfg, "research_actionable_fill_prior_strength", 6.0))
        )
        self.research_actionable_fill_prior_actionable = max(
            0.0, min(1.0, float(getattr(cfg, "research_actionable_fill_prior_actionable", 0.85)))
        )
        self.research_actionable_fill_rank_weight = max(
            0.0, float(getattr(cfg, "research_actionable_fill_rank_weight", 0.10))
        )
        self.research_dust_risk_rank_penalty = max(
            0.0, float(getattr(cfg, "research_dust_risk_rank_penalty", 0.18))
        )
        self.research_dust_risk_target = max(
            0.0, min(1.0, float(getattr(cfg, "research_dust_risk_target", 0.15)))
        )
        self.research_kappa_one_away_bonus = max(
            0.0, float(getattr(cfg, "research_kappa_one_away_bonus", 0.10))
        )

        # Bounded partial-fill hold: only extend the original completion quote
        # lifetime; never top up exposure. Default cap is 750ms simulation time.
        self.research_partial_fill_hold_enabled = self._as_bool(
            getattr(cfg, "research_partial_fill_hold_enabled", True)
        )
        self.research_partial_fill_hold_one_away_only = self._as_bool(
            getattr(cfg, "research_partial_fill_hold_one_away_only", False)
        )
        self.research_partial_fill_hold_max_ns = max(
            int(self.mm_expiry_period),
            int(getattr(cfg, "research_partial_fill_hold_max_ns", 750_000_000)),
        )
        self.research_partial_fill_hold_min_dust_prob = max(
            0.0, min(1.0, float(getattr(cfg, "research_partial_fill_hold_min_dust_prob", 0.12)))
        )

        # Maker-only is scoped to normal MM / explicit maintenance quote pairs.
        # Inventory-management exits continue to use the inherited fee/risk path.
        self.research_force_mm_post_only = self._as_bool(
            getattr(cfg, "research_force_mm_post_only", True)
        )

        # Adaptive compaction keeps the exact V4.1 exposure theorem unchanged;
        # it only changes which safe candidate is retried and when.
        self.research_dust_compact_adaptive = self._as_bool(
            getattr(cfg, "research_dust_compact_adaptive", True)
        )
        self.research_dust_compact_cooldown_ticks = max(
            1, int(getattr(cfg, "research_dust_compact_cooldown_ticks", 100))
        )
        self.research_dust_compact_max_cooldown_ticks = max(
            self.research_dust_compact_cooldown_ticks,
            int(getattr(cfg, "research_dust_compact_max_cooldown_ticks", 600)),
        )
        self.research_dust_compact_prior_fill = max(
            0.0, min(1.0, float(getattr(cfg, "research_dust_compact_prior_fill", 0.02)))
        )
        self.research_dust_compact_prior_strength = max(
            0.0, float(getattr(cfg, "research_dust_compact_prior_strength", 8.0))
        )

        # V4.3 Phase 3: learn fill hazard; do not use it for live ranking by default.
        self.research_enable_fill_hazard = self._as_bool(
            getattr(cfg, "research_enable_fill_hazard", True)
        )
        self.research_use_fill_hazard_for_policy = self._as_bool(
            getattr(cfg, "research_use_fill_hazard_for_policy", False)
        )
        self.research_fill_hazard_min_samples = max(
            1, int(getattr(cfg, "research_fill_hazard_min_samples", 12))
        )
        self.research_fill_hazard_prior_strength = max(
            0.0, float(getattr(cfg, "research_fill_hazard_prior_strength", 8.0))
        )
        self.research_fill_hazard_prior_any = max(
            0.01, min(0.5, float(getattr(cfg, "research_fill_hazard_prior_any", 0.12)))
        )

        # V4.3 Phase 4: Score-EV ranking. Legacy _global_book_rank remains when off.
        self.research_enable_score_ev = self._as_bool(
            getattr(cfg, "research_enable_score_ev", True)
        )
        self.research_score_ev_min_trading = float(
            getattr(cfg, "research_score_ev_min_trading", 0.0)
        )
        self.research_score_ev_one_away_weight = max(
            0.0, float(getattr(cfg, "research_score_ev_one_away_weight", 0.18))
        )
        self.research_score_ev_two_away_weight = max(
            0.0, float(getattr(cfg, "research_score_ev_two_away_weight", 0.06))
        )
        self.research_score_ev_new_book_weight = max(
            0.0, float(getattr(cfg, "research_score_ev_new_book_weight", 0.0))
        )
        self.research_score_ev_dust_weight = max(
            0.0, float(getattr(cfg, "research_score_ev_dust_weight", 0.25))
        )
        self.research_score_ev_fees_bps = max(
            0.0, float(getattr(cfg, "research_score_ev_fees_bps", 0.5))
        )
        self.research_score_ev_min_fill_samples = max(
            1, int(getattr(cfg, "research_score_ev_min_fill_samples", 8))
        )
        self.research_score_ev_min_markout_samples = max(
            1, int(getattr(cfg, "research_score_ev_min_markout_samples", 8))
        )
        self.research_enable_score_velocity = self._as_bool(
            getattr(cfg, "research_enable_score_velocity", True)
        )
        self.research_score_velocity_weight = max(
            0.0, min(0.50, float(getattr(cfg, "research_score_velocity_weight", 0.08)))
        )

        self.research_enable_realization = self._as_bool(
            getattr(cfg, "research_enable_realization", True)
        )
        self.research_realize_age_ticks = max(
            1, int(getattr(cfg, "research_realize_age_ticks", 8))
        )
        self.research_profit_realize_bps = float(
            getattr(cfg, "research_profit_realize_bps", 2.0)
        )
        self.research_toxic_realize_bps = max(
            0.0, float(getattr(cfg, "research_toxic_realize_bps", 10.0))
        )
        self.RESEARCH_REALIZATION_VERSION = "realization_exit_v2"
        self.research_enable_hybrid_taker = self._as_bool(
            getattr(cfg, "research_enable_hybrid_taker", True)
        )
        self.research_hybrid_min_lock_bps = float(
            getattr(cfg, "research_hybrid_min_lock_bps", 1.0)
        )
        self.research_hybrid_maker_ev_gap_bps = max(
            0.0, float(getattr(cfg, "research_hybrid_maker_ev_gap_bps", 0.50))
        )
        self.research_hybrid_stale_age_ticks = max(
            1, int(getattr(cfg, "research_hybrid_stale_age_ticks", 16))
        )
        self.research_hybrid_min_maker_fill = max(
            0.0, min(1.0, float(getattr(cfg, "research_hybrid_min_maker_fill", 0.15)))
        )
        self.research_hybrid_partial_frac_cap = max(
            0.10, min(1.0, float(getattr(cfg, "research_hybrid_partial_frac_cap", 0.90)))
        )
        self.research_taker_net_floor_bps = float(
            getattr(cfg, "research_taker_net_floor_bps", 0.0)
        )
        self._research_ladder_bands = clamp_ladder_bands(
            getattr(cfg, "research_ladder_passive_max", 0.25),
            getattr(cfg, "research_ladder_competitive_max", 0.50),
            getattr(cfg, "research_ladder_aggressive_max", 0.70),
        )
        self.research_ladder_passive_max = self._research_ladder_bands.passive_max
        self.research_ladder_competitive_max = self._research_ladder_bands.competitive_max
        self.research_ladder_aggressive_max = self._research_ladder_bands.aggressive_max

        self.research_enable_inventory_state_v2 = self._as_bool(
            getattr(cfg, "research_enable_inventory_state_v2", True)
        )
        self.research_enable_same_side_suppression = self._as_bool(
            getattr(cfg, "research_enable_same_side_suppression", True)
        )
        self.research_enable_entry_size = self._as_bool(
            getattr(cfg, "research_enable_entry_size", True)
        )
        self.research_enable_min_order_admission = self._as_bool(
            getattr(cfg, "research_enable_min_order_admission", True)
        )
        self.research_min_order_tolerance = clamp_min_order_tolerance(
            getattr(cfg, "research_min_order_tolerance", None)
        )
        self.research_near_safe_enabled = self._as_bool(
            getattr(cfg, "research_near_safe_enabled", True)
        )
        self.research_near_safe_max_inventory_risk = max(
            0.0,
            min(1.0, float(getattr(cfg, "research_near_safe_max_inventory_risk", 0.35))),
        )
        self.research_near_safe_min_headroom = max(
            0.0,
            min(1.0, float(getattr(cfg, "research_near_safe_min_headroom", 0.25))),
        )
        self.research_near_safe_min_ev = float(
            getattr(cfg, "research_near_safe_min_ev", 0.0)
        )
        self.RESEARCH_ENTRY_SIZE_VERSION = "entry_size_v4_12_4_completion_exact_min"
        self.RESEARCH_MIN_ORDER_ADMISSION_VERSION = "min_order_admission_v3_two_away_exact_min"

        self.research_enable_quote_hysteresis = self._as_bool(
            getattr(cfg, "research_enable_quote_hysteresis", True)
        )
        self.research_hysteresis_min_price_ticks = max(
            0.25, float(getattr(cfg, "research_hysteresis_min_price_ticks", 2.0))
        )
        self.research_hysteresis_ev_threshold = max(
            0.0, float(getattr(cfg, "research_hysteresis_ev_threshold", 0.06))
        )
        self.RESEARCH_CHURN_VERSION = "quote_churn_v1"
        self.research_enable_adaptive_ttl = self._as_bool(
            getattr(cfg, "research_enable_adaptive_ttl", True)
        )
        baseline_ttl_ms = sim_delta_ms(0, int(getattr(self, "mm_expiry_period", 500_000_000))) or 500.0
        self.research_ttl_min_ms = max(
            50.0, float(getattr(cfg, "research_ttl_min_ms", 200.0))
        )
        self.research_ttl_max_ms = max(
            self.research_ttl_min_ms,
            float(getattr(cfg, "research_ttl_max_ms", max(800.0, baseline_ttl_ms))),
        )
        self.research_enable_dust_escape = self._as_bool(
            getattr(cfg, "research_enable_dust_escape", False)
        )
        self.research_dust_escape_min_age_ticks = max(
            1, int(getattr(cfg, "research_dust_escape_min_age_ticks", 400))
        )
        self.research_dust_escape_cost_bps = max(
            0.0, float(getattr(cfg, "research_dust_escape_cost_bps", 2.5))
        )
        self.research_enable_dust_economics = self._as_bool(
            getattr(cfg, "research_enable_dust_economics", True)
        )
        self.research_enable_dust_prevent = self._as_bool(
            getattr(cfg, "research_enable_dust_prevent", True)
        )
        self.research_dust_tiny_fraction = max(
            0.0,
            min(1.0, float(getattr(cfg, "research_dust_tiny_fraction", 0.50))),
        )
        self.research_dust_moderate_age_ticks = max(
            1, int(getattr(cfg, "research_dust_moderate_age_ticks", 400))
        )
        self.research_dust_maker_ev_floor_bps = float(
            getattr(cfg, "research_dust_maker_ev_floor_bps", 0.0)
        )

        self.research_enable_fast_candidate_screen = self._as_bool(
            getattr(cfg, "research_enable_fast_candidate_screen", True)
        )
        self.research_enable_execution_lanes = self._as_bool(
            getattr(cfg, "research_enable_execution_lanes", True)
        )
        self.research_enable_lane_scheduler = self._as_bool(
            getattr(cfg, "research_enable_lane_scheduler", True)
        )
        self.research_enable_aggressive_coverage = self._as_bool(
            getattr(cfg, "research_enable_aggressive_coverage", True)
        )
        self.research_enable_exit_urgency_v2 = self._as_bool(
            getattr(cfg, "research_enable_exit_urgency_v2", True)
        )
        self.research_enable_hybrid_realization_v2 = self._as_bool(
            getattr(cfg, "research_enable_hybrid_realization_v2", True)
        )
        self.research_enable_economic_taker = self._as_bool(
            getattr(cfg, "research_enable_economic_taker", True)
        )
        # V4.5: optimize combined SN79 action utility (PnL + round-trip/Kappa/
        # coverage/velocity value) rather than standalone taker economics only.
        self.research_enable_sn79_action_utility = self._as_bool(
            getattr(cfg, "research_enable_sn79_action_utility", True)
        )
        # V4.8: SCORE / ECONOMIC / RISK taker authorities may bypass maker-rung
        # urgency after their own bounded safety gates pass. The maker ladder is
        # fallback aggressiveness, not the supreme taker veto.
        self.research_enable_score_taker_direct = self._as_bool(
            getattr(cfg, "research_enable_score_taker_direct", True)
        )
        self.research_enable_economic_taker_direct = self._as_bool(
            getattr(cfg, "research_enable_economic_taker_direct", True)
        )
        self.research_economic_direct_max_loss_bps = min(
            0.0, float(getattr(cfg, "research_economic_direct_max_loss_bps", 0.0))
        )
        self.research_enable_risk_taker_direct = self._as_bool(
            getattr(cfg, "research_enable_risk_taker_direct", False)
        )
        # V4.11.2: explicit aggressive positive-EV realization authority.
        # This never subsidizes a losing Taker: the configured net floor is
        # clamped to >= 0 and taking must beat maker WAIT EV.
        self.research_enable_aggressive_positive_ev_taker = self._as_bool(
            getattr(cfg, "research_enable_aggressive_positive_ev_taker", True)
        )
        self.research_aggressive_positive_ev_min_net_bps = max(
            0.0, float(getattr(cfg, "research_aggressive_positive_ev_min_net_bps", 0.0))
        )
        self.research_aggressive_positive_ev_switch_margin_bps = max(
            0.0, float(getattr(cfg, "research_aggressive_positive_ev_switch_margin_bps", 0.50))
        )
        self.research_aggressive_positive_ev_one_away_margin_bps = max(
            0.0, float(getattr(cfg, "research_aggressive_positive_ev_one_away_margin_bps", 0.0))
        )
        self.research_aggressive_positive_ev_failed_exit_count = max(
            1, int(getattr(cfg, "research_aggressive_positive_ev_failed_exit_count", 8))
        )
        self.research_aggressive_positive_ev_min_age_ticks = max(
            1.0, float(getattr(cfg, "research_aggressive_positive_ev_min_age_ticks", 16.0))
        )
        self.research_aggressive_positive_ev_max_maker_fill = max(
            0.0, min(1.0, float(getattr(cfg, "research_aggressive_positive_ev_max_maker_fill", 0.08)))
        )
        self.research_aggressive_positive_ev_min_urgency = max(
            0.0, min(1.0, float(getattr(cfg, "research_aggressive_positive_ev_min_urgency", 0.30)))
        )
        self.research_maker_escalate_failed_exit_count = max(
            2, int(getattr(cfg, "research_maker_escalate_failed_exit_count", 8))
        )
        self.research_one_away_maker_escalate_failed_exit_count = max(
            1, int(getattr(cfg, "research_one_away_maker_escalate_failed_exit_count", 3))
        )
        self.research_risk_direct_max_loss_bps = min(
            0.0, float(getattr(cfg, "research_risk_direct_max_loss_bps", -10.0))
        )
        self.research_risk_direct_min_age_ticks = max(
            1.0, float(getattr(cfg, "research_risk_direct_min_age_ticks", 24.0))
        )
        self.research_risk_direct_failed_exit_count = max(
            1, int(getattr(cfg, "research_risk_direct_failed_exit_count", 3))
        )
        self.research_risk_direct_min_ev_advantage_bps = max(
            0.0, float(getattr(cfg, "research_risk_direct_min_ev_advantage_bps", 1.0))
        )
        self.research_failed_exit_penalty_bps = max(
            0.0, float(getattr(cfg, "research_failed_exit_penalty_bps", 0.75))
        )
        self.research_exit_age_penalty_bps_per_tick = max(
            0.0, float(getattr(cfg, "research_exit_age_penalty_bps_per_tick", 0.03))
        )
        self.research_cancel_before_taker = self._as_bool(
            getattr(cfg, "research_cancel_before_taker", True)
        )
        # V4.12.3: one authoritative actual-price Maker/Taker/Wait exit core.
        self.research_enable_unified_exit = self._as_bool(
            getattr(cfg, "research_enable_unified_exit", True)
        )
        self.research_unified_maker_net_floor_bps = max(
            0.0, float(getattr(cfg, "research_unified_maker_net_floor_bps", 0.0))
        )
        self.research_unified_profit_lock_min_bps = max(
            0.0, float(getattr(cfg, "research_unified_profit_lock_min_bps", 1.0))
        )
        self.research_unified_profit_lock_drawdown_bps = max(
            0.0, float(getattr(cfg, "research_unified_profit_lock_drawdown_bps", 2.0))
        )
        self.research_unified_switch_margin_bps = max(
            0.0, float(getattr(cfg, "research_unified_switch_margin_bps", 0.50))
        )
        self.research_enable_protective_taker = self._as_bool(
            getattr(cfg, "research_enable_protective_taker", True)
        )
        self.research_protective_taker_loss_floor_bps = min(
            0.0, float(getattr(cfg, "research_protective_taker_loss_floor_bps", -2.0))
        )
        self.research_protective_taker_ev_advantage_bps = max(
            0.0, float(getattr(cfg, "research_protective_taker_ev_advantage_bps", 1.0))
        )
        self.research_protective_taker_failed_exits = max(
            1, int(getattr(cfg, "research_protective_taker_failed_exits", 6))
        )
        self.research_protective_taker_min_age_ticks = max(
            1.0, float(getattr(cfg, "research_protective_taker_min_age_ticks", 8.0))
        )
        self.research_protective_taker_adverse_bps = max(
            0.5, float(getattr(cfg, "research_protective_taker_adverse_bps", 2.0))
        )
        # V4.12.6: move bounded protective exits earlier; never widen the -2 bps floor.
        self.research_early_escape_enabled = self._as_bool(
            getattr(cfg, "research_early_escape_enabled", True)
        )
        self.research_early_escape_failed_exits = max(
            1, int(getattr(cfg, "research_early_escape_failed_exits", 3))
        )
        self.research_early_escape_min_age_ticks = max(
            1.0, float(getattr(cfg, "research_early_escape_min_age_ticks", 5.0))
        )
        self.research_early_escape_drawdown_bps = max(
            0.0, float(getattr(cfg, "research_early_escape_drawdown_bps", 1.5))
        )
        self.research_early_escape_floor_headroom_bps = max(
            0.0, float(getattr(cfg, "research_early_escape_floor_headroom_bps", 0.75))
        )
        self.research_early_escape_ev_advantage_bps = max(
            0.0, float(getattr(cfg, "research_early_escape_ev_advantage_bps", 0.50))
        )
        self._research_peak_taker_net_bps = {}
        self._research_unified_exit_last = {}
        self.research_sn79_pnl_scale_bps = max(
            1.0, float(getattr(cfg, "research_sn79_pnl_scale_bps", 8.0))
        )
        self.research_sn79_pnl_weight = max(
            0.0, float(getattr(cfg, "research_sn79_pnl_weight", 1.0))
        )
        self.research_sn79_round_trip_weight = max(
            0.0, float(getattr(cfg, "research_sn79_round_trip_weight", 0.30))
        )
        self.research_sn79_kappa_weight = max(
            0.0, float(getattr(cfg, "research_sn79_kappa_weight", 0.35))
        )
        self.research_sn79_coverage_weight = max(
            0.0, float(getattr(cfg, "research_sn79_coverage_weight", 0.15))
        )
        self.research_sn79_capital_release_weight = max(
            0.0, float(getattr(cfg, "research_sn79_capital_release_weight", 0.15))
        )
        self.research_sn79_risk_reduction_weight = max(
            0.0, float(getattr(cfg, "research_sn79_risk_reduction_weight", 0.20))
        )
        self.research_sn79_velocity_weight = max(
            0.0, float(getattr(cfg, "research_sn79_velocity_weight", 0.25))
        )
        self.research_sn79_downside_weight = max(
            0.0, float(getattr(cfg, "research_sn79_downside_weight", 0.45))
        )
        self.research_sn79_min_utility_margin = max(
            0.0, float(getattr(cfg, "research_sn79_min_utility_margin", 0.03))
        )
        self.research_sn79_max_score_subsidy_loss_bps = min(
            0.0, float(getattr(cfg, "research_sn79_max_score_subsidy_loss_bps", 0.0))
        )
        self.research_sn79_one_away_loss_floor_bps = min(
            0.0, float(getattr(cfg, "research_sn79_one_away_loss_floor_bps", 0.0))
        )
        self.research_sn79_two_away_loss_floor_bps = min(
            0.0, float(getattr(cfg, "research_sn79_two_away_loss_floor_bps", 0.0))
        )
        self.research_sn79_uncovered_loss_floor_bps = min(
            0.0, float(getattr(cfg, "research_sn79_uncovered_loss_floor_bps", 0.0))
        )
        self.research_allow_score_loss_subsidy = self._as_bool(
            getattr(cfg, "research_allow_score_loss_subsidy", False)
        )
        if not self.research_allow_score_loss_subsidy:
            self.research_sn79_max_score_subsidy_loss_bps = 0.0
            self.research_sn79_one_away_loss_floor_bps = 0.0
            self.research_sn79_two_away_loss_floor_bps = 0.0
            self.research_sn79_uncovered_loss_floor_bps = 0.0
        self.research_kappa_lookback_ns = max(
            1,
            int(getattr(cfg, "research_kappa_lookback_ns", getattr(self, "pnl_lookback_ns", 10_800_000_000_000))),
        )
        self.research_kappa_expiry_warning_frac = max(
            0.01, min(0.50, float(getattr(cfg, "research_kappa_expiry_warning_frac", 0.20)))
        )
        self.research_kappa_expiry_rank_bonus = max(
            0.0, float(getattr(cfg, "research_kappa_expiry_rank_bonus", 0.20))
        )
        self.research_entry_recheck_ticks = max(
            1, int(getattr(cfg, "research_entry_recheck_ticks", 20))
        )
        self.research_enable_precise_reduction_qty = self._as_bool(
            getattr(cfg, "research_enable_precise_reduction_qty", True)
        )
        self.research_enable_dust_economic_gate = self._as_bool(
            getattr(cfg, "research_enable_dust_economic_gate", True)
        )
        self.research_enable_authoritative_kappa_state = self._as_bool(
            getattr(cfg, "research_enable_authoritative_kappa_state", True)
        )
        self.research_enable_markout_v2 = self._as_bool(
            getattr(cfg, "research_enable_markout_v2", True)
        )
        self.research_enable_fill_hazard_exit_compare = self._as_bool(
            getattr(cfg, "research_enable_fill_hazard_exit_compare", True)
        )
        _lane_budgets = normalize_lane_budgets(
            coverage_slots=getattr(cfg, "research_coverage_slots", None),
            completion_slots=getattr(cfg, "research_completion_slots", None),
            realization_slots=getattr(cfg, "research_realization_slots", None),
            shared_overflow_slots=getattr(cfg, "research_shared_overflow_slots", None),
        )
        self.research_coverage_slots = int(_lane_budgets.coverage_slots)
        self.research_completion_slots = int(_lane_budgets.completion_slots)
        self.research_realization_slots = int(_lane_budgets.realization_slots)
        self.research_shared_overflow_slots = int(_lane_budgets.shared_overflow_slots)
        self._research_lane_budgets = _lane_budgets
        self.research_candidate_count = clamp_candidate_count(
            getattr(cfg, "research_candidate_count", 10)
        )
        # V4.11 performance mode: concentrate score acquisition into a sticky
        # cohort and make the global candidate count authoritative.
        self.research_cohort_size = max(
            8, min(12, int(getattr(cfg, "research_cohort_size", 8)))
        )
        self.research_cohort_exploration_slots = max(
            0, min(2, int(getattr(cfg, "research_cohort_exploration_slots", 1)))
        )
        # V4.12 performance core: prevent the selected universe from becoming
        # fully occupied by stale inventory. New flat-book acquisition pauses
        # at this cap; existing positions and all reductions remain unrestricted.
        self.research_max_open_books = max(
            2, min(12, int(getattr(cfg, "research_max_open_books", 6)))
        )
        # V4.12.7 final-candidate: scarce acquisition slots should rotate into
        # incomplete Kappa books instead of reopening stable qualified books.
        # Refresh-required qualified books and all existing inventory remain eligible.
        self.research_suppress_qualified_acquisition = self._as_bool(
            getattr(cfg, "research_suppress_qualified_acquisition", True)
        )
        self.research_qualified_suppression_min_incomplete = max(
            1, min(16, int(getattr(cfg, "research_qualified_suppression_min_incomplete", 1)))
        )
        # V4.12.8 score-survival scheduler.  The existing 20% warning horizon
        # remains a visibility window; only the later critical portion may
        # bypass breadth rotation ahead of productive incomplete books.
        self.research_deadline_scheduler_enabled = self._as_bool(
            getattr(cfg, "research_deadline_scheduler_enabled", True)
        )
        self.research_deadline_critical_urgency = max(
            0.05, min(0.95, float(getattr(cfg, "research_deadline_critical_urgency", 0.50)))
        )
        self.research_deadline_rank_bonus = max(
            0.0, float(getattr(cfg, "research_deadline_rank_bonus", 0.25))
        )
        self.research_score_target_books = max(
            1, min(128, int(getattr(cfg, "research_score_target_books", 88)))
        )
        # Conservative Maker-only stale rescue for ONE_AWAY inventory.  It does
        # not widen the Taker floor: after repeated failed Maker exits we may
        # quote down to a tiny bounded Maker net floor to improve completion
        # probability before inventory becomes deeply stranded.
        self.research_stale_maker_rescue_enabled = self._as_bool(
            getattr(cfg, "research_stale_maker_rescue_enabled", True)
        )
        self.research_stale_maker_rescue_failed_exits = max(
            1, int(getattr(cfg, "research_stale_maker_rescue_failed_exits", 4))
        )
        self.research_stale_maker_rescue_critical_failed_exits = max(
            1, int(getattr(cfg, "research_stale_maker_rescue_critical_failed_exits", 1))
        )
        self.research_stale_maker_rescue_floor_bps = max(
            -2.0, min(0.0, float(getattr(cfg, "research_stale_maker_rescue_floor_bps", -1.0)))
        )
        self.research_score_qualified_pnl_floor = float(
            getattr(cfg, "research_score_qualified_pnl_floor", 0.0)
        )
        self.research_score_qualified_kappa_floor = float(
            getattr(cfg, "research_score_qualified_kappa_floor", 0.0)
        )
        self.research_lifecycle_taker_exit_prob = max(
            0.0, min(1.0, float(getattr(cfg, "research_lifecycle_taker_exit_prob", 0.30)))
        )
        self.research_lifecycle_slippage_bps = max(
            0.0, float(getattr(cfg, "research_lifecycle_slippage_bps", 0.75))
        )
        self.research_lifecycle_holding_bps = max(
            0.0, float(getattr(cfg, "research_lifecycle_holding_bps", 0.50))
        )
        self.research_positive_ev_min_order_override = self._as_bool(
            getattr(cfg, "research_positive_ev_min_order_override", False)
        )
        self.research_positive_ev_min_safe_fraction = max(
            0.20, min(0.80, float(getattr(cfg, "research_positive_ev_min_safe_fraction", 0.35)))
        )
        self.research_positive_ev_min_exit_fraction = max(
            0.25, min(1.0, float(getattr(cfg, "research_positive_ev_min_exit_fraction", 0.45)))
        )
        self.research_positive_ev_min_trading_ev = max(
            0.0, float(getattr(cfg, "research_positive_ev_min_trading_ev", 0.05))
        )
        # V4.11.2: ONE_AWAY books get a separate exact-minimum admission path.
        # Soft multiplicative sizing may not veto a hard-safe + positive-EV
        # 0.25 completion clip when modeled exit capacity is near the minimum.
        self.research_one_away_exact_min_enabled = self._as_bool(
            getattr(cfg, "research_one_away_exact_min_enabled", True)
        )
        self.research_one_away_exact_min_ev_bps = max(
            0.0, float(getattr(cfg, "research_one_away_exact_min_ev_bps", 0.0))
        )
        self.research_one_away_exact_min_safe_fraction = max(
            0.40, min(1.0, float(getattr(cfg, "research_one_away_exact_min_safe_fraction", 0.50)))
        )
        self.research_one_away_exact_min_exit_fraction = max(
            0.80, min(1.0, float(getattr(cfg, "research_one_away_exact_min_exit_fraction", 0.90)))
        )
        # V4.12.4: TWO_AWAY (1/3 observations) completion admission is binary.
        # The exchange cannot place the continuous 0.05-ish "safe size" seen in
        # live V4.12.3; it can place 0.25 or nothing.  Preserve the stricter
        # ONE_AWAY path above, but let TWO_AWAY books use one exact minimum clip
        # when the full clip is hard inventory-safe, trading EV is positive,
        # volume headroom is healthy, and modeled exit capacity is non-trivial.
        self.research_two_away_exact_min_enabled = self._as_bool(
            getattr(cfg, "research_two_away_exact_min_enabled", True)
        )
        self.research_two_away_exact_min_ev = max(
            0.0, float(getattr(cfg, "research_two_away_exact_min_ev", 0.0))
        )
        self.research_two_away_exact_min_max_inventory_risk = max(
            0.05, min(0.80, float(getattr(cfg, "research_two_away_exact_min_max_inventory_risk", 0.35)))
        )
        self.research_two_away_exact_min_exit_fraction = max(
            0.05, min(0.80, float(getattr(cfg, "research_two_away_exact_min_exit_fraction", 0.20)))
        )
        self.research_two_away_exact_min_min_headroom = max(
            0.05, min(1.0, float(getattr(cfg, "research_two_away_exact_min_min_headroom", 0.25)))
        )
        self.research_quiet_ttl_ms = max(
            500.0, float(getattr(cfg, "research_quiet_ttl_ms", 1000.0))
        )
        self.research_quote_tighten_mult = max(
            0.70, min(1.0, float(getattr(cfg, "research_quote_tighten_mult", 0.85)))
        )
        self.research_quote_width_floor_mult = max(
            0.70, min(1.0, float(getattr(cfg, "research_quote_width_floor_mult", 0.80)))
        )
        self.research_p95_target_ms = max(
            20.0, float(getattr(cfg, "research_p95_target_ms", P95_TARGET_MS))
        )
        # V4.12.1 execution-conversion pass. QUIET books publish roughly once per
        # simulated second; a 500ms maker exit is live for only half that window.
        # Keep exits alive for most of the publish interval but below one full
        # interval so a stale order expires before the next request can stack it.
        self.research_quiet_exit_ttl_ms = max(
            500.0, min(990.0, float(getattr(cfg, "research_quiet_exit_ttl_ms", 950.0)))
        )
        self.research_one_away_exit_ttl_ms = max(
            self.research_quiet_exit_ttl_ms,
            min(995.0, float(getattr(cfg, "research_one_away_exit_ttl_ms", 975.0))),
        )
        # Tighten flat ONE_AWAY quotes in QUIET markets without crossing our own
        # two-sided maker pair. This changes execution width, not the EV gate.
        self.research_one_away_quiet_width_mult = max(
            0.45, min(0.80, float(getattr(cfg, "research_one_away_quiet_width_mult", 0.60)))
        )
        self.research_one_away_quiet_min_ev = max(
            0.0, float(getattr(cfg, "research_one_away_quiet_min_ev", 0.0))
        )
        self.research_enable_one_away_quiet_tightening = self._as_bool(
            getattr(cfg, "research_enable_one_away_quiet_tightening", True)
        )
        # V4.12.2 touch-aware execution: a flat ONE_AWAY completion quote may be
        # directionally skewed, but it cannot drift arbitrarily far from touch.
        # The cap is symmetric and maker-only; it never crosses the spread.
        self.research_one_away_max_touch_bps = max(
            0.5, min(20.0, float(getattr(cfg, "research_one_away_max_touch_bps", 5.0)))
        )
        # Continuous distance prior for sparse fill-hazard cells. The research
        # model blends this with the legacy estimator until empirical hazard is
        # usable, so 2 bps and 25 bps quotes no longer look equally fillable.
        self.research_fill_distance_decay_bps = max(
            0.5, float(getattr(cfg, "research_fill_distance_decay_bps", 6.0))
        )
        self.research_fill_distance_near_boost = max(
            1.0, min(2.0, float(getattr(cfg, "research_fill_distance_near_boost", 1.35)))
        )
        self.research_fill_distance_floor_mult = max(
            0.02, min(0.50, float(getattr(cfg, "research_fill_distance_floor_mult", 0.10)))
        )
        self.research_fill_fallback_policy_weight = max(
            0.0, min(0.80, float(getattr(cfg, "research_fill_fallback_policy_weight", 0.45)))
        )
        # Local validator-Kappa is expensive and changes only on own realization
        # or rolling expiry. Recompute immediately on a new realization and at a
        # short cadence otherwise; 10 simulated seconds is negligible versus the
        # 3h scoring window while removing repeated hot-path work.
        self.research_local_kappa_refresh_ticks = max(
            1, min(60, int(getattr(cfg, "research_local_kappa_refresh_ticks", 10)))
        )
        self._research_realized_generation = 0
        self._research_local_kappa_cache_generation = -1
        self._research_local_kappa_cache_bucket = -1
        self._research_local_kappa_cache_value = None
        self.RESEARCH_SPEED_VERSION = "response_speed_v3_event_driven_kappa"

        # V4.3 Phase 1: independent market vs score regime. Defaults preserve
        # hard risk gates; they only stop the parent mean-spread STRESSED latch.
        self.research_regime_debounce_ticks = max(
            1, int(getattr(cfg, "research_regime_debounce_ticks", 3))
        )
        self.research_regime_stressed_ratio_enter = max(
            0.05, min(0.95, float(getattr(cfg, "research_regime_stressed_ratio_enter", 0.35)))
        )
        self.research_regime_stressed_ratio_exit = max(
            0.02,
            min(
                self.research_regime_stressed_ratio_enter,
                float(getattr(cfg, "research_regime_stressed_ratio_exit", 0.25)),
            ),
        )
        self.research_regime_toxic_ratio_enter = max(
            self.research_regime_stressed_ratio_enter,
            min(0.99, float(getattr(cfg, "research_regime_toxic_ratio_enter", 0.50))),
        )
        self.research_regime_toxic_ratio_exit = max(
            self.research_regime_stressed_ratio_exit,
            min(
                self.research_regime_toxic_ratio_enter,
                float(getattr(cfg, "research_regime_toxic_ratio_exit", 0.38)),
            ),
        )
        self.research_regime_quiet_trade_rate = max(
            0.0, float(getattr(cfg, "research_regime_quiet_trade_rate", 0.10))
        )
        self.research_regime_liquid_ratio_enter = max(
            0.05, min(0.95, float(getattr(cfg, "research_regime_liquid_ratio_enter", 0.55)))
        )
        self.research_regime_trend_frac_enter = max(
            0.20, min(0.90, float(getattr(cfg, "research_regime_trend_frac_enter", 0.45)))
        )
        self.research_regime_completion_pending_ratio = max(
            0.05, min(0.90, float(getattr(cfg, "research_regime_completion_pending_ratio", 0.20)))
        )
        self._research_regime_thresholds = RegimeV2Thresholds(
            stressed_ratio_enter=self.research_regime_stressed_ratio_enter,
            stressed_ratio_exit=self.research_regime_stressed_ratio_exit,
            toxic_ratio_enter=self.research_regime_toxic_ratio_enter,
            toxic_ratio_exit=self.research_regime_toxic_ratio_exit,
            quiet_trade_rate=self.research_regime_quiet_trade_rate,
            liquid_ratio_enter=self.research_regime_liquid_ratio_enter,
            liquid_ratio_exit=max(0.05, self.research_regime_liquid_ratio_enter - 0.10),
            trend_frac_enter=self.research_regime_trend_frac_enter,
            trend_frac_exit=max(0.15, self.research_regime_trend_frac_enter - 0.10),
            debounce_ticks=self.research_regime_debounce_ticks,
            coverage_inactive_ratio=float(
                getattr(self, "max_inactive_books_ratio", 0.375) or 0.375
            ),
            completion_pending_ratio=self.research_regime_completion_pending_ratio,
            completion_pending_exit=max(
                0.02, self.research_regime_completion_pending_ratio - 0.08
            ),
        )
        self._research_market_debounce = DebounceState("NORMAL", "NORMAL", 0)
        self._research_score_debounce = DebounceState("COVERAGE", "COVERAGE", 0)
        self._research_market_regime = "NORMAL"
        self._research_score_regime = "COVERAGE"
        self._research_quote_store = QuoteLifecycleStore()
        self._research_fill_hazard = FillHazardModel(
            min_samples=self.research_fill_hazard_min_samples,
            prior_strength=self.research_fill_hazard_prior_strength,
            prior_any=self.research_fill_hazard_prior_any,
            distance_decay_bps=self.research_fill_distance_decay_bps,
            distance_near_boost=self.research_fill_distance_near_boost,
            distance_floor_mult=self.research_fill_distance_floor_mult,
            fallback_policy_weight=self.research_fill_fallback_policy_weight,
        )
        self._research_hazard_last: dict[int, dict[str, Any]] = {}
        self._research_score_ev_last: dict[int, Any] = {}
        self._research_markout_by_book: dict[int, dict[str, float]] = {}
        self._research_markout_horizons: dict[int, dict[int, dict[str, float]]] = {}
        self._research_ofi = OfiTracker()
        self._research_ofi_last: dict[int, Any] = {}
        self._research_as_width_mult = 1.0
        self._research_cohort_ids: list[int] = []
        self._research_score_qualified_ids: set[int] = set()
        self._research_lifecycle_cost_last: dict[int, Any] = {}
        self._research_hysteresis_holds = 0
        self._research_hysteresis_replaces = 0
        self._research_ttl_stale_skips = 0
        self._research_dust_escape_attempts = 0
        self._research_dust_escape_orders = 0
        self._research_dust_econ_last: dict[int, Any] = {}
        self._research_dust_prevent_skips = 0
        self._research_timing: dict[str, float | int] = {}
        self._research_feature_cache = FeatureCache()
        self._research_response_ms: list[float] = []
        self._research_latency_ewma_ms: float | None = None
        self._research_book_universe_ids: set[int] = set()
        self._research_microprice_px: dict[int, float] = {}
        self._research_book_micro: dict[int, dict[str, Any]] = {}
        self._research_last_selection = None
        self._research_last_predictions: dict[int, Any] = {}
        self._research_markout_eval_ms = 0.0
        self._research_quotes_registered = 0
        self._research_fills_classified = 0
        self._research_markouts_emitted = 0

        self.research_run_id = time.strftime("%Y%m%d_%H%M%S", time.localtime())

        self._research_stress_spread_bps = self.research_stress_fallback_bps
        self._research_toxic_spread_bps = max(
            self.research_toxic_fallback_bps,
            self._research_stress_spread_bps + self.research_toxic_gap_bps,
        )
        self._research_exchange_min_order_size = max(
            0.0, float(getattr(self, "min_order_size", 0.0) or 0.0)
        )
        self._research_bootstrap_active = False
        self._research_position_tick_seen: dict[int, int] = {}
        self._research_round_trip_closes = 0
        self._research_velocity = VelocityState()
        self._research_position_opens = 0
        self._research_position_reductions = 0
        self._research_dust_blocks = 0
        self._research_round_trip_samples_by_book: dict[int, int] = {}
        self._research_realized_observations_by_book: dict[int, int] = {}
        self._research_session_identity: SessionIdentity | None = None
        self._research_session_obs_high_water: dict[int, int] = {}
        self._research_session_persist_enabled = self._as_bool(
            getattr(cfg, "research_session_persist_enabled", True)
        )
        self._research_session_save_every_n = max(
            1, int(getattr(cfg, "research_session_save_every_n", 100))
        )
        self._research_session_last_save_tick = -1
        self._research_transition_quarantine_ticks = max(
            1, int(getattr(cfg, "research_transition_quarantine_ticks", DEFAULT_TRANSITION_QUARANTINE_TICKS))
        )
        self._research_transition_quarantine_remaining = 0
        self._research_last_transition_reason: str | None = None
        self._research_last_realization_ts: dict[int, int] = {}
        self._research_realization_last: dict[int, Any] = {}
        self._research_exit_attempts: dict[int, dict[str, Any]] = {}
        self._research_taker_authority_counts = {"ECONOMIC": 0, "SCORE": 0, "RISK": 0, "POSITIVE_EV": 0}
        self._research_actual_taker_orders = 0
        self._research_kappa_realization_last: dict[int, Any] = {}
        self._research_inventory_state_last: dict[int, Any] = {}
        self._research_same_side_last: dict[int, Any] = {}
        self._research_entry_admission_cache: dict[int, dict[str, Any]] = {}
        self._research_kappa_roll_cache_key = None
        self._research_kappa_roll_ts_cache: dict[int, tuple[int, ...]] = {}
        self._research_kappa_roll_count_cache: dict[int, int] = {}
        self._research_kappa_roll_next_expiry_ts: int | None = None
        self._research_local_kappa_source_sig = None
        self._research_local_kappa_next_expiry_ts: int | None = None
        self._research_skip_summary_counts: dict[str, int] = {}
        # Rolling Kappa timestamps are the decision authority.  Lifetime/session
        # counters remain diagnostic only.  Persist these timestamps so a miner
        # reload cannot make scheduler/lanes forget still-valid observations.
        self._research_persisted_observation_timestamps: dict[int, list[int]] = {}
        self._research_active_inventory_policy = None
        self._research_entry_size_last: dict[int, Any] = {}
        self._research_volume_cap_book_id: int | None = None
        self._research_volume_cap_state = None
        self._research_last_miner_wealth: float | None = None
        self._research_last_volume_cap_quote = 0.0
        self._research_volume_cap_blocks = 0
        self._research_sim_start_ts: int | None = None
        self._research_last_sim_ts: int | None = None
        self._research_parked_dust: dict[int, dict[str, Any]] = {}
        self._research_dust_entries = 0
        self._research_dust_releases = 0
        self._research_dust_heartbeats = 0
        self._research_dust_compact_ids_this_tick: set[int] = set()
        self._research_dust_compact_attempts = 0
        self._research_dust_compact_orders = 0
        self._research_dust_compact_fills = 0
        self._research_dust_compact_active: dict[int, int] = {}
        self._research_volume_decimals = 8
        self._research_backfill_active = False
        self._research_quote_success_cap = 0
        self._research_quote_successes = 0
        self._research_quote_attempts = 0
        self._research_normal_quote_attempts = 0
        self._research_normal_quote_successes = 0
        self._research_completion_relaxed_successes = 0
        self._research_completion_relaxed_attempts = 0
        self._research_completion_quote_attempts = 0
        self._research_completion_quote_successes = 0
        self._research_completion_attempt_cap_hits = 0
        self._research_completion_success_cap_hits = 0
        self._research_normal_attempt_cap_hits = 0
        self._research_lane_used = {lane: 0 for lane in EXEC_LANES}
        self._research_lane_overflow_used = 0
        self._research_lane_cap_hits = {lane: 0 for lane in EXEC_LANES}
        self._research_last_lanes = None
        self._research_aggressive_context: dict[int, dict[str, Any]] = {}

        # V4.2 fill-quality and bounded liveness state.
        self._research_actionable_by_book: dict[int, dict[str, float | int]] = {}
        self._research_actionable_by_bucket: dict[tuple[int, str, int], dict[str, float | int]] = {}
        self._research_actionable_global_by_bucket: dict[tuple[str, int], dict[str, float | int]] = {}
        self._research_actionable_quote_count = 0
        self._research_actionable_maker_fills = 0
        self._research_actionable_fills = 0
        self._research_dust_maker_fills = 0
        self._research_partial_fill_hold_candidates = 0
        self._research_partial_fill_hold_quoted = 0
        self._research_forced_maker_quote_books = 0
        self._research_force_maker_context = False
        self._research_dust_compact_learning: dict[int, dict[str, float | int]] = {}
        self._research_dust_compact_cooldown_skips = 0

        self.research_enabled = self._env_bool(
            "STRATEGY1_RESEARCH", self._as_bool(getattr(cfg, "research_enabled", True))
        )
        self.research_every_n = max(1, self._env_int(
            "STRATEGY1_RESEARCH_EVERY_N", int(getattr(cfg, "research_every_n", 1))
        ))
        self.research_book_id = self._env_int(
            "STRATEGY1_RESEARCH_BOOK", int(getattr(cfg, "research_book_id", -1))
        )
        self.research_console = self._env_bool(
            "STRATEGY1_RESEARCH_CONSOLE", self._as_bool(getattr(cfg, "research_console", True))
        )
        # V4.12.1 keeps full JSONL evidence but makes the human/PM2 console a
        # compact operational stream. Expensive analysis remains possible from
        # JSONL without drowning live monitoring in 128-book NO_PREDICTION rows.
        self.research_compact_console = self._as_bool(
            getattr(cfg, "research_compact_console", True)
        )
        self.research_jsonl = self._env_bool(
            "STRATEGY1_RESEARCH_JSONL", self._as_bool(getattr(cfg, "research_jsonl", True))
        )
        self.research_queue_size = max(256, self._env_int(
            "STRATEGY1_RESEARCH_QUEUE", int(getattr(cfg, "research_queue_size", 8192))
        ))
        env_dir = os.getenv("STRATEGY1_RESEARCH_DIR", "").strip()
        configured = str(getattr(cfg, "research_output_dir", "") or "")
        self.research_output_dir = env_dir or configured or os.path.join(
            self.output_dir, "strategy1_research"
        )
        self._research_session_dir = str(
            getattr(cfg, "research_session_dir", "") or self.research_output_dir
        )

        self._rq = queue.Queue(maxsize=self.research_queue_size)
        self._rstop = threading.Event()
        self._research_output_file = None
        if self.research_enabled and self.research_jsonl:
            try:
                os.makedirs(self.research_output_dir, exist_ok=True)
                filename = (
                    f"strategy1_research_agent_{self.uid}_{self.research_run_id}.jsonl"
                    if self.research_rotate_jsonl
                    else f"strategy1_research_agent_{self.uid}.jsonl"
                )
                path = os.path.join(self.research_output_dir, filename)
                self._research_output_file = path
                self._rfile = open(path, "a", encoding="utf-8", buffering=1)
            except OSError as exc:
                print(f"[S1R_ERROR] stage=init_jsonl error={self._short(exc)}", flush=True)

        if self.research_enabled:
            self._rworker = threading.Thread(
                target=self._writer_loop,
                name=f"s1r-{getattr(self, 'uid', 'agent')}",
                daemon=True,
            )
            self._rworker.start()
            atexit.register(self._shutdown_research)

        self._research_ready = True
        for record in self._research_early:
            self._enqueue(record)
        self._research_early.clear()
        self._enqueue({
            "type": "RESEARCH_CONFIG",
            "agent_id": getattr(self, "uid", None),
            "wall_time_ns": time.time_ns(),
            "enabled": self.research_enabled,
            "every_n": self.research_every_n,
            "book_filter": self.research_book_id,
            "console": self.research_console,
            "compact_console": bool(getattr(self, "research_compact_console", True)),
            "jsonl": self.research_jsonl,
            "queue_size": self.research_queue_size,
            "output_dir": self.research_output_dir,
            "policy_version": self.RESEARCH_POLICY_VERSION,
            "kappa_scheduler_version": self.RESEARCH_KAPPA_SCHEDULER_VERSION,
            "kappa_state_version": self.RESEARCH_KAPPA_STATE_VERSION,
            "kappa_realization_version": self.RESEARCH_KAPPA_REALIZATION_VERSION,
            "markout_version": self.RESEARCH_MARKOUT_VERSION,
            "lanes_version": self.RESEARCH_LANES_VERSION,
            "score_acquisition_version": self.RESEARCH_SCORE_ACQUISITION_VERSION,
            "lifecycle_entry_version": self.RESEARCH_LIFECYCLE_ENTRY_VERSION,
            "candidate_count": int(getattr(self, "research_candidate_count", 10)),
            "cohort_size": int(getattr(self, "research_cohort_size", 8)),
            "max_open_books": int(getattr(self, "research_max_open_books", 6)),
            "suppress_qualified_acquisition": bool(getattr(self, "research_suppress_qualified_acquisition", True)),
            "qualified_suppression_min_incomplete": int(getattr(self, "research_qualified_suppression_min_incomplete", 1)),
            "deadline_scheduler": bool(getattr(self, "research_deadline_scheduler_enabled", True)),
            "deadline_critical_urgency": float(getattr(self, "research_deadline_critical_urgency", 0.50)),
            "deadline_rank_bonus": float(getattr(self, "research_deadline_rank_bonus", 0.25)),
            "score_target_books": int(getattr(self, "research_score_target_books", 88)),
            "stale_maker_rescue": bool(getattr(self, "research_stale_maker_rescue_enabled", True)),
            "stale_maker_rescue_failed_exits": int(getattr(self, "research_stale_maker_rescue_failed_exits", 4)),
            "stale_maker_rescue_floor_bps": float(getattr(self, "research_stale_maker_rescue_floor_bps", -1.0)),
            "cohort_exploration_slots": int(getattr(self, "research_cohort_exploration_slots", 1)),
            "positive_ev_min_order_override": bool(getattr(self, "research_positive_ev_min_order_override", False)),
            "one_away_exact_min_enabled": bool(getattr(self, "research_one_away_exact_min_enabled", True)),
            "one_away_exact_min_ev_bps": float(getattr(self, "research_one_away_exact_min_ev_bps", 0.0)),
            "one_away_exact_min_safe_fraction": float(getattr(self, "research_one_away_exact_min_safe_fraction", 0.50)),
            "one_away_exact_min_exit_fraction": float(getattr(self, "research_one_away_exact_min_exit_fraction", 0.90)),
            "two_away_exact_min_enabled": bool(getattr(self, "research_two_away_exact_min_enabled", True)),
            "two_away_exact_min_ev": float(getattr(self, "research_two_away_exact_min_ev", 0.0)),
            "two_away_exact_min_max_inventory_risk": float(getattr(self, "research_two_away_exact_min_max_inventory_risk", 0.35)),
            "two_away_exact_min_exit_fraction": float(getattr(self, "research_two_away_exact_min_exit_fraction", 0.20)),
            "two_away_exact_min_min_headroom": float(getattr(self, "research_two_away_exact_min_min_headroom", 0.25)),
            "enable_aggressive_positive_ev_taker": bool(getattr(self, "research_enable_aggressive_positive_ev_taker", True)),
            "aggressive_positive_ev_min_net_bps": float(getattr(self, "research_aggressive_positive_ev_min_net_bps", 0.0)),
            "aggressive_positive_ev_switch_margin_bps": float(getattr(self, "research_aggressive_positive_ev_switch_margin_bps", 0.50)),
            "aggressive_positive_ev_one_away_margin_bps": float(getattr(self, "research_aggressive_positive_ev_one_away_margin_bps", 0.0)),
            "aggressive_positive_ev_failed_exit_count": int(getattr(self, "research_aggressive_positive_ev_failed_exit_count", 8)),
            "aggressive_positive_ev_min_age_ticks": float(getattr(self, "research_aggressive_positive_ev_min_age_ticks", 16.0)),
            "aggressive_positive_ev_max_maker_fill": float(getattr(self, "research_aggressive_positive_ev_max_maker_fill", 0.08)),
            "aggressive_positive_ev_min_urgency": float(getattr(self, "research_aggressive_positive_ev_min_urgency", 0.30)),
            "quiet_ttl_ms": float(getattr(self, "research_quiet_ttl_ms", 1000.0)),
            "quiet_exit_ttl_ms": float(getattr(self, "research_quiet_exit_ttl_ms", 950.0)),
            "one_away_exit_ttl_ms": float(getattr(self, "research_one_away_exit_ttl_ms", 975.0)),
            "one_away_quiet_tightening": bool(getattr(self, "research_enable_one_away_quiet_tightening", True)),
            "one_away_quiet_width_mult": float(getattr(self, "research_one_away_quiet_width_mult", 0.60)),
            "one_away_max_touch_bps": float(getattr(self, "research_one_away_max_touch_bps", 5.0)),
            "fill_distance_decay_bps": float(getattr(self, "research_fill_distance_decay_bps", 6.0)),
            "fill_distance_near_boost": float(getattr(self, "research_fill_distance_near_boost", 1.35)),
            "fill_distance_floor_mult": float(getattr(self, "research_fill_distance_floor_mult", 0.10)),
            "fill_fallback_policy_weight": float(getattr(self, "research_fill_fallback_policy_weight", 0.45)),
            "local_kappa_refresh_ticks": int(getattr(self, "research_local_kappa_refresh_ticks", 10)),
            "score_acquisition_regimes": "COVERAGE,COMPLETION",
            "score_acquisition_selected_only": True,
            "session_schema": self.RESEARCH_SESSION_SCHEMA,
            "session_persist": self._research_session_persist_enabled,
            "transition_quarantine_ticks": self._research_transition_quarantine_ticks,
            "fix_global_stress": self.research_fix_global_stress,
            "neutral_fallback": self.research_neutral_fallback,
            "adaptive_spread_thresholds": self.research_adaptive_spread_thresholds,
            "stress_percentile": self.research_stress_percentile,
            "toxic_percentile": self.research_toxic_percentile,
            "inactive_bootstrap": self.research_inactive_bootstrap,
            "trade_global_stress": self.research_trade_global_stress,
            "sync_min_order": self.research_sync_min_order,
            "promote_min_order": self.research_promote_min_order,
            "bootstrap_dead_as_mm": self.research_bootstrap_dead_as_mm,
            "fix_inventory_util": self.research_fix_inventory_util,
            "fix_quote_reservation": self.research_fix_quote_reservation,
            "bootstrap_manage_min_clip": self.research_bootstrap_manage_min_clip,
            "bootstrap_force_close_ticks": self.research_bootstrap_force_close_ticks,
            "legacy_force_close_min_bps": self.research_bootstrap_force_close_min_bps,
            "legacy_hard_close_ticks": self.research_bootstrap_hard_close_ticks,
            "aggressive_close_touch_gate": self.research_aggressive_close_touch_gate,
            "aggressive_close_fee_buffer_bps": self.research_aggressive_close_fee_buffer_bps,
            "aggressive_close_min_net_bps": self.research_aggressive_close_min_net_bps,
            "candidate_backfill": self.research_candidate_backfill,
            "candidate_attempt_cap": self.research_candidate_attempt_cap,
            "toxic_pnl_min_samples": self.research_toxic_pnl_min_samples,
            "toxic_pnl_hard_floor": self.research_toxic_pnl_hard_floor,
            "yellow_sparse_active": self.research_yellow_sparse_active,
            "green_sparse_active": self.research_green_sparse_active,
            "dust_safe_close": self.research_dust_safe_close,
            "dust_park_enabled": self.research_dust_park_enabled,
            "dust_heartbeat_ticks": self.research_dust_heartbeat_ticks,
            "dust_warn_ticks": self.research_dust_warn_ticks,
            "dust_compact_enabled": self.research_dust_compact_enabled,
            "dust_compact_min_fraction": self.research_dust_compact_min_fraction,
            "dust_compact_books_per_tick": self.research_dust_compact_books_per_tick,
            "kappa_completion_enabled": self.research_kappa_completion_enabled,
            "kappa_completion_target": self.research_kappa_completion_target,
            "kappa_completion_rank_bonus": self.research_kappa_completion_rank_bonus,
            "kappa_completion_fill_mult": self.research_kappa_completion_fill_mult,
            "kappa_completion_fill_floor": self.research_kappa_completion_fill_floor,
            "kappa_completion_relaxed_success_cap": self.research_kappa_completion_relaxed_success_cap,
            "kappa_completion_attempt_cap": self.research_kappa_completion_attempt_cap,
            "kappa_completion_success_cap": self.research_kappa_completion_success_cap,
            "normal_attempt_cap": self.research_normal_attempt_cap,
            "kappa_completion_recent_pnl_floor": self.research_kappa_completion_recent_pnl_floor,
            "actionable_fill_enabled": self.research_actionable_fill_enabled,
            "actionable_fill_min_samples": self.research_actionable_fill_min_samples,
            "actionable_fill_prior_strength": self.research_actionable_fill_prior_strength,
            "actionable_fill_prior_actionable": self.research_actionable_fill_prior_actionable,
            "actionable_fill_rank_weight": self.research_actionable_fill_rank_weight,
            "dust_risk_rank_penalty": self.research_dust_risk_rank_penalty,
            "dust_risk_target": self.research_dust_risk_target,
            "kappa_one_away_bonus": self.research_kappa_one_away_bonus,
            "partial_fill_hold_enabled": self.research_partial_fill_hold_enabled,
            "partial_fill_hold_one_away_only": self.research_partial_fill_hold_one_away_only,
            "partial_fill_hold_max_ns": self.research_partial_fill_hold_max_ns,
            "partial_fill_hold_min_dust_prob": self.research_partial_fill_hold_min_dust_prob,
            "force_mm_post_only": self.research_force_mm_post_only,
            "dust_compact_adaptive": self.research_dust_compact_adaptive,
            "dust_compact_cooldown_ticks": self.research_dust_compact_cooldown_ticks,
            "dust_compact_max_cooldown_ticks": self.research_dust_compact_max_cooldown_ticks,
            "dust_compact_prior_fill": self.research_dust_compact_prior_fill,
            "dust_compact_prior_strength": self.research_dust_compact_prior_strength,
            "regime_debounce_ticks": self.research_regime_debounce_ticks,
            "regime_stressed_ratio_enter": self.research_regime_stressed_ratio_enter,
            "regime_completion_pending_ratio": self.research_regime_completion_pending_ratio,
            "quote_lifecycle": True,
            "markout_horizons_ms": (100, 250, 500, 1000),
            "enable_fill_hazard": self.research_enable_fill_hazard,
            "use_fill_hazard_for_policy": self.research_use_fill_hazard_for_policy,
            "fill_hazard_min_samples": self.research_fill_hazard_min_samples,
            "fill_hazard_prior_any": self.research_fill_hazard_prior_any,
            "enable_score_ev": self.research_enable_score_ev,
            "enable_realization": self.research_enable_realization,
            "enable_hybrid_taker": self.research_enable_hybrid_taker,
            "hybrid_version": getattr(self, "RESEARCH_HYBRID_VERSION", "hybrid_maker_taker_v3"),
            "exit_hazard_ev_version": getattr(
                self, "RESEARCH_EXIT_HAZARD_EV_VERSION", EXIT_HAZARD_EV_VERSION
            ),
            "hybrid_min_lock_bps": self.research_hybrid_min_lock_bps,
            "hybrid_maker_ev_gap_bps": self.research_hybrid_maker_ev_gap_bps,
            "hybrid_stale_age_ticks": self.research_hybrid_stale_age_ticks,
            "hybrid_min_maker_fill": self.research_hybrid_min_maker_fill,
            "ladder_version": self.RESEARCH_LADDER_VERSION,
            "ladder_passive_max": self.research_ladder_passive_max,
            "ladder_competitive_max": self.research_ladder_competitive_max,
            "ladder_aggressive_max": self.research_ladder_aggressive_max,
            "taker_econ_version": self.RESEARCH_TAKER_ECON_VERSION,
            "taker_net_floor_bps": self.research_taker_net_floor_bps,
            "enable_sn79_action_utility": self.research_enable_sn79_action_utility,
            "sn79_pnl_scale_bps": self.research_sn79_pnl_scale_bps,
            "sn79_pnl_weight": self.research_sn79_pnl_weight,
            "sn79_round_trip_weight": self.research_sn79_round_trip_weight,
            "sn79_kappa_weight": self.research_sn79_kappa_weight,
            "sn79_coverage_weight": self.research_sn79_coverage_weight,
            "sn79_capital_release_weight": self.research_sn79_capital_release_weight,
            "sn79_risk_reduction_weight": self.research_sn79_risk_reduction_weight,
            "sn79_velocity_weight": self.research_sn79_velocity_weight,
            "sn79_downside_weight": self.research_sn79_downside_weight,
            "sn79_min_utility_margin": self.research_sn79_min_utility_margin,
            "sn79_max_score_subsidy_loss_bps": self.research_sn79_max_score_subsidy_loss_bps,
            "sn79_one_away_loss_floor_bps": self.research_sn79_one_away_loss_floor_bps,
            "sn79_two_away_loss_floor_bps": self.research_sn79_two_away_loss_floor_bps,
            "sn79_uncovered_loss_floor_bps": self.research_sn79_uncovered_loss_floor_bps,
            "enable_score_taker_direct": int(bool(self.research_enable_score_taker_direct)),
            "enable_economic_taker_direct": int(bool(self.research_enable_economic_taker_direct)),
            "economic_direct_max_loss_bps": self.research_economic_direct_max_loss_bps,
            "enable_risk_taker_direct": int(bool(self.research_enable_risk_taker_direct)),
            "risk_direct_max_loss_bps": self.research_risk_direct_max_loss_bps,
            "risk_direct_min_age_ticks": self.research_risk_direct_min_age_ticks,
            "risk_direct_failed_exit_count": self.research_risk_direct_failed_exit_count,
            "risk_direct_min_ev_advantage_bps": self.research_risk_direct_min_ev_advantage_bps,
            "failed_exit_penalty_bps": self.research_failed_exit_penalty_bps,
            "exit_age_penalty_bps_per_tick": self.research_exit_age_penalty_bps_per_tick,
            "cancel_before_taker": int(bool(self.research_cancel_before_taker)),
            "exit_qty_version": self.RESEARCH_EXIT_QTY_VERSION,
            "enable_entry_size": self.research_enable_entry_size,
            "enable_inventory_state_v2": self.research_enable_inventory_state_v2,
            "inventory_state_version": self.RESEARCH_INVENTORY_STATE_VERSION,
            "enable_same_side_suppression": self.research_enable_same_side_suppression,
            "same_side_version": self.RESEARCH_SAME_SIDE_VERSION,
            "exit_urgency_version": self.RESEARCH_EXIT_URGENCY_VERSION,
            "enable_min_order_admission": self.research_enable_min_order_admission,
            "min_order_tolerance": self.research_min_order_tolerance,
            "near_safe_enabled": self.research_near_safe_enabled,
            "near_safe_max_inventory_risk": self.research_near_safe_max_inventory_risk,
            "near_safe_min_headroom": self.research_near_safe_min_headroom,
            "near_safe_min_ev": self.research_near_safe_min_ev,
            "realize_age_ticks": self.research_realize_age_ticks,
            "profit_realize_bps": self.research_profit_realize_bps,
            "score_ev_min_trading": self.research_score_ev_min_trading,
            "score_ev_one_away_weight": self.research_score_ev_one_away_weight,
            "required_observation_count": self._research_required_observation_count(),
            "enable_quote_hysteresis": self.research_enable_quote_hysteresis,
            "hysteresis_min_price_ticks": self.research_hysteresis_min_price_ticks,
            "hysteresis_ev_threshold": self.research_hysteresis_ev_threshold,
            "enable_adaptive_ttl": self.research_enable_adaptive_ttl,
            "ttl_min_ms": self.research_ttl_min_ms,
            "ttl_max_ms": self.research_ttl_max_ms,
            "enable_dust_escape": self.research_enable_dust_escape,
            "dust_escape_min_age_ticks": self.research_dust_escape_min_age_ticks,
            "enable_dust_economics": self.research_enable_dust_economics,
            "dust_econ_version": self.RESEARCH_DUST_ECON_VERSION,
            "enable_dust_prevent": self.research_enable_dust_prevent,
            "dust_tiny_fraction": self.research_dust_tiny_fraction,
            "dust_moderate_age_ticks": self.research_dust_moderate_age_ticks,
            "dust_maker_ev_floor_bps": self.research_dust_maker_ev_floor_bps,
            "enable_fast_candidate_screen": self.research_enable_fast_candidate_screen,
            "enable_execution_lanes": self.research_enable_execution_lanes,
            "enable_lane_scheduler": self.research_enable_lane_scheduler,
            "enable_aggressive_coverage": self.research_enable_aggressive_coverage,
            "enable_exit_urgency_v2": self.research_enable_exit_urgency_v2,
            "enable_hybrid_realization_v2": self.research_enable_hybrid_realization_v2,
            "enable_economic_taker": self.research_enable_economic_taker,
            "enable_precise_reduction_qty": self.research_enable_precise_reduction_qty,
            "enable_dust_economic_gate": self.research_enable_dust_economic_gate,
            "enable_authoritative_kappa_state": self.research_enable_authoritative_kappa_state,
            "enable_markout_v2": self.research_enable_markout_v2,
            "enable_fill_hazard_exit_compare": self.research_enable_fill_hazard_exit_compare,
            "velocity_version": getattr(self, "RESEARCH_VELOCITY_VERSION", VELOCITY_VERSION),
            "coverage_slots": self.research_coverage_slots,
            "completion_slots": self.research_completion_slots,
            "realization_slots": self.research_realization_slots,
            "shared_overflow_slots": self.research_shared_overflow_slots,
            "candidate_count": self.research_candidate_count,
            "p95_target_ms": self.research_p95_target_ms,
            "rotate_jsonl": self.research_rotate_jsonl,
            "run_id": self.research_run_id,
            "output_file": self._research_output_file,
        })


    # ------------------------------------------------------------------
    # Research policy fixes
    # ------------------------------------------------------------------

    @staticmethod
    def _percentile(values: list[float], q: float) -> float | None:
        """Small-N linear percentile with no NumPy dependency."""
        if not values:
            return None
        xs = sorted(float(v) for v in values)
        if len(xs) == 1:
            return xs[0]
        pos = (len(xs) - 1) * min(1.0, max(0.0, q))
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac

    @staticmethod
    def _profile_float(profile: Any, name: str) -> float | None:
        try:
            value = getattr(profile, name, None)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _update_spread_thresholds(self, profiles: list[BookProfile]) -> None:
        spreads = [
            value
            for profile in profiles
            if (value := self._profile_float(profile, "spread_bps")) is not None
            and value >= 0.0
        ]

        if (
            self.research_adaptive_spread_thresholds
            and len(spreads) >= self.research_min_profiles_for_adaptive
        ):
            p_stress = self._percentile(spreads, self.research_stress_percentile)
            p_toxic = self._percentile(spreads, self.research_toxic_percentile)
            stress = max(
                self.research_stress_floor_bps,
                p_stress if p_stress is not None else self.research_stress_fallback_bps,
            )
            toxic = max(
                self.research_toxic_floor_bps,
                p_toxic if p_toxic is not None else self.research_toxic_fallback_bps,
                stress + self.research_toxic_gap_bps,
            )
        else:
            stress = self.research_stress_fallback_bps
            toxic = max(
                self.research_toxic_fallback_bps,
                stress + self.research_toxic_gap_bps,
            )

        self._research_stress_spread_bps = float(stress)
        self._research_toxic_spread_bps = float(toxic)

    def _sync_exchange_constraints(
        self,
        state: MarketSimulationStateUpdate,
    ) -> None:
        cfg = getattr(state, "config", None)

        # Quantity precision is needed even if min-size synchronization is
        # disabled, because exact inventory/dust classification must follow the
        # simulator's executable quantity grid.
        try:
            self._research_volume_decimals = max(
                0, int(getattr(cfg, "volumeDecimals", self._research_volume_decimals))
            )
        except (TypeError, ValueError):
            pass

        if not self.research_sync_min_order:
            return

        try:
            state_min = float(getattr(cfg, "min_order_size", 0.0) or 0.0)
        except (TypeError, ValueError):
            state_min = 0.0

        if state_min > 0.0:
            self._research_exchange_min_order_size = state_min
            # Keep inherited helpers aligned with the actual simulator contract.
            self.min_order_size = state_min
        else:
            self._research_exchange_min_order_size = max(
                0.0, float(getattr(self, "min_order_size", 0.0) or 0.0)
            )

    def _execution_flat_epsilon(self) -> float:
        """Half one quantity tick; only sub-half-tick residuals are execution-flat."""
        return max(
            0.5 * (10.0 ** (-int(self._research_volume_decimals))),
            1e-12,
        )

    def _research_current_identity(self, state=None) -> SessionIdentity:
        cfg = getattr(self, "config", None)
        endpoint = ""
        env_key = ""
        if cfg is not None:
            endpoint = str(getattr(cfg, "endpoint", "") or "")
            env_key = str(getattr(cfg, "research_environment_key", "") or "")
        endpoint = endpoint or str(os.getenv("BT_ENDPOINT") or os.getenv("ENDPOINT") or "")
        env_key = env_key or str(os.getenv("STRATEGY1_RESEARCH_ENV") or "")
        netuid = resolve_netuid(
            None if cfg is None else getattr(cfg, "netuid", None),
            os.getenv("NETUID"),
            os.getenv("BT_NETUID"),
        )
        return SessionIdentity(
            simulation_id=extract_simulation_id(state),
            network=infer_network(endpoint=endpoint, environment_key=env_key),
            netuid=netuid,
            schema=int(self.RESEARCH_SESSION_SCHEMA),
        )

    def _research_session_path(self, identity: SessionIdentity) -> str:
        directory = getattr(self, "_research_session_dir", None) or getattr(
            self, "research_output_dir", self.output_dir
        )
        return os.path.join(str(directory), state_filename(identity))

    def _research_read_session(self, identity: SessionIdentity) -> Any:
        path = self._research_session_path(identity)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                return json.loads(handle.read())
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {"schema": "invalid"}

    def _research_clear_session_observations(self) -> None:
        self._research_realized_observations_by_book = {}
        self._research_round_trip_samples_by_book = {}
        self._research_round_trip_closes = 0
        self._research_persisted_observation_timestamps = {}
        self._research_kappa_roll_cache_key = None
        self._research_kappa_roll_ts_cache = {}
        self._research_kappa_roll_count_cache = {}
        self._research_kappa_roll_next_expiry_ts = None
        self._research_local_kappa_source_sig = None
        self._research_local_kappa_next_expiry_ts = None
        self._research_local_kappa_cache_value = None
        self._research_velocity = VelocityState()
        self._research_session_obs_high_water = {}
        self._research_last_realization_ts = {}
        self._research_exit_attempts = {}
        self._research_taker_authority_counts = {"ECONOMIC": 0, "SCORE": 0, "RISK": 0, "POSITIVE_EV": 0}
        self._research_actual_taker_orders = 0
        self._research_sim_start_ts = None

    def _research_apply_session_snapshot(
        self,
        observations: dict[int, int],
        round_trip_samples: dict[int, int],
        round_trip_closes: int,
    ) -> None:
        self._research_realized_observations_by_book = dict(observations)
        self._research_round_trip_samples_by_book = dict(round_trip_samples)
        self._research_round_trip_closes = max(0, int(round_trip_closes))
        self._research_session_obs_high_water = dict(observations)

    @staticmethod
    def _research_sanitize_observation_timestamps(raw) -> dict[int, list[int]]:
        if not isinstance(raw, dict):
            return {}
        out: dict[int, list[int]] = {}
        for raw_book, raw_rows in raw.items():
            try:
                bid = int(raw_book)
            except (TypeError, ValueError):
                continue
            if not isinstance(raw_rows, (list, tuple)):
                continue
            rows: list[int] = []
            for value in raw_rows:
                try:
                    ts = int(value)
                except (TypeError, ValueError):
                    continue
                if ts >= 0:
                    rows.append(ts)
            if rows:
                out[bid] = sorted(set(rows))
        return out

    def _research_restore_observation_timestamps(self, raw_session) -> None:
        raw = raw_session if isinstance(raw_session, dict) else {}
        self._research_persisted_observation_timestamps = (
            self._research_sanitize_observation_timestamps(
                raw.get("rolling_observation_timestamps")
            )
        )
        self._research_kappa_roll_cache_key = None
        self._research_kappa_roll_next_expiry_ts = None
        self._research_local_kappa_source_sig = None
        self._research_local_kappa_next_expiry_ts = None
        self._research_local_kappa_cache_value = None

    def _research_emit_state_reset(self, decision, tick: int | None) -> None:
        fields = format_reset_fields(decision, tick)
        self._emit("STATE_RESET", force=True, **fields)
        print(
            f"[S1R_STATE_RESET] tick={fields.get('tick')} "
            f"reason={fields.get('reason')} "
            f"old_sim_id={fields.get('old_sim_id')} "
            f"new_sim_id={fields.get('new_sim_id')} "
            f"old_obs_total={fields.get('old_obs_total')} "
            f"new_obs_total={fields.get('new_obs_total')}",
            flush=True,
        )

    def _research_in_transition_quarantine(self) -> bool:
        return int(getattr(self, "_research_transition_quarantine_remaining", 0) or 0) > 0

    def _research_reconcile_account_inventory(self) -> int:
        live = reconcile_account_base(getattr(self, "accounts", None))
        positions = getattr(self, "_open_positions", None)
        if not isinstance(positions, dict):
            return len(live)
        positions.clear()
        return len(live)

    def _research_emit_session_transition(
        self,
        *,
        tick: int | None,
        old_sim: str | None,
        new_sim: str | None,
        reason: str,
        quarantine: int,
        inventory_reconciled: int,
    ) -> None:
        fields = format_transition_fields(
            tick=tick,
            old_sim=old_sim,
            new_sim=new_sim,
            reason=reason,
            quarantine=quarantine,
            inventory_reconciled=inventory_reconciled,
        )
        self._emit("SESSION_TRANSITION", force=True, **fields)
        print(
            f"[S1R_SESSION_TRANSITION] tick={fields.get('tick')} "
            f"old_sim={fields.get('old_sim')} "
            f"new_sim={fields.get('new_sim')} "
            f"reason={fields.get('reason')} "
            f"quarantine={fields.get('quarantine')} "
            f"inventory_reconciled={fields.get('inventory_reconciled')}",
            flush=True,
        )

    def _research_apply_session_transition(self, decision, state=None) -> None:
        self._research_clear_session_observations()
        clear_stale_session_runtime(self)
        reconciled = self._research_reconcile_account_inventory()
        ticks = max(1, int(getattr(self, "_research_transition_quarantine_ticks", DEFAULT_TRANSITION_QUARANTINE_TICKS)))
        self._research_transition_quarantine_remaining = ticks
        self._research_last_transition_reason = str(getattr(decision, "reason", "") or "")
        self._research_emit_session_transition(
            tick=int(getattr(self, "_tick", 0) or 0),
            old_sim=getattr(decision, "old_sim_id", None),
            new_sim=getattr(decision, "new_sim_id", None),
            reason=str(getattr(decision, "reason", "") or ""),
            quarantine=ticks,
            inventory_reconciled=reconciled,
        )

    def _research_transition_reconcile_cancels(self, response, state) -> None:
        cancel = getattr(response, "cancel_orders", None)
        if not callable(cancel):
            return
        accounts = getattr(self, "accounts", None) or {}
        try:
            items = accounts.items()
        except AttributeError:
            return
        for book_id, account in items:
            orders = getattr(account, "orders", None) or []
            if not orders:
                continue
            try:
                cancel(book_id=int(book_id))
            except (TypeError, ValueError):
                try:
                    cancel(int(book_id))
                except Exception:
                    continue

    def _research_sync_session(self, state) -> Any:
        current = self._research_current_identity(state)
        bound = getattr(self, "_research_session_identity", None)
        disk = None
        if bound is None and current.simulation_id:
            disk = self._research_read_session(current)
        decision = decide_session(
            current=current,
            bound=bound,
            disk=disk,
            live_observations=getattr(self, "_research_realized_observations_by_book", {}),
            live_round_trip_samples=getattr(self, "_research_round_trip_samples_by_book", {}),
            live_round_trip_closes=int(getattr(self, "_research_round_trip_closes", 0) or 0),
        )
        if decision.action == ACTION_RESET:
            self._research_emit_state_reset(
                decision, int(getattr(self, "_tick", 0) or 0)
            )
            if session_requires_transition_quarantine(decision.action, decision.reason):
                self._research_apply_session_transition(decision, state)
            else:
                self._research_clear_session_observations()
            if current.simulation_id:
                self._research_session_identity = current
                self._research_save_session(force=True)
            else:
                self._research_session_identity = None
            return decision
        if decision.action == ACTION_RESTORE:
            self._research_apply_session_snapshot(
                decision.observations,
                decision.round_trip_samples,
                decision.round_trip_closes,
            )
            # Counts alone are not enough for a rolling 3h Kappa authority.
            # Restore timestamp evidence when present; legacy snapshots without
            # timestamps intentionally fall back to zero recent observations
            # rather than falsely extending expired score credit.
            self._research_restore_observation_timestamps(disk)
        if current.simulation_id:
            self._research_session_identity = current
        self._research_guard_observation_monotonic()
        return decision

    def _research_guard_observation_monotonic(self) -> None:
        live = getattr(self, "_research_realized_observations_by_book", {}) or {}
        high = getattr(self, "_research_session_obs_high_water", {}) or {}
        guarded = enforce_monotonic(high, live)
        if observation_total(guarded) < observation_total(high):
            guarded = dict(high)
        if guarded != live:
            self._research_realized_observations_by_book = guarded
            self._emit(
                "ERROR",
                force=True,
                stage="obs_monotonic",
                tick=getattr(self, "_tick", None),
                old_obs_total=observation_total(high),
                new_obs_total=observation_total(guarded),
            )
        self._research_session_obs_high_water = dict(
            self._research_realized_observations_by_book
        )

    def _research_note_realized_observation(self, book_id: int, timestamp=None) -> None:
        # Lifetime/session count is retained only for diagnostics and migration.
        # The authoritative score/completion state is timestamp-based below.
        previous = getattr(self, "_research_realized_observations_by_book", {}) or {}
        updated = increment_observation(previous, book_id)
        updated = enforce_monotonic(
            getattr(self, "_research_session_obs_high_water", {}),
            updated,
        )
        self._research_realized_observations_by_book = updated
        self._research_session_obs_high_water = dict(updated)
        ts = timestamp
        if ts is None:
            ts = getattr(self, "_research_last_sim_ts", None)
        if ts is None:
            ts = getattr(self, "_tick", None)
        if ts is not None:
            try:
                bid = int(book_id)
                stamp = int(ts)
                self._research_last_realization_ts[bid] = stamp
                # V4.12: do NOT add raw trade-event timestamps to the rolling
                # Kappa authority. realized_pnl_history buckets are the canonical
                # score observations. Event timestamps and PnL-bucket timestamps
                # can differ for the same realization, which previously counted
                # one RT twice (e.g. 0 -> 1 -> 2). Persisted rolling timestamps
                # are refreshed only from the canonical history-derived cache.
                self._research_kappa_roll_cache_key = None
                self._research_kappa_roll_next_expiry_ts = None
                self._research_local_kappa_source_sig = None
            except (TypeError, ValueError):
                pass

    def _research_save_session(self, force: bool = False) -> None:
        if not getattr(self, "_research_session_persist_enabled", True):
            return
        identity = getattr(self, "_research_session_identity", None)
        if identity is None or not identity.simulation_id:
            return
        tick = int(getattr(self, "_tick", 0) or 0)
        every = max(1, int(getattr(self, "_research_session_save_every_n", 100)))
        last = int(getattr(self, "_research_session_last_save_tick", -1))
        if not force and last >= 0 and tick - last < every:
            return
        try:
            os.makedirs(os.path.dirname(self._research_session_path(identity)) or ".", exist_ok=True)
            payload = build_payload(
                identity,
                getattr(self, "_research_realized_observations_by_book", {}),
                getattr(self, "_research_round_trip_samples_by_book", {}),
                int(getattr(self, "_research_round_trip_closes", 0) or 0),
            )
            # Persist the evidence used by the rolling Kappa authority, not just
            # lifetime counts.  This keeps completion/coverage state coherent
            # across miner reloads inside the same simulation.
            self._research_refresh_rolling_kappa_cache()
            payload["rolling_observation_timestamps"] = {
                str(book): [int(ts) for ts in rows]
                for book, rows in sorted(
                    (getattr(self, "_research_kappa_roll_ts_cache", {}) or {}).items()
                )
                if rows
            }
            path = self._research_session_path(identity)
            tmp = path + f".tmp.{os.getpid()}"
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"), sort_keys=True)
            os.replace(tmp, path)
            self._research_session_last_save_tick = tick
        except OSError:
            pass

    def handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        # Session identity is a hard safety dependency. If it cannot be resolved,
        # fail closed into transition quarantine rather than trading on stale
        # inventory/Kappa/session state.
        try:
            self._research_sync_session(state)
        except Exception as exc:
            ticks = max(
                1,
                int(getattr(
                    self,
                    "_research_transition_quarantine_ticks",
                    DEFAULT_TRANSITION_QUARANTINE_TICKS,
                )),
            )
            self._research_transition_quarantine_remaining = max(
                int(getattr(self, "_research_transition_quarantine_remaining", 0) or 0),
                ticks,
            )
            self._research_last_transition_reason = "SESSION_SYNC_ERROR"
            try:
                self._emit(
                    "ERROR",
                    force=True,
                    stage="session_sync",
                    tick=getattr(self, "_tick", None),
                    reason="FAIL_CLOSED_QUARANTINE",
                    error=self._short(exc),
                )
            except Exception:
                pass
        quarantine = self._research_in_transition_quarantine()
        try:
            books = getattr(state, "books", None) or {}
            self._research_book_universe_ids.update(int(bid) for bid in books.keys())
        except Exception:
            pass
        try:
            ts = getattr(state, "timestamp", None)
            if ts is not None:
                self._research_last_sim_ts = int(ts)
                if getattr(self, "_research_sim_start_ts", None) is None:
                    self._research_sim_start_ts = int(ts)
        except (TypeError, ValueError):
            pass
        self._research_skip_summary_counts = {}
        self._research_timing = {
            "screen_ms": 0.0,
            "screen_all_books_ms": 0.0,
            "full_predict_ms": 0.0,
            "ranking_ms": 0.0,
            "selection_ms": 0.0,
            "build_orders_ms": 0.0,
            "logging_ms": 0.0,
            "adaptive_or_research_ms": 0.0,
            "candidate_count": 0,
            "forced_inventory_count": 0,
            "forced_kappa_count": 0,
            "screen_fallback": 0,
            "cache_hits": 0,
            "cache_misses": 0,
        }
        started = time.perf_counter()
        try:
            self._research_evaluate_markouts(state)
        except Exception:
            pass
        self._research_markout_eval_ms = (time.perf_counter() - started) * 1000.0
        if not getattr(self, "debug_enabled", False):
            try:
                notices = (getattr(state, "notices", None) or {}).get(self.uid, []) or []
                now = getattr(state, "timestamp", None)
                for notice in notices:
                    phase = type(notice).__name__.upper()
                    if any(token in phase for token in ("CANCEL", "EXPIRE", "REJECT", "FAIL")):
                        self._research_close_from_notice(notice, now, phase=phase)
            except Exception:
                pass
        response = super().handle(state)
        if quarantine:
            try:
                self._research_transition_reconcile_cancels(response, state)
            except Exception:
                pass
            try:
                self._research_reconcile_account_inventory()
            except Exception:
                pass
            self._research_transition_quarantine_remaining = max(
                0,
                int(getattr(self, "_research_transition_quarantine_remaining", 0) or 0) - 1,
            )
        try:
            self._research_guard_observation_monotonic()
            self._research_save_session(force=False)
        except Exception:
            pass
        total_ms = (time.perf_counter() - started) * 1000.0
        try:
            skip_counts = dict(getattr(self, "_research_skip_summary_counts", {}) or {})
            if skip_counts:
                self._emit(
                    "SKIP_SUMMARY",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    total=sum(skip_counts.values()),
                    reasons=skip_counts,
                )
        except Exception:
            pass
        self._research_record_timing(state, response, total_ms)
        try:
            tick = int(getattr(self, "_tick", 0) or 0)
            every = max(1, int(getattr(self, "research_every_n", 1) or 1))
            if tick <= 1 or tick % every == 0:
                self._research_emit_hybrid_summary(force=True)
        except Exception:
            pass
        return response

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        t0 = time.perf_counter()
        response = super().respond(state)
        self._research_timing["_respond_wall_ms"] = (time.perf_counter() - t0) * 1000.0
        if not getattr(self, "debug_enabled", False):
            try:
                self._research_register_submitted_quotes(response, state)
            except Exception:
                pass
        return response

    def predict_direction(self, book_id: int, book: Book, timestamp: int):
        prev_px = self._research_microprice_px.get(int(book_id))
        forecast = super().predict_direction(book_id, book, timestamp)
        try:
            bid = book.bids[0].price if getattr(book, "bids", None) else None
            ask = book.asks[0].price if getattr(book, "asks", None) else None
            bq = book.bids[0].quantity if getattr(book, "bids", None) else None
            aq = book.asks[0].quantity if getattr(book, "asks", None) else None
            micro_px = actual_microprice(bid, ask, bq, aq)
            if micro_px is not None:
                self._research_microprice_px[int(book_id)] = float(micro_px)
            deep = None
            persist = None
            try:
                deep = self._research_cached_deep_imbalance(int(book_id), book)
            except Exception:
                pass
            try:
                persist = float(self._trade_persistence(int(book_id)))
            except Exception:
                pass
            self._research_book_micro[int(book_id)] = {
                "microprice": micro_px,
                "microprice_delta": (
                    None if prev_px is None or micro_px is None
                    else float(micro_px) - float(prev_px)
                ),
                "deep_imbalance": deep,
                "trade_sign_persistence": persist,
                "momentum": getattr(forecast, "momentum_m", None),
                "trade_imbalance": getattr(forecast, "trade_imbalance", None),
                "imbalance": getattr(forecast, "imbalance", None),
            }
            ofi = self._research_update_ofi(int(book_id), book)
            self._research_book_micro[int(book_id)].update(ofi.as_log())
        except Exception:
            pass
        return forecast

    def _research_cheap_tob(
        self, book, *, book_id: int | None = None,
    ) -> tuple[float | None, float | None, int]:
        cache = getattr(self, "_research_feature_cache", None)
        fingerprint = book_touch_fingerprint(book)
        if cache is not None and book_id is not None:
            hit = cache.lookup_touch(int(book_id), fingerprint)
            if hit is not None:
                return hit.spread_bps, hit.imbalance, hit.trade_events
        spread_bps, imb, ntrade = self._research_compute_cheap_tob(book)
        if cache is not None and book_id is not None:
            cache.store_touch(
                int(book_id),
                fingerprint,
                spread_bps=spread_bps,
                imbalance=imb,
                trade_events=ntrade,
            )
        return spread_bps, imb, ntrade

    def _research_compute_cheap_tob(self, book) -> tuple[float | None, float | None, int]:
        bids = getattr(book, "bids", None) or []
        asks = getattr(book, "asks", None) or []
        if not bids or not asks:
            return None, None, 0
        try:
            bid = float(bids[0].price)
            ask = float(asks[0].price)
            bq = float(getattr(bids[0], "quantity", 0.0) or 0.0)
            aq = float(getattr(asks[0], "quantity", 0.0) or 0.0)
        except (TypeError, ValueError, IndexError, AttributeError):
            return None, None, 0
        mid = 0.5 * (bid + ask)
        spread_bps = ((ask - bid) / mid) * 10_000.0 if mid > 0.0 else None
        denom = bq + aq
        imb = ((bq - aq) / denom) if denom > 0.0 else 0.0
        ntrade = 0
        for event in getattr(book, "events", None) or []:
            etype = getattr(event, "type", None)
            if etype in ("t", "EVENT_TRADE", "ET"):
                ntrade += 1
        return spread_bps, imb, ntrade

    def _research_cached_deep_imbalance(self, book_id: int, book) -> float:
        cache = getattr(self, "_research_feature_cache", None)
        fingerprint = deep_book_fingerprint(book)
        if cache is not None:
            hit = cache.lookup_deep(int(book_id), fingerprint)
            if hit is not None:
                return hit
        value = float(self._compute_l2_l5_imbalance(book))
        if cache is not None:
            cache.store_deep(int(book_id), fingerprint, value)
        return value

    def _research_abs_inventory(self, book_id: int) -> float:
        try:
            snap = self._position_tracker_snapshot(int(book_id))
            return abs(float(getattr(snap, "net_qty", 0.0) or 0.0))
        except Exception:
            return 0.0

    def _research_keep_cheap_state(self, book_id, book, timestamp) -> None:
        """Keep momentum windows warm for books skipped by Stage 2."""
        cache = getattr(self, "_research_feature_cache", None)
        fingerprint = book_touch_fingerprint(book)
        if cache is not None and cache.keep_unchanged(int(book_id), fingerprint):
            return
        try:
            mid = self._book_mid(book)
        except Exception:
            mid = None
        if mid and mid > 0:
            try:
                self._update_direction_accuracy(book_id, mid)
            except Exception:
                pass
            try:
                self._update_momentum(book_id, timestamp, mid)
            except Exception:
                pass
        try:
            self._micro_prev[book_id] = self.microprice_signal(book)
        except Exception:
            pass
        try:
            self._research_update_ofi(int(book_id), book)
        except Exception:
            pass

    def _research_execution_lane_budgets(self):
        return normalize_lane_budgets(
            coverage_slots=getattr(self, "research_coverage_slots", None),
            completion_slots=getattr(self, "research_completion_slots", None),
            realization_slots=getattr(self, "research_realization_slots", None),
            shared_overflow_slots=getattr(self, "research_shared_overflow_slots", None),
        )

    def _research_reset_lane_usage(self) -> None:
        self._research_lane_budgets = self._research_execution_lane_budgets()
        self._research_lane_used = {lane: 0 for lane in EXEC_LANES}
        self._research_lane_overflow_used = 0
        self._research_lane_cap_hits = {lane: 0 for lane in EXEC_LANES}

    def _research_emit_lanes(self, *, stage: str) -> None:
        allocation = getattr(self, "_research_last_lanes", None)
        log = allocation.as_log() if allocation is not None else {}
        used = getattr(self, "_research_lane_used", {}) or {}
        diag = getattr(self, "_research_inventory_lane_diag", {}) or {}
        fields = {
            **log,
            **diag,
            "stage": stage,
            "coverage_exec_used": int(used.get(EXEC_LANE_COVERAGE, 0) or 0),
            "completion_exec_used": int(used.get(EXEC_LANE_COMPLETION, 0) or 0),
            "realization_exec_used": int(used.get(LANE_REALIZATION, 0) or 0),
            "overflow_exec_used": int(getattr(self, "_research_lane_overflow_used", 0) or 0),
        }
        # JSONL telemetry is sufficient here. Avoid synchronous flush=True console
        # I/O twice per request; it can directly inflate miner response latency.
        self._emit("LANES", force=True, tick=getattr(self, "_tick", None), **fields)

    def _research_fast_screen(self, state) -> ScreenResult:
        parked = getattr(self, "_research_parked_dust", {}) or {}
        store = getattr(self, "_research_quote_store", None)
        min_size = float(getattr(self, "_research_exchange_min_order_size", 0.0) or 0.0)
        max_inv = max(float(getattr(self, "max_inventory_base", 1.0) or 1.0), 1e-12)
        toxic_spread = float(getattr(self, "_research_toxic_spread_bps", 40.0) or 40.0)
        toxic_streak = int(getattr(self, "toxic_loss_streak", 4) or 4)
        close_thr = float(getattr(self, "inventory_close_threshold", 0.95) or 0.95)
        flat = self._execution_flat_epsilon()
        mems = getattr(self, "book_memory", {}) or {}
        score_ev_last = getattr(self, "_research_score_ev_last", {}) or {}
        ticks = getattr(self, "_position_ticks", {}) or {}
        pnl_floor = float(getattr(self, "research_kappa_completion_recent_pnl_floor", -0.01))
        rows: list[ScreenBook] = []
        lane_rows: list[LaneBook] = []
        cohort_candidates: list[CohortCandidate] = []
        score_qualified_ids: set[int] = set()
        obs_qualified_count = 0
        expiring_count = 0
        deadline_critical_qualified_count = 0
        deadline_critical_incomplete_count = 0
        min_deadline_ns = None
        one_away_count = 0
        two_away_count = 0
        actual_nonflat = 0
        stale_empty_position_keys = 0
        open_position_keys = getattr(self, "_open_positions", {}) or {}
        for book_id, book in (getattr(state, "books", None) or {}).items():
            bid = int(book_id)
            qty = self._research_abs_inventory(bid)
            # V4.7: FIFO dict membership is historical bookkeeping, not inventory.
            # A flattened defaultdict key must never consume REALIZATION capacity.
            has_inv = qty > flat
            if has_inv:
                actual_nonflat += 1
            elif bid in open_position_keys:
                stale_empty_position_keys += 1
            is_dust = bid in parked or (
                has_inv and min_size > 0.0 and qty + 1e-12 < min_size
            )
            kappa = self._research_kappa_book(bid)
            remaining = kappa.observations_remaining
            samples = kappa.realized_observation_count
            entry_feasible = True
            cached_admission = (getattr(self, "_research_entry_admission_cache", {}) or {}).get(bid)
            if cached_admission and not has_inv:
                age_ticks = int(getattr(self, "_tick", 0) or 0) - int(cached_admission.get("tick", 0) or 0)
                if age_ticks < int(getattr(self, "research_entry_recheck_ticks", 20)):
                    entry_feasible = bool(cached_admission.get("allow", True))
            mem = mems.get(bid)
            if mem is None:
                mem = mems.get(book_id)
            spread_bps, imb, ntrade = self._research_cheap_tob(book, book_id=bid)
            live = False
            if store is not None:
                try:
                    live = (
                        store.live_for_book_side(bid, "buy") is not None
                        or store.live_for_book_side(bid, "sell") is not None
                    )
                except Exception:
                    live = False
            hard = False
            if (qty / max_inv) >= close_thr:
                hard = True
            if mem is not None and int(getattr(mem, "loss_streak", 0) or 0) >= toxic_streak:
                hard = True
            if spread_bps is not None and spread_bps >= toxic_spread:
                hard = True
            fill_rate = float(getattr(mem, "fill_rate", 0.0) or 0.0) if mem is not None else 0.0
            spec = float(mem.specialization_score) if mem is not None else 0.0
            last_alpha = float(getattr(mem, "last_expected_alpha", 0.0) or 0.0) if mem is not None else 0.0
            ev = score_ev_last.get(bid)
            maker_ev = 0.0
            if ev is not None:
                last_alpha = max(last_alpha, float(getattr(ev, "final_score", 0.0) or 0.0))
                maker_ev = float(getattr(ev, "trading_ev", 0.0) or 0.0)
            cheap = cheap_book_score(
                spread_bps=spread_bps,
                trade_events=ntrade,
                top_imbalance=imb,
                fill_rate=fill_rate,
                last_alpha=last_alpha,
                specialization=spec,
            )
            recent = float(getattr(mem, "recent_pnl", 0.0) or 0.0) if mem is not None else 0.0
            age = 0.0
            try:
                age = float((ticks.get(bid) if ticks.get(bid) is not None else ticks.get(book_id)) or 0)
            except (TypeError, ValueError):
                age = 0.0
            profile = None
            try:
                profile = self._research_profile_for_book(bid)
            except Exception:
                profile = None

            # V4.11 score qualification is stricter than raw observation count.
            # Three observations make a book OBS-qualified; SCORE-qualified also
            # requires non-negative recent economics and, when available, a
            # non-negative raw Kappa proxy.
            expiry = self._research_kappa_expiry(bid)
            deadline_urgency = float(getattr(expiry, "expiry_urgency", 0.0) or 0.0)
            refresh_urgency = deadline_urgency if bool(getattr(expiry, "qualified", False)) else 0.0
            needs_refresh = bool(getattr(expiry, "qualified", False) and refresh_urgency > 0.0)
            deadline_critical = bool(
                getattr(self, "research_deadline_scheduler_enabled", True)
                and deadline_urgency >= float(getattr(self, "research_deadline_critical_urgency", 0.50))
                and int(samples) > 0
            )
            time_to_deadline_ns = getattr(expiry, "time_to_expiry_ns", None)
            raw_kappa = None if profile is None else getattr(profile, "raw_kappa", None)
            kappa_quality_ok = True
            if raw_kappa is not None:
                try:
                    rk = float(raw_kappa)
                    if math.isfinite(rk):
                        kappa_quality_ok = rk >= float(
                            getattr(self, "research_score_qualified_kappa_floor", 0.0)
                        )
                except (TypeError, ValueError):
                    pass
            score_qualified = bool(
                kappa.eligible
                and recent >= float(getattr(self, "research_score_qualified_pnl_floor", 0.0))
                and kappa_quality_ok
            )
            if kappa.eligible:
                obs_qualified_count += 1
            if score_qualified:
                score_qualified_ids.add(bid)
            if needs_refresh:
                expiring_count += 1
            if deadline_critical:
                if bool(getattr(expiry, "qualified", False)):
                    deadline_critical_qualified_count += 1
                else:
                    deadline_critical_incomplete_count += 1
            if time_to_deadline_ns is not None:
                try:
                    ttd = max(0, int(time_to_deadline_ns))
                    min_deadline_ns = ttd if min_deadline_ns is None else min(min_deadline_ns, ttd)
                except (TypeError, ValueError):
                    pass
            if int(remaining) == 1:
                one_away_count += 1
            elif int(remaining) == 2:
                two_away_count += 1

            urgency = 0.0
            if has_inv or is_dust:
                net_qty = qty
                try:
                    net_qty = float(self._position_tracker_snapshot(bid).net_qty)
                except Exception:
                    pass
                expected_mo = self._research_conservative_markout(bid)
                ofi_against = self._research_ofi_against(bid, net_qty)
                adverse_risk = composite_adverse_selection_risk(
                    expected_markout_bps=expected_mo,
                    ofi_against=ofi_against,
                )
                cap_headroom = self._research_volume_cap_headroom(state, bid)
                urgency = exit_urgency(
                    inventory_size=qty,
                    inventory_ratio=qty / max_inv,
                    inventory_age=age,
                    unrealized_pnl=None,
                    expected_markout=expected_mo,
                    volatility=float(getattr(profile, "volatility", 0.0) or 0.0) if profile is not None else 0.0,
                    inventory_sign=net_qty,
                    kappa_need=kappa_completion_need(remaining, None),
                    volume_cap_headroom=cap_headroom,
                    recent_realized_pnl=recent,
                    adverse_selection_risk=adverse_risk,
                )
                if hard:
                    urgency = max(urgency, 0.95)
            rows.append(
                ScreenBook(
                    book_id=bid,
                    has_inventory=has_inv,
                    is_dust=bool(is_dust),
                    observations_remaining=int(remaining),
                    is_hard_risk=hard,
                    has_live_quote=live,
                    cheap_score=cheap,
                )
            )
            lane_rows.append(
                LaneBook(
                    book_id=bid,
                    has_inventory=has_inv,
                    is_dust=bool(is_dust),
                    is_hard_risk=hard,
                    is_uncovered=int(samples) <= 0,
                    is_stale=not live and not has_inv,
                    is_inactive=str(getattr(profile, "tier", "") or "").upper() == "INACTIVE",
                    observations_remaining=int(remaining),
                    exit_urgency=urgency,
                    cheap_score=cheap,
                    maker_ev=maker_ev,
                    maker_ev_known=ev is not None,
                    economics_ok=recent >= pnl_floor,
                    entry_feasible=entry_feasible,
                    needs_refresh=needs_refresh,
                    refresh_urgency=refresh_urgency,
                    deadline_urgency=deadline_urgency,
                    deadline_critical=deadline_critical,
                    time_to_deadline_ns=time_to_deadline_ns,
                    score_qualified=score_qualified,
                )
            )
            cohort_candidates.append(
                CohortCandidate(
                    book_id=bid,
                    observations_remaining=int(remaining),
                    entry_feasible=entry_feasible,
                    economics_ok=recent >= pnl_floor,
                    hard_risk=hard,
                    has_inventory=has_inv or is_dust,
                    score_qualified=score_qualified,
                    needs_refresh=needs_refresh,
                    refresh_urgency=refresh_urgency,
                    deadline_urgency=deadline_urgency,
                    deadline_critical=deadline_critical,
                    cheap_score=cheap,
                )
            )

        # V4.12.7 breadth rotation: when there is productive ONE_AWAY/TWO_AWAY
        # backlog, stable score-qualified books are retired from *new acquisition*.
        # This does not touch existing positions, dust/risk exits, or expiry refresh.
        # V4.12.9 / St6.4 final: breadth rotation is now tied to the live
        # score target instead of being a telemetry-only number.  Current SN79
        # scoring tolerates 48 inactive books out of 128, so 80 qualified books
        # is the critical breadth boundary.  Target 88 keeps an 8-book rolling
        # expiry buffer.  Once the target is reached, forced suppression turns
        # off and the normal deadline/completion scheduler resumes.
        score_target = int(getattr(self, "research_score_target_books", 88) or 88)
        score_deficit = max(0, score_target - len(score_qualified_ids))
        lane_rows, qualified_suppressed_ids, productive_incomplete = apply_breadth_rotation_gate(
            lane_rows,
            enabled=bool(getattr(self, "research_suppress_qualified_acquisition", True))
            and score_deficit > 0,
            min_productive_incomplete=int(
                getattr(self, "research_qualified_suppression_min_incomplete", 1) or 1
            ),
        )
        self._research_qualified_suppressed_ids = set(qualified_suppressed_ids)
        self._research_productive_incomplete_count = int(productive_incomplete)

        # V4.12: when inventory concurrency is saturated, do not spend deep
        # prediction / quote capacity on additional flat-book acquisition. This
        # is a hard throughput-control cap, not a risk exit cap: existing
        # inventory, dust, and hard-risk books stay eligible for realization.
        max_open_books = int(getattr(self, "research_max_open_books", 6) or 6)
        open_cap_saturated = actual_nonflat >= max_open_books
        if open_cap_saturated:
            lane_rows = [
                replace(row, entry_feasible=False)
                if not (row.has_inventory or row.is_dust or row.is_hard_risk)
                else row
                for row in lane_rows
            ]
            cohort_candidates = [
                replace(row, entry_feasible=False)
                if not (row.has_inventory or row.hard_risk)
                else row
                for row in cohort_candidates
            ]

        cohort = update_sticky_cohort(
            getattr(self, "_research_cohort_ids", []),
            cohort_candidates,
            target_size=int(getattr(self, "research_cohort_size", 10)),
            exploration_slots=int(getattr(self, "research_cohort_exploration_slots", 1)),
        )
        self._research_cohort_ids = list(cohort)
        self._research_score_qualified_ids = set(score_qualified_ids)
        cohort_set = set(cohort)
        lane_rows = [replace(row, cohort_member=int(row.book_id) in cohort_set) for row in lane_rows]

        # One compact progress record per request. This is the primary V4.11
        # upside telemetry: concentration -> observations -> score qualification.
        try:
            self._emit(
                "COHORT",
                force=True,
                tick=getattr(self, "_tick", None),
                size=len(cohort),
                books=",".join(str(x) for x in cohort),
                target=int(getattr(self, "research_cohort_size", 10)),
            )
            self._emit(
                "SCORE_PROGRESS",
                force=True,
                tick=getattr(self, "_tick", None),
                score_qualified=len(score_qualified_ids),
                obs_qualified=int(obs_qualified_count),
                one_away=int(one_away_count),
                two_away=int(two_away_count),
                expiring=int(expiring_count),
                deadline_critical_qualified=int(deadline_critical_qualified_count),
                deadline_critical_incomplete=int(deadline_critical_incomplete_count),
                min_deadline_ns=min_deadline_ns,
                score_target=int(getattr(self, "research_score_target_books", 88)),
                score_deficit=max(0, int(getattr(self, "research_score_target_books", 88)) - len(score_qualified_ids)),
                cohort_size=len(cohort),
                productive_incomplete=int(getattr(self, "_research_productive_incomplete_count", 0) or 0),
                qualified_suppressed=len(getattr(self, "_research_qualified_suppressed_ids", set()) or set()),
            )
        except Exception:
            pass

        self._research_inventory_lane_diag = {
            "actual_nonflat_inventory": int(actual_nonflat),
            "max_open_books": int(getattr(self, "research_max_open_books", 6)),
            "open_cap_saturated": int(actual_nonflat >= int(getattr(self, "research_max_open_books", 6))),
            "stale_empty_position_keys": int(stale_empty_position_keys),
            "productive_incomplete": int(getattr(self, "_research_productive_incomplete_count", 0) or 0),
            "qualified_suppressed": len(getattr(self, "_research_qualified_suppressed_ids", set()) or set()),
            "deadline_critical_qualified": int(deadline_critical_qualified_count),
            "deadline_critical_incomplete": int(deadline_critical_incomplete_count),
            "min_deadline_ns": min_deadline_ns,
            "score_target": int(getattr(self, "research_score_target_books", 88)),
            "score_deficit": max(0, int(getattr(self, "research_score_target_books", 88)) - len(score_qualified_ids)),
        }
        cap = self.research_candidate_count
        if not self._research_lanes_on():
            result = select_fast_candidates(rows, cap)
            self._research_last_lanes = None
        else:
            allocation = select_lane_candidates(
                lane_rows,
                self._research_lane_budgets_for_screen(),
                max_candidates=cap,
            )
            self._research_last_lanes = allocation
            selected = list(allocation.selected)
            result = ScreenResult(
                selected=selected,
                forced=list(allocation.by_lane.get(LANE_REALIZATION, []))
                + list(allocation.by_lane.get(EXEC_LANE_COMPLETION, [])),
                forced_inventory=list(allocation.by_lane.get(LANE_REALIZATION, [])),
                forced_dust=[row.book_id for row in lane_rows if row.is_dust and row.book_id in selected],
                forced_kappa=list(allocation.by_lane.get(EXEC_LANE_COMPLETION, [])),
                forced_hard_risk=[row.book_id for row in lane_rows if row.is_hard_risk and row.book_id in selected],
                forced_live=[row.book_id for row in rows if row.has_live_quote and row.book_id in selected],
                screened_extra=list(allocation.by_lane.get(EXEC_LANE_COVERAGE, [])),
                candidate_count=int(cap),
                universe=len(lane_rows),
            )
            try:
                self._research_emit_lanes(stage="SCREEN")
            except Exception:
                pass
        self._research_last_screen = result
        return result

    def _research_full_predict_fallback(self, state: MarketSimulationStateUpdate):
        started = time.perf_counter()
        predictions = super()._predict_all_books(state)
        elapsed = (time.perf_counter() - started) * 1000.0
        self._research_timing["full_predict_ms"] = elapsed
        self._research_timing["screen_ms"] = float(self._research_timing.get("screen_ms", 0.0) or 0.0)
        self._research_timing["screen_all_books_ms"] = self._research_timing["screen_ms"]
        self._research_timing["candidate_count"] = len(predictions)
        self._research_timing["screen_fallback"] = 1
        cache = getattr(self, "_research_feature_cache", None)
        if cache is not None:
            self._research_timing["cache_hits"] = int(cache.hits)
            self._research_timing["cache_misses"] = int(cache.misses)
        return predictions

    def _predict_all_books(self, state: MarketSimulationStateUpdate):
        books = getattr(state, "books", None) or {}
        if not books:
            self._last_predictions = {}
            return {}
        if not getattr(self, "research_enable_fast_candidate_screen", False):
            return self._research_full_predict_fallback(state)
        started = time.perf_counter()
        self._research_last_lanes = None
        try:
            screen = self._research_fast_screen(state)
        except Exception:
            self._research_timing["screen_ms"] = (time.perf_counter() - started) * 1000.0
            self._research_timing["screen_all_books_ms"] = self._research_timing["screen_ms"]
            return self._research_full_predict_fallback(state)
        self._research_timing["screen_ms"] = (time.perf_counter() - started) * 1000.0
        self._research_timing["screen_all_books_ms"] = self._research_timing["screen_ms"]
        if not screen.selected:
            return self._research_full_predict_fallback(state)
        selected = {int(bid) for bid in screen.selected}
        started = time.perf_counter()
        predictions = {}
        timestamp = getattr(state, "timestamp", 0)
        for book_id, book in books.items():
            if int(book_id) in selected:
                predictions[book_id] = self.predict_direction(book_id, book, timestamp)
            else:
                self._research_keep_cheap_state(book_id, book, timestamp)
        self._last_predictions = predictions
        self._research_timing["full_predict_ms"] = (time.perf_counter() - started) * 1000.0
        self._research_timing["candidate_count"] = len(screen.selected)
        self._research_timing["forced_inventory_count"] = len(screen.forced_inventory)
        self._research_timing["forced_kappa_count"] = len(screen.forced_kappa)
        self._research_timing["screen_fallback"] = 0
        cache = getattr(self, "_research_feature_cache", None)
        if cache is not None:
            self._research_timing["cache_hits"] = int(cache.hits)
            self._research_timing["cache_misses"] = int(cache.misses)
        return predictions

    def _compute_local_kappa(self, state):
        """Event-driven miner-side validator Kappa cache.

        V4.12.1 refreshed on a fixed tick cadence, which still created ~90 ms
        periodic spikes. Kappa only changes when realized history changes or a
        rolling-window observation expires, so V4.12.2 keys the cache to those
        events instead of wall/tick cadence.
        """
        try:
            self._research_refresh_rolling_kappa_cache()
        except Exception:
            pass
        now = int(getattr(self, "_research_last_sim_ts", 0) or 0)
        source_sig = getattr(self, "_research_kappa_roll_cache_key", None)
        next_expiry = getattr(self, "_research_kappa_roll_next_expiry_ts", None)
        cached = getattr(self, "_research_local_kappa_cache_value", None)
        if (
            cached is not None
            and getattr(self, "_research_local_kappa_source_sig", None) == source_sig
            and (next_expiry is None or now <= 0 or now <= int(next_expiry))
        ):
            return cached
        value = super()._compute_local_kappa(state)
        self._research_local_kappa_cache_value = value
        self._research_local_kappa_source_sig = source_sig
        self._research_local_kappa_next_expiry_ts = next_expiry
        return value

    def select_books_for_trading(self, state, predictions):
        started = time.perf_counter()
        selection = super().select_books_for_trading(state, predictions)
        if getattr(self, "research_enable_fast_candidate_screen", False):
            screen = getattr(self, "_research_last_screen", None)
            allowed = {int(bid) for bid in getattr(screen, "selected", []) or []}
            if allowed:
                selection.alpha_books = [
                    bid for bid in selection.alpha_books if int(bid) in allowed
                ]
                selection.maintenance_books = [
                    bid for bid in selection.maintenance_books if int(bid) in allowed
                ]
        elapsed = (time.perf_counter() - started) * 1000.0
        self._research_timing["selection_ms"] = elapsed
        self._research_timing["ranking_ms"] = elapsed
        return selection

    def _research_record_timing(self, state, response, total_ms: float) -> None:
        timing = getattr(self, "_research_timing", {}) or {}
        named = timing_payload({
            **timing,
            "total_response_ms": float(total_ms),
        })
        screen_ms = named["screen_ms"]
        predict_ms = named["full_predict_ms"]
        select_ms = named["ranking_ms"]
        build_ms = named["build_orders_ms"]
        logging_ms = named["logging_ms"]
        respond_wall = float(timing.get("_respond_wall_ms", 0.0) or 0.0)
        residual = max(0.0, respond_wall - (screen_ms + predict_ms + select_ms + build_ms))
        adaptive_ms = float(getattr(self, "_research_markout_eval_ms", 0.0) or 0.0) + residual
        samples = getattr(self, "_research_response_ms", None)
        if samples is None:
            samples = []
            self._research_response_ms = samples
        samples.append(float(total_ms))
        if len(samples) > 1024:
            del samples[:-1024]
        prev_latency = getattr(self, "_research_latency_ewma_ms", None)
        if prev_latency is None:
            self._research_latency_ewma_ms = float(total_ms)
        else:
            try:
                self._research_latency_ewma_ms = (
                    0.20 * float(total_ms) + 0.80 * float(prev_latency)
                )
            except (TypeError, ValueError):
                self._research_latency_ewma_ms = float(total_ms)
        p95 = self._research_pct(samples, 0.95)
        payload = {
            "tick": getattr(self, "_tick", None),
            "timestamp": getattr(state, "timestamp", None),
            "screen_ms": round(screen_ms, 4),
            "screen_all_books_ms": round(screen_ms, 4),
            "full_predict_ms": round(predict_ms, 4),
            "ranking_ms": round(select_ms, 4),
            "selection_ms": round(select_ms, 4),
            "build_orders_ms": round(build_ms, 4),
            "logging_ms": round(logging_ms, 4),
            "adaptive_or_research_ms": round(adaptive_ms, 4),
            "total_response_ms": round(float(total_ms), 4),
            "candidate_count": int(timing.get("candidate_count", 0) or 0),
            "forced_inventory_count": int(timing.get("forced_inventory_count", 0) or 0),
            "forced_kappa_count": int(timing.get("forced_kappa_count", 0) or 0),
            "screen_fallback": int(timing.get("screen_fallback", 0) or 0),
            "cache_hits": int(timing.get("cache_hits", 0) or 0),
            "cache_misses": int(timing.get("cache_misses", 0) or 0),
            "mean_response_ms": round(sum(samples) / max(len(samples), 1), 4),
            "p50_response_ms": round(self._research_pct(samples, 0.50), 4),
            "p95_response_ms": round(p95, 4),
            "p99_response_ms": round(self._research_pct(samples, 0.99), 4),
            "worst_response_ms": round(max(samples), 4),
            "p95_target_ms": float(getattr(self, "research_p95_target_ms", P95_TARGET_MS)),
            "p95_over_target": int(p95 > float(getattr(self, "research_p95_target_ms", P95_TARGET_MS))),
            "instructions": len(getattr(response, "instructions", []) or []),
        }
        try:
            self._emit("RESPOND_TIMING", force=True, **payload)
        except Exception:
            pass

    @staticmethod
    def _research_pct(xs: list[float], p: float) -> float:
        if not xs:
            return 0.0
        ordered = sorted(float(x) for x in xs)
        k = (len(ordered) - 1) * max(0.0, min(1.0, float(p)))
        lo = int(k)
        hi = min(len(ordered) - 1, lo + 1)
        if lo == hi:
            return ordered[lo]
        w = k - lo
        return ordered[lo] * (1.0 - w) + ordered[hi] * w

    def _research_tick_size(self, state) -> float | None:
        try:
            decimals = int(getattr(getattr(state, "config", None), "priceDecimals", 0) or 0)
            return 10.0 ** (-decimals) if decimals >= 0 else None
        except (TypeError, ValueError):
            return None

    def _research_instruction_is_maker(self, instruction) -> bool:
        flag = self._get(instruction, "postOnly", "post_only")
        if flag is True or flag == 1:
            return True
        if isinstance(flag, str) and flag.strip().lower() in {"true", "1", "yes"}:
            return True
        return False

    def _research_instruction_side(self, instruction) -> str:
        raw = self._get(instruction, "direction", "side")
        token = str(getattr(raw, "name", raw)).upper()
        if token in {"0", "BUY", "BID", "ORDERDIRECTION.BUY"}:
            return "buy"
        return "sell"

    def _research_ttl_ms(self, instruction) -> float | None:
        expiry = self._get(
            instruction, "expiryPeriod", "expiry_period", "expiryPeriodNs",
        )
        if expiry is None:
            expiry = getattr(self, "mm_expiry_period", None)
        try:
            return sim_delta_ms(0, int(expiry))
        except (TypeError, ValueError):
            return None

    def _research_queue_metrics(self, book, side: str, quote_price: float | None):
        if book is None or quote_price is None:
            return {}
        levels = getattr(book, "bids", None) if side == "buy" else getattr(book, "asks", None)
        if not levels:
            return {}
        target = float(quote_price)
        for level in list(levels)[:8]:
            try:
                price = float(getattr(level, "price"))
            except (TypeError, ValueError, AttributeError):
                continue
            if abs(price - target) > 1e-12:
                continue
            orders = getattr(level, "orders", None)
            order_maps = None
            if orders:
                order_maps = []
                for order in orders:
                    try:
                        order_maps.append(
                            {"quantity": float(getattr(order, "quantity", 0.0) or 0.0)}
                        )
                    except (TypeError, ValueError, AttributeError):
                        continue
            qty = None
            try:
                qty = float(getattr(level, "quantity"))
            except (TypeError, ValueError, AttributeError):
                qty = None
            return optional_queue_metrics(level_quantity=qty, orders=order_maps)
        return {}

    def _research_register_submitted_quotes(self, response, state) -> None:
        store = getattr(self, "_research_quote_store", None)
        if store is None:
            return
        now = getattr(state, "timestamp", None)
        tick_size = self._research_tick_size(state)
        books = getattr(state, "books", None) or {}
        selection = getattr(self, "_research_last_selection", None)
        profiles = {
            int(getattr(p, "book_id")): p
            for p in list(getattr(selection, "profiles", None) or [])
            if getattr(p, "book_id", None) is not None
        }
        for instruction in getattr(response, "instructions", []) or []:
            if not self._research_instruction_is_maker(instruction):
                continue
            book_id = self._get(instruction, "bookId", "book_id")
            if book_id is None:
                continue
            try:
                book_id = int(book_id)
            except (TypeError, ValueError):
                continue
            side = self._research_instruction_side(instruction)
            client_id = self._get(instruction, "clientOrderId", "client_order_id")
            try:
                client_id = int(client_id) if client_id is not None else None
            except (TypeError, ValueError):
                client_id = None
            quote_price = self._get(instruction, "price", "limitPrice", "limit_price")
            quantity = self._get(instruction, "quantity", "qty", "size")
            try:
                quote_price = float(quote_price) if quote_price is not None else None
            except (TypeError, ValueError):
                quote_price = None
            try:
                quantity = float(quantity) if quantity is not None else None
            except (TypeError, ValueError):
                quantity = None
            book = books.get(book_id) if isinstance(books, dict) else None
            bid = ask = mid = spread = spread_bps = None
            if book is not None and getattr(book, "bids", None) and getattr(book, "asks", None):
                bid = float(book.bids[0].price)
                ask = float(book.asks[0].price)
                mid = 0.5 * (bid + ask)
                spread = ask - bid
                if mid > 0.0:
                    spread_bps = spread / mid * 10_000.0
            dist_ticks, dist_bps = touch_distance(
                side, quote_price or 0.0, bid, ask, mid, tick_size,
            ) if quote_price is not None else (None, None)
            profile = profiles.get(book_id)
            signals = self._research_book_micro.get(book_id) or {}
            decision = (
                self._book_record(book_id) if getattr(self, "debug_enabled", False) else {}
            )
            predicted = None
            if side == "buy":
                predicted = decision.get("fill_buy")
            elif side == "sell":
                predicted = decision.get("fill_sell")
            haz_pack = (getattr(self, "_research_hazard_last", {}) or {}).get(int(book_id), {})
            haz_pred = haz_pack.get(side)
            old_est = haz_pack.get("policy") or haz_pack.get("old")
            if old_est is not None:
                predicted = getattr(old_est, side, predicted)
            inventory_before = None
            try:
                inventory_before = float(self._position_tracker_snapshot(book_id).net_qty)
            except Exception:
                inv = decision.get("inventory") or {}
                inventory_before = inv.get("net_base")
            queue = self._research_queue_metrics(book, side, quote_price)
            ttl_ms = self._research_ttl_ms(instruction)
            feat = HazardFeatures.from_snapshot(
                side=side,
                distance_from_touch_bps=dist_bps,
                spread_bps=spread_bps,
                volatility=(
                    getattr(profile, "volatility", None) if profile is not None else None
                ),
                trade_rate=(
                    getattr(profile, "trade_rate", None) if profile is not None else None
                ),
                imbalance=(
                    getattr(profile, "imbalance", None) if profile is not None else
                    signals.get("imbalance")
                ),
                market_regime=getattr(self, "_research_market_regime", None),
                ttl_ms=ttl_ms,
                quote_age_ms=0.0,
            )
            if haz_pred is None and getattr(self, "research_enable_fill_hazard", False):
                haz_pred = self._research_fill_hazard.predict(feat)
            record = QuoteRecord(
                quote_id=store.next_quote_id(),
                client_id=client_id,
                book=book_id,
                side=side,
                decision_ts=now if now is None else int(now),
                submit_ts=now if now is None else int(now),
                requested_quantity=quantity,
                remaining_quantity=quantity,
                quote_price=quote_price,
                configured_ttl_ms=ttl_ms,
                predicted_fill_probability=(
                    float(predicted) if predicted is not None else None
                ),
                predicted_any_fill_probability=(
                    None if haz_pred is None else float(haz_pred.any_fill)
                ),
                predicted_actionable_fill_probability=(
                    None if haz_pred is None else float(haz_pred.actionable_fill)
                ),
                predicted_dust_probability=(
                    None if haz_pred is None else float(haz_pred.dust)
                ),
                hazard_source=None if haz_pred is None else str(haz_pred.source),
                hazard_features={
                    "side": feat.side,
                    "dist_bucket": feat.dist_bucket,
                    "spread_bucket": feat.spread_bucket,
                    "vol_bucket": feat.vol_bucket,
                    "trade_bucket": feat.trade_bucket,
                    "imb_bucket": feat.imb_bucket,
                    "regime_group": feat.regime_group,
                    "ttl_bucket": feat.ttl_bucket,
                    "ttl_ms": feat.ttl_ms,
                    "distance_from_touch_bps": feat.distance_from_touch_bps,
                    "quote_age_bucket": feat.quote_age_bucket,
                    "quote_age_ms": feat.quote_age_ms,
                },
                market_regime=getattr(self, "_research_market_regime", None),
                score_regime=getattr(self, "_research_score_regime", None),
                book_archetype=decision.get("archetype"),
                snapshot={
                    "mid": mid,
                    "microprice": signals.get("microprice"),
                    "microprice_delta": signals.get("microprice_delta"),
                    "best_bid": bid,
                    "best_ask": ask,
                    "spread": spread,
                    "spread_bps": spread_bps,
                    "distance_from_touch_ticks": dist_ticks,
                    "distance_from_touch_bps": dist_bps,
                    "volatility": (
                        getattr(profile, "volatility", None) if profile is not None else None
                    ),
                    "trade_rate": (
                        getattr(profile, "trade_rate", None) if profile is not None else None
                    ),
                    "imbalance": (
                        getattr(profile, "imbalance", None) if profile is not None else
                        signals.get("imbalance")
                    ),
                    **self._research_ofi_fields(book_id),
                    "deep_imbalance": signals.get("deep_imbalance"),
                    "momentum": signals.get("momentum"),
                    "trade_imbalance": signals.get("trade_imbalance"),
                    "trade_sign_persistence": signals.get("trade_sign_persistence"),
                    "inventory_before": inventory_before,
                    "kappa_observation_count_before": int(
                        self._research_kappa_book(book_id).realized_observation_count
                    ),
                    "kappa_lifetime_observation_count_before": int(
                        self._research_realized_observations_by_book.get(book_id, 0)
                    ),
                    "alpha": (
                        None if decision.get("signal") is None
                        else float(decision.get("signal"))
                    ),
                    "quote_ev": (
                        None if (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id)) is None
                        else getattr(
                            self._research_score_ev_last[int(book_id)], "trading_ev", None
                        )
                    ),
                    "inventory_util": (
                        None if inventory_before is None
                        else abs(float(inventory_before))
                        / max(float(getattr(self, "max_inventory_base", 1.0) or 1.0), 1e-9)
                    ),
                    "inventory_state": (
                        "FLAT" if inventory_before is None or abs(float(inventory_before)) <= 1e-12
                        else ("LONG" if float(inventory_before) > 0.0 else "SHORT")
                    ),
                    "toxic": str(getattr(self, "_research_market_regime", "")).upper() in {"TOXIC"},
                    **queue,
                },
            )
            store.register_quote(record)
            replaced = getattr(store, "last_replaced", None)
            if replaced is not None:
                self._research_observe_quote_end(
                    replaced, filled=False, timestamp=record.submit_ts, reason="REPLACE",
                )
            self._research_quotes_registered += 1
            payload = {
                "quote_id": record.quote_id,
                "client_id": record.client_id,
                "book": record.book,
                "side": record.side,
                "decision_timestamp": record.decision_ts,
                "submit_timestamp": record.submit_ts,
                "cancel_timestamp": None,
                "fill_timestamp": None,
                "quote_price": record.quote_price,
                "requested_quantity": record.requested_quantity,
                "filled_quantity": 0.0,
                "remaining_quantity": record.remaining_quantity,
                "quote_age_ms": 0.0,
                "configured_ttl_ms": record.configured_ttl_ms,
                "predicted_fill_probability": record.predicted_fill_probability,
                "predicted_any_fill_probability": record.predicted_any_fill_probability,
                "predicted_actionable_fill_probability": record.predicted_actionable_fill_probability,
                "predicted_dust_probability": record.predicted_dust_probability,
                "hazard_source": record.hazard_source,
                "market_regime": record.market_regime,
                "score_regime": record.score_regime,
                "book_archetype": record.book_archetype,
                **record.snapshot,
            }
            self._emit("QUOTE", force=True, tick=getattr(self, "_tick", None), **payload)
            if haz_pred is not None:
                self._emit(
                    "EXEC_PROB",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    book=record.book,
                    side=record.side,
                    p_any=haz_pred.any_fill,
                    p_actionable=haz_pred.actionable_fill,
                    p_dust=haz_pred.dust,
                    ttf_hazard=haz_pred.time_to_fill_hazard,
                    remaining_p_any=haz_pred.remaining_any_fill,
                    source=haz_pred.source,
                    usable=int(bool(haz_pred.usable)),
                    n=haz_pred.n_at_risk,
                )

    def _research_close_from_notice(self, notice, timestamp, phase: str | None = None) -> None:
        store = getattr(self, "_research_quote_store", None)
        if store is None:
            return
        book_id = self._get(notice, "bookId", "book_id")
        client_id = self._get(notice, "clientOrderId", "client_order_id")
        if book_id is None:
            return
        try:
            book_id = int(book_id)
        except (TypeError, ValueError):
            return
        try:
            client_id = int(client_id) if client_id is not None else None
        except (TypeError, ValueError):
            client_id = None
        record = store.lookup(book_id, client_id)
        if record is None or not record.open:
            return
        ts = timestamp if timestamp is None else int(timestamp)
        store.close_quote(record, cancel_ts=ts)
        token = str(phase or "").upper()
        reason = "EXPIRE" if "EXPIRE" in token else "CANCEL"
        self._research_observe_quote_end(
            record, filled=False, timestamp=ts, reason=reason,
        )

    def _research_observe_quote_end(
        self,
        record: QuoteRecord,
        *,
        filled: bool,
        timestamp: int | None,
        reason: str,
        fill_class: str | None = None,
    ) -> None:
        if not getattr(self, "research_enable_fill_hazard", False):
            return
        if getattr(record, "hazard_closed", False):
            return
        if filled is False and record.fill_ts is not None:
            return
        stored = getattr(record, "hazard_features", None) or {}
        try:
            feat = HazardFeatures(
                side=str(stored.get("side") or record.side or "buy"),
                dist_bucket=int(stored.get("dist_bucket", 1)),
                spread_bucket=int(stored.get("spread_bucket", 1)),
                vol_bucket=int(stored.get("vol_bucket", 1)),
                trade_bucket=int(stored.get("trade_bucket", 1)),
                imb_bucket=int(stored.get("imb_bucket", 1)),
                regime_group=str(stored.get("regime_group") or "NORMAL"),
                ttl_bucket=int(stored.get("ttl_bucket", 1)),
                ttl_ms=float(stored.get("ttl_ms") or record.configured_ttl_ms or 500.0),
            )
        except Exception:
            feat = HazardFeatures.from_snapshot(
                side=record.side,
                distance_from_touch_bps=(record.snapshot or {}).get("distance_from_touch_bps"),
                spread_bps=(record.snapshot or {}).get("spread_bps"),
                volatility=(record.snapshot or {}).get("volatility"),
                trade_rate=(record.snapshot or {}).get("trade_rate"),
                imbalance=(record.snapshot or {}).get("imbalance"),
                market_regime=record.market_regime,
                ttl_ms=record.configured_ttl_ms,
            )
        age_ms = sim_delta_ms(record.submit_ts, timestamp)
        if age_ms is None:
            age_ms = 0.0
        pred = None
        if record.predicted_any_fill_probability is not None:
            pred = HazardPrediction(
                any_fill=float(record.predicted_any_fill_probability),
                actionable_fill=float(record.predicted_actionable_fill_probability or 0.0),
                dust=float(record.predicted_dust_probability or 0.0),
                source=str(record.hazard_source or "fallback"),
                usable=True,
                n_at_risk=0,
                ttl_ms=feat.ttl_ms,
            )
        self._research_fill_hazard.observe(
            feat,
            age_ms=float(age_ms),
            filled=bool(filled),
            fill_class=fill_class,
            predicted=pred,
        )
        record.hazard_closed = True
        if pred is not None:
            try:
                self._research_emit_fill_cal(pred, feat.side)
            except Exception:
                pass

    def _research_emit_fill_cal(self, predicted: HazardPrediction, side: str) -> None:
        model = self._research_fill_hazard
        overall = model.brier_overall()
        mapping = (
            ("ANY", predicted.any_fill),
            ("ACTIONABLE", predicted.actionable_fill),
            ("DUST", predicted.dust),
        )
        for kind, p in mapping:
            bucket = cal_bucket(p)
            snap = model.calibration.get((kind, str(side).upper(), bucket))
            if snap is None or snap.sample_count <= 0:
                continue
            row = snap.snapshot()
            self._emit(
                "FILL_CAL",
                force=True,
                tick=getattr(self, "_tick", None),
                kind=kind,
                side=str(side).upper(),
                bucket=bucket,
                bucket_label=cal_bucket_label(bucket),
                predicted_mean=row["predicted_mean"],
                observed_rate=row["observed_rate"],
                sample_count=row["sample_count"],
                brier_component=row["brier_component"],
                brier_overall=overall.get(kind),
                observations=model.observations,
                events=model.events,
                censored=model.censored,
            )

    def _research_on_own_fill(
        self,
        *,
        event,
        book_id: int,
        before: float,
        after: float,
        kappa_before: int,
        kappa_after: int,
        is_maker: bool,
    ) -> None:
        store = getattr(self, "_research_quote_store", None)
        if store is None:
            return
        client_id = getattr(event, "clientOrderId", None)
        try:
            client_id = int(client_id) if client_id is not None else None
        except (TypeError, ValueError):
            client_id = None
        event_side = getattr(event, "side", None)
        if is_maker:
            side = "buy" if event_side == 1 else "sell"
        else:
            side = "buy" if event_side == 0 else "sell"
        record = store.lookup(book_id, client_id)
        fill_qty = abs(float(getattr(event, "quantity", 0.0) or 0.0))
        fill_price = float(getattr(event, "price", 0.0) or 0.0)
        fill_ts = getattr(event, "timestamp", None)
        fill_ts = None if fill_ts is None else int(fill_ts)
        eps = self._execution_flat_epsilon()
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        requested = record.requested_quantity if record is not None else None
        filled_cum = fill_qty
        quote_id = None
        remaining = None
        quote_age_ms = None
        snap = {}
        if record is not None:
            quote_id = record.quote_id
            filled_cum = store.apply_fill(
                record, fill_qty=fill_qty, fill_ts=fill_ts, flat_eps=eps,
            )
            requested = record.requested_quantity
            remaining = record.remaining_quantity
            quote_age_ms = sim_delta_ms(record.submit_ts, fill_ts)
            snap = dict(record.snapshot)
            side = record.side or side
        fill_class = classify_fill(
            inventory_before=before,
            inventory_after=after,
            fill_quantity=fill_qty,
            requested_quantity=requested,
            filled_quantity=filled_cum,
            min_order_size=min_size,
            flat_eps=eps,
        )
        self._research_fills_classified += 1
        fee = None
        try:
            fee = float(event.makerFee if is_maker else event.takerFee)
        except Exception:
            fee = None
        payload = {
            "quote_id": quote_id,
            "client_id": client_id,
            "book": book_id,
            "side": side,
            "decision_timestamp": None if record is None else record.decision_ts,
            "submit_timestamp": None if record is None else record.submit_ts,
            "cancel_timestamp": None if record is None else record.cancel_ts,
            "fill_timestamp": fill_ts,
            "mid": snap.get("mid"),
            "microprice": snap.get("microprice"),
            "microprice_delta": snap.get("microprice_delta"),
            "best_bid": snap.get("best_bid"),
            "best_ask": snap.get("best_ask"),
            "spread": snap.get("spread"),
            "spread_bps": snap.get("spread_bps"),
            "quote_price": None if record is None else record.quote_price,
            "distance_from_touch_ticks": snap.get("distance_from_touch_ticks"),
            "distance_from_touch_bps": snap.get("distance_from_touch_bps"),
            "volatility": snap.get("volatility"),
            "trade_rate": snap.get("trade_rate"),
            "imbalance": snap.get("imbalance"),
            "ofi_raw": snap.get("ofi_raw"),
            "ofi_normalized": snap.get("ofi_normalized"),
            "ofi_fast": snap.get("ofi_fast"),
            "ofi_source": snap.get("ofi_source"),
            "deep_imbalance": snap.get("deep_imbalance"),
            "momentum": snap.get("momentum"),
            "trade_imbalance": snap.get("trade_imbalance"),
            "trade_sign_persistence": snap.get("trade_sign_persistence"),
            "inventory_before": before,
            "inventory_after": after,
            "requested_quantity": requested,
            "filled_quantity": filled_cum,
            "remaining_quantity": remaining,
            "quote_age_ms": quote_age_ms,
            "configured_ttl_ms": None if record is None else record.configured_ttl_ms,
            "predicted_fill_probability": (
                None if record is None else record.predicted_fill_probability
            ),
            "predicted_any_fill_probability": (
                None if record is None else record.predicted_any_fill_probability
            ),
            "predicted_actionable_fill_probability": (
                None if record is None else record.predicted_actionable_fill_probability
            ),
            "predicted_dust_probability": (
                None if record is None else record.predicted_dust_probability
            ),
            "hazard_source": None if record is None else record.hazard_source,
            "market_regime": None if record is None else record.market_regime,
            "score_regime": None if record is None else record.score_regime,
            "book_archetype": None if record is None else record.book_archetype,
            "kappa_observation_count_before": kappa_before,
            "kappa_observation_count_after": kappa_after,
            "maker": bool(is_maker),
            "taker": (not bool(is_maker)),
            "fee": fee,
            "fill_class": fill_class,
            "fill_price": fill_price,
            "min_order_size": min_size,
        }
        if "queue_ahead" in snap:
            payload["queue_ahead"] = snap["queue_ahead"]
        if "queue_depth_at_price" in snap:
            payload["queue_depth_at_price"] = snap["queue_depth_at_price"]
        self._emit("FILL", force=True, tick=getattr(self, "_tick", None), **payload)

        if is_maker and record is not None:
            self._research_observe_quote_end(
                record,
                filled=True,
                timestamp=fill_ts,
                reason="FILL",
                fill_class=str(fill_class),
            )

        if record is not None and (
            remaining is not None and remaining <= eps
            or fill_class in {"FLAT", "FULL", "CROSS_DUST"}
        ):
            store.close_quote(record, fill_ts=fill_ts)

        if is_maker and fill_ts is not None and fill_price > 0.0:
            dropped = store.schedule_markouts(
                quote_id=int(quote_id if quote_id is not None else store.next_quote_id()),
                book=int(book_id),
                side=str(side),
                fill_price=float(fill_price),
                fill_ts=int(fill_ts),
            )
            for row in dropped:
                self._research_markouts_emitted += 1
                self._emit(
                    "MARKOUT",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    quote_id=row.quote_id,
                    book=row.book,
                    side=row.side,
                    horizon_ms=row.horizon_ms,
                    fill_price=row.fill_price,
                    future_mid=row.future_mid,
                    markout_bps=row.markout_bps,
                    status=row.status,
                )

    def _research_evaluate_markouts(self, state) -> None:
        store = getattr(self, "_research_quote_store", None)
        if store is None:
            return
        now = int(getattr(state, "timestamp", 0) or 0)
        try:
            if getattr(self, "research_enable_markout_v2", True):
                store.record_book_mids(getattr(state, "books", None) or {}, now)
        except Exception:
            pass
        if not store.pending:
            return
        for row in store.evaluate(now_ts=now):
            self._research_markouts_emitted += 1
            if row.status == "OK" and row.markout_bps is not None:
                stats = self._research_markout_by_book.setdefault(
                    int(row.book), {"n": 0.0, "sum": 0.0}
                )
                stats["n"] = float(stats.get("n", 0.0)) + 1.0
                stats["sum"] = float(stats.get("sum", 0.0)) + float(row.markout_bps)
                horizons = self._research_markout_horizons.setdefault(int(row.book), {})
                hrow = horizons.setdefault(int(row.horizon_ms), {"n": 0.0, "sum": 0.0})
                hrow["n"] = float(hrow.get("n", 0.0)) + 1.0
                hrow["sum"] = float(hrow.get("sum", 0.0)) + float(row.markout_bps)
            self._emit(
                "MARKOUT",
                force=True,
                tick=getattr(self, "_tick", None),
                quote_id=row.quote_id,
                book=row.book,
                side=row.side,
                horizon_ms=row.horizon_ms,
                fill_price=row.fill_price,
                future_mid=row.future_mid,
                future_ts=getattr(row, "future_ts", None),
                markout_bps=row.markout_bps,
                status=row.status,
            )

    def _log_submitted_instructions(self, response, state) -> None:
        super()._log_submitted_instructions(response, state)
        try:
            self._research_register_submitted_quotes(response, state)
        except Exception:
            pass

    def _log_notices(self, state, tick: int) -> None:
        super()._log_notices(state, tick)
        try:
            notices = (getattr(state, "notices", None) or {}).get(self.uid, []) or []
            now = getattr(state, "timestamp", None)
            for notice in notices:
                phase = type(notice).__name__.upper()
                if any(token in phase for token in ("CANCEL", "EXPIRE", "REJECT", "FAIL")):
                    self._research_close_from_notice(notice, now, phase=phase)
        except Exception:
            pass

    def _research_regime_snapshot(
        self,
        profiles: list,
        predictions,
        selection,
    ) -> dict[str, Any]:
        """One pass over already-built profiles. Does not rescan L2 books."""
        profile_list = list(profiles or [])
        n = len(profile_list)
        spreads: list[float] = []
        vols: list[float] = []
        rates: list[float] = []
        imbalances: list[float] = []
        inactive = 0
        red = 0
        green = 0
        liquid_count = 0
        stressed_count = 0
        low_trade_count = 0
        dead_rate = float(getattr(self, "archetype_dead_trade_rate", 0.0) or 0.0)
        stress_cut = float(self._research_stress_spread_bps)
        for profile in profile_list:
            tier = str(getattr(profile, "tier", "")).upper()
            if tier == "INACTIVE":
                inactive += 1
            elif tier == "RED":
                red += 1
            elif tier == "GREEN":
                green += 1
            spread = self._profile_float(profile, "spread_bps")
            vol = self._profile_float(profile, "volatility")
            rate = self._profile_float(profile, "trade_rate")
            imb = self._profile_float(profile, "imbalance")
            if spread is not None:
                spreads.append(spread)
                if spread >= stress_cut:
                    stressed_count += 1
            if vol is not None:
                vols.append(vol)
            if rate is not None:
                rates.append(rate)
                if rate < dead_rate:
                    low_trade_count += 1
            if imb is not None:
                imbalances.append(abs(imb))
            spread_ok = (spread or 0.0) < stress_cut
            rate_ok = (rate or 0.0) >= dead_rate
            if spread_ok and rate_ok:
                liquid_count += 1

        pred_values = list(predictions.values()) if isinstance(predictions, dict) else []
        pred_n = max(len(pred_values), 1)
        up = sum(1 for p in pred_values if str(getattr(p, "direction", "")).upper() == "UP")
        down = sum(1 for p in pred_values if str(getattr(p, "direction", "")).upper() == "DOWN")
        hold = sum(1 for p in pred_values if str(getattr(p, "direction", "")).upper() == "HOLD")
        micro: list[float] = []
        scores: list[float] = []
        for pred in pred_values:
            log_ret = getattr(pred, "log_return", None)
            mom = getattr(pred, "momentum_m", None)
            if log_ret is not None:
                try:
                    micro.append(abs(float(log_ret)))
                except (TypeError, ValueError):
                    pass
            elif mom is not None:
                try:
                    micro.append(abs(float(mom)))
                except (TypeError, ValueError):
                    pass
            try:
                scores.append(abs(float(getattr(pred, "score", 0.0) or 0.0)))
            except (TypeError, ValueError):
                pass

        universe = self._research_kappa_universe(profile_list)
        target = universe.required_observations
        obs_map = {
            row.book: row.realized_observation_count for row in universe.books
        }
        pending = (
            universe.pending_count
            if getattr(self, "research_kappa_completion_enabled", True)
            else 0
        )
        buckets = universe.bucket_counts()
        activity_deficit = self._research_activity_deficit_ratio(obs_map, target, n)
        rt_velocity = self._research_round_trip_velocity()

        denom = max(n, 1)
        return {
            "book_count": n,
            "active": max(0, n - inactive),
            "inactive": inactive,
            "inactive_frac": inactive / denom,
            "red_frac": red / denom,
            "green_frac": green / denom,
            "spread_med": self._percentile(spreads, 0.50),
            "spread_p90": self._percentile(spreads, 0.90),
            "spread_max": max(spreads) if spreads else None,
            "vol_med": self._percentile(vols, 0.50),
            "vol_p90": self._percentile(vols, 0.90),
            "trade_rate_med": self._percentile(rates, 0.50),
            "trade_rate_p90": self._percentile(rates, 0.90),
            "imbalance_med": self._percentile(imbalances, 0.50),
            "micro_vel_med": self._percentile(micro, 0.50),
            "liquid_ratio": liquid_count / denom,
            "stressed_ratio": stressed_count / denom,
            "low_trade_ratio": low_trade_count / denom,
            "trend_up_ratio": up / pred_n,
            "trend_down_ratio": down / pred_n,
            "hold_frac": hold / pred_n,
            "up_frac": up / pred_n,
            "down_frac": down / pred_n,
            "mean_abs_score": (sum(scores) / len(scores)) if scores else 0.0,
            "mean_volatility": (sum(vols) / len(vols)) if vols else 0.0,
            "mean_trade_rate": (sum(rates) / len(rates)) if rates else 0.0,
            "mean_spread_bps": (sum(spreads) / len(spreads)) if spreads else None,
            "mean_imbalance": (
                sum(
                    float(getattr(p, "imbalance", 0.0) or 0.0)
                    for p in profile_list
                ) / denom
            ),
            "pending_kappa_frac": pending / denom,
            "books_0_obs": int(buckets.get("books_0_obs", 0) or 0),
            "books_1_remaining": int(buckets.get("books_1_remaining", 0) or 0),
            "books_2_remaining": int(buckets.get("books_2_remaining", 0) or 0),
            "books_eligible": int(buckets.get("books_eligible", 0) or 0),
            "activity_deficit": activity_deficit,
            "round_trip_velocity": rt_velocity,
            "stress_spread_bps": stress_cut,
            "toxic_spread_bps": float(self._research_toxic_spread_bps),
            "tier_counts": dict(getattr(selection, "tier_counts", {}) or {}),
        }

    def _research_parent_regime(self, snapshot: dict[str, Any], decision) -> MarketRegime:
        """Project V2 labels onto the inherited MarketRegime object."""
        n = int(snapshot.get("book_count", 0) or 0)
        return MarketRegime(
            mode=decision.parent_mode,
            hold_frac=float(snapshot.get("hold_frac", 0.0) or 0.0),
            up_frac=float(snapshot.get("up_frac", 0.0) or 0.0),
            down_frac=float(snapshot.get("down_frac", 0.0) or 0.0),
            mean_score=0.0,
            mean_abs_score=float(snapshot.get("mean_abs_score", 0.0) or 0.0),
            mean_volatility=float(snapshot.get("mean_volatility", 0.0) or 0.0),
            mean_trade_rate=float(snapshot.get("mean_trade_rate", 0.0) or 0.0),
            mean_spread_bps=snapshot.get("mean_spread_bps"),
            mean_imbalance=float(snapshot.get("mean_imbalance", 0.0) or 0.0),
            mean_log_return=snapshot.get("micro_vel_med"),
            return_dispersion=None,
            direction_dispersion=0.0,
            tier_counts=dict(snapshot.get("tier_counts") or {}),
            inactive_frac=float(snapshot.get("inactive_frac", 0.0) or 0.0),
            red_frac=float(snapshot.get("red_frac", 0.0) or 0.0),
            green_frac=float(snapshot.get("green_frac", 0.0) or 0.0),
            scoring_overlay=decision.scoring_overlay,
            confidence=min(1.0, 0.35 + 0.65 * float(snapshot.get("liquid_ratio", 0.0) or 0.0)),
            book_count=n,
        )

    def classify_market_regime_from_profiles(
        self,
        profiles,
        predictions,
        selection,
    ):
        """V4.3: MarketRegime V2 + ScoreRegime. Parent mean-spread latch is not used."""
        started = time.perf_counter()
        profile_list = list(profiles or [])
        self._update_spread_thresholds(profile_list)
        snapshot = self._research_regime_snapshot(profile_list, predictions, selection)
        decision = classify_regime_v2(
            snapshot,
            market_state=self._research_market_debounce,
            score_state=self._research_score_debounce,
            thresholds=self._research_regime_thresholds,
        )
        self._research_market_debounce = decision.market_debounce
        self._research_score_debounce = decision.score_debounce
        self._research_market_regime = decision.market_regime
        self._research_score_regime = decision.score_regime
        regime = self._research_parent_regime(snapshot, decision)
        try:
            setattr(regime, "research_market_regime", decision.market_regime)
            setattr(regime, "research_score_regime", decision.score_regime)
            setattr(regime, "research_market_trigger", decision.market_trigger)
            setattr(regime, "research_score_trigger", decision.score_trigger)
        except Exception:
            pass
        self._last_regime = regime
        if getattr(self, "debug_enabled", False):
            self._debug_current_regime = regime
        if getattr(self, "_debug_stage_ms", None) is not None:
            self._debug_stage_ms["classify_regime_ms"] = (
                time.perf_counter() - started
            ) * 1000.0

        self._emit(
            "REGIME",
            force=True,
            tick=getattr(self, "_tick", None),
            market_regime=decision.market_regime,
            score_regime=decision.score_regime,
            mode=decision.parent_mode,
            overlay=decision.scoring_overlay,
            book_count=snapshot["book_count"],
            active=snapshot["active"],
            inactive=snapshot["inactive"],
            spread_med=snapshot["spread_med"],
            spread_p90=snapshot["spread_p90"],
            spread_max=snapshot["spread_max"],
            vol_med=snapshot["vol_med"],
            vol_p90=snapshot["vol_p90"],
            trade_rate_med=snapshot["trade_rate_med"],
            trade_rate_p90=snapshot["trade_rate_p90"],
            liquid_ratio=snapshot["liquid_ratio"],
            stressed_ratio=snapshot["stressed_ratio"],
            trend_up_ratio=snapshot["trend_up_ratio"],
            trend_down_ratio=snapshot["trend_down_ratio"],
            imbalance_med=snapshot["imbalance_med"],
            micro_vel_med=snapshot["micro_vel_med"],
            pending_kappa_frac=snapshot["pending_kappa_frac"],
            market_trigger=decision.market_trigger,
            market_threshold=decision.market_threshold,
            score_trigger=decision.score_trigger,
            score_threshold=decision.score_threshold,
            stress_spread_bps=snapshot["stress_spread_bps"],
            toxic_spread_bps=snapshot["toxic_spread_bps"],
            parent_mode=decision.parent_mode,
            score_acquisition_mode=int(score_acquisition_mode(
                score_regime=decision.score_regime,
                scoring_overlay=decision.scoring_overlay,
            )),
            score_acquisition_version=self.RESEARCH_SCORE_ACQUISITION_VERSION,
            adaptive=self.research_adaptive_spread_thresholds,
            min_order_size=self._research_exchange_min_order_size,
        )
        score_metrics = score_regime_metrics(snapshot)
        self._emit(
            "SCORE_REGIME",
            force=True,
            tick=getattr(self, "_tick", None),
            state=decision.score_regime,
            coverage_ratio=score_metrics["coverage_ratio"],
            eligible_ratio=score_metrics["eligible_ratio"],
            one_away=score_metrics["one_away"],
            two_away=score_metrics["two_away"],
            rt_velocity=score_metrics["round_trip_velocity"],
            trigger=decision.score_trigger,
        )
        return regime

    def get_regime_params(self, regime: MarketRegime) -> RegimeParamSet:
        params = super().get_regime_params(regime)
        mode = str(getattr(regime, "mode", "")).upper()
        overlay = str(getattr(regime, "scoring_overlay", "")).upper()

        # The parent already turns quoting back on for SCORING_PRESSURE.
        # Outside that overlay, research mode treats global STRESSED as a
        # conservative risk state rather than a universal no-trade switch.
        if (
            self.research_trade_global_stress
            and mode == "STRESSED"
            and overlay != "SCORING_PRESSURE"
        ):
            return RegimeParamSet(
                quote_enabled=True,
                alpha_enabled=False,
                spread_offset=max(float(params.spread_offset), 0.45),
                skew_strength=min(float(params.skew_strength), 0.05),
                size_mult=min(
                    float(params.size_mult),
                    self.research_global_stress_size_mult,
                ),
                profit_target_bps=params.profit_target_bps,
                stop_loss_bps=params.stop_loss_bps,
                min_fill_prob=params.min_fill_prob,
                buy_bias=params.buy_bias,
                sell_bias=params.sell_bias,
            )
        return params

    def estimate_fill_probability(
        self,
        book: Book,
        mid: float,
        spread: float,
        trade_rate: float,
        buy_price: float,
        sell_price: float,
        book_id: int | None = None,
    ) -> FillProbabilityEstimate:
        old = super().estimate_fill_probability(
            book, mid, spread, trade_rate, buy_price, sell_price, book_id=book_id,
        )
        if not getattr(self, "research_enable_fill_hazard", False):
            return old
        try:
            ttl_ms = sim_delta_ms(0, int(getattr(self, "mm_expiry_period", 500_000_000)))
            ttl_ms = 500.0 if ttl_ms is None else float(ttl_ms)
            touch_bid = mid - 0.5 * spread
            touch_ask = mid + 0.5 * spread
            buy_dist_bps = (
                ((touch_bid - buy_price) / mid) * 10_000.0 if mid > 0 else None
            )
            sell_dist_bps = (
                ((sell_price - touch_ask) / mid) * 10_000.0 if mid > 0 else None
            )
            profile = self._research_profile_for_book(book_id)
            vol = None if profile is None else getattr(profile, "volatility", None)
            imb = None if profile is None else getattr(profile, "imbalance", None)
            spread_bps = (spread / mid) * 10_000.0 if mid > 0 else None
            regime = getattr(self, "_research_market_regime", None)
            buy_feat = HazardFeatures.from_snapshot(
                side="buy",
                distance_from_touch_bps=buy_dist_bps,
                spread_bps=spread_bps,
                volatility=vol,
                trade_rate=trade_rate,
                imbalance=imb,
                market_regime=regime,
                ttl_ms=ttl_ms,
            )
            sell_feat = HazardFeatures.from_snapshot(
                side="sell",
                distance_from_touch_bps=sell_dist_bps,
                spread_bps=spread_bps,
                volatility=vol,
                trade_rate=trade_rate,
                imbalance=imb,
                market_regime=regime,
                ttl_ms=ttl_ms,
            )
            model = self._research_fill_hazard
            pred_buy = model.predict(buy_feat)
            pred_sell = model.predict(sell_feat)
            if book_id is not None:
                self._research_hazard_last[int(book_id)] = {
                    "old": old,
                    "buy": pred_buy,
                    "sell": pred_sell,
                    "buy_feat": buy_feat,
                    "sell_feat": sell_feat,
                }
            buy = model.select_policy_probability(
                old.buy, pred_buy,
                use_for_policy=self.research_use_fill_hazard_for_policy,
            )
            sell = model.select_policy_probability(
                old.sell, pred_sell,
                use_for_policy=self.research_use_fill_hazard_for_policy,
            )
            policy_est = FillProbabilityEstimate(buy=buy, sell=sell)
            if book_id is not None:
                self._research_hazard_last.setdefault(int(book_id), {})["policy"] = policy_est
            return policy_est
        except Exception:
            return old

    def _research_profile_for_book(self, book_id: int | None):
        if book_id is None:
            return None
        selection = getattr(self, "_research_last_selection", None)
        for profile in list(getattr(selection, "profiles", None) or []):
            try:
                if int(getattr(profile, "book_id")) == int(book_id):
                    return profile
            except (TypeError, ValueError, AttributeError):
                continue
        return None

    def _mem(self, book_id: int):
        """Attach the book id to parent BookMemory for completion-aware ranking."""
        mem = super()._mem(book_id)
        try:
            setattr(mem, "_research_book_id", int(book_id))
        except Exception:
            pass
        return mem

    def _prefer_maker(self, book_id: int) -> bool:
        """Force maker economics only inside explicitly scoped V4.2 quote contexts."""
        if (
            getattr(self, "research_force_mm_post_only", False)
            and getattr(self, "_research_force_maker_context", False)
        ):
            return True
        return super()._prefer_maker(book_id)

    def _place_round_trip_limits(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        size: float,
        post_only: bool = True,
        expiry_period: int | None = None,
        client_id_base: int = 0,
    ) -> int:
        """Honor explicit maintenance maker-only intent without changing exits."""
        old_context = bool(getattr(self, "_research_force_maker_context", False))
        prev_cap_book = self._research_volume_cap_bind_book(book_id)
        if post_only and self.research_force_mm_post_only:
            self._research_force_maker_context = True
        try:
            cap = self._research_volume_cap_quote(state)
            if cap > 0.0 and self._research_volume_cap_remaining(state, book_id) <= 0.0:
                self._research_emit_volume_cap(
                    state, book_id, allowed=False, reason="CAP_REACHED", force=True,
                )
                return 0
            return super()._place_round_trip_limits(
                response, state, book_id, size, post_only=post_only,
                expiry_period=expiry_period, client_id_base=client_id_base,
            )
        finally:
            self._research_force_maker_context = old_context
            self._research_volume_cap_book_id = prev_cap_book

    def _record_fill_quote(self, mem, side: str, dist_from_touch: float) -> None:
        """Keep parent any-fill learning and add V4.2 quote-quality telemetry."""
        super()._record_fill_quote(mem, side, dist_from_touch)
        if not self.research_actionable_fill_enabled:
            return
        book_id = getattr(mem, "_research_book_id", None)
        if book_id is None:
            return
        bucket = self._spread_dist_bucket(float(dist_from_touch))
        key = (int(book_id), str(side), int(bucket))
        row = self._research_actionable_by_bucket.setdefault(
            key, {"quotes": 0, "maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0}
        )
        row["quotes"] = int(row.get("quotes", 0)) + 1
        grow = self._research_actionable_global_by_bucket.setdefault(
            (str(side), int(bucket)),
            {"quotes": 0, "maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0},
        )
        grow["quotes"] = int(grow.get("quotes", 0)) + 1
        self._research_actionable_quote_count += 1

    def _actionable_fill_snapshot(self, book_id: int) -> dict[str, float | int | str]:
        """Hierarchical post-fill quality: book first, then global distance buckets."""
        row = self._research_actionable_by_book.get(int(book_id), {})
        book_samples = int(row.get("maker_fills", 0) or 0)
        source = "BOOK"
        source_row = row

        if book_samples < self.research_actionable_fill_min_samples:
            mem = self._mem(int(book_id))
            global_rows = []
            for side, bucket in (
                ("buy", int(getattr(mem, "last_buy_dist_bucket", 0))),
                ("sell", int(getattr(mem, "last_sell_dist_bucket", 0))),
            ):
                grow = self._research_actionable_global_by_bucket.get((side, bucket), {})
                if int(grow.get("maker_fills", 0) or 0) > 0:
                    global_rows.append(grow)
            global_samples = sum(int(r.get("maker_fills", 0) or 0) for r in global_rows)
            if global_samples >= self.research_actionable_fill_min_samples:
                source = "GLOBAL_BUCKET"
                source_row = {
                    "maker_fills": global_samples,
                    "actionable": sum(int(r.get("actionable", 0) or 0) for r in global_rows),
                    "dust": sum(int(r.get("dust", 0) or 0) for r in global_rows),
                    "fill_fraction_sum": 0.0,
                }
            else:
                source = "PRIOR"
                source_row = {}

        samples = int(source_row.get("maker_fills", 0) or 0)
        actionable = int(source_row.get("actionable", 0) or 0)
        dust = int(source_row.get("dust", 0) or 0)
        prior_strength = float(self.research_actionable_fill_prior_strength)
        prior_a = float(self.research_actionable_fill_prior_actionable)
        denom = samples + prior_strength
        if denom > 0.0:
            p_actionable = (actionable + prior_strength * prior_a) / denom
            p_dust = (dust + prior_strength * (1.0 - prior_a)) / denom
        else:
            p_actionable = prior_a
            p_dust = 1.0 - prior_a
        mean_fill_fraction = (
            float(source_row.get("fill_fraction_sum", 0.0) or 0.0) / max(samples, 1)
        )
        return {
            "samples": samples,
            "book_samples": book_samples,
            "actionable": actionable,
            "dust": dust,
            "p_actionable": max(0.0, min(1.0, p_actionable)),
            "p_dust": max(0.0, min(1.0, p_dust)),
            "mean_fill_fraction": mean_fill_fraction,
            "confident": int(samples >= self.research_actionable_fill_min_samples),
            "source": source,
        }

    def _record_actionable_maker_fill(
        self,
        *,
        book_id: int,
        side: str,
        bucket: int,
        before: float,
        after: float,
        fill_qty: float,
        timestamp: int | None,
    ) -> dict[str, float | int | str | bool] | None:
        """Classify maker fills by post-fill state: ACTIONABLE/FLAT versus DUST."""
        if not self.research_actionable_fill_enabled:
            return None
        eps = self._execution_flat_epsilon()
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        if min_size <= 0.0:
            return None
        # Fill quality is defined by the POST-FILL inventory state.  A maker fill
        # is useful when it leaves either FLAT inventory or an executable position
        # >= the exchange minimum; it is harmful when it strands sub-minimum dust.
        is_flat = abs(after) < eps
        is_dust = self._is_dust_qty(after)
        is_actionable = bool(is_flat or (abs(after) + eps >= min_size))
        if not is_dust and not is_actionable:
            return None
        fill_fraction = abs(float(fill_qty)) / max(min_size, 1e-12)
        row = self._research_actionable_by_book.setdefault(
            int(book_id),
            {"maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0, "fill_fraction_sum": 0.0},
        )
        row["maker_fills"] = int(row.get("maker_fills", 0)) + 1
        row["actionable"] = int(row.get("actionable", 0)) + int(is_actionable)
        row["dust"] = int(row.get("dust", 0)) + int(is_dust)
        row["fill_qty"] = float(row.get("fill_qty", 0.0)) + abs(float(fill_qty))
        row["fill_fraction_sum"] = float(row.get("fill_fraction_sum", 0.0)) + fill_fraction

        key = (int(book_id), str(side), int(bucket))
        brow = self._research_actionable_by_bucket.setdefault(
            key, {"quotes": 0, "maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0}
        )
        brow["maker_fills"] = int(brow.get("maker_fills", 0)) + 1
        brow["actionable"] = int(brow.get("actionable", 0)) + int(is_actionable)
        brow["dust"] = int(brow.get("dust", 0)) + int(is_dust)
        brow["fill_qty"] = float(brow.get("fill_qty", 0.0)) + abs(float(fill_qty))
        grow = self._research_actionable_global_by_bucket.setdefault(
            (str(side), int(bucket)),
            {"quotes": 0, "maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0},
        )
        grow["maker_fills"] = int(grow.get("maker_fills", 0)) + 1
        grow["actionable"] = int(grow.get("actionable", 0)) + int(is_actionable)
        grow["dust"] = int(grow.get("dust", 0)) + int(is_dust)
        grow["fill_qty"] = float(grow.get("fill_qty", 0.0)) + abs(float(fill_qty))

        self._research_actionable_maker_fills += 1
        self._research_actionable_fills += int(is_actionable)
        self._research_dust_maker_fills += int(is_dust)
        snap = self._actionable_fill_snapshot(int(book_id))
        event = {
            "book_id": int(book_id),
            "side": str(side),
            "bucket": int(bucket),
            "timestamp": timestamp,
            "net_before": float(before),
            "net_after": float(after),
            "fill_qty": abs(float(fill_qty)),
            "fill_fraction": fill_fraction,
            "classification": (
                "DUST" if is_dust else ("FLAT" if is_flat else "ACTIONABLE")
            ),
            "actionable": bool(is_actionable),
            "flat": bool(is_flat),
            "dust": bool(is_dust),
            **snap,
        }
        self._emit("ACTIONABLE_FILL", tick=getattr(self, "_tick", None), **event)
        return event

    def _partial_fill_hold_expiry(
        self, state: MarketSimulationStateUpdate, book_id: int, completion_samples: int
    ) -> int:
        base = int(self.mm_expiry_period)
        if not self.research_partial_fill_hold_enabled:
            return base
        if completion_samples >= self._research_required_observation_count():
            return base
        if (
            self.research_partial_fill_hold_one_away_only
            and completion_samples != self._research_required_observation_count() - 1
        ):
            return base
        quality = self._actionable_fill_snapshot(int(book_id))
        if float(quality["p_dust"]) + 1e-12 < self.research_partial_fill_hold_min_dust_prob:
            return base
        publish = int(getattr(getattr(state, "config", None), "publish_interval", base) or base)
        return max(base, min(int(self.research_partial_fill_hold_max_ns), publish))

    def _research_required_observation_count(self) -> int:
        return required_observation_count(
            kappa_min_observations=getattr(self, "kappa_min_observations", None),
            research_target=getattr(self, "research_kappa_completion_target", None),
        )

    def _research_refresh_rolling_kappa_cache(self) -> None:
        """Refresh rolling observation authority only on data/expiry changes.

        V4.12.1 included the current tick in the cache key, forcing a full
        realized-history scan every request. This version keeps the exact same
        rolling semantics while recomputing only when history/persisted evidence
        changes or the earliest retained observation reaches the lookback edge.
        """
        history = getattr(self, "realized_pnl_history", {}) or {}
        persisted = getattr(self, "_research_persisted_observation_timestamps", {}) or {}
        now = getattr(self, "_research_last_sim_ts", None)
        if now is None:
            now = max(history.keys(), default=0)
        current = int(now or 0)
        lookback = int(getattr(self, "research_kappa_lookback_ns", 10_800_000_000_000))
        history_points = sum(len(books or {}) for books in history.values())
        persisted_points = sum(len(rows or []) for rows in persisted.values())
        latest_history_ts = int(max(history.keys(), default=0) or 0)
        source_key = (
            lookback,
            len(history),
            history_points,
            latest_history_ts,
            persisted_points,
        )
        next_expiry = getattr(self, "_research_kappa_roll_next_expiry_ts", None)
        if (
            source_key == getattr(self, "_research_kappa_roll_cache_key", None)
            and (next_expiry is None or current <= 0 or current <= int(next_expiry))
        ):
            return

        live = rolling_observation_timestamps(history, now=current, lookback_ns=lookback)
        cutoff = current - lookback if lookback > 0 else None
        merged: dict[int, set[int]] = {}
        for source in (persisted, live):
            for raw_book, raw_rows in source.items():
                try:
                    bid = int(raw_book)
                except (TypeError, ValueError):
                    continue
                for value in raw_rows or ():
                    try:
                        ts = int(value)
                    except (TypeError, ValueError):
                        continue
                    if cutoff is not None and ts < cutoff:
                        continue
                    if current > 0 and ts > current:
                        continue
                    merged.setdefault(bid, set()).add(ts)
        timestamps = {
            int(book): tuple(sorted(rows))
            for book, rows in merged.items()
            if rows
        }
        self._research_persisted_observation_timestamps = {
            int(book): list(rows) for book, rows in timestamps.items()
        }
        self._research_kappa_roll_ts_cache = timestamps
        self._research_kappa_roll_count_cache = {
            int(book): len(rows) for book, rows in timestamps.items()
        }
        persisted_points_after = sum(len(rows) for rows in timestamps.values())
        self._research_kappa_roll_cache_key = (
            lookback,
            len(history),
            history_points,
            latest_history_ts,
            persisted_points_after,
        )
        expiries = [
            int(ts) + lookback
            for rows in timestamps.values()
            for ts in rows
            if lookback > 0
        ]
        self._research_kappa_roll_next_expiry_ts = min(expiries) if expiries else None

    def _research_rolling_observation_counts(self) -> dict[int, int]:
        self._research_refresh_rolling_kappa_cache()
        return dict(getattr(self, "_research_kappa_roll_count_cache", {}) or {})

    def _research_kappa_expiry(self, book_id: int):
        self._research_refresh_rolling_kappa_cache()
        history = getattr(self, "realized_pnl_history", {}) or {}
        now = getattr(self, "_research_last_sim_ts", None)
        if now is None:
            now = max(history.keys(), default=0)
        return kappa_expiry_from_timestamps(
            int(book_id),
            (getattr(self, "_research_kappa_roll_ts_cache", {}) or {}).get(int(book_id), ()),
            now=now,
            lookback_ns=getattr(self, "research_kappa_lookback_ns", 10_800_000_000_000),
            required_observations=self._research_required_observation_count(),
            warning_horizon_frac=getattr(self, "research_kappa_expiry_warning_frac", 0.20),
        )

    def _research_kappa_universe(self, profiles=None):
        required = self._research_required_observation_count()
        if getattr(self, "research_enable_authoritative_kappa_state", True):
            counts = self._research_rolling_observation_counts()
            universe_ids = set(self._research_observation_universe(profiles).keys())
            return build_kappa_universe(counts, required, universe_ids=universe_ids)
        counts = self._research_observation_universe(profiles)
        return build_kappa_universe(counts, required)

    def _research_kappa_book(self, book_id: int):
        bid = int(book_id)
        required = self._research_required_observation_count()
        if getattr(self, "research_enable_authoritative_kappa_state", True):
            realized = int(self._research_rolling_observation_counts().get(bid, 0) or 0)
        else:
            realized = int(
                (getattr(self, "_research_realized_observations_by_book", {}) or {}).get(bid, 0)
                or 0
            )
        return kappa_book_state(bid, realized, required)

    def _research_observation_universe(self, profiles=None) -> dict[int, int]:
        """Authoritative Kappa universe including zero-observation books."""
        counts = {
            int(book): int(nobs or 0)
            for book, nobs in (getattr(self, "_research_realized_observations_by_book", {}) or {}).items()
        }
        universe_ids = set(getattr(self, "_research_book_universe_ids", set()) or set())
        try:
            universe_ids.update(int(bid) for bid in (getattr(self, "accounts", {}) or {}).keys())
        except Exception:
            pass
        for profile in list(profiles or []):
            try:
                universe_ids.add(int(getattr(profile, "book_id")))
            except (TypeError, ValueError, AttributeError):
                continue
        for bid in universe_ids:
            counts.setdefault(int(bid), 0)
        return counts

    def _research_lanes_on(self) -> bool:
        return bool(getattr(self, "research_enable_lane_scheduler", True)) and bool(
            getattr(self, "research_enable_execution_lanes", True)
        )

    def _research_dust_econ_on(self) -> bool:
        return bool(getattr(self, "research_enable_dust_economic_gate", True)) and bool(
            getattr(self, "research_enable_dust_economics", True)
        )

    @staticmethod
    def _research_bind_response_method(response, name: str, fn) -> bool:
        """Shadow a Pydantic method without BaseModel.__setattr__.

        FinanceAgentResponse.limit_order is a method, not a field. Assigning
        it the normal way raises ValueError under Pydantic v2 and aborts the
        whole handle tick. object.__setattr__ stores an instance override.
        """
        try:
            object.__setattr__(response, name, fn)
            return getattr(response, name, None) is fn
        except Exception:
            return False

    @staticmethod
    def _research_unbind_response_method(response, name: str) -> None:
        data = getattr(response, "__dict__", None)
        if isinstance(data, dict) and name in data:
            data.pop(name, None)
            return
        try:
            object.__delattr__(response, name)
        except Exception:
            pass

    def _research_lane_budgets_for_screen(self):
        budgets = getattr(self, "_research_lane_budgets", None)
        if budgets is None:
            return budgets
        if getattr(self, "research_enable_aggressive_coverage", True):
            return budgets
        return normalize_lane_budgets(
            coverage_slots=0,
            completion_slots=budgets.completion_slots,
            realization_slots=budgets.realization_slots,
            shared_overflow_slots=budgets.shared_overflow_slots,
        )

    def _research_velocity_state(self) -> VelocityState:
        state = getattr(self, "_research_velocity", None)
        if not isinstance(state, VelocityState):
            state = VelocityState()
            self._research_velocity = state
        return state

    def _research_inventory_ages(self) -> list[float]:
        ages: list[float] = []
        ticks = getattr(self, "_position_ticks", {}) or {}
        for value in ticks.values():
            try:
                age = float(value)
            except (TypeError, ValueError):
                continue
            if age > 0.0:
                ages.append(age)
        return ages

    def _research_hybrid_summary_payload(self) -> dict[str, Any]:
        vel = self._research_velocity_state()
        # VelocityState is intentionally process/run-local.  Session restore may
        # restore lifetime Kappa / round-trip counters, but those historical
        # closes must not be divided by the new process's elapsed simulation
        # time (which previously produced 65 -> 32.5 -> 21.7 ... with zero new
        # round trips).
        payload = vel.snapshot(
            simulation_time=self._research_simulation_time_s(),
            inventory_ages=self._research_inventory_ages(),
        )
        auth = getattr(self, "_research_taker_authority_counts", {}) or {}
        payload.update({
            "economic_taker_auth": int(auth.get("ECONOMIC", 0) or 0),
            "score_taker_auth": int(auth.get("SCORE", 0) or 0),
            "risk_taker_auth": int(auth.get("RISK", 0) or 0),
            "positive_ev_taker_auth": int(auth.get("POSITIVE_EV", 0) or 0),
            "actual_taker_orders": int(getattr(self, "_research_actual_taker_orders", 0) or 0),
            "actual_taker_fills": int(getattr(vel.taker, "count", 0) or 0),
            "maker_realized_pnl": float(getattr(vel.maker, "pnl", 0.0) or 0.0)
                + float(getattr(vel.competitive_maker, "pnl", 0.0) or 0.0)
                + float(getattr(vel.aggressive_maker, "pnl", 0.0) or 0.0),
            "taker_realized_pnl": float(getattr(vel.taker, "pnl", 0.0) or 0.0),
        })
        return payload

    def _research_emit_hybrid_summary(self, *, force: bool = False) -> None:
        payload = self._research_hybrid_summary_payload()
        self._emit(
            "HYBRID_SUMMARY",
            force=force,
            tick=getattr(self, "_tick", None),
            **payload,
        )

    def _research_simulation_time_s(self) -> float:
        start_ts = getattr(self, "_research_sim_start_ts", None)
        now_ts = getattr(self, "_research_last_sim_ts", None)
        if start_ts is not None and now_ts is not None:
            sim_time = max(0.0, (float(now_ts) - float(start_ts)) / 1_000_000_000.0)
            if sim_time > 0.0:
                return sim_time
        return float(max(int(getattr(self, "_tick", 0) or 0), 0))

    def _research_round_trip_velocity(self) -> float:
        # Use only round trips observed by the current runtime velocity state.
        # _research_round_trip_closes is persisted across miner reloads within
        # the same simulation and is therefore a lifetime/session counter, not
        # a valid numerator for restart-local velocity.
        return round_trip_velocity(
            int(self._research_velocity_state().completed_round_trips),
            self._research_simulation_time_s(),
        )

    def _research_activity_deficit_ratio(
        self,
        observation_counts: dict[int, int],
        required: int,
        book_count: int,
    ) -> float:
        last_map = getattr(self, "_research_last_realization_ts", {}) or {}
        now = getattr(self, "_research_last_sim_ts", None)
        stale_ns = 30_000_000_000.0
        deficit = 0
        for book, nobs in observation_counts.items():
            realized = int(nobs or 0)
            if realized <= 0:
                deficit += 1
                continue
            if realized >= int(required):
                continue
            last = last_map.get(int(book))
            if last is None or now is None:
                continue
            try:
                if float(now) - float(last) >= stale_ns:
                    deficit += 1
            except (TypeError, ValueError):
                continue
        return deficit / max(int(book_count), 1)

    def _completion_observation_count(self, book_id: int) -> int:
        return self._research_kappa_book(book_id).realized_observation_count

    def _research_observations_remaining(self, book_id: int) -> int:
        return self._research_kappa_book(book_id).observations_remaining

    def _is_kappa_completion_candidate(self, book_id: int) -> bool:
        if not self.research_kappa_completion_enabled:
            return False
        kappa = self._research_kappa_book(book_id)
        if kappa.realized_observation_count <= 0 or kappa.eligible:
            return False
        mem = self._mem(book_id)
        return float(getattr(mem, "recent_pnl", 0.0) or 0.0) >= (
            self.research_kappa_completion_recent_pnl_floor
        )

    def _research_update_ofi(self, book_id: int, book) -> Any:
        snap = self._research_ofi.update(int(book_id), extract_touch(book))
        self._research_ofi_last[int(book_id)] = snap
        micro = self._research_book_micro.setdefault(int(book_id), {})
        micro.update(snap.as_log())
        return snap

    def _research_ofi_snapshot(self, book_id: int):
        return self._research_ofi_last.get(int(book_id), UNSUPPORTED)

    def _research_ofi_fields(self, book_id: int) -> dict[str, Any]:
        return dict(self._research_ofi_snapshot(book_id).as_log())

    def _research_ofi_against(self, book_id: int, inventory_sign: float = 0.0) -> float:
        snap = self._research_ofi_snapshot(book_id)
        flow = None
        if snap.supported:
            flow = snap.ofi_fast if snap.ofi_fast is not None else snap.ofi_normalized
        return ofi_against_position(flow, inventory_sign)

    def _research_horizon_expected_markout(self, book_id: int) -> float | None:
        horizons = (getattr(self, "_research_markout_horizons", {}) or {}).get(int(book_id))
        if not horizons:
            return None
        if all(
            int((horizons.get(int(horizon)) or {}).get("n", 0) or 0) <= 0
            for horizon in MARKOUT_HORIZONS_MS
        ):
            return None
        return expected_markout_bps(horizons)

    def _research_markout_snapshot(self, book_id: int) -> tuple[float | None, int]:
        horizons = (getattr(self, "_research_markout_horizons", {}) or {}).get(int(book_id))
        if horizons:
            n = sum(
                int((horizons.get(int(horizon)) or {}).get("n", 0) or 0)
                for horizon in MARKOUT_HORIZONS_MS
            )
            if n > 0:
                return expected_markout_bps(horizons), n
        row = self._research_markout_by_book.get(int(book_id), {})
        n = int(row.get("n", 0) or 0)
        if n <= 0:
            return None, 0
        return float(row.get("sum", 0.0)) / n, n

    def _research_conservative_markout(self, book_id: int) -> float:
        """Book markout with an adverse prior when data is sparse/missing."""
        mean, samples = self._research_markout_snapshot(int(book_id))
        return conservative_expected_markout_bps(
            mean_bps=mean,
            samples=samples,
        )

    def _research_strategy_latency_ms(self) -> float | None:
        """Previous-request strategy latency EWMA for Score-EV.

        This is intentionally separate from markout compute time, quote TTL, and
        validator/simulator instruction delay. The current request is not used,
        avoiding a circular ranking dependency.
        """
        value = getattr(self, "_research_latency_ewma_ms", None)
        if value is None:
            samples = list(getattr(self, "_research_response_ms", []) or [])
            if not samples:
                return None
            value = samples[-1]
        try:
            value = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or value < 0.0:
            return None
        return value

    def _research_score_ev_for_book(
        self,
        book_id: int,
        expected_alpha: float,
        mem,
    ):
        profile = self._research_profile_for_book(book_id)
        spread_bps = 0.0
        if profile is not None:
            try:
                spread_bps = float(getattr(profile, "spread_bps", 0.0) or 0.0)
            except (TypeError, ValueError):
                spread_bps = 0.0
        haz = (getattr(self, "_research_hazard_last", {}) or {}).get(int(book_id), {})
        pred_buy = haz.get("buy")
        pred_sell = haz.get("sell")
        old = haz.get("old")
        policy_est = haz.get("policy")
        fill_old = float(getattr(mem, "fill_rate", 0.0) or 0.0)
        source_est = policy_est if policy_est is not None else old
        if source_est is not None:
            fill_old = 0.5 * (
                float(getattr(source_est, "buy", 0.0))
                + float(getattr(source_est, "sell", 0.0))
            )
        hazard_any = None
        hazard_act = None
        hazard_dust = None
        hazard_usable = False
        if pred_buy is not None or pred_sell is not None:
            anys = []
            acts = []
            dusts = []
            usable = False
            for pred in (pred_buy, pred_sell):
                if pred is None:
                    continue
                anys.append(float(pred.any_fill))
                acts.append(float(pred.actionable_fill))
                dusts.append(float(pred.dust))
                usable = usable or bool(pred.usable)
            if anys:
                hazard_any = sum(anys) / len(anys)
                hazard_act = sum(acts) / len(acts)
                hazard_dust = sum(dusts) / len(dusts)
                hazard_usable = usable
        quality = self._actionable_fill_snapshot(int(book_id))
        dust_prob = float(quality.get("p_dust", 0.0) or 0.0)
        if hazard_dust is not None and hazard_usable:
            dust_prob = max(dust_prob, float(hazard_dust))
        markout_mean, markout_n = self._research_markout_snapshot(book_id)
        expected_override = self._research_horizon_expected_markout(book_id)
        inv_util = 0.0
        inventory_blocked = False
        try:
            snap = self._position_tracker_snapshot(int(book_id))
            cap = max(float(getattr(self, "max_inventory_base", 1.0) or 1.0), 1e-9)
            inv_util = min(1.0, abs(float(getattr(snap, "net_qty", 0.0) or 0.0)) / cap)
            inventory_blocked = inv_util + 1e-12 >= 1.0
        except Exception:
            pass
        toxic = int(book_id) in getattr(self, "_research_parked_dust", {})
        market_regime = str(getattr(self, "_research_market_regime", "") or "").upper()
        unsafe = market_regime in {"TOXIC"}
        inventory_state = "FLAT"
        if inventory_blocked:
            inventory_state = "BLOCKED"
        elif toxic:
            inventory_state = "DUST"
        elif inv_util > 0.05:
            inventory_state = "LONG"
        recent_pnl = None
        try:
            recent_pnl = float(getattr(mem, "recent_pnl", 0.0) or 0.0)
        except (TypeError, ValueError):
            recent_pnl = None
        last_ts = (getattr(self, "_research_last_realization_ts", {}) or {}).get(int(book_id))
        now_ts = getattr(self, "_research_last_sim_ts", None)
        side = "MM"
        net = 0.0
        if inv_util > 0.05:
            try:
                net = float(self._position_tracker_snapshot(int(book_id)).net_qty)
            except Exception:
                net = 0.0
            side = "SELL" if net > 0 else "BUY"
            if inventory_state == "FLAT":
                inventory_state = "LONG" if net > 0 else "SHORT"
        ofi_against = self._research_ofi_against(int(book_id), net)
        book_realization_time = None
        realization_time_reference = None
        try:
            book_realization_time, realization_time_reference = (
                self._research_velocity_state().expected_realization_time(int(book_id))
            )
        except Exception:
            pass
        return score_velocity_priority(
            book=int(book_id),
            side=side,
            alpha=float(expected_alpha),
            fill_prob_old=float(fill_old),
            fill_prob_hazard=hazard_any,
            actionable_fill_hazard=hazard_act,
            hazard_usable=hazard_usable,
            learned_actionable_p=float(quality.get("p_actionable", 0.0) or 0.0),
            learned_actionable_samples=int(quality.get("samples", 0) or 0),
            dust_prob=dust_prob,
            spread_capture_bps=0.5 * max(0.0, spread_bps),
            markout_mean_bps=markout_mean,
            markout_samples=markout_n,
            expected_markout_override=expected_override,
            ofi_against=ofi_against,
            fees_bps=self._research_lifecycle_entry_cost_bps(int(book_id), spread_bps),
            realized_observation_count=self._completion_observation_count(book_id),
            required=self._research_required_observation_count(),
            inventory_util=inv_util,
            latency_ms=self._research_strategy_latency_ms(),
            last_realization_time=None if last_ts is None else float(last_ts),
            now=None if now_ts is None else float(now_ts),
            recent_realized_pnl=recent_pnl,
            inventory_state=inventory_state,
            toxic=bool(toxic),
            inventory_blocked=bool(inventory_blocked),
            unsafe=bool(unsafe),
            min_trading_ev=float(self.research_score_ev_min_trading),
            min_fill_samples=int(self.research_score_ev_min_fill_samples),
            min_markout_samples=int(self.research_score_ev_min_markout_samples),
            one_away_weight=float(self.research_score_ev_one_away_weight),
            two_away_weight=float(self.research_score_ev_two_away_weight),
            new_book_weight=float(self.research_score_ev_new_book_weight),
            dust_target=float(self.research_dust_risk_target),
            dust_weight=float(self.research_score_ev_dust_weight),
            volume_cap_headroom=self._research_volume_cap_headroom(
                getattr(self, "_research_volume_cap_state", None),
                int(book_id),
            ),
            expected_realization_time=book_realization_time,
            realization_time_reference=realization_time_reference,
            score_velocity_weight=float(self.research_score_velocity_weight),
            enable_score_velocity=bool(self.research_enable_score_velocity),
        )

    def _global_book_rank(self, expected_alpha: float, mem) -> float:
        """V4.3 Score-EV rank, or V4.2 legacy rank when the feature flag is off."""
        book_id = getattr(mem, "_research_book_id", None)
        if book_id is None:
            return super()._global_book_rank(expected_alpha, mem)
        book_id = int(book_id)

        if not getattr(self, "research_enable_score_ev", False):
            return self._research_legacy_book_rank(expected_alpha, mem, book_id)

        breakdown = self._research_score_ev_for_book(book_id, expected_alpha, mem)
        self._research_score_ev_last[book_id] = breakdown
        try:
            self._emit(
                "RANK",
                force=True,
                tick=getattr(self, "_tick", None),
                **breakdown.as_log(),
            )
        except Exception:
            pass
        try:
            kappa = self._research_kappa_book(book_id)
            self._emit(
                "KAPPA",
                force=True,
                tick=getattr(self, "_tick", None),
                **kappa.as_log(),
                obs=kappa.realized_observation_count,
                remaining=kappa.observations_remaining,
                required=kappa.required_observations,
                completion_value=float(breakdown.completion_value),
                trading_ev=float(breakdown.trading_ev),
                final_priority=(
                    None if not math.isfinite(breakdown.final_priority)
                    else float(breakdown.final_priority)
                ),
                lane=str(breakdown.lane),
                last_realization_time=breakdown.last_realization_time,
                recent_realized_pnl=breakdown.recent_realized_pnl,
                inventory_state=breakdown.inventory_state,
                reject_reason=breakdown.reject_reason,
            )
        except Exception:
            pass
        chosen = select_rank(
            enable_score_ev=True,
            score_ev=breakdown,
            legacy_rank=super()._global_book_rank(expected_alpha, mem),
        )
        if chosen is None:
            return float("-1e9")
        rank = float(chosen)
        try:
            expiry = self._research_kappa_expiry(book_id)
            urgency = float(getattr(expiry, "expiry_urgency", 0.0) or 0.0)
            if urgency > 0.0:
                if bool(getattr(expiry, "qualified", False)):
                    # Preserve already-good Kappa books before their third-most-recent
                    # observation rolls out of the validator window. Economics still
                    # gates the quote; this is ranking only.
                    rank += float(self.research_kappa_expiry_rank_bonus) * urgency
                elif bool(getattr(self, "research_deadline_scheduler_enabled", True)):
                    # V4.12.8: incomplete progress can also expire.  Bias deep
                    # ranking toward the ONE_AWAY/TWO_AWAY books whose existing
                    # observations are closest to falling out of the rolling window.
                    rank += float(getattr(self, "research_deadline_rank_bonus", 0.25)) * urgency
        except Exception:
            pass
        return rank

    def _research_legacy_book_rank(self, expected_alpha: float, mem, book_id: int) -> float:
        """V4.2 rank kept for A/B when research_enable_score_ev=0."""
        base_rank = super()._global_book_rank(expected_alpha, mem)
        target = self._research_required_observation_count()
        if self.research_kappa_completion_enabled and self._is_kappa_completion_candidate(book_id):
            samples = self._completion_observation_count(book_id)
            denom = max(1, target - 1)
            progress = max(0.0, min(1.0, samples / denom))
            base_rank += self.research_kappa_completion_rank_bonus * progress

        if not self.research_actionable_fill_enabled:
            return base_rank
        quality = self._actionable_fill_snapshot(book_id)
        samples = int(quality["samples"])
        confident = samples >= self.research_actionable_fill_min_samples
        if confident:
            p_actionable = float(quality["p_actionable"])
            p_dust = float(quality["p_dust"])
            quality_adjust = self.research_actionable_fill_rank_weight * (
                p_actionable - self.research_actionable_fill_prior_actionable
            )
            dust_penalty = self.research_dust_risk_rank_penalty * max(
                0.0, p_dust - self.research_dust_risk_target
            )
            base_rank += quality_adjust - dust_penalty

        if (
            self.research_kappa_completion_enabled
            and self._is_kappa_completion_candidate(book_id)
            and self._completion_observation_count(book_id) == target - 1
        ):
            quality_scale = (
                0.50 + 0.50 * float(quality["p_actionable"])
                if confident else 0.75
            )
            base_rank += self.research_kappa_one_away_bonus * quality_scale
        return base_rank

    def _research_emit_scheduler(self, stats: dict, selection) -> None:
        universe = self._research_kappa_universe(getattr(selection, "profiles", None))
        buckets = universe.bucket_counts()
        attempts = int(getattr(self, "_research_completion_quote_attempts", 0) or 0)
        successes = int(getattr(self, "_research_completion_quote_successes", 0) or 0)
        success_rate = (successes / attempts) if attempts > 0 else 0.0
        velocity = self._research_round_trip_velocity()
        allocation = getattr(self, "_research_last_lanes", None)
        acquisition_grants = score_acquisition_grants(allocation)
        payload = {
            **buckets,
            "kappa_eligible": universe.eligible_count,
            "score_acquisition_mode": int(bool(getattr(self, "_research_score_acquisition_mode", False))),
            "score_acquisition_grants": len(acquisition_grants),
            "score_acquisition_version": self.RESEARCH_SCORE_ACQUISITION_VERSION,
            "kappa_completion_attempts": attempts,
            "kappa_completion_successes": successes,
            "completion_attempts": attempts,
            "completion_successes": successes,
            "kappa_completion_success_rate": success_rate,
            "round_trip_velocity": velocity,
            "enable_score_ev": int(bool(self.research_enable_score_ev)),
            "kappa_scheduler_version": getattr(
                self, "RESEARCH_KAPPA_SCHEDULER_VERSION", "kappa_completion_v3"
            ),
            "kappa_state_version": getattr(
                self, "RESEARCH_KAPPA_STATE_VERSION", KAPPA_STATE_VERSION
            ),
            "mm_candidates": stats.get("mm_candidates"),
        }
        if allocation is not None:
            payload.update(allocation.as_log())
        used = getattr(self, "_research_lane_used", {}) or {}
        payload.update({
            "coverage_exec_used": int(used.get(EXEC_LANE_COVERAGE, 0) or 0),
            "completion_exec_used": int(used.get(EXEC_LANE_COMPLETION, 0) or 0),
            "realization_exec_used": int(used.get(LANE_REALIZATION, 0) or 0),
            "overflow_exec_used": int(getattr(self, "_research_lane_overflow_used", 0) or 0),
            "lanes_version": getattr(self, "RESEARCH_LANES_VERSION", "execution_lanes_v1"),
        })
        if isinstance(stats, dict):
            stats.update({f"research_{k}": v for k, v in payload.items()})
        self._emit("SCHED", force=True, tick=getattr(self, "_tick", None), **payload)

    def _is_compactable_dust(self, net_base: float) -> bool:
        if not self.research_dust_compact_enabled or not self._is_dust_qty(net_base):
            return False
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        if min_size <= 0.0:
            return False
        abs_base = abs(float(net_base))
        threshold = self.research_dust_compact_min_fraction * min_size
        return abs_base > threshold + self._execution_flat_epsilon()

    def _dust_compaction_learning_snapshot(self, book_id: int) -> dict[str, float | int]:
        row = self._research_dust_compact_learning.get(int(book_id), {})
        attempts = int(row.get("attempts", 0) or 0)
        successes = int(row.get("successful_attempts", 0) or 0)
        prior_strength = float(self.research_dust_compact_prior_strength)
        denom = attempts + prior_strength
        p_fill = (
            (successes + prior_strength * self.research_dust_compact_prior_fill) / denom
            if denom > 0.0 else self.research_dust_compact_prior_fill
        )
        return {
            "attempts": attempts,
            "successful_attempts": successes,
            "p_fill": max(0.0, min(1.0, p_fill)),
            "failure_streak": int(row.get("failure_streak", 0) or 0),
            "last_attempt_tick": int(row.get("last_attempt_tick", -1) or -1),
            "next_allowed_tick": int(row.get("next_allowed_tick", 0) or 0),
        }

    def _record_dust_compaction_attempt(self, book_id: int) -> None:
        now = int(getattr(self, "_tick", 0) or 0)
        row = self._research_dust_compact_learning.setdefault(
            int(book_id),
            {"attempts": 0, "successful_attempts": 0, "failure_streak": 0,
             "last_attempt_tick": -1, "last_success_attempt_tick": -1,
             "next_allowed_tick": 0},
        )
        prev = int(row.get("last_attempt_tick", -1) or -1)
        success_prev = int(row.get("last_success_attempt_tick", -1) or -1)
        if prev >= 0 and success_prev != prev:
            row["failure_streak"] = int(row.get("failure_streak", 0) or 0) + 1
        row["attempts"] = int(row.get("attempts", 0) or 0) + 1
        row["last_attempt_tick"] = now
        cooldown = min(
            self.research_dust_compact_max_cooldown_ticks,
            self.research_dust_compact_cooldown_ticks
            * max(1, int(row.get("failure_streak", 0) or 0) + 1),
        )
        row["next_allowed_tick"] = now + int(cooldown)

    def _record_dust_compaction_success(self, book_id: int) -> None:
        submitted_tick = self._research_dust_compact_active.get(int(book_id))
        if submitted_tick is None:
            return
        row = self._research_dust_compact_learning.setdefault(
            int(book_id),
            {"attempts": 0, "successful_attempts": 0, "failure_streak": 0,
             "last_attempt_tick": int(submitted_tick), "last_success_attempt_tick": -1,
             "next_allowed_tick": 0},
        )
        if int(row.get("last_success_attempt_tick", -1) or -1) != int(submitted_tick):
            row["successful_attempts"] = int(row.get("successful_attempts", 0) or 0) + 1
            row["last_success_attempt_tick"] = int(submitted_tick)
        row["failure_streak"] = 0
        row["next_allowed_tick"] = int(getattr(self, "_tick", 0) or 0) + (
            self.research_dust_compact_cooldown_ticks
        )

    def _select_dust_compaction_books(self, state: MarketSimulationStateUpdate) -> set[int]:
        """Rank only theorem-safe dust, with bounded retry cooldown and learned fill quality."""
        if not self.research_dust_compact_enabled:
            return set()
        tick = int(getattr(self, "_tick", 0) or 0)
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        rows: list[tuple[float, float, int, int]] = []
        for book_id, info in self._research_parked_dust.items():
            qty = float(info.get("net_base", 0.0) or 0.0)
            if not self._is_compactable_dust(qty):
                continue
            if book_id not in getattr(state, "books", {}):
                continue
            first_tick = int(info.get("first_tick", tick))
            age = max(0, tick - first_tick)
            learn = self._dust_compaction_learning_snapshot(int(book_id))
            if (
                self.research_dust_compact_adaptive
                and tick < int(learn["next_allowed_tick"])
            ):
                self._research_dust_compact_cooldown_skips += 1
                continue
            size_fraction = abs(qty) / max(min_size, 1e-12)
            age_score = min(1.0, age / max(float(self.research_dust_warn_ticks), 1.0))
            score = (
                2.0 * float(learn["p_fill"])
                + 0.55 * min(1.0, size_fraction)
                + 0.45 * age_score
            )
            rows.append((score, abs(qty), age, int(book_id)))
        rows.sort(reverse=True)
        return {
            book_id
            for _score, _abs_qty, _age, book_id
            in rows[: self.research_dust_compact_books_per_tick]
        }

    def _dust_compaction_safe_for_any_fill(self, net_base: float) -> bool:
        """Proof condition for q -> q - sign(q)*f, 0<=f<=min_size."""
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        q = abs(float(net_base))
        return min_size > 0.0 and q > 0.5 * min_size and q < min_size

    def _sparse_active_tier_enabled(self, tier: str) -> bool:
        tier_u = str(tier or "").upper()
        return (
            (tier_u == "YELLOW" and self.research_yellow_sparse_active)
            or (tier_u == "GREEN" and self.research_green_sparse_active)
        )

    def _is_dust_qty(self, net_base: float) -> bool:
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        abs_base = abs(float(net_base))
        return (
            self.research_dust_safe_close
            and min_size > 0.0
            and abs_base >= self._execution_flat_epsilon()
            and abs_base + 1e-12 < min_size
        )

    def _refresh_dust_state(
        self,
        book_id: int,
        net_base: float,
        *,
        emit: bool = True,
    ) -> bool:
        """Park exchange-uncloseable residuals without hiding exact inventory.

        V4.1 Strict does not synthesize an exchange-illegal exact close and does
        not add fresh risk merely to make dust executable. Dust remains in exact
        accounting, is removed from the finite management queue, and is
        quarantined from fresh MM/maintenance orders.
        """
        if not self.research_dust_park_enabled:
            self._research_parked_dust.pop(book_id, None)
            return False

        tick = int(getattr(self, "_tick", 0) or 0)
        qty = float(net_base)
        is_dust = self._is_dust_qty(qty)
        prior = self._research_parked_dust.get(book_id)

        if not is_dust:
            if prior is not None:
                self._research_dust_releases += 1
                self._research_parked_dust.pop(book_id, None)
                if emit:
                    self._emit(
                        "POSITION_GUARD",
                        tick=tick,
                        book_id=book_id,
                        reason="DUST_RELEASED",
                        net_base=qty,
                        min_order_size=self._research_exchange_min_order_size,
                        first_tick=prior.get("first_tick"),
                        age_ticks=max(0, tick - int(prior.get("first_tick", tick))),
                        parked=False,
                    )
            return False

        if prior is None:
            prior = {
                "first_tick": tick,
                "last_tick": tick,
                "last_emit_tick": tick,
                "net_base": qty,
            }
            self._research_parked_dust[book_id] = prior
            self._research_dust_entries += 1
            self._research_dust_blocks += 1
            if emit:
                self._emit(
                    "POSITION_GUARD",
                    tick=tick,
                    book_id=book_id,
                    reason="DUST_POSITION",
                    net_base=qty,
                    min_order_size=self._research_exchange_min_order_size,
                    first_tick=tick,
                    age_ticks=0,
                    parked=True,
                    stale=False,
                )
            return True

        prior["last_tick"] = tick
        prior["net_base"] = qty
        age = max(0, tick - int(prior.get("first_tick", tick)))
        last_emit = int(prior.get("last_emit_tick", tick))
        if emit and tick - last_emit >= self.research_dust_heartbeat_ticks:
            prior["last_emit_tick"] = tick
            self._research_dust_heartbeats += 1
            self._emit(
                "POSITION_GUARD",
                tick=tick,
                book_id=book_id,
                reason="DUST_HEARTBEAT",
                net_base=qty,
                min_order_size=self._research_exchange_min_order_size,
                first_tick=prior.get("first_tick"),
                age_ticks=age,
                parked=True,
                stale=(age >= self.research_dust_warn_ticks),
            )
        return True

    def classify_book_archetype(
        self,
        profile: BookProfile,
        regime: MarketRegime,
    ) -> BookArchetype:
        spread_bps = float(getattr(profile, "spread_bps", 0.0) or 0.0)
        trade_rate = float(getattr(profile, "trade_rate", 0.0) or 0.0)
        volatility = float(getattr(profile, "volatility", 0.0) or 0.0)
        imbalance = float(getattr(profile, "imbalance", 0.0) or 0.0)
        predict_score = float(getattr(profile, "predict_score", 0.0) or 0.0)
        tier = str(getattr(profile, "tier", "")).upper()
        overlay = str(getattr(regime, "scoring_overlay", "")).upper()
        score_regime = str(
            getattr(regime, "research_score_regime", None)
            or getattr(self, "_research_score_regime", "")
            or ""
        ).upper()
        profile_book_id = getattr(profile, "book_id", None)

        stress_cutoff = (
            self._research_stress_spread_bps
            if self.research_adaptive_spread_thresholds
            else float(self.archetype_stressed_spread_bps)
        )
        bootstrap_inactive = (
            self.research_inactive_bootstrap
            and self.research_bootstrap_dead_as_mm
            and tier == "INACTIVE"
            and score_acquisition_granted(
                profile_book_id,
                allocation=getattr(self, "_research_last_lanes", None),
                score_regime=score_regime,
                scoring_overlay=overlay,
            )
        )
        parked_dust = (
            self.research_dust_park_enabled
            and profile_book_id in self._research_parked_dust
        )

        # Parked dust is an execution quarantine, not a market-risk opinion.
        # Represent it as TOXIC_BOOK only to guarantee inherited maintenance/MM
        # paths cannot add a new order; archetype_source preserves the semantics.
        if parked_dust:
            archetype: BookArchetype = "TOXIC_BOOK"
            source = "PARKED_DUST"
        # Local risk always wins. Global STRESSED is deliberately not a local
        # archetype condition; otherwise one regime bit poisons all books.
        elif spread_bps >= stress_cutoff:
            archetype: BookArchetype = "STRESSED"
            source = "LOCAL_SPREAD"
        elif bootstrap_inactive:
            # Cold books often have trade_rate~=0 precisely because they have no
            # realized history yet. Calling all of them DEAD creates a second
            # bootstrap deadlock. Preserve genuine local risk, but let ordinary
            # cold books enter conservative MM long enough to acquire samples.
            extreme_vol = (
                self.research_bootstrap_extreme_vol_mult
                * max(float(self.archetype_vol_threshold), 1e-12)
            )
            if volatility >= extreme_vol:
                archetype = "TOXIC_BOOK"
                source = "BOOTSTRAP_EXTREME_VOL"
            elif abs(imbalance) >= self.archetype_wall_imbalance:
                archetype = "WALL_BOOK"
                source = "BOOTSTRAP_WALL"
            elif (
                volatility >= self.archetype_vol_threshold
                and abs(predict_score) >= self.direction_threshold
            ):
                archetype = "TREND_BOOK"
                source = "BOOTSTRAP_VOL_DIRECTION"
            elif abs(predict_score) >= self.direction_threshold:
                archetype = "TREND_BOOK"
                source = "BOOTSTRAP_DIRECTION"
            else:
                archetype = "MM_BOOK"
                source = "INACTIVE_BOOTSTRAP"
        elif (
            self._sparse_active_tier_enabled(tier)
            and trade_rate < self.archetype_dead_trade_rate
        ):
            # V4.1 Strict bridge: a book that already earned YELLOW history but has
            # sparse tape is not identical to an unproven DEAD book. Preserve
            # genuine local risk first, then directional/wall information.
            extreme_vol = (
                self.research_bootstrap_extreme_vol_mult
                * max(float(self.archetype_vol_threshold), 1e-12)
            )
            if volatility >= extreme_vol:
                archetype = "TOXIC_BOOK"
                source = "ACTIVE_SPARSE_EXTREME_VOL"
            elif abs(imbalance) >= self.archetype_wall_imbalance:
                archetype = "WALL_BOOK"
                source = "ACTIVE_SPARSE_WALL"
            elif abs(predict_score) >= self.direction_threshold:
                archetype = "TREND_BOOK"
                source = "ACTIVE_SPARSE_TREND"
            else:
                archetype = "MM_BOOK"
                source = "ACTIVE_SPARSE_MM"
        elif trade_rate < self.archetype_dead_trade_rate:
            archetype = "DEAD_BOOK"
            source = "DEAD_TRADE_RATE"
        elif spread_bps < self.archetype_mm_spread_bps:
            archetype = "MM_BOOK"
            source = "NARROW_MM"
        elif abs(imbalance) >= self.archetype_wall_imbalance:
            archetype = "WALL_BOOK"
            source = "WALL_IMBALANCE"
        elif (
            volatility >= self.archetype_vol_threshold
            and abs(predict_score) >= self.direction_threshold
        ):
            archetype = "TREND_BOOK"
            source = "VOL_AND_DIRECTION"
        elif volatility >= self.archetype_vol_threshold:
            archetype = "TOXIC_BOOK"
            source = "HIGH_VOL"
        elif abs(predict_score) >= self.direction_threshold:
            archetype = "TREND_BOOK"
            source = "DIRECTION"
        else:
            archetype = "MM_BOOK" if self.research_neutral_fallback else "TOXIC_BOOK"
            source = (
                "NEUTRAL_FALLBACK"
                if self.research_neutral_fallback
                else "LEGACY_TOXIC_FALLBACK"
            )

        if self.debug_enabled:
            record = self._book_record(profile.book_id)
            record["archetype"] = archetype
            record["archetype_source"] = source
            record["profile_spread_bps"] = spread_bps
            record["volatility"] = volatility
            record["trade_rate"] = trade_rate
            record["imbalance"] = imbalance
            record["stress_spread_bps"] = stress_cutoff
            record["toxic_spread_bps"] = self._research_toxic_spread_bps
            record["stressed_by_spread"] = spread_bps >= stress_cutoff
            record["stressed_by_regime"] = False
            record["legacy_stressed_by_regime"] = (
                str(getattr(regime, "mode", "")).upper() == "STRESSED"
            )
            record["bootstrap_inactive"] = bootstrap_inactive
            record["parked_dust"] = parked_dust
            record["dead_trade_rate_hit"] = trade_rate < self.archetype_dead_trade_rate
            record["active_sparse"] = (
                self._sparse_active_tier_enabled(tier)
                and trade_rate < self.archetype_dead_trade_rate
            )
            record["active_sparse_tier"] = tier if record["active_sparse"] else None
        return archetype

    def is_toxic_book(
        self,
        book_id: int,
        profile: BookProfile,
        archetype: BookArchetype,
    ) -> bool:
        dust_info = self._research_parked_dust.get(book_id)
        if self.research_dust_park_enabled and dust_info is not None:
            if self.debug_enabled:
                record = self._book_record(book_id)
                record["dust_position"] = True
                record["dust_quarantine"] = True
                record["dust_qty"] = abs(float(dust_info.get("net_base", 0.0)))
                record["toxic"] = False
            # Parent Strategy1 exposes a boolean pre-candidate exclusion hook.
            # Use it as quarantine transport; emitted diagnostics rewrite this
            # to DUST_QUARANTINE so parked dust is not called genuine toxicity.
            return True

        mem = self._mem(book_id)
        spread_bps = self._profile_float(profile, "spread_bps")
        toxic_cutoff = (
            self._research_toxic_spread_bps
            if self.research_adaptive_spread_thresholds
            else float(self.toxic_spread_bps)
        )

        toxic_loss = mem.loss_streak >= self.toxic_loss_streak
        pnl_samples = int(self._research_round_trip_samples_by_book.get(book_id, 0))
        toxic_pnl_raw = mem.recent_pnl < self.toxic_recent_pnl
        toxic_pnl = toxic_pnl_raw and (
            pnl_samples >= self.research_toxic_pnl_min_samples
            or mem.recent_pnl <= self.research_toxic_pnl_hard_floor
        )
        toxic_spread = (
            spread_bps is not None and spread_bps > toxic_cutoff
        )
        toxic_archetype = archetype in ("STRESSED", "TOXIC_BOOK")
        toxic_red_tier = str(getattr(profile, "tier", "")).upper() == "RED"

        toxic = any((
            toxic_loss,
            toxic_pnl,
            toxic_spread,
            toxic_archetype,
            toxic_red_tier,
        ))

        if self.debug_enabled:
            record = self._book_record(book_id)
            record["toxic"] = toxic
            record["loss_streak"] = mem.loss_streak
            record["recent_pnl"] = mem.recent_pnl
            record["toxic_loss"] = toxic_loss
            record["toxic_pnl"] = toxic_pnl
            record["toxic_pnl_raw"] = toxic_pnl_raw
            record["toxic_pnl_samples"] = pnl_samples
            record["toxic_pnl_min_samples"] = self.research_toxic_pnl_min_samples
            record["toxic_pnl_hard_floor"] = self.research_toxic_pnl_hard_floor
            record["toxic_spread"] = toxic_spread
            record["toxic_archetype"] = toxic_archetype
            record["toxic_red_tier"] = toxic_red_tier
            record["toxic_spread_bps"] = toxic_cutoff
        return toxic

    def _net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        """V2 inventory snapshot in one unit system: signed base utilization."""
        if not self.research_fix_inventory_util:
            return super()._net_inventory(book_id, mid)
        if mid <= 0:
            return InventorySnapshot(0.0, 0.0, "FLAT", None, None, 0)

        tracker = self._position_tracker_snapshot(book_id)
        net_base = float(tracker.net_qty)
        max_base = max(float(self.max_inventory_base), 1e-9)
        signed_util = net_base / max_base
        flat_eps = self._execution_flat_epsilon()

        if abs(net_base) < flat_eps:
            band = "FLAT"
            # Empty defaultdict entries are not positions. Prune them so
            # diagnostics and any legacy callers cannot mistake history for risk.
            positions = getattr(self, "_open_positions", None)
            if isinstance(positions, dict):
                pos = positions.get(book_id)
                if pos is not None and not pos.get("longs") and not pos.get("shorts"):
                    positions.pop(book_id, None)
            self._position_ticks.pop(book_id, None)
            self._research_position_tick_seen.pop(book_id, None)
            self._inventory_reason.pop(book_id, None)
        else:
            band = (
                "MAX_LONG" if net_base >= max_base
                else "MAX_SHORT" if net_base <= -max_base
                else "LONG" if net_base > 0.0
                else "SHORT"
            )
            # Strategy1_Debug can query inventory more than once per request.
            # Age must advance once per research tick, not once per diagnostic call.
            current_tick = int(getattr(self, "_tick", 0) or 0)
            if self._research_position_tick_seen.get(book_id) != current_tick:
                self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
                self._research_position_tick_seen[book_id] = current_tick

        position_ticks = self._position_ticks.get(book_id, 0)
        vwap = tracker.vwap_entry
        unrealized_bps: float | None = None
        if vwap and vwap > 0:
            if net_base > 0:
                unrealized_bps = ((mid - vwap) / vwap) * 10_000.0
            elif net_base < 0:
                unrealized_bps = ((vwap - mid) / vwap) * 10_000.0

        inventory = InventorySnapshot(
            net_base=net_base,
            inventory_ratio=signed_util,
            band=band,
            vwap_entry=vwap,
            unrealized_bps=unrealized_bps,
            position_ticks=position_ticks,
            opened_at_ns=tracker.opened_at_ns,
            reason=self._inventory_reason.get(book_id, "UNKNOWN"),
        )
        try:
            setattr(inventory, "_research_book_id", int(book_id))
        except Exception:
            pass
        if self.debug_enabled:
            self._book_record(book_id)["inventory"] = {
                "net_base": inventory.net_base,
                "ratio": inventory.inventory_ratio,
                "signed_util": signed_util,
                "band": inventory.band,
                "vwap_entry": inventory.vwap_entry,
                "unrealized_bps": inventory.unrealized_bps,
                "position_ticks": inventory.position_ticks,
                "reason": inventory.reason,
            }
        self._refresh_dust_state(book_id, net_base, emit=True)
        return inventory

    def _inventory_util(self, inventory: InventorySnapshot) -> float:
        if not self.research_fix_inventory_util:
            return super()._inventory_util(inventory)
        return abs(float(inventory.net_base)) / max(float(self.max_inventory_base), 1e-9)

    def _research_inventory_state_inputs(self, book_id: int | None, inventory) -> dict[str, Any]:
        bid = None if book_id is None else int(book_id)
        ev = None if bid is None else (getattr(self, "_research_score_ev_last", {}) or {}).get(bid)
        markout = 0.0
        as_risk = 0.0
        if ev is not None:
            markout = float(getattr(ev, "expected_markout_bps", 0.0) or 0.0)
            as_risk = float(getattr(ev, "adverse_selection_risk", 0.0) or 0.0)
        elif bid is not None:
            mean, n = self._research_markout_snapshot(bid)
            if mean is not None and n > 0:
                markout = float(mean)
        remaining = 0 if bid is None else self._research_observations_remaining(bid)
        ofi_val = None
        if bid is not None:
            snap = self._research_ofi_snapshot(bid)
            if getattr(snap, "supported", False):
                ofi_val = snap.ofi_fast if snap.ofi_fast is not None else snap.ofi_normalized
        recent = None
        failed = None
        if bid is not None:
            mem = self._mem(bid)
            try:
                recent = float(getattr(mem, "recent_pnl", 0.0) or 0.0)
            except (TypeError, ValueError, AttributeError):
                recent = None
            try:
                failed = int(getattr(mem, "loss_streak", 0) or 0) >= 2
            except (TypeError, ValueError, AttributeError):
                failed = None
        vol = 0.0
        if bid is not None:
            profile = self._research_profile_for_book(bid)
            try:
                vol = float(getattr(profile, "volatility", 0.0) or 0.0) if profile is not None else 0.0
            except (TypeError, ValueError):
                vol = 0.0
        last = None if bid is None else (getattr(self, "_research_realization_last", {}) or {}).get(bid)
        urgency = float(getattr(last, "exit_urgency", 0.0) or 0.0) if last is not None else 0.0
        return {
            "urgency": urgency,
            "inventory_ratio": self._inventory_util(inventory),
            "band": getattr(inventory, "band", None),
            "inventory_size": abs(float(getattr(inventory, "net_base", 0.0) or 0.0)),
            "inventory_age": float(getattr(inventory, "position_ticks", 0) or 0),
            "unrealized_pnl": getattr(inventory, "unrealized_bps", None),
            "volatility": vol,
            "ofi": ofi_val,
            "expected_markout": markout,
            "kappa_need": kappa_completion_need(
                remaining, getattr(inventory, "unrealized_bps", None),
            ),
            "volume_cap_headroom": (
                1.0 if bid is None else self._research_volume_cap_headroom(
                    getattr(self, "_research_volume_cap_state", None), bid,
                )
            ),
            "recent_realized_pnl": recent,
            "adverse_selection_risk": as_risk,
            "realization_failed": failed,
            "inventory_sign": float(getattr(inventory, "net_base", 0.0) or 0.0),
        }

    def _research_inventory_state(self, book_id: int | None, inventory):
        if not getattr(self, "research_enable_inventory_state_v2", True):
            return None
        hard = str(getattr(inventory, "band", "")).upper() in {"MAX_LONG", "MAX_SHORT"}
        state = classify_inventory_state(
            **self._research_inventory_state_inputs(book_id, inventory),
            hard_emergency=hard,
        )
        policy = inventory_state_policy(state, hard_safety=hard)
        if book_id is not None:
            tick = getattr(self, "_tick", None)
            prev = (getattr(self, "_research_inventory_state_last", {}) or {}).get(int(book_id))
            row = {"state": state, "tick": tick, **policy.as_log()}
            self._research_inventory_state_last[int(book_id)] = row
            if prev is None or prev.get("state") != state or prev.get("tick") != tick:
                try:
                    self._emit(
                        "INVENTORY_STATE",
                        force=True,
                        tick=tick,
                        book=int(book_id),
                        **row,
                    )
                except Exception:
                    pass
        return policy

    def _inventory_needs_management(self, inventory: InventorySnapshot) -> bool:
        if inventory.band in ("MAX_LONG", "MAX_SHORT"):
            return True
        if getattr(self, "research_enable_inventory_state_v2", True):
            book_id = getattr(inventory, "_research_book_id", None)
            policy = self._research_inventory_state(book_id, inventory)
            if policy is not None and policy.state in {
                STATE_DEFENSIVE, STATE_EXIT_ONLY, STATE_EMERGENCY,
            }:
                return True

        abs_base = abs(float(inventory.net_base))
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        eps = self._execution_flat_epsilon()

        # V4.1 Strict: only mathematically safe, explicitly selected dust enters
        # management. Tiny/nonselected dust remains parked and consumes no slot.
        if (
            self.research_dust_park_enabled
            and self._is_dust_qty(inventory.net_base)
        ):
            book_id = getattr(inventory, "_research_book_id", None)
            return (
                book_id is not None
                and int(book_id) in self._research_dust_compact_ids_this_tick
                and self._dust_compaction_safe_for_any_fill(inventory.net_base)
            )

        if (
            self._research_bootstrap_active
            and self.research_bootstrap_manage_min_clip
            and abs_base >= eps
            and (min_size <= 0.0 or abs_base + 1e-12 >= min_size)
        ):
            return True
        if getattr(self, "research_enable_realization", True):
            return inventory_should_manage(
                inventory_ratio=self._inventory_util(inventory),
                inventory_age=float(getattr(inventory, "position_ticks", 0) or 0),
                unrealized_pnl=inventory.unrealized_bps,
                band=getattr(inventory, "band", None),
                close_threshold=float(self.inventory_close_threshold),
                realize_age_ticks=float(self.research_realize_age_ticks),
                profit_realize_bps=float(self.research_profit_realize_bps),
                toxic_realize_bps=float(self.research_toxic_realize_bps),
            )
        return self._inventory_util(inventory) >= float(self.inventory_close_threshold)

    def _inventory_urgency(
        self,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ) -> float:
        book_id = getattr(inventory, "_research_book_id", None)
        if book_id is not None:
            last = (getattr(self, "_research_realization_last", {}) or {}).get(int(book_id))
            if last is not None:
                return float(getattr(last, "exit_urgency", 0.0) or 0.0)
        if not getattr(self, "research_enable_realization", True):
            return super()._inventory_urgency(inventory, regime_params, regime, archetype)
        remaining = 0
        if book_id is not None:
            remaining = self._research_observations_remaining(int(book_id))
        return exit_urgency(
            inventory_size=abs(float(inventory.net_base)),
            inventory_ratio=self._inventory_util(inventory),
            inventory_age=float(getattr(inventory, "position_ticks", 0) or 0),
            unrealized_pnl=inventory.unrealized_bps,
            expected_markout=(
                self._research_conservative_markout(int(book_id))
                if book_id is not None else -2.0
            ),
            volatility=0.0,
            ofi=None,
            imbalance=0.0,
            inventory_sign=float(inventory.net_base),
            kappa_need=kappa_completion_need(remaining, inventory.unrealized_bps),
            volume_cap_headroom=self._research_volume_cap_headroom(
                getattr(self, "_research_volume_cap_state", None),
                int(book_id),
            ),
            recent_realized_pnl=None,
            adverse_selection_risk=0.0,
        )

    def skewed_quote_prices(
        self,
        bid: float,
        ask: float,
        signal: float,
        inventory_ratio: float,
        regime_params: RegimeParamSet,
        price_dec: int,
        edge_bias: float = 0.0,
    ) -> tuple[float, float] | None:
        if not self.research_fix_quote_reservation:
            return super().skewed_quote_prices(
                bid, ask, signal, inventory_ratio, regime_params, price_dec, edge_bias
            )
        spread = ask - bid
        if spread <= 0.0:
            return None
        mid = 0.5 * (bid + ask)
        directional = max(-1.0, min(1.0, float(signal) + float(edge_bias)))
        directional_bias = (
            float(regime_params.buy_bias) if directional >= 0.0
            else float(regime_params.sell_bias)
        )
        alpha_shift = (
            spread * float(regime_params.skew_strength) * directional * directional_bias
        )
        # Positive inventory (long) shifts reservation DOWN; negative inventory
        # shifts it UP, encouraging the side that reduces risk.
        inventory_shift = (
            spread * float(self.inventory_skew_strength) * float(inventory_ratio)
        )
        reservation = mid + alpha_shift - inventory_shift
        width_mult = max(
            float(getattr(self, "research_quote_width_floor_mult", 0.80) or 0.80),
            float(getattr(self, "_research_as_width_mult", 1.0) or 1.0),
        )
        if bool(getattr(self, "_research_completion_quiet_tight_context", False)):
            width_mult = min(
                width_mult,
                float(getattr(self, "research_one_away_quiet_width_mult", 0.60) or 0.60),
            )
        half_spread = spread * max(0.05, float(regime_params.spread_offset)) * width_mult
        tick_size = 10.0 ** (-int(price_dec))
        bid_px = min(reservation - half_spread, ask - tick_size)
        ask_px = max(reservation + half_spread, bid + tick_size)
        bid_px = round(bid_px, price_dec)
        ask_px = round(ask_px, price_dec)
        if bool(getattr(self, "_research_completion_quiet_tight_context", False)):
            # V4.12.2: completion quotes must stay executable. Directional alpha
            # may skew which side is more attractive, but neither side may drift
            # tens of bps away from its own touch in a QUIET ONE_AWAY book.
            max_touch_bps = float(
                getattr(self, "research_one_away_max_touch_bps", 5.0) or 5.0
            )
            max_touch_px = max(0.0, mid * max_touch_bps / 10_000.0)
            bid_floor = max(tick_size, bid - max_touch_px)
            ask_ceiling = ask + max_touch_px
            bid_px = round(max(bid_px, bid_floor), price_dec)
            ask_px = round(min(ask_px, ask_ceiling), price_dec)
            # Never cross our maker pair / the opposing touch.
            bid_px = min(bid_px, round(ask - tick_size, price_dec))
            ask_px = max(ask_px, round(bid + tick_size, price_dec))
        policy = getattr(self, "_research_active_inventory_policy", None)
        if (
            policy is not None
            and getattr(self, "research_enable_same_side_suppression", True)
        ):
            suppression = same_side_suppression(policy.state)
            bid_px, ask_px = apply_exit_competitiveness(
                bid_px=bid_px,
                ask_px=ask_px,
                best_bid=bid,
                best_ask=ask,
                tick_size=tick_size,
                inventory_sign=float(inventory_ratio),
                suppression=suppression,
                price_decimals=int(price_dec),
            )
        elif policy is not None and policy.improve_exit:
            tick_size = 10.0 ** (-int(price_dec))
            ratio = float(inventory_ratio)
            if ratio > 0.0:
                ask_px = round(min(ask_px, ask - tick_size), price_dec)
            elif ratio < 0.0:
                bid_px = round(max(bid_px, bid + tick_size), price_dec)
        if bid_px <= 0.0 or bid_px >= ask_px:
            return None
        return bid_px, ask_px

    def _compute_close_score(
        self,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ) -> float:
        if not self.research_fix_inventory_util:
            return super()._compute_close_score(
                inventory, regime_params, regime, archetype
            )
        unreal = inventory.unrealized_bps
        target = max(float(regime_params.profit_target_bps), 1e-9)
        stop = max(float(regime_params.stop_loss_bps), 1e-9)
        pnl_component = 0.0
        if unreal is not None:
            if unreal >= target or unreal <= -stop:
                pnl_component = 1.0
            elif unreal > 0.0:
                pnl_component = unreal / target
            else:
                pnl_component = abs(unreal) / stop
        inventory_risk = min(1.0, self._inventory_util(inventory))
        regime_risk = 0.0
        if str(getattr(regime, "mode", "")).upper() == "STRESSED":
            regime_risk = 1.0
        elif archetype in ("TOXIC_BOOK", "WALL_BOOK"):
            regime_risk = 0.6
        elif archetype == "DEAD_BOOK":
            regime_risk = 0.4
        time_risk = min(
            1.0,
            float(inventory.position_ticks) / max(float(self.position_max_ticks), 1.0),
        )
        return (
            0.5 * pnl_component
            + 0.3 * inventory_risk
            + 0.2 * max(regime_risk, time_risk)
        )

    def _allows_aggressive_close(
        self,
        book_id: int,
        inventory: InventorySnapshot,
        close_score: float,
        time_stop: bool,
        stop_loss_hit: bool,
    ) -> bool:
        if not taker_allowed_after_transition(
            quarantine=self._research_in_transition_quarantine()
        ):
            return False
        # Safety controls remain immediate. These are the only bootstrap paths
        # allowed to ignore the touch-economics gate.
        if stop_loss_hit or inventory.band in ("MAX_LONG", "MAX_SHORT"):
            return True

        if (
            self._research_bootstrap_active
            and self.research_bootstrap_allow_aggressive_close
        ):
            ticks = int(inventory.position_ticks)
            if not self.research_aggressive_close_touch_gate:
                # Explicit A/B fallback to the previous V2 behavior.
                if ticks >= self.research_bootstrap_hard_close_ticks:
                    return True
                if ticks >= self.research_bootstrap_force_close_ticks:
                    unreal = inventory.unrealized_bps
                    return (
                        unreal is not None
                        and unreal >= self.research_bootstrap_force_close_min_bps
                    )
                return False

            if ticks >= self.research_bootstrap_force_close_ticks:
                ctx = self._research_aggressive_context.get(book_id) or {}
                net_touch_bps = ctx.get("net_touch_bps")
                if net_touch_bps is not None:
                    # Age permits consideration; executable economics decide.
                    return (
                        float(net_touch_bps)
                        >= self.research_aggressive_close_min_net_bps
                    )

            # V4.1 Strict deliberately removes age-only / close-score-only market
            # exits while the scoring-pressure bootstrap is active. The old
            # age-60 cohort was overwhelmingly loss-making, and age>=180 alone
            # is not evidence that crossing the spread improves the outcome.
            return False

        return super()._allows_aggressive_close(
            book_id, inventory, close_score, time_stop, stop_loss_hit
        )

    def _execute_aggressive_close(
        self,
        response: FinanceAgentResponse,
        book_id: int,
        book: Book,
        qty: float,
        long_pos: bool,
    ) -> bool:
        """Submit close without clearing position state before confirmed fill."""
        if not taker_allowed_after_transition(
            quarantine=self._research_in_transition_quarantine()
        ):
            return False
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        if not self._passes_fee_gate(book_id, aggressive=True):
            return False
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return False
        state = getattr(self, "_research_volume_cap_state", None)
        px = 0.0
        try:
            px = float(book.asks[0].price if not long_pos else book.bids[0].price)
        except (TypeError, ValueError, IndexError, AttributeError):
            px = 0.0
        requested = abs(float(qty)) * px
        if state is not None and not self._research_can_add_volume(
            state, book_id, requested,
        ):
            self._research_emit_volume_cap(
                state, book_id, requested_notional=requested, allowed=False, force=True,
            )
            return False
        account = self.accounts[book_id]
        if bool(getattr(self, "research_cancel_before_taker", True)):
            resting = getattr(account, "orders", None) or []
            order_ids = [
                getattr(order, "id", None) for order in resting
                if getattr(order, "id", None) is not None
            ]
            if order_ids:
                # Need one slot for CANCEL and one for MARKET. If the response is
                # already saturated, do not risk flattening while leaving a stale
                # same-direction maker exit that could reopen the book.
                if self._count_book_instructions(response, book_id) + 2 > self.max_instructions_per_book:
                    return False
                response.cancel_orders(book_id=book_id, order_ids=order_ids, delay=0)
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return False
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.market_order(
                book_id=book_id, direction=close_dir, quantity=qty,
                stp=STP.CANCEL_OLDEST, delay=0,
            )
            return True
        if close_dir == OrderDirection.BUY:
            px = book.asks[0].price
            if account.quote_balance.free >= qty * px:
                response.market_order(
                    book_id=book_id, direction=close_dir, quantity=qty,
                    stp=STP.CANCEL_OLDEST, delay=0,
                )
                return True
        return False

    def _manage_inventory(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        book: Book,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ) -> int:
        self._research_volume_cap_bind_book(book_id)
        if self._research_in_transition_quarantine():
            return 0
        # Estimate executable close economics at the opposing touch rather than
        # using mark-to-mid. The configurable buffer covers taker fee/slippage.
        touch_gross_bps = None
        net_touch_bps = None
        entry = inventory.vwap_entry
        if (
            entry is not None
            and float(entry) > 0.0
            and getattr(book, "bids", None)
            and getattr(book, "asks", None)
        ):
            entry_f = float(entry)
            if inventory.net_base > 0.0:
                touch_px = float(book.bids[0].price)
                touch_gross_bps = (touch_px - entry_f) / entry_f * 10_000.0
            elif inventory.net_base < 0.0:
                touch_px = float(book.asks[0].price)
                touch_gross_bps = (entry_f - touch_px) / entry_f * 10_000.0
            if touch_gross_bps is not None:
                net_touch_bps = (
                    float(touch_gross_bps)
                    - self.research_aggressive_close_fee_buffer_bps
                )

        self._research_aggressive_context[book_id] = {
            "touch_gross_bps": touch_gross_bps,
            "net_touch_bps": net_touch_bps,
            "age_ticks": int(inventory.position_ticks),
        }
        if self.debug_enabled:
            record = self._book_record(book_id)
            record["aggressive_touch_gross_bps"] = touch_gross_bps
            record["aggressive_touch_net_bps"] = net_touch_bps
            record["aggressive_close_fee_buffer_bps"] = (
                self.research_aggressive_close_fee_buffer_bps
            )
            record["aggressive_close_min_net_bps"] = (
                self.research_aggressive_close_min_net_bps
            )

        min_size = max(0.0, float(self._research_exchange_min_order_size))
        abs_base = abs(float(inventory.net_base))
        if (
            self.research_dust_safe_close
            and inventory.band != "FLAT"
            and self._is_dust_qty(inventory.net_base)
        ):
            self._refresh_dust_state(book_id, inventory.net_base, emit=True)
            compact_selected = (
                book_id in self._research_dust_compact_ids_this_tick
                and self._dust_compaction_safe_for_any_fill(inventory.net_base)
            )
            if self._research_dust_econ_on():
                return self._research_manage_dust(
                    response,
                    state,
                    book_id,
                    book,
                    inventory,
                    regime_params,
                    compact_selected=compact_selected,
                )
            if compact_selected:
                self._research_dust_compact_attempts += 1
                before_ix = len(response.instructions)
                n = super()._place_passive_inventory_exit(
                    response,
                    state,
                    book_id,
                    book,
                    inventory,
                    min_size,
                )
                if n:
                    self._research_dust_compact_orders += 1
                    self._research_dust_compact_active[book_id] = int(
                        getattr(self, "_tick", 0) or 0
                    )
                    if self.research_dust_compact_adaptive:
                        self._record_dust_compaction_attempt(book_id)
                    self._inventory_reason[book_id] = "DUST_COMPACT"
                    self._emit(
                        "POSITION_GUARD",
                        tick=getattr(self, "_tick", None),
                        book_id=book_id,
                        reason="DUST_COMPACT",
                        net_base=inventory.net_base,
                        min_order_size=min_size,
                        projected_full_fill_net=(
                            float(inventory.net_base)
                            - (min_size if inventory.net_base > 0.0 else -min_size)
                        ),
                        exposure_nonincreasing=True,
                        instructions=len(response.instructions) - before_ix,
                    )
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record["action"] = "MANAGE"
                        record["reason"] = "DUST_COMPACT"
                        record["dust_compact"] = True
                        record["dust_compact_qty"] = min_size
                        record["instructions"] = n
                    return n

                self._emit(
                    "POSITION_GUARD",
                    tick=getattr(self, "_tick", None),
                    book_id=book_id,
                    reason="DUST_COMPACT_BLOCKED",
                    net_base=inventory.net_base,
                    min_order_size=min_size,
                    exposure_nonincreasing=True,
                )

            if self._research_try_dust_escape(
                response, state, book_id, book, inventory, min_size,
            ):
                return 1

            if self.debug_enabled:
                record = self._book_record(book_id)
                record["dust_position"] = True
                record["dust_quarantine"] = True
                record["dust_qty"] = abs_base
                record["min_order_size"] = min_size
                record["dust_compact_selected"] = compact_selected
            return 0
        if not getattr(self, "research_enable_realization", True):
            return super()._manage_inventory(
                response, state, book_id, book, inventory, regime_params, regime, archetype
            )
        return self._research_manage_realization(
            response, state, book_id, book, inventory, regime_params, regime, archetype
        )

    def _research_volume_cap_quote(self, state) -> float:
        return agent_volume_cap_quote(self, state)

    def _research_book_traded_volume(self, book_id) -> float:
        return agent_book_traded_volume(self, book_id)

    def _research_volume_cap_remaining(self, state, book_id) -> float:
        return agent_volume_cap_remaining(self, state, book_id)

    def _research_volume_cap_headroom(self, state, book_id) -> float:
        return agent_volume_cap_headroom(self, state, book_id)

    def _research_can_add_volume(self, state, book_id, quote_notional) -> bool:
        return agent_can_add_volume(self, state, book_id, quote_notional)

    def _research_volume_cap_bind_book(self, book_id):
        prev = getattr(self, "_research_volume_cap_book_id", None)
        try:
            self._research_volume_cap_book_id = int(book_id)
        except (TypeError, ValueError):
            self._research_volume_cap_book_id = None
        return prev

    def _can_add_volume(self, state, quote_notional: float) -> bool:
        """Intercept inherited global remaining so parent quote paths use the current book."""
        book_id = getattr(self, "_research_volume_cap_book_id", None)
        if book_id is not None:
            return self._research_can_add_volume(state, book_id, quote_notional)
        cap = self._research_volume_cap_quote(state)
        if cap <= 0.0:
            return True
        try:
            notional = float(quote_notional)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(notional) or notional < 0.0:
            return False
        return notional <= cap

    def _research_emit_volume_cap(
        self,
        state,
        book_id,
        *,
        requested_notional: float | None = None,
        allowed: bool | None = None,
        reason: str | None = None,
        force: bool = False,
    ) -> None:
        cap = self._research_volume_cap_quote(state)
        used = self._research_book_traded_volume(book_id)
        remaining = self._research_volume_cap_remaining(state, book_id)
        headroom = self._research_volume_cap_headroom(state, book_id)
        token = reason or agent_volume_cap_reason(
            self, state, book_id, requested_notional,
        )
        if allowed is None:
            if requested_notional is None:
                allowed = token == REASON_OK
            else:
                allowed = self._research_can_add_volume(
                    state, book_id, requested_notional,
                )
        if not allowed:
            self._research_volume_cap_blocks = int(
                getattr(self, "_research_volume_cap_blocks", 0) or 0
            ) + 1
        try:
            self._emit(
                "VOLUME_CAP",
                force=force,
                tick=getattr(self, "_tick", None),
                book=int(book_id),
                traded_volume=used,
                cap_quote=cap,
                remaining_quote=remaining,
                headroom=headroom,
                requested_notional=requested_notional,
                allowed=int(bool(allowed)),
                reason=token,
            )
        except Exception:
            pass

    def _research_emit_volume_cap_summary(self, state) -> None:
        snap = agent_volume_cap_snapshot(self, state)
        try:
            self._emit(
                "VOLUME_CAP",
                force=True,
                tick=getattr(self, "_tick", None),
                book=None,
                traded_volume=None,
                cap_quote=snap.get("cap_quote"),
                remaining_quote=None,
                headroom=snap.get("median_headroom"),
                requested_notional=None,
                allowed=None,
                reason="SUMMARY",
                books_cap_reached=snap.get("books_cap_reached"),
                books_headroom_lt_10pct=snap.get("books_headroom_lt_10pct"),
                books_headroom_lt_25pct=snap.get("books_headroom_lt_25pct"),
                median_headroom=snap.get("median_headroom"),
                min_headroom=snap.get("min_headroom"),
                book_count=snap.get("book_count"),
            )
        except Exception:
            pass

    def _research_bind_volume_state(self, state) -> None:
        self._research_volume_cap_state = state
        cfg = getattr(state, "config", None) if state is not None else None
        if cfg is not None:
            wealth = None
            try:
                wealth = float(getattr(cfg, "miner_wealth"))
            except (TypeError, ValueError):
                wealth = None
            if wealth is not None and math.isfinite(wealth):
                self._research_last_miner_wealth = wealth
        self._research_last_volume_cap_quote = self._research_volume_cap_quote(state)

    def _research_exit_hazard_prediction(self, book_id: int, inventory):
        haz = (getattr(self, "_research_hazard_last", {}) or {}).get(int(book_id), {})
        long_pos = float(getattr(inventory, "net_base", 0.0) or 0.0) > 0.0
        pred = haz.get("sell") if long_pos else haz.get("buy")
        if pred is None or not isinstance(pred, HazardPrediction):
            return None
        return pred

    def _research_exit_fill_hazard(self, book_id: int, inventory) -> float | None:
        pred = self._research_exit_hazard_prediction(int(book_id), inventory)
        if pred is None:
            return None
        try:
            if not bool(getattr(pred, "usable", False)):
                return None
            fill = float(getattr(pred, "actionable_fill", None))
        except (TypeError, ValueError):
            return None
        if not math.isfinite(fill):
            return None
        return max(0.0, min(1.0, fill))

    def _research_exit_attempt_context(self, book_id: int) -> tuple[int, float]:
        """Return unresolved maker-exit count and age for this inventory lifecycle."""
        bid = int(book_id)
        row = (getattr(self, "_research_exit_attempts", {}) or {}).get(bid) or {}
        failed = max(0, int(row.get("failed_exit_count", 0) or 0))
        first_tick = row.get("first_tick")
        now = int(getattr(self, "_tick", 0) or 0)
        since = 0.0 if first_tick is None else float(max(0, now - int(first_tick)))
        return failed, since

    def _research_note_exit_attempt(self, book_id: int, action: str, *, placed: bool) -> None:
        """Track repeated maker realization attempts without treating stale FIFO keys as inventory."""
        if not placed:
            return
        bid = int(book_id)
        token = str(action or "").upper()
        now = int(getattr(self, "_tick", 0) or 0)
        table = getattr(self, "_research_exit_attempts", None)
        if not isinstance(table, dict):
            table = {}
            self._research_exit_attempts = table
        row = table.setdefault(
            bid, {"first_tick": now, "last_tick": None, "failed_exit_count": 0, "last_action": None}
        )
        if row.get("first_tick") is None:
            row["first_tick"] = now
        last_tick = row.get("last_tick")
        last_action = str(row.get("last_action") or "").upper()
        maker_tokens = {"PASSIVE_MAKER_EXIT", "COMPETITIVE_MAKER_EXIT", "AGGRESSIVE_MAKER_EXIT"}
        if token in maker_tokens:
            if last_tick is not None and int(last_tick) < now and last_action in maker_tokens:
                row["failed_exit_count"] = max(0, int(row.get("failed_exit_count", 0) or 0)) + 1
            row["last_tick"] = now
            row["last_action"] = token
        elif "TAKER" in token:
            row["last_tick"] = now
            row["last_action"] = token

    def _research_reset_exit_attempt(self, book_id: int) -> None:
        table = getattr(self, "_research_exit_attempts", None)
        if isinstance(table, dict):
            table.pop(int(book_id), None)
        peaks = getattr(self, "_research_peak_taker_net_bps", None)
        if isinstance(peaks, dict):
            peaks.pop(int(book_id), None)
        unified = getattr(self, "_research_unified_exit_last", None)
        if isinstance(unified, dict):
            unified.pop(int(book_id), None)

    def _research_live_fee_bps(
        self, book_id: int, *, is_maker: bool, fallback_bps: float | None = None
    ) -> float:
        fallback = float(
            self.research_score_ev_fees_bps if fallback_bps is None else fallback_bps
        )
        account = (getattr(self, "accounts", {}) or {}).get(int(book_id))
        fees = getattr(account, "fees", None) if account is not None else None
        rate = None
        if fees is not None:
            rate = getattr(fees, "maker_fee_rate" if is_maker else "taker_fee_rate", None)
        return fee_rate_to_bps(
            rate, fallback_bps=fallback, allow_rebate=bool(is_maker),
        )

    def _research_round_trip_fee_bps(self, book_id: int) -> float:
        # Maker acquisition + probable taker realization is the conservative
        # lifecycle fee used for entry ranking. Maker rebates are preserved.
        maker = self._research_live_fee_bps(book_id, is_maker=True)
        taker = self._research_live_fee_bps(book_id, is_maker=False)
        return maker + taker

    def _research_lifecycle_entry_cost_bps(self, book_id: int, spread_bps: float) -> float:
        cost = lifecycle_entry_cost_bps(
            maker_fee_bps=self._research_live_fee_bps(book_id, is_maker=True),
            taker_fee_bps=self._research_live_fee_bps(book_id, is_maker=False),
            spread_bps=max(0.0, float(spread_bps or 0.0)),
            taker_exit_probability=float(getattr(self, "research_lifecycle_taker_exit_prob", 0.30)),
            slippage_bps=float(getattr(self, "research_lifecycle_slippage_bps", 0.75)),
            holding_risk_bps=float(getattr(self, "research_lifecycle_holding_bps", 0.50)),
        )
        self._research_lifecycle_cost_last[int(book_id)] = cost
        return float(cost.total_bps)

    def _research_exit_ttl_target_ms(self, book_id: int) -> float:
        regime = str(getattr(self, "_research_market_regime", "") or "").upper()
        if regime == "QUIET":
            remaining = int(self._research_observations_remaining(int(book_id)))
            return (
                float(getattr(self, "research_one_away_exit_ttl_ms", 975.0))
                if remaining == 1
                else float(getattr(self, "research_quiet_exit_ttl_ms", 950.0))
            )
        return float(sim_delta_ms(0, int(getattr(self, "mm_expiry_period", 500_000_000))) or 500.0)

    def _research_actual_exit_hazard(
        self, book_id: int, book, inventory, close_px: float, ttl_ms: float
    ) -> HazardPrediction | None:
        if not bool(getattr(self, "research_enable_fill_hazard", False)):
            return None
        model = getattr(self, "_research_fill_hazard", None)
        if model is None:
            return None
        try:
            bid = float(book.bids[0].price)
            ask = float(book.asks[0].price)
            mid = 0.5 * (bid + ask)
        except Exception:
            try:
                bid = float(book.bids[0].price)
                ask = float(book.asks[0].price)
                mid = 0.5 * (bid + ask)
            except Exception:
                return None
        profile = self._research_profile_for_book(int(book_id))
        long_pos = float(getattr(inventory, "net_base", 0.0) or 0.0) > 0.0
        side = "sell" if long_pos else "buy"
        tick = None
        try:
            # Infer tick from displayed prices when state precision is unavailable.
            tick = 10.0 ** (-max(0, len(str(ask).split(".")[1]) if "." in str(ask) else 0))
        except Exception:
            tick = None
        _ticks, dist_bps = touch_distance(
            side, float(close_px), bid, ask, mid, tick,
        )
        feat = HazardFeatures.from_snapshot(
            side=side,
            distance_from_touch_bps=dist_bps,
            spread_bps=((ask - bid) / mid * 10_000.0) if mid > 0.0 else 0.0,
            volatility=(getattr(profile, "volatility", 0.0) if profile is not None else 0.0),
            trade_rate=(getattr(profile, "trade_rate", 0.0) if profile is not None else 0.0),
            imbalance=(getattr(profile, "imbalance", 0.0) if profile is not None else 0.0),
            market_regime=str(getattr(self, "_research_market_regime", "NORMAL") or "NORMAL"),
            ttl_ms=float(ttl_ms),
        )
        try:
            return model.predict(feat)
        except Exception:
            return None

    def _research_apply_unified_exit(
        self,
        legacy,
        *,
        book_id: int,
        book,
        inventory,
        state,
        expected_markout: float,
        adverse_selection_risk: float,
        stop_hit: bool,
        failed_exit_count: int,
        time_since_first_exit_attempt: float,
        observations_remaining: int,
    ):
        if not bool(getattr(self, "research_enable_unified_exit", True)):
            return legacy
        entry = getattr(inventory, "vwap_entry", None)
        if entry is None or float(entry or 0.0) <= 0.0:
            return legacy
        try:
            bid = float(book.bids[0].price)
            ask = float(book.asks[0].price)
        except Exception:
            return legacy
        long_pos = float(inventory.net_base) > 0.0
        price_dec = int(getattr(state.config, "priceDecimals", 2) or 2)
        tick = 10.0 ** (-price_dec)
        maker_action = str(getattr(legacy, "proposed_rung", "") or "")
        if maker_action not in {ACTION_PASSIVE, ACTION_COMPETITIVE, ACTION_AGGRESSIVE}:
            maker_action = ACTION_AGGRESSIVE if int(failed_exit_count) >= 3 else ACTION_COMPETITIVE
        raw_maker_px = maker_exit_price(
            bid=bid, ask=ask, long_position=long_pos, action=maker_action, tick_size=tick,
        )
        maker_fee = self._research_live_fee_bps(int(book_id), is_maker=True)
        taker_fee = self._research_live_fee_bps(int(book_id), is_maker=False)
        entry_fee = maker_fee  # Research entries are post-only Maker orders.
        maker_floor = float(getattr(self, "research_unified_maker_net_floor_bps", 0.0))

        # V4.12.8 stale-Maker rescue.  V4.12.6 proved that a -2 bps Taker
        # rescue window often does not exist in ~30 bps spread books: the
        # immediate Taker unwind is already deeply negative.  Keep the Taker
        # floor unchanged, but allow ONE_AWAY Maker realization to relax from
        # strict breakeven to a tiny bounded Maker floor after repeated failed
        # exits (or sooner when its existing Kappa observations are at a
        # critical rolling deadline).
        rescue_active = False
        rescue_reason = ""
        rescue_deadline_urgency = 0.0
        raw_maker_net = unified_completion_net_bps(
            entry_price=float(entry), exit_price=float(raw_maker_px),
            long_position=long_pos, entry_fee_bps=entry_fee, exit_fee_bps=maker_fee,
        )
        if (
            bool(getattr(self, "research_stale_maker_rescue_enabled", True))
            and int(observations_remaining) == 1
            and float(raw_maker_net) < float(maker_floor)
        ):
            try:
                expiry = self._research_kappa_expiry(int(book_id))
                rescue_deadline_urgency = float(getattr(expiry, "expiry_urgency", 0.0) or 0.0)
            except Exception:
                rescue_deadline_urgency = 0.0
            normal_trigger = int(failed_exit_count) >= int(
                getattr(self, "research_stale_maker_rescue_failed_exits", 4)
            )
            critical_trigger = bool(
                rescue_deadline_urgency >= float(getattr(self, "research_deadline_critical_urgency", 0.50))
                and int(failed_exit_count) >= int(
                    getattr(self, "research_stale_maker_rescue_critical_failed_exits", 1)
                )
            )
            if normal_trigger or critical_trigger:
                configured_floor = float(getattr(self, "research_stale_maker_rescue_floor_bps", -1.0))
                taker_floor = float(getattr(self, "research_protective_taker_loss_floor_bps", -2.0))
                maker_floor = max(taker_floor, min(0.0, configured_floor))
                rescue_active = True
                rescue_reason = "DEADLINE" if critical_trigger else "FAILED_EXITS"

        be_px = unified_breakeven_price(
            entry_price=float(entry),
            long_position=long_pos,
            round_trip_fee_bps=entry_fee + maker_fee,
            net_floor_bps=maker_floor,
        )
        if long_pos:
            maker_px = max(float(raw_maker_px), float(be_px))
            maker_px = math.ceil(maker_px / tick - 1e-12) * tick
        else:
            maker_px = min(float(raw_maker_px), float(be_px))
            maker_px = math.floor(maker_px / tick + 1e-12) * tick
        maker_px = round(max(tick, maker_px), price_dec)
        taker_px = bid if long_pos else ask
        econ = getattr(legacy, "taker_economics", None)
        impact = 0.0
        holding = 0.0
        if econ is not None:
            try:
                impact = max(0.0, float(getattr(econ.taker, "market_impact_buffer", 0.0) or 0.0))
            except Exception:
                impact = 0.0
            try:
                holding = max(0.0, float(getattr(econ.holding, "expected_holding_cost", 0.0) or 0.0))
            except Exception:
                holding = 0.0
        slippage = max(0.0, float(getattr(self, "research_lifecycle_slippage_bps", 0.75)))
        maker_net = unified_completion_net_bps(
            entry_price=float(entry), exit_price=maker_px, long_position=long_pos,
            entry_fee_bps=entry_fee, exit_fee_bps=maker_fee,
        )
        taker_net = unified_completion_net_bps(
            entry_price=float(entry), exit_price=taker_px, long_position=long_pos,
            entry_fee_bps=entry_fee, exit_fee_bps=taker_fee,
            slippage_bps=slippage, impact_bps=impact,
        )
        if rescue_active:
            try:
                self._emit(
                    "STALE_RESCUE", force=True, tick=getattr(self, "_tick", None),
                    book=int(book_id), observations_remaining=int(observations_remaining),
                    failed_exits=int(failed_exit_count), reason=rescue_reason,
                    deadline_urgency=float(rescue_deadline_urgency),
                    raw_maker_price=float(raw_maker_px), selected_maker_price=float(maker_px),
                    raw_maker_net_bps=float(raw_maker_net), maker_net_bps=float(maker_net),
                    maker_floor_bps=float(maker_floor), taker_net_bps=float(taker_net),
                )
            except Exception:
                pass
        ttl_ms = self._research_exit_ttl_target_ms(int(book_id))
        actual_hazard = self._research_actual_exit_hazard(
            int(book_id), book, inventory, maker_px, ttl_ms,
        )
        if actual_hazard is not None:
            p_maker = float(getattr(actual_hazard, "actionable_fill", 0.0) or 0.0)
        else:
            old = getattr(legacy, "maker_fill_hazard", None)
            p_maker = 0.0 if old is None else float(old)
        reversal = max(0.0, -float(expected_markout or 0.0)) + 4.0 * max(0.0, float(adverse_selection_risk or 0.0))
        wait_ev = unified_wait_value_bps(
            maker_net_bps=maker_net, taker_net_bps=taker_net, p_maker_fill=p_maker,
            holding_cost_bps=holding, reversal_cost_bps=reversal,
            failed_exit_count=int(failed_exit_count),
            failed_exit_penalty_bps=float(getattr(self, "research_failed_exit_penalty_bps", 0.75)),
            age_ticks=max(
                float(getattr(inventory, "position_ticks", 0) or 0),
                float(time_since_first_exit_attempt),
            ),
            age_penalty_bps_per_tick=float(getattr(self, "research_exit_age_penalty_bps_per_tick", 0.03)),
        )
        peaks = getattr(self, "_research_peak_taker_net_bps", None)
        if not isinstance(peaks, dict):
            peaks = {}
            self._research_peak_taker_net_bps = peaks
        peak = max(float(peaks.get(int(book_id), taker_net)), float(taker_net))
        peaks[int(book_id)] = peak
        hard_emergency = str(getattr(inventory, "band", "") or "").upper() in {"MAX_LONG", "MAX_SHORT"}
        unified = choose_unified_exit(
            maker_net_bps=maker_net, taker_net_bps=taker_net, wait_ev_bps=wait_ev,
            maker_price=maker_px, taker_price=taker_px, breakeven_px=be_px,
            p_maker_fill=p_maker, peak_taker_net_bps=peak,
            failed_exit_count=int(failed_exit_count), inventory_age=max(
                float(getattr(inventory, "position_ticks", 0) or 0),
                float(time_since_first_exit_attempt),
            ),
            observations_remaining=int(observations_remaining),
            expected_markout_bps=float(expected_markout or 0.0),
            adverse_selection_risk=float(adverse_selection_risk or 0.0),
            inventory_state=str(getattr(legacy, "state", "NORMAL") or "NORMAL"),
            stop_loss_hit=bool(stop_hit), hard_emergency=bool(hard_emergency),
            profit_lock_min_bps=float(getattr(self, "research_unified_profit_lock_min_bps", 1.0)),
            profit_lock_drawdown_bps=float(getattr(self, "research_unified_profit_lock_drawdown_bps", 2.0)),
            switch_margin_bps=float(getattr(self, "research_unified_switch_margin_bps", 0.50)),
            protective_enabled=bool(getattr(self, "research_enable_protective_taker", True)),
            protective_loss_floor_bps=float(getattr(self, "research_protective_taker_loss_floor_bps", -2.0)),
            protective_ev_advantage_bps=float(getattr(self, "research_protective_taker_ev_advantage_bps", 1.0)),
            protective_failed_exits=int(getattr(self, "research_protective_taker_failed_exits", 6)),
            protective_min_age_ticks=float(getattr(self, "research_protective_taker_min_age_ticks", 8.0)),
            protective_adverse_bps=float(getattr(self, "research_protective_taker_adverse_bps", 2.0)),
            early_escape_enabled=bool(getattr(self, "research_early_escape_enabled", True)),
            early_escape_failed_exits=int(getattr(self, "research_early_escape_failed_exits", 3)),
            early_escape_min_age_ticks=float(getattr(self, "research_early_escape_min_age_ticks", 5.0)),
            early_escape_drawdown_bps=float(getattr(self, "research_early_escape_drawdown_bps", 1.5)),
            early_escape_floor_headroom_bps=float(getattr(self, "research_early_escape_floor_headroom_bps", 0.75)),
            early_escape_ev_advantage_bps=float(getattr(self, "research_early_escape_ev_advantage_bps", 0.50)),
        )
        self._research_unified_exit_last[int(book_id)] = unified
        if unified.action == UNIFIED_KEEP_MAKER:
            return replace(
                legacy,
                action=maker_action, selected_action=maker_action,
                maker_exit_ev=maker_net, maker_fill_hazard=p_maker,
                taker_allowed=False, direct_taker_authorized=False,
                economic_taker_authorized=False, score_taker_authorized=False,
                risk_taker_authorized=False, aggressive_positive_ev_taker_authorized=False,
                taker_authority="NONE", trigger=unified.reason, hybrid_reason=unified.reason,
                unified_exit=unified,
            )
        if unified.action == UNIFIED_TAKER_PROFIT_LOCK:
            authority = "ECONOMIC"
            floor = 0.0
            econ_auth, risk_auth = True, False
        elif unified.action == UNIFIED_TAKER_PROTECT:
            authority = "RISK"
            floor = float(getattr(self, "research_protective_taker_loss_floor_bps", -2.0))
            econ_auth, risk_auth = False, True
        else:
            authority = "RISK"
            floor = float(getattr(self, "research_risk_direct_max_loss_bps", -10.0))
            econ_auth, risk_auth = False, True
        return replace(
            legacy,
            action=ACTION_TAKER, selected_action=ACTION_TAKER,
            maker_exit_ev=maker_net, maker_fill_hazard=p_maker,
            taker_allowed=True, taker_qty_frac=1.0,
            direct_taker_authorized=True, economic_taker_authorized=econ_auth,
            score_taker_authorized=False, risk_taker_authorized=risk_auth,
            aggressive_positive_ev_taker_authorized=False,
            taker_authority=authority, allowed_loss_floor_bps=floor,
            trigger=unified.action, hybrid_reason=unified.reason, unified_exit=unified,
        )

    def _research_evaluate_realization(
        self,
        book_id: int,
        book,
        inventory,
        state,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ):
        profile = self._research_profile_for_book(book_id)
        ev = (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id))
        markout_mean, markout_n = self._research_markout_snapshot(book_id)
        expected_markout = self._research_conservative_markout(int(book_id))
        if ev is not None:
            expected_markout = float(getattr(ev, "expected_markout_bps", expected_markout) or expected_markout)
        elif markout_mean is not None and markout_n > 0:
            expected_markout = conservative_expected_markout_bps(
                mean_bps=markout_mean, samples=markout_n,
            )
        vol = 0.0
        if profile is not None:
            try:
                vol = float(getattr(profile, "volatility", 0.0) or 0.0)
            except (TypeError, ValueError):
                vol = 0.0
        ofi_val = None
        ofi_snap = self._research_ofi_snapshot(int(book_id))
        if ofi_snap.supported:
            ofi_val = (
                ofi_snap.ofi_fast
                if ofi_snap.ofi_fast is not None
                else ofi_snap.ofi_normalized
            )
        adverse = 0.0
        if ev is not None:
            adverse = float(getattr(ev, "adverse_selection_risk", 0.0) or 0.0)
        else:
            adverse = composite_adverse_selection_risk(
                expected_markout_bps=expected_markout,
                ofi_against=self._research_ofi_against(
                    int(book_id), float(inventory.net_base),
                ),
            )
        recent = None
        failed = None
        mem = self._mem(book_id)
        try:
            recent = float(getattr(mem, "recent_pnl", 0.0) or 0.0)
        except (TypeError, ValueError, AttributeError):
            recent = None
        try:
            failed = int(getattr(mem, "loss_streak", 0) or 0) >= 2
        except (TypeError, ValueError, AttributeError):
            failed = None
        failed_exit_count, time_since_first_exit_attempt = self._research_exit_attempt_context(book_id)
        failed = bool(failed) or failed_exit_count > 0
        kappa = self._research_kappa_book(book_id)
        remaining = kappa.observations_remaining
        bid = float(book.bids[0].price)
        ask = float(book.asks[0].price)
        mid = 0.5 * (bid + ask)
        spread_bps = ((ask - bid) / mid) * 10_000.0 if mid > 0.0 else 0.0
        stop = max(float(getattr(regime_params, "stop_loss_bps", 35.0) or 35.0), 1e-9)
        unreal = inventory.unrealized_bps
        stop_hit = unreal is not None and float(unreal) <= -stop
        fee = self._research_live_fee_bps(int(book_id), is_maker=False)
        slip = float(self.research_aggressive_close_fee_buffer_bps)
        boost = kappa_realization_boost(
            observations_remaining=remaining,
            unrealized_pnl_bps=unreal,
            eligible=kappa.eligible,
            crossing_cost_bps=taker_crossing_cost_bps(
                fee_bps=fee, spread_bps=spread_bps, slippage_bps=slip,
            ),
        )
        self._research_kappa_realization_last[int(book_id)] = boost
        legacy = evaluate_realization(
            book=int(book_id),
            inventory_size=float(inventory.net_base),
            inventory_ratio=self._inventory_util(inventory),
            inventory_age=float(getattr(inventory, "position_ticks", 0) or 0),
            unrealized_pnl=unreal,
            expected_markout=expected_markout,
            volatility=vol,
            ofi=ofi_val,
            imbalance=0.0,
            kappa_need=boost.boost,
            observations_remaining=remaining,
            required_observations=int(getattr(kappa, "required_observations", 3) or 3),
            volume_cap_headroom=self._research_volume_cap_headroom(state, book_id),
            recent_realized_pnl=recent,
            adverse_selection_risk=adverse,
            fee_bps=fee,
            spread_bps=spread_bps,
            slippage_bps=slip,
            band=getattr(inventory, "band", None),
            stop_loss_hit=bool(stop_hit),
            hard_emergency=str(getattr(inventory, "band", "")).upper() in {
                "MAX_LONG", "MAX_SHORT",
            },
            inventory_sign=float(inventory.net_base),
            maker_fill_hazard=self._research_exit_fill_hazard(int(book_id), inventory),
            hazard=(
                self._research_exit_hazard_prediction(int(book_id), inventory)
                if getattr(self, "research_enable_fill_hazard_exit_compare", True)
                else None
            ),
            enable_hybrid=bool(getattr(self, "research_enable_hybrid_taker", True))
            and bool(getattr(self, "research_enable_hybrid_realization_v2", True)),
            min_lock_bps=float(getattr(self, "research_hybrid_min_lock_bps", 1.0)),
            maker_ev_gap_bps=float(getattr(self, "research_hybrid_maker_ev_gap_bps", 0.50)),
            stale_age_ticks=float(getattr(self, "research_hybrid_stale_age_ticks", 16)),
            min_maker_fill=float(getattr(self, "research_hybrid_min_maker_fill", 0.15)),
            volume_capped=self._research_volume_cap_headroom(state, book_id) <= 0.0,
            dust=self._is_dust_qty(float(inventory.net_base)),
            transition_quarantine=self._research_in_transition_quarantine(),
            realization_failed=failed,
            ladder_bands=getattr(self, "_research_ladder_bands", None),
            net_floor_bps=float(getattr(self, "research_taker_net_floor_bps", 0.0)),
            use_exit_urgency_v2=bool(getattr(self, "research_enable_exit_urgency_v2", True)),
            use_fill_hazard_ev=bool(
                getattr(self, "research_enable_fill_hazard_exit_compare", True)
            ),
            allow_economic_taker=bool(getattr(self, "research_enable_economic_taker", True)),
            enable_sn79_action_utility=bool(
                getattr(self, "research_enable_sn79_action_utility", True)
            ),
            allow_score_taker_direct=bool(
                getattr(self, "research_enable_score_taker_direct", True)
            ),
            allow_economic_taker_direct=bool(
                getattr(self, "research_enable_economic_taker_direct", True)
            ),
            economic_direct_max_loss_bps=float(
                getattr(self, "research_economic_direct_max_loss_bps", -20.0)
            ),
            allow_risk_taker_direct=bool(
                getattr(self, "research_enable_risk_taker_direct", True)
            ),
            risk_direct_max_loss_bps=float(
                getattr(self, "research_risk_direct_max_loss_bps", -25.0)
            ),
            risk_direct_min_age_ticks=float(
                getattr(self, "research_risk_direct_min_age_ticks", 24.0)
            ),
            risk_direct_failed_exit_count=int(
                getattr(self, "research_risk_direct_failed_exit_count", 3)
            ),
            risk_direct_min_ev_advantage_bps=float(
                getattr(self, "research_risk_direct_min_ev_advantage_bps", 1.0)
            ),
            allow_aggressive_positive_ev_taker=bool(
                getattr(self, "research_enable_aggressive_positive_ev_taker", True)
            ),
            aggressive_positive_ev_min_net_bps=float(
                getattr(self, "research_aggressive_positive_ev_min_net_bps", 0.0)
            ),
            aggressive_positive_ev_switch_margin_bps=float(
                getattr(self, "research_aggressive_positive_ev_switch_margin_bps", 0.50)
            ),
            aggressive_positive_ev_one_away_margin_bps=float(
                getattr(self, "research_aggressive_positive_ev_one_away_margin_bps", 0.0)
            ),
            aggressive_positive_ev_failed_exit_count=int(
                getattr(self, "research_aggressive_positive_ev_failed_exit_count", 8)
            ),
            aggressive_positive_ev_min_age_ticks=float(
                getattr(self, "research_aggressive_positive_ev_min_age_ticks", 16.0)
            ),
            aggressive_positive_ev_max_maker_fill=float(
                getattr(self, "research_aggressive_positive_ev_max_maker_fill", 0.08)
            ),
            aggressive_positive_ev_min_urgency=float(
                getattr(self, "research_aggressive_positive_ev_min_urgency", 0.30)
            ),
            failed_exit_count=failed_exit_count,
            time_since_first_exit_attempt=time_since_first_exit_attempt,
            maker_escalate_failed_exit_count=int(
                getattr(self, "research_maker_escalate_failed_exit_count", 8)
            ),
            one_away_maker_escalate_failed_exit_count=int(
                getattr(self, "research_one_away_maker_escalate_failed_exit_count", 3)
            ),
            failed_exit_penalty_bps=float(
                getattr(self, "research_failed_exit_penalty_bps", 0.75)
            ),
            exit_age_penalty_bps_per_tick=float(
                getattr(self, "research_exit_age_penalty_bps_per_tick", 0.03)
            ),
            sn79_pnl_scale_bps=float(getattr(self, "research_sn79_pnl_scale_bps", 8.0)),
            sn79_pnl_weight=float(getattr(self, "research_sn79_pnl_weight", 1.0)),
            sn79_round_trip_weight=float(
                getattr(self, "research_sn79_round_trip_weight", 0.30)
            ),
            sn79_kappa_weight=float(getattr(self, "research_sn79_kappa_weight", 0.35)),
            sn79_coverage_weight=float(
                getattr(self, "research_sn79_coverage_weight", 0.15)
            ),
            sn79_capital_release_weight=float(
                getattr(self, "research_sn79_capital_release_weight", 0.15)
            ),
            sn79_risk_reduction_weight=float(
                getattr(self, "research_sn79_risk_reduction_weight", 0.20)
            ),
            sn79_velocity_weight=float(
                getattr(self, "research_sn79_velocity_weight", 0.25)
            ),
            sn79_downside_weight=float(
                getattr(self, "research_sn79_downside_weight", 0.45)
            ),
            sn79_min_utility_margin=float(
                getattr(self, "research_sn79_min_utility_margin", 0.03)
            ),
            sn79_max_score_subsidy_loss_bps=float(
                getattr(self, "research_sn79_max_score_subsidy_loss_bps", -2.0)
            ),
            sn79_one_away_loss_floor_bps=float(
                getattr(self, "research_sn79_one_away_loss_floor_bps", -8.0)
            ),
            sn79_two_away_loss_floor_bps=float(
                getattr(self, "research_sn79_two_away_loss_floor_bps", -6.0)
            ),
            sn79_uncovered_loss_floor_bps=float(
                getattr(self, "research_sn79_uncovered_loss_floor_bps", -5.0)
            ),
        )
        return self._research_apply_unified_exit(
            legacy,
            book_id=int(book_id), book=book, inventory=inventory, state=state,
            expected_markout=float(expected_markout or 0.0),
            adverse_selection_risk=float(adverse or 0.0), stop_hit=bool(stop_hit),
            failed_exit_count=int(failed_exit_count),
            time_since_first_exit_attempt=float(time_since_first_exit_attempt),
            observations_remaining=int(remaining),
        )

    def _research_place_maker_exit(
        self,
        response,
        state,
        book_id: int,
        book,
        inventory,
        qty: float,
        action: str,
        close_price: float | None = None,
    ) -> int:
        if self._research_in_transition_quarantine():
            return 0
        long_pos = float(inventory.net_base) > 0.0
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        price_dec = int(getattr(state.config, "priceDecimals", 2) or 2)
        tick = 10.0 ** (-price_dec)
        bid = float(book.bids[0].price)
        ask = float(book.asks[0].price)
        if close_price is None:
            close_px = round(
                maker_exit_price(
                    bid=bid,
                    ask=ask,
                    long_position=long_pos,
                    action=action,
                    tick_size=tick,
                ),
                price_dec,
            )
        else:
            close_px = round(float(close_price), price_dec)
        if close_px <= 0.0:
            return 0
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return 0
        requested = abs(float(qty)) * close_px
        if not self._research_can_add_volume(state, book_id, requested):
            self._research_emit_volume_cap(
                state, book_id, requested_notional=requested, allowed=False, force=True,
            )
            return 0
        account = self.accounts[book_id]
        post_only = True
        expiry_period = int(self.mm_expiry_period)
        regime = str(getattr(self, "_research_market_regime", "") or "").upper()
        if regime == "QUIET":
            remaining = int(self._research_observations_remaining(int(book_id)))
            target_ms = (
                float(getattr(self, "research_one_away_exit_ttl_ms", 975.0))
                if remaining == 1
                else float(getattr(self, "research_quiet_exit_ttl_ms", 950.0))
            )
            desired = max(expiry_period, int(ms_to_ns(target_ms)))
            publish = int(getattr(getattr(state, "config", None), "publish_interval", 0) or 0)
            if publish > 0:
                # Never keep a maker exit alive into the next publish cycle.
                # This increases live-time without permitting same-side stacking.
                ceiling = max(expiry_period, int(float(publish) * 0.98))
                desired = min(desired, ceiling)
            expiry_period = max(expiry_period, desired)
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.limit_order(
                book_id=book_id,
                direction=close_dir,
                quantity=qty,
                price=close_px,
                stp=STP.CANCEL_BOTH,
                postOnly=post_only,
                timeInForce=TimeInForce.GTT,
                expiryPeriod=expiry_period,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )
            return 1
        if close_dir == OrderDirection.BUY and account.quote_balance.free >= qty * close_px:
            response.limit_order(
                book_id=book_id,
                direction=close_dir,
                quantity=qty,
                price=close_px,
                stp=STP.CANCEL_BOTH,
                postOnly=post_only,
                timeInForce=TimeInForce.GTT,
                expiryPeriod=expiry_period,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )
            return 1
        return 0

    def _research_manage_realization(
        self,
        response,
        state,
        book_id: int,
        book,
        inventory,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ) -> int:
        if self._research_in_transition_quarantine():
            return 0
        if getattr(inventory, "band", None) == "FLAT":
            return 0
        placed = 0
        vol_dec = int(getattr(state.config, "volumeDecimals", 8) or 8)
        min_size = exchange_min_order_size(
            getattr(self, "_research_exchange_min_order_size", 0.0), vol_dec,
        )
        raw_inv = abs(float(inventory.net_base))
        if getattr(self, "research_enable_precise_reduction_qty", True):
            flatten = choose_reduce_quantity(
                inventory=float(inventory.net_base),
                desired=raw_inv,
                min_order=min_size,
                volume_decimals=vol_dec,
            )
        else:
            flatten = choose_reduce_quantity(
                inventory=float(inventory.net_base),
                desired=raw_inv if raw_inv + 1e-12 >= min_size else 0.0,
                min_order=min_size,
                volume_decimals=vol_dec,
            )
        qty = flatten.quantity
        if self._try_close_loans(
            response, book_id, inventory.unrealized_bps, regime_params.profit_target_bps,
        ):
            placed += 1
        if qty <= 0:
            try:
                self._emit(
                    "EXIT_QTY",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    book=int(book_id),
                    selected_action="SKIP",
                    **flatten.as_log(),
                )
            except Exception:
                pass
            return placed
        decision = self._research_evaluate_realization(
            book_id, book, inventory, state, regime_params, regime, archetype,
        )
        self._research_realization_last[int(book_id)] = decision
        unified_exit = getattr(decision, "unified_exit", None)
        if unified_exit is not None:
            try:
                self._emit(
                    "EXIT", force=True, tick=getattr(self, "_tick", None),
                    book=int(book_id), inventory=float(inventory.net_base),
                    entry_price=getattr(inventory, "vwap_entry", None),
                    observations_remaining=self._research_observations_remaining(int(book_id)),
                    failed_exit_count=getattr(decision, "failed_exit_count", 0),
                    inventory_age=getattr(inventory, "position_ticks", 0),
                    selected_action=decision.selected_action,
                    taker_authority=getattr(decision, "taker_authority", "NONE"),
                    **unified_exit.as_log(),
                )
            except Exception:
                pass
        auth_counts = getattr(self, "_research_taker_authority_counts", None)
        if not isinstance(auth_counts, dict):
            auth_counts = {"ECONOMIC": 0, "SCORE": 0, "RISK": 0, "POSITIVE_EV": 0}
            self._research_taker_authority_counts = auth_counts
        if bool(getattr(decision, "economic_taker_authorized", False)):
            auth_counts["ECONOMIC"] = int(auth_counts.get("ECONOMIC", 0) or 0) + 1
        if bool(getattr(decision, "score_taker_authorized", False)):
            auth_counts["SCORE"] = int(auth_counts.get("SCORE", 0) or 0) + 1
        if bool(getattr(decision, "risk_taker_authorized", False)):
            auth_counts["RISK"] = int(auth_counts.get("RISK", 0) or 0) + 1
        if bool(getattr(decision, "aggressive_positive_ev_taker_authorized", False)):
            auth_counts["POSITIVE_EV"] = int(auth_counts.get("POSITIVE_EV", 0) or 0) + 1
        urgency_log = {}
        breakdown = getattr(decision, "urgency_breakdown", None)
        if breakdown is not None:
            urgency_log = breakdown.as_log()
            try:
                self._emit(
                    "EXIT_URGENCY",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    book=int(book_id),
                    **urgency_log,
                )
            except Exception:
                pass
        ladder_log = {}
        rung = getattr(decision, "ladder_rung", None)
        if rung is not None:
            ladder_log = rung.as_log()
            try:
                self._emit(
                    "LADDER",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    book=int(book_id),
                    selected_action=decision.selected_action,
                    trigger=decision.trigger,
                    hybrid_reason=getattr(decision, "hybrid_reason", None),
                    **ladder_log,
                )
            except Exception:
                pass
        econ = getattr(decision, "taker_economics", None)
        if econ is not None:
            try:
                self._emit(
                    "TAKER_DECISION",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    book=int(book_id),
                    selected_action=decision.selected_action,
                    trigger=decision.trigger,
                    hybrid_reason=getattr(decision, "hybrid_reason", None),
                    taker_eligible=int(bool(getattr(decision, "taker_eligible", False))),
                    economic_taker_authorized=int(bool(getattr(decision, "economic_taker_authorized", False))),
                    score_taker_authorized=int(bool(getattr(decision, "score_taker_authorized", False))),
                    risk_taker_authorized=int(bool(getattr(decision, "risk_taker_authorized", False))),
                    aggressive_positive_ev_taker_authorized=int(bool(
                        getattr(decision, "aggressive_positive_ev_taker_authorized", False)
                    )),
                    aggressive_positive_ev_trigger=getattr(decision, "aggressive_positive_ev_trigger", ""),
                    aggressive_positive_ev_advantage_bps=getattr(decision, "aggressive_positive_ev_advantage_bps", 0.0),
                    aggressive_positive_ev_switch_margin_bps=getattr(decision, "aggressive_positive_ev_switch_margin_bps", 0.0),
                    aggressive_positive_ev_floor_bps=getattr(decision, "aggressive_positive_ev_floor_bps", 0.0),
                    direct_taker_authorized=int(bool(getattr(decision, "direct_taker_authorized", False))),
                    taker_authority=getattr(decision, "taker_authority", "NONE"),
                    allowed_loss_floor_bps=getattr(decision, "allowed_loss_floor_bps", None),
                    economic_direct_max_loss_bps=getattr(decision, "economic_direct_max_loss_bps", None),
                    failed_exit_count=getattr(decision, "failed_exit_count", 0),
                    time_since_first_exit_attempt=getattr(decision, "time_since_first_exit_attempt", 0.0),
                    wait_ev=(
                        getattr(getattr(decision, "unified_exit", None), "wait_ev_bps", None)
                        if getattr(decision, "unified_exit", None) is not None
                        else getattr(getattr(decision, "maker_taker_ev", None), "expected_maker_exit_value", None)
                    ),
                    taker_ev=(
                        getattr(getattr(decision, "unified_exit", None), "taker_net_bps", None)
                        if getattr(decision, "unified_exit", None) is not None
                        else getattr(getattr(decision, "maker_taker_ev", None), "expected_taker_exit_value", None)
                    ),
                    aggressive_maker_ev=(
                        getattr(getattr(decision, "unified_exit", None), "maker_net_bps", None)
                        if getattr(decision, "unified_exit", None) is not None
                        else getattr(getattr(decision, "maker_taker_ev", None), "expected_maker_exit_value", decision.maker_exit_ev)
                    ),
                    early_escape_trigger=(
                        int(bool(getattr(getattr(decision, "unified_exit", None), "early_escape_trigger", False)))
                    ),
                    early_escape_reason=(
                        getattr(getattr(decision, "unified_exit", None), "early_escape_reason", "")
                    ),
                    protective_loss_headroom_bps=(
                        getattr(getattr(decision, "unified_exit", None), "protective_loss_headroom_bps", None)
                    ),
                    protective_margin_bps=(
                        getattr(getattr(decision, "unified_exit", None), "protective_margin_bps", None)
                    ),
                    **econ.as_log(),
                    **(
                        getattr(decision, "action_utility", None).as_log()
                        if getattr(decision, "action_utility", None) is not None
                        else {}
                    ),
                )
            except Exception:
                pass
        ev_log = {}
        maker_taker_ev = getattr(decision, "maker_taker_ev", None)
        if maker_taker_ev is not None:
            ev_log.update(maker_taker_ev.as_log())
        action_utility = getattr(decision, "action_utility", None)
        if action_utility is not None:
            ev_log.update(action_utility.as_log())
        unified_exit = getattr(decision, "unified_exit", None)
        if unified_exit is not None:
            ev_log.update(unified_exit.as_log())
        try:
            self._emit(
                "REALIZATION",
                force=True,
                tick=getattr(self, "_tick", None),
                **decision.as_log(),
            )
        except Exception:
            pass
        if self.debug_enabled:
            record = self._book_record(book_id)
            record["exit_urgency"] = decision.exit_urgency
            record["inventory_state"] = decision.state
            record["realization_action"] = decision.selected_action
        try:
            self._research_velocity_state().note_exit_intent(
                int(book_id), str(decision.selected_action or ""),
            )
        except Exception:
            pass
        if decision.selected_action == ACTION_TAKER:
            long_pos = float(inventory.net_base) > 0.0
            frac = float(getattr(decision, "taker_qty_frac", 1.0) or 1.0)
            cap = float(getattr(self, "research_hybrid_partial_frac_cap", 0.90) or 0.90)
            econ = getattr(decision, "taker_economics", None)
            trigger_token = str(getattr(decision, "trigger", "") or "")
            catastrophic = bool(getattr(econ, "catastrophic", False)) or trigger_token in {
                "TAKER_CATASTROPHIC", UNIFIED_HARD_TAKER,
            }
            unified = getattr(decision, "unified_exit", None)
            unified_action = str(getattr(unified, "action", "") or "")
            if catastrophic:
                frac = 1.0
            elif unified_action in {UNIFIED_TAKER_PROFIT_LOCK, UNIFIED_TAKER_PROTECT} and raw_inv < 2.0 * min_size - 1e-12:
                # A 0.25 position at a 0.25 exchange minimum cannot be split safely.
                frac = 1.0
            else:
                frac = min(max(0.0, frac), cap)
            sized = choose_reduce_quantity(
                inventory=float(inventory.net_base),
                desired=raw_inv * frac,
                min_order=min_size,
                volume_decimals=vol_dec,
            )
            take_qty = sized.quantity
            try:
                self._emit(
                    "EXIT_QTY",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    book=int(book_id),
                    selected_action=decision.selected_action,
                    **sized.as_log(),
                )
            except Exception:
                pass
            if take_qty <= 0.0:
                return placed
            try:
                self._emit(
                    "HYBRID",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    book=int(book_id),
                    selected_action=decision.selected_action,
                    trigger=decision.trigger,
                    hybrid_reason=getattr(decision, "hybrid_reason", None),
                    taker_lock_pnl_bps=getattr(decision, "taker_lock_pnl_bps", None),
                    taker_crossing_cost_bps=getattr(decision, "taker_crossing_cost_bps", None),
                    maker_exit_ev=decision.maker_exit_ev,
                    maker_fill_hazard=getattr(decision, "maker_fill_hazard", None),
                    taker_qty_frac=frac,
                    requested_qty=take_qty,
                    inventory=qty,
                    allowed=1,
                    **ev_log,
                )
            except Exception:
                pass
            if self._execute_aggressive_close(response, book_id, book, take_qty, long_pos):
                placed += 1
                self._research_actual_taker_orders = int(
                    getattr(self, "_research_actual_taker_orders", 0) or 0
                ) + 1
                self._research_note_exit_attempt(
                    int(book_id), str(decision.selected_action or ""), placed=True,
                )
                self._inventory_reason[book_id] = str(
                    getattr(decision, "trigger", "SELECTIVE_TAKER") or "SELECTIVE_TAKER"
                )
            return placed
        try:
            self._emit(
                "HYBRID",
                force=False,
                tick=getattr(self, "_tick", None),
                book=int(book_id),
                selected_action=decision.selected_action,
                trigger=decision.trigger,
                hybrid_reason=getattr(decision, "hybrid_reason", None),
                taker_lock_pnl_bps=getattr(decision, "taker_lock_pnl_bps", None),
                taker_crossing_cost_bps=getattr(decision, "taker_crossing_cost_bps", None),
                maker_exit_ev=decision.maker_exit_ev,
                maker_fill_hazard=getattr(decision, "maker_fill_hazard", None),
                taker_qty_frac=getattr(decision, "taker_qty_frac", 0.0),
                requested_qty=0.0,
                inventory=qty,
                allowed=0,
                **ev_log,
            )
        except Exception:
            pass
        try:
            self._emit(
                "EXIT_QTY",
                force=True,
                tick=getattr(self, "_tick", None),
                book=int(book_id),
                selected_action=decision.selected_action,
                **flatten.as_log(),
            )
        except Exception:
            pass
        unified = getattr(decision, "unified_exit", None)
        unified_px = getattr(unified, "maker_price", None) if unified is not None else None
        n = self._research_place_maker_exit(
            response, state, book_id, book, inventory, qty, decision.selected_action,
            close_price=unified_px,
        )
        if n:
            placed += n
            self._research_note_exit_attempt(
                int(book_id), str(decision.selected_action or ""), placed=True,
            )
            self._inventory_reason[book_id] = decision.selected_action
        return placed

    def _research_dust_age_ticks(self, book_id: int, inventory) -> int:
        parked = (getattr(self, "_research_parked_dust", {}) or {}).get(int(book_id)) or {}
        tick = int(getattr(self, "_tick", 0) or 0)
        first = parked.get("first_tick")
        if first is None:
            return max(0, int(getattr(inventory, "position_ticks", 0) or 0))
        return max(0, tick - int(first or tick))

    def _research_evaluate_dust(
        self,
        book_id: int,
        book,
        inventory,
        state,
        regime_params: RegimeParamSet,
        reduce_qty: float,
    ):
        profile = self._research_profile_for_book(book_id)
        ev = (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id))
        expected_markout = self._research_conservative_markout(int(book_id))
        if ev is not None:
            expected_markout = float(getattr(ev, "expected_markout_bps", expected_markout) or expected_markout)
        vol = 0.0
        if profile is not None:
            try:
                vol = float(getattr(profile, "volatility", 0.0) or 0.0)
            except (TypeError, ValueError):
                vol = 0.0
        ofi_val = None
        ofi_snap = self._research_ofi_snapshot(int(book_id))
        if ofi_snap.supported:
            ofi_val = (
                ofi_snap.ofi_fast
                if ofi_snap.ofi_fast is not None
                else ofi_snap.ofi_normalized
            )
        remaining = self._research_observations_remaining(book_id)
        unreal = inventory.unrealized_bps
        bid = float(book.bids[0].price)
        ask = float(book.asks[0].price)
        mid = 0.5 * (bid + ask)
        spread_bps = ((ask - bid) / mid) * 10_000.0 if mid > 0.0 else 0.0
        stop = max(float(getattr(regime_params, "stop_loss_bps", 35.0) or 35.0), 1e-9)
        stop_hit = unreal is not None and float(unreal) <= -stop
        fee = self._research_live_fee_bps(int(book_id), is_maker=False)
        slip = float(self.research_aggressive_close_fee_buffer_bps)
        return evaluate_dust_action(
            inventory=float(inventory.net_base),
            min_order=max(0.0, float(self._research_exchange_min_order_size)),
            reduce_qty=float(reduce_qty),
            age_ticks=float(self._research_dust_age_ticks(book_id, inventory)),
            unrealized_pnl=unreal,
            spread_bps=spread_bps,
            fee_bps=fee,
            slippage_bps=slip,
            expected_markout=expected_markout,
            volatility=vol,
            ofi=ofi_val,
            inventory_ratio=self._inventory_util(inventory),
            kappa_need=kappa_completion_need(remaining, unreal),
            volume_cap_headroom=self._research_volume_cap_headroom(state, book_id),
            stop_loss_hit=bool(stop_hit),
            band=getattr(inventory, "band", None),
            tiny_fraction=float(getattr(self, "research_dust_tiny_fraction", 0.50)),
            moderate_age_ticks=float(
                getattr(self, "research_dust_moderate_age_ticks", 400)
            ),
            maker_ev_floor_bps=float(
                getattr(self, "research_dust_maker_ev_floor_bps", 0.0)
            ),
            net_floor_bps=float(getattr(self, "research_taker_net_floor_bps", 0.0)),
            eps=self._execution_flat_epsilon(),
        )

    def _research_manage_dust(
        self,
        response,
        state,
        book_id: int,
        book,
        inventory,
        regime_params: RegimeParamSet,
        *,
        compact_selected: bool,
    ) -> int:
        """Park tiny dust. Maker-clean only when EV is positive. Never loss-make CROSS."""
        vol_dec = int(getattr(state.config, "volumeDecimals", 8) or 8)
        min_size = exchange_min_order_size(
            getattr(self, "_research_exchange_min_order_size", 0.0), vol_dec,
        )
        sized = choose_reduce_quantity(
            inventory=float(inventory.net_base),
            desired=abs(float(inventory.net_base)),
            min_order=min_size,
            volume_decimals=vol_dec,
        )
        decision = self._research_evaluate_dust(
            book_id, book, inventory, state, regime_params, sized.quantity,
        )
        self._research_dust_econ_last[int(book_id)] = decision
        try:
            self._emit(
                "DUST_ECON",
                force=True,
                tick=getattr(self, "_tick", None),
                book=int(book_id),
                compact_selected=int(bool(compact_selected)),
                **decision.as_log(),
            )
        except Exception:
            pass
        abs_base = abs(float(inventory.net_base))
        if not decision.allow or decision.reduce_qty <= 1e-18:
            if self.debug_enabled:
                record = self._book_record(book_id)
                record["dust_position"] = True
                record["dust_quarantine"] = True
                record["dust_qty"] = abs_base
                record["dust_action"] = decision.action
                record["dust_reason"] = decision.reason
                record["dust_band"] = decision.band
                record["dust_compact_selected"] = compact_selected
            return 0
        if not compact_selected and decision.action != DUST_ACTION_TAKER:
            if self.debug_enabled:
                record = self._book_record(book_id)
                record["dust_position"] = True
                record["dust_quarantine"] = True
                record["dust_qty"] = abs_base
                record["dust_action"] = decision.action
                record["dust_reason"] = decision.reason
                record["dust_compact_selected"] = compact_selected
            return 0
        qty = float(decision.reduce_qty)
        if decision.action in {DUST_ACTION_PASSIVE, DUST_ACTION_COMPETITIVE}:
            self._research_dust_compact_attempts += 1
            maker_action = (
                ACTION_COMPETITIVE
                if decision.action == DUST_ACTION_COMPETITIVE
                else ACTION_PASSIVE
            )
            n = self._research_place_maker_exit(
                response, state, book_id, book, inventory, qty, maker_action,
            )
            if n:
                self._research_dust_compact_orders += 1
                self._research_dust_compact_active[book_id] = int(
                    getattr(self, "_tick", 0) or 0
                )
                if self.research_dust_compact_adaptive:
                    self._record_dust_compaction_attempt(book_id)
                self._inventory_reason[book_id] = decision.action
                self._emit(
                    "POSITION_GUARD",
                    tick=getattr(self, "_tick", None),
                    book_id=book_id,
                    reason=decision.action,
                    net_base=inventory.net_base,
                    min_order_size=min_size,
                    projected_full_fill_net=decision.inventory_after,
                    exposure_nonincreasing=True,
                    instructions=n,
                )
                if self.debug_enabled:
                    record = self._book_record(book_id)
                    record["action"] = "MANAGE"
                    record["reason"] = decision.action
                    record["dust_compact"] = True
                    record["dust_compact_qty"] = qty
                    record["instructions"] = n
                return n
            self._emit(
                "POSITION_GUARD",
                tick=getattr(self, "_tick", None),
                book_id=book_id,
                reason="DUST_COMPACT_BLOCKED",
                net_base=inventory.net_base,
                min_order_size=min_size,
                dust_reason=decision.reason,
                exposure_nonincreasing=True,
            )
            return 0
        if decision.action == DUST_ACTION_TAKER:
            long_pos = float(inventory.net_base) > 0.0
            placed = self._execute_aggressive_close(
                response, book_id, book, qty, long_pos,
            )
            if not placed:
                if self.debug_enabled:
                    record = self._book_record(book_id)
                    record["dust_position"] = True
                    record["dust_quarantine"] = True
                    record["dust_qty"] = abs_base
                    record["dust_action"] = decision.action
                    record["dust_reason"] = "PLACE_FAIL"
                return 0
            self._inventory_reason[book_id] = decision.action
            self._emit(
                "POSITION_GUARD",
                tick=getattr(self, "_tick", None),
                book_id=book_id,
                reason=decision.action,
                net_base=inventory.net_base,
                projected_net=decision.inventory_after,
                min_order_size=min_size,
                dust_reason=decision.reason,
                exposure_nonincreasing=True,
            )
            if self.debug_enabled:
                record = self._book_record(book_id)
                record["action"] = "MANAGE"
                record["reason"] = decision.action
                record["dust_taker"] = True
            return 1
        return 0

    def _research_try_dust_escape(
        self,
        response,
        state,
        book_id: int,
        book,
        inventory,
        min_size: float,
    ) -> bool:
        """Experimental old-dust reducer. Passive compact remains the default path."""
        if self._research_in_transition_quarantine():
            return False
        if self._research_dust_econ_on():
            return False
        if not getattr(self, "research_enable_dust_escape", False):
            return False
        if min_size <= 0.0 or not self._is_dust_qty(inventory.net_base):
            return False
        parked = (getattr(self, "_research_parked_dust", {}) or {}).get(int(book_id)) or {}
        tick = int(getattr(self, "_tick", 0) or 0)
        first = int(parked.get("first_tick", tick) or tick)
        age = max(0, tick - first)
        ctx = (getattr(self, "_research_aggressive_context", {}) or {}).get(int(book_id)) or {}
        net_touch = ctx.get("net_touch_bps")
        benefit = 0.0 if net_touch is None else float(net_touch)
        benefit += min(5.0, 2.0 * (age / max(1.0, float(self.research_dust_escape_min_age_ticks))))
        cost = float(self.research_dust_escape_cost_bps)
        ok, after, reason = dust_escape_allowed(
            inventory_before=float(inventory.net_base),
            reduce_qty=float(min_size),
            age_ticks=age,
            min_age_ticks=int(self.research_dust_escape_min_age_ticks),
            benefit_bps=benefit,
            cost_bps=cost,
            eps=self._execution_flat_epsilon(),
        )
        self._research_dust_escape_attempts += 1
        if not ok:
            self._emit(
                "POSITION_GUARD",
                tick=tick,
                book_id=book_id,
                reason="DUST_ESCAPE_BLOCKED",
                net_base=inventory.net_base,
                projected_net=after,
                escape_reason=reason,
                age_ticks=age,
                benefit_bps=benefit,
                cost_bps=cost,
                exposure_nonincreasing=abs(after) < abs(float(inventory.net_base)),
            )
            return False
        long_pos = float(inventory.net_base) > 0.0
        placed = self._execute_aggressive_close(
            response, book_id, book, float(min_size), long_pos,
        )
        if not placed:
            self._emit(
                "POSITION_GUARD",
                tick=tick,
                book_id=book_id,
                reason="DUST_ESCAPE_BLOCKED",
                net_base=inventory.net_base,
                projected_net=after,
                escape_reason="PLACE_FAIL",
                age_ticks=age,
            )
            return False
        self._research_dust_escape_orders += 1
        self._inventory_reason[book_id] = "DUST_ESCAPE"
        self._emit(
            "POSITION_GUARD",
            tick=tick,
            book_id=book_id,
            reason="DUST_ESCAPE",
            net_base=inventory.net_base,
            projected_net=after,
            min_order_size=min_size,
            age_ticks=age,
            benefit_bps=benefit,
            cost_bps=cost,
            exposure_nonincreasing=True,
        )
        if self.debug_enabled:
            record = self._book_record(book_id)
            record["action"] = "MANAGE"
            record["reason"] = "DUST_ESCAPE"
            record["dust_escape"] = True
        return True

    def _dust_fill_matches_recent_compaction(self, book_id: int) -> bool:
        """Telemetry-only attribution guard for DUST_COMPACT fills.

        A dust transition is counted as a compaction fill only when this book
        actually emitted a DUST_COMPACT order recently. This prevents natural
        residual cleanup from inflating the compaction-fill metric.
        """
        submitted_tick = self._research_dust_compact_active.get(int(book_id))
        if submitted_tick is None:
            return False
        now = int(getattr(self, "_tick", 0) or 0)
        # Notices/fills can be observed on the following request. Keep a short
        # two-tick attribution window; repeated compaction attempts refresh it.
        return 0 <= now - int(submitted_tick) <= 2

    def onTrade(self, event, validator: str | None = None) -> None:
        book_id = getattr(event, "bookId", None)
        own = (
            getattr(event, "takerAgentId", None) == getattr(self, "uid", None)
            or getattr(event, "makerAgentId", None) == getattr(self, "uid", None)
        )
        before = 0.0
        pnl_before = 0.0
        if book_id is not None:
            before = float(self._position_tracker_snapshot(book_id).net_qty)
            pnl_before = float(self._pnl_tick_buffer.get(book_id, 0.0))

        super().onTrade(event, validator)
        if book_id is None or not own:
            return

        after = float(self._position_tracker_snapshot(book_id).net_qty)
        pnl_after = float(self._pnl_tick_buffer.get(book_id, 0.0))
        realized_delta = pnl_after - pnl_before
        kappa_before_authoritative = self._research_kappa_book(int(book_id)).realized_observation_count
        if abs(realized_delta) > 1e-12:
            self._research_note_realized_observation(
                book_id, timestamp=getattr(event, "timestamp", None)
            )
            # Invalidate cached miner-side Kappa immediately on a new realized
            # observation. The authoritative rolling state still comes from
            # realized_pnl_history on the next request.
            self._research_realized_generation = int(
                getattr(self, "_research_realized_generation", 0) or 0
            ) + 1
            self._research_local_kappa_cache_value = None
        kappa_after_authoritative = self._research_kappa_book(int(book_id)).realized_observation_count

        before_was_dust = self._is_dust_qty(before)
        self._refresh_dust_state(book_id, after, emit=True)
        eps = self._execution_flat_epsilon()

        if abs(before) < eps and abs(after) >= eps:
            transition = "OPEN"
            self._research_position_opens += 1
        elif abs(before) >= eps and abs(after) < eps:
            transition = "FLAT"
            self._research_round_trip_closes += 1
            self._research_position_reductions += 1
            self._research_round_trip_samples_by_book[book_id] = (
                self._research_round_trip_samples_by_book.get(book_id, 0) + 1
            )
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
                if self.research_dust_compact_adaptive:
                    self._record_dust_compaction_success(book_id)
        elif before * after < 0.0:
            # A cross closes the old FIFO lifecycle and opens a new opposite
            # residual. Check this BEFORE REDUCE/INCREASE magnitude tests.
            transition = "CROSS"
            self._research_position_reductions += 1
            if abs(realized_delta) > 1e-12:
                self._research_round_trip_closes += 1
                self._research_round_trip_samples_by_book[book_id] = (
                    self._research_round_trip_samples_by_book.get(book_id, 0) + 1
                )
            self._position_ticks[book_id] = 0
            self._research_position_tick_seen[book_id] = int(getattr(self, "_tick", 0) or 0)
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
                if self.research_dust_compact_adaptive:
                    self._record_dust_compaction_success(book_id)
                self._inventory_reason[book_id] = "DUST_COMPACT"
        elif abs(after) < abs(before) - eps:
            transition = "REDUCE"
            self._research_position_reductions += 1
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
                if self.research_dust_compact_adaptive:
                    self._record_dust_compaction_success(book_id)
        elif abs(after) > abs(before) + eps:
            transition = "INCREASE"
        else:
            transition = "UNCHANGED"

        if transition in {"OPEN", "FLAT", "CROSS"}:
            self._research_reset_exit_attempt(int(book_id))

        round_trip_event = transition == "FLAT" or (
            transition == "CROSS" and abs(realized_delta) > 1e-12
        )
        try:
            vel = self._research_velocity_state()
            fill_qty = abs(float(getattr(event, "quantity", 0.0) or 0.0))
            vel.note_volume(fill_qty)
            ts = getattr(event, "timestamp", None)
            if ts is None:
                ts = getattr(self, "_research_last_sim_ts", 0) or 0
            ts = float(ts)
            if transition == "OPEN":
                vel.note_open(int(book_id), ts)
            elif transition in {"FLAT", "REDUCE", "CROSS"}:
                vel.note_realized(
                    int(book_id),
                    ts,
                    closed_qty=abs(before) if round_trip_event else 0.0,
                    round_trip=round_trip_event,
                    flatten=transition in {"FLAT", "CROSS"},
                )
            if transition in {"FLAT", "REDUCE", "CROSS"} and abs(realized_delta) > 1e-12:
                vel.note_exit_fill(int(book_id), realized_delta)
            kappa = self._research_kappa_book(int(book_id))
            if kappa.realized_observation_count > 0:
                vel.note_active_book(int(book_id))
            vel.note_qualified_book(int(book_id), eligible=bool(kappa.eligible))
        except Exception:
            pass

        actionable_event = None
        is_maker = getattr(event, "makerAgentId", None) == getattr(self, "uid", None)
        if is_maker:
            # Parent semantics: event.side==1 means our maker was the buyer;
            # event.side==0 means our maker was the seller.
            side = "buy" if getattr(event, "side", None) == 1 else "sell"
            mem = self._mem(book_id)
            bucket = int(
                getattr(mem, "last_buy_dist_bucket", 0)
                if side == "buy"
                else getattr(mem, "last_sell_dist_bucket", 0)
            )
            actionable_event = self._record_actionable_maker_fill(
                book_id=int(book_id),
                side=side,
                bucket=bucket,
                before=before,
                after=after,
                fill_qty=float(getattr(event, "quantity", 0.0) or 0.0),
                timestamp=getattr(event, "timestamp", None),
            )

        try:
            self._research_on_own_fill(
                event=event,
                book_id=int(book_id),
                before=before,
                after=after,
                kappa_after=int(kappa_after_authoritative),
                kappa_before=int(kappa_before_authoritative),
                is_maker=is_maker,
            )
        except Exception:
            pass

        self._emit(
            "POSITION",
            tick=getattr(self, "_tick", None),
            timestamp=getattr(event, "timestamp", None),
            book_id=book_id,
            transition=transition,
            net_before=before,
            net_after=after,
            realized_pnl_delta=realized_delta,
            realized_book_observations=int(kappa_after_authoritative),
            lifetime_realized_book_observations=int(
                self._research_realized_observations_by_book.get(book_id, 0) or 0
            ),
            round_trip=round_trip_event,
            round_trip_total=self._research_round_trip_closes,
            round_trip_book_samples=self._research_round_trip_samples_by_book.get(book_id, 0),
            execution_flat_epsilon=eps,
            reason=self._inventory_reason.get(
                book_id,
                "FLAT" if transition == "FLAT" else "UNKNOWN",
            ),
            actionable_fill_class=(
                actionable_event.get("classification") if actionable_event else None
            ),
            actionable_fill_fraction=(
                actionable_event.get("fill_fraction") if actionable_event else None
            ),
        )

    def _research_book_exit_rate(self, book_id: int | None, inventory) -> float | None:
        if book_id is None:
            return None
        samples = int(
            (getattr(self, "_research_round_trip_samples_by_book", {}) or {}).get(
                int(book_id), 0
            ) or 0
        )
        sim_t = self._research_simulation_time_s()
        age = float(getattr(inventory, "position_ticks", 0) or 0)
        if samples > 0 and sim_t > 0.0:
            return samples / sim_t
        if (
            age >= float(getattr(self, "research_realize_age_ticks", 8) or 8)
            and abs(float(getattr(inventory, "net_base", 0.0) or 0.0)) > 0.0
        ):
            return 0.0
        return None

    def _research_entry_toxicity(self, book_id: int | None, profile) -> float:
        toxic = 0.0
        if book_id is not None:
            ev = (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id))
            if ev is not None:
                toxic = max(toxic, float(getattr(ev, "adverse_selection_risk", 0.0) or 0.0))
            mem = self._mem(int(book_id))
            streak = float(getattr(mem, "loss_streak", 0) or 0)
            cap = max(float(getattr(self, "toxic_loss_streak", 4) or 4), 1.0)
            toxic = max(toxic, min(1.0, streak / cap))
        markout_mean, markout_n = (None, 0)
        if book_id is not None:
            markout_mean, markout_n = self._research_markout_snapshot(int(book_id))
        if markout_mean is not None and markout_n > 0 and float(markout_mean) < 0.0:
            toxic = max(toxic, min(1.0, abs(float(markout_mean)) / 12.0))
        return max(0.0, min(1.0, toxic))

    def _research_entry_size_decision(self, *, base_size: float, profile, inventory):
        book_id = getattr(profile, "book_id", None)
        ev = None
        if book_id is not None:
            ev = (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id))
        markout = 0.0
        if ev is not None:
            markout = float(getattr(ev, "expected_markout_bps", 0.0) or 0.0)
        elif book_id is not None:
            mean, n = self._research_markout_snapshot(int(book_id))
            if mean is not None and n > 0:
                markout = float(mean)
        drawdown = 0.0
        if book_id is not None:
            try:
                drawdown = min(0.0, float(getattr(self._mem(int(book_id)), "recent_pnl", 0.0) or 0.0))
            except (TypeError, ValueError, AttributeError):
                drawdown = 0.0
        vol = 0.0
        try:
            vol = float(getattr(profile, "volatility", 0.0) or 0.0)
        except (TypeError, ValueError):
            vol = 0.0
        return allowed_entry_size(
            base_size=float(base_size),
            existing_inventory=abs(float(getattr(inventory, "net_base", 0.0) or 0.0)),
            max_inventory=float(getattr(self, "max_inventory_base", 1.2) or 1.2),
            inventory_age=float(getattr(inventory, "position_ticks", 0) or 0),
            volatility=vol,
            toxicity=self._research_entry_toxicity(book_id, profile),
            expected_markout=markout,
            ofi_against=self._research_ofi_against(
                int(book_id) if book_id is not None else 0,
                float(getattr(inventory, "net_base", 0.0) or 0.0),
            ) if book_id is not None else 0.0,
            volume_cap_headroom=(
                self._research_volume_cap_headroom(
                    getattr(self, "_research_volume_cap_state", None),
                    int(book_id),
                )
                if book_id is not None
                else 1.0
            ),
            exit_rate=self._research_book_exit_rate(book_id, inventory),
            recent_drawdown=drawdown,
            hard_max_entry=float(getattr(self, "mm_base_size", base_size) or base_size),
        )

    def dynamic_order_size(
        self,
        base_size: float,
        profile: BookProfile,
        regime_params: RegimeParamSet,
        inventory: InventorySnapshot,
        vol_dec: int,
        mid: float | None = None,
    ) -> float:
        # Reproduce Strategy1 sizing but use dimensionally-correct base
        # utilization rather than dividing a capital ratio by a clip count.
        predict_score = float(getattr(profile, "predict_score", 0.0) or 0.0)
        volatility = float(getattr(profile, "volatility", 0.0) or 0.0)
        confidence = max(0.5, min(2.0, 1.0 + abs(predict_score)))
        vol_scale = 1.0
        if volatility > 0.0:
            vol_scale = max(
                0.5, min(2.0, float(self.profile_vol_scale) / volatility)
            )
        spread_factor = 1.0
        if profile.spread is not None and mid is not None and mid > 0.0:
            spread_bps = (float(profile.spread) / mid) * 10_000.0
            spread_factor = max(0.5, min(1.5, 1.0 - spread_bps / 20.0))
        kappa_scale = 1.0
        if profile.raw_kappa is not None:
            kappa_scale = max(0.5, min(1.5, 1.0 + float(profile.raw_kappa) * 0.2))
        inventory_factor = max(0.3, 1.0 - min(1.0, self._inventory_util(inventory)))
        raw_model_size = (
            float(base_size)
            * confidence
            * float(regime_params.size_mult)
            * vol_scale
            * spread_factor
            * kappa_scale
            * inventory_factor
        )
        size = self._round_order_size(raw_model_size, vol_dec)
        min_size = max(0.0, self._research_exchange_min_order_size)
        promoted = False
        rounded_before_promotion = size
        entry = None
        admission = None
        if getattr(self, "research_enable_entry_size", True):
            entry = self._research_entry_size_decision(
                base_size=float(base_size),
                profile=profile,
                inventory=inventory,
            )
            size = min(size, self._round_order_size(float(entry.entry_size), vol_dec))
        remaining = max(
            0.0,
            float(self.max_inventory_base) - abs(float(inventory.net_base)),
        )
        safe_size = float(entry.entry_size) if entry is not None else float(size)
        book_id = getattr(profile, "book_id", None)
        trading_ev = 0.0
        if book_id is not None:
            ev = (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id))
            if ev is not None:
                trading_ev = float(getattr(ev, "trading_ev", 0.0) or 0.0)
        headroom = 1.0
        if book_id is not None:
            headroom = self._research_volume_cap_headroom(
                getattr(self, "_research_volume_cap_state", None),
                int(book_id),
            )
        risk_after_min = 0.0
        if min_size > 0.0:
            after_min = abs(float(inventory.net_base)) + min_size
            inv_cap = max(float(getattr(self, "max_inventory_base", 1.2) or 1.2), 1e-9)
            risk_after_min = inventory_holding_risk(
                inventory_ratio=after_min / inv_cap,
                volatility=volatility,
                inventory_age=float(getattr(inventory, "position_ticks", 0) or 0),
            )
        elif entry is not None:
            risk_after_min = float(entry.inventory_risk_after_fill)
        exit_capacity = (
            float(entry.expected_exit_capacity) if entry is not None else remaining
        )
        kappa_remaining = None
        if book_id is not None:
            try:
                kappa_remaining = int(self._research_kappa_book(int(book_id)).observations_remaining)
            except Exception:
                kappa_remaining = None
        # V4.12 hard concurrency gate. The screen suppresses new flat-book
        # candidates when already saturated; this second gate also accounts for
        # new books planned earlier in the same response so a burst cannot jump
        # from 5 open books to 8 merely because several quotes are built at once.
        if book_id is not None and abs(float(inventory.net_base)) <= self._execution_flat_epsilon():
            max_open = int(getattr(self, "research_max_open_books", 6) or 6)
            base_open = int(
                (getattr(self, "_research_inventory_lane_diag", {}) or {}).get(
                    "actual_nonflat_inventory", 0
                ) or 0
            )
            now_tick = int(getattr(self, "_tick", 0) or 0)
            cache_rows = getattr(self, "_research_entry_admission_cache", {}) or {}
            planned_books = {
                int(bid) for bid, row in cache_rows.items()
                if isinstance(row, dict)
                and int(row.get("tick", -1) or -1) == now_tick
                and bool(row.get("planned_open", False))
            }
            if int(book_id) not in planned_books and base_open + len(planned_books) >= max_open:
                try:
                    self._emit(
                        "ENTRY_SIZE",
                        force=True,
                        tick=getattr(self, "_tick", None),
                        book=int(book_id),
                        entry_size=0.0,
                        safe_size=float(safe_size),
                        admission="BLOCKED",
                        allow=0,
                        trigger="OPEN_BOOK_CAP",
                        min_order=float(min_size),
                        open_books=base_open,
                        planned_open_books=len(planned_books),
                        max_open_books=max_open,
                    )
                except Exception:
                    pass
                return 0.0

        if getattr(self, "research_enable_min_order_admission", True):
            admission = admit_minimum_order(
                safe_size=safe_size,
                min_order=min_size,
                tolerance=float(getattr(self, "research_min_order_tolerance", 0.20)),
                trading_ev=trading_ev,
                inventory_risk=risk_after_min,
                exit_capacity=exit_capacity,
                volume_headroom=headroom,
                remaining_inventory=remaining,
                enable_near_safe=bool(getattr(self, "research_near_safe_enabled", True)),
                min_trading_ev=float(getattr(self, "research_near_safe_min_ev", 0.0)),
                max_inventory_risk=float(
                    getattr(self, "research_near_safe_max_inventory_risk", 0.35)
                ),
                min_headroom=float(getattr(self, "research_near_safe_min_headroom", 0.25)),
                enable_positive_ev_override=bool(
                    getattr(self, "research_positive_ev_min_order_override", False)
                ),
                positive_ev_min_safe_fraction=float(
                    getattr(self, "research_positive_ev_min_safe_fraction", 0.35)
                ),
                positive_ev_min_exit_fraction=float(
                    getattr(self, "research_positive_ev_min_exit_fraction", 0.45)
                ),
                positive_ev_min_trading_ev=float(
                    getattr(self, "research_positive_ev_min_trading_ev", 0.05)
                ),
                observations_remaining=kappa_remaining,
                enable_one_away_exact_min=bool(
                    getattr(self, "research_one_away_exact_min_enabled", True)
                ),
                one_away_min_trading_ev=float(
                    getattr(self, "research_one_away_exact_min_ev_bps", 0.0)
                ),
                one_away_min_safe_fraction=float(
                    getattr(self, "research_one_away_exact_min_safe_fraction", 0.50)
                ),
                one_away_min_exit_fraction=float(
                    getattr(self, "research_one_away_exact_min_exit_fraction", 0.90)
                ),
                enable_two_away_exact_min=bool(
                    getattr(self, "research_two_away_exact_min_enabled", True)
                ),
                two_away_min_trading_ev=float(
                    getattr(self, "research_two_away_exact_min_ev", 0.0)
                ),
                two_away_max_inventory_risk=float(
                    getattr(self, "research_two_away_exact_min_max_inventory_risk", 0.35)
                ),
                two_away_min_exit_fraction=float(
                    getattr(self, "research_two_away_exact_min_exit_fraction", 0.20)
                ),
                two_away_min_headroom=float(
                    getattr(self, "research_two_away_exact_min_min_headroom", 0.25)
                ),
            )
            if book_id is not None:
                self._research_entry_admission_cache[int(book_id)] = {
                    "tick": int(getattr(self, "_tick", 0) or 0),
                    "allow": bool(admission.allow),
                    "band": str(admission.band),
                    "safe_size": float(admission.safe_size),
                    "min_order": float(admission.min_order),
                    "planned_open": bool(
                        admission.allow
                        and abs(float(inventory.net_base)) <= self._execution_flat_epsilon()
                    ),
                }
            if not admission.allow:
                size = 0.0
            elif admission.band == ADMISSION_SAFE:
                if size + 1e-12 >= min_size:
                    size = min(size, float(admission.size), remaining)
                else:
                    size = min(max(min_size, 0.0), float(admission.size), remaining)
            else:
                size = min(float(admission.size), remaining)
                promoted = bool(admission.promoted)
        elif (
            self.research_promote_min_order
            and size > 0.0
            and min_size > 0.0
            and size + 1e-12 < min_size
        ):
            cap = remaining
            if entry is not None:
                cap = min(cap, float(entry.entry_size), float(entry.expected_exit_capacity))
            if min_size <= cap + 1e-12:
                size = round(min_size, vol_dec)
                promoted = True
            else:
                size = 0.0
        size = min(size, remaining)
        if entry is not None or admission is not None:
            if book_id is not None:
                if entry is not None:
                    self._research_entry_size_last[int(book_id)] = entry
                payload = {}
                if entry is not None:
                    payload.update(entry.as_log(book=int(book_id)))
                if admission is not None:
                    payload.update(admission.as_log(book=int(book_id)))
                self._emit(
                    "ENTRY_SIZE",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    **payload,
                )
        if self.debug_enabled and hasattr(profile, "book_id"):
            record = self._book_record(profile.book_id)
            record["dynamic_size_model_raw"] = raw_model_size
            record["dynamic_size_raw"] = rounded_before_promotion
            record["dynamic_size_final"] = size
            record["inventory_util"] = self._inventory_util(inventory)
            record["min_order_size"] = min_size
            record["size_promoted_to_min"] = promoted
            if entry is not None:
                record["entry_size"] = entry.entry_size
                record["expected_exit_capacity"] = entry.expected_exit_capacity
                record["inventory_after_full_fill"] = entry.inventory_after_full_fill
                record["inventory_risk_after_fill"] = entry.inventory_risk_after_fill
            if admission is not None:
                record["admission"] = admission.band
                record["admission_trigger"] = admission.trigger
        return size

    def _research_live_quote(self, book_id: int, side: str):
        store = getattr(self, "_research_quote_store", None)
        if store is None:
            return None
        rec = store.live_for_book_side(int(book_id), side)
        if rec is None or not rec.open:
            return None
        return rec

    def _research_choose_ttl(
        self, book_id: int, profile, state, *, baseline_ns: int,
    ) -> tuple[float | None, str, float | None]:
        baseline = sim_delta_ms(0, int(baseline_ns)) or 500.0
        haz_pack = (getattr(self, "_research_hazard_last", {}) or {}).get(int(book_id), {})
        preds = [p for p in (haz_pack.get("buy"), haz_pack.get("sell")) if p is not None]
        fill_hazard = None
        if preds:
            fill_hazard = sum(float(p.any_fill) for p in preds) / len(preds)
        signals = self._research_book_micro.get(int(book_id)) or {}
        tick = self._research_tick_size(state) or 0.01
        micro_delta = signals.get("microprice_delta")
        vel_ticks = None
        if micro_delta is not None and tick > 0:
            vel_ticks = abs(float(micro_delta)) / tick
        ofi_snap = self._research_ofi_snapshot(int(book_id))
        ofi = None
        if ofi_snap.supported:
            ofi = ofi_snap.ofi_fast if ofi_snap.ofi_fast is not None else ofi_snap.ofi_normalized
        vol = None if profile is None else getattr(profile, "volatility", None)
        queue_ahead = None
        toxic = str(getattr(self, "_research_market_regime", "")).upper() in {"TOXIC", "STRESSED"}
        ttl, reason, _info = choose_ttl_ms(
            baseline_ms=float(baseline),
            min_ms=float(self.research_ttl_min_ms),
            max_ms=float(self.research_ttl_max_ms),
            fill_hazard=fill_hazard,
            volatility=None if vol is None else float(vol),
            ofi=ofi,
            microprice_velocity=vel_ticks,
            toxicity=toxic,
            market_regime=getattr(self, "_research_market_regime", None),
            queue_ahead=queue_ahead,
            stale_velocity_ticks=8.0,
        )
        # V4.11: in a genuinely QUIET book, a 350-500ms quote often expires
        # before any natural interaction. Stretch only non-adverse quotes; hard
        # toxicity/OFI/velocity shortening above remains authoritative.
        regime = str(getattr(self, "_research_market_regime", "") or "").upper()
        if ttl is not None and regime == "QUIET" and reason in {"BASELINE", "STABLE_LONG"}:
            ttl = min(
                float(self.research_ttl_max_ms),
                max(float(ttl), float(getattr(self, "research_quiet_ttl_ms", 1000.0))),
            )
            reason = "QUIET_LONG"
        return ttl, reason, fill_hazard

    def _research_hysteresis_hold_sides(
        self,
        state,
        book_id: int,
        book,
        profile,
        prediction,
        inventory,
        regime_params,
        edge_bias: float,
        chosen_ttl_ms: float | None = None,
    ) -> set[str]:
        hold: set[str] = set()
        if book is None or not getattr(book, "bids", None) or not getattr(book, "asks", None):
            return hold
        try:
            tick_size = self._research_tick_size(state) or 0.01
            now = getattr(state, "timestamp", None)
            price_dec = int(getattr(getattr(state, "config", None), "priceDecimals", 2) or 2)
            prices = self.skewed_quote_prices(
                float(book.bids[0].price),
                float(book.asks[0].price),
                float(getattr(prediction, "score", 0.0) or 0.0),
                float(getattr(inventory, "inventory_ratio", 0.0) or 0.0),
                regime_params,
                price_dec,
                edge_bias=float(edge_bias or 0.0),
            )
            if not prices:
                return hold
            new_buy, new_sell = prices
            alpha = float(getattr(prediction, "score", 0.0) or 0.0)
            ofi_snap = self._research_ofi_snapshot(int(book_id))
            new_ofi = None
            if ofi_snap.supported:
                new_ofi = (
                    ofi_snap.ofi_fast
                    if ofi_snap.ofi_fast is not None
                    else ofi_snap.ofi_normalized
                )
            regime = getattr(self, "_research_market_regime", None)
            try:
                util = float(self._inventory_util(inventory))
            except Exception:
                util = 0.0
            toxic = str(regime or "").upper() in {"TOXIC"} or int(book_id) in (
                getattr(self, "_research_parked_dust", {}) or {}
            )
            ev_row = (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id))
            new_ev = None if ev_row is None else getattr(ev_row, "trading_ev", None)
            hard = toxic or str(getattr(inventory, "band", "")).upper() in {
                "MAX_LONG", "MAX_SHORT",
            }
            if ev_row is not None and not bool(getattr(ev_row, "eligible", True)):
                hard = True
            for side, new_px in (("buy", new_buy), ("sell", new_sell)):
                rec = self._research_live_quote(book_id, side)
                snap = {} if rec is None else dict(rec.snapshot or {})
                age_ms = None if rec is None else sim_delta_ms(rec.submit_ts, now)
                old_ofi = None
                if str(snap.get("ofi_source") or "") == "OFI":
                    old_ofi = snap.get("ofi_fast")
                    if old_ofi is None:
                        old_ofi = snap.get("ofi_normalized")
                live_ttl = None if rec is None else rec.configured_ttl_ms
                decision = should_replace_quote(
                    old_price=None if rec is None else rec.quote_price,
                    new_price=float(new_px),
                    tick_size=float(tick_size),
                    min_price_ticks=float(self.research_hysteresis_min_price_ticks),
                    old_alpha=snap.get("alpha"),
                    new_alpha=alpha,
                    old_ofi=None if old_ofi is None else float(old_ofi),
                    new_ofi=new_ofi,
                    old_regime=None if rec is None else rec.market_regime,
                    new_regime=regime,
                    old_inventory_util=snap.get("inventory_util"),
                    new_inventory_util=util,
                    old_inventory_state=snap.get("inventory_state"),
                    new_inventory_state=getattr(inventory, "band", None),
                    old_toxic=bool(snap.get("toxic")),
                    new_toxic=toxic,
                    order_age_ms=age_ms,
                    ttl_ms=live_ttl,
                    old_ev=snap.get("quote_ev"),
                    new_ev=new_ev,
                    ev_improve_threshold=float(self.research_hysteresis_ev_threshold),
                    chosen_ttl=chosen_ttl_ms if chosen_ttl_ms is not None else live_ttl,
                    hard_safety=hard,
                )
                try:
                    self._emit(
                        "CANCEL_DECISION",
                        force=True,
                        tick=getattr(self, "_tick", None),
                        **decision.as_log(book=int(book_id), side=side),
                    )
                except Exception:
                    pass
                if decision.cancel:
                    self._research_hysteresis_replaces += 1
                else:
                    self._research_hysteresis_holds += 1
                    hold.add(side)
        except Exception:
            return set()
        return hold

    def _research_cancelled_order_ids(self, response, book_id: int) -> set[int]:
        cancelled: set[int] = set()
        for instruction in getattr(response, "instructions", []) or []:
            if int(self._get(instruction, "bookId", "book_id") or -1) != int(book_id):
                continue
            if str(self._get(instruction, "type") or "").upper() != "CANCEL_ORDERS":
                continue
            for row in getattr(instruction, "cancellations", None) or []:
                oid = self._get(row, "orderId", "order_id")
                try:
                    cancelled.add(int(oid))
                except (TypeError, ValueError):
                    continue
        return cancelled

    def _research_side_committed_qty(self, response, book_id: int, side: str) -> float:
        token = str(side).lower()
        cancelled = self._research_cancelled_order_ids(response, int(book_id))
        total = 0.0
        account = (getattr(self, "accounts", {}) or {}).get(int(book_id))
        for order in (getattr(account, "orders", None) or []):
            try:
                if int(getattr(order, "id", -1)) in cancelled:
                    continue
                order_side = "buy" if int(getattr(order, "side", -1)) == 0 else "sell"
                if order_side == token:
                    total += max(0.0, float(getattr(order, "quantity", 0.0) or 0.0))
            except (TypeError, ValueError):
                continue
        for instruction in getattr(response, "instructions", []) or []:
            typ = str(self._get(instruction, "type") or "").upper()
            if typ not in {"PLACE_ORDER_LIMIT", "PLACE_ORDER_MARKET"}:
                continue
            try:
                if int(self._get(instruction, "bookId", "book_id")) != int(book_id):
                    continue
            except (TypeError, ValueError):
                continue
            direction = self._get(instruction, "direction")
            name = str(getattr(direction, "name", direction)).upper()
            inst_side = "buy" if name in {"0", "BUY", "BID", "ORDERDIRECTION.BUY"} else "sell"
            if inst_side != token:
                continue
            try:
                total += max(0.0, float(self._get(instruction, "quantity", "qty", "size") or 0.0))
            except (TypeError, ValueError):
                continue
        return total

    def _research_effective_exposure_allows(
        self, response, book_id: int, side: str, qty: float, inventory_net: float
    ) -> bool:
        q = max(0.0, float(qty or 0.0))
        if q <= 0.0:
            return False
        committed = self._research_side_committed_qty(response, int(book_id), side)
        token = str(side).lower()
        net = float(inventory_net or 0.0)
        # Never submit a second live/pending order on the same side. This is the
        # V4.10 anti-race rule that prevents delayed fills from stacking.
        if committed > self._execution_flat_epsilon():
            return False
        if net > self._execution_flat_epsilon() and token == "sell":
            return q <= net + 1e-12
        if net < -self._execution_flat_epsilon() and token == "buy":
            return q <= abs(net) + 1e-12
        max_inv = max(0.0, float(getattr(self, "max_inventory_base", 0.0) or 0.0))
        if token == "buy":
            return net + q <= max_inv + 1e-12
        return net - q >= -max_inv - 1e-12

    def _place_skewed_quotes(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        book,
        profile: BookProfile,
        prediction: DirectionForecast,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        size: float,
        edge_bias: float,
        stats: dict | None = None,
    ) -> int:
        """Quote with isolated NORMAL and KAPPA_COMPLETION scheduler lanes.

        V4 allowed every Kappa-completion failure to consume the same shared
        candidate-attempt budget as normal economics. V4.1 gives completion a
        bounded sub-budget (default 4 attempts / 2 successes) and reserves the
        remainder (default 8 attempts / at least 2 success capacity) for normal
        MM. Completion-cap skips do not consume normal or total attempt budget.
        """
        if self._research_in_transition_quarantine():
            return 0
        self._research_volume_cap_bind_book(book_id)
        cap = self._research_volume_cap_quote(state)
        if cap > 0.0 and self._research_volume_cap_remaining(state, book_id) <= 0.0:
            if self.debug_enabled:
                record = self._book_record(book_id)
                record["action"] = "SKIP"
                record["reason"] = "VOLUME_CAP"
            self._research_emit_volume_cap(
                state, book_id, allowed=False, reason="CAP_REACHED", force=True,
            )
            return 0
        completion_samples = self._completion_observation_count(book_id)
        completion_candidate = (
            inventory.band == "FLAT"
            and self._is_kappa_completion_candidate(book_id)
        )
        if str(getattr(inventory, "band", "")).upper() != "FLAT":
            lane = LANE_REALIZATION
        elif completion_candidate:
            lane = EXEC_LANE_COMPLETION
        elif (
            inventory.band == "FLAT"
            and completion_samples <= 0
            and self.research_kappa_completion_enabled
        ):
            lane = EXEC_LANE_COVERAGE
        else:
            lane = EXEC_LANE_COVERAGE if self._research_lanes_on() else LANE_NORMAL

        if getattr(self, "research_enable_score_ev", False):
            ev = (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id))
            if ev is not None and not bool(getattr(ev, "eligible", True)):
                if self.debug_enabled:
                    record = self._book_record(book_id)
                    record["action"] = "SKIP"
                    record["reason"] = str(getattr(ev, "reject_reason", None) or "SCORE_EV")
                    record["scheduler_lane"] = lane
                return 0

        ev = (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id))
        expected_mo = self._research_conservative_markout(int(book_id))
        as_risk = 0.0
        if ev is not None:
            expected_mo = float(getattr(ev, "expected_markout_bps", expected_mo) or expected_mo)
            as_risk = float(getattr(ev, "adverse_selection_risk", 0.0) or 0.0)
        else:
            override = self._research_horizon_expected_markout(int(book_id))
            if override is not None:
                expected_mo = float(override)
            as_risk = composite_adverse_selection_risk(
                expected_markout_bps=expected_mo,
                ofi_against=self._research_ofi_against(
                    int(book_id), float(getattr(inventory, "net_base", 0.0) or 0.0),
                ),
            )
        ofi_snap = self._research_ofi_snapshot(int(book_id))
        self._research_as_width_mult = quote_width_multiplier(
            adverse_selection_risk=as_risk,
            ofi_normalized=ofi_snap.ofi_normalized if ofi_snap.supported else None,
        )
        if (
            int(book_id) in set(getattr(self, "_research_cohort_ids", []) or [])
            and str(getattr(self, "_research_market_regime", "") or "").upper() == "QUIET"
            and float(as_risk) < 0.25
            and float(expected_mo) >= -4.0
        ):
            self._research_as_width_mult = max(
                float(getattr(self, "research_quote_width_floor_mult", 0.80)),
                float(self._research_as_width_mult)
                * float(getattr(self, "research_quote_tighten_mult", 0.85)),
            )
        adverse_block = (
            str(getattr(inventory, "band", "")).upper() == "FLAT"
            and entry_adverse_blocked(
                expected_markout_bps=expected_mo,
                adverse_selection_risk=as_risk,
            )
        )
        try:
            self._emit(
                "ADVERSE",
                force=True,
                tick=getattr(self, "_tick", None),
                book=int(book_id),
                expected_markout=expected_mo,
                adverse_selection_risk=as_risk,
                width_mult=float(self._research_as_width_mult),
                blocked=int(bool(adverse_block)),
                **ofi_snap.as_log(),
            )
        except Exception:
            pass
        if adverse_block:
            if self.debug_enabled:
                record = self._book_record(book_id)
                record["action"] = "SKIP"
                record["reason"] = "ADVERSE_SELECTION"
                record["scheduler_lane"] = lane
            self._research_as_width_mult = 1.0
            return 0

        if self._research_backfill_active:
            if self._research_lanes_on() and lane in EXEC_LANES:
                # Candidate screening is the single authoritative lane allocator.
                # Do not re-budget here: that previously discarded unused-lane
                # spill grants and burned lane capacity before a quote succeeded.
                allocation = getattr(self, "_research_last_lanes", None)
                if allocation is not None:
                    granted = set((allocation.by_lane or {}).get(lane, []) or [])
                    if int(book_id) not in granted:
                        hits = getattr(self, "_research_lane_cap_hits", None)
                        if isinstance(hits, dict):
                            hits[lane] = int(hits.get(lane, 0) or 0) + 1
                        if self.debug_enabled:
                            record = self._book_record(book_id)
                            record["action"] = "SKIP"
                            record["reason"] = "LANE_NOT_GRANTED"
                            record["scheduler_lane"] = lane
                        self._research_as_width_mult = 1.0
                        return 0
            else:
                admit, reject = admit_scheduler_candidate(
                    lane=lane if lane != EXEC_LANE_COMPLETION else LANE_COMPLETION,
                    quote_successes=int(self._research_quote_successes),
                    quote_success_cap=int(self._research_quote_success_cap),
                    completion_attempts=int(self._research_completion_quote_attempts),
                    completion_attempt_cap=int(self.research_kappa_completion_attempt_cap),
                    completion_successes=int(self._research_completion_quote_successes),
                    completion_success_cap=int(self.research_kappa_completion_success_cap),
                    normal_attempts=int(self._research_normal_quote_attempts),
                    normal_attempt_cap=int(self.research_normal_attempt_cap),
                )
                if not admit:
                    if reject == "KAPPA_COMPLETION_SUCCESS_CAP":
                        self._research_completion_success_cap_hits += 1
                    elif reject == "KAPPA_COMPLETION_ATTEMPT_CAP":
                        self._research_completion_attempt_cap_hits += 1
                    elif reject == "NORMAL_MM_ATTEMPT_CAP":
                        self._research_normal_attempt_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record["action"] = "SKIP"
                        record["reason"] = str(reject or "MM_SUCCESS_CAP")
                        record["scheduler_lane"] = lane
                    self._research_as_width_mult = 1.0
                    return 0
            if lane in {LANE_COMPLETION, EXEC_LANE_COMPLETION}:
                self._research_completion_quote_attempts += 1
            else:
                self._research_normal_quote_attempts += 1
            self._research_quote_attempts += 1

        allow_relaxed_fill = (
            completion_candidate
            and self._research_completion_relaxed_successes
                < self.research_kappa_completion_relaxed_success_cap
        )

        old_min_fill = float(regime_params.min_fill_prob)
        relaxed_min_fill = old_min_fill
        if allow_relaxed_fill:
            relaxed_min_fill = max(
                self.research_kappa_completion_fill_floor,
                old_min_fill * self.research_kappa_completion_fill_mult,
            )
            regime_params.min_fill_prob = min(old_min_fill, relaxed_min_fill)
            self._research_completion_relaxed_attempts += 1

        if self.debug_enabled:
            record = self._book_record(book_id)
            record["scheduler_lane"] = lane
            record["normal_attempts_used"] = self._research_normal_quote_attempts
            record["normal_attempt_cap"] = self.research_normal_attempt_cap
            record["completion_attempts_used"] = self._research_completion_quote_attempts
            record["completion_attempt_cap"] = self.research_kappa_completion_attempt_cap
            record["completion_successes_used"] = self._research_completion_quote_successes
            record["completion_success_cap"] = self.research_kappa_completion_success_cap
            record["kappa_completion_candidate"] = completion_candidate
            record["kappa_completion_samples"] = completion_samples
            record["kappa_completion_target"] = self.research_kappa_completion_target
            record["kappa_completion_fill_relaxed"] = allow_relaxed_fill
            record["kappa_completion_min_fill_original"] = old_min_fill
            record["kappa_completion_min_fill_effective"] = float(regime_params.min_fill_prob)

        old_expiry = int(self.mm_expiry_period)
        old_maker_context = bool(getattr(self, "_research_force_maker_context", False))
        old_completion_tight = bool(
            getattr(self, "_research_completion_quiet_tight_context", False)
        )
        completion_ev = None if ev is None else getattr(ev, "trading_ev", None)
        try:
            completion_ev = None if completion_ev is None else float(completion_ev)
        except (TypeError, ValueError):
            completion_ev = None
        self._research_completion_quiet_tight_context = bool(
            getattr(self, "research_enable_one_away_quiet_tightening", True)
            and completion_candidate
            and str(getattr(self, "_research_market_regime", "") or "").upper() == "QUIET"
            and completion_ev is not None
            and math.isfinite(completion_ev)
            and completion_ev >= float(getattr(self, "research_one_away_quiet_min_ev", 0.0))
        )
        hold_expiry = old_expiry
        hold_active = False
        incomplete_flat = (
            inventory.band == "FLAT"
            and completion_samples < self._research_required_observation_count()
        )
        if incomplete_flat:
            hold_expiry = self._partial_fill_hold_expiry(
                state, book_id, completion_samples
            )
            hold_active = hold_expiry > old_expiry
            if hold_active:
                self._research_partial_fill_hold_candidates += 1
                self.mm_expiry_period = hold_expiry
        if self.research_force_mm_post_only:
            self._research_force_maker_context = True

        hold_sides: set[str] = set()
        ttl_reason = "BASELINE"
        fill_hazard = None
        chosen_ttl_ms: float | None = None
        if getattr(self, "research_enable_adaptive_ttl", False):
            chosen, ttl_reason, fill_hazard = self._research_choose_ttl(
                book_id, profile, state, baseline_ns=old_expiry,
            )
            chosen_ttl_ms = chosen
            if chosen is None:
                self._research_ttl_stale_skips += 1
                if self.debug_enabled:
                    record = self._book_record(book_id)
                    record["action"] = "SKIP"
                    record["reason"] = "TTL_STALE"
                    record["ttl_reason"] = ttl_reason
                regime_params.min_fill_prob = old_min_fill
                self.mm_expiry_period = old_expiry
                self._research_force_maker_context = old_maker_context
                self._research_completion_quiet_tight_context = old_completion_tight
                self._research_as_width_mult = 1.0
                return 0
            self.mm_expiry_period = ms_to_ns(chosen)
            if hold_active and not (
                str(getattr(self, "_research_market_regime", "")).upper()
                in {"TOXIC", "STRESSED"}
            ) and ttl_reason not in {"TOXIC_SHORT", "ADVERSE_SHORT"}:
                self.mm_expiry_period = max(int(self.mm_expiry_period), int(hold_expiry))
            try:
                vol = None if profile is None else getattr(profile, "volatility", None)
                live = self._research_live_quote(book_id, "buy") or self._research_live_quote(
                    book_id, "sell"
                )
                quote_age = None if live is None else sim_delta_ms(
                    live.submit_ts, getattr(state, "timestamp", None)
                )
                self._emit(
                    "TTL",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    book=int(book_id),
                    chosen_ttl=chosen,
                    chosen_ttl_ms=sim_delta_ms(0, int(self.mm_expiry_period)),
                    quote_age=quote_age,
                    ttl_reason=ttl_reason,
                    fill_hazard=fill_hazard,
                    toxicity=int(
                        str(getattr(self, "_research_market_regime", "")).upper()
                        in {"TOXIC", "STRESSED"}
                    ),
                    volatility=vol,
                    market_regime=getattr(self, "_research_market_regime", None),
                )
            except Exception:
                pass

        if getattr(self, "research_enable_quote_hysteresis", False):
            hold_sides = self._research_hysteresis_hold_sides(
                state, book_id, book, profile, prediction, inventory,
                regime_params, edge_bias, chosen_ttl_ms=chosen_ttl_ms,
            )

        if self.debug_enabled:
            record = self._book_record(book_id)
            quality = self._actionable_fill_snapshot(book_id)
            record["actionable_fill_samples"] = quality["samples"]
            record["actionable_fill_p"] = quality["p_actionable"]
            record["dust_fill_p"] = quality["p_dust"]
            record["actionable_fill_source"] = quality["source"]
            record["partial_fill_hold"] = hold_active
            record["partial_fill_hold_expiry_ns"] = hold_expiry
            record["force_mm_post_only"] = bool(self.research_force_mm_post_only)
            record["chosen_ttl_ms"] = sim_delta_ms(0, int(self.mm_expiry_period))
            record["ttl_reason"] = ttl_reason
            record["fill_hazard"] = fill_hazard
            record["hysteresis_hold_buy"] = "buy" in hold_sides
            record["hysteresis_hold_sell"] = "sell" in hold_sides

        orig_limit = getattr(response, "limit_order", None)
        orig_record_fill = self._record_fill_quote
        orig_fill_est = self.estimate_fill_probability
        prev_cap_book = self._research_volume_cap_bind_book(book_id)
        policy = None
        if getattr(self, "research_enable_inventory_state_v2", True):
            policy = self._research_inventory_state(int(book_id), inventory)
            self._research_active_inventory_policy = policy
        suppression = None
        net = float(getattr(inventory, "net_base", 0.0) or 0.0)
        if (
            policy is not None
            and getattr(self, "research_enable_same_side_suppression", True)
        ):
            suppression = same_side_suppression(policy.state)
            self._research_same_side_last[int(book_id)] = suppression
            try:
                self._emit(
                    "SAME_SIDE",
                    force=True,
                    tick=getattr(self, "_tick", None),
                    book=int(book_id),
                    inventory=net,
                    **suppression.as_log(),
                )
            except Exception:
                pass

            def _gated_fill(*args, **kwargs):
                est = orig_fill_est(*args, **kwargs)
                buy, sell = apply_fill_priority(
                    buy_fill=getattr(est, "buy", 0.0),
                    sell_fill=getattr(est, "sell", 0.0),
                    inventory_sign=net,
                    suppression=suppression,
                )
                return FillProbabilityEstimate(buy=buy, sell=sell)

            self.estimate_fill_probability = _gated_fill
        dust_prevent = bool(getattr(self, "research_enable_dust_prevent", True))
        if orig_limit is not None and (hold_sides or policy is not None or dust_prevent):
            vol_dec = int(getattr(getattr(state, "config", None), "volumeDecimals", 8) or 8)
            min_size = max(
                0.0, float(getattr(self, "_research_exchange_min_order_size", 0.0) or 0.0)
            )
            eps = self._execution_flat_epsilon()
            haz_pack = (getattr(self, "_research_hazard_last", {}) or {}).get(int(book_id), {})
            dust_target = float(getattr(self, "research_dust_risk_target", 0.15) or 0.15)

            def _gated_limit_order(*args, **kwargs):
                direction = kwargs.get("direction")
                if direction is None and len(args) >= 2:
                    direction = args[1]
                token = str(getattr(direction, "name", direction)).upper()
                side = "buy" if token in {"0", "BUY", "BID", "ORDERDIRECTION.BUY"} else "sell"
                if side in hold_sides:
                    return None
                if suppression is not None and side_is_suppressed(
                    side=side, inventory_sign=net, suppression=suppression,
                ):
                    return None
                if policy is not None:
                    if not policy.allow_maker_entry and abs(net) <= 1e-12:
                        return None
                    mult = side_size_multiplier(
                        side=side, inventory_sign=net, policy=policy,
                    )
                    if mult <= 1e-12:
                        return None
                    if abs(mult - 1.0) > 1e-12:
                        qty = kwargs.get("quantity")
                        if qty is None and len(args) >= 3:
                            qty = args[2]
                        try:
                            scaled = self._round_order_size(float(qty) * mult, vol_dec)
                        except (TypeError, ValueError):
                            return None
                        if scaled <= 0.0:
                            return None
                        kwargs = dict(kwargs)
                        kwargs["quantity"] = scaled
                qty_for_exposure = kwargs.get("quantity")
                if qty_for_exposure is None and len(args) >= 3:
                    qty_for_exposure = args[2]
                try:
                    exposure_qty = float(qty_for_exposure)
                except (TypeError, ValueError):
                    return None
                if not self._research_effective_exposure_allows(
                    response, int(book_id), side, exposure_qty, net,
                ):
                    if self.debug_enabled:
                        self._book_record(book_id)["effective_exposure_block"] = side
                    return None
                if dust_prevent:
                    qty = kwargs.get("quantity")
                    if qty is None and len(args) >= 3:
                        qty = args[2]
                    try:
                        signed = float(qty) if side == "buy" else -float(qty)
                    except (TypeError, ValueError):
                        signed = 0.0
                    if quote_would_create_dust(
                        inventory_before=net,
                        signed_fill_qty=signed,
                        min_order_size=min_size,
                        eps=eps,
                    ):
                        self._research_dust_prevent_skips = int(
                            getattr(self, "_research_dust_prevent_skips", 0) or 0
                        ) + 1
                        return None
                    pred = haz_pack.get(side)
                    usable = bool(getattr(pred, "usable", False)) if pred is not None else False
                    dust_prob = 0.0
                    if pred is not None:
                        try:
                            dust_prob = float(getattr(pred, "dust", 0.0) or 0.0)
                        except (TypeError, ValueError):
                            dust_prob = 0.0
                    if predicted_dust_blocks_increase(
                        dust_prob=dust_prob,
                        dust_target=dust_target,
                        inventory_before=net,
                        signed_qty=signed,
                        usable=usable,
                        eps=eps,
                    ):
                        self._research_dust_prevent_skips = int(
                            getattr(self, "_research_dust_prevent_skips", 0) or 0
                        ) + 1
                        return None
                return orig_limit(*args, **kwargs)

            def _gated_record_fill(mem, side, dist_from_touch):
                token = str(side).lower()
                if token in hold_sides:
                    return None
                if suppression is not None and side_is_suppressed(
                    side=token, inventory_sign=net, suppression=suppression,
                ):
                    return None
                return orig_record_fill(mem, side, dist_from_touch)

            self._research_bind_response_method(
                response, "limit_order", _gated_limit_order,
            )
            if hold_sides or suppression is not None:
                self._record_fill_quote = _gated_record_fill

        try:
            placed = super()._place_skewed_quotes(
                response,
                state,
                book_id,
                book,
                profile,
                prediction,
                inventory,
                regime_params,
                size,
                edge_bias,
                stats=stats,
            )
        finally:
            self._research_unbind_response_method(response, "limit_order")
            self._record_fill_quote = orig_record_fill
            self._research_active_inventory_policy = None
            self.estimate_fill_probability = orig_fill_est
            regime_params.min_fill_prob = old_min_fill
            self.mm_expiry_period = old_expiry
            self._research_force_maker_context = old_maker_context
            self._research_completion_quiet_tight_context = old_completion_tight
            self._research_as_width_mult = 1.0
            self._research_volume_cap_book_id = prev_cap_book

        if hold_sides and not placed:
            placed = len(hold_sides)

        if self._research_backfill_active and placed:
            self._research_quote_successes += 1
            if self._research_lanes_on() and lane in EXEC_LANES:
                used = getattr(self, "_research_lane_used", None)
                if not isinstance(used, dict):
                    used = {token: 0 for token in EXEC_LANES}
                    self._research_lane_used = used
                used[lane] = int(used.get(lane, 0) or 0) + 1
                budgets = getattr(self, "_research_lane_budgets", None) or self._research_execution_lane_budgets()
                # This metric is execution spill beyond each lane's own reserve;
                # the screen allocation still owns the actual capacity decision.
                self._research_lane_overflow_used = sum(
                    max(0, int(used.get(token, 0) or 0) - budgets.reserved(token))
                    for token in EXEC_LANES
                )
            if completion_candidate:
                self._research_completion_quote_successes += 1
            else:
                self._research_normal_quote_successes += 1

        if completion_candidate and placed:
            if allow_relaxed_fill:
                self._research_completion_relaxed_successes += 1
            if hold_active:
                self._research_partial_fill_hold_quoted += 1
            if self.debug_enabled:
                self._book_record(book_id)["kappa_completion_quote_success"] = True
        if placed and self.research_force_mm_post_only:
            self._research_forced_maker_quote_books += 1

        return placed

    def _place_directional_round_trip(self, *args, **kwargs) -> int:
        if self._research_in_transition_quarantine():
            return 0
        book_id = kwargs.get("book_id")
        if book_id is None and len(args) >= 3:
            book_id = args[2]
        state = kwargs.get("state")
        if state is None and len(args) >= 2:
            state = args[1]
        prev = getattr(self, "_research_volume_cap_book_id", None)
        if book_id is not None:
            self._research_volume_cap_bind_book(book_id)
        try:
            if book_id is not None and state is not None:
                cap = self._research_volume_cap_quote(state)
                if cap > 0.0 and self._research_volume_cap_remaining(state, book_id) <= 0.0:
                    self._research_emit_volume_cap(
                        state, book_id, allowed=False, reason="CAP_REACHED", force=True,
                    )
                    return 0
            return super()._place_directional_round_trip(*args, **kwargs)
        finally:
            self._research_volume_cap_book_id = prev

    def build_mm_strategy_instructions(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        selection: BookSelection,
        predictions: dict[int, DirectionForecast],
        regime: MarketRegime,
        collect_archetypes: bool = True,
    ) -> dict:
        started = time.perf_counter()
        self._research_last_selection = selection
        self._research_last_predictions = predictions
        self._sync_exchange_constraints(state)

        overlay = str(getattr(regime, "scoring_overlay", "")).upper()
        score_regime = str(
            getattr(regime, "research_score_regime", None)
            or getattr(self, "_research_score_regime", "")
            or ""
        ).upper()
        allocation = getattr(self, "_research_last_lanes", None)
        acquisition_mode = score_acquisition_mode(
            score_regime=score_regime, scoring_overlay=overlay,
        )
        acquisition_grants = score_acquisition_grants(allocation)
        # V4.9 completion bridge: independent COMPLETION/COVERAGE score states
        # can open the inherited inactive gate, but only when the fast scheduler
        # actually granted a Coverage/Completion lane. Legacy SCORING_PRESSURE
        # remains the only no-allocation fallback.
        bootstrap = bool(
            self.research_inactive_bootstrap
            and acquisition_mode
            and (
                bool(acquisition_grants)
                or (allocation is None and overlay == "SCORING_PRESSURE")
            )
        )
        self._research_bootstrap_active = bootstrap
        self._research_score_acquisition_mode = bool(acquisition_mode)
        self._research_score_acquisition_grants = set(acquisition_grants)

        old_skip_inactive = self.mm_skip_inactive_tier
        old_maintenance_mult = self.maintenance_size_mult
        old_max_mm_books = self.max_mm_books_per_tick
        old_maintenance_books = list(getattr(selection, "maintenance_books", []) or [])

        self._research_backfill_active = bool(self.research_candidate_backfill)
        self._research_quote_success_cap = int(old_max_mm_books)
        self._research_quote_successes = 0
        self._research_quote_attempts = 0
        self._research_normal_quote_attempts = 0
        self._research_normal_quote_successes = 0
        self._research_completion_relaxed_successes = 0
        self._research_completion_relaxed_attempts = 0
        self._research_completion_quote_attempts = 0
        self._research_completion_quote_successes = 0
        self._research_completion_attempt_cap_hits = 0
        self._research_completion_success_cap_hits = 0
        self._research_normal_attempt_cap_hits = 0
        self._research_reset_lane_usage()
        self._research_dust_compact_ids_this_tick = self._select_dust_compaction_books(state)
        self._research_score_ev_last = {}
        self._research_bind_volume_state(state)

        # V4.12.7 defense-in-depth: even if inherited selection proposes
        # maintenance on a stable qualified book, do not let it consume a fresh
        # inventory slot while productive incomplete books are waiting.
        suppressed_qualified = set(getattr(self, "_research_qualified_suppressed_ids", set()) or set())
        if suppressed_qualified:
            selection.maintenance_books = [
                bid for bid in (getattr(selection, "maintenance_books", []) or [])
                if int(bid) not in suppressed_qualified
            ]

        if self._research_backfill_active:
            # Parent Strategy1 slices mm_candidates before calling our quote
            # hook. Scan the full current profile set so candidates rejected by
            # the completion lane cannot prevent normal candidates later in the
            # ranking from being evaluated. Actual expensive quote attempts remain
            # bounded by the lane allocator and hard per-book/order safety gates.
            profile_scan = len(getattr(selection, "profiles", []) or [])
            self.max_mm_books_per_tick = max(
                old_max_mm_books,
                self.research_candidate_attempt_cap,
                profile_scan,
            )

        try:
            # V4.9: bridge the independent ScoreRegime to the inherited inactive
            # gate. The global flag is opened only for this bounded build call;
            # candidate prediction + lane admission still constrain which books
            # can reach quoting. Maintenance is additionally restricted to the
            # current acquisition grants so cold maintenance cannot crowd out
            # one-away/two-away completion.
            if bootstrap:
                self.mm_skip_inactive_tier = False
                if allocation is not None:
                    selection.maintenance_books = [
                        bid for bid in old_maintenance_books
                        if int(bid) in acquisition_grants
                    ]

            # Coverage/completion orders must be executable. Promote only the
            # maintenance clip, and only when a single minimum order remains
            # inside the configured absolute base inventory cap.
            if (
                bootstrap
                and self.research_bootstrap_maintenance_min_order
                and self._research_exchange_min_order_size > 0.0
            ):
                maintenance_base = float(
                    getattr(self, "maintenance_order_size", 0.0) or 0.0
                )
                min_size = self._research_exchange_min_order_size
                if (
                    maintenance_base > 0.0
                    and min_size <= float(self.max_inventory_base) + 1e-12
                ):
                    required_mult = min_size / maintenance_base
                    if required_mult > self.maintenance_size_mult:
                        self.maintenance_size_mult = required_mult

            stats = super().build_mm_strategy_instructions(
                response,
                state,
                selection,
                predictions,
                regime,
                collect_archetypes=collect_archetypes,
            )
            if isinstance(stats, dict):
                stats["research_bootstrap_active"] = bootstrap
                stats["research_stress_spread_bps"] = self._research_stress_spread_bps
                stats["research_toxic_spread_bps"] = self._research_toxic_spread_bps
                stats["research_min_order_size"] = self._research_exchange_min_order_size
                stats["research_round_trip_closes"] = self._research_round_trip_closes
                stats["research_position_opens"] = self._research_position_opens
                stats["research_dust_blocks"] = self._research_dust_blocks
                stats["research_parked_dust"] = len(self._research_parked_dust)
                stats["research_dust_entries"] = self._research_dust_entries
                stats["research_dust_releases"] = self._research_dust_releases
                stats["research_dust_heartbeats"] = self._research_dust_heartbeats
                stats["research_dust_compact_selected"] = len(self._research_dust_compact_ids_this_tick)
                stats["research_dust_compact_attempts"] = self._research_dust_compact_attempts
                stats["research_dust_compact_orders"] = self._research_dust_compact_orders
                stats["research_dust_compact_fills"] = self._research_dust_compact_fills
                stats["research_quote_attempts"] = self._research_quote_attempts
                stats["research_normal_quote_attempts"] = self._research_normal_quote_attempts
                stats["research_normal_quote_successes"] = self._research_normal_quote_successes
                stats["research_normal_attempt_cap"] = self.research_normal_attempt_cap
                stats["research_completion_quote_attempts"] = self._research_completion_quote_attempts
                stats["research_completion_quote_successes"] = self._research_completion_quote_successes
                stats["research_completion_attempt_cap"] = self.research_kappa_completion_attempt_cap
                stats["research_completion_success_cap"] = self.research_kappa_completion_success_cap
                stats["research_completion_relaxed_attempts"] = self._research_completion_relaxed_attempts
                stats["research_completion_relaxed_successes"] = self._research_completion_relaxed_successes
                stats["research_completion_attempt_cap_hits"] = self._research_completion_attempt_cap_hits
                stats["research_completion_success_cap_hits"] = self._research_completion_success_cap_hits
                stats["research_normal_attempt_cap_hits"] = self._research_normal_attempt_cap_hits
                stats["research_quote_successes"] = self._research_quote_successes
                stats["research_quote_success_cap"] = self._research_quote_success_cap
                stats["research_actionable_maker_fills"] = self._research_actionable_maker_fills
                stats["research_actionable_fills"] = self._research_actionable_fills
                stats["research_dust_maker_fills"] = self._research_dust_maker_fills
                stats["research_partial_fill_hold_candidates"] = self._research_partial_fill_hold_candidates
                stats["research_partial_fill_hold_quoted"] = self._research_partial_fill_hold_quoted
                stats["research_forced_maker_quote_books"] = self._research_forced_maker_quote_books
                stats["research_hysteresis_holds"] = getattr(self, "_research_hysteresis_holds", 0)
                stats["research_hysteresis_replaces"] = getattr(self, "_research_hysteresis_replaces", 0)
                stats["research_ttl_stale_skips"] = getattr(self, "_research_ttl_stale_skips", 0)
                stats["research_dust_escape_attempts"] = getattr(self, "_research_dust_escape_attempts", 0)
                stats["research_dust_escape_orders"] = getattr(self, "_research_dust_escape_orders", 0)
                stats["research_dust_prevent_skips"] = getattr(self, "_research_dust_prevent_skips", 0)
                stats["research_dust_compact_cooldown_skips"] = self._research_dust_compact_cooldown_skips
                stats["research_flat_epsilon"] = self._execution_flat_epsilon()
                try:
                    self._research_emit_scheduler(stats, selection)
                except Exception:
                    pass
                try:
                    if self._research_lanes_on():
                        self._research_emit_lanes(stage="EXEC")
                except Exception:
                    pass
                try:
                    self._research_emit_volume_cap_summary(state)
                    snap = agent_volume_cap_snapshot(self, state)
                    stats["research_books_cap_reached"] = snap.get("books_cap_reached")
                    stats["research_books_headroom_lt_10pct"] = snap.get("books_headroom_lt_10pct")
                    stats["research_books_headroom_lt_25pct"] = snap.get("books_headroom_lt_25pct")
                    stats["research_median_headroom"] = snap.get("median_headroom")
                    stats["research_min_headroom"] = snap.get("min_headroom")
                    stats["research_volume_cap_blocks"] = getattr(
                        self, "_research_volume_cap_blocks", 0
                    )
                except Exception:
                    pass
            return stats
        finally:
            self.mm_skip_inactive_tier = old_skip_inactive
            self.maintenance_size_mult = old_maintenance_mult
            self.max_mm_books_per_tick = old_max_mm_books
            selection.maintenance_books = old_maintenance_books
            self._research_bootstrap_active = False
            self._research_backfill_active = False
            self._research_timing["build_orders_ms"] = (
                time.perf_counter() - started
            ) * 1000.0

    # Strategy1_Debug emits an explicit DECISION payload rather than forwarding
    # the full internal record. Override it so the new research diagnostics are
    # actually persisted to JSONL and visible in [S1R_SKIP]/[S1R_QUOTE].
    def _emit_book_decision(
        self,
        state,
        regime,
        book_id: int,
        book,
        profile,
        prediction,
        record: dict[str, Any],
    ) -> None:
        bid = book.bids[0].price if getattr(book, "bids", None) else None
        ask = book.asks[0].price if getattr(book, "asks", None) else None
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        touch_spread_bps = None
        if mid and bid is not None and ask is not None:
            touch_spread_bps = ((ask - bid) / mid) * 10_000.0

        profile_spread_bps = (
            self._profile_float(profile, "spread_bps")
            if profile is not None else None
        )
        mem = self._mem(book_id) if profile is not None else None

        reason = str(record.get("reason", DebugReason.NO_ACTION))
        if (
            record.get("dust_quarantine")
            and reason in ("TOXIC_BOOK", "TOXIC_REGIME")
        ):
            reason = "DUST_QUARANTINE"
        overlay = str(getattr(regime, "scoring_overlay", "")).upper()
        score_regime = str(
            getattr(regime, "research_score_regime", None)
            or getattr(self, "_research_score_regime", "")
            or ""
        ).upper()
        inactive_gate_bypassed = bool(
            self.research_inactive_bootstrap
            and score_acquisition_granted(
                book_id,
                allocation=getattr(self, "_research_last_lanes", None),
                score_regime=score_regime,
                scoring_overlay=overlay,
            )
        )
        # If a future Debug implementation diagnoses after policy restoration,
        # never present INACTIVE_TIER as a proven live gate when V2 bypasses it.
        if reason == "INACTIVE_TIER" and inactive_gate_bypassed and not self.mm_skip_inactive_tier:
            reason = "INACTIVE_DIAGNOSTIC_ONLY"
        self._debug_reason_counts[reason] += 1
        self._emit(
            "DECISION",
            tick=self._tick,
            timestamp=getattr(state, "timestamp", None),
            book_id=book_id,
            action=record.get("action", "SKIP"),
            reason=reason,
            regime=getattr(regime, "mode", None),
            overlay=getattr(regime, "scoring_overlay", None),
            score_regime=score_regime,
            score_acquisition_granted=int(inactive_gate_bypassed),
            archetype=record.get("archetype"),
            archetype_source=record.get("archetype_source"),
            tier=getattr(profile, "tier", None) if profile is not None else None,
            mid=mid,
            spread_bps=profile_spread_bps if profile_spread_bps is not None else touch_spread_bps,
            touch_spread_bps=touch_spread_bps,
            volatility=getattr(profile, "volatility", None) if profile is not None else None,
            trade_rate=getattr(profile, "trade_rate", None) if profile is not None else None,
            imbalance=getattr(profile, "imbalance", None) if profile is not None else None,
            **self._research_ofi_fields(book_id),
            expected_markout=None if (
                (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id)) is None
            ) else getattr(
                self._research_score_ev_last[int(book_id)], "expected_markout_bps", None
            ),
            adverse_selection_risk=None if (
                (getattr(self, "_research_score_ev_last", {}) or {}).get(int(book_id)) is None
            ) else getattr(
                self._research_score_ev_last[int(book_id)], "adverse_selection_risk", None
            ),
            direction=getattr(prediction, "direction", None) if prediction else None,
            signal=getattr(prediction, "score", None) if prediction else None,
            expected_alpha=record.get("expected_alpha"),
            min_expected_alpha=self.min_expected_alpha,
            fill_buy=record.get("fill_buy"),
            fill_sell=record.get("fill_sell"),
            bid_px=record.get("bid_px"),
            ask_px=record.get("ask_px"),
            quantity=record.get("quantity"),
            expected_realized_pnl=record.get("expected_realized_pnl"),
            inventory=record.get("inventory"),
            instructions=record.get("instructions", 0),
            decision_ms=record.get("quote_ms", record.get("manage_ms")),
            loss_streak=record.get(
                "loss_streak",
                getattr(mem, "loss_streak", None) if mem is not None else None,
            ),
            recent_pnl=record.get(
                "recent_pnl",
                getattr(mem, "recent_pnl", None) if mem is not None else None,
            ),
            toxic_loss=record.get("toxic_loss"),
            toxic_pnl=record.get("toxic_pnl"),
            toxic_spread=record.get("toxic_spread"),
            toxic_archetype=record.get("toxic_archetype"),
            toxic_red_tier=record.get("toxic_red_tier"),
            stressed_by_spread=record.get("stressed_by_spread"),
            stressed_by_regime=record.get("stressed_by_regime"),
            legacy_stressed_by_regime=record.get("legacy_stressed_by_regime"),
            stress_spread_bps=record.get(
                "stress_spread_bps", self._research_stress_spread_bps
            ),
            toxic_spread_bps=record.get(
                "toxic_spread_bps", self._research_toxic_spread_bps
            ),
            min_order_size=record.get(
                "min_order_size", self._research_exchange_min_order_size
            ),
            dynamic_size_raw=record.get("dynamic_size_raw"),
            dynamic_size_final=record.get("dynamic_size_final"),
            size_promoted_to_min=record.get("size_promoted_to_min"),
            inactive_bootstrap=inactive_gate_bypassed,
            inactive_gate_bypassed=inactive_gate_bypassed and not self.mm_skip_inactive_tier,
            dead_trade_rate_hit=record.get("dead_trade_rate_hit"),
            active_sparse=record.get("active_sparse"),
            active_sparse_tier=record.get("active_sparse_tier"),
            dust_quarantine=record.get("dust_quarantine"),
            dust_compact=record.get("dust_compact"),
            dust_compact_selected=record.get("dust_compact_selected"),
            scheduler_lane=record.get("scheduler_lane"),
            normal_attempts_used=record.get("normal_attempts_used"),
            normal_attempt_cap=record.get("normal_attempt_cap"),
            completion_attempts_used=record.get("completion_attempts_used"),
            completion_attempt_cap=record.get("completion_attempt_cap"),
            completion_successes_used=record.get("completion_successes_used"),
            completion_success_cap=record.get("completion_success_cap"),
            kappa_completion_candidate=record.get("kappa_completion_candidate"),
            kappa_completion_samples=record.get("kappa_completion_samples"),
            kappa_completion_target=record.get("kappa_completion_target"),
            kappa_completion_fill_relaxed=record.get("kappa_completion_fill_relaxed"),
            kappa_completion_min_fill_original=record.get("kappa_completion_min_fill_original"),
            kappa_completion_min_fill_effective=record.get("kappa_completion_min_fill_effective"),
            kappa_completion_quote_success=record.get("kappa_completion_quote_success"),
            actionable_fill_samples=record.get("actionable_fill_samples"),
            actionable_fill_p=record.get("actionable_fill_p"),
            dust_fill_p=record.get("dust_fill_p"),
            actionable_fill_source=record.get("actionable_fill_source"),
            partial_fill_hold=record.get("partial_fill_hold"),
            partial_fill_hold_expiry_ns=record.get("partial_fill_hold_expiry_ns"),
            force_mm_post_only=record.get("force_mm_post_only"),
            toxic_pnl_raw=record.get("toxic_pnl_raw"),
            toxic_pnl_samples=record.get("toxic_pnl_samples"),
            aggressive_touch_gross_bps=record.get("aggressive_touch_gross_bps"),
            aggressive_touch_net_bps=record.get("aggressive_touch_net_bps"),
            bootstrap_inactive=record.get("bootstrap_inactive"),
            inventory_util=record.get("inventory_util"),
            dust_position=record.get("dust_position"),
        )

    def _research_add_logging_ms(self, started: float) -> None:
        try:
            timing = getattr(self, "_research_timing", None)
            if not isinstance(timing, dict):
                return
            timing["logging_ms"] = float(timing.get("logging_ms", 0.0) or 0.0) + (
                time.perf_counter() - started
            ) * 1000.0
        except Exception:
            pass

    # Intercept every Strategy1_Debug event. No synchronous bt.logging call here.
    def _emit(self, event_type: str, force: bool = False, **payload: Any) -> None:
        started = time.perf_counter()
        try:
            self._research_emit_body(event_type, force=force, **payload)
        finally:
            self._research_add_logging_ms(started)

    def _research_emit_body(self, event_type: str, force: bool = False, **payload: Any) -> None:
        if not getattr(self, "debug_enabled", True) and not force:
            return
        if event_type == "RUN_SUMMARY":
            try:
                payload.update(self._research_hybrid_summary_payload())
            except Exception:
                pass
            payload.setdefault("research_round_trip_closes", getattr(self, "_research_round_trip_closes", 0))
            payload.setdefault("research_position_opens", getattr(self, "_research_position_opens", 0))
            payload.setdefault("research_position_reductions", getattr(self, "_research_position_reductions", 0))
            payload.setdefault("research_dust_blocks", getattr(self, "_research_dust_blocks", 0))
            payload.setdefault("research_parked_dust_positions", len(getattr(self, "_research_parked_dust", {})))
            payload.setdefault("research_dust_entries", getattr(self, "_research_dust_entries", 0))
            payload.setdefault("research_dust_releases", getattr(self, "_research_dust_releases", 0))
            payload.setdefault("research_dust_heartbeats", getattr(self, "_research_dust_heartbeats", 0))
            payload.setdefault("research_dust_compact_attempts", getattr(self, "_research_dust_compact_attempts", 0))
            payload.setdefault("research_dust_compact_orders", getattr(self, "_research_dust_compact_orders", 0))
            payload.setdefault("research_dust_compact_fills", getattr(self, "_research_dust_compact_fills", 0))
            payload.setdefault("research_dust_compact_cooldown_skips", getattr(self, "_research_dust_compact_cooldown_skips", 0))
            payload.setdefault("research_actionable_quote_count", getattr(self, "_research_actionable_quote_count", 0))
            payload.setdefault("research_actionable_maker_fills", getattr(self, "_research_actionable_maker_fills", 0))
            payload.setdefault("research_actionable_fills", getattr(self, "_research_actionable_fills", 0))
            payload.setdefault("research_dust_maker_fills", getattr(self, "_research_dust_maker_fills", 0))
            maker_fills = max(1, int(getattr(self, "_research_actionable_maker_fills", 0)))
            payload.setdefault("research_actionable_fill_ratio", getattr(self, "_research_actionable_fills", 0) / maker_fills)
            payload.setdefault("research_dust_maker_fill_ratio", getattr(self, "_research_dust_maker_fills", 0) / maker_fills)
            payload.setdefault("research_partial_fill_hold_candidates", getattr(self, "_research_partial_fill_hold_candidates", 0))
            payload.setdefault("research_partial_fill_hold_quoted", getattr(self, "_research_partial_fill_hold_quoted", 0))
            payload.setdefault("research_forced_maker_quote_books", getattr(self, "_research_forced_maker_quote_books", 0))
            payload.setdefault("research_normal_quote_attempts", getattr(self, "_research_normal_quote_attempts", 0))
            payload.setdefault("research_normal_quote_successes", getattr(self, "_research_normal_quote_successes", 0))
            payload.setdefault("research_normal_attempt_cap", getattr(self, "research_normal_attempt_cap", 0))
            payload.setdefault("research_completion_quote_attempts", getattr(self, "_research_completion_quote_attempts", 0))
            payload.setdefault("research_completion_quote_successes", getattr(self, "_research_completion_quote_successes", 0))
            payload.setdefault("research_completion_attempt_cap", getattr(self, "research_kappa_completion_attempt_cap", 0))
            payload.setdefault("research_completion_success_cap", getattr(self, "research_kappa_completion_success_cap", 0))
            payload.setdefault("research_completion_relaxed_attempts", getattr(self, "_research_completion_relaxed_attempts", 0))
            payload.setdefault("research_completion_relaxed_successes", getattr(self, "_research_completion_relaxed_successes", 0))
            payload.setdefault("research_completion_attempt_cap_hits", getattr(self, "_research_completion_attempt_cap_hits", 0))
            payload.setdefault("research_completion_success_cap_hits", getattr(self, "_research_completion_success_cap_hits", 0))
            payload.setdefault("research_normal_attempt_cap_hits", getattr(self, "_research_normal_attempt_cap_hits", 0))
            universe = self._research_kappa_universe()
            kappa_summary = summary_kappa(universe)
            payload.setdefault("research_kappa_state_version", self.RESEARCH_KAPPA_STATE_VERSION)
            payload.setdefault("research_realized_observation_total", kappa_summary["realized_total"])
            payload.setdefault(
                "research_kappa_books_with_obs",
                sum(1 for row in universe.books if not row.uncovered),
            )
            payload.setdefault("research_kappa_books_pending_1", kappa_summary["pending_1"])
            payload.setdefault("research_kappa_books_pending_2", kappa_summary["pending_2"])
            payload.setdefault("research_kappa_books_eligible", kappa_summary["eligible"])
            dust_registry = getattr(self, "_research_parked_dust", {})
            payload.setdefault(
                "research_kappa_incomplete_dust",
                sum(1 for bid in dust_registry if not universe.book(int(bid)).eligible),
            )
            payload.setdefault(
                "research_kappa_zero_obs_dust",
                sum(1 for bid in dust_registry if universe.book(int(bid)).uncovered),
            )
            payload.setdefault(
                "research_kappa_one_away_dust",
                sum(1 for bid in dust_registry if universe.book(int(bid)).one_away),
            )
            payload.setdefault(
                "research_parked_dust_abs_base",
                sum(
                    abs(float(info.get("net_base", 0.0)))
                    for info in getattr(self, "_research_parked_dust", {}).values()
                ),
            )
            try:
                current_tick = int(getattr(self, "_tick", 0) or 0)
                dust_registry = getattr(self, "_research_parked_dust", {})
                payload.setdefault(
                    "research_oldest_dust_ticks",
                    max(
                        (
                            max(0, current_tick - int(info.get("first_tick", current_tick)))
                            for info in dust_registry.values()
                        ),
                        default=0,
                    ),
                )
                payload.setdefault(
                    "research_open_positions",
                    sum(
                        1 for bid in getattr(self, "_open_positions", {})
                        if abs(float(self._position_tracker_snapshot(bid).net_qty))
                        >= self._execution_flat_epsilon()
                    ),
                )
                payload.setdefault(
                    "research_actionable_open_positions",
                    sum(
                        1 for bid in getattr(self, "_open_positions", {})
                        if bid not in dust_registry
                        and abs(float(self._position_tracker_snapshot(bid).net_qty))
                        >= self._execution_flat_epsilon()
                    ),
                )
            except Exception:
                pass
        if event_type == "DECISION" and str(payload.get("action", "")).upper() == "SKIP":
            try:
                raw_reason = str(payload.get("reason", "NO_ACTION") or "NO_ACTION")
                reason = str(self.REASON_ALIAS.get(raw_reason, raw_reason))
                counts = getattr(self, "_research_skip_summary_counts", None)
                if not isinstance(counts, dict):
                    counts = {}
                    self._research_skip_summary_counts = counts
                counts[reason] = int(counts.get(reason, 0) or 0) + 1
            except Exception:
                pass
        try:
            safe = self._json_safe(payload)
        except Exception:
            safe = payload
        record = {
            "type": event_type,
            "agent_id": getattr(self, "uid", None),
            "wall_time_ns": time.time_ns(),
            **safe,
        }
        if not getattr(self, "_research_ready", False):
            self._research_early.append(record)
            return
        if getattr(self, "research_enabled", False):
            self._enqueue(record)

    def _enqueue(self, record: dict[str, Any]) -> None:
        if self._rq is None:
            return
        try:
            self._rq.put_nowait(record)
        except queue.Full:
            self._rdropped += 1

    def _writer_loop(self) -> None:
        assert self._rq is not None and self._rstop is not None
        while not self._rstop.is_set() or not self._rq.empty():
            try:
                record = self._rq.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if self._rfile is not None:
                    self._rfile.write(json.dumps(record, separators=(",", ":"), sort_keys=True, default=str) + "\n")
                if self.research_console and self._console_allowed(record):
                    line = self._format_human(record)
                    if line:
                        print(line, flush=True)
            except Exception as exc:
                try:
                    print(f"[S1R_ERROR] stage=telemetry error={self._short(exc)}", flush=True)
                except Exception:
                    pass
            finally:
                self._rq.task_done()

    def _console_allowed(self, r: dict[str, Any]) -> bool:
        typ = str(r.get("type", ""))
        if typ == "STATE_RESET":
            return False
        tick = self._int(r.get("tick"))
        sampled = tick is None or tick == 1 or tick % max(1, int(self.research_every_n)) == 0
        book = self._int(r.get("book_id", r.get("book")))
        if self.research_book_id >= 0 and book is not None and book != self.research_book_id:
            return False

        if bool(getattr(self, "research_compact_console", True)):
            # Events that represent actual state/economic outcomes are always visible.
            if typ in {
                "ERROR", "RESEARCH_CONFIG", "DEBUG_CONFIG", "SESSION_TRANSITION",
                "FILL", "POSITION", "POSITION_GUARD", "ACTIONABLE_FILL", "MARKOUT",
                "RUN_SUMMARY", "STALE_RESCUE",
            }:
                return True
            if typ == "ORDER_LIFECYCLE":
                phase = str(r.get("phase", "")).upper()
                return any(token in phase for token in ("TRADE", "FILL", "REJECT", "FAIL")) or sampled
            if typ == "TAKER_DECISION":
                authorized = any(
                    int(r.get(key, 0) or 0)
                    for key in (
                        "economic_taker_authorized", "score_taker_authorized",
                        "risk_taker_authorized", "aggressive_positive_ev_taker_authorized",
                        "direct_taker_authorized",
                    )
                )
                return bool(authorized) or sampled
            if typ == "ENTRY_SIZE":
                trigger = str(r.get("trigger", "")).upper()
                allow = int(r.get("allow", 0) or 0)
                return trigger in {"ONE_AWAY_EXACT_MIN", "TWO_AWAY_EXACT_MIN", "OPEN_BOOK_CAP"} or (allow and sampled)
            if typ == "DECISION" and str(r.get("action", "")).upper() == "SKIP":
                # V4.12.2: JSONL keeps every per-book skip, but compact console
                # prints one aggregated SKIP_SUMMARY instead of 100+ lines.
                return False
            if typ == "SKIP_SUMMARY":
                return sampled
            # Decision internals and per-book model diagnostics are sampled; the
            # JSONL writer still receives every event.
            if typ in {
                "REGIME", "SCORE_REGIME", "SCORE_PROGRESS", "COHORT",
                "REALIZATION", "EXIT_URGENCY", "LADDER", "EXIT_QTY",
                "INVENTORY_STATE", "QUOTE", "EXEC_PROB", "FILL_CAL", "RANK",
                "KAPPA", "SCHED", "LANES", "CANCEL_DECISION", "TTL",
                "RESPOND_TIMING", "VOLUME_CAP", "HYBRID", "HYBRID_SUMMARY",
                "ADVERSE", "ORDER",
            }:
                return sampled
            return sampled

        if typ in {"ERROR", "RUN_SUMMARY", "RESEARCH_CONFIG", "DEBUG_CONFIG", "POSITION", "POSITION_GUARD", "ACTIONABLE_FILL", "REGIME", "SCORE_REGIME", "STALE_RESCUE", "REALIZATION", "EXIT_URGENCY", "LADDER", "TAKER_DECISION", "EXIT_QTY", "INVENTORY_STATE", "ENTRY_SIZE", "QUOTE", "EXEC_PROB", "FILL", "MARKOUT", "FILL_CAL", "RANK", "KAPPA", "SCHED", "LANES", "CANCEL_DECISION", "TTL", "RESPOND_TIMING", "VOLUME_CAP", "HYBRID", "HYBRID_SUMMARY", "SESSION_TRANSITION"}:
            return True
        if typ == "ORDER_LIFECYCLE":
            phase = str(r.get("phase", "")).upper()
            if any(token in phase for token in ("TRADE", "FILL", "REJECT", "FAIL")):
                return True
        return sampled

    def _format_human(self, r: dict[str, Any]) -> str | None:
        typ = str(r.get("type", ""))
        if typ == "RESEARCH_CONFIG":
            return (f"[S1R_CONFIG] enabled={int(bool(r.get('enabled')))} every_n={r.get('every_n')} "
                    f"book={r.get('book_filter')} jsonl={int(bool(r.get('jsonl')))} "
                    f"queue={r.get('queue_size')} policy={self._short(r.get('policy_version'))} "
                    f"breadth_gate={int(bool(r.get('suppress_qualified_acquisition')))} "
                    f"breadth_min={r.get('qualified_suppression_min_incomplete')} "
                    f"deadline_sched={int(bool(r.get('deadline_scheduler')))} "
                    f"deadline_critical={self._fmt(r.get('deadline_critical_urgency'))} "
                    f"score_target={r.get('score_target_books')} "
                    f"stale_rescue={int(bool(r.get('stale_maker_rescue')))} "
                    f"stale_rescue_floor={self._fmt(r.get('stale_maker_rescue_floor_bps'))} "
                    f"fix_global_stress={int(bool(r.get('fix_global_stress')))} "
                    f"neutral_fallback={int(bool(r.get('neutral_fallback')))} "
                    f"adaptive_spread={int(bool(r.get('adaptive_spread_thresholds')))} "
                    f"inactive_bootstrap={int(bool(r.get('inactive_bootstrap')))} "
                    f"bootstrap_dead_as_mm={int(bool(r.get('bootstrap_dead_as_mm')))} "
                    f"fix_inv={int(bool(r.get('fix_inventory_util')))} "
                    f"fix_reservation={int(bool(r.get('fix_quote_reservation')))} "
                    f"manage_min_clip={int(bool(r.get('bootstrap_manage_min_clip')))} "
                    f"close_age_gate={r.get('bootstrap_force_close_ticks')} "
                    f"touch_gate={int(bool(r.get('aggressive_close_touch_gate')))} "
                    f"touch_buffer_bps={self._fmt(r.get('aggressive_close_fee_buffer_bps'))} "
                    f"touch_min_net_bps={self._fmt(r.get('aggressive_close_min_net_bps'))} "
                    f"backfill={int(bool(r.get('candidate_backfill')))} "
                    f"attempt_cap={r.get('candidate_attempt_cap')} "
                    f"toxic_samples={r.get('toxic_pnl_min_samples')} "
                    f"yellow_sparse={int(bool(r.get('yellow_sparse_active')))} "
                    f"green_sparse={int(bool(r.get('green_sparse_active')))} "
                    f"dust_safe={int(bool(r.get('dust_safe_close')))} "
                    f"dust_park={int(bool(r.get('dust_park_enabled')))} "
                    f"dust_hb={r.get('dust_heartbeat_ticks')} "
                    f"dust_compact={int(bool(r.get('dust_compact_enabled')))} "
                    f"dust_compact_frac={self._fmt(r.get('dust_compact_min_fraction'))} "
                    f"kappa_complete={int(bool(r.get('kappa_completion_enabled')))} "
                    f"kappa_target={r.get('kappa_completion_target')} "
                    f"kappa_bonus={self._fmt(r.get('kappa_completion_rank_bonus'))} "
                    f"kappa_fill_mult={self._fmt(r.get('kappa_completion_fill_mult'))} "
                    f"kappa_attempt_cap={r.get('kappa_completion_attempt_cap')} "
                    f"kappa_success_cap={r.get('kappa_completion_success_cap')} "
                    f"normal_attempt_cap={r.get('normal_attempt_cap')} "
                    f"actionable_fill={int(bool(r.get('actionable_fill_enabled')))} "
                    f"actionable_min_samples={r.get('actionable_fill_min_samples')} "
                    f"actionable_rank_w={self._fmt(r.get('actionable_fill_rank_weight'))} "
                    f"dust_rank_penalty={self._fmt(r.get('dust_risk_rank_penalty'))} "
                    f"dust_target={self._fmt(r.get('dust_risk_target'))} "
                    f"one_away_bonus={self._fmt(r.get('kappa_one_away_bonus'))} "
                    f"partial_hold={int(bool(r.get('partial_fill_hold_enabled')))} "
                    f"hold_min_dust={self._fmt(r.get('partial_fill_hold_min_dust_prob'))} "
                    f"force_mm_po={int(bool(r.get('force_mm_post_only')))} "
                    f"dust_cooldown={r.get('dust_compact_cooldown_ticks')} "
                    f"min_order_sync={int(bool(r.get('sync_min_order')))} "
                    f"run_id={self._short(r.get('run_id'))} "
                    f"file={self._short(r.get('output_file'))}")
        if typ == "STATE_RESET":
            return (
                f"[S1R_STATE_RESET] tick={r.get('tick')} "
                f"reason={r.get('reason')} "
                f"old_sim_id={r.get('old_sim_id')} "
                f"new_sim_id={r.get('new_sim_id')} "
                f"old_obs_total={r.get('old_obs_total')} "
                f"new_obs_total={r.get('new_obs_total')}"
            )
        if typ == "SESSION_TRANSITION":
            return (
                f"[S1R_SESSION_TRANSITION] tick={r.get('tick')} "
                f"old_sim={r.get('old_sim')} "
                f"new_sim={r.get('new_sim')} "
                f"reason={r.get('reason')} "
                f"quarantine={r.get('quarantine')} "
                f"inventory_reconciled={r.get('inventory_reconciled')}"
            )
        if typ == "DEBUG_CONFIG":
            return (f"[S1R_CONFIG] debug_enabled={int(bool(r.get('enabled')))} "
                    f"debug_every_n={r.get('every_n')} debug_book={r.get('book_filter')}")
        if typ == "REGIME":
            return (
                f"[S1R_REGIME] tick={r.get('tick')} "
                f"market_regime={self._short(r.get('market_regime'))} "
                f"score_regime={self._short(r.get('score_regime'))} "
                f"book_count={r.get('book_count')} "
                f"active={r.get('active')} inactive={r.get('inactive')} "
                f"spread_med={self._fmt(r.get('spread_med'))} "
                f"spread_p90={self._fmt(r.get('spread_p90'))} "
                f"spread_max={self._fmt(r.get('spread_max'))} "
                f"vol_med={self._fmt(r.get('vol_med'))} vol_p90={self._fmt(r.get('vol_p90'))} "
                f"trade_rate_med={self._fmt(r.get('trade_rate_med'))} "
                f"trade_rate_p90={self._fmt(r.get('trade_rate_p90'))} "
                f"liquid_ratio={self._fmt(r.get('liquid_ratio'))} "
                f"stressed_ratio={self._fmt(r.get('stressed_ratio'))} "
                f"trend_up_ratio={self._fmt(r.get('trend_up_ratio'))} "
                f"trend_down_ratio={self._fmt(r.get('trend_down_ratio'))} "
                f"market_trigger={self._short(r.get('market_trigger'))} "
                f"market_threshold={self._short(r.get('market_threshold'))} "
                f"score_trigger={self._short(r.get('score_trigger'))} "
                f"score_threshold={self._short(r.get('score_threshold'))}"
            )
        if typ == "SCORE_REGIME":
            return (
                f"[S1R_SCORE_REGIME] tick={r.get('tick')} "
                f"state={self._short(r.get('state'))} "
                f"coverage_ratio={self._fmt(r.get('coverage_ratio'))} "
                f"eligible_ratio={self._fmt(r.get('eligible_ratio'))} "
                f"one_away={r.get('one_away')} "
                f"two_away={r.get('two_away')} "
                f"rt_velocity={self._fmt(r.get('rt_velocity'))} "
                f"trigger={self._short(r.get('trigger'))}"
            )
        if typ == "SCORE_PROGRESS":
            return (
                f"[S1R_SCORE_PROGRESS] tick={r.get('tick')} "
                f"score_qualified={r.get('score_qualified')} obs_qualified={r.get('obs_qualified')} "
                f"one_away={r.get('one_away')} two_away={r.get('two_away')} "
                f"expiring={r.get('expiring')} "
                f"critical_q={r.get('deadline_critical_qualified')} "
                f"critical_i={r.get('deadline_critical_incomplete')} "
                f"score_target={r.get('score_target')} score_deficit={r.get('score_deficit')} "
                f"productive_incomplete={r.get('productive_incomplete')} "
                f"qualified_suppressed={r.get('qualified_suppressed')}"
            )
        if typ == "STALE_RESCUE":
            return (
                f"[S1R_STALE_RESCUE] tick={r.get('tick')} book={r.get('book')} "
                f"reason={self._short(r.get('reason'))} failed_exits={r.get('failed_exits')} "
                f"deadline={self._fmt(r.get('deadline_urgency'))} "
                f"raw_net={self._fmt(r.get('raw_maker_net_bps'))} "
                f"maker_net={self._fmt(r.get('maker_net_bps'))} "
                f"floor={self._fmt(r.get('maker_floor_bps'))} "
                f"taker_net={self._fmt(r.get('taker_net_bps'))} "
                f"raw_px={self._fmt(r.get('raw_maker_price'))} "
                f"maker_px={self._fmt(r.get('selected_maker_price'))}"
            )
        if typ == "REALIZATION":
            return (
                f"[S1R_REALIZATION] book={r.get('book')} "
                f"inventory={self._fmt(r.get('inventory'))} "
                f"inventory_age={self._fmt(r.get('inventory_age'))} "
                f"exit_urgency={self._fmt(r.get('exit_urgency'))} "
                f"proposed_rung={self._short(r.get('proposed_rung'))} "
                f"taker_eligible={r.get('taker_eligible')} "
                f"inventory_pressure={self._fmt(r.get('inventory_pressure'))} "
                f"inventory_age_pressure={self._fmt(r.get('inventory_age_pressure'))} "
                f"drawdown_pressure={self._fmt(r.get('drawdown_pressure'))} "
                f"volatility_pressure={self._fmt(r.get('volatility_pressure'))} "
                f"adverse_flow_pressure={self._fmt(r.get('adverse_flow_pressure'))} "
                f"markout_pressure={self._fmt(r.get('markout_pressure'))} "
                f"kappa_pressure={self._fmt(r.get('kappa_pressure'))} "
                f"volume_cap_pressure={self._fmt(r.get('volume_cap_pressure'))} "
                f"realization_failure_pressure={self._fmt(r.get('realization_failure_pressure'))} "
                f"state={self._short(r.get('state'))} "
                f"kappa_remaining={r.get('kappa_remaining')} "
                f"kappa_boost={self._fmt(r.get('kappa_boost'))} "
                f"kappa_mode={self._short(r.get('kappa_realization_mode'))} "
                f"maker_exit_ev={self._fmt(r.get('maker_exit_ev'))} "
                f"expected_maker_exit={self._fmt(r.get('expected_maker_exit_value'))} "
                f"expected_taker_exit={self._fmt(r.get('expected_taker_exit_value'))} "
                f"p_fill_horizon={self._fmt(r.get('exit_p_fill_horizon'))} "
                f"taker_exit_cost={self._fmt(r.get('taker_exit_cost'))} "
                f"authority={self._short(r.get('taker_authority'))} "
                f"maker_net={self._fmt(r.get('maker_net_bps_actual'))} "
                f"taker_net={self._fmt(r.get('taker_net_bps_actual'))} "
                f"wait_net={self._fmt(r.get('wait_ev_bps_actual'))} "
                f"early_escape={r.get('early_escape_trigger')} "
                f"early_reason={self._short(r.get('early_escape_reason'))} "
                f"loss_headroom={self._fmt(r.get('protective_loss_headroom_bps'))} "
                f"protect_margin={self._fmt(r.get('protective_margin_bps'))} "
                f"failed_exits={r.get('failed_exit_count')} "
                f"exit_wait={self._fmt(r.get('time_since_first_exit_attempt'))} "
                f"selected_action={self._short(r.get('selected_action'))}"
            )
        if typ == "EXIT_URGENCY":
            return (
                f"[S1R_EXIT_URGENCY] book={r.get('book')} "
                f"exit_urgency={self._fmt(r.get('exit_urgency'))} "
                f"inventory_pressure={self._fmt(r.get('inventory_pressure'))} "
                f"inventory_age_pressure={self._fmt(r.get('inventory_age_pressure'))} "
                f"drawdown_pressure={self._fmt(r.get('drawdown_pressure'))} "
                f"volatility_pressure={self._fmt(r.get('volatility_pressure'))} "
                f"adverse_flow_pressure={self._fmt(r.get('adverse_flow_pressure'))} "
                f"markout_pressure={self._fmt(r.get('markout_pressure'))} "
                f"kappa_pressure={self._fmt(r.get('kappa_pressure'))} "
                f"volume_cap_pressure={self._fmt(r.get('volume_cap_pressure'))} "
                f"realization_failure_pressure={self._fmt(r.get('realization_failure_pressure'))}"
            )
        if typ == "LADDER":
            return (
                f"[S1R_LADDER] book={r.get('book')} "
                f"exit_urgency={self._fmt(r.get('exit_urgency'))} "
                f"band={self._short(r.get('ladder_band'))} "
                f"proposed={self._short(r.get('proposed_rung'))} "
                f"selected={self._short(r.get('selected_action'))} "
                f"taker_eligible={r.get('taker_eligible')} "
                f"passive_max={self._fmt(r.get('ladder_passive_max'))} "
                f"competitive_max={self._fmt(r.get('ladder_competitive_max'))} "
                f"aggressive_max={self._fmt(r.get('ladder_aggressive_max'))} "
                f"trigger={self._short(r.get('trigger'))}"
            )
        if typ == "TAKER_DECISION":
            return (
                f"[S1R_TAKER_DECISION] book={r.get('book')} "
                f"take={r.get('taker_take')} "
                f"reason={self._short(r.get('taker_reason') or r.get('trigger'))} "
                f"holding={self._fmt(r.get('expected_holding_cost'))} "
                f"taker_cost={self._fmt(r.get('expected_taker_cost'))} "
                f"net={self._fmt(r.get('expected_net_realization_pnl'))} "
                f"floor={self._fmt(r.get('net_floor_bps'))} "
                f"inventory_risk={self._fmt(r.get('inventory_risk'))} "
                f"expected_adverse_move={self._fmt(r.get('expected_adverse_move'))} "
                f"inventory_age_cost={self._fmt(r.get('inventory_age_cost'))} "
                f"kappa_opportunity_cost={self._fmt(r.get('kappa_opportunity_cost'))} "
                f"volume_cap_opportunity_cost={self._fmt(r.get('volume_cap_opportunity_cost'))} "
                f"taker_fee={self._fmt(r.get('taker_fee'))} "
                f"spread_cross_cost={self._fmt(r.get('spread_cross_cost'))} "
                f"slippage_buffer={self._fmt(r.get('slippage_buffer'))} "
                f"market_impact_buffer={self._fmt(r.get('market_impact_buffer'))} "
                f"economic_ok={r.get('economic_ok')} "
                f"floor_ok={r.get('floor_ok')} "
                f"catastrophic={r.get('catastrophic')} "
                f"econ_auth={r.get('economic_taker_authorized')} "
                f"score_auth={r.get('score_taker_authorized')} "
                f"risk_auth={r.get('risk_taker_authorized')} "
                f"pos_ev_auth={r.get('aggressive_positive_ev_taker_authorized')} "
                f"pos_ev_trigger={self._short(r.get('aggressive_positive_ev_trigger'))} "
                f"pos_ev_adv={self._fmt(r.get('aggressive_positive_ev_advantage_bps'))} "
                f"pos_ev_margin={self._fmt(r.get('aggressive_positive_ev_switch_margin_bps'))} "
                f"pos_ev_floor={self._fmt(r.get('aggressive_positive_ev_floor_bps'))} "
                f"direct_auth={r.get('direct_taker_authorized')} "
                f"authority={self._short(r.get('taker_authority'))} "
                f"loss_floor={self._fmt(r.get('allowed_loss_floor_bps'))} "
                f"econ_floor={self._fmt(r.get('economic_direct_max_loss_bps'))} "
                f"failed_exits={r.get('failed_exit_count')} "
                f"exit_wait={self._fmt(r.get('time_since_first_exit_attempt'))} "
                f"wait_ev={self._fmt(r.get('wait_ev'))} "
                f"taker_ev={self._fmt(r.get('taker_ev'))} "
                f"early_escape={r.get('early_escape_trigger')} "
                f"early_reason={self._short(r.get('early_escape_reason'))} "
                f"loss_headroom={self._fmt(r.get('protective_loss_headroom_bps'))} "
                f"protect_margin={self._fmt(r.get('protective_margin_bps'))} "
                f"sn79_take={r.get('sn79_take')} "
                f"sn79_margin={self._fmt(r.get('sn79_utility_margin'))} "
                f"selected={self._short(r.get('selected_action'))}"
            )
        if typ == "EXIT_QTY":
            return (
                f"[S1R_EXIT_QTY] book={r.get('book')} "
                f"qty={self._fmt(r.get('exit_qty'))} "
                f"desired={self._fmt(r.get('exit_qty_desired'))} "
                f"before={self._fmt(r.get('inventory_before'))} "
                f"after={self._fmt(r.get('inventory_after'))} "
                f"min_order={self._fmt(r.get('min_order'))} "
                f"reason={self._short(r.get('exit_qty_reason'))} "
                f"selected={self._short(r.get('selected_action'))}"
            )
        if typ == "DUST_ECON":
            return (
                f"[S1R_DUST_ECON] book={r.get('book')} "
                f"band={self._short(r.get('dust_band'))} "
                f"action={self._short(r.get('dust_action'))} "
                f"reason={self._short(r.get('dust_reason'))} "
                f"qty={self._fmt(r.get('dust_qty'))} "
                f"reduce={self._fmt(r.get('dust_reduce_qty'))} "
                f"after={self._fmt(r.get('dust_inventory_after'))} "
                f"maker_ev={self._fmt(r.get('dust_maker_ev_bps'))} "
                f"holding={self._fmt(r.get('dust_holding_cost_bps'))} "
                f"cleanup={self._fmt(r.get('dust_cleanup_cost_bps'))} "
                f"net={self._fmt(r.get('dust_expected_net_bps'))} "
                f"cross={r.get('dust_cross')} "
                f"allow={r.get('dust_allow')} "
                f"catastrophic={r.get('dust_catastrophic')}"
            )
        if typ == "INVENTORY_STATE":
            return (
                f"[S1R_INVENTORY_STATE] book={r.get('book')} "
                f"state={self._short(r.get('state'))} "
                f"same_side_mult={self._fmt(r.get('same_side_entry_mult'))} "
                f"exit_mult={self._fmt(r.get('exit_side_mult'))} "
                f"allow_increase={r.get('allow_inventory_increase')} "
                f"taker={r.get('taker_eligible')} "
                f"aggressive_maker={r.get('allow_aggressive_maker')}"
            )
        if typ == "SAME_SIDE":
            return (
                f"[S1R_SAME_SIDE] book={r.get('book')} "
                f"state={self._short(r.get('same_side_state'))} "
                f"same_size={self._fmt(r.get('same_side_size_mult'))} "
                f"same_priority={self._fmt(r.get('same_side_priority'))} "
                f"exit_priority={self._fmt(r.get('exit_side_priority'))} "
                f"exit_ticks={self._fmt(r.get('exit_improve_ticks'))} "
                f"disabled={r.get('same_side_disabled')} "
                f"inventory={self._fmt(r.get('inventory'))}"
            )
        if typ == "HYBRID":
            return (
                f"[S1R_HYBRID] tick={r.get('tick')} book={r.get('book')} "
                f"action={self._short(r.get('selected_action'))} "
                f"reason={self._short(r.get('hybrid_reason') or r.get('trigger'))} "
                f"lock_pnl={self._fmt(r.get('taker_lock_pnl_bps'))} "
                f"cross_cost={self._fmt(r.get('taker_crossing_cost_bps'))} "
                f"maker_ev={self._fmt(r.get('maker_exit_ev'))} "
                f"expected_maker_exit={self._fmt(r.get('expected_maker_exit_value'))} "
                f"expected_taker_exit={self._fmt(r.get('expected_taker_exit_value'))} "
                f"p_fill_horizon={self._fmt(r.get('exit_p_fill_horizon'))} "
                f"maker_fill={self._fmt(r.get('maker_fill_hazard'))} "
                f"qty_frac={self._fmt(r.get('taker_qty_frac'))} "
                f"qty={self._fmt(r.get('requested_qty'))} "
                f"allowed={r.get('allowed')}"
            )
        if typ == "HYBRID_SUMMARY":
            return (
                f"[S1R_HYBRID_SUMMARY] tick={r.get('tick')} "
                f"rt_velocity={self._fmt(r.get('round_trip_velocity'))} "
                f"rt_conversion={self._fmt(r.get('round_trip_conversion'))} "
                f"coverage_velocity={self._fmt(r.get('coverage_velocity'))} "
                f"kappa_qual_velocity={self._fmt(r.get('kappa_qualification_velocity'))} "
                f"inv_age_med={self._fmt(r.get('inventory_age_median'))} "
                f"inv_age_p90={self._fmt(r.get('inventory_age_p90'))} "
                f"maker={r.get('maker_exit_count')}/{self._fmt(r.get('maker_exit_pnl'))} "
                f"competitive={r.get('competitive_maker_count')}/{self._fmt(r.get('competitive_maker_pnl'))} "
                f"aggressive={r.get('aggressive_maker_count')}/{self._fmt(r.get('aggressive_maker_pnl'))} "
                f"taker={r.get('taker_exit_count')}/{self._fmt(r.get('taker_exit_pnl'))} "
                f"auth=e{r.get('economic_taker_auth')}/s{r.get('score_taker_auth')}/r{r.get('risk_taker_auth')}/p{r.get('positive_ev_taker_auth')} "
                f"taker_orders={r.get('actual_taker_orders')} taker_fills={r.get('actual_taker_fills')} "
                f"maker_realized={self._fmt(r.get('maker_realized_pnl'))} "
                f"taker_realized={self._fmt(r.get('taker_realized_pnl'))}"
            )
        if typ == "ADVERSE":
            return (
                f"[S1R_ADVERSE] book={r.get('book')} "
                f"ofi_raw={self._fmt(r.get('ofi_raw'))} "
                f"ofi_normalized={self._fmt(r.get('ofi_normalized'))} "
                f"ofi_fast={self._fmt(r.get('ofi_fast'))} "
                f"expected_markout={self._fmt(r.get('expected_markout'))} "
                f"adverse_selection_risk={self._fmt(r.get('adverse_selection_risk'))} "
                f"source={self._short(r.get('ofi_source'))} "
                f"supported={r.get('ofi_supported')} "
                f"width_mult={self._fmt(r.get('width_mult'))} "
                f"blocked={r.get('blocked')}"
            )
        if typ == "ENTRY_SIZE":
            return (
                f"[S1R_ENTRY_SIZE] book={r.get('book')} "
                f"entry_size={self._fmt(r.get('entry_size'))} "
                f"safe_size={self._fmt(r.get('safe_size', r.get('entry_size')))} "
                f"admission={self._short(r.get('admission'))} "
                f"allow={r.get('admission_allow')} "
                f"trigger={self._short(r.get('admission_trigger'))} "
                f"min_order={self._fmt(r.get('min_order', r.get('min_order_size')))} "
                f"tolerance={self._fmt(r.get('tolerance'))} "
                f"expected_exit_capacity={self._fmt(r.get('expected_exit_capacity'))} "
                f"inventory_after_full_fill={self._fmt(r.get('inventory_after_full_fill'))} "
                f"inventory_risk_after_fill={self._fmt(r.get('inventory_risk_after_fill'))} "
                f"inventory_factor={self._fmt(r.get('inventory_factor'))} "
                f"liquidity_factor={self._fmt(r.get('liquidity_factor'))} "
                f"exit_capacity_factor={self._fmt(r.get('exit_capacity_factor'))} "
                f"volume_headroom_factor={self._fmt(r.get('volume_headroom_factor'))} "
                f"risk_factor={self._fmt(r.get('risk_factor'))}"
            )
        if typ == "VOLUME_CAP":
            if str(r.get("reason") or "").upper() == "SUMMARY":
                return (
                    f"[S1R_VOLUME_CAP] tick={r.get('tick')} reason=SUMMARY "
                    f"cap_quote={self._fmt(r.get('cap_quote'))} "
                    f"books_cap_reached={r.get('books_cap_reached')} "
                    f"books_headroom_lt_10pct={r.get('books_headroom_lt_10pct')} "
                    f"books_headroom_lt_25pct={r.get('books_headroom_lt_25pct')} "
                    f"median_headroom={self._fmt(r.get('median_headroom'))} "
                    f"min_headroom={self._fmt(r.get('min_headroom'))}"
                )
            return (
                f"[S1R_VOLUME_CAP] tick={r.get('tick')} "
                f"book={r.get('book')} "
                f"traded_volume={self._fmt(r.get('traded_volume'))} "
                f"cap_quote={self._fmt(r.get('cap_quote'))} "
                f"remaining_quote={self._fmt(r.get('remaining_quote'))} "
                f"headroom={self._fmt(r.get('headroom'))} "
                f"requested_notional={self._fmt(r.get('requested_notional'))} "
                f"allowed={r.get('allowed')} "
                f"reason={self._short(r.get('reason'))}"
            )
        if typ == "QUOTE":
            q_ahead = r.get("queue_ahead")
            q_depth = r.get("queue_depth_at_price")
            queue = ""
            if q_depth is not None:
                queue += f" queue_depth_at_price={self._fmt(q_depth)}"
            if q_ahead is not None:
                queue += f" queue_ahead={self._fmt(q_ahead)}"
            return (
                f"[S1R_QUOTE] tick={r.get('tick')} quote_id={r.get('quote_id')} "
                f"client_id={self._short(r.get('client_id'))} book={r.get('book')} "
                f"side={self._short(r.get('side'))} "
                f"decision_ts={r.get('decision_timestamp')} submit_ts={r.get('submit_timestamp')} "
                f"cancel_ts={r.get('cancel_timestamp')} fill_ts={r.get('fill_timestamp')} "
                f"mid={self._fmt(r.get('mid'))} microprice={self._fmt(r.get('microprice'))} "
                f"microprice_delta={self._fmt(r.get('microprice_delta'))} "
                f"best_bid={self._fmt(r.get('best_bid'))} best_ask={self._fmt(r.get('best_ask'))} "
                f"spread={self._fmt(r.get('spread'))} spread_bps={self._fmt(r.get('spread_bps'))} "
                f"quote_price={self._fmt(r.get('quote_price'))} "
                f"dist_ticks={self._fmt(r.get('distance_from_touch_ticks'))} "
                f"dist_bps={self._fmt(r.get('distance_from_touch_bps'))} "
                f"vol={self._fmt(r.get('volatility'))} trade_rate={self._fmt(r.get('trade_rate'))} "
                f"imbalance={self._fmt(r.get('imbalance'))} "
                f"ofi_raw={self._fmt(r.get('ofi_raw'))} "
                f"ofi_normalized={self._fmt(r.get('ofi_normalized'))} "
                f"ofi_fast={self._fmt(r.get('ofi_fast'))} "
                f"deep_imbalance={self._fmt(r.get('deep_imbalance'))} "
                f"momentum={self._fmt(r.get('momentum'))} "
                f"trade_imbalance={self._fmt(r.get('trade_imbalance'))} "
                f"trade_sign_persistence={self._fmt(r.get('trade_sign_persistence'))} "
                f"inv_before={self._fmt(r.get('inventory_before'))} "
                f"qty={self._fmt(r.get('requested_quantity'))} "
                f"ttl_ms={self._fmt(r.get('configured_ttl_ms'))} "
                f"p_fill={self._fmt(r.get('predicted_fill_probability'))} "
                f"p_any={self._fmt(r.get('predicted_any_fill_probability'))} "
                f"p_act={self._fmt(r.get('predicted_actionable_fill_probability'))} "
                f"p_dust={self._fmt(r.get('predicted_dust_probability'))} "
                f"market_regime={self._short(r.get('market_regime'))} "
                f"score_regime={self._short(r.get('score_regime'))} "
                f"archetype={self._short(r.get('book_archetype'))} "
                f"kappa_obs={r.get('kappa_observation_count_before')}"
                f"{queue}"
            )
        if typ == "EXEC_PROB":
            return (
                f"[S1R_EXEC_PROB] book={r.get('book')} "
                f"side={self._short(r.get('side'))} "
                f"p_any={self._fmt(r.get('p_any'))} "
                f"p_actionable={self._fmt(r.get('p_actionable'))} "
                f"p_dust={self._fmt(r.get('p_dust'))} "
                f"ttf_hazard={self._fmt(r.get('ttf_hazard'))} "
                f"remaining_p_any={self._fmt(r.get('remaining_p_any'))} "
                f"source={self._short(r.get('source'))} "
                f"usable={r.get('usable')} n={r.get('n')}"
            )
        if typ == "FILL":
            return (
                f"[S1R_FILL] tick={r.get('tick')} quote_id={r.get('quote_id')} "
                f"client_id={self._short(r.get('client_id'))} book={r.get('book')} "
                f"side={self._short(r.get('side'))} fill_class={self._short(r.get('fill_class'))} "
                f"fill_price={self._fmt(r.get('fill_price'))} "
                f"requested_qty={self._fmt(r.get('requested_quantity'))} "
                f"filled_qty={self._fmt(r.get('filled_quantity'))} "
                f"remaining_qty={self._fmt(r.get('remaining_quantity'))} "
                f"inv_before={self._fmt(r.get('inventory_before'))} "
                f"inv_after={self._fmt(r.get('inventory_after'))} "
                f"quote_age_ms={self._fmt(r.get('quote_age_ms'))} "
                f"ttl_ms={self._fmt(r.get('configured_ttl_ms'))} "
                f"p_fill={self._fmt(r.get('predicted_fill_probability'))} "
                f"p_any={self._fmt(r.get('predicted_any_fill_probability'))} "
                f"p_act={self._fmt(r.get('predicted_actionable_fill_probability'))} "
                f"p_dust={self._fmt(r.get('predicted_dust_probability'))} "
                f"market_regime={self._short(r.get('market_regime'))} "
                f"score_regime={self._short(r.get('score_regime'))} "
                f"archetype={self._short(r.get('book_archetype'))} "
                f"kappa_before={r.get('kappa_observation_count_before')} "
                f"kappa_after={r.get('kappa_observation_count_after')} "
                f"maker={int(bool(r.get('maker')))} fee={self._fmt(r.get('fee'))} "
                f"min_order={self._fmt(r.get('min_order_size'))}"
            )
        if typ == "MARKOUT":
            return (
                f"[S1R_MARKOUT] quote_id={r.get('quote_id')} book={r.get('book')} "
                f"side={self._short(r.get('side'))} horizon_ms={r.get('horizon_ms')} "
                f"fill_price={self._fmt(r.get('fill_price'))} "
                f"future_mid={self._fmt(r.get('future_mid'))} "
                f"future_ts={r.get('future_ts')} "
                f"markout_bps={self._fmt(r.get('markout_bps'))} "
                f"status={self._short(r.get('status'))}"
            )
        if typ == "FILL_CAL":
            return (
                f"[S1R_FILL_CAL] kind={self._short(r.get('kind'))} "
                f"side={self._short(r.get('side'))} "
                f"bucket={self._short(r.get('bucket_label') or r.get('bucket'))} "
                f"predicted_mean={self._fmt(r.get('predicted_mean'))} "
                f"observed_rate={self._fmt(r.get('observed_rate'))} "
                f"sample_count={r.get('sample_count')} "
                f"brier_component={self._fmt(r.get('brier_component'))} "
                f"brier_overall={self._fmt(r.get('brier_overall'))} "
                f"n={r.get('observations')} events={r.get('events')} "
                f"censored={r.get('censored')}"
            )
        if typ == "RANK":
            return (
                f"[S1R_RANK] book={r.get('book')} side={self._short(r.get('side'))} "
                f"alpha={self._fmt(r.get('alpha'))} "
                f"fill_prob_old={self._fmt(r.get('fill_prob_old'))} "
                f"fill_prob_hazard={self._fmt(r.get('fill_prob_hazard'))} "
                f"actionable_fill_prob={self._fmt(r.get('actionable_fill_prob'))} "
                f"dust_prob={self._fmt(r.get('dust_prob'))} "
                f"spread_capture_bps={self._fmt(r.get('spread_capture_bps'))} "
                f"expected_markout_bps={self._fmt(r.get('expected_markout_bps'))} "
                f"fees_bps={self._fmt(r.get('fees_bps'))} "
                f"trading_ev={self._fmt(r.get('trading_ev'))} "
                f"observation_count={r.get('observation_count')} "
                f"observations_remaining={r.get('observations_remaining')} "
                f"completion_value={self._fmt(r.get('completion_value'))} "
                f"dust_cost={self._fmt(r.get('dust_cost'))} "
                f"inventory_cost={self._fmt(r.get('inventory_cost'))} "
                f"latency_cost={self._fmt(r.get('latency_cost'))} "
                f"final_score={self._fmt(r.get('final_score'))} "
                f"eligible={int(bool(r.get('eligible')))} "
                f"reject={self._short(r.get('reject_reason'))}"
            )
        if typ == "KAPPA":
            return (
                f"[S1R_KAPPA] book={r.get('book')} "
                f"obs={r.get('obs', r.get('realized_observation_count'))} "
                f"required={r.get('required_observations', r.get('required'))} "
                f"remaining={r.get('remaining', r.get('observations_remaining'))} "
                f"eligible={int(bool(r.get('eligible')))} "
                f"completion_value={self._fmt(r.get('completion_value'))} "
                f"trading_ev={self._fmt(r.get('trading_ev'))} "
                f"final_priority={self._fmt(r.get('final_priority'))} "
                f"lane={self._short(r.get('lane'))}"
            )
        if typ == "SCHED":
            return (
                f"[S1R_SCHED] required={r.get('required_observation_count')} "
                f"books_0_obs={r.get('books_0_obs', r.get('books_zero_obs'))} "
                f"books_1_remaining={r.get('books_1_remaining', r.get('books_one_remaining'))} "
                f"books_2_remaining={r.get('books_2_remaining', r.get('books_two_remaining'))} "
                f"books_eligible={r.get('books_eligible', r.get('eligible_books'))} "
                f"completion_attempts={r.get('completion_attempts', r.get('kappa_completion_attempts'))} "
                f"completion_successes={r.get('completion_successes', r.get('kappa_completion_successes'))} "
                f"round_trip_velocity={self._fmt(r.get('round_trip_velocity'))} "
                f"score_ev={r.get('enable_score_ev')} "
                f"coverage={r.get('coverage_used')}/{r.get('coverage_slots')} "
                f"completion={r.get('completion_used')}/{r.get('completion_slots')} "
                f"realization={r.get('realization_used')}/{r.get('realization_slots')} "
                f"overflow={r.get('overflow_used')}/{r.get('shared_overflow_slots')}"
            )
        if typ == "LANES":
            return (
                f"[S1R_LANES] stage={r.get('stage')} "
                f"coverage={r.get('coverage_used')}/{r.get('coverage_slots')} "
                f"completion={r.get('completion_used')}/{r.get('completion_slots')} "
                f"realization={r.get('realization_used')}/{r.get('realization_slots')} "
                f"overflow={r.get('overflow_used')}/{r.get('shared_overflow_slots')} "
                f"selected={r.get('selected_count')} "
                f"actual_nonflat={r.get('actual_nonflat_inventory')} "
                f"stale_fifo_keys={r.get('stale_empty_position_keys')}"
            )
        if typ == "CANCEL_DECISION":
            return (
                f"[S1R_CANCEL_DECISION] book={r.get('book')} "
                f"side={self._short(r.get('side'))} cancel={r.get('cancel')} "
                f"cancel_reason={self._short(r.get('cancel_reason', r.get('reason')))} "
                f"reason={self._short(r.get('reason'))} "
                f"old_price={self._fmt(r.get('old_price'))} "
                f"new_price={self._fmt(r.get('new_price'))} "
                f"price_delta_ticks={self._fmt(r.get('price_delta_ticks'))} "
                f"old_ev={self._fmt(r.get('old_ev'))} "
                f"new_ev={self._fmt(r.get('new_ev'))} "
                f"ev_delta={self._fmt(r.get('ev_delta'))} "
                f"quote_age={self._fmt(r.get('quote_age', r.get('order_age_ms')))} "
                f"chosen_ttl={self._fmt(r.get('chosen_ttl'))}"
            )
        if typ == "TTL":
            return (
                f"[S1R_TTL] book={r.get('book')} "
                f"chosen_ttl={self._fmt(r.get('chosen_ttl', r.get('chosen_ttl_ms')))} "
                f"chosen_ttl_ms={self._fmt(r.get('chosen_ttl_ms'))} "
                f"quote_age={self._fmt(r.get('quote_age'))} "
                f"ttl_reason={self._short(r.get('ttl_reason'))} "
                f"fill_hazard={self._fmt(r.get('fill_hazard'))} "
                f"toxicity={r.get('toxicity')} "
                f"volatility={self._fmt(r.get('volatility'))} "
                f"market_regime={self._short(r.get('market_regime'))}"
            )
        if typ == "RESPOND_TIMING":
            return (
                f"[S1R_TIMING] tick={r.get('tick')} sim_ts={r.get('timestamp')} "
                f"screen_ms={self._fmt(r.get('screen_ms', r.get('screen_all_books_ms')))} "
                f"full_predict_ms={self._fmt(r.get('full_predict_ms'))} "
                f"ranking_ms={self._fmt(r.get('ranking_ms', r.get('selection_ms')))} "
                f"build_orders_ms={self._fmt(r.get('build_orders_ms'))} "
                f"logging_ms={self._fmt(r.get('logging_ms'))} "
                f"total_response_ms={self._fmt(r.get('total_response_ms'))} "
                f"p95_response_ms={self._fmt(r.get('p95_response_ms'))} "
                f"p95_target_ms={self._fmt(r.get('p95_target_ms'))} "
                f"candidate_count={r.get('candidate_count', 0)} "
                f"screen_fallback={r.get('screen_fallback', 0)} "
                f"forced_inventory_count={r.get('forced_inventory_count', 0)} "
                f"forced_kappa_count={r.get('forced_kappa_count', 0)}"
            )
        if typ == "TIMING":
            return (f"[S1R_REQ] tick={r.get('tick')} sim_ts={r.get('timestamp')} "
                    f"instructions={r.get('instructions', 0)} notices={r.get('notices', 0)} "
                    f"update_ms={self._fmt(r.get('update_ms'))} respond_ms={self._fmt(r.get('respond_ms'))} "
                    f"report_ms={self._fmt(r.get('report_ms'))} total_ms={self._fmt(r.get('total_ms'))}")
        if typ == "SKIP_SUMMARY":
            reasons = r.get("reasons") or {}
            if isinstance(reasons, dict):
                ordered = sorted(reasons.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
                reason_text = ",".join(f"{k}:{v}" for k, v in ordered[:8])
            else:
                reason_text = self._short(reasons)
            return (
                f"[S1R_SKIP_SUMMARY] tick={r.get('tick')} total={r.get('total', 0)} "
                f"reasons={reason_text}"
            )
        if typ == "DECISION":
            raw = str(r.get("reason", "NO_ACTION"))
            reason = self.REASON_ALIAS.get(raw, raw)
            action = str(r.get("action", "SKIP")).upper()
            inv = r.get("inventory") or {}
            common = (
                f"tick={r.get('tick')} book={r.get('book_id')} regime={self._short(r.get('regime'))} "
                f"overlay={self._short(r.get('overlay'))} archetype={self._short(r.get('archetype'))} "
                f"arch_src={self._short(r.get('archetype_source'))} tier={self._short(r.get('tier'))} "
                f"spread_bps={self._fmt(r.get('spread_bps'))} "
                f"stress_cut={self._fmt(r.get('stress_spread_bps'))} "
                f"toxic_cut={self._fmt(r.get('toxic_spread_bps'))} "
                f"volatility={self._fmt(r.get('volatility'))} trade_rate={self._fmt(r.get('trade_rate'))} "
                f"imbalance={self._fmt(r.get('imbalance'))} "
                f"ofi_raw={self._fmt(r.get('ofi_raw'))} "
                f"ofi_normalized={self._fmt(r.get('ofi_normalized'))} "
                f"ofi_fast={self._fmt(r.get('ofi_fast'))} "
                f"expected_markout={self._fmt(r.get('expected_markout'))} "
                f"adverse_selection_risk={self._fmt(r.get('adverse_selection_risk'))} "
                f"loss_streak={self._fmt(r.get('loss_streak'))} recent_pnl={self._fmt(r.get('recent_pnl'))} "
                f"toxic_loss={self._fmt(r.get('toxic_loss'))} toxic_pnl={self._fmt(r.get('toxic_pnl'))} "
                f"toxic_spread={self._fmt(r.get('toxic_spread'))} "
                f"toxic_archetype={self._fmt(r.get('toxic_archetype'))} "
                f"toxic_red_tier={self._fmt(r.get('toxic_red_tier'))} "
                f"stressed_by_spread={self._fmt(r.get('stressed_by_spread'))} "
                f"stressed_by_regime={self._fmt(r.get('stressed_by_regime'))} "
                f"legacy_global_stress={self._fmt(r.get('legacy_stressed_by_regime'))} "
                f"signal={self._fmt(r.get('signal'))} alpha={self._fmt(r.get('expected_alpha'))} "
                f"min_alpha={self._fmt(r.get('min_expected_alpha'))} "
                f"fill_bid={self._fmt(r.get('fill_buy'))} fill_ask={self._fmt(r.get('fill_sell'))} "
                f"qty={self._fmt(r.get('quantity'))} dyn_raw={self._fmt(r.get('dynamic_size_raw'))} "
                f"dyn_final={self._fmt(r.get('dynamic_size_final'))} "
                f"min_order={self._fmt(r.get('min_order_size'))} "
                f"promoted_min={self._fmt(r.get('size_promoted_to_min'))} "
                f"bootstrap={self._fmt(r.get('inactive_bootstrap'))} "
                f"inactive_bypass={self._fmt(r.get('inactive_gate_bypassed'))} "
                f"dead_rate_hit={self._fmt(r.get('dead_trade_rate_hit'))} "
                f"active_sparse={self._fmt(r.get('active_sparse'))} "
                f"active_sparse_tier={self._short(r.get('active_sparse_tier'))} "
                f"dust_quarantine={self._fmt(r.get('dust_quarantine'))} "
                f"dust_compact={self._fmt(r.get('dust_compact'))} "
                f"lane={self._fmt(r.get('scheduler_lane'))} "
                f"normal_attempts={self._fmt(r.get('normal_attempts_used'))}/{self._fmt(r.get('normal_attempt_cap'))} "
                f"completion_attempts={self._fmt(r.get('completion_attempts_used'))}/{self._fmt(r.get('completion_attempt_cap'))} "
                f"kappa_complete={self._fmt(r.get('kappa_completion_candidate'))} "
                f"kappa_samples={self._fmt(r.get('kappa_completion_samples'))} "
                f"kappa_fill_relaxed={self._fmt(r.get('kappa_completion_fill_relaxed'))} "
                f"actionable_p={self._fmt(r.get('actionable_fill_p'))} "
                f"dust_p={self._fmt(r.get('dust_fill_p'))} "
                f"actionable_n={self._fmt(r.get('actionable_fill_samples'))} "
                f"actionable_src={self._short(r.get('actionable_fill_source'))} "
                f"partial_hold={self._fmt(r.get('partial_fill_hold'))} "
                f"hold_expiry={self._fmt(r.get('partial_fill_hold_expiry_ns'))} "
                f"force_mm_po={self._fmt(r.get('force_mm_post_only'))} "
                f"toxic_pnl_samples={self._fmt(r.get('toxic_pnl_samples'))} "
                f"touch_net_bps={self._fmt(r.get('aggressive_touch_net_bps'))} "
                f"inv_util={self._fmt(r.get('inventory_util'))} "
                f"dust={self._fmt(r.get('dust_position'))} "
                f"exp_pnl={self._fmt(r.get('expected_realized_pnl'))} "
                f"inv_base={self._fmt(inv.get('net_base'))} inv_band={self._short(inv.get('band'))} "
                f"instructions={r.get('instructions', 0)}"
            )
            if action == "SKIP":
                return f"[S1R_SKIP] {common} side=BOTH reason={reason} raw_reason={raw}"
            return (f"[S1R_DECISION] {common} action={action} reason={reason} "
                    f"bid={self._fmt(r.get('bid_px'))} ask={self._fmt(r.get('ask_px'))} "
                    f"decision_ms={self._fmt(r.get('decision_ms'))}")
        if typ == "ACTIONABLE_FILL":
            return (
                f"[S1R_ACTIONABLE] tick={r.get('tick')} book={r.get('book_id')} "
                f"side={self._short(r.get('side'))} bucket={r.get('bucket')} "
                f"class={self._short(r.get('classification'))} "
                f"fill_qty={self._fmt(r.get('fill_qty'))} fill_frac={self._fmt(r.get('fill_fraction'))} "
                f"net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))} "
                f"p_actionable={self._fmt(r.get('p_actionable'))} p_dust={self._fmt(r.get('p_dust'))} "
                f"samples={r.get('samples')} confident={r.get('confident')}"
            )
        if typ == "POSITION":
            return (
                f"[S1R_POSITION] tick={r.get('tick')} book={r.get('book_id')} "
                f"transition={self._short(r.get('transition'))} "
                f"net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))} "
                f"realized_delta={self._fmt(r.get('realized_pnl_delta'))} "
                f"round_trip={self._fmt(r.get('round_trip'))} total_round_trips={r.get('round_trip_total')} "
                f"book_samples={r.get('round_trip_book_samples')} "
                f"realized_obs={r.get('realized_book_observations')} "
                f"flat_eps={self._fmt(r.get('execution_flat_epsilon'))} "
                f"reason={self._short(r.get('reason'))}"
            )
        if typ == "POSITION_GUARD":
            return (
                f"[S1R_DUST] tick={r.get('tick')} book={r.get('book_id')} "
                f"net_base={self._fmt(r.get('net_base'))} min_order={self._fmt(r.get('min_order_size'))} "
                f"age_ticks={self._fmt(r.get('age_ticks'))} parked={self._fmt(r.get('parked'))} "
                f"stale={self._fmt(r.get('stale'))} reason={self._short(r.get('reason'))}"
            )
        if typ == "ORDER_LIFECYCLE":
            phase = str(r.get("phase", "UNKNOWN")).upper()
            book = r.get("book_id")
            if phase == "SUBMITTED":
                p = r.get("instruction") or {}
                return (f"[S1R_ORDER] tick={r.get('tick')} book={book} "
                        f"side={self._side(self._pick(p, 'direction', 'side'))} "
                        f"type={self._short(self._pick(p, 'orderType', 'order_type', 'type'))} "
                        f"price={self._fmt(self._pick(p, 'price', 'limitPrice', 'limit_price'))} "
                        f"qty={self._fmt(self._pick(p, 'quantity', 'qty', 'size'))} "
                        f"tif={self._short(self._pick(p, 'timeInForce', 'time_in_force', 'tif'))} "
                        f"client_id={self._short(self._pick(p, 'clientOrderId', 'client_order_id'))} index={r.get('instruction_index')}")
            e = r.get("event") or {}
            if "TRADE" in phase or "FILL" in phase:
                return (f"[S1R_TRADE_NOTICE] tick={r.get('tick')} book={book} phase={phase} "
                        f"side={self._side(self._pick(e, 'direction', 'side'))} "
                        f"price={self._fmt(self._pick(e, 'price', 'tradePrice', 'trade_price'))} "
                        f"qty={self._fmt(self._pick(e, 'quantity', 'qty', 'size'))} "
                        f"client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))} "
                        f"net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))}")
            if "REJECT" in phase or "FAIL" in phase:
                return (f"[S1R_REJECT] tick={r.get('tick')} book={book} phase={phase} "
                        f"reason={self._short(self._pick(e, 'reason', 'message', 'status', 'error'), 240)} "
                        f"client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}")
            if "CANCEL" in phase or "EXPIRE" in phase:
                return (f"[S1R_CANCEL] tick={r.get('tick')} book={book} phase={phase} "
                        f"reason={self._short(self._pick(e, 'reason', 'message', 'status'))} "
                        f"client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}")
            return f"[S1R_NOTICE] tick={r.get('tick')} book={book} phase={phase}"
        if typ == "RUN_SUMMARY":
            avg = r.get("average_latency_ms") or {}
            mx = r.get("max_latency_ms") or {}
            return (f"[S1R_SUMMARY] tick={r.get('tick')} responses={r.get('responses')} "
                    f"top_skips={self._counts(r.get('reason_counts') or {}, 8)} "
                    f"events={self._counts(r.get('event_counts') or {}, 8)} "
                    f"avg_total_ms={self._fmt(avg.get('total_ms'))} max_total_ms={self._fmt(mx.get('total_ms'))} "
                    f"opens={r.get('research_position_opens', 0)} "
                    f"reductions={r.get('research_position_reductions', 0)} "
                    f"round_trips={r.get('research_round_trip_closes', 0)} "
                    f"open_positions={r.get('research_open_positions', 0)} "
                    f"actionable_open={r.get('research_actionable_open_positions', 0)} "
                    f"parked_dust={r.get('research_parked_dust_positions', 0)} "
                    f"parked_dust_base={self._fmt(r.get('research_parked_dust_abs_base'))} "
                    f"dust_entries={r.get('research_dust_entries', 0)} "
                    f"dust_releases={r.get('research_dust_releases', 0)} "
                    f"dust_compact_orders={r.get('research_dust_compact_orders', 0)} "
                    f"dust_compact_fills={r.get('research_dust_compact_fills', 0)} "
                    f"dust_cooldown_skips={r.get('research_dust_compact_cooldown_skips', 0)} "
                    f"actionable_fill_ratio={self._fmt(r.get('research_actionable_fill_ratio'))} "
                    f"dust_fill_ratio={self._fmt(r.get('research_dust_maker_fill_ratio'))} "
                    f"partial_hold={r.get('research_partial_fill_hold_quoted', 0)}/{r.get('research_partial_fill_hold_candidates', 0)} "
                    f"oldest_dust_ticks={r.get('research_oldest_dust_ticks', 0)} "
                    f"kappa_eligible={r.get('research_kappa_books_eligible', 0)} "
                    f"kappa_incomplete_dust={r.get('research_kappa_incomplete_dust', 0)} "
                    f"kappa_zero_dust={r.get('research_kappa_zero_obs_dust', 0)} "
                    f"kappa_one_away_dust={r.get('research_kappa_one_away_dust', 0)} "
                    f"kappa_pending1={r.get('research_kappa_books_pending_1', 0)} "
                    f"kappa_pending2={r.get('research_kappa_books_pending_2', 0)} "
                    f"normal_lane={r.get('research_normal_quote_attempts', 0)}/{r.get('research_normal_attempt_cap', 0)} "
                    f"completion_lane={r.get('research_completion_quote_attempts', 0)}/{r.get('research_completion_attempt_cap', 0)} "
                    f"completion_success={r.get('research_completion_quote_successes', 0)}/{r.get('research_completion_success_cap', 0)} "
                    f"dust_blocks={r.get('research_dust_blocks', 0)} queue_dropped={self._rdropped}")
        if typ == "ERROR":
            return (f"[S1R_ERROR] tick={r.get('tick')} stage={self._short(r.get('stage'))} "
                    f"type={self._short(r.get('error_type'))} error={self._short(r.get('error'), 400)}")
        return None

    def _shutdown_research(self) -> None:
        try:
            self._research_save_session(force=True)
        except Exception:
            pass
        if self._rstop is not None:
            self._rstop.set()
        if self._rq is not None:
            deadline = time.time() + 1.5
            while self._rq.unfinished_tasks and time.time() < deadline:
                time.sleep(0.01)
        if self._rworker is not None and self._rworker.is_alive():
            self._rworker.join(timeout=0.5)
        if self._rfile is not None:
            try:
                self._rfile.flush(); self._rfile.close()
            except OSError:
                pass
            self._rfile = None

    @staticmethod
    def _pick(obj: Any, *names: str) -> Any:
        if obj is None:
            return None
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    @staticmethod
    def _int(v: Any) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fmt(v: Any) -> str:
        if v is None:
            return "-"
        if isinstance(v, bool):
            return "1" if v else "0"
        try:
            x = float(v)
        except (TypeError, ValueError):
            return str(v).replace(" ", "_")
        if abs(x) >= 1000:
            return f"{x:.3f}"
        if abs(x) >= 1:
            return f"{x:.6f}".rstrip("0").rstrip(".")
        return f"{x:.8f}".rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _short(v: Any, n: int = 120) -> str:
        if v is None:
            return "-"
        return "_".join(str(v).replace("\n", " ").replace("\r", " ").split())[:n]

    @classmethod
    def _side(cls, v: Any) -> str:
        if v is None:
            return "-"
        s = str(v).upper()
        if "BUY" in s or s == "BID":
            return "BID"
        if "SELL" in s or s == "ASK":
            return "ASK"
        return cls._short(v)

    @staticmethod
    def _counts(d: dict[str, Any], n: int) -> str:
        try:
            items = sorted(((str(k), int(v)) for k, v in d.items()), key=lambda kv: (-kv[1], kv[0]))[:n]
            return ",".join(f"{k}:{v}" for k, v in items) or "-"
        except Exception:
            return "-"


if __name__ == "__main__":
    launch(Strategy1_Research)
