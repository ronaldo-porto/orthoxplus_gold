# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""BaseStrategy Phase 2 Step 1: regime V2 wiring and isolation checks."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")


def test_base_inlines_regime_v2_and_does_not_import_research():
    assert "from regime_v2 import" not in BASE
    assert "import regime_v2" not in BASE
    assert "class DebounceState" in BASE
    assert "class RegimeV2Thresholds" in BASE
    assert "def classify_regime_v2" in BASE
    assert "stressed_ratio_enter: float = 0.35" in BASE
    assert "research_regime_v2" not in BASE
    assert "from Strategy1_Research import" not in BASE
    assert "import Strategy1_Research" not in BASE


def test_base_does_not_bootstrap_sys_path_for_sibling_modules():
    """Standalone Base must not rely on agents/strategy being on sys.path."""
    assert "sys.path.insert" not in BASE
    assert "_agent_dir" not in BASE


def test_base_classifier_does_not_call_parent_5bps_path():
    assert "UNEXPOSED_BY_PARENT" not in BASE
    assert "_regime_v2_snapshot" in BASE
    assert "classify_regime_v2(" in BASE
    assert "REGIME_POLICY_VERSION = 'regime_v2'" in BASE


def test_score_regime_states_are_wired():
    assert "_score_regime" in BASE
    assert '"COVERAGE"' in BASE
    assert '"COMPLETION"' in BASE
    assert '"BALANCED"' in BASE
    regime = (ROOT / "agents" / "strategy" / "regime_v2.py").read_text(encoding="utf-8")
    assert '"COVERAGE"' in regime
    assert '"COMPLETION"' in regime
    assert '"BALANCED"' in regime
    assert "def score_regime_metrics" in BASE
    assert "def round_trip_velocity" in BASE


def test_adaptive_agent_source_unchanged_this_step():
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "classify_regime_v2" not in ADAPTIVE
    assert "regime_v2" not in ADAPTIVE
