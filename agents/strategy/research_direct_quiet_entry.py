# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.7.4.5 quiet/no-rebate Maker-entry quality gate.

This is intentionally narrow.  It does not replace the A1.6 observable Maker
edge authority.  It raises that edge floor only when the live book looks like
the resumed-Testnet failure regime: QUIET market, no meaningful Maker rebate,
very low recent trade activity, and a wide spread.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

DIRECT_QUIET_ENTRY_VERSION = "direct_quiet_entry_v4_16_2_a1_7_4_5"

# Runtime-evidence calibration from the resumed Testnet session:
# maker fee ~= 0 bps, median spread ~= 31.5 bps, median trade_rate ~= 0,
# while a 15 bps retrospective edge floor improved entry quality without a
# global retune of the normal/rebate regime.
DIRECT_A1745_QUIET_ZERO_REBATE_MIN_EDGE_BPS = 15.0
DIRECT_A1745_ZERO_REBATE_FLOOR_BPS = -1.0
DIRECT_A1745_LOW_TRADE_RATE_MAX = 0.10
DIRECT_A1745_WIDE_SPREAD_MIN_BPS = 20.0


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return default
    return out if math.isfinite(out) else default


@dataclass(frozen=True)
class QuietEntryGate:
    active: bool
    effective_min_edge_bps: float
    base_min_edge_bps: float
    regime: str
    maker_fee_bps: float
    spread_bps: float
    trade_rate: float
    zero_rebate: bool
    low_trade: bool
    wide_spread: bool
    reason: str

    def as_log(self) -> dict[str, Any]:
        return {
            "direct_quiet_entry_version": DIRECT_QUIET_ENTRY_VERSION,
            "a1745_gate_active": int(self.active),
            "a1745_effective_min_edge_bps": self.effective_min_edge_bps,
            "a1745_base_min_edge_bps": self.base_min_edge_bps,
            "a1745_regime": self.regime,
            "a1745_maker_fee_bps": self.maker_fee_bps,
            "a1745_spread_bps": self.spread_bps,
            "a1745_trade_rate": self.trade_rate,
            "a1745_zero_rebate": int(self.zero_rebate),
            "a1745_low_trade": int(self.low_trade),
            "a1745_wide_spread": int(self.wide_spread),
            "a1745_gate_reason": self.reason,
        }


def quiet_zero_rebate_entry_gate(
    *,
    regime: str,
    maker_fee_bps: float,
    spread_bps: float,
    trade_rate: float,
    base_min_edge_bps: float,
) -> QuietEntryGate:
    """Return the effective Maker edge floor for the current live book.

    A1.7.4.5 activates only when *all* four runtime-observable conditions match
    the resumed-Testnet regime.  Outside that regime the frozen A1.6 2.5 bps
    floor is returned unchanged.
    """
    reg = str(regime or "").upper()
    fee = _finite(maker_fee_bps)
    spread = max(0.0, _finite(spread_bps))
    rate = max(0.0, _finite(trade_rate))
    base = max(0.0, _finite(base_min_edge_bps))

    zero_rebate = fee >= DIRECT_A1745_ZERO_REBATE_FLOOR_BPS
    low_trade = rate <= DIRECT_A1745_LOW_TRADE_RATE_MAX
    wide_spread = spread >= DIRECT_A1745_WIDE_SPREAD_MIN_BPS
    active = reg == "QUIET" and zero_rebate and low_trade and wide_spread
    effective = max(base, DIRECT_A1745_QUIET_ZERO_REBATE_MIN_EDGE_BPS) if active else base

    if active:
        reason = "QUIET_ZERO_REBATE_LOW_TRADE_WIDE_SPREAD"
    elif reg != "QUIET":
        reason = "REGIME_NOT_QUIET"
    elif not zero_rebate:
        reason = "MAKER_REBATE_PRESENT"
    elif not low_trade:
        reason = "TRADE_ACTIVITY_HEALTHY"
    else:
        reason = "SPREAD_NOT_WIDE"

    return QuietEntryGate(
        active=bool(active),
        effective_min_edge_bps=float(effective),
        base_min_edge_bps=float(base),
        regime=reg,
        maker_fee_bps=float(fee),
        spread_bps=float(spread),
        trade_rate=float(rate),
        zero_rebate=bool(zero_rebate),
        low_trade=bool(low_trade),
        wide_spread=bool(wide_spread),
        reason=reason,
    )
