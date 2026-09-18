"""A feature must not be able to turn itself off silently.

This repository's recurring defect is not a wrong decision, it is a build that ships inert: the
switch parses, the telemetry says ``enabled=1``, and the code it was supposed to gate never runs.
A1.9.3's breadth admission did it, the queue-preservation work did it, and the pattern that would
do it next is the capability probe:

    v601_reserve = getattr(self, "_v601_reserve_dust", None)
    reserve_dust_now = dust_now if v601_reserve is None else int(v601_reserve(diag, dust_now))

Rename or move ``_v601_reserve_dust`` -- exactly what refactoring an 11,000-line class does -- and
the probe quietly returns None, v6.0.1 reverts to holding a slot for every dust book, and
``V601_RESERVE_STATE`` still prints ``enabled=1`` because that field reports the config switch, not
the wiring.  An acceptance gate would read the result as a null result rather than a broken build.

The probes are not removed here, because ~17 test harnesses exec one method's source into a
synthetic class and rely on the None branch to run at all; removing them belongs with the harness
consolidation.  What these tests do instead is make the failure loud: every probe must resolve, and
no new probe may appear.
"""
import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE_PATH = STRATEGY / "Strategy1_Research_Simple.py"
SIMPLE = SIMPLE_PATH.read_text(encoding="utf-8")

# The class chain an attribute may legitimately come from.
CHAIN = ("Strategy1_Research_Simple.py", "Strategy1_Research.py", "DetailedTemplateAgent.py",
         "BaseStrategy.py", "AdaptiveAgent.py")

# Names that resolve at runtime but are not assigned anywhere in the chain's source.
ALLOWED_UNRESOLVED = {
    "uid",                                      # supplied by the taos neuron framework
    "research_a193_breadth_admission_enabled",  # known orphan: read at one site, assigned nowhere;
                                                # retires with the dead A1.9.3 breadth override
}

# The capability probes that exist today: probed method -> the method that probes it.
# A new entry means a new way for a feature to disappear silently; think before adding one.
KNOWN_PROBES: set[tuple[str, str]] = set()
# Empty since 2026-09-18: all ten probes became direct calls.  The set stays because the
# ratchet below is what keeps it empty -- a new probe is a new way for a feature to vanish.


def _class(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return tree, next((n for n in tree.body if isinstance(n, ast.ClassDef)), None)


def _chain_surface():
    """Every method name and every ``self.x = ...`` target in the class chain."""
    surface = set()
    for name in CHAIN:
        path = STRATEGY / name
        if not path.exists():
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                surface.add(node.name)
            elif (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                  and node.value.id == "self" and isinstance(node.ctx, ast.Store)):
                surface.add(node.attr)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Attribute):
                surface.add(node.target.attr)
    return surface


def _self_probes():
    """[(lineno, probed name, hosting method)] for every getattr(self, "<literal>", ...)."""
    _tree, cls = _class(SIMPLE_PATH)
    found = []
    for fn in cls.body:
        if not isinstance(fn, ast.FunctionDef):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                    and node.func.id == "getattr" and len(node.args) >= 2
                    and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                found.append((node.lineno, node.args[1].value, fn.name))
    return found


def test_every_self_probe_resolves_somewhere_in_the_class_chain():
    """A probe that resolves to nothing is a rename or a typo, and it fails silently."""
    surface = _chain_surface()
    unresolved = sorted({(name, line) for line, name, _host in _self_probes()
                         if name not in surface and name not in ALLOWED_UNRESOLVED})
    assert not unresolved, (
        "getattr(self, ...) names nothing in the class chain -- renamed, misspelled, or the "
        f"feature is dead: {unresolved}"
    )


def test_capability_probes_do_not_spread():
    """Each of these is a place a feature can vanish without an error; hold the line at today's."""
    surface = _chain_surface()
    methods = {n.name for n in _class(SIMPLE_PATH)[1].body if isinstance(n, ast.FunctionDef)}
    probes = {(name, host) for _line, name, host in _self_probes() if name in methods}
    new = probes - KNOWN_PROBES
    assert not new, f"new capability probe(s): {sorted(new)} -- call the method directly instead"
    gone = KNOWN_PROBES - probes
    assert not gone, (
        f"probe(s) removed: {sorted(gone)} -- good, but drop them from KNOWN_PROBES in the same "
        "commit, and give the test harnesses that relied on the None branch an explicit stub"
    )
    for name, _host in KNOWN_PROBES:
        assert name in surface, f"{name} is probed but no longer exists: the feature is now inert"


def test_no_method_is_defined_twice_except_the_known_one():
    """Python binds the last definition; an earlier one is dead code that reads as live."""
    _tree, cls = _class(SIMPLE_PATH)
    seen, duplicated = set(), set()
    for node in cls.body:
        if isinstance(node, ast.FunctionDef):
            (duplicated if node.name in seen else seen).add(node.name)
    # _research_final_validate_instructions still has a shadowed copy that three tests pin;
    # retiring it is its own build.  _direct_order_client_id was removed in the v6.0.3 refactor.
    assert duplicated == {"_research_final_validate_instructions"}, sorted(duplicated)

# ---- the same rule for the tests themselves ------------------------------------------------------

def test_no_suite_reimplements_the_method_extractor():
    """A hand-copied extractor is how a test comes to assert against code that never runs.

    Twelve variants existed on 2026-09-18 and two of them returned the FIRST definition of a name,
    which for a shadowed method is the dead one.  They now share tests/_harness.py; a delegating
    adapter is fine, a re-implementation is not.
    """
    offenders = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (isinstance(node, ast.FunctionDef) and node.name in ("_method_source", "_class_defs")):
                continue
            calls = {n.func.attr for n in ast.walk(node)
                     if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            if "parse" in calls or "get_source_segment" in calls:
                offenders.append(f"{path.name}::{node.name}")
    assert not offenders, (
        f"these re-implement the extractor instead of importing it: {offenders} -- "
        "use `from _harness import extractor` (see tests/_harness.py)"
    )


def test_every_suite_that_extracts_methods_uses_the_shared_extractor():
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        src = path.read_text(encoding="utf-8")
        if "_method_source(" not in src and "_class_defs(" not in src:
            continue
        assert "from _harness import" in src, path.name
