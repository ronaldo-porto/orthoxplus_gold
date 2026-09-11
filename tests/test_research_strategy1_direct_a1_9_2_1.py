"""A1.9.2.1 -- severity-prioritised suppression budget.

A1.9.2's fee-conditioned detector worked. Its 35% budget did not: it was spent
in arrival order, so over 500 ticks the books that RECEIVED budget and the books
DENIED by the cap had identical severity --

    granted (157)      median severity 40.73   mean 42.56   max 83.69
    denied  ( 46)      median severity 40.73   mean 45.01   max 88.62

-- i.e. the cap selected at random with respect to risk. The three largest
losses of the run were all cap-admitted Book 114 entries at severity 59-88,
while Book 102 (the mildest flagged book) consumed 21% of the budget, 29 of its
33 suppressions being dwell re-suppressions that never re-competed against
newly arriving, more severe books.

Holding the budget FIXED and spending it severity-first covers 33 of the 46
cap-blocks and removes 91% of the cap-bucket damage at zero volume cost, so the
cap is deliberately not retuned here. Moving allocation and budget size in one
run would confound the two.

Severity excludes the maker fee on purpose. Within the already-flagged set:

    Spearman(tail,        pnl) = -0.380   <- best single ranker
    Spearman(|net_ewma|,  pnl) = -0.240
    Spearman(fee,         pnl) = +0.024   <- no ordering information

Fee decides WHETHER an entry is dangerous and is already the first gate; it does
not decide how dangerous.

Two further findings are pinned here. Every one of the 46 cap-blocks already had
>=5 samples (median 30), so shrinkage and allocation address disjoint leaks:
ALLOW_INSUFFICIENT_HISTORY admitted 10 of 10 entries at a positive fee, 9 of 10
ended Taker, for 22.1% of cubic downside. And 81% of cap-bucket damage landed in
the first 50 ticks -- before 24 candidates had been seen, so no quantile existed
and allocation fell back to arrival order. Book quality survives restarts, so
the severity distribution is seeded from it rather than relearned.
"""

import ast
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
SIMPLE = STRATEGY_DIR / "Strategy1_Research_Simple.py"
LAUNCHER = ROOT / "run_strategy1_research_simple_multi.sh"

sys.path.insert(0, str(STRATEGY_DIR))
SRC = SIMPLE.read_text()


# ------------------------------------------------------------- source pins

def test_version_advances_to_a1_9_2_1():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_4"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_4"' in SRC


def test_suppression_cap_is_unchanged():
    """The whole point: allocation moves, budget size does not."""
    assert "A192_MAX_SUPPRESSION_PCT = 35.0" in SRC


def test_frozen_base_untouched():
    base = (STRATEGY_DIR / "Strategy1_Research.py").read_text()
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in base


def test_new_admission_reason_exists_and_is_returned():
    assert 'A192_ALLOW_SEVERITY_RANK = "ALLOW_SEVERITY_RANK"' in SRC
    assert '"reason": A192_ALLOW_SEVERITY_RANK' in SRC


def test_severity_events_are_force_emitted():
    """ENTRY_DECISION is sampled; the audit trail must not be."""
    for ev in ("A192_SEVERITY_DEFER", "A192_COLDSTART_SHRINK", "A192_SEVERITY_SEED"):
        assert ev in SRC
    idx = SRC.index('"A192_SEVERITY_DEFER", force=True')
    assert idx > 0


def test_cap_check_precedes_severity_check():
    """The hard volume bound must be evaluated before the priority test, or
    prioritisation could admit past the cap."""
    body = SRC[SRC.index("def _a192_admission_verdict"):]
    body = body[:body.index("def _a192_note_round_trip")]
    assert body.index("(sup + 1) > allowance") < body.index("float(severity) < float(threshold)")


def test_deferral_does_not_consume_budget():
    """ALLOW_SEVERITY_RANK returns before _a192_window_suppressed is advanced,
    so prioritisation can only lower suppression, never raise it."""
    body = SRC[SRC.index("def _a192_admission_verdict"):]
    body = body[:body.index("def _a192_note_round_trip")]
    assert body.index('"reason": A192_ALLOW_SEVERITY_RANK') < body.index("self._a192_window_suppressed = sup + 1")


def test_dwell_suppressions_still_consume_budget():
    """Exempting dwell from the budget is the tempting wrong fix: it would
    inflate real suppression well past 35% while still reporting 35%."""
    body = SRC[SRC.index("def _a192_admission_verdict"):]
    body = body[:body.index("def _a192_note_round_trip")]
    tail = body[body.index("self._a192_window_suppressed = sup + 1"):]
    assert "A192_SUPPRESS_DWELL" in tail


def test_launcher_guards_the_new_phase():
    sh = LAUNCHER.read_text()
    assert 'strategy1_direct_v4_16_2_a1_9_4' in sh
    assert "research_a1921_severity_priority_enabled=1" in sh
    assert "A192_MAX_SUPPRESSION_PCT = 35.0" in sh


# --------------------------------------------------------- behaviour tests

def _lift():
    """Bind the A1.9.2.1 methods to a stub; the miner stack is not importable
    in preflight."""
    want = {"_a1921_severity", "_a1921_pooled_risk", "_a1921_shrink_risk",
            "_a1921_severity_threshold", "_a1921_seed_severity_history",
            "_a1921_enabled"}
    tree = ast.parse(SRC)
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef)
               and any(isinstance(f, ast.FunctionDef) and f.name == "_a1921_severity_threshold"
                       for f in n.body))
    ns = {"deque": deque, "Any": object}
    for f in cls.body:
        if isinstance(f, ast.FunctionDef) and f.name in want:
            m = ast.Module(body=[f], type_ignores=[])
            ast.fix_missing_locations(m)
            exec(compile(m, "<lift>", "exec"), ns)

    class Stub:
        A192_MIN_BOOK_SAMPLES = 5
        A192_MAX_SUPPRESSION_PCT = 35.0
        A1921_SEVERITY_HISTORY = 256
        A1921_MIN_SEVERITY_SAMPLES = 24
        A1921_COLDSTART_PRIOR_SAMPLES = 5.0
        A1921_MIN_POOL_BOOKS = 3
        A1921_SEED_FROM_BOOK_HISTORY = True
    for k in want:
        setattr(Stub, k, ns[k])
    Stub._a1921_severity = staticmethod(ns["_a1921_severity"])
    o = Stub()
    o.research_a1921_severity_priority_enabled = True
    o._a1921_sev_hist = deque(maxlen=256)
    o._a1921_seeded = False
    o._a1921_seed_count = 0
    o._a1921_pool_tick = -1
    o._a1921_pool = None
    o._direct_maker_quality_by_book = {}
    return o


class _Stat:
    def __init__(self, count, net, cube):
        self.count = count
        self.net_bps_ewma = net
        self.taker_net_shortfall_cube_ewma = cube


def test_severity_is_tail_plus_negative_net_and_ignores_fee():
    o = _lift()
    assert abs(o._a1921_severity(
        {"book_tail_shortfall_bps": 38.7149, "book_net_bps_ewma": -20.5838}) - 59.2987) < 1e-3
    # a profitable book contributes only its tail
    assert o._a1921_severity({"book_tail_shortfall_bps": 10.0, "book_net_bps_ewma": 8.0}) == 10.0
    # and severity never goes negative
    assert o._a1921_severity({"book_tail_shortfall_bps": -3.0, "book_net_bps_ewma": 0.0}) == 0.0


def test_severity_signature_takes_no_fee():
    """Pin the exclusion structurally, not just by value."""
    tree = ast.parse(SRC)
    fn = next(f for n in tree.body if isinstance(n, ast.ClassDef)
              for f in n.body
              if isinstance(f, ast.FunctionDef) and f.name == "_a1921_severity")
    args = [a.arg for a in fn.args.args]
    assert "maker_fee_bps" not in args and "fee" not in args
    assert "fee" not in ast.unparse(fn)


def test_pool_requires_a_minimum_number_of_qualified_books():
    o = _lift()
    o._direct_maker_quality_by_book = {1: _Stat(9, -10.0, 1000.0), 2: _Stat(7, -6.0, 8.0)}
    assert o._a1921_pooled_risk(5) is None
    o._direct_maker_quality_by_book[3] = _Stat(20, -2.0, 27.0)
    o._a1921_pool_tick = -1
    assert int(o._a1921_pooled_risk(6)["pool_books"]) == 3
    # a thin book must not contribute to the prior it will be shrunk toward
    o._direct_maker_quality_by_book[4] = _Stat(2, -99.0, 1e6)
    o._a1921_pool_tick = -1
    assert int(o._a1921_pooled_risk(7)["pool_books"]) == 3


def test_coldstart_shrinkage_gives_a_zero_history_book_real_risk():
    o = _lift()
    o._direct_maker_quality_by_book = {i: _Stat(20, -4.0, 27.0) for i in range(5)}
    pool = o._a1921_pooled_risk(1)
    thin = {"book_samples": 0, "book_net_bps_ewma": 0.0, "book_tail_shortfall_bps": 0.0}
    sh = o._a1921_shrink_risk(thin, pool)
    assert sh["a1921_shrink_weight"] == 0.0
    assert abs(sh["book_net_bps_ewma"] - pool["pool_net_bps_ewma"]) < 1e-3
    # this is the leak: a 0.0 tail was never flagged, so it was always admitted
    assert sh["book_tail_shortfall_bps"] > 0.0
    assert sh["a1921_coldstart_shrunk"] == 1
    # weight n/(n+K), rounded to 4dp for the log
    sh4 = o._a1921_shrink_risk(
        {"book_samples": 4, "book_net_bps_ewma": -1.0, "book_tail_shortfall_bps": 2.0}, pool)
    assert abs(sh4["a1921_shrink_weight"] - 4 / 9) < 1e-4


def test_threshold_is_none_when_ranking_cannot_or_need_not_apply():
    o = _lift()
    o._a192_window_opportunities = 100
    o._a192_window_candidates = 50
    assert o._a1921_severity_threshold() is None          # no history yet
    for i in range(100):
        o._a1921_sev_hist.append(float(i))
    o._a192_window_candidates = 35
    assert o._a1921_severity_threshold() is None          # budget covers all


def test_threshold_tracks_the_affordable_share_of_candidates():
    o = _lift()
    for i in range(100):
        o._a1921_sev_hist.append(float(i))
    o._a192_window_opportunities = 100
    o._a192_window_candidates = 70                        # frac = .35/.70 = .50
    t = o._a1921_severity_threshold()
    assert t is not None and abs(t - 50.0) < 2.0
    o._a192_window_candidates = 100                       # frac = .35 -> stricter
    assert o._a1921_severity_threshold() > t


def test_seeding_removes_the_warm_up_blind_spot():
    o = _lift()
    o._direct_maker_quality_by_book = {i: _Stat(30, -5.0 - i, float((20 + i) ** 3))
                                       for i in range(40)}
    assert o._a1921_seed_severity_history() == 40
    assert len(o._a1921_sev_hist) >= o.A1921_MIN_SEVERITY_SAMPLES
    assert o._a1921_seed_severity_history() == 0          # idempotent
    o._a192_window_opportunities = 100
    o._a192_window_candidates = 70
    assert o._a1921_severity_threshold() is not None      # armed at first candidate


def test_master_switch_restores_a1_9_2_behaviour():
    o = _lift()
    o.research_a1921_severity_priority_enabled = False
    assert o._a1921_enabled() is False


if __name__ == "__main__":  # runnable without pytest
    fails = []
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS %s" % name)
            except Exception as exc:
                fails.append((name, exc))
                print("FAIL %s -- %s" % (name, exc))
    print("\n%d passed, %d failed" % (
        sum(1 for n in globals() if n.startswith("test_")) - len(fails), len(fails)))
    sys.exit(1 if fails else 0)
