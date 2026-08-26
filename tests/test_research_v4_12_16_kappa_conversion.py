from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_execution_lanes import (
    LaneBook,
    apply_kappa_conversion_pressure_gate,
    completion_sort_key,
)
from research_quote_hysteresis import (
    ONE_AWAY_CONVERSION_TTL_VERSION,
    one_away_stale_completion_ttl,
)

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
LAUNCHER = (ROOT / "run_strategy1_research_test_multi.sh").read_text(encoding="utf-8")


def _gate(**overrides):
    rows = [
        LaneBook(book_id=1, observations_remaining=1, score_pnl_ready=True),
        LaneBook(book_id=2, observations_remaining=1, score_pnl_ready=False),
        LaneBook(book_id=3, observations_remaining=2),
        LaneBook(book_id=4, observations_remaining=3, is_uncovered=True),
        LaneBook(book_id=5, observations_remaining=3, has_inventory=True),
    ]
    args = dict(
        parked_open_books=6,
        max_parked_open_books=6,
        total_open_books=9,
        max_total_open_books=12,
        reserve_total_slots=3,
        enabled=True,
    )
    args.update(overrides)
    return rows, apply_kappa_conversion_pressure_gate(rows, **args)


def test_park_pressure_suppresses_only_fresh_coverage():
    rows, (gated, suppressed, productive, reason) = _gate()
    by_id = {row.book_id: row for row in gated}
    assert reason == "PARKED_CAP"
    assert suppressed == {4}
    assert productive == 3
    assert by_id[1].entry_feasible and by_id[2].entry_feasible
    assert by_id[3].entry_feasible and by_id[5].entry_feasible


def test_total_headroom_pressure_and_no_progress_fail_safe():
    _, (_, suppressed, _, reason) = _gate(parked_open_books=2)
    assert reason == "TOTAL_HEADROOM" and suppressed == {4}
    rows = [LaneBook(book_id=7, observations_remaining=3, is_uncovered=True)]
    gated, suppressed, productive, reason = apply_kappa_conversion_pressure_gate(
        rows, parked_open_books=6, max_parked_open_books=6,
        total_open_books=9, max_total_open_books=12,
    )
    assert productive == 0 and not suppressed
    assert gated[0].entry_feasible and reason == "DISABLED_OR_NO_PROGRESS"


def test_score_ready_one_away_leads_equal_completion_cost():
    positive = LaneBook(book_id=9, observations_remaining=1, score_pnl_ready=True)
    negative = LaneBook(book_id=8, observations_remaining=1, score_pnl_ready=False)
    assert completion_sort_key(positive) < completion_sort_key(negative)


def test_one_away_conversion_ttl_is_longer_but_sub_publish():
    ttl, reason, used = one_away_stale_completion_ttl(
        chosen_ttl_ms=None, ttl_reason="STALE",
        completion_candidate=True, completion_samples=2, completion_target=3,
        trading_ev=0.05, market_regime="QUIET",
        min_ttl_ms=250.0, stale_ttl_ms=900.0,
    )
    assert ONE_AWAY_CONVERSION_TTL_VERSION == "one_away_conversion_ttl_v4_12_16"
    assert used and ttl == 900.0
    assert reason == "ONE_AWAY_STALE_CONVERSION"


def test_conversion_ttl_keeps_bad_ev_and_stress_fail_closed():
    for regime, ev in (("TOXIC", 0.05), ("STRESSED", 0.05), ("QUIET", 0.0)):
        ttl, _, used = one_away_stale_completion_ttl(
            chosen_ttl_ms=None, ttl_reason="STALE",
            completion_candidate=True, completion_samples=2, completion_target=3,
            trading_ev=ev, market_regime=regime,
            min_ttl_ms=250.0, stale_ttl_ms=900.0,
        )
        assert ttl is None and not used


def test_predeploy_contract_keeps_v415_loss_floors_and_park_precedence():
    assert 'RESEARCH_POLICY_VERSION = "kappa_conversion_v4_12_16_predeploy"' in SRC
    assert 'RESEARCH_LANES_VERSION = "execution_lanes_v5_kappa_pressure"' in SRC
    assert "UNIFIED_KEEP_MAKER and not liveness_parked_now" in SRC
