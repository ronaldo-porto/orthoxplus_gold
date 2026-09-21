"""A1.9.7: post-fill protection (P1), order-side identity (P2), exit-gap telemetry (P3).

Measured on the A1.9.6.1 run, log 20260913_053425, ticks 1-8,000: 579 of 600
round trips were first evaluated two or more ticks after their opening fill.
"""
import ast
import textwrap
import typing
from pathlib import Path
from types import SimpleNamespace

import research_direct_postfill_protection as pp
from research_direct_book_ownership import (
    DIRECT_BOOK_OWNERSHIP_VERSION,
    ExchangeOrderIdentity,
    canonical_order_side,
    reduce_identity_quantity,
    register_exchange_identity,
)
from research_direct_exit_refresh import ABSENT_ENTRY_QUOTE_CANCEL
from research_direct_inflight_reservation import PendingExposureOrder, reduce_pending_quantity
from research_direct_postfill_protection import (
    A197_POSTFILL_PROTECTION_VERSION,
    SKIP_INVENTORY_OPENED,
    SKIP_LIVE_ORDER,
    book_cancelled_order_ids,
    book_instruction_kinds,
    close_gap,
    note_gap_skip,
    open_gap,
    position_mark_bps,
    postfill_protect_eligible,
    sign_flipped,
    strip_book_limit_orders,
)
from _harness import extractor

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_direct_postfill_protection.py").read_text()
OWNERSHIP = (STRATEGY / "research_direct_book_ownership.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()


_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


class _Base:
    def __init__(self):
        self.rows = []

    def _emit(self, event_type, force=False, **payload):
        self.rows.append((event_type, payload))

    def _execution_flat_epsilon(self):
        return 1e-9

    def rows_of(self, event_type):
        return [payload for kind, payload in self.rows if kind == event_type]


def _harness(*names):
    namespace = {name: getattr(pp, name) for name in dir(pp) if not name.startswith("__")}
    namespace.update(
        Any=typing.Any,
        ABSENT_ENTRY_QUOTE_CANCEL=ABSENT_ENTRY_QUOTE_CANCEL,
        DIRECT_BOOK_OWNERSHIP_VERSION=DIRECT_BOOK_OWNERSHIP_VERSION,
        ExchangeOrderIdentity=ExchangeOrderIdentity,
        PendingExposureOrder=PendingExposureOrder,
        canonical_order_side=canonical_order_side,
        reduce_identity_quantity=reduce_identity_quantity,
        reduce_pending_quantity=reduce_pending_quantity,
    )
    attrs = {}
    for name in names:
        local = dict(namespace)
        exec("from __future__ import annotations\n" + textwrap.dedent(_method_source(name)), local)
        attrs[name] = local[name]
    return type("Harness", (_Base,), attrs)


# ---- P2: a buy is side 0 -----------------------------------------------------------

def test_side_zero_is_a_buy():
    assert canonical_order_side(0) == "buy"
    assert canonical_order_side(1) == "sell"
    assert canonical_order_side("0") == "buy"
    assert canonical_order_side("OrderDirection.SELL") == "sell"
    assert canonical_order_side(None) == ""
    assert canonical_order_side("") == ""


def test_a_buy_identity_from_a_placement_notice_matches_its_reservation():
    registry = {}
    identity = register_exchange_identity(
        registry, exchange_order_id=1462469, book_id=6, client_order_id=70061, side=0,
        remaining_quantity=0.25,
    )
    reservation = PendingExposureOrder(
        book_id=6, side=canonical_order_side("buy"), quantity=0.25, client_order_id=70061,
        submitted_tick=4973, submitted_timestamp_ns=1, expiry_period_ns=3_000_000_000,
        order_kind="PLACE_ORDER_LIMIT",
    )
    assert identity.pending_key() == reservation.key()


def _note_fill_agent():
    harness = _harness(
        "_direct_pending_note_fill", "_direct_pending_ledger", "_direct_exchange_identity_registry",
        "_direct_emit_book_ownership_release", "_direct_emit_identity_diag",
    )
    agent = harness()
    agent.uid = 67
    agent._tick = 4973
    return agent


def _reserve(agent, *, book, client, side):
    row = PendingExposureOrder(
        book_id=book, side=canonical_order_side(side), quantity=0.25, client_order_id=client,
        submitted_tick=4973, submitted_timestamp_ns=1, expiry_period_ns=3_000_000_000,
        order_kind="PLACE_ORDER_LIMIT",
    )
    agent._direct_pending_ledger()[row.key()] = row


def _maker_fill(*, book, order, client):
    return SimpleNamespace(bookId=book, quantity=0.25, makerAgentId=67, makerOrderId=order,
                           takerAgentId=5, takerOrderId=9, clientOrderId=client)


def test_book_6_buy_fill_releases_on_the_fill_instead_of_local_expiry():
    """Book 6, tick 4,973: buy 70061 filled and its reservation lived to LOCAL_EXPIRY."""
    agent = _note_fill_agent()
    _reserve(agent, book=6, client=70061, side="buy")
    register_exchange_identity(
        agent._direct_exchange_identity_registry(), exchange_order_id=1462469, book_id=6,
        client_order_id=70061, side=0, remaining_quantity=0.25,
    )
    agent._direct_pending_note_fill(_maker_fill(book=6, order=1462469, client=70061))
    assert agent._direct_pending_ledger() == {}
    assert [r["reason"] for r in agent.rows_of("A17431_BOOK_OWNERSHIP_RELEASE")] == ["FILL_COMPLETE_EXACT"]


def test_the_side_less_buy_identity_is_the_live_defect():
    """Registered the way the old helper did it: the fill finds the identity and releases nothing."""
    agent = _note_fill_agent()
    _reserve(agent, book=6, client=70061, side="buy")
    agent._direct_exchange_identity_registry()[1462469] = ExchangeOrderIdentity(
        exchange_order_id=1462469, book_id=6, client_order_id="70061", side="", remaining_quantity=0.25,
    )
    agent._direct_pending_note_fill(_maker_fill(book=6, order=1462469, client=70061))
    assert len(agent._direct_pending_ledger()) == 1
    assert agent.rows_of("A17431_BOOK_OWNERSHIP_RELEASE") == []


def test_a_sell_fill_still_releases_on_the_fill():
    agent = _note_fill_agent()
    _reserve(agent, book=81, client=70812, side="sell")
    register_exchange_identity(
        agent._direct_exchange_identity_registry(), exchange_order_id=1349010, book_id=81,
        client_order_id=70812, side=1, remaining_quantity=0.25,
    )
    agent._direct_pending_note_fill(_maker_fill(book=81, order=1349010, client=70812))
    assert agent._direct_pending_ledger() == {}


# ---- P1: which positions the post-fill tick may evaluate -------------------------------

def test_mid_mark_for_long_short_and_unpriced():
    assert abs(position_mark_bps(net_base=0.25, vwap_entry=370.26, mid=368.26) - (-54.016)) < 1e-2
    assert abs(position_mark_bps(net_base=-0.25, vwap_entry=276.01, mid=283.2) - (-260.498)) < 1e-2
    assert position_mark_bps(net_base=0.25, vwap_entry=None, mid=100.0, fallback=-12.0) == -12.0
    assert position_mark_bps(net_base=0.0, vwap_entry=100.0, mid=90.0) is None


def test_only_the_absolute_band_is_eligible():
    assert postfill_protect_eligible(-260.5)        # book 81 at tick 103
    assert postfill_protect_eligible(-25.01)
    assert not postfill_protect_eligible(-25.0)     # classify_risk_band: ABSOLUTE is < -25
    assert not postfill_protect_eligible(-20.0)     # HARD_ESCAPE cannot clip at age 1
    assert not postfill_protect_eligible(5.0)
    assert not postfill_protect_eligible(None)
    assert not postfill_protect_eligible(float("nan"))


def test_instruction_helpers_touch_only_the_book_and_the_new_tail():
    old = SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=81)
    rows = [
        old,
        SimpleNamespace(type="CANCEL_ORDERS", bookId=81, cancellations=[SimpleNamespace(orderId=1349009)]),
        SimpleNamespace(type="PLACE_ORDER_MARKET", bookId=81),
        SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=81),
        SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=7),
    ]
    assert book_instruction_kinds(rows, book_id=81, first_new=1) == {"CANCEL_ORDERS", "PLACE_ORDER_MARKET", "PLACE_ORDER_LIMIT"}
    assert book_cancelled_order_ids(rows, book_id=81, first_new=1) == {1349009}
    assert strip_book_limit_orders(rows, book_id=81, first_new=1) == 1
    assert [r.type for r in rows] == ["PLACE_ORDER_LIMIT", "CANCEL_ORDERS", "PLACE_ORDER_MARKET", "PLACE_ORDER_LIMIT"]
    assert rows[0] is old and rows[-1].bookId == 7


def test_sign_flip():
    assert sign_flipped(-0.25, 0.25, 1e-9)
    assert not sign_flipped(-0.25, 0.0, 1e-9)
    assert not sign_flipped(0.0, 0.25, 1e-9)


# ---- P3: the exit gap ----------------------------------------------------------------

def test_book_6_gap_reads_as_measured():
    records = {}
    open_gap(records, 6, fill_tick=4973)
    assert note_gap_skip(records, 6, tick=4974, reason=SKIP_INVENTORY_OPENED, mark_bps=-54.141)
    assert note_gap_skip(records, 6, tick=4975, reason=SKIP_LIVE_ORDER, mark_bps=-88.982)
    assert not note_gap_skip(records, 6, tick=4975, reason=SKIP_LIVE_ORDER, mark_bps=-88.982)
    assert note_gap_skip(records, 6, tick=4976, reason=SKIP_LIVE_ORDER, mark_bps=-127.873)
    row = close_gap(records, 6, eval_tick=4977, p1_acted=False)
    assert row["gap_ticks"] == 4 and row["skipped_ticks"] == 3
    assert row["skips"] == ["INVENTORY_OPENED", "LIVE_ORDER", "LIVE_ORDER"]
    assert row["mark_t1_bps"] == -54.1
    assert row["a197_postfill_protection_version"] == A197_POSTFILL_PROTECTION_VERSION
    assert records == {}
    assert close_gap(records, 6, eval_tick=4978, p1_acted=False) is None


def test_gap_records_are_bounded_and_skips_before_the_fill_do_not_count():
    records = {}
    for book in range(300):
        open_gap(records, book, fill_tick=10, max_records=256)
    assert len(records) == 256 and 0 not in records and 299 in records
    assert not note_gap_skip(records, 299, tick=10, reason=SKIP_LIVE_ORDER, mark_bps=None)
    for tick in range(11, 30):
        note_gap_skip(records, 299, tick=tick, reason=SKIP_LIVE_ORDER, mark_bps=-1.0, max_marks=8)
    row = close_gap(records, 299, eval_tick=30, p1_acted=False)
    assert row["skipped_ticks"] == 19 and len(row["marks_bps"]) == 8


# ---- P1 at runtime --------------------------------------------------------------------

BOOK_81 = SimpleNamespace(bids=[SimpleNamespace(price=283.1)], asks=[SimpleNamespace(price=283.3)])
INV_81 = SimpleNamespace(net_base=-0.25, vwap_entry=276.01, unrealized_bps=None)


def _cancel(book, ids):
    return SimpleNamespace(type="CANCEL_ORDERS", bookId=book,
                           cancellations=[SimpleNamespace(orderId=i) for i in ids])


def _postfill_agent(queue):
    harness = _harness(
        "_a197_manage_postfill", "_a197_gap_records", "_a197_mark", "_a197_close_gap",
        "_a197_note_gap_skip", "_a197_postfill_candidate", "_a197_postfill_enabled",
        "_a197_note_own_fill",
    )

    class Agent(harness):
        def __init__(self):
            super().__init__()
            self._tick = 103
            self.cancels = []
            self.registered = []
            self.flag_during_manage = "unset"

        def _direct_entry_quote_orders(self, book_id):
            return [SimpleNamespace(id=1349009)]

        def _manage_inventory(self, response, state, book_id, *rest):
            self.flag_during_manage = self._a197_postfill_book
            return queue(response, book_id)

        def _direct_cancel_entry_quotes(self, response, book_id, *, reason):
            self.cancels.append(reason)
            response.instructions.append(_cancel(book_id, [1349009]))
            return 1

        def _a19_note_exit_cancel(self, book_id, order_ids, disposition):
            self.registered.append((book_id, list(order_ids), disposition))

    return Agent()


def _run(agent):
    response = SimpleNamespace(instructions=[])
    n = agent._a197_manage_postfill(response, None, 81, BOOK_81, INV_81, None, None, None)
    return n, response


def test_book_81_taker_exit_on_the_post_fill_tick():
    def taker(response, book_id):
        response.instructions += [_cancel(book_id, [1349009]), SimpleNamespace(type="PLACE_ORDER_MARKET", bookId=book_id)]
        return 2

    agent = _postfill_agent(taker)
    agent._tick = 102
    agent._a197_note_own_fill(book_id=81, before=0.0, after=-0.25)
    agent._tick = 103
    n, response = _run(agent)
    assert n == 2 and len(response.instructions) == 2
    assert agent.flag_during_manage == 81 and agent._a197_postfill_book is None
    assert agent.cancels == []                                   # the exit's own cancel covered it
    assert agent.registered == [(81, [1349009], ABSENT_ENTRY_QUOTE_CANCEL)]
    assert agent._a197_postfill_watch == {81: 103}
    (row,) = agent.rows_of("A197_POSTFILL_PROTECT")
    assert row["market_queued"] == 1 and row["fallback_cancel"] == 0 and row["fill_tick"] == 102
    assert row["mark_bps"] < -25.0
    (gap,) = agent.rows_of("A197_EXIT_GAP")
    assert gap["gap_ticks"] == 1 and gap["p1_acted"] == 1 and gap["skipped_ticks"] == 0


def test_a_limit_placement_never_shares_the_response_with_the_cancel():
    def maker(response, book_id):
        response.instructions += [SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=book_id),
                                  SimpleNamespace(type="PLACE_ORDER_LIMIT", bookId=7)]
        return 1

    agent = _postfill_agent(maker)
    n, response = _run(agent)
    kinds = [(r.type, r.bookId) for r in response.instructions]
    assert ("PLACE_ORDER_LIMIT", 81) not in kinds and ("PLACE_ORDER_LIMIT", 7) in kinds
    assert agent.cancels == ["INVENTORY_OPENED"] and agent.registered == []
    assert getattr(agent, "_a197_postfill_watch", {}) == {}      # no market order, nothing to watch
    (row,) = agent.rows_of("A197_POSTFILL_PROTECT")
    assert row["limits_stripped"] == 1 and row["market_queued"] == 0 and n == 1


def test_no_decision_still_cancels_the_entry_quotes():
    agent = _postfill_agent(lambda response, book_id: 0)
    n, _ = _run(agent)
    assert agent.cancels == ["INVENTORY_OPENED"] and n == 1


def test_the_post_fill_flag_is_cleared_when_management_raises():
    def boom(response, book_id):
        raise RuntimeError("frozen path failed")

    agent = _postfill_agent(boom)
    try:
        _run(agent)
    except RuntimeError:
        pass
    assert agent._a197_postfill_book is None


def test_the_switch_disables_post_fill_protection():
    agent = _postfill_agent(lambda response, book_id: 0)
    assert agent._a197_postfill_candidate(81, INV_81, 283.2)
    agent.research_a197_postfill_protect = False
    assert not agent._a197_postfill_candidate(81, INV_81, 283.2)


def test_a_protective_exit_that_reverses_the_position_is_counted():
    agent = _postfill_agent(lambda response, book_id: 0)
    agent._a197_postfill_watch = {81: 103}
    agent._tick = 104
    agent._a197_note_own_fill(book_id=81, before=-0.25, after=0.25)
    assert agent._a197_postfill_sign_flips == 1 and agent._a197_postfill_watch == {}
    (row,) = agent.rows_of("A197_POSTFILL_FLIP")
    assert row["protect_tick"] == 103


def test_a_flat_exit_ends_the_watch_and_the_gap():
    agent = _postfill_agent(lambda response, book_id: 0)
    agent._a197_postfill_watch = {81: 103}
    agent._a197_exit_gap = {81: pp.ExitGap(fill_tick=102)}
    agent._tick = 104
    agent._a197_note_own_fill(book_id=81, before=-0.25, after=0.0)
    assert agent._a197_postfill_watch == {} and agent._a197_exit_gap == {}
    assert getattr(agent, "_a197_postfill_sign_flips", 0) == 0


# ---- wiring ---------------------------------------------------------------------------

def test_inventory_loop_evaluates_absolute_books_instead_of_skipping_them():
    build = _method_source("build_mm_strategy_instructions")
    candidate = build.index("a197_postfill = self._a197_postfill_candidate(book_id, inventory, mid)")
    assert candidate < build.index('n_cancel = self._direct_cancel_entry_quotes(')
    assert "if not a197_postfill and self._direct_book_has_live_order(book_id):" in build
    assert build.index("manage_queue.append(") < build.index("a197_postfill_books.add(book_id)")
    managed = build.index("if book_id in a197_postfill_books:")
    assert managed < build.index("self._a197_manage_postfill(") < build.index("self._a197_close_gap(book_id, p1_acted=False)")
    assert "manage_queue[self.max_managed_books_per_tick:]" in build


def test_maker_exit_is_refused_first_on_the_post_fill_book():
    method = _method_source("_research_place_maker_exit")
    assert method.index('if getattr(self, "_a197_postfill_book", None) == int(book_id):') < method.index(
        "self._direct_partial_hold_active(")
    assert "return super()._research_place_maker_exit" in method


def test_fill_hook_and_flag_reset_are_wired():
    assert "self._a197_note_own_fill(book_id=int(book_id), before=float(before), after=float(after))" in _method_source(
        "_research_on_own_fill")
    method = _method_source("_a197_manage_postfill")
    assert "finally:\n            self._a197_postfill_book = None" in method
    assert "stripped = strip_book_limit_orders(" in method


def test_ownership_fix_is_in_the_shared_helper():
    assert 'token = "" if side is None else str(side).strip().lower()' in OWNERSHIP
    assert 'token = str(side or "").strip().lower()' not in OWNERSHIP


def test_version_and_launcher():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_10"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_10"' in SIMPLE
    assert A197_POSTFILL_PROTECTION_VERSION.endswith("a1_9_7")
    assert ("strategy1_direct_v4_16_2_a1_9_7) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; "
            "A1961_BUILD=1; A197_BUILD=1") in LAUNCHER
    start = LAUNCHER.index('PARAMS="')
    params = LAUNCHER[start:LAUNCHER.index('"', start + len('PARAMS="'))]
    assert "research_a197_postfill_protect=1" in params
    assert "tests/test_research_a1_9_7_postfill_protection.py" in LAUNCHER
    assert "[preflight] A1.9.7 post-fill protection / order-side identity / exit gap PASS" in LAUNCHER
