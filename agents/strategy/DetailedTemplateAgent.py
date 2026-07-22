# SPDX-FileCopyrightText: 2025 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""
DetailedTemplateAgent — reference miner template with explicit INPUT / OUTPUT mapping.

Use this file to learn the validator ↔ miner contract before building a real strategy.
It does NOT aim to score well on subnet 79.

Lifecycle (handled by taos.common.agents.SimulationAgent.handle):
  1. update(state)   — base class stores accounts, processes notices, fires event handlers
  2. respond(state)  — YOUR trading logic; return FinanceAgentResponse
  3. report(state, response) — optional logging

INPUT  — MarketSimulationStateUpdate (one message per publish_interval)
OUTPUT — FinanceAgentResponse (list of instructions)

Full field reference: agents/README.md (Appendix: MarketSimulationStateUpdate)
Protocol definitions: taos/im/protocol/__init__.py, models.py, instructions.py, response.py

Launch (local proxy):
  python DetailedTemplateAgent.py --port 8888 --agent_id 0 \\
    --params verbose_log=1 log_every_n=50 enable_trading=0 lazy_load=1

Mainnet / testnet (via run_miner.sh):
  --agent.path agents.DetailedTemplateAgent \\
  --agent.params verbose_log=0 log_every_n=100 enable_kappa_strategy=1 lazy_load=1
"""

from __future__ import annotations

import json
import math
import os
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Deque, Literal

import bittensor as bt

from taos.common.agents import launch
from taos.im.agents import FinanceSimulationAgent
from taos.im.protocol.events import SimulationStartEvent, TradeEvent
from taos.im.protocol import FinanceAgentResponse, MarketSimulationStateUpdate
from taos.im.protocol.instructions import (
    CancelOrdersInstruction,
    ClosePositionsInstruction,
    PlaceLimitOrderInstruction,
    PlaceMarketOrderInstruction,
)
from taos.im.protocol.models import (
    Book,
    Account,
    LoanSettlementOption,
    OrderCurrency,
    OrderDirection,
    STP,
    TimeInForce,
)
from taos.im.utils import duration_from_timestamp
from taos.im.utils.kappa import kappa_3


# ---------------------------------------------------------------------------
# Parsed views (optional helpers — copy into your own agent)
# ---------------------------------------------------------------------------


@dataclass
class BookSnapshot:
    """Readable subset of state.books[book_id]."""

    book_id: int
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    mid: float | None
    bid_levels: int
    ask_levels: int
    event_count: int
    trade_count: int
    last_trade_price: float | None
    last_trade_qty: float | None
    log_return: float | None = None
    pct_momentum: float | None = None


@dataclass
class AccountSnapshot:
    """Readable subset of state.accounts[uid][book_id] (also self.accounts after update)."""

    book_id: int
    base_total: float
    base_free: float
    quote_total: float
    quote_free: float
    base_loan: float
    quote_loan: float
    open_orders: int
    open_loans: int
    traded_volume: float | None
    maker_fee_rate: float | None
    taker_fee_rate: float | None


@dataclass
class StateSummary:
    """Top-level INPUT summary for one validator tick."""

    simulation_timestamp_ns: int
    simulation_time_human: str
    validator_hotkey: str
    taos_version: int | None
    book_count: int
    publish_interval_ns: int
    miner_wealth: float
    grace_period_ns: int
    notices_count: int
    notice_types: dict[str, int] = field(default_factory=dict)
    books: list[BookSnapshot] = field(default_factory=list)
    accounts: list[AccountSnapshot] = field(default_factory=list)


@dataclass
class ResponseSummary:
    """Readable view of OUTPUT instructions."""

    instruction_count: int
    by_type: dict[str, int]
    lines: list[str]


@dataclass
class DirectionForecast:
    """Per-book direction forecast from momentum + flow + trade flow."""

    book_id: int
    direction: Literal["UP", "DOWN", "HOLD"]
    score: float
    momentum_m: float
    flow_f: float
    trade_t: float
    log_return: float | None
    imbalance: float
    trade_imbalance: float


BookTier = Literal["INACTIVE", "RED", "YELLOW", "GREEN"]


@dataclass
class BookProfile:
    """Per-book market + miner snapshot for scoring-aware book selection."""

    book_id: int
    # Market
    spread: float | None
    mid: float | None
    spread_bps: float | None
    trade_rate: float
    volatility: float
    imbalance: float
    # Miner performance
    raw_kappa: float | None
    realized_pnl: float
    pnl_obs_count: int
    traded_volume: float
    predict_score: float
    predict_direction: str
    # Derived
    tier: BookTier
    alpha_rank: float


@dataclass
class BookSelection:
    """Scoring-aware book lists for alpha vs maintenance vs avoid."""

    alpha_books: list[int]
    maintenance_books: list[int]
    avoid_books: list[int]
    tier_counts: dict[str, int]
    profiles: list[BookProfile] = field(default_factory=list)


MarketRegimeMode = Literal[
    "QUIET",
    "CHOP",
    "TRENDING_UP",
    "TRENDING_DOWN",
    "DISPERSED",
    "STRESSED",
    "BROAD_LIQUID",
    "MIXED",
]

ScoringOverlay = Literal["SCORING_PRESSURE", "DAMAGE_CONTROL", "SCORING_COMFORT"]


@dataclass
class RealizedPnLEstimate:
    """Expected realized PnL if planned limits fill at assumed prices (FIFO model)."""

    book_id: int
    layer: str
    quantity: float
    buy_price: float | None
    sell_price: float | None
    leg_first_pnl: float
    leg_second_pnl: float
    expected_realized_pnl: float
    closes_existing_position: bool
    direction: str | None = None
    is_maker_assumed: bool = True


@dataclass
class MarketRegime:
    """Cross-book market regime from aggregated book profiles and predictions."""

    mode: MarketRegimeMode
    hold_frac: float
    up_frac: float
    down_frac: float
    mean_score: float
    mean_abs_score: float
    mean_volatility: float
    mean_trade_rate: float
    mean_spread_bps: float | None
    mean_imbalance: float
    mean_log_return: float | None
    return_dispersion: float | None
    direction_dispersion: float
    tier_counts: dict[str, int]
    inactive_frac: float
    red_frac: float
    green_frac: float
    scoring_overlay: ScoringOverlay | None
    confidence: float
    book_count: int


class DetailedTemplateAgent(FinanceSimulationAgent):
    """
    Annotated template: parse INPUT, build OUTPUT, log both sides.

    Agent params (--agent.params):
        verbose_log (bool, default True): Log parsed INPUT/OUTPUT summaries.
        log_every_n (int, default 50): Log every N ticks (reduces noise).
        enable_trading (bool, default False): If True, place a tiny demo limit on book 0.
        demo_order_size (float, default 0.25): BASE size for demo limit (enable_trading only).
        history_len (int, default 0): State history depth; 0 = disabled (faster).
        lazy_load (bool): Pass lazy_load=1 to defer parsing unused state fields.
        log_momentum_pnl (bool, default True): Log momentum + realized PnL history.
        momentum_window_ticks (int, default 10): Ticks for log-return momentum.
        pnl_lookback_ns (int, default 10800s sim time): Prune realized_pnl_history.
        pnl_log_file (bool, default False): Append momentum/pnl rows to CSV in output_dir.
        log_kappa (bool, default True): Log raw Kappa-3 per book + median + PnL sequences.
        pnl_sequence_max_entries (int, default 32): Max scoring buckets per book in logs.
        kappa_tau (float, default 0.0): Kappa-3 threshold (validator default).
        kappa_min_lookback_ns (int, default 5400s sim): Min history span for Kappa.
        kappa_min_observations (int, default 3): Min non-zero intervals per book.
        kappa_norm_min / kappa_norm_max (float): Normalization range (logging only).
        w_m, w_f, w_t (float): Weights for momentum, LOB flow, trade flow in score.
        direction_threshold (float): |score| must exceed this for UP/DOWN (default 0.15).
        momentum_scale (float): Scale log_return into [-1, 1] (default 0.002).
        flow_depth (int): LOB levels for imbalance (default 5).
        log_direction (bool): Log predict_direction() results (default True).
        log_book_profile (bool): Log book profiles + selection (default True).
        max_inactive_books_ratio (float): Validator inactive-book tolerance (default 0.375).
        green_kappa_threshold (float): Min raw Kappa for GREEN tier (default 0.0).
        red_kappa_threshold (float): Raw Kappa below this → RED when active (default -0.5).
        spread_alpha_max (float): Max spread/mid for alpha books (default 0.002).
        profile_w_k / profile_w_p / profile_w_s / profile_w_v (float): alpha_rank weights.
        profile_vol_scale (float): Volatility scale for alpha_rank penalty (default 0.01).
        log_regime (bool): Log cross-book market regime (default True).
        regime_hold_frac_threshold (float): HOLD fraction for QUIET/CHOP (default 0.7).
        regime_trend_frac_threshold (float): UP/DOWN fraction for trending (default 0.5).
        regime_dispersed_frac_threshold (float): Both directions for DISPERSED (default 0.25).
        regime_stressed_spread_bps (float): Mean spread bps for STRESSED (default 5.0).
        regime_chop_vol_threshold (float): Volatility for CHOP when HOLD-heavy (default 0.005).
        regime_active_trade_rate (float): Mean trades/tick for BROAD_LIQUID (default 2.0).
        enable_kappa_strategy (bool): Three-layer Kappa growth strategy (default False).
        maintenance_order_size (float): BASE size for maintenance round-trip limits (default 0.25).
        alpha_order_size (float): BASE size for alpha directional limits (default 0.25).
        max_alpha_books_per_tick (int): Max books for alpha layer per tick (default 4).
        max_maintenance_books_per_tick (int): Max maintenance books per tick (default 3).
        capital_turnover_cap (float): Volume cap multiplier vs miner_wealth (default 10.0).
        max_taker_fee_rate (float): Skip taker-style orders above this rate (default 0.015).
        max_instructions_per_book (int): Cap instructions per book per tick (default 5).
        min_order_size (float): Minimum BASE order size (default 0.25).
        log_kappa_strategy (bool): Log [KAPPA_STRATEGY] calibration stats (default True).
        log_predict_pnl (bool): Log [PREDICT_PNL] before strategy orders (default True).
        fast_update (bool): O(events) notice dispatch; skip 118-book debug loop (default False).
        sync_event_csv (bool): Sync orders/trades to CSV each tick (default True unless fast_update).
        log_latency (bool): Log [LATENCY] handle phase timings (default False).
    """

    def initialize(self) -> None:
        self.fast_update = bool(getattr(self.config, "fast_update", False))
        self.sync_event_csv = bool(
            getattr(self.config, "sync_event_csv", not self.fast_update)
        )
        self.log_latency = bool(getattr(self.config, "log_latency", False))
        self.verbose_log = bool(getattr(self.config, "verbose_log", True))
        self.log_every_n = int(getattr(self.config, "log_every_n", 50))
        self.enable_trading = bool(getattr(self.config, "enable_trading", False))
        self.demo_order_size = float(getattr(self.config, "demo_order_size", 0.25))
        self.log_momentum_pnl = bool(getattr(self.config, "log_momentum_pnl", True))
        self.momentum_window_ticks = max(2, int(getattr(self.config, "momentum_window_ticks", 10)))
        self.pnl_lookback_ns = int(getattr(self.config, "pnl_lookback_ns", 10_800_000_000_000))
        self.pnl_log_file = bool(getattr(self.config, "pnl_log_file", False))
        self.log_kappa = bool(getattr(self.config, "log_kappa", True))
        self.pnl_sequence_max_entries = int(getattr(self.config, "pnl_sequence_max_entries", 32))
        self.kappa_tau = float(getattr(self.config, "kappa_tau", 0.0))
        self.kappa_min_lookback = int(getattr(self.config, "kappa_min_lookback_ns", 5_400_000_000_000))
        self.kappa_min_observations = int(getattr(self.config, "kappa_min_observations", 3))
        self.kappa_norm_min = float(getattr(self.config, "kappa_norm_min", -2.5))
        self.kappa_norm_max = float(getattr(self.config, "kappa_norm_max", 2.5))
        self.w_m = float(getattr(self.config, "w_m", 1.0))
        self.w_f = float(getattr(self.config, "w_f", 1.0))
        self.w_t = float(getattr(self.config, "w_t", 0.5))
        self.direction_threshold = float(getattr(self.config, "direction_threshold", 0.15))
        self.momentum_scale = float(getattr(self.config, "momentum_scale", 0.002))
        self.flow_depth = int(getattr(self.config, "flow_depth", 5))
        self.log_direction = bool(getattr(self.config, "log_direction", True))
        self.log_book_profile = bool(getattr(self.config, "log_book_profile", True))
        self.max_inactive_books_ratio = float(getattr(self.config, "max_inactive_books_ratio", 0.375))
        self.green_kappa_threshold = float(getattr(self.config, "green_kappa_threshold", 0.0))
        self.red_kappa_threshold = float(getattr(self.config, "red_kappa_threshold", -0.5))
        self.spread_alpha_max = float(getattr(self.config, "spread_alpha_max", 0.002))
        self.profile_w_k = float(getattr(self.config, "profile_w_k", 1.0))
        self.profile_w_p = float(getattr(self.config, "profile_w_p", 0.5))
        self.profile_w_s = float(getattr(self.config, "profile_w_s", 0.3))
        self.profile_w_v = float(getattr(self.config, "profile_w_v", 0.2))
        self.profile_vol_scale = float(getattr(self.config, "profile_vol_scale", 0.01))
        self.log_regime = bool(getattr(self.config, "log_regime", True))
        self.regime_hold_frac_threshold = float(getattr(self.config, "regime_hold_frac_threshold", 0.7))
        self.regime_trend_frac_threshold = float(getattr(self.config, "regime_trend_frac_threshold", 0.5))
        self.regime_dispersed_frac_threshold = float(
            getattr(self.config, "regime_dispersed_frac_threshold", 0.25)
        )
        self.regime_stressed_spread_bps = float(getattr(self.config, "regime_stressed_spread_bps", 5.0))
        self.regime_chop_vol_threshold = float(getattr(self.config, "regime_chop_vol_threshold", 0.005))
        self.regime_active_trade_rate = float(getattr(self.config, "regime_active_trade_rate", 2.0))
        self.enable_kappa_strategy = bool(getattr(self.config, "enable_kappa_strategy", False))
        self.maintenance_order_size = float(getattr(self.config, "maintenance_order_size", 0.25))
        self.alpha_order_size = float(getattr(self.config, "alpha_order_size", 0.25))
        self.max_alpha_books_per_tick = max(1, int(getattr(self.config, "max_alpha_books_per_tick", 4)))
        self.max_maintenance_books_per_tick = max(
            1, int(getattr(self.config, "max_maintenance_books_per_tick", 3))
        )
        self.capital_turnover_cap = float(getattr(self.config, "capital_turnover_cap", 10.0))
        self.max_taker_fee_rate = float(getattr(self.config, "max_taker_fee_rate", 0.015))
        self.max_instructions_per_book = max(1, int(getattr(self.config, "max_instructions_per_book", 5)))
        self.min_order_size = float(getattr(self.config, "min_order_size", 0.25))
        self.log_kappa_strategy = bool(getattr(self.config, "log_kappa_strategy", True))
        self.log_predict_pnl = bool(getattr(self.config, "log_predict_pnl", True))
        self._last_kappa: dict | None = None
        self._last_pnl_estimates: list[RealizedPnLEstimate] = []
        self._last_strategy_stats: dict = {}
        self._last_predictions: dict[int, DirectionForecast] = {}
        self._last_profiles: list[BookProfile] = []
        self._last_selection: BookSelection | None = None
        self._last_regime: MarketRegime | None = None
        self._tick = 0

        # Mid-price series per book: (timestamp_ns, mid)
        self._mid_history: dict[int, list[tuple[int, float]]] = defaultdict(list)
        # Validator-shaped history: {scoring_timestamp: {book_id: realized_pnl}}
        self.realized_pnl_history: dict[int, dict[int, float]] = {}
        self.total_realized_pnl_by_book: dict[int, float] = defaultdict(float)
        # FIFO open lots — mirrors validator open_positions for scoring
        self._open_positions: dict[int, dict[str, Deque[tuple[int, float, float, float]]]] = defaultdict(
            lambda: {"longs": deque(), "shorts": deque()}
        )
        self._scoring_timestamp: int = 0
        self._pnl_tick_buffer: dict[int, float] = {}
        self._pnl_csv_path = os.path.join(self.output_dir, "momentum_pnl_log.csv")

    # ----- Momentum & realized PnL (validator-aligned tracking) ------------

    def _reset_pnl_state(self) -> None:
        self.realized_pnl_history.clear()
        self.total_realized_pnl_by_book.clear()
        self._open_positions.clear()
        self._mid_history.clear()
        self._last_kappa = None
        self._last_predictions.clear()
        self._last_profiles.clear()
        self._last_selection = None
        self._last_regime = None
        self._last_strategy_stats = {}
        self._last_pnl_estimates = []

    def _dispatch_notice_event(
        self,
        event,
        state: MarketSimulationStateUpdate,
        validator: str,
    ) -> bool:
        """Handle one notice; return True if simulation ended."""
        etype = event.type
        if etype in ("RESET_AGENTS", "RA"):
            return False
        if etype in ("EVENT_SIMULATION_START", "ESS"):
            self.onStart(event)
            return False
        if etype in ("EVENT_SIMULATION_END", "ESE"):
            self.onEnd(event)
            return True

        match etype:
            case (
                "RESPONSE_DISTRIBUTED_PLACE_ORDER_LIMIT"
                | "RESPONSE_DISTRIBUTED_PLACE_ORDER_MARKET"
                | "RDPOL"
                | "RDPOM"
            ):
                self.onOrderAccepted(event)
                if self.sync_event_csv:
                    self.log_order_event(event, state)
            case (
                "ERROR_RESPONSE_DISTRIBUTED_PLACE_ORDER_LIMIT"
                | "ERROR_RESPONSE_DISTRIBUTED_PLACE_ORDER_MARKET"
                | "ERDPOL"
                | "ERDPOM"
            ):
                self.onOrderRejected(event)
            case "RESPONSE_DISTRIBUTED_CANCEL_ORDERS" | "RDCO":
                for cancellation in event.cancellations:
                    self.onOrderCancelled(cancellation)
                    if self.sync_event_csv:
                        self.log_cancellation_event(cancellation, state)
            case "ERROR_RESPONSE_DISTRIBUTED_CANCEL_ORDERS" | "ERDCO":
                for cancellation in event.cancellations:
                    self.onOrderCancellationFailed(cancellation)
            case "RESPONSE_DISTRIBUTED_CLOSE_POSITIONS" | "RDCP":
                for close in event.closes:
                    self.onPositionClosed(close)
            case "ERROR_RESPONSE_DISTRIBUTED_CLOSE_POSITIONS" | "ERDCP":
                for close in event.closes:
                    self.onPositionCloseFailed(close)
            case "EVENT_TRADE" | "ET":
                self.onTrade(event, validator)
                if self.sync_event_csv:
                    self.log_trade_event(event, state)
            case _:
                if etype not in ("EVENT_TRADE", "ET"):
                    bt.logging.warning(f"Unknown event : {event}")
        return False

    def _fast_update(self, state: MarketSimulationStateUpdate) -> None:
        """
        Lightweight update: single pass over notices, no per-book debug logging.
        Avoids O(book_count × events) work in FinanceSimulationAgent.update().
        """
        if self.history_len:
            self.history.append(state.model_copy())
            self.history = self.history[-self.history_len:]
        else:
            self.history = []
        self.simulation_config = state.config
        self.accounts = state.accounts[self.uid]
        self.events = state.notices[self.uid]

        validator = state.dendrite.hotkey
        for event in self.events:
            self._dispatch_notice_event(event, state, validator)

    def update(self, state: MarketSimulationStateUpdate) -> None:
        """Process notices (incl. onTrade) then flush tick PnL into history."""
        self._scoring_timestamp = state.timestamp
        self._pnl_tick_buffer = {}
        if self.fast_update:
            self._fast_update(state)
        else:
            super().update(state)
        if self._pnl_tick_buffer:
            ts = self._scoring_timestamp
            bucket = self.realized_pnl_history.setdefault(ts, {})
            for book_id, pnl in self._pnl_tick_buffer.items():
                bucket[book_id] = round(bucket.get(book_id, 0.0) + pnl, 10)
                self.total_realized_pnl_by_book[book_id] += pnl
        self._prune_pnl_history(state.timestamp)

    def onStart(self, event: SimulationStartEvent) -> None:
        self._reset_pnl_state()
        bt.logging.info(f"Simulation start — reset momentum / realized PnL history ({event.logDir})")

    def onTrade(self, event: TradeEvent, validator: str | None = None) -> None:
        if event.bookId is None:
            return
        is_taker = self.uid == event.takerAgentId
        is_maker = self.uid == event.makerAgentId
        if not is_taker and not is_maker:
            return
        is_buy = (is_taker and event.side == 0) or (is_maker and event.side == 1)
        fee = event.makerFee if is_maker else event.takerFee
        realized_pnl, _ = self._match_trade_fifo(
            event.bookId,
            is_buy,
            event.quantity,
            event.price,
            fee,
            event.timestamp,
        )
        if realized_pnl != 0.0:
            self._pnl_tick_buffer[event.bookId] = self._pnl_tick_buffer.get(event.bookId, 0.0) + realized_pnl

    def _match_trade_fifo(
        self,
        book_id: int,
        is_buy: bool,
        quantity: float,
        price: float,
        fee: float,
        timestamp: int,
    ) -> tuple[float, float]:
        """FIFO round-trip matcher — same logic as validator _match_trade_fifo."""
        positions = self._open_positions[book_id]
        longs = positions["longs"]
        shorts = positions["shorts"]

        if is_buy:
            if not shorts:
                open_fee = fee if quantity > 0 else 0.0
                longs.append((timestamp, quantity, price, open_fee))
                return 0.0, 0.0
        else:
            if not longs:
                open_fee = fee if quantity > 0 else 0.0
                shorts.append((timestamp, quantity, price, open_fee))
                return 0.0, 0.0

        realized_pnl = 0.0
        roundtrip_volume = 0.0
        remaining_qty = quantity
        quantity_inv = 1.0 / quantity if quantity > 0 else 0.0

        if is_buy:
            while remaining_qty > 0 and shorts:
                old_ts, old_qty, old_price, old_fee = shorts[0]
                if old_qty <= remaining_qty:
                    price_pnl = (old_price - price) * old_qty
                    close_fee = fee * old_qty * quantity_inv
                    realized_pnl += price_pnl - old_fee - close_fee
                    roundtrip_volume += old_qty
                    remaining_qty -= old_qty
                    shorts.popleft()
                else:
                    old_qty_inv = 1.0 / old_qty
                    price_pnl = (old_price - price) * remaining_qty
                    close_fee = fee
                    open_fee = old_fee * remaining_qty * old_qty_inv
                    realized_pnl += price_pnl - open_fee - close_fee
                    roundtrip_volume += remaining_qty
                    shorts[0] = (old_ts, old_qty - remaining_qty, old_price, old_fee - open_fee)
                    remaining_qty = 0
            if remaining_qty > 0:
                longs.append((timestamp, remaining_qty, price, fee * remaining_qty * quantity_inv))
        else:
            while remaining_qty > 0 and longs:
                old_ts, old_qty, old_price, old_fee = longs[0]
                if old_qty <= remaining_qty:
                    price_pnl = (price - old_price) * old_qty
                    close_fee = fee * old_qty * quantity_inv
                    realized_pnl += price_pnl - old_fee - close_fee
                    roundtrip_volume += old_qty
                    remaining_qty -= old_qty
                    longs.popleft()
                else:
                    old_qty_inv = 1.0 / old_qty
                    price_pnl = (price - old_price) * remaining_qty
                    close_fee = fee
                    open_fee = old_fee * remaining_qty * old_qty_inv
                    realized_pnl += price_pnl - open_fee - close_fee
                    roundtrip_volume += remaining_qty
                    longs[0] = (old_ts, old_qty - remaining_qty, old_price, old_fee - open_fee)
                    remaining_qty = 0
            if remaining_qty > 0:
                shorts.append((timestamp, remaining_qty, price, fee * remaining_qty * quantity_inv))

        return realized_pnl, roundtrip_volume

    def _copy_positions_for_book(self, book_id: int) -> dict[str, Deque[tuple[int, float, float, float]]]:
        pos = self._open_positions[book_id]
        return {
            "longs": deque(pos["longs"]),
            "shorts": deque(pos["shorts"]),
        }

    def _simulate_fifo_pnl(
        self,
        positions: dict[str, Deque[tuple[int, float, float, float]]],
        is_buy: bool,
        quantity: float,
        price: float,
        fee: float,
        timestamp: int,
    ) -> tuple[float, float]:
        """FIFO matcher on a copied position book — does not mutate live state."""
        longs = positions["longs"]
        shorts = positions["shorts"]

        if is_buy:
            if not shorts:
                open_fee = fee if quantity > 0 else 0.0
                longs.append((timestamp, quantity, price, open_fee))
                return 0.0, 0.0
        else:
            if not longs:
                open_fee = fee if quantity > 0 else 0.0
                shorts.append((timestamp, quantity, price, open_fee))
                return 0.0, 0.0

        realized_pnl = 0.0
        roundtrip_volume = 0.0
        remaining_qty = quantity
        quantity_inv = 1.0 / quantity if quantity > 0 else 0.0

        if is_buy:
            while remaining_qty > 0 and shorts:
                old_ts, old_qty, old_price, old_fee = shorts[0]
                if old_qty <= remaining_qty:
                    price_pnl = (old_price - price) * old_qty
                    close_fee = fee * old_qty * quantity_inv
                    realized_pnl += price_pnl - old_fee - close_fee
                    roundtrip_volume += old_qty
                    remaining_qty -= old_qty
                    shorts.popleft()
                else:
                    old_qty_inv = 1.0 / old_qty
                    price_pnl = (old_price - price) * remaining_qty
                    close_fee = fee
                    open_fee = old_fee * remaining_qty * old_qty_inv
                    realized_pnl += price_pnl - open_fee - close_fee
                    roundtrip_volume += remaining_qty
                    shorts[0] = (old_ts, old_qty - remaining_qty, old_price, old_fee - open_fee)
                    remaining_qty = 0
            if remaining_qty > 0:
                longs.append((timestamp, remaining_qty, price, fee * remaining_qty * quantity_inv))
        else:
            while remaining_qty > 0 and longs:
                old_ts, old_qty, old_price, old_fee = longs[0]
                if old_qty <= remaining_qty:
                    price_pnl = (price - old_price) * old_qty
                    close_fee = fee * old_qty * quantity_inv
                    realized_pnl += price_pnl - old_fee - close_fee
                    roundtrip_volume += old_qty
                    remaining_qty -= old_qty
                    longs.popleft()
                else:
                    old_qty_inv = 1.0 / old_qty
                    price_pnl = (price - old_price) * remaining_qty
                    close_fee = fee
                    open_fee = old_fee * remaining_qty * old_qty_inv
                    realized_pnl += price_pnl - open_fee - close_fee
                    roundtrip_volume += remaining_qty
                    longs[0] = (old_ts, old_qty - remaining_qty, old_price, old_fee - open_fee)
                    remaining_qty = 0
            if remaining_qty > 0:
                shorts.append((timestamp, remaining_qty, price, fee * remaining_qty * quantity_inv))

        return realized_pnl, roundtrip_volume

    def _estimate_trade_fee(
        self,
        book_id: int,
        quantity: float,
        price: float,
        is_maker: bool,
    ) -> float:
        account = self.accounts.get(book_id)
        if not account or not account.fees or quantity <= 0 or price <= 0:
            return 0.0
        rate = (
            account.fees.maker_fee_rate if is_maker
            else account.fees.taker_fee_rate
        )
        return quantity * price * rate

    def _book_has_open_lots(self, book_id: int) -> bool:
        pos = self._open_positions.get(book_id)
        if not pos:
            return False
        return len(pos["longs"]) > 0 or len(pos["shorts"]) > 0

    def estimate_realized_pnl(
        self,
        book_id: int,
        is_buy: bool,
        quantity: float,
        price: float,
        is_maker: bool = True,
        timestamp: int = 0,
    ) -> float:
        """
        Expected realized PnL for one hypothetical fill using current FIFO open lots.

        Returns 0.0 when the trade would only open a new position (no round-trip close).
        """
        if quantity <= 0 or price <= 0:
            return 0.0
        positions = self._copy_positions_for_book(book_id)
        fee = self._estimate_trade_fee(book_id, quantity, price, is_maker)
        pnl, _ = self._simulate_fifo_pnl(positions, is_buy, quantity, price, fee, timestamp)
        return pnl

    def estimate_round_trip_pnl(
        self,
        book_id: int,
        buy_price: float,
        sell_price: float,
        quantity: float,
        is_maker: bool = True,
        direction: Literal["UP", "DOWN", "SYMMETRIC"] = "SYMMETRIC",
        timestamp: int = 0,
    ) -> RealizedPnLEstimate:
        """
        Expected realized PnL if a two-leg round-trip fills at buy_price / sell_price.

        UP / SYMMETRIC: buy then sell. DOWN: sell then buy.
        """
        positions = self._copy_positions_for_book(book_id)
        closes_existing = self._book_has_open_lots(book_id)
        buy_fee = self._estimate_trade_fee(book_id, quantity, buy_price, is_maker)
        sell_fee = self._estimate_trade_fee(book_id, quantity, sell_price, is_maker)

        leg_first_pnl = 0.0
        leg_second_pnl = 0.0
        dir_label: str | None = None

        if direction in ("UP", "SYMMETRIC"):
            dir_label = "UP" if direction == "UP" else None
            leg_first_pnl, _ = self._simulate_fifo_pnl(
                positions, True, quantity, buy_price, buy_fee, timestamp,
            )
            leg_second_pnl, _ = self._simulate_fifo_pnl(
                positions, False, quantity, sell_price, sell_fee, timestamp,
            )
        else:
            dir_label = "DOWN"
            leg_first_pnl, _ = self._simulate_fifo_pnl(
                positions, False, quantity, sell_price, sell_fee, timestamp,
            )
            leg_second_pnl, _ = self._simulate_fifo_pnl(
                positions, True, quantity, buy_price, buy_fee, timestamp,
            )

        return RealizedPnLEstimate(
            book_id=book_id,
            layer="",
            quantity=quantity,
            buy_price=buy_price,
            sell_price=sell_price,
            leg_first_pnl=leg_first_pnl,
            leg_second_pnl=leg_second_pnl,
            expected_realized_pnl=leg_first_pnl + leg_second_pnl,
            closes_existing_position=closes_existing,
            direction=dir_label,
            is_maker_assumed=is_maker,
        )

    def _estimate_plan_for_book(
        self,
        state: MarketSimulationStateUpdate,
        book_id: int,
        size: float,
        layer: str,
        direction: Literal["UP", "DOWN", "SYMMETRIC"] = "SYMMETRIC",
    ) -> RealizedPnLEstimate | None:
        cfg = state.config
        book = state.books.get(book_id)
        if not cfg or not book or not book.bids or not book.asks:
            return None
        vol_dec = cfg.volumeDecimals
        price_dec = cfg.priceDecimals
        qty = self._round_order_size(size, vol_dec)
        best_bid = round(book.bids[0].price, price_dec)
        best_ask = round(book.asks[0].price, price_dec)
        is_maker = self._prefer_maker(book_id)
        trip_dir: Literal["UP", "DOWN", "SYMMETRIC"] = direction
        if direction == "SYMMETRIC":
            trip_dir = "SYMMETRIC"
        estimate = self.estimate_round_trip_pnl(
            book_id,
            best_bid,
            best_ask,
            qty,
            is_maker=is_maker,
            direction=trip_dir,
            timestamp=state.timestamp,
        )
        estimate.layer = layer
        if direction in ("UP", "DOWN"):
            estimate.direction = direction
        return estimate

    def _log_predict_pnl(self, estimates: list[RealizedPnLEstimate]) -> None:
        if not estimates:
            return
        rows = [
            {
                "book": e.book_id,
                "layer": e.layer,
                "qty": e.quantity,
                "bid": e.buy_price,
                "ask": e.sell_price,
                "leg1": round(e.leg_first_pnl, 6),
                "leg2": round(e.leg_second_pnl, 6),
                "expected_pnl": round(e.expected_realized_pnl, 6),
                "closes_existing": e.closes_existing_position,
                "dir": e.direction,
                "maker": e.is_maker_assumed,
            }
            for e in estimates
        ]
        total = sum(e.expected_realized_pnl for e in estimates)
        bt.logging.info(
            f"[PREDICT_PNL] plans={len(estimates)} "
            f"total_expected={round(total, 6)} detail={json.dumps(rows)}"
        )

    def _update_momentum(self, book_id: int, timestamp: int, mid: float | None) -> tuple[float | None, float | None]:
        """Rolling log-return and percent change over momentum_window_ticks."""
        if mid is None or mid <= 0:
            return None, None
        hist = self._mid_history[book_id]
        if not hist or hist[-1][0] != timestamp:
            hist.append((timestamp, mid))
            if len(hist) > self.momentum_window_ticks:
                del hist[:-self.momentum_window_ticks]
        if len(hist) < 2:
            return None, None
        _, old_mid = hist[0]
        if old_mid <= 0:
            return None, None
        log_return = math.log(mid / old_mid)
        pct_momentum = (mid - old_mid) / old_mid
        return log_return, pct_momentum

    def _prune_pnl_history(self, current_timestamp: int) -> None:
        threshold = current_timestamp - self.pnl_lookback_ns
        self.realized_pnl_history = {
            ts: books for ts, books in self.realized_pnl_history.items() if ts >= threshold
        }

    def _summarize_pnl_history(self, max_entries: int = 8) -> dict:
        """Recent scoring buckets + cumulative totals (validator-shaped)."""
        recent_ts = sorted(self.realized_pnl_history.keys())
        tail = recent_ts[-max_entries:]
        recent = {
            ts: dict(self.realized_pnl_history[ts]) for ts in tail
        }
        return {
            "recent_buckets": recent,
            "bucket_count": len(self.realized_pnl_history),
            "total_by_book": dict(self.total_realized_pnl_by_book),
            "total_all_books": round(sum(self.total_realized_pnl_by_book.values()), 4),
        }

    def _realized_pnl_sequences_per_book(self, book_count: int) -> dict[int, list[tuple[int, float]]]:
        """Per-book time series of realized PnL at each scoring timestamp."""
        sequences: dict[int, list[tuple[int, float]]] = {b: [] for b in range(book_count)}
        for ts in sorted(self.realized_pnl_history.keys()):
            for book_id, pnl in self.realized_pnl_history[ts].items():
                if book_id in sequences:
                    sequences[book_id].append((ts, pnl))
        return sequences

    def _truncate_pnl_sequences(
        self,
        sequences: dict[int, list[tuple[int, float]]],
        max_entries: int,
    ) -> dict[int, list[list[float | int]]]:
        """Trim sequences for logging; format [[ts, pnl], ...] per book."""
        out: dict[int, list[list[float | int]]] = {}
        for book_id, seq in sequences.items():
            if not seq:
                continue
            tail = seq[-max_entries:]
            out[book_id] = [[ts, round(pnl, 6)] for ts, pnl in tail]
        return out

    def _book_mid(self, book: Book) -> float | None:
        if not book.bids or not book.asks:
            return None
        return (book.bids[0].price + book.asks[0].price) / 2.0

    def _normalize_momentum(self, log_return: float | None) -> float:
        """Map log-return to [-1, 1] for score term momentum_m."""
        if log_return is None:
            return 0.0
        scale = max(self.momentum_scale, 1e-12)
        return max(-1.0, min(1.0, log_return / scale))

    def _compute_flow_f(self, book: Book) -> float:
        """LOB imbalance in [-1, 1]: positive = bid-heavy (UP bias)."""
        if not book.bids and not book.asks:
            return 0.0
        depth = self.flow_depth
        bid_n = min(depth, len(book.bids)) if book.bids else 0
        ask_n = min(depth, len(book.asks)) if book.asks else 0
        bid_vol = sum(book.bids[i].quantity for i in range(bid_n))
        ask_vol = sum(book.asks[i].quantity for i in range(ask_n))
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        imbalance = (bid_vol - ask_vol) / total
        return max(-1.0, min(1.0, imbalance))

    def _compute_trade_t(self, book: Book) -> tuple[float, float]:
        """
        Net trade-initiated flow in [-1, 1] from events this interval.
        side 0 = BUY-initiated, side 1 = SELL-initiated.
        """
        buy_vol = 0.0
        sell_vol = 0.0
        events = book.events or []
        for event in events:
            etype = getattr(event, "type", None)
            if etype not in ("t", "EVENT_TRADE", "ET"):
                continue
            qty = float(getattr(event, "quantity", 0.0))
            side = getattr(event, "side", None)
            if side == 0:
                buy_vol += qty
            elif side == 1:
                sell_vol += qty
        total = buy_vol + sell_vol
        if total <= 0:
            return 0.0, 0.0
        imbalance = (buy_vol - sell_vol) / total
        return max(-1.0, min(1.0, imbalance)), imbalance

    def predict_direction(
        self,
        book_id: int,
        book: Book,
        timestamp: int,
    ) -> DirectionForecast:
        """
        Predict short-horizon direction using weighted score:

            score = w_m * momentum_m + w_f * flow_f + w_t * trade_t

        - momentum_m: normalized log-return over momentum_window_ticks
        - flow_f: order-book imbalance (bid vs ask depth)
        - trade_t: net buy vs sell initiated volume in book.events this tick

        Returns UP if score > threshold, DOWN if score < -threshold, else HOLD.
        """
        mid = self._book_mid(book)
        log_return, _ = self._update_momentum(book_id, timestamp, mid)
        momentum_m = self._normalize_momentum(log_return)
        flow_f = self._compute_flow_f(book)
        trade_t, trade_imbalance = self._compute_trade_t(book)
        imbalance = flow_f

        score = self.w_m * momentum_m + self.w_f * flow_f + self.w_t * trade_t

        if score > self.direction_threshold:
            direction: Literal["UP", "DOWN", "HOLD"] = "UP"
        elif score < -self.direction_threshold:
            direction = "DOWN"
        else:
            direction = "HOLD"

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

    def _predict_all_books(self, state: MarketSimulationStateUpdate) -> dict[int, DirectionForecast]:
        if not state.books:
            return {}
        predictions = {}
        for book_id, book in state.books.items():
            predictions[book_id] = self.predict_direction(book_id, book, state.timestamp)
        self._last_predictions = predictions
        return predictions

    def _log_direction_predictions(self, predictions: dict[int, DirectionForecast], max_books: int = 8) -> None:
        if not predictions:
            return
        sample = sorted(predictions.values(), key=lambda p: p.book_id)[:max_books]
        rows = [
            {
                "book": p.book_id,
                "dir": p.direction,
                "score": round(p.score, 4),
                "m": round(p.momentum_m, 4),
                "f": round(p.flow_f, 4),
                "t": round(p.trade_t, 4),
            }
            for p in sample
        ]
        bt.logging.info(
            f"[PREDICT] w_m={self.w_m} w_f={self.w_f} w_t={self.w_t} "
            f"threshold={self.direction_threshold} sample={json.dumps(rows)}"
        )
        if len(predictions) > max_books:
            bt.logging.info(f"[PREDICT] … {len(predictions) - max_books} more books omitted")

    # ----- Book profile & selection ----------------------------------------

    def _pnl_observation_count(self, book_id: int, current_ts: int) -> int:
        """Non-zero realized PnL buckets for a book within pnl_lookback_ns."""
        threshold = current_ts - self.pnl_lookback_ns
        count = 0
        for ts, books in self.realized_pnl_history.items():
            if ts < threshold:
                continue
            pnl = books.get(book_id, 0.0)
            if pnl != 0.0:
                count += 1
        return count

    def _realized_pnl_lookback(self, book_id: int, current_ts: int) -> float:
        threshold = current_ts - self.pnl_lookback_ns
        total = 0.0
        for ts, books in self.realized_pnl_history.items():
            if ts >= threshold:
                total += books.get(book_id, 0.0)
        return total

    def _book_volatility(self, book_id: int) -> float:
        """Rolling std of log-returns from mid history."""
        hist = self._mid_history.get(book_id, [])
        if len(hist) < 3:
            return 0.0
        mids = [m for _, m in hist if m > 0]
        if len(mids) < 3:
            return 0.0
        log_rets = [
            math.log(mids[i] / mids[i - 1])
            for i in range(1, len(mids))
            if mids[i - 1] > 0
        ]
        if len(log_rets) < 2:
            return 0.0
        mean = sum(log_rets) / len(log_rets)
        var = sum((r - mean) ** 2 for r in log_rets) / len(log_rets)
        return math.sqrt(var)

    def _spread_bps(self, spread: float | None, mid: float | None) -> float | None:
        if spread is None or mid is None or mid <= 0:
            return None
        return (spread / mid) * 10_000.0

    def _compute_alpha_rank(
        self,
        raw_kappa: float | None,
        predict_score: float,
        spread: float | None,
        mid: float | None,
        volatility: float,
    ) -> float:
        norm_kappa = 0.0
        if raw_kappa is not None:
            span = max(self.kappa_norm_max - self.kappa_norm_min, 1e-12)
            norm_kappa = max(0.0, min(1.0, (raw_kappa - self.kappa_norm_min) / span))
        spread_penalty = 0.0
        if spread is not None and mid is not None and mid > 0:
            spread_penalty = min(1.0, spread / mid)
        vol_scale = max(self.profile_vol_scale, 1e-12)
        vol_penalty = min(1.0, volatility / vol_scale)
        return (
            self.profile_w_k * norm_kappa
            + self.profile_w_p * abs(predict_score)
            - self.profile_w_s * spread_penalty
            - self.profile_w_v * vol_penalty
        )

    def _assign_book_tier(
        self,
        raw_kappa: float | None,
        pnl_obs_count: int,
        realized_pnl: float,
        traded_volume: float,
    ) -> BookTier:
        if pnl_obs_count < self.kappa_min_observations:
            return "INACTIVE"

        is_red = (
            realized_pnl < 0.0
            or (raw_kappa is not None and raw_kappa < self.red_kappa_threshold)
        )
        if is_red:
            return "RED"

        is_green = (
            raw_kappa is not None
            and raw_kappa >= self.green_kappa_threshold
            and realized_pnl >= 0.0
        )
        if is_green:
            return "GREEN"

        return "YELLOW"

    def build_book_profile(
        self,
        book_id: int,
        book: Book,
        state: MarketSimulationStateUpdate,
        prediction: DirectionForecast | None,
        raw_kappa: float | None,
    ) -> BookProfile:
        mid = self._book_mid(book)
        spread = None
        if book.bids and book.asks:
            spread = book.asks[0].price - book.bids[0].price
        imbalance = self._compute_flow_f(book)
        trade_rate = float(sum(
            1 for e in (book.events or [])
            if getattr(e, "type", None) in ("t", "EVENT_TRADE", "ET")
        ))
        volatility = self._book_volatility(book_id)
        pnl_obs_count = self._pnl_observation_count(book_id, state.timestamp)
        realized_pnl = self._realized_pnl_lookback(book_id, state.timestamp)
        traded_volume = 0.0
        if self.accounts and book_id in self.accounts:
            vol = self.accounts[book_id].traded_volume
            traded_volume = float(vol) if vol is not None else 0.0

        predict_score = prediction.score if prediction else 0.0
        predict_direction = prediction.direction if prediction else "HOLD"
        tier = self._assign_book_tier(raw_kappa, pnl_obs_count, realized_pnl, traded_volume)
        alpha_rank = self._compute_alpha_rank(
            raw_kappa, predict_score, spread, mid, volatility,
        )

        return BookProfile(
            book_id=book_id,
            spread=spread,
            mid=mid,
            spread_bps=self._spread_bps(spread, mid),
            trade_rate=trade_rate,
            volatility=volatility,
            imbalance=imbalance,
            raw_kappa=raw_kappa,
            realized_pnl=realized_pnl,
            pnl_obs_count=pnl_obs_count,
            traded_volume=traded_volume,
            predict_score=predict_score,
            predict_direction=predict_direction,
            tier=tier,
            alpha_rank=alpha_rank,
        )

    def build_all_book_profiles(
        self,
        state: MarketSimulationStateUpdate,
        predictions: dict[int, DirectionForecast],
        kappa_values: dict | None = None,
    ) -> list[BookProfile]:
        if not state.books:
            return []
        if kappa_values is None:
            kappa_values = self._compute_local_kappa(state)
        self._last_kappa = kappa_values
        raw_by_book = (kappa_values or {}).get("books", {})
        profiles = []
        for book_id, book in sorted(state.books.items()):
            raw_k = raw_by_book.get(book_id)
            if raw_k is None and book_id in raw_by_book:
                raw_k = raw_by_book[book_id]
            profiles.append(
                self.build_book_profile(
                    book_id,
                    book,
                    state,
                    predictions.get(book_id),
                    raw_k,
                )
            )
        self._last_profiles = profiles
        return profiles

    @staticmethod
    def rank_books_for_trading(
        profiles: list[BookProfile],
        spread_alpha_max: float = 0.002,
        max_maintenance: int | None = None,
    ) -> BookSelection:
        """
        Partition books for scoring-aware trading.

        - alpha_books: GREEN tier, sorted by alpha_rank (best first)
        - maintenance_books: INACTIVE — need round-trip activity for Kappa
        - avoid_books: RED tier — poor Kappa / negative PnL while active

        YELLOW books are omitted from all three lists (trade at normal discretion).
        """
        tier_counts: dict[str, int] = defaultdict(int)
        for p in profiles:
            tier_counts[p.tier] += 1

        alpha_candidates = [
            p for p in profiles
            if p.tier == "GREEN"
            and p.spread is not None
            and p.mid is not None
            and p.mid > 0
            and (p.spread / p.mid) <= spread_alpha_max
            and p.predict_direction != "HOLD"
        ]
        alpha_books = sorted(
            alpha_candidates,
            key=lambda p: p.alpha_rank,
            reverse=True,
        )
        alpha_ids = [p.book_id for p in alpha_books]

        maintenance_candidates = [p for p in profiles if p.tier == "INACTIVE"]
        maintenance_candidates.sort(key=lambda p: (p.pnl_obs_count, p.traded_volume))
        if max_maintenance is not None:
            maintenance_candidates = maintenance_candidates[:max_maintenance]
        maintenance_ids = [p.book_id for p in maintenance_candidates]

        avoid_ids = [p.book_id for p in profiles if p.tier == "RED"]

        return BookSelection(
            alpha_books=alpha_ids,
            maintenance_books=maintenance_ids,
            avoid_books=avoid_ids,
            tier_counts=dict(tier_counts),
            profiles=profiles,
        )

    def select_books_for_trading(
        self,
        state: MarketSimulationStateUpdate,
        predictions: dict[int, DirectionForecast],
    ) -> BookSelection:
        """Build profiles and return scoring-aware book selection for this tick."""
        profiles = self.build_all_book_profiles(state, predictions)
        book_count = len(profiles)
        max_inactive = int(self.max_inactive_books_ratio * book_count) if book_count else 0
        selection = self.rank_books_for_trading(
            profiles,
            spread_alpha_max=self.spread_alpha_max,
            max_maintenance=max(max_inactive, 1) if book_count else None,
        )
        self._last_selection = selection
        return selection

    def _log_book_profile_selection(self, selection: BookSelection) -> None:
        tier_counts = selection.tier_counts
        bt.logging.info(
            f"[BOOK_PROFILE] tier_counts={json.dumps(tier_counts)} "
            f"max_inactive_allowed={int(self.max_inactive_books_ratio * len(selection.profiles))}"
        )
        bt.logging.info(
            f"[BOOK_PROFILE] alpha={selection.alpha_books} "
            f"maintain={selection.maintenance_books} avoid={selection.avoid_books}"
        )
        sample = sorted(selection.profiles, key=lambda p: p.alpha_rank, reverse=True)[:8]
        rows = [
            {
                "book": p.book_id,
                "tier": p.tier,
                "rank": round(p.alpha_rank, 4),
                "kappa": round(p.raw_kappa, 4) if p.raw_kappa is not None else None,
                "pnl": round(p.realized_pnl, 4),
                "obs": p.pnl_obs_count,
                "dir": p.predict_direction,
                "spread_bps": round(p.spread_bps, 2) if p.spread_bps is not None else None,
            }
            for p in sample
        ]
        bt.logging.info(f"[BOOK_PROFILE] top_by_rank={json.dumps(rows)}")

    # ----- Market regime -------------------------------------------------------

    @staticmethod
    def classify_market_regime(
        profiles: list[BookProfile],
        predictions: dict[int, DirectionForecast] | None = None,
        tier_counts: dict[str, int] | None = None,
        max_inactive_books_ratio: float = 0.375,
        hold_frac_threshold: float = 0.7,
        trend_frac_threshold: float = 0.5,
        dispersed_frac_threshold: float = 0.25,
        stressed_spread_bps: float = 5.0,
        chop_vol_threshold: float = 0.005,
        active_trade_rate: float = 2.0,
    ) -> MarketRegime:
        """
        Classify cross-book market regime from per-book profiles.

        Modes (priority order):
            STRESSED → TRENDING_UP/DOWN → DISPERSED → CHOP → QUIET → BROAD_LIQUID → MIXED
        """
        n = len(profiles)
        if n == 0:
            return MarketRegime(
                mode="MIXED",
                hold_frac=0.0,
                up_frac=0.0,
                down_frac=0.0,
                mean_score=0.0,
                mean_abs_score=0.0,
                mean_volatility=0.0,
                mean_trade_rate=0.0,
                mean_spread_bps=None,
                mean_imbalance=0.0,
                mean_log_return=None,
                return_dispersion=None,
                direction_dispersion=0.0,
                tier_counts={},
                inactive_frac=0.0,
                red_frac=0.0,
                green_frac=0.0,
                scoring_overlay=None,
                confidence=0.0,
                book_count=0,
            )

        hold_n = sum(1 for p in profiles if p.predict_direction == "HOLD")
        up_n = sum(1 for p in profiles if p.predict_direction == "UP")
        down_n = sum(1 for p in profiles if p.predict_direction == "DOWN")
        hold_frac = hold_n / n
        up_frac = up_n / n
        down_frac = down_n / n

        scores = [p.predict_score for p in profiles]
        mean_score = sum(scores) / n
        mean_abs_score = sum(abs(s) for s in scores) / n
        direction_dispersion = (
            math.sqrt(sum((s - mean_score) ** 2 for s in scores) / n)
            if n > 1
            else 0.0
        )

        mean_volatility = sum(p.volatility for p in profiles) / n
        mean_trade_rate = sum(p.trade_rate for p in profiles) / n
        mean_imbalance = sum(p.imbalance for p in profiles) / n

        spread_samples = [p.spread_bps for p in profiles if p.spread_bps is not None]
        mean_spread_bps = sum(spread_samples) / len(spread_samples) if spread_samples else None

        log_returns: list[float] = []
        if predictions:
            for pred in predictions.values():
                if pred.log_return is not None:
                    log_returns.append(pred.log_return)
        mean_log_return: float | None = None
        return_dispersion: float | None = None
        if log_returns:
            mean_log_return = sum(log_returns) / len(log_returns)
            if len(log_returns) > 1:
                m = mean_log_return
                return_dispersion = math.sqrt(
                    sum((r - m) ** 2 for r in log_returns) / len(log_returns)
                )
            else:
                return_dispersion = 0.0

        if tier_counts is None:
            tier_counts = defaultdict(int)
            for p in profiles:
                tier_counts[p.tier] += 1
            tier_counts = dict(tier_counts)

        inactive_frac = tier_counts.get("INACTIVE", 0) / n
        red_frac = tier_counts.get("RED", 0) / n
        green_frac = tier_counts.get("GREEN", 0) / n

        max_inactive = int(max_inactive_books_ratio * n)
        inactive_count = tier_counts.get("INACTIVE", 0)
        red_count = tier_counts.get("RED", 0)
        green_count = tier_counts.get("GREEN", 0)

        scoring_overlay: ScoringOverlay | None = None
        if inactive_count >= max(max_inactive - 1, 1):
            scoring_overlay = "SCORING_PRESSURE"
        elif red_frac > 0.25:
            scoring_overlay = "DAMAGE_CONTROL"
        elif green_frac > 0.5 and red_frac < 0.1:
            scoring_overlay = "SCORING_COMFORT"

        mode: MarketRegimeMode = "MIXED"
        confidence = 0.3

        if mean_spread_bps is not None and mean_spread_bps >= stressed_spread_bps:
            mode = "STRESSED"
            confidence = min(1.0, mean_spread_bps / stressed_spread_bps)
        elif up_frac >= trend_frac_threshold and mean_score > 0:
            mode = "TRENDING_UP"
            confidence = min(1.0, up_frac + abs(mean_score))
        elif down_frac >= trend_frac_threshold and mean_score < 0:
            mode = "TRENDING_DOWN"
            confidence = min(1.0, down_frac + abs(mean_score))
        elif (
            up_frac >= dispersed_frac_threshold
            and down_frac >= dispersed_frac_threshold
        ):
            mode = "DISPERSED"
            confidence = min(1.0, min(up_frac, down_frac) / dispersed_frac_threshold)
        elif hold_frac >= hold_frac_threshold and mean_volatility >= chop_vol_threshold:
            mode = "CHOP"
            confidence = min(1.0, hold_frac)
        elif hold_frac >= hold_frac_threshold:
            mode = "QUIET"
            confidence = min(1.0, hold_frac)
        elif mean_trade_rate >= active_trade_rate:
            mode = "BROAD_LIQUID"
            confidence = min(1.0, mean_trade_rate / (active_trade_rate * 2))

        return MarketRegime(
            mode=mode,
            hold_frac=hold_frac,
            up_frac=up_frac,
            down_frac=down_frac,
            mean_score=mean_score,
            mean_abs_score=mean_abs_score,
            mean_volatility=mean_volatility,
            mean_trade_rate=mean_trade_rate,
            mean_spread_bps=mean_spread_bps,
            mean_imbalance=mean_imbalance,
            mean_log_return=mean_log_return,
            return_dispersion=return_dispersion,
            direction_dispersion=direction_dispersion,
            tier_counts=tier_counts,
            inactive_frac=inactive_frac,
            red_frac=red_frac,
            green_frac=green_frac,
            scoring_overlay=scoring_overlay,
            confidence=confidence,
            book_count=n,
        )

    def classify_market_regime_from_profiles(
        self,
        profiles: list[BookProfile],
        predictions: dict[int, DirectionForecast],
        selection: BookSelection | None = None,
    ) -> MarketRegime:
        """Classify regime using agent config thresholds; stores result on self."""
        tier_counts = selection.tier_counts if selection else None
        regime = self.classify_market_regime(
            profiles,
            predictions=predictions,
            tier_counts=tier_counts,
            max_inactive_books_ratio=self.max_inactive_books_ratio,
            hold_frac_threshold=self.regime_hold_frac_threshold,
            trend_frac_threshold=self.regime_trend_frac_threshold,
            dispersed_frac_threshold=self.regime_dispersed_frac_threshold,
            stressed_spread_bps=self.regime_stressed_spread_bps,
            chop_vol_threshold=self.regime_chop_vol_threshold,
            active_trade_rate=self.regime_active_trade_rate,
        )
        self._last_regime = regime
        return regime

    def _log_market_regime(self, regime: MarketRegime) -> None:
        payload = {
            "mode": regime.mode,
            "confidence": round(regime.confidence, 4),
            "hold_frac": round(regime.hold_frac, 4),
            "up_frac": round(regime.up_frac, 4),
            "down_frac": round(regime.down_frac, 4),
            "mean_score": round(regime.mean_score, 4),
            "mean_abs_score": round(regime.mean_abs_score, 4),
            "mean_vol": round(regime.mean_volatility, 6),
            "mean_trade_rate": round(regime.mean_trade_rate, 2),
            "mean_spread_bps": (
                round(regime.mean_spread_bps, 2) if regime.mean_spread_bps is not None else None
            ),
            "mean_imbalance": round(regime.mean_imbalance, 4),
            "mean_log_return": (
                round(regime.mean_log_return, 8) if regime.mean_log_return is not None else None
            ),
            "return_dispersion": (
                round(regime.return_dispersion, 8)
                if regime.return_dispersion is not None
                else None
            ),
            "direction_dispersion": round(regime.direction_dispersion, 4),
            "tier_counts": regime.tier_counts,
            "inactive_frac": round(regime.inactive_frac, 4),
            "red_frac": round(regime.red_frac, 4),
            "green_frac": round(regime.green_frac, 4),
            "scoring_overlay": regime.scoring_overlay,
            "books": regime.book_count,
        }
        bt.logging.info(f"[REGIME] {json.dumps(payload)}")

    def _compute_local_kappa(self, state: MarketSimulationStateUpdate) -> dict | None:
        """Run validator kappa_3() on miner-side realized_pnl_history."""
        cfg = state.config
        if not cfg or not self.realized_pnl_history:
            return None
        pnl_values = {ts: dict(books) for ts, books in self.realized_pnl_history.items()}
        return kappa_3(
            self.uid,
            pnl_values,
            self.kappa_tau,
            self.pnl_lookback_ns,
            self.kappa_norm_min,
            self.kappa_norm_max,
            self.kappa_min_lookback,
            self.kappa_min_observations,
            cfg.grace_period,
            [],
            cfg.book_count,
            cache=None,
        )

    # ----- INPUT parsing ---------------------------------------------------

    @staticmethod
    def parse_book(book_id: int, book: Book, detailed_depth: int) -> BookSnapshot:
        best_bid = book.bids[0].price if book.bids else None
        best_ask = book.asks[0].price if book.asks else None
        spread = (best_ask - best_bid) if best_bid is not None and best_ask is not None else None
        mid = (best_bid + best_ask) / 2 if spread is not None else None

        events = book.events or []
        trade_count = sum(1 for e in events if getattr(e, "type", None) in ("t", "EVENT_TRADE", "ET"))

        last_price = None
        last_qty = None
        trades = book.trades
        if trades:
            lt = trades[max(trades)]
            last_price = lt.price
            last_qty = lt.quantity

        return BookSnapshot(
            book_id=book_id,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=spread,
            mid=mid,
            bid_levels=len(book.bids),
            ask_levels=len(book.asks),
            event_count=len(events),
            trade_count=trade_count,
            last_trade_price=last_price,
            last_trade_qty=last_qty,
            log_return=None,
            pct_momentum=None,
        )

    @staticmethod
    def parse_account(book_id: int, account: Account) -> AccountSnapshot:
        fees = account.fees
        return AccountSnapshot(
            book_id=book_id,
            base_total=account.base_balance.total,
            base_free=account.base_balance.free,
            quote_total=account.quote_balance.total,
            quote_free=account.quote_balance.free,
            base_loan=account.base_loan,
            quote_loan=account.quote_loan,
            open_orders=len(account.orders),
            open_loans=len(account.loans),
            traded_volume=account.traded_volume,
            maker_fee_rate=fees.maker_fee_rate if fees else None,
            taker_fee_rate=fees.taker_fee_rate if fees else None,
        )

    def parse_notices(self, state: MarketSimulationStateUpdate) -> tuple[int, dict[str, int]]:
        events = state.notices.get(self.uid, []) if state.notices else []
        counts: dict[str, int] = {}
        for ev in events:
            t = getattr(ev, "type", type(ev).__name__)
            counts[t] = counts.get(t, 0) + 1
        return len(events), counts

    def parse_state(self, state: MarketSimulationStateUpdate) -> StateSummary:
        """
        Build a structured INPUT summary from MarketSimulationStateUpdate.

        Top-level fields on `state`:
            version      — validator taos package version (int | None)
            timestamp    — simulation time in nanoseconds since sim start
            config       — MarketSimulationConfig (decimals, wealth, book_count, …)
            books        — dict[book_id, Book]  environment / order books
            accounts     — dict[uid, dict[book_id, Account]]  your balances & orders
            notices      — dict[uid, list[Event]]  fills, rejects, sim start/end, …
            dendrite     — bittensor metadata (validator hotkey, axon, …)
            compressed   — raw compressed payload if lazy_load deferred parsing
        """
        cfg = state.config
        notice_count, notice_types = self.parse_notices(state)

        book_snaps: list[BookSnapshot] = []
        if state.books:
            depth = cfg.detailed_book_levels if cfg else 5
            for book_id, book in sorted(state.books.items()):
                snap = self.parse_book(book_id, book, depth)
                log_ret, pct = self._update_momentum(book_id, state.timestamp, snap.mid)
                snap.log_return = log_ret
                snap.pct_momentum = pct
                book_snaps.append(snap)

        account_snaps: list[AccountSnapshot] = []
        uid_accounts = state.accounts.get(self.uid, {}) if state.accounts else {}
        for book_id in sorted(uid_accounts.keys()):
            account_snaps.append(self.parse_account(book_id, uid_accounts[book_id]))

        return StateSummary(
            simulation_timestamp_ns=state.timestamp,
            simulation_time_human=duration_from_timestamp(state.timestamp),
            validator_hotkey=state.dendrite.hotkey,
            taos_version=state.version,
            book_count=cfg.book_count if cfg else len(state.books or {}),
            publish_interval_ns=cfg.publish_interval if cfg else 0,
            miner_wealth=cfg.miner_wealth if cfg else 0.0,
            grace_period_ns=cfg.grace_period if cfg else 0,
            notices_count=notice_count,
            notice_types=notice_types,
            books=book_snaps,
            accounts=account_snaps,
        )

    # ----- OUTPUT parsing --------------------------------------------------

    @staticmethod
    def parse_response(response: FinanceAgentResponse) -> ResponseSummary:
        """
        Summarize FinanceAgentResponse.instructions for logging / debugging.

        Each instruction is one of:
            PLACE_ORDER_MARKET  — PlaceMarketOrderInstruction
            PLACE_ORDER_LIMIT   — PlaceLimitOrderInstruction
            CANCEL_ORDERS       — CancelOrdersInstruction (wraps cancel list)
            CLOSE_POSITIONS     — ClosePositionsInstruction (margin close)
            RESET_AGENT         — ResetAgentsInstruction (rare)
        """
        by_type: dict[str, int] = {}
        lines: list[str] = []

        for instr in response.instructions:
            itype = instr.type
            by_type[itype] = by_type.get(itype, 0) + 1
            lines.append(str(instr))

        return ResponseSummary(
            instruction_count=len(response.instructions),
            by_type=by_type,
            lines=lines,
        )

    # ----- Example OUTPUT builders (copy & adapt) --------------------------

    def build_demo_instructions(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int = 0,
    ) -> None:
        """
        Demonstrates every major OUTPUT pattern. Only used when enable_trading=True.

        In production, call only the methods you need on `response`:
            response.market_order(...)
            response.limit_order(...)
            response.cancel_order(...)
            response.cancel_orders(...)
            response.close_position(...)
            response.close_positions(...)
        """
        cfg = state.config
        book = state.books[book_id]
        account = self.accounts[book_id]
        vol_dec = cfg.volumeDecimals
        price_dec = cfg.priceDecimals
        size = round(self.demo_order_size, vol_dec)

        best_bid = book.bids[0].price if book.bids else cfg.init_price * 0.99
        best_ask = book.asks[0].price if book.asks else cfg.init_price * 1.01
        buy_price = round(best_bid, price_dec)
        sell_price = round(best_ask, price_dec)

        # 1) Passive limit (maker) — most common for market-making strategies
        if account.quote_balance.free >= buy_price * size:
            response.limit_order(
                book_id=book_id,
                direction=OrderDirection.BUY,
                quantity=size,
                price=buy_price,
                clientOrderId=1001,
                stp=STP.CANCEL_OLDEST,
                postOnly=True,
                timeInForce=TimeInForce.GTC,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )

        # 2) Limit with expiry (GTT)
        if account.base_balance.free >= size:
            response.limit_order(
                book_id=book_id,
                direction=OrderDirection.SELL,
                quantity=size,
                price=sell_price,
                clientOrderId=1002,
                timeInForce=TimeInForce.GTT,
                expiryPeriod=cfg.publish_interval,
                delay=0,
            )

        # 3) Cancel example — only if we have open orders
        if account.orders:
            response.cancel_order(book_id=book_id, order_id=account.orders[0].id, delay=0)

        # 4) Market order example — commented out by default (aggressive, uses taker fees)
        # response.market_order(
        #     book_id=book_id,
        #     direction=OrderDirection.BUY,
        #     quantity=size,
        #     currency=OrderCurrency.BASE,
        #     stp=STP.CANCEL_OLDEST,
        #     leverage=0.0,
        #     delay=50_000_000,
        # )

        # 5) Close leveraged position — only if loans exist
        if account.loans:
            loan_order_id = next(iter(account.loans.keys()))
            response.close_position(book_id=book_id, order_id=loan_order_id, delay=0)

    # ----- Kappa growth strategy (three-layer execution) ---------------------

    def _count_book_instructions(self, response: FinanceAgentResponse, book_id: int) -> int:
        return sum(1 for instr in response.instructions if getattr(instr, "bookId", None) == book_id)

    def _round_order_size(self, size: float, vol_dec: int) -> float:
        return round(max(size, self.min_order_size), vol_dec)

    def _total_traded_volume(self) -> float:
        total = 0.0
        for account in self.accounts.values():
            vol = account.traded_volume
            if vol is not None:
                total += float(vol)
        return total

    def _volume_cap_quote(self, state: MarketSimulationStateUpdate) -> float:
        cfg = state.config
        if not cfg:
            return 0.0
        return self.capital_turnover_cap * cfg.miner_wealth

    def _volume_cap_remaining(self, state: MarketSimulationStateUpdate) -> float:
        return max(0.0, self._volume_cap_quote(state) - self._total_traded_volume())

    def _can_add_volume(self, state: MarketSimulationStateUpdate, quote_notional: float) -> bool:
        return quote_notional <= self._volume_cap_remaining(state)

    def _passes_fee_gate(self, book_id: int, aggressive: bool) -> bool:
        account = self.accounts.get(book_id)
        if not account or not account.fees:
            return True
        if aggressive and account.fees.taker_fee_rate > self.max_taker_fee_rate:
            return False
        return True

    def _prefer_maker(self, book_id: int) -> bool:
        account = self.accounts.get(book_id)
        if not account or not account.fees:
            return True
        return account.fees.maker_fee_rate <= account.fees.taker_fee_rate

    def _alpha_regime_allows(self, regime: MarketRegime) -> bool:
        if regime.mode in ("STRESSED", "QUIET", "CHOP"):
            return False
        if regime.mode in ("TRENDING_UP", "TRENDING_DOWN", "BROAD_LIQUID"):
            return True
        if regime.mode == "DISPERSED":
            return True
        return regime.mode == "MIXED" and regime.mean_abs_score > self.direction_threshold

    def _estimate_local_normalized_median(self) -> float | None:
        """Proxy median of normalized raw Kappas (validator uses activity-weighted values)."""
        if not self._last_kappa or not self._last_profiles:
            return None
        raw_by_book = self._last_kappa.get("books", {})
        span = max(self.kappa_norm_max - self.kappa_norm_min, 1e-12)
        norms: list[float] = []
        for profile in self._last_profiles:
            raw = raw_by_book.get(profile.book_id)
            if raw is None:
                continue
            norms.append(max(0.0, min(1.0, (raw - self.kappa_norm_min) / span)))
        if not norms:
            return None
        norms.sort()
        mid = len(norms) // 2
        if len(norms) % 2:
            return norms[mid]
        return (norms[mid - 1] + norms[mid]) / 2.0

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
        """Place bid+ask limit pair for round-trip potential; returns instruction count."""
        cfg = state.config
        book = state.books.get(book_id)
        account = self.accounts.get(book_id)
        if not cfg or not book or not account:
            return 0
        if not book.bids or not book.asks:
            return 0

        vol_dec = cfg.volumeDecimals
        price_dec = cfg.priceDecimals
        qty = self._round_order_size(size, vol_dec)
        best_bid = round(book.bids[0].price, price_dec)
        best_ask = round(book.asks[0].price, price_dec)
        mid = (best_bid + best_ask) / 2.0
        quote_notional = qty * mid * 2

        if not self._can_add_volume(state, quote_notional):
            return 0
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return 0

        placed = 0
        tif = TimeInForce.GTT if expiry_period else TimeInForce.GTC
        use_post_only = post_only and self._prefer_maker(book_id)

        if account.quote_balance.free >= best_bid * qty:
            if self._count_book_instructions(response, book_id) < self.max_instructions_per_book:
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.BUY,
                    quantity=qty,
                    price=best_bid,
                    clientOrderId=client_id_base + book_id * 10 + 1,
                    stp=STP.CANCEL_OLDEST,
                    postOnly=use_post_only,
                    timeInForce=tif,
                    expiryPeriod=expiry_period,
                    leverage=0.0,
                    settlement_option=LoanSettlementOption.NONE,
                    delay=0,
                )
                placed += 1

        if account.base_balance.free >= qty:
            if self._count_book_instructions(response, book_id) < self.max_instructions_per_book:
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.SELL,
                    quantity=qty,
                    price=best_ask,
                    clientOrderId=client_id_base + book_id * 10 + 2,
                    stp=STP.CANCEL_OLDEST,
                    postOnly=use_post_only,
                    timeInForce=tif,
                    expiryPeriod=expiry_period,
                    leverage=0.0,
                    settlement_option=LoanSettlementOption.NONE,
                    delay=0,
                )
                placed += 1

        return placed

    def _place_directional_round_trip(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        book_id: int,
        direction: Literal["UP", "DOWN"],
        size: float,
        client_id_base: int = 50000,
    ) -> int:
        """
        Entry + exit limits aligned with predicted direction for round-trip discipline.
        UP: buy at bid (entry), sell at ask (exit). DOWN: sell then buy.
        """
        cfg = state.config
        book = state.books.get(book_id)
        account = self.accounts.get(book_id)
        if not cfg or not book or not account:
            return 0
        if not book.bids or not book.asks:
            return 0

        vol_dec = cfg.volumeDecimals
        price_dec = cfg.priceDecimals
        qty = self._round_order_size(size, vol_dec)
        best_bid = round(book.bids[0].price, price_dec)
        best_ask = round(book.asks[0].price, price_dec)
        mid = (best_bid + best_ask) / 2.0
        quote_notional = qty * mid * 2

        if not self._can_add_volume(state, quote_notional):
            return 0
        if not self._passes_fee_gate(book_id, aggressive=False):
            return 0
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return 0

        expiry = cfg.publish_interval
        use_post_only = self._prefer_maker(book_id)
        placed = 0

        if direction == "UP":
            if account.quote_balance.free >= best_bid * qty:
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.BUY,
                    quantity=qty,
                    price=best_bid,
                    clientOrderId=client_id_base + book_id * 10 + 1,
                    stp=STP.CANCEL_OLDEST,
                    postOnly=use_post_only,
                    timeInForce=TimeInForce.GTC,
                    leverage=0.0,
                    settlement_option=LoanSettlementOption.NONE,
                    delay=0,
                )
                placed += 1
            if (
                placed > 0
                and account.base_balance.free >= qty
                and self._count_book_instructions(response, book_id) < self.max_instructions_per_book
            ):
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.SELL,
                    quantity=qty,
                    price=best_ask,
                    clientOrderId=client_id_base + book_id * 10 + 2,
                    stp=STP.CANCEL_OLDEST,
                    postOnly=use_post_only,
                    timeInForce=TimeInForce.GTT,
                    expiryPeriod=expiry,
                    leverage=0.0,
                    settlement_option=LoanSettlementOption.NONE,
                    delay=0,
                )
                placed += 1
        else:
            if account.base_balance.free >= qty:
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.SELL,
                    quantity=qty,
                    price=best_ask,
                    clientOrderId=client_id_base + book_id * 10 + 3,
                    stp=STP.CANCEL_OLDEST,
                    postOnly=use_post_only,
                    timeInForce=TimeInForce.GTC,
                    leverage=0.0,
                    settlement_option=LoanSettlementOption.NONE,
                    delay=0,
                )
                placed += 1
            if (
                placed > 0
                and account.quote_balance.free >= best_bid * qty
                and self._count_book_instructions(response, book_id) < self.max_instructions_per_book
            ):
                response.limit_order(
                    book_id=book_id,
                    direction=OrderDirection.BUY,
                    quantity=qty,
                    price=best_bid,
                    clientOrderId=client_id_base + book_id * 10 + 4,
                    stp=STP.CANCEL_OLDEST,
                    postOnly=use_post_only,
                    timeInForce=TimeInForce.GTT,
                    expiryPeriod=expiry,
                    leverage=0.0,
                    settlement_option=LoanSettlementOption.NONE,
                    delay=0,
                )
                placed += 1

        return placed

    def build_kappa_strategy_instructions(
        self,
        response: FinanceAgentResponse,
        state: MarketSimulationStateUpdate,
        selection: BookSelection,
        predictions: dict[int, DirectionForecast],
        regime: MarketRegime,
    ) -> dict:
        """
        Three-layer Kappa strategy:
          1. Maintenance on INACTIVE books (round-trip limits for Kappa observations)
          2. Alpha on GREEN books when regime allows (directional round-trip limits)
          3. Skip RED avoid books (no directional aggression)
        """
        stats = {
            "maintenance_books": 0,
            "maintenance_instructions": 0,
            "alpha_books": 0,
            "alpha_instructions": 0,
            "skipped_avoid": len(selection.avoid_books),
            "skipped_volume": 0,
            "skipped_fee": 0,
            "skipped_regime": 0,
        }
        cfg = state.config
        if not cfg or not state.books:
            self._last_strategy_stats = stats
            return stats

        avoid_set = set(selection.avoid_books)
        profile_by_id = {p.book_id: p for p in selection.profiles}
        expiry = cfg.publish_interval

        # Layer 1: maintenance — boost under SCORING_PRESSURE
        maint_limit = self.max_maintenance_books_per_tick
        if regime.scoring_overlay == "SCORING_PRESSURE":
            maint_limit = min(
                len(selection.maintenance_books),
                self.max_maintenance_books_per_tick * 2,
            )
        maint_books = [
            b for b in selection.maintenance_books
            if b not in avoid_set and b in state.books
        ][:maint_limit]

        pnl_estimates: list[RealizedPnLEstimate] = []
        for book_id in maint_books:
            est = self._estimate_plan_for_book(
                state, book_id, self.maintenance_order_size, "maintenance", "SYMMETRIC",
            )
            if est:
                pnl_estimates.append(est)

        if not self._alpha_regime_allows(regime):
            pass
        else:
            alpha_books_preview = [
                b for b in selection.alpha_books
                if b not in avoid_set and b in state.books and b in predictions
            ][:self.max_alpha_books_per_tick]
            for book_id in alpha_books_preview:
                pred = predictions[book_id]
                if pred.direction == "HOLD":
                    continue
                profile = profile_by_id.get(book_id)
                scale = 1.0
                if profile:
                    scale = max(0.5, min(2.0, 1.0 + profile.alpha_rank))
                size = self.alpha_order_size * scale
                trip_dir: Literal["UP", "DOWN"] = (
                    "UP" if pred.direction == "UP" else "DOWN"
                )
                est = self._estimate_plan_for_book(
                    state, book_id, size, "alpha", trip_dir,
                )
                if est:
                    pnl_estimates.append(est)

        self._last_pnl_estimates = pnl_estimates
        if self.log_predict_pnl and pnl_estimates:
            self._log_predict_pnl(pnl_estimates)

        for book_id in maint_books:
            placed = self._place_round_trip_limits(
                response,
                state,
                book_id,
                self.maintenance_order_size,
                post_only=True,
                expiry_period=expiry,
                client_id_base=10000,
            )
            if placed == 0 and not self._can_add_volume(
                state,
                self.maintenance_order_size * (state.books[book_id].bids[0].price if state.books[book_id].bids else 300) * 2,
            ):
                stats["skipped_volume"] += 1
            elif placed > 0:
                stats["maintenance_books"] += 1
                stats["maintenance_instructions"] += placed

        # Layer 2: alpha — gated by regime
        if not self._alpha_regime_allows(regime):
            stats["skipped_regime"] = len(selection.alpha_books)
        else:
            alpha_books = [
                b for b in selection.alpha_books
                if b not in avoid_set and b in state.books and b in predictions
            ][:self.max_alpha_books_per_tick]

            for book_id in alpha_books:
                pred = predictions[book_id]
                if pred.direction == "HOLD":
                    continue
                profile = profile_by_id.get(book_id)
                scale = 1.0
                if profile:
                    scale = max(0.5, min(2.0, 1.0 + profile.alpha_rank))
                size = self.alpha_order_size * scale
                placed = self._place_directional_round_trip(
                    response,
                    state,
                    book_id,
                    pred.direction,
                    size,
                    client_id_base=50000,
                )
                if placed == 0 and not self._passes_fee_gate(book_id, aggressive=False):
                    stats["skipped_fee"] += 1
                elif placed == 0:
                    stats["skipped_volume"] += 1
                elif placed > 0:
                    stats["alpha_books"] += 1
                    stats["alpha_instructions"] += placed

        # Layer 3: avoid books — no new orders (logged via skipped_avoid)

        self._last_strategy_stats = stats
        return stats

    def _log_kappa_strategy_calibration(
        self,
        state: MarketSimulationStateUpdate,
        selection: BookSelection,
        regime: MarketRegime,
        stats: dict,
    ) -> None:
        raw_median = None
        if self._last_kappa:
            m = self._last_kappa.get("median")
            raw_median = round(m, 6) if m is not None else None

        norm_median_proxy = self._estimate_local_normalized_median()
        book_count = len(selection.profiles) or regime.book_count
        max_inactive = int(self.max_inactive_books_ratio * book_count)
        inactive_count = selection.tier_counts.get("INACTIVE", 0)

        payload = {
            "tick": self._tick,
            "regime": regime.mode,
            "scoring_overlay": regime.scoring_overlay,
            "tier_counts": selection.tier_counts,
            "inactive_count": inactive_count,
            "max_inactive_allowed": max_inactive,
            "raw_kappa_median": raw_median,
            "norm_kappa_median_proxy": (
                round(norm_median_proxy, 6) if norm_median_proxy is not None else None
            ),
            "volume_cap_remaining": round(self._volume_cap_remaining(state), 2),
            "volume_cap_total": round(self._volume_cap_quote(state), 2),
            "alpha_books": selection.alpha_books[:8],
            "maintain_books": selection.maintenance_books[:8],
            "avoid_books": selection.avoid_books[:8],
            "strategy_stats": stats,
        }
        bt.logging.info(f"[KAPPA_STRATEGY] {json.dumps(payload)}")

    # ----- Main hooks --------------------------------------------------------

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        """
        INPUT:  state (MarketSimulationStateUpdate) — see parse_state() and module docstring.
        OUTPUT: FinanceAgentResponse with instructions[] sent back to validator → simulator.

        After update() ran, also available on self:
            self.accounts     — dict[book_id, Account] for this UID
            self.events        — list of notices for this UID this tick
            self.simulation_config — same as state.config
        """
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
        strategy_stats: dict = {}
        if state.books and not in_grace:
            if self.enable_kappa_strategy:
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
            self.enable_kappa_strategy or self.enable_trading
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

    def report(
        self,
        state: MarketSimulationStateUpdate,
        response: FinanceAgentResponse,
    ) -> None:
        """Optional: log OUTPUT size every tick when instructions were sent."""
        if response.instructions:
            bt.logging.debug(
                f"Tick {self._tick}: submitted {len(response.instructions)} instruction(s) "
                f"at T={state.timestamp}"
            )

    def _log_input(self, summary: StateSummary) -> None:
        header = {
            "tick": self._tick,
            "uid": self.uid,
            "validator": summary.validator_hotkey,
            "T": summary.simulation_timestamp_ns,
            "sim_time": summary.simulation_time_human,
            "books": summary.book_count,
            "notices": summary.notices_count,
            "notice_types": summary.notice_types,
            "miner_wealth": summary.miner_wealth,
            "publish_interval_ns": summary.publish_interval_ns,
        }
        bt.logging.info(f"[INPUT] {json.dumps(header)}")

        # First 3 books only — avoid log spam on 40+ books
        for snap in summary.books[:3]:
            mom = ""
            if snap.log_return is not None:
                mom = f" mom_log={snap.log_return:.6f} mom_pct={snap.pct_momentum:.4%}"
            bt.logging.info(
                f"[INPUT book {snap.book_id}] "
                f"bid={snap.best_bid} ask={snap.best_ask} spread={snap.spread} "
                f"events={snap.event_count} trades={snap.trade_count} "
                f"last={snap.last_trade_price}@{snap.last_trade_qty}{mom}"
            )
        if len(summary.books) > 3:
            bt.logging.info(f"[INPUT] … {len(summary.books) - 3} more books omitted")

        for snap in summary.accounts[:3]:
            bt.logging.info(
                f"[INPUT account book {snap.book_id}] "
                f"BASE total={snap.base_total:.4f} free={snap.base_free:.4f} "
                f"QUOTE total={snap.quote_total:.2f} free={snap.quote_free:.2f} "
                f"orders={snap.open_orders} traded_vol={snap.traded_volume} "
                f"fees m/t={snap.maker_fee_rate}/{snap.taker_fee_rate}"
            )

    def _log_momentum_and_pnl(self, summary: StateSummary, state: MarketSimulationStateUpdate) -> None:
        timestamp = state.timestamp
        pnl_summary = self._summarize_pnl_history()
        mom_sample = [
            {
                "book": s.book_id,
                "mid": s.mid,
                "log_return": round(s.log_return, 8) if s.log_return is not None else None,
                "pct": round(s.pct_momentum, 6) if s.pct_momentum is not None else None,
            }
            for s in summary.books[:5]
        ]
        bt.logging.info(
            f"[MOMENTUM] window_ticks={self.momentum_window_ticks} "
            f"books_sample={json.dumps(mom_sample)}"
        )
        bt.logging.info(
            f"[REALIZED_PNL] T={timestamp} buckets={pnl_summary['bucket_count']} "
            f"total={pnl_summary['total_all_books']:.4f} "
            f"by_book={json.dumps({k: round(v, 4) for k, v in pnl_summary['total_by_book'].items()})}"
        )
        if pnl_summary["recent_buckets"]:
            bt.logging.info(
                f"[REALIZED_PNL history] last buckets: "
                f"{json.dumps(pnl_summary['recent_buckets'], default=str)}"
            )
        tick_pnl = self.realized_pnl_history.get(timestamp, {})
        if tick_pnl:
            bt.logging.info(f"[REALIZED_PNL this tick] {json.dumps(tick_pnl)}")

        if self.log_kappa:
            self._log_kappa_and_sequences(state, timestamp)

        if self.pnl_log_file:
            self._append_pnl_csv(timestamp, summary.books[:10], pnl_summary)

    def _log_kappa_and_sequences(self, state: MarketSimulationStateUpdate, timestamp: int) -> None:
        cfg = state.config
        if not cfg:
            return

        book_count = cfg.book_count
        sequences = self._realized_pnl_sequences_per_book(book_count)
        truncated = self._truncate_pnl_sequences(sequences, self.pnl_sequence_max_entries)

        kappa_values = self._compute_local_kappa(state)
        self._last_kappa = kappa_values

        if kappa_values is None:
            span = 0
            if self.realized_pnl_history:
                ts_sorted = sorted(self.realized_pnl_history.keys())
                span = ts_sorted[-1] - ts_sorted[0]
            bt.logging.info(
                f"[KAPPA] not available — buckets={len(self.realized_pnl_history)} "
                f"span_ns={span} need_min_lookback_ns={self.kappa_min_lookback}"
            )
        else:
            raw_by_book = kappa_values.get("books", {})
            rounded_kappa = {
                int(b): (round(v, 6) if v is not None else None)
                for b, v in raw_by_book.items()
            }
            median_kappa = kappa_values.get("median")
            median_str = round(median_kappa, 6) if median_kappa is not None else None
            bt.logging.info(
                f"[KAPPA raw per book] {json.dumps(rounded_kappa)}"
            )
            bt.logging.info(
                f"[KAPPA median] {median_str} "
                f"(average={kappa_values.get('average')}, total={kappa_values.get('total')})"
            )

        bt.logging.info(
            f"[REALIZED_PNL sequence per book] "
            f"last_{self.pnl_sequence_max_entries}_buckets={json.dumps(truncated)}"
        )

        if self.pnl_log_file:
            self._append_kappa_csv(timestamp, kappa_values, truncated)

    def _append_kappa_csv(
        self,
        timestamp: int,
        kappa_values: dict | None,
        truncated_sequences: dict[int, list[list[float | int]]],
    ) -> None:
        import csv

        path = os.path.join(self.output_dir, "kappa_pnl_sequences.csv")
        exists = os.path.isfile(path)
        raw_by_book = (kappa_values or {}).get("books", {})
        median_kappa = (kappa_values or {}).get("median")

        with open(path, "a", newline="") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow([
                    "timestamp", "book_id", "raw_kappa", "median_kappa",
                    "pnl_sequence_json",
                ])
            if not truncated_sequences and not raw_by_book:
                writer.writerow([timestamp, "", "", median_kappa, ""])
                return
            books = sorted(set(raw_by_book.keys()) | set(truncated_sequences.keys()))
            for book_id in books:
                raw_k = raw_by_book.get(book_id)
                seq = truncated_sequences.get(book_id, [])
                writer.writerow([
                    timestamp,
                    book_id,
                    round(raw_k, 6) if raw_k is not None else "",
                    round(median_kappa, 6) if median_kappa is not None else "",
                    json.dumps(seq),
                ])

    def _append_pnl_csv(
        self,
        timestamp: int,
        books: list[BookSnapshot],
        pnl_summary: dict,
    ) -> None:
        import csv

        exists = os.path.isfile(self._pnl_csv_path)
        with open(self._pnl_csv_path, "a", newline="") as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow([
                    "timestamp", "book_id", "mid", "log_return", "pct_momentum",
                    "tick_realized_pnl", "cumulative_realized_pnl", "total_all_books",
                ])
            tick_bucket = self.realized_pnl_history.get(timestamp, {})
            total_all = pnl_summary["total_all_books"]
            for snap in books:
                writer.writerow([
                    timestamp,
                    snap.book_id,
                    snap.mid,
                    snap.log_return,
                    snap.pct_momentum,
                    tick_bucket.get(snap.book_id, 0.0),
                    self.total_realized_pnl_by_book.get(snap.book_id, 0.0),
                    total_all,
                ])

    def _log_output(self, out: ResponseSummary) -> None:
        bt.logging.info(
            f"[OUTPUT] count={out.instruction_count} types={json.dumps(out.by_type)}"
        )
        for line in out.lines[:10]:
            bt.logging.info(f"[OUTPUT] {line}")
        if len(out.lines) > 10:
            bt.logging.info(f"[OUTPUT] … {len(out.lines) - 10} more instructions omitted")


if __name__ == "__main__":
    launch(DetailedTemplateAgent)
