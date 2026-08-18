# SPDX-License-Identifier: MIT
"""Strategy1 research wrapper with low-impact, grep-friendly observability.

Requires Strategy1_Debug.py beside this file. Trading decisions stay in the parent;
this class only replaces synchronous [S1DBG] emission with an async telemetry queue
and renders concise research events such as [S1R_SKIP], [S1R_ORDER], [S1R_FILL].
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import sys
import threading
import time
from typing import Any

_agent_dir = os.path.dirname(os.path.abspath(__file__))
if _agent_dir not in sys.path:
    sys.path.insert(0, _agent_dir)

from taos.common.agents import launch
from Strategy1_Debug import Strategy1_Debug


class Strategy1_Research(Strategy1_Debug):
    REASON_ALIAS = {
        "LOW_EXPECTED_ALPHA": "ALPHA",
        "ZERO_ORDER_SIZE": "SIZE_ZERO",
        "MAX_INVENTORY": "INVENTORY_MAX",
        "INVALID_QUOTE_PRICES": "BAD_PRICE",
        "VOLUME_CAP": "VOLUME_CAP",
        "NON_POSITIVE_EDGE": "EDGE",
        "NEGATIVE_EXPECTED_PNL": "NEG_PNL",
        "LOW_FILL_PROBABILITY": "FILL_PROB",
        "INSTRUCTION_LIMIT": "INSTR_LIMIT",
        "INSUFFICIENT_BALANCE": "BALANCE",
        "QUOTE_ORDER_GATE": "QUOTE_GATE",
        "QUOTE_DISABLED": "REGIME_DISABLED",
        "TOXIC_BOOK": "TOXIC",
        "TOXIC_REGIME": "TOXIC_REGIME",
        "INACTIVE_TIER": "INACTIVE",
        "MM_CANDIDATE_LIMIT": "MM_LIMIT",
        "MANAGEMENT_LIMIT": "MANAGEMENT_LIMIT",
        "MANAGE_ORDER_GATE": "MANAGE_GATE",
        "MAINT_INVENTORY_NONFLAT": "MAINT_INVENTORY",
        "MAINT_ARCHETYPE_BLOCK": "MAINT_ARCHETYPE",
        "MAINT_ORDER_GATE": "MAINT_GATE",
        "NO_BOOK_SIDES": "NO_BOOK_SIDES",
        "NO_PROFILE": "NO_PROFILE",
        "AVOID_LIST": "AVOID",
        "NO_PREDICTION": "NO_PREDICTION",
        "GRACE_PERIOD": "GRACE",
        "NO_ACTION": "NO_ACTION",
        # Reserved for descendants; base Strategy1 does not currently emit these.
        "HARD_CAP": "HARD_CAP",
        "STALE": "STALE",
        "DUST": "DUST",
    }

    def initialize(self) -> None:
        # Strategy1_Debug.initialize() calls self._emit(), so prepare an early buffer.
        self._research_ready = False
        self._research_early: list[dict[str, Any]] = []
        self._rq = None
        self._rstop = None
        self._rworker = None
        self._rfile = None
        self._rdropped = 0
        super().initialize()

        cfg = self.config
        self.research_enabled = self._env_bool(
            "STRATEGY1_RESEARCH", self._as_bool(getattr(cfg, "research_enabled", True))
        )
        self.research_every_n = max(1, self._env_int(
            "STRATEGY1_RESEARCH_EVERY_N", int(getattr(cfg, "research_every_n", 1))
        ))
        self.research_book_id = self._env_int(
            "STRATEGY1_RESEARCH_BOOK", int(getattr(cfg, "research_book_id", -1))
        )
        self.research_console = self._env_bool(
            "STRATEGY1_RESEARCH_CONSOLE", self._as_bool(getattr(cfg, "research_console", True))
        )
        self.research_jsonl = self._env_bool(
            "STRATEGY1_RESEARCH_JSONL", self._as_bool(getattr(cfg, "research_jsonl", True))
        )
        self.research_queue_size = max(256, self._env_int(
            "STRATEGY1_RESEARCH_QUEUE", int(getattr(cfg, "research_queue_size", 8192))
        ))
        env_dir = os.getenv("STRATEGY1_RESEARCH_DIR", "").strip()
        configured = str(getattr(cfg, "research_output_dir", "") or "")
        self.research_output_dir = env_dir or configured or os.path.join(
            self.output_dir, "strategy1_research"
        )

        self._rq = queue.Queue(maxsize=self.research_queue_size)
        self._rstop = threading.Event()
        if self.research_enabled and self.research_jsonl:
            try:
                os.makedirs(self.research_output_dir, exist_ok=True)
                path = os.path.join(
                    self.research_output_dir,
                    f"strategy1_research_agent_{self.uid}.jsonl",
                )
                self._rfile = open(path, "a", encoding="utf-8", buffering=1)
            except OSError as exc:
                print(f"[S1R_ERROR] stage=init_jsonl error={self._short(exc)}", flush=True)

        if self.research_enabled:
            self._rworker = threading.Thread(
                target=self._writer_loop,
                name=f"s1r-{getattr(self, 'uid', 'agent')}",
                daemon=True,
            )
            self._rworker.start()
            atexit.register(self._shutdown_research)

        self._research_ready = True
        for record in self._research_early:
            self._enqueue(record)
        self._research_early.clear()
        self._enqueue({
            "type": "RESEARCH_CONFIG",
            "agent_id": getattr(self, "uid", None),
            "wall_time_ns": time.time_ns(),
            "enabled": self.research_enabled,
            "every_n": self.research_every_n,
            "book_filter": self.research_book_id,
            "console": self.research_console,
            "jsonl": self.research_jsonl,
            "queue_size": self.research_queue_size,
            "output_dir": self.research_output_dir,
        })

    # Intercept every Strategy1_Debug event. No synchronous bt.logging call here.
    def _emit(self, event_type: str, force: bool = False, **payload: Any) -> None:
        if not getattr(self, "debug_enabled", True) and not force:
            return
        try:
            safe = self._json_safe(payload)
        except Exception:
            safe = payload
        record = {
            "type": event_type,
            "agent_id": getattr(self, "uid", None),
            "wall_time_ns": time.time_ns(),
            **safe,
        }
        if event_type in {"RUN_SUMMARY", "ERROR"}:
            record["research_queue_dropped"] = getattr(self, "_rdropped", 0)
            if self._rq is not None:
                record["research_queue_depth"] = self._rq.qsize()
        if not getattr(self, "_research_ready", False):
            self._research_early.append(record)
            return
        if getattr(self, "research_enabled", False):
            self._enqueue(record)

    def _enqueue(self, record: dict[str, Any]) -> None:
        if self._rq is None:
            return
        try:
            self._rq.put_nowait(record)
        except queue.Full:
            self._rdropped += 1
            if record.get("type") not in {
                "ERROR",
                "RUN_SUMMARY",
                "RESEARCH_CONFIG",
                "DEBUG_CONFIG",
            }:
                return
            try:
                self._rq.get_nowait()
                self._rq.task_done()
                self._rq.put_nowait(record)
            except queue.Empty:
                return
            except queue.Full:
                return

    def _writer_loop(self) -> None:
        assert self._rq is not None and self._rstop is not None
        while not self._rstop.is_set() or not self._rq.empty():
            try:
                record = self._rq.get(timeout=0.25)
            except queue.Empty:
                continue
            try:
                if self._rfile is not None:
                    self._rfile.write(json.dumps(record, separators=(",", ":"), sort_keys=True, default=str) + "\n")
                if self.research_console and self._console_allowed(record):
                    line = self._format_human(record)
                    if line:
                        print(line, flush=True)
            except Exception as exc:
                try:
                    print(f"[S1R_ERROR] stage=telemetry error={self._short(exc)}", flush=True)
                except Exception:
                    pass
            finally:
                self._rq.task_done()

    def _console_allowed(self, r: dict[str, Any]) -> bool:
        typ = str(r.get("type", ""))
        if typ in {"ERROR", "RUN_SUMMARY", "RESEARCH_CONFIG", "DEBUG_CONFIG", "ORDER_LIFECYCLE"}:
            return True
        tick = self._int(r.get("tick"))
        if tick is not None and tick != 1 and tick % self.research_every_n != 0:
            return False
        book = self._int(r.get("book_id"))
        if self.research_book_id >= 0 and book is not None:
            return book == self.research_book_id
        return True

    def _format_human(self, r: dict[str, Any]) -> str | None:
        typ = str(r.get("type", ""))
        if typ == "RESEARCH_CONFIG":
            return (f"[S1R_CONFIG] enabled={int(bool(r.get('enabled')))} every_n={r.get('every_n')} "
                    f"book={r.get('book_filter')} jsonl={int(bool(r.get('jsonl')))} "
                    f"queue={r.get('queue_size')} dir={self._short(r.get('output_dir'))}")
        if typ == "DEBUG_CONFIG":
            return (f"[S1R_CONFIG] debug_enabled={int(bool(r.get('enabled')))} "
                    f"debug_every_n={r.get('every_n')} debug_book={r.get('book_filter')}")
        if typ == "TIMING":
            return (f"[S1R_REQ] tick={r.get('tick')} sim_ts={r.get('timestamp')} "
                    f"instructions={r.get('instructions', 0)} notices={r.get('notices', 0)} "
                    f"update_ms={self._fmt(r.get('update_ms'))} respond_ms={self._fmt(r.get('respond_ms'))} "
                    f"report_ms={self._fmt(r.get('report_ms'))} total_ms={self._fmt(r.get('total_ms'))}")
        if typ == "DECISION":
            raw = str(r.get("reason", "NO_ACTION"))
            reason = self.REASON_ALIAS.get(raw, raw)
            action = str(r.get("action", "SKIP")).upper()
            inv = r.get("inventory") or {}
            common = (f"tick={r.get('tick')} book={r.get('book_id')} regime={self._short(r.get('regime'))} "
                      f"archetype={self._short(r.get('archetype'))} tier={self._short(r.get('tier'))} "
                      f"signal={self._fmt(r.get('signal'))} alpha={self._fmt(r.get('expected_alpha'))} "
                      f"min_alpha={self._fmt(r.get('min_expected_alpha'))} fill_bid={self._fmt(r.get('fill_buy'))} "
                      f"fill_ask={self._fmt(r.get('fill_sell'))} qty={self._fmt(r.get('quantity'))} "
                      f"exp_pnl={self._fmt(r.get('expected_realized_pnl'))} inv_base={self._fmt(inv.get('net_base'))} "
                      f"inv_band={self._short(inv.get('band'))} instructions={r.get('instructions', 0)}")
            if action == "SKIP":
                return f"[S1R_SKIP] {common} side=BOTH reason={reason} raw_reason={raw}"
            return (f"[S1R_QUOTE] {common} action={action} reason={reason} "
                    f"bid={self._fmt(r.get('bid_px'))} ask={self._fmt(r.get('ask_px'))} "
                    f"decision_ms={self._fmt(r.get('decision_ms'))}")
        if typ == "ORDER_LIFECYCLE":
            phase = str(r.get("phase", "UNKNOWN")).upper()
            book = r.get("book_id")
            if phase == "SUBMITTED":
                p = r.get("instruction") or {}
                return (f"[S1R_ORDER] tick={r.get('tick')} book={book} "
                        f"side={self._side(self._pick(p, 'direction', 'side'))} "
                        f"type={self._short(self._pick(p, 'orderType', 'order_type', 'type'))} "
                        f"price={self._fmt(self._pick(p, 'price', 'limitPrice', 'limit_price'))} "
                        f"qty={self._fmt(self._pick(p, 'quantity', 'qty', 'size'))} "
                        f"tif={self._short(self._pick(p, 'timeInForce', 'time_in_force', 'tif'))} "
                        f"client_id={self._short(self._pick(p, 'clientOrderId', 'client_order_id'))} index={r.get('instruction_index')}")
            e = r.get("event") or {}
            if "TRADE" in phase or "FILL" in phase:
                return (f"[S1R_FILL] tick={r.get('tick')} book={book} phase={phase} "
                        f"side={self._side(self._pick(e, 'direction', 'side'))} "
                        f"price={self._fmt(self._pick(e, 'price', 'tradePrice', 'trade_price'))} "
                        f"qty={self._fmt(self._pick(e, 'quantity', 'qty', 'size'))} "
                        f"client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))} "
                        f"net_before={self._fmt(r.get('net_before'))} net_after={self._fmt(r.get('net_after'))}")
            if "REJECT" in phase or "FAIL" in phase:
                return (f"[S1R_REJECT] tick={r.get('tick')} book={book} phase={phase} "
                        f"reason={self._short(self._pick(e, 'reason', 'message', 'status', 'error'), 240)} "
                        f"client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}")
            if "CANCEL" in phase or "EXPIRE" in phase:
                return (f"[S1R_CANCEL] tick={r.get('tick')} book={book} phase={phase} "
                        f"reason={self._short(self._pick(e, 'reason', 'message', 'status'))} "
                        f"client_id={self._short(self._pick(e, 'clientOrderId', 'client_order_id'))}")
            return f"[S1R_NOTICE] tick={r.get('tick')} book={book} phase={phase}"
        if typ == "RUN_SUMMARY":
            avg = r.get("average_latency_ms") or {}
            mx = r.get("max_latency_ms") or {}
            return (f"[S1R_SUMMARY] tick={r.get('tick')} responses={r.get('responses')} "
                    f"top_skips={self._counts(r.get('reason_counts') or {}, 8)} "
                    f"events={self._counts(r.get('event_counts') or {}, 8)} "
                    f"avg_total_ms={self._fmt(avg.get('total_ms'))} max_total_ms={self._fmt(mx.get('total_ms'))} "
                    f"queue_dropped={self._rdropped}")
        if typ == "ERROR":
            return (f"[S1R_ERROR] tick={r.get('tick')} stage={self._short(r.get('stage'))} "
                    f"type={self._short(r.get('error_type'))} error={self._short(r.get('error'), 400)}")
        return None

    def _shutdown_research(self) -> None:
        if self._rstop is not None:
            self._rstop.set()
        if self._rq is not None:
            deadline = time.time() + 1.5
            while self._rq.unfinished_tasks and time.time() < deadline:
                time.sleep(0.01)
        if self._rworker is not None and self._rworker.is_alive():
            self._rworker.join(timeout=0.5)
        if self._rfile is not None:
            try:
                self._rfile.flush(); self._rfile.close()
            except OSError:
                pass
            self._rfile = None

    @staticmethod
    def _pick(obj: Any, *names: str) -> Any:
        if obj is None:
            return None
        for name in names:
            if isinstance(obj, dict) and name in obj:
                return obj[name]
            if hasattr(obj, name):
                return getattr(obj, name)
        return None

    @staticmethod
    def _int(v: Any) -> int | None:
        try:
            return int(v) if v is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _fmt(v: Any) -> str:
        if v is None:
            return "-"
        if isinstance(v, bool):
            return "1" if v else "0"
        try:
            x = float(v)
        except (TypeError, ValueError):
            return str(v).replace(" ", "_")
        if abs(x) >= 1000:
            return f"{x:.3f}"
        if abs(x) >= 1:
            return f"{x:.6f}".rstrip("0").rstrip(".")
        return f"{x:.8f}".rstrip("0").rstrip(".") or "0"

    @staticmethod
    def _short(v: Any, n: int = 120) -> str:
        if v is None:
            return "-"
        return "_".join(str(v).replace("\n", " ").replace("\r", " ").split())[:n]

    @classmethod
    def _side(cls, v: Any) -> str:
        if v is None:
            return "-"
        s = str(v).upper()
        if "BUY" in s or s == "BID":
            return "BID"
        if "SELL" in s or s == "ASK":
            return "ASK"
        return cls._short(v)

    @staticmethod
    def _counts(d: dict[str, Any], n: int) -> str:
        try:
            items = sorted(((str(k), int(v)) for k, v in d.items()), key=lambda kv: (-kv[1], kv[0]))[:n]
            return ",".join(f"{k}:{v}" for k, v in items) or "-"
        except Exception:
            return "-"


if __name__ == "__main__":
    launch(Strategy1_Research)
