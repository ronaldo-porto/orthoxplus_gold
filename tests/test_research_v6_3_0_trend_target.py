"""v6.3: every book holds inventory in the direction of its own recent move, and a making layer rests around it.

Mainnet UID 94, 14,040 recorded ticks (09-23 14:47 to 09-24 13:30 JST): consecutive 60 s returns correlate +0.45 and
300 s returns +0.64 (gone by 20 min, 97% of books, every 3,000-tick window); a resting-order maker's inventory points
against the move (ours -0.36 with the past 300 s return, alpha negative on 128 of 128 books; the skill-positive 233
family +0.33).  Replayed with the validator's arithmetic, a fill model calibrated on 22,000 of our live orders and our
58 ms order delay, the target of +/-2 clips by the 120 s move with a fee-capped making layer one tick inside scores
skill +5.3 / +5.4 / +5.3 and making 55 / 87 / 140 over the three 3-h windows.
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

import research_v63_trend_target as tt  # noqa: E402
import research_v62_breadth as br  # noqa: E402
import research_v6214_touch_life as tl  # noqa: E402
import research_v6210_touch_improve as ti  # noqa: E402
import research_v6215_order_life as ol  # noqa: E402
import research_v631_sim_reset as sr  # noqa: E402
import research_v632_target_gate as tg632  # noqa: E402
from research_session_state import extract_simulation_id  # noqa: E402
from research_direct_exit_ledger import DirectExitLedger  # noqa: E402
from research_direct_exit_refresh import ABSENT_REPRICE_CANCEL  # noqa: E402
from _harness import extractor  # noqa: E402
import test_research_v6_2_14_touch_life as t14  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
NOW = t14.NOW
TICK = 0.01


# ---- 1. the rules -------------------------------------------------------------------------------------------------

def test_the_client_id_families_are_v63s_own_and_readable():
    assert tt.client_ids(3, tt.ROLE_TOWARD) == (60031, 60032) and tt.client_ids(3, tt.ROLE_MAKING) == (65031, 65032)
    assert tt.role_of(60032) == tt.ROLE_TOWARD and tt.role_of(65031) == tt.ROLE_MAKING
    assert tt.role_of(70031) is None and tt.role_of(80032) is None and tt.role_of(None) is None
    assert tt.own_client_ids(3) == {60031, 60032, 65031, 65032}
    assert not (tt.own_client_ids(3) & set(br.entry_client_ids(3))) and not (tt.own_client_ids(3) & set(tl.exit_client_ids(3)))


def test_the_signal_is_the_mids_move_over_the_lookback_and_needs_a_full_history():
    h = tt.new_history(3)
    for x in (1.0, 2.0, 2.5, 4.0):
        h.append(x)
    assert tt.signal_bps(h, 3) == 3.0 and tt.signal_bps(h, 4) is None
    assert tt.log_mid_bps(100.0, 100.02) is not None and tt.log_mid_bps(100.02, 100.0) is None
    assert tt.log_mid_bps(None, 100.0) is None


def test_the_target_is_plus_or_minus_the_clips_by_the_signs_sign():
    assert tt.target_base(3.0, target_abs=2.0) == 2.0 and tt.target_base(-0.1, target_abs=2.0) == -2.0
    assert tt.target_base(0.0, target_abs=2.0) == 0.0 and tt.target_base(None, target_abs=2.0) == 0.0
    assert tt.target_base(5.0, target_abs=2.0, dead_zone_bps=10.0) == 0.0


def test_wanted_moves_toward_the_target_and_makes_one_clip_around_it():
    assert tt.wanted(0.0, 2.0, clip=1.0, making_width=1.0) == {"buy": 1.0, "sell": 0.0}
    assert tt.wanted(2.0, 2.0, clip=1.0, making_width=1.0) == {"buy": 1.0, "sell": 1.0}
    assert tt.wanted(3.0, 2.0, clip=1.0, making_width=1.0) == {"buy": 0.0, "sell": 1.0}
    assert tt.wanted(2.0, -2.0, clip=1.0, making_width=1.0) == {"buy": 0.0, "sell": 1.0}
    assert tt.wanted(0.5, 0.0, clip=1.0, making_width=0.0) == {"buy": 0.0, "sell": 0.5}
    assert tt.role_for("buy", 1.5, 2.0) == tt.ROLE_TOWARD and tt.role_for("sell", 1.5, 2.0) == tt.ROLE_MAKING
    assert tt.role_for("buy", 2.0, 2.0) == tt.ROLE_MAKING and tt.role_for("sell", 2.5, 2.0) == tt.ROLE_TOWARD


def test_the_book_stop_pauses_below_half_the_floor_and_releases_above_a_quarter():
    assert tt.stop_state(-9.1, 18.0, False) and not tt.stop_state(-8.9, 18.0, False)
    assert tt.stop_state(-6.0, 18.0, True) and not tt.stop_state(-4.4, 18.0, True)
    assert tt.stop_state(None, 18.0, True) and not tt.stop_state(None, 18.0, False)
    assert not tt.stop_state(-100.0, 0.0, False)


def test_the_fee_cap_is_the_median_maker_fee_and_the_making_gates_read_the_book():
    assert tt.fee_cap_bps([10.0, 50.0, 30.0, None]) == 30.0 and tt.fee_cap_bps([]) is None
    assert tt.making_side_verdict("buy", own_depth=1.0, other_depth=2.0, last_taker=None) == tt.CANCEL_THIN
    assert tt.making_side_verdict("buy", own_depth=2.0, other_depth=2.0, last_taker=tl.TAKER_SELL) == tt.CANCEL_HIT
    assert tt.making_side_verdict("sell", own_depth=2.0, other_depth=2.0, last_taker=tl.TAKER_SELL) is None


def test_prices_one_tick_inside_only_where_the_v6210_rule_allows_and_the_touch_otherwise():
    view = ti.TouchView(100.00, 100.03, 100.00, 100.03, 0.0, 0.0)          # 3-tick spread: inside is legal
    assert tt.improve_price(view, "buy", TICK) == 100.01 and tt.improve_price(view, "sell", TICK) == 100.02
    narrow = ti.TouchView(100.00, 100.02, 100.00, 100.02, 0.0, 0.0)        # 2-tick spread: inside would be the mid
    assert tt.improve_price(narrow, "buy", TICK) is None and tt.improve_price(narrow, "sell", TICK) is None
    assert tt.toward_price(narrow, "buy", TICK, raw_bid=100.00, raw_ask=100.02) == 100.01   # the mid: a target order may
    one_tick = ti.TouchView(100.00, 100.01, 100.00, 100.01, 0.0, 0.0)
    assert tt.toward_price(one_tick, "buy", TICK, raw_bid=100.00, raw_ask=100.01) == 100.00
    assert tt.toward_price(one_tick, "sell", TICK, raw_bid=100.00, raw_ask=100.01) == 100.01
    assert tt.toward_price(view, "sell", TICK, raw_bid=100.00, raw_ask=100.03) == 100.02
    assert tt.toward_price(None, "sell", TICK, raw_bid=100.00, raw_ask=100.03) == 100.03


def test_the_caps_are_the_universes_target_plus_one_making_clip():
    assert tt.caps_for(128, clip=1.0) == {"research_max_total_abs_base": 384.0, "research_a195_max_seed_abs_base": 384.0}
    assert tt.caps_for(10, clip=0.25)["research_max_total_abs_base"] == 7.5


def test_the_lean_log_writes_a_repeated_row_on_its_sample_state_only():
    assert tt.lean_drop("FILL_CAL", 7) and not tt.lean_drop("FILL_CAL", 10) and not tt.lean_drop("FILL_CAL", 0)
    assert tt.lean_drop("A17431_BOOK_OWNERSHIP_RESERVE", 11) and not tt.lean_drop("V62_STATE", 11)
    assert not tt.lean_drop("ORDER_LIFECYCLE", 3) and not tt.lean_drop("RESPOND_TIMING", 3) and not tt.lean_drop("FILL", 3)
    assert not tt.lean_drop("FILL_CAL", None) and tt.lean_drop("QUOTE", 3, every=2) is True and tt.lean_drop("QUOTE", 4, every=2) is False
    assert tt.LEAN_SAMPLED_ROWS.isdisjoint({"FILL", "POSITION", "MARKOUT", "ORDER_LIFECYCLE", "A1961_TAKER_OUTCOME",
                                            "A19_EXIT_REPRICE_CANCEL", "A199_EPOCH_REWIND"})   # the v5.0.0 analytics tap


# ---- 2. the pass -----------------------------------------------------------------------------------------------------

class _Dir:
    BUY, SELL = "BUY", "SELL"


class _Resp(t14._Resp):
    def limit_order(self, **kw):
        self.instructions.append(types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=kw["book_id"], **kw))


class _Mirror:
    def __init__(self, alphas):
        self.alphas = dict(alphas)

    def book_alphas(self):
        return dict(self.alphas)


class _PassAgent:
    max_instructions_per_book = 5
    mm_base_size = 0.25

    def __init__(self, *, venue=None, fees=None, alphas=None, live_sides=None, making=True, fee_cap=True, stop=True,
                 clip=1.0, floor=18.0, last=None):
        self.research_v63_trend_target = True
        self.research_v63_making_layer = making
        self.research_v63_fee_cap = fee_cap
        self.research_v63_book_stop = stop
        self.research_v63_clip_base = clip
        self.research_v63_alpha_floor = floor
        self._v63_counts, self._v63_errors, self._v63_mids, self._v63_paused, self._v63_last = {}, 0, {}, set(), {}
        self._v63_caps_applied = False
        self.research_v631_sim_reset = True
        self._v631_last_ts, self._v631_last_sim_id, self._v631_last_reset = None, None, {}
        self._v6214_last_taker = dict(last or {})
        self._v6211_mirror = _Mirror(alphas or {})
        self.venue = dict(venue or {}); self.fees = dict(fees or {}); self.live = dict(live_sides or {})
        self.ledger = DirectExitLedger(); self.notes = []; self.emitted = []; self._tick = 9

    def _v62_on(self):
        return True

    def _a19_ledger_ref(self):
        return self.ledger

    @staticmethod
    def _v61_price_decimals(state):
        return 2

    @staticmethod
    def _execution_flat_epsilon():
        return 1e-9

    def _a195_venue_net_by_book(self, books):
        return dict(self.venue)

    def _research_live_fee_bps(self, book_id, *, is_maker=True):
        return float(self.fees.get(int(book_id), 40.0))

    def _v6215_live_sides(self, book_id):
        return frozenset(self.live.get(int(book_id), ()))

    def _direct_signed_inventory(self, book_id):
        return 0.0

    @staticmethod
    def _count_book_instructions(response, book_id):
        return sum(1 for ix in response.instructions if getattr(ix, "bookId", None) == book_id)

    def _a19_note_exit_cancel(self, book_id, order_ids, disposition):
        self.notes.append((int(book_id), list(order_ids), disposition))

    def _emit(self, event_type, force=False, **payload):
        self.emitted.append((event_type, payload))


def _pass_agent(**kwargs):
    ns = {name: getattr(tt, name) for name in (
        "V63_TREND_TARGET_VERSION",)}
    ns.update({
        "V63_ALPHA_FLOOR_DEFAULT": tt.ALPHA_FLOOR_DEFAULT, "V63_CANCEL_FEE": tt.CANCEL_FEE, "V63_CANCEL_PAUSED": tt.CANCEL_PAUSED,
        "V63_CANCEL_UNWANTED": tt.CANCEL_UNWANTED, "V63_CLIP_BASE": tt.CLIP_BASE, "V63_MAKING_CLIPS": tt.MAKING_CLIPS,
        "V63_ROLE_MAKING": tt.ROLE_MAKING, "V63_ROLE_TOWARD": tt.ROLE_TOWARD, "V63_SIDE_BUY": tt.SIDE_BUY,
        "V63_SIDE_SELL": tt.SIDE_SELL, "V63_SIGNAL_STATES": tt.SIGNAL_STATES, "V63_TARGET_CLIPS": tt.TARGET_CLIPS,
        "v63_caps_for": tt.caps_for, "v63_client_ids": tt.client_ids, "v63_fee_cap_bps": tt.fee_cap_bps,
        "v63_improve_price": tt.improve_price, "v63_log_mid_bps": tt.log_mid_bps, "v63_making_side_verdict": tt.making_side_verdict,
        "v63_new_history": tt.new_history, "v63_own_client_ids": tt.own_client_ids, "v63_role_for": tt.role_for,
        "v63_role_of": tt.role_of, "v63_signal_bps": tt.signal_bps, "v63_stop_state": tt.stop_state,
        "v63_target_base": tt.target_base, "v63_toward_price": tt.toward_price, "v63_wanted": tt.wanted,
        "v62_touch_prices": br.touch_prices, "v62_lot_quantity": br.lot_quantity, "v62_entry_client_ids": br.entry_client_ids,
        "v6214_exit_client_ids": tl.exit_client_ids, "v6214_others_touch": tl.others_touch, "v6214_last_taker_side": tl.last_taker_side,
        "v6210_touch_view": ti.touch_view, "V6215_BACKSTOP_MS": ol.BACKSTOP_MS, "ABSENT_REPRICE_CANCEL": ABSENT_REPRICE_CANCEL,
        "OrderDirection": _Dir, "STP": types.SimpleNamespace(CANCEL_BOTH="CANCEL_BOTH"),
        "TimeInForce": types.SimpleNamespace(GTT="GTT"), "LoanSettlementOption": types.SimpleNamespace(NONE="NONE"), "Any": Any,
        "V631_SIM_RESET_VERSION": sr.V631_SIM_RESET_VERSION, "v631_new_simulation": sr.new_simulation,
        "extract_simulation_id": extract_simulation_id,
        "V632TargetGate": tg632.TargetGate, "V632_PAPER_LAG_NS": tg632.PAPER_LAG_NS, "V632_SAMPLE_NS": tg632.SAMPLE_NS,
        "V62_DEFAULT_LOOKBACK_NS": tg632.LOOKBACK_NS,
    })
    cls = type("P", (_PassAgent,), {})
    for name in ("_v63_pass", "_v63_count", "_v63_clip", "_v63_apply_caps", "_v63_snapshot", "_v63_book_alphas",
                 "_v631_observe_sim", "_v6214_note_prints", "_v6214_others_depth", "_v6214_cancelled_ids",
                 "_v63_on", "_v632_gate_on", "_v632_gate_ref", "_v632_count"):
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]), "<v63>", "exec"), ns)
        setattr(cls, name, ns[name])
    return cls(**kwargs)


def _book(bid=100.00, ask=100.03, bid_q=2.5, ask_q=2.5, events=None):
    return t14.Book([t14.Level(bid, [t14.Order(1, bid_q)])], [t14.Level(ask, [t14.Order(2, ask_q)])], events=events or [])


def _state(books):
    return types.SimpleNamespace(books=books, timestamp=NOW, config=t14.b0._Config())


def _prime(agent, book_id, delta_bps, bid=100.00, ask=100.03):
    """A full history one lookback deep, ``delta_bps`` below the book's current mid (the pass appends the current)."""
    h = tt.new_history(tt.SIGNAL_STATES)
    for _ in range(tt.SIGNAL_STATES):
        h.append(tt.log_mid_bps(bid, ask) - float(delta_bps))
    agent._v63_mids[book_id] = h


def _rising(book_id, agent, **kw):
    _prime(agent, book_id, 1.0, **kw)


def _falling(book_id, agent, **kw):
    _prime(agent, book_id, -1.0, **kw)


def _placed(resp):
    return {(ix.bookId, ix.direction, ix.clientOrderId, ix.price, ix.quantity) for ix in resp.instructions if ix.type == "PLACE_ORDER_LIMIT"}


def _cancelled(resp):
    return {(ix.bookId, c.orderId) for ix in resp.instructions if ix.type == "CANCEL_ORDERS" for c in ix.cancellations}


def test_without_a_signal_only_the_making_layer_rests_one_tick_inside():
    agent = _pass_agent(); resp = _Resp(); stats = {}
    n = agent._v63_pass(resp, _state({3: _book()}), stats)
    assert n == 2
    assert _placed(resp) == {(3, "BUY", 65031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 1.0)}
    ix = [i for i in resp.instructions if i.type == "PLACE_ORDER_LIMIT"][0]
    assert ix.postOnly is True and ix.timeInForce == "GTT" and ix.expiryPeriod == int(ol.BACKSTOP_MS * 1e6) and ix.stp == "CANCEL_BOTH"
    assert agent._v63_last["flat"] == 1 and agent._v63_last["no_signal"] == 1 and stats["quoted"] == 2


def test_a_rising_mid_targets_two_clips_long_and_only_the_bid_rests():
    agent = _pass_agent(); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _placed(resp) == {(3, "BUY", 60031, 100.01, 1.0)}          # toward the target, one tick inside
    assert agent._v63_last["long"] == 1 and agent._v63_last["placed_toward"] == 1


def test_at_the_target_the_making_layer_rests_both_sides_and_beyond_it_only_the_reducing_side():
    agent = _pass_agent(venue={3: 2.0}); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _placed(resp) == {(3, "BUY", 65031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 1.0)}
    agent = _pass_agent(venue={3: 3.0}); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _placed(resp) == {(3, "SELL", 60032, 100.02, 1.0)}          # beyond the target it reduces: a target order


def test_a_flipped_signal_cancels_the_unwanted_order_and_sells_toward_the_new_target_next_state():
    agent = _pass_agent(venue={3: 2.0}); _falling(3, agent)
    t14._rest(agent, 11, 3, 0, 100.01, cid=65031)                        # the old making bid
    t14._rest(agent, 12, 3, 1, 100.02, cid=65032)                        # the old making ask
    resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _cancelled(resp) == {(3, 11)}                             # the bid is unwanted; the ask still reduces
    assert _placed(resp) == set() and agent.notes == [(3, [11], ABSENT_REPRICE_CANCEL)]
    assert agent._v63_last["short"] == 1 and agent._v63_counts.get("cancel_unwanted") == 1
    # the next state: the cancelled bid side is still owned until seen, the ask rests, nothing new is placed
    agent.live = {3: ("buy", "sell")}; resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _placed(resp) == set()
    # once the ask is gone and the bid side is free, the sell toward the target goes out
    agent.ledger.orders.clear(); agent.live = {}; resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _placed(resp) == {(3, "SELL", 60032, 100.02, 1.0)}


def test_the_making_layer_needs_a_price_one_tick_inside_but_a_target_order_joins_the_touch():
    agent = _pass_agent(); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book(ask=100.01)}), {})           # 1-tick spread
    assert _placed(resp) == {(3, "BUY", 60031, 100.00, 1.0)}
    agent = _pass_agent(venue={3: 2.0}); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book(ask=100.01)}), {})
    assert _placed(resp) == set() and agent._v63_counts.get("making_skipped_no_improve") == 2


def test_the_making_layer_skips_the_thin_and_the_just_hit_side_and_cancels_one_that_turned_so():
    agent = _pass_agent(venue={3: 2.0}); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book(bid_q=1.0, ask_q=3.0)}), {})   # the bid side is thin
    assert _placed(resp) == {(3, "SELL", 65032, 100.02, 1.0)} and agent._v63_counts.get("making_skipped_side") == 1
    agent = _pass_agent(venue={3: 2.0}, last={3: tl.TAKER_SELL}); _rising(3, agent)
    t14._rest(agent, 11, 3, 0, 100.01, cid=65031); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _cancelled(resp) == {(3, 11)} and agent._v63_counts.get("cancel_just_hit") == 1
    # a target order on the just-hit side is not touched by the side rule
    agent = _pass_agent(last={3: tl.TAKER_SELL}); _rising(3, agent)
    t14._rest(agent, 13, 3, 0, 100.01, cid=60031); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _cancelled(resp) == set()


def test_the_fee_cap_keeps_the_making_layer_off_the_dearer_half_of_the_books():
    agent = _pass_agent(venue={3: 2.0, 4: 2.0}, fees={3: 60.0, 4: 20.0, 5: 30.0}); _rising(3, agent); _rising(4, agent)
    resp = _Resp()
    agent._v63_pass(resp, _state({3: _book(), 4: _book(), 5: _book()}), {})
    assert agent._v63_last["fee_cap_bps"] == 30.0
    assert {p[0] for p in _placed(resp) if p[2] >= 65000} == {4, 5}          # book 3 (60 bps) makes nothing
    assert agent._v63_counts.get("making_skipped_fee") == 2
    agent = _pass_agent(venue={3: 2.0}, fees={3: 60.0, 4: 20.0, 5: 30.0}); _rising(3, agent)
    t14._rest(agent, 11, 3, 0, 100.01, cid=65031); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book(), 4: _book(), 5: _book()}), {})
    assert (3, 11) in _cancelled(resp) and agent._v63_counts.get("cancel_fee_capped") == 1
    agent = _pass_agent(venue={3: 2.0}, fees={3: 60.0}, fee_cap=False); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert len(_placed(resp)) == 2 and agent._v63_last["fee_cap_bps"] is None


def test_the_book_stop_targets_zero_and_quotes_no_making_layer_until_the_alpha_recovers():
    agent = _pass_agent(venue={3: 2.0}, alphas={3: -10.0}); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert 3 in agent._v63_paused and agent._v63_last["paused"] == 1
    assert _placed(resp) == {(3, "SELL", 60032, 100.02, 1.0)}          # toward flat, no making layer
    agent.alphas = None; agent._v6211_mirror = _Mirror({3: -4.0}); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert 3 not in agent._v63_paused and agent._v63_counts.get("book_releases") == 1
    agent = _pass_agent(venue={3: 2.0}, alphas={3: -10.0}, stop=False); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert not agent._v63_paused and len(_placed(resp)) == 2


def test_an_owned_side_takes_no_second_order_and_the_venue_position_is_the_inventory():
    agent = _pass_agent(live_sides={3: ("buy",)}); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _placed(resp) == set()
    agent = _pass_agent(venue={3: 1.5}); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _placed(resp) == {(3, "BUY", 60031, 100.01, 1.0), (3, "SELL", 65032, 100.02, 0.5)}   # the clip toward, half a clip of making room above
    agent = _pass_agent(venue={3: 2.9}); _rising(3, agent); resp = _Resp()
    agent._v63_pass(resp, _state({3: _book()}), {})
    assert _placed(resp) == {(3, "SELL", 60032, 100.02, 1.0)} and agent._v63_counts.get("below_min_order") == 1


def test_the_caps_are_raised_once_and_re_asserted_silently():
    agent = _pass_agent(); agent.research_max_total_abs_base = 6.0; agent.research_a195_max_seed_abs_base = 5.0
    agent._v63_apply_caps(_state({b: _book() for b in range(4)}))
    assert agent.research_max_total_abs_base == 12.0 and agent.research_a195_max_seed_abs_base == 12.0
    assert [e[0] for e in agent.emitted] == ["V63_CAPS"] and agent._v63_counts == {"caps_applied": 1}
    agent.research_max_total_abs_base = 5.0
    agent._v63_apply_caps(_state({b: _book() for b in range(4)}))
    assert agent.research_max_total_abs_base == 12.0 and agent._v63_counts.get("caps_reasserted") == 1 and len(agent.emitted) == 1
    agent.research_max_total_abs_base = 40.0                          # a larger bound is never lowered
    agent._v63_apply_caps(_state({b: _book() for b in range(4)}))
    assert agent.research_max_total_abs_base == 40.0


def test_the_snapshot_reports_the_switches_and_the_last_request():
    agent = _pass_agent(); _rising(3, agent)
    agent._v63_pass(_Resp(), _state({3: _book()}), {})
    snap = agent._v63_snapshot()
    for key in ("version", "clip", "signal_states", "target_clips", "making_clips", "alpha_floor", "paused_books", "last", "errors"):
        assert key in snap
    assert snap["version"] == tt.V63_TREND_TARGET_VERSION and snap["clip"] == 1.0 and snap["last"]["long"] == 1


# ---- 3. wiring -----------------------------------------------------------------------------------------------------

def test_all_five_rules_default_on_and_ship_in_params():
    for key in ("research_v63_trend_target", "research_v63_making_layer", "research_v63_fee_cap", "research_v63_book_stop",
                "research_v63_lean_log"):
        assert f'getattr(self.config, "{key}", True)' in SIMPLE
    for key in ("research_v63_trend_target", "research_v63_making_layer", "research_v63_book_stop", "research_v63_lean_log"):
        assert f"{key}=1" in LAUNCHER
    # R3 ships off (score first) but stays a switch the launcher carries
    assert "research_v63_fee_cap=0" in LAUNCHER and 'research_v63_fee_cap=0"* || "$PARAMS" == *"research_v63_fee_cap=1"' in LAUNCHER
    assert 'getattr(self.config, "research_v63_clip_base", V63_CLIP_BASE)' in SIMPLE and "research_v63_clip_base=1.0" in LAUNCHER
    # v6.3.2: the validator's published debeta_skill_floor moved from 18.1 (09-24) to 30.1-30.5 (09-25)
    assert 'getattr(self.config, "research_v63_alpha_floor", V63_ALPHA_FLOOR_DEFAULT)' in SIMPLE and "research_v63_alpha_floor=30" in LAUNCHER


def test_the_target_pass_owns_acquisition_and_the_frozen_exit_chain_stays_off():
    loop = _simple("build_mm_strategy_instructions")
    assert 'for raw_id, book in ({} if self._v63_on() else (getattr(state, "books", None) or {})).items():' in loop
    assert "v62_placed = self._v63_pass(response, state, stats)" in loop
    assert loop.index("self._v63_apply_caps(state)") < loop.index("self._v63_pass(response, state, stats)")
    assert loop.index("self._v62_apply_caps(state)") < loop.index("self._v63_apply_caps(state)")
    branch = loop[loop.index("if self._v63_on():\n                    # v6.3 R1"):]
    assert "else:\n                    v62_placed = self._v62_acquire(response, state, stats)\n                    self._v625_apply_caps(state)" in branch


def test_the_pass_runs_before_the_exit_ids_the_life_the_sanitizer_and_the_validator():
    loop = _simple("build_mm_strategy_instructions")
    assert loop.index("self._v63_pass(response, state, stats)") < loop.index("self._v6214_assign_exit_identity(response)") \
        < loop.index("self._v6215_normalize_expiry(response)") < loop.index("self._research_sanitize_maker_instructions(response, state)") \
        < loop.index("self._research_final_validate_instructions(response, state)")


def test_the_state_row_reports_every_switch_and_the_pins_arm_and_gate_are_v63():
    tele = _simple("_v62_telemetry")
    for key in ("trend_target_on=", "making_layer_on=", "fee_cap_on=", "book_stop_on=", "lean_log_on=", "trend_target=self._v63_snapshot()"):
        assert key in tele
    emit = _simple("_emit")
    assert emit.index('if bool(getattr(self, "research_v63_lean_log", False)) and v63_lean_drop(event_type, getattr(self, "_tick", 0)):') \
        < emit.index('analytics = getattr(self, "_v500_analytics", None)') < emit.index("return super()._emit(event_type, force=force, **payload)")
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE and 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE
    assert "strategy1_direct_v6_3_0)" in LAUNCHER and "V6215_BUILD=1; V63_BUILD=1" in LAUNCHER
    assert "strategy1_direct_v6_2_15)" in LAUNCHER
    assert 'if [[ "$V63_BUILD" == "1" ]]; then' in LAUNCHER and "[preflight] v6.3 trend target PASS" in LAUNCHER
    assert "tests/test_research_v6_3_0_trend_target.py" in LAUNCHER
    arm = LAUNCHER[LAUNCHER.index("strategy1_direct_v6_3_0) "):]
    arm = arm[:arm.index(";;") + 2]
    assert '[[ "$INHERITED_SHORT_LOTS_SOURCE" == "default" ]] && INHERITED_SHORT_LOTS="exit"' in arm
    block = LAUNCHER[LAUNCHER.index('if [[ "$V63_BUILD" == "1" ]]; then'):]
    block = block[:block.index("[preflight] v6.3 trend target PASS")]
    assert 'research_v6211_alpha_mirror=1' in block and '[[ "$INHERITED_SHORT_LOTS" == "exit" ]]' in block
    assert "for raw_id, book in ({} if self._v63_on() else" in block
