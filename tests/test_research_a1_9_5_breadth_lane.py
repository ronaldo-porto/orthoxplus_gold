"""A1.9.5 step 4: breadth authority moved to where the block actually is.

Measured on log 20260912_121832 (4,233 ticks) and 20260911_224141 (9,738):
A1.9.3 produced zero admits and zero denies in both.  All 1,320 one-away RANK
rows were `eligible=True` with `reject_reason=None`, so the override's trigger
never occurred.  545 A1745 low-edge blocks landed on one-away books instead,
median edge +8.40 bps against a floor raised from 2.5 to 15.0.
"""
import ast
from pathlib import Path

from research_direct_breadth_lane import (
    A195_BREADTH_LANE_VERSION,
    A195_BREADTH_RELIEF_COOLDOWN_TICKS,
    A195_MAX_BREADTH_RELIEF_PER_TICK,
    DENY_COOLDOWN,
    DENY_DISABLED,
    DENY_EDGE_BELOW_BASE,
    DENY_GATE_INACTIVE,
    DENY_NO_DEFICIT,
    DENY_NOT_ONE_AWAY,
    DENY_TICK_BUDGET,
    RELIEF_GRANTED,
    evaluate_breadth_relief,
)

ROOT = Path(__file__).parents[1]
SIMPLE = (ROOT / "agents" / "strategy" / "Strategy1_Research_Simple.py").read_text()
MODULE = (ROOT / "agents" / "strategy" / "research_direct_breadth_lane.py").read_text()

# The measured live state: base floor 2.5, gate escalated to 15.0, the median
# blocked one-away book showing +8.40 bps, score deficit 9.
LIVE = dict(
    enabled=True, observations_remaining=1, current_edge_bps=8.40,
    base_min_edge_bps=2.5, effective_min_edge_bps=15.0,
    score_deficit=9, granted_this_tick=0,
)


def _ev(**kw):
    return evaluate_breadth_relief(**{**LIVE, **kw})


def _method_source(name: str) -> str:
    tree = ast.parse(SIMPLE)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    defs = [n for n in cls.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    assert defs, name
    return ast.get_source_segment(SIMPLE, defs[-1])


# ---- the live case this exists for ---------------------------------------

def test_the_median_blocked_one_away_book_is_now_admitted():
    verdict = _ev()
    assert verdict.allow
    assert verdict.reason == RELIEF_GRANTED
    assert verdict.relieved_min_edge_bps == 2.5
    assert verdict.relief_bps == 12.5


def test_relief_restores_the_base_floor_and_never_goes_below_it():
    for edge in (2.5, 5.0, 8.4, 14.9):
        verdict = _ev(current_edge_bps=edge)
        assert verdict.allow
        assert verdict.relieved_min_edge_bps == LIVE["base_min_edge_bps"]


def test_the_whole_measured_blocked_range_clears_the_base_floor():
    """min +2.52, max +14.93 -- every one of them is above base 2.5."""
    for edge in (2.52, 8.40, 14.93):
        assert _ev(current_edge_bps=edge).allow


# ---- the lines that must not be crossed ----------------------------------

def test_negative_edge_is_never_relieved():
    """Paying to trade for score is buying breadth by destroying kappa."""
    for edge in (-0.01, -5.0, -50.0):
        verdict = _ev(current_edge_bps=edge)
        assert not verdict.allow
        assert verdict.reason == DENY_EDGE_BELOW_BASE


def test_zero_edge_is_never_relieved():
    assert not _ev(current_edge_bps=0.0).allow


def test_an_edge_under_the_base_floor_stays_blocked():
    verdict = _ev(current_edge_bps=2.49)
    assert not verdict.allow and verdict.reason == DENY_EDGE_BELOW_BASE


def test_books_more_than_one_away_are_excluded():
    """A single round trip does not change their qualification state, so
    relief would buy volume rather than breadth."""
    for remaining in (2, 3, 0, -1):
        verdict = _ev(observations_remaining=remaining)
        assert not verdict.allow
        assert verdict.reason == DENY_NOT_ONE_AWAY


def test_nothing_is_relieved_when_the_regime_gate_never_escalated():
    verdict = _ev(effective_min_edge_bps=2.5)
    assert not verdict.allow and verdict.reason == DENY_GATE_INACTIVE
    assert _ev(effective_min_edge_bps=1.0).reason == DENY_GATE_INACTIVE


def test_relief_stops_once_the_score_target_is_met():
    verdict = _ev(score_deficit=0)
    assert not verdict.allow and verdict.reason == DENY_NO_DEFICIT


# ---- bounds ---------------------------------------------------------------

def test_per_tick_budget_is_enforced():
    assert _ev(granted_this_tick=1).allow
    verdict = _ev(granted_this_tick=A195_MAX_BREADTH_RELIEF_PER_TICK)
    assert not verdict.allow and verdict.reason == DENY_TICK_BUDGET


def test_breadth_is_a_flow_not_a_burst():
    """Granting the whole deficit at once converts it to simultaneous
    inventory, which is how capacity starvation started."""
    assert A195_MAX_BREADTH_RELIEF_PER_TICK <= 3


def test_one_book_cannot_hold_the_budget_every_tick():
    verdict = _ev(ticks_since_last_relief=1)
    assert not verdict.allow and verdict.reason == DENY_COOLDOWN
    assert _ev(ticks_since_last_relief=A195_BREADTH_RELIEF_COOLDOWN_TICKS).allow
    # A book never relieved has no cooldown to serve.
    assert _ev(ticks_since_last_relief=None).allow


def test_the_switch_turns_it_off():
    verdict = _ev(enabled=False)
    assert not verdict.allow and verdict.reason == DENY_DISABLED
    assert verdict.relieved_min_edge_bps == verdict.effective_min_edge_bps


def test_hostile_inputs_do_not_relieve():
    for bad in (None, float("nan"), "x"):
        assert not _ev(current_edge_bps=bad).allow


def test_log_payload_is_json_safe_and_versioned():
    import json
    payload = _ev().as_log()
    assert payload["a195_breadth_lane_version"] == A195_BREADTH_LANE_VERSION
    assert payload["relief_bps"] == 12.5
    json.dumps(payload)


# ---- wiring ---------------------------------------------------------------

def test_the_dead_a193_hook_is_retired_with_its_evidence():
    assert "RETIRED in A1.9.5 step 4" in SIMPLE
    # The call site is gone...
    assert "self._a193_breadth_override(" not in SIMPLE
    # ...but the method and its budget helpers remain: step 4 draws on them,
    # and the A1.9.2 loader suite requires them present.
    assert "def _a193_breadth_override(" in SIMPLE
    assert "def _a193_breadth_budget(" in SIMPLE
    assert "def _a193_score_deficit(" in SIMPLE


def test_relief_lands_at_the_a1745_boundary():
    idx_gate = SIMPLE.index("effective_maker_min_edge_bps = float(a1745_gate.effective_min_edge_bps)")
    idx_relief = SIMPLE.index("effective_maker_min_edge_bps = self._a195_breadth_relief(")
    idx_choose = SIMPLE.index("decision = choose_direct_execution(")
    assert idx_gate < idx_relief < idx_choose


def test_the_budget_is_shared_with_a193_not_duplicated():
    body = _method_source("_a195_breadth_relief")
    assert "_a193_breadth_budget(self._a193_score_deficit())" in body


def test_relief_fails_closed():
    body = _method_source("_a195_breadth_relief")
    assert "except Exception:" in body
    assert "return float(effective_min_edge_bps)" in body


def test_denies_are_only_counted_where_relief_was_possible():
    """NOT_ONE_AWAY fires on almost every book; counting it would bury the
    signal that breadth-critical books are being refused."""
    body = _method_source("_a195_breadth_relief")
    assert '"NOT_ONE_AWAY", "GATE_INACTIVE", "DISABLED"' in body


def test_summary_stats_are_exported():
    for key in (
        "direct_a195_breadth_lane", "direct_a195_breadth_lane_version",
        "direct_a195_breadth_grants", "direct_a195_breadth_denies",
        "direct_a195_breadth_relief_bps_total",
        "direct_a195_breadth_deny_reasons",
    ):
        assert key in SIMPLE, key


def test_module_records_the_zero_event_evidence():
    assert "zero" in MODULE
    assert "1,320" in MODULE
    assert "545" in MODULE
