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

__all__ = ["class_defs", "defs_extractor", "extractor", "legacy_capability_attrs",
           "legacy_capability_stubs", "method_source", "shadowed_definitions"]


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
