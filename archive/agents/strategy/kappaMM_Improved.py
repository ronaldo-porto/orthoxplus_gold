# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""
KappaMMStrategyAgent — inventory-aware market making for subnet 79.

Extends DetailedTemplateAgent (profiles, regime, Kappa tracking, PnL estimates)
with:

  - Book archetype classifier
  - Regime-specific parameters
  - Expected fill probability
  - Dynamic order sizing
  - Inventory-skewed quoting
  - Profit-target / stop-loss management (+ close_position for loans)

Launch:
  --agent.path agents \\
  --agent.name KappaMMStrategyAgent \\
  --agent.params enable_mm_strategy=1 lazy_load=1 verbose_log=0 log_every_n=100
"""

from __future__ import annotations

import json
import math
import os
import sys
from dataclasses import dataclass
from typing import Literal

import bittensor as bt

_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

from taos.common.agents import launch
from taos.im.protocol import FinanceAgentResponse, MarketSimulationStateUpdate
from taos.im.protocol.models import Book, OrderDirection, STP, TimeInForce
from taos.im.protocol.instructions import LoanSettlementOption

from DetailedTemplateAgent import (
    BookProfile,
    BookSelection,
    DetailedTemplateAgent,
    DirectionForecast,
    MarketRegime,
    MarketRegimeMode,
)

BookArchetype = Literal[
    "LIQUID_ACTIVE",
    "LIQUID_QUIET",
    "VOLATILE",
    "STRESSED",
    "TRENDING",
    "CHOP",
]

InventoryBand = Literal["FLAT", "LONG", "SHORT", "MAX_LONG", "MAX_SHORT"]


@dataclass
class RegimeParamSet:
    quote_enabled: bool
    alpha_enabled: bool
    spread_offset: float
    skew_strength: float
    size_mult: float
    profit_target_bps: float
    stop_loss_bps: float
    min_fill_prob: float


@dataclass
class FillProbabilityEstimate:
    buy: float
    sell: float


@dataclass
class InventorySnapshot:
    net_base: float
    inventory_ratio: float
    band: InventoryBand
    entry_mid: float | None
    unrealized_bps: float | None


@dataclass
class BookMemory:
    quote_count: int = 0
    fill_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    recent_pnl: float = 0.0
    last_activity_ts: int = 0
    loss_streak: int = 0
    last_signal: float = 0.0
    last_expected_alpha: float = 0.0

    @property
    def fill_rate(self) -> float:
        return self.fill_count / max(self.quote_count, 1)

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / max(total, 1)


DEFAULT_REGIME_PARAMS: dict[MarketRegimeMode, RegimeParamSet] = {
    "QUIET": RegimeParamSet(
        quote_enabled=True, alpha_enabled=False,
        spread_offset=0.25, skew_strength=0.15, size_mult=0.8,
        profit_target_bps=5.0, stop_loss_bps=35.0, min_fill_prob=0.15,
    ),
    "CHOP": RegimeParamSet(
        quote_enabled=True, alpha_enabled=False,
        spread_offset=0.35, skew_strength=0.10, size_mult=0.7,
        profit_target_bps=8.0, stop_loss_bps=40.0, min_fill_prob=0.20,
    ),
    "TRENDING_UP": RegimeParamSet(
        quote_enabled=True, alpha_enabled=True,
        spread_offset=0.20, skew_strength=0.30, size_mult=1.2,
        profit_target_bps=12.0, stop_loss_bps=45.0, min_fill_prob=0.25,
    ),
    "TRENDING_DOWN": RegimeParamSet(
        quote_enabled=True, alpha_enabled=True,
        spread_offset=0.20, skew_strength=0.30, size_mult=1.2,
        profit_target_bps=12.0, stop_loss_bps=45.0, min_fill_prob=0.25,
    ),
    "BROAD_LIQUID": RegimeParamSet(
        quote_enabled=True, alpha_enabled=True,
        spread_offset=0.22, skew_strength=0.20, size_mult=1.0,
        profit_target_bps=10.0, stop_loss_bps=40.0, min_fill_prob=0.20,
    ),
    "DISPERSED": RegimeParamSet(
        quote_enabled=True, alpha_enabled=True,
        spread_offset=0.28, skew_strength=0.25, size_mult=0.9,
        profit_target_bps=10.0, stop_loss_bps=40.0, min_fill_prob=0.22,
    ),
    "STRESSED": RegimeParamSet(
        quote_enabled=False, alpha_enabled=False,
        spread_offset=0.45, skew_strength=0.05, size_mult=0.5,
        profit_target_bps=15.0, stop_loss_bps=25.0, min_fill_prob=0.30,
    ),
    "MIXED": RegimeParamSet(
        quote_enabled=True, alpha_enabled=False,
        spread_offset=0.28, skew_strength=0.18, size_mult=0.85,
        profit_target_bps=8.0, stop_loss_bps=38.0, min_fill_prob=0.18,
    ),
}


class KappaMMStrategyAgent(DetailedTemplateAgent):
    """
    Inventory-aware MM strategy built on DetailedTemplateAgent signals.

    Agent params (in addition to DetailedTemplateAgent params):
        enable_mm_strategy (bool): Run MM logic (default True).
        mm_base_size (float): Base quote size in BASE (default 0.25).
        max_inventory_base (float): Max |net_base| before band caps (default 2.0).
        inventory_skew_strength (float): Quote skew per inventory_ratio (default 0.35).
        archetype_stressed_spread_bps (float): STRESSED archetype threshold (default 8.0).
        archetype_active_trade_rate (float): LIQUID_ACTIVE threshold (default 2.0).
        archetype_vol_threshold (float): VOLATILE / CHOP vol threshold (default 0.006).
        trade_rate_ref (float): Fill model reference trade rate (default 2.0).
        log_mm_strategy (bool): Log [MM_STRATEGY] (default True).
        mm_expiry_period_ns (int): GTT expiry for quotes (default 500_000_000).
    """

    def initialize(self) -> None:
        super().initialize()
        cfg = self.config
        self.enable_mm_strategy = bool(getattr(cfg, "enable_mm_strategy", True))
        self.enable_kappa_strategy = bool(getattr(cfg, "enable_kappa_strategy", False))
        self.mm_base_size = float(getattr(cfg, "mm_base_size", 0.25))
        self.max_inventory_base = float(getattr(cfg, "max_inventory_base", 2.0))
        self.inventory_skew_strength = float(getattr(cfg, "inventory_skew_strength", 0.35))
        self.archetype_stressed_spread_bps = float(
            getattr(cfg, "archetype_stressed_spread_bps", 8.0)
        )
        self.archetype_active_trade_rate = float(
            getattr(cfg, "archetype_active_trade_rate", 2.0)
        )
        self.archetype_vol_threshold = float(getattr(cfg, "archetype_vol_threshold", 0.006))
        self.trade_rate_ref = float(getattr(cfg, "trade_rate_ref", 2.0))
        self.log_mm_strategy = bool(getattr(cfg, "log_mm_strategy", True))
        self.mm_expiry_period = int(getattr(cfg, "mm_expiry_period_ns", 500_000_000))

        # New safety / selection controls. These prevent over-trading and reduce
        # downside-heavy Kappa damage. Tune from logs, not guesses.
        self.max_mm_books_per_tick = max(1, int(getattr(cfg, "max_mm_books_per_tick", 16)))
        self.min_expected_alpha = float(getattr(cfg, "min_expected_alpha", 0.25))
        self.toxic_loss_streak = max(1, int(getattr(cfg, "toxic_loss_streak", 3)))
        self.toxic_recent_pnl = float(getattr(cfg, "toxic_recent_pnl", -0.01))
        self.toxic_spread_bps = float(getattr(cfg, "toxic_spread_bps", 10.0))
        self.microprice_weight = float(getattr(cfg, "microprice_weight", 0.8))
        self.coverage_boost_weight = float(getattr(cfg, "coverage_boost_weight", 0.15))

        self.book_memory: dict[int, BookMemory] = {}

        self._initial_base: dict[int, float] = {}
        self._entry_mid: dict[int, float] = {}
        self._last_mm_stats: dict = {}

        bt.logging.info(
            f"KappaMMStrategyAgent: mm={self.enable_mm_strategy} "
            f"base_size={self.mm_base_size} max_inv={self.max_inventory_base}"
        )

    def _reset_pnl_state(self) -> None:
        super()._reset_pnl_state()
        self._initial_base.clear()
        self._entry_mid.clear()
        self.book_memory.clear()
        self._last_mm_stats = {}

    def get_regime_params(self, regime: MarketRegime) -> RegimeParamSet:
        params = DEFAULT_REGIME_PARAMS.get(
            regime.mode, DEFAULT_REGIME_PARAMS["MIXED"]
        )
        if regime.scoring_overlay == "SCORING_PRESSURE":
            return RegimeParamSet(
                quote_enabled=True,
                alpha_enabled=False,
                spread_offset=params.spread_offset,
                skew_strength=params.skew_strength * 0.5,
                size_mult=min(params.size_mult, 0.6),
                profit_target_bps=params.profit_target_bps,
                stop_loss_bps=params.stop_loss_bps,
                min_fill_prob=params.min_fill_prob,
            )
        return params

    def classify_book_archetype(
        self,
        profile: BookProfile,
        regime: MarketRegime,
    ) -> BookArchetype:
        spread_bps = profile.spread_bps or 0.0
        if spread_bps >= self.archetype_stressed_spread_bps or regime.mode == "STRESSED":
            return "STRESSED"
        if profile.trade_rate >= self.archetype_active_trade_rate:
            if abs(profile.predict_score) >= self.direction_threshold:
                return "TRENDING"
            return "LIQUID_ACTIVE"
        if profile.volatility >= self.archetype_vol_threshold:
            if profile.predict_direction == "HOLD":
                return "CHOP"
            return "VOLATILE"
        if abs(profile.predict_score) >= self.direction_threshold:
            return "TRENDING"
        return "LIQUID_QUIET"

    def onTrade(self, event, validator: str | None = None) -> None:
        """Update parent FIFO PnL, then update per-book memory from actual fills."""
        book_id = getattr(event, "bookId", None)
        before = self._pnl_tick_buffer.get(book_id, 0.0) if book_id is not None else 0.0
        super().onTrade(event, validator)

        if book_id is None:
            return
        is_ours = self.uid == getattr(event, "takerAgentId", None) or self.uid == getattr(event, "makerAgentId", None)
        if not is_ours:
            return

        mem = self.book_memory.setdefault(book_id, BookMemory())
        mem.fill_count += 1
        mem.last_activity_ts = getattr(event, "timestamp", 0) or self._scoring_timestamp

        after = self._pnl_tick_buffer.get(book_id, 0.0)
        realized_pnl = after - before
        if realized_pnl != 0.0:
            # Exponential memory: reacts to recent damage without forgetting all history.
            mem.recent_pnl = 0.90 * mem.recent_pnl + 0.10 * realized_pnl
            if realized_pnl > 0:
                mem.win_count += 1
                mem.loss_streak = 0
            else:
                mem.loss_count += 1
                mem.loss_streak += 1

    def microprice_signal(self, book: Book) -> float:
        """Best-level microprice displacement in [-1, 1]. Positive means bid depth dominates."""
        if not book.bids or not book.asks:
            return 0.0
        bid = book.bids[0].price
        ask = book.asks[0].price
        bid_qty = book.bids[0].quantity
        ask_qty = book.asks[0].quantity
        spread = ask - bid
        if spread <= 0:
            return 0.0
        mid = 0.5 * (bid + ask)
        micro = (ask * bid_qty + bid * ask_qty) / max(bid_qty + ask_qty, 1e-9)
        return max(-1.0, min(1.0, (micro - mid) / spread))

    def predict_direction(self, book_id: int, book: Book, timestamp: int) -> DirectionForecast:
        """Parent signal plus microprice. This makes the signal less clone-like."""
        mid = self._book_mid(book)
        log_return, _ = self._update_momentum(book_id, timestamp, mid)
        momentum_m = self._normalize_momentum(log_return)
        flow_f = self._compute_flow_f(book)
        trade_t, trade_imbalance = self._compute_trade_t(book)
        micro = self.microprice_signal(book)

        score = (
            self.w_m * momentum_m
            + self.w_f * flow_f
            + self.w_t * trade_t
            + self.microprice_weight * micro
        )

        if score > self.direction_threshold:
            direction: Literal["UP", "DOWN", "HOLD"] = "UP"
        elif score < -self.direction_threshold:
            direction = "DOWN"
        else:
            direction = "HOLD"

        mem = self.book_memory.setdefault(book_id, BookMemory())
        mem.last_signal = score

        return DirectionForecast(
            book_id=book_id,
            direction=direction,
            score=score,
            momentum_m=momentum_m,
            flow_f=flow_f,
            trade_t=trade_t,
            log_return=log_return,
            imbalance=flow_f,
            trade_imbalance=trade_imbalance,
        )

    def coverage_priority(self, book_id: int, now: int) -> float:
        """Higher value means this book has been ignored longer and needs activity."""
        mem = self.book_memory.setdefault(book_id, BookMemory())
        if mem.last_activity_ts <= 0:
            return 1.0
        age = max(0, now - mem.last_activity_ts)
        return min(1.0, age / max(self.pnl_lookback_ns, 1))

    def is_toxic_book(self, profile: BookProfile, archetype: BookArchetype, mem: BookMemory) -> bool:
        """Hard Kappa protection: skip books likely to create downside outliers."""
        spread_bad = profile.spread_bps is not None and profile.spread_bps >= self.toxic_spread_bps
        return (
            archetype == "STRESSED"
            or spread_bad
            or mem.loss_streak >= self.toxic_loss_streak
            or mem.recent_pnl <= self.toxic_recent_pnl
            or profile.tier == "RED"
        )

    def expected_alpha_score(
        self,
        profile: BookProfile,
        prediction: DirectionForecast,
        fill_est: FillProbabilityEstimate,
        mem: BookMemory,
        book_id: int,
        now: int,
    ) -> float:
        """Ranking score for quote candidates. Combines signal, fill, memory and coverage."""
        signal = min(1.0, abs(prediction.score))
        fill = 0.5 * (fill_est.buy + fill_est.sell)
        memory_quality = 0.5 * mem.win_rate + 0.5 * mem.fill_rate
        pnl_term = max(-1.0, min(1.0, mem.recent_pnl * 100.0))
        coverage = self.coverage_priority(book_id, now)

        score = (
            0.35 * signal
            + 0.25 * fill
            + 0.20 * memory_quality
            + 0.10 * pnl_term
            + self.coverage_boost_weight * coverage
        )
        return score

    def _net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        account = self.accounts.get(book_id)
        if not account or mid <= 0:
            return InventorySnapshot(0.0, 0.0, "FLAT", None, None)

        current_base = account.base_balance.total - account.base_loan
        if book_id not in self._initial_base:
            self._initial_base[book_id] = current_base
        net_base = current_base - self._initial_base[book_id]

        wealth_per_book = 0.0
        if self.simulation_config:
            wealth_per_book = self.simulation_config.miner_wealth / max(
                self.simulation_config.book_count, 1
            )
        inventory_ratio = 0.0
        if wealth_per_book > 0:
            inventory_ratio = (net_base * mid) / wealth_per_book

        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-9)
        flat_eps = self.mm_base_size * 1e-3

        if abs(net_base) < flat_eps:
            band: InventoryBand = "FLAT"
        elif net_base > 0:
            band = "MAX_LONG" if inventory_ratio >= max_ratio else "LONG"
        else:
            band = "MAX_SHORT" if inventory_ratio >= max_ratio else "SHORT"

        entry = self._entry_mid.get(book_id)
        unrealized_bps: float | None = None
        if entry and entry > 0:
            if net_base > 0:
                unrealized_bps = ((mid - entry) / entry) * 10_000.0
            elif net_base < 0:
                unrealized_bps = ((entry - mid) / entry) * 10_000.0

        return InventorySnapshot(
            net_base=net_base,
            inventory_ratio=inventory_ratio,
            band=band,
            entry_mid=entry,
            unrealized_bps=unrealized_bps,
        )

    def estimate_fill_probability(
        self,
        book: Book,
        mid: float,
        spread: float,
        trade_rate: float,
        buy_price: float,
        sell_price: float,
    ) -> FillProbabilityEstimate:
        if spread <= 0 or mid <= 0:
            return FillProbabilityEstimate(0.0, 0.0)

        trade_factor = min(1.0, trade_rate / max(self.trade_rate_ref, 1e-9))

        bid_depth = book.bids[0].quantity if book.bids else 0.0
        ask_depth = book.asks[0].quantity if book.asks else 0.0
        total_bid = sum(l.quantity for l in book.bids) if book.bids else bid_depth
        total_ask = sum(l.quantity for l in book.asks) if book.asks else ask_depth

        buy_dist = (mid - buy_price) / spread
        sell_dist = (sell_price - mid) / spread
        buy_depth_f = bid_depth / max(total_bid, 1e-9)
        sell_depth_f = ask_depth / max(total_ask, 1e-9)

        p_buy = trade_factor * (0.4 * buy_depth_f + 0.6 * max(0.0, 1.0 - buy_dist))
        p_sell = trade_factor * (0.4 * sell_depth_f + 0.6 * max(0.0, 1.0 - sell_dist))
        return FillProbabilityEstimate(
            buy=max(0.0, min(1.0, p_buy)),
            sell=max(0.0, min(1.0, p_sell)),
        )

    def dynamic_order_size(
        self,
        base_size: float,
        profile: BookProfile,
        regime_params: RegimeParamSet,
        inventory: InventorySnapshot,
        vol_dec: int,
    ) -> float:
        vol_scale = 1.0
        if profile.volatility > 0:
            target_vol = self.profile_vol_scale
            vol_scale = max(0.5, min(2.0, target_vol / profile.volatility))

        kappa_scale = 1.0
        if profile.raw_kappa is not None:
            kappa_scale = max(0.5, min(1.5, 1.0 + profile.raw_kappa * 0.2))

        inv_util = abs(inventory.inventory_ratio)
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-9)
        inventory_scale = max(0.3, 1.0 - inv_util / max(max_ratio, 1e-9))

        size = (
            base_size
            * regime_params.size_mult
            * vol_scale
            * kappa_scale
            * inventory_scale
        )
        return self._round_order_size(size, vol_dec)

    def skewed_quote_prices(
        self,
        bid: float,
        ask: float,
        signal: float,
        inventory_ratio: float,
        regime_params: RegimeParamSet,
        price_dec: int,
    ) -> tuple[float, float] | None:
        spread = ask - bid
        if spread <= 0:
            return None
        mid = 0.5 * (bid + ask)
        offset = regime_params.spread_offset
        signal_skew = regime_params.skew_strength * signal
        inv_skew = self.inventory_skew_strength * inventory_ratio
        bid_px = round(mid - spread * (offset + signal_skew + inv_skew), price_dec)
        ask_px = round(mid + spread * (offset - signal_skew - inv_skew), price_dec)
        if bid_px <= 0 or bid_px >= ask_px:
            return None
        return bid_px, ask_px

    def _record_entry_mid(self, book_id: int, mid: float) -> None:
        if book_id not in self._entry_mid:
            self._entry_mid[book_id] = mid

    def _try_close_loans(
        self,
        response: FinanceAgentResponse,
        book_id: int,
        unrealized_bps: float | None,
        profit_target_bps: float,
    ) -> bool:
        account = self.accounts.get(book_id)
        if not account or not account.loans:
            return False
        if unrealized_bps is None or unrealized_bps < profit_target_bps:
            return False
        for order_id in list(account.loans.keys()):
            if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
                break
            response.close_position(book_id=book_id, order_id=order_id, delay=0)
        return True

    def _manage_inventory(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        book: Book,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        size: float,
    ) -> int:
        if inventory.band == "FLAT":
            return 0

        placed = 0
        mid = (book.bids[0].price + book.asks[0].price) / 2.0
        qty = self._round_order_size(abs(inventory.net_base), state.config.volumeDecimals)
        if qty <= 0:
            return 0

        if self._try_close_loans(
            response, book_id, inventory.unrealized_bps, regime_params.profit_target_bps,
        ):
            placed += 1

        unreal = inventory.unrealized_bps
        stop = regime_params.stop_loss_bps
        target = regime_params.profit_target_bps

        long_pos = inventory.net_base > 0
        stop_hit = unreal is not None and unreal <= -stop
        target_hit = unreal is not None and unreal >= target

        if stop_hit or (target_hit and regime_params.quote_enabled is False):
            close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
            if self._passes_fee_gate(book_id, aggressive=True):
                if self._count_book_instructions(response, book_id) < self.max_instructions_per_book:
                    if close_dir == OrderDirection.SELL and self.accounts[book_id].base_balance.free >= qty:
                        response.market_order(
                            book_id=book_id,
                            direction=close_dir,
                            quantity=qty,
                            stp=STP.CANCEL_OLDEST,
                            delay=0,
                        )
                        placed += 1
                        self._entry_mid.pop(book_id, None)
                    elif close_dir == OrderDirection.BUY:
                        px = book.asks[0].price
                        if self.accounts[book_id].quote_balance.free >= qty * px:
                            response.market_order(
                                book_id=book_id,
                                direction=close_dir,
                                quantity=qty,
                                stp=STP.CANCEL_OLDEST,
                                delay=0,
                            )
                            placed += 1
                            self._entry_mid.pop(book_id, None)
            return placed

        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        close_px = round(
            book.bids[0].price if close_dir == OrderDirection.BUY else book.asks[0].price,
            state.config.priceDecimals,
        )
        if self._count_book_instructions(response, book_id) < self.max_instructions_per_book:
            if close_dir == OrderDirection.SELL and self.accounts[book_id].base_balance.free >= qty:
                response.limit_order(
                    book_id=book_id,
                    direction=close_dir,
                    quantity=qty,
                    price=close_px,
                    stp=STP.CANCEL_BOTH,
                    postOnly=self._prefer_maker(book_id),
                    timeInForce=TimeInForce.GTT,
                    expiryPeriod=self.mm_expiry_period,
                    leverage=0.0,
                    settlement_option=LoanSettlementOption.NONE,
                    delay=0,
                )
                placed += 1
            elif close_dir == OrderDirection.BUY:
                if self.accounts[book_id].quote_balance.free >= qty * close_px:
                    response.limit_order(
                        book_id=book_id,
                        direction=close_dir,
                        quantity=qty,
                        price=close_px,
                        stp=STP.CANCEL_BOTH,
                        postOnly=self._prefer_maker(book_id),
                        timeInForce=TimeInForce.GTT,
                        expiryPeriod=self.mm_expiry_period,
                        leverage=0.0,
                        settlement_option=LoanSettlementOption.NONE,
                        delay=0,
                    )
                    placed += 1
        return placed

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
    ) -> int:
        if inventory.band in ("MAX_LONG", "MAX_SHORT"):
            return 0

        cfg = state.config
        bid = book.bids[0].price
        ask = book.asks[0].price
        spread = ask - bid
        mid = (bid + ask) / 2.0

        prices = self.skewed_quote_prices(
            bid, ask, prediction.score, inventory.inventory_ratio,
            regime_params, cfg.priceDecimals,
        )
        if not prices:
            return 0
        bid_px, ask_px = prices
        qty = self.dynamic_order_size(
            size, profile, regime_params, inventory, cfg.volumeDecimals,
        )
        if qty <= 0:
            return 0

        fill_est = self.estimate_fill_probability(
            book, mid, spread, profile.trade_rate, bid_px, ask_px,
        )
        quote_notional = qty * mid * 2
        if not self._can_add_volume(state, quote_notional):
            return 0

        est = self.estimate_round_trip_pnl(
            book_id, bid_px, ask_px, qty,
            is_maker=self._prefer_maker(book_id),
            direction="SYMMETRIC",
            timestamp=state.timestamp,
        )
        adj_pnl = est.expected_realized_pnl * (fill_est.buy + fill_est.sell) / 2.0
        if fill_est.buy < regime_params.min_fill_prob and fill_est.sell < regime_params.min_fill_prob:
            return 0

        placed = 0
        acct = self.accounts[book_id]

        buy_size = qty
        sell_size = qty
        if inventory.band == "LONG":
            buy_size = self._round_order_size(qty * 0.5, cfg.volumeDecimals)
        elif inventory.band == "SHORT":
            sell_size = self._round_order_size(qty * 0.5, cfg.volumeDecimals)

        if (
            fill_est.buy >= regime_params.min_fill_prob
            and acct.quote_balance.free >= bid_px * buy_size
            and self._count_book_instructions(response, book_id) < self.max_instructions_per_book
        ):
            response.limit_order(
                book_id=book_id,
                direction=OrderDirection.BUY,
                quantity=buy_size,
                price=bid_px,
                clientOrderId=70000 + book_id * 10 + 1,
                stp=STP.CANCEL_BOTH,
                postOnly=self._prefer_maker(book_id),
                timeInForce=TimeInForce.GTT,
                expiryPeriod=self.mm_expiry_period,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )
            placed += 1
            self._record_entry_mid(book_id, mid)

        if (
            fill_est.sell >= regime_params.min_fill_prob
            and acct.base_balance.free >= sell_size
            and self._count_book_instructions(response, book_id) < self.max_instructions_per_book
        ):
            response.limit_order(
                book_id=book_id,
                direction=OrderDirection.SELL,
                quantity=sell_size,
                price=ask_px,
                clientOrderId=70000 + book_id * 10 + 2,
                stp=STP.CANCEL_BOTH,
                postOnly=self._prefer_maker(book_id),
                timeInForce=TimeInForce.GTT,
                expiryPeriod=self.mm_expiry_period,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )
            placed += 1
            self._record_entry_mid(book_id, mid)

        if self.log_predict_pnl and placed > 0:
            bt.logging.info(
                f"[PREDICT_PNL] mm book={book_id} fill_b={round(fill_est.buy, 3)} "
                f"fill_s={round(fill_est.sell, 3)} adj_pnl={round(adj_pnl, 6)} "
                f"bid={bid_px} ask={ask_px}"
            )
        return placed

    def build_mm_strategy_instructions(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        selection: BookSelection,
        predictions: dict[int, DirectionForecast],
        regime: MarketRegime,
    ) -> dict:
        """
        Improved execution loop:
          1. Manage existing inventory first.
          2. Schedule limited maintenance by coverage decay.
          3. Score quote candidates by expected alpha.
          4. Quote only top-N candidates.
          5. Hard-skip toxic books to protect Kappa downside.
        """
        stats = {
            "quoted": 0,
            "managed": 0,
            "maintenance": 0,
            "skipped_avoid": 0,
            "skipped_toxic": 0,
            "skipped_low_alpha": 0,
            "candidate_count": 0,
            "instructions": 0,
        }
        if not state.config or not state.books:
            self._last_mm_stats = stats
            return stats

        regime_params = self.get_regime_params(regime)
        avoid_set = set(selection.avoid_books)
        profile_by_id = {p.book_id: p for p in selection.profiles}

        archetype_rows: list[dict] = []
        quote_candidates: list[tuple[float, int, Book, BookProfile, DirectionForecast, InventorySnapshot, RegimeParamSet]] = []

        # Maintenance scheduler: use existing inactive list, but prioritize books
        # whose activity is oldest. This avoids wide uncontrolled MM just to get coverage.
        maint_limit = self.max_maintenance_books_per_tick
        if regime.scoring_overlay == "SCORING_PRESSURE":
            maint_limit = min(len(selection.maintenance_books), self.max_maintenance_books_per_tick * 2)
        maintenance_due = sorted(
            selection.maintenance_books,
            key=lambda bid: self.coverage_priority(bid, state.timestamp),
            reverse=True,
        )[:maint_limit]
        maintenance_due_set = set(maintenance_due)

        for book_id, book in state.books.items():
            if not book.bids or not book.asks:
                continue
            profile = profile_by_id.get(book_id)
            prediction = predictions.get(book_id)
            if not profile or not prediction:
                continue

            archetype = self.classify_book_archetype(profile, regime)
            mem = self.book_memory.setdefault(book_id, BookMemory())
            archetype_rows.append({
                "book": book_id,
                "arch": archetype,
                "tier": profile.tier,
                "fill": round(mem.fill_rate, 3),
                "win": round(mem.win_rate, 3),
                "loss_streak": mem.loss_streak,
            })

            if book_id in avoid_set:
                stats["skipped_avoid"] += 1
                continue

            if self.is_toxic_book(profile, archetype, mem):
                stats["skipped_toxic"] += 1
                continue

            mid = (book.bids[0].price + book.asks[0].price) / 2.0
            inventory = self._net_inventory(book_id, mid)

            # First priority: reduce dangerous inventory.
            if inventory.band != "FLAT":
                n = self._manage_inventory(
                    response, state, book_id, book, inventory, regime_params, self.mm_base_size,
                )
                if n:
                    stats["managed"] += 1
                    stats["instructions"] += n
                continue

            # Second priority: controlled activity coverage.
            if book_id in maintenance_due_set:
                n = self._place_round_trip_limits(
                    response, state, book_id, self.maintenance_order_size,
                    post_only=True, expiry_period=state.config.publish_interval,
                    client_id_base=20000,
                )
                if n:
                    stats["maintenance"] += 1
                    stats["instructions"] += n
                    mem.quote_count += n
                continue

            if not regime_params.quote_enabled:
                continue
            if archetype in ("STRESSED", "VOLATILE") and regime.mode in ("CHOP", "STRESSED"):
                stats["skipped_toxic"] += 1
                continue

            bid = book.bids[0].price
            ask = book.asks[0].price
            spread = ask - bid
            prices = self.skewed_quote_prices(
                bid, ask, prediction.score, inventory.inventory_ratio,
                regime_params, state.config.priceDecimals,
            )
            if not prices or spread <= 0:
                continue
            bid_px, ask_px = prices
            fill_est = self.estimate_fill_probability(
                book, mid, spread, profile.trade_rate, bid_px, ask_px,
            )
            expected_alpha = self.expected_alpha_score(
                profile, prediction, fill_est, mem, book_id, state.timestamp,
            )
            mem.last_expected_alpha = expected_alpha

            if expected_alpha < self.min_expected_alpha:
                stats["skipped_low_alpha"] += 1
                continue

            quote_candidates.append((
                expected_alpha, book_id, book, profile, prediction, inventory, regime_params,
            ))

        stats["candidate_count"] = len(quote_candidates)
        quote_candidates.sort(key=lambda x: x[0], reverse=True)

        # Quote only the best books this tick. This is the key anti-overtrade gate.
        for expected_alpha, book_id, book, profile, prediction, inventory, params in quote_candidates[:self.max_mm_books_per_tick]:
            before = len(response.instructions)
            n = self._place_skewed_quotes(
                response, state, book_id, book, profile, prediction,
                inventory, params, self.mm_base_size,
            )
            if n:
                stats["quoted"] += 1
                stats["instructions"] += n
                self.book_memory.setdefault(book_id, BookMemory()).quote_count += n
            # Global soft cap: do not let one tick explode with instructions.
            if len(response.instructions) - before > self.max_instructions_per_book:
                break

        # Alpha layer: only top alpha books, not every positive book.
        if regime_params.alpha_enabled and self._alpha_regime_allows(regime):
            alpha_ranked = []
            for book_id in selection.alpha_books:
                if book_id in avoid_set or book_id not in state.books:
                    continue
                pred = predictions.get(book_id)
                profile = profile_by_id.get(book_id)
                mem = self.book_memory.setdefault(book_id, BookMemory())
                if not pred or not profile or pred.direction == "HOLD":
                    continue
                if self.is_toxic_book(profile, self.classify_book_archetype(profile, regime), mem):
                    continue
                alpha_ranked.append((abs(pred.score) + 0.25 * mem.win_rate, book_id, pred))

            alpha_ranked.sort(key=lambda x: x[0], reverse=True)
            for _, book_id, pred in alpha_ranked[:self.max_alpha_books_per_tick]:
                n = self._place_directional_round_trip(
                    response, state, book_id,
                    "UP" if pred.direction == "UP" else "DOWN",
                    self.alpha_order_size,
                    client_id_base=80000,
                )
                if n:
                    stats["instructions"] += n
                    self.book_memory.setdefault(book_id, BookMemory()).quote_count += n

        stats["archetypes"] = archetype_rows[:12]
        self._last_mm_stats = stats
        return stats

    def _log_mm_strategy(self, stats: dict, regime: MarketRegime) -> None:
        bt.logging.info(
            f"[MM_STRATEGY] regime={regime.mode} overlay={regime.scoring_overlay} "
            f"stats={json.dumps({k: v for k, v in stats.items() if k != 'archetypes'})}"
        )
        if stats.get("archetypes"):
            bt.logging.info(f"[MM_STRATEGY] archetypes={json.dumps(stats['archetypes'])}")

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        response = FinanceAgentResponse(agent_id=self.uid)
        self._tick += 1

        summary = self.parse_state(state)
        predictions = self._predict_all_books(state)
        selection = self.select_books_for_trading(state, predictions)
        regime = self.classify_market_regime_from_profiles(
            selection.profiles, predictions, selection,
        )

        if self.verbose_log and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_input(summary)
        if self.log_direction and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_direction_predictions(predictions)
        if self.log_book_profile and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_book_profile_selection(selection)
        if self.log_regime and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_market_regime(regime)
        if self.log_momentum_pnl and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_momentum_and_pnl(summary, state)

        in_grace = state.timestamp < summary.grace_period_ns
        if state.books and not in_grace:
            if self.enable_mm_strategy:
                mm_stats = self.build_mm_strategy_instructions(
                    response, state, selection, predictions, regime,
                )
                if self.log_mm_strategy and (
                    self._tick == 1 or self._tick % self.log_every_n == 0
                ):
                    self._log_mm_strategy(mm_stats, regime)
            elif self.enable_kappa_strategy:
                strategy_stats = self.build_kappa_strategy_instructions(
                    response, state, selection, predictions, regime,
                )
                if self.log_kappa_strategy and (
                    self._tick == 1 or self._tick % self.log_every_n == 0
                ):
                    self._log_kappa_strategy_calibration(
                        state, selection, regime, strategy_stats,
                    )
            elif self.enable_trading:
                self.build_demo_instructions(response, state, book_id=0)
        elif state.books and in_grace and (
            self.enable_mm_strategy or self.enable_kappa_strategy or self.enable_trading
        ):
            bt.logging.info(
                f"Grace period active (T={state.timestamp} < {summary.grace_period_ns}); "
                "no orders placed."
            )

        if self.verbose_log and response.instructions and (
            self._tick == 1 or self._tick % self.log_every_n == 0
        ):
            self._log_output(self.parse_response(response))

        return response


if __name__ == "__main__":
    launch(KappaMMStrategyAgent)
