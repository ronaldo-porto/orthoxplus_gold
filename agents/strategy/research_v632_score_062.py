# SPDX-License-Identifier: MIT
"""v6.3.2 S1: the agent's own score, on the arithmetic the mainnet validator runs now.

Why (measured 09-25 against the validator's published gauges; upstream taos-im 2564fa5, "0.6.2"):

* Mainnet runs 0.6.2 (it publishes gauges only 0.6.2 has).  Its launch values change the skill leg: kappa needs at
  least ``skill_min_books`` = 20 books over the floor (4 before), a skill leg filled on fewer than
  (1 - ``skill_max_inactive_books`` = 0.375) of the field's books is scaled by its coverage, and the windows are the
  kappa lookback on the validator's 600-s sampled history keys.  The agent's mirrors kept 60-s buckets (alpha) and
  raw timestamps pruned every state (making); the validator also finalises a pending capture on its sampled clock.
* At the 09-25 simulation seam the validator did not run 0.6.2's history shift: every uid registered before the old
  sim's last 3 h kept that block (its keys stay above the new clock, so it is never pruned inside the new simulation)
  and its inventory was carried over.  Validator making = old block + new window for the 129 such uids (corr 1.000);
  UID 94's per-book alpha matches only with the carried inventory (corr 0.9945); its skill -0.147 is reproduced at
  -0.150.  The agent's mirrors discard their window at a seam, so they could not see what the validator scores.

What this module does (telemetry: it decides nothing):

* ``skill_062`` -- the 0.6.2 skill leg for one uid's per-book alphas: kappa over the books whose |alpha| clears the
  floor, zero below ``min_books``, scaled by coverage (books filled / the required share of the field's books).  The
  P11 counterparty factor is not modelled (it needs every taker's flow; UID 94's is 0.98-0.99).
* ``SeamBlock`` / ``validator_view`` -- what a validator that skips the shift scores: every retained block's sums plus
  the live window's, each block's inventory terms including the inventory carried into it.  The first print after a
  seam, which marks the carried inventory on the jump between the two simulations' prices, is not modelled (it leaves
  the window 3 sim-h after the seam).
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from research_v6211_score_logic import kappa_of_alpha

V632_SCORE_062_VERSION = "score_062_v6_3_2"

SKILL_MIN_BOOKS = 20                   # scoring.debeta.skill_min_books, 0.6.2 launch value
SKILL_MAX_INACTIVE_BOOKS = 0.375       # scoring.debeta.skill_max_inactive_books, 0.6.2 launch value
FIELD_BOOKS = 128                      # the coverage denominator: the widest coverage in the field (the whole board)
SAMPLE_NS = 600_000_000_000            # scoring.activity.trade_volume_sampling_interval
PRUNE_EVERY_NS = 60_000_000_000        # the validator's prune cadence


def kappa_floored_062(book_alphas: Iterable[Any], floor: float, min_books: int = SKILL_MIN_BOOKS) -> float:
    """0.6.2 ``kappa_floored``: kappa over the books whose |alpha| clears the floor, 0 below max(4, min_books)."""
    q = [float(x) for x in book_alphas if abs(float(x)) >= float(floor)]
    return kappa_of_alpha(q) if len(q) >= max(4, int(min_books or 4)) else 0.0


def coverage_factor(filled_books: int, *, max_inactive: float = SKILL_MAX_INACTIVE_BOOKS,
                    total_books: int = FIELD_BOOKS) -> float:
    """0.6.2 ``coverage_weight`` for one uid: min(1, n / ((1 - max_inactive) x total)); 0 with no filled book."""
    n = int(filled_books)
    if not max_inactive or float(max_inactive) <= 0:
        return 1.0
    required = (1.0 - float(max_inactive)) * float(total_books)
    if required <= 0:
        return 1.0
    return min(1.0, n / required) if n > 0 else 0.0


def skill_062(book_alphas: dict, floor: float, *, min_books: int = SKILL_MIN_BOOKS,
              max_inactive: float = SKILL_MAX_INACTIVE_BOOKS, total_books: int = FIELD_BOOKS) -> dict[str, Any]:
    """The skill leg for one uid from {book: alpha} over its traded books (P11 not modelled)."""
    vals = [float(v) for v in (book_alphas or {}).values() if v is not None and math.isfinite(float(v))]
    q = [a for a in vals if abs(a) >= float(floor)]
    kappa = kappa_floored_062(vals, floor, min_books)
    cov = coverage_factor(len(vals), max_inactive=max_inactive, total_books=total_books)
    return {
        "skill": round(kappa * cov, 4), "kappa": round(kappa, 4), "coverage_factor": round(cov, 4),
        "books": len(vals), "books_over_floor": len(q), "over_floor_positive": sum(1 for a in q if a > 0),
        "alpha_sum": round(sum(vals), 3), "floor": float(floor), "min_books": int(min_books),
    }


def making_per_book(buy: dict, sell: dict) -> float:
    """sum_b 2*min(buy_b, sell_b), clamped at 0 on the total (``balanced_reward_per_book`` for one uid)."""
    tot = 0.0
    for b in set(buy or {}) | set(sell or {}):
        tot += 2.0 * min(float((buy or {}).get(b, 0.0)), float((sell or {}).get(b, 0.0)))
    return max(0.0, tot)


def alpha_sums(mirror: Any) -> dict[str, dict]:
    """A copy of an OwnAlphaMirror's running window sums (per book)."""
    return {name: dict(getattr(mirror, name, {}) or {}) for name in ("mtm", "invsum", "invn", "drift", "fills")}


class SeamBlock:
    """One simulation's last window as a validator that skips the history shift keeps it."""

    __slots__ = ("sums", "buy", "sell", "inventory", "carried_in", "ts")

    def __init__(self, *, sums: dict, buy: dict, sell: dict, inventory: dict, carried_in: dict, ts: int | None):
        self.sums = {k: dict(v) for k, v in (sums or {}).items()}
        self.buy = dict(buy or {})
        self.sell = dict(sell or {})
        self.inventory = dict(inventory or {})        # the uid's inventory the mirror held at the seam
        self.carried_in = dict(carried_in or {})      # inventory already carried into this block's simulation
        self.ts = ts

    def snapshot(self) -> dict[str, Any]:
        return {"ts": self.ts, "books": len(self.sums.get("invn", {})), "making": round(making_per_book(self.buy, self.sell), 3),
                "inventory_abs": round(sum(abs(float(v)) for v in self.inventory.values()), 4)}


def carried(blocks: Iterable[SeamBlock]) -> dict[int, float]:
    """The inventory a shift-skipping validator still holds from every seam (each block's own + what it carried in)."""
    out: dict[int, float] = {}
    for blk in blocks or ():
        for b, v in blk.inventory.items():
            out[int(b)] = out.get(int(b), 0.0) + float(v or 0.0)
    return out


def _book_alpha(mtm: float, invsum: float, invn: float, drift: float) -> float | None:
    return None if invn <= 0 else mtm - invsum / invn * drift


def validator_view(blocks: list, sums_now: dict, buy_now: dict, sell_now: dict) -> tuple[dict, float]:
    """Per-book alpha over the traded books and making, as a shift-skipping validator scores them.

    Each window's inventory terms include the inventory carried into it: mtm + carry x drift, invsum + carry x prints.
    """
    books: set[int] = set()
    parts = []
    for blk in blocks or ():
        parts.append((blk.sums, blk.carried_in))
    parts.append((sums_now, carried(blocks)))
    tot = {k: {} for k in ("mtm", "invsum", "invn", "drift")}
    for sums, carry in parts:
        for b in set(sums.get("invn", {})) | set(sums.get("mtm", {})):
            b = int(b)
            books.add(b)
            n = float(sums.get("invn", {}).get(b, 0.0)); dr = float(sums.get("drift", {}).get(b, 0.0))
            c = float(carry.get(b, 0.0))
            tot["mtm"][b] = tot["mtm"].get(b, 0.0) + float(sums.get("mtm", {}).get(b, 0.0)) + c * dr
            tot["invsum"][b] = tot["invsum"].get(b, 0.0) + float(sums.get("invsum", {}).get(b, 0.0)) + c * n
            tot["invn"][b] = tot["invn"].get(b, 0.0) + n
            tot["drift"][b] = tot["drift"].get(b, 0.0) + dr
    buy: dict[int, float] = {}
    sell: dict[int, float] = {}
    for src_b, src_s in [(blk.buy, blk.sell) for blk in (blocks or ())] + [(buy_now, sell_now)]:
        for b, v in (src_b or {}).items():
            buy[int(b)] = buy.get(int(b), 0.0) + float(v)
        for b, v in (src_s or {}).items():
            sell[int(b)] = sell.get(int(b), 0.0) + float(v)
    traded = set(buy) | set(sell)
    alphas = {}
    for b in books & traded:
        a = _book_alpha(tot["mtm"].get(b, 0.0), tot["invsum"].get(b, 0.0), tot["invn"].get(b, 0.0), tot["drift"].get(b, 0.0))
        if a is not None:
            alphas[b] = a
    return alphas, making_per_book(buy, sell)
