"""v6.40 (k139): the exact text undo every older version test applies before it compares a file with an earlier build.

v6.40 = v6.39 (841a8fc) + the three-class layout of SN79 0.6.5 (research_v640_*; scratchpad k139/CHANGES.md): two new
agent files (research_v6_40_classes.py, simple_parts/v6_40_part.py) and hook edits, each an exact (old, new) text pair
recorded by the build in tests/_v640_edits.json:
  edits        on the JOINED Simple (simple_parts/join.py's single-file text): 15 in Strategy1_Research_Simple.py, 2 in
               simple_parts/v6_32_part.py, 2 in simple_parts/v6_36_part.py (the MANIFEST's chunk ranges follow them);
  split_edits  V640Part's import and first base in the real Simple (the MANIFEST lists them first in simple_edits, so the
               joined text stays the single file);
  miner        taos/common/neurons/miner.py: the upstream 0.6.5 axon keep-alive;
  launcher     the 11 PARAMS keys, the "# v6.40." preflight block, the gate's test entry.
``joined_v639(text)`` / ``source_v639(name, text)`` / ``launcher_v639(text)`` reverse them -- v6.39's text byte for byte
(tests/test_research_v6_40.py pins the digests) -- and leave a text without v6.40's edits as it is, so the chain is
idempotent; tests/_v639_undo.py applies them first (parse, source_v638, launcher_v638), so every older pin inherits
them.  STRUCTURAL."""
from __future__ import annotations

import json
from pathlib import Path

import _v641_undo   # v6.41 (k144): its exact text / AST undo runs first in every function below

_HERE = Path(__file__).resolve().parent
EDITS = json.loads((_HERE / "_v640_edits.json").read_text(encoding="utf-8"))
JOINED_EDITS = tuple((e["old"], e["new"]) for e in EDITS["edits"])
LAUNCHER_EDITS = tuple((e["old"], e["new"]) for e in EDITS.get("launcher", ()))
MINER_EDITS = tuple((e["old"], e["new"]) for e in EDITS.get("miner", ()))
BASE_JOINED_SHA256 = EDITS["base_joined_sha256"]           # v6.39's joined Simple (MANIFEST v640_base_sha256)
JOINED_SHA256 = EDITS["joined_sha256"]                     # v6.40's joined Simple (MANIFEST pre_split_sha256)
LAUNCHER_BASE_SHA256 = EDITS.get("launcher_base_sha256")
SWITCHES = ("research_v640_traded_classes", "research_v640_c2du", "research_v640_c2d", "research_v640_c2j",
            "research_v640_c2q", "research_v640_c2q_pf", "research_v640_c1w", "research_v640_guard_hold",
            "research_v640_guard_init_price", "research_v640_s14_class_key", "research_v640_s14_flush_spec")
# The git file-list pins (`git diff --name-only <commit> -- agents/strategy taos`) list a new file once it is tracked and a
# changed one whether staged or not: every such pin subtracts V640_PATHS (as V639_PATHS), so it holds untracked and tracked.
V640_NEW = frozenset({"agents/strategy/research_v6_40_classes.py", "agents/strategy/simple_parts/v6_40_part.py"})
V640_MODULES = frozenset({"agents/strategy/Strategy1_Research_Simple.py", "agents/strategy/simple_parts/MANIFEST.json",
                          "agents/strategy/simple_parts/v6_32_part.py", "agents/strategy/simple_parts/v6_36_part.py",
                          "taos/common/neurons/miner.py"})
V640_PATHS = V640_NEW | V640_MODULES
V640_TESTS = frozenset({"tests/test_research_v6_40.py", "tests/_v640_undo.py", "tests/_v640_gen.py",
                        "tests/_v640_edits.json"})
MARKER = "self._v6_40_init()"


def _reverse(text, pairs, what):
    for old, new in reversed(pairs):
        if text.count(new) != 1:
            raise AssertionError("v6.40 %s edit not found exactly once: %r" % (what, new[:80]))
        text = text.replace(new, old)
    return text


def is_v640_joined(text) -> bool:
    return isinstance(text, str) and MARKER in text


def joined_v639(text):
    """The joined Simple of v6.40 with v6.40's edits reversed: v6.39's joined text byte for byte.  A text that carries
    none of them (an older build's, or already undone) comes back as it is; one that carries some but not all raises."""
    if not isinstance(text, str):
        return text
    text = _v641_undo.joined_v640(text)                    # v6.41 first (tests/_v641_undo.py)
    present = [new for _old, new in JOINED_EDITS if new in text]
    if not present:
        return text
    return _reverse(text, JOINED_EDITS, "joined Simple")


def source_v639(name, text):
    """agents/strategy/<name> (or taos/common/neurons/miner.py) with v6.40's edits reversed: v6.39's file.  Simple's name
    takes the joined text; a file v6.40 did not edit comes back as it is."""
    text = _v641_undo.source_v640(name, text)              # v6.41 first (tests/_v641_undo.py)
    base = str(name).rsplit("/", 1)[-1]
    if base == "Strategy1_Research_Simple.py":
        return joined_v639(text)
    if base == "miner.py" and any(new in text for _old, new in MINER_EDITS):
        return _reverse(text, MINER_EDITS, "miner")
    return text


SHIPPED = {"research_v640_traded_classes": "0", "research_v640_c2du": "0", "research_v640_c2d": "0",
           "research_v640_c2j": "0", "research_v640_c2q": "0", "research_v640_c2q_pf": "0", "research_v640_c1w": "0",
           "research_v640_guard_hold": "1", "research_v640_guard_init_price": "1", "research_v640_s14_class_key": "1",
           "research_v640_s14_flush_spec": "1"}
# The one-flip sets a PASS may ship with a PARAMS edit alone (k139 B7): each is a v6.40 launcher change, so the undo puts
# its values back (the older pins then compare v6.39's launcher); tests/test_research_v6_40.py allows exactly these.
FLIPS = {
    "C2P": {"research_v640_traded_classes": "0,2", "research_v640_c2du": "1"},
    "C2D": {"research_v640_traded_classes": "0,2", "research_v640_c2du": "1", "research_v640_c2d": "1"},
    "C2J": {"research_v640_traded_classes": "0,2", "research_v640_c2du": "1", "research_v640_c2j": "1"},
    "C2J2": {"research_v640_traded_classes": "0,2", "research_v640_c2du": "1", "research_v640_c2j": "2"},
    "C2Q": {"research_v640_traded_classes": "0,2", "research_v640_c2du": "1", "research_v640_c2q": "1",
            "research_v640_c2q_pf": None},                 # None: any pool factor > 0 (the board's published value)
    "C1W": {"research_v640_c1w": "1", "research_v634_class1_entry": "1"},
    # G2 "guarded class-2 entry" (coordinator / user 10-24): class 2 deep only (C2D), C2Q's break-even on the live pool
    # factor with the configured one as its fallback (None: any > 0)
    "G2": {"research_v640_traded_classes": "0,2", "research_v640_c2du": "1", "research_v640_c2d": "1",
           "research_v640_c2q": "1", "research_v640_c2q_pf": None},
    "CLASS1": {"research_v640_traded_classes": "0,1"},
    "CLASS2": {"research_v640_traded_classes": "0,2"},
    "CLASS12": {"research_v640_traded_classes": "0,1,2", "research_v640_c2du": "1"},
    # v6.45 (USER 10-30 "build 3.5x", Round 4 C1P): G2 plus class 1 on the class-0 machinery (traded 0,1,2)
    "G2C1": {"research_v640_traded_classes": "0,1,2", "research_v640_c2du": "1", "research_v640_c2d": "1",
             "research_v640_c2q": "1", "research_v640_c2q_pf": None},
}
_DEFAULTS = dict(SHIPPED, research_v634_class1_entry="0")


def params_of(text):
    start = text.index('PARAMS="')
    body = text[start + len('PARAMS="'):text.index('"', start + len('PARAMS="'))].replace("\\\n", " ")
    return dict(t.split("=", 1) for t in body.split() if "=" in t)


def flip_of(text):
    """The documented flip a launcher ships ("" = the shipped defaults), or None when its v6.40 values are neither."""
    vals = params_of(text)
    diff = {k: vals.get(k) for k in _DEFAULTS if vals.get(k) != _DEFAULTS[k]}
    if not diff:
        return ""
    for name, flip in FLIPS.items():
        if set(diff) == set(flip) and all(v is None or diff[k] == v for k, v in flip.items()):
            if "research_v640_c2q_pf" in flip:
                try:
                    if not float(diff["research_v640_c2q_pf"]) > 0.0:
                        continue
                except ValueError:
                    continue
            return name
    return None


def _unflip(text):
    """A documented flip's values back to the shipped defaults, each on its own PARAMS line (anything else: as it is)."""
    if not flip_of(text):
        return text
    vals = params_of(text)
    for k, d in _DEFAULTS.items():
        v = vals.get(k)
        if v is None or v == d:
            continue
        for end in (" \\\n", '"\n'):
            old, new = "\n%s=%s%s" % (k, v, end), "\n%s=%s%s" % (k, d, end)
            if text.count(old) == 1:
                text = text.replace(old, new)
                break
    return text


def launcher_v639(text):
    """v6.40's launcher with its edits reversed (a documented one-flip's values first put back): v6.39's launcher byte for
    byte (tests/test_research_v6_40.py pins the digest).  A launcher without the v6.40 block comes back as it is."""
    text = _v641_undo.launcher_v640(text)                  # v6.41 first (tests/_v641_undo.py)
    if "# v6.40." not in text:
        return text
    return _reverse(_unflip(text), LAUNCHER_EDITS, "launcher")


# ---- the same undo on a parsed module (the older tests strip ASTs: tests/_v638_undo.undo -> _v639_undo.undo -> this) ----
import ast as _ast

SIMPLE_CLASS = "Strategy1_Research_Simple"
PART_MODULE = "simple_parts.v6_40_part"


def _names_v640(node) -> bool:
    """Does the node name a v6.40 identifier (a v6_40_ / _v6_40_ name or attribute, a research_v640_ key)?"""
    for n in _ast.walk(node):
        if isinstance(n, _ast.Name) and (n.id.startswith("v6_40") or n.id.startswith("_v6_40")):
            return True
        if isinstance(n, _ast.Attribute) and (n.attr.startswith("_v6_40") or n.attr.startswith("research_v640")):
            return True
        if isinstance(n, _ast.Constant) and isinstance(n.value, str) and n.value.startswith(("research_v640", "_v6_40")):
            return True
    return False


class _IfExpBack(_ast.NodeTransformer):
    """``(A if v6_40_x is None else B)`` -> A; ``(A if <v6.40 test> else B)`` -> B (the branch v6.40 off takes)."""

    def visit_IfExp(self, node):
        self.generic_visit(node)
        if not _names_v640(node.test):
            return node
        t = node.test
        if isinstance(t, _ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], _ast.Is) \
                and isinstance(t.comparators[0], _ast.Constant) and t.comparators[0].value is None:
            return node.body
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
            if isinstance(st, _ast.If) and _names_v640(st.test):
                for inner in st.orelse:                       # v6.40's if: its else branch (none: nothing)
                    _strip(inner)
                    out.append(inner)
                continue
            if isinstance(st, (_ast.Expr, _ast.Assign, _ast.AugAssign, _ast.AnnAssign)) and _names_v640(st):
                continue                                      # a v6.40 statement
            _strip(st)
            out.append(st)
        setattr(owner, field, out)


def undo_tree(tree):
    """v6.40's hooks undone in a parsed module, in place: its imports and V640Part base, every if / statement naming a
    v6.40 identifier (an if keeps its else branch), every conditional expression on one (the branch v6.40 off takes).
    On the joined Simple == ast.parse(joined_v639(text)) exactly (tests/test_research_v6_40.py); any module without
    v6.40 names is left as it is.  Returns the tree."""
    _v641_undo.undo_tree(tree)                             # v6.41 first (tests/_v641_undo.py)
    tree.body = [st for st in tree.body if not (isinstance(st, _ast.ImportFrom) and st.module == PART_MODULE)]
    for st in tree.body:
        if isinstance(st, _ast.ClassDef) and st.name == SIMPLE_CLASS:
            st.bases = [b for b in st.bases if not (isinstance(b, _ast.Name) and b.id == "V640Part")]
            _IfExpBack().visit(st)
            _strip(st)
    return tree


def undo_node(node):
    """undo_tree's statement / conditional-expression rules on one parsed node (a method, a loop), in place; returns it."""
    _v641_undo.undo_node(node)                             # v6.41 first
    _IfExpBack().visit(node)
    _strip(node)
    return node
