"""v6.42 (k150): the exact text undo every older version test applies before it compares a file with an earlier build.

v6.42 = the final v6.41 + k149's L1T rule as a live port (research_v642_l1t in {0, 1, 2, 3}) and k151's bound of its carried size
(research_v642_l1c in {0, 1, 3, 4}); two new agent
files (research_v6_42_l1t.py, simple_parts/v6_42_part.py) and exact (old, new) text pairs recorded by the build in
tests/_v642_edits.json:
  edits         on the JOINED Simple (simple_parts/join.py's single-file text): seven hooks in Strategy1_Research_Simple.py
                (the v6.3 pass's note, the deep ladder's plan / doom / cancelled / L1 hold / carry / placed; the MANIFEST's
                chunk ranges follow them);
  split_edits   V642Part's import and first base in the real Simple (first in the MANIFEST's simple_edits);
  launcher      the PARAMS key (its own line, last), v6.40's / v6.41's class-line checks (V642Part first), the "# v6.42."
                block, the gate's test entry.
``joined_v641(text)`` / ``source_v641(name, text)`` / ``launcher_v641(text)`` reverse them -- the final v6.41's text byte
for byte (tests/test_research_v6_42.py pins the digests) -- and leave a text without v6.42's edits as it is, so the chain is
idempotent; tests/_v641_undo.py applies them first (joined_v640, source_v640, launcher_v640, undo_tree, undo_node), so
every older pin inherits them.  The PARAMS key is taken out by its own line whatever its value or the value of the key
before it (a documented flip of either keeps the undo exact).  STRUCTURAL."""
from __future__ import annotations

import ast as _ast
import json
import re
from pathlib import Path

_HERE = Path(__file__).resolve().parent
EDITS = json.loads((_HERE / "_v642_edits.json").read_text(encoding="utf-8"))
JOINED_EDITS = tuple((e["old"], e["new"]) for e in EDITS["edits"])
_PARAMS_RE = re.compile(r' \\\nresearch_v642_l1t=[^\s"]* \\\nresearch_v642_l1c=[^\s"]*"\n')
LAUNCHER_EDITS = tuple((e["old"], e["new"]) for e in EDITS.get("launcher", ())
                       if "\nresearch_v642_l1t=" not in e["new"])          # the PARAMS lines: by their own pattern below
BASE_JOINED_SHA256 = EDITS["base_joined_sha256"]           # the final v6.41's joined Simple (MANIFEST v642_base_sha256)
JOINED_SHA256 = EDITS["joined_sha256"]                     # v6.42's joined Simple (MANIFEST pre_split_sha256)
LAUNCHER_BASE_SHA256 = EDITS.get("launcher_base_sha256")
SWITCHES = ("research_v642_l1t", "research_v642_l1c")
SHIPPED = {"research_v642_l1t": str(EDITS.get("launcher_l1t_default", "0")),
           "research_v642_l1c": str(EDITS.get("launcher_l1c_default", "0"))}
MODES = ("0", "1", "2", "3")
L1C_MODES = ("0", "1", "3", "4")
# The documented flips: the registered (l1t, l1c) pairs by a PARAMS edit alone (0 0 = v6.41's code on every path) --
# k149's L1T / L1TS / L1TI, k151's L1TC / L1TSC / L1TSX and k153's L1TSXA.
_PAIRS = {"OFF": ("0", "0"), "L1T": ("1", "0"), "L1TS": ("2", "0"), "L1TI": ("3", "0"), "L1TC": ("1", "1"),
          "L1TSC": ("2", "1"), "L1TSX": ("2", "3"), "L1TSXA": ("2", "4")}
FLIPS = {name: {"research_v642_l1t": t, "research_v642_l1c": c} for name, (t, c) in _PAIRS.items()
         if (t, c) != (SHIPPED["research_v642_l1t"], SHIPPED["research_v642_l1c"])}


def params_of(text):
    start = text.index('PARAMS="')
    body = text[start + len('PARAMS="'):text.index('"', start + len('PARAMS="'))].replace("\\\n", " ")
    return dict(t.split("=", 1) for t in body.split() if "=" in t)


def flip_of(text):
    """The documented v6.42 flip a launcher ships ("" = the shipped values), or None when its pair is none of them."""
    p = params_of(text)
    v = {k: p.get(k) for k in SWITCHES}
    if v == SHIPPED:
        return ""
    for name, flip in FLIPS.items():
        if flip == v:
            return name
    return None


def _unflip(text):
    """A documented flip's value back to the shipped one, on its own PARAMS line (anything else: as it is)."""
    if not flip_of(text):
        return text
    p = params_of(text)
    old = "\nresearch_v642_l1t=%s \\\nresearch_v642_l1c=%s\"\n" % (p["research_v642_l1t"], p["research_v642_l1c"])
    new = "\nresearch_v642_l1t=%s \\\nresearch_v642_l1c=%s\"\n" % (SHIPPED["research_v642_l1t"], SHIPPED["research_v642_l1c"])
    return text.replace(old, new) if text.count(old) == 1 else text


V642_NEW = frozenset({"agents/strategy/research_v6_42_l1t.py", "agents/strategy/simple_parts/v6_42_part.py"})
V642_MODULES = frozenset({"agents/strategy/Strategy1_Research_Simple.py", "agents/strategy/simple_parts/MANIFEST.json"})
V642_PATHS = V642_NEW | V642_MODULES
V642_TESTS = frozenset({"tests/test_research_v6_42.py", "tests/_v642_undo.py", "tests/_v642_edits.json"})
MARKER = "self._v6_42_note(book_id, now_ts, inv, v6_40_g is None and book_id not in v6_23_foreign)"


def _reverse(text, pairs, what):
    for old, new in reversed(pairs):
        if text.count(new) != 1:
            raise AssertionError("v6.42 %s edit not found exactly once: %r" % (what, new[:80]))
        text = text.replace(new, old)
    return text


def is_v642_joined(text) -> bool:
    return isinstance(text, str) and MARKER in text


def joined_v641(text):
    """The joined Simple of v6.42 with v6.42's edits reversed: the final v6.41's joined text byte for byte.  A text that
    carries none of them comes back as it is."""
    if not isinstance(text, str) or not any(new in text for _old, new in JOINED_EDITS):
        return text
    return _reverse(text, JOINED_EDITS, "joined Simple")


def source_v641(name, text):
    """agents/strategy/<name> with v6.42's edits reversed (Simple's name takes the joined text); any other file comes
    back as it is (v6.42 edits no other module)."""
    if not isinstance(text, str):
        return text
    if str(name).rsplit("/", 1)[-1] == "Strategy1_Research_Simple.py":
        return joined_v641(text)
    return text


def launcher_v641(text):
    """v6.42's launcher with its edits reversed: the final v6.41's launcher byte for byte (a launcher without the v6.42
    block comes back as it is).  The two PARAMS keys go by their own lines, whatever the values around them."""
    if not isinstance(text, str) or "# v6.42." not in text:
        return text
    text = _reverse(text, LAUNCHER_EDITS, "launcher")
    if len(_PARAMS_RE.findall(text)) != 1:
        raise AssertionError("v6.42 PARAMS line not found exactly once")
    return _PARAMS_RE.sub('"\n', text, count=1)


# ---- the same undo on a parsed module (tests/_v641_undo.undo_tree runs this first) ----

SIMPLE_CLASS = "Strategy1_Research_Simple"
PART_MODULE = "simple_parts.v6_42_part"
_ATTRS = frozenset(SWITCHES)


def _names_v642(node) -> bool:
    """Does the node name a v6.42 identifier (a v6_42 / _v6_42 name or attribute, research_v642_l1t)?"""
    for n in _ast.walk(node):
        if isinstance(n, _ast.Name) and n.id.startswith(("v6_42", "_v6_42")):
            return True
        if isinstance(n, _ast.Attribute) and (n.attr.startswith("_v6_42") or n.attr in _ATTRS):
            return True
        if isinstance(n, _ast.Constant) and isinstance(n.value, str) and (n.value in _ATTRS or n.value.startswith("_v6_42")):
            return True
    return False


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
            if isinstance(st, _ast.If) and _names_v642(st.test):
                for inner in st.orelse:
                    _strip(inner)
                    out.append(inner)
                continue
            if isinstance(st, (_ast.Expr, _ast.Assign, _ast.AugAssign, _ast.AnnAssign)) and _names_v642(st):
                continue
            _strip(st)
            out.append(st)
        setattr(owner, field, out)


def undo_tree(tree):
    """v6.42's hooks undone in a parsed module, in place: its import and V642Part base, every if / statement naming a
    v6.42 identifier (an if keeps its else branch).  On the joined Simple == ast.parse(joined_v641(text)) exactly
    (tests/test_research_v6_42.py); a module without v6.42 names is left as it is.  Returns the tree."""
    tree.body = [st for st in tree.body if not (isinstance(st, _ast.ImportFrom) and st.module == PART_MODULE)]
    for st in tree.body:
        if isinstance(st, _ast.ClassDef) and st.name == SIMPLE_CLASS:
            st.bases = [b for b in st.bases if not (isinstance(b, _ast.Name) and b.id == "V642Part")]
            _strip(st)
    return tree


def undo_node(node):
    _strip(node)
    return node
