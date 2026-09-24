# SPDX-License-Identifier: MIT
"""v6.3: every book holds inventory in the direction of its own recent move, and a making layer rests around it.

Why (mainnet UID 94, 14,040 recorded ticks, 09-23 14:47 to 09-24 13:30 JST; scratchpad v63/):

* The market trends.  With each book's own drift removed, consecutive returns correlate +0.22 at 10 s, +0.45 at 60 s,
  +0.64 at 300 s and fade to zero by 20 min; 97% of books show it and every 3,000-tick window does.
* The validator's alpha is sum(inventory x price change) minus mean inventory x the window's drift: it pays for being
  long while the price rises more than the window average.  A resting-order maker is filled on the side the price is
  running over, so its inventory points AGAINST the move (ours correlated -0.36 with the past 300 s return; the
  skill-positive family 233/215/101 +0.33) and its alpha is negative on almost every book (UID 94: 0 of 128).
* Replayed with the validator's own arithmetic, a calibrated fill model and our real 58 ms order delay: a target of
  +/-2 clips by the sign of the mid's 120 s move, quoted one tick inside, plus a two-sided making layer of one clip
  around it (only where a price one tick inside exists, not on the thin or just-hit side, and only on books whose
  maker fee is at or below the median) scores skill +5.3 / +5.4 / +5.3 and making 55 / 87 / 140 over the three
  3-h windows (skill 3.7-4.1 and making 147-157 are the live 233 family).  Making scales with the clip, and skill
  keeps its sign for every lookback from 60 to 300 s.

Rules, one switch each:

R1 trend target   ``research_v63_trend_target``  -- per book, target = +/-TARGET_CLIPS x clip by the sign of the
                  log-mid change over SIGNAL_STATES states; orders toward the target rest one tick inside the
                  others' best (the v6.2.10 rule) or at the touch, and chase it (v6.2.14 S1 cancels the order the
                  touch moved away from).  Resting orders only.  The frozen exit chain and the v6.2 acquisition pass
                  are not run: a position is the target, not something to exit.
R2 making layer   ``research_v63_making_layer``  -- one clip each side around the target while the inventory stays
                  inside [target - clip, target + clip]; placed only where a price one tick inside exists and not
                  on the thin or just-hit side (v6.2.14 S2 at placement, and cancelled when it turns so).
R3 fee cap        ``research_v63_fee_cap``       -- the making layer runs only on books whose live maker fee is at
                  or below the median maker fee across the books (makers pay ~48 bps on mainnet; the score is fee
                  blind, the capital is not).
R4 clip           ``research_v63_clip_base``     -- the order size in base (1.0 = four minimum orders).
R5 book stop      ``research_v63_book_stop``     -- a book whose own windowed alpha falls below half the skill floor
                  (``research_v63_alpha_floor``, the validator's published debeta_skill_floor) targets zero and
                  quotes no making layer until its alpha recovers above a quarter of the floor.
R6 lean log       ``research_v63_lean_log``       -- the per-quote and bookkeeping rows that repeat the same fact
                  (FILL_CAL, QUOTE, EXEC_PROB, A19_TICK_OBSERVE, the A17431/A17432 ownership and identity rows:
                  ~370 of the ~660 rows a request writes, measured 09-24) are written on every 10th state only.
                  Logging costs the request thread ~15-20 ms of its ~237 ms (measured); the lever is not latency
                  but the log volume (~400 KB per state) and the writer thread's GIL time.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from typing import Any, Iterable, Sequence

from research_v6210_touch_improve import improved_ask, improved_bid
from research_v6214_touch_life import REASON_JUST_HIT, REASON_THIN_SIDE, side_verdict

V63_TREND_TARGET_VERSION = "trend_target_v6_3_0"

SIGNAL_STATES = 120          # the lookback of the mid's move, in states (~ sim-s); 60-300 all keep skill positive
TARGET_CLIPS = 2             # the target |inventory|, in clips
MAKING_CLIPS = 1             # the making layer's width around the target, in clips
CLIP_BASE = 1.0              # the order size, in base (four minimum orders)
ALPHA_FLOOR_DEFAULT = 18.0   # the validator's published debeta_skill_floor (mainnet 09-24: 18.1)
BOOK_STOP_FRACTION = 0.5     # pause below -fraction x floor ...
BOOK_STOP_RELEASE = 0.25     # ... and resume above -release x floor

TARGET_CLIENT_ID_BASE = 60000
MAKING_CLIENT_ID_BASE = 65000
CLIENT_ID_STRIDE = 10
CLIENT_ID_BUY = 1
CLIENT_ID_SELL = 2

ROLE_TOWARD = "toward"
ROLE_MAKING = "making"
SIDE_BUY = "buy"
SIDE_SELL = "sell"

LEAN_LOG_EVERY_TICKS = 10
LEAN_SAMPLED_ROWS = frozenset({
    "FILL_CAL", "QUOTE", "EXEC_PROB", "A19_TICK_OBSERVE",
    "A17431_BOOK_OWNERSHIP_RESERVE", "A17431_BOOK_OWNERSHIP_RELEASE",
    "A17432_IDENTITY_RELEASE", "A17432_STALE_CANCEL_IGNORED",
})

CANCEL_UNWANTED = "UNWANTED"
CANCEL_THIN = "THIN_SIDE"
CANCEL_HIT = "JUST_HIT"
CANCEL_FEE = "FEE_CAPPED"
CANCEL_PAUSED = "BOOK_PAUSED"


def _finite(value: Any, default: float = float("nan")) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


# ---- identities --------------------------------------------------------------------------------------------------

def client_ids(book_id: int, role: str) -> tuple[int, int]:
    """(buy, sell) client ids of this book's orders of ``role``."""
    base = (TARGET_CLIENT_ID_BASE if role == ROLE_TOWARD else MAKING_CLIENT_ID_BASE) + int(book_id) * CLIENT_ID_STRIDE
    return base + CLIENT_ID_BUY, base + CLIENT_ID_SELL


def own_client_ids(book_id: int) -> set[int]:
    return set(client_ids(book_id, ROLE_TOWARD)) | set(client_ids(book_id, ROLE_MAKING))


def role_of(client_id: Any) -> str | None:
    """The role a client id was placed under, or None for an id that is not v6.3's."""
    try:
        cid = int(client_id)
    except (TypeError, ValueError):
        return None
    for base, role in ((TARGET_CLIENT_ID_BASE, ROLE_TOWARD), (MAKING_CLIENT_ID_BASE, ROLE_MAKING)):
        if base <= cid < base + CLIENT_ID_STRIDE * 500 and cid % CLIENT_ID_STRIDE in (CLIENT_ID_BUY, CLIENT_ID_SELL):
            return role
    return None


# ---- the signal and the target ----------------------------------------------------------------------------------

def log_mid_bps(best_bid: Any, best_ask: Any) -> float | None:
    """ln(mid) x 1e4 of a valid, uncrossed touch; None otherwise."""
    bid, ask = _finite(best_bid), _finite(best_ask)
    if not (math.isfinite(bid) and math.isfinite(ask)) or bid <= 0.0 or ask <= bid:
        return None
    return math.log(0.5 * (bid + ask)) * 1e4


def new_history(lookback: int = SIGNAL_STATES) -> deque:
    return deque(maxlen=max(1, int(lookback)) + 1)


def signal_bps(history: Sequence[float], lookback: int = SIGNAL_STATES) -> float | None:
    """The mid's move over the last ``lookback`` states, in bps; None until the history is long enough."""
    n = max(1, int(lookback))
    if len(history) < n + 1:
        return None
    return float(history[-1]) - float(history[-1 - n])


def target_base(signal: Any, *, target_abs: float, dead_zone_bps: float = 0.0) -> float:
    """+/-target_abs by the sign of the signal; 0 when there is none or it is inside the dead zone."""
    s = _finite(signal)
    if not math.isfinite(s) or abs(s) <= max(0.0, float(dead_zone_bps)):
        return 0.0
    return float(target_abs) if s > 0.0 else -float(target_abs)


def wanted(inventory: Any, target: float, *, clip: float, making_width: float) -> dict[str, float]:
    """How much each side may still buy or sell: toward the target, plus the making width beyond it."""
    inv = _finite(inventory, 0.0)
    c = max(0.0, float(clip)); w = max(0.0, float(making_width))
    return {SIDE_BUY: max(0.0, min(c, float(target) + w - inv)), SIDE_SELL: max(0.0, min(c, inv - (float(target) - w)))}


def role_for(side: str, inventory: Any, target: float, eps: float = 1e-9) -> str:
    """An order that moves the inventory toward the target is a target order; any other is the making layer's."""
    inv = _finite(inventory, 0.0)
    if side == SIDE_BUY:
        return ROLE_TOWARD if inv < float(target) - eps else ROLE_MAKING
    return ROLE_TOWARD if inv > float(target) + eps else ROLE_MAKING


# ---- the making layer's gates -----------------------------------------------------------------------------------

def fee_cap_bps(maker_fees_bps: Iterable[Any]) -> float | None:
    """The median live maker fee across the books; None when no fee is readable."""
    fees = [f for f in (_finite(x) for x in maker_fees_bps) if math.isfinite(f)]
    return float(statistics.median(fees)) if fees else None


def making_side_verdict(side: str, *, own_depth: Any, other_depth: Any, last_taker: Any) -> str | None:
    """CANCEL_THIN / CANCEL_HIT when the making layer may not rest on this side now; None when it may."""
    why = side_verdict(side=side, own_depth=own_depth, other_depth=other_depth, last_taker=last_taker, ok_token="")
    if why == REASON_THIN_SIDE:
        return CANCEL_THIN
    if why == REASON_JUST_HIT:
        return CANCEL_HIT
    return None


def improve_price(view: Any, side: str, tick: float) -> float | None:
    """One tick inside the others' best, or None where that is not a legal maker price (the v6.2.10 rule)."""
    if view is None:
        return None
    if side == SIDE_BUY:
        return improved_bid(others_bid=view.others_bid, others_ask=view.others_ask, raw_ask=view.raw_ask, tick=tick)
    return improved_ask(others_bid=view.others_bid, others_ask=view.others_ask, raw_bid=view.raw_bid, tick=tick)


def toward_price(view: Any, side: str, tick: float, *, raw_bid: float, raw_ask: float) -> float:
    """A target order's price: one tick inside the others' best wherever that still rests (strictly inside the
    spread), else the touch.

    Unlike the making layer, a target order is the only order of ours that may sit at the mid of a two-tick
    spread: the making layer never rests there (it needs a price strictly better than the mid), so nothing of
    ours can cross it.  Replay: this rule keeps most of the skill the strict rule gives up (v63/r16).
    """
    t = _finite(tick)
    if view is not None and math.isfinite(t) and t > 0.0:
        if side == SIDE_BUY:
            cand = round(float(view.others_bid) + t, 8)
            if cand < float(raw_ask) - 0.5 * t and cand > float(raw_bid) - 0.5 * t:
                return float(cand)
        else:
            cand = round(float(view.others_ask) - t, 8)
            if cand > float(raw_bid) + 0.5 * t and cand < float(raw_ask) + 0.5 * t:
                return float(cand)
    return float(raw_bid) if side == SIDE_BUY else float(raw_ask)


# ---- the book stop ----------------------------------------------------------------------------------------------

def stop_state(alpha: Any, floor: Any, paused: bool, *, fraction: float = BOOK_STOP_FRACTION,
               release: float = BOOK_STOP_RELEASE) -> bool:
    """Paused once the book's windowed alpha is below -fraction x floor; released above -release x floor."""
    a, f = _finite(alpha), _finite(floor)
    if not (math.isfinite(a) and math.isfinite(f)) or f <= 0.0:
        return bool(paused)
    if a < -fraction * f:
        return True
    if a > -release * f:
        return False
    return bool(paused)


# ---- the lean log -----------------------------------------------------------------------------------------------

def lean_drop(event_type: Any, tick: Any, *, every: int = LEAN_LOG_EVERY_TICKS) -> bool:
    """True when a sampled row is off its sample state and is not written."""
    if str(event_type) not in LEAN_SAMPLED_ROWS:
        return False
    try:
        t = int(tick or 0)
    except (TypeError, ValueError):
        return False
    return t % max(1, int(every)) != 0


# ---- the exposure caps ------------------------------------------------------------------------------------------

def caps_for(book_count: int, *, clip: float, target_clips: int = TARGET_CLIPS,
             making_clips: int = MAKING_CLIPS) -> dict[str, float]:
    """The exposure bound the universe implies: every book may hold its target plus one making clip."""
    n = max(1, int(book_count))
    per_book = (max(0, int(target_clips)) + max(0, int(making_clips))) * max(0.0, float(clip))
    return {"research_max_total_abs_base": float(n) * per_book, "research_a195_max_seed_abs_base": float(n) * per_book}
