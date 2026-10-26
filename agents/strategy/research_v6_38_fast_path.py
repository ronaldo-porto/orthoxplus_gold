"""v6.38 (research_v638_fast_path): U2 stage A's reply-time changes (scratchpad k115 probes P5 / P1 / P3, built in k123)
behind ONE launcher switch, every decision as v6.37's.

k115 measured each change as a patch on v6.37 FROZEN_1 (identity runs on a virtual clock, calm 415,844 + busy 220,514
instructions: every response and every persisted session byte-identical; interleaved A/B on real clocks: P5 -13.0 ms,
P1 -7.1 ms, P3 -1.7 ms a request).  Each sits behind "if <switch>: the new code, else: the original code", read where
its code runs:

  P5  taos/im/protocol/models.py   _V638_TRADES_ONLY    LazyBook.events validates the trade events only; order /
                                                        cancellation events stay the wire dicts (every reader on the
                                                        agent's path skips non-trades; Book.trades / .orders /
                                                        .cancellations / parse() get every event as a model).  The
                                                        protocol is shared by every agent the venv's taos serves, so
                                                        the module ships it OFF and this agent turns it on (apply).
  P1  Strategy1_Research_Simple    research_v638_fast_path   the A1.9.0.2 resting-exit observer stops after its ledger
                                                        sweep: its per-book shadow-exit pass is measurement only (its
                                                        state feeds its own lean-sampled rows and the stats dict).
  P3  Strategy1_Research_Simple    research_v638_fast_path   the four trade walkers (the v6.2 making mirror, the v6.2.11
                                                        alpha mirror, the v6.2.14 last-taker side, the v6.33 deep
                                                        layer) read one parse of each book's events a request
                                                        (trades_of) instead of each parsing every event: the four
                                                        parsers are byte-identical, the parse is trade_dict's, and the
                                                        mirrors re-read the parsed dicts through their own parser
                                                        (a parsed dict parses to an equal dict).

The launcher's PARAMS reach the agent only in its constructor, after the miner imported the protocol, so the protocol's
switch cannot be an import-time constant: the constructor writes research_v638_fast_path into it (apply) before any
request; Simple reads its own attribute.  One agent per miner process (run_miner_multi.sh starts one pm2 process per
UID), so the process's switch is the agent's.  A protocol without the switch (another tree's taos through the venv's
editable install) runs its original code whatever this switch says -- every decision the same, P5's speed absent -- so
it is reported (None in the result and one line on stdout) instead of stopping the agent.  STRUCTURAL.
"""
from __future__ import annotations

import importlib
import sys

from research_v62_making_mirror import trade_dict
from research_v6214_touch_life import last_taker_side as _v6214_last_taker_side

V6_38_FAST_PATH_VERSION = "fast_path_v6_38"

# the protocol's in-code switch as (module, name); the protocol may be another tree's: reported, never fatal
SWITCHES = (
    ("taos.im.protocol.models", "_V638_TRADES_ONLY"),
)
SHARED = frozenset({"taos.im.protocol.models"})

# P3: one book's trades parsed once a request -- keyed by the event list itself (held, so its id cannot be reused while
# it is cached), emptied at the start of each request's update (trades_reset); bounded in case a request never resets
_TRADES: dict[int, tuple[object, list]] = {}
_MAX_LISTS = 4096
# v6.41 D (research_v641_speed, written by research_v6_41_speed.apply): read the protocol's recorded trade candidates
_V641_SCAN_ONCE = False


def apply(on) -> dict:
    """Writes ``on`` into the protocol's switch and returns {switch: value} as the module holds it (None: a protocol
    without the switch, whose original code runs either way)."""
    value = bool(on)
    held = {}
    for module_name, switch in SWITCHES:
        module = importlib.import_module(module_name)
        if isinstance(getattr(module, switch, None), bool):
            setattr(module, switch, value)
            held[switch] = getattr(module, switch)
        elif module_name in SHARED:
            held[switch] = None
            print(f"[v6.38] fast path: {module_name} carries no {switch} (another tree's taos?) -- the protocol runs "
                  f"its original code", flush=True)
        else:
            raise RuntimeError(f"v6.38: {module_name} carries no switch {switch}")
    return held


def trades_reset() -> None:
    """Empties the parse cache: the start of a request's update, so the cache holds one request's event lists."""
    _TRADES.clear()
    models = sys.modules.get("taos.im.protocol.models")         # v6.41 D: the protocol's side tables with it
    reset = getattr(models, "v641_reset", None)
    if reset is not None:
        reset()


def trades_of(events) -> list:
    """The trades of one book's event list, in order: trade_dict of each event, Nones dropped -- what each of the four
    walkers' own loop builds -- parsed once per list object.  The same list goes to every caller: none writes into it
    or into a trade dict.  An event the parser raises on raises here, as in a walker's own loop; nothing is cached."""
    if not events:
        return []
    hit = _TRADES.get(id(events))
    if hit is not None and hit[0] is events:
        return hit[1]
    src = events
    if _V641_SCAN_ONCE:                                         # v6.41 D: only the elements trade_dict can parse
        models = sys.modules.get("taos.im.protocol.models")
        cand_of = getattr(models, "v641_trade_candidates", None)
        cand = cand_of(events) if cand_of is not None else None
        if cand is not None:
            src = cand
    out = []
    for ev in src:
        t = trade_dict(ev)
        if t is not None:
            out.append(t)
    if len(_TRADES) >= _MAX_LISTS:
        _TRADES.clear()
    _TRADES[id(events)] = (events, out)
    return out


def feed(events):
    """A mirror's input for one book: the shared parse (each parsed dict re-reads through the mirror's own parser as an
    equal dict), or the book's raw events when the parse raises -- the mirror's own loop then meets the same event
    after the same prefix, exactly as it would have on its own."""
    try:
        return trades_of(events)
    except Exception:
        return events


def last_taker_side(events):
    """v6.2.14's last_taker_side (the taker side of the book's latest print) from the shared parse: the last trade of
    the forward parse is the first its backward scan meets.  Anything but a list / tuple, or a forward parse that
    raises (an event trade_dict cannot read), goes to the original backward scan, which decides as on its own."""
    if not isinstance(events, (list, tuple)):
        return _v6214_last_taker_side(events)
    try:
        trades = trades_of(events)
    except Exception:
        return _v6214_last_taker_side(events)
    return int(trades[-1]["s"]) if trades else None
