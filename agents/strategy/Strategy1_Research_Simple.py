# SPDX-License-Identifier: MIT
"""Strategy1-Direct V4.16.2 A1.6.2 Final Liveness Closure Research candidate.

This module intentionally does *not* add another strategy layer.  It reuses the
existing V4.16.2 Research state/learning/persistence infrastructure but replaces
its hot orchestration path with the shortest useful authority chain:

    128-book observable scan -> current spread/fee/Kappa rank -> deep top-K
                  -> hard safety -> current Maker edge -> Maker/Skip -> final validation

For non-flat inventory A1.6.2 reuses the inherited placement plumbing but
substitutes a simple observable Maker/Wait/risk-Taker decision for this overlay only.

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
    DIRECT_MAKER_MAX_TTL_MS,
    cap_maker_quote_geometry,
    direct_maker_expiry_ns,
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
from research_position_exit import BAND_ABSOLUTE, new_exposure_allowed
from research_risk_guard import evaluate_risk_guard


SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_6_2"
SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_6_2"


class Strategy1_Research_Simple(Strategy1_Research):
    """V4.16.2 safety/session state with A1.6.2 observable trade authority + final liveness closure.

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
      * A1.6 observable Maker/Wait/Taker exit authority for non-flat inventory;
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
                maker_min_ev=DIRECT_MAKER_MIN_EV,
                maker_min_edge_bps=DIRECT_MAKER_MIN_EDGE_BPS,
                maker_quality_max_penalty=0.0,
                migrated_quality_initial_weight=MIGRATED_QUALITY_INITIAL_WEIGHT,
                migrated_quality_full_weight_samples=MIGRATED_QUALITY_FULL_WEIGHT_SAMPLES,
                migrated_quality_global_full_weight_samples=MIGRATED_QUALITY_GLOBAL_FULL_WEIGHT_SAMPLES,
                maker_max_touch_improvement_bps=DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS,
                maker_max_ttl_ms=DIRECT_MAKER_MAX_TTL_MS,
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
                direct_max_pre_submit_age_ms=DIRECT_MAX_PRE_SUBMIT_AGE_MS,
                direct_liveness_version="direct_liveness_v4_16_2_a1_6_2",
                dust_fastpath_forced=0,
                direct_dust_compaction=1,
                placement_only_final_validation=1,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # A1.5.1 Maker lifecycle learning.  Learn NET realized downside, including
    # partial reductions and fees, rather than gross entry-to-final-price drift.
    # ------------------------------------------------------------------
    def onTrade(self, event, validator: str | None = None) -> None:
        book_id = getattr(event, "bookId", None)
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

    def _research_on_own_fill(
        self, *, event, book_id: int, before: float, after: float,
        kappa_before: int, kappa_after: int, is_maker: bool,
    ) -> None:
        super()._research_on_own_fill(
            event=event, book_id=book_id, before=before, after=after,
            kappa_before=kappa_before, kappa_after=kappa_after, is_maker=is_maker,
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

    def _research_apply_unified_exit(self, legacy, **kwargs):
        """Use A1.6 observable exit chooser without mutating frozen Research code.

        Strategy1_Research imports ``choose_position_exit`` at module scope.
        Temporarily substitute the Direct pure chooser only for this call, then
        restore the original symbol immediately.
        """
        module = importlib.import_module("Strategy1_Research")
        original = getattr(module, "choose_position_exit")
        setattr(module, "choose_position_exit", choose_observable_position_exit)
        try:
            return super()._research_apply_unified_exit(legacy, **kwargs)
        finally:
            setattr(module, "choose_position_exit", original)

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        # A1.6 preserves the A1.5 freshness-budget measurement. New Maker
        # exposure is not submitted after the freshness budget is already spent.
        self._direct_request_wall_started = time.perf_counter()
        return super().respond(state)

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

        request_started = getattr(self, "_direct_request_wall_started", None)
        if request_started is not None:
            pre_submit_age_ms = (time.perf_counter() - float(request_started)) * 1000.0
            if pre_submit_age_ms > DIRECT_MAX_PRE_SUBMIT_AGE_MS:
                self._direct_freshness_budget_skips = int(
                    getattr(self, "_direct_freshness_budget_skips", 0) or 0
                ) + 1
                tick = int(getattr(self, "_tick", 0) or 0)
                if tick <= 2 or tick % DIRECT_TELEMETRY_SAMPLE_TICKS == 0:
                    try:
                        self._emit(
                            "DIRECT_FRESHNESS_SKIP", force=True, tick=tick, book=int(book_id),
                            pre_submit_age_ms=float(pre_submit_age_ms),
                            max_pre_submit_age_ms=DIRECT_MAX_PRE_SUBMIT_AGE_MS,
                        )
                    except Exception:
                        pass
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
        expiry_ns = direct_maker_expiry_ns(int(self.mm_expiry_period))
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
                    instructions=len(getattr(response, "instructions", None) or []) - before_ix,
                )
            except Exception:
                pass
        return placed

    def _research_final_validate_instructions(self, response, state) -> None:
        """Placement-only validation with dust-aware total-open accounting.

        A1.6.2 closes the split-authority bug exposed by the 2,381-tick run:
        Direct pre-admission excluded sub-minimum dust from productive open-book
        capacity, while the inherited final validator counted every non-flat dust
        book toward ``research_max_total_open_books`` and rejected fresh entries.

        Exact dust BASE remains in absolute-exposure accounting. Only the total
        open-book cap is temporarily expanded by the current dust-book count while
        placement instructions are passed through the authoritative validator.
        Non-placement instructions (e.g. CANCEL_ORDERS) remain untouched.
        """
        original = list(getattr(response, "instructions", None) or [])
        if not original:
            return

        def _kind(instruction) -> str:
            value = getattr(instruction, "type", None)
            if value is None and isinstance(instruction, dict):
                value = instruction.get("type")
            return str(value or "")

        placement_types = {"PLACE_ORDER_LIMIT", "PLACE_ORDER_MARKET"}
        placements = [item for item in original if _kind(item) in placement_types]
        if not placements:
            return

        try:
            response.instructions[:] = placements
        except Exception:
            object.__setattr__(response, "instructions", placements)

        base_cap = int(getattr(self, "research_max_total_open_books", 8) or 8)
        dust_books = self._direct_dust_count(state)
        self.research_max_total_open_books = base_cap + int(dust_books)
        try:
            super()._research_final_validate_instructions(response, state)
        finally:
            self.research_max_total_open_books = base_cap

        validated = list(getattr(response, "instructions", None) or [])
        validated_ids = {id(item) for item in validated}

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
        }

        profile_by_id = {int(p.book_id): p for p in (getattr(selection, "profiles", None) or [])}
        screen = getattr(self, "_research_last_screen", None)
        selected_ids = {int(x) for x in (getattr(screen, "selected", None) or [])}
        if not selected_ids:
            selected_ids = {int(x) for x in (predictions or {}).keys()}

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
        abs_slots = max(0, int(math.floor((max_abs - abs_now + 1e-12) / min_size)))
        portfolio_slots = max(
            0, min(abs_slots, max_open - effective_open_now, max_active - active_now)
        )
        stats["portfolio_open_slots"] = int(portfolio_slots)

        # One flat-entry path.  No maintenance branch and no separate alpha branch.
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

        self._last_mm_stats = stats
        self._research_timing["build_orders_ms"] = (time.perf_counter() - started) * 1000.0
        return stats


if __name__ == "__main__":
    launch(Strategy1_Research_Simple)
