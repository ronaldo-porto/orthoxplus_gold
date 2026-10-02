"""v6.11: S1 a shut pooled board no longer silences every book -- research_v68_book_gate=1 (v6.8 S2, its code unchanged).

v6.9 S4 restored the pooled board as the regime gate on two premises observed on the previous simulation: the 500k/book
cap is scarce, and the gated hours spend it ~5x less efficiently.  On sim 20260929_2015 neither held and the board
silenced the whole fleet from 12:40-14:54 JST 10-01: while it was open the volume line withheld 1.7-2.6% of book-states,
deep-band capture was 1.10 bps when it shut and 1.15-1.22 after, and field making rose 39% while ours fell from 2.5% to
0.2% of the field.  A tail held the pooled mean at -6.6 (median book -2.7, 106/128 above the per-book floor), and a paper
book pinned at its bound scores alpha exactly 0 against a pool that needs > 0.  The per-book floor and the volume line stay.
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

import research_v633_deep_layer as dl  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_3_3_deep_layer as d633  # noqa: E402
import test_research_v6_8 as t68  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
FLOOR = 30.0                                     # research_v63_alpha_floor as shipped: the per-book floor is -15
GATE_LINE = 'return bool(v68_per_book_gate(bool(getattr(self, "research_v68_book_gate", False))))'


def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]


def _shipped():
    return _params()[len('PARAMS="'):].replace("\\\n", " ")


# ---- 1. the mechanism -------------------------------------------------------------------------------------------------

def _pinned_layer(books=(1, 2, 3), inv=8.0):
    layer = dl.DeepLayer()
    for b in books:
        db = dl.DeepBook()
        db.sweeps.extend([21.5] * dl.SWEEP_MIN)
        db.paper.fills = 1                                            # counted by the pool
        db.paper.inv = inv if b % 2 else -inv                         # pinned long or short at the bound
        for p in (100.0, 99.5, 99.25, 99.75, 99.0):                   # every dp exact in binary: the zero is exact
            db.paper.on_print(dl.sampled_key(t63.NOW), p, 1, 2.0, inv)    # no resting paper order: inv cannot move
        layer.books[b] = db
    return layer


def test_a_paper_book_pinned_at_its_bound_scores_exactly_zero_and_the_pool_never_reopens():
    layer = _pinned_layer()
    assert all(db.paper.alpha() == 0.0 for db in layer.books.values())    # I*drift - mean(I)*drift
    assert layer.update_board() is False and layer.board_alpha == 0.0      # the pool needs > 0
    assert [b for b in layer.books if layer.book_open(b, FLOOR, 0.0)] == []
    layer.per_book_gate = True
    assert [b for b in layer.books if layer.book_open(b, FLOOR, 0.0)] == [1, 2, 3]


def test_a_tail_shuts_the_pool_and_the_per_book_floor_opens_the_rest():
    alphas = {1: -23.0, 2: -10.1, 3: -2.7, 4: 1.7, 5: 7.0}                 # UID 2's record 10-02: p10 p25 p50 p75 p90
    layer = d633._deep_layer(books=tuple(alphas), alpha=0.0)
    for b, a in alphas.items():
        layer.books[b].paper.buckets[dl.sampled_key(t63.NOW)] = [a, 0.0, 1.0, 0.0]
    assert layer.update_board() is False and abs(layer.board_alpha + 5.42) < 1e-9
    assert [b for b in alphas if layer.book_open(b, FLOOR, 0.0)] == []                     # v6.9 / v6.10: all quiet
    layer.per_book_gate = True
    assert [b for b in alphas if layer.book_open(b, FLOOR, 0.0)] == [2, 3, 4, 5]           # v6.11: the tail only


# ---- 2. the agent -----------------------------------------------------------------------------------------------------

def test_the_shipped_v610_pass_is_silent_and_the_shipped_v611_pass_trades_the_book_its_record_opens():
    agent, resp = t68._shut_board_pass(fallback=True, book_gate=False)          # v6.9 / v6.10 as shipped: the blackout
    assert t63._placed(resp) == set() and agent._v68_counts == {"idle_book_states": 2}
    agent, resp = t68._shut_board_pass(fallback=True, book_gate=True)           # v6.11 as shipped
    assert t63._placed(resp) == t68._deep_sides(3) and agent._v68_counts == {"idle_book_states": 1}


# ---- 3. wiring --------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_and_now_agrees_with_the_agent_default():
    params = _params()
    assert "research_v68_book_gate=1 " in params and "research_v68_book_gate=0" not in params
    assert 'self.research_v68_book_gate = self._as_bool(getattr(self.config, "research_v68_book_gate", True))' in SIMPLE
    assert GATE_LINE in SIMPLE


def test_the_v611_block_is_gated_preflighted_and_listed():
    assert "; V6_10_BUILD=0; V6_11_BUILD=0; V6_12_BUILD=0\n" in LAUNCHER        # v6.12 appends its own
    arm = next(line for line in LAUNCHER.splitlines() if line.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_10_BUILD=1; V6_11_BUILD=1; V6_12_BUILD=1"), arm
    assert 'if [[ "${V6_11_BUILD:-0}" == "1" ]]; then' in LAUNCHER
    assert 'echo "[preflight] v6.11 per-book gate PASS"' in LAUNCHER
    assert "tests/test_research_v6_11.py" in LAUNCHER
    assert 'research_v68_book_gate=0 "*' not in LAUNCHER                         # v6.9 S4's check is retired


def test_every_v611_preflight_pattern_is_in_the_file_it_checks():
    start = LAUNCHER.index('if [[ "${V6_11_BUILD:-0}" == "1" ]]; then')
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.11 per-book gate PASS"', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 1, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def _run_block(params, agent_path=STRATEGY):
    start = LAUNCHER.index("# v6.11 S1.")
    stop = LAUNCHER.index('echo "[preflight] v6.11 per-book gate PASS"', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_11_BUILD": "1"}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env)


def test_the_v611_block_passes_the_shipped_params_and_refuses_the_v69_value():
    out = _run_block(_shipped())
    assert out.returncode == 0 and "[preflight] v6.11 per-book gate PASS" in out.stdout, out.stderr
    for bad in ("research_v68_book_gate=0 ", "research_v68_book_gate=10 ", ""):      # v6.9's value, a longer one, gone
        out = _run_block(_shipped().replace("research_v68_book_gate=1 ", bad))
        assert out.returncode == 1 and "v6.11 S1 build without research_v68_book_gate=1" in out.stderr, repr(bad)


def test_the_v611_block_refuses_an_agent_whose_gate_ignores_the_switch(tmp_path):
    assert SIMPLE.count(GATE_LINE) == 1
    (tmp_path / "Strategy1_Research_Simple.py").write_text(SIMPLE.replace(GATE_LINE, "return False"))
    out = _run_block(_shipped(), agent_path=tmp_path)
    assert out.returncode == 1 and "v6.11 S1 the per-book gate does not read research_v68_book_gate" in out.stderr
