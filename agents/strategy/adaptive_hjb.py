# SPDX-License-Identifier: MIT
"""Analytical Adaptive HJB-style reservation controller.

SHADOW ONLY. This module never constructs orders. Reservation price:

    r = microprice
      + alpha
      - q * gamma * sigma^2 * horizon
      - latency_penalty
      - adverse_penalty

Inputs must be real BaseStrategy values: inventory q, volatility sigma,
alpha, fill hazard, markout/adverse risk, latency, and market regime.
No online PDE solve. No Strategy1 / Research / score_ev / Strategy5 imports.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Literal

from adaptive_ev import trading_ev

Phase = Literal["DISABLED", "OBSERVE", "BOOTSTRAP", "NORMAL", "DRIFT"]

VOL_RATIO_CAP = 8.0
HALF_SPREAD_MIN = 0.08
HALF_SPREAD_MAX = 2.75
PENALTY_SPREAD_CAP = 0.90
SIZE_INV_CUT = 0.45
SIZE_TOX_CUT = 0.25
DRIFT_SIZE_SCALE = 0.65


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


@dataclass(frozen=True)
class HjbConfig:
    gamma: float = 0.15
    gamma_min: float = 0.05
    gamma_max: float = 0.60
    kappa: float = 1.5
    horizon: float = 1.0
    alpha_shift: float = 0.28
    vol_floor: float = 5e-4
    latency_weight: float = 0.15
    adverse_weight: float = 0.20
    ofi_alpha_weight: float = 0.15
    inv_var_scale: float = 0.35
    inventory_gamma_weight: float = 0.40
    vol_gamma_weight: float = 0.35
    toxicity_gamma_weight: float = 0.50
    drawdown_gamma_weight: float = 0.40
    drift_gamma_mult: float = 1.35
    fees_bps: float = 0.5


@dataclass(frozen=True)
class HjbState:
    mid: float
    microprice: float
    spread: float
    alpha: float
    inventory: float
    sigma: float
    fill_hazard_buy: float
    fill_hazard_sell: float
    actionable_buy: float
    actionable_sell: float
    ofi: float
    markout_bps: float
    toxicity: float
    latency_ms: float
    drawdown: float
    phase: Phase
    base_size: float
    regime: str = ""


@dataclass(frozen=True)
class HjbQuote:
    reservation: float
    bid: float
    ask: float
    bid_distance: float
    ask_distance: float
    bid_size: float
    ask_size: float
    gamma: float
    sigma: float
    alpha: float
    inventory: float
    latency_penalty: float
    adverse_penalty: float
    inventory_term: float
    estimated_ev: float
    fill_hazard_buy: float
    fill_hazard_sell: float
    markout_bps: float
    regime: str = ""

    def as_log(self) -> dict[str, Any]:
        return {
            "hjb_reservation": self.reservation,
            "hjb_bid": self.bid,
            "hjb_ask": self.ask,
            "hjb_bid_distance": self.bid_distance,
            "hjb_ask_distance": self.ask_distance,
            "hjb_bid_size_proposal": self.bid_size,
            "hjb_ask_size_proposal": self.ask_size,
            "inventory": self.inventory,
            "gamma": self.gamma,
            "sigma": self.sigma,
            "alpha": self.alpha,
            "fill_hazard_buy": self.fill_hazard_buy,
            "fill_hazard_sell": self.fill_hazard_sell,
            "markout": self.markout_bps,
            "latency_penalty": self.latency_penalty,
            "adverse_penalty": self.adverse_penalty,
            "inventory_term": self.inventory_term,
            "estimated_hjb_ev": self.estimated_ev,
            "regime": self.regime,
        }


def adaptive_gamma(state: HjbState, cfg: HjbConfig | None = None) -> float:
    cfg = cfg or HjbConfig()
    vol_ratio = _clip(
        max(state.sigma, cfg.vol_floor) / max(cfg.vol_floor, 1e-9), 1.0, VOL_RATIO_CAP
    )
    gamma = float(cfg.gamma)
    gamma *= 1.0 + cfg.inventory_gamma_weight * abs(_clip(state.inventory, -1.0, 1.0))
    gamma *= 1.0 + cfg.vol_gamma_weight * max(0.0, vol_ratio - 1.0) / (VOL_RATIO_CAP - 1.0)
    gamma *= 1.0 + cfg.toxicity_gamma_weight * _clip(state.toxicity, 0.0, 1.0)
    gamma *= 1.0 + cfg.drawdown_gamma_weight * _clip(state.drawdown, 0.0, 1.0)
    if state.phase == "DRIFT":
        gamma *= float(cfg.drift_gamma_mult)
    elif state.phase == "OBSERVE":
        gamma *= 1.10
    return _clip(gamma, cfg.gamma_min, cfg.gamma_max)


def _intensity_kappa(state: HjbState, cfg: HjbConfig) -> float:
    fill = 0.5 * (
        _clip(state.fill_hazard_buy, 0.0, 1.0) + _clip(state.fill_hazard_sell, 0.0, 1.0)
    )
    return max(1e-6, float(cfg.kappa) * (0.35 + 0.65 * fill))


def _quote_ev(
    *,
    mid: float,
    bid: float,
    ask: float,
    p_buy: float,
    p_sell: float,
    markout_bps: float,
    fees_bps: float,
) -> float:
    if mid <= 0.0 or ask <= bid:
        return 0.0
    capture = 1.0e4 * (ask - bid) / mid / 2.0
    p = 0.5 * (_clip(p_buy, 0.0, 1.0) + _clip(p_sell, 0.0, 1.0))
    return trading_ev(p, capture, markout_bps, fees_bps)


def _distance_fill(p_touch: float, distance: float, spread: float) -> float:
    x = max(0.0, float(distance) / max(float(spread), 1e-9))
    return _clip(float(p_touch) * math.exp(-1.25 * x), 0.01, 0.95)


def reservation_price(
    *,
    microprice: float,
    alpha_term: float,
    inventory: float,
    gamma: float,
    sigma: float,
    horizon: float,
    mid: float,
    latency_penalty: float,
    adverse_penalty: float,
    vol_floor: float = 5e-4,
) -> tuple[float, float]:
    """Return (r, inventory_term) for the shadow reservation formula."""
    sigma_used = max(float(sigma), float(vol_floor), 0.0)
    inventory_term = (
        float(inventory) * float(gamma) * (sigma_used ** 2) * float(horizon) * max(float(mid), 0.0)
    )
    reservation = (
        float(microprice)
        + float(alpha_term)
        - inventory_term
        - float(latency_penalty)
        - float(adverse_penalty)
    )
    return reservation, inventory_term


def shadow_quote_ev(
    *,
    mid: float,
    spread: float,
    bid: float,
    ask: float,
    fill_buy: float,
    fill_sell: float,
    markout_bps: float,
    fees_bps: float = 0.5,
) -> float:
    p_buy = _distance_fill(fill_buy, mid - bid, spread)
    p_sell = _distance_fill(fill_sell, ask - mid, spread)
    return _quote_ev(
        mid=mid,
        bid=bid,
        ask=ask,
        p_buy=p_buy,
        p_sell=p_sell,
        markout_bps=markout_bps,
        fees_bps=fees_bps,
    )


@dataclass(frozen=True)
class HjbOverlayBounds:
    mix: float = 0.25
    max_center_frac: float = 0.05
    max_spread_widen: float = 0.06
    max_spread_tighten: float = 0.02
    max_side_delta: float = 0.10
    max_size_cut: float = 0.15
    max_exit_boost: float = 0.08
    defensive_inventory: float = 0.30


@dataclass(frozen=True)
class HjbOverlayDecision:
    applied: bool
    reason: str
    spread_scale: float = 1.0
    buy_bias_scale: float = 1.0
    sell_bias_scale: float = 1.0
    size_scale: float = 1.0
    exit_urgency_scale: float = 1.0
    center_shift_frac: float = 0.0
    edge_bias_delta: float = 0.0

    def as_log(self) -> dict[str, Any]:
        return {
            "hjb_overlay_applied": int(self.applied),
            "hjb_overlay_reason": self.reason,
            "hjb_spread_scale": self.spread_scale,
            "hjb_buy_bias_scale": self.buy_bias_scale,
            "hjb_sell_bias_scale": self.sell_bias_scale,
            "hjb_size_scale": self.size_scale,
            "hjb_exit_urgency_scale": self.exit_urgency_scale,
            "hjb_center_shift_frac": self.center_shift_frac,
            "hjb_edge_bias_delta": self.edge_bias_delta,
        }


def hold_hjb_overlay(reason: str = "HOLD") -> HjbOverlayDecision:
    return HjbOverlayDecision(applied=False, reason=reason)


def hjb_quote_valid(quote: HjbQuote | None, *, mid: float, spread: float) -> bool:
    if quote is None or mid <= 0.0 or spread <= 0.0:
        return False
    if not math.isfinite(quote.reservation) or not math.isfinite(quote.bid) or not math.isfinite(quote.ask):
        return False
    if quote.bid <= 0.0 or quote.ask <= quote.bid:
        return False
    if abs(quote.reservation - mid) > 4.0 * spread:
        return False
    return True


def shadow_results_allow_overlay(evidence: dict[str, Any]) -> bool:
    """Inventory physics must be valid. Full-price HJB EV is not required."""
    if int(evidence.get("n", 0) or 0) < 10:
        return False
    if float(evidence.get("mean_reservation_shift_long", 1.0) or 1.0) >= 0.0:
        return False
    if float(evidence.get("mean_reservation_shift_short", -1.0) or -1.0) <= 0.0:
        return False
    rows = evidence.get("rows") or []
    flat = next((r for r in rows if r.get("scenario") == "flat_calm"), None)
    if flat is None:
        return False
    try:
        flat_term = float(flat.get("inventory_term", 1.0))
    except (TypeError, ValueError):
        return False
    if abs(flat_term) > 1e-9:
        return False
    toxic_win = any(
        r.get("scenario") in {"toxic_markout", "regime_toxic"}
        and float(r.get("estimated_hjb_ev", 0.0)) > float(r.get("estimated_base_ev", 0.0))
        for r in rows
    )
    return bool(toxic_win)


def propose_hjb_overlay(
    quote: HjbQuote | None,
    *,
    base_bid: float,
    base_ask: float,
    mid: float,
    spread: float,
    base_ev: float,
    phase: Phase,
    bounds: HjbOverlayBounds | None = None,
    skew_strength: float = 0.20,
    base_size: float = 0.25,
) -> HjbOverlayDecision:
    """Bounded Base→HJB correction. Never returns raw HJB prices."""
    bounds = bounds or HjbOverlayBounds()
    if phase in {"DISABLED", "OBSERVE"}:
        return hold_hjb_overlay("HOLD_PHASE")
    if not hjb_quote_valid(quote, mid=mid, spread=spread):
        return hold_hjb_overlay("INVALID")
    assert quote is not None
    mix = _clip(bounds.mix, 0.0, 0.50)
    if mix <= 0.0:
        return hold_hjb_overlay("HOLD_MIX")
    ev_ok = float(quote.estimated_ev) + 1e-12 >= float(base_ev)
    defensive = (
        abs(float(quote.inventory)) >= float(bounds.defensive_inventory)
        or float(quote.adverse_penalty) > 1e-12
        or phase == "DRIFT"
    )
    if not ev_ok and not defensive:
        return hold_hjb_overlay("HJB_EV_NOT_POSITIVE")

    allow_tighten = ev_ok and phase != "DRIFT"
    base_res = 0.5 * (float(base_bid) + float(base_ask))
    delta_frac = (float(quote.reservation) - base_res) / max(float(spread), 1e-9)
    if not ev_ok:
        if quote.inventory > 1e-9:
            delta_frac = min(0.0, delta_frac)
        elif quote.inventory < -1e-9:
            delta_frac = max(0.0, delta_frac)
        else:
            delta_frac = 0.0
    delta_frac *= mix
    delta_frac = _clip(delta_frac, -bounds.max_center_frac, bounds.max_center_frac)
    edge = delta_frac / max(float(skew_strength), 0.05)

    hjb_w = (float(quote.ask) - float(quote.bid)) / max(float(spread), 1e-9)
    base_w = (float(base_ask) - float(base_bid)) / max(float(spread), 1e-9)
    width_delta = (hjb_w / max(base_w, 1e-9) - 1.0) * mix
    if not allow_tighten:
        width_delta = max(0.0, width_delta)
    width_delta = _clip(width_delta, -bounds.max_spread_tighten, bounds.max_spread_widen)
    spread_scale = 1.0 + width_delta

    buy_scale = 1.0
    sell_scale = 1.0
    inv = float(quote.inventory)
    side = mix * float(bounds.max_side_delta) * min(1.0, abs(inv))
    if inv > 0.05:
        buy_scale = 1.0 - side
        sell_scale = 1.0 + 0.50 * side
    elif inv < -0.05:
        sell_scale = 1.0 - side
        buy_scale = 1.0 + 0.50 * side

    hjb_size = min(float(quote.bid_size), float(quote.ask_size))
    size_cut = max(0.0, 1.0 - hjb_size / max(float(base_size), 1e-9))
    size_scale = 1.0 - _clip(mix * size_cut, 0.0, bounds.max_size_cut)

    exit_scale = 1.0
    if defensive:
        exit_scale = 1.0 + mix * float(bounds.max_exit_boost)

    reason = "HJB_EV" if ev_ok else "HJB_DEFENSIVE"
    return HjbOverlayDecision(
        applied=True,
        reason=reason,
        spread_scale=spread_scale,
        buy_bias_scale=_clip(buy_scale, 1.0 - bounds.max_side_delta, 1.0 + bounds.max_side_delta),
        sell_bias_scale=_clip(sell_scale, 1.0 - bounds.max_side_delta, 1.0 + bounds.max_side_delta),
        size_scale=min(1.0, size_scale),
        exit_urgency_scale=max(1.0, min(1.0 + bounds.max_exit_boost, exit_scale)),
        center_shift_frac=delta_frac,
        edge_bias_delta=_clip(edge, -0.35, 0.35),
    )


def compute_hjb_quote(state: HjbState, cfg: HjbConfig | None = None) -> HjbQuote | None:
    cfg = cfg or HjbConfig()
    mid = float(state.mid)
    spread = float(state.spread)
    if mid <= 0.0 or spread <= 0.0:
        return None
    micro = float(state.microprice) if state.microprice > 0.0 else mid
    inventory = _clip(state.inventory, -1.0, 1.0)
    alpha_eff = _clip(float(state.alpha), -1.0, 1.0)
    gamma = adaptive_gamma(state, cfg)
    vol_ratio = _clip(
        max(state.sigma, cfg.vol_floor) / max(cfg.vol_floor, 1e-9), 1.0, VOL_RATIO_CAP
    )
    sigma_term = vol_ratio * vol_ratio

    alpha_term = cfg.alpha_shift * alpha_eff * spread
    alpha_term = _clip(alpha_term, -PENALTY_SPREAD_CAP * spread, PENALTY_SPREAD_CAP * spread)

    toxicity = _clip(state.toxicity, 0.0, 1.0)
    adverse_from_markout = _clip(max(0.0, -float(state.markout_bps)) / 8.0, 0.0, 1.0)
    adverse = max(toxicity, adverse_from_markout)
    adverse_penalty = cfg.adverse_weight * adverse * spread
    latency_penalty = (
        cfg.latency_weight * math.tanh(max(0.0, float(state.latency_ms)) / 250.0) * spread
    )

    reservation, inventory_term = reservation_price(
        microprice=micro,
        alpha_term=alpha_term,
        inventory=inventory,
        gamma=gamma,
        sigma=float(state.sigma),
        horizon=float(cfg.horizon),
        mid=mid,
        latency_penalty=latency_penalty,
        adverse_penalty=adverse_penalty,
        vol_floor=float(cfg.vol_floor),
    )
    inventory_term = _clip(
        inventory_term, -PENALTY_SPREAD_CAP * spread, PENALTY_SPREAD_CAP * spread
    )
    reservation = micro + alpha_term - inventory_term - latency_penalty - adverse_penalty

    kappa = _intensity_kappa(state, cfg)
    as_term = math.log1p(gamma / kappa) / max(gamma, 1e-9)
    vol_half = 0.5 * gamma * sigma_term * cfg.horizon * cfg.inv_var_scale
    half = 0.50 + as_term + vol_half
    half += cfg.latency_weight * math.tanh(max(0.0, float(state.latency_ms)) / 250.0)
    half += cfg.adverse_weight * adverse
    half = _clip(half, HALF_SPREAD_MIN, HALF_SPREAD_MAX)
    half_bid = _clip(half * (1.0 + 0.35 * max(inventory, 0.0)), HALF_SPREAD_MIN, HALF_SPREAD_MAX)
    half_ask = _clip(half * (1.0 + 0.35 * max(-inventory, 0.0)), HALF_SPREAD_MIN, HALF_SPREAD_MAX)

    bid = reservation - half_bid * spread
    ask = reservation + half_ask * spread
    if bid <= 0.0 or ask <= bid:
        return None

    size = max(0.0, float(state.base_size))
    tox_scale = 1.0 - SIZE_TOX_CUT * toxicity
    bid_size = size * (1.0 - SIZE_INV_CUT * max(inventory, 0.0)) * tox_scale
    ask_size = size * (1.0 - SIZE_INV_CUT * max(-inventory, 0.0)) * tox_scale
    if state.phase == "DRIFT":
        bid_size *= DRIFT_SIZE_SCALE
        ask_size *= DRIFT_SIZE_SCALE
    bid_size = min(size, max(0.0, bid_size))
    ask_size = min(size, max(0.0, ask_size))

    p_buy = _distance_fill(state.actionable_buy or state.fill_hazard_buy, mid - bid, spread)
    p_sell = _distance_fill(state.actionable_sell or state.fill_hazard_sell, ask - mid, spread)
    est = _quote_ev(
        mid=mid,
        bid=bid,
        ask=ask,
        p_buy=p_buy,
        p_sell=p_sell,
        markout_bps=float(state.markout_bps) - 1.5 * adverse,
        fees_bps=cfg.fees_bps,
    )
    return HjbQuote(
        reservation=reservation,
        bid=bid,
        ask=ask,
        bid_distance=mid - bid,
        ask_distance=ask - mid,
        bid_size=bid_size,
        ask_size=ask_size,
        gamma=gamma,
        sigma=float(state.sigma),
        alpha=alpha_eff,
        inventory=inventory,
        latency_penalty=latency_penalty,
        adverse_penalty=adverse_penalty,
        inventory_term=inventory_term,
        estimated_ev=est,
        fill_hazard_buy=float(state.fill_hazard_buy),
        fill_hazard_sell=float(state.fill_hazard_sell),
        markout_bps=float(state.markout_bps),
        regime=str(state.regime or ""),
    )


def _base_state(**overrides: Any) -> HjbState:
    data = dict(
        mid=100.0,
        microprice=100.0,
        spread=0.20,
        alpha=0.0,
        inventory=0.0,
        sigma=5e-4,
        fill_hazard_buy=0.22,
        fill_hazard_sell=0.22,
        actionable_buy=0.18,
        actionable_sell=0.18,
        ofi=0.0,
        markout_bps=0.0,
        toxicity=0.0,
        latency_ms=80.0,
        drawdown=0.0,
        phase="NORMAL",
        base_size=0.25,
        regime="CHOP",
    )
    data.update(overrides)
    return HjbState(**data)


def run_shadow_campaign(cfg: HjbConfig | None = None) -> dict[str, Any]:
    """Offline shadow campaign. Never submits orders; returns comparison evidence."""
    cfg = cfg or HjbConfig()
    scenarios: list[tuple[str, HjbState]] = [
        ("flat_calm", _base_state()),
        ("long_inventory", _base_state(inventory=0.70)),
        ("short_inventory", _base_state(inventory=-0.70)),
        ("positive_alpha", _base_state(alpha=0.60, ofi=0.30, microprice=100.04)),
        ("toxic_markout", _base_state(markout_bps=-6.0, toxicity=0.80)),
        ("high_vol", _base_state(sigma=2.5e-3)),
        ("drawdown", _base_state(drawdown=0.80, inventory=0.40)),
        ("drift", _base_state(phase="DRIFT", inventory=0.30, markout_bps=-2.0)),
        ("observe", _base_state(phase="OBSERVE")),
        ("latency", _base_state(latency_ms=400.0, inventory=0.50)),
        (
            "low_fill",
            _base_state(
                fill_hazard_buy=0.06,
                fill_hazard_sell=0.06,
                actionable_buy=0.04,
                actionable_sell=0.04,
            ),
        ),
        ("regime_toxic", _base_state(regime="TOXIC", toxicity=1.0, markout_bps=-8.0)),
    ]
    rows: list[dict[str, Any]] = []
    for name, state in scenarios:
        quote = compute_hjb_quote(state, cfg)
        if quote is None:
            continue
        base_bid = state.mid - 0.25 * state.spread
        base_ask = state.mid + 0.25 * state.spread
        adaptive_bid = state.mid - 0.22 * state.spread
        adaptive_ask = state.mid + 0.28 * state.spread
        base_ev = shadow_quote_ev(
            mid=state.mid,
            spread=state.spread,
            bid=base_bid,
            ask=base_ask,
            fill_buy=state.fill_hazard_buy,
            fill_sell=state.fill_hazard_sell,
            markout_bps=state.markout_bps,
            fees_bps=cfg.fees_bps,
        )
        rows.append(
            {
                "scenario": name,
                "phase": state.phase,
                "inventory": state.inventory,
                "gamma": quote.gamma,
                "reservation": quote.reservation,
                "microprice": state.microprice,
                "hjb_bid": quote.bid,
                "hjb_ask": quote.ask,
                "hjb_bid_distance": quote.bid_distance,
                "hjb_ask_distance": quote.ask_distance,
                "hjb_bid_size": quote.bid_size,
                "hjb_ask_size": quote.ask_size,
                "base_bid": base_bid,
                "base_ask": base_ask,
                "adaptive_bid": adaptive_bid,
                "adaptive_ask": adaptive_ask,
                "estimated_base_ev": base_ev,
                "estimated_hjb_ev": quote.estimated_ev,
                "latency_penalty": quote.latency_penalty,
                "adverse_penalty": quote.adverse_penalty,
                "inventory_term": quote.inventory_term,
                "policy_activated": 0,
            }
        )

    gammas_normal = [r["gamma"] for r in rows if r["phase"] == "NORMAL"]
    gammas_drift = [r["gamma"] for r in rows if r["phase"] == "DRIFT"]
    long_rows = [r for r in rows if r["inventory"] > 0.2]
    short_rows = [r for r in rows if r["inventory"] < -0.2]
    hjb_ev_wins = sum(1 for r in rows if r["estimated_hjb_ev"] > r["estimated_base_ev"])
    return {
        "n": len(rows),
        "policy_activated": 0,
        "mean_gamma_normal": sum(gammas_normal) / max(len(gammas_normal), 1),
        "mean_gamma_drift": sum(gammas_drift) / max(len(gammas_drift), 1),
        "mean_inventory_term_long": (
            sum(r["inventory_term"] for r in long_rows) / max(len(long_rows), 1)
        ),
        "mean_inventory_term_short": (
            sum(r["inventory_term"] for r in short_rows) / max(len(short_rows), 1)
        ),
        "mean_reservation_shift_long": (
            sum(-r["inventory_term"] for r in long_rows) / max(len(long_rows), 1)
        ),
        "mean_reservation_shift_short": (
            sum(-r["inventory_term"] for r in short_rows) / max(len(short_rows), 1)
        ),
        "hjb_ev_better_count": hjb_ev_wins,
        "hjb_ev_better_frac": hjb_ev_wins / max(len(rows), 1),
        "max_bid_size": max((r["hjb_bid_size"] for r in rows), default=0.0),
        "max_ask_size": max((r["hjb_ask_size"] for r in rows), default=0.0),
        "base_size": 0.25,
        "rows": rows,
    }
