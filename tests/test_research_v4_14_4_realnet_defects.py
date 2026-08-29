# SPDX-License-Identifier: MIT
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))

from research_realnet_exit_authority import (
    ACTION_DEFER,
    ACTION_KEEP_MAKER,
    ACTION_PARK,
    ACTION_TAKER_ESCAPE,
    STAGE_HARD_ESCAPE,
    STAGE_SOFT_ESCAPE,
    STAGE_SOFT_HOLD,
    arbitrate_realnet_exit,
)
from research_scheduler_retry import SchedulerRetryGuard, score_ev_fingerprint


def test_soft_corridor_keeps_profitable_young_maker_even_if_old_liveness_authorized():
    d = arbitrate_realnet_exit(
        taker_net_bps=-12.0,
        maker_net_bps=2.5,
        maker_executable=True,
        failed_exit_count=1,
        inventory_age=3,
        adverse_evidence=True,
        liveness_authorized=True,
        liveness_floor_bps=-12.0,
    )
    assert d.action == ACTION_KEEP_MAKER
    assert d.stage == STAGE_SOFT_HOLD
    assert d.legacy_conflict_resolved


def test_hard_corridor_overrides_legacy_minus12_park_and_has_no_age_veto():
    d = arbitrate_realnet_exit(
        taker_net_bps=-20.0,
        maker_net_bps=4.0,
        maker_executable=True,
        failed_exit_count=0,
        inventory_age=0,
        liveness_authorized=False,
        liveness_park=True,
        liveness_floor_bps=-12.0,
    )
    assert d.action == ACTION_TAKER_ESCAPE
    assert d.stage == STAGE_HARD_ESCAPE
    assert d.authorized
    assert d.legacy_conflict_resolved


def test_below_minus25_never_gets_normal_taker_authority():
    d = arbitrate_realnet_exit(
        taker_net_bps=-25.01,
        maker_net_bps=-5.0,
        maker_executable=True,
        failed_exit_count=100,
        inventory_age=100,
        adverse_evidence=True,
        liveness_authorized=True,
        liveness_floor_bps=-12.0,
    )
    assert d.action == ACTION_PARK
    assert not d.authorized


def test_soft_escape_after_veto_bound_releases_inventory():
    d = arbitrate_realnet_exit(
        taker_net_bps=-14.0,
        maker_net_bps=2.0,
        maker_executable=True,
        failed_exit_count=4,
        inventory_age=8,
        adverse_evidence=True,
        wait_ev_bps=-20.0,
    )
    assert d.action == ACTION_TAKER_ESCAPE
    assert d.stage == STAGE_SOFT_ESCAPE


def test_above_soft_floor_defers_to_existing_unified_exit():
    d = arbitrate_realnet_exit(
        taker_net_bps=-5.0,
        maker_net_bps=0.5,
        maker_executable=True,
        failed_exit_count=20,
        inventory_age=50,
        adverse_evidence=True,
        liveness_authorized=True,
        liveness_floor_bps=-12.0,
    )
    assert d.action == ACTION_DEFER


class _EV:
    def __init__(self, reason, trading_ev, maker_ev=0.0, toxic=False, eligible=False):
        self.reject_reason = reason
        self.trading_ev = trading_ev
        self.maker_ev = maker_ev
        self.toxic = toxic
        self.eligible = eligible


def test_negative_ev_candidate_is_quarantined_and_does_not_retry_next_tick():
    g = SchedulerRetryGuard(negative_ev_base_ticks=8, toxic_base_ticks=16, max_cooldown_ticks=64)
    ev = _EV("NEGATIVE_EV", -3.0)
    fp = score_ev_fingerprint(ev)
    first = g.record_reject(17, tick=100, reason="NEGATIVE_EV", fingerprint=fp)
    assert first.blocked and first.blocked_until_tick == 108
    assert g.should_skip(17, tick=101, fingerprint=fp).blocked


def test_toxic_candidate_gets_longer_initial_quarantine():
    g = SchedulerRetryGuard(negative_ev_base_ticks=8, toxic_base_ticks=16, max_cooldown_ticks=64)
    ev = _EV("TOXIC_BOOK", -1.0, toxic=True)
    d = g.record_reject(9, tick=50, reason="TOXIC_BOOK", fingerprint=score_ev_fingerprint(ev))
    assert d.blocked_until_tick == 66


def test_repeated_same_hard_failure_backs_off_but_is_bounded():
    g = SchedulerRetryGuard(negative_ev_base_ticks=8, toxic_base_ticks=16, max_cooldown_ticks=32)
    fp = score_ev_fingerprint(_EV("NEGATIVE_EV", -3.0))
    d1 = g.record_reject(1, tick=0, reason="NEGATIVE_EV", fingerprint=fp)
    d2 = g.record_reject(1, tick=d1.blocked_until_tick, reason="NEGATIVE_EV", fingerprint=fp)
    d3 = g.record_reject(1, tick=d2.blocked_until_tick, reason="NEGATIVE_EV", fingerprint=fp)
    assert d1.remaining_ticks == 8
    assert d2.remaining_ticks == 16
    assert d3.remaining_ticks == 32


def test_material_ev_change_clears_quarantine_early():
    g = SchedulerRetryGuard()
    old = score_ev_fingerprint(_EV("NEGATIVE_EV", -5.0))
    new = score_ev_fingerprint(_EV("", 2.0, eligible=True))
    g.record_reject(7, tick=10, reason="NEGATIVE_EV", fingerprint=old)
    d = g.should_skip(7, tick=11, fingerprint=new)
    assert not d.blocked
    assert d.fingerprint_changed


def test_success_clears_quarantine():
    g = SchedulerRetryGuard()
    g.record_reject(3, tick=10, reason="TOXIC", fingerprint=("x",))
    g.clear(3)
    assert not g.should_skip(3, tick=11, fingerprint=("x",)).blocked
