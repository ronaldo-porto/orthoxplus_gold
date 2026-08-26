# SPDX-License-Identifier: MIT
"""Research V4.3 Phase 1: independent MarketRegime V2 and ScoreRegime.

Pure functions so unit tests do not import Strategy1 / bittensor.

MarketRegime is derived from cross-sectional book statistics.
ScoreRegime is derived from Kappa / coverage state only.
Neither state is allowed to copy a missing parent trigger into STRESSED.
"""
from __future__ import annotations

import math
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
    "COVERAGE",
    "COMPLETION",
    "BALANCED",
)
# Legacy names kept only so persisted / parent strings can be remapped.
_SCORE_REGIME_ALIASES = {
    "NORMAL": "BALANCED",
    "COVERAGE_PRESSURE": "COVERAGE",
    "COMPLETION_PRESSURE": "COMPLETION",
}

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
    score_coverage_enter: float = 0.40
    score_coverage_exit: float = 0.25
    score_eligible_enter: float = 0.22
    score_eligible_exit: float = 0.14
    score_near_qual_enter: float = 0.18
    score_velocity_balanced: float = 0.05


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


def _as_count(value: Any, default: int | None = 0) -> int | None:
    if value is None:
        return default
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


def normalize_score_regime(label: str | None) -> str:
    token = str(label or "COVERAGE").upper()
    token = _SCORE_REGIME_ALIASES.get(token, token)
    if token not in SCORE_REGIMES:
        return "COVERAGE"
    return token


def score_regime_metrics(stats: Mapping[str, Any]) -> dict[str, Any]:
    """Scoring-state ratios only. Market vol/spread/inactive are ignored."""
    n = _as_count(stats.get("book_count"), 0) or 0
    raw_0 = stats.get("books_0_obs")
    raw_1 = stats.get("books_1_remaining")
    raw_2 = stats.get("books_2_remaining")
    raw_e = stats.get("books_eligible")
    scoring_present = any(value is not None for value in (raw_0, raw_1, raw_2, raw_e))
    if scoring_present:
        books_0 = _as_count(raw_0, 0) or 0
        books_1 = _as_count(raw_1, 0) or 0
        books_2 = _as_count(raw_2, 0) or 0
        books_eligible = _as_count(raw_e, 0) or 0
        if n <= 0:
            n = books_0 + books_1 + books_2 + books_eligible
    else:
        books_0 = n
        books_1 = 0
        books_2 = 0
        books_eligible = 0
    denom = max(n, 1)
    coverage_ratio = books_0 / denom
    eligible_ratio = books_eligible / denom
    deficit = stats.get("activity_deficit")
    if deficit is None:
        activity = coverage_ratio
    else:
        try:
            activity = float(deficit)
        except (TypeError, ValueError):
            activity = coverage_ratio
        if not math.isfinite(activity):
            activity = coverage_ratio
        elif activity > 1.0 and n > 0:
            activity = activity / float(n)
        activity = min(1.0, max(0.0, activity))
    try:
        rt_velocity = float(stats.get("round_trip_velocity") or 0.0)
    except (TypeError, ValueError):
        rt_velocity = 0.0
    if not math.isfinite(rt_velocity) or rt_velocity < 0.0:
        rt_velocity = 0.0
    return {
        "book_count": n,
        "books_0_obs": books_0,
        "books_1_remaining": books_1,
        "books_2_remaining": books_2,
        "books_eligible": books_eligible,
        "coverage_ratio": coverage_ratio,
        "eligible_ratio": eligible_ratio,
        "one_away": books_1,
        "two_away": books_2,
        "near_ratio": (books_1 + books_2) / denom,
        "activity_deficit": activity,
        "round_trip_velocity": rt_velocity,
    }


def propose_score_regime(
    stats: Mapping[str, Any],
    current: str,
    thresholds: RegimeV2Thresholds,
) -> tuple[str, str, str]:
    """Kappa scoring state only. Independent of MarketRegime and market vol/spread."""
    metrics = score_regime_metrics(stats)
    n = int(metrics["book_count"])
    if n <= 0:
        return "BALANCED", "EMPTY_SCORE_UNIVERSE", "book_count=0"

    current = normalize_score_regime(current)
    coverage_signal = max(float(metrics["coverage_ratio"]), float(metrics["activity_deficit"]))
    eligible_ratio = float(metrics["eligible_ratio"])
    near_ratio = float(metrics["near_ratio"])
    rt_velocity = float(metrics["round_trip_velocity"])
    cov_enter = float(thresholds.score_coverage_enter)
    cov_exit = float(thresholds.score_coverage_exit)
    elig_enter = float(thresholds.score_eligible_enter)
    elig_exit = float(thresholds.score_eligible_exit)
    vel_enter = float(thresholds.score_velocity_balanced)

    if current == "COVERAGE":
        if coverage_signal + 1e-12 >= cov_exit:
            return (
                "COVERAGE",
                "ZERO_OBS_HYSTERESIS",
                f"coverage_signal>={cov_exit:g}",
            )
    elif coverage_signal + 1e-12 >= cov_enter:
        return (
            "COVERAGE",
            "ZERO_OBS_COVERAGE",
            f"coverage_signal>={cov_enter:g}",
        )

    balanced_ready = eligible_ratio + 1e-12 >= elig_enter or (
        eligible_ratio + 1e-12 >= elig_exit and rt_velocity + 1e-12 >= vel_enter
    )
    if current == "BALANCED":
        if coverage_signal + 1e-12 < cov_enter and eligible_ratio + 1e-12 >= elig_exit:
            return (
                "BALANCED",
                "QUALIFIED_HYSTERESIS",
                f"eligible_ratio>={elig_exit:g}",
            )
    elif coverage_signal + 1e-12 < cov_exit and balanced_ready:
        return (
            "BALANCED",
            "QUALIFIED_COVERAGE",
            f"eligible_ratio>={elig_enter:g}",
        )

    if current == "COMPLETION":
        return (
            "COMPLETION",
            "NEAR_QUAL_HYSTERESIS",
            f"near_ratio={near_ratio:g}",
        )
    return (
        "COMPLETION",
        "NEAR_QUALIFICATION",
        f"near_ratio>={float(thresholds.score_near_qual_enter):g}",
    )


def classify_regime_v2(
    stats: Mapping[str, Any],
    *,
    market_state: DebounceState | None = None,
    score_state: DebounceState | None = None,
    thresholds: RegimeV2Thresholds | None = None,
) -> RegimeV2Decision:
    thr = thresholds or RegimeV2Thresholds()
    market_state = market_state or DebounceState("NORMAL", "NORMAL", 0)
    score_state = score_state or DebounceState("COVERAGE", "COVERAGE", 0)

    market_raw, market_trigger, market_threshold = propose_market_regime(
        stats, market_state.current, thr,
    )
    score_raw, score_trigger, score_threshold = propose_score_regime(
        stats, score_state.current, thr,
    )
    market_next = apply_debounce(market_state, market_raw, thr.debounce_ticks)
    score_next = apply_debounce(score_state, score_raw, thr.debounce_ticks)

    overlay = (
        "SCORING_PRESSURE" if score_next.current == "COVERAGE" else None
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
