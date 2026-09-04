# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.6.3 deterministic exposure-liveness math.

Pure helpers only: no TAOS/bittensor imports and no learned state.
"""
from __future__ import annotations

from dataclasses import dataclass

DIRECT_EXPOSURE_VERSION = "direct_exposure_v4_16_2_a1_6_3"


def worst_case_abs_inventory(net: float, buy_qty: float = 0.0, sell_qty: float = 0.0) -> float:
    """Worst absolute inventory reachable if live BUY/SELL orders fill in any subset."""
    n = float(net)
    b = max(0.0, float(buy_qty or 0.0))
    s = max(0.0, float(sell_qty or 0.0))
    return max(abs(n), abs(n + b), abs(n - s), abs(n + b - s))


def outstanding_reservation(net: float, buy_qty: float, sell_qty: float, *, min_order: float, eps: float = 1e-9) -> tuple[float, int]:
    """Return additional absolute BASE reservation and productive-open reservation."""
    current = abs(float(net))
    worst = worst_case_abs_inventory(net, buy_qty, sell_qty)
    reserve_abs = max(0.0, worst - current)
    reserve_open = int(current + eps < float(min_order) and worst + eps >= float(min_order))
    return reserve_abs, reserve_open


@dataclass(frozen=True)
class BatchExposure:
    previous_worst_abs: float
    new_worst_abs: float
    delta_worst_abs: float
    risk_reducing: bool
    opens_productive_slot: bool


def add_order_to_batch(
    *,
    net: float,
    buy_before: float,
    sell_before: float,
    side: str,
    quantity: float,
    min_order: float,
    eps: float = 1e-9,
) -> BatchExposure:
    """Exposure effect of adding one order to a same-request book batch.

    Symmetric Maker BUY+SELL orders reserve the worst one-sided fill, not net zero.
    A genuine reduction while over cap has zero new reservation and is therefore legal.
    """
    token = str(side or "").lower()
    q = max(0.0, float(quantity or 0.0))
    b0 = max(0.0, float(buy_before or 0.0))
    s0 = max(0.0, float(sell_before or 0.0))
    b1 = b0 + (q if token in {"buy", "bid", "b", "0"} else 0.0)
    s1 = s0 + (q if token not in {"buy", "bid", "b", "0"} else 0.0)
    prev = worst_case_abs_inventory(net, b0, s0)
    new = worst_case_abs_inventory(net, b1, s1)
    delta = max(0.0, new - prev)
    return BatchExposure(
        previous_worst_abs=prev,
        new_worst_abs=new,
        delta_worst_abs=delta,
        risk_reducing=delta <= 1e-12,
        opens_productive_slot=(prev + eps < float(min_order) and new + eps >= float(min_order)),
    )
