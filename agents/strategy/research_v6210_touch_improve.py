# SPDX-License-Identifier: MIT
"""v6.2.10: quote one tick inside the best price of the OTHER traders -- first in the queue, never over ourselves.

Why (2026-09-22, a virtual two-sided maker replayed on four recordings, both nets, with an exact queue-position
fill model built from the recorded order lists and scored by the validator's own making and alpha code):

* Joining the touch puts a new quote at the back of the queue, so it fills only when a taker order clears the
  whole level: 97% of the replayed entry fills under the current policy are such sweeps, and they mark -9 bps at
  60 states (the 3% partial fills: +1).  UID 82 v6.2.8 at tick 3,000 shows the same shape live: entries +2.0 /
  -7.9 bps at 10 / 60 states while exits, at the own touch, mark +6.8 / +9.2.
* One tick inside, alone at a new best price, the quote is filled by the next taker of any size.  At the mainnet
  volume-cap pace this raised the validator's making per unit volume by +6-10% on mainnet and +25-32% on testnet
  for entries, +17-33% with exits too, and turned entry markouts and alpha positive on mainnet.  Two ticks
  inside was no better than one: the mechanism is queue priority, not price.
* Patience (long quote life, cancel-when-far, resting behind the touch) was worse in every recording.

Two rules, one switch each, sharing one definition of "the others' best price":

* **P1 -- entries.**  The v6.2 acquire quotes go to the others' best bid + one tick / best ask - one tick.
* **P2 -- exits.**  A maker exit the frozen pricer puts at the own touch goes one tick inside it, through the
  same price-level intercept as v6.2.8 S1 (the three frozen producers price through one scope).

Structural guards, none of them sized from observed data:

* **Never over ourselves.**  The book shows our own resting orders; improving on them would walk the price one
  tick per re-quote.  "The others' best" skips every order we own -- by the ledger's order ids for the book
  (every accepted order, entries and exits, until the exchange reports it gone) or by our entry client ids.
* **Strictly inside, never crossing.**  An improved bid stays below every ask, ours included (post-only, and the
  CANCEL_BOTH self-trade rule), and symmetrically for an ask.
* **Our side of the mid.**  An improved bid stays below the others' mid and an ask above it.  This bounds a
  one-tick war with another improving maker at the spread's midpoint, and it is why a spread under three ticks
  is simply joined.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable

V6210_TOUCH_IMPROVE_VERSION = "touch_improve_v6_2_10"

# Only the detailed levels carry order lists (simulation_detailed_book_levels = 5).
DETAIL_LEVELS = 5


def _finite(value: Any, default: float = float("nan")) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _get(obj: Any, *names: str) -> Any:
    """First present attribute/key among ``names`` (levels and orders arrive as models or raw dicts)."""
    for name in names:
        if isinstance(obj, dict):
            if name in obj:
                return obj[name]
        else:
            try:
                value = getattr(obj, name)
            except Exception:
                continue
            if value is not None:
                return value
    return None


def on_grid(price: float, tick: float) -> float:
    """``price`` rounded to the nearest multiple of ``tick`` (the venue's price grid)."""
    t = max(_finite(tick, 0.01), 1e-12)
    return round(round(price / t) * t, 10)


def others_best(
    levels: Iterable[Any] | None,
    *,
    own_ids: set,
    own_cids: set,
    max_levels: int = DETAIL_LEVELS,
    eps: float = 1e-12,
) -> tuple[float | None, bool]:
    """The best price on one side among levels holding any quantity that is not ours.

    Returns ``(price, ours_there_too)``.  A level without an order list cannot be split, so it counts as the
    others' (the frozen behaviour).  ``None`` when the detailed levels hold only our own orders or nothing.
    """
    if not levels:
        return None, False
    try:
        seq = list(levels)[: max(1, int(max_levels))]
    except Exception:
        return None, False
    for lvl in seq:
        px = _finite(_get(lvl, "price", "p"))
        qty = _finite(_get(lvl, "quantity", "q"), 0.0)
        if not math.isfinite(px) or px <= 0.0 or qty <= eps:
            continue
        orders = _get(lvl, "orders", "o")
        if orders is None:
            return px, False
        own_q = 0.0
        for o in orders:
            oid, cid = _get(o, "id", "i"), _get(o, "client_id", "c")
            if (oid is not None and oid in own_ids) or (cid is not None and cid in own_cids):
                own_q += max(0.0, _finite(_get(o, "quantity", "q"), 0.0))
        if qty - own_q > eps:
            return px, own_q > eps
    return None, False


def improved_bid(*, others_bid: Any, others_ask: Any, raw_ask: Any, tick: Any) -> float | None:
    """One tick above the others' best bid; None unless strictly below every ask and below the others' mid."""
    b, a, ra, t = _finite(others_bid), _finite(others_ask), _finite(raw_ask), _finite(tick)
    if not (math.isfinite(b) and math.isfinite(a) and math.isfinite(ra) and math.isfinite(t)) or t <= 0.0 or a <= b:
        return None
    cand = on_grid(b + t, t)
    mid = 0.5 * (b + a)
    if cand < ra - 0.5 * t and cand < mid - 1e-12:
        return cand
    return None


def improved_ask(*, others_bid: Any, others_ask: Any, raw_bid: Any, tick: Any) -> float | None:
    """One tick below the others' best ask; None unless strictly above every bid and above the others' mid."""
    b, a, rb, t = _finite(others_bid), _finite(others_ask), _finite(raw_bid), _finite(tick)
    if not (math.isfinite(b) and math.isfinite(a) and math.isfinite(rb) and math.isfinite(t)) or t <= 0.0 or a <= b:
        return None
    cand = on_grid(a - t, t)
    mid = 0.5 * (b + a)
    if cand > rb + 0.5 * t and cand > mid + 1e-12:
        return cand
    return None


class TouchView:
    """One book's touch as the others show it, for one request."""

    __slots__ = ("raw_bid", "raw_ask", "others_bid", "others_ask", "own_at_bid", "own_at_ask")

    def __init__(self, raw_bid, raw_ask, others_bid, others_ask, own_at_bid, own_at_ask):
        self.raw_bid, self.raw_ask = raw_bid, raw_ask
        self.others_bid, self.others_ask = others_bid, others_ask
        self.own_at_bid, self.own_at_ask = own_at_bid, own_at_ask


def touch_view(book: Any, *, own_ids: set, own_cids: set) -> TouchView | None:
    """Raw and others' best bid/ask of ``book``; None when either side is empty or the raw touch is crossed."""
    try:
        bids, asks = _get(book, "bids"), _get(book, "asks")
        if not bids or not asks:
            return None
        raw_bid = _finite(_get(bids[0], "price", "p"))
        raw_ask = _finite(_get(asks[0], "price", "p"))
    except Exception:
        return None
    if not (math.isfinite(raw_bid) and math.isfinite(raw_ask)) or raw_bid <= 0.0 or raw_ask <= raw_bid:
        return None
    ob, own_b = others_best(bids, own_ids=own_ids, own_cids=own_cids)
    oa, own_a = others_best(asks, own_ids=own_ids, own_cids=own_cids)
    if ob is None or oa is None or oa <= ob:
        return None
    return TouchView(raw_bid, raw_ask, ob, oa, own_b, own_a)


def entry_prices(
    view: TouchView | None, *, tick: Any, quote_buy: bool, quote_sell: bool,
) -> tuple[float | None, float | None]:
    """(bid, ask) for this request's entry quotes: improved where legal, None where the side joins."""
    if view is None:
        return None, None
    bid = improved_bid(others_bid=view.others_bid, others_ask=view.others_ask, raw_ask=view.raw_ask, tick=tick) \
        if quote_buy else None
    ask = improved_ask(others_bid=view.others_bid, others_ask=view.others_ask, raw_bid=view.raw_bid, tick=tick) \
        if quote_sell else None
    if bid is not None and ask is not None and not bid < ask - 0.5 * _finite(tick, 0.01):
        return None, None
    return bid, ask


def improved_exit_price(
    price: Any, *, bid: Any, ask: Any, long_position: bool, tick: Any, view: TouchView | None,
) -> float | None:
    """The improved price for a maker exit the frozen pricer put at the OWN touch; None to leave it alone.

    Only an own-touch price is moved: a passive rung that rests deeper is a deliberate choice.  ``bid``/``ask`` are
    the touch the frozen pricer saw; the view must describe the same touch or nothing is done.
    """
    p, t = _finite(price), _finite(tick)
    if view is None or not math.isfinite(p) or not math.isfinite(t) or t <= 0.0:
        return None
    if abs(_finite(bid) - view.raw_bid) > 0.5 * t or abs(_finite(ask) - view.raw_ask) > 0.5 * t:
        return None
    if long_position:
        if abs(p - view.raw_ask) > 0.5 * t:
            return None
        return improved_ask(others_bid=view.others_bid, others_ask=view.others_ask, raw_bid=view.raw_bid, tick=t)
    if abs(p - view.raw_bid) > 0.5 * t:
        return None
    return improved_bid(others_bid=view.others_bid, others_ask=view.others_ask, raw_ask=view.raw_ask, tick=t)


def improved_price_fn(inner: Callable, *, view_for: Callable, on_improved: Callable | None = None,
                      marker: str | None = None) -> Callable:
    """Wrap the (capped) frozen ``maker_exit_price`` so an own-touch maker exit goes one tick inside.

    ``view_for(bid, ask)`` returns the TouchView of the book being priced, or None.  ``marker`` (the v6.2.8
    CAPPED_MARKER) is set so a nested price scope neither re-wraps nor restores early.
    """
    def improved(*, bid, ask, long_position, action, tick_size):
        price = inner(bid=bid, ask=ask, long_position=long_position, action=action, tick_size=tick_size)
        try:
            better = improved_exit_price(price, bid=bid, ask=ask, long_position=long_position,
                                         tick=tick_size, view=view_for(bid, ask))
        except Exception:
            return price
        if better is None:
            return price
        if on_improved is not None:
            try:
                on_improved()
            except Exception:
                pass
        return better
    if marker:
        setattr(improved, marker, True)
    setattr(improved, "__wrapped__", inner)
    return improved
