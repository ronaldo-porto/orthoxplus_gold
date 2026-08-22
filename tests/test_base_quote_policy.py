# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""BaseStrategy Phase 2 Step 4: hysteresis, TTL, dust prevention isolation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")
HYST = (ROOT / "agents" / "strategy" / "quote_hysteresis.py").read_text(encoding="utf-8")


def test_base_inlines_hysteresis_and_does_not_import_research():
    assert "from quote_hysteresis import" not in BASE
    assert "import quote_hysteresis" not in BASE
    assert "def should_replace_quote" in BASE
    assert "def choose_ttl_ms" in BASE
    assert "def would_create_dust" in BASE
    assert "def predicted_dust_blocks_increase" in BASE
    assert "research_quote_hysteresis" not in BASE
    assert "from Strategy1_Research import" not in BASE
    assert "import Strategy1_Research" not in BASE


def test_dust_escape_is_not_promoted():
    assert "dust_escape_allowed" not in BASE
    assert "_research_try_dust_escape" not in BASE
    assert "research_enable_dust_escape" not in BASE
    assert "NOT wired" in HYST or "NOT promoted" in HYST or "intentionally NOT" in HYST


def test_deploy_policy_version_unchanged():
    assert "DEPLOY_POLICY_VERSION = 'base_v4_1_1_maker_guard'" in BASE
    assert "QUOTE_POLICY_VERSION = 'quote_hysteresis_ttl_v1'" in BASE


def test_hysteresis_and_bounded_ttl_are_wired():
    assert "should_replace_quote" in BASE
    assert "choose_ttl_ms" in BASE
    assert "ttl_min_ms" in BASE
    assert "ttl_max_ms" in BASE
    assert "adaptive_ttl_enabled" in BASE
    assert "quote_hysteresis_enabled" in BASE
    assert "HARD_SAFETY" in HYST
    assert "clamp_ttl_ms" in HYST


def test_dust_prevention_uses_predicted_risk_before_submit():
    assert "would_create_dust" in BASE
    assert "predicted_dust_blocks_increase" in BASE
    assert "dust_prevent_enabled" in BASE
    assert "_dust_prevent_skip_sides" in BASE
    assert "research_dust_park_enabled" in BASE
    assert "research_dust_compact_enabled" in BASE


def test_production_metrics_are_emitted():
    for field in (
        "cancel_reason",
        "quote_age",
        "chosen_ttl",
        "dust_probability",
        "dust_creation_count",
        "dust_cleanup_attempts",
        "dust_cleanup_successes",
    ):
        assert field in BASE


def test_adaptive_agent_source_unchanged_this_step():
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "quote_hysteresis" not in ADAPTIVE
    assert "dust_escape_allowed" not in ADAPTIVE
    assert "QUOTE_POLICY_VERSION" not in ADAPTIVE
