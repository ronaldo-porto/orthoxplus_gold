# SPDX-License-Identifier: MIT
"""Strategy1-Direct V4.16.2 A1.5 Research candidate.

This module intentionally does *not* add another strategy layer.  It reuses the
existing V4.16.2 Research state/learning/persistence infrastructure but replaces
its hot orchestration path with the shortest useful authority chain:

    selected book -> hard safety -> LifecycleEV -> bounded Maker quality
                  -> TotalScore rank -> Maker/Taker/Skip -> final validation

For non-flat inventory the existing V4.16 PositionExitController remains the
only realization authority.

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
    DIRECT_TAKER_MIN_EV,
    DIRECT_TAKER_MIN_EDGE_BPS,
    DIRECT_TAKER_ENTRY_ENABLED,
    choose_direct_execution,
    direct_lifecycle_breakdown,
)
from research_direct_quality import (
    COLD_START_TAKER_RATE,
    DIRECT_QUALITY_VERSION,
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
    DIRECT_MAX_PRE_SUBMIT_AGE_MS,
    DIRECT_TELEMETRY_SAMPLE_TICKS,
    FastPathRow,
    cheap_priority,
    clamp_candidate_count as direct_fastpath_candidate_count,
    select_fastpath_rows,
)
from research_neutral_prediction import is_neutral_forecast, prediction_source_of
from research_position_exit import BAND_ABSOLUTE, new_exposure_allowed
from research_risk_guard import evaluate_risk_guard
from research_role_size import maker_entry_size, taker_clip_size
from research_lifecycle_ev import LifecycleCost


SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_5"
SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_5"


class Strategy1_Research_Simple(Strategy1_Research):
    """V4.16.2 state/learning with A1.5 net-downside learning + Direct FastPath.

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
      * A1.5 net-realized/LPM3-aware Maker lifecycle cost;
      * A1.5 Direct FastPath top-K screening/profile build;
      * Maker-only acquisition; PositionExitController retains Taker EXIT;
      * V4.16 PositionExitController for every non-flat position;
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
        self._direct_quality_last: dict[int, Any] = {}
        self._direct_realization_cost_last: dict[int, Any] = {}
        self._direct_quote_geometry_last: dict[int, dict[str, float]] = {}
        self._direct_fastpath_last_selected_tick: dict[int, int] = {}
        self._direct_fastpath_profile_cache: dict[int, Any] = {}
        self._direct_request_wall_started: float | None = None
        self._direct_event_pnl_before: dict[int, float] = {}
        self._direct_fastpath_screen_calls = 0
        self._direct_freshness_budget_skips = 0
        try:
            self._emit(
                "SIMPLE_CONFIG",
                force=True,
                simple_policy_version=SIMPLE_POLICY_VERSION,
                authority="DIRECT_FASTPATH>HARD_SAFETY>NET_DOWNSIDE_LIFECYCLE_EV>TOTAL_SCORE>MAKER_OR_SKIP",
                direct_economics_version=DIRECT_ECONOMICS_VERSION,
                execution_controller_version=DIRECT_EXECUTION_CONTROLLER_VERSION,
                exit_authority="POSITION_EXIT_CONTROLLER",
                separate_maintenance_authority=0,
                separate_alpha_authority=0,
                lane_execution_caps=0,
                latency_hard_gate=0,
                duplicate_adverse_hard_gate=0,
                taker_kappa_subsidy=0,
                direct_quality_version=DIRECT_QUALITY_VERSION,
                direct_execution_quality_version=DIRECT_EXECUTION_QUALITY_VERSION,
                maker_min_ev=DIRECT_MAKER_MIN_EV,
                maker_quality_max_penalty=0.03,
                maker_max_touch_improvement_bps=DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS,
                maker_max_ttl_ms=DIRECT_MAKER_MAX_TTL_MS,
                dust_exempt_cap=DIRECT_DUST_EXEMPT_CAP,
                cold_start_taker_rate=COLD_START_TAKER_RATE,
                taker_entry_min_ev=DIRECT_TAKER_MIN_EV,
                taker_entry_min_edge_bps=DIRECT_TAKER_MIN_EDGE_BPS,
                learned_taker_shortfall_cost=1,
                net_realized_shortfall_cost=1,
                kappa_lpm3_downside_cost=1,
                taker_frequency_is_badness=0,
                taker_entry_enabled=int(DIRECT_TAKER_ENTRY_ENABLED),
                direct_fastpath_version=DIRECT_FASTPATH_VERSION,
                direct_fastpath_candidate_count=DIRECT_FASTPATH_CANDIDATE_COUNT,
                direct_max_pre_submit_age_ms=DIRECT_MAX_PRE_SUBMIT_AGE_MS,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # A1.5 Maker lifecycle learning.  Learn NET realized downside, including
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
        quality = maker_quality_adjustment(
            stats=stats,
            global_stats=getattr(self, "_direct_maker_quality_global", None),
            rolling_samples=rolling_n,
            rolling_loss_rate=rolling_loss,
            rolling_realized_mean=rolling_mean,
        )
        self._direct_quality_last[int(book_id)] = quality
        return quality

    # ------------------------------------------------------------------
    # A1.5 LifecycleEV: A1.1 latency/adverse correction stays intact.  Maker
    # quality is a bounded rank deduction, not a new hard lifecycle veto.
        # ------------------------------------------------------------------
    def _research_lifecycle_entry_cost_bps(self, book_id: int, spread_bps: float) -> float:
        """A1.5 net-realized, Kappa-downside-aware Maker lifecycle cost.

        The inherited V4.16 model prices every Taker exit as fixed crossing +
        slippage + fee.  A1.3 proved this suppresses profitable Maker->Taker
        lifecycles.  Direct A1.5 prices net realized negative shortfall and a cubic downside
        severity proxy; profitable Taker exits still contribute zero shortfall.
        """
        bid = int(book_id)
        stats = (getattr(self, "_direct_maker_quality_by_book", {}) or {}).get(bid)
        global_stats = getattr(self, "_direct_maker_quality_global", None)
        taker_fee = float(self._research_live_fee_bps(bid, is_maker=False))
        maker_fee = float(self._research_live_fee_bps(bid, is_maker=True))
        holding = float(getattr(self, "research_lifecycle_holding_bps", 0.50) or 0.50)
        estimate = maker_realization_cost_estimate(
            stats=stats,
            global_stats=global_stats,
            taker_fee_bps=taker_fee,
            holding_risk_bps=holding,
        )
        self._direct_realization_cost_last[bid] = estimate
        # Preserve the inherited telemetry contract with a LifecycleCost object.
        # Crossing/slippage are intentionally zero here because realized gross
        # shortfall already learns their actual price impact from completed RTs.
        cost = LifecycleCost(
            maker_entry_fee_bps=maker_fee,
            taker_fee_bps=max(0.0, taker_fee),
            expected_exit_fee_bps=float(estimate.expected_taker_fee_bps),
            expected_cross_bps=0.0,
            expected_slippage_bps=0.0,
            holding_risk_bps=float(estimate.holding_risk_bps),
            taker_exit_probability=float(estimate.effective_taker_exit_rate),
            expected_future_taker_cost_bps=(
                float(estimate.expected_negative_shortfall_bps)
                + float(estimate.expected_taker_fee_bps)
            ),
        )
        self._research_lifecycle_cost_last[bid] = cost
        return float(cost.total_bps)

    def _research_score_ev_for_book(self, book_id: int, expected_alpha: float, mem):
        base = super()._research_score_ev_for_book(book_id, expected_alpha, mem)
        learned_cost = (getattr(self, "_direct_realization_cost_last", {}) or {}).get(int(book_id))
        if learned_cost is not None:
            # Make RANK telemetry describe the economics actually used in A1.5
            # rather than the inherited fixed Taker-cost diagnostic fields.
            base = replace(
                base,
                fees_bps=float(learned_cost.total_cost_bps),
                taker_prob_effective=float(learned_cost.effective_taker_exit_rate),
                taker_prob_excess=max(
                    0.0,
                    float(learned_cost.effective_taker_exit_rate)
                    - float(learned_cost.prior_taker_exit_rate),
                ),
                expected_taker_cost=float(learned_cost.total_cost_bps),
                expected_future_taker_cost_bps=(
                    float(learned_cost.expected_negative_shortfall_bps)
                    + float(learned_cost.expected_taker_fee_bps)
                ),
                expected_taker_exit_fee_bps=float(learned_cost.expected_taker_fee_bps),
                expected_crossing_bps=0.0,
                expected_slippage_bps=0.0,
            )
        direct = direct_lifecycle_breakdown(
            base,
            min_trading_ev=float(getattr(self, "research_score_ev_min_trading", 0.0) or 0.0),
        )
        quality = self._direct_quality_for_book(int(book_id))
        final = float(getattr(direct, "final_score", float("-inf")))
        if bool(getattr(direct, "eligible", False)) and math.isfinite(final):
            # Downrank repeated poor Maker lifecycles without blocking an
            # independently positive directional Taker opportunity.
            direct = replace(direct, final_score=final - float(quality.total_penalty))
        return direct

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        # A1.5 measures the actual wall-clock age of the decision path. New Maker
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
        """Single entry authority: hard safety -> LifecycleEV -> Maker/Taker/Skip."""
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

        if not bool(getattr(ev, "eligible", False)):
            return 0
        life = float(getattr(ev, "lifecycle_ev", getattr(ev, "trading_ev", -1.0)) or 0.0)
        if not math.isfinite(life) or life < 0.0:
            return 0

        p_fill = float(getattr(ev, "actionable_fill_prob", 0.50) or 0.50)
        remaining_obs = int(getattr(ev, "observations_remaining", 3) or 3)
        required_obs = int(getattr(ev, "required_observation_count", 3) or 3)
        min_size = float(getattr(self, "_research_exchange_min_order_size", 0.25) or 0.25)
        inv_headroom = max(
            0.0,
            float(getattr(self, "max_inventory_base", 1.2) or 1.2)
            - abs(float(getattr(inventory, "net_base", 0.0) or 0.0)),
        )
        maker_role = maker_entry_size(
            lifecycle_ev=life,
            p_fill=p_fill,
            observations_remaining=remaining_obs,
            min_order=min_size,
            inventory_headroom=inv_headroom,
            volume_headroom=self._research_volume_cap_headroom(state, book_id),
        )
        taker_role = taker_clip_size(
            inventory_qty=max(min_size, 0.25),
            min_order=min_size,
        )
        # A1.5 preserves separate execution telemetry but disables Taker entry.  Maker uses the already fill-weighted
        # Direct LifecycleEV.  Taker must independently earn its actual half-spread
        # crossing + fee + slippage from the raw directional forecast.  Kappa is
        # upstream ranking only and cannot rescue negative Taker economics.
        quality = (getattr(self, "_direct_quality_last", {}) or {}).get(int(book_id))
        if quality is None:
            quality = self._direct_quality_for_book(int(book_id))
        maker_lifecycle_ev = life - float(getattr(quality, "total_penalty", 0.0) or 0.0)
        decision = choose_direct_execution(
            maker_lifecycle_ev=maker_lifecycle_ev,
            directional_score=float(getattr(prediction, "score", 0.0) or 0.0),
            crossing_bps=max(0.0, float(getattr(ev, "spread_capture_bps", 0.0) or 0.0)),
            maker_size=float(maker_role.size),
            taker_clip=float(taker_role.size or min_size),
            neutral_fallback=is_neutral_forecast(prediction),
            maker_fee_bps=float(getattr(ev, "maker_fee_bps", 0.0) or 0.0),
            taker_fee_bps=float(getattr(ev, "taker_fee_bps", 0.0) or 0.0),
            slippage_bps=float(getattr(self, "research_lifecycle_slippage_bps", 0.75) or 0.75),
            expected_markout_bps=float(getattr(ev, "expected_markout_bps", 0.0) or 0.0),
        )

        tick = int(getattr(self, "_tick", 0) or 0)
        if decision.action != EXEC_ACTION_SKIP or tick <= 2 or tick % DIRECT_TELEMETRY_SAMPLE_TICKS == 0:
            try:
                self._emit(
                    "ENTRY_DECISION",
                    force=True,
                    tick=tick,
                    book=int(book_id),
                    lane="DIRECT",
                    safe=1,
                    lifecycle_ev=life,
                    maker_lifecycle_ev_adjusted=maker_lifecycle_ev,
                    **quality.as_log(),
                    **(
                        (getattr(self, "_direct_realization_cost_last", {}) or {})
                        .get(int(book_id))
                        .as_log()
                        if (getattr(self, "_direct_realization_cost_last", {}) or {}).get(int(book_id)) is not None
                        else {}
                    ),
                    total_score_value=float(getattr(ev, "total_score_component", 0.0) or 0.0),
                    prediction_source=prediction_source_of(prediction),
                    neutral_fallback_used=int(is_neutral_forecast(prediction)),
                    direct_mode=1,
                    **decision.as_log(),
                )
            except Exception:
                pass

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

        # Maker is the only remaining action.  No legacy quote-level economics
        # are allowed to veto it after LifecycleEV + execution utility passed.
        placed = self._simple_place_maker(
            response,
            state,
            book_id,
            book,
            profile,
            prediction,
            inventory,
            regime_params,
            float(decision.maker_size or maker_role.size),
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
        quality_cache = getattr(self, "_direct_quality_last", {}) or {}
        last_selected = getattr(self, "_direct_fastpath_last_selected_tick", {}) or {}

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
            cached_profile = profile_cache.get(bid)
            cached_alpha = float(getattr(cached_profile, "alpha_rank", 0.0) or 0.0)
            cached_pnl = float(getattr(cached_profile, "realized_pnl", 0.0) or 0.0)
            quality = quality_cache.get(bid)
            quality_penalty = float(getattr(quality, "total_penalty", 0.0) or 0.0)
            raw_rows.append((
                bid, remaining, qualified, has_inv, is_dust, spread_bps,
                cached_alpha, cached_pnl, quality_penalty,
            ))

        target = max(1, int(getattr(self, "research_kappa_completion_target", 80) or 80))
        score_deficit = max(0, target - qualified_count)
        rows: list[FastPathRow] = []
        for (bid, remaining, qualified, has_inv, is_dust, spread_bps,
             cached_alpha, cached_pnl, quality_penalty) in raw_rows:
            stale_ticks = max(0, tick - int(last_selected.get(bid, 0) or 0))
            priority = cheap_priority(
                observations_remaining=remaining, qualified=qualified,
                spread_bps=spread_bps, cached_alpha_rank=cached_alpha,
                cached_realized_pnl=cached_pnl, quality_penalty=quality_penalty,
                ticks_since_selected=stale_ticks, score_deficit=score_deficit,
            )
            rows.append(FastPathRow(
                book_id=bid, priority=priority, observations_remaining=remaining,
                qualified=qualified, has_inventory=has_inv, is_dust=is_dust,
            ))

        configured = direct_fastpath_candidate_count(
            getattr(self, "research_candidate_count", DIRECT_FASTPATH_CANDIDATE_COUNT)
        )
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
            "direct_effective_open_books": int(effective_total_open_books(
                actual_nonflat=actual_nonflat, dust_nonflat=dust_nonflat,
            )),
            "direct_qualified_count": int(qualified_count),
            "direct_score_deficit": int(score_deficit),
        }
        if tick <= 2 or tick % DIRECT_TELEMETRY_SAMPLE_TICKS == 0:
            try:
                self._emit(
                    "DIRECT_FASTPATH", force=True, tick=tick,
                    direct_fastpath_version=DIRECT_FASTPATH_VERSION, universe=len(rows),
                    selected=len(selected), qualified=qualified_count, score_deficit=score_deficit,
                    inventory_books=actual_nonflat, dust_books=dust_nonflat,
                )
            except Exception:
                pass
        return result

    def select_books_for_trading(self, state, predictions):
        """Build expensive profiles only for the bounded Direct FastPath set."""
        started = time.perf_counter()
        screen = getattr(self, "_research_last_screen", None)
        selected_ids = {int(x) for x in (getattr(screen, "selected", None) or [])}
        if not selected_ids:
            selected_ids = {int(x) for x in (predictions or {}).keys()}
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
        direct = raw.get("direct_maker_quality_a1_5")
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
            try:
                self._emit(
                    "DIRECT_QUALITY_RESTORE", force=True,
                    tick=getattr(self, "_tick", None), books=len(restored),
                    global_samples=int(self._direct_maker_quality_global.count),
                    direct_quality_version=DIRECT_QUALITY_VERSION,
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
            payload["direct_maker_quality_a1_5"] = {
                "version": DIRECT_QUALITY_VERSION,
                "global": getattr(self, "_direct_maker_quality_global", MakerLifecycleStats()).as_state(),
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
        self._direct_quality_last = {}
        self._direct_realization_cost_last = {}

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
        effective_open_now = effective_total_open_books(
            actual_nonflat=open_now, dust_nonflat=dust_now,
        )
        active_now = int(diag.get("active_nonflat_inventory", 0) or 0)
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
