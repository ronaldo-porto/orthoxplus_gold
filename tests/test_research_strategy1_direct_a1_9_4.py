"""A1.9.4 -- the rebate stops being an admission waiver.

A1.9.2 short-circuited admission on `fee <= 0`, returning ALLOW_REBATE_ENTRY
before reading a single history field, on the finding that poor-history books
entered at a rebate produced 0 bad round trips in 21 observations while the
same books at a positive fee produced a 30.4% bad rate and 87% of all cubic
downside.

The A1.9.3 run (ticks 1-459) falsified the unconditional form of that. 52% of
admissions (118 of 227) came through the rebate branch, and it carried nearly
all the damage. From A174_TAIL_COUNTERFACTUAL, 21 forced Taker exits, 2,189.0
bps avoidable, mean 104 bps:

    book   mean entry fee   avoidable    share   fills   net_bps_ewma
      97          -48.9        881.4     40.3%      11          -58.5
      39          -10.7        612.6     28.0%      31          -29.2
      61           -7.8        380.8     17.4%      29          -13.6

85.6% of all avoidable loss, from three books admitted through the waiver on
every one of their 116 entries -- ranked in exactly the order of their rebate
depth. Every one of those exits was risk-authorized and none was economically
authorized.

Two structural reasons the waiver cannot stand, neither fitted to that log:

  1. A rebate is the venue's compensation for expected adverse selection, so
     its SIZE measures how dangerous the venue believes quoting there is.
     Book 97 paid 48.9 bps to quote and charged 71 bps of Taker cost to leave.
  2. `book_net_bps_ewma` is realized net per round trip and ALREADY INCLUDES
     the rebate. A rebate book with negative net has been paid and still lost.
     That is an accounting identity, not a threshold.

So fee sign moves AFTER the history read and becomes an exemption bounded by
the harm the book has actually done: admitted while `rebate_bps >= severity`,
otherwise ranked as an ordinary candidate.

It deliberately does NOT become a severity credit. A1.9.2.1 measured
Spearman(fee, pnl) = +0.024 within the already-flagged set, and the damage
above ranks by rebate DEPTH -- crediting the rebate would rank the worst books
safest. Severity stays fee-blind and the 35% cap is untouched, so admission
ordering is the only thing this revision moves.

Only 4 rebate books appear in that run and book 83 (-7.7 bps) took no damage
on 4 fills, so the DEPTH ordering is not treated as calibrated anywhere here:
nothing below depends on it. The tests pin the ordering change and the bound,
which follow from the two structural reasons alone.
"""

import ast
import math
import sys
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
SIMPLE = STRATEGY_DIR / "Strategy1_Research_Simple.py"
LAUNCHER = ROOT / "run_strategy1_research_simple_multi.sh"

sys.path.insert(0, str(STRATEGY_DIR))
SRC = SIMPLE.read_text()


# ------------------------------------------------------------ versioning

def test_version_advances_to_a1_9_4():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_4"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_4"' in SRC


def test_launcher_guards_the_new_phase():
    sh = LAUNCHER.read_text()
    assert "strategy1_direct_v4_16_2_a1_9_4" in sh
    assert "research_a194_rebate_conjunction_enabled=1" in sh


def test_events_are_named_so_an_analysis_can_grep_them():
    """A1.9.3 shipped inert and was only caught by after-the-fact log
    analysis. Every phase since names its events in the banner."""
    assert 'DIRECT_A194_EVENTS = ("A194_REBATE_COVERED", "A194_REBATE_WAIVER_WITHDRAWN")' in SRC
    banner = SRC.split("def _a192_check_activation")[1].split("\n    def ")[0]
    assert "a194_events" in banner
    assert "a194_rebate_conjunction_enabled" in banner


# ------------------------------------------------------- source structure

def _verdict():
    """Body of the gate with its docstring stripped -- the docstring names the
    same constants the ordering checks look for."""
    body = SRC.split("def _a192_admission_verdict")[1].split("\n    def ")[0]
    return body.split('"""', 2)[2]


def test_the_unconditional_waiver_is_gone():
    """THE line that cost the A1.9.3 run: `if fee <= 0.0:` returning
    ALLOW_REBATE_ENTRY before any history was read."""
    body = _verdict()
    assert "if fee <= 0.0 and not self._a194_enabled():" in body
    assert "\n        if fee <= 0.0:\n" not in body


def test_fee_is_evaluated_after_book_history():
    """The reversal of the A1.9.2 ordering is the whole revision."""
    body = _verdict()
    assert body.index("risk = self._a192_book_risk(bid)") < body.index("A194_ALLOW_REBATE_COVERED")


def test_severity_ranking_stays_fee_blind():
    """Spearman(fee, pnl) = +0.024 inside the flagged set, and A1.9.3 damage
    ranked by rebate depth -- a credit would rank the worst books safest."""
    body = _verdict()
    assert "severity = self._a1921_severity(risk)" in body
    sev = ast.parse(SRC)
    fn = next(n for n in ast.walk(sev)
              if isinstance(n, ast.FunctionDef) and n.name == "_a1921_severity")
    src = ast.unparse(fn)
    assert "fee" not in src and "rebate" not in src


def test_the_a192_thresholds_do_not_move():
    """Admission ordering and detector/budget size cannot both move in one
    run or neither is attributable."""
    for const in ("A192_MAX_SUPPRESSION_PCT = 35.0", "A192_NET_BPS_FLOOR = 0.0",
                  "A192_TAIL_SHORTFALL_FLOOR_BPS = 5.0", "A192_MIN_BOOK_SAMPLES = 5"):
        assert const in SRC


def test_coverage_test_precedes_candidate_accounting():
    """A covered book must not be counted as a candidate, or it would enter
    the severity distribution it was exempted from."""
    body = _verdict()
    assert body.index("A194_ALLOW_REBATE_COVERED") < body.index("self._a192_candidates = int(")


# ------------------------------------------------------------ behavioural

WANTED = {
    "_a192_reset_admission", "_a192_enabled", "_a192_book_risk",
    "_a192_window_roll", "_a192_admission_verdict",
    "_a1921_enabled", "_a1921_severity", "_a1921_severity_threshold",
    "_a1921_pooled_risk", "_a1921_shrink_risk", "_a1921_seed_severity_history",
    "_a194_enabled", "_a194_rebate_bps",
}


def _load():
    tree = ast.parse(SRC)
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and
                      (t.id.startswith("A192_") or t.id.startswith("DIRECT_A192_")
                       or t.id.startswith("A193_") or t.id.startswith("DIRECT_A193_")
                       or t.id.startswith("A194_") or t.id.startswith("DIRECT_A194_"))
                      for t in n.targets)]
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    assert {m.name for m in methods} == WANTED, WANTED ^ {m.name for m in methods}
    ns = {"math": math, "Any": object, "SIMPLE_ENGINE_VERSION": "test", "deque": deque}
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=consts + methods, type_ignores=[])), "<a194>", "exec"), ns)
    return ns


NS = _load()
BOOK = 7


class _Stats:
    def __init__(self, count=0, net_bps_ewma=0.0, cube=0.0):
        self.count = count
        self.net_bps_ewma = net_bps_ewma
        self.taker_net_shortfall_cube_ewma = cube


class _Agent:
    A192_MIN_BOOK_SAMPLES = 5
    A192_NET_BPS_FLOOR = 0.0
    A192_TAIL_SHORTFALL_FLOOR_BPS = 5.0
    A192_MAX_SUPPRESSION_PCT = 35.0
    A192_WINDOW_TICKS = 200
    A192_DWELL_BASE_TICKS = 40
    A192_DWELL_MAX_TICKS = 400
    A192_RECOVERY_CLEAN_RTS = 2
    A1921_SEVERITY_HISTORY = 256
    A1921_MIN_SEVERITY_SAMPLES = 24
    A1921_COLDSTART_PRIOR_SAMPLES = 5.0
    A1921_MIN_POOL_BOOKS = 3
    A1921_SEED_FROM_BOOK_HISTORY = True
    A194_REBATE_CONJUNCTION = True

    def __init__(self, a194=True):
        self._tick = 100
        self.research_a192_book_risk_admission_enabled = True
        self.research_a1921_severity_priority_enabled = True
        self.research_a194_rebate_conjunction_enabled = a194
        self._direct_maker_quality_by_book = {}
        self.events = []
        self._a192_reset_admission()

    def _emit(self, event_type, force=False, **payload):
        self.events.append((event_type, payload))

    def rows(self, name):
        return [p for e, p in self.events if e == name]


for _n in WANTED:
    setattr(_Agent, _n, NS[_n])


# Book 97 to scale: paid deeply, lost far more than it was paid.
# severity = tail 60.0 + |net -58.5| = 118.5, rebate 48.9.
BOOK97 = _Stats(count=12, net_bps_ewma=-58.5, cube=60.0 ** 3)
# Mildly damaged: severity = 6.0 + 0.5 = 6.5, comfortably under an 8 bps rebate.
MILD_TAIL = _Stats(count=20, net_bps_ewma=-0.5, cube=6.0 ** 3)
# Profitable, so never flagged at all.
GOOD = _Stats(count=20, net_bps_ewma=+2.0, cube=8.0 ** 3)


def _agent(stats=BOOK97, a194=True, cap=100.0):
    a = _Agent(a194=a194)
    if stats is not None:
        a._direct_maker_quality_by_book[BOOK] = stats
    a.A192_MAX_SUPPRESSION_PCT = cap
    return a


def test_rebate_bps_is_the_positive_magnitude_of_a_credit():
    a = _agent()
    assert a._a194_rebate_bps(-8.0) == 8.0
    assert a._a194_rebate_bps(0.0) == 0.0
    assert a._a194_rebate_bps(3.0) == 0.0      # a cost is not a rebate


def test_deep_rebate_does_not_save_a_book_that_lost_more_than_it_paid():
    """Book 97 to scale: -48.9 bps rebate against 118.5 bps of measured harm.
    A1.9.2 admitted this on all 15 of its entries."""
    v = _agent()._a192_admission_verdict(BOOK, maker_fee_bps=-48.9, tick=100)
    assert v["suppress"] is True
    assert v["reason"] == NS["A192_SUPPRESS_FLAGGED"]


def test_a192_would_have_admitted_that_exact_book():
    """Pins the delta rather than asserting the new behaviour in isolation."""
    v = _agent(a194=False)._a192_admission_verdict(BOOK, maker_fee_bps=-48.9, tick=100)
    assert v["suppress"] is False
    assert v["reason"] == NS["A192_ALLOW_REBATE"]


def test_rebate_exceeding_measured_harm_still_admits():
    """The A1.9.2 finding survives where it is actually true: being paid more
    than the book has cost you is a real exemption."""
    v = _agent(MILD_TAIL)._a192_admission_verdict(BOOK, maker_fee_bps=-8.0, tick=100)
    assert v["suppress"] is False
    assert v["reason"] == NS["A194_ALLOW_REBATE_COVERED"]


def test_coverage_is_measured_against_severity_not_net_alone():
    """Kappa harm is cubic: a book with a small mean loss and a large tail is
    not covered by a rebate that only exceeds the mean."""
    a = _agent(_Stats(count=20, net_bps_ewma=-0.5, cube=40.0 ** 3))
    v = a._a192_admission_verdict(BOOK, maker_fee_bps=-5.0, tick=100)
    assert v["suppress"] is True


def test_a_profitable_rebate_book_is_never_even_scored():
    """Unflagged books short-circuit before the coverage test, so the common
    case costs nothing and keeps its A1.9.2 verdict."""
    a = _agent(GOOD)
    v = a._a192_admission_verdict(BOOK, maker_fee_bps=-8.0, tick=100)
    assert v["suppress"] is False
    assert v["reason"] == NS["A192_ALLOW_BOOK_OK"]
    assert a._a192_candidates == 0


def test_a_positive_fee_book_is_unaffected_by_this_revision():
    for a194 in (True, False):
        v = _agent(a194=a194)._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
        assert v["suppress"] is True
        assert v["reason"] == NS["A192_SUPPRESS_FLAGGED"]


def test_book_without_enough_history_is_still_admitted():
    """A1.9.4 adds no new way to deny a book that has no track record."""
    a = _agent(_Stats(count=2, net_bps_ewma=-9.0, cube=20.0 ** 3))
    v = a._a192_admission_verdict(BOOK, maker_fee_bps=-8.0, tick=100)
    assert v["suppress"] is False


def test_withdrawal_is_announced_once_per_book():
    a = _agent()
    for t in (100, 101, 102):
        a._a192_admission_verdict(BOOK, maker_fee_bps=-48.9, tick=t)
    rows = a.rows("A194_REBATE_WAIVER_WITHDRAWN")
    assert len(rows) == 1
    r = rows[0]
    assert r["book"] == BOOK
    assert r["rebate_bps"] == 48.9
    assert r["severity_shortfall_bps"] > 0.0
    assert a._a194_waiver_withdrawn == 3      # counted every time, logged once


def test_coverage_is_announced_once_per_book():
    a = _agent(MILD_TAIL)
    for t in (100, 101):
        a._a192_admission_verdict(BOOK, maker_fee_bps=-8.0, tick=t)
    rows = a.rows("A194_REBATE_COVERED")
    assert len(rows) == 1
    assert rows[0]["rebate_bps"] == 8.0
    assert a._a194_rebate_covered_admits == 2


def test_the_switch_restores_a192_exactly():
    a = _agent(a194=False)
    v = a._a192_admission_verdict(BOOK, maker_fee_bps=-48.9, tick=100)
    assert v["reason"] == NS["A192_ALLOW_REBATE"]
    assert a._a192_candidates == 0             # history never read
    assert a._a194_waiver_withdrawn == 0
    assert a.rows("A194_REBATE_WAIVER_WITHDRAWN") == []


def test_a_rebate_no_longer_breaks_quarantine():
    """Under A1.9.2 a rebate re-admitted a dwelling book unconditionally --
    the same immunity by another route."""
    a = _agent()
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)     # quarantine it
    v = a._a192_admission_verdict(BOOK, maker_fee_bps=-48.9, tick=101)
    assert v["suppress"] is True
    assert v["reason"] == NS["A192_SUPPRESS_DWELL"]


def test_a_covering_rebate_still_releases_quarantine():
    """The bound is symmetric: coverage is an exemption wherever it holds, so
    quarantine is not a one-way ratchet."""
    a = _agent(MILD_TAIL)
    a._direct_maker_quality_by_book[BOOK] = BOOK97
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    a._direct_maker_quality_by_book[BOOK] = MILD_TAIL
    v = a._a192_admission_verdict(BOOK, maker_fee_bps=-8.0, tick=101)
    assert v["suppress"] is False
    assert v["reason"] == NS["A194_ALLOW_REBATE_COVERED"]


def test_suppression_stays_inside_the_volume_cap():
    """The revision admits more books to the candidate pool; the 35% bound on
    removed flow is what keeps that from becoming a volume gate."""
    a = _agent(cap=35.0)
    for t in range(100, 160):
        a._a192_admission_verdict(BOOK + (t % 9), maker_fee_bps=-48.9, tick=t)
    opp = a._a192_window_opportunities
    assert opp == 60
    assert a._a192_window_suppressed <= math.ceil(opp * 0.35)
