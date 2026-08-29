# SPDX-License-Identifier: MIT
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY_DIR))

from research_scheduler_retry import SchedulerRetryGuard

SRC = (STRATEGY_DIR / "Strategy1_Research.py").read_text(encoding="utf-8")
RUNNER = (ROOT / "run_strategy1_research_test_multi.sh").read_text(encoding="utf-8")


def test_v4144_strategy_wires_single_exit_authority_and_retry_before_lane_allocation():
    assert 'RESEARCH_POLICY_VERSION = "realnet_authority_rotation_v4_14_4"' in SRC
    assert 'RESEARCH_ENGINE_REVISION = "lean_engine_p1_realnet_fix_v4_14_4"' in SRC
    assert "arbitrate_realnet_exit(" in SRC
    assert "REALNET_EXIT_AUTHORITY" in SRC
    assert "_research_scheduler_retry_allows_v4144(" in SRC
    assert "entry_feasible=bool(entry_feasible) and (" in SRC
    assert SRC.index("# Re-apply the V4.14.4 arbiter") < SRC.index("self._research_unified_exit_last[int(book_id)] = unified")


def test_v4144_scheduler_retry_is_cleared_on_session_transition():
    assert 'retry_guard = getattr(self, "_research_scheduler_retry_guard", None)' in SRC
    assert 'retry_reset = getattr(retry_guard, "reset", None)' in SRC
    g = SchedulerRetryGuard()
    g.record_reject(5, tick=100, reason="TOXIC", fingerprint=("TOXIC",))
    assert g.snapshot()["scheduler_retry_active"] == 1
    g.reset()
    assert g.snapshot()["scheduler_retry_active"] == 0
    assert g.snapshot()["scheduler_retry_rejects"] == 0


def test_v4144_launcher_fails_closed_on_new_helper_versions():
    assert 'research_realnet_exit_authority.py missing' in RUNNER
    assert 'research_scheduler_retry.py missing' in RUNNER
    assert 'REALNET_EXIT_AUTHORITY_VERSION != "realnet_exit_authority_v4_14_4"' in RUNNER
    assert 'SCHEDULER_RETRY_VERSION != "scheduler_retry_rotation_v4_14_4"' in RUNNER
    assert 'V4.14.4 RealNet exit authority + scheduler retry rotation API OK' in RUNNER
