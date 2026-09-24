"""v6.3.1 S2: under v6.3 the request no longer computes what only the retired entry/exit pipeline reads.

Live UID 94 on v6.3.0 (RESPOND_TIMING p50, ticks 200-1,656): full_predict 42.8 ms for ~109 candidate books, ranking
9.0 ms, the regime classifier ~0.9 ms, and a second quote-store registration of the same response 9.8 ms, out of a
~260 ms handler.  v6.3's pass reads none of it; the frozen acquisition branch and the inventory/exit loop that do
read it do not act under v6.3.  The fast screen stays (the dust normalizer's admission reads its census).
"""
import ast
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v631_lean_handler as lh  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
BASE_S1 = (STRATEGY / "Strategy1.py").read_text()
TEMPLATE = (STRATEGY / "DetailedTemplateAgent.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")

METHODS = ("_v631_lean_on", "_v631_count", "_v631_snapshot", "_predict_all_books",
           "classify_market_regime_from_profiles", "_research_register_submitted_quotes")


class _Base:
    """What ``super()`` resolves to: the frozen pipeline, recorded."""

    def __init__(self, *, v63=True, lean=True, screen=None, screen_error=False):
        self.research_v631_lean_handler = lean
        self._v63 = v63
        self._screen = screen if screen is not None else types.SimpleNamespace(
            selected=[1, 2, 3], forced_inventory=[1, 2], forced_kappa=[3])
        self._screen_error = screen_error
        self._research_timing = {"full_predict_ms": 0.0, "screen_ms": 0.0}
        self._research_market_regime = "NORMAL"
        self.calls = []

    def _v63_on(self):
        return self._v63

    def _research_fast_screen(self, state):
        self.calls.append("screen")
        if self._screen_error:
            raise RuntimeError("screen")
        self._research_last_screen = self._screen
        return self._screen

    def _predict_all_books(self, state):
        self.calls.append("full_predict")
        return {1: "forecast"}

    def classify_market_regime_from_profiles(self, profiles, predictions, selection):
        self.calls.append("classify")
        self._research_market_regime = "TOXIC"
        return "REAL_REGIME"

    def _research_register_submitted_quotes(self, response, state):
        self.calls.append(("register", id(response)))


def _agent(**kw):
    body = "".join("    " + line + "\n" for name in METHODS for line in _simple(name).splitlines())
    scope = {"_Base": _Base, "time": __import__("time"), "Any": object,
             "V631_LEAN_HANDLER_VERSION": lh.V631_LEAN_HANDLER_VERSION,
             "v631_idle_regime_fields": lh.idle_regime_fields, "v631_empty_selection_fields": lh.empty_selection_fields,
             "MarketRegime": lambda **kw: types.SimpleNamespace(**kw),
             "BookSelection": lambda **kw: types.SimpleNamespace(**kw)}
    exec("from __future__ import annotations\nclass Harness(_Base):\n" + body, scope)
    return scope["Harness"](**kw)


def _state(n=3):
    return types.SimpleNamespace(books={i: object() for i in range(1, n + 1)}, timestamp=1)


# ---- 1. the pure parts ---------------------------------------------------------------------------------------------

def _dataclass_fields(name):
    cls = next(n for n in ast.walk(ast.parse(TEMPLATE)) if isinstance(n, ast.ClassDef) and n.name == name)
    return [n.target.id for n in cls.body if isinstance(n, ast.AnnAssign)]


def test_the_field_sets_are_exactly_the_frozen_dataclasses():
    assert list(lh.empty_selection_fields()) == _dataclass_fields("BookSelection")
    assert list(lh.idle_regime_fields()) == _dataclass_fields("MarketRegime")


def test_the_empty_selection_and_the_idle_regime_carry_nothing():
    sel = lh.empty_selection_fields()
    assert sel["alpha_books"] == [] and sel["maintenance_books"] == [] and sel["profiles"] == []
    assert lh.empty_selection_fields()["profiles"] is not sel["profiles"]                  # fresh lists each call
    reg = lh.idle_regime_fields()
    assert reg["mode"] == "QUIET" and reg["scoring_overlay"] is None and reg["book_count"] == 0


def test_the_idle_mode_is_a_key_of_the_frozen_regime_parameter_table():
    assert f'"{lh.IDLE_REGIME_MODE}": RegimeParamSet(' in BASE_S1


# ---- 2. predict: the screen only -----------------------------------------------------------------------------------

def test_lean_predict_runs_the_screen_and_no_forecast():
    agent = _agent()
    assert agent._predict_all_books(_state()) == {}
    assert agent.calls == ["screen"] and agent._last_predictions == {}
    t = agent._research_timing
    assert t["full_predict_ms"] == 0.0 and t["screen_fallback"] == 0 and t["screen_ms"] >= 0.0
    assert t["candidate_count"] == 3 and t["forced_inventory_count"] == 2 and t["forced_kappa_count"] == 1
    assert agent._v631_counts == {"predict_skipped": 1}


def test_a_screen_error_never_falls_back_to_the_128_book_forecast():
    agent = _agent(screen_error=True)
    assert agent._predict_all_books(_state()) == {}
    assert agent.calls == ["screen"] and agent._v631_counts["screen_errors"] == 1


def test_no_books_no_screen():
    agent = _agent()
    assert agent._predict_all_books(types.SimpleNamespace(books={})) == {} and agent.calls == []


@pytest.mark.parametrize("v63,lean", [(True, False), (False, True), (False, False)])
def test_off_or_without_v63_the_frozen_predict_runs(v63, lean):
    agent = _agent(v63=v63, lean=lean)
    assert agent._predict_all_books(_state()) == {1: "forecast"} and agent.calls == ["full_predict"]


# ---- 3. regime and selection -----------------------------------------------------------------------------------------

def test_lean_regime_is_one_fixed_idle_object_and_touches_no_research_label():
    agent = _agent()
    first = agent.classify_market_regime_from_profiles([], {}, None)
    second = agent.classify_market_regime_from_profiles([], {}, None)
    assert first is second and first.mode == "QUIET" and agent._last_regime is first
    assert agent.calls == [] and agent._research_market_regime == "NORMAL"
    assert agent._v631_counts["regime_skipped"] == 2
    off = _agent(lean=False)
    assert off.classify_market_regime_from_profiles([], {}, None) == "REAL_REGIME"


def test_lean_selection_returns_before_any_profile_is_built():
    src = _simple("select_books_for_trading")
    body = ast.parse(src).body[0].body
    guard = body[1] if isinstance(body[0], ast.Expr) else body[0]           # after the docstring
    assert isinstance(guard, ast.If) and ast.unparse(guard.test) == "self._v631_lean_on()"
    assert isinstance(guard.body[-1], ast.Return) and "BookSelection(**v631_empty_selection_fields())" in ast.unparse(guard)


# ---- 4. one registration per response ------------------------------------------------------------------------------

def test_a_response_is_registered_once_and_the_next_one_again():
    agent = _agent()
    r1, r2 = object(), object()
    agent._research_register_submitted_quotes(r1, None)
    agent._research_register_submitted_quotes(r1, None)                 # Strategy1_Research.respond, same object
    agent._research_register_submitted_quotes(r2, None)
    assert agent.calls == [("register", id(r1)), ("register", id(r2))]
    assert agent._v631_counts["registrations_deduped"] == 1
    off = _agent(lean=False)
    off._research_register_submitted_quotes(r1, None); off._research_register_submitted_quotes(r1, None)
    assert len(off.calls) == 2


# ---- 5. why skipping is safe: under v6.3 nothing that acts reads predictions, profiles or the regime -----------------

def _build_mm():
    return ast.parse(_simple("build_mm_strategy_instructions")).body[0]


def _parents(root):
    out = {}
    for node in ast.walk(root):
        for child in ast.iter_child_nodes(node):
            out[child] = node
    return out


def _retired(node, parents):
    """Inside the frozen inventory/exit loop (empty under v6.3) or the non-v6.2 acquisition branch."""
    while node in parents:
        parent = parents[node]
        if isinstance(parent, ast.For) and "{} if self._v63_on() else" in ast.unparse(parent.iter):
            return True
        if isinstance(parent, ast.For) and ast.unparse(parent.iter).startswith("manage_queue["):
            return True                         # filled only inside the loop above (asserted below)
        if isinstance(parent, ast.If) and ast.unparse(parent.test) == "self._v62_on()" and node in parent.orelse:
            return True
        node = parent
    return False


def test_build_mm_reads_predictions_profiles_and_regime_params_only_in_retired_branches():
    fn = _build_mm(); parents = _parents(fn)
    reads = []
    for node in ast.walk(fn):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load) and node.id in (
                "predictions", "profile_by_id", "regime_params", "selection", "regime"):
            reads.append(node)
    assert reads
    allowed_outside = {
        # the audit copy, the selection's profiles (empty), the regime params lookup, the shortlist fallback that
        # v6.2 overwrites with every book, and the stats label
        "self._research_last_predictions = predictions", "self._research_last_selection = selection",
        "profile_by_id = {int(p.book_id): p for p in getattr(selection, 'profiles', None) or []}",
        "regime_params = self.get_regime_params(regime)",
        "selected_ids = {int(x) for x in (predictions or {}).keys()}",
    }
    outside = set()
    for node in reads:
        if _retired(node, parents):
            continue
        stmt = node
        while stmt in parents and not isinstance(stmt, ast.stmt):
            stmt = parents[stmt]
        outside.add(ast.unparse(stmt).split("\n")[0])
    unexpected = {s for s in outside if s not in allowed_outside}
    assert not unexpected, unexpected


def test_the_manage_queue_is_filled_only_inside_the_loop_v63_empties():
    fn = _build_mm(); parents = _parents(fn)
    appends = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and ast.unparse(n.func) == "manage_queue.append"]
    assert appends and all(_retired(n, parents) for n in appends)
    inits = [n for n in ast.walk(fn) if isinstance(n, ast.Assign) and ast.unparse(n.targets[0]) == "manage_queue"]
    assert len(inits) == 1 and ast.unparse(inits[0].value) in ("[]", "list()")


def test_under_v62_the_shortlist_is_every_book_whatever_the_predictions():
    src = ast.unparse(_build_mm())
    i = src.index("selected_ids = {int(x) for x in (predictions or {}).keys()}")
    tail = src[i:i + 400]
    assert "if self._v62_on():" in tail and "selected_ids = {int(x) for x in getattr(state, 'books', None) or {}}" in tail


def test_the_respond_order_is_predict_select_regime_then_build():
    body = BASE_S1[BASE_S1.index("    def respond(self, state: MarketSimulationStateUpdate)"):]
    idx = [body.index(s) for s in ("self._predict_all_books(state)", "self.select_books_for_trading(state, predictions)",
                                   "self.classify_market_regime_from_profiles(", "self.build_mm_strategy_instructions(")]
    assert idx == sorted(idx)


# ---- 6. wiring -----------------------------------------------------------------------------------------------------

def test_the_switch_defaults_on_rides_on_v63_and_is_launched_on():
    assert 'self.research_v631_lean_handler = self._as_bool(getattr(self.config, "research_v631_lean_handler", True))' in SIMPLE
    assert 'return bool(self._v63_on() and getattr(self, "research_v631_lean_handler", False))' in SIMPLE
    assert "lean_handler_on=int(self._v631_lean_on())," in SIMPLE
    assert "research_v631_lean_handler=1" in LAUNCHER and "[preflight] v6.3.1 lean handler PASS" in LAUNCHER
