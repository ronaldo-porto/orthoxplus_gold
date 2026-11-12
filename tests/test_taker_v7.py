# SPDX-License-Identifier: MIT
"""v7.0 TakerV7 tests (the launch preflight of run_taker_v7.sh runs this file and refuses on any failure).

The core (agents/strategy/taker_v7_core.py) is stdlib only and is tested directly.  The agent class TakerV7 is tested
against the real taos / bittensor when they import (the miner's interpreter), else against minimal stand-in modules
that record what the agent asks of FinanceAgentResponse; the stand-ins are removed again afterwards.
"""
from __future__ import annotations

import gzip
import importlib.util
import json
import math
import os
import random
import re
import sys
import time
import types
from enum import IntEnum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STRAT = ROOT / "agents" / "strategy"
if str(STRAT) not in sys.path:
    sys.path.insert(0, str(STRAT))

import taker_v7_core as core  # noqa: E402

UID = 68
GRID = {"price_decimals": 2, "volume_decimals": 4, "min_order": 0.25}
BI, QI = 83.3333, 25000.0           # class-0-like initial balances (W = QI + BI x mid ~ 50k at mid 300)


def P(**kw):
    d = {}
    d.update(kw)
    return core.parse_params(d)


def acc(base_total=BI, base_init=BI, quote_init=QI, base_free=None, quote_free=None, fee=0.0003, v=0.0, orders=()):
    bf = base_total if base_free is None else base_free
    qf = QI if quote_free is None else quote_free
    return {"i": UID, "b": 0, "bb": {"c": "BASE", "t": base_total, "f": bf, "r": 0.0, "i": base_init},
            "qb": {"c": "QUOTE", "t": qf, "f": qf, "r": 0.0, "i": quote_init},
            "o": [{"y": "o", "i": i, "c": None, "t": 1, "q": 1.0, "s": 0, "p": 299.0, "l": 0.0} for i in orders],
            "l": {}, "f": {"v": 0.0, "m": 0.0, "t": fee}, "bl": 0.0, "ql": 0.0, "bc": 0.0, "qc": 0.0, "v": v}


def book(bid, ask, f, q=50.0, bid_id=0):
    return {"i": bid_id, "r": 0.4, "b": [{"p": bid, "q": q, "o": []}] if bid is not None else [],
            "a": [{"p": ask, "q": q, "o": []}] if ask is not None else [], "e": [], "f": f}


def cfg(n=256, classes=None, **kw):
    c = {"simulation_id": "sim_a", "book_count": n, "priceDecimals": 2, "volumeDecimals": 4, "min_order_size": 0.25,
         "miner_wealth": 50000.0, "duration": 86400 * 10 ** 9, "time_unit": "ns"}
    if classes is not None:
        c["classes"] = classes
    c.update(kw)
    return c


def w_at(mid, bi=BI, qi=QI):
    return qi + bi * mid


def f_for_target(t_units, mid, k=0.0035, bi=BI, qi=QI):
    """The fundamental that puts the target at t_units (inside the L clip)."""
    w = w_at(mid, bi, qi)
    gap = t_units * mid / (w * k)
    return mid * (1.0 + gap / 1e4)


# ---- parameters ----------------------------------------------------------------------------------------------------

def test_params_defaults_are_the_copied_constants():
    p = P()
    assert p["k"] == 0.0035 and p["l"] == 0.55 and p["clip"] == 7.0 and p["big"] == 15.0
    assert p["classes"] == (0,) and p["pace"] is True and p["cap"] == 0.0 and p["cap_mult"] == 10.0
    assert p["c1_k"] == 0.036 and p["c1_l"] == 0.50 and p["c1_clip_notional"] == 3500.0     # v7.1 COPIED (uid 68)


def _parse_kwargs_like(text):
    """taos ParseKwargs (taos/common/config/__init__.py:32-42): key=value, float when it parses."""
    ns = types.SimpleNamespace()
    for value in text.split():
        key, value = value.split("=")
        try:
            value = float(value)
        except (TypeError, ValueError):
            pass
        setattr(ns, key, value)
    return ns


def _launcher_params():
    text = (ROOT / "run_taker_v7.sh").read_text()
    m = re.search(r'^PARAMS="(.*?)"$', text, re.S | re.M)
    assert m, "run_taker_v7.sh has no PARAMS block"
    s = m.group(1).replace("\\\n", " ")
    return (s.replace("${LOG_DIR}", "/tmp/x").replace("${HISTORY_ANCHOR}", "established")
            .replace("${V7_CLASSES}", "0").replace("${V7_MAKER_CLASSES}", "1"))


def test_params_from_the_launcher_parse_to_the_defaults():
    env = os.environ.get("V7_LAUNCH_PARAMS")
    for text in filter(None, [env, _launcher_params()]):
        ns = _parse_kwargs_like(text)
        p = core.parse_params(ns)
        assert p["k"] == 0.005 and p["l"] == 0.55 and p["clip"] == 7.0 and p["big"] == 15.0   # v7.2 launch slope
        assert p["classes"] == ((0,) if text is not env else p["classes"]) and p["classes"] in ((0,), (0, 1))
        assert p["maker_classes"] == ((1,) if text is not env else p["maker_classes"])
        assert p["maker_classes"] in ((), (1,), (1, 2)) and not set(p["maker_classes"]) & set(p["classes"])
        assert p["mk_notional"] == 1800.0 and p["mk_guard_bp"] == 10.0 and p["mk_band_clips"] == 1.0
        assert p["mk_pull"] is True and p["mk_expiry_s"] == 60.0
        assert p["c1_k"] == 0.036 and p["c1_l"] == 0.5 and p["c1_clip_notional"] == 3500.0
        assert p["pace"] is True and p["cap"] == 0.0
        assert getattr(ns, "lazy_load") == 1.0 and getattr(ns, "history_len") == 0.0
        assert p["history_anchor"]


def test_params_reject_unsupported_values():
    import pytest
    for bad in ({"v7_classes": "0+1"}, {"v7_classes": 1.0}, {"v7_big": 5}, {"v7_pace": 2}, {"v7_k": 0},
                {"v7_l": 1.5}, {"v7_clip": "x"}, {"v7_classes": "0,2"}, {"v7_classes": "0,1,2"},
                {"v7_classes": "1,0"}, {"v7_classes": "1"}, {"v7_c1_k": 0}, {"v7_c1_l": 2},
                {"v7_c1_clip_notional": -1}):
        with pytest.raises(ValueError):
            core.parse_params(bad)
    assert core.parse_params({"v7_classes": 0.0})["classes"] == (0,)      # ParseKwargs turns "0" into 0.0
    assert core.parse_params({"v7_pace": 0.0})["pace"] is False
    assert core.parse_params({"v7_classes": "0,1"})["classes"] == (0, 1)
    assert core.parse_params({"v7_classes": " 0, 1 "})["classes"] == (0, 1)


# ---- target, trigger, side, size -------------------------------------------------------------------------------------

def test_target_math_and_clip():
    assert math.isclose(core.target_units(100.0, 50000.0, 300.0, 0.0035, 0.55), 0.35 * 50000 / 300)
    assert math.isclose(core.target_units(200.0, 50000.0, 300.0, 0.0035, 0.55), 0.55 * 50000 / 300)
    assert math.isclose(core.target_units(-300.0, 50000.0, 300.0, 0.0035, 0.55), -0.55 * 50000 / 300)
    assert core.target_units(0.0, 50000.0, 300.0, 0.0035, 0.55) == 0.0


def _decide(bid, ask, f, base_total=BI, quote_free=QI, base_free=None, fee=0.0003, p=None, grid=GRID, wealth=None,
            base_init=BI, quote_init=QI):
    bf = base_total if base_free is None else base_free
    return core.decide(bid, ask, f, base_total, base_init, quote_init, bf, quote_free, fee, p or P(), grid, wealth)


def test_decide_trigger_side_clip_big():
    bid, ask = 299.99, 300.01
    mid = 300.0
    r, o, inv, t = _decide(bid, ask, f_for_target(5.0, mid))
    assert r == "in_band" and o is None and abs(t - 5.0) < 1e-6 and inv == 0.0
    r, o, _, t = _decide(bid, ask, f_for_target(10.0, mid))
    assert r == "send" and o == (core.SIDE_BUY, 7.0, 300.01)
    r, o, _, _ = _decide(bid, ask, f_for_target(20.0, mid))
    assert r == "send" and o == (core.SIDE_BUY, 15.0, 300.01)
    r, o, _, _ = _decide(bid, ask, f_for_target(-10.0, mid))
    assert r == "send" and o == (core.SIDE_SELL, 7.0, 299.99)
    r, o, _, _ = _decide(bid, ask, f_for_target(-16.0, mid))
    assert r == "send" and o == (core.SIDE_SELL, 15.0, 299.99)
    # inventory above a zero target -> sell; |d| exactly CLIP triggers
    r, o, inv, _ = _decide(bid, ask, mid, base_total=BI + 12.0)
    assert r == "send" and o[0] == core.SIDE_SELL and o[1] == 7.0 and abs(inv - 12.0) < 1e-9
    r, o, _, _ = _decide(bid, ask, mid, base_total=BI - 7.001)
    assert r == "send" and o == (core.SIDE_BUY, 7.0, 300.01)
    r, o, _, _ = _decide(bid, ask, mid, base_total=BI - 6.99)
    assert r == "in_band"
    # at the limit: T saturates at L x W / mid whatever the gap
    r, o, inv, t = _decide(bid, ask, mid * 1.05, base_total=BI + 0.55 * w_at(mid) / mid)
    assert abs(t - 0.55 * w_at(mid) / mid) < 1e-6 and r == "in_band"


def test_decide_wealth_source():
    bid, ask = 299.99, 300.01
    # no quote initial -> W falls back to miner_wealth (the per-book figure)
    r, o, _, t = _decide(bid, ask, 301.0, quote_init=None, wealth=50000.0)
    assert r == "send" and math.isclose(t, 0.0035 * (1e4 * 1.0 / 300.0) * 50000.0 / 300.0, rel_tol=1e-9)
    r, o, _, _ = _decide(bid, ask, 301.0, quote_init=None, wealth=None)
    assert r == "no_wealth" and o is None
    r, o, _, _ = _decide(bid, ask, 301.0, base_init=None)
    assert r == "no_initial" and o is None


# ---- clamps ------------------------------------------------------------------------------------------------------------

def test_buy_clamped_to_free_quote_with_fee_margin():
    bid, ask, fee = 299.99, 300.01, 0.0003
    p = P()
    qf = 3.33339 * ask * (1 + fee + p["fee_margin"])
    r, o, _, _ = _decide(bid, ask, f_for_target(20.0, 300.0), quote_free=qf, fee=fee)
    assert r == "send" and o[0] == core.SIDE_BUY and o[1] == 3.3333
    assert o[1] * ask * (1 + fee) <= qf
    r, o, _, _ = _decide(bid, ask, f_for_target(20.0, 300.0), quote_free=0.2 * ask)
    assert r == "below_min" and o is None
    r, o, _, _ = _decide(bid, ask, f_for_target(20.0, 300.0), quote_free=0.0)
    assert r == "no_quote"


def test_sell_clamped_to_free_base_never_short():
    bid, ask = 299.99, 300.01
    r, o, _, _ = _decide(bid, ask, f_for_target(-20.0, 300.0), base_total=BI, base_free=2.56789)
    assert r == "send" and o == (core.SIDE_SELL, 2.5678, 299.99)
    r, o, _, _ = _decide(bid, ask, f_for_target(-20.0, 300.0), base_total=BI, base_free=0.1)
    assert r == "below_min"
    r, o, _, _ = _decide(bid, ask, f_for_target(-20.0, 300.0), base_total=BI, base_free=0.0)
    assert r == "no_base"


def test_volume_and_price_decimals():
    grid = {"price_decimals": 4, "volume_decimals": 2, "min_order": 0.2396}
    r, o, _, _ = _decide(313.0001, 313.0101, f_for_target(9.0, 313.0051), grid=grid)
    assert r == "send" and o == (core.SIDE_BUY, 7.0, 313.0101)
    assert core.floor_to(2.56789, 2) == 2.56 and core.floor_to(7.0, 4) == 7.0 and core.floor_to(0.29999999, 4) == 0.2999


# ---- no f, crossed, one-sided ------------------------------------------------------------------------------------------

def test_no_order_without_f_or_with_a_bad_book():
    for f in (None, 0.0, -1.0, float("nan"), float("inf"), "x", True):
        r, o, _, _ = _decide(299.99, 300.01, f, base_total=BI - 50)
        assert r == "no_f" and o is None, f
    r, o, _, _ = _decide(300.02, 300.01, 310.0)
    assert r == "crossed" and o is None
    r, o, _, _ = _decide(300.01, 300.01, 310.0)
    assert r == "crossed" and o is None
    r, o, _, _ = _decide(None, 300.01, 310.0)
    assert r == "one_sided" and o is None
    r, o, _, _ = _decide(299.99, None, 310.0)
    assert r == "one_sided" and o is None


# ---- class map ---------------------------------------------------------------------------------------------------------

def _classes(sizes, pd=(2, 2, 4), vd=(4, 4, 4), mins=(0.25, 2.5364, 0.2396)):
    out, first = [], 0
    for i, n in enumerate(sizes):
        out.append({"name": f"simulation_{i}", "books": list(range(first, first + n)), "priceDecimals": pd[i],
                    "volumeDecimals": vd[i], "config": {"min_order_size": mins[i], "priceDecimals": pd[i],
                                                        "volumeDecimals": vd[i]}})
        first += n
    return out


def test_class_map_256_and_128_layouts():
    lay = core.class_layout(cfg(256), 256)
    assert lay["ok"] and lay["c0"] == frozenset(range(0, 128)) and lay["c1"] == frozenset(range(128, 192))
    assert lay["c2"] == frozenset(range(192, 256)) and lay["source"].startswith("layout_only")
    lay = core.class_layout(cfg(128), 128)
    assert lay["ok"] and lay["c0"] == frozenset(range(0, 64)) and lay["c1"] == frozenset(range(64, 96))
    assert lay["c2"] == frozenset(range(96, 128))
    for n in (64, 200, 255, 512, None):
        lay = core.class_layout(cfg(n), n)
        assert not lay["ok"] and lay["reason"].startswith("layout_unknown"), n


def test_class_map_reads_the_validator_class_list():
    lay = core.class_layout(cfg(256, classes=_classes((128, 64, 64), pd=(3, 2, 4))), 256)
    assert lay["ok"] and lay["source"] == "classes" and lay["price_decimals"] == 3 and lay["min_order"] == 0.25
    lay = core.class_layout(cfg(128, classes=_classes((64, 32, 32))), 128)
    assert lay["ok"] and lay["c0"] == frozenset(range(64))
    # a class list that disagrees with the layout fails closed
    lay = core.class_layout(cfg(256, classes=_classes((64, 96, 96))), 256)
    assert not lay["ok"] and lay["reason"].startswith("classes_mismatch")
    lay = core.class_layout(cfg(256, classes="garbage"), 256)
    assert lay["ok"] and lay["source"] == "layout_only_unreadable"


# ---- one state ---------------------------------------------------------------------------------------------------------

def _state(n=256, need=12.0, orders=None, classes=None, mid=300.0):
    """Every book wants a buy of ``need`` units (T = need, inv 0); ``orders`` = {book: [ids]}."""
    orders = orders or {}
    books, accounts = {}, {}
    for b in range(n):
        books[b] = book(mid - 0.01, mid + 0.01, f_for_target(need, mid))
        a = acc(orders=orders.get(b, ()))
        a["b"] = b
        accounts[b] = a
    return cfg(n, classes=classes), books, {UID: accounts}


def test_step_trades_class0_only_one_instruction_per_book():
    for n, c0 in ((256, range(0, 128)), (128, range(0, 64))):
        core_ = core.TakerCore(UID, P())
        config, books, accounts = _state(n)
        actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
        took = [a[1] for a in actions if a[0] == core.ACT_TAKE]
        assert sorted(took) == list(c0)
        assert len(set(a[1] for a in actions)) == len(actions)              # one instruction per book
        assert all(a[2] == core.SIDE_BUY and a[3] == 7.0 and a[4] == 300.01 for a in actions)
    core_ = core.TakerCore(UID, P())
    config, books, accounts = _state(200)
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert actions == [] and core_.c.get("layout_off") == 1
    # two validators on two layouts alternate: each layout is computed and logged once
    core_ = core.TakerCore(UID, P())
    s256, s128 = _state(256), _state(128)
    for i in range(6):
        c, b, a = s256 if i % 2 == 0 else s128
        core_.step((i + 1) * 10 ** 9, "v256" if i % 2 == 0 else "v128", c, b, a, [])
    assert len(core_.layout_changes) == 2


def test_startup_cleanup_cancels_every_class_and_skips_the_take_there():
    core_ = core.TakerCore(UID, P())
    config, books, accounts = _state(256, orders={5: [11, 12], 130: [21], 250: [31, 32, 33]})
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    cancels = {a[1]: a[2] for a in actions if a[0] == core.ACT_CANCEL}
    assert cancels == {5: [11, 12], 130: [21], 250: [31, 32, 33]}
    takes = {a[1] for a in actions if a[0] == core.ACT_TAKE}
    assert 5 not in takes and 4 in takes and 130 not in takes and 250 not in takes
    assert len(set(a[1] for a in actions)) == len(actions)
    # the next state, the orders gone: book 5 takes, nothing is cancelled
    config, books, accounts = _state(256)
    actions, _ = core_.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in actions if a[0] == core.ACT_CANCEL]
    assert 5 in {a[1] for a in actions if a[0] == core.ACT_TAKE}


def test_inherited_class1_class2_positions_are_left_alone():
    core_ = core.TakerCore(UID, P())
    config, books, accounts = _state(256)
    for b in range(128, 256):
        accounts[UID][b]["bb"]["t"] = BI + 40.0                          # an inherited long on every c1 / c2 book
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert all(a[1] < 128 for a in actions)


# ---- pace guard --------------------------------------------------------------------------------------------------------

def test_pace_bucket():
    pc = core.Pace(500_000.0)
    assert math.isclose(pc.depth, 500_000.0 * 10_800 / 86_400) and math.isclose(pc.rate, 500_000.0 / 86_400)
    s = 10 ** 9
    assert pc.allow(3, 4500.0, 0) == "ok"
    pc.spend(3, 60_000.0, 0)
    assert pc.allow(3, 4500.0, 0) == "bucket"
    assert pc.allow(3, 4500.0, 100 * s) == "bucket"                      # +578.7 -> 3,078.7 tokens
    assert pc.allow(3, 4500.0, 400 * s) == "ok"                          # +2,314.8 more -> 5,393.5
    assert pc.allow(4, 4500.0, 0) == "ok"                                # books are independent


def test_pace_ledger_and_venue_floor():
    s = 10 ** 9
    pc2 = core.Pace(500_000.0)
    t = 0
    for i in range(80):
        pc2.spend(9, 6000.0, t)
        t += 1000 * s
    # 480,000 inside the window (80 fills in 80,000 s), + 4,500 <= cap, + 25,000 > cap
    assert pc2.used(9, t) == 480_000.0
    assert pc2.allow(9, 4500.0, t) == "ok"
    assert pc2.allow(9, 25_000.0, t) == "ledger"
    # the window rolls: 86,400 s after the first fill it leaves the ledger
    assert pc2.used(9, 86_400 * s + 1) == 474_000.0
    # the venue's own traded volume floors the ledger
    pc3 = core.Pace(500_000.0)
    assert pc3.allow(1, 4500.0, 0, venue_volume=499_000.0) == "ledger"
    assert pc3.allow(1, 4500.0, 0, venue_volume=None) == "ok"


def test_fills_from_notices_spend_tokens_and_are_counted_once():
    core_ = core.TakerCore(UID, P())
    config, books, accounts = _state(256, need=20.0)               # 15-unit orders
    big = {"y": "ET", "b": 3, "i": 99, "Ta": UID, "Ma": 1234, "Ti": 1, "Mi": 2, "Tf": 0.0, "Mf": 0.0, "s": 0,
           "q": 200.0, "p": 300.0, "t": 1}
    maker = dict(big, i=100, b=4, Ta=555, Ma=UID, s=1, q=1.0)
    other = dict(big, i=101, b=5, Ta=555, Ma=556)
    refusal = {"y": "ERDPOL", "b": 6, "o": None, "c": None, "s": 0, "q": 7.0, "u": False,
               "m": "CONTRACT_VIOLATION", "p": 300.01, "t": 1}
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [big, big, maker, other, refusal])
    takes = {a[1] for a in actions if a[0] == core.ACT_TAKE}
    assert 3 not in takes and 4 in takes                                 # 2,500 tokens left < 15 x 300.01
    assert core_.c.get("paced_bucket") == 1 and core_.c.get("fill_dup") == 1
    assert core_.fills_taker == 1 and core_.fills_maker == 1
    assert math.isclose(core_.fill_notional, 60_000.0 + 300.0) and core_.fill_qty_buy == 201.0
    assert core_.refusals == {"CONTRACT_VIOLATION": 1}
    # object notices (the validated TradeEvent model) read the same way
    ev = types.SimpleNamespace(**dict(big, i=7, b=8))
    core_.step(2 * 10 ** 9, "hk", config, books, accounts, [ev])
    assert core_.runs["hk"].pace.sums[8] == 60_000.0


def test_venue_capped_book_is_not_traded():
    core_ = core.TakerCore(UID, P())
    config, books, accounts = _state(256)
    accounts[UID][9]["v"] = 500_000.0
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert 9 not in {a[1] for a in actions} and core_.c.get("paced_ledger") == 1


def test_pace_off_and_cap_sources():
    core_ = core.TakerCore(UID, P(v7_pace=0))
    config, books, accounts = _state(256)
    accounts[UID][9]["v"] = 600_000.0
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert 9 in {a[1] for a in actions}
    assert core.TakerCore(UID, P()).cap_for(cfg()) == 500_000.0
    assert core.TakerCore(UID, P(v7_cap=300_000)).cap_for(cfg()) == 300_000.0
    assert core.TakerCore(UID, P()).cap_for(cfg(miner_wealth=None)) == core.CAP_DEFAULT


def test_pace_clock_across_a_simulation_seam_and_per_validator():
    rs = core.RunState(500_000.0)
    rs.advance(5 * 10 ** 9, "a")
    rs.advance(7 * 10 ** 9, "a")
    assert rs.clock == 2 * 10 ** 9
    rs.advance(10 ** 9, "b")                                             # a new simulation: one step
    assert rs.clock == 3 * 10 ** 9
    rs.advance(10 ** 9, "b")                                             # the same state again: no time
    assert rs.clock == 3 * 10 ** 9
    assert rs.new_trade((3, 1)) and not rs.new_trade((3, 1))
    rs.advance(2 * 10 ** 9, "c")                                         # a new simulation forgets the trade ids
    assert rs.new_trade((3, 1))
    core_ = core.TakerCore(UID, P())
    config, books, accounts = _state(256)
    core_.step(10 ** 9, "v1", config, books, accounts, [])
    core_.step(10 ** 9, "v2", config, books, accounts, [])
    assert set(core_.runs) == {"v1", "v2"}


def test_corr_summary_helper():
    assert core.corr([(1, 2), (2, 4), (3, 6)]) == 1.0
    assert core.corr([(1, 1)]) is None
    assert core.pctl([3, 1, 2], 0.5) == 2


# ---- the agent class -----------------------------------------------------------------------------------------------

def _real_taos():
    try:
        import bittensor  # noqa: F401
        from taos.im.protocol import FinanceAgentResponse  # noqa: F401
        return True
    except Exception:
        return False


class _Rec:
    """Stand-in FinanceAgentResponse: records the calls."""

    def __init__(self, agent_id):
        self.agent_id = agent_id
        self.instructions = []

    def limit_order(self, **kw):
        self.instructions.append(("limit", kw))

    def cancel_orders(self, **kw):
        self.instructions.append(("cancel", kw))


def _stub_modules():
    class OrderDirection(IntEnum):
        BUY = 0
        SELL = 1

    class STP(IntEnum):
        NO_STP = 0
        CANCEL_OLDEST = 1

    class TimeInForce(IntEnum):
        GTC = 0
        GTT = 1
        IOC = 2
        FOK = 3

    class LoanSettlementOption(IntEnum):
        NONE = -2
        FIFO = -1

    class FinanceSimulationAgent:
        pass

    log = types.SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None,
                                warning=lambda *a, **k: None, error=lambda *a, **k: None)
    mods = {
        "bittensor": types.SimpleNamespace(logging=log),
        "taos": types.ModuleType("taos"),
        "taos.common": types.ModuleType("taos.common"),
        "taos.common.agents": types.SimpleNamespace(launch=lambda cls: None),
        "taos.im": types.ModuleType("taos.im"),
        "taos.im.agents": types.SimpleNamespace(FinanceSimulationAgent=FinanceSimulationAgent),
        "taos.im.protocol": types.SimpleNamespace(FinanceAgentResponse=_Rec, MarketSimulationStateUpdate=object),
        "taos.im.protocol.models": types.SimpleNamespace(OrderDirection=OrderDirection, STP=STP,
                                                         TimeInForce=TimeInForce,
                                                         LoanSettlementOption=LoanSettlementOption),
    }
    return mods


def _load_agent_class(stubbed):
    saved = {}
    if stubbed:
        for name, mod in _stub_modules().items():
            saved[name] = sys.modules.get(name)
            sys.modules[name] = mod
    try:
        spec = importlib.util.spec_from_file_location("TakerV7_under_test", STRAT / "TakerV7.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod
    finally:
        if stubbed:
            for name, old in saved.items():
                if old is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = old


def _agent(mod, tmp_path, **params):
    agent = mod.TakerV7.__new__(mod.TakerV7)
    agent.uid = UID
    base = {"v7_log_dir": str(tmp_path), "v7_log_every": 2, "lazy_load": 1.0}
    base.update(params)
    agent.config = types.SimpleNamespace(**base)
    agent.initialize()
    return agent


def _st(config, books, accounts, notices=None, ts=10 ** 9, hotkey="hk"):
    return types.SimpleNamespace(timestamp=ts, config=config, books=books, accounts=accounts,
                                 notices={UID: notices or []}, dendrite=types.SimpleNamespace(hotkey=hotkey))


def test_agent_builds_ioc_limit_orders_one_per_book(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path)
    config, books, accounts = _state(256, orders={5: [11, 12]})
    resp = agent.handle(_st(config, books, accounts))
    if real:
        from taos.im.protocol.models import TimeInForce, LoanSettlementOption
        ins = resp.instructions
        limits = [i for i in ins if i.type == "PLACE_ORDER_LIMIT"]
        cancels = [i for i in ins if i.type == "CANCEL_ORDERS"]
        assert len(limits) == 127 and len(cancels) == 1 and cancels[0].bookId == 5
        assert all(i.timeInForce == TimeInForce.IOC and i.postOnly is False and i.leverage == 0.0 and i.delay == 0
                   and i.settleFlag == LoanSettlementOption.NONE and int(i.direction) == 0 for i in limits)
        assert len({i.bookId for i in ins}) == len(ins)
        assert resp.agent_id == UID
    else:
        ins = resp.instructions
        limits = [kw for kind, kw in ins if kind == "limit"]
        cancels = [kw for kind, kw in ins if kind == "cancel"]
        assert len(limits) == 127 and len(cancels) == 1 and cancels[0]["book_id"] == 5
        assert cancels[0]["order_ids"] == [11, 12]
        assert all(int(kw["timeInForce"]) == 2 and kw["postOnly"] is False and kw["leverage"] == 0.0
                   and kw["delay"] == 0 and int(kw["settlement_option"]) == -2 and int(kw["direction"]) == 0
                   and kw["quantity"] == 7.0 and kw["price"] == 300.01 for kw in limits)
        books_seen = [kw["book_id"] for _, kw in ins]
        assert len(set(books_seen)) == len(books_seen) and max(books_seen) < 128
    # the summary line arrives through the writer thread after log_every states
    agent.handle(_st(config, books, accounts, ts=2 * 10 ** 9))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    kinds = [x["kind"] for x in lines]
    assert kinds[0] == "header" and "layout" in kinds and "summary" in kinds
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["handler_ms"]["n"] == 2 and summ["c0_books"] == 127 and summ["errors"] == 0   # book 5 is busy


def test_agent_survives_a_bad_state(tmp_path):
    mod = _load_agent_class(stubbed=not _real_taos())
    agent = _agent(mod, tmp_path)
    bad = types.SimpleNamespace(timestamp=1, config=None, books=None, accounts=None, notices=None, dendrite=None)
    resp = agent.handle(bad)
    assert resp.instructions == [] and agent._errors == 0
    worse = types.SimpleNamespace(timestamp=1)                           # no fields at all -> caught, empty reply
    resp = agent.handle(worse)
    assert resp.instructions == [] and agent._errors == 1
    assert agent._writer.flush()


def test_agent_refuses_unsupported_params(tmp_path):
    import pytest
    mod = _load_agent_class(stubbed=not _real_taos())
    with pytest.raises(ValueError):
        _agent(mod, tmp_path, v7_classes="0+2")


# ---- timing on a 256-book state built from a recorded one -------------------------------------------------------

FIXTURE = ROOT / "tests" / "data" / "taker_v7_state256.json.gz"


def _recorded_state(seed=20261112):
    """The fixture's 256 books (21 levels a side, recorded fv) as wire dicts with synthetic events at the recorded
    counts; accounts with class-0-like initial balances and a random inventory so a share of the books trade."""
    fx = json.load(gzip.open(FIXTURE, "rt", encoding="utf-8"))
    rng = random.Random(seed)
    books, accs = {}, {}
    for k, b in fx["books"].items():
        bid = int(k)
        lv = lambda rows: [{"p": p, "q": q, "o": []} for p, q in rows]
        ev = [{"y": "o", "i": 900000 + j, "c": None, "t": fx["timestamp"] - j, "q": 1.0, "s": j % 2,
               "p": b["b"][0][0], "l": 0.0} for j in range(b["ne"])]
        books[bid] = {"i": bid, "r": 0.4, "b": lv(b["b"]), "a": lv(b["a"]), "e": ev, "f": b["f"]}
        mid = 0.5 * (b["b"][0][0] + b["a"][0][0])
        bi, qi = 25000.0 / mid, 25000.0
        inv = rng.uniform(-60.0, 60.0)
        a = acc(base_total=bi + inv, base_init=bi, quote_init=qi, base_free=max(0.0, bi + inv),
                quote_free=qi - inv * mid)
        a["b"] = bid
        accs[bid] = a
    return fx, books, {UID: accs}


def test_handler_timing_on_a_recorded_256_book_state():
    fx, books, accounts = _recorded_state()
    config = cfg(256)
    core_ = core.TakerCore(UID, P())
    times, sends = [], []
    ts = fx["timestamp"]
    for i in range(300):
        t0 = time.perf_counter()
        actions, _ = core_.step(ts + i * 10 ** 9, "hk", config, books, accounts, [], collect=(i % 60 == 59))
        times.append((time.perf_counter() - t0) * 1000.0)
        sends.append(len(actions))
    p50, p95 = core.pctl(times, 0.5), core.pctl(times, 0.95)
    print(f"TIMING core.step 256 books (128 class-0): p50 {p50:.3f} ms p95 {p95:.3f} ms max {max(times):.3f} ms "
          f"instructions/state p50 {core.pctl(sends, 0.5)}")
    assert p95 < 50.0
    real = _real_taos()
    if real:
        from taos.im.protocol.models import LazyAccounts, LazyBooks
        import tempfile
        mod = _load_agent_class(stubbed=False)
        with tempfile.TemporaryDirectory() as d:
            agent = _agent(mod, Path(d), v7_log_every=1000)
            ht = []
            for i in range(200):
                st = _st(config, LazyBooks(books), LazyAccounts(accounts), ts=ts + i * 10 ** 9)
                t0 = time.perf_counter()
                agent.handle(st)
                ht.append((time.perf_counter() - t0) * 1000.0)
            assert agent._writer.flush()
            print(f"TIMING TakerV7.handle (real taos, lazy wire dicts): p50 {core.pctl(ht, 0.5):.3f} ms "
                  f"p95 {core.pctl(ht, 0.95):.3f} ms")
            assert core.pctl(ht, 0.95) < 50.0


# ---- v7.0.1: the summary split by the cap check (diagnostic only) --------------------------------------------------

def test_v701_split_flags_mark_capped_books_and_change_no_decision():
    config, books, accounts = _state(256)
    accounts[UID][9]["v"] = 500_000.0                                   # at the cap: even a CLIP order is refused
    accounts[UID][10]["v"] = 500_000.0 - 7 * 300.0 - 1.0                # one CLIP order at mid still fits: free
    a_plain, none = core.TakerCore(UID, P()).step(10 ** 9, "hk", config, books, accounts, [])
    assert none is None
    core_ = core.TakerCore(UID, P())
    a_diag, pairs = core_.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    assert a_diag == a_plain                                             # the diagnostic changes no decision
    took = {a[1] for a in a_diag if a[0] == core.ACT_TAKE}
    assert 9 not in took and 10 in took and core_.c.get("paced_ledger") == 1
    flags = core_.pair_flags
    assert len(pairs) == len(flags) == 128 and flags[9] is True and flags[10] is False and flags.count(True) == 1
    split = core.split_pairs(pairs, flags)
    assert split["free"]["n"] == 127 and split["capped"]["n"] == 1
    assert math.isclose(split["capped"]["dev_p50"], 12.0, abs_tol=1e-6) and split["free"]["corr"] is None
    # pace off: nothing is capped
    core_off = core.TakerCore(UID, P(v7_pace=0))
    _, pairs_off = core_off.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    assert len(pairs_off) == 128 and core_off.pair_flags.count(True) == 0


def test_v701_split_pairs_helper():
    pairs = [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0), (5.0, -4.0)]
    s = core.split_pairs(pairs, [False, False, False, True])
    assert s["free"]["n"] == 3 and s["free"]["corr"] == 1.0 and s["capped"]["n"] == 1 and s["capped"]["corr"] is None
    assert s["free"]["dev_p50"] == 1.0 and math.isclose(s["free"]["t_sd"], math.sqrt(2.0 / 3.0))
    assert core.split_pairs(pairs, [False])["error"] == "misaligned"
    assert core.split_pairs(pairs, None)["error"] == "misaligned"
    s2 = core.split_pairs(pairs, [None, False, False, True])            # unknown -> in neither half
    assert s2["free"]["n"] == 2 and s2["capped"]["n"] == 1


def test_v701_agent_summary_carries_the_split(tmp_path):
    mod = _load_agent_class(stubbed=not _real_taos())
    agent = _agent(mod, tmp_path)
    config, books, accounts = _state(256)
    accounts[UID][9]["v"] = 500_000.0
    agent.handle(_st(config, books, accounts))
    agent.handle(_st(config, books, accounts, ts=2 * 10 ** 9))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    assert lines[0]["kind"] == "header" and lines[0]["version"] == core.V7_VERSION
    summ = [x for x in lines if x["kind"] == "summary"][0]
    sp = summ["c0_split"]
    assert summ["c0_books"] == 128 and summ["errors"] == 0
    assert sp["free"]["n"] == 127 and sp["capped"]["n"] == 1
    assert summ["c0_corr_inv_target"] is None or isinstance(summ["c0_corr_inv_target"], float)


# ---- v7.1: class 1 = Round 22 option A behind v7_classes --------------------------------------------------------------

C1CFG = lambda: cfg(256, classes=_classes((128, 64, 64)))


def test_v71_class_layout_reads_the_class1_grid_and_fails_closed_alone():
    lay = core.class_layout(C1CFG(), 256)
    assert lay["ok"] and lay["c1_ok"] and lay["c1"] == frozenset(range(128, 192))
    assert lay["c1_grid"] == {"price_decimals": 2, "volume_decimals": 4, "min_order": 2.5364}
    assert lay["min_order"] == 0.25                                       # class 0's grid is unchanged
    lay = core.class_layout(cfg(128, classes=_classes((64, 32, 32))), 128)
    assert lay["c1_ok"] and lay["c1"] == frozenset(range(64, 96))
    # no class list: class 0 trades on the layout, class 1 stays off
    lay = core.class_layout(cfg(256), 256)
    assert lay["ok"] and not lay["c1_ok"] and lay["c1_reason"] == "c1_class_list_absent"
    # a class-1 entry that disagrees with the layout: class 1 off, class 0 on
    lay = core.class_layout(cfg(256, classes=_classes((128, 32, 96))), 256)
    assert lay["ok"] and not lay["c1_ok"] and lay["c1_reason"].startswith("c1_classes_mismatch")
    # the cache key sees a class-1 grid change
    a = core.layout_key(C1CFG(), 256)
    b = core.layout_key(cfg(256, classes=_classes((128, 64, 64), mins=(0.25, 3.0, 0.2396))), 256)
    assert a != b


def test_v71_decide_class1_rule_sizes_in_notional():
    g1 = {"price_decimals": 2, "volume_decimals": 4, "min_order": 2.5364}
    mid = 29.6
    bi, qi = 25000.0 / mid, 25000.0
    w = qi + bi * mid
    gap = 20.0                                                            # 0.036 x 20 = 0.72 -> clipped at 0.50
    f = mid * (1.0 + gap / 1e4)
    r, order, inv, t = core.decide(29.59, 29.61, f, bi, bi, qi, bi, qi, 0.0003, P(), g1, None, rule=(0.036, 0.5, 3500.0))
    assert r == "send" and math.isclose(t, 0.5 * w / mid) and inv == 0.0
    side, qty, price = order
    assert side == core.SIDE_BUY and price == 29.61 and qty == core.floor_to(3500.0 / mid, 4)
    # inside one clip: no order
    f_small = mid * (1.0 + (100.0 / (0.036 * w / mid)) / 1e4)              # T1 = 100 units < 118.24-unit clip
    r, order, _, t = core.decide(29.59, 29.61, f_small, bi, bi, qi, bi, qi, 0.0003, P(), g1, None,
                                 rule=(0.036, 0.5, 3500.0))
    assert r == "in_band" and order is None and 99.0 < t < 101.0
    # a clip below the class-1 minimum order is not sent
    r, order, _, _ = core.decide(29.59, 29.61, f, bi, bi, qi, bi, qi, 0.0003, P(), g1, None, rule=(0.036, 0.5, 50.0))
    assert r == "below_min" and order is None
    # rule None is exactly the class-0 rule
    a = core.decide(299.99, 300.01, f_for_target(12.0, 300.0), BI, BI, QI, BI, QI, 0.0003, P(), GRID, None)
    b = core.decide(299.99, 300.01, f_for_target(12.0, 300.0), BI, BI, QI, BI, QI, 0.0003, P(), GRID, None, rule=None)
    assert a == b and a[0] == "send" and a[1][1] == 7.0


def test_v71_step_trades_class0_and_class1_one_instruction_per_book():
    config, books, accounts = _state(256, classes=_classes((128, 64, 64)), orders={130: [5]})
    core_ = core.TakerCore(UID, P(v7_classes="0,1"))
    actions, pairs = core_.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    takes = {a[1]: a for a in actions if a[0] == core.ACT_TAKE}
    cancels = [a for a in actions if a[0] == core.ACT_CANCEL]
    assert len({a[1] for a in actions}) == len(actions)                   # one instruction per book
    assert sorted(b for b in takes if b < 128) == list(range(128))
    assert all(takes[b][3] == 7.0 for b in range(128))                     # class 0: 7-unit clip
    c1 = sorted(b for b in takes if 128 <= b < 192)
    assert c1 == [b for b in range(128, 192) if b != 130]                  # 130 is the cleanup book
    assert [a[1] for a in cancels] == [130]
    assert all(takes[b][2] == core.SIDE_BUY and takes[b][3] == core.floor_to(3500.0 / 300.0, 4) and takes[b][4] == 300.01
               for b in c1)
    assert not any(b >= 192 for b in takes)                                # class 2 stays off
    assert len(pairs) == 128 and len(core_.pairs_c1) == 63
    assert core_.c.get("c1_send_buy") == 63 and core_.c.get("send_buy") == 128


def test_v71_class1_off_without_a_class_list_and_with_classes_0():
    config, books, accounts = _state(256)                                  # no class list
    a01, _ = core.TakerCore(UID, P(v7_classes="0,1")).step(10 ** 9, "hk", config, books, accounts, [])
    c = core.TakerCore(UID, P(v7_classes="0,1"))
    c.step(10 ** 9, "hk", config, books, accounts, [])
    a0, _ = core.TakerCore(UID, P()).step(10 ** 9, "hk", config, books, accounts, [])
    assert a01 == a0 and c.c.get("c1_off") == 1
    config, books, accounts = _state(256, classes=_classes((128, 64, 64)))
    c0only = core.TakerCore(UID, P())
    a0, _ = c0only.step(10 ** 9, "hk", config, books, accounts, [])
    assert all(a[1] < 128 for a in a0) and not any(k.startswith("c1_") for k in c0only.c)


def test_v71_class0_decisions_unchanged_by_class1():
    fx, books, accounts = _recorded_state()
    config = C1CFG()
    rng = random.Random(5)
    c0, c01 = core.TakerCore(UID, P()), core.TakerCore(UID, P(v7_classes="0,1"))
    ts = fx["timestamp"]
    n1 = 0
    for i in range(120):
        for bk in books.values():
            bk["f"] = bk["f"] * (1.0 + rng.uniform(-0.002, 0.002))
        a0, _ = c0.step(ts + i * 10 ** 9, "hk", config, books, accounts, [])
        a01, _ = c01.step(ts + i * 10 ** 9, "hk", config, books, accounts, [])
        assert [a for a in a01 if a[1] < 128] == a0
        assert not any(a[1] >= 192 for a in a01)
        n1 += sum(1 for a in a01 if 128 <= a[1] < 192)
    assert n1 > 0


def test_v71_pace_state_restarts_when_the_book_count_changes():
    core_ = core.TakerCore(UID, P(v7_classes="0,1"))
    config, books, accounts = _state(128, classes=_classes((64, 32, 32)))
    fill = {"y": "ET", "b": 70, "i": 1, "Ta": UID, "Ma": 9, "s": 0, "q": 100.0, "p": 300.0, "t": 1}
    core_.step(10 ** 9, "hk", config, books, accounts, [fill])
    assert core_.runs["hk"].pace.sums[70] == 30_000.0
    core_.step(2 * 10 ** 9, "hk", config, books, accounts, [])           # same layout: kept
    assert core_.runs["hk"].pace.sums[70] == 30_000.0
    config, books, accounts = _state(256, classes=_classes((128, 64, 64)))
    core_.step(3 * 10 ** 9, "hk", config, books, accounts, [])           # 128 -> 256: book 70 is class 0 now
    assert 70 not in core_.runs["hk"].pace.sums and core_.c.get("pace_reset_layout") == 1


def test_v71_agent_summary_and_layout_carry_class1(tmp_path):
    mod = _load_agent_class(stubbed=not _real_taos())
    agent = _agent(mod, tmp_path, v7_classes="0,1")
    config, books, accounts = _state(256, classes=_classes((128, 64, 64)))
    agent.handle(_st(config, books, accounts))
    agent.handle(_st(config, books, accounts, ts=2 * 10 ** 9))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    assert lines[0]["version"] == core.V7_VERSION and lines[0]["params"]["classes"] == [0, 1]
    lay = [x for x in lines if x["kind"] == "layout"][0]
    assert lay["c1_ok"] is True and lay["c1"] == [128, 191] and lay["c1_grid"]["min_order"] == 2.5364
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["errors"] == 0 and summ["c1_books"] == 64 and summ["c0_books"] == 128
    assert summ["counts"].get("c1_send_buy") == 128                       # 64 books x 2 states (no fills in the stub)
    assert "c1_ledger_p50" in summ["pace"]


def test_v71_handler_timing_with_class1_on():
    fx, books, accounts = _recorded_state()
    config = C1CFG()
    core_ = core.TakerCore(UID, P(v7_classes="0,1"))
    times = []
    ts = fx["timestamp"]
    for i in range(300):
        t0 = time.perf_counter()
        core_.step(ts + i * 10 ** 9, "hk", config, books, accounts, [], collect=(i % 60 == 59))
        times.append((time.perf_counter() - t0) * 1000.0)
    p50, p95 = core.pctl(times, 0.5), core.pctl(times, 0.95)
    print(f"TIMING core.step 256 books (128 class-0 + 64 class-1): p50 {p50:.3f} ms p95 {p95:.3f} ms")
    assert p95 < 50.0


# ---- v7.2: the maker leg (Round 24) ------------------------------------------------------------------------------------

MKCLS = lambda: _classes((128, 64, 64))
STEP_CFG = dict(fp_update_period=30 * 10 ** 9 + 1, publish_interval=10 ** 9)   # K = 30 states per fundamental step
C1_MID = 9.0                                                                  # class-1-like price: 1,800 notional = 200 units


def _mk_state(orders=None, f_c1=None, inv_c1=0.0, n=256, classes=True, bid=8.99, ask=9.01, **cfg_kw):
    """Class 0 as _state (every book wants a 12-unit buy); class 1 / class 2 books at ~9.00 with f = mid unless given.
    ``orders`` = {book: [(id, side, price, qty)]} resting orders of ours."""
    orders = orders or {}
    config, books, accounts = _state(n, classes=MKCLS() if classes else None)
    config.update(STEP_CFG)
    config.update(cfg_kw)
    bi, qi = 25000.0 / C1_MID, 25000.0
    for b in range(128, n):
        books[b] = book(bid, ask, C1_MID if f_c1 is None else f_c1)
        a = acc(base_total=bi + inv_c1, base_init=bi, quote_init=qi, base_free=bi + inv_c1, quote_free=qi)
        a["b"] = b
        accounts[UID][b] = a
    for b, rows in orders.items():
        accounts[UID][b]["o"] = [{"y": "o", "i": i, "c": None, "t": 1, "q": q, "s": s, "p": px, "l": 0.0}
                                 for i, s, px, q in rows]
    return config, books, accounts


def test_v72_params_maker_classes_and_bounds():
    import pytest
    p = P()
    assert p["maker_classes"] == () and p["mk_notional"] == 1800.0 and p["mk_guard_bp"] == 10.0
    assert p["mk_band_clips"] == 1.0 and p["mk_pull"] is True and p["mk_expiry_s"] == 60.0 and p["k"] == 0.0035
    assert P(v7_maker_classes=1.0)["maker_classes"] == (1,)                # ParseKwargs turns "1" into 1.0
    assert P(v7_maker_classes="1,2")["maker_classes"] == (1, 2)
    assert P(v7_maker_classes=" 1, 2 ")["maker_classes"] == (1, 2)
    assert P(v7_maker_classes="none")["maker_classes"] == () and P(v7_maker_classes="off")["maker_classes"] == ()
    assert P(v7_mk_pull=0.0)["mk_pull"] is False
    for bad in ({"v7_maker_classes": 0.0}, {"v7_maker_classes": "0,1"}, {"v7_maker_classes": 2.0},
                {"v7_maker_classes": "2,1"}, {"v7_maker_classes": "1,2,3"},
                {"v7_maker_classes": 1.0, "v7_classes": "0,1"},            # class 1 taken AND made
                {"v7_mk_notional": 0}, {"v7_mk_guard_bp": -1}, {"v7_mk_band_clips": 0}, {"v7_mk_pull": 2},
                {"v7_mk_expiry_s": 0}):
        with pytest.raises(ValueError):
            core.parse_params(bad)


def test_v72_class_layout_reads_the_class2_grid():
    lay = core.class_layout(cfg(256, classes=MKCLS()), 256)
    assert lay["c1_ok"] and lay["c1_grid"] == {"price_decimals": 2, "volume_decimals": 4, "min_order": 2.5364}
    assert lay["c2_ok"] and lay["c2_grid"] == {"price_decimals": 4, "volume_decimals": 4, "min_order": 0.2396}
    lay = core.class_layout(cfg(256), 256)
    assert lay["ok"] and not lay["c2_ok"] and lay["c2_reason"] == "c2_class_list_absent"
    assert not lay["c1_ok"] and lay["c1_reason"] == "c1_class_list_absent"
    lay = core.class_layout(cfg(256, classes=MKCLS()[:2]), 256)
    assert lay["c1_ok"] and not lay["c2_ok"] and lay["c2_reason"] == "c2_class_list_absent"
    k1 = core.layout_key(cfg(256, classes=MKCLS()), 256)
    k2 = core.layout_key(cfg(256, classes=_classes((128, 64, 64), mins=(0.25, 2.5364, 0.5))), 256)
    assert k1 != k2


def test_v72_step_phase_grid():
    c = cfg(256, **STEP_CFG)
    assert core.step_phase(0, c) == (30, 0) and core.step_phase(29 * 10 ** 9, c) == (30, 29)
    assert core.step_phase(30 * 10 ** 9, c) == (30, 0) and core.step_phase(61 * 10 ** 9 + 5, c) == (30, 1)
    assert core.step_phase(10 ** 9, cfg(256, fp_update_period=30 * 10 ** 9, publish_interval=10 ** 9)) == (30, 1)
    assert core.step_phase(10 ** 9, cfg(256)) is None
    assert core.step_phase(10 ** 9, cfg(256, fp_update_period=7, publish_interval=10 ** 9)) is None
    assert core.step_phase(None, c) is None and core.step_phase(-1, c) is None


def test_v72_maker_quotes_both_sides_at_the_touch_and_class0_still_takes():
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
    config, books, accounts = _mk_state()
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    takes = [a for a in actions if a[0] == core.ACT_TAKE]
    quotes = [a for a in actions if a[0] == core.ACT_QUOTE]
    assert sorted(a[1] for a in takes) == list(range(128))                # class 0 = the taker, as before
    assert sorted(set(a[1] for a in quotes)) == list(range(128, 192))     # class 1 = the maker leg; class 2 untouched
    assert len(quotes) == 128 and not [a for a in actions if a[0] == core.ACT_CANCEL]
    for b in range(128, 192):
        mine = {a[2]: a for a in quotes if a[1] == b}
        assert mine[core.SIDE_BUY][4] == 8.99 and mine[core.SIDE_SELL][4] == 9.01
        assert mine[core.SIDE_BUY][3] == 200.0 and mine[core.SIDE_SELL][3] == 200.0     # 1,800 / 9.00
    assert core_.c["mk_quote_bid"] == 64 and core_.c["mk_quote_ask"] == 64
    # the taker loop never touches a maker class, also with an inherited inventory there
    core_ = core.TakerCore(UID, P(v7_maker_classes="1,2"))
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert sorted(set(a[1] for a in actions if a[0] == core.ACT_QUOTE)) == list(range(128, 256))
    assert all(a[1] < 128 for a in actions if a[0] == core.ACT_TAKE)


def test_v72_maker_keeps_matching_orders_and_replaces_the_rest():
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
    orders = {
        130: [(11, 0, 8.99, 200.0), (12, 1, 9.01, 200.0)],                 # both at the touch: kept
        131: [(21, 0, 8.97, 200.0), (22, 1, 9.01, 50.0)],                  # the bid is behind the touch: replaced
        132: [(31, 0, 8.99, 200.0), (32, 0, 8.99, 200.0), (33, 1, 9.05, 1.0)],   # a second bid and a far ask
        5: [(41, 0, 299.0, 1.0)],                                          # class 0: the start-up cleanup as before
        200: [(51, 1, 9.5, 1.0)],                                          # class 2, not a maker class: cleanup
    }
    config, books, accounts = _mk_state(orders=orders)
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    by = {}
    for a in actions:
        by.setdefault(a[1], []).append(a)
    assert 130 not in by                                                   # nothing to do
    assert by[131][0] == (core.ACT_CANCEL, 131, [21]) and by[131][1][:3] == (core.ACT_QUOTE, 131, core.SIDE_BUY)
    assert len(by[131]) == 2 and by[131][1][4] == 8.99
    assert by[132][0] == (core.ACT_CANCEL, 132, [32, 33]) and by[132][1][:3] == (core.ACT_QUOTE, 132, core.SIDE_SELL)
    assert by[5] == [(core.ACT_CANCEL, 5, [41])] and by[200] == [(core.ACT_CANCEL, 200, [51])]
    assert core_.c["mk_keep"] == 4 and core_.c["mk_cancel_orders"] == 3 and core_.c["cancel_books"] == 2
    # the cancel comes before the quotes of its book
    for b, rows in by.items():
        kinds = [r[0] for r in rows]
        assert kinds == sorted(kinds, key=lambda k_: k_ != core.ACT_CANCEL)


def test_v72_maker_stays_flat_one_clip_band():
    for inv, sides in ((0.0, {0, 1}), (199.0, {0, 1}), (200.0, {1}), (450.0, {1}), (-200.0, {0}), (-900.0, {0})):
        core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
        config, books, accounts = _mk_state(inv_c1=inv)
        actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
        got = {a[2] for a in actions if a[0] == core.ACT_QUOTE and a[1] == 150}
        assert got == sides, (inv, got)
    # a resting bid is pulled once the inventory reaches the band
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
    config, books, accounts = _mk_state(inv_c1=200.0, orders={150: [(7, 0, 8.99, 200.0), (8, 1, 9.01, 200.0)]})
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert [a for a in actions if a[1] == 150] == [(core.ACT_CANCEL, 150, [7])]
    # a sell can never exceed the free base (never short), a buy never the free quote
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
    config, books, accounts = _mk_state()
    accounts[UID][150]["bb"]["f"] = 120.5
    accounts[UID][151]["qb"]["f"] = 900.0
    accounts[UID][152]["bb"]["f"] = 1.0                                    # below the class-1 minimum 2.5364
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    q = {(a[1], a[2]): a[3] for a in actions if a[0] == core.ACT_QUOTE}
    assert q[(150, core.SIDE_SELL)] == 120.5 and q[(150, core.SIDE_BUY)] == 200.0
    assert q[(151, core.SIDE_BUY)] == core.floor_to(900.0 / (8.99 * 1.001), 4)
    assert (152, core.SIDE_SELL) not in q and core_.c["mk_below_min"] == 1


def test_v72_maker_guard_against_the_wrong_side_of_f():
    # f 12 bp below the bid: a buy there is > 10 bp on the wrong side -> no bid; the ask stays
    cases = ((8.99 / (1 + 12e-4), {1}), (8.99 / (1 + 9e-4), {0, 1}), (9.01 / (1 - 12e-4), {0}),
             (9.01 / (1 - 9e-4), {0, 1}), (9.0, {0, 1}))
    for f, sides in cases:
        core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
        config, books, accounts = _mk_state(f_c1=f)
        actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
        got = {a[2] for a in actions if a[0] == core.ACT_QUOTE and a[1] == 150}
        assert got == sides, (f, got)
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0, v7_mk_guard_bp=100))
    config, books, accounts = _mk_state(f_c1=8.99 / (1 + 12e-4))
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert {a[2] for a in actions if a[0] == core.ACT_QUOTE and a[1] == 150} == {0, 1}
    # a bad book cancels what rests and quotes nothing
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
    config, books, accounts = _mk_state(orders={150: [(7, 0, 8.99, 200.0)]})
    books[150] = book(8.99, None, 9.0)
    books[151] = book(8.99, 9.01, None)
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert [a for a in actions if a[1] in (150, 151)] == [(core.ACT_CANCEL, 150, [7])]
    assert core_.c["mk_bad_book"] == 2


def test_v72_maker_pulls_every_quote_on_the_last_state_before_the_step():
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
    rest = {b: [(1000 + b, 0, 8.99, 200.0), (2000 + b, 1, 9.01, 200.0)] for b in range(128, 192)}
    config, books, accounts = _mk_state(orders=rest)
    actions, _ = core_.step(28 * 10 ** 9, "hk", config, books, accounts, [])          # phase 28: kept
    assert not [a for a in actions if a[1] >= 128]
    actions, _ = core_.step(29 * 10 ** 9, "hk", config, books, accounts, [])          # phase 29 = K - 1: pulled
    mk = [a for a in actions if a[1] >= 128]
    assert len(mk) == 64 and all(a[0] == core.ACT_CANCEL and len(a[2]) == 2 for a in mk)
    assert core_.c["mk_pull_states"] == 1
    assert len([a for a in actions if a[0] == core.ACT_TAKE]) == 128                  # the taker still acts
    config, books, accounts = _mk_state()                                              # the orders are gone
    actions, _ = core_.step(29 * 10 ** 9 + 5, "hk", config, books, accounts, [])
    assert not [a for a in actions if a[1] >= 128]                                     # still the pull state: no quote
    actions, _ = core_.step(30 * 10 ** 9, "hk", config, books, accounts, [])          # phase 0: the new touch is quoted
    assert len([a for a in actions if a[0] == core.ACT_QUOTE]) == 128
    # no grid in the config: quotes go on, the pull is off and counted
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
    config, books, accounts = _mk_state()
    del config["fp_update_period"]
    actions, _ = core_.step(29 * 10 ** 9, "hk", config, books, accounts, [])
    assert len([a for a in actions if a[0] == core.ACT_QUOTE]) == 128 and core_.c["mk_no_grid"] == 1
    # v7_mk_pull=0: never pulled
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0, v7_mk_pull=0.0))
    config, books, accounts = _mk_state()
    actions, _ = core_.step(29 * 10 ** 9, "hk", config, books, accounts, [])
    assert len([a for a in actions if a[0] == core.ACT_QUOTE]) == 128


def test_v72_maker_fails_closed_without_the_class_list():
    core_ = core.TakerCore(UID, P(v7_maker_classes="1,2"))
    config, books, accounts = _mk_state(classes=False, orders={150: [(7, 0, 8.99, 200.0)], 5: [(9, 0, 299.0, 1.0)]})
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in actions if a[0] == core.ACT_QUOTE]
    assert (core.ACT_CANCEL, 150, [7]) in actions and (core.ACT_CANCEL, 5, [9]) in actions   # the cleanup owns them
    assert core_.c["mk_c1_off"] == 1 and core_.c["mk_c2_off"] == 1
    assert len([a for a in actions if a[0] == core.ACT_TAKE]) == 127
    # an unknown book count: no orders at all, resting orders still cancelled
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
    config, books, accounts = _state(200, orders={150: [7]})
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert actions == [(core.ACT_CANCEL, 150, [7])]


def test_v72_maker_off_is_v71_and_class0_is_unchanged_by_the_maker_leg():
    config, books, accounts = _mk_state(orders={5: [(41, 0, 299.0, 1.0)], 150: [(7, 0, 8.99, 200.0)]})
    off = core.TakerCore(UID, P())
    a_off, _ = off.step(10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in a_off if a[0] == core.ACT_QUOTE]
    assert (core.ACT_CANCEL, 150, [7]) in a_off                           # v7.1: the cleanup cancels it
    on = core.TakerCore(UID, P(v7_maker_classes=1.0))
    a_on, _ = on.step(10 ** 9, "hk", config, books, accounts, [])
    c0 = lambda acts: [a for a in acts if a[1] < 128]
    assert c0(a_on) == c0(a_off)
    keys = [k for k in off.c if not k.startswith("mk_") and k not in ("cancel_books", "cancel_orders")]
    assert {k: off.c[k] for k in keys} == {k: on.c.get(k) for k in keys}


def test_v72_maker_respects_the_pace_guard_and_counts_maker_fills():
    core_ = core.TakerCore(UID, P(v7_maker_classes=1.0))
    config, books, accounts = _mk_state()
    accounts[UID][150]["v"] = 499_000.0                                    # the validator's rolling volume: capped
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in actions if a[1] == 150] and core_.c["mk_paced_ledger"] == 2
    assert len([a for a in actions if a[0] == core.ACT_QUOTE and a[1] == 151]) == 2
    # our maker fills: counted by side and spent on the same per-book ledger
    notices = [{"y": "ET", "b": 151, "i": 1, "Ta": 9, "Ma": UID, "s": 1, "q": 100.0, "p": 8.99},     # we bought
               {"y": "ET", "b": 151, "i": 2, "Ta": 9, "Ma": UID, "s": 0, "q": 50.0, "p": 9.01}]      # we sold
    core_.step(2 * 10 ** 9, "hk", config, books, accounts, notices)
    assert core_.fills_maker == 2 and core_.mk_buy_notional == 899.0 and core_.mk_sell_notional == 450.5
    assert core_.runs["hk"].pace.sums[151] == 899.0 + 450.5


def test_v72_agent_builds_post_only_gtt_quotes_and_logs_the_maker_leg(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path, v7_maker_classes=1.0, v7_k=0.005)
    config, books, accounts = _mk_state(orders={131: [(21, 0, 8.97, 200.0)]})
    resp = agent.handle(_st(config, books, accounts))
    if real:
        from taos.im.protocol.models import TimeInForce
        ins = resp.instructions
        limits = [i for i in ins if i.type == "PLACE_ORDER_LIMIT"]
        quotes = [i for i in limits if i.bookId >= 128]
        takes = [i for i in limits if i.bookId < 128]
        cancels = [i for i in ins if i.type == "CANCEL_ORDERS"]
        assert len(quotes) == 128 and len(takes) == 128 and len(cancels) == 1 and cancels[0].bookId == 131
        assert all(i.postOnly is True and i.timeInForce == TimeInForce.GTT and i.expiryPeriod == 60 * 10 ** 9
                   and i.leverage == 0.0 and i.delay == 0 for i in quotes)
        assert all(i.postOnly is False and i.timeInForce == TimeInForce.IOC for i in takes)
        order = [i.type for i in ins if i.bookId == 131]
        assert order[0] == "CANCEL_ORDERS"
    else:
        ins = resp.instructions
        quotes = [kw for kind, kw in ins if kind == "limit" and kw["book_id"] >= 128]
        takes = [kw for kind, kw in ins if kind == "limit" and kw["book_id"] < 128]
        cancels = [kw for kind, kw in ins if kind == "cancel"]
        assert len(quotes) == 128 and len(takes) == 128 and len(cancels) == 1 and cancels[0]["book_id"] == 131
        assert all(kw["postOnly"] is True and int(kw["timeInForce"]) == 1 and kw["expiryPeriod"] == 60 * 10 ** 9
                   and kw["leverage"] == 0.0 and kw["delay"] == 0 and int(kw["settlement_option"]) == -2
                   and kw["quantity"] == 200.0 and kw["price"] in (8.99, 9.01) for kw in quotes)
        assert all(kw["postOnly"] is False and int(kw["timeInForce"]) == 2 and "expiryPeriod" not in kw
                   for kw in takes)
        order = [kind for kind, kw in ins if kw["book_id"] == 131]
        assert order[0] == "cancel" and order[1:] == ["limit", "limit"]
    agent.handle(_st(config, books, accounts, ts=2 * 10 ** 9))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    assert lines[0]["version"] == "v7.2" and lines[0]["params"]["maker_classes"] == [1]
    assert lines[0]["params"]["k"] == 0.005
    lay = [x for x in lines if x["kind"] == "layout"][0]
    assert lay["maker_classes"] == [1] and lay["c2_ok"] is True and lay["c2"] == [192, 255]
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["errors"] == 0 and summ["maker"]["books"] == 64 and summ["maker"]["sides_2"] == 64
    assert summ["maker"]["gap_abs_bp_p50"] == 0.0 and summ["maker"]["pull_state"] is False
    assert summ["maker_fills"] == {"buy_notional": 0.0, "sell_notional": 0.0}
    assert summ["counts"]["mk_quote_bid"] == 128 and "mk_c1_ledger_p50" in summ["pace"]


def test_v72_handler_timing_with_the_maker_leg():
    fx, books, accounts = _recorded_state()
    config = C1CFG()
    config.update(STEP_CFG)
    core_ = core.TakerCore(UID, P(v7_maker_classes="1,2", v7_k=0.005))
    times = []
    ts = fx["timestamp"]
    for i in range(300):
        t0 = time.perf_counter()
        core_.step(ts + i * 10 ** 9, "hk", config, books, accounts, [], collect=(i % 60 == 59))
        times.append((time.perf_counter() - t0) * 1000.0)
    p50, p95 = core.pctl(times, 0.5), core.pctl(times, 0.95)
    print(f"TIMING core.step 256 books (128 taker + 128 maker): p50 {p50:.3f} ms p95 {p95:.3f} ms")
    assert p95 < 50.0
