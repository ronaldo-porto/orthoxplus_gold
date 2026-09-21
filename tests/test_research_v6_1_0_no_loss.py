"""v6.1: no order may realize a negative FIFO PnL by the validator's arithmetic.

Measured on mainnet UID 34 (v6.0.2, log 20260917_214432, ticks 0-3,647, 2026-09-18):

* 349 ``A171_EXIT_DIAGNOSTIC`` rows in band ABSOLUTE_PROTECTION, every one selecting TAKER_EXIT,
  while the agent's own fee-inclusive ``maker_net_bps`` at those same evaluations read median
  +119.8 bps and was >= +1 bps on 90.8% of them.
* 149 ABSOLUTE round trips, 148 losing, realized median -39.4 bps; a >= +1 bps maker close was
  available on 128 of the 149.
* 315 ``A199_EXIT_AUTHORITY`` rows, all rule POSITIVE_MAKER, converting a maker at median
  +130.2 bps into a taker.
* After those exits the touch keeps moving away (t60 avoided_bps median +104, 91% > 0) and only
  10-11% of the lots ever see the entry price again within 900 s -- so the answer is not to wait
  at the entry price but to rest at the lot's fee-inclusive break-even, which on mainnet is ~88
  bps BELOW entry for a long (maker fee median -32.5 bps, entry rebate median +56 bps).

The cause is one arithmetic: ``classify_risk_band`` reads the A1.7.1 mid MTM, which excludes
fees, while ``match_trade_fifo`` charges both legs' fees at the close.

These tests run the real methods in a harness, not copies of them.
"""
import textwrap
import typing
from dataclasses import dataclass
from pathlib import Path

import pytest

import research_v61_lot_floor as lf
from _harness import extractor
from research_direct_exit import DIRECT_MAKER_EXIT_TARGET_BPS

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()

_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")

METHODS = [
    "_v61_on", "_v61_count", "_v61_price_decimals", "_v61_positions", "_v61_taker_verdict",
    "_v61_note_refusal", "_v61_compaction_price_ok", "_v61_floor_for", "_v61_rewrite_exit",
    "_v61_apply_floor", "_v61_lots_session_state", "_v61_lots_from_session",
    "_v61_restored_side", "_v61_telemetry",
    "_v62_on", "_v623_on", "_v623_count", "_v623_lifted", "_v623_release",   # v6.2.3, off here
]

# The mainnet shape: 0.25 BASE at 400, entered maker at a 56 bps rebate; live maker fee -32.5 bps,
# taker +46.9 bps (UID 34 medians).
Q, P0, OPEN_FEE = 0.25, 400.0, -0.56
MAKER_BPS, TAKER_BPS = -32.5, 46.9
BOOK, TS = 68, 74_046_000_000_000
# The -40 bps touch at which every one of the 149 ABSOLUTE takers fired.  A maker close here
# is still positive (two rebates); it is the +46.9 bps taker fee that turns it into a loss.
ABSOLUTE_TOUCH = 398.4
# Far enough through the break-even (~396.5 long, ~403.5 short) that a maker would lose too.
BELOW_FLOOR_LONG = 394.0
ABOVE_FLOOR_SHORT = 406.0


class _Level:
    def __init__(self, price):
        self.price = price


class _Book:
    def __init__(self, bid=ABSOLUTE_TOUCH, ask=None):
        self.bids = [_Level(bid)]
        self.asks = [_Level(ask if ask is not None else bid + 0.02)]


class _Inventory:
    def __init__(self, net_base=Q, vwap_entry=P0):
        self.net_base = net_base
        self.vwap_entry = vwap_entry


@dataclass
class _Decision:
    """The three fields of PositionExitDecision that the rewrite touches."""

    action: str
    selected_qty: float = Q
    reason: str = "ABSOLUTE_PROTECTION_REDUCE"


class _Config:
    priceDecimals = 2


class _State:
    config = _Config()


class _Base:
    _research_exchange_min_order_size = 0.25

    def _research_live_fee_bps(self, book_id, *, is_maker, fallback_bps=None):
        return MAKER_BPS if is_maker else TAKER_BPS

    def _v600_tolerance(self):
        return 1e-9

    def _v502_note_compaction_refusal(self, book_id):
        self.compaction_refusals.append(int(book_id))

    def _emit(self, event_type, force=False, **payload):
        self.rows.append((event_type, payload))


def _agent(*, v61=True, tick=3000, lots=((Q, P0, OPEN_FEE),), long=True):
    from dataclasses import replace as _replace

    body = "".join(
        textwrap.indent(textwrap.dedent(_method_source(n)), "    ") + "\n" for n in METHODS
    )
    scope = {
        "_Base": _Base, "Any": typing.Any, "replace": _replace,
        "ACTION_MAKER_EXIT": "MAKER_EXIT", "ACTION_TAKER_EXIT": "TAKER_EXIT",
        "DIRECT_MAKER_EXIT_TARGET_BPS": DIRECT_MAKER_EXIT_TARGET_BPS,
        "V61_LOT_FLOOR_VERSION": lf.V61_LOT_FLOOR_VERSION,
        "V61_STATE_EVERY_TICKS": lf.V61_STATE_EVERY_TICKS,
        "V61_REWRITE_NONE": lf.REWRITE_NONE,
        "v61_floor_price": lf.floor_price,
        "v61_floored_close_price": lf.floored_close_price,
        "v61_head_lot": lf.head_lot,
        "v61_fifo_close_net_bps": lf.fifo_close_net_bps,
        "v61_taker_close_pnl": lf.taker_close_pnl,
        "v61_rewrite_exit_action": lf.rewrite_exit_action,
    }
    exec("from __future__ import annotations\nclass Harness(_Base):\n" + body, scope)
    agent = scope["Harness"]()
    agent.research_v61_no_loss = v61
    agent.research_v623_premium_floor = False   # v6.2.3 has its own suite
    agent._tick = tick
    agent._v61_counts = {}
    agent._v61_last = {}
    agent._v61_state_reported = False
    agent._v61_restored_lots = {}
    agent._v61_errors = 0
    agent._direct_v61_taker_refusals = 0
    agent._direct_v61_compaction_refusals = 0
    agent.rows = []
    agent.compaction_refusals = []
    side = "longs" if long else "shorts"
    agent._open_positions = {
        BOOK: {"longs": [], "shorts": []},
    }
    for qty, price, fee in lots:
        agent._open_positions[BOOK][side].append((TS, qty, price, fee))
    return agent


def _rows(agent, name):
    return [p for t, p in agent.rows if t == name]


# ---- T1 the executor choke point ---------------------------------------------------------------

def test_t1_the_mainnet_absolute_taker_is_refused():
    agent = _agent()
    ok, detail = agent._v61_taker_verdict(BOOK, _Book(), Q, True)
    assert ok is False
    assert detail["fifo_pnl"] < 0 and detail["closed_qty"] == pytest.approx(Q)
    assert detail["taker_fee_bps"] == TAKER_BPS and detail["touch"] == ABSOLUTE_TOUCH


def test_t1_a_taker_that_realizes_a_gain_passes():
    agent = _agent()
    ok, detail = agent._v61_taker_verdict(BOOK, _Book(bid=399.95), Q, True)
    assert ok is True and detail["fifo_pnl"] >= 0


def test_t1_off_lets_the_loss_through():
    agent = _agent(v61=False)
    assert agent._v61_on() is False


def test_t1_a_flat_book_is_not_this_guards_question():
    agent = _agent(lots=())
    ok, _detail = agent._v61_taker_verdict(BOOK, _Book(), Q, True)
    assert ok is True


def test_t1_a_refusal_is_counted_and_emitted():
    agent = _agent()
    _ok, detail = agent._v61_taker_verdict(BOOK, _Book(), Q, True)
    agent._v61_note_refusal(BOOK, Q, True, detail)
    assert agent._direct_v61_taker_refusals == 1
    assert agent._v61_counts["taker_refused"] == 1
    row = _rows(agent, "V61_TAKER_REFUSED")
    assert len(row) == 1 and row[0]["book"] == BOOK and row[0]["long_position"] == 1
    assert row[0]["fifo_pnl"] < 0


# ---- T2 the exit rewrite -----------------------------------------------------------------------

def test_t2_a_loss_realizing_taker_becomes_a_floored_maker():
    agent = _agent()
    out, rewrite = agent._v61_rewrite_exit(
        BOOK, _Decision("TAKER_EXIT"), book=_Book(), inventory=_Inventory(),
        exit_kwargs={"inventory_qty": Q, "maker_net_bps": 119.8, "taker_net_bps": -42.0},
    )
    assert rewrite == lf.REWRITE_TAKER_TO_MAKER
    assert out.action == "MAKER_EXIT" and out.reason == "V61_FLOOR_MAKER"
    row = _rows(agent, "V61_EXIT_REWRITE")[0]
    assert row["from_action"] == "TAKER_EXIT" and row["taker_acceptable"] == 0
    assert row["floor_price"] < P0, "two rebates put the floor below the entry"


def test_t2_a_taker_the_validator_would_pay_for_stands():
    agent = _agent()
    out, rewrite = agent._v61_rewrite_exit(
        BOOK, _Decision("TAKER_EXIT"), book=_Book(bid=399.95), inventory=_Inventory(),
        exit_kwargs={"inventory_qty": Q},
    )
    assert rewrite == lf.REWRITE_NONE and out.action == "TAKER_EXIT"
    assert _rows(agent, "V61_EXIT_REWRITE") == []


@pytest.mark.parametrize("action", ["WAIT", "PARK"])
def test_t2_an_idle_full_lot_rests_the_floor(action):
    agent = _agent()
    out, rewrite = agent._v61_rewrite_exit(
        BOOK, _Decision(action, selected_qty=0.0, reason="A199_NOT_EXITING"),
        book=_Book(), inventory=_Inventory(), exit_kwargs={"inventory_qty": Q},
    )
    assert rewrite == lf.REWRITE_IDLE_TO_MAKER
    assert out.action == "MAKER_EXIT" and out.selected_qty == pytest.approx(Q)


def test_t2_dust_keeps_its_own_path():
    agent = _agent(lots=((0.1, P0, -0.22),))
    out, rewrite = agent._v61_rewrite_exit(
        BOOK, _Decision("WAIT"), book=_Book(), inventory=_Inventory(net_base=0.1),
        exit_kwargs={"inventory_qty": 0.1},
    )
    assert rewrite == lf.REWRITE_NONE and out.action == "WAIT"


def test_t2_a_maker_exit_is_never_rewritten():
    agent = _agent()
    out, rewrite = agent._v61_rewrite_exit(
        BOOK, _Decision("MAKER_EXIT"), book=_Book(), inventory=_Inventory(),
        exit_kwargs={"inventory_qty": Q},
    )
    assert rewrite == lf.REWRITE_NONE and out.action == "MAKER_EXIT"


def test_t2_off_rewrites_nothing():
    agent = _agent(v61=False)
    out, rewrite = agent._v61_rewrite_exit(
        BOOK, _Decision("TAKER_EXIT"), book=_Book(), inventory=_Inventory(),
        exit_kwargs={"inventory_qty": Q},
    )
    assert rewrite == lf.REWRITE_NONE and out.action == "TAKER_EXIT"


def test_t2_a_flat_book_has_no_floor_to_rewrite_to():
    agent = _agent(lots=())
    _out, rewrite = agent._v61_rewrite_exit(
        BOOK, _Decision("WAIT"), book=_Book(), inventory=_Inventory(),
        exit_kwargs={"inventory_qty": Q},
    )
    assert rewrite == lf.REWRITE_NONE


# ---- T3 the placement floor --------------------------------------------------------------------

def test_t3_a_loss_making_price_is_raised_to_the_floor():
    agent = _agent()
    price, net, floored = agent._v61_apply_floor(
        BOOK, _State(), _Inventory(), Q, BELOW_FLOOR_LONG, "PASSIVE_MAKER_EXIT",
    )
    assert floored is True
    assert price > BELOW_FLOOR_LONG and price < P0, "the floor sits below the entry, above the ask"
    assert net >= DIRECT_MAKER_EXIT_TARGET_BPS - 1e-9, "the placed price carries the target"
    row = _rows(agent, "V61_EXIT_FLOORED")[0]
    assert row["requested_price"] == BELOW_FLOOR_LONG and row["placed_price"] == price
    assert agent._v61_counts["floored_placements"] == 1


def test_t3_a_price_already_above_the_floor_is_untouched():
    """The -40 bps ABSOLUTE touch is one of these: a maker there already closes positive."""
    agent = _agent()
    price, net, floored = agent._v61_apply_floor(
        BOOK, _State(), _Inventory(), Q, ABSOLUTE_TOUCH, "PASSIVE_MAKER_EXIT",
    )
    assert floored is False and price == ABSOLUTE_TOUCH and net is None
    assert _rows(agent, "V61_EXIT_FLOORED") == []


def test_t3_a_short_lot_floor_is_a_ceiling():
    agent = _agent(long=False)
    price, _net, floored = agent._v61_apply_floor(
        BOOK, _State(), _Inventory(net_base=-Q), Q, ABOVE_FLOOR_SHORT, "PASSIVE_MAKER_EXIT",
    )
    assert floored is True and price < ABOVE_FLOOR_SHORT and price > P0


def test_t3_a_flat_book_is_not_floored():
    agent = _agent(lots=())
    price, net, floored = agent._v61_apply_floor(
        BOOK, _State(), _Inventory(), Q, BELOW_FLOOR_LONG, "PASSIVE_MAKER_EXIT",
    )
    assert (price, net, floored) == (BELOW_FLOOR_LONG, None, False)


def test_t3_the_floor_uses_the_head_lot_not_the_average():
    """FIFO closes the OLDEST lot; a second, better lot must not lower the floor."""
    agent = _agent(lots=((Q, P0, OPEN_FEE), (Q, 380.0, -0.5)))
    solo = _agent()
    assert agent._v61_floor_for(BOOK, True) == solo._v61_floor_for(BOOK, True)


# ---- T4 the dust-compaction clip -----------------------------------------------------------------

def test_t4_a_clip_below_the_break_even_is_refused():
    agent = _agent(lots=((0.1, P0, -0.22),))
    assert agent._v61_compaction_price_ok(BOOK, 0.1, BELOW_FLOOR_LONG, _State()) is False
    assert agent.compaction_refusals == [], "the caller books the refusal, not the predicate"
    assert agent._direct_v61_compaction_refusals == 1
    row = _rows(agent, "V61_COMPACTION_REFUSED")[0]
    assert row["clip_price"] == BELOW_FLOOR_LONG and row["floor_price"] > BELOW_FLOOR_LONG


def test_t4_a_clip_at_or_above_the_break_even_is_allowed():
    agent = _agent(lots=((0.1, P0, -0.22),))
    assert agent._v61_compaction_price_ok(BOOK, 0.1, ABSOLUTE_TOUCH, _State()) is True
    assert agent._direct_v61_compaction_refusals == 0


def test_t4_a_book_with_no_lot_is_allowed():
    agent = _agent(lots=())
    assert agent._v61_compaction_price_ok(BOOK, 0.1, BELOW_FLOOR_LONG, _State()) is True


# ---- T5 lot persistence ---------------------------------------------------------------------------

def test_t5_the_lots_round_trip_through_the_session_file():
    agent = _agent(lots=((Q, P0, OPEN_FEE), (Q, 402.0, -0.31)))
    saved = agent._v61_lots_session_state()
    assert saved["version"] == lf.V61_LOT_FLOOR_VERSION
    assert saved["books"][str(BOOK)]["longs"] == [[TS, Q, P0, OPEN_FEE], [TS, Q, 402.0, -0.31]]
    restored = agent._v61_lots_from_session(saved)
    assert restored[BOOK]["longs"] == [(TS, Q, P0, OPEN_FEE), (TS, Q, 402.0, -0.31)]


def test_t5_a_restored_side_is_used_when_it_still_adds_up():
    agent = _agent()
    agent._v61_restored_lots = agent._v61_lots_from_session(agent._v61_lots_session_state())
    lots = agent._v61_restored_side(BOOK, "longs", Q)
    assert lots == [(TS, Q, P0, OPEN_FEE)]
    assert agent._v61_counts["lots_restored"] == 1


def test_t5_a_restored_side_that_no_longer_matches_the_venue_is_dropped():
    agent = _agent()
    agent._v61_restored_lots = agent._v61_lots_from_session(agent._v61_lots_session_state())
    assert agent._v61_restored_side(BOOK, "longs", 0.75) == []
    assert agent._v61_counts["lots_restore_mismatch"] == 1


def test_t5_a_restored_side_is_consumed_once():
    agent = _agent()
    agent._v61_restored_lots = agent._v61_lots_from_session(agent._v61_lots_session_state())
    assert agent._v61_restored_side(BOOK, "longs", Q)
    assert agent._v61_restored_side(BOOK, "longs", Q) == []


def test_t5_off_restores_nothing():
    agent = _agent(v61=False)
    agent._v61_restored_lots = {BOOK: {"longs": [(TS, Q, P0, OPEN_FEE)]}}
    assert agent._v61_restored_side(BOOK, "longs", Q) == []


def test_t5_a_malformed_payload_is_ignored():
    agent = _agent()
    assert agent._v61_lots_from_session(None) == {}
    assert agent._v61_lots_from_session({"books": {"x": {"longs": [[1, 2]]}}}) == {}


# ---- T6 telemetry, switch and wiring ---------------------------------------------------------------

def test_t6_the_state_row_reports_the_refusals():
    agent = _agent()
    agent._direct_v61_taker_refusals = 3
    agent._direct_v61_compaction_refusals = 1
    agent._v61_telemetry(_State())
    row = _rows(agent, "V61_STATE")[0]
    assert row["enabled"] == 1 and row["taker_refusals"] == 3 and row["compaction_refusals"] == 1
    assert row["errors"] == 0 and row["v61_lot_floor_version"] == lf.V61_LOT_FLOOR_VERSION


def test_t6_the_state_row_shows_the_switch_off():
    agent = _agent(v61=False)
    agent._v61_telemetry(_State())
    assert _rows(agent, "V61_STATE")[0]["enabled"] == 0


def test_t6_the_state_row_is_emitted_once_then_on_the_cadence():
    agent = _agent(tick=3000)
    agent._v61_telemetry(_State())
    agent._tick = 3001
    agent._v61_telemetry(_State())
    assert len(_rows(agent, "V61_STATE")) == 1
    agent._tick = 3100
    agent._v61_telemetry(_State())
    assert len(_rows(agent, "V61_STATE")) == 2


def test_t6_source_wiring():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_9"' in SIMPLE
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v6_2_9"' in SIMPLE
    assert "from research_v61_lot_floor import (" in SIMPLE
    # the choke point precedes the frozen market order
    assert SIMPLE.index("ok, detail = self._v61_taker_verdict(") < SIMPLE.index(
        "placed = super()._execute_aggressive_close("
    )
    # the floor precedes the A1.9.1 classifier, or every floored order is cancelled as stale
    assert SIMPLE.index("close_price, v61_net, v61_floored = self._v61_apply_floor(") < SIMPLE.index(
        "verdict = self._a191_decide("
    )
    # the rewrite runs after the A1.9.8/A1.9.9 authorities have had their say
    assert SIMPLE.index("decision, a199_rule, a198_arm = self._a199_authorize_exit(") < SIMPLE.index(
        "decision, v61_rewrite = self._v61_rewrite_exit("
    )
    # the resting net the classifier reads is the validator's, not vwap with today's fee twice
    assert "v61_fifo_close_net_bps(" in SIMPLE
    for stat in ("direct_v61_no_loss", "direct_v61_taker_refusals", "direct_v61_compaction_refusals"):
        assert SIMPLE.count(f'"{stat}"') >= 2, stat


def test_t6_launcher():
    body = "\n".join(
        ln for ln in LAUNCHER.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "research_v61_no_loss=1" in body
    assert "V610_BUILD=1" in body
    assert "strategy1_direct_v6_1_0)" in body
    assert "tests/test_research_v6_1_0_no_loss.py" in body
    assert "tests/test_research_v6_1_0_lot_floor.py" in body
    assert "[preflight] v6.1 no-loss FIFO floor PASS" in body
