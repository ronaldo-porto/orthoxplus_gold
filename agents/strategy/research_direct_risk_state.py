# SPDX-License-Identifier: MIT
"""A1.9.9: one exit authority for a position that has reached ABSOLUTE_PROTECTION.

Measured on the A1.9.8 run, log 20260914_012543, ticks 1-4,000 (378 round trips,
G10 1.5746).  Book 49 bought 0.25 at 271.14 on tick 2,753 and was ABSOLUTE at
its first evaluation (-205 bps mark).  The frozen chooser parks a position whose
reduction is not executable, and it reads the reduction as not executable
whenever the published touch is crossed.  On 216 of book 49's 218 evaluations
the reduction read not executable, each time with the taker net 8 to 753 bps
above the maker net: a bid above the ask.  On the two ticks the touch read
valid the chooser sent the taker: tick 2,758 went out as a bounded market order
and came back unfilled, tick 2,975 filled at 238.46 -- -8.199, 92.6% of the
run's cubic downside.  269 of the run's 415 ABSOLUTE evaluations were PARK,
against 6 in A1.9.7.

A crossed touch is not a reason to hold.  A market order matches the venue's
own book, and the simulator bounds it from the venue's best price at arrival
(simulate/trading/src/cpp/book/Book.cpp, Book::placeOrder), not from ours.  An
order that meets no liquidity comes back unfilled and the position is still
open -- which is the case for sending it again, not for no longer sending it.

The state machine, per book and per position:

    (none) --ABSOLUTE evaluation--> ABSOLUTE_EXIT_PENDING --flat / new position / epoch--> (none)

The state is sticky: a later evaluation in a better band does not leave it.
After their first ABSOLUTE evaluation 7 of 378 round trips reached a better band
before closing, carrying 0.06% of cubic.  On book 71 every "NORMAL" reading came
between ABSOLUTE ones, with the taker net at +483, +169, +466 and +157 bps.

While a position is pending, the final decision must be an exit:

    R1  A1.9.8, unchanged: a loss-recovery arm over ABSOLUTE_PROTECTION_REDUCE
        gives the taker back.
    R2  a maker exit priced below the +1 bps grace floor becomes the taker.
    R3  WAIT or PARK becomes the taker.

A taker passes unchanged.  A1.9.9 also let a positive maker (>= +1 bps, the
A1.7.2 grace or the A1.7.4.4 arm) rest while pending; A1.9.9.1 refuses it (R4):

    R4  any other maker exit becomes the taker.

Measured on the A1.9.9 run, log 20260914_083519, ticks 1-3,732: 5 pending
episodes rested a positive maker.  Book 74 held a +75 bps A1.7.4.4 maker for
109 ticks -- each 4,000 ms exit expired unfilled and was placed again, and the
A1.7.5 60-tick hold budget spends only on evaluated ticks, so it had used 22 --
then crossed at -165.7 bps against -38.1 when it became pending.  Across A1.9.7, A1.9.8 and
A1.9.9, 2 of the 14 episodes that rested a positive maker after reaching
ABSOLUTE ended positive.

R2, R3 and R4 need an executable quantity -- at least the minimum order, not
dust -- and a price on both sides of the book: an empty side is a market that
cannot be exited, and the frozen close path reads that side's price.  Those
positions stay on the frozen PARK path.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from research_direct_absolute_authority import restore_absolute_taker
from research_position_exit import (
    ACTION_MAKER_EXIT,
    ACTION_PARK_EXIT,
    ACTION_TAKER_EXIT,
    ACTION_WAIT,
    BAND_ABSOLUTE,
    PositionExitDecision,
    taker_clip_qty,
)

A199_RISK_STATE_VERSION = "direct_risk_state_v4_16_2_a1_9_9"
A1991_PENDING_OWNER_VERSION = "direct_pending_owner_v4_16_2_a1_9_9_1"

STATE_EXIT_PENDING = "ABSOLUTE_EXIT_PENDING"
# The pending taker keeps the frozen reason token, so the RISK authority, the
# -25 bps bound and every trigger-keyed consumer read it as an ABSOLUTE reduce.
# The corridor token is what tells the two apart in EXIT_DECISION rows.
A199_PENDING_REASON = "ABSOLUTE_PROTECTION_REDUCE"
A199_PENDING_CORRIDOR = "DIRECT_EXIT_PENDING_A199"
# A1.7.2 DIRECT_MAKER_EXIT_TARGET_BPS: the smallest maker exit its grace keeps.
A199_GRACE_MAKER_FLOOR_BPS = 1.0

RULE_A198_ARM = "A198_ARM"
RULE_LOSS_MAKER = "LOSS_MAKER"
RULE_NOT_EXITING = "NOT_EXITING"
RULE_POSITIVE_MAKER = "POSITIVE_MAKER"

ENTER = "ENTER"
CLEAR_FLAT = "CLEAR_FLAT"
CLEAR_NEW_POSITION = "CLEAR_NEW_POSITION"
CLEAR_EPOCH = "CLEAR_EPOCH"

# A pending exit that sends nothing for this many consecutive ticks gets a row.
A199_STALL_ROW_TICKS = 3


def _finite(value: Any, default: float | None = None) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def _token(obj: Any, name: str) -> str:
    return str(getattr(obj, name, "") or "")


@dataclass
class ExitPending:
    """One position's ABSOLUTE exit state."""

    sign: int
    entry: float | None
    since_tick: int
    evaluations: int = 0
    overrides: int = 0
    last_tick: int = -1


def position_sign(net_base: Any, eps: Any) -> int:
    net = _finite(net_base, 0.0) or 0.0
    e = abs(_finite(eps, 0.0) or 0.0)
    if net > e:
        return 1
    if net < -e:
        return -1
    return 0


def same_position(state: ExitPending, *, sign: int, entry: Any) -> bool:
    """A different side or a different entry price is a different position.

    A missing entry on either side never ends the state by itself: an unknown
    cost basis is not evidence that the position changed.
    """
    if int(state.sign) != int(sign):
        return False
    old = _finite(state.entry)
    new = _finite(entry)
    if old is None or new is None:
        return True
    return math.isclose(old, new, rel_tol=1e-9, abs_tol=1e-9)


def step_exit_pending(
    table: dict[int, ExitPending],
    book_id: Any,
    *,
    base_band: Any,
    net_base: Any,
    vwap_entry: Any,
    tick: Any,
    eps: Any,
) -> tuple[ExitPending | None, tuple[tuple[str, ExitPending], ...]]:
    """Advance one book's state at an exit evaluation.

    Returns the state after this evaluation and every transition it made, each
    with the state it concerns: the new one for ENTER, the ended one otherwise.
    """
    bid = int(book_id)
    now = int(_finite(tick, 0.0) or 0)
    sign = position_sign(net_base, eps)
    transitions: list[tuple[str, ExitPending]] = []
    state = table.get(bid)
    if state is not None and sign == 0:
        table.pop(bid, None)
        return None, ((CLEAR_FLAT, state),)
    if state is not None and not same_position(state, sign=sign, entry=vwap_entry):
        table.pop(bid, None)
        transitions.append((CLEAR_NEW_POSITION, state))
        state = None
    if state is None and sign != 0 and str(base_band or "") == BAND_ABSOLUTE:
        state = ExitPending(sign=sign, entry=_finite(vwap_entry), since_tick=now)
        table[bid] = state
        transitions.append((ENTER, state))
    if state is not None:
        state.evaluations += 1
        state.last_tick = now
    return state, tuple(transitions)


def two_sided_touch(bids: Any, asks: Any) -> bool:
    """A positive price on both sides of the book.  A crossed touch still qualifies."""
    try:
        if not bids or not asks:
            return False
        bid = _finite(getattr(bids[0], "price", None))
        ask = _finite(getattr(asks[0], "price", None))
    except Exception:
        return False
    return bid is not None and ask is not None and bid > 0.0 and ask > 0.0


def pending_taker(
    base_decision: Any,
    decision: Any,
    *,
    maker_net_bps: Any,
    taker_net_bps: Any,
    inventory_qty: Any,
    min_order: Any,
    taker_clip: Any,
) -> Any:
    """The reduce a pending position ships: the frozen base's own when it has one."""
    if (
        _token(base_decision, "action") == ACTION_TAKER_EXIT
        and _token(base_decision, "risk_band") == BAND_ABSOLUTE
    ):
        return base_decision
    del decision
    return PositionExitDecision(
        action=ACTION_TAKER_EXIT,
        risk_band=BAND_ABSOLUTE,
        maker_exit_utility=float(_finite(maker_net_bps, 0.0) or 0.0),
        taker_exit_utility=float(_finite(taker_net_bps, 0.0) or 0.0),
        wait_utility=0.0,
        selected_qty=taker_clip_qty(
            inventory_qty=abs(_finite(inventory_qty, 0.0) or 0.0),
            min_order=_finite(min_order, 0.25) or 0.25,
            taker_clip=_finite(taker_clip, 0.25) or 0.25,
        ),
        reason=A199_PENDING_REASON,
        corridor_action=A199_PENDING_CORRIDOR,
        corridor_stage=BAND_ABSOLUTE,
        continuation_penalty=0.0,
        low_fill_maker_rejected=0,
    )


def authorize_exit(
    *,
    pending: ExitPending | None,
    base_decision: Any,
    decision: Any,
    maker_net_bps: Any,
    taker_net_bps: Any,
    inventory_qty: Any,
    min_order: Any,
    taker_clip: Any,
    is_dust: Any,
    touch_two_sided: Any,
    a198_enabled: bool = True,
    grace_floor_bps: float = A199_GRACE_MAKER_FLOOR_BPS,
    allow_positive_maker: bool = True,
) -> tuple[Any, str | None, str | None]:
    """The final exit decision, the rule that set it, and the A1.9.8 arm when R1 did."""
    if decision is None:
        return decision, None, None
    # R1: A1.9.8 exactly as it shipped.  Its base is ABSOLUTE, so the position
    # is always pending by the time it applies.
    restored, arm = restore_absolute_taker(
        base_decision=base_decision, decision=decision, enabled=bool(a198_enabled),
    )
    if arm:
        return restored, RULE_A198_ARM, arm
    if pending is None:
        return decision, None, None
    action = _token(decision, "action")
    if action == ACTION_TAKER_EXIT:
        return decision, None, None
    qty = abs(_finite(inventory_qty, 0.0) or 0.0)
    floor = abs(_finite(min_order, 0.25) or 0.25)
    executable = qty + 1e-12 >= floor and not bool(is_dust) and bool(touch_two_sided)
    if not executable:
        return decision, None, None
    reduce_inputs = dict(
        maker_net_bps=maker_net_bps, taker_net_bps=taker_net_bps,
        inventory_qty=qty, min_order=floor, taker_clip=taker_clip,
    )
    maker = _finite(maker_net_bps, 0.0) or 0.0
    # R2: a resting maker exit priced at a loss holds the book for two ticks.
    if action == ACTION_MAKER_EXIT and maker + 1e-12 < float(grace_floor_bps):
        return pending_taker(base_decision, decision, **reduce_inputs), RULE_LOSS_MAKER, None
    # R4 (A1.9.9.1): a pending position owns its book; no maker exit rests at any price.
    if action == ACTION_MAKER_EXIT and not allow_positive_maker:
        return pending_taker(base_decision, decision, **reduce_inputs), RULE_POSITIVE_MAKER, None
    # R3: a pending position is never left without an exit.
    if action in (ACTION_WAIT, ACTION_PARK_EXIT):
        return pending_taker(base_decision, decision, **reduce_inputs), RULE_NOT_EXITING, None
    return decision, None, None


# ---- measurement: a pending exit that sends nothing ---------------------------------

def end_exit_stall(
    stalls: dict[int, dict[str, int]],
    book_id: Any,
    *,
    tick: Any,
    ended_by: str,
    row_ticks: int = A199_STALL_ROW_TICKS,
) -> dict[str, Any] | None:
    """End one book's silence; a row when it lasted at least `row_ticks` ticks."""
    row = stalls.pop(int(book_id), None)
    if row is None or int(row.get("ticks", 0)) < int(row_ticks):
        return None
    return {
        "book": int(book_id),
        "from_tick": int(row["from_tick"]),
        "to_tick": int(row["last_tick"]),
        "ticks": int(row["ticks"]),
        "ended_by": str(ended_by),
        "end_tick": int(_finite(tick, 0.0) or 0),
    }


def note_exit_stall(
    stalls: dict[int, dict[str, int]],
    book_id: Any,
    *,
    tick: Any,
    has_instruction: bool,
    row_ticks: int = A199_STALL_ROW_TICKS,
) -> dict[str, Any] | None:
    """Count a pending book's consecutive ticks without an instruction.  One count per tick."""
    bid = int(book_id)
    now = int(_finite(tick, 0.0) or 0)
    if has_instruction:
        return end_exit_stall(stalls, bid, tick=now, ended_by="INSTRUCTION", row_ticks=row_ticks)
    row = stalls.get(bid)
    if row is None:
        stalls[bid] = {"from_tick": now, "ticks": 1, "last_tick": now}
    elif int(row["last_tick"]) != now:
        row["ticks"] = int(row["ticks"]) + 1
        row["last_tick"] = now
    return None
