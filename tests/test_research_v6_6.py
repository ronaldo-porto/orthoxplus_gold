"""v6.6: S1 add spacing, S2 budget-line pacing, S3 two clips of deep room, S4 a capped book takes no placement, S5 exchange
identities keyed by (book, order id).

Mainnet 09-27/28: UID 104 (v6.5) led the field on making (8,054) but its skill leg fell to rank 0.475 on one book -- book 7
filled four 2-base deep sells in 32 s into a 12% spike, to its 6-base bound, and covered at the top (-175.6).  UID 94
(v6.4.1) capped six more books under the 600-s rate pacer.  Replay with the validator's arithmetic on six windows (the
latest three contain the spike): lowest window skill 0.99 -> 3.85 at UID 104's allowance and 1.28 -> 2.67 at UID 94's.
"""
import ast
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_direct_book_ownership as own  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v641_pace as vp  # noqa: E402
import research_v64_board as b64  # noqa: E402
import research_v65_deep_clips as dc  # noqa: E402
import research_v66_add_spacing as sp  # noqa: E402
import research_v66_pace_line as pl  # noqa: E402
import test_research_a1_9_7_postfill_protection as t197  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_3_3_deep_layer as d633  # noqa: E402
import test_research_v6_4_1_volume_pace as t641  # noqa: E402
import test_research_v6_4_board as t64  # noqa: E402
from _harness import extractor  # noqa: E402
from research_volume_cap import volume_cap_headroom  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
TICK = 0.01
S = 1_000_000_000
NOW = t63.NOW
UID = 104
EXTRA = {"V66AddSpacing": sp.AddSpacing, "V66_CANCEL_SPACING": sp.CANCEL_SPACING,
         "V66_ADD_SPACING_VERSION": sp.V66_ADD_SPACING_VERSION, "v66_adds_to_position": sp.adds_to_position,
         "V66PaceLine": pl.PaceLine, "V66_PACE_LINE_VERSION": pl.V66_PACE_LINE_VERSION}
V66_METHODS = ("_v66_count", "_v66_spacing_on", "_v66_spacing_ref", "_v66_pace_line_on", "_v66_pace_ref", "_v66_snapshot")


def _method(name):
    scope = dict(EXTRA)
    exec(compile(ast.Module(body=[ast.parse(_src(name)).body[0]], type_ignores=[]), f"<{name}>", "exec"), scope)
    return scope[name]


def _with66(fn):
    g = dict(fn.__globals__)
    g.update(EXTRA)
    return types.FunctionType(fn.__code__, g, fn.__name__, fn.__defaults__, fn.__closure__)


def _v66(agent, *, spacing=True, line=True):
    cls = type(agent)
    methods = {n: _with66(getattr(cls, n)) for n in ("_v63_pass", "_v633_book")}
    for n in V66_METHODS:
        methods[n] = _method(n)
    agent.__class__ = type("P66", (cls,), methods)
    agent.research_v66_add_spacing, agent.research_v66_pace_line = spacing, line
    agent._v66_spacing, agent._v66_pace, agent._v66_counts, agent._v66_errors = None, None, {}, 0
    agent.uid = UID
    return agent


def _layer(clip=2.0, max_clips=2.0, books=(3,), depth=21.5, alpha=50.0):
    layer = dl.DeepLayer(clip=clip, max_clips=max_clips)
    for b in books:
        db = dl.DeepBook()
        db.sweeps.extend([depth] * dl.SWEEP_MIN)
        db.paper.fills = 1
        db.paper.buckets[dl.sampled_key(NOW)] = [float(alpha), 0.0, 1.0, 0.0]
        layer.books[b] = db
    return layer


def _agent(venue=None, *, spacing=True, last_add=None):
    agent = _v66(t64._agent(_layer(), venue=venue or {}), spacing=spacing)
    if last_add is not None:
        agent._v66_spacing_ref().last[3] = dict(last_add)
    return agent


def _sides(resp):
    return {(p[1], p[3], p[4]) for p in d633._deep_placed(resp)}


# ---- 1. S1 the rule -------------------------------------------------------------------------------------------------

def test_only_an_order_that_grows_a_held_position_is_an_add():
    assert sp.adds_to_position(sp.SIDE_BUY, 2.0) and sp.adds_to_position(sp.SIDE_SELL, -2.0)
    assert not sp.adds_to_position(sp.SIDE_SELL, 2.0) and not sp.adds_to_position(sp.SIDE_BUY, -2.0)   # reducing
    assert not sp.adds_to_position(sp.SIDE_BUY, 0.0) and not sp.adds_to_position(sp.SIDE_SELL, 0.0)    # a flat book
    assert not sp.adds_to_position(sp.SIDE_BUY, None) and not sp.adds_to_position("x", 3.0)


def test_an_add_rests_only_a_full_depth_beyond_the_last_add():
    # book 7, 09-27 23:30 JST: sold 286.96, then 286.83 one second later -- the second sell is not a depth beyond
    assert not sp.spaced(sp.SIDE_SELL, 286.83, 286.96, 21.5, TICK)
    assert not sp.spaced(sp.SIDE_SELL, 287.17, 286.96, 21.5, TICK)
    assert sp.spaced(sp.SIDE_SELL, 287.175, 286.96, 21.5, TICK)                    # exactly one depth above
    assert sp.spaced(sp.SIDE_BUY, 99.785, 100.0, 21.5, TICK) and not sp.spaced(sp.SIDE_BUY, 99.80, 100.0, 21.5, TICK)
    assert sp.spaced(sp.SIDE_SELL, 1.0, None, 21.5, TICK)                          # no record: no constraint
    for bad in ((None, 21.5, TICK), (100.3, None, TICK), (100.3, 21.5, 0.0), (100.3, -1.0, TICK)):
        assert not sp.spaced(sp.SIDE_SELL, bad[0], 100.0, bad[1], bad[2])         # unreadable: never past a record


def test_our_fills_are_read_from_the_prints_as_maker_or_taker():
    trades = [{"p": 100.05, "q": 2.0, "s": 0, "Ma": UID, "Ta": 7},             # taker bought: our ask filled
              {"p": 99.90, "q": 1.0, "s": 1, "Ma": UID, "Ta": 7},              # taker sold: our bid filled
              {"p": 100.10, "q": 0.5, "s": 0, "Ma": 9, "Ta": UID},             # we took the ask
              {"p": 99.80, "q": 0.5, "s": 1, "Ma": 9, "Ta": UID},              # we hit the bid
              {"p": 99.00, "q": 3.0, "s": 1, "Ma": 9, "Ta": 7},                # someone else's print
              {"p": 99.00, "q": 3.0, "s": 1, "Ma": UID, "Ta": UID}]            # a self-trade
    assert sp.own_fills(trades, UID) == [("sell", 100.05, 2.0), ("buy", 99.90, 1.0), ("buy", 100.10, 0.5),
                                         ("sell", 99.80, 0.5)]
    assert sp.own_fills(trades, None) == []


def test_the_record_keeps_the_last_add_and_forgets_on_flat_and_on_the_other_side():
    s = sp.AddSpacing()
    # flat -> short 2 at 286.96 -> short 4 at 286.83: both grow the short; the last add is the second
    s.note(7, [{"p": 286.96, "q": 2.0, "s": 0, "Ma": UID, "Ta": 134}], UID, -2.0)
    assert s.last_add(7, "sell") == 286.96
    s.note(7, [{"p": 286.83, "q": 2.0, "s": 0, "Ma": UID, "Ta": 35}], UID, -4.0)
    assert s.last_add(7, "sell") == 286.83 and s.adds_recorded == 2
    # a reducing buy leaves the sell record alone and records nothing
    s.note(7, [{"p": 288.99, "q": 2.0, "s": 1, "Ma": UID, "Ta": 28}], UID, -2.0)
    assert s.last_add(7, "sell") == 286.83 and s.last_add(7, "buy") is None and s.adds_recorded == 2
    # two fills in one state: the inventory before them is rebuilt from the inventory after
    s.note(7, [{"p": 291.78, "q": 2.0, "s": 0, "Ma": UID, "Ta": 52}, {"p": 290.62, "q": 2.0, "s": 0, "Ma": UID, "Ta": 9}],
           UID, -6.0)
    assert s.last_add(7, "sell") == 290.62
    # back to flat: the book forgets
    s.note(7, [{"p": 290.0, "q": 6.0, "s": 1, "Ma": UID, "Ta": 9}], UID, 0.0)
    assert s.last_add(7, "sell") is None and 7 not in s.last
    # a long records buys; a short afterwards drops the stale buy record
    s.note(8, [{"p": 50.0, "q": 1.0, "s": 1, "Ma": UID, "Ta": 9}], UID, 1.0)
    assert s.last_add(8, "buy") == 50.0
    s.note(8, [], UID, -1.0)
    assert s.last_add(8, "buy") is None
    assert s.allows(7, "sell", 280.0, -2.0, 21.5, TICK)                           # no record left: allowed
    assert s.snapshot() == {"version": "add_spacing_v6_6", "books_recorded": 0, "adds_recorded": 5, "rebases": 0}


def test_a_short_swept_straight_to_long_drops_its_sell_record():
    s = sp.AddSpacing()
    s.note(9, [{"p": 101.0, "q": 2.0, "s": 0, "Ma": UID, "Ta": 7}], UID, -2.0)
    assert s.last_add(9, "sell") == 101.0
    # one sweep covers the short and carries the book to +1: the covering buy is not an add, the sell record is stale
    s.note(9, [{"p": 100.0, "q": 3.0, "s": 1, "Ma": UID, "Ta": 7}], UID, 1.0)
    assert s.last_add(9, "sell") is None and s.last_add(9, "buy") is None
    assert s.allows(9, "buy", 100.5, 1.0, 21.5, TICK)                             # no record on the new side


def test_a_new_simulation_forgets_every_record():
    s = sp.AddSpacing()
    s.maybe_rebase(NOW)
    s.note(3, [{"p": 100.05, "q": 2.0, "s": 0, "Ma": UID, "Ta": 7}], UID, -2.0)
    s.maybe_rebase(NOW + 10 * S)
    assert s.last_add(3, "sell") == 100.05
    s.maybe_rebase(NOW - 2 * 3600 * S)                                             # the clock ran back two hours
    assert s.last_add(3, "sell") is None and s.rebases == 1


# ---- 2. S1 in the pass ----------------------------------------------------------------------------------------------

MID = 100.015
SELL_PX = dl.deep_price(MID, 21.5, dl.SIDE_SELL, bid=100.00, ask=100.03, tick=TICK, decimals=2)   # 100.23
BUY_PX = dl.deep_price(MID, 21.5, dl.SIDE_BUY, bid=100.00, ask=100.03, tick=TICK, decimals=2)     # 99.8


def test_a_held_short_does_not_add_less_than_a_depth_above_its_last_sell():
    assert (SELL_PX, BUY_PX) == (100.23, 99.8)
    agent = _agent({3: -2.0}, last_add={"sell": 100.03})                            # needs >= 100.245
    resp = t64._run(agent, {3: t63._book()})
    assert _sides(resp) == {("BUY", BUY_PX, 2.0)}                                  # only the reducing side
    assert agent._v66_counts.get("placement_spaced_out") == 1
    agent = _agent({3: -2.0}, last_add={"sell": 100.00})                            # needs >= 100.215
    resp = t64._run(agent, {3: t63._book()})
    assert _sides(resp) == {("BUY", BUY_PX, 2.0), ("SELL", SELL_PX, 2.0)}


def test_the_pass_records_our_own_sell_before_it_places():
    ev = [{"y": "t", "p": 100.05, "q": 2.0, "s": 0, "Ma": UID, "Ta": 7}]            # our ask filled this state
    agent = _agent({3: -2.0})
    resp = t64._run(agent, {3: t63._book(events=ev)})
    assert agent._v66_spacing.last_add(3, "sell") == 100.05
    assert _sides(resp) == {("BUY", BUY_PX, 2.0)}                                  # 100.23 < 100.05 + 0.215


def test_a_resting_add_that_is_no_longer_spaced_is_cancelled_and_the_reducing_order_stays():
    agent = _agent({3: -2.0}, last_add={"sell": 100.03})
    t63.t14._rest(agent, 21, 3, 0, BUY_PX, cid=40031)
    t63.t14._rest(agent, 22, 3, 1, SELL_PX, cid=40032)
    resp = t64._run(agent, {3: t63._book()})
    assert t63._cancelled(resp) == {(3, 22)} and t63._placed(resp) == set()
    assert agent._v66_counts.get("cancel_spacing") == 1
    assert agent._v633_counts.get("cancel_v66_spacing") == 1


def test_a_flat_book_and_the_switch_off_are_v6_5():
    resp = t64._run(_agent({}, last_add={"sell": 100.03}), {3: t63._book()})
    assert _sides(resp) == {("BUY", BUY_PX, 2.0), ("SELL", SELL_PX, 2.0)}         # flat: nothing to space from
    resp = t64._run(_agent({3: -2.0}, spacing=False, last_add={"sell": 100.03}), {3: t63._book()})
    assert _sides(resp) == {("BUY", BUY_PX, 2.0), ("SELL", SELL_PX, 2.0)}
    old = t64._agent(_layer(), venue={3: -2.0})                                    # an agent from before v6.6
    assert _sides(t64._run(old, {3: t63._book()})) == {("BUY", BUY_PX, 2.0), ("SELL", SELL_PX, 2.0)}


def test_in_a_blown_out_spread_the_unit_is_the_vacuum_depth():
    wide = dict(bid=100.00, ask=100.30)                                           # vacuum 0.8 x 15 = 12 ticks
    mid, vac = 100.15, b64.vacuum_depth(100.00, 100.30, TICK)
    sell = b64.vacuum_price(mid, vac, dl.SIDE_SELL, bid=100.00, ask=100.30, tick=TICK, decimals=2)
    assert abs(vac - 12.0) < 1e-9 and sell == 100.27
    resp = t64._run(_agent({3: -2.0}, last_add={"sell": 100.20}), {3: t63._book(**wide)})   # needs >= 100.32
    assert {s for s, _p, _q in _sides(resp)} == {"BUY"}
    resp = t64._run(_agent({3: -2.0}, last_add={"sell": 100.10}), {3: t63._book(**wide)})   # needs >= 100.22
    assert {s for s, _p, _q in _sides(resp)} == {"BUY", "SELL"}                  # 21.5 ticks would have refused it


# ---- 3. S2 the line -------------------------------------------------------------------------------------------------

CAP = 500_000.0


def test_the_line_runs_from_the_first_reading_to_the_cap_at_the_simulations_end():
    assert pl.line_end_ns(0, None) == vp.ASSESSMENT_NS
    assert pl.line_end_ns(10 * S, 50 * S) == 50 * S
    assert pl.line_end_ns(0, 10 ** 20) == vp.ASSESSMENT_NS                        # never past one assessment period
    end = 10_600 * S
    assert abs(pl.line_volume(CAP, 0.0, 0, 0, end) - CAP * 600 / 10_600) < 1e-6      # one interval of burst up front
    assert abs(pl.line_volume(CAP, 100_000.0, 0, 5_000 * S, end) - (100_000 + 400_000 * 5_600 / 10_600)) < 1e-6
    assert pl.line_volume(CAP, 100_000.0, 0, end, end) == CAP and pl.line_volume(CAP, 0.0, 0, 0, 0) == CAP


def test_a_quiet_book_banks_its_allowance_and_a_burst_may_spend_it():
    line, rate = pl.PaceLine(), vp.VolumePace()
    t0, end = NOW - 7_200 * S, NOW + 40_000 * S
    for p in (line, rate):
        p.observe(3, t0, 20_000.0)
    # two quiet hours, then a sweep burst of 12,000 quote in the last 600 s
    rate.observe(3, NOW - 600 * S, 20_000.0)
    for p in (line, rate):
        p.observe(3, NOW, 32_000.0)
    assert rate.paced(3, NOW, 32_000.0, CAP, end)                                  # v6.4.1 holds it: 20/s > ~11.7/s
    assert not line.paced(3, NOW, 32_000.0, CAP, end)                              # under its line (~99,300)


def test_a_near_cap_book_that_overspends_stays_held_until_the_line_catches_up():
    line, rate = pl.PaceLine(), vp.VolumePace()
    t0, end = NOW - 3_600 * S, NOW + 150_000 * S
    for p in (line, rate):
        p.observe(21, t0, 498_768.0)                                               # book 21, 09-28 10:33 JST: 1,232 left
    # 600 s of nothing: v6.4.1 has no rate over its window any more and lets the book add again
    rate.observe(21, NOW - 700 * S, 499_070.0)
    rate.observe(21, NOW - 100 * S, 499_070.0)
    line.observe(21, NOW, 499_070.0)
    rate.observe(21, NOW, 499_070.0)
    assert not rate.paced(21, NOW, 499_070.0, CAP, end)
    assert line.paced(21, NOW, 499_070.0, CAP, end)                                # 302 spent > 1,232 x 4,200/86,400


def test_the_line_restarts_after_a_volume_reset_and_a_new_simulation():
    line = pl.PaceLine()
    line.observe(3, NOW, 300_000.0)
    line.observe(3, NOW + S, 10.0)                                                 # the venue's volume went down
    assert line.start[3] == (NOW + S, 10.0) and line.restarts == 1
    line.observe(3, NOW - 2 * 3600 * S, 5.0)                                       # the clock ran back: a new simulation
    assert line.rebases == 1 and line.start == {3: (NOW - 2 * 3600 * S, 5.0)}
    assert not pl.PaceLine().paced(3, NOW, 1e9, 0.0, None)                         # no cap: never paced
    assert not pl.PaceLine().paced(3, NOW, 1e9, CAP, None)                         # no line yet: never paced
    snap = line.snapshot()
    assert snap == {"version": "pace_line_v6_6", "books": 1, "paced_now": 0, "rebases": 1, "restarts": 1}


def _pace_agent(venue=None, *, line=True, used=None, start=None, rate=None):
    agent = _v66(t641._agent(_layer(), used=used, rate=rate, venue=venue or {}), line=line)
    agent._v66_pace = pl.PaceLine()
    for b, (t0, u0) in (start or {}).items():
        agent._v66_pace.observe(b, t0, u0)
    return agent


def test_in_the_pass_the_line_lets_a_burst_trade_that_the_rate_rule_held():
    duration = NOW + 48_000 * S
    args = dict(used={3: 20_000.0}, start={3: (NOW - 3_600 * S, 10_000.0)}, rate={3: 20.0})
    resp = t641._run(_pace_agent(line=False, **args), {3: t63._book()}, duration)
    assert _sides(resp) == set()                                                   # v6.4.1: paced, a flat book adds only
    agent = _pace_agent(**args)
    resp = t641._run(agent, {3: t63._book()}, duration)
    assert _sides(resp) == {("BUY", BUY_PX, 2.0), ("SELL", SELL_PX, 2.0)}
    assert isinstance(agent._v66_pace, pl.PaceLine) and len(agent._v641_pace.books[3]) == 1    # v6.4.1's never read


def test_in_the_pass_a_book_over_its_line_only_reduces():
    duration = NOW + 48_000 * S
    agent = _pace_agent({3: 2.0}, used={3: 499_700.0}, start={3: (NOW - 3_600 * S, 495_000.0)})
    resp = t641._run(agent, {3: t63._book()}, duration)
    assert {s for s, _p, _q in _sides(resp)} == {"SELL"}
    assert agent._v641_counts.get("paced_book_states") == 1
    assert agent._v66_snapshot()["pace_line"]["paced_now"] == 1


# ---- 4. S3 two clips of room ------------------------------------------------------------------------------------------

def test_the_deep_bound_is_two_clips_again():
    assert dc.MAX_CLIPS == 2.0 and dc.CLIP_MULT == 2.0
    assert dc.book_bound(2.0, dc.MAX_CLIPS) == 4.0
    assert dc.caps_for(128, clip=2.0, max_clips=dc.MAX_CLIPS)["research_max_total_abs_base"] == 768.0
    assert dc.caps_for(128, clip=1.0, max_clips=dc.MAX_CLIPS)["research_max_total_abs_base"] == 384.0   # --deep_clip_mult 1.0
    resp = t64._run(_agent({3: 4.0}), {3: t63._book()})
    assert {s for s, _p, _q in _sides(resp)} == {"SELL"}                          # at 4 base: reduce only


# ---- 5. S4 a capped book ----------------------------------------------------------------------------------------------

def test_capped_is_the_validators_own_test():
    # query.py drops every non-cancel instruction once miner_volumes[book] >= volume_cap
    assert volume_cap_headroom(CAP, CAP) == 0.0 and volume_cap_headroom(CAP, 501_665.0) == 0.0
    assert volume_cap_headroom(CAP, 499_999.99) > 0.0 and volume_cap_headroom(0.0, 10 ** 9) == 1.0   # no cap: open


def test_the_final_validator_refuses_a_reducing_order_on_a_capped_book():
    body = _src("_research_final_validate_instructions")                          # the live (last) definition
    guard = 'if risk_reducing_batch and headroom <= 0.0 and bool(getattr(self, "research_v66_cap_closed", False)):'
    assert guard in body
    seg = body[body.index(guard):body.index("if not risk_reducing_batch:")]
    assert '"VOLUME_CAP", False,' in seg and "continue" in seg and '_v66_count("cap_closed_rejects")' in seg
    assert body.index("headroom = float(self._research_volume_cap_headroom(state, book_id))") < body.index(guard)


# ---- 6. S5 identities keyed by (book, order id) -------------------------------------------------------------------------

def _two_books(keyed):
    reg = {}
    for book, cid in ((78, 40781), (88, 40881)):
        own.register_exchange_identity(reg, exchange_order_id=1076206, book_id=book, client_order_id=cid, side=0,
                                       remaining_quantity=2.0, book_keyed=keyed)
    return reg


def test_the_same_order_id_on_two_books_is_two_identities():
    old = _two_books(False)
    assert len(old) == 1 and old[1076206].book_id == 88                           # A1.7.4.3.2: the second overwrote
    assert own.cancellation_identity_decision(old, exchange_order_id=1076206, notice_book_id=78, success=True)[0] == "BOOK_MISMATCH"
    reg = _two_books(True)
    assert set(reg) == {(78, 1076206), (88, 1076206)}
    d, ident = own.cancellation_identity_decision(reg, exchange_order_id=1076206, notice_book_id=78, success=True, book_keyed=True)
    assert d == "RELEASE" and ident.book_id == 78 and ident.client_order_id == "40781"
    assert own.cancellation_identity_decision(reg, exchange_order_id=1076206, notice_book_id=88, success=False,
                                              book_keyed=True)[0] == "FAILED_KEEP"
    assert own.cancellation_identity_decision(reg, exchange_order_id=1076206, notice_book_id=99, success=True,
                                              book_keyed=True) == ("STALE_UNKNOWN", None)
    assert own.cancellation_identity_decision(reg, exchange_order_id=1076206, notice_book_id=None, success=True,
                                              book_keyed=True) == ("STALE_UNKNOWN", None)     # two books: ambiguous
    reg.pop((88, 1076206))
    assert own.cancellation_identity_decision(reg, exchange_order_id=1076206, notice_book_id=None, success=True,
                                              book_keyed=True)[0] == "RELEASE"
    assert own.identity_key(78, 1076206) == 1076206 and own.identity_key(78, 1076206, book_keyed=True) == (78, 1076206)


def _fill_agent(keyed):
    agent = t197._note_fill_agent()
    agent.uid = 67
    agent.research_v66_book_identity = keyed
    for book, cid in ((78, 40781), (88, 40881)):
        t197._reserve(agent, book=book, client=cid, side="buy")
        own.register_exchange_identity(agent._direct_exchange_identity_registry(), exchange_order_id=1076206,
                                       book_id=book, client_order_id=cid, side=0, remaining_quantity=0.25,
                                       book_keyed=keyed)
    agent._direct_pending_note_fill(t197._maker_fill(book=78, order=1076206, client=40781))
    return {k[0] for k in agent._direct_pending_ledger()}, agent._direct_exchange_identity_registry()


def test_a_fill_releases_its_own_books_reservation_only():
    books, reg = _fill_agent(True)
    assert books == {88} and set(reg) == {(88, 1076206)}                          # book 78's own order, released
    books, reg = _fill_agent(False)
    assert books == {78}                                                           # before v6.6: book 88's was released


# ---- 7. wiring ------------------------------------------------------------------------------------------------------------

def test_the_switches_default_on_ship_in_params_and_are_preflighted():
    for name in ("research_v66_add_spacing", "research_v66_pace_line", "research_v66_cap_closed", "research_v66_book_identity"):
        assert f'self.{name} = self._as_bool(getattr(self.config, "{name}", True))' in SIMPLE
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"\n', start + len('PARAMS="'))]
    for key in ("research_v66_add_spacing=1", "research_v66_pace_line=1", "research_v66_cap_closed=1",
                "research_v66_book_identity=1", "research_v65_deep_max_clips=2.0", "research_v65_deep_clip_mult=${DEEP_CLIP_MULT}"):
        assert key in params
    assert 'echo "[preflight] v6.6 add spacing / pace line / cap closed / book identity PASS"' in LAUNCHER
    assert "tests/test_research_v6_6.py" in LAUNCHER
    assert '[[ "$DEEP_CLIP_MULT" == "1.0" || "$DEEP_CLIP_MULT" == "2.0" ]]' in LAUNCHER
    assert "    --deep_clip_mult)\n" in LAUNCHER and "    --deep_clip_mult=*)\n" in LAUNCHER


def test_the_state_row_reports_v6_6():
    assert 'v66=(self._v66_snapshot() if getattr(self, "research_v66_add_spacing", None) is not None else {}),' in _src("_v62_telemetry")
    agent = _agent({3: -2.0}, last_add={"sell": 100.03})
    t64._run(agent, {3: t63._book()})
    snap = agent._v66_snapshot()
    assert snap["add_spacing_on"] == 1 and snap["add_spacing"]["version"] == "add_spacing_v6_6"
    assert snap["add_spacing"]["books_recorded"] == 1 and snap["placement_spaced_out"] == 1 and snap["errors"] == 0


def test_the_pass_notes_fills_before_the_books_are_quoted():
    src = _src("_v63_pass")
    assert src.index("spacing.note(book_id, book_trades,") < src.index("placed_total += self._v633_book(")
    assert src.index("spacing.maybe_rebase(now_ts)") < src.index("for raw_id in sorted(books")
