# SPDX-License-Identifier: MIT
"""
AdaptiveAgent — bounded control plane over the frozen BaseStrategy champion.

Architecture
------------
Research  →  BaseStrategy (BASE CHAMPION)  →  AdaptiveAgent

AdaptiveAgent(BaseStrategy). It adapts Base outputs. It does not rebuild
Score-EV, OFI, fill hazard, screening, realization, or orders.

BaseStrategy is authoritative for:

- risk
- order validity
- inventory caps
- dust safety
- maker safety
- volume caps
- final execution

Adaptive only:

1. schedules OBSERVE -> BOOTSTRAP -> NORMAL plus composite DRIFT;
2. applies bounded RegimeParamSet spread/size corrections;
3. keeps per-book/per-side execution memory for drift and quality;
4. persists environment-isolated learning;
5. ranks already-safe dust-compaction candidates with cooldown;
6. overlays Score-EV completion *weights* by phase;
7. applies a bounded expected-value overlay (fill, markout, spread, side, size, exit urgency, specialization; never low-fill → tighten);
8. detects persistent fast-vs-slow drift (fill, markout, pnl, dust, spread, vol, inventory age) and recovers DRIFT -> BOOTSTRAP -> NORMAL;
9. applies a conservative bounded HJB overlay (center, asymmetry, spread, size cut, exit urgency) after Adaptive EV, then Base safety clamps the final order. Raw HJB prices are never submitted.
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
from adaptive_drift import (
    DriftConfig,
    DriftObservation,
    DriftTracker,
    PhaseClocks,
    current_phase,
    enter_or_extend_drift,
    phase_transition_reason,
)
from adaptive_hjb import (
    HjbConfig,
    HjbOverlayBounds,
    HjbState,
    compute_hjb_quote,
    propose_hjb_overlay,
    shadow_quote_ev,
)
from adaptive_ev import (
    EvSnapshot,
    apply_drift_defensive_floors,
    apply_earlier_realization,
    choose_overlay,
)
from adaptive_persistence import (
    CURRENT_SCHEMA,
    apply_session_reset,
    build_identity,
    build_save_payload,
    decide_load,
    infer_network,
    kappa_state_from_stats,
    merge_priors_into_stats,
    parse_environment_key,
    state_filename,
)


class AdaptiveAgent(BaseStrategy):
    """Bounded adaptive execution layer over the verified standalone BaseStrategy."""

    ADAPTIVE_VERSION = "adaptive_v3_hjb_shadow"
    ADAPTIVE_INHERIT_BASE = True
    ADAPTIVE_STATE_SCHEMA = CURRENT_SCHEMA

    # ------------------------------------------------------------------
    # Lifecycle / configuration
    # ------------------------------------------------------------------
    def initialize(self) -> None:
        super().initialize()
        self._adaptive_enforce_champion_caps()
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
        # Default off: Base frozen hazard is the fill engine. Residual overlay
        # is allowed only when Base reports a fallback_reason.
        self.adaptive_fill_overlay_enabled = self._adaptive_bool(
            getattr(cfg, "adaptive_fill_overlay_enabled", False)
        )

        # Bounded EV overlay. Tightening is allowed only when AdaptiveUtility
        # beats the Base quote. Size never exceeds the Base multiplier.
        self.adaptive_max_widen = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_max_widen", 0.18)), 0.0, 0.50
        )
        self.adaptive_max_tighten = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_max_tighten", 0.06)), 0.0, 0.15
        )
        self.adaptive_max_size_cut = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_max_size_cut", 0.35)), 0.0, 0.70
        )
        self.adaptive_max_exit_boost = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_max_exit_boost", 0.20)), 0.0, 0.35
        )
        self.adaptive_min_side_scale = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_min_side_scale", 0.50)), 0.25, 1.0
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

        # Fast-vs-slow drift detector. V2 compared a 250-request fill window
        # against the all-time fill rate, which stayed NORMAL through gradual
        # execution-rate changes. V3 requires multiple deteriorating windows
        # plus a sample floor, then recovers DRIFT -> BOOTSTRAP -> NORMAL.
        self.adaptive_drift_window_requests = max(
            25, int(float(getattr(cfg, "adaptive_drift_window_requests", 100)))
        )
        self.adaptive_drift_start_requests = max(
            self.adaptive_observe_requests,
            int(float(getattr(cfg, "adaptive_drift_start_requests", self.adaptive_observe_requests))),
        )
        self.adaptive_drift_min_quotes = max(
            10, int(float(getattr(cfg, "adaptive_drift_min_quotes", 30)))
        )
        self.adaptive_drift_min_windows = max(
            2, int(float(getattr(cfg, "adaptive_drift_min_windows", 2)))
        )
        self.adaptive_drift_min_samples = max(
            20, int(float(getattr(cfg, "adaptive_drift_min_samples", 40)))
        )
        self.adaptive_drift_min_window_samples = max(
            8, int(float(getattr(cfg, "adaptive_drift_min_window_samples", 20)))
        )
        self.adaptive_drift_min_signals = max(
            1, int(float(getattr(cfg, "adaptive_drift_min_signals", 1)))
        )
        self.adaptive_drift_fast_alpha = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_fast_alpha", 0.15)), 0.02, 0.50
        )
        self.adaptive_drift_slow_alpha = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_slow_alpha", 0.03)), 0.005, 0.15
        )
        self.adaptive_drift_fill_abs_min = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_fill_abs_min", 0.02)), 0.001, 0.10
        )
        self.adaptive_drift_fill_relative = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_fill_relative", 0.25)), 0.10, 1.50
        )
        self.adaptive_drift_spread_ratio = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_spread_ratio", 1.25)), 1.05, 2.50
        )
        self.adaptive_drift_spread_delta_bps = max(
            0.5, float(getattr(cfg, "adaptive_drift_spread_delta_bps", 4.0))
        )
        self.adaptive_drift_markout_delta_bps = max(
            0.5, float(getattr(cfg, "adaptive_drift_markout_delta_bps", 2.0))
        )
        self.adaptive_drift_dust_abs = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_dust_abs", 0.05)), 0.01, 0.40
        )
        self.adaptive_drift_vol_abs = max(
            1e-6, float(getattr(cfg, "adaptive_drift_vol_abs", 0.0008))
        )
        self.adaptive_drift_vol_rel = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_vol_rel", 0.25)), 0.05, 1.50
        )
        self.adaptive_drift_inventory_age_abs = max(
            0.5, float(getattr(cfg, "adaptive_drift_inventory_age_abs", 3.0))
        )
        self.adaptive_drift_inventory_age_rel = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_inventory_age_rel", 0.25)), 0.05, 1.50
        )
        self.adaptive_drift_min_widen = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_min_widen", 0.08)), 0.0, 0.50
        )
        self.adaptive_drift_exit_boost = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_exit_boost", 0.12)), 0.0, 0.35
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
        self.adaptive_drift_hold_requests = max(
            self.adaptive_drift_window_requests,
            int(float(getattr(cfg, "adaptive_drift_hold_requests", 500))),
        )
        self.adaptive_drift_recovery_requests = max(
            self.adaptive_drift_window_requests,
            int(float(getattr(cfg, "adaptive_drift_recovery_requests", 500))),
        )
        self.adaptive_drift_trust_scale = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_drift_trust_scale", 0.35)), 0.05, 1.0
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

        # Kept for launcher compatibility. Score-EV already pays one-away;
        # Adaptive V3 does not add a second completion bonus.
        self.adaptive_kappa_one_away_bonus = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_kappa_one_away_bonus", 0.0)), 0.0, 0.15
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

        self.adaptive_hjb_shadow_enabled = self._adaptive_bool(
            getattr(cfg, "adaptive_hjb_shadow_enabled", True)
        )
        # Raw HJB prices are never submitted. A conservative bounded overlay
        # may nudge Base params after Adaptive EV.
        requested_hjb_policy = self._adaptive_bool(
            getattr(cfg, "adaptive_hjb_policy_enabled", False)
        )
        self.adaptive_hjb_policy_enabled = False
        if requested_hjb_policy:
            bt.logging.warning(
                "AdaptiveAgent: raw HJB policy requested but ignored; using bounded overlay"
            )
        self.adaptive_hjb_overlay_enabled = self._adaptive_bool(
            getattr(cfg, "adaptive_hjb_overlay_enabled", True)
        )
        self.adaptive_hjb_gamma = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_gamma", 0.15)), 0.01, 1.0
        )
        self.adaptive_hjb_gamma_min = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_gamma_min", 0.05)), 0.01, 0.50
        )
        self.adaptive_hjb_gamma_max = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_gamma_max", 0.60)), 0.05, 2.0
        )
        if self.adaptive_hjb_gamma_max < self.adaptive_hjb_gamma_min:
            self.adaptive_hjb_gamma_max = self.adaptive_hjb_gamma_min
        self.adaptive_hjb_kappa = max(
            0.05, float(getattr(cfg, "adaptive_hjb_kappa", 1.5))
        )
        self.adaptive_hjb_horizon = max(
            0.05, float(getattr(cfg, "adaptive_hjb_horizon", 1.0))
        )
        self.adaptive_hjb_alpha_shift = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_alpha_shift", 0.28)), 0.0, 1.0
        )
        self.adaptive_hjb_vol_floor = max(
            1e-6, float(getattr(cfg, "adaptive_hjb_vol_floor", 5e-4))
        )
        self.adaptive_hjb_latency_weight = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_latency_weight", 0.15)), 0.0, 1.0
        )
        self.adaptive_hjb_adverse_weight = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_adverse_weight", 0.20)), 0.0, 1.0
        )
        self.adaptive_hjb_overlay_mix = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_overlay_mix", 0.25)), 0.0, 0.50
        )
        self.adaptive_hjb_max_center_frac = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_max_center_frac", 0.05)), 0.0, 0.15
        )
        self.adaptive_hjb_overlay_max_widen = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_overlay_max_widen", 0.06)), 0.0, 0.20
        )
        self.adaptive_hjb_overlay_max_tighten = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_overlay_max_tighten", 0.02)), 0.0, 0.08
        )
        self.adaptive_hjb_overlay_max_side = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_overlay_max_side", 0.10)), 0.0, 0.25
        )
        self.adaptive_hjb_overlay_max_size_cut = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_overlay_max_size_cut", 0.15)), 0.0, 0.35
        )
        self.adaptive_hjb_overlay_max_exit = self._adaptive_clamp(
            float(getattr(cfg, "adaptive_hjb_overlay_max_exit", 0.08)), 0.0, 0.20
        )
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
        self._adaptive_base_score_ev_one_away = float(
            getattr(self, "score_ev_one_away_weight", 0.18) or 0.18
        )
        self._adaptive_base_score_ev_two_away = float(
            getattr(self, "score_ev_two_away_weight", 0.06) or 0.06
        )

        # Runtime state.
        self.adaptive_total_requests = 0
        self.adaptive_session_requests = 0
        self._adaptive_last_sim_timestamp: int | None = None
        self._adaptive_last_saved_request = 0
        self._adaptive_drift_until_request = 0
        self._adaptive_recovery_until_request = 0
        self._adaptive_drift_tracker = DriftTracker(self._adaptive_drift_config())
        self._adaptive_last_drift_metrics: dict[str, Any] = {}
        self._adaptive_last_phase = "OBSERVE"

        self._adaptive_books: dict[int, dict[str, Any]] = {}
        self._adaptive_global = self._adaptive_new_stats()
        self._adaptive_state_lock = threading.Lock()
        self._adaptive_last_fill_diag: dict[int, dict[str, Any]] = {}
        self._adaptive_last_exec_diag: dict[int, dict[str, Any]] = {}
        self._adaptive_last_hjb: dict[int, dict[str, Any]] = {}
        self._adaptive_identity = self._adaptive_current_identity()
        self._adaptive_load_reason = "uninitialized"

        if self.adaptive_persistence_enabled:
            self._adaptive_load_state()
            atexit.register(self._adaptive_save_state, True)
        else:
            self._adaptive_load_reason = "disabled"

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
            persistence_reason=self._adaptive_load_reason,
            identity=self._adaptive_identity,
            observe_requests=self.adaptive_observe_requests,
            normal_after_requests=self.adaptive_normal_after_requests,
            drift_start_requests=self.adaptive_drift_start_requests,
            drift_window_requests=self.adaptive_drift_window_requests,
            drift_min_windows=self.adaptive_drift_min_windows,
            drift_min_samples=self.adaptive_drift_min_samples,
            drift_recovery_requests=self.adaptive_drift_recovery_requests,
            drift_trust_scale=self.adaptive_drift_trust_scale,
            drift_fill_abs_min=self.adaptive_drift_fill_abs_min,
            drift_fill_relative=self.adaptive_drift_fill_relative,
            drift_spread_ratio=self.adaptive_drift_spread_ratio,
            drift_spread_delta_bps=self.adaptive_drift_spread_delta_bps,
            drift_markout_delta_bps=self.adaptive_drift_markout_delta_bps,
            drift_dust_abs=self.adaptive_drift_dust_abs,
            drift_pnl_hard_floor=self.adaptive_drift_pnl_hard_floor,
            drift_pnl_ratio=self.adaptive_drift_pnl_ratio,
            kappa_one_away_bonus=self.adaptive_kappa_one_away_bonus,
            fill_overlay_enabled=self.adaptive_fill_overlay_enabled,
            hjb_shadow_enabled=self.adaptive_hjb_shadow_enabled,
            hjb_overlay_enabled=self.adaptive_hjb_overlay_enabled,
            hjb_policy_enabled=self.adaptive_hjb_policy_enabled,
            hjb_gamma=self.adaptive_hjb_gamma,
            dust_cooldown_ticks=self.adaptive_dust_cooldown_ticks,
            dust_max_cooldown_ticks=self.adaptive_dust_max_cooldown_ticks,
        )

    def _adaptive_enforce_champion_caps(self) -> None:
        """Keep Base hard caps authoritative. Overlays may tighten, never loosen."""
        self.min_expected_alpha = max(
            0.18, float(getattr(self, "min_expected_alpha", 0.18) or 0.18)
        )
        self.mm_base_size = min(
            0.25, max(0.0, float(getattr(self, "mm_base_size", 0.25) or 0.25))
        )
        self.max_inventory_base = min(
            1.20, max(0.0, float(getattr(self, "max_inventory_base", 1.20) or 1.20))
        )
        self.max_mm_books_per_tick = min(
            4, max(1, int(getattr(self, "max_mm_books_per_tick", 4) or 4))
        )
        if hasattr(self, "max_managed_books_per_tick"):
            self.max_managed_books_per_tick = min(
                8, max(1, int(self.max_managed_books_per_tick or 8))
            )
        self.mm_force_post_only = True
        if hasattr(self, "score_ev_new_book_weight"):
            self.score_ev_new_book_weight = 0.0

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
        identity = getattr(self, "_adaptive_identity", None) or self._adaptive_current_identity()
        return Path(self.adaptive_state_dir) / state_filename(identity)

    def _adaptive_current_identity(self) -> dict[str, Any]:
        cfg = getattr(self, "config", None)
        env = str(getattr(self, "adaptive_environment_key", "unscoped") or "unscoped")
        parsed_network, parsed_uid = parse_environment_key(env)
        endpoint = ""
        if cfg is not None:
            endpoint = str(getattr(cfg, "endpoint", "") or "")
        endpoint = endpoint or str(os.getenv("BT_ENDPOINT", "") or "")
        network = infer_network(endpoint=endpoint, environment_key=env)
        if network == "unknown":
            network = parsed_network
        netuid = parsed_uid
        if netuid is None and cfg is not None:
            try:
                netuid = int(getattr(cfg, "netuid"))
            except (TypeError, ValueError, AttributeError):
                netuid = None
        min_order = float(getattr(self, "_research_exchange_min_order_size", 0.0) or 0.0)
        if min_order <= 0.0:
            min_order = float(
                getattr(self, "min_order_size", 0.0)
                or getattr(self, "mm_base_size", 0.0)
                or 0.0
            )
        family = "im"
        if cfg is not None:
            family = str(getattr(cfg, "simulation_family", "") or family)
        family = str(os.getenv("ADAPTIVE_SIMULATION_FAMILY", "") or family or "im")
        return build_identity(
            network=network,
            netuid=netuid,
            validator_environment=env,
            base_version=str(getattr(self, "DEPLOY_POLICY_VERSION", "unknown")),
            adaptive_version=str(self.ADAPTIVE_VERSION),
            schema=int(self.ADAPTIVE_STATE_SCHEMA),
            min_order_size=min_order,
            simulation_family=family,
        )

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
        """Load compatible execution priors only. Never restore a phase clock."""
        self.adaptive_total_requests = 0
        self.adaptive_session_requests = 0
        self._adaptive_drift_until_request = 0
        self._adaptive_recovery_until_request = 0
        self._adaptive_last_sim_timestamp = None
        self._adaptive_load_reason = "missing"
        path = self._adaptive_state_path()
        try:
            if not path.is_file():
                return
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                self._adaptive_load_reason = "corrupted"
                bt.logging.warning(f"AdaptiveAgent: corrupted state {path}")
                return
            identity = getattr(self, "_adaptive_identity", None) or self._adaptive_current_identity()
            decision = decide_load(identity, raw)
            self._adaptive_load_reason = decision.reason
            self.adaptive_total_requests = 0
            self._adaptive_global = merge_priors_into_stats(
                self._adaptive_new_stats(), decision.global_priors
            )
            self._adaptive_books = {}
            for book_id, priors in decision.book_priors.items():
                self._adaptive_books[int(book_id)] = merge_priors_into_stats(
                    self._adaptive_new_stats(), priors
                )
            self._adaptive_reset_drift_window()
            self._adaptive_log_reset(
                reason=f"load_{decision.reason}",
                old_phase="UNKNOWN",
                old_requests=0,
                kappa_books=0,
                kept_priors=decision.prior_factor > 0.0,
                mismatches=decision.mismatches,
                prior_factor=decision.prior_factor,
            )
            bt.logging.info(
                "AdaptiveAgent: loaded persistence "
                f"reason={decision.reason} factor={decision.prior_factor} "
                f"phase={decision.phase} mismatches={decision.mismatches}"
            )
        except Exception as exc:
            self._adaptive_load_reason = "corrupted"
            self.adaptive_total_requests = 0
            bt.logging.warning(f"AdaptiveAgent: state load failed: {exc}")

    def _adaptive_state_payload(self) -> dict[str, Any]:
        identity = getattr(self, "_adaptive_identity", None) or self._adaptive_current_identity()
        return build_save_payload(
            identity=identity,
            global_stats=self._adaptive_global,
            book_stats=self._adaptive_books,
            session_state={
                "total_requests": int(self.adaptive_total_requests),
                "session_requests": int(self.adaptive_session_requests),
                "last_sim_timestamp": self._adaptive_last_sim_timestamp,
                "phase": self._adaptive_phase(),
            },
            kappa_state=kappa_state_from_stats(self._adaptive_global, self._adaptive_books),
            drift_state={
                "drift_until_request": int(self._adaptive_drift_until_request),
                "recovery_until_request": int(self._adaptive_recovery_until_request),
                "metrics": dict(self._adaptive_last_drift_metrics or {}),
            },
        )

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

    def _adaptive_drift_config(self) -> DriftConfig:
        return DriftConfig(
            fast_alpha=float(self.adaptive_drift_fast_alpha),
            slow_alpha=float(self.adaptive_drift_slow_alpha),
            window_requests=int(self.adaptive_drift_window_requests),
            min_windows=int(self.adaptive_drift_min_windows),
            min_samples=int(self.adaptive_drift_min_samples),
            min_window_samples=int(self.adaptive_drift_min_window_samples),
            min_signals=int(self.adaptive_drift_min_signals),
            fill_abs=float(self.adaptive_drift_fill_abs_min),
            fill_rel=float(self.adaptive_drift_fill_relative),
            markout_delta_bps=float(self.adaptive_drift_markout_delta_bps),
            spread_ratio=float(self.adaptive_drift_spread_ratio),
            spread_delta_bps=float(self.adaptive_drift_spread_delta_bps),
            pnl_hard_floor=float(self.adaptive_drift_pnl_hard_floor),
            pnl_ratio=float(self.adaptive_drift_pnl_ratio),
            pnl_baseline_min=float(self.adaptive_drift_pnl_baseline_min),
            dust_abs=float(self.adaptive_drift_dust_abs),
            vol_abs=float(self.adaptive_drift_vol_abs),
            vol_rel=float(self.adaptive_drift_vol_rel),
            inventory_age_abs=float(self.adaptive_drift_inventory_age_abs),
            inventory_age_rel=float(self.adaptive_drift_inventory_age_rel),
            hold_requests=int(self.adaptive_drift_hold_requests),
            recovery_requests=int(self.adaptive_drift_recovery_requests),
        )

    def _adaptive_reset_drift_window(self) -> None:
        tracker = getattr(self, "_adaptive_drift_tracker", None)
        if tracker is None:
            self._adaptive_drift_tracker = DriftTracker(self._adaptive_drift_config())
        else:
            tracker.cfg = self._adaptive_drift_config()
            tracker.reset(request=int(self.adaptive_total_requests))
        self._adaptive_last_drift_metrics = {}
        self._adaptive_last_saved_request = int(self.adaptive_total_requests)

    def _adaptive_phase_clocks(self) -> PhaseClocks:
        return PhaseClocks(
            observe_requests=int(self.adaptive_observe_requests),
            normal_after_requests=int(self.adaptive_normal_after_requests),
            drift_until_request=int(self._adaptive_drift_until_request),
            recovery_until_request=int(self._adaptive_recovery_until_request),
            total_requests=int(self.adaptive_total_requests),
        )

    def _adaptive_phase(self) -> str:
        return current_phase(
            self._adaptive_phase_clocks(),
            enabled=bool(self.adaptive_enabled),
        )

    def _adaptive_global_fill_totals(self) -> tuple[int, int]:
        g = self._adaptive_global
        quotes = sum(g["buy_quotes"]) + sum(g["sell_quotes"])
        fills = sum(g["buy_fills"]) + sum(g["sell_fills"])
        return int(quotes), int(fills)

    def _adaptive_mean(self, values: list[float]) -> float | None:
        if not values:
            return None
        return float(sum(values) / len(values))

    def _adaptive_mean_profile_field(self, name: str) -> float | None:
        profiles = getattr(self, "_last_profiles", None) or []
        values: list[float] = []
        for profile in profiles:
            try:
                value = float(getattr(profile, name, 0.0) or 0.0)
            except (TypeError, ValueError, AttributeError):
                continue
            if math.isfinite(value) and value >= 0.0:
                values.append(value)
        return self._adaptive_mean(values)

    def _adaptive_mean_inventory_age(self) -> float | None:
        ages: list[float] = []
        for decision in (getattr(self, "_realization_last", {}) or {}).values():
            try:
                age = float(getattr(decision, "inventory_age", 0.0) or 0.0)
            except (TypeError, ValueError, AttributeError):
                continue
            if math.isfinite(age) and age > 0.0:
                ages.append(age)
        return self._adaptive_mean(ages)

    def _adaptive_median_spread_bps(self) -> float | None:
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
            return None
        spreads.sort()
        n = len(spreads)
        if n % 2:
            return float(spreads[n // 2])
        return float(0.5 * (spreads[n // 2 - 1] + spreads[n // 2]))

    def _adaptive_collect_drift_observation(self) -> DriftObservation:
        hazards: list[float] = []
        actionables: list[float] = []
        dusts: list[float] = []
        markouts: list[float] = []
        last = getattr(self, "_execution_last", {}) or {}
        for row in last.values():
            if not isinstance(row, dict):
                continue
            for pred in (row.get("buy"), row.get("sell")):
                hazard = self._adaptive_pred_field(pred, "any_fill")
                actionable = self._adaptive_pred_field(pred, "actionable_fill")
                dust = self._adaptive_pred_field(pred, "dust")
                if hazard is not None:
                    hazards.append(hazard)
                if actionable is not None:
                    actionables.append(actionable)
                if dust is not None:
                    dusts.append(dust)
        for ev in (getattr(self, "_score_ev_last", {}) or {}).values():
            try:
                markout = getattr(ev, "expected_markout_bps", None)
                if markout is None:
                    continue
                markout = float(markout)
            except (TypeError, ValueError, AttributeError):
                continue
            if math.isfinite(markout):
                markouts.append(markout)

        dust_rate = self._adaptive_mean(dusts)
        if dust_rate is None:
            attempts = int(self._adaptive_global.get("dust_attempts", 0) or 0)
            if attempts > 0:
                fills = int(self._adaptive_global.get("dust_fills", 0) or 0)
                dust_rate = 1.0 - fills / max(attempts, 1)

        maker_obs = int(self._adaptive_global.get("maker_realized_obs", 0) or 0)
        maker_pnl = None
        if maker_obs >= int(self.adaptive_drift_min_maker_realized):
            maker_pnl = float(
                self._adaptive_global.get("maker_pnl_short_ewma", 0.0) or 0.0
            )
            if not math.isfinite(maker_pnl):
                maker_pnl = None

        return DriftObservation(
            fill_hazard=self._adaptive_mean(hazards),
            actionable_fill=self._adaptive_mean(actionables),
            markout_bps=self._adaptive_mean(markouts),
            spread_bps=self._adaptive_median_spread_bps(),
            maker_pnl=maker_pnl,
            realized_pnl=maker_pnl,
            dust_rate=dust_rate,
            volatility=self._adaptive_mean_profile_field("volatility"),
            inventory_age=self._adaptive_mean_inventory_age(),
        )

    def _adaptive_maybe_detect_drift(self) -> None:
        tracker = getattr(self, "_adaptive_drift_tracker", None)
        if tracker is None:
            return
        tracker.observe(self._adaptive_collect_drift_observation())
        verdict = tracker.maybe_close_window(int(self.adaptive_total_requests))
        if verdict is None:
            return

        old_until = int(self._adaptive_drift_until_request)
        old_phase = self._adaptive_last_phase
        armed = self.adaptive_total_requests >= self.adaptive_drift_start_requests
        if not armed:
            tracker.consecutive_deteriorating = 0
        trigger = bool(verdict.trigger_drift and armed)
        if trigger:
            clocks = enter_or_extend_drift(
                self._adaptive_phase_clocks(),
                hold_requests=int(self.adaptive_drift_hold_requests),
                recovery_requests=int(self.adaptive_drift_recovery_requests),
            )
            self._adaptive_drift_until_request = int(clocks.drift_until_request)
            self._adaptive_recovery_until_request = int(clocks.recovery_until_request)

        metrics = {
            "tick": int(getattr(self, "_tick", 0) or 0),
            "requests": self.adaptive_total_requests,
            "phase_before": old_phase,
            "phase_after": self._adaptive_phase(),
            "armed": int(armed),
            "trigger": trigger,
            "drift_extended": int(self._adaptive_drift_until_request > old_until),
            "drift_until_request": self._adaptive_drift_until_request,
            "recovery_until_request": self._adaptive_recovery_until_request,
            "trust_scale": (
                float(self.adaptive_drift_trust_scale)
                if self._adaptive_phase() == "DRIFT"
                else 1.0
            ),
            **verdict.as_log(),
            **tracker.as_log(),
        }
        self._adaptive_last_drift_metrics = metrics
        self._adaptive_emit(
            "ADAPTIVE_DRIFT",
            force=trigger or verdict.deteriorated,
            **metrics,
        )

    def _adaptive_log_phase_if_changed(self) -> None:
        phase = self._adaptive_phase()
        if phase == self._adaptive_last_phase:
            return
        old = self._adaptive_last_phase
        self._adaptive_last_phase = phase
        reason = phase_transition_reason(old, phase)
        bt.logging.info(
            f"AdaptiveAgent: phase {old} -> {phase} "
            f"reason={reason} requests={self.adaptive_total_requests}"
        )
        self._adaptive_emit(
            "ADAPTIVE_PHASE",
            force=True,
            tick=int(getattr(self, "_tick", 0) or 0),
            old_phase=old,
            new_phase=phase,
            reason=reason,
            requests=self.adaptive_total_requests,
            drift_until_request=self._adaptive_drift_until_request,
            recovery_until_request=self._adaptive_recovery_until_request,
            trust_scale=(
                float(self.adaptive_drift_trust_scale) if phase == "DRIFT" else 1.0
            ),
            drift=self._adaptive_last_drift_metrics,
        )

    def _adaptive_log_reset(
        self,
        *,
        reason: str,
        old_phase: str,
        old_requests: int,
        kappa_books: int,
        kept_priors: bool,
        mismatches: list[str] | None = None,
        prior_factor: float | None = None,
    ) -> None:
        payload = {
            "reason": reason,
            "old_phase": old_phase,
            "new_phase": "OBSERVE",
            "old_requests": int(old_requests),
            "cleared_kappa_books": int(kappa_books),
            "kept_execution_priors": int(bool(kept_priors)),
            "mismatches": list(mismatches or []),
            "prior_factor": prior_factor,
            "identity": getattr(self, "_adaptive_identity", None),
        }
        bt.logging.info(
            "AdaptiveAgent: RESET "
            f"reason={reason} {old_phase}->OBSERVE "
            f"requests={old_requests} kappa_books={kappa_books} "
            f"kept_priors={int(bool(kept_priors))}"
        )
        self._adaptive_emit("ADAPTIVE_RESET", force=True, **payload)

    def _adaptive_reset_session_scoped_state(self, reason: str = "session_reset") -> None:
        """New scoring episode: OBSERVE. Keep environment execution priors."""
        old_phase = self._adaptive_phase()
        old_requests = int(self.adaptive_total_requests)
        kappa_books = sum(
            1
            for stats in self._adaptive_books.values()
            if int(stats.get("session_realized_obs", 0) or 0) > 0
        )
        apply_session_reset(self._adaptive_global)
        for stats in self._adaptive_books.values():
            apply_session_reset(stats)

        if hasattr(self, "_research_realized_observations_by_book"):
            self._research_realized_observations_by_book.clear()
        if hasattr(self, "_research_round_trip_samples_by_book"):
            self._research_round_trip_samples_by_book.clear()

        self.adaptive_total_requests = 0
        self.adaptive_session_requests = 0
        self._adaptive_drift_until_request = 0
        self._adaptive_recovery_until_request = 0
        self._adaptive_last_sim_timestamp = None
        self._adaptive_reset_drift_window()
        self._adaptive_last_phase = "OBSERVE"
        self._adaptive_log_reset(
            reason=reason,
            old_phase=old_phase,
            old_requests=old_requests,
            kappa_books=kappa_books,
            kept_priors=True,
        )

    # ------------------------------------------------------------------
    # Main request hook
    # ------------------------------------------------------------------

    def _adaptive_apply_phase_controls(self) -> tuple:
        """Apply temporary scheduler / Score-EV weight overlays; return restore tuple."""
        old = (
            int(self.max_mm_books_per_tick),
            bool(self.research_kappa_completion_enabled),
            float(self.research_kappa_completion_rank_bonus),
            int(self.research_kappa_completion_relaxed_success_cap),
            float(getattr(self, "score_ev_one_away_weight", self._adaptive_base_score_ev_one_away)),
            float(getattr(self, "score_ev_two_away_weight", self._adaptive_base_score_ev_two_away)),
        )
        phase = self._adaptive_phase()
        one = self._adaptive_base_score_ev_one_away
        two = self._adaptive_base_score_ev_two_away

        if phase == "OBSERVE":
            self.max_mm_books_per_tick = min(
                self._adaptive_base_max_mm_books,
                self.adaptive_observe_max_mm_books,
            )
            self.research_kappa_completion_enabled = False
            self.research_kappa_completion_rank_bonus = 0.0
            self.research_kappa_completion_relaxed_success_cap = 0
            self.score_ev_one_away_weight = 0.0
            self.score_ev_two_away_weight = 0.0
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
            scale = self.adaptive_bootstrap_kappa_rank_scale
            self.score_ev_one_away_weight = one * scale
            self.score_ev_two_away_weight = two * scale
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
            self.score_ev_one_away_weight = one * 0.25
            self.score_ev_two_away_weight = two * 0.25
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
            self.score_ev_one_away_weight = one
            self.score_ev_two_away_weight = two
        return old

    def _adaptive_restore_phase_controls(self, old: tuple) -> None:
        (
            self.max_mm_books_per_tick,
            self.research_kappa_completion_enabled,
            self.research_kappa_completion_rank_bonus,
            self.research_kappa_completion_relaxed_success_cap,
            self.score_ev_one_away_weight,
            self.score_ev_two_away_weight,
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
                self._adaptive_reset_session_scoped_state("sim_timestamp_rewind")

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
            # Consume Base outputs already computed this request; no extra
            # 128-book scan.
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
                    market_regime=getattr(self, "_market_regime", None),
                    score_regime=getattr(self, "_score_regime", None),
                    ttl_min_ms=getattr(self, "ttl_min_ms", None),
                    ttl_max_ms=getattr(self, "ttl_max_ms", None),
                    persistence_reason=getattr(self, "_adaptive_load_reason", None),
                    drift_until_request=self._adaptive_drift_until_request,
                    recovery_until_request=self._adaptive_recovery_until_request,
                    drift=self._adaptive_last_drift_metrics,
                )
            self._adaptive_save_state(False)
        return response

    # ------------------------------------------------------------------
    # Restart-safe Kappa completion state
    # ------------------------------------------------------------------
    def _completion_observation_count(self, book_id: int) -> int:
        # Episode-only Adaptive Kappa memory. Load and sim-reset zero
        # session_realized_obs so a prior simulation cannot inflate Base.
        local = int(super()._completion_observation_count(book_id))
        episode = int(self._adaptive_book(book_id).get("session_realized_obs", 0))
        return max(local, episode)

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

    @staticmethod
    def _adaptive_pred_field(pred: Any, name: str) -> float | None:
        if pred is None:
            return None
        try:
            value = getattr(pred, name, None)
            if value is None:
                return None
            value = float(value)
        except (TypeError, ValueError, AttributeError):
            return None
        return value if math.isfinite(value) else None

    def _adaptive_base_outputs(self, book_id: int | None = None) -> dict[str, Any]:
        """Read BaseStrategy engine outputs. Never recompute them here."""
        out: dict[str, Any] = {
            "market_regime": getattr(self, "_market_regime", None),
            "score_regime": getattr(self, "_score_regime", None),
            "ttl_min_ms": getattr(self, "ttl_min_ms", None),
            "ttl_max_ms": getattr(self, "ttl_max_ms", None),
        }
        if book_id is None:
            return out
        bid = int(book_id)
        last = (getattr(self, "_execution_last", {}) or {}).get(bid, {}) or {}
        buy_pred = last.get("buy")
        sell_pred = last.get("sell")
        ev = (getattr(self, "_score_ev_last", {}) or {}).get(bid)
        snap = (getattr(self, "_quote_submit_snapshot", {}) or {}).get(bid, {}) or {}
        acts = [
            v
            for v in (
                self._adaptive_pred_field(buy_pred, "actionable_fill"),
                self._adaptive_pred_field(sell_pred, "actionable_fill"),
            )
            if v is not None
        ]
        dusts = [
            v
            for v in (
                self._adaptive_pred_field(buy_pred, "dust"),
                self._adaptive_pred_field(sell_pred, "dust"),
            )
            if v is not None
        ]
        out.update(
            {
                "fallback_reason": last.get("fallback_reason") or "",
                "model_confidence": last.get("model_confidence"),
                "fill_hazard_any_buy": self._adaptive_pred_field(buy_pred, "any_fill"),
                "fill_hazard_any_sell": self._adaptive_pred_field(sell_pred, "any_fill"),
                "fill_hazard_usable": bool(
                    (buy_pred is not None and bool(getattr(buy_pred, "usable", False)))
                    or (sell_pred is not None and bool(getattr(sell_pred, "usable", False)))
                ),
                "actionable_fill_probability": (
                    None if ev is None else getattr(ev, "actionable_fill_prob", None)
                ),
                "dust_probability": None if ev is None else getattr(ev, "dust_prob", None),
                "score_ev": None if ev is None else getattr(ev, "final_score", None),
                "kappa_completion_value": (
                    None if ev is None else getattr(ev, "completion_value", None)
                ),
                "markout_estimate": (
                    None if ev is None else getattr(ev, "expected_markout_bps", None)
                ),
                "trading_ev": None if ev is None else getattr(ev, "trading_ev", None),
                "observations_remaining": (
                    None if ev is None else getattr(ev, "observations_remaining", None)
                ),
                "ofi": snap.get("ofi_fast") if snap.get("ofi_supported") else None,
                "ofi_supported": snap.get("ofi_supported"),
                "imbalance": snap.get("imbalance"),
                "chosen_ttl": snap.get("chosen_ttl"),
                "inventory_util": snap.get("inventory_util"),
                "candidate_reject_reason": (
                    None if ev is None else getattr(ev, "reject_reason", None)
                ),
            }
        )
        if out["actionable_fill_probability"] is None and acts:
            out["actionable_fill_probability"] = sum(acts) / len(acts)
        if out["dust_probability"] is None and dusts:
            out["dust_probability"] = sum(dusts) / len(dusts)
        return out

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
        if book_id is not None:
            self._adaptive_last_fill_diag[int(book_id)] = {
                "phase": self._adaptive_phase(),
                "base_buy": float(base.buy),
                "base_sell": float(base.sell),
                "final_buy": float(base.buy),
                "final_sell": float(base.sell),
                "overlay": 0,
                **self._adaptive_base_outputs(int(book_id)),
            }
        if (
            not self.adaptive_enabled
            or not getattr(self, "adaptive_fill_overlay_enabled", False)
            or book_id is None
            or spread <= 0.0
            or mid <= 0.0
        ):
            return base

        last = (getattr(self, "_execution_last", {}) or {}).get(int(book_id), {}) or {}
        fallback_reason = str(last.get("fallback_reason") or "")
        if not fallback_reason:
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
            "overlay": 1,
            "fallback_reason": fallback_reason,
            **self._adaptive_base_outputs(bid),
        }
        self._adaptive_last_fill_diag[bid] = diag
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

    def _adaptive_ev_snapshot(self, book_id: int) -> EvSnapshot:
        """Build an EV snapshot from Base outputs plus Adaptive memory."""
        bid = int(book_id)
        outputs = self._adaptive_base_outputs(bid)
        conf, fill_rate, maker_pnl = self._adaptive_execution_quality(bid)
        ev = (getattr(self, "_score_ev_last", {}) or {}).get(bid)
        reject = "" if ev is None else str(getattr(ev, "reject_reason", "") or "")
        if reject in {"TOXIC", "UNSAFE", "INVENTORY_BLOCKED"}:
            conf = 0.0
        p = outputs.get("actionable_fill_probability")
        if p is None:
            buy_p = outputs.get("fill_hazard_any_buy")
            sell_p = outputs.get("fill_hazard_any_sell")
            vals = [float(v) for v in (buy_p, sell_p) if v is not None]
            p = sum(vals) / len(vals) if vals else 0.12
        capture = 0.0 if ev is None else float(getattr(ev, "spread_capture_bps", 0.0) or 0.0)
        markout = 0.0 if ev is None else float(getattr(ev, "expected_markout_bps", 0.0) or 0.0)
        fees = 0.5 if ev is None else float(getattr(ev, "fees_bps", 0.5) or 0.5)
        completion = 0.0 if ev is None else float(getattr(ev, "completion_value", 0.0) or 0.0)
        inventory = 0.0 if ev is None else float(getattr(ev, "inventory_cost", 0.0) or 0.0)
        dust = outputs.get("dust_probability")
        if dust is None:
            dust = 0.0 if ev is None else float(getattr(ev, "dust_prob", 0.0) or 0.0)
        latency = 0.0 if ev is None else float(getattr(ev, "latency_cost", 0.0) or 0.0)
        spec = 0.0
        try:
            spec = float(getattr(self._mem(bid), "specialization_score", 0.0) or 0.0)
        except Exception:
            spec = 0.0
        learned_spec = 0.5 * (1.0 + math.tanh(maker_pnl / max(self.adaptive_pnl_scale, 1e-6)))
        spec = (1.0 - 0.35 * conf) * spec + (0.35 * conf) * learned_spec
        last_real = (getattr(self, "_realization_last", {}) or {}).get(bid)
        exit_u = 0.0
        if last_real is not None:
            try:
                exit_u = float(getattr(last_real, "exit_urgency", 0.0) or 0.0)
            except (TypeError, ValueError):
                exit_u = 0.0
        try:
            inv_ratio = float(outputs.get("inventory_util") or 0.0)
        except (TypeError, ValueError):
            inv_ratio = 0.0
        stats = self._adaptive_book(bid)
        buy_fill = None
        sell_fill = None
        bq = sum(int(x) for x in stats["buy_quotes"])
        sq = sum(int(x) for x in stats["sell_quotes"])
        if bq >= int(self.adaptive_fill_min_samples):
            buy_fill = sum(int(x) for x in stats["buy_fills"]) / max(bq, 1)
        if sq >= int(self.adaptive_fill_min_samples):
            sell_fill = sum(int(x) for x in stats["sell_fills"]) / max(sq, 1)
        learned_markout = math.tanh(maker_pnl / max(self.adaptive_pnl_scale, 1e-6)) * 8.0
        return EvSnapshot(
            actionable_p=float(p),
            spread_capture_bps=capture,
            markout_bps=markout,
            fees_bps=fees,
            completion_value=completion,
            inventory_cost=inventory,
            dust_prob=float(dust or 0.0),
            latency_cost=latency,
            learned_fill=float(fill_rate) if conf > 0.0 else None,
            learned_markout_bps=learned_markout if conf > 0.0 else None,
            buy_fill=buy_fill,
            sell_fill=sell_fill,
            confidence=float(conf),
            specialization=spec,
            exit_urgency=exit_u,
            inventory_ratio=inv_ratio,
        )

    def _adaptive_regime_overlay(
        self,
        book_id: int,
        regime_params: RegimeParamSet,
    ) -> RegimeParamSet:
        phase = self._adaptive_phase()
        base_quote = {
            "spread_offset": float(regime_params.spread_offset),
            "size_mult": float(regime_params.size_mult),
            "buy_bias": float(regime_params.buy_bias),
            "sell_bias": float(regime_params.sell_bias),
        }
        if phase in {"DISABLED", "OBSERVE"}:
            self._adaptive_last_exec_diag[int(book_id)] = {
                "phase": phase,
                "base_quote": base_quote,
                "adaptive_quote": dict(base_quote),
                "base_ev": None,
                "adaptive_ev": None,
                "spread_delta": 0.0,
                "fill_hazard_delta": 0.0,
                "markout_delta": 0.0,
                "reason": "HOLD",
                "confidence": 0.0,
                "exit_urgency_scale": 1.0,
            }
            return regime_params

        snap = self._adaptive_ev_snapshot(int(book_id))
        max_tighten = float(self.adaptive_max_tighten)
        max_widen = float(self.adaptive_max_widen)
        max_size_cut = float(self.adaptive_max_size_cut)
        max_exit_boost = float(self.adaptive_max_exit_boost)
        if phase == "BOOTSTRAP":
            max_tighten *= 0.50
            max_widen *= 0.60
            max_size_cut *= 0.60
            max_exit_boost *= 0.60
        elif phase == "DRIFT":
            snap = replace(
                snap,
                confidence=float(snap.confidence) * float(self.adaptive_drift_trust_scale),
            )
            max_tighten = 0.0
            max_widen = max(max_widen, float(self.adaptive_drift_min_widen))
            max_exit_boost = max(max_exit_boost, float(self.adaptive_drift_exit_boost))
        decision = choose_overlay(
            snap,
            phase=phase,  # type: ignore[arg-type]
            max_tighten=max_tighten,
            max_widen=max_widen,
            max_size_cut=max_size_cut,
            max_exit_boost=max_exit_boost,
        )
        size_scale = min(1.0, float(decision.proposal.size_scale))
        base_spread = float(regime_params.spread_offset)
        spread_scale = self._adaptive_clamp(
            float(decision.proposal.spread_scale),
            1.0 - float(self.adaptive_max_tighten),
            1.0 + float(self.adaptive_max_widen),
        )
        min_side = float(self.adaptive_min_side_scale)
        buy_scale = self._adaptive_clamp(
            float(decision.proposal.buy_bias_scale), min_side, 2.0
        )
        sell_scale = self._adaptive_clamp(
            float(decision.proposal.sell_bias_scale), min_side, 2.0
        )
        exit_scale = self._adaptive_clamp(
            float(decision.proposal.exit_urgency_scale),
            1.0,
            1.0 + float(self.adaptive_max_exit_boost),
        )
        if phase == "DRIFT":
            spread_scale, size_scale, exit_scale = apply_drift_defensive_floors(
                spread_scale=spread_scale,
                size_scale=size_scale,
                exit_urgency_scale=exit_scale,
                min_widen=float(self.adaptive_drift_min_widen),
                max_widen=float(self.adaptive_max_widen),
                max_size=float(self.adaptive_drift_size_scale),
                min_exit_boost=float(self.adaptive_drift_exit_boost),
                max_exit_boost=float(self.adaptive_max_exit_boost),
            )
        adapted = replace(
            regime_params,
            spread_offset=max(0.05, base_spread * spread_scale),
            size_mult=max(0.0, min(float(regime_params.size_mult), float(regime_params.size_mult) * size_scale)),
            buy_bias=max(0.25, min(2.0, float(regime_params.buy_bias) * buy_scale)),
            sell_bias=max(0.25, min(2.0, float(regime_params.sell_bias) * sell_scale)),
        )
        adaptive_quote = {
            "spread_offset": float(adapted.spread_offset),
            "size_mult": float(adapted.size_mult),
            "buy_bias": float(adapted.buy_bias),
            "sell_bias": float(adapted.sell_bias),
        }
        log = {
            "phase": phase,
            "base_quote": base_quote,
            "adaptive_quote": adaptive_quote,
            "base_ev": decision.base_ev,
            "adaptive_ev": decision.adaptive_ev,
            "spread_delta": decision.spread_delta,
            "fill_hazard_delta": decision.fill_hazard_delta,
            "markout_delta": decision.markout_delta,
            "reason": decision.reason,
            "confidence": decision.confidence,
            "exit_urgency_scale": exit_scale,
            **decision.as_log(),
        }
        self._adaptive_last_exec_diag[int(book_id)] = log
        if self.debug_enabled and hasattr(self, "_book_record"):
            record = self._book_record(int(book_id))
            record["adaptive_phase"] = phase
            record["adaptive_reason"] = decision.reason
            record["adaptive_base_ev"] = decision.base_ev
            record["adaptive_ev"] = decision.adaptive_ev
            record["adaptive_spread_delta"] = decision.spread_delta
        self._adaptive_emit("ADAPTIVE_EV", force=decision.accepted, book=int(book_id), **log)
        return adapted

    def _adaptive_hjb_overlay_bounds(self) -> HjbOverlayBounds:
        return HjbOverlayBounds(
            mix=float(self.adaptive_hjb_overlay_mix),
            max_center_frac=float(self.adaptive_hjb_max_center_frac),
            max_spread_widen=float(self.adaptive_hjb_overlay_max_widen),
            max_spread_tighten=float(self.adaptive_hjb_overlay_max_tighten),
            max_side_delta=float(self.adaptive_hjb_overlay_max_side),
            max_size_cut=float(self.adaptive_hjb_overlay_max_size_cut),
            max_exit_boost=float(self.adaptive_hjb_overlay_max_exit),
        )

    def _adaptive_hjb_config(self) -> HjbConfig:
        return HjbConfig(
            gamma=float(self.adaptive_hjb_gamma),
            gamma_min=float(self.adaptive_hjb_gamma_min),
            gamma_max=float(self.adaptive_hjb_gamma_max),
            kappa=float(self.adaptive_hjb_kappa),
            horizon=float(self.adaptive_hjb_horizon),
            alpha_shift=float(self.adaptive_hjb_alpha_shift),
            vol_floor=float(self.adaptive_hjb_vol_floor),
            latency_weight=float(self.adaptive_hjb_latency_weight),
            adverse_weight=float(self.adaptive_hjb_adverse_weight),
        )

    def _adaptive_hjb_shadow(
        self,
        *,
        state,
        book_id: int,
        book,
        profile: BookProfile,
        prediction: DirectionForecast,
        inventory: InventorySnapshot,
        base_params: RegimeParamSet,
        adapted_params: RegimeParamSet,
        size: float,
        edge_bias: float,
        overlay_log: dict[str, Any] | None = None,
        emit: bool = True,
    ) -> dict[str, Any] | None:
        """Score HJB vs Base. Never submit raw HJB prices."""
        if not self.adaptive_hjb_shadow_enabled and not self.adaptive_hjb_overlay_enabled:
            return None
        if self.adaptive_hjb_policy_enabled:
            return None
        try:
            if not getattr(book, "bids", None) or not getattr(book, "asks", None):
                return None
            best_bid = float(book.bids[0].price)
            best_ask = float(book.asks[0].price)
            spread = best_ask - best_bid
            mid = 0.5 * (best_bid + best_ask)
            if mid <= 0.0 or spread <= 0.0:
                return None
            cfg_state = getattr(state, "config", None)
            try:
                price_dec = int(getattr(cfg_state, "priceDecimals", 8) or 8)
            except (TypeError, ValueError, AttributeError):
                price_dec = 8
            alpha = float(getattr(prediction, "score", 0.0) or 0.0)
            inv_ratio = float(getattr(inventory, "inventory_ratio", 0.0) or 0.0)
            base_prices = self.skewed_quote_prices(
                best_bid, best_ask, alpha, inv_ratio, base_params, price_dec, edge_bias=edge_bias
            )
            adaptive_prices = self.skewed_quote_prices(
                best_bid, best_ask, alpha, inv_ratio, adapted_params, price_dec, edge_bias=edge_bias
            )
            if not base_prices or not adaptive_prices:
                return None

            micro_sig = float(self.microprice_signal(book) or 0.0)
            microprice = mid + micro_sig * spread
            try:
                util = float(self._inventory_util(inventory))
            except Exception:
                util = min(1.0, abs(inv_ratio))
            net = float(getattr(inventory, "net_base", 0.0) or 0.0)
            signed_inv = util if net >= 0.0 else -util

            outputs = self._adaptive_base_outputs(int(book_id))
            buy_h = outputs.get("fill_hazard_any_buy")
            sell_h = outputs.get("fill_hazard_any_sell")
            last = (getattr(self, "_execution_last", {}) or {}).get(int(book_id), {}) or {}
            act_buy = self._adaptive_pred_field(last.get("buy"), "actionable_fill")
            act_sell = self._adaptive_pred_field(last.get("sell"), "actionable_fill")
            markout = outputs.get("markout_estimate")
            if markout is None:
                ev = (getattr(self, "_score_ev_last", {}) or {}).get(int(book_id))
                markout = None if ev is None else getattr(ev, "expected_markout_bps", 0.0)
            snap = (getattr(self, "_quote_submit_snapshot", {}) or {}).get(int(book_id), {}) or {}
            ofi = outputs.get("ofi")
            if ofi is None and snap.get("ofi_supported"):
                ofi = snap.get("ofi_fast")
            latency_ms = outputs.get("chosen_ttl")
            if latency_ms is None:
                latency_ms = snap.get("chosen_ttl")
            regime = str(getattr(self, "_market_regime", "") or "")
            parked = int(book_id) in (getattr(self, "_research_parked_dust", {}) or {})
            toxicity = 1.0 if regime.upper() in {"TOXIC"} or parked or bool(snap.get("toxic")) else 0.0
            try:
                markout_f = float(markout or 0.0)
            except (TypeError, ValueError):
                markout_f = 0.0
            toxicity = max(toxicity, self._adaptive_clamp(-markout_f / 8.0, 0.0, 1.0))

            drawdown = 0.0
            unreal = getattr(inventory, "unrealized_bps", None)
            try:
                if unreal is not None and float(unreal) < 0.0:
                    drawdown = self._adaptive_clamp(abs(float(unreal)) / 40.0, 0.0, 1.0)
            except (TypeError, ValueError):
                drawdown = 0.0
            pnl = float(self._adaptive_global.get("maker_pnl_short_ewma", 0.0) or 0.0)
            if pnl < 0.0:
                drawdown = max(
                    drawdown,
                    self._adaptive_clamp(-pnl / max(self.adaptive_pnl_scale, 1e-6), 0.0, 1.0),
                )

            try:
                sigma = float(getattr(profile, "volatility", 0.0) or 0.0)
            except (TypeError, ValueError):
                sigma = 0.0
            hjb_cfg = self._adaptive_hjb_config()
            hjb_state = HjbState(
                mid=mid,
                microprice=microprice,
                spread=spread,
                alpha=alpha,
                inventory=signed_inv,
                sigma=sigma,
                fill_hazard_buy=float(buy_h) if buy_h is not None else 0.0,
                fill_hazard_sell=float(sell_h) if sell_h is not None else 0.0,
                actionable_buy=float(act_buy) if act_buy is not None else 0.0,
                actionable_sell=float(act_sell) if act_sell is not None else 0.0,
                ofi=float(ofi or 0.0),
                markout_bps=markout_f,
                toxicity=float(toxicity),
                latency_ms=float(latency_ms or 0.0),
                drawdown=float(drawdown),
                phase=self._adaptive_phase(),  # type: ignore[arg-type]
                base_size=max(0.0, float(size)),
                regime=regime,
            )
            quote = compute_hjb_quote(hjb_state, hjb_cfg)
            if quote is None:
                return None
            base_ev = shadow_quote_ev(
                mid=mid,
                spread=spread,
                bid=float(base_prices[0]),
                ask=float(base_prices[1]),
                fill_buy=hjb_state.fill_hazard_buy,
                fill_sell=hjb_state.fill_hazard_sell,
                markout_bps=markout_f,
                fees_bps=hjb_cfg.fees_bps,
            )
            payload = {
                "book": int(book_id),
                "base_bid": float(base_prices[0]),
                "base_ask": float(base_prices[1]),
                "adaptive_bid": float(adaptive_prices[0]),
                "adaptive_ask": float(adaptive_prices[1]),
                "hjb_reservation": quote.reservation,
                "hjb_bid": quote.bid,
                "hjb_ask": quote.ask,
                "inventory": quote.inventory,
                "gamma": quote.gamma,
                "sigma": quote.sigma,
                "alpha": quote.alpha,
                "fill_hazard_buy": quote.fill_hazard_buy,
                "fill_hazard_sell": quote.fill_hazard_sell,
                "markout": quote.markout_bps,
                "latency_ms": float(latency_ms or 0.0),
                "latency_penalty": quote.latency_penalty,
                "adverse_penalty": quote.adverse_penalty,
                "inventory_term": quote.inventory_term,
                "market_regime": regime,
                "score_regime": str(getattr(self, "_score_regime", "") or ""),
                "estimated_base_ev": base_ev,
                "estimated_hjb_ev": quote.estimated_ev,
                "policy_activated": 0,
                "_quote": quote,
                "_mid": mid,
                "_spread": spread,
                **quote.as_log(),
                **(overlay_log or {}),
            }
            self._adaptive_last_hjb[int(book_id)] = payload
            if emit and self.adaptive_hjb_shadow_enabled:
                public = {k: v for k, v in payload.items() if not str(k).startswith("_")}
                self._adaptive_emit("ADAPTIVE_HJB_SHADOW", force=True, **public)
            return payload
        except Exception:
            return None

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
        quote_size = float(size)
        quote_edge = float(edge_bias)
        overlay_log: dict[str, Any] = {}
        if self.adaptive_enabled:
            hjb_payload = self._adaptive_hjb_shadow(
                state=state,
                book_id=book_id,
                book=book,
                profile=profile,
                prediction=prediction,
                inventory=inventory,
                base_params=regime_params,
                adapted_params=adapted_params,
                size=quote_size,
                edge_bias=quote_edge,
                emit=False,
            )
            if self.adaptive_hjb_overlay_enabled and hjb_payload:
                decision = propose_hjb_overlay(
                    hjb_payload.get("_quote"),
                    base_bid=float(hjb_payload.get("base_bid", 0.0) or 0.0),
                    base_ask=float(hjb_payload.get("base_ask", 0.0) or 0.0),
                    mid=float(hjb_payload.get("_mid", 0.0) or 0.0),
                    spread=float(hjb_payload.get("_spread", 0.0) or 0.0),
                    base_ev=float(hjb_payload.get("estimated_base_ev", 0.0) or 0.0),
                    phase=self._adaptive_phase(),  # type: ignore[arg-type]
                    bounds=self._adaptive_hjb_overlay_bounds(),
                    skew_strength=float(getattr(adapted_params, "skew_strength", 0.20) or 0.20),
                    base_size=quote_size,
                )
                overlay_log = decision.as_log()
                if decision.applied:
                    adapted_params = replace(
                        adapted_params,
                        spread_offset=max(
                            0.05, float(adapted_params.spread_offset) * decision.spread_scale
                        ),
                        size_mult=max(
                            0.0,
                            min(
                                float(adapted_params.size_mult),
                                float(adapted_params.size_mult) * decision.size_scale,
                            ),
                        ),
                        buy_bias=max(
                            0.25,
                            min(2.0, float(adapted_params.buy_bias) * decision.buy_bias_scale),
                        ),
                        sell_bias=max(
                            0.25,
                            min(2.0, float(adapted_params.sell_bias) * decision.sell_bias_scale),
                        ),
                    )
                    quote_size = min(quote_size, quote_size * decision.size_scale)
                    quote_edge = quote_edge + float(decision.edge_bias_delta)
                hjb_payload.update(overlay_log)
                hjb_payload["exit_urgency_scale"] = float(decision.exit_urgency_scale)
                self._adaptive_last_hjb[int(book_id)] = hjb_payload
                if self.adaptive_hjb_shadow_enabled:
                    public = {
                        k: v for k, v in hjb_payload.items() if not str(k).startswith("_")
                    }
                    self._adaptive_emit("ADAPTIVE_HJB_SHADOW", force=True, **public)
            elif hjb_payload is not None and self.adaptive_hjb_shadow_enabled:
                public = {k: v for k, v in hjb_payload.items() if not str(k).startswith("_")}
                self._adaptive_emit("ADAPTIVE_HJB_SHADOW", force=True, **public)
        placed = super()._place_skewed_quotes(
            response,
            state,
            book_id,
            book,
            profile,
            prediction,
            inventory,
            adapted_params,
            quote_size,
            quote_edge,
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
                base=self._adaptive_base_outputs(bid),
                inventory_band=getattr(inventory, "band", None),
                inventory_base=getattr(inventory, "net_base", None),
                inventory_ticks=getattr(inventory, "position_ticks", None),
            )
        return placed

    def _evaluate_realization(
        self,
        book_id: int,
        book,
        inventory,
        state,
        regime_params: RegimeParamSet,
    ):
        decision = super()._evaluate_realization(
            book_id, book, inventory, state, regime_params
        )
        if not self.adaptive_enabled or self._adaptive_phase() in {"DISABLED", "OBSERVE"}:
            return decision
        diag = self._adaptive_last_exec_diag.get(int(book_id)) or {}
        try:
            scale = float(diag.get("exit_urgency_scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            scale = 1.0
        if self._adaptive_phase() == "DRIFT":
            scale = max(scale, 1.0 + float(self.adaptive_drift_exit_boost))
        hjb = self._adaptive_last_hjb.get(int(book_id)) or {}
        try:
            hjb_exit = float(hjb.get("exit_urgency_scale", 1.0) or 1.0)
        except (TypeError, ValueError):
            hjb_exit = 1.0
        scale = max(scale, hjb_exit)
        urgency, action = apply_earlier_realization(
            base_urgency=float(getattr(decision, "exit_urgency", 0.0) or 0.0),
            scale=scale,
            base_action=str(getattr(decision, "selected_action", "") or ""),
            taker_allowed=bool(getattr(decision, "taker_allowed", False)),
            max_boost=float(self.adaptive_max_exit_boost),
        )
        if (
            urgency <= float(decision.exit_urgency) + 1e-12
            and action == str(decision.selected_action)
        ):
            return decision
        return replace(
            decision,
            exit_urgency=urgency,
            action=action,
            selected_action=action,
            trigger=(
                "ADAPTIVE_EARLIER_EXIT"
                if action != str(decision.selected_action)
                else decision.trigger
            ),
        )

    def _global_book_rank(self, expected_alpha: float, mem: BookMemory) -> float:
        base_rank = float(super()._global_book_rank(expected_alpha, mem))
        if not self.adaptive_enabled or self._adaptive_phase() == "OBSERVE":
            return base_rank

        book_id = getattr(mem, "_research_book_id", None)
        if book_id is None:
            return base_rank
        bid = int(book_id)

        # Score-EV already includes Kappa completion value. Do not add a
        # second one-away bonus on top of Base.
        confidence, _fill_rate, maker_pnl = self._adaptive_execution_quality(bid)
        if confidence <= 0.0:
            return base_rank

        pnl_quality = math.tanh(maker_pnl / self.adaptive_pnl_scale)
        quality = pnl_quality
        adjust = (
            self.adaptive_rank_max_adjust
            * confidence
            * self._adaptive_clamp(quality, -1.0, 1.0)
        )
        spec = 0.0
        try:
            spec = float(getattr(mem, "specialization_score", 0.0) or 0.0)
        except (TypeError, ValueError):
            spec = 0.0
        if spec < 0.20:
            adjust -= (
                self.adaptive_rank_max_adjust
                * 0.50
                * confidence
                * ((0.20 - spec) / 0.20)
            )
        if self._adaptive_phase() == "DRIFT":
            adjust = min(adjust, 0.0)
        return base_rank + adjust

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
        parked = getattr(self, "_research_parked_dust", {}) or {}
        cap = max(1, int(self.research_dust_compact_books_per_tick))
        old_cap = cap
        try:
            self.research_dust_compact_books_per_tick = max(cap, len(parked))
            universe = set(super()._select_dust_compaction_books(state))
        finally:
            self.research_dust_compact_books_per_tick = old_cap

        rows: list[tuple[float, float, int, int]] = []
        for bid in universe:
            info = parked.get(int(bid), {})
            qty = float(info.get("net_base", 0.0) or 0.0)
            if not self._dust_compaction_safe_for_any_fill(qty):
                continue
            stats = self._adaptive_book(int(bid))
            last_tick = int(stats.get("dust_last_attempt_tick", -1))
            cooldown = self._adaptive_dust_cooldown(stats)
            if last_tick >= 0 and tick - last_tick < cooldown:
                continue
            first_tick = int(info.get("first_tick", tick))
            age = max(0, tick - first_tick)
            posterior = self._adaptive_dust_fill_posterior(stats)
            rows.append((posterior, abs(qty), age, int(bid)))

        rows.sort(reverse=True)
        selected = {bid for _score, _qty, _age, bid in rows[:cap]}

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
