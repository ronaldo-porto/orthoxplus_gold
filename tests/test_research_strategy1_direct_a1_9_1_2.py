"""A1.9.1.2 -- reprice ownership release integration.

A1.9.1.1 activated correctly: phase B, behaviour_change=1, shadow_mode=0,
42 reprice cancels by tick 100, and 42/42 acknowledged by the exchange at
exactly T+1. But the intended cycle did not happen.

Observed:

    T    A19 cancels exchange order
    T+1  exchange confirms  ->  ownership says UNKNOWN_EXCHANGE_ORDER_ID
    T+3  ownership finally releases via LOCAL_EXPIRY
    T+4  replacement placed          (median 4 ticks)

The A1.7.4.3.2 identity gate refuses to release on a cancellation whose
exchange order id it never registered, and Maker exits are not in that
registry: `register_exchange_identity` returns None when the placement notice
carries no client order id. So the local reservation survived to LOCAL_EXPIRY
and the book sat unquoted -- removing queue liquidity early while gaining no
faster repricing, the opposite of A1.9.1's intent.

The repair narrows rather than weakens the safety rule. Release requires all
of: an order A1.9 cancelled itself, exact exchange-order-id match, successful
cancellation, matching book, and an unambiguous pending reservation.
"""

import ast
import math
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
SIMPLE = STRATEGY_DIR / "Strategy1_Research_Simple.py"

sys.path.insert(0, str(STRATEGY_DIR))
SRC = SIMPLE.read_text()

from research_direct_book_ownership import (  # noqa: E402
    ExchangeOrderIdentity, PendingExposureOrder, canonical_order_side,
    cancellation_identity_decision,
)
from research_direct_exit_refresh import ABSENT_REPRICE_CANCEL  # noqa: E402

BOOK = 90
OID = 95959


def test_version_advances_to_a1_9_1_2():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_1_2"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_1_2"' in SRC


def test_release_is_attempted_before_the_stale_refusal():
    """Ordering is the fix: after the refusal it would never run."""
    body = SRC.split("def _direct_note_cancellation_identity_notice")[1].split("\n    def ")[0]
    hook = body.index("_a191_release_reprice_ownership")
    refusal = body.index("_direct_stale_cancels_ignored")
    assert hook < refusal


def test_safety_rule_is_narrowed_not_weakened():
    body = SRC.split("def _a191_release_reprice_ownership")[1].split("\n    def ")[0]
    assert "if row is None:" in body            # only orders A1.9 cancelled
    assert "if not bool(success):" in body      # failed cancel releases nothing
    assert "CANCEL_BOOK_ID_MISMATCH" in body    # book must match
    assert "NO_UNIQUE_PENDING_RESERVATION" in body  # ambiguity refused


def test_telemetry_separates_the_two_clocks():
    """ack_ticks conflated exchange response with internal settlement."""
    assert "exchange_ack_ticks=" in SRC
    assert "ownership_release_ticks=" in SRC
    assert "ack_ticks=age" not in SRC


# ------------------------------------------------------------------ harness

WANTED = {
    "_a191_release_reprice_ownership", "_direct_release_pending_exact",
    "_direct_pending_ledger", "_direct_emit_identity_diag",
    "_direct_emit_book_ownership_release",
    # The integration point: without this the release helper can be perfect and
    # still never be reached, which is the class of bug A1.9.1 already shipped.
    "_direct_note_cancellation_identity_notice", "_direct_exchange_identity_registry",
}


def _load():
    cls = next(n for n in ast.parse(SRC).body
               if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    methods = [n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name in WANTED]
    assert {m.name for m in methods} == WANTED, WANTED ^ {m.name for m in methods}
    ns = {
        "math": math, "Any": object,
        "canonical_order_side": canonical_order_side,
        "PendingExposureOrder": PendingExposureOrder,
        "DIRECT_BOOK_OWNERSHIP_VERSION": "test",
        "cancellation_identity_decision": cancellation_identity_decision,
        "ExchangeOrderIdentity": ExchangeOrderIdentity,
        "ABSENT_REPRICE_CANCEL": ABSENT_REPRICE_CANCEL,
    }
    exec(compile(ast.fix_missing_locations(ast.Module(body=methods, type_ignores=[])),
                 "<a1912>", "exec"), ns)
    return {n: ns[n] for n in WANTED}


class _Row:
    def __init__(self, book_id, side, client_order_id, quantity=0.25):
        self.book_id = book_id
        self.side = side
        self.client_order_id = client_order_id
        self.quantity = quantity


class _Agent:
    A19_CANCEL_MEMO_MAX = 2048

    def __init__(self):
        self._tick = 7
        self._pending = {}
        self._a191_reprice_release = {}
        self._identity_registry = {}
        self._direct_stale_cancels_ignored = 0
        self.events = []
        for n in ("_a191_ownership_releases", "_a191_ownership_release_blocked",
                  "_a191_ownership_release_ticks_total", "_a191_exchange_ack_ticks_total",
                  "_a191_exchange_acks", "_direct_identity_releases",
                  "_direct_release_mismatch_blocks"):
            setattr(self, n, 0)

    def _emit(self, event_type, force=False, **payload):
        self.events.append((event_type, payload))

    def _direct_exchange_identity_registry(self):
        return self._identity_registry

    def rows(self, name):
        return [p for e, p in self.events if e == name]


for _n, _f in _load().items():
    setattr(_Agent, _n, _f)


def _agent(client_id=None, reserved_side="sell", extra_reservation=False):
    a = _Agent()
    a._pending[(BOOK, "exit-1", reserved_side)] = _Row(BOOK, reserved_side, "exit-1")
    if extra_reservation:
        a._pending[(BOOK, "exit-2", reserved_side)] = _Row(BOOK, reserved_side, "exit-2")
    a._direct_pending_ledger = lambda: a._pending
    a._a191_reprice_release[OID] = {
        "book_id": BOOK, "side": "sell", "client_id": client_id, "tick": 6,
    }
    return a


# ---------------------------------------------------- the release now happens

def test_confirmed_cancel_releases_the_reservation_immediately():
    """THE regression test: this used to wait for LOCAL_EXPIRY."""
    a = _agent()
    assert a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK, success=True) is True
    assert a._pending == {}
    assert a._a191_ownership_releases == 1


def test_release_reports_the_exchange_clock():
    a = _agent()
    a._tick = 7                                   # cancelled at 6, confirmed at 7
    a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK, success=True)
    ack = a.rows("A19_CANCEL_ACK")[0]
    assert ack["exchange_ack_ticks"] == 1
    assert ack["release_path"] == "A1912_IDENTITY"
    assert ack["cancel_reason"] == ABSENT_REPRICE_CANCEL


def test_exact_client_id_is_used_when_the_notice_carries_one():
    a = _agent(client_id="exit-1")
    assert a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK, success=True) is True
    assert a._pending == {}


def test_release_is_idempotent():
    a = _agent()
    assert a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK, success=True) is True
    assert a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK, success=True) is False
    assert a._a191_ownership_releases == 1


# ------------------------------------------------ the safety rule still holds

def test_unrelated_cancel_releases_nothing():
    """A cancellation A1.9 did not issue must remain a stale cancel."""
    a = _agent()
    assert a._a191_release_reprice_ownership(
        exchange_order_id=11111, notice_book_id=BOOK, success=True) is False
    assert len(a._pending) == 1
    assert a._a191_ownership_releases == 0


def test_failed_cancel_releases_nothing():
    """A failed cancellation is not proof the order is gone."""
    a = _agent()
    assert a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK, success=False) is False
    assert len(a._pending) == 1


def test_book_mismatch_releases_nothing():
    a = _agent()
    assert a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK + 1, success=True) is False
    assert len(a._pending) == 1
    assert a.rows("A17432_STALE_CANCEL_IGNORED") == []
    blocked = [p for e, p in a.events if e == "A1912_REPRICE_RELEASE_BLOCKED"]
    assert blocked[0]["reason"] == "CANCEL_BOOK_ID_MISMATCH"


def test_ambiguous_reservation_is_refused_not_guessed():
    """Two candidate reservations on the same book/side: never guess."""
    a = _agent(extra_reservation=True)
    assert a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK, success=True) is False
    assert len(a._pending) == 2
    blocked = [p for e, p in a.events if e == "A1912_REPRICE_RELEASE_BLOCKED"]
    assert blocked[0]["reason"] == "NO_UNIQUE_PENDING_RESERVATION"
    assert a._a191_ownership_release_blocked == 1


def test_wrong_side_reservation_is_not_released():
    a = _agent(reserved_side="buy")               # cancelled order was a sell
    assert a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK, success=True) is False
    assert len(a._pending) == 1


def test_no_reservation_at_all_is_not_an_error():
    a = _agent()
    a._pending.clear()
    assert a._a191_release_reprice_ownership(
        exchange_order_id=OID, notice_book_id=BOOK, success=True) is False
    assert a._a191_ownership_release_blocked == 1


def test_stats_expose_the_release_path():
    for key in (
        "direct_a191_ownership_releases", "direct_a191_ownership_release_blocked",
        "direct_a191_exchange_acks", "direct_a191_mean_exchange_ack_ticks",
        "direct_a191_pending_reprice_releases",
    ):
        assert f'stats["{key}"]' in SRC, key


# ------------------------------------------- the notice path reaches the fix

class _Cancellation:
    def __init__(self, order_id, success=True):
        self.orderId = order_id
        self.success = success


class _Notice:
    def __init__(self, book_id, cancellations):
        self.bookId = book_id
        self.cancellations = cancellations


def test_cancellation_notice_releases_an_a19_reprice_order():
    """End-to-end through the real notice handler, not the helper alone.

    A1.9.1 shipped a correct-looking mechanism that was never reached. The
    helper being right is not evidence the notice path calls it.
    """
    a = _agent()
    a._direct_note_cancellation_identity_notice(
        _Notice(BOOK, [_Cancellation(OID, success=True)]), phase="TEST",
    )
    assert a._pending == {}
    assert a._a191_ownership_releases == 1
    assert a._direct_stale_cancels_ignored == 0


def test_unknown_cancellation_is_still_ignored_as_stale():
    a = _agent()
    a._direct_note_cancellation_identity_notice(
        _Notice(BOOK, [_Cancellation(424242, success=True)]), phase="TEST",
    )
    assert len(a._pending) == 1
    assert a._direct_stale_cancels_ignored == 1
    assert a.rows("A17432_STALE_CANCEL_IGNORED")[0]["reason"] == "UNKNOWN_EXCHANGE_ORDER_ID"


def test_failed_cancellation_of_an_a19_order_is_ignored_as_stale():
    a = _agent()
    a._direct_note_cancellation_identity_notice(
        _Notice(BOOK, [_Cancellation(OID, success=False)]), phase="TEST",
    )
    assert len(a._pending) == 1
    assert a._a191_ownership_releases == 0
    assert a._direct_stale_cancels_ignored == 1
