# SPDX-License-Identifier: MIT
"""Strategy1 research V4.2 Strict: dust-aware actionable-fill execution.

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
14. Recent-PnL toxicity requires enough completed samples unless loss crosses a hard floor.
15. Sparse-tape YELLOW and GREEN books retain a conservative active path instead of reverting
    to DEAD solely because recent trade_rate is near zero.
16. Sub-minimum exact inventory remains precisely tracked and parked outside the finite
    management pool when it is too small to reduce safely with one minimum-size order.
17. Large dust (> half the exchange minimum) gets bounded passive DUST_COMPACT treatment;
    every possible partial fill is non-increasing in absolute exposure.
18. Kappa-completion priority concentrates quote opportunities on books with 1-2 confirmed
    realized observations so they can reach the 3-observation score-eligibility threshold.
19. Kappa completion is isolated from normal MM through separate attempt/success
    budgets; failed completion candidates cannot consume the normal economic lane.
20. Maker opening fills are classified as ACTIONABLE versus DUST; candidate ranking learns
    post-fill actionability instead of treating every partial fill as equally useful.
21. One-away Kappa books receive an extra bounded priority only when their learned fill
    quality is acceptable; dust-prone books are penalized without adding a hard gate.
22. Incomplete-book quotes can keep the original bounded maker exposure alive slightly longer
    (750ms cap by default), reducing premature sub-minimum residuals without rescue top-ups.
23. Safe dust compaction keeps the V4 theorem but adds fill-posterior ranking and bounded
    retry cooldown so repeated 1%-quality attempts do not dominate execution capacity.
24. Normal MM and explicit maintenance quote pairs are forced maker-only inside their quote
    context; inventory-management exits retain the inherited risk behavior.

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
    RESEARCH_POLICY_VERSION = "dust_actionable_v4_2_strict"
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
        "DUST_QUARANTINE": "DUST_PARK",
        "DUST_RELEASED": "DUST_RELEASE",
        "DUST_COMPACT": "DUST_COMPACT",
        "DUST_COMPACT_BLOCKED": "DUST_COMPACT_BLOCKED",
        "KAPPA_COMPLETION": "KAPPA_COMPLETE",
        "KAPPA_COMPLETION_ATTEMPT_CAP": "KAPPA_ATTEMPT_CAP",
        "KAPPA_COMPLETION_SUCCESS_CAP": "KAPPA_SUCCESS_CAP",
        "NORMAL_MM_ATTEMPT_CAP": "NORMAL_ATTEMPT_CAP",
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
        # V4.1 Strict execution-quality fixes, all derived from the completed
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
        self.research_green_sparse_active = self._as_bool(
            getattr(cfg, "research_green_sparse_active", True)
        )
        self.research_dust_park_enabled = self._as_bool(
            getattr(cfg, "research_dust_park_enabled", True)
        )
        self.research_dust_heartbeat_ticks = max(
            1, int(getattr(cfg, "research_dust_heartbeat_ticks", 250))
        )
        self.research_dust_warn_ticks = max(
            self.research_dust_heartbeat_ticks,
            int(getattr(cfg, "research_dust_warn_ticks", 1000)),
        )
        # V4.1 Strict: dust liveness. A minimum-size opposite order can only
        # reduce exposure for every possible partial fill when |dust| > 0.5*min.
        self.research_dust_compact_enabled = self._as_bool(
            getattr(cfg, "research_dust_compact_enabled", True)
        )
        self.research_dust_compact_min_fraction = max(
            0.500001,
            min(0.95, float(getattr(cfg, "research_dust_compact_min_fraction", 0.50))),
        )
        self.research_dust_compact_books_per_tick = max(
            1, int(getattr(cfg, "research_dust_compact_books_per_tick", 2))
        )

        # V4.1 Strict: concentrate realized observations so more individual books
        # cross the validator's per-book minimum observation requirement.
        self.research_kappa_completion_enabled = self._as_bool(
            getattr(cfg, "research_kappa_completion_enabled", True)
        )
        self.research_kappa_completion_target = max(
            2, int(getattr(cfg, "research_kappa_completion_target", 3))
        )
        self.research_kappa_completion_rank_bonus = max(
            0.0, float(getattr(cfg, "research_kappa_completion_rank_bonus", 0.30))
        )
        self.research_kappa_completion_fill_mult = max(
            0.50,
            min(1.0, float(getattr(cfg, "research_kappa_completion_fill_mult", 0.70))),
        )
        self.research_kappa_completion_fill_floor = max(
            0.0, float(getattr(cfg, "research_kappa_completion_fill_floor", 0.10))
        )
        self.research_kappa_completion_relaxed_success_cap = max(
            0, int(getattr(cfg, "research_kappa_completion_relaxed_success_cap", 2))
        )

        # V4.1 Strict: isolate Kappa completion from the normal economic lane.
        # The total attempt cap remains research_candidate_attempt_cap (12 by
        # default). Completion receives a hard sub-budget; the remainder is
        # reserved for normal candidates and cannot be stolen by completion
        # failures.
        requested_completion_attempt_cap = max(
            0, int(getattr(cfg, "research_kappa_completion_attempt_cap", 4))
        )
        self.research_kappa_completion_attempt_cap = min(
            self.research_candidate_attempt_cap,
            requested_completion_attempt_cap,
        )
        self.research_normal_attempt_cap = max(
            0,
            self.research_candidate_attempt_cap
            - self.research_kappa_completion_attempt_cap,
        )
        requested_completion_success_cap = max(
            0, int(getattr(cfg, "research_kappa_completion_success_cap", 2))
        )
        self.research_kappa_completion_success_cap = min(
            int(self.max_mm_books_per_tick),
            requested_completion_success_cap,
        )
        self.research_kappa_completion_relaxed_success_cap = min(
            self.research_kappa_completion_relaxed_success_cap,
            self.research_kappa_completion_success_cap,
        )

        self.research_kappa_completion_recent_pnl_floor = float(
            getattr(cfg, "research_kappa_completion_recent_pnl_floor", -0.01)
        )

        # --------------------------------------------------------------
        # V4.2 Strict: prevent the dominant long-run failure observed in the
        # research logs: maker partial fills create sub-minimum positions,
        # parked dust removes books from the usable completion pool, and Kappa
        # coverage stalls.  V4.2 learns fill QUALITY, not merely any-fill rate.
        # --------------------------------------------------------------
        self.research_actionable_fill_enabled = self._as_bool(
            getattr(cfg, "research_actionable_fill_enabled", True)
        )
        self.research_actionable_fill_min_samples = max(
            1, int(getattr(cfg, "research_actionable_fill_min_samples", 4))
        )
        self.research_actionable_fill_prior_strength = max(
            0.0, float(getattr(cfg, "research_actionable_fill_prior_strength", 6.0))
        )
        self.research_actionable_fill_prior_actionable = max(
            0.0, min(1.0, float(getattr(cfg, "research_actionable_fill_prior_actionable", 0.85)))
        )
        self.research_actionable_fill_rank_weight = max(
            0.0, float(getattr(cfg, "research_actionable_fill_rank_weight", 0.10))
        )
        self.research_dust_risk_rank_penalty = max(
            0.0, float(getattr(cfg, "research_dust_risk_rank_penalty", 0.18))
        )
        self.research_dust_risk_target = max(
            0.0, min(1.0, float(getattr(cfg, "research_dust_risk_target", 0.15)))
        )
        self.research_kappa_one_away_bonus = max(
            0.0, float(getattr(cfg, "research_kappa_one_away_bonus", 0.10))
        )

        # Bounded partial-fill hold: only extend the original completion quote
        # lifetime; never top up exposure. Default cap is 750ms simulation time.
        self.research_partial_fill_hold_enabled = self._as_bool(
            getattr(cfg, "research_partial_fill_hold_enabled", True)
        )
        self.research_partial_fill_hold_one_away_only = self._as_bool(
            getattr(cfg, "research_partial_fill_hold_one_away_only", False)
        )
        self.research_partial_fill_hold_max_ns = max(
            int(self.mm_expiry_period),
            int(getattr(cfg, "research_partial_fill_hold_max_ns", 750_000_000)),
        )
        self.research_partial_fill_hold_min_dust_prob = max(
            0.0, min(1.0, float(getattr(cfg, "research_partial_fill_hold_min_dust_prob", 0.12)))
        )

        # Maker-only is scoped to normal MM / explicit maintenance quote pairs.
        # Inventory-management exits continue to use the inherited fee/risk path.
        self.research_force_mm_post_only = self._as_bool(
            getattr(cfg, "research_force_mm_post_only", True)
        )

        # Adaptive compaction keeps the exact V4.1 exposure theorem unchanged;
        # it only changes which safe candidate is retried and when.
        self.research_dust_compact_adaptive = self._as_bool(
            getattr(cfg, "research_dust_compact_adaptive", True)
        )
        self.research_dust_compact_cooldown_ticks = max(
            1, int(getattr(cfg, "research_dust_compact_cooldown_ticks", 100))
        )
        self.research_dust_compact_max_cooldown_ticks = max(
            self.research_dust_compact_cooldown_ticks,
            int(getattr(cfg, "research_dust_compact_max_cooldown_ticks", 600)),
        )
        self.research_dust_compact_prior_fill = max(
            0.0, min(1.0, float(getattr(cfg, "research_dust_compact_prior_fill", 0.02)))
        )
        self.research_dust_compact_prior_strength = max(
            0.0, float(getattr(cfg, "research_dust_compact_prior_strength", 8.0))
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

        # V4.2 fill-quality and bounded liveness state.
        self._research_actionable_by_book: dict[int, dict[str, float | int]] = {}
        self._research_actionable_by_bucket: dict[tuple[int, str, int], dict[str, float | int]] = {}
        self._research_actionable_global_by_bucket: dict[tuple[str, int], dict[str, float | int]] = {}
        self._research_actionable_quote_count = 0
        self._research_actionable_maker_fills = 0
        self._research_actionable_fills = 0
        self._research_dust_maker_fills = 0
        self._research_partial_fill_hold_candidates = 0
        self._research_partial_fill_hold_quoted = 0
        self._research_forced_maker_quote_books = 0
        self._research_force_maker_context = False
        self._research_dust_compact_learning: dict[int, dict[str, float | int]] = {}
        self._research_dust_compact_cooldown_skips = 0

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
            "policy_version": self.RESEARCH_POLICY_VERSION,
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
            "green_sparse_active": self.research_green_sparse_active,
            "dust_safe_close": self.research_dust_safe_close,
            "dust_park_enabled": self.research_dust_park_enabled,
            "dust_heartbeat_ticks": self.research_dust_heartbeat_ticks,
            "dust_warn_ticks": self.research_dust_warn_ticks,
            "dust_compact_enabled": self.research_dust_compact_enabled,
            "dust_compact_min_fraction": self.research_dust_compact_min_fraction,
            "dust_compact_books_per_tick": self.research_dust_compact_books_per_tick,
            "kappa_completion_enabled": self.research_kappa_completion_enabled,
            "kappa_completion_target": self.research_kappa_completion_target,
            "kappa_completion_rank_bonus": self.research_kappa_completion_rank_bonus,
            "kappa_completion_fill_mult": self.research_kappa_completion_fill_mult,
            "kappa_completion_fill_floor": self.research_kappa_completion_fill_floor,
            "kappa_completion_relaxed_success_cap": self.research_kappa_completion_relaxed_success_cap,
            "kappa_completion_attempt_cap": self.research_kappa_completion_attempt_cap,
            "kappa_completion_success_cap": self.research_kappa_completion_success_cap,
            "normal_attempt_cap": self.research_normal_attempt_cap,
            "kappa_completion_recent_pnl_floor": self.research_kappa_completion_recent_pnl_floor,
            "actionable_fill_enabled": self.research_actionable_fill_enabled,
            "actionable_fill_min_samples": self.research_actionable_fill_min_samples,
            "actionable_fill_prior_strength": self.research_actionable_fill_prior_strength,
            "actionable_fill_prior_actionable": self.research_actionable_fill_prior_actionable,
            "actionable_fill_rank_weight": self.research_actionable_fill_rank_weight,
            "dust_risk_rank_penalty": self.research_dust_risk_rank_penalty,
            "dust_risk_target": self.research_dust_risk_target,
            "kappa_one_away_bonus": self.research_kappa_one_away_bonus,
            "partial_fill_hold_enabled": self.research_partial_fill_hold_enabled,
            "partial_fill_hold_one_away_only": self.research_partial_fill_hold_one_away_only,
            "partial_fill_hold_max_ns": self.research_partial_fill_hold_max_ns,
            "partial_fill_hold_min_dust_prob": self.research_partial_fill_hold_min_dust_prob,
            "force_mm_post_only": self.research_force_mm_post_only,
            "dust_compact_adaptive": self.research_dust_compact_adaptive,
            "dust_compact_cooldown_ticks": self.research_dust_compact_cooldown_ticks,
            "dust_compact_max_cooldown_ticks": self.research_dust_compact_max_cooldown_ticks,
            "dust_compact_prior_fill": self.research_dust_compact_prior_fill,
            "dust_compact_prior_strength": self.research_dust_compact_prior_strength,
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

    def _mem(self, book_id: int):
        """Attach the book id to parent BookMemory for completion-aware ranking."""
        mem = super()._mem(book_id)
        try:
            setattr(mem, "_research_book_id", int(book_id))
        except Exception:
            pass
        return mem

    def _prefer_maker(self, book_id: int) -> bool:
        """Force maker economics only inside explicitly scoped V4.2 quote contexts."""
        if (
            getattr(self, "research_force_mm_post_only", False)
            and getattr(self, "_research_force_maker_context", False)
        ):
            return True
        return super()._prefer_maker(book_id)

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
        """Honor explicit maintenance maker-only intent without changing exits."""
        old_context = bool(getattr(self, "_research_force_maker_context", False))
        if post_only and self.research_force_mm_post_only:
            self._research_force_maker_context = True
        try:
            return super()._place_round_trip_limits(
                response, state, book_id, size, post_only=post_only,
                expiry_period=expiry_period, client_id_base=client_id_base,
            )
        finally:
            self._research_force_maker_context = old_context

    def _record_fill_quote(self, mem, side: str, dist_from_touch: float) -> None:
        """Keep parent any-fill learning and add V4.2 quote-quality telemetry."""
        super()._record_fill_quote(mem, side, dist_from_touch)
        if not self.research_actionable_fill_enabled:
            return
        book_id = getattr(mem, "_research_book_id", None)
        if book_id is None:
            return
        bucket = self._spread_dist_bucket(float(dist_from_touch))
        key = (int(book_id), str(side), int(bucket))
        row = self._research_actionable_by_bucket.setdefault(
            key, {"quotes": 0, "maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0}
        )
        row["quotes"] = int(row.get("quotes", 0)) + 1
        grow = self._research_actionable_global_by_bucket.setdefault(
            (str(side), int(bucket)),
            {"quotes": 0, "maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0},
        )
        grow["quotes"] = int(grow.get("quotes", 0)) + 1
        self._research_actionable_quote_count += 1

    def _actionable_fill_snapshot(self, book_id: int) -> dict[str, float | int | str]:
        """Hierarchical post-fill quality: book first, then global distance buckets."""
        row = self._research_actionable_by_book.get(int(book_id), {})
        book_samples = int(row.get("maker_fills", 0) or 0)
        source = "BOOK"
        source_row = row

        if book_samples < self.research_actionable_fill_min_samples:
            mem = self._mem(int(book_id))
            global_rows = []
            for side, bucket in (
                ("buy", int(getattr(mem, "last_buy_dist_bucket", 0))),
                ("sell", int(getattr(mem, "last_sell_dist_bucket", 0))),
            ):
                grow = self._research_actionable_global_by_bucket.get((side, bucket), {})
                if int(grow.get("maker_fills", 0) or 0) > 0:
                    global_rows.append(grow)
            global_samples = sum(int(r.get("maker_fills", 0) or 0) for r in global_rows)
            if global_samples >= self.research_actionable_fill_min_samples:
                source = "GLOBAL_BUCKET"
                source_row = {
                    "maker_fills": global_samples,
                    "actionable": sum(int(r.get("actionable", 0) or 0) for r in global_rows),
                    "dust": sum(int(r.get("dust", 0) or 0) for r in global_rows),
                    "fill_fraction_sum": 0.0,
                }
            else:
                source = "PRIOR"
                source_row = {}

        samples = int(source_row.get("maker_fills", 0) or 0)
        actionable = int(source_row.get("actionable", 0) or 0)
        dust = int(source_row.get("dust", 0) or 0)
        prior_strength = float(self.research_actionable_fill_prior_strength)
        prior_a = float(self.research_actionable_fill_prior_actionable)
        denom = samples + prior_strength
        if denom > 0.0:
            p_actionable = (actionable + prior_strength * prior_a) / denom
            p_dust = (dust + prior_strength * (1.0 - prior_a)) / denom
        else:
            p_actionable = prior_a
            p_dust = 1.0 - prior_a
        mean_fill_fraction = (
            float(source_row.get("fill_fraction_sum", 0.0) or 0.0) / max(samples, 1)
        )
        return {
            "samples": samples,
            "book_samples": book_samples,
            "actionable": actionable,
            "dust": dust,
            "p_actionable": max(0.0, min(1.0, p_actionable)),
            "p_dust": max(0.0, min(1.0, p_dust)),
            "mean_fill_fraction": mean_fill_fraction,
            "confident": int(samples >= self.research_actionable_fill_min_samples),
            "source": source,
        }

    def _record_actionable_maker_fill(
        self,
        *,
        book_id: int,
        side: str,
        bucket: int,
        before: float,
        after: float,
        fill_qty: float,
        timestamp: int | None,
    ) -> dict[str, float | int | str | bool] | None:
        """Classify maker fills by post-fill state: ACTIONABLE/FLAT versus DUST."""
        if not self.research_actionable_fill_enabled:
            return None
        eps = self._execution_flat_epsilon()
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        if min_size <= 0.0:
            return None
        # Fill quality is defined by the POST-FILL inventory state.  A maker fill
        # is useful when it leaves either FLAT inventory or an executable position
        # >= the exchange minimum; it is harmful when it strands sub-minimum dust.
        is_flat = abs(after) < eps
        is_dust = self._is_dust_qty(after)
        is_actionable = bool(is_flat or (abs(after) + eps >= min_size))
        if not is_dust and not is_actionable:
            return None
        fill_fraction = abs(float(fill_qty)) / max(min_size, 1e-12)
        row = self._research_actionable_by_book.setdefault(
            int(book_id),
            {"maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0, "fill_fraction_sum": 0.0},
        )
        row["maker_fills"] = int(row.get("maker_fills", 0)) + 1
        row["actionable"] = int(row.get("actionable", 0)) + int(is_actionable)
        row["dust"] = int(row.get("dust", 0)) + int(is_dust)
        row["fill_qty"] = float(row.get("fill_qty", 0.0)) + abs(float(fill_qty))
        row["fill_fraction_sum"] = float(row.get("fill_fraction_sum", 0.0)) + fill_fraction

        key = (int(book_id), str(side), int(bucket))
        brow = self._research_actionable_by_bucket.setdefault(
            key, {"quotes": 0, "maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0}
        )
        brow["maker_fills"] = int(brow.get("maker_fills", 0)) + 1
        brow["actionable"] = int(brow.get("actionable", 0)) + int(is_actionable)
        brow["dust"] = int(brow.get("dust", 0)) + int(is_dust)
        brow["fill_qty"] = float(brow.get("fill_qty", 0.0)) + abs(float(fill_qty))
        grow = self._research_actionable_global_by_bucket.setdefault(
            (str(side), int(bucket)),
            {"quotes": 0, "maker_fills": 0, "actionable": 0, "dust": 0, "fill_qty": 0.0},
        )
        grow["maker_fills"] = int(grow.get("maker_fills", 0)) + 1
        grow["actionable"] = int(grow.get("actionable", 0)) + int(is_actionable)
        grow["dust"] = int(grow.get("dust", 0)) + int(is_dust)
        grow["fill_qty"] = float(grow.get("fill_qty", 0.0)) + abs(float(fill_qty))

        self._research_actionable_maker_fills += 1
        self._research_actionable_fills += int(is_actionable)
        self._research_dust_maker_fills += int(is_dust)
        snap = self._actionable_fill_snapshot(int(book_id))
        event = {
            "book_id": int(book_id),
            "side": str(side),
            "bucket": int(bucket),
            "timestamp": timestamp,
            "net_before": float(before),
            "net_after": float(after),
            "fill_qty": abs(float(fill_qty)),
            "fill_fraction": fill_fraction,
            "classification": (
                "DUST" if is_dust else ("FLAT" if is_flat else "ACTIONABLE")
            ),
            "actionable": bool(is_actionable),
            "flat": bool(is_flat),
            "dust": bool(is_dust),
            **snap,
        }
        self._emit("ACTIONABLE_FILL", tick=getattr(self, "_tick", None), **event)
        return event

    def _partial_fill_hold_expiry(
        self, state: MarketSimulationStateUpdate, book_id: int, completion_samples: int
    ) -> int:
        base = int(self.mm_expiry_period)
        if not self.research_partial_fill_hold_enabled:
            return base
        if completion_samples >= self.research_kappa_completion_target:
            return base
        if (
            self.research_partial_fill_hold_one_away_only
            and completion_samples != self.research_kappa_completion_target - 1
        ):
            return base
        quality = self._actionable_fill_snapshot(int(book_id))
        if float(quality["p_dust"]) + 1e-12 < self.research_partial_fill_hold_min_dust_prob:
            return base
        publish = int(getattr(getattr(state, "config", None), "publish_interval", base) or base)
        return max(base, min(int(self.research_partial_fill_hold_max_ns), publish))

    def _completion_observation_count(self, book_id: int) -> int:
        return int(self._research_realized_observations_by_book.get(int(book_id), 0))

    def _is_kappa_completion_candidate(self, book_id: int) -> bool:
        if not self.research_kappa_completion_enabled:
            return False
        samples = self._completion_observation_count(book_id)
        if samples <= 0 or samples >= self.research_kappa_completion_target:
            return False
        mem = self._mem(book_id)
        return float(getattr(mem, "recent_pnl", 0.0) or 0.0) >= (
            self.research_kappa_completion_recent_pnl_floor
        )

    def _global_book_rank(self, expected_alpha: float, mem) -> float:
        """V4.2 rank: preserve economics, finish Kappa, penalize dust-prone fills."""
        base_rank = super()._global_book_rank(expected_alpha, mem)
        book_id = getattr(mem, "_research_book_id", None)
        if book_id is None:
            return base_rank
        book_id = int(book_id)

        if self.research_kappa_completion_enabled and self._is_kappa_completion_candidate(book_id):
            samples = self._completion_observation_count(book_id)
            denom = max(1, self.research_kappa_completion_target - 1)
            progress = max(0.0, min(1.0, samples / denom))
            base_rank += self.research_kappa_completion_rank_bonus * progress

        if not self.research_actionable_fill_enabled:
            return base_rank
        quality = self._actionable_fill_snapshot(book_id)
        samples = int(quality["samples"])
        confident = samples >= self.research_actionable_fill_min_samples
        if confident:
            p_actionable = float(quality["p_actionable"])
            p_dust = float(quality["p_dust"])
            # Adjust only relative to the run-calibrated neutral prior.  This
            # prevents the fill-quality layer from simply rewarding every book.
            quality_adjust = self.research_actionable_fill_rank_weight * (
                p_actionable - self.research_actionable_fill_prior_actionable
            )
            dust_penalty = self.research_dust_risk_rank_penalty * max(
                0.0, p_dust - self.research_dust_risk_target
            )
            base_rank += quality_adjust - dust_penalty

        if (
            self.research_kappa_completion_enabled
            and self._is_kappa_completion_candidate(book_id)
            and self._completion_observation_count(book_id)
                == self.research_kappa_completion_target - 1
        ):
            # A one-away completion immediately creates an eligible book.  Weight
            # the extra bonus by learned actionability when confidence exists.
            quality_scale = (
                0.50 + 0.50 * float(quality["p_actionable"])
                if confident else 0.75
            )
            base_rank += self.research_kappa_one_away_bonus * quality_scale
        return base_rank

    def _is_compactable_dust(self, net_base: float) -> bool:
        if not self.research_dust_compact_enabled or not self._is_dust_qty(net_base):
            return False
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        if min_size <= 0.0:
            return False
        abs_base = abs(float(net_base))
        threshold = self.research_dust_compact_min_fraction * min_size
        return abs_base > threshold + self._execution_flat_epsilon()

    def _dust_compaction_learning_snapshot(self, book_id: int) -> dict[str, float | int]:
        row = self._research_dust_compact_learning.get(int(book_id), {})
        attempts = int(row.get("attempts", 0) or 0)
        successes = int(row.get("successful_attempts", 0) or 0)
        prior_strength = float(self.research_dust_compact_prior_strength)
        denom = attempts + prior_strength
        p_fill = (
            (successes + prior_strength * self.research_dust_compact_prior_fill) / denom
            if denom > 0.0 else self.research_dust_compact_prior_fill
        )
        return {
            "attempts": attempts,
            "successful_attempts": successes,
            "p_fill": max(0.0, min(1.0, p_fill)),
            "failure_streak": int(row.get("failure_streak", 0) or 0),
            "last_attempt_tick": int(row.get("last_attempt_tick", -1) or -1),
            "next_allowed_tick": int(row.get("next_allowed_tick", 0) or 0),
        }

    def _record_dust_compaction_attempt(self, book_id: int) -> None:
        now = int(getattr(self, "_tick", 0) or 0)
        row = self._research_dust_compact_learning.setdefault(
            int(book_id),
            {"attempts": 0, "successful_attempts": 0, "failure_streak": 0,
             "last_attempt_tick": -1, "last_success_attempt_tick": -1,
             "next_allowed_tick": 0},
        )
        prev = int(row.get("last_attempt_tick", -1) or -1)
        success_prev = int(row.get("last_success_attempt_tick", -1) or -1)
        if prev >= 0 and success_prev != prev:
            row["failure_streak"] = int(row.get("failure_streak", 0) or 0) + 1
        row["attempts"] = int(row.get("attempts", 0) or 0) + 1
        row["last_attempt_tick"] = now
        cooldown = min(
            self.research_dust_compact_max_cooldown_ticks,
            self.research_dust_compact_cooldown_ticks
            * max(1, int(row.get("failure_streak", 0) or 0) + 1),
        )
        row["next_allowed_tick"] = now + int(cooldown)

    def _record_dust_compaction_success(self, book_id: int) -> None:
        submitted_tick = self._research_dust_compact_active.get(int(book_id))
        if submitted_tick is None:
            return
        row = self._research_dust_compact_learning.setdefault(
            int(book_id),
            {"attempts": 0, "successful_attempts": 0, "failure_streak": 0,
             "last_attempt_tick": int(submitted_tick), "last_success_attempt_tick": -1,
             "next_allowed_tick": 0},
        )
        if int(row.get("last_success_attempt_tick", -1) or -1) != int(submitted_tick):
            row["successful_attempts"] = int(row.get("successful_attempts", 0) or 0) + 1
            row["last_success_attempt_tick"] = int(submitted_tick)
        row["failure_streak"] = 0
        row["next_allowed_tick"] = int(getattr(self, "_tick", 0) or 0) + (
            self.research_dust_compact_cooldown_ticks
        )

    def _select_dust_compaction_books(self, state: MarketSimulationStateUpdate) -> set[int]:
        """Rank only theorem-safe dust, with bounded retry cooldown and learned fill quality."""
        if not self.research_dust_compact_enabled:
            return set()
        tick = int(getattr(self, "_tick", 0) or 0)
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        rows: list[tuple[float, float, int, int]] = []
        for book_id, info in self._research_parked_dust.items():
            qty = float(info.get("net_base", 0.0) or 0.0)
            if not self._is_compactable_dust(qty):
                continue
            if book_id not in getattr(state, "books", {}):
                continue
            first_tick = int(info.get("first_tick", tick))
            age = max(0, tick - first_tick)
            learn = self._dust_compaction_learning_snapshot(int(book_id))
            if (
                self.research_dust_compact_adaptive
                and tick < int(learn["next_allowed_tick"])
            ):
                self._research_dust_compact_cooldown_skips += 1
                continue
            size_fraction = abs(qty) / max(min_size, 1e-12)
            age_score = min(1.0, age / max(float(self.research_dust_warn_ticks), 1.0))
            score = (
                2.0 * float(learn["p_fill"])
                + 0.55 * min(1.0, size_fraction)
                + 0.45 * age_score
            )
            rows.append((score, abs(qty), age, int(book_id)))
        rows.sort(reverse=True)
        return {
            book_id
            for _score, _abs_qty, _age, book_id
            in rows[: self.research_dust_compact_books_per_tick]
        }

    def _dust_compaction_safe_for_any_fill(self, net_base: float) -> bool:
        """Proof condition for q -> q - sign(q)*f, 0<=f<=min_size."""
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        q = abs(float(net_base))
        return min_size > 0.0 and q > 0.5 * min_size and q < min_size

    def _sparse_active_tier_enabled(self, tier: str) -> bool:
        tier_u = str(tier or "").upper()
        return (
            (tier_u == "YELLOW" and self.research_yellow_sparse_active)
            or (tier_u == "GREEN" and self.research_green_sparse_active)
        )

    def _is_dust_qty(self, net_base: float) -> bool:
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        abs_base = abs(float(net_base))
        return (
            self.research_dust_safe_close
            and min_size > 0.0
            and abs_base >= self._execution_flat_epsilon()
            and abs_base + 1e-12 < min_size
        )

    def _refresh_dust_state(
        self,
        book_id: int,
        net_base: float,
        *,
        emit: bool = True,
    ) -> bool:
        """Park exchange-uncloseable residuals without hiding exact inventory.

        V4.1 Strict does not synthesize an exchange-illegal exact close and does
        not add fresh risk merely to make dust executable. Dust remains in exact
        accounting, is removed from the finite management queue, and is
        quarantined from fresh MM/maintenance orders.
        """
        if not self.research_dust_park_enabled:
            self._research_parked_dust.pop(book_id, None)
            return False

        tick = int(getattr(self, "_tick", 0) or 0)
        qty = float(net_base)
        is_dust = self._is_dust_qty(qty)
        prior = self._research_parked_dust.get(book_id)

        if not is_dust:
            if prior is not None:
                self._research_dust_releases += 1
                self._research_parked_dust.pop(book_id, None)
                if emit:
                    self._emit(
                        "POSITION_GUARD",
                        tick=tick,
                        book_id=book_id,
                        reason="DUST_RELEASED",
                        net_base=qty,
                        min_order_size=self._research_exchange_min_order_size,
                        first_tick=prior.get("first_tick"),
                        age_ticks=max(0, tick - int(prior.get("first_tick", tick))),
                        parked=False,
                    )
            return False

        if prior is None:
            prior = {
                "first_tick": tick,
                "last_tick": tick,
                "last_emit_tick": tick,
                "net_base": qty,
            }
            self._research_parked_dust[book_id] = prior
            self._research_dust_entries += 1
            self._research_dust_blocks += 1
            if emit:
                self._emit(
                    "POSITION_GUARD",
                    tick=tick,
                    book_id=book_id,
                    reason="DUST_POSITION",
                    net_base=qty,
                    min_order_size=self._research_exchange_min_order_size,
                    first_tick=tick,
                    age_ticks=0,
                    parked=True,
                    stale=False,
                )
            return True

        prior["last_tick"] = tick
        prior["net_base"] = qty
        age = max(0, tick - int(prior.get("first_tick", tick)))
        last_emit = int(prior.get("last_emit_tick", tick))
        if emit and tick - last_emit >= self.research_dust_heartbeat_ticks:
            prior["last_emit_tick"] = tick
            self._research_dust_heartbeats += 1
            self._emit(
                "POSITION_GUARD",
                tick=tick,
                book_id=book_id,
                reason="DUST_HEARTBEAT",
                net_base=qty,
                min_order_size=self._research_exchange_min_order_size,
                first_tick=prior.get("first_tick"),
                age_ticks=age,
                parked=True,
                stale=(age >= self.research_dust_warn_ticks),
            )
        return True

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
        profile_book_id = getattr(profile, "book_id", None)
        parked_dust = (
            self.research_dust_park_enabled
            and profile_book_id in self._research_parked_dust
        )

        # Parked dust is an execution quarantine, not a market-risk opinion.
        # Represent it as TOXIC_BOOK only to guarantee inherited maintenance/MM
        # paths cannot add a new order; archetype_source preserves the semantics.
        if parked_dust:
            archetype: BookArchetype = "TOXIC_BOOK"
            source = "PARKED_DUST"
        # Local risk always wins. Global STRESSED is deliberately not a local
        # archetype condition; otherwise one regime bit poisons all books.
        elif spread_bps >= stress_cutoff:
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
            self._sparse_active_tier_enabled(tier)
            and trade_rate < self.archetype_dead_trade_rate
        ):
            # V4.1 Strict bridge: a book that already earned YELLOW history but has
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
            record["parked_dust"] = parked_dust
            record["dead_trade_rate_hit"] = trade_rate < self.archetype_dead_trade_rate
            record["active_sparse"] = (
                self._sparse_active_tier_enabled(tier)
                and trade_rate < self.archetype_dead_trade_rate
            )
            record["active_sparse_tier"] = tier if record["active_sparse"] else None
        return archetype

    def is_toxic_book(
        self,
        book_id: int,
        profile: BookProfile,
        archetype: BookArchetype,
    ) -> bool:
        dust_info = self._research_parked_dust.get(book_id)
        if self.research_dust_park_enabled and dust_info is not None:
            if self.debug_enabled:
                record = self._book_record(book_id)
                record["dust_position"] = True
                record["dust_quarantine"] = True
                record["dust_qty"] = abs(float(dust_info.get("net_base", 0.0)))
                record["toxic"] = False
            # Parent Strategy1 exposes a boolean pre-candidate exclusion hook.
            # Use it as quarantine transport; emitted diagnostics rewrite this
            # to DUST_QUARANTINE so parked dust is not called genuine toxicity.
            return True

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
        try:
            setattr(inventory, "_research_book_id", int(book_id))
        except Exception:
            pass
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
        self._refresh_dust_state(book_id, net_base, emit=True)
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

        # V4.1 Strict: only mathematically safe, explicitly selected dust enters
        # management. Tiny/nonselected dust remains parked and consumes no slot.
        if (
            self.research_dust_park_enabled
            and self._is_dust_qty(inventory.net_base)
        ):
            book_id = getattr(inventory, "_research_book_id", None)
            return (
                book_id is not None
                and int(book_id) in self._research_dust_compact_ids_this_tick
                and self._dust_compaction_safe_for_any_fill(inventory.net_base)
            )

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

            # V4.1 Strict deliberately removes age-only / close-score-only market
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
            and self._is_dust_qty(inventory.net_base)
        ):
            self._refresh_dust_state(book_id, inventory.net_base, emit=True)
            compact_selected = (
                book_id in self._research_dust_compact_ids_this_tick
                and self._dust_compaction_safe_for_any_fill(inventory.net_base)
            )
            if compact_selected:
                self._research_dust_compact_attempts += 1
                before_ix = len(response.instructions)
                n = super()._place_passive_inventory_exit(
                    response,
                    state,
                    book_id,
                    book,
                    inventory,
                    min_size,
                )
                if n:
                    self._research_dust_compact_orders += 1
                    self._research_dust_compact_active[book_id] = int(
                        getattr(self, "_tick", 0) or 0
                    )
                    if self.research_dust_compact_adaptive:
                        self._record_dust_compaction_attempt(book_id)
                    self._inventory_reason[book_id] = "DUST_COMPACT"
                    self._emit(
                        "POSITION_GUARD",
                        tick=getattr(self, "_tick", None),
                        book_id=book_id,
                        reason="DUST_COMPACT",
                        net_base=inventory.net_base,
                        min_order_size=min_size,
                        projected_full_fill_net=(
                            float(inventory.net_base)
                            - (min_size if inventory.net_base > 0.0 else -min_size)
                        ),
                        exposure_nonincreasing=True,
                        instructions=len(response.instructions) - before_ix,
                    )
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record["action"] = "MANAGE"
                        record["reason"] = "DUST_COMPACT"
                        record["dust_compact"] = True
                        record["dust_compact_qty"] = min_size
                        record["instructions"] = n
                    return n

                self._emit(
                    "POSITION_GUARD",
                    tick=getattr(self, "_tick", None),
                    book_id=book_id,
                    reason="DUST_COMPACT_BLOCKED",
                    net_base=inventory.net_base,
                    min_order_size=min_size,
                    exposure_nonincreasing=True,
                )

            if self.debug_enabled:
                record = self._book_record(book_id)
                record["dust_position"] = True
                record["dust_quarantine"] = True
                record["dust_qty"] = abs_base
                record["min_order_size"] = min_size
                record["dust_compact_selected"] = compact_selected
            return 0
        return super()._manage_inventory(
            response, state, book_id, book, inventory, regime_params, regime, archetype
        )

    def _dust_fill_matches_recent_compaction(self, book_id: int) -> bool:
        """Telemetry-only attribution guard for DUST_COMPACT fills.

        A dust transition is counted as a compaction fill only when this book
        actually emitted a DUST_COMPACT order recently. This prevents natural
        residual cleanup from inflating the compaction-fill metric.
        """
        submitted_tick = self._research_dust_compact_active.get(int(book_id))
        if submitted_tick is None:
            return False
        now = int(getattr(self, "_tick", 0) or 0)
        # Notices/fills can be observed on the following request. Keep a short
        # two-tick attribution window; repeated compaction attempts refresh it.
        return 0 <= now - int(submitted_tick) <= 2

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
        realized_delta = pnl_after - pnl_before
        if abs(realized_delta) > 1e-12:
            self._research_realized_observations_by_book[book_id] = (
                self._research_realized_observations_by_book.get(book_id, 0) + 1
            )

        before_was_dust = self._is_dust_qty(before)
        self._refresh_dust_state(book_id, after, emit=True)
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
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
                if self.research_dust_compact_adaptive:
                    self._record_dust_compaction_success(book_id)
        elif before * after < 0.0:
            # A cross closes the old FIFO lifecycle and opens a new opposite
            # residual. Check this BEFORE REDUCE/INCREASE magnitude tests.
            transition = "CROSS"
            self._research_position_reductions += 1
            if abs(realized_delta) > 1e-12:
                self._research_round_trip_closes += 1
                self._research_round_trip_samples_by_book[book_id] = (
                    self._research_round_trip_samples_by_book.get(book_id, 0) + 1
                )
            self._position_ticks[book_id] = 0
            self._research_position_tick_seen[book_id] = int(getattr(self, "_tick", 0) or 0)
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
                if self.research_dust_compact_adaptive:
                    self._record_dust_compaction_success(book_id)
                self._inventory_reason[book_id] = "DUST_COMPACT"
        elif abs(after) < abs(before) - eps:
            transition = "REDUCE"
            self._research_position_reductions += 1
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
                if self.research_dust_compact_adaptive:
                    self._record_dust_compaction_success(book_id)
        elif abs(after) > abs(before) + eps:
            transition = "INCREASE"
        else:
            transition = "UNCHANGED"

        round_trip_event = transition == "FLAT" or (
            transition == "CROSS" and abs(realized_delta) > 1e-12
        )

        actionable_event = None
        is_maker = getattr(event, "makerAgentId", None) == getattr(self, "uid", None)
        if is_maker:
            # Parent semantics: event.side==1 means our maker was the buyer;
            # event.side==0 means our maker was the seller.
            side = "buy" if getattr(event, "side", None) == 1 else "sell"
            mem = self._mem(book_id)
            bucket = int(
                getattr(mem, "last_buy_dist_bucket", 0)
                if side == "buy"
                else getattr(mem, "last_sell_dist_bucket", 0)
            )
            actionable_event = self._record_actionable_maker_fill(
                book_id=int(book_id),
                side=side,
                bucket=bucket,
                before=before,
                after=after,
                fill_qty=float(getattr(event, "quantity", 0.0) or 0.0),
                timestamp=getattr(event, "timestamp", None),
            )

        self._emit(
            "POSITION",
            tick=getattr(self, "_tick", None),
            timestamp=getattr(event, "timestamp", None),
            book_id=book_id,
            transition=transition,
            net_before=before,
            net_after=after,
            realized_pnl_delta=realized_delta,
            realized_book_observations=self._research_realized_observations_by_book.get(book_id, 0),
            round_trip=round_trip_event,
            round_trip_total=self._research_round_trip_closes,
            round_trip_book_samples=self._research_round_trip_samples_by_book.get(book_id, 0),
            execution_flat_epsilon=eps,
            reason=self._inventory_reason.get(
                book_id,
                "FLAT" if transition == "FLAT" else "UNKNOWN",
            ),
            actionable_fill_class=(
                actionable_event.get("classification") if actionable_event else None
            ),
            actionable_fill_fraction=(
                actionable_event.get("fill_fraction") if actionable_event else None
            ),
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
        """Quote with isolated NORMAL and KAPPA_COMPLETION scheduler lanes.

        V4 allowed every Kappa-completion failure to consume the same shared
        candidate-attempt budget as normal economics. V4.1 gives completion a
        bounded sub-budget (default 4 attempts / 2 successes) and reserves the
        remainder (default 8 attempts / at least 2 success capacity) for normal
        MM. Completion-cap skips do not consume normal or total attempt budget.
        """
        completion_candidate = (
            inventory.band == "FLAT"
            and self._is_kappa_completion_candidate(book_id)
        )
        completion_samples = self._completion_observation_count(book_id)
        lane = "COMPLETION" if completion_candidate else "NORMAL"

        if self._research_backfill_active:
            if self._research_quote_successes >= self._research_quote_success_cap:
                if self.debug_enabled:
                    record = self._book_record(book_id)
                    record["action"] = "SKIP"
                    record["reason"] = "MM_SUCCESS_CAP"
                    record["scheduler_lane"] = lane
                return 0

            if completion_candidate:
                if (
                    self._research_completion_quote_successes
                    >= self.research_kappa_completion_success_cap
                ):
                    self._research_completion_success_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record["action"] = "SKIP"
                        record["reason"] = "KAPPA_COMPLETION_SUCCESS_CAP"
                        record["scheduler_lane"] = lane
                    return 0
                if (
                    self._research_completion_quote_attempts
                    >= self.research_kappa_completion_attempt_cap
                ):
                    self._research_completion_attempt_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record["action"] = "SKIP"
                        record["reason"] = "KAPPA_COMPLETION_ATTEMPT_CAP"
                        record["scheduler_lane"] = lane
                    return 0
                self._research_completion_quote_attempts += 1
            else:
                if (
                    self._research_normal_quote_attempts
                    >= self.research_normal_attempt_cap
                ):
                    self._research_normal_attempt_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record["action"] = "SKIP"
                        record["reason"] = "NORMAL_MM_ATTEMPT_CAP"
                        record["scheduler_lane"] = lane
                    return 0
                self._research_normal_quote_attempts += 1

            # Only a candidate actually admitted into one of the two lanes
            # consumes the global attempt budget.
            self._research_quote_attempts += 1

        allow_relaxed_fill = (
            completion_candidate
            and self._research_completion_relaxed_successes
                < self.research_kappa_completion_relaxed_success_cap
        )

        old_min_fill = float(regime_params.min_fill_prob)
        relaxed_min_fill = old_min_fill
        if allow_relaxed_fill:
            relaxed_min_fill = max(
                self.research_kappa_completion_fill_floor,
                old_min_fill * self.research_kappa_completion_fill_mult,
            )
            regime_params.min_fill_prob = min(old_min_fill, relaxed_min_fill)
            self._research_completion_relaxed_attempts += 1

        if self.debug_enabled:
            record = self._book_record(book_id)
            record["scheduler_lane"] = lane
            record["normal_attempts_used"] = self._research_normal_quote_attempts
            record["normal_attempt_cap"] = self.research_normal_attempt_cap
            record["completion_attempts_used"] = self._research_completion_quote_attempts
            record["completion_attempt_cap"] = self.research_kappa_completion_attempt_cap
            record["completion_successes_used"] = self._research_completion_quote_successes
            record["completion_success_cap"] = self.research_kappa_completion_success_cap
            record["kappa_completion_candidate"] = completion_candidate
            record["kappa_completion_samples"] = completion_samples
            record["kappa_completion_target"] = self.research_kappa_completion_target
            record["kappa_completion_fill_relaxed"] = allow_relaxed_fill
            record["kappa_completion_min_fill_original"] = old_min_fill
            record["kappa_completion_min_fill_effective"] = float(regime_params.min_fill_prob)

        old_expiry = int(self.mm_expiry_period)
        old_maker_context = bool(getattr(self, "_research_force_maker_context", False))
        hold_expiry = old_expiry
        hold_active = False
        incomplete_flat = (
            inventory.band == "FLAT"
            and completion_samples < self.research_kappa_completion_target
        )
        if incomplete_flat:
            hold_expiry = self._partial_fill_hold_expiry(
                state, book_id, completion_samples
            )
            hold_active = hold_expiry > old_expiry
            if hold_active:
                self._research_partial_fill_hold_candidates += 1
                self.mm_expiry_period = hold_expiry
        if self.research_force_mm_post_only:
            self._research_force_maker_context = True

        if self.debug_enabled:
            record = self._book_record(book_id)
            quality = self._actionable_fill_snapshot(book_id)
            record["actionable_fill_samples"] = quality["samples"]
            record["actionable_fill_p"] = quality["p_actionable"]
            record["dust_fill_p"] = quality["p_dust"]
            record["actionable_fill_source"] = quality["source"]
            record["partial_fill_hold"] = hold_active
            record["partial_fill_hold_expiry_ns"] = hold_expiry
            record["force_mm_post_only"] = bool(self.research_force_mm_post_only)

        try:
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
        finally:
            regime_params.min_fill_prob = old_min_fill
            self.mm_expiry_period = old_expiry
            self._research_force_maker_context = old_maker_context

        if self._research_backfill_active and placed:
            self._research_quote_successes += 1
            if completion_candidate:
                self._research_completion_quote_successes += 1
            else:
                self._research_normal_quote_successes += 1

        if completion_candidate and placed:
            if allow_relaxed_fill:
                self._research_completion_relaxed_successes += 1
            if hold_active:
                self._research_partial_fill_hold_quoted += 1
            if self.debug_enabled:
                self._book_record(book_id)["kappa_completion_quote_success"] = True
        if placed and self.research_force_mm_post_only:
            self._research_forced_maker_quote_books += 1

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
            # Parent Strategy1 slices mm_candidates before calling our quote
            # hook. Scan the full current profile set so candidates rejected by
            # the completion lane cannot prevent normal candidates later in the
            # ranking from being evaluated. Actual expensive quote attempts are
            # still bounded by the lane caps (8 normal + 4 completion = 12).
            profile_scan = len(getattr(selection, "profiles", []) or [])
            self.max_mm_books_per_tick = max(
                old_max_mm_books,
                self.research_candidate_attempt_cap,
                profile_scan,
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
                stats["research_parked_dust"] = len(self._research_parked_dust)
                stats["research_dust_entries"] = self._research_dust_entries
                stats["research_dust_releases"] = self._research_dust_releases
                stats["research_dust_heartbeats"] = self._research_dust_heartbeats
                stats["research_dust_compact_selected"] = len(self._research_dust_compact_ids_this_tick)
                stats["research_dust_compact_attempts"] = self._research_dust_compact_attempts
                stats["research_dust_compact_orders"] = self._research_dust_compact_orders
                stats["research_dust_compact_fills"] = self._research_dust_compact_fills
                stats["research_quote_attempts"] = self._research_quote_attempts
                stats["research_normal_quote_attempts"] = self._research_normal_quote_attempts
                stats["research_normal_quote_successes"] = self._research_normal_quote_successes
                stats["research_normal_attempt_cap"] = self.research_normal_attempt_cap
                stats["research_completion_quote_attempts"] = self._research_completion_quote_attempts
                stats["research_completion_quote_successes"] = self._research_completion_quote_successes
                stats["research_completion_attempt_cap"] = self.research_kappa_completion_attempt_cap
                stats["research_completion_success_cap"] = self.research_kappa_completion_success_cap
                stats["research_completion_relaxed_attempts"] = self._research_completion_relaxed_attempts
                stats["research_completion_relaxed_successes"] = self._research_completion_relaxed_successes
                stats["research_completion_attempt_cap_hits"] = self._research_completion_attempt_cap_hits
                stats["research_completion_success_cap_hits"] = self._research_completion_success_cap_hits
                stats["research_normal_attempt_cap_hits"] = self._research_normal_attempt_cap_hits
                stats["research_quote_successes"] = self._research_quote_successes
                stats["research_quote_success_cap"] = self._research_quote_success_cap
                stats["research_actionable_maker_fills"] = self._research_actionable_maker_fills
                stats["research_actionable_fills"] = self._research_actionable_fills
                stats["research_dust_maker_fills"] = self._research_dust_maker_fills
                stats["research_partial_fill_hold_candidates"] = self._research_partial_fill_hold_candidates
                stats["research_partial_fill_hold_quoted"] = self._research_partial_fill_hold_quoted
                stats["research_forced_maker_quote_books"] = self._research_forced_maker_quote_books
                stats["research_dust_compact_cooldown_skips"] = self._research_dust_compact_cooldown_skips
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
        if (
            record.get("dust_quarantine")
            and reason in ("TOXIC_BOOK", "TOXIC_REGIME")
        ):
            reason = "DUST_QUARANTINE"
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
            active_sparse_tier=record.get("active_sparse_tier"),
            dust_quarantine=record.get("dust_quarantine"),
            dust_compact=record.get("dust_compact"),
            dust_compact_selected=record.get("dust_compact_selected"),
            scheduler_lane=record.get("scheduler_lane"),
            normal_attempts_used=record.get("normal_attempts_used"),
            normal_attempt_cap=record.get("normal_attempt_cap"),
            completion_attempts_used=record.get("completion_attempts_used"),
            completion_attempt_cap=record.get("completion_attempt_cap"),
            completion_successes_used=record.get("completion_successes_used"),
            completion_success_cap=record.get("completion_success_cap"),
            kappa_completion_candidate=record.get("kappa_completion_candidate"),
            kappa_completion_samples=record.get("kappa_completion_samples"),
            kappa_completion_target=record.get("kappa_completion_target"),
            kappa_completion_fill_relaxed=record.get("kappa_completion_fill_relaxed"),
            kappa_completion_min_fill_original=record.get("kappa_completion_min_fill_original"),
            kappa_completion_min_fill_effective=record.get("kappa_completion_min_fill_effective"),
            kappa_completion_quote_success=record.get("kappa_completion_quote_success"),
            actionable_fill_samples=record.get("actionable_fill_samples"),
            actionable_fill_p=record.get("actionable_fill_p"),
            dust_fill_p=record.get("dust_fill_p"),
            actionable_fill_source=record.get("actionable_fill_source"),
            partial_fill_hold=record.get("partial_fill_hold"),
            partial_fill_hold_expiry_ns=record.get("partial_fill_hold_expiry_ns"),
            force_mm_post_only=record.get("force_mm_post_only"),
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
            payload.setdefault("research_parked_dust_positions", len(getattr(self, "_research_parked_dust", {})))
            payload.setdefault("research_dust_entries", getattr(self, "_research_dust_entries", 0))
            payload.setdefault("research_dust_releases", getattr(self, "_research_dust_releases", 0))
            payload.setdefault("research_dust_heartbeats", getattr(self, "_research_dust_heartbeats", 0))
            payload.setdefault("research_dust_compact_attempts", getattr(self, "_research_dust_compact_attempts", 0))
            payload.setdefault("research_dust_compact_orders", getattr(self, "_research_dust_compact_orders", 0))
            payload.setdefault("research_dust_compact_fills", getattr(self, "_research_dust_compact_fills", 0))
            payload.setdefault("research_dust_compact_cooldown_skips", getattr(self, "_research_dust_compact_cooldown_skips", 0))
            payload.setdefault("research_actionable_quote_count", getattr(self, "_research_actionable_quote_count", 0))
            payload.setdefault("research_actionable_maker_fills", getattr(self, "_research_actionable_maker_fills", 0))
            payload.setdefault("research_actionable_fills", getattr(self, "_research_actionable_fills", 0))
            payload.setdefault("research_dust_maker_fills", getattr(self, "_research_dust_maker_fills", 0))
            maker_fills = max(1, int(getattr(self, "_research_actionable_maker_fills", 0)))
            payload.setdefault("research_actionable_fill_ratio", getattr(self, "_research_actionable_fills", 0) / maker_fills)
            payload.setdefault("research_dust_maker_fill_ratio", getattr(self, "_research_dust_maker_fills", 0) / maker_fills)
            payload.setdefault("research_partial_fill_hold_candidates", getattr(self, "_research_partial_fill_hold_candidates", 0))
            payload.setdefault("research_partial_fill_hold_quoted", getattr(self, "_research_partial_fill_hold_quoted", 0))
            payload.setdefault("research_forced_maker_quote_books", getattr(self, "_research_forced_maker_quote_books", 0))
            payload.setdefault("research_normal_quote_attempts", getattr(self, "_research_normal_quote_attempts", 0))
            payload.setdefault("research_normal_quote_successes", getattr(self, "_research_normal_quote_successes", 0))
            payload.setdefault("research_normal_attempt_cap", getattr(self, "research_normal_attempt_cap", 0))
            payload.setdefault("research_completion_quote_attempts", getattr(self, "_research_completion_quote_attempts", 0))
            payload.setdefault("research_completion_quote_successes", getattr(self, "_research_completion_quote_successes", 0))
            payload.setdefault("research_completion_attempt_cap", getattr(self, "research_kappa_completion_attempt_cap", 0))
            payload.setdefault("research_completion_success_cap", getattr(self, "research_kappa_completion_success_cap", 0))
            payload.setdefault("research_completion_relaxed_attempts", getattr(self, "_research_completion_relaxed_attempts", 0))
            payload.setdefault("research_completion_relaxed_successes", getattr(self, "_research_completion_relaxed_successes", 0))
            payload.setdefault("research_completion_attempt_cap_hits", getattr(self, "_research_completion_attempt_cap_hits", 0))
            payload.setdefault("research_completion_success_cap_hits", getattr(self, "_research_completion_success_cap_hits", 0))
            payload.setdefault("research_normal_attempt_cap_hits", getattr(self, "_research_normal_attempt_cap_hits", 0))
            obs_counts = getattr(self, "_research_realized_observations_by_book", {})
            target = int(getattr(self, "research_kappa_completion_target", 3))
            payload.setdefault("research_realized_observation_total", sum(obs_counts.values()))
            payload.setdefault("research_kappa_books_with_obs", sum(1 for v in obs_counts.values() if v > 0))
            payload.setdefault("research_kappa_books_pending_1", sum(1 for v in obs_counts.values() if v == 1))
            payload.setdefault("research_kappa_books_pending_2", sum(1 for v in obs_counts.values() if v == 2))
            payload.setdefault("research_kappa_books_eligible", sum(1 for v in obs_counts.values() if v >= target))
            dust_registry = getattr(self, "_research_parked_dust", {})
            payload.setdefault(
                "research_kappa_incomplete_dust",
                sum(1 for bid in dust_registry if int(obs_counts.get(int(bid), 0)) < target),
            )
            payload.setdefault(
                "research_kappa_zero_obs_dust",
                sum(1 for bid in dust_registry if int(obs_counts.get(int(bid), 0)) == 0),
            )
            payload.setdefault(
                "research_kappa_one_away_dust",
                sum(1 for bid in dust_registry if int(obs_counts.get(int(bid), 0)) == target - 1),
            )
            payload.setdefault(
                "research_parked_dust_abs_base",
                sum(
                    abs(float(info.get("net_base", 0.0)))
                    for info in getattr(self, "_research_parked_dust", {}).values()
                ),
            )
            try:
                current_tick = int(getattr(self, "_tick", 0) or 0)
                dust_registry = getattr(self, "_research_parked_dust", {})
                payload.setdefault(
                    "research_oldest_dust_ticks",
                    max(
                        (
                            max(0, current_tick - int(info.get("first_tick", current_tick)))
                            for info in dust_registry.values()
                        ),
                        default=0,
                    ),
                )
                payload.setdefault(
                    "research_open_positions",
                    sum(
                        1 for bid in getattr(self, "_open_positions", {})
                        if abs(float(self._position_tracker_snapshot(bid).net_qty))
                        >= self._execution_flat_epsilon()
                    ),
                )
                payload.setdefault(
                    "research_actionable_open_positions",
                    sum(
                        1 for bid in getattr(self, "_open_positions", {})
                        if bid not in dust_registry
                        and abs(float(self._position_tracker_snapshot(bid).net_qty))
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
        if typ in {"ERROR", "RUN_SUMMARY", "RESEARCH_CONFIG", "DEBUG_CONFIG", "POSITION", "POSITION_GUARD", "ACTIONABLE_FILL"}:
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
                    f"green_sparse={int(bool(r.get('green_sparse_active')))} "
                    f"dust_safe={int(bool(r.get('dust_safe_close')))} "
                    f"dust_park={int(bool(r.get('dust_park_enabled')))} "
                    f"dust_hb={r.get('dust_heartbeat_ticks')} "
                    f"dust_compact={int(bool(r.get('dust_compact_enabled')))} "
                    f"dust_compact_frac={self._fmt(r.get('dust_compact_min_fraction'))} "
                    f"kappa_complete={int(bool(r.get('kappa_completion_enabled')))} "
                    f"kappa_target={r.get('kappa_completion_target')} "
                    f"kappa_bonus={self._fmt(r.get('kappa_completion_rank_bonus'))} "
                    f"kappa_fill_mult={self._fmt(r.get('kappa_completion_fill_mult'))} "
                    f"kappa_attempt_cap={r.get('kappa_completion_attempt_cap')} "
                    f"kappa_success_cap={r.get('kappa_completion_success_cap')} "
                    f"normal_attempt_cap={r.get('normal_attempt_cap')} "
                    f"actionable_fill={int(bool(r.get('actionable_fill_enabled')))} "
                    f"actionable_min_samples={r.get('actionable_fill_min_samples')} "
                    f"actionable_rank_w={self._fmt(r.get('actionable_fill_rank_weight'))} "
                    f"dust_rank_penalty={self._fmt(r.get('dust_risk_rank_penalty'))} "
                    f"dust_target={self._fmt(r.get('dust_risk_target'))} "
                    f"one_away_bonus={self._fmt(r.get('kappa_one_away_bonus'))} "
                    f"partial_hold={int(bool(r.get('partial_fill_hold_enabled')))} "
                    f"hold_min_dust={self._fmt(r.get('partial_fill_hold_min_dust_prob'))} "
                    f"force_mm_po={int(bool(r.get('force_mm_post_only')))} "
                    f"dust_cooldown={r.get('dust_compact_cooldown_ticks')} "
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
                f"active_sparse_tier={self._short(r.get('active_sparse_tier'))} "
                f"dust_quarantine={self._fmt(r.get('dust_quarantine'))} "
                f"dust_compact={self._fmt(r.get('dust_compact'))} "
                f"lane={self._fmt(r.get('scheduler_lane'))} "
                f"normal_attempts={self._fmt(r.get('normal_attempts_used'))}/{self._fmt(r.get('normal_attempt_cap'))} "
                f"completion_attempts={self._fmt(r.get('completion_attempts_used'))}/{self._fmt(r.get('completion_attempt_cap'))} "
                f"kappa_complete={self._fmt(r.get('kappa_completion_candidate'))} "
                f"kappa_samples={self._fmt(r.get('kappa_completion_samples'))} "
                f"kappa_fill_relaxed={self._fmt(r.get('kappa_completion_fill_relaxed'))} "
                f"actionable_p={self._fmt(r.get('actionable_fill_p'))} "
                f"dust_p={self._fmt(r.get('dust_fill_p'))} "
                f"actionable_n={self._fmt(r.get('actionable_fill_samples'))} "
                f"actionable_src={self._short(r.get('actionable_fill_source'))} "
                f"partial_hold={self._fmt(r.get('partial_fill_hold'))} "
                f"hold_expiry={self._fmt(r.get('partial_fill_hold_expiry_ns'))} "
                f"force_mm_po={self._fmt(r.get('force_mm_post_only'))} "
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
        if typ == "ACTIONABLE_FILL":
            return (
                f"[S1R_ACTIONABLE] tick={r.get('tick')} book={r.get('book_id')} "
                f"side={self._short(r.get('side'))} bucket={r.get('bucket')} "
                f"class={self._short(r.get('classification'))} "
                f"fill_qty={self._fmt(r.get('fill_qty'))} fill_frac={self._fmt(r.get('fill_fraction'))} "
                f"net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))} "
                f"p_actionable={self._fmt(r.get('p_actionable'))} p_dust={self._fmt(r.get('p_dust'))} "
                f"samples={r.get('samples')} confident={r.get('confident')}"
            )
        if typ == "POSITION":
            return (
                f"[S1R_POSITION] tick={r.get('tick')} book={r.get('book_id')} "
                f"transition={self._short(r.get('transition'))} "
                f"net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))} "
                f"realized_delta={self._fmt(r.get('realized_pnl_delta'))} "
                f"round_trip={self._fmt(r.get('round_trip'))} total_round_trips={r.get('round_trip_total')} "
                f"book_samples={r.get('round_trip_book_samples')} "
                f"realized_obs={r.get('realized_book_observations')} "
                f"flat_eps={self._fmt(r.get('execution_flat_epsilon'))} "
                f"reason={self._short(r.get('reason'))}"
            )
        if typ == "POSITION_GUARD":
            return (
                f"[S1R_DUST] tick={r.get('tick')} book={r.get('book_id')} "
                f"net_base={self._fmt(r.get('net_base'))} min_order={self._fmt(r.get('min_order_size'))} "
                f"age_ticks={self._fmt(r.get('age_ticks'))} parked={self._fmt(r.get('parked'))} "
                f"stale={self._fmt(r.get('stale'))} reason={self._short(r.get('reason'))}"
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
                    f"actionable_open={r.get('research_actionable_open_positions', 0)} "
                    f"parked_dust={r.get('research_parked_dust_positions', 0)} "
                    f"parked_dust_base={self._fmt(r.get('research_parked_dust_abs_base'))} "
                    f"dust_entries={r.get('research_dust_entries', 0)} "
                    f"dust_releases={r.get('research_dust_releases', 0)} "
                    f"dust_compact_orders={r.get('research_dust_compact_orders', 0)} "
                    f"dust_compact_fills={r.get('research_dust_compact_fills', 0)} "
                    f"dust_cooldown_skips={r.get('research_dust_compact_cooldown_skips', 0)} "
                    f"actionable_fill_ratio={self._fmt(r.get('research_actionable_fill_ratio'))} "
                    f"dust_fill_ratio={self._fmt(r.get('research_dust_maker_fill_ratio'))} "
                    f"partial_hold={r.get('research_partial_fill_hold_quoted', 0)}/{r.get('research_partial_fill_hold_candidates', 0)} "
                    f"oldest_dust_ticks={r.get('research_oldest_dust_ticks', 0)} "
                    f"kappa_eligible={r.get('research_kappa_books_eligible', 0)} "
                    f"kappa_incomplete_dust={r.get('research_kappa_incomplete_dust', 0)} "
                    f"kappa_zero_dust={r.get('research_kappa_zero_obs_dust', 0)} "
                    f"kappa_one_away_dust={r.get('research_kappa_one_away_dust', 0)} "
                    f"kappa_pending1={r.get('research_kappa_books_pending_1', 0)} "
                    f"kappa_pending2={r.get('research_kappa_books_pending_2', 0)} "
                    f"normal_lane={r.get('research_normal_quote_attempts', 0)}/{r.get('research_normal_attempt_cap', 0)} "
                    f"completion_lane={r.get('research_completion_quote_attempts', 0)}/{r.get('research_completion_attempt_cap', 0)} "
                    f"completion_success={r.get('research_completion_quote_successes', 0)}/{r.get('research_completion_success_cap', 0)} "
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
