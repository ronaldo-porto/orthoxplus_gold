from __future__ import annotations
"""BaseStrategy — standalone SN79 V4.1 Strict deploy baseline.

This module contains the complete runtime strategy implementation directly.
It inherits FinanceSimulationAgent and does not require any external strategy
builder, generated flat module, or parent Strategy1 strategy module at runtime.

Detailed research telemetry is controlled by the launcher via --log.
"""
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
from taos.im.protocol.instructions import CancelOrdersInstruction, ClosePositionsInstruction, PlaceLimitOrderInstruction, PlaceMarketOrderInstruction
from taos.im.protocol.models import Book, Account, LoanSettlementOption, OrderCurrency, OrderDirection, STP, TimeInForce
from taos.im.utils import duration_from_timestamp
from taos.im.utils.kappa import kappa_3
import sys
import time
from collections import deque
from dataclasses import dataclass
from taos.im.protocol.models import Book, LoanSettlementOption, OrderDirection, STP, TimeInForce
import atexit
from collections import Counter
from enum import Enum
from typing import Any, Callable, TypeVar
import queue
import threading
from typing import Any
from taos.im.protocol.models import Book, OrderDirection, STP

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
    direction: Literal['UP', 'DOWN', 'HOLD']
    score: float
    momentum_m: float
    flow_f: float
    trade_t: float
    log_return: float | None
    imbalance: float
    trade_imbalance: float
BookTier = Literal['INACTIVE', 'RED', 'YELLOW', 'GREEN']

@dataclass
class BookProfile:
    """Per-book market + miner snapshot for scoring-aware book selection."""
    book_id: int
    spread: float | None
    mid: float | None
    spread_bps: float | None
    trade_rate: float
    volatility: float
    imbalance: float
    raw_kappa: float | None
    realized_pnl: float
    pnl_obs_count: int
    traded_volume: float
    predict_score: float
    predict_direction: str
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
MarketRegimeMode = Literal['QUIET', 'CHOP', 'TRENDING_UP', 'TRENDING_DOWN', 'DISPERSED', 'STRESSED', 'BROAD_LIQUID', 'MIXED']
ScoringOverlay = Literal['SCORING_PRESSURE', 'DAMAGE_CONTROL', 'SCORING_COMFORT']

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
_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)
BookArchetype = Literal['DEAD_BOOK', 'MM_BOOK', 'WALL_BOOK', 'TREND_BOOK', 'TOXIC_BOOK', 'STRESSED']
InventoryBand = Literal['FLAT', 'LONG', 'SHORT', 'MAX_LONG', 'MAX_SHORT']
InventoryReason = Literal['UNKNOWN', 'MAINTENANCE', 'MM', 'ALPHA', 'MARKET']
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
        return 0.4 * self.book_profit_factor + 0.35 * self.book_fill_factor + 0.25 * self.book_kappa_factor

@dataclass
class TuningMetrics:
    kappa_med: float
    win_rate: float
    skip_neg_rate: float
    objective: float
    window_ticks: int
TUNING_PARAM_BOUNDS: dict[str, tuple[float, float]] = {'min_expected_alpha': (0.15, 0.45), 'min_expected_realized_pnl': (0.0, 0.002), 'max_mm_books_per_tick': (4.0, 12.0), 'toxic_loss_streak': (2.0, 5.0), 'toxic_recent_pnl': (-0.05, -0.001), 'coverage_boost_weight': (0.05, 0.3)}

@dataclass
class InventorySnapshot:
    net_base: float
    inventory_ratio: float
    band: InventoryBand
    vwap_entry: float | None
    unrealized_bps: float | None
    position_ticks: int = 0
    opened_at_ns: int | None = None
    reason: InventoryReason = 'UNKNOWN'
DEFAULT_REGIME_PARAMS: dict[MarketRegimeMode, RegimeParamSet] = {'QUIET': RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=0.25, skew_strength=0.15, size_mult=0.8, profit_target_bps=5.0, stop_loss_bps=35.0, min_fill_prob=0.15), 'CHOP': RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=0.35, skew_strength=0.1, size_mult=0.7, profit_target_bps=8.0, stop_loss_bps=40.0, min_fill_prob=0.2), 'TRENDING_UP': RegimeParamSet(quote_enabled=True, alpha_enabled=True, spread_offset=0.2, skew_strength=0.3, size_mult=1.2, profit_target_bps=12.0, stop_loss_bps=45.0, min_fill_prob=0.25, buy_bias=2.0, sell_bias=0.5), 'TRENDING_DOWN': RegimeParamSet(quote_enabled=True, alpha_enabled=True, spread_offset=0.2, skew_strength=0.3, size_mult=1.2, profit_target_bps=12.0, stop_loss_bps=45.0, min_fill_prob=0.25, buy_bias=0.5, sell_bias=2.0), 'BROAD_LIQUID': RegimeParamSet(quote_enabled=True, alpha_enabled=True, spread_offset=0.22, skew_strength=0.2, size_mult=1.0, profit_target_bps=10.0, stop_loss_bps=40.0, min_fill_prob=0.2), 'DISPERSED': RegimeParamSet(quote_enabled=True, alpha_enabled=True, spread_offset=0.28, skew_strength=0.25, size_mult=0.9, profit_target_bps=10.0, stop_loss_bps=40.0, min_fill_prob=0.22), 'STRESSED': RegimeParamSet(quote_enabled=False, alpha_enabled=False, spread_offset=0.45, skew_strength=0.05, size_mult=0.5, profit_target_bps=15.0, stop_loss_bps=25.0, min_fill_prob=0.3, buy_bias=0.25, sell_bias=0.25), 'MIXED': RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=0.28, skew_strength=0.18, size_mult=0.85, profit_target_bps=8.0, stop_loss_bps=38.0, min_fill_prob=0.18)}
DEFAULT_ARCHETYPE_ADJUST: dict[BookArchetype, ArchetypeAdjust] = {'DEAD_BOOK': ArchetypeAdjust(size_mult=0.6, spread_offset_delta=0.1, min_fill_prob_delta=-0.05), 'MM_BOOK': ArchetypeAdjust(size_mult=0.3, spread_offset_delta=-0.05, skew_strength_mult=0.5), 'WALL_BOOK': ArchetypeAdjust(size_mult=0.5, spread_offset_delta=0.15, edge_bias=0.2), 'TREND_BOOK': ArchetypeAdjust(size_mult=1.0, spread_offset_delta=-0.05, skew_strength_mult=1.3, edge_bias=0.3), 'TOXIC_BOOK': ArchetypeAdjust(size_mult=0.4, spread_offset_delta=0.2, min_fill_prob_delta=0.05), 'STRESSED': ArchetypeAdjust(size_mult=0.25, quote_enabled_override=False)}
_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)
T = TypeVar('T')

class DebugReason:
    """Stable reason codes for grep, jq, and automated comparisons."""
    QUOTED = 'QUOTED'
    MANAGED_INVENTORY = 'MANAGED_INVENTORY'
    MAINTENANCE_ORDER = 'MAINTENANCE_ORDER'
    ALPHA_ORDER = 'ALPHA_ORDER'
    NO_BOOK_SIDES = 'NO_BOOK_SIDES'
    NO_PROFILE = 'NO_PROFILE'
    AVOID_LIST = 'AVOID_LIST'
    NO_PREDICTION = 'NO_PREDICTION'
    GRACE_PERIOD = 'GRACE_PERIOD'
    MANAGEMENT_LIMIT = 'MANAGEMENT_LIMIT'
    MANAGE_ORDER_GATE = 'MANAGE_ORDER_GATE'
    MAINT_INVENTORY_NONFLAT = 'MAINT_INVENTORY_NONFLAT'
    MAINT_ARCHETYPE_BLOCK = 'MAINT_ARCHETYPE_BLOCK'
    MAINT_ORDER_GATE = 'MAINT_ORDER_GATE'
    TOXIC_BOOK = 'TOXIC_BOOK'
    QUOTE_DISABLED = 'QUOTE_DISABLED'
    TOXIC_REGIME = 'TOXIC_REGIME'
    INACTIVE_TIER = 'INACTIVE_TIER'
    LOW_EXPECTED_ALPHA = 'LOW_EXPECTED_ALPHA'
    MM_CANDIDATE_LIMIT = 'MM_CANDIDATE_LIMIT'
    MAX_INVENTORY = 'MAX_INVENTORY'
    INVALID_QUOTE_PRICES = 'INVALID_QUOTE_PRICES'
    ZERO_ORDER_SIZE = 'ZERO_ORDER_SIZE'
    VOLUME_CAP = 'VOLUME_CAP'
    NON_POSITIVE_EDGE = 'NON_POSITIVE_EDGE'
    NEGATIVE_EXPECTED_PNL = 'NEGATIVE_EXPECTED_PNL'
    LOW_FILL_PROBABILITY = 'LOW_FILL_PROBABILITY'
    INSTRUCTION_LIMIT = 'INSTRUCTION_LIMIT'
    INSUFFICIENT_BALANCE = 'INSUFFICIENT_BALANCE'
    QUOTE_ORDER_GATE = 'QUOTE_ORDER_GATE'
    NO_ACTION = 'NO_ACTION'
_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)
BASESTRATEGY_PROVENANCE = {
    "policy": "V4.1 Strict",
    "direct_base": "FinanceSimulationAgent",
    "source_sha256": {
        "DetailedTemplateAgent.py": "da843173806a2d70ee09fe8df31e01dd8d69b0f20b1bb440d7c924aec2cacb92",
        "Strategy1.py": "ea4fb4c7e2fcd43de48fc8fa75cbf75ca4a46a64e0620ad0def1e6e0199b30bd",
        "Strategy1_Debug.py": "eba1de76de99b52bca739c7a643254bc165b2793ff227989c8d49d019fef06fe",
        "Strategy1_Research_v4_1_strict.py": "7a8554b712091b341c1553d99fe8523d38a44183c5a670e483cf671af1954fd8",
    },
}

class BaseStrategy(FinanceSimulationAgent):
    """Standalone V4.1 Strict deploy baseline."""
    DEPLOY_POLICY_VERSION = 'base_v4_1_strict'
    REASON_ALIAS = {'LOW_EXPECTED_ALPHA': 'ALPHA', 'ZERO_ORDER_SIZE': 'SIZE_ZERO', 'MAX_INVENTORY': 'INVENTORY_MAX', 'INVALID_QUOTE_PRICES': 'BAD_PRICE', 'VOLUME_CAP': 'VOLUME_CAP', 'NON_POSITIVE_EDGE': 'EDGE', 'NEGATIVE_EXPECTED_PNL': 'NEG_PNL', 'LOW_FILL_PROBABILITY': 'FILL_PROB', 'INSTRUCTION_LIMIT': 'INSTR_LIMIT', 'INSUFFICIENT_BALANCE': 'BALANCE', 'QUOTE_ORDER_GATE': 'QUOTE_GATE', 'QUOTE_DISABLED': 'REGIME_DISABLED', 'TOXIC_BOOK': 'TOXIC', 'TOXIC_REGIME': 'TOXIC_REGIME', 'INACTIVE_TIER': 'INACTIVE', 'MM_CANDIDATE_LIMIT': 'MM_LIMIT', 'MANAGEMENT_LIMIT': 'MANAGEMENT_LIMIT', 'MANAGE_ORDER_GATE': 'MANAGE_GATE', 'MAINT_INVENTORY_NONFLAT': 'MAINT_INVENTORY', 'MAINT_ARCHETYPE_BLOCK': 'MAINT_ARCHETYPE', 'MAINT_ORDER_GATE': 'MAINT_GATE', 'NO_BOOK_SIDES': 'NO_BOOK_SIDES', 'NO_PROFILE': 'NO_PROFILE', 'AVOID_LIST': 'AVOID', 'NO_PREDICTION': 'NO_PREDICTION', 'GRACE_PERIOD': 'GRACE', 'NO_ACTION': 'NO_ACTION', 'HARD_CAP': 'HARD_CAP', 'STALE': 'STALE', 'DUST': 'DUST', 'DUST_POSITION': 'DUST', 'INACTIVE_DIAGNOSTIC_ONLY': 'INACTIVE_DIAG', 'MM_SUCCESS_CAP': 'MM_CAP', 'DUST_QUARANTINE': 'DUST_PARK', 'DUST_RELEASED': 'DUST_RELEASE', 'DUST_COMPACT': 'DUST_COMPACT', 'DUST_COMPACT_BLOCKED': 'DUST_COMPACT_BLOCKED', 'KAPPA_COMPLETION': 'KAPPA_COMPLETE', 'KAPPA_COMPLETION_ATTEMPT_CAP': 'KAPPA_ATTEMPT_CAP', 'KAPPA_COMPLETION_SUCCESS_CAP': 'KAPPA_SUCCESS_CAP', 'NORMAL_MM_ATTEMPT_CAP': 'NORMAL_ATTEMPT_CAP'}

    def _bsimpl_0_DetailedTemplateAgent_initialize(self) -> None:
        self.fast_update = bool(getattr(self.config, 'fast_update', False))
        self.sync_event_csv = bool(getattr(self.config, 'sync_event_csv', not self.fast_update))
        self.log_latency = bool(getattr(self.config, 'log_latency', False))
        self.verbose_log = bool(getattr(self.config, 'verbose_log', True))
        self.log_every_n = int(getattr(self.config, 'log_every_n', 50))
        self.enable_trading = bool(getattr(self.config, 'enable_trading', False))
        self.demo_order_size = float(getattr(self.config, 'demo_order_size', 0.25))
        self.log_momentum_pnl = bool(getattr(self.config, 'log_momentum_pnl', True))
        self.momentum_window_ticks = max(2, int(getattr(self.config, 'momentum_window_ticks', 10)))
        self.pnl_lookback_ns = int(getattr(self.config, 'pnl_lookback_ns', 10800000000000))
        self.pnl_log_file = bool(getattr(self.config, 'pnl_log_file', False))
        self.log_kappa = bool(getattr(self.config, 'log_kappa', True))
        self.pnl_sequence_max_entries = int(getattr(self.config, 'pnl_sequence_max_entries', 32))
        self.kappa_tau = float(getattr(self.config, 'kappa_tau', 0.0))
        self.kappa_min_lookback = int(getattr(self.config, 'kappa_min_lookback_ns', 5400000000000))
        self.kappa_min_observations = int(getattr(self.config, 'kappa_min_observations', 3))
        self.kappa_norm_min = float(getattr(self.config, 'kappa_norm_min', -2.5))
        self.kappa_norm_max = float(getattr(self.config, 'kappa_norm_max', 2.5))
        self.w_m = float(getattr(self.config, 'w_m', 1.0))
        self.w_f = float(getattr(self.config, 'w_f', 1.0))
        self.w_t = float(getattr(self.config, 'w_t', 0.5))
        self.direction_threshold = float(getattr(self.config, 'direction_threshold', 0.15))
        self.momentum_scale = float(getattr(self.config, 'momentum_scale', 0.002))
        self.flow_depth = int(getattr(self.config, 'flow_depth', 5))
        self.log_direction = bool(getattr(self.config, 'log_direction', True))
        self.log_book_profile = bool(getattr(self.config, 'log_book_profile', True))
        self.max_inactive_books_ratio = float(getattr(self.config, 'max_inactive_books_ratio', 0.375))
        self.green_kappa_threshold = float(getattr(self.config, 'green_kappa_threshold', 0.0))
        self.red_kappa_threshold = float(getattr(self.config, 'red_kappa_threshold', -0.5))
        self.spread_alpha_max = float(getattr(self.config, 'spread_alpha_max', 0.002))
        self.profile_w_k = float(getattr(self.config, 'profile_w_k', 1.0))
        self.profile_w_p = float(getattr(self.config, 'profile_w_p', 0.5))
        self.profile_w_s = float(getattr(self.config, 'profile_w_s', 0.3))
        self.profile_w_v = float(getattr(self.config, 'profile_w_v', 0.2))
        self.profile_vol_scale = float(getattr(self.config, 'profile_vol_scale', 0.01))
        self.log_regime = bool(getattr(self.config, 'log_regime', True))
        self.regime_hold_frac_threshold = float(getattr(self.config, 'regime_hold_frac_threshold', 0.7))
        self.regime_trend_frac_threshold = float(getattr(self.config, 'regime_trend_frac_threshold', 0.5))
        self.regime_dispersed_frac_threshold = float(getattr(self.config, 'regime_dispersed_frac_threshold', 0.25))
        self.regime_stressed_spread_bps = float(getattr(self.config, 'regime_stressed_spread_bps', 5.0))
        self.regime_chop_vol_threshold = float(getattr(self.config, 'regime_chop_vol_threshold', 0.005))
        self.regime_active_trade_rate = float(getattr(self.config, 'regime_active_trade_rate', 2.0))
        self.enable_kappa_strategy = bool(getattr(self.config, 'enable_kappa_strategy', False))
        self.maintenance_order_size = float(getattr(self.config, 'maintenance_order_size', 0.25))
        self.alpha_order_size = float(getattr(self.config, 'alpha_order_size', 0.25))
        self.max_alpha_books_per_tick = max(1, int(getattr(self.config, 'max_alpha_books_per_tick', 4)))
        self.max_maintenance_books_per_tick = max(1, int(getattr(self.config, 'max_maintenance_books_per_tick', 3)))
        self.capital_turnover_cap = float(getattr(self.config, 'capital_turnover_cap', 10.0))
        self.max_taker_fee_rate = float(getattr(self.config, 'max_taker_fee_rate', 0.015))
        self.max_instructions_per_book = max(1, int(getattr(self.config, 'max_instructions_per_book', 5)))
        self.min_order_size = float(getattr(self.config, 'min_order_size', 0.25))
        self.log_kappa_strategy = bool(getattr(self.config, 'log_kappa_strategy', True))
        self.log_predict_pnl = bool(getattr(self.config, 'log_predict_pnl', True))
        self._last_kappa: dict | None = None
        self._last_pnl_estimates: list[RealizedPnLEstimate] = []
        self._last_strategy_stats: dict = {}
        self._last_predictions: dict[int, DirectionForecast] = {}
        self._last_profiles: list[BookProfile] = []
        self._last_selection: BookSelection | None = None
        self._last_regime: MarketRegime | None = None
        self._tick = 0
        self._mid_history: dict[int, list[tuple[int, float]]] = defaultdict(list)
        self.realized_pnl_history: dict[int, dict[int, float]] = {}
        self.total_realized_pnl_by_book: dict[int, float] = defaultdict(float)
        self._open_positions: dict[int, dict[str, Deque[tuple[int, float, float, float]]]] = defaultdict(lambda: {'longs': deque(), 'shorts': deque()})
        self._scoring_timestamp: int = 0
        self._pnl_tick_buffer: dict[int, float] = {}
        self._pnl_csv_path = os.path.join(self.output_dir, 'momentum_pnl_log.csv')

    def _bsimpl_0_DetailedTemplateAgent__reset_pnl_state(self) -> None:
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

    def _bsimpl_0_DetailedTemplateAgent__dispatch_notice_event(self, event, state: MarketSimulationStateUpdate, validator: str) -> bool:
        """Handle one notice; return True if simulation ended."""
        etype = event.type
        if etype in ('RESET_AGENTS', 'RA'):
            return False
        if etype in ('EVENT_SIMULATION_START', 'ESS'):
            self.onStart(event)
            return False
        if etype in ('EVENT_SIMULATION_END', 'ESE'):
            self.onEnd(event)
            return True
        match etype:
            case 'RESPONSE_DISTRIBUTED_PLACE_ORDER_LIMIT' | 'RESPONSE_DISTRIBUTED_PLACE_ORDER_MARKET' | 'RDPOL' | 'RDPOM':
                self.onOrderAccepted(event)
                if self.sync_event_csv:
                    self.log_order_event(event, state)
            case 'ERROR_RESPONSE_DISTRIBUTED_PLACE_ORDER_LIMIT' | 'ERROR_RESPONSE_DISTRIBUTED_PLACE_ORDER_MARKET' | 'ERDPOL' | 'ERDPOM':
                self.onOrderRejected(event)
            case 'RESPONSE_DISTRIBUTED_CANCEL_ORDERS' | 'RDCO':
                for cancellation in event.cancellations:
                    self.onOrderCancelled(cancellation)
                    if self.sync_event_csv:
                        self.log_cancellation_event(cancellation, state)
            case 'ERROR_RESPONSE_DISTRIBUTED_CANCEL_ORDERS' | 'ERDCO':
                for cancellation in event.cancellations:
                    self.onOrderCancellationFailed(cancellation)
            case 'RESPONSE_DISTRIBUTED_CLOSE_POSITIONS' | 'RDCP':
                for close in event.closes:
                    self.onPositionClosed(close)
            case 'ERROR_RESPONSE_DISTRIBUTED_CLOSE_POSITIONS' | 'ERDCP':
                for close in event.closes:
                    self.onPositionCloseFailed(close)
            case 'EVENT_TRADE' | 'ET':
                self.onTrade(event, validator)
                if self.sync_event_csv:
                    self.log_trade_event(event, state)
            case _:
                if etype not in ('EVENT_TRADE', 'ET'):
                    bt.logging.warning(f'Unknown event : {event}')
        return False

    def _bsimpl_0_DetailedTemplateAgent__fast_update(self, state: MarketSimulationStateUpdate) -> None:
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

    def _bsimpl_0_DetailedTemplateAgent_update(self, state: MarketSimulationStateUpdate) -> None:
        """Process notices (incl. onTrade) then flush tick PnL into history."""
        self._scoring_timestamp = state.timestamp
        self._pnl_tick_buffer = {}
        if self.fast_update:
            self._fast_update(state)
        else:
            super(BaseStrategy, self).update(state)
        if self._pnl_tick_buffer:
            ts = self._scoring_timestamp
            bucket = self.realized_pnl_history.setdefault(ts, {})
            for book_id, pnl in self._pnl_tick_buffer.items():
                bucket[book_id] = round(bucket.get(book_id, 0.0) + pnl, 10)
                self.total_realized_pnl_by_book[book_id] += pnl
        self._prune_pnl_history(state.timestamp)

    def _bsimpl_0_DetailedTemplateAgent_onStart(self, event: SimulationStartEvent) -> None:
        self._reset_pnl_state()
        bt.logging.info(f'Simulation start — reset momentum / realized PnL history ({event.logDir})')

    def _bsimpl_0_DetailedTemplateAgent_onTrade(self, event: TradeEvent, validator: str | None=None) -> None:
        if event.bookId is None:
            return
        is_taker = self.uid == event.takerAgentId
        is_maker = self.uid == event.makerAgentId
        if not is_taker and (not is_maker):
            return
        is_buy = is_taker and event.side == 0 or (is_maker and event.side == 1)
        fee = event.makerFee if is_maker else event.takerFee
        realized_pnl, _ = self._match_trade_fifo(event.bookId, is_buy, event.quantity, event.price, fee, event.timestamp)
        if realized_pnl != 0.0:
            self._pnl_tick_buffer[event.bookId] = self._pnl_tick_buffer.get(event.bookId, 0.0) + realized_pnl

    def _bsimpl_0_DetailedTemplateAgent__match_trade_fifo(self, book_id: int, is_buy: bool, quantity: float, price: float, fee: float, timestamp: int) -> tuple[float, float]:
        """FIFO round-trip matcher — same logic as validator _match_trade_fifo."""
        positions = self._open_positions[book_id]
        longs = positions['longs']
        shorts = positions['shorts']
        if is_buy:
            if not shorts:
                open_fee = fee if quantity > 0 else 0.0
                longs.append((timestamp, quantity, price, open_fee))
                return (0.0, 0.0)
        elif not longs:
            open_fee = fee if quantity > 0 else 0.0
            shorts.append((timestamp, quantity, price, open_fee))
            return (0.0, 0.0)
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
        return (realized_pnl, roundtrip_volume)

    def _bsimpl_0_DetailedTemplateAgent__copy_positions_for_book(self, book_id: int) -> dict[str, Deque[tuple[int, float, float, float]]]:
        pos = self._open_positions[book_id]
        return {'longs': deque(pos['longs']), 'shorts': deque(pos['shorts'])}

    def _bsimpl_0_DetailedTemplateAgent__simulate_fifo_pnl(self, positions: dict[str, Deque[tuple[int, float, float, float]]], is_buy: bool, quantity: float, price: float, fee: float, timestamp: int) -> tuple[float, float]:
        """FIFO matcher on a copied position book — does not mutate live state."""
        longs = positions['longs']
        shorts = positions['shorts']
        if is_buy:
            if not shorts:
                open_fee = fee if quantity > 0 else 0.0
                longs.append((timestamp, quantity, price, open_fee))
                return (0.0, 0.0)
        elif not longs:
            open_fee = fee if quantity > 0 else 0.0
            shorts.append((timestamp, quantity, price, open_fee))
            return (0.0, 0.0)
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
        return (realized_pnl, roundtrip_volume)

    def _bsimpl_0_DetailedTemplateAgent__estimate_trade_fee(self, book_id: int, quantity: float, price: float, is_maker: bool) -> float:
        account = self.accounts.get(book_id)
        if not account or not account.fees or quantity <= 0 or (price <= 0):
            return 0.0
        rate = account.fees.maker_fee_rate if is_maker else account.fees.taker_fee_rate
        return quantity * price * rate

    def _bsimpl_0_DetailedTemplateAgent__book_has_open_lots(self, book_id: int) -> bool:
        pos = self._open_positions.get(book_id)
        if not pos:
            return False
        return len(pos['longs']) > 0 or len(pos['shorts']) > 0

    def _bsimpl_0_DetailedTemplateAgent_estimate_realized_pnl(self, book_id: int, is_buy: bool, quantity: float, price: float, is_maker: bool=True, timestamp: int=0) -> float:
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

    def _bsimpl_0_DetailedTemplateAgent_estimate_round_trip_pnl(self, book_id: int, buy_price: float, sell_price: float, quantity: float, is_maker: bool=True, direction: Literal['UP', 'DOWN', 'SYMMETRIC']='SYMMETRIC', timestamp: int=0) -> RealizedPnLEstimate:
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
        if direction in ('UP', 'SYMMETRIC'):
            dir_label = 'UP' if direction == 'UP' else None
            leg_first_pnl, _ = self._simulate_fifo_pnl(positions, True, quantity, buy_price, buy_fee, timestamp)
            leg_second_pnl, _ = self._simulate_fifo_pnl(positions, False, quantity, sell_price, sell_fee, timestamp)
        else:
            dir_label = 'DOWN'
            leg_first_pnl, _ = self._simulate_fifo_pnl(positions, False, quantity, sell_price, sell_fee, timestamp)
            leg_second_pnl, _ = self._simulate_fifo_pnl(positions, True, quantity, buy_price, buy_fee, timestamp)
        return RealizedPnLEstimate(book_id=book_id, layer='', quantity=quantity, buy_price=buy_price, sell_price=sell_price, leg_first_pnl=leg_first_pnl, leg_second_pnl=leg_second_pnl, expected_realized_pnl=leg_first_pnl + leg_second_pnl, closes_existing_position=closes_existing, direction=dir_label, is_maker_assumed=is_maker)

    def _bsimpl_0_DetailedTemplateAgent__estimate_plan_for_book(self, state: MarketSimulationStateUpdate, book_id: int, size: float, layer: str, direction: Literal['UP', 'DOWN', 'SYMMETRIC']='SYMMETRIC') -> RealizedPnLEstimate | None:
        cfg = state.config
        book = state.books.get(book_id)
        if not cfg or not book or (not book.bids) or (not book.asks):
            return None
        vol_dec = cfg.volumeDecimals
        price_dec = cfg.priceDecimals
        qty = self._round_order_size(size, vol_dec)
        best_bid = round(book.bids[0].price, price_dec)
        best_ask = round(book.asks[0].price, price_dec)
        is_maker = self._prefer_maker(book_id)
        trip_dir: Literal['UP', 'DOWN', 'SYMMETRIC'] = direction
        if direction == 'SYMMETRIC':
            trip_dir = 'SYMMETRIC'
        estimate = self.estimate_round_trip_pnl(book_id, best_bid, best_ask, qty, is_maker=is_maker, direction=trip_dir, timestamp=state.timestamp)
        estimate.layer = layer
        if direction in ('UP', 'DOWN'):
            estimate.direction = direction
        return estimate

    def _bsimpl_0_DetailedTemplateAgent__log_predict_pnl(self, estimates: list[RealizedPnLEstimate]) -> None:
        if not estimates:
            return
        rows = [{'book': e.book_id, 'layer': e.layer, 'qty': e.quantity, 'bid': e.buy_price, 'ask': e.sell_price, 'leg1': round(e.leg_first_pnl, 6), 'leg2': round(e.leg_second_pnl, 6), 'expected_pnl': round(e.expected_realized_pnl, 6), 'closes_existing': e.closes_existing_position, 'dir': e.direction, 'maker': e.is_maker_assumed} for e in estimates]
        total = sum((e.expected_realized_pnl for e in estimates))
        bt.logging.info(f'[PREDICT_PNL] plans={len(estimates)} total_expected={round(total, 6)} detail={json.dumps(rows)}')

    def _bsimpl_0_DetailedTemplateAgent__update_momentum(self, book_id: int, timestamp: int, mid: float | None) -> tuple[float | None, float | None]:
        """Rolling log-return and percent change over momentum_window_ticks."""
        if mid is None or mid <= 0:
            return (None, None)
        hist = self._mid_history[book_id]
        if not hist or hist[-1][0] != timestamp:
            hist.append((timestamp, mid))
            if len(hist) > self.momentum_window_ticks:
                del hist[:-self.momentum_window_ticks]
        if len(hist) < 2:
            return (None, None)
        _, old_mid = hist[0]
        if old_mid <= 0:
            return (None, None)
        log_return = math.log(mid / old_mid)
        pct_momentum = (mid - old_mid) / old_mid
        return (log_return, pct_momentum)

    def _bsimpl_0_DetailedTemplateAgent__prune_pnl_history(self, current_timestamp: int) -> None:
        threshold = current_timestamp - self.pnl_lookback_ns
        self.realized_pnl_history = {ts: books for ts, books in self.realized_pnl_history.items() if ts >= threshold}

    def _bsimpl_0_DetailedTemplateAgent__summarize_pnl_history(self, max_entries: int=8) -> dict:
        """Recent scoring buckets + cumulative totals (validator-shaped)."""
        recent_ts = sorted(self.realized_pnl_history.keys())
        tail = recent_ts[-max_entries:]
        recent = {ts: dict(self.realized_pnl_history[ts]) for ts in tail}
        return {'recent_buckets': recent, 'bucket_count': len(self.realized_pnl_history), 'total_by_book': dict(self.total_realized_pnl_by_book), 'total_all_books': round(sum(self.total_realized_pnl_by_book.values()), 4)}

    def _bsimpl_0_DetailedTemplateAgent__realized_pnl_sequences_per_book(self, book_count: int) -> dict[int, list[tuple[int, float]]]:
        """Per-book time series of realized PnL at each scoring timestamp."""
        sequences: dict[int, list[tuple[int, float]]] = {b: [] for b in range(book_count)}
        for ts in sorted(self.realized_pnl_history.keys()):
            for book_id, pnl in self.realized_pnl_history[ts].items():
                if book_id in sequences:
                    sequences[book_id].append((ts, pnl))
        return sequences

    def _bsimpl_0_DetailedTemplateAgent__truncate_pnl_sequences(self, sequences: dict[int, list[tuple[int, float]]], max_entries: int) -> dict[int, list[list[float | int]]]:
        """Trim sequences for logging; format [[ts, pnl], ...] per book."""
        out: dict[int, list[list[float | int]]] = {}
        for book_id, seq in sequences.items():
            if not seq:
                continue
            tail = seq[-max_entries:]
            out[book_id] = [[ts, round(pnl, 6)] for ts, pnl in tail]
        return out

    def _bsimpl_0_DetailedTemplateAgent__book_mid(self, book: Book) -> float | None:
        if not book.bids or not book.asks:
            return None
        return (book.bids[0].price + book.asks[0].price) / 2.0

    def _bsimpl_0_DetailedTemplateAgent__normalize_momentum(self, log_return: float | None) -> float:
        """Map log-return to [-1, 1] for score term momentum_m."""
        if log_return is None:
            return 0.0
        scale = max(self.momentum_scale, 1e-12)
        return max(-1.0, min(1.0, log_return / scale))

    def _bsimpl_0_DetailedTemplateAgent__compute_flow_f(self, book: Book) -> float:
        """LOB imbalance in [-1, 1]: positive = bid-heavy (UP bias)."""
        if not book.bids and (not book.asks):
            return 0.0
        depth = self.flow_depth
        bid_n = min(depth, len(book.bids)) if book.bids else 0
        ask_n = min(depth, len(book.asks)) if book.asks else 0
        bid_vol = sum((book.bids[i].quantity for i in range(bid_n)))
        ask_vol = sum((book.asks[i].quantity for i in range(ask_n)))
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        imbalance = (bid_vol - ask_vol) / total
        return max(-1.0, min(1.0, imbalance))

    def _bsimpl_0_DetailedTemplateAgent__compute_trade_t(self, book: Book) -> tuple[float, float]:
        """
        Net trade-initiated flow in [-1, 1] from events this interval.
        side 0 = BUY-initiated, side 1 = SELL-initiated.
        """
        buy_vol = 0.0
        sell_vol = 0.0
        events = book.events or []
        for event in events:
            etype = getattr(event, 'type', None)
            if etype not in ('t', 'EVENT_TRADE', 'ET'):
                continue
            qty = float(getattr(event, 'quantity', 0.0))
            side = getattr(event, 'side', None)
            if side == 0:
                buy_vol += qty
            elif side == 1:
                sell_vol += qty
        total = buy_vol + sell_vol
        if total <= 0:
            return (0.0, 0.0)
        imbalance = (buy_vol - sell_vol) / total
        return (max(-1.0, min(1.0, imbalance)), imbalance)

    def _bsimpl_0_DetailedTemplateAgent_predict_direction(self, book_id: int, book: Book, timestamp: int) -> DirectionForecast:
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
            direction: Literal['UP', 'DOWN', 'HOLD'] = 'UP'
        elif score < -self.direction_threshold:
            direction = 'DOWN'
        else:
            direction = 'HOLD'
        return DirectionForecast(book_id=book_id, direction=direction, score=score, momentum_m=momentum_m, flow_f=flow_f, trade_t=trade_t, log_return=log_return, imbalance=imbalance, trade_imbalance=trade_imbalance)

    def _bsimpl_0_DetailedTemplateAgent__predict_all_books(self, state: MarketSimulationStateUpdate) -> dict[int, DirectionForecast]:
        if not state.books:
            return {}
        predictions = {}
        for book_id, book in state.books.items():
            predictions[book_id] = self.predict_direction(book_id, book, state.timestamp)
        self._last_predictions = predictions
        return predictions

    def _bsimpl_0_DetailedTemplateAgent__log_direction_predictions(self, predictions: dict[int, DirectionForecast], max_books: int=8) -> None:
        if not predictions:
            return
        sample = sorted(predictions.values(), key=lambda p: p.book_id)[:max_books]
        rows = [{'book': p.book_id, 'dir': p.direction, 'score': round(p.score, 4), 'm': round(p.momentum_m, 4), 'f': round(p.flow_f, 4), 't': round(p.trade_t, 4)} for p in sample]
        bt.logging.info(f'[PREDICT] w_m={self.w_m} w_f={self.w_f} w_t={self.w_t} threshold={self.direction_threshold} sample={json.dumps(rows)}')
        if len(predictions) > max_books:
            bt.logging.info(f'[PREDICT] … {len(predictions) - max_books} more books omitted')

    def _bsimpl_0_DetailedTemplateAgent__pnl_observation_count(self, book_id: int, current_ts: int) -> int:
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

    def _bsimpl_0_DetailedTemplateAgent__realized_pnl_lookback(self, book_id: int, current_ts: int) -> float:
        threshold = current_ts - self.pnl_lookback_ns
        total = 0.0
        for ts, books in self.realized_pnl_history.items():
            if ts >= threshold:
                total += books.get(book_id, 0.0)
        return total

    def _bsimpl_0_DetailedTemplateAgent__book_volatility(self, book_id: int) -> float:
        """Rolling std of log-returns from mid history."""
        hist = self._mid_history.get(book_id, [])
        if len(hist) < 3:
            return 0.0
        mids = [m for _, m in hist if m > 0]
        if len(mids) < 3:
            return 0.0
        log_rets = [math.log(mids[i] / mids[i - 1]) for i in range(1, len(mids)) if mids[i - 1] > 0]
        if len(log_rets) < 2:
            return 0.0
        mean = sum(log_rets) / len(log_rets)
        var = sum(((r - mean) ** 2 for r in log_rets)) / len(log_rets)
        return math.sqrt(var)

    def _bsimpl_0_DetailedTemplateAgent__spread_bps(self, spread: float | None, mid: float | None) -> float | None:
        if spread is None or mid is None or mid <= 0:
            return None
        return spread / mid * 10000.0

    def _bsimpl_0_DetailedTemplateAgent__compute_alpha_rank(self, raw_kappa: float | None, predict_score: float, spread: float | None, mid: float | None, volatility: float) -> float:
        norm_kappa = 0.0
        if raw_kappa is not None:
            span = max(self.kappa_norm_max - self.kappa_norm_min, 1e-12)
            norm_kappa = max(0.0, min(1.0, (raw_kappa - self.kappa_norm_min) / span))
        spread_penalty = 0.0
        if spread is not None and mid is not None and (mid > 0):
            spread_penalty = min(1.0, spread / mid)
        vol_scale = max(self.profile_vol_scale, 1e-12)
        vol_penalty = min(1.0, volatility / vol_scale)
        return self.profile_w_k * norm_kappa + self.profile_w_p * abs(predict_score) - self.profile_w_s * spread_penalty - self.profile_w_v * vol_penalty

    def _bsimpl_0_DetailedTemplateAgent__assign_book_tier(self, raw_kappa: float | None, pnl_obs_count: int, realized_pnl: float, traded_volume: float) -> BookTier:
        if pnl_obs_count < self.kappa_min_observations:
            return 'INACTIVE'
        is_red = realized_pnl < 0.0 or (raw_kappa is not None and raw_kappa < self.red_kappa_threshold)
        if is_red:
            return 'RED'
        is_green = raw_kappa is not None and raw_kappa >= self.green_kappa_threshold and (realized_pnl >= 0.0)
        if is_green:
            return 'GREEN'
        return 'YELLOW'

    def _bsimpl_0_DetailedTemplateAgent_build_book_profile(self, book_id: int, book: Book, state: MarketSimulationStateUpdate, prediction: DirectionForecast | None, raw_kappa: float | None) -> BookProfile:
        mid = self._book_mid(book)
        spread = None
        if book.bids and book.asks:
            spread = book.asks[0].price - book.bids[0].price
        imbalance = self._compute_flow_f(book)
        trade_rate = float(sum((1 for e in book.events or [] if getattr(e, 'type', None) in ('t', 'EVENT_TRADE', 'ET'))))
        volatility = self._book_volatility(book_id)
        pnl_obs_count = self._pnl_observation_count(book_id, state.timestamp)
        realized_pnl = self._realized_pnl_lookback(book_id, state.timestamp)
        traded_volume = 0.0
        if self.accounts and book_id in self.accounts:
            vol = self.accounts[book_id].traded_volume
            traded_volume = float(vol) if vol is not None else 0.0
        predict_score = prediction.score if prediction else 0.0
        predict_direction = prediction.direction if prediction else 'HOLD'
        tier = self._assign_book_tier(raw_kappa, pnl_obs_count, realized_pnl, traded_volume)
        alpha_rank = self._compute_alpha_rank(raw_kappa, predict_score, spread, mid, volatility)
        return BookProfile(book_id=book_id, spread=spread, mid=mid, spread_bps=self._spread_bps(spread, mid), trade_rate=trade_rate, volatility=volatility, imbalance=imbalance, raw_kappa=raw_kappa, realized_pnl=realized_pnl, pnl_obs_count=pnl_obs_count, traded_volume=traded_volume, predict_score=predict_score, predict_direction=predict_direction, tier=tier, alpha_rank=alpha_rank)

    def _bsimpl_0_DetailedTemplateAgent_build_all_book_profiles(self, state: MarketSimulationStateUpdate, predictions: dict[int, DirectionForecast], kappa_values: dict | None=None) -> list[BookProfile]:
        if not state.books:
            return []
        if kappa_values is None:
            kappa_values = self._compute_local_kappa(state)
        self._last_kappa = kappa_values
        raw_by_book = (kappa_values or {}).get('books', {})
        profiles = []
        for book_id, book in sorted(state.books.items()):
            raw_k = raw_by_book.get(book_id)
            if raw_k is None and book_id in raw_by_book:
                raw_k = raw_by_book[book_id]
            profiles.append(self.build_book_profile(book_id, book, state, predictions.get(book_id), raw_k))
        self._last_profiles = profiles
        return profiles

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_rank_books_for_trading(profiles: list[BookProfile], spread_alpha_max: float=0.002, max_maintenance: int | None=None) -> BookSelection:
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
        alpha_candidates = [p for p in profiles if p.tier == 'GREEN' and p.spread is not None and (p.mid is not None) and (p.mid > 0) and (p.spread / p.mid <= spread_alpha_max) and (p.predict_direction != 'HOLD')]
        alpha_books = sorted(alpha_candidates, key=lambda p: p.alpha_rank, reverse=True)
        alpha_ids = [p.book_id for p in alpha_books]
        maintenance_candidates = [p for p in profiles if p.tier == 'INACTIVE']
        maintenance_candidates.sort(key=lambda p: (p.pnl_obs_count, p.traded_volume))
        if max_maintenance is not None:
            maintenance_candidates = maintenance_candidates[:max_maintenance]
        maintenance_ids = [p.book_id for p in maintenance_candidates]
        avoid_ids = [p.book_id for p in profiles if p.tier == 'RED']
        return BookSelection(alpha_books=alpha_ids, maintenance_books=maintenance_ids, avoid_books=avoid_ids, tier_counts=dict(tier_counts), profiles=profiles)

    def _bsimpl_0_DetailedTemplateAgent_select_books_for_trading(self, state: MarketSimulationStateUpdate, predictions: dict[int, DirectionForecast]) -> BookSelection:
        """Build profiles and return scoring-aware book selection for this tick."""
        profiles = self.build_all_book_profiles(state, predictions)
        book_count = len(profiles)
        max_inactive = int(self.max_inactive_books_ratio * book_count) if book_count else 0
        selection = self.rank_books_for_trading(profiles, spread_alpha_max=self.spread_alpha_max, max_maintenance=max(max_inactive, 1) if book_count else None)
        self._last_selection = selection
        return selection

    def _bsimpl_0_DetailedTemplateAgent__log_book_profile_selection(self, selection: BookSelection) -> None:
        tier_counts = selection.tier_counts
        bt.logging.info(f'[BOOK_PROFILE] tier_counts={json.dumps(tier_counts)} max_inactive_allowed={int(self.max_inactive_books_ratio * len(selection.profiles))}')
        bt.logging.info(f'[BOOK_PROFILE] alpha={selection.alpha_books} maintain={selection.maintenance_books} avoid={selection.avoid_books}')
        sample = sorted(selection.profiles, key=lambda p: p.alpha_rank, reverse=True)[:8]
        rows = [{'book': p.book_id, 'tier': p.tier, 'rank': round(p.alpha_rank, 4), 'kappa': round(p.raw_kappa, 4) if p.raw_kappa is not None else None, 'pnl': round(p.realized_pnl, 4), 'obs': p.pnl_obs_count, 'dir': p.predict_direction, 'spread_bps': round(p.spread_bps, 2) if p.spread_bps is not None else None} for p in sample]
        bt.logging.info(f'[BOOK_PROFILE] top_by_rank={json.dumps(rows)}')

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_classify_market_regime(profiles: list[BookProfile], predictions: dict[int, DirectionForecast] | None=None, tier_counts: dict[str, int] | None=None, max_inactive_books_ratio: float=0.375, hold_frac_threshold: float=0.7, trend_frac_threshold: float=0.5, dispersed_frac_threshold: float=0.25, stressed_spread_bps: float=5.0, chop_vol_threshold: float=0.005, active_trade_rate: float=2.0) -> MarketRegime:
        """
        Classify cross-book market regime from per-book profiles.

        Modes (priority order):
            STRESSED → TRENDING_UP/DOWN → DISPERSED → CHOP → QUIET → BROAD_LIQUID → MIXED
        """
        n = len(profiles)
        if n == 0:
            return MarketRegime(mode='MIXED', hold_frac=0.0, up_frac=0.0, down_frac=0.0, mean_score=0.0, mean_abs_score=0.0, mean_volatility=0.0, mean_trade_rate=0.0, mean_spread_bps=None, mean_imbalance=0.0, mean_log_return=None, return_dispersion=None, direction_dispersion=0.0, tier_counts={}, inactive_frac=0.0, red_frac=0.0, green_frac=0.0, scoring_overlay=None, confidence=0.0, book_count=0)
        hold_n = sum((1 for p in profiles if p.predict_direction == 'HOLD'))
        up_n = sum((1 for p in profiles if p.predict_direction == 'UP'))
        down_n = sum((1 for p in profiles if p.predict_direction == 'DOWN'))
        hold_frac = hold_n / n
        up_frac = up_n / n
        down_frac = down_n / n
        scores = [p.predict_score for p in profiles]
        mean_score = sum(scores) / n
        mean_abs_score = sum((abs(s) for s in scores)) / n
        direction_dispersion = math.sqrt(sum(((s - mean_score) ** 2 for s in scores)) / n) if n > 1 else 0.0
        mean_volatility = sum((p.volatility for p in profiles)) / n
        mean_trade_rate = sum((p.trade_rate for p in profiles)) / n
        mean_imbalance = sum((p.imbalance for p in profiles)) / n
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
                return_dispersion = math.sqrt(sum(((r - m) ** 2 for r in log_returns)) / len(log_returns))
            else:
                return_dispersion = 0.0
        if tier_counts is None:
            tier_counts = defaultdict(int)
            for p in profiles:
                tier_counts[p.tier] += 1
            tier_counts = dict(tier_counts)
        inactive_frac = tier_counts.get('INACTIVE', 0) / n
        red_frac = tier_counts.get('RED', 0) / n
        green_frac = tier_counts.get('GREEN', 0) / n
        max_inactive = int(max_inactive_books_ratio * n)
        inactive_count = tier_counts.get('INACTIVE', 0)
        red_count = tier_counts.get('RED', 0)
        green_count = tier_counts.get('GREEN', 0)
        scoring_overlay: ScoringOverlay | None = None
        if inactive_count >= max(max_inactive - 1, 1):
            scoring_overlay = 'SCORING_PRESSURE'
        elif red_frac > 0.25:
            scoring_overlay = 'DAMAGE_CONTROL'
        elif green_frac > 0.5 and red_frac < 0.1:
            scoring_overlay = 'SCORING_COMFORT'
        mode: MarketRegimeMode = 'MIXED'
        confidence = 0.3
        if mean_spread_bps is not None and mean_spread_bps >= stressed_spread_bps:
            mode = 'STRESSED'
            confidence = min(1.0, mean_spread_bps / stressed_spread_bps)
        elif up_frac >= trend_frac_threshold and mean_score > 0:
            mode = 'TRENDING_UP'
            confidence = min(1.0, up_frac + abs(mean_score))
        elif down_frac >= trend_frac_threshold and mean_score < 0:
            mode = 'TRENDING_DOWN'
            confidence = min(1.0, down_frac + abs(mean_score))
        elif up_frac >= dispersed_frac_threshold and down_frac >= dispersed_frac_threshold:
            mode = 'DISPERSED'
            confidence = min(1.0, min(up_frac, down_frac) / dispersed_frac_threshold)
        elif hold_frac >= hold_frac_threshold and mean_volatility >= chop_vol_threshold:
            mode = 'CHOP'
            confidence = min(1.0, hold_frac)
        elif hold_frac >= hold_frac_threshold:
            mode = 'QUIET'
            confidence = min(1.0, hold_frac)
        elif mean_trade_rate >= active_trade_rate:
            mode = 'BROAD_LIQUID'
            confidence = min(1.0, mean_trade_rate / (active_trade_rate * 2))
        return MarketRegime(mode=mode, hold_frac=hold_frac, up_frac=up_frac, down_frac=down_frac, mean_score=mean_score, mean_abs_score=mean_abs_score, mean_volatility=mean_volatility, mean_trade_rate=mean_trade_rate, mean_spread_bps=mean_spread_bps, mean_imbalance=mean_imbalance, mean_log_return=mean_log_return, return_dispersion=return_dispersion, direction_dispersion=direction_dispersion, tier_counts=tier_counts, inactive_frac=inactive_frac, red_frac=red_frac, green_frac=green_frac, scoring_overlay=scoring_overlay, confidence=confidence, book_count=n)

    def _bsimpl_0_DetailedTemplateAgent_classify_market_regime_from_profiles(self, profiles: list[BookProfile], predictions: dict[int, DirectionForecast], selection: BookSelection | None=None) -> MarketRegime:
        """Classify regime using agent config thresholds; stores result on self."""
        tier_counts = selection.tier_counts if selection else None
        regime = self.classify_market_regime(profiles, predictions=predictions, tier_counts=tier_counts, max_inactive_books_ratio=self.max_inactive_books_ratio, hold_frac_threshold=self.regime_hold_frac_threshold, trend_frac_threshold=self.regime_trend_frac_threshold, dispersed_frac_threshold=self.regime_dispersed_frac_threshold, stressed_spread_bps=self.regime_stressed_spread_bps, chop_vol_threshold=self.regime_chop_vol_threshold, active_trade_rate=self.regime_active_trade_rate)
        self._last_regime = regime
        return regime

    def _bsimpl_0_DetailedTemplateAgent__log_market_regime(self, regime: MarketRegime) -> None:
        payload = {'mode': regime.mode, 'confidence': round(regime.confidence, 4), 'hold_frac': round(regime.hold_frac, 4), 'up_frac': round(regime.up_frac, 4), 'down_frac': round(regime.down_frac, 4), 'mean_score': round(regime.mean_score, 4), 'mean_abs_score': round(regime.mean_abs_score, 4), 'mean_vol': round(regime.mean_volatility, 6), 'mean_trade_rate': round(regime.mean_trade_rate, 2), 'mean_spread_bps': round(regime.mean_spread_bps, 2) if regime.mean_spread_bps is not None else None, 'mean_imbalance': round(regime.mean_imbalance, 4), 'mean_log_return': round(regime.mean_log_return, 8) if regime.mean_log_return is not None else None, 'return_dispersion': round(regime.return_dispersion, 8) if regime.return_dispersion is not None else None, 'direction_dispersion': round(regime.direction_dispersion, 4), 'tier_counts': regime.tier_counts, 'inactive_frac': round(regime.inactive_frac, 4), 'red_frac': round(regime.red_frac, 4), 'green_frac': round(regime.green_frac, 4), 'scoring_overlay': regime.scoring_overlay, 'books': regime.book_count}
        bt.logging.info(f'[REGIME] {json.dumps(payload)}')

    def _bsimpl_0_DetailedTemplateAgent__compute_local_kappa(self, state: MarketSimulationStateUpdate) -> dict | None:
        """Run validator kappa_3() on miner-side realized_pnl_history."""
        cfg = state.config
        if not cfg or not self.realized_pnl_history:
            return None
        pnl_values = {ts: dict(books) for ts, books in self.realized_pnl_history.items()}
        return kappa_3(self.uid, pnl_values, self.kappa_tau, self.pnl_lookback_ns, self.kappa_norm_min, self.kappa_norm_max, self.kappa_min_lookback, self.kappa_min_observations, cfg.grace_period, [], cfg.book_count, cache=None)

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_parse_book(book_id: int, book: Book, detailed_depth: int) -> BookSnapshot:
        best_bid = book.bids[0].price if book.bids else None
        best_ask = book.asks[0].price if book.asks else None
        spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
        mid = (best_bid + best_ask) / 2 if spread is not None else None
        events = book.events or []
        trade_count = sum((1 for e in events if getattr(e, 'type', None) in ('t', 'EVENT_TRADE', 'ET')))
        last_price = None
        last_qty = None
        trades = book.trades
        if trades:
            lt = trades[max(trades)]
            last_price = lt.price
            last_qty = lt.quantity
        return BookSnapshot(book_id=book_id, best_bid=best_bid, best_ask=best_ask, spread=spread, mid=mid, bid_levels=len(book.bids), ask_levels=len(book.asks), event_count=len(events), trade_count=trade_count, last_trade_price=last_price, last_trade_qty=last_qty, log_return=None, pct_momentum=None)

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_parse_account(book_id: int, account: Account) -> AccountSnapshot:
        fees = account.fees
        return AccountSnapshot(book_id=book_id, base_total=account.base_balance.total, base_free=account.base_balance.free, quote_total=account.quote_balance.total, quote_free=account.quote_balance.free, base_loan=account.base_loan, quote_loan=account.quote_loan, open_orders=len(account.orders), open_loans=len(account.loans), traded_volume=account.traded_volume, maker_fee_rate=fees.maker_fee_rate if fees else None, taker_fee_rate=fees.taker_fee_rate if fees else None)

    def _bsimpl_0_DetailedTemplateAgent_parse_notices(self, state: MarketSimulationStateUpdate) -> tuple[int, dict[str, int]]:
        events = state.notices.get(self.uid, []) if state.notices else []
        counts: dict[str, int] = {}
        for ev in events:
            t = getattr(ev, 'type', type(ev).__name__)
            counts[t] = counts.get(t, 0) + 1
        return (len(events), counts)

    def _bsimpl_0_DetailedTemplateAgent_parse_state(self, state: MarketSimulationStateUpdate) -> StateSummary:
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
        return StateSummary(simulation_timestamp_ns=state.timestamp, simulation_time_human=duration_from_timestamp(state.timestamp), validator_hotkey=state.dendrite.hotkey, taos_version=state.version, book_count=cfg.book_count if cfg else len(state.books or {}), publish_interval_ns=cfg.publish_interval if cfg else 0, miner_wealth=cfg.miner_wealth if cfg else 0.0, grace_period_ns=cfg.grace_period if cfg else 0, notices_count=notice_count, notice_types=notice_types, books=book_snaps, accounts=account_snaps)

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_parse_response(response: FinanceAgentResponse) -> ResponseSummary:
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
        return ResponseSummary(instruction_count=len(response.instructions), by_type=by_type, lines=lines)

    def _bsimpl_0_DetailedTemplateAgent_build_demo_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int=0) -> None:
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
        if account.quote_balance.free >= buy_price * size:
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=size, price=buy_price, clientOrderId=1001, stp=STP.CANCEL_OLDEST, postOnly=True, timeInForce=TimeInForce.GTC, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
        if account.base_balance.free >= size:
            response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=size, price=sell_price, clientOrderId=1002, timeInForce=TimeInForce.GTT, expiryPeriod=cfg.publish_interval, delay=0)
        if account.orders:
            response.cancel_order(book_id=book_id, order_id=account.orders[0].id, delay=0)
        if account.loans:
            loan_order_id = next(iter(account.loans.keys()))
            response.close_position(book_id=book_id, order_id=loan_order_id, delay=0)

    def _bsimpl_0_DetailedTemplateAgent__count_book_instructions(self, response: FinanceAgentResponse, book_id: int) -> int:
        return sum((1 for instr in response.instructions if getattr(instr, 'bookId', None) == book_id))

    def _bsimpl_0_DetailedTemplateAgent__round_order_size(self, size: float, vol_dec: int) -> float:
        return round(max(size, self.min_order_size), vol_dec)

    def _bsimpl_0_DetailedTemplateAgent__total_traded_volume(self) -> float:
        total = 0.0
        for account in self.accounts.values():
            vol = account.traded_volume
            if vol is not None:
                total += float(vol)
        return total

    def _bsimpl_0_DetailedTemplateAgent__volume_cap_quote(self, state: MarketSimulationStateUpdate) -> float:
        cfg = state.config
        if not cfg:
            return 0.0
        return self.capital_turnover_cap * cfg.miner_wealth

    def _bsimpl_0_DetailedTemplateAgent__volume_cap_remaining(self, state: MarketSimulationStateUpdate) -> float:
        return max(0.0, self._volume_cap_quote(state) - self._total_traded_volume())

    def _bsimpl_0_DetailedTemplateAgent__can_add_volume(self, state: MarketSimulationStateUpdate, quote_notional: float) -> bool:
        return quote_notional <= self._volume_cap_remaining(state)

    def _bsimpl_0_DetailedTemplateAgent__passes_fee_gate(self, book_id: int, aggressive: bool) -> bool:
        account = self.accounts.get(book_id)
        if not account or not account.fees:
            return True
        if aggressive and account.fees.taker_fee_rate > self.max_taker_fee_rate:
            return False
        return True

    def _bsimpl_0_DetailedTemplateAgent__prefer_maker(self, book_id: int) -> bool:
        account = self.accounts.get(book_id)
        if not account or not account.fees:
            return True
        return account.fees.maker_fee_rate <= account.fees.taker_fee_rate

    def _bsimpl_0_DetailedTemplateAgent__alpha_regime_allows(self, regime: MarketRegime) -> bool:
        if regime.mode in ('STRESSED', 'QUIET', 'CHOP'):
            return False
        if regime.mode in ('TRENDING_UP', 'TRENDING_DOWN', 'BROAD_LIQUID'):
            return True
        if regime.mode == 'DISPERSED':
            return True
        return regime.mode == 'MIXED' and regime.mean_abs_score > self.direction_threshold

    def _bsimpl_0_DetailedTemplateAgent__estimate_local_normalized_median(self) -> float | None:
        """Proxy median of normalized raw Kappas (validator uses activity-weighted values)."""
        if not self._last_kappa or not self._last_profiles:
            return None
        raw_by_book = self._last_kappa.get('books', {})
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

    def _bsimpl_0_DetailedTemplateAgent__place_round_trip_limits(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, size: float, post_only: bool=True, expiry_period: int | None=None, client_id_base: int=0) -> int:
        """Place bid+ask limit pair for round-trip potential; returns instruction count."""
        cfg = state.config
        book = state.books.get(book_id)
        account = self.accounts.get(book_id)
        if not cfg or not book or (not account):
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
                response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=qty, price=best_bid, clientOrderId=client_id_base + book_id * 10 + 1, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=tif, expiryPeriod=expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
        if account.base_balance.free >= qty:
            if self._count_book_instructions(response, book_id) < self.max_instructions_per_book:
                response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=qty, price=best_ask, clientOrderId=client_id_base + book_id * 10 + 2, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=tif, expiryPeriod=expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
        return placed

    def _bsimpl_0_DetailedTemplateAgent__place_directional_round_trip(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, direction: Literal['UP', 'DOWN'], size: float, client_id_base: int=50000) -> int:
        """
        Entry + exit limits aligned with predicted direction for round-trip discipline.
        UP: buy at bid (entry), sell at ask (exit). DOWN: sell then buy.
        """
        cfg = state.config
        book = state.books.get(book_id)
        account = self.accounts.get(book_id)
        if not cfg or not book or (not account):
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
        if direction == 'UP':
            if account.quote_balance.free >= best_bid * qty:
                response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=qty, price=best_bid, clientOrderId=client_id_base + book_id * 10 + 1, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=TimeInForce.GTC, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
            if placed > 0 and account.base_balance.free >= qty and (self._count_book_instructions(response, book_id) < self.max_instructions_per_book):
                response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=qty, price=best_ask, clientOrderId=client_id_base + book_id * 10 + 2, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
        else:
            if account.base_balance.free >= qty:
                response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=qty, price=best_ask, clientOrderId=client_id_base + book_id * 10 + 3, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=TimeInForce.GTC, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
            if placed > 0 and account.quote_balance.free >= best_bid * qty and (self._count_book_instructions(response, book_id) < self.max_instructions_per_book):
                response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=qty, price=best_bid, clientOrderId=client_id_base + book_id * 10 + 4, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
        return placed

    def _bsimpl_0_DetailedTemplateAgent_build_kappa_strategy_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime) -> dict:
        """
        Three-layer Kappa strategy:
          1. Maintenance on INACTIVE books (round-trip limits for Kappa observations)
          2. Alpha on GREEN books when regime allows (directional round-trip limits)
          3. Skip RED avoid books (no directional aggression)
        """
        stats = {'maintenance_books': 0, 'maintenance_instructions': 0, 'alpha_books': 0, 'alpha_instructions': 0, 'skipped_avoid': len(selection.avoid_books), 'skipped_volume': 0, 'skipped_fee': 0, 'skipped_regime': 0}
        cfg = state.config
        if not cfg or not state.books:
            self._last_strategy_stats = stats
            return stats
        avoid_set = set(selection.avoid_books)
        profile_by_id = {p.book_id: p for p in selection.profiles}
        expiry = cfg.publish_interval
        maint_limit = self.max_maintenance_books_per_tick
        if regime.scoring_overlay == 'SCORING_PRESSURE':
            maint_limit = min(len(selection.maintenance_books), self.max_maintenance_books_per_tick * 2)
        maint_books = [b for b in selection.maintenance_books if b not in avoid_set and b in state.books][:maint_limit]
        pnl_estimates: list[RealizedPnLEstimate] = []
        for book_id in maint_books:
            est = self._estimate_plan_for_book(state, book_id, self.maintenance_order_size, 'maintenance', 'SYMMETRIC')
            if est:
                pnl_estimates.append(est)
        if not self._alpha_regime_allows(regime):
            pass
        else:
            alpha_books_preview = [b for b in selection.alpha_books if b not in avoid_set and b in state.books and (b in predictions)][:self.max_alpha_books_per_tick]
            for book_id in alpha_books_preview:
                pred = predictions[book_id]
                if pred.direction == 'HOLD':
                    continue
                profile = profile_by_id.get(book_id)
                scale = 1.0
                if profile:
                    scale = max(0.5, min(2.0, 1.0 + profile.alpha_rank))
                size = self.alpha_order_size * scale
                trip_dir: Literal['UP', 'DOWN'] = 'UP' if pred.direction == 'UP' else 'DOWN'
                est = self._estimate_plan_for_book(state, book_id, size, 'alpha', trip_dir)
                if est:
                    pnl_estimates.append(est)
        self._last_pnl_estimates = pnl_estimates
        if self.log_predict_pnl and pnl_estimates:
            self._log_predict_pnl(pnl_estimates)
        for book_id in maint_books:
            placed = self._place_round_trip_limits(response, state, book_id, self.maintenance_order_size, post_only=True, expiry_period=expiry, client_id_base=10000)
            if placed == 0 and (not self._can_add_volume(state, self.maintenance_order_size * (state.books[book_id].bids[0].price if state.books[book_id].bids else 300) * 2)):
                stats['skipped_volume'] += 1
            elif placed > 0:
                stats['maintenance_books'] += 1
                stats['maintenance_instructions'] += placed
        if not self._alpha_regime_allows(regime):
            stats['skipped_regime'] = len(selection.alpha_books)
        else:
            alpha_books = [b for b in selection.alpha_books if b not in avoid_set and b in state.books and (b in predictions)][:self.max_alpha_books_per_tick]
            for book_id in alpha_books:
                pred = predictions[book_id]
                if pred.direction == 'HOLD':
                    continue
                profile = profile_by_id.get(book_id)
                scale = 1.0
                if profile:
                    scale = max(0.5, min(2.0, 1.0 + profile.alpha_rank))
                size = self.alpha_order_size * scale
                placed = self._place_directional_round_trip(response, state, book_id, pred.direction, size, client_id_base=50000)
                if placed == 0 and (not self._passes_fee_gate(book_id, aggressive=False)):
                    stats['skipped_fee'] += 1
                elif placed == 0:
                    stats['skipped_volume'] += 1
                elif placed > 0:
                    stats['alpha_books'] += 1
                    stats['alpha_instructions'] += placed
        self._last_strategy_stats = stats
        return stats

    def _bsimpl_0_DetailedTemplateAgent__log_kappa_strategy_calibration(self, state: MarketSimulationStateUpdate, selection: BookSelection, regime: MarketRegime, stats: dict) -> None:
        raw_median = None
        if self._last_kappa:
            m = self._last_kappa.get('median')
            raw_median = round(m, 6) if m is not None else None
        norm_median_proxy = self._estimate_local_normalized_median()
        book_count = len(selection.profiles) or regime.book_count
        max_inactive = int(self.max_inactive_books_ratio * book_count)
        inactive_count = selection.tier_counts.get('INACTIVE', 0)
        payload = {'tick': self._tick, 'regime': regime.mode, 'scoring_overlay': regime.scoring_overlay, 'tier_counts': selection.tier_counts, 'inactive_count': inactive_count, 'max_inactive_allowed': max_inactive, 'raw_kappa_median': raw_median, 'norm_kappa_median_proxy': round(norm_median_proxy, 6) if norm_median_proxy is not None else None, 'volume_cap_remaining': round(self._volume_cap_remaining(state), 2), 'volume_cap_total': round(self._volume_cap_quote(state), 2), 'alpha_books': selection.alpha_books[:8], 'maintain_books': selection.maintenance_books[:8], 'avoid_books': selection.avoid_books[:8], 'strategy_stats': stats}
        bt.logging.info(f'[KAPPA_STRATEGY] {json.dumps(payload)}')

    def _bsimpl_0_DetailedTemplateAgent_respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
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
        regime = self.classify_market_regime_from_profiles(selection.profiles, predictions, selection)
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
        if state.books and (not in_grace):
            if self.enable_kappa_strategy:
                strategy_stats = self.build_kappa_strategy_instructions(response, state, selection, predictions, regime)
                if self.log_kappa_strategy and (self._tick == 1 or self._tick % self.log_every_n == 0):
                    self._log_kappa_strategy_calibration(state, selection, regime, strategy_stats)
            elif self.enable_trading:
                self.build_demo_instructions(response, state, book_id=0)
        elif state.books and in_grace and (self.enable_kappa_strategy or self.enable_trading):
            bt.logging.info(f'Grace period active (T={state.timestamp} < {summary.grace_period_ns}); no orders placed.')
        if self.verbose_log and response.instructions and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_output(self.parse_response(response))
        return response

    def _bsimpl_0_DetailedTemplateAgent_report(self, state: MarketSimulationStateUpdate, response: FinanceAgentResponse) -> None:
        """Optional: log OUTPUT size every tick when instructions were sent."""
        if response.instructions:
            bt.logging.debug(f'Tick {self._tick}: submitted {len(response.instructions)} instruction(s) at T={state.timestamp}')

    def _bsimpl_0_DetailedTemplateAgent__log_input(self, summary: StateSummary) -> None:
        header = {'tick': self._tick, 'uid': self.uid, 'validator': summary.validator_hotkey, 'T': summary.simulation_timestamp_ns, 'sim_time': summary.simulation_time_human, 'books': summary.book_count, 'notices': summary.notices_count, 'notice_types': summary.notice_types, 'miner_wealth': summary.miner_wealth, 'publish_interval_ns': summary.publish_interval_ns}
        bt.logging.info(f'[INPUT] {json.dumps(header)}')
        for snap in summary.books[:3]:
            mom = ''
            if snap.log_return is not None:
                mom = f' mom_log={snap.log_return:.6f} mom_pct={snap.pct_momentum:.4%}'
            bt.logging.info(f'[INPUT book {snap.book_id}] bid={snap.best_bid} ask={snap.best_ask} spread={snap.spread} events={snap.event_count} trades={snap.trade_count} last={snap.last_trade_price}@{snap.last_trade_qty}{mom}')
        if len(summary.books) > 3:
            bt.logging.info(f'[INPUT] … {len(summary.books) - 3} more books omitted')
        for snap in summary.accounts[:3]:
            bt.logging.info(f'[INPUT account book {snap.book_id}] BASE total={snap.base_total:.4f} free={snap.base_free:.4f} QUOTE total={snap.quote_total:.2f} free={snap.quote_free:.2f} orders={snap.open_orders} traded_vol={snap.traded_volume} fees m/t={snap.maker_fee_rate}/{snap.taker_fee_rate}')

    def _bsimpl_0_DetailedTemplateAgent__log_momentum_and_pnl(self, summary: StateSummary, state: MarketSimulationStateUpdate) -> None:
        timestamp = state.timestamp
        pnl_summary = self._summarize_pnl_history()
        mom_sample = [{'book': s.book_id, 'mid': s.mid, 'log_return': round(s.log_return, 8) if s.log_return is not None else None, 'pct': round(s.pct_momentum, 6) if s.pct_momentum is not None else None} for s in summary.books[:5]]
        bt.logging.info(f'[MOMENTUM] window_ticks={self.momentum_window_ticks} books_sample={json.dumps(mom_sample)}')
        bt.logging.info(f"[REALIZED_PNL] T={timestamp} buckets={pnl_summary['bucket_count']} total={pnl_summary['total_all_books']:.4f} by_book={json.dumps({k: round(v, 4) for k, v in pnl_summary['total_by_book'].items()})}")
        if pnl_summary['recent_buckets']:
            bt.logging.info(f"[REALIZED_PNL history] last buckets: {json.dumps(pnl_summary['recent_buckets'], default=str)}")
        tick_pnl = self.realized_pnl_history.get(timestamp, {})
        if tick_pnl:
            bt.logging.info(f'[REALIZED_PNL this tick] {json.dumps(tick_pnl)}')
        if self.log_kappa:
            self._log_kappa_and_sequences(state, timestamp)
        if self.pnl_log_file:
            self._append_pnl_csv(timestamp, summary.books[:10], pnl_summary)

    def _bsimpl_0_DetailedTemplateAgent__log_kappa_and_sequences(self, state: MarketSimulationStateUpdate, timestamp: int) -> None:
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
            bt.logging.info(f'[KAPPA] not available — buckets={len(self.realized_pnl_history)} span_ns={span} need_min_lookback_ns={self.kappa_min_lookback}')
        else:
            raw_by_book = kappa_values.get('books', {})
            rounded_kappa = {int(b): round(v, 6) if v is not None else None for b, v in raw_by_book.items()}
            median_kappa = kappa_values.get('median')
            median_str = round(median_kappa, 6) if median_kappa is not None else None
            bt.logging.info(f'[KAPPA raw per book] {json.dumps(rounded_kappa)}')
            bt.logging.info(f"[KAPPA median] {median_str} (average={kappa_values.get('average')}, total={kappa_values.get('total')})")
        bt.logging.info(f'[REALIZED_PNL sequence per book] last_{self.pnl_sequence_max_entries}_buckets={json.dumps(truncated)}')
        if self.pnl_log_file:
            self._append_kappa_csv(timestamp, kappa_values, truncated)

    def _bsimpl_0_DetailedTemplateAgent__append_kappa_csv(self, timestamp: int, kappa_values: dict | None, truncated_sequences: dict[int, list[list[float | int]]]) -> None:
        import csv
        path = os.path.join(self.output_dir, 'kappa_pnl_sequences.csv')
        exists = os.path.isfile(path)
        raw_by_book = (kappa_values or {}).get('books', {})
        median_kappa = (kappa_values or {}).get('median')
        with open(path, 'a', newline='') as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(['timestamp', 'book_id', 'raw_kappa', 'median_kappa', 'pnl_sequence_json'])
            if not truncated_sequences and (not raw_by_book):
                writer.writerow([timestamp, '', '', median_kappa, ''])
                return
            books = sorted(set(raw_by_book.keys()) | set(truncated_sequences.keys()))
            for book_id in books:
                raw_k = raw_by_book.get(book_id)
                seq = truncated_sequences.get(book_id, [])
                writer.writerow([timestamp, book_id, round(raw_k, 6) if raw_k is not None else '', round(median_kappa, 6) if median_kappa is not None else '', json.dumps(seq)])

    def _bsimpl_0_DetailedTemplateAgent__append_pnl_csv(self, timestamp: int, books: list[BookSnapshot], pnl_summary: dict) -> None:
        import csv
        exists = os.path.isfile(self._pnl_csv_path)
        with open(self._pnl_csv_path, 'a', newline='') as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(['timestamp', 'book_id', 'mid', 'log_return', 'pct_momentum', 'tick_realized_pnl', 'cumulative_realized_pnl', 'total_all_books'])
            tick_bucket = self.realized_pnl_history.get(timestamp, {})
            total_all = pnl_summary['total_all_books']
            for snap in books:
                writer.writerow([timestamp, snap.book_id, snap.mid, snap.log_return, snap.pct_momentum, tick_bucket.get(snap.book_id, 0.0), self.total_realized_pnl_by_book.get(snap.book_id, 0.0), total_all])

    def _bsimpl_0_DetailedTemplateAgent__log_output(self, out: ResponseSummary) -> None:
        bt.logging.info(f'[OUTPUT] count={out.instruction_count} types={json.dumps(out.by_type)}')
        for line in out.lines[:10]:
            bt.logging.info(f'[OUTPUT] {line}')
        if len(out.lines) > 10:
            bt.logging.info(f'[OUTPUT] … {len(out.lines) - 10} more instructions omitted')

    def _bsimpl_1_Strategy1_initialize(self) -> None:
        self._bsimpl_0_DetailedTemplateAgent_initialize()
        cfg = self.config
        self.fast_update = bool(getattr(cfg, 'fast_update', True))
        self.sync_event_csv = bool(getattr(cfg, 'sync_event_csv', False))
        self.log_latency = bool(getattr(cfg, 'log_latency', True))
        self.history_len = int(getattr(cfg, 'history_len', 0))
        self.log_direction = bool(getattr(cfg, 'log_direction', False))
        self.log_book_profile = bool(getattr(cfg, 'log_book_profile', False))
        self.log_regime = bool(getattr(cfg, 'log_regime', False))
        self.log_momentum_pnl = bool(getattr(cfg, 'log_momentum_pnl', False))
        self.enable_mm_strategy = bool(getattr(cfg, 'enable_mm_strategy', True))
        self.enable_kappa_strategy = bool(getattr(cfg, 'enable_kappa_strategy', False))
        self.mm_base_size = float(getattr(cfg, 'mm_base_size', 0.25))
        self.max_inventory_base = float(getattr(cfg, 'max_inventory_base', 1.2))
        self.inventory_skew_strength = float(getattr(cfg, 'inventory_skew_strength', 0.35))
        self.target_inventory_ratio = float(getattr(cfg, 'target_inventory_ratio', 0.0))
        self.archetype_dead_trade_rate = float(getattr(cfg, 'archetype_dead_trade_rate', 0.1))
        self.archetype_mm_spread_bps = float(getattr(cfg, 'archetype_mm_spread_bps', 1.0))
        self.archetype_wall_imbalance = float(getattr(cfg, 'archetype_wall_imbalance', 0.6))
        self.archetype_stressed_spread_bps = float(getattr(cfg, 'archetype_stressed_spread_bps', 8.0))
        self.archetype_vol_threshold = float(getattr(cfg, 'archetype_vol_threshold', 0.006))
        self.trade_rate_ref = float(getattr(cfg, 'trade_rate_ref', 2.0))
        self.position_max_ticks = max(1, int(getattr(cfg, 'position_max_ticks', 300)))
        self.close_score_threshold = float(getattr(cfg, 'close_score_threshold', 0.8))
        self.inventory_close_threshold = float(getattr(cfg, 'inventory_close_threshold', 0.25))
        self.passive_exit_only = bool(getattr(cfg, 'passive_exit_only', True))
        self.aggressive_close_min_ticks = max(1, int(getattr(cfg, 'aggressive_close_min_ticks', 300)))
        self.maintenance_size_mult = float(getattr(cfg, 'maintenance_size_mult', 0.25))
        self.maintenance_passive_exit_only = bool(getattr(cfg, 'maintenance_passive_exit_only', True))
        self.log_mm_strategy = bool(getattr(cfg, 'log_mm_strategy', True))
        self.log_book_memory = bool(getattr(cfg, 'log_book_memory', False))
        self.mm_expiry_period = int(getattr(cfg, 'mm_expiry_period_ns', 500000000))
        self.flow_depth = max(1, int(getattr(cfg, 'flow_depth', 5)))
        self.min_expected_alpha = float(getattr(cfg, 'min_expected_alpha', 0.18))
        self.max_mm_books_per_tick = max(1, int(getattr(cfg, 'max_mm_books_per_tick', 4)))
        self.max_managed_books_per_tick = max(1, int(getattr(cfg, 'max_managed_books_per_tick', 4)))
        self.mm_skip_inactive_tier = bool(getattr(cfg, 'mm_skip_inactive_tier', True))
        self.toxic_loss_streak = max(1, int(getattr(cfg, 'toxic_loss_streak', 4)))
        self.toxic_recent_pnl = float(getattr(cfg, 'toxic_recent_pnl', -0.01))
        self.toxic_spread_bps = float(getattr(cfg, 'toxic_spread_bps', 10.0))
        self.w_micro = float(getattr(cfg, 'w_micro', 0.5))
        self.w_micro_vel = float(getattr(cfg, 'w_micro_vel', 0.4))
        self.w_deep = float(getattr(cfg, 'w_deep', 0.38))
        self.w_persist = float(getattr(cfg, 'w_persist', 0.32))
        self.deep_imbalance_end = max(2, int(getattr(cfg, 'deep_imbalance_end', 5)))
        self.trade_persistence_len = max(5, int(getattr(cfg, 'trade_persistence_len', 20)))
        self.micro_vel_scale = float(getattr(cfg, 'micro_vel_scale', 8.0))
        self.direction_accuracy_weight = float(getattr(cfg, 'direction_accuracy_weight', 0.12))
        self.book_specialization_weight = float(getattr(cfg, 'book_specialization_weight', 0.22))
        self.fill_learn_blend = float(getattr(cfg, 'fill_learn_blend', 0.45))
        self.fill_learn_min_samples = max(3, int(getattr(cfg, 'fill_learn_min_samples', 5)))
        self.coverage_boost_weight = float(getattr(cfg, 'coverage_boost_weight', 0.08))
        self.min_expected_realized_pnl = float(getattr(cfg, 'min_expected_realized_pnl', 0.0))
        self.enable_auto_tuning = bool(getattr(cfg, 'enable_auto_tuning', False))
        self.allow_tuning_config = bool(getattr(cfg, 'allow_tuning_config', False))
        self.tuning_interval_ns = int(getattr(cfg, 'tuning_interval_ns', 3600000000000))
        self.log_tuning = bool(getattr(cfg, 'log_tuning', True))
        _tuning_path = getattr(cfg, 'tuning_config_path', None)
        self._tuning_config_path = str(_tuning_path) if _tuning_path else os.path.join(self.output_dir, 'tuning.json')
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
        self.monitor_top_miners = bool(getattr(cfg, 'monitor_top_miners', False))
        self.monitor_top_n = max(1, int(getattr(cfg, 'monitor_top_n', 5)))
        if self.allow_tuning_config and os.path.isfile(self._tuning_config_path):
            self._reload_tuning_config_if_changed(force=True)
        self._clamp_tuning_params()
        bt.logging.info(f'Strategy1: mm={self.enable_mm_strategy} base_size={self.mm_base_size} max_inv={self.max_inventory_base} min_alpha={self.min_expected_alpha} max_mm_books={self.max_mm_books_per_tick} max_managed={self.max_managed_books_per_tick} skip_inactive_mm={self.mm_skip_inactive_tier} inv_close_thr={self.inventory_close_threshold} close_score={self.close_score_threshold} passive_exit={self.passive_exit_only} agg_min_ticks={self.aggressive_close_min_ticks} maint_size_mult={self.maintenance_size_mult} min_exp_pnl={self.min_expected_realized_pnl} fast_update={self.fast_update} sync_csv={self.sync_event_csv} log_latency={self.log_latency} history_len={self.history_len} coverage_w={self.coverage_boost_weight} max_mm={self.max_mm_books_per_tick} w_micro_vel={self.w_micro_vel} w_deep={self.w_deep} w_persist={self.w_persist} fill_learn={self.fill_learn_blend} spec_w={self.book_specialization_weight} toxic_streak={self.toxic_loss_streak} auto_tune={self.enable_auto_tuning} monitor_top={self.monitor_top_miners}')

    def _bsimpl_1_Strategy1_handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        """Instrument update/respond/report phases when log_latency is enabled."""
        if not self.log_latency:
            return super(BaseStrategy, self).handle(state)
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
            bt.logging.info(f'[LATENCY] tick={self._tick} update={round(t_update, 3)}s respond={round(t_respond, 3)}s report={round(t_report, 3)}s total={round(t_update + t_respond + t_report, 3)}s notices={notices} ix={len(response.instructions)}')
        return response

    def _bsimpl_1_Strategy1__passes_expected_pnl_gate(self, expected_realized_pnl: float) -> bool:
        return expected_realized_pnl > self.min_expected_realized_pnl

    def _bsimpl_1_Strategy1__mem(self, book_id: int) -> BookMemory:
        return self.book_memory.setdefault(book_id, BookMemory())

    def _bsimpl_1_Strategy1__spread_dist_bucket(self, dist_from_touch: float) -> int:
        """Map spread-normalized distance (0=touch, higher=inside book) to bucket 0..2."""
        if dist_from_touch <= 0.22:
            return 0
        if dist_from_touch <= 0.4:
            return 1
        return 2

    def _bsimpl_1_Strategy1__record_fill_quote(self, mem: BookMemory, side: Literal['buy', 'sell'], dist_from_touch: float) -> None:
        bucket = self._spread_dist_bucket(dist_from_touch)
        if side == 'buy':
            q = list(mem.fill_buy_quotes)
            q[bucket] += 1
            mem.fill_buy_quotes = tuple(q)
            mem.last_buy_dist_bucket = bucket
        else:
            q = list(mem.fill_sell_quotes)
            q[bucket] += 1
            mem.fill_sell_quotes = tuple(q)
            mem.last_sell_dist_bucket = bucket

    def _bsimpl_1_Strategy1__record_fill_hit(self, mem: BookMemory, side: Literal['buy', 'sell']) -> None:
        if side == 'buy':
            f = list(mem.fill_buy_fills)
            f[mem.last_buy_dist_bucket] += 1
            mem.fill_buy_fills = tuple(f)
        else:
            f = list(mem.fill_sell_fills)
            f[mem.last_sell_dist_bucket] += 1
            mem.fill_sell_fills = tuple(f)

    def _bsimpl_1_Strategy1__learned_side_fill_prob(self, mem: BookMemory, side: Literal['buy', 'sell'], dist_from_touch: float) -> float | None:
        bucket = self._spread_dist_bucket(dist_from_touch)
        if side == 'buy':
            quotes, fills = (mem.fill_buy_quotes[bucket], mem.fill_buy_fills[bucket])
        else:
            quotes, fills = (mem.fill_sell_quotes[bucket], mem.fill_sell_fills[bucket])
        if quotes < self.fill_learn_min_samples:
            return None
        return fills / quotes

    def _bsimpl_1_Strategy1__update_direction_accuracy(self, book_id: int, mid: float) -> None:
        pending = self._dir_pending.get(book_id)
        if not pending or mid <= 0 or pending.get('mid', 0) <= 0:
            return
        log_ret = math.log(mid / pending['mid'])
        thr = max(self.momentum_scale, 1e-09)
        pred = pending.get('direction', 'HOLD')
        if pred == 'HOLD':
            return
        mem = self._mem(book_id)
        if pred == 'UP':
            if log_ret > thr:
                mem.direction_hits += 1
            elif log_ret < -thr:
                mem.direction_misses += 1
        elif pred == 'DOWN':
            if log_ret < -thr:
                mem.direction_hits += 1
            elif log_ret > thr:
                mem.direction_misses += 1

    def _bsimpl_1_Strategy1__compute_l2_l5_imbalance(self, book: Book) -> float:
        """Deep-book imbalance on L2–L5 (exclude touch). Positive = bid-heavy."""
        if not book.bids and (not book.asks):
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

    def _bsimpl_1_Strategy1__update_trade_sign_history(self, book_id: int, book: Book, timestamp: int) -> None:
        if self._trade_signs_tick.get(book_id) == timestamp:
            return
        self._trade_signs_tick[book_id] = timestamp
        hist = self._trade_signs.setdefault(book_id, deque(maxlen=self.trade_persistence_len))
        for event in book.events or []:
            etype = getattr(event, 'type', None)
            if etype not in ('t', 'EVENT_TRADE', 'ET'):
                continue
            side = getattr(event, 'side', None)
            if side == 0:
                hist.append(1.0)
            elif side == 1:
                hist.append(-1.0)

    def _bsimpl_1_Strategy1__trade_persistence(self, book_id: int) -> float:
        hist = self._trade_signs.get(book_id)
        if not hist:
            return 0.0
        return max(-1.0, min(1.0, sum(hist) / len(hist)))

    def _bsimpl_1_Strategy1__kappa_factor_from_tier(self, tier: str) -> float:
        return {'GREEN': 1.0, 'YELLOW': 0.65, 'RED': 0.25, 'INACTIVE': 0.35}.get(tier, 0.5)

    def _bsimpl_1_Strategy1__sync_kappa_factor(self, mem: BookMemory, profile: BookProfile) -> None:
        k = self._kappa_factor_from_tier(profile.tier)
        mem.book_kappa_factor = 0.85 * mem.book_kappa_factor + 0.15 * k

    def _bsimpl_1_Strategy1__update_book_specialization(self, mem: BookMemory) -> None:
        mem.book_fill_factor = 0.88 * mem.book_fill_factor + 0.12 * mem.fill_rate
        pnl_component = max(0.0, min(1.0, 0.5 + mem.recent_pnl * 50.0))
        profit_blend = 0.65 * mem.win_rate + 0.35 * pnl_component
        mem.book_profit_factor = 0.9 * mem.book_profit_factor + 0.1 * profit_blend

    def _bsimpl_1_Strategy1__global_book_rank(self, expected_alpha: float, mem: BookMemory) -> float:
        spec = mem.specialization_score
        return expected_alpha * (0.72 + 0.28 * spec) + 0.12 * spec

    def _bsimpl_1_Strategy1__reset_pnl_state(self) -> None:
        self._bsimpl_0_DetailedTemplateAgent__reset_pnl_state()
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

    def _bsimpl_1_Strategy1__snapshot_tuning_params(self) -> dict[str, float | int]:
        return {'min_expected_alpha': self.min_expected_alpha, 'min_expected_realized_pnl': self.min_expected_realized_pnl, 'max_mm_books_per_tick': self.max_mm_books_per_tick, 'toxic_loss_streak': self.toxic_loss_streak, 'toxic_recent_pnl': self.toxic_recent_pnl, 'coverage_boost_weight': self.coverage_boost_weight}

    def _bsimpl_1_Strategy1__clamp_tuning_params(self) -> None:
        for key, (lo, hi) in TUNING_PARAM_BOUNDS.items():
            val = getattr(self, key)
            if key in ('max_mm_books_per_tick', 'toxic_loss_streak'):
                setattr(self, key, int(max(lo, min(hi, val))))
            else:
                setattr(self, key, max(lo, min(hi, val)))

    def _bsimpl_1_Strategy1__apply_tuning_overrides(self, overrides: dict) -> list[str]:
        applied: list[str] = []
        for key, raw in overrides.items():
            if key not in TUNING_PARAM_BOUNDS:
                continue
            lo, hi = TUNING_PARAM_BOUNDS[key]
            if key in ('max_mm_books_per_tick', 'toxic_loss_streak'):
                val = int(max(lo, min(hi, float(raw))))
            else:
                val = max(lo, min(hi, float(raw)))
            setattr(self, key, val)
            applied.append(key)
        return applied

    def _bsimpl_1_Strategy1__reload_tuning_config_if_changed(self, force: bool=False) -> list[str]:
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
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            bt.logging.warning(f'[TUNING] Failed to read {path}: {exc}')
            return []
        if not isinstance(data, dict):
            return []
        overrides = data.get('params', data)
        if not isinstance(overrides, dict):
            return []
        applied = self._apply_tuning_overrides(overrides)
        self._tuning_config_mtime = mtime
        self._clamp_tuning_params()
        if applied and self.log_tuning:
            bt.logging.info(f'[TUNING] hot-reload {path} applied={applied} snapshot={json.dumps(self._snapshot_tuning_params())}')
        return applied

    def _bsimpl_1_Strategy1__accumulate_tuning_window(self, stats: dict) -> None:
        if not self.enable_auto_tuning:
            return
        self._tuning_window['ticks'] = self._tuning_window.get('ticks', 0) + 1
        for key in ('skipped_negative_pnl', 'skipped_low_alpha', 'skipped_toxic', 'quoted', 'maintenance', 'instructions'):
            self._tuning_window[key] = self._tuning_window.get(key, 0) + int(stats.get(key, 0))

    def _bsimpl_1_Strategy1__aggregate_book_memory_win_rate(self) -> float:
        wins = sum((m.win_count for m in self.book_memory.values()))
        losses = sum((m.loss_count for m in self.book_memory.values()))
        return wins / max(wins + losses, 1)

    def _bsimpl_1_Strategy1__compute_tuning_metrics(self) -> TuningMetrics:
        kappa_med = self._estimate_local_normalized_median() or 0.0
        win_rate = self._aggregate_book_memory_win_rate()
        ticks = max(1, self._tuning_window.get('ticks', 1))
        skip_neg = self._tuning_window.get('skipped_negative_pnl', 0)
        skip_neg_rate = min(1.0, skip_neg / ticks)
        objective = 0.5 * kappa_med + 0.3 * win_rate - 0.2 * skip_neg_rate
        return TuningMetrics(kappa_med=kappa_med, win_rate=win_rate, skip_neg_rate=skip_neg_rate, objective=objective, window_ticks=ticks)

    def _bsimpl_1_Strategy1__apply_tuning_rules(self, metrics: TuningMetrics) -> None:
        if metrics.skip_neg_rate > 0.12:
            self.min_expected_alpha = min(0.45, self.min_expected_alpha + 0.02)
            self.min_expected_realized_pnl = min(0.002, self.min_expected_realized_pnl + 5e-05)
            self.max_mm_books_per_tick = max(4, self.max_mm_books_per_tick - 1)
        if metrics.win_rate < 0.45:
            self.min_expected_alpha = min(0.45, self.min_expected_alpha + 0.02)
            self.toxic_loss_streak = max(2, self.toxic_loss_streak - 1)
            self.toxic_recent_pnl = min(-0.001, self.toxic_recent_pnl - 0.002)
        if metrics.kappa_med > 0.55 and metrics.win_rate > 0.5 and (metrics.skip_neg_rate < 0.05):
            self.max_mm_books_per_tick = min(12, self.max_mm_books_per_tick + 1)
            self.min_expected_alpha = max(0.15, self.min_expected_alpha - 0.01)
        if self._last_tuning_objective > 0.0 and metrics.objective < self._last_tuning_objective - 0.04:
            self.max_mm_books_per_tick = max(4, self.max_mm_books_per_tick - 1)
            self.min_expected_alpha = min(0.45, self.min_expected_alpha + 0.03)
        self._clamp_tuning_params()

    def _bsimpl_1_Strategy1__persist_tuning_state(self, metrics: TuningMetrics) -> None:
        path = os.path.join(self.output_dir, 'tuning_state.json')
        history: list[dict] = []
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as f:
                    payload = json.load(f)
                history = payload.get('history', [])
            except (OSError, json.JSONDecodeError):
                history = []
        entry = {'objective': round(metrics.objective, 6), 'kappa_med': round(metrics.kappa_med, 4), 'win_rate': round(metrics.win_rate, 4), 'skip_neg_rate': round(metrics.skip_neg_rate, 4), 'window_ticks': metrics.window_ticks, 'params': self._snapshot_tuning_params()}
        history.append(entry)
        if len(history) > 96:
            history = history[-96:]
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'history': history, 'last': entry}, f, indent=2)
        except OSError as exc:
            bt.logging.warning(f'[TUNING] Failed to write {path}: {exc}')

    def _bsimpl_1_Strategy1__maybe_run_tuning_scheduler(self, state: MarketSimulationStateUpdate) -> None:
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
            bt.logging.info(f'[TUNING] step objective={round(metrics.objective, 4)} kappa_med={round(metrics.kappa_med, 4)} win_rate={round(metrics.win_rate, 4)} skip_neg_rate={round(metrics.skip_neg_rate, 4)} window_ticks={metrics.window_ticks} params={json.dumps(self._snapshot_tuning_params())}')
        self._last_tuning_objective = metrics.objective
        self._tuning_window = {}
        self._last_tuning_ts = now

    def _bsimpl_1_Strategy1__reason_from_client_id(self, client_id: int) -> InventoryReason:
        if client_id >= ALPHA_CLIENT_ID_BASE:
            return 'ALPHA'
        if client_id >= MM_CLIENT_ID_BASE:
            return 'MM'
        if client_id >= MAINT_CLIENT_ID_BASE:
            return 'MAINTENANCE'
        return 'UNKNOWN'

    def _bsimpl_1_Strategy1__inventory_util(self, inventory: InventorySnapshot) -> float:
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        return abs(inventory.inventory_ratio) / max(max_ratio, 1e-09)

    def _bsimpl_1_Strategy1__inventory_needs_management(self, inventory: InventorySnapshot) -> bool:
        if inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        return self._inventory_util(inventory) >= self.inventory_close_threshold

    def _bsimpl_1_Strategy1__maintenance_allowed(self, profile: BookProfile, archetype: BookArchetype) -> bool:
        if archetype in ('WALL_BOOK', 'TOXIC_BOOK', 'TREND_BOOK', 'STRESSED', 'DEAD_BOOK'):
            return False
        if archetype == 'MM_BOOK':
            return True
        if profile.tier == 'GREEN':
            return True
        return False

    def _bsimpl_1_Strategy1__allows_aggressive_close(self, book_id: int, inventory: InventorySnapshot, close_score: float, time_stop: bool, stop_loss_hit: bool) -> bool:
        if stop_loss_hit or inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        reason = self._inventory_reason.get(book_id, inventory.reason)
        if reason == 'MAINTENANCE' and self.maintenance_passive_exit_only:
            return False
        if self.passive_exit_only and inventory.position_ticks < self.aggressive_close_min_ticks:
            return False
        if close_score >= self.close_score_threshold:
            return True
        if time_stop:
            return True
        return False

    def _bsimpl_1_Strategy1_onTrade(self, event, validator: str | None=None) -> None:
        book_id = event.bookId
        pnl_before = 0.0
        net_before = 0.0
        flat_eps = self.mm_base_size * 0.001
        if book_id is not None:
            pnl_before = self._pnl_tick_buffer.get(book_id, 0.0)
            net_before = self._position_tracker_snapshot(book_id).net_qty
        self._bsimpl_0_DetailedTemplateAgent_onTrade(event, validator)
        if book_id is None:
            return
        is_taker = self.uid == event.takerAgentId
        is_maker = self.uid == event.makerAgentId
        if not is_taker and (not is_maker):
            return
        net_after = self._position_tracker_snapshot(book_id).net_qty
        if abs(net_after) < flat_eps:
            self._inventory_reason.pop(book_id, None)
        elif abs(net_after) > abs(net_before) or (abs(net_before) < flat_eps and abs(net_after) >= flat_eps):
            if is_maker and event.clientOrderId is not None:
                self._inventory_reason[book_id] = self._reason_from_client_id(event.clientOrderId)
            elif is_taker:
                self._inventory_reason[book_id] = 'MARKET'
        mem = self._mem(book_id)
        mem.fill_count += 1
        mem.last_activity_ts = event.timestamp
        if is_maker:
            agent_buy = is_taker and event.side == 0 or (is_maker and event.side == 1)
            self._record_fill_hit(mem, 'buy' if agent_buy else 'sell')
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

    def _bsimpl_1_Strategy1_microprice_signal(self, book: Book) -> float:
        if not book.bids or not book.asks:
            return 0.0
        bid = book.bids[0].price
        ask = book.asks[0].price
        bid_qty = book.bids[0].quantity
        ask_qty = book.asks[0].quantity
        mid = 0.5 * (bid + ask)
        micro = (ask * bid_qty + bid * ask_qty) / max(bid_qty + ask_qty, 1e-09)
        spread = ask - bid
        if spread <= 0:
            return 0.0
        return max(-1.0, min(1.0, (micro - mid) / spread))

    def _bsimpl_1_Strategy1_predict_direction(self, book_id: int, book: Book, timestamp: int) -> DirectionForecast:
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
        micro_vel_sig = max(-1.0, min(1.0, micro_vel * self.micro_vel_scale))
        imbalance = 0.55 * flow_f + 0.45 * deep_imb
        score = self.w_m * momentum_m + self.w_f * flow_f + self.w_t * trade_t + self.w_micro * micro + self.w_micro_vel * micro_vel_sig + self.w_deep * deep_imb + self.w_persist * trade_persist
        self._mem(book_id).last_signal = score
        if score > self.direction_threshold:
            direction: Literal['UP', 'DOWN', 'HOLD'] = 'UP'
        elif score < -self.direction_threshold:
            direction = 'DOWN'
        else:
            direction = 'HOLD'
        if mid > 0:
            self._dir_pending[book_id] = {'direction': direction, 'mid': mid, 'timestamp': timestamp}
        return DirectionForecast(book_id=book_id, direction=direction, score=score, momentum_m=momentum_m, flow_f=flow_f, trade_t=trade_t, log_return=log_return, imbalance=imbalance, trade_imbalance=trade_imbalance)

    def _bsimpl_1_Strategy1_coverage_priority(self, book_id: int, now: int) -> float:
        mem = self._mem(book_id)
        if mem.last_activity_ts <= 0:
            return 1.0
        age = max(0, now - mem.last_activity_ts)
        return min(1.0, age / max(self.pnl_lookback_ns, 1))

    def _bsimpl_1_Strategy1_is_toxic_book(self, book_id: int, profile: BookProfile, archetype: BookArchetype) -> bool:
        mem = self._mem(book_id)
        return mem.loss_streak >= self.toxic_loss_streak or mem.recent_pnl < self.toxic_recent_pnl or (profile.spread_bps is not None and profile.spread_bps > self.toxic_spread_bps) or (archetype == 'STRESSED') or (profile.tier == 'RED')

    def _bsimpl_1_Strategy1__tier_mm_boost(self, tier: str) -> float:
        """Prefer MM on books with kappa history (GREEN/YELLOW) over cold INACTIVE."""
        return {'GREEN': 0.18, 'YELLOW': 0.1, 'RED': -0.05, 'INACTIVE': -0.12}.get(tier, 0.0)

    def _bsimpl_1_Strategy1__inventory_urgency(self, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> float:
        close_score = self._compute_close_score(inventory, regime_params, regime, archetype)
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        inv_risk = min(1.0, abs(inventory.inventory_ratio) / max(max_ratio, 1e-09))
        time_risk = min(1.0, inventory.position_ticks / self.position_max_ticks)
        loss_risk = 0.0
        if inventory.unrealized_bps is not None and inventory.unrealized_bps < 0:
            loss_risk = min(1.0, abs(inventory.unrealized_bps) / regime_params.stop_loss_bps)
        return close_score + inv_risk + time_risk + loss_risk

    def _bsimpl_1_Strategy1_expected_alpha_score(self, profile: BookProfile, prediction: DirectionForecast, fill_est: FillProbabilityEstimate, mem: BookMemory, book_id: int, now: int) -> float:
        self._sync_kappa_factor(mem, profile)
        signal = min(1.0, abs(prediction.score))
        fill = 0.5 * (fill_est.buy + fill_est.sell)
        memory_bonus = 0.5 * mem.win_rate + 0.5 * mem.fill_rate
        accuracy_bonus = mem.direction_accuracy
        pnl_bonus = max(-1.0, min(1.0, mem.recent_pnl * 100))
        coverage = self.coverage_priority(book_id, now)
        tier_boost = self._tier_mm_boost(profile.tier)
        specialization = mem.specialization_score
        score = 0.26 * signal + 0.2 * fill + 0.12 * memory_bonus + self.direction_accuracy_weight * accuracy_bonus + 0.06 * pnl_bonus + self.coverage_boost_weight * coverage + self.book_specialization_weight * specialization + tier_boost
        mem.last_expected_alpha = score
        return score

    def _bsimpl_1_Strategy1__schedule_maintenance_books(self, selection: BookSelection, now: int, limit: int | None=None) -> list[int]:
        cap = limit if limit is not None else self.max_maintenance_books_per_tick
        candidates = list(selection.maintenance_books)
        inactive_ids = [p.book_id for p in selection.profiles if p.tier == 'INACTIVE']
        for book_id in inactive_ids:
            if book_id not in candidates:
                candidates.append(book_id)
        candidates.sort(key=lambda b: self.coverage_priority(b, now), reverse=True)
        return candidates[:cap]

    def _bsimpl_1_Strategy1_get_regime_params(self, regime: MarketRegime) -> RegimeParamSet:
        params = DEFAULT_REGIME_PARAMS.get(regime.mode, DEFAULT_REGIME_PARAMS['MIXED'])
        if regime.scoring_overlay == 'SCORING_PRESSURE':
            return RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=params.spread_offset, skew_strength=params.skew_strength * 0.5, size_mult=min(params.size_mult, 0.6), profit_target_bps=params.profit_target_bps, stop_loss_bps=params.stop_loss_bps, min_fill_prob=params.min_fill_prob, buy_bias=params.buy_bias, sell_bias=params.sell_bias)
        return params

    def _bsimpl_1_Strategy1_merge_regime_and_archetype_params(self, regime_params: RegimeParamSet, archetype: BookArchetype) -> RegimeParamSet:
        adj = DEFAULT_ARCHETYPE_ADJUST.get(archetype, ArchetypeAdjust())
        quote_enabled = adj.quote_enabled_override if adj.quote_enabled_override is not None else regime_params.quote_enabled
        return RegimeParamSet(quote_enabled=quote_enabled, alpha_enabled=regime_params.alpha_enabled, spread_offset=max(0.05, regime_params.spread_offset + adj.spread_offset_delta), skew_strength=regime_params.skew_strength * adj.skew_strength_mult, size_mult=regime_params.size_mult * adj.size_mult, profit_target_bps=regime_params.profit_target_bps, stop_loss_bps=regime_params.stop_loss_bps, min_fill_prob=max(0.05, min(0.95, regime_params.min_fill_prob + adj.min_fill_prob_delta)), buy_bias=regime_params.buy_bias, sell_bias=regime_params.sell_bias)

    def _bsimpl_1_Strategy1_get_archetype_edge_bias(self, archetype: BookArchetype) -> float:
        adj = DEFAULT_ARCHETYPE_ADJUST.get(archetype, ArchetypeAdjust())
        return adj.edge_bias

    def _bsimpl_1_Strategy1_classify_book_archetype(self, profile: BookProfile, regime: MarketRegime) -> BookArchetype:
        spread_bps = profile.spread_bps or 0.0
        if spread_bps >= self.archetype_stressed_spread_bps or regime.mode == 'STRESSED':
            return 'STRESSED'
        if profile.trade_rate < self.archetype_dead_trade_rate:
            return 'DEAD_BOOK'
        if spread_bps < self.archetype_mm_spread_bps:
            return 'MM_BOOK'
        if abs(profile.imbalance) >= self.archetype_wall_imbalance:
            return 'WALL_BOOK'
        if profile.volatility >= self.archetype_vol_threshold and abs(profile.predict_score) >= self.direction_threshold:
            return 'TREND_BOOK'
        if profile.volatility >= self.archetype_vol_threshold:
            return 'TOXIC_BOOK'
        if abs(profile.predict_score) >= self.direction_threshold:
            return 'TREND_BOOK'
        return 'TOXIC_BOOK'

    def _bsimpl_1_Strategy1__position_tracker_snapshot(self, book_id: int) -> PositionTracker:
        pos = self._open_positions.get(book_id)
        if not pos:
            return PositionTracker(0.0, None, None, 0.0, 0.0)
        long_qty = sum((q for _, q, _, _ in pos['longs']))
        short_qty = sum((q for _, q, _, _ in pos['shorts']))
        net_qty = long_qty - short_qty
        vwap: float | None = None
        opened_at: int | None = None
        if net_qty > 0 and long_qty > 0:
            vwap = sum((q * p for _, q, p, _ in pos['longs'])) / long_qty
            opened_at = pos['longs'][0][0]
        elif net_qty < 0 and short_qty > 0:
            vwap = sum((q * p for _, q, p, _ in pos['shorts'])) / short_qty
            opened_at = pos['shorts'][0][0]
        return PositionTracker(net_qty, vwap, opened_at, long_qty, short_qty)

    def _bsimpl_1_Strategy1__wealth_per_book(self) -> float:
        if not self.simulation_config:
            return 0.0
        return self.simulation_config.miner_wealth / max(self.simulation_config.book_count, 1)

    def _bsimpl_1_Strategy1__net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        if mid <= 0:
            return InventorySnapshot(0.0, 0.0, 'FLAT', None, None, 0)
        tracker = self._position_tracker_snapshot(book_id)
        net_base = tracker.net_qty
        wealth_per_book = self._wealth_per_book()
        inventory_ratio = 0.0
        if wealth_per_book > 0:
            inventory_ratio = net_base * mid / wealth_per_book
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        flat_eps = self.mm_base_size * 0.001
        if abs(net_base) < flat_eps:
            band: InventoryBand = 'FLAT'
            self._position_ticks.pop(book_id, None)
            self._inventory_reason.pop(book_id, None)
        elif net_base > 0:
            band = 'MAX_LONG' if inventory_ratio >= max_ratio else 'LONG'
            self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
        else:
            band = 'MAX_SHORT' if abs(inventory_ratio) >= max_ratio else 'SHORT'
            self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
        position_ticks = self._position_ticks.get(book_id, 0)
        vwap = tracker.vwap_entry
        unrealized_bps: float | None = None
        if vwap and vwap > 0:
            if net_base > 0:
                unrealized_bps = (mid - vwap) / vwap * 10000.0
            elif net_base < 0:
                unrealized_bps = (vwap - mid) / vwap * 10000.0
        return InventorySnapshot(net_base=net_base, inventory_ratio=inventory_ratio, band=band, vwap_entry=vwap, unrealized_bps=unrealized_bps, position_ticks=position_ticks, opened_at_ns=tracker.opened_at_ns, reason=self._inventory_reason.get(book_id, 'UNKNOWN'))

    def _bsimpl_1_Strategy1__log_book_memory_sample(self, state: MarketSimulationStateUpdate) -> None:
        rows: list[dict] = []
        for book_id in sorted(state.books.keys())[:10]:
            mem = self._mem(book_id)
            pos = self._position_tracker_snapshot(book_id)
            rows.append({'book': book_id, 'win': round(mem.win_rate, 3), 'fill': round(mem.fill_rate, 3), 'pnl': round(mem.recent_pnl, 5), 'streak': mem.loss_streak, 'quotes': mem.quote_count, 'fills': mem.fill_count, 'net': round(pos.net_qty, 4), 'vwap': round(pos.vwap_entry or 0.0, 4), 'ea': round(mem.last_expected_alpha, 4), 'dir_acc': round(mem.direction_accuracy, 3), 'profit_f': round(mem.book_profit_factor, 3), 'fill_f': round(mem.book_fill_factor, 3), 'kappa_f': round(mem.book_kappa_factor, 3), 'spec': round(mem.specialization_score, 3), 'fb': list(mem.fill_buy_fills), 'fs': list(mem.fill_sell_fills)})
        bt.logging.info(f'[BOOK_MEMORY] {json.dumps(rows)}')

    def _bsimpl_1_Strategy1_estimate_fill_probability(self, book: Book, mid: float, spread: float, trade_rate: float, buy_price: float, sell_price: float, book_id: int | None=None) -> FillProbabilityEstimate:
        if spread <= 0 or mid <= 0:
            return FillProbabilityEstimate(0.0, 0.0)
        trade_factor = min(1.0, trade_rate / max(self.trade_rate_ref, 1e-09))
        bid_depth = book.bids[0].quantity if book.bids else 0.0
        ask_depth = book.asks[0].quantity if book.asks else 0.0
        deep_bid = bid_depth
        deep_ask = ask_depth
        if book.bids:
            deep_bid = sum((book.bids[i].quantity for i in range(min(self.deep_imbalance_end, len(book.bids)))))
        if book.asks:
            deep_ask = sum((book.asks[i].quantity for i in range(min(self.deep_imbalance_end, len(book.asks)))))
        total_bid = deep_bid if deep_bid > 0 else sum((l.quantity for l in book.bids)) if book.bids else bid_depth
        total_ask = deep_ask if deep_ask > 0 else sum((l.quantity for l in book.asks)) if book.asks else ask_depth
        buy_dist = (mid - buy_price) / spread
        sell_dist = (sell_price - mid) / spread
        buy_depth_f = bid_depth / max(total_bid, 1e-09)
        sell_depth_f = ask_depth / max(total_ask, 1e-09)
        depth_buy = trade_rate / max(bid_depth + 1.0, 1e-09)
        depth_sell = trade_rate / max(ask_depth + 1.0, 1e-09)
        depth_buy = min(1.0, depth_buy / max(self.trade_rate_ref, 1e-09))
        depth_sell = min(1.0, depth_sell / max(self.trade_rate_ref, 1e-09))
        dist_buy = max(0.0, 1.0 - buy_dist)
        dist_sell = max(0.0, 1.0 - sell_dist)
        p_buy = trade_factor * (0.25 * buy_depth_f + 0.35 * dist_buy + 0.4 * depth_buy)
        p_sell = trade_factor * (0.25 * sell_depth_f + 0.35 * dist_sell + 0.4 * depth_sell)
        if book_id is not None:
            mem = self._mem(book_id)
            mem_blend = 0.1
            p_buy = (1.0 - mem_blend) * p_buy + mem_blend * mem.fill_rate
            p_sell = (1.0 - mem_blend) * p_sell + mem_blend * mem.fill_rate
            buy_touch_dist = max(0.0, 0.5 - buy_dist)
            sell_touch_dist = max(0.0, 0.5 - sell_dist)
            learned_buy = self._learned_side_fill_prob(mem, 'buy', buy_touch_dist)
            learned_sell = self._learned_side_fill_prob(mem, 'sell', sell_touch_dist)
            if learned_buy is not None:
                p_buy = (1.0 - self.fill_learn_blend) * p_buy + self.fill_learn_blend * learned_buy
            if learned_sell is not None:
                p_sell = (1.0 - self.fill_learn_blend) * p_sell + self.fill_learn_blend * learned_sell
        return FillProbabilityEstimate(buy=max(0.0, min(1.0, p_buy)), sell=max(0.0, min(1.0, p_sell)))

    def _bsimpl_1_Strategy1_dynamic_order_size(self, base_size: float, profile: BookProfile, regime_params: RegimeParamSet, inventory: InventorySnapshot, vol_dec: int, mid: float | None=None) -> float:
        confidence = max(0.5, min(2.0, 1.0 + abs(profile.predict_score)))
        vol_scale = 1.0
        if profile.volatility > 0:
            target_vol = self.profile_vol_scale
            vol_scale = max(0.5, min(2.0, target_vol / profile.volatility))
        spread_factor = 1.0
        if profile.spread is not None and mid is not None and (mid > 0):
            spread_bps = profile.spread / mid * 10000.0
            spread_factor = max(0.5, min(1.5, 1.0 - spread_bps / 20.0))
        kappa_scale = 1.0
        if profile.raw_kappa is not None:
            kappa_scale = max(0.5, min(1.5, 1.0 + profile.raw_kappa * 0.2))
        inv_util = abs(inventory.inventory_ratio)
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        inventory_factor = max(0.3, 1.0 - inv_util / max(max_ratio, 1e-09))
        size = base_size * confidence * regime_params.size_mult * vol_scale * spread_factor * kappa_scale * inventory_factor
        return self._round_order_size(size, vol_dec)

    def _bsimpl_1_Strategy1_skewed_quote_prices(self, bid: float, ask: float, signal: float, inventory_ratio: float, regime_params: RegimeParamSet, price_dec: int, edge_bias: float=0.0) -> tuple[float, float] | None:
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
        return (bid_px, ask_px)

    def _bsimpl_1_Strategy1__compute_close_score(self, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> float:
        unreal = inventory.unrealized_bps
        target = max(regime_params.profit_target_bps, 1e-09)
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
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        inventory_risk = min(1.0, abs(inventory.inventory_ratio) / max(max_ratio, 1e-09))
        regime_risk = 0.0
        if regime.mode == 'STRESSED':
            regime_risk = 1.0
        elif archetype in ('TOXIC_BOOK', 'WALL_BOOK'):
            regime_risk = 0.6
        elif archetype == 'DEAD_BOOK':
            regime_risk = 0.4
        time_risk = min(1.0, inventory.position_ticks / self.position_max_ticks)
        return 0.5 * pnl_component + 0.3 * inventory_risk + 0.2 * max(regime_risk, time_risk)

    def _bsimpl_1_Strategy1__clear_position_state(self, book_id: int) -> None:
        self._position_ticks.pop(book_id, None)
        self._inventory_reason.pop(book_id, None)

    def _bsimpl_1_Strategy1__place_passive_inventory_exit(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book: Book, inventory: InventorySnapshot, qty: float) -> int:
        """Single-sided passive exit: ask for long, bid for short."""
        long_pos = inventory.net_base > 0
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        close_px = round(book.bids[0].price if close_dir == OrderDirection.BUY else book.asks[0].price, state.config.priceDecimals)
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return 0
        account = self.accounts[book_id]
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.limit_order(book_id=book_id, direction=close_dir, quantity=qty, price=close_px, stp=STP.CANCEL_BOTH, postOnly=self._prefer_maker(book_id), timeInForce=TimeInForce.GTT, expiryPeriod=self.mm_expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            return 1
        if close_dir == OrderDirection.BUY and account.quote_balance.free >= qty * close_px:
            response.limit_order(book_id=book_id, direction=close_dir, quantity=qty, price=close_px, stp=STP.CANCEL_BOTH, postOnly=self._prefer_maker(book_id), timeInForce=TimeInForce.GTT, expiryPeriod=self.mm_expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            return 1
        return 0

    def _bsimpl_1_Strategy1__try_close_loans(self, response: FinanceAgentResponse, book_id: int, unrealized_bps: float | None, profit_target_bps: float) -> bool:
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

    def _bsimpl_1_Strategy1__execute_aggressive_close(self, response: FinanceAgentResponse, book_id: int, book: Book, qty: float, long_pos: bool) -> bool:
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        if not self._passes_fee_gate(book_id, aggressive=True):
            return False
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return False
        account = self.accounts[book_id]
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.market_order(book_id=book_id, direction=close_dir, quantity=qty, stp=STP.CANCEL_OLDEST, delay=0)
            self._clear_position_state(book_id)
            return True
        if close_dir == OrderDirection.BUY:
            px = book.asks[0].price
            if account.quote_balance.free >= qty * px:
                response.market_order(book_id=book_id, direction=close_dir, quantity=qty, stp=STP.CANCEL_OLDEST, delay=0)
                self._clear_position_state(book_id)
                return True
        return False

    def _bsimpl_1_Strategy1__manage_inventory(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book: Book, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> int:
        if inventory.band == 'FLAT':
            return 0
        placed = 0
        mid = (book.bids[0].price + book.asks[0].price) / 2.0
        qty = self._round_order_size(abs(inventory.net_base), state.config.volumeDecimals)
        if qty <= 0:
            return 0
        if self._try_close_loans(response, book_id, inventory.unrealized_bps, regime_params.profit_target_bps):
            placed += 1
        close_score = self._compute_close_score(inventory, regime_params, regime, archetype)
        long_pos = inventory.net_base > 0
        time_stop = inventory.position_ticks >= self.position_max_ticks
        stop_loss_hit = inventory.unrealized_bps is not None and inventory.unrealized_bps <= -regime_params.stop_loss_bps
        aggressive_close = self._allows_aggressive_close(book_id, inventory, close_score, time_stop, stop_loss_hit)
        if aggressive_close:
            if self._execute_aggressive_close(response, book_id, book, qty, long_pos):
                placed += 1
            return placed
        placed += self._place_passive_inventory_exit(response, state, book_id, book, inventory, qty)
        return placed

    def _bsimpl_1_Strategy1__place_skewed_quotes(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book: Book, profile: BookProfile, prediction: DirectionForecast, inventory: InventorySnapshot, regime_params: RegimeParamSet, size: float, edge_bias: float, stats: dict | None=None) -> int:
        if inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return 0
        cfg = state.config
        bid = book.bids[0].price
        ask = book.asks[0].price
        spread = ask - bid
        mid = (bid + ask) / 2.0
        prices = self.skewed_quote_prices(bid, ask, prediction.score, inventory.inventory_ratio, regime_params, cfg.priceDecimals, edge_bias=edge_bias)
        if not prices:
            return 0
        bid_px, ask_px = prices
        qty = self.dynamic_order_size(size, profile, regime_params, inventory, cfg.volumeDecimals, mid=mid)
        if qty <= 0:
            return 0
        fill_est = self.estimate_fill_probability(book, mid, spread, profile.trade_rate, bid_px, ask_px, book_id=book_id)
        quote_notional = qty * mid * 2
        if not self._can_add_volume(state, quote_notional):
            return 0
        edge = ask_px - bid_px
        expected_edge = edge * (fill_est.buy + fill_est.sell) / 2.0
        if expected_edge <= 0:
            return 0
        est = self.estimate_round_trip_pnl(book_id, bid_px, ask_px, qty, is_maker=self._prefer_maker(book_id), direction='SYMMETRIC', timestamp=state.timestamp)
        adj_pnl = est.expected_realized_pnl * (fill_est.buy + fill_est.sell) / 2.0
        if not self._passes_expected_pnl_gate(est.expected_realized_pnl):
            if stats is not None:
                stats['skipped_negative_pnl'] = stats.get('skipped_negative_pnl', 0) + 1
            if self.log_predict_pnl and self.verbose_log:
                bt.logging.info(f'[PREDICT_PNL] skip book={book_id} expected_pnl={round(est.expected_realized_pnl, 6)} (min={self.min_expected_realized_pnl}) bid={bid_px} ask={ask_px}')
            return 0
        if fill_est.buy < regime_params.min_fill_prob and fill_est.sell < regime_params.min_fill_prob:
            return 0
        placed = 0
        acct = self.accounts[book_id]
        mem = self._mem(book_id)
        buy_touch_dist = max(0.0, (mid - bid_px) / spread)
        sell_touch_dist = max(0.0, (ask_px - mid) / spread)
        buy_size = qty
        sell_size = qty
        if inventory.band == 'LONG':
            buy_size = self._round_order_size(qty * 0.5, cfg.volumeDecimals)
        elif inventory.band == 'SHORT':
            sell_size = self._round_order_size(qty * 0.5, cfg.volumeDecimals)
        if fill_est.buy >= regime_params.min_fill_prob and acct.quote_balance.free >= bid_px * buy_size and (self._count_book_instructions(response, book_id) < self.max_instructions_per_book):
            self._record_fill_quote(mem, 'buy', buy_touch_dist)
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=buy_size, price=bid_px, clientOrderId=70000 + book_id * 10 + 1, stp=STP.CANCEL_BOTH, postOnly=self._prefer_maker(book_id), timeInForce=TimeInForce.GTT, expiryPeriod=self.mm_expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            placed += 1
            self._mem(book_id).quote_count += 1
        if fill_est.sell >= regime_params.min_fill_prob and acct.base_balance.free >= sell_size and (self._count_book_instructions(response, book_id) < self.max_instructions_per_book):
            self._record_fill_quote(mem, 'sell', sell_touch_dist)
            response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=sell_size, price=ask_px, clientOrderId=70000 + book_id * 10 + 2, stp=STP.CANCEL_BOTH, postOnly=self._prefer_maker(book_id), timeInForce=TimeInForce.GTT, expiryPeriod=self.mm_expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            placed += 1
            self._mem(book_id).quote_count += 1
        if self.log_predict_pnl and self.verbose_log and (placed > 0):
            bt.logging.info(f'[PREDICT_PNL] mm book={book_id} fill_b={round(fill_est.buy, 3)} fill_s={round(fill_est.sell, 3)} expected_pnl={round(est.expected_realized_pnl, 6)} adj_pnl={round(adj_pnl, 6)} exp_edge={round(expected_edge, 6)} bid={bid_px} ask={ask_px}')
        return placed

    def _bsimpl_1_Strategy1__place_directional_round_trip(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, direction: Literal['UP', 'DOWN'], size: float, client_id_base: int=50000, stats: dict | None=None) -> int:
        cfg = state.config
        book = state.books.get(book_id)
        if not cfg or not book or (not book.bids) or (not book.asks):
            return 0
        qty = self._round_order_size(size, cfg.volumeDecimals)
        if qty <= 0:
            return 0
        best_bid = round(book.bids[0].price, cfg.priceDecimals)
        best_ask = round(book.asks[0].price, cfg.priceDecimals)
        est = self.estimate_round_trip_pnl(book_id, best_bid, best_ask, qty, is_maker=self._prefer_maker(book_id), direction=direction, timestamp=state.timestamp)
        if not self._passes_expected_pnl_gate(est.expected_realized_pnl):
            if stats is not None:
                stats['skipped_negative_pnl'] = stats.get('skipped_negative_pnl', 0) + 1
            if self.log_predict_pnl and self.verbose_log:
                bt.logging.info(f'[PREDICT_PNL] skip alpha book={book_id} dir={direction} expected_pnl={round(est.expected_realized_pnl, 6)} (min={self.min_expected_realized_pnl})')
            return 0
        if self.log_predict_pnl and self.verbose_log:
            bt.logging.info(f'[PREDICT_PNL] alpha book={book_id} dir={direction} expected_pnl={round(est.expected_realized_pnl, 6)} qty={qty}')
        return self._bsimpl_0_DetailedTemplateAgent__place_directional_round_trip(response, state, book_id, direction, size, client_id_base)

    def _bsimpl_1_Strategy1_build_mm_strategy_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime, collect_archetypes: bool=True) -> dict:
        stats = {'quoted': 0, 'managed': 0, 'maintenance': 0, 'skipped_avoid': 0, 'skipped_archetype': 0, 'skipped_toxic': 0, 'skipped_alpha': 0, 'skipped_negative_pnl': 0, 'skipped_low_alpha': 0, 'skipped_small_inv': 0, 'skipped_maint_arch': 0, 'mm_candidates': 0, 'alpha_ranked': 0, 'instructions': 0}
        regime_params = self.get_regime_params(regime)
        avoid_set = set(selection.avoid_books)
        profile_by_id = {p.book_id: p for p in selection.profiles}
        maint_limit = self.max_maintenance_books_per_tick
        maintenance_set = set(self._schedule_maintenance_books(selection, state.timestamp, limit=maint_limit))
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
                archetype_rows.append({'book': book_id, 'arch': archetype, 'tier': profile.tier, 'size_mult': round(book_params.size_mult, 3), 'fill': round(mem_row.fill_rate, 3), 'win': round(mem_row.win_rate, 3), 'streak': mem_row.loss_streak})
            if book_id in avoid_set:
                stats['skipped_avoid'] += 1
                continue
            prediction = predictions.get(book_id)
            if not prediction:
                continue
            mid = (book.bids[0].price + book.asks[0].price) / 2.0
            spread = book.asks[0].price - book.bids[0].price
            inventory = self._net_inventory(book_id, mid)
            if inventory.band != 'FLAT':
                if self._inventory_needs_management(inventory):
                    urgency = self._inventory_urgency(inventory, book_params, regime, archetype)
                    manage_queue.append((urgency, book_id, book, inventory, book_params, archetype))
                    continue
                stats['skipped_small_inv'] += 1
            toxic = self.is_toxic_book(book_id, profile, archetype)
            if book_id in maintenance_set:
                if maintenance_placed_books >= maint_limit:
                    stats['skipped_low_alpha'] += 1
                    continue
                if inventory.band != 'FLAT':
                    stats['skipped_small_inv'] += 1
                    continue
                if not self._maintenance_allowed(profile, archetype):
                    stats['skipped_maint_arch'] += 1
                    continue
                if toxic and regime.scoring_overlay != 'SCORING_PRESSURE':
                    stats['skipped_toxic'] += 1
                    continue
                maint_size = self.maintenance_order_size * self.maintenance_size_mult
                n = self._place_round_trip_limits(response, state, book_id, maint_size, post_only=True, expiry_period=state.config.publish_interval, client_id_base=MAINT_CLIENT_ID_BASE)
                if n:
                    maintenance_placed_books += 1
                    mem_m = self._mem(book_id)
                    mem_m.quote_count += n
                    if spread > 0:
                        self._record_fill_quote(mem_m, 'buy', max(0.0, (mid - book.bids[0].price) / spread))
                        self._record_fill_quote(mem_m, 'sell', max(0.0, (book.asks[0].price - mid) / spread))
                    stats['maintenance'] += 1
                    stats['instructions'] += n
                continue
            if toxic:
                stats['skipped_toxic'] += 1
                continue
            if not book_params.quote_enabled:
                stats['skipped_archetype'] += 1
                continue
            if archetype in ('TOXIC_BOOK', 'STRESSED') and regime.mode in ('CHOP', 'STRESSED'):
                stats['skipped_toxic'] += 1
                continue
            if self.mm_skip_inactive_tier and profile.tier == 'INACTIVE':
                stats['skipped_low_alpha'] += 1
                continue
            fill_est = self.estimate_fill_probability(book, mid, spread, profile.trade_rate, book.bids[0].price, book.asks[0].price, book_id=book_id)
            mem = self._mem(book_id)
            expected_alpha = self.expected_alpha_score(profile, prediction, fill_est, mem, book_id, state.timestamp)
            if expected_alpha < self.min_expected_alpha:
                stats['skipped_low_alpha'] += 1
                continue
            global_rank = self._global_book_rank(expected_alpha, mem)
            mm_candidates.append((global_rank, expected_alpha, book_id, book, profile, prediction, inventory, book_params, edge_bias))
        manage_queue.sort(key=lambda x: x[0], reverse=True)
        for _urgency, book_id, book, inventory, book_params, archetype in manage_queue[:self.max_managed_books_per_tick]:
            n = self._manage_inventory(response, state, book_id, book, inventory, book_params, regime, archetype)
            if n:
                stats['managed'] += 1
                stats['instructions'] += n
        mm_candidates.sort(key=lambda x: x[0], reverse=True)
        stats['mm_candidates'] = len(mm_candidates)
        for item in mm_candidates[:self.max_mm_books_per_tick]:
            _rank, _ea, book_id, book, profile, prediction, inventory, book_params, edge_bias = item
            before_ix = len(response.instructions)
            n = self._place_skewed_quotes(response, state, book_id, book, profile, prediction, inventory, book_params, self.mm_base_size, edge_bias, stats=stats)
            if n:
                stats['quoted'] += 1
                stats['instructions'] += n
            if len(response.instructions) - before_ix > self.max_instructions_per_book:
                break
        if regime_params.alpha_enabled and self._alpha_regime_allows(regime):
            for book_id in selection.alpha_books:
                if book_id in avoid_set or book_id not in state.books:
                    continue
                pred = predictions.get(book_id)
                profile = profile_by_id.get(book_id)
                book = state.books[book_id]
                if not pred or not profile or pred.direction == 'HOLD':
                    continue
                if not book.bids or not book.asks:
                    continue
                archetype = self.classify_book_archetype(profile, regime)
                if self.is_toxic_book(book_id, profile, archetype):
                    stats['skipped_toxic'] += 1
                    continue
                mid = (book.bids[0].price + book.asks[0].price) / 2.0
                spread = book.asks[0].price - book.bids[0].price
                fill_est = self.estimate_fill_probability(book, mid, spread, profile.trade_rate, book.bids[0].price, book.asks[0].price, book_id=book_id)
                mem = self._mem(book_id)
                ea = self.expected_alpha_score(profile, pred, fill_est, mem, book_id, state.timestamp)
                if ea < self.min_expected_alpha:
                    stats['skipped_alpha'] += 1
                    continue
                alpha_candidates.append((ea, book_id, pred, profile, archetype))
            alpha_candidates.sort(key=lambda x: x[0], reverse=True)
            stats['alpha_ranked'] = len(alpha_candidates)
            for ea, book_id, pred, profile, archetype in alpha_candidates[:self.max_alpha_books_per_tick]:
                merged = self.merge_regime_and_archetype_params(regime_params, archetype)
                alpha_size = self.dynamic_order_size(self.alpha_order_size, profile, merged, InventorySnapshot(0, 0, 'FLAT', None, None, 0), state.config.volumeDecimals, mid=profile.mid)
                n = self._place_directional_round_trip(response, state, book_id, 'UP' if pred.direction == 'UP' else 'DOWN', alpha_size, client_id_base=80000, stats=stats)
                if n:
                    self._mem(book_id).quote_count += n
                    stats['instructions'] += n
        stats['archetypes'] = archetype_rows[:12]
        self._last_mm_stats = stats
        return stats

    def _bsimpl_1_Strategy1__log_mm_strategy(self, stats: dict, regime: MarketRegime) -> None:
        bt.logging.info(f"[MM_STRATEGY] regime={regime.mode} overlay={regime.scoring_overlay} stats={json.dumps({k: v for k, v in stats.items() if k != 'archetypes'})}")
        if stats.get('archetypes'):
            bt.logging.info(f"[MM_STRATEGY] archetypes={json.dumps(stats['archetypes'])}")

    def _bsimpl_1_Strategy1_respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        response = FinanceAgentResponse(agent_id=self.uid)
        self._tick += 1
        log_tick = self._tick == 1 or self._tick % self.log_every_n == 0
        need_summary = log_tick and (self.verbose_log or self.log_momentum_pnl)
        summary = self.parse_state(state) if need_summary else None
        predictions = self._predict_all_books(state)
        selection = self.select_books_for_trading(state, predictions)
        regime = self.classify_market_regime_from_profiles(selection.profiles, predictions, selection)
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
        grace_period_ns = summary.grace_period_ns if summary else state.config.grace_period if state.config else 0
        in_grace = state.timestamp < grace_period_ns
        collect_archetypes = self.log_mm_strategy and log_tick
        if state.books and (not in_grace):
            if self.enable_mm_strategy:
                mm_stats = self.build_mm_strategy_instructions(response, state, selection, predictions, regime, collect_archetypes=collect_archetypes)
                self._accumulate_tuning_window(mm_stats)
                if self.log_mm_strategy and log_tick:
                    self._log_mm_strategy(mm_stats, regime)
            elif self.enable_kappa_strategy:
                strategy_stats = self.build_kappa_strategy_instructions(response, state, selection, predictions, regime)
                if self.log_kappa_strategy and (self._tick == 1 or self._tick % self.log_every_n == 0):
                    self._log_kappa_strategy_calibration(state, selection, regime, strategy_stats)
            elif self.enable_trading:
                self.build_demo_instructions(response, state, book_id=0)
        elif state.books and in_grace and (self.enable_mm_strategy or self.enable_kappa_strategy or self.enable_trading):
            bt.logging.info(f'Grace period active (T={state.timestamp} < {summary.grace_period_ns}); no orders placed.')
        if self.verbose_log and response.instructions and log_tick:
            self._log_output(self.parse_response(response))
        self._maybe_run_tuning_scheduler(state)
        if self.monitor_top_miners:
            try:
                from top_miner_monitor import write_tick_tap
                write_tick_tap(state, self._tick, self.output_dir, self.uid, self.monitor_top_n)
            except Exception as exc:
                bt.logging.warning(f'monitor tap failed: {exc}')
        return response

    def _bsimpl_2_Strategy1_Debug_initialize(self) -> None:
        self._bsimpl_1_Strategy1_initialize()
        cfg = self.config
        self.debug_enabled = self._env_bool('STRATEGY1_DEBUG', self._as_bool(getattr(cfg, 'debug_enabled', True)))
        self.debug_every_n = max(1, self._env_int('STRATEGY1_DEBUG_EVERY_N', int(getattr(cfg, 'debug_every_n', 1))))
        self.debug_summary_every_n = max(1, self._env_int('STRATEGY1_DEBUG_SUMMARY_N', int(getattr(cfg, 'debug_summary_every_n', 100))))
        self.debug_book_id = self._env_int('STRATEGY1_DEBUG_BOOK', int(getattr(cfg, 'debug_book_id', -1)))
        self.debug_jsonl = self._env_bool('STRATEGY1_DEBUG_JSONL', self._as_bool(getattr(cfg, 'debug_jsonl', True)))
        configured_dir = str(getattr(cfg, 'debug_output_dir', '') or '')
        env_dir = os.getenv('STRATEGY1_DEBUG_DIR', '').strip()
        self.debug_output_dir = env_dir or configured_dir or os.path.join(self.output_dir, 'strategy1_debug')
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
                path = os.path.join(self.debug_output_dir, f'strategy1_debug_agent_{self.uid}.jsonl')
                self._debug_file = open(path, 'a', encoding='utf-8', buffering=1)
                atexit.register(self._close_debug_file)
            except OSError as exc:
                self._debug_file = None
                bt.logging.warning(f'[S1DBG] cannot open JSONL output: {exc}')
        self._emit('DEBUG_CONFIG', force=True, enabled=self.debug_enabled, every_n=self.debug_every_n, summary_every_n=self.debug_summary_every_n, book_filter=self.debug_book_id, jsonl=self.debug_jsonl, output_dir=self.debug_output_dir)

    def _bsimpl_2_Strategy1_Debug_handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1_handle(state)
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
        self._record_latency('update_ms', update_ms)
        self._record_latency('respond_ms', respond_ms)
        self._record_latency('report_ms', report_ms)
        self._record_latency('total_ms', total_ms)
        self._debug_response_count += 1
        if self._should_emit_tick(self._tick):
            self._emit('TIMING', tick=self._tick, timestamp=getattr(state, 'timestamp', None), update_ms=round(update_ms, 4), respond_ms=round(respond_ms, 4), report_ms=round(report_ms, 4), total_ms=round(total_ms, 4), internal_ms={key: round(value, 4) for key, value in sorted(self._debug_stage_ms.items())}, notices=len((getattr(state, 'notices', None) or {}).get(self.uid, [])), instructions=len(getattr(response, 'instructions', []) or []))
        if self._tick == 1 or self._tick % self.debug_summary_every_n == 0:
            self._emit_run_summary(state)
        return response

    def _bsimpl_2_Strategy1_Debug_respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1_respond(state)
        self._debug_stage_ms = {}
        self._debug_book_records = {}
        self._debug_current_state = state
        self._debug_current_regime = None
        started = time.perf_counter()
        try:
            response = self._bsimpl_1_Strategy1_respond(state)
        except Exception as exc:
            self._emit('ERROR', force=True, tick=self._tick, stage='respond', error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            self._debug_stage_ms['respond_parent_ms'] = self._elapsed_ms(started)
        self._log_submitted_instructions(response, state)
        self._debug_current_state = None
        self._debug_current_regime = None
        return response

    def _bsimpl_2_Strategy1_Debug__predict_all_books(self, state: MarketSimulationStateUpdate):
        return self._timed('predict_all_books_ms', self._bsimpl_0_DetailedTemplateAgent__predict_all_books, state)

    def _bsimpl_2_Strategy1_Debug_select_books_for_trading(self, state, predictions):
        return self._timed('select_books_ms', self._bsimpl_0_DetailedTemplateAgent_select_books_for_trading, state, predictions)

    def _bsimpl_2_Strategy1_Debug_classify_market_regime_from_profiles(self, profiles, predictions, selection):
        regime = self._timed('classify_regime_ms', self._bsimpl_0_DetailedTemplateAgent_classify_market_regime_from_profiles, profiles, predictions, selection)
        if self.debug_enabled:
            self._debug_current_regime = regime
        return regime

    def _bsimpl_2_Strategy1_Debug_build_mm_strategy_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime, collect_archetypes: bool=True) -> dict:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1_build_mm_strategy_instructions(response, state, selection, predictions, regime, collect_archetypes=collect_archetypes)
        self._debug_current_regime = regime
        started = time.perf_counter()
        stats = self._bsimpl_1_Strategy1_build_mm_strategy_instructions(response, state, selection, predictions, regime, collect_archetypes=collect_archetypes)
        self._debug_stage_ms['build_mm_ms'] = self._elapsed_ms(started)
        self._finalize_book_decisions(response=response, state=state, selection=selection, predictions=predictions, regime=regime)
        return stats

    def _bsimpl_2_Strategy1_Debug__net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        inventory = self._bsimpl_1_Strategy1__net_inventory(book_id, mid)
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['inventory'] = {'net_base': inventory.net_base, 'ratio': inventory.inventory_ratio, 'band': inventory.band, 'vwap_entry': inventory.vwap_entry, 'unrealized_bps': inventory.unrealized_bps, 'position_ticks': inventory.position_ticks, 'reason': inventory.reason}
        return inventory

    def _bsimpl_2_Strategy1_Debug_classify_book_archetype(self, profile: BookProfile, regime: MarketRegime) -> BookArchetype:
        archetype = self._bsimpl_1_Strategy1_classify_book_archetype(profile, regime)
        if self.debug_enabled:
            self._book_record(profile.book_id)['archetype'] = archetype
        return archetype

    def _bsimpl_2_Strategy1_Debug_is_toxic_book(self, book_id: int, profile: BookProfile, archetype: BookArchetype) -> bool:
        toxic = self._bsimpl_1_Strategy1_is_toxic_book(book_id, profile, archetype)
        if self.debug_enabled:
            self._book_record(book_id)['toxic'] = toxic
        return toxic

    def _bsimpl_2_Strategy1_Debug_expected_alpha_score(self, profile: BookProfile, prediction: DirectionForecast, fill_est, mem, book_id: int, now: int) -> float:
        score = self._bsimpl_1_Strategy1_expected_alpha_score(profile, prediction, fill_est, mem, book_id, now)
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['expected_alpha'] = score
            record['fill_buy'] = getattr(fill_est, 'buy', None)
            record['fill_sell'] = getattr(fill_est, 'sell', None)
        return score

    def _bsimpl_2_Strategy1_Debug__manage_inventory(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> int:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1__manage_inventory(response, state, book_id, book, inventory, regime_params, regime, archetype)
        before = len(getattr(response, 'instructions', []) or [])
        started = time.perf_counter()
        placed = self._bsimpl_1_Strategy1__manage_inventory(response, state, book_id, book, inventory, regime_params, regime, archetype)
        elapsed = self._elapsed_ms(started)
        record = self._book_record(book_id)
        record['manage_ms'] = elapsed
        record['action'] = 'MANAGE' if placed else 'SKIP'
        record['reason'] = DebugReason.MANAGED_INVENTORY if placed else DebugReason.MANAGE_ORDER_GATE
        record['instructions_added'] = len(getattr(response, 'instructions', []) or []) - before
        return placed

    def _bsimpl_2_Strategy1_Debug__place_skewed_quotes(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book, profile: BookProfile, prediction: DirectionForecast, inventory: InventorySnapshot, regime_params: RegimeParamSet, size: float, edge_bias: float, stats: dict | None=None) -> int:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1__place_skewed_quotes(response, state, book_id, book, profile, prediction, inventory, regime_params, size, edge_bias, stats=stats)
        diagnosis = self._diagnose_quote_setup(response=response, state=state, book_id=book_id, book=book, profile=profile, prediction=prediction, inventory=inventory, regime_params=regime_params, size=size, edge_bias=edge_bias)
        before = len(getattr(response, 'instructions', []) or [])
        started = time.perf_counter()
        placed = self._bsimpl_1_Strategy1__place_skewed_quotes(response, state, book_id, book, profile, prediction, inventory, regime_params, size, edge_bias, stats=stats)
        elapsed = self._elapsed_ms(started)
        record = self._book_record(book_id)
        record.update(diagnosis)
        record['quote_ms'] = elapsed
        record['instructions_added'] = len(getattr(response, 'instructions', []) or []) - before
        if placed:
            record['action'] = 'QUOTE'
            record['reason'] = DebugReason.QUOTED
        else:
            record['action'] = 'SKIP'
            record['reason'] = diagnosis.get('gate_reason', DebugReason.QUOTE_ORDER_GATE)
        return placed

    def _bsimpl_2_Strategy1_Debug__place_directional_round_trip(self, *args, **kwargs) -> int:
        placed = self._bsimpl_1_Strategy1__place_directional_round_trip(*args, **kwargs)
        if self.debug_enabled:
            book_id = kwargs.get('book_id')
            if book_id is None and len(args) >= 3:
                book_id = args[2]
            if isinstance(book_id, int):
                record = self._book_record(book_id)
                if placed:
                    record['action'] = 'ALPHA'
                    record['reason'] = DebugReason.ALPHA_ORDER
        return placed

    def _bsimpl_2_Strategy1_Debug_onTrade(self, event, validator: str | None=None) -> None:
        book_id = self._get(event, 'bookId', 'book_id')
        net_before = None
        if isinstance(book_id, int):
            net_before = self._position_tracker_snapshot(book_id).net_qty
        self._bsimpl_1_Strategy1_onTrade(event, validator)
        if not self.debug_enabled or not self._book_matches(book_id):
            return
        net_after = None
        if isinstance(book_id, int):
            net_after = self._position_tracker_snapshot(book_id).net_qty
        self._debug_event_counts['TRADE_FILL'] += 1
        self._emit('ORDER_LIFECYCLE', tick=self._tick, phase='TRADE_FILL', book_id=book_id, event=self._event_payload(event), net_before=net_before, net_after=net_after)

    def _bsimpl_2_Strategy1_Debug__finalize_book_decisions(self, *, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime) -> None:
        if not self._should_emit_tick(self._tick):
            return
        profile_by_id = {profile.book_id: profile for profile in selection.profiles}
        avoid_set = set(selection.avoid_books)
        maintenance_set = set(self._schedule_maintenance_books(selection, state.timestamp, limit=self.max_maintenance_books_per_tick))
        instruction_counts = self._instruction_counts_by_book(response)
        regime_params = self.get_regime_params(regime)
        for book_id, book in state.books.items():
            if not self._book_matches(book_id):
                continue
            record = self._book_record(book_id)
            profile = profile_by_id.get(book_id)
            prediction = predictions.get(book_id)
            record['instructions'] = instruction_counts.get(book_id, 0)
            if not getattr(book, 'bids', None) or not getattr(book, 'asks', None):
                record.setdefault('action', 'SKIP')
                record.setdefault('reason', DebugReason.NO_BOOK_SIDES)
            elif profile is None:
                record.setdefault('action', 'SKIP')
                record.setdefault('reason', DebugReason.NO_PROFILE)
            elif book_id in avoid_set:
                record.setdefault('action', 'SKIP')
                record.setdefault('reason', DebugReason.AVOID_LIST)
            elif prediction is None:
                record.setdefault('action', 'SKIP')
                record.setdefault('reason', DebugReason.NO_PREDICTION)
            else:
                self._complete_trading_reason(record=record, book_id=book_id, profile=profile, prediction=prediction, maintenance_set=maintenance_set, regime=regime, regime_params=regime_params, instruction_count=instruction_counts.get(book_id, 0))
            self._emit_book_decision(state, regime, book_id, book, profile, prediction, record)

    def _bsimpl_2_Strategy1_Debug__complete_trading_reason(self, *, record: dict[str, Any], book_id: int, profile: BookProfile, prediction: DirectionForecast, maintenance_set: set[int], regime: MarketRegime, regime_params: RegimeParamSet, instruction_count: int) -> None:
        if record.get('reason'):
            return
        inventory = record.get('inventory', {})
        band = inventory.get('band', 'FLAT')
        archetype = record.get('archetype')
        toxic = bool(record.get('toxic', False))
        if band != 'FLAT' and self._inventory_record_needs_management(inventory):
            record['action'] = 'SKIP'
            record['reason'] = DebugReason.MANAGEMENT_LIMIT
            return
        if book_id in maintenance_set:
            if band != 'FLAT':
                reason = DebugReason.MAINT_INVENTORY_NONFLAT
            elif archetype is not None and (not self._maintenance_allowed(profile, archetype)):
                reason = DebugReason.MAINT_ARCHETYPE_BLOCK
            elif toxic and regime.scoring_overlay != 'SCORING_PRESSURE':
                reason = DebugReason.TOXIC_BOOK
            elif instruction_count:
                record['action'] = 'MAINTENANCE'
                record['reason'] = DebugReason.MAINTENANCE_ORDER
                return
            else:
                reason = DebugReason.MAINT_ORDER_GATE
            record['action'] = 'SKIP'
            record['reason'] = reason
            return
        if toxic:
            reason = DebugReason.TOXIC_BOOK
        elif archetype is not None:
            merged = self.merge_regime_and_archetype_params(regime_params, archetype)
            if not merged.quote_enabled:
                reason = DebugReason.QUOTE_DISABLED
            elif archetype in ('TOXIC_BOOK', 'STRESSED') and regime.mode in ('CHOP', 'STRESSED'):
                reason = DebugReason.TOXIC_REGIME
            elif self.mm_skip_inactive_tier and profile.tier == 'INACTIVE':
                reason = DebugReason.INACTIVE_TIER
            elif record.get('expected_alpha', float('-inf')) < self.min_expected_alpha:
                reason = DebugReason.LOW_EXPECTED_ALPHA
            elif instruction_count:
                record['action'] = 'ORDER'
                record['reason'] = DebugReason.ALPHA_ORDER
                return
            else:
                reason = DebugReason.MM_CANDIDATE_LIMIT
        else:
            reason = DebugReason.NO_ACTION
        record['action'] = 'SKIP'
        record['reason'] = reason

    def _bsimpl_2_Strategy1_Debug__diagnose_quote_setup(self, *, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book, profile: BookProfile, prediction: DirectionForecast, inventory: InventorySnapshot, regime_params: RegimeParamSet, size: float, edge_bias: float) -> dict[str, Any]:
        diag: dict[str, Any] = {}
        if inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            diag['gate_reason'] = DebugReason.MAX_INVENTORY
            return diag
        cfg = state.config
        bid = book.bids[0].price
        ask = book.asks[0].price
        spread = ask - bid
        mid = (bid + ask) / 2.0
        diag.update(mid=mid, spread=spread)
        prices = self.skewed_quote_prices(bid, ask, prediction.score, inventory.inventory_ratio, regime_params, cfg.priceDecimals, edge_bias=edge_bias)
        if not prices:
            diag['gate_reason'] = DebugReason.INVALID_QUOTE_PRICES
            return diag
        bid_px, ask_px = prices
        diag.update(bid_px=bid_px, ask_px=ask_px)
        qty = self.dynamic_order_size(size, profile, regime_params, inventory, cfg.volumeDecimals, mid=mid)
        diag['quantity'] = qty
        if qty <= 0:
            diag['gate_reason'] = DebugReason.ZERO_ORDER_SIZE
            return diag
        fill_est = self.estimate_fill_probability(book, mid, spread, profile.trade_rate, bid_px, ask_px, book_id=book_id)
        diag['fill_buy'] = fill_est.buy
        diag['fill_sell'] = fill_est.sell
        quote_notional = qty * mid * 2
        diag['quote_notional'] = quote_notional
        if not self._can_add_volume(state, quote_notional):
            diag['gate_reason'] = DebugReason.VOLUME_CAP
            return diag
        edge = ask_px - bid_px
        expected_edge = edge * (fill_est.buy + fill_est.sell) / 2.0
        diag['expected_edge'] = expected_edge
        if expected_edge <= 0:
            diag['gate_reason'] = DebugReason.NON_POSITIVE_EDGE
            return diag
        estimate = self.estimate_round_trip_pnl(book_id, bid_px, ask_px, qty, is_maker=self._prefer_maker(book_id), direction='SYMMETRIC', timestamp=state.timestamp)
        diag['expected_realized_pnl'] = estimate.expected_realized_pnl
        if not self._passes_expected_pnl_gate(estimate.expected_realized_pnl):
            diag['gate_reason'] = DebugReason.NEGATIVE_EXPECTED_PNL
            return diag
        if fill_est.buy < regime_params.min_fill_prob and fill_est.sell < regime_params.min_fill_prob:
            diag['gate_reason'] = DebugReason.LOW_FILL_PROBABILITY
            return diag
        before_count = self._count_book_instructions(response, book_id)
        if before_count >= self.max_instructions_per_book:
            diag['gate_reason'] = DebugReason.INSTRUCTION_LIMIT
            return diag
        account = self.accounts.get(book_id)
        if account is None:
            diag['gate_reason'] = DebugReason.INSUFFICIENT_BALANCE
            return diag
        buy_size = qty * (0.5 if inventory.band == 'LONG' else 1.0)
        sell_size = qty * (0.5 if inventory.band == 'SHORT' else 1.0)
        can_buy = fill_est.buy >= regime_params.min_fill_prob and account.quote_balance.free >= bid_px * buy_size
        can_sell = fill_est.sell >= regime_params.min_fill_prob and account.base_balance.free >= sell_size
        diag['can_buy'] = can_buy
        diag['can_sell'] = can_sell
        if not can_buy and (not can_sell):
            diag['gate_reason'] = DebugReason.INSUFFICIENT_BALANCE
        else:
            diag['gate_reason'] = DebugReason.QUOTE_ORDER_GATE
        return diag

    def _bsimpl_2_Strategy1_Debug__emit_book_decision(self, state, regime, book_id: int, book, profile, prediction, record: dict[str, Any]) -> None:
        bid = book.bids[0].price if getattr(book, 'bids', None) else None
        ask = book.asks[0].price if getattr(book, 'asks', None) else None
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        spread_bps = None
        if mid and bid is not None and (ask is not None):
            spread_bps = (ask - bid) / mid * 10000.0
        reason = str(record.get('reason', DebugReason.NO_ACTION))
        self._debug_reason_counts[reason] += 1
        self._emit('DECISION', tick=self._tick, timestamp=getattr(state, 'timestamp', None), book_id=book_id, action=record.get('action', 'SKIP'), reason=reason, regime=getattr(regime, 'mode', None), overlay=getattr(regime, 'scoring_overlay', None), archetype=record.get('archetype'), tier=getattr(profile, 'tier', None) if profile is not None else None, mid=mid, spread_bps=spread_bps, direction=getattr(prediction, 'direction', None) if prediction else None, signal=getattr(prediction, 'score', None) if prediction else None, expected_alpha=record.get('expected_alpha'), min_expected_alpha=self.min_expected_alpha, fill_buy=record.get('fill_buy'), fill_sell=record.get('fill_sell'), bid_px=record.get('bid_px'), ask_px=record.get('ask_px'), quantity=record.get('quantity'), expected_realized_pnl=record.get('expected_realized_pnl'), inventory=record.get('inventory'), instructions=record.get('instructions', 0), decision_ms=record.get('quote_ms', record.get('manage_ms')))

    def _bsimpl_2_Strategy1_Debug__log_submitted_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate) -> None:
        for index, instruction in enumerate(getattr(response, 'instructions', []) or []):
            book_id = self._get(instruction, 'bookId', 'book_id')
            if not self._book_matches(book_id):
                continue
            self._debug_event_counts['SUBMITTED'] += 1
            self._emit('ORDER_LIFECYCLE', tick=self._tick, timestamp=getattr(state, 'timestamp', None), phase='SUBMITTED', instruction_index=index, book_id=book_id, instruction=self._instruction_payload(instruction))

    def _bsimpl_2_Strategy1_Debug__log_notices(self, state: MarketSimulationStateUpdate, tick: int) -> None:
        notices = (getattr(state, 'notices', None) or {}).get(self.uid, []) or []
        for notice in notices:
            book_id = self._get(notice, 'bookId', 'book_id')
            if not self._book_matches(book_id):
                continue
            phase = type(notice).__name__.upper()
            self._debug_event_counts[phase] += 1
            self._emit('ORDER_LIFECYCLE', tick=tick, timestamp=getattr(state, 'timestamp', None), phase=phase, book_id=book_id, event=self._event_payload(notice))

    def _bsimpl_2_Strategy1_Debug__emit_run_summary(self, state: MarketSimulationStateUpdate) -> None:
        count = max(self._debug_response_count, 1)
        avg_latency = {name: round(total / count, 4) for name, total in sorted(self._debug_latency_sum_ms.items())}
        max_latency = {name: round(value, 4) for name, value in sorted(self._debug_latency_max_ms.items())}
        self._emit('RUN_SUMMARY', force=True, tick=self._tick, timestamp=getattr(state, 'timestamp', None), responses=self._debug_response_count, reason_counts=dict(self._debug_reason_counts), event_counts=dict(self._debug_event_counts), average_latency_ms=avg_latency, max_latency_ms=max_latency)

    def _bsimpl_2_Strategy1_Debug__emit(self, event_type: str, force: bool=False, **payload: Any) -> None:
        if not self.debug_enabled and (not force):
            return
        record = {'type': event_type, 'agent_id': getattr(self, 'uid', None), 'wall_time_ns': time.time_ns(), **self._json_safe(payload)}
        try:
            line = json.dumps(record, separators=(',', ':'), sort_keys=True)
            bt.logging.info(f'[S1DBG] {line}')
            if self._debug_file is not None:
                self._debug_file.write(line + '\n')
        except Exception as exc:
            bt.logging.warning(f'[S1DBG] emit failed: {exc}')

    def _bsimpl_2_Strategy1_Debug__timed(self, name: str, fn: Callable[..., T], *args, **kwargs) -> T:
        if not self.debug_enabled:
            return fn(*args, **kwargs)
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self._debug_stage_ms[name] = self._elapsed_ms(started)

    def _bsimpl_2_Strategy1_Debug__record_latency(self, name: str, value: float) -> None:
        self._debug_latency_sum_ms[name] += value
        self._debug_latency_max_ms[name] = max(self._debug_latency_max_ms[name], value)

    def _bsimpl_2_Strategy1_Debug__book_record(self, book_id: int) -> dict[str, Any]:
        return self._debug_book_records.setdefault(book_id, {})

    def _bsimpl_2_Strategy1_Debug__instruction_counts_by_book(self, response: FinanceAgentResponse) -> Counter[int]:
        counts: Counter[int] = Counter()
        for instruction in getattr(response, 'instructions', []) or []:
            book_id = self._get(instruction, 'bookId', 'book_id')
            if isinstance(book_id, int):
                counts[book_id] += 1
        return counts

    def _bsimpl_2_Strategy1_Debug__inventory_record_needs_management(self, inventory: dict[str, Any]) -> bool:
        band = inventory.get('band')
        if band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        ratio = abs(float(inventory.get('ratio', 0.0) or 0.0))
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        utilization = ratio / max(max_ratio, 1e-09)
        return utilization >= self.inventory_close_threshold

    def _bsimpl_2_Strategy1_Debug__instruction_payload(self, instruction: Any) -> dict[str, Any]:
        payload = self._object_payload(instruction)
        payload.setdefault('instruction_type', type(instruction).__name__)
        return payload

    def _bsimpl_2_Strategy1_Debug__event_payload(self, event: Any) -> dict[str, Any]:
        payload = self._object_payload(event)
        payload.setdefault('event_type', type(event).__name__)
        return payload

    def _bsimpl_2_Strategy1_Debug__object_payload(self, obj: Any) -> dict[str, Any]:
        if obj is None:
            return {}
        if hasattr(obj, 'model_dump'):
            try:
                value = obj.model_dump(mode='json')
                if isinstance(value, dict):
                    return self._json_safe(value)
            except Exception:
                pass
        if hasattr(obj, 'dict'):
            try:
                value = obj.dict()
                if isinstance(value, dict):
                    return self._json_safe(value)
            except Exception:
                pass
        names = ('bookId', 'book_id', 'orderId', 'order_id', 'clientOrderId', 'client_order_id', 'direction', 'side', 'price', 'quantity', 'remainingQuantity', 'filledQuantity', 'timestamp', 'delay', 'reason', 'status', 'takerAgentId', 'makerAgentId')
        result: dict[str, Any] = {}
        for name in names:
            if hasattr(obj, name):
                result[name] = self._json_safe(getattr(obj, name))
        return result

    @classmethod
    def _bsimpl_2_Strategy1_Debug__json_safe(cls, value: Any) -> Any:
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
        if hasattr(value, 'model_dump'):
            try:
                return cls._json_safe(value.model_dump(mode='json'))
            except Exception:
                pass
        return str(value)

    @staticmethod
    def _bsimpl_2_Strategy1_Debug__get(obj: Any, *names: str) -> Any:
        if obj is None:
            return None
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    def _bsimpl_2_Strategy1_Debug__book_matches(self, book_id: Any) -> bool:
        return self.debug_book_id < 0 or book_id == self.debug_book_id

    def _bsimpl_2_Strategy1_Debug__should_emit_tick(self, tick: int) -> bool:
        return tick == 1 or tick % self.debug_every_n == 0

    def _bsimpl_2_Strategy1_Debug__close_debug_file(self) -> None:
        handle = getattr(self, '_debug_file', None)
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except OSError:
                pass
            self._debug_file = None

    @staticmethod
    def _bsimpl_2_Strategy1_Debug__elapsed_ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000.0

    @staticmethod
    def _bsimpl_2_Strategy1_Debug__as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

    @classmethod
    def _bsimpl_2_Strategy1_Debug__env_bool(cls, name: str, default: bool) -> bool:
        value = os.getenv(name)
        return default if value is None else cls._as_bool(value)

    @staticmethod
    def _bsimpl_2_Strategy1_Debug__env_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def _bsimpl_3_Strategy1_Research_initialize(self) -> None:
        self._research_ready = False
        self._research_early: list[dict[str, Any]] = []
        self._rq = None
        self._rstop = None
        self._rworker = None
        self._rfile = None
        self._rdropped = 0
        self._bsimpl_2_Strategy1_Debug_initialize()
        cfg = self.config
        self.research_fix_global_stress = self._as_bool(getattr(cfg, 'research_fix_global_stress', True))
        self.research_neutral_fallback = self._as_bool(getattr(cfg, 'research_neutral_fallback', True))
        self.research_adaptive_spread_thresholds = self._as_bool(getattr(cfg, 'research_adaptive_spread_thresholds', True))
        self.research_stress_percentile = min(0.999, max(0.5, float(getattr(cfg, 'research_stress_percentile', 0.95))))
        self.research_toxic_percentile = min(0.9999, max(self.research_stress_percentile, float(getattr(cfg, 'research_toxic_percentile', 0.99))))
        self.research_stress_floor_bps = max(0.0, float(getattr(cfg, 'research_stress_floor_bps', 8.0)))
        self.research_toxic_floor_bps = max(0.0, float(getattr(cfg, 'research_toxic_floor_bps', 10.0)))
        self.research_stress_fallback_bps = max(self.research_stress_floor_bps, float(getattr(cfg, 'research_stress_fallback_bps', 35.0)))
        self.research_toxic_fallback_bps = max(self.research_toxic_floor_bps, float(getattr(cfg, 'research_toxic_fallback_bps', 40.0)))
        self.research_toxic_gap_bps = max(0.0, float(getattr(cfg, 'research_toxic_gap_bps', 2.0)))
        self.research_min_profiles_for_adaptive = max(4, int(getattr(cfg, 'research_min_profiles_for_adaptive', 16)))
        self.research_inactive_bootstrap = self._as_bool(getattr(cfg, 'research_inactive_bootstrap', True))
        self.research_trade_global_stress = self._as_bool(getattr(cfg, 'research_trade_global_stress', True))
        self.research_global_stress_size_mult = min(1.0, max(0.05, float(getattr(cfg, 'research_global_stress_size_mult', 0.35))))
        self.research_sync_min_order = self._as_bool(getattr(cfg, 'research_sync_min_order', True))
        self.research_promote_min_order = self._as_bool(getattr(cfg, 'research_promote_min_order', True))
        self.research_bootstrap_maintenance_min_order = self._as_bool(getattr(cfg, 'research_bootstrap_maintenance_min_order', True))
        self.research_bootstrap_dead_as_mm = self._as_bool(getattr(cfg, 'research_bootstrap_dead_as_mm', True))
        self.research_bootstrap_extreme_vol_mult = max(1.0, float(getattr(cfg, 'research_bootstrap_extreme_vol_mult', 1.75)))
        self.research_fix_inventory_util = self._as_bool(getattr(cfg, 'research_fix_inventory_util', True))
        self.research_fix_quote_reservation = self._as_bool(getattr(cfg, 'research_fix_quote_reservation', True))
        self.research_bootstrap_manage_min_clip = self._as_bool(getattr(cfg, 'research_bootstrap_manage_min_clip', True))
        self.research_bootstrap_allow_aggressive_close = self._as_bool(getattr(cfg, 'research_bootstrap_allow_aggressive_close', True))
        self.research_bootstrap_force_close_ticks = max(1, int(getattr(cfg, 'research_bootstrap_force_close_ticks', 60)))
        self.research_bootstrap_force_close_min_bps = float(getattr(cfg, 'research_bootstrap_force_close_min_bps', -5.0))
        self.research_bootstrap_hard_close_ticks = max(self.research_bootstrap_force_close_ticks, int(getattr(cfg, 'research_bootstrap_hard_close_ticks', 180)))
        self.research_dust_safe_close = self._as_bool(getattr(cfg, 'research_dust_safe_close', True))
        self.research_rotate_jsonl = self._as_bool(getattr(cfg, 'research_rotate_jsonl', True))
        self.research_candidate_backfill = self._as_bool(getattr(cfg, 'research_candidate_backfill', True))
        self.research_candidate_attempt_cap = max(1, int(getattr(cfg, 'research_candidate_attempt_cap', 12)))
        self.research_aggressive_close_touch_gate = self._as_bool(getattr(cfg, 'research_aggressive_close_touch_gate', True))
        self.research_aggressive_close_fee_buffer_bps = max(0.0, float(getattr(cfg, 'research_aggressive_close_fee_buffer_bps', 3.0)))
        self.research_aggressive_close_min_net_bps = float(getattr(cfg, 'research_aggressive_close_min_net_bps', 0.0))
        self.research_toxic_pnl_min_samples = max(0, int(getattr(cfg, 'research_toxic_pnl_min_samples', 3)))
        self.research_toxic_pnl_hard_floor = float(getattr(cfg, 'research_toxic_pnl_hard_floor', -0.05))
        self.research_yellow_sparse_active = self._as_bool(getattr(cfg, 'research_yellow_sparse_active', True))
        self.research_green_sparse_active = self._as_bool(getattr(cfg, 'research_green_sparse_active', True))
        self.research_dust_park_enabled = self._as_bool(getattr(cfg, 'research_dust_park_enabled', True))
        self.research_dust_heartbeat_ticks = max(1, int(getattr(cfg, 'research_dust_heartbeat_ticks', 250)))
        self.research_dust_warn_ticks = max(self.research_dust_heartbeat_ticks, int(getattr(cfg, 'research_dust_warn_ticks', 1000)))
        self.research_dust_compact_enabled = self._as_bool(getattr(cfg, 'research_dust_compact_enabled', True))
        self.research_dust_compact_min_fraction = max(0.500001, min(0.95, float(getattr(cfg, 'research_dust_compact_min_fraction', 0.5))))
        self.research_dust_compact_books_per_tick = max(1, int(getattr(cfg, 'research_dust_compact_books_per_tick', 2)))
        self.research_kappa_completion_enabled = self._as_bool(getattr(cfg, 'research_kappa_completion_enabled', True))
        self.research_kappa_completion_target = max(2, int(getattr(cfg, 'research_kappa_completion_target', 3)))
        self.research_kappa_completion_rank_bonus = max(0.0, float(getattr(cfg, 'research_kappa_completion_rank_bonus', 0.3)))
        self.research_kappa_completion_fill_mult = max(0.5, min(1.0, float(getattr(cfg, 'research_kappa_completion_fill_mult', 0.7))))
        self.research_kappa_completion_fill_floor = max(0.0, float(getattr(cfg, 'research_kappa_completion_fill_floor', 0.1)))
        self.research_kappa_completion_relaxed_success_cap = max(0, int(getattr(cfg, 'research_kappa_completion_relaxed_success_cap', 2)))
        requested_completion_attempt_cap = max(0, int(getattr(cfg, 'research_kappa_completion_attempt_cap', 4)))
        self.research_kappa_completion_attempt_cap = min(self.research_candidate_attempt_cap, requested_completion_attempt_cap)
        self.research_normal_attempt_cap = max(0, self.research_candidate_attempt_cap - self.research_kappa_completion_attempt_cap)
        requested_completion_success_cap = max(0, int(getattr(cfg, 'research_kappa_completion_success_cap', 2)))
        self.research_kappa_completion_success_cap = min(int(self.max_mm_books_per_tick), requested_completion_success_cap)
        self.research_kappa_completion_relaxed_success_cap = min(self.research_kappa_completion_relaxed_success_cap, self.research_kappa_completion_success_cap)
        self.research_kappa_completion_recent_pnl_floor = float(getattr(cfg, 'research_kappa_completion_recent_pnl_floor', -0.01))
        self.research_run_id = time.strftime('%Y%m%d_%H%M%S', time.localtime())
        self._research_stress_spread_bps = self.research_stress_fallback_bps
        self._research_toxic_spread_bps = max(self.research_toxic_fallback_bps, self._research_stress_spread_bps + self.research_toxic_gap_bps)
        self._research_exchange_min_order_size = max(0.0, float(getattr(self, 'min_order_size', 0.0) or 0.0))
        self._research_bootstrap_active = False
        self._research_position_tick_seen: dict[int, int] = {}
        self._research_round_trip_closes = 0
        self._research_position_opens = 0
        self._research_position_reductions = 0
        self._research_dust_blocks = 0
        self._research_round_trip_samples_by_book: dict[int, int] = {}
        self._research_realized_observations_by_book: dict[int, int] = {}
        self._research_parked_dust: dict[int, dict[str, Any]] = {}
        self._research_dust_entries = 0
        self._research_dust_releases = 0
        self._research_dust_heartbeats = 0
        self._research_dust_compact_ids_this_tick: set[int] = set()
        self._research_dust_compact_attempts = 0
        self._research_dust_compact_orders = 0
        self._research_dust_compact_fills = 0
        self._research_dust_compact_active: dict[int, int] = {}
        self._research_volume_decimals = 8
        self._research_backfill_active = False
        self._research_quote_success_cap = 0
        self._research_quote_successes = 0
        self._research_quote_attempts = 0
        self._research_normal_quote_attempts = 0
        self._research_normal_quote_successes = 0
        self._research_completion_relaxed_successes = 0
        self._research_completion_relaxed_attempts = 0
        self._research_completion_quote_attempts = 0
        self._research_completion_quote_successes = 0
        self._research_completion_attempt_cap_hits = 0
        self._research_completion_success_cap_hits = 0
        self._research_normal_attempt_cap_hits = 0
        self._research_aggressive_context: dict[int, dict[str, Any]] = {}
        self.research_enabled = self._env_bool('STRATEGY1_RESEARCH', self._as_bool(getattr(cfg, 'research_enabled', True)))
        self.research_every_n = max(1, self._env_int('STRATEGY1_RESEARCH_EVERY_N', int(getattr(cfg, 'research_every_n', 1))))
        self.research_book_id = self._env_int('STRATEGY1_RESEARCH_BOOK', int(getattr(cfg, 'research_book_id', -1)))
        self.research_console = self._env_bool('STRATEGY1_RESEARCH_CONSOLE', self._as_bool(getattr(cfg, 'research_console', True)))
        self.research_jsonl = self._env_bool('STRATEGY1_RESEARCH_JSONL', self._as_bool(getattr(cfg, 'research_jsonl', True)))
        self.research_queue_size = max(256, self._env_int('STRATEGY1_RESEARCH_QUEUE', int(getattr(cfg, 'research_queue_size', 8192))))
        env_dir = os.getenv('STRATEGY1_RESEARCH_DIR', '').strip()
        configured = str(getattr(cfg, 'research_output_dir', '') or '')
        self.research_output_dir = env_dir or configured or os.path.join(self.output_dir, 'strategy1_research')
        self._rq = queue.Queue(maxsize=self.research_queue_size)
        self._rstop = threading.Event()
        self._research_output_file = None
        if self.research_enabled and self.research_jsonl:
            try:
                os.makedirs(self.research_output_dir, exist_ok=True)
                filename = f'strategy1_research_agent_{self.uid}_{self.research_run_id}.jsonl' if self.research_rotate_jsonl else f'strategy1_research_agent_{self.uid}.jsonl'
                path = os.path.join(self.research_output_dir, filename)
                self._research_output_file = path
                self._rfile = open(path, 'a', encoding='utf-8', buffering=1)
            except OSError as exc:
                print(f'[S1R_ERROR] stage=init_jsonl error={self._short(exc)}', flush=True)
        if self.research_enabled:
            self._rworker = threading.Thread(target=self._writer_loop, name=f"s1r-{getattr(self, 'uid', 'agent')}", daemon=True)
            self._rworker.start()
            atexit.register(self._shutdown_research)
        self._research_ready = True
        for record in self._research_early:
            self._enqueue(record)
        self._research_early.clear()
        self._enqueue({'type': 'RESEARCH_CONFIG', 'agent_id': getattr(self, 'uid', None), 'wall_time_ns': time.time_ns(), 'enabled': self.research_enabled, 'every_n': self.research_every_n, 'book_filter': self.research_book_id, 'console': self.research_console, 'jsonl': self.research_jsonl, 'queue_size': self.research_queue_size, 'output_dir': self.research_output_dir, 'policy_version': 'deadlock_fix_v4_1_strict', 'fix_global_stress': self.research_fix_global_stress, 'neutral_fallback': self.research_neutral_fallback, 'adaptive_spread_thresholds': self.research_adaptive_spread_thresholds, 'stress_percentile': self.research_stress_percentile, 'toxic_percentile': self.research_toxic_percentile, 'inactive_bootstrap': self.research_inactive_bootstrap, 'trade_global_stress': self.research_trade_global_stress, 'sync_min_order': self.research_sync_min_order, 'promote_min_order': self.research_promote_min_order, 'bootstrap_dead_as_mm': self.research_bootstrap_dead_as_mm, 'fix_inventory_util': self.research_fix_inventory_util, 'fix_quote_reservation': self.research_fix_quote_reservation, 'bootstrap_manage_min_clip': self.research_bootstrap_manage_min_clip, 'bootstrap_force_close_ticks': self.research_bootstrap_force_close_ticks, 'legacy_force_close_min_bps': self.research_bootstrap_force_close_min_bps, 'legacy_hard_close_ticks': self.research_bootstrap_hard_close_ticks, 'aggressive_close_touch_gate': self.research_aggressive_close_touch_gate, 'aggressive_close_fee_buffer_bps': self.research_aggressive_close_fee_buffer_bps, 'aggressive_close_min_net_bps': self.research_aggressive_close_min_net_bps, 'candidate_backfill': self.research_candidate_backfill, 'candidate_attempt_cap': self.research_candidate_attempt_cap, 'toxic_pnl_min_samples': self.research_toxic_pnl_min_samples, 'toxic_pnl_hard_floor': self.research_toxic_pnl_hard_floor, 'yellow_sparse_active': self.research_yellow_sparse_active, 'green_sparse_active': self.research_green_sparse_active, 'dust_safe_close': self.research_dust_safe_close, 'dust_park_enabled': self.research_dust_park_enabled, 'dust_heartbeat_ticks': self.research_dust_heartbeat_ticks, 'dust_warn_ticks': self.research_dust_warn_ticks, 'dust_compact_enabled': self.research_dust_compact_enabled, 'dust_compact_min_fraction': self.research_dust_compact_min_fraction, 'dust_compact_books_per_tick': self.research_dust_compact_books_per_tick, 'kappa_completion_enabled': self.research_kappa_completion_enabled, 'kappa_completion_target': self.research_kappa_completion_target, 'kappa_completion_rank_bonus': self.research_kappa_completion_rank_bonus, 'kappa_completion_fill_mult': self.research_kappa_completion_fill_mult, 'kappa_completion_fill_floor': self.research_kappa_completion_fill_floor, 'kappa_completion_relaxed_success_cap': self.research_kappa_completion_relaxed_success_cap, 'kappa_completion_attempt_cap': self.research_kappa_completion_attempt_cap, 'kappa_completion_success_cap': self.research_kappa_completion_success_cap, 'normal_attempt_cap': self.research_normal_attempt_cap, 'kappa_completion_recent_pnl_floor': self.research_kappa_completion_recent_pnl_floor, 'rotate_jsonl': self.research_rotate_jsonl, 'run_id': self.research_run_id, 'output_file': self._research_output_file})

    @staticmethod
    def _bsimpl_3_Strategy1_Research__percentile(values: list[float], q: float) -> float | None:
        """Small-N linear percentile with no NumPy dependency."""
        if not values:
            return None
        xs = sorted((float(v) for v in values))
        if len(xs) == 1:
            return xs[0]
        pos = (len(xs) - 1) * min(1.0, max(0.0, q))
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac

    @staticmethod
    def _bsimpl_3_Strategy1_Research__profile_float(profile: Any, name: str) -> float | None:
        try:
            value = getattr(profile, name, None)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _bsimpl_3_Strategy1_Research__update_spread_thresholds(self, profiles: list[BookProfile]) -> None:
        spreads = [value for profile in profiles if (value := self._profile_float(profile, 'spread_bps')) is not None and value >= 0.0]
        if self.research_adaptive_spread_thresholds and len(spreads) >= self.research_min_profiles_for_adaptive:
            p_stress = self._percentile(spreads, self.research_stress_percentile)
            p_toxic = self._percentile(spreads, self.research_toxic_percentile)
            stress = max(self.research_stress_floor_bps, p_stress if p_stress is not None else self.research_stress_fallback_bps)
            toxic = max(self.research_toxic_floor_bps, p_toxic if p_toxic is not None else self.research_toxic_fallback_bps, stress + self.research_toxic_gap_bps)
        else:
            stress = self.research_stress_fallback_bps
            toxic = max(self.research_toxic_fallback_bps, stress + self.research_toxic_gap_bps)
        self._research_stress_spread_bps = float(stress)
        self._research_toxic_spread_bps = float(toxic)

    def _bsimpl_3_Strategy1_Research__sync_exchange_constraints(self, state: MarketSimulationStateUpdate) -> None:
        cfg = getattr(state, 'config', None)
        try:
            self._research_volume_decimals = max(0, int(getattr(cfg, 'volumeDecimals', self._research_volume_decimals)))
        except (TypeError, ValueError):
            pass
        if not self.research_sync_min_order:
            return
        try:
            state_min = float(getattr(cfg, 'min_order_size', 0.0) or 0.0)
        except (TypeError, ValueError):
            state_min = 0.0
        if state_min > 0.0:
            self._research_exchange_min_order_size = state_min
            self.min_order_size = state_min
        else:
            self._research_exchange_min_order_size = max(0.0, float(getattr(self, 'min_order_size', 0.0) or 0.0))

    def _bsimpl_3_Strategy1_Research__execution_flat_epsilon(self) -> float:
        """Half one quantity tick; only sub-half-tick residuals are execution-flat."""
        return max(0.5 * 10.0 ** (-int(self._research_volume_decimals)), 1e-12)

    def _bsimpl_3_Strategy1_Research_classify_market_regime_from_profiles(self, profiles, predictions, selection):
        profile_list = list(profiles)
        self._update_spread_thresholds(profile_list)
        regime = self._bsimpl_2_Strategy1_Debug_classify_market_regime_from_profiles(profile_list, predictions, selection)
        spreads = [value for profile in profile_list if (value := self._profile_float(profile, 'spread_bps')) is not None]
        vols = [value for profile in profile_list if (value := self._profile_float(profile, 'volatility')) is not None]
        rates = [value for profile in profile_list if (value := self._profile_float(profile, 'trade_rate')) is not None]
        inactive = sum((1 for profile in profile_list if str(getattr(profile, 'tier', '')).upper() == 'INACTIVE'))
        active = max(0, len(profile_list) - inactive)
        stressed_count = sum((1 for value in spreads if value >= self._research_stress_spread_bps))
        liquid_count = sum((1 for profile in profile_list if (self._profile_float(profile, 'spread_bps') or 0.0) < self._research_stress_spread_bps and (self._profile_float(profile, 'trade_rate') or 0.0) >= float(getattr(self, 'archetype_dead_trade_rate', 0.0))))
        low_trade_count = sum((1 for profile in profile_list if (self._profile_float(profile, 'trade_rate') or 0.0) < float(getattr(self, 'archetype_dead_trade_rate', 0.0))))
        pred_values = list(predictions.values()) if isinstance(predictions, dict) else []
        up = sum((1 for p in pred_values if str(getattr(p, 'direction', '')).upper() == 'UP'))
        down = sum((1 for p in pred_values if str(getattr(p, 'direction', '')).upper() == 'DOWN'))
        pred_n = max(len(pred_values), 1)
        n = max(len(profile_list), 1)
        trigger = self._pick(regime, 'trigger', 'reason', 'cause')
        threshold = self._pick(regime, 'threshold', 'trigger_threshold')
        self._emit('REGIME', tick=self._tick, mode=getattr(regime, 'mode', None), overlay=getattr(regime, 'scoring_overlay', None), book_count=len(profile_list), active=active, inactive=inactive, spread_med=self._percentile(spreads, 0.5), spread_p90=self._percentile(spreads, 0.9), spread_max=max(spreads) if spreads else None, stress_spread_bps=self._research_stress_spread_bps, toxic_spread_bps=self._research_toxic_spread_bps, vol_med=self._percentile(vols, 0.5), vol_p90=self._percentile(vols, 0.9), trade_rate_med=self._percentile(rates, 0.5), liquid_ratio=liquid_count / n, low_trade_ratio=low_trade_count / n, stressed_ratio=stressed_count / n, trend_up_ratio=up / pred_n, trend_down_ratio=down / pred_n, trigger=trigger if trigger is not None else 'UNEXPOSED_BY_PARENT', threshold=threshold if threshold is not None else 'UNEXPOSED_BY_PARENT', adaptive=self.research_adaptive_spread_thresholds, min_order_size=self._research_exchange_min_order_size)
        return regime

    def _bsimpl_3_Strategy1_Research_get_regime_params(self, regime: MarketRegime) -> RegimeParamSet:
        params = self._bsimpl_1_Strategy1_get_regime_params(regime)
        mode = str(getattr(regime, 'mode', '')).upper()
        overlay = str(getattr(regime, 'scoring_overlay', '')).upper()
        if self.research_trade_global_stress and mode == 'STRESSED' and (overlay != 'SCORING_PRESSURE'):
            return RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=max(float(params.spread_offset), 0.45), skew_strength=min(float(params.skew_strength), 0.05), size_mult=min(float(params.size_mult), self.research_global_stress_size_mult), profit_target_bps=params.profit_target_bps, stop_loss_bps=params.stop_loss_bps, min_fill_prob=params.min_fill_prob, buy_bias=params.buy_bias, sell_bias=params.sell_bias)
        return params

    def _bsimpl_3_Strategy1_Research__mem(self, book_id: int):
        """Attach the book id to parent BookMemory for completion-aware ranking."""
        mem = self._bsimpl_1_Strategy1__mem(book_id)
        try:
            setattr(mem, '_research_book_id', int(book_id))
        except Exception:
            pass
        return mem

    def _bsimpl_3_Strategy1_Research__completion_observation_count(self, book_id: int) -> int:
        return int(self._research_realized_observations_by_book.get(int(book_id), 0))

    def _bsimpl_3_Strategy1_Research__is_kappa_completion_candidate(self, book_id: int) -> bool:
        if not self.research_kappa_completion_enabled:
            return False
        samples = self._completion_observation_count(book_id)
        if samples <= 0 or samples >= self.research_kappa_completion_target:
            return False
        mem = self._mem(book_id)
        return float(getattr(mem, 'recent_pnl', 0.0) or 0.0) >= self.research_kappa_completion_recent_pnl_floor

    def _bsimpl_3_Strategy1_Research__global_book_rank(self, expected_alpha: float, mem) -> float:
        """Preserve Strategy1 economics, then prioritize near-complete Kappa books."""
        base_rank = self._bsimpl_1_Strategy1__global_book_rank(expected_alpha, mem)
        if not self.research_kappa_completion_enabled:
            return base_rank
        book_id = getattr(mem, '_research_book_id', None)
        if book_id is None or not self._is_kappa_completion_candidate(int(book_id)):
            return base_rank
        samples = self._completion_observation_count(int(book_id))
        denom = max(1, self.research_kappa_completion_target - 1)
        progress = max(0.0, min(1.0, samples / denom))
        return base_rank + self.research_kappa_completion_rank_bonus * progress

    def _bsimpl_3_Strategy1_Research__is_compactable_dust(self, net_base: float) -> bool:
        if not self.research_dust_compact_enabled or not self._is_dust_qty(net_base):
            return False
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        if min_size <= 0.0:
            return False
        abs_base = abs(float(net_base))
        threshold = self.research_dust_compact_min_fraction * min_size
        return abs_base > threshold + self._execution_flat_epsilon()

    def _bsimpl_3_Strategy1_Research__select_dust_compaction_books(self, state: MarketSimulationStateUpdate) -> set[int]:
        """Pick a tiny bounded set; ordinary executable inventory keeps its 8-slot pool."""
        if not self.research_dust_compact_enabled:
            return set()
        tick = int(getattr(self, '_tick', 0) or 0)
        rows: list[tuple[float, int, int]] = []
        for book_id, info in self._research_parked_dust.items():
            qty = float(info.get('net_base', 0.0) or 0.0)
            if not self._is_compactable_dust(qty):
                continue
            if book_id not in getattr(state, 'books', {}):
                continue
            first_tick = int(info.get('first_tick', tick))
            age = max(0, tick - first_tick)
            rows.append((abs(qty), age, int(book_id)))
        rows.sort(reverse=True)
        return {book_id for _abs_qty, _age, book_id in rows[:self.research_dust_compact_books_per_tick]}

    def _bsimpl_3_Strategy1_Research__dust_compaction_safe_for_any_fill(self, net_base: float) -> bool:
        """Proof condition for q -> q - sign(q)*f, 0<=f<=min_size."""
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        q = abs(float(net_base))
        return min_size > 0.0 and q > 0.5 * min_size and (q < min_size)

    def _bsimpl_3_Strategy1_Research__sparse_active_tier_enabled(self, tier: str) -> bool:
        tier_u = str(tier or '').upper()
        return tier_u == 'YELLOW' and self.research_yellow_sparse_active or (tier_u == 'GREEN' and self.research_green_sparse_active)

    def _bsimpl_3_Strategy1_Research__is_dust_qty(self, net_base: float) -> bool:
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        abs_base = abs(float(net_base))
        return self.research_dust_safe_close and min_size > 0.0 and (abs_base >= self._execution_flat_epsilon()) and (abs_base + 1e-12 < min_size)

    def _bsimpl_3_Strategy1_Research__refresh_dust_state(self, book_id: int, net_base: float, *, emit: bool=True) -> bool:
        """Park exchange-uncloseable residuals without hiding exact inventory.

        V4.1 Strict does not synthesize an exchange-illegal exact close and does
        not add fresh risk merely to make dust executable. Dust remains in exact
        accounting, is removed from the finite management queue, and is
        quarantined from fresh MM/maintenance orders.
        """
        if not self.research_dust_park_enabled:
            self._research_parked_dust.pop(book_id, None)
            return False
        tick = int(getattr(self, '_tick', 0) or 0)
        qty = float(net_base)
        is_dust = self._is_dust_qty(qty)
        prior = self._research_parked_dust.get(book_id)
        if not is_dust:
            if prior is not None:
                self._research_dust_releases += 1
                self._research_parked_dust.pop(book_id, None)
                if emit:
                    self._emit('POSITION_GUARD', tick=tick, book_id=book_id, reason='DUST_RELEASED', net_base=qty, min_order_size=self._research_exchange_min_order_size, first_tick=prior.get('first_tick'), age_ticks=max(0, tick - int(prior.get('first_tick', tick))), parked=False)
            return False
        if prior is None:
            prior = {'first_tick': tick, 'last_tick': tick, 'last_emit_tick': tick, 'net_base': qty}
            self._research_parked_dust[book_id] = prior
            self._research_dust_entries += 1
            self._research_dust_blocks += 1
            if emit:
                self._emit('POSITION_GUARD', tick=tick, book_id=book_id, reason='DUST_POSITION', net_base=qty, min_order_size=self._research_exchange_min_order_size, first_tick=tick, age_ticks=0, parked=True, stale=False)
            return True
        prior['last_tick'] = tick
        prior['net_base'] = qty
        age = max(0, tick - int(prior.get('first_tick', tick)))
        last_emit = int(prior.get('last_emit_tick', tick))
        if emit and tick - last_emit >= self.research_dust_heartbeat_ticks:
            prior['last_emit_tick'] = tick
            self._research_dust_heartbeats += 1
            self._emit('POSITION_GUARD', tick=tick, book_id=book_id, reason='DUST_HEARTBEAT', net_base=qty, min_order_size=self._research_exchange_min_order_size, first_tick=prior.get('first_tick'), age_ticks=age, parked=True, stale=age >= self.research_dust_warn_ticks)
        return True

    def _bsimpl_3_Strategy1_Research_classify_book_archetype(self, profile: BookProfile, regime: MarketRegime) -> BookArchetype:
        spread_bps = float(getattr(profile, 'spread_bps', 0.0) or 0.0)
        trade_rate = float(getattr(profile, 'trade_rate', 0.0) or 0.0)
        volatility = float(getattr(profile, 'volatility', 0.0) or 0.0)
        imbalance = float(getattr(profile, 'imbalance', 0.0) or 0.0)
        predict_score = float(getattr(profile, 'predict_score', 0.0) or 0.0)
        tier = str(getattr(profile, 'tier', '')).upper()
        overlay = str(getattr(regime, 'scoring_overlay', '')).upper()
        stress_cutoff = self._research_stress_spread_bps if self.research_adaptive_spread_thresholds else float(self.archetype_stressed_spread_bps)
        bootstrap_inactive = self.research_inactive_bootstrap and self.research_bootstrap_dead_as_mm and (overlay == 'SCORING_PRESSURE') and (tier == 'INACTIVE')
        profile_book_id = getattr(profile, 'book_id', None)
        parked_dust = self.research_dust_park_enabled and profile_book_id in self._research_parked_dust
        if parked_dust:
            archetype: BookArchetype = 'TOXIC_BOOK'
            source = 'PARKED_DUST'
        elif spread_bps >= stress_cutoff:
            archetype: BookArchetype = 'STRESSED'
            source = 'LOCAL_SPREAD'
        elif bootstrap_inactive:
            extreme_vol = self.research_bootstrap_extreme_vol_mult * max(float(self.archetype_vol_threshold), 1e-12)
            if volatility >= extreme_vol:
                archetype = 'TOXIC_BOOK'
                source = 'BOOTSTRAP_EXTREME_VOL'
            elif abs(imbalance) >= self.archetype_wall_imbalance:
                archetype = 'WALL_BOOK'
                source = 'BOOTSTRAP_WALL'
            elif volatility >= self.archetype_vol_threshold and abs(predict_score) >= self.direction_threshold:
                archetype = 'TREND_BOOK'
                source = 'BOOTSTRAP_VOL_DIRECTION'
            elif abs(predict_score) >= self.direction_threshold:
                archetype = 'TREND_BOOK'
                source = 'BOOTSTRAP_DIRECTION'
            else:
                archetype = 'MM_BOOK'
                source = 'INACTIVE_BOOTSTRAP'
        elif self._sparse_active_tier_enabled(tier) and trade_rate < self.archetype_dead_trade_rate:
            extreme_vol = self.research_bootstrap_extreme_vol_mult * max(float(self.archetype_vol_threshold), 1e-12)
            if volatility >= extreme_vol:
                archetype = 'TOXIC_BOOK'
                source = 'ACTIVE_SPARSE_EXTREME_VOL'
            elif abs(imbalance) >= self.archetype_wall_imbalance:
                archetype = 'WALL_BOOK'
                source = 'ACTIVE_SPARSE_WALL'
            elif abs(predict_score) >= self.direction_threshold:
                archetype = 'TREND_BOOK'
                source = 'ACTIVE_SPARSE_TREND'
            else:
                archetype = 'MM_BOOK'
                source = 'ACTIVE_SPARSE_MM'
        elif trade_rate < self.archetype_dead_trade_rate:
            archetype = 'DEAD_BOOK'
            source = 'DEAD_TRADE_RATE'
        elif spread_bps < self.archetype_mm_spread_bps:
            archetype = 'MM_BOOK'
            source = 'NARROW_MM'
        elif abs(imbalance) >= self.archetype_wall_imbalance:
            archetype = 'WALL_BOOK'
            source = 'WALL_IMBALANCE'
        elif volatility >= self.archetype_vol_threshold and abs(predict_score) >= self.direction_threshold:
            archetype = 'TREND_BOOK'
            source = 'VOL_AND_DIRECTION'
        elif volatility >= self.archetype_vol_threshold:
            archetype = 'TOXIC_BOOK'
            source = 'HIGH_VOL'
        elif abs(predict_score) >= self.direction_threshold:
            archetype = 'TREND_BOOK'
            source = 'DIRECTION'
        else:
            archetype = 'MM_BOOK' if self.research_neutral_fallback else 'TOXIC_BOOK'
            source = 'NEUTRAL_FALLBACK' if self.research_neutral_fallback else 'LEGACY_TOXIC_FALLBACK'
        if self.debug_enabled:
            record = self._book_record(profile.book_id)
            record['archetype'] = archetype
            record['archetype_source'] = source
            record['profile_spread_bps'] = spread_bps
            record['volatility'] = volatility
            record['trade_rate'] = trade_rate
            record['imbalance'] = imbalance
            record['stress_spread_bps'] = stress_cutoff
            record['toxic_spread_bps'] = self._research_toxic_spread_bps
            record['stressed_by_spread'] = spread_bps >= stress_cutoff
            record['stressed_by_regime'] = False
            record['legacy_stressed_by_regime'] = str(getattr(regime, 'mode', '')).upper() == 'STRESSED'
            record['bootstrap_inactive'] = bootstrap_inactive
            record['parked_dust'] = parked_dust
            record['dead_trade_rate_hit'] = trade_rate < self.archetype_dead_trade_rate
            record['active_sparse'] = self._sparse_active_tier_enabled(tier) and trade_rate < self.archetype_dead_trade_rate
            record['active_sparse_tier'] = tier if record['active_sparse'] else None
        return archetype

    def _bsimpl_3_Strategy1_Research_is_toxic_book(self, book_id: int, profile: BookProfile, archetype: BookArchetype) -> bool:
        dust_info = self._research_parked_dust.get(book_id)
        if self.research_dust_park_enabled and dust_info is not None:
            if self.debug_enabled:
                record = self._book_record(book_id)
                record['dust_position'] = True
                record['dust_quarantine'] = True
                record['dust_qty'] = abs(float(dust_info.get('net_base', 0.0)))
                record['toxic'] = False
            return True
        mem = self._mem(book_id)
        spread_bps = self._profile_float(profile, 'spread_bps')
        toxic_cutoff = self._research_toxic_spread_bps if self.research_adaptive_spread_thresholds else float(self.toxic_spread_bps)
        toxic_loss = mem.loss_streak >= self.toxic_loss_streak
        pnl_samples = int(self._research_round_trip_samples_by_book.get(book_id, 0))
        toxic_pnl_raw = mem.recent_pnl < self.toxic_recent_pnl
        toxic_pnl = toxic_pnl_raw and (pnl_samples >= self.research_toxic_pnl_min_samples or mem.recent_pnl <= self.research_toxic_pnl_hard_floor)
        toxic_spread = spread_bps is not None and spread_bps > toxic_cutoff
        toxic_archetype = archetype in ('STRESSED', 'TOXIC_BOOK')
        toxic_red_tier = str(getattr(profile, 'tier', '')).upper() == 'RED'
        toxic = any((toxic_loss, toxic_pnl, toxic_spread, toxic_archetype, toxic_red_tier))
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['toxic'] = toxic
            record['loss_streak'] = mem.loss_streak
            record['recent_pnl'] = mem.recent_pnl
            record['toxic_loss'] = toxic_loss
            record['toxic_pnl'] = toxic_pnl
            record['toxic_pnl_raw'] = toxic_pnl_raw
            record['toxic_pnl_samples'] = pnl_samples
            record['toxic_pnl_min_samples'] = self.research_toxic_pnl_min_samples
            record['toxic_pnl_hard_floor'] = self.research_toxic_pnl_hard_floor
            record['toxic_spread'] = toxic_spread
            record['toxic_archetype'] = toxic_archetype
            record['toxic_red_tier'] = toxic_red_tier
            record['toxic_spread_bps'] = toxic_cutoff
        return toxic

    def _bsimpl_3_Strategy1_Research__net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        """V2 inventory snapshot in one unit system: signed base utilization."""
        if not self.research_fix_inventory_util:
            return self._bsimpl_2_Strategy1_Debug__net_inventory(book_id, mid)
        if mid <= 0:
            return InventorySnapshot(0.0, 0.0, 'FLAT', None, None, 0)
        tracker = self._position_tracker_snapshot(book_id)
        net_base = float(tracker.net_qty)
        max_base = max(float(self.max_inventory_base), 1e-09)
        signed_util = net_base / max_base
        flat_eps = self._execution_flat_epsilon()
        if abs(net_base) < flat_eps:
            band = 'FLAT'
            self._position_ticks.pop(book_id, None)
            self._research_position_tick_seen.pop(book_id, None)
            self._inventory_reason.pop(book_id, None)
        else:
            band = 'MAX_LONG' if net_base >= max_base else 'MAX_SHORT' if net_base <= -max_base else 'LONG' if net_base > 0.0 else 'SHORT'
            current_tick = int(getattr(self, '_tick', 0) or 0)
            if self._research_position_tick_seen.get(book_id) != current_tick:
                self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
                self._research_position_tick_seen[book_id] = current_tick
        position_ticks = self._position_ticks.get(book_id, 0)
        vwap = tracker.vwap_entry
        unrealized_bps: float | None = None
        if vwap and vwap > 0:
            if net_base > 0:
                unrealized_bps = (mid - vwap) / vwap * 10000.0
            elif net_base < 0:
                unrealized_bps = (vwap - mid) / vwap * 10000.0
        inventory = InventorySnapshot(net_base=net_base, inventory_ratio=signed_util, band=band, vwap_entry=vwap, unrealized_bps=unrealized_bps, position_ticks=position_ticks, opened_at_ns=tracker.opened_at_ns, reason=self._inventory_reason.get(book_id, 'UNKNOWN'))
        try:
            setattr(inventory, '_research_book_id', int(book_id))
        except Exception:
            pass
        if self.debug_enabled:
            self._book_record(book_id)['inventory'] = {'net_base': inventory.net_base, 'ratio': inventory.inventory_ratio, 'signed_util': signed_util, 'band': inventory.band, 'vwap_entry': inventory.vwap_entry, 'unrealized_bps': inventory.unrealized_bps, 'position_ticks': inventory.position_ticks, 'reason': inventory.reason}
        self._refresh_dust_state(book_id, net_base, emit=True)
        return inventory

    def _bsimpl_3_Strategy1_Research__inventory_util(self, inventory: InventorySnapshot) -> float:
        if not self.research_fix_inventory_util:
            return self._bsimpl_1_Strategy1__inventory_util(inventory)
        return abs(float(inventory.net_base)) / max(float(self.max_inventory_base), 1e-09)

    def _bsimpl_3_Strategy1_Research__inventory_needs_management(self, inventory: InventorySnapshot) -> bool:
        if inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        abs_base = abs(float(inventory.net_base))
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        eps = self._execution_flat_epsilon()
        if self.research_dust_park_enabled and self._is_dust_qty(inventory.net_base):
            book_id = getattr(inventory, '_research_book_id', None)
            return book_id is not None and int(book_id) in self._research_dust_compact_ids_this_tick and self._dust_compaction_safe_for_any_fill(inventory.net_base)
        if self._research_bootstrap_active and self.research_bootstrap_manage_min_clip and (abs_base >= eps) and (min_size <= 0.0 or abs_base + 1e-12 >= min_size):
            return True
        return self._inventory_util(inventory) >= float(self.inventory_close_threshold)

    def _bsimpl_3_Strategy1_Research_skewed_quote_prices(self, bid: float, ask: float, signal: float, inventory_ratio: float, regime_params: RegimeParamSet, price_dec: int, edge_bias: float=0.0) -> tuple[float, float] | None:
        if not self.research_fix_quote_reservation:
            return self._bsimpl_1_Strategy1_skewed_quote_prices(bid, ask, signal, inventory_ratio, regime_params, price_dec, edge_bias)
        spread = ask - bid
        if spread <= 0.0:
            return None
        mid = 0.5 * (bid + ask)
        directional = max(-1.0, min(1.0, float(signal) + float(edge_bias)))
        directional_bias = float(regime_params.buy_bias) if directional >= 0.0 else float(regime_params.sell_bias)
        alpha_shift = spread * float(regime_params.skew_strength) * directional * directional_bias
        inventory_shift = spread * float(self.inventory_skew_strength) * float(inventory_ratio)
        reservation = mid + alpha_shift - inventory_shift
        half_spread = spread * max(0.05, float(regime_params.spread_offset))
        tick_size = 10.0 ** (-int(price_dec))
        bid_px = min(reservation - half_spread, ask - tick_size)
        ask_px = max(reservation + half_spread, bid + tick_size)
        bid_px = round(bid_px, price_dec)
        ask_px = round(ask_px, price_dec)
        if bid_px <= 0.0 or bid_px >= ask_px:
            return None
        return (bid_px, ask_px)

    def _bsimpl_3_Strategy1_Research__compute_close_score(self, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> float:
        if not self.research_fix_inventory_util:
            return self._bsimpl_1_Strategy1__compute_close_score(inventory, regime_params, regime, archetype)
        unreal = inventory.unrealized_bps
        target = max(float(regime_params.profit_target_bps), 1e-09)
        stop = max(float(regime_params.stop_loss_bps), 1e-09)
        pnl_component = 0.0
        if unreal is not None:
            if unreal >= target or unreal <= -stop:
                pnl_component = 1.0
            elif unreal > 0.0:
                pnl_component = unreal / target
            else:
                pnl_component = abs(unreal) / stop
        inventory_risk = min(1.0, self._inventory_util(inventory))
        regime_risk = 0.0
        if str(getattr(regime, 'mode', '')).upper() == 'STRESSED':
            regime_risk = 1.0
        elif archetype in ('TOXIC_BOOK', 'WALL_BOOK'):
            regime_risk = 0.6
        elif archetype == 'DEAD_BOOK':
            regime_risk = 0.4
        time_risk = min(1.0, float(inventory.position_ticks) / max(float(self.position_max_ticks), 1.0))
        return 0.5 * pnl_component + 0.3 * inventory_risk + 0.2 * max(regime_risk, time_risk)

    def _bsimpl_3_Strategy1_Research__allows_aggressive_close(self, book_id: int, inventory: InventorySnapshot, close_score: float, time_stop: bool, stop_loss_hit: bool) -> bool:
        if stop_loss_hit or inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        if self._research_bootstrap_active and self.research_bootstrap_allow_aggressive_close:
            ticks = int(inventory.position_ticks)
            if not self.research_aggressive_close_touch_gate:
                if ticks >= self.research_bootstrap_hard_close_ticks:
                    return True
                if ticks >= self.research_bootstrap_force_close_ticks:
                    unreal = inventory.unrealized_bps
                    return unreal is not None and unreal >= self.research_bootstrap_force_close_min_bps
                return False
            if ticks >= self.research_bootstrap_force_close_ticks:
                ctx = self._research_aggressive_context.get(book_id) or {}
                net_touch_bps = ctx.get('net_touch_bps')
                if net_touch_bps is not None:
                    return float(net_touch_bps) >= self.research_aggressive_close_min_net_bps
            return False
        return self._bsimpl_1_Strategy1__allows_aggressive_close(book_id, inventory, close_score, time_stop, stop_loss_hit)

    def _bsimpl_3_Strategy1_Research__execute_aggressive_close(self, response: FinanceAgentResponse, book_id: int, book: Book, qty: float, long_pos: bool) -> bool:
        """Submit close without clearing position state before confirmed fill."""
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        if not self._passes_fee_gate(book_id, aggressive=True):
            return False
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return False
        account = self.accounts[book_id]
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.market_order(book_id=book_id, direction=close_dir, quantity=qty, stp=STP.CANCEL_OLDEST, delay=0)
            return True
        if close_dir == OrderDirection.BUY:
            px = book.asks[0].price
            if account.quote_balance.free >= qty * px:
                response.market_order(book_id=book_id, direction=close_dir, quantity=qty, stp=STP.CANCEL_OLDEST, delay=0)
                return True
        return False

    def _bsimpl_3_Strategy1_Research__manage_inventory(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book: Book, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> int:
        touch_gross_bps = None
        net_touch_bps = None
        entry = inventory.vwap_entry
        if entry is not None and float(entry) > 0.0 and getattr(book, 'bids', None) and getattr(book, 'asks', None):
            entry_f = float(entry)
            if inventory.net_base > 0.0:
                touch_px = float(book.bids[0].price)
                touch_gross_bps = (touch_px - entry_f) / entry_f * 10000.0
            elif inventory.net_base < 0.0:
                touch_px = float(book.asks[0].price)
                touch_gross_bps = (entry_f - touch_px) / entry_f * 10000.0
            if touch_gross_bps is not None:
                net_touch_bps = float(touch_gross_bps) - self.research_aggressive_close_fee_buffer_bps
        self._research_aggressive_context[book_id] = {'touch_gross_bps': touch_gross_bps, 'net_touch_bps': net_touch_bps, 'age_ticks': int(inventory.position_ticks)}
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['aggressive_touch_gross_bps'] = touch_gross_bps
            record['aggressive_touch_net_bps'] = net_touch_bps
            record['aggressive_close_fee_buffer_bps'] = self.research_aggressive_close_fee_buffer_bps
            record['aggressive_close_min_net_bps'] = self.research_aggressive_close_min_net_bps
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        abs_base = abs(float(inventory.net_base))
        if self.research_dust_safe_close and inventory.band != 'FLAT' and self._is_dust_qty(inventory.net_base):
            self._refresh_dust_state(book_id, inventory.net_base, emit=True)
            compact_selected = book_id in self._research_dust_compact_ids_this_tick and self._dust_compaction_safe_for_any_fill(inventory.net_base)
            if compact_selected:
                self._research_dust_compact_attempts += 1
                before_ix = len(response.instructions)
                n = self._bsimpl_1_Strategy1__place_passive_inventory_exit(response, state, book_id, book, inventory, min_size)
                if n:
                    self._research_dust_compact_orders += 1
                    self._research_dust_compact_active[book_id] = int(getattr(self, '_tick', 0) or 0)
                    self._inventory_reason[book_id] = 'DUST_COMPACT'
                    self._emit('POSITION_GUARD', tick=getattr(self, '_tick', None), book_id=book_id, reason='DUST_COMPACT', net_base=inventory.net_base, min_order_size=min_size, projected_full_fill_net=float(inventory.net_base) - (min_size if inventory.net_base > 0.0 else -min_size), exposure_nonincreasing=True, instructions=len(response.instructions) - before_ix)
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record['action'] = 'MANAGE'
                        record['reason'] = 'DUST_COMPACT'
                        record['dust_compact'] = True
                        record['dust_compact_qty'] = min_size
                        record['instructions'] = n
                    return n
                self._emit('POSITION_GUARD', tick=getattr(self, '_tick', None), book_id=book_id, reason='DUST_COMPACT_BLOCKED', net_base=inventory.net_base, min_order_size=min_size, exposure_nonincreasing=True)
            if self.debug_enabled:
                record = self._book_record(book_id)
                record['dust_position'] = True
                record['dust_quarantine'] = True
                record['dust_qty'] = abs_base
                record['min_order_size'] = min_size
                record['dust_compact_selected'] = compact_selected
            return 0
        return self._bsimpl_2_Strategy1_Debug__manage_inventory(response, state, book_id, book, inventory, regime_params, regime, archetype)

    def _bsimpl_3_Strategy1_Research__dust_fill_matches_recent_compaction(self, book_id: int) -> bool:
        """Telemetry-only attribution guard for DUST_COMPACT fills.

        A dust transition is counted as a compaction fill only when this book
        actually emitted a DUST_COMPACT order recently. This prevents natural
        residual cleanup from inflating the compaction-fill metric.
        """
        submitted_tick = self._research_dust_compact_active.get(int(book_id))
        if submitted_tick is None:
            return False
        now = int(getattr(self, '_tick', 0) or 0)
        return 0 <= now - int(submitted_tick) <= 2

    def _bsimpl_3_Strategy1_Research_onTrade(self, event, validator: str | None=None) -> None:
        book_id = getattr(event, 'bookId', None)
        own = getattr(event, 'takerAgentId', None) == getattr(self, 'uid', None) or getattr(event, 'makerAgentId', None) == getattr(self, 'uid', None)
        before = 0.0
        pnl_before = 0.0
        if book_id is not None:
            before = float(self._position_tracker_snapshot(book_id).net_qty)
            pnl_before = float(self._pnl_tick_buffer.get(book_id, 0.0))
        self._bsimpl_2_Strategy1_Debug_onTrade(event, validator)
        if book_id is None or not own:
            return
        after = float(self._position_tracker_snapshot(book_id).net_qty)
        pnl_after = float(self._pnl_tick_buffer.get(book_id, 0.0))
        realized_delta = pnl_after - pnl_before
        if abs(realized_delta) > 1e-12:
            self._research_realized_observations_by_book[book_id] = self._research_realized_observations_by_book.get(book_id, 0) + 1
        before_was_dust = self._is_dust_qty(before)
        self._refresh_dust_state(book_id, after, emit=True)
        eps = self._execution_flat_epsilon()
        if abs(before) < eps and abs(after) >= eps:
            transition = 'OPEN'
            self._research_position_opens += 1
        elif abs(before) >= eps and abs(after) < eps:
            transition = 'FLAT'
            self._research_round_trip_closes += 1
            self._research_position_reductions += 1
            self._research_round_trip_samples_by_book[book_id] = self._research_round_trip_samples_by_book.get(book_id, 0) + 1
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
        elif before * after < 0.0:
            transition = 'CROSS'
            self._research_position_reductions += 1
            if abs(realized_delta) > 1e-12:
                self._research_round_trip_closes += 1
                self._research_round_trip_samples_by_book[book_id] = self._research_round_trip_samples_by_book.get(book_id, 0) + 1
            self._position_ticks[book_id] = 0
            self._research_position_tick_seen[book_id] = int(getattr(self, '_tick', 0) or 0)
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
                self._inventory_reason[book_id] = 'DUST_COMPACT'
        elif abs(after) < abs(before) - eps:
            transition = 'REDUCE'
            self._research_position_reductions += 1
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
        elif abs(after) > abs(before) + eps:
            transition = 'INCREASE'
        else:
            transition = 'UNCHANGED'
        round_trip_event = transition == 'FLAT' or (transition == 'CROSS' and abs(realized_delta) > 1e-12)
        self._emit('POSITION', tick=getattr(self, '_tick', None), timestamp=getattr(event, 'timestamp', None), book_id=book_id, transition=transition, net_before=before, net_after=after, realized_pnl_delta=realized_delta, realized_book_observations=self._research_realized_observations_by_book.get(book_id, 0), round_trip=round_trip_event, round_trip_total=self._research_round_trip_closes, round_trip_book_samples=self._research_round_trip_samples_by_book.get(book_id, 0), execution_flat_epsilon=eps, reason=self._inventory_reason.get(book_id, 'FLAT' if transition == 'FLAT' else 'UNKNOWN'))

    def _bsimpl_3_Strategy1_Research_dynamic_order_size(self, base_size: float, profile: BookProfile, regime_params: RegimeParamSet, inventory: InventorySnapshot, vol_dec: int, mid: float | None=None) -> float:
        predict_score = float(getattr(profile, 'predict_score', 0.0) or 0.0)
        volatility = float(getattr(profile, 'volatility', 0.0) or 0.0)
        confidence = max(0.5, min(2.0, 1.0 + abs(predict_score)))
        vol_scale = 1.0
        if volatility > 0.0:
            vol_scale = max(0.5, min(2.0, float(self.profile_vol_scale) / volatility))
        spread_factor = 1.0
        if profile.spread is not None and mid is not None and (mid > 0.0):
            spread_bps = float(profile.spread) / mid * 10000.0
            spread_factor = max(0.5, min(1.5, 1.0 - spread_bps / 20.0))
        kappa_scale = 1.0
        if profile.raw_kappa is not None:
            kappa_scale = max(0.5, min(1.5, 1.0 + float(profile.raw_kappa) * 0.2))
        inventory_factor = max(0.3, 1.0 - min(1.0, self._inventory_util(inventory)))
        raw_model_size = float(base_size) * confidence * float(regime_params.size_mult) * vol_scale * spread_factor * kappa_scale * inventory_factor
        size = self._round_order_size(raw_model_size, vol_dec)
        min_size = max(0.0, self._research_exchange_min_order_size)
        promoted = False
        rounded_before_promotion = size
        if self.research_promote_min_order and size > 0.0 and (min_size > 0.0) and (size + 1e-12 < min_size):
            remaining = max(0.0, float(self.max_inventory_base) - abs(float(inventory.net_base)))
            if min_size <= remaining + 1e-12:
                size = round(min_size, vol_dec)
                promoted = True
            else:
                size = 0.0
        if self.debug_enabled and hasattr(profile, 'book_id'):
            record = self._book_record(profile.book_id)
            record['dynamic_size_model_raw'] = raw_model_size
            record['dynamic_size_raw'] = rounded_before_promotion
            record['dynamic_size_final'] = size
            record['inventory_util'] = self._inventory_util(inventory)
            record['min_order_size'] = min_size
            record['size_promoted_to_min'] = promoted
        return size

    def _bsimpl_3_Strategy1_Research__place_skewed_quotes(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book, profile: BookProfile, prediction: DirectionForecast, inventory: InventorySnapshot, regime_params: RegimeParamSet, size: float, edge_bias: float, stats: dict | None=None) -> int:
        """Quote with isolated NORMAL and KAPPA_COMPLETION scheduler lanes.

        V4 allowed every Kappa-completion failure to consume the same shared
        candidate-attempt budget as normal economics. V4.1 gives completion a
        bounded sub-budget (default 4 attempts / 2 successes) and reserves the
        remainder (default 8 attempts / at least 2 success capacity) for normal
        MM. Completion-cap skips do not consume normal or total attempt budget.
        """
        completion_candidate = inventory.band == 'FLAT' and self._is_kappa_completion_candidate(book_id)
        completion_samples = self._completion_observation_count(book_id)
        lane = 'COMPLETION' if completion_candidate else 'NORMAL'
        if self._research_backfill_active:
            if self._research_quote_successes >= self._research_quote_success_cap:
                if self.debug_enabled:
                    record = self._book_record(book_id)
                    record['action'] = 'SKIP'
                    record['reason'] = 'MM_SUCCESS_CAP'
                    record['scheduler_lane'] = lane
                return 0
            if completion_candidate:
                if self._research_completion_quote_successes >= self.research_kappa_completion_success_cap:
                    self._research_completion_success_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record['action'] = 'SKIP'
                        record['reason'] = 'KAPPA_COMPLETION_SUCCESS_CAP'
                        record['scheduler_lane'] = lane
                    return 0
                if self._research_completion_quote_attempts >= self.research_kappa_completion_attempt_cap:
                    self._research_completion_attempt_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record['action'] = 'SKIP'
                        record['reason'] = 'KAPPA_COMPLETION_ATTEMPT_CAP'
                        record['scheduler_lane'] = lane
                    return 0
                self._research_completion_quote_attempts += 1
            else:
                if self._research_normal_quote_attempts >= self.research_normal_attempt_cap:
                    self._research_normal_attempt_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record['action'] = 'SKIP'
                        record['reason'] = 'NORMAL_MM_ATTEMPT_CAP'
                        record['scheduler_lane'] = lane
                    return 0
                self._research_normal_quote_attempts += 1
            self._research_quote_attempts += 1
        allow_relaxed_fill = completion_candidate and self._research_completion_relaxed_successes < self.research_kappa_completion_relaxed_success_cap
        old_min_fill = float(regime_params.min_fill_prob)
        relaxed_min_fill = old_min_fill
        if allow_relaxed_fill:
            relaxed_min_fill = max(self.research_kappa_completion_fill_floor, old_min_fill * self.research_kappa_completion_fill_mult)
            regime_params.min_fill_prob = min(old_min_fill, relaxed_min_fill)
            self._research_completion_relaxed_attempts += 1
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['scheduler_lane'] = lane
            record['normal_attempts_used'] = self._research_normal_quote_attempts
            record['normal_attempt_cap'] = self.research_normal_attempt_cap
            record['completion_attempts_used'] = self._research_completion_quote_attempts
            record['completion_attempt_cap'] = self.research_kappa_completion_attempt_cap
            record['completion_successes_used'] = self._research_completion_quote_successes
            record['completion_success_cap'] = self.research_kappa_completion_success_cap
            record['kappa_completion_candidate'] = completion_candidate
            record['kappa_completion_samples'] = completion_samples
            record['kappa_completion_target'] = self.research_kappa_completion_target
            record['kappa_completion_fill_relaxed'] = allow_relaxed_fill
            record['kappa_completion_min_fill_original'] = old_min_fill
            record['kappa_completion_min_fill_effective'] = float(regime_params.min_fill_prob)
        try:
            placed = self._bsimpl_2_Strategy1_Debug__place_skewed_quotes(response, state, book_id, book, profile, prediction, inventory, regime_params, size, edge_bias, stats=stats)
        finally:
            regime_params.min_fill_prob = old_min_fill
        if self._research_backfill_active and placed:
            self._research_quote_successes += 1
            if completion_candidate:
                self._research_completion_quote_successes += 1
            else:
                self._research_normal_quote_successes += 1
        if completion_candidate and placed:
            if allow_relaxed_fill:
                self._research_completion_relaxed_successes += 1
            if self.debug_enabled:
                self._book_record(book_id)['kappa_completion_quote_success'] = True
        return placed

    def _bsimpl_3_Strategy1_Research_build_mm_strategy_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime, collect_archetypes: bool=True) -> dict:
        self._sync_exchange_constraints(state)
        overlay = str(getattr(regime, 'scoring_overlay', '')).upper()
        bootstrap = self.research_inactive_bootstrap and overlay == 'SCORING_PRESSURE'
        self._research_bootstrap_active = bootstrap
        old_skip_inactive = self.mm_skip_inactive_tier
        old_maintenance_mult = self.maintenance_size_mult
        old_max_mm_books = self.max_mm_books_per_tick
        self._research_backfill_active = bool(self.research_candidate_backfill)
        self._research_quote_success_cap = int(old_max_mm_books)
        self._research_quote_successes = 0
        self._research_quote_attempts = 0
        self._research_normal_quote_attempts = 0
        self._research_normal_quote_successes = 0
        self._research_completion_relaxed_successes = 0
        self._research_completion_relaxed_attempts = 0
        self._research_completion_quote_attempts = 0
        self._research_completion_quote_successes = 0
        self._research_completion_attempt_cap_hits = 0
        self._research_completion_success_cap_hits = 0
        self._research_normal_attempt_cap_hits = 0
        self._research_dust_compact_ids_this_tick = self._select_dust_compaction_books(state)
        if self._research_backfill_active:
            profile_scan = len(getattr(selection, 'profiles', []) or [])
            self.max_mm_books_per_tick = max(old_max_mm_books, self.research_candidate_attempt_cap, profile_scan)
        try:
            if bootstrap:
                self.mm_skip_inactive_tier = False
            if bootstrap and self.research_bootstrap_maintenance_min_order and (self._research_exchange_min_order_size > 0.0):
                maintenance_base = float(getattr(self, 'maintenance_order_size', 0.0) or 0.0)
                min_size = self._research_exchange_min_order_size
                if maintenance_base > 0.0 and min_size <= float(self.max_inventory_base) + 1e-12:
                    required_mult = min_size / maintenance_base
                    if required_mult > self.maintenance_size_mult:
                        self.maintenance_size_mult = required_mult
            stats = self._bsimpl_2_Strategy1_Debug_build_mm_strategy_instructions(response, state, selection, predictions, regime, collect_archetypes=collect_archetypes)
            if isinstance(stats, dict):
                stats['research_bootstrap_active'] = bootstrap
                stats['research_stress_spread_bps'] = self._research_stress_spread_bps
                stats['research_toxic_spread_bps'] = self._research_toxic_spread_bps
                stats['research_min_order_size'] = self._research_exchange_min_order_size
                stats['research_round_trip_closes'] = self._research_round_trip_closes
                stats['research_position_opens'] = self._research_position_opens
                stats['research_dust_blocks'] = self._research_dust_blocks
                stats['research_parked_dust'] = len(self._research_parked_dust)
                stats['research_dust_entries'] = self._research_dust_entries
                stats['research_dust_releases'] = self._research_dust_releases
                stats['research_dust_heartbeats'] = self._research_dust_heartbeats
                stats['research_dust_compact_selected'] = len(self._research_dust_compact_ids_this_tick)
                stats['research_dust_compact_attempts'] = self._research_dust_compact_attempts
                stats['research_dust_compact_orders'] = self._research_dust_compact_orders
                stats['research_dust_compact_fills'] = self._research_dust_compact_fills
                stats['research_quote_attempts'] = self._research_quote_attempts
                stats['research_normal_quote_attempts'] = self._research_normal_quote_attempts
                stats['research_normal_quote_successes'] = self._research_normal_quote_successes
                stats['research_normal_attempt_cap'] = self.research_normal_attempt_cap
                stats['research_completion_quote_attempts'] = self._research_completion_quote_attempts
                stats['research_completion_quote_successes'] = self._research_completion_quote_successes
                stats['research_completion_attempt_cap'] = self.research_kappa_completion_attempt_cap
                stats['research_completion_success_cap'] = self.research_kappa_completion_success_cap
                stats['research_completion_relaxed_attempts'] = self._research_completion_relaxed_attempts
                stats['research_completion_relaxed_successes'] = self._research_completion_relaxed_successes
                stats['research_completion_attempt_cap_hits'] = self._research_completion_attempt_cap_hits
                stats['research_completion_success_cap_hits'] = self._research_completion_success_cap_hits
                stats['research_normal_attempt_cap_hits'] = self._research_normal_attempt_cap_hits
                stats['research_quote_successes'] = self._research_quote_successes
                stats['research_quote_success_cap'] = self._research_quote_success_cap
                stats['research_flat_epsilon'] = self._execution_flat_epsilon()
            return stats
        finally:
            self.mm_skip_inactive_tier = old_skip_inactive
            self.maintenance_size_mult = old_maintenance_mult
            self.max_mm_books_per_tick = old_max_mm_books
            self._research_bootstrap_active = False
            self._research_backfill_active = False

    def _bsimpl_3_Strategy1_Research__emit_book_decision(self, state, regime, book_id: int, book, profile, prediction, record: dict[str, Any]) -> None:
        bid = book.bids[0].price if getattr(book, 'bids', None) else None
        ask = book.asks[0].price if getattr(book, 'asks', None) else None
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        touch_spread_bps = None
        if mid and bid is not None and (ask is not None):
            touch_spread_bps = (ask - bid) / mid * 10000.0
        profile_spread_bps = self._profile_float(profile, 'spread_bps') if profile is not None else None
        mem = self._mem(book_id) if profile is not None else None
        reason = str(record.get('reason', DebugReason.NO_ACTION))
        if record.get('dust_quarantine') and reason in ('TOXIC_BOOK', 'TOXIC_REGIME'):
            reason = 'DUST_QUARANTINE'
        inactive_gate_bypassed = self.research_inactive_bootstrap and str(getattr(regime, 'scoring_overlay', '')).upper() == 'SCORING_PRESSURE'
        if reason == 'INACTIVE_TIER' and inactive_gate_bypassed and (not self.mm_skip_inactive_tier):
            reason = 'INACTIVE_DIAGNOSTIC_ONLY'
        self._debug_reason_counts[reason] += 1
        self._emit('DECISION', tick=self._tick, timestamp=getattr(state, 'timestamp', None), book_id=book_id, action=record.get('action', 'SKIP'), reason=reason, regime=getattr(regime, 'mode', None), overlay=getattr(regime, 'scoring_overlay', None), archetype=record.get('archetype'), archetype_source=record.get('archetype_source'), tier=getattr(profile, 'tier', None) if profile is not None else None, mid=mid, spread_bps=profile_spread_bps if profile_spread_bps is not None else touch_spread_bps, touch_spread_bps=touch_spread_bps, volatility=getattr(profile, 'volatility', None) if profile is not None else None, trade_rate=getattr(profile, 'trade_rate', None) if profile is not None else None, imbalance=getattr(profile, 'imbalance', None) if profile is not None else None, direction=getattr(prediction, 'direction', None) if prediction else None, signal=getattr(prediction, 'score', None) if prediction else None, expected_alpha=record.get('expected_alpha'), min_expected_alpha=self.min_expected_alpha, fill_buy=record.get('fill_buy'), fill_sell=record.get('fill_sell'), bid_px=record.get('bid_px'), ask_px=record.get('ask_px'), quantity=record.get('quantity'), expected_realized_pnl=record.get('expected_realized_pnl'), inventory=record.get('inventory'), instructions=record.get('instructions', 0), decision_ms=record.get('quote_ms', record.get('manage_ms')), loss_streak=record.get('loss_streak', getattr(mem, 'loss_streak', None) if mem is not None else None), recent_pnl=record.get('recent_pnl', getattr(mem, 'recent_pnl', None) if mem is not None else None), toxic_loss=record.get('toxic_loss'), toxic_pnl=record.get('toxic_pnl'), toxic_spread=record.get('toxic_spread'), toxic_archetype=record.get('toxic_archetype'), toxic_red_tier=record.get('toxic_red_tier'), stressed_by_spread=record.get('stressed_by_spread'), stressed_by_regime=record.get('stressed_by_regime'), legacy_stressed_by_regime=record.get('legacy_stressed_by_regime'), stress_spread_bps=record.get('stress_spread_bps', self._research_stress_spread_bps), toxic_spread_bps=record.get('toxic_spread_bps', self._research_toxic_spread_bps), min_order_size=record.get('min_order_size', self._research_exchange_min_order_size), dynamic_size_raw=record.get('dynamic_size_raw'), dynamic_size_final=record.get('dynamic_size_final'), size_promoted_to_min=record.get('size_promoted_to_min'), inactive_bootstrap=inactive_gate_bypassed, inactive_gate_bypassed=inactive_gate_bypassed and (not self.mm_skip_inactive_tier), dead_trade_rate_hit=record.get('dead_trade_rate_hit'), active_sparse=record.get('active_sparse'), active_sparse_tier=record.get('active_sparse_tier'), dust_quarantine=record.get('dust_quarantine'), dust_compact=record.get('dust_compact'), dust_compact_selected=record.get('dust_compact_selected'), scheduler_lane=record.get('scheduler_lane'), normal_attempts_used=record.get('normal_attempts_used'), normal_attempt_cap=record.get('normal_attempt_cap'), completion_attempts_used=record.get('completion_attempts_used'), completion_attempt_cap=record.get('completion_attempt_cap'), completion_successes_used=record.get('completion_successes_used'), completion_success_cap=record.get('completion_success_cap'), kappa_completion_candidate=record.get('kappa_completion_candidate'), kappa_completion_samples=record.get('kappa_completion_samples'), kappa_completion_target=record.get('kappa_completion_target'), kappa_completion_fill_relaxed=record.get('kappa_completion_fill_relaxed'), kappa_completion_min_fill_original=record.get('kappa_completion_min_fill_original'), kappa_completion_min_fill_effective=record.get('kappa_completion_min_fill_effective'), kappa_completion_quote_success=record.get('kappa_completion_quote_success'), toxic_pnl_raw=record.get('toxic_pnl_raw'), toxic_pnl_samples=record.get('toxic_pnl_samples'), aggressive_touch_gross_bps=record.get('aggressive_touch_gross_bps'), aggressive_touch_net_bps=record.get('aggressive_touch_net_bps'), bootstrap_inactive=record.get('bootstrap_inactive'), inventory_util=record.get('inventory_util'), dust_position=record.get('dust_position'))

    def _bsimpl_3_Strategy1_Research__emit(self, event_type: str, force: bool=False, **payload: Any) -> None:
        if not getattr(self, 'debug_enabled', True) and (not force):
            return
        if event_type == 'RUN_SUMMARY':
            payload.setdefault('research_round_trip_closes', getattr(self, '_research_round_trip_closes', 0))
            payload.setdefault('research_position_opens', getattr(self, '_research_position_opens', 0))
            payload.setdefault('research_position_reductions', getattr(self, '_research_position_reductions', 0))
            payload.setdefault('research_dust_blocks', getattr(self, '_research_dust_blocks', 0))
            payload.setdefault('research_parked_dust_positions', len(getattr(self, '_research_parked_dust', {})))
            payload.setdefault('research_dust_entries', getattr(self, '_research_dust_entries', 0))
            payload.setdefault('research_dust_releases', getattr(self, '_research_dust_releases', 0))
            payload.setdefault('research_dust_heartbeats', getattr(self, '_research_dust_heartbeats', 0))
            payload.setdefault('research_dust_compact_attempts', getattr(self, '_research_dust_compact_attempts', 0))
            payload.setdefault('research_dust_compact_orders', getattr(self, '_research_dust_compact_orders', 0))
            payload.setdefault('research_dust_compact_fills', getattr(self, '_research_dust_compact_fills', 0))
            payload.setdefault('research_normal_quote_attempts', getattr(self, '_research_normal_quote_attempts', 0))
            payload.setdefault('research_normal_quote_successes', getattr(self, '_research_normal_quote_successes', 0))
            payload.setdefault('research_normal_attempt_cap', getattr(self, 'research_normal_attempt_cap', 0))
            payload.setdefault('research_completion_quote_attempts', getattr(self, '_research_completion_quote_attempts', 0))
            payload.setdefault('research_completion_quote_successes', getattr(self, '_research_completion_quote_successes', 0))
            payload.setdefault('research_completion_attempt_cap', getattr(self, 'research_kappa_completion_attempt_cap', 0))
            payload.setdefault('research_completion_success_cap', getattr(self, 'research_kappa_completion_success_cap', 0))
            payload.setdefault('research_completion_relaxed_attempts', getattr(self, '_research_completion_relaxed_attempts', 0))
            payload.setdefault('research_completion_relaxed_successes', getattr(self, '_research_completion_relaxed_successes', 0))
            payload.setdefault('research_completion_attempt_cap_hits', getattr(self, '_research_completion_attempt_cap_hits', 0))
            payload.setdefault('research_completion_success_cap_hits', getattr(self, '_research_completion_success_cap_hits', 0))
            payload.setdefault('research_normal_attempt_cap_hits', getattr(self, '_research_normal_attempt_cap_hits', 0))
            obs_counts = getattr(self, '_research_realized_observations_by_book', {})
            target = int(getattr(self, 'research_kappa_completion_target', 3))
            payload.setdefault('research_realized_observation_total', sum(obs_counts.values()))
            payload.setdefault('research_kappa_books_with_obs', sum((1 for v in obs_counts.values() if v > 0)))
            payload.setdefault('research_kappa_books_pending_1', sum((1 for v in obs_counts.values() if v == 1)))
            payload.setdefault('research_kappa_books_pending_2', sum((1 for v in obs_counts.values() if v == 2)))
            payload.setdefault('research_kappa_books_eligible', sum((1 for v in obs_counts.values() if v >= target)))
            payload.setdefault('research_parked_dust_abs_base', sum((abs(float(info.get('net_base', 0.0))) for info in getattr(self, '_research_parked_dust', {}).values())))
            try:
                current_tick = int(getattr(self, '_tick', 0) or 0)
                dust_registry = getattr(self, '_research_parked_dust', {})
                payload.setdefault('research_oldest_dust_ticks', max((max(0, current_tick - int(info.get('first_tick', current_tick))) for info in dust_registry.values()), default=0))
                payload.setdefault('research_open_positions', sum((1 for bid in getattr(self, '_open_positions', {}) if abs(float(self._position_tracker_snapshot(bid).net_qty)) >= self._execution_flat_epsilon())))
                payload.setdefault('research_actionable_open_positions', sum((1 for bid in getattr(self, '_open_positions', {}) if bid not in dust_registry and abs(float(self._position_tracker_snapshot(bid).net_qty)) >= self._execution_flat_epsilon())))
            except Exception:
                pass
        try:
            safe = self._json_safe(payload)
        except Exception:
            safe = payload
        record = {'type': event_type, 'agent_id': getattr(self, 'uid', None), 'wall_time_ns': time.time_ns(), **safe}
        if not getattr(self, '_research_ready', False):
            self._research_early.append(record)
            return
        if getattr(self, 'research_enabled', False):
            self._enqueue(record)

    def _bsimpl_3_Strategy1_Research__enqueue(self, record: dict[str, Any]) -> None:
        if self._rq is None:
            return
        try:
            self._rq.put_nowait(record)
        except queue.Full:
            self._rdropped += 1

    def _bsimpl_3_Strategy1_Research__writer_loop(self) -> None:
        assert self._rq is not None and self._rstop is not None
        while not self._rstop.is_set() or not self._rq.empty():
            try:
                record = self._rq.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if self._rfile is not None:
                    self._rfile.write(json.dumps(record, separators=(',', ':'), sort_keys=True, default=str) + '\n')
                if self.research_console and self._console_allowed(record):
                    line = self._format_human(record)
                    if line:
                        print(line, flush=True)
            except Exception as exc:
                try:
                    print(f'[S1R_ERROR] stage=telemetry error={self._short(exc)}', flush=True)
                except Exception:
                    pass
            finally:
                self._rq.task_done()

    def _bsimpl_3_Strategy1_Research__console_allowed(self, r: dict[str, Any]) -> bool:
        typ = str(r.get('type', ''))
        if typ in {'ERROR', 'RUN_SUMMARY', 'RESEARCH_CONFIG', 'DEBUG_CONFIG', 'POSITION', 'POSITION_GUARD'}:
            return True
        if typ == 'ORDER_LIFECYCLE':
            phase = str(r.get('phase', '')).upper()
            if any((token in phase for token in ('TRADE', 'FILL', 'REJECT', 'FAIL'))):
                return True
        tick = self._int(r.get('tick'))
        if tick is not None and tick != 1 and (tick % self.research_every_n != 0):
            return False
        book = self._int(r.get('book_id'))
        if self.research_book_id >= 0 and book is not None:
            return book == self.research_book_id
        return True

    def _bsimpl_3_Strategy1_Research__format_human(self, r: dict[str, Any]) -> str | None:
        typ = str(r.get('type', ''))
        if typ == 'RESEARCH_CONFIG':
            return f"[S1R_CONFIG] enabled={int(bool(r.get('enabled')))} every_n={r.get('every_n')} book={r.get('book_filter')} jsonl={int(bool(r.get('jsonl')))} queue={r.get('queue_size')} policy={self._short(r.get('policy_version'))} fix_global_stress={int(bool(r.get('fix_global_stress')))} neutral_fallback={int(bool(r.get('neutral_fallback')))} adaptive_spread={int(bool(r.get('adaptive_spread_thresholds')))} inactive_bootstrap={int(bool(r.get('inactive_bootstrap')))} bootstrap_dead_as_mm={int(bool(r.get('bootstrap_dead_as_mm')))} fix_inv={int(bool(r.get('fix_inventory_util')))} fix_reservation={int(bool(r.get('fix_quote_reservation')))} manage_min_clip={int(bool(r.get('bootstrap_manage_min_clip')))} close_age_gate={r.get('bootstrap_force_close_ticks')} touch_gate={int(bool(r.get('aggressive_close_touch_gate')))} touch_buffer_bps={self._fmt(r.get('aggressive_close_fee_buffer_bps'))} touch_min_net_bps={self._fmt(r.get('aggressive_close_min_net_bps'))} backfill={int(bool(r.get('candidate_backfill')))} attempt_cap={r.get('candidate_attempt_cap')} toxic_samples={r.get('toxic_pnl_min_samples')} yellow_sparse={int(bool(r.get('yellow_sparse_active')))} green_sparse={int(bool(r.get('green_sparse_active')))} dust_safe={int(bool(r.get('dust_safe_close')))} dust_park={int(bool(r.get('dust_park_enabled')))} dust_hb={r.get('dust_heartbeat_ticks')} dust_compact={int(bool(r.get('dust_compact_enabled')))} dust_compact_frac={self._fmt(r.get('dust_compact_min_fraction'))} kappa_complete={int(bool(r.get('kappa_completion_enabled')))} kappa_target={r.get('kappa_completion_target')} kappa_bonus={self._fmt(r.get('kappa_completion_rank_bonus'))} kappa_fill_mult={self._fmt(r.get('kappa_completion_fill_mult'))} kappa_attempt_cap={r.get('kappa_completion_attempt_cap')} kappa_success_cap={r.get('kappa_completion_success_cap')} normal_attempt_cap={r.get('normal_attempt_cap')} min_order_sync={int(bool(r.get('sync_min_order')))} run_id={self._short(r.get('run_id'))} file={self._short(r.get('output_file'))}"
        if typ == 'DEBUG_CONFIG':
            return f"[S1R_CONFIG] debug_enabled={int(bool(r.get('enabled')))} debug_every_n={r.get('every_n')} debug_book={r.get('book_filter')}"
        if typ == 'REGIME':
            return f"[S1R_REGIME] tick={r.get('tick')} mode={self._short(r.get('mode'))} overlay={self._short(r.get('overlay'))} book_count={r.get('book_count')} active={r.get('active')} inactive={r.get('inactive')} spread_med={self._fmt(r.get('spread_med'))} spread_p90={self._fmt(r.get('spread_p90'))} spread_max={self._fmt(r.get('spread_max'))} stress_cut={self._fmt(r.get('stress_spread_bps'))} toxic_cut={self._fmt(r.get('toxic_spread_bps'))} vol_med={self._fmt(r.get('vol_med'))} vol_p90={self._fmt(r.get('vol_p90'))} trade_rate_med={self._fmt(r.get('trade_rate_med'))} liquid_ratio={self._fmt(r.get('liquid_ratio'))} low_trade_ratio={self._fmt(r.get('low_trade_ratio'))} stressed_ratio={self._fmt(r.get('stressed_ratio'))} trend_up_ratio={self._fmt(r.get('trend_up_ratio'))} trend_down_ratio={self._fmt(r.get('trend_down_ratio'))} min_order={self._fmt(r.get('min_order_size'))} trigger={self._short(r.get('trigger'))} threshold={self._short(r.get('threshold'))}"
        if typ == 'TIMING':
            return f"[S1R_REQ] tick={r.get('tick')} sim_ts={r.get('timestamp')} instructions={r.get('instructions', 0)} notices={r.get('notices', 0)} update_ms={self._fmt(r.get('update_ms'))} respond_ms={self._fmt(r.get('respond_ms'))} report_ms={self._fmt(r.get('report_ms'))} total_ms={self._fmt(r.get('total_ms'))}"
        if typ == 'DECISION':
            raw = str(r.get('reason', 'NO_ACTION'))
            reason = self.REASON_ALIAS.get(raw, raw)
            action = str(r.get('action', 'SKIP')).upper()
            inv = r.get('inventory') or {}
            common = f"tick={r.get('tick')} book={r.get('book_id')} regime={self._short(r.get('regime'))} overlay={self._short(r.get('overlay'))} archetype={self._short(r.get('archetype'))} arch_src={self._short(r.get('archetype_source'))} tier={self._short(r.get('tier'))} spread_bps={self._fmt(r.get('spread_bps'))} stress_cut={self._fmt(r.get('stress_spread_bps'))} toxic_cut={self._fmt(r.get('toxic_spread_bps'))} volatility={self._fmt(r.get('volatility'))} trade_rate={self._fmt(r.get('trade_rate'))} imbalance={self._fmt(r.get('imbalance'))} loss_streak={self._fmt(r.get('loss_streak'))} recent_pnl={self._fmt(r.get('recent_pnl'))} toxic_loss={self._fmt(r.get('toxic_loss'))} toxic_pnl={self._fmt(r.get('toxic_pnl'))} toxic_spread={self._fmt(r.get('toxic_spread'))} toxic_archetype={self._fmt(r.get('toxic_archetype'))} toxic_red_tier={self._fmt(r.get('toxic_red_tier'))} stressed_by_spread={self._fmt(r.get('stressed_by_spread'))} stressed_by_regime={self._fmt(r.get('stressed_by_regime'))} legacy_global_stress={self._fmt(r.get('legacy_stressed_by_regime'))} signal={self._fmt(r.get('signal'))} alpha={self._fmt(r.get('expected_alpha'))} min_alpha={self._fmt(r.get('min_expected_alpha'))} fill_bid={self._fmt(r.get('fill_buy'))} fill_ask={self._fmt(r.get('fill_sell'))} qty={self._fmt(r.get('quantity'))} dyn_raw={self._fmt(r.get('dynamic_size_raw'))} dyn_final={self._fmt(r.get('dynamic_size_final'))} min_order={self._fmt(r.get('min_order_size'))} promoted_min={self._fmt(r.get('size_promoted_to_min'))} bootstrap={self._fmt(r.get('inactive_bootstrap'))} inactive_bypass={self._fmt(r.get('inactive_gate_bypassed'))} dead_rate_hit={self._fmt(r.get('dead_trade_rate_hit'))} active_sparse={self._fmt(r.get('active_sparse'))} active_sparse_tier={self._short(r.get('active_sparse_tier'))} dust_quarantine={self._fmt(r.get('dust_quarantine'))} dust_compact={self._fmt(r.get('dust_compact'))} lane={self._fmt(r.get('scheduler_lane'))} normal_attempts={self._fmt(r.get('normal_attempts_used'))}/{self._fmt(r.get('normal_attempt_cap'))} completion_attempts={self._fmt(r.get('completion_attempts_used'))}/{self._fmt(r.get('completion_attempt_cap'))} kappa_complete={self._fmt(r.get('kappa_completion_candidate'))} kappa_samples={self._fmt(r.get('kappa_completion_samples'))} kappa_fill_relaxed={self._fmt(r.get('kappa_completion_fill_relaxed'))} toxic_pnl_samples={self._fmt(r.get('toxic_pnl_samples'))} touch_net_bps={self._fmt(r.get('aggressive_touch_net_bps'))} inv_util={self._fmt(r.get('inventory_util'))} dust={self._fmt(r.get('dust_position'))} exp_pnl={self._fmt(r.get('expected_realized_pnl'))} inv_base={self._fmt(inv.get('net_base'))} inv_band={self._short(inv.get('band'))} instructions={r.get('instructions', 0)}"
            if action == 'SKIP':
                return f'[S1R_SKIP] {common} side=BOTH reason={reason} raw_reason={raw}'
            return f"[S1R_QUOTE] {common} action={action} reason={reason} bid={self._fmt(r.get('bid_px'))} ask={self._fmt(r.get('ask_px'))} decision_ms={self._fmt(r.get('decision_ms'))}"
        if typ == 'POSITION':
            return f"[S1R_POSITION] tick={r.get('tick')} book={r.get('book_id')} transition={self._short(r.get('transition'))} net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))} realized_delta={self._fmt(r.get('realized_pnl_delta'))} round_trip={self._fmt(r.get('round_trip'))} total_round_trips={r.get('round_trip_total')} book_samples={r.get('round_trip_book_samples')} realized_obs={r.get('realized_book_observations')} flat_eps={self._fmt(r.get('execution_flat_epsilon'))} reason={self._short(r.get('reason'))}"
        if typ == 'POSITION_GUARD':
            return f"[S1R_DUST] tick={r.get('tick')} book={r.get('book_id')} net_base={self._fmt(r.get('net_base'))} min_order={self._fmt(r.get('min_order_size'))} age_ticks={self._fmt(r.get('age_ticks'))} parked={self._fmt(r.get('parked'))} stale={self._fmt(r.get('stale'))} reason={self._short(r.get('reason'))}"
        if typ == 'ORDER_LIFECYCLE':
            phase = str(r.get('phase', 'UNKNOWN')).upper()
            book = r.get('book_id')
            if phase == 'SUBMITTED':
                p = r.get('instruction') or {}
                return f"[S1R_ORDER] tick={r.get('tick')} book={book} side={self._side(self._pick(p, 'direction', 'side'))} type={self._short(self._pick(p, 'orderType', 'order_type', 'type'))} price={self._fmt(self._pick(p, 'price', 'limitPrice', 'limit_price'))} qty={self._fmt(self._pick(p, 'quantity', 'qty', 'size'))} tif={self._short(self._pick(p, 'timeInForce', 'time_in_force', 'tif'))} client_id={self._short(self._pick(p, 'clientOrderId', 'client_order_id'))} index={r.get('instruction_index')}"
            e = r.get('event') or {}
            if 'TRADE' in phase or 'FILL' in phase:
                return f"[S1R_FILL] tick={r.get('tick')} book={book} phase={phase} side={self._side(self._pick(e, 'direction', 'side'))} price={self._fmt(self._pick(e, 'price', 'tradePrice', 'trade_price'))} qty={self._fmt(self._pick(e, 'quantity', 'qty', 'size'))} client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))} net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))}"
            if 'REJECT' in phase or 'FAIL' in phase:
                return f"[S1R_REJECT] tick={r.get('tick')} book={book} phase={phase} reason={self._short(self._pick(e, 'reason', 'message', 'status', 'error'), 240)} client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}"
            if 'CANCEL' in phase or 'EXPIRE' in phase:
                return f"[S1R_CANCEL] tick={r.get('tick')} book={book} phase={phase} reason={self._short(self._pick(e, 'reason', 'message', 'status'))} client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}"
            return f"[S1R_NOTICE] tick={r.get('tick')} book={book} phase={phase}"
        if typ == 'RUN_SUMMARY':
            avg = r.get('average_latency_ms') or {}
            mx = r.get('max_latency_ms') or {}
            return f"[S1R_SUMMARY] tick={r.get('tick')} responses={r.get('responses')} top_skips={self._counts(r.get('reason_counts') or {}, 8)} events={self._counts(r.get('event_counts') or {}, 8)} avg_total_ms={self._fmt(avg.get('total_ms'))} max_total_ms={self._fmt(mx.get('total_ms'))} opens={r.get('research_position_opens', 0)} reductions={r.get('research_position_reductions', 0)} round_trips={r.get('research_round_trip_closes', 0)} open_positions={r.get('research_open_positions', 0)} actionable_open={r.get('research_actionable_open_positions', 0)} parked_dust={r.get('research_parked_dust_positions', 0)} parked_dust_base={self._fmt(r.get('research_parked_dust_abs_base'))} dust_entries={r.get('research_dust_entries', 0)} dust_releases={r.get('research_dust_releases', 0)} dust_compact_orders={r.get('research_dust_compact_orders', 0)} dust_compact_fills={r.get('research_dust_compact_fills', 0)} oldest_dust_ticks={r.get('research_oldest_dust_ticks', 0)} kappa_eligible={r.get('research_kappa_books_eligible', 0)} kappa_pending1={r.get('research_kappa_books_pending_1', 0)} kappa_pending2={r.get('research_kappa_books_pending_2', 0)} normal_lane={r.get('research_normal_quote_attempts', 0)}/{r.get('research_normal_attempt_cap', 0)} completion_lane={r.get('research_completion_quote_attempts', 0)}/{r.get('research_completion_attempt_cap', 0)} completion_success={r.get('research_completion_quote_successes', 0)}/{r.get('research_completion_success_cap', 0)} dust_blocks={r.get('research_dust_blocks', 0)} queue_dropped={self._rdropped}"
        if typ == 'ERROR':
            return f"[S1R_ERROR] tick={r.get('tick')} stage={self._short(r.get('stage'))} type={self._short(r.get('error_type'))} error={self._short(r.get('error'), 400)}"
        return None

    def _bsimpl_3_Strategy1_Research__shutdown_research(self) -> None:
        if self._rstop is not None:
            self._rstop.set()
        if self._rq is not None:
            deadline = time.time() + 1.5
            while self._rq.unfinished_tasks and time.time() < deadline:
                time.sleep(0.01)
        if self._rworker is not None and self._rworker.is_alive():
            self._rworker.join(timeout=0.5)
        if self._rfile is not None:
            try:
                self._rfile.flush()
                self._rfile.close()
            except OSError:
                pass
            self._rfile = None

    @staticmethod
    def _bsimpl_3_Strategy1_Research__pick(obj: Any, *names: str) -> Any:
        if obj is None:
            return None
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    @staticmethod
    def _bsimpl_3_Strategy1_Research__int(v: Any) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bsimpl_3_Strategy1_Research__fmt(v: Any) -> str:
        if v is None:
            return '-'
        if isinstance(v, bool):
            return '1' if v else '0'
        try:
            x = float(v)
        except (TypeError, ValueError):
            return str(v).replace(' ', '_')
        if abs(x) >= 1000:
            return f'{x:.3f}'
        if abs(x) >= 1:
            return f'{x:.6f}'.rstrip('0').rstrip('.')
        return f'{x:.8f}'.rstrip('0').rstrip('.') or '0'

    @staticmethod
    def _bsimpl_3_Strategy1_Research__short(v: Any, n: int=120) -> str:
        if v is None:
            return '-'
        return '_'.join(str(v).replace('\n', ' ').replace('\r', ' ').split())[:n]

    @classmethod
    def _bsimpl_3_Strategy1_Research__side(cls, v: Any) -> str:
        if v is None:
            return '-'
        s = str(v).upper()
        if 'BUY' in s or s == 'BID':
            return 'BID'
        if 'SELL' in s or s == 'ASK':
            return 'ASK'
        return cls._short(v)

    @staticmethod
    def _bsimpl_3_Strategy1_Research__counts(d: dict[str, Any], n: int) -> str:
        try:
            items = sorted(((str(k), int(v)) for k, v in d.items()), key=lambda kv: (-kv[1], kv[0]))[:n]
            return ','.join((f'{k}:{v}' for k, v in items)) or '-'
        except Exception:
            return '-'
    _accumulate_tuning_window = _bsimpl_1_Strategy1__accumulate_tuning_window
    _aggregate_book_memory_win_rate = _bsimpl_1_Strategy1__aggregate_book_memory_win_rate
    _allows_aggressive_close = _bsimpl_3_Strategy1_Research__allows_aggressive_close
    _alpha_regime_allows = _bsimpl_0_DetailedTemplateAgent__alpha_regime_allows
    _append_kappa_csv = _bsimpl_0_DetailedTemplateAgent__append_kappa_csv
    _append_pnl_csv = _bsimpl_0_DetailedTemplateAgent__append_pnl_csv
    _apply_tuning_overrides = _bsimpl_1_Strategy1__apply_tuning_overrides
    _apply_tuning_rules = _bsimpl_1_Strategy1__apply_tuning_rules
    _as_bool = _bsimpl_2_Strategy1_Debug__as_bool
    _assign_book_tier = _bsimpl_0_DetailedTemplateAgent__assign_book_tier
    _book_has_open_lots = _bsimpl_0_DetailedTemplateAgent__book_has_open_lots
    _book_matches = _bsimpl_2_Strategy1_Debug__book_matches
    _book_mid = _bsimpl_0_DetailedTemplateAgent__book_mid
    _book_record = _bsimpl_2_Strategy1_Debug__book_record
    _book_volatility = _bsimpl_0_DetailedTemplateAgent__book_volatility
    _can_add_volume = _bsimpl_0_DetailedTemplateAgent__can_add_volume
    _clamp_tuning_params = _bsimpl_1_Strategy1__clamp_tuning_params
    _clear_position_state = _bsimpl_1_Strategy1__clear_position_state
    _close_debug_file = _bsimpl_2_Strategy1_Debug__close_debug_file
    _complete_trading_reason = _bsimpl_2_Strategy1_Debug__complete_trading_reason
    _completion_observation_count = _bsimpl_3_Strategy1_Research__completion_observation_count
    _compute_alpha_rank = _bsimpl_0_DetailedTemplateAgent__compute_alpha_rank
    _compute_close_score = _bsimpl_3_Strategy1_Research__compute_close_score
    _compute_flow_f = _bsimpl_0_DetailedTemplateAgent__compute_flow_f
    _compute_l2_l5_imbalance = _bsimpl_1_Strategy1__compute_l2_l5_imbalance
    _compute_local_kappa = _bsimpl_0_DetailedTemplateAgent__compute_local_kappa
    _compute_trade_t = _bsimpl_0_DetailedTemplateAgent__compute_trade_t
    _compute_tuning_metrics = _bsimpl_1_Strategy1__compute_tuning_metrics
    _console_allowed = _bsimpl_3_Strategy1_Research__console_allowed
    _copy_positions_for_book = _bsimpl_0_DetailedTemplateAgent__copy_positions_for_book
    _count_book_instructions = _bsimpl_0_DetailedTemplateAgent__count_book_instructions
    _counts = _bsimpl_3_Strategy1_Research__counts
    _diagnose_quote_setup = _bsimpl_2_Strategy1_Debug__diagnose_quote_setup
    _dispatch_notice_event = _bsimpl_0_DetailedTemplateAgent__dispatch_notice_event
    _dust_compaction_safe_for_any_fill = _bsimpl_3_Strategy1_Research__dust_compaction_safe_for_any_fill
    _dust_fill_matches_recent_compaction = _bsimpl_3_Strategy1_Research__dust_fill_matches_recent_compaction
    _elapsed_ms = _bsimpl_2_Strategy1_Debug__elapsed_ms
    _emit = _bsimpl_3_Strategy1_Research__emit
    _emit_book_decision = _bsimpl_3_Strategy1_Research__emit_book_decision
    _emit_run_summary = _bsimpl_2_Strategy1_Debug__emit_run_summary
    _enqueue = _bsimpl_3_Strategy1_Research__enqueue
    _env_bool = _bsimpl_2_Strategy1_Debug__env_bool
    _env_int = _bsimpl_2_Strategy1_Debug__env_int
    _estimate_local_normalized_median = _bsimpl_0_DetailedTemplateAgent__estimate_local_normalized_median
    _estimate_plan_for_book = _bsimpl_0_DetailedTemplateAgent__estimate_plan_for_book
    _estimate_trade_fee = _bsimpl_0_DetailedTemplateAgent__estimate_trade_fee
    _event_payload = _bsimpl_2_Strategy1_Debug__event_payload
    _execute_aggressive_close = _bsimpl_3_Strategy1_Research__execute_aggressive_close
    _execution_flat_epsilon = _bsimpl_3_Strategy1_Research__execution_flat_epsilon
    _fast_update = _bsimpl_0_DetailedTemplateAgent__fast_update
    _finalize_book_decisions = _bsimpl_2_Strategy1_Debug__finalize_book_decisions
    _fmt = _bsimpl_3_Strategy1_Research__fmt
    _format_human = _bsimpl_3_Strategy1_Research__format_human
    _get = _bsimpl_2_Strategy1_Debug__get
    _global_book_rank = _bsimpl_3_Strategy1_Research__global_book_rank
    _instruction_counts_by_book = _bsimpl_2_Strategy1_Debug__instruction_counts_by_book
    _instruction_payload = _bsimpl_2_Strategy1_Debug__instruction_payload
    _int = _bsimpl_3_Strategy1_Research__int
    _inventory_needs_management = _bsimpl_3_Strategy1_Research__inventory_needs_management
    _inventory_record_needs_management = _bsimpl_2_Strategy1_Debug__inventory_record_needs_management
    _inventory_urgency = _bsimpl_1_Strategy1__inventory_urgency
    _inventory_util = _bsimpl_3_Strategy1_Research__inventory_util
    _is_compactable_dust = _bsimpl_3_Strategy1_Research__is_compactable_dust
    _is_dust_qty = _bsimpl_3_Strategy1_Research__is_dust_qty
    _is_kappa_completion_candidate = _bsimpl_3_Strategy1_Research__is_kappa_completion_candidate
    _json_safe = _bsimpl_2_Strategy1_Debug__json_safe
    _kappa_factor_from_tier = _bsimpl_1_Strategy1__kappa_factor_from_tier
    _learned_side_fill_prob = _bsimpl_1_Strategy1__learned_side_fill_prob
    _log_book_memory_sample = _bsimpl_1_Strategy1__log_book_memory_sample
    _log_book_profile_selection = _bsimpl_0_DetailedTemplateAgent__log_book_profile_selection
    _log_direction_predictions = _bsimpl_0_DetailedTemplateAgent__log_direction_predictions
    _log_input = _bsimpl_0_DetailedTemplateAgent__log_input
    _log_kappa_and_sequences = _bsimpl_0_DetailedTemplateAgent__log_kappa_and_sequences
    _log_kappa_strategy_calibration = _bsimpl_0_DetailedTemplateAgent__log_kappa_strategy_calibration
    _log_market_regime = _bsimpl_0_DetailedTemplateAgent__log_market_regime
    _log_mm_strategy = _bsimpl_1_Strategy1__log_mm_strategy
    _log_momentum_and_pnl = _bsimpl_0_DetailedTemplateAgent__log_momentum_and_pnl
    _log_notices = _bsimpl_2_Strategy1_Debug__log_notices
    _log_output = _bsimpl_0_DetailedTemplateAgent__log_output
    _log_predict_pnl = _bsimpl_0_DetailedTemplateAgent__log_predict_pnl
    _log_submitted_instructions = _bsimpl_2_Strategy1_Debug__log_submitted_instructions
    _maintenance_allowed = _bsimpl_1_Strategy1__maintenance_allowed
    _manage_inventory = _bsimpl_3_Strategy1_Research__manage_inventory
    _match_trade_fifo = _bsimpl_0_DetailedTemplateAgent__match_trade_fifo
    _maybe_run_tuning_scheduler = _bsimpl_1_Strategy1__maybe_run_tuning_scheduler
    _mem = _bsimpl_3_Strategy1_Research__mem
    _net_inventory = _bsimpl_3_Strategy1_Research__net_inventory
    _normalize_momentum = _bsimpl_0_DetailedTemplateAgent__normalize_momentum
    _object_payload = _bsimpl_2_Strategy1_Debug__object_payload
    _passes_expected_pnl_gate = _bsimpl_1_Strategy1__passes_expected_pnl_gate
    _passes_fee_gate = _bsimpl_0_DetailedTemplateAgent__passes_fee_gate
    _percentile = _bsimpl_3_Strategy1_Research__percentile
    _persist_tuning_state = _bsimpl_1_Strategy1__persist_tuning_state
    _pick = _bsimpl_3_Strategy1_Research__pick
    _place_directional_round_trip = _bsimpl_2_Strategy1_Debug__place_directional_round_trip
    _place_passive_inventory_exit = _bsimpl_1_Strategy1__place_passive_inventory_exit
    _place_round_trip_limits = _bsimpl_0_DetailedTemplateAgent__place_round_trip_limits
    _place_skewed_quotes = _bsimpl_3_Strategy1_Research__place_skewed_quotes
    _pnl_observation_count = _bsimpl_0_DetailedTemplateAgent__pnl_observation_count
    _position_tracker_snapshot = _bsimpl_1_Strategy1__position_tracker_snapshot
    _predict_all_books = _bsimpl_2_Strategy1_Debug__predict_all_books
    _prefer_maker = _bsimpl_0_DetailedTemplateAgent__prefer_maker
    _profile_float = _bsimpl_3_Strategy1_Research__profile_float
    _prune_pnl_history = _bsimpl_0_DetailedTemplateAgent__prune_pnl_history
    _realized_pnl_lookback = _bsimpl_0_DetailedTemplateAgent__realized_pnl_lookback
    _realized_pnl_sequences_per_book = _bsimpl_0_DetailedTemplateAgent__realized_pnl_sequences_per_book
    _reason_from_client_id = _bsimpl_1_Strategy1__reason_from_client_id
    _record_fill_hit = _bsimpl_1_Strategy1__record_fill_hit
    _record_fill_quote = _bsimpl_1_Strategy1__record_fill_quote
    _record_latency = _bsimpl_2_Strategy1_Debug__record_latency
    _refresh_dust_state = _bsimpl_3_Strategy1_Research__refresh_dust_state
    _reload_tuning_config_if_changed = _bsimpl_1_Strategy1__reload_tuning_config_if_changed
    _reset_pnl_state = _bsimpl_1_Strategy1__reset_pnl_state
    _round_order_size = _bsimpl_0_DetailedTemplateAgent__round_order_size
    _schedule_maintenance_books = _bsimpl_1_Strategy1__schedule_maintenance_books
    _select_dust_compaction_books = _bsimpl_3_Strategy1_Research__select_dust_compaction_books
    _short = _bsimpl_3_Strategy1_Research__short
    _should_emit_tick = _bsimpl_2_Strategy1_Debug__should_emit_tick
    _shutdown_research = _bsimpl_3_Strategy1_Research__shutdown_research
    _side = _bsimpl_3_Strategy1_Research__side
    _simulate_fifo_pnl = _bsimpl_0_DetailedTemplateAgent__simulate_fifo_pnl
    _snapshot_tuning_params = _bsimpl_1_Strategy1__snapshot_tuning_params
    _sparse_active_tier_enabled = _bsimpl_3_Strategy1_Research__sparse_active_tier_enabled
    _spread_bps = _bsimpl_0_DetailedTemplateAgent__spread_bps
    _spread_dist_bucket = _bsimpl_1_Strategy1__spread_dist_bucket
    _summarize_pnl_history = _bsimpl_0_DetailedTemplateAgent__summarize_pnl_history
    _sync_exchange_constraints = _bsimpl_3_Strategy1_Research__sync_exchange_constraints
    _sync_kappa_factor = _bsimpl_1_Strategy1__sync_kappa_factor
    _tier_mm_boost = _bsimpl_1_Strategy1__tier_mm_boost
    _timed = _bsimpl_2_Strategy1_Debug__timed
    _total_traded_volume = _bsimpl_0_DetailedTemplateAgent__total_traded_volume
    _trade_persistence = _bsimpl_1_Strategy1__trade_persistence
    _truncate_pnl_sequences = _bsimpl_0_DetailedTemplateAgent__truncate_pnl_sequences
    _try_close_loans = _bsimpl_1_Strategy1__try_close_loans
    _update_book_specialization = _bsimpl_1_Strategy1__update_book_specialization
    _update_direction_accuracy = _bsimpl_1_Strategy1__update_direction_accuracy
    _update_momentum = _bsimpl_0_DetailedTemplateAgent__update_momentum
    _update_spread_thresholds = _bsimpl_3_Strategy1_Research__update_spread_thresholds
    _update_trade_sign_history = _bsimpl_1_Strategy1__update_trade_sign_history
    _volume_cap_quote = _bsimpl_0_DetailedTemplateAgent__volume_cap_quote
    _volume_cap_remaining = _bsimpl_0_DetailedTemplateAgent__volume_cap_remaining
    _wealth_per_book = _bsimpl_1_Strategy1__wealth_per_book
    _writer_loop = _bsimpl_3_Strategy1_Research__writer_loop
    build_all_book_profiles = _bsimpl_0_DetailedTemplateAgent_build_all_book_profiles
    build_book_profile = _bsimpl_0_DetailedTemplateAgent_build_book_profile
    build_demo_instructions = _bsimpl_0_DetailedTemplateAgent_build_demo_instructions
    build_kappa_strategy_instructions = _bsimpl_0_DetailedTemplateAgent_build_kappa_strategy_instructions
    build_mm_strategy_instructions = _bsimpl_3_Strategy1_Research_build_mm_strategy_instructions
    classify_book_archetype = _bsimpl_3_Strategy1_Research_classify_book_archetype
    classify_market_regime = _bsimpl_0_DetailedTemplateAgent_classify_market_regime
    classify_market_regime_from_profiles = _bsimpl_3_Strategy1_Research_classify_market_regime_from_profiles
    coverage_priority = _bsimpl_1_Strategy1_coverage_priority
    dynamic_order_size = _bsimpl_3_Strategy1_Research_dynamic_order_size
    estimate_fill_probability = _bsimpl_1_Strategy1_estimate_fill_probability
    estimate_realized_pnl = _bsimpl_0_DetailedTemplateAgent_estimate_realized_pnl
    estimate_round_trip_pnl = _bsimpl_0_DetailedTemplateAgent_estimate_round_trip_pnl
    expected_alpha_score = _bsimpl_2_Strategy1_Debug_expected_alpha_score
    get_archetype_edge_bias = _bsimpl_1_Strategy1_get_archetype_edge_bias
    get_regime_params = _bsimpl_3_Strategy1_Research_get_regime_params
    handle = _bsimpl_2_Strategy1_Debug_handle
    initialize = _bsimpl_3_Strategy1_Research_initialize
    is_toxic_book = _bsimpl_3_Strategy1_Research_is_toxic_book
    merge_regime_and_archetype_params = _bsimpl_1_Strategy1_merge_regime_and_archetype_params
    microprice_signal = _bsimpl_1_Strategy1_microprice_signal
    onStart = _bsimpl_0_DetailedTemplateAgent_onStart
    onTrade = _bsimpl_3_Strategy1_Research_onTrade
    parse_account = _bsimpl_0_DetailedTemplateAgent_parse_account
    parse_book = _bsimpl_0_DetailedTemplateAgent_parse_book
    parse_notices = _bsimpl_0_DetailedTemplateAgent_parse_notices
    parse_response = _bsimpl_0_DetailedTemplateAgent_parse_response
    parse_state = _bsimpl_0_DetailedTemplateAgent_parse_state
    predict_direction = _bsimpl_1_Strategy1_predict_direction
    rank_books_for_trading = _bsimpl_0_DetailedTemplateAgent_rank_books_for_trading
    report = _bsimpl_0_DetailedTemplateAgent_report
    respond = _bsimpl_2_Strategy1_Debug_respond
    select_books_for_trading = _bsimpl_2_Strategy1_Debug_select_books_for_trading
    skewed_quote_prices = _bsimpl_3_Strategy1_Research_skewed_quote_prices
    update = _bsimpl_0_DetailedTemplateAgent_update
if __name__ == '__main__':
    launch(BaseStrategy)
