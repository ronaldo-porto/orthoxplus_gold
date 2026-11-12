# SPDX-License-Identifier: MIT
"""v7.2 TakerV7: the taker core on the mainnet winners' target-inventory rule (Round 21; USER GO 11-12 02:4x JST); v7.1
added class 1 = Round 22 option A behind v7_classes ("0" = v7.0.1 behaviour, "0,1" = classes 0 and 1); v7.2 (Round 24,
USER 11-13) adds the maker leg behind v7_maker_classes ("none" = v7.1 behaviour, "1" or "1,2"): flat two-sided post-only
quotes on the maker classes' books, built here as postOnly GTT limit orders.

The agent class only.  The rule, the class map, the clamps and the pace guard live in taker_v7_core.py (stdlib only;
see its docstring for every label and source).  This class:

* is a FinanceSimulationAgent (taos/im/agents/__init__.py:381), the base Strategy1_Research_Simple reaches through
  DetailedTemplateAgent (agents/strategy/DetailedTemplateAgent.py:242) and RandomTakerAgent through GenTRXAgent ->
  FinanceAgent (taos/im/agents/__init__.py:1052); Strategy1_Research_Simple is never imported;
* builds the reply as both of them do: FinanceAgentResponse(agent_id=uid) + response.limit_order /
  response.cancel_orders (taos/im/protocol/response.py:105-199, :231-260) -> PlaceLimitOrderInstruction /
  CancelOrdersInstruction (taos/im/protocol/instructions.py:110-153, :176-196);
* replaces the base handle / update / report (STRUCTURAL speed, Round 21 S S4): the base update builds a debug text
  over every book and event and keeps state copies (taos/im/agents/__init__.py:760-895), the base report logs every
  instruction (:1025-1046).  Here: no events of the books, level 1 only, no per-book logging; the books and accounts
  are read from the lazy wire dicts (lazy_load=1, models.py:2743 / 2834);
* logs a header, the class layout and a compact summary every v7_log_every states through a background writer
  thread (JSON lines in logs/m1_taker_v7/), with the handler time p50 / p95 over those states;
* GC (MODEL, the A1.9.9.2 approach of research_direct_idle_gc.py): collect + freeze after init and after the first
  state; with a running event loop the automatic full pass is moved out of requests and run 50 ms after every
  v7_gc_every-th reply.
"""
from __future__ import annotations

import gc
import hashlib
import json
import os
import queue
import sys
import threading
import time
import traceback

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import bittensor as bt  # noqa: E402

from taos.common.agents import launch  # noqa: E402
from taos.im.agents import FinanceSimulationAgent  # noqa: E402
from taos.im.protocol import FinanceAgentResponse, MarketSimulationStateUpdate  # noqa: E402
from taos.im.protocol.models import LoanSettlementOption, OrderDirection, STP, TimeInForce  # noqa: E402

import taker_v7_core as core  # noqa: E402

_REPO = os.path.dirname(os.path.dirname(_HERE))
DEFAULT_LOG_DIR = os.path.join(_REPO, "logs", "m1_taker_v7")
IDLE_GC_DELAY_S = 0.05
GC_GEN2_OFF = 1_000_000


def _sha16(path: str) -> str:
    try:
        with open(path, "rb") as fh:
            return hashlib.sha256(fh.read()).hexdigest()[:16]
    except Exception:
        return ""


class _Writer:
    """JSON lines on a daemon thread: the request path only enqueues a dict."""

    def __init__(self, path: str):
        self.path = path
        self.q: queue.SimpleQueue = queue.SimpleQueue()
        self.n_put = 0
        self.n_done = 0
        self.t = threading.Thread(target=self._run, name="taker-v7-writer", daemon=True)
        self.t.start()

    def put(self, obj: dict) -> None:
        self.n_put += 1
        self.q.put(obj)

    def flush(self, timeout: float = 5.0) -> bool:
        """Wait (off the request path: tests / shutdown) until every queued line is written."""
        end = time.time() + timeout
        while self.n_done < self.n_put and time.time() < end:
            time.sleep(0.01)
        return self.n_done >= self.n_put

    def _run(self) -> None:
        while True:
            obj = self.q.get()
            try:
                line = json.dumps(obj, default=str, separators=(",", ":"))
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:
                pass
            self.n_done += 1


class TakerV7(FinanceSimulationAgent):
    """Pure class-0 taker (v7.0)."""

    def initialize(self):
        self.v7 = core.parse_params(self.config)          # raises on an unsupported value: the miner does not start
        self.core = core.TakerCore(self.uid, self.v7)
        log_dir = self.v7["log_dir"] or DEFAULT_LOG_DIR
        os.makedirs(log_dir, exist_ok=True)
        stamp = time.strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(log_dir, f"taker_v7_{self.uid}_{stamp}.jsonl")
        self._writer = _Writer(self.log_path)
        self._times: list = []
        self._n = 0
        self._errors = 0
        self._last_summary_ts = None
        self._gc_installed = False
        self._gc_handle = None
        self._gc_ms: list = []
        self._first_done = False
        self._writer.put({
            "kind": "header", "version": core.V7_VERSION, "core": core.CORE_VERSION, "uid": self.uid,
            "pid": os.getpid(), "python": sys.version.split()[0], "wall": time.strftime("%Y-%m-%d %H:%M:%S %z"),
            "params": {k: v for k, v in self.v7.items()},
            "history_anchor": self.v7.get("history_anchor") or "(not given)",
            "lazy_load": bool(getattr(self.config, "lazy_load", False)),
            "sha16": {"TakerV7.py": _sha16(os.path.join(_HERE, "TakerV7.py")),
                      "taker_v7_core.py": _sha16(os.path.join(_HERE, "taker_v7_core.py"))},
            "log_path": self.log_path,
        })
        bt.logging.info(f"[TakerV7] {core.V7_VERSION} uid={self.uid} params={self.v7} log={self.log_path}")
        gc.collect()
        gc.freeze()

    # ---- the request path ---------------------------------------------------------------------------------------

    def handle(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        t0 = time.perf_counter()
        try:
            self.update(state)
            response = self.respond(state)
        except Exception:
            response = FinanceAgentResponse(agent_id=self.uid)
            self._errors += 1
            if self._errors <= 20 or self._errors % 100 == 0:
                self._writer.put({"kind": "error", "n": self._errors, "ts": getattr(state, "timestamp", None),
                                  "trace": traceback.format_exc()[-4000:]})
        ms = (time.perf_counter() - t0) * 1000.0
        self._times.append(ms)
        self._n += 1
        if self._n % self.v7["log_every"] == 0:
            self._summary(state)
        if not self._first_done:
            self._first_done = True
            gc.collect()
            gc.freeze()
        self._idle_gc_schedule()
        return response

    def update(self, state: MarketSimulationStateUpdate) -> None:
        """Light update: the config and our notices only (no history, no event dispatch, no per-book text)."""
        self.simulation_config = state.config
        notices = state.notices or {}
        ev = notices.get(self.uid) if isinstance(notices, dict) else None
        if ev is None and isinstance(notices, dict):
            ev = notices.get(str(self.uid))
        self.events = ev or []

    def respond(self, state: MarketSimulationStateUpdate) -> FinanceAgentResponse:
        dendrite = getattr(state, "dendrite", None)
        hotkey = (getattr(dendrite, "hotkey", None) or "") if dendrite is not None else ""
        collect = (self._n + 1) % self.v7["log_every"] == 0
        actions, pairs = self.core.step(state.timestamp, hotkey, state.config, state.books, state.accounts,
                                        self.events, collect=collect)
        if self.core.layout_changes:
            for key, lay in self.core.layout_changes:
                self._writer.put({"kind": "layout", "ts": state.timestamp, "key": key,
                                  "ok": lay["ok"], "reason": lay["reason"], "source": lay["source"],
                                  "book_count": lay["book_count"], "c0": [min(lay["c0"]), max(lay["c0"])]
                                  if lay["c0"] else None, "price_decimals": lay["price_decimals"],
                                  "volume_decimals": lay["volume_decimals"], "min_order": lay["min_order"],
                                  "cap": self.core.cap_used, "classes": list(self.v7["classes"]),
                                  "c1_ok": lay.get("c1_ok"), "c1_reason": lay.get("c1_reason"),
                                  "c1": [min(lay["c1"]), max(lay["c1"])] if lay.get("c1") else None,
                                  "c1_grid": lay.get("c1_grid"),
                                  "maker_classes": list(self.v7["maker_classes"]),
                                  "c2_ok": lay.get("c2_ok"), "c2_reason": lay.get("c2_reason"),
                                  "c2": [min(lay["c2"]), max(lay["c2"])] if lay.get("c2") else None,
                                  "c2_grid": lay.get("c2_grid")})
            self.core.layout_changes.clear()
        if pairs is not None:
            self._pairs = pairs
            self._pair_flags = self.core.pair_flags      # v7.0.1 diagnostic: aligned with pairs
            self._pairs_c1 = self.core.pairs_c1           # v7.1: class-1 (inv, T) pairs
            self._mk_stats = self.core.mk_stats           # v7.2: the maker leg's picture
        response = FinanceAgentResponse(agent_id=self.uid)
        expiry = int(self.v7["mk_expiry_s"] * 1e9)
        for a in actions:
            if a[0] == core.ACT_QUOTE:
                _, b, side, qty, price = a
                response.limit_order(
                    book_id=b,
                    direction=OrderDirection.BUY if side == core.SIDE_BUY else OrderDirection.SELL,
                    quantity=qty,
                    price=price,
                    delay=0,
                    stp=STP.CANCEL_OLDEST,
                    postOnly=True,
                    timeInForce=TimeInForce.GTT,
                    expiryPeriod=expiry,
                    leverage=0.0,
                    settlement_option=LoanSettlementOption.NONE,
                )
            elif a[0] == core.ACT_TAKE:
                _, b, side, qty, price = a
                response.limit_order(
                    book_id=b,
                    direction=OrderDirection.BUY if side == core.SIDE_BUY else OrderDirection.SELL,
                    quantity=qty,
                    price=price,
                    delay=0,
                    stp=STP.CANCEL_OLDEST,
                    postOnly=False,
                    timeInForce=TimeInForce.IOC,
                    leverage=0.0,
                    settlement_option=LoanSettlementOption.NONE,
                )
            else:
                _, b, ids = a
                response.cancel_orders(book_id=b, order_ids=ids, delay=0)
        return response

    def report(self, state, response) -> None:
        """No per-state logging (the summary line carries the counts)."""
        return None

    # ---- summary + GC -------------------------------------------------------------------------------------------

    def _summary(self, state) -> None:
        c = self.core
        ts = list(self._times)
        self._times.clear()
        pairs = getattr(self, "_pairs", None) or []
        self._pairs = None
        flags = getattr(self, "_pair_flags", None)
        self._pair_flags = None
        p1 = getattr(self, "_pairs_c1", None) or []
        self._pairs_c1 = None
        d1 = [abs(t - i) for i, t in p1]
        mk_stats = getattr(self, "_mk_stats", None)
        self._mk_stats = None
        try:
            split = core.split_pairs(pairs, flags) if pairs else None   # v7.0.1 diagnostic, never raises here
        except Exception as exc:
            split = {"error": type(exc).__name__}
        rs = c.runs.get((getattr(getattr(state, "dendrite", None), "hotkey", None) or ""), None)
        pace = {}
        if rs is not None and c.layout and c.layout.get("ok"):
            used = [rs.pace.used(b, rs.clock) for b in c.layout["c0"]]
            tok = [rs.pace.tokens.get(b, rs.pace.depth) for b in c.layout["c0"]]
            pace = {"ledger_p50": core.pctl(used, 0.5), "ledger_max": max(used) if used else None,
                    "tokens_min": min(tok) if tok else None, "cap": rs.pace.cap, "clock_s": rs.clock / 1e9}
            if 1 in c.p["classes"] and c.layout.get("c1_ok"):
                used1 = [rs.pace.used(b, rs.clock) for b in c.layout["c1"]]
                tok1 = [rs.pace.tokens.get(b, rs.pace.depth) for b in c.layout["c1"]]
                pace.update(c1_ledger_p50=core.pctl(used1, 0.5), c1_ledger_max=max(used1) if used1 else None,
                            c1_tokens_min=min(tok1) if tok1 else None)
            for ci in c.p["maker_classes"]:                # v7.2: the maker classes' own rolling ledger
                if c.layout.get(f"c{ci}_ok"):
                    usedm = [rs.pace.used(b, rs.clock) for b in c.layout[f"c{ci}"]]
                    pace[f"mk_c{ci}_ledger_p50"] = core.pctl(usedm, 0.5)
                    pace[f"mk_c{ci}_ledger_max"] = max(usedm) if usedm else None
        d = [abs(t - i) for i, t in pairs]
        self._writer.put({
            "kind": "summary", "n": self._n, "ts": getattr(state, "timestamp", None),
            "sim": getattr(getattr(state, "config", None), "simulation_id", None),
            "handler_ms": {"p50": core.pctl(ts, 0.5), "p95": core.pctl(ts, 0.95), "max": max(ts) if ts else None,
                           "n": len(ts)},
            "counts": dict(c.c), "refusals": dict(c.refusals),
            "fills": {"taker": c.fills_taker, "maker": c.fills_maker, "notional": round(c.fill_notional, 2),
                      "qty_buy": round(c.fill_qty_buy, 4), "qty_sell": round(c.fill_qty_sell, 4)},
            "c0_corr_inv_target": core.corr(pairs), "c0_books": len(pairs),
            "c0_abs_dev_p50": core.pctl(d, 0.5), "c0_split": split,
            "c1_corr_inv_target": core.corr(p1) if p1 else None, "c1_books": len(p1),
            "c1_abs_dev_p50": core.pctl(d1, 0.5), "pace": pace, "errors": self._errors,
            "maker": mk_stats, "maker_fills": {"buy_notional": round(c.mk_buy_notional, 2),
                                               "sell_notional": round(c.mk_sell_notional, 2)},
            "gc_idle_ms_p50": core.pctl(self._gc_ms, 0.5), "gc_idle_n": len(self._gc_ms),
        })
        self._gc_ms = []
        c.reset_counters()

    def _idle_gc_schedule(self) -> None:
        every = self.v7["gc_every"]
        if every <= 0 or self._n % every:
            return
        try:
            import asyncio
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if not self._gc_installed:
            g0, g1, _ = gc.get_threshold()
            gc.set_threshold(g0, g1, GC_GEN2_OFF)
            self._gc_installed = True
        if self._gc_handle is not None:
            self._gc_handle.cancel()
        self._gc_handle = loop.call_later(IDLE_GC_DELAY_S, self._idle_gc)

    def _idle_gc(self) -> None:
        self._gc_handle = None
        t0 = time.perf_counter()
        try:
            gc.collect()
        except Exception:
            pass
        self._gc_ms.append((time.perf_counter() - t0) * 1000.0)


if __name__ == "__main__":
    """
    Local standalone run through the proxy:
    python TakerV7.py --port 8888 --agent_id 0 --params v7_k=0.0035 v7_l=0.55 v7_clip=7 v7_big=15
    """
    launch(TakerV7)
