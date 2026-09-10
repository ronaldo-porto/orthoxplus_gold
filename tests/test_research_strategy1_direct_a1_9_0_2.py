"""A1.9.0.2 -- live resting-exit observer.

A1.9.0 and A1.9.0.1 both measured ``resting_present = 0`` and produced zero
shadow decisions.  The ledger was not the problem; the call site was.  Both
revisions hung the observer on ``_research_place_maker_exit``, which only runs
once the strategy has decided to place a NEW exit, and that moment is
structurally after the old exit is gone:

* the frozen final validator drops a placement onto a book that still holds a
  live order, so a book with a resting exit never reaches the placement path
* the 3,000 ms exit TTL expires a full second before the 4,000 ms re-quote
  cycle returns, so the order is already retired when the path does run

A1.9.0.2 moves the observation to the top of ``respond``: every open-inventory
book, every tick, before any live-order or ownership gate.  Still measurement
only -- A1.9.1 is what acts on the decision.
"""

import ast
import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
SIMPLE = STRATEGY_DIR / "Strategy1_Research_Simple.py"
LEDGER = STRATEGY_DIR / "research_direct_exit_ledger.py"
FROZEN = STRATEGY_DIR / "Strategy1_Research.py"

sys.path.insert(0, str(STRATEGY_DIR))

SRC = SIMPLE.read_text()
LEDGER_SRC = LEDGER.read_text()

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
    ABSENT_EXPIRED,
    ABSENT_FILLED,
    ABSENT_LEDGER_SWEEP,
    AGENT_CANCEL_DISPOSITIONS,
    EVAL_PERSIST_ELIGIBLE,
    EXIT_HOLD,
    REASON_QUEUE_PRESERVED,
    REASON_STALE_BEHIND_TOUCH,
    behind_ticks,
    classify_resting_maker_exit,
    exit_eval_class,
    forgone_edge_bps,
)
from research_unified_exit import completion_net_bps as unified_completion_net_bps  # noqa: E402

MS = 1_000_000
PUBLISH_NS = 1000 * MS
# A realistic simulation clock.  Timestamp 0 is not a usable fixture: the
# ledger's TTL-filtered read deliberately drops rows of unknowable age.
T0 = 1_700_000_000_000_000_000
BOOK = 7


# --------------------------------------------------------------- versioning

def test_version_pins_advance_to_a1_9_0_2():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_1"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_1"' in SRC
    assert DIRECT_EXIT_LEDGER_VERSION == "direct_exit_ledger_v4_16_2_a1_9_0_3"


def test_frozen_base_untouched():
    """A1.9.0.2 is a Simple-layer observer.  The frozen authority does not move."""
    frozen = FROZEN.read_text()
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in frozen
    assert "_a19_observe_tick_resting_exits" not in frozen


# ------------------------------------------------------- call-site placement

def test_observer_runs_before_super_respond():
    """The whole defect was call-site timing; pin the ordering in place."""
    body = SRC.split("    def respond(self, state:")[1].split("\n    def ")[0]
    observe = body.index("_a19_observe_tick_resting_exits")
    delegate = body.index("super().respond(state)")
    assert observe < delegate, "observer must run before the frozen decision chain"


def test_observer_is_fault_isolated():
    body = SRC.split("    def respond(self, state:")[1].split("\n    def ")[0]
    guarded = body.split("_a19_observe_tick_resting_exits")[1]
    assert guarded.lstrip().startswith("(state)")
    assert "except Exception" in guarded.split("super().respond")[0]


def test_observer_never_calls_net_inventory():
    """`_net_inventory` ages `_position_ticks`; the pre-tick pass must not.

    Position age drives the exit escalation ladder, so calling it before the
    base increments `_tick` would double-age an idle book and change trading
    behaviour in a measurement-only revision.
    """
    body = SRC.split("    def _a19_observe_tick_resting_exits")[1].split("\n    def ")[0]
    # The comment naming the hazard is welcome; an actual call is not.
    assert "self._net_inventory(" not in body
    assert "_position_tracker_snapshot" in body


def test_observer_emits_no_instructions():
    """Measurement only: no placement, cancel, or threshold write."""
    body = SRC.split("    def _a19_observe_tick_resting_exits")[1].split("\n    def ")[0]
    for forbidden in (
        "response", "place_order", "cancel_orders", "add_instruction",
        "self.research_profitable_exit_ttl_ms =",
    ):
        assert forbidden not in body, forbidden


# ------------------------------------------------- side-effect-free inventory

def test_resting_inventory_view_is_frozen_and_pure():
    view = RestingInventoryView.from_tracker(
        types.SimpleNamespace(net_qty=0.5, vwap_entry=100.0)
    )
    assert view.net_base == 0.5 and view.vwap_entry == 100.0
    try:
        view.net_base = 1.0
    except Exception:
        pass
    else:
        raise AssertionError("view must be immutable")


def test_resting_inventory_view_tolerates_garbage():
    view = RestingInventoryView.from_tracker(types.SimpleNamespace())
    assert view.net_base == 0.0 and view.vwap_entry is None
    view = RestingInventoryView.from_tracker(
        types.SimpleNamespace(net_qty="x", vwap_entry="y")
    )
    assert view.net_base == 0.0 and view.vwap_entry is None


# ------------------------------------------------------- behavioural harness

def _load_observer():
    """Bind the observer methods to a stub exposing only what they touch.

    The full agent module cannot be imported in this environment, so the
    methods are extracted from the source they will actually run as.
    """
    cls = next(
        n for n in ast.parse(SRC).body
        if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple"
    )
    wanted = {
        "_a19_observe_tick_resting_exits", "_a19_emit_tick_lifecycle",
        "_a19_resting_net_bps", "_a19_close_side_orders", "_a19_tick_size",
        "_a19_ledger_ref", "_direct_account_orders",
        # A1.9.0.3 dependencies of the observer body.
        "_a19_resolve_disposition", "_a19_is_entry_quote_row",
        "_direct_entry_quote_client_ids",
    }
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {m.name for m in methods} == wanted
    ns = {
        "math": math, "Any": object,
        "DirectExitLedger": DirectExitLedger,
        "RestingInventoryView": RestingInventoryView,
        "close_side_for": close_side_for,
        "ABSENT_EXPIRED": ABSENT_EXPIRED, "ABSENT_FILLED": ABSENT_FILLED,
        "ABSENT_LEDGER_SWEEP": ABSENT_LEDGER_SWEEP,
        "AGENT_CANCEL_DISPOSITIONS": AGENT_CANCEL_DISPOSITIONS,
        "LEDGER_REMOVED_CANCELLED": LEDGER_REMOVED_CANCELLED,
        "LEDGER_REMOVED_FILLED": LEDGER_REMOVED_FILLED,
        "LEDGER_REMOVED_TTL_SWEEP": LEDGER_REMOVED_TTL_SWEEP,
        "EVAL_PERSIST_ELIGIBLE": EVAL_PERSIST_ELIGIBLE, "EXIT_HOLD": EXIT_HOLD,
        "behind_ticks": behind_ticks,
        "classify_resting_maker_exit": classify_resting_maker_exit,
        "exit_eval_class": exit_eval_class, "forgone_edge_bps": forgone_edge_bps,
        "unified_completion_net_bps": unified_completion_net_bps,
        "DIRECT_MAKER_EXIT_TARGET_BPS": 2.0,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 "<a1902_observer>", "exec"), ns)
    return {name: ns[name] for name in wanted}


class _Level:
    def __init__(self, price):
        self.price = price


class _Book:
    def __init__(self, bid, ask):
        self.bids = [_Level(bid)]
        self.asks = [_Level(ask)]


class _State:
    def __init__(self, books, timestamp):
        self.books = books
        self.timestamp = timestamp
        self.config = types.SimpleNamespace(priceDecimals=2, publish_interval=PUBLISH_NS)


class _Agent:
    research_profitable_exit_ttl_ms = 3000.0
    research_profitable_exit_min_net_bps = 0.0
    research_profitable_exit_reprice_ticks = 3.0
    _research_market_regime = "NORMAL"
    _research_volume_decimals = 4
    research_score_ev_fees_bps = 1.0

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
        self._tick = tick - 1  # the base increments _tick after this pass
        self._a19_observe_tick_resting_exits(_State({BOOK: _Book(bid, ask)}, timestamp))

    def rows(self, event_type="A19_TICK_OBSERVE"):
        return [p for e, p in self.events if e == event_type]


_STATIC = {"_a19_tick_size", "_direct_entry_quote_client_ids"}
for _name, _fn in _load_observer().items():
    setattr(_Agent, _name, staticmethod(_fn) if _name in _STATIC else _fn)


def _agent_with_resting_exit():
    agent = _Agent()
    agent.positions[BOOK] = (0.5, 100.00)   # long 0.5 base entered at 100.00
    agent._a19_ledger.note_accepted(
        order_id=555, book_id=BOOK, side=1, price=100.20, quantity=0.5,
        timestamp_ns=T0, tick=1, action="PASSIVE_MAKER_EXIT",
    )
    return agent


# ------------------------------------------------------ the A1.9.0.2 gate

def test_observer_sees_the_exit_for_its_whole_resting_life():
    """The gate A1.9.0 and A1.9.0.1 both failed."""
    agent = _agent_with_resting_exit()
    for i in range(3):
        agent.observe(tick=i + 1, timestamp=T0 + i * PUBLISH_NS)

    rows = agent.rows()
    assert len(rows) == 3
    assert [r["resting_present"] for r in rows] == [1, 1, 1]
    assert [r["resting_age_ms"] for r in rows] == [0.0, 1000.0, 2000.0]
    assert [r["resting_observed_ticks"] for r in rows] == [1, 2, 3]
    assert agent._a19_tick_resting_hits == 3
    assert agent._a19_tick_first_sightings == 1


def test_account_snapshot_stays_blind_while_the_ledger_sees():
    """The A1.9.0.1 control: this is the blind spot, still measured."""
    agent = _agent_with_resting_exit()
    agent.observe(tick=1, timestamp=T0)
    row = agent.rows()[0]
    assert row["resting_present"] == 1
    assert row["resting_present_account"] == 0


def test_shadow_decisions_are_produced():
    agent = _agent_with_resting_exit()
    for i in range(3):
        agent.observe(tick=i + 1, timestamp=T0 + i * PUBLISH_NS)
    rows = agent.rows()
    assert all(r["shadow_decision"] == EXIT_HOLD for r in rows)
    assert all(r["shadow_reason"] == REASON_QUEUE_PRESERVED for r in rows)
    assert agent._a19_tick_shadow_holds == 3
    assert agent._a19_tick_shadow_reprices == 0
    assert all(r["shadow_mode"] == 1 for r in rows)


def test_structural_staleness_still_reprices():
    """HOLD is the default, but a market that walks away must not be held."""
    agent = _agent_with_resting_exit()
    agent.observe(tick=1, timestamp=T0)
    agent.observe(tick=2, timestamp=T0 + PUBLISH_NS, bid=99.95, ask=100.05)
    row = agent.rows()[-1]
    assert row["shadow_decision"] != EXIT_HOLD
    assert row["shadow_reason"] == REASON_STALE_BEHIND_TOUCH
    assert row["drift_ticks"] >= 3.0
    assert agent._a19_tick_shadow_reprices == 1


def test_favourable_drift_is_held_not_repriced():
    """A resting sell BELOW the touch is closer to filling; A1.8 tore these up."""
    agent = _agent_with_resting_exit()
    agent.observe(tick=1, timestamp=T0)
    agent.observe(tick=2, timestamp=T0 + PUBLISH_NS, bid=100.40, ask=100.50)
    row = agent.rows()[-1]
    assert row["shadow_decision"] == EXIT_HOLD
    assert row["drift_ticks"] < 0.0
    assert row["forgone_edge_bps"] > 0.0


def test_expiry_closes_the_lifecycle():
    agent = _agent_with_resting_exit()
    for i in range(3):
        agent.observe(tick=i + 1, timestamp=T0 + i * PUBLISH_NS)
    agent._a19_ledger.note_removed(555, cause="LEDGER_CANCELLED")
    agent.observe(tick=4, timestamp=T0 + 3 * PUBLISH_NS)

    life = agent.rows("A19_TICK_LIFECYCLE")
    assert len(life) == 1
    assert life[0]["disposition"] == ABSENT_EXPIRED
    assert life[0]["observed_ticks"] == 3
    assert life[0]["peak_age_ms"] == 2000.0
    assert agent._a19_tick_lifecycles == 1


def test_fill_is_distinguished_from_expiry():
    agent = _agent_with_resting_exit()
    agent.observe(tick=1, timestamp=T0)
    agent._a19_ledger.note_removed(555, cause="LEDGER_FILLED")
    agent.positions[BOOK] = (0.2, 100.00)          # position shrank -> filled
    agent.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert agent.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_FILLED


def test_flat_book_is_skipped_and_retires_its_lifecycle():
    agent = _agent_with_resting_exit()
    agent.observe(tick=1, timestamp=T0)
    agent.positions[BOOK] = (0.0, None)
    agent.observe(tick=2, timestamp=T0 + PUBLISH_NS)
    assert agent._a19_tick_observations == 1          # flat book not observed
    assert agent.rows("A19_TICK_LIFECYCLE")[0]["disposition"] == ABSENT_FILLED
    assert BOOK not in agent._a19_tick_seen


def test_expired_row_beyond_ttl_is_not_reported_as_resting():
    """Past its TTL the exchange has retired it, cancellation notice or not."""
    agent = _agent_with_resting_exit()
    agent.observe(tick=1, timestamp=T0 + 4 * PUBLISH_NS)   # 4,000 ms > 3,000 ms TTL
    row = agent.rows()[0]
    assert row["resting_present"] == 0
    assert row["ledger_expiry_lagged"] == 1
    assert agent._a19_tick_expiry_lag_hits == 1


def test_untimed_rows_are_counted_not_silently_dropped():
    """An unknowable-age row must not masquerade as a blind observer."""
    agent = _Agent()
    agent.positions[BOOK] = (0.5, 100.00)
    agent._a19_ledger.note_accepted(
        order_id=556, book_id=BOOK, side=1, price=100.20, quantity=0.5,
        timestamp_ns=0, tick=1, action="PASSIVE_MAKER_EXIT",
    )
    agent.observe(tick=1, timestamp=T0)
    assert agent.rows()[0]["ledger_untimed"] == 1
    assert agent._a19_tick_untimed_rows == 1


def test_short_side_uses_the_bid_touch():
    agent = _Agent()
    agent.positions[BOOK] = (-0.5, 100.00)        # short: exit is a BUY
    agent._a19_ledger.note_accepted(
        order_id=557, book_id=BOOK, side=0, price=99.80, quantity=0.5,
        timestamp_ns=T0, tick=1, action="PASSIVE_MAKER_EXIT",
    )
    agent.observe(tick=1, timestamp=T0)
    row = agent.rows()[0]
    assert row["resting_present"] == 1
    assert row["touch_price"] == 100.10           # the bid, not the ask
    assert row["net_base"] == -0.5


def test_opposite_side_order_is_not_mistaken_for_an_exit():
    agent = _Agent()
    agent.positions[BOOK] = (0.5, 100.00)
    agent._a19_ledger.note_accepted(                # a BUY while long: an entry
        order_id=558, book_id=BOOK, side=0, price=99.90, quantity=0.5,
        timestamp_ns=T0, tick=1, action="ENTRY",
    )
    agent.observe(tick=1, timestamp=T0)
    assert agent.rows()[0]["resting_present"] == 0


def test_tick_label_matches_the_tick_being_observed():
    """The base increments _tick after this pass; the label must compensate."""
    agent = _agent_with_resting_exit()
    agent.observe(tick=9, timestamp=T0)
    assert agent.rows()[0]["tick"] == 9


def test_toxic_regime_is_excluded_from_the_eligible_denominator():
    agent = _agent_with_resting_exit()
    agent._research_market_regime = "TOXIC"
    agent.observe(tick=1, timestamp=T0)
    assert agent.rows()[0]["eval_class"] != EVAL_PERSIST_ELIGIBLE
    assert agent._a19_tick_eligible == 0


def test_missing_book_side_does_not_raise():
    agent = _agent_with_resting_exit()
    empty = types.SimpleNamespace(bids=[], asks=[])
    agent._tick = 0
    agent._a19_observe_tick_resting_exits(_State({BOOK: empty}, T0))
    row = agent.rows()[0]
    assert row["touch_price"] is None
    assert row["shadow_decision"] == ""


# ------------------------------------------------------------- stats surface

def test_gate_metrics_are_reported():
    for key in (
        "direct_a1902_tick_observations", "direct_a1902_tick_resting_hits",
        "direct_a1902_tick_shadow_holds", "direct_a1902_tick_shadow_reprices",
        "direct_a1902_tick_lifecycles", "direct_a1902_tick_untimed_rows",
    ):
        assert f'stats["{key}"]' in SRC, key
    assert 'stats["direct_a19_phase"] = "B_QUEUE_PRESERVING_EXIT"' in SRC


def test_a1901_control_counters_are_retained():
    """The old counters prove the placement path was the blind one; keep them."""
    for key in ("direct_a1901_ledger_resting_hits", "direct_a1901_ledger_only_hits"):
        assert f'stats["{key}"]' in SRC, key
    assert "_a19_observe_exit_evaluation" in SRC
