"""v6.24: S1's auto mode keys on the making basis the validator pays -- research_v624_pay_basis.

v6.23 + one switch.  S4 (research_v624_pay_basis, default on): S1's auto mode reads the paying basis the validator
publishes on its config page (the label config_scoring_debeta_making_basis): realized -> active, captured -> inactive,
whatever the release; while this host has never read it, v6.23's release rule (a 0.6.3 validator on).  The basis comes
through a per-host shared cache: one GET per FETCH_PERIOD_S per host, timed by the last ATTEMPT; the last known basis is
never replaced by a failed GET, a page without the label or a lost file, and it outlives the agents.  Modes 1 / 0 and
S1's rule itself are unchanged; with the switch off, or the basis unknown, the build is v6.23.

No test here touches the host's cache (/tmp/orthoxplus_validator_config) or the network: every SharedBasis is built on a
test directory with an injected page, and the agent's own constructor (V624SharedBasis) is replaced by one that fails.
"""
import ast
import difflib
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v623_validator_063 as vo  # noqa: E402
import research_v624_pay_basis as pb  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
from _harness import extractor  # noqa: E402
import _v638_undo  # noqa: E402
from _harness import simple_text, read_source, copy_source, strategy_view  # noqa: E402

SIMPLE = simple_text()
MODULE = read_source(STRATEGY / "research_v624_pay_basis.py")
LAUNCHER = read_source(ROOT / "run_strategy1_research_simple_multi.sh")
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
CAPTURED, REALIZED = pb.BASIS_CAPTURED, pb.BASIS_REALIZED
LABEL = "config_scoring_debeta_making_basis"
SWITCH_LINE = 'self.research_v624_pay_basis = self._as_bool(getattr(self.config, "research_v624_pay_basis", True))'
OBSERVE_LINE = "active = bool(v624_realized_active(v6_24_mode, spec, self._v6_24_basis))"
FIELDS = ("pay_basis_on", "basis", "basis_seen_age_s", "basis_attempt_age_s", "basis_fetches", "basis_fetch_errors")
V624_NAMES = {"V624SharedBasis": pb.SharedBasis, "v624_reads_basis": pb.reads_basis,
              "v624_realized_active": pb.realized_active}
EXTRA = dict(t623.EXTRA, **V624_NAMES)
METHODS = ("_v6_23_observe", "_v6_23_snapshot", "_v6_24_basis_ref")
BUY_ONLY = ([(3, "BUY", 40031, 99.8, 2.0)], [])          # t623's book 3 with S1 active: the ask's level 0 is withheld


def _page(value=CAPTURED, *, label=LABEL):
    """The validator's config page as it is served: other metrics, then the neuron_info_info line whose labels carry its
    launch flags (a comma inside a quoted value, neighbours with the same prefix)."""
    flags = ('coldkey_name="taos",config_scoring_debeta_class_weights="0.95,0.05",'
             f'config_scoring_debeta_floor_scale="0.0",{label}="{value}",config_scoring_debeta_making_horizon_s="20.0",'
             'config_scoring_debeta_min_books="4",uid="0"')
    return ("# HELP validator_gauges Validator state.\n# TYPE validator_gauges gauge\n"
            'validator_gauges{netuid="79",sim_id="20261005_1128",validator_gauge_name="scoring_shadow_suspended"} 0\n'
            "# HELP neuron_info_info Info summaries for the running validator.\n# TYPE neuron_info_info gauge\n"
            f"neuron_info_info{{{flags}}} 1.0\n"
            "# HELP simulation_config_info Market configuration per asset class.\n"
            'simulation_config_info{asset_class="market",books="0-127",n_books="128",netuid="79"} 1.0\n')


# ---- 1. the module: the page and the rule ---------------------------------------------------------------------------

def test_the_constants_are_the_specified_ones_and_each_is_labelled():
    assert pb.V624_PAY_BASIS_VERSION == "pay_basis_v6_24"
    assert pb.CONFIG_URL == "http://84.32.70.8:9091/metrics/validator"
    assert pb.CACHE_DIR == "/tmp/orthoxplus_validator_config"
    assert (pb.FETCH_PERIOD_S, pb.FETCH_TIMEOUT_S, pb.POLL_S) == (300.0, 60.0, 30.0)
    assert (pb.BASIS_CAPTURED, pb.BASIS_REALIZED) == ("captured", "realized")
    assert (pb.MODE_AUTO, pb.MODE_ON, pb.MODE_OFF) == (vo.MODE_AUTO, vo.MODE_ON, vo.MODE_OFF) == ("auto", "on", "off")
    assert pb.parse_mode is vo.parse_mode
    for name in ("CONFIG_URL", "CACHE_DIR", "FETCH_PERIOD_S", "FETCH_TIMEOUT_S", "POLL_S", "BASIS_CAPTURED",
                 "BASIS_REALIZED"):
        line = next(ln for ln in MODULE.splitlines() if ln.startswith(name + " = "))
        assert "# STRUCTURAL" in line or "# OBSERVED" in line, name
    assert "# OBSERVED" in next(ln for ln in MODULE.splitlines() if ln.startswith("FETCH_TIMEOUT_S = "))
    assert set(pb.__all__) == {"BASIS_CAPTURED", "BASIS_REALIZED", "CACHE_DIR", "CONFIG_URL", "FETCH_PERIOD_S",
                               "FETCH_TIMEOUT_S", "MODE_AUTO", "MODE_OFF", "MODE_ON", "POLL_S", "SharedBasis",
                               "V624_PAY_BASIS_VERSION", "parse_basis", "parse_mode", "reads_basis", "realized_active"}
    feed = pb.SharedBasis()                                    # built, never stepped or started: no file, no page
    assert (feed.url, feed.cache_dir, feed.period_s, feed.poll_s, feed.timeout_s) == \
        (pb.CONFIG_URL, pb.CACHE_DIR, 300.0, 30.0, 60.0)
    assert feed._fetch == pb.SharedBasis._http_get and feed._clock is time.time
    assert feed._paths() == ("/tmp/orthoxplus_validator_config/basis.json", "/tmp/orthoxplus_validator_config/fetch.lock")
    assert feed.snapshot() is None and feed.basis() is None and feed._thread is None


def test_the_basis_is_the_label_on_the_config_page():
    assert pb.parse_basis(_page(CAPTURED)) == CAPTURED          # UID 0's page since 23:50 JST 10-09
    assert pb.parse_basis(_page(REALIZED)) == REALIZED
    assert pb.parse_basis(f'{LABEL}="captured"') == CAPTURED
    assert pb.parse_basis(f'x{{{LABEL}="realized"}} 1.0') == REALIZED
    assert pb.parse_basis(f'a{{b="1", {LABEL}="realized"}} 1.0') == REALIZED            # a space after the comma
    assert pb.parse_basis(_page(REALIZED) + _page(CAPTURED)) == REALIZED                  # the first one on the page


def test_realized_whatever_its_case_and_surrounding_space_and_any_other_value_is_captured():
    for value in ("realized", "Realized", "REALIZED", " realized", "realized ", "\trealized\t", " ReAlIzEd  "):
        assert pb.parse_basis(_page(value)) == REALIZED, value
    for value in ("captured", "Captured", " CAPTURED ", "realised", "realized_v2", "x", "0", "real ized", "none"):
        assert pb.parse_basis(_page(value)) == CAPTURED, value       # upstream: anything but 'realized' pays captured


def test_no_label_an_empty_value_or_an_input_that_is_not_text_reads_nothing():
    for text in ("", 'neuron_info_info{uid="0"} 1.0\n', _page(label="config_scoring_debeta_making_horizon"),
                 _page(label="xconfig_scoring_debeta_making_basis"),
                 _page(label="config_scoring_debeta_making_basis_v2"), _page(""), _page("   "),
                 f"{LABEL}=captured", f"{LABEL}='captured'", f'{LABEL}="captured', f'{LABEL} = "captured"'):
        assert pb.parse_basis(text) is None, text
    for junk in (None, _page().encode(), f'{LABEL}="realized"'.encode(), bytearray(b"x"), 63, 1.5, True, ["x"],
                 {LABEL: "realized"}, object()):
        assert pb.parse_basis(junk) is None, junk


def test_s4_the_full_matrix_of_mode_basis_and_release():
    v623 = {63: True, 62: False, None: False}
    for spec in (62, 63, None):
        for basis in (REALIZED, CAPTURED, None):
            assert pb.realized_active(vo.MODE_ON, spec, basis) is True, (spec, basis)
            assert pb.realized_active(vo.MODE_OFF, spec, basis) is False, (spec, basis)
            want = (basis == REALIZED) if basis is not None else v623[spec]
            assert pb.realized_active(vo.MODE_AUTO, spec, basis) is want, (spec, basis)
        assert pb.realized_active(vo.MODE_AUTO, spec, None) is vo.realized_active(vo.MODE_AUTO, spec)   # v6.23 exactly
    for junk in ("Realized", " realized", "x", "", 1, True, ["realized"], ("captured",)):
        assert pb.realized_active("auto", 63, junk) is True and pb.realized_active("auto", 62, junk) is False, junk
    for mode in (None, "x", "1", 1):                                  # not a parsed mode: never active (v6.23)
        assert pb.realized_active(mode, 63, REALIZED) is vo.realized_active(mode, 63) is False, mode
    for raw, want in (("auto", True), ("1", True), (1.0, True), ("0", False), (0.0, False)):
        assert pb.realized_active(pb.parse_mode(raw), 62, REALIZED) is want, raw
    assert pb.reads_basis(vo.MODE_AUTO) and pb.reads_basis(pb.parse_mode(None))
    assert not pb.reads_basis(vo.MODE_ON) and not pb.reads_basis(vo.MODE_OFF) and not pb.reads_basis(None)


# ---- 2. one reading per host ---------------------------------------------------------------------------------------

class _Page:
    """An injected GET: records each call and answers from a script, in order, the last answer repeating; an answer that
    is an exception is raised."""

    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, url, timeout):
        self.calls.append((url, timeout))
        answer = self.answers[min(len(self.calls), len(self.answers)) - 1]
        if isinstance(answer, BaseException):
            raise answer
        return answer


class _Clock:
    def __init__(self, now=1_000.0):
        self.now = float(now)

    def __call__(self):
        return self.now


def _basis(tmp, page, clock, **kw):
    return pb.SharedBasis(cache_dir=str(tmp), fetch=page, clock=clock, **kw)


def _cache(tmp):
    return json.loads((Path(tmp) / "basis.json").read_text())


def _write(tmp, **data):
    Path(tmp).mkdir(parents=True, exist_ok=True)
    (Path(tmp) / "basis.json").write_text(json.dumps(data))


def test_the_first_step_gets_the_page_once_and_writes_the_hosts_cache(tmp_path):
    page, clock = _Page(_page(CAPTURED)), _Clock(1_000.0)
    feed = _basis(tmp_path / "host", page, clock)
    assert feed.basis() is None and not (tmp_path / "host").exists()
    feed.step()
    assert page.calls == [(pb.CONFIG_URL, pb.FETCH_TIMEOUT_S)]
    assert _cache(tmp_path / "host") == {"basis": "captured", "seen": 1000.0, "attempt": 1000.0}
    assert feed.basis() == CAPTURED and feed.snapshot() == {"basis": "captured", "seen": 1000.0, "attempt": 1000.0}
    assert (feed.fetches, feed.fetch_errors, feed.reads, feed.last_error) == (1, 0, 1, None)
    assert sorted(p.name for p in (tmp_path / "host").iterdir()) == ["basis.json", "fetch.lock"]   # tmp file replaced


def test_one_get_per_period_per_host(tmp_path):
    page, clock = _Page(_page(CAPTURED)), _Clock(1_000.0)
    a, b = _basis(tmp_path, page, clock), _basis(tmp_path, page, clock)
    a.step()
    clock.now += pb.FETCH_PERIOD_S - 1
    a.step()
    b.step()
    assert len(page.calls) == 1 and b.basis() == CAPTURED and (a.fetches, b.fetches) == (1, 0)   # b reads a's GET
    clock.now += 1                                                                     # the attempt is a period old
    b.step()
    a.step()
    assert len(page.calls) == 2 and (a.fetches, b.fetches) == (1, 1)                    # one GET: whoever steps first
    assert _cache(tmp_path) == {"basis": "captured", "seen": 1300.0, "attempt": 1300.0}


def test_a_failed_get_keeps_the_last_basis_and_is_retried_once_per_period_not_at_every_poll(tmp_path):
    page = _Page(_page(CAPTURED), OSError("down"), OSError("down"), _page(REALIZED))
    clock = _Clock(1_000.0)
    feed = _basis(tmp_path, page, clock)
    feed.step()
    clock.now = 1_300.0
    feed.step()                                                                        # due: the GET fails
    assert len(page.calls) == 2 and feed.basis() == CAPTURED and feed.fetch_errors == 1
    assert feed.last_error == "fetch: OSError"
    assert _cache(tmp_path) == {"basis": "captured", "seen": 1000.0, "attempt": 1300.0}
    for now in (1_330.0, 1_450.0, 1_599.0):                     # the reading is over a period old, the attempt is not
        clock.now = now
        feed.step()
    assert len(page.calls) == 2 and feed.basis() == CAPTURED
    assert feed.status()["seen_age_s"] == 599.0 and feed.status()["attempt_age_s"] == 299.0
    clock.now = 1_600.0
    feed.step()                                                                        # fails again
    assert len(page.calls) == 3 and feed.basis() == CAPTURED and feed.fetch_errors == 2
    clock.now = 1_900.0
    feed.step()                                                                        # the dial moved
    assert len(page.calls) == 4 and feed.basis() == REALIZED and feed.fetch_errors == 2
    assert _cache(tmp_path) == {"basis": "realized", "seen": 1900.0, "attempt": 1900.0}


def test_a_page_without_the_label_or_with_an_empty_one_keeps_the_last_basis(tmp_path):
    page = _Page(_page(REALIZED), 'neuron_info_info{uid="0"} 1.0\n', _page(""), "")
    clock = _Clock(1_000.0)
    feed = _basis(tmp_path, page, clock)
    feed.step()
    for i in (1, 2, 3):
        clock.now = 1_000.0 + i * pb.FETCH_PERIOD_S
        feed.step()
        assert feed.basis() == REALIZED and feed.fetch_errors == i and feed.last_error == "fetch: no basis label", i
        assert _cache(tmp_path) == {"basis": "realized", "seen": 1000.0, "attempt": clock.now}, i
    assert len(page.calls) == 4


def test_before_any_reading_the_basis_is_unknown_and_the_attempt_is_still_stamped(tmp_path):
    page, clock = _Page(TimeoutError("slow")), _Clock(1_000.0)
    feed = _basis(tmp_path, page, clock)
    feed.step()
    assert feed.basis() is None and _cache(tmp_path) == {"basis": None, "seen": None, "attempt": 1000.0}
    assert feed.status() == {"fetches": 1, "fetch_errors": 1, "reads": 1, "read_errors": 0,
                             "last_error": "fetch: TimeoutError", "basis": None, "seen_age_s": None,
                             "attempt_age_s": 0.0}
    clock.now = 1_010.0
    feed.step()
    assert len(page.calls) == 1                                                        # once per period, not per poll


def test_the_cache_outlives_the_agent_and_a_new_one_starts_from_it(tmp_path):
    page, clock = _Page(_page(CAPTURED)), _Clock(1_000.0)
    _basis(tmp_path, page, clock).step()
    clock.now = 1_100.0
    late = _Page(AssertionError("never asked"))
    restarted = _basis(tmp_path, late, clock)
    assert restarted.basis() is None                                                   # built: nothing read yet
    restarted.load()
    assert restarted.basis() == CAPTURED and late.calls == [] and restarted.reads == 1
    assert restarted.status()["seen_age_s"] == 100.0 and restarted.status()["attempt_age_s"] == 100.0
    restarted.step()                                                                   # not due: no GET
    assert late.calls == [] and restarted.basis() == CAPTURED


def test_while_another_agent_holds_the_lock_nothing_is_fetched_and_the_cache_is_read(tmp_path):
    _write(tmp_path, basis="captured", seen=1_000.0, attempt=1_000.0)
    page, clock = _Page(_page(REALIZED)), _Clock(1_000.0 + pb.FETCH_PERIOD_S)          # due
    feed = _basis(tmp_path, page, clock)
    with open(tmp_path / "fetch.lock", "a") as other:
        fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)                     # another agent is fetching
        feed.step()
        assert page.calls == [] and feed.fetches == 0 and feed.basis() == CAPTURED
        fcntl.flock(other.fileno(), fcntl.LOCK_UN)
    feed.step()
    assert len(page.calls) == 1 and feed.basis() == REALIZED


class _Raced(pb.SharedBasis):
    """Another agent refreshes the host's cache between this one's first read and its lock."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.first = True

    def _read_cache(self):
        if self.first:
            self.first = False
            return {"basis": None, "seen": None, "attempt": 0.0}                       # what it saw: due
        return super()._read_cache()


def test_a_cache_refreshed_while_this_agent_waited_for_the_lock_is_not_fetched_again(tmp_path):
    _write(tmp_path, basis="realized", seen=1_000.0, attempt=1_000.0)
    page = _Page(_page(CAPTURED))
    feed = _Raced(cache_dir=str(tmp_path), fetch=page, clock=_Clock(1_000.0))
    feed.step()
    assert page.calls == [] and feed.basis() == REALIZED


def test_a_torn_or_foreign_cache_is_not_used_and_the_next_get_replaces_it(tmp_path):
    for i, junk in enumerate(("{", "[]", "null", '{"basis": "Captured", "attempt": 1000}', '{"basis": "captured"}',
                              '{"basis": "captured", "attempt": "x"}', '{"basis": "captured", "attempt": NaN}',
                              '{"basis": "captured", "attempt": true}', '{"basis": ["realized"], "attempt": 1000}')):
        host = tmp_path / str(i)
        host.mkdir()
        (host / "basis.json").write_text(junk)
        page = _Page(_page(REALIZED))
        feed = _basis(host, page, _Clock(1_000.0))
        feed.load()
        assert feed.basis() is None and feed.snapshot() is None, junk
        feed.step()
        assert len(page.calls) == 1 and feed.basis() == REALIZED and _cache(host)["basis"] == "realized", junk
        assert (feed.read_errors > 0) is (junk == "{") and (feed.last_error is None) is (junk != "{"), junk
        assert junk != "{" or feed.last_error == "read: JSONDecodeError"


def test_an_attempt_stamped_ahead_of_the_clock_is_due(tmp_path):
    _write(tmp_path, basis="captured", seen=5_000.0, attempt=5_000.0)
    page, clock = _Page(_page(REALIZED)), _Clock(1_000.0)                              # the clock stepped back
    feed = _basis(tmp_path, page, clock)
    feed.step()
    assert len(page.calls) == 1 and feed.basis() == REALIZED
    assert _cache(tmp_path) == {"basis": "realized", "seen": 1000.0, "attempt": 1000.0}


def test_a_known_basis_is_only_ever_replaced_by_one_the_page_published(tmp_path):
    page, clock = _Page(_page(CAPTURED), OSError("down")), _Clock(1_000.0)
    feed = _basis(tmp_path, page, clock)
    feed.step()
    _write(tmp_path, basis=None, seen=None, attempt=1_100.0)     # an agent that never read one stamps a failed attempt
    clock.now = 1_150.0
    feed.step()                                                                        # not due: it reads it
    assert len(page.calls) == 1 and feed.basis() == CAPTURED
    assert feed.snapshot() == {"basis": "captured", "seen": 1000.0, "attempt": 1100.0}
    (tmp_path / "basis.json").unlink()                                                 # the file is lost ...
    feed.step()                                                                        # ... and the GET fails
    assert len(page.calls) == 2 and feed.basis() == CAPTURED
    assert _cache(tmp_path) == {"basis": "captured", "seen": 1000.0, "attempt": 1150.0}   # written back
    other = _basis(tmp_path, _Page(AssertionError("never asked")), clock)
    other.load()
    assert other.basis() == CAPTURED


def test_the_readers_never_raise(tmp_path):
    def bad_clock():
        raise RuntimeError("clock")

    feed = pb.SharedBasis(cache_dir=str(tmp_path / "never"), fetch=_Page(""), clock=bad_clock)
    feed._snap = {"basis": "captured", "seen": 1.0, "attempt": 2.0}
    st = feed.status()
    assert st["basis"] == "captured" and st["seen_age_s"] is None and st["attempt_age_s"] is None
    assert st["last_error"] == "status: RuntimeError" and st["fetches"] == 0
    for junk in (None, [], "x", {"basis": "x"}, {"basis": ["realized"]}, {"basis": None}, {}):
        feed._snap = junk
        assert feed.basis() is None and feed.snapshot() is junk, junk
        assert feed.status()["basis"] is None
    not_a_dir = tmp_path / "file"
    not_a_dir.write_text("x")
    feed = pb.SharedBasis(cache_dir=str(not_a_dir), fetch=_Page(""), clock=_Clock())
    feed.load()                                                                        # unreadable: nothing, no raise
    assert feed.basis() is None and feed.read_errors == 1

    class _Broken(pb.SharedBasis):
        def _read_cache(self):
            raise RuntimeError("disk")

    broken = _Broken(cache_dir=str(tmp_path / "never"), fetch=_Page(""), clock=_Clock())
    broken.load()
    assert broken.basis() is None and broken.last_error == "load: RuntimeError"
    assert not (tmp_path / "never").exists()


class _Held(pb.SharedBasis):
    """Its thread waits for the test before its first cycle."""

    def __init__(self, *a, gate, **k):
        super().__init__(*a, **k)
        self.gate = gate

    def _run(self):
        self.gate.wait(10.0)
        super()._run()


def test_start_loads_the_cache_before_the_thread_runs_then_the_thread_keeps_it(tmp_path):
    _write(tmp_path, basis="captured", seen=1_000.0, attempt=1_000.0)
    got, gate = threading.Event(), threading.Event()

    def fetch(url, timeout):
        got.set()
        return _page(REALIZED)

    feed = _Held(cache_dir=str(tmp_path), fetch=fetch, clock=_Clock(1_000.0 + pb.FETCH_PERIOD_S), poll_s=0.01,
                 gate=gate)
    feed.start()
    thread = feed._thread
    try:
        assert feed.basis() == CAPTURED and not got.is_set()           # loaded by start() itself, before any cycle
        assert thread.daemon and thread.name == "v624-pay-basis" and thread.is_alive()
        feed.start()
        assert feed._thread is thread                                  # once per instance
        gate.set()
        assert got.wait(10.0)                                          # the thread's first cycle: the attempt is due
        for _ in range(1_000):
            if feed.basis() == REALIZED:
                break
            time.sleep(0.01)
        assert feed.basis() == REALIZED and feed.fetches == 1
    finally:
        gate.set()
        feed.stop()
        thread.join(10.0)
    assert not thread.is_alive()


# ---- 3. the agent: v6.23's observation with S4, compiled from the real Simple ------------------------------------------

class _NoHostFeed:
    """The agent's own V624SharedBasis in this suite: a test that reaches it would have an agent read the host's cache
    and the validator's page -- it raises instead (inside the observation: an error, S1 inactive)."""

    def __init__(self, *a, **k):
        raise AssertionError("a test reached the host's basis feed")


SCOPE = dict(EXTRA, V624SharedBasis=_NoHostFeed)
_COMPILED: dict = {}


def _compile(name, scope=None):
    ns = dict(SCOPE if scope is None else scope)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), ns)
    return ns[name]


def _methods():
    if not _COMPILED:
        _COMPILED.update({n: _compile(n) for n in METHODS})
    return _COMPILED


def _agent(*, on=True, feed=None, mode="auto", methods=None, **kw):
    """t623's harness agent (the v6.22 pass with v6.23, fundamental 38 bps above book 3's mid) with the real Simple's
    _v6_23_observe, _v6_23_snapshot and _v6_24_basis_ref; ``feed`` is the host's reading of the basis."""
    agent = t623._agent(mode=mode, **kw)
    agent.__class__ = type("P624", (type(agent),), dict(methods or _methods()))
    agent.research_v624_pay_basis = on
    agent._v6_24_basis_feed, agent._v6_24_basis = feed, None
    return agent


class _Asked(pb.SharedBasis):
    """A host's reading that counts the agent's questions (basis())."""

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.asked = 0

    def basis(self):
        self.asked += 1
        return super().basis()


def _host(tmp, basis, *, now=1_100.0, stamped=False):
    """A host's reading: a SharedBasis on a test directory whose cache holds ``basis`` (seen and attempted at 1,000 s; no
    cache for None, or with ``stamped`` an attempt that read nothing), loaded as start() loads it.  It never GETs."""
    if basis is not None or stamped:
        _write(tmp, basis=basis, seen=None if basis is None else 1_000.0, attempt=1_000.0)
    feed = _Asked(cache_dir=str(tmp), fetch=_Page(AssertionError("no GET in an agent test")), clock=_Clock(now))
    feed.load()
    return feed


def _same(agent, twin, *, spec=63, twin_spec=63, ref=False):
    """Two requests of the agent and its twin -- a v6.23 agent (t623) or the v6.22 reference: what reaches the venue
    and the state left behind."""
    for k in range(2):
        ts = t623.NOW + k * t623.S
        got = t623._out(t623._run(agent, ts=ts, version=spec))
        want = t623._out(t623._run_ref(twin, ts=ts) if ref else t623._run(twin, ts=ts, version=twin_spec))
        if got != want:
            return False
    return t623.t622._fingerprint(agent) == t623.t622._fingerprint(twin)


def test_s4_spec_63_with_a_captured_basis_is_the_v622_pass(tmp_path):
    """The live case since 23:50 JST 10-09: a 0.6.3 validator paying captured -- S1 stays off."""
    agent = _agent(feed=_host(tmp_path, CAPTURED))
    assert _same(agent, t623._ref(), ref=True)
    assert agent._v6_23_active is False and agent._v6_23_spec == 63 and agent._v6_24_basis == CAPTURED
    assert agent._v6_23_errors == 0 and "active_requests" not in agent._v6_23_counts
    assert not any(k.startswith(("fp_", "withheld_")) for k in agent._v6_23_counts)


def test_s4_a_realized_basis_activates_s1_whatever_the_release(tmp_path):
    for i, spec in enumerate((63, 62, None, 55)):
        agent, twin = _agent(feed=_host(tmp_path / str(i), REALIZED)), t623._agent()
        assert t623._out(t623._run(agent, version=spec)) == t623._out(t623._run(twin, version=63)) == BUY_ONLY, spec
        assert agent._v6_23_active is True and agent._v6_23_spec == vo.validator_spec(spec), spec
        assert agent._v6_24_basis == REALIZED and agent._v6_23_counts == twin._v6_23_counts, spec


def test_s4_an_unknown_basis_is_v623_exactly(tmp_path):
    """No cache on the host, or one stamped by attempts that read nothing: the release decides, as in v6.23."""
    for i, (stamped, spec) in enumerate(((False, 63), (False, 62), (False, None), (False, "x"), (True, 63),
                                         (True, 62))):
        agent, twin = _agent(feed=_host(tmp_path / str(i), None, stamped=stamped)), t623._agent()
        assert _same(agent, twin, spec=spec, twin_spec=spec), (stamped, spec)
        assert agent._v6_24_basis is None and agent._v6_23_active is twin._v6_23_active, (stamped, spec)
        assert agent._v6_23_counts == twin._v6_23_counts and agent._v6_23_errors == twin._v6_23_errors == 0


def test_s4_off_is_v623_exactly_whatever_the_basis(tmp_path):
    """research_v624_pay_basis=0: the release decides even with a captured basis on the host; nothing reads it."""
    for i, (basis, spec) in enumerate(((CAPTURED, 63), (REALIZED, 62), (None, 63), (CAPTURED, 62))):
        feed = _host(tmp_path / str(i), basis)
        agent, twin = _agent(on=False, feed=feed), t623._agent()
        assert _same(agent, twin, spec=spec, twin_spec=spec), (basis, spec)
        assert agent._v6_23_active is (spec == 63) and agent._v6_24_basis is None, (basis, spec)
        assert agent._v6_23_counts == twin._v6_23_counts and agent._v6_23_errors == 0 and feed.asked == 0
    agent = _agent(on=False, feed=None)                                # reaching the feed would be an error here
    t623._run(agent, version=63)
    assert agent._v6_23_errors == 0 and agent._v6_24_basis_feed is None and agent._v6_23_active is True


def test_s4_modes_1_and_0_never_read_the_basis(tmp_path):
    for mode, want in (("on", True), ("off", False), ("1", True), ("0", False)):
        agent = _agent(mode=mode, feed=None)                           # reaching the feed would be an error here
        t623._run(agent, version=63)
        assert agent._v6_23_active is want and agent._v6_23_errors == 0, mode
        assert agent._v6_24_basis_feed is None and agent._v6_24_basis is None, mode
    for i, (mode, basis, spec, want) in enumerate((("off", REALIZED, 63, False), ("on", CAPTURED, 62, True))):
        feed = _host(tmp_path / str(i), basis)
        agent, twin = _agent(mode=mode, feed=feed), t623._agent(mode=mode)
        assert _same(agent, twin, spec=spec, twin_spec=spec), mode
        assert agent._v6_23_active is want and agent._v6_24_basis is None and feed.asked == 0, mode
    feed = _host(tmp_path / "auto", CAPTURED)
    agent = _agent(feed=feed)
    assert _same(agent, t623._ref(), ref=True) and feed.asked == 2                      # auto: once per request


def test_s4_across_pages_inventories_caps_and_resting_orders(tmp_path):
    """t623's scenarios, two requests each: a known basis decides (captured: the v6.22 pass; realized: v6.23 active),
    an unknown one or the switch off leaves v6.23's decision."""
    hosts = {b: _host(tmp_path / str(b), b) for b in (CAPTURED, REALIZED, None)}
    cases = ((True, CAPTURED, 63, None), (True, REALIZED, 62, 63), (True, None, 63, 63), (True, None, 62, 62),
             (False, CAPTURED, 63, 63))
    seen = 0
    for page, pname, inv, used, rest in t623._scenarios():
        for on, basis, spec, twin_spec in cases:
            agent = _agent(on=on, feed=hosts[basis], page=page, inv=inv, used=used)
            twin = (t623._ref(page=page, inv=inv, used=used) if twin_spec is None
                    else t623._agent(page=page, inv=inv, used=used))
            if rest is not None:
                rest(agent)
                rest(twin)
            case = (pname, inv, used, getattr(rest, "__name__", None), on, basis, spec)
            assert _same(agent, twin, spec=spec, twin_spec=twin_spec, ref=twin_spec is None), case
            assert agent._v6_23_errors == 0, case
            if twin_spec is not None:
                assert agent._v6_23_active is twin._v6_23_active and agent._v6_23_counts == twin._v6_23_counts, case
            else:
                assert agent._v6_23_active is False, case
            seen += 1
    assert seen == 168 * len(cases)


def _obs(**kw):
    agent = _agent(**kw)
    agent._tick = 0
    return agent


def test_the_state_row_carries_the_basis_and_a_flip_is_an_activation_or_deactivation_row(tmp_path):
    feed = _host(tmp_path, CAPTURED, now=1_100.0)
    agent = _obs(feed=feed)
    state = t623._state(version=63)
    agent._v6_23_observe(state)                                        # 0.6.3, paying captured: inactive
    _write(tmp_path, basis="realized", seen=1_200.0, attempt=1_200.0)  # the dial moved (a validator restart)
    feed._clock.now = 1_230.0
    feed.load()
    agent._v6_23_observe(state)
    agent._v6_23_observe(state)                                        # no change: no row
    _write(tmp_path, basis="captured", seen=1_500.0, attempt=1_500.0)
    feed._clock.now = 1_500.0
    feed.load()
    agent._v6_23_observe(state)
    rows = t623._rows(agent)
    assert [(r["reason"], r["spec"], r["active"], r["basis"]) for r in rows] == [
        ("start", 63, 0, "captured"), ("activation", 63, 1, "realized"), ("deactivation", 63, 0, "captured")]
    assert {k: rows[0][k] for k in FIELDS} == {"pay_basis_on": 1, "basis": "captured", "basis_seen_age_s": 100.0,
                                               "basis_attempt_age_s": 100.0, "basis_fetches": 0,
                                               "basis_fetch_errors": 0}
    assert (rows[1]["basis_seen_age_s"], rows[2]["basis_seen_age_s"]) == (30.0, 0.0)
    assert rows[-1]["counts"] == {"active_requests": 2, "activations": 1, "deactivations": 1}
    assert rows[-1]["version"] == vo.V623_VALIDATOR_063_VERSION and rows[-1]["errors"] == 0


def test_the_state_row_counts_the_hosts_gets_and_says_when_the_switch_is_off(tmp_path):
    page, clock = _Page(_page(CAPTURED), OSError("down")), _Clock(1_000.0)
    feed = _basis(tmp_path, page, clock)
    feed.step()
    clock.now += pb.FETCH_PERIOD_S
    feed.step()
    clock.now += 12.5
    agent = _obs(feed=feed)
    agent._v6_23_observe(t623._state(version=63))
    row = t623._rows(agent)[0]
    assert tuple(row[k] for k in FIELDS) == (1, "captured", 312.5, 12.5, 2, 1)
    off = _obs(on=False, feed=None)
    off._v6_23_observe(t623._state(version=63))
    row = t623._rows(off)[0]
    assert tuple(row[k] for k in FIELDS) == (0, None, None, None, 0, 0) and row["active"] == 1
    t623_row = t623._obs()                                    # v6.23's harness (no v6.24 attribute): the same fields
    t623_row._v6_23_observe(t623._state(version=62))
    assert tuple(t623._rows(t623_row)[0][k] for k in FIELDS) == (0, None, None, None, 0, 0)


def test_an_error_reading_the_basis_counts_an_error_and_leaves_s1_inactive_until_it_reads(tmp_path):
    agent = _agent(feed=None)                                          # the accessor raises (_NoHostFeed)
    t623._run(agent, version=63)
    assert agent._v6_23_active is False and agent._v6_23_errors == 1 and agent._v6_23_spec is None
    assert agent._v6_24_basis_feed is None
    agent._v6_24_basis_feed = _host(tmp_path, REALIZED)
    t623._run(agent, version=62)
    assert agent._v6_23_active is True and agent._v6_23_errors == 1 and agent._v6_24_basis == REALIZED


class _Recorder:
    """V624SharedBasis as the accessor sees it: every instance and start() recorded; the host says captured."""
    made: list = []

    def __init__(self, *a, **k):
        assert not a and not k                                         # the module's own defaults: url, dir, period
        self.started = 0
        _Recorder.made.append(self)

    def start(self):
        self.started += 1

    def basis(self):
        return CAPTURED

    def status(self):
        return {"fetches": 3, "fetch_errors": 1, "seen_age_s": 4.0, "attempt_age_s": 5.0}


def test_the_basis_reader_starts_on_first_use_in_auto_only_and_once():
    methods = {n: _compile(n, dict(SCOPE, V624SharedBasis=_Recorder)) for n in METHODS}
    _Recorder.made = []
    agent = _agent(feed=None, methods=methods)
    for _ in range(3):
        t623._run(agent, version=63)
    assert len(_Recorder.made) == 1 and _Recorder.made[0].started == 1
    assert agent._v6_24_basis_feed is _Recorder.made[0] and agent._v6_23_active is False
    assert agent._v6_24_basis == CAPTURED and agent._v6_23_errors == 0
    assert tuple(agent._v6_23_snapshot()[k] for k in FIELDS) == (1, "captured", 4.0, 5.0, 3, 1)
    for kw in ({"on": False}, {"mode": "on"}, {"mode": "off"}):
        _Recorder.made = []
        agent = _agent(feed=None, methods=methods, **kw)
        t623._run(agent, version=63)
        assert _Recorder.made == [] and agent._v6_24_basis_feed is None, kw


# ---- 4. wiring -------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_its_agent_default():
    params = t617._params()
    assert "research_v624_pay_basis=1 " in t617._shipped() + " " and params.count("research_v624_pay_basis=") == 1
    assert 'research_v623_start_once=1 \\\nresearch_v624_pay_basis=1 \\\n' in LAUNCHER   # after v6.23's keys (v6.25's next)
    assert SIMPLE.count(SWITCH_LINE) == 1
    init = _src("_init_build_switches")
    assert init.index("self._v6_23_start_error: str | None = None") < init.index(SWITCH_LINE) < \
        init.index("self._v6_24_basis_feed = None") < init.index("self._v6_24_basis: str | None = None") < \
        init.index("self._v69_counts: dict[str, int] = {}")


def test_every_v624_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v624_pay_basis")
    assert {(a.name, a.asname) for a in imp.names} == {("SharedBasis", "V624SharedBasis"),
                                                       ("reads_basis", "v624_reads_basis"),
                                                       ("realized_active", "v624_realized_active")}
    for alias in imp.names:
        assert EXTRA[alias.asname] is getattr(pb, alias.name), alias.asname
    assert SIMPLE.count("from research_v624_pay_basis import (") == 1
    assert t623.V624_IDENTIFIERS == V624_IDENTIFIERS                  # v6.23's strip takes the same names out


def test_the_rule_is_wired_into_v623s_observation_and_nowhere_else():
    observe = _src("_v6_23_observe")
    first = next(n for n in ast.parse(observe).body[0].body if isinstance(n, ast.Try))
    assert len(first.body) == 2
    assert ast.unparse(first.body[0]) == "spec = v623_validator_spec(getattr(state, 'version', None))"
    branch = first.body[1]
    assert isinstance(branch, ast.If)
    assert ast.unparse(branch.test) == "bool(getattr(self, 'research_v624_pay_basis', False))"
    assert [ast.unparse(s) for s in branch.body] == [
        "v6_24_mode = getattr(self, 'research_v623_realized_mode', None)",
        "self._v6_24_basis = self._v6_24_basis_ref().basis() if v624_reads_basis(v6_24_mode) else None",
        "active = bool(v624_realized_active(v6_24_mode, spec, self._v6_24_basis))"]
    assert [ast.unparse(s) for s in branch.orelse] == [                # v6.23's own call, unchanged
        "active = bool(v623_realized_active(getattr(self, 'research_v623_realized_mode', None), spec))"]
    assert [ast.unparse(s) for s in first.handlers[0].body] == [ast.unparse(ast.parse(x)) for x in (   # v6.38: as this
        "self._v6_23_errors = int(getattr(self, '_v6_23_errors', 0) or 0) + 1", "spec, active = (None, False)")]  # interpreter writes it
    assert observe.count(OBSERVE_LINE) == 1 and SIMPLE.count(OBSERVE_LINE) == 1
    for call, n in (("self._v6_24_basis_ref()", 1), ("V624SharedBasis(", 1), ("v624_realized_active(", 1),
                    ("v624_reads_basis(", 1), ("self._v6_23_observe(state)", 1)):
        assert SIMPLE.count(call) == n, call
    ref = _src("_v6_24_basis_ref")
    assert "feed = V624SharedBasis()" in ref and ref.index("feed.start()") < ref.index("self._v6_24_basis_feed = feed")


def test_the_state_row_gains_exactly_the_six_fields():
    snap = ast.parse(_src("_v6_23_snapshot")).body[0]
    ret = next(n for n in ast.walk(snap) if isinstance(n, ast.Return))
    keys = [k.value for k in ret.value.keys]
    assert keys == ["version", "mode", "spec", "active", "against_levels", "against_gap_bps", "against_adding_on",
                    "class_guard_on", "start_once_on", "layout", "foreign_books", "foreign_range", "counts", "errors",
                    "start_error", *FIELDS]
    assert [ast.unparse(v) for v in ret.value.values[-6:]] == [
        "int(bool(getattr(self, 'research_v624_pay_basis', False)))", "getattr(self, '_v6_24_basis', None)",
        "v6_24_status.get('seen_age_s')", "v6_24_status.get('attempt_age_s')",
        "int(v6_24_status.get('fetches', 0) or 0)", "int(v6_24_status.get('fetch_errors', 0) or 0)"]


# ---- 5. v6.23's code, statement by statement (the committed v6.23: 2cdc0be) ---------------------------------------------

V623_COMMIT = "2cdc0be"
V624_IDENTIFIERS = {"research_v624_pay_basis"} | set(V624_NAMES)
# v6.25 (S5, research_v625_lean) only adds too, except that its making decisions read the leaned inventory v6_25_inv /
# v6_25_fills where v6.24 read inv (each is inv itself without a lean): the strip takes v6.25's additions out as well
# and reads those two names back as inv (tests/test_research_v6_25.py strips them against the committed v6.24).
V625_IDENTIFIERS, V625_LEANED = t623.V625_IDENTIFIERS, t623.V625_LEANED
# v6.26 (S6, research_v626_feed_clock) only adds too, except the freshness-limit keyword it hands each reader of the
# fundamental page (None -- v6.16's limit -- with the switch off): the strip takes both out (tests/test_research_v6_26.py
# strips them against the committed v6.25).
V626_IDENTIFIERS, V626_KEYWORDS = t623.V626_IDENTIFIERS, t623.V626_KEYWORDS
# v6.27 (S7, research_v627_touch_centre) only adds: the strip takes its additions out as well (tests/test_research_v6_27.py
# strips them against the committed v6.26).
V627_IDENTIFIERS = t623.V627_IDENTIFIERS
# v6.28 (S8, research_v628_flat_launch) only adds: the strip takes its additions out as well (tests/test_research_v6_28.py
# strips them against the committed v6.27).
V628_IDENTIFIERS = t623.V628_IDENTIFIERS
# v6.29 (S9, research_v629_touch_fp_move, and S8's first save) only adds: the strip takes its additions out as well
# (tests/test_research_v6_29.py checks them against v6.28's footprint).
V6_29_IDENTIFIERS = t623.V6_29_IDENTIFIERS
# v6.30 (the identity profile: D1 research_v630_deep_tier, D2 research_v630_s9_lookback) only adds: the strip takes its
# additions out as well (tests/test_research_v6_30.py checks them against the committed v6.29).
V6_30_IDENTIFIERS = t623.V6_30_IDENTIFIERS
# v6.31 (S12b research_v631_touch_improve, the single-write save research_v631_single_write_save) only adds: the
# strip takes its additions out as well (tests/test_research_v6_31.py checks them against the committed v6.30).
V6_31_IDENTIFIERS = t623.V6_31_IDENTIFIERS
# v6.32 (S14 research_v632_fee_making, P1 research_v632_state_fundamental) only adds: the strip takes its additions out
# as well (tests/test_research_v6_32.py checks them against the committed v6.31).
V6_32_IDENTIFIERS = t623.V6_32_IDENTIFIERS
# v6.33 (D4 research_v633_touch_identity) only adds: the strip takes its additions out as well
# (tests/test_research_v6_33.py checks them against the committed v6.32).
V6_33_IDENTIFIERS = t623.V6_33_IDENTIFIERS
# v6.34 (research_v634_class1_entry) only adds: the strip takes its additions out as well
# (tests/test_research_v6_34.py checks them against v6.33).
V6_34_IDENTIFIERS = t623.V6_34_IDENTIFIERS
# v6.36 (research_v636_step_withdrawal) only adds: the strip takes its additions out as well
# (tests/test_research_v6_36.py checks them against v6.35).
V6_36_IDENTIFIERS = t623.V6_36_IDENTIFIERS
# v6.37 (research_v637_fast_path) only adds: the strip takes its additions out as well
# (tests/test_research_v6_37.py checks them against v6.36).
V6_37_IDENTIFIERS = t623.V6_37_IDENTIFIERS
# v6.38 layer 3 only adds: the strip takes its additions out as well (tests/test_research_v6_38_l3.py).
V6_38_IDENTIFIERS = t623.V6_38_IDENTIFIERS


def _v623(rel):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V623_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120)
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V623_COMMIT} is not available here")
    return out.stdout


def _mentions(node):
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in V625_LEANED and not isinstance(n.ctx, ast.Store):
            continue                                                         # read back as inv (visit_Name)
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V624_IDENTIFIERS or name in V625_IDENTIFIERS or name in V626_IDENTIFIERS
                                      or name in V627_IDENTIFIERS or name in V628_IDENTIFIERS
                                      or name in V6_29_IDENTIFIERS or name in V6_30_IDENTIFIERS
                                      or name in V6_31_IDENTIFIERS
                                      or name in V6_32_IDENTIFIERS
                                      or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name in V6_36_IDENTIFIERS
                                      or name in V6_37_IDENTIFIERS
                                      or name in V6_38_IDENTIFIERS
                                      or name.startswith(("_v6_24_", "v6_24_", "_v6_25_", "v6_25_", "_v6_26_",
                                                          "v6_26_", "_v6_27_", "v6_27_", "_v6_28_", "v6_28_",
                                                          "_v6_29_", "v6_29_", "_v6_30_", "v6_30_", "_v6_31_",
                                                          "v6_31_", "_v6_32_", "v6_32_", "_v6_33_", "v6_33_",
                                                          "_v6_34_", "v6_34_", "_v6_36_", "v6_36_",
                                                          "_v6_37_", "v6_37_", "_v6_38_", "v6_38_"))):
            return True
    return False


class _Strip624(ast.NodeTransformer):
    """Removes what v6.24 (and v6.25, v6.26, v6.27, v6.28, v6.29 and v6.30 after it) adds and nothing else: its import,
    its methods, statements naming its identifiers (an if on one keeps its else), dict entries naming them, v6.26's
    limit keyword, and every docstring (v6.24 extended one); v6.25's leaned inventory reads back as inv."""

    def visit_Module(self, node):                      # v6.38's changes undone first (tests/_v638_undo.py)
        _v638_undo.undo(node)
        return self.generic_visit(node)

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v624_pay_basis", "research_v625_lean",
                                                                "research_v626_feed_clock", "research_v627_touch_centre",
                                                                "research_v628_flat_launch",
                                                                "research_v6_29_touch_move",
                                                                "research_v630_identity_profile",
                                                                "research_v631_touch_improve",
                                                                "research_v6_32_fee_making",
                                                                "research_v6_32_state_fp",
                                                                "research_v6_33_touch_identity",
                                                                "research_v6_34_class1",
                                                                "research_v6_36_step_withdrawal",
                                                                "research_v6_37_fast_path",
                                                                "research_v6_38_canary",
                                                                "research_v6_38_kbl",
                                                                "research_v6_38_lean",
                                                                "research_v6_38_keeper"):
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith(("_v6_24_", "_v6_25_", "_v6_26_", "_v6_27_",
                                                                       "_v6_28_", "_v6_29_", "_v6_30_", "_v6_31_",
                                                                       "_v6_32_", "_v6_33_", "_v6_34_", "_v6_36_",
                                                                       "_v6_37_", "_v6_38_")):
                continue
            if isinstance(st, ast.If) and _mentions(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            if isinstance(st, ast.Try) and all(_mentions(b) for b in st.body):
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions(st):
                continue
            out.append(st)
        return out

    def generic_visit(self, node):
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(node, field, None), list):
                body = getattr(node, field)
                if field == "body" and isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)) and body and \
                        isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and \
                        isinstance(body[0].value.value, str):
                    body = body[1:]                                          # the docstring
                setattr(node, field, self._stmts(body))
        for h in getattr(node, "handlers", None) or []:
            h.body = self._stmts(h.body)
        for name, value in ast.iter_fields(node):
            if name in ("body", "orelse", "finalbody", "handlers"):
                continue
            if isinstance(value, list):
                setattr(node, name, [self.visit(v) if isinstance(v, ast.AST) else v for v in value])
            elif isinstance(value, ast.AST):
                setattr(node, name, self.visit(value))
        return node

    def visit_Dict(self, node):
        pairs = [(k, v) for k, v in zip(node.keys, node.values)
                 if not ((k is not None and _mentions(k)) or _mentions(v))]
        node.keys, node.values = [k for k, _v in pairs], [v for _k, v in pairs]
        return self.generic_visit(node)

    def visit_Name(self, node):
        if node.id in V625_LEANED and not isinstance(node.ctx, ast.Store):
            node.id = "inv"                                                  # v6.25: v6.24's inv
        return node

    def visit_Call(self, node):
        node.keywords = [k for k in node.keywords if k.arg not in V626_KEYWORDS]      # v6.26: the reader's limit
        return self.generic_visit(node)

    def visit_IfExp(self, node):                                             # its body / orelse are expressions
        node.test, node.body, node.orelse = self.visit(node.test), self.visit(node.body), self.visit(node.orelse)
        return node


def _stripped(text):
    return ast.dump(_Strip624().visit(ast.parse(text)))


def test_every_v623_statement_is_in_v624_unchanged_and_v624_only_adds():
    """Simple of the committed v6.23 == Simple of v6.24 with v6.24's additions taken out: no v6.23 statement, argument,
    call or branch was changed or dropped.  v6.24's code is its import, its accessor, its switch and state, the six row
    fields, and one if on the switch whose else is v6.23's own S1 decision (switch off: v6.23, tested above)."""
    old = _v623("agents/strategy/Strategy1_Research_Simple.py")
    assert not _mentions(ast.parse(old)), "v6.23 already names a v6.24 identifier: the strip would hide a change"
    assert _stripped(old) == _stripped(SIMPLE)
    assert _stripped(SIMPLE) != _stripped(old.replace("spec = v623_validator_spec(", "spec = v623_validator_spec(0 or "))
    stripped_new = _Strip624().visit(ast.parse(SIMPLE))
    assert not _mentions(stripped_new)                                   # nothing of v6.24 left behind
    assert ast.dump(stripped_new) != ast.dump(ast.parse(SIMPLE))


def test_the_launcher_only_adds_to_v623s():
    """The committed v6.23 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS'
    last key), the rest added -- the v6.24 block and its gate entry.  v6.25 extends the same three lines, adds its block
    and gate entry, and re-points three greps (v6.6, v6.17, v6.23) at the leaned inventory its pass hands over.  v6.26,
    v6.27, v6.28 and v6.29 each extend the same three lines and add their block and gate entry, and v6.30 extends them
    with two PARAMS keys."""
    launcher = _v638_undo.launcher_v637(LAUNCHER)               # v6.37's launcher: v6.38's edits undone
    old, new = _v623("run_strategy1_research_simple_multi.sh").splitlines(), launcher.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(ln for ln in old if ln.startswith("A19X_BUILD=0;"))
    arm = next(ln for ln in old if ln.startswith("  strategy1_direct_v6_3_0)"))
    greps = (('spacing.note(book_id, book_trades, getattr(self, "uid", None), {}, eps=eps)', "v6_25_fills"),
             ("placed_total += self._v617_book(response, book_id, deep, raw_bid, raw_ask, {}, touch_rows, already,",
              "v6_25_inv"),
             ("against = (self._v6_23_against(book_id, raw_bid, raw_ask, cfg, {})", "v6_25_inv"))
    v625_greps = [next(ln for ln in old if f"grep -qF '{g.format('inv')}'" in ln) for g, _name in greps]
    assert removed == [defaults, arm, 'research_v623_start_once=1"', *v625_greps]
    blocks = []
    for head in ("# v6.24 S4.", "# v6.25 S5.", "# v6.26 S6.", "# v6.27 S7.", "# v6.28 S8.", "# v6.29 S9.", "# v6.30.",
                 "# v6.31.", "# v6.32.", "# v6.33.", "# v6.34.", "# v6.36.", "# v6.37.", "# v6.38 L3."):
        start = launcher.index(head)
        blocks += launcher[start:launcher.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    leaned = [ln.replace(f"'{g.format('inv')}'", f"'{g.format(name)}'") for ln, (g, name) in zip(v625_greps, greps)]
    assert sorted(added) == sorted([defaults + "; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0"
                                    "; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0",
                                    arm + "; V6_24_BUILD=1; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1"
                                    "; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1",
                                    "research_v623_start_once=1 \\", "research_v624_pay_basis=1 \\",
                                    "research_v625_lean=1 \\", "research_v625_lean_slope=1.0 \\",
                                    "research_v625_lean_cap=48 \\", "research_v626_feed_clock=1 \\",
                                    "research_v627_touch_centre=1 \\", "research_v628_flat_launch=1 \\",
                                    "research_v629_touch_fp_move=1 \\", "research_v630_deep_tier=1 \\",
                                    "research_v630_s9_lookback=1 \\", "research_v631_touch_improve=1 \\",
                                    "research_v631_single_write_save=1 \\",
                                    "research_v632_fee_making=1 \\",
                                    "research_v632_fee_weight=0.005 \\",
                                    "research_v632_state_fundamental=1 \\",
                                    "research_v633_touch_identity=1 \\",
                                    "research_v634_class1_entry=0 \\",
                                    "research_v636_step_withdrawal=1 \\",
                                    "research_v637_fast_path=1 \\",
                                    "research_v638_kbl=0 \\",
                                    "research_v638_lean=0 \\",
                                    "research_v638_lean_arm=0 \\",
                                    "research_v638_c1=0 \\",
                                    'research_v638_c1_arm=0"', *leaned,
                                    "      tests/test_research_v6_24.py \\", "      tests/test_research_v6_25.py \\",
                                    "      tests/test_research_v6_26.py \\", "      tests/test_research_v6_27.py \\",
                                    "      tests/test_research_v6_28.py \\", "      tests/test_research_v6_29.py \\",
                                    "      tests/test_research_v6_30.py \\",
                                    "      tests/test_research_v6_31.py \\",
                                    "      tests/test_research_v6_32.py \\",
                                    "      tests/test_research_v6_33.py \\",
                                    "      tests/test_research_v6_34.py \\",
                                    "      tests/test_research_v6_36.py \\",
                                    "      tests/test_research_v6_37.py \\",
                                    "      tests/test_research_v6_38_l3.py \\", *blocks])


# ---- 6. the launcher -----------------------------------------------------------------------------------------------

def test_the_v624_block_is_gated_preflighted_and_listed():
    assert "; V6_21_BUILD=0; V6_22_BUILD=0; V6_23_BUILD=0; V6_24_BUILD=0; V6_25_BUILD=0; V6_26_BUILD=0; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_22_BUILD=1; V6_23_BUILD=1; V6_24_BUILD=1; V6_25_BUILD=1; V6_26_BUILD=1; V6_27_BUILD=1; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_24_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_24_BUILD=") == 2
    assert "      tests/test_research_v6_23.py \\\n      tests/test_research_v6_24.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.23 validator 0.6.3 PASS') < LAUNCHER.index("# v6.24 S4.") < \
        LAUNCHER.index('echo "[preflight] v6.24 pay basis PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')


_PATTERN = r"grep -qF (?:'([^']+)'|\"([^\"]+)\") \"\$(AGENT_PATH|SCRIPT_DIR)/([^\"]+)\""


def _block():
    start = LAUNCHER.index('if [[ "${V6_24_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.24 pay basis PASS', start)]


def test_every_v624_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 6, pats
    for single, double, var, name in pats:
        needle = single or double
        assert var == "AGENT_PATH" and read_source(STRATEGY / name).count(needle) == 1, (needle, name)
    needles = {single or double for single, double, _var, _name in pats}
    assert needles == {"from research_v624_pay_basis import (", SWITCH_LINE, OBSERVE_LINE,
                       "def parse_basis(text: Any) -> str | None:",
                       "def realized_active(mode: Any, spec: Any, basis: Any) -> bool:", "class SharedBasis:"}


def _run_block(params, agent_path=strategy_view(), script_dir=ROOT, build="1"):
    start = LAUNCHER.index("# v6.24 S4.")
    stop = LAUNCHER.index('echo "[preflight] v6.24 pay basis PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "SCRIPT_DIR": str(script_dir), "V6_24_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v624_block_passes_the_shipped_params_and_refuses_a_build_without_the_switch():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    assert out.stdout == ("[preflight] v6.24 pay basis PASS (S1's auto mode keys on the making basis the validator "
                          "publishes as paying: realized -> active, captured -> inactive, never read on this host -> "
                          "v6.23's release rule; one GET of its config page per 5 min per host)\n"), out.stdout
    for bad in ("research_v624_pay_basis=0", "research_v624_pay_basis=10", "research_v624_pay_basis=", "",
                "research_v624_pay_basis=1x", "xresearch_v624_pay_basis=1", "research_v624_pay_basis=true"):
        out = _run_block(shipped.replace("research_v624_pay_basis=1", bad))
        assert out.returncode == 1 and \
            "ERROR: v6.24 S4 build without research_v624_pay_basis=1 in PARAMS." in out.stderr, (bad, out.stderr)
    out = _run_block("", build="0")                                    # another build: the block is not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v624_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = re.findall(_PATTERN + r" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2", _block())
    assert len(cases) == 6, cases
    for i, (single, double, var, name, message) in enumerate(cases):
        needle = single or double
        tmp = tmp_path / str(i)
        (tmp / "agents" / "strategy").mkdir(parents=True)
        for n in ("Strategy1_Research_Simple.py", "research_v624_pay_basis.py"):
            copy_source(STRATEGY / n, tmp / "agents" / "strategy" / n)
        path = tmp / "agents" / "strategy" / name
        text = path.read_text()
        assert text.count(needle) == 1, needle
        path.write_text(text.replace(needle, "# removed"))
        out = _run_block(t617._shipped(), agent_path=tmp / "agents" / "strategy", script_dir=tmp)
        assert out.returncode == 1 and message in out.stderr and "PASS" not in out.stdout, (name, needle, out.stderr)
