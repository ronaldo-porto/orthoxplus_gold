# SPDX-FileCopyrightText: 2026
# SPDX-License-Identifier: MIT
"""Strategy1_Debug — observability-only wrapper for Strategy1.

This agent intentionally inherits Strategy1 and leaves its trading decisions in the
parent class. It adds five local-test diagnostics:

1. A configurable debug switch and book/tick filters.
2. One structured decision record per book.
3. Explicit no-action/skip reason codes.
4. Stage-level latency measurements in milliseconds.
5. Order submission and simulator-notice lifecycle records.

Place this file beside Strategy1.py and DetailedTemplateAgent.py.

Example local launch:

    STRATEGY1_DEBUG=1 \
    STRATEGY1_DEBUG_EVERY_N=1 \
    STRATEGY1_DEBUG_JSONL=1 \
    python Strategy1_Debug.py --port 8888 --agent_id 0 \
      --params enable_mm_strategy=1 verbose_log=0 log_every_n=100

Optional environment variables:

    STRATEGY1_DEBUG=0|1             Enable diagnostics (default: 1 for this agent)
    STRATEGY1_DEBUG_EVERY_N=N       Emit decision/timing records every N ticks
    STRATEGY1_DEBUG_BOOK=BOOK_ID    Emit book-specific records only for this book
    STRATEGY1_DEBUG_JSONL=0|1       Also write JSONL records to disk
    STRATEGY1_DEBUG_DIR=PATH        JSONL output directory
    STRATEGY1_DEBUG_SUMMARY_N=N     Emit rolling summary every N ticks

The same values can be passed as agent params using:

    debug_enabled, debug_every_n, debug_book_id, debug_jsonl,
    debug_output_dir, debug_summary_every_n
"""

from __future__ import annotations

import atexit
import json
import math
import os
import sys
import time
from collections import Counter
from enum import Enum
from typing import Any, Callable, TypeVar

import bittensor as bt

_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

from taos.common.agents import launch
from taos.im.protocol import FinanceAgentResponse, MarketSimulationStateUpdate

from Strategy1 import (
    BookArchetype,
    BookProfile,
    BookSelection,
    DirectionForecast,
    InventorySnapshot,
    MarketRegime,
    RegimeParamSet,
    Strategy1,
)

T = TypeVar("T")


class DebugReason:
    """Stable reason codes for grep, jq, and automated comparisons."""

    QUOTED = "QUOTED"
    MANAGED_INVENTORY = "MANAGED_INVENTORY"
    MAINTENANCE_ORDER = "MAINTENANCE_ORDER"
    ALPHA_ORDER = "ALPHA_ORDER"

    NO_BOOK_SIDES = "NO_BOOK_SIDES"
    NO_PROFILE = "NO_PROFILE"
    AVOID_LIST = "AVOID_LIST"
    NO_PREDICTION = "NO_PREDICTION"
    GRACE_PERIOD = "GRACE_PERIOD"

    MANAGEMENT_LIMIT = "MANAGEMENT_LIMIT"
    MANAGE_ORDER_GATE = "MANAGE_ORDER_GATE"
    MAINT_INVENTORY_NONFLAT = "MAINT_INVENTORY_NONFLAT"
    MAINT_ARCHETYPE_BLOCK = "MAINT_ARCHETYPE_BLOCK"
    MAINT_ORDER_GATE = "MAINT_ORDER_GATE"

    TOXIC_BOOK = "TOXIC_BOOK"
    QUOTE_DISABLED = "QUOTE_DISABLED"
    TOXIC_REGIME = "TOXIC_REGIME"
    INACTIVE_TIER = "INACTIVE_TIER"
    LOW_EXPECTED_ALPHA = "LOW_EXPECTED_ALPHA"
    MM_CANDIDATE_LIMIT = "MM_CANDIDATE_LIMIT"

    MAX_INVENTORY = "MAX_INVENTORY"
    INVALID_QUOTE_PRICES = "INVALID_QUOTE_PRICES"
    ZERO_ORDER_SIZE = "ZERO_ORDER_SIZE"
    VOLUME_CAP = "VOLUME_CAP"
    NON_POSITIVE_EDGE = "NON_POSITIVE_EDGE"
    NEGATIVE_EXPECTED_PNL = "NEGATIVE_EXPECTED_PNL"
    LOW_FILL_PROBABILITY = "LOW_FILL_PROBABILITY"
    INSTRUCTION_LIMIT = "INSTRUCTION_LIMIT"
    INSUFFICIENT_BALANCE = "INSUFFICIENT_BALANCE"
    QUOTE_ORDER_GATE = "QUOTE_ORDER_GATE"
    NO_ACTION = "NO_ACTION"


class Strategy1_Debug(Strategy1):
    """Strategy1 with isolated, structured local-test diagnostics."""

    def initialize(self) -> None:
        super().initialize()

        cfg = self.config
        self.debug_enabled = self._env_bool(
            "STRATEGY1_DEBUG",
            self._as_bool(getattr(cfg, "debug_enabled", True)),
        )
        self.debug_every_n = max(
            1,
            self._env_int(
                "STRATEGY1_DEBUG_EVERY_N",
                int(getattr(cfg, "debug_every_n", 1)),
            ),
        )
        self.debug_summary_every_n = max(
            1,
            self._env_int(
                "STRATEGY1_DEBUG_SUMMARY_N",
                int(getattr(cfg, "debug_summary_every_n", 100)),
            ),
        )
        self.debug_book_id = self._env_int(
            "STRATEGY1_DEBUG_BOOK",
            int(getattr(cfg, "debug_book_id", -1)),
        )
        self.debug_jsonl = self._env_bool(
            "STRATEGY1_DEBUG_JSONL",
            self._as_bool(getattr(cfg, "debug_jsonl", True)),
        )

        configured_dir = str(getattr(cfg, "debug_output_dir", "") or "")
        env_dir = os.getenv("STRATEGY1_DEBUG_DIR", "").strip()
        self.debug_output_dir = env_dir or configured_dir or os.path.join(
            self.output_dir, "strategy1_debug"
        )

        self._debug_file = None
        self._debug_stage_ms: dict[str, float] = {}
        self._debug_book_records: dict[int, dict[str, Any]] = {}
        self._debug_current_state: MarketSimulationStateUpdate | None = None
        self._debug_current_regime: MarketRegime | None = None
        self._debug_reason_counts: Counter[str] = Counter()
        self._debug_event_counts: Counter[str] = Counter()
        self._debug_latency_sum_ms: Counter[str] = Counter()
        self._debug_latency_max_ms: Counter[str] = Counter()
        self._debug_response_count = 0

        if self.debug_enabled and self.debug_jsonl:
            try:
                os.makedirs(self.debug_output_dir, exist_ok=True)
                path = os.path.join(
                    self.debug_output_dir,
                    f"strategy1_debug_agent_{self.uid}.jsonl",
                )
                self._debug_file = open(path, "a", encoding="utf-8", buffering=1)
                atexit.register(self._close_debug_file)
            except OSError as exc:
                self._debug_file = None
                bt.logging.warning(f"[S1DBG] cannot open JSONL output: {exc}")

        self._emit(
            "DEBUG_CONFIG",
            force=True,
            enabled=self.debug_enabled,
            every_n=self.debug_every_n,
            summary_every_n=self.debug_summary_every_n,
            book_filter=self.debug_book_id,
            jsonl=self.debug_jsonl,
            output_dir=self.debug_output_dir,
        )

    # ------------------------------------------------------------------
    # Main lifecycle and stage timing
    # ------------------------------------------------------------------

    def handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        if not self.debug_enabled:
            return super().handle(state)

        # Notices are logged before update() consumes them. This captures order-created,
        # rejection, cancellation, expiry, trade, and any future simulator notice types.
        next_tick = self._tick + 1
        self._log_notices(state, next_tick)

        t0 = time.perf_counter()
        self.update(state)
        update_ms = self._elapsed_ms(t0)

        t1 = time.perf_counter()
        response = self.respond(state)
        respond_ms = self._elapsed_ms(t1)

        t2 = time.perf_counter()
        self.report(state, response)
        report_ms = self._elapsed_ms(t2)

        total_ms = update_ms + respond_ms + report_ms
        self._record_latency("update_ms", update_ms)
        self._record_latency("respond_ms", respond_ms)
        self._record_latency("report_ms", report_ms)
        self._record_latency("total_ms", total_ms)
        self._debug_response_count += 1

        if self._should_emit_tick(self._tick):
            self._emit(
                "TIMING",
                tick=self._tick,
                timestamp=getattr(state, "timestamp", None),
                update_ms=round(update_ms, 4),
                respond_ms=round(respond_ms, 4),
                report_ms=round(report_ms, 4),
                total_ms=round(total_ms, 4),
                internal_ms={
                    key: round(value, 4)
                    for key, value in sorted(self._debug_stage_ms.items())
                },
                notices=len((getattr(state, "notices", None) or {}).get(self.uid, [])),
                instructions=len(getattr(response, "instructions", []) or []),
            )

        if self._tick == 1 or self._tick % self.debug_summary_every_n == 0:
            self._emit_run_summary(state)

        return response

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        if not self.debug_enabled:
            return super().respond(state)

        self._debug_stage_ms = {}
        self._debug_book_records = {}
        self._debug_current_state = state
        self._debug_current_regime = None

        started = time.perf_counter()
        try:
            response = super().respond(state)
        except Exception as exc:
            self._emit(
                "ERROR",
                force=True,
                tick=self._tick,
                stage="respond",
                error_type=type(exc).__name__,
                error=str(exc),
            )
            raise
        finally:
            self._debug_stage_ms["respond_parent_ms"] = self._elapsed_ms(started)

        self._log_submitted_instructions(response, state)
        self._debug_current_state = None
        self._debug_current_regime = None
        return response

    def _predict_all_books(self, state: MarketSimulationStateUpdate):
        return self._timed("predict_all_books_ms", super()._predict_all_books, state)

    def select_books_for_trading(self, state, predictions):
        return self._timed(
            "select_books_ms",
            super().select_books_for_trading,
            state,
            predictions,
        )

    def classify_market_regime_from_profiles(self, profiles, predictions, selection):
        regime = self._timed(
            "classify_regime_ms",
            super().classify_market_regime_from_profiles,
            profiles,
            predictions,
            selection,
        )
        if self.debug_enabled:
            self._debug_current_regime = regime
        return regime

    def build_mm_strategy_instructions(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        selection: BookSelection,
        predictions: dict[int, DirectionForecast],
        regime: MarketRegime,
        collect_archetypes: bool = True,
    ) -> dict:
        if not self.debug_enabled:
            return super().build_mm_strategy_instructions(
                response,
                state,
                selection,
                predictions,
                regime,
                collect_archetypes=collect_archetypes,
            )

        self._debug_current_regime = regime
        started = time.perf_counter()
        stats = super().build_mm_strategy_instructions(
            response,
            state,
            selection,
            predictions,
            regime,
            collect_archetypes=collect_archetypes,
        )
        self._debug_stage_ms["build_mm_ms"] = self._elapsed_ms(started)

        self._finalize_book_decisions(
            response=response,
            state=state,
            selection=selection,
            predictions=predictions,
            regime=regime,
        )
        return stats

    # ------------------------------------------------------------------
    # Low-impact observation hooks called by Strategy1's original logic
    # ------------------------------------------------------------------

    def _net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        inventory = super()._net_inventory(book_id, mid)
        if self.debug_enabled:
            record = self._book_record(book_id)
            record["inventory"] = {
                "net_base": inventory.net_base,
                "ratio": inventory.inventory_ratio,
                "band": inventory.band,
                "vwap_entry": inventory.vwap_entry,
                "unrealized_bps": inventory.unrealized_bps,
                "position_ticks": inventory.position_ticks,
                "reason": inventory.reason,
            }
        return inventory

    def classify_book_archetype(
        self,
        profile: BookProfile,
        regime: MarketRegime,
    ) -> BookArchetype:
        archetype = super().classify_book_archetype(profile, regime)
        if self.debug_enabled:
            self._book_record(profile.book_id)["archetype"] = archetype
        return archetype

    def is_toxic_book(
        self,
        book_id: int,
        profile: BookProfile,
        archetype: BookArchetype,
    ) -> bool:
        toxic = super().is_toxic_book(book_id, profile, archetype)
        if self.debug_enabled:
            self._book_record(book_id)["toxic"] = toxic
        return toxic

    def expected_alpha_score(
        self,
        profile: BookProfile,
        prediction: DirectionForecast,
        fill_est,
        mem,
        book_id: int,
        now: int,
    ) -> float:
        score = super().expected_alpha_score(
            profile,
            prediction,
            fill_est,
            mem,
            book_id,
            now,
        )
        if self.debug_enabled:
            record = self._book_record(book_id)
            record["expected_alpha"] = score
            record["fill_buy"] = getattr(fill_est, "buy", None)
            record["fill_sell"] = getattr(fill_est, "sell", None)
        return score

    def _manage_inventory(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        book,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ) -> int:
        if not self.debug_enabled:
            return super()._manage_inventory(
                response,
                state,
                book_id,
                book,
                inventory,
                regime_params,
                regime,
                archetype,
            )

        before = len(getattr(response, "instructions", []) or [])
        started = time.perf_counter()
        placed = super()._manage_inventory(
            response,
            state,
            book_id,
            book,
            inventory,
            regime_params,
            regime,
            archetype,
        )
        elapsed = self._elapsed_ms(started)
        record = self._book_record(book_id)
        record["manage_ms"] = elapsed
        record["action"] = "MANAGE" if placed else "SKIP"
        record["reason"] = (
            DebugReason.MANAGED_INVENTORY if placed else DebugReason.MANAGE_ORDER_GATE
        )
        record["instructions_added"] = (
            len(getattr(response, "instructions", []) or []) - before
        )
        return placed

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
        if not self.debug_enabled:
            return super()._place_skewed_quotes(
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

        diagnosis = self._diagnose_quote_setup(
            response=response,
            state=state,
            book_id=book_id,
            book=book,
            profile=profile,
            prediction=prediction,
            inventory=inventory,
            regime_params=regime_params,
            size=size,
            edge_bias=edge_bias,
        )
        before = len(getattr(response, "instructions", []) or [])
        started = time.perf_counter()
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
        elapsed = self._elapsed_ms(started)

        record = self._book_record(book_id)
        record.update(diagnosis)
        record["quote_ms"] = elapsed
        record["instructions_added"] = (
            len(getattr(response, "instructions", []) or []) - before
        )
        if placed:
            record["action"] = "QUOTE"
            record["reason"] = DebugReason.QUOTED
        else:
            record["action"] = "SKIP"
            record["reason"] = diagnosis.get("gate_reason", DebugReason.QUOTE_ORDER_GATE)
        return placed

    def _place_directional_round_trip(self, *args, **kwargs) -> int:
        placed = super()._place_directional_round_trip(*args, **kwargs)
        if self.debug_enabled:
            book_id = kwargs.get("book_id")
            if book_id is None and len(args) >= 3:
                book_id = args[2]
            if isinstance(book_id, int):
                record = self._book_record(book_id)
                if placed:
                    record["action"] = "ALPHA"
                    record["reason"] = DebugReason.ALPHA_ORDER
        return placed

    def onTrade(self, event, validator: str | None = None) -> None:
        book_id = self._get(event, "bookId", "book_id")
        net_before = None
        if isinstance(book_id, int):
            net_before = self._position_tracker_snapshot(book_id).net_qty

        super().onTrade(event, validator)

        if not self.debug_enabled or not self._book_matches(book_id):
            return
        net_after = None
        if isinstance(book_id, int):
            net_after = self._position_tracker_snapshot(book_id).net_qty
        self._debug_event_counts["TRADE_FILL"] += 1
        self._emit(
            "ORDER_LIFECYCLE",
            tick=self._tick,
            phase="TRADE_FILL",
            book_id=book_id,
            event=self._event_payload(event),
            net_before=net_before,
            net_after=net_after,
        )

    # ------------------------------------------------------------------
    # Decision finalization and quote-gate diagnostics
    # ------------------------------------------------------------------

    def _finalize_book_decisions(
        self,
        *,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        selection: BookSelection,
        predictions: dict[int, DirectionForecast],
        regime: MarketRegime,
    ) -> None:
        if not self._should_emit_tick(self._tick):
            return

        profile_by_id = {profile.book_id: profile for profile in selection.profiles}
        avoid_set = set(selection.avoid_books)
        maintenance_set = set(
            self._schedule_maintenance_books(
                selection,
                state.timestamp,
                limit=self.max_maintenance_books_per_tick,
            )
        )
        instruction_counts = self._instruction_counts_by_book(response)
        regime_params = self.get_regime_params(regime)

        for book_id, book in state.books.items():
            if not self._book_matches(book_id):
                continue

            record = self._book_record(book_id)
            profile = profile_by_id.get(book_id)
            prediction = predictions.get(book_id)
            record["instructions"] = instruction_counts.get(book_id, 0)

            if not getattr(book, "bids", None) or not getattr(book, "asks", None):
                record.setdefault("action", "SKIP")
                record.setdefault("reason", DebugReason.NO_BOOK_SIDES)
            elif profile is None:
                record.setdefault("action", "SKIP")
                record.setdefault("reason", DebugReason.NO_PROFILE)
            elif book_id in avoid_set:
                record.setdefault("action", "SKIP")
                record.setdefault("reason", DebugReason.AVOID_LIST)
            elif prediction is None:
                record.setdefault("action", "SKIP")
                record.setdefault("reason", DebugReason.NO_PREDICTION)
            else:
                self._complete_trading_reason(
                    record=record,
                    book_id=book_id,
                    profile=profile,
                    prediction=prediction,
                    maintenance_set=maintenance_set,
                    regime=regime,
                    regime_params=regime_params,
                    instruction_count=instruction_counts.get(book_id, 0),
                )

            self._emit_book_decision(state, regime, book_id, book, profile, prediction, record)

    def _complete_trading_reason(
        self,
        *,
        record: dict[str, Any],
        book_id: int,
        profile: BookProfile,
        prediction: DirectionForecast,
        maintenance_set: set[int],
        regime: MarketRegime,
        regime_params: RegimeParamSet,
        instruction_count: int,
    ) -> None:
        if record.get("reason"):
            return

        inventory = record.get("inventory", {})
        band = inventory.get("band", "FLAT")
        archetype = record.get("archetype")
        toxic = bool(record.get("toxic", False))

        if band != "FLAT" and self._inventory_record_needs_management(inventory):
            record["action"] = "SKIP"
            record["reason"] = DebugReason.MANAGEMENT_LIMIT
            return

        if book_id in maintenance_set:
            if band != "FLAT":
                reason = DebugReason.MAINT_INVENTORY_NONFLAT
            elif archetype is not None and not self._maintenance_allowed(profile, archetype):
                reason = DebugReason.MAINT_ARCHETYPE_BLOCK
            elif toxic and regime.scoring_overlay != "SCORING_PRESSURE":
                reason = DebugReason.TOXIC_BOOK
            elif instruction_count:
                record["action"] = "MAINTENANCE"
                record["reason"] = DebugReason.MAINTENANCE_ORDER
                return
            else:
                reason = DebugReason.MAINT_ORDER_GATE
            record["action"] = "SKIP"
            record["reason"] = reason
            return

        if toxic:
            reason = DebugReason.TOXIC_BOOK
        elif archetype is not None:
            merged = self.merge_regime_and_archetype_params(regime_params, archetype)
            if not merged.quote_enabled:
                reason = DebugReason.QUOTE_DISABLED
            elif archetype in ("TOXIC_BOOK", "STRESSED") and regime.mode in (
                "CHOP",
                "STRESSED",
            ):
                reason = DebugReason.TOXIC_REGIME
            elif self.mm_skip_inactive_tier and profile.tier == "INACTIVE":
                reason = DebugReason.INACTIVE_TIER
            elif record.get("expected_alpha", float("-inf")) < self.min_expected_alpha:
                reason = DebugReason.LOW_EXPECTED_ALPHA
            elif instruction_count:
                record["action"] = "ORDER"
                record["reason"] = DebugReason.ALPHA_ORDER
                return
            else:
                reason = DebugReason.MM_CANDIDATE_LIMIT
        else:
            reason = DebugReason.NO_ACTION

        record["action"] = "SKIP"
        record["reason"] = reason

    def _diagnose_quote_setup(
        self,
        *,
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
    ) -> dict[str, Any]:
        diag: dict[str, Any] = {}
        if inventory.band in ("MAX_LONG", "MAX_SHORT"):
            diag["gate_reason"] = DebugReason.MAX_INVENTORY
            return diag

        cfg = state.config
        bid = book.bids[0].price
        ask = book.asks[0].price
        spread = ask - bid
        mid = (bid + ask) / 2.0
        diag.update(mid=mid, spread=spread)

        prices = self.skewed_quote_prices(
            bid,
            ask,
            prediction.score,
            inventory.inventory_ratio,
            regime_params,
            cfg.priceDecimals,
            edge_bias=edge_bias,
        )
        if not prices:
            diag["gate_reason"] = DebugReason.INVALID_QUOTE_PRICES
            return diag

        bid_px, ask_px = prices
        diag.update(bid_px=bid_px, ask_px=ask_px)
        qty = self.dynamic_order_size(
            size,
            profile,
            regime_params,
            inventory,
            cfg.volumeDecimals,
            mid=mid,
        )
        diag["quantity"] = qty
        if qty <= 0:
            diag["gate_reason"] = DebugReason.ZERO_ORDER_SIZE
            return diag

        fill_est = self.estimate_fill_probability(
            book,
            mid,
            spread,
            profile.trade_rate,
            bid_px,
            ask_px,
            book_id=book_id,
        )
        diag["fill_buy"] = fill_est.buy
        diag["fill_sell"] = fill_est.sell

        quote_notional = qty * mid * 2
        diag["quote_notional"] = quote_notional
        if not self._can_add_volume(state, quote_notional):
            diag["gate_reason"] = DebugReason.VOLUME_CAP
            return diag

        edge = ask_px - bid_px
        expected_edge = edge * (fill_est.buy + fill_est.sell) / 2.0
        diag["expected_edge"] = expected_edge
        if expected_edge <= 0:
            diag["gate_reason"] = DebugReason.NON_POSITIVE_EDGE
            return diag

        estimate = self.estimate_round_trip_pnl(
            book_id,
            bid_px,
            ask_px,
            qty,
            is_maker=self._prefer_maker(book_id),
            direction="SYMMETRIC",
            timestamp=state.timestamp,
        )
        diag["expected_realized_pnl"] = estimate.expected_realized_pnl
        if not self._passes_expected_pnl_gate(estimate.expected_realized_pnl):
            diag["gate_reason"] = DebugReason.NEGATIVE_EXPECTED_PNL
            return diag

        if (
            fill_est.buy < regime_params.min_fill_prob
            and fill_est.sell < regime_params.min_fill_prob
        ):
            diag["gate_reason"] = DebugReason.LOW_FILL_PROBABILITY
            return diag

        before_count = self._count_book_instructions(response, book_id)
        if before_count >= self.max_instructions_per_book:
            diag["gate_reason"] = DebugReason.INSTRUCTION_LIMIT
            return diag

        account = self.accounts.get(book_id)
        if account is None:
            diag["gate_reason"] = DebugReason.INSUFFICIENT_BALANCE
            return diag

        buy_size = qty * (0.5 if inventory.band == "LONG" else 1.0)
        sell_size = qty * (0.5 if inventory.band == "SHORT" else 1.0)
        can_buy = (
            fill_est.buy >= regime_params.min_fill_prob
            and account.quote_balance.free >= bid_px * buy_size
        )
        can_sell = (
            fill_est.sell >= regime_params.min_fill_prob
            and account.base_balance.free >= sell_size
        )
        diag["can_buy"] = can_buy
        diag["can_sell"] = can_sell
        if not can_buy and not can_sell:
            diag["gate_reason"] = DebugReason.INSUFFICIENT_BALANCE
        else:
            diag["gate_reason"] = DebugReason.QUOTE_ORDER_GATE
        return diag

    # ------------------------------------------------------------------
    # Structured logging
    # ------------------------------------------------------------------

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
        spread_bps = None
        if mid and bid is not None and ask is not None:
            spread_bps = ((ask - bid) / mid) * 10_000.0

        reason = str(record.get("reason", DebugReason.NO_ACTION))
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
            archetype=record.get("archetype"),
            tier=getattr(profile, "tier", None) if profile is not None else None,
            mid=mid,
            spread_bps=spread_bps,
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
        )

    def _log_submitted_instructions(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
    ) -> None:
        for index, instruction in enumerate(getattr(response, "instructions", []) or []):
            book_id = self._get(instruction, "bookId", "book_id")
            if not self._book_matches(book_id):
                continue
            self._debug_event_counts["SUBMITTED"] += 1
            self._emit(
                "ORDER_LIFECYCLE",
                tick=self._tick,
                timestamp=getattr(state, "timestamp", None),
                phase="SUBMITTED",
                instruction_index=index,
                book_id=book_id,
                instruction=self._instruction_payload(instruction),
            )

    def _log_notices(self, state: MarketSimulationStateUpdate, tick: int) -> None:
        notices = (getattr(state, "notices", None) or {}).get(self.uid, []) or []
        for notice in notices:
            book_id = self._get(notice, "bookId", "book_id")
            if not self._book_matches(book_id):
                continue
            phase = type(notice).__name__.upper()
            self._debug_event_counts[phase] += 1
            self._emit(
                "ORDER_LIFECYCLE",
                tick=tick,
                timestamp=getattr(state, "timestamp", None),
                phase=phase,
                book_id=book_id,
                event=self._event_payload(notice),
            )

    def _emit_run_summary(self, state: MarketSimulationStateUpdate) -> None:
        count = max(self._debug_response_count, 1)
        avg_latency = {
            name: round(total / count, 4)
            for name, total in sorted(self._debug_latency_sum_ms.items())
        }
        max_latency = {
            name: round(value, 4)
            for name, value in sorted(self._debug_latency_max_ms.items())
        }
        self._emit(
            "RUN_SUMMARY",
            force=True,
            tick=self._tick,
            timestamp=getattr(state, "timestamp", None),
            responses=self._debug_response_count,
            reason_counts=dict(self._debug_reason_counts),
            event_counts=dict(self._debug_event_counts),
            average_latency_ms=avg_latency,
            max_latency_ms=max_latency,
        )

    def _emit(self, event_type: str, force: bool = False, **payload: Any) -> None:
        if not self.debug_enabled and not force:
            return
        record = {
            "type": event_type,
            "agent_id": getattr(self, "uid", None),
            "wall_time_ns": time.time_ns(),
            **self._json_safe(payload),
        }
        try:
            line = json.dumps(record, separators=(",", ":"), sort_keys=True)
            bt.logging.info(f"[S1DBG] {line}")
            if self._debug_file is not None:
                self._debug_file.write(line + "\n")
        except Exception as exc:  # Debugging must never break trading.
            bt.logging.warning(f"[S1DBG] emit failed: {exc}")

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def _timed(self, name: str, fn: Callable[..., T], *args, **kwargs) -> T:
        if not self.debug_enabled:
            return fn(*args, **kwargs)
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self._debug_stage_ms[name] = self._elapsed_ms(started)

    def _record_latency(self, name: str, value: float) -> None:
        self._debug_latency_sum_ms[name] += value
        self._debug_latency_max_ms[name] = max(self._debug_latency_max_ms[name], value)

    def _book_record(self, book_id: int) -> dict[str, Any]:
        return self._debug_book_records.setdefault(book_id, {})

    def _instruction_counts_by_book(self, response: FinanceAgentResponse) -> Counter[int]:
        counts: Counter[int] = Counter()
        for instruction in getattr(response, "instructions", []) or []:
            book_id = self._get(instruction, "bookId", "book_id")
            if isinstance(book_id, int):
                counts[book_id] += 1
        return counts

    def _inventory_record_needs_management(self, inventory: dict[str, Any]) -> bool:
        band = inventory.get("band")
        if band in ("MAX_LONG", "MAX_SHORT"):
            return True
        ratio = abs(float(inventory.get("ratio", 0.0) or 0.0))
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-9)
        utilization = ratio / max(max_ratio, 1e-9)
        return utilization >= self.inventory_close_threshold

    def _instruction_payload(self, instruction: Any) -> dict[str, Any]:
        payload = self._object_payload(instruction)
        payload.setdefault("instruction_type", type(instruction).__name__)
        return payload

    def _event_payload(self, event: Any) -> dict[str, Any]:
        payload = self._object_payload(event)
        payload.setdefault("event_type", type(event).__name__)
        return payload

    def _object_payload(self, obj: Any) -> dict[str, Any]:
        if obj is None:
            return {}
        if hasattr(obj, "model_dump"):
            try:
                value = obj.model_dump(mode="json")
                if isinstance(value, dict):
                    return self._json_safe(value)
            except Exception:
                pass
        if hasattr(obj, "dict"):
            try:
                value = obj.dict()
                if isinstance(value, dict):
                    return self._json_safe(value)
            except Exception:
                pass

        names = (
            "bookId",
            "book_id",
            "orderId",
            "order_id",
            "clientOrderId",
            "client_order_id",
            "direction",
            "side",
            "price",
            "quantity",
            "remainingQuantity",
            "filledQuantity",
            "timestamp",
            "delay",
            "reason",
            "status",
            "takerAgentId",
            "makerAgentId",
        )
        result: dict[str, Any] = {}
        for name in names:
            if hasattr(obj, name):
                result[name] = self._json_safe(getattr(obj, name))
        return result

    @classmethod
    def _json_safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if isinstance(value, Enum):
            return cls._json_safe(value.value)
        if isinstance(value, dict):
            return {str(k): cls._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe(v) for v in value]
        if hasattr(value, "model_dump"):
            try:
                return cls._json_safe(value.model_dump(mode="json"))
            except Exception:
                pass
        return str(value)

    @staticmethod
    def _get(obj: Any, *names: str) -> Any:
        if obj is None:
            return None
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    def _book_matches(self, book_id: Any) -> bool:
        return self.debug_book_id < 0 or book_id == self.debug_book_id

    def _should_emit_tick(self, tick: int) -> bool:
        return tick == 1 or tick % self.debug_every_n == 0

    def _close_debug_file(self) -> None:
        handle = getattr(self, "_debug_file", None)
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except OSError:
                pass
            self._debug_file = None

    @staticmethod
    def _elapsed_ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000.0

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    @classmethod
    def _env_bool(cls, name: str, default: bool) -> bool:
        value = os.getenv(name)
        return default if value is None else cls._as_bool(value)

    @staticmethod
    def _env_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default


if __name__ == "__main__":
    launch(Strategy1_Debug)
