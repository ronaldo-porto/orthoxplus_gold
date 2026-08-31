# SPDX-License-Identifier: MIT
"""Compact execution-quality model used only as a scheduler tie-breaker.

It measures how efficiently a book converts Maker quote attempts into useful
round trips.  It is deliberately *not* a score authority: TOTAL_SCORE_FRONTIER
owns score priority and hard economics/feasibility remain separate gates.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

EXECUTION_QUALITY_VERSION = "execution_quality_v4_15_1"
TIER_PRODUCTIVE = "PRODUCTIVE"
TIER_UNKNOWN = "UNKNOWN"
TIER_INEFFICIENT = "INEFFICIENT"


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


def _clip01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass(frozen=True)
class ExecutionQualitySnapshot:
    book_id: int
    maker_quotes: int
    maker_fills: int
    contract_rejects: int
    realized_pnl: float
    positive_count: int
    negative_count: int
    maker_fee_bps: float
    fill_rate_hint: float = 0.0
    raw_kappa: float | None = None
    ticks_since_last_rt: int | None = None
    fresh_round_trips: int = 0

    @property
    def placements_per_rt(self) -> float:
        fresh = max(0, int(self.fresh_round_trips or 0))
        if fresh <= 0:
            return float(max(0, self.maker_quotes))
        return float(max(0, self.maker_quotes)) / float(fresh)

    @property
    def maker_fill_conversion(self) -> float:
        if self.maker_quotes <= 0:
            return _clip01(self.fill_rate_hint)
        empirical = float(max(0, self.maker_fills)) / float(max(1, self.maker_quotes))
        n = min(12, max(0, self.maker_quotes))
        w = n / 12.0
        return _clip01(w * empirical + (1.0 - w) * _clip01(self.fill_rate_hint))

    @property
    def contract_reject_rate(self) -> float:
        return float(max(0, self.contract_rejects)) / float(max(1, self.maker_quotes))

    @property
    def loss_rate(self) -> float:
        n = max(0, self.positive_count) + max(0, self.negative_count)
        return 0.0 if n <= 0 else float(max(0, self.negative_count)) / float(n)

    @property
    def raw_kappa_norm(self) -> float:
        if self.raw_kappa is None:
            return 0.5
        value = _finite(self.raw_kappa, 0.0)
        return _clip01((max(-2.5, min(2.5, value)) + 2.5) / 5.0)

    @property
    def rebate_score(self) -> float:
        return _clip01(max(0.0, -_finite(self.maker_fee_bps)) / 15.0)

    @property
    def rt_efficiency(self) -> float:
        if self.fresh_round_trips <= 0:
            return 0.45 if self.maker_quotes < 12 else 0.20
        if self.maker_quotes <= 0:
            return 0.45
        return _clip01(12.0 / max(12.0, self.placements_per_rt))

    @property
    def pnl_quality(self) -> float:
        n = self.positive_count + self.negative_count
        if n <= 0:
            return 0.50
        positive_ratio = float(max(0, self.positive_count)) / float(max(1, n))
        sign = 1.0 if _finite(self.realized_pnl) >= 0.0 else 0.0
        return _clip01(0.55 * positive_ratio + 0.45 * sign)

    @property
    def freshness_score(self) -> float:
        if self.ticks_since_last_rt is None:
            return 0.50
        age = max(0, int(self.ticks_since_last_rt))
        return _clip01(1.0 - min(age, 300) / 300.0)

    @property
    def execution_tier(self) -> str:
        fresh = max(0, int(self.fresh_round_trips or 0))
        if self.maker_quotes >= 20 and (fresh <= 0 or self.maker_fill_conversion < 0.05):
            return TIER_INEFFICIENT
        if self.maker_quotes >= 12 and self.contract_reject_rate > 0.025:
            return TIER_INEFFICIENT
        if fresh > 0 and self.placements_per_rt > 45.0:
            return TIER_INEFFICIENT
        if self.maker_quotes < 6 or self.maker_fills < 2 or fresh <= 0:
            return TIER_UNKNOWN
        return TIER_PRODUCTIVE

    @property
    def score(self) -> float:
        reject_penalty = _clip01(self.contract_reject_rate / 0.02)
        loss_penalty = _clip01(self.loss_rate / 0.50)
        score = (
            0.30 * self.rt_efficiency
            + 0.20 * self.maker_fill_conversion
            + 0.18 * self.pnl_quality
            + 0.10 * self.rebate_score
            + 0.12 * self.raw_kappa_norm
            + 0.10 * self.freshness_score
            - 0.16 * reject_penalty
            - 0.18 * loss_penalty
        )
        return max(-1.0, min(1.0, score))

    def as_log(self) -> dict[str, Any]:
        return {
            "execution_quality_version": EXECUTION_QUALITY_VERSION,
            "execution_quality_score": float(self.score),
            "execution_quality_tier": self.execution_tier,
            "placements_per_rt": float(self.placements_per_rt),
            "maker_fill_conversion": float(self.maker_fill_conversion),
            "contract_reject_rate": float(self.contract_reject_rate),
            "loss_rate": float(self.loss_rate),
            "maker_rebate_score": float(self.rebate_score),
            "ticks_since_last_rt": self.ticks_since_last_rt,
        }


def sanitize_execution_quality_runtime(raw: Any) -> dict[int, dict[str, int]]:
    if not isinstance(raw, Mapping):
        return {}
    keys = (
        "maker_quotes", "maker_fills", "contract_rejects", "last_rt_tick",
        "fresh_round_trips", "fresh_positive_round_trips", "fresh_negative_round_trips",
    )
    out: dict[int, dict[str, int]] = {}
    for raw_book, raw_row in raw.items():
        try:
            bid = int(raw_book)
        except (TypeError, ValueError):
            continue
        if not isinstance(raw_row, Mapping):
            continue
        row: dict[str, int] = {}
        for key in keys:
            try:
                value = int(raw_row.get(key, 0) or 0)
            except (TypeError, ValueError):
                value = 0
            row[key] = max(0, value)
        out[bid] = row
    return out
