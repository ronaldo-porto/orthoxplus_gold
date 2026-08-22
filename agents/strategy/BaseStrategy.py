from __future__ import annotations

"""BaseStrategy — optimized standalone SN79 V4.1.1 Strict base class.

This module contains the complete runtime strategy implementation directly.
It is intended to be the stable parent for future Strategy agents while
preserving the validated V4.1 Strict trading behavior and optional research
telemetry.
"""

import atexit
import json
import math
import os
import queue
import threading
import time
from collections import Counter, OrderedDict, defaultdict, deque
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Deque, Iterable, Literal, Mapping, TypeVar

import bittensor as bt
from taos.common.agents import launch
from taos.im.agents import FinanceSimulationAgent
from taos.im.protocol import FinanceAgentResponse, MarketSimulationStateUpdate
from taos.im.protocol.events import SimulationStartEvent, TradeEvent
from taos.im.protocol.instructions import (
    CancelOrdersInstruction,
    ClosePositionsInstruction,
    PlaceLimitOrderInstruction,
    PlaceMarketOrderInstruction,
)
from taos.im.protocol.models import (
    Account,
    Book,
    LoanSettlementOption,
    OrderCurrency,
    OrderDirection,
    STP,
    TimeInForce,
)
from taos.im.utils import duration_from_timestamp
from taos.im.utils.kappa import kappa_3

T = TypeVar("T")


# ---------------------------------------------------------------------------
# Inlined production helpers. BaseStrategy is loaded by miner.py via
# importlib.util.spec_from_file_location + exec_module, which does not put
# agents/strategy on sys.path. These definitions must live in this file.
# Behavior and thresholds match the former standalone helper modules.
# Dust escape is intentionally not included.
# ---------------------------------------------------------------------------

MARKET_REGIMES = (
    "QUIET",
    "NORMAL",
    "LIQUID",
    "TREND_UP",
    "TREND_DOWN",
    "STRESSED",
    "TOXIC",
)
SCORE_REGIMES = (
    "NORMAL",
    "COVERAGE_PRESSURE",
    "COMPLETION_PRESSURE",
)

# Map Research V2 market labels onto the inherited Strategy1 MarketRegime.mode
# vocabulary used by get_regime_params / merge_regime_and_archetype_params.
PARENT_MARKET_MODE = {
    "QUIET": "QUIET",
    "NORMAL": "MIXED",
    "LIQUID": "BROAD_LIQUID",
    "TREND_UP": "TRENDING_UP",
    "TREND_DOWN": "TRENDING_DOWN",
    "STRESSED": "STRESSED",
    "TOXIC": "STRESSED",
}


@dataclass(frozen=True)
class RegimeV2Thresholds:
    stressed_ratio_enter: float = 0.35
    stressed_ratio_exit: float = 0.25
    toxic_ratio_enter: float = 0.50
    toxic_ratio_exit: float = 0.38
    quiet_trade_rate: float = 0.10
    liquid_ratio_enter: float = 0.55
    liquid_ratio_exit: float = 0.45
    trend_frac_enter: float = 0.45
    trend_frac_exit: float = 0.35
    debounce_ticks: int = 3
    coverage_inactive_ratio: float = 0.375
    completion_pending_ratio: float = 0.20
    completion_pending_exit: float = 0.12


@dataclass(frozen=True)
class DebounceState:
    current: str
    pending: str
    hold: int = 0


@dataclass(frozen=True)
class RegimeV2Decision:
    market_regime: str
    score_regime: str
    market_trigger: str
    market_threshold: str
    score_trigger: str
    score_threshold: str
    parent_mode: str
    scoring_overlay: str | None
    market_debounce: DebounceState
    score_debounce: DebounceState


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    xs = sorted(float(v) for v in values)
    if len(xs) == 1:
        return xs[0]
    pos = (len(xs) - 1) * min(1.0, max(0.0, q))
    lo = int(pos)
    hi = min(lo + 1, len(xs) - 1)
    frac = pos - lo
    return xs[lo] * (1.0 - frac) + xs[hi] * frac


def apply_debounce(state: DebounceState, candidate: str, debounce_ticks: int) -> DebounceState:
    debounce_ticks = max(1, int(debounce_ticks))
    if candidate == state.current:
        return DebounceState(current=state.current, pending=state.current, hold=0)
    if candidate == state.pending:
        hold = state.hold + 1
    else:
        hold = 1
    if hold >= debounce_ticks:
        return DebounceState(current=candidate, pending=candidate, hold=0)
    return DebounceState(current=state.current, pending=candidate, hold=hold)


def _ratio_high(value: float, current: str, label: str, enter: float, exit_: float) -> bool:
    threshold = exit_ if current == label else enter
    return value + 1e-12 >= threshold


def propose_market_regime(
    stats: Mapping[str, Any],
    current: str,
    thresholds: RegimeV2Thresholds,
) -> tuple[str, str, str]:
    """Return (regime, trigger, threshold) from cross-section stats only.

    `parent_mode` / `parent_trigger` on stats are ignored. Missing parent
    fields must never become STRESSED.
    """
    n = int(stats.get("book_count", 0) or 0)
    if n <= 0:
        return "NORMAL", "EMPTY_CROSS_SECTION", "book_count=0"

    stressed_ratio = float(stats.get("stressed_ratio", 0.0) or 0.0)
    liquid_ratio = float(stats.get("liquid_ratio", 0.0) or 0.0)
    trend_up = float(stats.get("trend_up_ratio", 0.0) or 0.0)
    trend_down = float(stats.get("trend_down_ratio", 0.0) or 0.0)
    trade_med = stats.get("trade_rate_med")
    spread_med = stats.get("spread_med")
    vol_med = stats.get("vol_med")
    stress_cut = float(stats.get("stress_spread_bps", 0.0) or 0.0)
    toxic_cut = float(stats.get("toxic_spread_bps", 0.0) or 0.0)

    toxic_ratio_hit = _ratio_high(
        stressed_ratio, current, "TOXIC",
        thresholds.toxic_ratio_enter, thresholds.toxic_ratio_exit,
    )
    toxic_spread_hit = (
        spread_med is not None
        and toxic_cut > 0.0
        and float(spread_med) + 1e-12 >= toxic_cut
    )
    high_vol = vol_med is not None and float(vol_med) >= 0.006
    if toxic_ratio_hit and (toxic_spread_hit or high_vol):
        return (
            "TOXIC",
            "TOXIC_STRESSED_RATIO",
            f"stressed_ratio>={thresholds.toxic_ratio_enter:g}",
        )

    stressed_ratio_hit = _ratio_high(
        stressed_ratio, current, "STRESSED",
        thresholds.stressed_ratio_enter, thresholds.stressed_ratio_exit,
    )
    median_stress_hit = (
        spread_med is not None
        and stress_cut > 0.0
        and float(spread_med) + 1e-12 >= stress_cut
    )
    if current == "STRESSED":
        # Exit only when both ratio and median are clearly below the cut.
        stay = stressed_ratio + 1e-12 >= thresholds.stressed_ratio_exit or (
            spread_med is not None
            and stress_cut > 0.0
            and float(spread_med) + 1e-12 >= stress_cut * 0.90
        )
        if stay:
            return (
                "STRESSED",
                "STRESSED_HYSTERESIS",
                f"stressed_ratio>={thresholds.stressed_ratio_exit:g}",
            )
    elif stressed_ratio_hit or median_stress_hit:
        trigger = "STRESSED_RATIO" if stressed_ratio_hit else "MEDIAN_SPREAD"
        threshold = (
            f"stressed_ratio>={thresholds.stressed_ratio_enter:g}"
            if stressed_ratio_hit
            else f"spread_med>={stress_cut:g}"
        )
        return "STRESSED", trigger, threshold

    quiet_hit = (
        trade_med is not None
        and float(trade_med) + 1e-12 < thresholds.quiet_trade_rate
        and stressed_ratio < thresholds.stressed_ratio_exit
    )
    if quiet_hit:
        return (
            "QUIET",
            "LOW_TRADE_RATE",
            f"trade_rate_med<{thresholds.quiet_trade_rate:g}",
        )

    trend_enter = (
        thresholds.trend_frac_exit
        if current in {"TREND_UP", "TREND_DOWN"}
        else thresholds.trend_frac_enter
    )
    if trend_up >= trend_enter and trend_up > trend_down + 1e-12:
        return "TREND_UP", "TREND_UP_RATIO", f"trend_up_ratio>={trend_enter:g}"
    if trend_down >= trend_enter and trend_down > trend_up + 1e-12:
        return "TREND_DOWN", "TREND_DOWN_RATIO", f"trend_down_ratio>={trend_enter:g}"

    liquid_hit = _ratio_high(
        liquid_ratio, current, "LIQUID",
        thresholds.liquid_ratio_enter, thresholds.liquid_ratio_exit,
    )
    if liquid_hit and stressed_ratio < thresholds.stressed_ratio_exit:
        return (
            "LIQUID",
            "LIQUID_RATIO",
            f"liquid_ratio>={thresholds.liquid_ratio_enter:g}",
        )

    return "NORMAL", "DEFAULT_NORMAL", "cross_section_unexceptional"


def propose_score_regime(
    stats: Mapping[str, Any],
    current: str,
    thresholds: RegimeV2Thresholds,
) -> tuple[str, str, str]:
    """Kappa / inactive coverage only. Independent of MarketRegime."""
    n = int(stats.get("book_count", 0) or 0)
    if n <= 0:
        return "NORMAL", "EMPTY_SCORE_UNIVERSE", "book_count=0"

    if stats.get("inactive") is not None:
        inactive = int(stats.get("inactive") or 0)
    else:
        inactive = int(round(float(stats.get("inactive_frac", 0.0) or 0.0) * n))
    pending_frac = float(stats.get("pending_kappa_frac", 0.0) or 0.0)

    # Match DetailedTemplateAgent: inactive_count >= max(int(ratio*n)-1, 1).
    max_inactive = int(float(thresholds.coverage_inactive_ratio) * n)
    enter_count = max(max_inactive - 1, 1)
    exit_count = max(enter_count - 1, 1)
    coverage_hit = inactive >= (exit_count if current == "COVERAGE_PRESSURE" else enter_count)
    if coverage_hit:
        return (
            "COVERAGE_PRESSURE",
            "INACTIVE_COVERAGE",
            f"inactive>={enter_count}",
        )

    completion_hit = _ratio_high(
        pending_frac,
        current,
        "COMPLETION_PRESSURE",
        thresholds.completion_pending_ratio,
        thresholds.completion_pending_exit,
    )
    if completion_hit:
        return (
            "COMPLETION_PRESSURE",
            "KAPPA_PENDING",
            f"pending_kappa_frac>={thresholds.completion_pending_ratio:g}",
        )
    return "NORMAL", "SCORE_NORMAL", "coverage_and_completion_clear"


def classify_regime_v2(
    stats: Mapping[str, Any],
    *,
    market_state: DebounceState | None = None,
    score_state: DebounceState | None = None,
    thresholds: RegimeV2Thresholds | None = None,
) -> RegimeV2Decision:
    thr = thresholds or RegimeV2Thresholds()
    market_state = market_state or DebounceState("NORMAL", "NORMAL", 0)
    score_state = score_state or DebounceState("NORMAL", "NORMAL", 0)

    market_raw, market_trigger, market_threshold = propose_market_regime(
        stats, market_state.current, thr,
    )
    score_raw, score_trigger, score_threshold = propose_score_regime(
        stats, score_state.current, thr,
    )
    market_next = apply_debounce(market_state, market_raw, thr.debounce_ticks)
    score_next = apply_debounce(score_state, score_raw, thr.debounce_ticks)

    overlay = (
        "SCORING_PRESSURE" if score_next.current == "COVERAGE_PRESSURE" else None
    )
    return RegimeV2Decision(
        market_regime=market_next.current,
        score_regime=score_next.current,
        market_trigger=market_trigger,
        market_threshold=market_threshold,
        score_trigger=score_trigger,
        score_threshold=score_threshold,
        parent_mode=PARENT_MARKET_MODE[market_next.current],
        scoring_overlay=overlay,
        market_debounce=market_next,
        score_debounce=score_next,
    )


FILL_CLASSES = (
    "FULL",
    "ACTIONABLE_PARTIAL",
    "DUST_PARTIAL",
    "FLAT",
    "CROSS_DUST",
)
MARKOUT_HORIZONS_MS = (100, 250, 500, 1000)
NS_PER_MS = 1_000_000


def sim_delta_ms(start: float | int | None, end: float | int | None) -> float | None:
    """Simulator timestamps are nanoseconds."""
    if start is None or end is None:
        return None
    return (float(end) - float(start)) / NS_PER_MS


def ms_to_ns(ms: float | int) -> int:
    return int(float(ms) * NS_PER_MS)


def is_flat(qty: float, eps: float) -> bool:
    return abs(float(qty)) < max(float(eps), 1e-12)


def is_dust(qty: float, min_order_size: float, eps: float) -> bool:
    """Sub-minimum but not execution-flat. Uses the runtime min, never a hardcoded 0.25."""
    abs_qty = abs(float(qty))
    min_size = max(0.0, float(min_order_size))
    if min_size <= 0.0:
        return False
    return abs_qty >= max(float(eps), 1e-12) and abs_qty + 1e-12 < min_size


def is_actionable(qty: float, min_order_size: float, eps: float) -> bool:
    min_size = max(0.0, float(min_order_size))
    if min_size <= 0.0:
        return not is_flat(qty, eps)
    return abs(float(qty)) + max(float(eps), 1e-12) >= min_size


def remaining_quantity(
    requested: float | None,
    filled_cum: float | None,
    eps: float,
) -> float | None:
    if requested is None:
        return None
    filled = 0.0 if filled_cum is None else max(0.0, float(filled_cum))
    left = max(0.0, float(requested) - filled)
    return 0.0 if left <= max(float(eps), 1e-12) else left


def classify_fill(
    *,
    inventory_before: float,
    inventory_after: float,
    fill_quantity: float,
    requested_quantity: float | None,
    filled_quantity: float | None,
    min_order_size: float,
    flat_eps: float,
) -> str:
    """One label per fill from inventory + fill + runtime min size.

    Priority:
      CROSS_DUST — sign flip that leaves dust on the far side
      FLAT — post-fill inventory is execution-flat
      DUST_PARTIAL — post-fill inventory is sub-minimum
      FULL — resting quote remaining is ~0 and leftover is tradable
      ACTIONABLE_PARTIAL — leftover is tradable and the quote still has size
    """
    del fill_quantity  # used by callers for remaining via filled_quantity
    eps = max(float(flat_eps), 1e-12)
    before = float(inventory_before)
    after = float(inventory_after)
    min_size = max(0.0, float(min_order_size))

    crossed = (
        (not is_flat(before, eps))
        and (not is_flat(after, eps))
        and (before * after < 0.0)
    )
    if crossed and is_dust(after, min_size, eps):
        return "CROSS_DUST"
    if is_flat(after, eps):
        return "FLAT"
    if is_dust(after, min_size, eps):
        return "DUST_PARTIAL"

    remaining = remaining_quantity(requested_quantity, filled_quantity, eps)
    quote_complete = remaining is not None and remaining <= eps
    if quote_complete:
        return "FULL"
    if is_actionable(after, min_size, eps):
        return "ACTIONABLE_PARTIAL"
    return "DUST_PARTIAL"


def side_markout_bps(
    side: str,
    fill_price: float,
    future_mid: float | None,
) -> float | None:
    """Maker side-adjusted markout in bps of fill price.

    BUY:  (future_mid - fill_price) / fill_price * 1e4
    SELL: (fill_price - future_mid) / fill_price * 1e4
    Positive is favorable for the maker.
    """
    if future_mid is None:
        return None
    px = float(fill_price)
    if px <= 0.0:
        return None
    mid = float(future_mid)
    raw = (mid - px) / px * 10_000.0
    token = str(side).lower()
    if token in {"sell", "ask", "s", "1"}:
        return -raw
    return raw


def actual_microprice(
    bid: float | None,
    ask: float | None,
    bid_qty: float | None,
    ask_qty: float | None,
) -> float | None:
    if bid is None or ask is None:
        return None
    bq = 0.0 if bid_qty is None else float(bid_qty)
    aq = 0.0 if ask_qty is None else float(ask_qty)
    denom = bq + aq
    if denom <= 0.0:
        return None
    return (float(ask) * bq + float(bid) * aq) / denom


def touch_distance(
    side: str,
    quote_price: float,
    best_bid: float | None,
    best_ask: float | None,
    mid: float | None,
    tick_size: float | None,
) -> tuple[float | None, float | None]:
    token = str(side).lower()
    if token in {"buy", "bid", "b", "0"}:
        touch = best_bid
        delta = None if touch is None else float(touch) - float(quote_price)
    else:
        touch = best_ask
        delta = None if touch is None else float(quote_price) - float(touch)
    ticks = None
    bps = None
    if delta is not None and tick_size is not None and float(tick_size) > 0.0:
        ticks = delta / float(tick_size)
    if delta is not None and mid is not None and float(mid) > 0.0:
        bps = delta / float(mid) * 10_000.0
    return ticks, bps


def optional_queue_metrics(
    *,
    level_quantity: float | None,
    orders: Iterable[Mapping[str, Any]] | None,
) -> dict[str, float]:
    """Return queue fields only from genuine book data. Never infer missing orders."""
    out: dict[str, float] = {}
    if level_quantity is not None:
        out["queue_depth_at_price"] = float(level_quantity)
    if not orders:
        return out
    ahead = 0.0
    saw = False
    for order in orders:
        try:
            ahead += float(order.get("quantity", 0.0) or 0.0)
            saw = True
        except (TypeError, ValueError, AttributeError):
            continue
    if saw:
        out["queue_ahead"] = ahead
    return out


@dataclass
class PendingMarkout:
    quote_id: int
    book: int
    side: str
    fill_price: float
    fill_ts: int
    horizon_ms: int


@dataclass
class MarkoutResult:
    quote_id: int
    book: int
    side: str
    horizon_ms: int
    fill_price: float
    future_mid: float | None
    markout_bps: float | None
    status: str = "OK"


@dataclass
class QuoteRecord:
    quote_id: int
    client_id: int | None
    book: int
    side: str
    decision_ts: int | None = None
    submit_ts: int | None = None
    cancel_ts: int | None = None
    fill_ts: int | None = None
    requested_quantity: float | None = None
    filled_quantity: float = 0.0
    remaining_quantity: float | None = None
    quote_price: float | None = None
    configured_ttl_ms: float | None = None
    predicted_fill_probability: float | None = None
    predicted_any_fill_probability: float | None = None
    predicted_actionable_fill_probability: float | None = None
    predicted_dust_probability: float | None = None
    hazard_source: str | None = None
    hazard_features: dict[str, Any] | None = None
    hazard_closed: bool = False
    market_regime: str | None = None
    score_regime: str | None = None
    book_archetype: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)
    open: bool = True


class QuoteLifecycleStore:
    """Bounded live-quote + pending-markout store. No full-history scan."""

    def __init__(
        self,
        *,
        horizons_ms: tuple[int, ...] = MARKOUT_HORIZONS_MS,
        max_live: int = 1024,
        max_pending_markouts: int = 2048,
        missing_after_ms: int = 2500,
    ) -> None:
        self.horizons_ms = tuple(int(h) for h in horizons_ms)
        self.max_live = max(8, int(max_live))
        self.max_pending_markouts = max(16, int(max_pending_markouts))
        self.missing_after_ns = ms_to_ns(missing_after_ms)
        self._seq = 0
        self.live: OrderedDict[tuple[int, int], QuoteRecord] = OrderedDict()
        self.by_id: dict[int, QuoteRecord] = {}
        self.pending: deque[PendingMarkout] = deque()
        self.last_replaced: QuoteRecord | None = None

    def next_quote_id(self) -> int:
        self._seq += 1
        return self._seq

    def _client_key(self, book: int, client_id: int | None) -> tuple[int, int] | None:
        if client_id is None:
            return None
        return (int(book), int(client_id))

    def _evict_live(self) -> None:
        while len(self.live) > self.max_live:
            key, rec = self.live.popitem(last=False)
            rec.open = False
            self.by_id.pop(rec.quote_id, None)

    def register_quote(self, record: QuoteRecord) -> QuoteRecord:
        self.last_replaced = None
        key = self._client_key(record.book, record.client_id)
        if key is not None and key in self.live:
            prev = self.live.pop(key)
            prev.open = False
            prev.cancel_ts = prev.cancel_ts or record.submit_ts
            self.by_id.pop(prev.quote_id, None)
            self.last_replaced = prev
        if key is not None:
            self.live[key] = record
            self.live.move_to_end(key)
        self.by_id[record.quote_id] = record
        self._evict_live()
        return record

    def lookup(self, book: int, client_id: int | None) -> QuoteRecord | None:
        key = self._client_key(book, client_id)
        if key is None:
            return None
        return self.live.get(key)

    def live_for_book_side(self, book: int, side: str) -> QuoteRecord | None:
        want = str(side).lower()
        target = int(book)
        for rec in reversed(self.live.values()):
            if rec.open and rec.book == target and str(rec.side).lower() == want:
                return rec
        return None

    def close_quote(
        self,
        record: QuoteRecord,
        *,
        cancel_ts: int | None = None,
        fill_ts: int | None = None,
    ) -> None:
        record.open = False
        if cancel_ts is not None:
            record.cancel_ts = cancel_ts
        if fill_ts is not None:
            record.fill_ts = fill_ts
        key = self._client_key(record.book, record.client_id)
        if key is not None:
            self.live.pop(key, None)
        self.by_id.pop(record.quote_id, None)

    def apply_fill(
        self,
        record: QuoteRecord,
        *,
        fill_qty: float,
        fill_ts: int | None,
        flat_eps: float,
    ) -> float:
        record.filled_quantity = float(record.filled_quantity) + abs(float(fill_qty))
        record.fill_ts = fill_ts
        record.remaining_quantity = remaining_quantity(
            record.requested_quantity, record.filled_quantity, flat_eps,
        )
        return record.filled_quantity

    def schedule_markouts(
        self,
        *,
        quote_id: int,
        book: int,
        side: str,
        fill_price: float,
        fill_ts: int,
    ) -> None:
        for horizon in self.horizons_ms:
            self.pending.append(
                PendingMarkout(
                    quote_id=int(quote_id),
                    book=int(book),
                    side=str(side),
                    fill_price=float(fill_price),
                    fill_ts=int(fill_ts),
                    horizon_ms=int(horizon),
                )
            )
        return self._cap_pending(now_ts=int(fill_ts))

    def _cap_pending(self, now_ts: int) -> list[MarkoutResult]:
        dropped: list[MarkoutResult] = []
        while len(self.pending) > self.max_pending_markouts:
            item = self.pending.popleft()
            dropped.append(
                MarkoutResult(
                    quote_id=item.quote_id,
                    book=item.book,
                    side=item.side,
                    horizon_ms=item.horizon_ms,
                    fill_price=item.fill_price,
                    future_mid=None,
                    markout_bps=None,
                    status="MISSING_FUTURE",
                )
            )
        return dropped

    def evaluate(
        self,
        *,
        now_ts: int,
        mids: Mapping[int, float | None],
    ) -> list[MarkoutResult]:
        """Emit due markouts. O(pending), not O(all books / history)."""
        due: list[MarkoutResult] = []
        kept: deque[PendingMarkout] = deque()
        now = int(now_ts)
        for item in self.pending:
            age_ns = now - int(item.fill_ts)
            if age_ns < 0:
                kept.append(item)
                continue
            ready = age_ns >= ms_to_ns(item.horizon_ms)
            missing = age_ns >= self.missing_after_ns
            if not ready and not missing:
                kept.append(item)
                continue
            future_mid = mids.get(int(item.book))
            if ready and future_mid is not None:
                due.append(
                    MarkoutResult(
                        quote_id=item.quote_id,
                        book=item.book,
                        side=item.side,
                        horizon_ms=item.horizon_ms,
                        fill_price=item.fill_price,
                        future_mid=float(future_mid),
                        markout_bps=side_markout_bps(
                            item.side, item.fill_price, float(future_mid),
                        ),
                        status="OK",
                    )
                )
                continue
            if missing:
                due.append(
                    MarkoutResult(
                        quote_id=item.quote_id,
                        book=item.book,
                        side=item.side,
                        horizon_ms=item.horizon_ms,
                        fill_price=item.fill_price,
                        future_mid=None,
                        markout_bps=None,
                        status="MISSING_FUTURE",
                    )
                )
                continue
            kept.append(item)
        self.pending = kept
        return due


AGE_EDGES_MS = (100, 250, 500, 1000)
N_AGE_BINS = len(AGE_EDGES_MS) + 1
CAL_BUCKETS = (
    ("0.00_0.05", 0.00, 0.05),
    ("0.05_0.10", 0.05, 0.10),
    ("0.10_0.20", 0.10, 0.20),
    ("0.20_0.40", 0.20, 0.40),
    ("0.40_1.00", 0.40, 1.01),
)
DIST_EDGES_BPS = (0.5, 2.0)
SPREAD_EDGES = (5.0, 12.0)
VOL_EDGES = (0.002, 0.006)
TRADE_EDGES = (0.2, 1.0)
IMB_EDGES = (-0.15, 0.15)
TTL_EDGES_MS = (200.0, 600.0)
REGIME_GROUPS = {
    "QUIET": "QUIET",
    "NORMAL": "NORMAL",
    "LIQUID": "NORMAL",
    "TREND_UP": "TREND",
    "TREND_DOWN": "TREND",
    "STRESSED": "STRESS",
    "TOXIC": "STRESS",
    "MIXED": "NORMAL",
    "BROAD_LIQUID": "NORMAL",
    "TRENDING_UP": "TREND",
    "TRENDING_DOWN": "TREND",
    "CHOP": "QUIET",
    "DISPERSED": "NORMAL",
}


FROZEN_MIN_SAMPLES = 12
FROZEN_PRIOR_STRENGTH = 8.0
FROZEN_PRIOR_ANY = 0.12
FROZEN_PRIOR_ACTIONABLE_GIVEN_FILL = 0.55
FROZEN_P_MIN = 0.01
FROZEN_P_MAX = 0.95
FROZEN_FEATURE_LOGIT_WEIGHT = 0.0


def _clip(p: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(p)))


def _logit(p: float) -> float:
    x = _clip(p, 1e-6, 1.0 - 1e-6)
    return math.log(x / (1.0 - x))


def _sigmoid(z: float) -> float:
    if z >= 30.0:
        return 1.0
    if z <= -30.0:
        return 0.0
    return 1.0 / (1.0 + math.exp(-z))


def _bucket(value: float | None, edges: tuple[float, ...]) -> int:
    if value is None:
        return 1
    x = float(value)
    for i, edge in enumerate(edges):
        if x < edge:
            return i
    return len(edges)


def age_bin(age_ms: float) -> int:
    age = max(0.0, float(age_ms))
    for i, edge in enumerate(AGE_EDGES_MS):
        if age < float(edge):
            return i
    return len(AGE_EDGES_MS)


def bins_for_ttl(ttl_ms: float) -> range:
    """Bins whose left edge is strictly before TTL (P(T < TTL) via discrete hazard)."""
    ttl = max(0.0, float(ttl_ms))
    last = 0
    left = 0.0
    for i, edge in enumerate(AGE_EDGES_MS):
        if left < ttl:
            last = i
        left = float(edge)
    if left < ttl:
        last = len(AGE_EDGES_MS)
    return range(0, last + 1)


def cal_bucket(p: float) -> str:
    x = max(0.0, min(1.0, float(p)))
    for name, lo, hi in CAL_BUCKETS:
        if lo <= x < hi:
            return name
    return CAL_BUCKETS[-1][0]


def outcome_from_fill_class(fill_class: str | None) -> Literal["actionable", "dust", "other"]:
    token = str(fill_class or "").upper()
    if token in {"DUST_PARTIAL", "CROSS_DUST", "DUST"}:
        return "dust"
    if token in {"FULL", "ACTIONABLE_PARTIAL", "FLAT", "ACTIONABLE"}:
        return "actionable"
    return "other"


@dataclass
class HazardFeatures:
    side: str
    dist_bucket: int
    spread_bucket: int
    vol_bucket: int
    trade_bucket: int
    imb_bucket: int
    regime_group: str
    ttl_bucket: int
    ttl_ms: float

    @classmethod
    def from_snapshot(
        cls,
        *,
        side: str,
        distance_from_touch_bps: float | None,
        spread_bps: float | None,
        volatility: float | None,
        trade_rate: float | None,
        imbalance: float | None,
        market_regime: str | None,
        ttl_ms: float | None,
    ) -> "HazardFeatures":
        ttl = 500.0 if ttl_ms is None else max(1.0, float(ttl_ms))
        regime = REGIME_GROUPS.get(str(market_regime or "NORMAL").upper(), "NORMAL")
        return cls(
            side="buy" if str(side).lower() in {"buy", "bid", "b", "0"} else "sell",
            dist_bucket=_bucket(distance_from_touch_bps, DIST_EDGES_BPS),
            spread_bucket=_bucket(spread_bps, SPREAD_EDGES),
            vol_bucket=_bucket(volatility, VOL_EDGES),
            trade_bucket=_bucket(trade_rate, TRADE_EDGES),
            imb_bucket=_bucket(imbalance, IMB_EDGES),
            regime_group=regime,
            ttl_bucket=_bucket(ttl, TTL_EDGES_MS),
            ttl_ms=ttl,
        )


@dataclass
class _Counts:
    at_risk: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)
    fills: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)
    censored: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)
    actionable: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)
    dust: list[int] = field(default_factory=lambda: [0] * N_AGE_BINS)

    def observe(self, bin_idx: int, filled: bool, outcome: str | None) -> None:
        idx = max(0, min(N_AGE_BINS - 1, int(bin_idx)))
        for k in range(0, idx):
            self.at_risk[k] += 1
        self.at_risk[idx] += 1
        if filled:
            self.fills[idx] += 1
            if outcome == "actionable":
                self.actionable[idx] += 1
            elif outcome == "dust":
                self.dust[idx] += 1
        else:
            self.censored[idx] += 1

    def n0(self) -> int:
        return int(self.at_risk[0])


@dataclass
class HazardPrediction:
    any_fill: float
    actionable_fill: float
    dust: float
    source: str
    usable: bool
    n_at_risk: int
    ttl_ms: float


@dataclass
class CalBucket:
    predicted_sum: float = 0.0
    observed_sum: float = 0.0
    brier_sum: float = 0.0
    sample_count: int = 0

    def add(self, predicted: float, observed: float) -> None:
        p = _clip(predicted, 0.0, 1.0)
        y = 1.0 if float(observed) >= 0.5 else 0.0
        self.predicted_sum += p
        self.observed_sum += y
        self.brier_sum += (p - y) ** 2
        self.sample_count += 1

    def snapshot(self) -> dict[str, float | int]:
        n = max(1, self.sample_count)
        return {
            "predicted_mean": self.predicted_sum / n if self.sample_count else 0.0,
            "observed_rate": self.observed_sum / n if self.sample_count else 0.0,
            "sample_count": self.sample_count,
            "brier_component": self.brier_sum / n if self.sample_count else 0.0,
        }


class FillHazardModel:
    """Bounded empirical hazard: primary (side, dist) plus shrunk global/side priors."""

    def __init__(
        self,
        *,
        min_samples: int = FROZEN_MIN_SAMPLES,
        prior_strength: float = FROZEN_PRIOR_STRENGTH,
        prior_any: float = FROZEN_PRIOR_ANY,
        prior_actionable_given_fill: float = FROZEN_PRIOR_ACTIONABLE_GIVEN_FILL,
        p_min: float = FROZEN_P_MIN,
        p_max: float = FROZEN_P_MAX,
        feature_logit_weight: float = FROZEN_FEATURE_LOGIT_WEIGHT,
    ) -> None:
        self.min_samples = max(1, int(min_samples))
        self.prior_strength = max(0.0, float(prior_strength))
        self.prior_any = _clip(prior_any, 0.01, 0.5)
        self.prior_actionable_given_fill = _clip(prior_actionable_given_fill, 0.05, 0.95)
        self.p_min = max(0.0, float(p_min))
        self.p_max = min(1.0, float(p_max))
        self.feature_logit_weight = max(0.0, min(1.0, float(feature_logit_weight)))
        self.global_counts = _Counts()
        self.side_counts: dict[str, _Counts] = {"buy": _Counts(), "sell": _Counts()}
        self.cells: dict[tuple[str, int], _Counts] = {}
        self.feature_counts: dict[tuple[str, str | int], _Counts] = {}
        self.calibration: dict[tuple[str, str, str], CalBucket] = {}
        self.brier_any_sum = 0.0
        self.brier_any_n = 0
        self.brier_act_sum = 0.0
        self.brier_act_n = 0
        self.brier_dust_sum = 0.0
        self.brier_dust_n = 0
        self.observations = 0
        self.events = 0
        self.censored = 0

    def _cell(self, side: str, dist_bucket: int) -> _Counts:
        key = (side, int(dist_bucket))
        return self.cells.setdefault(key, _Counts())

    def _feat(self, name: str, bucket: str | int) -> _Counts:
        return self.feature_counts.setdefault((name, bucket), _Counts())

    def observe(
        self,
        features: HazardFeatures,
        *,
        age_ms: float,
        filled: bool,
        fill_class: str | None = None,
        predicted: HazardPrediction | None = None,
        include_in_calibration: bool = True,
    ) -> None:
        idx = age_bin(age_ms)
        outcome = outcome_from_fill_class(fill_class) if filled else None
        self.global_counts.observe(idx, filled, outcome)
        self.side_counts[features.side].observe(idx, filled, outcome)
        self._cell(features.side, features.dist_bucket).observe(idx, filled, outcome)
        self._feat("spread", features.spread_bucket).observe(idx, filled, outcome)
        self._feat("vol", features.vol_bucket).observe(idx, filled, outcome)
        self._feat("trade", features.trade_bucket).observe(idx, filled, outcome)
        self._feat("imb", features.imb_bucket).observe(idx, filled, outcome)
        self._feat("regime", features.regime_group).observe(idx, filled, outcome)
        self._feat("ttl", features.ttl_bucket).observe(idx, filled, outcome)
        self.observations += 1
        if filled:
            self.events += 1
        else:
            self.censored += 1
        if include_in_calibration and predicted is not None:
            self._calibrate(features, filled, outcome, predicted, age_ms)

    def _calibrate(
        self,
        features: HazardFeatures,
        filled: bool,
        outcome: str | None,
        predicted: HazardPrediction,
        age_ms: float,
    ) -> None:
        ttl = max(1.0, float(features.ttl_ms))
        if (not filled) and age_ms + 1e-9 < ttl:
            return
        y_any = 1.0 if filled and age_ms <= ttl + 1e-9 else 0.0
        y_act = 1.0 if y_any >= 0.5 and outcome == "actionable" else 0.0
        y_dust = 1.0 if y_any >= 0.5 and outcome == "dust" else 0.0
        self._add_cal("ANY", features.side, predicted.any_fill, y_any)
        self._add_cal("ACTIONABLE", features.side, predicted.actionable_fill, y_act)
        self._add_cal("DUST", features.side, predicted.dust, y_dust)
        self.brier_any_sum += (predicted.any_fill - y_any) ** 2
        self.brier_any_n += 1
        self.brier_act_sum += (predicted.actionable_fill - y_act) ** 2
        self.brier_act_n += 1
        self.brier_dust_sum += (predicted.dust - y_dust) ** 2
        self.brier_dust_n += 1

    def _add_cal(self, kind: str, side: str, predicted: float, observed: float) -> None:
        key = (kind, side.upper(), cal_bucket(predicted))
        bucket = self.calibration.setdefault(key, CalBucket())
        bucket.add(predicted, observed)

    def _hazard_path(self, counts: _Counts, ttl_ms: float) -> tuple[float, float, float, int]:
        alpha = self.prior_strength
        n_bins = max(1, len(tuple(bins_for_ttl(ttl_ms))))
        h0 = 1.0 - (1.0 - self.prior_any) ** (1.0 / n_bins)
        surv = 1.0
        act_cif = 0.0
        dust_cif = 0.0
        for k in bins_for_ttl(ttl_ms):
            n = counts.at_risk[k]
            d = counts.fills[k]
            h = (d + alpha * h0) / (n + alpha) if (n + alpha) > 0 else h0
            h = _clip(h, 0.0, 0.999)
            fills = max(d, 0)
            p_act_g = (
                (counts.actionable[k] + alpha * self.prior_actionable_given_fill)
                / (fills + alpha)
                if (fills + alpha) > 0
                else self.prior_actionable_given_fill
            )
            p_dust_g = (
                (counts.dust[k] + alpha * (1.0 - self.prior_actionable_given_fill))
                / (fills + alpha)
                if (fills + alpha) > 0
                else (1.0 - self.prior_actionable_given_fill)
            )
            act_cif += surv * h * p_act_g
            dust_cif += surv * h * p_dust_g
            surv *= (1.0 - h)
        p_any = 1.0 - surv
        return p_any, act_cif, dust_cif, counts.n0()

    def predict(self, features: HazardFeatures) -> HazardPrediction:
        ttl = features.ttl_ms
        p_g, a_g, d_g, n_g = self._hazard_path(self.global_counts, ttl)
        p_s, a_s, d_s, n_s = self._hazard_path(self.side_counts[features.side], ttl)
        p_c, a_c, d_c, n_c = self._hazard_path(
            self._cell(features.side, features.dist_bucket), ttl,
        )
        k = self.prior_strength
        p0 = self.prior_any
        a0 = p0 * self.prior_actionable_given_fill
        d0 = p0 * (1.0 - self.prior_actionable_given_fill)
        denom = n_c + n_s + n_g + k
        p_any = (n_c * p_c + n_s * p_s + n_g * p_g + k * p0) / denom
        p_act = (n_c * a_c + n_s * a_s + n_g * a_g + k * a0) / denom
        p_dust = (n_c * d_c + n_s * d_s + n_g * d_g + k * d0) / denom
        if n_g >= self.min_samples and self.feature_logit_weight > 0.0:
            adj = 0.0
            extras = (
                ("spread", features.spread_bucket),
                ("vol", features.vol_bucket),
                ("trade", features.trade_bucket),
                ("imb", features.imb_bucket),
                ("regime", features.regime_group),
                ("ttl", features.ttl_bucket),
            )
            used = 0
            for name, bucket in extras:
                counts = self.feature_counts.get((name, bucket))
                if counts is None or counts.n0() < max(4, self.min_samples // 2):
                    continue
                p_f, _, _, _ = self._hazard_path(counts, ttl)
                adj += _logit(p_f) - _logit(max(p_g, 1e-6))
                used += 1
            if used:
                p_any = _sigmoid(_logit(p_any) + self.feature_logit_weight * adj / used)

        usable = n_g >= self.min_samples or n_c >= max(4, self.min_samples // 2)
        if n_c >= self.min_samples:
            source = "cell"
        elif n_s >= self.min_samples:
            source = "side"
        elif n_g >= self.min_samples:
            source = "global"
        else:
            source = "fallback"
            usable = False

        p_any = _clip(p_any, self.p_min, self.p_max)
        p_act = _clip(p_act, 0.0, self.p_max)
        p_dust = _clip(p_dust, 0.0, self.p_max)
        cap = max(p_any, 1e-9)
        if p_act + p_dust > cap:
            scale = cap / (p_act + p_dust)
            p_act *= scale
            p_dust *= scale
        return HazardPrediction(
            any_fill=p_any,
            actionable_fill=p_act,
            dust=p_dust,
            source=source,
            usable=usable,
            n_at_risk=n_c if n_c > 0 else n_s if n_s > 0 else n_g,
            ttl_ms=ttl,
        )

    def select_policy_probability(
        self,
        old_prob: float,
        predicted: HazardPrediction,
        *,
        use_for_policy: bool,
    ) -> float:
        prob, _, _ = self.apply_policy_fill(old_prob, predicted, use_for_policy=use_for_policy)
        return prob

    def model_confidence(self, predicted: HazardPrediction) -> float:
        if predicted.source == "global":
            n = self.global_counts.n0()
        else:
            n = max(0, int(predicted.n_at_risk))
        return _clip(float(n) / max(1, self.min_samples), 0.0, 1.0)

    def calibration_fallback_reason(self) -> str:
        min_cal = max(self.min_samples * 2, 24)
        if self.brier_any_n < min_cal:
            return ""
        pred_sum = 0.0
        obs_sum = 0.0
        n = 0
        for bucket in self.calibration.values():
            if bucket.sample_count <= 0:
                continue
            pred_sum += bucket.predicted_sum
            obs_sum += bucket.observed_sum
            n += int(bucket.sample_count)
        if n < min_cal:
            return ""
        if abs(pred_sum / n - obs_sum / n) > 0.40:
            return "LOW_CONFIDENCE"
        return ""

    def apply_policy_fill(
        self,
        old_prob: float,
        predicted: HazardPrediction | None,
        *,
        use_for_policy: bool,
    ) -> tuple[float, str, float]:
        """Return (probability, fallback_reason, confidence).

        fallback_reason is '' when the frozen hazard is used for policy.
        """
        legacy = _clip(old_prob, 0.0, 1.0)
        if not use_for_policy:
            return legacy, "POLICY_DISABLED", 0.0
        if predicted is None:
            return legacy, "UNSUPPORTED_FEATURES", 0.0
        conf = self.model_confidence(predicted)
        any_fill = predicted.any_fill
        if any_fill is None or any_fill != any_fill:
            return legacy, "INVALID_OUTPUT", conf
        try:
            any_fill = float(any_fill)
        except (TypeError, ValueError):
            return legacy, "INVALID_OUTPUT", conf
        if any_fill < 0.0 or any_fill > 1.0:
            return legacy, "INVALID_OUTPUT", conf
        if predicted.source == "fallback" or not predicted.usable:
            return legacy, "INSUFFICIENT_SAMPLES", conf
        if conf + 1e-12 < 0.5:
            return legacy, "LOW_CONFIDENCE", conf
        cal_reason = self.calibration_fallback_reason()
        if cal_reason:
            return legacy, cal_reason, conf
        return _clip(any_fill, 0.0, 1.0), "", conf

    def brier_overall(self) -> dict[str, float | int]:
        return {
            "ANY": (self.brier_any_sum / self.brier_any_n) if self.brier_any_n else 0.0,
            "ACTIONABLE": (self.brier_act_sum / self.brier_act_n) if self.brier_act_n else 0.0,
            "DUST": (self.brier_dust_sum / self.brier_dust_n) if self.brier_dust_n else 0.0,
            "n": self.brier_any_n,
        }

    def calibration_rows(self, kind: str, side: str) -> list[dict[str, Any]]:
        rows = []
        overall = self.brier_overall()
        for name, _, _ in CAL_BUCKETS:
            key = (kind, side.upper(), name)
            snap = self.calibration.get(key, CalBucket()).snapshot()
            rows.append(
                {
                    "kind": kind,
                    "side": side.upper(),
                    "bucket": name,
                    **snap,
                    "brier_overall": overall.get(kind, 0.0),
                }
            )
        return rows


PROTOCOL_DEFAULT_MIN_REALIZED_OBSERVATIONS = 3


def required_observation_count(
    *,
    kappa_min_observations: int | None = None,
    research_target: int | None = None,
) -> int:
    """Runtime Kappa qualification threshold.

    Prefer an explicit Research scheduler target, else the miner-configured
    ``kappa_min_observations`` (mirrors validator
    ``scoring.kappa.min_realized_observations``). The protocol default of 3 is
    used only when nothing is configured.
    """
    for value in (research_target, kappa_min_observations):
        if value is None:
            continue
        try:
            n = int(value)
        except (TypeError, ValueError):
            continue
        if n >= 1:
            return n
    return PROTOCOL_DEFAULT_MIN_REALIZED_OBSERVATIONS


def observation_progress(
    realized_observation_count: int,
    required: int,
) -> tuple[int, int, int]:
    req = max(1, int(required))
    realized = max(0, int(realized_observation_count))
    remaining = max(0, req - realized)
    return realized, req, remaining


def completion_value(
    *,
    observations_remaining: int,
    required_observation_count: int,
    one_away_weight: float = 0.18,
    two_away_weight: float = 0.06,
    new_book_weight: float = 0.0,
) -> float:
    """Scheduler bonus: 1 remaining >> 2 remaining > new book.

    Already-qualified books (remaining 0) get no completion pressure.
    """
    remaining = max(0, int(observations_remaining))
    required = max(1, int(required_observation_count))
    if remaining <= 0:
        return 0.0
    if remaining == 1:
        return max(0.0, float(one_away_weight))
    if remaining == 2:
        return max(0.0, float(two_away_weight))
    if remaining >= required:
        return max(0.0, float(new_book_weight))
    decay = max(0.0, float(two_away_weight)) * (2.0 / float(remaining))
    return decay


def conservative_actionable_probability(
    *,
    hazard_p: float | None,
    hazard_usable: bool,
    learned_p: float | None,
    learned_samples: int,
    fill_prob_old: float,
    min_samples: int = 8,
    prior: float = 0.55,
    p_min: float = 0.05,
    p_max: float = 0.90,
) -> float:
    """Do not blindly trust sparse hazard or learned actionable-fill data."""
    if hazard_usable and hazard_p is not None:
        return max(p_min, min(p_max, float(hazard_p)))
    n = max(0, int(learned_samples))
    fallback = max(p_min, min(p_max, float(fill_prob_old) * float(prior)))
    if learned_p is None or n <= 0:
        return fallback
    learned = max(p_min, min(p_max, float(learned_p)))
    if n >= max(1, int(min_samples)):
        return learned
    strength = float(max(1, int(min_samples)))
    blended = (n * learned + strength * fallback) / (n + strength)
    return max(p_min, min(p_max, blended))


def conservative_markout_bps(
    *,
    mean_bps: float | None,
    samples: int,
    min_samples: int = 8,
    fallback_bps: float = 0.0,
    prior_strength: float = 8.0,
    clip_abs: float = 20.0,
) -> float:
    """Sparse markout shrinks toward 0 rather than an optimistic mean."""
    n = max(0, int(samples))
    fb = float(fallback_bps)
    if mean_bps is None or n <= 0:
        return fb
    mean = max(-clip_abs, min(clip_abs, float(mean_bps)))
    strength = max(0.0, float(prior_strength))
    if n >= max(1, int(min_samples)):
        return mean
    blended = (n * mean + strength * fb) / (n + strength)
    return max(-clip_abs, min(clip_abs, blended))


def trading_ev(
    *,
    actionable_fill_prob: float,
    spread_capture_bps: float,
    expected_markout_bps: float,
    fees_bps: float,
    edge_scale_bps: float = 8.0,
) -> float:
    """P(actionable fill) * tanh((spread capture + markout - fees) / scale)."""
    p = max(0.0, min(1.0, float(actionable_fill_prob)))
    edge = float(spread_capture_bps) + float(expected_markout_bps) - float(fees_bps)
    scale = max(1e-6, float(edge_scale_bps))
    return p * math.tanh(edge / scale)


def dust_cost(
    dust_prob: float,
    *,
    target: float = 0.15,
    weight: float = 0.25,
) -> float:
    return max(0.0, float(weight)) * max(0.0, float(dust_prob) - float(target))


def inventory_cost(inventory_util: float, *, weight: float = 0.08) -> float:
    util = max(0.0, min(1.0, float(inventory_util)))
    return max(0.0, float(weight)) * util * util


def latency_cost(latency_ms: float | None, *, weight: float = 0.04, ref_ms: float = 50.0) -> float:
    if latency_ms is None:
        return 0.0
    frac = max(0.0, min(1.0, float(latency_ms) / max(1.0, float(ref_ms))))
    return max(0.0, float(weight)) * frac


def hard_safety_blocks(
    *,
    toxic: bool = False,
    inventory_blocked: bool = False,
    unsafe: bool = False,
    trading_ev_value: float = 0.0,
    min_trading_ev: float = 0.0,
) -> str | None:
    """Hard gates beat completion value. Returns a reject reason or None."""
    if toxic:
        return "TOXIC"
    if inventory_blocked:
        return "INVENTORY_BLOCKED"
    if unsafe:
        return "UNSAFE"
    if float(trading_ev_value) < float(min_trading_ev):
        return "NEGATIVE_EV"
    return None


def legacy_global_rank(expected_alpha: float, specialization: float = 0.0) -> float:
    """Parent Strategy1 rank, kept for A/B when Score-EV is off."""
    spec = max(0.0, min(1.0, float(specialization)))
    return float(expected_alpha) * (0.72 + 0.28 * spec) + 0.12 * spec


@dataclass(frozen=True)
class ScoreEVBreakdown:
    book: int
    side: str
    alpha: float
    fill_prob_old: float
    fill_prob_hazard: float | None
    actionable_fill_prob: float
    dust_prob: float
    spread_capture_bps: float
    expected_markout_bps: float
    fees_bps: float
    trading_ev: float
    observation_count: int
    required_observation_count: int
    observations_remaining: int
    completion_value: float
    dust_cost: float
    inventory_cost: float
    latency_cost: float
    final_score: float
    eligible: bool
    reject_reason: str | None

    def as_log(self) -> dict[str, Any]:
        return {
            "book": self.book,
            "side": self.side,
            "alpha": self.alpha,
            "fill_prob_old": self.fill_prob_old,
            "fill_prob_hazard": self.fill_prob_hazard,
            "actionable_fill_prob": self.actionable_fill_prob,
            "dust_prob": self.dust_prob,
            "spread_capture_bps": self.spread_capture_bps,
            "expected_markout_bps": self.expected_markout_bps,
            "fees_bps": self.fees_bps,
            "trading_ev": self.trading_ev,
            "observation_count": self.observation_count,
            "required_observation_count": self.required_observation_count,
            "observations_remaining": self.observations_remaining,
            "completion_value": self.completion_value,
            "dust_cost": self.dust_cost,
            "inventory_cost": self.inventory_cost,
            "latency_cost": self.latency_cost,
            "final_score": (
                self.final_score if math.isfinite(self.final_score) else None
            ),
            "eligible": self.eligible,
            "reject_reason": self.reject_reason,
        }


def compute_score_ev(
    *,
    book: int,
    side: str = "MM",
    alpha: float = 0.0,
    fill_prob_old: float = 0.0,
    fill_prob_hazard: float | None = None,
    actionable_fill_hazard: float | None = None,
    hazard_usable: bool = False,
    learned_actionable_p: float | None = None,
    learned_actionable_samples: int = 0,
    dust_prob: float = 0.0,
    spread_capture_bps: float = 0.0,
    markout_mean_bps: float | None = None,
    markout_samples: int = 0,
    fees_bps: float = 0.5,
    realized_observation_count: int = 0,
    required: int = PROTOCOL_DEFAULT_MIN_REALIZED_OBSERVATIONS,
    inventory_util: float = 0.0,
    latency_ms: float | None = None,
    toxic: bool = False,
    inventory_blocked: bool = False,
    unsafe: bool = False,
    min_trading_ev: float = 0.0,
    min_fill_samples: int = 8,
    min_markout_samples: int = 8,
    one_away_weight: float = 0.18,
    two_away_weight: float = 0.06,
    new_book_weight: float = 0.0,
    dust_target: float = 0.15,
    dust_weight: float = 0.25,
    inventory_weight: float = 0.08,
    latency_weight: float = 0.04,
) -> ScoreEVBreakdown:
    realized, req, remaining = observation_progress(realized_observation_count, required)
    p_act = conservative_actionable_probability(
        hazard_p=actionable_fill_hazard,
        hazard_usable=hazard_usable,
        learned_p=learned_actionable_p,
        learned_samples=learned_actionable_samples,
        fill_prob_old=fill_prob_old,
        min_samples=min_fill_samples,
    )
    markout = conservative_markout_bps(
        mean_bps=markout_mean_bps,
        samples=markout_samples,
        min_samples=min_markout_samples,
    )
    t_ev = trading_ev(
        actionable_fill_prob=p_act,
        spread_capture_bps=spread_capture_bps,
        expected_markout_bps=markout,
        fees_bps=fees_bps,
    )
    c_val = completion_value(
        observations_remaining=remaining,
        required_observation_count=req,
        one_away_weight=one_away_weight,
        two_away_weight=two_away_weight,
        new_book_weight=new_book_weight,
    )
    d_cost = dust_cost(dust_prob, target=dust_target, weight=dust_weight)
    i_cost = inventory_cost(inventory_util, weight=inventory_weight)
    l_cost = latency_cost(latency_ms, weight=latency_weight)
    reason = hard_safety_blocks(
        toxic=toxic,
        inventory_blocked=inventory_blocked,
        unsafe=unsafe,
        trading_ev_value=t_ev,
        min_trading_ev=min_trading_ev,
    )
    eligible = reason is None
    final = t_ev + c_val - d_cost - i_cost - l_cost if eligible else float("-inf")
    return ScoreEVBreakdown(
        book=int(book),
        side=str(side),
        alpha=float(alpha),
        fill_prob_old=float(fill_prob_old),
        fill_prob_hazard=None if fill_prob_hazard is None else float(fill_prob_hazard),
        actionable_fill_prob=p_act,
        dust_prob=max(0.0, min(1.0, float(dust_prob))),
        spread_capture_bps=float(spread_capture_bps),
        expected_markout_bps=markout,
        fees_bps=float(fees_bps),
        trading_ev=t_ev,
        observation_count=realized,
        required_observation_count=req,
        observations_remaining=remaining,
        completion_value=c_val,
        dust_cost=d_cost,
        inventory_cost=i_cost,
        latency_cost=l_cost,
        final_score=final,
        eligible=eligible,
        reject_reason=reason,
    )


def select_rank(
    *,
    enable_score_ev: bool,
    score_ev: ScoreEVBreakdown | None,
    legacy_rank: float,
) -> float | None:
    """Feature flag: Score-EV ranking or inherited global rank. None = reject."""
    if not enable_score_ev:
        return float(legacy_rank)
    if score_ev is None or not score_ev.eligible:
        return None
    return float(score_ev.final_score)


def scheduler_bucket_counts(
    observation_counts: dict[int, int],
    required: int,
    *,
    eligible_ids: set[int] | None = None,
) -> dict[str, int]:
    req = max(1, int(required))
    zero = 0
    rem1 = 0
    rem2 = 0
    for _book, n in observation_counts.items():
        realized, _, remaining = observation_progress(int(n), req)
        if realized <= 0:
            zero += 1
        if remaining == 1:
            rem1 += 1
        elif remaining == 2:
            rem2 += 1
    return {
        "books_zero_obs": zero,
        "books_one_remaining": rem1,
        "books_two_remaining": rem2,
        "eligible_books": len(eligible_ids) if eligible_ids is not None else 0,
        "tracked_books": len(observation_counts),
        "required_observation_count": req,
    }


HARD_SAFETY_REASONS = frozenset(
    {"HARD_SAFETY", "TOXIC", "INVENTORY_BLOCKED", "UNSAFE", "TTL_EXPIRED"}
)


def price_delta_ticks(old_price: float | None, new_price: float | None, tick_size: float) -> float:
    if old_price is None or new_price is None:
        return float("inf")
    tick = max(float(tick_size), 1e-12)
    return abs(float(new_price) - float(old_price)) / tick


def _sign(x: float, eps: float = 1e-9) -> int:
    if x > eps:
        return 1
    if x < -eps:
        return -1
    return 0


def imbalance_reversed(old_imb: float | None, new_imb: float | None, *, min_abs: float = 0.15) -> bool:
    if old_imb is None or new_imb is None:
        return False
    old = float(old_imb)
    new = float(new_imb)
    if abs(old) < min_abs or abs(new) < min_abs:
        return False
    return _sign(old) != 0 and _sign(new) != 0 and _sign(old) != _sign(new)


def alpha_reversed(old_alpha: float | None, new_alpha: float | None, *, min_abs: float = 0.08) -> bool:
    if old_alpha is None or new_alpha is None:
        return False
    old = float(old_alpha)
    new = float(new_alpha)
    if abs(old) < min_abs and abs(new) < min_abs:
        return False
    if _sign(old) != 0 and _sign(new) != 0 and _sign(old) != _sign(new):
        return abs(new - old) >= min_abs
    return abs(new - old) >= max(0.15, 2.0 * min_abs)


@dataclass(frozen=True)
class CancelDecision:
    cancel: bool
    reason: str
    old_price: float | None
    new_price: float | None
    price_delta_ticks: float
    old_ev: float | None
    new_ev: float | None
    ev_delta: float
    order_age_ms: float | None

    def as_log(self, *, book: int, side: str) -> dict[str, Any]:
        return {
            "book": int(book),
            "side": str(side),
            "cancel": int(bool(self.cancel)),
            "reason": self.reason,
            "old_price": self.old_price,
            "new_price": self.new_price,
            "price_delta_ticks": self.price_delta_ticks,
            "old_ev": self.old_ev,
            "new_ev": self.new_ev,
            "ev_delta": self.ev_delta,
            "order_age_ms": self.order_age_ms,
        }


def should_replace_quote(
    *,
    old_price: float | None,
    new_price: float | None,
    tick_size: float,
    min_price_ticks: float = 1.0,
    old_alpha: float | None = None,
    new_alpha: float | None = None,
    old_imbalance: float | None = None,
    new_imbalance: float | None = None,
    old_regime: str | None = None,
    new_regime: str | None = None,
    old_inventory_util: float | None = None,
    new_inventory_util: float | None = None,
    inventory_util_delta: float = 0.15,
    old_toxic: bool = False,
    new_toxic: bool = False,
    order_age_ms: float | None = None,
    ttl_ms: float | None = None,
    ttl_replace_frac: float = 0.85,
    old_ev: float | None = None,
    new_ev: float | None = None,
    ev_improve_threshold: float = 0.04,
    hard_safety: bool = False,
) -> CancelDecision:
    """HOLD unless a listed replacement rule fires. Hard safety is immediate."""
    ticks = price_delta_ticks(old_price, new_price, tick_size)
    ev_delta = 0.0
    if old_ev is not None and new_ev is not None:
        ev_delta = float(new_ev) - float(old_ev)
    age = None if order_age_ms is None else max(0.0, float(order_age_ms))

    def _dec(cancel: bool, reason: str) -> CancelDecision:
        return CancelDecision(
            cancel=cancel,
            reason=reason,
            old_price=old_price,
            new_price=new_price,
            price_delta_ticks=ticks if ticks != float("inf") else -1.0,
            old_ev=old_ev,
            new_ev=new_ev,
            ev_delta=ev_delta,
            order_age_ms=age,
        )

    if old_price is None:
        return _dec(True, "NEW")
    if hard_safety or new_toxic:
        return _dec(True, "HARD_SAFETY")
    if ttl_ms is not None and age is not None and float(ttl_ms) > 0:
        if age + 1e-9 >= float(ttl_ms) * max(0.0, min(1.0, float(ttl_replace_frac))):
            return _dec(True, "TTL_EXPIRED")
    if ticks >= max(1e-9, float(min_price_ticks)):
        return _dec(True, "PRICE")
    if alpha_reversed(old_alpha, new_alpha):
        return _dec(True, "ALPHA")
    if imbalance_reversed(old_imbalance, new_imbalance):
        return _dec(True, "OFI")
    old_r = str(old_regime or "").upper()
    new_r = str(new_regime or "").upper()
    if old_r and new_r and old_r != new_r:
        return _dec(True, "REGIME")
    if (
        old_inventory_util is not None
        and new_inventory_util is not None
        and abs(float(new_inventory_util) - float(old_inventory_util))
            >= max(0.0, float(inventory_util_delta))
    ):
        return _dec(True, "INVENTORY")
    if bool(old_toxic) != bool(new_toxic):
        return _dec(True, "TOXICITY")
    if ev_delta >= max(0.0, float(ev_improve_threshold)):
        return _dec(True, "EV")
    return _dec(False, "HOLD")


def clamp_ttl_ms(ttl_ms: float, min_ms: float, max_ms: float) -> float:
    lo = max(1.0, float(min_ms))
    hi = max(lo, float(max_ms))
    return max(lo, min(hi, float(ttl_ms)))


def choose_ttl_ms(
    *,
    baseline_ms: float,
    min_ms: float,
    max_ms: float,
    fill_hazard: float | None = None,
    volatility: float | None = None,
    imbalance: float | None = None,
    microprice_velocity: float | None = None,
    toxicity: bool = False,
    market_regime: str | None = None,
    queue_ahead: float | None = None,
    vol_high: float = 0.006,
    hazard_high: float = 0.35,
    imb_adverse: float = 0.35,
    stale_velocity_ticks: float | None = None,
) -> tuple[float | None, str, dict[str, Any]]:
    """Bounded TTL. Returns (None, STALE, ...) to skip submit."""
    regime = str(market_regime or "NORMAL").upper()
    vol = 0.0 if volatility is None else float(volatility)
    haz = 0.0 if fill_hazard is None else max(0.0, min(1.0, float(fill_hazard)))
    imb = 0.0 if imbalance is None else float(imbalance)
    vel = 0.0 if microprice_velocity is None else abs(float(microprice_velocity))
    info = {
        "fill_hazard": haz,
        "toxicity": int(bool(toxicity)),
        "volatility": vol,
        "imbalance": imb,
        "microprice_velocity": vel,
        "queue_ahead": queue_ahead,
        "market_regime": regime,
    }
    if stale_velocity_ticks is not None and vel >= float(stale_velocity_ticks):
        return None, "STALE", info
    if toxicity or regime in {"TOXIC", "STRESSED"}:
        ttl = clamp_ttl_ms(float(baseline_ms) * 0.50, min_ms, max_ms)
        return ttl, "TOXIC_SHORT", info
    if vol >= float(vol_high) or abs(imb) >= float(imb_adverse):
        ttl = clamp_ttl_ms(float(baseline_ms) * 0.70, min_ms, max_ms)
        return ttl, "ADVERSE_SHORT", info
    if haz >= float(hazard_high) and vol < 0.5 * float(vol_high) and abs(imb) < 0.5 * float(imb_adverse):
        stretch = 1.35
        if queue_ahead is not None and float(queue_ahead) > 0.0:
            stretch = 1.20
        ttl = clamp_ttl_ms(float(baseline_ms) * stretch, min_ms, max_ms)
        return ttl, "STABLE_LONG", info
    ttl = clamp_ttl_ms(float(baseline_ms), min_ms, max_ms)
    return ttl, "BASELINE", info


def would_create_dust(
    *,
    inventory_before: float,
    signed_fill_qty: float,
    min_order_size: float,
    eps: float = 1e-12,
) -> bool:
    """True when a fill would create dust or fail to reduce existing dust."""
    before = float(inventory_before)
    after = before + float(signed_fill_qty)
    min_size = max(0.0, float(min_order_size))
    e = max(float(eps), 1e-12)

    def _dust(qty: float) -> bool:
        aq = abs(float(qty))
        return min_size > 0.0 and aq >= e and aq + 1e-12 < min_size

    if not _dust(after):
        return False
    if abs(before) < e:
        return True
    return abs(after) + e >= abs(before)


def predicted_dust_blocks_increase(
    *,
    dust_prob: float,
    dust_target: float,
    inventory_before: float,
    signed_qty: float,
    usable: bool,
    eps: float = 1e-12,
) -> bool:
    """Skip exposure-increasing quotes when predicted dust exceeds the verified target."""
    if not usable:
        return False
    if float(dust_prob) <= float(dust_target):
        return False
    after = float(inventory_before) + float(signed_qty)
    return abs(after) > abs(float(inventory_before)) + max(float(eps), 1e-12)


@dataclass
class BookSnapshot:
    """Readable subset of state.books[book_id]."""
    book_id: int
    best_bid: float | None
    best_ask: float | None
    spread: float | None
    mid: float | None
    bid_levels: int
    ask_levels: int
    event_count: int
    trade_count: int
    last_trade_price: float | None
    last_trade_qty: float | None
    log_return: float | None = None
    pct_momentum: float | None = None

@dataclass
class AccountSnapshot:
    """Readable subset of state.accounts[uid][book_id] (also self.accounts after update)."""
    book_id: int
    base_total: float
    base_free: float
    quote_total: float
    quote_free: float
    base_loan: float
    quote_loan: float
    open_orders: int
    open_loans: int
    traded_volume: float | None
    maker_fee_rate: float | None
    taker_fee_rate: float | None

@dataclass
class StateSummary:
    """Top-level INPUT summary for one validator tick."""
    simulation_timestamp_ns: int
    simulation_time_human: str
    validator_hotkey: str
    taos_version: int | None
    book_count: int
    publish_interval_ns: int
    miner_wealth: float
    grace_period_ns: int
    notices_count: int
    notice_types: dict[str, int] = field(default_factory=dict)
    books: list[BookSnapshot] = field(default_factory=list)
    accounts: list[AccountSnapshot] = field(default_factory=list)

@dataclass
class ResponseSummary:
    """Readable view of OUTPUT instructions."""
    instruction_count: int
    by_type: dict[str, int]
    lines: list[str]

@dataclass
class DirectionForecast:
    """Per-book direction forecast from momentum + flow + trade flow."""
    book_id: int
    direction: Literal['UP', 'DOWN', 'HOLD']
    score: float
    momentum_m: float
    flow_f: float
    trade_t: float
    log_return: float | None
    imbalance: float
    trade_imbalance: float
BookTier = Literal['INACTIVE', 'RED', 'YELLOW', 'GREEN']

@dataclass
class BookProfile:
    """Per-book market + miner snapshot for scoring-aware book selection."""
    book_id: int
    spread: float | None
    mid: float | None
    spread_bps: float | None
    trade_rate: float
    volatility: float
    imbalance: float
    raw_kappa: float | None
    realized_pnl: float
    pnl_obs_count: int
    traded_volume: float
    predict_score: float
    predict_direction: str
    tier: BookTier
    alpha_rank: float

@dataclass
class BookSelection:
    """Scoring-aware book lists for alpha vs maintenance vs avoid."""
    alpha_books: list[int]
    maintenance_books: list[int]
    avoid_books: list[int]
    tier_counts: dict[str, int]
    profiles: list[BookProfile] = field(default_factory=list)
MarketRegimeMode = Literal['QUIET', 'CHOP', 'TRENDING_UP', 'TRENDING_DOWN', 'DISPERSED', 'STRESSED', 'BROAD_LIQUID', 'MIXED']
ScoringOverlay = Literal['SCORING_PRESSURE', 'DAMAGE_CONTROL', 'SCORING_COMFORT']

@dataclass
class RealizedPnLEstimate:
    """Expected realized PnL if planned limits fill at assumed prices (FIFO model)."""
    book_id: int
    layer: str
    quantity: float
    buy_price: float | None
    sell_price: float | None
    leg_first_pnl: float
    leg_second_pnl: float
    expected_realized_pnl: float
    closes_existing_position: bool
    direction: str | None = None
    is_maker_assumed: bool = True

@dataclass
class MarketRegime:
    """Cross-book market regime from aggregated book profiles and predictions."""
    mode: MarketRegimeMode
    hold_frac: float
    up_frac: float
    down_frac: float
    mean_score: float
    mean_abs_score: float
    mean_volatility: float
    mean_trade_rate: float
    mean_spread_bps: float | None
    mean_imbalance: float
    mean_log_return: float | None
    return_dispersion: float | None
    direction_dispersion: float
    tier_counts: dict[str, int]
    inactive_frac: float
    red_frac: float
    green_frac: float
    scoring_overlay: ScoringOverlay | None
    confidence: float
    book_count: int
BookArchetype = Literal['DEAD_BOOK', 'MM_BOOK', 'WALL_BOOK', 'TREND_BOOK', 'TOXIC_BOOK', 'STRESSED']
InventoryBand = Literal['FLAT', 'LONG', 'SHORT', 'MAX_LONG', 'MAX_SHORT']
InventoryReason = Literal['UNKNOWN', 'MAINTENANCE', 'MM', 'ALPHA', 'MARKET']
MAINT_CLIENT_ID_BASE = 20000
MM_CLIENT_ID_BASE = 70000
ALPHA_CLIENT_ID_BASE = 80000

@dataclass
class RegimeParamSet:
    quote_enabled: bool
    alpha_enabled: bool
    spread_offset: float
    skew_strength: float
    size_mult: float
    profit_target_bps: float
    stop_loss_bps: float
    min_fill_prob: float
    buy_bias: float = 1.0
    sell_bias: float = 1.0

@dataclass
class ArchetypeAdjust:
    """Multipliers/deltas applied on top of regime params for a book archetype."""
    size_mult: float = 1.0
    spread_offset_delta: float = 0.0
    skew_strength_mult: float = 1.0
    min_fill_prob_delta: float = 0.0
    edge_bias: float = 0.0
    quote_enabled_override: bool | None = None

@dataclass
class FillProbabilityEstimate:
    buy: float
    sell: float

@dataclass
class PositionTracker:
    """VWAP position derived from FIFO open lots (validator-aligned)."""
    net_qty: float
    vwap_entry: float | None
    opened_at_ns: int | None
    long_qty: float
    short_qty: float

@dataclass
class BookMemory:
    """Per-book trading memory — drives skip/size, fill learning, direction accuracy."""
    quote_count: int = 0
    fill_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    recent_pnl: float = 0.0
    last_activity_ts: int = 0
    loss_streak: int = 0
    last_signal: float = 0.0
    last_expected_alpha: float = 0.0
    direction_hits: int = 0
    direction_misses: int = 0
    fill_buy_quotes: tuple[int, int, int] = (0, 0, 0)
    fill_buy_fills: tuple[int, int, int] = (0, 0, 0)
    fill_sell_quotes: tuple[int, int, int] = (0, 0, 0)
    fill_sell_fills: tuple[int, int, int] = (0, 0, 0)
    last_buy_dist_bucket: int = 0
    last_sell_dist_bucket: int = 0
    book_profit_factor: float = 0.5
    book_fill_factor: float = 0.5
    book_kappa_factor: float = 0.5

    @property
    def fill_rate(self) -> float:
        return self.fill_count / max(self.quote_count, 1)

    @property
    def win_rate(self) -> float:
        total = self.win_count + self.loss_count
        return self.win_count / max(total, 1)

    @property
    def direction_accuracy(self) -> float:
        total = self.direction_hits + self.direction_misses
        if total == 0:
            return 0.5
        return self.direction_hits / total

    @property
    def specialization_score(self) -> float:
        return 0.4 * self.book_profit_factor + 0.35 * self.book_fill_factor + 0.25 * self.book_kappa_factor

@dataclass
class TuningMetrics:
    kappa_med: float
    win_rate: float
    skip_neg_rate: float
    objective: float
    window_ticks: int
TUNING_PARAM_BOUNDS: dict[str, tuple[float, float]] = {'min_expected_alpha': (0.15, 0.45), 'min_expected_realized_pnl': (0.0, 0.002), 'max_mm_books_per_tick': (4.0, 12.0), 'toxic_loss_streak': (2.0, 5.0), 'toxic_recent_pnl': (-0.05, -0.001), 'coverage_boost_weight': (0.05, 0.3)}

@dataclass
class InventorySnapshot:
    net_base: float
    inventory_ratio: float
    band: InventoryBand
    vwap_entry: float | None
    unrealized_bps: float | None
    position_ticks: int = 0
    opened_at_ns: int | None = None
    reason: InventoryReason = 'UNKNOWN'
DEFAULT_REGIME_PARAMS: dict[MarketRegimeMode, RegimeParamSet] = {'QUIET': RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=0.25, skew_strength=0.15, size_mult=0.8, profit_target_bps=5.0, stop_loss_bps=35.0, min_fill_prob=0.15), 'CHOP': RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=0.35, skew_strength=0.1, size_mult=0.7, profit_target_bps=8.0, stop_loss_bps=40.0, min_fill_prob=0.2), 'TRENDING_UP': RegimeParamSet(quote_enabled=True, alpha_enabled=True, spread_offset=0.2, skew_strength=0.3, size_mult=1.2, profit_target_bps=12.0, stop_loss_bps=45.0, min_fill_prob=0.25, buy_bias=2.0, sell_bias=0.5), 'TRENDING_DOWN': RegimeParamSet(quote_enabled=True, alpha_enabled=True, spread_offset=0.2, skew_strength=0.3, size_mult=1.2, profit_target_bps=12.0, stop_loss_bps=45.0, min_fill_prob=0.25, buy_bias=0.5, sell_bias=2.0), 'BROAD_LIQUID': RegimeParamSet(quote_enabled=True, alpha_enabled=True, spread_offset=0.22, skew_strength=0.2, size_mult=1.0, profit_target_bps=10.0, stop_loss_bps=40.0, min_fill_prob=0.2), 'DISPERSED': RegimeParamSet(quote_enabled=True, alpha_enabled=True, spread_offset=0.28, skew_strength=0.25, size_mult=0.9, profit_target_bps=10.0, stop_loss_bps=40.0, min_fill_prob=0.22), 'STRESSED': RegimeParamSet(quote_enabled=False, alpha_enabled=False, spread_offset=0.45, skew_strength=0.05, size_mult=0.5, profit_target_bps=15.0, stop_loss_bps=25.0, min_fill_prob=0.3, buy_bias=0.25, sell_bias=0.25), 'MIXED': RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=0.28, skew_strength=0.18, size_mult=0.85, profit_target_bps=8.0, stop_loss_bps=38.0, min_fill_prob=0.18)}
DEFAULT_ARCHETYPE_ADJUST: dict[BookArchetype, ArchetypeAdjust] = {'DEAD_BOOK': ArchetypeAdjust(size_mult=0.6, spread_offset_delta=0.1, min_fill_prob_delta=-0.05), 'MM_BOOK': ArchetypeAdjust(size_mult=0.3, spread_offset_delta=-0.05, skew_strength_mult=0.5), 'WALL_BOOK': ArchetypeAdjust(size_mult=0.5, spread_offset_delta=0.15, edge_bias=0.2), 'TREND_BOOK': ArchetypeAdjust(size_mult=1.0, spread_offset_delta=-0.05, skew_strength_mult=1.3, edge_bias=0.3), 'TOXIC_BOOK': ArchetypeAdjust(size_mult=0.4, spread_offset_delta=0.2, min_fill_prob_delta=0.05), 'STRESSED': ArchetypeAdjust(size_mult=0.25, quote_enabled_override=False)}

class DebugReason:
    """Stable reason codes for grep, jq, and automated comparisons."""
    QUOTED = 'QUOTED'
    MANAGED_INVENTORY = 'MANAGED_INVENTORY'
    MAINTENANCE_ORDER = 'MAINTENANCE_ORDER'
    ALPHA_ORDER = 'ALPHA_ORDER'
    NO_BOOK_SIDES = 'NO_BOOK_SIDES'
    NO_PROFILE = 'NO_PROFILE'
    AVOID_LIST = 'AVOID_LIST'
    NO_PREDICTION = 'NO_PREDICTION'
    GRACE_PERIOD = 'GRACE_PERIOD'
    MANAGEMENT_LIMIT = 'MANAGEMENT_LIMIT'
    MANAGE_ORDER_GATE = 'MANAGE_ORDER_GATE'
    MAINT_INVENTORY_NONFLAT = 'MAINT_INVENTORY_NONFLAT'
    MAINT_ARCHETYPE_BLOCK = 'MAINT_ARCHETYPE_BLOCK'
    MAINT_ORDER_GATE = 'MAINT_ORDER_GATE'
    TOXIC_BOOK = 'TOXIC_BOOK'
    QUOTE_DISABLED = 'QUOTE_DISABLED'
    TOXIC_REGIME = 'TOXIC_REGIME'
    INACTIVE_TIER = 'INACTIVE_TIER'
    LOW_EXPECTED_ALPHA = 'LOW_EXPECTED_ALPHA'
    MM_CANDIDATE_LIMIT = 'MM_CANDIDATE_LIMIT'
    MAX_INVENTORY = 'MAX_INVENTORY'
    INVALID_QUOTE_PRICES = 'INVALID_QUOTE_PRICES'
    ZERO_ORDER_SIZE = 'ZERO_ORDER_SIZE'
    VOLUME_CAP = 'VOLUME_CAP'
    NON_POSITIVE_EDGE = 'NON_POSITIVE_EDGE'
    NEGATIVE_EXPECTED_PNL = 'NEGATIVE_EXPECTED_PNL'
    LOW_FILL_PROBABILITY = 'LOW_FILL_PROBABILITY'
    INSTRUCTION_LIMIT = 'INSTRUCTION_LIMIT'
    INSUFFICIENT_BALANCE = 'INSUFFICIENT_BALANCE'
    QUOTE_ORDER_GATE = 'QUOTE_ORDER_GATE'
    NO_ACTION = 'NO_ACTION'
class BaseStrategy(FinanceSimulationAgent):
    """Optimized standalone V4.1 Strict base for all derived Strategy agents."""
    DEPLOY_POLICY_VERSION = 'base_v4_1_1_maker_guard'
    REGIME_POLICY_VERSION = 'regime_v2'
    EXECUTION_POLICY_VERSION = 'execution_v1_frozen'
    SCORE_EV_POLICY_VERSION = 'score_ev_v1'
    QUOTE_POLICY_VERSION = 'quote_hysteresis_ttl_v1'
    REASON_ALIAS = {'LOW_EXPECTED_ALPHA': 'ALPHA', 'ZERO_ORDER_SIZE': 'SIZE_ZERO', 'MAX_INVENTORY': 'INVENTORY_MAX', 'INVALID_QUOTE_PRICES': 'BAD_PRICE', 'VOLUME_CAP': 'VOLUME_CAP', 'NON_POSITIVE_EDGE': 'EDGE', 'NEGATIVE_EXPECTED_PNL': 'NEG_PNL', 'LOW_FILL_PROBABILITY': 'FILL_PROB', 'INSTRUCTION_LIMIT': 'INSTR_LIMIT', 'INSUFFICIENT_BALANCE': 'BALANCE', 'QUOTE_ORDER_GATE': 'QUOTE_GATE', 'QUOTE_DISABLED': 'REGIME_DISABLED', 'TOXIC_BOOK': 'TOXIC', 'TOXIC_REGIME': 'TOXIC_REGIME', 'INACTIVE_TIER': 'INACTIVE', 'MM_CANDIDATE_LIMIT': 'MM_LIMIT', 'MANAGEMENT_LIMIT': 'MANAGEMENT_LIMIT', 'MANAGE_ORDER_GATE': 'MANAGE_GATE', 'MAINT_INVENTORY_NONFLAT': 'MAINT_INVENTORY', 'MAINT_ARCHETYPE_BLOCK': 'MAINT_ARCHETYPE', 'MAINT_ORDER_GATE': 'MAINT_GATE', 'NO_BOOK_SIDES': 'NO_BOOK_SIDES', 'NO_PROFILE': 'NO_PROFILE', 'AVOID_LIST': 'AVOID', 'NO_PREDICTION': 'NO_PREDICTION', 'GRACE_PERIOD': 'GRACE', 'NO_ACTION': 'NO_ACTION', 'HARD_CAP': 'HARD_CAP', 'STALE': 'STALE', 'DUST': 'DUST', 'DUST_POSITION': 'DUST', 'INACTIVE_DIAGNOSTIC_ONLY': 'INACTIVE_DIAG', 'MM_SUCCESS_CAP': 'MM_CAP', 'DUST_QUARANTINE': 'DUST_PARK', 'DUST_RELEASED': 'DUST_RELEASE', 'DUST_COMPACT': 'DUST_COMPACT', 'DUST_COMPACT_BLOCKED': 'DUST_COMPACT_BLOCKED', 'KAPPA_COMPLETION': 'KAPPA_COMPLETE', 'KAPPA_COMPLETION_ATTEMPT_CAP': 'KAPPA_ATTEMPT_CAP', 'KAPPA_COMPLETION_SUCCESS_CAP': 'KAPPA_SUCCESS_CAP', 'NORMAL_MM_ATTEMPT_CAP': 'NORMAL_ATTEMPT_CAP'}

    def _bsimpl_0_DetailedTemplateAgent_initialize(self) -> None:
        self.fast_update = bool(getattr(self.config, 'fast_update', False))
        self.sync_event_csv = bool(getattr(self.config, 'sync_event_csv', not self.fast_update))
        self.log_latency = bool(getattr(self.config, 'log_latency', False))
        self.verbose_log = bool(getattr(self.config, 'verbose_log', True))
        self.log_every_n = int(getattr(self.config, 'log_every_n', 50))
        self.enable_trading = bool(getattr(self.config, 'enable_trading', False))
        self.demo_order_size = float(getattr(self.config, 'demo_order_size', 0.25))
        self.log_momentum_pnl = bool(getattr(self.config, 'log_momentum_pnl', True))
        self.momentum_window_ticks = max(2, int(getattr(self.config, 'momentum_window_ticks', 10)))
        self.pnl_lookback_ns = int(getattr(self.config, 'pnl_lookback_ns', 10800000000000))
        self.pnl_log_file = bool(getattr(self.config, 'pnl_log_file', False))
        self.log_kappa = bool(getattr(self.config, 'log_kappa', True))
        self.pnl_sequence_max_entries = int(getattr(self.config, 'pnl_sequence_max_entries', 32))
        self.kappa_tau = float(getattr(self.config, 'kappa_tau', 0.0))
        self.kappa_min_lookback = int(getattr(self.config, 'kappa_min_lookback_ns', 5400000000000))
        self.kappa_min_observations = int(getattr(self.config, 'kappa_min_observations', 3))
        self.kappa_norm_min = float(getattr(self.config, 'kappa_norm_min', -2.5))
        self.kappa_norm_max = float(getattr(self.config, 'kappa_norm_max', 2.5))
        self.w_m = float(getattr(self.config, 'w_m', 1.0))
        self.w_f = float(getattr(self.config, 'w_f', 1.0))
        self.w_t = float(getattr(self.config, 'w_t', 0.5))
        self.direction_threshold = float(getattr(self.config, 'direction_threshold', 0.15))
        self.momentum_scale = float(getattr(self.config, 'momentum_scale', 0.002))
        self.flow_depth = int(getattr(self.config, 'flow_depth', 5))
        self.log_direction = bool(getattr(self.config, 'log_direction', True))
        self.log_book_profile = bool(getattr(self.config, 'log_book_profile', True))
        self.max_inactive_books_ratio = float(getattr(self.config, 'max_inactive_books_ratio', 0.375))
        self.green_kappa_threshold = float(getattr(self.config, 'green_kappa_threshold', 0.0))
        self.red_kappa_threshold = float(getattr(self.config, 'red_kappa_threshold', -0.5))
        self.spread_alpha_max = float(getattr(self.config, 'spread_alpha_max', 0.002))
        self.profile_w_k = float(getattr(self.config, 'profile_w_k', 1.0))
        self.profile_w_p = float(getattr(self.config, 'profile_w_p', 0.5))
        self.profile_w_s = float(getattr(self.config, 'profile_w_s', 0.3))
        self.profile_w_v = float(getattr(self.config, 'profile_w_v', 0.2))
        self.profile_vol_scale = float(getattr(self.config, 'profile_vol_scale', 0.01))
        self.log_regime = bool(getattr(self.config, 'log_regime', True))
        self.regime_hold_frac_threshold = float(getattr(self.config, 'regime_hold_frac_threshold', 0.7))
        self.regime_trend_frac_threshold = float(getattr(self.config, 'regime_trend_frac_threshold', 0.5))
        self.regime_dispersed_frac_threshold = float(getattr(self.config, 'regime_dispersed_frac_threshold', 0.25))
        self.regime_stressed_spread_bps = float(getattr(self.config, 'regime_stressed_spread_bps', 5.0))
        self.regime_chop_vol_threshold = float(getattr(self.config, 'regime_chop_vol_threshold', 0.005))
        self.regime_active_trade_rate = float(getattr(self.config, 'regime_active_trade_rate', 2.0))
        self.enable_kappa_strategy = bool(getattr(self.config, 'enable_kappa_strategy', False))
        self.maintenance_order_size = float(getattr(self.config, 'maintenance_order_size', 0.25))
        self.alpha_order_size = float(getattr(self.config, 'alpha_order_size', 0.25))
        self.max_alpha_books_per_tick = max(1, int(getattr(self.config, 'max_alpha_books_per_tick', 4)))
        self.max_maintenance_books_per_tick = max(1, int(getattr(self.config, 'max_maintenance_books_per_tick', 3)))
        self.capital_turnover_cap = float(getattr(self.config, 'capital_turnover_cap', 10.0))
        self.max_taker_fee_rate = float(getattr(self.config, 'max_taker_fee_rate', 0.015))
        self.max_instructions_per_book = max(1, int(getattr(self.config, 'max_instructions_per_book', 5)))
        self.min_order_size = float(getattr(self.config, 'min_order_size', 0.25))
        self.log_kappa_strategy = bool(getattr(self.config, 'log_kappa_strategy', True))
        self.log_predict_pnl = bool(getattr(self.config, 'log_predict_pnl', True))
        self._last_kappa: dict | None = None
        self._last_pnl_estimates: list[RealizedPnLEstimate] = []
        self._last_strategy_stats: dict = {}
        self._last_predictions: dict[int, DirectionForecast] = {}
        self._last_profiles: list[BookProfile] = []
        self._last_selection: BookSelection | None = None
        self._last_regime: MarketRegime | None = None
        self._tick = 0
        self._mid_history: dict[int, list[tuple[int, float]]] = defaultdict(list)
        self.realized_pnl_history: dict[int, dict[int, float]] = {}
        self.total_realized_pnl_by_book: dict[int, float] = defaultdict(float)
        # Hot-path caches: profile construction used to scan the full PnL
        # history twice for every book, and local Kappa was recomputed even
        # when the realized-PnL history had not changed.
        self._pnl_history_version = 0
        self._pnl_lookback_cache_key: tuple[int, int] | None = None
        self._pnl_obs_cache: dict[int, int] = {}
        self._pnl_sum_cache: dict[int, float] = {}
        self._local_kappa_cache_key: tuple[Any, ...] | None = None
        self._local_kappa_cache: dict | None = None
        self._open_positions: dict[int, dict[str, Deque[tuple[int, float, float, float]]]] = defaultdict(lambda: {'longs': deque(), 'shorts': deque()})
        self._scoring_timestamp: int = 0
        self._pnl_tick_buffer: dict[int, float] = {}
        self._pnl_csv_path = os.path.join(self.output_dir, 'momentum_pnl_log.csv')

    def _bsimpl_0_DetailedTemplateAgent__reset_pnl_state(self) -> None:
        self.realized_pnl_history.clear()
        self._invalidate_pnl_caches()
        self.total_realized_pnl_by_book.clear()
        self._open_positions.clear()
        self._mid_history.clear()
        self._last_kappa = None
        self._last_predictions.clear()
        self._last_profiles.clear()
        self._last_selection = None
        self._last_regime = None
        self._last_strategy_stats = {}
        self._last_pnl_estimates = []

    def _bsimpl_0_DetailedTemplateAgent__dispatch_notice_event(self, event, state: MarketSimulationStateUpdate, validator: str) -> bool:
        """Handle one notice; return True if simulation ended."""
        etype = event.type
        if etype in ('RESET_AGENTS', 'RA'):
            return False
        if etype in ('EVENT_SIMULATION_START', 'ESS'):
            self.onStart(event)
            return False
        if etype in ('EVENT_SIMULATION_END', 'ESE'):
            self.onEnd(event)
            return True
        match etype:
            case 'RESPONSE_DISTRIBUTED_PLACE_ORDER_LIMIT' | 'RESPONSE_DISTRIBUTED_PLACE_ORDER_MARKET' | 'RDPOL' | 'RDPOM':
                self.onOrderAccepted(event)
                if self.sync_event_csv:
                    self.log_order_event(event, state)
            case 'ERROR_RESPONSE_DISTRIBUTED_PLACE_ORDER_LIMIT' | 'ERROR_RESPONSE_DISTRIBUTED_PLACE_ORDER_MARKET' | 'ERDPOL' | 'ERDPOM':
                self.onOrderRejected(event)
            case 'RESPONSE_DISTRIBUTED_CANCEL_ORDERS' | 'RDCO':
                for cancellation in event.cancellations:
                    self.onOrderCancelled(cancellation)
                    if self.sync_event_csv:
                        self.log_cancellation_event(cancellation, state)
            case 'ERROR_RESPONSE_DISTRIBUTED_CANCEL_ORDERS' | 'ERDCO':
                for cancellation in event.cancellations:
                    self.onOrderCancellationFailed(cancellation)
            case 'RESPONSE_DISTRIBUTED_CLOSE_POSITIONS' | 'RDCP':
                for close in event.closes:
                    self.onPositionClosed(close)
            case 'ERROR_RESPONSE_DISTRIBUTED_CLOSE_POSITIONS' | 'ERDCP':
                for close in event.closes:
                    self.onPositionCloseFailed(close)
            case 'EVENT_TRADE' | 'ET':
                self.onTrade(event, validator)
                if self.sync_event_csv:
                    self.log_trade_event(event, state)
            case _:
                if etype not in ('EVENT_TRADE', 'ET'):
                    bt.logging.warning(f'Unknown event : {event}')
        return False

    def _bsimpl_0_DetailedTemplateAgent__fast_update(self, state: MarketSimulationStateUpdate) -> None:
        """
        Lightweight update: single pass over notices, no per-book debug logging.
        Avoids O(book_count × events) work in FinanceSimulationAgent.update().
        """
        if self.history_len:
            self.history.append(state.model_copy())
            self.history = self.history[-self.history_len:]
        else:
            self.history = []
        self.simulation_config = state.config
        self.accounts = state.accounts[self.uid]
        self.events = state.notices[self.uid]
        validator = state.dendrite.hotkey
        for event in self.events:
            self._dispatch_notice_event(event, state, validator)

    def _bsimpl_0_DetailedTemplateAgent_update(self, state: MarketSimulationStateUpdate) -> None:
        """Process notices (incl. onTrade) then flush tick PnL into history."""
        self._scoring_timestamp = state.timestamp
        self._pnl_tick_buffer = {}
        if self.fast_update:
            self._fast_update(state)
        else:
            super(BaseStrategy, self).update(state)
        history_changed = False
        if self._pnl_tick_buffer:
            ts = self._scoring_timestamp
            bucket = self.realized_pnl_history.setdefault(ts, {})
            for book_id, pnl in self._pnl_tick_buffer.items():
                bucket[book_id] = round(bucket.get(book_id, 0.0) + pnl, 10)
                self.total_realized_pnl_by_book[book_id] += pnl
            history_changed = True
        before_prune = len(self.realized_pnl_history)
        self._prune_pnl_history(state.timestamp)
        if len(self.realized_pnl_history) != before_prune:
            history_changed = True
        if history_changed:
            self._invalidate_pnl_caches()

    def _bsimpl_0_DetailedTemplateAgent_onStart(self, event: SimulationStartEvent) -> None:
        self._reset_pnl_state()
        bt.logging.info(f'Simulation start — reset momentum / realized PnL history ({event.logDir})')

    def _bsimpl_0_DetailedTemplateAgent_onTrade(self, event: TradeEvent, validator: str | None=None) -> None:
        if event.bookId is None:
            return
        is_taker = self.uid == event.takerAgentId
        is_maker = self.uid == event.makerAgentId
        if not is_taker and (not is_maker):
            return
        is_buy = is_taker and event.side == 0 or (is_maker and event.side == 1)
        fee = event.makerFee if is_maker else event.takerFee
        realized_pnl, _ = self._match_trade_fifo(event.bookId, is_buy, event.quantity, event.price, fee, event.timestamp)
        if realized_pnl != 0.0:
            self._pnl_tick_buffer[event.bookId] = self._pnl_tick_buffer.get(event.bookId, 0.0) + realized_pnl

    def _bsimpl_0_DetailedTemplateAgent__match_trade_fifo(self, book_id: int, is_buy: bool, quantity: float, price: float, fee: float, timestamp: int) -> tuple[float, float]:
        """FIFO round-trip matcher — same logic as validator _match_trade_fifo."""
        positions = self._open_positions[book_id]
        longs = positions['longs']
        shorts = positions['shorts']
        if is_buy:
            if not shorts:
                open_fee = fee if quantity > 0 else 0.0
                longs.append((timestamp, quantity, price, open_fee))
                return (0.0, 0.0)
        elif not longs:
            open_fee = fee if quantity > 0 else 0.0
            shorts.append((timestamp, quantity, price, open_fee))
            return (0.0, 0.0)
        realized_pnl = 0.0
        roundtrip_volume = 0.0
        remaining_qty = quantity
        quantity_inv = 1.0 / quantity if quantity > 0 else 0.0
        if is_buy:
            while remaining_qty > 0 and shorts:
                old_ts, old_qty, old_price, old_fee = shorts[0]
                if old_qty <= remaining_qty:
                    price_pnl = (old_price - price) * old_qty
                    close_fee = fee * old_qty * quantity_inv
                    realized_pnl += price_pnl - old_fee - close_fee
                    roundtrip_volume += old_qty
                    remaining_qty -= old_qty
                    shorts.popleft()
                else:
                    old_qty_inv = 1.0 / old_qty
                    price_pnl = (old_price - price) * remaining_qty
                    close_fee = fee
                    open_fee = old_fee * remaining_qty * old_qty_inv
                    realized_pnl += price_pnl - open_fee - close_fee
                    roundtrip_volume += remaining_qty
                    shorts[0] = (old_ts, old_qty - remaining_qty, old_price, old_fee - open_fee)
                    remaining_qty = 0
            if remaining_qty > 0:
                longs.append((timestamp, remaining_qty, price, fee * remaining_qty * quantity_inv))
        else:
            while remaining_qty > 0 and longs:
                old_ts, old_qty, old_price, old_fee = longs[0]
                if old_qty <= remaining_qty:
                    price_pnl = (price - old_price) * old_qty
                    close_fee = fee * old_qty * quantity_inv
                    realized_pnl += price_pnl - old_fee - close_fee
                    roundtrip_volume += old_qty
                    remaining_qty -= old_qty
                    longs.popleft()
                else:
                    old_qty_inv = 1.0 / old_qty
                    price_pnl = (price - old_price) * remaining_qty
                    close_fee = fee
                    open_fee = old_fee * remaining_qty * old_qty_inv
                    realized_pnl += price_pnl - open_fee - close_fee
                    roundtrip_volume += remaining_qty
                    longs[0] = (old_ts, old_qty - remaining_qty, old_price, old_fee - open_fee)
                    remaining_qty = 0
            if remaining_qty > 0:
                shorts.append((timestamp, remaining_qty, price, fee * remaining_qty * quantity_inv))
        return (realized_pnl, roundtrip_volume)

    def _bsimpl_0_DetailedTemplateAgent__copy_positions_for_book(self, book_id: int) -> dict[str, Deque[tuple[int, float, float, float]]]:
        pos = self._open_positions[book_id]
        return {'longs': deque(pos['longs']), 'shorts': deque(pos['shorts'])}

    def _bsimpl_0_DetailedTemplateAgent__simulate_fifo_pnl(self, positions: dict[str, Deque[tuple[int, float, float, float]]], is_buy: bool, quantity: float, price: float, fee: float, timestamp: int) -> tuple[float, float]:
        """FIFO matcher on a copied position book — does not mutate live state."""
        longs = positions['longs']
        shorts = positions['shorts']
        if is_buy:
            if not shorts:
                open_fee = fee if quantity > 0 else 0.0
                longs.append((timestamp, quantity, price, open_fee))
                return (0.0, 0.0)
        elif not longs:
            open_fee = fee if quantity > 0 else 0.0
            shorts.append((timestamp, quantity, price, open_fee))
            return (0.0, 0.0)
        realized_pnl = 0.0
        roundtrip_volume = 0.0
        remaining_qty = quantity
        quantity_inv = 1.0 / quantity if quantity > 0 else 0.0
        if is_buy:
            while remaining_qty > 0 and shorts:
                old_ts, old_qty, old_price, old_fee = shorts[0]
                if old_qty <= remaining_qty:
                    price_pnl = (old_price - price) * old_qty
                    close_fee = fee * old_qty * quantity_inv
                    realized_pnl += price_pnl - old_fee - close_fee
                    roundtrip_volume += old_qty
                    remaining_qty -= old_qty
                    shorts.popleft()
                else:
                    old_qty_inv = 1.0 / old_qty
                    price_pnl = (old_price - price) * remaining_qty
                    close_fee = fee
                    open_fee = old_fee * remaining_qty * old_qty_inv
                    realized_pnl += price_pnl - open_fee - close_fee
                    roundtrip_volume += remaining_qty
                    shorts[0] = (old_ts, old_qty - remaining_qty, old_price, old_fee - open_fee)
                    remaining_qty = 0
            if remaining_qty > 0:
                longs.append((timestamp, remaining_qty, price, fee * remaining_qty * quantity_inv))
        else:
            while remaining_qty > 0 and longs:
                old_ts, old_qty, old_price, old_fee = longs[0]
                if old_qty <= remaining_qty:
                    price_pnl = (price - old_price) * old_qty
                    close_fee = fee * old_qty * quantity_inv
                    realized_pnl += price_pnl - old_fee - close_fee
                    roundtrip_volume += old_qty
                    remaining_qty -= old_qty
                    longs.popleft()
                else:
                    old_qty_inv = 1.0 / old_qty
                    price_pnl = (price - old_price) * remaining_qty
                    close_fee = fee
                    open_fee = old_fee * remaining_qty * old_qty_inv
                    realized_pnl += price_pnl - open_fee - close_fee
                    roundtrip_volume += remaining_qty
                    longs[0] = (old_ts, old_qty - remaining_qty, old_price, old_fee - open_fee)
                    remaining_qty = 0
            if remaining_qty > 0:
                shorts.append((timestamp, remaining_qty, price, fee * remaining_qty * quantity_inv))
        return (realized_pnl, roundtrip_volume)

    def _bsimpl_0_DetailedTemplateAgent__estimate_trade_fee(self, book_id: int, quantity: float, price: float, is_maker: bool) -> float:
        account = self.accounts.get(book_id)
        if not account or not account.fees or quantity <= 0 or (price <= 0):
            return 0.0
        rate = account.fees.maker_fee_rate if is_maker else account.fees.taker_fee_rate
        return quantity * price * rate

    def _bsimpl_0_DetailedTemplateAgent__book_has_open_lots(self, book_id: int) -> bool:
        pos = self._open_positions.get(book_id)
        if not pos:
            return False
        return len(pos['longs']) > 0 or len(pos['shorts']) > 0

    def _bsimpl_0_DetailedTemplateAgent_estimate_realized_pnl(self, book_id: int, is_buy: bool, quantity: float, price: float, is_maker: bool=True, timestamp: int=0) -> float:
        """
        Expected realized PnL for one hypothetical fill using current FIFO open lots.

        Returns 0.0 when the trade would only open a new position (no round-trip close).
        """
        if quantity <= 0 or price <= 0:
            return 0.0
        positions = self._copy_positions_for_book(book_id)
        fee = self._estimate_trade_fee(book_id, quantity, price, is_maker)
        pnl, _ = self._simulate_fifo_pnl(positions, is_buy, quantity, price, fee, timestamp)
        return pnl

    def _bsimpl_0_DetailedTemplateAgent_estimate_round_trip_pnl(self, book_id: int, buy_price: float, sell_price: float, quantity: float, is_maker: bool=True, direction: Literal['UP', 'DOWN', 'SYMMETRIC']='SYMMETRIC', timestamp: int=0) -> RealizedPnLEstimate:
        """
        Expected realized PnL if a two-leg round-trip fills at buy_price / sell_price.

        UP / SYMMETRIC: buy then sell. DOWN: sell then buy.
        """
        positions = self._copy_positions_for_book(book_id)
        closes_existing = self._book_has_open_lots(book_id)
        buy_fee = self._estimate_trade_fee(book_id, quantity, buy_price, is_maker)
        sell_fee = self._estimate_trade_fee(book_id, quantity, sell_price, is_maker)
        leg_first_pnl = 0.0
        leg_second_pnl = 0.0
        dir_label: str | None = None
        if direction in ('UP', 'SYMMETRIC'):
            dir_label = 'UP' if direction == 'UP' else None
            leg_first_pnl, _ = self._simulate_fifo_pnl(positions, True, quantity, buy_price, buy_fee, timestamp)
            leg_second_pnl, _ = self._simulate_fifo_pnl(positions, False, quantity, sell_price, sell_fee, timestamp)
        else:
            dir_label = 'DOWN'
            leg_first_pnl, _ = self._simulate_fifo_pnl(positions, False, quantity, sell_price, sell_fee, timestamp)
            leg_second_pnl, _ = self._simulate_fifo_pnl(positions, True, quantity, buy_price, buy_fee, timestamp)
        return RealizedPnLEstimate(book_id=book_id, layer='', quantity=quantity, buy_price=buy_price, sell_price=sell_price, leg_first_pnl=leg_first_pnl, leg_second_pnl=leg_second_pnl, expected_realized_pnl=leg_first_pnl + leg_second_pnl, closes_existing_position=closes_existing, direction=dir_label, is_maker_assumed=is_maker)

    def _bsimpl_0_DetailedTemplateAgent__estimate_plan_for_book(self, state: MarketSimulationStateUpdate, book_id: int, size: float, layer: str, direction: Literal['UP', 'DOWN', 'SYMMETRIC']='SYMMETRIC') -> RealizedPnLEstimate | None:
        cfg = state.config
        book = state.books.get(book_id)
        if not cfg or not book or (not book.bids) or (not book.asks):
            return None
        vol_dec = cfg.volumeDecimals
        price_dec = cfg.priceDecimals
        qty = self._round_order_size(size, vol_dec)
        best_bid = round(book.bids[0].price, price_dec)
        best_ask = round(book.asks[0].price, price_dec)
        is_maker = self._prefer_maker(book_id)
        trip_dir: Literal['UP', 'DOWN', 'SYMMETRIC'] = direction
        if direction == 'SYMMETRIC':
            trip_dir = 'SYMMETRIC'
        estimate = self.estimate_round_trip_pnl(book_id, best_bid, best_ask, qty, is_maker=is_maker, direction=trip_dir, timestamp=state.timestamp)
        estimate.layer = layer
        if direction in ('UP', 'DOWN'):
            estimate.direction = direction
        return estimate

    def _bsimpl_0_DetailedTemplateAgent__log_predict_pnl(self, estimates: list[RealizedPnLEstimate]) -> None:
        if not estimates:
            return
        rows = [{'book': e.book_id, 'layer': e.layer, 'qty': e.quantity, 'bid': e.buy_price, 'ask': e.sell_price, 'leg1': round(e.leg_first_pnl, 6), 'leg2': round(e.leg_second_pnl, 6), 'expected_pnl': round(e.expected_realized_pnl, 6), 'closes_existing': e.closes_existing_position, 'dir': e.direction, 'maker': e.is_maker_assumed} for e in estimates]
        total = sum((e.expected_realized_pnl for e in estimates))
        bt.logging.info(f'[PREDICT_PNL] plans={len(estimates)} total_expected={round(total, 6)} detail={json.dumps(rows)}')

    def _bsimpl_0_DetailedTemplateAgent__update_momentum(self, book_id: int, timestamp: int, mid: float | None) -> tuple[float | None, float | None]:
        """Rolling log-return and percent change over momentum_window_ticks."""
        if mid is None or mid <= 0:
            return (None, None)
        hist = self._mid_history[book_id]
        if not hist or hist[-1][0] != timestamp:
            hist.append((timestamp, mid))
            if len(hist) > self.momentum_window_ticks:
                del hist[:-self.momentum_window_ticks]
        if len(hist) < 2:
            return (None, None)
        _, old_mid = hist[0]
        if old_mid <= 0:
            return (None, None)
        log_return = math.log(mid / old_mid)
        pct_momentum = (mid - old_mid) / old_mid
        return (log_return, pct_momentum)

    def _invalidate_pnl_caches(self) -> None:
        """Invalidate derived PnL/Kappa caches after a history mutation."""
        self._pnl_history_version = int(getattr(self, '_pnl_history_version', 0)) + 1
        self._pnl_lookback_cache_key = None
        self._pnl_obs_cache = {}
        self._pnl_sum_cache = {}
        self._local_kappa_cache_key = None
        self._local_kappa_cache = None

    def _prepare_pnl_lookback_cache(self, current_ts: int) -> None:
        """Aggregate lookback PnL once per history-version/timestamp.

        The original implementation scanned every realized-PnL bucket once
        for observation count and again for realized PnL for each book. With
        128 books this is O(books * history). This cache preserves the same
        bucket semantics while reducing profile construction to one history
        pass plus O(1) lookups per book.
        """
        key = (int(getattr(self, '_pnl_history_version', 0)), int(current_ts))
        if self._pnl_lookback_cache_key == key:
            return
        threshold = int(current_ts) - int(self.pnl_lookback_ns)
        obs: dict[int, int] = defaultdict(int)
        totals: dict[int, float] = defaultdict(float)
        for ts, books in self.realized_pnl_history.items():
            if ts < threshold:
                continue
            for book_id, pnl in books.items():
                value = float(pnl)
                totals[book_id] += value
                if value != 0.0:
                    obs[book_id] += 1
        self._pnl_obs_cache = dict(obs)
        self._pnl_sum_cache = dict(totals)
        self._pnl_lookback_cache_key = key

    def _bsimpl_0_DetailedTemplateAgent__prune_pnl_history(self, current_timestamp: int) -> None:
        threshold = current_timestamp - self.pnl_lookback_ns
        self.realized_pnl_history = {ts: books for ts, books in self.realized_pnl_history.items() if ts >= threshold}

    def _bsimpl_0_DetailedTemplateAgent__summarize_pnl_history(self, max_entries: int=8) -> dict:
        """Recent scoring buckets + cumulative totals (validator-shaped)."""
        recent_ts = sorted(self.realized_pnl_history.keys())
        tail = recent_ts[-max_entries:]
        recent = {ts: dict(self.realized_pnl_history[ts]) for ts in tail}
        return {'recent_buckets': recent, 'bucket_count': len(self.realized_pnl_history), 'total_by_book': dict(self.total_realized_pnl_by_book), 'total_all_books': round(sum(self.total_realized_pnl_by_book.values()), 4)}

    def _bsimpl_0_DetailedTemplateAgent__realized_pnl_sequences_per_book(self, book_count: int) -> dict[int, list[tuple[int, float]]]:
        """Per-book time series of realized PnL at each scoring timestamp."""
        sequences: dict[int, list[tuple[int, float]]] = {b: [] for b in range(book_count)}
        for ts in sorted(self.realized_pnl_history.keys()):
            for book_id, pnl in self.realized_pnl_history[ts].items():
                if book_id in sequences:
                    sequences[book_id].append((ts, pnl))
        return sequences

    def _bsimpl_0_DetailedTemplateAgent__truncate_pnl_sequences(self, sequences: dict[int, list[tuple[int, float]]], max_entries: int) -> dict[int, list[list[float | int]]]:
        """Trim sequences for logging; format [[ts, pnl], ...] per book."""
        out: dict[int, list[list[float | int]]] = {}
        for book_id, seq in sequences.items():
            if not seq:
                continue
            tail = seq[-max_entries:]
            out[book_id] = [[ts, round(pnl, 6)] for ts, pnl in tail]
        return out

    def _bsimpl_0_DetailedTemplateAgent__book_mid(self, book: Book) -> float | None:
        if not book.bids or not book.asks:
            return None
        return (book.bids[0].price + book.asks[0].price) / 2.0

    def _bsimpl_0_DetailedTemplateAgent__normalize_momentum(self, log_return: float | None) -> float:
        """Map log-return to [-1, 1] for score term momentum_m."""
        if log_return is None:
            return 0.0
        scale = max(self.momentum_scale, 1e-12)
        return max(-1.0, min(1.0, log_return / scale))

    def _bsimpl_0_DetailedTemplateAgent__compute_flow_f(self, book: Book) -> float:
        """LOB imbalance in [-1, 1]: positive = bid-heavy (UP bias)."""
        if not book.bids and (not book.asks):
            return 0.0
        depth = self.flow_depth
        bid_n = min(depth, len(book.bids)) if book.bids else 0
        ask_n = min(depth, len(book.asks)) if book.asks else 0
        bid_vol = sum((book.bids[i].quantity for i in range(bid_n)))
        ask_vol = sum((book.asks[i].quantity for i in range(ask_n)))
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        imbalance = (bid_vol - ask_vol) / total
        return max(-1.0, min(1.0, imbalance))

    def _bsimpl_0_DetailedTemplateAgent__compute_trade_t(self, book: Book) -> tuple[float, float]:
        """
        Net trade-initiated flow in [-1, 1] from events this interval.
        side 0 = BUY-initiated, side 1 = SELL-initiated.
        """
        buy_vol = 0.0
        sell_vol = 0.0
        events = book.events or []
        for event in events:
            etype = getattr(event, 'type', None)
            if etype not in ('t', 'EVENT_TRADE', 'ET'):
                continue
            qty = float(getattr(event, 'quantity', 0.0))
            side = getattr(event, 'side', None)
            if side == 0:
                buy_vol += qty
            elif side == 1:
                sell_vol += qty
        total = buy_vol + sell_vol
        if total <= 0:
            return (0.0, 0.0)
        imbalance = (buy_vol - sell_vol) / total
        return (max(-1.0, min(1.0, imbalance)), imbalance)

    def _bsimpl_0_DetailedTemplateAgent_predict_direction(self, book_id: int, book: Book, timestamp: int) -> DirectionForecast:
        """
        Predict short-horizon direction using weighted score:

            score = w_m * momentum_m + w_f * flow_f + w_t * trade_t

        - momentum_m: normalized log-return over momentum_window_ticks
        - flow_f: order-book imbalance (bid vs ask depth)
        - trade_t: net buy vs sell initiated volume in book.events this tick

        Returns UP if score > threshold, DOWN if score < -threshold, else HOLD.
        """
        mid = self._book_mid(book)
        log_return, _ = self._update_momentum(book_id, timestamp, mid)
        momentum_m = self._normalize_momentum(log_return)
        flow_f = self._compute_flow_f(book)
        trade_t, trade_imbalance = self._compute_trade_t(book)
        imbalance = flow_f
        score = self.w_m * momentum_m + self.w_f * flow_f + self.w_t * trade_t
        if score > self.direction_threshold:
            direction: Literal['UP', 'DOWN', 'HOLD'] = 'UP'
        elif score < -self.direction_threshold:
            direction = 'DOWN'
        else:
            direction = 'HOLD'
        return DirectionForecast(book_id=book_id, direction=direction, score=score, momentum_m=momentum_m, flow_f=flow_f, trade_t=trade_t, log_return=log_return, imbalance=imbalance, trade_imbalance=trade_imbalance)

    def _bsimpl_0_DetailedTemplateAgent__predict_all_books(self, state: MarketSimulationStateUpdate) -> dict[int, DirectionForecast]:
        if not state.books:
            return {}
        predictions = {}
        for book_id, book in state.books.items():
            predictions[book_id] = self.predict_direction(book_id, book, state.timestamp)
        self._last_predictions = predictions
        return predictions

    def _bsimpl_0_DetailedTemplateAgent__log_direction_predictions(self, predictions: dict[int, DirectionForecast], max_books: int=8) -> None:
        if not predictions:
            return
        sample = sorted(predictions.values(), key=lambda p: p.book_id)[:max_books]
        rows = [{'book': p.book_id, 'dir': p.direction, 'score': round(p.score, 4), 'm': round(p.momentum_m, 4), 'f': round(p.flow_f, 4), 't': round(p.trade_t, 4)} for p in sample]
        bt.logging.info(f'[PREDICT] w_m={self.w_m} w_f={self.w_f} w_t={self.w_t} threshold={self.direction_threshold} sample={json.dumps(rows)}')
        if len(predictions) > max_books:
            bt.logging.info(f'[PREDICT] … {len(predictions) - max_books} more books omitted')

    def _bsimpl_0_DetailedTemplateAgent__pnl_observation_count(self, book_id: int, current_ts: int) -> int:
        """Non-zero realized-PnL buckets within the scoring lookback."""
        self._prepare_pnl_lookback_cache(current_ts)
        return int(self._pnl_obs_cache.get(book_id, 0))

    def _bsimpl_0_DetailedTemplateAgent__realized_pnl_lookback(self, book_id: int, current_ts: int) -> float:
        self._prepare_pnl_lookback_cache(current_ts)
        return float(self._pnl_sum_cache.get(book_id, 0.0))

    def _bsimpl_0_DetailedTemplateAgent__book_volatility(self, book_id: int) -> float:
        """Rolling std of log-returns from mid history."""
        hist = self._mid_history.get(book_id, [])
        if len(hist) < 3:
            return 0.0
        mids = [m for _, m in hist if m > 0]
        if len(mids) < 3:
            return 0.0
        log_rets = [math.log(mids[i] / mids[i - 1]) for i in range(1, len(mids)) if mids[i - 1] > 0]
        if len(log_rets) < 2:
            return 0.0
        mean = sum(log_rets) / len(log_rets)
        var = sum(((r - mean) ** 2 for r in log_rets)) / len(log_rets)
        return math.sqrt(var)

    def _bsimpl_0_DetailedTemplateAgent__spread_bps(self, spread: float | None, mid: float | None) -> float | None:
        if spread is None or mid is None or mid <= 0:
            return None
        return spread / mid * 10000.0

    def _bsimpl_0_DetailedTemplateAgent__compute_alpha_rank(self, raw_kappa: float | None, predict_score: float, spread: float | None, mid: float | None, volatility: float) -> float:
        norm_kappa = 0.0
        if raw_kappa is not None:
            span = max(self.kappa_norm_max - self.kappa_norm_min, 1e-12)
            norm_kappa = max(0.0, min(1.0, (raw_kappa - self.kappa_norm_min) / span))
        spread_penalty = 0.0
        if spread is not None and mid is not None and (mid > 0):
            spread_penalty = min(1.0, spread / mid)
        vol_scale = max(self.profile_vol_scale, 1e-12)
        vol_penalty = min(1.0, volatility / vol_scale)
        return self.profile_w_k * norm_kappa + self.profile_w_p * abs(predict_score) - self.profile_w_s * spread_penalty - self.profile_w_v * vol_penalty

    def _bsimpl_0_DetailedTemplateAgent__assign_book_tier(self, raw_kappa: float | None, pnl_obs_count: int, realized_pnl: float, traded_volume: float) -> BookTier:
        if pnl_obs_count < self.kappa_min_observations:
            return 'INACTIVE'
        is_red = realized_pnl < 0.0 or (raw_kappa is not None and raw_kappa < self.red_kappa_threshold)
        if is_red:
            return 'RED'
        is_green = raw_kappa is not None and raw_kappa >= self.green_kappa_threshold and (realized_pnl >= 0.0)
        if is_green:
            return 'GREEN'
        return 'YELLOW'

    def _bsimpl_0_DetailedTemplateAgent_build_book_profile(self, book_id: int, book: Book, state: MarketSimulationStateUpdate, prediction: DirectionForecast | None, raw_kappa: float | None) -> BookProfile:
        mid = self._book_mid(book)
        spread = None
        if book.bids and book.asks:
            spread = book.asks[0].price - book.bids[0].price
        # predict_direction() already computed top-of-book flow for this tick.
        # Reuse it rather than traversing book depth a second time.
        imbalance = float(prediction.flow_f) if prediction is not None else self._compute_flow_f(book)
        trade_rate = float(sum((1 for e in book.events or [] if getattr(e, 'type', None) in ('t', 'EVENT_TRADE', 'ET'))))
        volatility = self._book_volatility(book_id)
        pnl_obs_count = self._pnl_observation_count(book_id, state.timestamp)
        realized_pnl = self._realized_pnl_lookback(book_id, state.timestamp)
        traded_volume = 0.0
        if self.accounts and book_id in self.accounts:
            vol = self.accounts[book_id].traded_volume
            traded_volume = float(vol) if vol is not None else 0.0
        predict_score = prediction.score if prediction else 0.0
        predict_direction = prediction.direction if prediction else 'HOLD'
        tier = self._assign_book_tier(raw_kappa, pnl_obs_count, realized_pnl, traded_volume)
        alpha_rank = self._compute_alpha_rank(raw_kappa, predict_score, spread, mid, volatility)
        return BookProfile(book_id=book_id, spread=spread, mid=mid, spread_bps=self._spread_bps(spread, mid), trade_rate=trade_rate, volatility=volatility, imbalance=imbalance, raw_kappa=raw_kappa, realized_pnl=realized_pnl, pnl_obs_count=pnl_obs_count, traded_volume=traded_volume, predict_score=predict_score, predict_direction=predict_direction, tier=tier, alpha_rank=alpha_rank)

    def _bsimpl_0_DetailedTemplateAgent_build_all_book_profiles(self, state: MarketSimulationStateUpdate, predictions: dict[int, DirectionForecast], kappa_values: dict | None=None) -> list[BookProfile]:
        if not state.books:
            return []
        # One PnL-history aggregation serves every book profile this tick.
        self._prepare_pnl_lookback_cache(state.timestamp)
        if kappa_values is None:
            kappa_values = self._compute_local_kappa(state)
        self._last_kappa = kappa_values
        raw_by_book = (kappa_values or {}).get('books', {})
        profiles = []
        for book_id, book in sorted(state.books.items()):
            raw_k = raw_by_book.get(book_id)
            if raw_k is None and book_id in raw_by_book:
                raw_k = raw_by_book[book_id]
            profiles.append(self.build_book_profile(book_id, book, state, predictions.get(book_id), raw_k))
        self._last_profiles = profiles
        return profiles

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_rank_books_for_trading(profiles: list[BookProfile], spread_alpha_max: float=0.002, max_maintenance: int | None=None) -> BookSelection:
        """
        Partition books for scoring-aware trading.

        - alpha_books: GREEN tier, sorted by alpha_rank (best first)
        - maintenance_books: INACTIVE — need round-trip activity for Kappa
        - avoid_books: RED tier — poor Kappa / negative PnL while active

        YELLOW books are omitted from all three lists (trade at normal discretion).
        """
        tier_counts: dict[str, int] = defaultdict(int)
        for p in profiles:
            tier_counts[p.tier] += 1
        alpha_candidates = [p for p in profiles if p.tier == 'GREEN' and p.spread is not None and (p.mid is not None) and (p.mid > 0) and (p.spread / p.mid <= spread_alpha_max) and (p.predict_direction != 'HOLD')]
        alpha_books = sorted(alpha_candidates, key=lambda p: p.alpha_rank, reverse=True)
        alpha_ids = [p.book_id for p in alpha_books]
        maintenance_candidates = [p for p in profiles if p.tier == 'INACTIVE']
        maintenance_candidates.sort(key=lambda p: (p.pnl_obs_count, p.traded_volume))
        if max_maintenance is not None:
            maintenance_candidates = maintenance_candidates[:max_maintenance]
        maintenance_ids = [p.book_id for p in maintenance_candidates]
        avoid_ids = [p.book_id for p in profiles if p.tier == 'RED']
        return BookSelection(alpha_books=alpha_ids, maintenance_books=maintenance_ids, avoid_books=avoid_ids, tier_counts=dict(tier_counts), profiles=profiles)

    def _bsimpl_0_DetailedTemplateAgent_select_books_for_trading(self, state: MarketSimulationStateUpdate, predictions: dict[int, DirectionForecast]) -> BookSelection:
        """Build profiles and return scoring-aware book selection for this tick."""
        profiles = self.build_all_book_profiles(state, predictions)
        book_count = len(profiles)
        max_inactive = int(self.max_inactive_books_ratio * book_count) if book_count else 0
        selection = self.rank_books_for_trading(profiles, spread_alpha_max=self.spread_alpha_max, max_maintenance=max(max_inactive, 1) if book_count else None)
        self._last_selection = selection
        return selection

    def _bsimpl_0_DetailedTemplateAgent__log_book_profile_selection(self, selection: BookSelection) -> None:
        tier_counts = selection.tier_counts
        bt.logging.info(f'[BOOK_PROFILE] tier_counts={json.dumps(tier_counts)} max_inactive_allowed={int(self.max_inactive_books_ratio * len(selection.profiles))}')
        bt.logging.info(f'[BOOK_PROFILE] alpha={selection.alpha_books} maintain={selection.maintenance_books} avoid={selection.avoid_books}')
        sample = sorted(selection.profiles, key=lambda p: p.alpha_rank, reverse=True)[:8]
        rows = [{'book': p.book_id, 'tier': p.tier, 'rank': round(p.alpha_rank, 4), 'kappa': round(p.raw_kappa, 4) if p.raw_kappa is not None else None, 'pnl': round(p.realized_pnl, 4), 'obs': p.pnl_obs_count, 'dir': p.predict_direction, 'spread_bps': round(p.spread_bps, 2) if p.spread_bps is not None else None} for p in sample]
        bt.logging.info(f'[BOOK_PROFILE] top_by_rank={json.dumps(rows)}')

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_classify_market_regime(profiles: list[BookProfile], predictions: dict[int, DirectionForecast] | None=None, tier_counts: dict[str, int] | None=None, max_inactive_books_ratio: float=0.375, hold_frac_threshold: float=0.7, trend_frac_threshold: float=0.5, dispersed_frac_threshold: float=0.25, stressed_spread_bps: float=5.0, chop_vol_threshold: float=0.005, active_trade_rate: float=2.0) -> MarketRegime:
        """
        Classify cross-book market regime from per-book profiles.

        Modes (priority order):
            STRESSED → TRENDING_UP/DOWN → DISPERSED → CHOP → QUIET → BROAD_LIQUID → MIXED
        """
        n = len(profiles)
        if n == 0:
            return MarketRegime(mode='MIXED', hold_frac=0.0, up_frac=0.0, down_frac=0.0, mean_score=0.0, mean_abs_score=0.0, mean_volatility=0.0, mean_trade_rate=0.0, mean_spread_bps=None, mean_imbalance=0.0, mean_log_return=None, return_dispersion=None, direction_dispersion=0.0, tier_counts={}, inactive_frac=0.0, red_frac=0.0, green_frac=0.0, scoring_overlay=None, confidence=0.0, book_count=0)
        hold_n = sum((1 for p in profiles if p.predict_direction == 'HOLD'))
        up_n = sum((1 for p in profiles if p.predict_direction == 'UP'))
        down_n = sum((1 for p in profiles if p.predict_direction == 'DOWN'))
        hold_frac = hold_n / n
        up_frac = up_n / n
        down_frac = down_n / n
        scores = [p.predict_score for p in profiles]
        mean_score = sum(scores) / n
        mean_abs_score = sum((abs(s) for s in scores)) / n
        direction_dispersion = math.sqrt(sum(((s - mean_score) ** 2 for s in scores)) / n) if n > 1 else 0.0
        mean_volatility = sum((p.volatility for p in profiles)) / n
        mean_trade_rate = sum((p.trade_rate for p in profiles)) / n
        mean_imbalance = sum((p.imbalance for p in profiles)) / n
        spread_samples = [p.spread_bps for p in profiles if p.spread_bps is not None]
        mean_spread_bps = sum(spread_samples) / len(spread_samples) if spread_samples else None
        log_returns: list[float] = []
        if predictions:
            for pred in predictions.values():
                if pred.log_return is not None:
                    log_returns.append(pred.log_return)
        mean_log_return: float | None = None
        return_dispersion: float | None = None
        if log_returns:
            mean_log_return = sum(log_returns) / len(log_returns)
            if len(log_returns) > 1:
                m = mean_log_return
                return_dispersion = math.sqrt(sum(((r - m) ** 2 for r in log_returns)) / len(log_returns))
            else:
                return_dispersion = 0.0
        if tier_counts is None:
            tier_counts = defaultdict(int)
            for p in profiles:
                tier_counts[p.tier] += 1
            tier_counts = dict(tier_counts)
        inactive_frac = tier_counts.get('INACTIVE', 0) / n
        red_frac = tier_counts.get('RED', 0) / n
        green_frac = tier_counts.get('GREEN', 0) / n
        max_inactive = int(max_inactive_books_ratio * n)
        inactive_count = tier_counts.get('INACTIVE', 0)
        red_count = tier_counts.get('RED', 0)
        green_count = tier_counts.get('GREEN', 0)
        scoring_overlay: ScoringOverlay | None = None
        if inactive_count >= max(max_inactive - 1, 1):
            scoring_overlay = 'SCORING_PRESSURE'
        elif red_frac > 0.25:
            scoring_overlay = 'DAMAGE_CONTROL'
        elif green_frac > 0.5 and red_frac < 0.1:
            scoring_overlay = 'SCORING_COMFORT'
        mode: MarketRegimeMode = 'MIXED'
        confidence = 0.3
        if mean_spread_bps is not None and mean_spread_bps >= stressed_spread_bps:
            mode = 'STRESSED'
            confidence = min(1.0, mean_spread_bps / stressed_spread_bps)
        elif up_frac >= trend_frac_threshold and mean_score > 0:
            mode = 'TRENDING_UP'
            confidence = min(1.0, up_frac + abs(mean_score))
        elif down_frac >= trend_frac_threshold and mean_score < 0:
            mode = 'TRENDING_DOWN'
            confidence = min(1.0, down_frac + abs(mean_score))
        elif up_frac >= dispersed_frac_threshold and down_frac >= dispersed_frac_threshold:
            mode = 'DISPERSED'
            confidence = min(1.0, min(up_frac, down_frac) / dispersed_frac_threshold)
        elif hold_frac >= hold_frac_threshold and mean_volatility >= chop_vol_threshold:
            mode = 'CHOP'
            confidence = min(1.0, hold_frac)
        elif hold_frac >= hold_frac_threshold:
            mode = 'QUIET'
            confidence = min(1.0, hold_frac)
        elif mean_trade_rate >= active_trade_rate:
            mode = 'BROAD_LIQUID'
            confidence = min(1.0, mean_trade_rate / (active_trade_rate * 2))
        return MarketRegime(mode=mode, hold_frac=hold_frac, up_frac=up_frac, down_frac=down_frac, mean_score=mean_score, mean_abs_score=mean_abs_score, mean_volatility=mean_volatility, mean_trade_rate=mean_trade_rate, mean_spread_bps=mean_spread_bps, mean_imbalance=mean_imbalance, mean_log_return=mean_log_return, return_dispersion=return_dispersion, direction_dispersion=direction_dispersion, tier_counts=tier_counts, inactive_frac=inactive_frac, red_frac=red_frac, green_frac=green_frac, scoring_overlay=scoring_overlay, confidence=confidence, book_count=n)

    def _bsimpl_0_DetailedTemplateAgent_classify_market_regime_from_profiles(self, profiles: list[BookProfile], predictions: dict[int, DirectionForecast], selection: BookSelection | None=None) -> MarketRegime:
        """Classify regime using agent config thresholds; stores result on self."""
        tier_counts = selection.tier_counts if selection else None
        regime = self.classify_market_regime(profiles, predictions=predictions, tier_counts=tier_counts, max_inactive_books_ratio=self.max_inactive_books_ratio, hold_frac_threshold=self.regime_hold_frac_threshold, trend_frac_threshold=self.regime_trend_frac_threshold, dispersed_frac_threshold=self.regime_dispersed_frac_threshold, stressed_spread_bps=self.regime_stressed_spread_bps, chop_vol_threshold=self.regime_chop_vol_threshold, active_trade_rate=self.regime_active_trade_rate)
        self._last_regime = regime
        return regime

    def _bsimpl_0_DetailedTemplateAgent__log_market_regime(self, regime: MarketRegime) -> None:
        payload = {'mode': regime.mode, 'confidence': round(regime.confidence, 4), 'hold_frac': round(regime.hold_frac, 4), 'up_frac': round(regime.up_frac, 4), 'down_frac': round(regime.down_frac, 4), 'mean_score': round(regime.mean_score, 4), 'mean_abs_score': round(regime.mean_abs_score, 4), 'mean_vol': round(regime.mean_volatility, 6), 'mean_trade_rate': round(regime.mean_trade_rate, 2), 'mean_spread_bps': round(regime.mean_spread_bps, 2) if regime.mean_spread_bps is not None else None, 'mean_imbalance': round(regime.mean_imbalance, 4), 'mean_log_return': round(regime.mean_log_return, 8) if regime.mean_log_return is not None else None, 'return_dispersion': round(regime.return_dispersion, 8) if regime.return_dispersion is not None else None, 'direction_dispersion': round(regime.direction_dispersion, 4), 'tier_counts': regime.tier_counts, 'inactive_frac': round(regime.inactive_frac, 4), 'red_frac': round(regime.red_frac, 4), 'green_frac': round(regime.green_frac, 4), 'scoring_overlay': regime.scoring_overlay, 'books': regime.book_count}
        bt.logging.info(f'[REGIME] {json.dumps(payload)}')

    def _bsimpl_0_DetailedTemplateAgent__compute_local_kappa(self, state: MarketSimulationStateUpdate) -> dict | None:
        """Run validator kappa_3(), reusing the result until PnL history changes."""
        cfg = state.config
        if not cfg or not self.realized_pnl_history:
            return None
        key = (
            int(getattr(self, '_pnl_history_version', 0)),
            float(self.kappa_tau),
            int(self.pnl_lookback_ns),
            float(self.kappa_norm_min),
            float(self.kappa_norm_max),
            int(self.kappa_min_lookback),
            int(self.kappa_min_observations),
            int(cfg.grace_period),
            int(cfg.book_count),
        )
        if self._local_kappa_cache_key == key:
            return self._local_kappa_cache
        pnl_values = {ts: dict(books) for ts, books in self.realized_pnl_history.items()}
        result = kappa_3(
            self.uid, pnl_values, self.kappa_tau, self.pnl_lookback_ns,
            self.kappa_norm_min, self.kappa_norm_max, self.kappa_min_lookback,
            self.kappa_min_observations, cfg.grace_period, [], cfg.book_count, cache=None
        )
        self._local_kappa_cache_key = key
        self._local_kappa_cache = result
        return result

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_parse_book(book_id: int, book: Book, detailed_depth: int) -> BookSnapshot:
        best_bid = book.bids[0].price if book.bids else None
        best_ask = book.asks[0].price if book.asks else None
        spread = best_ask - best_bid if best_bid is not None and best_ask is not None else None
        mid = (best_bid + best_ask) / 2 if spread is not None else None
        events = book.events or []
        trade_count = sum((1 for e in events if getattr(e, 'type', None) in ('t', 'EVENT_TRADE', 'ET')))
        last_price = None
        last_qty = None
        trades = book.trades
        if trades:
            lt = trades[max(trades)]
            last_price = lt.price
            last_qty = lt.quantity
        return BookSnapshot(book_id=book_id, best_bid=best_bid, best_ask=best_ask, spread=spread, mid=mid, bid_levels=len(book.bids), ask_levels=len(book.asks), event_count=len(events), trade_count=trade_count, last_trade_price=last_price, last_trade_qty=last_qty, log_return=None, pct_momentum=None)

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_parse_account(book_id: int, account: Account) -> AccountSnapshot:
        fees = account.fees
        return AccountSnapshot(book_id=book_id, base_total=account.base_balance.total, base_free=account.base_balance.free, quote_total=account.quote_balance.total, quote_free=account.quote_balance.free, base_loan=account.base_loan, quote_loan=account.quote_loan, open_orders=len(account.orders), open_loans=len(account.loans), traded_volume=account.traded_volume, maker_fee_rate=fees.maker_fee_rate if fees else None, taker_fee_rate=fees.taker_fee_rate if fees else None)

    def _bsimpl_0_DetailedTemplateAgent_parse_notices(self, state: MarketSimulationStateUpdate) -> tuple[int, dict[str, int]]:
        events = state.notices.get(self.uid, []) if state.notices else []
        counts: dict[str, int] = {}
        for ev in events:
            t = getattr(ev, 'type', type(ev).__name__)
            counts[t] = counts.get(t, 0) + 1
        return (len(events), counts)

    def _bsimpl_0_DetailedTemplateAgent_parse_state(self, state: MarketSimulationStateUpdate) -> StateSummary:
        """
        Build a structured INPUT summary from MarketSimulationStateUpdate.

        Top-level fields on `state`:
            version      — validator taos package version (int | None)
            timestamp    — simulation time in nanoseconds since sim start
            config       — MarketSimulationConfig (decimals, wealth, book_count, …)
            books        — dict[book_id, Book]  environment / order books
            accounts     — dict[uid, dict[book_id, Account]]  your balances & orders
            notices      — dict[uid, list[Event]]  fills, rejects, sim start/end, …
            dendrite     — bittensor metadata (validator hotkey, axon, …)
            compressed   — raw compressed payload if lazy_load deferred parsing
        """
        cfg = state.config
        notice_count, notice_types = self.parse_notices(state)
        book_snaps: list[BookSnapshot] = []
        if state.books:
            depth = cfg.detailed_book_levels if cfg else 5
            for book_id, book in sorted(state.books.items()):
                snap = self.parse_book(book_id, book, depth)
                log_ret, pct = self._update_momentum(book_id, state.timestamp, snap.mid)
                snap.log_return = log_ret
                snap.pct_momentum = pct
                book_snaps.append(snap)
        account_snaps: list[AccountSnapshot] = []
        uid_accounts = state.accounts.get(self.uid, {}) if state.accounts else {}
        for book_id in sorted(uid_accounts.keys()):
            account_snaps.append(self.parse_account(book_id, uid_accounts[book_id]))
        return StateSummary(simulation_timestamp_ns=state.timestamp, simulation_time_human=duration_from_timestamp(state.timestamp), validator_hotkey=state.dendrite.hotkey, taos_version=state.version, book_count=cfg.book_count if cfg else len(state.books or {}), publish_interval_ns=cfg.publish_interval if cfg else 0, miner_wealth=cfg.miner_wealth if cfg else 0.0, grace_period_ns=cfg.grace_period if cfg else 0, notices_count=notice_count, notice_types=notice_types, books=book_snaps, accounts=account_snaps)

    @staticmethod
    def _bsimpl_0_DetailedTemplateAgent_parse_response(response: FinanceAgentResponse) -> ResponseSummary:
        """
        Summarize FinanceAgentResponse.instructions for logging / debugging.

        Each instruction is one of:
            PLACE_ORDER_MARKET  — PlaceMarketOrderInstruction
            PLACE_ORDER_LIMIT   — PlaceLimitOrderInstruction
            CANCEL_ORDERS       — CancelOrdersInstruction (wraps cancel list)
            CLOSE_POSITIONS     — ClosePositionsInstruction (margin close)
            RESET_AGENT         — ResetAgentsInstruction (rare)
        """
        by_type: dict[str, int] = {}
        lines: list[str] = []
        for instr in response.instructions:
            itype = instr.type
            by_type[itype] = by_type.get(itype, 0) + 1
            lines.append(str(instr))
        return ResponseSummary(instruction_count=len(response.instructions), by_type=by_type, lines=lines)

    def _bsimpl_0_DetailedTemplateAgent_build_demo_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int=0) -> None:
        """
        Demonstrates every major OUTPUT pattern. Only used when enable_trading=True.

        In production, call only the methods you need on `response`:
            response.market_order(...)
            response.limit_order(...)
            response.cancel_order(...)
            response.cancel_orders(...)
            response.close_position(...)
            response.close_positions(...)
        """
        cfg = state.config
        book = state.books[book_id]
        account = self.accounts[book_id]
        vol_dec = cfg.volumeDecimals
        price_dec = cfg.priceDecimals
        size = round(self.demo_order_size, vol_dec)
        best_bid = book.bids[0].price if book.bids else cfg.init_price * 0.99
        best_ask = book.asks[0].price if book.asks else cfg.init_price * 1.01
        buy_price = round(best_bid, price_dec)
        sell_price = round(best_ask, price_dec)
        if account.quote_balance.free >= buy_price * size:
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=size, price=buy_price, clientOrderId=1001, stp=STP.CANCEL_OLDEST, postOnly=True, timeInForce=TimeInForce.GTC, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
        if account.base_balance.free >= size:
            response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=size, price=sell_price, clientOrderId=1002, timeInForce=TimeInForce.GTT, expiryPeriod=cfg.publish_interval, delay=0)
        if account.orders:
            response.cancel_order(book_id=book_id, order_id=account.orders[0].id, delay=0)
        if account.loans:
            loan_order_id = next(iter(account.loans.keys()))
            response.close_position(book_id=book_id, order_id=loan_order_id, delay=0)

    def _bsimpl_0_DetailedTemplateAgent__count_book_instructions(self, response: FinanceAgentResponse, book_id: int) -> int:
        return sum((1 for instr in response.instructions if getattr(instr, 'bookId', None) == book_id))

    def _bsimpl_0_DetailedTemplateAgent__round_order_size(self, size: float, vol_dec: int) -> float:
        return round(max(size, self.min_order_size), vol_dec)

    def _bsimpl_0_DetailedTemplateAgent__total_traded_volume(self) -> float:
        total = 0.0
        for account in self.accounts.values():
            vol = account.traded_volume
            if vol is not None:
                total += float(vol)
        return total

    def _bsimpl_0_DetailedTemplateAgent__volume_cap_quote(self, state: MarketSimulationStateUpdate) -> float:
        cfg = state.config
        if not cfg:
            return 0.0
        return self.capital_turnover_cap * cfg.miner_wealth

    def _bsimpl_0_DetailedTemplateAgent__volume_cap_remaining(self, state: MarketSimulationStateUpdate) -> float:
        return max(0.0, self._volume_cap_quote(state) - self._total_traded_volume())

    def _bsimpl_0_DetailedTemplateAgent__can_add_volume(self, state: MarketSimulationStateUpdate, quote_notional: float) -> bool:
        return quote_notional <= self._volume_cap_remaining(state)

    def _bsimpl_0_DetailedTemplateAgent__passes_fee_gate(self, book_id: int, aggressive: bool) -> bool:
        account = self.accounts.get(book_id)
        if not account or not account.fees:
            return True
        if aggressive and account.fees.taker_fee_rate > self.max_taker_fee_rate:
            return False
        return True

    def _bsimpl_0_DetailedTemplateAgent__prefer_maker(self, book_id: int) -> bool:
        account = self.accounts.get(book_id)
        if not account or not account.fees:
            return True
        return account.fees.maker_fee_rate <= account.fees.taker_fee_rate

    def _bsimpl_0_DetailedTemplateAgent__alpha_regime_allows(self, regime: MarketRegime) -> bool:
        if regime.mode in ('STRESSED', 'QUIET', 'CHOP'):
            return False
        if regime.mode in ('TRENDING_UP', 'TRENDING_DOWN', 'BROAD_LIQUID'):
            return True
        if regime.mode == 'DISPERSED':
            return True
        return regime.mode == 'MIXED' and regime.mean_abs_score > self.direction_threshold

    def _bsimpl_0_DetailedTemplateAgent__estimate_local_normalized_median(self) -> float | None:
        """Proxy median of normalized raw Kappas (validator uses activity-weighted values)."""
        if not self._last_kappa or not self._last_profiles:
            return None
        raw_by_book = self._last_kappa.get('books', {})
        span = max(self.kappa_norm_max - self.kappa_norm_min, 1e-12)
        norms: list[float] = []
        for profile in self._last_profiles:
            raw = raw_by_book.get(profile.book_id)
            if raw is None:
                continue
            norms.append(max(0.0, min(1.0, (raw - self.kappa_norm_min) / span)))
        if not norms:
            return None
        norms.sort()
        mid = len(norms) // 2
        if len(norms) % 2:
            return norms[mid]
        return (norms[mid - 1] + norms[mid]) / 2.0

    def _bsimpl_0_DetailedTemplateAgent__place_round_trip_limits(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, size: float, post_only: bool=True, expiry_period: int | None=None, client_id_base: int=0) -> int:
        """Place bid+ask limit pair for round-trip potential; returns instruction count."""
        cfg = state.config
        book = state.books.get(book_id)
        account = self.accounts.get(book_id)
        if not cfg or not book or (not account):
            return 0
        if not book.bids or not book.asks:
            return 0
        vol_dec = cfg.volumeDecimals
        price_dec = cfg.priceDecimals
        qty = self._round_order_size(size, vol_dec)
        best_bid = round(book.bids[0].price, price_dec)
        best_ask = round(book.asks[0].price, price_dec)
        mid = (best_bid + best_ask) / 2.0
        quote_notional = qty * mid * 2
        if not self._can_add_volume(state, quote_notional):
            return 0
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return 0
        placed = 0
        tif = TimeInForce.GTT if expiry_period else TimeInForce.GTC
        # Respect the caller's explicit post-only contract.  This helper is
        # used by maintenance/bootstrap quote pairs.  Falling back to taker
        # behavior here can create accidental inventory when the delayed order
        # reaches a moved book.
        use_post_only = bool(post_only)
        if account.quote_balance.free >= best_bid * qty:
            if self._count_book_instructions(response, book_id) < self.max_instructions_per_book:
                response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=qty, price=best_bid, clientOrderId=client_id_base + book_id * 10 + 1, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=tif, expiryPeriod=expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
        if account.base_balance.free >= qty:
            if self._count_book_instructions(response, book_id) < self.max_instructions_per_book:
                response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=qty, price=best_ask, clientOrderId=client_id_base + book_id * 10 + 2, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=tif, expiryPeriod=expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
        return placed

    def _bsimpl_0_DetailedTemplateAgent__place_directional_round_trip(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, direction: Literal['UP', 'DOWN'], size: float, client_id_base: int=50000) -> int:
        """
        Entry + exit limits aligned with predicted direction for round-trip discipline.
        UP: buy at bid (entry), sell at ask (exit). DOWN: sell then buy.
        """
        cfg = state.config
        book = state.books.get(book_id)
        account = self.accounts.get(book_id)
        if not cfg or not book or (not account):
            return 0
        if not book.bids or not book.asks:
            return 0
        vol_dec = cfg.volumeDecimals
        price_dec = cfg.priceDecimals
        qty = self._round_order_size(size, vol_dec)
        best_bid = round(book.bids[0].price, price_dec)
        best_ask = round(book.asks[0].price, price_dec)
        mid = (best_bid + best_ask) / 2.0
        quote_notional = qty * mid * 2
        if not self._can_add_volume(state, quote_notional):
            return 0
        if not self._passes_fee_gate(book_id, aggressive=False):
            return 0
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return 0
        expiry = cfg.publish_interval
        use_post_only = self._prefer_maker(book_id)
        placed = 0
        if direction == 'UP':
            if account.quote_balance.free >= best_bid * qty:
                response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=qty, price=best_bid, clientOrderId=client_id_base + book_id * 10 + 1, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=TimeInForce.GTC, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
            if placed > 0 and account.base_balance.free >= qty and (self._count_book_instructions(response, book_id) < self.max_instructions_per_book):
                response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=qty, price=best_ask, clientOrderId=client_id_base + book_id * 10 + 2, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
        else:
            if account.base_balance.free >= qty:
                response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=qty, price=best_ask, clientOrderId=client_id_base + book_id * 10 + 3, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=TimeInForce.GTC, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
            if placed > 0 and account.quote_balance.free >= best_bid * qty and (self._count_book_instructions(response, book_id) < self.max_instructions_per_book):
                response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=qty, price=best_bid, clientOrderId=client_id_base + book_id * 10 + 4, stp=STP.CANCEL_OLDEST, postOnly=use_post_only, timeInForce=TimeInForce.GTT, expiryPeriod=expiry, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
                placed += 1
        return placed

    def _bsimpl_0_DetailedTemplateAgent_build_kappa_strategy_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime) -> dict:
        """
        Three-layer Kappa strategy:
          1. Maintenance on INACTIVE books (round-trip limits for Kappa observations)
          2. Alpha on GREEN books when regime allows (directional round-trip limits)
          3. Skip RED avoid books (no directional aggression)
        """
        stats = {'maintenance_books': 0, 'maintenance_instructions': 0, 'alpha_books': 0, 'alpha_instructions': 0, 'skipped_avoid': len(selection.avoid_books), 'skipped_volume': 0, 'skipped_fee': 0, 'skipped_regime': 0}
        cfg = state.config
        if not cfg or not state.books:
            self._last_strategy_stats = stats
            return stats
        avoid_set = set(selection.avoid_books)
        profile_by_id = {p.book_id: p for p in selection.profiles}
        expiry = cfg.publish_interval
        maint_limit = self.max_maintenance_books_per_tick
        if regime.scoring_overlay == 'SCORING_PRESSURE':
            maint_limit = min(len(selection.maintenance_books), self.max_maintenance_books_per_tick * 2)
        maint_books = [b for b in selection.maintenance_books if b not in avoid_set and b in state.books][:maint_limit]
        pnl_estimates: list[RealizedPnLEstimate] = []
        for book_id in maint_books:
            est = self._estimate_plan_for_book(state, book_id, self.maintenance_order_size, 'maintenance', 'SYMMETRIC')
            if est:
                pnl_estimates.append(est)
        if not self._alpha_regime_allows(regime):
            pass
        else:
            alpha_books_preview = [b for b in selection.alpha_books if b not in avoid_set and b in state.books and (b in predictions)][:self.max_alpha_books_per_tick]
            for book_id in alpha_books_preview:
                pred = predictions[book_id]
                if pred.direction == 'HOLD':
                    continue
                profile = profile_by_id.get(book_id)
                scale = 1.0
                if profile:
                    scale = max(0.5, min(2.0, 1.0 + profile.alpha_rank))
                size = self.alpha_order_size * scale
                trip_dir: Literal['UP', 'DOWN'] = 'UP' if pred.direction == 'UP' else 'DOWN'
                est = self._estimate_plan_for_book(state, book_id, size, 'alpha', trip_dir)
                if est:
                    pnl_estimates.append(est)
        self._last_pnl_estimates = pnl_estimates
        if self.log_predict_pnl and pnl_estimates:
            self._log_predict_pnl(pnl_estimates)
        for book_id in maint_books:
            placed = self._place_round_trip_limits(response, state, book_id, self.maintenance_order_size, post_only=True, expiry_period=expiry, client_id_base=10000)
            if placed == 0 and (not self._can_add_volume(state, self.maintenance_order_size * (state.books[book_id].bids[0].price if state.books[book_id].bids else 300) * 2)):
                stats['skipped_volume'] += 1
            elif placed > 0:
                stats['maintenance_books'] += 1
                stats['maintenance_instructions'] += placed
        if not self._alpha_regime_allows(regime):
            stats['skipped_regime'] = len(selection.alpha_books)
        else:
            alpha_books = [b for b in selection.alpha_books if b not in avoid_set and b in state.books and (b in predictions)][:self.max_alpha_books_per_tick]
            for book_id in alpha_books:
                pred = predictions[book_id]
                if pred.direction == 'HOLD':
                    continue
                profile = profile_by_id.get(book_id)
                scale = 1.0
                if profile:
                    scale = max(0.5, min(2.0, 1.0 + profile.alpha_rank))
                size = self.alpha_order_size * scale
                placed = self._place_directional_round_trip(response, state, book_id, pred.direction, size, client_id_base=50000)
                if placed == 0 and (not self._passes_fee_gate(book_id, aggressive=False)):
                    stats['skipped_fee'] += 1
                elif placed == 0:
                    stats['skipped_volume'] += 1
                elif placed > 0:
                    stats['alpha_books'] += 1
                    stats['alpha_instructions'] += placed
        self._last_strategy_stats = stats
        return stats

    def _bsimpl_0_DetailedTemplateAgent__log_kappa_strategy_calibration(self, state: MarketSimulationStateUpdate, selection: BookSelection, regime: MarketRegime, stats: dict) -> None:
        raw_median = None
        if self._last_kappa:
            m = self._last_kappa.get('median')
            raw_median = round(m, 6) if m is not None else None
        norm_median_proxy = self._estimate_local_normalized_median()
        book_count = len(selection.profiles) or regime.book_count
        max_inactive = int(self.max_inactive_books_ratio * book_count)
        inactive_count = selection.tier_counts.get('INACTIVE', 0)
        payload = {'tick': self._tick, 'regime': regime.mode, 'scoring_overlay': regime.scoring_overlay, 'tier_counts': selection.tier_counts, 'inactive_count': inactive_count, 'max_inactive_allowed': max_inactive, 'raw_kappa_median': raw_median, 'norm_kappa_median_proxy': round(norm_median_proxy, 6) if norm_median_proxy is not None else None, 'volume_cap_remaining': round(self._volume_cap_remaining(state), 2), 'volume_cap_total': round(self._volume_cap_quote(state), 2), 'alpha_books': selection.alpha_books[:8], 'maintain_books': selection.maintenance_books[:8], 'avoid_books': selection.avoid_books[:8], 'strategy_stats': stats}
        bt.logging.info(f'[KAPPA_STRATEGY] {json.dumps(payload)}')

    def _bsimpl_0_DetailedTemplateAgent_respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        """
        INPUT:  state (MarketSimulationStateUpdate) — see parse_state() and module docstring.
        OUTPUT: FinanceAgentResponse with instructions[] sent back to validator → simulator.

        After update() ran, also available on self:
            self.accounts     — dict[book_id, Account] for this UID
            self.events        — list of notices for this UID this tick
            self.simulation_config — same as state.config
        """
        response = FinanceAgentResponse(agent_id=self.uid)
        self._tick += 1
        summary = self.parse_state(state)
        predictions = self._predict_all_books(state)
        selection = self.select_books_for_trading(state, predictions)
        regime = self.classify_market_regime_from_profiles(selection.profiles, predictions, selection)
        if self.verbose_log and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_input(summary)
        if self.log_direction and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_direction_predictions(predictions)
        if self.log_book_profile and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_book_profile_selection(selection)
        if self.log_regime and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_market_regime(regime)
        if self.log_momentum_pnl and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_momentum_and_pnl(summary, state)
        in_grace = state.timestamp < summary.grace_period_ns
        strategy_stats: dict = {}
        if state.books and (not in_grace):
            if self.enable_kappa_strategy:
                strategy_stats = self.build_kappa_strategy_instructions(response, state, selection, predictions, regime)
                if self.log_kappa_strategy and (self._tick == 1 or self._tick % self.log_every_n == 0):
                    self._log_kappa_strategy_calibration(state, selection, regime, strategy_stats)
            elif self.enable_trading:
                self.build_demo_instructions(response, state, book_id=0)
        elif state.books and in_grace and (self.enable_kappa_strategy or self.enable_trading):
            bt.logging.info(f'Grace period active (T={state.timestamp} < {summary.grace_period_ns}); no orders placed.')
        if self.verbose_log and response.instructions and (self._tick == 1 or self._tick % self.log_every_n == 0):
            self._log_output(self.parse_response(response))
        return response

    def _bsimpl_0_DetailedTemplateAgent_report(self, state: MarketSimulationStateUpdate, response: FinanceAgentResponse) -> None:
        """Optional: log OUTPUT size every tick when instructions were sent."""
        if response.instructions:
            bt.logging.debug(f'Tick {self._tick}: submitted {len(response.instructions)} instruction(s) at T={state.timestamp}')

    def _bsimpl_0_DetailedTemplateAgent__log_input(self, summary: StateSummary) -> None:
        header = {'tick': self._tick, 'uid': self.uid, 'validator': summary.validator_hotkey, 'T': summary.simulation_timestamp_ns, 'sim_time': summary.simulation_time_human, 'books': summary.book_count, 'notices': summary.notices_count, 'notice_types': summary.notice_types, 'miner_wealth': summary.miner_wealth, 'publish_interval_ns': summary.publish_interval_ns}
        bt.logging.info(f'[INPUT] {json.dumps(header)}')
        for snap in summary.books[:3]:
            mom = ''
            if snap.log_return is not None:
                mom = f' mom_log={snap.log_return:.6f} mom_pct={snap.pct_momentum:.4%}'
            bt.logging.info(f'[INPUT book {snap.book_id}] bid={snap.best_bid} ask={snap.best_ask} spread={snap.spread} events={snap.event_count} trades={snap.trade_count} last={snap.last_trade_price}@{snap.last_trade_qty}{mom}')
        if len(summary.books) > 3:
            bt.logging.info(f'[INPUT] … {len(summary.books) - 3} more books omitted')
        for snap in summary.accounts[:3]:
            bt.logging.info(f'[INPUT account book {snap.book_id}] BASE total={snap.base_total:.4f} free={snap.base_free:.4f} QUOTE total={snap.quote_total:.2f} free={snap.quote_free:.2f} orders={snap.open_orders} traded_vol={snap.traded_volume} fees m/t={snap.maker_fee_rate}/{snap.taker_fee_rate}')

    def _bsimpl_0_DetailedTemplateAgent__log_momentum_and_pnl(self, summary: StateSummary, state: MarketSimulationStateUpdate) -> None:
        timestamp = state.timestamp
        pnl_summary = self._summarize_pnl_history()
        mom_sample = [{'book': s.book_id, 'mid': s.mid, 'log_return': round(s.log_return, 8) if s.log_return is not None else None, 'pct': round(s.pct_momentum, 6) if s.pct_momentum is not None else None} for s in summary.books[:5]]
        bt.logging.info(f'[MOMENTUM] window_ticks={self.momentum_window_ticks} books_sample={json.dumps(mom_sample)}')
        bt.logging.info(f"[REALIZED_PNL] T={timestamp} buckets={pnl_summary['bucket_count']} total={pnl_summary['total_all_books']:.4f} by_book={json.dumps({k: round(v, 4) for k, v in pnl_summary['total_by_book'].items()})}")
        if pnl_summary['recent_buckets']:
            bt.logging.info(f"[REALIZED_PNL history] last buckets: {json.dumps(pnl_summary['recent_buckets'], default=str)}")
        tick_pnl = self.realized_pnl_history.get(timestamp, {})
        if tick_pnl:
            bt.logging.info(f'[REALIZED_PNL this tick] {json.dumps(tick_pnl)}')
        if self.log_kappa:
            self._log_kappa_and_sequences(state, timestamp)
        if self.pnl_log_file:
            self._append_pnl_csv(timestamp, summary.books[:10], pnl_summary)

    def _bsimpl_0_DetailedTemplateAgent__log_kappa_and_sequences(self, state: MarketSimulationStateUpdate, timestamp: int) -> None:
        cfg = state.config
        if not cfg:
            return
        book_count = cfg.book_count
        sequences = self._realized_pnl_sequences_per_book(book_count)
        truncated = self._truncate_pnl_sequences(sequences, self.pnl_sequence_max_entries)
        kappa_values = self._compute_local_kappa(state)
        self._last_kappa = kappa_values
        if kappa_values is None:
            span = 0
            if self.realized_pnl_history:
                ts_sorted = sorted(self.realized_pnl_history.keys())
                span = ts_sorted[-1] - ts_sorted[0]
            bt.logging.info(f'[KAPPA] not available — buckets={len(self.realized_pnl_history)} span_ns={span} need_min_lookback_ns={self.kappa_min_lookback}')
        else:
            raw_by_book = kappa_values.get('books', {})
            rounded_kappa = {int(b): round(v, 6) if v is not None else None for b, v in raw_by_book.items()}
            median_kappa = kappa_values.get('median')
            median_str = round(median_kappa, 6) if median_kappa is not None else None
            bt.logging.info(f'[KAPPA raw per book] {json.dumps(rounded_kappa)}')
            bt.logging.info(f"[KAPPA median] {median_str} (average={kappa_values.get('average')}, total={kappa_values.get('total')})")
        bt.logging.info(f'[REALIZED_PNL sequence per book] last_{self.pnl_sequence_max_entries}_buckets={json.dumps(truncated)}')
        if self.pnl_log_file:
            self._append_kappa_csv(timestamp, kappa_values, truncated)

    def _bsimpl_0_DetailedTemplateAgent__append_kappa_csv(self, timestamp: int, kappa_values: dict | None, truncated_sequences: dict[int, list[list[float | int]]]) -> None:
        import csv
        path = os.path.join(self.output_dir, 'kappa_pnl_sequences.csv')
        exists = os.path.isfile(path)
        raw_by_book = (kappa_values or {}).get('books', {})
        median_kappa = (kappa_values or {}).get('median')
        with open(path, 'a', newline='') as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(['timestamp', 'book_id', 'raw_kappa', 'median_kappa', 'pnl_sequence_json'])
            if not truncated_sequences and (not raw_by_book):
                writer.writerow([timestamp, '', '', median_kappa, ''])
                return
            books = sorted(set(raw_by_book.keys()) | set(truncated_sequences.keys()))
            for book_id in books:
                raw_k = raw_by_book.get(book_id)
                seq = truncated_sequences.get(book_id, [])
                writer.writerow([timestamp, book_id, round(raw_k, 6) if raw_k is not None else '', round(median_kappa, 6) if median_kappa is not None else '', json.dumps(seq)])

    def _bsimpl_0_DetailedTemplateAgent__append_pnl_csv(self, timestamp: int, books: list[BookSnapshot], pnl_summary: dict) -> None:
        import csv
        exists = os.path.isfile(self._pnl_csv_path)
        with open(self._pnl_csv_path, 'a', newline='') as f:
            writer = csv.writer(f)
            if not exists:
                writer.writerow(['timestamp', 'book_id', 'mid', 'log_return', 'pct_momentum', 'tick_realized_pnl', 'cumulative_realized_pnl', 'total_all_books'])
            tick_bucket = self.realized_pnl_history.get(timestamp, {})
            total_all = pnl_summary['total_all_books']
            for snap in books:
                writer.writerow([timestamp, snap.book_id, snap.mid, snap.log_return, snap.pct_momentum, tick_bucket.get(snap.book_id, 0.0), self.total_realized_pnl_by_book.get(snap.book_id, 0.0), total_all])

    def _bsimpl_0_DetailedTemplateAgent__log_output(self, out: ResponseSummary) -> None:
        bt.logging.info(f'[OUTPUT] count={out.instruction_count} types={json.dumps(out.by_type)}')
        for line in out.lines[:10]:
            bt.logging.info(f'[OUTPUT] {line}')
        if len(out.lines) > 10:
            bt.logging.info(f'[OUTPUT] … {len(out.lines) - 10} more instructions omitted')

    def _bsimpl_1_Strategy1_initialize(self) -> None:
        self._bsimpl_0_DetailedTemplateAgent_initialize()
        cfg = self.config
        self.fast_update = bool(getattr(cfg, 'fast_update', True))
        self.sync_event_csv = bool(getattr(cfg, 'sync_event_csv', False))
        self.log_latency = bool(getattr(cfg, 'log_latency', False))
        self.history_len = int(getattr(cfg, 'history_len', 0))
        self.log_direction = bool(getattr(cfg, 'log_direction', False))
        self.log_book_profile = bool(getattr(cfg, 'log_book_profile', False))
        self.log_regime = bool(getattr(cfg, 'log_regime', False))
        self.log_momentum_pnl = bool(getattr(cfg, 'log_momentum_pnl', False))
        self.enable_mm_strategy = bool(getattr(cfg, 'enable_mm_strategy', True))
        self.enable_kappa_strategy = bool(getattr(cfg, 'enable_kappa_strategy', False))
        self.mm_base_size = float(getattr(cfg, 'mm_base_size', 0.25))
        self.max_inventory_base = float(getattr(cfg, 'max_inventory_base', 1.2))
        self.inventory_skew_strength = float(getattr(cfg, 'inventory_skew_strength', 0.35))
        self.target_inventory_ratio = float(getattr(cfg, 'target_inventory_ratio', 0.0))
        self.archetype_dead_trade_rate = float(getattr(cfg, 'archetype_dead_trade_rate', 0.1))
        self.archetype_mm_spread_bps = float(getattr(cfg, 'archetype_mm_spread_bps', 1.0))
        self.archetype_wall_imbalance = float(getattr(cfg, 'archetype_wall_imbalance', 0.6))
        self.archetype_stressed_spread_bps = float(getattr(cfg, 'archetype_stressed_spread_bps', 8.0))
        self.archetype_vol_threshold = float(getattr(cfg, 'archetype_vol_threshold', 0.006))
        self.trade_rate_ref = float(getattr(cfg, 'trade_rate_ref', 2.0))
        self.position_max_ticks = max(1, int(getattr(cfg, 'position_max_ticks', 300)))
        self.close_score_threshold = float(getattr(cfg, 'close_score_threshold', 0.8))
        self.inventory_close_threshold = float(getattr(cfg, 'inventory_close_threshold', 0.25))
        self.passive_exit_only = bool(getattr(cfg, 'passive_exit_only', True))
        self.aggressive_close_min_ticks = max(1, int(getattr(cfg, 'aggressive_close_min_ticks', 300)))
        self.maintenance_size_mult = float(getattr(cfg, 'maintenance_size_mult', 0.25))
        self.maintenance_passive_exit_only = bool(getattr(cfg, 'maintenance_passive_exit_only', True))
        self.log_mm_strategy = bool(getattr(cfg, 'log_mm_strategy', True))
        self.log_book_memory = bool(getattr(cfg, 'log_book_memory', False))
        self.mm_expiry_period = int(getattr(cfg, 'mm_expiry_period_ns', 500000000))
        # V4.1.1 correctness guard:
        # Normal market-making quotes are intended to earn maker economics.  The
        # previous implementation delegated postOnly to _prefer_maker(), which
        # could intentionally return False when maker fees exceeded taker fees.
        # With simulated instruction delay, an otherwise passive quote could then
        # become marketable before arrival and open inventory as a taker.  Keep
        # inventory-management exits unchanged; this guard applies only to normal
        # MM/maintenance quoting paths.
        self.mm_force_post_only = self._as_bool(getattr(cfg, 'mm_force_post_only', True))
        self.mm_maker_guard_reprice = self._as_bool(getattr(cfg, 'mm_maker_guard_reprice', True))
        self.flow_depth = max(1, int(getattr(cfg, 'flow_depth', 5)))
        self.min_expected_alpha = float(getattr(cfg, 'min_expected_alpha', 0.18))
        self.max_mm_books_per_tick = max(1, int(getattr(cfg, 'max_mm_books_per_tick', 4)))
        self.max_managed_books_per_tick = max(1, int(getattr(cfg, 'max_managed_books_per_tick', 4)))
        self.mm_skip_inactive_tier = bool(getattr(cfg, 'mm_skip_inactive_tier', True))
        self.toxic_loss_streak = max(1, int(getattr(cfg, 'toxic_loss_streak', 4)))
        self.toxic_recent_pnl = float(getattr(cfg, 'toxic_recent_pnl', -0.01))
        self.toxic_spread_bps = float(getattr(cfg, 'toxic_spread_bps', 10.0))
        self.w_micro = float(getattr(cfg, 'w_micro', 0.5))
        self.w_micro_vel = float(getattr(cfg, 'w_micro_vel', 0.4))
        self.w_deep = float(getattr(cfg, 'w_deep', 0.38))
        self.w_persist = float(getattr(cfg, 'w_persist', 0.32))
        self.deep_imbalance_end = max(2, int(getattr(cfg, 'deep_imbalance_end', 5)))
        self.trade_persistence_len = max(5, int(getattr(cfg, 'trade_persistence_len', 20)))
        self.micro_vel_scale = float(getattr(cfg, 'micro_vel_scale', 8.0))
        self.direction_accuracy_weight = float(getattr(cfg, 'direction_accuracy_weight', 0.12))
        self.book_specialization_weight = float(getattr(cfg, 'book_specialization_weight', 0.22))
        self.fill_learn_blend = float(getattr(cfg, 'fill_learn_blend', 0.45))
        self.fill_learn_min_samples = max(3, int(getattr(cfg, 'fill_learn_min_samples', 5)))
        self.coverage_boost_weight = float(getattr(cfg, 'coverage_boost_weight', 0.08))
        self.min_expected_realized_pnl = float(getattr(cfg, 'min_expected_realized_pnl', 0.0))
        self.enable_auto_tuning = bool(getattr(cfg, 'enable_auto_tuning', False))
        self.allow_tuning_config = bool(getattr(cfg, 'allow_tuning_config', False))
        self.tuning_interval_ns = int(getattr(cfg, 'tuning_interval_ns', 3600000000000))
        self.log_tuning = bool(getattr(cfg, 'log_tuning', True))
        _tuning_path = getattr(cfg, 'tuning_config_path', None)
        self._tuning_config_path = str(_tuning_path) if _tuning_path else os.path.join(self.output_dir, 'tuning.json')
        self._position_ticks: dict[int, int] = {}
        self._inventory_reason: dict[int, InventoryReason] = {}
        self._micro_prev: dict[int, float] = {}
        self._dir_pending: dict[int, dict] = {}
        self._trade_signs: dict[int, Deque[float]] = {}
        self._trade_signs_tick: dict[int, int] = {}
        self.book_memory: dict[int, BookMemory] = {}
        self._last_mm_stats: dict = {}
        self._tuning_window: dict[str, int] = {}
        self._last_tuning_ts: int = 0
        self._last_tuning_objective: float = 0.0
        self._tuning_config_mtime: float = 0.0
        self.monitor_top_miners = bool(getattr(cfg, 'monitor_top_miners', False))
        self.monitor_top_n = max(1, int(getattr(cfg, 'monitor_top_n', 5)))
        if self.allow_tuning_config and os.path.isfile(self._tuning_config_path):
            self._reload_tuning_config_if_changed(force=True)
        self._clamp_tuning_params()
        bt.logging.info(f'Strategy1: mm={self.enable_mm_strategy} base_size={self.mm_base_size} max_inv={self.max_inventory_base} min_alpha={self.min_expected_alpha} max_mm_books={self.max_mm_books_per_tick} max_managed={self.max_managed_books_per_tick} skip_inactive_mm={self.mm_skip_inactive_tier} inv_close_thr={self.inventory_close_threshold} close_score={self.close_score_threshold} passive_exit={self.passive_exit_only} agg_min_ticks={self.aggressive_close_min_ticks} maint_size_mult={self.maintenance_size_mult} min_exp_pnl={self.min_expected_realized_pnl} fast_update={self.fast_update} sync_csv={self.sync_event_csv} log_latency={self.log_latency} history_len={self.history_len} coverage_w={self.coverage_boost_weight} max_mm={self.max_mm_books_per_tick} w_micro_vel={self.w_micro_vel} w_deep={self.w_deep} w_persist={self.w_persist} fill_learn={self.fill_learn_blend} spec_w={self.book_specialization_weight} toxic_streak={self.toxic_loss_streak} auto_tune={self.enable_auto_tuning} monitor_top={self.monitor_top_miners} mm_force_post_only={self.mm_force_post_only} mm_maker_guard_reprice={self.mm_maker_guard_reprice}')

    def _bsimpl_1_Strategy1_handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        """Instrument update/respond/report phases when log_latency is enabled."""
        if not self.log_latency:
            return super(BaseStrategy, self).handle(state)
        t0 = time.perf_counter()
        self.update(state)
        t_update = time.perf_counter() - t0
        t1 = time.perf_counter()
        response = self.respond(state)
        t_respond = time.perf_counter() - t1
        t2 = time.perf_counter()
        self.report(state, response)
        t_report = time.perf_counter() - t2
        if self._tick == 1 or self._tick % self.log_every_n == 0:
            notices = len((state.notices or {}).get(self.uid, []))
            bt.logging.info(f'[LATENCY] tick={self._tick} update={round(t_update, 3)}s respond={round(t_respond, 3)}s report={round(t_report, 3)}s total={round(t_update + t_respond + t_report, 3)}s notices={notices} ix={len(response.instructions)}')
        return response

    def _bsimpl_1_Strategy1__passes_expected_pnl_gate(self, expected_realized_pnl: float) -> bool:
        return expected_realized_pnl > self.min_expected_realized_pnl

    def _bsimpl_1_Strategy1__mem(self, book_id: int) -> BookMemory:
        return self.book_memory.setdefault(book_id, BookMemory())

    def _bsimpl_1_Strategy1__spread_dist_bucket(self, dist_from_touch: float) -> int:
        """Map spread-normalized distance (0=touch, higher=inside book) to bucket 0..2."""
        if dist_from_touch <= 0.22:
            return 0
        if dist_from_touch <= 0.4:
            return 1
        return 2

    def _bsimpl_1_Strategy1__record_fill_quote(self, mem: BookMemory, side: Literal['buy', 'sell'], dist_from_touch: float) -> None:
        bucket = self._spread_dist_bucket(dist_from_touch)
        if side == 'buy':
            q = list(mem.fill_buy_quotes)
            q[bucket] += 1
            mem.fill_buy_quotes = tuple(q)
            mem.last_buy_dist_bucket = bucket
        else:
            q = list(mem.fill_sell_quotes)
            q[bucket] += 1
            mem.fill_sell_quotes = tuple(q)
            mem.last_sell_dist_bucket = bucket

    def _bsimpl_1_Strategy1__record_fill_hit(self, mem: BookMemory, side: Literal['buy', 'sell']) -> None:
        if side == 'buy':
            f = list(mem.fill_buy_fills)
            f[mem.last_buy_dist_bucket] += 1
            mem.fill_buy_fills = tuple(f)
        else:
            f = list(mem.fill_sell_fills)
            f[mem.last_sell_dist_bucket] += 1
            mem.fill_sell_fills = tuple(f)

    def _bsimpl_1_Strategy1__learned_side_fill_prob(self, mem: BookMemory, side: Literal['buy', 'sell'], dist_from_touch: float) -> float | None:
        bucket = self._spread_dist_bucket(dist_from_touch)
        if side == 'buy':
            quotes, fills = (mem.fill_buy_quotes[bucket], mem.fill_buy_fills[bucket])
        else:
            quotes, fills = (mem.fill_sell_quotes[bucket], mem.fill_sell_fills[bucket])
        if quotes < self.fill_learn_min_samples:
            return None
        return fills / quotes

    def _bsimpl_1_Strategy1__update_direction_accuracy(self, book_id: int, mid: float) -> None:
        pending = self._dir_pending.get(book_id)
        if not pending or mid <= 0 or pending.get('mid', 0) <= 0:
            return
        log_ret = math.log(mid / pending['mid'])
        thr = max(self.momentum_scale, 1e-09)
        pred = pending.get('direction', 'HOLD')
        if pred == 'HOLD':
            return
        mem = self._mem(book_id)
        if pred == 'UP':
            if log_ret > thr:
                mem.direction_hits += 1
            elif log_ret < -thr:
                mem.direction_misses += 1
        elif pred == 'DOWN':
            if log_ret < -thr:
                mem.direction_hits += 1
            elif log_ret > thr:
                mem.direction_misses += 1

    def _bsimpl_1_Strategy1__compute_l2_l5_imbalance(self, book: Book) -> float:
        """Deep-book imbalance on L2–L5 (exclude touch). Positive = bid-heavy."""
        if not book.bids and (not book.asks):
            return 0.0
        bid_vol = 0.0
        ask_vol = 0.0
        if book.bids:
            for i in range(1, min(self.deep_imbalance_end, len(book.bids))):
                bid_vol += book.bids[i].quantity
        if book.asks:
            for i in range(1, min(self.deep_imbalance_end, len(book.asks))):
                ask_vol += book.asks[i].quantity
        total = bid_vol + ask_vol
        if total <= 0:
            return 0.0
        return max(-1.0, min(1.0, (bid_vol - ask_vol) / total))

    def _bsimpl_1_Strategy1__update_trade_sign_history(self, book_id: int, book: Book, timestamp: int) -> None:
        if self._trade_signs_tick.get(book_id) == timestamp:
            return
        self._trade_signs_tick[book_id] = timestamp
        hist = self._trade_signs.setdefault(book_id, deque(maxlen=self.trade_persistence_len))
        for event in book.events or []:
            etype = getattr(event, 'type', None)
            if etype not in ('t', 'EVENT_TRADE', 'ET'):
                continue
            side = getattr(event, 'side', None)
            if side == 0:
                hist.append(1.0)
            elif side == 1:
                hist.append(-1.0)

    def _bsimpl_1_Strategy1__trade_persistence(self, book_id: int) -> float:
        hist = self._trade_signs.get(book_id)
        if not hist:
            return 0.0
        return max(-1.0, min(1.0, sum(hist) / len(hist)))

    def _bsimpl_1_Strategy1__kappa_factor_from_tier(self, tier: str) -> float:
        return {'GREEN': 1.0, 'YELLOW': 0.65, 'RED': 0.25, 'INACTIVE': 0.35}.get(tier, 0.5)

    def _bsimpl_1_Strategy1__sync_kappa_factor(self, mem: BookMemory, profile: BookProfile) -> None:
        k = self._kappa_factor_from_tier(profile.tier)
        mem.book_kappa_factor = 0.85 * mem.book_kappa_factor + 0.15 * k

    def _bsimpl_1_Strategy1__update_book_specialization(self, mem: BookMemory) -> None:
        mem.book_fill_factor = 0.88 * mem.book_fill_factor + 0.12 * mem.fill_rate
        pnl_component = max(0.0, min(1.0, 0.5 + mem.recent_pnl * 50.0))
        profit_blend = 0.65 * mem.win_rate + 0.35 * pnl_component
        mem.book_profit_factor = 0.9 * mem.book_profit_factor + 0.1 * profit_blend

    def _bsimpl_1_Strategy1__global_book_rank(self, expected_alpha: float, mem: BookMemory) -> float:
        spec = mem.specialization_score
        return expected_alpha * (0.72 + 0.28 * spec) + 0.12 * spec

    def _bsimpl_1_Strategy1__reset_pnl_state(self) -> None:
        self._bsimpl_0_DetailedTemplateAgent__reset_pnl_state()
        self._position_ticks.clear()
        self._inventory_reason.clear()
        self._micro_prev.clear()
        self._dir_pending.clear()
        self._trade_signs.clear()
        self._trade_signs_tick.clear()
        self.book_memory.clear()
        self._last_mm_stats = {}
        self._tuning_window = {}
        self._last_tuning_ts = 0
        self._last_tuning_objective = 0.0

    def _bsimpl_1_Strategy1__snapshot_tuning_params(self) -> dict[str, float | int]:
        return {'min_expected_alpha': self.min_expected_alpha, 'min_expected_realized_pnl': self.min_expected_realized_pnl, 'max_mm_books_per_tick': self.max_mm_books_per_tick, 'toxic_loss_streak': self.toxic_loss_streak, 'toxic_recent_pnl': self.toxic_recent_pnl, 'coverage_boost_weight': self.coverage_boost_weight}

    def _bsimpl_1_Strategy1__clamp_tuning_params(self) -> None:
        for key, (lo, hi) in TUNING_PARAM_BOUNDS.items():
            val = getattr(self, key)
            if key in ('max_mm_books_per_tick', 'toxic_loss_streak'):
                setattr(self, key, int(max(lo, min(hi, val))))
            else:
                setattr(self, key, max(lo, min(hi, val)))

    def _bsimpl_1_Strategy1__apply_tuning_overrides(self, overrides: dict) -> list[str]:
        applied: list[str] = []
        for key, raw in overrides.items():
            if key not in TUNING_PARAM_BOUNDS:
                continue
            lo, hi = TUNING_PARAM_BOUNDS[key]
            if key in ('max_mm_books_per_tick', 'toxic_loss_streak'):
                val = int(max(lo, min(hi, float(raw))))
            else:
                val = max(lo, min(hi, float(raw)))
            setattr(self, key, val)
            applied.append(key)
        return applied

    def _bsimpl_1_Strategy1__reload_tuning_config_if_changed(self, force: bool=False) -> list[str]:
        path = self._tuning_config_path
        if not path or not os.path.isfile(path):
            return []
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return []
        if not force and mtime <= self._tuning_config_mtime:
            return []
        try:
            with open(path, encoding='utf-8') as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            bt.logging.warning(f'[TUNING] Failed to read {path}: {exc}')
            return []
        if not isinstance(data, dict):
            return []
        overrides = data.get('params', data)
        if not isinstance(overrides, dict):
            return []
        applied = self._apply_tuning_overrides(overrides)
        self._tuning_config_mtime = mtime
        self._clamp_tuning_params()
        if applied and self.log_tuning:
            bt.logging.info(f'[TUNING] hot-reload {path} applied={applied} snapshot={json.dumps(self._snapshot_tuning_params())}')
        return applied

    def _bsimpl_1_Strategy1__accumulate_tuning_window(self, stats: dict) -> None:
        if not self.enable_auto_tuning:
            return
        self._tuning_window['ticks'] = self._tuning_window.get('ticks', 0) + 1
        for key in ('skipped_negative_pnl', 'skipped_low_alpha', 'skipped_toxic', 'quoted', 'maintenance', 'instructions'):
            self._tuning_window[key] = self._tuning_window.get(key, 0) + int(stats.get(key, 0))

    def _bsimpl_1_Strategy1__aggregate_book_memory_win_rate(self) -> float:
        wins = sum((m.win_count for m in self.book_memory.values()))
        losses = sum((m.loss_count for m in self.book_memory.values()))
        return wins / max(wins + losses, 1)

    def _bsimpl_1_Strategy1__compute_tuning_metrics(self) -> TuningMetrics:
        kappa_med = self._estimate_local_normalized_median() or 0.0
        win_rate = self._aggregate_book_memory_win_rate()
        ticks = max(1, self._tuning_window.get('ticks', 1))
        skip_neg = self._tuning_window.get('skipped_negative_pnl', 0)
        skip_neg_rate = min(1.0, skip_neg / ticks)
        objective = 0.5 * kappa_med + 0.3 * win_rate - 0.2 * skip_neg_rate
        return TuningMetrics(kappa_med=kappa_med, win_rate=win_rate, skip_neg_rate=skip_neg_rate, objective=objective, window_ticks=ticks)

    def _bsimpl_1_Strategy1__apply_tuning_rules(self, metrics: TuningMetrics) -> None:
        if metrics.skip_neg_rate > 0.12:
            self.min_expected_alpha = min(0.45, self.min_expected_alpha + 0.02)
            self.min_expected_realized_pnl = min(0.002, self.min_expected_realized_pnl + 5e-05)
            self.max_mm_books_per_tick = max(4, self.max_mm_books_per_tick - 1)
        if metrics.win_rate < 0.45:
            self.min_expected_alpha = min(0.45, self.min_expected_alpha + 0.02)
            self.toxic_loss_streak = max(2, self.toxic_loss_streak - 1)
            self.toxic_recent_pnl = min(-0.001, self.toxic_recent_pnl - 0.002)
        if metrics.kappa_med > 0.55 and metrics.win_rate > 0.5 and (metrics.skip_neg_rate < 0.05):
            self.max_mm_books_per_tick = min(12, self.max_mm_books_per_tick + 1)
            self.min_expected_alpha = max(0.15, self.min_expected_alpha - 0.01)
        if self._last_tuning_objective > 0.0 and metrics.objective < self._last_tuning_objective - 0.04:
            self.max_mm_books_per_tick = max(4, self.max_mm_books_per_tick - 1)
            self.min_expected_alpha = min(0.45, self.min_expected_alpha + 0.03)
        self._clamp_tuning_params()

    def _bsimpl_1_Strategy1__persist_tuning_state(self, metrics: TuningMetrics) -> None:
        path = os.path.join(self.output_dir, 'tuning_state.json')
        history: list[dict] = []
        if os.path.isfile(path):
            try:
                with open(path, encoding='utf-8') as f:
                    payload = json.load(f)
                history = payload.get('history', [])
            except (OSError, json.JSONDecodeError):
                history = []
        entry = {'objective': round(metrics.objective, 6), 'kappa_med': round(metrics.kappa_med, 4), 'win_rate': round(metrics.win_rate, 4), 'skip_neg_rate': round(metrics.skip_neg_rate, 4), 'window_ticks': metrics.window_ticks, 'params': self._snapshot_tuning_params()}
        history.append(entry)
        if len(history) > 96:
            history = history[-96:]
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'history': history, 'last': entry}, f, indent=2)
        except OSError as exc:
            bt.logging.warning(f'[TUNING] Failed to write {path}: {exc}')

    def _bsimpl_1_Strategy1__maybe_run_tuning_scheduler(self, state: MarketSimulationStateUpdate) -> None:
        if not self.enable_auto_tuning:
            return
        now = state.timestamp
        if self._last_tuning_ts <= 0:
            self._last_tuning_ts = now
            return
        if now - self._last_tuning_ts < self.tuning_interval_ns:
            return
        self._reload_tuning_config_if_changed()
        kappa_values = self._compute_local_kappa(state)
        if kappa_values:
            self._last_kappa = kappa_values
        metrics = self._compute_tuning_metrics()
        self._apply_tuning_rules(metrics)
        self._reload_tuning_config_if_changed()
        self._persist_tuning_state(metrics)
        if self.log_tuning:
            bt.logging.info(f'[TUNING] step objective={round(metrics.objective, 4)} kappa_med={round(metrics.kappa_med, 4)} win_rate={round(metrics.win_rate, 4)} skip_neg_rate={round(metrics.skip_neg_rate, 4)} window_ticks={metrics.window_ticks} params={json.dumps(self._snapshot_tuning_params())}')
        self._last_tuning_objective = metrics.objective
        self._tuning_window = {}
        self._last_tuning_ts = now

    def _bsimpl_1_Strategy1__reason_from_client_id(self, client_id: int) -> InventoryReason:
        if client_id >= ALPHA_CLIENT_ID_BASE:
            return 'ALPHA'
        if client_id >= MM_CLIENT_ID_BASE:
            return 'MM'
        if client_id >= MAINT_CLIENT_ID_BASE:
            return 'MAINTENANCE'
        return 'UNKNOWN'

    def _bsimpl_1_Strategy1__inventory_util(self, inventory: InventorySnapshot) -> float:
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        return abs(inventory.inventory_ratio) / max(max_ratio, 1e-09)

    def _bsimpl_1_Strategy1__inventory_needs_management(self, inventory: InventorySnapshot) -> bool:
        if inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        return self._inventory_util(inventory) >= self.inventory_close_threshold

    def _bsimpl_1_Strategy1__maintenance_allowed(self, profile: BookProfile, archetype: BookArchetype) -> bool:
        if archetype in ('WALL_BOOK', 'TOXIC_BOOK', 'TREND_BOOK', 'STRESSED', 'DEAD_BOOK'):
            return False
        if archetype == 'MM_BOOK':
            return True
        if profile.tier == 'GREEN':
            return True
        return False

    def _bsimpl_1_Strategy1__allows_aggressive_close(self, book_id: int, inventory: InventorySnapshot, close_score: float, time_stop: bool, stop_loss_hit: bool) -> bool:
        if stop_loss_hit or inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        reason = self._inventory_reason.get(book_id, inventory.reason)
        if reason == 'MAINTENANCE' and self.maintenance_passive_exit_only:
            return False
        if self.passive_exit_only and inventory.position_ticks < self.aggressive_close_min_ticks:
            return False
        if close_score >= self.close_score_threshold:
            return True
        if time_stop:
            return True
        return False

    def _bsimpl_1_Strategy1_onTrade(self, event, validator: str | None=None) -> None:
        book_id = event.bookId
        pnl_before = 0.0
        net_before = 0.0
        flat_eps = self.mm_base_size * 0.001
        if book_id is not None:
            pnl_before = self._pnl_tick_buffer.get(book_id, 0.0)
            net_before = self._position_tracker_snapshot(book_id).net_qty
        self._bsimpl_0_DetailedTemplateAgent_onTrade(event, validator)
        if book_id is None:
            return
        is_taker = self.uid == event.takerAgentId
        is_maker = self.uid == event.makerAgentId
        if not is_taker and (not is_maker):
            return
        net_after = self._position_tracker_snapshot(book_id).net_qty
        if abs(net_after) < flat_eps:
            self._inventory_reason.pop(book_id, None)
        elif abs(net_after) > abs(net_before) or (abs(net_before) < flat_eps and abs(net_after) >= flat_eps):
            if is_maker and event.clientOrderId is not None:
                self._inventory_reason[book_id] = self._reason_from_client_id(event.clientOrderId)
            elif is_taker:
                self._inventory_reason[book_id] = 'MARKET'
        mem = self._mem(book_id)
        mem.fill_count += 1
        mem.last_activity_ts = event.timestamp
        if is_maker:
            agent_buy = is_taker and event.side == 0 or (is_maker and event.side == 1)
            self._record_fill_hit(mem, 'buy' if agent_buy else 'sell')
        realized_pnl = self._pnl_tick_buffer.get(book_id, 0.0) - pnl_before
        if realized_pnl != 0.0:
            mem.recent_pnl = 0.9 * mem.recent_pnl + 0.1 * realized_pnl
            if realized_pnl > 0:
                mem.win_count += 1
                mem.loss_streak = 0
            elif realized_pnl < 0:
                mem.loss_count += 1
                mem.loss_streak += 1
        self._update_book_specialization(mem)

    def _bsimpl_1_Strategy1_microprice_signal(self, book: Book) -> float:
        if not book.bids or not book.asks:
            return 0.0
        bid = book.bids[0].price
        ask = book.asks[0].price
        bid_qty = book.bids[0].quantity
        ask_qty = book.asks[0].quantity
        mid = 0.5 * (bid + ask)
        micro = (ask * bid_qty + bid * ask_qty) / max(bid_qty + ask_qty, 1e-09)
        spread = ask - bid
        if spread <= 0:
            return 0.0
        return max(-1.0, min(1.0, (micro - mid) / spread))

    def _bsimpl_1_Strategy1_predict_direction(self, book_id: int, book: Book, timestamp: int) -> DirectionForecast:
        mid = self._book_mid(book)
        if mid > 0:
            self._update_direction_accuracy(book_id, mid)
        log_return, _ = self._update_momentum(book_id, timestamp, mid)
        momentum_m = self._normalize_momentum(log_return)
        flow_f = self._compute_flow_f(book)
        trade_t, trade_imbalance = self._compute_trade_t(book)
        deep_imb = self._compute_l2_l5_imbalance(book)
        self._update_trade_sign_history(book_id, book, timestamp)
        trade_persist = self._trade_persistence(book_id)
        micro = self.microprice_signal(book)
        micro_prev = self._micro_prev.get(book_id, micro)
        micro_vel = micro - micro_prev
        self._micro_prev[book_id] = micro
        micro_vel_sig = max(-1.0, min(1.0, micro_vel * self.micro_vel_scale))
        imbalance = 0.55 * flow_f + 0.45 * deep_imb
        score = self.w_m * momentum_m + self.w_f * flow_f + self.w_t * trade_t + self.w_micro * micro + self.w_micro_vel * micro_vel_sig + self.w_deep * deep_imb + self.w_persist * trade_persist
        self._mem(book_id).last_signal = score
        if score > self.direction_threshold:
            direction: Literal['UP', 'DOWN', 'HOLD'] = 'UP'
        elif score < -self.direction_threshold:
            direction = 'DOWN'
        else:
            direction = 'HOLD'
        if mid > 0:
            self._dir_pending[book_id] = {'direction': direction, 'mid': mid, 'timestamp': timestamp}
        return DirectionForecast(book_id=book_id, direction=direction, score=score, momentum_m=momentum_m, flow_f=flow_f, trade_t=trade_t, log_return=log_return, imbalance=imbalance, trade_imbalance=trade_imbalance)

    def _bsimpl_1_Strategy1_coverage_priority(self, book_id: int, now: int) -> float:
        mem = self._mem(book_id)
        if mem.last_activity_ts <= 0:
            return 1.0
        age = max(0, now - mem.last_activity_ts)
        return min(1.0, age / max(self.pnl_lookback_ns, 1))

    def _bsimpl_1_Strategy1_is_toxic_book(self, book_id: int, profile: BookProfile, archetype: BookArchetype) -> bool:
        mem = self._mem(book_id)
        return mem.loss_streak >= self.toxic_loss_streak or mem.recent_pnl < self.toxic_recent_pnl or (profile.spread_bps is not None and profile.spread_bps > self.toxic_spread_bps) or (archetype == 'STRESSED') or (profile.tier == 'RED')

    def _bsimpl_1_Strategy1__tier_mm_boost(self, tier: str) -> float:
        """Prefer MM on books with kappa history (GREEN/YELLOW) over cold INACTIVE."""
        return {'GREEN': 0.18, 'YELLOW': 0.1, 'RED': -0.05, 'INACTIVE': -0.12}.get(tier, 0.0)

    def _bsimpl_1_Strategy1__inventory_urgency(self, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> float:
        close_score = self._compute_close_score(inventory, regime_params, regime, archetype)
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        inv_risk = min(1.0, abs(inventory.inventory_ratio) / max(max_ratio, 1e-09))
        time_risk = min(1.0, inventory.position_ticks / self.position_max_ticks)
        loss_risk = 0.0
        if inventory.unrealized_bps is not None and inventory.unrealized_bps < 0:
            loss_risk = min(1.0, abs(inventory.unrealized_bps) / regime_params.stop_loss_bps)
        return close_score + inv_risk + time_risk + loss_risk

    def _bsimpl_1_Strategy1_expected_alpha_score(self, profile: BookProfile, prediction: DirectionForecast, fill_est: FillProbabilityEstimate, mem: BookMemory, book_id: int, now: int) -> float:
        self._sync_kappa_factor(mem, profile)
        signal = min(1.0, abs(prediction.score))
        fill = 0.5 * (fill_est.buy + fill_est.sell)
        memory_bonus = 0.5 * mem.win_rate + 0.5 * mem.fill_rate
        accuracy_bonus = mem.direction_accuracy
        pnl_bonus = max(-1.0, min(1.0, mem.recent_pnl * 100))
        coverage = self.coverage_priority(book_id, now)
        tier_boost = self._tier_mm_boost(profile.tier)
        specialization = mem.specialization_score
        score = 0.26 * signal + 0.2 * fill + 0.12 * memory_bonus + self.direction_accuracy_weight * accuracy_bonus + 0.06 * pnl_bonus + self.coverage_boost_weight * coverage + self.book_specialization_weight * specialization + tier_boost
        mem.last_expected_alpha = score
        return score

    def _bsimpl_1_Strategy1__schedule_maintenance_books(self, selection: BookSelection, now: int, limit: int | None=None) -> list[int]:
        cap = limit if limit is not None else self.max_maintenance_books_per_tick
        candidates = list(selection.maintenance_books)
        inactive_ids = [p.book_id for p in selection.profiles if p.tier == 'INACTIVE']
        for book_id in inactive_ids:
            if book_id not in candidates:
                candidates.append(book_id)
        candidates.sort(key=lambda b: self.coverage_priority(b, now), reverse=True)
        return candidates[:cap]

    def _bsimpl_1_Strategy1_get_regime_params(self, regime: MarketRegime) -> RegimeParamSet:
        params = DEFAULT_REGIME_PARAMS.get(regime.mode, DEFAULT_REGIME_PARAMS['MIXED'])
        if regime.scoring_overlay == 'SCORING_PRESSURE':
            return RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=params.spread_offset, skew_strength=params.skew_strength * 0.5, size_mult=min(params.size_mult, 0.6), profit_target_bps=params.profit_target_bps, stop_loss_bps=params.stop_loss_bps, min_fill_prob=params.min_fill_prob, buy_bias=params.buy_bias, sell_bias=params.sell_bias)
        return params

    def _bsimpl_1_Strategy1_merge_regime_and_archetype_params(self, regime_params: RegimeParamSet, archetype: BookArchetype) -> RegimeParamSet:
        adj = DEFAULT_ARCHETYPE_ADJUST.get(archetype, ArchetypeAdjust())
        quote_enabled = adj.quote_enabled_override if adj.quote_enabled_override is not None else regime_params.quote_enabled
        return RegimeParamSet(quote_enabled=quote_enabled, alpha_enabled=regime_params.alpha_enabled, spread_offset=max(0.05, regime_params.spread_offset + adj.spread_offset_delta), skew_strength=regime_params.skew_strength * adj.skew_strength_mult, size_mult=regime_params.size_mult * adj.size_mult, profit_target_bps=regime_params.profit_target_bps, stop_loss_bps=regime_params.stop_loss_bps, min_fill_prob=max(0.05, min(0.95, regime_params.min_fill_prob + adj.min_fill_prob_delta)), buy_bias=regime_params.buy_bias, sell_bias=regime_params.sell_bias)

    def _bsimpl_1_Strategy1_get_archetype_edge_bias(self, archetype: BookArchetype) -> float:
        adj = DEFAULT_ARCHETYPE_ADJUST.get(archetype, ArchetypeAdjust())
        return adj.edge_bias

    def _bsimpl_1_Strategy1_classify_book_archetype(self, profile: BookProfile, regime: MarketRegime) -> BookArchetype:
        spread_bps = profile.spread_bps or 0.0
        if spread_bps >= self.archetype_stressed_spread_bps or regime.mode == 'STRESSED':
            return 'STRESSED'
        if profile.trade_rate < self.archetype_dead_trade_rate:
            return 'DEAD_BOOK'
        if spread_bps < self.archetype_mm_spread_bps:
            return 'MM_BOOK'
        if abs(profile.imbalance) >= self.archetype_wall_imbalance:
            return 'WALL_BOOK'
        if profile.volatility >= self.archetype_vol_threshold and abs(profile.predict_score) >= self.direction_threshold:
            return 'TREND_BOOK'
        if profile.volatility >= self.archetype_vol_threshold:
            return 'TOXIC_BOOK'
        if abs(profile.predict_score) >= self.direction_threshold:
            return 'TREND_BOOK'
        return 'TOXIC_BOOK'

    def _bsimpl_1_Strategy1__position_tracker_snapshot(self, book_id: int) -> PositionTracker:
        pos = self._open_positions.get(book_id)
        if not pos:
            return PositionTracker(0.0, None, None, 0.0, 0.0)
        long_qty = sum((q for _, q, _, _ in pos['longs']))
        short_qty = sum((q for _, q, _, _ in pos['shorts']))
        net_qty = long_qty - short_qty
        vwap: float | None = None
        opened_at: int | None = None
        if net_qty > 0 and long_qty > 0:
            vwap = sum((q * p for _, q, p, _ in pos['longs'])) / long_qty
            opened_at = pos['longs'][0][0]
        elif net_qty < 0 and short_qty > 0:
            vwap = sum((q * p for _, q, p, _ in pos['shorts'])) / short_qty
            opened_at = pos['shorts'][0][0]
        return PositionTracker(net_qty, vwap, opened_at, long_qty, short_qty)

    def _bsimpl_1_Strategy1__wealth_per_book(self) -> float:
        if not self.simulation_config:
            return 0.0
        return self.simulation_config.miner_wealth / max(self.simulation_config.book_count, 1)

    def _bsimpl_1_Strategy1__net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        if mid <= 0:
            return InventorySnapshot(0.0, 0.0, 'FLAT', None, None, 0)
        tracker = self._position_tracker_snapshot(book_id)
        net_base = tracker.net_qty
        wealth_per_book = self._wealth_per_book()
        inventory_ratio = 0.0
        if wealth_per_book > 0:
            inventory_ratio = net_base * mid / wealth_per_book
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        flat_eps = self.mm_base_size * 0.001
        if abs(net_base) < flat_eps:
            band: InventoryBand = 'FLAT'
            self._position_ticks.pop(book_id, None)
            self._inventory_reason.pop(book_id, None)
        elif net_base > 0:
            band = 'MAX_LONG' if inventory_ratio >= max_ratio else 'LONG'
            self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
        else:
            band = 'MAX_SHORT' if abs(inventory_ratio) >= max_ratio else 'SHORT'
            self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
        position_ticks = self._position_ticks.get(book_id, 0)
        vwap = tracker.vwap_entry
        unrealized_bps: float | None = None
        if vwap and vwap > 0:
            if net_base > 0:
                unrealized_bps = (mid - vwap) / vwap * 10000.0
            elif net_base < 0:
                unrealized_bps = (vwap - mid) / vwap * 10000.0
        return InventorySnapshot(net_base=net_base, inventory_ratio=inventory_ratio, band=band, vwap_entry=vwap, unrealized_bps=unrealized_bps, position_ticks=position_ticks, opened_at_ns=tracker.opened_at_ns, reason=self._inventory_reason.get(book_id, 'UNKNOWN'))

    def _bsimpl_1_Strategy1__log_book_memory_sample(self, state: MarketSimulationStateUpdate) -> None:
        rows: list[dict] = []
        for book_id in sorted(state.books.keys())[:10]:
            mem = self._mem(book_id)
            pos = self._position_tracker_snapshot(book_id)
            rows.append({'book': book_id, 'win': round(mem.win_rate, 3), 'fill': round(mem.fill_rate, 3), 'pnl': round(mem.recent_pnl, 5), 'streak': mem.loss_streak, 'quotes': mem.quote_count, 'fills': mem.fill_count, 'net': round(pos.net_qty, 4), 'vwap': round(pos.vwap_entry or 0.0, 4), 'ea': round(mem.last_expected_alpha, 4), 'dir_acc': round(mem.direction_accuracy, 3), 'profit_f': round(mem.book_profit_factor, 3), 'fill_f': round(mem.book_fill_factor, 3), 'kappa_f': round(mem.book_kappa_factor, 3), 'spec': round(mem.specialization_score, 3), 'fb': list(mem.fill_buy_fills), 'fs': list(mem.fill_sell_fills)})
        bt.logging.info(f'[BOOK_MEMORY] {json.dumps(rows)}')

    def _bsimpl_1_Strategy1_estimate_fill_probability(self, book: Book, mid: float, spread: float, trade_rate: float, buy_price: float, sell_price: float, book_id: int | None=None) -> FillProbabilityEstimate:
        if spread <= 0 or mid <= 0:
            return FillProbabilityEstimate(0.0, 0.0)
        trade_factor = min(1.0, trade_rate / max(self.trade_rate_ref, 1e-09))
        bid_depth = book.bids[0].quantity if book.bids else 0.0
        ask_depth = book.asks[0].quantity if book.asks else 0.0
        deep_bid = bid_depth
        deep_ask = ask_depth
        if book.bids:
            deep_bid = sum((book.bids[i].quantity for i in range(min(self.deep_imbalance_end, len(book.bids)))))
        if book.asks:
            deep_ask = sum((book.asks[i].quantity for i in range(min(self.deep_imbalance_end, len(book.asks)))))
        total_bid = deep_bid if deep_bid > 0 else sum((l.quantity for l in book.bids)) if book.bids else bid_depth
        total_ask = deep_ask if deep_ask > 0 else sum((l.quantity for l in book.asks)) if book.asks else ask_depth
        buy_dist = (mid - buy_price) / spread
        sell_dist = (sell_price - mid) / spread
        buy_depth_f = bid_depth / max(total_bid, 1e-09)
        sell_depth_f = ask_depth / max(total_ask, 1e-09)
        depth_buy = trade_rate / max(bid_depth + 1.0, 1e-09)
        depth_sell = trade_rate / max(ask_depth + 1.0, 1e-09)
        depth_buy = min(1.0, depth_buy / max(self.trade_rate_ref, 1e-09))
        depth_sell = min(1.0, depth_sell / max(self.trade_rate_ref, 1e-09))
        dist_buy = max(0.0, 1.0 - buy_dist)
        dist_sell = max(0.0, 1.0 - sell_dist)
        p_buy = trade_factor * (0.25 * buy_depth_f + 0.35 * dist_buy + 0.4 * depth_buy)
        p_sell = trade_factor * (0.25 * sell_depth_f + 0.35 * dist_sell + 0.4 * depth_sell)
        if book_id is not None:
            mem = self._mem(book_id)
            mem_blend = 0.1
            p_buy = (1.0 - mem_blend) * p_buy + mem_blend * mem.fill_rate
            p_sell = (1.0 - mem_blend) * p_sell + mem_blend * mem.fill_rate
            buy_touch_dist = max(0.0, 0.5 - buy_dist)
            sell_touch_dist = max(0.0, 0.5 - sell_dist)
            learned_buy = self._learned_side_fill_prob(mem, 'buy', buy_touch_dist)
            learned_sell = self._learned_side_fill_prob(mem, 'sell', sell_touch_dist)
            if learned_buy is not None:
                p_buy = (1.0 - self.fill_learn_blend) * p_buy + self.fill_learn_blend * learned_buy
            if learned_sell is not None:
                p_sell = (1.0 - self.fill_learn_blend) * p_sell + self.fill_learn_blend * learned_sell
        return FillProbabilityEstimate(buy=max(0.0, min(1.0, p_buy)), sell=max(0.0, min(1.0, p_sell)))

    def _bsimpl_1_Strategy1_dynamic_order_size(self, base_size: float, profile: BookProfile, regime_params: RegimeParamSet, inventory: InventorySnapshot, vol_dec: int, mid: float | None=None) -> float:
        confidence = max(0.5, min(2.0, 1.0 + abs(profile.predict_score)))
        vol_scale = 1.0
        if profile.volatility > 0:
            target_vol = self.profile_vol_scale
            vol_scale = max(0.5, min(2.0, target_vol / profile.volatility))
        spread_factor = 1.0
        if profile.spread is not None and mid is not None and (mid > 0):
            spread_bps = profile.spread / mid * 10000.0
            spread_factor = max(0.5, min(1.5, 1.0 - spread_bps / 20.0))
        kappa_scale = 1.0
        if profile.raw_kappa is not None:
            kappa_scale = max(0.5, min(1.5, 1.0 + profile.raw_kappa * 0.2))
        inv_util = abs(inventory.inventory_ratio)
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        inventory_factor = max(0.3, 1.0 - inv_util / max(max_ratio, 1e-09))
        size = base_size * confidence * regime_params.size_mult * vol_scale * spread_factor * kappa_scale * inventory_factor
        return self._round_order_size(size, vol_dec)

    def _bsimpl_1_Strategy1_skewed_quote_prices(self, bid: float, ask: float, signal: float, inventory_ratio: float, regime_params: RegimeParamSet, price_dec: int, edge_bias: float=0.0) -> tuple[float, float] | None:
        spread = ask - bid
        if spread <= 0:
            return None
        mid = 0.5 * (bid + ask)
        offset = regime_params.spread_offset
        inventory_edge = self.target_inventory_ratio - inventory_ratio
        buy_edge = signal + edge_bias + inventory_edge
        sell_edge = -signal - edge_bias - inventory_edge
        buy_skew = regime_params.skew_strength * buy_edge * regime_params.buy_bias
        sell_skew = regime_params.skew_strength * sell_edge * regime_params.sell_bias
        inv_skew = self.inventory_skew_strength * inventory_edge
        bid_px = round(mid - spread * (offset + buy_skew + inv_skew), price_dec)
        ask_px = round(mid + spread * (offset - sell_skew - inv_skew), price_dec)
        if bid_px <= 0 or bid_px >= ask_px:
            return None
        return (bid_px, ask_px)

    def _bsimpl_1_Strategy1__compute_close_score(self, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> float:
        unreal = inventory.unrealized_bps
        target = max(regime_params.profit_target_bps, 1e-09)
        stop = regime_params.stop_loss_bps
        pnl_component = 0.0
        if unreal is not None:
            if unreal >= target:
                pnl_component = 1.0
            elif unreal <= -stop:
                pnl_component = 1.0
            elif unreal > 0:
                pnl_component = unreal / target
            else:
                pnl_component = abs(unreal) / stop
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        inventory_risk = min(1.0, abs(inventory.inventory_ratio) / max(max_ratio, 1e-09))
        regime_risk = 0.0
        if regime.mode == 'STRESSED':
            regime_risk = 1.0
        elif archetype in ('TOXIC_BOOK', 'WALL_BOOK'):
            regime_risk = 0.6
        elif archetype == 'DEAD_BOOK':
            regime_risk = 0.4
        time_risk = min(1.0, inventory.position_ticks / self.position_max_ticks)
        return 0.5 * pnl_component + 0.3 * inventory_risk + 0.2 * max(regime_risk, time_risk)

    def _bsimpl_1_Strategy1__clear_position_state(self, book_id: int) -> None:
        self._position_ticks.pop(book_id, None)
        self._inventory_reason.pop(book_id, None)

    def _bsimpl_1_Strategy1__place_passive_inventory_exit(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book: Book, inventory: InventorySnapshot, qty: float) -> int:
        """Single-sided passive exit: ask for long, bid for short."""
        long_pos = inventory.net_base > 0
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        close_px = round(book.bids[0].price if close_dir == OrderDirection.BUY else book.asks[0].price, state.config.priceDecimals)
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return 0
        account = self.accounts[book_id]
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.limit_order(book_id=book_id, direction=close_dir, quantity=qty, price=close_px, stp=STP.CANCEL_BOTH, postOnly=self._prefer_maker(book_id), timeInForce=TimeInForce.GTT, expiryPeriod=self.mm_expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            return 1
        if close_dir == OrderDirection.BUY and account.quote_balance.free >= qty * close_px:
            response.limit_order(book_id=book_id, direction=close_dir, quantity=qty, price=close_px, stp=STP.CANCEL_BOTH, postOnly=self._prefer_maker(book_id), timeInForce=TimeInForce.GTT, expiryPeriod=self.mm_expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            return 1
        return 0

    def _bsimpl_1_Strategy1__try_close_loans(self, response: FinanceAgentResponse, book_id: int, unrealized_bps: float | None, profit_target_bps: float) -> bool:
        account = self.accounts.get(book_id)
        if not account or not account.loans:
            return False
        if unrealized_bps is None or unrealized_bps < profit_target_bps:
            return False
        for order_id in list(account.loans.keys()):
            if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
                break
            response.close_position(book_id=book_id, order_id=order_id, delay=0)
        return True

    def _bsimpl_1_Strategy1__execute_aggressive_close(self, response: FinanceAgentResponse, book_id: int, book: Book, qty: float, long_pos: bool) -> bool:
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        if not self._passes_fee_gate(book_id, aggressive=True):
            return False
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return False
        account = self.accounts[book_id]
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.market_order(book_id=book_id, direction=close_dir, quantity=qty, stp=STP.CANCEL_OLDEST, delay=0)
            self._clear_position_state(book_id)
            return True
        if close_dir == OrderDirection.BUY:
            px = book.asks[0].price
            if account.quote_balance.free >= qty * px:
                response.market_order(book_id=book_id, direction=close_dir, quantity=qty, stp=STP.CANCEL_OLDEST, delay=0)
                self._clear_position_state(book_id)
                return True
        return False

    def _bsimpl_1_Strategy1__manage_inventory(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book: Book, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> int:
        if inventory.band == 'FLAT':
            return 0
        placed = 0
        mid = (book.bids[0].price + book.asks[0].price) / 2.0
        qty = self._round_order_size(abs(inventory.net_base), state.config.volumeDecimals)
        if qty <= 0:
            return 0
        if self._try_close_loans(response, book_id, inventory.unrealized_bps, regime_params.profit_target_bps):
            placed += 1
        close_score = self._compute_close_score(inventory, regime_params, regime, archetype)
        long_pos = inventory.net_base > 0
        time_stop = inventory.position_ticks >= self.position_max_ticks
        stop_loss_hit = inventory.unrealized_bps is not None and inventory.unrealized_bps <= -regime_params.stop_loss_bps
        aggressive_close = self._allows_aggressive_close(book_id, inventory, close_score, time_stop, stop_loss_hit)
        if aggressive_close:
            if self._execute_aggressive_close(response, book_id, book, qty, long_pos):
                placed += 1
            return placed
        placed += self._place_passive_inventory_exit(response, state, book_id, book, inventory, qty)
        return placed

    def _bsimpl_1_Strategy1__place_skewed_quotes(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book: Book, profile: BookProfile, prediction: DirectionForecast, inventory: InventorySnapshot, regime_params: RegimeParamSet, size: float, edge_bias: float, stats: dict | None=None) -> int:
        if inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return 0
        cfg = state.config
        bid = book.bids[0].price
        ask = book.asks[0].price
        spread = ask - bid
        mid = (bid + ask) / 2.0
        prices = self.skewed_quote_prices(bid, ask, prediction.score, inventory.inventory_ratio, regime_params, cfg.priceDecimals, edge_bias=edge_bias)
        if not prices:
            return 0
        bid_px, ask_px = prices

        # V4.1.1 NORMAL-MM maker guard.
        #
        # Research V4.1 already constrains reservation quotes relative to the
        # current touch, but the validator/simulator applies instruction delay.
        # A quote that is passive at decision time may become marketable before
        # arrival.  postOnly=True is therefore the authoritative protection.
        #
        # Repricing is intentionally conservative and only prevents a quote from
        # crossing the CURRENT touch.  It does not alter inventory exits.
        normal_mm_post_only = bool(self.mm_force_post_only) or bool(self._prefer_maker(book_id))
        maker_guard_adjusted = False
        if normal_mm_post_only and self.mm_maker_guard_reprice:
            tick_size = 10.0 ** (-int(cfg.priceDecimals))
            maker_bid_cap = round(ask - tick_size, cfg.priceDecimals)
            maker_ask_floor = round(bid + tick_size, cfg.priceDecimals)
            guarded_bid = min(float(bid_px), maker_bid_cap)
            guarded_ask = max(float(ask_px), maker_ask_floor)
            guarded_bid = round(guarded_bid, cfg.priceDecimals)
            guarded_ask = round(guarded_ask, cfg.priceDecimals)
            maker_guard_adjusted = (guarded_bid != bid_px) or (guarded_ask != ask_px)
            bid_px, ask_px = guarded_bid, guarded_ask
            if bid_px <= 0.0 or bid_px >= ask_px or bid_px >= ask or ask_px <= bid:
                if stats is not None:
                    stats['maker_guard_skipped'] = stats.get('maker_guard_skipped', 0) + 1
                return 0
        if stats is not None:
            if self.mm_force_post_only and not self._prefer_maker(book_id):
                stats['maker_guard_forced_post_only'] = stats.get('maker_guard_forced_post_only', 0) + 1
            if maker_guard_adjusted:
                stats['maker_guard_repriced'] = stats.get('maker_guard_repriced', 0) + 1

        qty = self.dynamic_order_size(size, profile, regime_params, inventory, cfg.volumeDecimals, mid=mid)
        if qty <= 0:
            return 0
        fill_est = self.estimate_fill_probability(book, mid, spread, profile.trade_rate, bid_px, ask_px, book_id=book_id)
        quote_notional = qty * mid * 2
        if not self._can_add_volume(state, quote_notional):
            return 0
        edge = ask_px - bid_px
        expected_edge = edge * (fill_est.buy + fill_est.sell) / 2.0
        if expected_edge <= 0:
            return 0
        est = self.estimate_round_trip_pnl(book_id, bid_px, ask_px, qty, is_maker=normal_mm_post_only, direction='SYMMETRIC', timestamp=state.timestamp)
        adj_pnl = est.expected_realized_pnl * (fill_est.buy + fill_est.sell) / 2.0
        if not self._passes_expected_pnl_gate(est.expected_realized_pnl):
            if stats is not None:
                stats['skipped_negative_pnl'] = stats.get('skipped_negative_pnl', 0) + 1
            if self.log_predict_pnl and self.verbose_log:
                bt.logging.info(f'[PREDICT_PNL] skip book={book_id} expected_pnl={round(est.expected_realized_pnl, 6)} (min={self.min_expected_realized_pnl}) bid={bid_px} ask={ask_px}')
            return 0
        if fill_est.buy < regime_params.min_fill_prob and fill_est.sell < regime_params.min_fill_prob:
            return 0
        placed = 0
        acct = self.accounts[book_id]
        mem = self._mem(book_id)
        buy_touch_dist = max(0.0, (mid - bid_px) / spread)
        sell_touch_dist = max(0.0, (ask_px - mid) / spread)
        buy_size = qty
        sell_size = qty
        if inventory.band == 'LONG':
            buy_size = self._round_order_size(qty * 0.5, cfg.volumeDecimals)
        elif inventory.band == 'SHORT':
            sell_size = self._round_order_size(qty * 0.5, cfg.volumeDecimals)
        if fill_est.buy >= regime_params.min_fill_prob and acct.quote_balance.free >= bid_px * buy_size and (self._count_book_instructions(response, book_id) < self.max_instructions_per_book):
            self._record_fill_quote(mem, 'buy', buy_touch_dist)
            response.limit_order(book_id=book_id, direction=OrderDirection.BUY, quantity=buy_size, price=bid_px, clientOrderId=70000 + book_id * 10 + 1, stp=STP.CANCEL_BOTH, postOnly=normal_mm_post_only, timeInForce=TimeInForce.GTT, expiryPeriod=self.mm_expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            placed += 1
            self._mem(book_id).quote_count += 1
        if fill_est.sell >= regime_params.min_fill_prob and acct.base_balance.free >= sell_size and (self._count_book_instructions(response, book_id) < self.max_instructions_per_book):
            self._record_fill_quote(mem, 'sell', sell_touch_dist)
            response.limit_order(book_id=book_id, direction=OrderDirection.SELL, quantity=sell_size, price=ask_px, clientOrderId=70000 + book_id * 10 + 2, stp=STP.CANCEL_BOTH, postOnly=normal_mm_post_only, timeInForce=TimeInForce.GTT, expiryPeriod=self.mm_expiry_period, leverage=0.0, settlement_option=LoanSettlementOption.NONE, delay=0)
            placed += 1
            self._mem(book_id).quote_count += 1
        if self.log_predict_pnl and self.verbose_log and (placed > 0):
            bt.logging.info(f'[PREDICT_PNL] mm book={book_id} fill_b={round(fill_est.buy, 3)} fill_s={round(fill_est.sell, 3)} expected_pnl={round(est.expected_realized_pnl, 6)} adj_pnl={round(adj_pnl, 6)} exp_edge={round(expected_edge, 6)} bid={bid_px} ask={ask_px}')
        return placed

    def _bsimpl_1_Strategy1__place_directional_round_trip(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, direction: Literal['UP', 'DOWN'], size: float, client_id_base: int=50000, stats: dict | None=None) -> int:
        cfg = state.config
        book = state.books.get(book_id)
        if not cfg or not book or (not book.bids) or (not book.asks):
            return 0
        qty = self._round_order_size(size, cfg.volumeDecimals)
        if qty <= 0:
            return 0
        best_bid = round(book.bids[0].price, cfg.priceDecimals)
        best_ask = round(book.asks[0].price, cfg.priceDecimals)
        est = self.estimate_round_trip_pnl(book_id, best_bid, best_ask, qty, is_maker=self._prefer_maker(book_id), direction=direction, timestamp=state.timestamp)
        if not self._passes_expected_pnl_gate(est.expected_realized_pnl):
            if stats is not None:
                stats['skipped_negative_pnl'] = stats.get('skipped_negative_pnl', 0) + 1
            if self.log_predict_pnl and self.verbose_log:
                bt.logging.info(f'[PREDICT_PNL] skip alpha book={book_id} dir={direction} expected_pnl={round(est.expected_realized_pnl, 6)} (min={self.min_expected_realized_pnl})')
            return 0
        if self.log_predict_pnl and self.verbose_log:
            bt.logging.info(f'[PREDICT_PNL] alpha book={book_id} dir={direction} expected_pnl={round(est.expected_realized_pnl, 6)} qty={qty}')
        return self._bsimpl_0_DetailedTemplateAgent__place_directional_round_trip(response, state, book_id, direction, size, client_id_base)

    def _bsimpl_1_Strategy1_build_mm_strategy_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime, collect_archetypes: bool=True) -> dict:
        # Archetype row materialization is diagnostic-only. Avoid per-book dict
        # creation on the production no-log path while preserving it with --log.
        collect_archetypes = bool(
            collect_archetypes
            and (
                getattr(self, 'log_mm_strategy', False)
                or getattr(self, 'debug_enabled', False)
                or getattr(self, 'research_enabled', False)
            )
        )
        stats = {'quoted': 0, 'managed': 0, 'maintenance': 0, 'skipped_avoid': 0, 'skipped_archetype': 0, 'skipped_toxic': 0, 'skipped_alpha': 0, 'skipped_negative_pnl': 0, 'skipped_low_alpha': 0, 'skipped_small_inv': 0, 'skipped_maint_arch': 0, 'mm_candidates': 0, 'alpha_ranked': 0, 'instructions': 0}
        regime_params = self.get_regime_params(regime)
        avoid_set = set(selection.avoid_books)
        profile_by_id = {p.book_id: p for p in selection.profiles}
        maint_limit = self.max_maintenance_books_per_tick
        maintenance_set = set(self._schedule_maintenance_books(selection, state.timestamp, limit=maint_limit))
        archetype_rows: list[dict] = []
        mm_candidates: list[tuple] = []
        alpha_candidates: list[tuple] = []
        manage_queue: list[tuple[float, int, Book, InventorySnapshot, RegimeParamSet, BookArchetype]] = []
        maintenance_placed_books = 0
        for book_id, book in state.books.items():
            if not book.bids or not book.asks:
                continue
            profile = profile_by_id.get(book_id)
            if not profile:
                continue
            archetype = self.classify_book_archetype(profile, regime)
            book_params = self.merge_regime_and_archetype_params(regime_params, archetype)
            edge_bias = self.get_archetype_edge_bias(archetype)
            if collect_archetypes:
                mem_row = self._mem(book_id)
                archetype_rows.append({'book': book_id, 'arch': archetype, 'tier': profile.tier, 'size_mult': round(book_params.size_mult, 3), 'fill': round(mem_row.fill_rate, 3), 'win': round(mem_row.win_rate, 3), 'streak': mem_row.loss_streak})
            if book_id in avoid_set:
                stats['skipped_avoid'] += 1
                continue
            prediction = predictions.get(book_id)
            if not prediction:
                continue
            mid = (book.bids[0].price + book.asks[0].price) / 2.0
            spread = book.asks[0].price - book.bids[0].price
            inventory = self._net_inventory(book_id, mid)
            if inventory.band != 'FLAT':
                if self._inventory_needs_management(inventory):
                    urgency = self._inventory_urgency(inventory, book_params, regime, archetype)
                    manage_queue.append((urgency, book_id, book, inventory, book_params, archetype))
                    continue
                stats['skipped_small_inv'] += 1
            toxic = self.is_toxic_book(book_id, profile, archetype)
            if book_id in maintenance_set:
                if maintenance_placed_books >= maint_limit:
                    stats['skipped_low_alpha'] += 1
                    continue
                if inventory.band != 'FLAT':
                    stats['skipped_small_inv'] += 1
                    continue
                if not self._maintenance_allowed(profile, archetype):
                    stats['skipped_maint_arch'] += 1
                    continue
                if toxic and regime.scoring_overlay != 'SCORING_PRESSURE':
                    stats['skipped_toxic'] += 1
                    continue
                maint_size = self.maintenance_order_size * self.maintenance_size_mult
                n = self._place_round_trip_limits(response, state, book_id, maint_size, post_only=True, expiry_period=state.config.publish_interval, client_id_base=MAINT_CLIENT_ID_BASE)
                if n:
                    maintenance_placed_books += 1
                    mem_m = self._mem(book_id)
                    mem_m.quote_count += n
                    if spread > 0:
                        self._record_fill_quote(mem_m, 'buy', max(0.0, (mid - book.bids[0].price) / spread))
                        self._record_fill_quote(mem_m, 'sell', max(0.0, (book.asks[0].price - mid) / spread))
                    stats['maintenance'] += 1
                    stats['instructions'] += n
                continue
            if toxic:
                stats['skipped_toxic'] += 1
                continue
            if not book_params.quote_enabled:
                stats['skipped_archetype'] += 1
                continue
            if archetype in ('TOXIC_BOOK', 'STRESSED') and regime.mode in ('CHOP', 'STRESSED'):
                stats['skipped_toxic'] += 1
                continue
            if self.mm_skip_inactive_tier and profile.tier == 'INACTIVE':
                stats['skipped_low_alpha'] += 1
                continue
            fill_est = self.estimate_fill_probability(book, mid, spread, profile.trade_rate, book.bids[0].price, book.asks[0].price, book_id=book_id)
            mem = self._mem(book_id)
            expected_alpha = self.expected_alpha_score(profile, prediction, fill_est, mem, book_id, state.timestamp)
            if expected_alpha < self.min_expected_alpha:
                stats['skipped_low_alpha'] += 1
                continue
            global_rank = self._global_book_rank(expected_alpha, mem)
            if not math.isfinite(global_rank) or global_rank <= -1e8:
                stats['skipped_score_ev'] = stats.get('skipped_score_ev', 0) + 1
                continue
            mm_candidates.append((global_rank, expected_alpha, book_id, book, profile, prediction, inventory, book_params, edge_bias))
        manage_queue.sort(key=lambda x: x[0], reverse=True)
        for _urgency, book_id, book, inventory, book_params, archetype in manage_queue[:self.max_managed_books_per_tick]:
            n = self._manage_inventory(response, state, book_id, book, inventory, book_params, regime, archetype)
            if n:
                stats['managed'] += 1
                stats['instructions'] += n
        mm_candidates.sort(key=lambda x: x[0], reverse=True)
        stats['mm_candidates'] = len(mm_candidates)
        for item in mm_candidates[:self.max_mm_books_per_tick]:
            _rank, _ea, book_id, book, profile, prediction, inventory, book_params, edge_bias = item
            before_ix = len(response.instructions)
            n = self._place_skewed_quotes(response, state, book_id, book, profile, prediction, inventory, book_params, self.mm_base_size, edge_bias, stats=stats)
            if n:
                stats['quoted'] += 1
                stats['instructions'] += n
            if len(response.instructions) - before_ix > self.max_instructions_per_book:
                break
        if regime_params.alpha_enabled and self._alpha_regime_allows(regime):
            for book_id in selection.alpha_books:
                if book_id in avoid_set or book_id not in state.books:
                    continue
                pred = predictions.get(book_id)
                profile = profile_by_id.get(book_id)
                book = state.books[book_id]
                if not pred or not profile or pred.direction == 'HOLD':
                    continue
                if not book.bids or not book.asks:
                    continue
                archetype = self.classify_book_archetype(profile, regime)
                if self.is_toxic_book(book_id, profile, archetype):
                    stats['skipped_toxic'] += 1
                    continue
                mid = (book.bids[0].price + book.asks[0].price) / 2.0
                spread = book.asks[0].price - book.bids[0].price
                fill_est = self.estimate_fill_probability(book, mid, spread, profile.trade_rate, book.bids[0].price, book.asks[0].price, book_id=book_id)
                mem = self._mem(book_id)
                ea = self.expected_alpha_score(profile, pred, fill_est, mem, book_id, state.timestamp)
                if ea < self.min_expected_alpha:
                    stats['skipped_alpha'] += 1
                    continue
                alpha_candidates.append((ea, book_id, pred, profile, archetype))
            alpha_candidates.sort(key=lambda x: x[0], reverse=True)
            stats['alpha_ranked'] = len(alpha_candidates)
            for ea, book_id, pred, profile, archetype in alpha_candidates[:self.max_alpha_books_per_tick]:
                merged = self.merge_regime_and_archetype_params(regime_params, archetype)
                alpha_size = self.dynamic_order_size(self.alpha_order_size, profile, merged, InventorySnapshot(0, 0, 'FLAT', None, None, 0), state.config.volumeDecimals, mid=profile.mid)
                n = self._place_directional_round_trip(response, state, book_id, 'UP' if pred.direction == 'UP' else 'DOWN', alpha_size, client_id_base=80000, stats=stats)
                if n:
                    self._mem(book_id).quote_count += n
                    stats['instructions'] += n
        stats['archetypes'] = archetype_rows[:12]
        self._last_mm_stats = stats
        return stats

    def _bsimpl_1_Strategy1__log_mm_strategy(self, stats: dict, regime: MarketRegime) -> None:
        bt.logging.info(f"[MM_STRATEGY] regime={regime.mode} overlay={regime.scoring_overlay} stats={json.dumps({k: v for k, v in stats.items() if k != 'archetypes'})}")
        if stats.get('archetypes'):
            bt.logging.info(f"[MM_STRATEGY] archetypes={json.dumps(stats['archetypes'])}")

    def _bsimpl_1_Strategy1_respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        response = FinanceAgentResponse(agent_id=self.uid)
        self._tick += 1
        log_tick = self._tick == 1 or self._tick % self.log_every_n == 0
        need_summary = log_tick and (self.verbose_log or self.log_momentum_pnl)
        summary = self.parse_state(state) if need_summary else None
        predictions = self._predict_all_books(state)
        selection = self.select_books_for_trading(state, predictions)
        regime = self.classify_market_regime_from_profiles(selection.profiles, predictions, selection)
        if summary and self.verbose_log and log_tick:
            self._log_input(summary)
        if self.log_direction and log_tick:
            self._log_direction_predictions(predictions)
        if self.log_book_profile and log_tick:
            self._log_book_profile_selection(selection)
        if self.log_regime and log_tick:
            self._log_market_regime(regime)
        if summary and self.log_momentum_pnl and log_tick:
            self._log_momentum_and_pnl(summary, state)
        if self.log_book_memory and log_tick:
            self._log_book_memory_sample(state)
        grace_period_ns = summary.grace_period_ns if summary else state.config.grace_period if state.config else 0
        in_grace = state.timestamp < grace_period_ns
        collect_archetypes = self.log_mm_strategy and log_tick
        if state.books and (not in_grace):
            if self.enable_mm_strategy:
                mm_stats = self.build_mm_strategy_instructions(response, state, selection, predictions, regime, collect_archetypes=collect_archetypes)
                self._accumulate_tuning_window(mm_stats)
                if self.log_mm_strategy and log_tick:
                    self._log_mm_strategy(mm_stats, regime)
            elif self.enable_kappa_strategy:
                strategy_stats = self.build_kappa_strategy_instructions(response, state, selection, predictions, regime)
                if self.log_kappa_strategy and (self._tick == 1 or self._tick % self.log_every_n == 0):
                    self._log_kappa_strategy_calibration(state, selection, regime, strategy_stats)
            elif self.enable_trading:
                self.build_demo_instructions(response, state, book_id=0)
        elif state.books and in_grace and (self.enable_mm_strategy or self.enable_kappa_strategy or self.enable_trading):
            bt.logging.info(f'Grace period active (T={state.timestamp} < {summary.grace_period_ns}); no orders placed.')
        if self.verbose_log and response.instructions and log_tick:
            self._log_output(self.parse_response(response))
        self._maybe_run_tuning_scheduler(state)
        if self.monitor_top_miners:
            try:
                from top_miner_monitor import write_tick_tap
                write_tick_tap(state, self._tick, self.output_dir, self.uid, self.monitor_top_n)
            except Exception as exc:
                bt.logging.warning(f'monitor tap failed: {exc}')
        return response

    def _bsimpl_2_Strategy1_Debug_initialize(self) -> None:
        self._bsimpl_1_Strategy1_initialize()
        cfg = self.config
        self.debug_enabled = self._env_bool('STRATEGY1_DEBUG', self._as_bool(getattr(cfg, 'debug_enabled', False)))
        self.debug_every_n = max(1, self._env_int('STRATEGY1_DEBUG_EVERY_N', int(getattr(cfg, 'debug_every_n', 1))))
        self.debug_summary_every_n = max(1, self._env_int('STRATEGY1_DEBUG_SUMMARY_N', int(getattr(cfg, 'debug_summary_every_n', 100))))
        self.debug_book_id = self._env_int('STRATEGY1_DEBUG_BOOK', int(getattr(cfg, 'debug_book_id', -1)))
        self.debug_jsonl = self._env_bool('STRATEGY1_DEBUG_JSONL', self._as_bool(getattr(cfg, 'debug_jsonl', True)))
        self.debug_slow_request_ms = max(0.0, float(getattr(cfg, 'debug_slow_request_ms', 250.0)))
        configured_dir = str(getattr(cfg, 'debug_output_dir', '') or '')
        env_dir = os.getenv('STRATEGY1_DEBUG_DIR', '').strip()
        self.debug_output_dir = env_dir or configured_dir or os.path.join(self.output_dir, 'strategy1_debug')
        self._debug_file = None
        self._debug_stage_ms: dict[str, float] = {}
        self._debug_book_records: dict[int, dict[str, Any]] = {}
        self._debug_current_state: MarketSimulationStateUpdate | None = None
        self._debug_current_regime: MarketRegime | None = None
        self._debug_reason_counts: Counter[str] = Counter()
        self._debug_event_counts: Counter[str] = Counter()
        self._debug_latency_sum_ms: Counter[str] = Counter()
        self._debug_latency_max_ms: Counter[str] = Counter()
        self._debug_response_count = 0
        if self.debug_enabled and self.debug_jsonl:
            try:
                os.makedirs(self.debug_output_dir, exist_ok=True)
                path = os.path.join(self.debug_output_dir, f'strategy1_debug_agent_{self.uid}.jsonl')
                self._debug_file = open(path, 'a', encoding='utf-8', buffering=1)
                atexit.register(self._close_debug_file)
            except OSError as exc:
                self._debug_file = None
                bt.logging.warning(f'[S1DBG] cannot open JSONL output: {exc}')
        self._emit('DEBUG_CONFIG', force=True, enabled=self.debug_enabled, every_n=self.debug_every_n, summary_every_n=self.debug_summary_every_n, book_filter=self.debug_book_id, jsonl=self.debug_jsonl, output_dir=self.debug_output_dir, slow_request_ms=self.debug_slow_request_ms)

    def _bsimpl_2_Strategy1_Debug_handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1_handle(state)
        next_tick = self._tick + 1
        self._log_notices(state, next_tick)
        t0 = time.perf_counter()
        self.update(state)
        update_ms = self._elapsed_ms(t0)
        t1 = time.perf_counter()
        response = self.respond(state)
        respond_ms = self._elapsed_ms(t1)
        t2 = time.perf_counter()
        self.report(state, response)
        report_ms = self._elapsed_ms(t2)
        total_ms = update_ms + respond_ms + report_ms
        self._record_latency('update_ms', update_ms)
        self._record_latency('respond_ms', respond_ms)
        self._record_latency('report_ms', report_ms)
        self._record_latency('total_ms', total_ms)
        self._debug_response_count += 1
        if self._should_emit_tick(self._tick):
            self._emit(
                'TIMING',
                tick=self._tick,
                timestamp=getattr(state, 'timestamp', None),
                update_ms=round(update_ms, 4),
                respond_ms=round(respond_ms, 4),
                report_ms=round(report_ms, 4),
                total_ms=round(total_ms, 4),
                screen_all_books_ms=0.0,
                full_predict_ms=round(float(self._debug_stage_ms.get('predict_all_books_ms', 0.0) or 0.0), 4),
                select_books_ms=round(float(self._debug_stage_ms.get('select_books_ms', 0.0) or 0.0), 4),
                build_orders_ms=round(float(self._debug_stage_ms.get('build_mm_ms', 0.0) or 0.0), 4),
                total_response_ms=round(total_ms, 4),
                internal_ms={key: round(value, 4) for key, value in sorted(self._debug_stage_ms.items())},
                notices=len((getattr(state, 'notices', None) or {}).get(self.uid, [])),
                instructions=len(getattr(response, 'instructions', []) or []),
            )
        if self.debug_slow_request_ms > 0.0 and total_ms >= self.debug_slow_request_ms:
            stage_ms = {key: round(value, 4) for key, value in sorted(self._debug_stage_ms.items())}
            max_stage = max(stage_ms.items(), key=lambda item: item[1]) if stage_ms else (None, None)
            try:
                open_positions = sum(1 for bid in getattr(self, '_open_positions', {}) if abs(float(self._position_tracker_snapshot(bid).net_qty)) >= self._execution_flat_epsilon())
            except Exception:
                open_positions = None
            self._emit(
                'SLOW_REQUEST',
                force=True,
                tick=self._tick,
                timestamp=getattr(state, 'timestamp', None),
                threshold_ms=round(self.debug_slow_request_ms, 4),
                update_ms=round(update_ms, 4),
                respond_ms=round(respond_ms, 4),
                report_ms=round(report_ms, 4),
                total_ms=round(total_ms, 4),
                max_stage=max_stage[0],
                max_stage_ms=max_stage[1],
                internal_ms=stage_ms,
                notices=len((getattr(state, 'notices', None) or {}).get(self.uid, [])),
                instructions=len(getattr(response, 'instructions', []) or []),
                book_count=len(getattr(state, 'books', {}) or {}),
                open_positions=open_positions,
                parked_dust=len(getattr(self, '_research_parked_dust', {}) or {}),
            )
        if self._tick == 1 or self._tick % self.debug_summary_every_n == 0:
            self._emit_run_summary(state)
        return response

    def _bsimpl_2_Strategy1_Debug_respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1_respond(state)
        self._debug_stage_ms = {}
        self._debug_book_records = {}
        self._debug_current_state = state
        self._debug_current_regime = None
        started = time.perf_counter()
        try:
            response = self._bsimpl_1_Strategy1_respond(state)
        except Exception as exc:
            self._emit('ERROR', force=True, tick=self._tick, stage='respond', error_type=type(exc).__name__, error=str(exc))
            raise
        finally:
            self._debug_stage_ms['respond_parent_ms'] = self._elapsed_ms(started)
        self._log_submitted_instructions(response, state)
        self._debug_current_state = None
        self._debug_current_regime = None
        return response

    def _bsimpl_2_Strategy1_Debug__predict_all_books(self, state: MarketSimulationStateUpdate):
        return self._timed('predict_all_books_ms', self._bsimpl_0_DetailedTemplateAgent__predict_all_books, state)

    def _bsimpl_2_Strategy1_Debug_select_books_for_trading(self, state, predictions):
        return self._timed('select_books_ms', self._bsimpl_0_DetailedTemplateAgent_select_books_for_trading, state, predictions)

    def _bsimpl_2_Strategy1_Debug_classify_market_regime_from_profiles(self, profiles, predictions, selection):
        regime = self._timed('classify_regime_ms', self._bsimpl_0_DetailedTemplateAgent_classify_market_regime_from_profiles, profiles, predictions, selection)
        if self.debug_enabled:
            self._debug_current_regime = regime
        return regime

    def _bsimpl_2_Strategy1_Debug_build_mm_strategy_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime, collect_archetypes: bool=True) -> dict:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1_build_mm_strategy_instructions(response, state, selection, predictions, regime, collect_archetypes=collect_archetypes)
        self._debug_current_regime = regime
        started = time.perf_counter()
        stats = self._bsimpl_1_Strategy1_build_mm_strategy_instructions(response, state, selection, predictions, regime, collect_archetypes=collect_archetypes)
        self._debug_stage_ms['build_mm_ms'] = self._elapsed_ms(started)
        self._finalize_book_decisions(response=response, state=state, selection=selection, predictions=predictions, regime=regime)
        return stats

    def _bsimpl_2_Strategy1_Debug__net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        inventory = self._bsimpl_1_Strategy1__net_inventory(book_id, mid)
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['inventory'] = {'net_base': inventory.net_base, 'ratio': inventory.inventory_ratio, 'band': inventory.band, 'vwap_entry': inventory.vwap_entry, 'unrealized_bps': inventory.unrealized_bps, 'position_ticks': inventory.position_ticks, 'reason': inventory.reason}
        return inventory

    def _bsimpl_2_Strategy1_Debug_classify_book_archetype(self, profile: BookProfile, regime: MarketRegime) -> BookArchetype:
        archetype = self._bsimpl_1_Strategy1_classify_book_archetype(profile, regime)
        if self.debug_enabled:
            self._book_record(profile.book_id)['archetype'] = archetype
        return archetype

    def _bsimpl_2_Strategy1_Debug_is_toxic_book(self, book_id: int, profile: BookProfile, archetype: BookArchetype) -> bool:
        toxic = self._bsimpl_1_Strategy1_is_toxic_book(book_id, profile, archetype)
        if self.debug_enabled:
            self._book_record(book_id)['toxic'] = toxic
        return toxic

    def _bsimpl_2_Strategy1_Debug_expected_alpha_score(self, profile: BookProfile, prediction: DirectionForecast, fill_est, mem, book_id: int, now: int) -> float:
        score = self._bsimpl_1_Strategy1_expected_alpha_score(profile, prediction, fill_est, mem, book_id, now)
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['expected_alpha'] = score
            record['fill_buy'] = getattr(fill_est, 'buy', None)
            record['fill_sell'] = getattr(fill_est, 'sell', None)
        return score

    def _bsimpl_2_Strategy1_Debug__manage_inventory(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> int:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1__manage_inventory(response, state, book_id, book, inventory, regime_params, regime, archetype)
        before = len(getattr(response, 'instructions', []) or [])
        started = time.perf_counter()
        placed = self._bsimpl_1_Strategy1__manage_inventory(response, state, book_id, book, inventory, regime_params, regime, archetype)
        elapsed = self._elapsed_ms(started)
        record = self._book_record(book_id)
        record['manage_ms'] = elapsed
        record['action'] = 'MANAGE' if placed else 'SKIP'
        record['reason'] = DebugReason.MANAGED_INVENTORY if placed else DebugReason.MANAGE_ORDER_GATE
        record['instructions_added'] = len(getattr(response, 'instructions', []) or []) - before
        return placed

    def _bsimpl_2_Strategy1_Debug__place_skewed_quotes(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book, profile: BookProfile, prediction: DirectionForecast, inventory: InventorySnapshot, regime_params: RegimeParamSet, size: float, edge_bias: float, stats: dict | None=None) -> int:
        if not self.debug_enabled:
            return self._bsimpl_1_Strategy1__place_skewed_quotes(response, state, book_id, book, profile, prediction, inventory, regime_params, size, edge_bias, stats=stats)
        diagnosis = self._diagnose_quote_setup(response=response, state=state, book_id=book_id, book=book, profile=profile, prediction=prediction, inventory=inventory, regime_params=regime_params, size=size, edge_bias=edge_bias)
        before = len(getattr(response, 'instructions', []) or [])
        started = time.perf_counter()
        placed = self._bsimpl_1_Strategy1__place_skewed_quotes(response, state, book_id, book, profile, prediction, inventory, regime_params, size, edge_bias, stats=stats)
        elapsed = self._elapsed_ms(started)
        record = self._book_record(book_id)
        record.update(diagnosis)
        record['quote_ms'] = elapsed
        all_instructions = list(getattr(response, 'instructions', []) or [])
        added_instructions = all_instructions[before:]
        record['instructions_added'] = len(added_instructions)
        if added_instructions:
            maker_flags = []
            touch_safe_flags = []
            touch_bid = book.bids[0].price if getattr(book, 'bids', None) else None
            touch_ask = book.asks[0].price if getattr(book, 'asks', None) else None
            for instr in added_instructions:
                maker_flags.append(bool(self._get(instr, 'postOnly', 'post_only')))
                direction = self._get(instr, 'direction')
                price = self._get(instr, 'price')
                safe = True
                try:
                    if direction == OrderDirection.BUY and touch_ask is not None:
                        safe = float(price) < float(touch_ask)
                    elif direction == OrderDirection.SELL and touch_bid is not None:
                        safe = float(price) > float(touch_bid)
                except (TypeError, ValueError):
                    safe = False
                touch_safe_flags.append(bool(safe))
            record['maker_guard_post_only'] = all(maker_flags)
            record['maker_guard_touch_safe'] = all(touch_safe_flags)
            record['maker_guard_forced'] = bool(self.mm_force_post_only and not self._prefer_maker(book_id))
        if placed:
            record['action'] = 'QUOTE'
            record['reason'] = DebugReason.QUOTED
        else:
            record['action'] = 'SKIP'
            record['reason'] = diagnosis.get('gate_reason', DebugReason.QUOTE_ORDER_GATE)
        return placed

    def _bsimpl_2_Strategy1_Debug__place_directional_round_trip(self, *args, **kwargs) -> int:
        placed = self._bsimpl_1_Strategy1__place_directional_round_trip(*args, **kwargs)
        if self.debug_enabled:
            book_id = kwargs.get('book_id')
            if book_id is None and len(args) >= 3:
                book_id = args[2]
            if isinstance(book_id, int):
                record = self._book_record(book_id)
                if placed:
                    record['action'] = 'ALPHA'
                    record['reason'] = DebugReason.ALPHA_ORDER
        return placed

    def _bsimpl_2_Strategy1_Debug_onTrade(self, event, validator: str | None=None) -> None:
        book_id = self._get(event, 'bookId', 'book_id')
        net_before = None
        if isinstance(book_id, int):
            net_before = self._position_tracker_snapshot(book_id).net_qty
        self._bsimpl_1_Strategy1_onTrade(event, validator)
        if not self.debug_enabled or not self._book_matches(book_id):
            return
        net_after = None
        if isinstance(book_id, int):
            net_after = self._position_tracker_snapshot(book_id).net_qty
        self._debug_event_counts['TRADE_FILL'] += 1
        self._emit('ORDER_LIFECYCLE', tick=self._tick, phase='TRADE_FILL', book_id=book_id, event=self._event_payload(event), net_before=net_before, net_after=net_after)

    def _bsimpl_2_Strategy1_Debug__finalize_book_decisions(self, *, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime) -> None:
        if not self._should_emit_tick(self._tick):
            return
        profile_by_id = {profile.book_id: profile for profile in selection.profiles}
        avoid_set = set(selection.avoid_books)
        maintenance_set = set(self._schedule_maintenance_books(selection, state.timestamp, limit=self.max_maintenance_books_per_tick))
        instruction_counts = self._instruction_counts_by_book(response)
        regime_params = self.get_regime_params(regime)
        for book_id, book in state.books.items():
            if not self._book_matches(book_id):
                continue
            record = self._book_record(book_id)
            profile = profile_by_id.get(book_id)
            prediction = predictions.get(book_id)
            record['instructions'] = instruction_counts.get(book_id, 0)
            if not getattr(book, 'bids', None) or not getattr(book, 'asks', None):
                record.setdefault('action', 'SKIP')
                record.setdefault('reason', DebugReason.NO_BOOK_SIDES)
            elif profile is None:
                record.setdefault('action', 'SKIP')
                record.setdefault('reason', DebugReason.NO_PROFILE)
            elif book_id in avoid_set:
                record.setdefault('action', 'SKIP')
                record.setdefault('reason', DebugReason.AVOID_LIST)
            elif prediction is None:
                record.setdefault('action', 'SKIP')
                record.setdefault('reason', DebugReason.NO_PREDICTION)
            else:
                self._complete_trading_reason(record=record, book_id=book_id, profile=profile, prediction=prediction, maintenance_set=maintenance_set, regime=regime, regime_params=regime_params, instruction_count=instruction_counts.get(book_id, 0))
            self._emit_book_decision(state, regime, book_id, book, profile, prediction, record)

    def _bsimpl_2_Strategy1_Debug__complete_trading_reason(self, *, record: dict[str, Any], book_id: int, profile: BookProfile, prediction: DirectionForecast, maintenance_set: set[int], regime: MarketRegime, regime_params: RegimeParamSet, instruction_count: int) -> None:
        if record.get('reason'):
            return
        inventory = record.get('inventory', {})
        band = inventory.get('band', 'FLAT')
        archetype = record.get('archetype')
        toxic = bool(record.get('toxic', False))
        if band != 'FLAT' and self._inventory_record_needs_management(inventory):
            record['action'] = 'SKIP'
            record['reason'] = DebugReason.MANAGEMENT_LIMIT
            return
        if book_id in maintenance_set:
            if band != 'FLAT':
                reason = DebugReason.MAINT_INVENTORY_NONFLAT
            elif archetype is not None and (not self._maintenance_allowed(profile, archetype)):
                reason = DebugReason.MAINT_ARCHETYPE_BLOCK
            elif toxic and regime.scoring_overlay != 'SCORING_PRESSURE':
                reason = DebugReason.TOXIC_BOOK
            elif instruction_count:
                record['action'] = 'MAINTENANCE'
                record['reason'] = DebugReason.MAINTENANCE_ORDER
                return
            else:
                reason = DebugReason.MAINT_ORDER_GATE
            record['action'] = 'SKIP'
            record['reason'] = reason
            return
        if toxic:
            reason = DebugReason.TOXIC_BOOK
        elif archetype is not None:
            merged = self.merge_regime_and_archetype_params(regime_params, archetype)
            if not merged.quote_enabled:
                reason = DebugReason.QUOTE_DISABLED
            elif archetype in ('TOXIC_BOOK', 'STRESSED') and regime.mode in ('CHOP', 'STRESSED'):
                reason = DebugReason.TOXIC_REGIME
            elif self.mm_skip_inactive_tier and profile.tier == 'INACTIVE':
                reason = DebugReason.INACTIVE_TIER
            elif record.get('expected_alpha', float('-inf')) < self.min_expected_alpha:
                reason = DebugReason.LOW_EXPECTED_ALPHA
            elif instruction_count:
                record['action'] = 'ORDER'
                record['reason'] = DebugReason.ALPHA_ORDER
                return
            else:
                reason = DebugReason.MM_CANDIDATE_LIMIT
        else:
            reason = DebugReason.NO_ACTION
        record['action'] = 'SKIP'
        record['reason'] = reason

    def _bsimpl_2_Strategy1_Debug__diagnose_quote_setup(self, *, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book, profile: BookProfile, prediction: DirectionForecast, inventory: InventorySnapshot, regime_params: RegimeParamSet, size: float, edge_bias: float) -> dict[str, Any]:
        diag: dict[str, Any] = {}
        if inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            diag['gate_reason'] = DebugReason.MAX_INVENTORY
            return diag
        cfg = state.config
        bid = book.bids[0].price
        ask = book.asks[0].price
        spread = ask - bid
        mid = (bid + ask) / 2.0
        diag.update(mid=mid, spread=spread)
        prices = self.skewed_quote_prices(bid, ask, prediction.score, inventory.inventory_ratio, regime_params, cfg.priceDecimals, edge_bias=edge_bias)
        if not prices:
            diag['gate_reason'] = DebugReason.INVALID_QUOTE_PRICES
            return diag
        bid_px, ask_px = prices
        diag.update(bid_px=bid_px, ask_px=ask_px)
        qty = self.dynamic_order_size(size, profile, regime_params, inventory, cfg.volumeDecimals, mid=mid)
        diag['quantity'] = qty
        if qty <= 0:
            diag['gate_reason'] = DebugReason.ZERO_ORDER_SIZE
            return diag
        fill_est = self.estimate_fill_probability(book, mid, spread, profile.trade_rate, bid_px, ask_px, book_id=book_id)
        diag['fill_buy'] = fill_est.buy
        diag['fill_sell'] = fill_est.sell
        quote_notional = qty * mid * 2
        diag['quote_notional'] = quote_notional
        if not self._can_add_volume(state, quote_notional):
            diag['gate_reason'] = DebugReason.VOLUME_CAP
            return diag
        edge = ask_px - bid_px
        expected_edge = edge * (fill_est.buy + fill_est.sell) / 2.0
        diag['expected_edge'] = expected_edge
        if expected_edge <= 0:
            diag['gate_reason'] = DebugReason.NON_POSITIVE_EDGE
            return diag
        estimate = self.estimate_round_trip_pnl(book_id, bid_px, ask_px, qty, is_maker=self._prefer_maker(book_id), direction='SYMMETRIC', timestamp=state.timestamp)
        diag['expected_realized_pnl'] = estimate.expected_realized_pnl
        if not self._passes_expected_pnl_gate(estimate.expected_realized_pnl):
            diag['gate_reason'] = DebugReason.NEGATIVE_EXPECTED_PNL
            return diag
        if fill_est.buy < regime_params.min_fill_prob and fill_est.sell < regime_params.min_fill_prob:
            diag['gate_reason'] = DebugReason.LOW_FILL_PROBABILITY
            return diag
        before_count = self._count_book_instructions(response, book_id)
        if before_count >= self.max_instructions_per_book:
            diag['gate_reason'] = DebugReason.INSTRUCTION_LIMIT
            return diag
        account = self.accounts.get(book_id)
        if account is None:
            diag['gate_reason'] = DebugReason.INSUFFICIENT_BALANCE
            return diag
        buy_size = qty * (0.5 if inventory.band == 'LONG' else 1.0)
        sell_size = qty * (0.5 if inventory.band == 'SHORT' else 1.0)
        can_buy = fill_est.buy >= regime_params.min_fill_prob and account.quote_balance.free >= bid_px * buy_size
        can_sell = fill_est.sell >= regime_params.min_fill_prob and account.base_balance.free >= sell_size
        diag['can_buy'] = can_buy
        diag['can_sell'] = can_sell
        if not can_buy and (not can_sell):
            diag['gate_reason'] = DebugReason.INSUFFICIENT_BALANCE
        else:
            diag['gate_reason'] = DebugReason.QUOTE_ORDER_GATE
        return diag

    def _bsimpl_2_Strategy1_Debug__emit_book_decision(self, state, regime, book_id: int, book, profile, prediction, record: dict[str, Any]) -> None:
        bid = book.bids[0].price if getattr(book, 'bids', None) else None
        ask = book.asks[0].price if getattr(book, 'asks', None) else None
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        spread_bps = None
        if mid and bid is not None and (ask is not None):
            spread_bps = (ask - bid) / mid * 10000.0
        reason = str(record.get('reason', DebugReason.NO_ACTION))
        self._debug_reason_counts[reason] += 1
        self._emit('DECISION', tick=self._tick, timestamp=getattr(state, 'timestamp', None), book_id=book_id, action=record.get('action', 'SKIP'), reason=reason, regime=getattr(regime, 'mode', None), overlay=getattr(regime, 'scoring_overlay', None), archetype=record.get('archetype'), tier=getattr(profile, 'tier', None) if profile is not None else None, mid=mid, spread_bps=spread_bps, direction=getattr(prediction, 'direction', None) if prediction else None, signal=getattr(prediction, 'score', None) if prediction else None, expected_alpha=record.get('expected_alpha'), min_expected_alpha=self.min_expected_alpha, fill_buy=record.get('fill_buy'), fill_sell=record.get('fill_sell'), bid_px=record.get('bid_px'), ask_px=record.get('ask_px'), quantity=record.get('quantity'), expected_realized_pnl=record.get('expected_realized_pnl'), inventory=record.get('inventory'), instructions=record.get('instructions', 0), decision_ms=record.get('quote_ms', record.get('manage_ms')))

    def _bsimpl_2_Strategy1_Debug__log_submitted_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate) -> None:
        for index, instruction in enumerate(getattr(response, 'instructions', []) or []):
            book_id = self._get(instruction, 'bookId', 'book_id')
            if not self._book_matches(book_id):
                continue
            self._debug_event_counts['SUBMITTED'] += 1
            self._emit('ORDER_LIFECYCLE', tick=self._tick, timestamp=getattr(state, 'timestamp', None), phase='SUBMITTED', instruction_index=index, book_id=book_id, instruction=self._instruction_payload(instruction))

    def _bsimpl_2_Strategy1_Debug__log_notices(self, state: MarketSimulationStateUpdate, tick: int) -> None:
        notices = (getattr(state, 'notices', None) or {}).get(self.uid, []) or []
        for notice in notices:
            book_id = self._get(notice, 'bookId', 'book_id')
            if not self._book_matches(book_id):
                continue
            phase = type(notice).__name__.upper()
            self._debug_event_counts[phase] += 1
            self._emit('ORDER_LIFECYCLE', tick=tick, timestamp=getattr(state, 'timestamp', None), phase=phase, book_id=book_id, event=self._event_payload(notice))

    def _bsimpl_2_Strategy1_Debug__emit_run_summary(self, state: MarketSimulationStateUpdate) -> None:
        count = max(self._debug_response_count, 1)
        avg_latency = {name: round(total / count, 4) for name, total in sorted(self._debug_latency_sum_ms.items())}
        max_latency = {name: round(value, 4) for name, value in sorted(self._debug_latency_max_ms.items())}
        self._emit('RUN_SUMMARY', force=True, tick=self._tick, timestamp=getattr(state, 'timestamp', None), responses=self._debug_response_count, reason_counts=dict(self._debug_reason_counts), event_counts=dict(self._debug_event_counts), average_latency_ms=avg_latency, max_latency_ms=max_latency)

    def _bsimpl_2_Strategy1_Debug__emit(self, event_type: str, force: bool=False, **payload: Any) -> None:
        if not self.debug_enabled and (not force):
            return
        record = {'type': event_type, 'agent_id': getattr(self, 'uid', None), 'wall_time_ns': time.time_ns(), **self._json_safe(payload)}
        try:
            line = json.dumps(record, separators=(',', ':'), sort_keys=True)
            bt.logging.info(f'[S1DBG] {line}')
            if self._debug_file is not None:
                self._debug_file.write(line + '\n')
        except Exception as exc:
            bt.logging.warning(f'[S1DBG] emit failed: {exc}')

    def _bsimpl_2_Strategy1_Debug__timed(self, name: str, fn: Callable[..., T], *args, **kwargs) -> T:
        if not self.debug_enabled:
            return fn(*args, **kwargs)
        started = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        finally:
            self._debug_stage_ms[name] = self._elapsed_ms(started)

    def _bsimpl_2_Strategy1_Debug__record_latency(self, name: str, value: float) -> None:
        self._debug_latency_sum_ms[name] += value
        self._debug_latency_max_ms[name] = max(self._debug_latency_max_ms[name], value)

    def _bsimpl_2_Strategy1_Debug__book_record(self, book_id: int) -> dict[str, Any]:
        return self._debug_book_records.setdefault(book_id, {})

    def _bsimpl_2_Strategy1_Debug__instruction_counts_by_book(self, response: FinanceAgentResponse) -> Counter[int]:
        counts: Counter[int] = Counter()
        for instruction in getattr(response, 'instructions', []) or []:
            book_id = self._get(instruction, 'bookId', 'book_id')
            if isinstance(book_id, int):
                counts[book_id] += 1
        return counts

    def _bsimpl_2_Strategy1_Debug__inventory_record_needs_management(self, inventory: dict[str, Any]) -> bool:
        band = inventory.get('band')
        if band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        ratio = abs(float(inventory.get('ratio', 0.0) or 0.0))
        max_ratio = self.max_inventory_base / max(self.mm_base_size, 1e-09)
        utilization = ratio / max(max_ratio, 1e-09)
        return utilization >= self.inventory_close_threshold

    def _bsimpl_2_Strategy1_Debug__instruction_payload(self, instruction: Any) -> dict[str, Any]:
        payload = self._object_payload(instruction)
        payload.setdefault('instruction_type', type(instruction).__name__)
        return payload

    def _bsimpl_2_Strategy1_Debug__event_payload(self, event: Any) -> dict[str, Any]:
        payload = self._object_payload(event)
        payload.setdefault('event_type', type(event).__name__)
        return payload

    def _bsimpl_2_Strategy1_Debug__object_payload(self, obj: Any) -> dict[str, Any]:
        if obj is None:
            return {}
        if hasattr(obj, 'model_dump'):
            try:
                value = obj.model_dump(mode='json')
                if isinstance(value, dict):
                    return self._json_safe(value)
            except Exception:
                pass
        if hasattr(obj, 'dict'):
            try:
                value = obj.dict()
                if isinstance(value, dict):
                    return self._json_safe(value)
            except Exception:
                pass
        names = ('bookId', 'book_id', 'orderId', 'order_id', 'clientOrderId', 'client_order_id', 'direction', 'side', 'price', 'quantity', 'remainingQuantity', 'filledQuantity', 'timestamp', 'delay', 'reason', 'status', 'takerAgentId', 'makerAgentId')
        result: dict[str, Any] = {}
        for name in names:
            if hasattr(obj, name):
                result[name] = self._json_safe(getattr(obj, name))
        return result

    @classmethod
    def _bsimpl_2_Strategy1_Debug__json_safe(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, bool)):
            return value
        if isinstance(value, float):
            return value if math.isfinite(value) else str(value)
        if isinstance(value, Enum):
            return cls._json_safe(value.value)
        if isinstance(value, dict):
            return {str(k): cls._json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple, set)):
            return [cls._json_safe(v) for v in value]
        if hasattr(value, 'model_dump'):
            try:
                return cls._json_safe(value.model_dump(mode='json'))
            except Exception:
                pass
        return str(value)

    @staticmethod
    def _bsimpl_2_Strategy1_Debug__get(obj: Any, *names: str) -> Any:
        if obj is None:
            return None
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    def _bsimpl_2_Strategy1_Debug__book_matches(self, book_id: Any) -> bool:
        return self.debug_book_id < 0 or book_id == self.debug_book_id

    def _bsimpl_2_Strategy1_Debug__should_emit_tick(self, tick: int) -> bool:
        return tick == 1 or tick % self.debug_every_n == 0

    def _bsimpl_2_Strategy1_Debug__close_debug_file(self) -> None:
        handle = getattr(self, '_debug_file', None)
        if handle is not None:
            try:
                handle.flush()
                handle.close()
            except OSError:
                pass
            self._debug_file = None

    @staticmethod
    def _bsimpl_2_Strategy1_Debug__elapsed_ms(started: float) -> float:
        return (time.perf_counter() - started) * 1000.0

    @staticmethod
    def _bsimpl_2_Strategy1_Debug__as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}

    @classmethod
    def _bsimpl_2_Strategy1_Debug__env_bool(cls, name: str, default: bool) -> bool:
        value = os.getenv(name)
        return default if value is None else cls._as_bool(value)

    @staticmethod
    def _bsimpl_2_Strategy1_Debug__env_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    def _bsimpl_3_Strategy1_Research_initialize(self) -> None:
        self._research_ready = False
        self._research_early: list[dict[str, Any]] = []
        self._rq = None
        self._rstop = None
        self._rworker = None
        self._rfile = None
        self._rdropped = 0
        # Resolve the research switch before Debug.initialize() calls self._emit().
        # This lets the production no-log path avoid even early telemetry records.
        cfg = self.config
        self.research_enabled = self._env_bool(
            'STRATEGY1_RESEARCH',
            self._as_bool(getattr(cfg, 'research_enabled', False)),
        )
        self._bsimpl_2_Strategy1_Debug_initialize()
        cfg = self.config
        self.research_fix_global_stress = self._as_bool(getattr(cfg, 'research_fix_global_stress', True))
        self.research_neutral_fallback = self._as_bool(getattr(cfg, 'research_neutral_fallback', True))
        self.research_adaptive_spread_thresholds = self._as_bool(getattr(cfg, 'research_adaptive_spread_thresholds', True))
        self.research_stress_percentile = min(0.999, max(0.5, float(getattr(cfg, 'research_stress_percentile', 0.95))))
        self.research_toxic_percentile = min(0.9999, max(self.research_stress_percentile, float(getattr(cfg, 'research_toxic_percentile', 0.99))))
        self.research_stress_floor_bps = max(0.0, float(getattr(cfg, 'research_stress_floor_bps', 8.0)))
        self.research_toxic_floor_bps = max(0.0, float(getattr(cfg, 'research_toxic_floor_bps', 10.0)))
        self.research_stress_fallback_bps = max(self.research_stress_floor_bps, float(getattr(cfg, 'research_stress_fallback_bps', 35.0)))
        self.research_toxic_fallback_bps = max(self.research_toxic_floor_bps, float(getattr(cfg, 'research_toxic_fallback_bps', 40.0)))
        self.research_toxic_gap_bps = max(0.0, float(getattr(cfg, 'research_toxic_gap_bps', 2.0)))
        self.research_min_profiles_for_adaptive = max(4, int(getattr(cfg, 'research_min_profiles_for_adaptive', 16)))
        self.research_inactive_bootstrap = self._as_bool(getattr(cfg, 'research_inactive_bootstrap', True))
        self.research_trade_global_stress = self._as_bool(getattr(cfg, 'research_trade_global_stress', True))
        self.research_global_stress_size_mult = min(1.0, max(0.05, float(getattr(cfg, 'research_global_stress_size_mult', 0.35))))
        self.research_sync_min_order = self._as_bool(getattr(cfg, 'research_sync_min_order', True))
        self.research_promote_min_order = self._as_bool(getattr(cfg, 'research_promote_min_order', True))
        self.research_bootstrap_maintenance_min_order = self._as_bool(getattr(cfg, 'research_bootstrap_maintenance_min_order', True))
        self.research_bootstrap_dead_as_mm = self._as_bool(getattr(cfg, 'research_bootstrap_dead_as_mm', True))
        self.research_bootstrap_extreme_vol_mult = max(1.0, float(getattr(cfg, 'research_bootstrap_extreme_vol_mult', 1.75)))
        self.research_fix_inventory_util = self._as_bool(getattr(cfg, 'research_fix_inventory_util', True))
        self.research_fix_quote_reservation = self._as_bool(getattr(cfg, 'research_fix_quote_reservation', True))
        self.research_bootstrap_manage_min_clip = self._as_bool(getattr(cfg, 'research_bootstrap_manage_min_clip', True))
        self.research_bootstrap_allow_aggressive_close = self._as_bool(getattr(cfg, 'research_bootstrap_allow_aggressive_close', True))
        self.research_bootstrap_force_close_ticks = max(1, int(getattr(cfg, 'research_bootstrap_force_close_ticks', 60)))
        self.research_bootstrap_force_close_min_bps = float(getattr(cfg, 'research_bootstrap_force_close_min_bps', -5.0))
        self.research_bootstrap_hard_close_ticks = max(self.research_bootstrap_force_close_ticks, int(getattr(cfg, 'research_bootstrap_hard_close_ticks', 180)))
        self.research_dust_safe_close = self._as_bool(getattr(cfg, 'research_dust_safe_close', True))
        self.research_rotate_jsonl = self._as_bool(getattr(cfg, 'research_rotate_jsonl', True))
        self.research_candidate_backfill = self._as_bool(getattr(cfg, 'research_candidate_backfill', True))
        self.research_candidate_attempt_cap = max(1, int(getattr(cfg, 'research_candidate_attempt_cap', 12)))
        self.research_aggressive_close_touch_gate = self._as_bool(getattr(cfg, 'research_aggressive_close_touch_gate', True))
        self.research_aggressive_close_fee_buffer_bps = max(0.0, float(getattr(cfg, 'research_aggressive_close_fee_buffer_bps', 3.0)))
        self.research_aggressive_close_min_net_bps = float(getattr(cfg, 'research_aggressive_close_min_net_bps', 0.0))
        self.research_toxic_pnl_min_samples = max(0, int(getattr(cfg, 'research_toxic_pnl_min_samples', 3)))
        self.research_toxic_pnl_hard_floor = float(getattr(cfg, 'research_toxic_pnl_hard_floor', -0.05))
        self.research_yellow_sparse_active = self._as_bool(getattr(cfg, 'research_yellow_sparse_active', True))
        self.research_green_sparse_active = self._as_bool(getattr(cfg, 'research_green_sparse_active', True))
        self.research_dust_park_enabled = self._as_bool(getattr(cfg, 'research_dust_park_enabled', True))
        self.research_dust_heartbeat_ticks = max(1, int(getattr(cfg, 'research_dust_heartbeat_ticks', 250)))
        self.research_dust_warn_ticks = max(self.research_dust_heartbeat_ticks, int(getattr(cfg, 'research_dust_warn_ticks', 1000)))
        self.research_dust_compact_enabled = self._as_bool(getattr(cfg, 'research_dust_compact_enabled', True))
        self.research_dust_compact_min_fraction = max(0.500001, min(0.95, float(getattr(cfg, 'research_dust_compact_min_fraction', 0.5))))
        self.research_dust_compact_books_per_tick = max(1, int(getattr(cfg, 'research_dust_compact_books_per_tick', 2)))
        self.research_kappa_completion_enabled = self._as_bool(getattr(cfg, 'research_kappa_completion_enabled', True))
        self.research_kappa_completion_target = required_observation_count(
            kappa_min_observations=getattr(self, 'kappa_min_observations', None),
            research_target=getattr(cfg, 'research_kappa_completion_target', None),
        )
        self.research_kappa_completion_rank_bonus = max(0.0, float(getattr(cfg, 'research_kappa_completion_rank_bonus', 0.3)))
        self.research_kappa_completion_fill_mult = max(0.5, min(1.0, float(getattr(cfg, 'research_kappa_completion_fill_mult', 0.7))))
        self.research_kappa_completion_fill_floor = max(0.0, float(getattr(cfg, 'research_kappa_completion_fill_floor', 0.1)))
        self.research_kappa_completion_relaxed_success_cap = max(0, int(getattr(cfg, 'research_kappa_completion_relaxed_success_cap', 2)))
        requested_completion_attempt_cap = max(0, int(getattr(cfg, 'research_kappa_completion_attempt_cap', 4)))
        self.research_kappa_completion_attempt_cap = min(self.research_candidate_attempt_cap, requested_completion_attempt_cap)
        self.research_normal_attempt_cap = max(0, self.research_candidate_attempt_cap - self.research_kappa_completion_attempt_cap)
        requested_completion_success_cap = max(0, int(getattr(cfg, 'research_kappa_completion_success_cap', 2)))
        self.research_kappa_completion_success_cap = min(int(self.max_mm_books_per_tick), requested_completion_success_cap)
        self.research_kappa_completion_relaxed_success_cap = min(self.research_kappa_completion_relaxed_success_cap, self.research_kappa_completion_success_cap)
        self.research_kappa_completion_recent_pnl_floor = float(getattr(cfg, 'research_kappa_completion_recent_pnl_floor', -0.01))
        self.score_ev_enabled = self._as_bool(getattr(cfg, 'score_ev_enabled', True))
        self.score_ev_min_trading = float(getattr(cfg, 'score_ev_min_trading', 0.0))
        self.score_ev_one_away_weight = max(0.0, float(getattr(cfg, 'score_ev_one_away_weight', 0.18)))
        self.score_ev_two_away_weight = max(0.0, float(getattr(cfg, 'score_ev_two_away_weight', 0.06)))
        self.score_ev_new_book_weight = max(0.0, float(getattr(cfg, 'score_ev_new_book_weight', 0.0)))
        self.score_ev_dust_weight = max(0.0, float(getattr(cfg, 'score_ev_dust_weight', 0.25)))
        self.score_ev_dust_target = max(0.0, min(1.0, float(getattr(cfg, 'score_ev_dust_target', 0.15))))
        self.score_ev_fees_bps = max(0.0, float(getattr(cfg, 'score_ev_fees_bps', 0.5)))
        self.score_ev_min_fill_samples = max(1, int(getattr(cfg, 'score_ev_min_fill_samples', 8)))
        self.score_ev_min_markout_samples = max(1, int(getattr(cfg, 'score_ev_min_markout_samples', 8)))
        self._score_ev_last: dict[int, Any] = {}
        self.quote_hysteresis_enabled = self._as_bool(getattr(cfg, 'quote_hysteresis_enabled', True))
        self.hysteresis_min_price_ticks = max(0.25, float(getattr(cfg, 'hysteresis_min_price_ticks', 1.0)))
        self.hysteresis_ev_threshold = max(0.0, float(getattr(cfg, 'hysteresis_ev_threshold', 0.04)))
        self.adaptive_ttl_enabled = self._as_bool(getattr(cfg, 'adaptive_ttl_enabled', True))
        _baseline_ttl_ms = sim_delta_ms(0, int(getattr(self, 'mm_expiry_period', 500000000) or 500000000)) or 500.0
        self.ttl_min_ms = max(50.0, float(getattr(cfg, 'ttl_min_ms', 200.0)))
        self.ttl_max_ms = max(self.ttl_min_ms, float(getattr(cfg, 'ttl_max_ms', max(800.0, _baseline_ttl_ms))))
        self.dust_prevent_enabled = self._as_bool(getattr(cfg, 'dust_prevent_enabled', True))
        self._hysteresis_holds = 0
        self._hysteresis_replaces = 0
        self._ttl_stale_skips = 0
        self._dust_creation_count = 0
        self._dust_prevent_skips = 0
        self._quote_submit_snapshot: dict[int, dict[str, Any]] = {}
        self.research_run_id = time.strftime('%Y%m%d_%H%M%S', time.localtime())
        self._research_stress_spread_bps = self.research_stress_fallback_bps
        self._research_toxic_spread_bps = max(self.research_toxic_fallback_bps, self._research_stress_spread_bps + self.research_toxic_gap_bps)
        self._research_exchange_min_order_size = max(0.0, float(getattr(self, 'min_order_size', 0.0) or 0.0))
        self._research_bootstrap_active = False
        self._research_position_tick_seen: dict[int, int] = {}
        self._research_round_trip_closes = 0
        self._research_position_opens = 0
        self._research_position_reductions = 0
        self._research_dust_blocks = 0
        self._research_round_trip_samples_by_book: dict[int, int] = {}
        self._research_realized_observations_by_book: dict[int, int] = {}
        self._research_parked_dust: dict[int, dict[str, Any]] = {}
        self._regime_v2_thresholds = RegimeV2Thresholds()
        self._market_regime_debounce = DebounceState('NORMAL', 'NORMAL', 0)
        self._score_regime_debounce = DebounceState('NORMAL', 'NORMAL', 0)
        self._market_regime = 'NORMAL'
        self._score_regime = 'NORMAL'
        self.execution_hazard_enabled = self._as_bool(getattr(cfg, 'execution_hazard_enabled', True))
        self.execution_hazard_use_for_policy = self._as_bool(getattr(cfg, 'execution_hazard_use_for_policy', True))
        self._execution_observe_cap = max(64, int(getattr(cfg, 'execution_hazard_observe_cap', 8192)))
        self._execution_quotes = QuoteLifecycleStore(max_live=1024, max_pending_markouts=16)
        self._execution_hazard = FillHazardModel()
        self._execution_last: dict[int, dict[str, Any]] = {}
        self._execution_fills_classified = 0
        self._research_dust_entries = 0
        self._research_dust_releases = 0
        self._research_dust_heartbeats = 0
        self._research_dust_compact_ids_this_tick: set[int] = set()
        self._research_dust_compact_attempts = 0
        self._research_dust_compact_orders = 0
        self._research_dust_compact_fills = 0
        self._research_dust_compact_active: dict[int, int] = {}
        self._research_volume_decimals = 8
        self._research_backfill_active = False
        self._research_quote_success_cap = 0
        self._research_quote_successes = 0
        self._research_quote_attempts = 0
        self._research_normal_quote_attempts = 0
        self._research_normal_quote_successes = 0
        self._research_completion_relaxed_successes = 0
        self._research_completion_relaxed_attempts = 0
        self._research_completion_quote_attempts = 0
        self._research_completion_quote_successes = 0
        self._research_completion_attempt_cap_hits = 0
        self._research_completion_success_cap_hits = 0
        self._research_normal_attempt_cap_hits = 0
        self._research_aggressive_context: dict[int, dict[str, Any]] = {}
        # research_enabled resolved before Debug.initialize()
        self.research_every_n = max(1, self._env_int('STRATEGY1_RESEARCH_EVERY_N', int(getattr(cfg, 'research_every_n', 1))))
        self.research_book_id = self._env_int('STRATEGY1_RESEARCH_BOOK', int(getattr(cfg, 'research_book_id', -1)))
        self.research_console = self._env_bool('STRATEGY1_RESEARCH_CONSOLE', self._as_bool(getattr(cfg, 'research_console', True)))
        self.research_jsonl = self._env_bool('STRATEGY1_RESEARCH_JSONL', self._as_bool(getattr(cfg, 'research_jsonl', True)))
        self.research_queue_size = max(256, self._env_int('STRATEGY1_RESEARCH_QUEUE', int(getattr(cfg, 'research_queue_size', 8192))))
        env_dir = os.getenv('STRATEGY1_RESEARCH_DIR', '').strip()
        configured = str(getattr(cfg, 'research_output_dir', '') or '')
        self.research_output_dir = env_dir or configured or os.path.join(self.output_dir, 'strategy1_research')
        self._rq = queue.Queue(maxsize=self.research_queue_size) if self.research_enabled else None
        self._rstop = threading.Event() if self.research_enabled else None
        self._research_output_file = None
        if self.research_enabled and self.research_jsonl:
            try:
                os.makedirs(self.research_output_dir, exist_ok=True)
                filename = f'strategy1_research_agent_{self.uid}_{self.research_run_id}.jsonl' if self.research_rotate_jsonl else f'strategy1_research_agent_{self.uid}.jsonl'
                path = os.path.join(self.research_output_dir, filename)
                self._research_output_file = path
                self._rfile = open(path, 'a', encoding='utf-8', buffering=1)
            except OSError as exc:
                print(f'[S1R_ERROR] stage=init_jsonl error={self._short(exc)}', flush=True)
        if self.research_enabled:
            self._rworker = threading.Thread(target=self._writer_loop, name=f"s1r-{getattr(self, 'uid', 'agent')}", daemon=True)
            self._rworker.start()
            atexit.register(self._shutdown_research)
        self._research_ready = True
        if self.research_enabled:
            for record in self._research_early:
                self._enqueue(record)
        self._research_early.clear()
        if self.research_enabled:
            self._enqueue({'type': 'RESEARCH_CONFIG', 'agent_id': getattr(self, 'uid', None), 'wall_time_ns': time.time_ns(), 'enabled': self.research_enabled, 'every_n': self.research_every_n, 'book_filter': self.research_book_id, 'console': self.research_console, 'jsonl': self.research_jsonl, 'queue_size': self.research_queue_size, 'output_dir': self.research_output_dir, 'policy_version': 'deadlock_fix_v4_1_1_strict', 'fix_global_stress': self.research_fix_global_stress, 'neutral_fallback': self.research_neutral_fallback, 'adaptive_spread_thresholds': self.research_adaptive_spread_thresholds, 'stress_percentile': self.research_stress_percentile, 'toxic_percentile': self.research_toxic_percentile, 'inactive_bootstrap': self.research_inactive_bootstrap, 'trade_global_stress': self.research_trade_global_stress, 'sync_min_order': self.research_sync_min_order, 'promote_min_order': self.research_promote_min_order, 'bootstrap_dead_as_mm': self.research_bootstrap_dead_as_mm, 'fix_inventory_util': self.research_fix_inventory_util, 'fix_quote_reservation': self.research_fix_quote_reservation, 'bootstrap_manage_min_clip': self.research_bootstrap_manage_min_clip, 'bootstrap_force_close_ticks': self.research_bootstrap_force_close_ticks, 'legacy_force_close_min_bps': self.research_bootstrap_force_close_min_bps, 'legacy_hard_close_ticks': self.research_bootstrap_hard_close_ticks, 'aggressive_close_touch_gate': self.research_aggressive_close_touch_gate, 'aggressive_close_fee_buffer_bps': self.research_aggressive_close_fee_buffer_bps, 'aggressive_close_min_net_bps': self.research_aggressive_close_min_net_bps, 'candidate_backfill': self.research_candidate_backfill, 'candidate_attempt_cap': self.research_candidate_attempt_cap, 'toxic_pnl_min_samples': self.research_toxic_pnl_min_samples, 'toxic_pnl_hard_floor': self.research_toxic_pnl_hard_floor, 'yellow_sparse_active': self.research_yellow_sparse_active, 'green_sparse_active': self.research_green_sparse_active, 'dust_safe_close': self.research_dust_safe_close, 'dust_park_enabled': self.research_dust_park_enabled, 'dust_heartbeat_ticks': self.research_dust_heartbeat_ticks, 'dust_warn_ticks': self.research_dust_warn_ticks, 'dust_compact_enabled': self.research_dust_compact_enabled, 'dust_compact_min_fraction': self.research_dust_compact_min_fraction, 'dust_compact_books_per_tick': self.research_dust_compact_books_per_tick, 'kappa_completion_enabled': self.research_kappa_completion_enabled, 'kappa_completion_target': self.research_kappa_completion_target, 'kappa_completion_rank_bonus': self.research_kappa_completion_rank_bonus, 'kappa_completion_fill_mult': self.research_kappa_completion_fill_mult, 'kappa_completion_fill_floor': self.research_kappa_completion_fill_floor, 'kappa_completion_relaxed_success_cap': self.research_kappa_completion_relaxed_success_cap, 'kappa_completion_attempt_cap': self.research_kappa_completion_attempt_cap, 'kappa_completion_success_cap': self.research_kappa_completion_success_cap, 'normal_attempt_cap': self.research_normal_attempt_cap, 'kappa_completion_recent_pnl_floor': self.research_kappa_completion_recent_pnl_floor, 'rotate_jsonl': self.research_rotate_jsonl, 'run_id': self.research_run_id, 'output_file': self._research_output_file, 'base_deploy_policy_version': self.DEPLOY_POLICY_VERSION, 'regime_policy_version': self.REGIME_POLICY_VERSION, 'mm_force_post_only': self.mm_force_post_only, 'mm_maker_guard_reprice': self.mm_maker_guard_reprice, 'debug_slow_request_ms': self.debug_slow_request_ms})

    @staticmethod
    def _bsimpl_3_Strategy1_Research__percentile(values: list[float], q: float) -> float | None:
        """Small-N linear percentile with no NumPy dependency."""
        if not values:
            return None
        xs = sorted((float(v) for v in values))
        if len(xs) == 1:
            return xs[0]
        pos = (len(xs) - 1) * min(1.0, max(0.0, q))
        lo = int(pos)
        hi = min(lo + 1, len(xs) - 1)
        frac = pos - lo
        return xs[lo] * (1.0 - frac) + xs[hi] * frac

    @staticmethod
    def _bsimpl_3_Strategy1_Research__profile_float(profile: Any, name: str) -> float | None:
        try:
            value = getattr(profile, name, None)
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _bsimpl_3_Strategy1_Research__update_spread_thresholds(self, profiles: list[BookProfile]) -> None:
        spreads = [value for profile in profiles if (value := self._profile_float(profile, 'spread_bps')) is not None and value >= 0.0]
        if self.research_adaptive_spread_thresholds and len(spreads) >= self.research_min_profiles_for_adaptive:
            p_stress = self._percentile(spreads, self.research_stress_percentile)
            p_toxic = self._percentile(spreads, self.research_toxic_percentile)
            stress = max(self.research_stress_floor_bps, p_stress if p_stress is not None else self.research_stress_fallback_bps)
            toxic = max(self.research_toxic_floor_bps, p_toxic if p_toxic is not None else self.research_toxic_fallback_bps, stress + self.research_toxic_gap_bps)
        else:
            stress = self.research_stress_fallback_bps
            toxic = max(self.research_toxic_fallback_bps, stress + self.research_toxic_gap_bps)
        self._research_stress_spread_bps = float(stress)
        self._research_toxic_spread_bps = float(toxic)

    def _bsimpl_3_Strategy1_Research__sync_exchange_constraints(self, state: MarketSimulationStateUpdate) -> None:
        cfg = getattr(state, 'config', None)
        try:
            self._research_volume_decimals = max(0, int(getattr(cfg, 'volumeDecimals', self._research_volume_decimals)))
        except (TypeError, ValueError):
            pass
        if not self.research_sync_min_order:
            return
        try:
            state_min = float(getattr(cfg, 'min_order_size', 0.0) or 0.0)
        except (TypeError, ValueError):
            state_min = 0.0
        if state_min > 0.0:
            self._research_exchange_min_order_size = state_min
            self.min_order_size = state_min
        else:
            self._research_exchange_min_order_size = max(0.0, float(getattr(self, 'min_order_size', 0.0) or 0.0))

    def _bsimpl_3_Strategy1_Research__execution_flat_epsilon(self) -> float:
        """Half one quantity tick; only sub-half-tick residuals are execution-flat."""
        return max(0.5 * 10.0 ** (-int(self._research_volume_decimals)), 1e-12)

    def _regime_v2_snapshot(self, profile_list, predictions, selection) -> dict[str, Any]:
        """One pass over already-built profiles/predictions. No extra L2 scans."""
        n = len(profile_list)
        spreads: list[float] = []
        vols: list[float] = []
        rates: list[float] = []
        spread_rate: list[tuple[float | None, float | None]] = []
        inactive = 0
        red = 0
        green = 0
        imb_sum = 0.0
        target = int(getattr(self, 'research_kappa_completion_target', 3))
        obs_map = getattr(self, '_research_realized_observations_by_book', {}) or {}
        kappa_on = getattr(self, 'research_kappa_completion_enabled', True)
        pending = 0
        for profile in profile_list:
            tier = str(getattr(profile, 'tier', '')).upper()
            if tier == 'INACTIVE':
                inactive += 1
            elif tier == 'RED':
                red += 1
            elif tier == 'GREEN':
                green += 1
            spread = self._profile_float(profile, 'spread_bps')
            vol = self._profile_float(profile, 'volatility')
            rate = self._profile_float(profile, 'trade_rate')
            imb = self._profile_float(profile, 'imbalance')
            if spread is not None:
                spreads.append(spread)
            if vol is not None:
                vols.append(vol)
            if rate is not None:
                rates.append(rate)
            if imb is not None:
                imb_sum += imb
            spread_rate.append((spread, rate))
            if kappa_on:
                try:
                    nobs = int(obs_map.get(int(getattr(profile, 'book_id')), 0) or 0)
                except (TypeError, ValueError):
                    nobs = 0
                if 0 < nobs < target:
                    pending += 1

        if self.research_adaptive_spread_thresholds and len(spreads) >= self.research_min_profiles_for_adaptive:
            p_stress = self._percentile(spreads, self.research_stress_percentile)
            p_toxic = self._percentile(spreads, self.research_toxic_percentile)
            stress = max(self.research_stress_floor_bps, p_stress if p_stress is not None else self.research_stress_fallback_bps)
            toxic = max(self.research_toxic_floor_bps, p_toxic if p_toxic is not None else self.research_toxic_fallback_bps, stress + self.research_toxic_gap_bps)
        else:
            stress = self.research_stress_fallback_bps
            toxic = max(self.research_toxic_fallback_bps, stress + self.research_toxic_gap_bps)
        self._research_stress_spread_bps = float(stress)
        self._research_toxic_spread_bps = float(toxic)

        dead_rate = float(getattr(self, 'archetype_dead_trade_rate', 0.0) or 0.0)
        stressed_count = 0
        liquid_count = 0
        for spread, rate in spread_rate:
            if spread is not None and spread >= stress:
                stressed_count += 1
            if (spread or 0.0) < stress and (rate or 0.0) >= dead_rate:
                liquid_count += 1

        pred_values = list(predictions.values()) if isinstance(predictions, dict) else []
        pred_n = max(len(pred_values), 1)
        up = 0
        down = 0
        hold = 0
        score_abs = 0.0
        score_n = 0
        for pred in pred_values:
            direction = str(getattr(pred, 'direction', '')).upper()
            if direction == 'UP':
                up += 1
            elif direction == 'DOWN':
                down += 1
            elif direction == 'HOLD':
                hold += 1
            try:
                score_abs += abs(float(getattr(pred, 'score', 0.0) or 0.0))
                score_n += 1
            except (TypeError, ValueError):
                pass

        denom = max(n, 1)
        return {
            'book_count': n,
            'active': max(0, n - inactive),
            'inactive': inactive,
            'inactive_frac': inactive / denom,
            'red_frac': red / denom,
            'green_frac': green / denom,
            'spread_med': self._percentile(spreads, 0.5),
            'vol_med': self._percentile(vols, 0.5),
            'trade_rate_med': self._percentile(rates, 0.5),
            'liquid_ratio': liquid_count / denom,
            'stressed_ratio': stressed_count / denom,
            'trend_up_ratio': up / pred_n,
            'trend_down_ratio': down / pred_n,
            'hold_frac': hold / pred_n,
            'up_frac': up / pred_n,
            'down_frac': down / pred_n,
            'mean_abs_score': (score_abs / score_n) if score_n else 0.0,
            'mean_volatility': (sum(vols) / len(vols)) if vols else 0.0,
            'mean_trade_rate': (sum(rates) / len(rates)) if rates else 0.0,
            'mean_spread_bps': (sum(spreads) / len(spreads)) if spreads else None,
            'mean_imbalance': imb_sum / denom,
            'pending_kappa_frac': pending / denom,
            'stress_spread_bps': float(stress),
            'toxic_spread_bps': float(toxic),
            'tier_counts': dict(getattr(selection, 'tier_counts', {}) or {}),
        }

    def _build_market_regime_from_v2(self, snapshot: dict[str, Any], decision) -> MarketRegime:
        n = int(snapshot.get('book_count', 0) or 0)
        return MarketRegime(
            mode=decision.parent_mode,
            hold_frac=float(snapshot.get('hold_frac', 0.0) or 0.0),
            up_frac=float(snapshot.get('up_frac', 0.0) or 0.0),
            down_frac=float(snapshot.get('down_frac', 0.0) or 0.0),
            mean_score=0.0,
            mean_abs_score=float(snapshot.get('mean_abs_score', 0.0) or 0.0),
            mean_volatility=float(snapshot.get('mean_volatility', 0.0) or 0.0),
            mean_trade_rate=float(snapshot.get('mean_trade_rate', 0.0) or 0.0),
            mean_spread_bps=snapshot.get('mean_spread_bps'),
            mean_imbalance=float(snapshot.get('mean_imbalance', 0.0) or 0.0),
            mean_log_return=None,
            return_dispersion=None,
            direction_dispersion=0.0,
            tier_counts=dict(snapshot.get('tier_counts') or {}),
            inactive_frac=float(snapshot.get('inactive_frac', 0.0) or 0.0),
            red_frac=float(snapshot.get('red_frac', 0.0) or 0.0),
            green_frac=float(snapshot.get('green_frac', 0.0) or 0.0),
            scoring_overlay=decision.scoring_overlay,
            confidence=min(1.0, 0.35 + 0.65 * float(snapshot.get('liquid_ratio', 0.0) or 0.0)),
            book_count=n,
        )

    def _bsimpl_3_Strategy1_Research_classify_market_regime_from_profiles(self, profiles, predictions, selection):
        """MarketRegime V2 + independent ScoreRegime. Parent 5 bps mean-spread latch is not used."""
        started = time.perf_counter()
        profile_list = list(profiles or [])
        snapshot = self._regime_v2_snapshot(profile_list, predictions, selection)
        decision = classify_regime_v2(
            snapshot,
            market_state=getattr(self, '_market_regime_debounce', None),
            score_state=getattr(self, '_score_regime_debounce', None),
            thresholds=getattr(self, '_regime_v2_thresholds', None),
        )
        self._market_regime_debounce = decision.market_debounce
        self._score_regime_debounce = decision.score_debounce
        self._market_regime = decision.market_regime
        self._score_regime = decision.score_regime
        regime = self._build_market_regime_from_v2(snapshot, decision)
        try:
            setattr(regime, 'market_regime', decision.market_regime)
            setattr(regime, 'score_regime', decision.score_regime)
            setattr(regime, 'market_trigger', decision.market_trigger)
            setattr(regime, 'score_trigger', decision.score_trigger)
        except Exception:
            pass
        self._last_regime = regime
        if getattr(self, 'debug_enabled', False):
            self._debug_current_regime = regime
        if getattr(self, '_debug_stage_ms', None) is not None:
            self._debug_stage_ms['classify_regime_ms'] = (time.perf_counter() - started) * 1000.0
        self._emit(
            'REGIME',
            tick=self._tick,
            market_regime=decision.market_regime,
            score_regime=decision.score_regime,
            mode=decision.parent_mode,
            overlay=decision.scoring_overlay,
            book_count=snapshot['book_count'],
            inactive=snapshot['inactive'],
            stressed_ratio=snapshot['stressed_ratio'],
            liquid_ratio=snapshot['liquid_ratio'],
            spread_med=snapshot['spread_med'],
            trade_rate_med=snapshot['trade_rate_med'],
            pending_kappa_frac=snapshot['pending_kappa_frac'],
            market_trigger=decision.market_trigger,
            score_trigger=decision.score_trigger,
        )
        return regime

    def _bsimpl_3_Strategy1_Research_get_regime_params(self, regime: MarketRegime) -> RegimeParamSet:
        params = self._bsimpl_1_Strategy1_get_regime_params(regime)
        mode = str(getattr(regime, 'mode', '')).upper()
        overlay = str(getattr(regime, 'scoring_overlay', '')).upper()
        if self.research_trade_global_stress and mode == 'STRESSED' and (overlay != 'SCORING_PRESSURE'):
            return RegimeParamSet(quote_enabled=True, alpha_enabled=False, spread_offset=max(float(params.spread_offset), 0.45), skew_strength=min(float(params.skew_strength), 0.05), size_mult=min(float(params.size_mult), self.research_global_stress_size_mult), profit_target_bps=params.profit_target_bps, stop_loss_bps=params.stop_loss_bps, min_fill_prob=params.min_fill_prob, buy_bias=params.buy_bias, sell_bias=params.sell_bias)
        return params

    def _bsimpl_3_Strategy1_Research__mem(self, book_id: int):
        """Attach the book id to parent BookMemory for completion-aware ranking."""
        mem = self._bsimpl_1_Strategy1__mem(book_id)
        try:
            setattr(mem, '_research_book_id', int(book_id))
        except Exception:
            pass
        return mem

    def _bsimpl_3_Strategy1_Research__completion_observation_count(self, book_id: int) -> int:
        return int(self._research_realized_observations_by_book.get(int(book_id), 0))

    def _required_observation_count(self) -> int:
        return required_observation_count(
            kappa_min_observations=getattr(self, 'kappa_min_observations', None),
            research_target=getattr(self, 'research_kappa_completion_target', None),
        )

    def _observations_remaining(self, book_id: int) -> int:
        required = self._required_observation_count()
        realized = self._completion_observation_count(book_id)
        return max(0, required - int(realized))

    def _bsimpl_3_Strategy1_Research__is_kappa_completion_candidate(self, book_id: int) -> bool:
        if not self.research_kappa_completion_enabled:
            return False
        samples = self._completion_observation_count(book_id)
        target = self._required_observation_count()
        if samples <= 0 or samples >= target:
            return False
        mem = self._mem(book_id)
        return float(getattr(mem, 'recent_pnl', 0.0) or 0.0) >= self.research_kappa_completion_recent_pnl_floor

    def _score_ev_for_book(self, book_id: int, expected_alpha: float, mem):
        profile = self._execution_profile_for_book(book_id)
        spread_bps = 0.0
        if profile is not None:
            try:
                spread_bps = float(getattr(profile, 'spread_bps', 0.0) or 0.0)
            except (TypeError, ValueError):
                spread_bps = 0.0
        last = (getattr(self, '_execution_last', {}) or {}).get(int(book_id), {})
        pred_buy = last.get('buy')
        pred_sell = last.get('sell')
        old = last.get('legacy')
        fill_old = float(getattr(mem, 'fill_rate', 0.0) or 0.0)
        if old is not None:
            fill_old = 0.5 * (float(getattr(old, 'buy', 0.0)) + float(getattr(old, 'sell', 0.0)))
        hazard_any = None
        hazard_act = None
        hazard_dust = None
        hazard_usable = False
        if pred_buy is not None or pred_sell is not None:
            anys = []
            acts = []
            dusts = []
            usable = False
            for pred in (pred_buy, pred_sell):
                if pred is None:
                    continue
                anys.append(float(pred.any_fill))
                acts.append(float(pred.actionable_fill))
                dusts.append(float(pred.dust))
                usable = usable or bool(pred.usable)
            if anys:
                hazard_any = sum(anys) / len(anys)
                hazard_act = sum(acts) / len(acts)
                hazard_dust = sum(dusts) / len(dusts)
                hazard_usable = usable
        dust_prob = 0.0 if hazard_dust is None or not hazard_usable else float(hazard_dust)
        inv_util = 0.0
        inventory_blocked = False
        try:
            snap = self._position_tracker_snapshot(int(book_id))
            cap = max(float(getattr(self, 'max_inventory_base', 1.0) or 1.0), 1e-9)
            inv_util = min(1.0, abs(float(getattr(snap, 'net_qty', 0.0) or 0.0)) / cap)
            inventory_blocked = inv_util + 1e-12 >= 1.0
        except Exception:
            pass
        toxic = int(book_id) in getattr(self, '_research_parked_dust', {})
        market_regime = str(getattr(self, '_market_regime', '') or '').upper()
        unsafe = market_regime in {'TOXIC'}
        side = 'MM'
        if inv_util > 0.05:
            net = 0.0
            try:
                net = float(self._position_tracker_snapshot(int(book_id)).net_qty)
            except Exception:
                net = 0.0
            side = 'SELL' if net > 0 else 'BUY'
        return compute_score_ev(
            book=int(book_id),
            side=side,
            alpha=float(expected_alpha),
            fill_prob_old=float(fill_old),
            fill_prob_hazard=hazard_any,
            actionable_fill_hazard=hazard_act,
            hazard_usable=hazard_usable,
            dust_prob=dust_prob,
            spread_capture_bps=0.5 * max(0.0, spread_bps),
            fees_bps=float(self.score_ev_fees_bps),
            realized_observation_count=self._completion_observation_count(book_id),
            required=self._required_observation_count(),
            inventory_util=inv_util,
            toxic=bool(toxic),
            inventory_blocked=bool(inventory_blocked),
            unsafe=bool(unsafe),
            min_trading_ev=float(self.score_ev_min_trading),
            min_fill_samples=int(self.score_ev_min_fill_samples),
            min_markout_samples=int(self.score_ev_min_markout_samples),
            one_away_weight=float(self.score_ev_one_away_weight),
            two_away_weight=float(self.score_ev_two_away_weight),
            new_book_weight=float(self.score_ev_new_book_weight),
            dust_target=float(self.score_ev_dust_target),
            dust_weight=float(self.score_ev_dust_weight),
        )

    def _bsimpl_3_Strategy1_Research__global_book_rank(self, expected_alpha: float, mem) -> float:
        """Score-EV rank, or V4.1 legacy rank when the feature flag is off."""
        book_id = getattr(mem, '_research_book_id', None)
        if book_id is None:
            return self._bsimpl_1_Strategy1__global_book_rank(expected_alpha, mem)
        book_id = int(book_id)
        if not getattr(self, 'score_ev_enabled', True):
            base_rank = self._bsimpl_1_Strategy1__global_book_rank(expected_alpha, mem)
            if not self.research_kappa_completion_enabled or not self._is_kappa_completion_candidate(book_id):
                return base_rank
            samples = self._completion_observation_count(book_id)
            denom = max(1, self._required_observation_count() - 1)
            progress = max(0.0, min(1.0, samples / denom))
            return base_rank + self.research_kappa_completion_rank_bonus * progress
        breakdown = self._score_ev_for_book(book_id, expected_alpha, mem)
        self._score_ev_last[book_id] = breakdown
        if getattr(self, 'debug_enabled', False) or getattr(self, 'research_enabled', False):
            self._emit(
                'SCORE_EV',
                force=True,
                tick=getattr(self, '_tick', None),
                book=book_id,
                trading_ev=breakdown.trading_ev,
                completion_value=breakdown.completion_value,
                dust_cost=breakdown.dust_cost,
                inventory_cost=breakdown.inventory_cost,
                latency_cost=breakdown.latency_cost,
                final_score=None if not math.isfinite(breakdown.final_score) else breakdown.final_score,
                observation_count=breakdown.observation_count,
                required_observation_count=breakdown.required_observation_count,
                observations_remaining=breakdown.observations_remaining,
                actionable_fill_prob=breakdown.actionable_fill_prob,
                dust_prob=breakdown.dust_prob,
                alpha=breakdown.alpha,
                eligible=int(breakdown.eligible),
                reject_reason=breakdown.reject_reason or '',
            )
        chosen = select_rank(
            enable_score_ev=True,
            score_ev=breakdown,
            legacy_rank=self._bsimpl_1_Strategy1__global_book_rank(expected_alpha, mem),
        )
        if chosen is None:
            return float('-1e9')
        return float(chosen)

    def _bsimpl_3_Strategy1_Research__is_compactable_dust(self, net_base: float) -> bool:
        if not self.research_dust_compact_enabled or not self._is_dust_qty(net_base):
            return False
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        if min_size <= 0.0:
            return False
        abs_base = abs(float(net_base))
        threshold = self.research_dust_compact_min_fraction * min_size
        return abs_base > threshold + self._execution_flat_epsilon()

    def _bsimpl_3_Strategy1_Research__select_dust_compaction_books(self, state: MarketSimulationStateUpdate) -> set[int]:
        """Pick a tiny bounded set; ordinary executable inventory keeps its 8-slot pool."""
        if not self.research_dust_compact_enabled:
            return set()
        tick = int(getattr(self, '_tick', 0) or 0)
        rows: list[tuple[float, int, int]] = []
        for book_id, info in self._research_parked_dust.items():
            qty = float(info.get('net_base', 0.0) or 0.0)
            if not self._is_compactable_dust(qty):
                continue
            if book_id not in getattr(state, 'books', {}):
                continue
            first_tick = int(info.get('first_tick', tick))
            age = max(0, tick - first_tick)
            rows.append((abs(qty), age, int(book_id)))
        rows.sort(reverse=True)
        return {book_id for _abs_qty, _age, book_id in rows[:self.research_dust_compact_books_per_tick]}

    def _bsimpl_3_Strategy1_Research__dust_compaction_safe_for_any_fill(self, net_base: float) -> bool:
        """Proof condition for q -> q - sign(q)*f, 0<=f<=min_size."""
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        q = abs(float(net_base))
        return min_size > 0.0 and q > 0.5 * min_size and (q < min_size)

    def _bsimpl_3_Strategy1_Research__sparse_active_tier_enabled(self, tier: str) -> bool:
        tier_u = str(tier or '').upper()
        return tier_u == 'YELLOW' and self.research_yellow_sparse_active or (tier_u == 'GREEN' and self.research_green_sparse_active)

    def _bsimpl_3_Strategy1_Research__is_dust_qty(self, net_base: float) -> bool:
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        abs_base = abs(float(net_base))
        return self.research_dust_safe_close and min_size > 0.0 and (abs_base >= self._execution_flat_epsilon()) and (abs_base + 1e-12 < min_size)

    def _bsimpl_3_Strategy1_Research__refresh_dust_state(self, book_id: int, net_base: float, *, emit: bool=True) -> bool:
        """Park exchange-uncloseable residuals without hiding exact inventory.

        V4.1 Strict does not synthesize an exchange-illegal exact close and does
        not add fresh risk merely to make dust executable. Dust remains in exact
        accounting, is removed from the finite management queue, and is
        quarantined from fresh MM/maintenance orders.
        """
        if not self.research_dust_park_enabled:
            self._research_parked_dust.pop(book_id, None)
            return False
        tick = int(getattr(self, '_tick', 0) or 0)
        qty = float(net_base)
        is_dust = self._is_dust_qty(qty)
        prior = self._research_parked_dust.get(book_id)
        if not is_dust:
            if prior is not None:
                self._research_dust_releases += 1
                self._research_parked_dust.pop(book_id, None)
                if emit:
                    self._emit('POSITION_GUARD', tick=tick, book_id=book_id, reason='DUST_RELEASED', net_base=qty, min_order_size=self._research_exchange_min_order_size, first_tick=prior.get('first_tick'), age_ticks=max(0, tick - int(prior.get('first_tick', tick))), parked=False)
            return False
        if prior is None:
            prior = {'first_tick': tick, 'last_tick': tick, 'last_emit_tick': tick, 'net_base': qty}
            self._research_parked_dust[book_id] = prior
            self._research_dust_entries += 1
            self._research_dust_blocks += 1
            if emit:
                self._emit('POSITION_GUARD', tick=tick, book_id=book_id, reason='DUST_POSITION', net_base=qty, min_order_size=self._research_exchange_min_order_size, first_tick=tick, age_ticks=0, parked=True, stale=False)
            return True
        prior['last_tick'] = tick
        prior['net_base'] = qty
        age = max(0, tick - int(prior.get('first_tick', tick)))
        last_emit = int(prior.get('last_emit_tick', tick))
        if emit and tick - last_emit >= self.research_dust_heartbeat_ticks:
            prior['last_emit_tick'] = tick
            self._research_dust_heartbeats += 1
            self._emit('POSITION_GUARD', tick=tick, book_id=book_id, reason='DUST_HEARTBEAT', net_base=qty, min_order_size=self._research_exchange_min_order_size, first_tick=prior.get('first_tick'), age_ticks=age, parked=True, stale=age >= self.research_dust_warn_ticks)
        return True

    def _bsimpl_3_Strategy1_Research_classify_book_archetype(self, profile: BookProfile, regime: MarketRegime) -> BookArchetype:
        spread_bps = float(getattr(profile, 'spread_bps', 0.0) or 0.0)
        trade_rate = float(getattr(profile, 'trade_rate', 0.0) or 0.0)
        volatility = float(getattr(profile, 'volatility', 0.0) or 0.0)
        imbalance = float(getattr(profile, 'imbalance', 0.0) or 0.0)
        predict_score = float(getattr(profile, 'predict_score', 0.0) or 0.0)
        tier = str(getattr(profile, 'tier', '')).upper()
        overlay = str(getattr(regime, 'scoring_overlay', '')).upper()
        stress_cutoff = self._research_stress_spread_bps if self.research_adaptive_spread_thresholds else float(self.archetype_stressed_spread_bps)
        bootstrap_inactive = self.research_inactive_bootstrap and self.research_bootstrap_dead_as_mm and (overlay == 'SCORING_PRESSURE') and (tier == 'INACTIVE')
        profile_book_id = getattr(profile, 'book_id', None)
        parked_dust = self.research_dust_park_enabled and profile_book_id in self._research_parked_dust
        if parked_dust:
            archetype: BookArchetype = 'TOXIC_BOOK'
            source = 'PARKED_DUST'
        elif spread_bps >= stress_cutoff:
            archetype: BookArchetype = 'STRESSED'
            source = 'LOCAL_SPREAD'
        elif bootstrap_inactive:
            extreme_vol = self.research_bootstrap_extreme_vol_mult * max(float(self.archetype_vol_threshold), 1e-12)
            if volatility >= extreme_vol:
                archetype = 'TOXIC_BOOK'
                source = 'BOOTSTRAP_EXTREME_VOL'
            elif abs(imbalance) >= self.archetype_wall_imbalance:
                archetype = 'WALL_BOOK'
                source = 'BOOTSTRAP_WALL'
            elif volatility >= self.archetype_vol_threshold and abs(predict_score) >= self.direction_threshold:
                archetype = 'TREND_BOOK'
                source = 'BOOTSTRAP_VOL_DIRECTION'
            elif abs(predict_score) >= self.direction_threshold:
                archetype = 'TREND_BOOK'
                source = 'BOOTSTRAP_DIRECTION'
            else:
                archetype = 'MM_BOOK'
                source = 'INACTIVE_BOOTSTRAP'
        elif self._sparse_active_tier_enabled(tier) and trade_rate < self.archetype_dead_trade_rate:
            extreme_vol = self.research_bootstrap_extreme_vol_mult * max(float(self.archetype_vol_threshold), 1e-12)
            if volatility >= extreme_vol:
                archetype = 'TOXIC_BOOK'
                source = 'ACTIVE_SPARSE_EXTREME_VOL'
            elif abs(imbalance) >= self.archetype_wall_imbalance:
                archetype = 'WALL_BOOK'
                source = 'ACTIVE_SPARSE_WALL'
            elif abs(predict_score) >= self.direction_threshold:
                archetype = 'TREND_BOOK'
                source = 'ACTIVE_SPARSE_TREND'
            else:
                archetype = 'MM_BOOK'
                source = 'ACTIVE_SPARSE_MM'
        elif trade_rate < self.archetype_dead_trade_rate:
            archetype = 'DEAD_BOOK'
            source = 'DEAD_TRADE_RATE'
        elif spread_bps < self.archetype_mm_spread_bps:
            archetype = 'MM_BOOK'
            source = 'NARROW_MM'
        elif abs(imbalance) >= self.archetype_wall_imbalance:
            archetype = 'WALL_BOOK'
            source = 'WALL_IMBALANCE'
        elif volatility >= self.archetype_vol_threshold and abs(predict_score) >= self.direction_threshold:
            archetype = 'TREND_BOOK'
            source = 'VOL_AND_DIRECTION'
        elif volatility >= self.archetype_vol_threshold:
            archetype = 'TOXIC_BOOK'
            source = 'HIGH_VOL'
        elif abs(predict_score) >= self.direction_threshold:
            archetype = 'TREND_BOOK'
            source = 'DIRECTION'
        else:
            archetype = 'MM_BOOK' if self.research_neutral_fallback else 'TOXIC_BOOK'
            source = 'NEUTRAL_FALLBACK' if self.research_neutral_fallback else 'LEGACY_TOXIC_FALLBACK'
        if self.debug_enabled:
            record = self._book_record(profile.book_id)
            record['archetype'] = archetype
            record['archetype_source'] = source
            record['profile_spread_bps'] = spread_bps
            record['volatility'] = volatility
            record['trade_rate'] = trade_rate
            record['imbalance'] = imbalance
            record['stress_spread_bps'] = stress_cutoff
            record['toxic_spread_bps'] = self._research_toxic_spread_bps
            record['stressed_by_spread'] = spread_bps >= stress_cutoff
            record['stressed_by_regime'] = False
            record['legacy_stressed_by_regime'] = str(getattr(regime, 'mode', '')).upper() == 'STRESSED'
            record['bootstrap_inactive'] = bootstrap_inactive
            record['parked_dust'] = parked_dust
            record['dead_trade_rate_hit'] = trade_rate < self.archetype_dead_trade_rate
            record['active_sparse'] = self._sparse_active_tier_enabled(tier) and trade_rate < self.archetype_dead_trade_rate
            record['active_sparse_tier'] = tier if record['active_sparse'] else None
        return archetype

    def _bsimpl_3_Strategy1_Research_is_toxic_book(self, book_id: int, profile: BookProfile, archetype: BookArchetype) -> bool:
        dust_info = self._research_parked_dust.get(book_id)
        if self.research_dust_park_enabled and dust_info is not None:
            if self.debug_enabled:
                record = self._book_record(book_id)
                record['dust_position'] = True
                record['dust_quarantine'] = True
                record['dust_qty'] = abs(float(dust_info.get('net_base', 0.0)))
                record['toxic'] = False
            return True
        mem = self._mem(book_id)
        spread_bps = self._profile_float(profile, 'spread_bps')
        toxic_cutoff = self._research_toxic_spread_bps if self.research_adaptive_spread_thresholds else float(self.toxic_spread_bps)
        toxic_loss = mem.loss_streak >= self.toxic_loss_streak
        pnl_samples = int(self._research_round_trip_samples_by_book.get(book_id, 0))
        toxic_pnl_raw = mem.recent_pnl < self.toxic_recent_pnl
        toxic_pnl = toxic_pnl_raw and (pnl_samples >= self.research_toxic_pnl_min_samples or mem.recent_pnl <= self.research_toxic_pnl_hard_floor)
        toxic_spread = spread_bps is not None and spread_bps > toxic_cutoff
        toxic_archetype = archetype in ('STRESSED', 'TOXIC_BOOK')
        toxic_red_tier = str(getattr(profile, 'tier', '')).upper() == 'RED'
        toxic = any((toxic_loss, toxic_pnl, toxic_spread, toxic_archetype, toxic_red_tier))
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['toxic'] = toxic
            record['loss_streak'] = mem.loss_streak
            record['recent_pnl'] = mem.recent_pnl
            record['toxic_loss'] = toxic_loss
            record['toxic_pnl'] = toxic_pnl
            record['toxic_pnl_raw'] = toxic_pnl_raw
            record['toxic_pnl_samples'] = pnl_samples
            record['toxic_pnl_min_samples'] = self.research_toxic_pnl_min_samples
            record['toxic_pnl_hard_floor'] = self.research_toxic_pnl_hard_floor
            record['toxic_spread'] = toxic_spread
            record['toxic_archetype'] = toxic_archetype
            record['toxic_red_tier'] = toxic_red_tier
            record['toxic_spread_bps'] = toxic_cutoff
        return toxic

    def _bsimpl_3_Strategy1_Research__net_inventory(self, book_id: int, mid: float) -> InventorySnapshot:
        """V2 inventory snapshot in one unit system: signed base utilization."""
        if not self.research_fix_inventory_util:
            return self._bsimpl_2_Strategy1_Debug__net_inventory(book_id, mid)
        if mid <= 0:
            return InventorySnapshot(0.0, 0.0, 'FLAT', None, None, 0)
        tracker = self._position_tracker_snapshot(book_id)
        net_base = float(tracker.net_qty)
        max_base = max(float(self.max_inventory_base), 1e-09)
        signed_util = net_base / max_base
        flat_eps = self._execution_flat_epsilon()
        if abs(net_base) < flat_eps:
            band = 'FLAT'
            self._position_ticks.pop(book_id, None)
            self._research_position_tick_seen.pop(book_id, None)
            self._inventory_reason.pop(book_id, None)
        else:
            band = 'MAX_LONG' if net_base >= max_base else 'MAX_SHORT' if net_base <= -max_base else 'LONG' if net_base > 0.0 else 'SHORT'
            current_tick = int(getattr(self, '_tick', 0) or 0)
            if self._research_position_tick_seen.get(book_id) != current_tick:
                self._position_ticks[book_id] = self._position_ticks.get(book_id, 0) + 1
                self._research_position_tick_seen[book_id] = current_tick
        position_ticks = self._position_ticks.get(book_id, 0)
        vwap = tracker.vwap_entry
        unrealized_bps: float | None = None
        if vwap and vwap > 0:
            if net_base > 0:
                unrealized_bps = (mid - vwap) / vwap * 10000.0
            elif net_base < 0:
                unrealized_bps = (vwap - mid) / vwap * 10000.0
        inventory = InventorySnapshot(net_base=net_base, inventory_ratio=signed_util, band=band, vwap_entry=vwap, unrealized_bps=unrealized_bps, position_ticks=position_ticks, opened_at_ns=tracker.opened_at_ns, reason=self._inventory_reason.get(book_id, 'UNKNOWN'))
        try:
            setattr(inventory, '_research_book_id', int(book_id))
        except Exception:
            pass
        if self.debug_enabled:
            self._book_record(book_id)['inventory'] = {'net_base': inventory.net_base, 'ratio': inventory.inventory_ratio, 'signed_util': signed_util, 'band': inventory.band, 'vwap_entry': inventory.vwap_entry, 'unrealized_bps': inventory.unrealized_bps, 'position_ticks': inventory.position_ticks, 'reason': inventory.reason}
        self._refresh_dust_state(book_id, net_base, emit=True)
        return inventory

    def _bsimpl_3_Strategy1_Research__inventory_util(self, inventory: InventorySnapshot) -> float:
        if not self.research_fix_inventory_util:
            return self._bsimpl_1_Strategy1__inventory_util(inventory)
        return abs(float(inventory.net_base)) / max(float(self.max_inventory_base), 1e-09)

    def _bsimpl_3_Strategy1_Research__inventory_needs_management(self, inventory: InventorySnapshot) -> bool:
        if inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        abs_base = abs(float(inventory.net_base))
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        eps = self._execution_flat_epsilon()
        if self.research_dust_park_enabled and self._is_dust_qty(inventory.net_base):
            book_id = getattr(inventory, '_research_book_id', None)
            return book_id is not None and int(book_id) in self._research_dust_compact_ids_this_tick and self._dust_compaction_safe_for_any_fill(inventory.net_base)
        if self._research_bootstrap_active and self.research_bootstrap_manage_min_clip and (abs_base >= eps) and (min_size <= 0.0 or abs_base + 1e-12 >= min_size):
            return True
        return self._inventory_util(inventory) >= float(self.inventory_close_threshold)

    def _bsimpl_3_Strategy1_Research_skewed_quote_prices(self, bid: float, ask: float, signal: float, inventory_ratio: float, regime_params: RegimeParamSet, price_dec: int, edge_bias: float=0.0) -> tuple[float, float] | None:
        if not self.research_fix_quote_reservation:
            return self._bsimpl_1_Strategy1_skewed_quote_prices(bid, ask, signal, inventory_ratio, regime_params, price_dec, edge_bias)
        spread = ask - bid
        if spread <= 0.0:
            return None
        mid = 0.5 * (bid + ask)
        directional = max(-1.0, min(1.0, float(signal) + float(edge_bias)))
        directional_bias = float(regime_params.buy_bias) if directional >= 0.0 else float(regime_params.sell_bias)
        alpha_shift = spread * float(regime_params.skew_strength) * directional * directional_bias
        inventory_shift = spread * float(self.inventory_skew_strength) * float(inventory_ratio)
        reservation = mid + alpha_shift - inventory_shift
        half_spread = spread * max(0.05, float(regime_params.spread_offset))
        tick_size = 10.0 ** (-int(price_dec))
        bid_px = min(reservation - half_spread, ask - tick_size)
        ask_px = max(reservation + half_spread, bid + tick_size)
        bid_px = round(bid_px, price_dec)
        ask_px = round(ask_px, price_dec)
        if bid_px <= 0.0 or bid_px >= ask_px:
            return None
        return (bid_px, ask_px)

    def _bsimpl_3_Strategy1_Research__compute_close_score(self, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> float:
        if not self.research_fix_inventory_util:
            return self._bsimpl_1_Strategy1__compute_close_score(inventory, regime_params, regime, archetype)
        unreal = inventory.unrealized_bps
        target = max(float(regime_params.profit_target_bps), 1e-09)
        stop = max(float(regime_params.stop_loss_bps), 1e-09)
        pnl_component = 0.0
        if unreal is not None:
            if unreal >= target or unreal <= -stop:
                pnl_component = 1.0
            elif unreal > 0.0:
                pnl_component = unreal / target
            else:
                pnl_component = abs(unreal) / stop
        inventory_risk = min(1.0, self._inventory_util(inventory))
        regime_risk = 0.0
        if str(getattr(regime, 'mode', '')).upper() == 'STRESSED':
            regime_risk = 1.0
        elif archetype in ('TOXIC_BOOK', 'WALL_BOOK'):
            regime_risk = 0.6
        elif archetype == 'DEAD_BOOK':
            regime_risk = 0.4
        time_risk = min(1.0, float(inventory.position_ticks) / max(float(self.position_max_ticks), 1.0))
        return 0.5 * pnl_component + 0.3 * inventory_risk + 0.2 * max(regime_risk, time_risk)

    def _bsimpl_3_Strategy1_Research__allows_aggressive_close(self, book_id: int, inventory: InventorySnapshot, close_score: float, time_stop: bool, stop_loss_hit: bool) -> bool:
        if stop_loss_hit or inventory.band in ('MAX_LONG', 'MAX_SHORT'):
            return True
        if self._research_bootstrap_active and self.research_bootstrap_allow_aggressive_close:
            ticks = int(inventory.position_ticks)
            if not self.research_aggressive_close_touch_gate:
                if ticks >= self.research_bootstrap_hard_close_ticks:
                    return True
                if ticks >= self.research_bootstrap_force_close_ticks:
                    unreal = inventory.unrealized_bps
                    return unreal is not None and unreal >= self.research_bootstrap_force_close_min_bps
                return False
            if ticks >= self.research_bootstrap_force_close_ticks:
                ctx = self._research_aggressive_context.get(book_id) or {}
                net_touch_bps = ctx.get('net_touch_bps')
                if net_touch_bps is not None:
                    return float(net_touch_bps) >= self.research_aggressive_close_min_net_bps
            return False
        return self._bsimpl_1_Strategy1__allows_aggressive_close(book_id, inventory, close_score, time_stop, stop_loss_hit)

    def _bsimpl_3_Strategy1_Research__execute_aggressive_close(self, response: FinanceAgentResponse, book_id: int, book: Book, qty: float, long_pos: bool) -> bool:
        """Submit close without clearing position state before confirmed fill."""
        close_dir = OrderDirection.SELL if long_pos else OrderDirection.BUY
        if not self._passes_fee_gate(book_id, aggressive=True):
            return False
        if self._count_book_instructions(response, book_id) >= self.max_instructions_per_book:
            return False
        account = self.accounts[book_id]
        if close_dir == OrderDirection.SELL and account.base_balance.free >= qty:
            response.market_order(book_id=book_id, direction=close_dir, quantity=qty, stp=STP.CANCEL_OLDEST, delay=0)
            return True
        if close_dir == OrderDirection.BUY:
            px = book.asks[0].price
            if account.quote_balance.free >= qty * px:
                response.market_order(book_id=book_id, direction=close_dir, quantity=qty, stp=STP.CANCEL_OLDEST, delay=0)
                return True
        return False

    def _bsimpl_3_Strategy1_Research__manage_inventory(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book: Book, inventory: InventorySnapshot, regime_params: RegimeParamSet, regime: MarketRegime, archetype: BookArchetype) -> int:
        touch_gross_bps = None
        net_touch_bps = None
        entry = inventory.vwap_entry
        if entry is not None and float(entry) > 0.0 and getattr(book, 'bids', None) and getattr(book, 'asks', None):
            entry_f = float(entry)
            if inventory.net_base > 0.0:
                touch_px = float(book.bids[0].price)
                touch_gross_bps = (touch_px - entry_f) / entry_f * 10000.0
            elif inventory.net_base < 0.0:
                touch_px = float(book.asks[0].price)
                touch_gross_bps = (entry_f - touch_px) / entry_f * 10000.0
            if touch_gross_bps is not None:
                net_touch_bps = float(touch_gross_bps) - self.research_aggressive_close_fee_buffer_bps
        self._research_aggressive_context[book_id] = {'touch_gross_bps': touch_gross_bps, 'net_touch_bps': net_touch_bps, 'age_ticks': int(inventory.position_ticks)}
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['aggressive_touch_gross_bps'] = touch_gross_bps
            record['aggressive_touch_net_bps'] = net_touch_bps
            record['aggressive_close_fee_buffer_bps'] = self.research_aggressive_close_fee_buffer_bps
            record['aggressive_close_min_net_bps'] = self.research_aggressive_close_min_net_bps
        min_size = max(0.0, float(self._research_exchange_min_order_size))
        abs_base = abs(float(inventory.net_base))
        if self.research_dust_safe_close and inventory.band != 'FLAT' and self._is_dust_qty(inventory.net_base):
            self._refresh_dust_state(book_id, inventory.net_base, emit=True)
            compact_selected = book_id in self._research_dust_compact_ids_this_tick and self._dust_compaction_safe_for_any_fill(inventory.net_base)
            if compact_selected:
                self._research_dust_compact_attempts += 1
                before_ix = len(response.instructions)
                n = self._bsimpl_1_Strategy1__place_passive_inventory_exit(response, state, book_id, book, inventory, min_size)
                if n:
                    self._research_dust_compact_orders += 1
                    self._research_dust_compact_active[book_id] = int(getattr(self, '_tick', 0) or 0)
                    self._inventory_reason[book_id] = 'DUST_COMPACT'
                    self._emit('POSITION_GUARD', tick=getattr(self, '_tick', None), book_id=book_id, reason='DUST_COMPACT', net_base=inventory.net_base, min_order_size=min_size, projected_full_fill_net=float(inventory.net_base) - (min_size if inventory.net_base > 0.0 else -min_size), exposure_nonincreasing=True, instructions=len(response.instructions) - before_ix)
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record['action'] = 'MANAGE'
                        record['reason'] = 'DUST_COMPACT'
                        record['dust_compact'] = True
                        record['dust_compact_qty'] = min_size
                        record['instructions'] = n
                    return n
                self._emit('POSITION_GUARD', tick=getattr(self, '_tick', None), book_id=book_id, reason='DUST_COMPACT_BLOCKED', net_base=inventory.net_base, min_order_size=min_size, exposure_nonincreasing=True)
            if self.debug_enabled:
                record = self._book_record(book_id)
                record['dust_position'] = True
                record['dust_quarantine'] = True
                record['dust_qty'] = abs_base
                record['min_order_size'] = min_size
                record['dust_compact_selected'] = compact_selected
            return 0
        return self._bsimpl_2_Strategy1_Debug__manage_inventory(response, state, book_id, book, inventory, regime_params, regime, archetype)

    def _bsimpl_3_Strategy1_Research__dust_fill_matches_recent_compaction(self, book_id: int) -> bool:
        """Telemetry-only attribution guard for DUST_COMPACT fills.

        A dust transition is counted as a compaction fill only when this book
        actually emitted a DUST_COMPACT order recently. This prevents natural
        residual cleanup from inflating the compaction-fill metric.
        """
        submitted_tick = self._research_dust_compact_active.get(int(book_id))
        if submitted_tick is None:
            return False
        now = int(getattr(self, '_tick', 0) or 0)
        return 0 <= now - int(submitted_tick) <= 2

    def _bsimpl_3_Strategy1_Research_onTrade(self, event, validator: str | None=None) -> None:
        book_id = getattr(event, 'bookId', None)
        own = getattr(event, 'takerAgentId', None) == getattr(self, 'uid', None) or getattr(event, 'makerAgentId', None) == getattr(self, 'uid', None)
        before = 0.0
        pnl_before = 0.0
        if book_id is not None:
            before = float(self._position_tracker_snapshot(book_id).net_qty)
            pnl_before = float(self._pnl_tick_buffer.get(book_id, 0.0))
        self._bsimpl_2_Strategy1_Debug_onTrade(event, validator)
        if book_id is None or not own:
            return
        after = float(self._position_tracker_snapshot(book_id).net_qty)
        pnl_after = float(self._pnl_tick_buffer.get(book_id, 0.0))
        realized_delta = pnl_after - pnl_before
        if abs(realized_delta) > 1e-12:
            self._research_realized_observations_by_book[book_id] = self._research_realized_observations_by_book.get(book_id, 0) + 1
        before_was_dust = self._is_dust_qty(before)
        self._refresh_dust_state(book_id, after, emit=True)
        eps = self._execution_flat_epsilon()
        if abs(before) < eps and abs(after) >= eps:
            transition = 'OPEN'
            self._research_position_opens += 1
        elif abs(before) >= eps and abs(after) < eps:
            transition = 'FLAT'
            self._research_round_trip_closes += 1
            self._research_position_reductions += 1
            self._research_round_trip_samples_by_book[book_id] = self._research_round_trip_samples_by_book.get(book_id, 0) + 1
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
        elif before * after < 0.0:
            transition = 'CROSS'
            self._research_position_reductions += 1
            if abs(realized_delta) > 1e-12:
                self._research_round_trip_closes += 1
                self._research_round_trip_samples_by_book[book_id] = self._research_round_trip_samples_by_book.get(book_id, 0) + 1
            self._position_ticks[book_id] = 0
            self._research_position_tick_seen[book_id] = int(getattr(self, '_tick', 0) or 0)
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
                self._inventory_reason[book_id] = 'DUST_COMPACT'
        elif abs(after) < abs(before) - eps:
            transition = 'REDUCE'
            self._research_position_reductions += 1
            if before_was_dust and self._dust_fill_matches_recent_compaction(book_id):
                self._research_dust_compact_fills += 1
        elif abs(after) > abs(before) + eps:
            transition = 'INCREASE'
        else:
            transition = 'UNCHANGED'
        round_trip_event = transition == 'FLAT' or (transition == 'CROSS' and abs(realized_delta) > 1e-12)
        self._emit('POSITION', tick=getattr(self, '_tick', None), timestamp=getattr(event, 'timestamp', None), book_id=book_id, transition=transition, net_before=before, net_after=after, realized_pnl_delta=realized_delta, realized_book_observations=self._research_realized_observations_by_book.get(book_id, 0), round_trip=round_trip_event, round_trip_total=self._research_round_trip_closes, round_trip_book_samples=self._research_round_trip_samples_by_book.get(book_id, 0), execution_flat_epsilon=eps, reason=self._inventory_reason.get(book_id, 'FLAT' if transition == 'FLAT' else 'UNKNOWN'))
        self._execution_on_fill(event, book_id=book_id, before=before, after=after)

    def _execution_field(self, obj: Any, *names: str) -> Any:
        if obj is None:
            return None
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    def _execution_ttl_ms(self) -> float:
        ttl = sim_delta_ms(0, int(getattr(self, 'mm_expiry_period', 500000000) or 500000000))
        return 500.0 if ttl is None else float(ttl)

    def _execution_profile_for_book(self, book_id: int | None):
        if book_id is None:
            return None
        selection = getattr(self, '_last_selection', None)
        for profile in list(getattr(selection, 'profiles', None) or []):
            try:
                if int(getattr(profile, 'book_id')) == int(book_id):
                    return profile
            except (TypeError, ValueError, AttributeError):
                continue
        return None

    def _execution_features(self, *, side: str, book_id: int | None, mid: float, spread: float, trade_rate: float, quote_price: float) -> HazardFeatures:
        touch = mid - 0.5 * spread if str(side).lower() == 'buy' else mid + 0.5 * spread
        dist_bps = None
        if mid > 0.0:
            if str(side).lower() == 'buy':
                dist_bps = ((touch - quote_price) / mid) * 10000.0
            else:
                dist_bps = ((quote_price - touch) / mid) * 10000.0
        profile = self._execution_profile_for_book(book_id)
        spread_bps = (spread / mid) * 10000.0 if mid > 0.0 else None
        return HazardFeatures.from_snapshot(
            side=side,
            distance_from_touch_bps=dist_bps,
            spread_bps=spread_bps,
            volatility=None if profile is None else getattr(profile, 'volatility', None),
            trade_rate=trade_rate,
            imbalance=None if profile is None else getattr(profile, 'imbalance', None),
            market_regime=getattr(self, '_market_regime', None),
            ttl_ms=self._execution_ttl_ms(),
        )

    def _bsimpl_3_Strategy1_Research_estimate_fill_probability(self, book: Book, mid: float, spread: float, trade_rate: float, buy_price: float, sell_price: float, book_id: int | None=None) -> FillProbabilityEstimate:
        old = self._bsimpl_1_Strategy1_estimate_fill_probability(book, mid, spread, trade_rate, buy_price, sell_price, book_id=book_id)
        if not getattr(self, 'execution_hazard_enabled', True):
            if book_id is not None:
                self._execution_last[int(book_id)] = {
                    'legacy': old,
                    'buy': None,
                    'sell': None,
                    'fallback_reason': 'POLICY_DISABLED',
                    'model_confidence': 0.0,
                }
            return old
        try:
            model = self._execution_hazard
            buy_feat = self._execution_features(side='buy', book_id=book_id, mid=mid, spread=spread, trade_rate=trade_rate, quote_price=buy_price)
            sell_feat = self._execution_features(side='sell', book_id=book_id, mid=mid, spread=spread, trade_rate=trade_rate, quote_price=sell_price)
            pred_buy = model.predict(buy_feat)
            pred_sell = model.predict(sell_feat)
            use_policy = bool(getattr(self, 'execution_hazard_use_for_policy', True))
            buy, buy_reason, buy_conf = model.apply_policy_fill(old.buy, pred_buy, use_for_policy=use_policy)
            sell, sell_reason, sell_conf = model.apply_policy_fill(old.sell, pred_sell, use_for_policy=use_policy)
            fallback_reason = buy_reason or sell_reason
            if book_id is not None:
                self._execution_last[int(book_id)] = {
                    'legacy': old,
                    'buy': pred_buy,
                    'sell': pred_sell,
                    'buy_feat': buy_feat,
                    'sell_feat': sell_feat,
                    'fallback_reason': fallback_reason,
                    'model_confidence': min(buy_conf, sell_conf),
                }
            return FillProbabilityEstimate(buy=buy, sell=sell)
        except Exception:
            if book_id is not None:
                self._execution_last[int(book_id)] = {
                    'legacy': old,
                    'buy': None,
                    'sell': None,
                    'fallback_reason': 'UNSUPPORTED_FEATURES',
                    'model_confidence': 0.0,
                }
            return old

    def _execution_observe_quote_end(self, record: QuoteRecord, *, filled: bool, timestamp: int | None, fill_class: str | None=None) -> None:
        if not getattr(self, 'execution_hazard_enabled', True):
            return
        if getattr(record, 'hazard_closed', False):
            return
        if filled is False and record.fill_ts is not None:
            return
        model = getattr(self, '_execution_hazard', None)
        if model is None or int(getattr(model, 'observations', 0) or 0) >= int(self._execution_observe_cap):
            record.hazard_closed = True
            return
        stored = getattr(record, 'hazard_features', None) or {}
        try:
            feat = HazardFeatures(
                side=str(stored.get('side') or record.side or 'buy'),
                dist_bucket=int(stored.get('dist_bucket', 1)),
                spread_bucket=int(stored.get('spread_bucket', 1)),
                vol_bucket=int(stored.get('vol_bucket', 1)),
                trade_bucket=int(stored.get('trade_bucket', 1)),
                imb_bucket=int(stored.get('imb_bucket', 1)),
                regime_group=str(stored.get('regime_group') or 'NORMAL'),
                ttl_bucket=int(stored.get('ttl_bucket', 1)),
                ttl_ms=float(stored.get('ttl_ms') or record.configured_ttl_ms or 500.0),
            )
        except Exception:
            feat = HazardFeatures.from_snapshot(
                side=record.side,
                distance_from_touch_bps=(record.snapshot or {}).get('distance_from_touch_bps'),
                spread_bps=(record.snapshot or {}).get('spread_bps'),
                volatility=(record.snapshot or {}).get('volatility'),
                trade_rate=(record.snapshot or {}).get('trade_rate'),
                imbalance=(record.snapshot or {}).get('imbalance'),
                market_regime=record.market_regime,
                ttl_ms=record.configured_ttl_ms,
            )
        age_ms = sim_delta_ms(record.submit_ts, timestamp)
        if age_ms is None:
            age_ms = 0.0 if filled else float(feat.ttl_ms)
        predicted = None
        if record.predicted_any_fill_probability is not None:
            predicted = HazardPrediction(
                any_fill=float(record.predicted_any_fill_probability),
                actionable_fill=float(record.predicted_actionable_fill_probability or 0.0),
                dust=float(record.predicted_dust_probability or 0.0),
                source=str(record.hazard_source or 'fallback'),
                usable=str(record.hazard_source or '') not in {'', 'fallback'},
                n_at_risk=0,
                ttl_ms=feat.ttl_ms,
            )
        model.observe(feat, age_ms=age_ms, filled=filled, fill_class=fill_class, predicted=predicted)
        record.hazard_closed = True

    def _execution_register_submitted(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate) -> None:
        store = getattr(self, '_execution_quotes', None)
        if store is None:
            return
        now = getattr(state, 'timestamp', None)
        ttl_ms = self._execution_ttl_ms()
        for instruction in list(getattr(response, 'instructions', None) or []):
            kind = type(instruction).__name__.upper()
            if 'LIMIT' not in kind:
                continue
            book_id = self._execution_field(instruction, 'bookId', 'book_id')
            client_id = self._execution_field(instruction, 'clientOrderId', 'client_order_id')
            if book_id is None:
                continue
            try:
                book_id = int(book_id)
            except (TypeError, ValueError):
                continue
            try:
                client_id = int(client_id) if client_id is not None else None
            except (TypeError, ValueError):
                client_id = None
            direction = self._execution_field(instruction, 'direction', 'side')
            side = 'buy'
            try:
                token = str(getattr(direction, 'name', direction)).lower()
                if token in {'1', 'sell', 'ask', 's'}:
                    side = 'sell'
            except Exception:
                side = 'buy'
            quantity = self._execution_field(instruction, 'quantity', 'volume')
            quote_price = self._execution_field(instruction, 'price')
            expiry = self._execution_field(instruction, 'expiryPeriod', 'expiry_period')
            inst_ttl = ttl_ms
            try:
                if expiry is not None:
                    inst_ttl = sim_delta_ms(0, int(expiry)) or ttl_ms
            except (TypeError, ValueError):
                inst_ttl = ttl_ms
            last = self._execution_last.get(book_id, {})
            pred = last.get(side)
            feat = last.get(f'{side}_feat')
            snap = dict((getattr(self, '_quote_submit_snapshot', {}) or {}).get(book_id, {}) or {})
            record = QuoteRecord(
                quote_id=store.next_quote_id(),
                client_id=client_id,
                book=book_id,
                side=side,
                decision_ts=None if now is None else int(now),
                submit_ts=None if now is None else int(now),
                requested_quantity=None if quantity is None else float(quantity),
                remaining_quantity=None if quantity is None else float(quantity),
                quote_price=None if quote_price is None else float(quote_price),
                configured_ttl_ms=inst_ttl,
                predicted_fill_probability=None if pred is None else float(pred.any_fill),
                predicted_any_fill_probability=None if pred is None else float(pred.any_fill),
                predicted_actionable_fill_probability=None if pred is None else float(pred.actionable_fill),
                predicted_dust_probability=None if pred is None else float(pred.dust),
                hazard_source=None if pred is None else str(pred.source),
                hazard_features=None if feat is None else {
                    'side': feat.side,
                    'dist_bucket': feat.dist_bucket,
                    'spread_bucket': feat.spread_bucket,
                    'vol_bucket': feat.vol_bucket,
                    'trade_bucket': feat.trade_bucket,
                    'imb_bucket': feat.imb_bucket,
                    'regime_group': feat.regime_group,
                    'ttl_bucket': feat.ttl_bucket,
                    'ttl_ms': feat.ttl_ms,
                },
                market_regime=getattr(self, '_market_regime', None),
                score_regime=getattr(self, '_score_regime', None),
                snapshot=snap,
            )
            store.register_quote(record)
            replaced = getattr(store, 'last_replaced', None)
            if replaced is not None:
                self._execution_observe_quote_end(replaced, filled=False, timestamp=record.submit_ts)

    def _execution_close_from_notices(self, state: MarketSimulationStateUpdate) -> None:
        store = getattr(self, '_execution_quotes', None)
        if store is None:
            return
        notices = (getattr(state, 'notices', None) or {}).get(self.uid, []) or []
        timestamp = getattr(state, 'timestamp', None)
        for notice in notices:
            phase = type(notice).__name__.upper()
            if not any(token in phase for token in ('CANCEL', 'EXPIRE', 'REJECT')):
                continue
            book_id = self._execution_field(notice, 'bookId', 'book_id')
            client_id = self._execution_field(notice, 'clientOrderId', 'client_order_id')
            if book_id is None:
                continue
            try:
                book_id = int(book_id)
            except (TypeError, ValueError):
                continue
            try:
                client_id = int(client_id) if client_id is not None else None
            except (TypeError, ValueError):
                client_id = None
            record = store.lookup(book_id, client_id)
            if record is None or not record.open:
                continue
            ts = timestamp if timestamp is None else int(timestamp)
            quote_age = sim_delta_ms(record.submit_ts, ts)
            cancel_reason = 'EXPIRE' if 'EXPIRE' in phase else 'CANCEL'
            store.close_quote(record, cancel_ts=ts)
            self._execution_observe_quote_end(record, filled=False, timestamp=ts)
            if getattr(self, 'debug_enabled', False) or getattr(self, 'research_enabled', False):
                self._emit(
                    'QUOTE',
                    force=True,
                    tick=getattr(self, '_tick', None),
                    book_id=int(book_id),
                    cancel_reason=cancel_reason,
                    quote_age=quote_age,
                    chosen_ttl=record.configured_ttl_ms,
                    dust_probability=record.predicted_dust_probability,
                )

    def _execution_on_fill(self, event, *, book_id: int, before: float, after: float) -> None:
        store = getattr(self, '_execution_quotes', None)
        if store is None:
            return
        is_maker = getattr(event, 'makerAgentId', None) == getattr(self, 'uid', None)
        client_id = getattr(event, 'clientOrderId', None)
        try:
            client_id = int(client_id) if client_id is not None else None
        except (TypeError, ValueError):
            client_id = None
        fill_qty = abs(float(getattr(event, 'quantity', 0.0) or 0.0))
        fill_ts = getattr(event, 'timestamp', None)
        fill_ts = None if fill_ts is None else int(fill_ts)
        eps = self._execution_flat_epsilon()
        min_size = max(0.0, float(getattr(self, '_research_exchange_min_order_size', 0.0) or 0.0))
        record = store.lookup(int(book_id), client_id)
        if record is None:
            side_token = str(getattr(event, 'side', '')).lower()
            record = store.live_for_book_side(int(book_id), 'buy' if side_token in {'0', 'buy', 'bid'} else 'sell')
        requested = None if record is None else record.requested_quantity
        filled_cum = fill_qty
        remaining = None
        if record is not None:
            filled_cum = store.apply_fill(record, fill_qty=fill_qty, fill_ts=fill_ts, flat_eps=eps)
            requested = record.requested_quantity
            remaining = record.remaining_quantity
        fill_class = classify_fill(
            inventory_before=before,
            inventory_after=after,
            fill_quantity=fill_qty,
            requested_quantity=requested,
            filled_quantity=filled_cum,
            min_order_size=min_size,
            flat_eps=eps,
        )
        self._execution_fills_classified += 1
        if fill_class in {'DUST_PARTIAL', 'CROSS_DUST'}:
            self._dust_creation_count += 1
        pred_any = None if record is None else record.predicted_any_fill_probability
        pred_act = None if record is None else record.predicted_actionable_fill_probability
        pred_dust = None if record is None else record.predicted_dust_probability
        last = self._execution_last.get(int(book_id), {})
        fallback_reason = last.get('fallback_reason', '')
        confidence = last.get('model_confidence', 0.0)
        if record is not None:
            fallback_reason = '' if record.hazard_source not in {None, 'fallback'} else (fallback_reason or 'INSUFFICIENT_SAMPLES')
            if is_maker:
                self._execution_observe_quote_end(record, filled=True, timestamp=fill_ts, fill_class=str(fill_class))
            if remaining is not None and remaining <= eps or fill_class in {'FLAT', 'FULL', 'CROSS_DUST'}:
                store.close_quote(record, fill_ts=fill_ts)
        if getattr(self, 'debug_enabled', False) or getattr(self, 'research_enabled', False):
            self._emit(
                'EXECUTION',
                force=True,
                tick=getattr(self, '_tick', None),
                book_id=int(book_id),
                actual_fill_class=fill_class,
                predicted_any_fill_probability=pred_any,
                predicted_actionable_fill_probability=pred_act,
                predicted_dust_probability=pred_dust,
                model_confidence=confidence,
                fallback_reason=fallback_reason or '',
                min_order_size=min_size,
            )

    def _bsimpl_3_Strategy1_Research_handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        self._execution_close_from_notices(state)
        return self._bsimpl_2_Strategy1_Debug_handle(state)

    def _bsimpl_3_Strategy1_Research_respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        response = self._bsimpl_2_Strategy1_Debug_respond(state)
        self._execution_register_submitted(response, state)
        return response

    def _bsimpl_3_Strategy1_Research_dynamic_order_size(self, base_size: float, profile: BookProfile, regime_params: RegimeParamSet, inventory: InventorySnapshot, vol_dec: int, mid: float | None=None) -> float:
        predict_score = float(getattr(profile, 'predict_score', 0.0) or 0.0)
        volatility = float(getattr(profile, 'volatility', 0.0) or 0.0)
        confidence = max(0.5, min(2.0, 1.0 + abs(predict_score)))
        vol_scale = 1.0
        if volatility > 0.0:
            vol_scale = max(0.5, min(2.0, float(self.profile_vol_scale) / volatility))
        spread_factor = 1.0
        if profile.spread is not None and mid is not None and (mid > 0.0):
            spread_bps = float(profile.spread) / mid * 10000.0
            spread_factor = max(0.5, min(1.5, 1.0 - spread_bps / 20.0))
        kappa_scale = 1.0
        if profile.raw_kappa is not None:
            kappa_scale = max(0.5, min(1.5, 1.0 + float(profile.raw_kappa) * 0.2))
        inventory_factor = max(0.3, 1.0 - min(1.0, self._inventory_util(inventory)))
        raw_model_size = float(base_size) * confidence * float(regime_params.size_mult) * vol_scale * spread_factor * kappa_scale * inventory_factor
        size = self._round_order_size(raw_model_size, vol_dec)
        min_size = max(0.0, self._research_exchange_min_order_size)
        promoted = False
        rounded_before_promotion = size
        if self.research_promote_min_order and size > 0.0 and (min_size > 0.0) and (size + 1e-12 < min_size):
            remaining = max(0.0, float(self.max_inventory_base) - abs(float(inventory.net_base)))
            if min_size <= remaining + 1e-12:
                size = round(min_size, vol_dec)
                promoted = True
            else:
                size = 0.0
        if self.debug_enabled and hasattr(profile, 'book_id'):
            record = self._book_record(profile.book_id)
            record['dynamic_size_model_raw'] = raw_model_size
            record['dynamic_size_raw'] = rounded_before_promotion
            record['dynamic_size_final'] = size
            record['inventory_util'] = self._inventory_util(inventory)
            record['min_order_size'] = min_size
            record['size_promoted_to_min'] = promoted
        return size

    def _quote_tick_size(self, state) -> float:
        try:
            decimals = int(getattr(getattr(state, 'config', None), 'priceDecimals', 0) or 0)
            return 10.0 ** (-decimals) if decimals >= 0 else 0.01
        except (TypeError, ValueError):
            return 0.01

    def _live_quote(self, book_id: int, side: str):
        store = getattr(self, '_execution_quotes', None)
        if store is None:
            return None
        rec = store.live_for_book_side(int(book_id), side)
        if rec is None or not rec.open:
            return None
        return rec

    def _predicted_dust(self, book_id: int) -> tuple[float, bool]:
        ev = (getattr(self, '_score_ev_last', {}) or {}).get(int(book_id))
        if ev is not None:
            return float(getattr(ev, 'dust_prob', 0.0) or 0.0), True
        last = (getattr(self, '_execution_last', {}) or {}).get(int(book_id), {})
        preds = [p for p in (last.get('buy'), last.get('sell')) if p is not None]
        if not preds:
            return 0.0, False
        usable = any(bool(getattr(p, 'usable', False)) for p in preds)
        dust = sum(float(getattr(p, 'dust', 0.0) or 0.0) for p in preds) / len(preds)
        return dust, usable

    def _choose_quote_ttl(self, book_id: int, profile, state, *, baseline_ns: int) -> tuple[float | None, str, float | None]:
        baseline = sim_delta_ms(0, int(baseline_ns)) or 500.0
        last = (getattr(self, '_execution_last', {}) or {}).get(int(book_id), {})
        preds = [p for p in (last.get('buy'), last.get('sell')) if p is not None]
        fill_hazard = None
        if preds:
            fill_hazard = sum(float(p.any_fill) for p in preds) / len(preds)
        imb = None if profile is None else getattr(profile, 'imbalance', None)
        vol = None if profile is None else getattr(profile, 'volatility', None)
        toxic = str(getattr(self, '_market_regime', '') or '').upper() in {'TOXIC', 'STRESSED'}
        ttl, reason, _info = choose_ttl_ms(
            baseline_ms=float(baseline),
            min_ms=float(self.ttl_min_ms),
            max_ms=float(self.ttl_max_ms),
            fill_hazard=fill_hazard,
            volatility=None if vol is None else float(vol),
            imbalance=None if imb is None else float(imb),
            toxicity=toxic,
            market_regime=getattr(self, '_market_regime', None),
            stale_velocity_ticks=8.0,
        )
        return ttl, reason, fill_hazard

    def _hysteresis_hold_sides(
        self,
        state,
        book_id: int,
        book,
        profile,
        prediction,
        inventory,
        regime_params,
        edge_bias: float,
    ) -> set[str]:
        hold: set[str] = set()
        if book is None or not getattr(book, 'bids', None) or not getattr(book, 'asks', None):
            return hold
        try:
            tick_size = self._quote_tick_size(state)
            now = getattr(state, 'timestamp', None)
            price_dec = int(getattr(getattr(state, 'config', None), 'priceDecimals', 2) or 2)
            prices = self.skewed_quote_prices(
                float(book.bids[0].price),
                float(book.asks[0].price),
                float(getattr(prediction, 'score', 0.0) or 0.0),
                float(getattr(inventory, 'inventory_ratio', 0.0) or 0.0),
                regime_params,
                price_dec,
                edge_bias=float(edge_bias or 0.0),
            )
            if not prices:
                return hold
            new_buy, new_sell = prices
            alpha = float(getattr(prediction, 'score', 0.0) or 0.0)
            imb = None if profile is None else getattr(profile, 'imbalance', None)
            regime = getattr(self, '_market_regime', None)
            try:
                util = float(self._inventory_util(inventory))
            except Exception:
                util = 0.0
            toxic = str(regime or '').upper() in {'TOXIC'} or int(book_id) in (getattr(self, '_research_parked_dust', {}) or {})
            ev_row = (getattr(self, '_score_ev_last', {}) or {}).get(int(book_id))
            new_ev = None if ev_row is None else getattr(ev_row, 'trading_ev', None)
            hard = toxic or str(getattr(inventory, 'band', '')).upper() in {'MAX_LONG', 'MAX_SHORT'}
            if ev_row is not None and not bool(getattr(ev_row, 'eligible', True)):
                hard = True
            for side, new_px in (('buy', new_buy), ('sell', new_sell)):
                rec = self._live_quote(book_id, side)
                snap = {} if rec is None else dict(rec.snapshot or {})
                age_ms = None if rec is None else sim_delta_ms(rec.submit_ts, now)
                decision = should_replace_quote(
                    old_price=None if rec is None else rec.quote_price,
                    new_price=float(new_px),
                    tick_size=float(tick_size),
                    min_price_ticks=float(self.hysteresis_min_price_ticks),
                    old_alpha=snap.get('alpha'),
                    new_alpha=alpha,
                    old_imbalance=snap.get('imbalance'),
                    new_imbalance=None if imb is None else float(imb),
                    old_regime=None if rec is None else rec.market_regime,
                    new_regime=regime,
                    old_inventory_util=snap.get('inventory_util'),
                    new_inventory_util=util,
                    old_toxic=bool(snap.get('toxic')),
                    new_toxic=toxic,
                    order_age_ms=age_ms,
                    ttl_ms=None if rec is None else rec.configured_ttl_ms,
                    old_ev=snap.get('quote_ev'),
                    new_ev=new_ev,
                    ev_improve_threshold=float(self.hysteresis_ev_threshold),
                    hard_safety=hard,
                )
                if getattr(self, 'debug_enabled', False) or getattr(self, 'research_enabled', False):
                    self._emit(
                        'QUOTE',
                        force=True,
                        tick=getattr(self, '_tick', None),
                        book_id=int(book_id),
                        side=side,
                        cancel_reason=decision.reason,
                        quote_age=decision.order_age_ms,
                        chosen_ttl=None if rec is None else rec.configured_ttl_ms,
                        dust_probability=(getattr(self, '_quote_submit_snapshot', {}) or {}).get(int(book_id), {}).get('dust_probability'),
                    )
                if decision.cancel:
                    self._hysteresis_replaces += 1
                else:
                    self._hysteresis_holds += 1
                    hold.add(side)
        except Exception:
            return set()
        return hold

    def _dust_prevent_skip_sides(self, inventory, buy_size: float, sell_size: float, book_id: int) -> set[str]:
        skip: set[str] = set()
        if not getattr(self, 'dust_prevent_enabled', True):
            return skip
        min_size = max(0.0, float(getattr(self, '_research_exchange_min_order_size', 0.0) or 0.0))
        eps = self._execution_flat_epsilon()
        before = float(getattr(inventory, 'net_base', 0.0) or 0.0)
        dust_prob, usable = self._predicted_dust(int(book_id))
        target = float(getattr(self, 'score_ev_dust_target', 0.15))
        if would_create_dust(inventory_before=before, signed_fill_qty=float(buy_size), min_order_size=min_size, eps=eps):
            skip.add('buy')
        if would_create_dust(inventory_before=before, signed_fill_qty=-float(sell_size), min_order_size=min_size, eps=eps):
            skip.add('sell')
        if predicted_dust_blocks_increase(dust_prob=dust_prob, dust_target=target, inventory_before=before, signed_qty=float(buy_size), usable=usable):
            skip.add('buy')
        if predicted_dust_blocks_increase(dust_prob=dust_prob, dust_target=target, inventory_before=before, signed_qty=-float(sell_size), usable=usable):
            skip.add('sell')
        return skip

    def _bsimpl_3_Strategy1_Research__place_skewed_quotes(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, book_id: int, book, profile: BookProfile, prediction: DirectionForecast, inventory: InventorySnapshot, regime_params: RegimeParamSet, size: float, edge_bias: float, stats: dict | None=None) -> int:
        """Quote with isolated NORMAL and KAPPA_COMPLETION scheduler lanes.

        V4 allowed every Kappa-completion failure to consume the same shared
        candidate-attempt budget as normal economics. V4.1 gives completion a
        bounded sub-budget (default 4 attempts / 2 successes) and reserves the
        remainder (default 8 attempts / at least 2 success capacity) for normal
        MM. Completion-cap skips do not consume normal or total attempt budget.
        """
        completion_candidate = inventory.band == 'FLAT' and self._is_kappa_completion_candidate(book_id)
        completion_samples = self._completion_observation_count(book_id)
        lane = 'COMPLETION' if completion_candidate else 'NORMAL'
        if self._research_backfill_active:
            if self._research_quote_successes >= self._research_quote_success_cap:
                if self.debug_enabled:
                    record = self._book_record(book_id)
                    record['action'] = 'SKIP'
                    record['reason'] = 'MM_SUCCESS_CAP'
                    record['scheduler_lane'] = lane
                return 0
            if completion_candidate:
                if self._research_completion_quote_successes >= self.research_kappa_completion_success_cap:
                    self._research_completion_success_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record['action'] = 'SKIP'
                        record['reason'] = 'KAPPA_COMPLETION_SUCCESS_CAP'
                        record['scheduler_lane'] = lane
                    return 0
                if self._research_completion_quote_attempts >= self.research_kappa_completion_attempt_cap:
                    self._research_completion_attempt_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record['action'] = 'SKIP'
                        record['reason'] = 'KAPPA_COMPLETION_ATTEMPT_CAP'
                        record['scheduler_lane'] = lane
                    return 0
                self._research_completion_quote_attempts += 1
            else:
                if self._research_normal_quote_attempts >= self.research_normal_attempt_cap:
                    self._research_normal_attempt_cap_hits += 1
                    if self.debug_enabled:
                        record = self._book_record(book_id)
                        record['action'] = 'SKIP'
                        record['reason'] = 'NORMAL_MM_ATTEMPT_CAP'
                        record['scheduler_lane'] = lane
                    return 0
                self._research_normal_quote_attempts += 1
            self._research_quote_attempts += 1
        allow_relaxed_fill = completion_candidate and self._research_completion_relaxed_successes < self.research_kappa_completion_relaxed_success_cap
        old_min_fill = float(regime_params.min_fill_prob)
        relaxed_min_fill = old_min_fill
        if allow_relaxed_fill:
            relaxed_min_fill = max(self.research_kappa_completion_fill_floor, old_min_fill * self.research_kappa_completion_fill_mult)
            regime_params.min_fill_prob = min(old_min_fill, relaxed_min_fill)
            self._research_completion_relaxed_attempts += 1
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['scheduler_lane'] = lane
            record['normal_attempts_used'] = self._research_normal_quote_attempts
            record['normal_attempt_cap'] = self.research_normal_attempt_cap
            record['completion_attempts_used'] = self._research_completion_quote_attempts
            record['completion_attempt_cap'] = self.research_kappa_completion_attempt_cap
            record['completion_successes_used'] = self._research_completion_quote_successes
            record['completion_success_cap'] = self.research_kappa_completion_success_cap
            record['kappa_completion_candidate'] = completion_candidate
            record['kappa_completion_samples'] = completion_samples
            record['kappa_completion_target'] = self._required_observation_count()
            record['kappa_completion_fill_relaxed'] = allow_relaxed_fill
            record['kappa_completion_min_fill_original'] = old_min_fill
            record['kappa_completion_min_fill_effective'] = float(regime_params.min_fill_prob)
        old_expiry = int(self.mm_expiry_period)
        hold_sides: set[str] = set()
        ttl_reason = 'BASELINE'
        fill_hazard = None
        chosen_ttl = None
        if getattr(self, 'adaptive_ttl_enabled', True):
            chosen_ttl, ttl_reason, fill_hazard = self._choose_quote_ttl(
                book_id, profile, state, baseline_ns=old_expiry,
            )
            if chosen_ttl is None:
                self._ttl_stale_skips += 1
                if self.debug_enabled:
                    record = self._book_record(book_id)
                    record['action'] = 'SKIP'
                    record['reason'] = 'TTL_STALE'
                    record['ttl_reason'] = ttl_reason
                regime_params.min_fill_prob = old_min_fill
                return 0
            self.mm_expiry_period = ms_to_ns(chosen_ttl)
        if getattr(self, 'quote_hysteresis_enabled', True):
            hold_sides = self._hysteresis_hold_sides(
                state, book_id, book, profile, prediction, inventory, regime_params, edge_bias,
            )
        dust_prob, _usable = self._predicted_dust(int(book_id))
        self._quote_submit_snapshot[int(book_id)] = {
            'alpha': float(getattr(prediction, 'score', 0.0) or 0.0),
            'imbalance': None if profile is None else getattr(profile, 'imbalance', None),
            'inventory_util': float(getattr(inventory, 'inventory_ratio', 0.0) or 0.0),
            'toxic': str(getattr(self, '_market_regime', '') or '').upper() in {'TOXIC'} or int(book_id) in (getattr(self, '_research_parked_dust', {}) or {}),
            'quote_ev': None if (getattr(self, '_score_ev_last', {}) or {}).get(int(book_id)) is None else getattr(self._score_ev_last[int(book_id)], 'trading_ev', None),
            'chosen_ttl': chosen_ttl if chosen_ttl is not None else (sim_delta_ms(0, int(self.mm_expiry_period)) or 500.0),
            'ttl_reason': ttl_reason,
            'dust_probability': dust_prob,
        }
        qty_hint = float(size)
        buy_size = qty_hint
        sell_size = qty_hint
        band = str(getattr(inventory, 'band', '') or '').upper()
        if band == 'LONG':
            buy_size = qty_hint * 0.5
        elif band == 'SHORT':
            sell_size = qty_hint * 0.5
        skip_dust = self._dust_prevent_skip_sides(inventory, buy_size, sell_size, int(book_id))
        if skip_dust:
            self._dust_prevent_skips += len(skip_dust)
            hold_sides = set(hold_sides) | skip_dust
        orig_limit = getattr(response, 'limit_order', None)
        orig_record_fill = self._record_fill_quote
        if hold_sides and orig_limit is not None:
            def _gated_limit_order(*args, **kwargs):
                direction = kwargs.get('direction')
                if direction is None and len(args) >= 2:
                    direction = args[1]
                token = str(getattr(direction, 'name', direction)).upper()
                side = 'buy' if token in {'0', 'BUY', 'BID', 'ORDERDIRECTION.BUY'} else 'sell'
                if side in hold_sides:
                    return None
                return orig_limit(*args, **kwargs)

            def _gated_record_fill(mem, side, dist_from_touch):
                if str(side).lower() in hold_sides:
                    return None
                return orig_record_fill(mem, side, dist_from_touch)

            response.limit_order = _gated_limit_order
            self._record_fill_quote = _gated_record_fill
        if self.debug_enabled:
            record = self._book_record(book_id)
            record['chosen_ttl'] = self._quote_submit_snapshot[int(book_id)]['chosen_ttl']
            record['ttl_reason'] = ttl_reason
            record['dust_probability'] = dust_prob
            record['hysteresis_hold_buy'] = 'buy' in hold_sides
            record['hysteresis_hold_sell'] = 'sell' in hold_sides
        try:
            placed = self._bsimpl_2_Strategy1_Debug__place_skewed_quotes(response, state, book_id, book, profile, prediction, inventory, regime_params, size, edge_bias, stats=stats)
        finally:
            if hold_sides and orig_limit is not None:
                response.limit_order = orig_limit
                self._record_fill_quote = orig_record_fill
            regime_params.min_fill_prob = old_min_fill
            self.mm_expiry_period = old_expiry
        if self._research_backfill_active and placed:
            self._research_quote_successes += 1
            if completion_candidate:
                self._research_completion_quote_successes += 1
            else:
                self._research_normal_quote_successes += 1
        if completion_candidate and placed:
            if allow_relaxed_fill:
                self._research_completion_relaxed_successes += 1
            if self.debug_enabled:
                self._book_record(book_id)['kappa_completion_quote_success'] = True
        return placed

    def _bsimpl_3_Strategy1_Research_build_mm_strategy_instructions(self, response: FinanceAgentResponse, state: MarketSimulationStateUpdate, selection: BookSelection, predictions: dict[int, DirectionForecast], regime: MarketRegime, collect_archetypes: bool=True) -> dict:
        self._sync_exchange_constraints(state)
        overlay = str(getattr(regime, 'scoring_overlay', '')).upper()
        bootstrap = self.research_inactive_bootstrap and overlay == 'SCORING_PRESSURE'
        self._research_bootstrap_active = bootstrap
        old_skip_inactive = self.mm_skip_inactive_tier
        old_maintenance_mult = self.maintenance_size_mult
        old_max_mm_books = self.max_mm_books_per_tick
        self._research_backfill_active = bool(self.research_candidate_backfill)
        self._research_quote_success_cap = int(old_max_mm_books)
        self._research_quote_successes = 0
        self._research_quote_attempts = 0
        self._research_normal_quote_attempts = 0
        self._research_normal_quote_successes = 0
        self._research_completion_relaxed_successes = 0
        self._research_completion_relaxed_attempts = 0
        self._research_completion_quote_attempts = 0
        self._research_completion_quote_successes = 0
        self._research_completion_attempt_cap_hits = 0
        self._research_completion_success_cap_hits = 0
        self._research_normal_attempt_cap_hits = 0
        self._research_dust_compact_ids_this_tick = self._select_dust_compaction_books(state)
        if self._research_backfill_active:
            profile_scan = len(getattr(selection, 'profiles', []) or [])
            self.max_mm_books_per_tick = max(old_max_mm_books, self.research_candidate_attempt_cap, profile_scan)
        try:
            if bootstrap:
                self.mm_skip_inactive_tier = False
            if bootstrap and self.research_bootstrap_maintenance_min_order and (self._research_exchange_min_order_size > 0.0):
                maintenance_base = float(getattr(self, 'maintenance_order_size', 0.0) or 0.0)
                min_size = self._research_exchange_min_order_size
                if maintenance_base > 0.0 and min_size <= float(self.max_inventory_base) + 1e-12:
                    required_mult = min_size / maintenance_base
                    if required_mult > self.maintenance_size_mult:
                        self.maintenance_size_mult = required_mult
            stats = self._bsimpl_2_Strategy1_Debug_build_mm_strategy_instructions(response, state, selection, predictions, regime, collect_archetypes=collect_archetypes)
            if isinstance(stats, dict):
                stats['research_bootstrap_active'] = bootstrap
                stats['research_stress_spread_bps'] = self._research_stress_spread_bps
                stats['research_toxic_spread_bps'] = self._research_toxic_spread_bps
                stats['research_min_order_size'] = self._research_exchange_min_order_size
                stats['research_round_trip_closes'] = self._research_round_trip_closes
                stats['research_position_opens'] = self._research_position_opens
                stats['research_dust_blocks'] = self._research_dust_blocks
                stats['research_parked_dust'] = len(self._research_parked_dust)
                stats['research_dust_entries'] = self._research_dust_entries
                stats['research_dust_releases'] = self._research_dust_releases
                stats['research_dust_heartbeats'] = self._research_dust_heartbeats
                stats['research_dust_compact_selected'] = len(self._research_dust_compact_ids_this_tick)
                stats['research_dust_compact_attempts'] = self._research_dust_compact_attempts
                stats['research_dust_compact_orders'] = self._research_dust_compact_orders
                stats['research_dust_compact_fills'] = self._research_dust_compact_fills
                stats['dust_creation_count'] = getattr(self, '_dust_creation_count', 0)
                stats['dust_cleanup_attempts'] = self._research_dust_compact_attempts
                stats['dust_cleanup_successes'] = self._research_dust_compact_fills
                stats['hysteresis_holds'] = getattr(self, '_hysteresis_holds', 0)
                stats['hysteresis_replaces'] = getattr(self, '_hysteresis_replaces', 0)
                stats['dust_prevent_skips'] = getattr(self, '_dust_prevent_skips', 0)
                stats['research_quote_attempts'] = self._research_quote_attempts
                stats['research_normal_quote_attempts'] = self._research_normal_quote_attempts
                stats['research_normal_quote_successes'] = self._research_normal_quote_successes
                stats['research_normal_attempt_cap'] = self.research_normal_attempt_cap
                stats['research_completion_quote_attempts'] = self._research_completion_quote_attempts
                stats['research_completion_quote_successes'] = self._research_completion_quote_successes
                stats['research_completion_attempt_cap'] = self.research_kappa_completion_attempt_cap
                stats['research_completion_success_cap'] = self.research_kappa_completion_success_cap
                stats['research_completion_relaxed_attempts'] = self._research_completion_relaxed_attempts
                stats['research_completion_relaxed_successes'] = self._research_completion_relaxed_successes
                stats['research_completion_attempt_cap_hits'] = self._research_completion_attempt_cap_hits
                stats['research_completion_success_cap_hits'] = self._research_completion_success_cap_hits
                stats['research_normal_attempt_cap_hits'] = self._research_normal_attempt_cap_hits
                stats['research_quote_successes'] = self._research_quote_successes
                stats['research_quote_success_cap'] = self._research_quote_success_cap
                stats['research_flat_epsilon'] = self._execution_flat_epsilon()
            return stats
        finally:
            self.mm_skip_inactive_tier = old_skip_inactive
            self.maintenance_size_mult = old_maintenance_mult
            self.max_mm_books_per_tick = old_max_mm_books
            self._research_bootstrap_active = False
            self._research_backfill_active = False

    def _bsimpl_3_Strategy1_Research__emit_book_decision(self, state, regime, book_id: int, book, profile, prediction, record: dict[str, Any]) -> None:
        bid = book.bids[0].price if getattr(book, 'bids', None) else None
        ask = book.asks[0].price if getattr(book, 'asks', None) else None
        mid = (bid + ask) / 2.0 if bid is not None and ask is not None else None
        touch_spread_bps = None
        if mid and bid is not None and (ask is not None):
            touch_spread_bps = (ask - bid) / mid * 10000.0
        profile_spread_bps = self._profile_float(profile, 'spread_bps') if profile is not None else None
        mem = self._mem(book_id) if profile is not None else None
        reason = str(record.get('reason', DebugReason.NO_ACTION))
        if record.get('dust_quarantine') and reason in ('TOXIC_BOOK', 'TOXIC_REGIME'):
            reason = 'DUST_QUARANTINE'
        inactive_gate_bypassed = self.research_inactive_bootstrap and str(getattr(regime, 'scoring_overlay', '')).upper() == 'SCORING_PRESSURE'
        if reason == 'INACTIVE_TIER' and inactive_gate_bypassed and (not self.mm_skip_inactive_tier):
            reason = 'INACTIVE_DIAGNOSTIC_ONLY'
        self._debug_reason_counts[reason] += 1
        self._emit('DECISION', tick=self._tick, timestamp=getattr(state, 'timestamp', None), book_id=book_id, action=record.get('action', 'SKIP'), reason=reason, regime=getattr(regime, 'mode', None), overlay=getattr(regime, 'scoring_overlay', None), archetype=record.get('archetype'), archetype_source=record.get('archetype_source'), tier=getattr(profile, 'tier', None) if profile is not None else None, mid=mid, spread_bps=profile_spread_bps if profile_spread_bps is not None else touch_spread_bps, touch_spread_bps=touch_spread_bps, volatility=getattr(profile, 'volatility', None) if profile is not None else None, trade_rate=getattr(profile, 'trade_rate', None) if profile is not None else None, imbalance=getattr(profile, 'imbalance', None) if profile is not None else None, direction=getattr(prediction, 'direction', None) if prediction else None, signal=getattr(prediction, 'score', None) if prediction else None, expected_alpha=record.get('expected_alpha'), min_expected_alpha=self.min_expected_alpha, fill_buy=record.get('fill_buy'), fill_sell=record.get('fill_sell'), bid_px=record.get('bid_px'), ask_px=record.get('ask_px'), quantity=record.get('quantity'), expected_realized_pnl=record.get('expected_realized_pnl'), inventory=record.get('inventory'), instructions=record.get('instructions', 0), decision_ms=record.get('quote_ms', record.get('manage_ms')), loss_streak=record.get('loss_streak', getattr(mem, 'loss_streak', None) if mem is not None else None), recent_pnl=record.get('recent_pnl', getattr(mem, 'recent_pnl', None) if mem is not None else None), toxic_loss=record.get('toxic_loss'), toxic_pnl=record.get('toxic_pnl'), toxic_spread=record.get('toxic_spread'), toxic_archetype=record.get('toxic_archetype'), toxic_red_tier=record.get('toxic_red_tier'), stressed_by_spread=record.get('stressed_by_spread'), stressed_by_regime=record.get('stressed_by_regime'), legacy_stressed_by_regime=record.get('legacy_stressed_by_regime'), stress_spread_bps=record.get('stress_spread_bps', self._research_stress_spread_bps), toxic_spread_bps=record.get('toxic_spread_bps', self._research_toxic_spread_bps), min_order_size=record.get('min_order_size', self._research_exchange_min_order_size), dynamic_size_raw=record.get('dynamic_size_raw'), dynamic_size_final=record.get('dynamic_size_final'), size_promoted_to_min=record.get('size_promoted_to_min'), inactive_bootstrap=inactive_gate_bypassed, inactive_gate_bypassed=inactive_gate_bypassed and (not self.mm_skip_inactive_tier), dead_trade_rate_hit=record.get('dead_trade_rate_hit'), active_sparse=record.get('active_sparse'), active_sparse_tier=record.get('active_sparse_tier'), dust_quarantine=record.get('dust_quarantine'), dust_compact=record.get('dust_compact'), dust_compact_selected=record.get('dust_compact_selected'), scheduler_lane=record.get('scheduler_lane'), normal_attempts_used=record.get('normal_attempts_used'), normal_attempt_cap=record.get('normal_attempt_cap'), completion_attempts_used=record.get('completion_attempts_used'), completion_attempt_cap=record.get('completion_attempt_cap'), completion_successes_used=record.get('completion_successes_used'), completion_success_cap=record.get('completion_success_cap'), kappa_completion_candidate=record.get('kappa_completion_candidate'), kappa_completion_samples=record.get('kappa_completion_samples'), kappa_completion_target=record.get('kappa_completion_target'), kappa_completion_fill_relaxed=record.get('kappa_completion_fill_relaxed'), kappa_completion_min_fill_original=record.get('kappa_completion_min_fill_original'), kappa_completion_min_fill_effective=record.get('kappa_completion_min_fill_effective'), kappa_completion_quote_success=record.get('kappa_completion_quote_success'), maker_guard_post_only=record.get('maker_guard_post_only'), maker_guard_touch_safe=record.get('maker_guard_touch_safe'), maker_guard_forced=record.get('maker_guard_forced'), toxic_pnl_raw=record.get('toxic_pnl_raw'), toxic_pnl_samples=record.get('toxic_pnl_samples'), aggressive_touch_gross_bps=record.get('aggressive_touch_gross_bps'), aggressive_touch_net_bps=record.get('aggressive_touch_net_bps'), bootstrap_inactive=record.get('bootstrap_inactive'), inventory_util=record.get('inventory_util'), dust_position=record.get('dust_position'))

    def _bsimpl_3_Strategy1_Research__emit(self, event_type: str, force: bool=False, **payload: Any) -> None:
        # Production fast path: no JSON-safe conversion, timestamps, queue work,
        # or diagnostic aggregation when both telemetry layers are disabled.
        if not (getattr(self, 'debug_enabled', False) or getattr(self, 'research_enabled', False)):
            return
        if not getattr(self, 'debug_enabled', False) and (not force) and not getattr(self, 'research_enabled', False):
            return
        if event_type == 'RUN_SUMMARY':
            payload.setdefault('research_round_trip_closes', getattr(self, '_research_round_trip_closes', 0))
            payload.setdefault('research_position_opens', getattr(self, '_research_position_opens', 0))
            payload.setdefault('research_position_reductions', getattr(self, '_research_position_reductions', 0))
            payload.setdefault('research_dust_blocks', getattr(self, '_research_dust_blocks', 0))
            payload.setdefault('research_parked_dust_positions', len(getattr(self, '_research_parked_dust', {})))
            payload.setdefault('research_dust_entries', getattr(self, '_research_dust_entries', 0))
            payload.setdefault('research_dust_releases', getattr(self, '_research_dust_releases', 0))
            payload.setdefault('research_dust_heartbeats', getattr(self, '_research_dust_heartbeats', 0))
            payload.setdefault('research_dust_compact_attempts', getattr(self, '_research_dust_compact_attempts', 0))
            payload.setdefault('research_dust_compact_orders', getattr(self, '_research_dust_compact_orders', 0))
            payload.setdefault('research_dust_compact_fills', getattr(self, '_research_dust_compact_fills', 0))
            payload.setdefault('research_normal_quote_attempts', getattr(self, '_research_normal_quote_attempts', 0))
            payload.setdefault('research_normal_quote_successes', getattr(self, '_research_normal_quote_successes', 0))
            payload.setdefault('research_normal_attempt_cap', getattr(self, 'research_normal_attempt_cap', 0))
            payload.setdefault('research_completion_quote_attempts', getattr(self, '_research_completion_quote_attempts', 0))
            payload.setdefault('research_completion_quote_successes', getattr(self, '_research_completion_quote_successes', 0))
            payload.setdefault('research_completion_attempt_cap', getattr(self, 'research_kappa_completion_attempt_cap', 0))
            payload.setdefault('research_completion_success_cap', getattr(self, 'research_kappa_completion_success_cap', 0))
            payload.setdefault('research_completion_relaxed_attempts', getattr(self, '_research_completion_relaxed_attempts', 0))
            payload.setdefault('research_completion_relaxed_successes', getattr(self, '_research_completion_relaxed_successes', 0))
            payload.setdefault('research_completion_attempt_cap_hits', getattr(self, '_research_completion_attempt_cap_hits', 0))
            payload.setdefault('research_completion_success_cap_hits', getattr(self, '_research_completion_success_cap_hits', 0))
            payload.setdefault('research_normal_attempt_cap_hits', getattr(self, '_research_normal_attempt_cap_hits', 0))
            obs_counts = getattr(self, '_research_realized_observations_by_book', {})
            target = int(getattr(self, 'research_kappa_completion_target', 3))
            payload.setdefault('research_realized_observation_total', sum(obs_counts.values()))
            payload.setdefault('research_kappa_books_with_obs', sum((1 for v in obs_counts.values() if v > 0)))
            payload.setdefault('research_kappa_books_pending_1', sum((1 for v in obs_counts.values() if v == 1)))
            payload.setdefault('research_kappa_books_pending_2', sum((1 for v in obs_counts.values() if v == 2)))
            payload.setdefault('research_kappa_books_eligible', sum((1 for v in obs_counts.values() if v >= target)))
            payload.setdefault('research_parked_dust_abs_base', sum((abs(float(info.get('net_base', 0.0))) for info in getattr(self, '_research_parked_dust', {}).values())))
            try:
                current_tick = int(getattr(self, '_tick', 0) or 0)
                dust_registry = getattr(self, '_research_parked_dust', {})
                payload.setdefault('research_oldest_dust_ticks', max((max(0, current_tick - int(info.get('first_tick', current_tick))) for info in dust_registry.values()), default=0))
                payload.setdefault('research_open_positions', sum((1 for bid in getattr(self, '_open_positions', {}) if abs(float(self._position_tracker_snapshot(bid).net_qty)) >= self._execution_flat_epsilon())))
                payload.setdefault('research_actionable_open_positions', sum((1 for bid in getattr(self, '_open_positions', {}) if bid not in dust_registry and abs(float(self._position_tracker_snapshot(bid).net_qty)) >= self._execution_flat_epsilon())))
            except Exception:
                pass
        try:
            safe = self._json_safe(payload)
        except Exception:
            safe = payload
        record = {'type': event_type, 'agent_id': getattr(self, 'uid', None), 'wall_time_ns': time.time_ns(), **safe}
        if not getattr(self, '_research_ready', False):
            self._research_early.append(record)
            return
        if getattr(self, 'research_enabled', False):
            self._enqueue(record)

    def _bsimpl_3_Strategy1_Research__enqueue(self, record: dict[str, Any]) -> None:
        if self._rq is None:
            return
        try:
            self._rq.put_nowait(record)
        except queue.Full:
            self._rdropped += 1

    def _bsimpl_3_Strategy1_Research__writer_loop(self) -> None:
        assert self._rq is not None and self._rstop is not None
        while not self._rstop.is_set() or not self._rq.empty():
            try:
                record = self._rq.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if self._rfile is not None:
                    self._rfile.write(json.dumps(record, separators=(',', ':'), sort_keys=True, default=str) + '\n')
                if self.research_console and self._console_allowed(record):
                    line = self._format_human(record)
                    if line:
                        print(line, flush=True)
            except Exception as exc:
                try:
                    print(f'[S1R_ERROR] stage=telemetry error={self._short(exc)}', flush=True)
                except Exception:
                    pass
            finally:
                self._rq.task_done()

    def _bsimpl_3_Strategy1_Research__console_allowed(self, r: dict[str, Any]) -> bool:
        typ = str(r.get('type', ''))
        if typ in {'ERROR', 'RUN_SUMMARY', 'RESEARCH_CONFIG', 'DEBUG_CONFIG', 'POSITION', 'POSITION_GUARD', 'SLOW_REQUEST', 'REGIME'}:
            return True
        if typ == 'ORDER_LIFECYCLE':
            phase = str(r.get('phase', '')).upper()
            if any((token in phase for token in ('TRADE', 'FILL', 'REJECT', 'FAIL'))):
                return True
        tick = self._int(r.get('tick'))
        if tick is not None and tick != 1 and (tick % self.research_every_n != 0):
            return False
        book = self._int(r.get('book_id'))
        if self.research_book_id >= 0 and book is not None:
            return book == self.research_book_id
        return True

    def _bsimpl_3_Strategy1_Research__format_human(self, r: dict[str, Any]) -> str | None:
        typ = str(r.get('type', ''))
        if typ == 'RESEARCH_CONFIG':
            return f"[S1R_CONFIG] enabled={int(bool(r.get('enabled')))} every_n={r.get('every_n')} book={r.get('book_filter')} jsonl={int(bool(r.get('jsonl')))} queue={r.get('queue_size')} policy={self._short(r.get('policy_version'))} fix_global_stress={int(bool(r.get('fix_global_stress')))} neutral_fallback={int(bool(r.get('neutral_fallback')))} adaptive_spread={int(bool(r.get('adaptive_spread_thresholds')))} inactive_bootstrap={int(bool(r.get('inactive_bootstrap')))} bootstrap_dead_as_mm={int(bool(r.get('bootstrap_dead_as_mm')))} fix_inv={int(bool(r.get('fix_inventory_util')))} fix_reservation={int(bool(r.get('fix_quote_reservation')))} manage_min_clip={int(bool(r.get('bootstrap_manage_min_clip')))} close_age_gate={r.get('bootstrap_force_close_ticks')} touch_gate={int(bool(r.get('aggressive_close_touch_gate')))} touch_buffer_bps={self._fmt(r.get('aggressive_close_fee_buffer_bps'))} touch_min_net_bps={self._fmt(r.get('aggressive_close_min_net_bps'))} backfill={int(bool(r.get('candidate_backfill')))} attempt_cap={r.get('candidate_attempt_cap')} toxic_samples={r.get('toxic_pnl_min_samples')} yellow_sparse={int(bool(r.get('yellow_sparse_active')))} green_sparse={int(bool(r.get('green_sparse_active')))} dust_safe={int(bool(r.get('dust_safe_close')))} dust_park={int(bool(r.get('dust_park_enabled')))} dust_hb={r.get('dust_heartbeat_ticks')} dust_compact={int(bool(r.get('dust_compact_enabled')))} dust_compact_frac={self._fmt(r.get('dust_compact_min_fraction'))} kappa_complete={int(bool(r.get('kappa_completion_enabled')))} kappa_target={r.get('kappa_completion_target')} kappa_bonus={self._fmt(r.get('kappa_completion_rank_bonus'))} kappa_fill_mult={self._fmt(r.get('kappa_completion_fill_mult'))} kappa_attempt_cap={r.get('kappa_completion_attempt_cap')} kappa_success_cap={r.get('kappa_completion_success_cap')} normal_attempt_cap={r.get('normal_attempt_cap')} min_order_sync={int(bool(r.get('sync_min_order')))} run_id={self._short(r.get('run_id'))} file={self._short(r.get('output_file'))} base_policy={self._short(r.get('base_deploy_policy_version'))} mm_post_only={self._fmt(r.get('mm_force_post_only'))} maker_reprice={self._fmt(r.get('mm_maker_guard_reprice'))} slow_ms={self._fmt(r.get('debug_slow_request_ms'))}"
        if typ == 'DEBUG_CONFIG':
            return f"[S1R_CONFIG] debug_enabled={int(bool(r.get('enabled')))} debug_every_n={r.get('every_n')} debug_book={r.get('book_filter')} slow_request_ms={self._fmt(r.get('slow_request_ms'))}"
        if typ == 'REGIME':
            return f"[S1R_REGIME] tick={r.get('tick')} market={self._short(r.get('market_regime'))} score={self._short(r.get('score_regime'))} mode={self._short(r.get('mode'))} overlay={self._short(r.get('overlay'))} books={r.get('book_count')} inactive={r.get('inactive')} stressed={self._fmt(r.get('stressed_ratio'))} liquid={self._fmt(r.get('liquid_ratio'))} spread_med={self._fmt(r.get('spread_med'))} trade_med={self._fmt(r.get('trade_rate_med'))} pending_kappa={self._fmt(r.get('pending_kappa_frac'))} m_trig={self._short(r.get('market_trigger'))} s_trig={self._short(r.get('score_trigger'))}"
        if typ == 'TIMING':
            return f"[S1R_REQ] tick={r.get('tick')} sim_ts={r.get('timestamp')} instructions={r.get('instructions', 0)} notices={r.get('notices', 0)} update_ms={self._fmt(r.get('update_ms'))} respond_ms={self._fmt(r.get('respond_ms'))} report_ms={self._fmt(r.get('report_ms'))} total_ms={self._fmt(r.get('total_ms'))}"
        if typ == 'SLOW_REQUEST':
            return f"[S1R_SLOW] tick={r.get('tick')} total_ms={self._fmt(r.get('total_ms'))} threshold_ms={self._fmt(r.get('threshold_ms'))} max_stage={self._short(r.get('max_stage'))} max_stage_ms={self._fmt(r.get('max_stage_ms'))} update_ms={self._fmt(r.get('update_ms'))} respond_ms={self._fmt(r.get('respond_ms'))} report_ms={self._fmt(r.get('report_ms'))} instructions={r.get('instructions', 0)} books={r.get('book_count')} open_positions={r.get('open_positions')} parked_dust={r.get('parked_dust')}"
        if typ == 'DECISION':
            raw = str(r.get('reason', 'NO_ACTION'))
            reason = self.REASON_ALIAS.get(raw, raw)
            action = str(r.get('action', 'SKIP')).upper()
            inv = r.get('inventory') or {}
            common = f"tick={r.get('tick')} book={r.get('book_id')} regime={self._short(r.get('regime'))} overlay={self._short(r.get('overlay'))} archetype={self._short(r.get('archetype'))} arch_src={self._short(r.get('archetype_source'))} tier={self._short(r.get('tier'))} spread_bps={self._fmt(r.get('spread_bps'))} stress_cut={self._fmt(r.get('stress_spread_bps'))} toxic_cut={self._fmt(r.get('toxic_spread_bps'))} volatility={self._fmt(r.get('volatility'))} trade_rate={self._fmt(r.get('trade_rate'))} imbalance={self._fmt(r.get('imbalance'))} loss_streak={self._fmt(r.get('loss_streak'))} recent_pnl={self._fmt(r.get('recent_pnl'))} toxic_loss={self._fmt(r.get('toxic_loss'))} toxic_pnl={self._fmt(r.get('toxic_pnl'))} toxic_spread={self._fmt(r.get('toxic_spread'))} toxic_archetype={self._fmt(r.get('toxic_archetype'))} toxic_red_tier={self._fmt(r.get('toxic_red_tier'))} stressed_by_spread={self._fmt(r.get('stressed_by_spread'))} stressed_by_regime={self._fmt(r.get('stressed_by_regime'))} legacy_global_stress={self._fmt(r.get('legacy_stressed_by_regime'))} signal={self._fmt(r.get('signal'))} alpha={self._fmt(r.get('expected_alpha'))} min_alpha={self._fmt(r.get('min_expected_alpha'))} fill_bid={self._fmt(r.get('fill_buy'))} fill_ask={self._fmt(r.get('fill_sell'))} qty={self._fmt(r.get('quantity'))} dyn_raw={self._fmt(r.get('dynamic_size_raw'))} dyn_final={self._fmt(r.get('dynamic_size_final'))} min_order={self._fmt(r.get('min_order_size'))} promoted_min={self._fmt(r.get('size_promoted_to_min'))} bootstrap={self._fmt(r.get('inactive_bootstrap'))} inactive_bypass={self._fmt(r.get('inactive_gate_bypassed'))} dead_rate_hit={self._fmt(r.get('dead_trade_rate_hit'))} active_sparse={self._fmt(r.get('active_sparse'))} active_sparse_tier={self._short(r.get('active_sparse_tier'))} dust_quarantine={self._fmt(r.get('dust_quarantine'))} dust_compact={self._fmt(r.get('dust_compact'))} lane={self._fmt(r.get('scheduler_lane'))} normal_attempts={self._fmt(r.get('normal_attempts_used'))}/{self._fmt(r.get('normal_attempt_cap'))} completion_attempts={self._fmt(r.get('completion_attempts_used'))}/{self._fmt(r.get('completion_attempt_cap'))} kappa_complete={self._fmt(r.get('kappa_completion_candidate'))} kappa_samples={self._fmt(r.get('kappa_completion_samples'))} kappa_fill_relaxed={self._fmt(r.get('kappa_completion_fill_relaxed'))} maker_po={self._fmt(r.get('maker_guard_post_only'))} maker_touch_safe={self._fmt(r.get('maker_guard_touch_safe'))} maker_forced={self._fmt(r.get('maker_guard_forced'))} toxic_pnl_samples={self._fmt(r.get('toxic_pnl_samples'))} touch_net_bps={self._fmt(r.get('aggressive_touch_net_bps'))} inv_util={self._fmt(r.get('inventory_util'))} dust={self._fmt(r.get('dust_position'))} exp_pnl={self._fmt(r.get('expected_realized_pnl'))} inv_base={self._fmt(inv.get('net_base'))} inv_band={self._short(inv.get('band'))} instructions={r.get('instructions', 0)}"
            if action == 'SKIP':
                return f'[S1R_SKIP] {common} side=BOTH reason={reason} raw_reason={raw}'
            return f"[S1R_QUOTE] {common} action={action} reason={reason} bid={self._fmt(r.get('bid_px'))} ask={self._fmt(r.get('ask_px'))} decision_ms={self._fmt(r.get('decision_ms'))}"
        if typ == 'POSITION':
            return f"[S1R_POSITION] tick={r.get('tick')} book={r.get('book_id')} transition={self._short(r.get('transition'))} net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))} realized_delta={self._fmt(r.get('realized_pnl_delta'))} round_trip={self._fmt(r.get('round_trip'))} total_round_trips={r.get('round_trip_total')} book_samples={r.get('round_trip_book_samples')} realized_obs={r.get('realized_book_observations')} flat_eps={self._fmt(r.get('execution_flat_epsilon'))} reason={self._short(r.get('reason'))}"
        if typ == 'POSITION_GUARD':
            return f"[S1R_DUST] tick={r.get('tick')} book={r.get('book_id')} net_base={self._fmt(r.get('net_base'))} min_order={self._fmt(r.get('min_order_size'))} age_ticks={self._fmt(r.get('age_ticks'))} parked={self._fmt(r.get('parked'))} stale={self._fmt(r.get('stale'))} reason={self._short(r.get('reason'))}"
        if typ == 'ORDER_LIFECYCLE':
            phase = str(r.get('phase', 'UNKNOWN')).upper()
            book = r.get('book_id')
            if phase == 'SUBMITTED':
                p = r.get('instruction') or {}
                return f"[S1R_ORDER] tick={r.get('tick')} book={book} side={self._side(self._pick(p, 'direction', 'side'))} type={self._short(self._pick(p, 'orderType', 'order_type', 'type'))} price={self._fmt(self._pick(p, 'price', 'limitPrice', 'limit_price'))} qty={self._fmt(self._pick(p, 'quantity', 'qty', 'size'))} tif={self._short(self._pick(p, 'timeInForce', 'time_in_force', 'tif'))} client_id={self._short(self._pick(p, 'clientOrderId', 'client_order_id'))} index={r.get('instruction_index')}"
            e = r.get('event') or {}
            if 'TRADE' in phase or 'FILL' in phase:
                return f"[S1R_FILL] tick={r.get('tick')} book={book} phase={phase} side={self._side(self._pick(e, 'direction', 'side'))} price={self._fmt(self._pick(e, 'price', 'tradePrice', 'trade_price'))} qty={self._fmt(self._pick(e, 'quantity', 'qty', 'size'))} client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))} net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))}"
            if 'REJECT' in phase or 'FAIL' in phase:
                return f"[S1R_REJECT] tick={r.get('tick')} book={book} phase={phase} reason={self._short(self._pick(e, 'reason', 'message', 'status', 'error'), 240)} client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}"
            if 'CANCEL' in phase or 'EXPIRE' in phase:
                return f"[S1R_CANCEL] tick={r.get('tick')} book={book} phase={phase} reason={self._short(self._pick(e, 'reason', 'message', 'status'))} client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}"
            return f"[S1R_NOTICE] tick={r.get('tick')} book={book} phase={phase}"
        if typ == 'RUN_SUMMARY':
            avg = r.get('average_latency_ms') or {}
            mx = r.get('max_latency_ms') or {}
            return f"[S1R_SUMMARY] tick={r.get('tick')} responses={r.get('responses')} top_skips={self._counts(r.get('reason_counts') or {}, 8)} events={self._counts(r.get('event_counts') or {}, 8)} avg_total_ms={self._fmt(avg.get('total_ms'))} max_total_ms={self._fmt(mx.get('total_ms'))} opens={r.get('research_position_opens', 0)} reductions={r.get('research_position_reductions', 0)} round_trips={r.get('research_round_trip_closes', 0)} open_positions={r.get('research_open_positions', 0)} actionable_open={r.get('research_actionable_open_positions', 0)} parked_dust={r.get('research_parked_dust_positions', 0)} parked_dust_base={self._fmt(r.get('research_parked_dust_abs_base'))} dust_entries={r.get('research_dust_entries', 0)} dust_releases={r.get('research_dust_releases', 0)} dust_compact_orders={r.get('research_dust_compact_orders', 0)} dust_compact_fills={r.get('research_dust_compact_fills', 0)} oldest_dust_ticks={r.get('research_oldest_dust_ticks', 0)} kappa_eligible={r.get('research_kappa_books_eligible', 0)} kappa_pending1={r.get('research_kappa_books_pending_1', 0)} kappa_pending2={r.get('research_kappa_books_pending_2', 0)} normal_lane={r.get('research_normal_quote_attempts', 0)}/{r.get('research_normal_attempt_cap', 0)} completion_lane={r.get('research_completion_quote_attempts', 0)}/{r.get('research_completion_attempt_cap', 0)} completion_success={r.get('research_completion_quote_successes', 0)}/{r.get('research_completion_success_cap', 0)} dust_blocks={r.get('research_dust_blocks', 0)} queue_dropped={self._rdropped}"
        if typ == 'ERROR':
            return f"[S1R_ERROR] tick={r.get('tick')} stage={self._short(r.get('stage'))} type={self._short(r.get('error_type'))} error={self._short(r.get('error'), 400)}"
        return None

    def _bsimpl_3_Strategy1_Research__shutdown_research(self) -> None:
        if self._rstop is not None:
            self._rstop.set()
        if self._rq is not None:
            deadline = time.time() + 1.5
            while self._rq.unfinished_tasks and time.time() < deadline:
                time.sleep(0.01)
        if self._rworker is not None and self._rworker.is_alive():
            self._rworker.join(timeout=0.5)
        if self._rfile is not None:
            try:
                self._rfile.flush()
                self._rfile.close()
            except OSError:
                pass
            self._rfile = None

    @staticmethod
    def _bsimpl_3_Strategy1_Research__pick(obj: Any, *names: str) -> Any:
        if obj is None:
            return None
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    @staticmethod
    def _bsimpl_3_Strategy1_Research__int(v: Any) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _bsimpl_3_Strategy1_Research__fmt(v: Any) -> str:
        if v is None:
            return '-'
        if isinstance(v, bool):
            return '1' if v else '0'
        try:
            x = float(v)
        except (TypeError, ValueError):
            return str(v).replace(' ', '_')
        if abs(x) >= 1000:
            return f'{x:.3f}'
        if abs(x) >= 1:
            return f'{x:.6f}'.rstrip('0').rstrip('.')
        return f'{x:.8f}'.rstrip('0').rstrip('.') or '0'

    @staticmethod
    def _bsimpl_3_Strategy1_Research__short(v: Any, n: int=120) -> str:
        if v is None:
            return '-'
        return '_'.join(str(v).replace('\n', ' ').replace('\r', ' ').split())[:n]

    @classmethod
    def _bsimpl_3_Strategy1_Research__side(cls, v: Any) -> str:
        if v is None:
            return '-'
        s = str(v).upper()
        if 'BUY' in s or s == 'BID':
            return 'BID'
        if 'SELL' in s or s == 'ASK':
            return 'ASK'
        return cls._short(v)

    @staticmethod
    def _bsimpl_3_Strategy1_Research__counts(d: dict[str, Any], n: int) -> str:
        try:
            items = sorted(((str(k), int(v)) for k, v in d.items()), key=lambda kv: (-kv[1], kv[0]))[:n]
            return ','.join((f'{k}:{v}' for k, v in items)) or '-'
        except Exception:
            return '-'
    _accumulate_tuning_window = _bsimpl_1_Strategy1__accumulate_tuning_window
    _aggregate_book_memory_win_rate = _bsimpl_1_Strategy1__aggregate_book_memory_win_rate
    _allows_aggressive_close = _bsimpl_3_Strategy1_Research__allows_aggressive_close
    _alpha_regime_allows = _bsimpl_0_DetailedTemplateAgent__alpha_regime_allows
    _append_kappa_csv = _bsimpl_0_DetailedTemplateAgent__append_kappa_csv
    _append_pnl_csv = _bsimpl_0_DetailedTemplateAgent__append_pnl_csv
    _apply_tuning_overrides = _bsimpl_1_Strategy1__apply_tuning_overrides
    _apply_tuning_rules = _bsimpl_1_Strategy1__apply_tuning_rules
    _as_bool = _bsimpl_2_Strategy1_Debug__as_bool
    _assign_book_tier = _bsimpl_0_DetailedTemplateAgent__assign_book_tier
    _book_has_open_lots = _bsimpl_0_DetailedTemplateAgent__book_has_open_lots
    _book_matches = _bsimpl_2_Strategy1_Debug__book_matches
    _book_mid = _bsimpl_0_DetailedTemplateAgent__book_mid
    _book_record = _bsimpl_2_Strategy1_Debug__book_record
    _book_volatility = _bsimpl_0_DetailedTemplateAgent__book_volatility
    _can_add_volume = _bsimpl_0_DetailedTemplateAgent__can_add_volume
    _clamp_tuning_params = _bsimpl_1_Strategy1__clamp_tuning_params
    _clear_position_state = _bsimpl_1_Strategy1__clear_position_state
    _close_debug_file = _bsimpl_2_Strategy1_Debug__close_debug_file
    _complete_trading_reason = _bsimpl_2_Strategy1_Debug__complete_trading_reason
    _completion_observation_count = _bsimpl_3_Strategy1_Research__completion_observation_count
    _compute_alpha_rank = _bsimpl_0_DetailedTemplateAgent__compute_alpha_rank
    _compute_close_score = _bsimpl_3_Strategy1_Research__compute_close_score
    _compute_flow_f = _bsimpl_0_DetailedTemplateAgent__compute_flow_f
    _compute_l2_l5_imbalance = _bsimpl_1_Strategy1__compute_l2_l5_imbalance
    _compute_local_kappa = _bsimpl_0_DetailedTemplateAgent__compute_local_kappa
    _compute_trade_t = _bsimpl_0_DetailedTemplateAgent__compute_trade_t
    _compute_tuning_metrics = _bsimpl_1_Strategy1__compute_tuning_metrics
    _console_allowed = _bsimpl_3_Strategy1_Research__console_allowed
    _copy_positions_for_book = _bsimpl_0_DetailedTemplateAgent__copy_positions_for_book
    _count_book_instructions = _bsimpl_0_DetailedTemplateAgent__count_book_instructions
    _counts = _bsimpl_3_Strategy1_Research__counts
    _diagnose_quote_setup = _bsimpl_2_Strategy1_Debug__diagnose_quote_setup
    _dispatch_notice_event = _bsimpl_0_DetailedTemplateAgent__dispatch_notice_event
    _dust_compaction_safe_for_any_fill = _bsimpl_3_Strategy1_Research__dust_compaction_safe_for_any_fill
    _dust_fill_matches_recent_compaction = _bsimpl_3_Strategy1_Research__dust_fill_matches_recent_compaction
    _elapsed_ms = _bsimpl_2_Strategy1_Debug__elapsed_ms
    _emit = _bsimpl_3_Strategy1_Research__emit
    _emit_book_decision = _bsimpl_3_Strategy1_Research__emit_book_decision
    _emit_run_summary = _bsimpl_2_Strategy1_Debug__emit_run_summary
    _enqueue = _bsimpl_3_Strategy1_Research__enqueue
    _env_bool = _bsimpl_2_Strategy1_Debug__env_bool
    _env_int = _bsimpl_2_Strategy1_Debug__env_int
    _estimate_local_normalized_median = _bsimpl_0_DetailedTemplateAgent__estimate_local_normalized_median
    _estimate_plan_for_book = _bsimpl_0_DetailedTemplateAgent__estimate_plan_for_book
    _estimate_trade_fee = _bsimpl_0_DetailedTemplateAgent__estimate_trade_fee
    _event_payload = _bsimpl_2_Strategy1_Debug__event_payload
    _execute_aggressive_close = _bsimpl_3_Strategy1_Research__execute_aggressive_close
    _execution_flat_epsilon = _bsimpl_3_Strategy1_Research__execution_flat_epsilon
    _fast_update = _bsimpl_0_DetailedTemplateAgent__fast_update
    _finalize_book_decisions = _bsimpl_2_Strategy1_Debug__finalize_book_decisions
    _fmt = _bsimpl_3_Strategy1_Research__fmt
    _format_human = _bsimpl_3_Strategy1_Research__format_human
    _get = _bsimpl_2_Strategy1_Debug__get
    _global_book_rank = _bsimpl_3_Strategy1_Research__global_book_rank
    _instruction_counts_by_book = _bsimpl_2_Strategy1_Debug__instruction_counts_by_book
    _instruction_payload = _bsimpl_2_Strategy1_Debug__instruction_payload
    _int = _bsimpl_3_Strategy1_Research__int
    _inventory_needs_management = _bsimpl_3_Strategy1_Research__inventory_needs_management
    _inventory_record_needs_management = _bsimpl_2_Strategy1_Debug__inventory_record_needs_management
    _inventory_urgency = _bsimpl_1_Strategy1__inventory_urgency
    _inventory_util = _bsimpl_3_Strategy1_Research__inventory_util
    _is_compactable_dust = _bsimpl_3_Strategy1_Research__is_compactable_dust
    _is_dust_qty = _bsimpl_3_Strategy1_Research__is_dust_qty
    _is_kappa_completion_candidate = _bsimpl_3_Strategy1_Research__is_kappa_completion_candidate
    _json_safe = _bsimpl_2_Strategy1_Debug__json_safe
    _kappa_factor_from_tier = _bsimpl_1_Strategy1__kappa_factor_from_tier
    _learned_side_fill_prob = _bsimpl_1_Strategy1__learned_side_fill_prob
    _log_book_memory_sample = _bsimpl_1_Strategy1__log_book_memory_sample
    _log_book_profile_selection = _bsimpl_0_DetailedTemplateAgent__log_book_profile_selection
    _log_direction_predictions = _bsimpl_0_DetailedTemplateAgent__log_direction_predictions
    _log_input = _bsimpl_0_DetailedTemplateAgent__log_input
    _log_kappa_and_sequences = _bsimpl_0_DetailedTemplateAgent__log_kappa_and_sequences
    _log_kappa_strategy_calibration = _bsimpl_0_DetailedTemplateAgent__log_kappa_strategy_calibration
    _log_market_regime = _bsimpl_0_DetailedTemplateAgent__log_market_regime
    _log_mm_strategy = _bsimpl_1_Strategy1__log_mm_strategy
    _log_momentum_and_pnl = _bsimpl_0_DetailedTemplateAgent__log_momentum_and_pnl
    _log_notices = _bsimpl_2_Strategy1_Debug__log_notices
    _log_output = _bsimpl_0_DetailedTemplateAgent__log_output
    _log_predict_pnl = _bsimpl_0_DetailedTemplateAgent__log_predict_pnl
    _log_submitted_instructions = _bsimpl_2_Strategy1_Debug__log_submitted_instructions
    _maintenance_allowed = _bsimpl_1_Strategy1__maintenance_allowed
    _manage_inventory = _bsimpl_3_Strategy1_Research__manage_inventory
    _match_trade_fifo = _bsimpl_0_DetailedTemplateAgent__match_trade_fifo
    _maybe_run_tuning_scheduler = _bsimpl_1_Strategy1__maybe_run_tuning_scheduler
    _mem = _bsimpl_3_Strategy1_Research__mem
    _net_inventory = _bsimpl_3_Strategy1_Research__net_inventory
    _normalize_momentum = _bsimpl_0_DetailedTemplateAgent__normalize_momentum
    _object_payload = _bsimpl_2_Strategy1_Debug__object_payload
    _passes_expected_pnl_gate = _bsimpl_1_Strategy1__passes_expected_pnl_gate
    _passes_fee_gate = _bsimpl_0_DetailedTemplateAgent__passes_fee_gate
    _percentile = _bsimpl_3_Strategy1_Research__percentile
    _persist_tuning_state = _bsimpl_1_Strategy1__persist_tuning_state
    _pick = _bsimpl_3_Strategy1_Research__pick
    _place_directional_round_trip = _bsimpl_2_Strategy1_Debug__place_directional_round_trip
    _place_passive_inventory_exit = _bsimpl_1_Strategy1__place_passive_inventory_exit
    _place_round_trip_limits = _bsimpl_0_DetailedTemplateAgent__place_round_trip_limits
    _place_skewed_quotes = _bsimpl_3_Strategy1_Research__place_skewed_quotes
    _pnl_observation_count = _bsimpl_0_DetailedTemplateAgent__pnl_observation_count
    _position_tracker_snapshot = _bsimpl_1_Strategy1__position_tracker_snapshot
    _predict_all_books = _bsimpl_2_Strategy1_Debug__predict_all_books
    _prefer_maker = _bsimpl_0_DetailedTemplateAgent__prefer_maker
    _profile_float = _bsimpl_3_Strategy1_Research__profile_float
    _prune_pnl_history = _bsimpl_0_DetailedTemplateAgent__prune_pnl_history
    _realized_pnl_lookback = _bsimpl_0_DetailedTemplateAgent__realized_pnl_lookback
    _realized_pnl_sequences_per_book = _bsimpl_0_DetailedTemplateAgent__realized_pnl_sequences_per_book
    _reason_from_client_id = _bsimpl_1_Strategy1__reason_from_client_id
    _record_fill_hit = _bsimpl_1_Strategy1__record_fill_hit
    _record_fill_quote = _bsimpl_1_Strategy1__record_fill_quote
    _record_latency = _bsimpl_2_Strategy1_Debug__record_latency
    _refresh_dust_state = _bsimpl_3_Strategy1_Research__refresh_dust_state
    _reload_tuning_config_if_changed = _bsimpl_1_Strategy1__reload_tuning_config_if_changed
    _reset_pnl_state = _bsimpl_1_Strategy1__reset_pnl_state
    _round_order_size = _bsimpl_0_DetailedTemplateAgent__round_order_size
    _schedule_maintenance_books = _bsimpl_1_Strategy1__schedule_maintenance_books
    _select_dust_compaction_books = _bsimpl_3_Strategy1_Research__select_dust_compaction_books
    _short = _bsimpl_3_Strategy1_Research__short
    _should_emit_tick = _bsimpl_2_Strategy1_Debug__should_emit_tick
    _shutdown_research = _bsimpl_3_Strategy1_Research__shutdown_research
    _side = _bsimpl_3_Strategy1_Research__side
    _simulate_fifo_pnl = _bsimpl_0_DetailedTemplateAgent__simulate_fifo_pnl
    _snapshot_tuning_params = _bsimpl_1_Strategy1__snapshot_tuning_params
    _sparse_active_tier_enabled = _bsimpl_3_Strategy1_Research__sparse_active_tier_enabled
    _spread_bps = _bsimpl_0_DetailedTemplateAgent__spread_bps
    _spread_dist_bucket = _bsimpl_1_Strategy1__spread_dist_bucket
    _summarize_pnl_history = _bsimpl_0_DetailedTemplateAgent__summarize_pnl_history
    _sync_exchange_constraints = _bsimpl_3_Strategy1_Research__sync_exchange_constraints
    _sync_kappa_factor = _bsimpl_1_Strategy1__sync_kappa_factor
    _tier_mm_boost = _bsimpl_1_Strategy1__tier_mm_boost
    _timed = _bsimpl_2_Strategy1_Debug__timed
    _total_traded_volume = _bsimpl_0_DetailedTemplateAgent__total_traded_volume
    _trade_persistence = _bsimpl_1_Strategy1__trade_persistence
    _truncate_pnl_sequences = _bsimpl_0_DetailedTemplateAgent__truncate_pnl_sequences
    _try_close_loans = _bsimpl_1_Strategy1__try_close_loans
    _update_book_specialization = _bsimpl_1_Strategy1__update_book_specialization
    _update_direction_accuracy = _bsimpl_1_Strategy1__update_direction_accuracy
    _update_momentum = _bsimpl_0_DetailedTemplateAgent__update_momentum
    _update_spread_thresholds = _bsimpl_3_Strategy1_Research__update_spread_thresholds
    _update_trade_sign_history = _bsimpl_1_Strategy1__update_trade_sign_history
    _volume_cap_quote = _bsimpl_0_DetailedTemplateAgent__volume_cap_quote
    _volume_cap_remaining = _bsimpl_0_DetailedTemplateAgent__volume_cap_remaining
    _wealth_per_book = _bsimpl_1_Strategy1__wealth_per_book
    _writer_loop = _bsimpl_3_Strategy1_Research__writer_loop
    build_all_book_profiles = _bsimpl_0_DetailedTemplateAgent_build_all_book_profiles
    build_book_profile = _bsimpl_0_DetailedTemplateAgent_build_book_profile
    build_demo_instructions = _bsimpl_0_DetailedTemplateAgent_build_demo_instructions
    build_kappa_strategy_instructions = _bsimpl_0_DetailedTemplateAgent_build_kappa_strategy_instructions
    build_mm_strategy_instructions = _bsimpl_3_Strategy1_Research_build_mm_strategy_instructions
    classify_book_archetype = _bsimpl_3_Strategy1_Research_classify_book_archetype
    classify_market_regime = _bsimpl_0_DetailedTemplateAgent_classify_market_regime
    classify_market_regime_from_profiles = _bsimpl_3_Strategy1_Research_classify_market_regime_from_profiles
    coverage_priority = _bsimpl_1_Strategy1_coverage_priority
    dynamic_order_size = _bsimpl_3_Strategy1_Research_dynamic_order_size
    estimate_fill_probability = _bsimpl_3_Strategy1_Research_estimate_fill_probability
    estimate_realized_pnl = _bsimpl_0_DetailedTemplateAgent_estimate_realized_pnl
    estimate_round_trip_pnl = _bsimpl_0_DetailedTemplateAgent_estimate_round_trip_pnl
    expected_alpha_score = _bsimpl_2_Strategy1_Debug_expected_alpha_score
    get_archetype_edge_bias = _bsimpl_1_Strategy1_get_archetype_edge_bias
    get_regime_params = _bsimpl_3_Strategy1_Research_get_regime_params
    handle = _bsimpl_3_Strategy1_Research_handle
    initialize = _bsimpl_3_Strategy1_Research_initialize
    is_toxic_book = _bsimpl_3_Strategy1_Research_is_toxic_book
    merge_regime_and_archetype_params = _bsimpl_1_Strategy1_merge_regime_and_archetype_params
    microprice_signal = _bsimpl_1_Strategy1_microprice_signal
    onStart = _bsimpl_0_DetailedTemplateAgent_onStart
    onTrade = _bsimpl_3_Strategy1_Research_onTrade
    parse_account = _bsimpl_0_DetailedTemplateAgent_parse_account
    parse_book = _bsimpl_0_DetailedTemplateAgent_parse_book
    parse_notices = _bsimpl_0_DetailedTemplateAgent_parse_notices
    parse_response = _bsimpl_0_DetailedTemplateAgent_parse_response
    parse_state = _bsimpl_0_DetailedTemplateAgent_parse_state
    predict_direction = _bsimpl_1_Strategy1_predict_direction
    rank_books_for_trading = _bsimpl_0_DetailedTemplateAgent_rank_books_for_trading
    report = _bsimpl_0_DetailedTemplateAgent_report
    respond = _bsimpl_3_Strategy1_Research_respond
    select_books_for_trading = _bsimpl_2_Strategy1_Debug_select_books_for_trading
    skewed_quote_prices = _bsimpl_3_Strategy1_Research_skewed_quote_prices
    update = _bsimpl_0_DetailedTemplateAgent_update
if __name__ == '__main__':
    launch(BaseStrategy)
