"""A1.9.9.1: a pending ABSOLUTE position owns its book -- no maker exit rests at any price.

Measured on the A1.9.9 run, log 20260914_083519, ticks 1-3,732 (G10 0.0196).  A1.9.9 refused a
loss maker, WAIT and PARK once a position was pending, but let a maker at or above +1 bps rest.
Book 74 reached ABSOLUTE and rested an A1.7.4.4 maker for 109 ticks, then for 129 more.  The
A1.7.4.4 arm ignores the failed-exit count, and the A1.7.5 hold budget spends only on ticks the
book is evaluated -- a resting exit skips evaluation -- so neither ended the hold.  Across A1.9.7,
A1.9.8 and A1.9.9, 2 of the 14 episodes that rested a positive maker after ABSOLUTE ended positive.
"""
import ast
import textwrap
import typing
from collections import defaultdict, deque
from pathlib import Path
from types import SimpleNamespace

import research_direct_risk_state as rs
from research_direct_absolute_authority import restore_absolute_taker
from research_direct_exit import choose_observable_position_exit
from research_direct_positive_maker_kappa import apply_positive_maker_kappa_veto
from research_direct_tail_recovery import choose_tail_recovery_override
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    BAND_ABSOLUTE,
    BAND_HARD_ESCAPE,
    BAND_NORMAL,
)

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RISK = (STRATEGY / "research_direct_risk_state.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
EPS = 5e-05


def _method_source(name):
    tree = ast.parse(SIMPLE)
    defs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Strategy1_Research_Simple":
            defs += [ast.get_source_segment(SIMPLE, n) for n in node.body
                     if isinstance(n, ast.FunctionDef) and n.name == name]
    assert defs, name
    return defs[-1]


def _function_source(text, name):
    for node in ast.parse(text).body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return ast.get_source_segment(text, node)
    raise AssertionError(name)


def _book(bid, ask):
    return SimpleNamespace(bids=[SimpleNamespace(price=bid, quantity=1.0)] if bid else [],
                           asks=[SimpleNamespace(price=ask, quantity=1.0)] if ask else [])


def _stack(*, risk, maker, taker, age, failed, qty=0.25, touch=True):
    """The Direct chooser's frozen stack up to the point the exit authority decides."""
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
        risk_velocity_bps_per_tick_value=0.0, inventory_qty=qty, inventory_age=age,
        failed_exit_count=failed, catastrophic_hard_risk=False, reduction_executable=executable,
    )
    vetoed = apply_positive_maker_kappa_veto(
        base_decision=recovery, maker_net_bps=maker, taker_net_bps=taker, maker_executable=True,
        catastrophic_hard_risk=False, inventory_qty=qty,
    )
    return base, vetoed


def _authorize(pending, base, decision, *, maker, taker, allow=False, qty=0.25, dust=False, two_sided=True):
    return rs.authorize_exit(
        pending=pending, base_decision=base, decision=decision, maker_net_bps=maker,
        taker_net_bps=taker, inventory_qty=qty, min_order=0.25, taker_clip=0.25, is_dust=dust,
        touch_two_sided=two_sided, allow_positive_maker=allow,
    )


def _pending(sign=1, entry=100.0, since=1650):
    return rs.ExitPending(sign=sign, entry=entry, since_tick=since)


# (book, tick, position risk, maker net, taker net, age, failed exits): the A1.9.9 run's pending
# evaluations that the A1.7.4.4 arm turned into a resting maker.
EPISODES = [
    (74, 1657, -26.6, 75.0, -38.1, 37, 9),
    (74, 1875, -42.1, 65.4, -51.4, 12, 3),
    (85, 68, -30.2, 12.7, -37.2, 5, 1),
    (11, 2190, -28.0, 16.7, -38.2, 6, 1),
]


# ---- the rule ------------------------------------------------------------------------------------

def test_the_a1_9_9_positive_maker_episodes_now_ship_the_frozen_reduce():
    for book, tick, risk, maker, taker, age, failed in EPISODES:
        base, vetoed = _stack(risk=risk, maker=maker, taker=taker, age=age, failed=failed)
        assert base.risk_band == BAND_ABSOLUTE and base.reason == "ABSOLUTE_PROTECTION_REDUCE", book
        assert vetoed.action == ACTION_MAKER_EXIT and vetoed.corridor_action == "DIRECT_POSITIVE_MAKER_KAPPA_A1744"
        # A1.9.9, as it ran: the positive maker rested.
        final, rule, arm = _authorize(_pending(), base, vetoed, maker=maker, taker=taker, allow=True)
        assert final is vetoed and rule is None and arm is None, (book, tick)
        final, rule, arm = _authorize(_pending(), base, vetoed, maker=maker, taker=taker)
        assert final is base and rule == rs.RULE_POSITIVE_MAKER and arm is None, (book, tick)
        assert final.action == ACTION_TAKER_EXIT and final.selected_qty == 0.25


def test_a_maker_from_any_band_becomes_the_pending_taker():
    # The A1.7.2 grace maker, a HARD_ESCAPE positive veto, and a NORMAL maker after a bounce.  The
    # bounce gives up the maker-taker spread on a winning exit and adds no cubic downside.
    cases = [
        (dict(risk=-30.0, maker=5.1, taker=-34.5, age=2.0, failed=0), BAND_ABSOLUTE, "ABSOLUTE_POSITIVE_MAKER_GRACE"),
        (dict(risk=-22.0, maker=5.0, taker=-40.0, age=5.0, failed=0), BAND_HARD_ESCAPE, "HARD_ESCAPE_POSITIVE_MAKER_VETO"),
        (dict(risk=150.0, maker=167.3, taker=140.0, age=10.0, failed=0), BAND_NORMAL, "NORMAL_MAKER_NET"),
    ]
    for case, band, reason in cases:
        base, decision = _stack(**case)
        assert base.risk_band == band and decision.action == ACTION_MAKER_EXIT and decision.reason == reason
        final, rule, arm = _authorize(_pending(), base, decision, maker=case["maker"], taker=case["taker"])
        assert rule == rs.RULE_POSITIVE_MAKER and arm is None, reason
        assert final.action == ACTION_TAKER_EXIT and final.risk_band == BAND_ABSOLUTE
        assert final.reason == rs.A199_PENDING_REASON and final.corridor_action == rs.A199_PENDING_CORRIDOR
        assert final.selected_qty == 0.25
        assert final.maker_exit_utility == case["maker"] and final.taker_exit_utility == case["taker"]


def test_a_position_that_is_not_pending_keeps_every_maker():
    book, tick, risk, maker, taker, age, failed = EPISODES[0]
    cases = [(_stack(risk=risk, maker=maker, taker=taker, age=age, failed=failed), maker, taker),
             (_stack(risk=-22.0, maker=5.0, taker=-40.0, age=5.0, failed=0), 5.0, -40.0),
             (_stack(risk=-30.0, maker=5.1, taker=-34.5, age=2.0, failed=0), 5.1, -34.5)]
    for (base, decision), mk, tk in cases:
        final, rule, arm = _authorize(None, base, decision, maker=mk, taker=tk)
        assert final is decision and rule is None and arm is None


def test_ownership_needs_an_executable_reduction():
    book, tick, risk, maker, taker, age, failed = EPISODES[0]
    base, vetoed = _stack(risk=risk, maker=maker, taker=taker, age=age, failed=failed)
    for kwargs in (dict(two_sided=False), dict(dust=True), dict(qty=0.1)):
        final, rule, _ = _authorize(_pending(), base, vetoed, maker=maker, taker=taker, **kwargs)
        assert final is vetoed and rule is None, kwargs


def test_the_earlier_rules_still_decide_first():
    # R1: book 95 of the A1.9.7 run, the A1.9.8 recovery-maker arm.
    base, vetoed = _stack(risk=-31.3, maker=-21.0, taker=-40.5, age=2.0, failed=0)
    expected, arm = restore_absolute_taker(base_decision=base, decision=vetoed)
    final, rule, got = _authorize(_pending(), base, vetoed, maker=-21.0, taker=-40.5)
    assert final is expected and rule == rs.RULE_A198_ARM and got == arm
    # R2: a loss maker keeps its own label.
    base, hard = _stack(risk=-22.0, maker=-20.0, taker=-40.0, age=5.0, failed=0)
    final, rule, _ = _authorize(_pending(), base, hard, maker=-20.0, taker=-40.0)
    assert final.action == ACTION_TAKER_EXIT and rule == rs.RULE_LOSS_MAKER
    # R3: WAIT after a bounce on a crossed touch.
    base, wait = _stack(risk=10.0, maker=-5.0, taker=-3.0, age=40.0, failed=0, touch=False)
    assert wait.action == ACTION_WAIT
    final, rule, _ = _authorize(_pending(), base, wait, maker=-5.0, taker=-3.0)
    assert final.action == ACTION_TAKER_EXIT and rule == rs.RULE_NOT_EXITING
    # A taker passes.
    base, same = _stack(risk=-40.0, maker=-50.0, taker=-45.0, age=5.0, failed=0)
    assert same.action == ACTION_TAKER_EXIT
    final, rule, _ = _authorize(_pending(), base, same, maker=-50.0, taker=-45.0)
    assert final is same and rule is None


def test_r4_sits_after_the_loss_maker_rule_and_before_the_not_exiting_rule():
    body = _function_source(RISK, "authorize_exit")
    r2 = body.index("if action == ACTION_MAKER_EXIT and maker + 1e-12 < float(grace_floor_bps):")
    r4 = body.index("if action == ACTION_MAKER_EXIT and not allow_positive_maker:")
    r3 = body.index("if action in (ACTION_WAIT, ACTION_PARK_EXIT):")
    assert body.index("if not executable:") < r2 < r4 < r3
    assert "RULE_POSITIVE_MAKER, None" in body[r4:r3]


# ---- runtime: the Simple methods, executed from source ---------------------------------------

class _Agent:
    def __init__(self):
        self.rows = []
        self._tick = 0
        self._open_positions = defaultdict(lambda: {"longs": deque(), "shorts": deque()})

    def _emit(self, event_type, force=False, **payload):
        self.rows.append((event_type, payload))

    def rows_of(self, event_type):
        return [payload for kind, payload in self.rows if kind == event_type]

    def _execution_flat_epsilon(self):
        return EPS

    def _a198_enabled(self):
        return True


NAMESPACE = {name: getattr(rs, name) for name in dir(rs) if not name.startswith("__")}
NAMESPACE.update(Any=typing.Any)
AUTHORITY = ("_a199_authorize_exit", "_a199_note_transition", "_a199_pending_table", "_a199_note_override")


def _harness(*names):
    attrs = {}
    for name in names:
        local = dict(NAMESPACE)
        exec("from __future__ import annotations\n" + textwrap.dedent(_method_source(name)), local)
        attrs[name] = local[name]
    return type("Harness", (_Agent,), attrs)


def _run_book_74(agent):
    book, tick, risk, maker, taker, age, failed = EPISODES[0]
    agent._tick = tick
    base, vetoed = _stack(risk=risk, maker=maker, taker=taker, age=age, failed=failed)
    exit_kwargs = dict(maker_net_bps=maker, taker_net_bps=taker, inventory_qty=0.25, min_order=0.25,
                       taker_clip=0.25, is_dust=False, valid_opposite_touch=True, inventory_age=age,
                       failed_exit_count=failed)
    final, rule, arm = agent._a199_authorize_exit(
        book, base_decision=base, decision=vetoed, exit_kwargs=exit_kwargs,
        inventory=SimpleNamespace(net_base=0.25, vwap_entry=100.0), book=_book(99.6, 100.4),
        position_risk_bps=risk,
    )
    return base, vetoed, exit_kwargs, final, rule, arm


def test_the_authority_counts_and_logs_the_refused_positive_maker():
    agent = _harness(*AUTHORITY)()
    base, vetoed, exit_kwargs, final, rule, arm = _run_book_74(agent)
    assert final is base and rule == rs.RULE_POSITIVE_MAKER and arm is None
    agent._a199_note_override(74, {"a199_rule": rule, "a199_replaced": vetoed, "base_decision": base,
                                   "position_risk_bps": -26.6, **exit_kwargs})
    (row,) = agent.rows_of("A199_EXIT_AUTHORITY")
    assert row["rule"] == "POSITIVE_MAKER" and row["replaced_action"] == ACTION_MAKER_EXIT
    assert row["replaced_reason"] == "A1744_POSITIVE_MAKER_RISK_VETO" and row["failed_exit_count"] == 9
    assert row["base_reason"] == "ABSOLUTE_PROTECTION_REDUCE" and row["pending_since_tick"] == 1657
    assert agent._a1991_rule_positive_maker == 1 and agent._a199_exit_pending[74].overrides == 1
    assert not getattr(agent, "_a199_rule_loss_maker", 0) and not getattr(agent, "_a199_rule_not_exiting", 0)


def test_the_switch_off_restores_a1_9_9():
    agent = _harness(*AUTHORITY)()
    agent.research_a1991_pending_owns_book = False
    base, vetoed, _, final, rule, _ = _run_book_74(agent)
    assert final is vetoed and rule is None
    assert agent._a199_exit_pending[74].overrides == 0


# ---- wiring ------------------------------------------------------------------------------------

def test_the_rule_is_wired_counted_and_launched():
    authority = _method_source("_a199_authorize_exit")
    assert 'allow_positive_maker=not bool(getattr(self, "research_a1991_pending_owns_book", True)),' in authority
    assert "rule in (RULE_LOSS_MAKER, RULE_NOT_EXITING, RULE_POSITIVE_MAKER)" in authority
    chooser = _method_source("_research_apply_unified_exit")
    assert "a199_rule in (RULE_LOSS_MAKER, RULE_NOT_EXITING, RULE_POSITIVE_MAKER)" in chooser
    assert "elif rule == RULE_POSITIVE_MAKER:" in _method_source("_a199_note_override")
    assert "self.research_a1991_pending_owns_book = self._as_bool(" in SIMPLE
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v5_0_2"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v5_0_2"' in SIMPLE
    assert rs.A1991_PENDING_OWNER_VERSION.endswith("a1_9_9_1") and rs.RULE_POSITIVE_MAKER == "POSITIVE_MAKER"
    for key in ("direct_a1991_version", "direct_a1991_pending_owns_book", "direct_a1991_rule_positive_maker"):
        assert f'stats["{key}"]' in SIMPLE, key
    assert ("strategy1_direct_v4_16_2_a1_9_9_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; "
            "A197_BUILD=1; A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    for switch in ("research_a1991_pending_owns_book=1", "research_a199_exit_pending_authority=1",
                   "research_a199_epoch_resync=1", "research_a198_absolute_taker_authority=1"):
        assert switch in params, switch
    assert "tests/test_research_a1_9_9_1_pending_owner.py" in LAUNCHER
    assert "[preflight] A1.9.9.1 pending position owns its book PASS" in LAUNCHER
    assert "if action == ACTION_MAKER_EXIT and not allow_positive_maker:" in LAUNCHER
    assert 'allow_positive_maker=not bool(getattr(self, "research_a1991_pending_owns_book", True)),' in LAUNCHER
