"""A1.9.5 step 1: the venue-vs-local reconciliation observer.

Step 1 is measurement only, so these tests assert two things in equal measure:
that the arithmetic is right, and that the observer cannot change behaviour.
"""
from pathlib import Path

from research_direct_reconcile import (
    A195_DEFAULT_TOLERANCE_BASE,
    A195_MAX_DETAIL_ROWS,
    A195_RECONCILE_VERSION,
    UNRESOLVED_NO_ACCOUNT,
    UNRESOLVED_NO_BALANCE,
    UNRESOLVED_NO_INITIAL,
    reconcile_books,
    venue_net_base,
)

ROOT = Path(__file__).parents[1]
SIMPLE = (ROOT / "agents" / "strategy" / "Strategy1_Research_Simple.py").read_text()
MODULE = (ROOT / "agents" / "strategy" / "research_direct_reconcile.py").read_text()


def _method_body(source: str, name: str) -> str:
    """Source of one method, from its `def` to the next method at same indent.

    Slicing to a nearby comment marker is not safe here -- the A1.9.3 marker
    occurs three times in this file, so an `index()` pair silently produced an
    empty string and every assertion over it passed vacuously.
    """
    needle = f"    def {name}("
    start = source.index(needle)
    nxt = source.find("\n    def ", start + len(needle))
    body = source[start:] if nxt == -1 else source[start:nxt]
    assert len(body) > 200, f"{name} body looks truncated ({len(body)} chars)"
    return body


class _Balance:
    def __init__(self, total=None, free=None, reserved=None, initial=None):
        self.total = total
        self.free = free
        self.reserved = reserved
        self.initial = initial


class _Account:
    def __init__(self, balance):
        self.base_balance = balance


def _acct(total, initial, free=None, reserved=None):
    return _Account(_Balance(total=total, free=free, reserved=reserved, initial=initial))


# ---- venue_net_base: total is a balance, not a position -------------------

def test_venue_net_subtracts_the_initial_endowment():
    """The defect this whole observer exists to expose."""
    net, components, reason = venue_net_base(_acct(total=10.25, initial=10.0))
    assert reason is None
    assert abs(net - 0.25) < 1e-12
    assert components["total"] == 10.25
    assert components["initial"] == 10.0
    # Reading `total` as a position would have said 10.25 instead of 0.25.
    assert net != components["total"]


def test_venue_net_is_signed_for_a_short():
    net, _components, reason = venue_net_base(_acct(total=9.75, initial=10.0))
    assert reason is None
    assert abs(net - (-0.25)) < 1e-12


def test_flat_book_reconciles_to_zero_not_to_the_endowment():
    net, _components, reason = venue_net_base(_acct(total=10.0, initial=10.0))
    assert reason is None
    assert net == 0.0


def test_unresolved_cases_are_named_not_guessed():
    for account, expected in [
        (None, UNRESOLVED_NO_ACCOUNT),
        (_Account(None), UNRESOLVED_NO_BALANCE),
        (_acct(total=10.0, initial=None), UNRESOLVED_NO_INITIAL),
    ]:
        net, _components, reason = venue_net_base(account)
        assert net is None
        assert reason == expected


def test_missing_initial_never_falls_back_to_zero():
    """A balance without an endowment cannot yield a position -- say so."""
    net, _components, reason = venue_net_base(_acct(total=10.0, initial=None))
    assert net is None and reason == UNRESOLVED_NO_INITIAL
    report = reconcile_books({1: 0.0}, {1: _acct(total=10.0, initial=None)})
    assert report.books_resolved == 0
    assert report.books_unresolved == 1
    # Nothing may be counted as agreement just because it could not be read.
    assert report.books_diverged == 0


def test_non_finite_balance_is_unresolved():
    net, _components, reason = venue_net_base(_acct(total=float("nan"), initial=10.0))
    assert net is None and reason is not None


# ---- reconcile_books ------------------------------------------------------

def test_agreement_reports_no_divergence():
    report = reconcile_books(
        {1: 0.25, 2: -0.5, 3: 0.0},
        {1: _acct(10.25, 10.0), 2: _acct(9.5, 10.0), 3: _acct(10.0, 10.0)},
    )
    assert report.books_seen == 3
    assert report.books_resolved == 3
    assert report.books_diverged == 0
    assert report.max_abs_divergence == 0.0
    assert report.worst_book is None or report.max_abs_divergence == 0.0
    assert not report.diverged_rows


def test_divergence_is_local_minus_venue_and_signed_correctly():
    # Agent believes flat; venue still holds 0.25 -- the restart-amnesia shape.
    report = reconcile_books({7: 0.0}, {7: _acct(10.25, 10.0)})
    assert report.books_diverged == 1
    assert report.worst_book == 7
    row = report.diverged_rows[0]
    assert abs(row.divergence - (-0.25)) < 1e-12
    assert abs(report.max_abs_divergence - 0.25) < 1e-12
    assert abs(report.total_abs_divergence - 0.25) < 1e-12


def test_abs_base_totals_are_tracked_on_both_sides():
    report = reconcile_books(
        {1: 0.25, 2: -0.5},
        {1: _acct(10.25, 10.0), 2: _acct(9.5, 10.0)},
    )
    assert abs(report.local_abs_base - 0.75) < 1e-12
    assert abs(report.venue_abs_base - 0.75) < 1e-12


def test_tolerance_suppresses_float_noise_but_not_real_gaps():
    tiny = reconcile_books({1: 0.25 + 1e-12}, {1: _acct(10.25, 10.0)}, tolerance=1e-9)
    assert tiny.books_diverged == 0
    real = reconcile_books({1: 0.26}, {1: _acct(10.25, 10.0)}, tolerance=1e-9)
    assert real.books_diverged == 1


def test_unresolved_book_with_inventory_is_surfaced():
    """Silence about a book we hold is the dangerous case."""
    report = reconcile_books({4: 0.25}, {})
    assert report.books_unresolved == 1
    assert report.unresolved_counts.get(UNRESOLVED_NO_ACCOUNT) == 1
    assert [r.book_id for r in report.diverged_rows] == [4]
    assert report.diverged_rows[0].resolved is False


def test_unresolved_flat_book_is_counted_but_not_detailed():
    report = reconcile_books({4: 0.0}, {})
    assert report.books_unresolved == 1
    assert not report.diverged_rows


def test_detail_rows_are_ranked_worst_first_and_bounded():
    local = {i: 0.0 for i in range(40)}
    accounts = {i: _acct(10.0 + i * 0.01, 10.0) for i in range(40)}
    report = reconcile_books(local, accounts, max_detail_rows=5)
    assert report.books_diverged == 39  # book 0 is exactly flat
    assert len(report.diverged_rows) == 5
    gaps = [abs(r.divergence) for r in report.diverged_rows]
    assert gaps == sorted(gaps, reverse=True)
    assert report.worst_book == 39


def test_legacy_balance_is_carried_for_comparison():
    report = reconcile_books(
        {1: 0.0}, {1: _acct(10.25, 10.0)}, legacy_base_by_book={1: 10.25},
    )
    row = report.diverged_rows[0]
    assert row.legacy_base == 10.25
    assert row.venue_net == 0.25
    # The whole point: the legacy helper's number is not the position.
    assert row.legacy_base != row.venue_net


def test_empty_universe_is_safe():
    report = reconcile_books({}, {})
    assert report.books_seen == 0
    assert report.max_abs_divergence == 0.0
    assert report.as_log()["books_seen"] == 0


def test_accounts_that_raise_on_lookup_do_not_propagate():
    class _Hostile:
        def __getitem__(self, key):
            raise RuntimeError("venue unavailable")

    report = reconcile_books({1: 0.25}, _Hostile())
    assert report.books_unresolved == 1


# ---- log payload ----------------------------------------------------------

def test_log_payload_is_versioned_and_json_safe():
    report = reconcile_books({1: 0.0}, {1: _acct(10.25, 10.0)})
    payload = report.as_log()
    assert payload["a195_reconcile_version"] == A195_RECONCILE_VERSION
    assert payload["books_diverged"] == 1
    assert payload["diverged"][0]["book"] == 1
    import json
    json.dumps(payload)  # must survive the research queue serializer


def test_log_payload_omits_detail_when_everything_agrees():
    payload = reconcile_books({1: 0.25}, {1: _acct(10.25, 10.0)}).as_log()
    assert "diverged" not in payload


# ---- wiring and the measurement-only contract -----------------------------

def test_observer_is_wired_behind_its_own_switch():
    assert "research_a195_reconcile_observe" in SIMPLE
    assert "_a195_reconcile_enabled" in SIMPLE
    assert "self._a195_emit_reconcile(state, tick)" in SIMPLE
    assert '"A195_RECONCILE"' in SIMPLE


def test_observer_reads_the_pure_tracker_not_net_inventory():
    """_net_inventory advances position age, which drives exit escalation."""
    body = _method_body(SIMPLE, "_a195_local_base_by_book")
    assert "_position_tracker_snapshot(" in body
    # The docstring names _net_inventory to say why it is avoided, so assert
    # on a call rather than a mention.
    assert "_net_inventory(" not in body


def test_observer_body_places_no_orders_and_mutates_no_positions():
    body = _method_body(SIMPLE, "_a195_emit_reconcile")
    for forbidden in (
        "place_order", "cancel_order", "_open_positions", "positions.clear",
        "self._tick =", "_position_ticks", "_net_inventory(",
    ):
        assert forbidden not in body, forbidden


def test_step1_does_not_gate_quoting_yet():
    """The divergence interlock is F2 / step 3, not step 1."""
    body = _method_body(SIMPLE, "_a195_emit_reconcile")
    assert "divergence_abort" not in body
    # The only early exit is the disabled-switch guard; nothing returns a
    # decision that the caller could act on.
    assert body.count("return") == 2
    assert "if not self._a195_reconcile_enabled():" in body
    assert "self._emit(" in body


def test_module_documents_the_balance_versus_position_trap():
    assert "total - initial" in MODULE
    assert "_net_inventory" in MODULE


def test_summary_stats_are_exported():
    for key in (
        "direct_a195_reconcile_observe",
        "direct_a195_reconcile_emits",
        "direct_a195_reconcile_diverged_books",
        "direct_a195_reconcile_unresolved_books",
        "direct_a195_reconcile_max_abs_divergence",
    ):
        assert key in SIMPLE, key


def test_detail_row_cap_is_bounded():
    """The live log is already ~365MB; 128 books per emit would be worse."""
    assert 0 < A195_MAX_DETAIL_ROWS <= 32
    assert A195_DEFAULT_TOLERANCE_BASE > 0.0
