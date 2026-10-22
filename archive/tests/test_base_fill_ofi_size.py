# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""BaseStrategy Phase 2 Step 2.3: fill-hazard, OFI, markout, size, hysteresis."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")
HAZARD = (ROOT / "agents" / "strategy" / "execution_hazard.py").read_text(encoding="utf-8")
HYST = (ROOT / "agents" / "strategy" / "quote_hysteresis.py").read_text(encoding="utf-8")


def test_base_inlines_ofi_markout_size_and_does_not_import_research():
    assert "from adverse import" not in BASE
    assert "import adverse" not in BASE
    assert "from entry_size import" not in BASE
    assert "import entry_size" not in BASE
    assert "from quote_hysteresis import" not in BASE
    assert "research_adverse" not in BASE
    assert "research_entry_size" not in BASE
    assert "research_quote_hysteresis" not in BASE
    assert "research_fill_hazard" not in BASE
    assert "from Strategy1_Research import" not in BASE
    assert "import Strategy1_Research" not in BASE
    assert "class OfiTracker" in BASE
    assert "def extract_touch" in BASE
    assert "def ofi_increment" in BASE
    assert "def expected_markout_bps" in BASE
    assert "def allowed_entry_size" in BASE
    assert "def ofi_reversed" in BASE


def test_deploy_policy_version():
    assert "DEPLOY_POLICY_VERSION = 'base_v4_4_champion'" in BASE
    assert "EXECUTION_POLICY_VERSION = 'execution_v1_frozen'" in BASE
    assert "QUOTE_POLICY_VERSION = 'quote_hysteresis_ttl_v2'" in BASE
    assert "ADVERSE_POLICY_VERSION = 'ofi_markout_v1'" in BASE
    assert "ENTRY_SIZE_POLICY_VERSION = 'entry_size_v1'" in BASE


def test_frozen_parameters_and_no_online_learning():
    assert "FROZEN_FEATURE_LOGIT_WEIGHT = 0.0" in BASE
    assert "FROZEN_FEATURE_LOGIT_WEIGHT = 0.0" in HAZARD
    assert "FROZEN_MIN_SAMPLES = 12" in BASE
    assert "FROZEN_MARKOUT_MIN_SAMPLES = 4" in BASE
    assert "FROZEN_MARKOUT_PRIOR_STRENGTH = 8.0" in BASE
    assert "FROZEN_HYSTERESIS_MIN_PRICE_TICKS = 2.0" in BASE
    assert "FROZEN_HYSTERESIS_EV_THRESHOLD = 0.06" in BASE
    assert "OFI_FAST_ALPHA = 0.45" in BASE
    assert "mm_base_size', 0.25)" in BASE
    assert "max_inventory_base', 1.2)" in BASE
    assert "min_expected_alpha', 0.18)" in BASE


def test_conservative_fallbacks_are_wired():
    assert "INSUFFICIENT_SAMPLES" in BASE
    assert "UNSUPPORTED_FEATURES" in BASE
    assert "LOW_CONFIDENCE" in BASE
    assert "apply_policy_fill" in BASE
    assert "source=\"UNSUPPORTED\"" in BASE or "source='UNSUPPORTED'" in BASE
    assert "ofi_against=ofi_against" in BASE
    assert "expected_markout_override=expected_override" in BASE
    assert "entry_size_enabled" in BASE
    assert "ADVERSE_BLOCK" in BASE
    assert "schedule_markouts" in BASE
    assert "_evaluate_markouts" in BASE
    assert "_update_ofi_from_state" in BASE


def test_hysteresis_uses_ofi_not_imbalance():
    assert "if ofi_reversed(old_ofi, new_ofi)" in BASE
    assert "if imbalance_reversed(old_imbalance, new_imbalance)" not in BASE
    assert "never treated as OFI" in BASE
    assert "never treated as OFI" in HYST
    assert "min_price_ticks: float = 2.0" in BASE
    assert "ev_improve_threshold: float = 0.06" in BASE
    assert "STALE" in BASE
    assert "adaptive_ttl_enabled" in BASE


def test_hard_safety_and_caps_remain():
    assert "HARD_SAFETY" in BASE
    assert "mm_force_post_only" in BASE
    assert "max_mm_books_per_tick', 4)" in BASE
    assert "_research_try_dust_escape" not in BASE


def test_adaptive_inherits_without_new_imports():
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "from adverse import" not in ADAPTIVE
    assert "from entry_size import" not in ADAPTIVE
    assert "import adverse" not in ADAPTIVE
    assert "import entry_size" not in ADAPTIVE
    assert "research_adverse" not in ADAPTIVE
    assert "research_entry_size" not in ADAPTIVE
