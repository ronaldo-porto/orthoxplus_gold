# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""
Strategy5 — floor-aware HJB / Avellaneda–Stoikov overlay on Strategy3.

Extends Strategy3 so all survival controls stay intact:
  - base-unit inventory caps and once-per-tick position aging
  - cancel-on-risk / avoid-book repair
  - fill learning, FIFO PnL gate, ranked book caps
  - grace period, weak/left-tail defense, floor telemetry
  - separate directional alpha off by default (alpha via fair-value shift)

Quote core is a practical AS/HJB reservation + intensity half-spread in
**spread units**, with July 2026 soft-floor pressure:
  - below floor → stronger expected-edge gate + wider quotes
  - weak books → smaller size, positive-EV sides only
  - left-tail → inventory repair only (no fresh risk by default)
  - strong books (positive PnL + good fills) → mild tighten

Launch:
  ./agents/strategy/run_strategy5.sh -w <coldkey> -h <hotkey> -u 79 -a 8091
"""

from __future__ import annotations

import math
import os
import sys
from typing import Literal

import bittensor as bt

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

from DetailedTemplateAgent import (
    BookProfile,
    BookSelection,
    DirectionForecast,
    MarketRegime,
)
from Strategy3 import (
    InventorySnapshot,
    RegimeParamSet,
    Strategy3,
)


class Strategy5(Strategy3):
    """Strategy3 survival stack + floor-aware AS/HJB reservation quotes."""

    def initialize(self) -> None:
        super().initialize()
        cfg = self.config

        # Practical AS/HJB controls (spread-normalized; not a discrete HJB grid).
        self.hjb_gamma = float(getattr(cfg, "hjb_gamma", 0.15))
        self.hjb_kappa = float(getattr(cfg, "hjb_kappa", 1.5))
        self.hjb_vol_floor = float(getattr(cfg, "hjb_vol_floor", 5e-4))
        self.hjb_horizon = float(getattr(cfg, "hjb_horizon", 1.0))
        self.hjb_alpha_shift = float(getattr(cfg, "hjb_alpha_shift", 0.28))
        self.hjb_vol_spread_weight = float(getattr(cfg, "hjb_vol_spread_weight", 0.18))
        self.hjb_inv_spread_weight = float(getattr(cfg, "hjb_inv_spread_weight", 0.22))
        self.hjb_intensity_spread_weight = float(
            getattr(cfg, "hjb_intensity_spread_weight", 0.10)
        )
        self.hjb_max_half_spread_mult = float(
            getattr(cfg, "hjb_max_half_spread_mult", 2.75)
        )
        self.hjb_min_half_spread_mult = float(
            getattr(cfg, "hjb_min_half_spread_mult", 0.08)
        )
        self.hjb_fallback_to_s3 = bool(getattr(cfg, "hjb_fallback_to_s3", True))

        # Floor-aware HJB pressure (July 2026 soft floor + Pareto shape 1.0).
        self.enable_floor_awareness = bool(getattr(cfg, "enable_floor_awareness", True))
        self.floor_guard_ratio = float(
            getattr(
                cfg,
                "floor_guard_ratio",
                getattr(cfg, "score_floor_guard_ratio", 1.05),
            )
        )
        # Keep Strategy3 guard in sync when HJB floor_guard_ratio is set.
        self.score_floor_guard_ratio = self.floor_guard_ratio
        self.hjb_floor_edge_boost = float(getattr(cfg, "hjb_floor_edge_boost", 0.15))
        self.hjb_weak_book_size_mult = float(getattr(cfg, "hjb_weak_book_size_mult", 0.5))
        self.hjb_weak_book_size_mult = max(0.05, min(1.0, self.hjb_weak_book_size_mult))
        self.hjb_left_tail_quote_enabled = bool(
            getattr(cfg, "hjb_left_tail_quote_enabled", False)
        )
        self.hjb_weak_widen_mult = float(getattr(cfg, "hjb_weak_widen_mult", 1.25))
        self.hjb_below_floor_widen_mult = float(
            getattr(cfg, "hjb_below_floor_widen_mult", 1.18)
        )
        self.hjb_strong_tighten_mult = float(getattr(cfg, "hjb_strong_tighten_mult", 0.90))
        self.hjb_min_side_edge_bps = float(getattr(cfg, "hjb_min_side_edge_bps", 0.5))

        # Alpha comes from fair-value reservation shift, not separate GTC/GTT alpha.
        if not bool(getattr(cfg, "enable_separate_alpha", False)):
            self.enable_separate_alpha = False

        self._quote_vol = self.hjb_vol_floor
        self._quote_trade_rate = self.trade_rate_ref
        self._hjb_fallback_count = 0
        self._hjb_ctx_book_id: int | None = None
        self._hjb_ctx_is_weak = False
        self._hjb_ctx_is_left_tail = False
        self._hjb_ctx_is_strong = False
        self._hjb_ctx_below_floor = False
        self._hjb_ctx_inventory_util = 0.0
        self._hjb_last_reservation = 0.0
        self._hjb_last_used_fallback = False

        bt.logging.info(
            "Strategy5: floor-aware HJB/AS on Strategy3 "
            f"gamma={self.hjb_gamma} kappa={self.hjb_kappa} "
            f"horizon={self.hjb_horizon} alpha_shift={self.hjb_alpha_shift} "
            f"floor_aware={self.enable_floor_awareness} "
            f"floor_guard={self.floor_guard_ratio} "
            f"edge_boost={self.hjb_floor_edge_boost} "
            f"weak_size={self.hjb_weak_book_size_mult} "
            f"left_tail_quote={self.hjb_left_tail_quote_enabled} "
            f"max_inv={self.max_inventory_base} mm_size={self.mm_base_size} "
            f"separate_alpha={self.enable_separate_alpha}"
        )

    @staticmethod
    def _clip(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    def _hjb_book_quality(
        self,
        book_id: int,
        profile: BookProfile,
    ) -> tuple[bool, bool, bool, bool]:
        """Return (is_weak, is_left_tail, is_strong, below_floor_guard)."""
        weak = book_id in getattr(self, "_last_weak_books", set())
        left_tail = book_id in getattr(self, "_last_left_tail_books", set())
        mem = self._mem(book_id)
        is_strong = (
            float(profile.realized_pnl) > 0.0
            and mem.fill_rate >= 0.20
            and mem.win_rate >= 0.50
            and mem.loss_streak < max(1, self.toxic_loss_streak - 1)
            and not weak
            and not left_tail
        )
        thr = 0.0
        trading = float(getattr(self, "_floor_score_ema", 0.0))
        scores = getattr(self, "_last_book_scores", {}) or {}
        if scores:
            thr = self._soft_floor_threshold(scores)
            trading = self._estimate_agent_trading_score(scores)
        below_floor = thr > 0.0 and trading < self.floor_guard_ratio * thr
        return weak, left_tail, is_strong, below_floor

    def _hjb_spread_pressure(
        self,
        is_weak: bool,
        is_strong: bool,
        below_floor: bool,
        signed_inv: float,
    ) -> float:
        """Widen weak/below-floor books; tighten only strong profitable books."""
        pressure = 1.0
        # Inventory-improving quotes may stay closer; otherwise widen weak books.
        improving = abs(signed_inv) >= 0.15
        if is_weak and not improving:
            pressure *= self.hjb_weak_widen_mult
        if below_floor and not is_strong:
            pressure *= self.hjb_below_floor_widen_mult
        if is_strong and not below_floor:
            pressure *= self.hjb_strong_tighten_mult
        return self._clip(pressure, 0.70, 2.25)

    def _hjb_min_expected_pnl(self, below_floor: bool) -> float:
        threshold = float(self.min_expected_realized_pnl)
        if below_floor and self.enable_floor_awareness:
            boost = self.hjb_floor_edge_boost * max(
                float(getattr(self, "_floor_pnl_scale", 0.02)), 1e-4
            )
            threshold = max(threshold, 0.0) + boost
        return threshold

    def get_hjb_optimal_quotes(
        self,
        bid: float,
        ask: float,
        signal: float,
        signed_inv: float,
        regime_params: RegimeParamSet,
        price_dec: int,
        edge_bias: float = 0.0,
        volatility: float | None = None,
        trade_rate: float | None = None,
        spread_pressure: float = 1.0,
    ) -> tuple[float, float] | None:
        """
        AS/HJB-inspired quotes in spread units with optional floor pressure.

        reservation = mid + spread * (alpha_signal - inventory_pressure)
        half_spread = spread * (regime_offset + vol + |inv| + intensity) * pressure
        """
        spread = ask - bid
        if spread <= 0:
            return None
        mid = 0.5 * (bid + ask)
        if mid <= 0:
            return None

        vol = max(
            float(volatility if volatility is not None else self._quote_vol),
            self.hjb_vol_floor,
        )
        vol_ref = max(
            getattr(self, "profile_vol_scale", self.archetype_vol_threshold), 1e-9
        )
        vol_ratio = self._clip(vol / vol_ref, 0.0, 4.0)

        inv = self._clip(float(signed_inv), -1.5, 1.5)
        alpha = self._clip(float(signal) + float(edge_bias), -1.0, 1.0)

        gamma = max(self.hjb_gamma, 1e-6)
        reservation_shift = (
            self.hjb_alpha_shift * alpha
            - self.inventory_skew_strength
            * inv
            * (1.0 + 0.35 * vol_ratio)
            * (1.0 + 0.50 * gamma)
            * self.hjb_horizon
        )
        reservation_shift = self._clip(reservation_shift, -0.90, 0.90)
        reservation = mid + spread * reservation_shift
        self._hjb_last_reservation = reservation

        intensity = self._clip(
            float(trade_rate if trade_rate is not None else self._quote_trade_rate)
            / max(self.trade_rate_ref, 1e-9),
            0.20,
            4.0,
        )
        kappa = max(self.hjb_kappa, 1e-6)
        as_term = math.log1p(gamma / kappa) / gamma
        half_ratio = (
            max(0.05, regime_params.spread_offset)
            + self.hjb_vol_spread_weight * min(vol_ratio, 2.0)
            + self.hjb_inv_spread_weight * min(abs(inv), 1.0)
            + self.hjb_intensity_spread_weight
            * self._clip(as_term, 0.0, 5.0)
            * self.hjb_horizon
            / max(intensity, 1e-9)
        )
        half_ratio *= max(0.70, float(spread_pressure))
        half_ratio = self._clip(
            half_ratio,
            self.hjb_min_half_spread_mult,
            self.hjb_max_half_spread_mult,
        )

        half_buy = half_ratio / self._clip(regime_params.buy_bias, 0.25, 2.0)
        half_sell = half_ratio / self._clip(regime_params.sell_bias, 0.25, 2.0)
        half_buy = self._clip(
            half_buy, self.hjb_min_half_spread_mult, self.hjb_max_half_spread_mult
        )
        half_sell = self._clip(
            half_sell, self.hjb_min_half_spread_mult, self.hjb_max_half_spread_mult
        )

        tick = 10.0 ** (-price_dec)
        bid_px = round(reservation - spread * half_buy, price_dec)
        ask_px = round(reservation + spread * half_sell, price_dec)
        bid_px = min(bid_px, ask - tick)
        ask_px = max(ask_px, bid + tick)
        bid_px = round(bid_px, price_dec)
        ask_px = round(ask_px, price_dec)
        if bid_px <= 0 or bid_px >= ask_px:
            return None
        return bid_px, ask_px

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
        """Prefer floor-aware HJB/AS overlay; optionally fall back to Strategy3 skew."""
        pressure = self._hjb_spread_pressure(
            is_weak=self._hjb_ctx_is_weak,
            is_strong=self._hjb_ctx_is_strong,
            below_floor=self._hjb_ctx_below_floor,
            signed_inv=inventory_ratio,
        )
        prices = self.get_hjb_optimal_quotes(
            bid=bid,
            ask=ask,
            signal=signal,
            signed_inv=inventory_ratio,
            regime_params=regime_params,
            price_dec=price_dec,
            edge_bias=edge_bias,
            volatility=self._quote_vol,
            trade_rate=self._quote_trade_rate,
            spread_pressure=pressure,
        )
        self._hjb_last_used_fallback = False
        if prices is not None:
            return prices
        if self.hjb_fallback_to_s3:
            self._hjb_fallback_count += 1
            self._hjb_last_used_fallback = True
            return super().skewed_quote_prices(
                bid,
                ask,
                signal,
                inventory_ratio,
                regime_params,
                price_dec,
                edge_bias=edge_bias,
            )
        return None

    def _side_improves_inventory(
        self,
        side: Literal["buy", "sell"],
        inventory: InventorySnapshot,
    ) -> bool:
        if inventory.band in ("LONG", "MAX_LONG"):
            return side == "sell"
        if inventory.band in ("SHORT", "MAX_SHORT"):
            return side == "buy"
        return False

    def _side_edge_bps(
        self,
        side: Literal["buy", "sell"],
        bid_px: float,
        ask_px: float,
        fair: float,
        mid: float,
    ) -> float:
        ref = max(mid, fair, 1e-12)
        if side == "buy":
            return (fair - bid_px) / ref * 10_000.0
        return (ask_px - fair) / ref * 10_000.0

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
    ) -> int:
        if inventory.band in ("MAX_LONG", "MAX_SHORT"):
            return 0

        self._quote_vol = max(float(profile.volatility or 0.0), self.hjb_vol_floor)
        self._quote_trade_rate = max(float(profile.trade_rate or 0.0), 1e-9)

        is_weak, is_left_tail, is_strong, below_floor = self._hjb_book_quality(
            book_id, profile
        )
        self._hjb_ctx_book_id = book_id
        self._hjb_ctx_is_weak = is_weak
        self._hjb_ctx_is_left_tail = is_left_tail
        self._hjb_ctx_is_strong = is_strong
        self._hjb_ctx_below_floor = below_floor and self.enable_floor_awareness
        self._hjb_ctx_inventory_util = self._signed_inventory_util(inventory)

        # Left-tail: repair inventory only unless explicitly enabled.
        if (
            self.enable_floor_awareness
            and is_left_tail
            and not self.hjb_left_tail_quote_enabled
            and inventory.band == "FLAT"
        ):
            if stats is not None:
                stats["skipped_left_tail"] = stats.get("skipped_left_tail", 0) + 1
            return 0

        quote_size = size
        if self.enable_floor_awareness and is_weak:
            quote_size = size * self.hjb_weak_book_size_mult
            if stats is not None:
                stats["weak_size_reduced"] = stats.get("weak_size_reduced", 0) + 1

        cfg = state.config
        bid = book.bids[0].price
        ask = book.asks[0].price
        spread = ask - bid
        mid = (bid + ask) / 2.0
        signed_inv = self._signed_inventory_util(inventory)

        prices = self.skewed_quote_prices(
            bid,
            ask,
            prediction.score,
            signed_inv,
            regime_params,
            cfg.priceDecimals,
            edge_bias=edge_bias,
        )
        if not prices:
            return 0
        bid_px, ask_px = prices
        fair = self._hjb_last_reservation if self._hjb_last_reservation > 0 else mid

        qty = self.dynamic_order_size(
            quote_size, profile, regime_params, inventory, cfg.volumeDecimals, mid=mid
        )
        if qty <= 0:
            return 0

        fill_est = self.estimate_fill_probability(
            book, mid, spread, profile.trade_rate, bid_px, ask_px, book_id=book_id
        )
        quote_notional = qty * mid * 2
        if not self._can_add_volume(state, quote_notional):
            return 0

        edge = ask_px - bid_px
        expected_edge = edge * (fill_est.buy + fill_est.sell) / 2.0
        if expected_edge <= 0:
            return 0

        est = self.estimate_round_trip_pnl(
            book_id,
            bid_px,
            ask_px,
            qty,
            is_maker=self._prefer_maker(book_id),
            direction="SYMMETRIC",
            timestamp=state.timestamp,
        )
        adj_pnl = est.expected_realized_pnl * (fill_est.buy + fill_est.sell) / 2.0
        min_pnl = self._hjb_min_expected_pnl(self._hjb_ctx_below_floor)
        if adj_pnl <= min_pnl:
            if stats is not None:
                stats["skipped_negative_pnl"] = stats.get("skipped_negative_pnl", 0) + 1
            return 0
        if (
            fill_est.buy < regime_params.min_fill_prob
            and fill_est.sell < regime_params.min_fill_prob
        ):
            return 0

        buy_size = qty
        sell_size = qty
        if inventory.band == "LONG":
            buy_size = self._round_order_size(qty * 0.5, cfg.volumeDecimals)
        elif inventory.band == "SHORT":
            sell_size = self._round_order_size(qty * 0.5, cfg.volumeDecimals)
        max_buy = max(0.0, self.max_inventory_base - inventory.net_base)
        max_sell = max(0.0, self.max_inventory_base + inventory.net_base)
        buy_size = self._round_order_size(min(buy_size, max_buy), cfg.volumeDecimals)
        sell_size = self._round_order_size(min(sell_size, max_sell), cfg.volumeDecimals)

        buy_edge = self._side_edge_bps("buy", bid_px, ask_px, fair, mid)
        sell_edge = self._side_edge_bps("sell", bid_px, ask_px, fair, mid)

        allow_buy = True
        allow_sell = True
        if self.enable_floor_awareness and is_left_tail and not self.hjb_left_tail_quote_enabled:
            # Inventory repair only: quote the reducing side.
            allow_buy = self._side_improves_inventory("buy", inventory)
            allow_sell = self._side_improves_inventory("sell", inventory)
        elif self.enable_floor_awareness and is_weak:
            # Weak books: only positive-EV sides (or inventory-improving).
            allow_buy = (
                buy_edge >= self.hjb_min_side_edge_bps
                or self._side_improves_inventory("buy", inventory)
            )
            allow_sell = (
                sell_edge >= self.hjb_min_side_edge_bps
                or self._side_improves_inventory("sell", inventory)
            )

        placed = 0
        acct = self.accounts[book_id]
        mem = self._mem(book_id)
        buy_touch_dist = max(0.0, (bid - bid_px) / max(spread, 1e-12))
        sell_touch_dist = max(0.0, (ask_px - ask) / max(spread, 1e-12))

        if (
            allow_buy
            and buy_size > 0
            and fill_est.buy >= regime_params.min_fill_prob
            and acct.quote_balance.free >= bid_px * buy_size
            and self._count_book_instructions(response, book_id)
            < self.max_instructions_per_book
        ):
            self._record_fill_quote(mem, "buy", buy_touch_dist)
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
            mem.quote_count += 1

        if (
            allow_sell
            and sell_size > 0
            and fill_est.sell >= regime_params.min_fill_prob
            and acct.base_balance.free >= sell_size
            and self._count_book_instructions(response, book_id)
            < self.max_instructions_per_book
        ):
            self._record_fill_quote(mem, "sell", sell_touch_dist)
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
            mem.quote_count += 1

        if stats is not None and placed:
            stats["hjb_quotes"] = stats.get("hjb_quotes", 0) + placed
            if self._hjb_last_used_fallback:
                stats["hjb_fallback_count"] = stats.get("hjb_fallback_count", 0) + 1
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
        pre_fallback = self._hjb_fallback_count
        stats = super().build_mm_strategy_instructions(
            response,
            state,
            selection,
            predictions,
            regime,
            collect_archetypes=collect_archetypes,
        )
        tick_fallbacks = max(0, self._hjb_fallback_count - pre_fallback)
        stats["hjb_fallback_count"] = int(
            stats.get("hjb_fallback_count", 0)
        ) + tick_fallbacks

        trading = float(stats.get("estimated_trading_score", 0.0))
        soft_floor = float(stats.get("estimated_soft_floor_score", 0.0))
        to_median = float(stats.get("estimated_score_to_median", 0.0))
        weak_n = int(stats.get("weak_books_count", 0))
        left_n = int(stats.get("left_tail_books_count", 0))
        bt.logging.info(
            "[HJB_FLOOR] "
            f"estimated_trading_score={trading:.6f} "
            f"estimated_soft_floor_score={soft_floor:.6f} "
            f"score_to_median={to_median:.6f} "
            f"weak_books={weak_n} left_tail_books={left_n} "
            f"hjb_fallback_count={stats.get('hjb_fallback_count', 0)} "
            f"quoted={stats.get('quoted', 0)} managed={stats.get('managed', 0)}"
        )
        return stats


if __name__ == "__main__":
    launch(Strategy5)
