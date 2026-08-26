# SPDX-License-Identifier: MIT
"""Bounded fast-vs-slow Adaptive drift detector.

BaseStrategy remains the execution engine. This module only decides when
learned Adaptive parameters should be distrusted.

Each request updates decayed fast/slow EWMAs. A finite request window then
scores deterioration on:

    fill hazard, actionable fill, markout, realized pnl,
    dust rate, spread, volatility, inventory age

DRIFT requires persistent evidence: multiple deteriorating windows plus a
minimum sample count. A single bad observation is not enough.

Recovery is DRIFT -> BOOTSTRAP -> NORMAL, never immediate DRIFT -> NORMAL.
No Strategy1 / Research / score_ev imports.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Literal

Phase = Literal["DISABLED", "OBSERVE", "BOOTSTRAP", "NORMAL", "DRIFT"]


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass
class DualEwma:
    fast: float = 0.0
    slow: float = 0.0
    n: int = 0
    initialized: bool = False

    def update(self, value: float, fast_alpha: float, slow_alpha: float) -> None:
        x = float(value)
        if not self.initialized:
            self.fast = x
            self.slow = x
            self.initialized = True
            self.n = 1
            return
        fa = _clip(fast_alpha, 0.01, 0.80)
        sa = _clip(slow_alpha, 0.005, min(fa, 0.50))
        self.fast = (1.0 - fa) * self.fast + fa * x
        self.slow = (1.0 - sa) * self.slow + sa * x
        self.n += 1

    def as_log(self) -> dict[str, Any]:
        return {
            "fast": self.fast if self.initialized else None,
            "slow": self.slow if self.initialized else None,
            "n": self.n,
        }


@dataclass(frozen=True)
class DriftConfig:
    fast_alpha: float = 0.15
    slow_alpha: float = 0.03
    window_requests: int = 100
    min_windows: int = 2
    min_samples: int = 40
    min_window_samples: int = 20
    min_signals: int = 1
    fill_abs: float = 0.02
    fill_rel: float = 0.25
    markout_delta_bps: float = 2.0
    spread_ratio: float = 1.25
    spread_delta_bps: float = 4.0
    pnl_hard_floor: float = -0.02
    pnl_ratio: float = 0.35
    pnl_baseline_min: float = 0.03
    dust_abs: float = 0.05
    vol_abs: float = 0.0008
    vol_rel: float = 0.25
    inventory_age_abs: float = 3.0
    inventory_age_rel: float = 0.25
    hold_requests: int = 500
    recovery_requests: int = 500


@dataclass(frozen=True)
class DriftObservation:
    fill_hazard: float | None = None
    actionable_fill: float | None = None
    markout_bps: float | None = None
    spread_bps: float | None = None
    maker_pnl: float | None = None
    realized_pnl: float | None = None
    dust_rate: float | None = None
    volatility: float | None = None
    inventory_age: float | None = None


@dataclass(frozen=True)
class ChannelSignal:
    name: str
    fast: float | None
    slow: float | None
    delta: float
    relative: float
    deteriorated: bool

    def as_log(self) -> dict[str, Any]:
        return {
            "fast": self.fast,
            "slow": self.slow,
            "delta": self.delta,
            "relative": self.relative,
            "deteriorated": int(self.deteriorated),
        }


@dataclass(frozen=True)
class WindowVerdict:
    deteriorated: bool
    trigger_drift: bool
    consecutive_deteriorating: int
    hit_count: int
    window_samples: int
    total_samples: int
    reason: str
    signals: dict[str, ChannelSignal] = field(default_factory=dict)

    def as_log(self) -> dict[str, Any]:
        return {
            "deteriorated": int(self.deteriorated),
            "trigger_drift": int(self.trigger_drift),
            "consecutive_deteriorating": self.consecutive_deteriorating,
            "hit_count": self.hit_count,
            "window_samples": self.window_samples,
            "total_samples": self.total_samples,
            "reason": self.reason,
            "signals": {name: sig.as_log() for name, sig in self.signals.items()},
        }


@dataclass
class PhaseClocks:
    observe_requests: int
    normal_after_requests: int
    drift_until_request: int = 0
    recovery_until_request: int = 0
    total_requests: int = 0


def current_phase(clocks: PhaseClocks, *, enabled: bool = True) -> Phase:
    if not enabled:
        return "DISABLED"
    req = int(clocks.total_requests)
    if req < int(clocks.observe_requests):
        return "OBSERVE"
    if req < int(clocks.drift_until_request):
        return "DRIFT"
    bootstrap_until = max(
        int(clocks.normal_after_requests),
        int(clocks.recovery_until_request),
    )
    if req < bootstrap_until:
        return "BOOTSTRAP"
    return "NORMAL"


def phase_transition_reason(old: str, new: str) -> str:
    if old == new:
        return "HOLD"
    key = f"{old}->{new}"
    return {
        "OBSERVE->BOOTSTRAP": "OBSERVE_TO_BOOTSTRAP",
        "OBSERVE->DRIFT": "DRIFT_ENTER",
        "BOOTSTRAP->NORMAL": "BOOTSTRAP_TO_NORMAL",
        "BOOTSTRAP->DRIFT": "DRIFT_ENTER",
        "NORMAL->DRIFT": "DRIFT_ENTER",
        "DRIFT->BOOTSTRAP": "DRIFT_RECOVER_BOOTSTRAP",
        "DRIFT->NORMAL": "DRIFT_SKIPPED_BOOTSTRAP",
        "DRIFT->OBSERVE": "SESSION_RESET",
        "BOOTSTRAP->OBSERVE": "SESSION_RESET",
        "NORMAL->OBSERVE": "SESSION_RESET",
        "NORMAL->BOOTSTRAP": "DRIFT_RECOVER_BOOTSTRAP",
    }.get(key, f"{old}_TO_{new}")


def enter_or_extend_drift(
    clocks: PhaseClocks,
    *,
    hold_requests: int,
    recovery_requests: int,
) -> PhaseClocks:
    hold = max(1, int(hold_requests))
    recovery = max(1, int(recovery_requests))
    drift_until = max(
        int(clocks.drift_until_request),
        int(clocks.total_requests) + hold,
    )
    return replace(
        clocks,
        drift_until_request=drift_until,
        recovery_until_request=drift_until + recovery,
    )


def _lower_is_worse(
    series: DualEwma,
    *,
    abs_min: float,
    rel_min: float,
    min_n: int,
    name: str,
) -> ChannelSignal:
    if not series.initialized or series.n < min_n:
        return ChannelSignal(name, None, None, 0.0, 0.0, False)
    delta = float(series.slow) - float(series.fast)
    relative = delta / max(abs(float(series.slow)), abs_min, 1e-9)
    hit = delta >= abs_min and relative >= rel_min
    return ChannelSignal(name, series.fast, series.slow, delta, relative, hit)


def _higher_is_worse(
    series: DualEwma,
    *,
    abs_min: float,
    min_n: int,
    name: str,
    rel_min: float = 0.0,
) -> ChannelSignal:
    if not series.initialized or series.n < min_n:
        return ChannelSignal(name, None, None, 0.0, 0.0, False)
    delta = float(series.fast) - float(series.slow)
    relative = delta / max(abs(float(series.slow)), abs_min, 1e-9)
    hit = delta >= abs_min and relative >= max(0.0, float(rel_min))
    return ChannelSignal(name, series.fast, series.slow, delta, relative, hit)


def _spread_signal(series: DualEwma, cfg: DriftConfig) -> ChannelSignal:
    if not series.initialized or series.n < cfg.min_samples:
        return ChannelSignal("spread", None, None, 0.0, 0.0, False)
    slow = max(float(series.slow), 1e-9)
    fast = float(series.fast)
    expand_ratio = fast / slow
    expand_delta = fast - float(series.slow)
    collapse_ratio = slow / max(fast, 1e-9)
    collapse_delta = float(series.slow) - fast
    expand = (
        expand_ratio >= cfg.spread_ratio
        and expand_delta >= cfg.spread_delta_bps
    )
    collapse = (
        collapse_ratio >= cfg.spread_ratio
        and collapse_delta >= cfg.spread_delta_bps
    )
    if expand:
        return ChannelSignal("spread", fast, series.slow, expand_delta, expand_ratio, True)
    if collapse:
        return ChannelSignal(
            "spread", fast, series.slow, -collapse_delta, collapse_ratio, True
        )
    return ChannelSignal("spread", fast, series.slow, expand_delta, expand_ratio, False)


def _pnl_signal(series: DualEwma, cfg: DriftConfig) -> ChannelSignal:
    if not series.initialized or series.n < cfg.min_samples:
        return ChannelSignal("maker_pnl", None, None, 0.0, 0.0, False)
    fast = float(series.fast)
    slow = float(series.slow)
    delta = slow - fast
    relative = fast / slow if slow > 1e-9 else 0.0
    hard = fast <= cfg.pnl_hard_floor
    relative_hit = slow >= cfg.pnl_baseline_min and fast <= slow * cfg.pnl_ratio
    return ChannelSignal("maker_pnl", fast, slow, delta, relative, bool(hard or relative_hit))


class DriftTracker:
    def __init__(self, cfg: DriftConfig | None = None) -> None:
        self.cfg = cfg or DriftConfig()
        self.fill_hazard = DualEwma()
        self.actionable_fill = DualEwma()
        self.markout = DualEwma()
        self.spread = DualEwma()
        self.maker_pnl = DualEwma()
        self.dust_rate = DualEwma()
        self.volatility = DualEwma()
        self.inventory_age = DualEwma()
        self.consecutive_deteriorating = 0
        self.window_samples = 0
        self.total_samples = 0
        self.window_start_request = 0

    def reset(self, *, request: int = 0) -> None:
        self.fill_hazard = DualEwma()
        self.actionable_fill = DualEwma()
        self.markout = DualEwma()
        self.spread = DualEwma()
        self.maker_pnl = DualEwma()
        self.dust_rate = DualEwma()
        self.volatility = DualEwma()
        self.inventory_age = DualEwma()
        self.consecutive_deteriorating = 0
        self.window_samples = 0
        self.total_samples = 0
        self.window_start_request = int(request)

    def observe(self, obs: DriftObservation) -> None:
        cfg = self.cfg
        updated = False
        if obs.fill_hazard is not None:
            self.fill_hazard.update(obs.fill_hazard, cfg.fast_alpha, cfg.slow_alpha)
            updated = True
        if obs.actionable_fill is not None:
            self.actionable_fill.update(
                obs.actionable_fill, cfg.fast_alpha, cfg.slow_alpha
            )
            updated = True
        if obs.markout_bps is not None:
            self.markout.update(obs.markout_bps, cfg.fast_alpha, cfg.slow_alpha)
            updated = True
        if obs.spread_bps is not None:
            self.spread.update(obs.spread_bps, cfg.fast_alpha, cfg.slow_alpha)
            updated = True
        pnl = obs.maker_pnl if obs.maker_pnl is not None else obs.realized_pnl
        if pnl is not None:
            self.maker_pnl.update(pnl, cfg.fast_alpha, cfg.slow_alpha)
            updated = True
        if obs.dust_rate is not None:
            self.dust_rate.update(obs.dust_rate, cfg.fast_alpha, cfg.slow_alpha)
            updated = True
        if obs.volatility is not None:
            self.volatility.update(obs.volatility, cfg.fast_alpha, cfg.slow_alpha)
            updated = True
        if obs.inventory_age is not None:
            self.inventory_age.update(obs.inventory_age, cfg.fast_alpha, cfg.slow_alpha)
            updated = True
        if updated:
            self.window_samples += 1
            self.total_samples += 1

    def _evaluate_channels(self) -> dict[str, ChannelSignal]:
        cfg = self.cfg
        return {
            "fill_hazard": _lower_is_worse(
                self.fill_hazard,
                abs_min=cfg.fill_abs,
                rel_min=cfg.fill_rel,
                min_n=cfg.min_samples,
                name="fill_hazard",
            ),
            "actionable_fill": _lower_is_worse(
                self.actionable_fill,
                abs_min=cfg.fill_abs,
                rel_min=cfg.fill_rel,
                min_n=cfg.min_samples,
                name="actionable_fill",
            ),
            "markout": _lower_is_worse(
                self.markout,
                abs_min=cfg.markout_delta_bps,
                rel_min=0.0,
                min_n=cfg.min_samples,
                name="markout",
            ),
            "spread": _spread_signal(self.spread, cfg),
            "maker_pnl": _pnl_signal(self.maker_pnl, cfg),
            "dust_rate": _higher_is_worse(
                self.dust_rate,
                abs_min=cfg.dust_abs,
                min_n=cfg.min_samples,
                name="dust_rate",
            ),
            "volatility": _higher_is_worse(
                self.volatility,
                abs_min=cfg.vol_abs,
                rel_min=cfg.vol_rel,
                min_n=cfg.min_samples,
                name="volatility",
            ),
            "inventory_age": _higher_is_worse(
                self.inventory_age,
                abs_min=cfg.inventory_age_abs,
                rel_min=cfg.inventory_age_rel,
                min_n=cfg.min_samples,
                name="inventory_age",
            ),
        }

    def maybe_close_window(self, request: int) -> WindowVerdict | None:
        if int(request) - int(self.window_start_request) < int(self.cfg.window_requests):
            return None
        signals = self._evaluate_channels()
        hits = [name for name, sig in signals.items() if sig.deteriorated]
        enough_window = self.window_samples >= int(self.cfg.min_window_samples)
        enough_total = self.total_samples >= int(self.cfg.min_samples)
        deteriorated = bool(
            enough_window
            and enough_total
            and len(hits) >= int(self.cfg.min_signals)
        )
        if deteriorated:
            self.consecutive_deteriorating += 1
        else:
            self.consecutive_deteriorating = 0
        trigger = bool(
            deteriorated
            and self.consecutive_deteriorating >= int(self.cfg.min_windows)
            and enough_total
        )
        if trigger:
            reason = "DRIFT_PERSISTENT:" + ",".join(hits)
        elif deteriorated:
            reason = "WINDOW_DETERIORATING:" + ",".join(hits)
        elif not enough_window:
            reason = "WINDOW_INSUFFICIENT_SAMPLES"
        else:
            reason = "WINDOW_STABLE"
        verdict = WindowVerdict(
            deteriorated=deteriorated,
            trigger_drift=trigger,
            consecutive_deteriorating=self.consecutive_deteriorating,
            hit_count=len(hits),
            window_samples=self.window_samples,
            total_samples=self.total_samples,
            reason=reason,
            signals=signals,
        )
        self.window_start_request = int(request)
        self.window_samples = 0
        return verdict

    def as_log(self) -> dict[str, Any]:
        return {
            "fill_hazard": self.fill_hazard.as_log(),
            "actionable_fill": self.actionable_fill.as_log(),
            "markout": self.markout.as_log(),
            "spread": self.spread.as_log(),
            "maker_pnl": self.maker_pnl.as_log(),
            "dust_rate": self.dust_rate.as_log(),
            "volatility": self.volatility.as_log(),
            "inventory_age": self.inventory_age.as_log(),
            "consecutive_deteriorating": self.consecutive_deteriorating,
            "window_samples": self.window_samples,
            "total_samples": self.total_samples,
        }
