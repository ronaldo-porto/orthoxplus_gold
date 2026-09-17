"""v5.0.4: whose session this is (H1), when the validator began the UID's history (H2), a recorder
budget that counts the disk (H3), and a score copy over the validator's rounds (H4).

The measurements these are built on, all from 2026-09-16:

  * UID 125 was launched from the tree and research directory UID 18 had used.  The session file is
    named ``research_session_unknown_79_20260913_0722.json`` -- network, subnet, simulation, no UID --
    so UID 125 restored UID 18's evidence.  Its quiet gate logged UNARMED / OBSERVATIONS at tick 0,
    and its activity belief started at UID 18's earliest observation (sim 37,198 s).
  * The validator opened UID 125's Kappa window at sim 52,805 s = its registration (47,405 s) plus
    5,400 s; its first request came at 47,988 s.  ``trade._process_uid_trade_volumes`` writes a round
    for every UID on every state, answered or not.
  * The recorder counted about 197 KB of JSON a state against about 37 KB written by gzip.
  * States arrive every 1.000 sim-s.  Bucketing each stored per-fill event at the first state strictly
    after it rebuilds all 4,312 observation stamps in UID 125's session file.
"""
import ast
import gzip
import json
import os
import random
import tempfile
import textwrap
import typing
from pathlib import Path
from types import SimpleNamespace

import research_v5_activity as act
import research_v5_mirror_rounds as mr
import research_v5_newcomer_gate as ng
import research_v5_observatory as obs
import research_v5_session_identity as si
from research_v5_score_mirror import mirror_score

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
IDENTITY = (STRATEGY / "research_v5_session_identity.py").read_text()
ROUNDS = (STRATEGY / "research_v5_mirror_rounds.py").read_text()
OBSERVATORY = (STRATEGY / "research_v5_observatory.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()

SECOND = 1_000_000_000
SIM = "20260913_0722"


def _method_source(name):
    for node in ast.walk(ast.parse(SIMPLE)):
        if isinstance(node, ast.ClassDef) and node.name == "Strategy1_Research_Simple":
            defs = [ast.get_source_segment(SIMPLE, n) for n in node.body
                    if isinstance(n, ast.FunctionDef) and n.name == name]
            assert defs, name
            return defs[-1]
    raise AssertionError(name)


def _constant(name):
    for node in ast.parse(SIMPLE).body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(name)


def _harness(base, names, namespace):
    # Compiled inside a class body so the methods' zero-argument super() resolves.
    body = "".join(textwrap.indent(textwrap.dedent(_method_source(n)), "    ") + "\n" for n in names)
    scope = dict(namespace, _Base=base, Any=typing.Any, os=os, json=json)
    exec("from __future__ import annotations\nclass Harness(_Base):\n" + body, scope)
    return scope["Harness"]


# ---- H1: the session file belongs to one UID -------------------------------------------------------

def test_the_session_file_name_carries_the_uid():
    legacy = "/logs/research_session_unknown_79_20260913_0722.json"
    assert si.uid_session_path(legacy, 125) == "/logs/research_session_unknown_79_20260913_0722.uid125.json"
    assert si.uid_session_path(legacy, 18) != si.uid_session_path(legacy, 125)
    assert si.uid_session_path(legacy, None).endswith(".uidna.json")
    assert si.uid_session_path(legacy, -1).endswith(".uidna.json")
    assert si.uid_session_path(legacy, True).endswith(".uidna.json"), "a bool is not a UID"


def test_a_payload_says_whose_it_is():
    mine = {"schema": 1, si.OWNER_KEY: si.owner_record(125)}
    theirs = {"schema": 1, si.OWNER_KEY: si.owner_record(18)}
    legacy = {"schema": 1, "observations": {"4": 3}}
    assert si.owner_verdict(mine, 125) == si.OWNER_OWN
    assert si.owner_verdict(theirs, 125) == si.OWNER_FOREIGN
    assert si.owner_verdict(legacy, 125) == si.OWNER_UNOWNED
    assert si.owner_verdict(None, 125) == si.OWNER_MISSING
    assert si.owner_verdict({"schema": "invalid"}, 125) == si.OWNER_INVALID
    assert si.owner_verdict(mine, None) == si.OWNER_UNOWNED, "a process without a UID owns nothing"


def test_the_uid_125_incident_no_longer_restores_another_uids_evidence():
    """UID 125's own file does not exist; the shared legacy file holds UID 18's observations."""
    legacy = {"schema": 1, "observations": {"4": 3}, "rolling_observation_timestamps": {"4": [37_198 * SECOND]}}
    choice = si.choose_session(None, uid=125, legacy=None, legacy_exists=True, mode=si.LEGACY_IGNORE)
    assert choice.payload is None and choice.source == si.SOURCE_LEGACY_IGNORED
    # With nothing restored, the gate sees no prior evidence and can arm.
    assert ng.prior_evidence(observations={}, start_source=act.EVIDENCE_FIRST_STATE) is None
    # The operator can still say the legacy file is this UID's.
    adopted = si.choose_session(None, uid=125, legacy=legacy, legacy_exists=True, mode=si.LEGACY_ADOPT)
    assert adopted.payload is legacy and adopted.source == si.SOURCE_LEGACY_ADOPTED
    assert ng.prior_evidence(observations={4: 3}) == ng.EVIDENCE_OBSERVATIONS


def test_the_choice_covers_every_combination():
    mine = {"schema": 1, si.OWNER_KEY: si.owner_record(7)}
    theirs = {"schema": 1, si.OWNER_KEY: si.owner_record(8)}
    unowned = {"schema": 1}
    bad = {"schema": "invalid"}
    c = si.choose_session
    assert c(mine, uid=7, legacy=theirs, legacy_exists=True, mode=si.LEGACY_ADOPT).payload is mine
    assert c(bad, uid=7).source == si.SOURCE_INVALID and c(bad, uid=7).payload is bad
    assert c(None, uid=7).source == si.SOURCE_NONE and c(None, uid=7).payload is None
    assert c(theirs, uid=7).source == si.SOURCE_FOREIGN_REFUSED and c(theirs, uid=7).owner_uid == 8
    assert c(unowned, uid=7).source == si.SOURCE_UNOWNED_REFUSED
    assert c(unowned, uid=7, mode=si.LEGACY_ADOPT).payload is unowned
    assert c(None, uid=7, legacy=theirs, legacy_exists=True, mode=si.LEGACY_ADOPT).source == si.SOURCE_FOREIGN_REFUSED
    assert c(None, uid=7, legacy=bad, legacy_exists=True, mode=si.LEGACY_ADOPT).source == si.SOURCE_LEGACY_INVALID
    # A v5.0.4 legacy-named file this UID wrote with the switch off is still this UID's.
    assert c(None, uid=7, legacy=mine, legacy_exists=True, mode=si.LEGACY_ADOPT).payload is mine
    assert c(None, uid=7, legacy=None, legacy_exists=True, mode=si.LEGACY_ADOPT).source == si.SOURCE_NONE


def test_only_ignore_and_adopt_are_legacy_modes():
    assert si.legacy_mode(None) == si.LEGACY_IGNORE and si.legacy_mode("") == si.LEGACY_IGNORE
    assert si.legacy_mode(" Adopt ") == si.LEGACY_ADOPT
    assert si.legacy_mode("yes") is None and si.legacy_mode(1.0) is None


class _SessionBase:
    """The base class's session path and read, over a real directory."""

    def __init__(self, directory, uid):
        self.directory = directory
        self.uid = uid
        self.emitted = []
        self._tick = 0
        self.research_v504_session_per_uid = True
        self.research_v504_legacy_session = si.LEGACY_IGNORE

    def _emit(self, kind, force=False, **row):
        self.emitted.append((kind, row))

    def _research_session_path(self, identity):
        return os.path.join(self.directory, f"research_session_unknown_79_{identity}.json")

    def _research_read_session(self, identity):
        path = self._research_session_path(identity)
        if not os.path.isfile(path):
            return None
        with open(path, encoding="utf-8") as handle:
            return json.loads(handle.read())


SESSION_NAMES = dict(
    uid_session_path=si.uid_session_path, choose_session=si.choose_session, LEGACY_IGNORE=si.LEGACY_IGNORE,
    LEGACY_ADOPT=si.LEGACY_ADOPT, new_mirror_state=mr.new_mirror_state,
    mirror_restored_start=mr.restored_start, restored_session=ng.restored_session,
    V504_MIRROR_SESSION_KEY=mr.SESSION_KEY, V503_NEWCOMER_GATE_VERSION=ng.V503_NEWCOMER_GATE_VERSION,
    V504_REGISTRATION_IDENTITY_VERSION=si.V504_REGISTRATION_IDENTITY_VERSION,
)


def test_the_agent_reads_its_own_file_and_leaves_the_legacy_one_alone():
    directory = tempfile.mkdtemp()
    legacy_path = os.path.join(directory, f"research_session_unknown_79_{SIM}.json")
    with open(legacy_path, "w") as handle:
        json.dump({"schema": 1, "observations": {"4": 3}}, handle)
    Harness = _harness(_SessionBase, ("_research_session_path", "_v504_read_session"), SESSION_NAMES)
    agent = Harness(directory, 125)
    own_path = agent._research_session_path(SIM)
    assert own_path.endswith(f"_{SIM}.uid125.json")
    assert agent._v504_read_session(SIM) is None
    (kind, row), = agent.emitted
    assert kind == "V504_SESSION_OWNER" and row["source"] == si.SOURCE_LEGACY_IGNORED and row["legacy_exists"] == 1

    # This UID's own file, with the rounds start and the gate anchor it kept: the earlier one wins.
    with open(own_path, "w") as handle:
        json.dump({
            "schema": 1, si.OWNER_KEY: si.owner_record(125),
            mr.SESSION_KEY: mr.session_state(47_405 * SECOND),
            ng.V503_NEWCOMER_GATE_VERSION: ng.session_state(opened=True, anchor=47_988 * SECOND, open_reason="GATE_REACHED"),
        }, handle)
    payload = agent._v504_read_session(SIM)
    assert payload[si.OWNER_KEY]["uid"] == 125
    assert agent._v504_mirror["restored_start"] == 47_405 * SECOND
    assert agent.emitted[-1][1]["source"] == si.SOURCE_UID_FILE

    # UID 18 launched from the same tree reads neither UID 125's file nor, by default, the legacy one.
    other = Harness(directory, 18)
    assert other._v504_read_session(SIM) is None
    other.research_v504_legacy_session = si.LEGACY_ADOPT
    assert other._v504_read_session(SIM) == {"schema": 1, "observations": {"4": 3}}

    # Switch off: v5.0.3's shared file, read by everyone.
    agent.research_v504_session_per_uid = False
    assert agent._research_session_path(SIM) == legacy_path
    assert agent._v504_read_session(SIM) == {"schema": 1, "observations": {"4": 3}}


def test_the_save_writes_the_owner_and_the_rounds_start():
    save = _method_source("_research_save_session")
    assert "payload[OWNER_KEY] = owner_record(getattr(self, \"uid\", None))" in save
    assert "payload[V504_MIRROR_SESSION_KEY] = mirror_session_state(v504_start)" in save
    assert save.index("payload[V503_NEWCOMER_GATE_VERSION]") < save.index("payload[OWNER_KEY]")
    assert "raw = self._v504_read_session(identity)" in _method_source("_research_read_session")


# ---- H2: the declared history start ---------------------------------------------------------------

def test_the_anchor_forms():
    assert si.parse_history_anchor(None).mode == si.MODE_AUTO
    assert si.parse_history_anchor(" AUTO ").mode == si.MODE_AUTO
    assert si.parse_history_anchor("established").mode == si.MODE_ESTABLISHED
    declared = si.parse_history_anchor("20260913_0722@47405")
    assert declared.mode == si.MODE_DECLARED and declared.simulation_id == SIM
    assert declared.start_ns == 47_405 * SECOND
    assert si.parse_history_anchor("20260913_0722@47405.5").start_ns == 47_405_500_000_000
    # --agent.params turns anything numeric into a float, so a bare number is never a declaration.
    for bad in (47405.0, "47405", "@47405", "sim@", "sim@nan", "sim@inf", "a b@1", "x@y@1", "sim@1e999"):
        assert si.parse_history_anchor(bad).mode == si.MODE_INVALID, bad


def test_the_pin_for_each_declaration():
    first = 47_988 * SECOND
    auto = si.parse_history_anchor("auto")
    assert si.resolve_history_pin(auto, simulation_id=SIM, first_state_ns=first) is None
    declared = si.parse_history_anchor(f"{SIM}@47405")
    pin = si.resolve_history_pin(declared, simulation_id=SIM, first_state_ns=first)
    assert (pin.start_ns, pin.source, pin.established) == (47_405 * SECOND, si.PIN_DECLARED, False)
    # Declared after the first state: the validator was already sending states, so it began no later.
    late = si.resolve_history_pin(si.parse_history_anchor(f"{SIM}@50000"), simulation_id=SIM, first_state_ns=first)
    assert (late.start_ns, late.source) == (first, si.PIN_DECLARED_CLAMPED)
    # A later simulation: the validator shifted and kept the rounds, so the UID is established.
    later = si.resolve_history_pin(declared, simulation_id="20260918_1745", first_state_ns=5 * SECOND)
    assert later.established and later.source == si.PIN_EARLIER_SIMULATION
    assert later.start_ns == 5 * SECOND - si.ESTABLISHED_LEAD_NS
    unknown = si.resolve_history_pin(declared, simulation_id=None, first_state_ns=first)
    assert unknown.start_ns is None and unknown.source == si.PIN_UNKNOWN_SIMULATION
    assert si.resolve_history_pin(declared, simulation_id=SIM, first_state_ns=None) is None
    invalid = si.parse_history_anchor("junk")
    assert si.resolve_history_pin(invalid, simulation_id=SIM, first_state_ns=first) is None


def test_an_established_start_puts_every_windowed_round_trip_past_the_activation_floor():
    first = 86_400 * SECOND
    start = first - si.ESTABLISHED_LEAD_NS
    belief = act.ActivityBelief()
    assert belief.pin(start, si.PIN_ESTABLISHED)
    assert belief.gate_open(first)
    # The oldest observation the 3 h window can still hold is already activated.
    assert belief.floor_ts <= first - si.KAPPA_LOOKBACK_NS
    assert belief.activated([first - si.KAPPA_LOOKBACK_NS])
    # And the newcomer gate, if it were asked, would already be open.
    gate = ng.gate_timestamp(start, min_lookback_ns=act.KAPPA_MIN_LOOKBACK_NS,
                             scoring_interval_ns=act.SCORING_INTERVAL_NS)
    assert ng.should_open(now=first, gate_ts=gate) == ng.OPEN_GATE_REACHED


def test_a_pinned_belief_ignores_evidence_and_still_follows_a_new_simulation():
    belief = act.ActivityBelief()
    belief.pin(47_405 * SECOND, si.PIN_DECLARED)
    assert belief.note_evidence(37_198 * SECOND, act.EVIDENCE_OBSERVATION) is False
    assert belief.note_evidence(47_988 * SECOND, act.EVIDENCE_FIRST_STATE) is False
    assert (belief.history_start_ts, belief.history_start_source) == (47_405 * SECOND, si.PIN_DECLARED)
    belief.rebase(-80_000 * SECOND)
    assert belief.history_start_ts == -32_595 * SECOND and belief.pinned
    assert act.ActivityBelief().pin("x", "y") is False
    # Unpinned, v5.0.1's earliest-evidence rule is unchanged.
    plain = act.ActivityBelief()
    plain.note_evidence(47_988 * SECOND, act.EVIDENCE_FIRST_STATE)
    assert plain.note_evidence(37_198 * SECOND, act.EVIDENCE_OBSERVATION) is True


class _GateBase:
    def __init__(self, *, pin=None, belief_source=act.EVIDENCE_FIRST_STATE, observations=None):
        self._v504_history_pin = pin
        self._v501_belief = SimpleNamespace(history_start_source=belief_source)
        self._research_session_identity = None
        self._research_realized_observations_by_book = observations or {}
        self.realized_pnl_history = {}
        self._research_realized_pnl_events_by_book = {}
        self._research_round_trip_closes = 0
        self._research_sim_start_ts = None
        self.research_v503_newcomer_gate = True

    def _v501_refresh(self):
        return None


GATE_NAMES = dict(
    restored_session=ng.restored_session, prior_evidence=ng.prior_evidence, effective_anchor=ng.effective_anchor,
    gate_timestamp=ng.gate_timestamp, arm_decision=ng.arm_decision,
    KAPPA_MIN_LOOKBACK_NS=act.KAPPA_MIN_LOOKBACK_NS, SCORING_INTERVAL_NS=act.SCORING_INTERVAL_NS,
    EVIDENCE_FIRST_STATE=act.EVIDENCE_FIRST_STATE, V503_NEWCOMER_GATE_VERSION=ng.V503_NEWCOMER_GATE_VERSION,
    V504_EVIDENCE_DECLARED_ESTABLISHED=_constant("V504_EVIDENCE_DECLARED_ESTABLISHED"),
)


def test_the_gate_uses_a_declared_start_and_never_holds_an_established_uid():
    Gate = _harness(_GateBase, ("_v503_gate_evaluate",), GATE_NAMES)
    state = SimpleNamespace(timestamp=47_988 * SECOND)
    declared = si.HistoryPin(47_405 * SECOND, si.PIN_DECLARED, False)
    # A newcomer with a declared start: armed, and the gate is the validator's, not the first state's.
    agent = Gate(pin=declared, belief_source=si.PIN_DECLARED)
    agent._v503_gate_evaluate(state)
    assert agent._v503_gate_armed is True and agent._v503_gate_anchor_ts == 47_405 * SECOND
    assert agent._v503_gate_ts == 47_405 * SECOND + act.KAPPA_MIN_LOOKBACK_NS + act.SCORING_INTERVAL_NS + ng.V503_GATE_MARGIN_NS
    # A declared start does not excuse real evidence.
    evidenced = Gate(pin=declared, observations={4: 3})
    evidenced._v503_gate_evaluate(state)
    assert evidenced._v503_gate_armed is False and evidenced._v503_gate_open_reason == ng.EVIDENCE_OBSERVATIONS
    # Established: never armed.
    established = Gate(pin=si.HistoryPin(1, si.PIN_ESTABLISHED, True), belief_source=si.PIN_ESTABLISHED)
    established._v503_gate_evaluate(state)
    assert established._v503_gate_armed is False
    assert established._v503_gate_open_reason == _constant("V504_EVIDENCE_DECLARED_ESTABLISHED")
    assert ng.gate_state(armed=False, open_reason=established._v503_gate_open_reason) == ng.GATE_UNARMED
    # No pin: v5.0.3 exactly, including its restored-start evidence.
    inferred = Gate(pin=None, belief_source=act.EVIDENCE_OBSERVATION)
    inferred._v503_gate_evaluate(state)
    assert inferred._v503_gate_open_reason == ng.EVIDENCE_RESTORED_START
    fresh = Gate(pin=None)
    fresh._v503_gate_evaluate(state)
    assert fresh._v503_gate_armed is True and fresh._v503_gate_anchor_ts == 47_988 * SECOND


# ---- H3: the recorder budget counts the disk --------------------------------------------------------

def _raw_state(i):
    # Wire-like values: prices, sizes, ids and fees that do not repeat, so gzip compresses about as it
    # does on the real feed (5:1) rather than 100:1.
    rng = random.Random(i)
    trades = [{"y": "t", "b": 4, "i": rng.randrange(10**9), "p": round(rng.uniform(90, 110), 3),
               "q": round(rng.uniform(0.25, 9), 4), "s": rng.randrange(2), "t": rng.randrange(10**18),
               "Ta": -rng.randrange(100, 200), "Ma": rng.randrange(256), "Tf": rng.random(), "Mf": -rng.random()}
              for k in range(60)]
    return {4: {"i": 4, "b": [{"p": 99.0, "q": 3.0}], "a": [{"p": 101.0, "q": 2.0}], "e": trades}}


def _record(basis, *, max_bytes, rotate_bytes=10**9, states=400):
    directory = tempfile.mkdtemp()
    recorder = obs.StateRecorder(directory, uid=125, max_bytes=max_bytes, rotate_bytes=rotate_bytes,
                                 budget_basis=basis)
    for i in range(1, states + 1):
        if recorder.stopped_reason:
            break
        recorder._write((i, i * SECOND, _raw_state(i), False))
    recorder.close()
    on_disk = sum(os.path.getsize(os.path.join(directory, f)) for f in os.listdir(directory))
    return recorder, on_disk, directory


def test_the_disk_basis_counts_what_gzip_wrote():
    recorder, on_disk, _ = _record(obs.BUDGET_DISK, max_bytes=0, states=300)
    assert recorder.stopped_reason == obs.STOP_CLOSED
    assert recorder.disk_bytes == on_disk
    # Random test values compress worse than the real feed (about 5:1), and still well above 1:1.
    assert recorder.bytes_written > 1.5 * on_disk, "the payload count overstates the disk"


def test_rotated_parts_add_up():
    recorder, on_disk, directory = _record(obs.BUDGET_DISK, max_bytes=0, rotate_bytes=4_000, states=300)
    assert recorder.snapshot()["parts"] == len(os.listdir(directory)) > 2
    assert recorder.disk_bytes == on_disk
    for name in os.listdir(directory):
        with gzip.open(os.path.join(directory, name), "rt") as handle:
            assert all(json.loads(line)["n_trades"] == 60 for line in handle)


def test_the_same_budget_lasts_several_times_longer_on_the_disk_basis():
    budget = 300_000
    payload, _, _ = _record(obs.BUDGET_PAYLOAD, max_bytes=budget, states=2_000)
    disk, on_disk, _ = _record(obs.BUDGET_DISK, max_bytes=budget, states=2_000)
    assert payload.stopped_reason == disk.stopped_reason == obs.STOP_BUDGET
    assert disk.states_written > 2 * payload.states_written
    # gzip flushes in blocks, so the stop lands within one block past the budget, and the files hold
    # what the recorder says they hold.
    assert budget <= disk.disk_bytes == on_disk < budget + 256 * 1024
    snap = disk.snapshot()
    assert snap["budget_basis"] == obs.BUDGET_DISK and snap["max_bytes"] == budget
    assert snap["disk_bytes"] == disk.disk_bytes


def test_the_default_basis_is_v5_0_3_and_an_unknown_basis_falls_back_to_it():
    assert obs.StateRecorder(tempfile.mkdtemp(), uid=1).budget_basis == obs.BUDGET_PAYLOAD
    assert obs.StateRecorder(tempfile.mkdtemp(), uid=1, budget_basis="bytes").budget_basis == obs.BUDGET_PAYLOAD
    assert obs.V504_DISK_BUDGET_MB == 8192
    handle = SimpleNamespace(tell=lambda: 7)
    assert obs.disk_position(handle) == 7
    assert obs.disk_position(SimpleNamespace()) == 0


# ---- H4: the validator's rounds ---------------------------------------------------------------------

def test_the_round_grid_ends_now_and_starts_at_the_history_start():
    rounds = mr.validator_rounds(10 * SECOND, 15 * SECOND, step_ns=SECOND, lookback_ns=10_800 * SECOND)
    assert rounds == [t * SECOND for t in range(10, 16)]
    assert mr.validator_rounds(0, 20_000 * SECOND, step_ns=SECOND, lookback_ns=10_800 * SECOND)[0] == 9_200 * SECOND
    assert mr.validator_rounds(None, 5 * SECOND, step_ns=SECOND, lookback_ns=0) == [5 * SECOND]
    assert mr.validator_rounds(9 * SECOND, 5 * SECOND, step_ns=SECOND, lookback_ns=0) == [5 * SECOND]
    assert mr.validator_rounds(0, 5 * SECOND, step_ns=0, lookback_ns=0) == []
    # A start off the grid never adds a column before it.
    assert mr.validator_rounds(SECOND // 2, 3 * SECOND, step_ns=SECOND, lookback_ns=0)[0] == SECOND


def test_a_fill_belongs_to_the_first_state_strictly_after_it():
    bucket = lambda ts: mr.state_bucket(ts, step_ns=SECOND, phase_ns=7 * SECOND)
    assert bucket(50_298_400_000_000) == 50_299 * SECOND
    assert bucket(50_299 * SECOND) == 50_300 * SECOND, "measured on book 100 of UID 125's session file"
    assert bucket(-1) == 0 and bucket(None) is None
    shifted = mr.state_bucket(1_200_000_000, step_ns=SECOND, phase_ns=1_500_000_000)
    assert shifted == 1_500_000_000


def test_the_history_before_this_process_is_rebuilt_from_the_stored_fills():
    events = {4: [[10_200_000_000, 1.0], [10_700_000_000, 0.5], [12_000_000_000, 2.0], [15_100_000_000, 9.0]],
              "5": [(11_100_000_000, -0.25), (11_300_000_000, 0.25)], "x": [(1, 1.0)], 6: [["bad", 1.0], (1,)]}
    history = mr.rebuild_history(events, step_ns=SECOND, phase_ns=15 * SECOND, before_ns=15 * SECOND)
    assert history == {11 * SECOND: {4: 1.5}, 13 * SECOND: {4: 2.0}}, "a netted-out state holds nothing"
    assert mr.rebuild_history(events, step_ns=None, phase_ns=0, before_ns=0) == {}
    live = {16 * SECOND: {4: 9.0}, 13 * SECOND: {4: 7.0}}
    merged = mr.merge_history(history, live)
    assert merged[13 * SECOND] == {4: 7.0} and merged[11 * SECOND] == {4: 1.5}
    assert mr.merge_history({}, live) is live


def test_the_start_is_declared_then_restored_then_the_first_state():
    assert mr.resolve_start(declared=5, restored=3, first_state=9) == (5, mr.START_DECLARED)
    assert mr.resolve_start(declared=None, restored=3, first_state=9) == (3, mr.START_RESTORED)
    assert mr.resolve_start(declared=None, restored=12, first_state=9) == (9, mr.START_FIRST_STATE)
    assert mr.resolve_start(declared=None, restored=None, first_state=None) == (None, None)
    assert mr.rebase_keys({10: "a", 20: "b"}, -15, keep_from=0) == {5: "b"}
    assert mr.rebase_keys([10, "x"], 5) == {15: None}
    assert mr.restored_start(mr.session_state(42)) == 42 and mr.restored_start(None) is None


def _history(start_s, end_s, books=41):
    history = {}
    for ts in range(start_s, end_s, 7):
        history[ts * SECOND] = {book: 0.5 for book in range(ts % books, ts % books + 3)}
    return history


def test_the_validators_rounds_open_kappa_when_the_validator_does():
    """UID 125: registered at 47,405 s, first request at 47,988 s, Kappa at 52,805 s."""
    registration, first, gate = 47_405, 47_988, 52_805
    history = _history(first, gate + 1)
    kwargs = dict(book_count=128, miner_wealth=50_000.0, grace_period_ns=600 * SECOND, volume_decimals=4)
    grid = mr.validator_rounds(registration * SECOND, gate * SECOND, step_ns=SECOND, lookback_ns=10_800 * SECOND)
    seen = range(first * SECOND, gate * SECOND + 1, SECOND)
    assert mirror_score(history, grid, now_ts=gate * SECOND, **kwargs).kappa_available is True
    assert mirror_score(history, seen, now_ts=gate * SECOND, **kwargs).kappa_available is False
    # A process that saw every state since registration scores exactly what the grid scores.
    whole = range(registration * SECOND, gate * SECOND + 1, SECOND)
    a = mirror_score(history, grid, now_ts=gate * SECOND, **kwargs)
    b = mirror_score(history, whole, now_ts=gate * SECOND, **kwargs)
    assert (a.kappa_score, a.pnl_score, a.n_rounds, a.scored_books) == (b.kappa_score, b.pnl_score, b.n_rounds, b.scored_books)


class _MirrorBase:
    def __init__(self, *, anchor="auto", events=None, restored_start=None):
        self._v504_history_anchor = si.parse_history_anchor(anchor)
        self._v504_history_pin = None
        self._v501_belief = act.ActivityBelief()
        self._v504_mirror = mr.new_mirror_state()
        self._v504_mirror["restored_start"] = restored_start
        self._research_realized_pnl_events_by_book = events or {}
        self.realized_pnl_history = {}
        self.research_v504_mirror_rounds = True


MIRROR_NAMES = dict(
    MODE_INVALID=si.MODE_INVALID, resolve_history_pin=si.resolve_history_pin,
    extract_simulation_id=lambda state: getattr(state, "simulation_id", None),
    resolve_start=mr.resolve_start, positive_step=mr.positive_step, rebuild_history=mr.rebuild_history,
    rebase_keys=mr.rebase_keys, REBASE_MIN_JUMP_NS=act.REBASE_MIN_JUMP_NS, KAPPA_LOOKBACK_NS=si.KAPPA_LOOKBACK_NS,
    validator_rounds=mr.validator_rounds, merge_history=mr.merge_history,
    BASIS_OBSERVED=mr.BASIS_OBSERVED, BASIS_VALIDATOR_GRID=mr.BASIS_VALIDATOR_GRID,
)
MIRROR = ("_v504_resolve_pin", "_v504_service", "_v504_mirror_inputs")


def _state(ts, sim=SIM, step=SECOND):
    return SimpleNamespace(timestamp=ts, simulation_id=sim, config=SimpleNamespace(publish_interval=step))


def test_a_restarted_process_scores_the_rounds_and_fills_it_did_not_see():
    Agent = _harness(_MirrorBase, MIRROR, MIRROR_NAMES)
    restart = 54_000 * SECOND
    events = {4: [[50_000_500_000_000, 1.0], [53_999_200_000_000, 2.0], [54_000 * SECOND, 5.0]]}
    agent = Agent(anchor=f"{SIM}@47405", events=events, restored_start=47_988 * SECOND)
    agent.realized_pnl_history = {restart: {4: 5.0}}
    agent._v504_service(_state(restart))
    assert agent._v504_history_pin.source == si.PIN_DECLARED
    assert agent._v501_belief.pinned and agent._v501_belief.history_start_ts == 47_405 * SECOND
    history, rounds, row = agent._v504_mirror_inputs(restart)
    # The fill at 53,999.2 s and the one at 54,000.0 s belong to states this process saw itself, so
    # only the 50,000.5 s fill is rebuilt, and nothing is counted twice.
    assert row["mirror_start_source"] == mr.START_DECLARED and row["mirror_restored_states"] == 1
    # The declared start is inside the 3 h window, so the grid begins there, not 3 h back.
    assert rounds[0] == 47_405 * SECOND and rounds[-1] == restart and len(rounds) == 6_596
    assert history[50_001 * SECOND] == {4: 1.0} and history[restart] == {4: 5.0}
    assert sorted(history) == [50_001 * SECOND, restart]
    # The restored fills reach back to where this UID's own file began, which is inside the window.
    assert row["mirror_pnl_known_from"] == 47_988 * SECOND and row["mirror_pnl_complete"] == 0
    assert agent._v504_mirror_inputs(47_988 * SECOND + si.KAPPA_LOOKBACK_NS)[2]["mirror_pnl_complete"] == 1
    # A boundary restart with nothing restored scores earlier rounds as flat, and says so.
    blank = Agent(anchor="established")
    blank._v504_service(_state(5 * SECOND, sim="20260918_1745"))
    _, blank_rounds, blank_row = blank._v504_mirror_inputs(5 * SECOND)
    assert blank._v504_history_pin.established and len(blank_rounds) == 10_801
    assert blank_row["mirror_pnl_known_from"] == 5 * SECOND and blank_row["mirror_pnl_complete"] == 0
    # Without a declaration the start is the one this UID's own file kept.
    kept = Agent(events=events, restored_start=47_988 * SECOND)
    kept._v504_service(_state(restart))
    assert kept._v504_history_pin is None and kept._v504_mirror["start_source"] == mr.START_RESTORED


def test_a_new_simulation_moves_the_rounds_and_a_rewind_does_not():
    Agent = _harness(_MirrorBase, MIRROR, MIRROR_NAMES)
    agent = Agent(events={4: [[90_000_500_000_000, 1.0]]}, restored_start=80_000 * SECOND)
    agent._v504_service(_state(95_000 * SECOND))
    assert agent._v504_mirror["history"] == {90_001 * SECOND: {4: 1.0}}
    agent._v504_service(_state(94_990 * SECOND))                     # a 10 s checkpoint rewind
    assert agent._v504_mirror["rebases"] == 0
    agent._v504_service(_state(0))                                   # the next simulation's clock
    mirror = agent._v504_mirror
    # The shift is measured from the last state seen (94,990 s), as the validator's is.
    assert mirror["rebases"] == 1 and mirror["start_ts"] == -14_990 * SECOND
    assert mirror["history"] == {-4_989 * SECOND: {4: 1.0}} and mirror["live_since"] == 10 * SECOND
    assert mirror["pnl_known_from"] == -14_990 * SECOND
    history, rounds, _ = agent._v504_mirror_inputs(0)
    assert rounds[0] == -si.KAPPA_LOOKBACK_NS and rounds[-1] == 0 and -4_989 * SECOND in history


def test_the_switch_off_and_the_unknown_cadence_keep_v5_0_3s_rounds():
    Agent = _harness(_MirrorBase, MIRROR, MIRROR_NAMES)
    agent = Agent()
    agent._v504_service(_state(100 * SECOND, step=None))
    assert agent._v504_mirror_inputs(100 * SECOND) == (None, None, {"mirror_rounds_basis": mr.BASIS_OBSERVED})
    assert agent._v504_mirror["fallbacks"] == 1 and agent._v504_mirror["history"] is None
    agent.research_v504_mirror_rounds = False
    assert agent._v504_mirror_inputs(100 * SECOND) is None
    invalid = Agent(anchor="47405")
    invalid._v504_service(_state(100 * SECOND))
    assert invalid._v504_history_pin is None and not invalid._v501_belief.pinned
    score = _method_source("_v500_emit_score")
    assert "if grid_rounds:\n                    history, rounds = grid_history, grid_rounds" in score
    assert "first_round = min(rounds) if rounds else now_ts" in score


# ---- wiring -----------------------------------------------------------------------------------------

def test_v5_0_4_is_wired_and_launched():
    respond = _method_source("respond")
    assert respond.index("self._a199_service_resync(state)") < respond.index("self._v504_service(state)")
    assert respond.index("self._v504_service(state)") < respond.index("quiet = self._v503_gate_response(state)")
    assert respond.index("self._v503_service(state)") < respond.index("self._v504_telemetry(state)")
    assert "belief.pin(pin.start_ns, pin.source)" in _method_source("_v504_resolve_pin")
    assert "budget_basis=(" in _method_source("_v503_recorder_handle")
    for switch in ("research_v504_session_per_uid", "research_v504_disk_budget", "research_v504_mirror_rounds"):
        assert f'self.{switch} = self._as_bool(\n            getattr(self.config, "{switch}", True)' in SIMPLE, switch
    assert 'getattr(self.config, "research_v504_history_anchor", ANCHOR_AUTO)' in SIMPLE
    assert 'getattr(self.config, "research_v504_legacy_session", LEGACY_IGNORE)' in SIMPLE
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_0_0"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_0_0"' in SIMPLE
    assert si.V504_REGISTRATION_IDENTITY_VERSION.endswith("v5_0_4")
    assert mr.V504_MIRROR_ROUNDS_VERSION.endswith("v5_0_4") and obs.V504_DISK_BUDGET_VERSION.endswith("v5_0_4")
    assert obs.V503_OBSERVATORY_VERSION.endswith("v5_0_3"), "the recorder's row format is unchanged"
    for key in ("direct_v504_registration_identity_version", "direct_v504_session_per_uid",
                "direct_v504_session_source", "direct_v504_legacy_session", "direct_v504_history_anchor",
                "direct_v504_pin_source", "direct_v504_disk_budget", "direct_v504_recorder_disk_bytes",
                "direct_v504_mirror_rounds", "direct_v504_mirror_start_source",
                "direct_v504_mirror_restored_states", "direct_v504_errors"):
        assert f'stats["{key}"]' in SIMPLE, key
    assert ("strategy1_direct_v5_0_4) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; "
            "A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; "
            "V502_BUILD=1; V503_BUILD=1; V504_BUILD=1 ;;") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    for switch in ("research_v504_session_per_uid=1", "research_v504_legacy_session=${LEGACY_SESSION}",
                   "research_v504_history_anchor=${HISTORY_ANCHOR}", "research_v504_disk_budget=1",
                   "research_v503_recorder_max_mb=${RECORDER_MAX_MB}", "research_v504_mirror_rounds=1",
                   "research_v503_newcomer_gate=1", "research_v503_state_recorder=1"):
        assert switch in params, switch
    for default in ('HISTORY_ANCHOR="${HISTORY_ANCHOR:-auto}"', 'LEGACY_SESSION="${LEGACY_SESSION:-ignore}"',
                    'RECORDER_MAX_MB="${RECORDER_MAX_MB:-8192}"'):
        assert default in LAUNCHER, default
    assert "tests/test_research_v5_0_4_registration_identity.py" in LAUNCHER
    assert "[preflight] v5.0.4 registration identity + disk budget PASS" in LAUNCHER
    guards = {
        'return uid_session_path(path, getattr(self, "uid", None))': SIMPLE,
        "raw = self._v504_read_session(identity)": SIMPLE,
        "belief.pin(pin.start_ns, pin.source)": SIMPLE,
        "self._v504_service(state)": SIMPLE,
        "spent = self.disk_bytes if self.budget_basis == BUDGET_DISK else self.bytes_written": OBSERVATORY,
        'provider = getattr(self, "_v504_mirror_inputs", None)': SIMPLE,
    }
    for literal, source in guards.items():
        assert literal in source and literal in LAUNCHER, literal


def test_the_frozen_baseline_is_untouched_and_the_helpers_are_pure():
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in RESEARCH
    assert "v504" not in RESEARCH
    for text, allowed in (
        (IDENTITY, {"__future__", "typing", "dataclasses", "research_v5_activity"}),
        (ROUNDS, {"__future__", "typing"}),
    ):
        tree = ast.parse(text)
        assert {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} == allowed
        assert {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names} <= {"math"}


def test_nothing_the_strategy_decides_reads_the_score_copy_or_the_recorder():
    """H1 and H2 change what the agent restores and when its gate opens; H3 and H4 are telemetry."""
    for node in ast.walk(ast.parse(SIMPLE)):
        if isinstance(node, ast.ClassDef) and node.name == "Strategy1_Research_Simple":
            for fn in node.body:
                if not isinstance(fn, ast.FunctionDef):
                    continue
                src = ast.get_source_segment(SIMPLE, fn)
                if "_v504_mirror_inputs" in src:
                    assert fn.name in {"_v504_mirror_inputs", "_v500_emit_score"}, fn.name
                if "_v504_mirror[" in src or 'getattr(self, "_v504_mirror"' in src:
                    # build_mm_strategy_instructions only copies two values into its stats block.
                    assert fn.name in {"_v504_read_session", "_v504_service", "_v504_mirror_inputs",
                                       "_v504_telemetry", "_research_save_session",
                                       "build_mm_strategy_instructions"}, fn.name
                    if fn.name == "build_mm_strategy_instructions":
                        uses = [line for line in src.splitlines() if "v504_mirror" in line]
                        assert all("stats[" in line or "v504_mirror = getattr(" in line for line in uses), uses
