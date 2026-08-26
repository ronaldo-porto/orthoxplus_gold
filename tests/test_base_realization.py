# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""BaseStrategy Phase 2 Step 2.2: realization inlining and isolation."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")


def test_base_inlines_realization_and_does_not_import_research():
    assert "from realization import" not in BASE
    assert "import realization" not in BASE
    assert "research_realization" not in BASE
    assert "from Strategy1_Research import" not in BASE
    assert "import Strategy1_Research" not in BASE
    assert "def evaluate_realization" in BASE
    assert "def exit_urgency" in BASE
    assert "def inventory_should_manage" in BASE
    assert "def selective_taker_allowed" in BASE
    assert "def _manage_realization" in BASE
    assert "SELECTIVE_TAKER_EXIT" in BASE
    assert "PASSIVE_MAKER_EXIT" in BASE


def test_base_execution_flow_keeps_hard_safety_and_caps():
    assert "min_expected_alpha', 0.18)" in BASE
    assert "mm_base_size', 0.25)" in BASE
    assert "max_inventory_base', 1.2)" in BASE
    assert "max_mm_books_per_tick', 4)" in BASE
    assert "mm_force_post_only" in BASE
    assert "NEGATIVE_EV" in BASE
    assert "TOXIC" in BASE
    assert "INVENTORY_BLOCKED" in BASE


def test_adaptive_does_not_import_realization_or_research():
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "from realization import" not in ADAPTIVE
    assert "import realization" not in ADAPTIVE
    assert "research_realization" not in ADAPTIVE
    assert "from Strategy1_Research import" not in ADAPTIVE
