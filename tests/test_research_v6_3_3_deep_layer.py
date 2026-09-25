"""v6.3.3: a gated deep layer -- both sides of a book rest where the book's own sweeps reach while that book's paper
record says those fills pay.

Mainnet, sim 20260924_1653: the clean-window skill leaders fill THROUGH the touch (74-88% of their fills, a median 11-50
ticks from the mid, markout +8..+68 ticks); sweeps reach >= 5 ticks past the touch ~300 times per book-hour.  Replayed
on this simulation with the validator's arithmetic, deep-only books at the p90 sweep depth score making 1,934 (v6.3.2:
339), alpha +1,988 and skill +2.9 on the published floor; on the previous, trending simulation the board stays shut.
"""
import random
import sys
import types
from pathlib import Path

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v633_deep_layer as dl  # noqa: E402
import research_v63_trend_target as tt  # noqa: E402
import research_v62_breadth as br  # noqa: E402
import research_v6214_touch_life as tl  # noqa: E402
import research_v6211_score_logic as sl  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
TICK = 0.01


def _t(p, s, ta, q=1.0, ma=5):
    return {"p": p, "q": q, "s": s, "Ma": ma, "Ta": ta, "y": "t"}


# ---- 1. the rules --------------------------------------------------------------------------------------------------

def test_the_deep_client_ids_are_their_own_family():
    assert dl.client_ids(3) == (40031, 40032) and dl.own_client_ids(3) == {40031, 40032}
    assert dl.is_deep_client_id(40031) and dl.is_deep_client_id(41272) and not dl.is_deep_client_id(40030)
    assert not dl.is_deep_client_id(60031) and not dl.is_deep_client_id(65032) and not dl.is_deep_client_id(None)
    for b in range(128):
        mine = dl.own_client_ids(b)
        assert not (mine & tt.own_client_ids(b)) and not (mine & set(br.entry_client_ids(b))) and not (mine & set(tl.exit_client_ids(b)))
        assert all(tt.role_of(c) is None for c in mine)


def test_a_trade_event_is_read_whatever_its_shape_and_non_trades_are_dropped():
    assert dl.trade_of(_t(100.0, 1, 300)) == {"p": 100.0, "q": 1.0, "s": 1, "Ma": 5, "Ta": 300}
    ns = types.SimpleNamespace(p=99.5, q=0.25, s=0, Ma=None, Ta=7, y="t")
    assert dl.trade_of(ns) == {"p": 99.5, "q": 0.25, "s": 0, "Ma": -1, "Ta": 7}
    assert dl.trade_of({"p": 1.0, "q": 1.0, "s": 0, "y": "o"}) is None and dl.trade_of(_t(100.0, 1, 3, q=0.0)) is None


def test_a_sweep_is_how_far_one_takers_prints_reach_past_the_previous_touch():
    trades = [_t(99.99, 1, 300), _t(99.90, 1, 300), _t(99.80, 1, 300),       # taker 300 sells 20 ticks through 100.00
              _t(100.05, 0, 7), _t(100.035, 0, 7),                          # taker 7 buys 2 ticks through 100.03
              _t(100.00, 1, 9)]                                             # taker 9 sold at the touch: no sweep
    got = sorted(round(d, 6) for d in dl.sweep_depths(trades, 100.00, 100.03, TICK))
    assert got == [2.0, 20.0]
    assert dl.sweep_depths(trades, None, 100.03, TICK) == []
    assert dl.quantile([5, 1, 9, 3, 7], 0.9) == 9 and dl.quantile([5, 1, 9, 3, 7], 0.5) == 5 and dl.quantile([], 0.9) is None


def test_the_deep_price_is_the_depth_from_the_mid_and_never_inside_the_touch():
    assert dl.deep_price(100.015, 21.5, dl.SIDE_BUY, bid=100.00, ask=100.03, tick=TICK, decimals=2) == 99.80
    assert dl.deep_price(100.015, 21.5, dl.SIDE_SELL, bid=100.00, ask=100.03, tick=TICK, decimals=2) == 100.23
    assert dl.deep_price(100.015, 0.2, dl.SIDE_BUY, bid=100.00, ask=100.03, tick=TICK, decimals=2) == 99.99
    assert dl.deep_price(100.015, 0.2, dl.SIDE_SELL, bid=100.00, ask=100.03, tick=TICK, decimals=2) == 100.04
    assert not dl.needs_reprice(99.80, 100.015, 21.5, TICK)
    assert dl.needs_reprice(99.95, 100.015, 21.5, TICK) and dl.needs_reprice(99.60, 100.015, 21.5, TICK)
    assert (dl.SWEEP_QUANTILE, dl.SWEEP_MIN, dl.DEEP_CLIPS, dl.DEEP_MAX_CLIPS) == (0.9, 20, 1.0, 2.0)


def test_a_paper_order_fills_only_through_its_price_and_scores_the_validator_arithmetic():
    p = dl.PaperDeep(); p.bid, p.ask = 99.90, 100.10
    for px, s in [(100.00, 1), (99.90, 1), (99.85, 1), (99.95, 0), (100.05, 0), (100.12, 0), (100.02, 1)]:
        p.on_print(0, px, s, 1.0, 2.0)
    # 99.90 exactly does not fill; 99.85 goes through the bid (long 1 at 99.90, marked -0.05); +0.10 +0.10 +0.07 on
    # the long; 100.12 goes through the ask (flat at 100.10, marked -0.02): mtm 0.20, inventory 3 over 7 prints, drift
    # 100.02 - 100.00 = +0.02
    assert p.fills == 2 and p.inv == 0.0 and p.bid is None and p.ask is None
    assert abs(p.alpha() - (0.20 - 3.0 / 7.0 * 0.02)) < 1e-9
    q = dl.PaperDeep(); q.bid = 99.90; q.inv = 2.0
    q.on_print(0, 99.50, 1, 1.0, 2.0)
    assert q.fills == 0 and q.inv == 2.0                             # no room past the inventory bound


def _deep_layer(books=(3,), depth=21.5, alpha=50.0, fills=1):
    layer = dl.DeepLayer()
    for b in books:
        db = dl.DeepBook()
        db.sweeps.extend([depth] * dl.SWEEP_MIN)
        db.paper.fills = fills
        db.paper.buckets[dl.sampled_key(t63.NOW)] = [float(alpha), 0.0, 1.0, 0.0]
        layer.books[b] = db
    return layer


def test_the_board_opens_on_a_positive_mean_paper_record_and_a_book_needs_depth_room_and_its_own_record():
    layer = _deep_layer(books=(1, 2), alpha=50.0)
    layer.books[2].paper.buckets[dl.sampled_key(t63.NOW)] = [-20.0, 0.0, 1.0, 0.0]
    assert layer.update_board() is True and abs(layer.board_alpha - 15.0) < 1e-9 and layer.board_opens == 1
    assert layer.book_open(1, 30.0, 0.0)
    assert not layer.book_open(2, 30.0, 0.0) and layer.book_open(2, 50.0, 0.0)     # -20 < -15, but not < -25
    assert not layer.book_open(1, 30.0, 2.5)                                      # past the inventory bound
    assert not layer.book_open(9, 30.0, 0.0)                                      # no record, no depth
    layer.books[1].paper.buckets[dl.sampled_key(t63.NOW)] = [-100.0, 0.0, 1.0, 0.0]
    assert layer.update_board() is False and not layer.book_open(1, 30.0, 0.0)
    fresh = dl.DeepLayer()
    assert fresh.update_board() is False and fresh.depth(3) is None
    assert layer.room(dl.SIDE_BUY, 1.5) and not layer.room(dl.SIDE_BUY, 2.0)
    assert layer.room(dl.SIDE_SELL, -1.0) and not layer.room(dl.SIDE_SELL, -2.0)


def test_a_closed_board_shuts_a_book_whose_own_record_is_fine():
    layer = _deep_layer(books=(1, 2), alpha=-10.0)                # own records -10 >= -15, the board's mean < 0
    assert layer.update_board() is False
    assert not layer.book_open(1, 30.0, 0.0) and not layer.book_open(2, 30.0, 0.0)


def test_a_book_whose_own_record_is_under_half_the_floor_stays_shut():
    layer = _deep_layer(books=(1, 2), alpha=100.0)
    layer.books[2].paper.buckets[dl.sampled_key(t63.NOW)] = [-16.0, 0.0, 1.0, 0.0]
    assert layer.update_board() is True
    assert layer.book_open(1, 30.0, 0.0) and not layer.book_open(2, 30.0, 0.0) and layer.book_open(2, 40.0, 0.0)


def test_observe_reads_sweeps_against_the_previous_touch_and_places_the_paper_orders_for_the_next_state():
    layer = dl.DeepLayer()
    for i in range(dl.SWEEP_MIN):
        layer.observe(3, t63.NOW + i * S, [_t(99.80, 1, 300)] if i else [], bid=100.00, ask=100.03, tick=TICK, decimals=2)
    assert len(layer.books[3].sweeps) == dl.SWEEP_MIN - 1 and layer.depth(3) is None
    d = layer.observe(3, t63.NOW + 30 * S, [_t(99.80, 1, 300)], bid=100.00, ask=100.03, tick=TICK, decimals=2)
    assert abs(d - 20.0) < 1e-6
    assert layer.books[3].paper.bid == dl.deep_price(100.015, d, dl.SIDE_BUY, bid=100.00, ask=100.03, tick=TICK, decimals=2)
    assert layer.books[3].paper.ask == dl.deep_price(100.015, d, dl.SIDE_SELL, bid=100.00, ask=100.03, tick=TICK, decimals=2)
    assert layer.books[3].paper.bid <= 100.00 - 0.18 and layer.books[3].paper.ask >= 100.03 + 0.18
    assert layer.books[3].prev_touch == (100.00, 100.03)


def test_a_sweep_is_measured_against_the_touch_before_it_not_the_touch_it_left():
    layer = dl.DeepLayer()
    layer.observe(3, t63.NOW, [], bid=100.00, ask=100.03, tick=TICK, decimals=2)
    # the sweep sold down to 99.80 and the book now quotes 99.70/99.73: 20 ticks through the touch it hit
    layer.observe(3, t63.NOW + S, [_t(99.80, 1, 300)], bid=99.70, ask=99.73, tick=TICK, decimals=2)
    assert [round(d, 6) for d in layer.books[3].sweeps] == [20.0]


def test_the_records_prune_on_the_validators_cadence_and_a_new_simulation_starts_them_over():
    layer = _deep_layer()
    layer.books[3].paper.buckets[0] = [5.0, 0.0, 1.0, 0.0]
    layer.maybe_prune(t63.NOW)
    assert 0 not in layer.books[3].paper.buckets and layer.last_prune_ts == t63.NOW
    layer.maybe_prune(t63.NOW + 30 * S)
    assert layer.last_prune_ts == t63.NOW
    layer.maybe_prune(t63.NOW - 36 * S)                          # a checkpoint rewind: kept
    assert layer.books and layer.rebases == 0
    layer.maybe_prune(600 * S)
    assert layer.books == {} and layer.rebases == 1 and layer.board_open is False
    assert set(layer.snapshot()) >= {"version", "books", "board_open", "board_alpha", "board_opens", "rebases",
                                     "books_with_depth", "depth_p50", "paper_alpha_sum", "paper_fills"}


# ---- 2. the pass ---------------------------------------------------------------------------------------------------

def _agent(layer=None, gate=True, **kw):
    agent = t63._pass_agent(**kw)
    agent.research_v632_target_gate = gate
    agent._v632_gate, agent._v632_counts, agent._v632_errors = None, {}, 0
    agent.research_v633_deep_layer = True
    agent._v633_deep, agent._v633_counts, agent._v633_errors = layer, {}, 0
    return agent


def _deep_placed(resp):
    return {p for p in t63._placed(resp) if dl.is_deep_client_id(p[2])}


def test_an_open_book_rests_deep_on_both_sides_and_cancels_its_v63_orders():
    agent = _agent(_deep_layer())
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    want_b = dl.deep_price(100.015, 21.5, dl.SIDE_BUY, bid=100.00, ask=100.03, tick=TICK, decimals=2)
    want_a = dl.deep_price(100.015, 21.5, dl.SIDE_SELL, bid=100.00, ask=100.03, tick=TICK, decimals=2)
    assert t63._placed(resp) == {(3, "BUY", 40031, want_b, 1.0), (3, "SELL", 40032, want_a, 1.0)}
    assert agent._v63_last["deep_books"] == 1 and agent._v63_last["placed_deep"] == 2
    # a v6.3 making bid resting on an open book is cancelled and its side waits for the removal
    agent = _agent(_deep_layer())
    t63.t14._rest(agent, 11, 3, 0, 100.01, cid=65031)
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert t63._cancelled(resp) == {(3, 11)} and agent._v633_counts.get("cancel_deep_owns_book") == 1
    assert t63._placed(resp) == {(3, "SELL", 40032, want_a, 1.0)}


def test_a_resting_deep_order_stays_in_its_band_and_goes_when_it_drifts_or_runs_out_of_room():
    agent = _agent(_deep_layer())
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031); t63.t14._rest(agent, 22, 3, 1, 100.23, cid=40032)
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert t63._cancelled(resp) == set() and t63._placed(resp) == set()
    agent = _agent(_deep_layer())
    t63.t14._rest(agent, 21, 3, 0, 99.95, cid=40031)                   # 6.5 ticks from the mid: under half the depth
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert (3, 21) in t63._cancelled(resp) and agent._v633_counts.get("cancel_deep_reprice") == 1
    agent = _agent(_deep_layer(), venue={3: 2.0})                      # long two clips: the bid has no room
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert (3, 21) in t63._cancelled(resp) and agent._v633_counts.get("cancel_deep_no_room") == 1
    assert {p[1] for p in _deep_placed(resp)} == {"SELL"}


def test_a_shut_book_cancels_its_deep_orders_and_runs_v632():
    layer = _deep_layer(alpha=-100.0)                                  # the board's record is negative: shut
    agent = _agent(layer)
    t63.t14._rest(agent, 21, 3, 0, 99.80, cid=40031)
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert (3, 21) in t63._cancelled(resp) and agent._v633_counts.get("cancel_deep_shut") == 1
    assert not _deep_placed(resp) and agent._v63_last["deep_books"] == 0
    assert (3, "SELL", 65032, 100.02, 1.0) in t63._placed(resp)       # v6.3.2's making ask; the bid side waits


def test_a_closed_board_keeps_even_a_sound_book_on_v632():
    agent = _agent(_deep_layer(alpha=-10.0))                        # its own record is fine, the board is shut
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert not _deep_placed(resp) and agent._v63_last["deep_books"] == 0
    assert t63._placed(resp) == {(3, "BUY", 65031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 1.0)}


def test_without_the_switch_nothing_deep_happens():
    agent = _agent(_deep_layer())
    agent.research_v633_deep_layer = False
    resp = t63._Resp()
    agent._v63_pass(resp, t63._state({3: t63._book()}), {})
    assert not _deep_placed(resp) and agent._v63_last["deep_books"] == 0
    assert t63._placed(resp) == {(3, "BUY", 65031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 1.0)}


def test_a_new_simulation_id_clears_the_deep_layer_even_when_the_clock_moves_forward():
    import test_research_v6_3_1_sim_reset as t631
    agent = _agent(_deep_layer())
    agent._v63_pass(t63._Resp(), t631._state({3: t63._book()}, t63.NOW, sim_id="20260918_2028"), {})
    agent._v63_pass(t63._Resp(), t631._state({3: t63._book()}, t63.NOW + S, sim_id="20260924_1653"), {})
    rows = [p for e, p in agent.emitted if e == "V631_SIM_RESET"]
    assert rows and rows[-1]["reason"] == "SIM_ID_CHANGE" and rows[-1]["deep_books_cleared"] == 1
    assert agent._v633_deep.rebases == 0 and agent._v633_deep.board_opens == 0      # the v6.3.1 reset, not the rewind
    assert all(len(db.sweeps) == 0 for db in agent._v633_deep.books.values())


def test_a_new_simulation_clears_the_deep_layer():
    agent = _agent(_deep_layer())
    agent._v63_pass(t63._Resp(), t63._state({3: t63._book()}), {})
    import test_research_v6_3_1_sim_reset as t631
    agent._v63_pass(t63._Resp(), t631._state({3: t63._book()}, t631.NEW_SIM), {})
    rows = [p for e, p in agent.emitted if e == "V631_SIM_RESET"]
    assert rows and rows[0]["deep_books_cleared"] == 1
    assert agent._v633_deep.board_open is False and all(len(db.sweeps) == 0 for db in agent._v633_deep.books.values())


# ---- 3. wiring -----------------------------------------------------------------------------------------------------

def test_the_switch_defaults_on_rides_on_v63_and_ships_in_params_and_the_preflight():
    assert 'self.research_v633_deep_layer = self._as_bool(getattr(self.config, "research_v633_deep_layer", True))' in SIMPLE
    assert 'return bool(self._v63_on() and getattr(self, "research_v633_deep_layer", False))' in SIMPLE
    assert "research_v633_deep_layer=1" in LAUNCHER and "[preflight] v6.3.3 deep layer PASS" in LAUNCHER
    assert "tests/test_research_v6_3_3_deep_layer.py" in LAUNCHER
    tele = _simple("_v62_telemetry")
    assert "deep_layer_on=int(self._v633_on())," in tele and "deep_layer=self._v633_snapshot()," in tele


def test_the_pass_reads_the_board_first_then_each_book_after_its_touch_and_before_the_v63_orders():
    body = _simple("_v63_pass")
    assert body.index("deep.update_board()") < body.index("for raw_id in sorted(books, key=lambda x: int(x)):")
    assert body.index("raw_bid, raw_ask = prices") < body.index("depth = deep.observe(book_id, now_ts, trades, bid=raw_bid, ask=raw_ask, tick=tick_size, decimals=dec)") \
        < body.index("hist = mids.get(book_id)")
    assert body.index("deep_open = bool(deep.book_open(book_id, floor, inv))") < body.index("own_cids = (") \
        < body.index("doomed.append((row, V633_CANCEL_SHUT))")
    assert "| v633_own_client_ids(book_id)" in body
