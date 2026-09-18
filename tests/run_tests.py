#!/usr/bin/env python
# SPDX-License-Identifier: MIT
"""Run this repository's tests, with or without pytest installed.

    python tests/run_tests.py                     # every tests/test_*.py
    python tests/run_tests.py tests/test_x.py     # just these
    python tests/run_tests.py --list              # collect only, run nothing

Why this exists: the miner host has no pytest, and the launcher's preflight gate
(``RESEARCH_PREFLIGHT_ONLY=1``) invokes ``python -m pytest``, so the gate aborted under
``set -e`` before it could run a single test -- for every build in the v4.16 -> v6.0.3 series.
The only working runner lived in a session scratchpad, outside git.  This is that runner, in
the repository, with the fidelity gaps closed:

  * ``@pytest.mark.skip`` is honoured instead of silently running the test anyway;
  * ``pytest.skip(..., allow_module_level=True)`` at import is a skip, not a failure;
  * ``tmp_path`` and ``monkeypatch`` are supplied by signature, so fixture-taking tests run;
  * ``@pytest.mark.parametrize`` expands to one case per value;
  * only functions DEFINED in the file are collected, never ones it imported;
  * the module is registered in ``sys.modules`` before execution, so pickling works.

If the real pytest is importable it is used instead, and this file only supplies the paths.
"""
from __future__ import annotations

import importlib.util
import inspect
import os
import sys
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _install_stub_if_needed() -> bool:
    """Return True when the bundled stub is standing in for a missing pytest."""
    try:
        import pytest  # noqa: F401

        return False
    except ImportError:
        pass
    sys.path.insert(0, str(ROOT / "tests"))
    import _pytest_stub

    sys.modules["pytest"] = _pytest_stub
    return True


class _MonkeyPatch:
    """setattr/setenv/delenv with undo, which is all this suite uses."""

    def __init__(self):
        self._undo = []

    def setattr(self, target, name, value=None, raising=True):
        if isinstance(target, str):  # "module.attr" form
            mod_name, _, name = target.rpartition(".")
            target, value, raising = importlib.import_module(mod_name), name if value is None else value, raising
        had = hasattr(target, name)
        old = getattr(target, name, None)
        if raising and not had:
            raise AttributeError(f"{target!r} has no attribute {name!r}")
        setattr(target, name, value)
        self._undo.append(lambda: setattr(target, name, old) if had else delattr(target, name))

    def setenv(self, name, value):
        old = os.environ.get(name)
        os.environ[name] = str(value)
        self._undo.append(lambda: os.environ.__setitem__(name, old) if old is not None else os.environ.pop(name, None))

    def delenv(self, name, raising=True):
        old = os.environ.get(name)
        if old is None and raising:
            raise KeyError(name)
        os.environ.pop(name, None)
        self._undo.append(lambda: os.environ.__setitem__(name, old) if old is not None else None)

    def undo(self):
        while self._undo:
            self._undo.pop()()


def _fixtures(fn, stack):
    """Build kwargs for the builtin fixtures this suite uses; unknown ones are an error."""
    import tempfile

    kwargs = {}
    for name in inspect.signature(fn).parameters:
        if name == "tmp_path":
            tmp = tempfile.TemporaryDirectory()
            stack.append(tmp.cleanup)
            kwargs[name] = Path(tmp.name)
        elif name == "monkeypatch":
            mp = _MonkeyPatch()
            stack.append(mp.undo)
            kwargs[name] = mp
        else:
            raise RuntimeError(f"{fn.__name__}: unsupported fixture {name!r}")
    return kwargs


def _cases(fn):
    """[(label, kwargs)] -- one per parametrize case, or a single unparametrized case."""
    sets = list(getattr(fn, "__pytest_parametrize__", []))
    if not sets:
        return [("", {})]
    cases = [("", {})]
    for names, values in sets:
        grown = []
        for label, kwargs in cases:
            for value in values:
                vals = value if len(names) > 1 else (value,)
                extra = dict(zip(names, vals))
                tag = "-".join(str(v) for v in vals)
                grown.append((f"{label}[{tag}]" if label else f"[{tag}]", {**kwargs, **extra}))
        cases = grown
    return cases


def _skipped_exception():
    import pytest

    exc = getattr(getattr(pytest, "skip", None), "Exception", None)  # real pytest
    return exc or getattr(pytest, "Skipped", ())                     # tests/_pytest_stub.py


def _collect(path):
    """Import one test file. Returns (module, module_level_skip_reason)."""

    _SKIPPED = _skipped_exception()
    # The plain stem, not a decorated name: a test that spawns a subprocess (multiprocessing with
    # the spawn start method) makes the child re-import this module BY NAME to unpickle a
    # module-level function, and tests/ is on sys.path.  A prefixed name is unimportable there and
    # kills the child -- which is how tests/test_metagraph_worker.py took a whole run down.
    name = path.stem
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except _SKIPPED as exc:  # module-level skip
        return None, getattr(exc, "reason", "") or "skipped at module level"
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod, None


def main(argv):
    stubbed = _install_stub_if_needed()
    import pytest

    paths = [Path(a) for a in argv if not a.startswith("-")]
    if not paths:
        paths = sorted((ROOT / "tests").glob("test_*.py"))
    paths = [p if p.is_absolute() else (ROOT / p) for p in paths]
    listing = "--list" in argv

    # tests/ too, so a suite can `from _harness import extractor` the way pytest allows.
    sys.path[:0] = [str(ROOT), str(ROOT / "agents" / "strategy"), str(ROOT / "tests")]

    if not stubbed and not listing:
        # Real pytest is installed: it is the authority on markers, fixtures and collection.
        # Running our own collector beside it would report a different result on the same tree.
        return int(pytest.main(["-q", *[str(p) for p in paths]]))
    passed = skipped = failed = 0
    failures = []

    for path in paths:
        rel = path.relative_to(ROOT)
        try:
            mod, module_skip = _collect(path)
        except Exception:
            failed += 1
            failures.append((rel, "<import>", traceback.format_exc()))
            continue
        if mod is None:
            skipped += 1
            print(f"SKIP {rel} :: {module_skip}")
            continue

        names = [n for n in vars(mod) if n.startswith("test_")]
        for name in sorted(names):
            fn = getattr(mod, name)
            # Only functions defined in this file: `dir()` also yields imported ones.
            if not callable(fn) or getattr(fn, "__module__", None) != mod.__name__:
                continue
            reason = getattr(fn, "__pytest_skip__", None)
            if reason is not None:
                skipped += 1
                continue
            for label, params in _cases(fn):
                if listing:
                    print(f"{rel}::{name}{label}")
                    continue
                stack = []
                try:
                    fn(**{**_fixtures(fn, stack), **params})
                    passed += 1
                except _skipped_exception():
                    skipped += 1
                except Exception:
                    failed += 1
                    failures.append((rel, name + label, traceback.format_exc()))
                finally:
                    while stack:
                        stack.pop()()

    if listing:
        return 0
    for rel, name, tb in failures:
        print(f"\n=== FAIL {rel}::{name} ===\n{tb}")
    note = "  (pytest not installed: using tests/_pytest_stub.py)" if stubbed else ""
    print(f"\n{passed} passed, {skipped} skipped, {failed} failed{note}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
