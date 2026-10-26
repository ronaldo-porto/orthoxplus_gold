"""v6.41 (k144): the exact text undo every older version test applies before it compares a file with an earlier build.

v6.41 = v6.40.1 (dc17be6) + B1 speed (research_v641_speed, scratchpad k141 P3) + B2 per-class units (research_v641_unit_safety)
+ B2b C2Q's inventory (research_v641_c2q_reduce / _c2q_persist) + B7 S9's page once per request (research_v641_s9_page); three new
agent files (research_v6_41_speed.py, research_v6_41_units.py, simple_parts/v6_41_part.py) and exact (old, new) text pairs
recorded by the build in tests/_v641_edits.json:
  edits         on the JOINED Simple (simple_parts/join.py's single-file text): Strategy1_Research_Simple.py and
                simple_parts/v6_32_part.py (the MANIFEST's chunk ranges follow them);
  split_edits   V641Part's import and first base in the real Simple (first in the MANIFEST's simple_edits);
  module_edits  B1's five module files (taos/im/protocol/models.py and four agents/strategy modules);
  launcher      the PARAMS keys, v6.40's class-line check (V641Part first), the "# v6.41." block, the gate's test entry.
``joined_v640(text)`` / ``source_v640(name, text)`` / ``launcher_v640(text)`` reverse them -- v6.40.1's text byte for byte
(tests/test_research_v6_41.py pins the digests) -- and leave a text without v6.41's edits as it is, so the chain is
idempotent; tests/_v640_undo.py applies them first (joined_v639, source_v639, launcher_v639, undo_tree), so every older
pin inherits them.  STRUCTURAL."""
from __future__ import annotations

import ast as _ast
import json
from pathlib import Path

import _v642_undo   # v6.42 (k150): its exact text / AST undo runs first in every function below

_HERE = Path(__file__).resolve().parent
EDITS = json.loads((_HERE / "_v641_edits.json").read_text(encoding="utf-8"))
JOINED_EDITS = tuple((e["old"], e["new"]) for e in EDITS["edits"])
LAUNCHER_EDITS = tuple((e["old"], e["new"]) for e in EDITS.get("launcher", ()))
MODULE_EDITS = {name.rsplit("/", 1)[-1]: tuple((o, n) for o, n in pairs)
                for name, pairs in (EDITS.get("module_edits") or {}).items()}
BASE_JOINED_SHA256 = EDITS["base_joined_sha256"]           # v6.40's joined Simple (MANIFEST v641_base_sha256)
JOINED_SHA256 = EDITS["joined_sha256"]                     # v6.41's joined Simple (MANIFEST pre_split_sha256)
LAUNCHER_BASE_SHA256 = EDITS.get("launcher_base_sha256")
SWITCHES = ("research_v641_speed", "research_v641_unit_safety", "research_v641_c2q_reduce", "research_v641_c2q_persist",
            "research_v641_s9_page")
SHIPPED = {k: "1" for k in SWITCHES}
# The documented one-flips (k144 flip proofs): each v6.41 item turned off by a PARAMS edit alone (= v6.40.1's code on that
# path); the undo puts the shipped value back first, so every older pin compares v6.40.1's launcher.
FLIPS = {"NO_" + k[len("research_v641_"):].upper(): {k: "0"} for k in SWITCHES}


def params_of(text):
    start = text.index('PARAMS="')
    body = text[start + len('PARAMS="'):text.index('"', start + len('PARAMS="'))].replace("\\\n", " ")
    return dict(t.split("=", 1) for t in body.split() if "=" in t)


def flip_of(text):
    """The documented v6.41 flip a launcher ships ("" = the shipped values), or None when its v6.41 values are neither."""
    vals = params_of(text)
    diff = {k: vals.get(k) for k in SWITCHES if vals.get(k) != SHIPPED[k]}
    if not diff:
        return ""
    for name, flip in FLIPS.items():
        if diff == flip:
            return name
    return None


def _unflip(text):
    """A documented flip's value back to the shipped one, on its own PARAMS line (anything else: as it is)."""
    if not flip_of(text):
        return text
    for k, v in params_of(text).items():
        if k in SHIPPED and v != SHIPPED[k]:
            for end in (" \\\n", '"\n'):
                old, new = "\n%s=%s%s" % (k, v, end), "\n%s=%s%s" % (k, SHIPPED[k], end)
                if text.count(old) == 1:
                    text = text.replace(old, new)
                    break
    return text


V641_NEW = frozenset({"agents/strategy/research_v6_41_speed.py", "agents/strategy/research_v6_41_units.py",
                      "agents/strategy/simple_parts/v6_41_part.py"})
V641_MODULES = frozenset({"agents/strategy/Strategy1_Research_Simple.py", "agents/strategy/simple_parts/MANIFEST.json",
                          "agents/strategy/simple_parts/v6_32_part.py",
                          "agents/strategy/research_direct_idle_gc.py", "agents/strategy/research_v629_reply_path.py",
                          "agents/strategy/research_v633_deep_layer.py", "agents/strategy/research_v6_38_fast_path.py",
                          "taos/im/protocol/models.py"})
V641_PATHS = V641_NEW | V641_MODULES
V641_TESTS = frozenset({"tests/test_research_v6_41.py", "tests/_v641_undo.py", "tests/_v641_edits.json"})
MARKER = "self._v6_41_init()"


def _reverse(text, pairs, what):
    for old, new in reversed(pairs):
        if text.count(new) != 1:
            raise AssertionError("v6.41 %s edit not found exactly once: %r" % (what, new[:80]))
        text = text.replace(new, old)
    return text


def is_v641_joined(text) -> bool:
    return isinstance(text, str) and MARKER in text


def joined_v640(text):
    """The joined Simple of v6.41 with v6.41's edits reversed: v6.40's joined text byte for byte.  A text that carries
    none of them comes back as it is."""
    if not isinstance(text, str):
        return text
    text = _v642_undo.joined_v641(text)                    # v6.42 first (tests/_v642_undo.py)
    if not any(new in text for _old, new in JOINED_EDITS):
        for base, pairs in MODULE_EDITS.items():        # one of B1's module files (the older parse() helpers take any text)
            if pairs and all(new in text for _old, new in pairs):
                return _reverse(text, pairs, base)
        return text
    return _reverse(text, JOINED_EDITS, "joined Simple")


def source_v640(name, text):
    """agents/strategy/<name> (or taos/im/protocol/models.py) with v6.41's edits reversed: v6.40.1's file.  Simple's name
    takes the joined text; a file v6.41 did not edit comes back as it is."""
    if not isinstance(text, str):
        return text
    text = _v642_undo.source_v641(name, text)              # v6.42 first (tests/_v642_undo.py)
    base = str(name).rsplit("/", 1)[-1]
    if base == "Strategy1_Research_Simple.py":
        return joined_v640(text)
    pairs = MODULE_EDITS.get(base)
    if pairs and any(new in text for _old, new in pairs):
        return _reverse(text, pairs, base)
    return text


def launcher_v640(text):
    """v6.41's launcher with its edits reversed: v6.40.1's launcher byte for byte (a launcher without the v6.41 block
    comes back as it is)."""
    text = _v642_undo.launcher_v641(text)                  # v6.42 first (tests/_v642_undo.py)
    if not isinstance(text, str) or "# v6.41." not in text:
        return text
    return _reverse(_unflip(text), LAUNCHER_EDITS, "launcher")


# ---- the same undo on a parsed module (tests/_v640_undo.undo_tree runs this first) ----

SIMPLE_CLASS = "Strategy1_Research_Simple"
PART_MODULE = "simple_parts.v6_41_part"
SPEED_MODULE = "research_v6_41_speed"
_ATTRS = frozenset({"_v641_ts_backstep", "_v641_prev_state_ts", "_v641_saving", "_v641_deferred_save"} | set(SWITCHES))
_NAMES = frozenset({"v641_ts", "v641_prev", "v641_work"})
GA = "v6_41_ga"            # F1's hoisted getattr: back to the builtin


def _names_v641(node) -> bool:
    """Does the node name a v6.41 identifier (a v6_41 / _v6_41 name or attribute, a research_v641_* switch of v6.41,
    B1's _v641_* attributes / locals)?"""
    for n in _ast.walk(node):
        if isinstance(n, _ast.Name) and (n.id.startswith(("v6_41", "_v6_41")) or n.id in _NAMES):
            return True
        if isinstance(n, _ast.Attribute) and (n.attr.startswith("_v6_41") or n.attr in _ATTRS):
            return True
        if isinstance(n, _ast.Constant) and isinstance(n.value, str) and (n.value in _ATTRS or n.value.startswith("_v6_41")):
            return True
    return False


class _GaBack(_ast.NodeTransformer):
    def visit_Name(self, node):
        if node.id == GA:
            return _ast.copy_location(_ast.Name(id="getattr", ctx=node.ctx), node)
        return node


class _IfExpBack(_ast.NodeTransformer):
    def visit_IfExp(self, node):
        self.generic_visit(node)
        if not _names_v641(node.test):
            return node
        return node.orelse


def _stmt_lists(node):
    for field in ("body", "orelse", "finalbody"):
        value = getattr(node, field, None)
        if isinstance(value, list) and all(isinstance(v, _ast.stmt) for v in value):
            yield node, field
    for h in getattr(node, "handlers", None) or ():
        yield h, "body"


def _strip(node):
    for owner, field in list(_stmt_lists(node)):
        out = []
        for st in getattr(owner, field):
            if isinstance(st, _ast.If) and _names_v641(st.test):
                for inner in st.orelse:
                    _strip(inner)
                    out.append(inner)
                continue
            if isinstance(st, (_ast.Expr, _ast.Assign, _ast.AugAssign, _ast.AnnAssign)) and _names_v641(st):
                continue
            _strip(st)
            out.append(st)
        setattr(owner, field, out)


def undo_tree(tree):
    """v6.41's hooks undone in a parsed module, in place: its imports and V641Part base, every if / statement naming a
    v6.41 identifier (an if keeps its else branch), every conditional expression on one (the off branch), F1's
    hoisted getattr back to the builtin.  On the joined Simple == ast.parse(joined_v640(text)) exactly
    (tests/test_research_v6_41.py); a module without v6.41 names is left as it is.  Returns the tree."""
    _v642_undo.undo_tree(tree)                             # v6.42 first (tests/_v642_undo.py)
    tree.body = [st for st in tree.body
                 if not (isinstance(st, _ast.ImportFrom) and st.module in (PART_MODULE, SPEED_MODULE))]
    for st in tree.body:
        if isinstance(st, _ast.ClassDef) and st.name == SIMPLE_CLASS:
            st.bases = [b for b in st.bases if not (isinstance(b, _ast.Name) and b.id == "V641Part")]
            _GaBack().visit(st)          # first: F1's hoisted assignment then names v6_41_getattr only (dropped below)
            _strip(st)
            _IfExpBack().visit(st)
    return tree


def undo_node(node):
    _v642_undo.undo_node(node)                             # v6.42 first
    _GaBack().visit(node)
    _strip(node)
    _IfExpBack().visit(node)
    return node
