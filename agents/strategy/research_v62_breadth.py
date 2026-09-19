# SPDX-License-Identifier: MIT
"""v6.2.0: symmetric touch quotes on every valid flat book -- breadth, in the validator's units.

Why (validator 0.6.1 rung 2, live on mainnet since 2026-09-18 13:46; upstream commit 0234998):

* The de-beta making leg is, per uid, the sum over books of 2·min(buy capture, sell capture), where a
  fill's capture is (centred mid − price)·qty for the buyer and the negation for the seller, and the
  centred mid is the mean of the 31 prints around the fill.  It is ranked among the positive makers and
  carries 0.25 of the trading score now (w_make 0.5 of de-beta weight 0.5) and 0.5 at the final rung.
* Making is a SUM over books, and the validator caps every (uid, book) at 500,000 quote per 24 h
  (capital_turnover_cap × miner_wealth).  At ~250 quote per BASE that cap is 250 BASE per 3 sim-h
  window per book -- 1,000 fills of 0.25, one per 10.8 s -- which the engine's measured close cadence
  already reaches on the books it quotes.  So the lever is the number of books quoted, not lot size.
* The engine quoted ~8 books at a time (the ranker's top-20 admitted into 8 slots capped at 2.0 BASE):
  making 199.5, rank 0.07 of the 44 makers on 2026-09-18.  Per book it already quotes both sides at
  entry and holds one lot in flight; only breadth was missing.

What this module decides, per book and per request:

* eligibility: a valid uncrossed L1, a flat book (no lot, no dust -- dust belongs to the compactor),
  no live or pending order on the book (the A1.7.4.3.1 ownership view), volume-cap headroom, enough
  free balance for the lot, and two instructions of budget on the book;
* the quotes: the best bid and the best ask, on the price grid, one lot each, post-only.  No forecast
  skew and none of the ranker's fill-probability / expected-PnL gates: the making term is symmetric by
  construction and the measured directional alpha of this engine is ~0 (sign 50/50 on its larger books).
* the caps: derived from the universe, not observed -- book_count × lot BASE, book_count open books.

One lot in flight per book (the existing behaviour, kept): the validator's FIFO closes the oldest lot
first, so a second lot on a book that moved away does not unlock a close, and 2·min(buy, sell) does not
grow on the heavier side until the reducing side fills.  A held book costs nothing once there are no
slots.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

V62_VERSION = "breadth_v6_2_0"
V62_STATE_EVERY_TICKS = 100

ENTRY_CLIENT_ID_BASE = 70000
ENTRY_CLIENT_ID_STRIDE = 10
ENTRY_CLIENT_ID_BUY = 1
ENTRY_CLIENT_ID_SELL = 2

REASON_OK = "OK"
REASON_NO_L1 = "NO_L1"
REASON_CROSSED = "CROSSED"
REASON_NOT_FLAT = "NOT_FLAT"
REASON_LIVE_ORDER = "LIVE_ORDER"
REASON_VOLUME_CAP = "VOLUME_CAP"
REASON_BUDGET = "INSTRUCTION_BUDGET"
REASON_NO_BALANCE = "NO_BALANCE"
SKIP_REASONS = (REASON_NO_L1, REASON_CROSSED, REASON_NOT_FLAT, REASON_LIVE_ORDER, REASON_VOLUME_CAP,
                REASON_BUDGET, REASON_NO_BALANCE)


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


@dataclass(frozen=True)
class BookFacts:
    """Everything the per-book verdict needs, gathered by the caller from the frozen views."""
    book_id: int
    best_bid: float | None
    best_ask: float | None
    net_base: float            # signed position from the tracker; dust counts as not flat
    live_order: bool           # acknowledged or pending placement ownership on the book
    cap_ok: bool               # volume-cap headroom for a two-sided lot (the 500k rule)
    quote_free: float          # free quote balance on the book's account
    base_free: float           # free base balance on the book's account
    instructions_used: int     # instructions already queued for this book this request
    max_instructions: int      # the validator's per-book budget (5)


def entry_client_ids(book_id: int) -> tuple[int, int]:
    """The client ids the frozen entry-quote machinery recognises: (buy, sell)."""
    base = ENTRY_CLIENT_ID_BASE + int(book_id) * ENTRY_CLIENT_ID_STRIDE
    return base + ENTRY_CLIENT_ID_BUY, base + ENTRY_CLIENT_ID_SELL


def touch_prices(best_bid: Any, best_ask: Any, price_decimals: Any) -> tuple[float, float] | None:
    """The best bid and the best ask on the price grid, or None when the L1 is missing or crossed."""
    bid = _finite(best_bid, float("nan"))
    ask = _finite(best_ask, float("nan"))
    if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0.0 or ask <= 0.0:
        return None
    if ask <= bid:
        return None
    try:
        d = max(0, int(price_decimals))
    except (TypeError, ValueError):
        d = 2
    return round(bid, d), round(ask, d)


def universe_verdict(f: BookFacts, *, lot: float, flat_eps: float) -> str:
    """Why this book does or does not get its two touch quotes this request."""
    prices = touch_prices(f.best_bid, f.best_ask, 2)
    if f.best_bid is None or f.best_ask is None:
        return REASON_NO_L1
    if prices is None:
        return REASON_CROSSED if _finite(f.best_bid) > 0.0 and _finite(f.best_ask) > 0.0 else REASON_NO_L1
    if abs(_finite(f.net_base)) > max(0.0, _finite(flat_eps)):
        return REASON_NOT_FLAT
    if f.live_order:
        return REASON_LIVE_ORDER
    if not f.cap_ok:
        return REASON_VOLUME_CAP
    if int(f.instructions_used) + 2 > int(f.max_instructions):
        return REASON_BUDGET
    bid, ask = prices
    q = max(0.0, _finite(lot))
    if _finite(f.quote_free) < bid * q or _finite(f.base_free) < q:
        return REASON_NO_BALANCE
    return REASON_OK


def universe_caps(book_count: int, lot: float) -> dict[str, float | int]:
    """The portfolio caps implied by the universe: every book may hold its one lot in flight."""
    n = max(1, int(book_count))
    q = max(0.0, _finite(lot))
    return {
        "research_max_total_abs_base": float(n) * q,
        "research_max_total_open_books": n,
        "research_max_active_open_books": n,
        "research_max_open_books": n,
        "max_managed_books_per_tick": n,
        "max_mm_books_per_tick": n,
        # v6.2.2: the startup seed's size bound.  A1.9.5 bounded the venue import at 24 BASE for the
        # 8-slot model; at breadth the venue legitimately holds one lot per book plus partial-fill
        # overhang, so the bound is two lots per book (a restart on UID 82 left 36 books unseeded).
        "research_a195_max_seed_abs_base": 2.0 * float(n) * q,
    }


def lot_quantity(lot: Any, volume_decimals: Any) -> float:
    """The lot on the volume grid (the A1.9.6 wire snap lifts it one ulp on the way out)."""
    q = max(0.0, _finite(lot))
    try:
        d = max(0, int(volume_decimals))
    except (TypeError, ValueError):
        d = 4
    return float(round(q, d))
