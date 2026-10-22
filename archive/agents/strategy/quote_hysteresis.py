# SPDX-License-Identifier: MIT
"""Production quote hysteresis, bounded adaptive TTL, and dust-prevention helpers.

Promoted from verified Research V4.3 Phase 1.8. Dust escape is intentionally
NOT wired into BaseStrategy: Phase 1 did not prove it safe and economically
useful.

Hold through tiny theoretical updates. Replace only on a listed material
change. OFI reversal uses Cont–Kukanov–Stoikov flow, never static imbalance.
Hard safety cancellation is immediate. TTL is always clamped to [min, max].
Frozen defaults: min_price_ticks=2.0, ev_improve_threshold=0.06.
No Strategy1 / Research runtime imports.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    """Static imbalance helper. Not an OFI signal."""
    if old_imb is None or new_imb is None:
        return False
    old = float(old_imb)
    new = float(new_imb)
    if abs(old) < min_abs or abs(new) < min_abs:
        return False
    return _sign(old) != 0 and _sign(new) != 0 and _sign(old) != _sign(new)


def ofi_reversed(old_ofi: float | None, new_ofi: float | None, *, min_abs: float = 0.10) -> bool:
    """True only when supported OFI flips sign with material magnitude."""
    if old_ofi is None or new_ofi is None:
        return False
    old = float(old_ofi)
    new = float(new_ofi)
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
    chosen_ttl: float | None = None

    @property
    def cancel_reason(self) -> str:
        return self.reason

    @property
    def quote_age(self) -> float | None:
        return self.order_age_ms

    def as_log(self, *, book: int, side: str) -> dict[str, Any]:
        return {
            "book": int(book),
            "side": str(side),
            "cancel": int(bool(self.cancel)),
            "reason": self.reason,
            "cancel_reason": self.reason,
            "old_price": self.old_price,
            "new_price": self.new_price,
            "price_delta_ticks": self.price_delta_ticks,
            "old_ev": self.old_ev,
            "new_ev": self.new_ev,
            "ev_delta": self.ev_delta,
            "order_age_ms": self.order_age_ms,
            "quote_age": self.order_age_ms,
            "chosen_ttl": self.chosen_ttl,
        }


def should_replace_quote(
    *,
    old_price: float | None,
    new_price: float | None,
    tick_size: float,
    min_price_ticks: float = 2.0,
    old_alpha: float | None = None,
    new_alpha: float | None = None,
    old_imbalance: float | None = None,
    new_imbalance: float | None = None,
    old_ofi: float | None = None,
    new_ofi: float | None = None,
    old_regime: str | None = None,
    new_regime: str | None = None,
    old_inventory_util: float | None = None,
    new_inventory_util: float | None = None,
    inventory_util_delta: float = 0.15,
    old_inventory_state: str | None = None,
    new_inventory_state: str | None = None,
    old_toxic: bool = False,
    new_toxic: bool = False,
    order_age_ms: float | None = None,
    ttl_ms: float | None = None,
    ttl_replace_frac: float = 0.85,
    old_ev: float | None = None,
    new_ev: float | None = None,
    ev_improve_threshold: float = 0.06,
    chosen_ttl: float | None = None,
    hard_safety: bool = False,
) -> CancelDecision:
    """HOLD unless a listed replacement rule fires. Hard safety is immediate.

    Static imbalance is accepted for compatibility and never treated as OFI.
    """
    del old_imbalance, new_imbalance
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
            chosen_ttl=None if chosen_ttl is None else float(chosen_ttl),
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
    if ofi_reversed(old_ofi, new_ofi):
        return _dec(True, "OFI")
    old_r = str(old_regime or "").upper()
    new_r = str(new_regime or "").upper()
    if old_r and new_r and old_r != new_r:
        return _dec(True, "REGIME")
    old_state = str(old_inventory_state or "").upper()
    new_state = str(new_inventory_state or "").upper()
    if old_state and new_state and old_state != new_state:
        return _dec(True, "INVENTORY")
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
    ofi: float | None = None,
    microprice_velocity: float | None = None,
    toxicity: bool = False,
    market_regime: str | None = None,
    queue_ahead: float | None = None,
    vol_high: float = 0.006,
    hazard_high: float = 0.35,
    imb_adverse: float = 0.35,
    ofi_adverse: float = 0.25,
    stale_velocity_ticks: float | None = None,
) -> tuple[float | None, str, dict[str, Any]]:
    """Bounded TTL. Returns (None, STALE, ...) to skip submit.

    Imbalance is logged only. Shortening uses volatility, toxicity, or real OFI.
    """
    del imb_adverse
    regime = str(market_regime or "NORMAL").upper()
    vol = 0.0 if volatility is None else float(volatility)
    haz = 0.0 if fill_hazard is None else max(0.0, min(1.0, float(fill_hazard)))
    imb = 0.0 if imbalance is None else float(imbalance)
    flow = None if ofi is None else float(ofi)
    vel = 0.0 if microprice_velocity is None else abs(float(microprice_velocity))
    info = {
        "fill_hazard": haz,
        "toxicity": int(bool(toxicity)),
        "volatility": vol,
        "imbalance": imb,
        "ofi": flow,
        "microprice_velocity": vel,
        "queue_ahead": queue_ahead,
        "market_regime": regime,
        "chosen_ttl": None,
    }
    if stale_velocity_ticks is not None and vel >= float(stale_velocity_ticks):
        return None, "STALE", info
    if toxicity or regime in {"TOXIC", "STRESSED"}:
        ttl = clamp_ttl_ms(float(baseline_ms) * 0.50, min_ms, max_ms)
        info["chosen_ttl"] = ttl
        return ttl, "TOXIC_SHORT", info
    if vol >= float(vol_high) or (flow is not None and abs(flow) >= float(ofi_adverse)):
        ttl = clamp_ttl_ms(float(baseline_ms) * 0.70, min_ms, max_ms)
        info["chosen_ttl"] = ttl
        return ttl, "ADVERSE_SHORT", info
    if (
        haz >= float(hazard_high)
        and vol < 0.5 * float(vol_high)
        and (flow is None or abs(flow) < 0.5 * float(ofi_adverse))
    ):
        stretch = 1.35
        if queue_ahead is not None and float(queue_ahead) > 0.0:
            stretch = 1.20
        ttl = clamp_ttl_ms(float(baseline_ms) * stretch, min_ms, max_ms)
        info["chosen_ttl"] = ttl
        return ttl, "STABLE_LONG", info
    ttl = clamp_ttl_ms(float(baseline_ms), min_ms, max_ms)
    info["chosen_ttl"] = ttl
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


def projected_inventory_after(inventory_before: float, reduce_qty: float) -> float:
    """Always flatten: subtract sign(before) * qty. Never flips the reduce direction."""
    before = float(inventory_before)
    qty = max(0.0, float(reduce_qty))
    if abs(before) <= 1e-18:
        return 0.0
    return before - (1.0 if before > 0.0 else -1.0) * qty


def dust_escape_allowed(
    *,
    inventory_before: float,
    reduce_qty: float,
    age_ticks: int,
    min_age_ticks: int,
    benefit_bps: float,
    cost_bps: float,
    eps: float = 1e-12,
) -> tuple[bool, float, str]:
    """Experimental old-dust reducer. Must strictly cut absolute exposure."""
    before = float(inventory_before)
    after = projected_inventory_after(before, reduce_qty)
    if abs(before) <= max(float(eps), 1e-18):
        return False, after, "FLAT"
    if int(age_ticks) < max(1, int(min_age_ticks)):
        return False, after, "TOO_YOUNG"
    if abs(after) + max(float(eps), 1e-18) >= abs(before):
        return False, after, "EXPOSURE_INCREASE"
    if float(benefit_bps) <= float(cost_bps):
        return False, after, "UNECONOMIC"
    return True, after, "ESCAPE"
