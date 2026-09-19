"""v6.0.2: the active-book cap goes from 6 to 8 (MAX_ACTIVE_BOOKS, one launcher setting).

Measured on UID 68 (v6.0.1, testnet, ticks 0 to 2,622, 2026-09-17): the workable-dust reserve was
held in 10% of admission rows instead of 100%, and every one of the 71 zero-slot rows was bound by
ACTIVE; 66 of 106 rows held all 6 books.  The frozen Research clamp allows 8, the total-open cap is
already 8, and 8 x 0.25 BASE is exactly the 2.0 BASE cap, so no other limit moves.
"""
import subprocess
import textwrap
from pathlib import Path
from types import SimpleNamespace

from research_direct_legacy_baseline import admission_decomposition
from research_direct_liveness import admission_slots

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
MIN = 0.25


def _frozen_caps(**cfg):
    """Run the frozen Research cap clamps on a config, exactly as written."""
    start = RESEARCH.index("        self.research_max_active_open_books = max(\n")
    end = RESEARCH.index("        self.research_inventory_liveness_enabled", start)
    agent = SimpleNamespace(mm_base_size=0.25)
    exec(textwrap.dedent(RESEARCH[start:end]), {"self": agent, "cfg": SimpleNamespace(**cfg)})
    return (agent.research_max_active_open_books, agent.research_max_total_open_books,
            agent.research_max_total_abs_base, agent.research_max_open_books)


def _slots(active, eff_abs, dust=0, cap=8):
    return admission_slots(
        effective_abs=eff_abs, active_books=active, effective_open_books=active, dust_count=dust,
        max_abs=2.0, max_active=cap, max_open=8, min_order=MIN,
    )


# ---- T1 the frozen clamps ---------------------------------------------------------------------------

def test_t1_frozen_clamp_accepts_8_and_moves_no_other_cap():
    base = dict(research_max_open_books=0, research_max_total_open_books=8, research_max_total_abs_base=2.0)
    for want in (6, 7, 8):
        active, total, abs_cap, alias = _frozen_caps(research_max_active_open_books=want, **base)
        assert (active, total, abs_cap, alias) == (want, 8, 2.0, want)
    # Anything above 8 is clamped, so the launcher's 6-8 range is the whole usable range.
    assert _frozen_caps(research_max_active_open_books=9, **base)[0] == 8


# ---- T2 admission with 8 books ----------------------------------------------------------------------

def test_t2_the_six_book_row_now_admits_entries():
    # The typical v6.0.1 zero-slot row: 6 active books, 1.50 BASE, no workable dust.
    assert _slots(6, 1.5, cap=6) == 0
    assert _slots(6, 1.5, cap=8) == 2
    assert _slots(6, 1.5, dust=1, cap=8) == 1, "the workable-dust reserve still takes one"
    assert _slots(7, 1.75, cap=8) == 1
    assert _slots(8, 2.0, cap=8) == 0


def test_t2_active_and_base_caps_are_never_exceeded():
    for cap in (6, 7, 8):
        for active in range(0, 9):
            for units in range(0, 9):
                eff = units * MIN
                for dust in (0, 1):
                    s = _slots(active, eff, dust=dust, cap=cap)
                    if s:
                        assert active + s <= cap
                        assert eff + s * MIN <= 2.0 + 1e-9
                        assert active + s <= 8


def test_t2_base_cap_binds_before_the_book_cap_when_positions_run_large():
    d = admission_decomposition(
        raw_total_abs=1.9, ledger_abs=0.0, dust_abs=0.0, dust_exempt_abs=0.0, inherited_exempt_abs=0.0,
        reserved_abs=0.0, active_books=6, effective_open_books=6, dust_count=0,
        max_abs=2.0, max_active=8, max_open=8, min_order=MIN,
    )
    assert (d.portfolio_slots, d.binding) == (0, "ABS")


# ---- T3 wiring --------------------------------------------------------------------------------------

def test_t3_rows_report_the_caps():
    assert 'cap_max_active=int(terms.get("max_active", 0) or 0),' in SIMPLE
    assert 'cap_max_open=int(terms.get("max_open", 0) or 0),' in SIMPLE
    assert 'cap_max_abs=float(terms.get("max_abs", 0.0) or 0.0),' in SIMPLE
    assert 'max_active_books=int(getattr(self, "research_max_active_open_books", 0) or 0),' in SIMPLE
    # Admission and the live validator read the same attribute.
    assert SIMPLE.count('max_active = int(getattr(self, "research_max_active_open_books", 6) or 6)') >= 3


def test_t3_versions_and_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_2"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_2"' in SIMPLE
    assert ("  strategy1_direct_v6_0_2) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; "
            "A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; "
            "V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1 ;;") in LAUNCHER
    assert "  strategy1_direct_v6_0_1) A19X_BUILD=1" in LAUNCHER, "earlier arms stay"
    assert 'MAX_ACTIVE_BOOKS="${MAX_ACTIVE_BOOKS:-8}"' in LAUNCHER
    assert "research_max_open_books=${MAX_ACTIVE_BOOKS} research_max_active_open_books=${MAX_ACTIVE_BOOKS} " \
           "research_max_total_open_books=8 research_max_total_abs_base=2.0" in LAUNCHER
    assert "[preflight] v6.0.2 active-book cap PASS" in LAUNCHER
    assert "tests/test_research_v6_0_2_active_cap.py" in LAUNCHER


# ---- T4 launcher behaviour --------------------------------------------------------------------------

def _run(script, env):
    base = {"PATH": "/usr/bin:/bin"}
    base.update(env)
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=base, cwd=ROOT, timeout=30)


def _params_script():
    start = LAUNCHER.index('# v6.0.2: only a v6.0.2 build may change the active-book cap')
    end = LAUNCHER.index('"\n', LAUNCHER.index('PARAMS="', start) + len('PARAMS="')) + 2
    return LAUNCHER[start:end] + 'echo "$PARAMS"\n'


def test_t4_earlier_builds_keep_six_books():
    out = _run(_params_script(), {"V602_BUILD": "0", "MAX_ACTIVE_BOOKS": "8"})
    assert "research_max_active_open_books=6 " in out.stdout and "research_max_open_books=6 " in out.stdout
    out = _run(_params_script(), {"V602_BUILD": "1", "MAX_ACTIVE_BOOKS": "8"})
    assert "research_max_active_open_books=8 " in out.stdout and "research_max_open_books=8 " in out.stdout


def _preflight(value):
    start = LAUNCHER.index('if [[ "$V602_BUILD" == "1" ]]; then')
    end = LAUNCHER.index('\nfi\n', start) + 4
    script = _params_script() + LAUNCHER[start:end]
    return _run(script, {"V602_BUILD": "1", "MAX_ACTIVE_BOOKS": value, "AGENT_PATH": str(STRATEGY)})


def test_t4_preflight_accepts_6_to_8_only():
    for good in ("6", "7", "8"):
        out = _preflight(good)
        assert out.returncode == 0 and f"active-book cap PASS (max_active_books={good})" in out.stdout, out.stderr
    for bad in ("5", "9", "10", "eight", ""):
        out = _preflight(bad)
        assert out.returncode == 1 and "is not 6, 7 or 8" in out.stderr, (bad, out.stderr)


def test_t4_flag_overrides_the_environment():
    head = LAUNCHER[:LAUNCHER.index('[[ -f "$SCRIPT_DIR/run_miner_multi.sh" ]]')]
    script = head + 'echo "max=$MAX_ACTIVE_BOOKS"\n'

    def run(*args, env=None):
        base = {"PATH": "/usr/bin:/bin"}
        base.update(env or {})
        return subprocess.run(["bash", "-c", script, "launcher", *args], capture_output=True, text=True,
                              env=base, cwd=ROOT, timeout=30)

    assert "max=8" in run().stdout
    assert "max=6" in run(env={"MAX_ACTIVE_BOOKS": "6"}).stdout
    assert "max=7" in run("--max_active_books", "7", env={"MAX_ACTIVE_BOOKS": "6"}).stdout
    assert "max=6" in run("--max_active_books=6").stdout
    assert run("--max_active_books").returncode == 2
