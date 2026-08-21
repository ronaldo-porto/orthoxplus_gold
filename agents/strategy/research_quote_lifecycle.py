# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 2: quote lifecycle, fill class, and delayed markout.

Pure functions so unit tests do not import Strategy1 / bittensor.
Does not change trading policy. Queue fields are omitted unless real data is supplied.
"""
from __future__ import annotations

from collections import OrderedDict, deque
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

FILL_CLASSES = (
    "FULL",
    "ACTIONABLE_PARTIAL",
    "DUST_PARTIAL",
    "FLAT",
    "CROSS_DUST",
)
MARKOUT_HORIZONS_MS = (100, 250, 500, 1000)
NS_PER_MS = 1_000_000


def sim_delta_ms(start: float | int | None, end: float | int | None) -> float | None:
    """Simulator timestamps are nanoseconds."""
    if start is None or end is None:
        return None
    return (float(end) - float(start)) / NS_PER_MS


def ms_to_ns(ms: float | int) -> int:
    return int(float(ms) * NS_PER_MS)


def is_flat(qty: float, eps: float) -> bool:
    return abs(float(qty)) < max(float(eps), 1e-12)


def is_dust(qty: float, min_order_size: float, eps: float) -> bool:
    """Sub-minimum but not execution-flat. Uses the runtime min, never a hardcoded 0.25."""
    abs_qty = abs(float(qty))
    min_size = max(0.0, float(min_order_size))
    if min_size <= 0.0:
        return False
    return abs_qty >= max(float(eps), 1e-12) and abs_qty + 1e-12 < min_size


def is_actionable(qty: float, min_order_size: float, eps: float) -> bool:
    min_size = max(0.0, float(min_order_size))
    if min_size <= 0.0:
        return not is_flat(qty, eps)
    return abs(float(qty)) + max(float(eps), 1e-12) >= min_size


def remaining_quantity(
    requested: float | None,
    filled_cum: float | None,
    eps: float,
) -> float | None:
    if requested is None:
        return None
    filled = 0.0 if filled_cum is None else max(0.0, float(filled_cum))
    left = max(0.0, float(requested) - filled)
    return 0.0 if left <= max(float(eps), 1e-12) else left


def classify_fill(
    *,
    inventory_before: float,
    inventory_after: float,
    fill_quantity: float,
    requested_quantity: float | None,
    filled_quantity: float | None,
    min_order_size: float,
    flat_eps: float,
) -> str:
    """One label per fill from inventory + fill + runtime min size.

    Priority:
      CROSS_DUST — sign flip that leaves dust on the far side
      FLAT — post-fill inventory is execution-flat
      DUST_PARTIAL — post-fill inventory is sub-minimum
      FULL — resting quote remaining is ~0 and leftover is tradable
      ACTIONABLE_PARTIAL — leftover is tradable and the quote still has size
    """
    del fill_quantity  # used by callers for remaining via filled_quantity
    eps = max(float(flat_eps), 1e-12)
    before = float(inventory_before)
    after = float(inventory_after)
    min_size = max(0.0, float(min_order_size))

    crossed = (
        (not is_flat(before, eps))
        and (not is_flat(after, eps))
        and (before * after < 0.0)
    )
    if crossed and is_dust(after, min_size, eps):
        return "CROSS_DUST"
    if is_flat(after, eps):
        return "FLAT"
    if is_dust(after, min_size, eps):
        return "DUST_PARTIAL"

    remaining = remaining_quantity(requested_quantity, filled_quantity, eps)
    quote_complete = remaining is not None and remaining <= eps
    if quote_complete:
        return "FULL"
    if is_actionable(after, min_size, eps):
        return "ACTIONABLE_PARTIAL"
    return "DUST_PARTIAL"


def side_markout_bps(
    side: str,
    fill_price: float,
    future_mid: float | None,
) -> float | None:
    """Maker side-adjusted markout in bps of fill price.

    BUY:  (future_mid - fill_price) / fill_price * 1e4
    SELL: (fill_price - future_mid) / fill_price * 1e4
    Positive is favorable for the maker.
    """
    if future_mid is None:
        return None
    px = float(fill_price)
    if px <= 0.0:
        return None
    mid = float(future_mid)
    raw = (mid - px) / px * 10_000.0
    token = str(side).lower()
    if token in {"sell", "ask", "s", "1"}:
        return -raw
    return raw


def actual_microprice(
    bid: float | None,
    ask: float | None,
    bid_qty: float | None,
    ask_qty: float | None,
) -> float | None:
    if bid is None or ask is None:
        return None
    bq = 0.0 if bid_qty is None else float(bid_qty)
    aq = 0.0 if ask_qty is None else float(ask_qty)
    denom = bq + aq
    if denom <= 0.0:
        return None
    return (float(ask) * bq + float(bid) * aq) / denom


def touch_distance(
    side: str,
    quote_price: float,
    best_bid: float | None,
    best_ask: float | None,
    mid: float | None,
    tick_size: float | None,
) -> tuple[float | None, float | None]:
    token = str(side).lower()
    if token in {"buy", "bid", "b", "0"}:
        touch = best_bid
        delta = None if touch is None else float(touch) - float(quote_price)
    else:
        touch = best_ask
        delta = None if touch is None else float(quote_price) - float(touch)
    ticks = None
    bps = None
    if delta is not None and tick_size is not None and float(tick_size) > 0.0:
        ticks = delta / float(tick_size)
    if delta is not None and mid is not None and float(mid) > 0.0:
        bps = delta / float(mid) * 10_000.0
    return ticks, bps


def optional_queue_metrics(
    *,
    level_quantity: float | None,
    orders: Iterable[Mapping[str, Any]] | None,
) -> dict[str, float]:
    """Return queue fields only from genuine book data. Never infer missing orders."""
    out: dict[str, float] = {}
    if level_quantity is not None:
        out["queue_depth_at_price"] = float(level_quantity)
    if not orders:
        return out
    ahead = 0.0
    saw = False
    for order in orders:
        try:
            ahead += float(order.get("quantity", 0.0) or 0.0)
            saw = True
        except (TypeError, ValueError, AttributeError):
            continue
    if saw:
        out["queue_ahead"] = ahead
    return out


@dataclass
class PendingMarkout:
    quote_id: int
    book: int
    side: str
    fill_price: float
    fill_ts: int
    horizon_ms: int


@dataclass
class MarkoutResult:
    quote_id: int
    book: int
    side: str
    horizon_ms: int
    fill_price: float
    future_mid: float | None
    markout_bps: float | None
    status: str = "OK"


@dataclass
class QuoteRecord:
    quote_id: int
    client_id: int | None
    book: int
    side: str
    decision_ts: int | None = None
    submit_ts: int | None = None
    cancel_ts: int | None = None
    fill_ts: int | None = None
    requested_quantity: float | None = None
    filled_quantity: float = 0.0
    remaining_quantity: float | None = None
    quote_price: float | None = None
    configured_ttl_ms: float | None = None
    predicted_fill_probability: float | None = None
    predicted_any_fill_probability: float | None = None
    predicted_actionable_fill_probability: float | None = None
    predicted_dust_probability: float | None = None
    hazard_source: str | None = None
    hazard_features: dict[str, Any] | None = None
    hazard_closed: bool = False
    market_regime: str | None = None
    score_regime: str | None = None
    book_archetype: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)
    open: bool = True


class QuoteLifecycleStore:
    """Bounded live-quote + pending-markout store. No full-history scan."""

    def __init__(
        self,
        *,
        horizons_ms: tuple[int, ...] = MARKOUT_HORIZONS_MS,
        max_live: int = 1024,
        max_pending_markouts: int = 2048,
        missing_after_ms: int = 2500,
    ) -> None:
        self.horizons_ms = tuple(int(h) for h in horizons_ms)
        self.max_live = max(8, int(max_live))
        self.max_pending_markouts = max(16, int(max_pending_markouts))
        self.missing_after_ns = ms_to_ns(missing_after_ms)
        self._seq = 0
        self.live: OrderedDict[tuple[int, int], QuoteRecord] = OrderedDict()
        self.by_id: dict[int, QuoteRecord] = {}
        self.pending: deque[PendingMarkout] = deque()
        self.last_replaced: QuoteRecord | None = None

    def next_quote_id(self) -> int:
        self._seq += 1
        return self._seq

    def _client_key(self, book: int, client_id: int | None) -> tuple[int, int] | None:
        if client_id is None:
            return None
        return (int(book), int(client_id))

    def _evict_live(self) -> None:
        while len(self.live) > self.max_live:
            key, rec = self.live.popitem(last=False)
            rec.open = False
            self.by_id.pop(rec.quote_id, None)

    def register_quote(self, record: QuoteRecord) -> QuoteRecord:
        self.last_replaced = None
        key = self._client_key(record.book, record.client_id)
        if key is not None and key in self.live:
            prev = self.live.pop(key)
            prev.open = False
            prev.cancel_ts = prev.cancel_ts or record.submit_ts
            self.by_id.pop(prev.quote_id, None)
            self.last_replaced = prev
        if key is not None:
            self.live[key] = record
            self.live.move_to_end(key)
        self.by_id[record.quote_id] = record
        self._evict_live()
        return record

    def lookup(self, book: int, client_id: int | None) -> QuoteRecord | None:
        key = self._client_key(book, client_id)
        if key is None:
            return None
        return self.live.get(key)

    def live_for_book_side(self, book: int, side: str) -> QuoteRecord | None:
        want = str(side).lower()
        target = int(book)
        for rec in reversed(self.live.values()):
            if rec.open and rec.book == target and str(rec.side).lower() == want:
                return rec
        return None

    def close_quote(
        self,
        record: QuoteRecord,
        *,
        cancel_ts: int | None = None,
        fill_ts: int | None = None,
    ) -> None:
        record.open = False
        if cancel_ts is not None:
            record.cancel_ts = cancel_ts
        if fill_ts is not None:
            record.fill_ts = fill_ts
        key = self._client_key(record.book, record.client_id)
        if key is not None:
            self.live.pop(key, None)
        self.by_id.pop(record.quote_id, None)

    def apply_fill(
        self,
        record: QuoteRecord,
        *,
        fill_qty: float,
        fill_ts: int | None,
        flat_eps: float,
    ) -> float:
        record.filled_quantity = float(record.filled_quantity) + abs(float(fill_qty))
        record.fill_ts = fill_ts
        record.remaining_quantity = remaining_quantity(
            record.requested_quantity, record.filled_quantity, flat_eps,
        )
        return record.filled_quantity

    def schedule_markouts(
        self,
        *,
        quote_id: int,
        book: int,
        side: str,
        fill_price: float,
        fill_ts: int,
    ) -> None:
        for horizon in self.horizons_ms:
            self.pending.append(
                PendingMarkout(
                    quote_id=int(quote_id),
                    book=int(book),
                    side=str(side),
                    fill_price=float(fill_price),
                    fill_ts=int(fill_ts),
                    horizon_ms=int(horizon),
                )
            )
        return self._cap_pending(now_ts=int(fill_ts))

    def _cap_pending(self, now_ts: int) -> list[MarkoutResult]:
        dropped: list[MarkoutResult] = []
        while len(self.pending) > self.max_pending_markouts:
            item = self.pending.popleft()
            dropped.append(
                MarkoutResult(
                    quote_id=item.quote_id,
                    book=item.book,
                    side=item.side,
                    horizon_ms=item.horizon_ms,
                    fill_price=item.fill_price,
                    future_mid=None,
                    markout_bps=None,
                    status="MISSING_FUTURE",
                )
            )
        return dropped

    def evaluate(
        self,
        *,
        now_ts: int,
        mids: Mapping[int, float | None],
    ) -> list[MarkoutResult]:
        """Emit due markouts. O(pending), not O(all books / history)."""
        due: list[MarkoutResult] = []
        kept: deque[PendingMarkout] = deque()
        now = int(now_ts)
        for item in self.pending:
            age_ns = now - int(item.fill_ts)
            if age_ns < 0:
                kept.append(item)
                continue
            ready = age_ns >= ms_to_ns(item.horizon_ms)
            missing = age_ns >= self.missing_after_ns
            if not ready and not missing:
                kept.append(item)
                continue
            future_mid = mids.get(int(item.book))
            if ready and future_mid is not None:
                due.append(
                    MarkoutResult(
                        quote_id=item.quote_id,
                        book=item.book,
                        side=item.side,
                        horizon_ms=item.horizon_ms,
                        fill_price=item.fill_price,
                        future_mid=float(future_mid),
                        markout_bps=side_markout_bps(
                            item.side, item.fill_price, float(future_mid),
                        ),
                        status="OK",
                    )
                )
                continue
            if missing:
                due.append(
                    MarkoutResult(
                        quote_id=item.quote_id,
                        book=item.book,
                        side=item.side,
                        horizon_ms=item.horizon_ms,
                        fill_price=item.fill_price,
                        future_mid=None,
                        markout_bps=None,
                        status="MISSING_FUTURE",
                    )
                )
                continue
            kept.append(item)
        self.pending = kept
        return due
