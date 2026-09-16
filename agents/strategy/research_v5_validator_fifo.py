# SPDX-License-Identifier: MIT
"""v5.0.3 G3: the validator's FIFO matcher, transcribed.

``taos/im/validator/trade.py match_trade_fifo`` is what turns this agent's fills into the realized
PnL the whole score is built on.  The agent keeps its own copy so it can score itself, and that copy
diverges from the validator's in exactly one place: the PARTIAL close.

    validator:  close_fee = fee * remaining_qty * quantity_inv      # prorated to the closed part
    agent:      close_fee = fee                                     # the whole fill's fee

When the closing fill is larger than the lot in front of it, the agent charges (or, at a rebate,
credits) the entire fill's fee to the part that closed, and the remainder that opens a new lot then
carries a prorated fee as well -- the same fee counted twice.

On this venue the maker fee is a rebate (median -73 bps on UID 18's run 20260915_063311, 98% of
entry decisions), so the error is a systematic OVERSTATEMENT of realized PnL on every partial close.

Measured on that run: 247 of 2,247 reducing fills (11.0%) took the partial branch, and at the
median fill fee of -0.4638 quote the double count is worth about +114.  The agent's ledger closed
the run at +1,803 against the validator's FIFO at +1,591, a gap of 212, so this accounts for
roughly HALF of it and the remainder is not yet explained -- fills the agent never saw, the
validator's per-round rounding to the volume decimals, and self-trades are the open candidates.
What is certain is the code: the two matchers differ here, and only one of them is the one the
score is computed with.

That number is not cosmetic.  Realized PnL per book per second is the input to kappa_3, to the PnL
score, and -- through ``_research_note_realized_pnl_event`` -- to the rolling Kappa authority that
book admission reads.  An agent scoring itself on inflated PnL mis-ranks its own books.

This module is the validator's version, with its comments kept where they explain the arithmetic.
It operates on the same ``{'longs': deque, 'shorts': deque}`` structure the agent already keeps, so
it is a drop-in for ``DetailedTemplateAgent._match_trade_fifo``.  Lots are ``(timestamp, quantity,
price, fee)`` with the fee attached to the OPENING leg, and both legs' fees are charged when the lot
closes -- which is why a maker rebate earned on entry is only realized at the exit.
"""
from __future__ import annotations

from typing import Any, MutableMapping

V503_VALIDATOR_FIFO_VERSION = "direct_validator_fifo_v5_0_3"


def match_trade_fifo(
    positions: MutableMapping[str, Any],
    *,
    is_buy: bool,
    quantity: float,
    price: float,
    fee: float,
    timestamp: Any,
) -> tuple[float, float]:
    """Realized PnL and round-trip volume for one fill, by the validator's own arithmetic.

    Mutates ``positions`` in place.  Returns ``(realized_pnl, roundtrip_volume)``.
    """
    longs = positions["longs"]
    shorts = positions["shorts"]

    if is_buy:
        if not shorts:
            longs.append((timestamp, quantity, price, fee))
            return 0.0, 0.0
    else:
        if not longs:
            shorts.append((timestamp, quantity, price, fee))
            return 0.0, 0.0

    realized_pnl = 0.0
    roundtrip_volume = 0.0
    remaining_qty = quantity

    quantity_inv = 1.0 / quantity if quantity > 0 else 0.0

    if is_buy:
        # Buying: close shorts first (FIFO), then open longs
        while remaining_qty > 0 and shorts:
            old_ts, old_qty, old_price, old_fee = shorts[0]

            if old_qty <= remaining_qty:
                price_pnl = (old_price - price) * old_qty
                close_fee = fee * old_qty * quantity_inv
                realized_pnl += price_pnl - old_fee - close_fee
                roundtrip_volume += old_qty
                remaining_qty -= old_qty
                shorts.popleft()
            else:
                old_qty_inv = 1.0 / old_qty

                price_pnl = (old_price - price) * remaining_qty
                # Prorate the fill fee to the portion that closes here; earlier
                # fully-closed lots in this same fill already took their share.
                close_fee = fee * remaining_qty * quantity_inv
                open_fee = old_fee * remaining_qty * old_qty_inv
                realized_pnl += price_pnl - open_fee - close_fee
                roundtrip_volume += remaining_qty

                remaining_position_fee = old_fee - open_fee
                shorts[0] = (old_ts, old_qty - remaining_qty, old_price, remaining_position_fee)
                remaining_qty = 0

        if remaining_qty > 0:
            open_fee = fee * remaining_qty * quantity_inv
            longs.append((timestamp, remaining_qty, price, open_fee))

    else:
        # Selling: close longs first (FIFO), then open shorts
        while remaining_qty > 0 and longs:
            old_ts, old_qty, old_price, old_fee = longs[0]

            if old_qty <= remaining_qty:
                price_pnl = (price - old_price) * old_qty
                close_fee = fee * old_qty * quantity_inv
                realized_pnl += price_pnl - old_fee - close_fee
                roundtrip_volume += old_qty
                remaining_qty -= old_qty
                longs.popleft()
            else:
                old_qty_inv = 1.0 / old_qty

                price_pnl = (price - old_price) * remaining_qty
                close_fee = fee * remaining_qty * quantity_inv
                open_fee = old_fee * remaining_qty * old_qty_inv
                realized_pnl += price_pnl - open_fee - close_fee
                roundtrip_volume += remaining_qty

                remaining_position_fee = old_fee - open_fee
                longs[0] = (old_ts, old_qty - remaining_qty, old_price, remaining_position_fee)
                remaining_qty = 0

        if remaining_qty > 0:
            open_fee = fee * remaining_qty * quantity_inv
            shorts.append((timestamp, remaining_qty, price, open_fee))

    return realized_pnl, roundtrip_volume
