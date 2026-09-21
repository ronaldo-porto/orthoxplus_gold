"""v6.2.2: the startup seed at breadth.

The v6.2.1 relaunch on testnet UID 82 (2026-09-19 00:22, same research dir, session found with 90 v6.1 lots):
the A1.9.5 startup seed wrote 77 synthetic lots at the restart quote with fee 0 and PARKED them as inherited
(never quoted, never exited), skipped 36 more books over its 24 BASE bound, and never consulted the session's
restored lots (only the deferred reseed path did).  The managed universe then had nothing to manage.

One causal change in the startup seed: (1) its size bound is the universe's (two lots per book), applied
with the caps from update() before the first seed; (2) a position whose lots the session remembers is
ours -- the seed writes those lots (the validator's entry prices and fees) and does not park it.  Off
restores v6.2.1 exactly.
"""
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v62_breadth as br  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


# ------------------------------------------------------------------------------------------------
# 1. The pure caps
# ------------------------------------------------------------------------------------------------

def test_universe_caps_carry_the_seed_bound():
    assert br.universe_caps(128, 0.25)["research_a195_max_seed_abs_base"] == 64.0
    assert br.universe_caps(10, 0.5)["research_a195_max_seed_abs_base"] == 10.0
    assert br.universe_caps(0, 0.25)["research_a195_max_seed_abs_base"] == 0.5


# ------------------------------------------------------------------------------------------------
# 2. The seed-lot choice in a harness
# ------------------------------------------------------------------------------------------------

class _Lot:
    def __init__(self, book_id, net_base, price=204.33, tick=0):
        self.book_id = book_id
        self.net_base = net_base
        self.price = price
        self.tick = tick

    def as_tuple(self):
        return (int(self.tick), abs(float(self.net_base)), float(self.price), 0.0)


class _Base:
    def __init__(self, breadth=True, seed=True, restored=None, mismatch=False):
        self.research_v62_breadth = breadth
        self.research_v622_seed_at_breadth = seed
        self.restored = dict(restored or {})
        self.mismatch = mismatch
        self.asked = []

    def _v61_restored_side(self, book_id, side, target_qty):
        self.asked.append((int(book_id), side, float(target_qty)))
        if self.mismatch:
            raise RuntimeError("restore exploded")
        lots = self.restored.pop((int(book_id), side), [])
        total = sum(q for _t, q, _p, _f in lots)
        return lots if lots and abs(total - float(target_qty)) <= 1e-9 else []


_ns = {"_Base": _Base}
exec("class Harness(_Base):\n    pass\n", _ns)
for _name in ("_v62_on", "_v622_on", "_v622_count", "_v622_seed_lots"):
    exec("class Harness(Harness):\n" + textwrap.indent(textwrap.dedent(_method_source(_name)), "    "), _ns)
Harness = _ns["Harness"]


def test_restored_lots_replace_the_synthetic_lot_and_are_not_parked():
    own = [(48_811, 0.25, 201.92, -0.0002)]
    h = Harness(restored={(116, "longs"): own})
    lots, restored = h._v622_seed_lots(116, "longs", _Lot(116, 0.25, price=193.20))
    assert restored is True and lots == own                     # the validator's entry, not the quote
    assert h.asked == [(116, "longs", 0.25)]
    assert h._v622_counts == {"restored_books": 1}


def test_no_session_lots_means_the_synthetic_lot_as_before():
    h = Harness(restored={})
    lots, restored = h._v622_seed_lots(22, "shorts", _Lot(22, -0.25, price=273.28))
    assert restored is False and lots == [(0, 0.25, 273.28, 0.0)]
    assert h._v622_counts == {"synthetic_books": 1}


def test_a_position_that_no_longer_matches_the_session_is_synthetic():
    h = Harness(restored={(3, "longs"): [(1, 0.25, 216.26, 0.0)]})
    lots, restored = h._v622_seed_lots(3, "longs", _Lot(3, 0.3603))   # a partial fill since the save
    assert restored is False and lots == [(0, 0.3603, 204.33, 0.0)]


def test_off_or_breadth_off_never_asks_the_session():
    for h in (Harness(seed=False, restored={(1, "longs"): [(1, 0.25, 1.0, 0.0)]}),
              Harness(breadth=False, restored={(1, "longs"): [(1, 0.25, 1.0, 0.0)]})):
        lots, restored = h._v622_seed_lots(1, "longs", _Lot(1, 0.25, price=2.0))
        assert restored is False and lots == [(0, 0.25, 2.0, 0.0)] and h.asked == []


def test_a_failing_restore_falls_back_to_the_synthetic_lot():
    h = Harness(mismatch=True)
    lots, restored = h._v622_seed_lots(9, "longs", _Lot(9, 0.25, price=5.0))
    assert restored is False and lots == [(0, 0.25, 5.0, 0.0)]


# ------------------------------------------------------------------------------------------------
# 3. Wiring: the seed loop, the parking skip, the caps before the first seed, telemetry, launcher
# ------------------------------------------------------------------------------------------------

def test_seed_loop_uses_the_session_lots_and_skips_parking_them():
    src = _method_source("_a195_seed_inventory_from_venue")
    assert "seed_lots, restored = self._v622_seed_lots(int(lot.book_id), side, lot)" in src
    assert "pos[side].extend(seed_lots)" in src
    assert "if lot.residue_class == SEED_REAL and int(lot.book_id) not in restored_books:" in src
    assert 'payload["v622_restored_books"] = int(len(restored_books))' in src
    assert 'payload["v622_seed_abs_bound"]' in src
    # a restored book never reaches the v6.0.0 park loop: it is not in `inherited`
    assert src.index("restored_books: set[int] = set()") < src.index("for lot in split.tracker_lots:")
    assert src.index("self._a196_inherited_real = inherited") < src.index("for inherited_book, inherited_net in sorted(inherited.items()):")


def test_caps_are_applied_from_update_before_the_first_seed():
    upd = _method_source("update")
    assert "if self._v622_on():" in upd and "self._v62_apply_caps(state)" in upd
    assert upd.index("self._v62_apply_caps(state)") < upd.index("return super().update(state)")
    # the seed itself runs in respond(), after update()
    assert "self._a195_seed_inventory_from_venue(state)" in _method_source("respond")


def test_switch_defaults_on_and_telemetry_and_stats():
    init = _method_source("_init_build_switches")
    assert 'getattr(self.config, "research_v622_seed_at_breadth", True)' in init
    tele = _method_source("_v62_telemetry")
    assert "seed_at_breadth_on=int(self._v622_on())" in tele
    assert '"direct_v622_restored_books"' in SIMPLE
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_8"' in SIMPLE


def test_launcher_arm_params_guard_and_gate():
    assert "strategy1_direct_v6_2_8)" in LAUNCHER and "V622_BUILD=1 ;;" in LAUNCHER
    assert "research_v622_seed_at_breadth=1" in LAUNCHER
    assert "[preflight] v6.2.2 seed at breadth PASS" in LAUNCHER
    assert "tests/test_research_v6_2_2_seed_at_breadth.py" in LAUNCHER
