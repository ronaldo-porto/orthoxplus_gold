"""v6.0.3: the partial-fill recovery releases a short lot once its bounded hold is gone (D1).

Measured on UID 68 (v6.0.2 at 8 books, testnet, ticks 0 to 5,123, 2026-09-17): 19 short-lot
episodes, 9 of them longer than 50 ticks, 121 ``A173_PARTIAL_REMAINDER_CANCEL`` rows on those
positions -- every one ``EXIT_COMPLETE`` with ``bound_order_id: null`` -- and 9 episodes that ended
in a forced exit.  The handler released a row only when the position was not pre-v6.0.0 dust
(``eps < |q| < min_order``), so a short lot kept its row, and with no bound order every order on
the book counted as conflicting: the v6.0.0 lot exit placed in the previous request was cancelled
at the next one.

These tests run the real ``_direct_service_partial_fill_recovery`` in a harness, not a copy of it.
"""
import ast
import textwrap
import typing
from pathlib import Path
from types import SimpleNamespace

import research_v600_short_lots as sl
import research_v603_recovery as rec
from research_direct_exit_refresh import ABSENT_PARTIAL_REMAINDER_CANCEL
from research_direct_liveness import is_dust_inventory, partition_bound_remainder_orders
from _harness import extractor

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()

MIN = 0.25
EPS = 0.00005  # half a base unit at 4 volume decimals
TWO_THIRDS = 2.0 / 3.0
BOUNDARY = TWO_THIRDS * MIN  # 0.16667

# Book 107's episode, as the v6.0.2 order stream shows it.
BOOK_107_NET = 0.1996
BOOK_107_BOUND = 946119


_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


METHODS = [
    # the method under test
    "_direct_service_partial_fill_recovery",
    # v6.0.3
    "_v603_on", "_v603_count", "_v603_is_short_lot", "_v603_disposition",
    "_v603_note_release", "_v603_note_cancel", "_v603_open_short_lot_rows", "_v603_telemetry",
    # the v6.0.0 predicate it asks
    "_v600_on", "_v600_tolerance", "_v600_classify_qty", "_v600_is_inherited_parked",
    "_v600_counts_as_dust", "_v600_count",
]


class _Response:
    """Just enough of the publisher to record what the handler sent."""

    def __init__(self):
        self.cancels = []

    def cancel_orders(self, *, book_id, order_ids, delay=0):
        self.cancels.append((int(book_id), [int(o) for o in order_ids]))


class _Base:
    max_instructions_per_book = 4
    _research_exchange_min_order_size = MIN
    _research_volume_decimals = 4
    _a1961_base_decimals = 4

    def _execution_flat_epsilon(self):
        return EPS

    def _count_book_instructions(self, response, book_id):
        return sum(1 for bid, _ids in response.cancels if bid == int(book_id))


def _agent(*, v603=True, v600=True, tick=3000):
    body = "".join(textwrap.indent(textwrap.dedent(_method_source(n)), "    ") + "\n" for n in METHODS)
    scope = {
        "_Base": _Base, "Any": typing.Any,
        "ABSENT_PARTIAL_REMAINDER_CANCEL": ABSENT_PARTIAL_REMAINDER_CANCEL,
        "direct_is_dust_inventory": is_dust_inventory,
        "direct_partition_bound_remainder_orders": partition_bound_remainder_orders,
        "V603_DISPOSITION_HOLD": rec.DISPOSITION_HOLD,
        "V603_DISPOSITION_LEGACY": rec.DISPOSITION_LEGACY,
        "V603_DISPOSITION_RELEASE": rec.DISPOSITION_RELEASE,
        "V603_RECOVERY_VERSION": rec.V603_RECOVERY_VERSION,
        "V603_STATE_EVERY_TICKS": rec.V603_STATE_EVERY_TICKS,
        "v603_row_disposition": rec.row_disposition,
        "V600_CLASS_DUST": sl.CLASS_DUST, "V600_CLASS_FLAT": sl.CLASS_FLAT,
        "V600_CLASS_PARKED": sl.CLASS_PARKED, "V600_CLASS_SHORT_LOT": sl.CLASS_SHORT_LOT,
        "V600_SHORT_LOT_FRACTION_DEFAULT": sl.V600_SHORT_LOT_FRACTION_DEFAULT,
        "v600_classify_position": sl.classify_position,
        "v600_leftover_tolerance": sl.leftover_tolerance,
        "v600_short_lot_boundary": sl.short_lot_boundary,
    }
    exec("from __future__ import annotations\nclass Harness(_Base):\n" + body, scope)
    agent = scope["Harness"]()
    agent.research_v603_short_lot_release = v603
    agent.research_v600_short_lots = v600
    agent.research_v600_short_lot_min_fraction = TWO_THIRDS
    agent._v600_inherited_parked = {}
    agent._v600_counts = {}
    agent._v603_counts = {}
    agent._v603_last = {}
    agent._v603_cancels_reported = 0
    agent._v603_state_reported = False
    agent._v603_errors = 0
    agent._direct_v603_short_lot_releases = 0
    agent._direct_partial_recovery = {}
    agent._direct_partial_hold_releases = 0
    agent._direct_partial_wrong_side_cancels = 0
    agent._direct_partial_hold_live = 0
    agent._direct_partial_bound_holds = 0
    agent._direct_partial_bound_pending = 0
    agent._research_partial_fill_hold_quoted = 0
    agent._tick = int(tick)
    agent.rows = []
    agent.holds = {}
    agent.net = {}
    agent.orders = {}
    agent.exit_cancels = []
    agent._emit = lambda kind, **kw: agent.rows.append(dict(kw, type=kind))
    agent._direct_signed_inventory = lambda bid: float(agent.net.get(int(bid), 0.0))
    agent._direct_partial_hold_active = lambda bid, state: bool(agent.holds.get(int(bid), False))
    agent._direct_account_orders = lambda bid: list(agent.orders.get(int(bid), []))
    agent._direct_order_side_price = lambda o: (o.side, o.price)
    agent._a19_note_exit_cancel = lambda bid, ids, reason: agent.exit_cancels.append(
        (int(bid), list(ids), reason)
    )
    return agent


def _order(oid, side="sell", price=100.0, quantity=MIN):
    return SimpleNamespace(id=int(oid), side=side, price=float(price), quantity=float(quantity))


def _episode(agent, *, book=107, net=BOOK_107_NET, mode="EXIT_COMPLETE", hold=False,
             bound_order_id=BOOK_107_BOUND, live_order_ids=(946279,), side="sell"):
    """Book 107 at the moment the v6.0.0 lot exit is resting and the hold has expired."""
    agent.net[book] = float(net)
    agent.holds[book] = bool(hold)
    agent.orders[book] = [_order(oid, side=side) for oid in live_order_ids]
    agent._direct_partial_recovery[book] = {
        "mode": mode, "desired_side": side, "bound_order_id": bound_order_id,
        "target_inventory": 0.0, "preserve_existing_remainder": True,
    }
    return agent


def _rows(agent, kind):
    return [r for r in agent.rows if r.get("type") == kind]


def _run(agent):
    response = _Response()
    instructions, holds = agent._direct_service_partial_fill_recovery(response, SimpleNamespace())
    return response, instructions, holds


# ---- T1 book 107 replay -----------------------------------------------------------------------------

def test_t1_short_lot_with_the_hold_expired_is_released_and_nothing_is_cancelled():
    agent = _episode(_agent())
    response, instructions, holds = _run(agent)

    assert response.cancels == [], "the v6.0.0 lot exit must survive"
    assert (instructions, holds) == (0, 0)
    assert 107 not in agent._direct_partial_recovery, "the row must be released"
    assert agent._direct_v603_short_lot_releases == 1
    assert _rows(agent, "A173_PARTIAL_REMAINDER_CANCEL") == []
    released = _rows(agent, "V603_PARTIAL_RELEASE")
    assert len(released) == 1
    assert released[0]["book"] == 107 and released[0]["mode"] == "EXIT_COMPLETE"
    assert released[0]["hold_expired"] == 1 and released[0]["cancelled_orders"] == 0
    assert abs(released[0]["net_base"] - BOOK_107_NET) < 1e-12


def test_t1_off_restores_the_v6_0_2_cancel():
    agent = _episode(_agent(v603=False))
    response, instructions, _holds = _run(agent)

    assert response.cancels == [(107, [946279])]
    assert instructions == 1
    assert 107 in agent._direct_partial_recovery, "v6.0.2 keeps the row"
    cancels = _rows(agent, "A173_PARTIAL_REMAINDER_CANCEL")
    assert len(cancels) == 1 and cancels[0]["bound_order_id"] is None
    assert cancels[0]["v603_disposition"] == rec.DISPOSITION_LEGACY
    assert agent._v603_counts.get("short_lot_cancels") == 1, "the R1 counter reads the defect"
    assert agent._direct_v603_short_lot_releases == 0


def test_t1_the_loop_stops_after_one_request():
    """v6.0.2 cancelled a fresh exit every request; v6.0.3 owns nothing after the first."""
    agent = _episode(_agent())
    _run(agent)
    agent.orders[107] = [_order(946426)]  # the next lot exit, five ticks later
    agent._tick += 5
    response, instructions, _holds = _run(agent)
    assert response.cancels == [] and instructions == 0

    legacy = _episode(_agent(v603=False))
    for i, oid in enumerate((946279, 946426, 946569)):
        legacy.orders[107] = [_order(oid)]
        legacy._tick += 5 * i
        _run(legacy)
    assert len(_rows(legacy, "A173_PARTIAL_REMAINDER_CANCEL")) == 3


def test_t1_every_stuck_book_from_the_run_is_released():
    # book -> the short lot it carried (v6.0.2 run, episodes over 50 ticks).
    stuck = {107: 0.1996, 127: 0.1985, 74: 0.1996, 119: 0.2002, 46: 0.2002, 85: 0.1998, 7: 0.2002}
    agent = _agent()
    for book, net in stuck.items():
        _episode(agent, book=book, net=net, live_order_ids=(900000 + book,))
    response, instructions, holds = _run(agent)
    assert response.cancels == [] and (instructions, holds) == (0, 0)
    assert agent._direct_partial_recovery == {}
    assert agent._direct_v603_short_lot_releases == len(stuck)


# ---- T2 the bound hold is unchanged -----------------------------------------------------------------

def test_t2_active_hold_keeps_the_bound_order_and_cancels_the_others():
    agent = _episode(_agent(), hold=True, live_order_ids=(BOOK_107_BOUND, 946279))
    response, instructions, holds = _run(agent)

    assert response.cancels == [(107, [946279])], "only the unbound order goes"
    assert (instructions, holds) == (1, 1)
    assert 107 in agent._direct_partial_recovery, "the hold keeps its row"
    assert agent._direct_v603_short_lot_releases == 0
    held = _rows(agent, "A173_PARTIAL_REMAINDER_HOLD")
    assert len(held) == 1 and held[0]["bound_order_id"] == BOOK_107_BOUND
    cancels = _rows(agent, "A173_PARTIAL_REMAINDER_CANCEL")
    assert cancels[0]["v603_disposition"] == rec.DISPOSITION_HOLD
    assert "short_lot_cancels" not in agent._v603_counts, "a bound cancel is not the R1 defect"


def test_t2_hold_with_no_live_remainder_still_blocks_replacement():
    agent = _episode(_agent(), hold=True, live_order_ids=())
    _response, instructions, holds = _run(agent)
    assert (instructions, holds) == (0, 1)
    assert len(_rows(agent, "A1731_PARTIAL_REMAINDER_PENDING")) == 1
    assert 107 in agent._direct_partial_recovery


def test_t2_an_active_hold_without_a_bound_id_is_not_a_hold():
    """The spec's 'inactive or unbound': only a named order can be protected."""
    agent = _episode(_agent(), hold=True, bound_order_id=None)
    response, _instructions, holds = _run(agent)
    assert response.cancels == [] and holds == 0
    assert agent._direct_v603_short_lot_releases == 1
    assert _rows(agent, "V603_PARTIAL_RELEASE")[0]["hold_expired"] == 0


# ---- T3 dust is unchanged ---------------------------------------------------------------------------

def test_t3_dust_keeps_the_v6_0_2_path_including_the_normalize_cancel():
    for net in (0.0504, 0.1249, 0.1501, 0.1666):
        assert net + 1e-12 < BOUNDARY, net
        agent = _episode(_agent(), net=net, mode="NORMALIZE", bound_order_id=None,
                         live_order_ids=(91107,))
        response, instructions, _holds = _run(agent)
        assert response.cancels == [(107, [91107])], net
        assert instructions == 1 and 107 in agent._direct_partial_recovery, net
        assert agent._direct_v603_short_lot_releases == 0, net
        cancels = _rows(agent, "A173_PARTIAL_REMAINDER_CANCEL")
        assert cancels[0]["v603_disposition"] == rec.DISPOSITION_LEGACY, net
        assert "short_lot_cancels" not in agent._v603_counts, net


def test_t3_the_boundary_is_the_v6_0_0_one():
    agent = _agent()
    assert agent._v603_is_short_lot(1, BOUNDARY, eps=EPS, min_order=MIN)
    assert not agent._v603_is_short_lot(1, BOUNDARY - 1e-6, eps=EPS, min_order=MIN)
    # v6.0.0 off: nothing under a lot is a short lot, so v6.0.3 releases nothing.
    off = _agent(v600=False)
    assert not off._v603_is_short_lot(1, BOOK_107_NET, eps=EPS, min_order=MIN)
    _episode(off)
    response, _instructions, _holds = _run(off)
    assert response.cancels == [(107, [946279])]


# ---- T4 full lots and parked lots --------------------------------------------------------------------

def test_t4_a_full_lot_is_released_by_the_frozen_path_before_v6_0_3_sees_it():
    agent = _episode(_agent(), net=0.25)
    response, instructions, holds = _run(agent)
    assert response.cancels == [] and (instructions, holds) == (0, 0)
    assert 107 not in agent._direct_partial_recovery
    assert agent._direct_partial_hold_releases == 1
    assert agent._direct_v603_short_lot_releases == 0, "not a v6.0.3 release"
    assert _rows(agent, "V603_PARTIAL_RELEASE") == []


def test_t4_a_flat_book_is_released_the_same_way():
    agent = _episode(_agent(), net=0.0)
    response, _instructions, _holds = _run(agent)
    assert response.cancels == [] and 107 not in agent._direct_partial_recovery
    assert agent._direct_v603_short_lot_releases == 0


def test_t4_a_parked_inherited_lot_counts_as_dust_and_is_untouched_by_v6_0_3():
    agent = _agent()
    agent._v600_inherited_parked = {108: 0.2469}
    _episode(agent, book=108, net=0.2469, mode="ENTRY_COMPLETE", bound_order_id=None,
             live_order_ids=(946700,))
    response, instructions, _holds = _run(agent)
    assert response.cancels == [(108, [946700])], "a parked lot keeps the v6.0.2 path"
    assert instructions == 1 and 108 in agent._direct_partial_recovery
    assert agent._direct_v603_short_lot_releases == 0
    assert "short_lot_cancels" not in agent._v603_counts


def test_t4_a_short_sell_side_lot_releases_too():
    agent = _episode(_agent(), net=-0.1996, side="buy")
    response, _instructions, _holds = _run(agent)
    assert response.cancels == [] and agent._direct_v603_short_lot_releases == 1
    assert _rows(agent, "V603_PARTIAL_RELEASE")[0]["desired_side"] == "buy"


# ---- T5 telemetry, switch, launcher -------------------------------------------------------------------

def test_t5_state_row_reports_releases_open_rows_and_cancels():
    agent = _agent(tick=100)
    _episode(agent)
    _episode(agent, book=74, net=0.1996, live_order_ids=(900074,))
    _run(agent)
    agent._v603_telemetry(SimpleNamespace())
    state = _rows(agent, "V603_STATE")[-1]
    assert state["enabled"] == 1 and state["releases"] == 2
    assert state["short_lot_rows_open"] == 0, "no row may own a short lot outside a live hold"
    assert state["short_lot_cancels_since"] == 0 and state["short_lot_cancels_total"] == 0
    assert state["errors"] == 0
    assert state["v603_recovery_version"] == rec.V603_RECOVERY_VERSION
    assert state["last_release"]["book"] in (107, 74)


def test_t5_state_row_shows_the_defect_when_the_switch_is_off():
    agent = _agent(tick=100, v603=False)
    _episode(agent)
    _run(agent)
    agent._v603_telemetry(SimpleNamespace())
    first = _rows(agent, "V603_STATE")[-1]
    assert first["enabled"] == 0 and first["releases"] == 0
    assert first["short_lot_rows_open"] == 1 and first["short_lot_cancels_total"] == 1
    agent._tick = 200
    agent.orders[107] = [_order(946426)]
    _run(agent)
    agent._v603_telemetry(SimpleNamespace())
    second = _rows(agent, "V603_STATE")[-1]
    assert second["short_lot_cancels_since"] == 1 and second["short_lot_cancels_total"] == 2


def test_t5_a_broken_predicate_falls_back_to_v6_0_2_and_counts_the_error():
    agent = _episode(_agent())

    def boom(*a, **kw):
        raise RuntimeError("predicate")

    agent._v600_counts_as_dust = boom
    response, instructions, _holds = _run(agent)
    assert response.cancels == [(107, [946279])], "a failure must not change behaviour"
    assert instructions == 1 and agent._v603_errors >= 1


def test_t5_disposition_table():
    kw = dict(enabled=True, counts_as_dust=False)
    assert rec.row_disposition(hold_active=True, bound=True, **kw) == rec.DISPOSITION_HOLD
    assert rec.row_disposition(hold_active=True, bound=False, **kw) == rec.DISPOSITION_RELEASE
    assert rec.row_disposition(hold_active=False, bound=False, **kw) == rec.DISPOSITION_RELEASE
    assert rec.row_disposition(enabled=False, hold_active=False, bound=False,
                               counts_as_dust=False) == rec.DISPOSITION_LEGACY
    assert rec.row_disposition(enabled=True, hold_active=False, bound=False,
                               counts_as_dust=True) == rec.DISPOSITION_LEGACY
    assert rec.release_is_safe(disposition=rec.DISPOSITION_RELEASE, cancelled_orders=0)
    assert not rec.release_is_safe(disposition=rec.DISPOSITION_RELEASE, cancelled_orders=1)
    assert rec.release_is_safe(disposition=rec.DISPOSITION_LEGACY, cancelled_orders=1)


def test_t5_source_wiring():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_10"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_10"' in SIMPLE
    assert "if disposition == V603_DISPOSITION_RELEASE:" in SIMPLE
    assert 'getattr(self.config, "research_v603_short_lot_release", True)' in SIMPLE
    assert '"direct_v603_short_lot_releases": 0,' in SIMPLE
    # The release must precede the cancel: a release that cancels is the defect itself.
    body = SIMPLE[SIMPLE.index("def _direct_service_partial_fill_recovery"):
                  SIMPLE.index("def _direct_place_dust_normalizer")]
    assert body.index("if disposition == V603_DISPOSITION_RELEASE:") < body.index("response.cancel_orders(")
    # The frozen modules stay frozen.
    liveness = (STRATEGY / "research_direct_liveness.py").read_text()
    assert "def is_dust_inventory(qty: float, *, min_order: float, eps: float) -> bool:" in liveness
    assert "v603" not in liveness.lower()


def test_t5_launcher():
    assert ("  strategy1_direct_v6_0_3) A19X_BUILD=1; A195_BUILD=1; A196_BUILD=1; A1961_BUILD=1; A197_BUILD=1; "
            "A198_BUILD=1; A199_BUILD=1; A1991_BUILD=1; A1992_BUILD=1; V500_BUILD=1; V501_BUILD=1; "
            "V502_BUILD=1; V503_BUILD=1; V504_BUILD=1; V600_BUILD=1; V601_BUILD=1; V602_BUILD=1; "
            "V603_BUILD=1 ;;") in LAUNCHER
    assert "  strategy1_direct_v6_0_2) A19X_BUILD=1" in LAUNCHER, "earlier arms stay"
    # v6.1 layers on top: its own arm sets every v6.0.3 flag and V610_BUILD as well.
    assert "  strategy1_direct_v6_1_0) A19X_BUILD=1" in LAUNCHER
    assert "V603_BUILD=1; V610_BUILD=1 ;;" in LAUNCHER
    assert "research_v603_short_lot_release=1" in LAUNCHER
    assert '[[ "$V603_BUILD" == "1" ]]; then' in LAUNCHER
    assert "[preflight] v6.0.3 short-lot release PASS" in LAUNCHER
    assert "tests/test_research_v6_0_3_short_lot_release.py" in LAUNCHER
    # v6.0.3 keeps the v6.0.2 cap setting, so the 8-book run continues unchanged.
    assert 'MAX_ACTIVE_BOOKS="${MAX_ACTIVE_BOOKS:-8}"' in LAUNCHER
