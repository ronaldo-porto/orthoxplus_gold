# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""BaseStrategy Phase 2 Step 2: frozen execution-model wiring and isolation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")
HAZARD = (ROOT / "agents" / "strategy" / "execution_hazard.py").read_text(encoding="utf-8")
LIFECYCLE = (ROOT / "agents" / "strategy" / "execution_lifecycle.py").read_text(encoding="utf-8")


def test_base_inlines_execution_model_and_does_not_import_research():
    assert "from execution_lifecycle import" not in BASE
    assert "from execution_hazard import" not in BASE
    assert "import execution_lifecycle" not in BASE
    assert "import execution_hazard" not in BASE
    assert "class QuoteLifecycleStore" in BASE
    assert "class QuoteRecord" in BASE
    assert "def classify_fill" in BASE
    assert "class FillHazardModel" in BASE
    assert "class HazardFeatures" in BASE
    assert "class HazardPrediction" in BASE
    assert "research_quote_lifecycle" not in BASE
    assert "research_fill_hazard" not in BASE
    assert "from Strategy1_Research import" not in BASE
    assert "import Strategy1_Research" not in BASE


def test_deploy_policy_version_unchanged():
    assert "DEPLOY_POLICY_VERSION = 'base_v4_4_champion'" in BASE
    assert "EXECUTION_POLICY_VERSION = 'execution_v1_frozen'" in BASE


def test_base_wires_hazard_with_legacy_fallback():
    assert "estimate_fill_probability = _bsimpl_3_Strategy1_Research_estimate_fill_probability" in BASE
    assert "_bsimpl_1_Strategy1_estimate_fill_probability" in BASE
    assert "apply_policy_fill" in BASE
    assert "INSUFFICIENT_SAMPLES" in BASE
    assert "UNSUPPORTED_FEATURES" in BASE
    assert "_execution_observe_cap" in BASE


def test_fill_class_and_runtime_min_order_are_wired():
    assert "classify_fill(" in BASE
    assert "_research_exchange_min_order_size" in BASE
    assert "FULL" in LIFECYCLE
    assert "ACTIONABLE_PARTIAL" in LIFECYCLE
    assert "DUST_PARTIAL" in LIFECYCLE
    assert "FLAT" in LIFECYCLE
    assert "CROSS_DUST" in LIFECYCLE
    assert "never a hardcoded 0.25" in LIFECYCLE


def test_production_telemetry_fields_are_emitted():
    for field in (
        "predicted_any_fill_probability",
        "predicted_actionable_fill_probability",
        "predicted_dust_probability",
        "actual_fill_class",
        "model_confidence",
        "fallback_reason",
    ):
        assert field in BASE
    assert "EXECUTION" in BASE


def test_feature_logit_adaptation_is_frozen_off():
    assert "FROZEN_FEATURE_LOGIT_WEIGHT = 0.0" in HAZARD
    assert "feature_logit_weight: float = FROZEN_FEATURE_LOGIT_WEIGHT" in HAZARD


def test_adaptive_agent_source_unchanged_this_step():
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "execution_hazard" not in ADAPTIVE
    assert "execution_lifecycle" not in ADAPTIVE
    assert "classify_fill" not in ADAPTIVE
