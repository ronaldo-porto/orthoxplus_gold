"""The preflight gate must actually run, and its guards must actually fire.

Measured 2026-09-18, before this build: the launcher's 53-file test gate lived inside
``RESEARCH_PREFLIGHT_ONLY=1`` -- a branch the real launch path never takes -- and invoked
``python -m pytest``, which this host cannot import, so ``set -euo pipefail`` aborted the gate
before its PASS line.  No test had gated a launch since the v4.16 series.  Separately, 59 of the
100 PARAMS keys had no guard at all, so a misspelled key was silent: the agent took its source
default and the launcher still reported the build.

These tests exist so that neither can rot back: the gate must call the in-repo runner, and the
key guard must reject a typo.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
LAUNCHER_PATH = ROOT / "run_strategy1_research_simple_multi.sh"
LAUNCHER = LAUNCHER_PATH.read_text(encoding="utf-8")
RUNNER = ROOT / "tests" / "run_tests.py"
STUB = ROOT / "tests" / "_pytest_stub.py"


# ---- the runner is in the repository ------------------------------------------------------------

def test_the_runner_and_its_stub_are_carried_in_the_repo():
    assert RUNNER.is_file(), "the only working runner must not live in a scratchpad again"
    assert STUB.is_file()
    # Neither may be collected as a test module by pytest or by the runner itself.
    assert not RUNNER.name.startswith("test_") and not STUB.name.startswith("test_")


def test_the_runner_reports_its_own_collection():
    out = subprocess.run(
        [sys.executable, str(RUNNER), "tests/test_version_pins.py", "--list"],
        capture_output=True, text=True, cwd=ROOT, timeout=120,
    )
    assert out.returncode == 0, out.stderr
    assert "tests/test_version_pins.py::test_the_pin_matches_the_file_on_disk" in out.stdout


def test_the_runner_honours_skip_markers():
    """The scratchpad runner ran @pytest.mark.skip tests anyway and counted them as failures."""
    out = subprocess.run(
        [sys.executable, str(RUNNER), "tests/test_research_strategy1_direct_a1_5.py"],
        capture_output=True, text=True, cwd=ROOT, timeout=300,
    )
    assert out.returncode == 0, out.stderr
    assert "SKIP tests/test_research_strategy1_direct_a1_5.py" in out.stdout
    assert "0 failed" in out.stdout


# ---- the launcher gate --------------------------------------------------------------------------

def test_the_gate_calls_the_repo_runner_not_bare_pytest():
    assert 'python "$SCRIPT_DIR/tests/run_tests.py" \\' in LAUNCHER
    # Comments may discuss the old invocation; no command line may still make it.
    live = [l for l in LAUNCHER.splitlines() if not l.lstrip().startswith("#")]
    offenders = [l.strip() for l in live if "python -m pytest" in l]
    assert not offenders, f"bare pytest aborts the gate on a host without it: {offenders}"


def test_the_gate_still_names_every_file_it_named_before():
    # The 53-file list is the gate's contract; this build only changed how it is run.
    listed = [l.strip().rstrip(" \\") for l in LAUNCHER.splitlines() if l.strip().startswith("tests/test_")]
    assert len(listed) >= 53
    for name in ("tests/test_research_v6_0_3_short_lot_release.py",
                 "tests/test_research_v6_0_2_active_cap.py",
                 "tests/test_version_pins.py",
                 "tests/test_preflight_gate.py"):
        assert name in listed, name
    for name in listed:
        assert (ROOT / name).is_file(), f"{name} is in the gate list but not on disk"


# ---- the PARAMS key guard -------------------------------------------------------------------------

def _guard_script():
    """The guard block on its own, with AGENT_PATH/SCRIPT_DIR/PARAMS taken as arguments."""
    start = LAUNCHER.index("# Every PARAMS key must be read by name")
    end = LAUNCHER.index('echo "[preflight] PARAMS keys resolve to agent code PASS', start)
    end = LAUNCHER.index("\n", end) + 1
    return ('set -euo pipefail\nAGENT_PATH="$1"\nSCRIPT_DIR="$2"\nPARAMS="$3"\n'
            + LAUNCHER[start:end])


def _run_guard(params):
    return subprocess.run(
        ["bash", "-c", _guard_script(), "guard", "agents/strategy", ".", params],
        capture_output=True, text=True, cwd=ROOT, timeout=300,
    )


def test_the_key_guard_accepts_real_keys():
    out = _run_guard("research_v603_short_lot_release=1 mm_base_size=0.25 research_max_total_abs_base=2.0")
    assert out.returncode == 0, out.stderr
    assert "PARAMS keys resolve to agent code PASS (3 keys)" in out.stdout


def test_the_key_guard_rejects_a_typo():
    for typo in ("research_v603_short_lot_releases=1",       # plural
                 "research_a195_taker_floor_bp=-25.0",        # the sharpest: unbounded taker exit
                 "research_v601_workable_dust_reserv=1"):     # truncated
        out = _run_guard(typo)
        assert out.returncode == 1, (typo, out.stdout)
        assert "read by no agent code" in out.stderr, typo


def test_every_key_the_launcher_ships_today_resolves():
    """The guard is only worth having if it passes on the real PARAMS."""
    start = LAUNCHER.index('PARAMS="')
    end = LAUNCHER.index('"\n', LAUNCHER.index("research_v603_short_lot_release=1", start))
    params = LAUNCHER[start + len('PARAMS="'):end].replace("\\\n", " ")
    keys = [tok.split("=", 1)[0] for tok in params.split() if "=" in tok]
    # 172 at c85f631 (v6.9); +1 research_v691_free_base (v6.9.1 C2), +1 research_v610_behind_max_clips (v6.10 S2),
    # +1 research_v612_reduce_depth (v6.12 S1), +1 research_v614_book_floor_off (v6.14 S1), +1 research_v615_launch (v6.15 S1),
    # +1 research_v616_fair_value (v6.16 S1), +1 research_v617_touch_quote (v6.17 S1), +1 research_v618_centre_off (v6.18 S1),
    # +1 research_v619_surplus_hold (v6.19 S1), +1 research_v620_room_pace (v6.20 S1), +3 research_v621_rolloff_guard,
    # research_v621_pace_persist, research_v621_even_spend (v6.21 S1-S3), +3 research_v622_cap_window_pace,
    # research_v622_unknown_aged, research_v622_cap_skip (v6.22 S1-S3), +6 research_v623_realized_mode,
    # research_v623_against_levels, research_v623_against_gap_bps, research_v623_against_adding,
    # research_v623_class_guard, research_v623_start_once (v6.23 S1-S3), +1 research_v624_pay_basis (v6.24 S4),
    # +3 research_v625_lean, research_v625_lean_slope, research_v625_lean_cap (v6.25 S5), +1 research_v626_feed_clock
    # (v6.26 S6), +1 research_v627_touch_centre (v6.27 S7), +1 research_v628_flat_launch (v6.28 S8),
    # +1 research_v629_touch_fp_move (v6.29 S9), +2 research_v630_deep_tier, research_v630_s9_lookback (v6.30 D1, D2),
    # +2 research_v631_touch_improve, research_v631_single_write_save (v6.31 S12b, the single-write save),
    # +3 research_v632_fee_making, research_v632_fee_weight, research_v632_state_fundamental (v6.32 S14, P1),
    # +1 research_v633_touch_identity (v6.33 D4), +1 research_v634_class1_entry (v6.34),
    # +1 research_v636_step_withdrawal (v6.36), +1 research_v637_fast_path (v6.37), v6.38: -1
    # research_a193_breadth_admission_enabled (retired), +1 research_enable_fill_hazard (P2L), +1 research_v638_fast_path.
    # +5 v6.38 layer 3 (research_v638_kbl, _lean, _lean_arm, _c1, _c1_arm; all 0).
    # +1 research_v639_runlen (v6.39, k129).
    # +11 v6.40 (k139): research_v640_traded_classes, _c2du, _c2d, _c2j, _c2q, _c2q_pf, _c1w, _guard_hold,
    # _guard_init_price, _s14_class_key, _s14_flush_spec.
    # +5 v6.41 (k144): research_v641_speed, _unit_safety, _c2q_reduce, _c2q_persist, _s9_page.
    # +2 v6.42 (k150): research_v642_l1t, research_v642_l1c.
    assert len(keys) == 238, len(keys)
    out = _run_guard(" ".join(f"{k}=x" for k in keys))
    assert out.returncode == 0, out.stderr
    assert "(238 keys)" in out.stdout                                   # k144 / k150: + v6.41's 5, v6.42's 2
