"""
Per-tick tap + top-miner axon probe utilities for subnet 79.

The miner agent writes a compressed state snapshot each tick; the sidecar
``scripts/run_top_miner_axon_monitor.py`` queries top UIDs and appends JSONL.
"""

from __future__ import annotations

import gzip
import json
import os
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

import bittensor as bt

from taos.im.protocol import MarketSimulationStateUpdate
from taos.im.protocol.instructions import (
    CancelOrdersInstruction,
    PlaceLimitOrderInstruction,
    PlaceMarketOrderInstruction,
)
from taos.im.protocol.models import TradeInfo

# Public Agents dashboard (see doc/dashboard/README.md).
DASHBOARD_BASE_URL = "https://dashboard.simulate.trading"
DASHBOARD_AGENTS_UID = "edy6vxytuud4wd"
DASHBOARD_ORG_ID = 2
DEFAULT_DASHBOARD_VALIDATOR_WALLET = (
    "5EWwdZB7qCCMaAso5Mzcks4UUcPxKYvpAj32t5Mg1v6HSxoF"
)

# Panels on the Agent page that mirror miner_gauges / agent_gauges in report.py.
GRAFANA_AGENT_PANELS = {
    "score": "miner_gauges{miner_gauge_name=\"score\"}",
    "kappa": "miner_gauges{miner_gauge_name=\"kappa\"}",
    "requests": "miner_gauges{miner_gauge_name=\"requests\"}",
    "call_time": "miner_gauges{miner_gauge_name=\"call_time\"}",
    "daily_maker_volume": "miner_gauges{miner_gauge_name=\"daily_maker_volume\"}",
    "daily_taker_volume": "miner_gauges{miner_gauge_name=\"daily_taker_volume\"}",
    "round_trip_volume": "miner_gauges{miner_gauge_name=\"round_trip_volume\"}",
    "realized_pnl": "agent_gauges{agent_gauge_name=\"realized_pnl\"}",
    "pnl": "agent_gauges{agent_gauge_name=\"pnl\"}",
}


def build_agent_dashboard_url(
    agent_id: int,
    wallet: str,
    netuid: int = 79,
    org_id: int = DASHBOARD_ORG_ID,
) -> str:
    """Deep link to the Agents dashboard for a miner UID."""
    return (
        f"{DASHBOARD_BASE_URL}/d/{DASHBOARD_AGENTS_UID}/agents"
        f"?orgId={org_id}"
        f"&var-agent_id={agent_id}"
        f"&var-wallet={wallet}"
        f"&var-netuid={netuid}"
    )


def resolve_dashboard_wallet(
    tap_validator_hotkey: str | None,
    fallback_wallet: str | None = None,
) -> str:
    """Prefer the validator hotkey from the live synapse; else configured default."""
    if tap_validator_hotkey:
        return tap_validator_hotkey
    return fallback_wallet or DEFAULT_DASHBOARD_VALIDATOR_WALLET


def dashboard_links_for_uids(
    uids: list[int],
    wallet: str,
    netuid: int = 79,
) -> dict[str, str]:
    """String UID keys (JSON object keys are always strings)."""
    return {
        str(uid): build_agent_dashboard_url(uid, wallet, netuid) for uid in uids
    }


def monitor_dir(output_dir: str) -> Path:
    path = Path(output_dir) / "top_miner_monitor"
    path.mkdir(parents=True, exist_ok=True)
    return path


def get_top_uids(
    netuid: int = 79,
    n: int = 5,
    exclude: tuple[int, ...] = (0,),
    network: str = "finney",
) -> list[int]:
    """Rank miners by on-chain incentive (excludes validator/benchmark UIDs)."""
    sub = bt.Subtensor(network=network)
    mg = sub.metagraph(netuid)
    import numpy as np

    incentive = np.array(mg.I)
    candidates = [
        uid
        for uid in range(mg.n)
        if uid not in exclude and incentive[uid] > 0
    ]
    candidates.sort(key=lambda u: incentive[u], reverse=True)
    return candidates[:n]


def _book_to_dict(book: Any) -> dict:
    if hasattr(book, "parse"):
        return book.parse().model_dump(mode="json")
    if hasattr(book, "model_dump"):
        return book.model_dump(mode="json")
    return dict(book)


def state_to_tap_payload(state: MarketSimulationStateUpdate) -> dict[str, Any]:
    """JSON-serializable snapshot for cross-process dendrite probes."""
    books: dict[int, dict] = {}
    if state.books:
        for book_id, book in state.books.items():
            books[int(book_id)] = _book_to_dict(book)

    accounts: dict[int, dict[int, dict]] = {}
    if state.accounts:
        for uid, per_book in state.accounts.items():
            uid = int(uid)
            accounts[uid] = {}
            for book_id, account in per_book.items():
                accounts[uid][int(book_id)] = (
                    account.model_dump(mode="json")
                    if hasattr(account, "model_dump")
                    else dict(account)
                )

    notices: dict[int, list] = {}
    if state.notices:
        for uid, evs in state.notices.items():
            uid = int(uid)
            notices[uid] = [
                ev.model_dump(mode="json") if hasattr(ev, "model_dump") else ev
                for ev in evs
            ]

    cfg = state.config.model_dump(mode="json") if state.config else None
    validator_hotkey = None
    if state.dendrite is not None:
        validator_hotkey = getattr(state.dendrite, "hotkey", None)

    return {
        "timestamp": state.timestamp,
        "version": state.version,
        "validator_hotkey": validator_hotkey,
        "books": books,
        "accounts": accounts,
        "notices": notices,
        "config": cfg,
    }


def extract_trades_for_uids(
    state: MarketSimulationStateUpdate,
    target_uids: set[int],
) -> dict[int, list[dict]]:
    """Trades on any book where target UID is maker or taker this tick."""
    out: dict[int, list[dict]] = {uid: [] for uid in target_uids}
    if not state.books:
        return out

    for book_id, book in state.books.items():
        events = book.events or []
        for event in events:
            if isinstance(event, TradeInfo):
                trade = event
            else:
                etype = getattr(event, "type", None)
                if etype not in ("t", "EVENT_TRADE", "ET"):
                    continue
                trade = event
            taker_raw = trade.taker_agent_id
            maker_raw = trade.maker_agent_id
            if taker_raw is None or maker_raw is None:
                continue
            taker = int(taker_raw)
            maker = int(maker_raw)
            for uid, role in ((taker, "taker"), (maker, "maker")):
                if uid in target_uids:
                    out[uid].append({
                        "book": int(book_id),
                        "role": role,
                        "side": int(trade.side),
                        "price": float(trade.price),
                        "qty": float(trade.quantity),
                        "trade_ts": int(trade.timestamp),
                        "taker_uid": taker,
                        "maker_uid": maker,
                    })
    return out


def summarize_instructions(instructions: list) -> dict[str, Any]:
    by_book: dict[int, dict[str, int]] = defaultdict(
        lambda: {"limit": 0, "market": 0, "cancel": 0, "close": 0, "other": 0}
    )
    samples: list[dict] = []
    for inst in instructions[:40]:
        book_id = getattr(inst, "bookId", None)
        name = type(inst).__name__
        bucket = "other"
        if isinstance(inst, PlaceLimitOrderInstruction):
            bucket = "limit"
        elif isinstance(inst, PlaceMarketOrderInstruction):
            bucket = "market"
        elif isinstance(inst, CancelOrdersInstruction):
            bucket = "cancel"
        elif "Close" in name:
            bucket = "close"
        if book_id is not None:
            by_book[int(book_id)][bucket] += 1
        if len(samples) < 12:
            row: dict[str, Any] = {"type": name, "book": book_id}
            if hasattr(inst, "price"):
                row["price"] = getattr(inst, "price", None)
            if hasattr(inst, "quantity"):
                row["qty"] = getattr(inst, "quantity", None)
            if hasattr(inst, "direction"):
                row["dir"] = str(getattr(inst, "direction", ""))
            samples.append(row)

    return {
        "total": len(instructions),
        "by_book": {k: dict(v) for k, v in by_book.items()},
        "samples": samples,
    }


def write_tick_tap(
    state: MarketSimulationStateUpdate,
    tick: int,
    output_dir: str,
    local_uid: int,
    top_n: int = 5,
) -> Path:
    """Write gzip JSON tap + meta for the monitor sidecar."""
    base = monitor_dir(output_dir)
    payload = state_to_tap_payload(state)
    payload["local_tick"] = tick
    payload["local_uid"] = local_uid
    payload["tap_wall_ts"] = time.time()

    gz_path = base / "latest_state.json.gz"
    meta_path = base / "latest_meta.json"
    seq_path = base / "tick_seq.txt"

    target_uids = get_top_uids(n=top_n)
    trades = extract_trades_for_uids(state, set(target_uids))
    dash_wallet = resolve_dashboard_wallet(payload.get("validator_hotkey"))
    meta = {
        "sim_ts": state.timestamp,
        "local_tick": tick,
        "local_uid": local_uid,
        "validator_hotkey": payload.get("validator_hotkey"),
        "dashboard_wallet": dash_wallet,
        "dashboard_links": dashboard_links_for_uids(target_uids, dash_wallet),
        "tap_wall_ts": payload["tap_wall_ts"],
        "top_uids": target_uids,
        "trades": trades,
        "book_count": len(state.books or {}),
    }

    raw = json.dumps(payload, default=str).encode("utf-8")
    tmp_gz = base / "latest_state.json.gz.tmp"
    with gzip.open(tmp_gz, "wb") as f:
        f.write(raw)
    os.replace(tmp_gz, gz_path)
    meta_path.write_text(json.dumps(meta, indent=2) + "\n")

    try:
        seq = int(seq_path.read_text().strip()) + 1 if seq_path.is_file() else 1
    except ValueError:
        seq = 1
    seq_path.write_text(str(seq))

    return base


def load_tap_payload(tap_dir: Path) -> dict[str, Any]:
    gz_path = tap_dir / "latest_state.json.gz"
    with gzip.open(gz_path, "rt", encoding="utf-8") as f:
        return json.load(f)


def build_synapse_for_uid(data: dict[str, Any], uid: int) -> MarketSimulationStateUpdate:
    """Build per-miner synapse like validator query service."""
    accounts = data.get("accounts") or {}
    notices = data.get("notices") or {}
    request = {
        "timestamp": data["timestamp"],
        "version": data.get("version"),
        "books": data.get("books"),
        "accounts": accounts,
        "notices": notices,
        "config": data.get("config"),
    }
    synapse = MarketSimulationStateUpdate.parse_dict(request)
    object.__setattr__(
        synapse,
        "accounts",
        {uid: accounts[uid]} if uid in accounts else {},
    )
    object.__setattr__(
        synapse,
        "notices",
        {uid: notices.get(uid, [])},
    )
    return synapse


def axon_record(mg: bt.Metagraph, uid: int) -> dict[str, Any]:
    ax = mg.axons[uid]
    incentive = float(mg.I[uid])
    emission = float(mg.emission[uid])
    trust = float(mg.T[uid]) if hasattr(mg, "T") else None
    hk = mg.hotkeys[uid]
    hotkey = hk if isinstance(hk, str) else hk.ss58_address
    return {
        "uid": uid,
        "hotkey": hotkey,
        "ip": ax.ip,
        "port": int(ax.port),
        "is_serving": bool(ax.is_serving),
        "incentive": round(incentive, 8),
        "emission": round(emission, 4),
        "trust": round(trust, 6) if trust is not None else None,
    }


def dendrite_summary(synapse: MarketSimulationStateUpdate) -> dict[str, Any]:
    d = synapse.dendrite
    summary: dict[str, Any] = {
        "status_code": getattr(d, "status_code", None),
        "status_message": getattr(d, "status_message", None),
        "process_time_s": getattr(d, "process_time", None),
        "is_timeout": bool(getattr(synapse, "is_timeout", False)),
        "is_failure": bool(getattr(synapse, "is_failure", False)),
        "is_blacklist": bool(getattr(synapse, "is_blacklist", False)),
    }
    response = getattr(synapse, "response", None)
    if response is None:
        summary["instructions"] = summarize_instructions([])
        return summary
    instructions = getattr(response, "instructions", None) or []
    summary["agent_id"] = getattr(response, "agent_id", None)
    summary["instructions"] = summarize_instructions(instructions)
    return summary
