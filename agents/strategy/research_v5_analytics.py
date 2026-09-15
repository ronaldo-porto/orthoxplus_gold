# SPDX-License-Identifier: MIT
"""v5.0.0: maker and taker analytics, read from the rows the strategy already writes.

Nothing here decides anything.  ``observe`` reads FILL, POSITION, A1961_TAKER_OUTCOME,
MARKOUT, ORDER_LIFECYCLE and A19_EXIT_REPRICE_CANCEL payloads as they are emitted;
``flush`` runs once per request, after the fills of that request are all in, and returns
rows to log.  A fault here costs telemetry, never a trade.

Measured on the A1.9.9.1 run, log 20260914_141324, ticks 1-8,000.  Replaying that log's rows
through this ledger gives back its 810 round trips and its realized PnL exactly: 79.34 closed
plus 0.26 still open, against 79.60 on the POSITION rows.

    exit class     RTs  positive     PnL   cubic share   hold, entry fill to flat (median / p90)
    maker          494       484  +159.59       0.1%       16 s /  57 s
    ABSOLUTE       155         0   -49.25      75.6%       12 s /  42 s
    HARD_ESCAPE    118         0   -28.22      23.1%       50 s / 213 s
    RECOVERY        26         0    -3.37       0.9%       52 s / 221 s
    PROFIT_LOCK     15        15    +0.76       0.0%       83 s / 131 s

806 round trips opened with a maker fill and 4 were positions from before the process; no
entry was a taker, so there is no alpha-taker class.  POSITION's ``rt_hold_s`` is not a
holding time: it ends at the first exit order, not at flat.

What the rows could not say without a join, this ledger says per round trip: entry and
exit style, the taker class, spread captured against the mid at each fill, fees, hold
time, capital-seconds, orders placed, cancelled and repriced while open.  After a taker
exit it reads the book again at 1, 5, 20 and 60 ticks: the price the position would have
exited at had it waited, as bps the taker avoided (positive) or gave up (negative).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

V500_ANALYTICS_VERSION = "direct_analytics_v5_0_0"

V500_TAPPED_ROWS = frozenset({
    "FILL", "POSITION", "A1961_TAKER_OUTCOME", "MARKOUT", "ORDER_LIFECYCLE",
    "A19_EXIT_REPRICE_CANCEL", "A199_EPOCH_REWIND",
})

EXIT_MAKER = "MAKER"
EXIT_ABSOLUTE = "ABSOLUTE"
EXIT_HARD_ESCAPE = "HARD_ESCAPE"
EXIT_RECOVERY = "RECOVERY"
EXIT_PROFIT_LOCK = "PROFIT_LOCK"
EXIT_TAKER_OTHER = "TAKER_OTHER"
EXIT_TAKER_UNCLASSIFIED = "TAKER_UNCLASSIFIED"
EXIT_CLASSES = (EXIT_MAKER, EXIT_ABSOLUTE, EXIT_HARD_ESCAPE, EXIT_RECOVERY, EXIT_PROFIT_LOCK,
                EXIT_TAKER_OTHER, EXIT_TAKER_UNCLASSIFIED)
TRIGGER_CLASS = {
    "ABSOLUTE_PROTECTION_REDUCE": EXIT_ABSOLUTE,
    "HARD_ESCAPE_CLIP": EXIT_HARD_ESCAPE,
    "RECOVERY_TAKER_REDUCE": EXIT_RECOVERY,
    "NORMAL_TAKER_NONNEGATIVE": EXIT_PROFIT_LOCK,
}

COUNTERFACTUAL_HORIZONS = (1, 5, 20, 60)
MARKOUT_HORIZON_MS = 1000
WINDOW_NS = 10_800_000_000_000  # the validator's 3 h assessment window
FLAT_EPS = 5e-05
MAX_TRIPS_PER_BOOK = 256
MAX_PENDING_COUNTERFACTUALS = 64
MAX_EVENTS_PER_BOOK = 512


def _f(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _i(value: Any, default: int | None = None) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _median(values: Iterable[float]) -> float | None:
    ordered = sorted(values)
    n = len(ordered)
    if n == 0:
        return None
    mid = n // 2
    return ordered[mid] if n % 2 else 0.5 * (ordered[mid - 1] + ordered[mid])


def exit_class(closing_maker: bool, trigger: str | None) -> str:
    if closing_maker:
        return EXIT_MAKER
    if not trigger:
        return EXIT_TAKER_UNCLASSIFIED
    return TRIGGER_CLASS.get(str(trigger), EXIT_TAKER_OTHER)


def capture_bps(side: str, price: float, mid: float) -> float | None:
    """Spread captured against the mid: positive when a buy is below it or a sell above it."""
    if mid <= 0.0 or price <= 0.0:
        return None
    if str(side).lower().startswith("b"):
        return (mid - price) / mid * 10_000.0
    return (price - mid) / mid * 10_000.0


@dataclass
class Trip:
    book: int
    sign: float
    open_ts: int
    open_tick: int
    entry_maker_qty: float = 0.0
    entry_taker_qty: float = 0.0
    entry_notional: float = 0.0
    entry_capture_weighted: float = 0.0
    entry_capture_qty: float = 0.0
    entry_fee: float = 0.0
    exit_maker_qty: float = 0.0
    exit_taker_qty: float = 0.0
    exit_notional: float = 0.0
    exit_capture_weighted: float = 0.0
    exit_capture_qty: float = 0.0
    exit_fee: float = 0.0
    pnl: float = 0.0
    capital_s: float = 0.0
    last_ts: int = 0
    abs_qty: float = 0.0
    last_price: float = 0.0
    placements: int = 0
    cancels: int = 0
    reprices: int = 0
    close_ts: int = 0
    closing_maker: bool = False
    closed_by_cross: bool = False
    trigger: str = ""
    decision_net_bps: float | None = None
    realized_net_bps: float | None = None
    slippage_bps: float | None = None
    adopted: bool = False

    def accrue(self, ts: int, price: float) -> None:
        if self.last_ts and ts > self.last_ts:
            self.capital_s += self.abs_qty * (self.last_price or price) * (ts - self.last_ts) / 1e9
        if ts > self.last_ts:
            self.last_ts = ts
        if price > 0.0:
            self.last_price = price

    def record(self, tick: int) -> dict[str, Any]:
        hold_s = max(0.0, (self.close_ts - self.open_ts) / 1e9) if self.close_ts else 0.0
        entry_qty = self.entry_maker_qty + self.entry_taker_qty
        if self.adopted:
            entry_style = "INHERITED"
        elif self.entry_taker_qty <= FLAT_EPS:
            entry_style = EXIT_MAKER
        elif self.entry_maker_qty <= FLAT_EPS:
            entry_style = "TAKER"
        else:
            entry_style = "MIXED"
        cls = exit_class(self.closing_maker, self.trigger)
        return {
            "book": self.book,
            "side": "LONG" if self.sign > 0 else "SHORT",
            "open_tick": self.open_tick,
            "close_tick": int(tick),
            "entry_style": entry_style,
            "exit_class": cls,
            "trigger": self.trigger or None,
            "pnl": round(self.pnl, 8),
            "entry_qty": round(entry_qty, 6),
            "entry_capture_bps": (round(self.entry_capture_weighted / self.entry_capture_qty, 3)
                                  if self.entry_capture_qty > FLAT_EPS else None),
            "exit_capture_bps": (round(self.exit_capture_weighted / self.exit_capture_qty, 3)
                                 if self.exit_capture_qty > FLAT_EPS else None),
            "fees": round(self.entry_fee + self.exit_fee, 8),
            "hold_s": round(hold_s, 3),
            "capital_s": round(self.capital_s, 3),
            "pnl_per_hold_s": round(self.pnl / hold_s, 8) if hold_s > 0 else None,
            "pnl_per_capital_hour": round(self.pnl / (self.capital_s / 3600.0), 8) if self.capital_s > 0 else None,
            "placements": self.placements,
            "cancels": self.cancels,
            "reprices": self.reprices,
            "decision_net_bps": self.decision_net_bps,
            "realized_net_bps": self.realized_net_bps,
            "slippage_bps": self.slippage_bps,
            "crossed": int(self.closed_by_cross),
            "adopted": int(self.adopted),
            "close_ts": self.close_ts,
        }


@dataclass
class Counterfactual:
    book: int
    sign: float
    price: float
    qty: float
    fill_tick: int
    start_tick: int | None = None
    trigger: str = ""
    avoided_bps: dict[int, float | None] | None = None


class TradeAnalytics:
    """Per-process ledger.  Bounded; restarts empty with the process."""

    def __init__(self) -> None:
        self.open: dict[int, Trip] = {}
        self.closing: list[Trip] = []
        self.trips: dict[int, deque] = {}
        self.pending: list[Counterfactual] = []
        self.completed_counterfactuals: deque = deque(maxlen=512)
        self.markouts: dict[int, deque] = {}
        self.maker_sides: dict[int, dict[str, int]] = {}
        self.volume: dict[int, deque] = {}
        self.counterparties: deque = deque(maxlen=4096)
        self.rounds: dict[int, None] = {}
        self.rt_rows = 0
        self.counterfactual_rows = 0
        self.observe_errors = 0
        self.resets = 0
        self.adopted = 0
        self.unresolved = 0
        self.early_outcomes: dict[int, Mapping[str, Any]] = {}
        self.last_tick = 0

    # ---- rows ------------------------------------------------------------------------------------

    def observe(self, event_type: str, payload: Mapping[str, Any]) -> None:
        handler = _HANDLERS.get(event_type)
        if handler is None:
            return
        try:
            handler(self, payload)
        except Exception:
            self.observe_errors += 1

    def _fill(self, p: Mapping[str, Any]) -> None:
        book = _i(p.get("book"))
        if book is None:
            return
        price = _f(p.get("fill_price"))
        qty = abs(_f(p.get("filled_quantity")))
        if price <= 0.0 or qty <= 0.0:
            return
        before = _f(p.get("inventory_before"))
        after = _f(p.get("inventory_after"))
        ts = _i(p.get("fill_timestamp")) or _i(p.get("timestamp")) or 0
        tick = _i(p.get("tick"), 0) or 0
        maker = bool(p.get("maker"))
        side = str(p.get("side") or "")
        fee = _f(p.get("fee"))
        cap = capture_bps(side, price, _f(p.get("mid")))
        notional = price * qty
        self.volume.setdefault(book, deque(maxlen=MAX_EVENTS_PER_BOOK)).append((ts, notional))
        if maker and side:
            self.maker_sides.setdefault(book, {})[side.lower()[:1]] = ts
        was_flat = abs(before) <= FLAT_EPS
        is_flat = abs(after) <= FLAT_EPS
        crossed = before * after < -(FLAT_EPS * FLAT_EPS)
        trip = self.open.get(book)
        if trip is not None and (was_flat or (before > 0.0) != (trip.sign > 0.0)):
            # The tracker moved without a fill this ledger saw (a venue reseed): that lifecycle
            # cannot be resolved, and the position in front of this fill starts a new one.
            del self.open[book]
            self.unresolved += 1
            trip = None
        if trip is None and not was_flat:
            # A position opened before this process, or before a reseed: adopt it at this fill.
            trip = Trip(book=book, sign=1.0 if before > 0 else -1.0, open_ts=ts, open_tick=tick, last_ts=ts,
                        abs_qty=abs(before), last_price=price, adopted=True)
            self.open[book] = trip
            self.adopted += 1
        if trip is not None:
            trip.accrue(ts, price)
        if trip is not None and not was_flat and (is_flat or crossed or abs(after) + FLAT_EPS < abs(before)):
            closed_qty = abs(before) if (is_flat or crossed) else abs(before) - abs(after)
            closed_qty = min(closed_qty, qty)
            if maker:
                trip.exit_maker_qty += closed_qty
            else:
                trip.exit_taker_qty += closed_qty
            trip.exit_notional += closed_qty * price
            trip.exit_fee += fee * (closed_qty / qty)
            if cap is not None:
                trip.exit_capture_weighted += cap * closed_qty
                trip.exit_capture_qty += closed_qty
            trip.abs_qty = 0.0 if (is_flat or crossed) else abs(after)
            if not maker:
                self._register_counterfactual(trip, price, closed_qty, tick)
            if is_flat or crossed:
                trip.close_ts = ts
                trip.closing_maker = maker
                trip.closed_by_cross = crossed
                self.closing.append(trip)
                del self.open[book]
                trip = None
        elif trip is not None and not was_flat and abs(after) > abs(before) + FLAT_EPS:
            added = min(qty, abs(after) - abs(before))
            self._add_entry(trip, maker, added, price, fee * (added / qty), cap)
            trip.abs_qty = abs(after)
        if (was_flat or crossed) and not is_flat and book not in self.open:
            opened = abs(after)
            trip = Trip(book=book, sign=1.0 if after > 0 else -1.0, open_ts=ts, open_tick=tick, last_ts=ts,
                        abs_qty=opened, last_price=price)
            self._add_entry(trip, maker, min(qty, opened), price, fee * (min(qty, opened) / qty), cap)
            self.open[book] = trip

    @staticmethod
    def _add_entry(trip: Trip, maker: bool, qty: float, price: float, fee: float, cap: float | None) -> None:
        if maker:
            trip.entry_maker_qty += qty
        else:
            trip.entry_taker_qty += qty
        trip.entry_notional += qty * price
        trip.entry_fee += fee
        if cap is not None:
            trip.entry_capture_weighted += cap * qty
            trip.entry_capture_qty += qty

    def _register_counterfactual(self, trip: Trip, price: float, qty: float, tick: int) -> None:
        if len(self.pending) >= MAX_PENDING_COUNTERFACTUALS:
            return
        self.pending.append(Counterfactual(book=trip.book, sign=trip.sign, price=price, qty=qty, fill_tick=tick))

    def _position(self, p: Mapping[str, Any]) -> None:
        book = _i(p.get("book_id"))
        if book is None:
            return
        delta = _f(p.get("realized_pnl_delta"))
        if delta == 0.0:
            return
        target = None
        for trip in reversed(self.closing):
            if trip.book == book:
                target = trip
                break
        if target is None:
            target = self.open.get(book)
        if target is not None:
            target.pnl += delta

    def _taker_outcome(self, p: Mapping[str, Any]) -> None:
        book = _i(p.get("book"))
        if book is None:
            return
        trigger = str(p.get("trigger") or "")
        for trip in reversed(self.closing):
            if trip.book == book:
                self._attach_outcome(trip, p)
                break
        else:
            # Arrived before the fill that closes the trip: held for this request's flush.
            self.early_outcomes[book] = dict(p)
        for cf in reversed(self.pending):
            if cf.book == book and not cf.trigger:
                cf.trigger = trigger
                break

    @staticmethod
    def _attach_outcome(trip: Trip, p: Mapping[str, Any]) -> None:
        trip.trigger = str(p.get("trigger") or "")
        for name in ("decision_net_bps", "realized_net_bps", "slippage_bps"):
            value = p.get(name)
            setattr(trip, name, None if value is None else _f(value))

    def _markout(self, p: Mapping[str, Any]) -> None:
        if _i(p.get("horizon_ms")) != MARKOUT_HORIZON_MS or str(p.get("status") or "OK") != "OK":
            return
        book = _i(p.get("book"))
        if book is None:
            return
        self.markouts.setdefault(book, deque(maxlen=MAX_EVENTS_PER_BOOK)).append(
            (_i(p.get("tick"), 0) or 0, _f(p.get("markout_bps")))
        )

    def _order(self, p: Mapping[str, Any]) -> None:
        if str(p.get("phase") or "") != "SUBMITTED":
            return
        book = _i(p.get("book_id"))
        trip = self.open.get(book) if book is not None else None
        if trip is None:
            return
        instruction = p.get("instruction") or {}
        kind = str(instruction.get("type") or "")
        if kind.startswith("PLACE_ORDER"):
            trip.placements += 1
        elif kind == "CANCEL_ORDERS":
            trip.cancels += max(1, len(instruction.get("cancellations") or []))

    def _reprice(self, p: Mapping[str, Any]) -> None:
        book = _i(p.get("book"))
        trip = self.open.get(book) if book is not None else None
        if trip is not None:
            trip.reprices += 1

    def _rewind(self, p: Mapping[str, Any]) -> None:
        # A rewind reseeds the tracker from the venue.  Open lifecycles stay open; a later fill
        # that no longer matches one retires it as unresolved (see _fill).
        self.resets += 1

    def note_trade(self, *, uid: Any, maker_agent: Any, taker_agent: Any, ts: Any) -> None:
        counterparty = taker_agent if maker_agent == uid else maker_agent
        self.counterparties.append((_i(ts, 0) or 0, _i(counterparty, -1)))

    def note_round(self, ts: Any, keep_ns: int = WINDOW_NS) -> None:
        now = _i(ts)
        if now is None:
            return
        self.rounds[now] = None
        if len(self.rounds) > 2 * (keep_ns // 1_000_000_000 + 64):
            cutoff = now - keep_ns - 1_000_000_000
            self.rounds = {t: None for t in self.rounds if t >= cutoff}

    # ---- per request ------------------------------------------------------------------------------

    def flush(self, *, tick: int, now_ts: int, books: Mapping[Any, Any] | None) -> list[tuple[str, dict[str, Any]]]:
        rows: list[tuple[str, dict[str, Any]]] = []
        self.last_tick = int(tick)
        for trip in self.closing:
            early = self.early_outcomes.pop(trip.book, None)
            if early is not None and not trip.trigger and not trip.closing_maker:
                self._attach_outcome(trip, early)
            record = trip.record(tick)
            self.trips.setdefault(trip.book, deque(maxlen=MAX_TRIPS_PER_BOOK)).append(record)
            rows.append(("V500_RT", record))
            self.rt_rows += 1
        self.closing.clear()
        self.early_outcomes.clear()
        still: list[Counterfactual] = []
        for cf in self.pending:
            if cf.start_tick is None:
                cf.start_tick = int(tick)
                cf.avoided_bps = {}
            elapsed = int(tick) - cf.start_tick
            for horizon in COUNTERFACTUAL_HORIZONS:
                if horizon == elapsed and horizon not in cf.avoided_bps:
                    cf.avoided_bps[horizon] = self._avoided_bps(cf, books)
            if elapsed >= COUNTERFACTUAL_HORIZONS[-1]:
                for horizon in COUNTERFACTUAL_HORIZONS:
                    cf.avoided_bps.setdefault(horizon, None)
                row = {
                    "book": cf.book, "fill_tick": cf.fill_tick, "trigger": cf.trigger or None,
                    "exit_class": exit_class(False, cf.trigger), "exit_price": cf.price, "qty": round(cf.qty, 6),
                    "side": "LONG" if cf.sign > 0 else "SHORT",
                }
                for horizon in COUNTERFACTUAL_HORIZONS:
                    value = cf.avoided_bps[horizon]
                    row[f"avoided_bps_t{horizon}"] = None if value is None else round(value, 3)
                last = cf.avoided_bps[COUNTERFACTUAL_HORIZONS[-1]]
                row["avoided_pnl_t60"] = None if last is None else round(last * cf.price * cf.qty / 10_000.0, 8)
                rows.append(("V500_TAKER_COUNTERFACTUAL", row))
                self.completed_counterfactuals.append((now_ts, row))
                self.counterfactual_rows += 1
            else:
                still.append(cf)
        self.pending = still
        return rows

    @staticmethod
    def _avoided_bps(cf: Counterfactual, books: Mapping[Any, Any] | None) -> float | None:
        if not books:
            return None
        book = books.get(cf.book)
        if book is None:
            book = books.get(str(cf.book))
        bids = getattr(book, "bids", None) or []
        asks = getattr(book, "asks", None) or []
        if not bids or not asks:
            return None
        # The price the position would have closed at by waiting: a long sells at the bid, a short buys at the ask.
        touch = _f(bids[0].price) if cf.sign > 0 else _f(asks[0].price)
        if touch <= 0.0 or cf.price <= 0.0:
            return None
        return cf.sign * (cf.price - touch) / cf.price * 10_000.0

    # ---- periodic ---------------------------------------------------------------------------------

    def rollup(self, *, now_ts: int, window_ns: int = WINDOW_NS) -> list[tuple[str, dict[str, Any]]]:
        cutoff = int(now_ts) - int(window_ns)
        per_book: list[dict[str, Any]] = []
        classes: dict[str, dict[str, Any]] = {name: {"n": 0, "pnl": 0.0, "cubic": 0.0, "hold": [],
                                                      "slippage": []} for name in EXIT_CLASSES}
        book_pnl: dict[int, float] = {}
        total_cubic = 0.0
        for book, trips in self.trips.items():
            recent = [t for t in trips if int(t.get("close_ts") or 0) >= cutoff]
            if not recent:
                continue
            pnl = sum(t["pnl"] for t in recent)
            book_pnl[book] = pnl
            capital_s = sum(t["capital_s"] for t in recent)
            maker_n = sum(1 for t in recent if t["exit_class"] == EXIT_MAKER)
            captures = [t["entry_capture_bps"] for t in recent if t["entry_capture_bps"] is not None]
            marks = [bps for tk, bps in self.markouts.get(book, ())]
            per_book.append({
                "book": book, "rts": len(recent), "pnl": round(pnl, 6),
                "maker_exit_share": round(maker_n / len(recent), 4),
                "hold_s_median": _median(t["hold_s"] for t in recent),
                "capital_hours": round(capital_s / 3600.0, 6),
                "pnl_per_capital_hour": round(pnl / (capital_s / 3600.0), 6) if capital_s > 0 else None,
                "entry_capture_bps_mean": round(sum(captures) / len(captures), 3) if captures else None,
                "adverse_markout_share": round(sum(1 for m in marks if m < 0) / len(marks), 4) if marks else None,
                "cancels_per_rt": round(sum(t["cancels"] + t["reprices"] for t in recent) / len(recent), 3),
            })
            for t in recent:
                c = classes[t["exit_class"]]
                c["n"] += 1
                c["pnl"] += t["pnl"]
                cubic = max(0.0, -t["pnl"]) ** 3
                c["cubic"] += cubic
                total_cubic += cubic
                c["hold"].append(t["hold_s"])
                if t.get("slippage_bps") is not None:
                    c["slippage"].append(t["slippage_bps"])
        per_book.sort(key=lambda r: r["pnl"])
        counterfactuals: dict[str, list[dict[str, Any]]] = {}
        for ts, row in self.completed_counterfactuals:
            if ts >= cutoff:
                counterfactuals.setdefault(row["exit_class"], []).append(row)
        class_rows = {}
        for name, c in classes.items():
            if not c["n"]:
                continue
            cfs = counterfactuals.get(name, [])
            out = {
                "n": c["n"], "pnl": round(c["pnl"], 6),
                "cubic_share": round(c["cubic"] / total_cubic, 4) if total_cubic > 0 else 0.0,
                "hold_s_median": _median(c["hold"]),
                "slippage_bps_mean": round(sum(c["slippage"]) / len(c["slippage"]), 3) if c["slippage"] else None,
            }
            if name != EXIT_MAKER:
                for horizon in COUNTERFACTUAL_HORIZONS:
                    vals = [r[f"avoided_bps_t{horizon}"] for r in cfs if r.get(f"avoided_bps_t{horizon}") is not None]
                    out[f"avoided_bps_t{horizon}_mean"] = round(sum(vals) / len(vals), 3) if vals else None
                out["counterfactuals"] = len(cfs)
            class_rows[name] = out
        positive = sorted((v for v in book_pnl.values() if v > 0), reverse=True)
        total_positive = sum(positive)
        abs_total = sum(abs(v) for v in book_pnl.values())
        volume = {b: sum(n for ts, n in rows if ts >= cutoff) for b, rows in self.volume.items()}
        volume_total = sum(volume.values())
        open_notional = [t.sign * t.abs_qty * t.last_price for t in self.open.values()]
        recent_cp = [cp for ts, cp in self.counterparties if ts >= cutoff]
        cp_counts: dict[int, int] = {}
        for cp in recent_cp:
            cp_counts[cp] = cp_counts.get(cp, 0) + 1
        concentration = {
            "books_with_rts": len(book_pnl),
            "books_positive": sum(1 for v in book_pnl.values() if v > 0),
            "median_book_pnl": _median(book_pnl.values()),
            "top3_positive_pnl_share": round(sum(positive[:3]) / total_positive, 4) if total_positive > 0 else None,
            "pnl_hhi": round(sum((abs(v) / abs_total) ** 2 for v in book_pnl.values()), 4) if abs_total > 0 else None,
            "volume_hhi": round(sum((v / volume_total) ** 2 for v in volume.values()), 4) if volume_total > 0 else None,
            "two_sided_maker_books": sum(1 for b, sides in self.maker_sides.items()
                                         if min(sides.values(), default=0) >= cutoff and len(sides) == 2),
            "open_books": len(open_notional),
            "inventory_balance": (round(sum(open_notional) / sum(abs(x) for x in open_notional), 4)
                                  if open_notional and sum(abs(x) for x in open_notional) > 0 else None),
            "counterparty_fills": len(recent_cp),
            "counterparties": len(cp_counts),
            "top_counterparty_share": round(max(cp_counts.values()) / len(recent_cp), 4) if recent_cp else None,
        }
        return [
            ("V500_BOOK_PRODUCTIVITY", {"window_s": round(window_ns / 1e9), "books": per_book}),
            ("V500_TAKER_CLASSES", {"window_s": round(window_ns / 1e9), "classes": class_rows}),
            ("V500_CONCENTRATION", {"window_s": round(window_ns / 1e9), **concentration}),
        ]


_HANDLERS = {
    "FILL": TradeAnalytics._fill,
    "POSITION": TradeAnalytics._position,
    "A1961_TAKER_OUTCOME": TradeAnalytics._taker_outcome,
    "MARKOUT": TradeAnalytics._markout,
    "ORDER_LIFECYCLE": TradeAnalytics._order,
    "A19_EXIT_REPRICE_CANCEL": TradeAnalytics._reprice,
    "A199_EPOCH_REWIND": TradeAnalytics._rewind,
}
