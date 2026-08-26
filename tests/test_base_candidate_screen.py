# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""BaseStrategy Phase 2 Step 2.4: candidate screen and full-universe fallback."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")
PREDICT = """    def _bsimpl_1_Strategy1_predict_direction(self, book_id: int, book: Book, timestamp: int) -> DirectionForecast:"""


def test_base_inlines_screen_and_does_not_import_research():
    assert "from candidate_screen import" not in BASE
    assert "import candidate_screen" not in BASE
    assert "research_candidate_screen" not in BASE
    assert "from Strategy1_Research import" not in BASE
    assert "import Strategy1_Research" not in BASE
    assert "def select_fast_candidates" in BASE
    assert "class FeatureCache" in BASE
    assert "def cheap_book_score" in BASE
    assert "fast_candidate_screen_enabled" in BASE
    assert "_full_predict_fallback" in BASE


def test_deploy_policy_version():
    assert "DEPLOY_POLICY_VERSION = 'base_v4_4_champion'" in BASE
    assert "SCREEN_POLICY_VERSION = 'candidate_screen_v1'" in BASE


def test_forced_sets_always_include_inventory_kappa_risk():
    assert "Inventory, Kappa completion (1–2 remaining), and risk books always survive" in BASE
    assert "forced_inventory" in BASE
    assert "forced_kappa" in BASE
    assert "is_hard_risk" in BASE
    assert "has_inventory" in BASE


def test_full_predict_fallback_is_preserved():
    assert "def _bsimpl_0_DetailedTemplateAgent__predict_all_books" in BASE
    assert "for book_id, book in state.books.items():" in BASE
    assert "predictions[book_id] = self.predict_direction(book_id, book, state.timestamp)" in BASE
    assert "_full_predict_fallback" in BASE
    assert "fast_candidate_screen_enabled" in BASE
    assert "screen_fallback" in BASE
    assert "_predict_all_books = _bsimpl_3_Strategy1_Research__predict_all_books" in BASE


def test_strategy1_signal_engine_is_preserved():
    assert "def _bsimpl_1_Strategy1_predict_direction" in BASE
    assert "self.microprice_signal(book)" in BASE
    assert "micro_vel" in BASE
    assert "w_micro_vel" in BASE
    assert "_compute_l2_l5_imbalance" in BASE
    assert "w_deep" in BASE
    assert "_normalize_momentum" in BASE
    assert "_compute_trade_t" in BASE
    assert "_trade_persistence" in BASE
    assert "build_book_profile" in BASE
    assert "specialization_score" in BASE
    assert "DirectionForecast" in BASE


def test_timing_profile_fields_exist():
    assert "screen_all_books_ms=" in BASE
    assert "full_predict_ms=" in BASE
    assert "select_books_ms=" in BASE
    assert "build_orders_ms=" in BASE
    assert "total_response_ms=" in BASE
    assert "REQUIRED_TIMING_KEYS" in BASE


def test_adaptive_agent_source_unchanged_this_step():
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "select_fast_candidates" not in ADAPTIVE
    assert "candidate_screen" not in ADAPTIVE
    assert PREDICT not in ADAPTIVE
