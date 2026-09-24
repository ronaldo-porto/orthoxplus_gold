"""v6.2.1: at breadth every book with inventory reaches the managed set.

The tick-500 read of v6.2.0 on testnet UID 82 (2026-09-18 23:45): 118 of 128 books held a lot within
100 ticks; the A1.6.1 fast-path screen truncated its forced-inventory list to the candidate clamp
(20, hard limit 24); profiles were built for those alone; the manage loop skipped the rest without a
profile.  21-28 books got an exit per 100 ticks, exit liveness was 14%, the making mirror read 0.

One causal change: with breadth on, the screen's bound is the universe (``cap_override``), so the
forced-inventory list is never truncated and every inventory book is profiled and managed.  Off (or
breadth off) restores v6.2.0 exactly: the clamp stays.
"""
import sys
import textwrap
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_direct_fastpath as fp  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
FASTPATH = (STRATEGY / "research_direct_fastpath.py").read_text()
_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


def _rows(n_inventory, n_flat):
    rows = []
    for i in range(n_inventory):
        rows.append(fp.FastPathRow(book_id=i, priority=1.0, observations_remaining=0, qualified=True,
                                   has_inventory=True))
    for j in range(n_flat):
        rows.append(fp.FastPathRow(book_id=1000 + j, priority=float(j), observations_remaining=3,
                                   qualified=False, observable_edge_bps=5.0))
    return rows


# ------------------------------------------------------------------------------------------------
# 1. The pure selection
# ------------------------------------------------------------------------------------------------

def test_the_clamp_truncates_forced_inventory_without_the_override():
    """The v6.2.0 failure, reproduced: 118 inventory books, 20 survive the screen."""
    rows = _rows(118, 10)
    out = fp.select_fastpath_rows(rows, candidate_count=20, score_deficit=0, tick=1)
    assert len(out) == 20 and all(b < 118 for b in out)
    out2 = fp.select_fastpath_rows(rows, candidate_count=200, score_deficit=0, tick=1)
    assert len(out2) == fp.DIRECT_FASTPATH_MAX_CANDIDATES == 24


def test_the_universe_override_keeps_every_inventory_book():
    rows = _rows(118, 10)
    out = fp.select_fastpath_rows(rows, candidate_count=20, score_deficit=0, tick=1, cap_override=128)
    assert set(range(118)) <= set(out) and len(out) == 128
    out = fp.select_fastpath_rows(rows, candidate_count=20, score_deficit=5, tick=1, cap_override=128)
    assert set(range(118)) <= set(out) and len(out) == 128
    # inventory first, then the ranked flat books, no duplicates
    assert out[:118] == list(range(118)) and len(set(out)) == len(out)


def test_none_and_garbage_overrides_keep_the_clamp():
    rows = _rows(30, 30)
    base = fp.select_fastpath_rows(rows, candidate_count=20, score_deficit=0, tick=1)
    assert fp.select_fastpath_rows(rows, candidate_count=20, score_deficit=0, tick=1, cap_override=None) == base
    assert fp.select_fastpath_rows(rows, candidate_count=20, score_deficit=0, tick=1, cap_override="x") == base
    assert len(fp.select_fastpath_rows(rows, candidate_count=20, score_deficit=0, tick=1, cap_override=0)) == 1
    assert len(base) == 20


def test_dust_rows_still_take_no_slot_under_the_override():
    rows = _rows(10, 5) + [fp.FastPathRow(book_id=500, priority=9.0, observations_remaining=0, qualified=True,
                                          has_inventory=True, is_dust=True)]
    out = fp.select_fastpath_rows(rows, candidate_count=20, score_deficit=0, tick=1, cap_override=128)
    assert 500 not in out and set(range(10)) <= set(out)


# ------------------------------------------------------------------------------------------------
# 2. The helpers in a harness
# ------------------------------------------------------------------------------------------------

class _Base:
    def __init__(self, breadth=True, managed=True):
        self.research_v62_breadth = breadth
        self.research_v621_managed_universe = managed


_ns = {"_Base": _Base}
exec(textwrap.dedent("class Harness(_Base):\n    pass\n"), _ns)
for _name in ("_v62_on", "_v621_on", "_v621_cap_override"):
    exec("class Harness(Harness):\n" + textwrap.indent(textwrap.dedent(_method_source(_name)), "    "), _ns)
Harness = _ns["Harness"]


def test_cap_override_is_the_universe_when_on_and_none_otherwise():
    assert Harness()._v621_cap_override(128) == 128
    assert Harness()._v621_cap_override("128") == 128
    assert Harness()._v621_cap_override(0) is None
    assert Harness()._v621_cap_override("x") is None
    assert Harness(managed=False)._v621_cap_override(128) is None
    assert Harness(breadth=False)._v621_cap_override(128) is None        # requires breadth
    assert Harness(breadth=False, managed=True)._v621_on() is False


# ------------------------------------------------------------------------------------------------
# 3. Wiring: the screen passes the bound, the telemetry carries it, the launcher gates it
# ------------------------------------------------------------------------------------------------

def test_screen_passes_the_override_and_records_the_managed_set():
    src = _method_source("_research_fast_screen")
    assert "v621_cap = self._v621_cap_override(len(rows))" in src
    assert "cap_override=v621_cap," in src
    assert '"forced_inventory": int(len(forced_inventory))' in src
    # the bound is computed before the selection and recorded after the forced list exists
    assert src.index("v621_cap = self._v621_cap_override") < src.index("selected = select_fastpath_rows")
    assert src.index("forced_inventory = [r.book_id for r in rows") < src.index("self._v621_last = {")


def test_fastpath_signature_keeps_the_default_path():
    assert "cap_override: int | None = None" in FASTPATH
    assert "if cap_override is None:\n        cap = clamp_candidate_count(candidate_count)" in FASTPATH


def test_switch_defaults_on_and_telemetry_carries_the_managed_set():
    init = _method_source("_init_build_switches")
    assert 'getattr(self.config, "research_v621_managed_universe", True)' in init
    tele = _method_source("_v62_telemetry")
    assert "managed_universe_on=int(self._v621_on())" in tele
    assert 'managed_universe=dict(getattr(self, "_v621_last", {}) or {})' in tele
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_3_0"' in SIMPLE
    assert '"direct_v621_forced_inventory"' in SIMPLE


def test_launcher_arm_params_guard_and_gate():
    assert "strategy1_direct_v6_2_8)" in LAUNCHER and "V621_BUILD=1 ;;" in LAUNCHER
    assert "research_v621_managed_universe=1" in LAUNCHER
    assert "[preflight] v6.2.1 managed universe PASS" in LAUNCHER
    assert "grep -qF 'cap_override=v621_cap'" in LAUNCHER
    assert "tests/test_research_v6_2_1_managed_universe.py" in LAUNCHER
