# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""BaseStrategy Phase 2 Step 1: regime V2 wiring and isolation checks."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")


def test_base_imports_production_regime_module_not_research():
    assert "from regime_v2 import" in BASE
    assert "research_regime_v2" not in BASE
    assert "from Strategy1_Research import" not in BASE
    assert "import Strategy1_Research" not in BASE


def test_base_classifier_does_not_call_parent_5bps_path():
    assert "UNEXPOSED_BY_PARENT" not in BASE
    assert "_regime_v2_snapshot" in BASE
    assert "classify_regime_v2(" in BASE
    assert "REGIME_POLICY_VERSION = 'regime_v2'" in BASE


def test_score_regime_states_are_wired():
    assert "_score_regime" in BASE
    assert "COVERAGE_PRESSURE" in (ROOT / "agents" / "strategy" / "regime_v2.py").read_text(encoding="utf-8")
    assert "COMPLETION_PRESSURE" in (ROOT / "agents" / "strategy" / "regime_v2.py").read_text(encoding="utf-8")


def test_adaptive_agent_source_unchanged_this_step():
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "classify_regime_v2" not in ADAPTIVE
    assert "regime_v2" not in ADAPTIVE
