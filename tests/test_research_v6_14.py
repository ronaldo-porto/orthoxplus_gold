"""v6.14: S1 the deep layer opens a book on its depth and inventory, not on its paper alpha -- research_v614_book_floor_off.

v6.3.3's per-book rule (kept by v6.8 S2 / v6.11) closed a book whose own paper alpha is under -0.5 x the floor (30, the
0.6.2 skill floor the validator removed on 09-29).  Since 09-29 the making half pays every book's captured spread and
negative alpha is unpaid.  Replayed on both whole simulations (10-04): the floor kept 24% of book-states closed in this
simulation's quiet trend; without it making share +16.6% here and +8.1% on the previous simulation, eligible alpha +5%.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v614_book_floor as bf  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_3_3_deep_layer as d633  # noqa: E402
import test_research_v6_8 as t68  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
FLOOR = 30.0                                     # research_v63_alpha_floor as shipped: the per-book floor is -15
CALL_LINE = "if not deep_open and not v614_paper_floor_on(v614_off):"


def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]


def _shipped():
    return _params()[len('PARAMS="'):].replace("\\\n", " ")


# ---- 1. the rule ------------------------------------------------------------------------------------------------------

def test_the_switch_turns_the_paper_floor_off():
    assert bf.V614_BOOK_FLOOR_VERSION == "book_floor_off_v6_14"
    assert bf.paper_floor_on(False) is True and bf.paper_floor_on(None) is True
    assert bf.paper_floor_on(True) is False and bf.paper_floor_on(1) is False


def test_a_book_under_the_paper_floor_opens_only_without_the_floor():
    alphas = {1: -23.0, 2: -10.1, 3: 7.0}                                   # -23 is under -0.5 x 30
    layer = d633._deep_layer(books=tuple(alphas), alpha=0.0)
    for b, a in alphas.items():
        layer.books[b].paper.buckets[dl.sampled_key(t63.NOW)] = [a, 0.0, 1.0, 0.0]
    layer.per_book_gate = True
    assert [b for b in alphas if layer.book_open(b, FLOOR, 0.0)] == [2, 3]                       # v6.12
    assert [b for b in alphas if layer.book_open(b, FLOOR, 0.0, paper_floor=False)] == [1, 2, 3]  # v6.14


def test_without_the_floor_depth_and_the_inventory_bound_still_close_a_book():
    layer = d633._deep_layer(books=(1,), alpha=-50.0)
    layer.per_book_gate = True
    bound = layer.max_clips * layer.clip
    assert layer.book_open(1, FLOOR, 0.0, paper_floor=False)
    assert not layer.book_open(1, FLOOR, bound + 1.0, paper_floor=False)                       # over the bound
    assert layer.book_open(1, FLOOR, bound + 1.0, inventory_bound=False, paper_floor=False)    # v6.4 S2 reducing side
    layer.books[1].sweeps.clear()                                                              # depth unknown
    assert not layer.book_open(1, FLOOR, 0.0, paper_floor=False)
    assert not layer.book_open(2, FLOOR, 0.0, paper_floor=False)                               # a book it never saw


def test_the_floor_default_is_unchanged_for_every_other_caller():
    layer = d633._deep_layer(books=(1,), alpha=-20.0)
    layer.per_book_gate = True
    assert not layer.book_open(1, FLOOR, 0.0)


# ---- 2. the agent -----------------------------------------------------------------------------------------------------

def _pass(off):
    layer = d633._deep_layer(books=(3, 5), alpha=-5.0)
    layer.books[5].paper.buckets[dl.sampled_key(t63.NOW)] = [-20.0, 0.0, 1.0, 0.0]     # book 5 under the floor
    layer.per_book_gate = True
    agent = t68.t64._agent(layer)
    agent.research_v68_no_touch_fallback = True
    agent._v68_counts, agent._v68_errors = {}, 0
    agent.research_v614_book_floor_off = off
    agent._v614_counts, agent._v614_errors = {}, 0
    return agent, t68.t64._run(agent, {3: t63._book(), 5: t63._book()})


def test_the_shipped_v612_pass_idles_the_book_under_the_floor_and_v614_trades_it():
    agent, resp = _pass(False)
    assert t63._placed(resp) == t68._deep_sides(3) and agent._v68_counts == {"idle_book_states": 1}
    assert agent._v614_counts == {}
    agent, resp = _pass(True)
    assert t63._placed(resp) == t68._deep_sides(3) | t68._deep_sides(5)
    assert agent._v68_counts == {} and agent._v614_counts == {"opened_past_paper_floor": 1}
    assert agent._v614_errors == 0


def test_the_switch_is_in_the_state_record():
    assert 'book_floor_off_on=int(bool(getattr(self, "research_v614_book_floor_off", False))),' in SIMPLE
    assert "book_floor=(self._v614_snapshot()" in SIMPLE


# ---- 3. wiring --------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_agrees_with_the_agent_default():
    params = _params()
    assert "research_v614_book_floor_off=1" in params and "research_v614_book_floor_off=0" not in params
    assert ('self.research_v614_book_floor_off = self._as_bool(getattr(self.config, "research_v614_book_floor_off", True))'
            in SIMPLE)
    assert SIMPLE.count(CALL_LINE) == 1
    assert "research_v68_book_gate=1 " in params                     # v6.11's gate stays: only the floor goes


def test_the_v614_block_is_gated_preflighted_and_listed():
    assert "; V6_10_BUILD=0; V6_11_BUILD=0; V6_12_BUILD=0; V6_14_BUILD=0; V6_15_BUILD=0; V6_16_BUILD=0; V6_17_BUILD=0; V6_18_BUILD=0\n" in LAUNCHER   # v6.15, v6.16 append
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_11_BUILD=1; V6_12_BUILD=1; V6_14_BUILD=1; V6_15_BUILD=1; V6_16_BUILD=1; V6_17_BUILD=1; V6_18_BUILD=1"), arm
    assert 'if [[ "${V6_14_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert 'echo "[preflight] v6.14 per-book paper floor off PASS"' in LAUNCHER
    assert "tests/test_research_v6_14.py" in LAUNCHER


def test_every_v614_preflight_pattern_is_in_the_file_it_checks():
    start = LAUNCHER.index('if [[ "${V6_14_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.14 per-book paper floor off PASS"', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 3, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.14 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.14 per-book paper floor off PASS"', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_14_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v614_block_passes_the_shipped_params_and_refuses_the_switch_off():
    out = _run_block(_shipped())
    assert out.returncode == 0 and "[preflight] v6.14 per-book paper floor off PASS" in out.stdout, out.stderr
    for bad in ("research_v614_book_floor_off=0", "research_v614_book_floor_off=10", ""):
        out = _run_block(_shipped().replace("research_v614_book_floor_off=1", bad))
        assert out.returncode == 1 and "v6.14 S1 build without research_v614_book_floor_off=1" in out.stderr, repr(bad)


def test_the_v614_block_refuses_an_agent_that_ignores_the_switch_or_a_layer_without_it(tmp_path):
    for f in STRATEGY.glob("*.py"):
        (tmp_path / f.name).write_text(f.read_text())
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace(CALL_LINE, "if False:"))
    out = _run_block(_shipped(), agent_path=tmp_path)
    assert out.returncode == 1 and "does not read research_v614_book_floor_off" in out.stderr
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE)
    layer_src = (STRATEGY / "research_v633_deep_layer.py").read_text()
    (tmp_path / "research_v633_deep_layer.py").write_text(layer_src.replace("if not paper_floor:", "if False:"))
    out = _run_block(_shipped(), agent_path=tmp_path)
    assert out.returncode == 1 and "has no paper_floor switch" in out.stderr
