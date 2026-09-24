"""Every global name the strategy modules reference must exist at module level.

v6.2.6 shipped with ``V623_BOOK_LOSS`` used in ``_v623_lifted`` and never imported.  The suite did not
catch it: the harnesses execute a method's source inside a namespace they build themselves, so an
injected name hides a missing import, and ``py_compile`` only checks syntax.  On the testnet launch the
miner raised ``NameError`` on every tick from tick 3 -- no quotes, no exits, the validator's presence
gate dropping us -- until the import was added.

This is the guard for that whole class: Python's own scope analysis (``symtable``) resolves every name
in every function to a scope, and any name that resolves to the module's globals must actually be bound
there.  It runs on the modules the builds touch, needs no import of the agent itself, and costs
milliseconds.
"""
import builtins
import symtable
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"

# The overlay modules every v6.x build edits.  A module that is imported at test time would also be
# caught by importing it; these are the ones that are not importable without the agent's dependencies.
MODULES = [
    "Strategy1_Research_Simple.py",
    "research_v6214_touch_life.py",
    "research_v6215_order_life.py",
    "research_v63_trend_target.py",
]

# Strategy1_Research.py is frozen (test_research_v6_0_1_capacity::test_t4_frozen_files_are_untouched
# checks it against git), and it carries one pre-existing gap of exactly this kind: onStart's
# retained-history branch calls ``bt.logging.info`` while ``bt`` is bound only in the parent modules,
# so that branch raises NameError if it is ever taken (a simulation start with preserved history --
# the next mainnet boundary is a candidate).  Recorded here rather than fixed, because the fix is a
# one-line import into a frozen file and that is the owner's call.
KNOWN_FROZEN_GAPS = {"Strategy1_Research.py": {"bt"}}


def _unresolved(path: Path) -> set[str]:
    src = path.read_text()
    table = symtable.symtable(src, path.name, "exec")
    top = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported()}
    top |= set(dir(builtins))
    missing: set[str] = set()

    def walk(scope):
        for child in scope.get_children():
            for sym in child.get_symbols():
                if sym.is_referenced() and sym.is_global() and sym.get_name() not in top:
                    missing.add(sym.get_name())
            walk(child)

    walk(table)
    return missing


@pytest.mark.parametrize("name", MODULES)
def test_every_global_name_is_bound_at_module_level(name):
    path = STRATEGY / name
    if not path.exists():                       # the tree carries only what the build needs
        pytest.skip(f"{name} not in this tree")
    missing = _unresolved(path)
    assert not missing, f"{name} references undefined globals: {sorted(missing)}"


def test_the_guard_catches_a_missing_import():
    """The check itself, on a module that uses a name nobody bound."""
    broken = "import math\n\n\ndef f(x):\n    return MISSING_CONSTANT + math.floor(x)\n"
    table = symtable.symtable(broken, "broken.py", "exec")
    top = {s.get_name() for s in table.get_symbols() if s.is_assigned() or s.is_imported()}
    top |= set(dir(builtins))
    found = {
        sym.get_name()
        for child in table.get_children()
        for sym in child.get_symbols()
        if sym.is_referenced() and sym.is_global() and sym.get_name() not in top
    }
    assert found == {"MISSING_CONSTANT"}


def test_the_frozen_module_has_only_its_known_gap():
    """The frozen overlay is checked too, but its one known gap does not fail the gate."""
    path = STRATEGY / "Strategy1_Research.py"
    if not path.exists():
        pytest.skip("Strategy1_Research.py not in this tree")
    assert _unresolved(path) == KNOWN_FROZEN_GAPS["Strategy1_Research.py"]


def test_the_v6_2_6_constant_that_was_missing_is_imported_now():
    src = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
    assert "BOOK_LOSS as V623_BOOK_LOSS" in src
    assert "if status == V623_BOOK_LOSS:" in src
