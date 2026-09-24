"""v6.2.15.1: no taker stop.  The v6.2.15 build ships with S3 (the ABSOLUTE_PROTECTION loss stop) switched off.

Under the final rung the validator credits every fill's capture to its taker as well as its maker (31-print centred
mid), so a stop market order lands on the making leg -- the only leg that scores while our per-book alpha sits far
under the skill floor.  Mainnet UID 94 on v6.2.15 (2026-09-24, ticks 1-300): 118 stop takers on 113 positions filled
128 times, 16 bps past the prior mid; their capture was -7.55 (-9.2 / -6.7 bps of notional on sells / buys) and took
making from +4.34 (every other fill) to raw -10.47, score 0 -- against 1.84 for v6.2.14 on its own first 300 ticks.
The stops were right on direction (the price kept moving against the closed position: median +33 bps at 60 s, +75 bps
at 120 s), so the rule belongs to the skill leg (v6.3), not to the making-only v6.2 line.  S1 and S2 stay on; the S3
code stays behind its switch.
"""
import ast
import sys
import textwrap
import types
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _harness import extractor  # noqa: E402
import test_research_v6_2_15_order_life as t15  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
DEBUG = (STRATEGY / "Strategy1_Debug.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
KEYS = ("research_v6215_order_life", "research_v6215_side_ownership", "research_v6215_loss_stop")


def _params():
    """The launcher's PARAMS string as {key: value}."""
    body = LAUNCHER[LAUNCHER.index('PARAMS="') + len('PARAMS="'):]
    body = body[:body.index('"')]
    out = {}
    for token in body.replace("\\\n", " ").split():
        key, _, value = token.partition("=")
        out[key] = value
    return out


def _switch_agent(**config):
    """Run the three v6.2.15 switch assignments of _init_build_switches against a config."""
    fn = ast.parse(textwrap.dedent(_simple("_init_build_switches"))).body[0]
    fn.body = [node for node in fn.body if isinstance(node, ast.Assign) and any(
        isinstance(t, ast.Attribute) and t.attr in KEYS for t in node.targets)]
    assert len(fn.body) == 3
    as_bool = ast.parse(textwrap.dedent(extractor(DEBUG, cls_name="Strategy1_Debug")("_as_bool"))).body[0]
    ns = {"Any": Any}
    exec(compile(ast.Module(body=[fn, as_bool], type_ignores=[]), "<v62151>", "exec"), ns)
    agent = types.SimpleNamespace(config=types.SimpleNamespace(**config), _as_bool=ns["_as_bool"])
    ns["_init_build_switches"](agent)
    return agent


# ---- 1. the switch ----------------------------------------------------------------------------------------------

def test_a_config_without_the_keys_runs_s1_and_s2_without_the_stop():
    agent = _switch_agent()
    assert agent.research_v6215_order_life is True and agent.research_v6215_side_ownership is True
    assert agent.research_v6215_loss_stop is False


def test_the_launcher_ships_s1_and_s2_on_and_the_stop_off():
    params = _params()
    assert params["research_v6215_order_life"] == "1" and params["research_v6215_side_ownership"] == "1"
    assert params["research_v6215_loss_stop"] == "0"
    agent = _switch_agent(**{key: params[key] for key in KEYS})
    assert (agent.research_v6215_order_life, agent.research_v6215_side_ownership, agent.research_v6215_loss_stop) \
        == (True, True, False)


def test_the_switch_still_turns_the_stop_on_when_asked():
    # the rule is kept for the skill leg: an explicit 1 still arms it
    assert _switch_agent(research_v6215_loss_stop="1").research_v6215_loss_stop is True


# ---- 2. a position inside ABSOLUTE_PROTECTION keeps the v6.2.14 exit ---------------------------------------------

def test_a_deep_loser_is_not_stopped_and_its_taker_is_rewritten_and_refused_as_in_v6214():
    stop = _switch_agent().research_v6215_loss_stop
    note = t15._note_agent(stop=stop)
    assert not note._v6215_note_stop(3, t15._inv(0.25, 300.0), 290.0)          # -333 bps by the mid mark
    assert not note._v6215_stop_active(3) and note._v6215_stop_books == {} and note._v6215_counts == {}
    taker = types.SimpleNamespace(action="TAKER_EXIT", selected_qty=0.25)
    kw = dict(book=None, inventory=types.SimpleNamespace(net_base=0.25), exit_kwargs={"inventory_qty": 0.25})
    with pytest.raises(LookupError):                                            # the v6.1 rewrite runs
        t15._rewrite_agent(True, stop_on=stop)._v61_rewrite_exit(3, taker, **kw)
    agent = t15._exec_agent(True, stop_on=stop)
    assert agent._execute_aggressive_close(None, 3, None, 0.25, True) is False  # the no-loss executor refuses
    assert agent.sent == [] and agent.refused == [3] and agent._v6215_counts == {}


def test_the_exit_at_the_touch_is_not_cancelled_for_a_stop_when_off():
    stop = _switch_agent().research_v6215_loss_stop
    agent = t15._stop_life_agent(stop=stop, stop_books=(3,), net={3: 0.25})
    t15.t14._rest(agent, 11, 3, 1, 100.01, cid=80032)
    resp = t15.t14._Resp()
    agent._v6214_service_touch_life(resp, t15.t14._state({3: t15.t14._touch_book()}))
    assert t15.t14._cancelled(resp) == set() and agent._v6215_counts.get("stop_exit_cancels", 0) == 0


# ---- 3. wiring ----------------------------------------------------------------------------------------------------

def test_the_preflight_refuses_a_stop_in_params_or_in_the_agent_default():
    block = LAUNCHER[LAUNCHER.index('if [[ "$V6215_BUILD" == "1" ]]; then'):]
    block = block[:block.index('echo "[preflight] v6.2.15.1 no taker stop PASS"')]
    assert "for key in research_v6215_order_life research_v6215_side_ownership; do" in block
    assert "for key in research_v6215_order_life research_v6215_side_ownership research_v6215_loss_stop; do" not in block
    assert """grep -qF 'getattr(self.config, "research_v6215_loss_stop", False)' "$AGENT_PATH/Strategy1_Research_Simple.py" ||""" \
        in block
    assert '[[ "$PARAMS" == *"research_v6215_loss_stop=0"* && "$PARAMS" != *"research_v6215_loss_stop=1"* ]] ||' in block
    assert '[[ "$INHERITED_SHORT_LOTS" == "exit" ]] ||' in block


def test_the_fix_release_keeps_the_v6215_pin_arm_and_stop_code_and_is_gated():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE
    assert "strategy1_direct_v6_2_15)" in LAUNCHER
    assert "[preflight] v6.2.15.1 no taker stop PASS" in LAUNCHER
    assert "tests/test_research_v6_2_15_1_no_taker_stop.py" in LAUNCHER
    # the S3 code stays (the preflight still checks it) so the skill leg can switch it back on
    for site in ("self._v6215_note_stop(book_id, inventory, mid)", "why = V6215_STOP_REASON",
                 'if not ok and bool(getattr(self, "research_v6215_loss_stop", False)) and self._v6215_stop_active(int(book_id)):'):
        assert site in SIMPLE
    assert "loss_stop_on=int(bool(getattr(self, \"research_v6215_loss_stop\", False)))" in SIMPLE
