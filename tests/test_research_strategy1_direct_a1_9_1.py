"""A1.9.1 -- Queue-Preserving Maker Exit, behavioural Phase B.

Phase A established, over 260 ticks: 960 live resting-exit sightings, a clean
HOLD/REPRICE split (QUEUE_PRESERVED vs STALE_BEHIND_TOUCH with no mixed
reasons), HOLD forgone edge median 0.00 / p90 1.55 bps, and cancel acks 74/74
at exactly T+1.

Phase B acts on that decision. The behavioural delta is exactly two things,
which are one mechanism and must not be split:

  1. `research_profitable_exit_ttl_ms` 3000 -> 4000 (4x the 1,000 ms publish
     cadence), closing the dead window between expiry and the next re-quote;
  2. an explicit cancel for a resting exit the classifier calls stale.

HOLD is the ABSENCE of an action -- today's baseline already rides the exit to
expiry, because the frozen hold hook reads the blind `account.orders` view. So
every new risk lives in the reprice cancels, and that is what these tests pin.

Where the cancel is emitted matters: on the A1.9.0.1 build the placement path
saw a live resting exit on 0 of 492 calls while the tick observer saw 960, so a
cancel emitted only from the placement path would never fire. It goes out in a
post-pass after the frozen chain, where the shared 5-instruction book budget is
also known.
"""

import ast
import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
SIMPLE = STRATEGY_DIR / "Strategy1_Research_Simple.py"
FROZEN = STRATEGY_DIR / "Strategy1_Research.py"
LAUNCHER = ROOT / "run_strategy1_research_simple_multi.sh"

sys.path.insert(0, str(STRATEGY_DIR))

SRC = SIMPLE.read_text()
LAUNCHER_SRC = LAUNCHER.read_text()

from research_direct_exit_ledger import (  # noqa: E402
    LEDGER_REMOVED_CANCELLED, LEDGER_REMOVED_FILLED, LEDGER_REMOVED_TTL_SWEEP,
    DirectExitLedger, RestingInventoryView, close_side_for,
)
from research_direct_exit_refresh import (  # noqa: E402
    ABSENT_EXPIRED, ABSENT_FILLED, ABSENT_LEDGER_SWEEP, ABSENT_REPRICE_CANCEL,
    AGENT_CANCEL_DISPOSITIONS, EVAL_PERSIST_ELIGIBLE, EXIT_HOLD, EXIT_REPRICE,
    REASON_QUEUE_PRESERVED, REASON_STALE_BEHIND_TOUCH,
    behind_ticks, classify_resting_maker_exit, exit_eval_class, forgone_edge_bps,
)
from research_unified_exit import completion_net_bps as unified_completion_net_bps  # noqa: E402

MS = 1_000_000
PUBLISH_NS = 1000 * MS
T0 = 1_700_000_000_000_000_000
BOOK = 7
ENTRY_QUOTE_ASK_CID = 70000 + BOOK * 10 + 2


# --------------------------------------------------------------- versioning

def test_version_pins_advance_to_a1_9_1():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_5"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_5"' in SRC
    # A1.9.1.1: derived from runtime state, not a literal.
    assert 'stats["direct_a19_phase"] = self._a19_runtime_phase()' in SRC
    assert 'stats["direct_a19_behaviour_change"] = self._a19_behaviour_change()' in SRC


def test_frozen_base_untouched():
    frozen = FROZEN.read_text()
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in frozen
    assert "_a19" not in frozen and "_a191" not in frozen


def test_frozen_placement_delegation_is_not_wrapped():
    """A1.7.2 asserts this exact literal; wrapping it breaks three suites."""
    assert SRC.count("return super()._research_place_maker_exit") == 1
    assert "= super()._research_place_maker_exit" not in SRC


# ----------------------------------------------------- the atomic mechanism

def test_ttl_is_raised_through_params_not_initialize():
    """A1.8 mutated the TTL in initialize(); that must never recur."""
    assert "research_profitable_exit_ttl_ms=4000" in LAUNCHER_SRC
    assert "self.research_profitable_exit_ttl_ms = " not in SRC


def test_ttl_target_is_cadence_derived():
    from research_direct_exit_refresh import DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS
    assert DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS == 4000.0   # 4x 1,000 ms


def test_launcher_keeps_every_frozen_knob():
    for frozen in (
        "mm_base_size=0.25", "research_max_active_open_books=6",
        "research_max_open_books=6", "research_max_total_abs_base=2.0",
    ):
        assert frozen in LAUNCHER_SRC, frozen


def test_phase_b_has_a_master_switch():
    assert "research_a191_queue_preservation_enabled=1" in LAUNCHER_SRC
    assert "def _a191_enabled" in SRC


# ------------------------------------------------------------ the harness

WANTED = {
    "_a191_enabled", "_a191_verdict_store", "_a191_live_exit_row",
    "_a191_decide", "_a191_service_reprice_cancels",
    "_a19_is_entry_quote_row", "_direct_entry_quote_client_ids",
    "_a19_resting_net_bps", "_a19_tick_size", "_a19_ledger_ref",
    "_a19_note_exit_cancel", "_direct_account_orders",
    "_a191_check_activation", "_a19_runtime_phase", "_a19_behaviour_change",
}


def _load():
    cls = next(n for n in ast.parse(SRC).body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    assert {m.name for m in methods} == WANTED, WANTED ^ {m.name for m in methods}
    ns = {
        "math": math, "Any": object,
        "DirectExitLedger": DirectExitLedger,
        "RestingInventoryView": RestingInventoryView,
        "close_side_for": close_side_for,
        "EXIT_HOLD": EXIT_HOLD, "EXIT_REPRICE": EXIT_REPRICE,
        "REASON_QUEUE_PRESERVED": REASON_QUEUE_PRESERVED,
        "ABSENT_REPRICE_CANCEL": ABSENT_REPRICE_CANCEL,
        "behind_ticks": behind_ticks, "forgone_edge_bps": forgone_edge_bps,
        "classify_resting_maker_exit": classify_resting_maker_exit,
        "unified_completion_net_bps": unified_completion_net_bps,
        "DIRECT_MAKER_EXIT_TARGET_BPS": 2.0,
        "DIRECT_A19_PHASE_BEHAVIOURAL": "B_QUEUE_PRESERVING_EXIT",
        "DIRECT_A19_PHASE_SHADOW": "A_SHADOW_MEASUREMENT",
        "SIMPLE_ENGINE_VERSION": "strategy1_direct_v4_16_2_a1_9_4",
        "DIRECT_EXIT_REFRESH_VERSION": "direct_exit_refresh_v4_16_2_a1_9_2",
        "DIRECT_EXIT_LEDGER_VERSION": "direct_exit_ledger_v4_16_2_a1_9_0_3",
        "DIRECT_A19_PHASE_B_EVENTS": ("A19_QUEUE_HOLD", "A19_EXIT_REPRICE_CANCEL", "A19_REPRICE_BUDGET_BLOCK"),

    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 "<a191>", "exec"), ns)
    return {n: ns[n] for n in WANTED}


class _Level:
    def __init__(self, price): self.price = price


class _Book:
    def __init__(self, bid, ask):
        self.bids = [_Level(bid)]; self.asks = [_Level(ask)]


class _State:
    def __init__(self, books, timestamp):
        self.books = books; self.timestamp = timestamp
        self.config = types.SimpleNamespace(priceDecimals=2, publish_interval=PUBLISH_NS)


class _Response:
    """Records instructions the way the real response object would receive them."""
    def __init__(self):
        self.cancels = []
        self.placements = []

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
    A19_CANCEL_ACK_BUDGET_TICKS = 2
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


def _agent(oid=555, price=100.20, age_ms=0.0, client_id=None, net=0.5):
    a = _Agent()
    a.positions[BOOK] = (net, 100.00)
    a._a19_ledger.note_accepted(
        order_id=oid, book_id=BOOK, side=1 if net > 0 else 0, price=price,
        quantity=abs(net), timestamp_ns=T0 - int(age_ms * MS), tick=1,
        action="PASSIVE_MAKER_EXIT", client_id=client_id,
    )
    return a


def _state(bid=100.10, ask=100.20):
    return _State({BOOK: _Book(bid, ask)}, T0)


def _inv(a):
    return RestingInventoryView.from_tracker(a._position_tracker_snapshot(BOOK))


# ------------------------------------------------------------ HOLD behaviour

def test_fresh_exit_at_touch_holds():
    a = _agent()
    v = a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    assert v is not None
    assert v["decision"] == EXIT_HOLD
    assert v["reason"] == REASON_QUEUE_PRESERVED


def test_hold_emits_no_cancel():
    a = _agent()
    a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 0
    assert r.cancels == []


def test_favourable_drift_is_held():
    """A resting sell below the touch is closer to filling; A1.8 tore these up."""
    a = _agent()
    v = a._a191_decide(_state(100.40, 100.50), BOOK, _inv(a), 0.5, 100.50, "PASSIVE_MAKER_EXIT")
    assert v["decision"] == EXIT_HOLD
    assert v["drift_ticks"] < 0.0


def test_no_resting_order_falls_through_to_the_frozen_path():
    a = _Agent()
    a.positions[BOOK] = (0.5, 100.00)
    assert a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT") is None


def test_disabled_switch_falls_through():
    a = _agent()
    a.research_a191_queue_preservation_enabled = False
    assert a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT") is None


# --------------------------------------------------------- REPRICE behaviour

def test_stale_exit_reprices_and_cancels_the_exact_order():
    a = _agent(price=100.60)                     # far above a 100.20 touch
    v = a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    assert v["decision"] == EXIT_REPRICE
    assert v["reason"] == REASON_STALE_BEHIND_TOUCH
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 1
    assert r.cancels == [(BOOK, [555])]
    assert a._a191_reprice_cancels == 1


def test_cancel_is_registered_as_reprice_cancel():
    """So A1.9.0.3 attribution names it, instead of reporting EXPIRED."""
    a = _agent(price=100.60)
    a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    a._a191_service_reprice_cancels(_Response(), _state())
    assert a._a19_cancel_reason[555][1] == ABSENT_REPRICE_CANCEL
    assert ABSENT_REPRICE_CANCEL in AGENT_CANCEL_DISPOSITIONS


def test_never_cancel_and_replace_in_the_same_response():
    """The A1.7.4.3.1 ownership rule: the cancel must be visible in a later state."""
    a = _agent(price=100.60)
    a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    r = _Response()
    a._a191_service_reprice_cancels(r, _state())
    assert len(r.cancels) == 1
    assert r.placements == []
    # And the overlay returns 0 on any verdict, so no placement is emitted either.
    body = SRC.split("    def _research_place_maker_exit")[1].split("\n    def ")[0]
    gate = body.split("if verdict is not None:")[1].split("authority =")[0]
    assert "return 0" in gate
    assert "place_order" not in gate


def test_reprice_cancel_is_emitted_only_once():
    a = _agent(price=100.60)
    a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    r = _Response()
    a._a191_service_reprice_cancels(r, _state())
    a._a191_service_reprice_cancels(r, _state())
    assert len(r.cancels) == 1


def test_reprice_emits_telemetry():
    a = _agent(price=100.60)
    a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    a._a191_service_reprice_cancels(_Response(), _state())
    row = a.rows("A19_EXIT_REPRICE_CANCEL")[0]
    assert row["order_id"] == 555
    assert row["reason"] == REASON_STALE_BEHIND_TOUCH
    assert row["drift_ticks"] >= 3.0


# ------------------------------------------------------- the two REPRICE gates

def test_reprice_is_deferred_inside_one_publish_cycle_of_expiry():
    """Cancelling an order about to expire spends an instruction for nothing."""
    a = _agent(price=100.60, age_ms=3500.0)      # 500 ms left of a 4,000 ms TTL
    v = a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    assert v["decision"] == EXIT_HOLD
    assert v["deferred"] == "REMAINING_TTL"
    assert a._a191_reprice_deferred_ttl == 1
    r = _Response()
    a._a191_service_reprice_cancels(r, _state())
    assert r.cancels == []


def test_reprice_still_fires_with_a_full_cycle_left():
    a = _agent(price=100.60, age_ms=2000.0)      # 2,000 ms left
    v = a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    assert v["decision"] == EXIT_REPRICE
    assert v["deferred"] == ""


def test_reprice_falls_back_to_hold_when_the_instruction_budget_is_spent():
    """R3: cancels share the 5-instruction book budget with placements."""
    a = _agent(price=100.60)
    a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    a._book_instructions = 5
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 0
    assert r.cancels == []
    assert a._a191_reprice_deferred_budget == 1
    assert a.rows("A19_REPRICE_BUDGET_BLOCK")[0]["deferred"] == "INSTRUCTION_BUDGET"


# ------------------------------------------------------------ race conditions

def test_no_cancel_when_the_order_filled_before_the_post_pass():
    a = _agent(price=100.60)
    a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_FILLED)
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 0
    assert r.cancels == []


def test_no_cancel_when_a_different_order_now_rests():
    a = _agent(price=100.60)
    a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_CANCELLED)
    a._a19_ledger.note_accepted(order_id=999, book_id=BOOK, side=1, price=100.25,
                                quantity=0.5, timestamp_ns=T0, tick=2)
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 0


def test_expired_row_past_ttl_is_not_treated_as_resting():
    a = _agent(age_ms=5000.0)                    # past the 4,000 ms TTL
    assert a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT") is None


def test_entry_quote_is_never_preserved_or_cancelled_as_an_exit():
    a = _agent(client_id=ENTRY_QUOTE_ASK_CID, price=100.60)
    assert a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT") is None
    r = _Response()
    a._a191_service_reprice_cancels(r, _state())
    assert r.cancels == []


def test_short_position_uses_the_buy_side():
    a = _agent(oid=556, price=99.80, net=-0.5)
    v = a._a191_decide(_state(), BOOK, _inv(a), 0.5, 99.80, "PASSIVE_MAKER_EXIT")
    assert v is not None and v["decision"] == EXIT_HOLD


def test_flat_book_yields_no_verdict():
    a = _agent()
    a.positions[BOOK] = (0.0, None)
    assert a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT") is None


# ------------------------------------------------------------ verdict caching

def test_verdict_is_computed_once_per_book_per_tick():
    """The suppression and the cancel must never disagree within a tick."""
    a = _agent(price=100.60)
    first = a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    # A later call with a different comparand must reuse the cached verdict.
    second = a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.60, "PASSIVE_MAKER_EXIT")
    assert second is first


def test_verdict_cache_clears_on_the_next_tick():
    a = _agent(price=100.60)
    a._a191_decide(_state(), BOOK, _inv(a), 0.5, 100.20, "PASSIVE_MAKER_EXIT")
    a._tick = 2
    a._a191_verdict_store()
    assert a._a191_verdicts == {}


def test_post_pass_can_decide_without_the_placement_path():
    """A1.9.1.1: the post-pass seeds its own verdicts.

    A1.9.1 iterated only cached verdicts, whose sole writer was the placement
    path -- measured at 0 of 492 sightings of a live resting exit -- so it
    emitted 0 cancels in a 500-tick run while the classifier asked for 756.
    """
    a = _agent(price=100.60)
    r = _Response()
    assert a._a191_service_reprice_cancels(r, _state()) == 1
    assert r.cancels == [(BOOK, [555])]


def test_post_pass_falls_back_to_the_passive_touch_comparand():
    a = _agent(price=100.60)
    v = a._a191_decide(_state(), BOOK, _inv(a), 0.5, None, None)
    assert v is not None
    assert v["desired_price"] == 100.20      # the ask, for a long position


# ---------------------------------------------------------------- wiring

def test_post_pass_runs_after_the_frozen_chain():
    body = SRC.split("    def respond(self, state:")[1].split("\n    def ")[0]
    assert body.index("super().respond(state)") < body.index("_a191_service_reprice_cancels")


def test_hold_returns_zero_so_it_is_not_a_failed_exit():
    """`_research_note_exit_attempt` early-returns on placed=False."""
    frozen = FROZEN.read_text()
    block = frozen.split("def _research_note_exit_attempt")[1][:400]
    assert "if not placed:" in block and "return" in block


def test_gate_metrics_are_reported():
    for key in (
        "direct_a191_holds", "direct_a191_reprice_cancels",
        "direct_a191_reprice_deferred_ttl", "direct_a191_reprice_deferred_budget",
        "direct_a191_hold_share_pct", "direct_a191_placements_suppressed",
    ):
        assert f'stats["{key}"]' in SRC, key


def test_observation_rows_carry_order_id():
    """Without it a per-order hold rate cannot be computed after the fact."""
    block = SRC.split('"A19_TICK_OBSERVE", force=True')[1].split("shadow_mode=1")[0]
    assert "order_id=" in block
