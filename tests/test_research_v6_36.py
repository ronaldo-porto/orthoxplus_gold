"""v6.36: research_v636_step_withdrawal -- the pre-step withdrawal (A) and the stale-side hold (D).

v6.35 + one switch, default on, its own off switch.

The fundamental steps every fp_update_period (30 sim-s) and the state at 30n carries step n first; post-step miner
takers pick off what rests across the step before our cancels land.  A: on the last state before each step (phase K - 1)
every order of ours within r x mid of the book's L1 mid is cancelled (resting) or not sent (new), r = the median of the
book's own trailing record of |ln fp step| (one making window of steps), pooled over books until the book has 30.  D: on
the step state and the next, every order on the side the step moved against.  The reference is the replay
k101/vm711.py with pst=1 ssd=1 (its _k101_steps / _k101_radius / _k101_post); the engine parity itself runs outside the
suite (scratchpad k103/check/parity636.py), these tests pin the rule element by element.

No test here touches the host's caches, the network or a live tree, and nothing is imported from the scratchpad: the
agent is a small harness class carrying v6.36's real methods, its real _init_build_switches statements, respond()'s and
build_mm_strategy_instructions' real v6.36 statements and the real helpers they call (the account orders, the entry
quote ids, the price grid, the pending ledger).
"""
import ast
import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import types
from collections import deque
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v611_wire as w611  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v6_32_fee_making as fm  # noqa: E402
import research_v6_32_state_fp as sfp  # noqa: E402
import research_v6_36_step_withdrawal as sw  # noqa: E402
from research_direct_book_ownership import (  # noqa: E402
    DIRECT_BOOK_OWNERSHIP_VERSION, canonical_order_side, reserve_pending_order)
from research_direct_exit_refresh import ABSENT_ENTRY_QUOTE_CANCEL, ABSENT_REPRICE_CANCEL  # noqa: E402
from research_direct_inflight_reservation import (  # noqa: E402
    DIRECT_INFLIGHT_RESERVATION_VERSION, PendingExposureOrder)
from research_v62_breadth import V62_STATE_EVERY_TICKS  # noqa: E402
from _harness import extractor  # noqa: E402
import _v638_undo  # noqa: E402
import _v639_undo  # noqa: E402
import _v640_undo  # noqa: E402   (k139: v6.40's paths / text undo)
import _v641_undo  # noqa: E402  (k144: v6.41's undo, tests/_v641_undo.py)
import _v642_undo  # noqa: E402  (k150: v6.42's undo, tests/_v642_undo.py)
from _harness import simple_text, read_source, copy_source, strategy_view  # noqa: E402

SIMPLE = simple_text()
MODULE = read_source(STRATEGY / "research_v6_36_step_withdrawal.py")
LAUNCHER = _v640_undo.launcher_v639(read_source(ROOT / "run_strategy1_research_simple_multi.sh"))   # k139: v6.39's
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
_dbg = extractor(read_source(STRATEGY / "Strategy1_Debug.py"), cls_name="Strategy1_Debug")
_dta = extractor(read_source(STRATEGY / "DetailedTemplateAgent.py"), cls_name="DetailedTemplateAgent")
_res = extractor(read_source(STRATEGY / "Strategy1_Research.py"), cls_name="Strategy1_Research")
S = 1_000_000_000
PERIOD, INTERVAL = 30_000_000_001, 1_000_000_000          # mainnet 0.6.4: k97/validator_0035.txt
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
SWITCH_LINE = ('self.research_v636_step_withdrawal = self._as_bool(getattr(self.config, '
               '"research_v636_step_withdrawal", True))')
CLASSES = [{"name": "simulation_0", "books": list(range(0, 96)), "priceDecimals": 2, "volumeDecimals": 4,
            "config": {"fp_update_period": PERIOD, "publish_interval": INTERVAL, "min_order_size": 0.25}},
           {"name": "simulation_1", "books": list(range(96, 128)), "priceDecimals": 2, "volumeDecimals": 4,
            "config": {"fp_update_period": PERIOD, "publish_interval": INTERVAL, "min_order_size": 2.5364}}]
CONFIG = types.SimpleNamespace(fp_update_period=PERIOD, publish_interval=INTERVAL, priceDecimals=2, classes=CLASSES)
V6_36_IDENTIFIERS = {"research_v636_step_withdrawal", "V6_36_GRID_OK", "V6_36StepRecord",
                     "V6_36_STEP_WITHDRAWAL_VERSION"}
NAMES = {"V6_36_GRID_OK": sw.GRID_OK, "V6_36StepRecord": sw.StepRecord,
         "V6_36_STEP_WITHDRAWAL_VERSION": sw.V6_36_STEP_WITHDRAWAL_VERSION, "v6_36_block_phase": sw.block_phase,
         "v6_36_hold_phase": sw.hold_phase, "v6_36_l1_of": sw.l1_of, "v6_36_mid_of": sw.mid_of,
         "v6_36_placed_price": sw.placed_price, "v6_36_pre_step": sw.pre_step, "v6_36_side_of": sw.side_of,
         "v6_36_state_map": sw.state_map, "v6_36_step_grid": sw.step_grid, "v6_36_withdrawal": sw.withdrawal}
EXTRA = dict(NAMES, V62_STATE_EVERY_TICKS=V62_STATE_EVERY_TICKS, ABSENT_ENTRY_QUOTE_CANCEL=ABSENT_ENTRY_QUOTE_CANCEL,
             ABSENT_REPRICE_CANCEL=ABSENT_REPRICE_CANCEL, Any=Any, PendingExposureOrder=PendingExposureOrder,
             reserve_pending_order=reserve_pending_order, canonical_order_side=canonical_order_side,
             DIRECT_BOOK_OWNERSHIP_VERSION=DIRECT_BOOK_OWNERSHIP_VERSION,
             DIRECT_INFLIGHT_RESERVATION_VERSION=DIRECT_INFLIGHT_RESERVATION_VERSION,
             FinanceAgentResponse=object)                     # DetailedTemplateAgent's annotation, read at def time
METHODS = ("_v6_36_request", "_v6_36_drop", "_v6_36_cancel", "_v6_36_first", "_v6_36_doomed", "_v6_36_cancelled",
           "_v6_36_count", "_v6_36_report", "_v6_36_snapshot")
HELPERS = ("_direct_account_orders", "_direct_is_entry_quote_order", "_direct_entry_quote_client_ids",
           "_direct_order_client_id", "_v61_price_decimals", "_v611_price_lift_on", "_direct_record_pending_placements",
           "_direct_pending_ledger")
FIELDS = ("version", "step_withdrawal_on", "period_source", "period_status", "fp_update_period_ns",
          "publish_interval_ns", "states_per_step", "step_ns", "excluded_books", "window_steps", "min_own_steps",
          "radius_pooled_bps", "radius_own_p50_bps", "books_with_steps", "books_own_radius", "steps_seen", "step_gaps",
          "record_breaks", "resets", "phase", "block", "plan_books", "pulls", "holds", "cap_conflicts", "cap_dropped",
          "merged", "cap_unresolved", "counts", "errors")


def _mentions636(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V6_36_IDENTIFIERS or name.startswith(("_v6_36_", "v6_36_"))):
            return True
    return False


# v6.37 (research_v637_fast_path) only adds -- its import and, in _init_build_switches, its switch and the statements
# writing it into F2's three module switches and the exit ledger's dict -- so the checks that read v6.36's Simple whole
# (the import order, v6.35 = v6.36 minus v6.36) take v6.37 out first (_Drop637; tests/test_research_v6_37.py checks it
# against v6.36); _mentions636 stays v6.36's alone (the harness execs v6.36's statements only).
V6_37_IDENTIFIERS = {"research_v637_fast_path"}


def _mentions637(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V6_37_IDENTIFIERS or name.startswith(("_v6_37_", "v6_37_"))):
            return True
    return False


# v6.38 layer 3 (research_v638_kbl, research_v638_lean + _arm, research_v638_c1 + _arm; scratchpad k124) only adds -- its
# four modules' imports, its _v6_38_ methods and statements naming its identifiers -- so the checks that read Simple whole
# take it out first (_Drop638; tests/test_research_v6_38_l3.py checks layer 3 against v6.37).
V6_38_IDENTIFIERS = {"research_v638_kbl", "research_v638_lean", "research_v638_lean_arm", "research_v638_c1",
                     "research_v638_c1_arm"}
V6_38_MODULES = ("research_v6_38_canary", "research_v6_38_kbl", "research_v6_38_lean", "research_v6_38_keeper")


def _mentions638(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V6_38_IDENTIFIERS or name.startswith(("_v6_38_", "v6_38_"))):
            return True
    return False


class _Drop638(ast.NodeTransformer):
    """Takes out what v6.38 layer 3 adds and nothing else -- its imports, its _v6_38_ methods, statements naming its
    identifiers (an if on one keeps its else) -- keeping every docstring: v6.37 (FROZEN_1)."""

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in V6_38_MODULES:
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_38_"):
                continue
            if isinstance(st, ast.If) and _mentions638(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions638(st):
                continue
            out.append(st)
        return out

    def generic_visit(self, node):
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(node, field, None), list):
                setattr(node, field, self._stmts(getattr(node, field)))
        for h in getattr(node, "handlers", None) or []:
            h.body = self._stmts(h.body)
        for name, value in ast.iter_fields(node):
            if name in ("body", "orelse", "finalbody", "handlers"):
                continue
            if isinstance(value, list):
                setattr(node, name, [self.visit(v) if isinstance(v, ast.AST) else v for v in value])
            elif isinstance(value, ast.AST):
                setattr(node, name, self.visit(value))
        return node


class _Drop637(ast.NodeTransformer):
    """Takes out what v6.37 adds and nothing else -- its import, statements naming its identifiers (an if on one keeps
    its else) -- keeping every docstring: v6.36 as committed (6171a57)."""

    def visit_Module(self, node):                      # v6.38's changes undone first (tests/_v638_undo.py)
        _v638_undo.undo(node)
        return self.generic_visit(node)

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module == "research_v6_37_fast_path":
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_37_"):
                continue
            if isinstance(st, ast.If) and _mentions637(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions637(st):
                continue
            out.append(st)
        return out

    def generic_visit(self, node):
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(node, field, None), list):
                setattr(node, field, self._stmts(getattr(node, field)))
        for h in getattr(node, "handlers", None) or []:
            h.body = self._stmts(h.body)
        for name, value in ast.iter_fields(node):
            if name in ("body", "orelse", "finalbody", "handlers"):
                continue
            if isinstance(value, list):
                setattr(node, name, [self.visit(v) if isinstance(v, ast.AST) else v for v in value])
            elif isinstance(value, ast.AST):
                setattr(node, name, self.visit(value))
        return node


class _Drop636(ast.NodeTransformer):
    """Takes out what v6.36 adds and nothing else -- its import, its _v6_36_ methods, statements naming its identifiers
    (an if on one keeps its else) -- keeping every docstring: v6.35 as committed (38e06ed; Simple unchanged since
    a9dd99e)."""

    def visit_Module(self, node):                      # v6.38's changes undone first (tests/_v638_undo.py)
        _v638_undo.undo(node)
        return self.generic_visit(node)

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module == "research_v6_36_step_withdrawal":
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_36_"):
                continue
            if isinstance(st, ast.If) and _mentions636(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions636(st):
                continue
            out.append(st)
        return out

    def generic_visit(self, node):
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(node, field, None), list):
                setattr(node, field, self._stmts(getattr(node, field)))
        for h in getattr(node, "handlers", None) or []:
            h.body = self._stmts(h.body)
        for name, value in ast.iter_fields(node):
            if name in ("body", "orelse", "finalbody", "handlers"):
                continue
            if isinstance(value, list):
                setattr(node, name, [self.visit(v) if isinstance(v, ast.AST) else v for v in value])
            elif isinstance(value, ast.AST):
                setattr(node, name, self.visit(value))
        return node


# ---- the harness -----------------------------------------------------------------------------------------------------

class _Books:
    """LazyBooks' surface the v6.36 request reads: the raw wire dicts by book id (nothing parsed)."""

    def __init__(self, raw):
        self._raw_books = {int(k): v for k, v in (raw or {}).items()}

    def items(self):
        return self._raw_books.items()

    def __iter__(self):
        return iter(self._raw_books)

    def __len__(self):
        return len(self._raw_books)


def _raw(bid=99.99, ask=100.01, f=None):
    out = {"b": [] if bid is None else [{"p": bid, "q": 1.0}], "a": [] if ask is None else [{"p": ask, "q": 1.0}]}
    if f is not None:
        out["f"] = f
    return out


def _state(*, ts, raw=None, config=CONFIG):
    return types.SimpleNamespace(timestamp=int(ts), config=config, books=_Books(raw), version=64)


def _Order(oid, side, price, cid=None, qty=1.0):
    return types.SimpleNamespace(id=oid, side=side, price=price, client_id=cid, quantity=qty)


def _Acct(orders=()):
    return types.SimpleNamespace(orders=list(orders))


class _Resp:
    def __init__(self):
        self.instructions = []

    def cancel_orders(self, book_id, order_ids, delay=0):
        self.instructions.append(types.SimpleNamespace(
            type="CANCEL_ORDERS", bookId=book_id, delay=delay,
            cancellations=[types.SimpleNamespace(orderId=o, volume=None) for o in order_ids]))

    def limit_order(self, *, book_id, direction, quantity, price, clientOrderId=None, expiryPeriod=0, **kw):
        self.instructions.append(types.SimpleNamespace(
            type="PLACE_ORDER_LIMIT", bookId=book_id, direction=direction, quantity=quantity, price=price,
            clientOrderId=clientOrderId, expiryPeriod=expiryPeriod, **kw))

    def market_order(self, *, book_id, direction, quantity, clientOrderId=None, **kw):
        self.instructions.append(types.SimpleNamespace(
            type="PLACE_ORDER_MARKET", bookId=book_id, direction=direction, quantity=quantity,
            clientOrderId=clientOrderId, **kw))


class _Base:
    max_instructions_per_book = 5

    def __init__(self, uid=9):
        self.uid = uid
        self._tick = 1
        self.emitted, self.notes = [], []
        self.accounts = {}
        self.research_v611_price_lift = True
        self._v629_request_work = None
        self._direct_pending_exposure_orders = {}
        self._v6_32_fp = None
        self.config = types.SimpleNamespace()

    def _emit(self, event_type, force=False, **payload):
        self.emitted.append((event_type, payload))

    def _a19_note_exit_cancel(self, book_id, order_ids, disposition):
        self.notes.append((int(book_id), list(order_ids), disposition))


def _fn(source, name, scope):
    ns = dict(scope)
    exec(compile(textwrap.dedent(source), f"<{name}>", "exec"), ns)
    return ns[name]


def _hooks(method):
    """The v6.36 statements of ``method`` (an if on v6.36's state), in source order."""
    fn = ast.parse(_src(method)).body[0]
    found = []
    for node in ast.walk(fn):
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            for st in stmts if isinstance(stmts, list) else []:
                if isinstance(st, ast.If) and _mentions636(st.test):
                    found.append(st)
    return sorted(found, key=lambda st: st.lineno)


_KLASS: dict = {}


def _klass():
    if "k" not in _KLASS:
        methods = {}
        for n in METHODS + HELPERS:
            methods[n] = _fn(_src(n), n, EXTRA)
        methods["_direct_entry_quote_client_ids"] = staticmethod(methods["_direct_entry_quote_client_ids"])
        methods["_get"] = staticmethod(_fn(_dbg("_get"), "_get", EXTRA))              # staticmethods in the class
        methods["_as_bool"] = staticmethod(_fn(_dbg("_as_bool"), "_as_bool", EXTRA))
        methods["_count_book_instructions"] = _fn(_dta("_count_book_instructions"), "_count_book_instructions", EXTRA)
        methods["_research_instruction_side"] = _fn(_res("_research_instruction_side"), "_research_instruction_side",
                                                    EXTRA)
        req, cancel = _hooks("respond")
        drop, = _hooks("build_mm_strategy_instructions")
        for name, args, st in (("_h_request", "self, state", req), ("_h_cancel", "self, response, state", cancel),
                               ("_h_drop", "self, response, state", drop)):
            methods[name] = _fn(f"def {name}({args}):\n" + textwrap.indent(ast.unparse(st), "    ") + "\n", name, EXTRA)
        _KLASS["k"] = type("A636", (_Base,), methods)
    return _KLASS["k"]


def _init_source():
    """The v6.36 statements of _init_build_switches -- the real ones -- as one function of self."""
    fn = ast.parse(_src("_init_build_switches")).body[0]
    body = [st for st in fn.body if _mentions636(st)]
    assert len(body) == 10, [ast.unparse(st) for st in body]
    return "def _init636(self):\n" + textwrap.indent("\n".join(ast.unparse(st) for st in body), "    ") + "\n"


def _agent(*, on=True, uid=9):
    agent = _klass()(uid=uid)
    agent.config = types.SimpleNamespace(research_v636_step_withdrawal=on)
    _fn(_init_source(), "_init636", EXTRA)(agent)
    return agent


def _request(agent, ts, raw, *, config=CONFIG):
    """respond()'s v6.36 request statement on one state."""
    state = _state(ts=ts, raw=raw, config=config)
    agent._tick += 1
    agent._h_request(state)
    return state


def _feed(agent, blocks, fps, *, books=(3,), bid=99.99, ask=100.01, phases=(29, 0)):
    """States at the given phases of each block: fps(block) -> {book: f} (the step record needs the state before and the
    first state of each block)."""
    for n in blocks:
        for ph in phases:
            blk = n if ph != 0 else n + 1
            f = fps(blk)
            _request(agent, (30 * blk + ph) * S, {b: _raw(bid, ask, f.get(b)) for b in books})


def _walk(bps_by_book, start=100.0):
    """fps(block) for books stepping a fixed |log step| (bps) every block, alternately up and down."""
    def fps(blk):
        return {b: start * math.exp(((-1) ** blk) * bps * 1e-4 / 2.0) for b, bps in bps_by_book.items()}
    return fps


def _placed(resp, book=None):
    return [ix for ix in resp.instructions if ix.type.startswith("PLACE") and (book is None or ix.bookId == book)]


def _cancelled(resp, book=None):
    return sorted((ix.bookId, c.orderId) for ix in resp.instructions if ix.type == "CANCEL_ORDERS"
                  for c in ix.cancellations if book is None or ix.bookId == book)


def _place(resp, book, side, price, cid=None):
    resp.limit_order(book_id=book, direction="BUY" if side == BUY else "SELL", quantity=1.0, price=price,
                     clientOrderId=cid)


# ---- 1. the module ---------------------------------------------------------------------------------------------------

def test_the_module_states_its_version_and_its_constants_from_their_sources():
    assert sw.V6_36_STEP_WITHDRAWAL_VERSION == "step_withdrawal_v6_36"
    assert sw.MAKING_WINDOW_NS is fm.WINDOW_NS and sw.MAKING_WINDOW_NS == 10_800 * S        # the validator's window
    assert sw.MIN_OWN_STEPS == 30 and sw.HOLD_STATES == 2 and sw.PRICE_EPS == 1e-12           # vm711 pstm / ssd / slack
    assert sw.PROTOCOL_PERIOD_PAD_NS == 1 and sw.state_map is sfp.state_map
    assert sw.SIDE_BUY == BUY and sw.SIDE_SELL == SELL
    tree = ast.parse(MODULE)
    mods = sorted(n.module for n in tree.body if isinstance(n, ast.ImportFrom)) + \
        sorted(a.name for n in tree.body if isinstance(n, ast.Import) for a in n.names)
    assert mods == ["__future__", "collections", "research_v611_wire", "research_v633_deep_layer",
                    "research_v6_32_fee_making", "research_v6_32_state_fp", "typing", "math"], mods
    doc = ast.get_docstring(tree)
    for needle in ("STRUCTURAL", "OBSERVED", "PRE-REGISTERED", "k101", "vm711", "pst=1", "ssd=1", "fp_update_period",
                   "updatePeriod + 1", "MIN_OWN_STEPS", "WINDOW_STEPS", "HOLD_STATES", "fail closed",
                   "No constant here is sized from an observed metric"):
        assert needle in doc, needle
    for word in ("socket", "fcntl", "urllib", "random", "requests", "threading", "subprocess", "time"):
        assert not re.search(rf"^\s*(import|from)\s+{word}\b", MODULE, re.M), word
    assert "30_000" not in MODULE and "= 30 *" not in MODULE                      # no hard-coded 30-s period


def test_the_grid_reads_the_period_and_the_publish_interval_from_the_configuration():
    g = sw.step_grid(CONFIG)
    assert (g["status"], g["source"], g["period_ns"], g["interval_ns"]) == (sw.GRID_OK, "config.fp_update_period",
                                                                            PERIOD, INTERVAL)
    assert (g["k"], g["step_ns"], g["window_steps"], g["excluded"]) == (30, 30 * S, 360, frozenset())
    assert sw.step_grid({"fp_update_period": PERIOD, "publish_interval": INTERVAL})["k"] == 30          # a dict too
    assert sw.step_grid({"fp_update_period": 30 * S, "publish_interval": INTERVAL})["k"] == 30          # no +1 ns
    assert sw.step_grid({"fp_update_period": 15 * S + 1, "publish_interval": INTERVAL})["window_steps"] == 720
    assert sw.step_grid({"fp_update_period": 60 * S + 1, "publish_interval": 2 * S})["k"] == 30
    for cfg, status in (({"publish_interval": INTERVAL}, sw.GRID_NO_PERIOD),
                        ({"fp_update_period": None, "publish_interval": INTERVAL}, sw.GRID_NO_PERIOD),
                        ({"fp_update_period": "x", "publish_interval": INTERVAL}, sw.GRID_NO_PERIOD),
                        ({"fp_update_period": 0, "publish_interval": INTERVAL}, sw.GRID_NO_PERIOD),
                        ({"fp_update_period": True, "publish_interval": INTERVAL}, sw.GRID_NO_PERIOD),
                        ({"fp_update_period": PERIOD}, sw.GRID_NO_INTERVAL),
                        ({"fp_update_period": PERIOD, "publish_interval": 0}, sw.GRID_NO_INTERVAL),
                        ({"fp_update_period": 30 * S + 500_000_000, "publish_interval": INTERVAL}, sw.GRID_OFF),
                        ({"fp_update_period": 30 * S + 2, "publish_interval": INTERVAL}, sw.GRID_OFF),
                        ({"fp_update_period": 2 * S + 1, "publish_interval": INTERVAL}, sw.GRID_OFF),
                        (None, sw.GRID_NO_PERIOD), ("junk", sw.GRID_NO_PERIOD)):
        g = sw.step_grid(cfg)
        assert g["status"] == status and g["k"] is None and g["window_steps"] is None, (cfg, g)
        assert sw.block_phase(29 * S, g) is None
    other = [CLASSES[0], dict(CLASSES[1], config=dict(CLASSES[1]["config"], fp_update_period=15 * S + 1))]
    g = sw.step_grid(types.SimpleNamespace(fp_update_period=PERIOD, publish_interval=INTERVAL, classes=other))
    assert g["status"] == sw.GRID_OK and g["excluded"] == frozenset(range(96, 128))   # class 1 on another grid: v6.35
    bare = [CLASSES[0], {"books": [96, 97]}]
    assert sw.step_grid({"fp_update_period": PERIOD, "publish_interval": INTERVAL, "classes": bare})["excluded"] == \
        frozenset()


def test_the_29_s_state_is_the_pre_step_state_and_30n_the_step_state():
    g = sw.step_grid(CONFIG)
    assert sw.block_phase(0, g) == (0, 0) and sw.block_phase(29 * S, g) == (0, 29)
    assert sw.block_phase(30 * S, g) == (1, 0)
    assert sw.block_phase(2594 * S, g) == (86, 14)                         # a live state time (observatory, uid 30)
    assert sw.block_phase(30 * 1750 * S, g) == (1750, 0)                   # the state at 30n is block n, never n - 1
    assert sw.block_phase(30 * 1750 * S - 1, g) == (1749, 29)
    assert sw.block_phase(-S, g) is None and sw.block_phase(None, g) is None and sw.block_phase("x", g) is None
    assert sw.pre_step(29, g) and not sw.pre_step(28, g) and not sw.pre_step(0, g) and not sw.pre_step(None, g)
    assert sw.hold_phase(0) and sw.hold_phase(1) and not sw.hold_phase(2) and not sw.hold_phase(29)
    assert not sw.hold_phase(None) and not sw.hold_phase(-1)
    g2 = sw.step_grid({"fp_update_period": 10 * S + 1, "publish_interval": INTERVAL})
    assert sw.block_phase(19 * S, g2) == (1, 9) and sw.pre_step(9, g2) and not sw.pre_step(29, g2)


def test_the_step_record_takes_a_step_at_the_first_state_of_each_new_block_against_the_previous_state():
    rec = sw.StepRecord(window_steps=360)
    assert rec.observe(29 * S, 0, {3: 100.0, 4: 50.0}) == "first" and rec.step_block is None
    assert rec.observe(30 * S, 1, {3: 101.0, 4: 50.0}) == "step"
    assert rec.step_block == 1 and list(rec.steps[3]) == [pytest.approx(math.log(1.01) * 1e4)]
    assert list(rec.steps[4]) == [0.0] and rec.direction == {3: 1, 4: 0}       # a zero step counts, no direction
    assert rec.stale_side(3, 1) == SELL and rec.stale_side(4, 1) is None and rec.stale_side(3, 2) is None
    assert rec.observe(31 * S, 1, {3: 101.0, 4: 50.0}) == "same" and rec.step_block == 1
    assert rec.observe(59 * S, 1, {3: 101.0, 4: 50.0}) == "same"
    assert rec.observe(60 * S, 2, {3: 100.0, 4: 49.0}) == "step" and rec.stale_side(3, 2) == BUY
    assert rec.stale_side(4, 2) == BUY and len(rec.steps[3]) == 2
    assert rec.observe(150 * S, 5, {3: 99.0}) == "gap" and rec.step_block is None   # blocks 3, 4 unseen: no step
    assert len(rec.steps[3]) == 2 and rec.stale_side(3, 5) is None
    assert rec.observe(180 * S, 6, {3: 99.0, 4: 49.0}) == "step" and len(rec.steps[3]) == 3
    assert len(rec.steps[4]) == 2                                           # book 4 had no fp at the state before
    assert rec.counts["steps_seen"] == 3 and rec.counts["step_gaps"] == 1


def test_a_state_without_a_fundamental_or_a_grid_breaks_the_chain_and_a_rewind_starts_over():
    rec = sw.StepRecord()
    rec.observe(29 * S, 0, {3: 100.0})
    assert rec.observe(30 * S, 1, None) == "break" and rec.step_block is None
    assert rec.observe(31 * S, 1, {3: 101.0}) == "first"
    assert rec.observe(60 * S, 2, {3: 102.0}) == "step" and len(rec.steps[3]) == 1
    assert rec.observe(61 * S, 2, {}) == "break"
    assert rec.observe(62 * S, None, {3: 102.0}) == "break"
    assert rec.observe(90 * S, 3, {3: 103.0}) == "first" and len(rec.steps[3]) == 1   # the previous state broke
    assert rec.observe(91 * S, 3, {3: 103.0}) == "same"
    assert rec.observe(5 * S, 0, {3: 100.0}) == "reset+first"                # a clock that ran back: a new run
    assert rec.steps == {} and rec.step_block is None and rec.counts["resets"] == 1
    assert rec.observe(30 * S, 1, {3: float("nan")}) == "step"             # the reset state was block 0, fp 100
    assert rec.steps == {} and rec.direction == {}                          # a non-finite step is not recorded


def test_the_radius_is_the_books_own_median_from_30_steps_else_the_pool_and_none_before_the_first_step():
    rec = sw.StepRecord(window_steps=360)
    assert rec.radius(3) == (None, False) and rec.pooled_bps() is None
    for n in range(29):
        rec.steps.setdefault(3, deque(maxlen=360)).append(float(n + 1))
    rec.steps.setdefault(4, deque(maxlen=360)).extend([100.0, 200.0])
    r, own = rec.radius(3)
    pooled = sorted(list(rec.steps[3]) + [100.0, 200.0])
    assert own is False and r == pytest.approx(pooled[len(pooled) // 2] / 1e4)      # 29 own steps: the pool
    rec.steps[3].append(30.0)
    r, own = rec.radius(3)
    assert own is True and r == pytest.approx(15.5 / 1e4)                               # 30: its own (even median)
    assert rec.radius(4)[1] is False and rec.radius(99)[1] is False                  # a book without 30: the pool
    assert rec.radius(4, pooled=7.0) == (pytest.approx(7.0 / 1e4), False)
    w = sw.StepRecord(window_steps=360)
    w.observe(0, 0, {3: 100.0})
    f = 100.0
    for n in range(1, 400):
        f *= math.exp((1.0 if n <= 39 else 5.0) * 1e-4)
        w.observe(30 * n * S, n, {3: f})
    assert len(w.steps[3]) == 360 and w.radius(3)[0] == pytest.approx(5.0 / 1e4)    # the window rolled: the last 360
    w.resize(100)
    assert len(w.steps[3]) == 100 and w.steps[3].maxlen == 100


def test_the_decisions_are_vm711s_tests():
    assert sw.mid_of(99.99, 100.01) == pytest.approx(100.0) and sw.mid_of(100.0, 100.0) is None
    assert sw.mid_of(100.01, 99.99) is None and sw.mid_of(None, 100.0) is None and sw.mid_of(99.0, "x") is None
    mid, r = 100.0, 5e-4                                                     # r x mid = 0.05
    assert sw.in_radius(100.05, mid, r) and sw.in_radius(99.95, mid, r) and sw.in_radius(100.0, mid, r)
    assert not sw.in_radius(100.06, mid, r) and not sw.in_radius(99.94, mid, r)
    assert sw.in_radius(mid + r * mid + 0.9e-12, mid, r) and not sw.in_radius(mid + r * mid + 1e-11, mid, r)
    assert not sw.in_radius(None, mid, r) and not sw.in_radius(100.0, None, r) and not sw.in_radius(100.0, mid, None)
    w = sw.withdrawal
    assert w(BUY, 99.97, mid=mid, radius=r) == sw.PULL and w(SELL, 100.03, mid=mid, radius=r) == sw.PULL
    assert w(BUY, 99.90, mid=mid, radius=r) is None and w(BUY, 99.97, mid=None, radius=r) is None
    assert w(SELL, 101.0, hold_side=SELL) == sw.HOLD and w(BUY, 99.0, hold_side=SELL) is None
    assert w(SELL, 100.01, mid=mid, radius=r, hold_side=SELL) == sw.PULL
    assert w(None, 101.0, hold_side=SELL) is None and w(BUY, 99.0) is None
    for v, side in ((0, BUY), (1, SELL), ("BUY", BUY), ("sell", SELL), ("bid", BUY), ("ask", SELL),
                    (types.SimpleNamespace(name="SELL", value=1), SELL), (True, None), (None, None), (7, None)):
        assert sw.side_of(v) == side, v


def test_l1_and_the_placed_price_read_the_wire_and_the_venues_grid():
    assert sw.l1_of(_raw(99.99, 100.01)) == (99.99, 100.01)
    assert sw.l1_of(_raw(None, 100.01)) == (None, 100.01) and sw.l1_of({"b": [{"p": 0.0}], "a": []}) == (None, None)
    lvl = types.SimpleNamespace
    book = lvl(bids=[lvl(price=99.98)], asks=[lvl(price=100.02)])
    assert sw.l1_of(book) == (99.98, 100.02) and sw.l1_of(lvl(bids=[], asks=None)) == (None, None)
    assert sw.l1_of(lvl(_raw=_raw(1.0, 2.0))) == (1.0, 2.0) and sw.l1_of(None) == (None, None)
    assert sw.l1_of(lvl(_raw={"f": 1.0}, bids=[lvl(price=3.0)], asks=[lvl(price=4.0)])) == (3.0, 4.0)   # no wire side
    assert sw.l1_of({"f": 1.0}) == (None, None)
    low = 228.12999999999999545                                              # a double below its decimal (v6.1.1)
    assert sw.placed_price(low, 2, True) == 228.13 and sw.placed_price(low, 2, False) == 228.12
    assert sw.placed_price(low, 2, True) == w611.venue_placed_price(w611.wire_price(low, 2), 2)
    assert sw.placed_price(100.005, 2, True) == 100.0 and sw.placed_price(None, 2, True) is None
    assert sw.placed_price(-1.0, 2, True) is None and sw.placed_price(100.01, "x", True) == 100.01


# ---- 2. the agent: the request (H0) ----------------------------------------------------------------------------------

def test_the_init_statements_set_the_state_and_the_switch_ships_default_on():
    agent = _agent()
    assert agent.research_v636_step_withdrawal is True and isinstance(agent._v6_36_record, sw.StepRecord)
    assert (agent._v6_36_grid, agent._v6_36_l1, agent._v6_36_req, agent._v6_36_counts) == ({}, {}, {}, {})
    assert (agent._v6_36_errors, agent._v6_36_errors_seen, agent._v6_36_reported, agent._v6_36_erring) == (0, 0, False,
                                                                                                         False)
    assert _agent(on=False).research_v636_step_withdrawal is False and _agent(on="0").research_v636_step_withdrawal is \
        False
    assert SIMPLE.count(SWITCH_LINE) == 1
    a = _klass()()
    a.config = types.SimpleNamespace()
    _fn(_init_source(), "_init636", EXTRA)(a)
    assert a.research_v636_step_withdrawal is True                                     # absent from the config: on


def test_the_pre_step_state_plans_each_books_radius_and_mid_and_the_step_states_its_stale_side():
    agent = _agent()
    _feed(agent, range(10, 45), _walk({3: 4.0, 7: 10.0}), books=(3, 7))      # 35 steps, alternately up and down
    rec = agent._v6_36_record
    assert len(rec.steps[3]) == 35 and rec.radius(3) == (pytest.approx(4.0 / 1e4), True)
    st = _request(agent, (30 * 45 + 29) * S, {3: _raw(99.99, 100.01, 100.0), 7: _raw(49.99, 50.03, 50.0),
                                               8: _raw(10.0, 10.02, 10.0)})
    req = agent._v6_36_req
    assert (req["phase"], req["block"], req["pre_step"], req["hold"]) == (29, 45, True, False)
    assert req["books"][3] == (pytest.approx(100.0), pytest.approx(4.0 / 1e4), None)
    assert req["books"][7] == (pytest.approx(50.01), pytest.approx(10.0 / 1e4), None)
    pooled = (4.0 + 10.0) / 2.0                                              # the median of 35 x 4 and 35 x 10
    assert req["books"][8] == (pytest.approx(10.01), pytest.approx(pooled / 1e4), None)   # a new book: the pool
    assert agent._v6_36_counts["pull_own_radius"] >= 2 and agent._v6_36_counts["pull_pooled_radius"] >= 1
    f46 = _walk({3: 4.0, 7: 10.0})(46)
    _request(agent, 30 * 46 * S, {3: _raw(f=f46[3]), 7: _raw(f=f46[7])})
    req = agent._v6_36_req
    assert (req["phase"], req["pre_step"], req["hold"]) == (0, False, True)
    side3 = SELL if f46[3] > _walk({3: 4.0})(45)[3] else BUY
    assert req["books"] == {3: (None, None, side3), 7: (None, None, side3)}
    _request(agent, (30 * 46 + 1) * S, {3: _raw(f=f46[3]), 7: _raw(f=f46[7])})
    assert agent._v6_36_req["hold"] is True and agent._v6_36_req["books"][3] == (None, None, side3)
    _request(agent, (30 * 46 + 2) * S, {3: _raw(f=f46[3]), 7: _raw(f=f46[7])})
    assert agent._v6_36_req["books"] == {} and agent._v6_36_req["hold"] is False      # 30n + 2: normal
    _request(agent, (30 * 46 + 15) * S, {3: _raw(f=f46[3])})
    assert agent._v6_36_req["books"] == {} and agent._v6_36_req["phase"] == 15


def test_the_mid_is_the_books_last_two_sided_l1_and_a_crossed_or_missing_one_plans_no_pull():
    agent = _agent()
    _feed(agent, range(0, 31), _walk({3: 4.0, 4: 4.0, 5: 4.0}), books=(3, 4, 5))
    agent._v6_36_l1.pop(5, None)
    _request(agent, (30 * 31 + 28) * S, {3: _raw(99.98, 100.02, 1.0), 4: _raw(99.0, 101.0, 1.0)})
    _request(agent, (30 * 31 + 29) * S, {3: _raw(None, 100.04, 1.0), 4: _raw(101.0, 99.0, 1.0),
                                          5: _raw(None, 10.0, 1.0)})
    plan = agent._v6_36_req["books"]
    assert plan[3][0] == pytest.approx(100.0)                     # one side empty now: the last two-sided L1 (vm711)
    assert 4 not in plan and 5 not in plan                        # crossed now; never two-sided
    assert agent._v6_36_counts["pull_no_mid"] >= 2


def test_without_a_readable_period_the_switch_does_nothing_and_says_why():
    for cfg, status in ((types.SimpleNamespace(publish_interval=INTERVAL), sw.GRID_NO_PERIOD),
                        (types.SimpleNamespace(fp_update_period=PERIOD), sw.GRID_NO_INTERVAL),
                        (types.SimpleNamespace(fp_update_period=PERIOD + 500_000_000, publish_interval=INTERVAL),
                         sw.GRID_OFF), (None, sw.GRID_NO_PERIOD)):
        agent = _agent()
        for n in range(40):
            _request(agent, (30 * n + 29) * S, {3: _raw(f=100.0 + n)}, config=cfg)
            _request(agent, 30 * (n + 1) * S, {3: _raw(f=101.0 + n)}, config=cfg)
        assert agent._v6_36_req["books"] == {} and agent._v6_36_req["phase"] is None
        assert agent._v6_36_record.steps == {} and agent._v6_36_counts["grid_" + status] == 80
        assert agent._v6_36_snapshot()["period_status"] == status
        resp = _Resp()
        _place(resp, 3, BUY, 100.0)
        agent.accounts = {3: _Acct([_Order(1, 0, 100.0)])}
        agent._h_drop(resp, _state(ts=0))
        agent._h_cancel(resp, _state(ts=0))
        assert len(resp.instructions) == 1 and resp.instructions[0].type == "PLACE_ORDER_LIMIT"


def test_the_record_reads_v632s_map_of_the_same_state_else_the_states_own_fundamentals():
    agent = _agent()
    agent._v6_32_fp = (29 * S, {3: 100.0}, None)
    _request(agent, 29 * S, {3: _raw(f=555.0)})
    assert agent._v6_36_record.last_fp == {3: 100.0}                       # v6.32 P1's map of this very state
    agent._v6_32_fp = (29 * S, {3: 100.0}, None)                            # stale: another state's
    _request(agent, 30 * S, {3: _raw(f=101.0)})
    assert agent._v6_36_record.last_fp == {3: 101.0} and list(agent._v6_36_record.steps[3]) == [
        pytest.approx(math.log(1.01) * 1e4)]
    agent._v6_32_fp = None
    _request(agent, 31 * S, {3: _raw(f=None)})
    assert agent._v6_36_record.counts["breaks"] == 1                        # no fundamental anywhere: a break


def test_the_switch_off_plans_nothing_and_records_nothing():
    agent = _agent(on=False)
    _feed(agent, range(0, 40), _walk({3: 4.0}))
    _request(agent, (30 * 40 + 29) * S, {3: _raw(f=1.0)})
    assert agent._v6_36_req == {"phase": None, "block": None, "pre_step": False, "hold": False, "books": {}}
    assert agent._v6_36_record.counts["states"] == 0 and agent._v6_36_counts == {} and agent._v6_36_l1 == {}


# ---- 3. the agent: the drop (H1) and the cancel (H2) -----------------------------------------------------------------

def _pre_step_agent(bps=4.0, books=(3, 7), n=35, **kw):
    """An agent on the pre-step state of block 10 + n after n steps of ``bps`` on ``books`` (mid 100.0)."""
    agent = _agent(**kw)
    _feed(agent, range(10, 10 + n), _walk({b: bps for b in books}), books=books)
    st = _request(agent, (30 * (10 + n) + 29) * S, {b: _raw(99.99, 100.01, 100.0) for b in books})
    return agent, st


def test_a_cancels_exactly_the_in_radius_resting_orders_of_every_layer_and_drops_the_in_radius_placements():
    agent, st = _pre_step_agent()                                            # r x mid = 0.04 around 100.00
    touch_b, touch_s = 43031, 43032
    deep_b, lad_s, exit_s, entry_b = 60031, 60034, 21000, 70031
    agent.accounts = {3: _Acct([_Order(11, 0, 99.97, touch_b), _Order(12, 1, 100.04, touch_s),
                                _Order(13, 0, 99.95, deep_b), _Order(14, 1, 100.20, lad_s),
                                _Order(15, 1, 100.03, exit_s), _Order(16, 0, 99.96, entry_b)]),
                      7: _Acct([_Order(21, 0, 99.99, 43071)]), 9: _Acct([_Order(31, 0, 99.99, 43091)])}
    resp = _Resp()
    _place(resp, 3, BUY, 99.98, 43031)                                       # in: dropped
    _place(resp, 3, SELL, 100.05, 60032)                                     # out: kept
    _place(resp, 9, BUY, 99.99, 43091)                                       # book 9 outside the plan: kept
    resp.market_order(book_id=3, direction="SELL", quantity=1.0)            # not a resting order: untouched
    agent._h_drop(resp, st)
    limits = [(ix.bookId, ix.price) for ix in _placed(resp) if ix.type == "PLACE_ORDER_LIMIT"]
    assert limits == [(3, 100.05), (9, 99.99)]
    assert len(_placed(resp)) == 3 and agent._v6_36_counts["dropped_pull"] == 1
    agent._h_cancel(resp, st)
    assert _cancelled(resp) == [(3, 11), (3, 12), (3, 15), (3, 16), (7, 21)]        # 13 (0.05) and 14 out
    first3 = next(ix for ix in resp.instructions if ix.bookId == 3)
    assert first3.type == "CANCEL_ORDERS" and resp.instructions.index(first3) == 0  # first among book 3's
    assert sorted(agent.notes) == [(3, [11, 12, 15], ABSENT_REPRICE_CANCEL), (3, [16], ABSENT_ENTRY_QUOTE_CANCEL),
                                   (7, [21], ABSENT_REPRICE_CANCEL)]
    c = agent._v6_36_counts
    assert c["cancelled_pull"] == 5 and c["cancel_instructions"] == 2 and "cancelled_hold" not in c


def test_the_drop_judges_the_price_the_order_would_rest_at():
    agent, st = _pre_step_agent()                                            # r x mid = 0.04 around 100.00
    resp = _Resp()
    _place(resp, 3, SELL, 100.0449999)                                       # off the grid: rests at 100.04 -- in
    low = math.nextafter(100.05, 0.0)                                        # a double below 100.05: v6.1.1 lifts it
    _place(resp, 3, SELL, low)                                               # rests at 100.05 -- out
    agent._h_drop(resp, st)
    assert [ix.price for ix in _placed(resp)] == [low]
    agent.research_v611_price_lift = False                                   # no lift: the venue truncates to 100.04
    resp = _Resp()
    _place(resp, 3, SELL, low)
    agent._h_drop(resp, st)
    assert _placed(resp) == []


def test_the_policy_requotes_at_the_step_state_and_d_holds_the_stale_side_incl_reducing_orders_for_two_states():
    agent, st = _pre_step_agent()
    f_next = _walk({3: 4.0, 7: 4.0})(46)
    up = f_next[3] > _walk({3: 4.0})(45)[3]
    stale, fresh = (SELL, BUY) if up else (BUY, SELL)
    sside = 1 if stale == SELL else 0
    for ph in (0, 1):
        st = _request(agent, (30 * 46 + ph) * S, {3: _raw(f=f_next[3]), 7: _raw(f=f_next[7])})
        agent.accounts = {3: _Acct([_Order(41, sside, 100.50), _Order(42, 1 - sside, 99.50)])}
        resp = _Resp()
        _place(resp, 3, stale, 100.01 if stale == SELL else 99.99)            # the stale side: held (reducing or not)
        _place(resp, 3, fresh, 99.99 if fresh == BUY else 100.01)            # the side the step moved toward: sent
        resp.market_order(book_id=3, direction="SELL" if stale == SELL else "BUY", quantity=1.0)   # no resting order
        agent._h_drop(resp, st)
        agent._h_cancel(resp, st)
        limits = [ix.direction for ix in _placed(resp) if ix.type == "PLACE_ORDER_LIMIT"]
        assert limits == ["BUY" if fresh == BUY else "SELL"], ph
        assert [ix.type for ix in _placed(resp)].count("PLACE_ORDER_MARKET") == 1, ph
        assert _cancelled(resp) == [(3, 41)], ph                              # far from the mid too: D is no radius
    st = _request(agent, (30 * 46 + 2) * S, {3: _raw(f=f_next[3])})
    resp = _Resp()
    _place(resp, 3, stale, 100.01 if stale == SELL else 99.99)
    agent.accounts = {3: _Acct([_Order(41, sside, 100.50)])}
    agent._h_drop(resp, st)
    agent._h_cancel(resp, st)
    assert len(_placed(resp)) == 1 and _cancelled(resp) == []               # 30n + 2: the policy as usual
    assert agent._v6_36_counts["dropped_hold"] == 2 and agent._v6_36_counts["cancelled_hold"] == 2


def test_a_cancel_already_in_the_response_is_never_repeated_and_other_books_are_untouched():
    agent, st = _pre_step_agent()
    agent.accounts = {3: _Acct([_Order(11, 0, 99.99), _Order(12, 1, 100.01)]), 7: _Acct([_Order(11, 0, 99.99)])}
    resp = _Resp()
    resp.cancel_orders(3, [11])                                              # a post-pass already cancels 11 on book 3
    agent._h_drop(resp, st)
    agent._h_cancel(resp, st)
    assert _cancelled(resp) == [(3, 11), (3, 12), (7, 11)]                   # 12 added; book 7's own id 11 too
    assert [ix.bookId for ix in resp.instructions] == [3, 3, 7] and resp.instructions[0].cancellations[0].orderId == 12


def test_the_withdrawal_cancel_goes_first_in_its_book_and_the_cap_lets_the_cancel_win():
    agent, st = _pre_step_agent()
    agent.accounts = {3: _Acct([_Order(11, 0, 99.99)])}
    resp = _Resp()
    resp.cancel_orders(3, [90])
    for i, px in enumerate((100.10, 100.11, 100.12, 100.13)):                # outside the radius: kept by A
        _place(resp, 3, SELL, px, 60000 + i)
    _place(resp, 7, SELL, 100.20)
    agent._h_drop(resp, st)                                                  # 5 on book 3: one placement must go
    assert [ix.price for ix in _placed(resp, 3)] == [100.10, 100.11, 100.12]
    assert agent._v6_36_counts["cap_conflicts"] == 1 and agent._v6_36_counts["cap_dropped"] == 1
    agent._h_cancel(resp, st)
    book3 = [ix for ix in resp.instructions if ix.bookId == 3]
    assert len(book3) == 5 and book3[0].type == "CANCEL_ORDERS" and [c.orderId for c in book3[0].cancellations] == [11]
    assert resp.instructions[0] is book3[0]


def test_a_book_the_post_passes_filled_after_the_reservation_has_the_ids_merged_and_never_exceeds_the_cap():
    agent, st = _pre_step_agent()
    agent.accounts = {3: _Acct([_Order(11, 0, 99.99), _Order(12, 1, 100.02)])}
    resp = _Resp()
    for i in range(3):
        _place(resp, 3, SELL, 100.10 + i / 100, 60000 + i)
    agent._h_drop(resp, st)                                                  # 3 + 1 <= 5: nothing to drop
    resp.cancel_orders(3, [80])                                              # then two post-pass cancels (a191, v6214)
    resp.cancel_orders(3, [81])
    agent._h_cancel(resp, st)
    book3 = [ix for ix in resp.instructions if ix.bookId == 3]
    assert len(book3) == 5 and book3[0].type == "CANCEL_ORDERS"
    assert [c.orderId for c in book3[0].cancellations] == [11, 12, 80]       # merged into the first cancel, re-issued
    assert [c.orderId for c in book3[-1].cancellations] == [81]
    assert agent._v6_36_counts["merged"] == 1 and agent.notes == [(3, [11, 12], ABSENT_REPRICE_CANCEL)]
    full = _Resp()
    for i in range(5):
        _place(full, 3, SELL, 100.10 + i / 100)                              # full, and no cancel to merge into
    before = list(full.instructions)
    agent._h_cancel(full, st)
    assert full.instructions == before and agent._v6_36_counts["cap_unresolved"] == 1


def test_a_dropped_placement_is_never_recorded_as_resting():
    agent, st = _pre_step_agent()
    resp = _Resp()
    _place(resp, 3, BUY, 99.99, 43031)                                       # in the radius: dropped
    _place(resp, 3, SELL, 100.30, 60032)                                     # kept
    agent._h_drop(resp, st)
    agent._direct_record_pending_placements(resp, st)                        # what build_mm runs right after
    ledger = agent._direct_pending_ledger()
    assert [(k[0], k[2]) for k in ledger] == [(3, "sell")] and len(ledger) == 1
    body = _src("build_mm_strategy_instructions")
    final = body.index("self._research_final_validate_instructions(response, state)")
    drop = body.index("self._v6_36_drop(response, state)")
    record = body.index("self._direct_record_pending_placements(response, state)")
    assert final < drop < record


def test_an_error_on_one_book_leaves_that_book_as_v635_and_counts():
    agent, st = _pre_step_agent()
    agent.accounts = {3: _Acct([_Order(11, 0, 99.99)]), 7: _Acct([_Order("bad", 0, 99.99)])}
    resp = _Resp()
    _place(resp, 3, BUY, 99.99)
    agent._h_drop(resp, st)
    agent._h_cancel(resp, st)
    assert _cancelled(resp) == [(3, 11)] and agent._v6_36_errors == 2               # book 7 in the drop and the cancel
    agent2, st2 = _pre_step_agent()
    agent2._v6_36_req = {"books": {3: "not a plan"}}
    resp = _Resp()
    _place(resp, 3, BUY, 99.99)
    agent2._h_drop(resp, st2)
    agent2._h_cancel(resp, st2)
    assert len(resp.instructions) == 1 and agent2._v6_36_errors == 3               # placement, reservation, cancel
    agent3 = _agent()
    agent3._v6_36_record = None                                              # the request itself fails: no plan
    _request(agent3, 29 * S, {3: _raw(f=1.0)})
    assert agent3._v6_36_req["books"] == {} and agent3._v6_36_errors == 1


def test_a_class_on_another_grid_keeps_v635_on_its_books():
    other = [CLASSES[0], dict(CLASSES[1], config=dict(CLASSES[1]["config"], fp_update_period=15 * S + 1))]
    cfg = types.SimpleNamespace(fp_update_period=PERIOD, publish_interval=INTERVAL, priceDecimals=2, classes=other)
    agent = _agent()
    fps = _walk({3: 4.0, 100: 4.0})
    for n in range(10, 45):
        for ph, blk in ((29, n), (0, n + 1)):
            _request(agent, (30 * blk + ph) * S, {b: _raw(99.99, 100.01, fps(blk)[b]) for b in (3, 100)}, config=cfg)
    _request(agent, (30 * 45 + 29) * S, {b: _raw(99.99, 100.01, 100.0) for b in (3, 100)}, config=cfg)
    assert agent._v6_36_grid["excluded"] == frozenset(range(96, 128)) and set(agent._v6_36_req["books"]) == {3}
    _request(agent, 30 * 46 * S, {b: _raw(99.99, 100.01, fps(46)[b]) for b in (3, 100)}, config=cfg)
    assert agent._v6_36_req["hold"] is True and set(agent._v6_36_req["books"]) == {3}


def test_the_switch_off_is_v635_on_every_hook():
    agent, st = _pre_step_agent(on=False)
    agent.accounts = {3: _Acct([_Order(11, 0, 99.99)])}
    resp = _Resp()
    _place(resp, 3, BUY, 99.99)
    resp.cancel_orders(3, [5])
    before = [vars(ix).copy() for ix in resp.instructions]
    agent._h_drop(resp, st)
    agent._h_cancel(resp, st)
    assert [vars(ix) for ix in resp.instructions] == before and agent.notes == []
    assert [e for e, _kw in agent.emitted] == ["V6_36_STATE"] and agent.emitted[0][1]["step_withdrawal_on"] == 0


# ---- 4. the row ------------------------------------------------------------------------------------------------------

def test_the_row_reports_the_grid_the_record_the_pulls_the_holds_the_cap_and_the_errors_on_its_cadence():
    agent, st = _pre_step_agent()
    agent.accounts = {3: _Acct([_Order(11, 0, 99.99)])}
    resp = _Resp()
    _place(resp, 3, SELL, 100.01)
    agent._tick = 7
    agent._h_drop(resp, st)
    agent._h_cancel(resp, st)
    rows = [kw for e, kw in agent.emitted if e == "V6_36_STATE"]
    assert len(rows) == 1 and rows[0]["reason"] == "start" and rows[0]["tick"] == 7
    row = rows[0]
    assert set(FIELDS) <= set(row) and row["version"] == sw.V6_36_STEP_WITHDRAWAL_VERSION
    assert (row["step_withdrawal_on"], row["period_status"]) == (1, "ok")
    assert row["period_source"] == "config.fp_update_period"
    assert (row["fp_update_period_ns"], row["publish_interval_ns"], row["states_per_step"]) == (PERIOD, INTERVAL, 30)
    assert (row["window_steps"], row["min_own_steps"], row["phase"], row["plan_books"]) == (360, 30, 29, 2)
    assert row["pulls"] == {"cancelled": 1, "dropped": 1} and row["holds"] == {"cancelled": 0, "dropped": 0}
    assert row["radius_own_p50_bps"] == pytest.approx(4.0) and row["books_own_radius"] == 2 and row["errors"] == 0
    agent._tick = 8
    agent._h_cancel(_Resp(), st)
    assert len([1 for e, _ in agent.emitted if e == "V6_36_STATE"]) == 1                # no new reason: no row
    agent._tick = V62_STATE_EVERY_TICKS
    agent._h_cancel(_Resp(), st)
    assert agent.emitted[-1][1]["reason"] == "periodic"
    agent._v6_36_errors += 1
    agent._tick += 1
    agent._h_cancel(_Resp(), st)
    assert agent.emitted[-1][1]["reason"] == "error"

    class _Work:
        def __init__(self):
            self.jobs = []

        def add(self, name, fn):
            self.jobs.append((name, fn))
    work = _Work()
    agent2, st2 = _pre_step_agent()
    agent2._v629_request_work = work
    agent2._h_cancel(_Resp(), st2)
    assert [n for n, _ in work.jobs] == ["v6_36_state_row"] and agent2.emitted == []
    work.jobs[0][1]()
    assert agent2.emitted[0][0] == "V6_36_STATE"                                      # deferred after the reply


# ---- 5. the wiring ---------------------------------------------------------------------------------------------------

def test_the_hooks_sit_where_the_design_puts_them():
    respond = _src("respond")
    v632 = respond.index("self._v6_32_request(state)")
    req = respond.index("self._v6_36_request(state)")
    chain = respond.index("response = super().respond(state) if quiet is None else quiet")
    stall = respond.index("self._a199_note_exit_stalls(response)")
    cancel = respond.index("self._v6_36_cancel(response, state)")
    telemetry = respond.index("def _v629_post_reply_telemetry(state):")
    assert v632 < req < chain < stall < cancel < telemetry
    for post in ("self._a191_service_reprice_cancels(response, state)",
                 "self._v6214_service_touch_life(response, state)",
                 "self._a195_cancel_orphan_orders(response, state)",
                 "self._v611_lift_outgoing_prices(response, state)"):
        assert respond.index(post) < cancel, post
    test = "getattr(self, '_v6_36_counts', None) is not None"
    assert [ast.unparse(h.test) for h in _hooks("respond")] == [test, test]
    assert [ast.unparse(h.test) for h in _hooks("build_mm_strategy_instructions")] == [test]
    tree = _Drop637().visit(_Drop638().visit(ast.parse(SIMPLE)))                # v6.36: v6.38 L3, v6.37 out
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)]
    names = [n.module for n in imports]
    i = names.index("research_v6_36_step_withdrawal")
    assert names[i - 1] == "research_v6_34_class1" and names[i + 1] == "research_v64_board"
    assert {a.asname or a.name for a in imports[i].names} == set(NAMES)
    init = _src("_init_build_switches")
    assert init.index("self._v6_34_erring = False") < init.index(SWITCH_LINE) < init.index("self._v69_counts")
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    order = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    j = order.index("_v6_34_snapshot")
    assert order[j + 1:j + 1 + len(METHODS)] == list(METHODS) and order[j + 1 + len(METHODS)] == "_v619_snapshot"


# v6.35 = v6.34 (a9dd99e) with PARAMS only: its Simple is a9dd99e's (sha256 ecdf6929ff98...); the digest of its AST was
# computed once from that file and is embedded here -- per interpreter, since ast.dump's text differs between Python
# minors (k123: 3.10 = the miners' venv, so the launcher gate's interpreter; 3.12 = the host's python3)
V635_AST_SHA256_BY_PY = {(3, 10): "f720caa928d02c08e1ac97cafb356a6323a9f902623b45c821a008d06aa0c900",
                         (3, 12): "b786087b590517c0b93d6645850a668ab445d6b43b68f68b7591fc436c67547b"}
V635_AST_SHA256 = V635_AST_SHA256_BY_PY.get(tuple(sys.version_info[:2]), "no v6.35 digest for this interpreter")


def _ast_sha(tree):
    return hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def test_every_v635_statement_is_in_v636_unchanged_and_v636_only_adds():
    stripped = _Drop636().visit(_Drop637().visit(_Drop638().visit(ast.parse(SIMPLE))))   # later versions out
    assert _ast_sha(stripped) == V635_AST_SHA256                                  # v6.35 = v6.36 minus v6.36
    assert _ast_sha(ast.parse(SIMPLE)) != V635_AST_SHA256 and not _mentions636(stripped)
    for good, bad in (("self._research_final_validate_instructions(response, state)\n",
                       "self._research_sanitize_maker_instructions(response, state)\n"),
                      ("self._v6_32_request(state)", "self._v6_32_report()"),
                      ("            self._a199_note_exit_stalls(response)\n", "            pass\n")):
        assert SIMPLE.count(good) == 1, good
        assert _ast_sha(_Drop636().visit(_Drop637().visit(_Drop638().visit(
            ast.parse(SIMPLE.replace(good, bad)))))) != \
            V635_AST_SHA256, good
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show",
                              "a9dd99e:agents/strategy/Strategy1_Research_Simple.py"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        out = None
    if out is not None and out.returncode == 0:
        assert ast.dump(stripped) == ast.dump(ast.parse(out.stdout))


def test_the_frozen_modules_are_v635s():
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "diff", "--name-only", "a9dd99e", "--",
                              "agents/strategy", "taos"], cwd=ROOT, capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip("git or a9dd99e is not available here")
    # k106 F2 changes three more files, each pinned to its committed code by the F2 strip (tests/test_research_v6_37.py)
    f2 = {"agents/strategy/research_direct_exit_ledger.py", "agents/strategy/research_direct_idle_gc.py",
          "taos/im/protocol/models.py"}
    # v6.37 (6171a57 + F2): the module is committed since 6171a57, so git lists it beside Simple
    v636 = {"agents/strategy/research_v6_36_step_withdrawal.py"}
    # v6.37's and v6.38's own modules (listed once committed, k111 note 2) and v6.38's paths (tests/_v638_undo.py), with
    # v6.38 layer 3's four modules (listed once committed, k126)
    later = ({"agents/strategy/research_v6_37_fast_path.py"} | _v638_undo.V638_NEW | _v638_undo.V638_MODULES
             | _v638_undo.V638_ARCHIVED | _v638_undo.V638_L3_MODULES
             | (_v639_undo.V639_PATHS - {"agents/strategy/Strategy1_Research_Simple.py"})   # k129: v6.39's, either state
             | (_v640_undo.V640_PATHS - {"agents/strategy/Strategy1_Research_Simple.py"})    # k139: v6.40's, either state
             | (_v641_undo.V641_PATHS - {"agents/strategy/Strategy1_Research_Simple.py"})    # k144: v6.41's, either state
             | (_v642_undo.V642_PATHS - {"agents/strategy/Strategy1_Research_Simple.py"}))   # k150: v6.42's, either state
    assert [n for n in out.stdout.split() if n not in f2 | v636 | later] == ["agents/strategy/Strategy1_Research_Simple.py"]
    assert (STRATEGY / "research_v6_36_step_withdrawal.py").is_file()           # + the new module


# ---- 6. the launcher -------------------------------------------------------------------------------------------------

def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start + len('PARAMS="'):LAUNCHER.index('"', start + len('PARAMS="'))].replace("\\\n", " ")


def _block():
    start = LAUNCHER.index('if [[ "${V6_36_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.36 step withdrawal PASS', start)]


def _patterns():
    lists = re.findall(r"for _v636_pat in \\\n((?:      '[^']+' \\\n)*      '[^']+'); do", _block())
    assert len(lists) == 2, lists
    return [re.findall(r"'([^']+)'", x) for x in lists]


def test_the_v636_block_is_gated_preflighted_listed_and_ships_on():
    assert LAUNCHER.count("; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0\n") == 1
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_36_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_36_BUILD=") == 2
    assert "      tests/test_research_v6_34.py \\\n      tests/test_research_v6_36.py \\\n" in LAUNCHER
    assert 'research_v634_class1_entry=0 \\\nresearch_v636_step_withdrawal=1 \\\n' in LAUNCHER   # v6.37's next
    assert " research_v636_step_withdrawal=1 " in f" {_params()} " and _params().split()[-10] == \
        "research_v636_step_withdrawal=1"                       # then v6.37's key, v6.38's two and v6.38 L3's five
    assert LAUNCHER.index('echo "[preflight] v6.34 class-1 entry PASS') < LAUNCHER.index("# v6.36.") < \
        LAUNCHER.index('echo "[preflight] v6.36 step withdrawal PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    head = LAUNCHER[LAUNCHER.index("# v6.36."):LAUNCHER.index('if [[ "${V6_36_BUILD:-0}" == "1" ]]; then')]
    for needle in ("STRUCTURAL", "OBSERVED", "PRE-REGISTERED", "k101", "vm711 pst=1 ssd=1", "fp_update_period",
                   "hard-coded 30 s", "ships 1 but may be 0", "=0 / =1", "PARAMS edit", "first among its",
                   "max_instructions_per_book", "V6_36_STATE"):
        assert needle in head, needle
    assert LAUNCHER.count("# v6.36.") == 1


def test_every_v636_preflight_pattern_is_in_the_file_it_checks():
    agent_pats, module_pats = _patterns()
    assert len(agent_pats) == 12 and len(module_pats) == 7
    for p in agent_pats:
        assert SIMPLE.count(p) == 1, p
    for p in module_pats:
        assert MODULE.count(p) == 1, p
    assert {SWITCH_LINE, "self._v6_36_request(state)", "self._v6_36_drop(response, state)",
            "self._v6_36_cancel(response, state)", "self._v6_36_report()",
            "from research_v6_36_step_withdrawal import ("} <= set(agent_pats)
    assert '[[ -f "$AGENT_PATH/research_v6_36_step_withdrawal.py" ]] || {' in _block()


def _run_block(params, agent_path=strategy_view(), build="1"):
    start = LAUNCHER.index("# v6.36.")
    stop = LAUNCHER.index('echo "[preflight] v6.36 step withdrawal PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "SCRIPT_DIR": str(ROOT), "V6_36_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v636_block_passes_the_shipped_params_and_refuses_a_build_it_cannot_ship():
    shipped = _params()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "[preflight] v6.36 step withdrawal: on" and len(lines) == 2, out.stdout
    assert lines[1].startswith("[preflight] v6.36 step withdrawal PASS (on: "), out.stdout
    key = "research_v636_step_withdrawal"
    off = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {key}=0 "))
    assert off.returncode == 0 and off.stdout.splitlines()[0] == "[preflight] v6.36 step withdrawal: off"
    for bad in (f"{key}=2", "", f"{key}=true", f"{key}=11", f"{key}=01", f"x{key}=1", f"{key}=-1"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {bad} "))
        assert out.returncode == 1 and f"without {key}=0 or 1" in out.stderr and "PASS" not in out.stdout, bad
    for twice in (f"{key}=1 {key}=1", f"{key}=1 {key}=0"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {twice} "))
        assert out.returncode == 1 and f"{key} given 2 times" in out.stderr, twice
    out = _run_block("", build="0")
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v636_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    agent_pats, module_pats = _patterns()
    cases = [(p, "Strategy1_Research_Simple.py") for p in agent_pats] + \
        [(p, "research_v6_36_step_withdrawal.py") for p in module_pats]
    for i, (needle, name) in enumerate(cases):
        tmp = tmp_path / str(i)
        tmp.mkdir()
        for base in ("Strategy1_Research_Simple.py", "research_v6_36_step_withdrawal.py"):
            copy_source(STRATEGY / base, tmp / base)
        path = tmp / name
        path.write_text(path.read_text().replace(needle, "pass  # removed", 1))
        out = _run_block(_params(), agent_path=tmp)
        assert out.returncode == 1 and needle in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
    bare = tmp_path / "no_module"
    bare.mkdir()
    copy_source(STRATEGY / "Strategy1_Research_Simple.py", bare / "Strategy1_Research_Simple.py")
    out = _run_block(_params(), agent_path=bare)
    assert out.returncode == 1 and "module is missing" in out.stderr
