"""v6.18: S1 the v6.16 fundamental centre is off -- research_v618_centre_off.

v6.17 = v6.14 + v6.15 launch + v6.16 centre + v6.17 touch quote.  Taken apart on the same tapes (true-rule replays,
scratchpad k25) the touch quote carries the whole gain and the centre spends the volume cap on the ladder's
lowest-capture fills wherever the cap binds: previous simulation +57.3% without it against +27.7% with it, an aged UID
switched from v6.14 +41.0% against -7.3%.  With the switch every book's ladder keeps the mid (v6.15 exactly) while the
v6.17 touch quote keeps the published fundamental; without it the build is v6.17.
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v618_centre_off as co  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
CALL_LINE = 'if v618_centre_on(getattr(self, "research_v616_fair_value", False), v618_off):'
SWITCH_LINE = 'self.research_v618_centre_off = self._as_bool(getattr(self.config, "research_v618_centre_off", True))'
MID = {("BUY", 99.80), ("SELL", 100.23)}         # v6.15: level 0 at 21.5 ticks either side of the mid 100.015
CENTRED = {("BUY", 99.99), ("SELL", 100.23)}     # v6.16 with the fundamental 38 bps above the mid


# ---- 1. the module ------------------------------------------------------------------------------------------------------

def test_the_centre_applies_only_with_its_own_switch_on_and_v618s_off():
    assert co.centre_on(True, False) is True
    for fair_value_on, centre_off in ((True, True), (False, False), (False, True), (None, None), (0, 1), (1, 1)):
        assert co.centre_on(fair_value_on, centre_off) is False, (fair_value_on, centre_off)
    assert co.V618_CENTRE_OFF_VERSION == "centre_off_v6_18"


# ---- 2. the agent -------------------------------------------------------------------------------------------------------

def _agent(page, *, centre_off=True, fair_value=True):
    agent = t616._fv_agent(page, on=fair_value)
    agent.research_v618_centre_off = centre_off
    agent._v618_counts = {}
    return agent


def test_with_the_switch_every_ladder_keeps_the_mid_even_with_a_fresh_fundamental():
    for fp in (100.40, 99.60, 100.10, 100.015):
        agent = _agent(t616._page({3: fp}))
        assert t616._level0(t616._run(agent)) == MID, fp
        assert agent._v618_counts == {"centre_off_books": 1}, fp
        assert agent._v616_errors == 0
        assert not any(k.startswith(("placed_", "book_", "cancel_")) for k in agent._v616_counts), agent._v616_counts


def test_without_the_switch_the_build_is_v617_and_v618_counts_nothing():
    agent = _agent(t616._page({3: 100.40}), centre_off=False)
    assert t616._level0(t616._run(agent)) == CENTRED
    assert agent._v618_counts == {} and agent._v616_counts.get("book_armed") == 1
    agent = _agent(t616._page({3: 99.60}), centre_off=False)
    assert t616._level0(t616._run(agent)) == {("BUY", 99.80), ("SELL", 100.04)}


def test_with_the_v616_switch_off_the_v618_switch_changes_nothing():
    for centre_off in (True, False):
        agent = _agent(t616._page({3: 100.40}), centre_off=centre_off, fair_value=False)
        assert t616._level0(t616._run(agent)) == MID
        assert agent._v618_counts == {} and agent._v616_counts == {}


def test_a_resting_order_at_the_old_centre_is_judged_by_the_mid_band_again():
    agent = _agent(t616._page({3: 100.40}))
    t63.t14._rest(agent, 21, 3, 0, 99.99, cid=40031)       # v6.16's centred bid: 2.5 ticks from the mid, inside 0.5 x depth
    t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)
    resp = t616._run(agent)
    assert t63._cancelled(resp) == {(3, 21)}
    assert "cancel_centre_reprice" not in agent._v616_counts
    agent = _agent(t616._page({3: 100.40}))
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)       # v6.15's bid: kept, and nothing new on either side
    t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)
    resp = t616._run(agent)
    assert t63._cancelled(resp) == set() and t616._level0(resp) == set()


def test_the_touch_quote_keeps_the_fundamental_with_the_centre_off():
    agent = t617._tq_agent(t616._page({3: 100.40}))
    agent.research_v618_centre_off = True
    agent._v618_counts = {}
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)       # v6.15's level 0 on both sides: the deep layer places nothing
    t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)
    resp = t617._run(agent)
    assert t617._touch(resp) == {("BUY", 100.00, 2.0)}     # one deep clip at the best bid, toward the fundamental
    assert agent._v617_counts.get("placed_buy") == 1 and agent._v617_errors == 0
    assert agent._v618_counts == {"centre_off_books": 1} and t63._cancelled(resp) == set()


def test_the_snapshot_reports_the_version_and_the_counts():
    snap = t616._method("_v618_snapshot", {"V618_CENTRE_OFF_VERSION": co.V618_CENTRE_OFF_VERSION})
    agent = _agent(t616._page({3: 100.40}))
    t616._run(agent)
    assert snap(agent) == {"version": "centre_off_v6_18", "counts": {"centre_off_books": 1}}
    assert 'centre_off_on=int(bool(getattr(self, "research_v618_centre_off", False))),' in SIMPLE
    assert ('centre_off=(self._v618_snapshot() if getattr(self, "research_v618_centre_off", None) is not None else {}),'
            in SIMPLE)


# ---- 3. wiring and the launcher -------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    params = t617._params()
    assert "research_v618_centre_off=1" in params and "research_v618_centre_off=0" not in params
    assert SWITCH_LINE in SIMPLE
    assert "research_v616_fair_value=1" in params and "research_v617_touch_quote=1" in params   # the feed and the quote stay


def test_the_call_site_gates_the_v616_centre_and_keeps_its_preflighted_line():
    assert SIMPLE.count(CALL_LINE) == 1 and SIMPLE.count(t616.BOOK_LINE) == 1
    start = SIMPLE.index(CALL_LINE)
    assert 0 < SIMPLE.index(t616.BOOK_LINE, start) - start < 120          # the v6.16 call sits directly under the gate
    assert 'if bool(getattr(self, "research_v616_fair_value", False)):\n            try:' not in SIMPLE
    assert SIMPLE.count("from research_v618_centre_off import (") == 1


def test_the_v618_block_is_gated_preflighted_and_listed():
    assert "; V6_16_BUILD=0; V6_17_BUILD=0; V6_18_BUILD=0; V6_19_BUILD=0; V6_20_BUILD=0; V6_21_BUILD=0; V6_22_BUILD=0\n" in LAUNCHER
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_16_BUILD=1; V6_17_BUILD=1; V6_18_BUILD=1; V6_19_BUILD=1; V6_20_BUILD=1; V6_21_BUILD=1; V6_22_BUILD=1"), arm
    assert 'if [[ "${V6_18_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert "tests/test_research_v6_18.py" in LAUNCHER


def test_every_v618_preflight_pattern_is_in_the_file_it_checks():
    start = LAUNCHER.index('if [[ "${V6_18_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.18 centre off PASS', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 3, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.18 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.18 centre off PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_18_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v618_block_passes_the_shipped_params_and_refuses_the_switch_off():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and "[preflight] v6.18 centre off PASS" in out.stdout, out.stderr
    for bad in ("research_v618_centre_off=0", "research_v618_centre_off=10", ""):
        out = _run_block(shipped.replace("research_v618_centre_off=1", bad))
        assert out.returncode == 1 and "v6.18 S1 build without research_v618_centre_off=1" in out.stderr, repr(bad)
    for kept in ("research_v616_fair_value=1", "research_v617_touch_quote=1"):
        assert shipped.count(kept) == 1, kept
        out = _run_block(shipped.replace(kept, kept[:-1] + "0"))
        assert out.returncode == 1 and "the touch quote it keeps needs" in out.stderr, kept


def test_the_v618_block_refuses_an_agent_without_the_import_the_gate_or_the_rule():
    cases = (("Strategy1_Research_Simple.py", "from research_v618_centre_off import (", "v6.18 S1 module is not imported"),
             ("Strategy1_Research_Simple.py", CALL_LINE, "v6.18 S1 the v6.16 centre is not behind the v6.18 switch"),
             ("research_v618_centre_off.py", "def centre_on(fair_value_on: Any, centre_off: Any) -> bool:",
              "v6.18 S1 research_v618_centre_off has no centre_on"))
    for name, needle, message in cases:
        tmp = Path(tempfile.mkdtemp(prefix="t618_"))
        try:
            for n in ("Strategy1_Research_Simple.py", "research_v618_centre_off.py"):
                shutil.copy(STRATEGY / n, tmp / n)
            text = (tmp / name).read_text()
            assert text.count(needle) == 1, needle
            (tmp / name).write_text(text.replace(needle, "# removed"))
            out = _run_block(t617._shipped(), agent_path=tmp)
            assert out.returncode == 1 and message in out.stderr, (name, out.stderr)
        finally:
            shutil.rmtree(tmp)
