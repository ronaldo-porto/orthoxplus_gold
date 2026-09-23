# SPDX-License-Identifier: MIT
"""v6.2.14: rest only at the best price, add only where the book is deep, and let a filled exit free its book.

Why (mainnet UID 94 on v6.2.11.1, 2026-09-23: its own log for ticks 1-5,950 and the recorder's queue-position
replay scored with the validator's making and alpha arithmetic):

* The final validator re-priced every post-only order to at least ``research_post_only_safety_ticks = 2`` ticks from
  the opposite touch -- a reject cushion from the wide-spread era.  On today's one-tick spreads that is one tick
  BEHIND our own touch: 87-91% of maker exits rested there and filled 4.4% of the time.
* After a single post-only reject the contract guard re-priced every later exit on that book side one tick behind
  the best price until the position closed (5,214 exits), and the PASSIVE rung prices an exit two ticks behind.
* An order behind the touch fills only when the level ahead of it is swept: the fills a level breaks on.  2,425 of
  them (-4.8 validator capture per 100k) zeroed the making leg.
* Entries went to whichever side had room: 68-75% on the thin side of the book (field: -26 bps at 60 s there,
  +12 on the thick side), 57-67% right after that side had been hit.
* An exit placed without a client id never enters the identity registry, so its fill cannot release its book:
  after every exit fill the book waits for the local TTL.

Replay of the rules as the agent can run them (a cancel is seen one state later, one order batch per book, the
4-s TTL, recorder ticks 1-3,000 / 3,350-4,161): today's policy making 7.6 / 0.0; these rules 58.3 / 16.5 with
alpha -289 / -3; the same without the exit identity 15.9 / 9.1.

Four rules, one switch each, none sized from observed data -- the venue's price increment, the book's own depth
and its own last print are the only inputs:

* S0 ``research_v6214_touch_gap`` -- never behind the own touch.  The post-only cushion is one price increment (a
  buy strictly below the best ask, a sell strictly above the best bid: legal, and at the touch on a one-tick
  spread); a post-only reject re-prices from the fresh touch the same way; the PASSIVE exit rung is priced at the
  own touch.
* S1 ``research_v6214_touch_life`` -- an order lives while its price is the best on its side.  Once the touch has
  moved one increment away from it, it is cancelled; the normal paths re-place on a later state.
* S2 ``research_v6214_side_select`` -- the adding side rests only where its side of the book is at least as deep
  as the other side and the last print did not hit it.  A resting adding order is cancelled once that stops
  holding.
* S3 ``research_v6214_exit_identity`` -- every limit placement leaves with a client id (exits: 80000 + 10 x book
  + 1 buy / 2 sell), so its placement notice registers it and its fill or cancellation releases the book at once.
"""
from __future__ import annotations

import math
from typing import Any, Callable, Iterable

from research_v62_making_mirror import trade_dict

V6214_TOUCH_LIFE_VERSION = "touch_life_v6_2_14"

# S0: a post-only order needs one increment of distance from the opposite touch to be legal, and no more.
TOUCH_GAP_TICKS = 1

# S3: exit client ids.  Entries use 70000 + 10 x book + 1/2 and the dust compaction 91000 + 10 x book + side, so the
# 80000 block is free; one id per book side is unique because a book holds one order batch at a time.
EXIT_CLIENT_ID_BASE = 80000
EXIT_CLIENT_ID_STRIDE = 10
EXIT_CLIENT_ID_BUY = 1
EXIT_CLIENT_ID_SELL = 2

# S2 skip reasons at placement and S1/S2 cancel reasons.
REASON_THIN_SIDE = "THIN_SIDE"
REASON_JUST_HIT = "JUST_HIT"
CANCEL_BEHIND = "BEHIND_TOUCH"
CANCEL_THIN = "THIN_SIDE"
CANCEL_HIT = "JUST_HIT"

# The rung tokens the frozen exit pricer reads (research_realization.ACTION_*).
ACTION_PASSIVE = "PASSIVE_MAKER_EXIT"
ACTION_COMPETITIVE = "COMPETITIVE_MAKER_EXIT"

# Exchange order sides, and a trade's side as the validator records it: 0 = the taker bought (lifted the ask),
# 1 = the taker sold (hit the bid).
SIDE_BUY = 0
SIDE_SELL = 1
TAKER_BUY = 0
TAKER_SELL = 1

# Only the detailed levels carry order lists (simulation_detailed_book_levels = 5).
DETAIL_LEVELS = 5

NEVER_BEHIND_MARKER = "_v6214_never_behind"


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
            continue
        try:
            value = getattr(obj, name)
        except Exception:
            continue
        if value is not None:
            return value
    return None


# ---- S3: exit identity ---------------------------------------------------------------------------------

def exit_client_ids(book_id: int) -> tuple[int, int]:
    """The exit client ids of one book: (buy, sell)."""
    base = EXIT_CLIENT_ID_BASE + int(book_id) * EXIT_CLIENT_ID_STRIDE
    return base + EXIT_CLIENT_ID_BUY, base + EXIT_CLIENT_ID_SELL


def exit_client_id(book_id: int, side: str) -> int:
    """The exit client id for ``side`` ("buy"/"sell") on ``book_id``."""
    buy, sell = exit_client_ids(book_id)
    return buy if str(side or "").strip().lower() in {"buy", "bid", "b", "0"} else sell


# ---- S0: never behind the own touch ----------------------------------------------------------------------

def never_behind_action(action: Any) -> Any:
    """PASSIVE -> COMPETITIVE (the own touch); every other rung, taker and unknown action unchanged."""
    if str(action or "").upper() == ACTION_PASSIVE:
        return ACTION_COMPETITIVE
    return action


def never_behind_price_fn(original: Callable, on_capped: Callable | None = None, marker: str | None = None) -> Callable:
    """Wrap the frozen keyword-only ``maker_exit_price`` so a PASSIVE request is priced at the own touch."""
    def priced(*, bid, ask, long_position, action, tick_size):
        capped = never_behind_action(action)
        if capped is not action and on_capped is not None:
            try:
                on_capped()
            except Exception:
                pass
        return original(bid=bid, ask=ask, long_position=long_position, action=capped, tick_size=tick_size)
    setattr(priced, NEVER_BEHIND_MARKER, True)
    if marker:
        setattr(priced, marker, True)
    setattr(priced, "__wrapped__", original)
    return priced


def fresh_touch_price(
    *, side: str, original_price: float, best_bid: float, best_ask: float, tick_size: float,
    reject_streak: int = 1,
) -> float | None:
    """S0's replacement for the frozen ``guarded_post_only_price`` (same keywords).

    After a post-only reject the frozen guard placed a sell at least one tick ABOVE the best ask (and a buy below
    the best bid) for the rest of the position's life: one tick behind our own touch, where an order fills only on a
    sweep.  Legality needs only the opposite touch: a sell strictly above the best bid, a buy strictly below the best
    ask.  The original price is kept whenever it is legal; ``reject_streak`` is accepted and not used.
    """
    original = _finite(original_price, -1.0)
    bid = _finite(best_bid, -1.0)
    ask = _finite(best_ask, -1.0)
    tick = _finite(tick_size, -1.0)
    if original <= 0.0 or bid <= 0.0 or ask <= 0.0 or ask < bid or tick <= 0.0:
        return None
    token = str(side or "").strip().lower()
    if token == "sell":
        return max(original, round(bid + tick, 10))
    if token == "buy":
        price = min(original, round(ask - tick, 10))
        return price if price > 0.0 else None
    return None


# ---- S1: the touch -------------------------------------------------------------------------------------------

def behind_touch(*, side: int, price: Any, best_bid: Any, best_ask: Any, tick: Any) -> bool:
    """True once the best price on this order's side is at least one increment better than the order's own.

    A resting order is at the best price or behind it; a better price on its side means the touch has moved away.
    Unknown prices never read as behind.
    """
    p, t = _finite(price), _finite(tick)
    if not (math.isfinite(p) and math.isfinite(t)) or p <= 0.0 or t <= 0.0:
        return False
    if int(side) == SIDE_BUY:
        best = _finite(best_bid)
        return math.isfinite(best) and best > p + 0.5 * t
    best = _finite(best_ask)
    return math.isfinite(best) and 0.0 < best < p - 0.5 * t


# ---- S2: depth and the last print ------------------------------------------------------------------------

def last_taker_side(events: Iterable[Any] | None) -> int | None:
    """The side of the last trade among a state's book events (0: the taker bought, 1: sold); None if none.

    Events arrive in the order the book applied them, so the scan runs backwards and stops at the first trade: on
    mainnet a book carries dozens of order and cancel events per state and this runs for every book on every state.
    """
    if not events:
        return None
    try:
        seq = events if isinstance(events, (list, tuple)) else list(events)
    except Exception:
        return None
    for ev in reversed(seq):
        t = trade_dict(ev)
        if t is not None:
            return int(t["s"])
    return None


def hit_side(taker_side: Any) -> str | None:
    """The resting side a trade consumed: a taker sell hits the bids ("buy"), a taker buy lifts the asks."""
    try:
        s = int(taker_side)
    except (TypeError, ValueError):
        return None
    if s == TAKER_SELL:
        return "buy"
    if s == TAKER_BUY:
        return "sell"
    return None


def level_quantity(levels: Any) -> float:
    """The resting quantity of the best level on one side; NaN when the side is empty or unreadable."""
    try:
        seq = list(levels or [])
    except Exception:
        return float("nan")
    if not seq:
        return float("nan")
    return _finite(_get(seq[0], "quantity", "q"))


def others_touch(
    levels: Iterable[Any] | None, *, own_ids: set, own_cids: set, max_levels: int = DETAIL_LEVELS,
    eps: float = 1e-12,
) -> tuple[float | None, float]:
    """(price, quantity) of the best level on one side counting only the OTHER traders' orders.

    Our own resting orders are subtracted by order id (the ledger's) or client id.  A level without an order list
    cannot be split and counts wholly as the others' (the v6.2.10 convention).  ``(None, nan)`` when nothing but our
    own orders rests in the detailed levels.
    """
    if not levels:
        return None, float("nan")
    try:
        seq = list(levels)[: max(1, int(max_levels))]
    except Exception:
        return None, float("nan")
    for lvl in seq:
        px = _finite(_get(lvl, "price", "p"))
        qty = _finite(_get(lvl, "quantity", "q"), 0.0)
        if not math.isfinite(px) or px <= 0.0 or qty <= eps:
            continue
        orders = _get(lvl, "orders", "o")
        own_q = 0.0
        if orders is not None:
            for o in orders:
                oid, cid = _get(o, "id", "i"), _get(o, "client_id", "c")
                if (oid is not None and oid in own_ids) or (cid is not None and cid in own_cids):
                    own_q += max(0.0, _finite(_get(o, "quantity", "q"), 0.0))
        rest = qty - own_q
        if rest > eps:
            return px, rest
    return None, float("nan")


def side_verdict(*, side: str, own_depth: Any, other_depth: Any, last_taker: Any, ok_token: str) -> str:
    """S2 for one adding side: ``ok_token``, or why the side may not rest here.

    Thin: the book shows less resting quantity on this side's best level than on the other side's -- the side a
    level is about to break on.  Just hit: the last print took liquidity from this side.  An unreadable depth never
    refuses (no information is not a thin book).
    """
    own, other = _finite(own_depth), _finite(other_depth)
    if math.isfinite(own) and math.isfinite(other) and own < other - 1e-12:
        return REASON_THIN_SIDE
    if hit_side(last_taker) == str(side or "").strip().lower():
        return REASON_JUST_HIT
    return ok_token


def select_sides(
    sides: dict[str, str], *, bid_depth: Any, ask_depth: Any, last_taker: Any, ok_token: str,
) -> tuple[dict[str, str], int, int]:
    """S2 at placement over one book's side verdicts: only a side that is ``ok_token`` can be refused.

    Returns (sides, thin refusals, just-hit refusals).  A side another rule already refused (the exit side, the band,
    the venue band) passes through unchanged.
    """
    out = dict(sides or {})
    thin = hit = 0
    for side, own, other in (("buy", bid_depth, ask_depth), ("sell", ask_depth, bid_depth)):
        if out.get(side) != ok_token:
            continue
        why = side_verdict(side=side, own_depth=own, other_depth=other, last_taker=last_taker, ok_token=ok_token)
        if why == REASON_THIN_SIDE:
            thin += 1
        elif why == REASON_JUST_HIT:
            hit += 1
        out[side] = why
    return out, thin, hit


def is_adding(side: int, net_base: Any, eps: float = 1e-9) -> bool:
    """Whether a fill of an order on ``side`` would grow the position: both sides on a flat book."""
    net = _finite(net_base, 0.0)
    if int(side) == SIDE_BUY:
        return net >= -abs(eps)
    return net <= abs(eps)


def life_verdict(
    *, side: int, price: Any, best_bid: Any, best_ask: Any, tick: Any,
    touch_on: bool, select_on: bool, adding: bool,
    own_depth: Any = float("nan"), other_depth: Any = float("nan"), last_taker: Any = None,
) -> str | None:
    """Whether one resting order should be cancelled now: CANCEL_BEHIND / CANCEL_THIN / CANCEL_HIT, or None.

    The touch rule (S1) applies to every order; the depth and flow rule (S2) only to an order that adds to the
    position, and it reads the others' best levels (our own order excluded) exactly as placement read the book.
    """
    if touch_on and behind_touch(side=side, price=price, best_bid=best_bid, best_ask=best_ask, tick=tick):
        return CANCEL_BEHIND
    if select_on and adding:
        token = "buy" if int(side) == SIDE_BUY else "sell"
        why = side_verdict(side=token, own_depth=own_depth, other_depth=other_depth, last_taker=last_taker,
                           ok_token="")
        if why == REASON_THIN_SIDE:
            return CANCEL_THIN
        if why == REASON_JUST_HIT:
            return CANCEL_HIT
    return None
