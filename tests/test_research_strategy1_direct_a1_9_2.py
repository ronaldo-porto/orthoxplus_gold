"""A1.9.2 -- fee-conditioned book risk admission.

A1.9.1.2 fixed exit realization and still lost the run. Over 6,607 ticks the
Maker side produced +57.20 and the Taker side -67.07; 99.9% of cubic downside
came from Taker endings and 93.3% from ABSOLUTE_PROTECTION_REDUCE alone, while
Maker endings contributed 0.1%. More exit repricing cannot reach that: 36 of 47
ABSOLUTE lifecycles never had a strong Maker exit to preserve.

Reconstructing 736 round trips and building the control group the first pass
lacked -- the prior history of books whose trips came out FINE, not only those
that blew up:

    prior RT count          AUC 0.502   <- no signal whatsoever
    prior cumulative PnL    AUC 0.629
    entry Maker fee         AUC 0.669

    fee <= 0 & good history   n=142   bad  7.7%   PnL +25.08   cubic  1.23
    fee <= 0 & poor history   n= 21   bad  0.0%   PnL  +4.95   cubic  0.00
    fee >  0 & good history   n=323   bad 18.9%   PnL -14.94   cubic  2.15
    fee >  0 & poor history   n=250   bad 30.4%   PnL -30.61   cubic 23.57

A poor-history book entered at a rebate produced ZERO bad round trips. The harm
lives in the conjunction, so quarantining on history alone would have suppressed
21 profitable trips for nothing. One cell -- 34% of round trips -- carries 87%
of all cubic downside.

These tests pin the conjunction, the bound that stops a tail-risk gate becoming
a volume gate, and the activation reporting that two A1.9.1 runs lacked.
"""

import ast
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
SIMPLE = STRATEGY_DIR / "Strategy1_Research_Simple.py"
REFRESH = STRATEGY_DIR / "research_direct_exit_refresh.py"

sys.path.insert(0, str(STRATEGY_DIR))
SRC = SIMPLE.read_text()

BOOK = 115


# ------------------------------------------------------------- source pins

def test_version_advances_to_a1_9_2_1():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_3"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_3"' in SRC


def test_exit_refresh_version_no_longer_reports_a_stale_phase():
    """The A1.9.1.2 banner reported exit_refresh_version=a1_9_1_1 all run."""
    src = REFRESH.read_text()
    assert 'DIRECT_EXIT_REFRESH_VERSION = "direct_exit_refresh_v4_16_2_a1_9_2"' in src
    assert "a1_9_1_1" not in src


def test_admission_is_entry_only_by_construction():
    """Reduction must keep full authority: the gate lives behind the FLAT guard."""
    body = SRC.split("def _place_skewed_quotes")[1].split("\n    def ")[0]
    flat_guard = body.index('band", "FLAT") or "FLAT").upper() != "FLAT"')
    gate = body.index("_a192_admission_verdict(")
    assert flat_guard < gate


def test_only_a_maker_acquisition_can_be_suppressed():
    body = SRC.split("def _place_skewed_quotes")[1].split("\n    def ")[0]
    seg = body[body.index("A1.9.2 fee-conditioned"):body.index("_a192_admission_verdict(")]
    assert "decision.action == EXEC_ACTION_MAKER" in seg


def test_gate_runs_before_entry_decision_is_emitted():
    """Otherwise the log would report an action the strategy did not take."""
    body = SRC.split("def _place_skewed_quotes")[1].split("\n    def ")[0]
    assert body.index("_a192_admission_verdict(") < body.index('"ENTRY_DECISION"')


def test_prior_round_trip_count_is_never_a_risk_criterion():
    """It measured AUC 0.502 -- pure noise. Only sufficiency, never severity."""
    body = SRC.split("def _a192_admission_verdict")[1].split("\n    def ")[0]
    flagged = body[body.index("flagged = ("):body.index("if not flagged:")]
    assert "book_samples" not in flagged


def test_activation_banner_is_above_every_early_return():
    """A19_ACTIVATION_BANNER was once emitted after an early return, so it was
    absent in exactly the case that invalidated the run."""
    body = SRC.split("def _a192_check_activation")[1].split("\n    def ")[0]
    assert body.index("A192_ACTIVATION_BANNER") < body.index("return")


# ------------------------------------------------------------------ harness

WANTED = {
    "_a192_reset_admission", "_a192_enabled", "_a192_runtime_phase",
    "_a192_behaviour_change", "_a192_book_risk", "_a192_window_roll",
    "_a192_admission_verdict", "_a192_note_round_trip", "_a192_check_activation",
    # Behavioural coverage of the duplicate-ack repair. A source-text assertion
    # survives `if False:` -- the string stays in the dead block -- which is the
    # same gap that let the A1.9.1.2 notice hook ship untested.
    "_a19_settle_cancel_watch",
}


def _load():
    tree = ast.parse(SRC)
    consts = [n for n in tree.body if isinstance(n, ast.Assign)
              and any(isinstance(t, ast.Name) and
                      (t.id.startswith("A192_") or t.id.startswith("DIRECT_A192_"))
                      for t in n.targets)]
    cls = next(n for n in tree.body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    assert {m.name for m in methods} == WANTED, WANTED ^ {m.name for m in methods}
    ns = {"math": math, "Any": object, "SIMPLE_ENGINE_VERSION": "test"}
    exec(compile(ast.fix_missing_locations(
        ast.Module(body=consts + methods, type_ignores=[])), "<a192>", "exec"), ns)
    return ns


NS = _load()


class _Stats:
    def __init__(self, count=0, net_bps_ewma=0.0, cube=0.0):
        self.count = count
        self.net_bps_ewma = net_bps_ewma
        self.taker_net_shortfall_cube_ewma = cube


class _Agent:
    # thresholds mirror the class constants under test
    A192_MIN_BOOK_SAMPLES = 5
    A192_NET_BPS_FLOOR = 0.0
    A192_TAIL_SHORTFALL_FLOOR_BPS = 5.0
    A192_MAX_SUPPRESSION_PCT = 35.0
    A192_WINDOW_TICKS = 200
    A192_DWELL_BASE_TICKS = 40
    A192_DWELL_MAX_TICKS = 400
    A192_RECOVERY_CLEAN_RTS = 2
    A192_ACTIVATION_ALARM_CANDIDATES = 20
    A19_CANCEL_ACK_BUDGET_TICKS = 2

    def __init__(self, enabled=True):
        self._tick = 100
        self.research_a192_book_risk_admission_enabled = enabled
        self._direct_maker_quality_by_book = {}
        self.events = []
        self._a19_cancel_watch = {}
        self._a19_identity_acked = set()
        self._a19_cancel_acks = 0
        self._a19_cancel_ack_ticks_total = 0
        self._a19_cancel_not_acked = 0
        self._a19_duplicate_acks_suppressed = 0
        self._a192_reset_admission()

    def _emit(self, event_type, force=False, **payload):
        self.events.append((event_type, payload))

    def rows(self, name):
        return [p for e, p in self.events if e == name]


for _n in WANTED:
    setattr(_Agent, _n, NS[_n])


# A book that is genuinely toxic: loses money on average AND does it through
# Taker tails. 8 bps cubed = 512.
TOXIC = _Stats(count=20, net_bps_ewma=-3.0, cube=8.0 ** 3)
# Loses a little but produces no tail.
MILD = _Stats(count=20, net_bps_ewma=-3.0, cube=1.0 ** 3)
# Profitable.
GOOD = _Stats(count=20, net_bps_ewma=+2.0, cube=8.0 ** 3)


def _agent(stats=TOXIC, enabled=True, cap=None):
    a = _Agent(enabled=enabled)
    if stats is not None:
        a._direct_maker_quality_by_book[BOOK] = stats
    if cap is not None:
        # Tests about dwell/recovery must not be throttled by the volume bound;
        # the cap has its own tests below.
        a.A192_MAX_SUPPRESSION_PCT = cap
    return a


# ------------------------------------------------- THE conjunction, pinned

def test_positive_fee_and_toxic_book_is_suppressed():
    v = _agent()._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    assert v["suppress"] is True
    assert v["reason"] == NS["A192_SUPPRESS_FLAGGED"]


def test_rebate_admits_the_same_toxic_book():
    """THE finding: poor history at a rebate produced 0 bad RTs in 21 samples.
    History-only quarantine would have suppressed those for nothing."""
    v = _agent()._a192_admission_verdict(BOOK, maker_fee_bps=-1.0, tick=100)
    assert v["suppress"] is False
    assert v["reason"] == NS["A192_ALLOW_REBATE"]


def test_zero_fee_is_treated_as_a_rebate_entry():
    v = _agent()._a192_admission_verdict(BOOK, maker_fee_bps=0.0, tick=100)
    assert v["suppress"] is False


def test_fee_is_evaluated_before_book_history():
    """Ordering is the finding, not style: a rebate must short-circuit."""
    a = _agent()
    a._a192_admission_verdict(BOOK, maker_fee_bps=-1.0, tick=100)
    assert a._a192_candidates == 0          # never even scored the book
    assert a._a192_rebate_admits == 1


def test_positive_fee_and_profitable_book_is_admitted():
    v = _agent(GOOD)._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    assert v["suppress"] is False
    assert v["reason"] == NS["A192_ALLOW_BOOK_OK"]


def test_losing_book_without_a_tail_is_admitted():
    """net_bps < 0 alone is not the criterion; Kappa harm is cubic."""
    v = _agent(MILD)._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    assert v["suppress"] is False


def test_book_without_enough_history_is_admitted():
    v = _agent(_Stats(count=2, net_bps_ewma=-9.0, cube=20.0 ** 3))._a192_admission_verdict(
        BOOK, maker_fee_bps=2.0, tick=100)
    assert v["suppress"] is False
    assert v["reason"] == NS["A192_ALLOW_NO_HISTORY"]


def test_unknown_book_is_admitted():
    v = _agent(None)._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    assert v["suppress"] is False


def test_disabled_gate_never_suppresses():
    v = _agent(enabled=False)._a192_admission_verdict(BOOK, maker_fee_bps=9.0, tick=100)
    assert v["suppress"] is False
    assert v["reason"] == NS["A192_ALLOW_DISABLED"]


def test_tail_threshold_is_expressed_in_bps_not_bps_cubed():
    """Cube root keeps the floor interpretable and scale-stable."""
    a = _agent(_Stats(count=20, net_bps_ewma=-3.0, cube=6.0 ** 3))
    assert abs(a._a192_book_risk(BOOK)["book_tail_shortfall_bps"] - 6.0) < 1e-6


# ------------------------------------------------------- the volume bound

def test_suppression_is_capped_so_it_cannot_become_a_volume_gate():
    """Fee-only scored better on PnL but removed 78% of round trips, which
    would collapse RT velocity from 0.128 to ~0.028."""
    a = _agent()
    for i in range(200):
        a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    pct = 100.0 * a._a192_window_suppressed / a._a192_window_opportunities
    assert pct <= _Agent.A192_MAX_SUPPRESSION_PCT + 1e-9
    assert a._a192_cap_blocks > 0


def test_cap_block_is_reported_not_silent():
    a = _agent()
    seen = [a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
            for _ in range(50)]
    assert any(v["reason"] == NS["A192_ALLOW_CAP"] for v in seen)


def test_cap_window_rolls_so_the_bound_is_not_lifetime():
    a = _agent()
    for _ in range(50):
        a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    a._a192_window_roll(100 + _Agent.A192_WINDOW_TICKS)
    assert a._a192_window_opportunities == 0
    assert a._a192_window_suppressed == 0


def test_first_opportunity_in_a_window_is_never_cap_blocked():
    """Comparing (sup+1)/opp against the cap deadlocks: the first opportunity
    of every window is 1/1 = 100% and would always exceed it, so the gate would
    be permanently inert -- exactly the A1.9.1 failure in a new place."""
    a = _agent()
    v = a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    assert v["suppress"] is True


def test_cap_converges_to_the_bound_over_a_window():
    a = _agent()
    for _ in range(1000):
        a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    pct = 100.0 * a._a192_window_suppressed / a._a192_window_opportunities
    assert 34.0 <= pct <= 35.0


# -------------------------------------------------------- dwell + recovery

def test_dwell_escalates_with_repeat_strikes():
    a = _agent(cap=100.0)
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    first = a._a192_dwell_until[BOOK] - 100
    a._a192_dwell_until[BOOK] = 0                 # let it re-flag
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=300)
    second = a._a192_dwell_until[BOOK] - 300
    assert second > first


def test_dwell_is_bounded():
    a = _agent(cap=100.0)
    a._a192_strikes[BOOK] = 999
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    assert a._a192_dwell_until[BOOK] - 100 <= _Agent.A192_DWELL_MAX_TICKS


def test_quarantine_is_not_permanent():
    """A recovered book must be re-tested or the gate slowly starves the book set."""
    a = _agent(cap=100.0)
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    assert a._a192_strikes[BOOK] == 1
    for _ in range(_Agent.A192_RECOVERY_CLEAN_RTS):
        a._a192_note_round_trip(BOOK, net_bps=5.0, exit_is_taker=False)
    assert a._a192_strikes[BOOK] == 0
    assert BOOK not in a._a192_dwell_until
    assert a._a192_recoveries == 1


def test_recovery_needs_more_than_one_clean_round_trip():
    a = _agent(cap=100.0)
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    a._a192_note_round_trip(BOOK, net_bps=5.0, exit_is_taker=False)
    assert a._a192_strikes[BOOK] == 1


def test_a_positive_taker_exit_does_not_count_as_recovery():
    """It does not prove the book stopped producing tails."""
    a = _agent(cap=100.0)
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    for _ in range(5):
        a._a192_note_round_trip(BOOK, net_bps=5.0, exit_is_taker=True)
    assert a._a192_strikes[BOOK] == 1


def test_a_losing_maker_exit_does_not_count_as_recovery():
    a = _agent(cap=100.0)
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    for _ in range(5):
        a._a192_note_round_trip(BOOK, net_bps=-5.0, exit_is_taker=False)
    assert a._a192_strikes[BOOK] == 1


def test_recovery_emits_a_record():
    a = _agent(cap=100.0)
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    for _ in range(_Agent.A192_RECOVERY_CLEAN_RTS):
        a._a192_note_round_trip(BOOK, net_bps=5.0, exit_is_taker=False)
    assert a.rows("A192_BOOK_RECOVERED")


def test_dwell_suppresses_even_if_the_book_score_recovers_mid_quarantine():
    a = _agent(cap=100.0)
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    a._direct_maker_quality_by_book[BOOK] = GOOD
    v = a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=101)
    assert v["suppress"] is True
    assert v["reason"] == NS["A192_SUPPRESS_DWELL"]


def test_rebate_re_admits_even_during_dwell():
    a = _agent(cap=100.0)
    a._a192_admission_verdict(BOOK, maker_fee_bps=2.0, tick=100)
    v = a._a192_admission_verdict(BOOK, maker_fee_bps=-1.0, tick=101)
    assert v["suppress"] is False


# ------------------------------------------------------------- activation

def test_banner_reports_behaviour_change_one():
    a = _agent()
    a._a192_check_activation(1)
    b = a.rows("A192_ACTIVATION_BANNER")[0]
    assert b["a192_behaviour_change"] == 1
    assert b["a192_phase"] == NS["DIRECT_A192_PHASE_BEHAVIOURAL"]


def test_banner_reports_the_disabled_phase_honestly():
    a = _agent(enabled=False)
    a._a192_check_activation(1)
    b = a.rows("A192_ACTIVATION_BANNER")[0]
    assert b["a192_behaviour_change"] == 0
    assert b["a192_phase"] == NS["DIRECT_A192_PHASE_DISABLED"]


def test_banner_is_emitted_once():
    a = _agent()
    a._a192_check_activation(1)
    a._a192_check_activation(2)
    assert len(a.rows("A192_ACTIVATION_BANNER")) == 1


def test_alarm_fires_when_the_gate_is_inert():
    """The A1.9.1 failure: candidates accumulate, nothing is ever suppressed."""
    a = _agent()
    a._a192_candidates = _Agent.A192_ACTIVATION_ALARM_CANDIDATES
    a._a192_suppressions = 0
    a._a192_check_activation(50)
    assert a.rows("A192_ACTIVATION_ALARM")


def test_alarm_stays_silent_while_the_gate_acts():
    a = _agent()
    a._a192_candidates = _Agent.A192_ACTIVATION_ALARM_CANDIDATES
    a._a192_suppressions = 3
    a._a192_check_activation(50)
    assert not a.rows("A192_ACTIVATION_ALARM")


def test_alarm_stays_silent_when_deliberately_disabled():
    a = _agent(enabled=False)
    a._a192_candidates = 999
    a._a192_check_activation(50)
    assert not a.rows("A192_ACTIVATION_ALARM")


# ------------------------------------- A1.9.1.2 telemetry debt, now repaired

def test_exchange_ack_uses_the_notice_clock_not_the_tick_counter():
    """`self._tick` has not advanced when notices are ingested, so the field
    reported 0 on all 110 identity releases while the truth was 1 on 109."""
    body = SRC.split("def _a191_release_reprice_ownership")[1].split("\n    def ")[0]
    assert "_a19_notice_tick" in body
    assert 'self._a19_notice_tick = int(tick)' in SRC


def test_identity_ack_suppresses_the_duplicate_ledger_ack():
    """Every identity release also emitted a LEDGER_SETTLE ack one tick later:
    99 of 99 overlapped, inflating reprice ack counts ~2x."""
    a = _Agent()
    a._tick = 51
    a._a19_cancel_watch[(BOOK, 900)] = (50, "REPRICE_CANCEL")
    a._a19_identity_acked.add(900)
    a._a19_settle_cancel_watch(BOOK, live_ids=set())
    assert a.rows("A19_CANCEL_ACK") == []
    assert a._a19_duplicate_acks_suppressed == 1


def test_a_cancel_not_already_acked_still_reports_the_ledger_settle():
    """The suppression must be narrow: unclaimed cancels keep their record."""
    a = _Agent()
    a._tick = 51
    a._a19_cancel_watch[(BOOK, 901)] = (50, "ENTRY_QUOTE_CANCEL")
    a._a19_settle_cancel_watch(BOOK, live_ids=set())
    ack = a.rows("A19_CANCEL_ACK")
    assert len(ack) == 1
    assert ack[0]["release_path"] == "LEDGER_SETTLE"
    assert a._a19_duplicate_acks_suppressed == 0


def test_the_identity_claim_is_consumed_so_a_later_cancel_is_not_swallowed():
    """Exchange order ids are reused across restarts; a stale claim must not
    silence a genuine future ack for the same id."""
    a = _Agent()
    a._tick = 51
    a._a19_identity_acked.add(902)
    a._a19_cancel_watch[(BOOK, 902)] = (50, "REPRICE_CANCEL")
    a._a19_settle_cancel_watch(BOOK, live_ids=set())
    assert a.rows("A19_CANCEL_ACK") == []
    a._a19_cancel_watch[(BOOK, 902)] = (52, "REPRICE_CANCEL")
    a._tick = 53
    a._a19_settle_cancel_watch(BOOK, live_ids=set())
    assert len(a.rows("A19_CANCEL_ACK")) == 1


def test_new_telemetry_counters_are_reported():
    for key in (
        "direct_a192_entries_suppressed", "direct_a192_entries_admitted",
        "direct_a192_suppression_pct", "direct_a192_cap_blocks",
        "direct_a192_rebate_admits", "direct_a192_books_quarantined",
        "direct_a192_recoveries", "direct_a192_behaviour_change",
        "direct_a192_activation_alarm", "direct_a19_duplicate_acks_suppressed",
    ):
        assert f'stats["{key}"]' in SRC, key


def test_launcher_refuses_an_a192_build_without_the_switch():
    sh = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
    assert 'research_a192_book_risk_admission_enabled=1' in sh
    guard = sh.split("A192_BUILD:-0")[1]
    assert '"$PARAMS" ==' in guard        # by value, not by grepping the script


def test_launcher_reachability_guard_matches_the_call_not_the_definition():
    """Grepping the bare name matches `def _a192_admission_verdict(` and can
    never fail, so a build whose gate is unreachable would pass preflight."""
    sh = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
    assert r"self\._a192_admission_verdict(" in sh
    assert "grep -q '_a192_admission_verdict('" not in sh
