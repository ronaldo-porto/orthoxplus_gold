# SPDX-License-Identifier: MIT
"""Strategy1-direct Research candidate built on the proven V4.16.2 economics.

This module intentionally does *not* add another strategy layer.  It reuses the
existing V4.16.2 Research state/learning/persistence infrastructure but replaces
its hot orchestration path with the shortest useful authority chain:

    selected book -> hard safety -> LifecycleEV/TotalScore rank
                  -> Maker/Taker/Skip -> final contract validation

For non-flat inventory the existing V4.16 PositionExitController remains the
only realization authority.

The original Strategy1_Research.py is left untouched so this candidate can be
A/B tested against the V4.16.2 baseline.
"""
from __future__ import annotations

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
from research_execution_controller import (
    ACTION_MAKER as EXEC_ACTION_MAKER,
    ACTION_SKIP as EXEC_ACTION_SKIP,
    ACTION_TAKER as EXEC_ACTION_TAKER,
    choose_execution,
)
from research_neutral_prediction import is_neutral_forecast, prediction_source_of
from research_position_exit import BAND_ABSOLUTE, new_exposure_allowed
from research_risk_guard import evaluate_risk_guard
from research_role_size import maker_entry_size, taker_clip_size


SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1"
SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1"


class Strategy1_Research_Simple(Strategy1_Research):
    """V4.16.2 economics with Strategy1-style direct orchestration.

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
      * V4.16.2 LifecycleEV + TotalScore rank;
      * V4.16.2 Maker/Taker/Skip chooser;
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
        try:
            self._emit(
                "SIMPLE_CONFIG",
                force=True,
                simple_policy_version=SIMPLE_POLICY_VERSION,
                authority="HARD_SAFETY>LIFECYCLE_EV>TOTAL_SCORE>MAKER_TAKER_SKIP",
                exit_authority="POSITION_EXIT_CONTROLLER",
                separate_maintenance_authority=0,
                separate_alpha_authority=0,
                lane_execution_caps=0,
            )
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Inventory: one owner.  Any real position goes to PositionExitController.
    # ------------------------------------------------------------------
    def _inventory_needs_management(self, inventory) -> bool:
        return str(getattr(inventory, "band", "FLAT") or "FLAT").upper() != "FLAT"

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
        bid_px, ask_px = prices

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
                expiryPeriod=self.mm_expiry_period,
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
                expiryPeriod=self.mm_expiry_period,
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
        decision = choose_execution(
            lifecycle_ev=life,
            p_fill=p_fill,
            spread_capture_bps=float(getattr(ev, "spread_capture_bps", 0.0) or 0.0),
            score_value=float(getattr(ev, "total_score_component", 0.0) or 0.0),
            observations_remaining=remaining_obs,
            required_observations=required_obs,
            crossing_cost=0.12,
            maker_size=float(maker_role.size),
            taker_clip=float(taker_role.size or min_size),
            neutral_fallback=is_neutral_forecast(prediction),
            maker_fee_bps=float(getattr(ev, "maker_fee_bps", 0.0) or 0.0),
            taker_fee_bps=float(getattr(ev, "taker_fee_bps", 0.0) or 0.0),
            slippage_bps=float(getattr(self, "research_lifecycle_slippage_bps", 0.75) or 0.75),
        )

        try:
            self._emit(
                "ENTRY_DECISION",
                force=True,
                tick=getattr(self, "_tick", None),
                book=int(book_id),
                lane="DIRECT",
                safe=1,
                lifecycle_ev=life,
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

        # One flat-entry path.  No maintenance branch and no separate alpha branch.
        for book_id in selected_ids:
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
        success_cap = max(1, int(getattr(self, "max_mm_books_per_tick", 4) or 4))
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
