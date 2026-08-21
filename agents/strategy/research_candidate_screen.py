# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 6: cheap two-stage candidate screening.

Stage 1 scores every book from inexpensive state. Forced books (inventory,
dust, near-Kappa, hard-risk, live quotes) are never dropped. Stage 2 callers
run expensive predict_direction only on the selected IDs.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ScreenBook:
    book_id: int
    has_inventory: bool = False
    is_dust: bool = False
    observations_remaining: int = 0
    is_hard_risk: bool = False
    has_live_quote: bool = False
    cheap_score: float = 0.0


@dataclass
class ScreenResult:
    selected: list[int]
    forced: list[int]
    forced_inventory: list[int]
    forced_dust: list[int]
    forced_kappa: list[int]
    forced_hard_risk: list[int]
    forced_live: list[int]
    screened_extra: list[int]
    candidate_count: int
    universe: int

    def as_log(self) -> dict[str, int]:
        return {
            "candidate_count": len(self.selected),
            "forced_inventory_count": len(self.forced_inventory),
            "forced_kappa_count": len(self.forced_kappa),
            "forced_dust_count": len(self.forced_dust),
            "forced_hard_risk_count": len(self.forced_hard_risk),
            "forced_live_count": len(self.forced_live),
            "screened_extra_count": len(self.screened_extra),
            "universe": self.universe,
        }


def is_forced(book: ScreenBook) -> bool:
    if book.has_inventory or book.is_dust or book.is_hard_risk or book.has_live_quote:
        return True
    return 0 < int(book.observations_remaining) <= 2


def cheap_book_score(
    *,
    spread_bps: float | None = None,
    trade_events: int = 0,
    top_imbalance: float | None = None,
    fill_rate: float = 0.0,
    last_alpha: float = 0.0,
    specialization: float = 0.0,
) -> float:
    """Inexpensive ranking from already-available top-of-book / memory."""
    spread_term = 0.0
    if spread_bps is not None and spread_bps > 0.0:
        spread_term = max(0.0, 1.0 - min(float(spread_bps), 20.0) / 20.0)
    trade_term = min(1.0, max(0, int(trade_events)) / 8.0)
    imb_term = 0.0 if top_imbalance is None else min(1.0, abs(float(top_imbalance)))
    return (
        0.28 * max(0.0, min(1.0, float(fill_rate)))
        + 0.18 * max(0.0, min(1.0, float(specialization)))
        + 0.18 * max(0.0, min(1.0, float(last_alpha)))
        + 0.16 * trade_term
        + 0.12 * spread_term
        + 0.08 * imb_term
    )


def select_fast_candidates(
    books: list[ScreenBook],
    candidate_count: int,
) -> ScreenResult:
    """Forced books always survive. Extra slots fill by cheap_score descending."""
    cap = max(1, int(candidate_count))
    forced_inv: list[int] = []
    forced_dust: list[int] = []
    forced_kappa: list[int] = []
    forced_hard: list[int] = []
    forced_live: list[int] = []
    forced: list[ScreenBook] = []
    rest: list[ScreenBook] = []
    seen: set[int] = set()
    for book in books:
        bid = int(book.book_id)
        if bid in seen:
            continue
        seen.add(bid)
        if is_forced(book):
            forced.append(book)
            if book.has_inventory:
                forced_inv.append(bid)
            if book.is_dust:
                forced_dust.append(bid)
            if 0 < int(book.observations_remaining) <= 2:
                forced_kappa.append(bid)
            if book.is_hard_risk:
                forced_hard.append(bid)
            if book.has_live_quote:
                forced_live.append(bid)
        else:
            rest.append(book)
    forced_ids = [b.book_id for b in forced]
    extra_n = max(0, cap - len(forced_ids))
    rest.sort(key=lambda b: float(b.cheap_score), reverse=True)
    extra = [b.book_id for b in rest[:extra_n]]
    selected = forced_ids + extra
    return ScreenResult(
        selected=selected,
        forced=forced_ids,
        forced_inventory=forced_inv,
        forced_dust=forced_dust,
        forced_kappa=forced_kappa,
        forced_hard_risk=forced_hard,
        forced_live=forced_live,
        screened_extra=extra,
        candidate_count=cap,
        universe=len(seen),
    )
