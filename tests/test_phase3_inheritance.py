# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 3 Step 1: AdaptiveAgent inherits the Base champion and adapts outputs."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")


FORBIDDEN_IMPORTS = (
    "Strategy1_Research",
    "Strategy1_Debug",
    "Strategy1",
    "Strategy5",
    "research_regime_v2",
    "research_score_ev",
    "research_candidate_screen",
    "research_fill_hazard",
    "research_realization",
    "research_quote_hysteresis",
    "research_adverse",
    "research_entry_size",
    "score_ev",
    "candidate_screen",
    "realization",
    "quote_hysteresis",
    "adverse",
    "entry_size",
    "regime_v2",
)


def test_mro_is_adaptive_then_base_champion():
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class BaseStrategy(FinanceSimulationAgent)" in BASE
    assert "DEPLOY_POLICY_VERSION = 'base_v4_4_champion'" in BASE
    assert "BASE_CHAMPION = True" in BASE
    assert "ADAPTIVE_INHERIT_BASE = True" in ADAPTIVE


def test_no_research_or_sibling_runtime_imports():
    for mod in FORBIDDEN_IMPORTS:
        assert f"from {mod} import" not in ADAPTIVE
        assert f"import {mod}" not in ADAPTIVE


def test_super_on_every_contract_hook():
    for token in (
        "super().initialize()",
        "self._adaptive_enforce_champion_caps()",
        "super().handle(state)",
        "super().estimate_fill_probability(",
        "super().dynamic_order_size(",
        "super()._place_skewed_quotes(",
        "super()._global_book_rank(",
        "super()._select_dust_compaction_books(state)",
        "super()._completion_observation_count(",
        "super()._evaluate_realization(",
        "super().onTrade(",
        "super()._record_fill_quote(",
        "super()._record_fill_hit(",
    ):
        assert token in ADAPTIVE


def test_does_not_construct_orders_or_rebuild_engines():
    assert "limit_order(" not in ADAPTIVE
    assert "cancel_order(" not in ADAPTIVE
    assert "select_fast_candidates" not in ADAPTIVE
    assert "compute_score_ev(" not in ADAPTIVE
    realization_src = ADAPTIVE.replace("super()._evaluate_realization(", "").replace(
        "def _evaluate_realization(", ""
    )
    assert "evaluate_realization(" not in realization_src
    assert "from research_realization import" not in ADAPTIVE
    assert "dust_escape_allowed" not in ADAPTIVE
    assert "second one-away bonus on top of Base" in ADAPTIVE
    assert "adaptive_hjb_policy_enabled = False" in ADAPTIVE


def test_reads_base_outputs_and_does_not_write_engine_maps():
    for token in (
        "_score_ev_last",
        "_execution_last",
        "_quote_submit_snapshot",
        "_market_regime",
        "_score_regime",
        "ofi_fast",
        "ofi_supported",
        "fallback_reason",
        "_research_parked_dust",
    ):
        assert token in ADAPTIVE
    for assign in (
        "self._score_ev_last =",
        "self._execution_last =",
        "self._quote_submit_snapshot =",
        "self._feature_cache =",
        "self._ofi =",
    ):
        assert assign not in ADAPTIVE


def test_ofi_is_not_rebuilt_from_imbalance():
    outputs = ADAPTIVE.split("def _adaptive_base_outputs")[1].split(
        "def estimate_fill_probability"
    )[0]
    assert '"ofi": snap.get("ofi_fast") if snap.get("ofi_supported") else None' in outputs
    assert '"ofi": snap.get("imbalance")' not in outputs
    assert 'getattr(profile, "imbalance"' not in ADAPTIVE


def test_base_hard_caps_stay_authoritative():
    assert "def _adaptive_enforce_champion_caps(" in ADAPTIVE
    assert "self.min_expected_alpha = max(" in ADAPTIVE
    assert "self.mm_base_size = min(" in ADAPTIVE
    assert "self.max_inventory_base = min(" in ADAPTIVE
    assert "self.max_mm_books_per_tick = min(" in ADAPTIVE
    assert "self.mm_force_post_only = True" in ADAPTIVE
    assert "min_expected_alpha', 0.18" in BASE
    assert "mm_base_size', 0.25" in BASE
    assert "max_inventory_base', 1.2" in BASE
    assert "max_mm_books_per_tick', 4" in BASE
    assert "mm_force_post_only', True" in BASE
