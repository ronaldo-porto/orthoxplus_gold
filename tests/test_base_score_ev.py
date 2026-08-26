# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""BaseStrategy Phase 2 Step 3: Score-EV and Kappa scheduler wiring."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")
SCORE_EV = (ROOT / "agents" / "strategy" / "score_ev.py").read_text(encoding="utf-8")


def test_base_inlines_score_ev_and_does_not_import_research():
    assert "from score_ev import" not in BASE
    assert "import score_ev" not in BASE
    assert "def compute_score_ev" in BASE
    assert "def required_observation_count" in BASE
    assert "def select_rank" in BASE
    assert "def admit_scheduler_candidate" in BASE
    assert "def round_trip_velocity" in BASE
    assert "def score_velocity_priority" in BASE
    assert "research_score_ev" not in BASE
    assert "from Strategy1_Research import" not in BASE
    assert "import Strategy1_Research" not in BASE


def test_deploy_policy_version_unchanged():
    assert "DEPLOY_POLICY_VERSION = 'base_v4_4_champion'" in BASE
    assert "SCORE_EV_POLICY_VERSION = 'score_ev_v3'" in BASE
    assert "REALIZATION_POLICY_VERSION = 'realization_v1'" in BASE


def test_phase1_coefficients_are_frozen():
    assert "one_away_weight: float = 0.18" in SCORE_EV
    assert "two_away_weight: float = 0.06" in SCORE_EV
    assert "new_book_weight: float = 0.0" in SCORE_EV
    assert "dust_target: float = 0.15" in SCORE_EV
    assert "dust_weight: float = 0.25" in SCORE_EV
    assert "inventory_weight: float = 0.08" in SCORE_EV
    assert "latency_weight: float = 0.04" in SCORE_EV
    assert "edge_scale_bps: float = 8.0" in SCORE_EV
    assert "TradingEV" in SCORE_EV
    assert "CompletionValue" in SCORE_EV
    assert "ActivityDeficitValue" in SCORE_EV
    assert "AdverseSelectionRisk" in SCORE_EV


def test_base_tracks_required_and_remaining_observations():
    assert "_required_observation_count" in BASE
    assert "_observations_remaining" in BASE
    assert "_research_realized_observations_by_book" in BASE
    assert "required_observation_count=" in BASE
    assert "observations_remaining=" in BASE
    assert "_is_kappa_completion_candidate" in BASE
    assert "skipped_score_ev" in BASE


def test_hard_gates_and_score_ev_telemetry_are_wired():
    assert "reject_reason" in BASE
    assert "'SCORE_EV'" in BASE
    assert "trading_ev=" in BASE
    assert "completion_value=" in BASE
    assert "dust_cost=" in BASE
    assert "inventory_cost=" in BASE
    assert "NEGATIVE_EV" in (ROOT / "agents" / "strategy" / "score_ev.py").read_text(encoding="utf-8")
    assert "TOXIC" in SCORE_EV
    assert "INVENTORY_BLOCKED" in SCORE_EV


def test_adaptive_does_not_import_score_ev_module():
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "from score_ev import" not in ADAPTIVE
    assert "import score_ev" not in ADAPTIVE
    assert "SCORE_EV_POLICY_VERSION" not in ADAPTIVE
