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
    assert len(keys) == 108, len(keys)
    out = _run_guard(" ".join(f"{k}=x" for k in keys))
    assert out.returncode == 0, out.stderr
    assert "(108 keys)" in out.stdout
