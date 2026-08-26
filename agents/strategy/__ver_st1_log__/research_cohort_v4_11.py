# SPDX-License-Identifier: MIT
"""V4.11 sticky Kappa cohort selection.

The score engine needs concentration, not broad one-shot coverage.  This module
keeps economically feasible books in a small acquisition cohort until they are
score-qualified (or become unsafe), then rotates in the next best candidate.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence


@dataclass(frozen=True)
class CohortCandidate:
    book_id: int
    observations_remaining: int
    entry_feasible: bool = True
    economics_ok: bool = True
    hard_risk: bool = False
    has_inventory: bool = False
    score_qualified: bool = False
    needs_refresh: bool = False
    refresh_urgency: float = 0.0
    cheap_score: float = 0.0


def _valid(c: CohortCandidate) -> bool:
    if c.hard_risk or c.has_inventory:
        return False
    if not c.entry_feasible or not c.economics_ok:
        return False
    # Stable score-qualified books leave the acquisition cohort; expiring ones
    # may re-enter for refresh.
    if c.score_qualified and not c.needs_refresh:
        return False
    return True


def _priority(c: CohortCandidate) -> tuple:
    remaining = max(0, int(c.observations_remaining or 0))
    urgency = max(0.0, min(1.0, float(c.refresh_urgency or 0.0)))
    if remaining == 1:          # already has two observations
        tier = 0
    elif c.needs_refresh and urgency >= 0.75:
        tier = 1
    elif remaining == 2:        # already has one observation
        tier = 2
    elif c.needs_refresh:
        tier = 3
    else:                       # uncovered/new book
        tier = 4
    return (tier, -urgency, -float(c.cheap_score or 0.0), int(c.book_id))


def update_sticky_cohort(
    previous: Sequence[int] | None,
    candidates: Iterable[CohortCandidate],
    *,
    target_size: int = 10,
    exploration_slots: int = 1,
) -> list[int]:
    """Return a stable, bounded acquisition cohort.

    Existing valid members are retained first. Vacancies are filled by
    finish-before-rotate priority. ``exploration_slots`` intentionally allows a
    small amount of fresh discovery without churning the whole cohort.
    """
    target = max(1, min(32, int(target_size)))
    explore = max(0, min(target, int(exploration_slots)))
    rows = {int(c.book_id): c for c in candidates}

    kept: list[int] = []
    for raw in previous or ():
        bid = int(raw)
        c = rows.get(bid)
        if c is None or not _valid(c):
            continue
        if bid not in kept:
            kept.append(bid)
        if len(kept) >= target:
            return kept[:target]

    pool = [c for c in rows.values() if _valid(c) and int(c.book_id) not in kept]
    pool.sort(key=_priority)

    # Reserve at most ``explore`` slots for uncovered books. Progress books can
    # use every slot; brand-new books cannot evict work already in progress.
    progress = [c for c in pool if max(0, int(c.observations_remaining or 0)) <= 2 or c.needs_refresh]
    fresh = [c for c in pool if c not in progress]
    for c in progress:
        if len(kept) >= target:
            break
        kept.append(int(c.book_id))
    fresh_budget = min(explore, target - len(kept))
    for c in fresh[:fresh_budget]:
        kept.append(int(c.book_id))

    # If the cohort is still undersized (startup), fill the rest with the best
    # feasible fresh books. Sticky retention prevents this from recurring every tick.
    if len(kept) < target:
        used = set(kept)
        for c in fresh[fresh_budget:]:
            if len(kept) >= target:
                break
            if int(c.book_id) not in used:
                kept.append(int(c.book_id))
                used.add(int(c.book_id))
    return kept[:target]


def cohort_state(*, observations_remaining: int, score_qualified: bool, needs_refresh: bool) -> str:
    if needs_refresh:
        return "EXPIRING"
    if score_qualified:
        return "QUALIFIED"
    remaining = max(0, int(observations_remaining or 0))
    if remaining <= 0:
        return "OBS_QUALIFIED"
    if remaining == 1:
        return "ONE_AWAY"
    if remaining == 2:
        return "BUILDING"
    return "NEW"
