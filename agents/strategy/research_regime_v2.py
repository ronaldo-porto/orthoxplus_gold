# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 1: independent MarketRegime V2 and ScoreRegime.

Pure functions so unit tests do not import Strategy1 / bittensor.

MarketRegime is derived from cross-sectional book statistics.
ScoreRegime is derived from Kappa / coverage state only.
Neither state is allowed to copy a missing parent trigger into STRESSED.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

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


def parent_trigger_cannot_force_stressed(
    stats: Mapping[str, Any],
    parent_mode: str | None = "STRESSED",
    parent_trigger: str | None = None,
) -> str:
    """Helper for the UNEXPOSED_BY_PARENT regression test."""
    merged = dict(stats)
    merged["parent_mode"] = parent_mode
    merged["parent_trigger"] = parent_trigger
    merged["trigger"] = parent_trigger
    decision = classify_regime_v2(
        merged,
        thresholds=RegimeV2Thresholds(debounce_ticks=1),
    )
    return decision.market_regime
