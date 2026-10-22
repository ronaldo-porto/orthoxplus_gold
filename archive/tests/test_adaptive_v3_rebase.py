# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 3 Step 1: AdaptiveAgent rebase onto the BaseStrategy champion."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "run_adaptive_agent_multi.sh").read_text(encoding="utf-8")


def test_inheritance_is_base_strategy_only():
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "from BaseStrategy import" in ADAPTIVE
    assert "from Strategy1_Research import" not in ADAPTIVE
    assert "import Strategy1_Research" not in ADAPTIVE
    assert "from Strategy1 import" not in ADAPTIVE
    assert "from Strategy1_Debug import" not in ADAPTIVE
    for mod in (
        "research_regime_v2",
        "research_quote_lifecycle",
        "research_fill_hazard",
        "research_score_ev",
        "research_quote_hysteresis",
        "research_candidate_screen",
        "regime_v2",
        "execution_hazard",
        "execution_lifecycle",
        "score_ev",
        "quote_hysteresis",
        "adverse",
        "entry_size",
        "candidate_screen",
        "Strategy5",
    ):
        assert f"from {mod} import" not in ADAPTIVE
        assert f"import {mod}" not in ADAPTIVE


def test_version_and_schema():
    assert 'ADAPTIVE_VERSION = "adaptive_v3_hjb_shadow"' in ADAPTIVE
    assert "ADAPTIVE_STATE_SCHEMA = CURRENT_SCHEMA" in ADAPTIVE
    assert "from adaptive_persistence import" in ADAPTIVE
    assert "from adaptive_ev import" in ADAPTIVE
    assert "from adaptive_drift import" in ADAPTIVE
    assert "from adaptive_hjb import" in ADAPTIVE
    assert "DEPLOY_POLICY_VERSION = 'base_v4_4_champion'" in BASE
    assert "adaptive_v3_hjb_shadow" in LAUNCHER
    assert "adaptive_fill_overlay_enabled=0" in LAUNCHER
    assert "adaptive_hjb_shadow_enabled=1" in LAUNCHER
    assert "adaptive_hjb_overlay_enabled=1" in LAUNCHER
    assert "adaptive_hjb_policy_enabled=0" in LAUNCHER


def test_no_direct_order_construction():
    assert "limit_order(" not in ADAPTIVE
    assert "cancel_order(" not in ADAPTIVE
    assert "placed = super()._place_skewed_quotes(" in ADAPTIVE


def test_fill_engine_is_base_by_default():
    assert "adaptive_fill_overlay_enabled" in ADAPTIVE
    assert 'getattr(cfg, "adaptive_fill_overlay_enabled", False)' in ADAPTIVE
    assert "base = super().estimate_fill_probability(" in ADAPTIVE
    assert "if not fallback_reason:" in ADAPTIVE
    assert "return base" in ADAPTIVE


def test_score_ev_one_away_is_not_duplicated():
    assert "one_away = self.adaptive_kappa_one_away_bonus" not in ADAPTIVE
    assert "return base_rank + one_away" not in ADAPTIVE
    assert "base_rank = float(super()._global_book_rank" in ADAPTIVE
    assert "second one-away bonus on top of Base" in ADAPTIVE


def test_phase_controls_score_ev_weights():
    assert "self.score_ev_one_away_weight = 0.0" in ADAPTIVE
    assert "self.score_ev_two_away_weight = 0.0" in ADAPTIVE
    assert "self.score_ev_one_away_weight = one * scale" in ADAPTIVE
    assert "self.score_ev_one_away_weight = one * 0.25" in ADAPTIVE
    assert "self._adaptive_restore_phase_controls" in ADAPTIVE
    assert "super().handle(state)" in ADAPTIVE


def test_dust_selection_uses_base_universe_and_theorem():
    assert "universe = set(super()._select_dust_compaction_books(state))" in ADAPTIVE
    assert "_dust_compaction_safe_for_any_fill" in ADAPTIVE
    assert "dust_escape_allowed" not in ADAPTIVE


def test_consumes_base_engine_outputs():
    for token in (
        "_market_regime",
        "_score_regime",
        "_execution_last",
        "_score_ev_last",
        "_quote_submit_snapshot",
        "actionable_fill",
        "dust_prob",
        "completion_value",
        "expected_markout_bps",
        "ttl_min_ms",
        "ttl_max_ms",
        "chosen_ttl",
        "imbalance",
        "ofi_fast",
        "ofi_supported",
    ):
        assert token in ADAPTIVE


def test_preserved_control_plane():
    assert "def _adaptive_phase(" in ADAPTIVE
    assert "def _adaptive_maybe_detect_drift(" in ADAPTIVE
    assert "def _adaptive_regime_overlay(" in ADAPTIVE
    assert "def _adaptive_load_state(" in ADAPTIVE
    assert "decide_load(" in ADAPTIVE
    assert "adaptive_total_requests = 0" in ADAPTIVE
    assert "def _completion_observation_count(" in ADAPTIVE
    assert "def dynamic_order_size(" in ADAPTIVE
    assert "super().dynamic_order_size(" in ADAPTIVE
    assert "super().onTrade(event, validator)" in ADAPTIVE
    assert "select_fast_candidates" not in ADAPTIVE


def test_ev_overlay_not_low_fill_tighten():
    assert "from adaptive_ev import" in ADAPTIVE
    assert "choose_overlay(" in ADAPTIVE
    assert "tighten_need" not in ADAPTIVE
    assert "ADAPTIVE_EV" in ADAPTIVE
    assert "apply_earlier_realization(" in ADAPTIVE
    assert "super()._evaluate_realization(" in ADAPTIVE
    assert "adaptive_max_exit_boost" in ADAPTIVE
    assert "adaptive_min_side_scale" in ADAPTIVE
    for token in (
        "base_quote",
        "adaptive_quote",
        "base_ev",
        "adaptive_ev",
        "spread_delta",
        "fill_hazard_delta",
        "markout_delta",
        "reason",
        "confidence",
        "exit_urgency_scale",
    ):
        assert token in ADAPTIVE
    assert "size_mult=max(0.0, min(float(regime_params.size_mult)" in ADAPTIVE


def test_drift_recovery_is_bootstrap_not_immediate_normal():
    assert "enter_or_extend_drift(" in ADAPTIVE
    assert "_adaptive_recovery_until_request" in ADAPTIVE
    assert "DRIFT_RECOVER_BOOTSTRAP" in ADAPTIVE or "phase_transition_reason(" in ADAPTIVE
    assert "adaptive_drift_min_windows" in ADAPTIVE
    assert "adaptive_drift_trust_scale" in ADAPTIVE
    assert "max_tighten = 0.0" in ADAPTIVE
    assert "apply_drift_defensive_floors(" in ADAPTIVE
    assert "adaptive_drift_min_widen" in ADAPTIVE
    assert "adaptive_drift_exit_boost" in ADAPTIVE
    assert "volatility=self._adaptive_mean_profile_field" in ADAPTIVE
    assert "inventory_age=self._adaptive_mean_inventory_age()" in ADAPTIVE
    assert "_adaptive_drift_snapshot_quotes" not in ADAPTIVE
    assert "ADAPTIVE_PHASE" in ADAPTIVE
    assert "ADAPTIVE_DRIFT" in ADAPTIVE


def test_phase3_inherits_and_does_not_rebuild_base():
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "ADAPTIVE_INHERIT_BASE = True" in ADAPTIVE
    assert "def _adaptive_enforce_champion_caps(" in ADAPTIVE
    assert "self.min_expected_alpha = max(" in ADAPTIVE
    assert "self.mm_force_post_only = True" in ADAPTIVE
    outputs = ADAPTIVE.split("def _adaptive_base_outputs")[1].split("def estimate_fill_probability")[0]
    assert '"ofi": snap.get("ofi_fast") if snap.get("ofi_supported") else None' in outputs
    assert '"ofi": snap.get("imbalance")' not in outputs
    assert 'getattr(profile, "imbalance"' not in ADAPTIVE
    for assign in (
        "self._score_ev_last =",
        "self._execution_last =",
        "self._quote_submit_snapshot =",
        "self._feature_cache =",
        "self._ofi =",
    ):
        assert assign not in ADAPTIVE


def test_hjb_is_shadow_only():
    assert "from adaptive_hjb import" in ADAPTIVE
    assert "ADAPTIVE_HJB_SHADOW" in ADAPTIVE
    assert "self.adaptive_hjb_policy_enabled = False" in ADAPTIVE
    assert "adaptive_hjb_overlay_enabled" in ADAPTIVE
    assert "propose_hjb_overlay(" in ADAPTIVE
    assert "placed = super()._place_skewed_quotes(" in ADAPTIVE
    assert "adapted_params," in ADAPTIVE
    assert "hjb_bid" in ADAPTIVE
    assert "estimated_base_ev" in ADAPTIVE
    assert "estimated_hjb_ev" in ADAPTIVE
    assert "inventory=signed_inv" in ADAPTIVE
    assert 'sigma=sigma' in ADAPTIVE
    assert "alpha=alpha" in ADAPTIVE
    assert "fill_hazard_buy=" in ADAPTIVE
    assert "markout_bps=markout_f" in ADAPTIVE
    assert "latency_ms=float(latency_ms or 0.0)" in ADAPTIVE
    assert "regime=regime" in ADAPTIVE
    assert "policy_activated" in ADAPTIVE
    assert "from Strategy5 import" not in ADAPTIVE
    assert "import Strategy5" not in ADAPTIVE
    # HJB prices must not be the arguments to the live submit path.
    submit_idx = ADAPTIVE.index("placed = super()._place_skewed_quotes(")
    submit_block = ADAPTIVE[submit_idx : submit_idx + 400]
    assert "adapted_params" in submit_block
    assert "quote.bid" not in submit_block
    assert "hjb_bid" not in submit_block
