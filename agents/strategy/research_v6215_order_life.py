# SPDX-License-Identifier: MIT
"""v6.2.15: an order lives as long as its reason to rest, a book side is owned by its own order, and a position
whose mid mark is inside ABSOLUTE_PROTECTION is closed by the chain's own taker.

Mainnet UID 94 (v6.2.14, ticks 1-1,000, recorder with the full L1 order queue):

* The winners' maker orders fill at a median age of 7.0-8.0 s (p75 15-17 s, p90 27-36 s); ours at 0.7 s, because
  every order dies at the 4-s GTT and is re-placed at the back of the queue.  Exits leave the book at exactly
  4.0 s.  Of our early-cancelled entries that were alone at their price, 40 / 66 / 79% would have filled within
  10 / 30 / 60 s had they stayed.
* One live order owns the whole book: ~40 books per state are refused because an exit rests there, so a held book
  never rests its adding side while its exit waits.
* The v6.1 no-loss floor rewrites every loss-realizing taker into a resting maker, so a position that runs away
  is held: round trips older than 60 s (13.9%) average -113 bps and carry -15.8 of the -13.4 bps round-trip mean.

The three rules, one switch each (all off = v6.2.14):

S1 order life.  An entry or exit lives until it is filled, cancelled by a condition rule (v6.2.14: the touch moved
   away, the adding side turned thin or was just hit), or its backstop expires.  The backstop is not a strategy
   clock: it is the validator's presence window (an agent is absent after 50 failed queries, one query per
   1-s state), the longest a resting order may outlive the agent that manages it.
S2 side ownership.  A live order blocks new placements on its own book side only.  The exit side and the adding
   side of a held book are owned independently; the band still bounds what the adding side may add.
S3 loss stop.  A position whose mid mark against its VWAP entry is inside ABSOLUTE_PROTECTION (the frozen
   corridor's own boundary, the one A1.9.7 P1 already uses) keeps the chain's ABSOLUTE taker: the v6.1 floor does
   not rewrite it into a maker, the executor does not refuse it, and a resting exit does not hide the book from
   the evaluation that sends it.
"""

from __future__ import annotations

from typing import Any, Iterable

from research_direct_exit_ledger import LEDGER_REMOVED_TTL_SWEEP, LEDGER_SWEEP_GRACE_MS
from research_direct_postfill_protection import position_mark_bps, postfill_protect_eligible

V6215_ORDER_LIFE_VERSION = "order_life_v6_2_15"

# The validator's presence window: an agent whose last 50 queries all failed is absent (reward.py presence gate).
PRESENCE_WINDOW_QUERIES = 50
# The validator publishes one state per simulated second.
STATE_INTERVAL_NS = 1_000_000_000
BACKSTOP_NS = PRESENCE_WINDOW_QUERIES * STATE_INTERVAL_NS
BACKSTOP_MS = BACKSTOP_NS / 1_000_000.0

# Client-id families whose life S1 owns: v6.2.x touch entries (70000 + 10 * book + 1|2) and v6.2.14 exits
# (80000 + 10 * book + 1|2).  Compaction (91000) and dust normalizer orders keep their own short life.
ENTRY_CLIENT_ID_BASE = 70000
EXIT_CLIENT_ID_BASE = 80000
CLIENT_ID_FAMILY_SPAN = 10000

SIDE_BUY = "buy"
SIDE_SELL = "sell"

STOP_REASON = "V6215_LOSS_STOP"


def _finite(value: Any) -> float | None:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if out == out and out not in (float("inf"), float("-inf")) else None


# ---- S1: order life -------------------------------------------------------------------------------------------

def life_ms(order_life_on: bool, configured_ms: Any, default_ms: float) -> float:
    """The age below which a resting order is still ours to manage: the backstop under S1, else the configured TTL."""
    if order_life_on:
        return float(BACKSTOP_MS)
    value = _finite(configured_ms)
    return float(value) if value is not None and value > 0.0 else float(default_ms)


def sweep_grace_ms(order_life_on: bool) -> float:
    """How long past placement a ledger row may go without a removal notice before it is swept."""
    return float(BACKSTOP_MS + LEDGER_SWEEP_GRACE_MS) if order_life_on else float(LEDGER_SWEEP_GRACE_MS)


def owned_family(client_id: Any) -> bool:
    """True for the entry and exit client ids whose life S1 sets."""
    try:
        cid = int(client_id)
    except (TypeError, ValueError):
        return False
    return any(base <= cid < base + CLIENT_ID_FAMILY_SPAN for base in (ENTRY_CLIENT_ID_BASE, EXIT_CLIENT_ID_BASE))


def is_gtt_limit(kind: Any, time_in_force: Any) -> bool:
    """A resting limit order with an expiry (the only kind whose life S1 sets)."""
    if "LIMIT" not in str(kind or "").upper():
        return False
    token = str(getattr(time_in_force, "name", time_in_force) or "").upper()
    return token in {"GTT", "1", "TIMEINFORCE.GTT"}


def backstop_expiry(current_ns: Any) -> int | None:
    """The expiry S1 gives an owned order, or None when it already has it."""
    try:
        now = int(current_ns or 0)
    except (TypeError, ValueError):
        now = 0
    return None if now == int(BACKSTOP_NS) else int(BACKSTOP_NS)


def sweep_with_grace(ledger, now_ns: Any, grace_ms: float) -> int:
    """``DirectExitLedger.sweep`` with a caller-chosen grace, on the same rows and the same removal vocabulary.

    A clock that moved backwards means the simulation restarted and reused order ids: the ledger resets, exactly as
    its own sweep does.  Returns the number of rows removed.
    """
    try:
        now = float(now_ns)
    except (TypeError, ValueError):
        return 0
    if now <= 0.0:
        return 0
    orders = getattr(ledger, "orders", None)
    if not isinstance(orders, dict):
        return 0
    newest = max((row.placed_ns for row in orders.values()), default=0)
    if newest > 0 and now < float(newest):
        return int(ledger.reset())
    cutoff = float(grace_ms) * 1e6
    stale = [oid for oid, row in orders.items() if row.placed_ns > 0 and (now - float(row.placed_ns)) > cutoff]
    remember = getattr(ledger, "_remember_removal", None)
    for oid in stale:
        orders.pop(oid, None)
        if callable(remember):
            remember(oid, LEDGER_REMOVED_TTL_SWEEP)
        try:
            ledger.swept = int(getattr(ledger, "swept", 0) or 0) + 1
        except (AttributeError, TypeError):
            pass
    return len(stale)


# ---- S2: side ownership ---------------------------------------------------------------------------------------

def order_side_token(side: Any) -> str:
    """The venue sends a buy as 0 and a sell as 1; ``buy``/``sell`` tokens pass through."""
    token = "" if side is None else str(getattr(side, "name", side)).strip().lower()
    if token in {"0", "buy", "bid", "b"} or token.endswith(".buy"):
        return SIDE_BUY
    if token in {"1", "sell", "ask", "s"} or token.endswith(".sell"):
        return SIDE_SELL
    return ""


def reducing_side(net_base: Any) -> str:
    """The side that closes the position: sell a long, buy a short."""
    net = _finite(net_base) or 0.0
    return SIDE_SELL if net > 0.0 else SIDE_BUY


def live_sides(order_sides: Iterable[Any], pending_sides: Iterable[Any]) -> frozenset:
    """The book sides a live order owns: acknowledged orders (account view) plus local pending reservations.

    An order whose side cannot be read owns the whole book, as every order did before S2.
    """
    out = set()
    for side in list(order_sides) + list(pending_sides):
        token = order_side_token(side)
        if not token:
            return frozenset((SIDE_BUY, SIDE_SELL))
        out.add(token)
    return frozenset(out)


def mark_live_sides(sides: dict, live: Iterable[str], *, ok_token: str, live_token: str) -> tuple[dict, int]:
    """Refuse, per side, a quote whose side already rests an order of ours.  Returns (sides, refused)."""
    owned = {order_side_token(s) for s in live}
    out = dict(sides)
    refused = 0
    for side, why in list(out.items()):
        if why == ok_token and order_side_token(side) in owned:
            out[side] = live_token
            refused += 1
    return out, refused


# ---- S3: loss stop --------------------------------------------------------------------------------------------

def stop_mark_bps(*, net_base: Any, vwap_entry: Any, mid: Any, fallback: Any = None) -> float | None:
    """The position's mid mark against its VWAP entry, in bps (the A1.9.7 mark)."""
    return position_mark_bps(net_base=net_base, vwap_entry=vwap_entry, mid=mid, fallback=fallback)


def stop_eligible(mark_bps: Any) -> bool:
    """True when the mid mark alone is inside ABSOLUTE_PROTECTION (the frozen corridor's boundary)."""
    return bool(postfill_protect_eligible(mark_bps))
