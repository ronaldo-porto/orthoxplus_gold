# SPDX-License-Identifier: MIT
"""v5.0.1: which Kappa-eligible books the validator actually counts.

``reward.py`` multiplies each book's normalized Kappa-3 by a per-book activity factor before it
takes the median.  Under the validator's default flags (``taos/im/config``: activity impact 0,
decay_rate 0, trade_volume_sampling_interval 600 s; kappa min_lookback 5,400 s) that factor

  * starts at 0.0: a new registration resets it (``trade.reset_agent_histories``), and so does a
    fresh validator state (``persistence.py``);
  * is written only by ``calculate_kappa_score``, which returns before that step while the uid's
    stored rounds span less than min_lookback (``kappa_3`` returns None);
  * becomes 1.0 at the first scoring pass that finds a round trip in its latest 600 s bucket or
    the one before (``_aggregate_roundtrip_volumes``), and with decay_rate 0 it stays there.

A book with a Kappa-3 and a factor of 0 is scored 0.0: it sits inside the median, where a book
without a Kappa-3 is ignored.  Replaying UID 18's RealNet log (20260915_063311) with the gate
5,400 s after the agent's first state reproduces both activity means the dashboard showed
(0.2656 over ticks 5,631-5,706, 0.2969 over 5,866-6,136) and a Kappa-3 score of 0 at every
checkpoint to tick 8,489.  v5.0.0's mirror assumed a factor of 1.0 and reported 0.537.

The agent sees its own round trips: ``realized_pnl_history`` is keyed by the state timestamp, as
the validator's is, so a non-zero entry is a FIFO-closing fill at the validator's timestamp.  It
cannot see when the validator started storing its rounds, only evidence that it had by then: its
first state and its oldest retained observation.  A gate estimated from that evidence is never
earlier than the real one, so a book called activated here is activated by the validator's next
scoring pass (one every 5 s of simulation time).  A book called cold may already be activated,
which costs one redundant round trip.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from research_score_ev import ScoreEVBreakdown

V501_ACTIVITY_VERSION = "direct_validator_activity_v5_0_1"

# taos/im/config/__init__.py defaults.
ACTIVITY_SAMPLING_NS = 600_000_000_000      # scoring.activity.trade_volume_sampling_interval
KAPPA_MIN_LOOKBACK_NS = 5_400_000_000_000   # scoring.kappa.min_lookback
SCORING_INTERVAL_NS = 5_000_000_000         # scoring.interval

# A backward jump of the simulation clock at least this long is a new simulation.  The validator
# rebases the history it keeps onto the new clock (trade.shift_simulation_histories) and the agent
# rebases its rolling Kappa evidence the same way, so the estimated start moves with them.  A
# shorter jump is a checkpoint rewind and moves nothing.
REBASE_MIN_JUMP_NS = 600_000_000_000        # the simulation's grace_period

STATE_UNMODELED = "UNMODELED"
STATE_GATE_CLOSED = "GATE_CLOSED"
STATE_INCOMPLETE = "RAW_INCOMPLETE"
STATE_COLD = "RAW_ELIGIBLE_COLD"
STATE_ACTIVATED = "SCORE_ACTIVATED"

EVIDENCE_FIRST_STATE = "FIRST_STATE"
EVIDENCE_OBSERVATION = "OBSERVATION"


def activation_floor_ts(gate_ts: int, sampling_ns: int = ACTIVITY_SAMPLING_NS) -> int:
    """The earliest round trip that the first scoring pass after the gate still counts.

    That pass takes the latest bucket at or before its own 600 s floor and accepts it when it is
    at most one bucket older, so the bucket before the gate's bucket already counts.
    """
    return (int(gate_ts) // int(sampling_ns)) * int(sampling_ns) - int(sampling_ns)


def cliff_needed(activated: int, scored: int) -> int:
    """Activated books still missing before the median of the scored books is a Kappa, not 0.0."""
    return max(0, int(scored) // 2 + 1 - int(activated))


class ActivityBelief:
    """The gate and the activation floor, from the earliest evidence that the validator held rounds."""

    def __init__(
        self,
        *,
        min_lookback_ns: int = KAPPA_MIN_LOOKBACK_NS,
        sampling_ns: int = ACTIVITY_SAMPLING_NS,
        scoring_interval_ns: int = SCORING_INTERVAL_NS,
    ) -> None:
        self.min_lookback_ns = int(min_lookback_ns)
        self.sampling_ns = int(sampling_ns)
        self.scoring_interval_ns = int(scoring_interval_ns)
        self.history_start_ts: int | None = None
        self.history_start_source: str | None = None
        self.rebases = 0
        # v5.0.4 H2: a declared start.  Evidence no longer moves it; a new simulation's rebase still does.
        self.pinned = False

    def pin(self, ts: Any, source: str) -> bool:
        try:
            value = int(ts)
        except (TypeError, ValueError):
            return False
        self.history_start_ts = value
        self.history_start_source = str(source)
        self.pinned = True
        return True

    def note_evidence(self, ts: Any, source: str) -> bool:
        if self.pinned:
            return False
        try:
            value = int(ts)
        except (TypeError, ValueError):
            return False
        if self.history_start_ts is None or value < self.history_start_ts:
            self.history_start_ts = value
            self.history_start_source = str(source)
            return True
        return False

    def rebase(self, shift_ns: int) -> None:
        if self.history_start_ts is not None:
            self.history_start_ts += int(shift_ns)
        self.rebases += 1

    @property
    def gate_ts(self) -> int | None:
        if self.history_start_ts is None:
            return None
        # One scoring interval of slack: the first pass to see the full span can land up to one
        # interval after it.
        return self.history_start_ts + self.min_lookback_ns + self.scoring_interval_ns

    @property
    def floor_ts(self) -> int | None:
        gate = self.gate_ts
        return None if gate is None else activation_floor_ts(gate, self.sampling_ns)

    def gate_open(self, now: int) -> bool:
        gate = self.gate_ts
        return gate is not None and int(now) >= gate

    def window_open(self, now: int) -> bool:
        """From here on a round trip counts toward activation."""
        floor = self.floor_ts
        return floor is not None and int(now) >= floor

    def activated(self, timestamps: Iterable[int]) -> bool:
        floor = self.floor_ts
        if floor is None:
            return False
        return any(int(ts) >= floor for ts in timestamps)

    def book_state(self, timestamps: Iterable[int], *, eligible: bool, now: int) -> str:
        if not self.window_open(now):
            return STATE_GATE_CLOSED
        if not eligible:
            return STATE_INCOMPLETE
        return STATE_ACTIVATED if self.activated(timestamps) else STATE_COLD

    def factors(
        self,
        timestamps_by_book: Mapping[int, Iterable[int]],
        *,
        book_count: int,
        now: int,
    ) -> dict[int, float]:
        """The factor the validator holds for each book: 0.0 everywhere until the gate."""
        if not self.gate_open(now):
            return {book: 0.0 for book in range(int(book_count))}
        return {
            book: (1.0 if self.activated(timestamps_by_book.get(book, ())) else 0.0)
            for book in range(int(book_count))
        }


@dataclass(frozen=True)
class ActivityView:
    states: dict[int, str]
    cold: frozenset[int]
    eligible: int
    activated_eligible: int
    window_open: bool
    gate_open: bool


def activity_view(
    belief: ActivityBelief,
    rolling: Mapping[Any, Iterable[int]],
    *,
    required: int,
    now: int,
) -> ActivityView:
    """Each book with an observation in the window, by what the validator does with it."""
    states: dict[int, str] = {}
    cold: set[int] = set()
    eligible = activated = 0
    for raw_book, rows in rolling.items():
        book = int(raw_book)
        stamps = tuple(rows or ())
        is_eligible = len(stamps) >= int(required)
        eligible += int(is_eligible)
        state = belief.book_state(stamps, eligible=is_eligible, now=now)
        states[book] = state
        if state == STATE_COLD:
            cold.add(book)
        elif state == STATE_ACTIVATED:
            activated += 1
    return ActivityView(
        states=states, cold=frozenset(cold), eligible=eligible, activated_eligible=activated,
        window_open=belief.window_open(now), gate_open=belief.gate_open(now),
    )


@dataclass(frozen=True)
class ActivationScoreEV(ScoreEVBreakdown):
    """Score-EV with the v5.0.1 activation term named on the RANK row."""

    activation_value: float = 0.0
    score_state: str = STATE_UNMODELED

    def as_log(self) -> dict[str, Any]:
        row = ScoreEVBreakdown.as_log(self)
        row["activation_value"] = float(self.activation_value)
        row["score_state"] = self.score_state
        row["v501_activity_version"] = V501_ACTIVITY_VERSION
        return row
