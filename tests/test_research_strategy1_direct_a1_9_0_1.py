"""A1.9.0.1 — resting-exit observability repair.

Phase A measured `resting_present = 0` on all 3,746 exit evaluations.  The
A1.7.5 runtime log shows why that answer was blind rather than economic:

* profitable Maker exits rest a median 3,000 ms (three 1,000 ms cycles)
* 76.4% of consecutive exit evaluations on a book are one tick apart
* 27.0% of consecutive exit placements land while the previous exit is live
* yet PROFITABLE_EXIT_HOLD, A172_WAIT_CANCEL, A17431_BOOK_OWNERSHIP_BLOCK
  and FINAL_CONTRACT_REJECT each fired exactly 0 times in 29,183 ticks

The orders are real; `state.accounts[uid][book].orders` just does not carry
them at decision time.  A1.9.0.1 rebuilds the live-order view from the
acknowledged notice stream and keeps the account view as a control.  It is
still measurement only.
"""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SIMPLE = ROOT / "agents" / "strategy" / "Strategy1_Research_Simple.py"
LEDGER = ROOT / "agents" / "strategy" / "research_direct_exit_ledger.py"
FROZEN = ROOT / "agents" / "strategy" / "Strategy1_Research.py"
LAUNCHER = ROOT / "run_strategy1_research_simple_multi.sh"

SRC = SIMPLE.read_text()
LEDGER_SRC = LEDGER.read_text()

from research_direct_exit_ledger import (  # noqa: E402
    DIRECT_EXIT_LEDGER_VERSION,
    LEDGER_REMOVED_CANCELLED,
    LEDGER_REMOVED_FILLED,
    LEDGER_SWEEP_GRACE_MS,
    DirectExitLedger,
    close_side_for,
)

MS = 1_000_000


def _ledger_with(**kw):
    led = DirectExitLedger()
    base = dict(
        order_id=1, book_id=7, side=1, price=100.0, quantity=0.25,
        timestamp_ns=0, tick=0,
    )
    base.update(kw)
    led.note_accepted(**base)
    return led


# --------------------------------------------------------------- versioning

def test_version_pins_advance_to_a1_9_0_1():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_5"' in SRC
    assert 'SIMPLE_ENGINE_VERSION = "strategy1_direct_v4_16_2_a1_9_5"' in SRC
    assert DIRECT_EXIT_LEDGER_VERSION == "direct_exit_ledger_v4_16_2_a1_9_0_3"


def test_launcher_pins_the_same_version_and_preflights_this_suite():
    text = LAUNCHER.read_text()
    assert 'strategy1_direct_v4_16_2_a1_9_4"' in text
    assert "test_research_strategy1_direct_a1_9_0_1.py" in text
    # A1.9.1 Phase B raises the TTL through PARAMS, where the frozen base
    # clamps it to [1000, 5000] and the run manifest records the value.
    assert "research_profitable_exit_ttl_ms=4000" in text


def test_frozen_base_still_untouched():
    frozen = FROZEN.read_text()
    assert 'RESEARCH_POLICY_VERSION = "simplified_hybrid_authority_v4_16_2"' in frozen
    assert "_a19_" not in frozen
    assert "exit_ledger" not in frozen


# ------------------------------------------------- still measurement only

def test_phase_a2_remains_behaviour_neutral():
    # The shadow decision is computed and discarded; no cancel/reprice path.
    # A1.9.1 Phase B is now shipped, so the reprice emitter exists by design.
    # What must remain true is that the OLD shadow observer stays measurement
    # only: its decision is still discarded.
    assert "shadow_mode=1," in SRC
    assert "deliberately discarded" in SRC
    # The TTL is read, never assigned, by the overlay.
    assert "self.research_profitable_exit_ttl_ms =" not in SRC
    assert "cycle_bounded_profitable_exit_ttl_ms" not in SRC


def test_ledger_is_never_consulted_by_a_trading_decision():
    # Only the A1.9 observer and its own hooks may read the ledger.
    for line in SRC.splitlines():
        if "_a19_ledger_ref()" in line or "_a19_ledger" in line:
            assert "response." not in line, line
            assert "cancel_orders" not in line, line
            assert "place_order" not in line, line


def test_ledger_module_holds_no_strategy_authority():
    for banned in ("response", "cancel_orders", "place_order", "_emit", "self.accounts"):
        assert banned not in LEDGER_SRC, banned


# ------------------------------------------------------- ledger lifecycle

def test_accepted_order_becomes_visible_as_resting():
    led = _ledger_with()
    rows = led.live_orders(7, side=1)
    assert len(rows) == 1
    assert rows[0].order_id == 1
    assert rows[0].price == 100.0
    assert led.live_count(7) == 1


def test_cancellation_retires_the_row():
    led = _ledger_with()
    assert led.note_removed(1, cause=LEDGER_REMOVED_CANCELLED) is not None
    assert led.live_orders(7, side=1) == []
    assert led.removed == 1


def test_full_fill_retires_the_row():
    led = _ledger_with(quantity=0.25)
    led.note_removed(1, cause=LEDGER_REMOVED_FILLED, filled_qty=0.25)
    assert led.live_orders(7, side=1) == []


def test_partial_fill_keeps_the_remainder_resting():
    """A partially filled exit still owns its queue position."""
    led = _ledger_with(quantity=0.25)
    led.note_removed(1, cause=LEDGER_REMOVED_FILLED, filled_qty=0.10)
    rows = led.live_orders(7, side=1)
    assert len(rows) == 1
    assert rows[0].remaining == pytest.approx(0.15)
    led.note_removed(1, cause=LEDGER_REMOVED_FILLED, filled_qty=0.15)
    assert led.live_orders(7, side=1) == []


def test_side_filter_separates_entry_from_exit():
    led = DirectExitLedger()
    led.note_accepted(order_id=1, book_id=7, side=0, price=99.0, quantity=0.25,
                      timestamp_ns=0, tick=0)
    led.note_accepted(order_id=2, book_id=7, side=1, price=101.0, quantity=0.25,
                      timestamp_ns=0, tick=0)
    assert [r.order_id for r in led.live_orders(7, side=1)] == [2]
    assert [r.order_id for r in led.live_orders(7, side=0)] == [1]
    assert led.live_count(7) == 2


def test_close_side_matches_the_frozen_convention():
    # Long closes by selling (1); short closes by buying (0).
    assert close_side_for(0.25) == 1
    assert close_side_for(-0.25) == 0
    assert close_side_for(0.0) == 0


def test_newest_order_is_returned_first():
    led = DirectExitLedger()
    led.note_accepted(order_id=1, book_id=7, side=1, price=100.0, quantity=0.25,
                      timestamp_ns=1000 * MS, tick=1)
    led.note_accepted(order_id=2, book_id=7, side=1, price=100.5, quantity=0.25,
                      timestamp_ns=3000 * MS, tick=3)
    assert [r.order_id for r in led.live_orders(7, side=1)] == [2, 1]


def test_unmatched_removal_is_counted_not_crashed():
    led = DirectExitLedger()
    assert led.note_removed(999, cause=LEDGER_REMOVED_CANCELLED) is None
    assert led.unmatched_removals == 1


def test_malformed_acceptance_is_rejected():
    led = DirectExitLedger()
    assert not led.note_accepted(order_id=None, book_id=7, side=1, price=100.0,
                                 quantity=0.25, timestamp_ns=0, tick=0)
    assert not led.note_accepted(order_id=1, book_id=7, side=1, price=0.0,
                                 quantity=0.25, timestamp_ns=0, tick=0)
    assert not led.note_accepted(order_id=1, book_id=7, side=1, price=100.0,
                                 quantity=0.0, timestamp_ns=0, tick=0)
    assert led.live_count() == 0


# ------------------------------------------------------------- TTL sweep

def test_sweep_never_discards_an_order_inside_the_clamped_ttl_range():
    """The frozen base clamps the exit TTL to [1000, 5000] ms."""
    assert LEDGER_SWEEP_GRACE_MS > 5000.0
    led = _ledger_with(timestamp_ns=1000 * MS)
    led.sweep(6000 * MS)          # 5,000 ms of resting: the clamp ceiling
    assert led.live_count(7) == 1


def test_sweep_drops_a_row_whose_removal_notice_never_arrived():
    led = _ledger_with(timestamp_ns=1000 * MS)
    swept = led.sweep(int((1000.0 + LEDGER_SWEEP_GRACE_MS + 1000.0) * MS))
    assert swept == 1
    assert led.live_count(7) == 0
    assert led.swept == 1


def test_sweep_tolerates_an_unusable_clock():
    led = _ledger_with(timestamp_ns=1000 * MS)
    assert led.sweep(None) == 0
    assert led.sweep(0) == 0
    assert led.live_count(7) == 1


def test_age_reports_resting_time_in_ms():
    led = _ledger_with(timestamp_ns=1000 * MS)
    row = led.live_orders(7, side=1)[0]
    assert row.age_ms(4000 * MS) == pytest.approx(3000.0)
    assert row.age_ms(None) == 0.0


# ------------------------------------------------- overlay wiring contract

def test_overlay_feeds_the_ledger_from_the_notice_stream():
    for hook in ("def onOrderAccepted", "def onOrderCancelled"):
        assert hook in SRC, hook
    # Each override must delegate to the frozen handler first.
    assert "super().onOrderAccepted(event)" in SRC
    assert "super().onOrderCancelled(event)" in SRC
    # Fills retire rows through the existing onTrade override.
    assert "cause=LEDGER_REMOVED_FILLED" in SRC
    assert "cause=LEDGER_REMOVED_CANCELLED" in SRC


def test_observer_reads_the_ledger_and_keeps_the_account_control():
    assert "close_side = close_side_for(net_base)" in SRC
    assert "resting = ledger.live_orders(" in SRC
    assert "account_resting = self._a19_close_side_orders(bid, long_pos)" in SRC
    assert "resting_present_account=" in SRC
    assert "direct_a1901_ledger_only_hits" in SRC


def test_market_orders_do_not_enter_the_ledger():
    """Only post-only limit placements own a queue position."""
    idx = SRC.index("def onOrderAccepted")
    body = SRC[idx:idx + 1400]
    assert 'if etype and not ("RDPOL" in etype or "LIMIT" in etype):' in body
    assert 'price = getattr(event, "price", None)' in body
    assert "if price is None:" in body


def test_rejected_placements_do_not_enter_the_ledger():
    idx = SRC.index("def onOrderAccepted")
    body = SRC[idx:idx + 1400]
    assert 'if not bool(getattr(event, "success", True)):' in body


def test_tenure_runs_from_the_exchange_acknowledgement():
    assert '"first_tick": int(row.placed_tick or tick),' in SRC
    assert '"placed_ns": int(row.placed_ns or 0),' in SRC


def test_memory_is_bounded():
    led = DirectExitLedger()
    for oid in range(5000):
        led.note_accepted(order_id=oid, book_id=1, side=1, price=100.0,
                          quantity=0.25, timestamp_ns=oid * MS, tick=oid)
    assert led.live_count() <= 4096


# ------------------------------------------------ expiry-notice lag guard

def test_row_past_its_ttl_is_not_reported_as_holdable():
    """A removal notice lands one state late; 47.5% of placements hit this.

    Holding on an order the exchange has already retired would suppress the
    replacement exit and leave the position with nothing resting.
    """
    led = _ledger_with(timestamp_ns=1000 * MS)
    now = (1000 + 3000) * MS
    assert led.live_orders(7, side=1, max_age_ms=3000.0, now_ns=now) == []
    # Without the TTL filter the stale row is still visible, which is what the
    # lifecycle/absent-reason accounting needs.
    assert len(led.live_orders(7, side=1)) == 1


def test_row_inside_its_ttl_is_reported_as_holdable():
    led = _ledger_with(timestamp_ns=1000 * MS)
    now = (1000 + 2500) * MS
    rows = led.live_orders(7, side=1, max_age_ms=3000.0, now_ns=now)
    assert len(rows) == 1
    assert rows[0].age_ms(now) == pytest.approx(2500.0)


def test_ttl_filter_is_inert_without_a_clock():
    led = _ledger_with(timestamp_ns=1000 * MS)
    assert len(led.live_orders(7, side=1, max_age_ms=3000.0, now_ns=None)) == 1
    assert len(led.live_orders(7, side=1, max_age_ms=None, now_ns=9 * MS)) == 1


def test_observer_filters_resting_by_the_ttl_actually_in_force():
    assert "max_age_ms=exit_ttl_ms" in SRC
    assert 'getattr(self, "research_profitable_exit_ttl_ms", 3000.0)' in SRC
    assert "ledger_expiry_lagged=" in SRC
    assert "direct_a1901_ledger_expiry_lag_hits" in SRC


def test_clock_restart_discards_the_previous_session():
    """Restarts rewind the clock and reuse order ids."""
    led = _ledger_with(timestamp_ns=5000 * MS)
    assert led.sweep(10 * MS) == 1
    assert led.live_count(7) == 0


def test_overlay_sweeps_on_any_clock_movement_not_only_forward():
    assert 'if now_ns and now_ns != int(getattr(self, "_a19_last_state_ns", 0) or 0):' in SRC


def test_preflight_gate_covers_every_direct_regression_suite():
    """A suite that is not in the preflight list does not gate a deploy.

    `a1_7_4_3_1` was missing, so its assertions never ran before a run
    started.  This keeps the list closed structurally rather than by memory.
    """
    launcher = LAUNCHER.read_text()
    suites = sorted(
        p.name for p in (ROOT / "tests").glob("test_research_strategy1_direct_a1_*.py")
    )
    missing = [name for name in suites if name not in launcher]
    assert not missing, f"suites absent from the preflight gate: {missing}"
