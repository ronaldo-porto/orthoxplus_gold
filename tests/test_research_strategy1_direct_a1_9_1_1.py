"""A1.9.1.1 -- Phase B activation repair.

The first A1.9.1 run was invalid. It shipped as `strategy1_direct_v4_16_2_a1_9_1`
with `research_profitable_exit_ttl_ms=4000` active and PROFITABLE_EXIT_PERSIST
confirming 4,000 ms TTLs on real exits -- but produced **0** reprice cancels
across 500 ticks while the shadow classifier asked for 756. That is a 4,000 ms
TTL with no stale-cancel path: precisely the split the A1.9 design says must
never run.

Two independent defects:

1. **The post-pass was inert.** `_a191_service_reprice_cancels` iterated only
   `_a191_verdict_store()`, whose sole writer was `_a191_decide` called from
   `_research_place_maker_exit` -- the path measured at 0 of 492 sightings of a
   live resting exit. So the cache was empty on exactly the books needing a
   cancel. The post-pass now seeds verdicts by enumerating open-inventory books
   itself, the way the observer does.

2. **Telemetry lied about the phase.** Three sites hardcoded Phase A
   (`a19_phase="A_SHADOW_MEASUREMENT"`, `phase="A2_LEDGER_SHADOW_MEASUREMENT"`,
   `behaviour_change=0`) and `shadow_mode=1` was emitted unconditionally, so the
   log reported shadow mode while claiming an A1.9.1 engine. Phase and
   behaviour-change are now derived from the runtime enable flag in one place,
   and the launcher refuses to start a build that hardcodes Phase A.
"""

import ast
import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
SIMPLE = STRATEGY_DIR / "Strategy1_Research_Simple.py"
LAUNCHER = ROOT / "run_strategy1_research_simple_multi.sh"

sys.path.insert(0, str(STRATEGY_DIR))

SRC = SIMPLE.read_text()
LAUNCHER_SRC = LAUNCHER.read_text()

from research_direct_exit_ledger import (  # noqa: E402
    LEDGER_REMOVED_FILLED, DirectExitLedger, RestingInventoryView, close_side_for,
)
from research_direct_exit_refresh import (  # noqa: E402
    ABSENT_REPRICE_CANCEL, EXIT_HOLD, EXIT_REPRICE, REASON_QUEUE_PRESERVED,
    REASON_STALE_BEHIND_TOUCH, behind_ticks, classify_resting_maker_exit,
    forgone_edge_bps,
)
from research_unified_exit import completion_net_bps as unified_completion_net_bps  # noqa: E402

MS = 1_000_000
T0 = 1_700_000_000_000_000_000
BOOK = 7


# --------------------------------------------------------------- versioning

def test_version_advances_to_a1_9_1_1():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_4"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_4"' in SRC


# ------------------------------------------- defect 2: telemetry told the truth

def test_no_hardcoded_phase_a_telemetry_survives():
    """The exact strings that made the invalid run look like shadow mode."""
    assert 'a19_phase="A_SHADOW_MEASUREMENT"' not in SRC
    assert 'phase="A2_LEDGER_SHADOW_MEASUREMENT"' not in SRC
    assert "behaviour_change=0," not in SRC
    assert "a19_behaviour_change=0," not in SRC


def test_phase_is_derived_from_runtime_state():
    assert "def _a19_runtime_phase" in SRC
    assert "def _a19_behaviour_change" in SRC
    assert "a19_phase=self._a19_runtime_phase()" in SRC
    assert "phase=self._a19_runtime_phase()" in SRC


def test_shadow_mode_is_not_hardcoded_on_the_behavioural_observer():
    """A19_TICK_OBSERVE feeds the Phase B verdict, so its flag must be derived.

    A19_EXIT_EVAL keeps a literal shadow_mode=1: that is the A1.9.0.1 control
    observer whose decision really is discarded, so the literal is truthful
    there. Exactly one literal may remain.
    """
    block = SRC.split('"A19_TICK_OBSERVE", force=True')[1].split("shadow_mode")[1][:60]
    assert "int(not self._a191_enabled())" in block
    assert SRC.count("shadow_mode=1,") == 1
    assert "shadow_mode=1," in SRC.split('"A19_EXIT_EVAL", force=True')[1][:2000]


def test_config_row_names_the_phase_b_events():
    """The invalid run was grepped for A19_* names that do not exist."""
    assert "DIRECT_A19_PHASE_B_EVENTS" in SRC
    assert "phase_b_events=" in SRC
    for name in ("A19_QUEUE_HOLD", "A19_EXIT_REPRICE_CANCEL", "A19_REPRICE_BUDGET_BLOCK"):
        assert name in SRC, name


# --------------------------------------------------- launcher activation guard

def test_launcher_refuses_a_hardcoded_phase_a_build():
    assert "A1.9.1 behavioural activation guard" in LAUNCHER_SRC
    assert "A_SHADOW_MEASUREMENT" in LAUNCHER_SRC     # the refused pattern


def test_launcher_checks_params_value_not_script_text():
    """Grepping the script matches the guard's own source line and always passes."""
    assert '[[ "$PARAMS" == *"research_profitable_exit_ttl_ms=4000"* ]]' in LAUNCHER_SRC
    assert '[[ "$PARAMS" == *"research_a191_queue_preservation_enabled=1"* ]]' in LAUNCHER_SRC
    assert "grep -q 'research_profitable_exit_ttl_ms=4000' \"$0\"" not in LAUNCHER_SRC


def test_launcher_still_ships_the_atomic_mechanism():
    assert "research_profitable_exit_ttl_ms=4000" in LAUNCHER_SRC
    assert "research_a191_queue_preservation_enabled=1" in LAUNCHER_SRC


# --------------------------------- defect 1: the post-pass must seed itself

WANTED = {
    "_a191_enabled", "_a19_runtime_phase", "_a19_behaviour_change",
    "_a191_verdict_store", "_a191_live_exit_row", "_a191_decide",
    "_a191_service_reprice_cancels", "_a19_is_entry_quote_row",
    "_direct_entry_quote_client_ids", "_a19_resting_net_bps", "_a19_tick_size",
    "_a19_ledger_ref", "_a19_note_exit_cancel", "_direct_account_orders",
    "_a191_check_activation",
}


def _load():
    cls = next(n for n in ast.parse(SRC).body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    assert {m.name for m in methods} == WANTED, WANTED ^ {m.name for m in methods}
    ns = {
        "math": math, "Any": object, "DirectExitLedger": DirectExitLedger,
        "RestingInventoryView": RestingInventoryView, "close_side_for": close_side_for,
        "EXIT_HOLD": EXIT_HOLD, "EXIT_REPRICE": EXIT_REPRICE,
        "REASON_QUEUE_PRESERVED": REASON_QUEUE_PRESERVED,
        "ABSENT_REPRICE_CANCEL": ABSENT_REPRICE_CANCEL,
        "behind_ticks": behind_ticks, "forgone_edge_bps": forgone_edge_bps,
        "classify_resting_maker_exit": classify_resting_maker_exit,
        "unified_completion_net_bps": unified_completion_net_bps,
        "DIRECT_MAKER_EXIT_TARGET_BPS": 2.0,
        "SIMPLE_ENGINE_VERSION": "strategy1_direct_v4_16_2_a1_9_4",
        "DIRECT_EXIT_REFRESH_VERSION": "direct_exit_refresh_v4_16_2_a1_9_2",
        "DIRECT_EXIT_LEDGER_VERSION": "direct_exit_ledger_v4_16_2_a1_9_0_3",
        "DIRECT_A19_PHASE_B_EVENTS": ("A19_QUEUE_HOLD", "A19_EXIT_REPRICE_CANCEL", "A19_REPRICE_BUDGET_BLOCK"),

        "DIRECT_A19_PHASE_BEHAVIOURAL": "B_QUEUE_PRESERVING_EXIT",
        "DIRECT_A19_PHASE_SHADOW": "A_SHADOW_MEASUREMENT",
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 "<a1911>", "exec"), ns)
    return {n: ns[n] for n in WANTED}


class _Level:
    def __init__(self, price): self.price = price


class _Book:
    def __init__(self, bid, ask):
        self.bids = [_Level(bid)]; self.asks = [_Level(ask)]


class _State:
    def __init__(self, books, timestamp):
        self.books = books; self.timestamp = timestamp
        self.config = types.SimpleNamespace(priceDecimals=2, publish_interval=1000 * MS)


class _Response:
    def __init__(self):
        self.cancels = []

    def cancel_orders(self, *, book_id, order_ids, delay=0):
        self.cancels.append((int(book_id), list(order_ids)))


class _Agent:
    research_profitable_exit_ttl_ms = 4000.0
    research_profitable_exit_min_net_bps = 0.0
    research_profitable_exit_reprice_ticks = 3.0
    research_a191_queue_preservation_enabled = True
    _research_market_regime = "NORMAL"
    _research_volume_decimals = 4
    research_score_ev_fees_bps = 1.0
    max_instructions_per_book = 5
    A19_CANCEL_WATCH_MAX_TICKS = 10
    A19_CANCEL_MEMO_MAX = 2048
    A191_MIN_REMAINING_TTL_MS = 1000.0
    A191_ACTIVATION_ALARM_CANDIDATES = 20

    def __init__(self):
        self._tick = 1
        self._a19_ledger = DirectExitLedger()
        self._a19_cancel_watch = {}
        self._a19_cancel_reason = {}
        self._a191_verdicts = {}
        self._a191_verdict_tick = -1
        self.accounts = {}
        self.positions = {}
        self.events = []
        self._book_instructions = 0
        self._a191_activation_banner_emitted = False
        self._a191_activation_alarm_emitted = False
        self._a191_reprice_release = {}
        for n in ("_a191_holds", "_a191_reprice_cancels", "_a191_reprice_deferred_ttl",
                  "_a191_reprice_deferred_budget", "_a191_placements_suppressed",
                  "_a191_cancel_emit_failures", "_a191_postpass_cancels",
                  "_a191_reprice_candidates"):
            setattr(self, n, 0)

    def _position_tracker_snapshot(self, book_id):
        net, vwap = self.positions.get(int(book_id), (0.0, None))
        return types.SimpleNamespace(net_qty=net, vwap_entry=vwap)

    def _research_live_fee_bps(self, book_id, *, is_maker=True, fallback_bps=None):
        return -0.5 if is_maker else 3.0

    def _count_book_instructions(self, response, book_id):
        return self._book_instructions

    def _emit(self, event_type, force=False, **payload):
        self.events.append((event_type, payload))

    def rows(self, name):
        return [p for e, p in self.events if e == name]


_STATIC = {"_a19_tick_size", "_direct_entry_quote_client_ids"}
for _n, _f in _load().items():
    setattr(_Agent, _n, staticmethod(_f) if _n in _STATIC else _f)


def _stale_agent(price=100.60):
    a = _Agent()
    a.positions[BOOK] = (0.5, 100.00)
    a._a19_ledger.note_accepted(
        order_id=555, book_id=BOOK, side=1, price=price, quantity=0.5,
        timestamp_ns=T0, tick=1, action="PASSIVE_MAKER_EXIT",
    )
    return a


def _state():
    return _State({BOOK: _Book(100.10, 100.20)}, T0)


def test_post_pass_cancels_without_the_placement_path_ever_running():
    """THE regression test for the invalid run.

    No `_a191_decide` call precedes this -- exactly the production case where
    the placement path never sees the resting exit. A1.9.1 emitted 0 cancels
    here; A1.9.1.1 must emit one.
    """
    a = _stale_agent()
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 1
    assert r.cancels == [(BOOK, [555])]
    assert a._a19_cancel_reason[555][1] == ABSENT_REPRICE_CANCEL


def test_post_pass_seeds_a_verdict_for_every_open_inventory_book():
    a = _stale_agent()
    assert a._a191_verdicts == {}
    a._a191_service_reprice_cancels(_Response(), _state())
    assert BOOK in a._a191_verdicts
    assert a._a191_verdicts[BOOK]["decision"] == EXIT_REPRICE
    assert a._a191_verdicts[BOOK]["reason"] == REASON_STALE_BEHIND_TOUCH


def test_seeding_does_not_override_a_placement_path_verdict():
    """The placement path knows the real rung; its verdict must win."""
    a = _stale_agent()
    inv = RestingInventoryView.from_tracker(a._position_tracker_snapshot(BOOK))
    first = a._a191_decide(_state(), BOOK, inv, 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    a._a191_service_reprice_cancels(_Response(), _state())
    assert a._a191_verdicts[BOOK] is first


def test_seeded_hold_emits_no_cancel():
    a = _stale_agent(price=100.20)          # at the touch: nothing stale
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 0
    assert r.cancels == []
    assert a._a191_verdicts[BOOK]["decision"] == EXIT_HOLD


def test_seeding_skips_flat_books():
    a = _stale_agent()
    a.positions[BOOK] = (0.0, None)
    assert a._a191_service_reprice_cancels(_Response(), _state()) == 0
    assert a._a191_verdicts == {}


def test_seeding_respects_the_remaining_ttl_gate():
    a = _Agent()
    a.positions[BOOK] = (0.5, 100.00)
    a._a19_ledger.note_accepted(
        order_id=555, book_id=BOOK, side=1, price=100.60, quantity=0.5,
        timestamp_ns=T0 - int(3500 * MS), tick=1, action="PASSIVE_MAKER_EXIT",
    )
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 0
    assert a._a191_reprice_deferred_ttl == 1


def test_seeding_respects_the_instruction_budget():
    a = _stale_agent()
    a._book_instructions = 5
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 0
    assert a._a191_reprice_deferred_budget == 1


def test_disabled_switch_seeds_nothing_and_reports_shadow():
    a = _stale_agent()
    a.research_a191_queue_preservation_enabled = False
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 0
    assert a._a19_runtime_phase() == "A_SHADOW_MEASUREMENT"
    assert a._a19_behaviour_change() == 0


def test_enabled_switch_reports_phase_b():
    a = _stale_agent()
    assert a._a19_runtime_phase() == "B_QUEUE_PRESERVING_EXIT"
    assert a._a19_behaviour_change() == 1


def test_multiple_books_each_get_their_own_verdict():
    a = _stale_agent()
    other = 9
    a.positions[other] = (-0.5, 100.00)
    a._a19_ledger.note_accepted(
        order_id=777, book_id=other, side=0, price=99.40, quantity=0.5,
        timestamp_ns=T0, tick=1, action="PASSIVE_MAKER_EXIT",
    )
    st = _State({BOOK: _Book(100.10, 100.20), other: _Book(100.10, 100.20)}, T0)
    r = _Response()
    assert a._a191_service_reprice_cancels(r, st) == 2
    assert sorted(b for b, _ in r.cancels) == [BOOK, other]


def test_filled_order_is_not_cancelled_after_seeding():
    a = _stale_agent()
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_FILLED)
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 0


def test_stats_report_behaviour_change():
    assert 'stats["direct_a19_behaviour_change"] = self._a19_behaviour_change()' in SRC
    assert 'stats["direct_a19_phase"] = self._a19_runtime_phase()' in SRC


# ------------------------------------------------- activation self-reporting

def test_banner_reports_every_activation_field_the_gate_checks():
    """A 50-100 tick activation check should be one grep, not six."""
    a = _stale_agent()
    a._a191_service_reprice_cancels(_Response(), _state())
    row = a.rows("A19_ACTIVATION_BANNER")[0]
    assert row["a19_phase"] == "B_QUEUE_PRESERVING_EXIT"
    assert row["a19_behaviour_change"] == 1
    assert row["shadow_mode"] == 0
    assert row["profitable_exit_ttl_ms"] == 4000.0
    assert row["exit_refresh_version"] == "direct_exit_refresh_v4_16_2_a1_9_2"
    assert row["phase_b_events"] == (
        "A19_QUEUE_HOLD,A19_EXIT_REPRICE_CANCEL,A19_REPRICE_BUDGET_BLOCK"
    )


def test_banner_is_emitted_once():
    a = _stale_agent()
    for _ in range(3):
        a._a191_service_reprice_cancels(_Response(), _state())
    assert len(a.rows("A19_ACTIVATION_BANNER")) == 1


def test_banner_reports_shadow_when_the_switch_is_off():
    a = _stale_agent()
    a.research_a191_queue_preservation_enabled = False
    a._a191_service_reprice_cancels(_Response(), _state())
    row = a.rows("A19_ACTIVATION_BANNER")[0]
    assert row["a19_phase"] == "A_SHADOW_MEASUREMENT"
    assert row["a19_behaviour_change"] == 0
    assert row["shadow_mode"] == 1


def test_alarm_fires_when_reprice_candidates_never_get_cancelled():
    """The 2,961-tick invalid run must be impossible to repeat silently."""
    a = _stale_agent()
    # Candidates accumulate but every cancel is blocked by the budget guard,
    # which is what an inert behavioural path looks like from outside.
    a._book_instructions = 5
    for tick in range(1, 30):
        a._tick = tick
        a._a191_service_reprice_cancels(_Response(), _state())
    alarms = a.rows("A19_ACTIVATION_ALARM")
    assert len(alarms) == 1
    assert alarms[0]["verdict"] == "PHASE_B_INERT_STOP_THE_RUN"
    assert alarms[0]["reprice_cancels"] == 0
    assert alarms[0]["reprice_candidates"] >= a.A191_ACTIVATION_ALARM_CANDIDATES
    assert alarms[0]["deferred_budget"] > 0


def test_alarm_does_not_fire_once_a_cancel_lands():
    a = _stale_agent()
    for tick in range(1, 30):
        a._tick = tick
        a._a191_service_reprice_cancels(_Response(), _state())
    assert a.rows("A19_ACTIVATION_ALARM") == []
    assert a._a191_reprice_cancels >= 1


def test_alarm_is_silent_in_shadow_mode():
    """Shadow mode is a legitimate configuration, not a broken activation."""
    a = _stale_agent()
    a.research_a191_queue_preservation_enabled = False
    for tick in range(1, 30):
        a._tick = tick
        a._a191_service_reprice_cancels(_Response(), _state())
    assert a.rows("A19_ACTIVATION_ALARM") == []


def test_events_use_the_names_the_analysis_greps():
    """A191_* produced two false 'not wired' diagnoses; A19_* is what is read."""
    for name in ("A19_QUEUE_HOLD", "A19_EXIT_REPRICE_CANCEL", "A19_REPRICE_BUDGET_BLOCK"):
        assert f'"{name}"' in SRC, name
    assert "A191_EXIT_REPRICE_CANCEL" not in SRC
    assert "A191_EXIT_HOLD" not in SRC
    assert "A191_REPRICE_DEFERRED" not in SRC


def test_refresh_version_advances_so_it_is_an_activation_signal():
    from research_direct_exit_refresh import DIRECT_EXIT_REFRESH_VERSION
    assert DIRECT_EXIT_REFRESH_VERSION == "direct_exit_refresh_v4_16_2_a1_9_2"
