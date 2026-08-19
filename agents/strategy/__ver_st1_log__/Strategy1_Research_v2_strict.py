# SPDX-License-Identifier: MIT
"""Strategy1 research V2: diagnostics plus bootstrap/round-trip correctness fixes.

Requires Strategy1_Debug.py beside this file.

The production Strategy1 parent remains untouched. This research subclass keeps the
existing async [S1R_*] telemetry and intentionally changes only the policy defects
identified from the testnet research log:

1. Global STRESSED no longer forces every individual book to STRESSED.
2. Neutral per-book archetype fallback is MM_BOOK rather than TOXIC_BOOK.
3. Spread stress/toxicity cutoffs adapt cross-sectionally (P95/P99 by default).
4. INACTIVE books may bootstrap while SCORING_PRESSURE is active.
5. Global STRESSED becomes conservative quote mode instead of a total market kill.
6. The simulator min order size is synchronized per request; sub-minimum dynamic
   sizes may be promoted only when the remaining base-inventory headroom permits it.
7. One [S1R_REGIME] record and detailed per-book toxicity decomposition are emitted.
8. Cold INACTIVE books are not mislabeled DEAD solely from bootstrap trade-rate.
9. Inventory is represented as signed base utilization (net_base / max_inventory_base).
10. Quote skew uses a reservation-price shift instead of the legacy sign-confused formula.
11. Any executable bootstrap position is managed immediately; an optional aggressive close
    after the age gate requires non-negative estimated close-at-touch economics after buffer.
12. Dust is quarantined before normal quote selection, exact flatness follows volume precision,
    and [S1R_POSITION] exposes OPEN/INCREASE/REDUCE/FLAT transitions.
13. Quote selection backfills failed top-ranked candidates up to a bounded attempt cap while
    preserving the original successful quote-book cap.
14. Recent-PnL toxicity requires enough completed samples unless loss crosses a hard floor, and
    YELLOW sparse-tape books retain a conservative active path instead of automatic DEAD status.

The production Strategy1 parent remains untouched; this subclass intentionally changes the
above research-policy and correctness paths while retaining Strategy1 signals and ranking.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import sys
import threading
import time
from typing import Any

_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

from taos.common.agents import launch
from taos.im.protocol import FinanceAgentResponse, MarketSimulationStateUpdate
from taos.im.protocol.models import Book, OrderDirection, STP
from Strategy1 import (
    BookArchetype,
    BookProfile,
    BookSelection,
    DirectionForecast,
    InventorySnapshot,
    MarketRegime,
    RegimeParamSet,
)
from Strategy1_Debug import DebugReason, Strategy1_Debug


class Strategy1_Research(Strategy1_Debug):
    REASON_ALIAS = {
        "LOW_EXPECTED_ALPHA": "ALPHA",
        "ZERO_ORDER_SIZE": "SIZE_ZERO",
        "MAX_INVENTORY": "INVENTORY_MAX",
        "INVALID_QUOTE_PRICES": "BAD_PRICE",
        "VOLUME_CAP": "VOLUME_CAP",
        "NON_POSITIVE_EDGE": "EDGE",
        "NEGATIVE_EXPECTED_PNL": "NEG_PNL",
        "LOW_FILL_PROBABILITY": "FILL_PROB",
        "INSTRUCTION_LIMIT": "INSTR_LIMIT",
        "INSUFFICIENT_BALANCE": "BALANCE",
        "QUOTE_ORDER_GATE": "QUOTE_GATE",
        "QUOTE_DISABLED": "REGIME_DISABLED",
        "TOXIC_BOOK": "TOXIC",
        "TOXIC_REGIME": "TOXIC_REGIME",
        "INACTIVE_TIER": "INACTIVE",
        "MM_CANDIDATE_LIMIT": "MM_LIMIT",
        "MANAGEMENT_LIMIT": "MANAGEMENT_LIMIT",
        "MANAGE_ORDER_GATE": "MANAGE_GATE",
        "MAINT_INVENTORY_NONFLAT": "MAINT_INVENTORY",
        "MAINT_ARCHETYPE_BLOCK": "MAINT_ARCHETYPE",
        "MAINT_ORDER_GATE": "MAINT_GATE",
        "NO_BOOK_SIDES": "NO_BOOK_SIDES",
        "NO_PROFILE": "NO_PROFILE",
        "AVOID_LIST": "AVOID",
        "NO_PREDICTION": "NO_PREDICTION",
        "GRACE_PERIOD": "GRACE",
        "NO_ACTION": "NO_ACTION",
        # Reserved for descendants; base Strategy1 does not currently emit these.
        "HARD_CAP": "HARD_CAP",
        "STALE": "STALE",
        "DUST": "DUST",
        "DUST_POSITION": "DUST",
        "INACTIVE_DIAGNOSTIC_ONLY": "INACTIVE_DIAG",
        "MM_SUCCESS_CAP": "MM_CAP",
    }

    def initialize(self) -> None:
        # Strategy1_Debug.initialize() calls self._emit(), so prepare an early buffer.
        self._research_ready = False
        self._research_early: list[dict[str, Any]] = []
        self._rq = None
        self._rstop = None
        self._rworker = None
        self._rfile = None
        self._rdropped = 0
        super().initialize()

        cfg = self.config

        # --------------------------------------------------------------
        # Research policy: break the STRESSED/TOXIC/INACTIVE deadlock.
        # Defaults are intentionally explicit and can be overridden via
        # --agent.params without editing Strategy1.
        # --------------------------------------------------------------
        self.research_fix_global_stress = self._as_bool(
            getattr(cfg, "research_fix_global_stress", True)
        )
        self.research_neutral_fallback = self._as_bool(
            getattr(cfg, "research_neutral_fallback", True)
        )
        self.research_adaptive_spread_thresholds = self._as_bool(
            getattr(cfg, "research_adaptive_spread_thresholds", True)
        )
        self.research_stress_percentile = min(
            0.999, max(0.50, float(getattr(cfg, "research_stress_percentile", 0.95)))
        )
        self.research_toxic_percentile = min(
            0.9999, max(
                self.research_stress_percentile,
                float(getattr(cfg, "research_toxic_percentile", 0.99)),
            )
        )
        self.research_stress_floor_bps = max(
            0.0, float(getattr(cfg, "research_stress_floor_bps", 8.0))
        )
        self.research_toxic_floor_bps = max(
            0.0, float(getattr(cfg, "research_toxic_floor_bps", 10.0))
        )
        self.research_stress_fallback_bps = max(
            self.research_stress_floor_bps,
            float(getattr(cfg, "research_stress_fallback_bps", 35.0)),
        )
        self.research_toxic_fallback_bps = max(
            self.research_toxic_floor_bps,
            float(getattr(cfg, "research_toxic_fallback_bps", 40.0)),
        )
        self.research_toxic_gap_bps = max(
            0.0, float(getattr(cfg, "research_toxic_gap_bps", 2.0))
        )
        self.research_min_profiles_for_adaptive = max(
            4, int(getattr(cfg, "research_min_profiles_for_adaptive", 16))
        )
        self.research_inactive_bootstrap = self._as_bool(
            getattr(cfg, "research_inactive_bootstrap", True)
        )
        self.research_trade_global_stress = self._as_bool(
            getattr(cfg, "research_trade_global_stress", True)
        )
        self.research_global_stress_size_mult = min(
            1.0, max(
                0.05,
                float(getattr(cfg, "research_global_stress_size_mult", 0.35)),
            )
        )
        self.research_sync_min_order = self._as_bool(
            getattr(cfg, "research_sync_min_order", True)
        )
        self.research_promote_min_order = self._as_bool(
            getattr(cfg, "research_promote_min_order", True)
        )
        self.research_bootstrap_maintenance_min_order = self._as_bool(
            getattr(cfg, "research_bootstrap_maintenance_min_order", True)
        )
        # V2: the latest testnet log showed 100% INACTIVE, ~89% DEAD_BOOK and
        # fills without any confirmed flat round-trip. These switches target
        # exactly that second-stage bootstrap deadlock.
        self.research_bootstrap_dead_as_mm = self._as_bool(
            getattr(cfg, "research_bootstrap_dead_as_mm", True)
        )
        self.research_bootstrap_extreme_vol_mult = max(
            1.0, float(getattr(cfg, "research_bootstrap_extreme_vol_mult", 1.75))
        )
        self.research_fix_inventory_util = self._as_bool(
            getattr(cfg, "research_fix_inventory_util", True)
        )
        self.research_fix_quote_reservation = self._as_bool(
            getattr(cfg, "research_fix_quote_reservation", True)
        )
        self.research_bootstrap_manage_min_clip = self._as_bool(
            getattr(cfg, "research_bootstrap_manage_min_clip", True)
        )
        self.research_bootstrap_allow_aggressive_close = self._as_bool(
            getattr(cfg, "research_bootstrap_allow_aggressive_close", True)
        )
        self.research_bootstrap_force_close_ticks = max(
            1, int(getattr(cfg, "research_bootstrap_force_close_ticks", 60))
        )
        self.research_bootstrap_force_close_min_bps = float(
            getattr(cfg, "research_bootstrap_force_close_min_bps", -5.0)
        )
        self.research_bootstrap_hard_close_ticks = max(
            self.research_bootstrap_force_close_ticks,
            int(getattr(cfg, "research_bootstrap_hard_close_ticks", 180)),
        )
        self.research_dust_safe_close = self._as_bool(
            getattr(cfg, "research_dust_safe_close", True)
        )
        self.research_rotate_jsonl = self._as_bool(
            getattr(cfg, "research_rotate_jsonl", True)
        )
        # --------------------------------------------------------------
        # Strict V2 execution-quality fixes, all derived from the completed
        # research run. Keep them independently switchable for A/B testing.
        # --------------------------------------------------------------
        self.research_candidate_backfill = self._as_bool(
            getattr(cfg, "research_candidate_backfill", True)
        )
        self.research_candidate_attempt_cap = max(
            1, int(getattr(cfg, "research_candidate_attempt_cap", 12))
        )
        self.research_aggressive_close_touch_gate = self._as_bool(
            getattr(cfg, "research_aggressive_close_touch_gate", True)
        )
        self.research_aggressive_close_fee_buffer_bps = max(
            0.0, float(getattr(cfg, "research_aggressive_close_fee_buffer_bps", 3.0))
        )
        self.research_aggressive_close_min_net_bps = float(
            getattr(cfg, "research_aggressive_close_min_net_bps", 0.0)
        )
        self.research_toxic_pnl_min_samples = max(
            0, int(getattr(cfg, "research_toxic_pnl_min_samples", 3))
        )
        self.research_toxic_pnl_hard_floor = float(
            getattr(cfg, "research_toxic_pnl_hard_floor", -0.05)
        )
        self.research_yellow_sparse_active = self._as_bool(
            getattr(cfg, "research_yellow_sparse_active", True)
        )
        self.research_run_id = time.strftime("%Y%m%d_%H%M%S", time.localtime())

        self._research_stress_spread_bps = self.research_stress_fallback_bps
        self._research_toxic_spread_bps = max(
            self.research_toxic_fallback_bps,
            self._research_stress_spread_bps + self.research_toxic_gap_bps,
        )
        self._research_exchange_min_order_size = max(
            0.0, float(getattr(self, "min_order_size", 0.0) or 0.0)
        )
        self._research_bootstrap_active = False
        self._research_position_tick_seen: dict[int, int] = {}
        self._research_round_trip_closes = 0
        self._research_position_opens = 0
        self._research_position_reductions = 0
        self._research_dust_blocks = 0
        self._research_round_trip_samples_by_book: dict[int, int] = {}
        self._research_volume_decimals = 8
        self._research_backfill_active = False
        self._research_quote_success_cap = 0
        self._research_quote_successes = 0
        self._research_quote_attempts = 0
        self._research_aggressive_context: dict[int, dict[str, Any]] = {}

        self.research_enabled = self._env_bool(
            "STRATEGY1_RESEARCH", self._as_bool(getattr(cfg, "research_enabled", True))
        )
        self.research_every_n = max(1, self._env_int(
            "STRATEGY1_RESEARCH_EVERY_N", int(getattr(cfg, "research_every_n", 1))
        ))
        self.research_book_id = self._env_int(
            "STRATEGY1_RESEARCH_BOOK", int(getattr(cfg, "research_book_id", -1))
        )
        self.research_console = self._env_bool(
            "STRATEGY1_RESEARCH_CONSOLE", self._as_bool(getattr(cfg, "research_console", True))
        )
        self.research_jsonl = self._env_bool(
            "STRATEGY1_RESEARCH_JSONL", self._as_bool(getattr(cfg, "research_jsonl", True))
        )
        self.research_queue_size = max(256, self._env_int(
            "STRATEGY1_RESEARCH_QUEUE", int(getattr(cfg, "research_queue_size", 8192))
        ))
        env_dir = os.getenv("STRATEGY1_RESEARCH_DIR", "").strip()
        configured = str(getattr(cfg, "research_output_dir", "") or "")
        self.research_output_dir = env_dir or configured or os.path.join(
            self.output_dir, "strategy1_research"
        )

        self._rq = queue.Queue(maxsize=self.research_queue_size)
        self._rstop = threading.Event()
        self._research_output_file = None
        if self.research_enabled and self.research_jsonl:
            try:
                os.makedirs(self.research_output_dir, exist_ok=True)
                filename = (
                    f"strategy1_research_agent_{self.uid}_{self.research_run_id}.jsonl"
                    if self.research_rotate_jsonl
                    else f"strategy1_research_agent_{self.uid}.jsonl"
                )
                path = os.path.join(self.research_output_dir, filename)
                self._research_output_file = path
                self._rfile = open(path, "a", encoding="utf-8", buffering=1)
            except OSError as exc:
                print(f"[S1R_ERROR] stage=init_jsonl error={self._short(exc)}", flush=True)

        if self.research_enabled:
            self._rworker = threading.Thread(
                target=self._writer_loop,
                name=f"s1r-{getattr(self, 'uid', 'agent')}",
                daemon=True,
            )
            self._rworker.start()
            atexit.register(self._shutdown_research)

        self._research_ready = True
        for record in self._research_early:
            self._enqueue(record)
        self._research_early.clear()
        self._enqueue({
            "type": "RESEARCH_CONFIG",
            "agent_id": getattr(self, "uid", None),
            "wall_time_ns": time.time_ns(),
            "enabled": self.research_enabled,
            "every_n": self.research_every_n,
            "book_filter": self.research_book_id,
            "console": self.research_console,
            "jsonl": self.research_jsonl,
            "queue_size": self.research_queue_size,
            "output_dir": self.research_output_dir,
            "policy_version": "deadlock_fix_v2_strict",
            "fix_global_stress": self.research_fix_global_stress,
            "neutral_fallback": self.research_neutral_fallback,
            "adaptive_spread_thresholds": self.research_adaptive_spread_thresholds,
            "stress_percentile": self.research_stress_percentile,
            "toxic_percentile": self.research_toxic_percentile,
            "inactive_bootstrap": self.research_inactive_bootstrap,
            "trade_global_stress": self.research_trade_global_stress,
            "sync_min_order": self.research_sync_min_order,
            "promote_min_order": self.research_promote_min_order,
            "bootstrap_dead_as_mm": self.research_bootstrap_dead_as_mm,
            "fix_inventory_util": self.research_fix_inventory_util,
            "fix_quote_reservation": self.research_fix_quote_reservation,
            "bootstrap_manage_min_clip": self.research_bootstrap_manage_min_clip,
            "bootstrap_force_close_ticks": self.research_bootstrap_force_close_ticks,
            "legacy_force_close_min_bps": self.research_bootstrap_force_close_min_bps,
            "legacy_hard_close_ticks": self.research_bootstrap_hard_close_ticks,
            "aggressive_close_touch_gate": self.research_aggressive_close_touch_gate,
            "aggressive_close_fee_buffer_bps": self.research_aggressive_close_fee_buffer_bps,
            "aggressive_close_min_net_bps": self.research_aggressive_close_min_net_bps,
            "candidate_backfill": self.research_candidate_backfill,
            "candidate_attempt_cap": self.research_candidate_attempt_cap,
            "toxic_pnl_min_samples": self.research_toxic_pnl_min_samples,
            "toxic_pnl_hard_floor": self.research_toxic_pnl_hard_floor,
            "yellow_sparse_active": self.research_yellow_sparse_active,
            "dust_safe_close": self.research_dust_safe_close,
            "rotate_jsonl": self.research_rotate_jsonl,
            "run_id": self.research_run_id,
            "output_file": self._research_output_file,
        })


    # ------------------------------------------------------------------
    # Research policy fixes
    # ------------------------------------------------------------------

    @staticmethod
    def _percentile(values: list[float], q: float) -> float | None:
        """Small-N linear percentile with no NumPy dependency."""
        if not values:
            return None
        xs = sorted(float(v) for v in values)
        if len(xs) == 1:
            return xs[0]
        pos = (len(xs) - 1) * min(1.0, max(0.0, q))
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac

    @staticmethod
    def _profile_float(profile: Any, name: str) -> float | None:
        try:
            value = getattr(profile, name, None)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _update_spread_thresholds(self, profiles: list[BookProfile]) -> None:
        spreads = [
            value
            for profile in profiles
            if (value := self._profile_float(profile, "spread_bps")) is not None
            and value >= 0.0
        ]

        if (
            self.research_adaptive_spread_thresholds
            and len(spreads) >= self.research_min_profiles_for_adaptive
        ):
            p_stress = self._percentile(spreads, self.research_stress_percentile)
            p_toxic = self._percentile(spreads, self.research_toxic_percentile)
            stress = max(
                self.research_stress_floor_bps,
                p_stress if p_stress is not None else self.research_stress_fallback_bps,
            )
            toxic = max(
                self.research_toxic_floor_bps,
                p_toxic if p_toxic is not None else self.research_toxic_fallback_bps,
                stress + self.research_toxic_gap_bps,
            )
        else:
            stress = self.research_stress_fallback_bps
            toxic = max(
                self.research_toxic_fallback_bps,
                stress + self.research_toxic_gap_bps,
            )

        self._research_stress_spread_bps = float(stress)
        self._research_toxic_spread_bps = float(toxic)

    def _sync_exchange_constraints(
        self,
        state: MarketSimulationStateUpdate,
    ) -> None:
        cfg = getattr(state, "config", None)

        # Quantity precision is needed even if min-size synchronization is
        # disabled, because exact inventory/dust classification must follow the
        # simulator's executable quantity grid.
        try:
            self._research_volume_decimals = max(
                0, int(getattr(cfg, "volumeDecimals", self._research_volume_decimals))
            )
        except (TypeError, ValueError):
            pass

        if not self.research_sync_min_order:
            return

        try:
            state_min = float(getattr(cfg, "min_order_size", 0.0) or 0.0)
        except (TypeError, ValueError):
            state_min = 0.0

        if state_min > 0.0:
            self._research_exchange_min_order_size = state_min
            # Keep inherited helpers aligned with the actual simulator contract.
            self.min_order_size = state_min
        else:
            self._research_exchange_min_order_size = max(
                0.0, float(getattr(self, "min_order_size", 0.0) or 0.0)
            )

    def _execution_flat_epsilon(self) -> float:
        """Half one quantity tick; only sub-half-tick residuals are execution-flat."""
        return max(
            0.5 * (10.0 ** (-int(self._research_volume_decimals))),
            1e-12,
        )

    def classify_market_regime_from_profiles(
        self,
        profiles,
        predictions,
        selection,
    ):
        # Update local per-book risk cutoffs once per request. The global regime
        # classifier itself remains inherited so we can measure it honestly.
        profile_list = list(profiles)
        self._update_spread_thresholds(profile_list)
        regime = super().classify_market_regime_from_profiles(
            profile_list, predictions, selection
        )

        spreads = [
            value
            for profile in profile_list
            if (value := self._profile_float(profile, "spread_bps")) is not None
        ]
        vols = [
            value
            for profile in profile_list
            if (value := self._profile_float(profile, "volatility")) is not None
        ]
        rates = [
            value
            for profile in profile_list
            if (value := self._profile_float(profile, "trade_rate")) is not None
        ]

        inactive = sum(
            1 for profile in profile_list
            if str(getattr(profile, "tier", "")).upper() == "INACTIVE"
        )
        active = max(0, len(profile_list) - inactive)
        stressed_count = sum(
            1 for value in spreads
            if value >= self._research_stress_spread_bps
        )
        liquid_count = sum(
            1
            for profile in profile_list
            if (
                (self._profile_float(profile, "spread_bps") or 0.0)
                < self._research_stress_spread_bps
                and (self._profile_float(profile, "trade_rate") or 0.0)
                >= float(getattr(self, "archetype_dead_trade_rate", 0.0))
            )
        )
        low_trade_count = sum(
            1
            for profile in profile_list
            if (self._profile_float(profile, "trade_rate") or 0.0)
            < float(getattr(self, "archetype_dead_trade_rate", 0.0))
        )

        pred_values = list(predictions.values()) if isinstance(predictions, dict) else []
        up = sum(1 for p in pred_values if str(getattr(p, "direction", "")).upper() == "UP")
        down = sum(1 for p in pred_values if str(getattr(p, "direction", "")).upper() == "DOWN")
        pred_n = max(len(pred_values), 1)
        n = max(len(profile_list), 1)

        trigger = self._pick(regime, "trigger", "reason", "cause")
        threshold = self._pick(regime, "threshold", "trigger_threshold")

        self._emit(
            "REGIME",
            tick=self._tick,
            mode=getattr(regime, "mode", None),
            overlay=getattr(regime, "scoring_overlay", None),
            book_count=len(profile_list),
            active=active,
            inactive=inactive,
            spread_med=self._percentile(spreads, 0.50),
            spread_p90=self._percentile(spreads, 0.90),
            spread_max=max(spreads) if spreads else None,
            stress_spread_bps=self._research_stress_spread_bps,
            toxic_spread_bps=self._research_toxic_spread_bps,
            vol_med=self._percentile(vols, 0.50),
            vol_p90=self._percentile(vols, 0.90),
            trade_rate_med=self._percentile(rates, 0.50),
            liquid_ratio=liquid_count / n,
            low_trade_ratio=low_trade_count / n,
            stressed_ratio=stressed_count / n,
            trend_up_ratio=up / pred_n,
            trend_down_ratio=down / pred_n,
            trigger=trigger if trigger is not None else "UNEXPOSED_BY_PARENT",
            threshold=threshold if threshold is not None else "UNEXPOSED_BY_PARENT",
            adaptive=self.research_adaptive_spread_thresholds,
            min_order_size=self._research_exchange_min_order_size,
        )
        return regime

    def get_regime_params(self, regime: MarketRegime) -> RegimeParamSet:
        params = super().get_regime_params(regime)
        mode = str(getattr(regime, "mode", "")).upper()
        overlay = str(getattr(regime, "scoring_overlay", "")).upper()

        # The parent already turns quoting back on for SCORING_PRESSURE.
        # Outside that overlay, research mode treats global STRESSED as a
        # conservative risk state rather than a universal no-trade switch.
        if (
            self.research_trade_global_stress
            and mode == "STRESSED"
            and overlay != "SCORING_PRESSURE"
        ):
            return RegimeParamSet(
                quote_enabled=True,
                alpha_enabled=False,
                spread_offset=max(float(params.spread_offset), 0.45),
                skew_strength=min(float(params.skew_strength), 0.05),
                size_mult=min(
                    float(params.size_mult),
                    self.research_global_stress_size_mult,
                ),
                profit_target_bps=params.profit_target_bps,
                stop_loss_bps=params.stop_loss_bps,
                min_fill_prob=params.min_fill_prob,
                buy_bias=params.buy_bias,
                sell_bias=params.sell_bias,
            )
        return params

    def classify_book_archetype(
        self,
        profile: BookProfile,
        regime: MarketRegime,
    ) -> BookArchetype:
        spread_bps = float(getattr(profile, "spread_bps", 0.0) or 0.0)
        trade_rate = float(getattr(profile, "trade_rate", 0.0) or 0.0)
        volatility = float(getattr(profile, "volatility", 0.0) or 0.0)
        imbalance = float(getattr(profile, "imbalance", 0.0) or 0.0)
        predict_score = float(getattr(profile, "predict_score", 0.0) or 0.0)
        tier = str(getattr(profile, "tier", "")).upper()
        overlay = str(getattr(regime, "scoring_overlay", "")).upper()

        stress_cutoff = (
            self._research_stress_spread_bps
            if self.research_adaptive_spread_thresholds
            else float(self.archetype_stressed_spread_bps)
        )
        bootstrap_inactive = (
            self.research_inactive_bootstrap
            and self.research_bootstrap_dead_as_mm
            and overlay == "SCORING_PRESSURE"
            and tier == "INACTIVE"
        )

        # Local risk always wins. Global STRESSED is deliberately not a local
        # archetype condition; otherwise one regime bit poisons all books.
        if spread_bps >= stress_cutoff:
            archetype: BookArchetype = "STRESSED"
            source = "LOCAL_SPREAD"
        elif bootstrap_inactive:
            # Cold books often have trade_rate~=0 precisely because they have no
            # realized history yet. Calling all of them DEAD creates a second
            # bootstrap deadlock. Preserve genuine local risk, but let ordinary
            # cold books enter conservative MM long enough to acquire samples.
            extreme_vol = (
                self.research_bootstrap_extreme_vol_mult
                * max(float(self.archetype_vol_threshold), 1e-12)
            )
            if volatility >= extreme_vol:
                archetype = "TOXIC_BOOK"
                source = "BOOTSTRAP_EXTREME_VOL"
            elif abs(imbalance) >= self.archetype_wall_imbalance:
                archetype = "WALL_BOOK"
                source = "BOOTSTRAP_WALL"
            elif (
                volatility >= self.archetype_vol_threshold
                and abs(predict_score) >= self.direction_threshold
            ):
                archetype = "TREND_BOOK"
                source = "BOOTSTRAP_VOL_DIRECTION"
            elif abs(predict_score) >= self.direction_threshold:
                archetype = "TREND_BOOK"
                source = "BOOTSTRAP_DIRECTION"
            else:
                archetype = "MM_BOOK"
                source = "INACTIVE_BOOTSTRAP"
        elif (
            self.research_yellow_sparse_active
            and str(getattr(profile, "tier", "")).upper() == "YELLOW"
            and trade_rate < self.archetype_dead_trade_rate
        ):
            # Strict V2 bridge: a book that already earned YELLOW history but has
            # sparse tape is not identical to an unproven DEAD book. Preserve
            # genuine local risk first, then directional/wall information.
            extreme_vol = (
                self.research_bootstrap_extreme_vol_mult
                * max(float(self.archetype_vol_threshold), 1e-12)
            )
            if volatility >= extreme_vol:
                archetype = "TOXIC_BOOK"
                source = "ACTIVE_SPARSE_EXTREME_VOL"
            elif abs(imbalance) >= self.archetype_wall_imbalance:
                archetype = "WALL_BOOK"
                source = "ACTIVE_SPARSE_WALL"
            elif abs(predict_score) >= self.direction_threshold:
                archetype = "TREND_BOOK"
                source = "ACTIVE_SPARSE_TREND"
            else:
                archetype = "MM_BOOK"
                source = "ACTIVE_SPARSE_MM"
        elif trade_rate < self.archetype_dead_trade_rate:
            archetype = "DEAD_BOOK"
            source = "DEAD_TRADE_RATE"
        elif spread_bps < self.archetype_mm_spread_bps:
            archetype = "MM_BOOK"
            source = "NARROW_MM"
        elif abs(imbalance) >= self.archetype_wall_imbalance:
            archetype = "WALL_BOOK"
            source = "WALL_IMBALANCE"
        elif (
            volatility >= self.archetype_vol_threshold
            and abs(predict_score) >= self.direction_threshold
        ):
            archetype = "TREND_BOOK"
            source = "VOL_AND_DIRECTION"
        elif volatility >= self.archetype_vol_threshold:
            archetype = "TOXIC_BOOK"
            source = "HIGH_VOL"
        elif abs(predict_score) >= self.direction_threshold:
            archetype = "TREND_BOOK"
            source = "DIRECTION"
        else:
            archetype = "MM_BOOK" if self.research_neutral_fallback else "TOXIC_BOOK"
            source = (
                "NEUTRAL_FALLBACK"
                if self.research_neutral_fallback
                else "LEGACY_TOXIC_FALLBACK"
            )

        if self.debug_enabled:
            record = self._book_record(profile.book_id)
            record["archetype"] = archetype
            record["archetype_source"] = source
            record["profile_spread_bps"] = spread_bps
            record["volatility"] = volatility
            record["trade_rate"] = trade_rate
            record["imbalance"] = imbalance
            record["stress_spread_bps"] = stress_cutoff
            record["toxic_spread_bps"] = self._research_toxic_spread_bps
            record["stressed_by_spread"] = spread_bps >= stress_cutoff
            record["stressed_by_regime"] = False
            record["legacy_stressed_by_regime"] = (
                str(getattr(regime, "mode", "")).upper() == "STRESSED"
            )
            record["bootstrap_inactive"] = bootstrap_inactive
            record["dead_trade_rate_hit"] = trade_rate < self.archetype_dead_trade_rate
            record["active_sparse"] = (
                self.research_yellow_sparse_active
                and str(getattr(profile, "tier", "")).upper() == "YELLOW"
                and trade_rate < self.archetype_dead_trade_rate
            )
        return archetype

    def is_toxic_book(
        self,
        book_id: int,
        profile: BookProfile,
        archetype: BookArchetype,
    ) -> bool:
        mem = self._mem(book_id)
        spread_bps = self._profile_float(profile, "spread_bps")
        toxic_cutoff = (
            self._research_toxic_spread_bps
            if self.research_adaptive_spread_thresholds
            else float(self.toxic_spread_bps)
        )

        toxic_loss = mem.loss_streak >= self.toxic_loss_streak
        pnl_samples = int(self._research_round_trip_samples_by_book.get(book_id, 0))
        toxic_pnl_raw = mem.recent_pnl < self.toxic_recent_pnl
        toxic_pnl = toxic_pnl_raw and (
            pnl_samples >= self.research_toxic_pnl_min_samples
            or mem.recent_pnl <= self.research_toxic_pnl_hard_floor
        )
        toxic_spread = (
            spread_bps is not None and spread_bps > toxic_cutoff
        )
        toxic_archetype = archetype in ("STRESSED", "TOXIC_BOOK")
        toxic_red_tier = str(getattr(profile, "tier", "")).upper() == "RED"

        toxic = any((
            toxic_loss,
            toxic_pnl,
            toxic_spread,
            toxic_archetype,
            toxic_red_tier,
        ))

        if self.debug_enabled:
            record = self._book_record(book_id)
            record["toxic"] = toxic
            record["loss_streak"] = mem.loss_streak
            record["recent_pnl"] = mem.recent_pnl
            record["toxic_loss"] = toxic_loss
            record["toxic_pnl"] = toxic_pnl
            record["toxic_pnl_raw"] = toxic_pnl_raw
            record["toxic_pnl_samples"] = pnl_samples
            record["toxic_pnl_min_samples"] = self.research_toxic_pnl_min_samples
            record["toxic_pnl_hard_floor"] = self.research_toxic_pnl_hard_floor
            record["toxic_spread"] = toxic_spread
            record["toxic_archetype"] = toxic_archetype
            record["toxic_red_tier"] = toxic_red_tier
            record["toxic_spread_bps"] = toxic_cutoff
        return toxic

    def _net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        """V2 inventory snapshot in one unit system: signed base utilization."""
        if not self.research_fix_inventory_util:
            return super()._net_inventory(book_id, mid)
        if mid <= 0:
            return InventorySnapshot(0.0, 0.0, "FLAT", None, None, 0)

        tracker = self._position_tracker_snapshot(book_id)
        net_base = float(tracker.net_qty)
        max_base = max(float(self.max_inventory_base), 1e-9)
        signed_util = net_base / max_base
        flat_eps = self._execution_flat_epsilon()

        if abs(net_base) < flat_eps:
            band = "FLAT"
            self._position_ticks.pop(book_id, None)
            self._research_position_tick_seen.pop(book_id, None)
            self._inventory_reason.pop(book_id, None)
        else:
            band = (
                "MAX_LONG" if net_base >= max_base
                else "MAX_SHORT" if net_base <= -max_base
                else "LONG" if net_base > 0.0
                else "SHORT"
            )
            # Strategy1_Debug can query inventory more than once per request.
            # Age must advance once per research tick, not once per diagnostic call.
            current_tick = int(getattr(self, "_tick", 0) or 0)
            if self._research_position_tick_seen.get(book_id) != current_tick:
                self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
                self._research_position_tick_seen[book_id] = current_tick

        position_ticks = self._position_ticks.get(book_id, 0)
        vwap = tracker.vwap_entry
        unrealized_bps: float | None = None
        if vwap and vwap > 0:
            if net_base > 0:
                unrealized_bps = ((mid - vwap) / vwap) * 10_000.0
            elif net_base < 0:
                unrealized_bps = ((vwap - mid) / vwap) * 10_000.0

        inventory = InventorySnapshot(
            net_base=net_base,
            inventory_ratio=signed_util,
            band=band,
            vwap_entry=vwap,
            unrealized_bps=unrealized_bps,
            position_ticks=position_ticks,
            opened_at_ns=tracker.opened_at_ns,
            reason=self._inventory_reason.get(book_id, "UNKNOWN"),
        )
        if self.debug_enabled:
            self._book_record(book_id)["inventory"] = {
                "net_base": inventory.net_base,
                "ratio": inventory.inventory_ratio,
                "signed_util": signed_util,
                "band": inventory.band,
                "vwap_entry": inventory.vwap_entry,
                "unrealized_bps": inventory.unrealized_bps,
                "position_ticks": inventory.position_ticks,
                "reason": inventory.reason,
            }
        return inventory

    def _inventory_util(self, inventory: InventorySnapshot) -> float:
        if not self.research_fix_inventory_util:
            return super()._inventory_util(inventory)
        return abs(float(inventory.net_base)) / max(float(self.max_inventory_base), 1e-9)

    def _inventory_needs_management(self, inventory: InventorySnapshot) -> bool:
        if inventory.band in ("MAX_LONG", "MAX_SHORT"):
            return True

        abs_base = abs(float(inventory.net_base))
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        eps = self._execution_flat_epsilon()

        # Strict V2: quarantine exact non-flat residuals before normal candidate
        # selection. The management path will intentionally emit no over-close.
        if (
            self.research_dust_safe_close
            and min_size > 0.0
            and abs_base >= eps
            and abs_base + 1e-12 < min_size
        ):
            return True

        if (
            self._research_bootstrap_active
            and self.research_bootstrap_manage_min_clip
            and abs_base >= eps
            and (min_size <= 0.0 or abs_base + 1e-12 >= min_size)
        ):
            return True
        return self._inventory_util(inventory) >= float(self.inventory_close_threshold)

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
        if not self.research_fix_quote_reservation:
            return super().skewed_quote_prices(
                bid, ask, signal, inventory_ratio, regime_params, price_dec, edge_bias
            )
        spread = ask - bid
        if spread <= 0.0:
            return None
        mid = 0.5 * (bid + ask)
        directional = max(-1.0, min(1.0, float(signal) + float(edge_bias)))
        directional_bias = (
            float(regime_params.buy_bias) if directional >= 0.0
            else float(regime_params.sell_bias)
        )
        alpha_shift = (
            spread * float(regime_params.skew_strength) * directional * directional_bias
        )
        # Positive inventory (long) shifts reservation DOWN; negative inventory
        # shifts it UP, encouraging the side that reduces risk.
        inventory_shift = (
            spread * float(self.inventory_skew_strength) * float(inventory_ratio)
        )
        reservation = mid + alpha_shift - inventory_shift
        half_spread = spread * max(0.05, float(regime_params.spread_offset))
        tick_size = 10.0 ** (-int(price_dec))
        bid_px = min(reservation - half_spread, ask - tick_size)
        ask_px = max(reservation + half_spread, bid + tick_size)
        bid_px = round(bid_px, price_dec)
        ask_px = round(ask_px, price_dec)
        if bid_px <= 0.0 or bid_px >= ask_px:
            return None
        return bid_px, ask_px

    def _compute_close_score(
        self,
        inventory: InventorySnapshot,
        regime_params: RegimeParamSet,
        regime: MarketRegime,
        archetype: BookArchetype,
    ) -> float:
        if not self.research_fix_inventory_util:
            return super()._compute_close_score(
                inventory, regime_params, regime, archetype
            )
        unreal = inventory.unrealized_bps
        target = max(float(regime_params.profit_target_bps), 1e-9)
        stop = max(float(regime_params.stop_loss_bps), 1e-9)
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
        if str(getattr(regime, "mode", "")).upper() == "STRESSED":
            regime_risk = 1.0
        elif archetype in ("TOXIC_BOOK", "WALL_BOOK"):
            regime_risk = 0.6
        elif archetype == "DEAD_BOOK":
            regime_risk = 0.4
        time_risk = min(
            1.0,
            float(inventory.position_ticks) / max(float(self.position_max_ticks), 1.0),
        )
        return (
            0.5 * pnl_component
            + 0.3 * inventory_risk
            + 0.2 * max(regime_risk, time_risk)
        )

    def _allows_aggressive_close(
        self,
        book_id: int,
        inventory: InventorySnapshot,
        close_score: float,
        time_stop: bool,
        stop_loss_hit: bool,
    ) -> bool:
        # Safety controls remain immediate. These are the only bootstrap paths
        # allowed to ignore the touch-economics gate.
        if stop_loss_hit or inventory.band in ("MAX_LONG", "MAX_SHORT"):
            return True

        if (
            self._research_bootstrap_active
            and self.research_bootstrap_allow_aggressive_close
        ):
            ticks = int(inventory.position_ticks)
            if not self.research_aggressive_close_touch_gate:
                # Explicit A/B fallback to the previous V2 behavior.
                if ticks >= self.research_bootstrap_hard_close_ticks:
                    return True
                if ticks >= self.research_bootstrap_force_close_ticks:
                    unreal = inventory.unrealized_bps
                    return (
                        unreal is not None
                        and unreal >= self.research_bootstrap_force_close_min_bps
                    )
                return False

            if ticks >= self.research_bootstrap_force_close_ticks:
                ctx = self._research_aggressive_context.get(book_id) or {}
                net_touch_bps = ctx.get("net_touch_bps")
                if net_touch_bps is not None:
                    # Age permits consideration; executable economics decide.
                    return (
                        float(net_touch_bps)
                        >= self.research_aggressive_close_min_net_bps
                    )

            # Strict V2 deliberately removes age-only / close-score-only market
            # exits while the scoring-pressure bootstrap is active. The old
            # age-60 cohort was overwhelmingly loss-making, and age>=180 alone
            # is not evidence that crossing the spread improves the outcome.
            return False

        return super()._allows_aggressive_close(
            book_id, inventory, close_score, time_stop, stop_loss_hit
        )

    def _execute_aggressive_close(
        self,
        response: FinanceAgentResponse,
        book_id: int,
        book: Book,
        qty: float,
        long_pos: bool,
    ) -> bool:
        """Submit close without clearing position state before confirmed fill."""
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        if not self._passes_fee_gate(book_id, aggressive=True):
            return False
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return False
        account = self.accounts[book_id]
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.market_order(
                book_id=book_id, direction=close_dir, quantity=qty,
                stp=STP.CANCEL_OLDEST, delay=0,
            )
            return True
        if close_dir == OrderDirection.BUY:
            px = book.asks[0].price
            if account.quote_balance.free >= qty * px:
                response.market_order(
                    book_id=book_id, direction=close_dir, quantity=qty,
                    stp=STP.CANCEL_OLDEST, delay=0,
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
        # Estimate executable close economics at the opposing touch rather than
        # using mark-to-mid. The configurable buffer covers taker fee/slippage.
        touch_gross_bps = None
        net_touch_bps = None
        entry = inventory.vwap_entry
        if (
            entry is not None
            and float(entry) > 0.0
            and getattr(book, "bids", None)
            and getattr(book, "asks", None)
        ):
            entry_f = float(entry)
            if inventory.net_base > 0.0:
                touch_px = float(book.bids[0].price)
                touch_gross_bps = (touch_px - entry_f) / entry_f * 10_000.0
            elif inventory.net_base < 0.0:
                touch_px = float(book.asks[0].price)
                touch_gross_bps = (entry_f - touch_px) / entry_f * 10_000.0
            if touch_gross_bps is not None:
                net_touch_bps = (
                    float(touch_gross_bps)
                    - self.research_aggressive_close_fee_buffer_bps
                )

        self._research_aggressive_context[book_id] = {
            "touch_gross_bps": touch_gross_bps,
            "net_touch_bps": net_touch_bps,
            "age_ticks": int(inventory.position_ticks),
        }
        if self.debug_enabled:
            record = self._book_record(book_id)
            record["aggressive_touch_gross_bps"] = touch_gross_bps
            record["aggressive_touch_net_bps"] = net_touch_bps
            record["aggressive_close_fee_buffer_bps"] = (
                self.research_aggressive_close_fee_buffer_bps
            )
            record["aggressive_close_min_net_bps"] = (
                self.research_aggressive_close_min_net_bps
            )

        min_size = max(0.0, float(self._research_exchange_min_order_size))
        abs_base = abs(float(inventory.net_base))
        if (
            self.research_dust_safe_close
            and inventory.band != "FLAT"
            and min_size > 0.0
            and abs_base >= self._execution_flat_epsilon()
            and abs_base + 1e-12 < min_size
        ):
            self._research_dust_blocks += 1
            if self.debug_enabled:
                record = self._book_record(book_id)
                record["dust_position"] = True
                record["dust_qty"] = abs_base
                record["min_order_size"] = min_size
            self._emit(
                "POSITION_GUARD",
                tick=getattr(self, "_tick", None),
                book_id=book_id,
                reason="DUST_POSITION",
                net_base=inventory.net_base,
                min_order_size=min_size,
            )
            return 0
        return super()._manage_inventory(
            response, state, book_id, book, inventory, regime_params, regime, archetype
        )

    def onTrade(self, event, validator: str | None = None) -> None:
        book_id = getattr(event, "bookId", None)
        own = (
            getattr(event, "takerAgentId", None) == getattr(self, "uid", None)
            or getattr(event, "makerAgentId", None) == getattr(self, "uid", None)
        )
        before = 0.0
        pnl_before = 0.0
        if book_id is not None:
            before = float(self._position_tracker_snapshot(book_id).net_qty)
            pnl_before = float(self._pnl_tick_buffer.get(book_id, 0.0))
        super().onTrade(event, validator)
        if book_id is None or not own:
            return
        after = float(self._position_tracker_snapshot(book_id).net_qty)
        pnl_after = float(self._pnl_tick_buffer.get(book_id, 0.0))
        eps = self._execution_flat_epsilon()
        if abs(before) < eps and abs(after) >= eps:
            transition = "OPEN"
            self._research_position_opens += 1
        elif abs(before) >= eps and abs(after) < eps:
            transition = "FLAT"
            self._research_round_trip_closes += 1
            self._research_position_reductions += 1
            self._research_round_trip_samples_by_book[book_id] = (
                self._research_round_trip_samples_by_book.get(book_id, 0) + 1
            )
        elif abs(after) < abs(before) - eps:
            transition = "REDUCE"
            self._research_position_reductions += 1
        elif abs(after) > abs(before) + eps:
            transition = "INCREASE"
        elif before * after < 0.0:
            transition = "CROSS"
        else:
            transition = "UNCHANGED"
        self._emit(
            "POSITION",
            tick=getattr(self, "_tick", None),
            timestamp=getattr(event, "timestamp", None),
            book_id=book_id,
            transition=transition,
            net_before=before,
            net_after=after,
            realized_pnl_delta=pnl_after - pnl_before,
            round_trip=(transition == "FLAT"),
            round_trip_total=self._research_round_trip_closes,
            round_trip_book_samples=self._research_round_trip_samples_by_book.get(book_id, 0),
            execution_flat_epsilon=eps,
            reason=self._inventory_reason.get(book_id, "FLAT" if transition == "FLAT" else "UNKNOWN"),
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
        # Reproduce Strategy1 sizing but use dimensionally-correct base
        # utilization rather than dividing a capital ratio by a clip count.
        predict_score = float(getattr(profile, "predict_score", 0.0) or 0.0)
        volatility = float(getattr(profile, "volatility", 0.0) or 0.0)
        confidence = max(0.5, min(2.0, 1.0 + abs(predict_score)))
        vol_scale = 1.0
        if volatility > 0.0:
            vol_scale = max(
                0.5, min(2.0, float(self.profile_vol_scale) / volatility)
            )
        spread_factor = 1.0
        if profile.spread is not None and mid is not None and mid > 0.0:
            spread_bps = (float(profile.spread) / mid) * 10_000.0
            spread_factor = max(0.5, min(1.5, 1.0 - spread_bps / 20.0))
        kappa_scale = 1.0
        if profile.raw_kappa is not None:
            kappa_scale = max(0.5, min(1.5, 1.0 + float(profile.raw_kappa) * 0.2))
        inventory_factor = max(0.3, 1.0 - min(1.0, self._inventory_util(inventory)))
        raw_model_size = (
            float(base_size)
            * confidence
            * float(regime_params.size_mult)
            * vol_scale
            * spread_factor
            * kappa_scale
            * inventory_factor
        )
        size = self._round_order_size(raw_model_size, vol_dec)
        min_size = max(0.0, self._research_exchange_min_order_size)
        promoted = False
        rounded_before_promotion = size
        if (
            self.research_promote_min_order
            and size > 0.0
            and min_size > 0.0
            and size + 1e-12 < min_size
        ):
            remaining = max(
                0.0,
                float(self.max_inventory_base) - abs(float(inventory.net_base)),
            )
            if min_size <= remaining + 1e-12:
                size = round(min_size, vol_dec)
                promoted = True
            else:
                size = 0.0
        if self.debug_enabled and hasattr(profile, "book_id"):
            record = self._book_record(profile.book_id)
            record["dynamic_size_model_raw"] = raw_model_size
            record["dynamic_size_raw"] = rounded_before_promotion
            record["dynamic_size_final"] = size
            record["inventory_util"] = self._inventory_util(inventory)
            record["min_order_size"] = min_size
            record["size_promoted_to_min"] = promoted
        return size

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
        # Parent Strategy1 slices candidates before quote-level gates. Strict V2
        # scans a bounded larger prefix but preserves the original cap as the
        # number of books that may actually emit quotes.
        if self._research_backfill_active:
            if self._research_quote_successes >= self._research_quote_success_cap:
                if self.debug_enabled:
                    record = self._book_record(book_id)
                    record["action"] = "SKIP"
                    record["reason"] = "MM_SUCCESS_CAP"
                return 0
            if self._research_quote_attempts >= self.research_candidate_attempt_cap:
                return 0
            self._research_quote_attempts += 1

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
        if self._research_backfill_active and placed:
            self._research_quote_successes += 1
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
        self._sync_exchange_constraints(state)

        overlay = str(getattr(regime, "scoring_overlay", "")).upper()
        bootstrap = (
            self.research_inactive_bootstrap
            and overlay == "SCORING_PRESSURE"
        )
        self._research_bootstrap_active = bootstrap

        old_skip_inactive = self.mm_skip_inactive_tier
        old_maintenance_mult = self.maintenance_size_mult
        old_max_mm_books = self.max_mm_books_per_tick

        self._research_backfill_active = bool(self.research_candidate_backfill)
        self._research_quote_success_cap = int(old_max_mm_books)
        self._research_quote_successes = 0
        self._research_quote_attempts = 0
        if self._research_backfill_active:
            self.max_mm_books_per_tick = max(
                old_max_mm_books, self.research_candidate_attempt_cap
            )

        try:
            # FIX 3: permit cold books to acquire their first realized samples
            # while the parent explicitly signals scoring pressure.
            if bootstrap:
                self.mm_skip_inactive_tier = False

            # FIX 4: coverage orders must be executable. Promote only the
            # maintenance clip, and only when a single minimum order remains
            # inside the configured absolute base inventory cap.
            if (
                bootstrap
                and self.research_bootstrap_maintenance_min_order
                and self._research_exchange_min_order_size > 0.0
            ):
                maintenance_base = float(
                    getattr(self, "maintenance_order_size", 0.0) or 0.0
                )
                min_size = self._research_exchange_min_order_size
                if (
                    maintenance_base > 0.0
                    and min_size <= float(self.max_inventory_base) + 1e-12
                ):
                    required_mult = min_size / maintenance_base
                    if required_mult > self.maintenance_size_mult:
                        self.maintenance_size_mult = required_mult

            stats = super().build_mm_strategy_instructions(
                response,
                state,
                selection,
                predictions,
                regime,
                collect_archetypes=collect_archetypes,
            )
            if isinstance(stats, dict):
                stats["research_bootstrap_active"] = bootstrap
                stats["research_stress_spread_bps"] = self._research_stress_spread_bps
                stats["research_toxic_spread_bps"] = self._research_toxic_spread_bps
                stats["research_min_order_size"] = self._research_exchange_min_order_size
                stats["research_round_trip_closes"] = self._research_round_trip_closes
                stats["research_position_opens"] = self._research_position_opens
                stats["research_dust_blocks"] = self._research_dust_blocks
                stats["research_quote_attempts"] = self._research_quote_attempts
                stats["research_quote_successes"] = self._research_quote_successes
                stats["research_quote_success_cap"] = self._research_quote_success_cap
                stats["research_flat_epsilon"] = self._execution_flat_epsilon()
            return stats
        finally:
            self.mm_skip_inactive_tier = old_skip_inactive
            self.maintenance_size_mult = old_maintenance_mult
            self.max_mm_books_per_tick = old_max_mm_books
            self._research_bootstrap_active = False
            self._research_backfill_active = False

    # Strategy1_Debug emits an explicit DECISION payload rather than forwarding
    # the full internal record. Override it so the new research diagnostics are
    # actually persisted to JSONL and visible in [S1R_SKIP]/[S1R_QUOTE].
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
        touch_spread_bps = None
        if mid and bid is not None and ask is not None:
            touch_spread_bps = ((ask - bid) / mid) * 10_000.0

        profile_spread_bps = (
            self._profile_float(profile, "spread_bps")
            if profile is not None else None
        )
        mem = self._mem(book_id) if profile is not None else None

        reason = str(record.get("reason", DebugReason.NO_ACTION))
        inactive_gate_bypassed = (
            self.research_inactive_bootstrap
            and str(getattr(regime, "scoring_overlay", "")).upper() == "SCORING_PRESSURE"
        )
        # If a future Debug implementation diagnoses after policy restoration,
        # never present INACTIVE_TIER as a proven live gate when V2 bypasses it.
        if reason == "INACTIVE_TIER" and inactive_gate_bypassed and not self.mm_skip_inactive_tier:
            reason = "INACTIVE_DIAGNOSTIC_ONLY"
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
            archetype_source=record.get("archetype_source"),
            tier=getattr(profile, "tier", None) if profile is not None else None,
            mid=mid,
            spread_bps=profile_spread_bps if profile_spread_bps is not None else touch_spread_bps,
            touch_spread_bps=touch_spread_bps,
            volatility=getattr(profile, "volatility", None) if profile is not None else None,
            trade_rate=getattr(profile, "trade_rate", None) if profile is not None else None,
            imbalance=getattr(profile, "imbalance", None) if profile is not None else None,
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
            loss_streak=record.get(
                "loss_streak",
                getattr(mem, "loss_streak", None) if mem is not None else None,
            ),
            recent_pnl=record.get(
                "recent_pnl",
                getattr(mem, "recent_pnl", None) if mem is not None else None,
            ),
            toxic_loss=record.get("toxic_loss"),
            toxic_pnl=record.get("toxic_pnl"),
            toxic_spread=record.get("toxic_spread"),
            toxic_archetype=record.get("toxic_archetype"),
            toxic_red_tier=record.get("toxic_red_tier"),
            stressed_by_spread=record.get("stressed_by_spread"),
            stressed_by_regime=record.get("stressed_by_regime"),
            legacy_stressed_by_regime=record.get("legacy_stressed_by_regime"),
            stress_spread_bps=record.get(
                "stress_spread_bps", self._research_stress_spread_bps
            ),
            toxic_spread_bps=record.get(
                "toxic_spread_bps", self._research_toxic_spread_bps
            ),
            min_order_size=record.get(
                "min_order_size", self._research_exchange_min_order_size
            ),
            dynamic_size_raw=record.get("dynamic_size_raw"),
            dynamic_size_final=record.get("dynamic_size_final"),
            size_promoted_to_min=record.get("size_promoted_to_min"),
            inactive_bootstrap=inactive_gate_bypassed,
            inactive_gate_bypassed=inactive_gate_bypassed and not self.mm_skip_inactive_tier,
            dead_trade_rate_hit=record.get("dead_trade_rate_hit"),
            active_sparse=record.get("active_sparse"),
            toxic_pnl_raw=record.get("toxic_pnl_raw"),
            toxic_pnl_samples=record.get("toxic_pnl_samples"),
            aggressive_touch_gross_bps=record.get("aggressive_touch_gross_bps"),
            aggressive_touch_net_bps=record.get("aggressive_touch_net_bps"),
            bootstrap_inactive=record.get("bootstrap_inactive"),
            inventory_util=record.get("inventory_util"),
            dust_position=record.get("dust_position"),
        )

    # Intercept every Strategy1_Debug event. No synchronous bt.logging call here.
    def _emit(self, event_type: str, force: bool = False, **payload: Any) -> None:
        if not getattr(self, "debug_enabled", True) and not force:
            return
        if event_type == "RUN_SUMMARY":
            payload.setdefault("research_round_trip_closes", getattr(self, "_research_round_trip_closes", 0))
            payload.setdefault("research_position_opens", getattr(self, "_research_position_opens", 0))
            payload.setdefault("research_position_reductions", getattr(self, "_research_position_reductions", 0))
            payload.setdefault("research_dust_blocks", getattr(self, "_research_dust_blocks", 0))
            try:
                payload.setdefault(
                    "research_open_positions",
                    sum(
                        1 for bid in getattr(self, "_open_positions", {})
                        if abs(float(self._position_tracker_snapshot(bid).net_qty))
                        >= self._execution_flat_epsilon()
                    ),
                )
            except Exception:
                pass
        try:
            safe = self._json_safe(payload)
        except Exception:
            safe = payload
        record = {
            "type": event_type,
            "agent_id": getattr(self, "uid", None),
            "wall_time_ns": time.time_ns(),
            **safe,
        }
        if not getattr(self, "_research_ready", False):
            self._research_early.append(record)
            return
        if getattr(self, "research_enabled", False):
            self._enqueue(record)

    def _enqueue(self, record: dict[str, Any]) -> None:
        if self._rq is None:
            return
        try:
            self._rq.put_nowait(record)
        except queue.Full:
            self._rdropped += 1

    def _writer_loop(self) -> None:
        assert self._rq is not None and self._rstop is not None
        while not self._rstop.is_set() or not self._rq.empty():
            try:
                record = self._rq.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if self._rfile is not None:
                    self._rfile.write(json.dumps(record, separators=(",", ":"), sort_keys=True, default=str) + "\n")
                if self.research_console and self._console_allowed(record):
                    line = self._format_human(record)
                    if line:
                        print(line, flush=True)
            except Exception as exc:
                try:
                    print(f"[S1R_ERROR] stage=telemetry error={self._short(exc)}", flush=True)
                except Exception:
                    pass
            finally:
                self._rq.task_done()

    def _console_allowed(self, r: dict[str, Any]) -> bool:
        typ = str(r.get("type", ""))
        if typ in {"ERROR", "RUN_SUMMARY", "RESEARCH_CONFIG", "DEBUG_CONFIG", "POSITION", "POSITION_GUARD"}:
            return True
        if typ == "ORDER_LIFECYCLE":
            phase = str(r.get("phase", "")).upper()
            # Fills/rejects are always important. SUBMITTED/CANCEL/EXPIRE are
            # sampled with the normal tick cadence to avoid console latency.
            if any(token in phase for token in ("TRADE", "FILL", "REJECT", "FAIL")):
                return True
        tick = self._int(r.get("tick"))
        if tick is not None and tick != 1 and tick % self.research_every_n != 0:
            return False
        book = self._int(r.get("book_id"))
        if self.research_book_id >= 0 and book is not None:
            return book == self.research_book_id
        return True

    def _format_human(self, r: dict[str, Any]) -> str | None:
        typ = str(r.get("type", ""))
        if typ == "RESEARCH_CONFIG":
            return (f"[S1R_CONFIG] enabled={int(bool(r.get('enabled')))} every_n={r.get('every_n')} "
                    f"book={r.get('book_filter')} jsonl={int(bool(r.get('jsonl')))} "
                    f"queue={r.get('queue_size')} policy={self._short(r.get('policy_version'))} "
                    f"fix_global_stress={int(bool(r.get('fix_global_stress')))} "
                    f"neutral_fallback={int(bool(r.get('neutral_fallback')))} "
                    f"adaptive_spread={int(bool(r.get('adaptive_spread_thresholds')))} "
                    f"inactive_bootstrap={int(bool(r.get('inactive_bootstrap')))} "
                    f"bootstrap_dead_as_mm={int(bool(r.get('bootstrap_dead_as_mm')))} "
                    f"fix_inv={int(bool(r.get('fix_inventory_util')))} "
                    f"fix_reservation={int(bool(r.get('fix_quote_reservation')))} "
                    f"manage_min_clip={int(bool(r.get('bootstrap_manage_min_clip')))} "
                    f"close_age_gate={r.get('bootstrap_force_close_ticks')} "
                    f"touch_gate={int(bool(r.get('aggressive_close_touch_gate')))} "
                    f"touch_buffer_bps={self._fmt(r.get('aggressive_close_fee_buffer_bps'))} "
                    f"touch_min_net_bps={self._fmt(r.get('aggressive_close_min_net_bps'))} "
                    f"backfill={int(bool(r.get('candidate_backfill')))} "
                    f"attempt_cap={r.get('candidate_attempt_cap')} "
                    f"toxic_samples={r.get('toxic_pnl_min_samples')} "
                    f"yellow_sparse={int(bool(r.get('yellow_sparse_active')))} "
                    f"dust_safe={int(bool(r.get('dust_safe_close')))} "
                    f"min_order_sync={int(bool(r.get('sync_min_order')))} "
                    f"run_id={self._short(r.get('run_id'))} "
                    f"file={self._short(r.get('output_file'))}")
        if typ == "DEBUG_CONFIG":
            return (f"[S1R_CONFIG] debug_enabled={int(bool(r.get('enabled')))} "
                    f"debug_every_n={r.get('every_n')} debug_book={r.get('book_filter')}")
        if typ == "REGIME":
            return (
                f"[S1R_REGIME] tick={r.get('tick')} mode={self._short(r.get('mode'))} "
                f"overlay={self._short(r.get('overlay'))} book_count={r.get('book_count')} "
                f"active={r.get('active')} inactive={r.get('inactive')} "
                f"spread_med={self._fmt(r.get('spread_med'))} "
                f"spread_p90={self._fmt(r.get('spread_p90'))} "
                f"spread_max={self._fmt(r.get('spread_max'))} "
                f"stress_cut={self._fmt(r.get('stress_spread_bps'))} "
                f"toxic_cut={self._fmt(r.get('toxic_spread_bps'))} "
                f"vol_med={self._fmt(r.get('vol_med'))} vol_p90={self._fmt(r.get('vol_p90'))} "
                f"trade_rate_med={self._fmt(r.get('trade_rate_med'))} "
                f"liquid_ratio={self._fmt(r.get('liquid_ratio'))} "
                f"low_trade_ratio={self._fmt(r.get('low_trade_ratio'))} "
                f"stressed_ratio={self._fmt(r.get('stressed_ratio'))} "
                f"trend_up_ratio={self._fmt(r.get('trend_up_ratio'))} "
                f"trend_down_ratio={self._fmt(r.get('trend_down_ratio'))} "
                f"min_order={self._fmt(r.get('min_order_size'))} "
                f"trigger={self._short(r.get('trigger'))} threshold={self._short(r.get('threshold'))}"
            )
        if typ == "TIMING":
            return (f"[S1R_REQ] tick={r.get('tick')} sim_ts={r.get('timestamp')} "
                    f"instructions={r.get('instructions', 0)} notices={r.get('notices', 0)} "
                    f"update_ms={self._fmt(r.get('update_ms'))} respond_ms={self._fmt(r.get('respond_ms'))} "
                    f"report_ms={self._fmt(r.get('report_ms'))} total_ms={self._fmt(r.get('total_ms'))}")
        if typ == "DECISION":
            raw = str(r.get("reason", "NO_ACTION"))
            reason = self.REASON_ALIAS.get(raw, raw)
            action = str(r.get("action", "SKIP")).upper()
            inv = r.get("inventory") or {}
            common = (
                f"tick={r.get('tick')} book={r.get('book_id')} regime={self._short(r.get('regime'))} "
                f"overlay={self._short(r.get('overlay'))} archetype={self._short(r.get('archetype'))} "
                f"arch_src={self._short(r.get('archetype_source'))} tier={self._short(r.get('tier'))} "
                f"spread_bps={self._fmt(r.get('spread_bps'))} "
                f"stress_cut={self._fmt(r.get('stress_spread_bps'))} "
                f"toxic_cut={self._fmt(r.get('toxic_spread_bps'))} "
                f"volatility={self._fmt(r.get('volatility'))} trade_rate={self._fmt(r.get('trade_rate'))} "
                f"imbalance={self._fmt(r.get('imbalance'))} "
                f"loss_streak={self._fmt(r.get('loss_streak'))} recent_pnl={self._fmt(r.get('recent_pnl'))} "
                f"toxic_loss={self._fmt(r.get('toxic_loss'))} toxic_pnl={self._fmt(r.get('toxic_pnl'))} "
                f"toxic_spread={self._fmt(r.get('toxic_spread'))} "
                f"toxic_archetype={self._fmt(r.get('toxic_archetype'))} "
                f"toxic_red_tier={self._fmt(r.get('toxic_red_tier'))} "
                f"stressed_by_spread={self._fmt(r.get('stressed_by_spread'))} "
                f"stressed_by_regime={self._fmt(r.get('stressed_by_regime'))} "
                f"legacy_global_stress={self._fmt(r.get('legacy_stressed_by_regime'))} "
                f"signal={self._fmt(r.get('signal'))} alpha={self._fmt(r.get('expected_alpha'))} "
                f"min_alpha={self._fmt(r.get('min_expected_alpha'))} "
                f"fill_bid={self._fmt(r.get('fill_buy'))} fill_ask={self._fmt(r.get('fill_sell'))} "
                f"qty={self._fmt(r.get('quantity'))} dyn_raw={self._fmt(r.get('dynamic_size_raw'))} "
                f"dyn_final={self._fmt(r.get('dynamic_size_final'))} "
                f"min_order={self._fmt(r.get('min_order_size'))} "
                f"promoted_min={self._fmt(r.get('size_promoted_to_min'))} "
                f"bootstrap={self._fmt(r.get('inactive_bootstrap'))} "
                f"inactive_bypass={self._fmt(r.get('inactive_gate_bypassed'))} "
                f"dead_rate_hit={self._fmt(r.get('dead_trade_rate_hit'))} "
                f"active_sparse={self._fmt(r.get('active_sparse'))} "
                f"toxic_pnl_samples={self._fmt(r.get('toxic_pnl_samples'))} "
                f"touch_net_bps={self._fmt(r.get('aggressive_touch_net_bps'))} "
                f"inv_util={self._fmt(r.get('inventory_util'))} "
                f"dust={self._fmt(r.get('dust_position'))} "
                f"exp_pnl={self._fmt(r.get('expected_realized_pnl'))} "
                f"inv_base={self._fmt(inv.get('net_base'))} inv_band={self._short(inv.get('band'))} "
                f"instructions={r.get('instructions', 0)}"
            )
            if action == "SKIP":
                return f"[S1R_SKIP] {common} side=BOTH reason={reason} raw_reason={raw}"
            return (f"[S1R_QUOTE] {common} action={action} reason={reason} "
                    f"bid={self._fmt(r.get('bid_px'))} ask={self._fmt(r.get('ask_px'))} "
                    f"decision_ms={self._fmt(r.get('decision_ms'))}")
        if typ == "POSITION":
            return (
                f"[S1R_POSITION] tick={r.get('tick')} book={r.get('book_id')} "
                f"transition={self._short(r.get('transition'))} "
                f"net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))} "
                f"realized_delta={self._fmt(r.get('realized_pnl_delta'))} "
                f"round_trip={self._fmt(r.get('round_trip'))} total_round_trips={r.get('round_trip_total')} "
                f"book_samples={r.get('round_trip_book_samples')} "
                f"flat_eps={self._fmt(r.get('execution_flat_epsilon'))} "
                f"reason={self._short(r.get('reason'))}"
            )
        if typ == "POSITION_GUARD":
            return (
                f"[S1R_DUST] tick={r.get('tick')} book={r.get('book_id')} "
                f"net_base={self._fmt(r.get('net_base'))} min_order={self._fmt(r.get('min_order_size'))} "
                f"reason={self._short(r.get('reason'))}"
            )
        if typ == "ORDER_LIFECYCLE":
            phase = str(r.get("phase", "UNKNOWN")).upper()
            book = r.get("book_id")
            if phase == "SUBMITTED":
                p = r.get("instruction") or {}
                return (f"[S1R_ORDER] tick={r.get('tick')} book={book} "
                        f"side={self._side(self._pick(p, 'direction', 'side'))} "
                        f"type={self._short(self._pick(p, 'orderType', 'order_type', 'type'))} "
                        f"price={self._fmt(self._pick(p, 'price', 'limitPrice', 'limit_price'))} "
                        f"qty={self._fmt(self._pick(p, 'quantity', 'qty', 'size'))} "
                        f"tif={self._short(self._pick(p, 'timeInForce', 'time_in_force', 'tif'))} "
                        f"client_id={self._short(self._pick(p, 'clientOrderId', 'client_order_id'))} index={r.get('instruction_index')}")
            e = r.get("event") or {}
            if "TRADE" in phase or "FILL" in phase:
                return (f"[S1R_FILL] tick={r.get('tick')} book={book} phase={phase} "
                        f"side={self._side(self._pick(e, 'direction', 'side'))} "
                        f"price={self._fmt(self._pick(e, 'price', 'tradePrice', 'trade_price'))} "
                        f"qty={self._fmt(self._pick(e, 'quantity', 'qty', 'size'))} "
                        f"client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))} "
                        f"net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))}")
            if "REJECT" in phase or "FAIL" in phase:
                return (f"[S1R_REJECT] tick={r.get('tick')} book={book} phase={phase} "
                        f"reason={self._short(self._pick(e, 'reason', 'message', 'status', 'error'), 240)} "
                        f"client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}")
            if "CANCEL" in phase or "EXPIRE" in phase:
                return (f"[S1R_CANCEL] tick={r.get('tick')} book={book} phase={phase} "
                        f"reason={self._short(self._pick(e, 'reason', 'message', 'status'))} "
                        f"client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}")
            return f"[S1R_NOTICE] tick={r.get('tick')} book={book} phase={phase}"
        if typ == "RUN_SUMMARY":
            avg = r.get("average_latency_ms") or {}
            mx = r.get("max_latency_ms") or {}
            return (f"[S1R_SUMMARY] tick={r.get('tick')} responses={r.get('responses')} "
                    f"top_skips={self._counts(r.get('reason_counts') or {}, 8)} "
                    f"events={self._counts(r.get('event_counts') or {}, 8)} "
                    f"avg_total_ms={self._fmt(avg.get('total_ms'))} max_total_ms={self._fmt(mx.get('total_ms'))} "
                    f"opens={r.get('research_position_opens', 0)} "
                    f"reductions={r.get('research_position_reductions', 0)} "
                    f"round_trips={r.get('research_round_trip_closes', 0)} "
                    f"open_positions={r.get('research_open_positions', 0)} "
                    f"dust_blocks={r.get('research_dust_blocks', 0)} queue_dropped={self._rdropped}")
        if typ == "ERROR":
            return (f"[S1R_ERROR] tick={r.get('tick')} stage={self._short(r.get('stage'))} "
                    f"type={self._short(r.get('error_type'))} error={self._short(r.get('error'), 400)}")
        return None

    def _shutdown_research(self) -> None:
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
                self._rfile.flush(); self._rfile.close()
            except OSError:
                pass
            self._rfile = None

    @staticmethod
    def _pick(obj: Any, *names: str) -> Any:
        if obj is None:
            return None
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    @staticmethod
    def _int(v: Any) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fmt(v: Any) -> str:
        if v is None:
            return "-"
        if isinstance(v, bool):
            return "1" if v else "0"
        try:
            x = float(v)
        except (TypeError, ValueError):
            return str(v).replace(" ", "_")
        if abs(x) >= 1000:
            return f"{x:.3f}"
        if abs(x) >= 1:
            return f"{x:.6f}".rstrip("0").rstrip(".")
        return f"{x:.8f}".rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _short(v: Any, n: int = 120) -> str:
        if v is None:
            return "-"
        return "_".join(str(v).replace("\n", " ").replace("\r", " ").split())[:n]

    @classmethod
    def _side(cls, v: Any) -> str:
        if v is None:
            return "-"
        s = str(v).upper()
        if "BUY" in s or s == "BID":
            return "BID"
        if "SELL" in s or s == "ASK":
            return "ASK"
        return cls._short(v)

    @staticmethod
    def _counts(d: dict[str, Any], n: int) -> str:
        try:
            items = sorted(((str(k), int(v)) for k, v in d.items()), key=lambda kv: (-kv[1], kv[0]))[:n]
            return ",".join(f"{k}:{v}" for k, v in items) or "-"
        except Exception:
            return "-"


if __name__ == "__main__":
    launch(Strategy1_Research)
