"""v6.2.7: the making leg, taken as far as a maker can take it.

Measured on UID 82 at v6.2.6 tick 3,000 against the live mainnet field (2026-09-20).  The field fits
``trading = 0.395*kappa + 0.25*making_rank + 0.25*skill_rank`` with a median residual of -0.0001 over
255 agents, and no maker in it holds kappa: 0 of 255 have kappa > 0.65 together with making rank > 0.5,
corr(kappa, making_rank) = -0.286, and the whole top 11 sit at kappa 0.4980-0.4998.  So rule A is
retired (it cost 25% of releases, 24% of fills and returned premium books 16 -> 11), rule B becomes
corrective rather than proportional (balance stalled at 60.9%, median book 0.663), and the exposure
bound and the seed bound are reconciled to one band (the v6.2.6 launch refused 125,681 placements,
99.93%, holding 63.97 BASE against a 64.0 cap it had itself seeded past).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v627_maker_ceiling as mc  # noqa: E402
import research_v626_balanced_maker as bm  # noqa: E402
import research_v625_cap_paced as cp  # noqa: E402
from research_v62_breadth import universe_caps  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


# ------------------------------------------------------------------------------------------------
# 1. The per-book balance, in the validator's own terms
# ------------------------------------------------------------------------------------------------

def test_balance_is_the_validators_own_ratio():
    assert mc.book_balance(10.0, 10.0) == pytest.approx(1.0)      # perfectly balanced
    assert mc.book_balance(30.0, 10.0) == pytest.approx(0.5)      # 2*10/40
    assert mc.book_balance(10.0, 30.0) == pytest.approx(0.5)      # symmetric in the sides
    # the measured portfolio at v6.2.6 tick 3,000: 809.81 buy against 711.58 sell
    assert mc.book_balance(809.81, 711.58) == pytest.approx(0.9354, abs=1e-4)


def test_a_book_with_no_capture_yet_has_no_balance_to_correct():
    assert mc.book_balance(0.0, 0.0) is None
    assert mc.book_balance(None, None) is None


def test_a_pair_that_sums_negative_is_as_unbalanced_as_it_gets():
    # the raw ratio would report +2.5 here, which would read as "balanced" and quote both sides
    assert 2 * min(10.0, -50.0) / (10.0 - 50.0) == pytest.approx(2.5)
    assert mc.book_balance(10.0, -50.0) == pytest.approx(-1.0)
    assert mc.book_balance(-1.0, -1.0) == pytest.approx(-1.0)


def test_one_negative_side_reads_negative_while_the_sum_is_positive():
    assert mc.book_balance(100.0, -10.0) == pytest.approx(-0.2222, abs=1e-4)


# ------------------------------------------------------------------------------------------------
# 2. Rule B, corrective: the surplus side waits for the book
# ------------------------------------------------------------------------------------------------

def _sides(clip, buy, sell, *, target=mc.BALANCE_TARGET, min_order=0.25):
    return mc.balance_gate_sides(
        clip=clip, buy_capture=buy, sell_capture=sell,
        min_order=min_order, lots_of=cp.lots_of, target=target,
    )


def test_a_balanced_book_quotes_both_sides_at_the_paced_clip():
    out = _sides(0.5, 100.0, 95.0)          # balance 0.974, over the 0.85 target
    assert out == {"buy": 0.5, "sell": 0.5}


def test_an_unbalanced_book_quotes_the_deficit_side_alone():
    out = _sides(0.5, 100.0, 60.0)          # balance 0.75, under target; sell is behind
    assert out == {"buy": 0.0, "sell": 0.5}
    out = _sides(0.5, 60.0, 100.0)          # mirrored
    assert out == {"buy": 0.5, "sell": 0.0}


def test_the_measured_median_book_is_corrected_not_merely_trimmed():
    """v6.2.6's proportional split is what stalled balance at 0.663."""
    buy, sell = 100.0, 49.7                 # balance 0.664, the measured median book
    v626 = bm.side_clips(clip=0.5, buy_capture=buy, sell_capture=sell,
                         min_order=0.25, lots_of=cp.lots_of)
    # proportional: the leading side is trimmed to a quarter but still quotes, so it keeps scoring 0
    assert v626["buy"] > 0.0
    v627 = _sides(0.5, buy, sell)
    assert v627["buy"] == 0.0 and v627["sell"] == 0.5


def test_both_sides_stop_only_when_neither_is_positive():
    assert _sides(0.5, -1.0, -2.0) == {"buy": 0.0, "sell": 0.0}
    # one side still positive: the negative side is the deficit and is the only one that can lift
    # the minimum, so it alone quotes
    assert _sides(0.5, 100.0, -10.0) == {"buy": 0.0, "sell": 0.5}


def test_a_fresh_book_quotes_both_sides():
    assert _sides(0.5, 0.0, 0.0) == {"buy": 0.5, "sell": 0.5}


def test_the_clip_stays_on_the_venue_grid_and_a_zero_clip_quotes_nothing():
    assert _sides(0.7, 0.0, 0.0) == {"buy": 0.5, "sell": 0.5}      # floored to whole min orders
    assert _sides(0.0, 10.0, 10.0) == {"buy": 0.0, "sell": 0.0}
    assert _sides(0.1, 0.0, 0.0) == {"buy": 0.25, "sell": 0.25}    # never below one min order


def test_the_target_is_the_only_knob_and_it_is_honoured():
    assert _sides(0.5, 100.0, 60.0, target=0.70)["buy"] == 0.5     # 0.75 clears a 0.70 target
    assert _sides(0.5, 100.0, 60.0, target=0.80)["buy"] == 0.0


def test_the_gate_never_scores_zero_size():
    """Size on the leading side cannot raise 2*min(buy, sell); the gate must never spend it."""
    for buy, sell in ((100.0, 10.0), (10.0, 100.0), (500.0, 499.0), (1.0, 0.0)):
        out = _sides(0.5, buy, sell)
        if out["buy"] and out["sell"]:
            assert mc.book_balance(buy, sell) >= mc.BALANCE_TARGET
        else:
            leader = "buy" if buy > sell else "sell"
            assert out[leader] == 0.0


# ------------------------------------------------------------------------------------------------
# 3. The caps, reconciled to one band
# ------------------------------------------------------------------------------------------------

def test_the_seed_bound_can_no_longer_exceed_the_exposure_bound():
    """The v6.2.6 defect: universe_caps set the seed bound to twice the exposure bound."""
    raw = universe_caps(128, 0.25)
    assert raw["research_a195_max_seed_abs_base"] == 2.0 * raw["research_max_total_abs_base"]
    out = mc.band_caps(raw, band_clips=cp.BAND_CLIPS)
    assert out["research_a195_max_seed_abs_base"] <= out["research_max_total_abs_base"]


def test_the_exposure_bound_is_the_band_and_the_seed_is_one_clip():
    out = mc.band_caps(universe_caps(128, 0.25), band_clips=2.0)
    assert out["research_max_total_abs_base"] == pytest.approx(64.0)   # 128 books x 2 clips x 0.25
    assert out["research_a195_max_seed_abs_base"] == pytest.approx(32.0)


def test_a_relaunch_always_keeps_a_full_clip_per_book_of_headroom():
    """The v6.2.6 launch arrived at 63.97 of a 64.0 cap and refused 99.93% of its placements."""
    n, clip = 128, 0.25
    out = mc.band_caps(universe_caps(n, clip), band_clips=cp.BAND_CLIPS)
    headroom = out["research_max_total_abs_base"] - out["research_a195_max_seed_abs_base"]
    assert headroom == pytest.approx(n * clip)
    assert headroom >= n * clip        # every book can still open one clip after a full seed


def test_the_band_scales_with_the_paced_clip():
    for clip in (0.25, 0.5, 1.0, 4.0):
        out = mc.band_caps(universe_caps(128, clip), band_clips=2.0)
        assert out["research_max_total_abs_base"] == pytest.approx(128 * 2.0 * clip)
        assert out["research_a195_max_seed_abs_base"] == pytest.approx(128 * clip)


def test_the_count_caps_are_untouched_by_the_band():
    raw = universe_caps(128, 0.25)
    out = mc.band_caps(raw, band_clips=2.0)
    for key in ("research_max_total_open_books", "research_max_active_open_books",
                "research_max_open_books", "max_managed_books_per_tick", "max_mm_books_per_tick"):
        assert out[key] == raw[key]


def test_band_caps_composes_with_the_v626_count_widening():
    out = mc.band_caps(bm.open_book_caps(universe_caps(128, 0.25)), band_clips=2.0)
    assert out["research_max_total_open_books"] == 256      # v6.2.6 widening survives
    assert out["research_max_total_abs_base"] == pytest.approx(64.0)
    assert out["research_a195_max_seed_abs_base"] == pytest.approx(32.0)


def test_band_caps_is_inert_on_caps_that_carry_no_exposure_bound():
    assert mc.band_caps({"research_max_open_books": 8}, band_clips=2.0) == {"research_max_open_books": 8}
    assert mc.band_caps({"research_max_total_abs_base": 0.0}, band_clips=2.0)[
        "research_max_total_abs_base"] == 0.0


def test_a_degenerate_band_never_shrinks_the_exposure_bound():
    out = mc.band_caps(universe_caps(128, 0.25), band_clips=0.1)
    assert out["research_max_total_abs_base"] == pytest.approx(32.0)   # clamped to at least 1 clip


# ------------------------------------------------------------------------------------------------
# 4. Rule A is retired
# ------------------------------------------------------------------------------------------------

def test_the_loss_budget_default_is_off():
    src = _simple("_v626_loss_budget_on")
    assert 'getattr(self, "research_v626_loss_budget", False)' in src
    assert 'getattr(self, "research_v626_loss_budget", True)' not in src


def test_the_launcher_ships_the_loss_budget_off_and_the_v627_switches_on():
    assert "research_v626_loss_budget=0" in LAUNCHER
    assert "research_v627_balance_gate=1" in LAUNCHER
    assert "research_v627_band_caps=1" in LAUNCHER


def test_the_budget_code_survives_behind_its_switch():
    """Retired, not deleted: the switch still turns rule A back on for a comparison arm."""
    src = _simple("_v623_lifted")
    assert "if not self._v626_loss_budget_on():" in src
    assert "v626_spend_allowed(" in src


# ------------------------------------------------------------------------------------------------
# 5. Wiring
# ------------------------------------------------------------------------------------------------

def test_the_switches_are_wired_and_composable():
    assert 'research_v627_balance_gate", True)' in _simple("_v627_balance_gate_on")
    assert 'research_v627_band_caps", True)' in _simple("_v627_band_caps_on")
    # the balance gate is a refinement of rule B and cannot outlive it
    assert "self._v626_capture_balance_on()" in _simple("_v627_balance_gate_on")


def test_the_gate_replaces_the_proportional_split_at_the_one_call_site():
    src = _simple("_v626_side_clips")
    assert "if self._v627_balance_gate_on():" in src
    assert "v627_balance_gate_sides(" in src
    assert "v626_side_clips(" in src          # the v6.2.6 path is still reachable with the gate off


def test_both_caps_sites_go_through_the_band():
    assert "self._v627_caps(v626_open_book_caps(v62_universe_caps(n, lot)))" in _simple("_v62_apply_caps")
    raiser = _simple("_v625_apply_caps")
    assert "raw = v62_universe_caps(n, lot if self._v627_band_caps_on() else V625_BAND_CLIPS * lot)" in raiser
    assert "caps = self._v627_caps(v626_open_book_caps(raw))" in raiser


def test_the_band_is_applied_exactly_once_on_the_pacing_path():
    """With the gate on the raiser passes the plain clip; with it off it keeps v6.2.5's arithmetic."""
    clip = 0.5
    on = mc.band_caps(universe_caps(128, clip), band_clips=cp.BAND_CLIPS)
    off = universe_caps(128, cp.BAND_CLIPS * clip)
    assert on["research_max_total_abs_base"] == pytest.approx(off["research_max_total_abs_base"])


def test_the_telemetry_reports_the_new_switches():
    for key in ("balance_gate_on=", "band_caps_on=", "maker_ceiling="):
        assert key in SIMPLE


def test_the_version_is_pinned_to_v6_2_7():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_7"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_7"' in SIMPLE
    assert "strategy1_direct_v6_2_7)" in LAUNCHER
    assert "V627_BUILD=1" in LAUNCHER


def test_the_preflight_guards_the_whole_build():
    for needle in ("v6.2.7 module is not imported",
                   "v6.2.7 does not retire the loss budget by default",
                   "v6.2.7 quotes are not gated on the book balance",
                   "v6.2.7 startup caps are not reconciled to the band",
                   "v6.2.7 pacing caps would apply the band twice",
                   "v6.2.7 seed bound is not one clip per book",
                   "[preflight] v6.2.7 maker ceiling PASS"):
        assert needle in LAUNCHER
    assert "tests/test_research_v6_2_7_maker_ceiling.py" in LAUNCHER


def test_the_module_version_is_stamped():
    assert mc.V627_MAKER_CEILING_VERSION == "maker_ceiling_v6_2_7"
