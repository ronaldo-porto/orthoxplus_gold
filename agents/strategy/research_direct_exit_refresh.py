# SPDX-License-Identifier: MIT
"""Strategy1-Direct A1.9 queue-preserving Maker-exit helpers.

A1.8 shortened the profitable Maker-exit TTL to one publish cycle and failed:
useful volume fell ~24%, positive-RT production ~44%, and PnL rate ~79%.  The
bottleneck diagnosis (Maker exit realization) was right; the mechanism was not.

The root cause is that TTL, not the hysteresis rule, is the real repricing
clock.  ``_research_final_validate_instructions`` drops any placement on a book
that already holds a live order, and nothing cancels the stale exit first, so a
declined hold cannot actually reprice -- the quote just rides to expiry.  That
is also why the QUIET exit TTL is deliberately capped below one publish cycle
("never keep a maker exit alive into the next publish cycle ... without
permitting same-side stacking"): a sub-cycle TTL was the workaround for the
missing cancel-then-replace path.

A1.9 replaces the workaround with the real mechanism: hold a resting profitable
exit by queue position, and reprice it only for a structural reason, via an
explicit cancel that lets the replacement land on the next state.

Phase A (this revision) is measurement only.  ``classify_resting_maker_exit``
is exercised in shadow mode and its decision is discarded, so runtime behaviour
is identical to the A1.7.5 baseline.  Phase B is what acts on the decision.
"""
from __future__ import annotations

import math
from typing import Any

DIRECT_EXIT_REFRESH_VERSION = "direct_exit_refresh_v4_16_2_a1_9_0_3"

# A1.7.5 baseline persistence window, restored by reverting A1.8.  Phase B
# raises this to DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS via PARAMS so the
# order is still live at the next evaluation; Phase A does not change it.
DIRECT_A19_BASELINE_PROFITABLE_EXIT_TTL_MS = 3000.0
# 4x the verified 1,000 ms publish cadence.  Derived from cadence, not fitted.
DIRECT_A19_TARGET_PROFITABLE_EXIT_TTL_MS = 4000.0

EXIT_HOLD = "HOLD"
EXIT_REPRICE = "REPRICE"

# Reasons a resting exit must give up its queue position.  Each is structural:
# a changed contract (size), a broken economic floor, an urgency escalation, or
# the market genuinely leaving the quote behind.
REASON_QUEUE_PRESERVED = "QUEUE_PRESERVED"
REASON_NO_RESTING_ORDER = "NO_RESTING_ORDER"
REASON_UNREADABLE_ORDER = "UNREADABLE_ORDER"
REASON_SIZE_SHORTFALL = "SIZE_SHORTFALL"
REASON_NET_BELOW_FLOOR = "NET_BELOW_FLOOR"
REASON_LADDER_ESCALATION = "LADDER_ESCALATION"
REASON_STALE_BEHIND_TOUCH = "STALE_BEHIND_TOUCH"

# Hold-rate denominator classes.  Mirrors profitable_maker_exit_ttl_ms's own
# eligibility test so the gate can never measure a population the mechanism is
# not allowed to act on.
EVAL_PERSIST_ELIGIBLE = "PERSIST_ELIGIBLE"
EVAL_SHORT_TTL_REGIME = "SHORT_TTL_REGIME"
EVAL_NO_MAKER_NET = "NO_MAKER_NET"
EVAL_BELOW_MIN_NET = "BELOW_MIN_NET"

# Why a resting exit was not present at the next evaluation.
ABSENT_NEVER_PLACED = "NEVER_PLACED"
ABSENT_EXPIRED = "EXPIRED"
ABSENT_FILLED = "FILLED"
ABSENT_WAIT_CANCEL = "WAIT_CANCEL"
ABSENT_NEG_AGGRESSIVE_CANCEL = "NEG_AGGRESSIVE_CANCEL"
ABSENT_REPRICE_CANCEL = "REPRICE_CANCEL"
# A1.9.0.3: the two cancel paths that were never registered, so the orders they
# killed fell through to EXPIRED and overstated exchange-side expiry.
ABSENT_ENTRY_QUOTE_CANCEL = "ENTRY_QUOTE_CANCEL"
ABSENT_PARTIAL_REMAINDER_CANCEL = "PARTIAL_REMAINDER_CANCEL"
# Bounded memory, not a real disposition: the row aged out of the ledger without
# any notice explaining it.  Must stay distinct from a measured expiry.
ABSENT_LEDGER_SWEEP = "LEDGER_SWEEP"

# Every disposition that means "we asked for this cancel", as opposed to the
# exchange retiring the order on its own.
AGENT_CANCEL_DISPOSITIONS = frozenset({
    ABSENT_WAIT_CANCEL,
    ABSENT_NEG_AGGRESSIVE_CANCEL,
    ABSENT_REPRICE_CANCEL,
    ABSENT_ENTRY_QUOTE_CANCEL,
    ABSENT_PARTIAL_REMAINDER_CANCEL,
})

_LADDER_RUNG = {
    "PASSIVE_MAKER_EXIT": 0,
    "COMPETITIVE_MAKER_EXIT": 1,
    "AGGRESSIVE_MAKER_EXIT": 2,
}


def ladder_rung(action: Any) -> int:
    """Ladder urgency index, or -1 when the action is not a Maker exit rung."""
    return _LADDER_RUNG.get(str(action or "").upper(), -1)


def exit_eval_class(
    *,
    maker_net_bps: Any,
    min_net_bps: float,
    market_regime: Any,
) -> str:
    """Classify one exit evaluation for the A1.9 hold-rate denominator.

    TOXIC/STRESSED and sub-floor exits deliberately keep the short base TTL, so
    they cannot hold a queue position and must not count against the gate.
    """
    if str(market_regime or "NORMAL").upper() in {"TOXIC", "STRESSED"}:
        return EVAL_SHORT_TTL_REGIME
    try:
        net = float(maker_net_bps)
    except (TypeError, ValueError):
        return EVAL_NO_MAKER_NET
    if not math.isfinite(net):
        return EVAL_NO_MAKER_NET
    try:
        floor = float(min_net_bps)
    except (TypeError, ValueError):
        floor = 0.0
    if net <= floor:
        return EVAL_BELOW_MIN_NET
    return EVAL_PERSIST_ELIGIBLE


def forgone_edge_bps(
    *,
    existing_price: Any,
    desired_price: Any,
    long_position: bool,
) -> float:
    """Edge given up by holding a resting exit instead of repricing to the touch.

    Zero whenever the resting quote is at or better than the desired price.
    Reported on every HOLD so a favourable-side bound can be set from measured
    cost rather than guessed.
    """
    try:
        old = float(existing_price)
        new = float(desired_price)
    except (TypeError, ValueError):
        return 0.0
    if not (math.isfinite(old) and math.isfinite(new)) or old <= 0.0:
        return 0.0
    gap = (new - old) if long_position else (old - new)
    return max(0.0, gap / old * 10000.0)


def behind_ticks(
    *,
    existing_price: Any,
    desired_price: Any,
    tick_size: float,
    long_position: bool,
) -> float:
    """Signed adverse drift in ticks; negative means the quote is favourable."""
    try:
        old = float(existing_price)
        new = float(desired_price)
        tick = max(1e-12, float(tick_size))
    except (TypeError, ValueError):
        return 0.0
    if not (math.isfinite(old) and math.isfinite(new)):
        return 0.0
    return ((old - new) if long_position else (new - old)) / tick


def classify_resting_maker_exit(
    *,
    existing_price: Any,
    desired_price: Any,
    tick_size: float,
    long_position: bool,
    existing_qty: Any,
    desired_qty: Any,
    existing_net_bps: Any,
    floor_net_bps: float,
    existing_action: Any = None,
    desired_action: Any = None,
    reprice_ticks: float = 3.0,
    min_qty_fraction: float = 0.80,
) -> tuple[str, str]:
    """Decide whether a live same-side Maker exit keeps its queue position.

    HOLD is the default and every REPRICE needs a structural reason.  Measured
    ``exit_p_fill_horizon`` is ~0.043-0.049 and essentially flat across the
    PASSIVE/COMPETITIVE/AGGRESSIVE rungs, i.e. price aggression buys almost no
    fill probability while time in book does -- so repricing spends the whole
    queue position for close to nothing.

    Returns ``(decision, reason)``.
    """
    if existing_price is None or desired_price is None:
        return EXIT_REPRICE, REASON_NO_RESTING_ORDER
    try:
        old = float(existing_price)
        new = float(desired_price)
        tick = max(1e-12, float(tick_size))
        have = max(0.0, float(existing_qty))
        want = max(0.0, float(desired_qty))
        net = float(existing_net_bps)
        floor = float(floor_net_bps)
    except (TypeError, ValueError):
        return EXIT_REPRICE, REASON_UNREADABLE_ORDER
    if not (math.isfinite(old) and math.isfinite(new) and math.isfinite(net)):
        return EXIT_REPRICE, REASON_UNREADABLE_ORDER
    if old <= 0.0 or new <= 0.0 or want <= 0.0:
        return EXIT_REPRICE, REASON_UNREADABLE_ORDER
    if have + 1e-12 < want * max(0.0, min(1.0, float(min_qty_fraction))):
        return EXIT_REPRICE, REASON_SIZE_SHORTFALL
    # Judge the resting order on its OWN price, never on this cycle's desired
    # price.  A quote that still completes the lifecycle at or above the floor
    # is a valid exit even when the desired rung has moved.
    if net + 1e-12 < floor:
        return EXIT_REPRICE, REASON_NET_BELOW_FLOOR
    old_rung = ladder_rung(existing_action)
    new_rung = ladder_rung(desired_action)
    if old_rung >= 0 and new_rung > old_rung:
        return EXIT_REPRICE, REASON_LADDER_ESCALATION
    # Directional staleness only.  A resting SELL below the new desired price
    # (or a resting BUY above it) is closer to filling and nets no less, so the
    # favourable half-plane is held.  A1.8's symmetric abs() test tore those
    # orders down for no economic reason.
    if behind_ticks(
        existing_price=old, desired_price=new, tick_size=tick, long_position=long_position
    ) >= max(1.0, float(reprice_ticks)):
        return EXIT_REPRICE, REASON_STALE_BEHIND_TOUCH
    return EXIT_HOLD, REASON_QUEUE_PRESERVED
