# SPDX-License-Identifier: MIT
"""Bounded scheduler retry quarantine for SN79 Research V4.14.4.

The Wide-Kappa scheduler remains authoritative.  This helper only prevents a
currently impossible flat entry candidate from repeatedly consuming lane grants
across ticks after a hard economic/toxicity rejection.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Hashable

SCHEDULER_RETRY_VERSION = "scheduler_retry_rotation_v4_15_2"
COMPLETION_REQUOTE_REMAINING = frozenset({1, 2})
COMPLETION_REQUOTE_RETRY_EXEMPT_REASONS = frozenset({"NEGATIVE_EV"})

HARD_RETRY_REASONS = frozenset({
    "NEGATIVE_EV",
    "TOXIC",
    "TOXIC_BOOK",
    "AVOID",
    "AVOID_BOOK",
    "AVOID_LIST",
})


def normalize_reason(reason: Any) -> str:
    return str(reason or "").strip().upper()


def is_hard_retry_reason(reason: Any) -> bool:
    return normalize_reason(reason) in HARD_RETRY_REASONS


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def score_ev_fingerprint(ev: Any) -> Hashable | None:
    """Coarse fingerprint so a materially changed candidate can be reconsidered."""
    if ev is None:
        return None
    reason = normalize_reason(getattr(ev, "reject_reason", None))
    trading_ev = _finite(getattr(ev, "trading_ev", 0.0), 0.0)
    maker_ev = _finite(getattr(ev, "maker_ev", getattr(ev, "expected_maker_ev", 0.0)), 0.0)
    toxic = int(bool(getattr(ev, "toxic", False)))
    eligible = int(bool(getattr(ev, "eligible", True)))
    # Bucketing avoids clearing the quarantine for insignificant float noise.
    return (
        reason,
        round(trading_ev, 1),
        round(maker_ev, 1),
        toxic,
        eligible,
    )


@dataclass
class RetryState:
    reason: str
    reject_count: int
    blocked_until_tick: int
    fingerprint: Hashable | None
    last_reject_tick: int


@dataclass(frozen=True)
class RetryDecision:
    blocked: bool
    reason: str
    blocked_until_tick: int
    remaining_ticks: int
    reject_count: int
    fingerprint_changed: bool = False

    def as_log(self, *, book_id: int) -> dict[str, Any]:
        return {
            "scheduler_retry_version": SCHEDULER_RETRY_VERSION,
            "book": int(book_id),
            "retry_blocked": int(bool(self.blocked)),
            "retry_reason": self.reason,
            "retry_until_tick": int(self.blocked_until_tick),
            "retry_remaining_ticks": int(self.remaining_ticks),
            "retry_reject_count": int(self.reject_count),
            "retry_fingerprint_changed": int(bool(self.fingerprint_changed)),
        }


class SchedulerRetryGuard:
    """Small in-memory rejection cache; never blocks inventory/risk exits."""

    def __init__(
        self,
        *,
        negative_ev_base_ticks: int = 8,
        toxic_base_ticks: int = 16,
        avoid_base_ticks: int = 16,
        max_cooldown_ticks: int = 64,
    ) -> None:
        self.negative_ev_base_ticks = max(1, int(negative_ev_base_ticks))
        self.toxic_base_ticks = max(1, int(toxic_base_ticks))
        self.avoid_base_ticks = max(1, int(avoid_base_ticks))
        self.max_cooldown_ticks = max(
            self.negative_ev_base_ticks,
            self.toxic_base_ticks,
            self.avoid_base_ticks,
            int(max_cooldown_ticks),
        )
        self._state: dict[int, RetryState] = {}
        self.total_rejects = 0
        self.total_skips = 0
        self.total_rotations = 0
        self.total_clears = 0

    def _base(self, reason: str) -> int:
        token = normalize_reason(reason)
        if token == "NEGATIVE_EV":
            return self.negative_ev_base_ticks
        if token in {"TOXIC", "TOXIC_BOOK"}:
            return self.toxic_base_ticks
        return self.avoid_base_ticks

    def record_reject(
        self,
        book_id: int,
        *,
        tick: int,
        reason: Any,
        fingerprint: Hashable | None = None,
    ) -> RetryDecision:
        token = normalize_reason(reason)
        if not is_hard_retry_reason(token):
            return RetryDecision(False, token, int(tick), 0, 0)
        bid = int(book_id)
        now = max(0, int(tick))
        prev = self._state.get(bid)
        same_failure = bool(prev is not None and prev.reason == token and prev.fingerprint == fingerprint)
        reject_count = (prev.reject_count + 1) if same_failure else 1
        base = self._base(token)
        cooldown = min(self.max_cooldown_ticks, base * (2 ** min(3, reject_count - 1)))
        until = now + cooldown
        self._state[bid] = RetryState(token, reject_count, until, fingerprint, now)
        self.total_rejects += 1
        return RetryDecision(True, token, until, cooldown, reject_count)

    def should_skip(
        self,
        book_id: int,
        *,
        tick: int,
        fingerprint: Hashable | None = None,
        observations_remaining: int | None = None,
    ) -> RetryDecision:
        bid = int(book_id)
        now = max(0, int(tick))
        state = self._state.get(bid)
        if state is None:
            return RetryDecision(False, "", now, 0, 0)

        # A material score/economic state change should be reconsidered now.
        if fingerprint is not None and state.fingerprint is not None and fingerprint != state.fingerprint:
            self._state.pop(bid, None)
            self.total_clears += 1
            return RetryDecision(False, state.reason, now, 0, state.reject_count, True)

        if now >= state.blocked_until_tick:
            return RetryDecision(False, state.reason, state.blocked_until_tick, 0, state.reject_count)

        kappa_remaining = max(0, int(observations_remaining or 0))
        if (
            kappa_remaining in COMPLETION_REQUOTE_REMAINING
            and normalize_reason(state.reason) in COMPLETION_REQUOTE_RETRY_EXEMPT_REASONS
        ):
            # Last-tick NEGATIVE_EV must not hide one-away/two-away from the
            # due set. Quote-time EV still rejects a currently-negative book.
            # TOXIC / AVOID_LIST quarantine is unchanged.
            return RetryDecision(
                False, state.reason, state.blocked_until_tick, 0, state.reject_count,
            )

        remaining = state.blocked_until_tick - now
        self.total_skips += 1
        self.total_rotations += 1
        return RetryDecision(True, state.reason, state.blocked_until_tick, remaining, state.reject_count)

    def clear(self, book_id: int) -> None:
        if self._state.pop(int(book_id), None) is not None:
            self.total_clears += 1

    def reset(self) -> None:
        """Clear session-scoped quarantine state and counters."""
        self._state.clear()
        self.total_rejects = 0
        self.total_skips = 0
        self.total_rotations = 0
        self.total_clears = 0

    def snapshot(self) -> dict[str, int]:
        return {
            "scheduler_retry_active": len(self._state),
            "scheduler_retry_rejects": self.total_rejects,
            "scheduler_retry_skips": self.total_skips,
            "scheduler_retry_rotations": self.total_rotations,
            "scheduler_retry_clears": self.total_clears,
        }
