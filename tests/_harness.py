# SPDX-License-Identifier: MIT
"""One reviewed AST extractor for the tests that compile a method's source in isolation.

No test imports Strategy1_Research_Simple -- the class needs the whole miner framework -- so the
per-build suites read the file as text, pull one method's source out of it, and exec that into a
small synthetic class.  Every build copied that extractor from whichever neighbouring file it
started from, and by 2026-09-18 there were **twelve** hand-written variants across 18 files.

The variants were not equivalent.  Ten resolved a name to its LAST definition, which is what
Python binds; two returned the FIRST match, because their ``return`` sat inside the walk loop
(tests/test_research_v5_0_2_dust_liveness.py and tests/test_research_v5_0_3_newcomer_observatory.py).
Strategy1_Research_Simple defines ``_research_final_validate_instructions`` twice, so those two
files would have handed a test the dead copy and let its assertions pass against code that never
runs -- the exact defect the repository already carries in production.  Neither file asks for that
name today, so consolidating on last-wins is a no-op now and a trap removed later.

The extractor is the only thing shared.  Each suite keeps its own harness class, because the way a
method is hosted decides what ``super()`` resolves to, and that is a per-suite decision.
"""
from __future__ import annotations

import ast
from typing import Callable

__all__ = ["class_defs", "copy_source", "defs_extractor", "extractor", "legacy_capability_attrs",
           "legacy_capability_stubs", "method_source", "read_source", "shadowed_definitions", "simple_text",
           "strategy_view"]

SIMPLE_NAME = "Strategy1_Research_Simple.py"
_SIMPLE_TEXT: str | None = None


def simple_text() -> str:
    """v6.39 S4 (k129): Strategy1_Research_Simple's text as the interpreter binds it.

    The agent class is split into part modules (agents/strategy/simple_parts/); this is the file with every
    moved chunk back at the line it was cut from (simple_parts/join.py): byte-identical to the single file of
    the split commit, so every pin, order check, AST strip and exec-by-name extraction in this suite reads
    what it read before the split.  A tree without parts gives the file as it is.  Read once per process.
    """
    global _SIMPLE_TEXT
    if _SIMPLE_TEXT is None:
        import importlib.util
        import pathlib
        strategy = pathlib.Path(__file__).resolve().parents[1] / "agents" / "strategy"
        join_py = strategy / "simple_parts" / "join.py"
        if join_py.is_file():
            spec = importlib.util.spec_from_file_location("_simple_parts_join", join_py)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _SIMPLE_TEXT = module.joined_text(str(strategy))
        else:
            _SIMPLE_TEXT = (strategy / SIMPLE_NAME).read_text(encoding="utf-8")
    return _SIMPLE_TEXT


_V641_MODULES = ("research_direct_idle_gc.py", "research_v629_reply_path.py", "research_v633_deep_layer.py",
                 "research_v6_38_fast_path.py", "models.py")


def read_source(path) -> str:
    """The text of a source file of the tree: Simple's is the joined text (simple_text), any other its file.

    v6.41 (k144): B1's five module files (research_v641_speed, scratchpad k141 P3) read as v6.40.1's -- their v6.41
    edits reversed by tests/_v641_undo.source_v640, byte for byte -- as every version test before v6.41 read them; the
    v6.41 changes themselves are pinned by tests/test_research_v6_41.py (and k141's equivalence tests)."""
    import pathlib
    p = pathlib.Path(path)
    if p.name == SIMPLE_NAME:
        return simple_text()
    text = p.read_text(encoding="utf-8")
    if p.name in _V641_MODULES:
        import sys as _sys
        here = str(pathlib.Path(__file__).resolve().parent)
        if here not in _sys.path:
            _sys.path.insert(0, here)
        import _v641_undo
        text = _v641_undo.source_v640(p.name, text)
    return text


def copy_source(src, dst) -> None:
    """A test's copy of a source file into its own directory: Simple's copy is the joined text."""
    import pathlib
    import shutil
    src, dst = pathlib.Path(src), pathlib.Path(dst)
    if src.name == SIMPLE_NAME:
        if dst.is_dir():
            dst = dst / src.name
        dst.write_text(simple_text(), encoding="utf-8")
    else:
        shutil.copy(src, dst)


_STRATEGY_VIEW: str | None = None


def strategy_view() -> str:
    """agents/strategy as the launcher's preflight sees it: a full COPY of the directory whose
    Strategy1_Research_Simple.py is the joined text (the launcher builds the same view, v6.39 S4; a copy, not links:
    a recursive grep does not follow symlinked files -- k129 21:4x JST).  A tree without parts: the real directory.
    Built once per process, in a temp directory."""
    global _STRATEGY_VIEW
    if _STRATEGY_VIEW is None:
        import pathlib
        import shutil
        import tempfile
        strategy = pathlib.Path(__file__).resolve().parents[1] / "agents" / "strategy"
        if not (strategy / "simple_parts" / "join.py").is_file():
            _STRATEGY_VIEW = str(strategy)
        else:
            import atexit
            holder = tempfile.mkdtemp(prefix="simple_view_")
            atexit.register(shutil.rmtree, holder, True)              # removed when the test process exits
            view = pathlib.Path(holder) / "strategy"
            shutil.copytree(strategy, view, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
            (view / SIMPLE_NAME).unlink()
            (view / SIMPLE_NAME).write_text(simple_text(), encoding="utf-8")
            _STRATEGY_VIEW = str(view)
    return _STRATEGY_VIEW


def _classes(tree: ast.Module, cls_name: str | None):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and (cls_name is None or node.name == cls_name):
            yield node


def class_defs(text: str, name: str, *, cls_name: str | None = None) -> list[str]:
    """Every definition of ``name`` in the class body, in source order."""
    tree = ast.parse(text)
    out: list[str] = []
    for cls in _classes(tree, cls_name):
        out += [ast.get_source_segment(text, n) for n in cls.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    return out


def method_source(text: str, name: str, *, cls_name: str | None = None) -> str:
    """The definition Python actually binds: the LAST one in the class body.

    Resolved by position, never by a name->node dict, because a shadowed method would otherwise
    hand back the dead copy.
    """
    defs = class_defs(text, name, cls_name=cls_name)
    if not defs:
        raise AssertionError(f"{name} is not defined in {cls_name or 'the parsed class body'}")
    return defs[-1]


def shadowed_definitions(text: str, *, cls_name: str | None = None) -> dict[str, int]:
    """{name: count} for every method defined more than once -- the dead copies."""
    tree = ast.parse(text)
    counts: dict[str, int] = {}
    for cls in _classes(tree, cls_name):
        for n in cls.body:
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                counts[n.name] = counts.get(n.name, 0) + 1
    return {k: v for k, v in counts.items() if v > 1}


def extractor(default_text: str, *, cls_name: str | None = None) -> Callable[..., str]:
    """A ``_method_source(name, text=None)`` bound to one source file.

    Accepts the optional second argument the older copies took, so a suite that extracts from more
    than one file keeps working unchanged.
    """
    def _method_source(name: str, text: str | None = None) -> str:
        return method_source(default_text if text is None else text, name, cls_name=cls_name)

    return _method_source


def defs_extractor(default_text: str, *, cls_name: str | None = None) -> Callable[..., list[str]]:
    """A ``_class_defs(name, text=None)`` bound to one source file."""
    def _class_defs(name: str, text: str | None = None) -> list[str]:
        return class_defs(default_text if text is None else text, name, cls_name=cls_name)

    return _class_defs


# ---- capability stubs for pre-v6 suites -----------------------------------------------------------

def legacy_capability_stubs(agent, *, names=None, min_order: float = 0.25):
    """Give a pre-v6 harness the v6 methods its hosting code now calls directly.

    Until 2026-09-18 the overlay reached these through ``getattr(self, "<method>", None)`` and fell
    back when the probe missed.  A harness that compiles one hosting method without also compiling
    the v6 method therefore exercised the FALLBACK, and that is what these suites assert: they are
    contracts for their own build (A1.9.x, v5.0.x), not for v6.0.0/v6.0.1.  The live v6 path is
    covered by tests/test_research_v6_0_0_short_lots.py and tests/test_research_v6_0_1_capacity.py.

    So each stub here reproduces the fallback branch exactly, and installing one is a statement
    that the suite means the pre-v6 behaviour.  A suite that wants the real thing should compile
    the real method instead -- that is the honest way to change what it asserts.
    """
    stubs = _legacy_stub_map(min_order)
    for name, fn in stubs.items():
        if names is not None and name not in names:
            continue
        if not hasattr(agent, name):
            setattr(agent, name, fn)
    return agent


def legacy_capability_attrs(min_order: float = 0.25, *, skip=()):
    """The same stubs as staticmethods, for a harness built with ``type("Harness", ...)``.

    A plain function in a class body would be bound and swallow the first argument as ``self``.
    """
    return {k: staticmethod(v) for k, v in _legacy_stub_map(min_order).items() if k not in skip}


def _legacy_stub_map(min_order: float):
    return {
        # `if v600_chooser is not None: v600_chooser(exit_kwargs)` -- absent means no adjustment.
        "_v600_chooser_kwargs": lambda exit_kwargs: None,
        # `if executable is not None: inventory_qty = executable(...)` -- absent means unchanged.
        "_v600_executable_qty": lambda qty, min_o=min_order: qty,
        # absent means the pre-v6 rule: eps < |q| < min_order.
        "_v600_counts_as_dust": (lambda book_id, qty, *, eps, min_order=min_order:
                                 float(qty) > float(eps) and float(qty) + 1e-12 < float(min_order)),
        # `if v600_park is not None and ...` -- absent means nothing is parked.
        "_v600_note_inherited_clip": lambda book_id, size: None,
        # `if provider is not None` -- absent means no score-copy override.
        "_v504_mirror_inputs": lambda now_ts: None,
        # `if v601_workable is None or v601_workable(...)` -- absent counted every dust book.
        "_v601_is_workable": lambda book_id, qty, *, min_order=min_order: True,
        # `dust_now if v601_reserve is None else ...` -- absent reserved for every dust book.
        "_v601_reserve_dust": lambda diag, dust_now: dust_now,
    }
