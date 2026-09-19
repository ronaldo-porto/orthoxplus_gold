# SPDX-License-Identifier: MIT
"""v6.2.5: two sides on every book, each book's clip paced to the validator's turnover cap.

Why (measured 2026-09-20 on the validator's own per-book gauges, testnet UID 82 and the live mainnet
field; scratchpad ``cap625/``):

* Making per unit of maker volume is already at the field's median: ours 1.5 bps of the 3 sim-h
  window's maker volume against a mainnet median of 1.55 (p75 2.74).  What is missing is volume:
  1.1M per window against a field median of 8.0M, which is exactly the validator's turnover cap
  spread evenly -- 10 × 50,000 quote per (uid, book) per 24 sim-h is 62,500 per book per 3 sim-h,
  and 62,500 × 128 = 8.0M.  The v6.2.0 docstring's "the lever is breadth, not lot size" assumed the
  engine already reached that pace at one minimum order per fill; it reaches a seventh of it.
* Half of what we do capture is discarded by the formula: making counts 2·min(buy, sell) per book,
  and our books are one-sided because a book with a lot quotes only its exit (``universe_verdict``
  returns NOT_FLAT for any |net| over the execution epsilon).  Balance is 50% against 86% for the
  median mainnet maker, and 88-93% for the top ten.
* Kappa is the same machine's other leg.  It counts clean closing seconds per book, median over
  books, so it is set by how OFTEN a book closes: at the cap pace a round trip spends 2 × clip, so
  the clip alone fixes the cadence -- 0.25 BASE gives ~446 closes per book per window (one per 24 s),
  0.5 gives ~223, 1.0 gives ~112.  Making does not care how the volume is cut up.  One rule serves
  both: take the SMALLEST clip that still reaches the pace.

What this module decides, per book and per request:

* ``sides_verdict``: which sides may be quoted.  A flat book gets both, exactly as v6.2.0.  A book
  holding inventory gets its ADDING side too, while |net| + clip stays inside the band; its reducing
  side belongs to the exit path and is left alone.  Every other check (L1, crossed, live order,
  volume cap, instruction budget, balances) is the frozen ``research_v62_breadth.universe_verdict``,
  evaluated on the same facts with the inventory masked, so only the NOT_FLAT rule is lifted.
* the band: ``BAND_CLIPS`` × clip = one held clip plus one opposite, the smallest band in which a
  book holding a clip can still quote both sides.  The unit is the venue's minimum order.
* ``paced_clip``: the clip for the next sample, from the book's own realised volume.  The target is
  the validator's cap over the validator's own period (``PACE_PERIOD_NS``), the sample is the
  validator's own volume sampling interval (``PACE_SAMPLE_NS``), the measurement is the venue's
  ``account.traded_volume``, and the step is damped and quantised to whole minimum orders.  No
  constant here is sized from an observation.
* ``cap_reserve_ok``: adding stops before the cap with the room a full exit needs, because at the
  cap the validator drops every non-cancel instruction on the book -- exits included.

Off (either switch) the caller keeps v6.2.4 exactly: flat books only, one minimum order per side.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any

from research_v62_breadth import (
    REASON_OK as BREADTH_REASON_OK,
    BookFacts,
    universe_verdict as breadth_universe_verdict,
)

V625_CAP_PACED_VERSION = "cap_paced_maker_v6_2_5"

# The validator's own constants (taos/im/validator/query.py, scoring.activity.*).
PACE_PERIOD_NS = 86_400_000_000_000       # trade_volume_assessment_period: the cap's 24 sim-h
PACE_SAMPLE_NS = 600_000_000_000          # trade_volume_sampling_interval: the pace sample

# The band is a held clip plus one opposite: the smallest inventory in which both sides can rest.
BAND_CLIPS = 2.0
# A sample may at most double or halve the clip.  Damping, not a size: the pace is the target.
CLIP_STEP = 2.0

SIDE_BUY = "buy"
SIDE_SELL = "sell"
SIDES = (SIDE_BUY, SIDE_SELL)

REASON_OK = "OK"
REASON_BAND = "BAND"                 # the adding side would take |net| past the band
REASON_EXIT_SIDE = "EXIT_SIDE"       # the reducing side is the exit path's
REASON_CAP_RESERVE = "CAP_RESERVE"   # adding would eat the room the exit needs under the cap


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def band_for(clip: Any) -> float:
    """The inventory band that lets a book holding one clip still quote both sides."""
    return BAND_CLIPS * max(0.0, _finite(clip))


def lots_of(qty: Any, min_order: Any) -> float:
    """``qty`` quantised DOWN to whole minimum orders, at least one (the venue's granularity)."""
    unit = max(1e-12, _finite(min_order, 0.25))
    n = math.floor((max(0.0, _finite(qty)) + 1e-9) / unit)
    return unit * float(max(1, int(n)))


def with_cap_ok(facts: BookFacts, cap_ok: Any) -> BookFacts:
    """The same facts with the volume-cap answer re-taken for the paced clip."""
    return replace(facts, cap_ok=bool(cap_ok))


def sides_verdict(
    facts: BookFacts,
    *,
    clip: float,
    flat_eps: float,
    two_sided: bool,
) -> dict[str, str]:
    """Why each side of this book does or does not get its touch quote this request.

    The shared checks are the frozen breadth verdict on the same facts with the inventory masked,
    so a held book is refused for exactly the reasons a flat one would be.  Only NOT_FLAT is lifted,
    and only for the side that adds to the position, and only inside the band.
    """
    net = _finite(getattr(facts, "net_base", 0.0))
    eps = max(0.0, _finite(flat_eps))
    lot = max(0.0, _finite(clip))
    shared = breadth_universe_verdict(replace(facts, net_base=0.0), lot=lot, flat_eps=eps)
    if shared != BREADTH_REASON_OK:
        return {SIDE_BUY: shared, SIDE_SELL: shared}
    if abs(net) <= eps:
        return {SIDE_BUY: REASON_OK, SIDE_SELL: REASON_OK}
    if not two_sided:
        return {SIDE_BUY: "NOT_FLAT", SIDE_SELL: "NOT_FLAT"}
    adding = SIDE_BUY if net > 0.0 else SIDE_SELL
    reducing = SIDE_SELL if net > 0.0 else SIDE_BUY
    within = (abs(net) + lot) <= band_for(lot) + 1e-12
    return {adding: REASON_OK if within else REASON_BAND, reducing: REASON_EXIT_SIDE}


def pace_target_rate(cap_quote: Any, *, period_ns: Any = PACE_PERIOD_NS) -> float:
    """The cap spread over its own period, in quote per ns.  ``0`` when there is no cap."""
    cap = max(0.0, _finite(cap_quote))
    period = max(1.0, _finite(period_ns, float(PACE_PERIOD_NS)))
    if cap <= 0.0:
        return 0.0
    return cap / period


def observed_rate(*, prev_ns: Any, prev_volume: Any, now_ns: Any, volume: Any) -> float | None:
    """Realised quote per ns on this book since the last sample, or None when there is no sample."""
    if prev_ns is None:
        return None
    dt = _finite(now_ns) - _finite(prev_ns)
    if dt < float(PACE_SAMPLE_NS):
        return None
    dv = _finite(volume) - _finite(prev_volume)
    if dv < 0.0:      # the venue's 24 h window rolled; take the next sample as the first
        return None
    return dv / dt


def paced_clip(
    *,
    clip_now: Any,
    min_order: Any,
    target_rate: Any,
    obs_rate: Any,
    step: Any = CLIP_STEP,
    ceiling: Any = None,
) -> float:
    """The next clip: the smallest whole number of minimum orders that tracks the cap pace.

    ``obs_rate`` None leaves the clip where it is (no sample yet).  A book trading nothing steps up
    by ``step``; one over pace steps down, never below one minimum order.  ``ceiling`` (balances,
    cap headroom) clamps the result.
    """
    unit = max(1e-12, _finite(min_order, 0.25))
    now = max(unit, _finite(clip_now, unit))
    if obs_rate is None:
        return lots_of(now, unit)
    target = max(0.0, _finite(target_rate))
    if target <= 0.0:
        return lots_of(now, unit)
    hi = max(1.0, _finite(step, CLIP_STEP))
    rate = max(0.0, _finite(obs_rate))
    ratio = hi if rate <= 0.0 else min(hi, max(1.0 / hi, target / rate))
    want = lots_of(now * ratio, unit)
    if ceiling is not None:
        cap_clip = _finite(ceiling)
        if cap_clip >= unit:
            want = min(want, lots_of(cap_clip, unit))
        else:
            want = unit
    return max(unit, want)


def clip_ceiling(
    *,
    min_order: Any,
    base_free: Any,
    quote_free: Any,
    price: Any,
    cap_remaining: Any,
) -> float:
    """The largest clip this book's own balances and cap headroom can carry on both sides."""
    unit = max(1e-12, _finite(min_order, 0.25))
    px = _finite(price)
    by_base = max(0.0, _finite(base_free))
    by_quote = max(0.0, _finite(quote_free)) / px if px > 0.0 else 0.0
    out = min(by_base, by_quote)
    remaining = max(0.0, _finite(cap_remaining))
    if px > 0.0 and remaining > 0.0:
        out = min(out, remaining / (2.0 * px))   # one round trip has to fit under the cap
    return max(0.0, out) if out >= unit else 0.0


def cap_reserve_ok(
    *,
    cap_quote: Any,
    used: Any,
    net_base: Any,
    mid: Any,
    clip: Any,
) -> bool:
    """May this book still add?  At the cap the validator drops every non-cancel instruction on the
    book, so the exit of whatever we hold -- plus the clip we are about to add -- must still fit."""
    cap = _finite(cap_quote)
    if cap <= 0.0:
        return True
    px = _finite(mid)
    if px <= 0.0:
        return False
    lot = max(0.0, _finite(clip))
    reserve = (abs(_finite(net_base)) + lot) * px
    return (_finite(used) + 2.0 * lot * px + reserve) <= cap


@dataclass
class BookPace:
    """One book's pacing state, as the telemetry reports it."""
    clip: float
    obs_rate: float | None
    target_rate: float
    sampled_ns: int | None
    volume: float

    def as_log(self) -> dict[str, Any]:
        ratio = None
        if self.obs_rate is not None and self.obs_rate > 0.0 and self.target_rate > 0.0:
            ratio = round(self.target_rate / self.obs_rate, 4)
        return {
            "clip": round(float(self.clip), 4),
            "obs_rate": None if self.obs_rate is None else round(float(self.obs_rate), 9),
            "target_rate": round(float(self.target_rate), 9),
            "pace_ratio": ratio,
            "volume": round(float(self.volume), 2),
        }


def pace_snapshot(paces: dict[int, BookPace], *, min_order: Any = 0.25) -> dict[str, Any]:
    """Portfolio view of the pacing state for V62_STATE."""
    unit = max(1e-12, _finite(min_order, 0.25))
    clips = sorted(float(p.clip) for p in paces.values())
    ratios = sorted(
        float(p.target_rate / p.obs_rate)
        for p in paces.values()
        if p.obs_rate is not None and p.obs_rate > 0.0 and p.target_rate > 0.0
    )
    def p50(xs: list[float]) -> float | None:
        return None if not xs else round(xs[len(xs) // 2], 4)
    return {
        "books": len(paces),
        "clip_p50": p50(clips),
        "clip_max": None if not clips else round(clips[-1], 4),
        "clip_at_min": sum(1 for c in clips if c <= unit + 1e-12),
        "pace_ratio_p50": p50(ratios),
        "sampled": sum(1 for p in paces.values() if p.sampled_ns is not None),
    }
