# SPDX-License-Identifier: MIT
"""v6.2.8: close at the own touch, size inventory against the band, quote only where a round trip can pay.

Why (v6.2.7 on UID 82 to tick 6,600, 2026-09-21, against v6.2.6 on the same sim as a matched control):

The loss RATE and the MAKING damage have different owners, and only the second one is a strategy defect.

* **The loss rate (58% -> 95%) is the testnet fee regime, not a rule.**  At matched round-trip fee the two
  versions lose at the same rate -- 20-40 bps/side: 99.6% vs 99.4%; 40-80: 100% vs 99.8%.  The new sim
  opened with an empty fee history and ramped to a median 40-50 bps/side; v6.2.7 captured the spread on
  43% of its round trips and the fee turned 89% of those into losses.  Testnet runs 38 active makers per
  book against 218 on mainnet, so its dynamic fee curve sits far above the 0.4 passive-share target.  That
  is not something to tune for -- but it IS something to refuse to trade into (rule S3).

* **The making damage is exit-path closes at the FAR touch.**  Exit-path fills are 47% of all fills.  As a
  fraction of the spread (0 = own touch, -1 = far touch) they landed at +0.00 in the old sim, -0.33 in
  v6.2.6's fresh segment and -0.96 in v6.2.7.  The driver in every segment is size: <=0.5 base closes at
  the own touch, >1 base at the far one.  At matched size, own-touch closes take 15 s and are gross-positive
  (+0.35 / +1.35 even at 45-bps fees); far-touch closes take 60-83 s and are gross-negative.  The aggressive
  rung buys no speed -- it is reached only after a position has already stalled, and the fill it finally
  gets gives back a full spread against the centred mid, on exactly the side making needs.

  The chain: ``inventory_pressure = 0.5*tanh(size/0.50) + 0.5*|net|/max_inventory_base(1.2)`` -- slot-era
  references against a 5.79 clip and a two-clip band -- plus age, drawdown and failure terms that sit at 1.0
  in this regime (weights sum 0.38), put urgency at 0.45-0.55, straddling ``competitive_max = 0.50``.
  ``classify_realization_rung`` returns AGGRESSIVE at >= 0.50 and ``maker_exit_price`` prices it at the far
  touch.  Same class of defect as the v6.2.6 exposure cap: two subsystems sized from different constants.

Four rules, one switch each:

* **S1 -- no aggressive maker rung.**  A maker exit rests on its own side of the mid; the aggressive rung
  fills on the wrong side by construction.  Intercepted at the PRICE, not the decision, because the frozen
  module produces it in three places (``_research_apply_unified_exit``, ``_research_place_maker_exit``,
  ``_research_parked_touch_exit``) and the unified exit also falls back to AGGRESSIVE whenever the ladder
  proposes TAKER and ``failed_exit_count >= 3`` -- a decision-level cap would miss both.  The taker rung
  above 0.70 is untouched: that is the emergency exit, and a cross is a different action.
* **S2 -- inventory measured against the band.**  The exit-urgency inventory ratio becomes
  ``|net| / band_for(clip)``, the acquire path's own answer to "how full is this book".  Scoped to exit
  evaluation only: ``InventorySnapshot`` carries no book id, so the book is bound for the duration of the
  one frozen call that feeds urgency, and the nine other ``_inventory_util`` callers (sizing, close
  triggers) keep their frozen meaning.
* **S3 -- quote only where a round trip can pay.**  A round trip at the touch captures at most the full
  spread and pays the maker fee twice.  A book whose spread is at or under twice its maker fee cannot make
  a net-positive round trip at any size, so it gets no new entries.  Every term is the book's own L1 and
  the validator's own fee for that book; rebate books are always viable.  Held inventory keeps its exits.
  **Retired before launch (switch default OFF).**  The rule guards net-of-fee PnL, which is not what is
  scored: the validator's making leg is capture against a centred mid and its skill leg is MTM alpha, and
  both ignore the fee fields the source carries; the volume cap is ``capital_turnover_cap`` times the
  INITIAL capital, so fee losses do not shrink it either.  Measured admission: 5 of 57 testnet books
  (median spread 29.2 bps against a 50 bps maker fee) and 42 of 101 mainnet books.  The code stays so
  the switch can be read, but a fee gate only ever costs making here.
* **S4 -- both sides, skewed, never frozen.**  The v6.2.7 corrective gate is retired: it quoted one side
  50% of the time and froze the book 41%, and the starved side's capture fell.  Here every book quotes
  both sides; the deficit side gets the full clip, the surplus side a clip scaled by the imbalance and
  floored at one minimum order.  No side is ever zeroed, so no book can fall into an absorbing state.
"""
from __future__ import annotations

import math
from typing import Any

V628_TOUCH_EXIT_VERSION = "touch_exit_v6_2_8"

ACTION_PASSIVE = "PASSIVE_MAKER_EXIT"
ACTION_COMPETITIVE = "COMPETITIVE_MAKER_EXIT"
ACTION_AGGRESSIVE = "AGGRESSIVE_MAKER_EXIT"

SIDE_BUY = "buy"
SIDE_SELL = "sell"

REASON_FEE_UNVIABLE = "FEE_UNVIABLE"

# Marks a maker_exit_price wrapper so nested scopes neither re-wrap nor restore the wrong original.
CAPPED_MARKER = "_v628_capped"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


# ---- S1: no aggressive maker rung ------------------------------------------------------------------

def cap_maker_action(action: Any) -> Any:
    """AGGRESSIVE -> COMPETITIVE; every other action (passive, competitive, taker, unknown) unchanged."""
    token = str(action or "").upper()
    if token == ACTION_AGGRESSIVE:
        return ACTION_COMPETITIVE
    return action


def capped_price_fn(original, on_capped=None):
    """Wrap ``maker_exit_price`` so an aggressive request is priced at the own touch.

    ``original`` is the frozen keyword-only function.  ``on_capped`` is called once per downgrade, for
    telemetry.  The wrapper carries ``CAPPED_MARKER`` so a nested scope can see it is already installed.
    """
    def capped(*, bid, ask, long_position, action, tick_size):
        capped_action = cap_maker_action(action)
        if capped_action is not action and on_capped is not None:
            try:
                on_capped()
            except Exception:
                pass
        return original(
            bid=bid, ask=ask, long_position=long_position, action=capped_action, tick_size=tick_size,
        )
    setattr(capped, CAPPED_MARKER, True)
    setattr(capped, "__wrapped__", original)
    return capped


# ---- S2: inventory measured against the band -------------------------------------------------------

def band_inventory_util(*, net_base: Any, clip: Any, band_clips: Any) -> float:
    """``|net| / (band_clips * clip)``: how full this book is against its own two-clip band."""
    band = max(1.0, _finite(band_clips, 2.0)) * max(0.0, _finite(clip))
    if band <= 1e-12:
        return 0.0
    return abs(_finite(net_base)) / band


# ---- S3: quote only where a round trip can pay -----------------------------------------------------

def spread_bps(best_bid: Any, best_ask: Any) -> float | None:
    """The touch spread in bps of mid, or None when the L1 is missing, zero or crossed."""
    bid, ask = _finite(best_bid, float("nan")), _finite(best_ask, float("nan"))
    if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0.0 or ask <= bid:
        return None
    mid = 0.5 * (bid + ask)
    return (ask - bid) / mid * 1e4


def fee_viable(*, spread_bps_value: Any, maker_fee_bps: Any) -> bool:
    """True when a round trip at the touch can clear two maker fees: ``spread > 2 * fee``.

    A rebate (negative fee) is always viable.  A missing spread is not judged here -- the frozen breadth
    verdict already refuses a book with no L1 -- so it returns True and leaves that to NO_L1.
    """
    if spread_bps_value is None:
        return True
    s = _finite(spread_bps_value, float("nan"))
    if not math.isfinite(s):
        return True
    fee = _finite(maker_fee_bps)
    if fee < 0.0:
        return True
    return s > 2.0 * fee + 1e-12


# ---- S4: both sides, skewed, never frozen ----------------------------------------------------------

def balance_ratio(buy: Any, sell: Any) -> float:
    """(buy - sell) / (|buy| + |sell|) in [-1, 1]; 0 when the book has no capture yet."""
    b, s = _finite(buy), _finite(sell)
    denom = abs(b) + abs(s)
    if denom <= 1e-12:
        return 0.0
    return max(-1.0, min(1.0, (b - s) / denom))


def skewed_sides(
    *,
    clip: Any,
    buy_capture: Any,
    sell_capture: Any,
    min_order: Any,
    lots_of,
) -> dict[str, float]:
    """Both sides always quote.  The deficit side gets the clip; the surplus side a scaled clip >= 1 lot.

    Making is ``2*min(buy, sell)`` per book, so size belongs on the side that is behind -- but the leading
    side's fills are what close the deficit side's round trips, so it is trimmed, never stopped.  Neither
    side ever exceeds the paced (and bounded) clip, and neither is ever zero.
    """
    base = max(0.0, _finite(clip))
    unit = max(1e-12, _finite(min_order, 0.25))
    if base <= 0.0:
        return {SIDE_BUY: 0.0, SIDE_SELL: 0.0}
    q = lots_of(base, unit)
    r = balance_ratio(buy_capture, sell_capture)
    if abs(r) <= 1e-12:
        return {SIDE_BUY: q, SIDE_SELL: q}
    deficit, surplus = (SIDE_SELL, SIDE_BUY) if r > 0.0 else (SIDE_BUY, SIDE_SELL)
    trimmed = lots_of(q * (1.0 - abs(r)), unit)       # lots_of floors to whole lots, at least one
    return {deficit: q, surplus: max(unit, min(q, trimmed))}
