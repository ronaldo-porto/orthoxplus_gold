# SPDX-FileCopyrightText: 2026
# SPDX-License-Identifier: MIT
"""
Strategy4 — risk-constrained adaptive market making for
TAOS / MVTRX subnet 79.

This agent is designed to live next to Strategy1.py and DetailedTemplateAgent.py.
It keeps Strategy1's regime, profile, FIFO/VWAP, Kappa, maintenance, and reporting
infrastructure, while replacing the unsafe quote core with:

* L3-informed fair value (Strategy1 direction forecast)
* Avellaneda–Stoikov-style inventory-adjusted reservation price
* GLFT-inspired liquidity/risk spread adjustment
* side-specific expected-value gates
* side-specific inventory and signal sizing
* deterministic hard risk modes
* optional constrained contextual-UCB adaptation (Alpha-AS layer)
* exact active-order metadata and delayed post-fill markout learning
* July 2026 soft-floor awareness: local 0.79*Kappa+0.21*PnL proxy,
  floor-aware UCB rewards, earlier REDUCE_ONLY/LIQUIDATE on weak/left-tail books

The implementation intentionally constrains the adaptive layer to a small set of
safe multipliers. The policy never directly emits arbitrary prices or quantities.
Hard inventory, balance, toxicity, latency, stop-loss, and soft-floor rules always
have final control.

Launch example:

    python taos/im/neurons/miner.py \
      --netuid 79 \
      --agent.path agents \
      --agent.name Strategy4 \
      --agent.params enable_mm_strategy=1 alpha_policy_mode=deterministic \
          enable_phase_mining=0 max_mm_books_per_tick=8 \
          max_inventory_base=1.4 mm_base_size=0.25 verbose_log=0

For online contextual learning after offline/local validation:

    --agent.params alpha_policy_mode=ucb policy_exploration=0.20

Important: deploy only after multi-seed local races. No market-making model has a
universal performance guarantee across validator configurations.
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
    DirectionForecast,
    MarketRegime,
)
from Strategy1 import (
    ALPHA_CLIENT_ID_BASE,
    MAINT_CLIENT_ID_BASE,
    MM_CLIENT_ID_BASE,
    BookArchetype,
    BookMemory,
    FillProbabilityEstimate,
    InventorySnapshot,
    PositionTracker,
    RegimeParamSet,
    Strategy1,
)
from rolling_scoring import (
    RollingDiagnosticsSnapshot,
    RollingScoringConfig,
    compute_rolling_diagnostics,
    percentile as rolling_percentile,
    soft_floor_factor as rolling_soft_floor_factor,
)

RiskMode = Literal[
    "NORMAL",
    "CAUTIOUS",
    "REDUCE_ONLY",
    "LIQUIDATE",
    "DISABLED",
]
MiningPhase = Literal["OBSERVE", "CALIBRATE", "ACTIVE", "COOLDOWN", "RESUME"]
Side = Literal["buy", "sell"]
PolicyMode = Literal["deterministic", "ucb", "off"]


@dataclass(frozen=True)
class SafePolicyAction:
    """Bounded Alpha-AS action; never emits raw prices or quantities."""

    action_id: int
    name: str
    gamma_mult: float
    spread_mult: float
    alpha_mult: float
    size_mult: float
    expiry_mult: float


@dataclass
class QuoteDecision:
    fair_price: float
    reservation_price: float
    bid_price: float
    ask_price: float
    half_spread: float
    gamma: float
    toxicity: float
    risk_mode: RiskMode
    action: SafePolicyAction
    context_key: str


@dataclass
class ActiveOrderMeta:
    book_id: int
    client_order_id: int
    side: Side
    price: float
    submitted_qty: float
    submitted_ts: int
    distance_from_touch: float
    distance_bucket: int
    action_id: int
    context_key: str
    fair_price: float
    reservation_price: float
    inventory_before: float
    expected_value_bps: float
    counted_fill: bool = False
    filled_qty: float = 0.0
    was_weak: bool = False
    was_left_tail: bool = False


@dataclass
class PendingMarkout:
    book_id: int
    side: Side
    fill_price: float
    fill_qty: float
    fill_ts: int
    mature_ts: int
    action_id: int
    context_key: str
    inventory_before: float = 0.0
    was_weak: bool = False
    was_left_tail: bool = False


@dataclass
class AdaptiveBookMemory(BookMemory):
    """BookMemory with order-level fill and adverse-selection learning."""

    filled_order_count: int = 0
    submitted_qty: float = 0.0
    filled_qty: float = 0.0
    last_quote_ts: int = 0
    buy_markout_ema_bps: float = 0.0
    sell_markout_ema_bps: float = 0.0
    markout_samples: int = 0
    quote_rejections: int = 0

    @property
    def fill_rate(self) -> float:
        return max(0.0, min(1.0, self.filled_order_count / max(self.quote_count, 1)))

    @property
    def quantity_fill_rate(self) -> float:
        return max(0.0, min(1.0, self.filled_qty / max(self.submitted_qty, 1e-12)))


SAFE_POLICY_ACTIONS: tuple[SafePolicyAction, ...] = (
    SafePolicyAction(0, "defensive", 1.60, 1.35, 0.65, 0.55, 0.65),
    SafePolicyAction(1, "balanced", 1.00, 1.00, 1.00, 1.00, 1.00),
    SafePolicyAction(2, "alpha", 0.95, 1.02, 1.25, 0.85, 0.80),
    SafePolicyAction(3, "liquid", 0.85, 0.88, 0.85, 1.15, 0.90),
    SafePolicyAction(4, "toxic", 1.90, 1.55, 0.45, 0.35, 0.50),
)


class Strategy4(Strategy1):
    """Constrained Alpha-AS/GLFT dealer driven by TAOS L3 features."""

    def initialize(self) -> None:
        super().initialize()
        cfg = self.config

        # Reservation-price / fair-value controls.
        self.base_risk_aversion = float(getattr(cfg, "base_risk_aversion", 0.85))
        self.min_risk_aversion = float(getattr(cfg, "min_risk_aversion", 0.20))
        self.max_risk_aversion = float(getattr(cfg, "max_risk_aversion", 3.00))
        self.alpha_shift_spreads = float(getattr(cfg, "alpha_shift_spreads", 0.32))
        self.inventory_shift_spreads = float(
            getattr(cfg, "inventory_shift_spreads", 0.55)
        )
        self.max_fair_shift_spreads = float(
            getattr(cfg, "max_fair_shift_spreads", 0.75)
        )

        # Practical GLFT-inspired spread controls.
        self.glft_spread_weight = float(getattr(cfg, "glft_spread_weight", 0.12))
        self.glft_kappa_floor = float(getattr(cfg, "glft_kappa_floor", 0.20))
        self.glft_kappa_cap = float(getattr(cfg, "glft_kappa_cap", 4.00))
        self.vol_spread_weight = float(getattr(cfg, "vol_spread_weight", 0.20))
        self.toxicity_spread_weight = float(
            getattr(cfg, "toxicity_spread_weight", 0.35)
        )
        self.inventory_spread_weight = float(
            getattr(cfg, "inventory_spread_weight", 0.25)
        )
        self.min_half_spread_bps = float(getattr(cfg, "min_half_spread_bps", 0.15))
        self.max_half_spread_bps = float(getattr(cfg, "max_half_spread_bps", 25.0))

        # Activity / coverage overrides (S1 defaults are too selective for live
        # volume bootstrap — Strategy4 re-homes these after super().initialize).
        self.max_mm_books_per_tick = max(
            1, int(getattr(cfg, "max_mm_books_per_tick", 8))
        )
        self.max_managed_books_per_tick = max(
            1,
            int(
                getattr(
                    cfg,
                    "max_managed_books_per_tick",
                    max(self.max_mm_books_per_tick, 8),
                )
            ),
        )
        self.min_expected_alpha = float(getattr(cfg, "min_expected_alpha", 0.12))
        self.min_economic_signal = float(getattr(cfg, "min_economic_signal", 0.05))

        # Side-specific EV and markout controls.
        self.fee_buffer_bps = float(getattr(cfg, "fee_buffer_bps", 0.10))
        self.min_side_edge_bps = float(getattr(cfg, "min_side_edge_bps", 0.03))
        self.adverse_selection_weight = float(
            getattr(cfg, "adverse_selection_weight", 1.00)
        )
        self.unseen_markout_buffer_bps = float(
            getattr(cfg, "unseen_markout_buffer_bps", 0.20)
        )
        self.markout_horizon_ns = int(
            getattr(cfg, "markout_horizon_ns", 2_000_000_000)
        )
        self.markout_ema_alpha = float(getattr(cfg, "markout_ema_alpha", 0.12))
        self.max_pending_markouts = max(
            100, int(getattr(cfg, "max_pending_markouts", 10_000))
        )

        # Inventory and hard survival controls.
        self.cautious_inventory_util = float(
            getattr(cfg, "cautious_inventory_util", 0.55)
        )
        self.reduce_only_inventory_util = float(
            getattr(cfg, "reduce_only_inventory_util", 0.80)
        )
        self.liquidate_inventory_util = float(
            getattr(cfg, "liquidate_inventory_util", 0.98)
        )
        self.hard_stop_loss_bps = float(getattr(cfg, "hard_stop_loss_bps", 55.0))
        self.cautious_loss_streak = max(
            1, int(getattr(cfg, "cautious_loss_streak", 3))
        )
        self.reduce_only_loss_streak = max(
            self.cautious_loss_streak,
            int(getattr(cfg, "reduce_only_loss_streak", 5)),
        )
        self.size_inventory_skew = float(getattr(cfg, "size_inventory_skew", 1.35))
        self.max_side_size_mult = float(getattr(cfg, "max_side_size_mult", 1.50))
        self.min_side_size_mult = float(getattr(cfg, "min_side_size_mult", 0.10))
        self.cautious_size_mult = float(getattr(cfg, "cautious_size_mult", 0.70))

        # Toxicity / latency controls.
        self.toxicity_cautious_threshold = float(
            getattr(cfg, "toxicity_cautious_threshold", 0.65)
        )
        self.toxicity_reduce_threshold = float(
            getattr(cfg, "toxicity_reduce_threshold", 0.85)
        )
        self.latency_cautious_ms = float(getattr(cfg, "latency_cautious_ms", 250.0))
        self.latency_disable_ms = float(getattr(cfg, "latency_disable_ms", 800.0))
        self._last_response_latency_ms = 0.0

        # Alpha-AS constrained adaptive policy.
        raw_mode = str(getattr(cfg, "alpha_policy_mode", "deterministic")).lower()
        self.alpha_policy_mode: PolicyMode = (
            raw_mode if raw_mode in ("deterministic", "ucb", "off") else "deterministic"
        )
        self.policy_exploration = float(getattr(cfg, "policy_exploration", 0.20))
        self.policy_min_samples = max(1, int(getattr(cfg, "policy_min_samples", 8)))
        self.policy_reward_clip_bps = float(
            getattr(cfg, "policy_reward_clip_bps", 20.0)
        )
        self.policy_save_every = max(1, int(getattr(cfg, "policy_save_every", 100)))
        self.policy_state_path = str(
            getattr(
                cfg,
                "policy_state_path",
                os.path.join(self.output_dir, "constrained_alpha_policy.json"),
            )
        )

        # The fair-value shift already embeds directional alpha. Keep the separate
        # round-trip alpha branch off unless explicitly enabled.
        self.enable_separate_alpha = bool(getattr(cfg, "enable_separate_alpha", False))

        # Avoid mixing the old heuristic tuner with the constrained policy by default.
        if not bool(getattr(cfg, "allow_legacy_auto_tuning", False)):
            self.enable_auto_tuning = False

        # July 2026 soft-floor / relative-rank awareness (local estimator only).
        # Kappa and PnL lookbacks are independent — never assume equality.
        self.enable_floor_awareness = bool(getattr(cfg, "enable_floor_awareness", True))
        self.rolling_kappa_lookback_ns = int(
            getattr(cfg, "rolling_kappa_lookback_ns", 10_800_000_000_000)
        )
        self.rolling_pnl_lookback_ns = int(
            getattr(cfg, "rolling_pnl_lookback_ns", 10_800_000_000_000)
        )
        self.rolling_kappa_min_history_ns = int(
            getattr(cfg, "rolling_kappa_min_history_ns", 5_400_000_000_000)
        )
        self.rolling_min_observations = max(
            1, int(getattr(cfg, "rolling_min_observations", 3))
        )
        # Soft floor guard: only tighten once we have a real relative-rank
        # history. 1.02 keeps a small buffer without starving early volume.
        self.score_floor_guard_ratio = float(getattr(cfg, "score_floor_guard_ratio", 1.02))
        self.weak_book_score_quantile = float(
            getattr(cfg, "weak_book_score_quantile", 0.25)
        )
        self.weak_book_score_quantile = max(0.05, min(0.95, self.weak_book_score_quantile))
        self.left_tail_score_quantile = float(
            getattr(cfg, "left_tail_score_quantile", 0.12)
        )
        self.left_tail_score_quantile = max(
            0.01, min(self.weak_book_score_quantile, self.left_tail_score_quantile)
        )
        self.weak_book_size_mult = float(getattr(cfg, "weak_book_size_mult", 0.75))
        self.weak_book_size_mult = max(0.05, min(1.0, self.weak_book_size_mult))
        self.left_tail_new_risk_enabled = bool(
            getattr(cfg, "left_tail_new_risk_enabled", False)
        )
        self.expiry_horizon_frac = float(getattr(cfg, "expiry_horizon_frac", 0.15))
        self.expiry_horizon_frac = max(0.01, min(0.50, self.expiry_horizon_frac))
        self.top_rank_selectivity_boost = float(
            getattr(cfg, "top_rank_selectivity_boost", 0.15)
        )
        self.top_rank_selectivity_boost = max(0.0, min(1.0, self.top_rank_selectivity_boost))
        self.score_kappa_weight = float(getattr(cfg, "score_kappa_weight", 0.79))
        self.score_pnl_weight = float(getattr(cfg, "score_pnl_weight", 0.21))
        weight_sum = max(self.score_kappa_weight + self.score_pnl_weight, 1e-12)
        self.score_kappa_weight /= weight_sum
        self.score_pnl_weight /= weight_sum
        self.floor_percentile = float(getattr(cfg, "floor_percentile", 50.0))
        self.floor_softness = float(getattr(cfg, "floor_softness", 0.5))
        self.floor_softness = max(1e-6, min(1.0, self.floor_softness))
        self._floor_pnl_scale = float(getattr(cfg, "floor_pnl_scale", 0.02))
        self.left_tail_markout_penalty = float(
            getattr(cfg, "left_tail_markout_penalty", 1.75)
        )
        self.weak_book_markout_bonus = float(
            getattr(cfg, "weak_book_markout_bonus", 1.15)
        )
        self.inventory_risk_reward_penalty = float(
            getattr(cfg, "inventory_risk_reward_penalty", 0.35)
        )
        self.volume_quality_penalty = float(
            getattr(cfg, "volume_quality_penalty", 0.40)
        )
        self._floor_score_ema = 0.0
        self._last_book_scores: dict[int, float] = {}
        self._last_weak_books: set[int] = set()
        self._last_left_tail_books: set[int] = set()
        self._last_eligible_books: set[int] = set()
        self._last_expiring_strong_books: set[int] = set()
        self._last_floor_threshold = 0.0
        self._last_trading_proxy = 0.0
        self._last_soft_floor_proxy = 0.0
        self._last_score_to_median = 0.0
        self._last_score_to_internal_median = 0.0
        self._last_rolling_kappa_proxy = 0.0
        self._last_rolling_pnl_proxy = 0.0
        self._score_decline_streak: dict[int, int] = {}
        self._soft_floor_proxy_delta = 0.0
        self._last_rolling_snap: RollingDiagnosticsSnapshot | None = None
        self._top_rank_pressure = False
        self._rolling_cfg = RollingScoringConfig(
            kappa_lookback_ns=self.rolling_kappa_lookback_ns,
            pnl_lookback_ns=self.rolling_pnl_lookback_ns,
            kappa_min_history_ns=self.rolling_kappa_min_history_ns,
            min_observations=self.rolling_min_observations,
            kappa_weight=self.score_kappa_weight,
            pnl_weight=self.score_pnl_weight,
            floor_percentile=self.floor_percentile,
            floor_softness=self.floor_softness,
            score_floor_guard_ratio=self.score_floor_guard_ratio,
            weak_book_score_quantile=self.weak_book_score_quantile,
            left_tail_score_quantile=self.left_tail_score_quantile,
            expiry_horizon_frac=self.expiry_horizon_frac,
            pnl_scale=self._floor_pnl_scale,
            kappa_norm_min=self.kappa_norm_min,
            kappa_norm_max=self.kappa_norm_max,
            kappa_tau=self.kappa_tau,
        )

        # Simulation-time phased mining (local FSM; no Strategy3 dependency,
        # no wall-clock delays). OFF by default — OBSERVE/COOLDOWN cancel all
        # quotes and starve volume/Kappa bootstrap on live validators.
        self.enable_phase_mining = bool(getattr(cfg, "enable_phase_mining", False))
        self.startup_observe_ns = max(
            0, int(getattr(cfg, "startup_observe_ns", 60_000_000_000))
        )
        self.calibration_max_ns = max(
            1, int(getattr(cfg, "calibration_max_ns", 300_000_000_000))
        )
        self.min_calibration_books = max(
            1, int(getattr(cfg, "min_calibration_books", 3))
        )
        self.phase_score_guard_ratio = max(
            0.0, float(getattr(cfg, "phase_score_guard_ratio", 1.00))
        )
        self.cooldown_ns = max(1, int(getattr(cfg, "cooldown_ns", 120_000_000_000)))
        self.resume_score_guard_ratio = max(
            self.phase_score_guard_ratio,
            float(getattr(cfg, "resume_score_guard_ratio", 1.02)),
        )
        self.phase_min_expected_pnl = max(
            0.0, float(getattr(cfg, "phase_min_expected_pnl", 0.0))
        )
        self._mining_phase: MiningPhase = (
            "OBSERVE" if self.enable_phase_mining else "ACTIVE"
        )
        self._phase_started_ts: int | None = None
        self._phase_reason = (
            "startup_observation"
            if self.enable_phase_mining
            else "phase_mining_disabled"
        )
        self._phase_regime_mode: str = "MIXED"
        self._phase_calibrated_books: set[int] = set()

        self._active_order_meta: dict[int, ActiveOrderMeta] = {}
        self._pending_markouts: Deque[PendingMarkout] = deque()
        self._policy_stats: dict[str, dict[int, dict[str, float]]] = {}
        self._policy_updates = 0
        self._quoted_books_this_tick: set[int] = set()
        self._mm_client_seq = 0
        self._max_active_order_meta = max(
            256, int(getattr(cfg, "max_active_order_meta", 4096))
        )
        self._load_policy_state()

        bt.logging.info(
            "Strategy4 initialized: "
            f"policy={self.alpha_policy_mode} max_inv={self.max_inventory_base} "
            f"gamma={self.base_risk_aversion} alpha_shift={self.alpha_shift_spreads} "
            f"inv_shift={self.inventory_shift_spreads} min_side_edge_bps="
            f"{self.min_side_edge_bps} markout_horizon_ns={self.markout_horizon_ns} "
            f"floor_aware={self.enable_floor_awareness} "
            f"kappa_lb={self.rolling_kappa_lookback_ns} "
            f"pnl_lb={self.rolling_pnl_lookback_ns} "
            f"floor_guard={self.score_floor_guard_ratio} "
            f"weak_q={self.weak_book_score_quantile} "
            f"left_tail_q={self.left_tail_score_quantile} "
            f"left_tail_risk={self.left_tail_new_risk_enabled} "
            f"phase_mining={self.enable_phase_mining} phase={self._mining_phase} "
            f"observe_ns={self.startup_observe_ns} "
            f"calibration_ns={self.calibration_max_ns} "
            f"min_calibration_books={self.min_calibration_books} "
            f"cooldown_ns={self.cooldown_ns}"
        )

    # ------------------------------------------------------------------
    # Utility and persistence
    # ------------------------------------------------------------------

    @staticmethod
    def _clip(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, value))

    @staticmethod
    def _sign(value: float) -> float:
        if value > 0:
            return 1.0
        if value < 0:
            return -1.0
        return 0.0

    def _mem(self, book_id: int) -> AdaptiveBookMemory:
        mem = self.book_memory.get(book_id)
        if isinstance(mem, AdaptiveBookMemory):
            return mem
        if isinstance(mem, BookMemory):
            upgraded = AdaptiveBookMemory(**mem.__dict__)
        else:
            upgraded = AdaptiveBookMemory()
        self.book_memory[book_id] = upgraded
        return upgraded

    def _reset_pnl_state(self) -> None:
        super()._reset_pnl_state()
        if not hasattr(self, "_mining_phase"):
            # onStart arrived before initialize() finished; nothing to reset yet.
            return
        self._floor_score_ema = 0.0
        self._soft_floor_proxy_delta = 0.0
        self._last_book_scores.clear()
        self._last_weak_books.clear()
        self._last_left_tail_books.clear()
        self._last_eligible_books.clear()
        self._last_expiring_strong_books.clear()
        self._last_floor_threshold = 0.0
        self._last_trading_proxy = 0.0
        self._last_soft_floor_proxy = 0.0
        self._last_score_to_median = 0.0
        self._last_score_to_internal_median = 0.0
        self._last_rolling_kappa_proxy = 0.0
        self._last_rolling_pnl_proxy = 0.0
        self._last_rolling_snap = None
        self._top_rank_pressure = False
        self._score_decline_streak.clear()
        self._mining_phase = "OBSERVE" if self.enable_phase_mining else "ACTIVE"
        self._phase_started_ts = None
        self._phase_reason = (
            "startup_observation"
            if self.enable_phase_mining
            else "phase_mining_disabled"
        )
        self._phase_regime_mode = "MIXED"
        self._phase_calibrated_books.clear()

    def _record_fill_hit(self, mem: BookMemory, side: Side) -> None:
        """Disable Strategy1's last-bucket attribution.

        Exact order/bucket attribution is performed in onTrade() from active
        client-order metadata. This prevents a delayed fill from being credited
        to whichever quote happened to be submitted most recently.
        """
        del mem, side

    def _load_policy_state(self) -> None:
        if self.alpha_policy_mode != "ucb" or not os.path.isfile(self.policy_state_path):
            return
        try:
            with open(self.policy_state_path, encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            bt.logging.warning(f"[ALPHA_POLICY] load failed: {exc}")
            return
        raw = payload.get("contexts", {}) if isinstance(payload, dict) else {}
        if not isinstance(raw, dict):
            return
        clean: dict[str, dict[int, dict[str, float]]] = {}
        for context, action_rows in raw.items():
            if not isinstance(action_rows, dict):
                continue
            clean_rows: dict[int, dict[str, float]] = {}
            for action_id, row in action_rows.items():
                if not isinstance(row, dict):
                    continue
                try:
                    aid = int(action_id)
                    count = max(0.0, float(row.get("count", 0.0)))
                    mean = float(row.get("mean", 0.0))
                except (TypeError, ValueError):
                    continue
                if 0 <= aid < len(SAFE_POLICY_ACTIONS):
                    clean_rows[aid] = {"count": count, "mean": mean}
            if clean_rows:
                clean[str(context)] = clean_rows
        self._policy_stats = clean

    def _save_policy_state(self) -> None:
        if self.alpha_policy_mode != "ucb":
            return
        directory = os.path.dirname(self.policy_state_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {
            "version": 1,
            "actions": [action.__dict__ for action in SAFE_POLICY_ACTIONS],
            "contexts": {
                context: {str(aid): row for aid, row in rows.items()}
                for context, rows in self._policy_stats.items()
            },
        }
        temp_path = f"{self.policy_state_path}.tmp"
        try:
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, sort_keys=True)
            os.replace(temp_path, self.policy_state_path)
        except OSError as exc:
            bt.logging.warning(f"[ALPHA_POLICY] save failed: {exc}")

    def _policy_row(self, context_key: str, action_id: int) -> dict[str, float]:
        rows = self._policy_stats.setdefault(context_key, {})
        return rows.setdefault(action_id, {"count": 0.0, "mean": 0.0})

    def _update_policy_value(
        self,
        context_key: str,
        action_id: int,
        reward_bps: float,
    ) -> None:
        if self.alpha_policy_mode != "ucb":
            return
        # Forced toxic action is not explored; do not pollute UCB totals.
        if action_id < 0 or action_id >= 4:
            return
        reward = self._clip(
            reward_bps,
            -self.policy_reward_clip_bps,
            self.policy_reward_clip_bps,
        )
        row = self._policy_row(context_key, action_id)
        row["count"] += 1.0
        row["mean"] += (reward - row["mean"]) / row["count"]
        self._policy_updates += 1
        if self._policy_updates % self.policy_save_every == 0:
            self._save_policy_state()

    def _next_mm_client_id(self, side: Side) -> int:
        """Unique MM client id in [MM_CLIENT_ID_BASE, ALPHA_CLIENT_ID_BASE)."""
        self._mm_client_seq = (self._mm_client_seq + 1) % 4995
        # Encode side in the low digit; stay below ALPHA_CLIENT_ID_BASE (80000).
        return MM_CLIENT_ID_BASE + self._mm_client_seq * 2 + (1 if side == "buy" else 2)

    def _cancel_open_orders(
        self,
        response: FinanceAgentResponse,
        book_id: int,
    ) -> int:
        account = self.accounts.get(book_id)
        if not account or not account.orders:
            return 0
        order_ids = [
            order.id
            for order in account.orders
            if getattr(order, "id", None) is not None
        ]
        if not order_ids:
            return 0
        response.cancel_orders(book_id=book_id, order_ids=order_ids, delay=0)
        return 1

    # ------------------------------------------------------------------
    # Corrected inventory model and hard risk state
    # ------------------------------------------------------------------

    def _inventory_util(self, inventory: InventorySnapshot) -> float:
        """Dimensionally correct inventory utilization in base units."""
        return abs(inventory.net_base) / max(self.max_inventory_base, 1e-12)

    def _signed_inventory_util(self, inventory: InventorySnapshot) -> float:
        return self._clip(
            inventory.net_base / max(self.max_inventory_base, 1e-12),
            -1.5,
            1.5,
        )

    def _inventory_needs_management(self, inventory: InventorySnapshot) -> bool:
        if inventory.band in ("MAX_LONG", "MAX_SHORT"):
            return True
        return self._inventory_util(inventory) >= self.inventory_close_threshold

    def _net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        if mid <= 0:
            return InventorySnapshot(0.0, 0.0, "FLAT", None, None, 0)

        tracker: PositionTracker = self._position_tracker_snapshot(book_id)
        net_base = tracker.net_qty
        wealth_per_book = self._wealth_per_book()
        wealth_ratio = (
            (net_base * mid) / wealth_per_book if wealth_per_book > 0 else 0.0
        )
        flat_eps = self.mm_base_size * 1e-3
        aged = getattr(self, "_inventory_aged_books", None)
        if aged is None:
            aged = set()
            self._inventory_aged_books = aged

        if abs(net_base) < flat_eps:
            band = "FLAT"
            self._position_ticks.pop(book_id, None)
            self._inventory_reason.pop(book_id, None)
            aged.discard(book_id)
        elif net_base > 0:
            band = "MAX_LONG" if net_base >= self.max_inventory_base else "LONG"
            if book_id not in aged:
                self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
                aged.add(book_id)
        else:
            band = "MAX_SHORT" if net_base <= -self.max_inventory_base else "SHORT"
            if book_id not in aged:
                self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
                aged.add(book_id)

        vwap = tracker.vwap_entry
        unrealized_bps: float | None = None
        if vwap and vwap > 0:
            if net_base > 0:
                unrealized_bps = ((mid - vwap) / vwap) * 10_000.0
            elif net_base < 0:
                unrealized_bps = ((vwap - mid) / vwap) * 10_000.0

        return InventorySnapshot(
            net_base=net_base,
            inventory_ratio=wealth_ratio,
            band=band,
            vwap_entry=vwap,
            unrealized_bps=unrealized_bps,
            position_ticks=self._position_ticks.get(book_id, 0),
            opened_at_ns=tracker.opened_at_ns,
            reason=self._inventory_reason.get(book_id, "UNKNOWN"),
        )

    def _compute_close_score(
        self,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ) -> float:
        unreal = inventory.unrealized_bps
        target = max(regime_params.profit_target_bps, 1e-9)
        stop = max(regime_params.stop_loss_bps, 1e-9)
        pnl_component = 0.0
        if unreal is not None:
            denominator = target if unreal >= 0 else stop
            pnl_component = min(1.0, abs(unreal) / denominator)
        inventory_risk = min(1.0, self._inventory_util(inventory))
        time_risk = min(1.0, inventory.position_ticks / self.position_max_ticks)
        regime_risk = 1.0 if regime.mode == "STRESSED" else 0.0
        if archetype in ("TOXIC_BOOK", "WALL_BOOK"):
            regime_risk = max(regime_risk, 0.65)
        elif archetype == "DEAD_BOOK":
            regime_risk = max(regime_risk, 0.40)
        return 0.45 * pnl_component + 0.35 * inventory_risk + 0.20 * max(
            time_risk, regime_risk
        )

    def _l3_toxicity_score(
        self,
        profile: BookProfile,
        prediction: DirectionForecast,
        mem: AdaptiveBookMemory,
        archetype: BookArchetype,
    ) -> float:
        vol_ref = max(self.profile_vol_scale, 1e-9)
        vol_component = self._clip(profile.volatility / (2.5 * vol_ref), 0.0, 1.0)
        spread_component = self._clip(
            (profile.spread_bps or 0.0) / max(self.toxic_spread_bps, 1e-9),
            0.0,
            1.0,
        )
        flow_component = self._clip(
            0.5 * abs(prediction.trade_imbalance)
            + 0.5 * abs(prediction.imbalance),
            0.0,
            1.0,
        )
        adverse_component = self._clip(
            max(
                0.0,
                -mem.buy_markout_ema_bps,
                -mem.sell_markout_ema_bps,
            )
            / 5.0,
            0.0,
            1.0,
        )
        loss_component = self._clip(
            mem.loss_streak / max(self.reduce_only_loss_streak, 1),
            0.0,
            1.0,
        )
        archetype_component = {
            "STRESSED": 1.0,
            "TOXIC_BOOK": 0.85,
            "WALL_BOOK": 0.55,
            "TREND_BOOK": 0.35,
            "DEAD_BOOK": 0.40,
            "MM_BOOK": 0.05,
        }.get(archetype, 0.35)
        return self._clip(
            0.20 * vol_component
            + 0.15 * spread_component
            + 0.20 * flow_component
            + 0.20 * adverse_component
            + 0.15 * loss_component
            + 0.10 * archetype_component,
            0.0,
            1.0,
        )

    # ------------------------------------------------------------------
    # Rolling scoring diagnostics (independent Kappa / PnL lookbacks)
    # ------------------------------------------------------------------

    @staticmethod
    def _percentile(values: list[float], pct: float) -> float:
        return rolling_percentile(values, pct)

    def _soft_floor_factor(self, score: float, threshold: float) -> float:
        return rolling_soft_floor_factor(score, threshold, self.floor_softness)

    def _sync_rolling_config(self) -> RollingScoringConfig:
        cfg = getattr(self, "_rolling_cfg", None)
        if cfg is None:
            cfg = RollingScoringConfig()
            self._rolling_cfg = cfg
        cfg.kappa_lookback_ns = self.rolling_kappa_lookback_ns
        cfg.pnl_lookback_ns = self.rolling_pnl_lookback_ns
        cfg.kappa_min_history_ns = self.rolling_kappa_min_history_ns
        cfg.min_observations = self.rolling_min_observations
        cfg.kappa_weight = self.score_kappa_weight
        cfg.pnl_weight = self.score_pnl_weight
        cfg.floor_percentile = self.floor_percentile
        cfg.floor_softness = self.floor_softness
        cfg.score_floor_guard_ratio = self.score_floor_guard_ratio
        cfg.weak_book_score_quantile = self.weak_book_score_quantile
        cfg.left_tail_score_quantile = self.left_tail_score_quantile
        cfg.expiry_horizon_frac = self.expiry_horizon_frac
        cfg.pnl_scale = self._floor_pnl_scale
        cfg.kappa_norm_min = self.kappa_norm_min
        cfg.kappa_norm_max = self.kappa_norm_max
        cfg.kappa_tau = self.kappa_tau
        return cfg

    def _update_score_decline_streaks(self, book_scores: dict[int, float]) -> None:
        prev = self._last_book_scores
        streaks = getattr(self, "_score_decline_streak", None)
        if streaks is None:
            streaks = {}
            self._score_decline_streak = streaks
        for book_id, score in book_scores.items():
            prior = prev.get(book_id)
            if prior is not None and score + 1e-12 < prior:
                streaks[book_id] = streaks.get(book_id, 0) + 1
            else:
                streaks[book_id] = 0

    def _evaluate_score_floor(
        self,
        profiles: list[BookProfile],
    ) -> dict[str, float | int | bool | set[int] | dict[int, float]]:
        """Rolling Kappa/PnL diagnostics + soft-floor proxy for Strategy4."""
        book_ids = [p.book_id for p in profiles]
        snap = compute_rolling_diagnostics(
            self.realized_pnl_history,
            book_ids,
            self._sync_rolling_config(),
        )
        self._update_score_decline_streaks(snap.book_scores)
        self._top_rank_pressure = bool(snap.below_guard)

        raw_delta = snap.soft_floor_score_proxy - self._last_soft_floor_proxy
        self._soft_floor_proxy_delta = (
            0.80 * self._soft_floor_proxy_delta + 0.20 * raw_delta
        )
        self._floor_score_ema = (
            0.90 * self._floor_score_ema + 0.10 * snap.trading_score_proxy
            if self._floor_score_ema > 0.0
            else snap.trading_score_proxy
        )
        self._last_rolling_snap = snap
        self._last_book_scores = dict(snap.book_scores)
        self._last_weak_books = set(snap.weak_books)
        self._last_left_tail_books = set(snap.left_tail_books)
        self._last_eligible_books = set(snap.eligible_books)
        self._last_expiring_strong_books = set(snap.expiring_strong_books)
        self._last_floor_threshold = snap.floor_threshold
        self._last_trading_proxy = snap.trading_score_proxy
        self._last_soft_floor_proxy = snap.soft_floor_score_proxy
        self._last_score_to_median = snap.score_to_internal_median_delta
        self._last_score_to_internal_median = snap.score_to_internal_median
        self._last_rolling_kappa_proxy = snap.rolling_kappa_proxy
        self._last_rolling_pnl_proxy = snap.rolling_pnl_proxy
        return {
            "book_scores": dict(snap.book_scores),
            "estimated_trading_score": snap.trading_score_proxy,
            "estimated_soft_floor_score": snap.soft_floor_score_proxy,
            "estimated_score_to_median": snap.score_to_internal_median_delta,
            "trading_score_proxy": snap.trading_score_proxy,
            "soft_floor_score_proxy": snap.soft_floor_score_proxy,
            "rolling_kappa_proxy": snap.rolling_kappa_proxy,
            "rolling_pnl_proxy": snap.rolling_pnl_proxy,
            "score_to_internal_median": snap.score_to_internal_median,
            "floor_threshold": snap.floor_threshold,
            "weak_books": set(snap.weak_books),
            "left_tail_books": set(snap.left_tail_books),
            "eligible_books": set(snap.eligible_books),
            "expiring_strong_books": set(snap.expiring_strong_books),
            "below_guard": snap.below_guard,
            "top_rank_pressure": self._top_rank_pressure,
        }

    def _floor_aware_policy_reward(
        self,
        markout_bps: float,
        item: PendingMarkout,
    ) -> float:
        """UCB reward: soft-floor improvement + PnL; penalize junk volume / left-tail."""
        reward = markout_bps
        mem = self._mem(item.book_id)
        snap = self._last_rolling_snap
        diag = snap.books.get(item.book_id) if snap is not None else None

        # Realized PnL improvement (rolling when available).
        rolling_pnl = diag.rolling_pnl if diag is not None else mem.recent_pnl
        realized_pnl_signal = self._clip(
            rolling_pnl / max(self._floor_pnl_scale, 1e-9), -1.0, 1.0
        )
        reward += realized_pnl_signal

        if item.was_left_tail and markout_bps < 0.0:
            reward *= self.left_tail_markout_penalty
        elif item.was_weak and markout_bps > 0.0:
            # Reward weak-book recovery.
            reward *= self.weak_book_markout_bonus

        if self.enable_floor_awareness:
            reward += self._clip(self._soft_floor_proxy_delta * 50.0, -1.0, 1.0)

        # Penalize low-quality volume: fills with weak/negative edge and no PnL.
        if markout_bps <= 0.0 and (
            item.was_weak or item.was_left_tail or realized_pnl_signal <= 0.0
        ):
            reward -= self.volume_quality_penalty * max(1.0, abs(markout_bps))

        worsened = (
            (item.side == "buy" and item.inventory_before > 0.0)
            or (item.side == "sell" and item.inventory_before < 0.0)
        )
        if worsened and markout_bps <= 0.0:
            reward -= self.inventory_risk_reward_penalty * max(1.0, abs(markout_bps))
        elif worsened and markout_bps < self.min_side_edge_bps:
            reward -= 0.5 * self.inventory_risk_reward_penalty
        return reward

    # ------------------------------------------------------------------
    # Simulation-time phased mining (local FSM)
    # ------------------------------------------------------------------

    def _phase_elapsed(self, timestamp: int) -> int:
        """Elapsed phase time in simulation nanoseconds (never wall clock)."""
        now = int(timestamp)
        if self._phase_started_ts is None or now < self._phase_started_ts:
            self._phase_started_ts = now
            return 0
        return max(0, now - self._phase_started_ts)

    def _set_mining_phase(
        self,
        phase: MiningPhase,
        timestamp: int,
        reason: str,
    ) -> None:
        if phase != self._mining_phase:
            self._mining_phase = phase
            self._phase_started_ts = int(timestamp)
        self._phase_reason = reason

    def _phase_score_is_healthy(self, ratio: float) -> bool:
        score_ratio = self._last_score_to_internal_median
        median = self._last_floor_threshold
        # No relative-rank history yet — allow bootstrap rather than deadlock
        # (CALIBRATE never finishes when score_ratio stays 0 with no trades).
        if median <= 0.0 or score_ratio <= 0.0:
            return True
        return score_ratio >= ratio

    def _phase_book_is_clean(
        self,
        book_id: int,
        profile: BookProfile,
        mem: AdaptiveBookMemory,
    ) -> bool:
        """CALIBRATE-quality book: profitable, non-weak, no active loss risk."""
        if profile.tier not in ("GREEN", "YELLOW"):
            return False
        if profile.realized_pnl < 0.0 or mem.recent_pnl < 0.0:
            return False
        if mem.loss_streak > 0:
            return False
        if book_id in self._last_weak_books or book_id in self._last_left_tail_books:
            return False
        if profile.spread_bps is not None and profile.spread_bps >= self.toxic_spread_bps:
            return False
        return True

    def _update_mining_phase(
        self,
        state: MarketSimulationStateUpdate,
        regime: MarketRegime,
    ) -> None:
        """Advance the phase FSM using simulation time and local score state.

        Requires _evaluate_score_floor to have refreshed the floor proxies for
        this tick (build_mm_strategy_instructions calls it before quoting).
        """
        self._phase_regime_mode = regime.mode
        if not self.enable_phase_mining:
            self._set_mining_phase("ACTIVE", state.timestamp, "phase_mining_disabled")
            return

        elapsed = self._phase_elapsed(state.timestamp)
        normal_regime = regime.mode != "STRESSED"
        active_ok = self._phase_score_is_healthy(self.phase_score_guard_ratio)
        resume_ok = self._phase_score_is_healthy(self.resume_score_guard_ratio)
        calibrated = len(self._phase_calibrated_books)

        if self._mining_phase == "OBSERVE":
            if elapsed >= self.startup_observe_ns:
                self._set_mining_phase(
                    "CALIBRATE", state.timestamp, "startup_observation_complete"
                )
            else:
                self._phase_reason = "collecting_simulation_history"

        elif self._mining_phase == "CALIBRATE":
            if (
                calibrated >= self.min_calibration_books
                and active_ok
                and normal_regime
            ):
                self._set_mining_phase(
                    "ACTIVE", state.timestamp, "calibration_complete"
                )
            elif elapsed >= self.calibration_max_ns:
                self._set_mining_phase(
                    "COOLDOWN",
                    state.timestamp,
                    "calibration_timeout_score_or_coverage_guard",
                )
            else:
                self._phase_reason = (
                    f"calibrating_{calibrated}_of_{self.min_calibration_books}_books"
                )

        elif self._mining_phase == "ACTIVE":
            if not normal_regime:
                self._set_mining_phase("COOLDOWN", state.timestamp, "stressed_regime")
            elif not active_ok:
                self._set_mining_phase(
                    "COOLDOWN", state.timestamp, "internal_score_below_guard"
                )
            else:
                self._phase_reason = "active_guards_healthy"

        elif self._mining_phase == "COOLDOWN":
            if elapsed >= self.cooldown_ns and resume_ok and normal_regime:
                self._set_mining_phase(
                    "RESUME", state.timestamp, "cooldown_complete_guards_recovered"
                )
            elif elapsed < self.cooldown_ns:
                self._phase_reason = "minimum_cooldown"
            else:
                self._phase_reason = "waiting_for_score_or_regime_recovery"

        elif self._mining_phase == "RESUME":
            if not normal_regime or not resume_ok:
                self._set_mining_phase(
                    "COOLDOWN", state.timestamp, "resume_guard_failed"
                )
            elif elapsed > 0:
                self._set_mining_phase(
                    "ACTIVE", state.timestamp, "resume_guards_confirmed"
                )
            else:
                self._phase_reason = "confirming_resume_guards"

    def _phase_adjusted_risk_mode(
        self,
        risk_mode: RiskMode,
        inventory: InventorySnapshot,
        profile: BookProfile,
        mem: AdaptiveBookMemory,
    ) -> RiskMode:
        """Phase override on top of the base risk FSM; never loosens risk."""
        if not self.enable_phase_mining:
            return risk_mode
        phase = self._mining_phase
        if phase == "ACTIVE" or risk_mode in ("LIQUIDATE", "DISABLED"):
            return risk_mode
        if phase == "OBSERVE":
            return "REDUCE_ONLY" if inventory.band != "FLAT" else "DISABLED"
        if phase == "COOLDOWN":
            if risk_mode == "REDUCE_ONLY":
                return risk_mode
            return "REDUCE_ONLY" if inventory.band != "FLAT" else "DISABLED"
        if phase == "RESUME":
            return "CAUTIOUS" if risk_mode == "NORMAL" else risk_mode
        # CALIBRATE: NORMAL only for clean flat books; otherwise defensive.
        if risk_mode == "NORMAL":
            if inventory.band == "FLAT" and self._phase_book_is_clean(
                profile.book_id, profile, mem
            ):
                return "NORMAL"
            return "CAUTIOUS" if inventory.band != "FLAT" else "DISABLED"
        return risk_mode

    def _risk_mode(
        self,
        inventory: InventorySnapshot,
        profile: BookProfile,
        prediction: DirectionForecast,
        mem: AdaptiveBookMemory,
        archetype: BookArchetype,
    ) -> tuple[RiskMode, float]:
        toxicity = self._l3_toxicity_score(profile, prediction, mem, archetype)
        inv_util = self._inventory_util(inventory)
        unreal = inventory.unrealized_bps
        book_id = profile.book_id
        floor_on = self.enable_floor_awareness
        is_weak = floor_on and book_id in self._last_weak_books
        is_left = floor_on and book_id in self._last_left_tail_books
        book_score = self._last_book_scores.get(book_id)
        below_median = (
            floor_on
            and book_score is not None
            and self._last_floor_threshold > 0.0
            and book_score < self._last_floor_threshold
        )
        # Require a real score history. score_ratio==0 used to always trip
        # CAUTIOUS and starve early quoting.
        score_ratio = self._last_score_to_internal_median
        has_score_history = (
            self._last_floor_threshold > 0.0 and score_ratio > 0.0
        )
        near_floor = (
            floor_on
            and has_score_history
            and (
                score_ratio < self.score_floor_guard_ratio
                or self._top_rank_pressure
            )
        )
        decline = int(getattr(self, "_score_decline_streak", {}).get(book_id, 0))
        snap = self._last_rolling_snap
        rolling_pnl = (
            snap.books[book_id].rolling_pnl
            if snap is not None and book_id in snap.books
            else float(profile.realized_pnl)
        )

        # Toxic left-tail: disable when flat.
        if (
            floor_on
            and inventory.band == "FLAT"
            and is_left
            and (
                rolling_pnl < 0.0
                or archetype == "TOXIC_BOOK"
                or toxicity >= self.toxicity_cautious_threshold
                or mem.loss_streak >= self.cautious_loss_streak
            )
        ):
            return "DISABLED", toxicity

        if (
            floor_on
            and inventory.band == "FLAT"
            and rolling_pnl < 0.0
            and decline >= 2
            and (
                archetype == "TOXIC_BOOK"
                or toxicity >= self.toxicity_cautious_threshold
                or mem.loss_streak >= self.cautious_loss_streak
            )
        ):
            return "DISABLED", toxicity

        # Left-tail with dangerous inventory/loss: liquidate.
        if (
            is_left
            and inventory.band != "FLAT"
            and (
                rolling_pnl < 0.0
                or inv_util >= self.cautious_inventory_util * 0.55
                or (unreal is not None and unreal < 0.0)
                or mem.loss_streak >= self.cautious_loss_streak
                or decline >= 2
            )
        ):
            return "LIQUIDATE", toxicity

        if (
            inventory.band in ("MAX_LONG", "MAX_SHORT")
            or inv_util >= self.liquidate_inventory_util
            or (unreal is not None and unreal <= -self.hard_stop_loss_bps)
        ):
            return "LIQUIDATE", toxicity

        # Weak books with inventory: reduce-only.
        if (
            (is_weak or below_median)
            and inventory.band != "FLAT"
            and (
                inv_util >= self.cautious_inventory_util * 0.55
                or mem.loss_streak >= self.cautious_loss_streak
                or rolling_pnl < 0.0
            )
        ):
            return "REDUCE_ONLY", toxicity

        if inv_util >= self.reduce_only_inventory_util:
            return "REDUCE_ONLY", toxicity
        if mem.loss_streak >= self.reduce_only_loss_streak:
            return "REDUCE_ONLY", toxicity
        if toxicity >= self.toxicity_reduce_threshold:
            return "REDUCE_ONLY", toxicity

        if self._last_response_latency_ms >= self.latency_disable_ms:
            return ("REDUCE_ONLY" if inventory.band != "FLAT" else "DISABLED"), toxicity

        # Near soft-floor: cautious (never NORMAL until score/risk healthy).
        if (
            near_floor
            or inv_util >= self.cautious_inventory_util
            or mem.loss_streak >= self.cautious_loss_streak
            or toxicity >= self.toxicity_cautious_threshold
            or self._last_response_latency_ms >= self.latency_cautious_ms
            or (is_weak and inventory.band != "FLAT")
        ):
            return "CAUTIOUS", toxicity

        # Soft-floor caution only when we have real relative-rank history.
        # Weak books still quote in NORMAL with a size haircut at placement.
        if floor_on and has_score_history and (
            score_ratio < self.score_floor_guard_ratio or is_left
        ):
            return "CAUTIOUS", toxicity

        return "NORMAL", toxicity

    # ------------------------------------------------------------------
    # Book classification, policy, fair price, and GLFT-inspired quotes
    # ------------------------------------------------------------------

    def classify_book_archetype(
        self,
        profile: BookProfile,
        regime: MarketRegime,
    ) -> BookArchetype:
        """Risk-first precedence; tight spread no longer hides toxic flow."""
        spread_bps = profile.spread_bps or 0.0
        if spread_bps >= self.archetype_stressed_spread_bps or regime.mode == "STRESSED":
            return "STRESSED"
        if profile.volatility >= 1.75 * self.archetype_vol_threshold:
            return "TOXIC_BOOK"
        if abs(profile.imbalance) >= self.archetype_wall_imbalance:
            return "WALL_BOOK"
        if (
            profile.volatility >= self.archetype_vol_threshold
            or abs(profile.predict_score) >= self.direction_threshold
        ):
            return "TREND_BOOK"
        if profile.trade_rate < self.archetype_dead_trade_rate:
            return "DEAD_BOOK"
        if (
            spread_bps < self.archetype_mm_spread_bps
            or profile.volatility < self.archetype_vol_threshold
        ):
            return "MM_BOOK"
        return "TOXIC_BOOK"

    def _signed_archetype_bias(
        self,
        profile: BookProfile,
        prediction: DirectionForecast,
        archetype: BookArchetype,
    ) -> float:
        if archetype == "WALL_BOOK":
            return 0.20 * self._sign(profile.imbalance)
        if archetype == "TREND_BOOK":
            return 0.30 * self._sign(prediction.score)
        return 0.0

    def _context_key(
        self,
        regime: MarketRegime,
        archetype: BookArchetype,
        inventory: InventorySnapshot,
        prediction: DirectionForecast,
        toxicity: float,
    ) -> str:
        signal_bucket = "up" if prediction.score > self.direction_threshold else (
            "down" if prediction.score < -self.direction_threshold else "flat"
        )
        toxic_bucket = "high" if toxicity >= self.toxicity_cautious_threshold else "low"
        return ":".join(
            (
                str(regime.mode),
                archetype,
                inventory.band,
                signal_bucket,
                toxic_bucket,
            )
        )

    def _deterministic_policy_action(
        self,
        risk_mode: RiskMode,
        profile: BookProfile,
        prediction: DirectionForecast,
        archetype: BookArchetype,
    ) -> SafePolicyAction:
        if risk_mode in ("REDUCE_ONLY", "LIQUIDATE", "DISABLED"):
            return SAFE_POLICY_ACTIONS[4]
        if risk_mode == "CAUTIOUS" or archetype in ("TOXIC_BOOK", "WALL_BOOK"):
            return SAFE_POLICY_ACTIONS[0]
        if archetype == "MM_BOOK" and profile.trade_rate >= self.trade_rate_ref:
            return SAFE_POLICY_ACTIONS[3]
        if abs(prediction.score) >= 1.5 * self.direction_threshold:
            return SAFE_POLICY_ACTIONS[2]
        return SAFE_POLICY_ACTIONS[1]

    def _select_policy_action(
        self,
        context_key: str,
        risk_mode: RiskMode,
        profile: BookProfile,
        prediction: DirectionForecast,
        archetype: BookArchetype,
    ) -> SafePolicyAction:
        deterministic = self._deterministic_policy_action(
            risk_mode, profile, prediction, archetype
        )
        if self.alpha_policy_mode in ("off", "deterministic") or risk_mode != "NORMAL":
            return deterministic

        rows = self._policy_stats.setdefault(context_key, {})
        # Warm-start: prefer the least-sampled eligible action, not always defensive.
        under_sampled = [
            action
            for action in SAFE_POLICY_ACTIONS[:4]
            if rows.get(action.action_id, {}).get("count", 0.0) < self.policy_min_samples
        ]
        if under_sampled:
            return min(
                under_sampled,
                key=lambda action: rows.get(action.action_id, {}).get("count", 0.0),
            )

        total = sum(
            rows.get(action.action_id, {}).get("count", 0.0)
            for action in SAFE_POLICY_ACTIONS[:4]
        )
        best_action = deterministic
        best_score = -math.inf
        for action in SAFE_POLICY_ACTIONS[:4]:
            row = self._policy_row(context_key, action.action_id)
            count = max(row["count"], 1.0)
            bonus = self.policy_exploration * math.sqrt(
                math.log(max(total, 2.0)) / count
            )
            score = row["mean"] + bonus
            if score > best_score:
                best_score = score
                best_action = action
        return best_action

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
        if not book.bids or not book.asks:
            return None
        best_bid = book.bids[0].price
        best_ask = book.asks[0].price
        spread = best_ask - best_bid
        mid = 0.5 * (best_bid + best_ask)
        if spread <= 0 or mid <= 0:
            return None

        mem = self._mem(profile.book_id)
        risk_mode, toxicity = self._risk_mode(
            inventory, profile, prediction, mem, archetype
        )
        risk_mode = self._phase_adjusted_risk_mode(
            risk_mode, inventory, profile, mem
        )
        context_key = self._context_key(
            regime, archetype, inventory, prediction, toxicity
        )
        action = self._select_policy_action(
            context_key, risk_mode, profile, prediction, archetype
        )

        signal = self._clip(prediction.score, -1.0, 1.0)
        signed_bias = self._signed_archetype_bias(profile, prediction, archetype)
        alpha_signal = self._clip(signal + signed_bias, -1.0, 1.0)
        fair_shift = self._clip(
            self.alpha_shift_spreads * action.alpha_mult * alpha_signal,
            -self.max_fair_shift_spreads,
            self.max_fair_shift_spreads,
        )
        fair_price = mid + spread * fair_shift

        inv_util = self._signed_inventory_util(inventory)
        vol_ratio = self._clip(
            profile.volatility / max(self.profile_vol_scale, 1e-9),
            0.0,
            4.0,
        )
        gamma = self._clip(
            self.base_risk_aversion
            * action.gamma_mult
            * (1.0 + 0.25 * vol_ratio + 0.45 * toxicity + 0.35 * abs(inv_util)),
            self.min_risk_aversion,
            self.max_risk_aversion,
        )

        # A positive long inventory lowers the reservation price; a short raises it.
        inventory_shift = (
            self.inventory_shift_spreads
            * gamma
            * inv_util
            * (1.0 + 0.15 * vol_ratio)
        )
        reservation_price = fair_price - spread * inventory_shift

        # Practical GLFT term: lower trade intensity / deeper queue means wider quotes.
        intensity = self._clip(
            profile.trade_rate / max(self.trade_rate_ref, 1e-9),
            self.glft_kappa_floor,
            self.glft_kappa_cap,
        )
        glft_term = self.glft_spread_weight * math.log1p(gamma / intensity) / max(
            gamma, 1e-9
        )
        half_spread_ratio = (
            regime_params.spread_offset * action.spread_mult
            + self.vol_spread_weight * min(vol_ratio, 2.0)
            + self.toxicity_spread_weight * toxicity
            + self.inventory_spread_weight * min(abs(inv_util), 1.0)
            + glft_term
        )
        half_spread = spread * max(0.05, half_spread_ratio)
        half_spread_bps = half_spread / mid * 10_000.0
        half_spread_bps = self._clip(
            half_spread_bps,
            self.min_half_spread_bps,
            self.max_half_spread_bps,
        )
        half_spread = mid * half_spread_bps / 10_000.0

        tick_size = 10.0 ** (-price_decimals)
        bid_price = min(reservation_price - half_spread, best_ask - tick_size)
        ask_price = max(reservation_price + half_spread, best_bid + tick_size)
        bid_price = round(bid_price, price_decimals)
        ask_price = round(ask_price, price_decimals)
        if bid_price <= 0 or bid_price >= ask_price:
            return None

        return QuoteDecision(
            fair_price=fair_price,
            reservation_price=reservation_price,
            bid_price=bid_price,
            ask_price=ask_price,
            half_spread=half_spread,
            gamma=gamma,
            toxicity=toxicity,
            risk_mode=risk_mode,
            action=action,
            context_key=context_key,
        )

    # ------------------------------------------------------------------
    # Side-specific EV, size, fill learning, and order metadata
    # ------------------------------------------------------------------

    def _distance_from_touch(
        self,
        side: Side,
        quote_price: float,
        best_bid: float,
        best_ask: float,
    ) -> float:
        spread = max(best_ask - best_bid, 1e-12)
        if side == "buy":
            return max(0.0, (best_bid - quote_price) / spread)
        return max(0.0, (quote_price - best_ask) / spread)

    def _record_active_order(
        self,
        meta: ActiveOrderMeta,
    ) -> None:
        self._active_order_meta[meta.client_order_id] = meta
        if len(self._active_order_meta) > self._max_active_order_meta:
            # Drop oldest keys (insertion order) when the map grows too large.
            overflow = len(self._active_order_meta) - self._max_active_order_meta
            for key in list(self._active_order_meta.keys())[:overflow]:
                self._active_order_meta.pop(key, None)
        mem = self._mem(meta.book_id)
        mem.submitted_qty += meta.submitted_qty
        mem.last_quote_ts = meta.submitted_ts
        self._record_fill_quote(mem, meta.side, meta.distance_from_touch)

    def _adverse_selection_bps(self, mem: AdaptiveBookMemory, side: Side) -> float:
        samples = mem.markout_samples
        if samples <= 0:
            return self.unseen_markout_buffer_bps
        markout = (
            mem.buy_markout_ema_bps if side == "buy" else mem.sell_markout_ema_bps
        )
        return max(0.0, -markout) * self.adverse_selection_weight

    def _side_expected_value_bps(
        self,
        side: Side,
        decision: QuoteDecision,
        fill_probability: float,
        inventory: InventorySnapshot,
        mem: AdaptiveBookMemory,
    ) -> float:
        """Return conditional edge in bps (not fill-scaled).

        Fill probability is enforced separately via ``min_fill_prob`` so thin
        books are not double-penalized against ``min_side_edge_bps``.
        """
        del fill_probability
        mid = max(decision.fair_price, 1e-12)
        if side == "buy":
            gross_bps = (decision.fair_price - decision.bid_price) / mid * 10_000.0
            worsening = max(0.0, self._signed_inventory_util(inventory))
        else:
            gross_bps = (decision.ask_price - decision.fair_price) / mid * 10_000.0
            worsening = max(0.0, -self._signed_inventory_util(inventory))
        adverse = self._adverse_selection_bps(mem, side)
        inventory_cost = 0.50 * worsening * decision.gamma
        latency_cost = max(
            0.0,
            (self._last_response_latency_ms - self.latency_cautious_ms)
            / max(self.latency_cautious_ms, 1.0),
        ) * 0.25
        return (
            gross_bps
            - self.fee_buffer_bps
            - adverse
            - inventory_cost
            - latency_cost
        )

    def estimate_fill_probability(
        self,
        book: Book,
        mid: float,
        spread: float,
        trade_rate: float,
        buy_price: float,
        sell_price: float,
        book_id: int | None = None,
    ):
        """Estimate fills with one consistent distance-from-touch definition."""
        if spread <= 0 or mid <= 0 or not book.bids or not book.asks:
            return FillProbabilityEstimate(0.0, 0.0)

        best_bid = book.bids[0].price
        best_ask = book.asks[0].price
        buy_distance = self._distance_from_touch(
            "buy", buy_price, best_bid, best_ask
        )
        sell_distance = self._distance_from_touch(
            "sell", sell_price, best_bid, best_ask
        )
        trade_factor = self._clip(
            trade_rate / max(self.trade_rate_ref, 1e-9), 0.0, 1.0
        )

        bid_depth = max(book.bids[0].quantity, 0.0)
        ask_depth = max(book.asks[0].quantity, 0.0)
        bid_pressure = self._clip(
            trade_rate / max(bid_depth + 1.0, 1e-9)
            / max(self.trade_rate_ref, 1e-9),
            0.0,
            1.0,
        )
        ask_pressure = self._clip(
            trade_rate / max(ask_depth + 1.0, 1e-9)
            / max(self.trade_rate_ref, 1e-9),
            0.0,
            1.0,
        )
        buy_distance_score = math.exp(-1.35 * buy_distance)
        sell_distance_score = math.exp(-1.35 * sell_distance)

        p_buy = trade_factor * (
            0.55 * buy_distance_score + 0.45 * bid_pressure
        )
        p_sell = trade_factor * (
            0.55 * sell_distance_score + 0.45 * ask_pressure
        )

        if book_id is not None:
            mem = self._mem(book_id)
            learned_buy = self._learned_side_fill_prob(
                mem, "buy", buy_distance
            )
            learned_sell = self._learned_side_fill_prob(
                mem, "sell", sell_distance
            )
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
            quantity_prior = mem.quantity_fill_rate
            if mem.quote_count >= self.fill_learn_min_samples:
                p_buy = 0.90 * p_buy + 0.10 * quantity_prior
                p_sell = 0.90 * p_sell + 0.10 * quantity_prior

        return FillProbabilityEstimate(
            buy=self._clip(p_buy, 0.0, 1.0),
            sell=self._clip(p_sell, 0.0, 1.0),
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
        confidence = self._clip(1.0 + 0.35 * abs(profile.predict_score), 0.65, 1.35)
        vol_scale = 1.0
        if profile.volatility > 0:
            vol_scale = self._clip(
                self.profile_vol_scale / profile.volatility,
                0.40,
                1.35,
            )
        spread_factor = 1.0
        if profile.spread is not None and mid and mid > 0:
            spread_bps = profile.spread / mid * 10_000.0
            spread_factor = self._clip(1.0 - spread_bps / 25.0, 0.45, 1.10)
        kappa_scale = 1.0
        if profile.raw_kappa is not None:
            kappa_scale = self._clip(1.0 + profile.raw_kappa * 0.15, 0.60, 1.30)
        inventory_factor = self._clip(1.0 - self._inventory_util(inventory), 0.20, 1.0)
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

    def _side_sizes(
        self,
        common_size: float,
        inventory: InventorySnapshot,
        prediction: DirectionForecast,
        decision: QuoteDecision,
        volume_decimals: int,
    ) -> tuple[float, float]:
        inv = self._signed_inventory_util(inventory)
        buy_mult = math.exp(-self.size_inventory_skew * inv)
        sell_mult = math.exp(self.size_inventory_skew * inv)

        # Mild signal-size tilt; most directional expression comes from fair price.
        signal = self._clip(prediction.score, -1.0, 1.0)
        buy_mult *= 1.0 + 0.15 * signal
        sell_mult *= 1.0 - 0.15 * signal

        if decision.risk_mode == "CAUTIOUS":
            buy_mult *= self.cautious_size_mult
            sell_mult *= self.cautious_size_mult
        elif decision.risk_mode in ("REDUCE_ONLY", "LIQUIDATE", "DISABLED"):
            if inventory.net_base > 0:
                buy_mult = 0.0
                sell_mult = min(self.max_side_size_mult, 1.25)
            elif inventory.net_base < 0:
                sell_mult = 0.0
                buy_mult = min(self.max_side_size_mult, 1.25)
            else:
                buy_mult = 0.0
                sell_mult = 0.0

        buy_mult = self._clip(buy_mult, 0.0, self.max_side_size_mult)
        sell_mult = self._clip(sell_mult, 0.0, self.max_side_size_mult)
        buy_size = self._round_order_size(
            common_size * decision.action.size_mult * buy_mult,
            volume_decimals,
        )
        sell_size = self._round_order_size(
            common_size * decision.action.size_mult * sell_mult,
            volume_decimals,
        )

        # Hard post-fill inventory caps in base units.
        max_buy = max(0.0, self.max_inventory_base - inventory.net_base)
        max_sell = max(0.0, self.max_inventory_base + inventory.net_base)
        buy_size = self._round_order_size(min(buy_size, max_buy), volume_decimals)
        sell_size = self._round_order_size(min(sell_size, max_sell), volume_decimals)
        return buy_size, sell_size

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
        del edge_bias  # signed bias is derived from profile/prediction.
        if not state.config or not book.bids or not book.asks:
            return 0
        if regime is None or archetype is None:
            return 0

        decision = self._quote_decision(
            book,
            profile,
            prediction,
            inventory,
            regime_params,
            regime,
            archetype,
            state.config.priceDecimals,
        )
        if decision is None or decision.risk_mode in ("LIQUIDATE", "DISABLED"):
            return 0

        best_bid = book.bids[0].price
        best_ask = book.asks[0].price
        spread = best_ask - best_bid
        mid = 0.5 * (best_bid + best_ask)
        common_size = self.dynamic_order_size(
            size,
            profile,
            regime_params,
            inventory,
            state.config.volumeDecimals,
            mid=mid,
        )
        if (
            self.enable_phase_mining
            and self._mining_phase == "CALIBRATE"
        ):
            common_size = self._round_order_size(
                common_size * 0.25, state.config.volumeDecimals
            )
        if common_size <= 0:
            return 0

        # CALIBRATE: only small safe round trips with strictly positive
        # expected FIFO PnL on clean books; misses count as phase skips.
        in_calibration = (
            self.enable_phase_mining and self._mining_phase == "CALIBRATE"
        )
        if in_calibration:
            est = self.estimate_round_trip_pnl(
                book_id,
                decision.bid_price,
                decision.ask_price,
                common_size,
                is_maker=self._prefer_maker(book_id),
                direction="SYMMETRIC",
                timestamp=state.timestamp,
            )
            if est.expected_realized_pnl < self.phase_min_expected_pnl:
                if stats is not None:
                    stats["skipped_phase"] = stats.get("skipped_phase", 0) + 1
                return 0

        fill_est = self.estimate_fill_probability(
            book,
            mid,
            spread,
            profile.trade_rate,
            decision.bid_price,
            decision.ask_price,
            book_id=book_id,
        )
        buy_size, sell_size = self._side_sizes(
            common_size,
            inventory,
            prediction,
            decision,
            state.config.volumeDecimals,
        )
        is_weak = self.enable_floor_awareness and book_id in self._last_weak_books
        is_left = (
            self.enable_floor_awareness
            and book_id in self._last_left_tail_books
            and not self.left_tail_new_risk_enabled
        )
        if is_weak:
            buy_size = self._round_order_size(
                buy_size * self.weak_book_size_mult, state.config.volumeDecimals
            )
            sell_size = self._round_order_size(
                sell_size * self.weak_book_size_mult, state.config.volumeDecimals
            )
        # Left-tail flat books: no fresh symmetric quoting (inventory repair only).
        if is_left and inventory.band == "FLAT":
            if stats is not None:
                stats["skipped_left_tail"] = stats.get("skipped_left_tail", 0) + 1
            return 0

        mem = self._mem(book_id)
        buy_ev = self._side_expected_value_bps(
            "buy", decision, fill_est.buy, inventory, mem
        )
        sell_ev = self._side_expected_value_bps(
            "sell", decision, fill_est.sell, inventory, mem
        )

        if stats is not None:
            stats["buy_ev_bps_sum"] = stats.get("buy_ev_bps_sum", 0.0) + buy_ev
            stats["sell_ev_bps_sum"] = stats.get("sell_ev_bps_sum", 0.0) + sell_ev

        buy_ok = (
            buy_size > 0
            and fill_est.buy >= regime_params.min_fill_prob
            and buy_ev > self.min_side_edge_bps
        )
        sell_ok = (
            sell_size > 0
            and fill_est.sell >= regime_params.min_fill_prob
            and sell_ev > self.min_side_edge_bps
        )

        # Quote-side EV gates: cautious / weak books — inventory-improving side only.
        if decision.risk_mode == "CAUTIOUS" or (is_weak and inventory.band != "FLAT"):
            if inventory.net_base > 0.0:
                buy_ok = False
            elif inventory.net_base < 0.0:
                sell_ok = False
        elif is_weak and inventory.band == "FLAT":
            # Avoid symmetric quoting unless both sides clear positive EV.
            if buy_ok and sell_ok:
                pass
            elif not buy_ok and not sell_ok:
                pass
            # single-side OK when flat + positive EV
        if is_left and inventory.band != "FLAT":
            if inventory.net_base > 0.0:
                buy_ok = False
            elif inventory.net_base < 0.0:
                sell_ok = False

        if not buy_ok and not sell_ok:
            if stats is not None:
                stats["skipped_side_ev"] = stats.get("skipped_side_ev", 0) + 1
            return 0

        notional = (
            (buy_size * decision.bid_price if buy_ok else 0.0)
            + (sell_size * decision.ask_price if sell_ok else 0.0)
        )
        if not self._can_add_volume(state, notional):
            return 0

        account = self.accounts[book_id]
        placed = 0
        expiry = max(
            1,
            int(self.mm_expiry_period * decision.action.expiry_mult),
        )

        if (
            buy_ok
            and account.quote_balance.free >= decision.bid_price * buy_size
            and self._count_book_instructions(response, book_id)
            < self.max_instructions_per_book
        ):
            client_id = self._next_mm_client_id("buy")
            response.limit_order(
                book_id=book_id,
                direction=OrderDirection.BUY,
                quantity=buy_size,
                price=decision.bid_price,
                clientOrderId=client_id,
                stp=STP.CANCEL_BOTH,
                postOnly=self._prefer_maker(book_id),
                timeInForce=TimeInForce.GTT,
                expiryPeriod=expiry,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )
            distance = self._distance_from_touch(
                "buy", decision.bid_price, best_bid, best_ask
            )
            self._record_active_order(
                ActiveOrderMeta(
                    book_id=book_id,
                    client_order_id=client_id,
                    side="buy",
                    price=decision.bid_price,
                    submitted_qty=buy_size,
                    submitted_ts=state.timestamp,
                    distance_from_touch=distance,
                    distance_bucket=self._spread_dist_bucket(distance),
                    action_id=decision.action.action_id,
                    context_key=decision.context_key,
                    fair_price=decision.fair_price,
                    reservation_price=decision.reservation_price,
                    inventory_before=inventory.net_base,
                    expected_value_bps=buy_ev,
                    was_weak=is_weak,
                    was_left_tail=is_left,
                )
            )
            mem.quote_count += 1
            placed += 1

        if (
            sell_ok
            and account.base_balance.free >= sell_size
            and self._count_book_instructions(response, book_id)
            < self.max_instructions_per_book
        ):
            client_id = self._next_mm_client_id("sell")
            response.limit_order(
                book_id=book_id,
                direction=OrderDirection.SELL,
                quantity=sell_size,
                price=decision.ask_price,
                clientOrderId=client_id,
                stp=STP.CANCEL_BOTH,
                postOnly=self._prefer_maker(book_id),
                timeInForce=TimeInForce.GTT,
                expiryPeriod=expiry,
                leverage=0.0,
                settlement_option=LoanSettlementOption.NONE,
                delay=0,
            )
            distance = self._distance_from_touch(
                "sell", decision.ask_price, best_bid, best_ask
            )
            self._record_active_order(
                ActiveOrderMeta(
                    book_id=book_id,
                    client_order_id=client_id,
                    side="sell",
                    price=decision.ask_price,
                    submitted_qty=sell_size,
                    submitted_ts=state.timestamp,
                    distance_from_touch=distance,
                    distance_bucket=self._spread_dist_bucket(distance),
                    action_id=decision.action.action_id,
                    context_key=decision.context_key,
                    fair_price=decision.fair_price,
                    reservation_price=decision.reservation_price,
                    inventory_before=inventory.net_base,
                    expected_value_bps=sell_ev,
                    was_weak=is_weak,
                    was_left_tail=is_left,
                )
            )
            mem.quote_count += 1
            placed += 1

        if placed:
            self._quoted_books_this_tick.add(book_id)
            if in_calibration:
                self._phase_calibrated_books.add(book_id)
            if self.log_predict_pnl and self.verbose_log:
                bt.logging.info(
                    "[CONSTRAINED_QUOTE] "
                    f"book={book_id} phase={self._mining_phase} "
                    f"risk={decision.risk_mode} action="
                    f"{decision.action.name} gamma={decision.gamma:.3f} "
                    f"tox={decision.toxicity:.3f} fair={decision.fair_price:.6f} "
                    f"reservation={decision.reservation_price:.6f} "
                    f"bid={decision.bid_price} ask={decision.ask_price} "
                    f"buy_ev={buy_ev:.3f} sell_ev={sell_ev:.3f} "
                    f"fill_b={fill_est.buy:.3f} fill_s={fill_est.sell:.3f}"
                )
        return placed

    # ------------------------------------------------------------------
    # Fill and delayed markout learning
    # ------------------------------------------------------------------

    def onTrade(self, event, validator: str | None = None) -> None:
        book_id = getattr(event, "bookId", None)
        super().onTrade(event, validator)
        if book_id is None:
            return
        # Only attribute maker fills for our uid to active-order learning.
        if self.uid != getattr(event, "makerAgentId", None):
            return

        client_id = getattr(event, "clientOrderId", None)
        if client_id is None:
            return
        meta = self._active_order_meta.get(client_id)
        if meta is None or meta.book_id != book_id:
            return

        qty = max(0.0, float(getattr(event, "quantity", 0.0) or 0.0))
        fill_price = float(getattr(event, "price", meta.price) or meta.price)
        timestamp = int(getattr(event, "timestamp", 0) or 0)
        meta.filled_qty += qty
        mem = self._mem(book_id)
        mem.filled_qty += qty
        if not meta.counted_fill:
            meta.counted_fill = True
            mem.filled_order_count += 1
            # Correct exact-order distance attribution.
            if meta.side == "buy":
                fills = list(mem.fill_buy_fills)
                fills[meta.distance_bucket] += 1
                mem.fill_buy_fills = tuple(fills)
            else:
                fills = list(mem.fill_sell_fills)
                fills[meta.distance_bucket] += 1
                mem.fill_sell_fills = tuple(fills)

        if qty > 0 and fill_price > 0:
            if len(self._pending_markouts) >= self.max_pending_markouts:
                self._pending_markouts.popleft()
            self._pending_markouts.append(
                PendingMarkout(
                    book_id=book_id,
                    side=meta.side,
                    fill_price=fill_price,
                    fill_qty=qty,
                    fill_ts=timestamp,
                    mature_ts=timestamp + self.markout_horizon_ns,
                    action_id=meta.action_id,
                    context_key=meta.context_key,
                    inventory_before=meta.inventory_before,
                    was_weak=meta.was_weak,
                    was_left_tail=meta.was_left_tail,
                )
            )

        # Policy reward uses delayed markout only (single channel).
        if meta.filled_qty + 1e-12 >= meta.submitted_qty:
            self._active_order_meta.pop(client_id, None)
        self._update_book_specialization(mem)

    def _update_mature_markouts(self, state: MarketSimulationStateUpdate) -> None:
        if not self._pending_markouts:
            return
        remaining: Deque[PendingMarkout] = deque()
        while self._pending_markouts:
            item = self._pending_markouts.popleft()
            if state.timestamp < item.mature_ts:
                remaining.append(item)
                continue
            if item.fill_price <= 0:
                continue
            book = state.books.get(item.book_id)
            if not book or not book.bids or not book.asks:
                # Book briefly empty — retry a few horizons, then drop.
                if state.timestamp <= item.mature_ts + 5 * self.markout_horizon_ns:
                    remaining.append(item)
                continue
            mid = 0.5 * (book.bids[0].price + book.asks[0].price)
            if item.side == "buy":
                markout_bps = (mid - item.fill_price) / item.fill_price * 10_000.0
            else:
                markout_bps = (item.fill_price - mid) / item.fill_price * 10_000.0
            mem = self._mem(item.book_id)
            alpha = self.markout_ema_alpha
            if item.side == "buy":
                mem.buy_markout_ema_bps = (
                    (1.0 - alpha) * mem.buy_markout_ema_bps + alpha * markout_bps
                )
            else:
                mem.sell_markout_ema_bps = (
                    (1.0 - alpha) * mem.sell_markout_ema_bps + alpha * markout_bps
                )
            mem.markout_samples += 1
            reward = self._floor_aware_policy_reward(markout_bps, item)
            self._update_policy_value(item.context_key, item.action_id, reward)
        self._pending_markouts = remaining

    # ------------------------------------------------------------------
    # Inventory execution safety
    # ------------------------------------------------------------------

    def _execute_aggressive_close(
        self,
        response: FinanceAgentResponse,
        book_id: int,
        book: Book,
        qty: float,
        long_pos: bool,
    ) -> bool:
        """Submit a close, but retain state until fills confirm flat inventory."""
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
            return True
        if close_dir == OrderDirection.BUY and book.asks:
            px = book.asks[0].price
            if account.quote_balance.free >= qty * px:
                response.market_order(
                    book_id=book_id,
                    direction=close_dir,
                    quantity=qty,
                    stp=STP.CANCEL_OLDEST,
                    delay=0,
                )
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
        qty = self._round_order_size(
            abs(inventory.net_base), state.config.volumeDecimals
        )
        if qty <= 0:
            return 0
        force = (
            self._inventory_util(inventory) >= self.liquidate_inventory_util
            or inventory.band in ("MAX_LONG", "MAX_SHORT")
            or (
                inventory.unrealized_bps is not None
                and inventory.unrealized_bps <= -self.hard_stop_loss_bps
            )
        )
        if force:
            return int(
                self._execute_aggressive_close(
                    response,
                    book_id,
                    book,
                    qty,
                    inventory.net_base > 0,
                )
            )
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

    # ------------------------------------------------------------------
    # Full strategy orchestration: inventory first, entries second
    # ------------------------------------------------------------------

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
            "skipped_side_ev": 0,
            "skipped_left_tail": 0,
            "skipped_phase": 0,
            "cancelled_risk_orders": 0,
            "mm_candidates": 0,
            "alpha_ranked": 0,
            "instructions": 0,
            "trading_proxy": 0.0,
            "soft_floor_proxy": 0.0,
            "trading_score_proxy": 0.0,
            "soft_floor_score_proxy": 0.0,
            "rolling_kappa_proxy": 0.0,
            "rolling_pnl_proxy": 0.0,
            "score_to_median": 0.0,
            "score_to_internal_median": 0.0,
            "score_to_internal_median_ratio": 0.0,
            "eligible_books": 0,
            "expiring_strong_books": 0,
            "weak_books": 0,
            "left_tail_books": 0,
            "top_rank_pressure": 0,
            "risk_NORMAL": 0,
            "risk_CAUTIOUS": 0,
            "risk_REDUCE_ONLY": 0,
            "risk_LIQUIDATE": 0,
            "risk_DISABLED": 0,
            "mining_phase": self._mining_phase,
            "phase_elapsed_ns": 0,
            "phase_reason": self._phase_reason,
        }
        self._quoted_books_this_tick.clear()
        self._inventory_aged_books = set()
        regime_params = self.get_regime_params(regime)
        avoid_set = set(selection.avoid_books)
        profile_by_id = {profile.book_id: profile for profile in selection.profiles}
        maintenance_set = set(
            self._schedule_maintenance_books(
                selection,
                state.timestamp,
                limit=self.max_maintenance_books_per_tick,
            )
        )

        floor_state: dict = {
            "book_scores": {},
            "estimated_trading_score": 0.0,
            "estimated_soft_floor_score": 0.0,
            "estimated_score_to_median": 0.0,
            "floor_threshold": 0.0,
            "weak_books": set(),
            "left_tail_books": set(),
            "below_guard": False,
        }
        if self.enable_floor_awareness or self.enable_phase_mining:
            floor_state = self._evaluate_score_floor(selection.profiles)
            stats["trading_proxy"] = round(
                float(floor_state["estimated_trading_score"]), 6
            )
            stats["soft_floor_proxy"] = round(
                float(floor_state["estimated_soft_floor_score"]), 6
            )
            stats["trading_score_proxy"] = stats["trading_proxy"]
            stats["soft_floor_score_proxy"] = stats["soft_floor_proxy"]
            stats["rolling_kappa_proxy"] = round(
                float(floor_state.get("rolling_kappa_proxy", 0.0)), 6
            )
            stats["rolling_pnl_proxy"] = round(
                float(floor_state.get("rolling_pnl_proxy", 0.0)), 6
            )
            stats["score_to_median"] = round(
                float(floor_state["estimated_score_to_median"]), 6
            )
            stats["score_to_internal_median"] = round(
                float(floor_state.get("score_to_internal_median", 0.0)), 6
            )
            stats["score_to_internal_median_ratio"] = stats["score_to_internal_median"]
            stats["eligible_books"] = len(floor_state.get("eligible_books", set()))
            stats["expiring_strong_books"] = len(
                floor_state.get("expiring_strong_books", set())
            )
            stats["top_rank_pressure"] = int(bool(floor_state.get("top_rank_pressure")))
            stats["weak_books"] = len(floor_state["weak_books"])
            stats["left_tail_books"] = len(floor_state["left_tail_books"])

        # Advance the phase FSM with this tick's refreshed floor proxies.
        self._update_mining_phase(state, regime)
        stats["mining_phase"] = self._mining_phase
        stats["phase_elapsed_ns"] = self._phase_elapsed(state.timestamp)
        stats["phase_reason"] = self._phase_reason

        weak_books: set[int] = floor_state["weak_books"]  # type: ignore[assignment]
        left_tail_books: set[int] = floor_state["left_tail_books"]  # type: ignore[assignment]

        archetype_rows: list[dict] = []
        manage_queue: list[tuple] = []
        mm_candidates: list[tuple] = []
        alpha_candidates: list[tuple] = []

        for book_id, book in state.books.items():
            if not book.bids or not book.asks:
                continue
            mid = 0.5 * (book.bids[0].price + book.asks[0].price)
            inventory = self._net_inventory(book_id, mid)
            profile = profile_by_id.get(book_id)
            prediction = predictions.get(book_id)

            # Phase hard-stop applies even when profile/prediction is missing:
            # cancel all live orders and queue every non-flat book for repair.
            if (
                self.enable_phase_mining
                and self._mining_phase in ("OBSERVE", "COOLDOWN")
            ):
                n_cancel = self._cancel_open_orders(response, book_id)
                if n_cancel:
                    stats["cancelled_risk_orders"] += 1
                    stats["instructions"] += n_cancel
                phase_mode = "REDUCE_ONLY" if inventory.band != "FLAT" else "DISABLED"
                stats[f"risk_{phase_mode}"] += 1
                stats["skipped_phase"] += 1
                if inventory.band != "FLAT":
                    phase_archetype: BookArchetype = (
                        self.classify_book_archetype(profile, regime)
                        if profile is not None
                        else "STRESSED"
                    )
                    phase_params = (
                        self.merge_regime_and_archetype_params(
                            regime_params, phase_archetype
                        )
                        if profile is not None
                        else regime_params
                    )
                    manage_queue.append(
                        (
                            12.0 + self._inventory_util(inventory),
                            book_id,
                            book,
                            inventory,
                            phase_params,
                            phase_archetype,
                        )
                    )
                continue

            # Existing risk is always handled before avoid/profile/entry gates.
            if profile is None or prediction is None:
                if inventory.band != "FLAT":
                    manage_queue.append(
                        (
                            10.0 + self._inventory_util(inventory),
                            book_id,
                            book,
                            inventory,
                            regime_params,
                            "STRESSED",
                        )
                    )
                continue

            archetype = self.classify_book_archetype(profile, regime)
            book_params = self.merge_regime_and_archetype_params(
                regime_params, archetype
            )
            mem = self._mem(book_id)
            risk_mode, toxicity = self._risk_mode(
                inventory, profile, prediction, mem, archetype
            )
            base_risk_mode = risk_mode
            risk_mode = self._phase_adjusted_risk_mode(
                risk_mode, inventory, profile, mem
            )
            if risk_mode != base_risk_mode:
                stats["skipped_phase"] += 1
            stats[f"risk_{risk_mode}"] = stats.get(f"risk_{risk_mode}", 0) + 1
            if collect_archetypes:
                archetype_rows.append(
                    {
                        "book": book_id,
                        "arch": archetype,
                        "risk": risk_mode,
                        "tox": round(toxicity, 3),
                        "tier": profile.tier,
                        "inv_util": round(self._inventory_util(inventory), 3),
                        "fill": round(mem.fill_rate, 3),
                        "qfill": round(mem.quantity_fill_rate, 3),
                        "buy_mo": round(mem.buy_markout_ema_bps, 3),
                        "sell_mo": round(mem.sell_markout_ema_bps, 3),
                        "floor": round(self._last_book_scores.get(book_id, 0.0), 3),
                    }
                )

            if inventory.band != "FLAT" and (
                self._inventory_needs_management(inventory)
                or risk_mode in ("REDUCE_ONLY", "LIQUIDATE")
                or book_id in avoid_set
                or (
                    book_id in left_tail_books
                    and not self.left_tail_new_risk_enabled
                )
            ):
                n_cancel = self._cancel_open_orders(response, book_id)
                if n_cancel:
                    stats["cancelled_risk_orders"] += 1
                    stats["instructions"] += n_cancel
                urgency = self._inventory_urgency(
                    inventory, book_params, regime, archetype
                )
                if risk_mode == "LIQUIDATE":
                    urgency += 10.0
                elif risk_mode == "REDUCE_ONLY":
                    urgency += 4.0
                if book_id in left_tail_books:
                    urgency += 6.0
                manage_queue.append(
                    (
                        urgency,
                        book_id,
                        book,
                        inventory,
                        book_params,
                        archetype,
                    )
                )
                continue

            if book_id in avoid_set:
                stats["skipped_avoid"] += 1
                n_cancel = self._cancel_open_orders(response, book_id)
                if n_cancel:
                    stats["cancelled_risk_orders"] += 1
                    stats["instructions"] += n_cancel
                continue

            toxic = self.is_toxic_book(book_id, profile, archetype)
            if risk_mode in ("DISABLED", "LIQUIDATE", "REDUCE_ONLY"):
                stats["skipped_toxic"] += 1
                n_cancel = self._cancel_open_orders(response, book_id)
                if n_cancel:
                    stats["cancelled_risk_orders"] += 1
                    stats["instructions"] += n_cancel
                continue

            # Coverage orders are allowed only when flat and economically safe.
            if (
                book_id in maintenance_set
                and (
                    not self.enable_phase_mining
                    or self._mining_phase == "ACTIVE"
                )
            ):
                if inventory.band != "FLAT":
                    stats["skipped_small_inv"] += 1
                    continue
                if book_id in left_tail_books or book_id in weak_books:
                    stats["skipped_left_tail"] += 1
                    continue
                if not self._maintenance_allowed(profile, archetype):
                    stats["skipped_maint_arch"] += 1
                    continue
                if toxic or risk_mode != "NORMAL":
                    stats["skipped_toxic"] += 1
                    n_cancel = self._cancel_open_orders(response, book_id)
                    if n_cancel:
                        stats["cancelled_risk_orders"] += 1
                        stats["instructions"] += n_cancel
                    continue
                maintenance_size = (
                    self.maintenance_order_size * self.maintenance_size_mult
                )
                n = self._place_round_trip_limits(
                    response,
                    state,
                    book_id,
                    maintenance_size,
                    post_only=True,
                    expiry_period=state.config.publish_interval,
                    client_id_base=MAINT_CLIENT_ID_BASE,
                )
                if n:
                    mem.quote_count += n
                    mem.last_quote_ts = state.timestamp
                    stats["maintenance"] += 1
                    stats["instructions"] += n
                continue

            if toxic and risk_mode != "CAUTIOUS":
                stats["skipped_toxic"] += 1
                n_cancel = self._cancel_open_orders(response, book_id)
                if n_cancel:
                    stats["cancelled_risk_orders"] += 1
                    stats["instructions"] += n_cancel
                continue
            if not book_params.quote_enabled:
                stats["skipped_archetype"] += 1
                n_cancel = self._cancel_open_orders(response, book_id)
                if n_cancel:
                    stats["cancelled_risk_orders"] += 1
                    stats["instructions"] += n_cancel
                continue
            if self.mm_skip_inactive_tier and profile.tier == "INACTIVE":
                stats["skipped_low_alpha"] += 1
                continue
            if (
                book_id in left_tail_books
                and not self.left_tail_new_risk_enabled
            ):
                stats["skipped_left_tail"] += 1
                continue

            spread = book.asks[0].price - book.bids[0].price
            fill_est = self.estimate_fill_probability(
                book,
                mid,
                spread,
                profile.trade_rate,
                book.bids[0].price,
                book.asks[0].price,
                book_id=book_id,
            )
            expected_alpha = self.expected_alpha_score(
                profile,
                prediction,
                fill_est,
                mem,
                book_id,
                state.timestamp,
            )
            # Economic signal must contribute; coverage/prior alone cannot pass.
            economic_signal = 0.55 * min(1.0, abs(prediction.score)) + 0.45 * (
                0.5 * (fill_est.buy + fill_est.sell)
            )
            if (
                expected_alpha < self.min_expected_alpha
                or economic_signal < self.min_economic_signal
            ):
                stats["skipped_low_alpha"] += 1
                continue
            rank = self._global_book_rank(expected_alpha, mem) + 0.20 * economic_signal
            # Prefer stronger floor books for relative-rank emissions.
            book_floor = self._last_book_scores.get(book_id)
            if book_floor is not None:
                rank += 0.15 * book_floor
            if book_id in weak_books:
                rank *= 0.85
            mm_candidates.append(
                (
                    rank,
                    book_id,
                    book,
                    profile,
                    prediction,
                    inventory,
                    book_params,
                    archetype,
                )
            )

        manage_queue.sort(key=lambda row: row[0], reverse=True)
        for _, book_id, book, inventory, params, archetype in manage_queue[
            : self.max_managed_books_per_tick
        ]:
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
                stats["instructions"] += n

        mm_candidates.sort(key=lambda row: row[0], reverse=True)
        stats["mm_candidates"] = len(mm_candidates)
        for (
            _,
            book_id,
            book,
            profile,
            prediction,
            inventory,
            params,
            archetype,
        ) in mm_candidates[: self.max_mm_books_per_tick]:
            n = self._place_skewed_quotes(
                response,
                state,
                book_id,
                book,
                profile,
                prediction,
                inventory,
                params,
                self.mm_base_size,
                0.0,
                stats=stats,
                regime=regime,
                archetype=archetype,
            )
            if n:
                stats["quoted"] += 1
                stats["instructions"] += n

        # Optional separate alpha branch. Default is off because directional alpha
        # is already expressed through fair-price displacement. Fresh directional
        # risk is only permitted in the ACTIVE phase.
        if (
            self.enable_separate_alpha
            and (not self.enable_phase_mining or self._mining_phase == "ACTIVE")
            and regime_params.alpha_enabled
            and self._alpha_regime_allows(regime)
        ):
            for book_id in selection.alpha_books:
                if (
                    book_id in avoid_set
                    or book_id in self._quoted_books_this_tick
                    or book_id not in state.books
                ):
                    continue
                book = state.books[book_id]
                profile = profile_by_id.get(book_id)
                prediction = predictions.get(book_id)
                if (
                    not profile
                    or not prediction
                    or prediction.direction == "HOLD"
                    or not book.bids
                    or not book.asks
                ):
                    continue
                mid = 0.5 * (book.bids[0].price + book.asks[0].price)
                inventory = self._net_inventory(book_id, mid)
                if inventory.band != "FLAT":
                    continue
                archetype = self.classify_book_archetype(profile, regime)
                mem = self._mem(book_id)
                mode, toxicity = self._risk_mode(
                    inventory, profile, prediction, mem, archetype
                )
                if mode != "NORMAL" or toxicity >= self.toxicity_cautious_threshold:
                    continue
                alpha_candidates.append(
                    (abs(prediction.score), book_id, prediction, profile, archetype)
                )

            alpha_candidates.sort(key=lambda row: row[0], reverse=True)
            stats["alpha_ranked"] = len(alpha_candidates)
            for _, book_id, prediction, profile, archetype in alpha_candidates[
                : self.max_alpha_books_per_tick
            ]:
                params = self.merge_regime_and_archetype_params(
                    regime_params, archetype
                )
                size = self.dynamic_order_size(
                    self.alpha_order_size,
                    profile,
                    params,
                    InventorySnapshot(0, 0, "FLAT", None, None, 0),
                    state.config.volumeDecimals,
                    mid=profile.mid,
                )
                n = self._place_directional_round_trip(
                    response,
                    state,
                    book_id,
                    "UP" if prediction.direction == "UP" else "DOWN",
                    size,
                    client_id_base=ALPHA_CLIENT_ID_BASE,
                    stats=stats,
                )
                if n:
                    mem = self._mem(book_id)
                    mem.quote_count += n
                    mem.last_quote_ts = state.timestamp
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
        if self.enable_floor_awareness:
            bt.logging.info(
                "[SCORING_PROXY] "
                f"rolling_kappa_proxy={stats.get('rolling_kappa_proxy', 0.0)} "
                f"rolling_pnl_proxy={stats.get('rolling_pnl_proxy', 0.0)} "
                f"trading_score_proxy={stats.get('trading_score_proxy', 0.0)} "
                f"soft_floor_score_proxy={stats.get('soft_floor_score_proxy', 0.0)} "
                f"score_to_internal_median={stats.get('score_to_internal_median', 0.0)} "
                f"eligible_books={stats.get('eligible_books', 0)} "
                f"weak_books={stats.get('weak_books', 0)} "
                f"left_tail_books={stats.get('left_tail_books', 0)} "
                f"expiring_strong_books={stats.get('expiring_strong_books', 0)} "
                f"top_rank_pressure={stats.get('top_rank_pressure', 0)} "
                f"risk_mode={{NORMAL:{stats.get('risk_NORMAL', 0)},"
                f"CAUTIOUS:{stats.get('risk_CAUTIOUS', 0)},"
                f"REDUCE_ONLY:{stats.get('risk_REDUCE_ONLY', 0)},"
                f"LIQUIDATE:{stats.get('risk_LIQUIDATE', 0)},"
                f"DISABLED:{stats.get('risk_DISABLED', 0)}}}"
            )
        if self.enable_phase_mining:
            bt.logging.info(
                "[PHASE_POLICY] "
                f"mining_phase={stats.get('mining_phase', self._mining_phase)} "
                f"phase_elapsed_ns={stats.get('phase_elapsed_ns', 0)} "
                f"phase_reason={stats.get('phase_reason', self._phase_reason)} "
                f"calibrated_books={len(self._phase_calibrated_books)} "
                f"skipped_phase={stats.get('skipped_phase', 0)} "
                f"risk_mode_counts={{NORMAL:{stats.get('risk_NORMAL', 0)},"
                f"CAUTIOUS:{stats.get('risk_CAUTIOUS', 0)},"
                f"REDUCE_ONLY:{stats.get('risk_REDUCE_ONLY', 0)},"
                f"LIQUIDATE:{stats.get('risk_LIQUIDATE', 0)},"
                f"DISABLED:{stats.get('risk_DISABLED', 0)}}} "
                f"internal_score_proxy={stats.get('trading_proxy', 0.0)} "
                f"soft_floor_proxy={stats.get('soft_floor_proxy', 0.0)} "
                f"score_to_internal_median={stats.get('score_to_median', 0.0)} "
                f"score_to_internal_median_ratio="
                f"{stats.get('score_to_internal_median_ratio', 0.0)} "
                f"weak_books={stats.get('weak_books', 0)} "
                f"left_tail_books={stats.get('left_tail_books', 0)}"
            )

    # ------------------------------------------------------------------
    # Response and latency instrumentation
    # ------------------------------------------------------------------

    def handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        started = time.perf_counter()
        try:
            return super().handle(state)
        finally:
            self._last_response_latency_ms = (
                time.perf_counter() - started
            ) * 1_000.0

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        response = FinanceAgentResponse(agent_id=self.uid)
        self._tick += 1
        self._update_mature_markouts(state)

        log_tick = self._tick == 1 or self._tick % self.log_every_n == 0
        need_summary = log_tick and (self.verbose_log or self.log_momentum_pnl)
        summary = self.parse_state(state) if need_summary else None

        predictions = self._predict_all_books(state)
        selection = self.select_books_for_trading(state, predictions)
        regime = self.classify_market_regime_from_profiles(
            selection.profiles,
            predictions,
            selection,
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
            summary.grace_period_ns
            if summary
            else (state.config.grace_period if state.config else 0)
        )
        in_grace = state.timestamp < grace_period_ns
        if state.books and not in_grace:
            if self.enable_mm_strategy:
                stats = self.build_mm_strategy_instructions(
                    response,
                    state,
                    selection,
                    predictions,
                    regime,
                    collect_archetypes=self.log_mm_strategy and log_tick,
                )
                self._accumulate_tuning_window(stats)
                if self.log_mm_strategy and log_tick:
                    self._log_mm_strategy(stats, regime)
            elif self.enable_kappa_strategy:
                strategy_stats = self.build_kappa_strategy_instructions(
                    response, state, selection, predictions, regime
                )
                if self.log_kappa_strategy and log_tick:
                    self._log_kappa_strategy_calibration(
                        state, selection, regime, strategy_stats
                    )
            elif self.enable_trading:
                self.build_demo_instructions(response, state, book_id=0)
        elif state.books and in_grace and (
            self.enable_mm_strategy or self.enable_kappa_strategy or self.enable_trading
        ):
            bt.logging.info(
                f"Grace period active (T={state.timestamp} < {grace_period_ns}); "
                "no orders placed."
            )

        if self.verbose_log and response.instructions and log_tick:
            self._log_output(self.parse_response(response))
        if self.monitor_top_miners:
            try:
                from top_miner_monitor import write_tick_tap

                write_tick_tap(
                    state,
                    self._tick,
                    self.output_dir,
                    self.uid,
                    self.monitor_top_n,
                )
            except Exception as exc:
                bt.logging.warning(f"monitor tap failed: {exc}")
        return response


if __name__ == "__main__":
    launch(Strategy4)
