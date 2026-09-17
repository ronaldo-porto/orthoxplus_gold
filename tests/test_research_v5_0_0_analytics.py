"""v5.0.0: round-trip analytics and a local copy of the validator's score -- telemetry only.

Replaying the A1.9.9.1 log (20260914_141324, ticks 1-8,000) through the ledger gives back its 810
round trips and its realized PnL exactly (79.34 closed + 0.26 open = 79.60).  The mirror, run on
the same history with every scoring round counted as the validator counts it, put every scored
book's normalized Kappa-3 between 0.499 and 0.52: coverage and the median book decide the kappa
part of the score, not total PnL.
"""
import ast
import math
import random
import textwrap
import time
import typing
from pathlib import Path
from types import SimpleNamespace

import research_v5_analytics as va
import research_v5_score_mirror as sm

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MIRROR = (STRATEGY / "research_v5_score_mirror.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
NS = 1_000_000_000


# ---- the score mirror ---------------------------------------------------------------------------

def _dense_kappa(row, kept, tau=0.0, min_obs=3):
    """kappa_3's per-book row with numpy's semantics written out."""
    n = len(row)
    ordered = sorted(row)
    med = ordered[n // 2] if n % 2 else 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])
    dev = sorted(abs(x - med) for x in row)
    mad = max(dev[n // 2] if n % 2 else 0.5 * (dev[n // 2 - 1] + dev[n // 2]), 1e-6)
    r = [x / mad for x, k in zip(row, kept) if k]
    if sum(1 for x in r if x != 0) < min_obs:
        return None
    mean = sum(r) / len(r)
    std = math.sqrt(sum((x - mean) ** 2 for x in r) / len(r))
    lpm3 = sum(max(tau - x, 0.0) ** 3 for x in r) / len(r)
    denom = lpm3 + (0.1 * (abs(mean) + std)) ** 3
    return (mean - tau) / math.copysign(abs(denom) ** (1 / 3), denom)


def test_the_per_book_kappa_matches_a_dense_transcription_of_kappa_3():
    rng = random.Random(7)
    for trial in range(300):
        n = rng.randint(5, 300)
        row = [0.0] * n
        count = rng.randint(0, n) if trial % 5 == 0 else rng.randint(0, min(n, 12))
        for i in rng.sample(range(n), count):
            row[i] = round(rng.uniform(-2.0, 1.5), 4) or 0.3
        kept = [True] * n
        if trial % 3 == 0:
            for i in rng.sample(range(1, n), min(2, n - 1)):
                kept[i] = False
        tau = 0.05 if trial % 4 == 0 else 0.0
        got = sm.kappa3_book([x for x in row if x != 0], n, [x for x, k in zip(row, kept) if k and x != 0],
                             sum(kept), tau=tau)
        want = _dense_kappa(row, kept, tau=tau)
        assert (got is None) == (want is None), trial
        if want is not None:
            assert math.isclose(got, want, rel_tol=1e-9, abs_tol=1e-12), trial


def test_numpy_order_statistics_are_reproduced():
    assert sm._percentile_sorted([1.0, 2.0, 3.0, 4.0], 25) == 1.75
    assert sm._percentile_sorted([1.0, 2.0, 3.0, 4.0], 75) == 3.25
    assert sm._median_with_zeros([-1.0, 5.0, 7.0], 2) == 0.0
    assert sm._median_with_zeros([3.0, 4.0], 2) == 1.5
    assert math.isnan(sm._median_with_zeros([], 0))


def _rounds(n, start=0):
    return [(start + i) * NS for i in range(n)]


def _kappa(history, rounds, **kw):
    params = dict(book_count=2, lookback_ns=0, min_lookback_ns=0, min_observations=3, tau=0.0, grace_period_ns=0)
    params.update(kw)
    return sm.kappa3_books(history, rounds, **params)


def test_every_round_the_validator_stores_dilutes_kappa_toward_the_middle():
    history = {10 * NS: {0: 0.3}, 20 * NS: {0: 0.2}, 30 * NS: {0: -0.4}, 40 * NS: {0: 0.25}}
    traded_only = _kappa(history, [])
    every_round = _kappa(history, _rounds(8000))
    assert traded_only.n_rounds == 4 and every_round.n_rounds == 8000
    assert abs(every_round.books[0]) < abs(traded_only.books[0]) / 10
    assert abs(sm.normalized_kappa(every_round.books[0], -2.5, 2.5) - 0.5) < 0.01
    assert every_round.books[1] is None and every_round.observations[0] == 4


def test_a_book_needs_three_observations_inside_the_window_of_the_newest_round():
    history = {1 * NS: {0: 0.1}, 50 * NS: {0: 0.2}, 60 * NS: {0: 0.3}, 70 * NS: {0: -0.1, 1: 0.2}}
    rounds = _rounds(100)
    assert _kappa(history, rounds).books[0] is not None
    windowed = _kappa(history, rounds, lookback_ns=60 * NS)   # newest round 99 s: keeps 39 s and later
    assert windowed.observations[0] == 3 and windowed.books[0] is not None and windowed.books[1] is None
    tighter = _kappa(history, rounds, lookback_ns=35 * NS)
    assert tighter.observations[0] == 1 and tighter.books[0] is None
    assert _kappa(history, rounds, min_lookback_ns=200 * NS) is None


def test_a_round_after_a_changeover_gap_is_dropped():
    history = {10 * NS: {0: 0.1}, 20 * NS: {0: 0.2}, 30 * NS: {0: 0.3}, 400 * NS: {0: 0.4}}
    rounds = _rounds(40) + [400 * NS, 401 * NS]
    assert _kappa(history, rounds).observations[0] == 4
    assert _kappa(history, rounds, grace_period_ns=300 * NS).observations[0] == 3


def test_pnl_below_the_volume_decimals_is_no_observation():
    history = {1 * NS: {0: 0.00004}, 2 * NS: {0: 0.2}, 3 * NS: {0: 0.3}, 4 * NS: {0: 0.1}}
    assert _kappa(history, _rounds(10)).observations[0] == 4
    assert _kappa(history, _rounds(10), volume_decimals=4).observations[0] == 3


def test_books_without_kappa_past_the_allowance_count_as_zero():
    # 8 books allow int(0.375 x 8) = 3 without a Kappa.
    def score(scored):
        weighted = {b: (0.6 if b < scored else None) for b in range(8)}
        return sm._kappa_aggregate(weighted, 3)
    assert score(5)[0] == 0.6 and score(5)[4] == []
    assert score(4)[0] == 0.6 and score(4)[4] == [7]
    assert score(2)[0] == 0.0 and score(2)[4] == [5, 6, 7]


def test_the_outlier_penalty_matches_reward_py():
    # q1 0.9, q3 0.95, iqr 0.05: 0.1 is an outlier; (0.5 - 0.1) / 1.5 x (1 - e^-0.25)
    assert math.isclose(sm.outlier_penalty([0.9, 0.92, 0.95, 0.97, 0.1]),
                        (0.4 / 1.5) * (1 - math.exp(-0.25)), rel_tol=1e-12)
    assert sm.outlier_penalty([0.5, 0.5, 0.5]) == 0.0


def test_the_weighting_is_asymmetric_as_in_reward_py():
    assert sm.weighted_kappa(0.4, 1.0) == 0.4 and sm.weighted_kappa(0.8, 1.5) == 1
    assert sm.weighted_kappa(0.4, 1.5) == (2 - 1.5) * 0.4 and sm.weighted_kappa(0.4, 0.5) == 0.2
    assert sm.weighted_kappa(None) is None


def test_the_pnl_score_is_the_median_daily_return_of_scored_books_halved():
    # wealth 1,000 over a 3 h window: the reference is 125.
    kw = dict(book_count=8, miner_wealth=1000.0, lookback_ns=10_800 * NS, max_inactive_ratio=0.375)
    value, returns = sm.pnl_score({0: 12.5, 1: -25.0, 2: 250.0, 3: 25.0, 4: 37.5}, **kw)
    assert returns == {0: 0.1, 1: -0.2, 2: 1.0, 3: 0.2, 4: 0.3}
    assert math.isclose(value, 0.2 / 2)
    value, _ = sm.pnl_score({0: 12.5, 1: -25.0, 2: 250.0}, **kw)   # 5 inactive: 2 count as zero
    assert value == 0.0
    assert sm.pnl_score({}, **kw) == (0.0, {})


def test_the_trading_score_weights_both_parts_and_names_each_book():
    rounds = _rounds(7200)
    history = {}
    for book in range(4):
        for k in range(4):
            history.setdefault((600 + 900 * k + book) * NS, {})[book] = 0.2 if book != 3 else -0.2
    mirror = sm.mirror_score(history, rounds, now_ts=rounds[-1], book_count=4, miner_wealth=1000.0,
                             params={"kappa_min_lookback_ns": 0})
    assert mirror.kappa_available and mirror.scored_books == 4 and mirror.zeroed_books == 0
    assert math.isclose(mirror.trading_score, max(0.0, min(1.0, 0.79 * mirror.kappa_score + 0.21 * mirror.pnl_score)))
    assert mirror.books[3]["norm"] < 0.5 < mirror.books[0]["norm"]
    assert mirror.books[0]["status"] == sm.STATUS_SCORED and mirror.books[0]["oldest_ts"] == 600 * NS
    # A book's marginal is the score with it minus the score without it -- which can be negative even
    # for a winning book, because removing any book also moves the quartiles the penalty uses.
    weighted = {b: sm.weighted_kappa(mirror.books[b]["norm"]) for b in range(4)}
    for book in range(4):
        without = dict(weighted)
        without[book] = None
        assert math.isclose(mirror.books[book]["marginal_kappa"],
                            mirror.kappa_score - sm._kappa_aggregate(without, 1)[0], abs_tol=1e-15)
    assert mirror.books[3]["marginal_kappa"] <= 0.0
    assert math.isclose(mirror.books[0]["pnl_window"], 0.8) and mirror.as_log()["scored_books"] == 4
    short = sm.mirror_score(history, rounds[:3000], now_ts=rounds[2999], book_count=4, miner_wealth=1000.0)
    assert not short.kappa_available and short.kappa_score == 0.0


# ---- the round-trip ledger ----------------------------------------------------------------------

def _fill(book, side, price, qty, before, after, *, maker=True, ts, tick=1, mid=None, fee=0.0):
    return {"book": book, "side": side, "fill_price": price, "filled_quantity": qty, "inventory_before": before,
            "inventory_after": after, "maker": maker, "taker": not maker, "fee": fee,
            "mid": price if mid is None else mid, "fill_timestamp": ts, "tick": tick}


def _position(book, delta, transition="FLAT"):
    return {"book_id": book, "realized_pnl_delta": delta, "transition": transition, "round_trip": transition == "FLAT"}


def _books(touches):
    return {book: SimpleNamespace(bids=[SimpleNamespace(price=bid)], asks=[SimpleNamespace(price=ask)])
            for book, (bid, ask) in touches.items()}


def test_a_maker_round_trip_is_one_row_with_capture_hold_and_capital_time():
    an = va.TradeAnalytics()
    an.observe("FILL", _fill(7, "buy", 99.9, 0.25, 0.0, 0.25, ts=10 * NS, mid=100.0, fee=-0.001))
    an.observe("ORDER_LIFECYCLE", {"book_id": 7, "phase": "SUBMITTED", "instruction": {"type": "PLACE_ORDER_LIMIT"}})
    an.observe("ORDER_LIFECYCLE", {"book_id": 7, "phase": "ORDERCANCELLATIONSEVENT",
                                   "instruction": {"type": "CANCEL_ORDERS"}})
    an.observe("ORDER_LIFECYCLE", {"book_id": 7, "phase": "SUBMITTED",
                                   "instruction": {"type": "CANCEL_ORDERS", "cancellations": [{"orderId": 1}]}})
    an.observe("A19_EXIT_REPRICE_CANCEL", {"book": 7})
    an.observe("FILL", _fill(7, "sell", 100.2, 0.25, 0.25, 0.0, ts=30 * NS, mid=100.1, fee=-0.001))
    an.observe("POSITION", _position(7, 0.077))
    ((kind, row),) = an.flush(tick=5, now_ts=30 * NS, books={})
    assert kind == "V500_RT" and row["exit_class"] == va.EXIT_MAKER and row["entry_style"] == va.EXIT_MAKER
    assert row["pnl"] == 0.077 and row["hold_s"] == 20.0 and row["fees"] == -0.002
    assert math.isclose(row["capital_s"], 0.25 * 99.9 * 20.0)
    assert math.isclose(row["entry_capture_bps"], 10.0, rel_tol=1e-9)
    assert math.isclose(row["exit_capture_bps"], (100.2 - 100.1) / 100.1 * 1e4, rel_tol=1e-3)
    assert (row["placements"], row["cancels"], row["reprices"]) == (1, 1, 1)
    assert an.pending == [] and an.open == {}


def test_a_taker_exit_carries_its_trigger_and_the_book_is_read_again_after_it():
    an = va.TradeAnalytics()
    an.observe("FILL", _fill(3, "buy", 100.0, 0.25, 0.0, 0.25, ts=NS))
    an.observe("FILL", _fill(3, "sell", 99.0, 0.25, 0.25, 0.0, maker=False, ts=5 * NS, mid=99.3))
    an.observe("A1961_TAKER_OUTCOME", {"book": 3, "trigger": "ABSOLUTE_PROTECTION_REDUCE",
                                       "decision_net_bps": -95.0, "realized_net_bps": -100.0, "slippage_bps": 5.0})
    an.observe("POSITION", _position(3, -0.26))
    ((kind, rt),) = an.flush(tick=10, now_ts=5 * NS, books=_books({3: (98.0, 98.2)}))
    assert rt["exit_class"] == va.EXIT_ABSOLUTE and rt["slippage_bps"] == 5.0 and rt["pnl"] == -0.26
    bids = {11: 98.0, 15: 99.5, 30: 97.0, 70: 96.0}
    rows = []
    for tick in range(11, 71):
        bid = bids.get(tick, 98.5)
        rows += an.flush(tick=tick, now_ts=(5 + tick) * NS, books=_books({3: (bid, bid + 0.2)}))
    ((kind, cf),) = rows
    assert kind == "V500_TAKER_COUNTERFACTUAL" and cf["exit_class"] == va.EXIT_ABSOLUTE and cf["side"] == "LONG"
    # Sold at 99.0: a later bid of 98.0 means the exit avoided 101 bps; 99.5 means it gave up 51.
    assert math.isclose(cf["avoided_bps_t1"], 101.01, abs_tol=0.01)
    assert math.isclose(cf["avoided_bps_t5"], -50.505, abs_tol=0.01)
    assert math.isclose(cf["avoided_bps_t20"], 202.02, abs_tol=0.01)
    assert math.isclose(cf["avoided_pnl_t60"], 0.75, rel_tol=1e-6)
    assert an.pending == []


def test_a_short_exit_reads_the_ask_and_a_missing_book_reads_nothing():
    an = va.TradeAnalytics()
    an.observe("FILL", _fill(4, "sell", 50.0, 0.25, 0.0, -0.25, ts=NS))
    an.observe("FILL", _fill(4, "buy", 50.5, 0.25, -0.25, 0.0, maker=False, ts=2 * NS))
    an.flush(tick=1, now_ts=2 * NS, books={})
    an.flush(tick=2, now_ts=3 * NS, books=_books({4: (50.9, 51.0)}))
    rows = []
    for tick in range(3, 62):
        rows += an.flush(tick=tick, now_ts=tick * NS, books={})
    ((_, cf),) = rows
    assert cf["side"] == "SHORT" and cf["exit_class"] == va.EXIT_TAKER_UNCLASSIFIED
    assert math.isclose(cf["avoided_bps_t1"], (51.0 - 50.5) / 50.5 * 1e4, abs_tol=0.001)
    assert cf["avoided_bps_t5"] is None and cf["avoided_pnl_t60"] is None


def test_scale_ins_partial_exits_and_a_flip():
    an = va.TradeAnalytics()
    an.observe("FILL", _fill(9, "buy", 10.0, 0.25, 0.0, 0.25, ts=NS))
    an.observe("FILL", _fill(9, "buy", 10.1, 0.25, 0.25, 0.5, ts=2 * NS))
    an.observe("FILL", _fill(9, "sell", 10.2, 0.25, 0.5, 0.25, maker=False, ts=3 * NS))
    an.observe("POSITION", _position(9, 0.05, "REDUCE"))
    an.observe("FILL", _fill(9, "sell", 10.3, 0.5, 0.25, -0.25, ts=4 * NS))
    an.observe("POSITION", _position(9, 0.06, "CROSS"))
    ((kind, rt),) = an.flush(tick=4, now_ts=4 * NS, books={})
    assert rt["entry_qty"] == 0.5 and rt["exit_class"] == va.EXIT_MAKER and rt["crossed"] == 1
    assert math.isclose(rt["pnl"], 0.11) and len(an.pending) == 1
    assert an.open[9].sign == -1.0 and an.open[9].abs_qty == 0.25 and an.open[9].open_ts == 4 * NS


def test_an_inherited_position_is_adopted_and_a_reseeded_one_is_unresolved():
    an = va.TradeAnalytics()
    an.observe("FILL", _fill(5, "sell", 20.0, 0.25, 0.25, 0.0, ts=NS))
    ((_, rt),) = an.flush(tick=1, now_ts=NS, books={})
    assert rt["entry_style"] == "INHERITED" and rt["adopted"] == 1 and an.adopted == 1
    an.observe("FILL", _fill(6, "buy", 20.0, 0.25, 0.0, 0.25, ts=2 * NS))
    an.observe("A199_EPOCH_REWIND", {"tick": 2})
    an.observe("FILL", _fill(6, "buy", 20.0, 0.25, -0.25, 0.0, ts=3 * NS))
    ((_, rt),) = an.flush(tick=3, now_ts=3 * NS, books={})
    assert an.unresolved == 1 and an.resets == 1 and rt["side"] == "SHORT" and rt["adopted"] == 1


def test_an_outcome_logged_before_its_closing_fill_still_classifies_the_exit():
    an = va.TradeAnalytics()
    an.observe("FILL", _fill(2, "buy", 30.0, 0.25, 0.0, 0.25, ts=NS))
    an.observe("POSITION", _position(2, -0.1))
    an.observe("A1961_TAKER_OUTCOME", {"book": 2, "trigger": "HARD_ESCAPE_CLIP", "slippage_bps": 1.0})
    an.observe("FILL", _fill(2, "sell", 29.9, 0.25, 0.25, 0.0, maker=False, ts=2 * NS))
    ((_, rt),) = an.flush(tick=2, now_ts=2 * NS, books={})
    assert rt["exit_class"] == va.EXIT_HARD_ESCAPE and rt["pnl"] == -0.1 and an.early_outcomes == {}


def test_markouts_count_the_one_second_horizon_only():
    an = va.TradeAnalytics()
    an.observe("MARKOUT", {"book": 1, "horizon_ms": 100, "markout_bps": -5.0, "status": "OK", "tick": 1})
    an.observe("MARKOUT", {"book": 1, "horizon_ms": 1000, "markout_bps": -5.0, "status": "OK", "tick": 1})
    an.observe("MARKOUT", {"book": 1, "horizon_ms": 1000, "markout_bps": 3.0, "status": "STALE", "tick": 1})
    assert list(an.markouts[1]) == [(1, -5.0)]


def test_the_rollup_splits_cubic_downside_by_class_and_measures_concentration():
    an = va.TradeAnalytics()
    specs = [(1, True, None, 0.3), (2, False, "ABSOLUTE_PROTECTION_REDUCE", -0.5), (3, False, "HARD_ESCAPE_CLIP", -0.2)]
    for i, (book, maker, trigger, pnl) in enumerate(specs):
        ts = (i + 1) * 10 * NS
        an.observe("FILL", _fill(book, "buy", 10.0, 0.25, 0.0, 0.25, ts=ts))
        an.observe("FILL", _fill(book, "sell", 10.0, 0.25, 0.25, 0.0, maker=maker, ts=ts + NS))
        if trigger:
            an.observe("A1961_TAKER_OUTCOME", {"book": book, "trigger": trigger, "slippage_bps": 2.0})
        an.observe("POSITION", _position(book, pnl))
        an.flush(tick=i + 1, now_ts=ts + NS, books={})
    an.observe("FILL", _fill(1, "sell", 10.0, 0.25, 0.0, -0.25, ts=50 * NS))
    an.observe("FILL", _fill(8, "buy", 10.0, 0.25, 0.0, 0.25, maker=False, ts=50 * NS))
    for cp in (901, 901, 902):
        an.note_trade(uid=67, maker_agent=67, taker_agent=cp, ts=50 * NS)
    rows = dict(an.rollup(now_ts=60 * NS))
    classes = rows["V500_TAKER_CLASSES"]["classes"]
    assert classes[va.EXIT_MAKER]["n"] == 1 and classes[va.EXIT_MAKER]["cubic_share"] == 0.0
    assert math.isclose(classes[va.EXIT_ABSOLUTE]["cubic_share"], round(0.125 / 0.133, 4))
    assert classes[va.EXIT_HARD_ESCAPE]["slippage_bps_mean"] == 2.0
    conc = rows["V500_CONCENTRATION"]
    assert conc["books_with_rts"] == 3 and conc["books_positive"] == 1 and conc["median_book_pnl"] == -0.2
    assert conc["top3_positive_pnl_share"] == 1.0 and conc["two_sided_maker_books"] == 1
    assert conc["open_books"] == 2 and conc["inventory_balance"] == 0.0
    assert conc["counterparties"] == 2 and conc["top_counterparty_share"] == round(2 / 3, 4)
    books = rows["V500_BOOK_PRODUCTIVITY"]["books"]
    assert [b["book"] for b in books] == [2, 3, 1] and books[-1]["maker_exit_share"] == 1.0
    assert rows == dict(an.rollup(now_ts=60 * NS))


def test_a_malformed_row_costs_telemetry_not_a_request():
    class Exploding(dict):
        def get(self, *args, **kwargs):
            raise RuntimeError("boom")
    an = va.TradeAnalytics()
    an.observe("FILL", Exploding())
    an.observe("FILL", {"book": "x"})
    an.observe("RANK", {"book": 1})
    assert an.observe_errors == 1 and an.open == {}


def test_everything_the_ledger_keeps_is_bounded():
    an = va.TradeAnalytics()
    for i in range(va.MAX_TRIPS_PER_BOOK + 10):
        ts = (i + 1) * NS
        an.observe("FILL", _fill(1, "buy", 10.0, 0.25, 0.0, 0.25, ts=ts))
        an.observe("FILL", _fill(1, "sell", 10.0, 0.25, 0.25, 0.0, maker=False, ts=ts))
        an.flush(tick=i, now_ts=ts, books={})
    assert len(an.trips[1]) == va.MAX_TRIPS_PER_BOOK and len(an.pending) <= va.MAX_PENDING_COUNTERFACTUALS
    for t in range(20_000):
        an.note_round(t * NS)
    assert len(an.rounds) <= 2 * (va.WINDOW_NS // NS + 64)


# ---- runtime: the Simple methods, executed from source ------------------------------------------

def _method_source(name):
    for node in ast.walk(ast.parse(SIMPLE)):
        if isinstance(node, ast.ClassDef) and node.name == "Strategy1_Research_Simple":
            defs = [ast.get_source_segment(SIMPLE, n) for n in node.body
                    if isinstance(n, ast.FunctionDef) and n.name == name]
            assert defs, name
            return defs[-1]
    raise AssertionError(name)


class _Parent:
    def __init__(self):
        self.emitted = []
        self._tick = 0
        self.realized_pnl_history = {}

    def _emit(self, event_type, force=False, **payload):
        self.emitted.append((event_type, force, payload))


def _harness(*names):
    # Compiled inside a class body so the methods' zero-argument super() resolves.
    body = "".join(textwrap.indent(textwrap.dedent(_method_source(name)), "    ") + "\n" for name in names)
    namespace = {
        "_Parent": _Parent, "Any": typing.Any, "time": time, "V500_TAPPED_ROWS": va.V500_TAPPED_ROWS,
        "V500_ANALYTICS_VERSION": va.V500_ANALYTICS_VERSION, "V500_SCORE_MIRROR_VERSION": sm.V500_SCORE_MIRROR_VERSION,
        "VALIDATOR_SCORING_DEFAULTS": sm.VALIDATOR_SCORING_DEFAULTS, "mirror_score": sm.mirror_score,
        "V500_SCORE_EVERY_TICKS": 100, "V500_ROLLUP_EVERY_TICKS": 500,
    }
    exec("from __future__ import annotations\nclass Harness(_Parent):\n" + body, namespace)
    return namespace["Harness"]()


def test_the_emit_tap_reads_rows_and_passes_every_row_on_unchanged():
    agent = _harness("_emit")
    agent._v500_analytics = va.TradeAnalytics()
    payload = _fill(7, "buy", 99.9, 0.25, 0.0, 0.25, ts=NS)
    agent._emit("FILL", force=True, **payload)
    agent._emit("RANK", book=7)
    assert agent.emitted == [("FILL", True, payload), ("RANK", False, {"book": 7})]
    assert 7 in agent._v500_analytics.open

    class Broken:
        def observe(self, *args):
            raise RuntimeError("boom")
    agent._v500_analytics = Broken()
    agent._emit("POSITION", force=True, book_id=7)
    assert agent.emitted[-1] == ("POSITION", True, {"book_id": 7})


def test_the_switch_off_restores_a1_9_9_2():
    agent = _harness("_emit", "_v500_service")
    agent._v500_analytics = None
    agent._emit("FILL", force=True, **_fill(7, "buy", 99.9, 0.25, 0.0, 0.25, ts=NS))
    agent._v500_service(SimpleNamespace(timestamp=NS, books={}, config=None))
    assert [kind for kind, _, _ in agent.emitted] == ["FILL"]


def test_the_service_logs_the_universe_round_trips_score_and_rollups():
    agent = _harness("_emit", "_v500_service", "_v500_note_universe", "_v500_emit_score")
    agent._v500_analytics = va.TradeAnalytics()
    config = SimpleNamespace(book_count=4, miner_wealth=1000.0, publish_interval=NS, grace_period=0, volumeDecimals=4)
    books = _books({b: (99.0, 101.0) for b in range(4)})
    for tick in range(1, 501):
        agent._tick = tick
        if tick % 25 == 0:
            book = (tick // 25) % 4
            agent._emit("FILL", force=True, **_fill(book, "buy", 99.0, 0.25, 0.0, 0.25, ts=tick * NS))
            agent._emit("FILL", force=True, **_fill(book, "sell", 101.0, 0.25, 0.25, 0.0, ts=tick * NS + 1))
            agent._emit("POSITION", force=True, **_position(book, 0.5))
            agent.realized_pnl_history.setdefault(tick * NS, {})[book] = 0.5
        agent._v500_service(SimpleNamespace(timestamp=tick * NS, books=books, config=config))
    kinds = [kind for kind, _, _ in agent.emitted if kind.startswith("V500_")]
    assert kinds.count("V500_UNIVERSE") == 1 and kinds.count("V500_RT") == 20
    assert kinds.count("V500_SCORE") == 5 and kinds.count("V500_CONCENTRATION") == 1
    universe = next(p for k, _, p in agent.emitted if k == "V500_UNIVERSE")
    assert universe["book_count"] == 4 and universe["missing_book_ids"] == [] and universe["min_scored_books"] == 3
    score = [p for k, _, p in agent.emitted if k == "V500_SCORE"][-1]
    assert score["tick"] == 500 and score["n_rounds"] == 500 and score["history_complete"] == 0
    assert score["mirror_ms"] >= 0.0 and "lowest_marginal" in score
    assert agent._v500_last_score == {k: v for k, v in score.items() if k != "tick"}


# ---- nothing the strategy decides can read it ---------------------------------------------------

def test_no_decision_path_reads_the_analytics():
    # v5.0.3 adds one more READER of the mirror, and it is an emitter: _v503_emit_book_kappa writes
    # the per-book Kappa table the offline comparison against the validator's own per-book gauges
    # reads.  The rule this test enforces is unchanged -- no DECISION path may read the analytics.
    allowed = {"_emit", "_v500_service", "_v500_note_universe", "_v500_emit_score", "onTrade", "respond",
               "_v503_emit_book_kappa"}
    for node in ast.walk(ast.parse(SIMPLE)):
        if isinstance(node, ast.ClassDef) and node.name == "Strategy1_Research_Simple":
            for fn in node.body:
                if not isinstance(fn, ast.FunctionDef):
                    continue
                src = ast.get_source_segment(SIMPLE, fn)
                if "_v500_" not in src:
                    continue
                if 'stats["direct_v500_analytics"]' in src or "self.research_v500_analytics = self._as_bool(" in src:
                    continue
                assert fn.name in allowed, fn.name
    importers = [p.name for p in STRATEGY.glob("*.py")
                 if ("research_v5_analytics" in p.read_text(errors="replace")
                     or "research_v5_score_mirror" in p.read_text(errors="replace"))]
    assert sorted(importers) == ["Strategy1_Research_Simple.py", "research_v5_analytics.py",
                                 "research_v5_score_mirror.py"] or sorted(importers) == ["Strategy1_Research_Simple.py"]


# ---- wiring ------------------------------------------------------------------------------------

def test_v5_0_0_is_wired_and_launched():
    respond = _method_source("respond")
    assert respond.index("self._a199_note_exit_stalls(response)") < respond.index("self._v500_service(state)")
    assert "analytics.note_trade(" in _method_source("onTrade")
    assert "analytics.observe(event_type, payload)" in _method_source("_emit")
    assert "self.research_v500_analytics = self._as_bool(" in SIMPLE
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_0_0"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_0_0"' in SIMPLE
    assert va.V500_ANALYTICS_VERSION.endswith("v5_0_0") and sm.V500_SCORE_MIRROR_VERSION.endswith("v5_0_0")
    for key in ("direct_v500_analytics_version", "direct_v500_score_mirror_version", "direct_v500_analytics",
                "direct_v500_rt_rows", "direct_v500_counterfactual_rows", "direct_v500_observe_errors",
                "direct_v500_service_errors", "direct_v500_trading_score", "direct_v500_scored_books"):
        assert f'stats["{key}"]' in SIMPLE, key
    assert ("strategy1_direct_v5_0_0) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; "
            "A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    for switch in ("research_v500_analytics=1", "research_a1992_idle_gc=1", "research_a1991_pending_owns_book=1"):
        assert switch in params, switch
    assert "tests/test_research_v5_0_0_analytics.py" in LAUNCHER
    assert "[preflight] v5.0.0 analytics / validator score mirror PASS" in LAUNCHER
    assert "    for ts in rounds:" in MIRROR and "    for ts in rounds:" in LAUNCHER
    assert "analytics.observe(event_type, payload)" in LAUNCHER and "self._v500_service(state)" in LAUNCHER
