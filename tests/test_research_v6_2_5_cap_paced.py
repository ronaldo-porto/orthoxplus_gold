"""v6.2.5: two sides on every book, the clip paced to the validator's turnover cap.

Measured 2026-09-20 on the validator's own per-book gauges (testnet UID 82 and the live mainnet
field, scratchpad ``cap625/``): making per unit of maker volume is already the field median (ours
1.5 bps of the 3 sim-h window's maker volume against 1.55), but the volume is a seventh of the
field's (1.1M against 8.0M per window, which is 10 x 50,000 quote per book per 24 sim-h spread over
128 books) and half of the capture is discarded because a book holding a lot quotes only its exit
(balance 50% against the field's 86%).

Both rules here are the validator's own arithmetic: making counts 2*min(buy, sell) per book, so a
one-sided book scores its smaller side; and at the cap pace a round trip spends 2 x clip, so the
clip alone fixes the closing cadence kappa counts.  The smallest clip that still reaches the pace
serves both legs.
"""
import ast
import sys
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v625_cap_paced as cp  # noqa: E402
import research_v626_balanced_maker as bm  # noqa: E402
import research_v627_maker_ceiling as mc  # noqa: E402
import research_v628_touch_exit as te  # noqa: E402
from research_v62_breadth import (  # noqa: E402
    REASON_BUDGET, REASON_CROSSED, REASON_LIVE_ORDER, REASON_NO_BALANCE, REASON_NO_L1,
    REASON_OK, REASON_VOLUME_CAP, BookFacts,
)
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_v625_cap_paced.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")

S = 1_000_000_000
CAP = 500_000.0          # 10 x 50,000, the validator's per (uid, book) 24 sim-h cap
WINDOW_NS = 10_800 * S   # the kappa / making lookback


def facts(**kw):
    base = dict(
        book_id=7, best_bid=299.99, best_ask=300.01, net_base=0.0, live_order=False, cap_ok=True,
        quote_free=29_000.0, base_free=68.0, instructions_used=0, max_instructions=5,
    )
    base.update(kw)
    return BookFacts(**base)


# ------------------------------------------------------------------------------------------------
# 1. The band and the per-side verdict
# ------------------------------------------------------------------------------------------------

def test_the_band_is_one_held_clip_plus_one_opposite():
    assert cp.BAND_CLIPS == 2.0
    assert cp.band_for(0.25) == 0.5
    assert cp.band_for(1.0) == 2.0
    assert cp.band_for(-1.0) == 0.0


def test_a_flat_book_gets_both_sides_exactly_as_v6_2_0():
    out = cp.sides_verdict(facts(), clip=0.25, flat_eps=5e-5, two_sided=True)
    assert out == {"buy": REASON_OK, "sell": REASON_OK}
    assert cp.sides_verdict(facts(), clip=0.25, flat_eps=5e-5, two_sided=False) == out


def test_a_held_book_gets_its_adding_side_and_leaves_the_exit_side_alone():
    long_book = cp.sides_verdict(facts(net_base=0.25), clip=0.25, flat_eps=5e-5, two_sided=True)
    assert long_book == {"buy": cp.REASON_OK, "sell": cp.REASON_EXIT_SIDE}
    short_book = cp.sides_verdict(facts(net_base=-0.25), clip=0.25, flat_eps=5e-5, two_sided=True)
    assert short_book == {"sell": cp.REASON_OK, "buy": cp.REASON_EXIT_SIDE}


def test_the_adding_side_stops_at_the_band():
    at_band = cp.sides_verdict(facts(net_base=0.5), clip=0.25, flat_eps=5e-5, two_sided=True)
    assert at_band["buy"] == cp.REASON_BAND and at_band["sell"] == cp.REASON_EXIT_SIDE
    # the band scales with the clip: the same inventory is inside a 1.0 clip's band
    assert cp.sides_verdict(facts(net_base=0.5), clip=1.0, flat_eps=5e-5, two_sided=True)["buy"] == cp.REASON_OK


def test_two_sided_off_refuses_a_held_book_exactly_as_v6_2_4():
    out = cp.sides_verdict(facts(net_base=0.25), clip=0.25, flat_eps=5e-5, two_sided=False)
    assert out == {"buy": "NOT_FLAT", "sell": "NOT_FLAT"}


def test_dust_under_the_execution_epsilon_still_counts_as_flat():
    out = cp.sides_verdict(facts(net_base=4e-5), clip=0.25, flat_eps=5e-5, two_sided=True)
    assert out == {"buy": REASON_OK, "sell": REASON_OK}


@pytest.mark.parametrize("kw, reason", [
    (dict(best_bid=None), REASON_NO_L1),
    (dict(best_bid=300.05), REASON_CROSSED),
    (dict(live_order=True), REASON_LIVE_ORDER),
    (dict(cap_ok=False), REASON_VOLUME_CAP),
    (dict(instructions_used=4), REASON_BUDGET),
    (dict(base_free=0.0), REASON_NO_BALANCE),
    (dict(quote_free=0.0), REASON_NO_BALANCE),
])
def test_every_other_check_is_the_frozen_one_and_refuses_both_sides(kw, reason):
    out = cp.sides_verdict(facts(net_base=0.25, **kw), clip=0.25, flat_eps=5e-5, two_sided=True)
    assert out == {"buy": reason, "sell": reason}


def test_the_shared_checks_are_taken_on_the_clip_not_the_minimum_order():
    thin = facts(net_base=0.0, base_free=0.4)
    assert cp.sides_verdict(thin, clip=0.25, flat_eps=5e-5, two_sided=True)["buy"] == REASON_OK
    assert cp.sides_verdict(thin, clip=1.0, flat_eps=5e-5, two_sided=True)["buy"] == REASON_NO_BALANCE


# ------------------------------------------------------------------------------------------------
# 2. The pace: the validator's cap over the validator's period
# ------------------------------------------------------------------------------------------------

def test_the_period_and_the_sample_are_the_validator_constants():
    assert cp.PACE_PERIOD_NS == 86_400 * S       # scoring.activity.trade_volume_assessment_period
    assert cp.PACE_SAMPLE_NS == 600 * S          # scoring.activity.trade_volume_sampling_interval


def test_the_target_rate_is_the_cap_spread_over_its_period():
    rate = cp.pace_target_rate(CAP)
    assert rate == pytest.approx(CAP / (86_400 * S))
    # which is 62,500 quote per book per 3 sim-h window -- 8.0M over 128 books
    assert rate * WINDOW_NS == pytest.approx(62_500.0)
    assert rate * WINDOW_NS * 128 == pytest.approx(8_000_000.0)
    assert cp.pace_target_rate(0.0) == 0.0


def test_a_sample_needs_the_validator_sampling_interval_and_survives_a_rolled_window():
    assert cp.observed_rate(prev_ns=None, prev_volume=0.0, now_ns=600 * S, volume=10.0) is None
    assert cp.observed_rate(prev_ns=0, prev_volume=0.0, now_ns=599 * S, volume=10.0) is None
    assert cp.observed_rate(prev_ns=0, prev_volume=0.0, now_ns=600 * S, volume=6000.0) == pytest.approx(10.0 / S)
    assert cp.observed_rate(prev_ns=0, prev_volume=500.0, now_ns=600 * S, volume=10.0) is None


def test_the_clip_is_whole_minimum_orders_and_never_below_one():
    assert cp.lots_of(0.9, 0.25) == 0.75
    assert cp.lots_of(0.0, 0.25) == 0.25
    assert cp.lots_of(1.0, 0.25) == 1.0


def test_a_book_behind_the_pace_steps_up_and_one_over_it_steps_down():
    target = cp.pace_target_rate(CAP)
    slow = cp.paced_clip(clip_now=0.25, min_order=0.25, target_rate=target, obs_rate=target / 4.0)
    assert slow == 0.5                         # damped to one doubling per sample
    fast = cp.paced_clip(clip_now=1.0, min_order=0.25, target_rate=target, obs_rate=target * 4.0)
    assert fast == 0.5
    on_pace = cp.paced_clip(clip_now=0.5, min_order=0.25, target_rate=target, obs_rate=target)
    assert on_pace == 0.5


def test_a_book_trading_nothing_steps_up_and_one_with_no_sample_holds():
    target = cp.pace_target_rate(CAP)
    assert cp.paced_clip(clip_now=0.5, min_order=0.25, target_rate=target, obs_rate=0.0) == 1.0
    assert cp.paced_clip(clip_now=0.5, min_order=0.25, target_rate=target, obs_rate=None) == 0.5
    assert cp.paced_clip(clip_now=0.5, min_order=0.25, target_rate=0.0, obs_rate=1.0) == 0.5


def test_the_clip_never_falls_below_one_minimum_order():
    target = cp.pace_target_rate(CAP)
    assert cp.paced_clip(clip_now=0.25, min_order=0.25, target_rate=target, obs_rate=target * 1000) == 0.25


def test_the_ceiling_is_the_book_s_own_balances_and_cap_headroom():
    assert cp.clip_ceiling(min_order=0.25, base_free=68.0, quote_free=29_000.0, price=300.0,
                           cap_remaining=CAP) == pytest.approx(68.0)
    assert cp.clip_ceiling(min_order=0.25, base_free=68.0, quote_free=300.0, price=300.0,
                           cap_remaining=CAP) == pytest.approx(1.0)
    assert cp.clip_ceiling(min_order=0.25, base_free=68.0, quote_free=29_000.0, price=300.0,
                           cap_remaining=600.0) == pytest.approx(1.0)
    assert cp.clip_ceiling(min_order=0.25, base_free=0.1, quote_free=29_000.0, price=300.0,
                           cap_remaining=CAP) == 0.0


def test_the_ceiling_clamps_the_paced_clip():
    target = cp.pace_target_rate(CAP)
    got = cp.paced_clip(clip_now=1.0, min_order=0.25, target_rate=target, obs_rate=0.0, ceiling=1.4)
    assert got == 1.25


# ------------------------------------------------------------------------------------------------
# 3. The cap reserve: an exit must always fit
# ------------------------------------------------------------------------------------------------

def test_adding_stops_with_room_for_the_exit_of_what_is_held():
    kw = dict(cap_quote=CAP, mid=300.0, clip=0.25)
    assert cp.cap_reserve_ok(used=0.0, net_base=0.0, **kw) is True
    assert cp.cap_reserve_ok(used=CAP - 100.0, net_base=0.0, **kw) is False
    # room for two clips of new volume (150) plus the exit of the held clip and the new one (150)
    assert cp.cap_reserve_ok(used=CAP - 300.0, net_base=0.25, **kw) is True    # exactly fits
    assert cp.cap_reserve_ok(used=CAP - 299.0, net_base=0.25, **kw) is False
    assert cp.cap_reserve_ok(used=CAP - 300.0, net_base=1.25, **kw) is False   # a bigger lot needs more


def test_no_cap_means_no_reserve_and_a_dead_price_refuses():
    assert cp.cap_reserve_ok(cap_quote=0.0, used=1e9, net_base=10.0, mid=300.0, clip=1.0) is True
    assert cp.cap_reserve_ok(cap_quote=CAP, used=0.0, net_base=0.0, mid=0.0, clip=0.25) is False


# ------------------------------------------------------------------------------------------------
# 4. The agent's own helpers, executed
# ------------------------------------------------------------------------------------------------

class _Account:
    def __init__(self, traded=0.0):
        self.traded_volume = traded


class _Agent:
    """The v6.2.5 helpers on a synthetic agent: the real methods, nothing stubbed but the views."""

    mm_base_size = 0.25

    def __init__(self, *, two_sided=True, cap_pace=True, traded=0.0, cap=CAP):
        self.research_v625_two_sided = two_sided
        self.research_v625_cap_pace = cap_pace
        self._v625_counts = {}
        self._v625_pace = {}
        self._v625_caps_lot = None
        self._v625_errors = 0
        self._tick = 1
        self._cap = cap
        self._traded = traded
        self.emitted = []

    # the frozen views the helpers read
    def _v62_on(self):
        return True

    def _research_volume_cap_quote(self, state):
        return self._cap

    def _research_book_traded_volume(self, book_id):
        return self._traded

    def _research_volume_cap_remaining(self, state, book_id):
        return max(0.0, self._cap - self._traded)

    def _emit(self, name, **kw):
        self.emitted.append((name, kw))


def _bind(agent, *names):
    ns = {
        "v625_pace_target_rate": cp.pace_target_rate, "v625_observed_rate": cp.observed_rate,
        "v625_paced_clip": cp.paced_clip, "v625_clip_ceiling": cp.clip_ceiling,
        "v625_sides_verdict": cp.sides_verdict, "v625_cap_reserve_ok": cp.cap_reserve_ok,
        "v625_band_for": cp.band_for, "v625_pace_snapshot": cp.pace_snapshot,
        "V625BookPace": cp.BookPace, "V625_PACE_SAMPLE_NS": cp.PACE_SAMPLE_NS,
        "V625_BAND_CLIPS": cp.BAND_CLIPS, "V625_REASON_OK": cp.REASON_OK,
        "V625_PACE_PERIOD_NS": cp.PACE_PERIOD_NS,
        "v627_cap_absorption_clip": mc.cap_absorption_clip,
        "v627_bounded_ceiling": mc.bounded_ceiling, "v627_pace_rewound": mc.pace_rewound,
        "V628_REASON_FEE_UNVIABLE": te.REASON_FEE_UNVIABLE, "V628_TOUCH_EXIT_VERSION": te.V628_TOUCH_EXIT_VERSION,
        "v628_fee_viable": te.fee_viable, "v628_spread_bps": te.spread_bps,
        "v628_skewed_sides": te.skewed_sides, "v628_band_inventory_util": te.band_inventory_util,
        "V625_REASON_CAP_RESERVE": cp.REASON_CAP_RESERVE, "V625_CAP_PACED_VERSION": cp.V625_CAP_PACED_VERSION,
        "Any": object,
    }
    for name in names:
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]),
                     "<v625>", "exec"), ns)
        setattr(type(agent), name, ns[name])
    return agent


class _State:
    def __init__(self, ts):
        self.timestamp = ts
        self.books = {}


def _agent(**kw):
    agent = _Agent(**kw)
    # v6.2.7 has its own suite: bound and rewind stay off so this one measures v6.2.5's controller.
    agent.research_v627_clip_bound = False
    agent.research_v627_pace_rewind = False
    agent._v627_counts, agent._v627_errors = {}, 0
    agent.research_v628_rung_cap = False        # v6.2.8 has its own suite
    agent.research_v628_band_inventory = False
    agent.research_v628_fee_viable = False
    agent.research_v628_skew_sides = False
    agent._v628_counts, agent._v628_errors = {}, 0
    return _bind(agent, "_v625_two_sided_on", "_v625_cap_pace_on", "_v625_on", "_v625_count",
                 "_v625_now_ns", "_v625_clip", "_v625_sides", "_v625_snapshot",
                 "_v627_clip_bound_on", "_v627_pace_rewind_on", "_v627_clip_bound", "_v627_count",
                 "_v628_rung_cap_on", "_v628_band_inventory_on", "_v628_fee_viable_on", "_v628_skew_sides_on", "_v628_on", "_v628_count", "_v628_book_viable", "_v628_snapshot")


def test_the_first_call_opens_the_sample_at_one_minimum_order():
    agent = _agent()
    assert agent._v625_clip(7, _State(0), facts(), mid=300.0) == 0.25
    pace = agent._v625_pace[7]
    assert (pace.clip, pace.obs_rate, pace.sampled_ns) == (0.25, None, 0)


def test_the_clip_is_held_between_samples_and_paced_on_one():
    agent = _agent()
    agent._v625_clip(7, _State(0), facts(), mid=300.0)
    agent._traded = 1_000.0
    assert agent._v625_clip(7, _State(300 * S), facts(), mid=300.0) == 0.25      # too early
    # 1,000 quote in 600 s is a quarter of the pace (62,500 per 10,800 s), so the clip doubles
    assert agent._v625_clip(7, _State(600 * S), facts(), mid=300.0) == 0.5
    assert agent._v625_counts.get("clip_up") == 1


def test_cap_pace_off_keeps_one_minimum_order_and_never_samples():
    agent = _agent(cap_pace=False)
    assert agent._v625_clip(7, _State(600 * S), facts(), mid=300.0) == 0.25
    assert agent._v625_pace == {}


def test_the_agent_refuses_both_sides_when_the_exit_would_not_fit_under_the_cap():
    agent = _agent(traded=CAP - 100.0)
    out = agent._v625_sides(facts(net_base=0.25), clip=0.25, flat_eps=5e-5, state=_State(0), mid=300.0)
    assert out["buy"] == cp.REASON_CAP_RESERVE and out["sell"] == cp.REASON_EXIT_SIDE
    assert agent._v625_counts.get("cap_reserve") == 1


def test_the_snapshot_reports_the_pacing_state():
    agent = _agent()
    agent._v625_clip(7, _State(0), facts(), mid=300.0)
    agent._v625_clip(8, _State(0), facts(book_id=8), mid=300.0)
    snap = agent._v625_snapshot()
    assert snap["books"] == 2 and snap["clip_p50"] == 0.25 and snap["clip_at_min"] == 2
    assert snap["errors"] == 0


# ------------------------------------------------------------------------------------------------
# 5. The acquisition pass end to end, on the v6.2.0 harness with both switches on
# ------------------------------------------------------------------------------------------------

import test_research_v6_2_0_breadth as breadth  # noqa: E402


def _acquire_agent(*, two_sided=True, cap_pace=True, traded=None, cap=CAP, books=8):
    """The real v6.2.0 acquisition harness with the real v6.2.5 methods bound on top."""
    agent = breadth._agent(books=books)
    agent.research_v625_two_sided = two_sided
    agent.research_v625_cap_pace = cap_pace
    agent.research_v626_capture_balance = False      # v6.2.6 has its own suite: this one is v6.2.5
    agent.research_v626_quote_life = False
    agent.research_v627_balance_gate = False         # and v6.2.7 has its own
    agent.research_v627_band_caps = False
    agent.research_v627_clip_bound = False      # v6.2.7 bounds the clip; this suite is v6.2.5
    agent.research_v627_pace_rewind = False
    agent._v627_counts = {}
    agent.research_v628_rung_cap = False        # v6.2.8 has its own suite
    agent.research_v628_band_inventory = False
    agent.research_v628_fee_viable = False
    agent.research_v628_skew_sides = False
    agent._v628_counts, agent._v628_errors = {}, 0
    agent._v627_errors = 0
    agent._v626_counts = {}
    agent._v626_errors = 0
    agent._traded = dict(traded or {})
    agent._cap = cap
    cls = type(agent)
    ns = {
        "v625_pace_target_rate": cp.pace_target_rate, "v625_observed_rate": cp.observed_rate,
        "v625_paced_clip": cp.paced_clip, "v625_clip_ceiling": cp.clip_ceiling,
        "v625_sides_verdict": cp.sides_verdict, "v625_cap_reserve_ok": cp.cap_reserve_ok,
        "v625_band_for": cp.band_for, "v625_pace_snapshot": cp.pace_snapshot,
        "v625_with_cap_ok": cp.with_cap_ok, "V625BookPace": cp.BookPace,
        "V625_PACE_SAMPLE_NS": cp.PACE_SAMPLE_NS, "V625_BAND_CLIPS": cp.BAND_CLIPS,
        "V625_REASON_OK": cp.REASON_OK, "V625_REASON_CAP_RESERVE": cp.REASON_CAP_RESERVE,
        "V625_CAP_PACED_VERSION": cp.V625_CAP_PACED_VERSION,
        "v62_universe_caps": breadth.br.universe_caps, "Any": object,
    }
    ns.update({
        "OrderDirection": breadth._OrderDirection, "STP": breadth._STP,
        "TimeInForce": breadth._TIF, "LoanSettlementOption": breadth._LSO,
        "V62_REASON_OK": breadth.br.REASON_OK, "V62_SKIP_REASONS": breadth.br.SKIP_REASONS,
        "V62BookFacts": breadth.br.BookFacts, "v62_entry_client_ids": breadth.br.entry_client_ids,
        "v62_lot_quantity": breadth.br.lot_quantity, "v62_touch_prices": breadth.br.touch_prices,
        "v62_universe_verdict": breadth.br.universe_verdict,
        "V625_SIDE_BUY": cp.SIDE_BUY, "V625_SIDE_SELL": cp.SIDE_SELL,
        "V625_REASON_BAND": cp.REASON_BAND, "V625_REASON_EXIT_SIDE": cp.REASON_EXIT_SIDE,
        "v626_side_already_instructed": bm.side_already_instructed,
        "v626_skip_reason": bm.skip_reason, "v626_side_clips": bm.side_clips,
        "v626_balance_ratio": bm.balance_ratio, "v626_open_book_caps": bm.open_book_caps,
        "v627_band_caps": mc.band_caps, "v627_balance_gate_sides": mc.balance_gate_sides,
        "V627_BALANCE_TARGET": mc.BALANCE_TARGET,
        "v627_cap_absorption_clip": mc.cap_absorption_clip,
        "v627_bounded_ceiling": mc.bounded_ceiling, "v627_pace_rewound": mc.pace_rewound,
        "V628_REASON_FEE_UNVIABLE": te.REASON_FEE_UNVIABLE, "V628_TOUCH_EXIT_VERSION": te.V628_TOUCH_EXIT_VERSION,
        "v628_fee_viable": te.fee_viable, "v628_spread_bps": te.spread_bps,
        "v628_skewed_sides": te.skewed_sides, "v628_band_inventory_util": te.band_inventory_util,
        "V625_PACE_PERIOD_NS": cp.PACE_PERIOD_NS,
        "V626_REASON_SURPLUS": bm.REASON_SURPLUS,
    })
    for name in ("_v625_two_sided_on", "_v625_cap_pace_on", "_v625_on", "_v625_count",
                 "_v625_now_ns", "_v625_clip", "_v625_sides", "_v625_snapshot", "_v625_apply_caps",
                 "_v626_capture_balance_on", "_v626_quote_life_on", "_v626_count",
                 "_v626_side_clips", "_v626_book_capture", "_v62_entry_ttl_ns",
                 "_v627_balance_gate_on", "_v627_band_caps_on", "_v627_on", "_v627_count",
                 "_v627_caps", "_v627_snapshot",
                 "_v627_clip_bound_on", "_v627_pace_rewind_on", "_v627_clip_bound",
                 "_v628_rung_cap_on", "_v628_band_inventory_on", "_v628_fee_viable_on", "_v628_skew_sides_on", "_v628_on", "_v628_count", "_v628_book_viable", "_v628_snapshot",
                 "_v62_book_facts", "_v62_place_touch_quotes", "_v62_acquire"):
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]),
                     "<v625>", "exec"), ns)
        setattr(cls, name, ns[name])
    cls._research_volume_cap_quote = lambda self, state: self._cap
    cls._research_book_traded_volume = lambda self, book_id: self._traded.get(int(book_id), 0.0)
    cls._research_volume_cap_remaining = (
        lambda self, state, book_id: max(0.0, self._cap - self._traded.get(int(book_id), 0.0))
    )
    return agent


def _placed(response):
    out = {}
    for ix in response.instructions:
        out.setdefault(int(ix.bookId), []).append(ix)
    return out


def test_every_flat_book_still_gets_both_sides_at_one_minimum_order():
    agent = _acquire_agent()
    r = breadth._Response()
    agent._v62_acquire(r, breadth._state(8), {})
    placed = _placed(r)
    assert len(placed) == 8 and all(len(v) == 2 for v in placed.values())
    assert {float(ix.quantity) for ix in r.instructions} == {0.25}


def test_a_held_book_gets_only_its_adding_side():
    agent = _acquire_agent()
    agent.inventory[3] = 0.25            # long: the buy side adds, the sell side is the exit's
    agent.inventory[4] = -0.25           # short: the mirror image
    r = breadth._Response()
    agent._v62_acquire(r, breadth._state(8), {})
    placed = _placed(r)
    assert len(placed[3]) == 1 and placed[3][0].direction is breadth._OrderDirection.BUY
    assert len(placed[4]) == 1 and placed[4][0].direction is breadth._OrderDirection.SELL
    assert len(placed[0]) == 2
    assert agent._v625_counts.get("adding_on_held_book") == 2


def test_a_book_at_the_band_is_left_to_its_exit():
    agent = _acquire_agent()
    agent.inventory[3] = 0.5             # two clips: the band
    r = breadth._Response()
    agent._v62_acquire(r, breadth._state(8), {})
    assert 3 not in _placed(r)
    assert agent._v62_request.get(cp.REASON_BAND) == 1


def test_two_sided_off_is_v6_2_4_exactly():
    agent = _acquire_agent(two_sided=False)
    agent.inventory[3] = 0.25
    r = breadth._Response()
    agent._v62_acquire(r, breadth._state(8), {})
    placed = _placed(r)
    assert 3 not in placed and len(placed) == 7
    assert agent._v62_request.get("NOT_FLAT") == 1


def test_the_clip_grows_on_a_book_behind_the_pace_and_the_caps_follow():
    agent = _acquire_agent()
    state0 = breadth._state(8)
    agent._v62_acquire(breadth._Response(), state0, {})          # opens the samples
    agent._traded[3] = 1_000.0                                    # a quarter of the pace in 600 s
    agent._traded[0] = CAP / 86_400.0 * 600.0                     # exactly on the pace
    state1 = breadth._state(8)
    state1.timestamp = state0.timestamp + 600 * S
    r = breadth._Response()
    agent._v62_acquire(r, state1, {})
    agent._v625_apply_caps(state1)
    assert {float(ix.quantity) for ix in _placed(r)[3]} == {0.5}
    assert {float(ix.quantity) for ix in _placed(r)[0]} == {0.25}     # on pace: unchanged
    assert {float(ix.quantity) for ix in _placed(r)[1]} == {0.5}      # no volume at all: steps up
    assert agent.research_max_total_abs_base == pytest.approx(8 * 2.0 * 0.5)
    assert agent._v625_counts.get("caps_raised") == 1


def test_a_book_at_its_cap_is_refused_before_its_exit_is_trapped():
    agent = _acquire_agent(traded={3: CAP - 100.0})
    agent.inventory[3] = 0.25
    r = breadth._Response()
    agent._v62_acquire(r, breadth._state(8), {})
    assert 3 not in _placed(r)
    assert agent._v62_request.get(cp.REASON_CAP_RESERVE) == 1


# ------------------------------------------------------------------------------------------------
# 6. Wiring
# ------------------------------------------------------------------------------------------------

def test_the_acquisition_pass_sizes_and_sides_every_book():
    src = _simple("_v62_acquire")
    assert "clip = self._v625_clip(book_id, state, facts, mid=mid)" in src
    assert "sides = self._v625_sides(facts, clip=clip, flat_eps=eps, state=state, mid=mid)" in src
    assert "response, state, book_id, book, clip, sides, side_qty," in src
    # v6.2.4 exactly when both switches are off
    assert "verdict = v62_universe_verdict(facts, lot=lot, flat_eps=eps)" in src


def test_the_placement_honours_the_per_side_verdict():
    src = _simple("_v62_place_touch_quotes")
    assert "if sides is not None and sides.get(side) != V625_REASON_OK:" in src
    assert "postOnly=post_only" in src        # the frozen placement is otherwise untouched


def test_the_caps_follow_the_band_and_only_ever_grow():
    src = _simple("_v625_apply_caps")
    # v6.2.7 keeps this branch for the gate-off arm and applies the band once when the gate is on.
    assert "V625_BAND_CLIPS * lot)" in src
    assert "if prev is not None and lot <= float(prev) + 1e-12:" in src


def test_the_switches_default_on_and_are_read_from_config():
    assert 'getattr(self.config, "research_v625_two_sided", True)' in SIMPLE
    assert 'getattr(self.config, "research_v625_cap_pace", True)' in SIMPLE


def test_the_state_row_and_the_stats_carry_the_build():
    assert "two_sided_on=int(self._v625_two_sided_on())" in SIMPLE
    assert "cap_pace_on=int(self._v625_cap_pace_on())" in SIMPLE
    assert "cap_paced=self._v625_snapshot()" in SIMPLE
    assert 'stats["direct_v625_two_sided"] = int(self._v625_two_sided_on())' in SIMPLE
    assert 'stats["direct_v625_cap_pace"] = int(self._v625_cap_pace_on())' in SIMPLE


def test_the_version_pin_moved():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_9"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_9"' in SIMPLE


def test_the_launcher_carries_the_arm_the_params_and_the_guard():
    assert "strategy1_direct_v6_2_5)" in LAUNCHER and "V625_BUILD=1 ;;" in LAUNCHER
    assert "research_v625_two_sided=1" in LAUNCHER and "research_v625_cap_pace=1" in LAUNCHER
    assert "[preflight] v6.2.5 cap-paced maker PASS" in LAUNCHER
    assert "tests/test_research_v6_2_5_cap_paced.py" in LAUNCHER
    # v6.2.4 keeps its own arm, so either half can be run alone
    assert "strategy1_direct_v6_2_4)" in LAUNCHER
