"""v6.38: v6.37 FROZEN_1 + U2 stage A (scratchpad k123) -- cleanup and speed, every decision as v6.37's.

1. Item 1: the three refuted switches are off in source as well (research_v627_balance_gate, research_v63_fee_cap,
   research_v634_class1_entry; PARAMS ships each =0): a launcher that loses a key keeps the feature off.
2. Item 2: names nothing read are gone -- 17 imported names of Simple and 13 never-referenced defs (k112's list,
   re-checked against the whole tree: no reference anywhere, tests / launchers / docs included).
3. Item 3: research_a193_breadth_admission_enabled is retired with its launcher check (no code ever read it from the
   configuration; _a193_enabled() is True either way).
4. Item 4: the 34 never-imported strategy files, the tests that exercise only them and the scripts that only launch them
   are in archive/ (git mv: history kept, contents unchanged).
5. Item 5, research_v638_fast_path (ships 1; 0 = v6.37's code on every path): P5 the protocol validates a book's trade
   events only, P1 the A1.9.0.2 observer stops after its ledger sweep, P3 one trade parse a request for the four walkers
   -- each "if switch: new, else: the original"; research_v6_38_fast_path holds the switch writer and the shared parse.
   Plus P2L: research_enable_fill_hazard=0 (the fill-hazard model feeds telemetry only under the shipped config).
6. The wiring: Simple, the protocol and the exit ledger with v6.38 undone (tests/_v638_undo.py) are v6.37 FROZEN_1's ASTs,
   the launcher with v6.38 undone is v6.37's byte for byte; every older version test compares through that undo.
7. The launcher block.
The identity proof (calm + busy harness runs, every response and session byte-identical to v6.37's with the switch on and
off) is scratchpad k123's; these tests pin the code it proved.
"""
import ast
import contextlib
import hashlib
import io
import os
import random
import re
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6211_score_logic as sl  # noqa: E402
import research_v6214_touch_life as tl  # noqa: E402
import research_v62_making_mirror as mm  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v6_38_fast_path as fp  # noqa: E402
import test_research_v6_37 as t637  # noqa: E402
from _harness import extractor  # noqa: E402
import _v638_undo  # noqa: E402
import _v639_undo  # noqa: E402
from _harness import simple_text, read_source, strategy_view  # noqa: E402

SIMPLE = simple_text()
MODULE = read_source(STRATEGY / "research_v6_38_fast_path.py")
LAUNCHER = read_source(ROOT / "run_strategy1_research_simple_multi.sh")
MODELS = read_source(ROOT / "taos" / "im" / "protocol" / "models.py")
LEDGER_SRC = read_source(STRATEGY / "research_direct_exit_ledger.py")
RESEARCH = read_source(STRATEGY / "Strategy1_Research.py")
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
SWITCH = "research_v638_fast_path"
SWITCH_TEST = 'getattr(self, "research_v638_fast_path", False)'
SWITCH_LINE = 'self.research_v638_fast_path = self._as_bool(getattr(self.config, "research_v638_fast_path", True))'
APPLY_LINE = "self._v6_38_switches = v6_38_apply(self.research_v638_fast_path)"
V637_COMMIT = "6171a57"                      # the commit v6.37 FROZEN_1 was built on (v6.36); v6.37 is not committed
PY = tuple(sys.version_info[:2])
# v6.37 FROZEN_1's files (scratchpad k111/verify/frozen_1.sha256): sha256 of ast.dump of each, per interpreter (k123)
V637_AST = {
    "Simple": {(3, 10): "5a2cd0e4ef913a3da8ddeaa9300e011dd0957791b929603c4702087e36f310fe",
               (3, 12): "921359b0c69f4320e31ab05e13a4f83f943fbd90270effd7871b43b4cc962086"},
    "models": {(3, 10): "7e483ba6a1c29412eb556a43c28b140c86846bdea1a98dacdb748dc228370d05",
               (3, 12): "f6923cc0ba2c581fb3ad270336530dd52d5e239699a848ffa864ff5edb30cafe"},
    "ledger": {(3, 10): "2cd4be01f6eb1162178140851bd67177a59298ca7d2d0090e7102b7958758623",
               (3, 12): "cf5a67ef10d74a3dc8792276b00d83f99d56fb57294ebca730a08dab535db830"},
}
V637_LAUNCHER_SHA256 = "f62b9454fa958087345a93a33180f066b6e8b8834fde24e38810e8e6c326b964"
# k126 (the merged v6.38 = this layer 2 + v6.38 layer 3, scratchpad k124 / k126): v6.37 + layer 3 = k124's Dev_v6_38_l3
# launcher (its L3_FROZEN entry) and this layer 2 alone = k123's FROZEN_1 launcher (k123 verify/frozen_1.sha256)
V637_L3_LAUNCHER_SHA256 = "c11a588fd7530428cf31577de017da49f38472055da28413cf8f3ab70f26a398"
V638_A_LAUNCHER_SHA256 = "b358c9813ae939bec2a5be52e6fa53e8f7b4fd9a48988f677e6f0121aa2dd07e"
# k126 M-FROZEN_2 = M-FROZEN_1 + ONE default flip in PARAMS (research_v638_lean=1 + research_v638_lean_arm=3: CC on the
# k116 hash half) and its note line in layer 3's block header; with both undone the launcher is M-FROZEN_1's
M2_FLIP = (("research_v638_lean=0 \\\n", "research_v638_lean=1 \\\n"),
           ("research_v638_lean_arm=0 \\\n", "research_v638_lean_arm=3 \\\n"))
M2_NOTE = ("# M-FROZEN_2 (k126, 10-22 JST): ONE default flipped since -- research_v638_lean=1 + research_v638_lean_arm=3: CC (the\n"
           "# front-built lean) on the k116 hash half of the class-0 books; every other layer-3 switch still ships 0.\n")
M1_LAUNCHER_SHA256 = "a289b652e6ba99f8a565f8733e6c2c433d9c630383b22fe2e61ea34119f65b32"


def _l3_out(tree):
    """k126: v6.38 layer 3 taken out of a tree v6.38's undo has run on (t637's _Drop638: no layer-2 name is left)."""
    return t637._Drop638().visit(tree)


def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start + len('PARAMS="'):LAUNCHER.index('"\n', start + len('PARAMS="'))].replace("\\\n", " ")


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _cls(tree, name="Strategy1_Research_Simple"):
    return next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == name)


def _method(tree, name, cls="Strategy1_Research_Simple"):
    return next(n for n in _cls(tree, cls).body if isinstance(n, ast.FunctionDef) and n.name == name)


# ---- 1. the module ---------------------------------------------------------------------------------------------------

def test_the_module_names_its_version_its_one_switch_and_its_helpers():
    assert fp.V6_38_FAST_PATH_VERSION == "fast_path_v6_38"
    assert fp.SWITCHES == (("taos.im.protocol.models", "_V638_TRADES_ONLY"),) and fp.SHARED == {"taos.im.protocol.models"}
    for name in ("apply", "trades_reset", "trades_of", "feed", "last_taker_side"):
        assert callable(getattr(fp, name)), name
    assert fp.trade_dict is mm.trade_dict and fp._v6214_last_taker_side is tl.last_taker_side


@contextlib.contextmanager
def _protocol(module):
    name = "taos.im.protocol.models"
    prev = sys.modules.get(name)
    sys.modules[name] = module
    try:
        yield module
    finally:
        if prev is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prev


def test_apply_writes_the_protocols_switch_both_ways_and_reports_a_protocol_without_it():
    with _protocol(types.ModuleType("taos.im.protocol.models")) as fake:
        fake._V638_TRADES_ONLY = False
        assert fp.apply(True) == {"_V638_TRADES_ONLY": True} and fake._V638_TRADES_ONLY is True
        assert fp.apply(0) == {"_V638_TRADES_ONLY": False} and fake._V638_TRADES_ONLY is False
        assert fp.apply("1") == {"_V638_TRADES_ONLY": True}
    with _protocol(types.ModuleType("taos.im.protocol.models")) as bare:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            assert fp.apply(True) == {"_V638_TRADES_ONLY": None}
        assert "carries no _V638_TRADES_ONLY" in out.getvalue() and not hasattr(bare, "_V638_TRADES_ONLY")
    assert MODULE.count("setattr(module, switch, value)") == 1


def _event(rng):
    kind = rng.random()
    if kind < 0.35:
        return {"y": "t", "i": rng.randint(1, 9), "s": rng.choice([0, 1]), "t": rng.randint(1, 9),
                "q": rng.choice([0.5, 1.0, 2.0, 0.0, -1.0, "x", None]), "p": rng.choice([10.0, 10.5, 9.75, float("inf")]),
                "Ma": rng.choice([5, -3, None, 7]), "Ta": rng.choice([6, 9, None])}
    if kind < 0.5:
        return types.SimpleNamespace(y="t", p=rng.choice([10.0, 11.0]), q=rng.choice([1.0, 0.25]), s=rng.choice([0, 1]),
                                     Ma=rng.choice([5, None]), Ta=6)
    if kind < 0.75:
        return {"y": "o", "i": rng.randint(1, 9), "p": 10.0, "q": 1.0, "s": 0}
    if kind < 0.9:
        return {"y": "c", "i": rng.randint(1, 9), "p": 10.0, "q": 0.5}
    return rng.choice([{"y": "x"}, {"p": 10.0, "q": 1.0, "s": 1}, types.SimpleNamespace(type="o", p=10.0)])


def _walker(parse, events):
    return [t for t in (parse(ev) for ev in (events or ())) if t is not None]


def test_trades_of_is_each_walkers_own_parse_once_per_list_a_request():
    rng = random.Random(638)
    for _ in range(300):
        events = [_event(rng) for _ in range(rng.randint(0, 25))]
        fp.trades_reset()
        got = fp.trades_of(events)
        assert got == _walker(mm.trade_dict, events) == _walker(sl._trade, events) == _walker(dl.trade_of, events)
        assert fp.trades_of(events) is got or not events                     # parsed once, then the same list
        assert fp.trades_of(list(events)) == got                              # an equal list: parsed again, equal
        for t in got:                                                         # the mirrors re-read the parsed dicts
            assert mm.trade_dict(t) == t and sl._trade(t) == t
    assert fp.trades_of(None) == [] and fp.trades_of([]) == []
    events = [{"y": "t", "s": 1, "p": 10.0, "q": 1.0}]
    first = fp.trades_of(events)
    fp.trades_reset()
    assert fp.trades_of(events) is not first and fp.trades_of(events) == first and not fp._TRADES.get(id([]))


def test_the_cache_holds_its_lists_and_stays_bounded():
    fp.trades_reset()
    lists = [[{"y": "t", "s": 0, "p": 10.0, "q": float(k + 1)}] for k in range(fp._MAX_LISTS + 5)]
    for events in lists:
        fp.trades_of(events)
    assert len(fp._TRADES) <= fp._MAX_LISTS and all(hit[0] is lists_ for lists_, hit in
                                                    ((ev, fp._TRADES[id(ev)]) for ev in lists if id(ev) in fp._TRADES))
    fp.trades_reset()
    assert fp._TRADES == {}


def test_a_book_with_an_event_the_parser_raises_on_feeds_the_mirror_its_raw_events_and_caches_nothing():
    fp.trades_reset()
    bad = [{"y": "t", "s": 0, "p": 10.0, "q": 1.0, "Ma": 5}, {"y": "t", "s": 1, "p": 10.0, "q": 1.0, "Ma": "x"}]
    with pytest.raises(ValueError):
        mm.trade_dict(bad[1])                                                 # the walkers' own loops raise here
    with pytest.raises(ValueError):
        fp.trades_of(bad)
    assert fp.feed(bad) is bad and id(bad) not in fp._TRADES
    good = [{"y": "t", "s": 0, "p": 10.0, "q": 1.0}]
    assert fp.feed(good) is fp.trades_of(good)


def test_last_taker_side_is_v6214s_on_every_input():
    rng = random.Random(6214)
    for _ in range(400):
        events = [_event(rng) for _ in range(rng.randint(0, 20))]
        if rng.random() < 0.2:
            events.insert(rng.randint(0, len(events)), {"y": "t", "s": 1, "p": 10.0, "q": 1.0, "Ta": "bad"})
        fp.trades_reset()
        try:
            want = ("value", tl.last_taker_side(events))
        except Exception as exc:                                              # noqa: BLE001
            want = ("raised", type(exc))
        try:
            got = ("value", fp.last_taker_side(events))
        except Exception as exc:                                              # noqa: BLE001
            got = ("raised", type(exc))
        assert got == want, events
        assert fp.last_taker_side(tuple(events)) == want[1] if want[0] == "value" else True
        gen_a, gen_b = iter(list(events)), iter(list(events))
        try:
            want_gen = tl.last_taker_side(gen_a)
        except Exception as exc:                                              # noqa: BLE001
            want_gen = type(exc)
        try:
            got_gen = fp.last_taker_side(gen_b)
        except Exception as exc:                                              # noqa: BLE001
            got_gen = type(exc)
        assert got_gen == want_gen
    assert fp.last_taker_side(None) is None and fp.last_taker_side([]) is None


# ---- 2. P5: the protocol ---------------------------------------------------------------------------------------------

def _models_top(*names):
    tree = ast.parse(MODELS)
    return "\n\n".join(ast.get_source_segment(MODELS, n) for n in tree.body
                       if isinstance(n, ast.FunctionDef) and n.name in names)


def test_the_protocol_ships_p5_off_and_only_apply_writes_it():
    assert MODELS.count("\n_V638_TRADES_ONLY = False\n") == 1
    pat = re.compile(r"(?m)^[^#\n]*\b_V638_TRADES_ONLY\s*=[^=]")
    found = []
    for path in sorted(list(STRATEGY.glob("*.py")) + list((ROOT / "taos").rglob("*.py"))):
        found += [path.name for _m in pat.finditer(path.read_text(errors="replace"))]
    assert found == ["models.py"]


def test_p5_validates_the_trades_only_by_the_switch_off_paths_call_and_keeps_every_other_event_its_dict():
    for fast in (True, False):
        log = []
        ns = {"Order": t637._event_model("Order", log), "TradeInfo": t637._event_model("TradeInfo", log),
              "Cancellation": t637._event_model("Cancellation", log), "_F2_FAST": fast}
        exec(compile(_models_top("_v638_events", "_v638_all_events", "_f2_events"), "models.py[v6.38]", "exec"), ns)
        raw = [{"y": "o", "i": 1}, {"y": "t", "i": 2}, {"y": "c", "i": 3}, {"y": "x", "i": 4}, {"i": 5},
               {"y": "t", "i": 6}]
        log.clear()
        out = ns["_v638_events"](raw)
        assert [type(e).__name__ for e in out] == ["dict", "tuple", "dict", "dict", "dict", "tuple"]
        assert all(out[k] is raw[k] for k in (0, 2, 3, 4))                    # the wire dicts themselves
        assert log == [("TradeInfo", "validator" if fast else "model_validate")] * 2
        log.clear()
        full = ns["_v638_all_events"](raw)                                    # the switch-off list
        assert [e[0] if isinstance(e, tuple) else "dict" for e in full] == ["Order", "TradeInfo", "Cancellation",
                                                                            "dict", "dict", "TradeInfo"]
        assert [out[1], out[5]] == [full[1], full[5]]                         # the same trade objects
        assert log == [(n, "validator" if fast else "model_validate")
                       for n in ("Order", "TradeInfo", "Cancellation", "TradeInfo")]


class _StandInBook:
    """Book's three readers and model_construct as the protocol defines them (pydantic is not installed for this suite)."""
    trades = property(lambda self: {t.timestamp: t for t in self.events if t.type == 't'})
    orders = property(lambda self: {o.timestamp: o for o in self.events if o.type == 'o'})
    cancellations = property(lambda self: {c.timestamp: c for c in self.events if c.type == 'c'})

    @staticmethod
    def model_construct(**kw):
        return ("Book", tuple(sorted((k, repr(v)) for k, v in kw.items())))


class _Ev:
    """A stand-in event model object: its kind, timestamp and the wire dict it was built from."""

    def __init__(self, kind, e):
        self.type, self.timestamp, self.raw = kind, e.get("t"), e

    def __eq__(self, other):
        return isinstance(other, _Ev) and (self.type, self.raw) == (other.type, other.raw)

    def __repr__(self):
        return "Ev(%s,%r)" % (self.type, self.raw)


def _lazy_book(on, raw_events):
    """LazyBook's v6.38 methods (events, _v638_full, trades, orders, cancellations, parse) on stand-in models."""
    Ev = _Ev

    def model(kind):
        return types.SimpleNamespace(model_validate=lambda e: Ev(kind, e),
                                     __pydantic_validator__=types.SimpleNamespace(validate_python=lambda e: Ev(kind, e)))
    ns = {"Order": model("o"), "TradeInfo": model("t"), "Cancellation": model("c"), "Book": _StandInBook,
          "_StandInBook": _StandInBook,
          "_F2_FAST": True, "_V638_TRADES_ONLY": on}
    exec(compile(_models_top("_v638_events", "_v638_all_events", "_f2_events"), "models.py[v6.38]", "exec"), ns)
    tree = ast.parse(MODELS)
    lazy = _cls(tree, "LazyBook")
    body = []
    for fn in lazy.body:
        if isinstance(fn, ast.FunctionDef) and fn.name in ("events", "_v638_full", "trades", "orders", "cancellations",
                                                           "_v638_parse", "parse"):
            body.append(ast.unparse(fn))
    exec(compile("class _LB(_StandInBook):\n" + textwrap.indent("\n\n".join(body), "    ") + "\n", "<LazyBook>", "exec"),
         ns)
    book = ns["_LB"].__new__(ns["_LB"])
    book._raw, book._events = {"i": 3, "e": raw_events, "f": None}, None
    book.bids = book.asks = types.SimpleNamespace(parse=lambda: ())
    return book, ns


def test_lazybook_on_validates_trades_only_and_its_order_readers_see_every_event_as_with_the_switch_off():
    raw = [{"y": "o", "t": 1}, {"y": "t", "t": 2}, {"y": "c", "t": 3}, {"y": "t", "t": 4}]
    on, _ns_on = _lazy_book(True, raw)
    off, _ns_off = _lazy_book(False, raw)
    assert [type(e).__name__ for e in on.events] == ["dict", "_Ev", "dict", "_Ev"] and on.events is on.events
    assert [e.type for e in off.events] == ["o", "t", "c", "t"]
    for reader in ("trades", "orders", "cancellations"):
        assert getattr(on, reader) == getattr(off, reader), reader
    assert on.parse() == off.parse()
    assert on.__dict__["_v638_all"] is on._v638_full()                        # built once, then kept
    assert "_v638_all" not in off.__dict__                                    # off: never built


def test_with_pydantic_the_real_protocol_agrees_on_and_off():
    try:
        import pydantic  # noqa: F401
    except ImportError:
        pytest.skip("pydantic is not installed for this interpreter")
    try:
        import taos.im.protocol.models as models
    except Exception:                                                         # noqa: BLE001
        pytest.skip("the protocol does not import here")
    rng = random.Random(5)
    saved = models._V638_TRADES_ONLY
    try:
        for _ in range(40):
            raw = {"i": 1, "b": [], "a": [], "e": []}
            t = 1_000
            for k in range(rng.randint(0, 30)):
                t += rng.randint(1, 5)
                kind = rng.choice("otc")
                if kind == "o":
                    raw["e"].append({"y": "o", "i": k, "c": None, "t": t, "q": 1.0, "s": rng.choice([0, 1]),
                                     "p": 10.0 + rng.randint(0, 5) / 100, "l": 0.0})
                elif kind == "t":
                    raw["e"].append({"y": "t", "i": k, "s": rng.choice([0, 1]), "t": t, "q": 0.5, "p": 10.0,
                                     "Ti": k + 100, "Ta": 6, "Tf": 0.0, "Mi": k + 200, "Ma": rng.choice([5, -3]),
                                     "Mf": 0.0})
                else:
                    raw["e"].append({"y": "c", "i": k, "t": t, "p": 10.0, "q": 0.5, "s": 0, "l": 0.0})
            models._V638_TRADES_ONLY = False
            off = models.LazyBook(raw)
            ev_off = off.events                                               # built (lazily) with the switch off
            models._V638_TRADES_ONLY = True
            on = models.LazyBook(raw)
            ev_on = on.events
            assert [e.model_dump() for e in ev_on if not isinstance(e, dict)] == \
                [e.model_dump() for e in ev_off if e.type == "t"]
            assert [e for e in ev_on if isinstance(e, dict)] == [e2 for e2 in raw["e"] if e2["y"] != "t"]
            assert [mm.trade_dict(e) for e in ev_on] == [mm.trade_dict(e) for e in ev_off]
            assert [getattr(e, "type", None) in ("t", "EVENT_TRADE", "ET") for e in ev_on] == \
                [getattr(e, "type", None) in ("t", "EVENT_TRADE", "ET") for e in ev_off]
            for reader in ("trades", "orders", "cancellations"):
                models._V638_TRADES_ONLY = True
                a = {k: v.model_dump() for k, v in getattr(on, reader).items()}
                models._V638_TRADES_ONLY = False
                b = {k: v.model_dump() for k, v in getattr(off, reader).items()}
                assert a == b, reader
            models._V638_TRADES_ONLY = True
            pa = on.parse()
            models._V638_TRADES_ONLY = False
            pb = off.parse()
            assert [e.model_dump() for e in pa.e] == [e.model_dump() for e in pb.e]
    finally:
        models._V638_TRADES_ONLY = saved


# every file outside the protocol whose code reads a book's events, and how (k123's P5 audit; a new reader must be audited)
EVENT_READERS = {
    "agents/strategy/DetailedTemplateAgent.py": 4,      # trade counters: getattr(e, "type", None) in trade kinds (+ a docstring)
    "agents/strategy/Strategy1.py": 1,                  # trade-sign history: getattr(event, "type", None)
    "agents/strategy/Strategy1_Research_Simple.py": 10, # the walkers (trade parsers read "y"), v6.32 / v6.34 print filters
    "agents/strategy/research_v62_making_mirror.py": 1, # its docstring
    "agents/strategy/top_miner_monitor.py": 1,          # not imported by the agent (Strategy1.respond, gated off)
    "taos/im/agents/__init__.py": 2,                    # StateHistoryManager: the HybridTrainingAgent only
    "taos/im/agents/gentrx.py": 4,                      # GenTRXAgent: not this agent (+ a docstring)
}


def test_every_reader_of_book_events_is_the_audited_one():
    pat = re.compile(r"""(\bbook\.events\b|getattr\(book, ["']events["']|\.books\[[^]]+\]\.events\b)""")
    seen = {}
    for path in sorted(list(STRATEGY.glob("*.py")) + list((ROOT / "taos").rglob("*.py"))):
        rel = path.relative_to(ROOT).as_posix()
        if rel == "taos/im/protocol/models.py":
            continue
        n = len(pat.findall(read_source(path) if path.name == "Strategy1_Research_Simple.py"
                            else path.read_text(errors="replace")))                 # v6.39 S4: the joined text
        if n:
            seen[rel] = n
    assert seen == EVENT_READERS, seen


# ---- 3. P1: the A1.9.0.2 observer ------------------------------------------------------------------------------------

def test_p1_returns_right_after_the_ledger_sweep_and_the_pass_counter():
    fn = _method(ast.parse(SIMPLE), "_a19_observe_tick_resting_exits")
    body = fn.body[1:]                                                        # the docstring out
    texts = [ast.unparse(st) for st in body[:6]]
    assert texts[0] == "ledger = self._a19_ledger_ref()"
    assert texts[2].startswith("if now_ns and now_ns != int(getattr(self, '_a19_last_state_ns', 0) or 0):")
    assert "ledger.sweep(now_ns)" in texts[2]
    assert texts[3] == "tick = int(getattr(self, '_tick', 0) or 0) + 1" and texts[4] == "self._a19_tick_passes += 1"
    gate = body[5]
    assert isinstance(gate, ast.If) and ast.unparse(gate.test) == SWITCH_TEST.replace('"', "'")
    assert [ast.unparse(s) for s in gate.body] == ["return"] and gate.orelse == []
    assert ast.unparse(body[6]) == "books = getattr(state, 'books', None) or {}"


class _Touched(Exception):
    pass


class _Books(dict):
    def __bool__(self):
        raise _Touched("books read")


def _observer(on):
    calls = []
    ns = {}
    exec(compile(textwrap.dedent(_src("_a19_observe_tick_resting_exits")), "<observer>", "exec"), ns)
    agent = types.SimpleNamespace(_a19_last_state_ns=0, _tick=4, _a19_tick_passes=0,
                                  _a19_ledger_ref=lambda: types.SimpleNamespace(sweep=lambda ns_: calls.append(ns_)))
    if on is not None:
        agent.research_v638_fast_path = on
    state = types.SimpleNamespace(timestamp=7_000, books=_Books({1: object()}))
    return ns["_a19_observe_tick_resting_exits"], agent, state, calls


def test_p1_on_skips_the_per_book_pass_and_off_runs_it():
    fn, agent, state, calls = _observer(True)
    assert fn(agent, state) is None
    assert calls == [7_000] and agent._a19_tick_passes == 1 and agent._a19_last_state_ns == 7_000
    for on in (False, None):                                                  # off, or an agent without the switch
        fn, agent, state, calls = _observer(on)
        with pytest.raises(_Touched):
            fn(agent, state)
        assert calls == [7_000] and agent._a19_tick_passes == 1


# ---- 4. P3: the four walkers' sites ----------------------------------------------------------------------------------

def _switch_ifs(tree):
    out = []
    for fn in _cls(tree).body:
        if isinstance(fn, ast.FunctionDef):
            for node in ast.walk(fn):
                if isinstance(node, ast.If) and ast.unparse(node.test) == SWITCH_TEST.replace('"', "'"):
                    out.append((fn.name, node))
    return out


def test_the_switch_is_read_at_six_sites_each_new_code_then_the_original():
    sites = _switch_ifs(ast.parse(SIMPLE))
    assert sorted(name for name, _n in sites) == sorted(["update", "_a19_observe_tick_resting_exits", "_v62_feed_mirror",
                                                         "_v6211_feed_mirror", "_v6214_note_prints", "_v63_pass"])
    shape = {name: ([ast.unparse(s) for s in n.body], [ast.unparse(s) for s in n.orelse]) for name, n in sites}
    assert shape["update"] == (["v6_38_trades_reset()"], [])
    assert shape["_a19_observe_tick_resting_exits"] == (["return"], [])
    for name in ("_v62_feed_mirror", "_v6211_feed_mirror"):
        new, old = shape[name]
        assert len(new) == len(old) == 1 and new[0] == old[0].replace("getattr(book, 'events', None))",
                                                                       "v6_38_feed(getattr(book, 'events', None)))")
    assert shape["_v6214_note_prints"] == (["side = v6_38_last_taker_side(getattr(book, 'events', None))"],
                                           ["side = v6214_last_taker_side(getattr(book, 'events', None))"])
    assert shape["_v63_pass"] == (["trades = list(v6_38_trades_of(getattr(book, 'events', None)))"],
                                  ["trades = [t for t in (v633_trade_of(ev) for ev in getattr(book, 'events', None) or ())"
                                   " if t is not None]"])
    assert SIMPLE.count(f"if {SWITCH_TEST}:") == 6
    update = _method(ast.parse(SIMPLE), "update")
    assert isinstance(update.body[0], ast.If) and ast.unparse(update.body[0].test) == SWITCH_TEST.replace('"', "'")


def _state(rng, books=6):
    out = {}
    for b in range(books):
        events = []
        for _ in range(rng.randint(0, 12)):
            if rng.random() < 0.4:
                events.append({"y": "t", "s": rng.choice([0, 1]), "p": round(10 + rng.random(), 2),
                               "q": rng.choice([0.5, 1.0]), "Ma": rng.choice([30, 31, -3]), "Ta": rng.choice([30, 9])})
            else:
                events.append({"y": rng.choice("oc"), "p": 10.0, "q": 1.0})
        out[b] = types.SimpleNamespace(events=events)
    return out


def test_the_mirrors_fed_the_shared_parse_hold_exactly_what_they_hold_fed_the_raw_events():
    rng = random.Random(62)
    feeds = {}
    for name in ("_v62_feed_mirror", "_v6211_feed_mirror"):
        ns = {"V62MakingMirror": mm.MakingMirror, "V6211OwnAlphaMirror": sl.OwnAlphaMirror,
              "V62_DEFAULT_LOOKBACK_NS": mm.DEFAULT_LOOKBACK_NS, "V632_SAMPLE_NS": 1_000_000_000,
              "V632_PRUNE_EVERY_NS": 60_000_000_000, "v6_38_feed": fp.feed}
        exec(compile(textwrap.dedent(_src(name)), f"<{name}>", "exec"), ns)
        feeds[name] = ns[name]
    for on_name, flag in (("_v62_feed_mirror", "research_v62_making_mirror"),
                          ("_v6211_feed_mirror", "research_v6211_alpha_mirror")):
        agents = []
        for on in (True, False):
            a = types.SimpleNamespace(uid=30, research_kappa_lookback_ns=0, research_v632_score_062=True,
                                      research_v638_fast_path=on)
            setattr(a, flag, True)
            setattr(a, "_v62_on", lambda: True)
            agents.append(a)
        ts = 1_000_000_000_000
        for k in range(60):
            ts += 1_000_000_000
            books = _state(rng)
            if k % 15 == 14:                                  # a print the parser raises on, after good ones
                books[2].events = [{"y": "t", "s": 0, "p": 10.0, "q": 1.0, "Ma": 30, "Ta": 9},
                                   {"y": "t", "s": 1, "p": 10.5, "q": 1.0, "Ma": 9, "Ta": 30},
                                   {"y": "t", "s": 1, "p": 10.5, "q": 1.0, "Ma": "x", "Ta": 30}]
            fp.trades_reset()
            raised = []
            for a in agents:
                try:
                    feeds[on_name](a, types.SimpleNamespace(timestamp=ts, books=books))
                    raised.append(None)
                except ValueError as exc:                         # the mirror's own loop meets it, on and off
                    raised.append(str(exc))
            assert raised[0] == raised[1] and (raised[0] is not None) == (k % 15 == 14), (on_name, k)
        mirror_attr = "_v62_mirror" if on_name == "_v62_feed_mirror" else "_v6211_mirror"
        m_on, m_off = (getattr(a, mirror_attr, None) for a in agents)
        assert m_on is not None and m_off is not None, on_name
        assert {k: v for k, v in vars(m_on).items() if not k.endswith("_ms")} == \
            {k: v for k, v in vars(m_off).items() if not k.endswith("_ms")}, on_name      # all but its timers


def test_the_v6214_side_memo_is_the_same_on_and_off():
    rng = random.Random(14)
    ns = {"v6214_last_taker_side": tl.last_taker_side, "v6_38_last_taker_side": fp.last_taker_side}
    exec(compile(textwrap.dedent(_src("_v6214_note_prints")), "<v6214>", "exec"), ns)
    for _ in range(200):
        books = _state(rng, books=3)
        memos = []
        for on in (True, False):
            a = types.SimpleNamespace(research_v638_fast_path=on, _v6214_last_taker=None)
            fp.trades_reset()
            for b, book in books.items():
                ns["_v6214_note_prints"](a, b, book)
            memos.append(a._v6214_last_taker)
        assert memos[0] == memos[1]


# ---- 5. the wiring: v6.38 undone is v6.37 FROZEN_1 --------------------------------------------------------------------

def test_simple_the_protocol_and_the_ledger_with_v638_undone_are_v637s():
    for name, text in (("Simple", SIMPLE), ("models", MODELS), ("ledger", LEDGER_SRC)):
        undone = _v638_undo.parse(text)                                   # k126: Simple's layer 3 out as well
        digest = hashlib.sha256(ast.dump(_l3_out(undone) if name == "Simple" else undone).encode()).hexdigest()
        assert digest == V637_AST[name].get(PY, "no v6.37 digest for this interpreter"), name
        assert digest != hashlib.sha256(ast.dump(ast.parse(text)).encode()).hexdigest(), name     # it changes them
        tree = _v638_undo.parse(text)
        assert ast.dump(_v638_undo.undo(tree)) == ast.dump(_v638_undo.parse(text)), name         # idempotent
    for good, bad in (("self._a19_tick_passes += 1\n", "self._a19_tick_passes += 2\n"),
                      ('getattr(self.config, "research_v63_fee_cap", False)', 'getattr(self.config, "research_v63_fee_cap", 0)'),
                      ("trades = list(v6_38_trades_of(getattr(book, \"events\", None)))     # v6.38 P3: the one parse",
                       "trades = list(v6_38_trades_of(getattr(book, \"events\", None)))[:1]")):
        assert SIMPLE.count(good) == 1, good
        mutated = hashlib.sha256(ast.dump(_l3_out(_v638_undo.parse(SIMPLE.replace(good, bad)))).encode()).hexdigest()
        if "v6_38_trades_of" in good:                       # inside a switch branch: the undo drops it (the on path)
            assert mutated == V637_AST["Simple"].get(PY), good
        else:
            assert mutated != V637_AST["Simple"].get(PY), good


def test_the_launcher_with_v638_undone_is_v637s_byte_for_byte():
    launcher = _v639_undo.launcher_v638(LAUNCHER)                       # k129: v6.39's edits out first
    for off, on in M2_FLIP:                    # k129: v6.39 ships CC OFF (the user, 10-23): M-FROZEN_1's values, no note
        assert launcher.count(off) == 1 and launcher.count(on) == 0, on
    assert launcher.count(M2_NOTE) == 0
    assert _sha(launcher) == M1_LAUNCHER_SHA256                          # M-FROZEN_1's launcher, byte for byte
    layer3 = _v638_undo.V638_L3_HEAD in launcher                         # k126: the merged build carries layer 3 too
    assert _sha(_v638_undo.launcher_v637(launcher)) == (V637_L3_LAUNCHER_SHA256 if layer3 else V637_LAUNCHER_SHA256)
    with pytest.raises(AssertionError):
        _v638_undo.launcher_v637(_v638_undo.launcher_v637(launcher))     # applies to v6.38's text only
    if layer3:                       # layer 3's three launcher edits out (its block, its five keys, its gate entry)
        text = launcher.replace(launcher[launcher.index("# v6.38 L3.  "):
                                         launcher.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')], "", 1)
        tail = ("research_v638_fast_path=1 \\\nresearch_v638_kbl=0 \\\nresearch_v638_lean=0 \\\n"
                "research_v638_lean_arm=0 \\\nresearch_v638_c1=0 \\\nresearch_v638_c1_arm=0\"\n")
        assert text.count(tail) == 1 and text.count("      tests/test_research_v6_38_l3.py \\\n") == 1
        text = text.replace(tail, 'research_v638_fast_path=1"\n').replace("      tests/test_research_v6_38_l3.py \\\n", "")
        assert _sha(text) == V638_A_LAUNCHER_SHA256                      # k123's v6.38-A launcher, byte for byte
        assert _sha(_v638_undo.launcher_v637(text)) == V637_LAUNCHER_SHA256


def test_v638_imports_its_module_after_v637s_and_writes_the_switch_after_v637s_ledger_line():
    tree = ast.parse(SIMPLE)
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)
               and n.module not in t637.V6_38_MODULES + (_v639_undo.V639_MODULE,)]   # k126: layer 3's four out; k129: v6.39's
    names = [n.module for n in imports]
    i = names.index("research_v6_38_fast_path")
    assert names[i - 1] == "research_v6_37_fast_path" and names[i + 1] == "research_v64_board"
    assert {(a.name, a.asname) for a in imports[i].names} == {("apply", "v6_38_apply"), ("feed", "v6_38_feed"),
                                                              ("last_taker_side", "v6_38_last_taker_side"),
                                                              ("trades_of", "v6_38_trades_of"),
                                                              ("trades_reset", "v6_38_trades_reset")}
    init = _src("_init_build_switches")
    assert init.count(SWITCH_LINE) == 1 and init.count(APPLY_LINE) == 1
    assert init.index(t637.LEDGER_LINE) < init.index(SWITCH_LINE) < init.index(APPLY_LINE) < \
        init.index("self._v69_counts")


def test_the_changed_paths_are_f2s_and_v638s_own():
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "diff", "--name-only", V637_COMMIT, "--",
                              "agents/strategy", "taos"], cwd=ROOT, capture_output=True, text=True, timeout=120,
                             env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:                                                         # noqa: BLE001
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V637_COMMIT} is not available here")
    listed = set(out.stdout.split())
    new = {"agents/strategy/research_v6_37_fast_path.py"} | _v638_undo.V638_NEW
    new |= _v638_undo.V638_L3_MODULES                  # k126: v6.38 layer 3's four modules, listed once committed
    f2 = {"agents/strategy/Strategy1_Research_Simple.py", "agents/strategy/research_direct_exit_ledger.py",
          "agents/strategy/research_direct_idle_gc.py", "taos/im/protocol/models.py"}
    v639 = _v639_undo.V639_PATHS                       # k129: v6.39's paths, untracked or tracked (its test pins them)
    assert listed - new - v639 == (f2 | _v638_undo.V638_MODULES | _v638_undo.V638_ARCHIVED) - v639
    assert (STRATEGY / "research_v6_38_fast_path.py").is_file()


# ---- 6. items 1-4 ----------------------------------------------------------------------------------------------------

def test_item1_the_refuted_switches_are_off_in_source_and_ship_off():
    init = _src("_init_build_switches")
    for key in ("research_v627_balance_gate", "research_v63_fee_cap", "research_v634_class1_entry"):
        assert re.search(r'getattr\(self\.config, "%s", False\)' % key, init), key
        assert not re.search(r'getattr\(self(\.config)?, "%s", True\)' % key, SIMPLE), key
        assert f" {key}=0 " in f" {_params()} " and _params().count(f"{key}=") == 1, key
    assert 'research_v627_balance_gate", False)' in _src("_v627_balance_gate_on")


# item 2: (module, qualified name, the pattern a reference would match)
REMOVED_IMPORTS = {"maker_realization_cost_estimate", "effective_total_open_books", "ACTION_PARK_EXIT",
                   "worst_case_abs_inventory", "SEED_LEGACY_DUST", "legacy_dust_ceiling_bonus", "V61_REWRITE_IDLE",
                   "V61_REWRITE_TAKER", "V625_REASON_BAND", "V626_BALANCED_MAKER_VERSION", "V626_REASON_SIDE_OWNED",
                   "v626_balance_ratio", "V627_MAKER_CEILING_VERSION", "v627_book_balance", "V610_BEHIND_MAX_CLIPS_OFF",
                   "V612_REDUCE_DEPTH_FACTOR", "V612_REDUCE_DEPTH_VERSION"}
REMOVED_DEFS = (
    ("Strategy1_Research_Simple.py", "Strategy1_Research_Simple._a19_order_id", r"\b_a19_order_id\b"),
    ("Strategy1_Research_Simple.py", "Strategy1_Research_Simple._a19_order_price", r"\b_a19_order_price\b"),
    ("Strategy1_Research_Simple.py", "Strategy1_Research_Simple._direct_instruction_signed_qty",
     r"\b_direct_instruction_signed_qty\b"),
    ("research_clean_authority.py", "is_completion_requote_candidate", r"\bis_completion_requote_candidate\b"),
    ("research_direct_exit_ledger.py", "DirectExitLedger.note_action", r"\bnote_action\b"),
    ("research_kappa_realization.py", "KappaRealizationBoost.allow_pressure", r"\ballow_pressure\b"),
    ("research_kappa_state.py", "rolling_observation_counts", r"\brolling_observation_counts\b"),
    ("research_kappa_state.py", "kappa_expiry_state", r"\bkappa_expiry_state\b"),
    ("research_rolling_economics.py", "RollingBookEconomics.pnl_ready", r"\bpnl_ready\b"),
    ("research_rt_phase_timing.py", "RoundTripPhaseState.forget_book", r"\bforget_book\b"),
    ("DetailedTemplateAgent.py", "DetailedTemplateAgent.estimate_realized_pnl", r"\bestimate_realized_pnl\b"),
    ("research_projected_completion.py", "ProjectedCompletion.uncertain", r"\.uncertain\b"),
    ("research_quote_hysteresis.py", "imbalance_reversed", r"\bimbalance_reversed\b"),
)


def _defs(path):
    tree = ast.parse(path.read_text())
    out = set()
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            out.add(node.name)
            if isinstance(node, ast.ClassDef):
                out |= {f"{node.name}.{n.name}" for n in node.body if isinstance(n, ast.FunctionDef)}
    return out


def _tree_texts():
    """Every file of the tree a reference could sit in: code, tests, launchers, docs -- archive/ and the frozen __ver_*
    snapshots aside (copies of other objects), and the two files that name the removed names on purpose (this test and
    tests/_v638_undo.py, which puts their v6.37 source back for the older tests)."""
    skip = {Path(__file__).resolve(), (ROOT / "tests" / "_v638_undo.py").resolve()}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in (".git", "archive", "__pycache__") and not d.startswith("__ver_")]
        for f in filenames:
            p = Path(dirpath) / f
            if p.resolve() in skip or p.suffix in (".pyc", ".png", ".gz", ".zst"):
                continue
            try:
                yield p, p.read_text(errors="replace")
            except OSError:
                continue


def test_item2_the_removed_names_are_gone_and_named_nowhere():
    tree = ast.parse(SIMPLE)
    bound = {a.asname or a.name for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names}
    assert not REMOVED_IMPORTS & bound
    for name in REMOVED_IMPORTS:                                              # Simple names none of them any more
        assert not re.search(r"\b%s\b" % name, SIMPLE.replace("research_v612_reduce_depth.REDUCE_DEPTH_FACTOR", "")) or \
            name in ("maker_realization_cost_estimate",), name
    texts = list(_tree_texts())
    for fname, qual, pat in REMOVED_DEFS:
        assert qual not in _defs(STRATEGY / fname), qual
        hits = [p.relative_to(ROOT).as_posix() for p, text in texts if re.search(pat, text)]
        assert hits == [], (qual, hits)


def test_item3_the_a193_key_is_retired_and_its_reader_stays_true():
    assert "research_a193_breadth_admission_enabled" not in _params()
    assert '*"research_a193_breadth_admission_enabled=1"*' not in LAUNCHER
    assert "the research_a193_breadth_admission_enabled PARAMS check is retired with its key" in LAUNCHER
    ns = {}
    exec(compile(textwrap.dedent(_src("_a193_enabled")), "<a193>", "exec"), ns)
    assert ns["_a193_enabled"](types.SimpleNamespace()) is True
    assert not re.search(r"getattr\(self\.config, \"research_a193_breadth_admission_enabled\"", SIMPLE)


ARCHIVED_TESTS = ("test_adaptive_drift", "test_adaptive_ev", "test_adaptive_hjb", "test_adaptive_persistence",
                  "test_adaptive_session_reset", "test_adverse", "test_base_candidate_screen_bench",
                  "test_candidate_screen", "test_entry_size", "test_execution_hazard", "test_execution_lifecycle",
                  "test_quote_hysteresis", "test_realization", "test_regime_v2", "test_score_ev",
                  "test_adaptive_v3_rebase", "test_base_candidate_screen", "test_base_champion_contract",
                  "test_base_champion_verify", "test_base_dynamic_import", "test_base_execution_model",
                  "test_base_fill_ofi_size", "test_base_quote_policy", "test_base_realization", "test_base_regime_v2",
                  "test_base_score_ev", "test_phase3_inheritance", "test_v4138_base_adaptive_promotion")
ARCHIVED_SCRIPTS = ("run_adaptive_agent.sh", "run_adaptive_agent_multi.sh", "run_base_strategy.sh",
                    "run_base_strategy_multi.sh", "run_strategy3.sh", "run_strategy3_test.sh", "run_strategy4.sh",
                    "run_strategy4_test.sh", "run_strategy5.sh", "run_strategy5_test.sh", "run_strategy6.sh",
                    "run_strategy6_test.sh", "updated_run_adaptive_agent.sh", "updated_run_base_strategy.sh")
ARCHIVED = sorted(_v638_undo.V638_ARCHIVED | {f"tests/{t}.py" for t in ARCHIVED_TESTS} | set(ARCHIVED_SCRIPTS))


def test_item4_the_archived_files_moved_unchanged_and_nothing_live_reaches_them():
    assert len(ARCHIVED) == 83
    for rel in ARCHIVED:
        assert not (ROOT / rel).exists() and (ROOT / "archive" / rel).is_file(), rel
    assert {"SimpleRegressorAgent.py", "HybridTrainingAgent.py"} <= {p.name for p in STRATEGY.glob("*.py")}
    archived_modules = {Path(r).stem for r in ARCHIVED if r.startswith("agents/strategy/") and r.endswith(".py")}
    assert len(archived_modules) == 34
    live = [p for p in STRATEGY.glob("*.py")] + list((ROOT / "taos").rglob("*.py"))
    for path in live:
        for node in ast.walk(ast.parse(path.read_text(errors="replace"))):
            mods = [a.name for a in node.names] if isinstance(node, ast.Import) else \
                [node.module] if isinstance(node, ast.ImportFrom) and node.module else []
            assert not {m.split(".")[0] for m in mods} & archived_modules, (path.name, mods)
    for t in ARCHIVED_TESTS:
        assert f"tests/{t}.py" not in LAUNCHER, t
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "ls-tree", "-r", V637_COMMIT, "--"] + ARCHIVED, cwd=ROOT,
                             capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:                                                         # noqa: BLE001
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V637_COMMIT} is not available here")
    blobs = {line.split("\t")[1]: line.split()[2] for line in out.stdout.splitlines()}
    assert sorted(blobs) == ARCHIVED
    for rel, blob in blobs.items():
        data = (ROOT / "archive" / rel).read_bytes()
        assert hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest() == blob, rel     # git's own blob id


# ---- 7. P2L and the launcher -----------------------------------------------------------------------------------------

def test_p2l_ships_the_fill_hazard_off_and_its_decision_readers_are_the_audited_dead_ones():
    assert " research_enable_fill_hazard=0 " in f" {_params()} " and _params().count("research_enable_fill_hazard=") == 1
    tree = ast.parse(RESEARCH)
    readers = sorted({fn.name for fn in ast.walk(tree) if isinstance(fn, ast.FunctionDef)
                      and any(isinstance(n, ast.Constant) and n.value == "research_enable_fill_hazard"
                              for n in ast.walk(fn))} - {"initialize"})
    # telemetry: registration's predict (QUOTE / EXEC_PROB / FILL fields), the quote end's observe (FILL_CAL); decision
    # readers: estimate_fill_probability and _research_actual_exit_hazard, whose callers below run under v6.2 / v6.3 off
    assert readers == ["_research_actual_exit_hazard", "_research_observe_quote_end",
                       "_research_register_submitted_quotes", "estimate_fill_probability"]
    callers = {}
    for path, text in (("Strategy1_Research.py", RESEARCH), ("Strategy1_Research_Simple.py", SIMPLE)):
        for fn in ast.walk(ast.parse(text)):
            if isinstance(fn, ast.FunctionDef):
                for n in ast.walk(fn):
                    if isinstance(n, ast.Attribute) and n.attr in ("estimate_fill_probability",
                                                                   "_research_actual_exit_hazard"):
                        callers.setdefault(n.attr, set()).add(f"{path}:{fn.name}")
    assert callers == {"estimate_fill_probability": {"Strategy1_Research.py:_place_skewed_quotes",
                                                     "Strategy1_Research.py:estimate_fill_probability",
                                                     "Strategy1_Research_Simple.py:_place_skewed_quotes",
                                                     "Strategy1_Research_Simple.py:build_mm_strategy_instructions"},
                       "_research_actual_exit_hazard": {"Strategy1_Research.py:_research_apply_unified_exit"}}
    build = _src("build_mm_strategy_instructions")
    v62 = build.index("        if self._v62_on():\n")
    assert build.index("self._v63_pass(response, state, stats)") > v62 and \
        build.index("fill_est = self.estimate_fill_probability(") > build.index("        else:\n", v62)


def _block():
    start = LAUNCHER.index('if [[ "${V6_38_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.38 fast path PASS', start)]


def test_the_v638_block_is_gated_preflighted_listed_and_ships_on():
    assert LAUNCHER.count("; V6_36_BUILD=0; V6_37_BUILD=0; V6_38_BUILD=0\n") == 1
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_36_BUILD=1; V6_37_BUILD=1; V6_38_BUILD=1"), arm
    # k126: layer 3's "# v6.38 L3." block reads the same flag (its own test pins it); its five keys follow this one's
    assert LAUNCHER.count('if [[ "${V6_38_BUILD:-0}" == "1" ]]; then') == 3 and LAUNCHER.count("V6_38_BUILD=") == 2  # k129: + v6.39
    assert LAUNCHER.index("# v6.38.") < LAUNCHER.index('if [[ "${V6_38_BUILD:-0}" == "1" ]]; then') < \
        LAUNCHER.index("# v6.38 L3.")
    assert "      tests/test_research_v6_37.py \\\n      tests/test_research_v6_38.py \\\n" in LAUNCHER
    assert 'research_v637_fast_path=1 \\\nresearch_enable_fill_hazard=0 \\\nresearch_v638_fast_path=1 \\\n' in LAUNCHER
    assert _params().split()[-7] == "research_v638_fast_path=1" and len(_params().split()) == 220   # k129: + v6.39's key
    assert LAUNCHER.index('echo "[preflight] v6.37 fast path PASS') < LAUNCHER.index("# v6.38.") < \
        LAUNCHER.index('echo "[preflight] v6.38 fast path PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    head = LAUNCHER[LAUNCHER.index("# v6.38."):LAUNCHER.index('if [[ "${V6_38_BUILD:-0}" == "1" ]]; then')]
    for needle in ("STRUCTURAL", "OBSERVED", "k115", "k123", "_V638_TRADES_ONLY", "before any request",
                   "ships 1 but may be 0", "=0 / =1", "PARAMS edit", "no rebuild", "research_enable_fill_hazard=0"):
        assert needle in head, needle
    assert LAUNCHER.count("# v6.38.") == 1


def _patterns():
    lists = re.findall(r"for _v638_pat in \\\n((?:      '[^']+' \\\n)*      '[^']+'); do\n"
                       r"    grep -qF -- \"\$_v638_pat\" \"\$(AGENT_PATH|SCRIPT_DIR)/([^\"]+)\" \|\| \{", _block())
    assert len(lists) == 3, lists
    return [(p, var, name) for body, var, name in lists for p in re.findall(r"'([^']+)'", body)]


FILES = {"Strategy1_Research_Simple.py": "agents/strategy/Strategy1_Research_Simple.py",
         "research_v6_38_fast_path.py": "agents/strategy/research_v6_38_fast_path.py",
         "taos/im/protocol/models.py": "taos/im/protocol/models.py"}


def test_every_v638_preflight_pattern_is_in_the_file_it_checks():
    pats = _patterns()
    assert [len([p for p in pats if p[2] == name]) for name in FILES] == [6, 5, 4]
    for needle, _var, name in pats:
        assert read_source(ROOT / FILES[name]).count(needle) >= 1, needle
    block = _block()
    assert block.count('"$_v638_n" == "2"') == 1 and SIMPLE.count(
        '((int(raw_id), v6_38_feed(getattr(book, "events", None))) for raw_id, book in books.items()),') == 2
    assert block.count('"$_v638_n" == "6"') == 1 and SIMPLE.count(f"if {SWITCH_TEST}:") == 6


def _run_block(params, *, agent_path=strategy_view(), script_dir=ROOT, build="1"):
    start = LAUNCHER.index('if [[ "${V6_38_BUILD:-0}" == "1" ]]; then')
    text = LAUNCHER[start:LAUNCHER.index("\nfi\n", start) + len("\nfi\n")]
    script = 'set -euo pipefail\nAGENT_PATH="$1"\nSCRIPT_DIR="$2"\nPARAMS="$3"\nV6_38_BUILD="$4"\n' + text
    return subprocess.run(["bash", "-c", script, "v638", str(agent_path), str(script_dir), params, build],
                          capture_output=True, text=True, timeout=120)


def test_the_v638_block_passes_the_shipped_params_and_refuses_a_build_it_cannot_ship():
    shipped = _params()
    out = _run_block(shipped)
    assert out.returncode == 0, out.stderr
    assert "[preflight] v6.38 fast path: on\n" in out.stdout and "[preflight] v6.38 fill hazard: off\n" in out.stdout
    assert "[preflight] v6.38 fast path PASS (on:" in out.stdout
    out = _run_block(shipped.replace("research_v638_fast_path=1", "research_v638_fast_path=0"))
    assert out.returncode == 0 and "[preflight] v6.38 fast path: off\n" in out.stdout
    out = _run_block(shipped.replace("research_enable_fill_hazard=0", "research_enable_fill_hazard=1"))
    assert out.returncode == 0 and "[preflight] v6.38 fill hazard: on\n" in out.stdout
    for bad in (shipped.replace(" research_v638_fast_path=1", ""), shipped + " research_v638_fast_path=1",
                shipped.replace("research_v638_fast_path=1", "research_v638_fast_path=2"),
                shipped.replace(" research_enable_fill_hazard=0", ""), shipped + " research_enable_fill_hazard=0"):
        out = _run_block(bad)
        assert out.returncode == 1 and "ERROR: v6.38" in out.stderr, bad[-80:]
    assert _run_block(" research_v638_fast_path=7 ", build="0").returncode == 0      # not a v6.38 build: no check


def test_the_v638_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    shipped = _params()
    whole = tmp_path / "whole"
    for rel in FILES.values():
        (whole / rel).parent.mkdir(parents=True, exist_ok=True)
        (whole / rel).write_text(read_source(ROOT / rel))
    assert _run_block(shipped, agent_path=whole / "agents" / "strategy", script_dir=whole).returncode == 0
    for needle, _var, name in _patterns():
        tree = tmp_path / ("t%d" % abs(hash(needle)))
        for rel in FILES.values():
            (tree / rel).parent.mkdir(parents=True, exist_ok=True)
            text = read_source(ROOT / rel)
            (tree / rel).write_text(text.replace(needle, "") if rel == FILES[name] else text)
        out = _run_block(shipped, agent_path=tree / "agents" / "strategy", script_dir=tree)
        assert out.returncode == 1 and "ERROR: v6.38" in out.stderr, needle
    bare = tmp_path / "bare"
    for rel in FILES.values():
        if rel != FILES["research_v6_38_fast_path.py"]:
            (bare / rel).parent.mkdir(parents=True, exist_ok=True)
            (bare / rel).write_text(read_source(ROOT / rel))
    out = _run_block(shipped, agent_path=bare / "agents" / "strategy", script_dir=bare)
    assert out.returncode == 1 and "the fast-path module is missing" in out.stderr
    feed = '((int(raw_id), v6_38_feed(getattr(book, "events", None))) for raw_id, book in books.items()),'
    for old, new, why in ((feed, '((int(raw_id), getattr(book, "events", None)) for raw_id, book in books.items()),',
                           "mirrors do not both read"),
                          (f"if {SWITCH_TEST}:\n            v6_38_trades_reset()", "if True:\n            v6_38_trades_reset()",
                           "reads its switch at 5 sites")):
        one = tmp_path / ("one%d" % len(why))
        for rel in FILES.values():
            (one / rel).parent.mkdir(parents=True, exist_ok=True)
            text = read_source(ROOT / rel)
            (one / rel).write_text(text.replace(old, new, 1) if rel == FILES["Strategy1_Research_Simple.py"] else text)
        out = _run_block(shipped, agent_path=one / "agents" / "strategy", script_dir=one)
        assert out.returncode == 1 and why in out.stderr, (why, out.stderr)
