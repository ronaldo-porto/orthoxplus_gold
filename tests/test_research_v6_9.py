"""v6.9: a deep ladder -- S1 two more deep orders per side at each book's sweep-record p99 and max (one and two deep
clips, owned per level), S2 a four-clip bound, S3 a book ahead of its volume line keeps only its deepest order, S4 the
pooled board is the regime gate again.

Replayed 09-29/30 JST on the recorded prints (the validator's arithmetic, proportional_both pay against the field at six
snapshot times, the daily cap consumed from the simulation's start): the v6.8 model 1.112% mean pay, the ladder 1.585%
(+42.6%), kept books 124-127, kappa 2.1-4.8, worst book -100..-170.
"""
import ast
import re
import subprocess
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_contract_guard as cg  # noqa: E402
import research_direct_book_ownership as bo  # noqa: E402
import research_direct_exposure as dx  # noqa: E402
import research_direct_inflight_reservation as ir  # noqa: E402
import research_exit_quantity as eq  # noqa: E402
import research_v62_breadth as br  # noqa: E402
import research_v6214_touch_life as tl  # noqa: E402
import research_v6215_order_life as ol  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v63_trend_target as tt  # noqa: E402
import research_v64_board as b64  # noqa: E402
import research_v65_deep_clips as dc  # noqa: E402
import research_v69_deep_ladder as d69  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_4_1_volume_pace as t641  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
import test_research_v6_5_deep_clips as t65  # noqa: E402
import test_research_v6_8 as t68  # noqa: E402
from research_direct_exit_ledger import DirectExitLedger  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
TICK = 0.01
S = 1_000_000_000
SWITCHES = ("research_v69_deep_ladder", "research_v69_deep_first_pace")
# a book whose record rests level 0 (p97) at 30 ticks, the p99 level at 45 and the max level at 60
RECORD = [20.0] * 385 + [30.0] * 10 + [45.0] * 4 + [60.0]
DEPTH = {0: 30.0, 1: 45.0, 2: 60.0}
SIZE = {0: 2.0, 1: 2.0, 2: 4.0}
EXPIRY = 50_000_000_000


def _method(name, **extra):
    scope = {"Any": object, "V69_DEEP_LADDER_VERSION": d69.V69_DEEP_LADDER_VERSION,
             "V69_LADDER_QUANTILES": d69.LADDER_QUANTILES, "V69_LADDER_CLIPS": d69.LADDER_CLIPS,
             "v69_live_slots": d69.live_slots, "v69_caps_for": d69.caps_for, **extra}
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _px(depth, side, bid=100.00, ask=100.03):
    return dl.deep_price(0.5 * (bid + ask), depth, side, bid=bid, ask=ask, tick=TICK, decimals=2)


def _level(k, **book):
    b, s = dl.level_client_ids(3, k)
    return {(3, "BUY", b, _px(DEPTH[k], dl.SIDE_BUY, **book), SIZE[k]),
            (3, "SELL", s, _px(DEPTH[k], dl.SIDE_SELL, **book), SIZE[k])}


def _layer(alpha=50.0):
    layer = dl.DeepLayer(clip=2.0, max_clips=d69.DEEP_MAX_CLIPS, sweep_quantile=0.97)
    db = dl.DeepBook()
    db.sweeps.extend(RECORD)
    db.paper.fills = 1
    db.paper.buckets[dl.sampled_key(t63.NOW)] = [float(alpha), 0.0, 1.0, 0.0]
    layer.books[3] = db
    return layer


def _agent(*, ladder=True, deep_first=True, live=None, pace=False, **kw):
    """The v6.4 pass agent (v6.4.1 with ``pace``) with the v6.9 switches; ``live`` = {book: [(side, client id)]} is what
    the venue and the pending ledger show live."""
    agent = (t641._agent if pace else t64._agent)(_layer(), **kw)
    agent.research_v69_deep_ladder, agent.research_v69_deep_first_pace = ladder, deep_first
    agent._v69_counts, agent._v69_errors = {}, 0
    agent.slots = {b: list(rows) for b, rows in (live or {}).items()}
    agent._v69_live_slots = lambda b: d69.live_slots(agent.slots.get(int(b), ()))
    return agent


def _rest(agent, oid, side, level, *, price=None, book=3, **px):
    """A deep order of ours acknowledged and live at the venue."""
    cid = dl.level_client_ids(book, level)[side]
    if price is None:
        price = _px(DEPTH[level], dl.SIDE_BUY if side == 0 else dl.SIDE_SELL, **px)
    t63.t14._rest(agent, oid, book, side, price, cid=cid, qty=SIZE[level])
    agent.slots.setdefault(book, []).append((side, cid))
    return oid


def _rest_all(agent, first=21):
    for i, (side, level) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1), (0, 2), (1, 2))):
        _rest(agent, first + i, side, level)


# ---- 1. the rules -----------------------------------------------------------------------------------------------------

def test_s1_the_ladder_ids_are_deep_ids_one_pair_per_level():
    assert dl.client_ids(3) == dl.level_client_ids(3, 0) == (40031, 40032)             # v6.3.3's pair, unchanged
    assert dl.level_client_ids(3, 1) == (40033, 40034) and dl.level_client_ids(3, 2) == (40035, 40036)
    assert dl.level_client_ids(127, 2) == (41275, 41276) and dl.DEEP_LEVELS == 3
    for level in range(dl.DEEP_LEVELS):
        for cid in dl.level_client_ids(3, level):
            assert dl.deep_level(cid) == level and dl.is_deep_client_id(cid)
            assert dl.deep_level(str(cid)) == level                                    # a pending key holds it as text
    for cid in (40030, 40037, 40039, 39999, 42001, 60033, 65035, 70033, 80035, None, "", "x"):
        assert dl.deep_level(cid) is None and not dl.is_deep_client_id(cid), cid
    for b in (0, 3, 127):
        ladder = set(dl.level_client_ids(b, 1)) | set(dl.level_client_ids(b, 2))
        assert not (ladder & tt.own_client_ids(b)) and not (ladder & set(br.entry_client_ids(b)))
        assert not (ladder & set(tl.exit_client_ids(b)))
    assert dl.own_client_ids(3) == {40031, 40032}


def test_s1_the_levels_are_the_records_p99_and_max_each_deeper_than_level_0():
    layer = _layer()
    assert layer.depth(3) == 30.0 and layer.sweep_depths(3, d69.LADDER_QUANTILES) == (45.0, 60.0)
    assert layer.sweep_depths(3, (0.97,)) == (layer.depth(3),)                          # depth()'s own index rule
    forty = dl.DeepBook()
    forty.sweeps.extend(float(x) for x in range(40, 0, -1))
    layer.books[5] = forty
    assert layer.sweep_depths(5, (0.5, 0.97, 0.99, 1.0)) == (21.0, 39.0, 40.0, 40.0)
    assert layer.sweep_depths(5, (0.5, 0.97)) == tuple(dl.quantile(forty.sweeps, q) for q in (0.5, 0.97))
    assert d69.ladder_levels((45.0, 60.0), 30.0) == [(1, 45.0, 1.0), (2, 60.0, 2.0)]
    assert d69.ladder_levels((30.5, 60.0), 30.0) == [(2, 60.0, 2.0)]                    # not a price step deeper
    assert d69.ladder_levels((30.6, 30.5), 30.0) == [(1, 30.6, 1.0)]
    assert d69.ladder_levels((45.0, 60.0), 52.0) == [(2, 60.0, 2.0)]                    # a vacuum order past p99
    assert d69.ladder_levels((45.0, 60.0), None) == [(1, 45.0, 1.0), (2, 60.0, 2.0)]
    assert d69.ladder_levels(None, 30.0) == [] and layer.sweep_depths(9, d69.LADDER_QUANTILES) is None
    short = dl.DeepBook()
    short.sweeps.extend([5.0] * (dl.SWEEP_MIN - 1))
    layer.books[4] = short
    assert layer.sweep_depths(4, d69.LADDER_QUANTILES) is None and layer.depth(4) is None
    assert d69.LADDER_QUANTILES == (0.99, 1.0) and d69.LADDER_CLIPS == (1.0, 2.0) and d69.DEEP_MAX_CLIPS == 4.0
    # the id family has a pair per level: level 0 and every ladder level, within one book's ten ids
    assert dl.DEEP_LEVELS == 1 + len(d69.LADDER_QUANTILES) == 1 + len(d69.LADDER_CLIPS) and 2 * dl.DEEP_LEVELS <= 9


def test_s1_sizes_are_multiples_of_the_deep_clip():
    assert [d69.level_quantity(2.0, k) for k in (0, 1, 2)] == [2.0, 2.0, 4.0]
    assert [d69.level_quantity(1.0, k) for k in (0, 1, 2)] == [1.0, 1.0, 2.0]          # --deep_clip_mult 1.0
    assert d69.level_quantity(2.0, 7) == 2.0 and d69.level_quantity(None, 1) == 0.0


def test_s1_s2_the_cap_is_the_bound_plus_every_level_of_a_side_in_flight():
    caps = d69.caps_for(128, clip=2.0, max_clips=d69.DEEP_MAX_CLIPS)
    assert caps == {"research_max_total_abs_base": 2048.0, "research_a195_max_seed_abs_base": 2048.0}   # (8+2+2+4) x 128
    assert dc.caps_for(128, clip=2.0, max_clips=4.0)["research_max_total_abs_base"] == 1280.0           # v6.5: one order
    assert d69.caps_for(128, clip=1.0, max_clips=4.0)["research_max_total_abs_base"] == 1024.0


def test_s3_a_paced_side_keeps_the_deepest_level_only():
    assert [d69.deep_first_keeps(k) for k in (0, 1, 2)] == [False, False, True] and d69.DEEPEST_LEVEL == 2


def test_s1_a_live_order_holds_its_level_and_any_other_order_its_whole_side():
    slots = d69.live_slots([(0, 40031), (0, "40035"), (1, 40034), (types.SimpleNamespace(name="SELL"), "40036")])
    assert slots == {"buy": frozenset({0, 2}), "sell": frozenset({1, 2})}
    assert d69.slot_taken(slots, "buy", 0) and not d69.slot_taken(slots, "buy", 1) and d69.slot_taken(slots, 0, 2)
    assert d69.slot_taken(slots, "sell", 1) and not d69.slot_taken(slots, "sell", 0)
    for rows, side in (([(1, None)], "sell"), ([(0, 65031)], "buy"), ([(0, "")], "buy"), ([(0, 80031)], "buy")):
        held = d69.live_slots(rows)
        assert all(d69.slot_taken(held, side, k) for k in (0, 1, 2)), rows              # the whole side
        other = "buy" if side == "sell" else "sell"
        assert not any(d69.slot_taken(held, other, k) for k in (0, 1, 2)), rows
    unread = d69.live_slots([("?", 40031)])
    assert unread == {"buy": frozenset({d69.WHOLE_SIDE}), "sell": frozenset({d69.WHOLE_SIDE})}
    assert not d69.slot_taken(None, "buy", 0) and not d69.slot_taken({}, "sell", 2)


# ---- 2. the ladder in the pass ------------------------------------------------------------------------------------------

def test_s1_the_ladder_fills_in_shallow_first_one_new_order_per_side_per_request():
    agent = _agent()
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == _level(0) and t63._cancelled(resp) == set()
    _rest(agent, 11, 0, 0)
    _rest(agent, 12, 1, 0)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == _level(1) and t63._cancelled(resp) == set()
    _rest(agent, 13, 0, 1)
    _rest(agent, 14, 1, 1)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == _level(2) and t63._cancelled(resp) == set()
    _rest(agent, 15, 0, 2)
    _rest(agent, 16, 1, 2)
    resp = t64._run(agent, {3: t63._book()})
    assert resp.instructions == []                                                     # every level rests in its band
    assert agent._v69_counts == {"placed_l1": 2, "placed_l2": 2} and agent._v69_errors == 0
    ixs = [ix for ix in t64._run(_agent(), {3: t63._book()}).instructions]
    assert all(ix.postOnly is True and ix.timeInForce == "GTT" and ix.expiryPeriod == EXPIRY for ix in ixs)


def test_s1_each_level_keeps_its_own_band_and_a_resting_level_does_not_keep_the_next_out():
    agent = _agent()
    _rest(agent, 11, 0, 0)
    _rest(agent, 12, 1, 0)
    _rest(agent, 13, 0, 1, price=99.87)                  # 14.5 ticks from the mid: under half of its own 45 -> repriced
    resp = t64._run(agent, {3: t63._book()})
    assert t63._cancelled(resp) == {(3, 13)}
    # the bid's p99 order waits for its removal; its max level and the ask's p99 level go out this request
    assert t63._placed(resp) == {(3, "BUY", 40035, _px(60.0, dl.SIDE_BUY), 4.0), (3, "SELL", 40034, _px(45.0, dl.SIDE_SELL), 2.0)}
    assert agent._v69_counts == {"cancel_l1_deep_reprice": 1, "placed_l2": 1, "placed_l1": 1}
    assert agent._v633_counts.get("cancel_deep_reprice") == 1
    # a level-0 price would be in band for level 0 but not for the max level: bands are per level
    agent = _agent()
    _rest(agent, 15, 0, 2, price=_px(30.0, dl.SIDE_BUY))                                 # 30 ticks < 0.5 x 60
    resp = t64._run(agent, {3: t63._book()})
    assert t63._cancelled(resp) == {(3, 15)}


def test_s1_an_order_the_ledger_cannot_name_holds_its_whole_side():
    agent = _agent(live={3: [(0, None)]})
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "SELL", 40032, _px(30.0, dl.SIDE_SELL), 2.0)}
    agent = _agent(live={3: [(1, 80032)]})                                              # an exit of ours on the ask
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 40031, _px(30.0, dl.SIDE_BUY), 2.0)}


def test_the_ladder_off_is_v68_and_a_leftover_ladder_order_is_cancelled():
    agent = _agent(ladder=False)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == _level(0)
    _rest(agent, 11, 0, 0)
    _rest(agent, 12, 1, 0)
    resp = t64._run(agent, {3: t63._book()})
    assert resp.instructions == []                                                     # v6.8: one order per side
    agent = _agent(ladder=False)
    _rest(agent, 15, 0, 2)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._cancelled(resp) == {(3, 15)} and agent._v633_counts.get("cancel_deep_level_off") == 1
    assert t63._placed(resp) == {(3, "SELL", 40032, _px(30.0, dl.SIDE_SELL), 2.0)}      # the bid waits for the removal
    assert agent._v69_counts == {}


def test_s2_the_room_test_stays_pre_fill_so_every_level_rests_until_the_bound_is_reached():
    agent = _agent(venue={3: 7.9})                                                     # inside the 8-base bound
    for i, (side, level) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1))):
        _rest(agent, 11 + i, side, level)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == _level(2) and t63._cancelled(resp) == set()
    agent = _agent(venue={3: 8.0})                                                     # at the bound: every bid goes
    _rest_all(agent)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._cancelled(resp) == {(3, 21), (3, 23), (3, 25)} and t63._placed(resp) == set()
    assert agent._v633_counts.get("cancel_deep_no_room") == 3
    assert agent._v69_counts == {"cancel_l1_deep_no_room": 1, "cancel_l2_deep_no_room": 1}
    agent = _agent(venue={3: -8.0})                                                    # short at the bound: the asks go
    resp = t64._run(agent, {3: t63._book()})
    assert t63._placed(resp) == {(3, "BUY", 40031, _px(30.0, dl.SIDE_BUY), 2.0)}


def test_s1_in_a_blown_out_spread_level_0_rests_in_the_gap_and_the_ladder_behind_the_touch():
    wide = dict(bid=100.00, ask=100.30)                                                # vacuum at 12 ticks from the mid
    vb = b64.vacuum_price(100.15, 12.0, dl.SIDE_BUY, bid=100.00, ask=100.30, tick=TICK, decimals=2)
    va = b64.vacuum_price(100.15, 12.0, dl.SIDE_SELL, bid=100.00, ask=100.30, tick=TICK, decimals=2)
    agent = _agent()
    resp = t64._run(agent, {3: t63._book(**wide)})
    assert t63._placed(resp) == {(3, "BUY", 40031, vb, 2.0), (3, "SELL", 40032, va, 2.0)}
    _rest(agent, 11, 0, 0, price=vb)
    _rest(agent, 12, 1, 0, price=va)
    resp = t64._run(agent, {3: t63._book(**wide)})
    assert t63._placed(resp) == _level(1, **wide) and t63._cancelled(resp) == set()
    assert (99.70, 100.60) == tuple(sorted(p[3] for p in t63._placed(resp)))            # 45 ticks, behind the touch


# ---- 3. S3: deep-first pacing -------------------------------------------------------------------------------------------

def _paced(agent, inv, *, deep_first=True):
    agent.research_v69_deep_first_pace = deep_first
    resp = t63._Resp()
    req = {"deep_books": 0, "placed_deep": 0, "cancels": 0}
    rows = [r for r in agent.ledger.live_orders(3) if dl.is_deep_client_id(r.client_id)]
    agent._v633_book(resp, 3, agent._v633_deep, 30.0, 100.00, 100.03, inv, [], rows, set(), req, tick_size=TICK,
                     dec=2, clip=2.0, min_order=0.25, budget=5, expiry=EXPIRY,
                     cfg=types.SimpleNamespace(volumeDecimals=4), paced=True, bound_scale=1.0)
    return resp


def test_s3_a_paced_book_keeps_only_its_deepest_order_on_the_side_that_adds():
    agent = _agent(pace=True)
    _rest_all(agent)
    resp = _paced(agent, 0.0)                                                          # flat: both sides add
    assert t63._cancelled(resp) == {(3, 21), (3, 22), (3, 23), (3, 24)} and t63._placed(resp) == set()
    assert agent._v641_counts == {"cancel_v641_paced": 4} and agent._v69_counts == {"cancel_l1_v641_paced": 2}
    agent = _agent(pace=True)
    _rest_all(agent)
    resp = _paced(agent, 0.0, deep_first=False)                                        # v6.4.1: no adding order at all
    assert t63._cancelled(resp) == {(3, 21 + i) for i in range(6)} and t63._placed(resp) == set()
    agent = _agent(pace=True)
    resp = _paced(agent, 0.0)
    assert t63._placed(resp) == _level(2)
    assert agent._v69_counts == {"placed_l2": 2, "paced_kept_deepest": 2}
    agent = _agent(pace=True)
    resp = _paced(agent, 3.0)                                  # long: the bid adds (deepest only), the ask reduces
    assert t63._placed(resp) == {(3, "BUY", 40035, _px(60.0, dl.SIDE_BUY), 4.0), (3, "SELL", 40032, _px(30.0, dl.SIDE_SELL), 2.0)}


def test_s3_without_the_ladder_a_paced_book_is_v641s():
    agent = _agent(pace=True, ladder=False)
    resp = _paced(agent, 0.0)
    assert t63._placed(resp) == set()


# ---- 4. S1: ownership per level in the final validator --------------------------------------------------------------

VALIDATOR_NAMES = {
    "DIRECT_INFLIGHT_RESERVATION_VERSION": ir.DIRECT_INFLIGHT_RESERVATION_VERSION,
    "PendingExposureOrder": ir.PendingExposureOrder, "add_order_to_batch": dx.add_order_to_batch,
    "canonical_order_side": bo.canonical_order_side, "ownership_key": bo.ownership_key,
    "resolve_book_from_state_mapping": cg.resolve_book_from_state_mapping, "round_volume": eq.round_volume,
    "sanitize_post_only_limit_price": cg.sanitize_post_only_limit_price, "v633_deep_level": dl.deep_level,
    "v69_slot_taken": d69.slot_taken, "v6215_live_sides": ol.live_sides, "DirectExitLedger": DirectExitLedger,
}
REAL = ("_research_final_validate_instructions", "_v69_live_slots", "_v69_ledger_client_ids", "_v69_count",
        "_direct_book_has_live_order", "_v6215_live_sides", "_direct_order_client_id", "_direct_pending_ledger",
        "_direct_account_orders", "_a19_ledger_ref")


class _Host:
    """The live final validator (the last definition) with flat inventory, nothing reserved and headroom everywhere."""
    research_max_total_abs_base = 2048.0
    research_max_total_open_books = 128
    research_max_active_open_books = 128
    _research_exchange_min_order_size = 0.25

    def __init__(self, *, ladder=True, side_owned=True, account=None, pending=(), ledger=()):
        self.research_v6215_side_ownership, self.ladder = side_owned, ladder
        self.accounts = {b: types.SimpleNamespace(orders=list(rows)) for b, rows in (account or {}).items()}
        self._direct_pending_exposure_orders = {}
        for book, cid, side in pending:
            row = ir.PendingExposureOrder(book_id=book, side=side, quantity=2.0, client_order_id=cid, submitted_tick=8,
                                          submitted_timestamp_ns=1, expiry_period_ns=EXPIRY, order_kind="LIMIT")
            self._direct_pending_exposure_orders[row.key()] = row
        self._a19_ledger = DirectExitLedger()
        for oid, book, side, cid in ledger:
            self._a19_ledger.note_accepted(order_id=oid, book_id=book, side=side, price=99.5, quantity=2.0,
                                           timestamp_ns=1, tick=8, client_id=cid)
        self._tick, self._v69_counts, self.rejects, self.blocks = 9, {}, [], []

    def _v69_ladder_on(self):
        return self.ladder

    @staticmethod
    def _execution_flat_epsilon():
        return 1e-9

    @staticmethod
    def _get(obj, *names):
        for name in names:
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    def _research_instruction_side(self, instruction):
        return "buy" if str(instruction.direction).upper() in {"0", "BUY"} else "sell"

    def _research_log_final_contract_reject(self, book_id, side, old, bid, ask, reason, retry):
        self.rejects.append((int(book_id), reason))

    def _direct_emit_book_ownership_block(self, *, book_id, side, reason, quantity=0.0):
        self.blocks.append((int(book_id), side, reason))

    @staticmethod
    def _research_set_instruction_attr(instruction, name, value):
        setattr(instruction, name, value)
        return True

    def _direct_signed_inventory(self, book_id):
        return 0.0

    def _v600_on(self):
        return False

    def _a196_ledger_abs(self):
        return 0.0

    def _a1961_pending_abs(self):
        return 0.0

    def _a195_dust_exempt_abs(self, **_kw):
        return 0.0

    def _a196_inherited_parked_exempt(self, **_kw):
        return 0.0

    def _direct_outstanding_exposure_reservation(self, state):
        return 0.0, 0

    def _research_instruction_is_maker(self, instruction):
        return True

    def _research_volume_cap_headroom(self, state, book_id):
        return 1.0

    def _v66_count(self, key, n=1):
        pass

    def _emit(self, *a, **k):
        pass


for _name in REAL:
    setattr(_Host, _name, _method(_name, **VALIDATOR_NAMES))


def _acct(oid, side, cid):
    return types.SimpleNamespace(id=oid, side=side, client_id=cid, price=99.72 if side == 0 else 100.31, quantity=2.0)


def _ix(cid, side="BUY", book=3):
    return types.SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=book, direction=side, quantity=2.0,
                                 price=99.55 if side == "BUY" else 100.47, clientOrderId=cid, postOnly=True)


def _validate(host, *ixs):
    resp = types.SimpleNamespace(instructions=list(ixs))
    state = types.SimpleNamespace(books={3: t63._book()}, config=types.SimpleNamespace(priceDecimals=2, volumeDecimals=4))
    host.rejects = []
    host._research_final_validate_instructions(resp, state)
    return [ix.clientOrderId for ix in resp.instructions], list(host.rejects)


def test_s1_the_final_validator_owns_a_deep_order_per_level():
    host = _Host(account={3: [_acct(11, 0, 40031)]})                                   # level 0 live on the bid
    assert _validate(host, _ix(40033)) == ([40033], [])                                # the p99 level goes out
    assert host._v69_counts == {"slot_admitted": 1}
    assert _validate(host, _ix(40031)) == ([], [(3, "INFLIGHT_BOOK_ORDER")])           # its own level: still owned
    assert _validate(host, _ix(80031)) == ([], [(3, "INFLIGHT_BOOK_ORDER")])           # any other order: the whole side
    assert _validate(host, _ix(40032, "SELL")) == ([40032], [])                        # the ask is free
    assert _validate(host, _ix(40033), _ix(40035)) == ([40033], [(3, "SAME_REQUEST_BOOK_SIDE_OWNED")])


def test_s1_an_acknowledged_order_is_named_by_the_ledger_and_an_unnamed_one_holds_its_side():
    named = _Host(account={3: [_acct(11, 0, None)]}, ledger=[(11, 3, 0, 40031)])
    assert _validate(named, _ix(40033)) == ([40033], [])
    unnamed = _Host(account={3: [_acct(11, 0, None)]})
    assert _validate(unnamed, _ix(40033)) == ([], [(3, "INFLIGHT_BOOK_ORDER")])
    making = _Host(account={3: [_acct(11, 0, 65031)]})                                 # a v6.3 order of ours
    assert _validate(making, _ix(40033)) == ([], [(3, "INFLIGHT_BOOK_ORDER")])
    pending = _Host(pending=[(3, "40035", "buy")])                                      # sent, not yet acknowledged
    assert _validate(pending, _ix(40035)) == ([], [(3, "INFLIGHT_BOOK_ORDER")])
    assert _validate(pending, _ix(40033)) == ([40033], [])


def test_without_the_ladder_the_final_validator_is_v6215s_per_side_rule():
    host = _Host(ladder=False, account={3: [_acct(11, 0, 40031)]})
    assert _validate(host, _ix(40033)) == ([], [(3, "INFLIGHT_BOOK_ORDER")])
    assert _validate(host, _ix(40032, "SELL")) == ([40032], []) and host._v69_counts == {}


def test_the_live_slots_read_the_venue_the_ledger_and_local_reservations():
    host = _Host(account={3: [_acct(11, 0, 40031), _acct(12, 1, None), _acct(13, 0, None)]}, ledger=[(12, 3, 1, 40034)],
                 pending=[(3, "40035", "buy"), (4, "40041", "buy")])
    assert host._v69_live_slots(3) == {"buy": frozenset({0, 2, d69.WHOLE_SIDE}), "sell": frozenset({1})}
    assert host._v69_live_slots(4) == {"buy": frozenset({0}), "sell": frozenset()}
    index = host._v69_ledger_client_ids()
    assert index == {(3, 12): 40034} and host._v69_ledger_client_ids() is index        # once per request
    host._tick += 1
    assert host._v69_ledger_client_ids() is not index


# ---- 5. the agent ----------------------------------------------------------------------------------------------------

def _caps_obj(ladder):
    o = t65._obj(2.0, d69.DEEP_MAX_CLIPS)
    o._v69_ladder_on = lambda: ladder
    fn = _method("_v63_apply_caps", **t65.EXTRA)
    o._v63_apply_caps = types.MethodType(fn, o)
    return o


def test_s1_the_exposure_cap_counts_the_ladder_in_flight():
    books = types.SimpleNamespace(books={b: None for b in range(128)})
    o = _caps_obj(True)
    o._v63_apply_caps(books)
    assert o.research_max_total_abs_base == 2048.0 and o.research_a195_max_seed_abs_base == 2048.0
    o = _caps_obj(False)
    o._v63_apply_caps(books)
    assert o.research_max_total_abs_base == 1280.0                                     # v6.5: the bound + one order


def test_s1_s3_follow_their_switches_and_need_the_deep_layer():
    o = types.SimpleNamespace(research_v69_deep_ladder=True, research_v69_deep_first_pace=True)
    o._v633_on = lambda: True
    for n in ("_v69_ladder_on", "_v69_deep_first_on"):
        setattr(o, n, types.MethodType(_method(n), o))
    assert o._v69_ladder_on() and o._v69_deep_first_on()
    o.research_v69_deep_ladder = False
    assert not o._v69_ladder_on() and not o._v69_deep_first_on()                      # S3 acts on the ladder only
    o.research_v69_deep_ladder, o._v633_on = True, (lambda: False)
    assert not o._v69_ladder_on()


def test_s4_the_pooled_board_is_the_regime_gate_a_book_with_its_own_positive_record_waits_for_it():
    _agent_, resp = t68._shut_board_pass(fallback=True, book_gate=False)             # v6.9: the board shut, nothing
    assert t63._placed(resp) == set()
    _agent_, resp = t68._shut_board_pass(fallback=True, book_gate=True)              # v6.8: book 3's own record opens it
    assert {p[0] for p in t63._placed(resp)} == {3}


# ---- 6. wiring ---------------------------------------------------------------------------------------------------------

def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]


def test_the_switches_default_on_ship_in_params_and_are_preflighted():
    params = _params()
    for key in SWITCHES:
        assert f'self.{key} = self._as_bool(getattr(self.config, "{key}", True))' in SIMPLE, key
        assert f"{key}=1" in params, key
    assert "research_v65_deep_max_clips=4.0 " in params                               # S2
    assert "research_v68_book_gate=0 " in params                                     # S4
    assert 'echo "[preflight] v6.9 deep ladder PASS"' in LAUNCHER
    assert "tests/test_research_v6_9.py" in LAUNCHER


def test_every_v69_preflight_pattern_is_in_the_file_it_checks():
    start = LAUNCHER.index("  # v6.9: a deep ladder.")
    block = LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.9 deep ladder PASS"', start)]
    pats = re.findall(r"grep -qF '([^']+)' \"\$AGENT_PATH/([^\"]+)\"", block)
    assert len(pats) == 10, pats
    for pat, name in pats:
        assert pat in (STRATEGY / name).read_text(), (pat, name)


def test_the_launcher_parses():
    out = subprocess.run(["bash", "-n", str(ROOT / "run_strategy1_research_simple_multi.sh")], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr


def test_the_state_row_reports_v6_9():
    assert ('v69=(self._v69_snapshot() if getattr(self, "research_v69_deep_ladder", None) is not None else {}),'
            in _src("_v62_telemetry"))
    o = types.SimpleNamespace(_v69_counts={"placed_l1": 3}, _v69_errors=0, research_v69_deep_ladder=True,
                              research_v69_deep_first_pace=True, _v633_deep=dl.DeepLayer(clip=2.0, max_clips=4.0))
    o._v69_snapshot = types.MethodType(_method("_v69_snapshot"), o)
    assert o._v69_snapshot() == {"placed_l1": 3, "version": "deep_ladder_v6_9", "errors": 0, "deep_ladder_on": 1,
                                 "deep_first_pace_on": 1, "ladder_quantiles": [0.99, 1.0], "ladder_clips": [1.0, 2.0],
                                 "max_clips": 4.0, "pooled_board": 1}
