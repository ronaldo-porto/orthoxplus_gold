"""v6.3.1 S1: v6.3's per-simulation state starts over when the validator starts a new simulation.

At a new simulation the v6.2.11 own-alpha mirror discards its window, and ``book_alphas`` lists only books with own
fills in it.  The v6.3 book stop keeps a pause when a book has no alpha, and a paused book targets zero and quotes no
making layer: flat at the start of the new simulation, it never fills, never gets an alpha, and stays paused for the
rest of the process (28 of 128 books were paused at tick 1,000 of the live mainnet run).  The 120-state mid history
spans the reset, so each book's first ~120 targets would follow the jump between the two simulations' prices.
"""
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v631_sim_reset as sr  # noqa: E402
import research_v6211_score_logic as sl  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
S = 1_000_000_000
NOW = t63.NOW
NEW_SIM = 5 * S                 # the first state of a new simulation: its clock starts near zero


# ---- 1. the rule -------------------------------------------------------------------------------------------------

def test_a_new_simulation_is_a_new_id_or_a_clock_back_by_more_than_the_mirrors_rebase_jump():
    assert sr.new_simulation(last_ts=None, ts=NOW, last_sim_id=None, sim_id=None) is None          # the first state
    assert sr.new_simulation(last_ts=NOW, ts=NOW + S, last_sim_id="a", sim_id="a") is None           # the same run
    assert sr.new_simulation(last_ts=NOW, ts=NOW - 36 * S, last_sim_id=None, sim_id=None) is None    # a checkpoint rewind
    assert sr.new_simulation(last_ts=NOW, ts=NEW_SIM, last_sim_id=None, sim_id=None) == sr.REASON_CLOCK_REWIND
    assert sr.new_simulation(last_ts=NOW, ts=NOW + S, last_sim_id="a", sim_id="b") == sr.REASON_SIM_ID_CHANGE
    assert sr.new_simulation(last_ts=NOW, ts=NEW_SIM, last_sim_id="a", sim_id=None) == sr.REASON_CLOCK_REWIND
    assert sr.new_simulation(last_ts=NOW, ts=0, last_sim_id=None, sim_id=None) is None               # no timestamp
    assert sr.new_simulation(last_ts="x", ts=NEW_SIM, last_sim_id=None, sim_id=None) is None


def test_the_rule_resets_exactly_when_the_alpha_mirror_discards_its_window():
    assert sr.REBASE_MIN_JUMP_NS == sl.REBASE_MIN_JUMP_NS
    for back_ns in (30 * 60 * S, sl.REBASE_MIN_JUMP_NS - S, sl.REBASE_MIN_JUMP_NS + S, NOW - NEW_SIM):
        mirror = sl.OwnAlphaMirror(94)
        mirror.ingest_state(NOW, [])
        mirror.ingest_state(NOW - back_ns, [])
        reset = sr.new_simulation(last_ts=NOW, ts=NOW - back_ns, last_sim_id=None, sim_id=None) is not None
        assert reset == (mirror.rebases == 1), back_ns


# ---- 2. the pass -------------------------------------------------------------------------------------------------

def _state(books, ts, sim_id=None):
    cfg = t63.t14.b0._Config()
    if sim_id is not None:
        cfg = types.SimpleNamespace(**{k: getattr(cfg, k) for k in dir(cfg) if not k.startswith("_")}, simulation_id=sim_id)
    return types.SimpleNamespace(books=books, timestamp=ts, config=cfg)


def _paused_agent(on=True):
    """Book 3 was paused by the stop in the old simulation and its alpha is gone (the mirror reset)."""
    agent = t63._pass_agent(alphas={})
    agent.research_v631_sim_reset = on
    agent._v63_paused = {3}
    t63._rising(3, agent)
    t63._rising(4, agent)
    agent._v63_pass(t63._Resp(), _state({3: t63._book(), 4: t63._book()}, NOW), {})   # the last state of the old run
    return agent


def test_at_a_new_simulation_the_pauses_and_the_mid_histories_restart():
    agent = _paused_agent(on=True)
    assert agent._v63_paused == {3}                                   # no alpha: the stop keeps the pause
    resp = t63._Resp()
    agent._v63_pass(resp, _state({3: t63._book(), 4: t63._book()}, NEW_SIM), {})
    assert agent._v63_paused == set()
    assert all(len(h) == 1 for h in agent._v63_mids.values())          # only the new simulation's first mid
    # released and without a signal yet: the making layer quotes book 3 again, one tick inside on both sides
    assert {p for p in t63._placed(resp) if p[0] == 3} == {(3, "BUY", 65031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 1.0)}
    assert agent._v63_last["no_signal"] == 2 and agent._v63_counts.get("sim_resets") == 1
    rows = [p for e, p in agent.emitted if e == "V631_SIM_RESET"]
    assert len(rows) == 1 and rows[0]["reason"] == sr.REASON_CLOCK_REWIND
    assert rows[0]["paused_cleared"] == 1 and rows[0]["histories_cleared"] == 2
    assert rows[0]["old_ts"] == NOW and rows[0]["new_ts"] == NEW_SIM
    assert agent._v63_snapshot()["last_sim_reset"]["paused_cleared"] == 1


def test_off_the_pause_outlives_the_simulation_as_in_v630():
    agent = _paused_agent(on=False)
    resp = t63._Resp()
    agent._v63_pass(resp, _state({3: t63._book(), 4: t63._book()}, NEW_SIM), {})
    assert agent._v63_paused == {3} and not [p for p in t63._placed(resp) if p[0] == 3]
    assert agent._v63_counts.get("sim_changes") == 1 and not agent._v63_counts.get("sim_resets")
    assert not [e for e, _ in agent.emitted if e == "V631_SIM_RESET"]


def test_a_checkpoint_rewind_inside_the_simulation_keeps_the_pauses_and_histories():
    agent = _paused_agent(on=True)
    before = {b: len(h) for b, h in agent._v63_mids.items()}
    agent._v63_pass(t63._Resp(), _state({3: t63._book(), 4: t63._book()}, NOW - 36 * S), {})
    assert agent._v63_paused == {3} and not agent._v63_counts.get("sim_changes")
    assert all(len(agent._v63_mids[b]) >= before[b] for b in before)


def test_a_new_simulation_id_resets_even_when_the_clock_moves_forward():
    agent = t63._pass_agent(alphas={})
    agent._v63_paused = {3}
    agent._v63_pass(t63._Resp(), _state({3: t63._book()}, NOW, sim_id="20260918_2028"), {})
    agent._v63_pass(t63._Resp(), _state({3: t63._book()}, NOW + S, sim_id="20260925_0150"), {})
    assert agent._v63_paused == set() and agent._v63_counts.get("sim_resets") == 1
    assert [p for e, p in agent.emitted if e == "V631_SIM_RESET"][0]["reason"] == sr.REASON_SIM_ID_CHANGE


def test_a_reset_touches_nothing_but_v63s_own_state():
    agent = _paused_agent(on=True)
    agent.venue = {3: 1.5}                                              # venue truth is read, never cleared
    agent._v6214_last_taker = {3: "x"}
    agent._v63_pass(t63._Resp(), _state({3: t63._book(), 4: t63._book()}, NEW_SIM), {})
    assert agent.venue == {3: 1.5} and agent._v6214_last_taker == {3: "x"}
    assert agent._v63_errors == 0


# ---- 3. wiring ---------------------------------------------------------------------------------------------------

def test_the_pass_observes_every_state_before_it_reads_the_pauses_and_histories():
    body = SIMPLE[SIMPLE.index("    def _v63_pass(self, response, state, stats: dict) -> int:"):]
    body = body[:body.index("\n    def ", 10)]
    assert body.index("self._v631_observe_sim(state)") < body.index('paused = getattr(self, "_v63_paused", None)')
    assert body.index("self._v631_observe_sim(state)") < body.index('mids = getattr(self, "_v63_mids", None)')


def test_the_switch_defaults_on_and_the_state_row_reports_it():
    assert 'self.research_v631_sim_reset = self._as_bool(getattr(self.config, "research_v631_sim_reset", True))' in SIMPLE
    assert 'sim_reset_on=int(bool(getattr(self, "research_v631_sim_reset", False))),' in SIMPLE
    assert "research_v631_sim_reset=1" in LAUNCHER
