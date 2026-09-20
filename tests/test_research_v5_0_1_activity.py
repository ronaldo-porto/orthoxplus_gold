"""v5.0.1: the validator's activity factor, and the screen and rank that now respect it.

Under the validator's default flags a Kappa-eligible book is scored at its normalized Kappa-3 times
an activity factor that starts at 0.0 and becomes 1.0 only with a round trip once the uid's Kappa
gate is open (reward.py _aggregate_roundtrip_volumes / _apply_activity_factors).  Replaying UID 18's
RealNet log (20260915_063311) with that rule gives both activity means the dashboard showed (0.2656
over ticks 5,631-5,706 and 0.2969 over 5,866-6,136) and a Kappa-3 score of 0 at every checkpoint to
tick 8,489, where v5.0.0's mirror, assuming a factor of 1.0, reported 0.537.
"""
import ast
import math
import random
import re
import textwrap
import typing
from pathlib import Path
from types import SimpleNamespace

import research_direct_fastpath as fp
import research_v5_activity as act
import research_v5_score_mirror as sm
from research_candidate_screen import ScreenResult
from research_score_ev import ScoreEVBreakdown
from _harness import extractor

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
ACTIVITY = (STRATEGY / "research_v5_activity.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
NS = 1_000_000_000
BUCKET = 600 * NS
GATE = 5_400 * NS
LOOKBACK = 10_800 * NS
START = 30_000 * NS          # simulation time of the uid's first stored round in these tests


# ---- the validator, transcribed ----------------------------------------------------------------

def _validator_factors(round_trips, history_start, calls, *, book_count):
    """reward.py with activity impact 0 and decay_rate 0.

    round_trips: {book: [state_ts, ...]} of FIFO-closing fills.  trade.py keys their volume by the
    600 s sampled timestamp.  calculate_kappa_score returns before the factors while the uid's
    rounds span less than min_lookback (kappa_3 returns None).  Returns the factors after each call.
    """
    factors = {b: 0.0 for b in range(book_count)}
    out = []
    for call in calls:
        if call - history_start >= GATE:
            sampled = (call // BUCKET) * BUCKET
            for b in range(book_count):
                volumes = {}
                for ts in round_trips.get(b, ()):
                    if ts <= call:
                        key = (ts // BUCKET) * BUCKET
                        volumes[key] = volumes.get(key, 0.0) + 1.0
                latest_time = 0
                for ts, vol in volumes.items():
                    if vol > 0 and ts <= sampled and ts > latest_time:
                        latest_time = ts
                latest_volume = volumes[latest_time] if latest_time > 0 and latest_time >= sampled - BUCKET else 0.0
                if latest_volume > 0:
                    factors[b] = min(1 + 0.0, 2.0)
        out.append(dict(factors))
    return out


def _calls(first, last):
    first = -(-first // (5 * NS)) * (5 * NS)
    return list(range(first, last + 1, 5 * NS))


def _window(stamps, now):
    return tuple(ts for ts in stamps if now - LOOKBACK <= ts <= now)


def test_no_round_trip_counts_before_the_gate_and_the_bucket_before_the_gate_bucket_does():
    # Gate at 35,400 s, a bucket boundary: the first pass counts round trips from 34,800 s on.
    trips = {0: [START + 100 * NS, START + 3_000 * NS], 1: [34_850 * NS], 2: [34_700 * NS], 3: [35_410 * NS]}
    calls = _calls(START, 35_500 * NS)
    held = _validator_factors(trips, START, calls, book_count=4)
    gate_i = calls.index(35_400 * NS)
    assert all(v == 0.0 for f in held[:gate_i] for v in f.values())
    assert held[gate_i] == {0: 0.0, 1: 1.0, 2: 0.0, 3: 0.0}
    assert held[-1] == {0: 0.0, 1: 1.0, 2: 0.0, 3: 1.0}

    belief = act.ActivityBelief()
    belief.note_evidence(START, act.EVIDENCE_FIRST_STATE)
    assert belief.floor_ts == 34_800 * NS and belief.gate_ts == 35_405 * NS
    assert act.activation_floor_ts(35_400 * NS) == 34_800 * NS
    now = 35_500 * NS
    assert belief.factors({b: _window(t, now) for b, t in trips.items()}, book_count=4, now=now) == held[-1]
    assert belief.factors(trips, book_count=4, now=35_400 * NS) == {b: 0.0 for b in range(4)}


def test_the_belief_matches_the_validator_on_every_book_with_a_kappa():
    rng = random.Random(501)
    for trial in range(24):
        books = 6
        start = START + rng.randint(0, 599) * NS
        horizon = start + rng.randint(6_000, 14_000) * NS
        trips = {b: sorted(start + rng.randint(1, (horizon - start) // NS) * NS
                           for _ in range(rng.randint(0, 9))) for b in range(books)}
        calls = _calls(start, horizon)
        held = _validator_factors(trips, start, calls, book_count=books)
        belief = act.ActivityBelief()
        belief.note_evidence(start, act.EVIDENCE_FIRST_STATE)
        for i in range(0, len(calls), 37):
            now = calls[i]
            rolling = {b: _window(t, now) for b, t in trips.items()}
            mine = belief.factors(rolling, book_count=books, now=now)
            for b in range(books):
                assert mine[b] <= held[i][b], (trial, now, b)
                if len(rolling[b]) >= 3 and now >= belief.gate_ts:
                    assert mine[b] == held[i][b], (trial, now, b)


def test_a_later_start_estimate_only_ever_calls_fewer_books_activated():
    rng = random.Random(7)
    for trial in range(16):
        books = 5
        start = START
        horizon = start + 9_000 * NS
        trips = {b: sorted(start + rng.randint(1, 9_000) * NS for _ in range(rng.randint(0, 8)))
                 for b in range(books)}
        calls = _calls(start, horizon)
        held = _validator_factors(trips, start, calls, book_count=books)
        late = act.ActivityBelief()
        late.note_evidence(start + rng.randint(1, 2_400) * NS, act.EVIDENCE_FIRST_STATE)
        for i in range(0, len(calls), 29):
            now = calls[i]
            mine = late.factors({b: _window(t, now) for b, t in trips.items()}, book_count=books, now=now)
            assert all(mine[b] <= held[i][b] for b in range(books)), (trial, now)


def test_the_earliest_evidence_wins_and_a_new_simulation_moves_it():
    belief = act.ActivityBelief()
    assert belief.gate_ts is None and not belief.window_open(10**15) and not belief.gate_open(10**15)
    assert belief.note_evidence(40_000 * NS, act.EVIDENCE_FIRST_STATE)
    assert belief.note_evidence(31_000 * NS, act.EVIDENCE_OBSERVATION)
    assert not belief.note_evidence(35_000 * NS, act.EVIDENCE_FIRST_STATE)
    assert not belief.note_evidence("x", act.EVIDENCE_FIRST_STATE)
    assert belief.history_start_ts == 31_000 * NS and belief.history_start_source == act.EVIDENCE_OBSERVATION
    belief.rebase(-30_000 * NS)
    assert belief.history_start_ts == 1_000 * NS and belief.rebases == 1
    assert belief.gate_ts == (1_000 + 5_400 + 5) * NS


def test_each_book_is_named_by_what_the_validator_does_with_it():
    belief = act.ActivityBelief()
    belief.note_evidence(START, act.EVIDENCE_FIRST_STATE)
    floor = belief.floor_ts
    rolling = {
        1: (START + 10 * NS, START + 20 * NS, START + 30 * NS),     # eligible, all before the floor
        2: (START + 10 * NS, START + 20 * NS, floor + NS),          # eligible, activated
        3: (floor + 5 * NS,),                                       # one observation
    }
    closed = act.activity_view(belief, rolling, required=3, now=floor - NS)
    assert set(closed.states.values()) == {act.STATE_GATE_CLOSED} and closed.cold == frozenset()
    view = act.activity_view(belief, rolling, required=3, now=floor + 10 * NS)
    assert view.states == {1: act.STATE_COLD, 2: act.STATE_ACTIVATED, 3: act.STATE_INCOMPLETE}
    assert view.cold == frozenset({1}) and view.eligible == 2 and view.activated_eligible == 1
    assert view.window_open and not view.gate_open
    assert act.cliff_needed(56, 124) == 7 and act.cliff_needed(63, 124) == 0 and act.cliff_needed(62, 125) == 1


# ---- the mirror, with the factor -----------------------------------------------------------------

def _scored_history(books):
    # Kappa-3 does not depend on the size of equal round trips, only on how many there are, so
    # books with 3 to 6 observations give four different normalized values.
    rounds = [(START // NS + i) * NS for i in range(7_200)]
    history = {}
    for book in range(books):
        for k in range(3 + book % 4):
            history.setdefault(rounds[400 + 1_100 * k + book], {})[book] = 0.2
    return history, rounds


def _mirror(history, rounds, books, factors=None):
    return sm.mirror_score(history, rounds, now_ts=rounds[-1], book_count=books, miner_wealth=1_000.0,
                           params={"kappa_min_lookback_ns": 0}, marginal=False, activity_factors=factors)


def test_a_scored_book_the_validator_has_not_activated_counts_as_zero():
    history, rounds = _scored_history(20)
    plain = _mirror(history, rounds, 20)
    assert plain.scored_books == 20 and plain.activated_scored is None and "activity_weighted" not in plain.as_log()
    norms = sorted(plain.books[b]["norm"] for b in range(20))
    assert 0.5 < norms[0] < norms[-1] < 1.0 and len(set(round(n, 9) for n in norms)) == 4

    def score(activated):
        return _mirror(history, rounds, 20, {b: 1.0 if b < activated else 0.0 for b in range(20)})

    assert score(9).kappa_score == 0.0 and score(9).as_log()["cliff_needed"] == 2
    half = score(10)
    assert 0.0 < half.kappa_score < norms[-1] and half.as_log()["cliff_needed"] == 1
    past = score(11)
    assert past.kappa_score >= norms[0] - 1e-12 and past.kappa_penalty == 0.0
    assert past.as_log()["cliff_needed"] == 0
    # Once fewer than a quarter of the scored books are cold, the zeros are the outlier tail.
    assert score(14).kappa_penalty == 0.0 and score(17).kappa_penalty > 0.0
    full = score(20)
    assert full.kappa_penalty == plain.kappa_penalty == 0.0 and math.isclose(full.kappa_score, plain.kappa_score)
    row = score(12).as_log()
    assert row["activated_scored"] == 12 and row["cold_scored"] == 8
    assert math.isclose(row["kappa_score_all_active"], plain.kappa_score, abs_tol=1e-6)
    assert score(12).books[15]["activity"] == 0.0 and score(12).books[3]["activity"] == 1.0


# ---- Simple, executed from source ---------------------------------------------------------------

_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


def _constant(name):
    match = re.search(rf"^{name} = (.+)$", SIMPLE, re.M)
    assert match, name
    return ast.literal_eval(match.group(1))


NAMES = {
    "Any": typing.Any, "math": math, "ScoreEVBreakdown": ScoreEVBreakdown,
    "ActivationScoreEV": act.ActivationScoreEV, "ActivityBelief": act.ActivityBelief,
    "activity_view": act.activity_view, "cliff_needed": act.cliff_needed,
    "EVIDENCE_FIRST_STATE": act.EVIDENCE_FIRST_STATE, "EVIDENCE_OBSERVATION": act.EVIDENCE_OBSERVATION,
    "REBASE_MIN_JUMP_NS": act.REBASE_MIN_JUMP_NS, "V501_ACTIVITY_VERSION": act.V501_ACTIVITY_VERSION,
    "STATE_ACTIVATED": act.STATE_ACTIVATED, "STATE_COLD": act.STATE_COLD,
    "STATE_GATE_CLOSED": act.STATE_GATE_CLOSED, "STATE_INCOMPLETE": act.STATE_INCOMPLETE,
    "V501_ACTIVATION_VALUE": _constant("V501_ACTIVATION_VALUE"),
    "V501_STATE_EVERY_TICKS": _constant("V501_STATE_EVERY_TICKS"),
    "FastPathRow": fp.FastPathRow, "cheap_priority": fp.cheap_priority,
    "observable_maker_edge_bps": fp.observable_maker_edge_bps,
    "select_fastpath_rows": fp.select_fastpath_rows,
    "direct_fastpath_candidate_count": fp.clamp_candidate_count, "ScreenResult": ScreenResult,
    "DIRECT_FASTPATH_CANDIDATE_COUNT": fp.DIRECT_FASTPATH_CANDIDATE_COUNT,
    "DIRECT_TELEMETRY_SAMPLE_TICKS": fp.DIRECT_TELEMETRY_SAMPLE_TICKS,
    "DIRECT_FASTPATH_VERSION": fp.DIRECT_FASTPATH_VERSION,
}


class _Agent:
    """The base methods the v5.0.1 code reads, over a plain rolling observation table."""

    def __init__(self):
        self.emitted = []
        self._tick = 0
        self._research_last_sim_ts = None
        self._research_sim_start_ts = None
        self._research_kappa_roll_ts_cache = {}
        self._v501_belief = act.ActivityBelief()
        self._v501_tick = None
        self._v501_last_now = None
        self._v501_view = None
        self._v501_seen_activated = set()
        self._v501_cold_since = {}
        self._v501_pending_rows = []
        self._v501_window_reported = None
        self._v501_errors = 0

    def _emit(self, event_type, force=False, **payload):
        self.emitted.append((event_type, payload))

    def _research_refresh_rolling_kappa_cache(self):
        return None

    def _research_required_observation_count(self):
        return 3


def _harness(*names, base=_Agent, source=None, init=None):
    body = "".join(textwrap.indent(textwrap.dedent(source.get(n) if source and n in source else _method_source(n)),
                                   "    ") + "\n" for n in names)
    namespace = dict(NAMES, _Base=base)
    exec("from __future__ import annotations\nclass Harness(_Base):\n" + body, namespace)
    return namespace["Harness"](**(init or {}))


V501 = ("_v501_refresh", "_v501_cold_books", "_v501_activation_value", "_v501_service")


def _at(agent, tick, now):
    agent._tick = tick
    agent._research_last_sim_ts = now


def test_the_agent_finds_its_cold_books_and_logs_each_activation():
    agent = _harness(*V501)
    agent._research_sim_start_ts = START
    agent._research_kappa_roll_ts_cache = {
        4: (START + 50 * NS, START + 70 * NS, START + 90 * NS),
        5: (START + 60 * NS, START + 80 * NS),
    }
    floor = 34_800 * NS
    _at(agent, 100, floor - 10 * NS)
    assert agent._v501_cold_books() == frozenset()
    assert agent._v501_activation_value(4, 0) == (0.0, act.STATE_GATE_CLOSED)
    agent._v501_service(SimpleNamespace(config=SimpleNamespace(book_count=8), books={}))
    (kind, row), = agent.emitted
    assert kind == "V501_ACTIVITY_STATE" and row["window_open"] == 0 and row["cold_eligible"] == 0
    assert row["history_start_source"] == act.EVIDENCE_FIRST_STATE and row["s_to_gate"] == 615.0

    _at(agent, 101, floor)
    assert agent._v501_cold_books() == frozenset({4})
    assert agent._v501_activation_value(4, 0) == (_constant("V501_ACTIVATION_VALUE"), act.STATE_COLD)
    assert agent._v501_activation_value(5, 1) == (0.0, act.STATE_INCOMPLETE)
    assert agent._v501_activation_value(9, 3) == (0.0, act.STATE_INCOMPLETE)
    agent._v501_service(SimpleNamespace(config=SimpleNamespace(book_count=8), books={}))
    assert agent.emitted[-1][0] == "V501_ACTIVITY_STATE" and agent.emitted[-1][1]["window_open"] == 1
    assert agent.emitted[-1][1]["cold_books"] == [4] and agent.emitted[-1][1]["cliff_needed"] == 1

    agent._research_kappa_roll_ts_cache[4] = agent._research_kappa_roll_ts_cache[4] + (floor + 30 * NS,)
    agent._research_kappa_roll_ts_cache[5] = agent._research_kappa_roll_ts_cache[5] + (floor + 31 * NS,)
    _at(agent, 140, floor + 40 * NS)
    assert agent._v501_cold_books() == frozenset()
    agent._v501_service(SimpleNamespace(config=SimpleNamespace(book_count=8), books={}))
    rows = sorted((p["book"], p["was_cold"], p["cold_ticks"]) for k, p in agent.emitted if k == "V501_ACTIVATION")
    assert rows == [(4, 1, 39), (5, 0, None)]
    assert agent.emitted[-1][0] == "V501_ACTIVATION"      # no state row off the cadence
    _at(agent, 200, floor + 60 * NS)
    agent._v501_service(SimpleNamespace(config=SimpleNamespace(book_count=8), books={}))
    state = agent.emitted[-1][1]
    assert state["activated_eligible"] == 2 and state["activated_books_seen"] == 2 and state["activity_mean_seen"] == 0.25


def test_a_restarted_agent_takes_the_start_from_the_evidence_it_restored():
    agent = _harness(*V501)
    now = 39_000 * NS
    agent._research_sim_start_ts = now                        # this process began long after the gate
    agent._research_kappa_roll_ts_cache = {                   # restored from the session file
        1: (START + 10 * NS, START + 20 * NS, START + 30 * NS),
        2: (START + 15 * NS, START + 40 * NS, 36_000 * NS),
    }
    _at(agent, 1, now)
    assert agent._v501_cold_books() == frozenset({1})
    assert agent._v501_belief.history_start_source == act.EVIDENCE_OBSERVATION
    assert agent._v501_belief.history_start_ts == START + 10 * NS


def test_a_new_simulation_rebases_the_start_and_a_checkpoint_rewind_does_not():
    agent = _harness(*V501)
    agent._research_sim_start_ts = START
    _at(agent, 1, 40_000 * NS)
    agent._v501_refresh()
    _at(agent, 2, 39_990 * NS)                                # a 10 s checkpoint rewind
    agent._v501_refresh()
    assert agent._v501_belief.rebases == 0
    _at(agent, 3, 500 * NS)                                   # a new simulation clock
    agent._research_sim_start_ts = 500 * NS
    agent._v501_refresh()
    assert agent._v501_belief.rebases == 1
    assert agent._v501_belief.history_start_ts == START - 39_490 * NS


def test_the_switch_off_is_v5_0_0():
    agent = _harness(*V501)
    agent._v501_belief = None
    _at(agent, 100, 40_000 * NS)
    assert agent._v501_cold_books() == frozenset()
    assert agent._v501_activation_value(1, 0) == (0.0, None)
    agent._v501_service(SimpleNamespace(config=None, books={}))
    assert agent.emitted == []


class _ScoreAgent(_Agent):
    def __init__(self, obs, *, edge_spread_bps=12.0, maker_fee_bps=-2.0):
        super().__init__()
        self.obs = obs
        self.spread = edge_spread_bps
        self.fee = maker_fee_bps
        self._research_parked_dust = {}
        self._research_market_regime = "NORMAL"
        self._research_volume_cap_state = None

    def _research_profile_for_book(self, bid):
        return SimpleNamespace(spread_bps=self.spread)

    def _research_live_fee_bps(self, bid, is_maker=True):
        return self.fee if is_maker else 5.0

    def _completion_observation_count(self, bid):
        return self.obs.get(bid, 0)

    def _research_abs_inventory(self, bid):
        return 0.0

    def _execution_flat_epsilon(self):
        return 5e-05

    def _research_volume_cap_headroom(self, state, bid):
        return 1.0


def _score_agent(**kw):
    return _harness("_research_score_ev_for_book", *V501, base=_ScoreAgent, init=kw)


def test_the_rank_values_a_cold_book_as_it_values_a_one_away_book():
    agent = _score_agent(obs={1: 3, 2: 2, 3: 4})
    agent._research_sim_start_ts = START
    agent._research_kappa_roll_ts_cache = {1: (START + NS, START + 2 * NS, START + 3 * NS),
                                           2: (START + NS, START + 2 * NS),
                                           3: (START + NS, START + 2 * NS, 34_900 * NS, 34_950 * NS)}
    _at(agent, 50, 35_000 * NS)
    cold = agent._research_score_ev_for_book(1, 0.0, None)
    one_away = agent._research_score_ev_for_book(2, 0.0, None)
    warm = agent._research_score_ev_for_book(3, 0.0, None)
    edge = math.tanh((6.0 + 2.0) / 8.0)
    assert isinstance(cold, act.ActivationScoreEV) and cold.score_state == act.STATE_COLD
    assert cold.completion_value == 0.0 and cold.activity_deficit_value == one_away.completion_value == 0.20
    assert math.isclose(cold.total_score_component, 0.20) and math.isclose(cold.final_score, edge + 0.20)
    assert math.isclose(cold.final_score, one_away.final_score)
    assert warm.activity_deficit_value == 0.0 and math.isclose(warm.final_score, edge)
    assert warm.score_state == act.STATE_ACTIVATED and one_away.score_state == act.STATE_INCOMPLETE
    row = cold.as_log()
    assert row["activity_deficit_value"] == 0.20 and row["total_score_component"] == 0.20
    assert row["score_state"] == act.STATE_COLD and row["activation_value"] == 0.20


def test_a_cold_book_still_answers_to_the_hard_gates():
    agent = _score_agent(obs={1: 3}, edge_spread_bps=2.0, maker_fee_bps=4.0)
    agent._research_sim_start_ts = START
    agent._research_kappa_roll_ts_cache = {1: (START + NS, START + 2 * NS, START + 3 * NS)}
    _at(agent, 50, 35_000 * NS)
    ev = agent._research_score_ev_for_book(1, 0.0, None)
    assert ev.reject_reason == "NEGATIVE_CURRENT_EDGE" and not ev.eligible and ev.final_score == float("-inf")


def test_with_the_switch_off_the_rank_is_v5_0_0s():
    agent = _score_agent(obs={1: 3, 2: 1})
    agent._v501_belief = None
    for book, completion in ((1, 0.0), (2, 0.10)):
        ev = agent._research_score_ev_for_book(book, 0.0, None)
        assert type(ev) is ScoreEVBreakdown and ev.activity_deficit_value == 0.0
        assert ev.total_score_component == completion and "score_state" not in ev.as_log()


def test_the_one_away_completion_and_the_activation_value_are_one_number():
    score = _method_source("_research_score_ev_for_book")
    assert "if remaining == 1:\n            completion = 0.20" in score
    assert _constant("V501_ACTIVATION_VALUE") == 0.20


# ---- the fast screen --------------------------------------------------------------------------

class _ScreenAgent(_Agent):
    def __init__(self, eligible, cold):
        super().__init__()
        self.eligible = eligible
        self.cold = cold
        self._research_exchange_min_order_size = 0.25
        self._direct_fastpath_profile_cache = {}
        self._direct_fastpath_last_selected_tick = {}
        self._direct_edge_cooldown_until = {}
        self._research_inventory_lane_diag = {}
        self.research_candidate_count = 20
        self.research_score_target_books = 80

    def _execution_flat_epsilon(self):
        return 5e-05

    def _research_abs_inventory(self, bid):
        return 0.0

    def _research_kappa_book(self, bid):
        ok = bid in self.eligible
        return SimpleNamespace(observations_remaining=0 if ok else 2, eligible=ok)

    def _research_live_fee_bps(self, bid, is_maker=True):
        return -50.0

    def _v501_cold_books(self):
        return frozenset(self.cold)

    def _v621_cap_override(self, universe):
        # v6.2.1 off: the frozen A1.6.1 clamp bounds the screen, as this v5.0.1 contract assumes.
        return None


def _screen_state(books):
    level = SimpleNamespace(price=100.0, quantity=5.0)
    ask = SimpleNamespace(price=100.1, quantity=5.0)
    return SimpleNamespace(books={b: SimpleNamespace(bids=[level], asks=[ask]) for b in range(books)})


def test_the_screen_counts_a_cold_book_as_one_round_trip_away():
    eligible = set(range(100))
    cold = set(range(40, 100))
    state = _screen_state(128)
    agent = _harness("_research_fast_screen", base=_ScreenAgent, init={"eligible": eligible, "cold": cold})
    agent._tick = 7
    result = agent._research_fast_screen(state)
    picked = set(result.selected)
    # 40 score-qualified books leave a deficit of 40 against the 80-book target: most slots go to
    # books that one or two round trips make count.
    assert len(picked) == 20 and len(picked & (cold | set(range(100, 128)))) >= 15
    assert agent._direct_fastpath_priority_by_book[45] > agent._direct_fastpath_priority_by_book[5]

    off = _harness("_research_fast_screen", base=_ScreenAgent, init={"eligible": eligible, "cold": set()})
    off._tick = 7
    plain = off._research_fast_screen(state)
    source = _method_source("_research_fast_screen")
    v500 = source.replace(
        "        cold = self._v501_cold_books()\n", "").replace(
        "            if bid in cold:\n"
        "                # v5.0.1: eligible, but scored 0.0 until one more round trip activates it.\n"
        "                remaining, qualified = 1, False\n", "")
    assert "_v501_cold_books" not in v500 and "remaining, qualified = 1, False" not in v500
    ref = _harness("_research_fast_screen", base=_ScreenAgent, init={"eligible": eligible, "cold": set()},
                   source={"_research_fast_screen": v500})
    ref._tick = 7
    again = ref._research_fast_screen(state)
    assert (again.selected, again.forced, again.forced_kappa, again.screened_extra) == (
        plain.selected, plain.forced, plain.forced_kappa, plain.screened_extra)
    assert ref._direct_fastpath_priority_by_book == off._direct_fastpath_priority_by_book


# ---- wiring ------------------------------------------------------------------------------------

def test_v5_0_1_is_wired_and_launched():
    screen = _method_source("_research_fast_screen")
    assert screen.index("cold = self._v501_cold_books()") < screen.index("raw_rows = []")
    assert screen.index("remaining, qualified = 1, False") < screen.index("if qualified:\n                qualified_count += 1")
    score = _method_source("_research_score_ev_for_book")
    assert "activation_value, score_state = self._v501_activation_value(bid, remaining)" in score
    assert "total_score = completion + activation_value" in score
    respond = _method_source("respond")
    assert respond.index("self._v500_service(state)") < respond.index("self._v501_service(state)")
    assert "activity_factors=factors" in _method_source("_v500_emit_score")
    assert "self.research_v501_activity_alignment = self._as_bool(" in SIMPLE
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_6"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_6"' in SIMPLE
    assert act.V501_ACTIVITY_VERSION.endswith("v5_0_1")
    for key in ("direct_v501_activity_version", "direct_v501_activity_alignment", "direct_v501_window_open",
                "direct_v501_eligible_books", "direct_v501_activated_eligible", "direct_v501_cold_eligible",
                "direct_v501_cliff_needed", "direct_v501_errors", "direct_v500_kappa_score"):
        assert f'stats["{key}"]' in SIMPLE, key
    assert ("strategy1_direct_v5_0_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; "
            "A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    for switch in ("research_v501_activity_alignment=1", "research_v500_analytics=1", "research_a1992_idle_gc=1"):
        assert switch in params, switch
    assert "tests/test_research_v5_0_1_activity.py" in LAUNCHER
    assert "[preflight] v5.0.1 validator activity alignment PASS" in LAUNCHER
    for literal in ("remaining, qualified = 1, False",
                    "activation_value, score_state = self._v501_activation_value(bid, remaining)"):
        assert literal in SIMPLE and literal in LAUNCHER
    floor = "return (int(gate_ts) // int(sampling_ns)) * int(sampling_ns) - int(sampling_ns)"
    assert floor in ACTIVITY and floor in LAUNCHER
    weighting = "weighted[book] = weighted_kappa(norm, factor)"
    assert weighting in (STRATEGY / "research_v5_score_mirror.py").read_text() and weighting in LAUNCHER


def test_the_decision_inputs_read_neither_the_ledger_nor_the_mirror():
    tree = ast.parse(ACTIVITY)
    imported = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)}
    imported |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    assert "research_v5_analytics" not in imported and "research_v5_score_mirror" not in imported
    for name in ("_v501_refresh", "_v501_cold_books", "_v501_activation_value", "_research_fast_screen",
                 "_research_score_ev_for_book"):
        src = _method_source(name)
        assert "_v500_analytics" not in src and "mirror_score" not in src and "_v500_last_score" not in src, name
