# SPDX-License-Identifier: MIT
"""Strategy1-Direct V4.16.2 A1.7.4.3.1 Same-Book Pending Order Ownership Research candidate.

This module intentionally does *not* add another strategy layer.  It reuses the
existing V4.16.2 Research state/learning/persistence infrastructure but replaces
its hot orchestration path with the shortest useful authority chain:

    128-book observable scan -> current spread/fee/Kappa rank -> deep top-K
                  -> hard safety -> current Maker edge -> Maker/Skip -> final validation

A1.7.4.3.1 keeps A1.7.4.3 strict aggregate in-flight exposure reservation,
A1.7.4.2 Kappa-safe dust compaction, A1.7.4.1 replay de-duplication, A1.7.4
tail recovery, A1.7.2 TRUE-WAIT, and A1.7.3.1 partial-remainder/liveness
frozen. It closes one mechanical publisher gap: a book/side that already owns
a placement in the current response cannot receive another same-side placement
before the pending ledger is recorded. Trading economics, recovery thresholds,
FastPath, size, and portfolio limits are intentionally unchanged.
The frozen Strategy1_Research.py base remains untouched.

The original Strategy1_Research.py is left untouched so this candidate can be
A/B tested against the V4.16.2 baseline.
"""
from __future__ import annotations

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
)
from research_direct_dust_kappa import (
    DIRECT_DUST_KAPPA_VERSION,
    DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS,
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


SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_7_4_3_1"
SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_7_4_3_1"


class Strategy1_Research_Simple(Strategy1_Research):
    """V4.16.2 A1.7.4.3.1 same-book ownership overlay on A1.7.4.3.

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
        super()._log_notices(state, tick)
        ledger = self._direct_pending_ledger()
        if not ledger:
            return
        try:
            notices = (getattr(state, "notices", None) or {}).get(self.uid, []) or []
        except Exception:
            notices = []
        for notice in notices:
            phase = type(notice).__name__.upper()
            if not any(token in phase for token in ("CANCEL", "EXPIRE", "REJECT", "FAIL")):
                continue
            cid = None
            for name in ("clientOrderId", "client_order_id", "clientId", "client_id"):
                value = getattr(notice, name, None)
                if value is not None:
                    cid = value
                    break
            raw_book = getattr(notice, "bookId", getattr(notice, "book_id", None))
            try:
                bid = int(raw_book) if raw_book is not None else None
            except (TypeError, ValueError):
                bid = None
            matches = []
            for key in ledger:
                if bid is not None and int(key[0]) != bid:
                    continue
                if cid is not None and str(key[1]) != str(cid):
                    continue
                matches.append(key)
            # With neither stable book nor client id, do not guess. The bounded
            # expiry reconciler will release the reservation safely.
            if bid is None and cid is None:
                continue
            for key in matches:
                row = ledger.pop(key, None)
                if row is not None:
                    self._direct_emit_book_ownership_release(row=row, reason=f"NOTICE_{phase}")
                self._direct_pending_exposure_expired = int(
                    getattr(self, "_direct_pending_exposure_expired", 0) or 0
                ) + 1

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
        response = super().respond(state)
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

    def _direct_is_entry_quote_order(self, book_id: int, order) -> bool:
        cid = self._direct_order_client_id(order)
        return cid in {70000 + int(book_id) * 10 + 1, 70000 + int(book_id) * 10 + 2}

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

        remaining_obs = int(getattr(ev, "observations_remaining", 3) or 3)
        required_obs = int(getattr(ev, "required_observation_count", 3) or 3)
        min_size = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        # Keep acquisition size intentionally simple and conservative. Throughput
        # comes from better opportunity recall, not bigger individual positions.
        maker_size = min_size
        decision = choose_direct_execution(
            maker_lifecycle_ev=life,
            maker_current_edge_bps=current_edge_bps,
            maker_min_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS,
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
            kappa_decision = decide_kappa_safe_dust_compaction(
                net_base=net_base, min_order=min_size,
                vwap_entry=getattr(inventory, "vwap_entry", None),
                maker_close_price=maker_close_price,
                age_ticks=int(getattr(inventory, "position_ticks", 0) or 0),
                loss_floor_bps=DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS,
                eps=float(self._execution_flat_epsilon()),
            )
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
        """Reduce, but do not double-consume, a local reservation on own fill."""
        ledger = self._direct_pending_ledger()
        if not ledger:
            return
        book = getattr(event, "bookId", None)
        cid = getattr(event, "clientOrderId", None)
        qty = getattr(event, "quantity", None)
        if book is None or cid is None or qty is None:
            return
        try:
            bid = int(book)
            q = max(0.0, float(qty or 0.0))
        except (TypeError, ValueError):
            return
        if q <= 0.0:
            return
        matches = [k for k in ledger if k[0] == bid and k[1] == str(cid)]
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
                    self._direct_emit_book_ownership_release(row=released, reason="FILL_COMPLETE")

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
