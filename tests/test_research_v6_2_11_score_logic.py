"""v6.2.11: the score logic for the validator's final rung.

The oracles are the validator's own functions, verbatim (tests/_upstream_debeta_7a3cad7.py and
tests/_upstream_reward_7a3cad7.py, taos-im/sn-79 main 7a3cad7 "0.6.1 final rung").  Live check before the
build (scratchpad v6211/validate_mirror.py): the own-alpha mirror replayed over UID 82's recorder to the
20:31 JST 2026-09-22 gauge snapshot matched the validator's per-book alphas at corr 0.9999 (UID 67),
0.9992 (68), 0.989 (82), and skill at the gauge floor -0.028 / +0.099 / +0.521 vs -0.027 / +0.096 / +0.546;
1.8 ms per state on a mainnet recording.
"""
import ast
import random
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6211_score_logic as sl  # noqa: E402
import research_v6212_pace_defer as pd  # noqa: E402
import research_v623_premium_floor as pf  # noqa: E402
import _upstream_debeta_7a3cad7 as up  # noqa: E402
import _upstream_reward_7a3cad7 as ur  # noqa: E402
from _harness import extractor  # noqa: E402
import test_research_v6_2_5_cap_paced as c5  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
MODULE = (STRATEGY / "research_v6211_score_logic.py").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
UID = 67
S = 1_000_000_000


# ---- 1. the vendored skill functions are the validator's -------------------------------------------

def _body(src, name):
    fn = [n for n in ast.parse(src).body if isinstance(n, ast.FunctionDef) and n.name == name][0]
    body = fn.body[1:] if (fn.body and isinstance(fn.body[0], ast.Expr)
                           and isinstance(getattr(fn.body[0], "value", None), ast.Constant)) else fn.body
    return ast.dump(ast.Module(body=body, type_ignores=[]))


@pytest.mark.parametrize("name", ["kappa_of_alpha", "kappa_floored", "_rank01"])
def test_vendored_functions_are_the_oracle_code(name):
    oracle = (Path(__file__).resolve().parent / "_upstream_debeta_7a3cad7.py").read_text()
    assert _body(MODULE, name) == _body(oracle, name)


@pytest.mark.parametrize("seed", range(20))
def test_skill_and_positive_ranks_equal_the_oracle(seed):
    rng = random.Random(seed)
    vals = [rng.choice([-1, 1]) * rng.expovariate(0.05) for _ in range(rng.randint(0, 40))]
    floor = rng.uniform(0.0, 40.0)
    assert sl.kappa_floored(vals, floor) == up.kappa_floored(vals, floor)
    legs = [rng.choice([0.0, -rng.random(), rng.random(), 0.5]) for _ in range(rng.randint(1, 12))]
    assert sl.rank_positives(legs) == up._rank_positive_leg(legs, "positives", "x")


def test_the_smallest_positive_ranks_zero_and_a_lone_positive_ranks_one():
    assert sl.rank_positives([0.1, 0.5, 0.9, -1.0, 0.0]) == [0.0, 0.5, 1.0, 0.0, 0.0]
    assert sl.rank_positives([-1.0, 0.3, 0.0]) == [0.0, 1.0, 0.0]


# ---- 2. the own-alpha mirror is the validator's arithmetic for one uid -----------------------------

def _states(rng, *, n_states=60, books=(1, 2, 3), late_book=3, start=1000 * S):
    """Random states: board prints, some ours (maker or taker), a few self-trades; on ``late_book`` our
    first trade comes late, so the window's print count dilutes our inventory time-sum."""
    out = []
    price = {b: 100.0 + 10 * b for b in books}
    for k in range(n_states):
        ts = start + k * S
        per = []
        for b in books:
            trades = []
            for _ in range(rng.randint(0, 6)):
                price[b] = max(1.0, price[b] + rng.choice([-0.02, -0.01, 0.0, 0.01, 0.02]))
                ma, ta = rng.randint(90, 95), rng.randint(90, 95)
                ours = rng.random() < 0.35 and not (b == late_book and k < n_states // 2)
                if ours:
                    if rng.random() < 0.5:
                        ma = UID
                    else:
                        ta = UID
                if rng.random() < 0.03:
                    ma = ta = UID
                trades.append({"p": round(price[b], 2), "q": rng.choice([0.25, 0.5, 1.0]),
                               "s": rng.randint(0, 1), "Ma": ma, "Ta": ta, "y": "t"})
            per.append((b, trades))
        out.append((ts, per))
    return out


def _oracle(states, *, lookback, key):
    """The validator's accumulators for every uid, pruned at ``ts - lookback``; alphas for UID's traded books."""
    from collections import defaultdict
    mtm, invsum, inv = defaultdict(dict), defaultdict(dict), defaultdict(dict)
    invn, pfirst, plast, drift = {}, {}, {}, {}
    mh, ih, nh, dh = {}, {}, {}, {}
    fills = []
    for ts, per in states:
        for b, trades in per:
            if trades:
                up.accumulate_book_mtm(mtm, invsum, invn, inv, pfirst, plast, b, trades,
                                       mtm_hist=mh, invsum_hist=ih, invn_hist=nh, drift=drift,
                                       drift_hist=dh, ts=key(ts))
                fills += [(key(ts), b) for t in trades if (t["Ma"] == UID) != (t["Ta"] == UID)]
        thr = ts - lookback
        up.prune_hist_2level(mh, mtm, thr)
        up.prune_hist_2level(ih, invsum, thr)
        up.prune_hist_1level(nh, invn, thr)
        up.prune_hist_1level(dh, drift, thr)
    traded = {b for k, b in fills if k >= states[-1][0] - lookback}
    alphas = up.book_alphas_by_book(mtm, invsum, invn, drift).get(UID, {})
    return {b: a for b, a in alphas.items() if b in traded}


@pytest.mark.parametrize("seed", range(12))
def test_mirror_equals_the_validator_per_timestamp(seed):
    rng = random.Random(seed)
    states = _states(rng)
    lookback = 25 * S
    m = sl.OwnAlphaMirror(UID, lookback_ns=lookback, bucket_ns=1, prune_every_ns=0)
    for ts, per in states:
        m.ingest_state(ts, per)
    got = m.book_alphas()
    want = _oracle(states, lookback=lookback, key=lambda ts: ts)
    assert set(got) == set(want)
    for b in want:
        assert got[b] == pytest.approx(want[b], rel=1e-9, abs=1e-9)


@pytest.mark.parametrize("seed", range(6))
def test_bucketing_only_changes_the_history_key(seed):
    rng = random.Random(100 + seed)
    states = _states(rng, n_states=90)
    lookback, bucket = 40 * S, 7 * S
    m = sl.OwnAlphaMirror(UID, lookback_ns=lookback, bucket_ns=bucket, prune_every_ns=0)
    for ts, per in states:
        m.ingest_state(ts, per)
    want = _oracle(states, lookback=lookback, key=lambda ts: ts - ts % bucket)
    got = m.book_alphas()
    assert set(got) == set(want)
    for b in want:
        assert got[b] == pytest.approx(want[b], rel=1e-9, abs=1e-9)


def test_a_late_first_trade_is_diluted_by_the_whole_window_of_prints():
    # Book 5: 10 board prints, then we buy 1 and hold while the price rises 10 prints by 0.1.
    board = [(1000 * S + k * S, [(5, [{"p": 100.0, "q": 1.0, "s": 0, "Ma": 90, "Ta": 91}])]) for k in range(10)]
    ours = [(1010 * S, [(5, [{"p": 100.0, "q": 1.0, "s": 1, "Ma": UID, "Ta": 91}])])]
    rise = [(1011 * S + k * S, [(5, [{"p": 100.0 + 0.1 * (k + 1), "q": 1.0, "s": 0, "Ma": 90, "Ta": 91}])])
            for k in range(10)]
    m = sl.OwnAlphaMirror(UID, lookback_ns=10_000 * S, bucket_ns=1, prune_every_ns=0)
    for ts, per in board + ours + rise:
        m.ingest_state(ts, per)
    # mtm = 1 x 1.0; drift over the window = 1.0; mean inventory = 11 held prints / 21 prints
    assert m.book_alphas()[5] == pytest.approx(1.0 - (11 / 21) * 1.0)


def test_only_books_filled_inside_the_window_count_and_old_buckets_leave():
    m = sl.OwnAlphaMirror(UID, lookback_ns=10 * S, bucket_ns=1, prune_every_ns=0)
    m.ingest_state(1000 * S, [(1, [{"p": 100.0, "q": 1.0, "s": 1, "Ma": UID, "Ta": 91}])])
    assert set(m.book_alphas()) == {1}
    for k in range(1, 15):
        m.ingest_state(1000 * S + k * S, [(1, [{"p": 100.0 + 0.01 * k, "q": 1.0, "s": 0, "Ma": 90, "Ta": 91}])])
    assert m.book_alphas() == {}                  # our fill left the window
    assert 1 in m.inv and m.inv[1] == 1.0         # the position is still held (binv never forgets)


def test_self_trades_are_not_fills_and_net_to_zero():
    m = sl.OwnAlphaMirror(UID, bucket_ns=1, prune_every_ns=0)
    m.ingest_state(1000 * S, [(1, [{"p": 100.0, "q": 1.0, "s": 1, "Ma": UID, "Ta": UID}])])
    assert m.own_fills == 0 and m.book_alphas() == {} and m.inv.get(1) == 0.0


def test_a_new_simulation_resets_the_mirror_and_coverage_counts_the_window():
    m = sl.OwnAlphaMirror(UID, lookback_ns=100 * S, bucket_ns=1, prune_every_ns=0)
    m.ingest_state(100_000 * S, [(1, [{"p": 100.0, "q": 1.0, "s": 1, "Ma": UID, "Ta": 91}])])
    m.ingest_state(100_050 * S, [])
    assert m.coverage() == pytest.approx(0.5)
    m.ingest_state(10 * S, [])                    # the clock went back more than an hour
    assert m.rebases == 1 and m.states == 1 and m.inv == {}


def test_models_and_dicts_are_read_the_same_and_non_trades_are_skipped():
    class Ev:
        def __init__(self, **kw):
            self.__dict__.update(kw)
    a, b = sl.OwnAlphaMirror(UID, bucket_ns=1), sl.OwnAlphaMirror(UID, bucket_ns=1)
    rows = [{"p": 100.0, "q": 1.0, "s": 1, "Ma": UID, "Ta": 91, "y": "t"}, {"y": "o", "p": 1.0}]
    a.ingest_state(1000 * S, [(1, rows)])
    b.ingest_state(1000 * S, [(1, [Ev(**rows[0]), Ev(y="o", p=1.0)])])
    assert a.inv == b.inv == {1: 1.0} and a.prints == b.prints == 1


def test_snapshot_shape():
    m = sl.OwnAlphaMirror(UID, bucket_ns=1, prune_every_ns=0)
    for ts, per in _states(random.Random(7)):
        m.ingest_state(ts, per)
    snap = m.snapshot()
    for key in ("version", "states", "prints", "own_fills", "coverage", "books", "alpha_sum", "worst",
                "skill_own_floor", "abs_median", "last_ms", "prune_ms"):
        assert key in snap, key
    assert snap["version"] == sl.V6211_SCORE_LOGIC_VERSION == "score_logic_v6_2_11"


# ---- 3. the reward pipeline helpers ------------------------------------------------------------------

def test_percentile_is_numpy_linear():
    assert sl.percentile_linear([1, 2, 3, 4], 50) == 2.5
    assert sl.percentile_linear([1, 2, 3, 4], 25) == 1.75
    assert sl.percentile_linear([4, 1, 3, 2], 100) == 4
    assert sl.percentile_linear([7.0], 50) == 7.0


def test_soft_floor_matches_the_worked_example():
    field = [0.40, 0.60, 0.80]                       # median of positives 0.60 -> taper from 0.30
    assert sl.soft_floor_factor(0.20, field) == 0.0
    assert sl.soft_floor_factor(0.45, field) == pytest.approx(0.5)
    assert sl.soft_floor_factor(0.60, field) == 1.0
    assert sl.soft_floor_factor(0.70, field) == 1.0
    assert sl.soft_floor_factor(0.5, [0.5]) == 1.0   # fewer than two active scores: no floor


def test_trading_score_weights():
    assert sl.trading_score(1.0, 0.5) == pytest.approx(0.75)
    assert sl.trading_score(1.0, 0.1667, w_make=0.3) == pytest.approx(0.3 + 0.7 * 0.1667)


@pytest.mark.parametrize("seed", range(10))
def test_standing_step_equals_the_validator_ema(seed):
    rng = random.Random(seed)
    ema, n, last = None, 0, None
    o_ema, o_n, o_last = {}, {}, None
    ts = 5_000 * S
    for k in range(400):
        cur = 0.0 if k < rng.randint(0, 30) else rng.random()
        ts = ts + 5 * S if rng.random() > 0.01 else ts - 3_600 * S      # a rare seam
        ema, n, last = sl.track_record_step(ema, n, cur, ts=ts, last_ts=last)
        out, o_last = ur.apply_track_record_ema({UID: cur}, [UID], [], ts, sl.SCORE_EMA_HALFLIFE_NS,
                                                o_ema, o_n, o_last)
        assert n == o_n.get(UID, 0)
        if n:
            assert ema == pytest.approx(o_ema[UID], rel=1e-12)


def test_a_newcomer_standing_is_the_plain_mean_until_the_time_term_dominates():
    ema, n, last = None, 0, None
    for k, cur in enumerate([0.0, 0.0, 0.2, 0.8, 0.5]):
        ema, n, last = sl.track_record_step(ema, n, cur, ts=(k + 1) * 5 * S, last_ts=last)
    assert n == 3 and ema == pytest.approx((0.2 + 0.8 + 0.5) / 3)


def test_held_book():
    assert sl.held_book(0.25, 5e-5) and sl.held_book(-4.0, 5e-5)
    assert not sl.held_book(0.0, 5e-5) and not sl.held_book(None, 5e-5)


# ---- 4. R1: every book is lifted off the Kappa-era floor ---------------------------------------------

class _Floor:
    def __init__(self, *, lift_all, status_window):
        self.research_v6211_lift_all = lift_all
        self._window = status_window
        self._v623_errors = 0

    def _v623_on(self):
        return True

    def _v623_census(self, state):
        return {7: self._window}

    def _v626_loss_budget_on(self):
        return False


def _lifted_agent(**kw):
    ns = {"v623_book_status": pf.book_status, "V623_MIN_OBSERVATIONS": pf.KAPPA_MIN_REALIZED_OBSERVATIONS,
          "V623_BOOK_PREMIUM": pf.BOOK_PREMIUM, "V623_BOOK_LOSS": pf.BOOK_LOSS,
          "V6211_LIFT_ALL_STATUS": sl.LIFT_ALL_STATUS}
    exec(compile(ast.Module(body=[ast.parse(_simple("_v623_lifted")).body[0]], type_ignores=[]), "<v6211>", "exec"), ns)
    cls = type("_V6211Floor", (_Floor,), {"_v623_lifted": ns["_v623_lifted"]})
    return cls(**kw)


def test_a_premium_book_keeps_its_floor_without_r1_and_is_lifted_with_it():
    premium = pf.BookWindow(observations=5, negatives=0, realized_sum=1.0)
    assert pf.book_status(premium) == pf.BOOK_PREMIUM
    assert _lifted_agent(lift_all=False, status_window=premium)._v623_lifted(7) == (False, pf.BOOK_PREMIUM)
    assert _lifted_agent(lift_all=True, status_window=premium)._v623_lifted(7) == (True, pf.BOOK_PREMIUM)


def test_r1_lifts_even_without_a_census():
    agent = _lifted_agent(lift_all=True, status_window=None)
    agent._v623_census = lambda state: None
    assert agent._v623_lifted(7) == (True, sl.LIFT_ALL_STATUS)


def test_a_loss_book_is_lifted_either_way():
    loss = pf.BookWindow(observations=5, negatives=1, realized_sum=-1.0)
    assert _lifted_agent(lift_all=False, status_window=loss)._v623_lifted(7)[0] is True


def test_every_floor_site_still_asks_the_one_classifier():
    # The five callers read _v623_lifted; R1 changes its answer, not the callers.
    assert SIMPLE.count("self._v623_lifted(int(book_id), state)") >= 5


# ---- 5. R2: a held book is not a pace sample -----------------------------------------------------------

def _clip_ns():
    ns = {
        "v625_pace_target_rate": c5.cp.pace_target_rate, "v625_observed_rate": c5.cp.observed_rate,
        "v625_paced_clip": c5.cp.paced_clip, "v625_clip_ceiling": c5.cp.clip_ceiling,
        "V625BookPace": c5.cp.BookPace, "V625_PACE_SAMPLE_NS": c5.cp.PACE_SAMPLE_NS,
        "v627_bounded_ceiling": c5.mc.bounded_ceiling, "v627_pace_rewound": c5.mc.pace_rewound,
        "v6211_held_book": sl.held_book,
        "v6212_sample_after_hold": pd.sample_after_hold,
    }
    return ns


def _bound_pacer(hold_pace=True):
    agent = c5._agent()
    # a per-agent subclass, so the shared v6.2.5 harness class is never modified
    agent.__class__ = type("_V6211Agent", (type(agent),), {"_execution_flat_epsilon": lambda self: 5e-5})
    agent.research_v6211_hold_pace = hold_pace
    agent._v6211_counts = {}
    ns = _clip_ns()
    for name in ("_v625_clip", "_v6211_count"):
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]), "<v6211>", "exec"), ns)
        setattr(type(agent), name, ns[name])
    return agent


def test_a_held_book_quotes_one_minimum_order_and_never_steps_its_clip():
    agent = _bound_pacer()
    agent._v625_clip(7, c5._State(0), c5.facts(), mid=300.0)             # opens the sample flat
    agent._traded = 1_000.0                                              # a quarter of the pace
    got = agent._v625_clip(7, c5._State(600 * S), c5.facts(net_base=2.0), mid=300.0)
    assert got == 0.25 and agent._v6211_counts.get("pace_held") == 1
    pace = agent._v625_pace[7]
    assert pace.clip == 0.25 and pace.sampled_ns == 600 * S and pace.volume == 1_000.0   # re-seeded
    assert "clip_up" not in agent._v625_counts


def test_held_time_is_not_counted_when_the_book_is_flat_again():
    agent = _bound_pacer()
    agent._v625_clip(7, c5._State(0), c5.facts(), mid=300.0)
    agent._traded = 100.0
    agent._v625_clip(7, c5._State(900 * S), c5.facts(net_base=-1.0), mid=300.0)   # held: re-seeded at 900
    # flat at 1,200: only 300 s since the re-seed, below one sample -- no step on the held stretch
    assert agent._v625_clip(7, c5._State(1_200 * S), c5.facts(), mid=300.0) == 0.25
    assert "clip_up" not in agent._v625_counts


def test_without_r2_the_same_held_book_doubles():
    agent = _bound_pacer(hold_pace=False)
    agent._v625_clip(7, c5._State(0), c5.facts(), mid=300.0)
    agent._traded = 1_000.0
    assert agent._v625_clip(7, c5._State(600 * S), c5.facts(net_base=2.0), mid=300.0) == 0.5


def test_r2_sits_after_the_rewind_and_before_the_observed_rate():
    src = _simple("_v625_clip")
    assert src.index('self._v627_count("pace_rewound")') < src.index("v6211_held_book(") < src.index("obs = v625_observed_rate(")
    # v6.2.12 routes the re-seed through sample_after_hold, which returns (now_ns, used) with its switch off
    assert "pace.sampled_ns, pace.volume = v6212_sample_after_hold(" in src[src.index("v6211_held_book("):]


# ---- 6. wiring --------------------------------------------------------------------------------------

def test_switches_default_on_and_are_declared():
    for key in ("research_v6211_lift_all", "research_v6211_hold_pace", "research_v6211_alpha_mirror"):
        assert f'getattr(self.config, "{key}", True)' in SIMPLE
        assert f"{key}=1" in LAUNCHER


def test_the_mirror_is_fed_every_state_from_update():
    src = _simple("update")
    assert src.index("self._v62_feed_mirror(state)") < src.index("self._v6211_feed_mirror(state)") < src.index("return super().update(state)")


def test_the_state_row_reports_the_build():
    for key in ("lift_all_on=", "hold_pace_on=", "alpha_mirror_on=", "score_logic=self._v6211_snapshot(),"):
        assert key in SIMPLE


def test_version_and_launcher_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE
    assert "strategy1_direct_v6_2_14)" in LAUNCHER and "V6210_BUILD=1; V6211_BUILD=1 ;;" in LAUNCHER
    assert "strategy1_direct_v6_2_10)" in LAUNCHER                # the previous arm stays
    assert 'echo "[preflight] v6.2.11 score logic PASS"' in LAUNCHER
    assert "tests/test_research_v6_2_11_score_logic.py" in LAUNCHER
