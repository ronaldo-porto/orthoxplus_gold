# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Pin the frozen Base champion contract AdaptiveAgent must consume.

Does not modify AdaptiveAgent. Fails if Phase 3 drifts off the contract.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")
CONTRACT = (ROOT / "agents" / "strategy" / "BASE_CHAMPION_CONTRACT.md").read_text(
    encoding="utf-8"
)


def test_champion_identity_is_frozen():
    assert "DEPLOY_POLICY_VERSION = 'base_v4_4_champion'" in BASE
    assert "BASE_CHAMPION = True" in BASE
    assert "BASE_CHAMPION_FROZEN = True" in BASE
    assert "BASE_CHAMPION_PARENT = 'base_v4_1_1_maker_guard'" in BASE
    assert "SCORE_EV_POLICY_VERSION = 'score_ev_v3'" in BASE
    assert "SCREEN_POLICY_VERSION = 'candidate_screen_v1'" in BASE
    assert "base_v4_4_champion" in CONTRACT


def test_adaptive_still_subclasses_live_base_only():
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "from BaseStrategy import" in ADAPTIVE
    assert "from Strategy1_Research import" not in ADAPTIVE
    for helper in (
        "score_ev",
        "candidate_screen",
        "realization",
        "research_score_ev",
        "research_candidate_screen",
    ):
        assert f"from {helper} import" not in ADAPTIVE


def test_adaptive_calls_super_on_contract_hooks():
    for token in (
        "super().initialize()",
        "super().handle(state)",
        "super().estimate_fill_probability(",
        "super().dynamic_order_size(",
        "super()._place_skewed_quotes(",
        "super()._global_book_rank(",
        "super()._select_dust_compaction_books(state)",
        "super()._completion_observation_count(",
        "super().onTrade(",
    ):
        assert token in ADAPTIVE


def test_adaptive_reads_champion_outputs():
    for token in (
        "_score_ev_last",
        "_execution_last",
        "_quote_submit_snapshot",
        "_market_regime",
        "_score_regime",
        "actionable_fill",
        "expected_markout_bps",
        "completion_value",
        "fallback_reason",
        "ttl_min_ms",
        "ttl_max_ms",
        "_research_parked_dust",
    ):
        assert token in ADAPTIVE


def test_adaptive_does_not_construct_orders_or_rescreen():
    assert "limit_order(" not in ADAPTIVE
    assert "cancel_order(" not in ADAPTIVE
    assert "select_fast_candidates" not in ADAPTIVE
    assert "second one-away bonus on top of Base" in ADAPTIVE
    assert "ADAPTIVE_INHERIT_BASE = True" in ADAPTIVE
    assert "def _adaptive_enforce_champion_caps(" in ADAPTIVE
    assert '"ofi": snap.get("imbalance")' not in ADAPTIVE


def test_hard_caps_remain_in_base_source():
    assert "min_expected_alpha', 0.18" in BASE
    assert "mm_base_size', 0.25" in BASE
    assert "max_inventory_base', 1.2" in BASE
    assert "max_mm_books_per_tick', 4" in BASE
    assert "mm_force_post_only', True" in BASE
