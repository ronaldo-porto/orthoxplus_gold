# SPDX-License-Identifier: MIT
"""A1.9.5 step 3 (F2 behaviour): seed local inventory from venue truth.

Step 1 measured the gap.  On log 20260912_121832 at tick 1, immediately after a
restart:

    books_seen 128   books_diverged 120
    local_abs_base   0.0000
    venue_abs_base   8.8520

The agent believed it was flat.  The venue held 8.85 BASE across 120 books --
4.4x the entire 2.0 absolute cap -- and the agent then opened 2.13 BASE of new
positions on top of it.  The offset never resolved: 8.8520 at tick 1, 8.8802 at
tick 4,125.  A restart does not clear inventory, it only clears the belief.

Composition of that residue, which is what makes the fix non-trivial:

    10 books >= min_order   4.31 BASE   REAL   exitable
   110 books <  min_order   4.54 BASE   DUST   unexitable by construction

Seeding all of it naively takes ``abs_now`` to 8.85 against a 2.0 cap, which
drives ``admission_slots`` to 0 permanently -- the dust half can never be worked
off, so the agent would never trade again.  So the two halves are seeded into
different classes:

* REAL residue is seeded as ordinary inventory.  It counts against the cap, the
  agent stops admitting until it works the excess off, and that is the correct
  behaviour -- it genuinely holds those positions.  The transient is finite
  because these lots are exitable.
* LEGACY DUST is seeded for risk, ownership and reporting, and admitted to the
  A1.9.5 F3 parked class so it does not consume acquisition budget.  The F3
  ceiling is raised once, by the measured legacy amount, and never again:
  dust created after startup still competes for the original ceiling, so the
  purge pressure F3 was built to create is preserved.

Two things this module is careful about, because both would do real damage:

VWAP.  The venue reports a balance, not a cost basis, so the entry price of the
residue is unknowable.  Every seeded lot is priced at the current mid, making
its unrealized PnL exactly zero.  Any other choice fabricates PnL that the exit
machinery would then act on and kappa would score.

AGE.  Seeded lots are stamped with the current tick, not backdated.  Backdating
would present 120 positions to the escalation ladder as thousands of ticks old,
and ABSOLUTE_PROTECTION_REDUCE would fire on all of them at once -- the exact
path that carries 97% of cubic downside.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

A195_INVENTORY_TRUTH_VERSION = "direct_inventory_truth_v4_16_2_a1_9_5"

# Residue classes.
SEED_REAL = "REAL"
SEED_LEGACY_DUST = "LEGACY_DUST"

# Hard bounds on one seeding pass.  A seed is a one-shot write into live
# trading state; if the venue returns something unexpected, it must not be
# possible to inject an unbounded position book.
A195_MAX_SEED_BOOKS = 160
A195_MAX_SEED_ABS_BASE = 24.0

# Divergence below this is float noise, not a position.
A195_SEED_EPSILON_BASE = 1e-9


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(number):
        return default
    return number


@dataclass(frozen=True)
class SeedLot:
    """One synthetic lot to write into the local position tracker."""

    book_id: int
    net_base: float
    price: float
    tick: int
    residue_class: str

    @property
    def is_long(self) -> bool:
        return self.net_base > 0.0

    def as_tuple(self) -> tuple[int, float, float, float]:
        """`(timestamp, quantity, price, fee)`, the tracker's lot format.

        Quantity is unsigned -- direction is carried by which deque the lot is
        appended to.  Fee is zero: this lot was not paid for in this session,
        and charging a fabricated fee would distort realized PnL on exit.
        """
        return (int(self.tick), abs(float(self.net_base)), float(self.price), 0.0)


@dataclass(frozen=True)
class SeedPlan:
    """What a startup reconciliation would write, and what it would skip."""

    lots: tuple[SeedLot, ...] = ()
    skipped_already_held: tuple[int, ...] = ()
    skipped_no_price: tuple[int, ...] = ()
    skipped_over_bound: tuple[int, ...] = ()
    real_books: int = 0
    real_abs_base: float = 0.0
    dust_books: int = 0
    dust_abs_base: float = 0.0
    truncated: bool = False

    @property
    def total_abs_base(self) -> float:
        return self.real_abs_base + self.dust_abs_base

    def as_log(self) -> dict[str, Any]:
        return {
            "a195_inventory_truth_version": A195_INVENTORY_TRUTH_VERSION,
            "seeded_books": len(self.lots),
            "real_books": self.real_books,
            "real_abs_base": self.real_abs_base,
            "dust_books": self.dust_books,
            "dust_abs_base": self.dust_abs_base,
            "total_abs_base": self.total_abs_base,
            "skipped_already_held": len(self.skipped_already_held),
            "skipped_no_price": len(self.skipped_no_price),
            "skipped_over_bound": len(self.skipped_over_bound),
            "truncated": int(self.truncated),
        }


def build_seed_plan(
    *,
    venue_net_by_book: dict[int, float],
    local_net_by_book: dict[int, float],
    mid_by_book: dict[int, float],
    min_order: float,
    tick: int,
    epsilon: float = A195_SEED_EPSILON_BASE,
    max_books: int = A195_MAX_SEED_BOOKS,
    max_abs_base: float = A195_MAX_SEED_ABS_BASE,
) -> SeedPlan:
    """Plan the one-shot import of venue inventory into the local tracker.

    Only books the venue reports a position on AND the tracker believes are
    flat are seeded.  A book the tracker already holds is left alone: its local
    lots carry a real cost basis and a real age, and overwriting them with a
    synthetic mid-priced lot would destroy information rather than add it.
    """
    eps = abs(_finite(epsilon, A195_SEED_EPSILON_BASE))
    floor = max(1e-12, abs(_finite(min_order, 0.25)))
    cap_books = max(0, int(max_books))
    cap_abs = max(0.0, _finite(max_abs_base, A195_MAX_SEED_ABS_BASE))

    lots: list[SeedLot] = []
    already: list[int] = []
    no_price: list[int] = []
    over: list[int] = []
    real_n = dust_n = 0
    real_abs = dust_abs = 0.0
    running_abs = 0.0
    truncated = False

    for raw_id in sorted(venue_net_by_book, key=lambda b: -abs(_finite(venue_net_by_book.get(b)))):
        try:
            book_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        venue = _finite(venue_net_by_book.get(raw_id))
        if abs(venue) <= eps:
            continue
        if abs(_finite(local_net_by_book.get(book_id))) > eps:
            already.append(book_id)
            continue
        price = _finite(mid_by_book.get(book_id))
        if price <= 0.0:
            # Without a price there is no way to seed a lot whose unrealized
            # PnL is zero, and a zero-priced lot would poison every bps
            # calculation downstream.
            no_price.append(book_id)
            continue
        if len(lots) >= cap_books or running_abs + abs(venue) > cap_abs:
            over.append(book_id)
            truncated = True
            continue

        residue = SEED_REAL if abs(venue) >= floor else SEED_LEGACY_DUST
        lots.append(SeedLot(
            book_id=book_id, net_base=venue, price=price,
            tick=int(tick), residue_class=residue,
        ))
        running_abs += abs(venue)
        if residue == SEED_REAL:
            real_n += 1
            real_abs += abs(venue)
        else:
            dust_n += 1
            dust_abs += abs(venue)

    return SeedPlan(
        lots=tuple(lots),
        skipped_already_held=tuple(already),
        skipped_no_price=tuple(no_price),
        skipped_over_bound=tuple(over),
        real_books=real_n, real_abs_base=real_abs,
        dust_books=dust_n, dust_abs_base=dust_abs,
        truncated=truncated,
    )


def legacy_dust_ceiling_bonus(plan: SeedPlan) -> float:
    """Extra F3 parked-dust headroom this seed justifies, and no more.

    Raised once, by exactly the legacy dust imported.  Dust created after
    startup still competes for the original ceiling, so F3's overflow signal --
    the thing that says a purge is overdue -- keeps working.
    """
    return max(0.0, float(plan.dust_abs_base))
