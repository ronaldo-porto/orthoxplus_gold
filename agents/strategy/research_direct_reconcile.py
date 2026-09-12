# SPDX-License-Identifier: MIT
"""A1.9.5 step 1: venue-vs-local inventory reconciliation observer.

The agent's inventory comes entirely from ``_position_tracker_snapshot``, a
local accumulation of observed fills.  Nothing ever compares it against the
venue.  ``_research_reconcile_account_inventory`` reads the venue base and then
discards it -- it clears ``_open_positions`` and returns only ``len(live)`` --
so a divergence between what the agent believes it holds and what the exchange
says it holds is currently unobservable.  It also fired zero times in a 9,738
tick run, because it is only wired to a simulation transition.

This module measures that divergence and nothing else.  It places no orders,
mutates no agent state, and is safe to run on every sampled tick.

Two details the caller must not get wrong:

``Balance.total`` is a BALANCE, not a position.  The model carries ``initial``
alongside ``total``/``free``/``reserved``, and the miner is endowed with base
at simulation start, so the venue net position is ``total - initial``.
``reconcile_account_base`` in ``research_session_state`` returns ``total`` with
a ``free`` fallback and never subtracts ``initial``; using its output directly
as a position would be wrong by the endowment on every book.  Both figures are
reported here so the difference stays visible in the log.

Position age is a live trading input, so the caller must source local base from
``_position_tracker_snapshot`` (pure) and never from ``_net_inventory``, which
advances ``_position_ticks`` as a side effect.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

A195_RECONCILE_VERSION = "direct_reconcile_v4_16_2_a1_9_5"

# A divergence below this is reporting noise, not a real disagreement.
A195_DEFAULT_TOLERANCE_BASE = 1e-9

# Diverged books carried as detail rows in one emission.  The summary always
# counts every book; this only bounds the per-row payload.
A195_MAX_DETAIL_ROWS = 12

# Why a book could not be reconciled.  A book with no venue account, or with a
# balance carrying no `initial`, cannot yield a net position at all -- that is
# a distinct outcome from "reconciled, and they agree".
UNRESOLVED_NO_ACCOUNT = "NO_ACCOUNT"
UNRESOLVED_NO_BALANCE = "NO_BALANCE"
UNRESOLVED_NO_INITIAL = "NO_INITIAL"
UNRESOLVED_NOT_FINITE = "NOT_FINITE"


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


@dataclass(frozen=True)
class BookReconciliation:
    """One book's local belief against the venue's record."""

    book_id: int
    local_base: float
    venue_total: float | None
    venue_initial: float | None
    venue_free: float | None
    venue_reserved: float | None
    venue_net: float | None
    legacy_base: float | None
    divergence: float | None
    unresolved_reason: str | None

    @property
    def resolved(self) -> bool:
        return self.venue_net is not None and self.divergence is not None

    def as_log(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "book": int(self.book_id),
            "local_base": self.local_base,
            "venue_net": self.venue_net,
            "divergence": self.divergence,
        }
        if self.venue_total is not None:
            row["venue_total"] = self.venue_total
        if self.venue_initial is not None:
            row["venue_initial"] = self.venue_initial
        if self.venue_reserved is not None:
            row["venue_reserved"] = self.venue_reserved
        if self.legacy_base is not None:
            row["legacy_reconcile_base"] = self.legacy_base
        if self.unresolved_reason is not None:
            row["unresolved"] = self.unresolved_reason
        return row


@dataclass(frozen=True)
class ReconciliationReport:
    """Whole-universe reconciliation summary for one tick."""

    books_seen: int
    books_resolved: int
    books_unresolved: int
    books_diverged: int
    max_abs_divergence: float
    total_abs_divergence: float
    local_abs_base: float
    venue_abs_base: float
    worst_book: int | None
    unresolved_counts: dict[str, int]
    diverged_rows: tuple[BookReconciliation, ...]

    def as_log(self, *, max_rows: int = A195_MAX_DETAIL_ROWS) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "a195_reconcile_version": A195_RECONCILE_VERSION,
            "books_seen": self.books_seen,
            "books_resolved": self.books_resolved,
            "books_unresolved": self.books_unresolved,
            "books_diverged": self.books_diverged,
            "max_abs_divergence": self.max_abs_divergence,
            "total_abs_divergence": self.total_abs_divergence,
            "local_abs_base": self.local_abs_base,
            "venue_abs_base": self.venue_abs_base,
            "worst_book": self.worst_book,
        }
        if self.unresolved_counts:
            payload["unresolved_counts"] = dict(self.unresolved_counts)
        if self.diverged_rows:
            payload["diverged"] = [
                row.as_log() for row in self.diverged_rows[:max(0, int(max_rows))]
            ]
        return payload


def venue_net_base(account: Any) -> tuple[float | None, dict[str, float | None], str | None]:
    """Net BASE position the venue records for one book.

    Returns ``(net, components, unresolved_reason)``.  ``net`` is
    ``total - initial``; it is ``None`` when the account or either component is
    missing or non-finite, and ``unresolved_reason`` then says which.
    """
    components: dict[str, float | None] = {
        "total": None, "free": None, "reserved": None, "initial": None,
    }
    if account is None:
        return None, components, UNRESOLVED_NO_ACCOUNT
    balance = getattr(account, "base_balance", None)
    if balance is None:
        return None, components, UNRESOLVED_NO_BALANCE

    for name in ("total", "free", "reserved", "initial"):
        components[name] = _finite(getattr(balance, name, None))

    total = components["total"]
    if total is None:
        return None, components, UNRESOLVED_NOT_FINITE
    initial = components["initial"]
    if initial is None:
        # `initial` defaults to None on the model, so an older or partial
        # payload leaves the endowment unknown.  A net position cannot be
        # derived from a balance alone -- say so rather than guess zero.
        return None, components, UNRESOLVED_NO_INITIAL
    return total - initial, components, None


def reconcile_books(
    local_base_by_book: dict[int, float],
    accounts: Any,
    *,
    tolerance: float = A195_DEFAULT_TOLERANCE_BASE,
    legacy_base_by_book: dict[int, float] | None = None,
    max_detail_rows: int = A195_MAX_DETAIL_ROWS,
) -> ReconciliationReport:
    """Compare local per-book base against the venue's accounts.

    ``local_base_by_book`` is signed net base from the position tracker.
    ``legacy_base_by_book`` is the raw ``reconcile_account_base`` output, kept
    only so the balance-vs-position discrepancy stays visible.
    """
    tol = abs(_finite(tolerance) or A195_DEFAULT_TOLERANCE_BASE)
    legacy = legacy_base_by_book or {}

    resolved = unresolved = diverged = 0
    max_abs = total_abs = 0.0
    local_abs = venue_abs = 0.0
    worst_book: int | None = None
    unresolved_counts: dict[str, int] = {}
    rows: list[BookReconciliation] = []

    for book_id in sorted(local_base_by_book):
        local = _finite(local_base_by_book.get(book_id)) or 0.0
        local_abs += abs(local)

        account = None
        if accounts is not None:
            try:
                account = accounts[book_id]
            except Exception:
                # An observer must never propagate a venue-side failure into
                # the trading path, whatever the accessor decides to raise.
                account = None

        net, components, reason = venue_net_base(account)
        row = BookReconciliation(
            book_id=int(book_id),
            local_base=local,
            venue_total=components["total"],
            venue_initial=components["initial"],
            venue_free=components["free"],
            venue_reserved=components["reserved"],
            venue_net=net,
            legacy_base=_finite(legacy.get(book_id)),
            divergence=None if net is None else local - net,
            unresolved_reason=reason,
        )

        if net is None:
            unresolved += 1
            key = reason or UNRESOLVED_NO_ACCOUNT
            unresolved_counts[key] = unresolved_counts.get(key, 0) + 1
            # An unresolved book with real local inventory is itself a finding,
            # so it is carried as a detail row.
            if abs(local) > tol:
                rows.append(row)
            continue

        resolved += 1
        venue_abs += abs(net)
        gap = abs(row.divergence or 0.0)
        total_abs += gap
        if gap > max_abs:
            max_abs = gap
            worst_book = int(book_id)
        if gap > tol:
            diverged += 1
            rows.append(row)

    rows.sort(key=lambda r: abs(r.divergence) if r.divergence is not None else float("inf"), reverse=True)
    return ReconciliationReport(
        books_seen=len(local_base_by_book),
        books_resolved=resolved,
        books_unresolved=unresolved,
        books_diverged=diverged,
        max_abs_divergence=max_abs,
        total_abs_divergence=total_abs,
        local_abs_base=local_abs,
        venue_abs_base=venue_abs,
        worst_book=worst_book,
        unresolved_counts=unresolved_counts,
        diverged_rows=tuple(rows[:max(0, int(max_detail_rows))]),
    )
