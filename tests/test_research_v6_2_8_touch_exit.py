"""v6.2.8: close at the own touch, inventory against the band, quote only where a round trip can pay.

Measured on UID 82, v6.2.7 at tick 6,600 against v6.2.6 on the same sim (2026-09-21).  The loss-rate jump
(58% -> 95%) is the testnet fee regime -- at matched round-trip fee the two versions lose at the same rate
(20-40 bps/side 99.6% vs 99.4%) -- while the making damage is exit-path closes at the FAR touch: as a
fraction of the spread they moved +0.00 -> -0.96, and at matched size own-touch closes take 15 s and are
gross-positive while far-touch closes take 60-83 s and are gross-negative.  The frozen ladder reaches the
aggressive rung whenever urgency >= 0.50, and urgency sat at 0.45-0.55 on slot-era inventory references.
"""
import ast
import sys
import types
from contextlib import contextmanager
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v628_touch_exit as te  # noqa: E402
import research_v625_cap_paced as cp  # noqa: E402
import research_realization as rr  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")

BID, ASK, TICK = 100.00, 100.10, 0.01


# ------------------------------------------------------------------------------------------------
# S1: the aggressive maker rung is priced at the own touch
# ------------------------------------------------------------------------------------------------

def test_only_the_aggressive_rung_is_capped():
    assert te.cap_maker_action(te.ACTION_AGGRESSIVE) == te.ACTION_COMPETITIVE
    for other in (te.ACTION_PASSIVE, te.ACTION_COMPETITIVE, "SELECTIVE_TAKER_EXIT", "", None, "WAIT"):
        assert te.cap_maker_action(other) == other


def test_the_frozen_aggressive_price_is_the_far_touch():
    """The defect, pinned against the real pricer: a long selling 'aggressive' sits one tick over the bid."""
    long_aggr = rr.maker_exit_price(bid=BID, ask=ASK, long_position=True, action=te.ACTION_AGGRESSIVE, tick_size=TICK)
    short_aggr = rr.maker_exit_price(bid=BID, ask=ASK, long_position=False, action=te.ACTION_AGGRESSIVE, tick_size=TICK)
    assert long_aggr == pytest.approx(BID + TICK)       # far touch for a seller
    assert short_aggr == pytest.approx(ASK - TICK)      # far touch for a buyer


def test_the_capped_price_is_the_own_touch_on_both_sides():
    capped = te.capped_price_fn(rr.maker_exit_price)
    assert capped(bid=BID, ask=ASK, long_position=True, action=te.ACTION_AGGRESSIVE, tick_size=TICK) \
        == pytest.approx(ASK)                           # a long rests on the ask
    assert capped(bid=BID, ask=ASK, long_position=False, action=te.ACTION_AGGRESSIVE, tick_size=TICK) \
        == pytest.approx(BID)                           # a short rests on the bid


def test_the_capped_price_leaves_every_other_rung_exactly_as_frozen():
    capped = te.capped_price_fn(rr.maker_exit_price)
    for lp in (True, False):
        for action in (te.ACTION_PASSIVE, te.ACTION_COMPETITIVE):
            assert capped(bid=BID, ask=ASK, long_position=lp, action=action, tick_size=TICK) == \
                rr.maker_exit_price(bid=BID, ask=ASK, long_position=lp, action=action, tick_size=TICK)


def test_the_wrapper_counts_each_downgrade_and_is_marked():
    seen = []
    capped = te.capped_price_fn(rr.maker_exit_price, on_capped=lambda: seen.append(1))
    capped(bid=BID, ask=ASK, long_position=True, action=te.ACTION_AGGRESSIVE, tick_size=TICK)
    capped(bid=BID, ask=ASK, long_position=True, action=te.ACTION_COMPETITIVE, tick_size=TICK)
    assert len(seen) == 1
    assert getattr(capped, te.CAPPED_MARKER) is True
    assert capped.__wrapped__ is rr.maker_exit_price


class _Agent:
    def __init__(self, on=True):
        self.research_v628_rung_cap = on
        self.research_v62_breadth = True
        self._v628_counts = {}
        self._v628_errors = 0

    def _v62_on(self):
        return True


def _scoped_agent(on=True):
    """Bind the REAL scope method, with a stand-in frozen module carrying the real pricer."""
    frozen = types.SimpleNamespace(maker_exit_price=rr.maker_exit_price)
    ns = {
        "contextmanager": contextmanager,
        "importlib": types.SimpleNamespace(import_module=lambda name: frozen),
        "v628_capped_price_fn": te.capped_price_fn,
        "V628_CAPPED_MARKER": te.CAPPED_MARKER,
    }
    for name in ("_v628_rung_cap_on", "_v628_count", "_v628_rung_cap_scope"):
        tree = ast.parse(_simple(name))
        fn = tree.body[0]
        fn.decorator_list = [ast.Name(id="contextmanager", ctx=ast.Load())] if name == "_v628_rung_cap_scope" else []
        exec(compile(ast.fix_missing_locations(ast.Module(body=[fn], type_ignores=[])), "<v628>", "exec"), ns)
        setattr(_Agent, name, ns[name])
    return _Agent(on), frozen


def test_the_scope_swaps_the_frozen_pricer_and_restores_it():
    agent, frozen = _scoped_agent()
    original = frozen.maker_exit_price
    with agent._v628_rung_cap_scope():
        inside = frozen.maker_exit_price
        assert inside is not original and getattr(inside, te.CAPPED_MARKER)
        assert inside(bid=BID, ask=ASK, long_position=True, action=te.ACTION_AGGRESSIVE, tick_size=TICK) \
            == pytest.approx(ASK)
    assert frozen.maker_exit_price is original
    assert agent._v628_counts.get("rung_capped") == 1


def test_the_scope_restores_the_pricer_even_when_the_frozen_call_raises():
    agent, frozen = _scoped_agent()
    original = frozen.maker_exit_price
    with pytest.raises(RuntimeError):
        with agent._v628_rung_cap_scope():
            raise RuntimeError("frozen exit failed")
    assert frozen.maker_exit_price is original


def test_a_nested_scope_neither_rewraps_nor_restores_early():
    """_research_apply_unified_exit can reach _research_place_maker_exit: the inner scope must be inert."""
    agent, frozen = _scoped_agent()
    original = frozen.maker_exit_price
    with agent._v628_rung_cap_scope():
        outer = frozen.maker_exit_price
        with agent._v628_rung_cap_scope():
            assert frozen.maker_exit_price is outer          # not wrapped twice
        assert frozen.maker_exit_price is outer              # inner exit did not restore the original
    assert frozen.maker_exit_price is original


def test_the_scope_is_inert_when_the_switch_is_off():
    agent, frozen = _scoped_agent(on=False)
    original = frozen.maker_exit_price
    with agent._v628_rung_cap_scope():
        assert frozen.maker_exit_price is original


def test_all_three_frozen_price_producers_run_inside_the_scope():
    assert "with self._v628_rung_cap_scope():" in _simple("_research_apply_unified_exit")
    assert "self._v628_rung_cap_scope()" in _simple("_research_place_maker_exit")
    parked = _simple("_research_parked_touch_exit")
    assert "with self._v628_rung_cap_scope():" in parked
    assert "super()._research_parked_touch_exit(book_id, book, inventory)" in parked


def test_the_taker_rung_is_not_touched():
    """The emergency exit above urgency 0.70 is a cross, a different action; S1 only moves a maker price."""
    assert te.cap_maker_action("SELECTIVE_TAKER_EXIT") == "SELECTIVE_TAKER_EXIT"


# ------------------------------------------------------------------------------------------------
# S2: exit urgency measures inventory against the band
# ------------------------------------------------------------------------------------------------

def test_band_util_is_net_over_two_clips():
    assert te.band_inventory_util(net_base=1.2, clip=4.0, band_clips=2.0) == pytest.approx(0.15)
    assert te.band_inventory_util(net_base=-0.5, clip=0.25, band_clips=2.0) == pytest.approx(1.0)
    assert te.band_inventory_util(net_base=1.0, clip=0.0, band_clips=2.0) == 0.0


def test_the_frozen_reference_saturated_where_the_band_does_not():
    """1.2 BASE read 1.0 against the slot-era max_inventory_base; against a 4-base clip's band it is 0.15."""
    frozen = abs(1.2) / 1.2
    assert frozen == pytest.approx(1.0)
    assert te.band_inventory_util(net_base=1.2, clip=4.0, band_clips=cp.BAND_CLIPS) < 0.2


def test_the_binding_is_scoped_to_the_one_evaluation_call():
    src = _simple("_research_evaluate_realization")
    assert "self._v628_util_book = int(book_id)" in src
    assert "finally:" in src and "self._v628_util_book = prev" in src
    util = _simple("_inventory_util")
    assert "if book_id is None or not self._v628_band_inventory_on():" in util
    assert "return super()._inventory_util(inventory)" in util


def test_only_one_inventory_util_call_feeds_the_evaluation():
    """The scope is safe because the frozen evaluation calls _inventory_util exactly once (the urgency input)."""
    frozen = (STRATEGY / "Strategy1_Research.py").read_text()
    tree = ast.parse(frozen)
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef))
    fn = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_research_evaluate_realization")
    calls = [n for n in ast.walk(fn) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "_inventory_util"]
    assert len(calls) == 1


# ------------------------------------------------------------------------------------------------
# S3: quote only where a round trip can pay
# ------------------------------------------------------------------------------------------------

def test_spread_in_bps_of_mid():
    assert te.spread_bps(358.06, 358.75) == pytest.approx(19.25, abs=0.01)   # book 12 at tick 6,808
    assert te.spread_bps(None, 1.0) is None
    assert te.spread_bps(100.0, 100.0) is None                                # locked
    assert te.spread_bps(100.1, 100.0) is None                                # crossed


def test_viability_is_spread_over_two_fees():
    assert te.fee_viable(spread_bps_value=19.25, maker_fee_bps=45.0) is False    # the testnet regime
    assert te.fee_viable(spread_bps_value=19.25, maker_fee_bps=5.0) is True      # the old-sim regime
    assert te.fee_viable(spread_bps_value=20.0, maker_fee_bps=10.0) is False     # exactly break-even is not viable
    assert te.fee_viable(spread_bps_value=20.1, maker_fee_bps=10.0) is True


def test_a_rebate_book_is_always_viable():
    assert te.fee_viable(spread_bps_value=0.5, maker_fee_bps=-6.1) is True


def test_a_missing_spread_is_left_to_the_frozen_no_l1_verdict():
    assert te.fee_viable(spread_bps_value=None, maker_fee_bps=90.0) is True


def test_the_gate_stops_entries_only_and_is_counted():
    src = _simple("_v62_acquire")
    assert "if not self._v628_book_viable(book_id, facts):" in src
    assert "request[V628_REASON_FEE_UNVIABLE] = 0" in src
    viable = _simple("_v628_book_viable")
    assert "self._research_live_fee_bps(int(book_id), is_maker=True)" in viable
    # exits are not in the acquisition pass: the check never reaches an exit path
    assert "_v628_book_viable" not in _simple("_research_place_maker_exit")
    assert "_v628_book_viable" not in _simple("_research_apply_unified_exit")


# ------------------------------------------------------------------------------------------------
# S4: both sides, skewed, never frozen
# ------------------------------------------------------------------------------------------------

def _sk(clip, b, s):
    return te.skewed_sides(clip=clip, buy_capture=b, sell_capture=s, min_order=0.25, lots_of=cp.lots_of)


def test_a_fresh_or_balanced_book_quotes_both_sides_at_the_clip():
    assert _sk(0.5, 0.0, 0.0) == {"buy": 0.5, "sell": 0.5}
    assert _sk(0.5, 10.0, 10.0) == {"buy": 0.5, "sell": 0.5}


def test_the_deficit_side_gets_the_clip_and_the_surplus_a_trimmed_one():
    out = _sk(4.25, 100.0, 49.7)          # the measured median book: sell is behind
    assert out["sell"] == pytest.approx(4.25)
    assert 0.25 <= out["buy"] < 4.25


def test_no_side_is_ever_zero_so_no_book_can_freeze():
    """v6.2.6 and v6.2.7 zeroed both sides when both captures were negative -- an absorbing state."""
    for b, s in ((-80.4, -0.55), (-1.0, -2.0), (100.0, -10.0), (1e6, 1e-3), (-1e6, 1e6)):
        out = _sk(0.5, b, s)
        assert out["buy"] >= 0.25 and out["sell"] >= 0.25, (b, s, out)


def test_no_side_ever_exceeds_the_paced_clip():
    for clip in (0.25, 0.5, 4.25, 5.75):
        for b, s in ((0.0, 0.0), (100.0, 1.0), (1.0, 100.0), (-5.0, 5.0)):
            out = _sk(clip, b, s)
            assert max(out.values()) <= cp.lots_of(clip, 0.25) + 1e-12


def test_a_zero_clip_quotes_nothing():
    assert _sk(0.0, 1.0, 1.0) == {"buy": 0.0, "sell": 0.0}


def test_skew_supersedes_the_v627_gate_at_the_one_call_site():
    src = _simple("_v626_side_clips")
    i_628 = src.index("if self._v628_skew_sides_on():")
    i_627 = src.index("if self._v627_balance_gate_on():")
    assert i_628 < i_627


# ------------------------------------------------------------------------------------------------
# Switches, launcher, version
# ------------------------------------------------------------------------------------------------

def test_three_switches_default_on_and_are_declared():
    for key in ("research_v628_rung_cap", "research_v628_band_inventory", "research_v628_skew_sides"):
        assert f'getattr(self.config, "{key}", True)' in SIMPLE
        assert f"{key}=1" in LAUNCHER


def test_s3_is_retired_off_by_default_and_in_params():
    # Making and skill are fee-blind, so a net-of-fee entry gate costs making and buys no score.
    assert 'getattr(self.config, "research_v628_fee_viable", False)' in SIMPLE
    assert 'getattr(self, "research_v628_fee_viable", False)' in SIMPLE
    assert "research_v628_fee_viable=0 \\" in LAUNCHER
    assert "research_v628_fee_viable=1" not in LAUNCHER


def test_the_v627_gate_is_retired_in_params():
    assert "research_v627_balance_gate=0" in LAUNCHER


def test_the_telemetry_reports_the_build():
    for needle in ("rung_cap_on=", "band_inventory_on=", "fee_viable_on=", "skew_sides_on=", "touch_exit="):
        assert needle in SIMPLE


def test_the_version_and_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_13"' in SIMPLE
    assert "strategy1_direct_v6_2_8)" in LAUNCHER and "V628_BUILD=1 ;;" in LAUNCHER
    assert te.V628_TOUCH_EXIT_VERSION == "touch_exit_v6_2_8"


def test_the_preflight_guards_the_whole_build():
    for needle in ("v6.2.8 module is not imported",
                   "v6.2.8 unified exit is not priced through the capped rung",
                   "v6.2.8 maker exit placement is not priced through the capped rung",
                   "v6.2.8 parked-touch exit is not priced through the capped rung",
                   "v6.2.8 exit urgency does not measure inventory against the band",
                   "v6.2.8 entries are not gated on fee viability",
                   "v6.2.8 quotes are not skewed across both sides",
                   "[preflight] v6.2.8 touch exit PASS"):
        assert needle in LAUNCHER
    assert "tests/test_research_v6_2_8_touch_exit.py" in LAUNCHER
