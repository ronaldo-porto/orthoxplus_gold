"""A1.9.9: one exit authority for ABSOLUTE positions, one owner for a clock rewind.

Measured on the A1.9.8 run, log 20260914_012543, ticks 1-4,000 (378 round trips, G10 1.5746).
Book 49 parked for 216 ABSOLUTE evaluations on a crossed touch and exited at -1,211 bps,
92.6% of the run's cubic downside.  At tick 2,467 the simulation clock went back 36 s and six
books stayed diverged from the venue, 1.2498 BASE in total, until tick 4,000.
"""
import ast
import re
import textwrap
import typing
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace

import research_direct_risk_state as rs
import research_direct_session_epoch as se
import research_v5_dust_liveness as dl
from research_direct_absolute_authority import ARM_RECOVERY_MAKER, ARM_RELATIVE_VETO, restore_absolute_taker
from research_direct_exit import choose_observable_position_exit
from research_direct_positive_maker_kappa import apply_positive_maker_kappa_veto
from research_direct_tail_recovery import choose_tail_recovery_override
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_PARK_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    BAND_ABSOLUTE,
    BAND_HARD_ESCAPE,
    BAND_NORMAL,
)
from research_session_state import extract_simulation_id
from _harness import extractor, legacy_capability_attrs

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RISK = (STRATEGY / "research_direct_risk_state.py").read_text()
EPOCH = (STRATEGY / "research_direct_session_epoch.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
EPS = 5e-05


_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


def _level(price):
    return SimpleNamespace(price=price, quantity=1.0)


def _book(bid, ask):
    return SimpleNamespace(bids=[_level(bid)] if bid else [], asks=[_level(ask)] if ask else [])


def _order(kind, book, direction=None, qty=0.25):
    return SimpleNamespace(type=kind, bookId=book, direction=direction, quantity=qty)


def _stack(*, risk, maker, taker, age, failed, qty=0.25, touch=True, velocity=0.0):
    """The Direct chooser's frozen stack up to the point A1.9.9 decides, as Simple runs it."""
    executable = bool(touch and qty + 1e-12 >= 0.25)
    base = choose_observable_position_exit(
        maker_net_bps=maker, taker_net_bps=taker, p_maker_fill=0.1, unrealized_bps=risk,
        inventory_qty=qty, inventory_age=age, failed_exit_count=failed, min_order=0.25,
        taker_clip=0.25, reduction_executable=executable, is_dust=False, valid_opposite_touch=touch,
        hard_escape_min_age_ticks=2.0, positive_maker_veto_floor_bps=1.0,
        positive_maker_veto_max_failed_exits=4, absolute_positive_maker_veto_floor_bps=1.0,
        absolute_positive_maker_veto_max_failed_exits=1,
    )
    recovery = choose_tail_recovery_override(
        base_decision=base, maker_net_bps=maker, taker_net_bps=taker, position_risk_bps=risk,
        risk_velocity_bps_per_tick_value=velocity, inventory_qty=qty, inventory_age=age,
        failed_exit_count=failed, catastrophic_hard_risk=False, reduction_executable=executable,
    )
    vetoed = apply_positive_maker_kappa_veto(
        base_decision=recovery, maker_net_bps=maker, taker_net_bps=taker, maker_executable=True,
        catastrophic_hard_risk=False, inventory_qty=qty,
    )
    return base, vetoed


def _authorize(pending, base, decision, *, maker, taker, qty=0.25, dust=False, two_sided=True, a198=True):
    return rs.authorize_exit(
        pending=pending, base_decision=base, decision=decision, maker_net_bps=maker,
        taker_net_bps=taker, inventory_qty=qty, min_order=0.25, taker_clip=0.25, is_dust=dust,
        touch_two_sided=two_sided, a198_enabled=a198,
    )


def _pending(sign=1, entry=271.14, since=2755):
    return rs.ExitPending(sign=sign, entry=entry, since_tick=since)


# ---- the position risk state machine: the live defect ---------------------------------------

BOOK_49 = [  # (tick, position risk, maker net, taker net, age) on a long: maker below taker is a crossed touch
    (2755, -205.4, -258.3, -169.0, 2.0),     # first evaluation; the frozen chooser parked
    (2762, -389.3, -511.4, -285.4, 9.0),     # after the tick-2,758 market order came back unfilled
    (2868, -1044.5, -1424.7, -679.2, 115.0),
]
CROSSED = _book(263.4, 257.3)


def test_book_49_parked_on_a_crossed_touch_and_now_reduces():
    for tick, risk, maker, taker, age in BOOK_49:
        base, vetoed = _stack(risk=risk, maker=maker, taker=taker, age=age, failed=0, touch=False)
        assert base.risk_band == BAND_ABSOLUTE and base.reason == "ABSOLUTE_PROTECTION_PARK"
        assert vetoed is base                                 # no overlay touches a park this deep
        table = {}
        state, transitions = rs.step_exit_pending(
            table, 49, base_band=base.risk_band, net_base=0.25, vwap_entry=271.14, tick=tick, eps=EPS,
        )
        assert [name for name, _ in transitions] == [rs.ENTER] and table[49] is state
        final, rule, arm = _authorize(state, base, vetoed, maker=maker, taker=taker,
                                      two_sided=rs.two_sided_touch(CROSSED.bids, CROSSED.asks))
        assert final.action == ACTION_TAKER_EXIT and final.reason == "ABSOLUTE_PROTECTION_REDUCE"
        assert final.risk_band == BAND_ABSOLUTE and final.selected_qty == 0.25
        assert final.corridor_action == rs.A199_PENDING_CORRIDOR
        assert rule == rs.RULE_NOT_EXITING and arm is None


def test_an_empty_book_side_dust_and_a_short_quantity_stay_parked():
    base, vetoed = _stack(risk=-205.4, maker=-258.3, taker=-169.0, age=2.0, failed=0, touch=False)
    final, rule, _ = _authorize(_pending(), base, vetoed, maker=-258.3, taker=-169.0,
                                two_sided=rs.two_sided_touch([], [_level(257.3)]))
    assert final is vetoed and rule is None
    final, rule, _ = _authorize(_pending(), base, vetoed, maker=-258.3, taker=-169.0, dust=True)
    assert final is vetoed and rule is None
    short_base, short_final = _stack(risk=-30.0, maker=-50.0, taker=-40.0, age=2.0, failed=0, qty=0.1)
    assert short_base.action == ACTION_PARK_EXIT
    final, rule, _ = _authorize(_pending(), short_base, short_final, maker=-50.0, taker=-40.0, qty=0.1)
    assert final is short_final and rule is None
    assert rs.two_sided_touch(CROSSED.bids, CROSSED.asks)
    assert not rs.two_sided_touch([_level(0.0)], [_level(257.3)]) and not rs.two_sided_touch(None, None)


BOOK_95 = [(-31.3, -21.0, -40.5), (-28.0, -17.6, -38.5), (-29.4, -18.0, -38.7)]   # A1.9.7 run, age 2


def test_a1_9_8_restores_run_unchanged_inside_the_authority():
    cases = [dict(risk=r, maker=m, taker=t, age=2.0, failed=0) for r, m, t in BOOK_95]
    cases.append(dict(risk=-40.0, maker=-24.9, taker=-54.5, age=20.0, failed=3))     # relative veto
    for case in cases:
        base, vetoed = _stack(**case)
        expected, arm = restore_absolute_taker(base_decision=base, decision=vetoed)
        assert arm in (ARM_RECOVERY_MAKER, ARM_RELATIVE_VETO)
        for pending in (_pending(), None):
            final, rule, got = _authorize(pending, base, vetoed, maker=case["maker"], taker=case["taker"])
            assert final is expected and rule == rs.RULE_A198_ARM and got == arm


def test_with_a1_9_8_off_the_pending_state_still_refuses_the_loss_maker():
    base, vetoed = _stack(risk=-31.3, maker=-21.0, taker=-40.5, age=2.0, failed=0)
    final, rule, arm = _authorize(_pending(), base, vetoed, maker=-21.0, taker=-40.5, a198=False)
    assert final is base and rule == rs.RULE_LOSS_MAKER and arm is None


def test_positive_makers_keep_priority_while_pending():
    grace_base, grace = _stack(risk=-30.0, maker=3.0, taker=-40.0, age=2.0, failed=0)
    assert grace.reason == "ABSOLUTE_POSITIVE_MAKER_GRACE"
    strong_base, strong = _stack(risk=-30.0, maker=12.0, taker=-35.0, age=10.0, failed=2)
    assert strong.corridor_action == "DIRECT_POSITIVE_MAKER_KAPPA_A1744"
    bounce_base, bounce = _stack(risk=150.0, maker=167.3, taker=140.0, age=10.0, failed=0)  # book 114, tick 2,470
    assert bounce.reason == "NORMAL_MAKER_NET"
    for base, decision, maker, taker in ((grace_base, grace, 3.0, -40.0), (strong_base, strong, 12.0, -35.0),
                                         (bounce_base, bounce, 167.3, 140.0)):
        final, rule, arm = _authorize(_pending(), base, decision, maker=maker, taker=taker)
        assert final is decision and rule is None and arm is None


def test_a_pending_position_never_rests_a_loss_maker_or_waits_after_a_bounce():
    hard_base, hard = _stack(risk=-22.0, maker=-20.0, taker=-40.0, age=5.0, failed=0)
    assert hard_base.risk_band == BAND_HARD_ESCAPE and hard.reason == "HARD_RECOVERY_MAKER_EXIT"
    final, rule, _ = _authorize(_pending(), hard_base, hard, maker=-20.0, taker=-40.0)
    assert final.action == ACTION_TAKER_EXIT and rule == rs.RULE_LOSS_MAKER
    assert final.corridor_action == rs.A199_PENDING_CORRIDOR and final.selected_qty == 0.25
    # Not pending: HARD_ESCAPE keeps its recovery maker, exactly as in A1.9.8.
    final, rule, _ = _authorize(None, hard_base, hard, maker=-20.0, taker=-40.0)
    assert final is hard and rule is None

    # Book 71: "NORMAL" on a crossed touch after reaching ABSOLUTE, and waiting.
    wait_base, wait = _stack(risk=10.0, maker=-5.0, taker=-3.0, age=40.0, failed=0, touch=False)
    assert wait_base.risk_band == BAND_NORMAL and wait.action == ACTION_WAIT
    final, rule, _ = _authorize(_pending(), wait_base, wait, maker=-5.0, taker=-3.0)
    assert final.action == ACTION_TAKER_EXIT and rule == rs.RULE_NOT_EXITING
    final, rule, _ = _authorize(None, wait_base, wait, maker=-5.0, taker=-3.0)
    assert final is wait and rule is None

    defensive_base, defensive = _stack(risk=-12.0, maker=-10.0, taker=-25.0, age=5.0, failed=0)
    assert defensive.action in (ACTION_WAIT, ACTION_MAKER_EXIT)
    final, rule, _ = _authorize(_pending(), defensive_base, defensive, maker=-10.0, taker=-25.0)
    assert final.action == ACTION_TAKER_EXIT and rule in (rs.RULE_NOT_EXITING, rs.RULE_LOSS_MAKER)


def test_the_state_follows_one_position():
    table = {}
    state, tr = rs.step_exit_pending(table, 49, base_band=BAND_NORMAL, net_base=0.25, vwap_entry=271.14,
                                     tick=2753, eps=EPS)
    assert state is None and tr == () and not table
    state, tr = rs.step_exit_pending(table, 49, base_band=BAND_ABSOLUTE, net_base=0.25, vwap_entry=271.14,
                                     tick=2755, eps=EPS)
    assert [n for n, _ in tr] == [rs.ENTER] and state.since_tick == 2755 and state.evaluations == 1
    again, tr = rs.step_exit_pending(table, 49, base_band=BAND_NORMAL, net_base=0.25, vwap_entry=271.14,
                                     tick=2756, eps=EPS)
    assert again is state and tr == () and state.evaluations == 2 and state.last_tick == 2756
    # Book 49 closed on tick 2,975 and sold short at 247.81 on tick 2,982.
    new, tr = rs.step_exit_pending(table, 49, base_band=BAND_NORMAL, net_base=-0.25, vwap_entry=247.81,
                                   tick=2983, eps=EPS)
    assert new is None and [(n, s.since_tick) for n, s in tr] == [(rs.CLEAR_NEW_POSITION, 2755)]
    short, tr = rs.step_exit_pending(table, 49, base_band=BAND_ABSOLUTE, net_base=-0.25, vwap_entry=247.81,
                                     tick=2983, eps=EPS)
    assert [n for n, _ in tr] == [rs.ENTER] and short.sign == -1
    gone, tr = rs.step_exit_pending(table, 49, base_band=BAND_ABSOLUTE, net_base=0.0, vwap_entry=None,
                                    tick=2985, eps=EPS)
    assert gone is None and [n for n, _ in tr] == [rs.CLEAR_FLAT] and 49 not in table
    # A missing cost basis is not a new position.
    kept, _ = rs.step_exit_pending({49: rs.ExitPending(1, 271.14, 2755)}, 49, base_band=BAND_NORMAL,
                                   net_base=0.25, vwap_entry=None, tick=2760, eps=EPS)
    assert kept is not None


def test_a_silent_pending_exit_is_counted_once_per_tick():
    stalls = {}
    for tick in (2759, 2760, 2761, 2761):
        assert rs.note_exit_stall(stalls, 49, tick=tick, has_instruction=False) is None
    row = rs.note_exit_stall(stalls, 49, tick=2762, has_instruction=True)
    assert row == {"book": 49, "from_tick": 2759, "to_tick": 2761, "ticks": 3,
                   "ended_by": "INSTRUCTION", "end_tick": 2762}
    assert rs.note_exit_stall(stalls, 49, tick=2763, has_instruction=False) is None
    assert rs.end_exit_stall(stalls, 49, tick=2764, ended_by="CLEARED") is None and not stalls


# ---- the session epoch controller ------------------------------------------------------------

def test_the_a198_rewind_is_detected_and_nothing_else_is():
    event = se.detect_rewind(last_ts=57_157_000_000_000, ts=57_121_000_000_000,
                             last_sim_id="1789341943289", sim_id="1789341943289")
    assert event == {"reason": se.EPOCH_TIMESTAMP_REWIND, "old_ts": 57_157_000_000_000,
                     "new_ts": 57_121_000_000_000, "rewind_s": -36.0}
    assert se.detect_rewind(last_ts=57_156_000_000_000, ts=57_157_000_000_000, last_sim_id="a", sim_id="a") is None
    assert se.detect_rewind(last_ts=57_157_000_000_000, ts=57_157_000_000_000, last_sim_id="a", sim_id="a") is None
    assert se.detect_rewind(last_ts=None, ts=54_692_000_000_000, last_sim_id=None, sim_id="a") is None
    assert se.detect_rewind(last_ts=57_157_000_000_000, ts=1_000_000_000, last_sim_id="a", sim_id="b") is None
    assert se.detect_rewind(last_ts=57_157_000_000_000, ts=57_121_000_000_000,
                            last_sim_id=None, sim_id="a") is not None


# Tick 2,475 of the A1.9.8 run.  A195_RECONCILE logs each book's local total; the split of that total
# into tracker, ledger and fee residue below is reconstructed so that the parts add up to it.
DIVERGED_2475 = [
    # book, venue, tracker, ledger, residue, action, tracker after
    (102, 0.2380, 0.00, 0.0000, -0.0120, se.RESEED_REAL, 0.25),
    (17, -0.2511, 0.00, 0.0000, -0.0011, se.RESEED_REAL, -0.25),
    (81, -0.0030, -0.25, 0.0000, -0.0030, se.RESEED_CLEAR, 0.0),
    (32, -0.0020, 0.25, 0.0000, -0.0024, se.RESEED_DUST, 0.0),
    (114, 0.1326, 0.25, 0.1326, -0.0001, se.RESEED_DUST, 0.0),
    (7, -0.0003, 0.00, 0.0000, -0.0006, se.RESEED_DUST, 0.0),
]
LOCAL_2475 = {102: -0.012, 17: -0.0011, 81: -0.253, 32: 0.2476, 114: 0.3825, 7: -0.0006}


def test_each_diverged_book_is_rebuilt_to_venue_truth():
    for book, venue, tracker, ledger, residue, action, after in DIVERGED_2475:
        assert abs(tracker + ledger + residue - LOCAL_2475[book]) < 1e-9
        plan = se.plan_book_reseed(book_id=book, venue_net=venue, tracker_net=tracker, ledger=ledger,
                                   fee_residue=residue, pending=0.0, min_order=0.25, volume_decimals=4,
                                   mid=326.0)
        assert plan.action == action, book
        assert abs(plan.tracker_after - after) < 1e-12, book
        local_after = plan.tracker_after + ledger + plan.ledger_delta + residue
        assert abs(local_after - venue) < 1e-9, book
        assert plan.as_log()["book"] == book
    kept = se.plan_book_reseed(book_id=5, venue_net=0.2489, tracker_net=0.25, ledger=0.0, fee_residue=-0.0011,
                               pending=0.0, min_order=0.25, volume_decimals=4, mid=100.0)
    assert kept.action == se.RESEED_KEEP and kept.tracker_after == 0.25


def test_a_real_position_without_a_believable_price_waits():
    plan = se.plan_book_reseed(book_id=102, venue_net=0.238, tracker_net=0.0, ledger=0.0, fee_residue=-0.012,
                               pending=0.0, min_order=0.25, volume_decimals=4, mid=None)
    assert plan.action == se.RESEED_DEFER and plan.target == 0.25 and plan.tracker_after == 0.0
    pending = se.plan_book_reseed(book_id=9, venue_net=0.25, tracker_net=0.0, ledger=0.0, fee_residue=0.0,
                                  pending=0.25, min_order=0.25, volume_decimals=4, mid=50.0)
    assert pending.action == se.RESEED_KEEP                  # a pending seed already accounts for it
    no_ledger = se.plan_book_reseed(book_id=32, venue_net=0.1, tracker_net=0.0, ledger=0.0, fee_residue=0.0,
                                    pending=0.0, min_order=0.25, volume_decimals=4, mid=50.0,
                                    ledger_enabled=False)
    assert no_ledger.action == se.RESEED_REAL and no_ledger.tracker_after == 0.1


def test_only_exposure_reducing_placements_survive_a_resync():
    instructions = [
        _order(se.LIMIT, 1, se.BUY), _order(se.LIMIT, 1, se.SELL),         # flat book: both would open
        _order(se.LIMIT, 2, se.SELL), _order(se.MARKET, 2, se.SELL),       # long 0.25: exits
        _order(se.LIMIT, 2, se.BUY),                                       # long 0.25: adds
        _order(se.MARKET, 3, se.BUY), _order(se.MARKET, 3, se.BUY, 0.5),   # short 0.25: an exit, and a flip
        _order("CANCEL_ORDERS", 1),
        _order(se.LIMIT, 4, SimpleNamespace(value=se.SELL)),              # a deferred book: nothing
    ]
    removed = se.strip_exposure_increasing(instructions, net_by_book={1: 0.0, 2: 0.25, 3: -0.25, 4: 0.25},
                                           deferred={4}, eps=EPS)
    assert [(o.type, o.bookId, o.quantity) for o in instructions] == [
        (se.LIMIT, 2, 0.25), (se.MARKET, 2, 0.25), (se.MARKET, 3, 0.25), ("CANCEL_ORDERS", 1, 0.25)]
    assert sorted(removed) == [(1, se.LIMIT), (1, se.LIMIT), (2, se.LIMIT), (3, se.MARKET), (4, se.LIMIT)]
    outside = [_order(se.LIMIT, 1, se.BUY), _order(se.LIMIT, 4, se.SELL)]
    assert se.strip_exposure_increasing(outside, net_by_book={}, deferred={4}, eps=EPS,
                                        only_books={4}) == [(4, se.LIMIT)]
    assert [o.bookId for o in outside] == [1]
    assert se.order_direction(SimpleNamespace(direction="OrderDirection.SELL")) == se.SELL
    # The grid lift of A1.9.6 F11 is not a flip.
    lifted = [_order(se.LIMIT, 2, se.SELL, 0.25000000000000006)]
    assert se.strip_exposure_increasing(lifted, net_by_book={2: 0.25}, eps=EPS) == []


# ---- runtime: the Simple methods, executed from source ---------------------------------------

class _ExitLedger:
    def __init__(self, rows):
        self.rows = rows

    def reset(self):
        dropped, self.rows = self.rows, 0
        return dropped


class _QuoteStore:
    def __init__(self, rows):
        self.rows = rows

    def __len__(self):
        return self.rows

    def clear(self):
        self.rows = 0


class _Agent:
    def __init__(self):
        self.rows = []
        self._tick = 0
        self._open_positions = defaultdict(lambda: {"longs": deque(), "shorts": deque()})
        self._a196_legacy_dust_ledger = {}
        self._a1961_fee_residue = {}
        self._a1961_pending_seed = {}
        self._research_exchange_min_order_size = 0.25
        self.venue = {}
        self.mids = {}
        self.exit_ledger = _ExitLedger(2)

    def _emit(self, event_type, force=False, **payload):
        self.rows.append((event_type, payload))

    def rows_of(self, event_type):
        return [payload for kind, payload in self.rows if kind == event_type]

    def _execution_flat_epsilon(self):
        return EPS

    def _a198_enabled(self):
        return True

    def _a19_ledger_ref(self):
        return self.exit_ledger

    def _a196_volume_decimals(self, state=None):
        return 4

    def _a196_ledger_enabled(self):
        return True

    def _position_tracker_snapshot(self, book_id):
        pos = self._open_positions.get(book_id)
        if not pos:
            return SimpleNamespace(net_qty=0.0)
        return SimpleNamespace(net_qty=sum(q for _, q, _, _ in pos["longs"]) - sum(q for _, q, _, _ in pos["shorts"]))

    def _a195_venue_net_by_book(self, books):
        return {b: self.venue[b] for b in books if b in self.venue}

    def _a195_mid_by_book(self, books):
        return {b: self.mids[b] for b in books if self.mids.get(b)}


NAMESPACE = {name: getattr(module, name) for module in (se, rs, dl) for name in dir(module)
             if not name.startswith("__")}
NAMESPACE.update(extract_simulation_id=extract_simulation_id, Any=typing.Any)


def _harness(*names):
    attrs = {}
    for name in names:
        local = dict(NAMESPACE)
        exec("from __future__ import annotations\n" + textwrap.dedent(_method_source(name)), local)
        attrs[name] = local[name]
    # Pre-v6 contract: the overlay now calls the v6 capability methods directly, and this
    # suite pins its own build's behaviour, so it takes the fallbacks the probes used to.
    attrs.update(legacy_capability_attrs(skip=set(attrs)))
    return type("Harness", (_Agent,), attrs)


AUTHORITY = ("_a199_authorize_exit", "_a199_note_transition", "_a199_pending_table",
             "_a199_note_override", "_a199_note_own_fill", "_a199_exit_pending_enabled")


def test_the_authority_logs_the_state_and_every_refusal():
    agent = _harness(*AUTHORITY)()
    tick, risk, maker, taker, age = BOOK_49[0]
    agent._tick = tick
    base, vetoed = _stack(risk=risk, maker=maker, taker=taker, age=age, failed=0, touch=False)
    exit_kwargs = dict(maker_net_bps=maker, taker_net_bps=taker, inventory_qty=0.25, min_order=0.25,
                       taker_clip=0.25, is_dust=False, valid_opposite_touch=False, inventory_age=age,
                       failed_exit_count=0)
    inventory = SimpleNamespace(net_base=0.25, vwap_entry=271.14)
    final, rule, arm = agent._a199_authorize_exit(
        49, base_decision=base, decision=vetoed, exit_kwargs=exit_kwargs, inventory=inventory,
        book=CROSSED, position_risk_bps=risk,
    )
    assert final.action == ACTION_TAKER_EXIT and rule == rs.RULE_NOT_EXITING and arm is None
    agent._a199_note_override(49, {"a199_rule": rule, "a199_replaced": vetoed, "base_decision": base,
                                   "position_risk_bps": risk, **exit_kwargs})
    (enter,) = agent.rows_of("A199_EXIT_PENDING")
    assert enter["transition"] == rs.ENTER and enter["since_tick"] == 2755 and enter["evaluations"] == 1
    (refusal,) = agent.rows_of("A199_EXIT_AUTHORITY")
    assert refusal["rule"] == rs.RULE_NOT_EXITING and refusal["replaced_reason"] == "ABSOLUTE_PROTECTION_PARK"
    assert refusal["valid_opposite_touch"] == 0 and refusal["pending_since_tick"] == 2755
    assert refusal["a199_risk_state_version"] == rs.A199_RISK_STATE_VERSION
    assert agent._a199_pending_entered == 1 and agent._a199_rule_not_exiting == 1
    assert agent._a199_exit_pending[49].overrides == 1
    # An empty ask side keeps the frozen park: the close path reads a price that is not there.
    agent._tick = 2756
    final, rule, _ = agent._a199_authorize_exit(
        49, base_decision=base, decision=vetoed, exit_kwargs=exit_kwargs, inventory=inventory,
        book=_book(263.4, None), position_risk_bps=risk,
    )
    assert final is vetoed and rule is None
    agent._a199_note_own_fill(book_id=49, before=0.25, after=0.0)
    assert 49 not in agent._a199_exit_pending
    assert agent.rows_of("A199_EXIT_PENDING")[-1]["transition"] == rs.CLEAR_FLAT
    assert agent._a199_exit_pending_enabled()
    agent.research_a199_exit_pending_authority = False
    assert not agent._a199_exit_pending_enabled()


def test_a_silent_pending_exit_is_measured_from_the_response():
    agent = _harness("_a199_note_exit_stalls")()
    agent._a199_exit_pending = {49: _pending()}
    agent._open_positions[49]["longs"].append((2753, 0.25, 271.14, 0.0))
    for tick in (2759, 2760, 2761):
        agent._tick = tick
        assert agent._a199_note_exit_stalls(SimpleNamespace(instructions=[_order(se.LIMIT, 7, se.BUY)])) == 0
    agent._tick = 2762
    assert agent._a199_note_exit_stalls(SimpleNamespace(instructions=[_order(se.MARKET, 49, se.SELL)])) == 1
    (row,) = agent.rows_of("A199_EXIT_STALL")
    assert row["ticks"] == 3 and row["from_tick"] == 2759 and row["ended_by"] == "INSTRUCTION"
    assert row["tick"] == 2762 and agent._a199_stall_rows == 1 and agent._a199_stall_max_ticks == 3


EPOCH_METHODS = ("_a199_observe_epoch", "_a199_epoch_enabled", "_a199_pending_table")


def _state(ts, sim="1789341943289"):
    return SimpleNamespace(timestamp=ts, config=SimpleNamespace(simulation_id=sim))


def test_a_rewind_clears_every_order_registry_and_opens_a_resync():
    agent = _harness(*EPOCH_METHODS)()
    agent._direct_pending_exposure_orders = {(81, "buy", "70491"): object()}
    agent._direct_exchange_order_ownership = {2413951: object(), 2413953: object()}
    agent._a19_cancel_watch = {(81, 2413951): (2460, "REPRICE")}
    agent._research_quote_store = _QuoteStore(3)
    agent._a197_postfill_book = 93
    agent._tick = 2465
    assert agent._a199_observe_epoch(_state(57_156_000_000_000)) is None
    agent._tick = 2466
    assert agent._a199_observe_epoch(_state(57_157_000_000_000)) is None and not agent.rows
    event = agent._a199_observe_epoch(_state(57_121_000_000_000))
    assert event["rewind_s"] == -36.0
    assert not agent._direct_pending_exposure_orders and not agent._direct_exchange_order_ownership
    assert not agent._a19_cancel_watch and len(agent._research_quote_store) == 0
    assert agent._a197_postfill_book is None and agent.exit_ledger.rows == 0
    (row,) = agent.rows_of("A199_EPOCH_REWIND")
    assert row["tick"] == 2466 and row["state_tick"] == 2467 and row["exit_ledger_rows"] == 2
    assert row["registry_rows_cleared"] == 1 + 2 + 1 + 3 + 2
    assert row["registries"]["_direct_exchange_order_ownership"] == 2
    assert agent._a199_resync["since_tick"] == 2467
    assert agent._a199_resync["min_until_tick"] == 2467 + se.A199_RESYNC_MIN_TICKS
    assert agent._a199_resync["max_until_tick"] == 2467 + se.A199_RESYNC_MAX_TICKS
    agent._tick = 2467
    assert agent._a199_observe_epoch(_state(57_122_000_000_000)) is None
    assert len(agent.rows_of("A199_EPOCH_REWIND")) == 1 and agent._a199_epoch_rewinds == 1


def test_a_new_simulation_and_the_switch_leave_order_state_alone():
    agent = _harness(*EPOCH_METHODS)()
    agent._direct_exchange_order_ownership = {1: object()}
    agent._a199_observe_epoch(_state(57_157_000_000_000, sim="1789341943289"))
    assert agent._a199_observe_epoch(_state(1_000_000_000, sim="1789999999999")) is None
    assert agent._direct_exchange_order_ownership and not agent.rows
    off = _harness(*EPOCH_METHODS)()
    off.research_a199_epoch_resync = False
    off._direct_exchange_order_ownership = {1: object()}
    off._a199_observe_epoch(_state(57_157_000_000_000))
    assert off._a199_observe_epoch(_state(57_121_000_000_000)) is not None
    assert off._direct_exchange_order_ownership and not getattr(off, "_a199_resync", None) and not off.rows


RESYNC = ("_a199_service_resync", "_a199_apply_reseed", "_a199_close_resync", "_a199_pending_table",
          "_a199_note_transition", "_a199_resync_active", "_a199_entry_blocked", "_a199_strip_resync_exposure",
          # v5.0.2: the reseed keeps the residue ledger and recognizes a clip.
          "_v502_clip_tolerance", "_v502_add_residue", "_v502_count", "_v502_residue_abs",
          # v6.1: a RESEED_REAL restores this UID's saved FIFO lots when they still match.
          "_v600_tolerance", "_v61_on", "_v61_count", "_v61_restored_side")


def _open(agent, since=2467):
    agent._a199_resync = {"since_tick": since, "min_until_tick": since + se.A199_RESYNC_MIN_TICKS,
                          "max_until_tick": since + se.A199_RESYNC_MAX_TICKS, "reseeds": 0,
                          "entries_blocked": 0, "placements_stripped": 0}
    agent._a199_deferred = {}


def test_a_resync_rebuilds_diverged_books_and_resumes_on_the_first_clean_tick_past_the_ttl():
    agent = _harness(*RESYNC)()
    _open(agent)
    agent._tick = 2466
    agent._open_positions[81]["shorts"].append((2440, 0.25, 180.0, 0.0))    # a short the rewind undid
    agent._open_positions[5]["longs"].append((2400, 0.25, 100.0, 0.0))      # a long the venue still holds
    agent._a1961_fee_residue = {102: -0.012, 81: -0.003, 5: -0.0011}
    agent.venue = {102: 0.238, 81: -0.003, 5: 0.2489}
    agent.mids = {102: 325.4, 81: 180.2, 5: 100.1}
    agent._research_exit_attempts = {81: {"failed_exit_count": 2}}
    agent._direct_tail_recovery_active = {81: {"first_tick": 2450}}
    agent._a199_exit_pending = {81: _pending(sign=-1, entry=180.0, since=2460)}
    state = SimpleNamespace(books={102: object(), 81: object(), 5: object()})
    assert agent._a199_service_resync(state) == 2
    assert list(agent._open_positions[102]["longs"]) == [(2467, 0.25, 325.4, 0.0)]
    assert not agent._open_positions[81]["shorts"]
    assert list(agent._open_positions[5]["longs"]) == [(2400, 0.25, 100.0, 0.0)]
    assert 81 not in agent._research_exit_attempts and 81 not in agent._direct_tail_recovery_active
    assert 81 not in agent._a199_exit_pending
    seeds = {row["book"]: row for row in agent.rows_of("A199_EPOCH_RESEED")}
    assert seeds[102]["action"] == se.RESEED_REAL and seeds[81]["action"] == se.RESEED_CLEAR and 5 not in seeds
    assert seeds[81]["runtime_cleared"] == ["_research_exit_attempts", "_direct_tail_recovery_active"]
    assert agent.rows_of("A199_EXIT_PENDING")[-1]["transition"] == rs.CLEAR_EPOCH
    assert agent._a199_entry_blocked(7) and agent._a199_resync["entries_blocked"] == 1
    agent._tick = 2470                  # clean, but a resurrected order could still be resting
    assert agent._a199_service_resync(state) == 0 and agent._a199_resync_active()
    agent._tick = 2474                  # state 2,475: the first clean tick past the TTL horizon
    assert agent._a199_service_resync(state) == 0 and not agent._a199_resync_active()
    (resume,) = agent.rows_of("A199_EPOCH_RESUME")
    assert resume["tick"] == 2475 and resume["since_tick"] == 2467 and resume["clean"] == 1
    assert resume["reseeds"] == 2 and resume["entries_blocked"] == 1
    assert not agent._a199_entry_blocked(7)


def test_a_divergence_that_keeps_returning_cannot_hold_entries_shut():
    agent = _harness(*RESYNC)()
    _open(agent, since=10)
    agent.mids = {3: 50.0}
    state = SimpleNamespace(books={3: object()})
    for tick in range(9, 9 + se.A199_RESYNC_MAX_TICKS + 1):
        agent._tick = tick
        agent.venue = {3: 0.25 if tick % 2 else -0.25}
        agent._a199_service_resync(state)
    assert not agent._a199_resync_active()
    (resume,) = agent.rows_of("A199_EPOCH_RESUME")
    assert resume["clean"] == 0 and resume["tick"] == 10 + se.A199_RESYNC_MAX_TICKS


def test_a_position_without_a_price_blocks_only_its_own_book_until_it_has_one():
    agent = _harness(*RESYNC)()
    _open(agent)
    agent._a199_resync["min_until_tick"] = 2467
    agent._tick = 2466
    agent._a1961_fee_residue = {102: -0.012}
    agent.venue = {102: 0.238, 5: 0.0}
    agent.mids = {5: 100.0}             # book 102's touch is crossed: no believable mid
    state = SimpleNamespace(books={102: object(), 5: object()})
    assert agent._a199_service_resync(state) == 1 and agent._a199_deferred[102]["target"] == 0.25
    agent._tick = 2467
    assert agent._a199_service_resync(state) == 0 and not agent._a199_resync_active()
    assert agent.rows_of("A199_EPOCH_RESUME")[0]["deferred_books"] == [102]
    assert agent._a199_entry_blocked(102) and not agent._a199_entry_blocked(5)
    agent.mids = {5: 100.0, 102: 325.4}
    agent._tick = 2470
    assert agent._a199_service_resync(state) == 1
    assert list(agent._open_positions[102]["longs"]) == [(2471, 0.25, 325.4, 0.0)]
    assert not agent._a199_deferred and not agent._a199_entry_blocked(102)


def test_the_exposure_filter_acts_only_while_there_is_something_to_protect():
    agent = _harness(*RESYNC)()
    agent._a199_resync = {}
    agent._a199_deferred = {}
    agent._tick = 2468
    agent._open_positions[2]["longs"].append((2400, 0.25, 10.0, 0.0))
    response = SimpleNamespace(instructions=[_order(se.LIMIT, 1, se.BUY), _order(se.LIMIT, 2, se.SELL),
                                             _order(se.LIMIT, 2, se.BUY)])
    assert agent._a199_strip_resync_exposure(response) == 0 and len(response.instructions) == 3
    _open(agent)
    assert agent._a199_strip_resync_exposure(response) == 2
    assert [(o.bookId, o.direction) for o in response.instructions] == [(2, se.SELL)]
    (row,) = agent.rows_of("A199_EPOCH_EXPOSURE_STRIP")
    assert row["removed"] == 2 and row["books"] == [1, 2] and row["in_resync"] == 1
    assert agent._a199_resync["placements_stripped"] == 2 and agent._a199_epoch_placements_stripped == 2


# ---- wiring ------------------------------------------------------------------------------------

def test_every_registry_the_epoch_touches_is_a_real_agent_attribute():
    sources = "\n".join(p.read_text(errors="ignore") for p in sorted(STRATEGY.glob("*.py")))
    names = se.A199_EPOCH_CLEAR_MAPS + se.A199_RESEED_BOOK_MAPS + se.A199_RESEED_BOOK_SETS
    assert len(set(names)) == len(names)
    for name in names:
        assert re.search(r"self\." + re.escape(name) + r"\s*(:[^=\n]*)?=[^=]", sources), name


def test_the_controllers_are_wired_where_they_hold_authority():
    update = _method_source("update")
    assert update.index("self._a199_observe_epoch(state)") < update.index("return super().update(state)")
    respond = _method_source("respond")
    assert (respond.index("self._a1961_service_pending_seed(state)") < respond.index("self._a199_service_resync(state)")
            < respond.index("response = super().respond(state)"))
    assert (respond.index("self._a196_snap_outgoing_quantities(response, state)")
            < respond.index("self._a199_strip_resync_exposure(response)")
            < respond.index("self._a195_cancel_orphan_orders(response, state)"))
    assert respond.index("self._a191_service_reprice_cancels(response, state)") < respond.index(
        "self._a199_note_exit_stalls(response)")
    quotes = _method_source("_place_skewed_quotes")
    assert (quotes.index("if self._research_in_transition_quarantine():") < quotes.index(
        "if self._a199_entry_blocked(book_id):")
        < quotes.index('if str(getattr(inventory, "band", "FLAT") or "FLAT").upper() != "FLAT":'))
    chooser = _method_source("_research_apply_unified_exit")
    veto = chooser.index("decision = apply_positive_maker_kappa_veto(")
    authority = chooser.index("decision, a199_rule, a198_arm = self._a199_authorize_exit(")
    fallback = chooser.index("decision, a198_arm = restore_absolute_taker(")
    assert veto < authority < fallback < chooser.index('captured["pre_a1744_decision"] = pre_a1744_decision')
    assert "if self._a199_exit_pending_enabled():" in chooser
    assert chooser.index('setattr(module, "choose_position_exit", original)') < chooser.index(
        "self._a199_note_override(book_id_outer, captured)")
    fill = _method_source("_research_on_own_fill")
    assert fill.index("self._a197_note_own_fill(") < fill.index("self._a199_note_own_fill(")


def test_stats_version_switches_and_launcher():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_4"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_4"' in SIMPLE
    assert rs.A199_RISK_STATE_VERSION.endswith("a1_9_9") and se.A199_SESSION_EPOCH_VERSION.endswith("a1_9_9")
    for key in ("direct_a199_version", "direct_a199_session_epoch_version", "direct_a199_exit_pending_authority",
                "direct_a199_epoch_resync", "direct_a199_pending_books", "direct_a199_pending_entered",
                "direct_a199_pending_cleared", "direct_a199_rule_loss_maker", "direct_a199_rule_not_exiting",
                "direct_a199_stall_rows", "direct_a199_stall_max_ticks", "direct_a199_epoch_rewinds",
                "direct_a199_epoch_registry_rows_cleared", "direct_a199_epoch_reseeds",
                "direct_a199_epoch_deferred_books", "direct_a199_epoch_entries_blocked",
                "direct_a199_epoch_placements_stripped", "direct_a199_epoch_resync_active",
                "direct_a199_epoch_resyncs_closed"):
        assert f'stats["{key}"]' in SIMPLE, key
    assert ("strategy1_direct_v4_16_2_a1_9_9) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; "
            "A197_BUILD=1; A198_BUILD=1; A199_BUILD=1") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    for switch in ("research_a199_exit_pending_authority=1", "research_a199_epoch_resync=1",
                   "research_a198_absolute_taker_authority=1", "research_profitable_exit_ttl_ms=4000"):
        assert switch in params, switch
    assert "tests/test_research_a1_9_9_controllers.py" in LAUNCHER
    assert "[preflight] A1.9.9 exit-pending authority / session epoch resync PASS" in LAUNCHER
    for literal in ("if action in (ACTION_WAIT, ACTION_PARK_EXIT):",
                    "if action == ACTION_MAKER_EXIT and maker + 1e-12 < float(grace_floor_bps):",
                    "executable = qty + 1e-12 >= floor and not bool(is_dust) and bool(touch_two_sided)"):
        assert literal in RISK and literal in LAUNCHER, literal
    assert "if now < last:" in EPOCH and "if now < last:" in LAUNCHER
    # The resync must outlive the longest resting order this build sends.
    assert se.A199_RESYNC_MIN_TICKS * 1000 > 4000 + 1000
