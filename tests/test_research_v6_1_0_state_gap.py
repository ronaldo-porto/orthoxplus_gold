"""v6.1: a state the validator never sent takes its fills with it -- repair from venue truth.

Measured on mainnet UID 34 (v6.0.2, log 20260917_214432, 2026-09-18).  From tick 3,795 (06:35:39
wall) the validator delivered 147 states twice (a 0 s clock step), each followed by a skipped
state (a 2 s step).  Every trade notice that arrived was applied once, keyed by (book, trade id);
the fills lost were the ones inside the skipped states.  Book 72: BUY 0.25 @ 202.90 (order
5843308) placed at sim 77,854.08, state 77,856 never sent, our cancel answered "Order IDs 5843308
do not exist.", no trade notice anywhere, venue +0.25.  By tick 8,075 the venue held 15.4 BASE on
39 books the tracker did not know about -- the validator's own per-book balances agree -- while
the A1.9.5 reconcile said "0 unresolved".  Testnet never skipped a state.

The repair reuses the A1.9.9 reseed.  These tests run the real methods in a harness -- including
`_a199_service_resync`, `plan_book_reseed` and `_a199_apply_reseed` for the end-to-end case.
"""
import textwrap
import typing
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace

import pytest

import research_direct_risk_state as rs
import research_direct_session_epoch as se
import research_v5_dust_liveness as dl
import research_v61_state_gap as sg
from research_session_state import extract_simulation_id
from _harness import extractor, legacy_capability_attrs

ROOT = Path(__file__).parents[1]
SIMPLE = (ROOT / "agents" / "strategy" / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")

S = 1_000_000_000
T = 77_855 * S          # the state before book 72's lost fill
EPS = 5e-5

GAP = ("_v61_gap_on", "_v61_gap_count", "_v61_observe_state_step", "_v61_service_gap_repair",
       "_v61_note_gap_repair", "_v61_gap_telemetry", "_v61_memo_on")
RESYNC = ("_a199_service_resync", "_a199_apply_reseed", "_a199_close_resync", "_a199_pending_table",
          "_a199_note_transition", "_a199_resync_active", "_a199_entry_blocked", "_a199_strip_resync_exposure",
          "_v502_clip_tolerance", "_v502_add_residue", "_v502_count", "_v502_residue_abs",
          "_v600_tolerance", "_v61_on", "_v61_count", "_v61_restored_side")


class _ExitLedger:
    def reset(self):
        return 0


class _Agent:
    def __init__(self):
        self.rows = []
        self._tick = 0
        self._open_positions = defaultdict(lambda: {"longs": deque(), "shorts": deque()})
        self._a196_legacy_dust_ledger = {}
        self._a1961_fee_residue = {}
        self._a1961_pending_seed = {}
        self._research_exchange_min_order_size = 0.25
        self._a199_epoch_reseeds = 0
        self.venue = {}
        self.mids = {}

    def _emit(self, event_type, force=False, **payload):
        self.rows.append((event_type, payload))

    def rows_of(self, event_type):
        return [payload for kind, payload in self.rows if kind == event_type]

    def _execution_flat_epsilon(self):
        return EPS

    def _a19_ledger_ref(self):
        return _ExitLedger()

    def _a196_volume_decimals(self, state=None):
        return 4

    def _a196_ledger_enabled(self):
        return True

    def _position_tracker_snapshot(self, book_id):
        pos = self._open_positions.get(book_id)
        if not pos:
            return SimpleNamespace(net_qty=0.0)
        return SimpleNamespace(net_qty=sum(q for _, q, _, _ in pos["longs"]) - sum(q for _, q, _, _ in pos["shorts"]))

    def _a195_venue_net_by_book(self, books):
        return {b: self.venue[b] for b in books if b in self.venue}

    def _a195_mid_by_book(self, books):
        return {b: self.mids[b] for b in books if self.mids.get(b)}


NAMESPACE = {name: getattr(module, name) for module in (se, rs, dl) for name in dir(module)
             if not name.startswith("__")}
NAMESPACE.update(
    extract_simulation_id=extract_simulation_id, Any=typing.Any,
    V61_DEFAULT_STEP_NS=sg.DEFAULT_STEP_NS, V61_STEP_GAP=sg.STEP_GAP, V61_STEP_REPEAT=sg.STEP_REPEAT,
    V61_DIVERGENCE_CHECK_EVERY_TICKS=sg.V61_DIVERGENCE_CHECK_EVERY_TICKS,
    V61_GAP_STATE_EVERY_TICKS=sg.V61_GAP_STATE_EVERY_TICKS, V61_STATE_GAP_VERSION=sg.V61_STATE_GAP_VERSION,
    v61_classify_state_step=sg.classify_state_step, v61_confirmed_divergence=sg.confirmed_divergence,
    v61_diverged_books=sg.diverged_books,
)


def _agent(*, on=True, extra=()):
    attrs = {}
    for name in GAP + tuple(extra):
        local = dict(NAMESPACE)
        exec("from __future__ import annotations\n" + textwrap.dedent(_method_source(name)), local)
        attrs[name] = local[name]
    attrs.update(legacy_capability_attrs(skip=set(attrs)))
    agent = type("Harness", (_Agent,), attrs)()
    agent.research_v61_state_gap_repair = on
    agent._v61_gap_counts = {}
    agent._v61_last_state_ts = None
    agent._v61_gap_pending = None
    agent._v61_gap_window = None
    agent._v61_diverged_prev = set()
    agent._v61_standing_new = []
    agent._v61_last_gap = {}
    agent._v61_gap_state_reported = False
    agent._v61_gap_errors = 0
    agent._direct_v61_state_gaps = 0
    agent._direct_v61_state_repeats = 0
    agent._direct_v61_gap_reseeds = 0
    agent._direct_v61_standing_reseeds = 0
    return agent


def _state(ts, books=()):
    return SimpleNamespace(timestamp=ts, config=SimpleNamespace(publish_interval=S),
                           books={b: object() for b in books})


def _feed(agent, *stamps):
    for ts in stamps:
        agent._v61_observe_state_step(_state(ts))


# ---- T1 the pure module --------------------------------------------------------------------------

@pytest.mark.parametrize("prev, ts, expected", [
    (None, T, (sg.STEP_FIRST, 0)),
    (T, T + S, (sg.STEP_NORMAL, 0)),
    (T, T, (sg.STEP_REPEAT, 0)),
    (T, T + 2 * S, (sg.STEP_GAP, 1)),
    (T, T + 3 * S, (sg.STEP_GAP, 2)),
    (T, T + 2 * S + 5, (sg.STEP_GAP, 1)),
    (T, T - 31 * S, (sg.STEP_REWIND, 0)),
])
def test_t1_the_clock_step_is_classified(prev, ts, expected):
    assert sg.classify_state_step(prev, ts, step_ns=S) == expected


def test_t1_a_missing_publish_interval_means_one_second():
    assert sg.classify_state_step(T, T + 2 * S, step_ns=None) == (sg.STEP_GAP, 1)
    assert sg.classify_state_step(T, T + 2 * S, step_ns=0) == (sg.STEP_GAP, 1)


def test_t1_a_book_diverged_by_one_lot_is_found_and_the_fee_residue_is_not():
    venue = {72: 0.2462, 101: 0.000174, 40: 1.1004}
    tracker = {72: -0.0038, 101: 0.0, 40: -0.2517}
    assert sg.diverged_books(venue, tracker, min_order=0.25) == {72, 40}
    assert sg.diverged_books(venue, tracker, min_order=0.0) == set()
    assert sg.diverged_books({3: float("nan")}, {}, min_order=0.25) == set()


def test_t1_a_divergence_is_confirmed_by_the_next_check():
    assert sg.confirmed_divergence({72, 40}, {40, 5}) == {40}
    assert sg.confirmed_divergence(None, {40}) == set()


# ---- T2 the mainnet clock, as UID 34 received it -------------------------------------------------

def test_t2_a_repeat_then_a_gap_arms_the_repair_once():
    agent = _agent()
    agent._tick = 3810
    _feed(agent, T, T, T + 2 * S, T + 3 * S)
    assert agent._direct_v61_state_repeats == 1 and agent._direct_v61_state_gaps == 1
    assert agent._v61_gap_counts == {"repeats": 1, "gaps": 1, "missing_states": 1}
    assert agent._v61_gap_pending == {"tick": 3811, "prev_ts": T, "ts": T + 2 * S, "missing": 1}
    steps = agent.rows_of("V61_STATE_STEP")
    assert [r["step"] for r in steps] == [sg.STEP_REPEAT, sg.STEP_GAP]
    assert steps[1]["repair_armed"] == 1 and steps[1]["missing_states"] == 1


def test_t2_normal_states_arm_nothing_and_emit_nothing():
    agent = _agent()
    _feed(agent, T, T + S, T + 2 * S, T + 3 * S)
    assert agent._v61_gap_pending is None and agent.rows_of("V61_STATE_STEP") == []


def test_t2_off_still_counts_the_defect_but_arms_nothing():
    agent = _agent(on=False)
    _feed(agent, T, T, T + 2 * S)
    assert agent._direct_v61_state_gaps == 1 and agent._direct_v61_state_repeats == 1
    assert agent._v61_gap_pending is None
    assert agent.rows_of("V61_STATE_STEP")[-1]["repair_armed"] == 0


# ---- T3 the repair is one A1.9.9 pass --------------------------------------------------------------

def test_t3_a_gap_opens_the_resync_for_exactly_this_state():
    agent = _agent()
    agent._tick = 3811
    _feed(agent, T, T + 2 * S)
    agent._v61_service_gap_repair(_state(T + 2 * S, books=(72,)))
    assert agent._a199_resync["min_until_tick"] == agent._a199_resync["max_until_tick"] == 3812
    assert agent._a199_resync["cause"] == "V61_STATE_GAP"
    assert agent._v61_gap_pending is None and agent._v61_gap_counts["repairs"] == 1


def test_t3_an_open_rewind_resync_is_left_alone():
    agent = _agent()
    agent._tick = 3811
    rewind = {"since_tick": 3800, "min_until_tick": 3808, "max_until_tick": 3832, "reseeds": 3}
    agent._a199_resync = dict(rewind)
    _feed(agent, T, T + 2 * S)
    agent._v61_service_gap_repair(_state(T + 2 * S))
    assert agent._a199_resync == rewind and agent._v61_gap_counts["gap_inside_resync"] == 1


def test_t3_off_clears_a_pending_gap_and_opens_nothing():
    agent = _agent(on=False)
    agent._v61_gap_pending = {"tick": 3812, "prev_ts": T, "ts": T + 2 * S, "missing": 1}
    agent._v61_service_gap_repair(_state(T + 2 * S))
    assert agent._v61_gap_pending is None and not getattr(agent, "_a199_resync", None)


# ---- T4 end to end: book 72's lost fill, through the real A1.9.9 reseed ----------------------------

def _book72(extra=RESYNC):
    agent = _agent(extra=extra)
    agent._tick = 3811                       # the state after the skip is tick 3,812
    agent.venue = {72: 0.25, 5: 0.0}         # the venue kept the BUY the notice never reported
    agent.mids = {72: 202.9, 5: 100.0}
    return agent


def test_t4_the_lost_fill_is_rebuilt_and_no_entry_is_blocked():
    agent = _book72()
    state = _state(T + 2 * S, books=(72, 5))
    _feed(agent, T, T + 2 * S)
    agent._v61_service_gap_repair(state)
    assert agent._a199_service_resync(state) == 1
    agent._v61_note_gap_repair()
    assert list(agent._open_positions[72]["longs"]) == [(3812, 0.25, 202.9, 0.0)]
    assert not agent._open_positions[5]["longs"] and not agent._open_positions[5]["shorts"]
    assert not agent._a199_resync_active(), "the one-pass window closes before any decision"
    assert not agent._a199_entry_blocked(72) and not agent._a199_entry_blocked(5)
    (repair,) = agent.rows_of("V61_GAP_REPAIR")
    assert repair["reseeds"] == 1 and repair["window_closed"] == 1 and repair["missing_states"] == 1
    (resume,) = agent.rows_of("A199_EPOCH_RESUME")
    assert resume["since_tick"] == 3812 and resume["resync_ticks"] == 1 and resume["reseeds"] == 1
    assert agent._direct_v61_gap_reseeds == 1


def test_t4_without_the_repair_the_tracker_stays_wrong():
    agent = _book72()
    agent.research_v61_state_gap_repair = False
    state = _state(T + 2 * S, books=(72, 5))
    _feed(agent, T, T + 2 * S)
    agent._v61_service_gap_repair(state)
    assert agent._a199_service_resync(state) == 0
    assert not agent._open_positions[72]["longs"], "v6.0.3: the lost fill is never seen"


def test_t4_a_gap_with_nothing_lost_rebuilds_nothing():
    agent = _book72()
    agent.venue = {72: 0.0, 5: 0.0}
    state = _state(T + 2 * S, books=(72, 5))
    _feed(agent, T, T + 2 * S)
    agent._v61_service_gap_repair(state)
    assert agent._a199_service_resync(state) == 0
    agent._v61_note_gap_repair()
    assert agent.rows_of("V61_GAP_REPAIR")[0]["reseeds"] == 0 and not agent._a199_resync_active()


# ---- T5 the standing check: whatever the cause ------------------------------------------------------

def test_t5_a_book_diverged_at_two_consecutive_checks_is_reseeded():
    agent = _book72()
    agent.venue = {40: 0.5, 5: 0.0}
    agent.mids = {40: 511.0, 5: 100.0}
    state = _state(T, books=(40, 5))
    agent._tick = 3824                        # state 3,825: a check
    agent._v61_service_gap_repair(state)
    assert agent._v61_diverged_prev == {40} and not getattr(agent, "_a199_deferred", None)
    agent._tick = 3849                        # state 3,850: the next check, still diverged
    agent._v61_service_gap_repair(state)
    assert agent._a199_deferred[40]["cause"] == "V61_PERSISTENT_DIVERGENCE"
    assert agent.rows_of("V61_STANDING_DIVERGENCE")[0]["books"] == [40]
    assert agent._a199_service_resync(state) == 1
    agent._v61_note_gap_repair()
    assert sum(q for _, q, _, _ in agent._open_positions[40]["longs"]) == pytest.approx(0.5)
    assert 40 not in agent._a199_deferred and agent._direct_v61_standing_reseeds == 1


def test_t5_a_divergence_that_resolves_itself_is_not_touched():
    agent = _book72()
    agent.venue = {40: 0.5}
    agent._tick = 3824
    agent._v61_service_gap_repair(_state(T, books=(40,)))
    agent.venue = {40: 0.0}
    agent._tick = 3849
    agent._v61_service_gap_repair(_state(T, books=(40,)))
    assert not getattr(agent, "_a199_deferred", None) and agent._v61_diverged_prev == set()


def test_t5_only_every_25th_state_is_checked():
    agent = _book72()
    agent.venue = {40: 0.5}
    for tick in range(3825, 3848):
        agent._tick = tick
        agent._v61_service_gap_repair(_state(T, books=(40,)))
    assert agent._v61_diverged_prev == set() and "checks" not in agent._v61_gap_counts


# ---- T6 telemetry, wiring, launcher -----------------------------------------------------------------

def test_t6_the_state_row():
    agent = _agent()
    agent._tick = 3900
    agent.research_v61_request_memo = True
    _feed(agent, T, T, T + 2 * S)
    agent._v61_gap_telemetry(_state(T))
    row = agent.rows_of("V61_GAP_STATE")[0]
    assert row["enabled"] == 1 and row["gaps"] == 1 and row["repeats"] == 1 and row["errors"] == 0
    assert row["v61_state_gap_version"] == sg.V61_STATE_GAP_VERSION and row["request_memo"] == 1


def test_t6_source_wiring():
    update = SIMPLE[SIMPLE.index("    def update(self, state) -> None:"):]
    update = update[:update.index("return super().update(state)")]
    assert update.index("self._v61_observe_state_step(state)") < update.index("self._a199_observe_epoch(state)")
    assert (SIMPLE.index("self._v61_service_gap_repair(state)")
            < SIMPLE.index("            self._a199_service_resync(state)")
            < SIMPLE.index("self._v61_note_gap_repair()"))
    assert "from research_v61_state_gap import (" in SIMPLE
    for stat in ("direct_v61_state_gaps", "direct_v61_state_repeats", "direct_v61_gap_reseeds",
                 "direct_v61_standing_reseeds", "direct_v61_state_gap_repair"):
        assert SIMPLE.count(f'"{stat}"') >= 2, stat


def test_t6_launcher():
    body = "\n".join(ln for ln in LAUNCHER.splitlines() if not ln.lstrip().startswith("#"))
    assert "research_v61_state_gap_repair=1" in body
    assert "tests/test_research_v6_1_0_state_gap.py" in body
    assert "[preflight] v6.1 state-gap repair PASS" in body
