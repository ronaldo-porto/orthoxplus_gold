# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""
KappaMMStrategyAgent — inventory-aware market making for subnet 79.

Extends DetailedTemplateAgent (profiles, regime, Kappa tracking, PnL estimates)
with:

  - Book archetype classifier (DEAD / MM / WALL / TREND / TOXIC / STRESSED)
  - Per-archetype parameter adjustments merged with regime profiles
  - Expected fill probability (depth + distance + trade-rate model)
  - Dynamic order sizing (confidence, vol, inventory, spread, kappa)
  - Inventory-skewed quoting with regime directional bias
  - Composite close_score + time_stop position management
  - BookMemory (EMA PnL, fill/win rates, loss streak, activity age)
  - Toxic-book gate + expected_alpha scoring + max books per tick
  - Coverage scheduler (maintenance by time since last activity)
  - VWAP PositionTracker from FIFO open lots (validator-aligned)
  - Microprice-augmented predict_direction()
  - Pre-submit FIFO expected PnL gate (MM quotes + alpha round-trips)
  - Sim-time auto-tuning scheduler + optional tuning.json hot-reload

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
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Literal

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
    DetailedTemplateAgent,
    DirectionForecast,
    MarketRegime,
    MarketRegimeMode,
)

BookArchetype = Literal[
    "DEAD_BOOK",
    "MM_BOOK",
    "WALL_BOOK",
    "TREND_BOOK",
    "TOXIC_BOOK",
    "STRESSED",
]

InventoryBand = Literal["FLAT", "LONG", "SHORT", "MAX_LONG", "MAX_SHORT"]
InventoryReason = Literal["UNKNOWN", "MAINTENANCE", "MM", "ALPHA", "MARKET"]

MAINT_CLIENT_ID_BASE = 20000
MM_CLIENT_ID_BASE = 70000
ALPHA_CLIENT_ID_BASE = 80000


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
    buy_bias: float = 1.0
    sell_bias: float = 1.0


@dataclass
class ArchetypeAdjust:
    """Multipliers/deltas applied on top of regime params for a book archetype."""

    size_mult: float = 1.0
    spread_offset_delta: float = 0.0
    skew_strength_mult: float = 1.0
    min_fill_prob_delta: float = 0.0
    edge_bias: float = 0.0
    quote_enabled_override: bool | None = None


@dataclass
class FillProbabilityEstimate:
    buy: float
    sell: float


@dataclass
class PositionTracker:
    """VWAP position derived from FIFO open lots (validator-aligned)."""

    net_qty: float
    vwap_entry: float | None
    opened_at_ns: int | None
    long_qty: float
    short_qty: float


@dataclass
class BookMemory:
    """Per-book trading memory — drives skip/size, fill learning, direction accuracy."""

    quote_count: int = 0
    fill_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    recent_pnl: float = 0.0
    last_activity_ts: int = 0
    loss_streak: int = 0
    last_signal: float = 0.0
    last_expected_alpha: float = 0.0
    direction_hits: int = 0
    direction_misses: int = 0
    # Per-distance fill learning (spread-normalized buckets 0=tight .. 2=wide)
    fill_buy_quotes: tuple[int, int, int] = (0, 0, 0)
    fill_buy_fills: tuple[int, int, int] = (0, 0, 0)
    fill_sell_quotes: tuple[int, int, int] = (0, 0, 0)
    fill_sell_fills: tuple[int, int, int] = (0, 0, 0)
    last_buy_dist_bucket: int = 0
    last_sell_dist_bucket: int = 0
    book_profit_factor: float = 0.5
    book_fill_factor: float = 0.5
    book_kappa_factor: float = 0.5

    @property
    def fill_rate(self) -> float:
        return self.fill_count / max(self.quote_count, 1)

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / max(total, 1)

    @property
    def direction_accuracy(self) -> float:
        total = self.direction_hits + self.direction_misses
        if total == 0:
            return 0.5
        return self.direction_hits / total

    @property
    def specialization_score(self) -> float:
        return (
            0.40 * self.book_profit_factor
            + 0.35 * self.book_fill_factor
            + 0.25 * self.book_kappa_factor
        )


@dataclass
class TuningMetrics:
    kappa_med: float
    win_rate: float
    skip_neg_rate: float
    objective: float
    window_ticks: int


TUNING_PARAM_BOUNDS: dict[str, tuple[float, float]] = {
    "min_expected_alpha": (0.15, 0.45),
    "min_expected_realized_pnl": (0.0, 0.002),
    "max_mm_books_per_tick": (4.0, 12.0),
    "toxic_loss_streak": (2.0, 5.0),
    "toxic_recent_pnl": (-0.05, -0.001),
    "coverage_boost_weight": (0.05, 0.30),
}


@dataclass
class InventorySnapshot:
    net_base: float
    inventory_ratio: float
    band: InventoryBand
    vwap_entry: float | None
    unrealized_bps: float | None
    position_ticks: int = 0
    opened_at_ns: int | None = None
    reason: InventoryReason = "UNKNOWN"


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
        buy_bias=2.0, sell_bias=0.5,
    ),
    "TRENDING_DOWN": RegimeParamSet(
        quote_enabled=True, alpha_enabled=True,
        spread_offset=0.20, skew_strength=0.30, size_mult=1.2,
        profit_target_bps=12.0, stop_loss_bps=45.0, min_fill_prob=0.25,
        buy_bias=0.5, sell_bias=2.0,
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
        buy_bias=0.25, sell_bias=0.25,
    ),
    "MIXED": RegimeParamSet(
        quote_enabled=True, alpha_enabled=False,
        spread_offset=0.28, skew_strength=0.18, size_mult=0.85,
        profit_target_bps=8.0, stop_loss_bps=38.0, min_fill_prob=0.18,
    ),
}

DEFAULT_ARCHETYPE_ADJUST: dict[BookArchetype, ArchetypeAdjust] = {
    "DEAD_BOOK": ArchetypeAdjust(
        size_mult=0.6, spread_offset_delta=0.10, min_fill_prob_delta=-0.05,
    ),
    "MM_BOOK": ArchetypeAdjust(
        size_mult=0.3, spread_offset_delta=-0.05, skew_strength_mult=0.5,
    ),
    "WALL_BOOK": ArchetypeAdjust(
        size_mult=0.5, spread_offset_delta=0.15, edge_bias=0.20,
    ),
    "TREND_BOOK": ArchetypeAdjust(
        size_mult=1.0, spread_offset_delta=-0.05, skew_strength_mult=1.3,
        edge_bias=0.30,
    ),
    "TOXIC_BOOK": ArchetypeAdjust(
        size_mult=0.4, spread_offset_delta=0.20, min_fill_prob_delta=0.05,
    ),
    "STRESSED": ArchetypeAdjust(
        size_mult=0.25, quote_enabled_override=False,
    ),
}


class Strategy1(DetailedTemplateAgent):
    """
    Inventory-aware MM strategy built on DetailedTemplateAgent signals.

    Agent params (in addition to DetailedTemplateAgent params):
        enable_mm_strategy (bool): Run MM logic (default True).
        mm_base_size (float): Base quote size in BASE (default 0.25).
        max_inventory_base (float): Max |net_base| before band caps (default 2.0).
        inventory_skew_strength (float): Quote skew per inventory_ratio (default 0.35).
        target_inventory_ratio (float): Target net position / wealth (default 0.0).
        archetype_dead_trade_rate (float): DEAD_BOOK threshold (default 0.1).
        archetype_mm_spread_bps (float): MM_BOOK max spread bps (default 1.0).
        archetype_wall_imbalance (float): WALL_BOOK imbalance threshold (default 0.6).
        archetype_stressed_spread_bps (float): STRESSED archetype threshold (default 8.0).
        archetype_vol_threshold (float): TREND_BOOK vol threshold (default 0.006).
        trade_rate_ref (float): Fill model reference trade rate (default 2.0).
        position_max_ticks (int): Time-stop horizon before aggressive close (default 300).
        close_score_threshold (float): Composite close trigger (default 0.80).
        inventory_close_threshold (float): Min |inventory| util (vs max) to enter
            manage_inventory; smaller positions quote through with skew (default 0.25).
        passive_exit_only (bool): Prefer passive limits before market closes (default True).
        aggressive_close_min_ticks (int): Min position age before score/time aggressive
            close (default 300).
        maintenance_size_mult (float): Fraction of maintenance_order_size (default 0.25).
        maintenance_passive_exit_only (bool): Never market-close MAINTENANCE inventory
            except stop/MAX band (default True).
        log_mm_strategy (bool): Log [MM_STRATEGY] (default True).
        log_book_memory (bool): Log [BOOK_MEMORY] sample (default True).
        mm_expiry_period_ns (int): GTT expiry for quotes (default 500_000_000).
        min_expected_alpha (float): Min score to quote or alpha (default 0.25).
        max_mm_books_per_tick (int): Max MM quote books per tick (default 8).
        toxic_loss_streak (int): Skip quoting after N losses (default 3).
        toxic_recent_pnl (float): Skip if EMA recent_pnl below (default -0.01).
        toxic_spread_bps (float): Skip if spread bps above (default 10.0).
        w_micro (float): Microprice weight in predict_direction (default 0.8).
        coverage_boost_weight (float): Coverage term in expected_alpha_score (default 0.15).
        min_expected_realized_pnl (float): Min FIFO expected round-trip PnL to place
            MM/alpha orders (default 0.0 — must be strictly positive after fees).
        max_managed_books_per_tick (int): Cap inventory-management books per tick (default 4).
        mm_skip_inactive_tier (bool): Skip MM quotes on INACTIVE tier (maintenance only).
        enable_auto_tuning (bool): Sim-time param scheduler (default False).
        tuning_interval_ns (int): Sim nanoseconds between tuning steps (default 3600s).
        tuning_config_path (str): Optional JSON overrides; default output_dir/tuning.json.
        log_tuning (bool): Log [TUNING] steps (default True).
    """

    def initialize(self) -> None:
        super().initialize()
        cfg = self.config
        # Latency-first defaults (override DetailedTemplateAgent logging defaults).
        self.fast_update = bool(getattr(cfg, "fast_update", True))
        self.sync_event_csv = bool(getattr(cfg, "sync_event_csv", False))
        self.log_latency = bool(getattr(cfg, "log_latency", True))
        self.history_len = int(getattr(cfg, "history_len", 0))
        self.log_direction = bool(getattr(cfg, "log_direction", False))
        self.log_book_profile = bool(getattr(cfg, "log_book_profile", False))
        self.log_regime = bool(getattr(cfg, "log_regime", False))
        self.log_momentum_pnl = bool(getattr(cfg, "log_momentum_pnl", False))
        self.enable_mm_strategy = bool(getattr(cfg, "enable_mm_strategy", True))
        self.enable_kappa_strategy = bool(getattr(cfg, "enable_kappa_strategy", False))
        self.mm_base_size = float(getattr(cfg, "mm_base_size", 0.25))
        self.max_inventory_base = float(getattr(cfg, "max_inventory_base", 1.2))
        self.inventory_skew_strength = float(getattr(cfg, "inventory_skew_strength", 0.35))
        self.target_inventory_ratio = float(getattr(cfg, "target_inventory_ratio", 0.0))
        self.archetype_dead_trade_rate = float(getattr(cfg, "archetype_dead_trade_rate", 0.1))
        self.archetype_mm_spread_bps = float(getattr(cfg, "archetype_mm_spread_bps", 1.0))
        self.archetype_wall_imbalance = float(getattr(cfg, "archetype_wall_imbalance", 0.6))
        self.archetype_stressed_spread_bps = float(
            getattr(cfg, "archetype_stressed_spread_bps", 8.0)
        )
        self.archetype_vol_threshold = float(getattr(cfg, "archetype_vol_threshold", 0.006))
        self.trade_rate_ref = float(getattr(cfg, "trade_rate_ref", 2.0))
        self.position_max_ticks = max(1, int(getattr(cfg, "position_max_ticks", 300)))
        self.close_score_threshold = float(getattr(cfg, "close_score_threshold", 0.80))
        self.inventory_close_threshold = float(
            getattr(cfg, "inventory_close_threshold", 0.25)
        )
        self.passive_exit_only = bool(getattr(cfg, "passive_exit_only", True))
        self.aggressive_close_min_ticks = max(
            1, int(getattr(cfg, "aggressive_close_min_ticks", 300))
        )
        self.maintenance_size_mult = float(getattr(cfg, "maintenance_size_mult", 0.25))
        self.maintenance_passive_exit_only = bool(
            getattr(cfg, "maintenance_passive_exit_only", True)
        )
        self.log_mm_strategy = bool(getattr(cfg, "log_mm_strategy", True))
        self.log_book_memory = bool(getattr(cfg, "log_book_memory", False))
        self.mm_expiry_period = int(getattr(cfg, "mm_expiry_period_ns", 500_000_000))
        self.flow_depth = max(1, int(getattr(cfg, "flow_depth", 5)))
        self.min_expected_alpha = float(getattr(cfg, "min_expected_alpha", 0.18))
        self.max_mm_books_per_tick = max(
            1, int(getattr(cfg, "max_mm_books_per_tick", 4))
        )
        self.max_managed_books_per_tick = max(
            1, int(getattr(cfg, "max_managed_books_per_tick", 4))
        )
        self.mm_skip_inactive_tier = bool(getattr(cfg, "mm_skip_inactive_tier", True))
        self.toxic_loss_streak = max(1, int(getattr(cfg, "toxic_loss_streak", 4)))
        self.toxic_recent_pnl = float(getattr(cfg, "toxic_recent_pnl", -0.01))
        self.toxic_spread_bps = float(getattr(cfg, "toxic_spread_bps", 10.0))
        self.w_micro = float(getattr(cfg, "w_micro", 0.50))
        self.w_micro_vel = float(getattr(cfg, "w_micro_vel", 0.40))
        self.w_deep = float(getattr(cfg, "w_deep", 0.38))
        self.w_persist = float(getattr(cfg, "w_persist", 0.32))
        self.deep_imbalance_end = max(2, int(getattr(cfg, "deep_imbalance_end", 5)))
        self.trade_persistence_len = max(
            5, int(getattr(cfg, "trade_persistence_len", 20))
        )
        self.micro_vel_scale = float(getattr(cfg, "micro_vel_scale", 8.0))
        self.direction_accuracy_weight = float(
            getattr(cfg, "direction_accuracy_weight", 0.12)
        )
        self.book_specialization_weight = float(
            getattr(cfg, "book_specialization_weight", 0.22)
        )
        self.fill_learn_blend = float(getattr(cfg, "fill_learn_blend", 0.45))
        self.fill_learn_min_samples = max(
            3, int(getattr(cfg, "fill_learn_min_samples", 5))
        )
        self.coverage_boost_weight = float(getattr(cfg, "coverage_boost_weight", 0.08))
        self.min_expected_realized_pnl = float(
            getattr(cfg, "min_expected_realized_pnl", 0.0)
        )
        self.enable_auto_tuning = bool(getattr(cfg, "enable_auto_tuning", False))
        self.allow_tuning_config = bool(getattr(cfg, "allow_tuning_config", False))
        self.tuning_interval_ns = int(getattr(cfg, "tuning_interval_ns", 3_600_000_000_000))
        self.log_tuning = bool(getattr(cfg, "log_tuning", True))
        _tuning_path = getattr(cfg, "tuning_config_path", None)
        self._tuning_config_path = (
            str(_tuning_path)
            if _tuning_path
            else os.path.join(self.output_dir, "tuning.json")
        )

        self._position_ticks: dict[int, int] = {}
        self._inventory_reason: dict[int, InventoryReason] = {}
        self._micro_prev: dict[int, float] = {}
        self._dir_pending: dict[int, dict] = {}
        self._trade_signs: dict[int, Deque[float]] = {}
        self._trade_signs_tick: dict[int, int] = {}
        self.book_memory: dict[int, BookMemory] = {}
        self._last_mm_stats: dict = {}
        self._tuning_window: dict[str, int] = {}
        self._last_tuning_ts: int = 0
        self._last_tuning_objective: float = 0.0
        self._tuning_config_mtime: float = 0.0
        self.monitor_top_miners = bool(getattr(cfg, "monitor_top_miners", False))
        self.monitor_top_n = max(1, int(getattr(cfg, "monitor_top_n", 5)))

        if self.allow_tuning_config and os.path.isfile(self._tuning_config_path):
            self._reload_tuning_config_if_changed(force=True)
        self._clamp_tuning_params()

        bt.logging.info(
            f"Strategy1: mm={self.enable_mm_strategy} "
            f"base_size={self.mm_base_size} max_inv={self.max_inventory_base} "
            f"min_alpha={self.min_expected_alpha} max_mm_books={self.max_mm_books_per_tick} "
            f"max_managed={self.max_managed_books_per_tick} skip_inactive_mm={self.mm_skip_inactive_tier} "
            f"inv_close_thr={self.inventory_close_threshold} "
            f"close_score={self.close_score_threshold} "
            f"passive_exit={self.passive_exit_only} "
            f"agg_min_ticks={self.aggressive_close_min_ticks} "
            f"maint_size_mult={self.maintenance_size_mult} "
            f"min_exp_pnl={self.min_expected_realized_pnl} "
            f"fast_update={self.fast_update} sync_csv={self.sync_event_csv} "
            f"log_latency={self.log_latency} history_len={self.history_len} "
            f"coverage_w={self.coverage_boost_weight} max_mm={self.max_mm_books_per_tick} "
            f"w_micro_vel={self.w_micro_vel} w_deep={self.w_deep} w_persist={self.w_persist} "
            f"fill_learn={self.fill_learn_blend} spec_w={self.book_specialization_weight} "
            f"toxic_streak={self.toxic_loss_streak} "
            f"auto_tune={self.enable_auto_tuning} monitor_top={self.monitor_top_miners}"
        )

    def handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        """Instrument update/respond/report phases when log_latency is enabled."""
        if not self.log_latency:
            return super().handle(state)
        t0 = time.perf_counter()
        self.update(state)
        t_update = time.perf_counter() - t0
        t1 = time.perf_counter()
        response = self.respond(state)
        t_respond = time.perf_counter() - t1
        t2 = time.perf_counter()
        self.report(state, response)
        t_report = time.perf_counter() - t2
        if self._tick == 1 or self._tick % self.log_every_n == 0:
            notices = len((state.notices or {}).get(self.uid, []))
            bt.logging.info(
                f"[LATENCY] tick={self._tick} update={round(t_update, 3)}s "
                f"respond={round(t_respond, 3)}s report={round(t_report, 3)}s "
                f"total={round(t_update + t_respond + t_report, 3)}s "
                f"notices={notices} ix={len(response.instructions)}"
            )
        return response

    def _passes_expected_pnl_gate(self, expected_realized_pnl: float) -> bool:
        return expected_realized_pnl > self.min_expected_realized_pnl

    def _mem(self, book_id: int) -> BookMemory:
        return self.book_memory.setdefault(book_id, BookMemory())

    def _spread_dist_bucket(self, dist_from_touch: float) -> int:
        """Map spread-normalized distance (0=touch, higher=inside book) to bucket 0..2."""
        if dist_from_touch <= 0.22:
            return 0
        if dist_from_touch <= 0.40:
            return 1
        return 2

    def _record_fill_quote(
        self,
        mem: BookMemory,
        side: Literal["buy", "sell"],
        dist_from_touch: float,
    ) -> None:
        bucket = self._spread_dist_bucket(dist_from_touch)
        if side == "buy":
            q = list(mem.fill_buy_quotes)
            q[bucket] += 1
            mem.fill_buy_quotes = tuple(q)
            mem.last_buy_dist_bucket = bucket
        else:
            q = list(mem.fill_sell_quotes)
            q[bucket] += 1
            mem.fill_sell_quotes = tuple(q)
            mem.last_sell_dist_bucket = bucket

    def _record_fill_hit(self, mem: BookMemory, side: Literal["buy", "sell"]) -> None:
        if side == "buy":
            f = list(mem.fill_buy_fills)
            f[mem.last_buy_dist_bucket] += 1
            mem.fill_buy_fills = tuple(f)
        else:
            f = list(mem.fill_sell_fills)
            f[mem.last_sell_dist_bucket] += 1
            mem.fill_sell_fills = tuple(f)

    def _learned_side_fill_prob(
        self,
        mem: BookMemory,
        side: Literal["buy", "sell"],
        dist_from_touch: float,
    ) -> float | None:
        bucket = self._spread_dist_bucket(dist_from_touch)
        if side == "buy":
            quotes, fills = mem.fill_buy_quotes[bucket], mem.fill_buy_fills[bucket]
        else:
            quotes, fills = mem.fill_sell_quotes[bucket], mem.fill_sell_fills[bucket]
        if quotes < self.fill_learn_min_samples:
            return None
        return fills / quotes

    def _update_direction_accuracy(self, book_id: int, mid: float) -> None:
        pending = self._dir_pending.get(book_id)
        if not pending or mid <= 0 or pending.get("mid", 0) <= 0:
            return
        log_ret = math.log(mid / pending["mid"])
        thr = max(self.momentum_scale, 1e-9)
        pred = pending.get("direction", "HOLD")
        if pred == "HOLD":
            return
        mem = self._mem(book_id)
        if pred == "UP":
            if log_ret > thr:
                mem.direction_hits += 1
            elif log_ret < -thr:
                mem.direction_misses += 1
        elif pred == "DOWN":
            if log_ret < -thr:
                mem.direction_hits += 1
            elif log_ret > thr:
                mem.direction_misses += 1

    def _compute_l2_l5_imbalance(self, book: Book) -> float:
        """Deep-book imbalance on L2–L5 (exclude touch). Positive = bid-heavy."""
        if not book.bids and not book.asks:
            return 0.0
        bid_vol = 0.0
        ask_vol = 0.0
        if book.bids:
            for i in range(1, min(self.deep_imbalance_end, len(book.bids))):
                bid_vol += book.bids[i].quantity
        if book.asks:
            for i in range(1, min(self.deep_imbalance_end, len(book.asks))):
                ask_vol += book.asks[i].quantity
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        return max(-1.0, min(1.0, (bid_vol - ask_vol) / total))

    def _update_trade_sign_history(
        self,
        book_id: int,
        book: Book,
        timestamp: int,
    ) -> None:
        if self._trade_signs_tick.get(book_id) == timestamp:
            return
        self._trade_signs_tick[book_id] = timestamp
        hist = self._trade_signs.setdefault(
            book_id, deque(maxlen=self.trade_persistence_len),
        )
        for event in book.events or []:
            etype = getattr(event, "type", None)
            if etype not in ("t", "EVENT_TRADE", "ET"):
                continue
            side = getattr(event, "side", None)
            if side == 0:
                hist.append(1.0)
            elif side == 1:
                hist.append(-1.0)

    def _trade_persistence(self, book_id: int) -> float:
        hist = self._trade_signs.get(book_id)
        if not hist:
            return 0.0
        return max(-1.0, min(1.0, sum(hist) / len(hist)))

    def _kappa_factor_from_tier(self, tier: str) -> float:
        return {
            "GREEN": 1.0,
            "YELLOW": 0.65,
            "RED": 0.25,
            "INACTIVE": 0.35,
        }.get(tier, 0.5)

    def _sync_kappa_factor(self, mem: BookMemory, profile: BookProfile) -> None:
        k = self._kappa_factor_from_tier(profile.tier)
        mem.book_kappa_factor = 0.85 * mem.book_kappa_factor + 0.15 * k

    def _update_book_specialization(self, mem: BookMemory) -> None:
        mem.book_fill_factor = 0.88 * mem.book_fill_factor + 0.12 * mem.fill_rate
        pnl_component = max(0.0, min(1.0, 0.5 + mem.recent_pnl * 50.0))
        profit_blend = 0.65 * mem.win_rate + 0.35 * pnl_component
        mem.book_profit_factor = 0.90 * mem.book_profit_factor + 0.10 * profit_blend

    def _global_book_rank(
        self,
        expected_alpha: float,
        mem: BookMemory,
    ) -> float:
        spec = mem.specialization_score
        return expected_alpha * (0.72 + 0.28 * spec) + 0.12 * spec

    def _reset_pnl_state(self) -> None:
        super()._reset_pnl_state()
        self._position_ticks.clear()
        self._inventory_reason.clear()
        self._micro_prev.clear()
        self._dir_pending.clear()
        self._trade_signs.clear()
        self._trade_signs_tick.clear()
        self.book_memory.clear()
        self._last_mm_stats = {}
        self._tuning_window = {}
        self._last_tuning_ts = 0
        self._last_tuning_objective = 0.0

    def _snapshot_tuning_params(self) -> dict[str, float | int]:
        return {
            "min_expected_alpha": self.min_expected_alpha,
            "min_expected_realized_pnl": self.min_expected_realized_pnl,
            "max_mm_books_per_tick": self.max_mm_books_per_tick,
            "toxic_loss_streak": self.toxic_loss_streak,
            "toxic_recent_pnl": self.toxic_recent_pnl,
            "coverage_boost_weight": self.coverage_boost_weight,
        }

    def _clamp_tuning_params(self) -> None:
        for key, (lo, hi) in TUNING_PARAM_BOUNDS.items():
            val = getattr(self, key)
            if key in ("max_mm_books_per_tick", "toxic_loss_streak"):
                setattr(self, key, int(max(lo, min(hi, val))))
            else:
                setattr(self, key, max(lo, min(hi, val)))

    def _apply_tuning_overrides(self, overrides: dict) -> list[str]:
        applied: list[str] = []
        for key, raw in overrides.items():
            if key not in TUNING_PARAM_BOUNDS:
                continue
            lo, hi = TUNING_PARAM_BOUNDS[key]
            if key in ("max_mm_books_per_tick", "toxic_loss_streak"):
                val = int(max(lo, min(hi, float(raw))))
            else:
                val = max(lo, min(hi, float(raw)))
            setattr(self, key, val)
            applied.append(key)
        return applied

    def _reload_tuning_config_if_changed(self, force: bool = False) -> list[str]:
        path = self._tuning_config_path
        if not path or not os.path.isfile(path):
            return []
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return []
        if not force and mtime <= self._tuning_config_mtime:
            return []
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            bt.logging.warning(f"[TUNING] Failed to read {path}: {exc}")
            return []
        if not isinstance(data, dict):
            return []
        overrides = data.get("params", data)
        if not isinstance(overrides, dict):
            return []
        applied = self._apply_tuning_overrides(overrides)
        self._tuning_config_mtime = mtime
        self._clamp_tuning_params()
        if applied and self.log_tuning:
            bt.logging.info(
                f"[TUNING] hot-reload {path} applied={applied} "
                f"snapshot={json.dumps(self._snapshot_tuning_params())}"
            )
        return applied

    def _accumulate_tuning_window(self, stats: dict) -> None:
        if not self.enable_auto_tuning:
            return
        self._tuning_window["ticks"] = self._tuning_window.get("ticks", 0) + 1
        for key in (
            "skipped_negative_pnl",
            "skipped_low_alpha",
            "skipped_toxic",
            "quoted",
            "maintenance",
            "instructions",
        ):
            self._tuning_window[key] = (
                self._tuning_window.get(key, 0) + int(stats.get(key, 0))
            )

    def _aggregate_book_memory_win_rate(self) -> float:
        wins = sum(m.win_count for m in self.book_memory.values())
        losses = sum(m.loss_count for m in self.book_memory.values())
        return wins / max(wins + losses, 1)

    def _compute_tuning_metrics(self) -> TuningMetrics:
        kappa_med = self._estimate_local_normalized_median() or 0.0
        win_rate = self._aggregate_book_memory_win_rate()
        ticks = max(1, self._tuning_window.get("ticks", 1))
        skip_neg = self._tuning_window.get("skipped_negative_pnl", 0)
        skip_neg_rate = min(1.0, skip_neg / ticks)
        objective = (
            0.50 * kappa_med
            + 0.30 * win_rate
            - 0.20 * skip_neg_rate
        )
        return TuningMetrics(
            kappa_med=kappa_med,
            win_rate=win_rate,
            skip_neg_rate=skip_neg_rate,
            objective=objective,
            window_ticks=ticks,
        )

    def _apply_tuning_rules(self, metrics: TuningMetrics) -> None:
        if metrics.skip_neg_rate > 0.12:
            self.min_expected_alpha = min(0.45, self.min_expected_alpha + 0.02)
            self.min_expected_realized_pnl = min(
                0.002, self.min_expected_realized_pnl + 0.00005,
            )
            self.max_mm_books_per_tick = max(4, self.max_mm_books_per_tick - 1)

        if metrics.win_rate < 0.45:
            self.min_expected_alpha = min(0.45, self.min_expected_alpha + 0.02)
            self.toxic_loss_streak = max(2, self.toxic_loss_streak - 1)
            self.toxic_recent_pnl = min(-0.001, self.toxic_recent_pnl - 0.002)

        if (
            metrics.kappa_med > 0.55
            and metrics.win_rate > 0.50
            and metrics.skip_neg_rate < 0.05
        ):
            self.max_mm_books_per_tick = min(12, self.max_mm_books_per_tick + 1)
            self.min_expected_alpha = max(0.15, self.min_expected_alpha - 0.01)

        if (
            self._last_tuning_objective > 0.0
            and metrics.objective < self._last_tuning_objective - 0.04
        ):
            self.max_mm_books_per_tick = max(4, self.max_mm_books_per_tick - 1)
            self.min_expected_alpha = min(0.45, self.min_expected_alpha + 0.03)

        self._clamp_tuning_params()

    def _persist_tuning_state(self, metrics: TuningMetrics) -> None:
        path = os.path.join(self.output_dir, "tuning_state.json")
        history: list[dict] = []
        if os.path.isfile(path):
            try:
                with open(path, encoding="utf-8") as f:
                    payload = json.load(f)
                history = payload.get("history", [])
            except (OSError, json.JSONDecodeError):
                history = []
        entry = {
            "objective": round(metrics.objective, 6),
            "kappa_med": round(metrics.kappa_med, 4),
            "win_rate": round(metrics.win_rate, 4),
            "skip_neg_rate": round(metrics.skip_neg_rate, 4),
            "window_ticks": metrics.window_ticks,
            "params": self._snapshot_tuning_params(),
        }
        history.append(entry)
        if len(history) > 96:
            history = history[-96:]
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({"history": history, "last": entry}, f, indent=2)
        except OSError as exc:
            bt.logging.warning(f"[TUNING] Failed to write {path}: {exc}")

    def _maybe_run_tuning_scheduler(
        self,
        state: MarketSimulationStateUpdate,
    ) -> None:
        if not self.enable_auto_tuning:
            return
        now = state.timestamp
        if self._last_tuning_ts <= 0:
            self._last_tuning_ts = now
            return
        if now - self._last_tuning_ts < self.tuning_interval_ns:
            return

        self._reload_tuning_config_if_changed()
        kappa_values = self._compute_local_kappa(state)
        if kappa_values:
            self._last_kappa = kappa_values

        metrics = self._compute_tuning_metrics()
        self._apply_tuning_rules(metrics)
        self._reload_tuning_config_if_changed()
        self._persist_tuning_state(metrics)

        if self.log_tuning:
            bt.logging.info(
                f"[TUNING] step objective={round(metrics.objective, 4)} "
                f"kappa_med={round(metrics.kappa_med, 4)} "
                f"win_rate={round(metrics.win_rate, 4)} "
                f"skip_neg_rate={round(metrics.skip_neg_rate, 4)} "
                f"window_ticks={metrics.window_ticks} "
                f"params={json.dumps(self._snapshot_tuning_params())}"
            )

        self._last_tuning_objective = metrics.objective
        self._tuning_window = {}
        self._last_tuning_ts = now

    def _reason_from_client_id(self, client_id: int) -> InventoryReason:
        if client_id >= ALPHA_CLIENT_ID_BASE:
            return "ALPHA"
        if client_id >= MM_CLIENT_ID_BASE:
            return "MM"
        if client_id >= MAINT_CLIENT_ID_BASE:
            return "MAINTENANCE"
        return "UNKNOWN"

    def _inventory_util(self, inventory: InventorySnapshot) -> float:
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-9)
        return abs(inventory.inventory_ratio) / max(max_ratio, 1e-9)

    def _inventory_needs_management(self, inventory: InventorySnapshot) -> bool:
        if inventory.band in ("MAX_LONG", "MAX_SHORT"):
            return True
        return self._inventory_util(inventory) >= self.inventory_close_threshold

    def _maintenance_allowed(
        self,
        profile: BookProfile,
        archetype: BookArchetype,
    ) -> bool:
        if archetype in ("WALL_BOOK", "TOXIC_BOOK", "TREND_BOOK", "STRESSED", "DEAD_BOOK"):
            return False
        if archetype == "MM_BOOK":
            return True
        if profile.tier == "GREEN":
            return True
        return False

    def _allows_aggressive_close(
        self,
        book_id: int,
        inventory: InventorySnapshot,
        close_score: float,
        time_stop: bool,
        stop_loss_hit: bool,
    ) -> bool:
        if stop_loss_hit or inventory.band in ("MAX_LONG", "MAX_SHORT"):
            return True

        reason = self._inventory_reason.get(book_id, inventory.reason)
        if reason == "MAINTENANCE" and self.maintenance_passive_exit_only:
            return False

        if self.passive_exit_only and inventory.position_ticks < self.aggressive_close_min_ticks:
            return False

        if close_score >= self.close_score_threshold:
            return True
        if time_stop:
            return True
        return False

    def onTrade(self, event, validator: str | None = None) -> None:
        book_id = event.bookId
        pnl_before = 0.0
        net_before = 0.0
        flat_eps = self.mm_base_size * 1e-3
        if book_id is not None:
            pnl_before = self._pnl_tick_buffer.get(book_id, 0.0)
            net_before = self._position_tracker_snapshot(book_id).net_qty
        super().onTrade(event, validator)
        if book_id is None:
            return
        is_taker = self.uid == event.takerAgentId
        is_maker = self.uid == event.makerAgentId
        if not is_taker and not is_maker:
            return

        net_after = self._position_tracker_snapshot(book_id).net_qty
        if abs(net_after) < flat_eps:
            self._inventory_reason.pop(book_id, None)
        elif abs(net_after) > abs(net_before) or (
            abs(net_before) < flat_eps and abs(net_after) >= flat_eps
        ):
            if is_maker and event.clientOrderId is not None:
                self._inventory_reason[book_id] = self._reason_from_client_id(
                    event.clientOrderId
                )
            elif is_taker:
                self._inventory_reason[book_id] = "MARKET"

        mem = self._mem(book_id)
        mem.fill_count += 1
        mem.last_activity_ts = event.timestamp
        if is_maker:
            agent_buy = (is_taker and event.side == 0) or (is_maker and event.side == 1)
            self._record_fill_hit(mem, "buy" if agent_buy else "sell")
        realized_pnl = self._pnl_tick_buffer.get(book_id, 0.0) - pnl_before
        if realized_pnl != 0.0:
            mem.recent_pnl = 0.9 * mem.recent_pnl + 0.1 * realized_pnl
            if realized_pnl > 0:
                mem.win_count += 1
                mem.loss_streak = 0
            elif realized_pnl < 0:
                mem.loss_count += 1
                mem.loss_streak += 1
        self._update_book_specialization(mem)

    def microprice_signal(self, book: Book) -> float:
        if not book.bids or not book.asks:
            return 0.0
        bid = book.bids[0].price
        ask = book.asks[0].price
        bid_qty = book.bids[0].quantity
        ask_qty = book.asks[0].quantity
        mid = 0.5 * (bid + ask)
        micro = (ask * bid_qty + bid * ask_qty) / max(bid_qty + ask_qty, 1e-9)
        spread = ask - bid
        if spread <= 0:
            return 0.0
        return max(-1.0, min(1.0, (micro - mid) / spread))

    def predict_direction(
        self,
        book_id: int,
        book: Book,
        timestamp: int,
    ) -> DirectionForecast:
        mid = self._book_mid(book)
        if mid > 0:
            self._update_direction_accuracy(book_id, mid)
        log_return, _ = self._update_momentum(book_id, timestamp, mid)
        momentum_m = self._normalize_momentum(log_return)
        flow_f = self._compute_flow_f(book)
        trade_t, trade_imbalance = self._compute_trade_t(book)
        deep_imb = self._compute_l2_l5_imbalance(book)
        self._update_trade_sign_history(book_id, book, timestamp)
        trade_persist = self._trade_persistence(book_id)
        micro = self.microprice_signal(book)
        micro_prev = self._micro_prev.get(book_id, micro)
        micro_vel = micro - micro_prev
        self._micro_prev[book_id] = micro
        micro_vel_sig = max(
            -1.0,
            min(1.0, micro_vel * self.micro_vel_scale),
        )
        imbalance = 0.55 * flow_f + 0.45 * deep_imb
        score = (
            self.w_m * momentum_m
            + self.w_f * flow_f
            + self.w_t * trade_t
            + self.w_micro * micro
            + self.w_micro_vel * micro_vel_sig
            + self.w_deep * deep_imb
            + self.w_persist * trade_persist
        )
        self._mem(book_id).last_signal = score
        if score > self.direction_threshold:
            direction: Literal["UP", "DOWN", "HOLD"] = "UP"
        elif score < -self.direction_threshold:
            direction = "DOWN"
        else:
            direction = "HOLD"
        if mid > 0:
            self._dir_pending[book_id] = {
                "direction": direction,
                "mid": mid,
                "timestamp": timestamp,
            }
        return DirectionForecast(
            book_id=book_id,
            direction=direction,
            score=score,
            momentum_m=momentum_m,
            flow_f=flow_f,
            trade_t=trade_t,
            log_return=log_return,
            imbalance=imbalance,
            trade_imbalance=trade_imbalance,
        )

    def coverage_priority(self, book_id: int, now: int) -> float:
        mem = self._mem(book_id)
        if mem.last_activity_ts <= 0:
            return 1.0
        age = max(0, now - mem.last_activity_ts)
        return min(1.0, age / max(self.pnl_lookback_ns, 1))

    def is_toxic_book(
        self,
        book_id: int,
        profile: BookProfile,
        archetype: BookArchetype,
    ) -> bool:
        mem = self._mem(book_id)
        return (
            mem.loss_streak >= self.toxic_loss_streak
            or mem.recent_pnl < self.toxic_recent_pnl
            or (
                profile.spread_bps is not None
                and profile.spread_bps > self.toxic_spread_bps
            )
            or archetype == "STRESSED"
            or profile.tier == "RED"
        )

    def _tier_mm_boost(self, tier: str) -> float:
        """Prefer MM on books with kappa history (GREEN/YELLOW) over cold INACTIVE."""
        return {
            "GREEN": 0.18,
            "YELLOW": 0.10,
            "RED": -0.05,
            "INACTIVE": -0.12,
        }.get(tier, 0.0)

    def _inventory_urgency(
        self,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ) -> float:
        close_score = self._compute_close_score(
            inventory, regime_params, regime, archetype,
        )
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-9)
        inv_risk = min(1.0, abs(inventory.inventory_ratio) / max(max_ratio, 1e-9))
        time_risk = min(1.0, inventory.position_ticks / self.position_max_ticks)
        loss_risk = 0.0
        if inventory.unrealized_bps is not None and inventory.unrealized_bps < 0:
            loss_risk = min(1.0, abs(inventory.unrealized_bps) / regime_params.stop_loss_bps)
        return close_score + inv_risk + time_risk + loss_risk

    def expected_alpha_score(
        self,
        profile: BookProfile,
        prediction: DirectionForecast,
        fill_est: FillProbabilityEstimate,
        mem: BookMemory,
        book_id: int,
        now: int,
    ) -> float:
        self._sync_kappa_factor(mem, profile)
        signal = min(1.0, abs(prediction.score))
        fill = 0.5 * (fill_est.buy + fill_est.sell)
        memory_bonus = 0.5 * mem.win_rate + 0.5 * mem.fill_rate
        accuracy_bonus = mem.direction_accuracy
        pnl_bonus = max(-1.0, min(1.0, mem.recent_pnl * 100))
        coverage = self.coverage_priority(book_id, now)
        tier_boost = self._tier_mm_boost(profile.tier)
        specialization = mem.specialization_score
        score = (
            0.26 * signal
            + 0.20 * fill
            + 0.12 * memory_bonus
            + self.direction_accuracy_weight * accuracy_bonus
            + 0.06 * pnl_bonus
            + self.coverage_boost_weight * coverage
            + self.book_specialization_weight * specialization
            + tier_boost
        )
        mem.last_expected_alpha = score
        return score

    def _schedule_maintenance_books(
        self,
        selection: BookSelection,
        now: int,
        limit: int | None = None,
    ) -> list[int]:
        cap = limit if limit is not None else self.max_maintenance_books_per_tick
        candidates = list(selection.maintenance_books)
        inactive_ids = [
            p.book_id for p in selection.profiles if p.tier == "INACTIVE"
        ]
        for book_id in inactive_ids:
            if book_id not in candidates:
                candidates.append(book_id)
        candidates.sort(key=lambda b: self.coverage_priority(b, now), reverse=True)
        return candidates[:cap]

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
                buy_bias=params.buy_bias,
                sell_bias=params.sell_bias,
            )
        return params

    def merge_regime_and_archetype_params(
        self,
        regime_params: RegimeParamSet,
        archetype: BookArchetype,
    ) -> RegimeParamSet:
        adj = DEFAULT_ARCHETYPE_ADJUST.get(archetype, ArchetypeAdjust())
        quote_enabled = (
            adj.quote_enabled_override
            if adj.quote_enabled_override is not None
            else regime_params.quote_enabled
        )
        return RegimeParamSet(
            quote_enabled=quote_enabled,
            alpha_enabled=regime_params.alpha_enabled,
            spread_offset=max(0.05, regime_params.spread_offset + adj.spread_offset_delta),
            skew_strength=regime_params.skew_strength * adj.skew_strength_mult,
            size_mult=regime_params.size_mult * adj.size_mult,
            profit_target_bps=regime_params.profit_target_bps,
            stop_loss_bps=regime_params.stop_loss_bps,
            min_fill_prob=max(
                0.05,
                min(0.95, regime_params.min_fill_prob + adj.min_fill_prob_delta),
            ),
            buy_bias=regime_params.buy_bias,
            sell_bias=regime_params.sell_bias,
        )

    def get_archetype_edge_bias(self, archetype: BookArchetype) -> float:
        adj = DEFAULT_ARCHETYPE_ADJUST.get(archetype, ArchetypeAdjust())
        return adj.edge_bias

    def classify_book_archetype(
        self,
        profile: BookProfile,
        regime: MarketRegime,
    ) -> BookArchetype:
        spread_bps = profile.spread_bps or 0.0
        if spread_bps >= self.archetype_stressed_spread_bps or regime.mode == "STRESSED":
            return "STRESSED"
        if profile.trade_rate < self.archetype_dead_trade_rate:
            return "DEAD_BOOK"
        if spread_bps < self.archetype_mm_spread_bps:
            return "MM_BOOK"
        if abs(profile.imbalance) >= self.archetype_wall_imbalance:
            return "WALL_BOOK"
        if (
            profile.volatility >= self.archetype_vol_threshold
            and abs(profile.predict_score) >= self.direction_threshold
        ):
            return "TREND_BOOK"
        if profile.volatility >= self.archetype_vol_threshold:
            return "TOXIC_BOOK"
        if abs(profile.predict_score) >= self.direction_threshold:
            return "TREND_BOOK"
        return "TOXIC_BOOK"

    def _position_tracker_snapshot(self, book_id: int) -> PositionTracker:
        pos = self._open_positions.get(book_id)
        if not pos:
            return PositionTracker(0.0, None, None, 0.0, 0.0)
        long_qty = sum(q for _, q, _, _ in pos["longs"])
        short_qty = sum(q for _, q, _, _ in pos["shorts"])
        net_qty = long_qty - short_qty
        vwap: float | None = None
        opened_at: int | None = None
        if net_qty > 0 and long_qty > 0:
            vwap = sum(q * p for _, q, p, _ in pos["longs"]) / long_qty
            opened_at = pos["longs"][0][0]
        elif net_qty < 0 and short_qty > 0:
            vwap = sum(q * p for _, q, p, _ in pos["shorts"]) / short_qty
            opened_at = pos["shorts"][0][0]
        return PositionTracker(net_qty, vwap, opened_at, long_qty, short_qty)

    def _wealth_per_book(self) -> float:
        if not self.simulation_config:
            return 0.0
        return self.simulation_config.miner_wealth / max(
            self.simulation_config.book_count, 1
        )

    def _net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        if mid <= 0:
            return InventorySnapshot(0.0, 0.0, "FLAT", None, None, 0)

        tracker = self._position_tracker_snapshot(book_id)
        net_base = tracker.net_qty
        wealth_per_book = self._wealth_per_book()
        inventory_ratio = 0.0
        if wealth_per_book > 0:
            inventory_ratio = (net_base * mid) / wealth_per_book

        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-9)
        flat_eps = self.mm_base_size * 1e-3

        if abs(net_base) < flat_eps:
            band: InventoryBand = "FLAT"
            self._position_ticks.pop(book_id, None)
            self._inventory_reason.pop(book_id, None)
        elif net_base > 0:
            band = "MAX_LONG" if inventory_ratio >= max_ratio else "LONG"
            self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
        else:
            band = "MAX_SHORT" if abs(inventory_ratio) >= max_ratio else "SHORT"
            self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1

        position_ticks = self._position_ticks.get(book_id, 0)
        vwap = tracker.vwap_entry
        unrealized_bps: float | None = None
        if vwap and vwap > 0:
            if net_base > 0:
                unrealized_bps = ((mid - vwap) / vwap) * 10_000.0
            elif net_base < 0:
                unrealized_bps = ((vwap - mid) / vwap) * 10_000.0

        return InventorySnapshot(
            net_base=net_base,
            inventory_ratio=inventory_ratio,
            band=band,
            vwap_entry=vwap,
            unrealized_bps=unrealized_bps,
            position_ticks=position_ticks,
            opened_at_ns=tracker.opened_at_ns,
            reason=self._inventory_reason.get(book_id, "UNKNOWN"),
        )

    def _log_book_memory_sample(self, state: MarketSimulationStateUpdate) -> None:
        rows: list[dict] = []
        for book_id in sorted(state.books.keys())[:10]:
            mem = self._mem(book_id)
            pos = self._position_tracker_snapshot(book_id)
            rows.append({
                "book": book_id,
                "win": round(mem.win_rate, 3),
                "fill": round(mem.fill_rate, 3),
                "pnl": round(mem.recent_pnl, 5),
                "streak": mem.loss_streak,
                "quotes": mem.quote_count,
                "fills": mem.fill_count,
                "net": round(pos.net_qty, 4),
                "vwap": round(pos.vwap_entry or 0.0, 4),
                "ea": round(mem.last_expected_alpha, 4),
                "dir_acc": round(mem.direction_accuracy, 3),
                "profit_f": round(mem.book_profit_factor, 3),
                "fill_f": round(mem.book_fill_factor, 3),
                "kappa_f": round(mem.book_kappa_factor, 3),
                "spec": round(mem.specialization_score, 3),
                "fb": list(mem.fill_buy_fills),
                "fs": list(mem.fill_sell_fills),
            })
        bt.logging.info(f"[BOOK_MEMORY] {json.dumps(rows)}")

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
        if spread <= 0 or mid <= 0:
            return FillProbabilityEstimate(0.0, 0.0)

        trade_factor = min(1.0, trade_rate / max(self.trade_rate_ref, 1e-9))

        bid_depth = book.bids[0].quantity if book.bids else 0.0
        ask_depth = book.asks[0].quantity if book.asks else 0.0
        deep_bid = bid_depth
        deep_ask = ask_depth
        if book.bids:
            deep_bid = sum(
                book.bids[i].quantity
                for i in range(min(self.deep_imbalance_end, len(book.bids)))
            )
        if book.asks:
            deep_ask = sum(
                book.asks[i].quantity
                for i in range(min(self.deep_imbalance_end, len(book.asks)))
            )
        total_bid = deep_bid if deep_bid > 0 else (
            sum(l.quantity for l in book.bids) if book.bids else bid_depth
        )
        total_ask = deep_ask if deep_ask > 0 else (
            sum(l.quantity for l in book.asks) if book.asks else ask_depth
        )

        buy_dist = (mid - buy_price) / spread
        sell_dist = (sell_price - mid) / spread
        buy_depth_f = bid_depth / max(total_bid, 1e-9)
        sell_depth_f = ask_depth / max(total_ask, 1e-9)

        depth_buy = trade_rate / max(bid_depth + 1.0, 1e-9)
        depth_sell = trade_rate / max(ask_depth + 1.0, 1e-9)
        depth_buy = min(1.0, depth_buy / max(self.trade_rate_ref, 1e-9))
        depth_sell = min(1.0, depth_sell / max(self.trade_rate_ref, 1e-9))

        dist_buy = max(0.0, 1.0 - buy_dist)
        dist_sell = max(0.0, 1.0 - sell_dist)

        p_buy = trade_factor * (
            0.25 * buy_depth_f + 0.35 * dist_buy + 0.40 * depth_buy
        )
        p_sell = trade_factor * (
            0.25 * sell_depth_f + 0.35 * dist_sell + 0.40 * depth_sell
        )
        if book_id is not None:
            mem = self._mem(book_id)
            mem_blend = 0.10
            p_buy = (1.0 - mem_blend) * p_buy + mem_blend * mem.fill_rate
            p_sell = (1.0 - mem_blend) * p_sell + mem_blend * mem.fill_rate
            buy_touch_dist = max(0.0, 0.5 - buy_dist)
            sell_touch_dist = max(0.0, 0.5 - sell_dist)
            learned_buy = self._learned_side_fill_prob(mem, "buy", buy_touch_dist)
            learned_sell = self._learned_side_fill_prob(mem, "sell", sell_touch_dist)
            if learned_buy is not None:
                p_buy = (
                    (1.0 - self.fill_learn_blend) * p_buy
                    + self.fill_learn_blend * learned_buy
                )
            if learned_sell is not None:
                p_sell = (
                    (1.0 - self.fill_learn_blend) * p_sell
                    + self.fill_learn_blend * learned_sell
                )
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
        mid: float | None = None,
    ) -> float:
        confidence = max(0.5, min(2.0, 1.0 + abs(profile.predict_score)))

        vol_scale = 1.0
        if profile.volatility > 0:
            target_vol = self.profile_vol_scale
            vol_scale = max(0.5, min(2.0, target_vol / profile.volatility))

        spread_factor = 1.0
        if profile.spread is not None and mid is not None and mid > 0:
            spread_bps = (profile.spread / mid) * 10_000.0
            spread_factor = max(0.5, min(1.5, 1.0 - spread_bps / 20.0))

        kappa_scale = 1.0
        if profile.raw_kappa is not None:
            kappa_scale = max(0.5, min(1.5, 1.0 + profile.raw_kappa * 0.2))

        inv_util = abs(inventory.inventory_ratio)
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-9)
        inventory_factor = max(0.3, 1.0 - inv_util / max(max_ratio, 1e-9))

        size = (
            base_size
            * confidence
            * regime_params.size_mult
            * vol_scale
            * spread_factor
            * kappa_scale
            * inventory_factor
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
        edge_bias: float = 0.0,
    ) -> tuple[float, float] | None:
        spread = ask - bid
        if spread <= 0:
            return None
        mid = 0.5 * (bid + ask)
        offset = regime_params.spread_offset

        inventory_edge = self.target_inventory_ratio - inventory_ratio
        buy_edge = signal + edge_bias + inventory_edge
        sell_edge = -signal - edge_bias - inventory_edge

        buy_skew = regime_params.skew_strength * buy_edge * regime_params.buy_bias
        sell_skew = regime_params.skew_strength * sell_edge * regime_params.sell_bias
        inv_skew = self.inventory_skew_strength * inventory_edge

        bid_px = round(mid - spread * (offset + buy_skew + inv_skew), price_dec)
        ask_px = round(mid + spread * (offset - sell_skew - inv_skew), price_dec)
        if bid_px <= 0 or bid_px >= ask_px:
            return None
        return bid_px, ask_px

    def _compute_close_score(
        self,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ) -> float:
        unreal = inventory.unrealized_bps
        target = max(regime_params.profit_target_bps, 1e-9)
        stop = regime_params.stop_loss_bps

        pnl_component = 0.0
        if unreal is not None:
            if unreal >= target:
                pnl_component = 1.0
            elif unreal <= -stop:
                pnl_component = 1.0
            elif unreal > 0:
                pnl_component = unreal / target
            else:
                pnl_component = abs(unreal) / stop

        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-9)
        inventory_risk = min(1.0, abs(inventory.inventory_ratio) / max(max_ratio, 1e-9))

        regime_risk = 0.0
        if regime.mode == "STRESSED":
            regime_risk = 1.0
        elif archetype in ("TOXIC_BOOK", "WALL_BOOK"):
            regime_risk = 0.6
        elif archetype == "DEAD_BOOK":
            regime_risk = 0.4

        time_risk = min(1.0, inventory.position_ticks / self.position_max_ticks)

        return (
            0.5 * pnl_component
            + 0.3 * inventory_risk
            + 0.2 * max(regime_risk, time_risk)
        )

    def _clear_position_state(self, book_id: int) -> None:
        self._position_ticks.pop(book_id, None)
        self._inventory_reason.pop(book_id, None)

    def _place_passive_inventory_exit(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        book: Book,
        inventory: InventorySnapshot,
        qty: float,
    ) -> int:
        """Single-sided passive exit: ask for long, bid for short."""
        long_pos = inventory.net_base > 0
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        close_px = round(
            book.bids[0].price if close_dir == OrderDirection.BUY else book.asks[0].price,
            state.config.priceDecimals,
        )
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return 0
        account = self.accounts[book_id]
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
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
            return 1
        if close_dir == OrderDirection.BUY and account.quote_balance.free >= qty * close_px:
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
            return 1
        return 0

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

    def _execute_aggressive_close(
        self,
        response: FinanceAgentResponse,
        book_id: int,
        book: Book,
        qty: float,
        long_pos: bool,
    ) -> bool:
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        if not self._passes_fee_gate(book_id, aggressive=True):
            return False
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return False
        account = self.accounts[book_id]
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.market_order(
                book_id=book_id,
                direction=close_dir,
                quantity=qty,
                stp=STP.CANCEL_OLDEST,
                delay=0,
            )
            self._clear_position_state(book_id)
            return True
        if close_dir == OrderDirection.BUY:
            px = book.asks[0].price
            if account.quote_balance.free >= qty * px:
                response.market_order(
                    book_id=book_id,
                    direction=close_dir,
                    quantity=qty,
                    stp=STP.CANCEL_OLDEST,
                    delay=0,
                )
                self._clear_position_state(book_id)
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

        close_score = self._compute_close_score(
            inventory, regime_params, regime, archetype,
        )
        long_pos = inventory.net_base > 0
        time_stop = inventory.position_ticks >= self.position_max_ticks
        stop_loss_hit = (
            inventory.unrealized_bps is not None
            and inventory.unrealized_bps <= -regime_params.stop_loss_bps
        )
        aggressive_close = self._allows_aggressive_close(
            book_id, inventory, close_score, time_stop, stop_loss_hit,
        )

        if aggressive_close:
            if self._execute_aggressive_close(response, book_id, book, qty, long_pos):
                placed += 1
            return placed

        placed += self._place_passive_inventory_exit(
            response, state, book_id, book, inventory, qty,
        )
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
        edge_bias: float,
        stats: dict | None = None,
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
            regime_params, cfg.priceDecimals, edge_bias=edge_bias,
        )
        if not prices:
            return 0
        bid_px, ask_px = prices
        qty = self.dynamic_order_size(
            size, profile, regime_params, inventory, cfg.volumeDecimals, mid=mid,
        )
        if qty <= 0:
            return 0

        fill_est = self.estimate_fill_probability(
            book, mid, spread, profile.trade_rate, bid_px, ask_px, book_id=book_id,
        )
        quote_notional = qty * mid * 2
        if not self._can_add_volume(state, quote_notional):
            return 0

        edge = ask_px - bid_px
        expected_edge = edge * (fill_est.buy + fill_est.sell) / 2.0
        if expected_edge <= 0:
            return 0

        est = self.estimate_round_trip_pnl(
            book_id, bid_px, ask_px, qty,
            is_maker=self._prefer_maker(book_id),
            direction="SYMMETRIC",
            timestamp=state.timestamp,
        )
        adj_pnl = est.expected_realized_pnl * (fill_est.buy + fill_est.sell) / 2.0
        if not self._passes_expected_pnl_gate(est.expected_realized_pnl):
            if stats is not None:
                stats["skipped_negative_pnl"] = stats.get("skipped_negative_pnl", 0) + 1
            if self.log_predict_pnl and self.verbose_log:
                bt.logging.info(
                    f"[PREDICT_PNL] skip book={book_id} expected_pnl="
                    f"{round(est.expected_realized_pnl, 6)} "
                    f"(min={self.min_expected_realized_pnl}) bid={bid_px} ask={ask_px}"
                )
            return 0
        if (
            fill_est.buy < regime_params.min_fill_prob
            and fill_est.sell < regime_params.min_fill_prob
        ):
            return 0

        placed = 0
        acct = self.accounts[book_id]
        mem = self._mem(book_id)
        buy_touch_dist = max(0.0, (mid - bid_px) / spread)
        sell_touch_dist = max(0.0, (ask_px - mid) / spread)

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
            self._mem(book_id).quote_count += 1

        if (
            fill_est.sell >= regime_params.min_fill_prob
            and acct.base_balance.free >= sell_size
            and self._count_book_instructions(response, book_id) < self.max_instructions_per_book
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
            self._mem(book_id).quote_count += 1

        if self.log_predict_pnl and self.verbose_log and placed > 0:
            bt.logging.info(
                f"[PREDICT_PNL] mm book={book_id} fill_b={round(fill_est.buy, 3)} "
                f"fill_s={round(fill_est.sell, 3)} expected_pnl="
                f"{round(est.expected_realized_pnl, 6)} adj_pnl={round(adj_pnl, 6)} "
                f"exp_edge={round(expected_edge, 6)} bid={bid_px} ask={ask_px}"
            )
        return placed

    def _place_directional_round_trip(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        direction: Literal["UP", "DOWN"],
        size: float,
        client_id_base: int = 50000,
        stats: dict | None = None,
    ) -> int:
        cfg = state.config
        book = state.books.get(book_id)
        if not cfg or not book or not book.bids or not book.asks:
            return 0
        qty = self._round_order_size(size, cfg.volumeDecimals)
        if qty <= 0:
            return 0
        best_bid = round(book.bids[0].price, cfg.priceDecimals)
        best_ask = round(book.asks[0].price, cfg.priceDecimals)
        est = self.estimate_round_trip_pnl(
            book_id,
            best_bid,
            best_ask,
            qty,
            is_maker=self._prefer_maker(book_id),
            direction=direction,
            timestamp=state.timestamp,
        )
        if not self._passes_expected_pnl_gate(est.expected_realized_pnl):
            if stats is not None:
                stats["skipped_negative_pnl"] = stats.get("skipped_negative_pnl", 0) + 1
            if self.log_predict_pnl and self.verbose_log:
                bt.logging.info(
                    f"[PREDICT_PNL] skip alpha book={book_id} dir={direction} "
                    f"expected_pnl={round(est.expected_realized_pnl, 6)} "
                    f"(min={self.min_expected_realized_pnl})"
                )
            return 0
        if self.log_predict_pnl and self.verbose_log:
            bt.logging.info(
                f"[PREDICT_PNL] alpha book={book_id} dir={direction} "
                f"expected_pnl={round(est.expected_realized_pnl, 6)} qty={qty}"
            )
        return super()._place_directional_round_trip(
            response, state, book_id, direction, size, client_id_base,
        )

    def build_mm_strategy_instructions(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        selection: BookSelection,
        predictions: dict[int, DirectionForecast],
        regime: MarketRegime,
        collect_archetypes: bool = True,
    ) -> dict:
        stats = {
            "quoted": 0,
            "managed": 0,
            "maintenance": 0,
            "skipped_avoid": 0,
            "skipped_archetype": 0,
            "skipped_toxic": 0,
            "skipped_alpha": 0,
            "skipped_negative_pnl": 0,
            "skipped_low_alpha": 0,
            "skipped_small_inv": 0,
            "skipped_maint_arch": 0,
            "mm_candidates": 0,
            "alpha_ranked": 0,
            "instructions": 0,
        }
        regime_params = self.get_regime_params(regime)
        avoid_set = set(selection.avoid_books)
        profile_by_id = {p.book_id: p for p in selection.profiles}
        maint_limit = self.max_maintenance_books_per_tick
        maintenance_set = set(
            self._schedule_maintenance_books(
                selection, state.timestamp, limit=maint_limit,
            )
        )

        archetype_rows: list[dict] = []
        mm_candidates: list[tuple] = []
        alpha_candidates: list[tuple] = []
        manage_queue: list[tuple[float, int, Book, InventorySnapshot, RegimeParamSet, BookArchetype]] = []
        maintenance_placed_books = 0

        for book_id, book in state.books.items():
            if not book.bids or not book.asks:
                continue
            profile = profile_by_id.get(book_id)
            if not profile:
                continue

            archetype = self.classify_book_archetype(profile, regime)
            book_params = self.merge_regime_and_archetype_params(regime_params, archetype)
            edge_bias = self.get_archetype_edge_bias(archetype)
            if collect_archetypes:
                mem_row = self._mem(book_id)
                archetype_rows.append({
                    "book": book_id,
                    "arch": archetype,
                    "tier": profile.tier,
                    "size_mult": round(book_params.size_mult, 3),
                    "fill": round(mem_row.fill_rate, 3),
                    "win": round(mem_row.win_rate, 3),
                    "streak": mem_row.loss_streak,
                })

            if book_id in avoid_set:
                stats["skipped_avoid"] += 1
                continue

            prediction = predictions.get(book_id)
            if not prediction:
                continue

            mid = (book.bids[0].price + book.asks[0].price) / 2.0
            spread = book.asks[0].price - book.bids[0].price
            inventory = self._net_inventory(book_id, mid)

            if inventory.band != "FLAT":
                if self._inventory_needs_management(inventory):
                    urgency = self._inventory_urgency(
                        inventory, book_params, regime, archetype,
                    )
                    manage_queue.append(
                        (urgency, book_id, book, inventory, book_params, archetype),
                    )
                    continue
                stats["skipped_small_inv"] += 1

            toxic = self.is_toxic_book(book_id, profile, archetype)

            if book_id in maintenance_set:
                if maintenance_placed_books >= maint_limit:
                    stats["skipped_low_alpha"] += 1
                    continue
                if inventory.band != "FLAT":
                    stats["skipped_small_inv"] += 1
                    continue
                if not self._maintenance_allowed(profile, archetype):
                    stats["skipped_maint_arch"] += 1
                    continue
                if toxic and regime.scoring_overlay != "SCORING_PRESSURE":
                    stats["skipped_toxic"] += 1
                    continue
                maint_size = self.maintenance_order_size * self.maintenance_size_mult
                n = self._place_round_trip_limits(
                    response, state, book_id, maint_size,
                    post_only=True, expiry_period=state.config.publish_interval,
                    client_id_base=MAINT_CLIENT_ID_BASE,
                )
                if n:
                    maintenance_placed_books += 1
                    mem_m = self._mem(book_id)
                    mem_m.quote_count += n
                    if spread > 0:
                        self._record_fill_quote(
                            mem_m, "buy",
                            max(0.0, (mid - book.bids[0].price) / spread),
                        )
                        self._record_fill_quote(
                            mem_m, "sell",
                            max(0.0, (book.asks[0].price - mid) / spread),
                        )
                    stats["maintenance"] += 1
                    stats["instructions"] += n
                continue

            if toxic:
                stats["skipped_toxic"] += 1
                continue

            if not book_params.quote_enabled:
                stats["skipped_archetype"] += 1
                continue

            if archetype in ("TOXIC_BOOK", "STRESSED") and regime.mode in ("CHOP", "STRESSED"):
                stats["skipped_toxic"] += 1
                continue

            if self.mm_skip_inactive_tier and profile.tier == "INACTIVE":
                stats["skipped_low_alpha"] += 1
                continue

            fill_est = self.estimate_fill_probability(
                book, mid, spread, profile.trade_rate,
                book.bids[0].price, book.asks[0].price, book_id=book_id,
            )
            mem = self._mem(book_id)
            expected_alpha = self.expected_alpha_score(
                profile, prediction, fill_est, mem, book_id, state.timestamp,
            )
            if expected_alpha < self.min_expected_alpha:
                stats["skipped_low_alpha"] += 1
                continue

            global_rank = self._global_book_rank(expected_alpha, mem)
            mm_candidates.append(
                (
                    global_rank,
                    expected_alpha,
                    book_id,
                    book,
                    profile,
                    prediction,
                    inventory,
                    book_params,
                    edge_bias,
                )
            )

        manage_queue.sort(key=lambda x: x[0], reverse=True)
        for _urgency, book_id, book, inventory, book_params, archetype in manage_queue[
            :self.max_managed_books_per_tick
        ]:
            n = self._manage_inventory(
                response, state, book_id, book, inventory,
                book_params, regime, archetype,
            )
            if n:
                stats["managed"] += 1
                stats["instructions"] += n

        mm_candidates.sort(key=lambda x: x[0], reverse=True)
        stats["mm_candidates"] = len(mm_candidates)
        for item in mm_candidates[:self.max_mm_books_per_tick]:
            (
                _rank,
                _ea,
                book_id,
                book,
                profile,
                prediction,
                inventory,
                book_params,
                edge_bias,
            ) = item
            before_ix = len(response.instructions)
            n = self._place_skewed_quotes(
                response, state, book_id, book, profile, prediction,
                inventory, book_params, self.mm_base_size, edge_bias,
                stats=stats,
            )
            if n:
                stats["quoted"] += 1
                stats["instructions"] += n
            if len(response.instructions) - before_ix > self.max_instructions_per_book:
                break

        if regime_params.alpha_enabled and self._alpha_regime_allows(regime):
            for book_id in selection.alpha_books:
                if book_id in avoid_set or book_id not in state.books:
                    continue
                pred = predictions.get(book_id)
                profile = profile_by_id.get(book_id)
                book = state.books[book_id]
                if not pred or not profile or pred.direction == "HOLD":
                    continue
                if not book.bids or not book.asks:
                    continue
                archetype = self.classify_book_archetype(profile, regime)
                if self.is_toxic_book(book_id, profile, archetype):
                    stats["skipped_toxic"] += 1
                    continue
                mid = (book.bids[0].price + book.asks[0].price) / 2.0
                spread = book.asks[0].price - book.bids[0].price
                fill_est = self.estimate_fill_probability(
                    book, mid, spread, profile.trade_rate,
                    book.bids[0].price, book.asks[0].price, book_id=book_id,
                )
                mem = self._mem(book_id)
                ea = self.expected_alpha_score(
                    profile, pred, fill_est, mem, book_id, state.timestamp,
                )
                if ea < self.min_expected_alpha:
                    stats["skipped_alpha"] += 1
                    continue
                alpha_candidates.append((ea, book_id, pred, profile, archetype))

            alpha_candidates.sort(key=lambda x: x[0], reverse=True)
            stats["alpha_ranked"] = len(alpha_candidates)
            for ea, book_id, pred, profile, archetype in alpha_candidates[
                :self.max_alpha_books_per_tick
            ]:
                merged = self.merge_regime_and_archetype_params(
                    regime_params, archetype,
                )
                alpha_size = self.dynamic_order_size(
                    self.alpha_order_size,
                    profile,
                    merged,
                    InventorySnapshot(0, 0, "FLAT", None, None, 0),
                    state.config.volumeDecimals,
                    mid=profile.mid,
                )
                n = self._place_directional_round_trip(
                    response, state, book_id,
                    "UP" if pred.direction == "UP" else "DOWN",
                    alpha_size,
                    client_id_base=80000,
                    stats=stats,
                )
                if n:
                    self._mem(book_id).quote_count += n
                    stats["instructions"] += n

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

        log_tick = self._tick == 1 or self._tick % self.log_every_n == 0
        need_summary = log_tick and (
            self.verbose_log
            or self.log_momentum_pnl
        )
        summary = self.parse_state(state) if need_summary else None

        predictions = self._predict_all_books(state)
        selection = self.select_books_for_trading(state, predictions)
        regime = self.classify_market_regime_from_profiles(
            selection.profiles, predictions, selection,
        )

        if summary and self.verbose_log and log_tick:
            self._log_input(summary)
        if self.log_direction and log_tick:
            self._log_direction_predictions(predictions)
        if self.log_book_profile and log_tick:
            self._log_book_profile_selection(selection)
        if self.log_regime and log_tick:
            self._log_market_regime(regime)
        if summary and self.log_momentum_pnl and log_tick:
            self._log_momentum_and_pnl(summary, state)
        if self.log_book_memory and log_tick:
            self._log_book_memory_sample(state)

        grace_period_ns = (
            summary.grace_period_ns if summary
            else (state.config.grace_period if state.config else 0)
        )
        in_grace = state.timestamp < grace_period_ns
        collect_archetypes = self.log_mm_strategy and log_tick
        if state.books and not in_grace:
            if self.enable_mm_strategy:
                mm_stats = self.build_mm_strategy_instructions(
                    response, state, selection, predictions, regime,
                    collect_archetypes=collect_archetypes,
                )
                self._accumulate_tuning_window(mm_stats)
                if self.log_mm_strategy and log_tick:
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

        if self.verbose_log and response.instructions and log_tick:
            self._log_output(self.parse_response(response))

        self._maybe_run_tuning_scheduler(state)

        if self.monitor_top_miners:
            try:
                from top_miner_monitor import write_tick_tap

                write_tick_tap(
                    state, self._tick, self.output_dir, self.uid, self.monitor_top_n,
                )
            except Exception as exc:
                bt.logging.warning(f"monitor tap failed: {exc}")

        return response


if __name__ == "__main__":
    launch(Strategy1)
