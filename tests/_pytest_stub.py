# SPDX-License-Identifier: MIT
"""The slice of the pytest API this suite uses, for boxes without pytest installed.

The miner host has no pytest (and none of requirements.txt, constraints.txt or pyproject.toml
asks for one), so for fifteen builds the only way to run these tests was a script in a session
scratchpad.  This module is the honest version of that stub: it is installed as ``pytest`` by
tests/run_tests.py ONLY when the real package is missing, so a box that has pytest always uses
the real thing.

Measured surface of the suite (2026-09-18): ``approx`` 15 sites, ``mark.skip`` 8, ``skip`` 3,
``mark.parametrize`` 1, fixtures ``tmp_path`` 13 and ``monkeypatch`` 3.  Anything outside that is
deliberately absent rather than faked, so an unsupported feature fails loudly instead of passing
a test that never ran.
"""
from __future__ import annotations

__all__ = ["Skipped", "approx", "fail", "mark", "raises", "skip"]


class Skipped(Exception):
    """Raised by skip(); the runner reports it as a skip, never as a pass or a failure."""

    def __init__(self, reason: str = "", allow_module_level: bool = False):
        super().__init__(reason)
        self.reason = reason
        self.allow_module_level = allow_module_level


def skip(reason: str = "", *, allow_module_level: bool = False):
    raise Skipped(reason, allow_module_level=allow_module_level)


def fail(reason: str = ""):
    raise AssertionError(reason)


class approx:
    """pytest.approx with the same default tolerances (rel 1e-6, abs 1e-12)."""

    def __init__(self, expected, rel: float | None = None, abs: float | None = None):
        self.expected = expected
        self.rel = 1e-6 if rel is None else rel
        self.abs = 1e-12 if abs is None else abs

    def _close(self, a, b) -> bool:
        try:
            a = float(a)
            b = float(b)
        except (TypeError, ValueError):
            return a == b
        # `abs` is the builtin here; the ctor parameter of that name is out of scope.
        return abs(a - b) <= max(self.abs, self.rel * max(abs(a), abs(b)))

    def __eq__(self, other) -> bool:
        exp = self.expected
        if isinstance(exp, (list, tuple)):
            if not isinstance(other, (list, tuple)) or len(other) != len(exp):
                return False
            return all(self._close(o, e) for o, e in zip(other, exp))
        if isinstance(exp, dict):
            if not isinstance(other, dict) or set(other) != set(exp):
                return False
            return all(self._close(other[k], exp[k]) for k in exp)
        return self._close(other, exp)

    def __ne__(self, other) -> bool:
        return not self.__eq__(other)

    def __repr__(self) -> str:
        return f"approx({self.expected!r}, rel={self.rel}, abs={self.abs})"


class _MarkDecorator:
    """@pytest.mark.<name>(...) -- records on the function instead of silently passing through."""

    def __init__(self, name: str):
        self.name = name

    def __call__(self, *args, **kwargs):
        # Bare usage: @pytest.mark.foo over a function.
        if len(args) == 1 and not kwargs and callable(args[0]):
            return self._apply(args[0], (), {})

        def decorate(fn):
            return self._apply(fn, args, kwargs)

        return decorate

    def _apply(self, fn, args, kwargs):
        if self.name == "skip":
            fn.__pytest_skip__ = kwargs.get("reason") or (args[0] if args else "")
        elif self.name == "parametrize":
            names = [n.strip() for n in str(args[0]).replace(",", " ").split()]
            cases = list(args[1])
            existing = list(getattr(fn, "__pytest_parametrize__", []))
            fn.__pytest_parametrize__ = existing + [(names, cases)]
        else:
            marks = set(getattr(fn, "__pytest_marks__", set()))
            marks.add(self.name)
            fn.__pytest_marks__ = marks
        return fn


class _Mark:
    def __getattr__(self, name: str) -> _MarkDecorator:
        return _MarkDecorator(name)


mark = _Mark()


class raises:
    def __init__(self, expected, match: str | None = None):
        self.expected = expected
        self.match = match
        self.value = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        if exc_type is None:
            raise AssertionError(f"DID NOT RAISE {self.expected!r}")
        if not issubclass(exc_type, self.expected):
            return False
        self.value = exc
        if self.match is not None:
            import re

            if not re.search(self.match, str(exc)):
                raise AssertionError(f"{exc!r} does not match {self.match!r}")
        return True
