"""v6.39 (k129): the structural undo every version test applies before it compares a module with an earlier build.

v6.39 = v6.38 M-FROZEN_2 (k126) + U2 stage 2 / S3 (scratchpad k129/CHANGES_S3.md): the run-length growth and the off-path
CPU behind research_v639_runlen, every decision as v6.38's.  Its edits are additions behind the switch, in four modules:
  Simple        the import of research_v6_39_runlen; four init statements (the switch, apply, the census cache, the
                idle collector's deferred_full); the override _research_note_realized_pnl_event; the switch branch in
                _v623_census (replaced here by its else branch: the original call); the recorder's opener keyword.
  idle_gc       IdleCollector's deferred_full parameter and its three attributes; the generation choice in collect_idle
                (the young-pass branch); _v639_generation; the snapshot's extra keys.
  deep_layer    _V639_SORTED_CACHE; class SweepRecord; the record's construction; the two cache branches.
  observatory   `item = None` after the write in StateRecorder._run.
``undo(tree)`` takes them out of a parsed module in place (any other module is left as it is) and returns the tree:
then it is the AST of v6.38 M-FROZEN_2's file (tests/test_research_v6_39.py pins that against M-FROZEN_2's digests,
per interpreter).  Idempotent.  tests/_v638_undo.undo applies it first, so every older strip inherits it.  STRUCTURAL."""
from __future__ import annotations

import ast

import _v640_undo   # v6.40 (k139): its exact text undo runs first in parse / source_v638 / launcher_v638

SWITCH = "research_v639_runlen"
V639_MODULE = "research_v6_39_runlen"
SIMPLE_CLASS = "Strategy1_Research_Simple"
V639_NAMES = frozenset({SWITCH, "_v6_39_switches", "_v6_39_census_cache", "v6_39_apply", "v6_39_note_event",
                        "v6_39_observatory_open", "v6_39_window_census_cached"})
V639_SIMPLE_METHODS = frozenset({"_research_note_realized_pnl_event"})
V639_GC_ATTRS = frozenset({"deferred_full", "idle_young_passes", "idle_young_total_ms"})
# The git file-list pins (t636 / t637 / t638, `git diff --name-only <commit> -- agents/strategy taos`) list a new file
# only once it is tracked (committed or staged), and a changed file whether staged or not.  V639_PATHS is v6.39's ONE
# named set of the paths it adds (V639_NEW: untracked until the user commits) and changes (V639_MODULES); every such pin
# subtracts V639_PATHS minus the paths it names itself, so it holds both untracked and tracked (k129, coordinator 15:2x).
# S4 (the split) adds its part files to V639_NEW (tests/test_research_v6_39.py pins V639_NEW == the new files on disk).
V639_NEW = frozenset({"agents/strategy/research_v6_39_runlen.py",
                      "agents/strategy/simple_parts/MANIFEST.json",
                      "agents/strategy/simple_parts/__init__.py",
                      "agents/strategy/simple_parts/join.py",
                      "agents/strategy/simple_parts/v6_29_part.py",
                      "agents/strategy/simple_parts/v6_30_part.py",
                      "agents/strategy/simple_parts/v6_31_part.py",
                      "agents/strategy/simple_parts/v6_32_part.py",
                      "agents/strategy/simple_parts/v6_36_part.py"})
V639_MODULES = frozenset({"agents/strategy/Strategy1_Research_Simple.py", "agents/strategy/research_direct_idle_gc.py",
                          "agents/strategy/research_v633_deep_layer.py", "agents/strategy/research_v5_observatory.py"})
V639_PATHS = V639_NEW | V639_MODULES
V639_TESTS = frozenset({"tests/test_research_v6_39.py", "tests/_v639_undo.py"})


def _class(tree, name):
    return next((n for n in getattr(tree, "body", ()) if isinstance(n, ast.ClassDef) and n.name == name), None)


def _mentions(node, names) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in names:
            return True
        if isinstance(n, ast.Attribute) and n.attr in names:
            return True
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in names:
            return True
    return False


def _is_switch_test(node):
    """``getattr(self, "research_v639_runlen", False)``: the switch as Simple reads it."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) == 3 and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
            and isinstance(node.args[1], ast.Constant) and node.args[1].value == SWITCH)


def _stmt_lists(node):
    for field in ("body", "orelse", "finalbody"):
        value = getattr(node, field, None)
        if isinstance(value, list) and all(isinstance(v, ast.stmt) for v in value):
            yield node, field
    for h in getattr(node, "handlers", None) or ():
        yield h, "body"


def _rewrite(node, drop_if):
    """Every statement list under ``node``: an ``if`` whose test ``drop_if`` names is replaced by its else branch."""
    for owner, field in list(_stmt_lists(node)):
        out = []
        for st in getattr(owner, field):
            while isinstance(st, ast.If) and drop_if(st.test):
                for inner in st.orelse[:-1]:
                    _rewrite(inner, drop_if)
                    out.append(inner)
                if not st.orelse:
                    st = None
                    break
                st = st.orelse[-1]
            if st is None:
                continue
            _rewrite(st, drop_if)
            out.append(st)
        setattr(owner, field, out)


def _drop_stmts(node, pred):
    """Every statement list under ``node``: statements ``pred`` names are removed (recursively)."""
    for owner, field in list(_stmt_lists(node)):
        out = []
        for st in getattr(owner, field):
            if pred(st):
                continue
            _drop_stmts(st, pred)
            out.append(st)
        setattr(owner, field, out)


def _undo_simple(tree, cls):
    tree.body = [st for st in tree.body if not (isinstance(st, ast.ImportFrom) and st.module == V639_MODULE)]
    cls.body = [st for st in cls.body if not (isinstance(st, ast.FunctionDef) and st.name in V639_SIMPLE_METHODS)]
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "_init_build_switches":
            fn.body = [st for st in fn.body if not _mentions(st, V639_NAMES)]
    _rewrite(cls, _is_switch_test)
    for n in ast.walk(cls):
        if isinstance(n, ast.Call):
            n.keywords = [k for k in n.keywords if not (k.arg is None and _mentions(k.value, {SWITCH}))]


class _GenerationIfExp(ast.NodeTransformer):
    """``self._gc.collect() if generation == 2 else self._gc.collect(generation)`` -> ``self._gc.collect()``."""

    def visit_IfExp(self, node):
        self.generic_visit(node)
        return node.body if _mentions(node.test, {"generation"}) else node


def _undo_idle_gc(tree, cls):
    for fn in cls.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        if fn.name == "__init__":
            args = fn.args
            kw = [(a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if a.arg != "deferred_full"]
            args.kwonlyargs, args.kw_defaults = [a for a, _ in kw], [d for _, d in kw]
            fn.body = [st for st in fn.body if not (isinstance(st, ast.Assign) and _mentions(st, V639_GC_ATTRS))]
        elif fn.name == "collect_idle":
            fn.body = [st for st in fn.body
                       if not (isinstance(st, ast.Assign) and len(st.targets) == 1
                               and isinstance(st.targets[0], ast.Name) and st.targets[0].id == "generation")
                       and not (isinstance(st, ast.If) and _mentions(st.test, {"generation"}))]
            _GenerationIfExp().visit(fn)
        elif fn.name == "snapshot":
            for n in ast.walk(fn):
                if isinstance(n, ast.Dict):
                    pairs = [(k, v) for k, v in zip(n.keys, n.values)
                             if not (k is None and _mentions(v, {"deferred_full"}))]
                    n.keys, n.values = [k for k, _ in pairs], [v for _, v in pairs]
    cls.body = [st for st in cls.body if not (isinstance(st, ast.FunctionDef) and st.name == "_v639_generation")]


def _undo_deep_layer(tree):
    tree.body = [st for st in tree.body
                 if not (isinstance(st, ast.Assign) and _mentions(st, {"_V639_SORTED_CACHE"}))
                 and not (isinstance(st, ast.ClassDef) and st.name == "SweepRecord")]
    _rewrite(tree, lambda test: _mentions(test, {"_V639_SORTED_CACHE"}))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "SweepRecord":
            n.func.id = "deque"


def _undo_observatory(tree, cls):
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "_run":
            _drop_stmts(fn, lambda st: isinstance(st, ast.Assign) and len(st.targets) == 1
                        and isinstance(st.targets[0], ast.Name) and st.targets[0].id == "item"
                        and isinstance(st.value, ast.Constant) and st.value.value is None)


def undo(tree):
    """v6.39's changes undone in a parsed module, in place (see the module docstring); returns the tree.  v6.40's hooks
    go first (tests/_v640_undo.undo_tree), so every older strip inherits them."""
    _v640_undo.undo_tree(tree)
    cls = _class(tree, SIMPLE_CLASS)
    if cls is not None:
        _undo_simple(tree, cls)
    gc_cls = _class(tree, "IdleCollector")
    if gc_cls is not None:
        _undo_idle_gc(tree, gc_cls)
    if _class(tree, "DeepLayer") is not None:
        _undo_deep_layer(tree)
    rec = _class(tree, "StateRecorder")
    if rec is not None:
        _undo_observatory(tree, rec)
    return tree


def parse(text):
    """``ast.parse(text)`` with v6.39 undone: v6.38 M-FROZEN_2's AST of the same file (v6.40's text edits reversed
    first: tests/_v640_undo.py)."""
    return undo(ast.parse(_v640_undo.joined_v639(text)))


# The launcher: v6.39's text edits as (v6.39 text, v6.38 text) pairs, applied in order, each found exactly once.
# Filled by the build (k129/build/patch_launcher_v639.py writes LAUNCHER_EDITS below from the same strings it inserts).
V639_LAUNCHER_EDITS = (
    ('# v6.39.  research_v639_runlen: U2 stage 2 / S3 (scratchpad k115 H6 / H7 / H14, built in k129) -- the run-length growth\n# and the off-path CPU taken out with every decision as v6.38\'s.  STORE: the session-persisted rolling realized-PnL store\n# and the v6.23 census -- v6.38 copies, re-validates and re-sorts every book\'s list on every realized fill and re-walks\n# every stored event on every state (O(store) a request, ~11 ms a reply at the live plateau); the v6.39 store keeps its\n# lists marked clean (sorted, validated: what v6.38\'s prune leaves), finds the window edges by bisection and recomputes a\n# book\'s census only when its list or its edges changed, by the original per-book loop over the same items in the same\n# order (the same floats).  SWEEP: the v6.33 deep layer\'s sweep record keeps its sorted copy keyed on its own content\n# (v6.38 sorts it on every depth() call).  IDLE: the A1.9.9.2 idle full collection runs only when the interpreter\'s own\n# generation-2 trigger is reached (generation-1 passes since the last full pass >= the threshold saved at install), a\n# young-generation pass on every other gap (v6.38: the full pass on every gap, ~59 ms CPU a request, ~0 objects found).\n# OBS: the observatory recorder writes gzip at the library\'s default level (v6.38: gzip.open\'s own, level 9; the\n# decompressed data identical).  Each sits behind "if switch: new, else: the original" (the store through Simple\'s own\n# overrides: Strategy1_Research.py is frozen).  STRUCTURAL: each change keeps what its original computed for every\n# reader (k129 eq tests: 155,424 store / census cases + 132,240 sweep cases, 0 mismatches; identity runs on a virtual\n# clock: every response and every persisted session byte-identical to v6.38\'s, the switch on and off).\n# research_v639_runlen ships 1 but may be 0 (off: v6.38\'s code on every path, by a PARAMS edit, no rebuild); the block\n# accepts exactly one of =0 / =1 and prints its state.\nif [[ "${V6_38_BUILD:-0}" == "1" ]]; then\n  [[ -f "$AGENT_PATH/research_v6_39_runlen.py" ]] || {\n    echo "ERROR: v6.39 the runlen module is missing." >&2\n    exit 1\n  }\n  for _v639_pat in \\\n      \'from research_v6_39_runlen import (\' \\\n      \'self.research_v639_runlen = self._as_bool(getattr(self.config, "research_v639_runlen", True))\' \\\n      \'self._v6_39_switches = v6_39_apply(self.research_v639_runlen)\' \\\n      \'self._a1992_idle_gc.deferred_full = True\' \\\n      \'census = v6_39_window_census_cached(\' \\\n      \'self._research_realized_pnl_events_by_book = v6_39_note_event(\' \\\n      \'**({"opener": v6_39_observatory_open} if getattr(self, "research_v639_runlen", False) else {}),\'; do\n    grep -qF -- "$_v639_pat" "$AGENT_PATH/Strategy1_Research_Simple.py" || {\n      echo "ERROR: v6.39 the agent lacks: $_v639_pat" >&2\n      exit 1\n    }\n  done\n  _v639_n=$(grep -cF -- \'if getattr(self, "research_v639_runlen", False):\' "$AGENT_PATH/Strategy1_Research_Simple.py" || true)\n  [[ "$_v639_n" == "1" ]] || {\n    echo "ERROR: v6.39 the agent reads its switch as a branch at ${_v639_n} sites, not the 1 of the census." >&2\n    exit 1\n  }\n  for _v639_pat in \\\n      \'("research_v633_deep_layer", "_V639_SORTED_CACHE"),\' \\\n      \'class CleanRows(list):\' \\\n      \'def prune_events(\' \\\n      \'def note_event(\' \\\n      \'def window_census_cached(\' \\\n      \'GZIP_LEVEL = zlib.Z_DEFAULT_COMPRESSION\'; do\n    grep -qF -- "$_v639_pat" "$AGENT_PATH/research_v6_39_runlen.py" || {\n      echo "ERROR: v6.39 the runlen module lacks: $_v639_pat" >&2\n      exit 1\n    }\n  done\n  for _v639_pat in \\\n      \'_V639_SORTED_CACHE = False\' \\\n      \'self.sweeps: deque = SweepRecord(maxlen=SWEEP_KEEP)\' \\\n      \'if _V639_SORTED_CACHE and isinstance(values, SweepRecord):\'; do\n    grep -qF -- "$_v639_pat" "$AGENT_PATH/research_v633_deep_layer.py" || {\n      echo "ERROR: v6.39 the deep layer lacks: $_v639_pat" >&2\n      exit 1\n    }\n  done\n  for _v639_pat in \\\n      \'deferred_full: bool = False,\' \\\n      \'generation = self._v639_generation() if self.deferred_full else 2\' \\\n      \'return 2 if int(get_count()[2]) >= int(saved[2]) else 1\'; do\n    grep -qF -- "$_v639_pat" "$AGENT_PATH/research_direct_idle_gc.py" || {\n      echo "ERROR: v6.39 the idle collector lacks: $_v639_pat" >&2\n      exit 1\n    }\n  done\n  _v639_re=\' research_v639_runlen=(0|1) \'\n  [[ " $PARAMS " =~ $_v639_re ]] || {\n    echo "ERROR: v6.39 build without research_v639_runlen=0 or 1 in PARAMS (the runlen state must be explicit)." >&2\n    exit 1\n  }\n  _v639_on="${BASH_REMATCH[1]}"\n  _v639_n=$(tr \' \' \'\\n\' <<< "$PARAMS" | grep -c \'^research_v639_runlen=\' || true)\n  [[ "$_v639_n" == "1" ]] || {\n    echo "ERROR: v6.39 build with research_v639_runlen given ${_v639_n} times in PARAMS (exactly once: 0 or 1)." >&2\n    exit 1\n  }\n  if [[ "$_v639_on" == "1" ]]; then _v639_state="on"; else _v639_state="off"; fi\n  echo "[preflight] v6.39 runlen: ${_v639_state}"\n  echo "[preflight] v6.39 runlen PASS (${_v639_state}: the clean rolling store and the cached census, the sorted sweep record, the deferred idle full pass and the default gzip level, each behind the switch; every decision as v6.38\'s)"\nfi\n\nif [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then\n',
     'if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then\n'),
    ('research_v638_c1_arm=0 \\\nresearch_v639_runlen=1"\n',
     'research_v638_c1_arm=0"\n'),
    ('      tests/test_research_v6_38_l3.py \\\n      tests/test_research_v6_39.py \\\n',
     '      tests/test_research_v6_38_l3.py \\\n'),
)
# S4 (the split): the preflight's Simple view and the restore before the launch -- applied after S3's, undone first
V639_LAUNCHER_EDITS_S4 = (
    ('[[ -f "$AGENT_PATH/Strategy1_Research_Simple.py" ]] || { echo "ERROR: Strategy1_Research_Simple.py missing" >&2; exit 1; }\n# v6.39 S4 (k129): the agent class is split into part modules (agents/strategy/simple_parts/*.py, one mixin each, the\n# methods moved verbatim).  Every check below reads Simple\'s text as the interpreter binds it: for the preflight AGENT_PATH\n# is a view directory -- a full COPY of agents/strategy (cp -a: every check, recursive greps included, reads exactly the\n# real files) with Strategy1_Research_Simple.py replaced by the file with every moved chunk back at its line\n# (simple_parts/join.py: byte-identical to the single file of the split commit, the original line numbering; the suite\'s\n# tests/_harness.simple_text reads the same text).  The view lives under $TMPDIR (mktemp -d), never in the tree, and is\n# removed on every exit (the EXIT trap; HUP / INT / TERM exit through it) and before the launch.  The real directory\'s\n# Simple and its parts are byte-compiled below, and the miner is launched from the real directory.\nAGENT_PATH_REAL="$AGENT_PATH"\nSIMPLE_VIEW=""\nif [[ -f "$AGENT_PATH/simple_parts/join.py" ]]; then\n  SIMPLE_VIEW="$(mktemp -d)" || { echo "ERROR: v6.39 no temp directory for the Simple view" >&2; exit 1; }\n  trap \'rm -rf -- "$SIMPLE_VIEW"\' EXIT\n  trap \'exit 129\' HUP\n  trap \'exit 130\' INT\n  trap \'exit 143\' TERM\n  cp -a "$AGENT_PATH"/. "$SIMPLE_VIEW"/ || { echo "ERROR: v6.39 the Simple view could not be copied" >&2; exit 1; }\n  rm -f -- "$SIMPLE_VIEW/Strategy1_Research_Simple.py"\n  python3 "$AGENT_PATH/simple_parts/join.py" "$AGENT_PATH" > "$SIMPLE_VIEW/Strategy1_Research_Simple.py" || {\n    echo "ERROR: v6.39 simple_parts/join.py could not rebuild Strategy1_Research_Simple.py" >&2\n    exit 1\n  }\n  for _part in "$AGENT_PATH/Strategy1_Research_Simple.py" "$AGENT_PATH"/simple_parts/*.py; do\n    python3 -c \'import sys; compile(open(sys.argv[1], encoding="utf-8").read(), sys.argv[1], "exec")\' "$_part" || {\n      echo "ERROR: v6.39 $_part does not compile" >&2\n      exit 1\n    }\n  done\n  AGENT_PATH="$SIMPLE_VIEW"\nfi\n',
     '[[ -f "$AGENT_PATH/Strategy1_Research_Simple.py" ]] || { echo "ERROR: Strategy1_Research_Simple.py missing" >&2; exit 1; }\n'),
    ('# v6.39 S4: the miner runs from the real directory (the split Simple + its parts), not the preflight view.\nAGENT_PATH="$AGENT_PATH_REAL"\nif [[ -n "$SIMPLE_VIEW" ]]; then rm -rf -- "$SIMPLE_VIEW"; SIMPLE_VIEW=""; trap - EXIT HUP INT TERM; fi\n# Keep the strategy directory importable in the actual PM2/miner process, not\n# only in the preflight subprocess.\nexport PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"\n',
     '# Keep the strategy directory importable in the actual PM2/miner process, not\n# only in the preflight subprocess.\nexport PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"\n'),
)


def launcher_v638(text):
    """v6.39's launcher text with v6.39's edits undone (S4's view first, then S3's block / key / gate entry): v6.38
    M-FROZEN_2's launcher, byte for byte (tests/test_research_v6_39.py pins its sha256).  Raises if an edit is not found
    exactly once (then this is not v6.39's launcher).  v6.40's launcher edits are reversed first (tests/_v640_undo.py)."""
    text = _v640_undo.launcher_v639(text)
    for new, old in V639_LAUNCHER_EDITS_S4 + V639_LAUNCHER_EDITS:
        if text.count(new) != 1:
            raise AssertionError("v6.39 launcher edit not found exactly once: %r" % new[:80])
        text = text.replace(new, old)
    return text


# S3's exact text edits of the agent modules it changes, as (v6.39 text, v6.38 text) pairs in the order they were
# applied (k129 build/patch_s3.py; build/s3_text_edits.json verified both ways against M-FROZEN_2b and FROZEN_S3).
# The older versions' frozen-module pins (t628-t633) compare source_v638(...) with their commits byte for byte.
V639_TEXT_EDITS = {
    'research_v633_deep_layer.py': (
        ("SWEEP_KEEP = 400\n# v6.39 (research_v6_39_runlen, S3.2): a book's sweep record keeps its sorted copy keyed on its own content, so depth()\n# and sweep_depths() sort once per change instead of once per call.  Written by the agent's constructor\n# (research_v6_39_runlen.apply); off here, so the module alone behaves as v6.38.\n_V639_SORTED_CACHE = False\n",
         'SWEEP_KEEP = 400\n'),
        ('def quantile(values: Iterable[float], q: float) -> float | None:\n    if _V639_SORTED_CACHE and isinstance(values, SweepRecord):\n        v = values.sorted_floats()\n    else:\n        v = sorted(float(x) for x in values)\n',
         'def quantile(values: Iterable[float], q: float) -> float | None:\n    v = sorted(float(x) for x in values)\n'),
        ('class SweepRecord(deque):\n    """v6.39: a book\'s sweep record -- the deque of v6.38 (maxlen SWEEP_KEEP) with its sorted copy kept beside it,\n    keyed on the record\'s content (tuple equality): every mutation path (observe, the v6.8 restore, a test) is\n    seen, and the copy is the same list `sorted(float(x) for x in record)` builds."""\n    __slots__ = ("_v639_key", "_v639_sorted")\n\n    def __init__(self, *args, **kwargs) -> None:\n        super().__init__(*args, **kwargs)\n        self._v639_key = None\n        self._v639_sorted = None\n\n    def sorted_floats(self) -> list[float]:\n        key = tuple(self)\n        if self._v639_sorted is None or self._v639_key != key:\n            self._v639_sorted = sorted(float(x) for x in key)\n            self._v639_key = key\n        return self._v639_sorted\n\n\nclass DeepBook:\n',
         'class DeepBook:\n'),
        ('        self.sweeps: deque = SweepRecord(maxlen=SWEEP_KEEP)\n',
         '        self.sweeps: deque = deque(maxlen=SWEEP_KEEP)\n'),
        ('        if _V639_SORTED_CACHE and isinstance(db.sweeps, SweepRecord):\n            v = db.sweeps.sorted_floats()\n        else:\n            v = sorted(float(x) for x in db.sweeps)\n        n = len(v)\n',
         '        v = sorted(float(x) for x in db.sweeps)\n        n = len(v)\n'),
    ),
    'research_direct_idle_gc.py': (
        ('        fallback_responses: int = A1992_FALLBACK_RESPONSES,\n        supported: bool | None = None,\n        deferred_full: bool = False,\n    ) -> None:\n',
         '        fallback_responses: int = A1992_FALLBACK_RESPONSES,\n        supported: bool | None = None,\n    ) -> None:\n'),
        ("        self.f2_held = False                # F2: collection held off since a request began (its idle pass ends it)\n        self.f2_holds = 0\n        # v6.39 (research_v6_39_runlen, S3.3): the full pass only when the interpreter's own trigger is reached\n        # (generation-1 passes since the last full pass >= the threshold saved at install); a young-generation\n        # pass on every other idle gap.  Off (the default): v6.38's full pass on every gap.\n        self.deferred_full = bool(deferred_full)\n        self.idle_young_passes = 0\n        self.idle_young_total_ms = 0.0\n",
         '        self.f2_held = False                # F2: collection held off since a request began (its idle pass ends it)\n        self.f2_holds = 0\n'),
        ('        started = self._clock()\n        generation = self._v639_generation() if self.deferred_full else 2\n        try:\n            unreachable = int(self._gc.collect() if generation == 2 else self._gc.collect(generation))\n',
         '        started = self._clock()\n        try:\n            unreachable = int(self._gc.collect())\n'),
        ('        ms = (self._clock() - started) * 1000.0\n        if generation != 2:                 # v6.39: a young pass -- the gap was used, the full-pass counters untouched\n            self.idle_young_passes += 1\n            self.idle_young_total_ms += ms\n            self.responses_since_idle = 0\n            return unreachable\n        self.idle_passes += 1\n',
         '        ms = (self._clock() - started) * 1000.0\n        self.idle_passes += 1\n'),
        ('    def _v639_generation(self) -> int:\n        """v6.39: 2 (the full pass) once the interpreter\'s own generation-2 trigger is reached -- generation-1\n        passes since the last full pass (gc.get_count()[2]) >= the threshold saved at install -- else 1; 2 where\n        the gc module has no get_count or no threshold was saved."""\n        get_count = getattr(self._gc, "get_count", None)\n        saved = self.saved_threshold\n        if get_count is None or saved is None or len(saved) < 3:\n            return 2\n        try:\n            return 2 if int(get_count()[2]) >= int(saved[2]) else 1\n        except Exception:\n            return 2\n\n    # ---- interpreter ---------------------------------------------------------------------------\n',
         '    # ---- interpreter ---------------------------------------------------------------------------\n'),
        ('            "tracked_objects": self.tracked_objects,\n            **({"deferred_full": 1, "idle_young_passes": self.idle_young_passes,\n                "idle_young_mean_ms": (round(self.idle_young_total_ms / self.idle_young_passes, 3)\n                                       if self.idle_young_passes else 0.0)} if self.deferred_full else {}),\n        }\n',
         '            "tracked_objects": self.tracked_objects,\n        }\n'),
    ),
    'research_v5_observatory.py': (
        ("            try:\n                self._write(item)\n            except Exception:\n                self.errors += 1\n                self.stopped_reason = STOP_ERROR\n                self._close()\n            item = None     # v6.39 (k115 H14): the previous state's books are not held while waiting for the next\n",
         '            try:\n                self._write(item)\n            except Exception:\n                self.errors += 1\n                self.stopped_reason = STOP_ERROR\n                self._close()\n'),
    ),
    'Strategy1_Research_Simple.py': (
        ('from research_v6_38_fast_path import (  # noqa: E402\n    apply as v6_38_apply,\n    feed as v6_38_feed,\n    last_taker_side as v6_38_last_taker_side,\n    trades_of as v6_38_trades_of,\n    trades_reset as v6_38_trades_reset,\n)\nfrom research_v6_39_runlen import (\n    apply as v6_39_apply,\n    note_event as v6_39_note_event,\n    observatory_open as v6_39_observatory_open,\n    window_census_cached as v6_39_window_census_cached,\n)\n',
         'from research_v6_38_fast_path import (  # noqa: E402\n    apply as v6_38_apply,\n    feed as v6_38_feed,\n    last_taker_side as v6_38_last_taker_side,\n    trades_of as v6_38_trades_of,\n    trades_reset as v6_38_trades_reset,\n)\n'),
        ('        self._v6_38_switches = v6_38_apply(self.research_v638_fast_path)\n        # v6.39 (research_v639_runlen): U2 stage 2 / S3 (scratchpad k115 H6 / H7 / H14, built in k129) -- the run-length\n        # growth (the rolling realized-PnL store and the v6.23 census, the v6.33 sweep sorts) and the off-path CPU (the\n        # A1.9.9.2 idle full pass, the observatory\'s gzip level) behind one switch; every decision as v6.38\'s.\n        # Off: v6.38\'s code on every path.\n        self.research_v639_runlen = self._as_bool(getattr(self.config, "research_v639_runlen", True))\n        self._v6_39_switches = v6_39_apply(self.research_v639_runlen)\n        self._v6_39_census_cache = {}\n        if self.research_v639_runlen and getattr(self, "_a1992_idle_gc", None) is not None:\n            self._a1992_idle_gc.deferred_full = True\n',
         '        self._v6_38_switches = v6_38_apply(self.research_v638_fast_path)\n'),
        ('        if getattr(self, "research_v639_runlen", False):\n            # v6.39 S3.1: the same census from a per-book cache (the book\'s list object + its window edges)\n            cache = getattr(self, "_v6_39_census_cache", None)\n            if cache is None:\n                cache = {}\n                self._v6_39_census_cache = cache\n            census = v6_39_window_census_cached(\n                cache, events, now=now,\n                lookback_ns=int(getattr(self, "research_kappa_lookback_ns", 10_800_000_000_000) or 10_800_000_000_000),\n                step_ns=step,\n                decimals=(V623_VOLUME_DECIMALS if decimals is None else decimals),\n            )\n        else:\n            census = v623_window_census(\n                events, now=now,\n                lookback_ns=int(getattr(self, "research_kappa_lookback_ns", 10_800_000_000_000) or 10_800_000_000_000),\n                step_ns=step,\n                decimals=(V623_VOLUME_DECIMALS if decimals is None else decimals),\n            )\n',
         '        census = v623_window_census(\n            events, now=now,\n            lookback_ns=int(getattr(self, "research_kappa_lookback_ns", 10_800_000_000_000) or 10_800_000_000_000),\n            step_ns=step,\n            decimals=(V623_VOLUME_DECIMALS if decimals is None else decimals),\n        )\n'),
        ('    def _research_note_realized_pnl_event(\n        self, book_id: int, *, timestamp=None, realized_pnl: float\n    ) -> None:\n        """v6.39 S3.1: the frozen base\'s method (Strategy1_Research.py) with research_rolling_economics\'\n        note_realized_pnl_event replaced by the v6.39 store\'s note (== by content; the untouched books keep their\n        list objects).  Off: the base\'s method."""\n        if not getattr(self, "research_v639_runlen", False):\n            return super()._research_note_realized_pnl_event(book_id, timestamp=timestamp, realized_pnl=realized_pnl)\n        ts = timestamp\n        if ts is None:\n            ts = getattr(self, "_research_last_sim_ts", None)\n        if ts is None:\n            ts = int(getattr(self, "_tick", 0) or 0)\n        try:\n            its = int(ts)\n        except (TypeError, ValueError):\n            its = int(getattr(self, "_tick", 0) or 0)\n        now = getattr(self, "_research_last_sim_ts", None)\n        if now is None:\n            now = its\n        self._research_realized_pnl_events_by_book = v6_39_note_event(\n            getattr(self, "_research_realized_pnl_events_by_book", {}),\n            book_id=int(book_id),\n            timestamp=its,\n            realized_pnl=float(realized_pnl),\n            now=int(now or its),\n            lookback_ns=int(getattr(self, "research_kappa_lookback_ns", 10_800_000_000_000)),\n        )\n\n    def _v623_lifted(self, book_id: int, state=None) -> tuple[bool, str]:\n',
         '    def _v623_lifted(self, book_id: int, state=None) -> tuple[bool, str]:\n'),
        ('                budget_basis=(\n                    BUDGET_DISK if bool(getattr(self, "research_v504_disk_budget", True)) else BUDGET_PAYLOAD\n                ),\n                # v6.39 S3.4: gzip at the library\'s default level (off: gzip.open\'s own, level 9)\n                **({"opener": v6_39_observatory_open} if getattr(self, "research_v639_runlen", False) else {}),\n            )\n            self._v503_recorder = recorder\n',
         '                budget_basis=(\n                    BUDGET_DISK if bool(getattr(self, "research_v504_disk_budget", True)) else BUDGET_PAYLOAD\n                ),\n            )\n            self._v503_recorder = recorder\n'),
    ),
}


def source_v638(name, text):
    """``text`` of agents/strategy/<name> with v6.39 S3's edits undone: M-FROZEN_2b's file byte for byte
    (tests/test_research_v6_39.py pins it).  A module v6.39 did not edit comes back as it is.  Raises if one of the
    edits is not found exactly once (then this is not v6.39's text of that module).  v6.40's edits are reversed first
    (tests/_v640_undo.py)."""
    text = _v640_undo.source_v639(name, text)
    for new, old in reversed(V639_TEXT_EDITS.get(str(name).rsplit("/", 1)[-1], ())):
        if text.count(new) != 1:
            raise AssertionError("v6.39 text edit of %s not found exactly once: %r" % (name, new[:80]))
        text = text.replace(new, old)
    return text
