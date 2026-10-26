"""v6.41: research_v641_* -- the final SN79 0.6.5 build (scratchpad k144).

What is pinned here: the pure rules of research_v6_41_units (a state in one class's units, C2Q's reduce-only exit and
inventory-aware probe); S9's page once per request against vm739's reference rule (parity); the part's methods on a stub agent (default = v6.40.1 on every path, each switch's teeth on class-2 books: tick 0.0001,
decimals 4, minimum 0.2396, initial price 313); C2Q's window saved and put back; B1's module switches; the hook edits
and their exact undo (tests/_v641_undo.py: v6.40.1's joined Simple, launcher and module files byte for byte; the AST
undo == the text undo); the launcher's PARAMS and "# v6.41." block.  No test reads the scratchpad, the network or a
live tree."""
import ast
import hashlib
import json
import random
import sys
from collections import deque
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
PARTS = STRATEGY / "simple_parts"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(PARTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6_41_units as un  # noqa: E402
import research_v6_40_classes as cl  # noqa: E402
import _v641_undo  # noqa: E402
import _v642_undo  # noqa: E402  (k150: v6.42's undo, tests/_v642_undo.py)
import join  # noqa: E402

RAW_SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
PART = (PARTS / "v6_41_part.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
V640_JOINED_SHA256 = "e0177099db1e4e5da298bd7c93948315f7472b75a61424363d43f0a70eff44b6"   # dc17be6's joined Simple
G2 = cl.Grid(c=2, name="simulation_2", tick=0.0001, dec=4, vol_dec=4, min_order=0.2396, du=0.0104, unit=0.0104, scale=1.0)


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


# ---- 1. a state in one class's units ---------------------------------------------------------------------------------------

def test_unit_state_carries_the_class_decimals_and_reads_everything_else_through():
    books = {96: "b"}
    cfg = SimpleNamespace(priceDecimals=2, volumeDecimals=4, publish_interval=7, simulation_id="s")
    st = SimpleNamespace(config=cfg, books=books, timestamp=5, accounts={1: 2})
    u = un.unit_state(st, 4, 3)
    assert u.config.priceDecimals == 4 and u.config.volumeDecimals == 3
    assert u.config.publish_interval == 7 and u.config.simulation_id == "s"
    assert u.books is books and u.timestamp == 5 and u.accounts == {1: 2}
    assert cfg.priceDecimals == 2 and st.config is cfg                      # the real state is untouched
    d = un.unit_state(SimpleNamespace(config={"priceDecimals": 2, "x": 1}, books=books), 4, 4)
    assert d.config == {"priceDecimals": 4, "volumeDecimals": 4, "x": 1}
    with pytest.raises(AttributeError):
        un.unit_state(st, 4, 4).config.no_such_field


# ---- 2. C2Q's reduce-only exit and the probe -------------------------------------------------------------------------------

def test_the_reduce_only_exit_reduces_never_crosses_zero_and_joins_its_sides_best_price():
    assert un.reduce_exit(5.0, 1.0, 0.2396, 4, 313.0, 313.0002) == ("sell", 1.0, 313.0002)
    assert un.reduce_exit(-5.0, 1.0, 0.2396, 4, 313.0, 313.0002) == ("buy", 1.0, 313.0)
    assert un.reduce_exit(0.7, 1.0, 0.2396, 4, 313.0, 313.0002) == ("sell", 0.7, 313.0002)      # never past the position
    assert un.reduce_exit(1.1, 1.0, 0.2396, 4, 313.0, 313.0002)[1] == 1.1        # a sub-minimum remainder closes with it
    assert un.reduce_exit(1.3, 1.0, 0.2396, 4, 313.0, 313.0002)[1] == 1.0        # 0.3 >= the minimum stays for the next
    assert un.reduce_exit(0.2, 1.0, 0.2396, 4, 313.0, 313.0002) is None          # below the class minimum: no order
    assert un.reduce_exit(0.0, 1.0, 0.2396, 4, 313.0, 313.0002) is None
    assert un.reduce_exit(5.0, 1.0, 0.2396, 4, 313.0, 313.0) is None             # no uncrossed L1
    assert un.reduce_exit(5.0, 0.0, 0.2396, 4, 313.0, 313.1) is None             # no clip
    rng = random.Random(41)
    for _ in range(20000):
        pos = rng.choice((-1, 1)) * rng.uniform(0.0, 30.0)
        clip = rng.uniform(0.0, 3.0)
        bid = rng.uniform(100.0, 400.0)
        got = un.reduce_exit(pos, clip, 0.2396, 4, bid, bid + rng.choice((0.0001, 0.0005, 0.01)))
        if got is None:
            continue
        side, q, px = got
        assert q >= 0.2396 - 1e-12 and q <= abs(pos) + 1e-12                  # |pos| never increases
        assert side == ("sell" if pos > 0 else "buy")
        rest = abs(pos) - q
        assert rest < 1e-4 or rest >= 0.2396 - 1e-4          # 0 (up to one volume step) or at least ~one minimum left
        assert round(q, 4) == q


def test_a_resting_exit_is_kept_only_at_the_price_side_and_size_wanted():
    want = ("sell", 1.0, 313.0002)
    assert un.keeps_exit("sell", 313.0002, 1.0, want, 5.0, 0.0001)
    assert not un.keeps_exit("sell", 313.0004, 1.0, want, 5.0, 0.0001)        # behind the best price
    assert not un.keeps_exit("buy", 313.0002, 1.0, want, 5.0, 0.0001)
    assert not un.keeps_exit("sell", 313.0002, 6.0, want, 5.0, 0.0001)        # larger than the position
    assert un.keeps_exit("sell", 313.0002, 0.5, want, 5.0, 0.0001)


def test_a_probe_holds_the_adding_side_from_one_clip_on():
    assert un.probe_held(0.0, 1.0) == frozenset()
    assert un.probe_held(0.5, 1.0) == frozenset()
    assert un.probe_held(1.0, 1.0) == frozenset({"buy"})
    assert un.probe_held(-3.0, 1.0) == frozenset({"sell"})
    assert un.probe_held(3.0, 0.0) == frozenset()


# ---- 4. the part on a stub agent ------------------------------------------------------------------------------------------

def _part_module():
    import importlib
    try:
        return importlib.import_module("simple_parts.v6_41_part")
    except ImportError:
        import types
        for name in ("taos", "taos.im", "taos.im.protocol"):
            sys.modules.setdefault(name, types.ModuleType(name))
        models = sys.modules.get("taos.im.protocol.models") or types.ModuleType("taos.im.protocol.models")
        for enum in ("LoanSettlementOption", "OrderDirection", "STP", "TimeInForce"):
            if not hasattr(models, enum):
                setattr(models, enum, SimpleNamespace(NONE=0, BUY=0, SELL=1, CANCEL_BOTH=3, GTT=1))
        if not hasattr(models, "v641_raw_trades"):
            models.v641_raw_trades = lambda raw: None
        sys.modules["taos.im.protocol.models"] = models
        return importlib.import_module("simple_parts.v6_41_part")


class _Resp:
    def __init__(self, instructions=()):
        self.instructions = list(instructions)
        self.cancels, self.orders = [], []

    def cancel_orders(self, *, book_id, order_ids, delay):
        self.cancels.append((book_id, list(order_ids)))
        self.instructions.append({"type": "CANCEL_ORDERS", "bookId": book_id})

    def limit_order(self, **kw):
        self.orders.append(kw)
        self.instructions.append({"type": "PLACE_ORDER_LIMIT", "bookId": kw["book_id"]})


def _agent(**cfg):
    mod = _part_module()

    class Base:
        def __init__(self):
            self.calls = []

        def _refresh_dust_state(self, book_id, net_base, *, emit=True):
            self.calls.append(("dust", book_id, self._research_exchange_min_order_size))
            return True

        def _research_place_maker_exit(self, response, state, book_id, *a, **k):
            self.calls.append(("exit", book_id, state.config.priceDecimals, self._research_exchange_min_order_size))
            return 1

        def _research_apply_contract_reject_guard(self, response, state):
            dec = state.config.priceDecimals
            for ix in response.instructions:
                self.calls.append(("guard", ix["bookId"], dec))
            response.instructions[:] = [ix for ix in response.instructions if not ix.get("drop")]
            return {"skips": 1, "reprices": 0, "active": 2}

        def _select_dust_compaction_books(self, state):
            self.calls.append(("select", sorted(self._research_parked_dust), self._research_exchange_min_order_size))
            return set(self._research_parked_dust)

        def _direct_compact_selected_dust(self, response, state):
            self.calls.append(("compact", sorted(self._research_dust_compact_ids_this_tick), state.config.priceDecimals,
                               self._research_exchange_min_order_size))
            return len(self._research_dust_compact_ids_this_tick)

        def _direct_normalize_irreducible_dust(self, response, state, **kw):
            self.calls.append(("normalize", sorted(self._research_parked_dust), state.config.priceDecimals))
            return 1

        def _v6_40_request(self, state):
            self.calls.append(("v640_request",))

        def _v6_40_c2q_update(self):
            self.calls.append(("c2q_update",))
            if getattr(self, "next_st", None) is not None:      # v6.40's block decision, as the test sets it
                self._v6_40_c2q = dict(self.next_st)

        def _v6_40_c2q_cancel(self, response, book_id, rows, already, req, *, budget):
            self.calls.append(("c2q_cancel", book_id))
            return 0

        def _v6_40_count(self, key, n=1):
            pass

        def _get(self, ix, *names):
            for n in names:
                if n in ix:
                    return ix[n]
            return None

        def _research_instruction_is_maker(self, ix):
            return ix.get("maker", True)

        def _count_book_instructions(self, response, book_id):
            return sum(1 for ix in response.instructions if ix.get("bookId") == book_id)

        def _v69_live_slots(self, book_id):
            return {}

        def _a19_note_exit_cancel(self, *a):
            pass

        def _emit(self, kind, **row):
            self.calls.append(("emit", kind))

    class Agent(mod.V641Part, Base):
        def __init__(self, config):
            Base.__init__(self)
            self.config = SimpleNamespace(**config)
            self._tick = 0
            self._research_exchange_min_order_size = 0.25
            self._v6_40_grids_all = {}
            self._v6_40_grids = {}
            self._v6_40_table = None
            self._v6_40_c2 = frozenset()
            self._v6_40_c2q = {"armed": True, "probe": True}
            self.research_v640_c2q = 1
            self._research_parked_dust = {}

        @staticmethod
        def _as_bool(v):
            return str(v).strip().lower() in ("1", "true", "yes", "on")
    a = Agent(cfg)
    a._v6_41_init()
    return a


def _state():
    return SimpleNamespace(config=SimpleNamespace(priceDecimals=2, volumeDecimals=4), books={}, timestamp=10 ** 12)


def _with_class2(a):
    grids = {b: G2 for b in range(96, 128)}
    a._v6_40_grids_all, a._v6_40_grids, a._v6_40_c2 = dict(grids), dict(grids), frozenset(grids)
    a._v6_40_table = {"status": "ok", "book_class": {**{b: 0 for b in range(64)}, **{b: 1 for b in range(64, 96)},
                                                     **{b: 2 for b in range(96, 128)}}}
    return a


ON = dict(research_v641_unit_safety=1, research_v641_c2q_reduce=1, research_v641_c2q_persist=1, research_v641_s9_page=1)


def test_default_every_switch_is_off_and_every_path_is_the_base_call():
    a = _with_class2(_agent())
    assert not (a.research_v641_unit_safety or a.research_v641_c2q_reduce or a.research_v641_c2q_persist
                or a.research_v641_s9_page)
    assert a._v6_41_grid(100) is None and a._v6_41_min(100, 0.25) == 0.25 and a._v6_41_tick(100, 0.01) == 0.01
    st = _state()
    assert a._v6_41_book_state(st, 100) is st
    a._refresh_dust_state(100, 0.24)
    assert a.calls[-1] == ("dust", 100, 0.25)
    r = _Resp([{"bookId": 3}, {"bookId": 100}])
    a._research_apply_contract_reject_guard(r, st)
    assert [c for c in a.calls if c[0] == "guard"] == [("guard", 3, 2), ("guard", 100, 2)]
    a._research_parked_dust = {3: {}, 100: {}}
    a._select_dust_compaction_books(st)
    assert a.calls[-1] == ("select", [3, 100], 0.25)
    assert getattr(a, "_v661_vol", None) is None and a._v6_41_c2q_saved is None


def test_unit_safety_runs_each_frozen_path_in_the_books_class_units_and_class_0_as_before():
    a = _with_class2(_agent(**ON))
    st = _state()
    a._refresh_dust_state(3, 0.24)
    a._refresh_dust_state(100, 0.24)
    assert a.calls[-2:] == [("dust", 3, 0.25), ("dust", 100, 0.2396)] and a._research_exchange_min_order_size == 0.25
    assert a._research_place_maker_exit(None, st, 100, None, None, 1.0, "X") == 1
    assert a.calls[-1] == ("exit", 100, 4, 0.2396) and a._research_exchange_min_order_size == 0.25
    a._research_place_maker_exit(None, st, 3, None, None, 1.0, "X")
    assert a.calls[-1] == ("exit", 3, 2, 0.25)
    ixs = [{"bookId": 3}, {"bookId": 100}, {"bookId": 5, "maker": False}, {"bookId": 101, "drop": True}, {"bookId": 7}]
    r = _Resp(ixs)
    out = a._research_apply_contract_reject_guard(r, st)
    guard = [c for c in a.calls if c[0] == "guard"]
    assert guard == [("guard", 3, 2), ("guard", 5, 2), ("guard", 7, 2), ("guard", 100, 4), ("guard", 101, 4)]
    assert [ix["bookId"] for ix in r.instructions] == [3, 100, 5, 7]                     # original order, drop applied
    assert out["skips"] == 2


def test_the_dust_compactor_and_normalizer_run_per_class_and_class_2_is_left_out_while_disarmed():
    a = _with_class2(_agent(**ON))
    st = _state()
    a._research_parked_dust = {3: {}, 100: {}, 110: {}}
    sel = a._select_dust_compaction_books(st)
    assert sel == {3, 100, 110}
    assert a.calls[-2:] == [("select", [3], 0.25), ("select", [100, 110], 0.2396)]
    assert a._research_parked_dust == {3: {}, 100: {}, 110: {}}
    a._research_dust_compact_ids_this_tick = {3, 100}
    assert a._v6_41_compact(_Resp(), st) == 2
    assert a.calls[-2:] == [("compact", [3], 2, 0.25), ("compact", [100], 4, 0.2396)]
    assert a._research_dust_compact_ids_this_tick == {3, 100} and a._research_exchange_min_order_size == 0.25
    assert a._v6_41_normalize(_Resp(), st, force_liveness=False) == 2
    assert a.calls[-2:] == [("normalize", [3], 2), ("normalize", [100, 110], 4)]
    a._v6_40_c2q = {"armed": False, "probe": False}
    assert a._v6_41_normalize(_Resp(), st, force_liveness=False) == 1
    assert a.calls[-1] == ("normalize", [3], 2) and a._research_parked_dust == {3: {}, 100: {}, 110: {}}


def _row(oid, side, price, qty, cid):
    return SimpleNamespace(order_id=oid, side=side, price=price, remaining=qty, quantity=qty, client_id=cid)


def test_a_disarmed_class_2_book_keeps_one_reduce_only_exit_and_cancels_everything_else():
    a = _with_class2(_agent(**ON))
    a._v6_40_c2q = {"armed": False, "probe": False}
    import research_v617_touch_quote as tq
    buy_cid, sell_cid = tq.client_ids(100)
    deep_buy = _row(11, 0, 312.9, 1.0, 999)
    deep_sell = _row(12, 1, 313.1, 1.0, 998)
    kw = dict(budget=5, raw_bid=313.0, raw_ask=313.0002, inv=5.0, clip=1.0, expiry=10 ** 9)
    r = _Resp()
    assert a._v6_41_c2q_exit(r, 100, [deep_buy, deep_sell], set(), {}, **kw) == 0     # the sell side still holds an order
    assert r.cancels == [(100, [11, 12])] and r.orders == []
    r = _Resp()
    assert a._v6_41_c2q_exit(r, 100, [], set(), {}, **kw) == 1
    o = r.orders[0]
    assert o["direction"] == 1 and o["quantity"] == 1.0 and o["price"] == 313.0002 and o["postOnly"] is True
    assert o["clientOrderId"] == sell_cid
    r = _Resp()                                                                        # next request: kept as it is
    assert a._v6_41_c2q_exit(r, 100, [_row(20, 1, 313.0002, 1.0, sell_cid)], set(), {}, **kw) == 0
    assert r.cancels == [] and r.orders == []
    r = _Resp()                                                                        # the touch moved: re-priced
    assert a._v6_41_c2q_exit(r, 100, [_row(20, 1, 313.0003, 1.0, sell_cid)], set(), {}, **kw) == 0
    assert r.cancels == [(100, [20])] and r.orders == []
    r = _Resp()                                                                        # flat: nothing placed
    assert a._v6_41_c2q_exit(r, 100, [], set(), {}, **dict(kw, inv=0.0)) == 0 and r.orders == []
    r = _Resp()                                                                        # short: a buy at the bid
    assert a._v6_41_c2q_exit(r, 100, [], set(), {}, **dict(kw, inv=-0.5)) == 1
    assert r.orders[0]["direction"] == 0 and r.orders[0]["quantity"] == 0.5 and r.orders[0]["price"] == 313.0
    a._v6_40_grids = {}                                                                # no grid (held): v6.40's cancel
    a._v6_41_c2q_exit(_Resp(), 100, [deep_buy], set(), {}, **kw)
    assert a.calls[-1] == ("c2q_cancel", 100)


def test_a_probe_holds_the_adding_side_of_a_class_2_book_carrying_a_clip():
    a = _with_class2(_agent(**ON))
    deep = SimpleNamespace(clip=1.0)
    a._v6_40_c2q = {"armed": True, "probe": True}
    assert a._v6_41_probe_held(100, 2.0, deep, frozenset()) == frozenset({"buy"})
    assert a._v6_41_probe_held(100, -2.0, deep, frozenset({"x"})) == frozenset({"x", "sell"})
    assert a._v6_41_probe_held(100, 0.5, deep, frozenset()) == frozenset()
    assert a._v6_41_probe_held(3, 2.0, deep, frozenset()) == frozenset()              # class 0: never
    a._v6_40_c2q = {"armed": True, "probe": False}                                    # armed on evidence: never
    assert a._v6_41_probe_held(100, 2.0, deep, frozenset()) == frozenset()


def test_c2qs_window_is_saved_once_it_holds_class_2_evidence_and_put_back_before_the_first_decision():
    a = _with_class2(_agent(**ON))
    t = cl.ClassCaptureTracker()
    a._v6_32_tracker, a._v6_40_sim_id = t, "sim1"
    payload = {}
    a._v6_41_save_c2q(payload)
    assert payload == {}                                                              # no class-2 evidence: nothing
    t.k3q = deque([(1, 0.5, 1.0), (2, -0.2, 0.4)])
    t.k3q_r, t.k3q_c = 0.3, 1.4
    t.v640 = deque([(1, 0, 0.5, 1.0), (2, 2, -0.2, 0.4)])
    t.v640_sums = {0: [0.5, 1.0], 2: [-0.2, 0.4]}
    a._v6_41_save_c2q(payload)
    saved = json.loads(json.dumps(payload))
    b = _with_class2(_agent(**ON))
    b._v6_32_tracker, b._v6_40_sim_id = cl.ClassCaptureTracker(), "sim1"
    b._v6_41_read_c2q(saved)
    b._v6_40_c2q_update()
    tb = b._v6_32_tracker
    assert list(tb.k3q) == list(t.k3q) and tb.sums() == pytest.approx(t.sums())
    assert tb.class_sums(2) == pytest.approx(t.class_sums(2)) and tb.class_sums(0) == pytest.approx(t.class_sums(0))
    assert b.calls[-1] == ("c2q_update",) and b._v6_41_c2q_restore is None
    b._v6_41_read_c2q(saved)                                                  # a second read of the session: one-shot
    assert b._v6_41_c2q_restore is None
    c = _with_class2(_agent(**ON))                                                    # another simulation: not restored
    c._v6_32_tracker, c._v6_40_sim_id = cl.ClassCaptureTracker(), "sim2"
    c._v6_41_read_c2q(saved)
    c._v6_40_c2q_update()
    assert not c._v6_32_tracker.k3q


def test_the_telemetry_row_follows_v640s_request():
    a = _agent(**ON)
    a._v6_40_request(None)
    assert a.calls[-2:] == [("v640_request",), ("emit", "V6_41_STATE")]


# ---- 5. B1's module switches ------------------------------------------------------------------------------------------------

def test_b1_apply_writes_every_module_switch_and_off_restores_them():
    import research_v6_41_speed as sp
    try:
        got = sp.apply(True)
        assert all(v in (True, None) for v in got.values())
        got = sp.apply(False)
        assert all(v in (False, None) for v in got.values())
    finally:
        sp.apply(False)


# ---- 6. the hook edits and their exact undo -----------------------------------------------------------------------------------

def test_the_joined_simple_undoes_to_v640s_byte_for_byte_and_the_ast_undo_matches():
    joined = _v642_undo.joined_v641(join.joined_text(str(STRATEGY)))   # k150: v6.42's hooks out (the final v6.41's)
    assert _sha(joined) == _v641_undo.JOINED_SHA256
    manifest = json.loads((PARTS / "MANIFEST.json").read_text())
    assert manifest["pre_split_sha256"] == _v642_undo.JOINED_SHA256 and manifest["v641_base_sha256"] == V640_JOINED_SHA256
    assert manifest["v642_base_sha256"] == _v641_undo.JOINED_SHA256                    # k150
    back = _v641_undo.joined_v640(joined)
    assert _sha(back) == V640_JOINED_SHA256 == _v641_undo.BASE_JOINED_SHA256
    assert _v641_undo.joined_v640(back) == back                                        # idempotent
    assert ast.dump(_v641_undo.undo_tree(ast.parse(joined))) == ast.dump(ast.parse(back))


def test_the_split_puts_v641part_first_and_the_launcher_module_files_undo_exactly():
    assert RAW_SIMPLE.count("from simple_parts.v6_41_part import V641Part\n") == 1
    assert "class Strategy1_Research_Simple(V642Part, V641Part, V640Part, V636Part, " in RAW_SIMPLE   # k150: v6.42's first
    assert _sha(_v641_undo.launcher_v640(LAUNCHER)) == _v641_undo.LAUNCHER_BASE_SHA256
    edits = json.loads((Path(__file__).resolve().parent / "_v641_edits.json").read_text())
    for name, rec in edits["modules"].items():
        text = (ROOT / name).read_text()
        assert _sha(text) == rec["sha256"]
        assert _sha(_v641_undo.source_v640(name, text)) == rec["base_sha256"]


def test_every_v641_method_is_new_and_the_part_overrides_only_what_it_names():
    tree = ast.parse(PART)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "V641Part")
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    over = {"_refresh_dust_state", "_research_place_maker_exit", "_research_apply_contract_reject_guard",
            "_select_dust_compaction_books", "_v6_40_c2q_update", "_v6_40_request", "_v641_deferred_save"}
    assert all(n.startswith("_v6_41_") or n in over for n in names) and len(set(names)) == len(names)
    joined_defs = {n.name for n in ast.walk(ast.parse(join.joined_text(str(STRATEGY)))) if isinstance(n, ast.FunctionDef)}
    # Simple defines none of them but the frozen maker exit, whose override is reached through Simple's super() call
    assert set(names) & joined_defs == {"_research_place_maker_exit"}
    assert "return super()._research_place_maker_exit(" in join.joined_text(str(STRATEGY))


def test_the_launcher_ships_every_v641_key_once_at_its_shipped_value():
    text = _v641_undo._unflip(LAUNCHER)                     # a documented one-flip (k144 flip proofs) put back first
    start = text.index('PARAMS="')
    body = text[start + 8:text.index('"\n', start + 8)].replace("\\\n", " ")
    vals = [t.split("=", 1) for t in body.split() if "=" in t]
    for key in _v641_undo.SWITCHES:
        assert [v for k, v in vals if k == key] == [_v641_undo.SHIPPED[key]], key
    assert _v641_undo.flip_of(LAUNCHER) in [""] + list(_v641_undo.FLIPS)


def test_each_documented_flip_is_one_params_line_and_its_undo_is_the_shipped_launcher():
    base = _v641_undo._unflip(LAUNCHER)
    assert _v641_undo.flip_of(base) == ""
    for name, flip in _v641_undo.FLIPS.items():
        (key, val), = flip.items()
        old = "\n%s=%s" % (key, _v641_undo.SHIPPED[key])
        assert base.count(old) == 1, key
        flipped = base.replace(old, "\n%s=%s" % (key, val))
        assert _v641_undo.flip_of(flipped) == name and _v641_undo._unflip(flipped) == base
        assert _sha(_v641_undo.launcher_v640(flipped)) == _v641_undo.LAUNCHER_BASE_SHA256
    two = base.replace("\nresearch_v641_speed=1", "\nresearch_v641_speed=0").replace(
        "\nresearch_v641_s9_page=1", "\nresearch_v641_s9_page=0")
    assert _v641_undo.flip_of(two) is None                   # two items off at once: not a documented flip
    assert "# v6.41." in LAUNCHER and "tests/test_research_v6_41.py" in LAUNCHER


# ---- 8. S9's page once per request (B7, research_v641_s9_page) --------------------------------------------------------------

def test_s9s_page_is_observed_once_per_request_before_the_books_and_only_with_s9_on():
    import research_v6_29_touch_move as tm
    a = _agent(research_v641_s9_page=1)
    assert a.research_v641_s9_page is True and _agent().research_v641_s9_page is False
    a.research_v629_touch_fp_move, a.research_v632_state_fundamental = True, True
    a._v6_29_pages = tm.PageHistory()
    t = 10 ** 12
    a._direct_current_state_timestamp_ns = t
    snap = {"fp": {55: 293.6}, "sim_id": "s1", "sim_ts_ns": t}
    a._v6_32_s9_snapshot = lambda: snap
    st = SimpleNamespace(config=SimpleNamespace(simulation_id="s1"), books={})
    a._v6_41_s9_page(st)
    assert [p[:2] for p in a._v6_29_pages.pages] == [(t, t)]
    a._v6_41_s9_page(st)                                                    # the same request: the page is kept once
    assert len(a._v6_29_pages.pages) == 1 and a._v6_41_counts.get("s9_pages_recorded") == 1
    # _v6_29_held's own observe of the same page in the same request records nothing more (PageHistory's rule)
    assert a._v6_29_pages.observe(snap, t, "s1") is False
    a.research_v629_touch_fp_move = False                                    # S9 off: nothing observed
    a._direct_current_state_timestamp_ns = t + 10 ** 9
    a._v6_32_s9_snapshot = lambda: {"fp": {55: 293.7}, "sim_id": "s1", "sim_ts_ns": t + 10 ** 9}
    a._v6_41_s9_page(st)
    assert len(a._v6_29_pages.pages) == 1
    a.research_v629_touch_fp_move = True                                     # no state fundamental: the feed's page
    a.research_v632_state_fundamental = False
    a._v616_feed = SimpleNamespace(snapshot=lambda: {"fp": {55: 293.8}, "sim_id": "s1", "sim_ts_ns": t + 10 ** 9})
    a._v6_41_s9_page(st)
    assert len(a._v6_29_pages.pages) == 2 and a._v6_29_pages.pages[-1][2] == {55: 293.8}


# ---- 9. B1 (research_v641_speed): the module pieces the tree can test without k141's harness --------------------------------

def test_b1_fast_getattr_is_getattr_on_plain_objects_dicts_and_absent_names():
    import research_v6_41_speed as sp

    class P:
        __slots__ = ("x",)

        def __init__(self):
            self.x = 3
    obj, ns = P(), SimpleNamespace(clientOrderId=7)
    for o, name in ((obj, "x"), (obj, "clientOrderId"), (ns, "clientOrderId"), (ns, "client_id"), ({}, "keys"), (None, "a")):
        assert sp.fast_getattr(o, name, "d") == getattr(o, name, "d"), (o, name)


def test_b1_the_sweep_record_keyed_on_its_mutations_equals_the_sorted_copy_after_every_mutator():
    import research_v633_deep_layer as dlm
    saved = dlm._V641_VERSION_KEY
    try:
        dlm._V641_VERSION_KEY = True
        rec = dlm.SweepRecord(maxlen=7)
        rng = random.Random(3)
        for _ in range(3000):
            op = rng.choice(("append", "appendleft", "extend", "extend0", "pop", "popleft", "rotate", "clear", "set"))
            if op == "append":
                rec.append(rng.uniform(0, 9))
            elif op == "appendleft":
                rec.appendleft(rng.uniform(0, 9))
            elif op == "extend":
                rec.extend([rng.uniform(0, 9) for _ in range(rng.randint(1, 4))])
            elif op == "extend0":
                rec.extend([])
            elif op in ("pop", "popleft") and rec:
                getattr(rec, op)()
            elif op == "rotate":
                rec.rotate(rng.randint(-3, 3))
            elif op == "clear" and rng.random() < 0.05:
                rec.clear()
            elif op == "set" and rec:
                rec[rng.randrange(len(rec))] = rng.uniform(0, 9)
            assert rec.sorted_floats() == sorted(float(x) for x in rec)
    finally:
        dlm._V641_VERSION_KEY = saved


def test_b1_add_first_runs_before_every_task_queued_so_far():
    import research_v629_reply_path as rp
    w = rp.DeferredWork()
    got = []
    w.add("a", lambda: got.append("a"))
    w.add("b", lambda: got.append("b"))
    w.add_first("save", lambda: got.append("save"))
    assert [n for n, _fn in w._queue] == ["save", "a", "b"]


def _decided(**kw):
    st = {"armed": True, "probe": True, "block": 7, "b0": None, "b2": None, "r0": 1.0, "c0": 2.0, "r2": -1.0, "c2": 1.0,
          "pf": 1.86, "q": 0.5, "q2": float("nan")}
    st.update(kw)
    return st


def test_c2qs_last_verdict_and_its_betas_are_saved_with_the_window_and_held_while_the_mirror_is_empty():
    a = _with_class2(_agent(**ON))
    t = cl.ClassCaptureTracker()
    t.k3q, t.k3q_r, t.k3q_c = deque([(1, 0.5, 1.0), (2, -0.9, 0.4)]), -0.4, 1.4
    t.v640 = deque([(1, 0, 0.5, 1.0), (2, 2, -0.9, 0.4)])
    t.v640_sums = {0: [0.5, 1.0], 2: [-0.9, 0.4]}
    a._v6_32_tracker, a._v6_40_sim_id = t, "sim1"
    a._v6_40_c2q = _decided(armed=False, probe=False, b0=0.8, b2=0.3)          # the last block: disarmed
    payload = {}
    a._v6_41_save_c2q(payload)
    saved = json.loads(json.dumps(payload))
    assert saved[next(iter(saved))]["verdict"] == {"armed": False, "probe": False, "block": 7, "b0": 0.8, "b2": 0.3}
    # a relaunch in the same simulation, the making mirror still empty (v6.40 would probe): the first block takes the
    # saved verdict itself, the later ones the rule on the current sums with the saved betas
    b = _with_class2(_agent(**ON))
    b._v6_32_tracker, b._v6_40_sim_id = cl.ClassCaptureTracker(), "sim1"
    b._v6_41_read_c2q(saved)
    b.next_st = _decided(block=8, r0=0.5, c0=1.0, r2=0.9, c2=0.4)
    b._v6_40_c2q_update()
    assert b._v6_40_c2q["armed"] is False and b._v6_40_c2q["probe"] is False and b._v6_40_c2q_verdict == "disarmed"
    assert b._v6_41_counts.get("c2q_saved_verdict_blocks") == 1 and (b._v6_40_c2q["b0"], b._v6_40_c2q["b2"]) == (0.8, 0.3)
    b.next_st = _decided(block=8, armed=True)                                 # the same block again: nothing recomputed
    b._v6_40_c2q_update()
    assert b._v6_40_c2q_verdict == "disarmed"
    b.next_st = _decided(block=9, r0=0.5, c0=1.0, r2=0.9, c2=0.4)              # the next block: the rule, saved betas
    b._v6_40_c2q_update()
    want = cl.c2q_decision(-0.4, 1.4, 0.5, 1.0, 0.9, 0.4, 0.8, 0.3, 1.86)
    assert want["probe"] is False and b._v6_40_c2q["armed"] is want["armed"] is True and b._v6_40_c2q["probe"] is False
    assert b._v6_40_c2q_verdict == "armed" and b._v6_41_counts.get("c2q_saved_betas_blocks") == 1
    # no saved betas: the saved verdict itself
    c = _with_class2(_agent(**ON))
    c._v6_32_tracker, c._v6_40_sim_id = cl.ClassCaptureTracker(), "sim1"
    nb = json.loads(json.dumps(saved))
    nb[next(iter(nb))]["verdict"].update(b0=None, b2=None)
    c._v6_41_read_c2q(nb)
    c.next_st = _decided(block=8)
    c._v6_40_c2q_update()
    assert c._v6_40_c2q["armed"] is False and c._v6_40_c2q["probe"] is False and c._v6_41_c2q_saved is not None
    # the first block the live mirror can decide releases the saved verdict
    c.next_st = _decided(block=9, armed=True, probe=False, b0=0.7, b2=0.2)
    c._v6_40_c2q_update()
    assert c._v6_41_c2q_saved is None and c._v6_40_c2q["armed"] is True
    # a new simulation id: nothing restored (fresh: v6.40's probe)
    e = _with_class2(_agent(**ON))
    e._v6_32_tracker, e._v6_40_sim_id = cl.ClassCaptureTracker(), "sim2"
    e._v6_41_read_c2q(saved)
    e.next_st = _decided(block=8)
    e._v6_40_c2q_update()
    assert e._v6_41_c2q_saved is None and e._v6_40_c2q["probe"] is True and not e._v6_32_tracker.k3q


def test_s9s_reference_page_matches_vm739s_rule_on_every_state_only_with_the_page_once_per_request():
    """vm739 (scratchpad k137/vm739.py, the fmg block of its state loop): FP_H = the newest page whose time T <= t - fplag -
    fmh, read from the whole page tape on EVERY state.  With v6.32 P1 the agent's pages are the states' own fundamentals
    (page time = the state's time, fplag 0); its S9 reference is the newest page first seen at or before t - W.  Observed on
    every request (s9_page) the two choose the same page on every state; observed only on requests with a touch decision
    (v6.40.1) they do not."""
    import bisect
    import research_v6_29_touch_move as tm
    rng = random.Random(29)
    times, t = [], 10 ** 12
    for _ in range(3000):
        t += int(rng.choice((1.0, 1.0, 1.0, 2.0, 0.7)) * 1e9)
        times.append(t)
    W = 31.0

    def engine(i):                                    # np.searchsorted(FP_T, s - fmh, side="right") - 1 over the tape
        j = bisect.bisect_right(times, times[i] - W * 1e9) - 1
        return times[j] if j >= 0 else None

    def agent(decides):
        a = _agent(research_v641_s9_page=1)
        a.research_v629_touch_fp_move, a.research_v632_state_fundamental = True, True
        a._v6_29_pages = tm.PageHistory()
        out = []
        st = SimpleNamespace(config=SimpleNamespace(simulation_id="s1"), books={})
        for i, ts in enumerate(times):
            a._direct_current_state_timestamp_ns = ts
            snap = {"fp": {1: 100.0 + i}, "sim_id": "s1", "sim_ts_ns": ts}
            a._v6_32_s9_snapshot = lambda snap=snap: snap
            if decides is None:
                a._v6_41_s9_page(st)                   # v6.41: once per request, before the books
            elif decides(i):
                a._v6_29_pages.observe(snap, ts, "s1")  # v6.40.1: only inside a touch decision
            ref = a._v6_29_pages.reference(ts, W)
            out.append(ref[1] if ref is not None else None)
        return out
    eng = [engine(i) for i in range(len(times))]
    assert agent(None) == eng                                                # parity on every state
    sparse = agent(lambda i: rng.random() < 0.6)                            # 40 % of requests without a touch decision
    assert sum(1 for x, y in zip(sparse, eng) if x != y) > 100


# ---- 10. B4 (coordinator 11:0x JST 10-26; k140 R5: S9X passed, SX failed): S9 ships off, S12b on, D1 / D4 intact --------------

def _shipped_params():
    start = LAUNCHER.index('PARAMS="')
    body = LAUNCHER[start + 8:LAUNCHER.index('"\n', start + 8)].replace("\\\n", " ")
    return dict(t.split("=", 1) for t in body.split() if "=" in t)


def test_b4_ships_s9_off_with_s12b_d1_and_d4_on_and_the_undo_gives_v6401s_launcher():
    vals = _shipped_params()
    assert vals["research_v629_touch_fp_move"] == "0"                          # S9 off (R5 S9X)
    assert vals["research_v631_touch_improve"] == "1"                          # S12b on (SX failed)
    assert vals["research_v633_touch_identity"] == "1" and vals["research_v630_deep_tier"] == "1"   # D4, D1 on
    assert vals["research_v630_s9_lookback"] == "1"            # D2 kept as a key; inert while S9 is off (it multiplies S9)
    assert "research_v629_touch_fp_move=1 \\\n" in _v641_undo.launcher_v640(LAUNCHER)     # v6.40.1's launcher: S9 on
    assert _sha(_v641_undo.launcher_v640(LAUNCHER)) == _v641_undo.LAUNCHER_BASE_SHA256


def test_d4_the_touch_identity_does_not_depend_on_s9():
    """The user's standing rule (10-16): each UID a different agent at the touch.  D4 (v6.33) is drawn from the UID and
    the book alone and gates the touch quote on its own; none of its statements sits under S9's switch, so S9 off keeps it
    whole (D2, S9's look-back multiple, is the identity dimension S9 off makes inert)."""
    import research_v6_33_touch_identity as ti
    src = (STRATEGY / "research_v6_33_touch_identity.py").read_text()
    assert "v629" not in src and "touch_fp_move" not in src
    assert all(ti.draw(220, b) in (0, 1) for b in range(128))
    assert len({tuple(ti.draw(u, b) for b in range(64)) for u in (162, 220, 30)}) == 3       # per-UID profiles differ
    joined = join.joined_text(str(STRATEGY))
    for fn in ("_v617_touch", "_v6_40_touch_core"):
        node = next(n for n in ast.walk(ast.parse(joined if fn == "_v617_touch" else (PARTS / "v6_40_part.py").read_text()))
                    if isinstance(n, ast.FunctionDef) and n.name == fn)
        body = ast.unparse(node)
        assert body.count("self._v6_33_off(book_id)") == 1, fn                  # D4 read once per touch decision
        for sub in ast.walk(node):
            if isinstance(sub, ast.If) and "research_v629_touch_fp_move" in ast.unparse(sub.test):
                assert "_v6_33" not in ast.unparse(sub), fn                     # no D4 statement under S9's switch


# ---- 11. a FRESH registration (coordinator 10-26: every UID pruned; v6.41 starts from nothing) --------------------------------

def test_a_fresh_uid_with_no_saved_c2q_state_is_the_normal_cold_start():
    """No session, an empty one, one of another build or one whose C2Q entry is missing / malformed: nothing is restored, no
    error is counted, v6.40's rule decides from the first block (its probe), and the first save writes nothing until class-2
    evidence exists -- no warning loop, no refused start."""
    for raw in (None, {}, {"other": 1}, {"v641_c2q_window": None}, {"v641_c2q_window": {"version": 99}},
                {"v641_c2q_window": "garbage"}):
        a = _with_class2(_agent(**ON))
        a._v6_32_tracker, a._v6_40_sim_id = cl.ClassCaptureTracker(), "fresh"
        a._v6_41_read_c2q(raw)
        assert a._v6_41_c2q_restore is None and a._v6_41_errors == 0, raw
        a.next_st = _decided(block=1)                                         # v6.40's cold start: a probe
        a._v6_40_c2q_update()
        a._v6_40_c2q_update()
        assert a._v6_40_c2q["probe"] is True and a._v6_41_c2q_saved is None and a._v6_41_errors == 0, raw
        assert "c2q_restored" not in a._v6_41_counts and "c2q_restore_skipped" not in a._v6_41_counts, raw
        payload = {}
        a._v6_41_save_c2q(payload)                                            # no class-2 evidence yet: nothing saved
        assert payload == {} and a._v6_41_errors == 0, raw
    b = _with_class2(_agent(**ON))                                            # no tracker at all (C2Q off): nothing
    b._v6_41_read_c2q({"v641_c2q_window": {"version": 1, "k3q": [], "cls": []}})
    b._v6_40_c2q_update()
    assert b._v6_41_errors == 0
