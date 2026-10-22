"""A1.9.0.1 resting-order lifecycle ledger for Strategy1-Direct V4.16.2.

Why this module exists
----------------------
Every queue-preservation mechanism shipped so far reads the agent's live
orders from ``state.accounts[uid][book].orders``.  Measured against the
A1.7.5 runtime log (29,183 ticks, 33,336 accepted limit orders) that view
never contains the resting Maker exit at exit-decision time:

* ``PROFITABLE_EXIT_HOLD``            0 events
* ``A172_WAIT_CANCEL``                0 events
* ``A17431_BOOK_OWNERSHIP_BLOCK``     0 events
* ``FINAL_CONTRACT_REJECT``           0 events
* A1.9 Phase A ``resting_present``    0 of 3,746 evaluations

The exchange lifecycle stream says the opposite: a profitable Maker exit
rests for a median of 3,000 ms -- three full 1,000 ms publish cycles --
and 86% of them end in expiry rather than a fill.  The book is re-evaluated
one tick later 76.4% of the time, and 27.0% of consecutive exit placements
land while the previous exit is still live on the exchange.

So the orders are real and visible to the exchange; the account snapshot is
simply not a usable live-order source for this agent.  This ledger rebuilds
that view from the notice stream the agent already receives, which is the
same stream the exchange uses:

    PLACE_ORDER_LIMIT  ->  RDPOL (LimitOrderPlacementEvent, carries orderId)
                       ->  RDCO1 (OrderCancellationEvent)  | EVENT_TRADE

The ledger is pure state with no strategy authority.  A1.9.0.1 reads it for
measurement only; nothing in the trading path consults it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

DIRECT_EXIT_LEDGER_VERSION = "direct_exit_ledger_v4_16_2_a1_9_0_3"

# Removal causes.  These mirror the A1.9 absent-reason vocabulary so a
# lifecycle row and an exit evaluation can be joined without translation.
LEDGER_REMOVED_CANCELLED = "LEDGER_CANCELLED"
LEDGER_REMOVED_FILLED = "LEDGER_FILLED"
LEDGER_REMOVED_PARTIAL = "LEDGER_PARTIAL_FILL"
LEDGER_REMOVED_TTL_SWEEP = "LEDGER_TTL_SWEEP"

# A row is swept only well past any TTL the strategy can request.  The frozen
# base clamps research_profitable_exit_ttl_ms to [1000, 5000] ms, so 15,000 ms
# cannot discard an order the exchange still considers live.  The sweep exists
# to bound memory and to stop a dropped notice from pinning a phantom order,
# not to model expiry: expiry arrives as a real RDCO1 cancellation.
LEDGER_SWEEP_GRACE_MS = 15000.0

_MAX_TRACKED_ORDERS = 4096

# A1.9.0.3: why a row left the book, kept after the row itself is gone.  The
# observer notices the disappearance one or two states later, so the cause has
# to outlive the row or every removal reads as an expiry.
_MAX_REMOVAL_MEMO = 2048


@dataclass
class LedgerOrder:
    """One accepted limit order, as the exchange acknowledged it."""

    order_id: int
    book_id: int
    side: int
    price: float
    quantity: float
    remaining: float
    placed_ns: int
    placed_tick: int
    action: str = ""
    client_id: int | None = None

    def age_ms(self, now_ns: int | float | None) -> float:
        """Resting time in milliseconds, or 0.0 when the clock is unusable."""
        try:
            if now_ns is None:
                return 0.0
            return max(0.0, (float(now_ns) - float(self.placed_ns)) / 1e6)
        except (TypeError, ValueError):
            return 0.0


# ---- k106 F2 (reply time; every result identical) -------------------------------------------------------------------
# live_orders scanned EVERY tracked row for each call (~650 calls a state on the k106 harness, ~4 ms).  F2 keeps the
# order dict as _F2BookOrders: a dict whose every write also files the row under its book, so live_orders reads only
# that book's rows -- in the dict's own order: each order id keeps the sequence number of its first insertion (a
# re-write keeps the key's place, as the dict does; a removal and a new write move it to the end, as the dict does).
# Reads, iteration and every other dict behaviour are the dict's own.  A row without a usable book_id switches the
# index off for good (the original scan then runs, with the original outcome).  _F2_LEDGER_INDEX = False restores the
# scan.
_F2_LEDGER_INDEX = True


class _F2BookOrders(dict):
    """The ledger's order dict with a per-book index kept in step with every write (see the F2 note above)."""

    def __init__(self, *args, **kwargs):
        super().__init__()
        self._f2_books = {}                 # book_id -> {order_id: row}
        self._f2_where = {}                 # order_id -> the book_id it is filed under
        self._f2_seq = {}                   # order_id -> first-insertion sequence number
        self._f2_next = 0
        self._f2_broken = False
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def _f2_unfile(self, key):
        if key in self._f2_where:
            book = self._f2_where.pop(key)
            rows = self._f2_books.get(book)
            if rows is not None:
                rows.pop(key, None)
                if not rows:
                    del self._f2_books[book]

    def __setitem__(self, key, value):
        if key in self:
            self._f2_unfile(key)
        else:
            self._f2_seq[key] = self._f2_next
            self._f2_next += 1
        dict.__setitem__(self, key, value)
        try:
            book = value.book_id
            self._f2_books.setdefault(book, {})[key] = value
            self._f2_where[key] = book
        except Exception:
            self._f2_broken = True

    def __delitem__(self, key):
        dict.__delitem__(self, key)
        self._f2_unfile(key)
        self._f2_seq.pop(key, None)

    def pop(self, key, *default):
        if key in self:
            value = dict.pop(self, key)
            self._f2_unfile(key)
            self._f2_seq.pop(key, None)
            return value
        return dict.pop(self, key, *default)

    def popitem(self):
        key, value = dict.popitem(self)
        self._f2_unfile(key)
        self._f2_seq.pop(key, None)
        return key, value

    def clear(self):
        dict.clear(self)
        self._f2_books.clear()
        self._f2_where.clear()
        self._f2_seq.clear()

    def setdefault(self, key, default=None):
        if key not in self:
            self[key] = default
        return dict.__getitem__(self, key)

    def update(self, *args, **kwargs):
        for key, value in dict(*args, **kwargs).items():
            self[key] = value

    def _f2_book_rows(self, book_id):
        """This book's rows in the dict's own order, or None when the index cannot answer."""
        if self._f2_broken:
            return None
        try:
            rows = self._f2_books.get(book_id)
        except Exception:
            return None
        if not rows:
            return []
        seq = self._f2_seq
        return [row for _key, row in sorted(rows.items(), key=lambda item: seq[item[0]])]


def _f2_book_rows(orders, book_id):
    """The book's rows from the index, in the dict's order; None when the dict is not indexed or cannot answer."""
    if type(orders) is not _F2BookOrders:
        return None
    return orders._f2_book_rows(book_id)


def _v6_37_orders():
    """v6.37 (research_v637_fast_path): the order dict of a ledger being made, as _F2_LEDGER_INDEX stands when it is
    made -- F2's indexed dict while on, v6.36's plain dict once the agent switched the fast path off.  The agent writes
    the switch after this module's import (research_v6_37_fast_path), so a default fixed when the class was built would
    keep F2's dict for every later ledger."""
    if _F2_LEDGER_INDEX:
        return _F2BookOrders()
    return {}

@dataclass
class DirectExitLedger:
    """Live-order view rebuilt from acknowledged exchange notices."""

    if _F2_LEDGER_INDEX:
        orders: dict[int, LedgerOrder] = field(default_factory=_v6_37_orders)
    else:
        orders: dict[int, LedgerOrder] = field(default_factory=dict)
    removal_causes: dict[int, str] = field(default_factory=dict)
    accepted: int = 0
    removed: int = 0
    swept: int = 0
    unmatched_removals: int = 0

    # ---------------------------------------------------------------- writes

    def note_accepted(
        self, *, order_id, book_id, side, price, quantity,
        timestamp_ns, tick, client_id=None, action: str = "",
    ) -> bool:
        """Record an acknowledged limit order.  Returns True when stored."""
        try:
            oid = int(order_id)
            bid = int(book_id)
            sd = int(side)
            px = float(price)
            qty = float(quantity)
        except (TypeError, ValueError):
            return False
        if px <= 0.0 or qty <= 0.0:
            return False
        try:
            ts = int(timestamp_ns)
        except (TypeError, ValueError):
            ts = 0
        try:
            tk = int(tick)
        except (TypeError, ValueError):
            tk = 0
        try:
            cid = int(client_id) if client_id is not None else None
        except (TypeError, ValueError):
            cid = None
        self.orders[oid] = LedgerOrder(
            order_id=oid, book_id=bid, side=sd, price=px, quantity=qty,
            remaining=qty, placed_ns=ts, placed_tick=tk,
            action=str(action or ""), client_id=cid,
        )
        self.accepted += 1
        if len(self.orders) > _MAX_TRACKED_ORDERS:
            # Drop the oldest acknowledged rows first; they are the ones the
            # exchange is most likely to have already retired.
            for stale in sorted(self.orders, key=lambda k: self.orders[k].placed_ns)[:64]:
                self.orders.pop(stale, None)
                self.swept += 1
        return True

    def note_removed(self, order_id, *, cause: str, filled_qty=None) -> LedgerOrder | None:
        """Retire an order.  A partial fill decrements instead of removing."""
        try:
            oid = int(order_id)
        except (TypeError, ValueError):
            return None
        row = self.orders.get(oid)
        if row is None:
            self.unmatched_removals += 1
            return None
        if cause == LEDGER_REMOVED_FILLED and filled_qty is not None:
            try:
                row.remaining = max(0.0, float(row.remaining) - abs(float(filled_qty)))
            except (TypeError, ValueError):
                row.remaining = 0.0
            if row.remaining > 1e-12:
                return None
        self.orders.pop(oid, None)
        self._remember_removal(oid, cause)
        self.removed += 1
        return row

    def _remember_removal(self, order_id: int, cause: str) -> None:
        """Record why an order left the book, bounded in size."""
        self.removal_causes[int(order_id)] = str(cause or "")
        if len(self.removal_causes) > _MAX_REMOVAL_MEMO:
            for stale in list(self.removal_causes)[: _MAX_REMOVAL_MEMO // 4]:
                self.removal_causes.pop(stale, None)

    def removal_cause(self, order_id) -> str:
        """Why this order left the book, or "" when it is not remembered."""
        try:
            return self.removal_causes.get(int(order_id), "")
        except (TypeError, ValueError):
            return ""

    def reset(self) -> int:
        """Forget every tracked order.  Used when the simulation clock restarts."""
        dropped = len(self.orders)
        self.orders.clear()
        # A restart reuses order ids, so a retained cause would be attributed to
        # a different order in the next session.
        self.removal_causes.clear()
        self.swept += dropped
        return dropped

    def sweep(self, now_ns) -> int:
        """Drop rows far past any requestable TTL.  Returns the sweep count."""
        try:
            now = float(now_ns)
        except (TypeError, ValueError):
            return 0
        if now <= 0.0:
            return 0
        # A simulation restart rewinds the clock and reuses order ids.  Every
        # tracked row belongs to the previous session, so keeping any of them
        # would report a phantom resting exit.
        newest = max((row.placed_ns for row in self.orders.values()), default=0)
        if newest > 0 and now < float(newest):
            return self.reset()
        cutoff = LEDGER_SWEEP_GRACE_MS * 1e6
        stale = [
            oid for oid, row in self.orders.items()
            if row.placed_ns > 0 and (now - float(row.placed_ns)) > cutoff
        ]
        for oid in stale:
            self.orders.pop(oid, None)
            self._remember_removal(oid, LEDGER_REMOVED_TTL_SWEEP)
            self.swept += 1
        return len(stale)

    # ----------------------------------------------------------------- reads

    def live_orders(
        self, book_id, *, side=None, max_age_ms=None, now_ns=None,
    ) -> list[LedgerOrder]:
        """Live rows for a book, newest first, optionally one side only.

        ``max_age_ms`` exists because a removal notice arrives one state after
        the exchange acts on it.  Measured on the A1.7.5 log, 47.5% of exit
        placements see a row that has already reached its TTL but whose
        cancellation has not been delivered yet.  Treating such a row as
        holdable would suppress the replacement exit and leave the position
        with no resting order at all, so a caller that is about to act on the
        answer must pass the TTL it placed the order under.
        """
        try:
            bid = int(book_id)
        except (TypeError, ValueError):
            return []
        want = None
        if side is not None:
            try:
                want = int(side)
            except (TypeError, ValueError):
                return []
        if _F2_LEDGER_INDEX and (f2_rows := _f2_book_rows(self.orders, bid)) is not None:
            rows = [row for row in f2_rows if want is None or row.side == want]
        else:
            rows = [
                row for row in self.orders.values()
                if row.book_id == bid and (want is None or row.side == want)
            ]
        if max_age_ms is not None and now_ns is not None:
            try:
                limit = float(max_age_ms)
            except (TypeError, ValueError):
                limit = None
            if limit is not None and limit > 0.0:
                rows = [r for r in rows if r.placed_ns > 0 and r.age_ms(now_ns) < limit]
        rows.sort(key=lambda r: r.placed_ns, reverse=True)
        return rows

    def live_count(self, book_id=None) -> int:
        if book_id is None:
            return len(self.orders)
        return len(self.live_orders(book_id))

    def stats(self) -> dict:
        return {
            "ledger_version": DIRECT_EXIT_LEDGER_VERSION,
            "ledger_live": len(self.orders),
            "ledger_accepted": int(self.accepted),
            "ledger_removed": int(self.removed),
            "ledger_swept": int(self.swept),
            "ledger_unmatched_removals": int(self.unmatched_removals),
            "ledger_removal_causes_tracked": len(self.removal_causes),
        }


def close_side_for(net_base: float) -> int:
    """Exchange side that reduces this position: 1 (sell) when long."""
    try:
        return 1 if float(net_base) > 0.0 else 0
    except (TypeError, ValueError):
        return 0


@dataclass(frozen=True)
class RestingInventoryView:
    """The inventory facts the observer needs, read without side effects.

    A1.9.0.2 observes every open-inventory book at the top of ``respond``,
    before the frozen base has incremented ``_tick``.  The obvious way to get
    the position -- ``_net_inventory(book_id, mid)`` -- cannot be used there:
    it advances ``_position_ticks`` once per ``_tick``, guarded by
    ``_research_position_tick_seen[book_id] != current_tick``.  For a book that
    was not evaluated on the previous tick that guard does not hold, so a
    pre-tick call would age the position once and the tick's real call would
    age it again.  Position age drives the exit escalation ladder, so that is a
    live trading-behaviour change -- exactly what a measurement-only revision
    must not do.

    ``_position_tracker_snapshot`` is pure, and net_base plus vwap_entry are
    all the shadow classifier reads, so the observer builds this instead.
    """

    net_base: float
    vwap_entry: float | None

    @classmethod
    def from_tracker(cls, tracker) -> "RestingInventoryView":
        try:
            net = float(getattr(tracker, "net_qty", 0.0) or 0.0)
        except (TypeError, ValueError):
            net = 0.0
        vwap = getattr(tracker, "vwap_entry", None)
        try:
            vwap = float(vwap) if vwap is not None else None
        except (TypeError, ValueError):
            vwap = None
        return cls(net_base=net, vwap_entry=vwap)
