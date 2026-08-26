# SPDX-FileCopyrightText: 2026 Rayleigh Research <to@rayleigh.re>
# SPDX-License-Identifier: MIT
"""Phase 2 final verification: old Base (v4.1.1) vs new Base (screened champion).

Closed-loop policy sim. Same market path for both agents.
OLD = archived v4.1.1 rank + late flatten + full-universe predict.
NEW = live Phase 2 Score-EV / realization / OFI / entry-size / screen.

This is a synthetic proxy, not live ScoreVelocity / validator JSONL.
"""
from __future__ import annotations

import json
import math
import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "agents" / "strategy"))

from candidate_screen import (  # noqa: E402
    FeatureCache,
    ScreenBook,
    ScreenResult,
    cheap_book_score,
    select_fast_candidates,
)
from entry_size import allowed_entry_size  # noqa: E402
from realization import ACTION_TAKER, evaluate_realization  # noqa: E402
from score_ev import (  # noqa: E402
    admit_scheduler_candidate,
    compute_score_ev,
    legacy_global_rank,
    round_trip_velocity,
)

BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text(encoding="utf-8")
OLD_BASE = (
    ROOT / "agents" / "strategy" / "__ver_base__" / "BaseStrategy_v4_1_1_strict.py"
).read_text(encoding="utf-8")
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text(encoding="utf-8")

UNIVERSE = 64
MM_CAP = 4
CANDIDATE_COUNT = 20
TICKS = 180
REQUIRED = 3
MIN_ALPHA = 0.18
MM_BASE = 0.25
MAX_INV = 1.20
MIN_ORDER = 0.05
FEE_BPS = 0.5
KAPPA_W = 0.79
PNL_W = 0.21
PNL_SCALE = 8.0
KAPPA_SCALE = 2.0

INVENTORY_IDS = tuple(range(0, 6))
KAPPA_ONE_IDS = tuple(range(6, 12))
KAPPA_TWO_IDS = tuple(range(12, 18))
TOXIC_IDS = tuple(range(18, 24))
RISK_IDS = tuple(range(24, 28))


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


def _kappa3(pnls: list[float], tau: float = 0.0) -> float:
    if len(pnls) < REQUIRED:
        return 0.0
    mu = statistics.fmean(pnls)
    lpm = statistics.fmean([max(tau - x, 0.0) ** 3 for x in pnls])
    if lpm <= 1e-12:
        upm = statistics.fmean([max(x - tau, 0.0) ** 3 for x in pnls])
        denom = upm ** (1.0 / 3.0) if upm > 0.0 else 1e-9
        return (mu - tau) / denom
    return (mu - tau) / (lpm ** (1.0 / 3.0))


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


@dataclass
class BookState:
    book_id: int
    alpha: float
    fill_p: float
    spread_bps: float
    toxicity: float
    ofi_against: float
    markout_bps: float
    vol: float
    trades: int
    inventory: float = 0.0
    age: float = 0.0
    obs: int = 0
    dust: float = 0.0
    recent_pnl: float = 0.0
    last_quote_px: float | None = None
    quoted: bool = False


@dataclass
class AgentStats:
    latencies: list[float] = field(default_factory=list)
    pnls: list[float] = field(default_factory=list)
    maker_pnls: list[float] = field(default_factory=list)
    taker_pnls: list[float] = field(default_factory=list)
    opens: int = 0
    trips: int = 0
    maker_fills: int = 0
    taker_fills: int = 0
    dust_events: int = 0
    quote_holds: int = 0
    forced_misses: int = 0
    cache_hits: int = 0
    cache_misses: int = 0


def _seed_books() -> dict[int, BookState]:
    books: dict[int, BookState] = {}
    for i in range(UNIVERSE):
        toxic = i in TOXIC_IDS
        kappa1 = i in KAPPA_ONE_IDS
        kappa2 = i in KAPPA_TWO_IDS
        inv = i in INVENTORY_IDS
        alpha = 0.10 + (i % 11) * 0.03
        if toxic:
            alpha = 0.42
        if kappa1:
            alpha = 0.22
        if kappa2:
            alpha = 0.20
        books[i] = BookState(
            book_id=i,
            alpha=alpha,
            fill_p=0.18 if toxic else 0.34 + (i % 5) * 0.04,
            spread_bps=1.6 + (i % 4) * 0.4,
            toxicity=0.85 if toxic else 0.08 + (i % 7) * 0.02,
            ofi_against=0.70 if toxic else 0.05,
            markout_bps=-9.0 if toxic else 1.2 - (i % 6) * 0.15,
            vol=0.006 if toxic else 0.002,
            trades=1 + (i % 8),
            inventory=0.35 if inv else 0.0,
            age=6.0 if inv else 0.0,
            obs=2 if kappa1 else 1 if kappa2 else 0,
        )
    return books


def _clone(books: dict[int, BookState]) -> dict[int, BookState]:
    return {k: BookState(**vars(v)) for k, v in books.items()}


def _predict_work(n: int) -> float:
    """Stand-in for Strategy1 L2–L5 + microprice + memory on n books."""
    acc = 0.0
    for book_i in range(n):
        mid = 100.0 + book_i * 0.01
        for _ in range(48):
            for level in range(5):
                acc += (mid - 0.01 * level) * (4.0 + (book_i + level) % 3)
            acc += 0.001 * ((book_i + _) % 6)
    return acc


def _keep_cheap(n: int) -> float:
    acc = 0.0
    for book_i in range(n):
        acc += 100.0 + book_i * 0.01
    return acc


def _old_select(books: dict[int, BookState]) -> list[int]:
    ranked: list[tuple[float, int]] = []
    for book in books.values():
        if book.alpha < MIN_ALPHA and book.inventory == 0.0 and book.obs == 0:
            continue
        spec = 0.2 if book.obs else 0.05
        rank = legacy_global_rank(book.alpha, spec)
        if 0 < book.obs < REQUIRED:
            progress = book.obs / float(REQUIRED - 1)
            rank += 0.30 * progress
        if book.inventory:
            rank += 0.05
        ranked.append((rank, book.book_id))
    ranked.sort(reverse=True)
    return [bid for _rank, bid in ranked[:MM_CAP]]


def _new_select(
    books: dict[int, BookState],
    cache: FeatureCache,
) -> tuple[list[int], ScreenResult]:
    screen_books = []
    for book in books.values():
        remaining = max(0, REQUIRED - book.obs)
        cheap = cheap_book_score(
            spread_bps=book.spread_bps,
            trade_events=book.trades,
            fill_rate=book.fill_p,
            last_alpha=book.alpha,
        )
        screen_books.append(
            ScreenBook(
                book_id=book.book_id,
                has_inventory=abs(book.inventory) > 1e-9,
                is_dust=book.dust > 1e-9,
                observations_remaining=remaining,
                is_hard_risk=book.book_id in RISK_IDS or book.toxicity >= 0.7,
                cheap_score=cheap,
            )
        )
    result = select_fast_candidates(screen_books, candidate_count=CANDIDATE_COUNT)
    selected = list(result.selected)
    ev_rows: list[tuple[float, int]] = []
    quote_successes = 0
    completion_attempts = 0
    completion_successes = 0
    normal_attempts = 0
    for bid in selected:
        book = books[bid]
        remaining = max(0, REQUIRED - book.obs)
        lane = "COMPLETION" if 0 < remaining <= 2 else "NORMAL"
        ev = compute_score_ev(
            book=bid,
            alpha=book.alpha,
            fill_prob_old=book.fill_p,
            fill_prob_hazard=book.fill_p,
            hazard_usable=True,
            dust_prob=0.35 if book.dust else 0.08,
            spread_capture_bps=book.spread_bps,
            expected_markout_override=book.markout_bps,
            ofi_against=book.ofi_against,
            fees_bps=FEE_BPS,
            realized_observation_count=book.obs,
            required=REQUIRED,
            inventory_util=abs(book.inventory) / MAX_INV,
            toxic=book.toxicity >= 0.7,
            inventory_blocked=abs(book.inventory) >= MAX_INV - 1e-9,
            min_trading_ev=0.0,
        )
        if not ev.eligible:
            continue
        admitted, _reason = admit_scheduler_candidate(
            lane=lane,
            quote_successes=quote_successes,
            quote_success_cap=MM_CAP,
            completion_attempts=completion_attempts,
            completion_attempt_cap=4,
            completion_successes=completion_successes,
            completion_success_cap=2,
            normal_attempts=normal_attempts,
            normal_attempt_cap=4,
        )
        if not admitted:
            continue
        if lane == "COMPLETION":
            completion_attempts += 1
        else:
            normal_attempts += 1
        ev_rows.append((ev.final_score, bid))
    ev_rows.sort(reverse=True)
    quoted = [bid for _score, bid in ev_rows[:MM_CAP]]
    for bid in quoted:
        remaining = max(0, REQUIRED - books[bid].obs)
        if 0 < remaining <= 2:
            completion_successes += 1
        quote_successes += 1
    return quoted, result


def _old_size(book: BookState) -> float:
    room = max(0.0, MAX_INV - abs(book.inventory))
    return min(MM_BASE, room)


def _new_size(book: BookState) -> float:
    if book.toxicity >= 0.7 and abs(book.inventory) <= 1e-9:
        return 0.0
    decision = allowed_entry_size(
        base_size=MM_BASE,
        existing_inventory=book.inventory,
        max_inventory=MAX_INV,
        inventory_age=book.age,
        volatility=book.vol,
        toxicity=book.toxicity,
        expected_markout=book.markout_bps,
        ofi_against=book.ofi_against,
        volume_cap_headroom=0.85,
        hard_max_entry=MM_BASE,
    )
    return float(decision.entry_size)


def _fill(book: BookState, size: float, *, maker: bool, rng_u: float) -> tuple[float, float, bool]:
    """Return (filled_qty, pnl, is_dust)."""
    p = book.fill_p * (0.85 if maker else 1.05)
    if rng_u > p:
        return 0.0, 0.0, False
    filled = size
    edge = book.spread_bps * (0.45 if maker else -0.15) + book.markout_bps - FEE_BPS
    pnl = (edge / 10_000.0) * filled * 100.0
    dust = filled < MIN_ORDER
    return filled, pnl, dust


def _close_old(book: BookState) -> bool:
    if abs(book.inventory) <= 1e-9:
        return False
    return (abs(book.inventory) / MAX_INV) >= 0.95 or book.age >= 20.0


def _close_new(book: BookState) -> tuple[bool, bool]:
    if abs(book.inventory) <= 1e-9:
        return False, False
    remaining = max(0, REQUIRED - book.obs)
    decision = evaluate_realization(
        book=book.book_id,
        inventory_size=book.inventory,
        inventory_ratio=abs(book.inventory) / MAX_INV,
        inventory_age=book.age,
        unrealized_pnl=book.recent_pnl * 10_000.0 if book.recent_pnl else 1.5,
        expected_markout=book.markout_bps,
        volatility=book.vol,
        ofi=-book.ofi_against,
        observations_remaining=remaining,
        spread_bps=book.spread_bps,
        fee_bps=FEE_BPS,
    )
    should = decision.exit_urgency >= 0.22 or remaining in {1, 2} or book.age >= 8.0
    taker = decision.selected_action == ACTION_TAKER
    return should, taker


def _record_pnl(book: BookState, qty: float, pnl: float, stats: AgentStats, *, maker: bool) -> None:
    if qty <= 0.0:
        return
    if maker:
        stats.maker_fills += 1
        stats.maker_pnls.append(pnl)
    else:
        stats.taker_fills += 1
        stats.taker_pnls.append(pnl)
    stats.pnls.append(pnl)
    book.recent_pnl = 0.7 * book.recent_pnl + 0.3 * pnl


def _open_position(book: BookState, qty: float, pnl: float, stats: AgentStats, *, maker: bool) -> None:
    if qty <= 1e-12:
        return
    if qty + 1e-12 < MIN_ORDER:
        book.dust += qty
        stats.dust_events += 1
        return
    _record_pnl(book, qty, pnl, stats, maker=maker)
    book.inventory = qty
    book.age = 1.0
    stats.opens += 1


def _close_position(book: BookState, qty: float, pnl: float, stats: AgentStats, *, maker: bool) -> None:
    close_qty = min(abs(book.inventory), qty)
    if close_qty <= 1e-12:
        return
    _record_pnl(book, close_qty, pnl, stats, maker=maker)
    remain = abs(book.inventory) - close_qty
    if remain <= 1e-6:
        book.obs += 1
        stats.trips += 1
        book.inventory = 0.0
        book.age = 0.0
        return
    if remain + 1e-12 < MIN_ORDER:
        book.dust += remain
        stats.dust_events += 1
        book.inventory = 0.0
        book.age = 0.0
        return
    book.inventory = math.copysign(remain, book.inventory)


def run_agent(kind: str, books: dict[int, BookState]) -> AgentStats:
    stats = AgentStats()
    cache = FeatureCache()
    for tick in range(TICKS):
        t0 = time.perf_counter()
        if kind == "old":
            _predict_work(UNIVERSE)
            quoted = _old_select(books)
        else:
            quoted, result = _new_select(books, cache)
            _predict_work(len(result.selected))
            _keep_cheap(max(0, UNIVERSE - len(result.selected)))
            forced_now = {
                bid
                for bid, book in books.items()
                if abs(book.inventory) > 1e-9
                or book.dust > 1e-9
                or 0 < max(0, REQUIRED - book.obs) <= 2
                or bid in RISK_IDS
            }
            present = set(result.selected)
            stats.forced_misses += sum(1 for bid in forced_now if bid not in present)
            fp = (tick % 3, 1.0)
            hit = cache.lookup_touch(0, fp)
            if hit is None:
                cache.store_touch(0, fp, spread_bps=2.0, imbalance=0.0, trade_events=1)
                stats.cache_misses += 1
            else:
                stats.cache_hits += 1

        for bid in quoted:
            book = books[bid]
            u = ((tick * 17 + bid * 13) % 1000) / 1000.0
            if kind == "old":
                if abs(book.inventory) > 1e-9:
                    if _close_old(book):
                        qty, pnl, _dust = _fill(book, abs(book.inventory), maker=True, rng_u=u)
                        _close_position(book, qty, pnl, stats, maker=True)
                    continue
                size = _old_size(book)
                if size + 1e-12 < MIN_ORDER:
                    continue
                qty, pnl, _dust = _fill(book, size, maker=True, rng_u=u)
                book.last_quote_px = book.alpha * 10
                _open_position(book, qty, pnl, stats, maker=True)
            else:
                if abs(book.inventory) > 1e-9:
                    should_close, taker = _close_new(book)
                    if should_close:
                        qty, pnl, _dust = _fill(
                            book,
                            abs(book.inventory),
                            maker=not taker,
                            rng_u=u,
                        )
                        _close_position(book, qty, pnl, stats, maker=not taker)
                    continue
                if book.last_quote_px is not None:
                    tick_move = abs((book.alpha * 10) - book.last_quote_px) / 0.01
                    if tick_move < 2.0 and book.ofi_against < 0.3:
                        stats.quote_holds += 1
                size = _new_size(book)
                if size + 1e-12 < MIN_ORDER:
                    continue
                qty, pnl, _dust = _fill(book, size, maker=True, rng_u=u)
                book.last_quote_px = book.alpha * 10
                _open_position(book, qty, pnl, stats, maker=True)

        for book in books.values():
            if abs(book.inventory) > 1e-9:
                book.age += 1.0
        stats.latencies.append((time.perf_counter() - t0) * 1000.0)
    return stats


def summarize(stats: AgentStats, books: dict[int, BookState]) -> dict[str, float]:
    eligible = sum(1 for b in books.values() if b.obs >= REQUIRED)
    sim_t = float(TICKS)
    rtv = round_trip_velocity(stats.trips, sim_t)
    conversion = stats.trips / max(1, stats.opens)
    realized = sum(stats.pnls)
    maker = sum(stats.maker_pnls)
    taker = sum(stats.taker_pnls)
    fills = stats.maker_fills + stats.taker_fills
    maker_ratio = stats.maker_fills / max(1, fills)
    dust_abs = sum(b.dust for b in books.values())
    dust_books = sum(1 for b in books.values() if b.dust > 1e-9)
    kappa = _kappa3(stats.pnls)
    kappa_score = _clip01((kappa + KAPPA_SCALE) / (2.0 * KAPPA_SCALE))
    pnl_score = _clip01((realized + PNL_SCALE) / (2.0 * PNL_SCALE))
    trading_score = KAPPA_W * kappa_score + PNL_W * pnl_score
    score_velocity = trading_score / sim_t
    downside = min(stats.pnls) if stats.pnls else 0.0
    p10 = _percentile(stats.pnls, 0.10) if stats.pnls else 0.0
    lpm3 = (
        statistics.fmean([max(-x, 0.0) ** 3 for x in stats.pnls])
        if stats.pnls
        else 0.0
    )
    return {
        "score_velocity": score_velocity,
        "trading_score": trading_score,
        "kappa_eligible_books": float(eligible),
        "kappa3": kappa,
        "round_trip_velocity": rtv,
        "round_trip_conversion": conversion,
        "realized_pnl": realized,
        "maker_pnl": maker,
        "taker_pnl": taker,
        "maker_ratio": maker_ratio,
        "downside_min": downside,
        "downside_p10": p10,
        "downside_lpm3": lpm3,
        "dust_abs": dust_abs,
        "dust_books": float(dust_books),
        "dust_events": float(stats.dust_events),
        "latency_mean": statistics.fmean(stats.latencies) if stats.latencies else 0.0,
        "latency_p95": _percentile(stats.latencies, 0.95),
        "latency_p99": _percentile(stats.latencies, 0.99),
        "opens": float(stats.opens),
        "trips": float(stats.trips),
        "forced_misses": float(stats.forced_misses),
        "quote_holds": float(stats.quote_holds),
    }


def compare() -> dict[str, dict[str, float]]:
    seed = _seed_books()
    old_books = _clone(seed)
    new_books = _clone(seed)
    old_stats = run_agent("old", old_books)
    new_stats = run_agent("new", new_books)
    old = summarize(old_stats, old_books)
    new = summarize(new_stats, new_books)
    delta = {k: new[k] - old[k] for k in old}
    return {"old": old, "new": new, "delta": delta}


def test_architecture_standalone_and_hard_caps():
    assert "class BaseStrategy(FinanceSimulationAgent)" in BASE
    assert "from Strategy1_Research import" not in BASE
    assert "import Strategy1_Research" not in BASE
    for helper in (
        "research_regime_v2",
        "research_score_ev",
        "research_candidate_screen",
        "research_fill_hazard",
        "research_realization",
        "research_quote_hysteresis",
        "research_adverse",
        "research_entry_size",
        "score_ev",
        "candidate_screen",
        "realization",
        "quote_hysteresis",
        "adverse",
        "entry_size",
    ):
        assert f"from {helper} import" not in BASE
        assert f"import {helper}" not in BASE
    assert "min_expected_alpha', 0.18)" in BASE or "min_expected_alpha', 0.18" in BASE
    assert "mm_base_size', 0.25)" in BASE
    assert "max_inventory_base', 1.2)" in BASE
    assert "max_mm_books_per_tick', 4)" in BASE
    assert "mm_force_post_only', True)" in BASE
    assert "def compute_score_ev" in BASE
    assert "def select_fast_candidates" in BASE
    assert "def evaluate_realization" in BASE
    assert "class AdaptiveAgent(BaseStrategy)" in ADAPTIVE
    assert "from BaseStrategy import" in ADAPTIVE
    assert OLD_BASE.count("class BaseStrategy") == 1
    assert "DEPLOY_POLICY_VERSION = 'base_v4_4_champion'" in BASE
    assert "BASE_CHAMPION = True" in BASE
    assert "BASE_CHAMPION_FROZEN = True" in BASE
    assert "BASE_CHAMPION_PARENT = 'base_v4_1_1_maker_guard'" in BASE


def test_old_vs_new_primary_gates():
    report = compare()
    old, new, delta = report["old"], report["new"], report["delta"]
    print("\nPHASE2_FINAL_VERIFY " + json.dumps(report, indent=2, sort_keys=True))

    assert new["score_velocity"] > old["score_velocity"]
    assert new["kappa_eligible_books"] >= old["kappa_eligible_books"]
    assert new["round_trip_velocity"] > old["round_trip_velocity"]
    assert new["round_trip_conversion"] > old["round_trip_conversion"]
    assert new["downside_lpm3"] <= old["downside_lpm3"] + 1e-9
    assert new["downside_min"] >= min(old["downside_min"], 0.0) - 1e-9
    assert new["maker_pnl"] + 1e-9 >= old["maker_pnl"] * 0.98
    assert new["maker_ratio"] + 1e-9 >= min(old["maker_ratio"], 0.85)
    assert new["dust_abs"] <= old["dust_abs"] + 1e-9
    assert new["dust_books"] <= old["dust_books"] + 1e-9
    assert new["dust_events"] <= old["dust_events"] + 1e-9
    assert new["latency_mean"] < old["latency_mean"]
    assert new["latency_p95"] < old["latency_p95"]
    assert new["forced_misses"] == 0.0
    assert delta["score_velocity"] > 0.0


if __name__ == "__main__":
    report = compare()
    print(json.dumps(report, indent=2, sort_keys=True))
