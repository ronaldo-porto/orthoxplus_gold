# SPDX-License-Identifier: MIT
"""Analytical Adaptive HJB-style reservation controller.

SHADOW ONLY. This module never constructs orders. It scores a reservation
price from BaseStrategy state using an Avellaneda–Stoikov / GLFT approximation:

    reservation = microprice + alpha_adjustment - inventory_penalty
    inventory_penalty ~ q * gamma * sigma^2 * horizon

plus bounded latency and adverse-selection penalties. No online PDE solve.
No Strategy1 / Research / score_ev / Strategy5 imports.
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
    estimated_ev: float
    fill_hazard_buy: float
    fill_hazard_sell: float
    markout_bps: float

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
            "estimated_hjb_ev": self.estimated_ev,
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


def compute_hjb_quote(state: HjbState, cfg: HjbConfig | None = None) -> HjbQuote | None:
    cfg = cfg or HjbConfig()
    mid = float(state.mid)
    spread = float(state.spread)
    if mid <= 0.0 or spread <= 0.0:
        return None
    micro = float(state.microprice) if state.microprice > 0.0 else mid
    inventory = _clip(state.inventory, -1.0, 1.0)
    alpha_eff = _clip(
        float(state.alpha) + cfg.ofi_alpha_weight * _clip(state.ofi, -1.0, 1.0),
        -1.0,
        1.0,
    )
    gamma = adaptive_gamma(state, cfg)
    vol_ratio = _clip(
        max(state.sigma, cfg.vol_floor) / max(cfg.vol_floor, 1e-9), 1.0, VOL_RATIO_CAP
    )
    sigma_term = vol_ratio * vol_ratio

    alpha_adj = cfg.alpha_shift * alpha_eff * spread
    alpha_adj = _clip(alpha_adj, -PENALTY_SPREAD_CAP * spread, PENALTY_SPREAD_CAP * spread)
    inventory_penalty = (
        inventory * gamma * sigma_term * cfg.horizon * cfg.inv_var_scale * spread
    )
    inventory_penalty = _clip(
        inventory_penalty, -PENALTY_SPREAD_CAP * spread, PENALTY_SPREAD_CAP * spread
    )

    toxicity = _clip(state.toxicity, 0.0, 1.0)
    adverse_from_markout = _clip(max(0.0, -float(state.markout_bps)) / 8.0, 0.0, 1.0)
    adverse = max(toxicity, adverse_from_markout)
    adverse_penalty = cfg.adverse_weight * adverse * spread
    latency_penalty = (
        cfg.latency_weight * math.tanh(max(0.0, float(state.latency_ms)) / 250.0) * spread
    )

    signed = 0.0 if abs(inventory) < 1e-9 else (1.0 if inventory > 0.0 else -1.0)
    reservation = (
        micro
        + alpha_adj
        - inventory_penalty
        - signed * adverse_penalty
        - signed * latency_penalty
    )

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
        estimated_ev=est,
        fill_hazard_buy=float(state.fill_hazard_buy),
        fill_hazard_sell=float(state.fill_hazard_sell),
        markout_bps=float(state.markout_bps),
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
        "mean_reservation_shift_long": (
            sum(r["reservation"] - r["microprice"] for r in long_rows) / max(len(long_rows), 1)
        ),
        "mean_reservation_shift_short": (
            sum(r["reservation"] - r["microprice"] for r in short_rows) / max(len(short_rows), 1)
        ),
        "hjb_ev_better_count": hjb_ev_wins,
        "hjb_ev_better_frac": hjb_ev_wins / max(len(rows), 1),
        "max_bid_size": max((r["hjb_bid_size"] for r in rows), default=0.0),
        "max_ask_size": max((r["hjb_ask_size"] for r in rows), default=0.0),
        "base_size": 0.25,
        "rows": rows,
    }
