"""v6.37 (research_v637_fast_path): F2's reply-time changes (scratchpad k106) behind ONE launcher switch.

F2 made the reply path ~20 % faster with every decision identical (k106, OBSERVED on its harness under load: handle p50
118.6 -> 92.5 ms; identity runs on a virtual clock, 415,844 + 220,514 instructions: every response and every persisted
session file byte-identical to v6.36).  Each of its changes sits in its own module behind that module's own in-code
switch -- "if <switch>: F2's code, else: the original code" -- and each switch is read where its code runs:

  taos/im/protocol/models.py                      _F2_FAST            LazyLevel serves .price / .quantity off the
                                                                      wire dict until its orders are read; Order /
                                                                      Balance / Loan / Fees by an exact replica of
                                                                      model_construct; LazyBook.events through each
                                                                      model's own validator (read in those methods,
                                                                      at every call)
  agents/strategy/research_direct_idle_gc.py      F2_REQUEST_GC_HOLD  automatic collection held off from a request's
                                                                      start to its idle pass (read at each request's
                                                                      begin and each idle pass)
  agents/strategy/research_direct_exit_ledger.py  _F2_LEDGER_INDEX    live_orders from a per-book index (read in
                                                                      live_orders, and by _v6_37_orders when a
                                                                      ledger is made)

The launcher's PARAMS reach the agent only in its constructor, after the miner imported all three modules (the protocol
before the agent is even loaded), so the switch cannot be an import-time constant.  It is the process's: the agent's
constructor, before any request, writes research_v637_fast_path into the three module switches (apply) and gives the
one exit ledger made earlier in initialize() the order dict the switch selects (ledger_orders).  From then on every path
reads the switch where it runs: on, F2's code; off, v6.36's code (the original builds, no hold, the plain dict and the
scan).  One agent per miner process (run_miner_multi.sh starts one pm2 process per UID), so the process's switch is the
agent's.  STRUCTURAL.

The two strategy modules always come from the agent's own tree (its agent path): one without its switch is not v6.37
and stops the agent before any switch is written.  The protocol is taos's, imported through the venv's editable install
-- on a host that runs several trees from one venv, the tree last installed by run_miner_multi.sh's `pip install -e .`,
which after a pm2 restart may be another tree's.  A protocol without F2's switch runs its original code whatever this
switch says (every decision the same, F2's protocol speed absent), so it is reported -- None in the result and one line
on stdout -- instead of stopping the agent.
"""
from __future__ import annotations

import importlib

V6_37_FAST_PATH_VERSION = "fast_path_v6_37"

# F2's three in-code switches as (module, name) -- each module's own (scratchpad k106/f2_changes.txt C1-C5)
SWITCHES = (
    ("taos.im.protocol.models", "_F2_FAST"),
    ("research_direct_idle_gc", "F2_REQUEST_GC_HOLD"),
    ("research_direct_exit_ledger", "_F2_LEDGER_INDEX"),
)
SHARED = frozenset({"taos.im.protocol.models"})       # may be another tree's (see above): reported, never fatal
LEDGER_MODULE = "research_direct_exit_ledger"


def apply(on) -> dict:
    """Writes ``on`` into each of F2's three module switches and returns {switch: value} as the modules hold them.

    A strategy module without its bool switch (code that is not F2's) raises before anything is written: a switch that
    reached two of the agent's three paths must not run as if it reached all three.  A shared protocol without it is
    left alone and reported as None (its original code runs either way)."""
    value = bool(on)
    modules = []
    for module_name, switch in SWITCHES:
        module = importlib.import_module(module_name)
        if isinstance(getattr(module, switch, None), bool):
            modules.append((module, switch))
        elif module_name in SHARED:
            modules.append((None, switch))
        else:
            raise RuntimeError(f"v6.37: {module_name} carries no switch {switch}")
    held = {}
    for module, switch in modules:
        if module is None:
            held[switch] = None
            print(f"[v6.37] fast path: taos.im.protocol.models carries no {switch} (another tree's taos?) -- the "
                  f"protocol runs its original code", flush=True)
            continue
        setattr(module, switch, value)
        held[switch] = getattr(module, switch)
    return held


def ledger_orders(orders):
    """The exit ledger's order dict as its module's switch selects it now -- F2's indexed dict (on) or v6.36's plain
    dict (off) -- holding the same rows in the same order; ``orders`` itself when it already is that type."""
    fresh = importlib.import_module(LEDGER_MODULE)._v6_37_orders()
    if type(orders) is type(fresh):
        return orders
    fresh.update(orders)
    return fresh
