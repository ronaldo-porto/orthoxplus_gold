"""v6.40 (k139): the two mechanical copies in agents/strategy/simple_parts/v6_40_part.py, as AST transforms of Simple's
originals, and their exact reverse -- used by the build (scratchpad k139/build/gen_part.py writes the copies) and by
tests/test_research_v6_40.py (the copy on disk == the transform of the live original; the reverse of the copy == the
original).  STRUCTURAL: nothing but the substitutions documented in the part's docstring.

  deep_core(_v633_book)   -> _v6_40_deep_core: ``du = self._v6_40_du(book_id, tick_size)`` and ``scale, vmin, vdec =
                             self._v6_40_units(book_id, min_order, cfg)`` first after the docstring;
                             v633_deep_price(...) / v64_vacuum_price(...) -> v6_40_deep_price_du / v6_40_vacuum_price_du
                             with ``tick=tick_size`` -> ``du=du, tick=tick_size``; every other read of ``tick_size`` ->
                             ``du``; and UNITS (below).
  touch_core(_v617_touch) -> _v6_40_touch_core: ``unit, rest = self._v6_40_touch_units(book_id, tick_size)`` and the
                             same ``scale, vmin, vdec = ...`` first after the docstring; in v617_verdict(...)
                             ``tick=tick_size`` -> ``tick=unit, rest_ticks=rest``; ``price = v617_wanted_price(...)`` ->
                             ``price = self._v6_40_touch_price(toward, raw_bid, raw_ask, fp, tick_size, book_id)``; the
                             v6.31 improve block (``if bool(getattr(self, "research_v631_touch_improve", False)):``)
                             removed (_v6_40_touch_price applies it); and UNITS.
  UNITS (both)            -> ``q = v62_lot_quantity(q_, <vol decimals>)`` -> ``q = v62_lot_quantity(q_ * scale, vdec)``;
                             ``if q + 1e-12 < min_order:`` -> ``< vmin``; ``v691_quantity_within_free(q, free, min_order,
                             <vol decimals>)`` -> ``(q, free, vmin, vdec)``."""
from __future__ import annotations

import ast
import copy

DEEP_NAME, TOUCH_NAME = "_v6_40_deep_core", "_v6_40_touch_core"
PRICE_FUNCS = {"v633_deep_price": "v6_40_deep_price_du", "v64_vacuum_price": "v6_40_vacuum_price_du"}
IMPROVE_SWITCH = "research_v631_touch_improve"



def _same(node, text):
    """A statement is ``text`` -- by AST (ast.unparse's text differs between CPython versions: 3.10 writes a tuple
    target as "(a, b) = ...", 3.12 as "a, b = ...")."""
    return ast.dump(node) == ast.dump(ast.parse(text).body[0])

def _name(n, ctx=None):
    return ast.Name(id=n, ctx=ctx or ast.Load())


def _self_call(method, args):
    return ast.Call(func=ast.Attribute(value=_name("self"), attr=method, ctx=ast.Load()),
                    args=[_name(a) for a in args], keywords=[])


def _body_start(fn):
    """Index of the first statement after the docstring."""
    b = fn.body
    if b and isinstance(b[0], ast.Expr) and isinstance(getattr(b[0], "value", None), ast.Constant) \
            and isinstance(b[0].value.value, str):
        return 1
    return 0


class _Deep(ast.NodeTransformer):
    def visit_Call(self, node):
        if isinstance(node.func, ast.Name) and node.func.id in PRICE_FUNCS:
            node.func = _name(PRICE_FUNCS[node.func.id])
            node.args = [self.visit(a) for a in node.args]
            kws = []
            for k in node.keywords:
                if k.arg == "tick" and isinstance(k.value, ast.Name) and k.value.id == "tick_size":
                    kws.append(ast.keyword(arg="du", value=_name("du")))
                    kws.append(ast.keyword(arg="tick", value=_name("tick_size")))
                else:
                    kws.append(ast.keyword(arg=k.arg, value=self.visit(k.value)))
            node.keywords = kws
            return node
        return self.generic_visit(node)

    def visit_Name(self, node):
        if node.id == "tick_size" and isinstance(node.ctx, ast.Load):
            return _name("du")
        return node


class _Units(ast.NodeTransformer):
    """UNITS: the placed size in the class's scale, checked against the class's minimum (see the module docstring)."""

    def __init__(self):
        self.hits = {"lot": 0, "min": 0, "free": 0}

    def visit_Assign(self, node):
        self.generic_visit(node)
        v = node.value
        if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "q"
                and isinstance(v, ast.Call) and isinstance(v.func, ast.Name) and v.func.id == "v62_lot_quantity"
                and len(v.args) == 2 and isinstance(v.args[0], ast.Name) and v.args[0].id == "q_"):
            v.args = [ast.BinOp(left=_name("q_"), op=ast.Mult(), right=_name("scale")), _name("vdec")]
            self.hits["lot"] += 1
        return node

    def visit_Compare(self, node):
        self.generic_visit(node)
        if (len(node.ops) == 1 and isinstance(node.ops[0], ast.Lt) and len(node.comparators) == 1
                and isinstance(node.comparators[0], ast.Name) and node.comparators[0].id == "min_order"
                and ast.unparse(node.left) == "q + 1e-12"):
            node.comparators = [_name("vmin")]
            self.hits["min"] += 1
        return node

    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "v691_quantity_within_free" and len(node.args) == 4 \
                and isinstance(node.args[2], ast.Name) and node.args[2].id == "min_order":
            node.args = [node.args[0], node.args[1], _name("vmin"), _name("vdec")]
            self.hits["free"] += 1
        return node


def _units(stmts):
    t = _Units()
    out = [t.visit(st) for st in stmts]
    assert t.hits == {"lot": 1, "min": 1, "free": 1}, t.hits
    return out


def _units_back(stmts, original):
    """UNITS reversed: the three expressions put back from the original (the volume-decimals expression is the
    original's own)."""
    lot = next(n for n in ast.walk(original) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "v62_lot_quantity" and len(n.args) == 2 and ast.unparse(n.args[0]) == "q_")
    free = next(n for n in ast.walk(original) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                and n.func.id == "v691_quantity_within_free")

    class Back(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id == "v62_lot_quantity" and len(node.args) == 2 \
                    and ast.unparse(node.args[0]) == "q_ * scale" and ast.unparse(node.args[1]) == "vdec":
                return copy.deepcopy(lot)
            if isinstance(node.func, ast.Name) and node.func.id == "v691_quantity_within_free" \
                    and ast.unparse(node) == "v691_quantity_within_free(q, free, vmin, vdec)":
                return copy.deepcopy(free)
            return node

        def visit_Compare(self, node):
            self.generic_visit(node)
            if ast.unparse(node) == "q + 1e-12 < vmin":
                node.comparators = [_name("min_order")]
            return node
    return [Back().visit(st) for st in stmts]


def _units_first():
    return ast.Assign(targets=[ast.Tuple(elts=[_name("scale", ast.Store()), _name("vmin", ast.Store()),
                                               _name("vdec", ast.Store())], ctx=ast.Store())],
                      value=_self_call("_v6_40_units", ["book_id", "min_order", "cfg"]))


def deep_core(original: ast.FunctionDef) -> ast.FunctionDef:
    fn = copy.deepcopy(original)
    fn.name = DEEP_NAME
    i = _body_start(fn)
    body = _units([_Deep().visit(st) for st in fn.body[i:]])
    first = ast.Assign(targets=[_name("du", ast.Store())], value=_self_call("_v6_40_du", ["book_id", "tick_size"]))
    fn.body = fn.body[:i] + [first, _units_first()] + body
    return ast.fix_missing_locations(fn)


class _DeepBack(ast.NodeTransformer):
    def visit_Call(self, node):
        back = {v: k for k, v in PRICE_FUNCS.items()}
        if isinstance(node.func, ast.Name) and node.func.id in back:
            node.func = _name(back[node.func.id])
            node.args = [self.visit(a) for a in node.args]
            kws = []
            for k in node.keywords:
                if k.arg == "du":
                    continue
                kws.append(ast.keyword(arg=k.arg, value=self.visit(k.value)) if k.arg != "tick" else k)
            node.keywords = kws
            return node
        return self.generic_visit(node)

    def visit_Name(self, node):
        if node.id == "du" and isinstance(node.ctx, ast.Load):
            return _name("tick_size")
        return node


_ORIG: dict = {}


def deep_back(copy_fn: ast.FunctionDef, name: str = "_v633_book", original: ast.FunctionDef | None = None) -> ast.FunctionDef:
    """The reverse of deep_core (UNITS' volume-decimals expressions are taken from ``original``)."""
    _ORIG["deep"] = original
    fn = copy.deepcopy(copy_fn)
    fn.name = name
    i = _body_start(fn)
    first, second = fn.body[i], fn.body[i + 1]
    assert isinstance(first, ast.Assign) and _same(first, "du = self._v6_40_du(book_id, tick_size)"), ast.unparse(first)
    assert ast.dump(second) == ast.dump(_units_first()), ast.dump(second)
    fn.body = fn.body[:i] + _units_back([_DeepBack().visit(st) for st in fn.body[i + 2:]], _ORIG.get("deep"))
    return ast.fix_missing_locations(fn)


def _is_improve_if(st) -> bool:
    return isinstance(st, ast.If) and IMPROVE_SWITCH in ast.unparse(st.test)


class _Touch(ast.NodeTransformer):
    def visit_Call(self, node):
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "v617_verdict":
            kws = []
            for k in node.keywords:
                if k.arg == "tick":
                    kws.append(ast.keyword(arg="tick", value=_name("unit")))
                    kws.append(ast.keyword(arg="rest_ticks", value=_name("rest")))
                else:
                    kws.append(k)
            node.keywords = kws
        return node

    def visit_Assign(self, node):
        self.generic_visit(node)
        if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and node.targets[0].id == "price"
                and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Name)
                and node.value.func.id == "v617_wanted_price"):
            node.value = _self_call("_v6_40_touch_price", ["toward", "raw_bid", "raw_ask", "fp", "tick_size", "book_id"])
        return node


def touch_core(original: ast.FunctionDef) -> ast.FunctionDef:
    fn = copy.deepcopy(original)
    fn.name = TOUCH_NAME
    i = _body_start(fn)
    improve = [st for st in fn.body[i:] if _is_improve_if(st)]
    assert len(improve) == 1, "the v6.31 improve block must be one top-level statement"
    body = _units([_Touch().visit(st) for st in fn.body[i:] if not _is_improve_if(st)])
    wanted = [n for n in ast.walk(original) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "v617_wanted_price"]
    assert len(wanted) == 1
    first = ast.Assign(targets=[ast.Tuple(elts=[_name("unit", ast.Store()), _name("rest", ast.Store())], ctx=ast.Store())],
                       value=_self_call("_v6_40_touch_units", ["book_id", "tick_size"]))
    fn.body = fn.body[:i] + [first, _units_first()] + body
    return ast.fix_missing_locations(fn)


def touch_back(copy_fn: ast.FunctionDef, original: ast.FunctionDef) -> ast.FunctionDef:
    """The reverse needs the two pieces the copy dropped (the improve block, the wanted-price call): taken from the
    original by position and compared with everything else -- so the test proves the copy differs from the original
    in exactly the documented places."""
    fn = copy.deepcopy(copy_fn)
    fn.name = original.name
    i = _body_start(fn)
    first = fn.body[i]
    assert _same(first, "unit, rest = self._v6_40_touch_units(book_id, tick_size)"), ast.unparse(first)
    assert ast.dump(fn.body[i + 1]) == ast.dump(_units_first())
    body = _units_back(fn.body[i + 2:], original)
    j = _body_start(original)
    orig_body = original.body[j:]
    k = next(n for n, st in enumerate(orig_body) if _is_improve_if(st))
    body = body[:k] + [copy.deepcopy(orig_body[k])] + body[k:]
    wanted = next(n for n in ast.walk(original) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "v617_wanted_price")

    class Back(ast.NodeTransformer):
        def visit_Call(self, node):
            self.generic_visit(node)
            if isinstance(node.func, ast.Name) and node.func.id == "v617_verdict":
                kws = []
                for kw in node.keywords:
                    if kw.arg == "rest_ticks" and isinstance(kw.value, ast.Name) and kw.value.id == "rest":
                        continue
                    if kw.arg == "tick" and isinstance(kw.value, ast.Name) and kw.value.id == "unit":
                        kws.append(ast.keyword(arg="tick", value=_name("tick_size")))
                    else:
                        kws.append(kw)
                node.keywords = kws
            return node

        def visit_Assign(self, node):
            self.generic_visit(node)
            if (isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                    and node.value.func.attr == "_v6_40_touch_price"):
                node.value = copy.deepcopy(wanted)
            return node

    fn.body = fn.body[:i] + [Back().visit(st) for st in body]
    return ast.fix_missing_locations(fn)


def free_names(fn: ast.FunctionDef) -> set:
    """Names a function reads that it does not bind itself (arguments, assignments, loops, comprehensions, handlers,
    nested defs / lambdas): the module-level names it needs."""
    bound = set()
    for n in ast.walk(fn):
        if isinstance(n, ast.arg):
            bound.add(n.arg)
        elif isinstance(n, ast.Name) and isinstance(n.ctx, (ast.Store, ast.Del)):
            bound.add(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and n is not fn:
            bound.add(n.name)
        elif isinstance(n, ast.ExceptHandler) and n.name:
            bound.add(n.name)
        elif isinstance(n, (ast.Import, ast.ImportFrom)):
            for a in n.names:
                bound.add(a.asname or a.name.split(".")[0])
    used = {n.id for n in ast.walk(fn) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
    return used - bound


def simple_imports(tree: ast.Module) -> dict:
    """{bound name: (module, original name)} of Simple's module-level ``from X import Y [as Z]`` statements."""
    out = {}
    for st in tree.body:
        if isinstance(st, ast.ImportFrom) and st.module and not st.level:
            for a in st.names:
                out[a.asname or a.name] = (st.module, a.name)
    return out
