# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 1.7: OFI and delayed adverse-selection measurement.

Pure functions so unit tests do not import Strategy1 / bittensor.

OFI follows Cont–Kukanov–Stoikov and is computed only from consecutive
best-bid / best-ask price AND size snapshots. Static top-of-book imbalance
is never labeled OFI.

Maker markouts are side-corrected and delayed (100/250/500/1000 ms).
ExpectedMarkout and AdverseSelectionRisk are shrunk empirical estimates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from research_score_ev import adverse_selection_risk, conservative_markout_bps
from research_markout import CONSERVATIVE_MARKOUT_FALLBACK_BPS

MARKOUT_WEIGHTS_MS = {
    100: 0.15,
    250: 0.30,
    500: 0.35,
    1000: 0.20,
}
OFI_FAST_ALPHA = 0.45


def _finite(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


@dataclass(frozen=True)
class BookTouch:
    bid_px: float
    bid_qty: float
    ask_px: float
    ask_qty: float


@dataclass(frozen=True)
class OfiSnapshot:
    ofi_raw: float | None
    ofi_normalized: float | None
    ofi_fast: float | None
    supported: bool
    source: str

    def as_log(self) -> dict[str, Any]:
        return {
            "ofi_raw": self.ofi_raw,
            "ofi_normalized": self.ofi_normalized,
            "ofi_fast": self.ofi_fast,
            "ofi_supported": int(bool(self.supported)),
            "ofi_source": self.source,
        }


def extract_touch(book: Any) -> BookTouch | None:
    """Require best bid/ask price and quantity. Missing size means OFI is unsupported."""
    bids = getattr(book, "bids", None) or []
    asks = getattr(book, "asks", None) or []
    if not bids or not asks:
        return None
    bid_px = _finite(getattr(bids[0], "price", None))
    ask_px = _finite(getattr(asks[0], "price", None))
    if bid_px is None or ask_px is None or ask_px <= bid_px:
        return None
    if not hasattr(bids[0], "quantity") or not hasattr(asks[0], "quantity"):
        return None
    bid_qty = _finite(getattr(bids[0], "quantity", None))
    ask_qty = _finite(getattr(asks[0], "quantity", None))
    if bid_qty is None or ask_qty is None or bid_qty < 0.0 or ask_qty < 0.0:
        return None
    return BookTouch(bid_px=bid_px, bid_qty=bid_qty, ask_px=ask_px, ask_qty=ask_qty)


def ofi_increment(prev: BookTouch, curr: BookTouch) -> float:
    """Cont–Kukanov–Stoikov best-level order flow increment e_n."""
    if curr.bid_px > prev.bid_px:
        e_bid = curr.bid_qty
    elif curr.bid_px == prev.bid_px:
        e_bid = curr.bid_qty - prev.bid_qty
    else:
        e_bid = -prev.bid_qty
    if curr.ask_px < prev.ask_px:
        e_ask = -curr.ask_qty
    elif curr.ask_px == prev.ask_px:
        e_ask = -(curr.ask_qty - prev.ask_qty)
    else:
        e_ask = prev.ask_qty
    return e_bid + e_ask


def normalize_ofi(raw: float, touch: BookTouch) -> float | None:
    depth = touch.bid_qty + touch.ask_qty
    if depth <= 1e-12:
        return None
    return float(raw) / depth


def ofi_against_position(ofi: float | None, inventory_sign: float) -> float:
    """Positive when order flow is running against the open inventory."""
    if ofi is None:
        return 0.0
    flow = float(ofi)
    sign = float(inventory_sign)
    if sign > 0.0:
        return max(0.0, -flow)
    if sign < 0.0:
        return max(0.0, flow)
    return abs(flow)


UNSUPPORTED = OfiSnapshot(
    ofi_raw=None,
    ofi_normalized=None,
    ofi_fast=None,
    supported=False,
    source="UNSUPPORTED",
)


class OfiTracker:
    """Per-book consecutive-touch OFI. First snapshot never invents a value."""

    def __init__(self, *, fast_alpha: float = OFI_FAST_ALPHA) -> None:
        self.fast_alpha = min(0.95, max(0.05, float(fast_alpha)))
        self._prev: dict[int, BookTouch] = {}
        self._fast: dict[int, float] = {}

    def update(self, book_id: int, touch: BookTouch | None) -> OfiSnapshot:
        if touch is None:
            self._prev.pop(int(book_id), None)
            return UNSUPPORTED
        prev = self._prev.get(int(book_id))
        self._prev[int(book_id)] = touch
        if prev is None:
            return UNSUPPORTED
        raw = ofi_increment(prev, touch)
        norm = normalize_ofi(raw, touch)
        prev_fast = self._fast.get(int(book_id))
        if norm is None:
            fast = prev_fast
        elif prev_fast is None:
            fast = norm
        else:
            fast = self.fast_alpha * norm + (1.0 - self.fast_alpha) * prev_fast
        if fast is not None:
            self._fast[int(book_id)] = float(fast)
        return OfiSnapshot(
            ofi_raw=raw,
            ofi_normalized=norm,
            ofi_fast=fast,
            supported=True,
            source="OFI",
        )


def expected_markout_bps(
    horizon_stats: dict[int, dict[str, float]] | None,
    *,
    min_samples: int = 4,
    prior_strength: float = 8.0,
) -> float:
    """Horizon-weighted ExpectedMarkout. Sparse books shrink toward a conservative prior."""
    if not horizon_stats:
        return CONSERVATIVE_MARKOUT_FALLBACK_BPS
    weighted = 0.0
    weight_sum = 0.0
    for horizon, weight in MARKOUT_WEIGHTS_MS.items():
        row = horizon_stats.get(int(horizon)) or {}
        n = int(row.get("n", 0) or 0)
        total = row.get("sum")
        if n <= 0 or total is None:
            continue
        mean = float(total) / float(n)
        shrunk = conservative_markout_bps(
            mean_bps=mean,
            samples=n,
            min_samples=min_samples,
            fallback_bps=CONSERVATIVE_MARKOUT_FALLBACK_BPS,
            prior_strength=prior_strength,
        )
        weighted += float(weight) * shrunk
        weight_sum += float(weight)
    if weight_sum <= 1e-12:
        return CONSERVATIVE_MARKOUT_FALLBACK_BPS
    return weighted / weight_sum


def composite_adverse_selection_risk(
    *,
    expected_markout_bps: float,
    ofi_against: float = 0.0,
    markout_weight: float = 0.05,
    ofi_weight: float = 0.04,
) -> float:
    markout_risk = adverse_selection_risk(
        expected_markout_bps, weight=markout_weight,
    )
    ofi_risk = max(0.0, float(ofi_weight)) * math.tanh(max(0.0, float(ofi_against)))
    return max(0.0, min(1.0, markout_risk + ofi_risk))


def entry_adverse_blocked(
    *,
    expected_markout_bps: float,
    adverse_selection_risk: float,
    threshold: float = 0.70,
    toxic_markout_bps: float = -8.0,
) -> bool:
    if float(adverse_selection_risk) + 1e-12 >= float(threshold):
        return True
    return (
        float(expected_markout_bps) <= float(toxic_markout_bps)
        and float(adverse_selection_risk) + 1e-12 >= 0.45
    )


def quote_width_multiplier(
    *,
    adverse_selection_risk: float,
    ofi_normalized: float | None,
) -> float:
    ofi_abs = 0.0 if ofi_normalized is None else abs(float(ofi_normalized))
    return max(1.0, 1.0 + 0.60 * max(0.0, float(adverse_selection_risk)) + 0.25 * min(1.0, ofi_abs))
