# SPDX-FileCopyrightText: 2026
# SPDX-License-Identifier: MIT
"""
Strategy6 — latency-aware HJB/AS execution strategy for TAOS / SN79.

Strategy6 is the competitive continuation of the Strategy1 lineage.  It inherits
Strategy4 so it keeps Strategy1's signal/profile/Kappa infrastructure plus
Strategy4's corrected base-unit inventory model, score-floor controls, unique
client IDs, cancel/replace flow, exact order metadata, delayed markout learning,
side-specific EV, and hard risk modes.

Strategy6 adds four execution hardening layers:

1. Latency-aware HJB/AS quote construction
   - decays short-horizon alpha as expected simulator delay rises
   - adds HJB-style inventory pressure to the reservation price
   - widens the half-spread for volatility, low intensity, and stale-response risk

2. Runtime exchange-constraint synchronization
   - uses state.config.min_order_size rather than trusting a static CLI default
   - never rounds a capped side back above its remaining inventory allowance

3. Dust-safe inventory handling
   - never over-closes a residual position that is smaller than the exchange's
     minimum executable order size

4. Economically prioritized same-book instruction ordering
   - inventory-reducing side first
   - then higher side EV
   - then alpha-aligned side
   This matters because the validator gives the first instruction on a book the
   base delay and adds an extra random delay to subsequent instructions.

Recommended starting mode is deterministic.  Treat all parameters as a baseline
for multi-seed simulation races, not as a guaranteed production optimum.
"""

from __future__ import annotations

import math
import os
import sys

import bittensor as bt

_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

from taos.common.agents import launch
from taos.im.protocol import FinanceAgentResponse, MarketSimulationStateUpdate
from taos.im.protocol.models import Book, OrderDirection

from DetailedTemplateAgent import BookProfile, BookSelection, DirectionForecast, MarketRegime
from Strategy1 import BookArchetype, InventorySnapshot, RegimeParamSet
from Strategy4 import QuoteDecision, Strategy4


class Strategy6(Strategy4):
    """Strategy4 execution stack with latency-aware HJB quote hardening."""

    def initialize(self) -> None:
        super().initialize()
        cfg = self.config

        # ------------------------------------------------------------------
        # HJB / AS overlay
        # ------------------------------------------------------------------
        self.s6_hjb_gamma = float(getattr(cfg, "s6_hjb_gamma", 0.18))
        self.s6_hjb_kappa = float(getattr(cfg, "s6_hjb_kappa", 1.50))
        self.s6_hjb_horizon = float(getattr(cfg, "s6_hjb_horizon", 1.00))
        self.s6_hjb_inventory_extra = float(
            getattr(cfg, "s6_hjb_inventory_extra", 0.18)
        )
        self.s6_hjb_base_half_spread = float(
            getattr(cfg, "s6_hjb_base_half_spread", 0.06)
        )
        self.s6_hjb_vol_spread_weight = float(
            getattr(cfg, "s6_hjb_vol_spread_weight", 0.10)
        )
        self.s6_hjb_intensity_spread_weight = float(
            getattr(cfg, "s6_hjb_intensity_spread_weight", 0.06)
        )
        self.s6_hjb_latency_spread_weight = float(
            getattr(cfg, "s6_hjb_latency_spread_weight", 0.12)
        )
        self.s6_alpha_latency_decay = float(
            getattr(cfg, "s6_alpha_latency_decay", 0.85)
        )
        self.s6_gamma_vol_weight = float(getattr(cfg, "s6_gamma_vol_weight", 0.10))
        self.s6_gamma_latency_weight = float(
            getattr(cfg, "s6_gamma_latency_weight", 0.20)
        )

        # ------------------------------------------------------------------
        # Validator-delay estimator.  Defaults mirror the current SN79
        # validator mapping but remain configurable so validator changes can be
        # absorbed without editing strategy code.
        # ------------------------------------------------------------------
        self.s6_validator_timeout_ms = float(
            getattr(cfg, "s6_validator_timeout_ms", 3000.0)
        )
        self.s6_delay_min_ms = float(getattr(cfg, "s6_delay_min_ms", 10.0))
        self.s6_delay_max_ms = float(getattr(cfg, "s6_delay_max_ms", 1000.0))
        self.s6_delay_curve = float(getattr(cfg, "s6_delay_curve", 5.0))
        self.s6_network_buffer_ms = float(
            getattr(cfg, "s6_network_buffer_ms", 15.0)
        )

        # ------------------------------------------------------------------
        # Same-book instruction priority.  Risk-reducing orders get a large
        # deterministic bonus because only the first instruction avoids the
        # validator's extra same-book delay.
        # ------------------------------------------------------------------
        self.s6_inventory_priority_bonus = float(
            getattr(cfg, "s6_inventory_priority_bonus", 50.0)
        )
        self.s6_alpha_priority_bonus = float(
            getattr(cfg, "s6_alpha_priority_bonus", 2.0)
        )

        # Runtime exchange constraint cache.  It is synchronized from each
        # MarketSimulationStateUpdate before strategy orchestration.
        self._s6_exchange_min_order_size = max(float(self.min_order_size), 0.0)

        # Strategy6 expresses directional alpha through fair/reservation price.
        # Do not open the old independent GTC/GTT alpha branch unless explicitly
        # requested by the operator.
        if not bool(getattr(cfg, "enable_separate_alpha", False)):
            self.enable_separate_alpha = False

        bt.logging.info(
            "Strategy6: Strategy4 execution + latency-aware HJB "
            f"gamma={self.s6_hjb_gamma} kappa={self.s6_hjb_kappa} "
            f"horizon={self.s6_hjb_horizon} min_size={self.min_order_size} "
            f"floor_awareness={self.enable_floor_awareness} "
            f"policy={self.alpha_policy_mode}"
        )

    # ------------------------------------------------------------------
    # Exchange constraints / order-size safety
    # ------------------------------------------------------------------

    def _sync_exchange_constraints(self, state: MarketSimulationStateUpdate) -> None:
        cfg = state.config
        state_min = float(getattr(cfg, "min_order_size", 0.0) or 0.0) if cfg else 0.0
        if state_min > 0.0:
            self._s6_exchange_min_order_size = state_min
            # Existing inherited helpers use self.min_order_size.  Keep them in
            # sync with the actual simulator/exchange contract for this state.
            self.min_order_size = state_min
        else:
            self._s6_exchange_min_order_size = max(float(self.min_order_size), 0.0)

    def _normalize_side_size(
        self,
        raw_size: float,
        max_allowed: float,
        volume_decimals: int,
    ) -> float:
        """Round a side without violating min-size or remaining inventory cap.

        Strategy1/4's generic ``_round_order_size`` clamps *up* to the minimum.
        That is useful for ordinary order creation, but unsafe after applying a
        hard remaining-inventory cap: e.g. a 0.10 remaining allowance must never
        be rounded back up to a 0.25 order.  Strategy6 returns zero instead.
        """
        raw = max(0.0, float(raw_size))
        cap = max(0.0, float(max_allowed))
        min_size = max(0.0, float(self._s6_exchange_min_order_size))
        if raw <= 0.0 or cap <= 0.0:
            return 0.0
        if min_size > 0.0 and cap + 1e-12 < min_size:
            return 0.0

        size = min(raw, cap)
        size = round(size, volume_decimals)
        if min_size > 0.0 and size + 1e-12 < min_size:
            size = round(min_size, volume_decimals)
        if size > cap + 1e-12:
            size = round(cap, volume_decimals)
        if min_size > 0.0 and size + 1e-12 < min_size:
            return 0.0
        return max(0.0, size)

    def _side_sizes(
        self,
        common_size: float,
        inventory: InventorySnapshot,
        prediction: DirectionForecast,
        decision: QuoteDecision,
        volume_decimals: int,
    ) -> tuple[float, float]:
        """Strategy4 sizing with a hard, non-overridable post-fill inventory cap."""
        inv = self._signed_inventory_util(inventory)
        buy_mult = math.exp(-self.size_inventory_skew * inv)
        sell_mult = math.exp(self.size_inventory_skew * inv)

        signal = self._clip(prediction.score, -1.0, 1.0)
        buy_mult *= 1.0 + 0.15 * signal
        sell_mult *= 1.0 - 0.15 * signal

        if decision.risk_mode == "CAUTIOUS":
            buy_mult *= self.cautious_size_mult
            sell_mult *= self.cautious_size_mult
        elif decision.risk_mode in ("REDUCE_ONLY", "LIQUIDATE", "DISABLED"):
            if inventory.net_base > 0.0:
                buy_mult = 0.0
                sell_mult = min(self.max_side_size_mult, 1.25)
            elif inventory.net_base < 0.0:
                sell_mult = 0.0
                buy_mult = min(self.max_side_size_mult, 1.25)
            else:
                buy_mult = 0.0
                sell_mult = 0.0

        buy_mult = self._clip(buy_mult, 0.0, self.max_side_size_mult)
        sell_mult = self._clip(sell_mult, 0.0, self.max_side_size_mult)
        raw_buy = common_size * decision.action.size_mult * buy_mult
        raw_sell = common_size * decision.action.size_mult * sell_mult

        max_buy = max(0.0, self.max_inventory_base - inventory.net_base)
        max_sell = max(0.0, self.max_inventory_base + inventory.net_base)
        buy_size = self._normalize_side_size(raw_buy, max_buy, volume_decimals)
        sell_size = self._normalize_side_size(raw_sell, max_sell, volume_decimals)
        return buy_size, sell_size

    # ------------------------------------------------------------------
    # Latency-aware HJB / AS quotes
    # ------------------------------------------------------------------

    def _estimated_sim_delay_ms(self) -> float:
        """Estimate validator-assigned base simulator delay from last local latency."""
        timeout = max(self.s6_validator_timeout_ms, 1.0)
        process_ms = max(0.0, self._last_response_latency_ms + self.s6_network_buffer_ms)
        t = self._clip(process_ms / timeout, 0.0, 1.0)
        curve = max(self.s6_delay_curve, 1e-6)
        denom = math.exp(curve) - 1.0
        frac = (math.exp(curve * t) - 1.0) / max(denom, 1e-12)
        return self.s6_delay_min_ms + frac * (
            self.s6_delay_max_ms - self.s6_delay_min_ms
        )

    def _latency_staleness(self) -> float:
        expiry_ms = max(float(self.mm_expiry_period) / 1_000_000.0, 1.0)
        return self._clip(self._estimated_sim_delay_ms() / expiry_ms, 0.0, 2.0)

    def _quote_decision(
        self,
        book: Book,
        profile: BookProfile,
        prediction: DirectionForecast,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
        price_decimals: int,
    ) -> QuoteDecision | None:
        """Overlay Strategy4's constrained quote with latency-aware HJB terms."""
        base = super()._quote_decision(
            book,
            profile,
            prediction,
            inventory,
            regime_params,
            regime,
            archetype,
            price_decimals,
        )
        if base is None or not book.bids or not book.asks:
            return base

        best_bid = book.bids[0].price
        best_ask = book.asks[0].price
        spread = best_ask - best_bid
        mid = 0.5 * (best_bid + best_ask)
        if spread <= 0.0 or mid <= 0.0:
            return None

        vol_ratio = self._clip(
            float(profile.volatility or 0.0) / max(self.profile_vol_scale, 1e-9),
            0.0,
            4.0,
        )
        intensity = self._clip(
            float(profile.trade_rate or 0.0) / max(self.trade_rate_ref, 1e-9),
            0.20,
            4.0,
        )
        stale = self._latency_staleness()

        # ``s6_hjb_gamma`` is a tunable scale around the conservative default
        # 0.18; Strategy4 still provides the absolute risk-aversion bounds.
        gamma_scale = max(self.s6_hjb_gamma, 1e-6) / 0.18
        gamma = self._clip(
            base.gamma
            * gamma_scale
            * (
                1.0
                + self.s6_gamma_vol_weight * min(vol_ratio, 2.0)
                + self.s6_gamma_latency_weight * min(stale, 1.0)
            ),
            self.min_risk_aversion,
            self.max_risk_aversion,
        )

        # Short-horizon alpha loses value while an instruction is in flight.
        alpha_decay = math.exp(-self.s6_alpha_latency_decay * stale)
        fair_price = mid + (base.fair_price - mid) * alpha_decay

        # Preserve Strategy4's existing inventory reservation shift, then add a
        # bounded HJB-style inventory/variance/horizon term in spread units.
        inherited_inventory_shift = base.fair_price - base.reservation_price
        inv = self._signed_inventory_util(inventory)
        hjb_inventory_shift = (
            spread
            * self.s6_hjb_inventory_extra
            * gamma
            * inv
            * (0.25 + 0.50 * min(vol_ratio, 2.0))
            * max(self.s6_hjb_horizon, 0.0)
        )
        reservation_price = (
            fair_price - inherited_inventory_shift - hjb_inventory_shift
        )

        # AS/HJB spread term.  Strategy4 already has volatility, toxicity,
        # inventory and GLFT terms, so this is used as a lower bound rather than
        # blindly adding the same risks twice.
        kappa = max(self.s6_hjb_kappa, 1e-6)
        as_term = math.log1p(gamma / kappa) / max(gamma, 1e-9)
        hjb_half_ratio = (
            self.s6_hjb_base_half_spread
            + self.s6_hjb_vol_spread_weight * min(vol_ratio, 2.0)
            + self.s6_hjb_intensity_spread_weight
            * self._clip(as_term, 0.0, 5.0)
            * max(self.s6_hjb_horizon, 0.0)
            / max(intensity, 0.20)
            + self.s6_hjb_latency_spread_weight * min(stale, 1.5)
        )
        hjb_half_spread = spread * max(0.02, hjb_half_ratio)
        half_spread = max(base.half_spread, hjb_half_spread)

        half_bps = half_spread / mid * 10_000.0
        half_bps = self._clip(
            half_bps,
            self.min_half_spread_bps,
            self.max_half_spread_bps,
        )
        half_spread = mid * half_bps / 10_000.0

        tick = 10.0 ** (-price_decimals)
        bid_price = min(reservation_price - half_spread, best_ask - tick)
        ask_price = max(reservation_price + half_spread, best_bid + tick)
        bid_price = round(bid_price, price_decimals)
        ask_price = round(ask_price, price_decimals)
        if bid_price <= 0.0 or bid_price >= ask_price:
            return None

        return QuoteDecision(
            fair_price=fair_price,
            reservation_price=reservation_price,
            bid_price=bid_price,
            ask_price=ask_price,
            half_spread=half_spread,
            gamma=gamma,
            toxicity=base.toxicity,
            risk_mode=base.risk_mode,
            action=base.action,
            context_key=base.context_key,
        )

    # ------------------------------------------------------------------
    # Dust-safe inventory and latency-priority ordering
    # ------------------------------------------------------------------

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
        # Do not turn a small partial-fill residue into an opposite position by
        # clamping a close order above the actual inventory quantity.
        min_size = max(self._s6_exchange_min_order_size, 0.0)
        if (
            inventory.band != "FLAT"
            and min_size > 0.0
            and abs(inventory.net_base) + 1e-12 < min_size
        ):
            return 0
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

    def _instruction_priority(
        self,
        instruction,
        inventory: InventorySnapshot,
        prediction: DirectionForecast,
    ) -> float:
        client_id = getattr(instruction, "clientOrderId", None)
        meta = self._active_order_meta.get(client_id) if client_id is not None else None
        ev = float(getattr(meta, "expected_value_bps", 0.0) or 0.0)
        direction = getattr(instruction, "direction", None)

        side = None
        if direction == OrderDirection.BUY:
            side = "buy"
        elif direction == OrderDirection.SELL:
            side = "sell"

        priority = ev
        if side == "sell" and inventory.net_base > 0.0:
            priority += self.s6_inventory_priority_bonus
        elif side == "buy" and inventory.net_base < 0.0:
            priority += self.s6_inventory_priority_bonus

        signal = self._clip(prediction.score, -1.0, 1.0)
        if side == "buy" and signal > 0.0:
            priority += self.s6_alpha_priority_bonus * abs(signal)
        elif side == "sell" and signal < 0.0:
            priority += self.s6_alpha_priority_bonus * abs(signal)
        return priority

    def _place_skewed_quotes(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        book: Book,
        profile: BookProfile,
        prediction: DirectionForecast,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        size: float,
        edge_bias: float,
        stats: dict | None = None,
        *,
        regime: MarketRegime | None = None,
        archetype: BookArchetype | None = None,
    ) -> int:
        # Capture only instructions emitted by this quote call.  Strategy4 emits
        # BUY then SELL; Strategy6 reorders that fresh slice economically.
        start = len(response.instructions)
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
            stats,
            regime=regime,
            archetype=archetype,
        )
        if placed <= 1:
            return placed

        fresh = list(response.instructions[start:])
        fresh.sort(
            key=lambda instr: self._instruction_priority(instr, inventory, prediction),
            reverse=True,
        )
        response.instructions[start:] = fresh
        return placed

    def build_mm_strategy_instructions(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        selection: BookSelection,
        predictions: dict[int, DirectionForecast],
        regime: MarketRegime,
        collect_archetypes: bool = True,
    ) -> dict:
        # Sync constraints before *any* inventory-management or quote path runs.
        self._sync_exchange_constraints(state)
        stats = super().build_mm_strategy_instructions(
            response,
            state,
            selection,
            predictions,
            regime,
            collect_archetypes=collect_archetypes,
        )
        stats["s6_estimated_sim_delay_ms"] = round(self._estimated_sim_delay_ms(), 4)
        stats["s6_exchange_min_order_size"] = self._s6_exchange_min_order_size
        return stats


if __name__ == "__main__":
    launch(Strategy6)
