"""v6.37: research_v637_fast_path -- F2's reply-time changes (scratchpad k106) behind ONE launcher switch.

v6.36 + one switch, default on, its own off switch; every decision is v6.36's either way.

F2 (k106) made the reply path ~20 % faster without changing a decision (its identity runs on a virtual clock: every
response and every persisted session file byte-identical to v6.36).  Its changes sit in three modules, each behind that
module's own switch, read where its code runs -- "if <switch>: F2's code, else: the original code":

1. taos/im/protocol/models.py, _F2_FAST.  LazyLevel built every Order of a level on ANY attribute read, even .price, so
   the markout recorder's first read of each touch price built every touch order of all 128 books each state.  F2 serves
   .price / .quantity off the wire dict until the level's orders are read, builds Order / Balance / Loan / Fees with an
   exact replica of pydantic's model_construct (_f2_construct) for raw dicts holding the fields under their own names
   (anything else goes to model_construct), and validates LazyBook.events by calling each model's own validator directly
   (model_validate(e) is validate_python(e) with every option at its default).  The F2 strip
   (test_research_v6_23._drop_f2_models) gives back the committed protocol -- pinned below and in t623 / t632 / t633 /
   t634.  pydantic is not installed for this suite: the replica runs here on stand-in models against a reference copy of
   pydantic 2.13's model_construct loop; its equivalence with pydantic itself was measured with the agents' interpreter
   (k106 eqtest: 48,048 adversarial constructs, 30,000 adversarial event dicts, wire states -- 0 mismatches) and on the
   whole agent (the k106 / k109 identity runs: responses and persisted state byte for byte, the switch on and off).
2. agents/strategy/research_direct_idle_gc.py, F2_REQUEST_GC_HOLD.  Automatic collection is also held off for
   generations 0 and 1, from a request's begin to the idle pass after it (which re-enables it); any fallback re-enables.
   No strategy module observes a collection (A1.9.9.2's own guard), so no decision can see it.
3. agents/strategy/research_direct_exit_ledger.py, _F2_LEDGER_INDEX.  live_orders scanned every tracked row per call;
   F2's _F2BookOrders files each row under its book on every write, so live_orders reads one book's rows in the dict's
   own order.  v6.37's _v6_37_orders makes a ledger's dict as the switch stands when the ledger is made.
4. The switch (v6.37).  The miner imports those modules before the agent reads PARAMS, and each switch is read where its
   code runs, so the agent's constructor writes research_v637_fast_path into all three before any request
   (research_v6_37_fast_path.apply) and gives the ledger initialize() made before the switches were read the dict the
   switch selects (ledger_orders).  Off: v6.36's code on every path.  The agent's own two modules must carry their
   switch (apply raises before writing any); the protocol -- taos through the venv's editable install, another tree's
   after a pm2 restart on a host running several -- without it is reported, never fatal (its original code runs).
5. The wiring: Simple minus v6.37 is v6.36 (6171a57) exactly; F2's modules minus F2 (and minus v6.37's factory) are the
   committed ones; nothing else writes a switch.
6. The launcher block.
"""
import ast
import contextlib
import hashlib
import io
import os
import random
import re
import shutil
import subprocess
import sys
import textwrap
import types
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_direct_exit_ledger as led_mod  # noqa: E402
import research_direct_idle_gc as idle  # noqa: E402
import research_v6215_order_life as order_life  # noqa: E402
import research_v6_37_fast_path as fp  # noqa: E402
import test_research_a1_9_9_2_idle_gc as t1992  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
from _harness import extractor  # noqa: E402
import _v638_undo  # noqa: E402
import _v639_undo  # noqa: E402
from _harness import simple_text, read_source, copy_source, strategy_view  # noqa: E402

SIMPLE = simple_text()
MODULE = read_source(STRATEGY / "research_v6_37_fast_path.py")
LAUNCHER = read_source(ROOT / "run_strategy1_research_simple_multi.sh")
MODELS_PATH = ROOT / "taos" / "im" / "protocol" / "models.py"
MODELS = MODELS_PATH.read_text()
GC_MODULE = read_source(STRATEGY / "research_direct_idle_gc.py")
LEDGER_SRC = read_source(STRATEGY / "research_direct_exit_ledger.py")
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
_dbg = extractor(read_source(STRATEGY / "Strategy1_Debug.py"), cls_name="Strategy1_Debug")
V636_COMMIT = "6171a57"                      # v6.36 as committed (its protocol, collector and ledger are a9dd99e's)
SWITCH_LINE = 'self.research_v637_fast_path = self._as_bool(getattr(self.config, "research_v637_fast_path", True))'
APPLY_LINE = "self._v6_37_switches = v6_37_apply(self.research_v637_fast_path)"
LEDGER_LINE = "self._a19_ledger.orders = v6_37_ledger_orders(self._a19_ledger.orders)"
V6_37_IDENTIFIERS = {"research_v637_fast_path"}
SWITCHES = {"_F2_FAST": "taos.im.protocol.models", "F2_REQUEST_GC_HOLD": "research_direct_idle_gc",
            "_F2_LEDGER_INDEX": "research_direct_exit_ledger"}
F2_TOP = {"_F2_FAST", "_F2_LEVEL_RAW", "_f2_aliases", "_f2_construct", "_F2_ORDER_CONSTRUCT", "_F2_BALANCE_CONSTRUCT",
          "_F2_LOAN_CONSTRUCT", "_F2_FEES_CONSTRUCT", "_f2_events", "_f2_account"}
F2_METHODS = {("LazyLevel", "_load"), ("LazyLevel", "__getattr__"), ("LazyBook", "events"), ("LazyAccount", "data")}
# v6.36 = 6171a57: the digest of its Simple's AST (the file's sha256 is 9d3a2663d794..., k103's FROZEN_2) was computed
# once from that file and is embedded here -- per interpreter, since ast.dump's text differs between Python minors (k123:
# 3.10 = the miners' venv, so the launcher gate's interpreter; 3.12 = the host's python3); an interpreter without one fails
V636_AST_SHA256_BY_PY = {(3, 10): "c3ee0de980a8f63d3e51240b50a4f2db7ec72550c8f09ac39248059bea4a24ba",
                         (3, 12): "30ffad432a1acdbabf9f8a6e93c369db2dee939d5201f8785030fc70ddeff22d"}
V636_AST_SHA256 = V636_AST_SHA256_BY_PY.get(tuple(sys.version_info[:2]), "no v6.36 digest for this interpreter")


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
    """Takes out what v6.37 adds and nothing else -- its import, its _v6_37_ functions, statements naming its
    identifiers (an if on one keeps its else) -- keeping every docstring: Simple as v6.36 committed it (6171a57); in
    the exit ledger, v6.37's factory and F2's field naming it."""

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


def _committed(rel, commit=V636_COMMIT):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{commit}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {commit} is not available here")
    return out.stdout


def _top_names(tree):
    out = set()
    for st in tree.body:
        if isinstance(st, ast.FunctionDef):
            out.add(st.name)
        elif isinstance(st, ast.Assign):
            out.update(t.id for t in st.targets if isinstance(t, ast.Name))
    return out


def _fn(source, name, scope):
    ns = dict(scope)
    exec(compile(textwrap.dedent(source), f"<{name}>", "exec"), ns)
    return ns[name]


def _plain(cls_name, method):
    """A method of the protocol's class as a plain function of self (its decorators dropped), with its source."""
    tree = ast.parse(MODELS)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == cls_name)
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == method)
    fn.decorator_list = []
    return ast.unparse(fn)


def _top_source(*names):
    tree = ast.parse(MODELS)
    return "\n\n".join(ast.get_source_segment(MODELS, n) for n in tree.body
                       if isinstance(n, ast.FunctionDef) and n.name in names)


class _Switches:
    """F2's three module switches as this test leaves them: put back on exit, and the protocol module stands in for the
    real one (pydantic is not installed for this suite) -- a module carrying F2's switch, so apply() runs end to end."""
    NAME = "taos.im.protocol.models"

    def __init__(self, protocol=True):
        self.protocol = protocol

    def __enter__(self):
        self.prev = sys.modules.get(self.NAME)
        self.saved = (idle.F2_REQUEST_GC_HOLD, led_mod._F2_LEDGER_INDEX)
        self.module = types.ModuleType(self.NAME)
        if self.protocol:
            self.module._F2_FAST = True
        sys.modules[self.NAME] = self.module
        return self.module

    def __exit__(self, *exc):
        if self.prev is None:
            sys.modules.pop(self.NAME, None)
        else:
            sys.modules[self.NAME] = self.prev
        idle.F2_REQUEST_GC_HOLD, led_mod._F2_LEDGER_INDEX = self.saved
        return False


# ---- 1. the protocol (_F2_FAST) --------------------------------------------------------------------------------------

def test_the_f2_strip_gives_back_the_committed_protocol_and_f2_adds_only_its_names_and_branches():
    old = _committed("taos/im/protocol/models.py")
    tree = ast.parse(MODELS)
    assert ast.dump(t623._drop_f2_models(ast.parse(MODELS))) == ast.dump(ast.parse(old))
    assert ast.dump(tree) != ast.dump(ast.parse(old)) and not _mentions637(tree)          # v6.37 adds nothing here
    assert {n for n in _top_names(tree) if n.lower().startswith("_f2_")} == F2_TOP
    assert not {n for n in _top_names(ast.parse(old)) if n.lower().startswith("_f2_")}
    branched = set()
    for cls in tree.body:
        if isinstance(cls, ast.ClassDef):
            for fn in cls.body:
                if isinstance(fn, ast.FunctionDef) and any(isinstance(n, ast.If) and t623._mentions_f2(n.test)
                                                           for n in ast.walk(fn)):
                    branched.add((cls.name, fn.name))
    assert branched == F2_METHODS
    for good, bad in (('                orders = [Order.model_construct(**o) for o in self._raw.get("o", [])]\n',
                       '                orders = [Order.model_construct(**o) for o in self._raw.get("o", [])][1:]\n'),
                      ('                    parsed_events.append(Order.model_validate(e))\n',
                       '                    parsed_events.append(Order.model_construct(**e))\n')):
        assert MODELS.count(good) == 1, good                         # an edit of an original branch shows in the strip
        assert ast.dump(t623._drop_f2_models(ast.parse(MODELS.replace(good, bad)))) != ast.dump(ast.parse(old)), good


def test_each_module_ships_its_switch_on_and_the_agent_and_params_ship_the_fast_path_on():
    assert "\n_F2_FAST = True\n" in MODELS
    assert "\nF2_REQUEST_GC_HOLD = True\n" in GC_MODULE
    assert "\n_F2_LEDGER_INDEX = True\n" in LEDGER_SRC
    assert idle.F2_REQUEST_GC_HOLD is True and led_mod._F2_LEDGER_INDEX is True
    assert SIMPLE.count(SWITCH_LINE) == 1                                      # the agent's default: on
    assert " research_v637_fast_path=1 " in f" {_params()} "                   # PARAMS: on


class _Field:
    """The parts of pydantic's FieldInfo the replica reads."""

    def __init__(self, alias=None, validation_alias=None, required=True, default=None, factory=None):
        self.alias, self.validation_alias = alias, validation_alias
        self.required, self.default, self.factory = required, default, factory

    def is_required(self):
        return self.required

    def get_default(self, *, call_default_factory=False, validated_data=None):
        return self.factory() if self.factory is not None else self.default


def _reference_construct(cls, values):
    """pydantic 2.13's model_construct core loop (alias, then validation alias, then name, then default)."""
    values = dict(values)
    fields_values, fields_set = {}, set()
    for name, field in cls.__pydantic_fields__.items():
        if field.alias is not None and field.alias in values:
            fields_values[name] = values.pop(field.alias)
            fields_set.add(name)
        if name not in fields_set and field.validation_alias is not None:
            if field.validation_alias in values:
                fields_values[name] = values.pop(field.validation_alias)
                fields_set.add(name)
        if name not in fields_set:
            if name in values:
                fields_values[name] = values.pop(name)
                fields_set.add(name)
            elif not field.is_required():
                fields_values[name] = field.get_default(call_default_factory=True, validated_data=fields_values)
    m = object.__new__(cls)
    object.__setattr__(m, "__dict__", fields_values)
    object.__setattr__(m, "__pydantic_fields_set__", fields_set)
    object.__setattr__(m, "__pydantic_extra__", None)
    object.__setattr__(m, "__pydantic_private__", None)
    return m


def _stand_in(name, fields):
    # pydantic keeps the fields set, extra and private parts in slots, beside __dict__
    cls = type(name, (), {"__slots__": ("__dict__", "__pydantic_fields_set__", "__pydantic_extra__",
                                        "__pydantic_private__")})
    cls.__pydantic_fields__ = fields
    cls.model_config = {"populate_by_name": True}
    cls.__pydantic_post_init__ = None
    cls.__pydantic_root_model__ = False
    calls = []

    def model_construct(_fields_set=None, **values):
        calls.append(values)
        return _reference_construct(cls, values)
    cls.model_construct = staticmethod(model_construct)
    cls.calls = calls
    return cls


def _replica_namespace():
    ns = {}
    exec(compile(_top_source("_f2_aliases", "_f2_construct"), "models.py[F2]", "exec"), ns)
    return ns


def _state(m):
    return (type(m), list(m.__dict__.items()), m.__pydantic_fields_set__, m.__pydantic_extra__, m.__pydantic_private__)


def test_the_construct_replica_matches_model_construct_and_hands_alias_keys_and_non_dicts_to_it():
    ns = _replica_namespace()
    fields = {"y": _Field(required=False, default="o"), "i": _Field(alias="id", validation_alias="id"),
              "c": _Field(alias="client_id", validation_alias="client_id", required=False, default=None),
              "q": _Field(alias="quantity", validation_alias="quantity"),
              "l": _Field(alias="leverage", validation_alias="leverage", required=False, default=0.0),
              "o": _Field(alias="orders", validation_alias="orders", required=False, factory=list)}
    cls = _stand_in("Order", fields)
    build = ns["_f2_construct"](cls)
    rng = random.Random(106)
    values = [0, 7, 2.5, None, "x", True, [1]]
    for _ in range(4000):
        raw = {k: rng.choice(values) for k in ("y", "i", "c", "q", "l", "o", "zz") if rng.random() < 0.8}
        alias = rng.random() < 0.2
        if alias:
            raw[rng.choice(("id", "client_id", "quantity", "orders"))] = rng.choice(values)
        before = len(cls.calls)
        got = build(dict(raw))
        assert _state(got) == _state(_reference_construct(cls, raw)), raw
        assert (len(cls.calls) > before) == (not set(raw) <= set(fields)), raw   # a non-field key: model_construct
    a, b = build({"i": 1, "q": 2.0}), build({"i": 1, "q": 2.0})
    assert a.__dict__["o"] == [] and a.__dict__["o"] is not b.__dict__["o"]       # a default factory runs for each
    assert list(a.__dict__) == ["y", "i", "c", "q", "l", "o"] and a.__pydantic_fields_set__ == {"i", "q"}
    with pytest.raises(TypeError):
        build(None)
    with pytest.raises(TypeError):
        build([("i", 1)])
    with pytest.raises(TypeError):                                                # ** refuses a non-str key
        build({"i": 1, 7: 2})


def test_a_model_the_replica_does_not_cover_goes_to_model_construct():
    ns = _replica_namespace()
    allow = _stand_in("Extra", {"i": _Field()})
    allow.model_config = {"extra": "allow"}
    choices = _stand_in("Choices", {"i": _Field(validation_alias=object())})
    post = _stand_in("Post", {"i": _Field()})
    post.__pydantic_post_init__ = "model_post_init"
    for cls in (allow, choices, post):
        build = ns["_f2_construct"](cls)
        build({"i": 1})
        assert len(cls.calls) == 1, cls.__name__


class _Order:
    def __init__(self, **kw):
        self.kw = kw

    @classmethod
    def model_construct(cls, **kw):
        return ("construct", tuple(sorted(kw.items())))


class _LevelInfo:
    calls = 0

    @classmethod
    def model_construct(cls, p=None, q=None, o=None):
        cls.calls += 1
        return types.SimpleNamespace(p=p, q=q, o=o, price=p, quantity=q, orders=o)


def _lazy_level(fast):
    tree = ast.parse(MODELS)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LazyLevel")
    raw_map = next(n for n in tree.body
                   if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "_F2_LEVEL_RAW")
    ns = {"Sequence": Sequence, "Order": _Order, "LevelInfo": _LevelInfo, "_F2_FAST": fast,
          "_F2_ORDER_CONSTRUCT": lambda raw: ("f2", tuple(sorted(raw.items())))}
    exec(compile(ast.get_source_segment(MODELS, raw_map), "models.py[_F2_LEVEL_RAW]", "exec"), ns)   # the module's map
    exec(compile(textwrap.dedent(ast.get_source_segment(MODELS, cls)), "models.py[LazyLevel]", "exec"), ns)
    return ns["LazyLevel"]


def test_a_level_serves_price_and_quantity_from_the_wire_dict_until_its_orders_are_read():
    raw = {"p": 301.14, "q": 12.5, "o": [{"y": "o", "i": 1, "q": 6.0}, {"y": "o", "i": 2, "q": 6.5}]}
    for fast in (True, False):
        Level = _lazy_level(fast)
        _LevelInfo.calls = 0
        lv = Level(dict(raw))
        assert (lv.price, lv.p, lv.quantity, lv.q) == (301.14, 301.14, 12.5, 12.5)
        assert _LevelInfo.calls == (0 if fast else 1) and (lv._parsed is None) == fast
        orders = lv.orders
        assert _LevelInfo.calls == 1 and lv._raw is None
        assert [o[0] for o in orders] == (["f2", "f2"] if fast else ["construct", "construct"])
        assert [dict(o[1])["i"] for o in orders] == [1, 2]
        assert (lv.price, lv.quantity) == (301.14, 12.5)               # now off the built LevelInfo, the same values
    lv = _lazy_level(True)({"p": 1.0})
    assert lv.price == 1.0 and lv.quantity is None and lv._parsed is None
    assert lv.orders == [] and _LevelInfo.calls == 2


def _event_model(name, log):
    """An event model whose model_validate(e) is its validator's validate_python(e), as pydantic's with the defaults."""
    def validate_python(e):
        log.append((name, "validator"))
        if "bad" in e:
            raise ValueError(name)
        return (name, tuple(sorted(e.items())))

    def model_validate(e):
        log.append((name, "model_validate"))
        if "bad" in e:
            raise ValueError(name)
        return (name, tuple(sorted(e.items())))
    return types.SimpleNamespace(__pydantic_validator__=types.SimpleNamespace(validate_python=validate_python),
                                 model_validate=model_validate)


def _events_of(fast, raw_events):
    log = []
    ns = {"Order": _event_model("Order", log), "TradeInfo": _event_model("TradeInfo", log),
          "Cancellation": _event_model("Cancellation", log), "_F2_FAST": fast,
          "_V638_TRADES_ONLY": False}                  # v6.38 P5 off: F2's loop (tests/test_research_v6_38.py has P5's)
    exec(compile(_top_source("_f2_events"), "models.py[_f2_events]", "exec"), ns)
    events = _fn(_plain("LazyBook", "events"), "events", ns)
    book = types.SimpleNamespace(_raw={"e": raw_events}, _events=None)
    try:
        out = events(book)
    except ValueError as exc:
        return ("raised", str(exc)), log
    assert events(book) is out and book._events is out                              # built once, then kept
    return out, log


def test_the_events_go_through_each_models_own_validator_on_and_model_validate_off_with_the_same_results():
    raw = [{"y": "o", "i": 1}, {"y": "t", "i": 2}, {"y": "c", "i": 3}, {"y": "x", "i": 4}, {"i": 5}, {"y": "o", "i": 6}]
    on, log_on = _events_of(True, [dict(e) for e in raw])
    off, log_off = _events_of(False, [dict(e) for e in raw])
    assert on == off and on[3] == {"y": "x", "i": 4} and on[4] == {"i": 5}       # an unknown kind stays the raw dict
    assert [k for _n, k in log_on] == ["validator"] * 4 and [k for _n, k in log_off] == ["model_validate"] * 4
    assert [n for n, _k in log_on] == [n for n, _k in log_off] == ["Order", "TradeInfo", "Cancellation", "Order"]
    bad = [{"y": "o", "i": 1}, {"y": "t", "bad": 1}]
    assert _events_of(True, bad)[0] == _events_of(False, bad)[0] == ("raised", "TradeInfo")   # the same error
    assert _events_of(True, [])[0] == _events_of(False, [])[0] == []


def _model(name, log, tag):
    def construct(**kw):
        log.append((name, tag))
        return types.SimpleNamespace(kind=name, **kw)
    return construct


def _account_of(fast, raw):
    log = []
    ns = {"_F2_FAST": fast, "OrderCurrency": lambda c: ("currency", c)}
    for name in ("Balance", "Order", "Loan", "Fees"):
        ns[name] = types.SimpleNamespace(model_construct=_model(name, log, "model_construct"))
        ns[f"_F2_{name.upper()}_CONSTRUCT"] = lambda r, name=name: _model(name, log, "replica")(**r)
    ns["Account"] = types.SimpleNamespace(model_construct=lambda **kw: ("Account", kw))
    exec(compile(_top_source("_f2_account"), "models.py[_f2_account]", "exec"), ns)
    data = _fn(_plain("LazyAccount", "data"), "data", ns)
    acc = types.SimpleNamespace(_raw=raw, _parsed=None)
    out = data(acc)
    assert acc._raw is None and data(acc) is out                                    # built once, the raw dict dropped
    return out, log


def test_the_account_builds_its_parts_by_the_replica_on_and_model_construct_off_into_the_same_account():
    raw = {"i": 30, "b": 7, "bb": {"t": 10.0, "f": 2.0}, "qb": {"t": 500.0}, "bl": 1.0, "qc": 3.0,
           "o": [{"i": 1, "q": 1.0}, {"i": 2, "q": 2.0}], "l": {"5": {"c": "BASE", "a": 1.0}}, "f": {"m": 0.001},
           "v": 64}
    on, log_on = _account_of(True, dict(raw))
    off, log_off = _account_of(False, dict(raw))
    assert on == off and on[1]["l"][5].c == ("currency", "BASE") and on[1]["f"].m == 0.001
    assert {t for _n, t in log_on} == {"replica"} and {t for _n, t in log_off} == {"model_construct"}
    assert [n for n, _t in log_on] == [n for n, _t in log_off] == ["Balance", "Balance", "Order", "Order", "Loan",
                                                                    "Fees"]
    bare = {"i": 1}
    assert _account_of(True, dict(bare))[0] == _account_of(False, dict(bare))[0]      # no parts: the same defaults
    assert _account_of(True, dict(bare))[0][1]["f"] is None


# ---- 2. the request window's collection hold (F2_REQUEST_GC_HOLD) ----------------------------------------------------

class _GC(t1992.FakeGC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.enabled, self.log = True, []

    def isenabled(self):
        return self.enabled

    def disable(self):
        self.enabled = False
        self.log.append("disable")

    def enable(self):
        self.enabled = True
        self.log.append("enable")


def test_automatic_collection_is_off_from_the_request_to_its_idle_pass():
    fake, loop = _GC(), t1992.FakeLoop()
    collector = t1992._collector(fake)
    collector.begin_request(loop)
    assert not fake.enabled and collector.f2_held and collector.f2_holds == 1
    collector.end_request(loop)
    assert not fake.enabled                                          # the reply's compression runs without one too
    seen = []
    fake.on_collect = lambda: seen.append(fake.enabled)
    loop.run_due()
    assert seen == [False] and fake.enabled and not collector.f2_held and fake.collect_calls == 1
    assert fake.log == ["disable", "enable"]


def test_a_request_before_the_idle_pass_keeps_the_hold_and_a_fallback_ends_it():
    fake, loop = _GC(), t1992.FakeLoop()
    collector = t1992._collector(fake)
    for _ in range(idle.A1992_FALLBACK_RESPONSES):
        collector.begin_request(loop)
        collector.end_request(loop)
        assert not fake.enabled
    assert collector.f2_holds == 1 and fake.log == ["disable"]
    collector.begin_request(loop)
    collector.end_request(loop)
    assert collector.fallback_reason == idle.FALLBACK_NO_IDLE_GAP and fake.enabled and fake.log == ["disable", "enable"]
    fake, loop = _GC(), t1992.FakeLoop()
    collector = t1992._collector(fake)
    t1992._request(collector, loop)
    fake.fail = True
    loop.run_due()
    assert collector.fallback_reason == idle.FALLBACK_COLLECT_ERROR and fake.enabled


def test_nothing_is_held_without_install_without_the_gc_calls_or_when_collection_was_already_off():
    fake = _GC()
    collector = t1992._collector(fake)
    t1992._request(collector, None)
    assert fake.enabled and fake.log == []
    fake, loop = _GC(), t1992.FakeLoop()
    fake.enabled = False
    collector = t1992._collector(fake)
    t1992._request(collector, loop)
    loop.run_due()
    assert not fake.enabled and fake.log == []                       # someone else's switch is left alone
    fake, loop = t1992.FakeGC(), t1992.FakeLoop()                    # a gc module without isenabled / disable / enable
    collector = t1992._collector(fake)
    t1992._request(collector, loop)
    loop.run_due()
    assert collector.idle_passes == 1 and not collector.f2_held


def test_the_switch_off_holds_nothing_and_runs_a1992s_own_path():
    with _Switches():
        idle.F2_REQUEST_GC_HOLD = False
        fake, loop = _GC(), t1992.FakeLoop()
        collector = t1992._collector(fake)
        t1992._request(collector, loop)
        loop.run_due()
        assert fake.log == [] and fake.enabled and collector.f2_holds == 0 and not collector.f2_held
        assert collector.idle_passes == 1 and fake.collect_calls == 1 and fake.threshold[2] > 10    # A1.9.9.2's pass
        for _ in range(idle.A1992_FALLBACK_RESPONSES + 1):
            collector.begin_request(loop)
            collector.end_request(loop)
        assert collector.fallback_reason == idle.FALLBACK_NO_IDLE_GAP and fake.log == [] and fake.enabled
    assert idle.F2_REQUEST_GC_HOLD is True


def test_the_idle_collector_strip_gives_back_the_committed_module():
    old = _committed("agents/strategy/research_direct_idle_gc.py")
    assert ast.dump(t623._drop_f2_models(_v638_undo.parse(GC_MODULE))) == ast.dump(ast.parse(old))   # k129: v6.39 out
    assert ast.dump(ast.parse(GC_MODULE)) != ast.dump(ast.parse(old)) and not _mentions637(ast.parse(GC_MODULE))
    for good, bad in (("self._gc.set_threshold(saved[0], saved[1], A1992_FULL_THRESHOLD_OFF)",
                       "self._gc.set_threshold(saved[0], 0, A1992_FULL_THRESHOLD_OFF)"),
                      ("        self._f2_release()\n        self._cancel_timer()\n",
                       "        self._cancel_timer()\n")):
        assert GC_MODULE.count(good) == 1, good
        mutated = ast.dump(t623._drop_f2_models(_v638_undo.parse(GC_MODULE.replace(good, bad))))
        assert (mutated != ast.dump(ast.parse(old))) == ("set_threshold" in good), good   # the hold itself is F2's


# ---- 3. the exit ledger's book index (_F2_LEDGER_INDEX) --------------------------------------------------------------

def test_the_ledger_strips_give_back_the_committed_module():
    old = ast.dump(ast.parse(_committed("agents/strategy/research_direct_exit_ledger.py")))
    assert ast.dump(t623._drop_f2_models(_Drop637().visit(ast.parse(LEDGER_SRC)))) == old
    assert ast.dump(_Drop637().visit(t623._drop_f2_models(ast.parse(LEDGER_SRC)))) == old       # either order
    f2_only = t623._drop_f2_models(ast.parse(LEDGER_SRC))
    assert [n.name for n in f2_only.body if isinstance(n, ast.FunctionDef) and n.name.startswith("_v6_37_")] == \
        ["_v6_37_orders"]                                   # v6.37 adds its factory and the field naming it, no more
    fields = [ast.unparse(n) for n in ast.walk(ast.parse(LEDGER_SRC)) if isinstance(n, ast.AnnAssign)
              and getattr(n.target, "id", "") == "orders"]
    assert fields == ["orders: dict[int, LedgerOrder] = field(default_factory=_v6_37_orders)",
                      "orders: dict[int, LedgerOrder] = field(default_factory=dict)"]


def test_a_ledger_takes_the_dict_the_switch_selects_when_it_is_made():
    with _Switches():
        assert type(led_mod.DirectExitLedger().orders) is led_mod._F2BookOrders
        a, b = led_mod._v6_37_orders(), led_mod._v6_37_orders()
        assert a is not b and a == {} and type(a) is led_mod._F2BookOrders
        led_mod._F2_LEDGER_INDEX = False
        assert type(led_mod.DirectExitLedger().orders) is dict and type(led_mod._v6_37_orders()) is dict
        given = {}
        assert led_mod.DirectExitLedger(orders=given).orders is given                  # a dict given is kept as given
    assert type(led_mod.DirectExitLedger().orders) is led_mod._F2BookOrders


def _ledger_snapshot(led, now):
    out = []
    for b in range(8):
        for side in (None, 0, 1):
            for age in (None, 1500.0):
                rows = led.live_orders(b, side=side, max_age_ms=age, now_ns=now if age else None)
                out.append(tuple((r.order_id, r.book_id, r.side, r.placed_ns, r.remaining, r.action) for r in rows))
        out.append(led.live_count(b))
    out.append((led.live_count(), tuple(sorted(led.stats().items()))))
    out.append(tuple((k, v.order_id, v.book_id) for k, v in led.orders.items()))
    return out


def test_the_book_index_answers_exactly_as_the_scan_through_every_kind_of_write():
    for seq in range(60):
        rng = random.Random(seq)
        plain, indexed = led_mod.DirectExitLedger(orders={}), led_mod.DirectExitLedger()
        assert type(indexed.orders) is led_mod._F2BookOrders
        now = 10 ** 12
        for op in range(200):
            now += rng.randint(0, 3 * 10 ** 9)
            oid, book, r = rng.randint(1, 30), rng.randint(0, 7), rng.random()
            if r < 0.5:
                kw = dict(order_id=oid, book_id=book, side=rng.randint(0, 1), price=1.0 + rng.random(),
                          quantity=rng.choice((0.25, 1.0, 0.0)), timestamp_ns=rng.choice((now, 0)), tick=op)
                acts = [lambda led, kw=kw: led.note_accepted(**kw)]
            elif r < 0.75:
                cause = rng.choice((led_mod.LEDGER_REMOVED_CANCELLED, led_mod.LEDGER_REMOVED_FILLED))
                fq = rng.choice((None, 0.1, 5.0))
                acts = [lambda led, oid=oid, cause=cause, fq=fq: led.note_removed(oid, cause=cause, filled_qty=fq)]
            elif r < 0.82:
                t = now - 10 ** 13 if rng.random() < 0.2 else now
                acts = [lambda led, t=t: led.sweep(t)]
            elif r < 0.88:
                acts = [lambda led, t=now: order_life.sweep_with_grace(led, t, 3000.0)]       # pops from outside
            elif r < 0.9:
                acts = [lambda led: led.reset()]
            else:
                row = led_mod.LedgerOrder(order_id=oid, book_id=book, side=0, price=2.0, quantity=1.0, remaining=1.0,
                                          placed_ns=now, placed_tick=op)
                k = rng.randint(0, 4)
                acts = [(lambda led, oid=oid: led.orders.pop(oid, None)),
                        (lambda led, oid=oid, row=row: led.orders.__setitem__(oid, row)),      # may move it to a book
                        (lambda led, oid=oid, row=row: led.orders.setdefault(oid, row)),
                        (lambda led, oid=oid, row=row: led.orders.update({oid: row})),
                        (lambda led: led.orders.popitem() if led.orders else None)][k:k + 1]
            for led in (plain, indexed):
                for act in acts:
                    act(led)
            assert _ledger_snapshot(plain, now) == _ledger_snapshot(indexed, now), (seq, op)


def test_a_row_without_a_book_switches_the_index_off_for_the_scan():
    led = led_mod.DirectExitLedger()
    led.note_accepted(order_id=1, book_id=3, side=0, price=1.0, quantity=1.0, timestamp_ns=5, tick=1)
    assert [r.order_id for r in led.live_orders(3)] == [1]
    led.orders[2] = object()                                                      # no book_id: the scan's own error
    with pytest.raises(AttributeError):
        led.live_orders(3)


def test_the_switch_off_reads_by_the_scan_even_from_an_indexed_dict():
    led = led_mod.DirectExitLedger()
    for oid, book in ((1, 3), (2, 4), (3, 3)):
        led.note_accepted(order_id=oid, book_id=book, side=0, price=1.0, quantity=1.0, timestamp_ns=oid, tick=1)
    led.orders._f2_books[3].pop(3)                               # an index out of step: only the scan sees order 3
    with _Switches():
        assert [r.order_id for r in led.live_orders(3)] == [1]                      # on: the index answers
        led_mod._F2_LEDGER_INDEX = False
        assert [r.order_id for r in led.live_orders(3)] == [3, 1]                   # off: the scan, newest first
    assert [r.order_id for r in led.live_orders(3)] == [1]


# ---- 4. the switch (v6.37) -------------------------------------------------------------------------------------------

def test_the_module_names_f2s_three_switches_and_its_version():
    assert fp.V6_37_FAST_PATH_VERSION == "fast_path_v6_37"
    assert dict((name, mod) for mod, name in fp.SWITCHES) == SWITCHES and len(fp.SWITCHES) == 3
    assert fp.LEDGER_MODULE == "research_direct_exit_ledger"
    assert not re.search(r"(?m)^(?!#)\s*(_F2_FAST|F2_REQUEST_GC_HOLD|_F2_LEDGER_INDEX)\s*=", MODULE)   # setattr only


def test_apply_writes_every_switch_both_ways_and_returns_them_as_held():
    with _Switches() as protocol:
        for on in (False, True, 0, 1.0, "", "x"):
            held = fp.apply(on)
            assert held == {name: bool(on) for name in SWITCHES}
            assert (protocol._F2_FAST, idle.F2_REQUEST_GC_HOLD, led_mod._F2_LEDGER_INDEX) == (bool(on),) * 3
            assert all(type(v) is bool for v in held.values())
    assert idle.F2_REQUEST_GC_HOLD is True and led_mod._F2_LEDGER_INDEX is True


def test_a_strategy_module_without_its_switch_stops_apply_before_anything_is_written():
    with _Switches() as protocol:
        led_mod._F2_LEDGER_INDEX = "on"                                                  # the LAST module lacks it
        with pytest.raises(RuntimeError, match="_F2_LEDGER_INDEX"):
            fp.apply(False)
        assert protocol._F2_FAST is True and idle.F2_REQUEST_GC_HOLD is True            # the first two untouched
    with _Switches() as protocol:
        del idle.F2_REQUEST_GC_HOLD                                                      # restored by _Switches
        with pytest.raises(RuntimeError, match="F2_REQUEST_GC_HOLD"):
            fp.apply(False)
        assert protocol._F2_FAST is True and led_mod._F2_LEDGER_INDEX is True
    assert idle.F2_REQUEST_GC_HOLD is True and led_mod._F2_LEDGER_INDEX is True


def test_a_shared_protocol_without_the_switch_is_reported_and_left_alone():
    """The protocol comes through the venv's editable install (another tree's after a pm2 restart on a host running
    several trees): without F2's switch it runs its original code either way -- reported, never fatal."""
    assert fp.SHARED == frozenset({"taos.im.protocol.models"})
    for setup in ("absent", "not a bool"):
        with _Switches(protocol=setup == "not a bool") as protocol:
            if setup == "not a bool":
                protocol._F2_FAST = 1
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                held = fp.apply(False)
            assert held == {"_F2_FAST": None, "F2_REQUEST_GC_HOLD": False, "_F2_LEDGER_INDEX": False}, setup
            assert getattr(protocol, "_F2_FAST", "absent") == ("absent" if setup == "absent" else 1)   # untouched
            assert out.getvalue().startswith("[v6.37] fast path: taos.im.protocol.models carries no _F2_FAST"), out
    assert idle.F2_REQUEST_GC_HOLD is True and led_mod._F2_LEDGER_INDEX is True


def test_ledger_orders_keeps_every_row_in_order_and_the_dict_itself_when_it_already_fits():
    with _Switches():
        led = led_mod.DirectExitLedger()
        for oid, book in ((9, 3), (4, 5), (7, 3)):
            led.note_accepted(order_id=oid, book_id=book, side=1, price=2.0, quantity=1.0, timestamp_ns=oid, tick=1)
        indexed = led.orders
        assert fp.ledger_orders(indexed) is indexed
        led_mod._F2_LEDGER_INDEX = False
        plain = fp.ledger_orders(indexed)
        assert type(plain) is dict and list(plain.items()) == list(indexed.items())
        assert fp.ledger_orders(plain) is plain
        led_mod._F2_LEDGER_INDEX = True
        back = fp.ledger_orders(plain)
        assert type(back) is led_mod._F2BookOrders and list(back.items()) == list(plain.items())
        assert [r.order_id for r in back._f2_book_rows(3)] == [9, 7]                     # filed in the dict's order


class _Agent:
    """What the v6.37 statements of _init_build_switches read: the config, Strategy1_Debug's real _as_bool and the exit
    ledger initialize() made before them (A1.9.0.1's _a19_reset_exit_observation runs first)."""
    _as_bool = staticmethod(_fn(_dbg("_as_bool"), "_as_bool", {"Any": Any}))

    def __init__(self, config):
        self.config = config
        self._a19_ledger = led_mod.DirectExitLedger()


def _init_source():
    """The v6.37 statements of _init_build_switches -- the real ones -- as one function of self."""
    fn = ast.parse(_src("_init_build_switches")).body[0]
    body = [st for st in fn.body if _mentions637(st)]
    assert [ast.unparse(st) for st in body] == [SWITCH_LINE.replace('"', "'"), APPLY_LINE, LEDGER_LINE]
    return "def _init637(self):\n" + textwrap.indent("\n".join(ast.unparse(st) for st in body), "    ") + "\n"


def _init(config):
    agent = _Agent(config)
    rows = ((11, 3), (12, 7))
    for oid, book in rows:
        agent._a19_ledger.note_accepted(order_id=oid, book_id=book, side=0, price=1.5, quantity=1.0,
                                        timestamp_ns=oid, tick=1)
    before = list(agent._a19_ledger.orders.items())
    _fn(_init_source(), "_init637", {"v6_37_apply": fp.apply, "v6_37_ledger_orders": fp.ledger_orders})(agent)
    assert list(agent._a19_ledger.orders.items()) == before                       # the same rows, in the same order
    return agent


def test_the_init_statements_write_the_switch_into_all_three_and_the_ledger_takes_its_dict_default_on():
    for config, on in ((types.SimpleNamespace(), True), (types.SimpleNamespace(research_v637_fast_path=1.0), True),
                       (types.SimpleNamespace(research_v637_fast_path=0.0), False),
                       (types.SimpleNamespace(research_v637_fast_path="0"), False),
                       (types.SimpleNamespace(research_v637_fast_path="1"), True)):
        with _Switches() as protocol:
            agent = _init(config)
            assert agent.research_v637_fast_path is on
            assert agent._v6_37_switches == {name: on for name in SWITCHES}
            assert (protocol._F2_FAST, idle.F2_REQUEST_GC_HOLD, led_mod._F2_LEDGER_INDEX) == (on, on, on)
            assert type(agent._a19_ledger.orders) is (led_mod._F2BookOrders if on else dict), config
            assert [r.order_id for r in agent._a19_ledger.live_orders(3)] == [11]
            assert type(led_mod.DirectExitLedger().orders) is type(agent._a19_ledger.orders)   # later ledgers too
    assert idle.F2_REQUEST_GC_HOLD is True and led_mod._F2_LEDGER_INDEX is True


def test_the_switch_off_leaves_no_f2_path_reachable():
    """Off: the protocol's lazy wrappers take their original branch (section 1, the exec'd sources with _F2_FAST False),
    the collector holds nothing (section 2) and every ledger -- the one made before the switch and any made after --
    holds the plain dict, read by the scan (section 3)."""
    with _Switches() as protocol:
        agent = _init(types.SimpleNamespace(research_v637_fast_path=0))
        assert protocol._F2_FAST is False and idle.F2_REQUEST_GC_HOLD is False and led_mod._F2_LEDGER_INDEX is False
        assert type(agent._a19_ledger.orders) is dict and type(led_mod.DirectExitLedger().orders) is dict
        assert led_mod._f2_book_rows(agent._a19_ledger.orders, 3) is None            # the index cannot answer a dict
        fake, loop = _GC(), t1992.FakeLoop()
        collector = t1992._collector(fake)
        t1992._request(collector, loop)
        loop.run_due()
        assert fake.log == [] and collector.f2_holds == 0
    assert idle.F2_REQUEST_GC_HOLD is True and led_mod._F2_LEDGER_INDEX is True


# ---- 5. the wiring ---------------------------------------------------------------------------------------------------

def _ast_sha(tree):
    return hashlib.sha256(ast.dump(tree).encode()).hexdigest()


def test_every_v636_statement_is_in_v637_unchanged_and_v637_only_adds():
    stripped = _Drop637().visit(_Drop638().visit(ast.parse(SIMPLE)))            # v6.38 L3 out first
    assert _ast_sha(stripped) == V636_AST_SHA256                                  # v6.36 = v6.37 minus v6.37
    assert _ast_sha(ast.parse(SIMPLE)) != V636_AST_SHA256 and not _mentions637(stripped)
    for good, bad in (("self._research_final_validate_instructions(response, state)\n",
                       "self._research_sanitize_maker_instructions(response, state)\n"),
                      ("self._v6_36_request(state)", "self._v6_36_report()"),
                      ("        self._a19_reset_exit_observation()\n", "        pass\n")):
        assert SIMPLE.count(good) == 1, good
        assert _ast_sha(_Drop637().visit(_Drop638().visit(ast.parse(SIMPLE.replace(good, bad))))) != \
            V636_AST_SHA256, good
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show",
                              f"{V636_COMMIT}:agents/strategy/Strategy1_Research_Simple.py"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        out = None
    if out is not None and out.returncode == 0:
        assert ast.dump(stripped) == ast.dump(ast.parse(out.stdout))


def test_v637_sits_after_v636_in_the_switch_block_and_adds_nothing_to_the_request_path():
    tree = _Drop638().visit(_v638_undo.parse(SIMPLE))                            # v6.37: v6.38, then v6.38 L3 out
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)]
    names = [n.module for n in imports]
    i = names.index("research_v6_37_fast_path")
    assert names[i - 1] == "research_v6_36_step_withdrawal" and names[i + 1] == "research_v64_board"
    assert {(a.name, a.asname) for a in imports[i].names} == {("apply", "v6_37_apply"),
                                                              ("ledger_orders", "v6_37_ledger_orders")}
    init = _src("_init_build_switches")
    assert init.index("self._v6_36_erring = False") < init.index(SWITCH_LINE) < init.index(APPLY_LINE) < \
        init.index(LEDGER_LINE) < init.index("self._v69_counts")
    initialize = _src("initialize")
    assert initialize.index("self._a19_reset_exit_observation()") < initialize.index("self._init_build_switches()")
    assert "self._a19_ledger = DirectExitLedger()" in _src("_a19_reset_exit_observation")     # the ledger before
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    users = sorted(fn.name for fn in cls.body if isinstance(fn, ast.FunctionDef) and _mentions637(fn))
    assert users == ["_init_build_switches"]                    # no method of its own, nothing in respond / handle
    assert not [n for n in tree.body if not isinstance(n, ast.ClassDef) and _mentions637(n)
                and not (isinstance(n, ast.ImportFrom) and n.module == "research_v6_37_fast_path")]


def test_only_the_switch_module_writes_a_switch_and_only_by_its_name_list():
    pat = re.compile(r"(?m)^[^#\n]*\b(_F2_FAST|F2_REQUEST_GC_HOLD|_F2_LEDGER_INDEX)\s*=[^=]")
    found = []
    for path in sorted(list(STRATEGY.glob("*.py")) + list((ROOT / "taos").rglob("*.py"))):
        for m in pat.finditer(path.read_text(errors="replace")):
            found.append((path.name, m.group(1)))
    assert sorted(found) == [("models.py", "_F2_FAST"), ("research_direct_exit_ledger.py", "_F2_LEDGER_INDEX"),
                             ("research_direct_idle_gc.py", "F2_REQUEST_GC_HOLD")]     # each module's own default
    assert MODULE.count("setattr(module, switch, value)") == 1


def test_the_changed_files_are_simple_and_f2s_three_and_the_new_module():
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "diff", "--name-only", V636_COMMIT, "--",
                              "agents/strategy", "taos"], cwd=ROOT, capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V636_COMMIT} is not available here")
    listed = set(out.stdout.split())
    new = {"agents/strategy/research_v6_37_fast_path.py"} | _v638_undo.V638_NEW     # listed once committed (k111 note 2)
    new |= _v638_undo.V638_L3_MODULES                                                # v6.38 L3's, the same (k126)
    v638 = _v638_undo.V638_MODULES | _v638_undo.V638_ARCHIVED                       # v6.38's own (its test pins them)
    v639 = _v639_undo.V639_PATHS - {"agents/strategy/Strategy1_Research_Simple.py",
                                    "agents/strategy/research_direct_idle_gc.py"}   # k129: v6.39's, untracked or tracked
    assert sorted(listed - new - v638 - v639) == ["agents/strategy/Strategy1_Research_Simple.py",
                                           "agents/strategy/research_direct_exit_ledger.py",
                                           "agents/strategy/research_direct_idle_gc.py", "taos/im/protocol/models.py"]
    assert v638 <= listed
    assert (STRATEGY / "research_v6_37_fast_path.py").is_file()                  # + the new module


# ---- 6. the launcher -------------------------------------------------------------------------------------------------

def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start + len('PARAMS="'):LAUNCHER.index('"', start + len('PARAMS="'))].replace("\\\n", " ")


def _block():
    start = LAUNCHER.index('if [[ "${V6_37_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.37 fast path PASS', start)]


FILES = {"Strategy1_Research_Simple.py": "agents/strategy/Strategy1_Research_Simple.py",
         "research_v6_37_fast_path.py": "agents/strategy/research_v6_37_fast_path.py",
         "taos/im/protocol/models.py": "taos/im/protocol/models.py",
         "research_direct_idle_gc.py": "agents/strategy/research_direct_idle_gc.py",
         "research_direct_exit_ledger.py": "agents/strategy/research_direct_exit_ledger.py"}


def _patterns():
    """[(pattern, var, file)] of every list the block greps, in order."""
    lists = re.findall(r"for _v637_pat in \\\n((?:      '[^']+' \\\n)*      '[^']+'); do\n"
                       r"    grep -qF -- \"\$_v637_pat\" \"\$(AGENT_PATH|SCRIPT_DIR)/([^\"]+)\" \|\| \{", _block())
    assert len(lists) == 5, lists
    return [(p, var, name) for body, var, name in lists for p in re.findall(r"'([^']+)'", body)]


def test_the_v637_block_is_gated_preflighted_listed_and_ships_on():
    assert LAUNCHER.count("; V6_34_BUILD=0; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0\n") == 1
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_34_BUILD=1; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_37_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_37_BUILD=") == 2
    assert "      tests/test_research_v6_36.py \\\n      tests/test_research_v6_37.py \\\n" in LAUNCHER
    assert 'research_v636_step_withdrawal=1 \\\nresearch_v637_fast_path=1 \\\n' in LAUNCHER                 # v6.38's next
    assert _params().split()[-9:-6] == ["research_v637_fast_path=1", "research_enable_fill_hazard=0",
                                        "research_v638_fast_path=1"] and len(_params().split()) == 220   # + L3's 5, v6.39's 1
    assert LAUNCHER.index('echo "[preflight] v6.36 step withdrawal PASS') < LAUNCHER.index("# v6.37.") < \
        LAUNCHER.index('echo "[preflight] v6.37 fast path PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    head = LAUNCHER[LAUNCHER.index("# v6.37."):LAUNCHER.index('if [[ "${V6_37_BUILD:-0}" == "1" ]]; then')]
    for needle in ("STRUCTURAL", "OBSERVED", "k106", "_F2_FAST", "F2_REQUEST_GC_HOLD", "_F2_LEDGER_INDEX",
                   "before any request", "pydantic", "ships 1 but may be 0", "=0 / =1", "PARAMS edit", "no rebuild"):
        assert needle in head, needle
    assert LAUNCHER.count("# v6.37.") == 1


def test_every_v637_preflight_pattern_is_in_the_file_it_checks():
    pats = _patterns()
    assert [len([p for p in pats if p[2] == name]) for name in FILES] == [4, 5, 5, 3, 3]
    for needle, var, name in pats:
        assert read_source(ROOT / FILES[name]).count(needle) == 1, (needle, name)
        assert (var == "SCRIPT_DIR") == (name == "taos/im/protocol/models.py"), (needle, var)
    assert {SWITCH_LINE, APPLY_LINE, LEDGER_LINE} <= {p for p, _v, _n in pats}
    assert '[[ -f "$AGENT_PATH/research_v6_37_fast_path.py" ]] || {' in _block()


def _run_block(params, agent_path=strategy_view(), script_dir=ROOT, build="1"):
    start = LAUNCHER.index("# v6.37.")
    stop = LAUNCHER.index('echo "[preflight] v6.37 fast path PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "SCRIPT_DIR": str(script_dir), "V6_37_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v637_block_passes_the_shipped_params_and_refuses_a_build_it_cannot_ship():
    shipped = _params()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "[preflight] v6.37 fast path: on" and len(lines) == 2, out.stdout
    assert lines[1].startswith("[preflight] v6.37 fast path PASS (on: "), out.stdout
    key = "research_v637_fast_path"
    off = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {key}=0 "))
    assert off.returncode == 0 and off.stdout.splitlines()[0] == "[preflight] v6.37 fast path: off"
    for bad in (f"{key}=2", "", f"{key}=true", f"{key}=11", f"{key}=01", f"x{key}=1", f"{key}=-1"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {bad} "))
        assert out.returncode == 1 and f"without {key}=0 or 1" in out.stderr and "PASS" not in out.stdout, bad
    for twice in (f"{key}=1 {key}=1", f"{key}=1 {key}=0"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {twice} "))
        assert out.returncode == 1 and f"{key} given 2 times" in out.stderr, twice
    out = _run_block("", build="0")
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def _tree(tmp):
    (tmp / "agents" / "strategy").mkdir(parents=True)
    (tmp / "taos" / "im" / "protocol").mkdir(parents=True)
    for rel in FILES.values():
        copy_source(ROOT / rel, tmp / rel)
    return tmp


def test_the_v637_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    for i, (needle, _var, name) in enumerate(_patterns()):
        tmp = _tree(tmp_path / str(i))
        path = tmp / FILES[name]
        path.write_text(path.read_text().replace(needle, "pass  # removed", 1))
        out = _run_block(_params(), agent_path=tmp / "agents" / "strategy", script_dir=tmp)
        assert out.returncode == 1 and needle in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
    bare = _tree(tmp_path / "no_module")
    (bare / FILES["research_v6_37_fast_path.py"]).unlink()
    out = _run_block(_params(), agent_path=bare / "agents" / "strategy", script_dir=bare)
    assert out.returncode == 1 and "module is missing" in out.stderr
    whole = _tree(tmp_path / "whole")
    out = _run_block(_params(), agent_path=whole / "agents" / "strategy", script_dir=whole)
    assert out.returncode == 0 and "v6.37 fast path PASS" in out.stdout, out.stderr
