"""A1.9.8: ABSOLUTE_PROTECTION keeps its taker against the loss-recovery maker arms.

Measured on the A1.9.7 run, log 20260913_185830, ticks 1-4,000: 26 round trips got a
resting maker exit priced at a loss at their first ABSOLUTE evaluation.  None was
positive, 24 still ended on a taker, and they carried 58% of all cubic downside.
"""
import ast
import textwrap
import typing
from pathlib import Path
from types import SimpleNamespace

import research_direct_absolute_authority as aa
from research_direct_absolute_authority import (
    A198_ABSOLUTE_AUTHORITY_VERSION,
    ARM_RECOVERY_MAKER,
    ARM_RELATIVE_VETO,
    loss_recovery_arm,
    restore_absolute_taker,
)
from research_direct_exit import choose_observable_position_exit
from research_direct_positive_maker_kappa import apply_positive_maker_kappa_veto, classify_a1744_outcome
from research_direct_tail_recovery import choose_tail_recovery_override
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_PARK_EXIT,
    ACTION_TAKER_EXIT,
    BAND_ABSOLUTE,
    BAND_HARD_ESCAPE,
)

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_direct_absolute_authority.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()


def _method_source(name):
    tree = ast.parse(SIMPLE)
    defs = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "Strategy1_Research_Simple":
            defs += [ast.get_source_segment(SIMPLE, n) for n in node.body
                     if isinstance(n, ast.FunctionDef) and n.name == name]
    assert defs, name
    return defs[-1]


def _chain(*, risk, maker, taker, age, failed, qty=0.25, velocity=0.0, catastrophic=False, enabled=True):
    """The Direct chooser's decision stack, in the order Simple runs it."""
    base = choose_observable_position_exit(
        maker_net_bps=maker, taker_net_bps=taker, p_maker_fill=0.1, unrealized_bps=risk,
        inventory_qty=qty, inventory_age=age, failed_exit_count=failed,
        catastrophic_hard_risk=catastrophic, min_order=0.25, taker_clip=0.25,
        hard_escape_min_age_ticks=2.0, positive_maker_veto_floor_bps=1.0,
        positive_maker_veto_max_failed_exits=4, absolute_positive_maker_veto_floor_bps=1.0,
        absolute_positive_maker_veto_max_failed_exits=1,
    )
    recovery = choose_tail_recovery_override(
        base_decision=base, maker_net_bps=maker, taker_net_bps=taker, position_risk_bps=risk,
        risk_velocity_bps_per_tick_value=velocity, inventory_qty=qty, inventory_age=age,
        failed_exit_count=failed, catastrophic_hard_risk=catastrophic, reduction_executable=True,
    )
    vetoed = apply_positive_maker_kappa_veto(
        base_decision=recovery, maker_net_bps=maker, taker_net_bps=taker, maker_executable=True,
        catastrophic_hard_risk=catastrophic, inventory_qty=qty,
    )
    final, arm = restore_absolute_taker(base_decision=base, decision=vetoed, enabled=enabled)
    return SimpleNamespace(base=base, recovery=recovery, vetoed=vetoed, final=final, arm=arm)


# ---- the live defect, replayed through the frozen decision stack --------------------------

BOOK_95 = [  # (tick, position risk, maker net, taker net) at the first ABSOLUTE evaluation, age 2
    (3166, -31.3, -21.0, -40.5),   # the taker fired on tick 3168 and realized -109.5 bps
    (3192, -28.0, -17.6, -38.5),   # tick 3194, -129.9 bps
    (3200, -29.4, -18.0, -38.7),   # tick 3202, -99.8 bps
]


def test_book_95_recovery_maker_is_the_live_defect_and_the_taker_is_restored():
    for _tick, risk, maker, taker in BOOK_95:
        c = _chain(risk=risk, maker=maker, taker=taker, age=2.0, failed=0)
        assert c.base.reason == "ABSOLUTE_PROTECTION_REDUCE" and c.base.risk_band == BAND_ABSOLUTE
        assert c.vetoed.action == ACTION_MAKER_EXIT and c.vetoed.reason == "ABSOLUTE_RECOVERY_MAKER_EXIT"
        assert c.final is c.base and c.arm == ARM_RECOVERY_MAKER
        assert c.final.action == ACTION_TAKER_EXIT and c.final.selected_qty == 0.25


def test_post_fill_protection_now_reaches_the_taker():
    """A1.9.7 P1 acted twice; both decisions were recovery makers that P1 had to refuse."""
    for risk, maker, taker in ((-25.5, -15.4, -39.7),    # book 87, tick 3543, age 1
                               (-33.1, -2.1, -50.8)):    # book 98, tick 4174, age 1
        c = _chain(risk=risk, maker=maker, taker=taker, age=1.0, failed=0)
        assert c.vetoed.reason == "ABSOLUTE_RECOVERY_MAKER_EXIT"
        assert c.final.reason == "ABSOLUTE_PROTECTION_REDUCE" and c.arm == ARM_RECOVERY_MAKER


def test_relative_veto_is_replaced_in_absolute():
    """The median ABSOLUTE relative veto on the A1.9.7 run: maker -24.9, taker -54.5."""
    c = _chain(risk=-40.0, maker=-24.9, taker=-54.5, age=20.0, failed=3)
    assert c.recovery is c.base                                  # a failed exit already ended the recovery arm
    assert c.vetoed.corridor_action == "DIRECT_POSITIVE_MAKER_KAPPA_A175_RELATIVE"
    assert c.final is c.base and c.arm == ARM_RELATIVE_VETO
    # The shipped decision is no veto: no A175 row is emitted and no hold budget is spent.
    assert classify_a1744_outcome(
        base_decision=c.recovery, final_decision=c.final, maker_net_bps=-24.9, taker_net_bps=-54.5,
        maker_executable=True, catastrophic_hard_risk=False,
    ) is None


def test_a_maker_already_below_the_absolute_floor_crossed_anyway():
    c = _chain(risk=-98.0, maker=-82.3, taker=-112.6, age=4.0, failed=0)   # book 95, tick 3168
    assert c.vetoed is c.base and c.final is c.base and c.arm is None


# ---- what A1.9.8 leaves alone ------------------------------------------------------------

def test_a_positive_maker_keeps_priority_in_absolute():
    grace = _chain(risk=-30.0, maker=3.0, taker=-40.0, age=2.0, failed=0)
    assert grace.final.reason == "ABSOLUTE_POSITIVE_MAKER_GRACE" and grace.arm is None
    strong = _chain(risk=-30.0, maker=12.0, taker=-35.0, age=10.0, failed=2)
    assert strong.final.action == ACTION_MAKER_EXIT
    assert strong.final.corridor_action == "DIRECT_POSITIVE_MAKER_KAPPA_A1744" and strong.arm is None


def test_hard_escape_and_defensive_keep_their_recovery_makers():
    book_69 = _chain(risk=-20.3, maker=-21.2, taker=-44.2, age=3.0, failed=0)   # tick 1944, 20% of cubic
    assert book_69.base.risk_band == BAND_HARD_ESCAPE
    assert book_69.final.corridor_action == "DIRECT_POSITIVE_MAKER_KAPPA_A175_RELATIVE" and book_69.arm is None
    hard = _chain(risk=-22.0, maker=-20.0, taker=-40.0, age=5.0, failed=0)
    assert hard.final.reason == "HARD_RECOVERY_MAKER_EXIT" and hard.arm is None
    defensive = _chain(risk=-12.0, maker=-10.0, taker=-25.0, age=5.0, failed=0, velocity=-1.0)
    assert defensive.final.reason == "RECOVERY_MAKER_EXIT" and defensive.arm is None


def test_unreducible_and_catastrophic_positions_stay_on_the_frozen_path():
    park = _chain(risk=-30.0, maker=-20.0, taker=-40.0, age=2.0, failed=0, qty=0.1)
    assert park.base.action == ACTION_PARK_EXIT
    assert park.final.reason == "ABSOLUTE_RECOVERY_MAKER_EXIT" and park.arm is None
    max_exposure = _chain(risk=-10.0, maker=-20.0, taker=-40.0, age=2.0, failed=0, catastrophic=True)
    assert max_exposure.final.reason == "ABSOLUTE_PROTECTION_REDUCE" and max_exposure.arm is None


def test_the_switch_restores_frozen_behaviour():
    _tick, risk, maker, taker = BOOK_95[0]
    c = _chain(risk=risk, maker=maker, taker=taker, age=2.0, failed=0, enabled=False)
    assert c.final.reason == "ABSOLUTE_RECOVERY_MAKER_EXIT" and c.arm is None


def test_loss_recovery_arm_names_only_the_two_arms():
    def d(action, reason, corridor=None):
        return SimpleNamespace(action=action, reason=reason, corridor_action=corridor)

    assert loss_recovery_arm(d(ACTION_MAKER_EXIT, "ABSOLUTE_RECOVERY_MAKER_EXIT")) == ARM_RECOVERY_MAKER
    assert loss_recovery_arm(d(ACTION_MAKER_EXIT, "A1744_POSITIVE_MAKER_RISK_VETO",
                               "DIRECT_POSITIVE_MAKER_KAPPA_A175_RELATIVE")) == ARM_RELATIVE_VETO
    assert loss_recovery_arm(d(ACTION_MAKER_EXIT, "A1744_POSITIVE_MAKER_RISK_VETO",
                               "DIRECT_POSITIVE_MAKER_KAPPA_A1744")) is None
    assert loss_recovery_arm(d(ACTION_TAKER_EXIT, "ABSOLUTE_RECOVERY_MAKER_EXIT")) is None
    assert loss_recovery_arm(d(ACTION_MAKER_EXIT, "HARD_RECOVERY_MAKER_EXIT")) is None
    assert restore_absolute_taker(base_decision=None, decision="kept") == ("kept", None)


# ---- runtime telemetry ---------------------------------------------------------------------

class _Base:
    def __init__(self):
        self.rows = []

    def _emit(self, event_type, force=False, **payload):
        self.rows.append((event_type, payload))

    def rows_of(self, event_type):
        return [payload for kind, payload in self.rows if kind == event_type]


def _harness(*names):
    namespace = {name: getattr(aa, name) for name in dir(aa) if not name.startswith("__")}
    namespace.update(Any=typing.Any)
    attrs = {}
    for name in names:
        local = dict(namespace)
        exec("from __future__ import annotations\n" + textwrap.dedent(_method_source(name)), local)
        attrs[name] = local[name]
    return type("Harness", (_Base,), attrs)


def test_a_restore_is_counted_and_logged():
    agent = _harness("_a198_note_restore", "_a198_enabled")()
    _tick, risk, maker, taker = BOOK_95[0]
    c = _chain(risk=risk, maker=maker, taker=taker, age=2.0, failed=0)
    captured = {"a198_arm": c.arm, "a198_replaced": c.vetoed, "position_risk_bps": risk,
                "maker_net_bps": maker, "taker_net_bps": taker, "inventory_age": 2.0, "failed_exit_count": 0}
    agent._tick = 3166
    agent._a198_note_restore(95, captured)
    agent._a197_postfill_book = 87
    agent._tick = 3543
    agent._a198_note_restore(87, {**captured, "a198_arm": ARM_RELATIVE_VETO})
    assert agent._a198_restores == 2 and agent._a198_restored_recovery == 1
    assert agent._a198_restored_relative == 1 and agent._a198_restored_postfill == 1
    first, second = agent.rows_of("A198_ABSOLUTE_TAKER")
    assert first["book"] == 95 and first["arm"] == ARM_RECOVERY_MAKER and first["postfill"] == 0
    assert first["replaced_reason"] == "ABSOLUTE_RECOVERY_MAKER_EXIT" and first["taker_net_bps"] == -40.5
    assert first["a198_absolute_authority_version"] == A198_ABSOLUTE_AUTHORITY_VERSION
    assert second["book"] == 87 and second["postfill"] == 1 and second["tick"] == 3543
    assert agent._a198_enabled()
    agent.research_a198_absolute_taker_authority = False
    assert not agent._a198_enabled()


# ---- wiring --------------------------------------------------------------------------------

def test_the_chooser_restores_after_both_overlays_and_before_telemetry():
    method = _method_source("_research_apply_unified_exit")
    veto = method.index("decision = apply_positive_maker_kappa_veto(")
    restore = method.index("decision, a198_arm = restore_absolute_taker(")
    assert method.index("decision = choose_tail_recovery_override(") < veto < restore
    assert restore < method.index('captured["pre_a1744_decision"] = pre_a1744_decision')
    assert "base_decision=base_decision, decision=a198_replaced," in method
    assert "enabled=self._a198_enabled()," in method
    assert method.index('setattr(module, "choose_position_exit", original)') < method.index(
        "self._a198_note_restore(book_id_outer, captured)")


def test_the_module_is_limited_to_absolute_and_names_both_arms():
    assert '    if _token(base_decision, "risk_band") != BAND_ABSOLUTE:' in MODULE
    assert "    if reason == A198_RECOVERY_REASON:" in MODULE
    assert ('    if reason == A198_VETO_REASON and _token(decision, "corridor_action") == '
            "A198_RELATIVE_CORRIDOR:") in MODULE


def test_stats_version_and_launcher():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v5_0_1"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v5_0_1"' in SIMPLE
    assert A198_ABSOLUTE_AUTHORITY_VERSION.endswith("a1_9_8")
    for key in ("direct_a198_version", "direct_a198_absolute_taker_authority", "direct_a198_restores",
                "direct_a198_restored_recovery", "direct_a198_restored_relative",
                "direct_a198_restored_postfill"):
        assert f'stats["{key}"]' in SIMPLE
    assert ("strategy1_direct_v4_16_2_a1_9_8) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; "
            "A1961_BUILD=1; A197_BUILD=1; A198_BUILD=1") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    assert "research_a198_absolute_taker_authority=1" in params
    assert "research_a197_postfill_protect=1" in params
    assert "tests/test_research_a1_9_8_absolute_authority.py" in LAUNCHER
    assert "[preflight] A1.9.8 ABSOLUTE taker authority PASS" in LAUNCHER
