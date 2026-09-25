"""v6.3.2 S2: the trend target trades a book only while its own paper record would earn skill there, and a gated or
stopped book keeps its two-sided making layer.

Mainnet UID 94 on v6.3.1 in sim 20260924_1653: the median book's 120-s return autocorrelation is negative in every
1,000-state block (-0.04 to -0.29), ideal +/-2 positions reached 60 states late earn +89 over a 3-h window (the previous
simulation: +25,883), the target lost ~-880 in its first 1,600 states and the book stop then held 70.7% of all
book-time paused -- with no making layer either.  Cost-inclusive replays: 11 of 11 directional variants lose; a
two-sided maker one tick inside scores making 331 / alpha -67 against 81 / -1,000 for the target with the stop off.
"""
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v632_target_gate as tg  # noqa: E402
import research_v6215_order_life as ol  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000


# ---- 1. the paper record ------------------------------------------------------------------------------------------

def test_the_keys_are_the_validators_sampled_clock_and_the_lag_is_the_order_backstop():
    assert tg.sampled_key(1_234 * S) == 1_200 * S and tg.sampled_key(600 * S) == 600 * S
    assert tg.PAPER_LAG_NS == int(ol.BACKSTOP_MS * 1_000_000) == 50 * S
    assert tg.SAMPLE_NS == 600 * S and tg.LOOKBACK_NS == 10_800 * S
    assert tg.OPEN_FLOOR_FRACTION == 1.0 and tg.CLOSE_FLOOR_FRACTION == 0.5


def test_each_rule_target_is_held_from_one_lag_after_it_was_set():
    p = tg.PaperBook()
    rules = {0: 2.0, 10: -2.0, 20: 2.0, 30: 0.0}
    seen = {}
    rule = 0.0
    for t in range(0, 120):
        rule = rules.get(t, rule)
        p.observe(1000 * S + t * S, 100.0, rule)
        seen[t] = p.position
    assert seen[49] == 0.0 and seen[50] == 2.0 and seen[59] == 2.0 and seen[60] == -2.0 and seen[70] == 2.0 and seen[80] == 0.0
    assert not p.targets


def _brute_alpha(times, mids, rules, lag_s, now_s, lookback_s, sample_s=600):
    """sum(pos x dmid) - mean(pos) x sum(dmid) over the intervals whose closing state's sampled key is in the window."""
    thr = now_s - lookback_s
    pos_at = {}
    changes = []
    last = None
    for t, r in zip(times, rules):
        if last is None or r != last:
            changes.append((t, r)); last = r
    def pos(t):
        p = 0.0
        for ts, r in changes:
            if ts <= t - lag_s:
                p = r
        return p
    s_pdm = s_p = n = s_dm = 0.0
    for i in range(1, len(times)):
        t = times[i]
        if (t // sample_s) * sample_s < thr:
            continue
        held = pos(times[i - 1])
        dm = mids[i] - mids[i - 1]
        s_pdm += held * dm; s_p += held; n += 1; s_dm += dm
    return 0.0 if n == 0 else s_pdm - s_p / n * s_dm


def test_the_paper_alpha_is_the_validators_arithmetic_on_the_lagged_rule_position():
    rng = random.Random(3)
    times = list(range(0, 5_000, 1))
    mids, m = [], 100.0
    for _ in times:
        m += rng.gauss(0, 0.02); mids.append(m)
    rules = [(2.0 if mids[i] > mids[max(0, i - 120)] else -2.0) if i >= 120 else 0.0 for i in range(len(times))]
    p = tg.PaperBook()
    for t, mid, r in zip(times, mids, rules):
        p.observe(t * S, mid, r, lag_ns=50 * S, sample_ns=600 * S)
    now = times[-1]
    p.prune(now * S, lookback_ns=1_800 * S)
    want = _brute_alpha(times, mids, rules, 50, now, 1_800)
    assert abs(p.alpha() - want) < 1e-9 * max(1.0, abs(want))
    assert all(k >= now * S - 1_800 * S for k in p.buckets)


def _run(regime, floor=1.0, n=6_000):
    """A book whose mid trends slowly (period 2,000 s) or oscillates fast (period 100 s); the rule is v6.3's 120-s sign."""
    gate = tg.TargetGate(lookback_ns=1_800 * S)
    period = 2_000.0 if regime == "trend" else 100.0
    opened = False
    for t in range(n):
        mid = 100.0 + 0.5 * math.sin(2 * math.pi * t / period)
        past = 100.0 + 0.5 * math.sin(2 * math.pi * (t - 120) / period)
        rule = (2.0 if mid > past else -2.0) if t >= 120 else 0.0
        gate.maybe_prune(t * S)
        opened = gate.step(3, t * S, mid, rule, floor, stopped=False) or opened
    return gate, opened


def test_a_book_that_trends_opens_its_target_and_one_that_reverts_inside_the_lookback_never_does():
    trend, opened = _run("trend")
    assert opened and trend.books[3].alpha() > 1.0 and trend.opens >= 1
    chop, opened = _run("chop")
    assert not opened and chop.books[3].alpha() < 0.0 and chop.opens == 0


def test_the_gate_opens_at_the_floor_closes_under_half_of_it_and_on_the_stop():
    assert tg.gate_step(False, 30.0, 30.0, stopped=False) and not tg.gate_step(False, 29.9, 30.0, stopped=False)
    assert tg.gate_step(True, 15.0, 30.0, stopped=False) and not tg.gate_step(True, 14.9, 30.0, stopped=False)
    assert not tg.gate_step(True, 100.0, 30.0, stopped=True) and not tg.gate_step(False, 100.0, 30.0, stopped=True)
    assert not tg.gate_step(False, 100.0, 0.0, stopped=False) and not tg.gate_step(True, float("nan"), 30.0, stopped=False)


def test_every_book_starts_closed_the_gate_counts_and_prunes_on_the_validators_cadence_and_reset_clears_it():
    gate = tg.TargetGate()
    assert gate.step(1, 10 * S, 100.0, 2.0, 30.0, stopped=False) is False and gate.open == set()
    gate.books[1].buckets[tg.sampled_key(10 * S)] = [100.0, 0.0, 1.0, 0.0]        # a strong paper record
    assert gate.step(1, 11 * S, 100.0, 2.0, 30.0, stopped=False) is True and gate.opens == 1
    assert gate.step(1, 12 * S, 100.0, 2.0, 30.0, stopped=True) is False and gate.closes == 1
    gate.maybe_prune(100 * S); last = gate.last_prune_ts
    gate.maybe_prune(130 * S)
    assert gate.last_prune_ts == last                                             # < 60 s: no prune
    gate.maybe_prune(161 * S)
    assert gate.last_prune_ts == 161 * S
    snap = gate.snapshot()
    assert snap["books"] == 1 and snap["opens"] == 1 and snap["closes"] == 1 and snap["lag_s"] == 50.0
    gate.reset()
    assert gate.books == {} and gate.open == set() and gate.opens == 0 and gate.last_prune_ts is None


def test_the_boards_record_opens_every_book_and_a_stop_still_closes_one():
    gate = tg.TargetGate()
    key = tg.sampled_key(10 * S)
    for b, a in ((1, 100.0), (2, 5.0), (3, 0.0)):                        # mean 35: over a floor of 30
        gate.books[b] = tg.PaperBook(); gate.books[b].buckets[key] = [a, 0.0, 1.0, 0.0]
    assert gate.update_pool(30.0) is True and gate.pool_opens == 1 and abs(gate.pool_alpha - 35.0) < 1e-9
    assert gate.step(2, 11 * S, 100.0, 2.0, 30.0, stopped=False) is True   # a weak own record opens with the board
    assert gate.step(3, 11 * S, 100.0, 2.0, 30.0, stopped=True) is False   # the realized stop closes it anyway
    for b in (1, 2, 3):
        gate.books[b].buckets[key] = [10.0, 0.0, 1.0, 0.0]               # mean 10: under half the floor
    assert gate.update_pool(30.0) is False
    assert gate.step(2, 12 * S, 100.0, 2.0, 30.0, stopped=False) is False
    gate.books[1].buckets[key] = [16.0, 0.0, 1.0, 0.0]                    # its own record keeps an open book open
    gate.open.add(1)
    assert gate.step(1, 12 * S, 100.0, 2.0, 30.0, stopped=False) is True
    assert gate.snapshot()["pool_open"] == 0 and gate.snapshot()["pool_opens"] == 1
    empty = tg.TargetGate()
    assert empty.update_pool(30.0) is False


def test_a_clock_that_goes_back_a_simulation_starts_every_record_over_on_its_own():
    import research_v6211_score_logic as sl
    assert tg.REBASE_MIN_JUMP_NS == sl.REBASE_MIN_JUMP_NS
    gate = tg.TargetGate()
    gate.maybe_prune(80_000 * S)
    gate.step(1, 80_000 * S, 100.0, 2.0, 30.0, stopped=False)
    gate.maybe_prune(80_000 * S - 36 * S)                           # a checkpoint rewind inside the simulation
    assert gate.books and gate.rebases == 0
    gate.maybe_prune(600 * S)                                       # a new simulation
    assert gate.books == {} and gate.rebases == 1 and gate.last_prune_ts == 600 * S


def test_the_paper_record_drops_buckets_that_left_the_window_on_the_cadence():
    gate = tg.TargetGate(lookback_ns=1_800 * S)
    gate.step(1, 100 * S, 100.0, 2.0, 30.0, stopped=False)
    gate.books[1].buckets[0] = [50.0, 0.0, 1.0, 0.0]                  # key 0: leaves once now - 1,800 s > 0
    gate.maybe_prune(1_000 * S)
    assert 0 in gate.books[1].buckets
    gate.maybe_prune(1_900 * S)
    assert 0 not in gate.books[1].buckets and gate.books[1].alpha() == 0.0


# ---- 2. the pass ---------------------------------------------------------------------------------------------------

def _gated(**kw):
    agent = t63._pass_agent(**kw)
    agent.research_v632_target_gate = True
    agent._v632_gate, agent._v632_counts, agent._v632_errors = None, {}, 0
    return agent


def _open(agent, book_id, alpha=100.0):
    """A paper record that opens (and keeps open) the book's target."""
    gate = agent._v632_gate_ref()
    paper = gate.books.setdefault(book_id, tg.PaperBook())
    paper.buckets[tg.sampled_key(t63.NOW)] = [float(alpha), 0.0, 1.0, 0.0]


def test_a_closed_gate_keeps_a_rising_book_on_both_making_sides_and_records_the_rule():
    agent = _gated(); t63._rising(3, agent); resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert t63._placed(resp) == {(3, "BUY", 65031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 1.0)}
    assert agent._v63_last["gated"] == 1 and agent._v63_last["long"] == 0 and agent._v63_last["flat"] == 1
    assert agent._v632_gate.books[3].last_rule == 2.0                  # the paper record runs on the rule's target
    assert agent._v632_gate.open == set()


def test_an_open_gate_lets_the_target_trade():
    agent = _gated(); t63._rising(3, agent); _open(agent, 3); resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert t63._placed(resp) == {(3, "BUY", 60031, 100.01, 1.0)}      # toward the target, as v6.3
    assert agent._v63_last["long"] == 1 and agent._v63_last["gated"] == 0 and 3 in agent._v632_gate.open


def test_a_stopped_book_keeps_its_making_layer_under_the_gate():
    # short one clip below the zero target: v6.3 would cancel the resting making bid as paused
    agent = _gated(venue={3: -1.0}, alphas={3: -20.0}, floor=30.0); t63._rising(3, agent); _open(agent, 3)
    t63.t14._rest(agent, 11, 3, 0, 100.01, cid=65031)
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert 3 in agent._v63_paused and 3 not in agent._v632_gate.open   # the realized stop closes the gate
    assert t63._cancelled(resp) == set() and t63._placed(resp) == set() # the making bid rests on
    assert not agent._v63_counts.get("cancel_book_paused")
    # flat and stopped: both making sides go out
    agent = _gated(alphas={3: -20.0}, floor=30.0); t63._rising(3, agent); resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert 3 in agent._v63_paused
    assert t63._placed(resp) == {(3, "BUY", 65031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 1.0)}


def test_without_the_switch_the_v63_stop_still_idles_the_making_layer():
    agent = t63._pass_agent(venue={3: -1.0}, alphas={3: -10.0}); t63._rising(3, agent)
    t63.t14._rest(agent, 11, 3, 0, 100.01, cid=65031); resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert (3, 11) in t63._cancelled(resp) and agent._v63_counts.get("cancel_book_paused") == 1
    assert getattr(agent, "_v632_gate", None) is None


def test_a_new_simulation_id_clears_the_gates_even_when_the_clock_moves_forward():
    agent = _gated(); t63._rising(3, agent); _open(agent, 3)
    import test_research_v6_3_1_sim_reset as t631
    agent._v63_pass(t63._Resp(), t631._state({3: t63._book()}, t63.NOW, sim_id="20260918_2028"), {})
    assert agent._v632_gate.open == {3}
    agent._v63_pass(t63._Resp(), t631._state({3: t63._book()}, t63.NOW + S, sim_id="20260924_1653"), {})
    rows = [p for e, p in agent.emitted if e == "V631_SIM_RESET"]
    assert rows and rows[-1]["reason"] == "SIM_ID_CHANGE" and rows[-1]["gates_cleared"] == 1
    assert agent._v632_gate.open == set() and agent._v632_gate.rebases == 0      # the v6.3.1 reset, not the rewind


def test_a_new_simulation_clears_the_paper_records_and_the_gates():
    agent = _gated(); t63._rising(3, agent); _open(agent, 3)
    agent._v63_pass(t63._Resp(), t63._state({3: t63._book()}), {})
    assert agent._v632_gate.open == {3}
    import test_research_v6_3_1_sim_reset as t631
    agent._v63_pass(t63._Resp(), t631._state({3: t63._book()}, t631.NEW_SIM), {})
    rows = [p for e, p in agent.emitted if e == "V631_SIM_RESET"]
    assert rows and rows[0]["gates_cleared"] == 1
    assert agent._v632_gate.open == set() and set(agent._v632_gate.books) <= {3} and agent._v632_gate.opens == 0


# ---- 3. wiring -----------------------------------------------------------------------------------------------------

def test_the_switch_defaults_on_rides_on_v63_and_ships_with_the_validators_floor():
    assert 'self.research_v632_target_gate = self._as_bool(getattr(self.config, "research_v632_target_gate", True))' in SIMPLE
    assert 'return bool(self._v63_on() and getattr(self, "research_v632_target_gate", False))' in SIMPLE
    assert "research_v632_target_gate=1" in LAUNCHER and "[preflight] v6.3.2 target gate PASS" in LAUNCHER
    assert "research_v63_alpha_floor=30" in LAUNCHER and "research_v63_alpha_floor=18" not in LAUNCHER
    assert "tests/test_research_v6_3_2_target_gate.py" in LAUNCHER


def test_the_gate_steps_after_the_stop_and_before_the_wanted_quantities():
    body = _simple("_v63_pass")
    assert body.index("now_paused = v63_stop_state(alphas.get(book_id), floor, was)") \
        < body.index("opened = gate.step(book_id, now_ts, 0.5 * (raw_bid + raw_ask), rule_target, floor, stopped=is_paused)") \
        < body.index("want = v63_wanted(inv, target, clip=clip, making_width=0.0 if idle else width)")
    assert "if idle:\n                        doomed.append((row, V63_CANCEL_PAUSED))" in body
    assert "if not making_on or idle:" in body and "gate.maybe_prune(now_ts)" in body
    assert body.index("gate.update_pool(floor)") < body.index("for raw_id in sorted(books, key=lambda x: int(x)):")


def test_the_state_row_reports_the_gate():
    tele = _simple("_v62_telemetry")
    assert "target_gate_on=int(self._v632_gate_on())," in tele and "target_gate=self._v632_gate_snapshot()," in tele
