# SPDX-License-Identifier: MIT
"""A1.9.9: a simulation clock rewind is a session boundary for venue-coupled state.

Measured on the A1.9.8 run, log 20260914_012543.  Tick 2,466 was stamped
57,157 s and tick 2,467 57,121 s.  A validator restarts its simulator from the
latest checkpoint (taos/im/validator/update.py), so the venue went back 36 s
while the agent kept everything it had recorded in them.  The first trace was a
replayed trade: book 81, trade 35947, skipped by the A1.7.4.1 de-duplicator on
tick 2,466.  Reconciliation read 0 diverged books at tick 2,450 and 6 at tick
2,475, 1.2498 BASE in total, and all six stayed diverged to tick 4,000:

    book     local     venue
     102   -0.0120   +0.2380   a long the agent had closed, open again
      17   -0.0011   -0.2511   a short nothing manages
      81   -0.2530   -0.0030   a short that no longer exists
      32   +0.2476   -0.0020   a long that no longer exists
     114   +0.3825   +0.1326
       7   -0.0006   -0.0003

Book 102 then sold 0.25 at 326.50 on a book the tracker read as flat, and bought
it back at 338.79 as an ABSOLUTE exit: -3.09 on the tracker, 5.0% of the run's
cubic downside.  At the venue that sale closed the resurrected long and the
"exit" opened it again.

Two existing rules are right and stay: ``should_reset_on_timestamp_rewind`` keeps
scoring history across a rewind, and the trade de-duplicator outlives sessions.
The frozen SIM_ID_CHANGE transition is not the tool: it empties the tracker on
every quarantine tick, which after a same-simulation rewind would orphan every
real position.

What a rewind invalidates, and what this controller does about each:

    ORDERS     Orders placed after the checkpoint never existed; orders cancelled
               after it are live again until their TTL.  Every local order
               registry is cleared when the rewind is detected, before the
               state's trades are ingested.
    INVENTORY  The tracker holds fills the venue undid.  While a resync window is
               open, each book whose local base differs from venue truth is
               rebuilt from the venue: a lot at the current mid, dust in the
               ledger, or nothing.  A real position without a believable price
               waits, and only its own book is blocked meanwhile.
    EXPOSURE   Nothing opens or adds exposure while the window is open.  It closes
               on the first clean tick after every resurrected order's TTL has run
               out, and in any case after A199_RESYNC_MAX_TICKS.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Iterable

from research_direct_legacy_baseline import snap_quantity
from research_v5_dust_liveness import clip_split

A199_SESSION_EPOCH_VERSION = "direct_session_epoch_v4_16_2_a1_9_9"
EPOCH_TIMESTAMP_REWIND = "TIMESTAMP_REWIND"

# The longest-lived resting order this build sends is the 4,000 ms profitable
# exit (PARAMS research_profitable_exit_ttl_ms); entry quotes rest 3,000 ms.  At
# the 1,000 ms publish interval eight states outlive both, plus the one-state lag
# of a removal notice.
A199_RESYNC_MIN_TICKS = 8
# A divergence that returns on every tick must not block entries for good.
A199_RESYNC_MAX_TICKS = 32
# The A1.9.5 reconcile tolerance: below this a difference is float noise.
A199_RESEED_TOLERANCE_BASE = 1e-9

RESEED_KEEP = "KEEP"
RESEED_REAL = "REAL"
RESEED_DUST = "DUST"
RESEED_CLEAR = "CLEAR"
RESEED_DEFER = "DEFER"
# v5.0.2 F2: a clip the venue's settlement left a unit or two short -- one lot of min_order,
# with the shortfall in the residue ledger.
RESEED_CLIP = "CLIP"

LIMIT = "PLACE_ORDER_LIMIT"
MARKET = "PLACE_ORDER_MARKET"
BUY = 0
SELL = 1

# Order-lifecycle registries a rewind invalidates.  Each name is an attribute of
# the agent; tests/test_research_a1_9_9_controllers.py fails if one disappears.
A199_EPOCH_CLEAR_MAPS: tuple[str, ...] = (
    # A1.7.4.3 in-flight placements, A1.7.4.3.2 exchange order identities
    "_direct_pending_exposure_orders",
    "_direct_exchange_order_ownership",
    # A1.7.2 per-tick exit authority, A1.7.3.1 partial remainders
    "_direct_exit_authority_last",
    "_direct_partial_recovery",
    # A1.9.0.x resting-exit observation, A1.9.1 verdicts and reprice releases
    "_a19_exit_seen",
    "_a19_tick_seen",
    "_a19_pending_action",
    "_a19_cancel_watch",
    "_a19_cancel_reason",
    "_a19_identity_acked",
    "_a191_verdicts",
    "_a191_reprice_release",
    # A1.9.7 exit gaps and flip watch, A1.9.6.1 taker decisions awaiting a fill
    "_a197_exit_gap",
    "_a197_postfill_watch",
    "_a1961_taker_decision",
    # frozen base: live quotes and their mid history, reject guards
    "_research_quote_store",
    "_research_contract_reject_state",
    "_research_execution_reject_last",
)

# Per-position runtime of a book whose inventory is rebuilt.
A199_RESEED_BOOK_MAPS: tuple[str, ...] = (
    "_position_ticks",
    "_research_position_tick_seen",
    "_research_exit_attempts",
    "_research_peak_taker_net_bps",
    "_research_unified_exit_last",
    "_research_parked_inventory",
    "_inventory_reason",
    "_direct_maker_open",
    "_direct_tail_history",
    "_direct_tail_recovery_active",
    "_direct_a1744_veto_active",
    "_direct_a175_tail_budget_spent",
    "_a196_inherited_real",
)
A199_RESEED_BOOK_SETS: tuple[str, ...] = ("_direct_a175_tail_budget_exhausted",)


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _int(value: Any) -> int | None:
    number = _finite(value)
    return None if number is None else int(number)


# ---- detection ----------------------------------------------------------------------

def detect_rewind(*, last_ts: Any, ts: Any, last_sim_id: Any, sim_id: Any) -> dict[str, Any] | None:
    """A state stamped earlier than the previous one, in the same simulation."""
    now = _int(ts)
    last = _int(last_ts)
    if now is None or last is None or now <= 0 or last <= 0:
        return None
    if last_sim_id and sim_id and str(last_sim_id) != str(sim_id):
        # A new simulation: the frozen SIM_ID_CHANGE transition owns it.
        return None
    if now < last:
        return {
            "reason": EPOCH_TIMESTAMP_REWIND,
            "old_ts": int(last),
            "new_ts": int(now),
            "rewind_s": (now - last) / 1e9,
        }
    return None


# ---- inventory ----------------------------------------------------------------------

@dataclass(frozen=True)
class BookReseed:
    book_id: int
    action: str
    venue_net: float
    local_before: float
    tracker_before: float
    target: float
    tracker_after: float
    ledger_delta: float
    price: float | None

    def as_log(self) -> dict[str, Any]:
        return {
            "a199_session_epoch_version": A199_SESSION_EPOCH_VERSION,
            "book": int(self.book_id),
            "action": self.action,
            "venue_net": round(self.venue_net, 8),
            "local_before": round(self.local_before, 8),
            "tracker_before": round(self.tracker_before, 8),
            "target": round(self.target, 8),
            "tracker_after": round(self.tracker_after, 8),
            "ledger_delta": round(self.ledger_delta, 8),
            "price": self.price,
        }


def plan_book_reseed(
    *,
    book_id: Any,
    venue_net: Any,
    tracker_net: Any,
    ledger: Any,
    fee_residue: Any,
    pending: Any,
    min_order: Any,
    volume_decimals: Any = None,
    mid: Any = None,
    ledger_enabled: bool = True,
    tolerance: float = A199_RESEED_TOLERANCE_BASE,
    clip_tolerance: float = 0.0,
) -> BookReseed:
    """What makes local base equal venue base on one book.

    Local base is tracker + legacy ledger + fee residue + pending seed, exactly as
    A1.9.5 reconciliation reads it.  The ledger and the residue stay: they are
    BASE no lot can carry.  A pending seed is folded into the rebuilt book.  The
    tracker must hold the rest of venue truth.

    v5.0.2 F2: a target at most ``clip_tolerance`` below the minimum order is one clip of
    exactly min_order (RESEED_CLIP), and the shortfall is residue.  A1.9.9 sent it to the
    ledger, where nothing exits it.  ``clip_tolerance`` 0.0 is A1.9.9.
    """
    venue = _finite(venue_net, 0.0) or 0.0
    tracker = _finite(tracker_net, 0.0) or 0.0
    kept = (_finite(ledger, 0.0) or 0.0) + (_finite(fee_residue, 0.0) or 0.0)
    local = tracker + kept + (_finite(pending, 0.0) or 0.0)
    target = float(snap_quantity(venue - kept, volume_decimals))
    price = _finite(mid)
    if price is not None and price <= 0.0:
        price = None
    floor = abs(_finite(min_order, 0.25) or 0.25)
    tol = abs(_finite(tolerance, A199_RESEED_TOLERANCE_BASE) or A199_RESEED_TOLERANCE_BASE)

    def plan(action: str, *, tracker_after: float = 0.0, ledger_delta: float = 0.0,
             px: float | None = None) -> BookReseed:
        return BookReseed(
            book_id=int(book_id), action=action, venue_net=venue, local_before=local,
            tracker_before=tracker, target=target, tracker_after=float(tracker_after),
            ledger_delta=float(ledger_delta), price=px,
        )

    if abs(venue - local) <= tol:
        return plan(RESEED_KEEP, tracker_after=tracker)
    if abs(target) <= tol:
        return plan(RESEED_CLEAR)
    if abs(target) + 1e-12 >= floor or not ledger_enabled:
        if price is None:
            return plan(RESEED_DEFER)
        return plan(RESEED_REAL, tracker_after=target, px=price)
    clip = clip_split(target, min_order=floor, tolerance=clip_tolerance)
    if clip is not None:
        if price is None:
            return plan(RESEED_DEFER)
        return plan(RESEED_CLIP, tracker_after=clip[0], ledger_delta=clip[1], px=price)
    return plan(RESEED_DUST, ledger_delta=target)


def clear_epoch_registries(agent: Any, names: Iterable[str] = A199_EPOCH_CLEAR_MAPS) -> dict[str, int]:
    """Empty every named registry the agent has.  Returns rows dropped, by name."""
    dropped: dict[str, int] = {}
    for name in names:
        registry = getattr(agent, name, None)
        clearer = getattr(registry, "clear", None)
        if registry is None or not callable(clearer):
            continue
        try:
            rows = len(registry)
        except TypeError:
            rows = 0
        clearer()
        dropped[name] = int(rows)
    return dropped


def clear_book_runtime(
    agent: Any,
    book_id: Any,
    *,
    maps: Iterable[str] = A199_RESEED_BOOK_MAPS,
    sets: Iterable[str] = A199_RESEED_BOOK_SETS,
) -> list[str]:
    """Drop one book's per-position runtime.  Returns the names that held any."""
    bid = int(book_id)
    held: list[str] = []
    for name in maps:
        table = getattr(agent, name, None)
        if isinstance(table, dict) and bid in table:
            table.pop(bid, None)
            held.append(name)
    for name in sets:
        members = getattr(agent, name, None)
        if isinstance(members, set) and bid in members:
            members.discard(bid)
            held.append(name)
    return held


# ---- exposure -----------------------------------------------------------------------

def _book_of(instruction: Any) -> int | None:
    for name in ("bookId", "book_id"):
        raw = getattr(instruction, name, None)
        if raw is not None:
            try:
                return int(raw)
            except (TypeError, ValueError):
                return None
    return None


def order_direction(instruction: Any) -> int | None:
    raw = getattr(instruction, "direction", None)
    raw = getattr(raw, "value", raw)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        text = str(raw or "").upper()
        if "BUY" in text:
            return BUY
        if "SELL" in text:
            return SELL
        return None
    return value if value in (BUY, SELL) else None


def exposure_increasing(
    *, kind: str, direction: int | None, quantity: Any, net_base: Any, eps: Any, deferred: bool,
) -> bool:
    """True when a placement would open, add to or flip a position."""
    if kind not in (LIMIT, MARKET):
        return False
    if deferred:
        return True
    net = _finite(net_base, 0.0) or 0.0
    e = max(abs(_finite(eps, 0.0) or 0.0), 1e-9)
    if abs(net) <= e or direction is None:
        return True
    reduces = (net > 0.0 and direction == SELL) or (net < 0.0 and direction == BUY)
    if not reduces:
        return True
    return abs(_finite(quantity, 0.0) or 0.0) > abs(net) + e


def strip_exposure_increasing(
    instructions: Any,
    *,
    net_by_book: dict[int, float],
    deferred: Iterable[int] = (),
    eps: Any = 0.0,
    only_books: Iterable[int] | None = None,
) -> list[tuple[int, str]]:
    """Remove, in place, every placement that opens or adds exposure.

    Cancels always stay.  ``only_books`` limits the filter to those books.
    Returns (book, instruction type) for each removal.
    """
    if not isinstance(instructions, list):
        return []
    deferred_books = {int(b) for b in deferred}
    scope = None if only_books is None else {int(b) for b in only_books}
    kept: list[Any] = []
    removed: list[tuple[int, str]] = []
    for instruction in instructions:
        kind = str(getattr(instruction, "type", "") or "")
        book = _book_of(instruction)
        if (
            book is not None
            and kind in (LIMIT, MARKET)
            and (scope is None or book in scope)
            and exposure_increasing(
                kind=kind,
                direction=order_direction(instruction),
                quantity=getattr(instruction, "quantity", None),
                net_base=net_by_book.get(book, 0.0),
                eps=eps,
                deferred=book in deferred_books,
            )
        ):
            removed.append((book, kind))
            continue
        kept.append(instruction)
    if removed:
        instructions[:] = kept
    return removed
