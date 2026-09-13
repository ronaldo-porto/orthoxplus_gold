# SPDX-License-Identifier: MIT
"""A1.9.6.1: three places the agent's picture of the venue was wrong.

Measured on the first A1.9.6 run, log 20260913_010511 (stopped at tick 2,326).

SEED PRICING.  The startup seed priced inherited lots at (bids[0] + asks[0]) / 2
without checking that the quote was a market.  Both inherited books were crossed
on tick 1: book 39 bid 330.10 / ask 183.73 became an entry of 256.925, book 99
bid 411.37 / ask 285.28 became 348.325.  The exit logic then acted on +2,844 and
+1,805 bps of profit that never existed.  Book 99 was crossed on 2,317 of 2,326
exit evaluations, never ranked, and held 0.6524 BASE and an active slot for the
whole run.  The frozen `_research_book_mid` already refuses `ask < bid`; the seed
did not.  The fake side was a stale bid above the market: book 39's bid read
330.12 while its ask tracked the real price from 184 up to 350, and the agent's
buy-back filled at 350.8.  The fake entry hid that rise from the loss protection
until the ask crossed 256.925 around tick 750.  A lot whose book has no believable
quote now waits -- charged to exposure and covered by the F10 inherited
allowance, but with no exit logic until it can be priced -- and is seeded at the
first mid that has been valid for A1961_SEED_VALID_STREAK consecutive ticks.

BASE-DENOMINATED FEES.  ClearingManager::handleTrade takes a positive fee on the
side that receives BASE in BASE, `util::roundUp(fee / price, baseIncrementDecimals)`,
and refunds the over-collection in quote.  The tracker records the full traded
volume, so every fee-paying buy leaves the venue at least one unit below it.
Predicted units matched the observed divergence on 12 of 12 detailed books at
tick 500 and 12 of 12 at tick 2,325; 37 books had drifted by the end.  The amount
now goes to a fee-residue ledger, so tracker + ledgers equals the venue while
FLAT, entry eligibility and round-trip accounting stay exactly as they were.

THE ABORT RULE.  F8 bounds a taker exit against the market at the decision, and
across 89 taker exits it held: realized minus the decision-time estimate ranged
-7.8 to +22.8 bps.  But 62 of the 89 were already past the -25 bps floor when they
were decided, so "no round trip past its floor" measured trigger timing, not F8.
A1961_TAKER_OUTCOME reports the two separately.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal, InvalidOperation
import math
from typing import Any, Iterable

from research_direct_legacy_baseline import snap_quantity

A1961_VENUE_INTEGRITY_VERSION = "direct_venue_integrity_v4_16_2_a1_9_6_1"

# A deferred seed believes a mid only after this many consecutive valid readings.
# The stale bid came and went: book 39 read clean (195.86 / 195.87) on ticks
# 99-107 and book 99 (314-325) on ticks 1,550-1,558, crossed either side.  Replayed
# on that log, this rule seeds book 39 at 195.865 on tick 101 and book 99 near
# 321.4 on tick 1,552 -- both real prices.  One clean tick is not evidence.
A1961_SEED_VALID_STREAK = 3

# F8 bounds execution at the declared floor's magnitude.  An exit breaches only
# when it realizes more than this much worse than its own decision-time estimate.
A1961_SLIPPAGE_BOUND_BPS = 25.0
A1961_DECLARED_FLOOR_BPS = -25.0

# A taker outcome is attributed to the latest taker decision on its book only
# inside this window; anything older is reported as unmatched.
A1961_DECISION_MATCH_TICKS = 10

# How deep to look when checking that level 0 really is the best price.
A1961_TOUCH_SCAN_LEVELS = 64

SEED_WAIT = "WAIT"
SEED_NOW = "SEED"
SEED_DROP = "DROP"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _decimals(value: Any) -> int | None:
    try:
        d = int(value)
    except (TypeError, ValueError):
        return None
    return d if 0 <= d <= 12 else None


def _price(level: Any) -> float | None:
    try:
        p = float(getattr(level, "price"))
    except (TypeError, ValueError, AttributeError):
        return None
    return p if math.isfinite(p) and p > 0.0 else None


def _has_size(level: Any) -> bool:
    """A level that reports no positive quantity is not a price.  Unknown size passes."""
    raw = getattr(level, "quantity", None)
    if raw is None:
        return True
    try:
        q = float(raw)
    except (TypeError, ValueError):
        return False
    return math.isfinite(q) and q > 0.0


# ---- seed pricing --------------------------------------------------------------

def valid_touch(bids: Any, asks: Any, *, scan_levels: int = A1961_TOUCH_SCAN_LEVELS) -> tuple[float, float] | None:
    """Best bid and ask when they describe a market, otherwise None.

    Rejects what the frozen `_research_book_mid` rejects -- a missing,
    non-positive or crossed touch -- plus one thing it cannot see: a level 0 that a
    deeper level on the same side beats.  The strategy reads levels in payload
    order without sorting, so an out-of-order level 0 is a wrong price even when
    the book is not crossed.
    """
    try:
        if bids is None or asks is None or len(bids) == 0 or len(asks) == 0:
            return None
        bid = _price(bids[0])
        ask = _price(asks[0])
    except Exception:
        return None
    if bid is None or ask is None or ask < bid:
        return None
    try:
        if not (_has_size(bids[0]) and _has_size(asks[0])):
            return None
    except Exception:
        return None
    try:
        for i in range(1, min(len(bids), max(1, int(scan_levels)))):
            p = _price(bids[i])
            if p is not None and p > bid:
                return None
        for i in range(1, min(len(asks), max(1, int(scan_levels)))):
            p = _price(asks[i])
            if p is not None and p < ask:
                return None
    except Exception:
        return None
    return bid, ask


def touch_mid(bids: Any, asks: Any) -> float | None:
    touch = valid_touch(bids, asks)
    return None if touch is None else 0.5 * (touch[0] + touch[1])


@dataclass(frozen=True)
class UnpricedRoute:
    ledger: dict[int, float]
    pending: dict[int, float]
    over_bound: tuple[int, ...]
    snapped: int

    @property
    def ledger_abs(self) -> float:
        return sum(abs(v) for v in self.ledger.values())

    @property
    def pending_abs(self) -> float:
        return sum(abs(v) for v in self.pending.values())

    def as_log(self) -> dict[str, Any]:
        return {
            "a1961_venue_integrity_version": A1961_VENUE_INTEGRITY_VERSION,
            "a1961_unpriced_ledger_books": len(self.ledger),
            "a1961_unpriced_ledger_abs": round(self.ledger_abs, 6),
            "a1961_pending_books": sorted(self.pending),
            "a1961_pending_abs": round(self.pending_abs, 6),
            "a1961_unpriced_over_bound": len(self.over_bound),
            "a1961_unpriced_snapped": int(self.snapped),
        }


def route_unpriced_books(
    *,
    books: Iterable[Any],
    venue_net_by_book: dict[int, float],
    min_order: Any,
    volume_decimals: Any,
    remaining_books: Any,
    remaining_abs: Any,
    ledger_enabled: bool = True,
    grid_snap: bool = True,
    eps: float = 1e-9,
) -> UnpricedRoute:
    """Route the books the seed could not price.

    Legacy dust needs no price -- the ledger holds BASE only -- so it joins the
    ledger exactly as if it had been priced.  A REAL lot enters the tracker with a
    cost basis, so it waits.  With the ledger off, everything waits, as in A1.9.5.
    The seeding bounds still apply, biggest positions first.
    """
    floor = max(1e-12, abs(_finite(min_order, 0.25)))
    ids: list[int] = []
    for raw in books or ():
        try:
            ids.append(int(raw))
        except (TypeError, ValueError):
            continue
    ids = sorted(set(ids), key=lambda b: -abs(_finite(venue_net_by_book.get(b))))
    cap_books = max(0, int(_finite(remaining_books)))
    cap_abs = max(0.0, _finite(remaining_abs))
    ledger: dict[int, float] = {}
    pending: dict[int, float] = {}
    over: list[int] = []
    snapped = 0
    used_books = 0
    used_abs = 0.0
    for book in ids:
        raw = _finite(venue_net_by_book.get(book))
        net = snap_quantity(raw, volume_decimals) if grid_snap else raw
        if net != raw:
            snapped += 1
        if abs(net) <= abs(_finite(eps, 1e-9)):
            continue
        if used_books >= cap_books or used_abs + abs(net) > cap_abs + 1e-12:
            over.append(book)
            continue
        used_books += 1
        used_abs += abs(net)
        if abs(net) >= floor or not ledger_enabled:
            pending[book] = net
        else:
            ledger[book] = net
    return UnpricedRoute(ledger=ledger, pending=pending, over_bound=tuple(over), snapped=snapped)


@dataclass(frozen=True)
class PendingStep:
    action: str
    streak: int
    mid: float | None
    reason: str


def pending_seed_step(
    *,
    streak: Any,
    touch: tuple[float, float] | None,
    venue_net: Any,
    local_net: Any,
    eps: Any,
    required: int = A1961_SEED_VALID_STREAK,
) -> PendingStep:
    """One tick of a deferred seed.  Pure: the caller owns the state.

    The streak resets whenever the evidence breaks: no venue reading, a quote that
    is not a market, or a tracker that already holds the book -- seeding then would
    double count, and the lot stays charged to exposure while it waits.
    """
    tol = abs(_finite(eps, 5e-5))
    if venue_net is None:
        return PendingStep(SEED_WAIT, 0, None, "NO_VENUE_READING")
    net = _finite(venue_net)
    if abs(net) <= tol:
        return PendingStep(SEED_DROP, 0, None, "VENUE_FLAT")
    if abs(_finite(local_net)) > tol:
        return PendingStep(SEED_WAIT, 0, None, "TRACKER_HOLDS_BOOK")
    if touch is None:
        return PendingStep(SEED_WAIT, 0, None, "INVALID_TOUCH")
    run = max(0, int(_finite(streak))) + 1
    mid = 0.5 * (float(touch[0]) + float(touch[1]))
    if run >= max(1, int(required)):
        return PendingStep(SEED_NOW, run, mid, "VALID_STREAK")
    return PendingStep(SEED_WAIT, run, mid, "BUILDING_STREAK")


# ---- BASE-denominated fees -------------------------------------------------------

def base_fee_units(*, agent_buy: bool, fee: Any, price: Any, base_decimals: Any) -> int:
    """BASE units the venue deducts for a fill's fee; 0 when it takes none in BASE.

    Mirrors ClearingManager::handleTrade: only the side that receives BASE -- a buy,
    as maker or taker -- pays in BASE, only a positive fee is taken, and the amount
    is `roundUp(fee / price, baseIncrementDecimals)`.
    """
    if not agent_buy:
        return 0
    d = _decimals(base_decimals)
    if d is None:
        return 0
    f = _finite(fee)
    p = _finite(price)
    if f <= 0.0 or p <= 0.0:
        return 0
    try:
        amount = (Decimal(repr(f)) / Decimal(repr(p))).quantize(
            Decimal(1).scaleb(-d), rounding=ROUND_CEILING,
        )
        return max(0, int(amount.scaleb(d).to_integral_value()))
    except (InvalidOperation, ValueError, OverflowError):
        return 0


def apply_fee_residue(ledger: dict[int, float], *, book_id: Any, units: Any, base_decimals: Any) -> float:
    """Charge `units` of BASE to one book's fee residue.  Returns the book's new value."""
    key = int(book_id)
    d = _decimals(base_decimals)
    n = int(_finite(units))
    if d is None or n <= 0:
        return _finite((ledger or {}).get(key))
    value = round(_finite(ledger.get(key)) - n * (10.0 ** (-d)), d) + 0.0
    if value == 0.0:
        ledger.pop(key, None)
    else:
        ledger[key] = value
    return value


# ---- taker exits against their own decision ----------------------------------------

@dataclass(frozen=True)
class TakerOutcome:
    book: int
    tick: int
    realized_net_bps: float
    decision_tick: int | None
    decision_age_ticks: int | None
    decision_net_bps: float | None
    trigger: str | None
    slippage_bps: float | None
    slippage_breach: bool
    late_trigger: bool
    floor_bps: float
    slip_bound_bps: float

    def as_log(self) -> dict[str, Any]:
        def r(x):
            return None if x is None else round(float(x), 3)
        return {
            "a1961_venue_integrity_version": A1961_VENUE_INTEGRITY_VERSION,
            "book": int(self.book),
            "realized_net_bps": r(self.realized_net_bps),
            "decision_tick": self.decision_tick,
            "decision_age_ticks": self.decision_age_ticks,
            "decision_net_bps": r(self.decision_net_bps),
            "trigger": self.trigger,
            "slippage_bps": r(self.slippage_bps),
            "slippage_breach": int(self.slippage_breach),
            "late_trigger": int(self.late_trigger),
            "matched": int(self.decision_net_bps is not None),
            "floor_bps": float(self.floor_bps),
            "slip_bound_bps": float(self.slip_bound_bps),
        }


def taker_outcome(
    *,
    book: Any,
    tick: Any,
    realized_net_bps: Any,
    decision: dict[str, Any] | None = None,
    floor_bps: float = A1961_DECLARED_FLOOR_BPS,
    slip_bound_bps: float = A1961_SLIPPAGE_BOUND_BPS,
    match_ticks: int = A1961_DECISION_MATCH_TICKS,
) -> TakerOutcome:
    """Judge a completed taker round trip the way F8 can actually be judged.

    ``slippage_bps`` is realized minus the decision-time taker estimate; positive
    means the exit did better than estimated.  A breach is slippage worse than the
    bound.  ``late_trigger`` says the estimate was already past the declared floor
    when the exit was decided -- the loss existed before any order was sent.
    """
    realized = _finite(realized_net_bps)
    now = int(_finite(tick))
    d_tick = age = None
    d_net = None
    trigger = None
    if isinstance(decision, dict):
        try:
            candidate = int(decision.get("tick"))
        except (TypeError, ValueError):
            candidate = None
        if candidate is not None and 0 <= now - candidate <= max(0, int(match_ticks)):
            d_tick = candidate
            age = now - candidate
            d_net = _finite(decision.get("taker_net_bps"))
            reason = decision.get("reason")
            trigger = None if reason is None else str(reason)
    slippage = None if d_net is None else realized - d_net
    bound = abs(_finite(slip_bound_bps, A1961_SLIPPAGE_BOUND_BPS))
    floor = _finite(floor_bps, A1961_DECLARED_FLOOR_BPS)
    return TakerOutcome(
        book=int(_finite(book, -1)), tick=now, realized_net_bps=realized,
        decision_tick=d_tick, decision_age_ticks=age, decision_net_bps=d_net, trigger=trigger,
        slippage_bps=slippage, slippage_breach=bool(slippage is not None and slippage < -bound),
        late_trigger=bool(d_net is not None and d_net <= floor),
        floor_bps=floor, slip_bound_bps=bound,
    )
