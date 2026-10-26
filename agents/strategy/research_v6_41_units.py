"""v6.41 (scratchpad k144): pure rules for the per-class units of the legacy layers (B2) and C2Q's reduce-only exit and
inventory-aware probe (B2b).  STRUCTURAL: every number here is the venue's (the
class's tick, price / volume decimals, minimum) or the agent's own (its deep clip); nothing is sized to a sample.

B2  UnitState / unit_state: a read-through view of a state whose configuration carries one class's price / volume
    decimals -- the frozen base's code run on it prices and rounds in that class's units (V640Part's maker sanitizer
    uses the same idea with a bare namespace; the legacy layers read more of the state, so the view delegates every
    other attribute to the real state).
B2b reduce_exit: the one passive exit a disarmed class-2 book keeps -- on the side that reduces |inventory|, at most one
    clip and never more than the position (a remainder below the class minimum is closed with it), joining that side's
    best price (post-only: a sell at the best ask is above the best bid).  probe_held: during a C2Q probe the side that
    would add to |inventory| holds once the book carries a clip or more (the reducing side always stays).
(v6.42 candidates, NOT in v6.41 -- coordinator 21:1x JST 10-24: per-class v6.6.1 / v6.36 pools (they change traded-"0"
class 0 and vm739 pools every tape book) and S14's estimate per class (it never bound in the harness).)"""
from __future__ import annotations

import math
from typing import Any

V6_41_UNITS_VERSION = "units_v6_41"
SIDE_BUY = "buy"                 # research_v63_trend_target's side tokens
SIDE_SELL = "sell"
_EPS = 1e-9


def _finite(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return v if math.isfinite(v) else None


# ---- B2: a state in one class's units ----------------------------------------------------------------------------------

class UnitConfig:
    """The state's configuration with ``priceDecimals`` / ``volumeDecimals`` of one class; every other attribute read
    through to the real configuration."""

    __slots__ = ("_v641_cfg", "priceDecimals", "volumeDecimals")

    def __init__(self, cfg: Any, price_decimals: int, volume_decimals: int) -> None:
        object.__setattr__(self, "_v641_cfg", cfg)
        object.__setattr__(self, "priceDecimals", int(price_decimals))
        object.__setattr__(self, "volumeDecimals", int(volume_decimals))

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_v641_cfg"), name)


class UnitState:
    """The state with ``config`` in one class's units (UnitConfig); every other attribute read through to the real
    state (books, accounts, timestamp ...)."""

    __slots__ = ("_v641_state", "config")

    def __init__(self, state: Any, config: Any) -> None:
        object.__setattr__(self, "_v641_state", state)
        object.__setattr__(self, "config", config)

    def __getattr__(self, name: str) -> Any:
        return getattr(object.__getattribute__(self, "_v641_state"), name)


def unit_state(state: Any, price_decimals: int, volume_decimals: int) -> Any:
    """``state`` seen in one class's price / volume decimals (a dict configuration: a copy with the two keys set)."""
    cfg = getattr(state, "config", None)
    if isinstance(cfg, dict):
        view = dict(cfg)
        view["priceDecimals"], view["volumeDecimals"] = int(price_decimals), int(volume_decimals)
    else:
        view = UnitConfig(cfg, price_decimals, volume_decimals)
    return UnitState(state, view)


# ---- B2b: C2Q's reduce-only exit and inventory-aware probe ----------------------------------------------------------------

def reducing_side(position: Any) -> str | None:
    """The side that reduces |position| (a sell when long, a buy when short); None when flat or unreadable."""
    p = _finite(position)
    if p is None or abs(p) <= _EPS:
        return None
    return SIDE_SELL if p > 0.0 else SIDE_BUY


def adding_side(position: Any) -> str | None:
    """The side that adds to |position| (a buy when long, a sell when short); None when flat or unreadable."""
    p = _finite(position)
    if p is None or abs(p) <= _EPS:
        return None
    return SIDE_BUY if p > 0.0 else SIDE_SELL


def lot(q: Any, volume_decimals: Any) -> float:
    """``q`` floored to the volume grid (an exit never sells more than it holds)."""
    v = max(0.0, _finite(q) or 0.0)
    try:
        d = max(0, int(volume_decimals))
    except (TypeError, ValueError):
        d = 4
    f = 10.0 ** d
    return float(math.floor(v * f + 1e-6) / f)


def exit_quantity(position: Any, clip: Any, min_order: Any, volume_decimals: Any) -> float:
    """The reduce-only exit's size in the class's units: one clip, never more than |position|; a remainder below the
    class minimum is closed with it (an exit leaves 0 or at least one minimum); 0.0 when the size is below the minimum."""
    p = abs(_finite(position) or 0.0)
    c = _finite(clip) or 0.0
    vmin = _finite(min_order) or 0.0
    if p <= _EPS or c <= 0.0:
        return 0.0
    q = min(p, c)
    if 0.0 < p - q < vmin - _EPS:
        q = p
    q = lot(q, volume_decimals)
    if q + 1e-12 < vmin or q <= 0.0:
        return 0.0
    return q


def reduce_exit(position: Any, clip: Any, min_order: Any, volume_decimals: Any, bid: Any, ask: Any):
    """(side, quantity, price) of the reduce-only passive exit of a disarmed book: the reducing side, exit_quantity, that
    side's best price (a sell at the best ask, a buy at the best bid: both strictly inside the other side, so post-only
    legal); None when flat, below the minimum or without an uncrossed L1."""
    side = reducing_side(position)
    b, a = _finite(bid), _finite(ask)
    if side is None or b is None or a is None or b <= 0.0 or a <= b:
        return None
    q = exit_quantity(position, clip, min_order, volume_decimals)
    if q <= 0.0:
        return None
    return side, q, (a if side == SIDE_SELL else b)


def keeps_exit(row_side: str, row_price: Any, row_qty: Any, want: tuple, position: Any, tick: Any) -> bool:
    """A resting order is the exit to keep: on the wanted side, within half a tick of the wanted price, and its
    remaining size never more than |position| (it cannot cross zero)."""
    side, _q, price = want
    rp, rq, t = _finite(row_price), _finite(row_qty), _finite(tick) or 0.0
    if row_side != side or rp is None or rq is None or price is None:
        return False
    if abs(rp - float(price)) > 0.5 * t + 1e-12:
        return False
    return rq <= abs(_finite(position) or 0.0) + 1e-9


def probe_held(position: Any, clip: Any) -> frozenset:
    """During a C2Q probe: the side that adds to |position| holds once |position| is at least one clip (class-0 units,
    as the pass's inventory); the reducing side is never held.  Flat or below one clip: nothing held."""
    p, c = _finite(position), _finite(clip)
    if p is None or c is None or c <= 0.0 or abs(p) + _EPS < c:
        return frozenset()
    side = adding_side(p)
    return frozenset((side,)) if side is not None else frozenset()
