# SPDX-License-Identifier: MIT
"""
AdaptiveAgent — conservative self-calibrating Strategy built on BaseStrategy.

Architecture
------------
FinanceSimulationAgent
        |
    BaseStrategy
        |
   AdaptiveAgent

The BaseStrategy safety/execution invariants remain authoritative. AdaptiveAgent
only learns bounded execution/calibration overlays:

1. maker fill-probability calibration;
2. per-book execution-quality ranking;
3. bounded quote-width / size-multiplier adaptation;
4. restart-safe Kappa-completion observation state;
5. environment-isolated persistent learning;
6. OBSERVE -> BOOTSTRAP -> NORMAL phases plus composite DRIFT fallback;
7. dust-compaction cooldown/ranking learned without weakening the dust theorem;
8. stronger one-away Kappa-completion priority;
9. explicit Adaptive telemetry for auditability.

It intentionally does NOT override inventory correctness, dust safety, order
precision/minimum-order handling, hard inventory limits, FIFO accounting,
aggressive-close gates, CROSS lifecycle handling, or V4.1 scheduler budgets.
"""

from __future__ import annotations

import atexit
import json
import math
import os
import re
import sys
import threading
from dataclasses import replace
from pathlib import Path
from typing import Any, Literal

import bittensor as bt

_AGENT_DIR = os.path.dirname(os.path.abspath(__file__))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from taos.common.agents import launch

from BaseStrategy import (
    BaseStrategy,
    BookMemory,
    BookProfile,
    DirectionForecast,
    FillProbabilityEstimate,
    InventorySnapshot,
    RegimeParamSet,
)


class AdaptiveAgent(BaseStrategy):
    """Bounded adaptive execution layer over the verified standalone BaseStrategy."""

    ADAPTIVE_VERSION = "adaptive_v2_strict"
    ADAPTIVE_STATE_SCHEMA = 2

    # ------------------------------------------------------------------
    # Lifecycle / configuration
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        super().initialize()
        cfg = self.config

        self.adaptive_enabled = self._adaptive_bool(
            getattr(cfg, "adaptive_enabled", True)
        )

        # Learning phases.  These are request-count based so they are stable
        # across simulation-speed changes.
        self.adaptive_observe_requests = max(
            100, int(float(getattr(cfg, "adaptive_observe_requests", 1000)))
        )
        self.adaptive_normal_after_requests = max(
            self.adaptive_observe_requests + 100,
            int(float(getattr(cfg, "adaptive_normal_after_requests", 3000))),
        )

        # Fill calibration.  Learned probabilities are shrunk toward the
        # BaseStrategy model and bounded, never trusted blindly.
        self.adaptive_fill_min_samples = max(
            3, int(float(getattr(cfg, "adaptive_fill_min_samples", 8)))
        )
        self.adaptive_fill_full_confidence_samples = max(
            self.adaptive_fill_min_samples,
            int(float(getattr(cfg, "adaptive_fill_full_confidence_samples", 40))),
        )
        self.adaptive_fill_prior_strength = max(
            1.0, float(getattr(cfg, "adaptive_fill_prior_strength", 8.0))
        )
        self.adaptive_bootstrap_fill_blend = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_bootstrap_fill_blend", 0.25)), 0.0, 0.50
        )
        self.adaptive_normal_fill_blend = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_normal_fill_blend", 0.60)), 0.0, 0.80
        )
        self.adaptive_drift_fill_blend = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_fill_blend", 0.20)), 0.0, 0.50
        )
        self.adaptive_fill_max_delta = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_fill_max_delta", 0.15)), 0.01, 0.30
        )

        # Bounded execution overlay.  AdaptiveAgent may widen quotes or reduce
        # size materially; tightening is deliberately much smaller.
        self.adaptive_max_widen = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_max_widen", 0.18)), 0.0, 0.50
        )
        self.adaptive_max_tighten = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_max_tighten", 0.06)), 0.0, 0.15
        )
        self.adaptive_max_size_cut = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_max_size_cut", 0.35)), 0.0, 0.70
        )
        self.adaptive_pnl_scale = max(
            1e-6, float(getattr(cfg, "adaptive_pnl_scale", 0.03))
        )
        self.adaptive_target_maker_fill = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_target_maker_fill", 0.20)), 0.02, 0.80
        )
        self.adaptive_rank_max_adjust = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_rank_max_adjust", 0.06)), 0.0, 0.15
        )

        # V2 drift detection is composite.  V1 used a 12 percentage-point
        # absolute fill-rate shift, which was too insensitive in the observed
        # SN79 run.  V2 combines relative fill degradation, spread-regime
        # expansion, and maker realized-PnL deterioration.  It may enter DRIFT
        # during BOOTSTRAP (after OBSERVE) rather than waiting for NORMAL.
        self.adaptive_drift_window_requests = max(
            50, int(float(getattr(cfg, "adaptive_drift_window_requests", 250)))
        )
        self.adaptive_drift_start_requests = max(
            self.adaptive_observe_requests,
            int(float(getattr(cfg, "adaptive_drift_start_requests", self.adaptive_observe_requests))),
        )
        self.adaptive_drift_min_quotes = max(
            10, int(float(getattr(cfg, "adaptive_drift_min_quotes", 30)))
        )
        self.adaptive_drift_fill_abs_min = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_fill_abs_min", 0.005)), 0.001, 0.05
        )
        self.adaptive_drift_fill_relative = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_fill_relative", 0.40)), 0.10, 1.50
        )
        self.adaptive_drift_spread_ratio = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_spread_ratio", 1.30)), 1.05, 2.50
        )
        self.adaptive_drift_spread_delta_bps = max(
            0.5, float(getattr(cfg, "adaptive_drift_spread_delta_bps", 4.0))
        )
        self.adaptive_drift_pnl_hard_floor = min(
            0.0, float(getattr(cfg, "adaptive_drift_pnl_hard_floor", -0.02))
        )
        self.adaptive_drift_pnl_ratio = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_pnl_ratio", 0.35)), 0.0, 0.90
        )
        self.adaptive_drift_pnl_baseline_min = max(
            0.0, float(getattr(cfg, "adaptive_drift_pnl_baseline_min", 0.03))
        )
        self.adaptive_drift_min_maker_realized = max(
            2, int(float(getattr(cfg, "adaptive_drift_min_maker_realized", 6)))
        )
        self.adaptive_drift_baseline_alpha = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_baseline_alpha", 0.15)), 0.02, 0.50
        )
        self.adaptive_drift_hold_requests = max(
            self.adaptive_drift_window_requests,
            int(float(getattr(cfg, "adaptive_drift_hold_requests", 500))),
        )

        # Restart-safe, environment-isolated state.  The launcher supplies a
        # key such as testnet_366 or net_79.  An unscoped key disables disk
        # persistence by default to prevent accidental cross-network learning.
        env_key = os.getenv("ADAPTIVE_ENVIRONMENT_KEY", "").strip()
        cfg_key = str(getattr(cfg, "adaptive_environment_key", "") or "").strip()
        self.adaptive_environment_key = env_key or cfg_key or "unscoped"

        requested_persistence = self._adaptive_bool(
            getattr(cfg, "adaptive_persistence_enabled", True)
        )
        self.adaptive_persistence_enabled = (
            requested_persistence and self.adaptive_environment_key != "unscoped"
        )
        self.adaptive_save_every_n = max(
            25, int(float(getattr(cfg, "adaptive_save_every_n", 250)))
        )
        env_state_dir = os.getenv("ADAPTIVE_STATE_DIR", "").strip()
        cfg_state_dir = str(getattr(cfg, "adaptive_state_dir", "") or "").strip()
        self.adaptive_state_dir = (
            env_state_dir
            or cfg_state_dir
            or os.path.join(self.output_dir, "adaptive_state")
        )

        # Phase controls.  Observe is intentionally low-risk: at most two MM
        # books and minimum executable size. Bootstrap allows three books and
        # partial Kappa-completion pressure. Normal restores the BaseStrategy
        # scheduler limits. Risk exits are never weakened by these controls.
        self.adaptive_observe_max_mm_books = max(
            1, int(float(getattr(cfg, "adaptive_observe_max_mm_books", 2)))
        )
        self.adaptive_bootstrap_max_mm_books = max(
            self.adaptive_observe_max_mm_books,
            int(float(getattr(cfg, "adaptive_bootstrap_max_mm_books", 3))),
        )
        self.adaptive_bootstrap_size_scale = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_bootstrap_size_scale", 0.75)), 0.30, 1.0
        )
        self.adaptive_drift_size_scale = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_size_scale", 0.65)), 0.30, 1.0
        )
        self.adaptive_bootstrap_kappa_rank_scale = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_bootstrap_kappa_rank_scale", 0.50)),
            0.0,
            1.0,
        )

        # V2 Kappa completion: books with exactly target-1 observations receive
        # a small additional rank bonus.  The BaseStrategy completion gates,
        # attempt caps, fill floor and PnL floor remain authoritative.
        self.adaptive_kappa_one_away_bonus = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_kappa_one_away_bonus", 0.08)), 0.0, 0.15
        )

        # V2 dust execution learning.  This NEVER changes the proof condition
        # for a safe compaction.  It only changes which already-safe dust book
        # is retried and how soon after repeated failed selections.
        self.adaptive_dust_enabled = self._adaptive_bool(
            getattr(cfg, "adaptive_dust_enabled", True)
        )
        self.adaptive_dust_cooldown_ticks = max(
            10, int(float(getattr(cfg, "adaptive_dust_cooldown_ticks", 100)))
        )
        self.adaptive_dust_max_cooldown_ticks = max(
            self.adaptive_dust_cooldown_ticks,
            int(float(getattr(cfg, "adaptive_dust_max_cooldown_ticks", 600))),
        )
        self.adaptive_dust_prior_fill = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_dust_prior_fill", 0.02)), 0.001, 0.25
        )
        self.adaptive_dust_prior_strength = max(
            1.0, float(getattr(cfg, "adaptive_dust_prior_strength", 20.0))
        )

        # Explicit child-layer telemetry.  Quote telemetry is emitted only for
        # books that actually placed an MM instruction, avoiding 128-book/tick
        # JSONL amplification.
        self.adaptive_telemetry_every_n = max(
            1, int(float(getattr(cfg, "adaptive_telemetry_every_n", 25)))
        )

        # Preserve authoritative BaseStrategy scheduler values so phase controls
        # are temporary overlays only.
        self._adaptive_base_max_mm_books = int(self.max_mm_books_per_tick)
        self._adaptive_base_kappa_completion_enabled = bool(
            self.research_kappa_completion_enabled
        )
        self._adaptive_base_kappa_rank_bonus = float(
            self.research_kappa_completion_rank_bonus
        )
        self._adaptive_base_kappa_relaxed_success_cap = int(
            self.research_kappa_completion_relaxed_success_cap
        )

        # Runtime state.
        self.adaptive_total_requests = 0
        self.adaptive_session_requests = 0
        self._adaptive_last_sim_timestamp: int | None = None
        self._adaptive_last_saved_request = 0
        self._adaptive_drift_until_request = 0
        self._adaptive_drift_snapshot_request = 0
        self._adaptive_drift_snapshot_quotes = 0
        self._adaptive_drift_snapshot_fills = 0
        self._adaptive_drift_snapshot_maker_realized = 0
        self._adaptive_spread_window_sum = 0.0
        self._adaptive_spread_window_count = 0
        self._adaptive_spread_baseline_bps: float | None = None
        self._adaptive_last_drift_metrics: dict[str, Any] = {}
        self._adaptive_last_phase = "OBSERVE"

        self._adaptive_books: dict[int, dict[str, Any]] = {}
        self._adaptive_global = self._adaptive_new_stats()
        self._adaptive_state_lock = threading.Lock()
        self._adaptive_last_fill_diag: dict[int, dict[str, Any]] = {}
        self._adaptive_last_exec_diag: dict[int, dict[str, Any]] = {}

        if self.adaptive_persistence_enabled:
            self._adaptive_load_state()
            atexit.register(self._adaptive_save_state, True)

        self._adaptive_last_phase = self._adaptive_phase()
        bt.logging.info(
            "AdaptiveAgent: "
            f"enabled={self.adaptive_enabled} "
            f"version={self.ADAPTIVE_VERSION} "
            f"phase={self._adaptive_last_phase} "
            f"requests={self.adaptive_total_requests} "
            f"env={self.adaptive_environment_key} "
            f"persist={self.adaptive_persistence_enabled}"
        )

        self._adaptive_emit(
            "ADAPTIVE_CONFIG",
            force=True,
            version=self.ADAPTIVE_VERSION,
            state_schema=self.ADAPTIVE_STATE_SCHEMA,
            enabled=self.adaptive_enabled,
            phase=self._adaptive_last_phase,
            total_requests=self.adaptive_total_requests,
            environment_key=self.adaptive_environment_key,
            persistence=self.adaptive_persistence_enabled,
            observe_requests=self.adaptive_observe_requests,
            normal_after_requests=self.adaptive_normal_after_requests,
            drift_start_requests=self.adaptive_drift_start_requests,
            drift_window_requests=self.adaptive_drift_window_requests,
            drift_fill_abs_min=self.adaptive_drift_fill_abs_min,
            drift_fill_relative=self.adaptive_drift_fill_relative,
            drift_spread_ratio=self.adaptive_drift_spread_ratio,
            drift_spread_delta_bps=self.adaptive_drift_spread_delta_bps,
            drift_pnl_hard_floor=self.adaptive_drift_pnl_hard_floor,
            drift_pnl_ratio=self.adaptive_drift_pnl_ratio,
            kappa_one_away_bonus=self.adaptive_kappa_one_away_bonus,
            dust_cooldown_ticks=self.adaptive_dust_cooldown_ticks,
            dust_max_cooldown_ticks=self.adaptive_dust_max_cooldown_ticks,
        )

    @staticmethod
    def _adaptive_bool(value: Any) -> bool:
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "y"}
        return bool(value)

    @staticmethod
    def _adaptive_clamp(value: float, lo: float, hi: float) -> float:
        return max(lo, min(hi, float(value)))

    @staticmethod
    def _adaptive_sanitize_key(value: str) -> str:
        cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
        return cleaned[:96] or "unscoped"

    def _adaptive_emit(self, event_type: str, force: bool = False, **payload: Any) -> None:
        """Emit Adaptive-only JSONL through BaseStrategy's async logger."""
        if not (getattr(self, "debug_enabled", False) or getattr(self, "research_enabled", False)):
            return
        try:
            self._emit(event_type, force=force, **payload)
        except Exception:
            # Telemetry must never influence trading behavior.
            return

    def _adaptive_state_path(self) -> Path:
        key = self._adaptive_sanitize_key(self.adaptive_environment_key)
        return Path(self.adaptive_state_dir) / f"adaptive_state_{key}.json"

    # ------------------------------------------------------------------
    # State helpers / persistence
    # ------------------------------------------------------------------
    @staticmethod
    def _adaptive_new_stats() -> dict[str, Any]:
        return {
            "buy_quotes": [0, 0, 0],
            "buy_fills": [0, 0, 0],
            "sell_quotes": [0, 0, 0],
            "sell_fills": [0, 0, 0],
            "maker_fills": 0,
            "taker_fills": 0,
            "maker_realized_obs": 0,
            "taker_realized_obs": 0,
            "session_realized_obs": 0,
            "realized_pnl_ewma": 0.0,
            "maker_realized_pnl_ewma": 0.0,
            "maker_pnl_short_ewma": 0.0,
            "maker_pnl_long_ewma": 0.0,
            "taker_realized_pnl_ewma": 0.0,
            "taker_exit_age_ewma": 0.0,
            "dust_selections": 0,
            "dust_attempts": 0,
            "dust_fills": 0,
            "dust_fail_streak": 0,
            "dust_last_selection_tick": -1,
            "dust_last_attempt_tick": -1,
            "dust_last_fill_tick": -1,
            "dust_last_accounted_submit_tick": -1,
            "dust_last_success_submit_tick": -1,
        }

    def _adaptive_book(self, book_id: int) -> dict[str, Any]:
        bid = int(book_id)
        stats = self._adaptive_books.get(bid)
        if stats is None:
            stats = self._adaptive_new_stats()
            self._adaptive_books[bid] = stats
        return stats

    @staticmethod
    def _adaptive_validate_counter_vector(value: Any) -> list[int]:
        if not isinstance(value, list) or len(value) != 3:
            return [0, 0, 0]
        out = []
        for item in value:
            try:
                out.append(max(0, int(item)))
            except (TypeError, ValueError):
                out.append(0)
        return out

    def _adaptive_normalize_stats(self, raw: Any) -> dict[str, Any]:
        stats = self._adaptive_new_stats()
        if not isinstance(raw, dict):
            return stats
        for key in ("buy_quotes", "buy_fills", "sell_quotes", "sell_fills"):
            stats[key] = self._adaptive_validate_counter_vector(raw.get(key))
        for key in (
            "maker_fills",
            "taker_fills",
            "maker_realized_obs",
            "taker_realized_obs",
            "session_realized_obs",
            "dust_selections",
            "dust_attempts",
            "dust_fills",
            "dust_fail_streak",
        ):
            try:
                stats[key] = max(0, int(raw.get(key, 0)))
            except (TypeError, ValueError):
                stats[key] = 0
        for key in (
            "realized_pnl_ewma",
            "maker_realized_pnl_ewma",
            "maker_pnl_short_ewma",
            "maker_pnl_long_ewma",
            "taker_realized_pnl_ewma",
            "taker_exit_age_ewma",
        ):
            try:
                value = float(raw.get(key, 0.0))
                stats[key] = value if math.isfinite(value) else 0.0
            except (TypeError, ValueError):
                stats[key] = 0.0
        for key in (
            "dust_last_selection_tick",
            "dust_last_attempt_tick",
            "dust_last_fill_tick",
            "dust_last_accounted_submit_tick",
            "dust_last_success_submit_tick",
        ):
            try:
                stats[key] = int(raw.get(key, -1))
            except (TypeError, ValueError):
                stats[key] = -1
        return stats

    def _adaptive_load_state(self) -> None:
        path = self._adaptive_state_path()
        try:
            if not path.is_file():
                return
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return
            schema = int(payload.get("schema", -1))
            if schema not in (1, self.ADAPTIVE_STATE_SCHEMA):
                bt.logging.warning(f"AdaptiveAgent: ignoring incompatible state {path}")
                return
            if str(payload.get("environment_key", "")) != self.adaptive_environment_key:
                bt.logging.warning(f"AdaptiveAgent: ignoring environment-mismatched state {path}")
                return

            self.adaptive_total_requests = max(
                0, int(payload.get("total_requests", 0))
            )
            last_ts = payload.get("last_sim_timestamp")
            self._adaptive_last_sim_timestamp = (
                int(last_ts) if last_ts is not None else None
            )
            self._adaptive_drift_until_request = max(
                0, int(payload.get("drift_until_request", 0))
            )
            try:
                spread_baseline = payload.get("spread_baseline_bps")
                self._adaptive_spread_baseline_bps = (
                    float(spread_baseline) if spread_baseline is not None else None
                )
            except (TypeError, ValueError):
                self._adaptive_spread_baseline_bps = None

            self._adaptive_global = self._adaptive_normalize_stats(
                payload.get("global")
            )
            books = payload.get("books", {})
            if isinstance(books, dict):
                for key, raw in books.items():
                    try:
                        book_id = int(key)
                    except (TypeError, ValueError):
                        continue
                    self._adaptive_books[book_id] = self._adaptive_normalize_stats(raw)

            # Drift window snapshots intentionally restart with the process.
            q, f = self._adaptive_global_fill_totals()
            self._adaptive_drift_snapshot_quotes = q
            self._adaptive_drift_snapshot_fills = f
            self._adaptive_drift_snapshot_maker_realized = int(
                self._adaptive_global.get("maker_realized_obs", 0)
            )
            self._adaptive_drift_snapshot_request = self.adaptive_total_requests
            self._adaptive_last_saved_request = self.adaptive_total_requests
        except Exception as exc:
            bt.logging.warning(f"AdaptiveAgent: state load failed: {exc}")

    def _adaptive_state_payload(self) -> dict[str, Any]:
        return {
            "schema": self.ADAPTIVE_STATE_SCHEMA,
            "version": self.ADAPTIVE_VERSION,
            "environment_key": self.adaptive_environment_key,
            "total_requests": int(self.adaptive_total_requests),
            "last_sim_timestamp": self._adaptive_last_sim_timestamp,
            "drift_until_request": int(self._adaptive_drift_until_request),
            "spread_baseline_bps": self._adaptive_spread_baseline_bps,
            "global": self._adaptive_global,
            "books": {str(k): v for k, v in sorted(self._adaptive_books.items())},
        }

    def _adaptive_save_state(self, force: bool = False) -> None:
        if not self.adaptive_persistence_enabled:
            return
        if not force and (
            self.adaptive_total_requests - self._adaptive_last_saved_request
            < self.adaptive_save_every_n
        ):
            return

        path = self._adaptive_state_path()
        try:
            with self._adaptive_state_lock:
                path.parent.mkdir(parents=True, exist_ok=True)
                tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
                payload = self._adaptive_state_payload()
                tmp.write_text(
                    json.dumps(payload, separators=(",", ":"), sort_keys=True),
                    encoding="utf-8",
                )
                os.replace(tmp, path)
                self._adaptive_last_saved_request = self.adaptive_total_requests
        except Exception as exc:
            bt.logging.warning(f"AdaptiveAgent: state save failed: {exc}")

    def _adaptive_reset_session_scoped_state(self) -> None:
        """Reset scoring-session counters while preserving environment learning."""
        for stats in self._adaptive_books.values():
            stats["session_realized_obs"] = 0
        self._adaptive_global["session_realized_obs"] = 0

        # BaseStrategy completion counts are session-scoped too.
        if hasattr(self, "_research_realized_observations_by_book"):
            self._research_realized_observations_by_book.clear()
        if hasattr(self, "_research_round_trip_samples_by_book"):
            self._research_round_trip_samples_by_book.clear()

        self.adaptive_session_requests = 0

        # A new simulation is a fresh market-regime session. Preserve learned
        # per-book execution calibration, but do not carry a stale DRIFT window.
        self._adaptive_drift_until_request = 0
        q, f = self._adaptive_global_fill_totals()
        self._adaptive_drift_snapshot_quotes = q
        self._adaptive_drift_snapshot_fills = f
        self._adaptive_drift_snapshot_maker_realized = int(
            self._adaptive_global.get("maker_realized_obs", 0)
        )
        self._adaptive_drift_snapshot_request = self.adaptive_total_requests
        self._adaptive_spread_window_sum = 0.0
        self._adaptive_spread_window_count = 0
        self._adaptive_spread_baseline_bps = None
        self._adaptive_last_drift_metrics = {}
        # Neutralize short-vs-long drift state while retaining long-run quality.
        long_pnl = float(self._adaptive_global.get("maker_pnl_long_ewma", 0.0) or 0.0)
        self._adaptive_global["maker_pnl_short_ewma"] = long_pnl

    # ------------------------------------------------------------------
    # Phase / drift
    # ------------------------------------------------------------------
    def _adaptive_phase(self) -> str:
        if not self.adaptive_enabled:
            return "DISABLED"
        if self.adaptive_total_requests < self.adaptive_observe_requests:
            return "OBSERVE"
        if self.adaptive_total_requests < self._adaptive_drift_until_request:
            return "DRIFT"
        if self.adaptive_total_requests < self.adaptive_normal_after_requests:
            return "BOOTSTRAP"
        return "NORMAL"

    def _adaptive_global_fill_totals(self) -> tuple[int, int]:
        g = self._adaptive_global
        quotes = sum(g["buy_quotes"]) + sum(g["sell_quotes"])
        fills = sum(g["buy_fills"]) + sum(g["sell_fills"])
        return int(quotes), int(fills)

    def _adaptive_observe_market_state(self) -> None:
        """Accumulate a no-extra-book-scan market snapshot from BaseStrategy profiles."""
        profiles = getattr(self, "_last_profiles", None) or []
        spreads: list[float] = []
        for profile in profiles:
            try:
                value = float(getattr(profile, "spread_bps", 0.0) or 0.0)
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and value >= 0.0:
                spreads.append(value)
        if not spreads:
            return
        spreads.sort()
        n = len(spreads)
        if n % 2:
            median = spreads[n // 2]
        else:
            median = 0.5 * (spreads[n // 2 - 1] + spreads[n // 2])
        self._adaptive_spread_window_sum += float(median)
        self._adaptive_spread_window_count += 1

    def _adaptive_maybe_detect_drift(self) -> None:
        if self.adaptive_total_requests < self.adaptive_drift_start_requests:
            return
        if (
            self.adaptive_total_requests - self._adaptive_drift_snapshot_request
            < self.adaptive_drift_window_requests
        ):
            return

        total_q, total_f = self._adaptive_global_fill_totals()
        dq = max(0, total_q - self._adaptive_drift_snapshot_quotes)
        df = max(0, total_f - self._adaptive_drift_snapshot_fills)
        window_fill = self._adaptive_clamp(df / max(dq, 1), 0.0, 1.0)
        long_fill = self._adaptive_clamp(total_f / max(total_q, 1), 0.0, 1.0)
        fill_abs = abs(window_fill - long_fill)
        fill_rel = fill_abs / max(long_fill, self.adaptive_drift_fill_abs_min)

        maker_obs = int(self._adaptive_global.get("maker_realized_obs", 0))
        maker_obs_window = max(
            0, maker_obs - self._adaptive_drift_snapshot_maker_realized
        )
        maker_short = float(
            self._adaptive_global.get("maker_pnl_short_ewma", 0.0) or 0.0
        )
        maker_long = float(
            self._adaptive_global.get("maker_pnl_long_ewma", 0.0) or 0.0
        )

        spread_window = None
        if self._adaptive_spread_window_count > 0:
            spread_window = (
                self._adaptive_spread_window_sum
                / self._adaptive_spread_window_count
            )
        spread_baseline = self._adaptive_spread_baseline_bps
        spread_ratio = 1.0
        spread_delta = 0.0
        if (
            spread_window is not None
            and spread_baseline is not None
            and spread_baseline > 1e-9
        ):
            spread_ratio = spread_window / spread_baseline
            spread_delta = spread_window - spread_baseline

        fill_signal = (
            dq >= self.adaptive_drift_min_quotes
            and total_q >= 2 * self.adaptive_drift_min_quotes
            and fill_abs >= self.adaptive_drift_fill_abs_min
            and fill_rel >= self.adaptive_drift_fill_relative
        )
        spread_signal = (
            spread_window is not None
            and spread_baseline is not None
            and spread_ratio >= self.adaptive_drift_spread_ratio
            and spread_delta >= self.adaptive_drift_spread_delta_bps
        )
        pnl_hard_signal = (
            maker_obs_window >= self.adaptive_drift_min_maker_realized
            and maker_short <= self.adaptive_drift_pnl_hard_floor
        )
        pnl_relative_signal = (
            maker_obs_window >= self.adaptive_drift_min_maker_realized
            and maker_long >= self.adaptive_drift_pnl_baseline_min
            and maker_short <= maker_long * self.adaptive_drift_pnl_ratio
        )

        # One hard economic signal or one material spread-regime shift is
        # sufficient.  Fill-only drift remains conservative: it must combine
        # with PnL weakness unless the absolute/relative move is extreme.
        fill_extreme = fill_signal and fill_rel >= 0.80
        trigger = bool(
            pnl_hard_signal
            or spread_signal
            or fill_extreme
            or (fill_signal and pnl_relative_signal)
        )

        old_until = self._adaptive_drift_until_request
        if trigger:
            self._adaptive_drift_until_request = max(
                self._adaptive_drift_until_request,
                self.adaptive_total_requests + self.adaptive_drift_hold_requests,
            )

        metrics = {
            "tick": int(getattr(self, "_tick", 0) or 0),
            "requests": self.adaptive_total_requests,
            "phase_before": self._adaptive_last_phase,
            "trigger": trigger,
            "fill_signal": fill_signal,
            "fill_extreme": fill_extreme,
            "window_quotes": dq,
            "window_fills": df,
            "window_fill_rate": window_fill,
            "long_fill_rate": long_fill,
            "fill_abs_delta": fill_abs,
            "fill_relative_delta": fill_rel,
            "spread_signal": spread_signal,
            "spread_window_bps": spread_window,
            "spread_baseline_bps": spread_baseline,
            "spread_ratio": spread_ratio,
            "spread_delta_bps": spread_delta,
            "pnl_hard_signal": pnl_hard_signal,
            "pnl_relative_signal": pnl_relative_signal,
            "maker_realized_window": maker_obs_window,
            "maker_pnl_short": maker_short,
            "maker_pnl_long": maker_long,
            "drift_until_request": self._adaptive_drift_until_request,
            "drift_extended": self._adaptive_drift_until_request > old_until,
        }
        self._adaptive_last_drift_metrics = metrics
        self._adaptive_emit("ADAPTIVE_DRIFT", force=trigger, **metrics)

        # Baseline follows the environment slowly. During a shift this lets the
        # strategy eventually leave DRIFT after the new regime stabilizes.
        if spread_window is not None:
            if self._adaptive_spread_baseline_bps is None:
                self._adaptive_spread_baseline_bps = float(spread_window)
            else:
                a = self.adaptive_drift_baseline_alpha
                self._adaptive_spread_baseline_bps = (
                    (1.0 - a) * self._adaptive_spread_baseline_bps
                    + a * float(spread_window)
                )

        self._adaptive_drift_snapshot_request = self.adaptive_total_requests
        self._adaptive_drift_snapshot_quotes = total_q
        self._adaptive_drift_snapshot_fills = total_f
        self._adaptive_drift_snapshot_maker_realized = maker_obs
        self._adaptive_spread_window_sum = 0.0
        self._adaptive_spread_window_count = 0

    def _adaptive_log_phase_if_changed(self) -> None:
        phase = self._adaptive_phase()
        if phase == self._adaptive_last_phase:
            return
        old = self._adaptive_last_phase
        self._adaptive_last_phase = phase
        bt.logging.info(
            f"AdaptiveAgent: phase {old} -> {phase} "
            f"requests={self.adaptive_total_requests}"
        )
        self._adaptive_emit(
            "ADAPTIVE_PHASE",
            force=True,
            tick=int(getattr(self, "_tick", 0) or 0),
            old_phase=old,
            new_phase=phase,
            requests=self.adaptive_total_requests,
            drift_until_request=self._adaptive_drift_until_request,
        )

    # ------------------------------------------------------------------
    # Main request hook
    # ------------------------------------------------------------------
    def _adaptive_apply_phase_controls(self) -> tuple[int, bool, float, int]:
        """Apply temporary scheduler controls and return values to restore."""
        old = (
            int(self.max_mm_books_per_tick),
            bool(self.research_kappa_completion_enabled),
            float(self.research_kappa_completion_rank_bonus),
            int(self.research_kappa_completion_relaxed_success_cap),
        )
        phase = self._adaptive_phase()

        if phase == "OBSERVE":
            self.max_mm_books_per_tick = min(
                self._adaptive_base_max_mm_books,
                self.adaptive_observe_max_mm_books,
            )
            self.research_kappa_completion_enabled = False
            self.research_kappa_completion_rank_bonus = 0.0
            self.research_kappa_completion_relaxed_success_cap = 0
        elif phase == "BOOTSTRAP":
            self.max_mm_books_per_tick = min(
                self._adaptive_base_max_mm_books,
                self.adaptive_bootstrap_max_mm_books,
            )
            self.research_kappa_completion_enabled = (
                self._adaptive_base_kappa_completion_enabled
            )
            self.research_kappa_completion_rank_bonus = (
                self._adaptive_base_kappa_rank_bonus
                * self.adaptive_bootstrap_kappa_rank_scale
            )
            self.research_kappa_completion_relaxed_success_cap = min(
                self._adaptive_base_kappa_relaxed_success_cap, 1
            )
        elif phase == "DRIFT":
            self.max_mm_books_per_tick = min(
                self._adaptive_base_max_mm_books,
                self.adaptive_observe_max_mm_books,
            )
            self.research_kappa_completion_enabled = (
                self._adaptive_base_kappa_completion_enabled
            )
            self.research_kappa_completion_rank_bonus = min(
                self._adaptive_base_kappa_rank_bonus, 0.15
            )
            self.research_kappa_completion_relaxed_success_cap = 0
        else:
            self.max_mm_books_per_tick = self._adaptive_base_max_mm_books
            self.research_kappa_completion_enabled = (
                self._adaptive_base_kappa_completion_enabled
            )
            self.research_kappa_completion_rank_bonus = (
                self._adaptive_base_kappa_rank_bonus
            )
            self.research_kappa_completion_relaxed_success_cap = (
                self._adaptive_base_kappa_relaxed_success_cap
            )
        return old

    def _adaptive_restore_phase_controls(
        self,
        old: tuple[int, bool, float, int],
    ) -> None:
        (
            self.max_mm_books_per_tick,
            self.research_kappa_completion_enabled,
            self.research_kappa_completion_rank_bonus,
            self.research_kappa_completion_relaxed_success_cap,
        ) = old

    def handle(self, state):
        if self.adaptive_enabled:
            sim_ts = getattr(state, "timestamp", None)
            if sim_ts is not None:
                try:
                    sim_ts = int(sim_ts)
                except (TypeError, ValueError):
                    sim_ts = None

            # Simulation-time regression means a fresh scoring episode.
            if (
                sim_ts is not None
                and self._adaptive_last_sim_timestamp is not None
                and sim_ts < self._adaptive_last_sim_timestamp
            ):
                self._adaptive_reset_session_scoped_state()

            if sim_ts is not None:
                self._adaptive_last_sim_timestamp = sim_ts

            self.adaptive_total_requests += 1
            self.adaptive_session_requests += 1
            self._adaptive_log_phase_if_changed()

        phase_old = self._adaptive_apply_phase_controls()
        try:
            response = super().handle(state)
        finally:
            self._adaptive_restore_phase_controls(phase_old)

        if self.adaptive_enabled:
            # Reuse the profiles that BaseStrategy already computed; this adds
            # no second 128-book prediction/profile pass.
            self._adaptive_observe_market_state()
            self._adaptive_maybe_detect_drift()
            self._adaptive_log_phase_if_changed()

            if (
                self.adaptive_total_requests == 1
                or self.adaptive_total_requests % self.adaptive_telemetry_every_n == 0
            ):
                total_q, total_f = self._adaptive_global_fill_totals()
                obs = {
                    int(k): int(v.get("session_realized_obs", 0))
                    for k, v in self._adaptive_books.items()
                }
                target = int(getattr(self, "research_kappa_completion_target", 3))
                self._adaptive_emit(
                    "ADAPTIVE_SUMMARY",
                    tick=int(getattr(self, "_tick", 0) or 0),
                    timestamp=getattr(state, "timestamp", None),
                    phase=self._adaptive_phase(),
                    requests=self.adaptive_total_requests,
                    session_requests=self.adaptive_session_requests,
                    global_quotes=total_q,
                    global_fills=total_f,
                    global_fill_rate=total_f / max(total_q, 1),
                    maker_realized_obs=int(self._adaptive_global.get("maker_realized_obs", 0)),
                    maker_pnl_short=float(self._adaptive_global.get("maker_pnl_short_ewma", 0.0) or 0.0),
                    maker_pnl_long=float(self._adaptive_global.get("maker_pnl_long_ewma", 0.0) or 0.0),
                    kappa_pending_1=sum(1 for v in obs.values() if v == 1),
                    kappa_pending_2=sum(1 for v in obs.values() if v == 2),
                    kappa_eligible=sum(1 for v in obs.values() if v >= target),
                    parked_dust=len(getattr(self, "_research_parked_dust", {})),
                    drift=self._adaptive_last_drift_metrics,
                )
            self._adaptive_save_state(False)
        return response

    # ------------------------------------------------------------------
    # Restart-safe Kappa completion state
    # ------------------------------------------------------------------
    def _completion_observation_count(self, book_id: int) -> int:
        local = int(super()._completion_observation_count(book_id))
        persisted = int(self._adaptive_book(book_id).get("session_realized_obs", 0))
        return max(local, persisted)

    # ------------------------------------------------------------------
    # Maker fill-learning hooks
    # ------------------------------------------------------------------
    def _record_fill_quote(
        self,
        mem: BookMemory,
        side: Literal["buy", "sell"],
        dist_from_touch: float,
    ) -> None:
        super()._record_fill_quote(mem, side, dist_from_touch)
        if not self.adaptive_enabled:
            return

        book_id = getattr(mem, "_research_book_id", None)
        if book_id is None:
            return

        bucket = int(self._spread_dist_bucket(dist_from_touch))
        bucket = max(0, min(2, bucket))
        key = "buy_quotes" if side == "buy" else "sell_quotes"

        b = self._adaptive_book(int(book_id))
        b[key][bucket] += 1
        self._adaptive_global[key][bucket] += 1

    def _record_fill_hit(
        self,
        mem: BookMemory,
        side: Literal["buy", "sell"],
    ) -> None:
        # Bucket must be read from the same BookMemory fields used by BaseStrategy.
        bucket = (
            int(mem.last_buy_dist_bucket)
            if side == "buy"
            else int(mem.last_sell_dist_bucket)
        )
        bucket = max(0, min(2, bucket))

        super()._record_fill_hit(mem, side)
        if not self.adaptive_enabled:
            return

        book_id = getattr(mem, "_research_book_id", None)
        if book_id is None:
            return

        key = "buy_fills" if side == "buy" else "sell_fills"
        b = self._adaptive_book(int(book_id))
        b[key][bucket] += 1
        self._adaptive_global[key][bucket] += 1

    def _adaptive_fill_posterior(
        self,
        *,
        book_id: int,
        side: Literal["buy", "sell"],
        bucket: int,
        prior: float,
    ) -> tuple[float, float, int]:
        """Return (learned_probability, confidence, sample_count)."""
        stats = self._adaptive_book(book_id)
        q_key = "buy_quotes" if side == "buy" else "sell_quotes"
        f_key = "buy_fills" if side == "buy" else "sell_fills"

        bq = int(stats[q_key][bucket])
        bf = int(stats[f_key][bucket])
        gq = int(self._adaptive_global[q_key][bucket])
        gf = int(self._adaptive_global[f_key][bucket])

        # Prefer book-specific evidence. If sparse, use environment evidence.
        if bq >= self.adaptive_fill_min_samples:
            quotes, fills = bq, bf
        elif gq >= self.adaptive_fill_min_samples:
            quotes, fills = gq, gf
        else:
            return prior, 0.0, max(bq, gq)

        prior = self._adaptive_clamp(prior, 0.0, 1.0)
        learned = (
            fills + self.adaptive_fill_prior_strength * prior
        ) / (
            quotes + self.adaptive_fill_prior_strength
        )
        confidence = self._adaptive_clamp(
            quotes / max(self.adaptive_fill_full_confidence_samples, 1),
            0.0,
            1.0,
        )
        return self._adaptive_clamp(learned, 0.0, 1.0), confidence, quotes

    def _adaptive_fill_phase_blend(self) -> float:
        phase = self._adaptive_phase()
        if phase in {"DISABLED", "OBSERVE"}:
            return 0.0
        if phase == "BOOTSTRAP":
            return self.adaptive_bootstrap_fill_blend
        if phase == "DRIFT":
            return self.adaptive_drift_fill_blend
        return self.adaptive_normal_fill_blend

    def estimate_fill_probability(
        self,
        book,
        mid: float,
        spread: float,
        trade_rate: float,
        buy_price: float,
        sell_price: float,
        book_id: int | None = None,
    ) -> FillProbabilityEstimate:
        base = super().estimate_fill_probability(
            book,
            mid,
            spread,
            trade_rate,
            buy_price,
            sell_price,
            book_id=book_id,
        )
        if (
            not self.adaptive_enabled
            or book_id is None
            or spread <= 0.0
            or mid <= 0.0
        ):
            return base

        bid = int(book_id)
        phase_blend = self._adaptive_fill_phase_blend()
        buy_dist = max(0.0, (mid - buy_price) / spread)
        sell_dist = max(0.0, (sell_price - mid) / spread)
        buy_bucket = max(0, min(2, int(self._spread_dist_bucket(buy_dist))))
        sell_bucket = max(0, min(2, int(self._spread_dist_bucket(sell_dist))))

        buy_learned, buy_conf, buy_n = self._adaptive_fill_posterior(
            book_id=bid,
            side="buy",
            bucket=buy_bucket,
            prior=float(base.buy),
        )
        sell_learned, sell_conf, sell_n = self._adaptive_fill_posterior(
            book_id=bid,
            side="sell",
            bucket=sell_bucket,
            prior=float(base.sell),
        )

        buy_blend = phase_blend * buy_conf
        sell_blend = phase_blend * sell_conf
        buy = (1.0 - buy_blend) * float(base.buy) + buy_blend * buy_learned
        sell = (1.0 - sell_blend) * float(base.sell) + sell_blend * sell_learned

        buy = self._adaptive_clamp(
            buy,
            max(0.0, float(base.buy) - self.adaptive_fill_max_delta),
            min(1.0, float(base.buy) + self.adaptive_fill_max_delta),
        )
        sell = self._adaptive_clamp(
            sell,
            max(0.0, float(base.sell) - self.adaptive_fill_max_delta),
            min(1.0, float(base.sell) + self.adaptive_fill_max_delta),
        )

        diag = {
            "phase": self._adaptive_phase(),
            "base_buy": float(base.buy),
            "base_sell": float(base.sell),
            "learned_buy": buy_learned,
            "learned_sell": sell_learned,
            "final_buy": buy,
            "final_sell": sell,
            "buy_samples": buy_n,
            "sell_samples": sell_n,
            "buy_conf": buy_conf,
            "sell_conf": sell_conf,
            "buy_bucket": buy_bucket,
            "sell_bucket": sell_bucket,
            "phase_blend": phase_blend,
        }
        self._adaptive_last_fill_diag[bid] = diag

        if self.debug_enabled and hasattr(self, "_book_record"):
            record = self._book_record(bid)
            record["adaptive_phase"] = diag["phase"]
            record["adaptive_fill_base_buy"] = diag["base_buy"]
            record["adaptive_fill_base_sell"] = diag["base_sell"]
            record["adaptive_fill_learned_buy"] = diag["learned_buy"]
            record["adaptive_fill_learned_sell"] = diag["learned_sell"]
            record["adaptive_fill_buy"] = diag["final_buy"]
            record["adaptive_fill_sell"] = diag["final_sell"]
            record["adaptive_fill_buy_samples"] = buy_n
            record["adaptive_fill_sell_samples"] = sell_n
            record["adaptive_fill_buy_conf"] = buy_conf
            record["adaptive_fill_sell_conf"] = sell_conf

        return FillProbabilityEstimate(buy=buy, sell=sell)

    # ------------------------------------------------------------------
    # Phase-aware sizing
    # ------------------------------------------------------------------
    def dynamic_order_size(
        self,
        base_size: float,
        profile: BookProfile,
        regime_params: RegimeParamSet,
        inventory: InventorySnapshot,
        vol_dec: int,
        mid: float | None = None,
    ) -> float:
        size = float(
            super().dynamic_order_size(
                base_size,
                profile,
                regime_params,
                inventory,
                vol_dec,
                mid=mid,
            )
        )
        if not self.adaptive_enabled or size <= 0.0:
            return size

        phase = self._adaptive_phase()
        min_size = max(
            0.0, float(getattr(self, "_research_exchange_min_order_size", 0.0))
        )

        if phase == "OBSERVE":
            # Collect clean execution data with the smallest executable quote.
            if min_size > 0.0:
                return round(min(size, min_size), vol_dec)
            return size

        scale = 1.0
        if phase == "BOOTSTRAP":
            scale = self.adaptive_bootstrap_size_scale
        elif phase == "DRIFT":
            scale = self.adaptive_drift_size_scale

        if scale >= 1.0:
            return size

        reduced = self._round_order_size(size * scale, vol_dec)
        if reduced <= 0.0:
            return 0.0
        if min_size > 0.0 and reduced + 1e-12 < min_size:
            # Do not emit an exchange-invalid sub-minimum order. If the verified
            # BaseStrategy had room for min size, keep min size; otherwise skip.
            if size + 1e-12 >= min_size:
                return round(min_size, vol_dec)
            return 0.0
        return min(size, reduced)

    # ------------------------------------------------------------------
    # Bounded execution overlay
    # ------------------------------------------------------------------
    def _adaptive_execution_quality(
        self,
        book_id: int,
    ) -> tuple[float, float, float]:
        """Return (confidence, maker_fill_rate, maker_realized_pnl_ewma)."""
        stats = self._adaptive_book(book_id)
        quotes = sum(stats["buy_quotes"]) + sum(stats["sell_quotes"])
        fills = sum(stats["buy_fills"]) + sum(stats["sell_fills"])
        fill_rate = self._adaptive_clamp(fills / max(quotes, 1), 0.0, 1.0)
        confidence = self._adaptive_clamp(
            quotes / max(self.adaptive_fill_full_confidence_samples * 2, 1),
            0.0,
            1.0,
        )
        maker_pnl = float(stats.get("maker_realized_pnl_ewma", 0.0) or 0.0)
        if not math.isfinite(maker_pnl):
            maker_pnl = 0.0
        return confidence, fill_rate, maker_pnl

    def _adaptive_regime_overlay(
        self,
        book_id: int,
        regime_params: RegimeParamSet,
    ) -> RegimeParamSet:
        phase = self._adaptive_phase()
        if phase in {"DISABLED", "OBSERVE"}:
            return regime_params

        confidence, fill_rate, maker_pnl = self._adaptive_execution_quality(book_id)
        if confidence <= 0.0:
            return regime_params

        pnl_bad = self._adaptive_clamp(
            -maker_pnl / self.adaptive_pnl_scale, 0.0, 1.0
        )
        pnl_good = self._adaptive_clamp(
            maker_pnl / self.adaptive_pnl_scale, 0.0, 1.0
        )

        widen = self.adaptive_max_widen * confidence * pnl_bad
        tighten_need = self._adaptive_clamp(
            (self.adaptive_target_maker_fill - fill_rate)
            / max(self.adaptive_target_maker_fill, 1e-9),
            0.0,
            1.0,
        )
        tighten = (
            self.adaptive_max_tighten
            * confidence
            * pnl_good
            * tighten_need
        )

        if phase == "BOOTSTRAP":
            widen *= 0.60
            tighten *= 0.50
        elif phase == "DRIFT":
            # Drift response is one-sided defensive: widen and reduce size;
            # do not tighten until the environment stabilizes again.
            widen = max(widen, 0.08 * confidence)
            tighten = 0.0

        spread_scale = self._adaptive_clamp(
            1.0 + widen - tighten,
            1.0 - self.adaptive_max_tighten,
            1.0 + self.adaptive_max_widen,
        )

        size_cut = self.adaptive_max_size_cut * confidence * pnl_bad
        if phase == "BOOTSTRAP":
            size_cut *= 0.60
        elif phase == "DRIFT":
            size_cut = max(size_cut, 0.20 * confidence)

        # Never increase BaseStrategy size from this adaptive overlay.
        size_scale = self._adaptive_clamp(1.0 - size_cut, 0.30, 1.0)

        adapted = replace(
            regime_params,
            spread_offset=max(
                0.05, float(regime_params.spread_offset) * spread_scale
            ),
            size_mult=max(
                0.0, float(regime_params.size_mult) * size_scale
            ),
        )
        self._adaptive_last_exec_diag[int(book_id)] = {
            "phase": phase,
            "confidence": confidence,
            "maker_fill_rate": fill_rate,
            "maker_pnl_ewma": maker_pnl,
            "spread_scale": spread_scale,
            "size_scale": size_scale,
            "base_spread_offset": float(regime_params.spread_offset),
            "final_spread_offset": float(adapted.spread_offset),
            "base_size_mult": float(regime_params.size_mult),
            "final_size_mult": float(adapted.size_mult),
        }

        if self.debug_enabled and hasattr(self, "_book_record"):
            record = self._book_record(int(book_id))
            record["adaptive_phase"] = phase
            record["adaptive_exec_conf"] = confidence
            record["adaptive_maker_fill_rate"] = fill_rate
            record["adaptive_maker_pnl_ewma"] = maker_pnl
            record["adaptive_spread_scale"] = spread_scale
            record["adaptive_size_scale"] = size_scale

        return adapted

    def _place_skewed_quotes(
        self,
        response,
        state,
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
        adapted_params = (
            self._adaptive_regime_overlay(book_id, regime_params)
            if self.adaptive_enabled
            else regime_params
        )
        placed = super()._place_skewed_quotes(
            response,
            state,
            book_id,
            book,
            profile,
            prediction,
            inventory,
            adapted_params,
            size,
            edge_bias,
            stats=stats,
        )
        if placed > 0 and self.adaptive_enabled:
            bid = int(book_id)
            self._adaptive_emit(
                "ADAPTIVE_QUOTE",
                tick=int(getattr(self, "_tick", 0) or 0),
                timestamp=getattr(state, "timestamp", None),
                book_id=bid,
                phase=self._adaptive_phase(),
                placed=placed,
                completion_samples=self._completion_observation_count(bid),
                fill=self._adaptive_last_fill_diag.get(bid, {}),
                execution=self._adaptive_last_exec_diag.get(bid, {}),
                inventory_band=getattr(inventory, "band", None),
                inventory_base=getattr(inventory, "net_base", None),
                inventory_ticks=getattr(inventory, "position_ticks", None),
            )
        return placed

    def _global_book_rank(self, expected_alpha: float, mem: BookMemory) -> float:
        base_rank = float(super()._global_book_rank(expected_alpha, mem))
        if not self.adaptive_enabled or self._adaptive_phase() == "OBSERVE":
            return base_rank

        book_id = getattr(mem, "_research_book_id", None)
        if book_id is None:
            return base_rank
        bid = int(book_id)

        # V2: prefer a book that is exactly one observation away from Kappa
        # eligibility.  This is additive to V4.1's progress bonus and remains
        # bounded. DRIFT disables this extra pressure.
        one_away = 0.0
        if self._adaptive_phase() != "DRIFT":
            try:
                samples = self._completion_observation_count(bid)
                target = int(self.research_kappa_completion_target)
                if (
                    samples == target - 1
                    and self._is_kappa_completion_candidate(bid)
                ):
                    one_away = self.adaptive_kappa_one_away_bonus
            except Exception:
                one_away = 0.0

        confidence, fill_rate, maker_pnl = self._adaptive_execution_quality(bid)
        if confidence <= 0.0:
            return base_rank + one_away

        fill_quality = self._adaptive_clamp(
            (fill_rate - self.adaptive_target_maker_fill)
            / max(self.adaptive_target_maker_fill, 1e-9),
            -1.0,
            1.0,
        )
        pnl_quality = math.tanh(maker_pnl / self.adaptive_pnl_scale)
        quality = 0.45 * fill_quality + 0.55 * pnl_quality
        adjust = (
            self.adaptive_rank_max_adjust
            * confidence
            * self._adaptive_clamp(quality, -1.0, 1.0)
        )
        return base_rank + one_away + adjust

    # ------------------------------------------------------------------
    # Dust-compaction selection learning
    # ------------------------------------------------------------------
    def _adaptive_sync_dust_attempts(self) -> None:
        """Account actual BaseStrategy DUST_COMPACT submissions exactly once."""
        for book_id, submitted_tick in getattr(
            self, "_research_dust_compact_active", {}
        ).items():
            bid = int(book_id)
            tick = int(submitted_tick)
            stats = self._adaptive_book(bid)
            accounted = int(stats.get("dust_last_accounted_submit_tick", -1))
            if tick <= accounted:
                continue
            stats["dust_attempts"] = int(stats.get("dust_attempts", 0)) + 1
            stats["dust_last_attempt_tick"] = tick
            if tick == int(stats.get("dust_last_success_submit_tick", -2)):
                stats["dust_fail_streak"] = 0
            else:
                stats["dust_fail_streak"] = min(
                    1_000_000, int(stats.get("dust_fail_streak", 0)) + 1
                )
            stats["dust_last_accounted_submit_tick"] = tick

    def _adaptive_dust_cooldown(self, stats: dict[str, Any]) -> int:
        failures = max(0, int(stats.get("dust_fail_streak", 0)))
        multiplier = 1 + min(failures, 5)
        return min(
            self.adaptive_dust_max_cooldown_ticks,
            self.adaptive_dust_cooldown_ticks * multiplier,
        )

    def _adaptive_dust_fill_posterior(self, stats: dict[str, Any]) -> float:
        attempts = max(0, int(stats.get("dust_attempts", 0)))
        fills = max(0, int(stats.get("dust_fills", 0)))
        return self._adaptive_clamp(
            (
                fills
                + self.adaptive_dust_prior_strength
                * self.adaptive_dust_prior_fill
            )
            / (attempts + self.adaptive_dust_prior_strength),
            0.0,
            1.0,
        )

    def _select_dust_compaction_books(self, state) -> set[int]:
        if not self.adaptive_enabled or not self.adaptive_dust_enabled:
            return super()._select_dust_compaction_books(state)
        if not self.research_dust_compact_enabled:
            return set()

        tick = int(getattr(self, "_tick", 0) or 0)
        self._adaptive_sync_dust_attempts()
        min_size = max(
            0.0, float(getattr(self, "_research_exchange_min_order_size", 0.0))
        )
        rows: list[tuple[float, float, int, int]] = []

        for book_id, info in getattr(self, "_research_parked_dust", {}).items():
            bid = int(book_id)
            qty = float(info.get("net_base", 0.0) or 0.0)
            # BaseStrategy proof condition remains authoritative.
            if not self._is_compactable_dust(qty):
                continue
            if not self._dust_compaction_safe_for_any_fill(qty):
                continue
            if bid not in getattr(state, "books", {}):
                continue

            stats = self._adaptive_book(bid)
            last_tick = int(stats.get("dust_last_attempt_tick", -1))
            cooldown = self._adaptive_dust_cooldown(stats)
            if last_tick >= 0 and tick - last_tick < cooldown:
                continue

            first_tick = int(info.get("first_tick", tick))
            age = max(0, tick - first_tick)
            posterior = self._adaptive_dust_fill_posterior(stats)
            qty_fraction = (
                self._adaptive_clamp(abs(qty) / min_size, 0.0, 1.0)
                if min_size > 0.0
                else 0.0
            )
            age_score = self._adaptive_clamp(
                age / max(float(self.research_dust_warn_ticks), 1.0),
                0.0,
                3.0,
            )
            # Fill likelihood first; then age and closeness to min-order size.
            score = posterior + 0.015 * age_score + 0.01 * qty_fraction
            rows.append((score, abs(qty), age, bid))

        rows.sort(reverse=True)
        selected = {
            bid
            for _score, _qty, _age, bid in rows[
                : self.research_dust_compact_books_per_tick
            ]
        }

        for bid in selected:
            stats = self._adaptive_book(bid)
            stats["dust_selections"] = int(stats.get("dust_selections", 0)) + 1
            stats["dust_last_selection_tick"] = tick

        if selected:
            self._adaptive_emit(
                "ADAPTIVE_DUST",
                tick=tick,
                phase=self._adaptive_phase(),
                selected=sorted(selected),
                candidates=len(rows),
                selected_stats={
                    str(bid): {
                        "posterior": self._adaptive_dust_fill_posterior(
                            self._adaptive_book(bid)
                        ),
                        "selections": self._adaptive_book(bid)["dust_selections"],
                        "attempts": self._adaptive_book(bid)["dust_attempts"],
                        "fills": self._adaptive_book(bid)["dust_fills"],
                        "fail_streak": self._adaptive_book(bid)["dust_fail_streak"],
                        "cooldown": self._adaptive_dust_cooldown(
                            self._adaptive_book(bid)
                        ),
                    }
                    for bid in selected
                },
            )
        return selected

    # ------------------------------------------------------------------
    # Trade outcome learning
    # ------------------------------------------------------------------
    @staticmethod
    def _adaptive_ewma(old: float, new: float, alpha: float = 0.10) -> float:
        if not math.isfinite(new):
            return old
        return (1.0 - alpha) * old + alpha * new

    def onTrade(self, event, validator: str | None = None) -> None:
        book_id = getattr(event, "bookId", None)
        own = (
            getattr(event, "takerAgentId", None) == getattr(self, "uid", None)
            or getattr(event, "makerAgentId", None) == getattr(self, "uid", None)
        )
        pnl_before = 0.0
        before_qty = 0.0
        position_age = 0
        recent_dust_compaction = False
        if book_id is not None:
            pnl_before = float(self._pnl_tick_buffer.get(book_id, 0.0))
            if own:
                try:
                    before_qty = float(self._position_tracker_snapshot(book_id).net_qty)
                except Exception:
                    before_qty = 0.0
                position_age = int(self._position_ticks.get(book_id, 0) or 0)
                try:
                    recent_dust_compaction = (
                        self._is_dust_qty(before_qty)
                        and self._dust_fill_matches_recent_compaction(int(book_id))
                    )
                except Exception:
                    recent_dust_compaction = False

        super().onTrade(event, validator)

        if not self.adaptive_enabled or book_id is None or not own:
            return

        bid = int(book_id)
        stats = self._adaptive_book(bid)
        is_maker = getattr(event, "makerAgentId", None) == getattr(self, "uid", None)
        if is_maker:
            stats["maker_fills"] += 1
            self._adaptive_global["maker_fills"] += 1
        else:
            stats["taker_fills"] += 1
            self._adaptive_global["taker_fills"] += 1

        # Attribute successful safe dust compaction to the Adaptive selector.
        if recent_dust_compaction:
            try:
                after_qty = float(self._position_tracker_snapshot(bid).net_qty)
            except Exception:
                after_qty = before_qty
            if abs(after_qty) < abs(before_qty) - self._execution_flat_epsilon():
                stats["dust_fills"] = int(stats.get("dust_fills", 0)) + 1
                stats["dust_fail_streak"] = 0
                stats["dust_last_fill_tick"] = int(getattr(self, "_tick", 0) or 0)
                submitted_tick = int(
                    getattr(self, "_research_dust_compact_active", {}).get(bid, -1)
                )
                stats["dust_last_success_submit_tick"] = submitted_tick
                self._adaptive_emit(
                    "ADAPTIVE_DUST_FILL",
                    tick=int(getattr(self, "_tick", 0) or 0),
                    book_id=bid,
                    before_qty=before_qty,
                    after_qty=after_qty,
                    selections=stats["dust_selections"],
                    fills=stats["dust_fills"],
                    posterior=self._adaptive_dust_fill_posterior(stats),
                )

        pnl_after = float(self._pnl_tick_buffer.get(book_id, 0.0))
        realized_delta = pnl_after - pnl_before
        if abs(realized_delta) > 1e-12:
            stats["session_realized_obs"] += 1
            self._adaptive_global["session_realized_obs"] += 1
            stats["realized_pnl_ewma"] = self._adaptive_ewma(
                float(stats["realized_pnl_ewma"]),
                realized_delta,
            )
            self._adaptive_global["realized_pnl_ewma"] = self._adaptive_ewma(
                float(self._adaptive_global["realized_pnl_ewma"]),
                realized_delta,
            )

            if is_maker:
                stats["maker_realized_obs"] += 1
                self._adaptive_global["maker_realized_obs"] += 1
                stats["maker_realized_pnl_ewma"] = self._adaptive_ewma(
                    float(stats["maker_realized_pnl_ewma"]),
                    realized_delta,
                    0.10,
                )
                self._adaptive_global["maker_realized_pnl_ewma"] = self._adaptive_ewma(
                    float(self._adaptive_global["maker_realized_pnl_ewma"]),
                    realized_delta,
                    0.10,
                )
                self._adaptive_global["maker_pnl_short_ewma"] = self._adaptive_ewma(
                    float(self._adaptive_global.get("maker_pnl_short_ewma", 0.0)),
                    realized_delta,
                    0.25,
                )
                self._adaptive_global["maker_pnl_long_ewma"] = self._adaptive_ewma(
                    float(self._adaptive_global.get("maker_pnl_long_ewma", 0.0)),
                    realized_delta,
                    0.04,
                )
            else:
                stats["taker_realized_obs"] += 1
                self._adaptive_global["taker_realized_obs"] += 1
                stats["taker_realized_pnl_ewma"] = self._adaptive_ewma(
                    float(stats["taker_realized_pnl_ewma"]),
                    realized_delta,
                    0.10,
                )
                self._adaptive_global["taker_realized_pnl_ewma"] = self._adaptive_ewma(
                    float(self._adaptive_global["taker_realized_pnl_ewma"]),
                    realized_delta,
                    0.10,
                )
                stats["taker_exit_age_ewma"] = self._adaptive_ewma(
                    float(stats["taker_exit_age_ewma"]),
                    float(position_age),
                    0.10,
                )
                self._adaptive_global["taker_exit_age_ewma"] = self._adaptive_ewma(
                    float(self._adaptive_global["taker_exit_age_ewma"]),
                    float(position_age),
                    0.10,
                )

            self._adaptive_emit(
                "ADAPTIVE_REALIZED",
                tick=int(getattr(self, "_tick", 0) or 0),
                timestamp=getattr(event, "timestamp", None),
                book_id=bid,
                phase=self._adaptive_phase(),
                maker=is_maker,
                realized_pnl_delta=realized_delta,
                position_age_ticks=position_age,
                session_book_observations=stats["session_realized_obs"],
                maker_pnl_short=float(self._adaptive_global.get("maker_pnl_short_ewma", 0.0)),
                maker_pnl_long=float(self._adaptive_global.get("maker_pnl_long_ewma", 0.0)),
            )

            if (
                self.adaptive_persistence_enabled
                and self.adaptive_total_requests - self._adaptive_last_saved_request
                >= max(25, self.adaptive_save_every_n // 4)
            ):
                self._adaptive_save_state(True)


if __name__ == "__main__":
    launch(AdaptiveAgent)
