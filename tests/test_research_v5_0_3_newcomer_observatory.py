"""v5.0.3: the newcomer quiet gate (G1), the observatory (G2/G4) and the validator's FIFO (G3).

The measurements these are built on, all from UID 18's own RealNet run (log 20260915_063311) and
from upstream taos-im/sn-79 main (0.6.1):

  * ``apply_track_record_ema`` seeds a uid's standing at its FIRST NON-ZERO trading score, at k=0
    where alpha is 1.  Everything after that moves it by at most 1/(k+1).
  * ``trading = 0.79*kappa + 0.21*pnl``.  The PnL leg goes non-zero once more than half the scored
    books carry realized PnL -- 40 books by tick 203 on that run -- while ``kappa_3`` returns None,
    and so ``calculate_kappa_score`` returns 0.0, until the stored rounds span 5,400 sim-s.
  * UID 18 therefore seeded its standing at ~0.0001 and its weight and emission were exactly 0 for
    its entire immunity window.
  * ``match_trade_fifo`` prorates a fill's fee to the part of it that closes a lot.  This agent's
    inherited copy charged the whole fill's fee there; at this venue's maker rebate (median -73 bps)
    that overstates realized PnL.  That branch fired on 247 of 2,247 reducing fills (11.0%), worth
    about +114 at the median fill fee -- roughly half the 212 by which the agent's ledger (+1,803)
    ran ahead of the validator's FIFO (+1,591).  The rest of that gap is still unexplained, so
    these tests pin the ARITHMETIC, which is certain, and not the share, which is not.
"""
import ast
import io
import os
import tempfile
from pathlib import Path
from types import SimpleNamespace

import research_v5_newcomer_gate as ng
import research_v5_observatory as obs
import research_v5_validator_fifo as vf
from collections import deque
from research_v5_activity import EVIDENCE_FIRST_STATE, EVIDENCE_OBSERVATION, KAPPA_MIN_LOOKBACK_NS, SCORING_INTERVAL_NS
from research_v5_score_mirror import mirror_score
from _harness import extractor

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
STRATEGY1 = (STRATEGY / "Strategy1.py").read_text()
GATE = (STRATEGY / "research_v5_newcomer_gate.py").read_text()
FIFO = (STRATEGY / "research_v5_validator_fifo.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()

SECOND = 1_000_000_000
_TREES = {}


# This suite also extracts from other agent files, so the class name is not pinned --
# the same "any class in the text handed to it" rule its own copy used.
_method_source = extractor(SIMPLE)


def _positions(longs=(), shorts=()):
    return {"longs": deque(longs), "shorts": deque(shorts)}


# ---- G1: the seed trap this gate exists to close --------------------------------------------------

def test_the_pnl_leg_is_nonzero_long_before_kappa_can_be():
    """The trap itself, through the repo's own mirror: a full-breadth agent has a non-zero trading
    score -- and so seeds its standing -- while its Kappa is still None."""
    start = 1_000 * SECOND
    rounds = [start + i * SECOND for i in range(600)]          # 600 s of history: under min_lookback
    history = {}
    for i, ts in enumerate(rounds[:200]):
        # 41 books realize PnL, which is what makes the PnL median non-zero.
        history[ts] = {book: 5.0 for book in range((i % 41) + 1) if book < 41}
    mirror = mirror_score(
        history, rounds, now_ts=rounds[-1], book_count=128, miner_wealth=50_000.0,
        grace_period_ns=600 * SECOND, volume_decimals=4,
    )
    assert mirror.kappa_available is False and mirror.kappa_score == 0.0
    assert mirror.pnl_score > 0.0
    assert mirror.trading_score > 0.0, "a PnL-only score is exactly what seeds the standing"


def test_the_gate_is_the_validators_own_arithmetic():
    anchor = 7 * SECOND
    gate = ng.gate_timestamp(
        anchor, min_lookback_ns=KAPPA_MIN_LOOKBACK_NS, scoring_interval_ns=SCORING_INTERVAL_NS,
    )
    assert gate == anchor + KAPPA_MIN_LOOKBACK_NS + SCORING_INTERVAL_NS + ng.V503_GATE_MARGIN_NS
    assert ng.gate_timestamp(None, min_lookback_ns=1, scoring_interval_ns=1) is None
    assert ng.seconds_to_gate(now=gate - 90 * SECOND, gate_ts=gate) == 90.0
    assert ng.seconds_to_gate(now=None, gate_ts=gate) is None


def test_only_a_uid_with_no_history_at_all_is_a_newcomer():
    assert ng.prior_evidence(start_source=EVIDENCE_FIRST_STATE) is None
    assert ng.prior_evidence(observations={4: 2}) == ng.EVIDENCE_OBSERVATIONS
    assert ng.prior_evidence(pnl_history={11: {4: -3.0}}) == ng.EVIDENCE_PNL_HISTORY
    assert ng.prior_evidence(pnl_events={4: [(1, 2.0)]}) == ng.EVIDENCE_PNL_EVENTS
    assert ng.prior_evidence(round_trip_closes=1) == ng.EVIDENCE_ROUND_TRIPS
    # The activity belief moves its start earlier only from a restored observation, which only a uid
    # with a track record has.
    assert ng.prior_evidence(start_source=EVIDENCE_OBSERVATION) == ng.EVIDENCE_RESTORED_START
    # A zero count and an all-zero history are not evidence.
    assert ng.prior_evidence(observations={4: 0}, pnl_history={11: {4: 0.0}}, pnl_events={4: []},
                             round_trip_closes=0, start_source=EVIDENCE_FIRST_STATE) is None


def test_the_gate_refuses_to_arm_in_every_unsafe_direction():
    armed, reason = ng.arm_decision(switch_on=True, restored_open=False, evidence=None, gate_ts=99)
    assert armed is True and reason is None
    assert ng.arm_decision(switch_on=False, restored_open=False, evidence=None, gate_ts=99) == (False, ng.OPEN_SWITCH_OFF)
    assert ng.arm_decision(switch_on=True, restored_open=True, evidence=None, gate_ts=99) == (False, ng.OPEN_RESTORED)
    assert ng.arm_decision(switch_on=True, restored_open=False, evidence=ng.EVIDENCE_OBSERVATIONS,
                           gate_ts=99) == (False, ng.EVIDENCE_OBSERVATIONS)
    assert ng.arm_decision(switch_on=True, restored_open=False, evidence=None, gate_ts=None) == (False, ng.OPEN_NO_GATE)


def test_an_armed_gate_opens_on_time_and_immediately_on_any_exposure():
    gate = 10_000 * SECOND
    assert ng.should_open(now=gate - SECOND, gate_ts=gate, exposure=0.0) is None
    assert ng.should_open(now=gate, gate_ts=gate, exposure=0.0) == ng.OPEN_GATE_REACHED
    assert ng.should_open(now=gate + SECOND, gate_ts=gate, exposure=0.0) == ng.OPEN_GATE_REACHED
    # Exposure wins over the clock: a position is always managed.
    assert ng.should_open(now=0, gate_ts=gate, exposure=0.25) == ng.OPEN_EXPOSURE
    assert ng.should_open(now=0, gate_ts=None, exposure=0.0) == ng.OPEN_NO_GATE


def test_exposure_ignores_only_what_the_execution_epsilon_ignores():
    nets = {1: 0.25, 2: -0.25, 3: 0.00001, 4: 0.0, 5: "x"}
    assert ng.exposure_abs(nets, eps=0.00005) == 0.5
    assert ng.exposure_abs(nets, eps=0.0) > 0.5
    assert ng.exposure_abs(None, eps=0.0) == 0.0


def test_a_restart_inside_the_window_cannot_restart_the_clock():
    """The persisted anchor is earlier than the restart's first state, so it wins -- the same
    earliest-evidence rule the activity belief uses."""
    registration, restart = 100 * SECOND, 4_000 * SECOND
    assert ng.effective_anchor(registration, restart) == registration
    assert ng.effective_anchor(None, restart) == restart
    assert ng.effective_anchor(registration, None) == registration
    assert ng.effective_anchor(None, None) is None
    # And an opened gate stays open across the restart.
    row = ng.session_state(opened=True, anchor=registration, open_reason=ng.OPEN_GATE_REACHED)
    assert ng.restored_session(row) == (True, registration)
    assert ng.restored_session(None) == (False, None)
    assert ng.restored_session({"opened": "nonsense"}) == (False, None)


def test_a_save_before_the_first_decision_cannot_persist_the_gate_as_open():
    """The session file is written on the base's own cadence, possibly before the first request has
    evaluated the gate.  Persisting 'opened' from an unevaluated gate would stop a restart from ever
    arming it, which is the one failure that costs a registration."""
    source = _method_source("_v503_gate_session_state")
    assert "opened = armed is False and reason in (OPEN_GATE_REACHED, OPEN_EXPOSURE, OPEN_RESTORED)" in source
    assert "anchor=None if armed is None else" in source
    # The pure helper does what the method relies on.
    assert ng.restored_session(ng.session_state(opened=False, anchor=None, open_reason=None)) == (False, None)
    assert ng.restored_session(ng.session_state(opened=True, anchor=5, open_reason=ng.OPEN_GATE_REACHED)) == (True, 5)


def test_a_new_simulation_moves_the_gate_and_a_checkpoint_rewind_only_delays_it():
    """The state clock resets to ~0 at a simulation boundary while the validator rebases its stored
    rounds onto the new clock, so a gate fixed at evaluation would otherwise hold forever."""
    source = _method_source("_v503_gate_response")
    assert "if last is not None and now <= last - REBASE_MIN_JUMP_NS:" in source
    assert "self._v503_gate_ts += shift" in source and "self._v503_gate_anchor_ts += shift" in source
    assert "self._v503_gate_last_now = now" in source
    # The same threshold the activity belief uses, so both move together.
    assert "REBASE_MIN_JUMP_NS" in _method_source("_v501_refresh")


def test_the_reported_state_names_what_the_gate_is_doing():
    assert ng.gate_state(armed=True, open_reason=None) == ng.GATE_QUIET
    assert ng.gate_state(armed=False, open_reason=ng.OPEN_GATE_REACHED) == ng.GATE_OPEN
    assert ng.gate_state(armed=False, open_reason=ng.OPEN_EXPOSURE) == ng.GATE_OPEN
    assert ng.gate_state(armed=False, open_reason=ng.EVIDENCE_OBSERVATIONS) == ng.GATE_UNARMED
    assert ng.gate_state(armed=False, open_reason=ng.OPEN_SWITCH_OFF) == ng.GATE_UNARMED


# ---- G3: the validator's FIFO ---------------------------------------------------------------------

def test_a_partial_close_prorates_the_fill_fee():
    """The one divergence: 0.5 closes a 0.25 long, so only half the fill's fee belongs to the close."""
    positions = _positions(longs=[(1, 0.25, 100.0, -0.10)])
    realized, volume = vf.match_trade_fifo(
        positions, is_buy=False, quantity=0.5, price=101.0, fee=-0.20, timestamp=2,
    )
    # price 1.0 * 0.25, minus the opening fee (-0.10) and half the closing fee (-0.10).
    assert round(realized, 10) == round(0.25 + 0.10 + 0.10, 10)
    assert volume == 0.25
    # The other half of the fill opens a short carrying the other half of the fee.
    assert len(positions["shorts"]) == 1
    assert round(positions["shorts"][0][3], 10) == -0.10


def test_the_whole_fee_is_never_counted_twice():
    """The inherited arithmetic charged the whole fill fee to the close AND prorated it again onto
    the lot the same fill opened."""
    inherited = _positions(longs=[(1, 0.25, 100.0, -0.10)])
    old_realized = None
    # Inherited: close_fee = fee (the whole -0.20), remainder still opens with a prorated fee.
    old_realized = 0.25 + 0.10 + 0.20
    exact = _positions(longs=[(1, 0.25, 100.0, -0.10)])
    new_realized, _ = vf.match_trade_fifo(
        exact, is_buy=False, quantity=0.5, price=101.0, fee=-0.20, timestamp=2,
    )
    assert new_realized < old_realized, "a rebate counted twice overstates realized PnL"
    assert round(old_realized - new_realized, 10) == 0.10
    assert inherited["longs"]  # untouched: the comparison is arithmetic, not a second run


def test_a_full_close_and_an_opening_fill_are_unchanged():
    positions = _positions(shorts=[(1, 0.25, 100.0, -0.10)])
    realized, volume = vf.match_trade_fifo(
        positions, is_buy=True, quantity=0.25, price=99.0, fee=-0.10, timestamp=2,
    )
    assert round(realized, 10) == round(0.25 + 0.10 + 0.10, 10) and volume == 0.25
    assert not positions["shorts"] and not positions["longs"]
    fresh = _positions()
    assert vf.match_trade_fifo(fresh, is_buy=True, quantity=0.25, price=100.0, fee=-0.1, timestamp=1) == (0.0, 0.0)
    assert fresh["longs"][0] == (1, 0.25, 100.0, -0.1)


def test_several_lots_close_oldest_first():
    positions = _positions(longs=[(1, 0.25, 100.0, 0.0), (2, 0.25, 110.0, 0.0)])
    realized, volume = vf.match_trade_fifo(
        positions, is_buy=False, quantity=0.5, price=105.0, fee=0.0, timestamp=3,
    )
    # FIFO: +5 on the 100 lot, -5 on the 110 lot.
    assert round(realized, 10) == 0.0 and volume == 0.5
    assert not positions["longs"]


# ---- G2: the observatory ---------------------------------------------------------------------------

def test_a_recorded_state_keeps_every_trade_verbatim_with_both_counterparties():
    raw = {
        4: {"i": 4, "b": [{"p": 99.0, "q": 3.0}, {"p": 98.0, "q": 1.0}],
            "a": [{"p": 101.0, "q": 2.0}],
            "e": [{"y": "t", "b": 4, "p": 100.0, "q": 0.25, "s": 0, "Ta": 18, "Ma": 165, "Tf": 0.1, "Mf": -0.2},
                  {"y": "o", "b": 4}]},
        9: {"i": 9, "b": [], "a": [], "e": []},
    }
    row = obs.shape_state(raw, tick=7, ts=123, depth=False)
    assert row["tick"] == 7 and row["ts"] == 123 and row["depth"] == 0 and row["n_trades"] == 1
    trade = row["books"]["4"]["t"][0]
    assert trade["Ta"] == 18 and trade["Ma"] == 165 and trade["p"] == 100.0
    assert "y" in trade and trade["y"] == "t", "kept verbatim, not reshaped"
    # Without depth only the touch is kept; a book with nothing to say is dropped entirely.
    assert row["books"]["4"]["bb"] == {"p": 99.0, "q": 3.0}
    assert row["books"]["4"]["ba"] == {"p": 101.0, "q": 2.0}
    assert "b" not in row["books"]["4"] and "9" not in row["books"]


def test_the_full_ladder_is_kept_only_on_a_depth_state():
    raw = {4: {"i": 4, "b": [{"p": 99.0}, {"p": 98.0}], "a": [{"p": 101.0}], "e": []}}
    row = obs.shape_state(raw, tick=100, ts=1, depth=True)
    assert row["depth"] == 1
    assert row["books"]["4"]["b"] == [{"p": 99.0}, {"p": 98.0}]
    assert row["books"]["4"]["a"] == [{"p": 101.0}]
    assert "bb" not in row["books"]["4"]
    assert obs.depth_due(100, 100) and obs.depth_due(200, 100)
    assert not obs.depth_due(101, 100) and not obs.depth_due(0, 100) and not obs.depth_due(100, 0)


def test_the_recorder_reads_the_lazy_raw_books_and_nothing_else():
    lazy = SimpleNamespace(_raw_books={1: {"i": 1}})
    assert obs.raw_books_of(lazy) == {1: {"i": 1}}
    assert obs.raw_books_of(SimpleNamespace()) is None
    assert obs.raw_books_of({1: object()}) is None, "a parsed mapping is not the raw one"
    recorder = obs.StateRecorder(tempfile.mkdtemp(), uid=18)
    assert recorder.capture(SimpleNamespace(), tick=1, ts=1) is False
    assert recorder.last_skip == obs.SKIP_NO_RAW_BOOKS
    assert recorder.states_captured == 0


def test_the_writer_writes_one_compressed_line_per_state_and_stops_at_its_budget():
    written = []

    class _Sink(io.BytesIO):
        def close(self):
            written.append(self.getvalue())

    recorder = obs.StateRecorder(
        tempfile.mkdtemp(), uid=18, max_bytes=120, opener=lambda path, mode: _Sink(),
    )
    raw = {4: {"i": 4, "b": [{"p": 1.0}], "a": [], "e": [{"y": "t", "Ta": 1, "Ma": 2}]}}
    recorder._write((1, 10, raw, False))
    assert recorder.states_written == 1 and recorder.trades_written == 1
    assert recorder.bytes_written > 0
    while not recorder.stopped_reason and recorder.states_written < 40:
        recorder._write((recorder.states_written + 1, 10, raw, False))
    assert recorder.stopped_reason == obs.STOP_BUDGET
    assert recorder.bytes_written >= 120
    assert written and written[0].endswith(b"\n")
    assert recorder.capture(SimpleNamespace(_raw_books=raw), tick=2, ts=2) is False


def test_a_full_queue_drops_the_state_instead_of_the_tick():
    recorder = obs.StateRecorder(tempfile.mkdtemp(), uid=18, queue_size=1)
    recorder._thread = SimpleNamespace(is_alive=lambda: True)     # no writer thread in a test
    books = SimpleNamespace(_raw_books={1: {"i": 1, "e": []}})
    assert recorder.capture(books, tick=1, ts=1) is True
    assert recorder.capture(books, tick=2, ts=2) is False
    assert recorder.dropped == 1 and recorder.last_skip == obs.SKIP_QUEUE_FULL
    snap = recorder.snapshot()
    assert snap["states_captured"] == 1 and snap["dropped"] == 1 and snap["queued"] == 1


# ---- wiring ---------------------------------------------------------------------------------------

def test_v5_0_3_is_wired_and_launched():
    respond = _method_source("respond")
    assert respond.index("quiet = self._v503_gate_response(state)") < respond.index(
        "response = super().respond(state) if quiet is None else quiet"
    ), "the gate must decide before the frozen chain builds anything"
    assert respond.index("self._v502_service(state)") < respond.index("self._v503_service(state)")
    gate = _method_source("_v503_gate_response")
    assert "FinanceAgentResponse(agent_id=int(getattr(self, \"uid\", 0) or 0))" in gate
    assert "self._research_save_session(force=True)" in gate, "an opened gate is persisted at once"
    assert "reason = should_open(" in gate
    evaluate = _method_source("_v503_gate_evaluate")
    assert "armed, reason = arm_decision(" in evaluate and "prior_evidence(" in evaluate
    assert "restored_session(" in evaluate
    fifo = _method_source("_match_trade_fifo")
    assert "return super()._match_trade_fifo(" in fifo, "the switch off restores the inherited matcher"
    assert "v503_match_trade_fifo(" in fifo
    service = _method_source("_v503_service")
    assert "recorder.capture(" in service
    assert "payload[V503_NEWCOMER_GATE_VERSION] = self._v503_gate_session_state()" in _method_source(
        "_research_save_session"
    )
    for switch in ("research_v503_newcomer_gate", "research_v503_state_recorder",
                   "research_v503_fifo_fee_exact", "research_v503_book_kappa_rows"):
        assert f'self.{switch} = self._as_bool(\n            getattr(self.config, "{switch}", True)' in SIMPLE, switch
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE
    assert ng.V503_NEWCOMER_GATE_VERSION.endswith("v5_0_3")
    assert obs.V503_OBSERVATORY_VERSION.endswith("v5_0_3")
    assert vf.V503_VALIDATOR_FIFO_VERSION.endswith("v5_0_3")
    for key in ("direct_v503_newcomer_gate", "direct_v503_gate_state", "direct_v503_gate_quiet_requests",
                "direct_v503_fifo_fee_exact", "direct_v503_fifo_calls", "direct_v503_state_recorder",
                "direct_v503_states_written", "direct_v503_trades_written", "direct_v503_recorder_dropped",
                "direct_v503_gate_errors", "direct_v503_recorder_errors"):
        assert f'stats["{key}"]' in SIMPLE, key
    assert ("strategy1_direct_v5_0_3) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; "
            "A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; "
            "V502_BUILD=1; V503_BUILD=1 ;;") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    for switch in ("research_v503_newcomer_gate=1", "research_v503_state_recorder=1",
                   "research_v503_fifo_fee_exact=1", "research_v503_book_kappa_rows=1", "lazy_load=1"):
        assert switch in params, switch
    assert "tests/test_research_v5_0_3_newcomer_observatory.py" in LAUNCHER
    assert "[preflight] v5.0.3 newcomer gate + observatory PASS" in LAUNCHER
    guards = {
        "quiet = self._v503_gate_response(state)": SIMPLE,
        "response = super().respond(state) if quiet is None else quiet": SIMPLE,
        "armed, reason = arm_decision(": SIMPLE,
        "reason = should_open(": SIMPLE,
        "close_fee = fee * remaining_qty * quantity_inv": FIFO,
        "recorder.capture(": SIMPLE,
    }
    for literal, source in guards.items():
        assert literal in source and literal in LAUNCHER, literal


def test_the_frozen_baseline_is_untouched_and_the_pure_helpers_are_pure():
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in RESEARCH
    assert "v503" not in RESEARCH and "v503" not in STRATEGY1
    for text, allowed in (
        (GATE, {"__future__", "typing"}),
        (FIFO, {"__future__", "typing"}),
    ):
        imported = {node.module for node in ast.walk(ast.parse(text)) if isinstance(node, ast.ImportFrom)}
        assert imported == allowed, imported
    # The recorder owns I/O, so it may import the standard library it needs -- and nothing else.
    recorder_tree = ast.parse((STRATEGY / "research_v5_observatory.py").read_text())
    modules = {node.module for node in ast.walk(recorder_tree) if isinstance(node, ast.ImportFrom)}
    plain = {alias.name for node in ast.walk(recorder_tree) if isinstance(node, ast.Import)
             for alias in node.names}
    assert modules == {"__future__", "typing"}
    assert plain == {"gzip", "json", "os", "queue", "threading", "time"}, plain


def test_every_switch_off_restores_v5_0_2():
    """Each mechanism is independently reversible, which is what makes the build attributable."""
    assert ng.arm_decision(switch_on=False, restored_open=False, evidence=None, gate_ts=1)[0] is False
    assert "if not bool(getattr(self, \"research_v503_fifo_fee_exact\", True)):" in _method_source("_match_trade_fifo")
    recorder_handle = _method_source("_v503_recorder_handle")
    assert "if not bool(getattr(self, \"research_v503_state_recorder\", True)):" in recorder_handle
    assert "return None" in recorder_handle
    assert 'bool(getattr(self, "research_v503_book_kappa_rows", True))' in _method_source("_v503_service")
