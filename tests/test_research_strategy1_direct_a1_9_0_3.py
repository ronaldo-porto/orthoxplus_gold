"""A1.9.0.3 -- lifecycle attribution repair.

A1.9.0.2 passed the observability gate (665 live sightings, 329 HOLD / 162
REPRICE, cancel acks 60/60 at T+1) but mislabelled explicitly cancelled exits
as EXPIRED. Four distinct defects caused that:

1. **Two cancel paths were never registered.** Only `_direct_cancel_unsafe_wait_exits`
   called `_a19_note_exit_cancel`; `_direct_cancel_entry_quotes` and the
   partial-remainder cancel did not, so anything they killed read as EXPIRED.
2. **The cancel peek was not keyed to the tracked order.** It matched any
   watched order on the book, so it could borrow another order's reason.
3. **The watch is consumed by the placement path.** `_a19_settle_cancel_watch`
   pops entries, so by the time the tick observer noticed the disappearance the
   reason could already be gone.
4. **Entry quotes were adopted as exits.** On a long book the entry ASK rests on
   the close side; adopting one inflates `resting_present` and the hold rate,
   and reports EXPIRED when the quote manager cancels it.

The repair ranks evidence instead of guessing: ledger removal cause, then a
cancel we registered for that exact order id, then the position-shrank fallback.
Still measurement only.
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

sys.path.insert(0, str(STRATEGY_DIR))

SRC = SIMPLE.read_text()

from research_direct_exit_ledger import (  # noqa: E402
    DIRECT_EXIT_LEDGER_VERSION,
    LEDGER_REMOVED_CANCELLED,
    LEDGER_REMOVED_FILLED,
    LEDGER_REMOVED_TTL_SWEEP,
    DirectExitLedger,
    RestingInventoryView,
    close_side_for,
)
from research_direct_exit_refresh import (  # noqa: E402
    ABSENT_ENTRY_QUOTE_CANCEL,
    ABSENT_EXPIRED,
    ABSENT_FILLED,
    ABSENT_LEDGER_SWEEP,
    ABSENT_NEG_AGGRESSIVE_CANCEL,
    ABSENT_PARTIAL_REMAINDER_CANCEL,
    ABSENT_REPRICE_CANCEL,
    ABSENT_WAIT_CANCEL,
    AGENT_CANCEL_DISPOSITIONS,
    DIRECT_EXIT_REFRESH_VERSION,
    EVAL_PERSIST_ELIGIBLE,
    EXIT_HOLD,
    behind_ticks,
    classify_resting_maker_exit,
    exit_eval_class,
    forgone_edge_bps,
)
from research_unified_exit import completion_net_bps as unified_completion_net_bps  # noqa: E402

MS = 1_000_000
PUBLISH_NS = 1000 * MS
T0 = 1_700_000_000_000_000_000
BOOK = 7
ENTRY_QUOTE_ASK_CID = 70000 + BOOK * 10 + 2


# --------------------------------------------------------------- versioning

def test_version_pins_advance_to_a1_9_0_3():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_1"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_1"' in SRC
    assert DIRECT_EXIT_LEDGER_VERSION == "direct_exit_ledger_v4_16_2_a1_9_0_3"
    assert DIRECT_EXIT_REFRESH_VERSION == "direct_exit_refresh_v4_16_2_a1_9_0_3"


def test_frozen_base_untouched():
    frozen = FROZEN.read_text()
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in frozen
    assert "_a19_" not in frozen


def test_still_measurement_only():
    body = SRC.split("    def _a19_observe_tick_resting_exits")[1].split("\n    def ")[0]
    for forbidden in ("response", "place_order", "cancel_orders", "self._net_inventory("):
        assert forbidden not in body, forbidden


# ------------------------------------------------- every cancel path registers

def test_all_three_cancel_paths_register_a_reason():
    """The defect was silence, so assert each cancel site names itself."""
    # 1 definition + 4 sites: WAIT, entry-quote, partial-remainder, and the
    # A1.9.1 Phase B reprice cancel.
    assert SRC.count("_a19_note_exit_cancel(") == 5
    for reason in (
        "ABSENT_WAIT_CANCEL", "ABSENT_ENTRY_QUOTE_CANCEL",
        "ABSENT_PARTIAL_REMAINDER_CANCEL", "ABSENT_REPRICE_CANCEL",
    ):
        assert reason in SRC, reason


def test_every_cancel_orders_call_has_a_registered_reason():
    """A new cancel site must not be able to land unregistered."""
    for chunk in SRC.split("response.cancel_orders(")[1:]:
        window = chunk[:1200]
        assert "_a19_note_exit_cancel" in window, chunk[:120]


def test_agent_cancel_vocabulary_is_complete():
    assert ABSENT_WAIT_CANCEL in AGENT_CANCEL_DISPOSITIONS
    assert ABSENT_NEG_AGGRESSIVE_CANCEL in AGENT_CANCEL_DISPOSITIONS
    assert ABSENT_REPRICE_CANCEL in AGENT_CANCEL_DISPOSITIONS
    assert ABSENT_ENTRY_QUOTE_CANCEL in AGENT_CANCEL_DISPOSITIONS
    assert ABSENT_PARTIAL_REMAINDER_CANCEL in AGENT_CANCEL_DISPOSITIONS
    # Neither of these is something we asked for.
    assert ABSENT_EXPIRED not in AGENT_CANCEL_DISPOSITIONS
    assert ABSENT_FILLED not in AGENT_CANCEL_DISPOSITIONS
    assert ABSENT_LEDGER_SWEEP not in AGENT_CANCEL_DISPOSITIONS


# ------------------------------------------------------ ledger removal causes

def _ledger_with_order(oid=555, **kw):
    led = DirectExitLedger()
    base = dict(order_id=oid, book_id=BOOK, side=1, price=100.20, quantity=0.5,
                timestamp_ns=T0, tick=1)
    base.update(kw)
    led.note_accepted(**base)
    return led


def test_ledger_remembers_why_an_order_left():
    led = _ledger_with_order()
    led.note_removed(555, cause=LEDGER_REMOVED_CANCELLED)
    assert led.removal_cause(555) == LEDGER_REMOVED_CANCELLED

    led = _ledger_with_order(oid=556)
    led.note_removed(556, cause=LEDGER_REMOVED_FILLED)
    assert led.removal_cause(556) == LEDGER_REMOVED_FILLED


def test_partial_fill_does_not_record_a_removal():
    """The remainder is still resting; it has not left the book."""
    led = _ledger_with_order()
    led.note_removed(555, cause=LEDGER_REMOVED_FILLED, filled_qty=0.2)
    assert led.removal_cause(555) == ""
    assert led.live_count(BOOK) == 1


def test_sweep_records_its_own_cause():
    led = _ledger_with_order()
    led.sweep(T0 + 60 * 1000 * MS)
    assert led.removal_cause(555) == LEDGER_REMOVED_TTL_SWEEP


def test_reset_clears_causes_because_restarts_reuse_order_ids():
    led = _ledger_with_order()
    led.note_removed(555, cause=LEDGER_REMOVED_FILLED)
    led.reset()
    assert led.removal_cause(555) == ""


def test_removal_memo_is_bounded():
    led = DirectExitLedger()
    for oid in range(4000):
        led.note_accepted(order_id=oid, book_id=BOOK, side=1, price=100.0,
                          quantity=0.5, timestamp_ns=T0, tick=1)
        led.note_removed(oid, cause=LEDGER_REMOVED_CANCELLED)
    assert len(led.removal_causes) <= 2048


# ------------------------------------------------------- behavioural harness

def _load(names):
    cls = next(n for n in ast.parse(SRC).body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert {m.name for m in methods} == names, names ^ {m.name for m in methods}
    ns = {
        "math": math, "Any": object,
        "DirectExitLedger": DirectExitLedger,
        "RestingInventoryView": RestingInventoryView,
        "close_side_for": close_side_for,
        "LEDGER_REMOVED_CANCELLED": LEDGER_REMOVED_CANCELLED,
        "LEDGER_REMOVED_FILLED": LEDGER_REMOVED_FILLED,
        "LEDGER_REMOVED_TTL_SWEEP": LEDGER_REMOVED_TTL_SWEEP,
        "ABSENT_EXPIRED": ABSENT_EXPIRED, "ABSENT_FILLED": ABSENT_FILLED,
        "ABSENT_LEDGER_SWEEP": ABSENT_LEDGER_SWEEP,
        "AGENT_CANCEL_DISPOSITIONS": AGENT_CANCEL_DISPOSITIONS,
        "EVAL_PERSIST_ELIGIBLE": EVAL_PERSIST_ELIGIBLE, "EXIT_HOLD": EXIT_HOLD,
        "behind_ticks": behind_ticks,
        "classify_resting_maker_exit": classify_resting_maker_exit,
        "exit_eval_class": exit_eval_class, "forgone_edge_bps": forgone_edge_bps,
        "unified_completion_net_bps": unified_completion_net_bps,
        "DIRECT_MAKER_EXIT_TARGET_BPS": 2.0,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 "<a1903>", "exec"), ns)
    return {n: ns[n] for n in names}


WANTED = {
    "_a19_observe_tick_resting_exits", "_a19_emit_tick_lifecycle",
    "_a19_resolve_disposition", "_a19_is_entry_quote_row",
    "_direct_entry_quote_client_ids",
    "_a19_resting_net_bps", "_a19_close_side_orders", "_a19_tick_size",
    "_a19_ledger_ref", "_direct_account_orders", "_a19_note_exit_cancel",
}


class _Level:
    def __init__(self, price): self.price = price


class _Book:
    def __init__(self, bid, ask):
        self.bids = [_Level(bid)]; self.asks = [_Level(ask)]


class _State:
    def __init__(self, books, timestamp):
        self.books = books; self.timestamp = timestamp
        self.config = types.SimpleNamespace(priceDecimals=2, publish_interval=PUBLISH_NS)


class _Agent:
    research_profitable_exit_ttl_ms = 3000.0
    research_profitable_exit_min_net_bps = 0.0
    research_profitable_exit_reprice_ticks = 3.0
    _research_market_regime = "NORMAL"
    _research_volume_decimals = 4
    research_score_ev_fees_bps = 1.0
    A19_CANCEL_WATCH_MAX_TICKS = 10
    A19_CANCEL_ACK_BUDGET_TICKS = 2
    A19_CANCEL_MEMO_MAX = 2048

    def __init__(self):
        self._tick = 0
        self._a19_ledger = DirectExitLedger()
        self._a19_last_state_ns = 0
        self._a19_tick_seen = {}
        self._a19_pending_action = {}
        self._a19_cancel_watch = {}
        self._a19_cancel_reason = {}
        self._a19_tick_disposition_counts = {}
        self.accounts = {}
        self.positions = {}
        self.events = []
        for name in (
            "_a19_tick_passes", "_a19_tick_observations", "_a19_tick_resting_hits",
            "_a19_tick_expiry_lag_hits", "_a19_tick_eligible",
            "_a19_tick_eligible_with_resting", "_a19_tick_shadow_holds",
            "_a19_tick_shadow_reprices", "_a19_tick_first_sightings",
            "_a19_tick_lifecycles", "_a19_tick_max_observed_ticks",
            "_a19_tick_observed_ticks_total", "_a19_tick_untimed_rows",
            "_a19_tick_entry_quote_rows",
        ):
            setattr(self, name, 0)

    def _position_tracker_snapshot(self, book_id):
        net, vwap = self.positions.get(int(book_id), (0.0, None))
        return types.SimpleNamespace(net_qty=net, vwap_entry=vwap)

    def _execution_flat_epsilon(self):
        return 0.5 * (10.0 ** -self._research_volume_decimals)

    def _research_live_fee_bps(self, book_id, *, is_maker=True, fallback_bps=None):
        return -0.5 if is_maker else 3.0

    def _emit(self, event_type, force=False, **payload):
        self.events.append((event_type, payload))

    def observe(self, tick, timestamp, bid=100.10, ask=100.20):
        self._tick = tick - 1
        self._a19_observe_tick_resting_exits(_State({BOOK: _Book(bid, ask)}, timestamp))

    def rows(self, event_type="A19_TICK_OBSERVE"):
        return [p for e, p in self.events if e == event_type]


for _n, _f in _load(WANTED).items():
    setattr(_Agent, _n,
            staticmethod(_f) if _n in {"_a19_tick_size", "_direct_entry_quote_client_ids"} else _f)


def _agent_with_resting_exit(oid=555, client_id=None):
    a = _Agent()
    a.positions[BOOK] = (0.5, 100.00)
    a._a19_ledger.note_accepted(
        order_id=oid, book_id=BOOK, side=1, price=100.20, quantity=0.5,
        timestamp_ns=T0, tick=1, action="PASSIVE_MAKER_EXIT", client_id=client_id,
    )
    return a


# --------------------------------------------- the four dispositions, correct

def test_real_expiry_is_labelled_expired():
    """A cancellation notice we never asked for is the exchange retiring it."""
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_CANCELLED)
    a.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert a.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_EXPIRED
    assert a.rows("A19_TICK_LIFECYCLE")[0]["agent_cancelled"] == 0


def test_explicit_wait_cancel_is_labelled_wait_cancel():
    """The exact bug reported: this used to read EXPIRED."""
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._tick = 1
    a._a19_note_exit_cancel(BOOK, [555], ABSENT_WAIT_CANCEL)
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_CANCELLED)
    a.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    row = a.rows("A19_TICK_LIFECYCLE")[0]
    assert row["disposition"] == ABSENT_WAIT_CANCEL
    assert row["agent_cancelled"] == 1


def test_reprice_cancel_is_labelled_reprice_cancel():
    """Phase B's disposition must already resolve correctly."""
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._tick = 1
    a._a19_note_exit_cancel(BOOK, [555], ABSENT_REPRICE_CANCEL)
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_CANCELLED)
    a.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert a.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_REPRICE_CANCEL


def test_fill_is_labelled_filled():
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_FILLED)
    a.positions[BOOK] = (0.2, 100.00)
    a.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert a.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_FILLED


def test_fill_beats_a_stale_cancel_reason():
    """A fill is a fill even if we had also queued a cancel for that order."""
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._tick = 1
    a._a19_note_exit_cancel(BOOK, [555], ABSENT_WAIT_CANCEL)
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_FILLED)
    a.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert a.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_FILLED


def test_ledger_sweep_is_not_reported_as_expiry():
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._a19_ledger.sweep(T0 + 60 * 1000 * MS)
    a.observe(tick=2, timestamp=T0 + 60 * 1000 * MS)
    assert a.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_LEDGER_SWEEP


# ------------------------------------------ the defects that caused mislabels

def test_reason_survives_the_placement_path_consuming_the_watch():
    """Defect 3: `_a19_settle_cancel_watch` pops the watch entry."""
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._tick = 1
    a._a19_note_exit_cancel(BOOK, [555], ABSENT_WAIT_CANCEL)
    a._a19_cancel_watch.clear()               # simulate the placement path settling it
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_CANCELLED)
    a.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert a.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_WAIT_CANCEL


def test_another_orders_cancel_is_not_borrowed():
    """Defect 2: the peek matched any watched order on the book."""
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._tick = 1
    a._a19_note_exit_cancel(BOOK, [999], ABSENT_WAIT_CANCEL)   # a different order
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_CANCELLED)
    a.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert a.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_EXPIRED


def test_entry_quote_is_never_adopted_as_an_exit():
    """Defect 4: on a long book the entry ASK rests on the close side."""
    a = _Agent()
    a.positions[BOOK] = (0.5, 100.00)
    a._a19_ledger.note_accepted(
        order_id=777, book_id=BOOK, side=1, price=100.30, quantity=0.25,
        timestamp_ns=T0, tick=1, client_id=ENTRY_QUOTE_ASK_CID,
    )
    a.observe(tick=1, timestamp=T0)
    row = a.rows()[0]
    assert row["resting_present"] == 0
    assert a._a19_tick_entry_quote_rows == 1
    assert a._a19_tick_shadow_holds == 0


def test_a_real_exit_is_still_adopted_alongside_an_entry_quote():
    a = _agent_with_resting_exit()
    a._a19_ledger.note_accepted(
        order_id=777, book_id=BOOK, side=1, price=100.30, quantity=0.25,
        timestamp_ns=T0, tick=1, client_id=ENTRY_QUOTE_ASK_CID,
    )
    a.observe(tick=1, timestamp=T0)
    row = a.rows()[0]
    assert row["resting_present"] == 1
    assert a._a19_tick_seen[BOOK]["order_id"] == 555


def test_entry_quote_client_ids_match_the_cancel_path_convention():
    assert _Agent._direct_entry_quote_client_ids(BOOK) == {
        70000 + BOOK * 10 + 1, 70000 + BOOK * 10 + 2,
    }


def test_flat_book_retirement_names_the_real_reason():
    """A WAIT cancel on a book that then flattens is not a fill."""
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._tick = 1
    a._a19_note_exit_cancel(BOOK, [555], ABSENT_WAIT_CANCEL)
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_CANCELLED)
    a.positions[BOOK] = (0.0, None)
    a.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert a.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_WAIT_CANCEL


def test_disposition_counts_are_tallied():
    a = _agent_with_resting_exit()
    a.observe(tick=1, timestamp=T0)
    a._a19_ledger.note_removed(555, cause=LEDGER_REMOVED_CANCELLED)
    a.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert a._a19_tick_disposition_counts.get(ABSENT_EXPIRED) == 1


def test_cancel_memo_is_bounded():
    a = _Agent()
    a._tick = 1
    for oid in range(4000):
        a._a19_note_exit_cancel(BOOK, [oid], ABSENT_WAIT_CANCEL)
    assert len(a._a19_cancel_reason) <= a.A19_CANCEL_MEMO_MAX


# ------------------------------------------------- A1.9.0.2 gate not regressed

def test_observer_still_sees_the_whole_resting_life():
    a = _agent_with_resting_exit()
    for i in range(3):
        a.observe(tick=i + 1, timestamp=T0 + i * PUBLISH_NS)
    rows = a.rows()
    assert [r["resting_present"] for r in rows] == [1, 1, 1]
    assert a._a19_tick_shadow_holds == 3
    assert all(r["shadow_decision"] == EXIT_HOLD for r in rows)


def test_stats_surface_the_new_attribution():
    for key in (
        "direct_a1903_agent_cancelled_lifecycles",
        "direct_a1903_entry_quote_rows_excluded",
        "direct_a1903_cancel_reasons_tracked",
    ):
        assert f'stats["{key}"]' in SRC, key
    assert 'stats["direct_a19_phase"] = "B_QUEUE_PRESERVING_EXIT"' in SRC
