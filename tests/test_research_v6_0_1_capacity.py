"""v6.0.1: the dust recovery reserve is held only for dust the normalizer can work (C1).

Measured on UID 67 (v6.0.0, testnet, ticks 0 to 3,500, 2026-09-17): five parked inherited lots, about
one lot each, counted as dust, so the reserve (one active slot, one open book, one clip of BASE) was
held in all 142 admission rows.  The normalizer never works a position of half a lot or more.  Zero
entry slots in 71% of rows; the same rows without the reserve give 3%.
"""
import ast
import subprocess
import textwrap
import typing
from pathlib import Path

import research_v601_capacity as cap
from research_direct_legacy_baseline import admission_decomposition
from research_direct_liveness import admission_slots, dust_recovery_reserve_abs, normalization_allowed
from _harness import extractor

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()

MIN = 0.25
EPS = 0.00005


_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


V601_METHODS = ["_v601_on", "_v601_count", "_v601_is_workable", "_v601_reserve_dust", "_v601_telemetry"]


def _agent(on=True, parked=()):
    body = "".join(textwrap.indent(textwrap.dedent(_method_source(n)), "    ") + "\n" for n in V601_METHODS)
    scope = {
        "Any": typing.Any,
        "V601_CAPACITY_VERSION": cap.V601_CAPACITY_VERSION,
        "V601_STATE_EVERY_TICKS": cap.V601_STATE_EVERY_TICKS,
        "v601_is_workable_dust": cap.is_workable_dust,
        "v601_reserve_dust_count": cap.reserve_dust_count,
    }
    exec("from __future__ import annotations\nclass Harness:\n" + body, scope)
    agent = scope["Harness"]()
    agent.research_v601_workable_dust_reserve = on
    agent._v600_inherited_parked = {int(b): 0.252 for b in parked}
    agent._v601_counts = {}
    agent._v601_last = {}
    agent._v601_errors = 0
    agent._tick = 0
    agent.rows = []
    agent._emit = lambda kind, **kw: agent.rows.append(dict(kw, type=kind))
    return agent


# ---- T1 the workable set is exactly the normalizer's set ------------------------------------------

def test_t1_workable_dust_matches_normalization_allowed_at_every_size():
    for units in range(1, 2500):
        q = units / 10_000.0
        for sign in (1.0, -1.0):
            normalizer = normalization_allowed(
                net=sign * q, total_effective_abs=0.0, active_books=0, effective_open_books=0,
                max_abs=2.0, max_active=6, max_open=8, min_order=MIN, eps=EPS,
            )
            dust = q > EPS and q + 1e-12 < MIN
            assert cap.is_workable_dust(sign * q, is_dust=dust, parked=False, min_order=MIN) == normalizer, q


def test_t1_parked_lots_and_non_dust_are_never_workable():
    assert cap.is_workable_dust(0.05, is_dust=True, parked=False, min_order=MIN)
    assert not cap.is_workable_dust(0.05, is_dust=True, parked=True, min_order=MIN)
    assert not cap.is_workable_dust(0.05, is_dust=False, parked=False, min_order=MIN)
    assert cap.is_workable_dust(0.1249, is_dust=True, parked=False, min_order=MIN)
    assert not cap.is_workable_dust(0.125, is_dust=True, parked=False, min_order=MIN)
    assert not cap.is_workable_dust(0.252, is_dust=True, parked=True, min_order=MIN)
    assert not cap.is_workable_dust(0.05, is_dust=True, parked=False, min_order=0.0)


def test_t1_reserve_count_switch_and_bounds():
    assert cap.reserve_dust_count(enabled=False, dust_count=5, workable_count=0) == 5
    assert cap.reserve_dust_count(enabled=True, dust_count=5, workable_count=0) == 0
    assert cap.reserve_dust_count(enabled=True, dust_count=6, workable_count=1) == 1
    assert cap.reserve_dust_count(enabled=True, dust_count=1, workable_count=4) == 1
    assert cap.reserve_dust_count(enabled=True, dust_count=3, workable_count="bad") == 3
    assert cap.reserve_dust_count(enabled=True, dust_count=-2, workable_count=0) == 0


# ---- T2 the strategy methods ----------------------------------------------------------------------

def test_t2_parked_only_dust_releases_the_reserve_and_counts_it():
    agent = _agent(parked=(14, 26, 70, 101, 127))
    for book in (14, 26, 70, 101, 127):
        assert not agent._v601_is_workable(book, 0.252, min_order=MIN)
    diag = {"dust_nonflat_inventory": 5, "v601_workable_dust_inventory": 0}
    assert agent._v601_reserve_dust(diag, 5) == 0
    assert agent._v601_last == {"v601_dust_books": 5, "v601_workable_dust_books": 0, "v601_reserve_dust_books": 0}
    assert agent._v601_counts == {"samples": 1, "dust_present": 1, "reserve_released": 1}


def test_t2_workable_dust_still_holds_the_reserve():
    agent = _agent(parked=(14,))
    assert agent._v601_is_workable(58, 0.0996, min_order=MIN)
    assert agent._v601_reserve_dust({"v601_workable_dust_inventory": 1}, 6) == 1
    assert agent._v601_counts["reserve_held"] == 1


def test_t2_switch_off_and_missing_count_behave_as_v600():
    off = _agent(on=False, parked=(14,))
    assert off._v601_reserve_dust({"v601_workable_dust_inventory": 0}, 5) == 5
    on = _agent()
    assert on._v601_reserve_dust({}, 3) == 3, "no screen count: hold the reserve as v6.0.0 did"
    assert on._v601_reserve_dust({}, 0) == 0


def test_t2_state_row_is_emitted_at_start_and_every_100_ticks():
    agent = _agent(parked=(14, 26))
    agent._v601_reserve_dust({"v601_workable_dust_inventory": 0}, 2)
    for tick in (1, 2, 99, 100, 150, 200):
        agent._tick = tick
        agent._v601_telemetry(None)
    rows = [r for r in agent.rows if r["type"] == "V601_RESERVE_STATE"]
    assert [r["tick"] for r in rows] == [1, 100, 200]
    row = rows[0]
    assert row["v601_capacity_version"] == cap.V601_CAPACITY_VERSION
    assert (row["enabled"], row["dust_books"], row["workable_dust_books"], row["reserve_dust_books"],
            row["inherited_parked"], row["errors"]) == (1, 2, 0, 0, 2, 0)


# ---- T3 the admission gate on the measured v6.0.0 rows --------------------------------------------

def _slots(active, eff_abs, dust_count):
    return admission_slots(
        effective_abs=eff_abs, active_books=active, effective_open_books=active, dust_count=dust_count,
        max_abs=2.0, max_active=6, max_open=8, min_order=MIN,
    )


def test_t3_five_active_books_with_only_parked_dust_admit_one_entry():
    # The typical zero-slot row: 5 active books, 1.246 BASE, 5 parked lots.
    assert _slots(5, 1.246, dust_count=5) == 0, "v6.0.0"
    reserve = cap.reserve_dust_count(enabled=True, dust_count=5, workable_count=0)
    assert _slots(5, 1.246, dust_count=reserve) == 1
    assert dust_recovery_reserve_abs(dust_count=reserve, min_order=MIN) == 0.0


def test_t3_caps_are_unchanged_by_the_release():
    for active in range(0, 8):
        for eff in (0.0, 0.5, 1.25, 1.5, 1.75, 2.0):
            s = _slots(active, eff, dust_count=0)
            assert active + s <= max(6, active)
            assert eff + s * MIN <= 2.0 + 1e-9


def test_t3_decomposition_matches_the_gate_with_the_reserve_count():
    for dust in (0, 1, 5):
        for active in (0, 3, 5, 6):
            d = admission_decomposition(
                raw_total_abs=1.246 + 1.26, ledger_abs=0.0, dust_abs=1.26, dust_exempt_abs=1.26,
                inherited_exempt_abs=0.0, reserved_abs=0.0, active_books=active, effective_open_books=active,
                dust_count=dust, max_abs=2.0, max_active=6, max_open=8, min_order=MIN,
            )
            assert d.portfolio_slots == _slots(active, 1.246, dust)


# ---- T4 wiring ------------------------------------------------------------------------------------

def test_t4_every_reserve_site_uses_the_reserve_count_and_nothing_else_moved():
    build = _method_source("build_mm_strategy_instructions")
    assert build.count("dust_count=reserve_dust_now,") == 4
    assert "dust_count=dust_now" not in build
    assert "reserve_dust_now = int(self._v601_reserve_dust(diag, dust_now))" in build
    # Open and active counting still use the plain dust count's screen.
    assert 'dust_now = int(diag.get("dust_nonflat_inventory", 0) or 0)' in build
    screen = _method_source("_research_fast_screen")
    assert '"v601_workable_dust_inventory": int(workable_dust),' in screen
    assert "if self._v601_is_workable(bid, qty, min_order=min_size):" in screen
    emit = _method_source("_a196_emit_admission")
    assert 'payload.update(getattr(self, "_v601_last", None) or {})' in emit
    normalizer = _method_source("_direct_normalize_irreducible_dust")
    assert "v601" not in normalizer


def test_t4_the_score_row_carries_the_live_blend():
    assert cap.LIVE_BLEND_WEIGHTS == {"kappa": 0.5925, "pnl": 0.1575, "debeta": 0.25}
    # UID 125 at 2026-09-17: kappa 0.531, pnl 0.001, de-beta 0.06, validator total 0.33.
    ex = cap.live_trading_ex_debeta(0.531, 0.001)
    assert abs(ex - 0.3148) < 1e-3
    assert abs(ex + 0.25 * 0.06 - 0.33) < 1e-2
    assert cap.live_trading_ex_debeta(None, 0.1) is None
    assert cap.live_trading_ex_debeta(5.0, 5.0) == 1.0
    assert 'row["trading_score_live_ex_debeta"]' in SIMPLE and 'row["live_blend"]' in SIMPLE


def test_t4_frozen_files_are_untouched():
    for rel in ("agents/strategy/Strategy1_Research.py", "agents/strategy/research_direct_liveness.py",
                "agents/strategy/research_direct_legacy_baseline.py",
                "agents/strategy/research_v600_short_lots.py",
                "agents/strategy/research_v5_score_mirror.py"):
        try:
            out = subprocess.run(["git", "diff", "--quiet", "HEAD", "--", rel], cwd=ROOT,
                                 capture_output=True, timeout=30)
        except Exception:
            return
        if out.returncode not in (0, 1):
            return  # git unavailable for this user
        assert out.returncode == 0, rel


# ---- T5 launcher ----------------------------------------------------------------------------------

def test_t5_arm_params_and_guards():
    assert ("  strategy1_direct_v6_0_1) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; "
            "A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; "
            "V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1 ;;") in LAUNCHER
    assert "  strategy1_direct_v6_0_0) A19X_BUILD=1" in LAUNCHER, "earlier arms stay"
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_2"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_2"' in SIMPLE
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    assert "research_v601_workable_dust_reserve=1" in params
    assert "research_max_active_open_books=${MAX_ACTIVE_BOOKS}" in params and "research_max_total_abs_base=2.0" in params
    assert 'getattr(self.config, "research_v601_workable_dust_reserve"' in SIMPLE
    assert "[preflight] v6.0.1 workable-dust reserve PASS" in LAUNCHER
    assert "tests/test_research_v6_0_1_capacity.py" in LAUNCHER


def _launcher_args(*args, env=None):
    """Run the launcher up to its first file check, with the network guard and argument parsing."""
    head = LAUNCHER[:LAUNCHER.index('[[ -f "$SCRIPT_DIR/run_miner_multi.sh" ]]')]
    script = head + 'echo "anchor=$HISTORY_ANCHOR src=$HISTORY_ANCHOR_SOURCE netuid=$NETUID endpoint=$ENDPOINT"\n'
    base_env = {"PATH": "/usr/bin:/bin"}
    base_env.update(env or {})
    return subprocess.run(["bash", "-c", script, "launcher", *args], capture_output=True, text=True,
                          env=base_env, cwd=ROOT, timeout=30)


def test_t5_network_guard():
    bad = _launcher_args("-u", "79")
    assert bad.returncode == 2 and "netuid 79 is mainnet but the endpoint is testnet" in bad.stderr
    ok = _launcher_args("-u", "79", "-e", "wss://entrypoint-finney.opentensor.ai:443")
    assert ok.returncode == 0 and "netuid=79" in ok.stdout
    wrong = _launcher_args("-u", "366", "-e", "wss://entrypoint-finney.opentensor.ai:443")
    assert wrong.returncode == 2 and "is not mainnet SN79" in wrong.stderr
    assert _launcher_args("-u", "366").returncode == 0
    assert _launcher_args("-u", "79", "-e", "ws://127.0.0.1:9944").returncode == 0


def test_t5_history_anchor_flag():
    assert "anchor=auto src=default" in _launcher_args().stdout
    assert "anchor=established src=flag" in _launcher_args("--history_anchor", "established").stdout
    assert "anchor=established src=env" in _launcher_args(env={"HISTORY_ANCHOR": "established"}).stdout
    assert "anchor=auto src=flag" in _launcher_args(
        "--history_anchor=auto", env={"HISTORY_ANCHOR": "established"}).stdout
    assert _launcher_args("--history_anchor").returncode == 2
