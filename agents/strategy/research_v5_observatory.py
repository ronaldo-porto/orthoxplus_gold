# SPDX-License-Identifier: MIT
"""v5.0.3 G2: record what the validator sees, so the field can be measured instead of guessed.

Every scoring question that matters now -- what a top-20 miner actually does per book, how often a
resting quote gets a positive close on the MEDIAN book rather than a chosen one, whether the leaders
realize gains and hold losers -- is answerable from the state the validator already sends us, and
from nothing else we have.  Each state carries, for all 128 books, the depth AND the book's event
list, and a trade event names both sides:

    {"y": "t", "b": <book>, "t": <ts>, "p": <price>, "q": <qty>, "s": <side>,
     "Ta": <taker uid>, "Ma": <maker uid>, "Ti": .., "Mi": .., "Tf": .., "Mf": ..}

``Ta``/``Ma`` are the validator's own UIDs (``taos/im/protocol/models.py`` TradeInfo, whose
``model_validate`` requires them), which is what makes a per-UID replay possible at all.

COST.  The agent runs with ``lazy_load=1``, so ``state.books`` is a ``LazyBooks`` holding the raw
decompressed dicts and parsing a book only when something touches it.  This recorder never touches
them: it takes the raw mapping by reference and hands it to a writer thread, so the request path
pays one bounded-queue append.  Shaping, JSON and gzip all happen off the request path, and none of
it goes near the A1.9.9.2 idle collector's event-loop timer.

WHAT IS KEPT.  Trades are the measurement, so every book's trade events are kept on every state.
Depth is context, so the touch is kept on every state and the full ladder only every ``depth_every``
states.  On UID 18's own cadence that is about 2 MB an hour compressed against roughly 25 for whole
states.  A byte budget stops the recorder rather than the disk stopping the miner.

NOTHING HERE IS READ BY A DECISION.  The recorder is write-only: no strategy path consumes its
output, and a recorder fault is contained to the recorder.
"""
from __future__ import annotations

import gzip
import json
import os
import queue
import threading
import time
from typing import Any, Mapping

V503_OBSERVATORY_VERSION = "direct_observatory_v5_0_3"

# Raw wire keys, from taos/im/protocol/models.py.  Only these are assumed; every value is copied
# verbatim, so a field added upstream is recorded without a change here.
KEY_BOOK_ID = "i"
KEY_BIDS = "b"
KEY_ASKS = "a"
KEY_EVENTS = "e"
EVENT_TRADE = "t"
EVENT_TYPE = "y"

# Defaults.  depth_every is in states: 100 is about every 8 minutes of simulation at this cadence.
V503_DEPTH_EVERY = 100
V503_QUEUE_SIZE = 8
V503_MAX_BYTES = 2 * 1024 * 1024 * 1024
V503_ROTATE_BYTES = 256 * 1024 * 1024

STOP_BUDGET = "BYTE_BUDGET"
STOP_ERROR = "WRITE_ERROR"
STOP_CLOSED = "CLOSED"
SKIP_NO_RAW_BOOKS = "NO_RAW_BOOKS"
SKIP_QUEUE_FULL = "QUEUE_FULL"


def raw_books_of(books: Any) -> Mapping[Any, Any] | None:
    """The lazy raw book mapping, or None when the agent is not running lazily.

    Reaching for ``_raw_books`` is deliberate: it is the only view that costs nothing.  Without it
    the recorder records nothing rather than forcing 128 books through pydantic on the hot path.
    """
    raw = getattr(books, "_raw_books", None)
    return raw if isinstance(raw, Mapping) else None


def shape_state(
    raw_books: Mapping[Any, Any] | None,
    *,
    tick: int,
    ts: Any,
    depth: bool,
) -> dict[str, Any]:
    """One recorded state: every book's trades, its touch, and the full ladder when ``depth``.

    Values are copied verbatim from the wire dicts; nothing is parsed, renamed or rounded, so the
    replay reads exactly what the validator sent.
    """
    books: dict[str, Any] = {}
    trades = 0
    for raw_id, raw_book in (raw_books or {}).items():
        if not isinstance(raw_book, Mapping):
            continue
        bids = raw_book.get(KEY_BIDS) or []
        asks = raw_book.get(KEY_ASKS) or []
        row: dict[str, Any] = {}
        book_trades = [
            event for event in (raw_book.get(KEY_EVENTS) or [])
            if isinstance(event, Mapping) and event.get(EVENT_TYPE) == EVENT_TRADE
        ]
        if book_trades:
            row["t"] = book_trades
            trades += len(book_trades)
        if depth:
            row["b"] = list(bids)
            row["a"] = list(asks)
        else:
            if bids:
                row["bb"] = bids[0]
            if asks:
                row["ba"] = asks[0]
        if row:
            books[str(raw_id)] = row
    return {"tick": int(tick), "ts": ts, "depth": int(bool(depth)), "books": books, "n_trades": trades}


def depth_due(tick: Any, every: Any) -> bool:
    try:
        n = int(tick)
        step = int(every)
    except (TypeError, ValueError):
        return False
    return step > 0 and n > 0 and n % step == 0


class StateRecorder:
    """Owns the recorder's queue, writer thread and files for one agent process."""

    def __init__(
        self,
        directory: str,
        *,
        uid: Any,
        run_id: str = "",
        depth_every: int = V503_DEPTH_EVERY,
        max_bytes: int = V503_MAX_BYTES,
        rotate_bytes: int = V503_ROTATE_BYTES,
        queue_size: int = V503_QUEUE_SIZE,
        opener: Any = gzip.open,
        clock: Any = time.time,
    ) -> None:
        self.directory = str(directory)
        self.uid = uid
        self.run_id = str(run_id or time.strftime("%Y%m%d_%H%M%S"))
        self.depth_every = max(0, int(depth_every))
        self.max_bytes = max(0, int(max_bytes))
        self.rotate_bytes = max(1, int(rotate_bytes))
        self._opener = opener
        self._clock = clock
        self._queue: queue.Queue = queue.Queue(maxsize=max(1, int(queue_size)))
        self._thread: threading.Thread | None = None
        self._handle: Any = None
        self._part = 0
        self.bytes_written = 0
        self.states_captured = 0
        self.states_written = 0
        self.trades_written = 0
        self.dropped = 0
        self.errors = 0
        self.stopped_reason = ""
        self.last_skip = ""

    # ---- request path -------------------------------------------------------------------------

    def capture(self, books: Any, *, tick: int, ts: Any) -> bool:
        """Queue one state for the writer.  O(1): the raw mapping goes by reference."""
        if self.stopped_reason:
            return False
        raw = raw_books_of(books)
        if raw is None:
            self.last_skip = SKIP_NO_RAW_BOOKS
            return False
        try:
            self._queue.put_nowait((int(tick), ts, raw, depth_due(tick, self.depth_every)))
        except queue.Full:
            self.dropped += 1
            self.last_skip = SKIP_QUEUE_FULL
            return False
        self.states_captured += 1
        self.last_skip = ""
        self._ensure_thread()
        return True

    # ---- writer thread ------------------------------------------------------------------------

    def _ensure_thread(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        thread = threading.Thread(target=self._run, name="v503-observatory", daemon=True)
        self._thread = thread
        thread.start()

    def _run(self) -> None:
        while not self.stopped_reason:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            try:
                self._write(item)
            except Exception:
                self.errors += 1
                self.stopped_reason = STOP_ERROR
                self._close()

    def _write(self, item: tuple[int, Any, Mapping[Any, Any], bool]) -> None:
        tick, ts, raw, depth = item
        row = shape_state(raw, tick=tick, ts=ts, depth=depth)
        payload = json.dumps(row, separators=(",", ":"), default=str).encode("utf-8") + b"\n"
        handle = self._open()
        handle.write(payload)
        self.bytes_written += len(payload)
        self.states_written += 1
        self.trades_written += int(row.get("n_trades", 0) or 0)
        if self.max_bytes and self.bytes_written >= self.max_bytes:
            self.stopped_reason = STOP_BUDGET
            self._close()
            return
        if handle.tell() >= self.rotate_bytes:
            self._close()

    def _open(self) -> Any:
        if self._handle is None:
            os.makedirs(self.directory, exist_ok=True)
            self._part += 1
            name = f"observatory_uid{self.uid}_{self.run_id}_{self._part:04d}.jsonl.gz"
            self._handle = self._opener(os.path.join(self.directory, name), "ab")
        return self._handle

    def _close(self) -> None:
        handle, self._handle = self._handle, None
        if handle is not None:
            try:
                handle.close()
            except Exception:
                self.errors += 1

    def close(self) -> None:
        self.stopped_reason = self.stopped_reason or STOP_CLOSED
        self._close()

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": V503_OBSERVATORY_VERSION,
            "run_id": self.run_id,
            "depth_every": self.depth_every,
            "states_captured": self.states_captured,
            "states_written": self.states_written,
            "trades_written": self.trades_written,
            "bytes_written": self.bytes_written,
            "parts": self._part,
            "dropped": self.dropped,
            "errors": self.errors,
            "stopped_reason": self.stopped_reason,
            "last_skip": self.last_skip,
            "queued": self._queue.qsize(),
        }
