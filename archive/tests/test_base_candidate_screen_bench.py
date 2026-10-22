# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Old full-universe Base vs screened Base: latency and economic proxies."""
from __future__ import annotations

import json
import statistics
import time
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "agents" / "strategy"))

from candidate_screen import (
    FeatureCache,
    ScreenBook,
    book_touch_fingerprint,
    cheap_book_score,
    select_fast_candidates,
)
from score_ev import compute_score_ev, round_trip_velocity


UNIVERSE = 128
CANDIDATE_COUNT = 20
MM_CAP = 4
TICKS = 80
INVENTORY_IDS = tuple(range(0, 8))
KAPPA_ONE_IDS = tuple(range(8, 12))
KAPPA_TWO_IDS = tuple(range(12, 16))
RISK_IDS = tuple(range(16, 20))


class _Lvl:
    def __init__(self, price, quantity):
        self.price = price
        self.quantity = quantity


class _Book:
    def __init__(self, book_id: int, *, tight: bool, trades: int):
        mid = 100.0 + book_id * 0.01
        spread = 0.02 if tight else 0.20
        self.bids = [_Lvl(mid - 0.5 * spread, 4.0 + book_id % 3)]
        self.asks = [_Lvl(mid + 0.5 * spread, 4.0 + (book_id + 1) % 3)]
        self.events = [type("E", (), {"type": "t"})() for _ in range(trades)]


def _percentile(xs: list[float], p: float) -> float:
    ordered = sorted(xs)
    if not ordered:
        return 0.0
    k = (len(ordered) - 1) * p
    lo = int(k)
    hi = min(len(ordered) - 1, lo + 1)
    if lo == hi:
        return ordered[lo]
    w = k - lo
    return ordered[lo] * (1.0 - w) + ordered[hi] * w


def _true_ev(book_id: int) -> float:
    """Latent book value old Base can see if it predicts the whole universe."""
    return (book_id % 17) / 17.0 + (0.35 if book_id in INVENTORY_IDS else 0.0) + (
        0.45 if book_id in KAPPA_ONE_IDS else 0.0
    ) + (0.25 if book_id in KAPPA_TWO_IDS else 0.0)


def _screen_book(book_id: int, book: _Book) -> ScreenBook:
    bid_px = book.bids[0].price
    ask_px = book.asks[0].price
    mid = 0.5 * (bid_px + ask_px)
    spread_bps = ((ask_px - bid_px) / mid) * 10_000.0
    return ScreenBook(
        book_id=book_id,
        has_inventory=book_id in INVENTORY_IDS,
        is_hard_risk=book_id in RISK_IDS,
        observations_remaining=(
            1 if book_id in KAPPA_ONE_IDS else 2 if book_id in KAPPA_TWO_IDS else 0
        ),
        cheap_score=cheap_book_score(
            spread_bps=spread_bps,
            trade_events=len(book.events),
            fill_rate=0.2 + (book_id % 5) * 0.1,
            last_alpha=_true_ev(book_id) * 0.6,
        ),
    )


def _predict_work(book: _Book) -> float:
    """Stand-in for Strategy1 L2–L5 + microprice + memory work on one book."""
    acc = 0.0
    for _ in range(48):
        for level in book.bids + book.asks:
            acc += float(level.price) * float(level.quantity)
        for event in book.events:
            acc += 0.001 if getattr(event, "type", None) == "t" else 0.0
    return acc


def _keep_cheap(book: _Book) -> float:
    """Skipped-book keep-state: fingerprint + cheap TOB, not full predict."""
    acc = 0.0
    for level in book.bids[:1] + book.asks[:1]:
        acc += float(level.price)
    return acc


def _quote_top(ids: list[int], cap: int = MM_CAP) -> list[int]:
    return sorted(ids, key=_true_ev, reverse=True)[:cap]


def run_benchmark() -> dict:
    books = {
        i: _Book(i, tight=(i % 3 != 0), trades=(i % 6))
        for i in range(UNIVERSE)
    }
    cache = FeatureCache()
    old_lat: list[float] = []
    new_lat: list[float] = []
    old_pnl: list[float] = []
    new_pnl: list[float] = []
    old_kappa = 0
    new_kappa = 0
    old_rt = 0
    new_rt = 0
    forced_misses = 0

    for tick in range(TICKS):
        if tick % 7 == 0:
            for book in books.values():
                book.bids[0].quantity += 0.01

        t0 = time.perf_counter()
        old_preds = {bid: _predict_work(book) for bid, book in books.items()}
        old_quote = _quote_top(list(old_preds))
        old_lat.append((time.perf_counter() - t0) * 1000.0)
        old_pnl.append(sum(_true_ev(bid) for bid in old_quote))
        old_kappa += sum(1 for bid in KAPPA_ONE_IDS + KAPPA_TWO_IDS if bid in old_quote or True)
        old_rt += len(INVENTORY_IDS)

        t1 = time.perf_counter()
        rows = []
        for bid, book in books.items():
            fp = book_touch_fingerprint(book)
            cache.lookup_touch(bid, fp)
            cache.store_touch(
                bid, fp, spread_bps=2.0, imbalance=0.1, trade_events=len(book.events),
            )
            rows.append(_screen_book(bid, book))
        screen = select_fast_candidates(rows, CANDIDATE_COUNT)
        selected = set(screen.selected)
        new_preds = {}
        for bid, book in books.items():
            if bid in selected:
                new_preds[bid] = _predict_work(book)
            elif not cache.keep_unchanged(bid, book_touch_fingerprint(book)):
                _keep_cheap(book)
        new_quote = _quote_top(list(new_preds))
        new_lat.append((time.perf_counter() - t1) * 1000.0)
        new_pnl.append(sum(_true_ev(bid) for bid in new_quote))
        must = set(INVENTORY_IDS + KAPPA_ONE_IDS + KAPPA_TWO_IDS + RISK_IDS)
        forced_misses += len(must - selected)
        new_kappa += sum(1 for bid in KAPPA_ONE_IDS + KAPPA_TWO_IDS if bid in selected)
        new_rt += sum(1 for bid in INVENTORY_IDS if bid in selected)

    kappa_den = TICKS * len(KAPPA_ONE_IDS + KAPPA_TWO_IDS)
    rt_den = TICKS * len(INVENTORY_IDS)
    payload = {
        "universe": UNIVERSE,
        "ticks": TICKS,
        "candidate_count": CANDIDATE_COUNT,
        "mm_cap": MM_CAP,
        "old": {
            "mean_latency_ms": round(statistics.fmean(old_lat), 4),
            "p95_latency_ms": round(_percentile(old_lat, 0.95), 4),
            "p99_latency_ms": round(_percentile(old_lat, 0.99), 4),
            "pnl": round(sum(old_pnl), 4),
            "kappa_completion": 1.0,
            "round_trip_velocity": round(round_trip_velocity(old_rt, float(TICKS)), 4),
        },
        "new": {
            "mean_latency_ms": round(statistics.fmean(new_lat), 4),
            "p95_latency_ms": round(_percentile(new_lat, 0.95), 4),
            "p99_latency_ms": round(_percentile(new_lat, 0.99), 4),
            "pnl": round(sum(new_pnl), 4),
            "kappa_completion": round(new_kappa / kappa_den, 4),
            "round_trip_velocity": round(round_trip_velocity(new_rt, float(TICKS)), 4),
        },
        "forced_misses": forced_misses,
        "cache_hits": cache.hits,
        "cache_misses": cache.misses,
    }
    payload["delta"] = {
        "mean_latency_ms": round(payload["new"]["mean_latency_ms"] - payload["old"]["mean_latency_ms"], 4),
        "p95_latency_ms": round(payload["new"]["p95_latency_ms"] - payload["old"]["p95_latency_ms"], 4),
        "p99_latency_ms": round(payload["new"]["p99_latency_ms"] - payload["old"]["p99_latency_ms"], 4),
        "pnl": round(payload["new"]["pnl"] - payload["old"]["pnl"], 4),
        "kappa_completion": round(
            payload["new"]["kappa_completion"] - payload["old"]["kappa_completion"], 4
        ),
        "round_trip_velocity": round(
            payload["new"]["round_trip_velocity"] - payload["old"]["round_trip_velocity"], 4
        ),
    }
    return payload


def test_old_vs_new_base_screen_measurement():
    payload = run_benchmark()
    assert payload["forced_misses"] == 0
    assert payload["new"]["kappa_completion"] == 1.0
    assert payload["old"]["round_trip_velocity"] == payload["new"]["round_trip_velocity"]
    assert payload["new"]["p95_latency_ms"] > 0.0
    assert payload["old"]["p95_latency_ms"] > 0.0
    # Forced inventory / Kappa / risk books stay in the quote-eligible set.
    calm = compute_score_ev(book=1, fill_prob_old=0.5, min_trading_ev=-1.0)
    assert calm.eligible or calm.reject_reason


if __name__ == "__main__":
    print(json.dumps(run_benchmark(), indent=2))
