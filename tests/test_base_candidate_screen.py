# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 2 Step 5: fast candidate screening is not promoted (PROMISING, not PROVEN)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")
PREDICT = """    def _bsimpl_1_Strategy1_predict_direction(self, book_id: int, book: Book, timestamp: int) -> DirectionForecast:"""


def test_screen_module_is_not_imported_by_base():
    assert "research_candidate_screen" not in BASE
    assert "select_fast_candidates" not in BASE
    assert "from candidate_screen import" not in BASE
    assert "fast_candidate_screen" not in BASE


def test_full_predict_still_walks_every_book():
    assert "for book_id, book in state.books.items():" in BASE
    assert "predictions[book_id] = self.predict_direction(book_id, book, state.timestamp)" in BASE
    assert "_predict_all_books = _bsimpl_2_Strategy1_Debug__predict_all_books" in BASE
    assert "_bsimpl_0_DetailedTemplateAgent__predict_all_books" in BASE


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


def test_timing_profile_fields_exist_on_all_book_path():
    assert "screen_all_books_ms=0.0" in BASE
    assert "full_predict_ms=" in BASE
    assert "select_books_ms=" in BASE
    assert "build_orders_ms=" in BASE
    assert "total_response_ms=" in BASE


def test_adaptive_agent_source_unchanged_this_step():
    assert "from BaseStrategy import" in ADAPTIVE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "select_fast_candidates" not in ADAPTIVE
    assert "candidate_screen" not in ADAPTIVE
    assert PREDICT not in ADAPTIVE
