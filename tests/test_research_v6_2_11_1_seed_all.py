"""v6.2.11.1: with every book lifted, a restart tracks every inherited position.

The v6.2.11 relaunch of testnet UID 67 (2026-09-22 23:27 JST) held 414 base on 128 books after v6.2.10.  The
A1.9.5 startup seed walks books largest-first and stops at research_a195_max_seed_abs_base, which v6.2.2/v6.2.7
derive from the 0.25-lot universe caps (~32 base after a restart): A195_INVENTORY_SEED seeded 3 books (31.99
base) and skipped 125 "over bound".  The rest were orphaned -- traded as flat, never exited -- while their
mark-to-market kept moving the validator's alpha.  Under R1 the positions are meant to be released, so every
one is seeded; the exposure caps still block new adds until the releases bring the total under them.
"""
import math
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6211_score_logic as sl  # noqa: E402
import research_direct_inventory_truth as it  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


def _uid67_like():
    """128 books: 40 large positions (4-16 base) and 88 small ones, ~414 base in all."""
    venue = {}
    for b in range(40):
        venue[b] = (-1) ** b * (4.0 + 12.0 * (b % 7) / 6.0)
    for b in range(40, 128):
        venue[b] = (-1) ** b * (0.25 + 0.25 * (b % 4))
    return venue


def _plan(venue, *, max_books, max_abs):
    return it.build_seed_plan(
        venue_net_by_book=venue, local_net_by_book={}, mid_by_book={b: 300.0 for b in venue},
        min_order=0.25, tick=0, max_books=max_books, max_abs_base=max_abs,
    )


def test_the_restart_bound_orphans_most_of_a_large_inherited_book():
    venue = _uid67_like()
    plan = _plan(venue, max_books=160, max_abs=32.0)
    assert plan.truncated and len(plan.lots) < 10 and len(plan.skipped_over_bound) > 100


def test_seed_all_bounds_track_every_inherited_position():
    venue = _uid67_like()
    books, abs_bound = sl.seed_all_bounds(venue)
    plan = _plan(venue, max_books=books, max_abs=abs_bound)
    assert not plan.truncated and plan.skipped_over_bound == ()
    assert len(plan.lots) == 128
    assert math.isclose(plan.total_abs_base, sum(abs(v) for v in venue.values()))


def test_the_bound_is_finite_because_the_planner_reads_infinity_as_its_default():
    venue = _uid67_like()
    assert _plan(venue, max_books=500, max_abs=float("inf")).truncated      # inf -> the 24-base default
    books, abs_bound = sl.seed_all_bounds(venue)
    assert math.isfinite(abs_bound) and abs_bound > sum(abs(v) for v in venue.values())
    assert books > len(venue)


def test_seed_all_bounds_ignore_junk_and_never_return_zero():
    assert sl.seed_all_bounds({}) == (2, 1.0)
    books, abs_bound = sl.seed_all_bounds({1: "x", 2: float("nan"), 3: -2.5, 4: None})
    assert books == 2 and abs_bound == 3.5


def test_the_call_site_lifts_the_bound_only_with_both_switches():
    src = _simple("_a195_seed_inventory_from_venue")
    gate = src.index('seed_all = bool(getattr(self, "research_v62111_seed_all", False)) and bool(')
    assert 'getattr(self, "research_v6211_lift_all", False)' in src[gate:gate + 200]
    assert src.index("seed_books_bound, seed_abs_bound = v62111_seed_all_bounds(venue)") < src.index("plan = build_seed_plan(")
    assert "max_books=seed_books_bound," in src and "max_abs_base=seed_abs_bound," in src


def test_the_unpriced_route_and_the_log_use_the_same_bound():
    src = _simple("_a195_seed_inventory_from_venue")
    assert "remaining_books=int(seed_books_bound) - len(plan.lots)," in src
    assert "remaining_abs=float(seed_abs_bound) - float(plan.total_abs_base)," in src
    assert 'payload["v62111_seed_all"] = int(seed_all)' in src
    assert 'payload["v62111_seed_abs_bound"] = float(seed_abs_bound)' in src


def test_switch_default_on_declared_and_reported():
    assert 'getattr(self.config, "research_v62111_seed_all", True)' in SIMPLE
    assert "research_v62111_seed_all=1" in LAUNCHER
    assert "seed_all_on=int(" in SIMPLE
    assert sl.V62111_SEED_ALL_VERSION == "seed_all_v6_2_11_1"


def test_launcher_guards_and_gate_list():
    assert 'echo "[preflight] v6.2.11.1 seed all PASS"' in LAUNCHER
    assert "tests/test_research_v6_2_11_1_seed_all.py" in LAUNCHER
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_13"' in SIMPLE    # a fix release keeps the arm
