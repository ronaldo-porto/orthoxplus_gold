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
# v7.9.2 launcher profile (Round 43, MODEL picks; the launcher carries them as DEFAULTS): K1Z size pair, class-0 slope,
# class-0 target offset in clips, class-2 band half-life (sim-s) and limit.  v7.9.1 (core_12) was (1.0, 2.0) 0.005 0.0 2116.8 0.45.
PROFILE = dict(size_pair=(1.5, 2.5), size_pair_text="1.5,2.5", k=0.0035, off=0.0, hl=4233.6, rl=0.45)
V791_PROFILE = dict(size_pair=(1.0, 2.0), k=0.005, off=0.0, hl=2116.8, rl=0.45)


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
            .replace("${V7_CLASSES}", "0").replace("${V7_MAKER_CLASSES}", "1").replace("${V7_MK_TARGET}", "3")
            .replace("${V7_REL_CLASSES}", "2").replace("${V7_C0_CONTRA}", "1").replace("${V7_REL_FORM}", "band")
            # v7.5: the values the launcher sets for the default forms (V7_REL_FORM=band, V7_MK_TARGET=3)
            .replace("${REL_HL}", str(PROFILE["hl"])).replace("${REL_RATE}", "0.0209").replace("${REL_CLIP}", "1800")
            .replace("${MK_HL}", "2116.8").replace("${MK_L}", "0.70").replace("${MK_NOTIONAL}", "1500")
            .replace("${MK_GUARD}", "0").replace("${V7_C0_MAKER}", "1").replace("${V7_MK_DEPTH}", "0")
            .replace("${V7_C0_MK_TRIAL}", "0").replace("${V7_C0_MK_NOTIONAL}", V711T["c0n"]).replace("${V7_C0_MK_BAND_W}", V711T["c0b"])
            .replace("${V7_C2_MAKER}", "1").replace("${V7_C2_MK_NOTIONAL}", V711T["c2n"]).replace("${V7_C2_MK_BAND_W}", V711T["c2b"])
            .replace("${V7_MK_PLACE_ID}", "1").replace("${V7_TOUCH_ID}", "0").replace("${V7_SIZE_ID}", "1")
            .replace("${V7_SIZE_PAIR}", PROFILE["size_pair_text"].strip('"')).replace("${V7_C0_K}", str(PROFILE["k"]))
            .replace("${V7_C0_TGT_OFF}", str(PROFILE["off"])).replace("${V7_REL_L}", str(PROFILE["rl"])))


def test_params_from_the_launcher_parse_to_the_defaults():
    env = os.environ.get("V7_LAUNCH_PARAMS")
    for text in filter(None, [env, _launcher_params()]):
        ns = _parse_kwargs_like(text)
        p = core.parse_params(ns)
        assert p["k"] == PROFILE["k"] and p["l"] == 0.55 and p["clip"] == 7.0 and p["big"] == 15.0   # profile slope
        assert p["size_pair"] == PROFILE["size_pair"] and p["c0_tgt_off"] == PROFILE["off"]
        assert p["classes"] == ((0,) if text is not env else p["classes"]) and p["classes"] in ((0,), (0, 1))
        assert p["maker_classes"] == ((1,) if text is not env else p["maker_classes"])
        assert p["maker_classes"] in ((), (1,), (1, 2)) and not set(p["maker_classes"]) & set(p["classes"])
        assert p["mk_band_clips"] == 1.0 and p["mk_pull"] is True and p["mk_expiry_s"] == 60.0
        assert p["mk_target"] is (True if text is not env else p["mk_target"])          # v7.3 launch default
        assert p["mk_k"] == 0.005 and p["mk_ref_s"] == 10800.0
        # v7.5 launch defaults: class-1 band target (K2), class-2 band form (B2a-w), the class-0 term on
        assert p["mk_band"] is (True if text is not env else p["mk_band"]) and not (p["mk_band"] and p["mk_dyn"])
        assert p["rel_form"] == ("band" if text is not env else p["rel_form"]) and p["rel_form"] in core.REL_FORMS
        assert p["rel_classes"] == ((2,) if text is not env else p["rel_classes"]) and p["rel_classes"] in ((), (2,))
        assert p["c0_contra"] is (True if text is not env else p["c0_contra"])
        # each form carries the values it was replayed with (the launcher sets them from the switches)
        if p["mk_band"]:
            assert p["mk_dyn_hl_s"] == 2116.8 and p["mk_l"] == 0.70 and p["mk_notional"] == 1500.0
            assert p["mk_guard_bp"] == 0.0 and p["mk_band_mult"] == 2.0
        else:
            assert p["mk_dyn_hl_s"] == 1058.4 and p["mk_l"] == 0.45 and p["mk_notional"] == 1800.0
            assert p["mk_guard_bp"] == 10.0
        assert p["mk_dyn_rate_h"] == 0.03
        if p["rel_form"] == "t9":
            assert p["rel_hl_s"] == 529.2 and p["rel_rate_h"] == 0.0321 and p["rel_clip_notional"] == 930.0
        else:
            assert p["rel_hl_s"] == PROFILE["hl"] and p["rel_rate_h"] == 0.0209 and p["rel_clip_notional"] == 1800.0
        assert p["rel_l"] == PROFILE["rl"] and p["rel_band_mult"] == 2.0 and p["rel_mom"] == 0.0037
        assert p["step_thr_bp"] == 3.0 and p["step_max_dt_s"] == 60.0
        # v7.6 launch defaults: the class-0 maker on, values as Round 29 C copied them; v7.7: the depth maker off,
        # the class-0 maker offset trial on; v7.8: the class-0 maker and its trial off (S1 = v7.5)
        assert p["c0_maker"] is (True if text is not env else p["c0_maker"])             # v7.11: the maker profile
        assert p["mk_depth"] is (False if text is not env else p["mk_depth"])
        assert p["c0_mk_trial"] is (False if text is not env else p["c0_mk_trial"])
        assert p["touch_id"] is (False if text is not env else p["touch_id"])         # v7.9.2: E1T off
        assert p["size_id"] is (True if text is not env else p["size_id"])            # v7.9.2: K1Z on
        assert p["c0_mk_notional"] == V711["c0n"] and p["c0_mk_band_w"] == V711["c0b"]    # v7.11: COPIED profile values
        assert p["mkd_start_bp"] == 2.0 and p["mkd_every_s"] == 600.0
        assert not p["mk_depth"] or 1 in p["maker_classes"]
        assert p["c0_kc"] == 0.005 and p["c0_lc"] == 0.45 and p["c0_ref_s"] == 10800.0
        assert not set(p["rel_classes"]) & (set(p["maker_classes"]) | set(p["classes"]))
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
    assert lines[0]["version"] == "v7.11" and lines[0]["params"]["maker_classes"] == [1]
    assert lines[0]["params"]["mk_target"] is False                       # the code default: the v7.2 flat band
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


# ---- v7.3: the maker leg's contrarian target (Round 25 track S) -------------------------------------------------------

MKT = dict(v7_maker_classes=1.0, v7_mk_target=1.0)
C1_BI = 25000.0 / C1_MID                                                    # class-1 initial base in _mk_state


def _q150(core_, ts, collect=False, **kw):
    """One state built by _mk_state(**kw) -> the sides quoted on class-1 book 150."""
    config, books, accounts = _mk_state(**kw)
    actions, _ = core_.step(ts, "hk", config, books, accounts, [], collect=collect)
    return {a[2] for a in actions if a[0] == core.ACT_QUOTE and a[1] == 150}


def test_v73_params_target_and_bounds():
    import pytest
    p = P()
    assert core.V7_VERSION == "v7.11"
    assert p["mk_target"] is False and p["mk_k"] == 0.005 and p["mk_l"] == 0.45 and p["mk_ref_s"] == 10800.0
    assert P(v7_mk_target=1.0)["mk_target"] is True and P(v7_mk_target=0.0)["mk_target"] is False
    assert core.MK_REF_S_DEFAULT == core.DEPTH_S                           # the scoring lookback
    for bad in ({"v7_mk_target": 4}, {"v7_mk_target": "on"}, {"v7_mk_k": 0}, {"v7_mk_k": 1.5}, {"v7_mk_l": 0},
                {"v7_mk_l": 1.1}, {"v7_mk_ref_s": 0}):
        with pytest.raises(ValueError):
            core.parse_params(bad)


def test_v73_target_off_is_the_v72_flat_band():
    a = core.TakerCore(UID, P(v7_maker_classes=1.0))
    b = core.TakerCore(UID, P(v7_maker_classes=1.0, v7_mk_target=0.0))
    for i, (bid, ask, inv) in enumerate(((8.99, 9.01, 0.0), (8.97, 8.99, 0.0), (8.97, 8.99, 250.0),
                                         (9.01, 9.03, -250.0))):
        config, books, accounts = _mk_state(bid=bid, ask=ask, f_c1=0.5 * (bid + ask), inv_c1=inv)
        xa, _ = a.step((i + 1) * 10 ** 9, "hk", config, books, accounts, [], collect=True)
        xb, _ = b.step((i + 1) * 10 ** 9, "hk", config, books, accounts, [], collect=True)
        assert xa == xb
        got = {x[2] for x in xa if x[0] == core.ACT_QUOTE and x[1] == 150}
        assert got == ({0, 1} if inv == 0.0 else {1} if inv > 0 else {0}), (i, got)   # flat band: the move is ignored
    assert not a.runs["hk"].mk_ref and "target" not in a.mk_stats and "mk_ref_seed" not in a.c


def test_v73_target_buys_what_fell_and_sells_what_rose():
    core_ = core.TakerCore(UID, P(**MKT))
    assert _q150(core_, 10 ** 9) == set()                                  # seed: flat, ref = mid, target 0: in band
    rs = core_.runs["hk"]
    assert core_.c["mk_ref_seed"] == 64 and rs.mk_ref[150][0] == 0.5 * (8.99 + 9.01) and len(rs.mk_ref) == 64
    assert core_.c["mk_in_band"] == 64
    # the mid falls 22 bp: target about +0.11 W -> ONE bid, no ask (buy what fell)
    assert _q150(core_, 2 * 10 ** 9, bid=8.97, ask=8.99, f_c1=8.98) == {0}
    assert core_.c["mk_quote_bid"] == 64 and "mk_quote_ask" not in core_.c and "mk_band_ask" not in core_.c
    ref = rs.mk_ref[150][0]
    w = 25000.0 + C1_BI * 8.98
    tgt = -0.005 * 1e4 * (8.98 - ref) / ref * w                            # quote notional, about +5,560
    assert 5000.0 < tgt < 6000.0
    inv = tgt / 8.98
    # bought up to the target: no quote; less than a clip either side of it: still none; one clip above it: the ask
    assert _q150(core_, 3 * 10 ** 9, bid=8.97, ask=8.99, f_c1=8.98, inv_c1=inv) == set()
    assert _q150(core_, 3 * 10 ** 9 + 1, bid=8.97, ask=8.99, f_c1=8.98, inv_c1=inv + 190.0) == set()
    assert _q150(core_, 3 * 10 ** 9 + 2, bid=8.97, ask=8.99, f_c1=8.98, inv_c1=inv - 190.0) == set()
    assert _q150(core_, 4 * 10 ** 9, bid=8.97, ask=8.99, f_c1=8.98, inv_c1=inv + 201.0) == {1}
    assert _q150(core_, 4 * 10 ** 9 + 1, bid=8.97, ask=8.99, f_c1=8.98, inv_c1=inv - 201.0) == {0}
    # the mid is back at the reference with that inventory: the target is about 0 again -> sell it down
    assert _q150(core_, 5 * 10 ** 9, inv_c1=inv) == {1}
    # the mid rises 22 bp above the reference with no inventory: target short -> no bid (sell what rose)
    assert _q150(core_, 6 * 10 ** 9, bid=9.01, ask=9.03, f_c1=9.02) == {1}
    # never an order of another kind on a maker book, and class 0 still takes
    config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.98)
    actions, _ = core_.step(7 * 10 ** 9, "hk", config, books, accounts, [])
    assert all(a[0] == core.ACT_QUOTE for a in actions if 128 <= a[1] < 192)
    assert len([a for a in actions if a[0] == core.ACT_TAKE and a[1] < 128]) == 128


def test_v73_target_is_clipped_at_the_limit():
    core_ = core.TakerCore(UID, P(**MKT))
    _q150(core_, 10 ** 9)
    assert _q150(core_, 2 * 10 ** 9, collect=True, bid=8.79, ask=8.81, f_c1=8.80) == {0}   # -222 bp: k x ret = 1.1
    w = 25000.0 + C1_BI * 8.80
    st = core_.mk_stats
    assert st["target"] is True and st["refs"] == 64 and abs(st["tgt_abs_notional_max"] - 0.45 * w) < 1e-6
    assert abs(st["tgt_abs_notional_p50"] - 0.45 * w) < 1e-6 and abs(st["dev_abs_notional_max"] - 0.45 * w) < 1e-6
    lim_units = 0.45 * w / 8.80
    assert _q150(core_, 3 * 10 ** 9, bid=8.79, ask=8.81, f_c1=8.80, inv_c1=lim_units) == set()   # at the limit: no more
    assert _q150(core_, 4 * 10 ** 9, bid=8.79, ask=8.81, f_c1=8.80, inv_c1=lim_units + 205.0) == {1}


def test_v73_seed_adopts_the_inventory_found():
    core_ = core.TakerCore(UID, P(**MKT))
    assert _q150(core_, 10 ** 9, inv_c1=450.0) == set()                    # 4,050 notional long: adopted as target
    frac = 450.0 * C1_MID / (25000.0 + C1_BI * C1_MID)
    assert abs(core_.runs["hk"].mk_ref[150][0] - C1_MID / (1.0 - frac / 50.0)) < 1e-9
    assert _q150(core.TakerCore(UID, P(v7_maker_classes=1.0)), 10 ** 9, inv_c1=450.0) == {1}     # v7.2: sold down
    core_ = core.TakerCore(UID, P(**MKT))
    assert _q150(core_, 10 ** 9, inv_c1=-450.0) == set()
    # beyond the limit the target is the limit: only the reducing side is quoted
    core_ = core.TakerCore(UID, P(**MKT))
    assert _q150(core_, 10 ** 9, inv_c1=3000.0) == {1}                     # 27,000 = 0.54 W > 0.45 W
    core_ = core.TakerCore(UID, P(**MKT))
    assert _q150(core_, 10 ** 9, inv_c1=-2750.0) == {0}
    # the adopted target decays with the reference: one lookback later, same mid, it is 1/e of the inventory
    core_ = core.TakerCore(UID, P(**MKT))
    _q150(core_, 10 ** 9, inv_c1=450.0)
    assert _q150(core_, 10 ** 9 + 10800 * 10 ** 9, collect=True, inv_c1=450.0) == {1}
    assert abs(core_.mk_stats["tgt_abs_notional_max"] - 4050.0 * math.exp(-1.0)) < 5.0


def test_v73_reference_follows_the_mid_slowly_and_resets():
    core_ = core.TakerCore(UID, P(**MKT))
    _q150(core_, 10 ** 9)
    rs = core_.runs["hk"]
    r0 = rs.mk_ref[150][0]
    _q150(core_, 10 ** 9 + 10800 * 10 ** 9, bid=8.97, ask=8.99, f_c1=8.98)      # one scoring lookback later
    m1 = 0.5 * (8.97 + 8.99)
    assert abs(rs.mk_ref[150][0] - (r0 + (1.0 - math.exp(-1.0)) * (m1 - r0))) < 1e-12
    # it follows on a pull state too (no quote there) ...
    r1 = rs.mk_ref[150][0]
    assert _q150(core_, (10801 + 28) * 10 ** 9, bid=8.95, ask=8.97, f_c1=8.96) == set()
    assert core_.c["mk_pull_states"] == 1 and rs.mk_ref[150][0] < r1
    # ... and with an unusable f (no quote), but not on a one-sided book
    r2 = rs.mk_ref[150][0]
    config, books, accounts = _mk_state(bid=8.93, ask=8.95)
    for b in range(128, 192):
        books[b]["f"] = None
    books[151]["a"] = []
    r2b = rs.mk_ref[151][0]
    actions, _ = core_.step((10801 + 29) * 10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in actions if a[0] == core.ACT_QUOTE and a[1] >= 128] and core_.c["mk_bad_book"] == 64
    assert rs.mk_ref[150][0] < r2 and rs.mk_ref[151][0] == r2b
    # a new simulation clears the references: fresh endowments, re-seeded at the mid found
    config, books, accounts = _mk_state(bid=8.89, ask=8.91, f_c1=8.90)
    config["simulation_id"] = "sim_b"
    actions, _ = core_.step(5 * 10 ** 9, "hk", config, books, accounts, [])
    assert rs.mk_ref[150][0] == 0.5 * (8.89 + 8.91)
    assert not [a for a in actions if a[0] == core.ACT_QUOTE and a[1] >= 128]
    # a book-count change clears them too (book ids move between classes)
    config, books, accounts = _state(128, classes=_classes((64, 32, 32)))
    core_.step(6 * 10 ** 9, "hk", config, books, accounts, [])
    assert 150 not in rs.mk_ref and set(rs.mk_ref) <= set(range(64, 96)) and core_.c["pace_reset_layout"] == 1
    # references are per validator stream
    config, books, accounts = _mk_state()
    core_.step(7 * 10 ** 9, "other", config, books, accounts, [])
    assert core_.runs["other"].mk_ref is not rs.mk_ref and 150 in core_.runs["other"].mk_ref


def test_v73_guard_and_pull_still_apply_with_the_target():
    core_ = core.TakerCore(UID, P(**MKT))
    _q150(core_, 10 ** 9)
    # the mid fell 22 bp, so the target wants the bid, but f is 20 bp below the bid: the guard withholds it
    assert _q150(core_, 2 * 10 ** 9, bid=8.97, ask=8.99, f_c1=8.952) == set()
    assert core_.c["mk_guard_bid"] == 64 and "mk_quote_bid" not in core_.c
    # likewise the ask after a rise, when f is more than 10 bp above it
    assert _q150(core_, 3 * 10 ** 9, bid=9.01, ask=9.03, f_c1=9.05) == set()
    assert core_.c["mk_guard_ask"] == 64
    # the last state before the fundamental step: a resting bid is cancelled and nothing is placed
    config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.98, orders={150: [(7, 0, 8.97, 200.0)]})
    actions, _ = core_.step(29 * 10 ** 9, "hk", config, books, accounts, [])
    assert [a for a in actions if a[1] == 150] == [(core.ACT_CANCEL, 150, [7])]
    # the next state: the bid is quoted again; a resting bid at the wanted price is kept, a resting ask is cancelled
    config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.98,
                                        orders={150: [(8, 0, 8.97, 200.0), (9, 1, 8.99, 200.0)]})
    actions, _ = core_.step(30 * 10 ** 9, "hk", config, books, accounts, [])
    assert [a for a in actions if a[1] == 150] == [(core.ACT_CANCEL, 150, [9])]
    assert {a[2] for a in actions if a[0] == core.ACT_QUOTE and a[1] == 151} == {0}


def test_v73_target_fails_to_the_flat_band_without_a_wealth_figure():
    core_ = core.TakerCore(UID, P(**MKT))
    config, books, accounts = _mk_state()
    del config["miner_wealth"]
    accounts[UID][150]["qb"]["i"] = None
    actions, _ = core_.step(10 ** 9, "hk", config, books, accounts, [])
    assert 150 not in core_.runs["hk"].mk_ref and 151 in core_.runs["hk"].mk_ref and core_.c["mk_no_wealth"] == 1
    assert not [a for a in actions if a[1] == 150]                         # target 0, flat: nothing to do
    # ... and an inventory there is only worked flat
    config, books, accounts = _mk_state(inv_c1=450.0)
    del config["miner_wealth"]
    accounts[UID][150]["qb"]["i"] = None
    actions, _ = core_.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    assert {a[2] for a in actions if a[0] == core.ACT_QUOTE and a[1] == 150} == {1} and core_.c["mk_no_wealth"] == 2
    # the config's miner_wealth is the fallback, as for the taker
    config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.98)
    accounts[UID][150]["qb"]["i"] = None
    actions, _ = core_.step(3 * 10 ** 9, "hk", config, books, accounts, [])
    assert 150 in core_.runs["hk"].mk_ref and core_.c["mk_no_wealth"] == 2


def test_v73_target_respects_the_pace_guard_and_free_balances():
    core_ = core.TakerCore(UID, P(**MKT))
    _q150(core_, 10 ** 9)
    config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.98)
    accounts[UID][150]["v"] = 499_000.0                                    # the validator's rolling volume: capped
    accounts[UID][151]["qb"]["f"] = 900.0                                  # little free quote: the bid is clamped
    accounts[UID][152]["qb"]["f"] = 0.0
    actions, _ = core_.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    q = {(a[1], a[2]): a[3] for a in actions if a[0] == core.ACT_QUOTE}
    assert (150, core.SIDE_BUY) not in q and core_.c["mk_paced_ledger"] == 1
    assert q[(151, core.SIDE_BUY)] == core.floor_to(900.0 / (8.97 * 1.001), 4)
    assert (152, core.SIDE_BUY) not in q and core_.c["mk_no_quote"] == 1
    assert q[(153, core.SIDE_BUY)] == core.floor_to(1800.0 / 8.98, 4)


def test_v73_agent_logs_the_target_picture(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path, v7_k=0.005, **MKT)
    config, books, accounts = _mk_state()
    agent.handle(_st(config, books, accounts))
    config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.98)
    resp = agent.handle(_st(config, books, accounts, ts=2 * 10 ** 9))
    if real:
        quotes = [i for i in resp.instructions if i.type == "PLACE_ORDER_LIMIT" and i.bookId >= 128]
        assert len(quotes) == 64 and all(i.postOnly is True and i.price == 8.97 for i in quotes)
    else:
        quotes = [kw for kind, kw in resp.instructions if kind == "limit" and kw["book_id"] >= 128]
        assert len(quotes) == 64 and all(kw["postOnly"] is True and kw["price"] == 8.97 and int(kw["direction"]) == 0
                                         for kw in quotes)
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    assert lines[0]["version"] == "v7.11" and lines[0]["core"] == core.CORE_VERSION
    assert lines[0]["params"]["mk_target"] is True and lines[0]["params"]["mk_k"] == 0.005
    assert lines[0]["params"]["mk_l"] == 0.45 and lines[0]["params"]["mk_ref_s"] == 10800.0
    summ = [x for x in lines if x["kind"] == "summary"][0]
    mk = summ["maker"]
    assert summ["errors"] == 0 and mk["target"] is True and mk["refs"] == 64 and mk["books"] == 64
    assert mk["sides_1"] == 64 and 5000.0 < mk["tgt_abs_notional_p50"] < 6000.0
    assert mk["dev_abs_notional_p50"] == mk["tgt_abs_notional_p50"] and mk["corr_inv_target"] is None
    assert summ["counts"]["mk_quote_bid"] == 64 and summ["counts"]["mk_ref_seed"] == 64
    assert summ["counts"]["mk_in_band"] == 64 and "mk_quote_ask" not in summ["counts"]
    sp = mk["split"]                                                       # core 5.1 logging only
    assert sp["out_bid"] == 64 and sp["free"]["n"] == 64 and sp["blocked"]["n"] == 0
    assert sp["free"]["dev_abs_p50"] == mk["dev_abs_notional_p50"] and summ["counts"]["mk_out_bid"] == 64


def test_v73_core51_split_by_the_guard_is_logging_only():
    assert core.CORE_VERSION == "taker_v7_core_14" and core.V7_VERSION == "v7.11"
    keys = {"in_band", "out_bid", "out_ask", "blocked_bid", "blocked_ask", "free", "blocked"}
    # seed flat: everything in band and free
    core_ = core.TakerCore(UID, P(**MKT))
    assert _q150(core_, 10 ** 9, collect=True) == set()
    sp = core_.mk_stats["split"]
    assert set(sp) == keys and sp["in_band"] == 64 and sp["free"]["n"] == 64 and sp["blocked"]["n"] == 0
    assert sp["blocked"] == {"n": 0, "corr": None, "dev_abs_p50": None, "dev_abs_max": None}
    assert "mk_out_bid" not in core_.c and "mk_out_ask" not in core_.c
    # the mid fell 22 bp with f at the mid: the bid is wanted and allowed -> out_bid, free
    assert _q150(core_, 2 * 10 ** 9, collect=True, bid=8.97, ask=8.99, f_c1=8.98) == {0}
    sp = core_.mk_stats["split"]
    assert sp["out_bid"] == 64 and sp["in_band"] == sp["out_ask"] == sp["blocked_bid"] == sp["blocked_ask"] == 0
    assert sp["free"]["n"] == 64 and sp["free"]["dev_abs_p50"] == core_.mk_stats["dev_abs_notional_p50"]
    assert core_.c["mk_out_bid"] == 64 and "mk_guard_bid" not in core_.c
    # the same fall with f 20 bp below the bid: the guard withholds the bid -> blocked_bid
    assert _q150(core_, 3 * 10 ** 9, collect=True, bid=8.97, ask=8.99, f_c1=8.952) == set()
    sp = core_.mk_stats["split"]
    assert sp["blocked_bid"] == 64 and sp["free"]["n"] == 0 and sp["blocked"]["n"] == 64
    assert sp["blocked"]["dev_abs_max"] == core_.mk_stats["dev_abs_notional_max"] and sp["free"]["dev_abs_p50"] is None
    assert core_.c["mk_guard_bid"] == 64 and core_.c["mk_out_bid"] == 64
    # a rise with f 34 bp above the mid (the testnet picture): the ask is wanted and withheld -> blocked_ask
    assert _q150(core_, 4 * 10 ** 9, collect=True, bid=9.01, ask=9.03, f_c1=9.0507) == set()
    sp = core_.mk_stats["split"]
    assert sp["blocked_ask"] == 64 and sp["blocked"]["n"] == 64 and "mk_out_ask" not in core_.c
    # the same rise with f at the mid: allowed -> out_ask
    assert _q150(core_, 5 * 10 ** 9, collect=True, bid=9.01, ask=9.03, f_c1=9.02) == {1}
    sp = core_.mk_stats["split"]
    assert sp["out_ask"] == 64 and sp["free"]["n"] == 64 and core_.c["mk_out_ask"] == 64
    # a mixed state: one book long by more than a clip over its target under a far f, the rest in band
    core_ = core.TakerCore(UID, P(**MKT))
    _q150(core_, 10 ** 9)
    config, books, accounts = _mk_state()
    a = acc(base_total=C1_BI + 250.0, base_init=C1_BI, quote_init=25000.0, base_free=C1_BI + 250.0, quote_free=25000.0)
    a["b"] = 150
    accounts[UID][150] = a
    books[150] = book(8.99, 9.01, 9.0306)                                  # f 34 bp above the mid of book 150 only
    actions, _ = core_.step(2 * 10 ** 9, "hk", config, books, accounts, [], collect=True)
    assert not [x for x in actions if x[0] == core.ACT_QUOTE and x[1] == 150]
    sp = core_.mk_stats["split"]
    assert sp["blocked_ask"] == 1 and sp["in_band"] == 63 and sp["free"]["n"] == 63 and sp["blocked"]["n"] == 1
    assert abs(sp["blocked"]["dev_abs_p50"] - 2250.0) < 5.0 and sp["free"]["dev_abs_max"] < 5.0
    assert sp["in_band"] + sp["out_bid"] + sp["out_ask"] + sp["blocked_bid"] + sp["blocked_ask"] == 64
    # the helper refuses misaligned input instead of raising
    assert core.mk_split([(1.0, 2.0)], [])["error"] == "misaligned" and core.mk_split(None, None)["pairs"] is None
    # target off (the v7.2 flat band): no split, no new counters
    c2 = core.TakerCore(UID, P(v7_maker_classes=1.0))
    _q150(c2, 10 ** 9, collect=True)
    assert "split" not in c2.mk_stats and "mk_out_bid" not in c2.c and "mk_out_ask" not in c2.c


def test_v73_handler_timing_with_the_target():
    fx, books, accounts = _recorded_state()
    config = C1CFG()
    config.update(STEP_CFG)
    core_ = core.TakerCore(UID, P(v7_maker_classes="1,2", v7_k=0.005, v7_mk_target=1.0))
    times = []
    ts = fx["timestamp"]
    for i in range(300):
        t0 = time.perf_counter()
        core_.step(ts + i * 10 ** 9, "hk", config, books, accounts, [], collect=(i % 60 == 59))
        times.append((time.perf_counter() - t0) * 1000.0)
    p50, p95 = core.pctl(times, 0.5), core.pctl(times, 0.95)
    print(f"TIMING core.step 256 books (128 taker + 128 maker with the target): p50 {p50:.3f} ms p95 {p95:.3f} ms")
    assert p95 < 50.0 and len(core_.runs["hk"].mk_ref) == 128


# ---- v7.4: class-2 stepping taker, class-0 contrarian term, class-1 stepping target (Round 27 X0 / X1) ----------------

V74 = dict(v7_k=0.005, v7_maker_classes=1.0, v7_mk_target=2.0, v7_rel_classes=2.0, v7_c0_contra=1.0)
REL_STEP = 0.0209 * 60.0 / 529.2                                            # W per 60 sim-s of the class-2 target
DYN_STEP = 0.03 * 60.0 / 529.2                                              # W per 60 sim-s of the class-1 target
S60 = 60 * 10 ** 9


def _c0_state(mid, gap_bp=0.0, inv=0.0, n=256, sim="sim_a"):
    """Every book at ``mid`` (touch +-0.01) with f = mid x (1 + gap_bp / 1e4) and ``inv`` units of inventory."""
    books, accounts = {}, {}
    for b in range(n):
        books[b] = book(round(mid - 0.01, 2), round(mid + 0.01, 2), mid * (1.0 + gap_bp / 1e4))
        a = acc(base_total=BI + inv, base_init=BI, quote_init=QI, base_free=BI + inv, quote_free=QI)
        a["b"] = b
        accounts[b] = a
    return cfg(n, simulation_id=sim), books, {UID: accounts}


def _rel_state(mids=None, inv=None, free=None, classes=True, **kw):
    """_mk_state with the class-2 books (192-255) at their own mids: ``mids`` = {book: mid} (the rest 9.00), touch
    +-0.01, f = 9.60 (far above the mid, as on mainnet); ``inv`` = {book: units}; ``free`` = {book: free base}."""
    config, books, accounts = _mk_state(classes=classes, **kw)
    mids, inv, free = mids or {}, inv or {}, free or {}
    for b in range(192, 256):
        m = mids.get(b, C1_MID)
        books[b] = book(round(m - 0.01, 4), round(m + 0.01, 4), 9.6) if m is not None else book(8.99, None, 9.6)
        x = inv.get(b, 0.0)
        a = acc(base_total=C1_BI + x, base_init=C1_BI, quote_init=25000.0, base_free=free.get(b, C1_BI + x),
                quote_free=25000.0)
        a["b"] = b
        a["o"] = accounts[UID][b]["o"]
        accounts[UID][b] = a
    return config, books, accounts


def _c2(actions):
    return [a for a in actions if 192 <= a[1] < 256]


def test_v74_params_switches_bounds_and_conflicts():
    import pytest
    p = P()
    assert core.V7_VERSION == "v7.11" and core.CORE_VERSION == "taker_v7_core_14"
    # the code defaults are v7.3: every v7.4 leg off
    assert p["rel_classes"] == () and p["c0_contra"] is False and p["mk_target"] is False and p["mk_dyn"] is False
    assert p["rel_hl_s"] == 4 * 529.2 == 2116.8 and p["rel_rate_h"] == 0.0209 and p["rel_l"] == 0.45
    assert p["rel_clip_notional"] == 1800.0 == p["mk_notional"] and p["step_thr_bp"] == 3.0
    assert p["step_max_dt_s"] == 60.0 and p["mk_dyn_hl_s"] == 2 * 529.2 == 1058.4 and p["mk_dyn_rate_h"] == 0.03
    assert p["c0_kc"] == p["mk_k"] == 0.005 and p["c0_lc"] == p["mk_l"] == 0.45
    assert p["c0_ref_s"] == core.DEPTH_S == 10800.0
    q = P(**V74)
    assert q["rel_classes"] == (2,) and q["c0_contra"] is True and q["mk_target"] is True and q["mk_dyn"] is True
    assert P(v7_mk_target=1.0)["mk_dyn"] is False and P(v7_mk_target=1.0)["mk_target"] is True
    assert P(v7_rel_classes="none")["rel_classes"] == () and P(v7_rel_classes=" 2 ")["rel_classes"] == (2,)
    for bad in ({"v7_rel_classes": "1"}, {"v7_rel_classes": "1,2"}, {"v7_rel_classes": 0.0},
                {"v7_rel_classes": 2.0, "v7_maker_classes": "1,2"}, {"v7_c0_contra": 2}, {"v7_c0_contra": "on"},
                {"v7_c0_kc": 0}, {"v7_c0_lc": 1.5}, {"v7_c0_ref_s": 0}, {"v7_rel_hl_s": 0}, {"v7_rel_rate_h": 0},
                {"v7_rel_l": 0}, {"v7_rel_l": 1.1}, {"v7_rel_clip_notional": 0}, {"v7_step_thr_bp": -1},
                {"v7_step_max_dt_s": 0}, {"v7_mk_dyn_hl_s": 0}, {"v7_mk_dyn_rate_h": 0}, {"v7_mk_target": 4}):
        with pytest.raises(ValueError):
            core.parse_params(bad)


def test_v74_decide_takes_an_outside_target_and_an_added_term():
    p = P()
    rule = (0.0, 0.45, 1800.0)                                             # clip = 1,800 / 300 = 6 units; W = 50,000
    args = (BI, BI, QI, BI, QI, 0.0003, p, GRID, None)
    r = core.decide(299.99, 300.01, 310.0, *args, rule=rule, tfrac=0.04)   # the far f does not move the target
    assert r[0] == "send" and r[1] == (core.SIDE_BUY, 6.0, 300.01) and r[2] == 0.0
    assert abs(r[3] - 0.04 * w_at(300.0) / 300.0) < 1e-9
    assert core.decide(299.99, 300.01, 310.0, *args, rule=rule, tfrac=0.03)[0] == "in_band"
    r = core.decide(299.99, 300.01, 310.0, *args, rule=rule, tfrac=-0.04)
    assert r[0] == "send" and r[1] == (core.SIDE_SELL, 6.0, 299.99)
    # a sell stops at the free base (never a loan), and without free base there is no order
    r = core.decide(299.99, 300.01, 310.0, BI, BI, QI, 2.0, QI, 0.0003, p, GRID, None, rule=rule, tfrac=-0.04)
    assert r[1] == (core.SIDE_SELL, 2.0, 299.99)
    assert core.decide(299.99, 300.01, 310.0, BI, BI, QI, 0.0, QI, 0.0003, p, GRID, None, rule=rule,
                       tfrac=-0.04)[0] == "no_base"
    # add_frac: the gap term (10 bp x 0.0035 = 0.035 W) plus the added term, inside the common limit
    f10 = 300.0 * 1.001
    legacy = core.decide(299.99, 300.01, f10, *args)
    assert core.decide(299.99, 300.01, f10, *args, add_frac=0.0) == legacy and legacy[0] == "in_band"
    r = core.decide(299.99, 300.01, f10, *args, add_frac=0.05)
    assert r[0] == "send" and r[1] == (core.SIDE_BUY, 7.0, 300.01) and abs(r[3] - 0.085 * w_at(300.0) / 300.0) < 1e-9
    assert core.decide(299.99, 300.01, f10, *args, add_frac=-0.035)[0] == "in_band"     # the two terms cancel
    r = core.decide(299.99, 300.01, f10, *args, add_frac=-0.135)                        # net -0.10 W: sell
    assert r[1] == (core.SIDE_SELL, 15.0, 299.99)
    r = core.decide(299.99, 300.01, 300.0 * 1.02, *args, add_frac=0.3)                  # 0.55 + 0.3 -> the limit
    assert abs(r[3] - 0.55 * w_at(300.0) / 300.0) < 1e-9
    r = core.decide(299.99, 300.01, 300.0 * 0.98, *args, add_frac=0.3)                  # -0.55 + 0.3 = -0.25
    assert abs(r[3] + 0.25 * w_at(300.0) / 300.0) < 1e-9


def test_v74_c0_contra_seed_is_the_v73_decision_then_buys_what_fell():
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_contra=1.0))
    base = core.TakerCore(UID, P(v7_k=0.005))
    st = _c0_state(300.0)
    a1, p1 = c.step(10 ** 9, "hk", *st, [], collect=True)
    a0, p0 = base.step(10 ** 9, "hk", *st, [], collect=True)
    rs = c.runs["hk"]
    assert a1 == a0 == [] and p1 == p0 and c.c["c0_ref_seed"] == 128 and len(rs.c0_ref) == 128
    assert rs.c0_ref[5] == [300.0, rs.clock] and not base.runs["hk"].c0_ref and base.c0_stats is None
    assert c.c0_stats == {"books": 128, "refs": 128, "tc_abs_w_p50": 0.0, "tc_abs_w_max": 0.0, "tc_net_w": 0.0,
                          "opposed": 0, "at_own_limit": 0}
    # the mid falls 20 bp with f = mid (gap 0): the v7.3 taker has nothing to do, the term buys what fell
    st = _c0_state(299.4)
    a1, p1 = c.step(2 * 10 ** 9, "hk", *st, [], collect=True)
    a0, _ = base.step(2 * 10 ** 9, "hk", *st, [])
    ref = 300.0 + (1.0 - math.exp(-1.0 / 10800.0)) * (299.4 - 300.0)
    assert abs(rs.c0_ref[5][0] - ref) < 1e-12 and rs.c0_ref[5][1] == rs.clock
    tc = -0.005 * 1e4 * (299.4 - ref) / ref
    w = QI + BI * 299.4
    assert 0.0999 < tc < 0.1 and abs(p1[0][1] - tc * w / 299.4) < 1e-9
    assert a0 == [] and sorted(x[1] for x in a1) == list(range(128))
    assert all(x == (core.ACT_TAKE, x[1], core.SIDE_BUY, 15.0, 299.41) for x in a1)
    assert abs(c.c0_stats["tc_abs_w_p50"] - tc) < 1e-12 and c.c0_stats["opposed"] == 0
    assert abs(c.c0_stats["tc_net_w"] - 128 * tc) < 1e-9
    # no order of any kind outside class 0, and the class-1 / class-2 state is untouched
    assert not rs.step_st and not rs.mk_ref and c.rel_stats is None


def test_v74_c0_contra_opposed_terms_cancel_and_the_limits_hold():
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_contra=1.0))
    base = core.TakerCore(UID, P(v7_k=0.005))
    for x in (c, base):
        x.step(10 ** 9, "hk", *_c0_state(300.0), [])
    # the mid rose 20 bp and f is 20 bp above it: gap term +0.10 W, contrarian term -0.10 W -> nothing to do
    st = _c0_state(300.6, gap_bp=20.0)
    a1, p1 = c.step(2 * 10 ** 9, "hk", *st, [], collect=True)
    a0, _ = base.step(2 * 10 ** 9, "hk", *st, [])
    assert a1 == [] and len(a0) == 128 and all(x[2] == core.SIDE_BUY and x[3] == 15.0 for x in a0)
    assert abs(p1[0][1]) < 0.01 and c.c0_stats["opposed"] == 128 and c.c["in_band"] == 256
    # the term's own limit: a 200 bp fall alone gives +0.45 W, not 1.0
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_contra=1.0))
    c.step(10 ** 9, "hk", *_c0_state(300.0), [])
    _, p1 = c.step(2 * 10 ** 9, "hk", *_c0_state(294.0), [], collect=True)
    w = QI + BI * 294.0
    assert abs(p1[0][1] - 0.45 * w / 294.0) < 1e-9 and c.c0_stats["at_own_limit"] == 128
    # the common limit: gap term at +0.55 W plus the term at +0.45 W stays at 0.55 W
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_contra=1.0))
    c.step(10 ** 9, "hk", *_c0_state(300.0), [])
    _, p1 = c.step(2 * 10 ** 9, "hk", *_c0_state(294.0, gap_bp=200.0), [], collect=True)
    assert abs(p1[0][1] - 0.55 * w / 294.0) < 1e-9
    # ... and a gap term at -0.55 W with the term at +0.45 W nets -0.10 W
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_contra=1.0))
    c.step(10 ** 9, "hk", *_c0_state(300.0), [])
    _, p1 = c.step(2 * 10 ** 9, "hk", *_c0_state(294.0, gap_bp=-200.0), [], collect=True)
    assert abs(p1[0][1] + 0.10 * w / 294.0) < 1e-9


def test_v74_c0_contra_reference_follows_slowly_and_resets():
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_contra=1.0))
    c.step(10 ** 9, "hk", *_c0_state(300.0), [])
    rs = c.runs["hk"]
    # one scoring lookback later at the new mid: the reference has moved 1 - 1/e of the way
    _, p1 = c.step(10 ** 9 + 10800 * 10 ** 9, "hk", *_c0_state(299.4), [], collect=True)
    ref = 300.0 + (1.0 - math.exp(-1.0)) * (299.4 - 300.0)
    assert abs(rs.c0_ref[7][0] - ref) < 1e-12
    assert abs(c.c0_stats["tc_abs_w_p50"] - 0.005 * 1e4 * (ref - 299.4) / ref) < 1e-12
    # a book without a two-sided touch keeps its reference and gets no term
    config, books, accounts = _c0_state(299.4)
    books[7] = book(299.39, None, 299.4)
    c.step(2 * 10 ** 9 + 10800 * 10 ** 9, "hk", config, books, accounts, [])
    assert abs(rs.c0_ref[7][0] - ref) < 1e-12 and c.c["one_sided"] >= 1
    # a new simulation clears the references: they re-seed at the mid found, the term is 0 again
    n0 = c.c["c0_ref_seed"]
    a1, p1 = c.step(3 * 10 ** 9, "hk", *_c0_state(299.4, sim="sim_b"), [], collect=True)
    assert c.c["c0_ref_seed"] == n0 + 128 and rs.c0_ref[7][0] == 299.4 and c.c0_stats["tc_abs_w_max"] == 0.0
    # a book-count change clears them too
    c.step(4 * 10 ** 9, "hk", *_c0_state(299.4, n=128, sim="sim_b"), [])
    assert len(rs.c0_ref) == 64 and c.c["pace_reset_layout"] == 1


def test_v74_rel_first_state_seeds_flat_and_a_class_move_is_not_traded():
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    actions, _ = c.step(10 ** 9, "hk", *_rel_state(), [], collect=True)
    rs = c.runs["hk"]
    assert _c2(actions) == [] and c.c["rel_seed"] == 64 and sorted(rs.step_st) == list(range(192, 256))
    assert rs.step_st[200] == [math.log(9.0), 0.0, 0.0, rs.clock, math.log(9.0)]
    assert len([a for a in actions if a[0] == core.ACT_TAKE and a[1] < 128]) == 128      # class 0 still takes
    assert not [a for a in actions if 128 <= a[1] < 192]                                 # class 1 is left alone
    assert c.rel_stats["books"] == 64 and c.rel_stats["stepped"] == 64 and c.rel_stats["net_tgt_w"] == 0.0
    assert c.rel_stats["corr_inv_target"] is None and c.rel_stats["at_limit"] == 0
    # the whole class falls 111 bp together: no book moved against the class -> no step, no order
    for i in range(1, 6):
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={b: 8.90 for b in range(192, 256)}), [])
        assert _c2(actions) == []
    assert all(rs.step_st[b][2] == 0.0 and abs(rs.step_st[b][1]) < 1e-9 for b in range(192, 256))
    assert c.c["rel_in_band"] == 6 * 64


def test_v74_rel_buys_what_fell_against_its_class_one_clip_at_a_time():
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    rs = c.runs["hk"]
    w = 25000.0 + C1_BI * 8.91
    for i in range(1, 17):                                                 # book 200 is 100 bp below its class
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: 8.91}), [], collect=(i == 16))
        frac = rs.step_st[200][2]
        assert abs(frac - i * REL_STEP) < 1e-12                            # one step of the rate per 60 sim-s
        if frac * w < 1800.0:
            assert _c2(actions) == [], i
    assert i == 16 and 15 * REL_STEP * w < 1800.0 <= 16 * REL_STEP * w
    assert _c2(actions) == [(core.ACT_TAKE, 200, core.SIDE_BUY, 202.0202, 8.92)]          # IOC clip at the ask
    assert c.c["rel_send_buy"] == 1 and "rel_send_sell" not in c.c
    # the other 63 books sit 1.6 bp above the class mean: inside the 3 bp threshold, no step
    assert all(rs.step_st[b][2] == 0.0 for b in range(192, 256) if b != 200)
    x200 = 1e4 * (math.log(8.91 / 9.0) * 63.0 / 64.0)
    avg = x200 * (1.0 - 0.5 ** (16 * 60.0 / 2116.8))                       # sixteen equal EMA updates = one of 960 s
    assert abs(rs.step_st[200][1] - avg) < 1e-9
    assert c.rel_stats["books"] == 64 and abs(c.rel_stats["net_tgt_w"] - 16 * REL_STEP) < 1e-12
    assert abs(c.rel_stats["tgt_abs_notional_max"] - 16 * REL_STEP * w) < 1e-6
    # bought one clip: inside the band again until the target has moved another clip
    inv = {200: 202.0202}
    actions, _ = c.step(10 ** 9 + 17 * S60, "hk", *_rel_state(mids={200: 8.91}, inv=inv), [])
    assert _c2(actions) == []
    # the mirror: a book 100 bp above its class is sold, one clip at the bid
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    for i in range(1, 17):
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: 9.09}), [])
    w = 25000.0 + C1_BI * 9.09
    assert 15 * REL_STEP * w < 1800.0 <= 16 * REL_STEP * w
    assert _c2(actions) == [(core.ACT_TAKE, 200, core.SIDE_SELL, 198.0198, 9.08)]
    assert abs(c.runs["hk"].step_st[200][2] + 16 * REL_STEP) < 1e-12


def test_v74_rel_threshold_gap_cap_and_limit():
    # inside 3 bp of its average nothing steps; beyond it the target moves
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    rs = c.runs["hk"]
    c.step(10 ** 9 + S60, "hk", *_rel_state(mids={200: 8.9975}), [])       # -2.8 bp against the class
    assert rs.step_st[200][2] == 0.0
    c.step(10 ** 9 + 2 * S60, "hk", *_rel_state(mids={200: 8.9955}), [])   # -4.9 bp
    assert abs(rs.step_st[200][2] - REL_STEP) < 1e-12
    # a gap of 10,000 sim-s steps 60 s worth only (the average takes the whole gap)
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    rs = c.runs["hk"]
    c.step(10 ** 9 + 10000 * 10 ** 9, "hk", *_rel_state(mids={200: 8.91}), [])
    x200 = 1e4 * (math.log(8.91 / 9.0) * 63.0 / 64.0)
    assert abs(rs.step_st[200][2] - REL_STEP) < 1e-12
    assert abs(rs.step_st[200][1] - x200 * (1.0 - 0.5 ** (10000.0 / 2116.8))) < 1e-9
    # the limit: a fast rate reaches +-0.45 W and stays there
    c = core.TakerCore(UID, P(v7_rel_classes=2.0, v7_rel_rate_h=5.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    rs = c.runs["hk"]
    actions, _ = c.step(10 ** 9 + S60, "hk", *_rel_state(mids={200: 8.91, 201: 9.09}), [], collect=True)
    assert rs.step_st[200][2] == 0.45 and rs.step_st[201][2] == -0.45 and c.rel_stats["at_limit"] == 2
    assert abs(c.rel_stats["net_tgt_w"]) < 1e-12
    got = {a[1]: a for a in _c2(actions)}
    assert got[200][2] == core.SIDE_BUY and got[201][2] == core.SIDE_SELL and len(got) == 2
    c.step(10 ** 9 + 2 * S60, "hk", *_rel_state(mids={200: 8.91, 201: 9.09}), [])
    assert rs.step_st[200][2] == 0.45 and rs.step_st[201][2] == -0.45


def test_v74_rel_seed_adopts_the_inventory_and_sells_stop_at_the_free_base():
    # an inherited position inside the limit is adopted as the target: nothing to do
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    actions, _ = c.step(10 ** 9, "hk", *_rel_state(inv={200: 500.0, 201: -500.0}), [], collect=True)
    rs = c.runs["hk"]
    assert _c2(actions) == [] and abs(rs.step_st[200][2] - 0.09) < 1e-12 and abs(rs.step_st[201][2] + 0.09) < 1e-12
    assert abs(c.rel_stats["corr_inv_target"] - 1.0) < 1e-9 and c.rel_stats["dev_abs_notional_max"] < 1e-6
    # beyond the limit the target is the limit: the excess is sold down one clip at a time
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    actions, _ = c.step(10 ** 9, "hk", *_rel_state(inv={200: 3000.0}), [])
    assert c.runs["hk"].step_st[200][2] == 0.45
    assert _c2(actions) == [(core.ACT_TAKE, 200, core.SIDE_SELL, 200.0, 8.99)]
    # a short target beyond the endowment: the sell stops at the free base, then there is no order (never a loan)
    c = core.TakerCore(UID, P(v7_rel_classes=2.0, v7_rel_rate_h=5.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    actions, _ = c.step(10 ** 9 + S60, "hk", *_rel_state(mids={201: 9.09}, inv={201: -2000.0}, free={201: 50.0}),
                        [])
    assert _c2(actions) == [(core.ACT_TAKE, 201, core.SIDE_SELL, 50.0, 9.08)]   # target -0.45 W = -2,488 units
    actions, _ = c.step(10 ** 9 + 2 * S60, "hk", *_rel_state(mids={201: 9.09}, inv={201: -2050.0}, free={201: 0.0}),
                        [])
    assert _c2(actions) == [] and c.c["rel_no_base"] == 1


def test_v74_rel_missing_book_cleanup_and_fail_closed():
    # a book without a two-sided touch keeps its last value in the class mean: nobody else steps
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    c.step(10 ** 9, "hk", *_rel_state(mids={210: 8.80}), [])               # seeded 222 bp below the others
    rs = c.runs["hk"]
    for i in range(1, 4):
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={210: None}), [])
        assert _c2(actions) == []
    assert c.c["rel_bad_book"] == 3 and all(rs.step_st[b][2] == 0.0 for b in range(192, 256))
    assert rs.step_st[210][3] == 0 and rs.step_st[210][4] == math.log(8.80)      # not stepped since the seed
    actions, _ = c.step(10 ** 9 + 4 * S60, "hk", *_rel_state(mids={210: 8.80}), [])
    assert _c2(actions) == [] and all(rs.step_st[b][2] == 0.0 for b in range(192, 256))
    # a class-2 book with resting orders gets the cleanup cancel and no take in that state; its target still steps
    c = core.TakerCore(UID, P(v7_rel_classes=2.0, v7_rel_rate_h=5.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    actions, _ = c.step(10 ** 9 + S60, "hk", *_rel_state(mids={200: 8.91},
                                                         orders={200: [(31, 0, 8.90, 10.0)]}), [])
    assert _c2(actions) == [(core.ACT_CANCEL, 200, [31])] and c.runs["hk"].step_st[200][2] == 0.45
    actions, _ = c.step(10 ** 9 + 2 * S60, "hk", *_rel_state(mids={200: 8.91}), [])
    assert _c2(actions) == [(core.ACT_TAKE, 200, core.SIDE_BUY, 202.0202, 8.92)]
    # without the validator class list the leg is OFF (fail closed) and class 0 trades on
    c = core.TakerCore(UID, P(v7_rel_classes=2.0, v7_rel_rate_h=5.0))
    for i in range(3):
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: 8.91}, classes=False), [])
        assert _c2(actions) == [] and len([a for a in actions if a[1] < 128]) == 128
    assert c.c["rel_c2_off"] == 3 and not c.runs["hk"].step_st
    # a new simulation clears the stepping state: targets restart from the inventory found
    c = core.TakerCore(UID, P(v7_rel_classes=2.0, v7_rel_rate_h=5.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    c.step(10 ** 9 + S60, "hk", *_rel_state(mids={200: 8.91}), [])
    assert c.runs["hk"].step_st[200][2] == 0.45
    c.step(2 * 10 ** 9, "hk", *_rel_state(mids={200: 8.91}, simulation_id="sim_b"), [])
    assert c.runs["hk"].step_st[200][2] == 0.0 and c.c["rel_seed"] == 128


def test_v74_rel_leaves_class0_and_class1_decisions_unchanged():
    a = core.TakerCore(UID, P(**MKT, v7_k=0.005))
    b = core.TakerCore(UID, P(**MKT, v7_k=0.005, v7_rel_classes=2.0, v7_rel_rate_h=5.0))
    seen = 0
    for i, mids in enumerate(({}, {200: 8.91}, {200: 8.91, 230: 9.12}, {}, {201: 9.05})):
        st = _rel_state(mids=mids, bid=8.97 if i % 2 else 8.99, ask=8.99 if i % 2 else 9.01,
                        f_c1=8.98 if i % 2 else 9.0)
        xa, pa = a.step(10 ** 9 + i * S60, "hk", *st, [], collect=True)
        xb, pb = b.step(10 ** 9 + i * S60, "hk", *st, [], collect=True)
        assert [x for x in xb if x[1] < 192] == xa and pa == pb and a.mk_stats == b.mk_stats
        assert all(x[0] == core.ACT_TAKE for x in _c2(xb)) and len({x[1] for x in _c2(xb)}) == len(_c2(xb))
        seen += len(_c2(xb))
    assert seen >= 3 and a.rel_stats is None and b.rel_stats["books"] == 64


def test_v74_mk_dyn_target_steps_against_the_own_move():
    c = core.TakerCore(UID, P(v7_maker_classes=1.0, v7_mk_target=2.0))
    assert _q150(c, 10 ** 9, collect=True) == set()                        # seed: flat, target 0, in band
    rs = c.runs["hk"]
    assert c.c["mk_ref_seed"] == 64 and sorted(rs.step_st) == list(range(128, 192)) and not rs.mk_ref
    assert c.mk_stats["target"] is True and c.mk_stats["dyn"] is True and c.mk_stats["refs"] == 64
    w = 25000.0 + C1_BI * 8.98
    for i in range(1, 12):                                                 # the mid sits 22 bp below its start
        got = _q150(c, 10 ** 9 + i * S60, bid=8.97, ask=8.99, f_c1=8.98)
        assert abs(rs.step_st[150][2] - i * DYN_STEP) < 1e-12
        assert got == ({0} if i * DYN_STEP * w >= 1800.0 else set()), i    # ONE bid once a clip away, never an ask
    assert 10 * DYN_STEP * w < 1800.0 <= 11 * DYN_STEP * w and c.c["mk_quote_bid"] == 64
    assert "mk_quote_ask" not in c.c
    avg = 1e4 * math.log(8.98 / 9.0) * (1.0 - 0.5 ** (11 * 60.0 / 1058.4))
    assert abs(rs.step_st[150][1] - avg) < 1e-9
    # bought up to the target: no quote; a clip above it: the ask
    tgt_units = 11 * DYN_STEP * w / 8.98
    assert _q150(c, 10 ** 9 + 11 * S60 + 1, bid=8.97, ask=8.99, f_c1=8.98, inv_c1=tgt_units) == set()
    assert _q150(c, 10 ** 9 + 11 * S60 + 2, bid=8.97, ask=8.99, f_c1=8.98, inv_c1=tgt_units + 201.0) == {1}
    # the mirror: the mid above its average -> the target steps down -> ONE ask
    c = core.TakerCore(UID, P(v7_maker_classes=1.0, v7_mk_target=2.0))
    _q150(c, 10 ** 9)
    for i in range(1, 12):
        got = _q150(c, 10 ** 9 + i * S60, bid=9.01, ask=9.03, f_c1=9.02)
    assert got == {1} and abs(c.runs["hk"].step_st[150][2] + 11 * DYN_STEP) < 1e-12
    # class 1 has no class mean: a move of every class-1 book together is still traded (own price)
    assert c.c["mk_quote_ask"] == 64


def test_v74_mk_dyn_seed_limit_guard_and_pull():
    D = dict(v7_maker_classes=1.0, v7_mk_target=2.0)
    c = core.TakerCore(UID, P(**D))
    assert _q150(c, 10 ** 9, inv_c1=450.0) == set()                        # 4,050 notional long: adopted as target
    assert abs(c.runs["hk"].step_st[150][2] - 450.0 * C1_MID / 50000.0) < 1e-12
    c = core.TakerCore(UID, P(**D))
    assert _q150(c, 10 ** 9, inv_c1=3000.0) == {1}                         # 0.54 W > 0.45 W: only the reducing side
    assert c.runs["hk"].step_st[150][2] == 0.45
    # a fast rate reaches the limit in one step and stays there
    c = core.TakerCore(UID, P(**D, v7_mk_dyn_rate_h=5.0))
    _q150(c, 10 ** 9)
    assert _q150(c, 10 ** 9 + S60, collect=True, bid=8.97, ask=8.99, f_c1=8.98) == {0}
    w = 25000.0 + C1_BI * 8.98
    assert c.runs["hk"].step_st[150][2] == 0.45 and abs(c.mk_stats["tgt_abs_notional_max"] - 0.45 * w) < 1e-6
    assert c.mk_stats["split"]["out_bid"] == 64                            # the 5.1 split reads the stepping target too
    # the guard still withholds a bid priced more than 10 bp above f, the pull still clears the book
    assert _q150(c, 10 ** 9 + 2 * S60, bid=8.97, ask=8.99, f_c1=8.952) == set() and c.c["mk_guard_bid"] == 64
    pull_ts = 29 * 10 ** 9 + 4 * S60                                        # phase 29 of 30: the state before a step
    assert core.step_phase(pull_ts, STEP_CFG) == (30, 29)
    assert _q150(c, pull_ts, bid=8.97, ask=8.99, f_c1=8.98) == set() and c.c["mk_pull_states"] == 1
    # v7_mk_target = 1 is still the v7.3 level target: the reference dict is used, the stepping state is not
    c = core.TakerCore(UID, P(**MKT))
    _q150(c, 10 ** 9)
    assert _q150(c, 2 * 10 ** 9, collect=True, bid=8.97, ask=8.99, f_c1=8.98) == {0}
    assert len(c.runs["hk"].mk_ref) == 64 and not c.runs["hk"].step_st and c.mk_stats["dyn"] is False


def test_v74_all_legs_on_each_class_gets_its_own_kind_of_order():
    c = core.TakerCore(UID, P(**V74, v7_rel_rate_h=5.0, v7_mk_dyn_rate_h=5.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    st = _rel_state(mids={200: 8.91, 201: 9.09}, bid=8.97, ask=8.99, f_c1=8.98)
    actions, pairs = c.step(10 ** 9 + S60, "hk", *st, [], collect=True)
    c0 = [a for a in actions if a[1] < 128]
    c1 = [a for a in actions if 128 <= a[1] < 192]
    c2 = _c2(actions)
    assert len(c0) == 128 and all(a[0] == core.ACT_TAKE for a in c0)
    assert len(c1) == 64 and all(a[0] == core.ACT_QUOTE and a[2] == core.SIDE_BUY and a[4] == 8.97 for a in c1)
    assert sorted(a[1] for a in c2) == [200, 201] and all(a[0] == core.ACT_TAKE for a in c2)
    assert len({a[1] for a in actions}) == len(actions)                    # one instruction per book in this state
    assert len(pairs) == 128 and c.c0_stats["books"] == 128 and c.rel_stats["books"] == 64
    assert c.mk_stats["dyn"] is True and c.mk_stats["books"] == 64
    rs = c.runs["hk"]
    assert len(rs.c0_ref) == 128 and sorted(rs.step_st) == list(range(128, 256)) and not rs.mk_ref


def test_v74_agent_sends_ioc_takes_on_class2_and_logs_the_new_pictures(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path, **V74, v7_rel_rate_h=5.0)
    agent.handle(_st(*_rel_state()))
    resp = agent.handle(_st(*_rel_state(mids={200: 8.91}), ts=10 ** 9 + S60))
    if real:
        from taos.im.protocol.models import TimeInForce
        t2 = [i for i in resp.instructions if i.type == "PLACE_ORDER_LIMIT" and i.bookId >= 192]
        assert len(t2) == 1 and t2[0].bookId == 200 and t2[0].postOnly is False and t2[0].price == 8.92
        assert t2[0].timeInForce == TimeInForce.IOC and t2[0].leverage == 0.0 and t2[0].quantity == 202.0202
    else:
        t2 = [kw for kind, kw in resp.instructions if kind == "limit" and kw["book_id"] >= 192]
        assert len(t2) == 1 and t2[0]["book_id"] == 200 and t2[0]["postOnly"] is False and t2[0]["price"] == 8.92
        assert int(t2[0]["timeInForce"]) == 2 and int(t2[0]["direction"]) == 0 and t2[0]["quantity"] == 202.0202
        assert t2[0]["leverage"] == 0.0 and int(t2[0]["settlement_option"]) == -2 and "expiryPeriod" not in t2[0]
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    hp = lines[0]["params"]
    assert lines[0]["version"] == "v7.11" and lines[0]["core"] == "taker_v7_core_14"
    assert hp["rel_classes"] == [2] and hp["c0_contra"] is True and hp["mk_target"] is True and hp["mk_dyn"] is True
    assert hp["rel_hl_s"] == 2116.8 and hp["c0_ref_s"] == 10800.0 and hp["mk_dyn_hl_s"] == 1058.4
    lay = [x for x in lines if x["kind"] == "layout"][0]
    assert lay["rel_classes"] == [2] and lay["maker_classes"] == [1] and lay["c2_ok"] is True
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["errors"] == 0 and summ["rel"]["books"] == 64 and summ["rel"]["at_limit"] == 1
    assert summ["rel"]["net_tgt_w"] == 0.45 and summ["counts"]["rel_send_buy"] == 1
    assert summ["counts"]["rel_seed"] == 64 and summ["counts"]["c0_ref_seed"] == 128
    assert summ["c0_contra"]["books"] == 128 and summ["c0_contra"]["refs"] == 128
    assert summ["maker"]["dyn"] is True and "rel_c2_ledger_p50" in summ["pace"]
    assert summ["fills"]["taker"] == 0


def test_v74_handler_timing_with_every_leg():
    fx, books, accounts = _recorded_state()
    config = C1CFG()
    config.update(STEP_CFG)
    core_ = core.TakerCore(UID, P(**V74))
    times = []
    ts = fx["timestamp"]
    for i in range(300):
        t0 = time.perf_counter()
        core_.step(ts + i * 10 ** 9, "hk", config, books, accounts, [], collect=(i % 60 == 59))
        times.append((time.perf_counter() - t0) * 1000.0)
    p50, p95 = core.pctl(times, 0.5), core.pctl(times, 0.95)
    print(f"TIMING core.step 256 books (v7.4: 128 taker with the term + 64 maker stepping + 64 stepping taker): "
          f"p50 {p50:.3f} ms p95 {p95:.3f} ms")
    rs = core_.runs["hk"]
    assert p95 < 50.0 and len(rs.c0_ref) == 128 and len(rs.step_st) == 128 and not rs.mk_ref


# ---- v7.4 review fixes (independent review 11-13): the seed waits, f is not needed, finite guards, untested paths ----

def test_v74_rel_seed_waits_until_the_inventory_can_be_read():
    # the account of book 200 is absent on the first state: its price baseline is set, its target is NOT seeded
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    config, books, accounts = _rel_state(inv={200: 2000.0})
    del accounts[UID][200]
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    rs = c.runs["hk"]
    assert _c2(actions) == [] and c.c["rel_seed_wait"] == 1 and c.c["rel_seed"] == 64
    assert rs.step_st[200][:3] == [math.log(9.0), None, 0.0] and c.rel_stats["stepped"] == 63
    # ... then the inventory found (2,000 units = 0.36 W) is adopted as the target: nothing is sold
    actions, _ = c.step(10 ** 9 + S60, "hk", *_rel_state(inv={200: 2000.0}), [])
    assert _c2(actions) == [] and abs(rs.step_st[200][2] - 0.36) < 1e-12 and rs.step_st[200][1] == 0.0
    assert c.c["rel_seed_wait"] == 1
    # no initial base, or no wealth figure at all: the same wait
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    config, books, accounts = _rel_state(inv={200: 2000.0})
    accounts[UID][200]["bb"]["i"] = None
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert _c2(actions) == [] and c.c["rel_seed_wait"] == 1 and c.runs["hk"].step_st[200][1] is None
    config, books, accounts = _rel_state(inv={200: 2000.0})
    del config["miner_wealth"]
    accounts[UID][200]["qb"]["i"] = None
    actions, _ = c.step(10 ** 9 + S60, "hk", config, books, accounts, [])
    assert _c2(actions) == [] and c.c["rel_seed_wait"] == 2 and c.runs["hk"].step_st[200][1] is None
    actions, _ = c.step(10 ** 9 + 2 * S60, "hk", *_rel_state(inv={200: 2000.0}), [])
    assert _c2(actions) == [] and abs(c.runs["hk"].step_st[200][2] - 0.36) < 1e-12
    # a SEEDED book whose account is absent keeps stepping its target and sends nothing
    c = core.TakerCore(UID, P(v7_rel_classes=2.0, v7_rel_rate_h=5.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    config, books, accounts = _rel_state(mids={200: 8.91})
    del accounts[UID][200]
    actions, _ = c.step(10 ** 9 + S60, "hk", config, books, accounts, [])
    assert 200 not in {a[1] for a in actions} and c.runs["hk"].step_st[200][2] == 0.45
    assert c.c["rel_no_book_account"] == 1 and "rel_seed_wait" not in c.c


def test_v74_rel_trades_without_a_fundamental():
    p = P()
    rule = (0.0, 0.45, 1800.0)
    args = (BI, BI, QI, BI, QI, 0.0003, p, GRID, None)
    want = core.decide(299.99, 300.01, 310.0, *args, rule=rule, tfrac=0.04)
    assert want[0] == "send"
    for f in (None, 0.0, -1.0, float("nan"), "x"):
        assert core.decide(299.99, 300.01, f, *args, rule=rule, tfrac=0.04) == want       # f is not read
        assert core.decide(299.99, 300.01, f, *args)[0] == "no_f"                         # the gap rule needs it
        assert core.decide(299.99, 300.01, f, *args, add_frac=0.05)[0] == "no_f"
    # the leg: class-2 books without a fundamental are stepped and taken like the others
    c = core.TakerCore(UID, P(v7_rel_classes=2.0, v7_rel_rate_h=5.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    config, books, accounts = _rel_state(mids={200: 8.91, 201: 8.91})
    books[200]["f"] = None
    books[201]["f"] = 0.0
    actions, _ = c.step(10 ** 9 + S60, "hk", config, books, accounts, [])
    got = {a[1]: a for a in _c2(actions)}
    assert got[200] == (core.ACT_TAKE, 200, core.SIDE_BUY, 202.0202, 8.92)
    assert got[201] == (core.ACT_TAKE, 201, core.SIDE_BUY, 202.0202, 8.92) and "rel_no_f" not in c.c


def test_v74_rel_missing_book_keeps_its_last_move_in_the_class_mean():
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    rs = c.runs["hk"]
    c.step(10 ** 9 + S60, "hk", *_rel_state(mids={210: 8.10}), [])         # book 210 falls 1,054 bp: mean -16.5 bp
    x = -1e4 * math.log(8.10 / 9.0) / 64.0                                 # the other 63 books against that mean
    q = 0.5 ** (60.0 / 2116.8)
    assert abs(rs.step_st[200][1] - x * (1.0 - q)) < 1e-9 and abs(rs.step_st[200][2] + REL_STEP) < 1e-12
    actions, _ = c.step(10 ** 9 + 2 * S60, "hk", *_rel_state(mids={210: None}), [])      # 210: no two-sided touch
    assert rs.step_st[210][4] == math.log(8.10) and c.c["rel_bad_book"] == 1 and _c2(actions) == []
    # its last move stays in the mean: the others are still 16.5 bp above it and step again (dropping the book
    # would put them back on the mean: no second step, and the average falling back)
    assert abs(rs.step_st[200][1] - x * (1.0 - q * q)) < 1e-9 and abs(rs.step_st[200][2] + 2 * REL_STEP) < 1e-12


def test_v74_stepping_state_is_cleared_when_the_book_count_changes():
    c = core.TakerCore(UID, P(**V74, v7_rel_rate_h=5.0, v7_mk_dyn_rate_h=5.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    c.step(10 ** 9 + S60, "hk", *_rel_state(mids={200: 8.91}, bid=8.97, ask=8.99, f_c1=8.98), [])
    rs = c.runs["hk"]
    assert rs.step_st[200][2] == 0.45 and rs.step_st[150][2] == 0.45 and len(rs.step_st) == 128
    c.step(10 ** 9 + 2 * S60, "hk", *_c0_state(300.0, n=128), [])          # 128 books: the old ids mean other books
    assert not rs.step_st and len(rs.c0_ref) == 64 and c.c["pace_reset_layout"] == 1
    # back on 256 books every stepping book seeds again from the inventory found
    n0 = c.c["rel_seed"]
    c.step(10 ** 9 + 3 * S60, "hk", *_rel_state(mids={200: 8.91}), [])
    assert c.c["rel_seed"] == n0 + 64 and rs.step_st[200][2] == 0.0 and rs.step_st[150][2] == 0.0


def test_v74_rel_respects_the_pace_guard_and_the_free_quote():
    c = core.TakerCore(UID, P(v7_rel_classes=2.0, v7_rel_rate_h=5.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    config, books, accounts = _rel_state(mids={200: 8.91, 201: 8.91, 202: 8.91, 203: 8.91})
    accounts[UID][200]["v"] = 499_000.0                                    # the validator's rolling volume: capped
    accounts[UID][201]["qb"]["f"] = 900.0                                  # little free quote: the buy is clamped
    accounts[UID][202]["qb"]["f"] = 0.0
    actions, _ = c.step(10 ** 9 + S60, "hk", config, books, accounts, [])
    got = {a[1]: a for a in _c2(actions)}
    assert 200 not in got and c.c["rel_paced_ledger"] == 1
    afford = core.floor_to(900.0 / (8.92 * (1.0 + 0.0003 + c.p["fee_margin"])), 4)
    assert got[201] == (core.ACT_TAKE, 201, core.SIDE_BUY, afford, 8.92) and 0.0 < afford < 202.0202
    assert 202 not in got and c.c["rel_no_quote"] == 1
    assert got[203] == (core.ACT_TAKE, 203, core.SIDE_BUY, 202.0202, 8.92)
    assert all(a[0] == core.ACT_TAKE for a in got.values())


def test_v74_rel_quiet_time_inside_the_threshold_is_not_stepped_later():
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    rs = c.runs["hk"]
    c.step(10 ** 9 + S60, "hk", *_rel_state(), [])                         # 60 sim-s on the class mean: no step
    assert rs.step_st[200][2] == 0.0 and rs.step_st[200][3] == rs.clock
    c.step(10 ** 9 + S60 + 10 * 10 ** 9, "hk", *_rel_state(mids={200: 8.91}), [])
    assert abs(rs.step_st[200][2] - 0.0209 * 10.0 / 529.2) < 1e-12         # 10 sim-s of the rate, not 60


def test_v74_mk_dyn_target_without_wealth_or_inventory_waits():
    c = core.TakerCore(UID, P(v7_maker_classes=1.0, v7_mk_target=2.0))
    config, books, accounts = _mk_state()
    del config["miner_wealth"]
    accounts[UID][150]["qb"]["i"] = None
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    rs = c.runs["hk"]
    assert 150 not in rs.step_st and 151 in rs.step_st and c.c["mk_no_wealth"] == 1
    assert not [a for a in actions if a[1] == 150]                         # target 0, flat: nothing to do
    # no initial base: the inventory cannot be read, so the seed waits and the book is not quoted
    config, books, accounts = _mk_state(inv_c1=450.0)
    accounts[UID][150]["bb"]["i"] = None
    actions, _ = c.step(10 ** 9 + S60, "hk", config, books, accounts, [])
    assert 150 not in rs.step_st and c.c["mk_seed_wait"] == 1 and c.c["mk_no_initial"] == 1
    assert not [a for a in actions if a[0] == core.ACT_QUOTE and a[1] == 150]
    # readable again: the 450 units found (4,050 notional) are adopted as the target, nothing is quoted
    assert _q150(c, 10 ** 9 + 2 * S60, inv_c1=450.0) == set()
    assert abs(rs.step_st[150][2] - 450.0 * C1_MID / 50000.0) < 1e-12 and c.c["mk_seed_wait"] == 1


def test_v74_huge_numbers_never_leave_a_broken_stepping_state():
    c = core.TakerCore(UID, P(v7_rel_classes=2.0))
    config, books, accounts = _rel_state()
    accounts[UID][200]["qb"]["i"] = 1e308                                  # W of book 200 overflows on its seed state
    accounts[UID][200]["bb"]["i"] = 1e308
    books[201] = book(1e308, 1.5e308, 9.6)                                 # a mid that overflows
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    rs = c.runs["hk"]
    assert _c2(actions) == [] and 201 not in rs.step_st and rs.step_st[200][1] is None
    assert c.c["rel_seed_wait"] == 1 and c.c["rel_bad_book"] == 1
    actions, _ = c.step(10 ** 9 + S60, "hk", *_rel_state(), [])
    assert _c2(actions) == [] and len(rs.step_st) == 64
    assert all(math.isfinite(x) for e in rs.step_st.values() for x in e)
    # the helpers: an overflowing W or inventory is "cannot be read", never a NaN target
    big = (2777.0, 2777.0, 1e308, 25000.0, 1e308, 0.0003, 0.0)
    assert core.TakerCore._inv_frac(big, 9.0, 50000.0) is None
    assert core.TakerCore._inv_frac((1e307, 0.0, 1e307, 0.0, 1.7e308, 0.0003, 0.0), 9.0, None) is None   # W = inf
    assert core.TakerCore._inv_frac((1e308, 0.0, -1e308, 0.0, None, 0.0003, 0.0), 9.0, 50000.0) is None
    assert core.TakerCore._inv_frac((2977.0, 0.0, 2777.0, 0.0, None, 0.0003, 0.0), 9.0, float("inf")) is None
    assert abs(core.TakerCore._inv_frac((2977.0, 0.0, 2777.0, 0.0, None, 0.0003, 0.0), 9.0, 50000.0) - 0.036) < 1e-12
    m = core.TakerCore(UID, P(v7_maker_classes=1.0, v7_mk_target=2.0))
    st = {}
    assert m._mk_dyn_target(st, 150, 9.0, big, 10 ** 9, 50000.0) == 0.0 and not st and m.c["mk_no_wealth"] == 1
    assert m._mk_dyn_target(st, 150, 9.0, (1e308, 0.0, -1e308, 0.0, None, 0.0003, 0.0), 10 ** 9, 50000.0) == 0.0
    assert not st and m.c["mk_seed_wait"] == 1
    assert m._mk_dyn_target(st, 150, float("inf"), (2977.0, 0.0, 2777.0, 0.0, None, 0.0003, 0.0), 10 ** 9,
                            50000.0) == 0.0 and not st


def test_v74_switches_off_create_no_v74_state():
    c = core.TakerCore(UID, P(**MKT, v7_k=0.005))
    for i in range(3):
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: 8.91}), [], collect=True)
        assert _c2(actions) == []
    rs = c.runs["hk"]
    assert not rs.step_st and not rs.c0_ref and c.rel_stats is None and c.c0_stats is None
    assert not [k for k in c.c if k.startswith("rel_") or k in ("c0_ref_seed", "mk_seed_wait")]
    assert len(rs.mk_ref) == 64 and c.mk_stats["dyn"] is False


# ---- v7.5: class-2 band / two-term forms, class-1 band target (Round 28 C2 / C1) ------------------------------------
BAND = dict(v7_rel_classes=2.0, v7_rel_form="band")
T9 = dict(v7_rel_classes=2.0, v7_rel_form="t9", v7_rel_hl_s=529.2, v7_rel_rate_h=0.0321, v7_rel_clip_notional=930.0)
K2 = dict(v7_maker_classes=1.0, v7_mk_target=3.0, v7_mk_dyn_hl_s=2116.8, v7_mk_l=0.70, v7_mk_notional=1500.0,
          v7_mk_guard_bp=0.0)
V75 = dict(v7_k=0.005, v7_c0_contra=1.0, **BAND, **K2)


def _band_ref(xs, hl, mult=2.0, lim=0.45, dt=60.0):
    """The replays' band rule (round28/c2/py/c2_footing.py dev + band) on one book's x path: (avg, size, side) each."""
    out = []
    avg, m, side = xs[0], 0.0, 0.0
    out.append((avg, m, side))
    for x in xs[1:]:
        a = 1.0 - 0.5 ** (dt / hl)
        avg += a * (x - avg)
        d = avg - x
        m += a * (abs(d) - m)
        if abs(d) > max(mult * m, 1.0):
            side = lim if d > 0 else -lim
        out.append((avg, m, side))
    return out


def test_v75_params_forms_bounds_and_defaults():
    import pytest
    assert core.V7_VERSION == "v7.11" and core.CORE_VERSION == "taker_v7_core_14"
    p = P()
    assert p["rel_form"] == "step" and p["mk_band"] is False                      # the code defaults are v7.4
    assert p["rel_band_mult"] == 2.0 == p["mk_band_mult"] and p["rel_mom"] == 0.0037 and core.BAND_FLOOR_BP == 1.0
    q = P(**V75)
    assert q["rel_form"] == "band" and q["mk_band"] is True and q["mk_dyn"] is False and q["mk_target"] is True
    assert P(v7_rel_form=" T9 ")["rel_form"] == "t9" and P(v7_rel_form="step")["rel_form"] == "step"
    assert P(v7_mk_target=2.0)["mk_band"] is False and P(v7_mk_target=2.0)["mk_dyn"] is True
    for bad in ({"v7_rel_form": "ramp"}, {"v7_rel_form": 2.0}, {"v7_rel_form": True}, {"v7_rel_form": ""},
                {"v7_mk_target": 4}, {"v7_mk_target": 2.5}, {"v7_rel_band_mult": 0}, {"v7_rel_band_mult": 101},
                {"v7_mk_band_mult": 0}, {"v7_rel_mom": -0.001}, {"v7_rel_mom": 1.5}):
        with pytest.raises(ValueError):
            core.parse_params(bad)


def test_v75_rel_band_holds_the_seed_in_the_warm_up_then_trades_at_the_bound():
    c = core.TakerCore(UID, P(**BAND))
    actions, _ = c.step(10 ** 9, "hk", *_rel_state(), [], collect=True)
    rs = c.runs["hk"]
    assert _c2(actions) == [] and c.c["rel_seed"] == 64 and c.rel_stats["warm"] == 64
    assert c.rel_stats["form"] == "band"
    e = rs.step_st[200]
    assert len(e) == 8 and e[1] == 0.0 and e[2] == 0.0 and e[5] == 0.0 and e[6] == 0.0 and e[7] == e[3] == rs.clock
    mids = {200: 8.91, 201: 9.09}                                          # one book 100 bp down, one up: mean ~0
    for i in range(1, 36):                                                 # 35 x 60 s = 2,100 s < one half-life
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids=mids), [], collect=(i == 35))
        assert _c2(actions) == [], i                                       # warm-up: the seed (flat) is held
    assert rs.step_st[200][6] == 0.45 and rs.step_st[201][6] == -0.45      # the sides already sit at the bound
    assert all(rs.step_st[b][6] == 0.0 for b in range(192, 256) if b not in mids)
    assert c.rel_stats["warm"] == 64 and c.rel_stats["net_tgt_w"] == 0.0
    actions, _ = c.step(10 ** 9 + 36 * S60, "hk", *_rel_state(mids=mids), [], collect=True)   # 2,160 s: over
    got = {a[1]: a for a in _c2(actions)}
    assert sorted(got) == [200, 201] and all(a[0] == core.ACT_TAKE for a in got.values())
    assert got[200][2] == core.SIDE_BUY and got[200][4] == 8.92 and got[201][2] == core.SIDE_SELL
    assert got[201][4] == 9.08 and c.rel_stats["warm"] == 0 and c.rel_stats["at_limit"] == 2
    assert abs(c.rel_stats["net_tgt_w"]) < 1e-12 and c.c["rel_send_buy"] == 1 and c.c["rel_send_sell"] == 1


def test_v75_rel_band_state_follows_the_replay_formula_on_a_path():
    path = [9.0, 8.95, 8.91, 8.90, 8.93, 8.99, 9.05, 9.08, 9.04, 9.00, 8.97, 8.97, 8.97, 9.10, 9.12] * 3
    hl = 300.0
    c = core.TakerCore(UID, P(**BAND, v7_rel_hl_s=hl))
    xs = []
    for i, mid in enumerate(path):
        c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: mid}), [])
        r200 = 1e4 * math.log(mid / 9.0)
        xs.append(r200 - r200 / 64.0)                                      # the class mean holds book 200's move / 64
    ref = _band_ref(xs, hl)
    e = c.runs["hk"].step_st[200]
    assert abs(e[1] - ref[-1][0]) < 1e-8 and abs(e[5] - ref[-1][1]) < 1e-8 and e[6] == ref[-1][2]
    sides = [s for _, _, s in ref]
    assert 0.45 in sides and -0.45 in sides                                # the path flips the side both ways
    held = [i for i in range(1, len(xs)) if abs(ref[i][0] - xs[i]) <= max(2 * ref[i][1], 1.0) and sides[i] != 0.0]
    assert held                                                            # and holds it inside the band
    # step by step on a fresh core
    c = core.TakerCore(UID, P(**BAND, v7_rel_hl_s=hl))
    for i, mid in enumerate(path):
        c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: mid}), [])
        e = c.runs["hk"].step_st[200]
        assert abs(e[1] - ref[i][0]) < 1e-8 and abs(e[5] - ref[i][1]) < 1e-8 and e[6] == ref[i][2], i


def test_v75_rel_band_is_demeaned_across_the_class_and_never_shorts_past_the_base():
    c = core.TakerCore(UID, P(**BAND))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    rs = c.runs["hk"]
    for i in range(1, 37):                                                 # only book 200 falls: the rest rose 1.6 bp
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: 8.91}), [], collect=(i == 36))
    sides = [rs.step_st[b][6] for b in range(192, 256)]
    assert sides.count(0.45) == 1 and sides.count(-0.45) == 63 and rs.step_st[200][6] == 0.45
    ms = (0.45 - 63 * 0.45) / 64.0                                         # book 200 clipped at +0.45, the others
    assert [a[1] for a in _c2(actions)] == [200]                           # at -0.45 - ms = -0.014 W: inside a clip
    assert abs(c.rel_stats["net_tgt_w"] - (0.45 + 63 * (-0.45 - ms))) < 1e-12

    def low(mids):                                                         # book 201 holds only 500 base units
        config, books, accounts = _rel_state(mids=mids)
        accounts[UID][201]["bb"].update({"t": 0.0, "f": 0.0, "i": 500.0})
        return config, books, accounts
    c = core.TakerCore(UID, P(**BAND))
    c.step(10 ** 9, "hk", *low({}), [])
    for i in range(1, 40):
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *low({200: 8.91, 201: 9.09}), [], collect=(i == 39))
    assert c.runs["hk"].step_st[201][6] == -0.45 and not [a for a in _c2(actions) if a[1] == 201]
    assert "rel_no_base" not in c.c                                        # the target stopped at the base held
    bs = 500.0 * 9.09 / (25000.0 + 500.0 * 9.09)
    assert abs(c.rel_stats["net_tgt_w"] - (0.45 - bs)) < 1e-12


def test_v75_rel_t9_is_the_stepper_minus_the_momentum_term():
    c = core.TakerCore(UID, P(**T9))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    rs = c.runs["hk"]
    assert len(rs.step_st[200]) == 5                                       # the v7.4 stepper state
    hl, step = 529.2, 0.0321 * 60.0 / 529.2
    r200 = 1e4 * math.log(8.91 / 9.0)
    x = {200: r200 - r200 / 64.0, 0: -r200 / 64.0}                         # book 200 and any other book
    avg = {200: 0.0, 0: 0.0}
    integ = {200: 0.0, 0: 0.0}
    first = None
    for i in range(1, 25):
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: 8.91}), [], collect=True)
        fr = {}
        for k in (200, 0):
            avg[k] += (1.0 - 0.5 ** (60.0 / hl)) * (x[k] - avg[k])
            d = avg[k] - x[k]
            if abs(d) > 3.0:
                integ[k] = max(-0.45, min(0.45, integ[k] + (step if d > 0 else -step)))
            fr[k] = max(-0.45, min(0.45, integ[k] - 0.0037 * d))           # base share 0.50 > 0.45: no tighter bound
        assert abs(rs.step_st[200][2] - integ[200]) < 1e-12 and abs(rs.step_st[200][1] - avg[200]) < 1e-9
        assert abs(c.rel_stats["net_tgt_w"] - (fr[200] + 63 * fr[0])) < 1e-9, i
        mine = [a for a in _c2(actions) if a[1] == 200]
        if first is None and mine:
            first = (i, mine[0])
    # the momentum term first SELLS the book that fell (the integrator buys it back later), one clip of 930
    assert first is not None and first[0] == 1 and first[1][2] == core.SIDE_SELL and first[1][4] == 8.90
    assert abs(first[1][3] * 8.91 - 930.0) < 0.01


def test_v75_mk_band_target_k2_warm_up_quote_share_clip_and_guard_0():
    c = core.TakerCore(UID, P(**K2))
    assert _q150(c, 10 ** 9, collect=True) == set()
    rs = c.runs["hk"]
    assert c.c["mk_ref_seed"] == 64 and len(rs.step_st[150]) == 8 and not rs.mk_ref
    assert c.mk_stats["band"] is True and c.mk_stats["dyn"] is False and c.mk_stats["warm"] == 64
    for i in range(1, 36):                         # the mid 22 bp below its start: the side goes to +L, warm-up holds 0
        assert _q150(c, 10 ** 9 + i * S60, bid=8.97, ask=8.99, f_c1=8.98) == set(), i
    assert rs.step_st[150][6] == 0.70
    assert _q150(c, 10 ** 9 + 36 * S60, collect=True, bid=8.97, ask=8.99, f_c1=8.98) == {0}
    assert c.mk_stats["warm"] == 0
    assert abs(c.mk_stats["tgt_abs_notional_max"] - 25000.0) < 1e-6       # min(0.70, quote share) x W = quote held
    actions, _ = c.step(10 ** 9 + 37 * S60, "hk", *_mk_state(bid=8.97, ask=8.99, f_c1=8.98), [])
    q = [a for a in actions if a[0] == core.ACT_QUOTE and a[1] == 150]
    assert q == [(core.ACT_QUOTE, 150, core.SIDE_BUY, core.floor_to(1500.0 / 8.98, 4), 8.97)]   # clip 1,500
    # guard 0: a bid even 0.6 bp above f is withheld (guard 10 would quote it)
    n = c.c.get("mk_guard_bid", 0)
    assert _q150(c, 10 ** 9 + 38 * S60, bid=8.97, ask=8.99, f_c1=8.9695) == set() and c.c["mk_guard_bid"] == n + 64
    # the mirror: the mid above its start -> -L, bounded at the base share -> ONE ask after the warm-up
    c = core.TakerCore(UID, P(**K2))
    _q150(c, 10 ** 9)
    for i in range(1, 37):
        got = _q150(c, 10 ** 9 + i * S60, collect=(i == 36), bid=9.01, ask=9.03, f_c1=9.02)
    assert got == {1} and c.runs["hk"].step_st[150][6] == -0.70
    assert abs(c.mk_stats["tgt_abs_notional_max"] - C1_BI * 9.02) < 1e-6  # base share x W = the base held at the mid


def test_v75_mk_band_seed_adopts_the_inventory_until_the_warm_up_ends():
    c = core.TakerCore(UID, P(**K2))
    assert _q150(c, 10 ** 9, inv_c1=450.0) == set()                        # 4,050 notional long: held as the target
    assert abs(c.runs["hk"].step_st[150][2] - 450.0 * C1_MID / 50000.0) < 1e-12
    for i in range(1, 36):
        assert _q150(c, 10 ** 9 + i * S60, inv_c1=450.0) == set(), i
    assert c.runs["hk"].step_st[150][6] == 0.0                             # a flat price never opens the band
    assert _q150(c, 10 ** 9 + 36 * S60, inv_c1=450.0) == {1}               # warm-up over: target 0 -> ONE ask


def test_v75_band_legs_leave_the_other_classes_unchanged():
    base = P(v7_k=0.005, v7_c0_contra=1.0, v7_maker_classes=1.0, v7_mk_target=2.0, v7_rel_classes=2.0)
    new = P(**V75)
    a, b = core.TakerCore(UID, base), core.TakerCore(UID, new)
    for i in range(0, 40):
        st = _rel_state(mids={200: 8.91, 201: 9.09} if i else None, bid=8.97 if i else 8.99,
                        ask=8.99 if i else 9.01, f_c1=8.98 if i else None)
        xa, _ = a.step(10 ** 9 + i * S60, "hk", *st, [])
        xb, _ = b.step(10 ** 9 + i * S60, "hk", *st, [])
        assert [x for x in xa if x[1] < 128] == [x for x in xb if x[1] < 128], i     # class 0 identical
    assert sorted(b.runs["hk"].step_st) == list(range(128, 256))
    assert all(len(b.runs["hk"].step_st[k]) == 8 for k in range(128, 256))


def test_v75_all_legs_on_each_class_gets_its_own_kind_of_order():
    c = core.TakerCore(UID, P(**V75))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    for i in range(1, 37):
        st = _rel_state(mids={200: 8.91, 201: 9.09}, bid=8.97, ask=8.99, f_c1=8.98)
        actions, pairs = c.step(10 ** 9 + i * S60, "hk", *st, [], collect=(i == 36))
    c0 = [a for a in actions if a[1] < 128]
    c1 = [a for a in actions if 128 <= a[1] < 192]
    c2 = _c2(actions)
    assert c0 and all(a[0] == core.ACT_TAKE for a in c0)
    assert len(c1) == 64 and all(a[0] == core.ACT_QUOTE and a[2] == core.SIDE_BUY and a[4] == 8.97 for a in c1)
    assert sorted(a[1] for a in c2) == [200, 201] and all(a[0] == core.ACT_TAKE for a in c2)
    assert len({a[1] for a in actions}) == len(actions)                    # one instruction per book in this state
    assert c.mk_stats["band"] is True and c.rel_stats["form"] == "band" and c.rel_stats["warm"] == 0


def test_v75_agent_logs_the_forms(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path, **V75)
    agent.handle(_st(*_rel_state()))
    agent.handle(_st(*_rel_state(mids={200: 8.91}), ts=10 ** 9 + S60))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    hp = lines[0]["params"]
    assert lines[0]["version"] == "v7.11" and lines[0]["core"] == "taker_v7_core_14"
    assert hp["rel_form"] == "band" and hp["mk_band"] is True and hp["mk_l"] == 0.70 and hp["mk_guard_bp"] == 0.0
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["errors"] == 0 and summ["rel"]["form"] == "band" and summ["rel"]["warm"] == 64
    assert summ["maker"]["band"] is True and summ["maker"]["warm"] == 64


def test_v75_handler_timing_with_every_leg():
    fx, books, accounts = _recorded_state()
    config = C1CFG()
    config.update(STEP_CFG)
    core_ = core.TakerCore(UID, P(**V75))
    times = []
    ts = fx["timestamp"]
    for i in range(300):
        t0 = time.perf_counter()
        core_.step(ts + i * 10 ** 9, "hk", config, books, accounts, [], collect=(i % 60 == 59))
        times.append((time.perf_counter() - t0) * 1000.0)
    p50, p95 = core.pctl(times, 0.5), core.pctl(times, 0.95)
    print(f"TIMING core.step 256 books (v7.5: 128 taker with the term + 64 maker band + 64 band taker): "
          f"p50 {p50:.3f} ms p95 {p95:.3f} ms")
    assert p95 < 50.0 and len(core_.runs["hk"].step_st) == 128


# ---- v7.5 review tests (independent review 11-14: paths its surviving mutants showed untested) -----------------------
def _with_base(mids, b, units, inv=0.0):
    """_rel_state with class-2 book ``b`` endowed with ``units`` of base (holding units + inv)."""
    config, books, accounts = _rel_state(mids=mids)
    accounts[UID][b]["bb"].update({"t": units + inv, "f": units + inv, "i": units})
    return config, books, accounts


def test_v75_rel_band_restart_holds_the_inherited_position_then_works_it():
    inv = {200: 0.30 * 50000.0 / 9.0}                                     # 0.30 W inherited at 9.00
    c = core.TakerCore(UID, P(**BAND))
    actions, _ = c.step(10 ** 9, "hk", *_rel_state(inv=inv), [])
    assert abs(c.runs["hk"].step_st[200][2] - 0.30) < 1e-12 and _c2(actions) == []
    for i in range(1, 36):                                                 # warm-up: the inherited 0.30 W is held
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *_rel_state(inv=inv), [])
        assert _c2(actions) == [], i
    actions, _ = c.step(10 ** 9 + 36 * S60, "hk", *_rel_state(inv=inv), [])
    assert [(a[1], a[2]) for a in _c2(actions)] == [(200, core.SIDE_SELL)]   # flat prices: side 0 -> worked to 0
    # an inherited position beyond the limit: the seed is clipped to 0.45 W, so the excess is sold at once
    big = {200: 0.60 * 50000.0 / 9.0}
    c = core.TakerCore(UID, P(**BAND))
    actions, _ = c.step(10 ** 9, "hk", *_rel_state(inv=big), [])
    assert c.runs["hk"].step_st[200][2] == 0.45 and [(a[1], a[2]) for a in _c2(actions)] == [(200, core.SIDE_SELL)]


def test_v75_rel_band_long_side_is_not_capped_by_the_quote_share():
    c = core.TakerCore(UID, P(**BAND))                                     # book 200 holds 4,000 base: quote share
    c.step(10 ** 9, "hk", *_with_base({}, 200, 4000.0), [])                # 25,000 / 60,640 = 0.41 < 0.45
    for i in range(1, 37):
        c.step(10 ** 9 + i * S60, "hk", *_with_base({200: 8.91, 201: 9.09}, 200, 4000.0), [], collect=(i == 36))
    w200 = 25000.0 + 4000.0 * 8.91
    assert abs(c.rel_stats["tgt_abs_notional_max"] - 0.45 * w200) < 1e-6   # as the replay: +L, no quote-share cap


def test_v75_rel_band_floor_is_a_floor_not_an_offset():
    c = core.TakerCore(UID, P(**BAND, v7_rel_hl_s=300.0))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    c.step(10 ** 9 + S60, "hk", *_rel_state(mids={200: 9.0012}), [])       # 1.3 bp against the class
    e = c.runs["hk"].step_st[200]
    d = e[1] - 1e4 * math.log(9.0012 / 9.0) * 63.0 / 64.0
    assert max(2.0 * e[5], 1.0) < abs(d) <= 2.0 * e[5] + 1.0               # the case that tells max() from a sum
    assert e[6] == -0.45


def test_v75_band_multiples_and_half_lives_are_per_leg():
    # class 2 with its own multiple (1.0) while the class-1 multiple stays 2.0
    path = [9.0, 8.95, 8.91, 8.90, 8.93, 8.99, 9.05, 9.08, 9.04, 9.00, 8.97, 8.97, 8.97, 9.10, 9.12] * 3
    hl = 300.0
    c = core.TakerCore(UID, P(**BAND, v7_rel_hl_s=hl, v7_rel_band_mult=1.0, v7_maker_classes=1.0, v7_mk_target=3.0))
    xs = []
    for i, mid in enumerate(path):
        c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: mid}), [])
        r200 = 1e4 * math.log(mid / 9.0)
        xs.append(r200 - r200 / 64.0)
        e = c.runs["hk"].step_st[200]
        ref = _band_ref(xs, hl, mult=1.0)[-1]
        assert abs(e[1] - ref[0]) < 1e-8 and abs(e[5] - ref[1]) < 1e-8 and e[6] == ref[2], i
    assert [s for *_, s in _band_ref(xs, hl, 1.0)] != [s for *_, s in _band_ref(xs, hl, 2.0)]   # the path tells them apart
    # class 1 with its own multiple (3.0) and half-life (1,000 sim-s), while class 2 keeps 2.0 / 2,116.8
    hl1, m1 = 1000.0, 3.0
    c = core.TakerCore(UID, P(**dict(K2, v7_mk_dyn_hl_s=hl1, v7_mk_band_mult=m1, v7_rel_classes=2.0,
                                     v7_rel_form="band")))
    p1 = [9.0, 8.98, 8.95, 8.96, 9.0, 9.04, 9.07, 9.05, 9.0, 8.97, 8.97, 8.97, 9.06, 9.08, 9.02, 8.99, 8.95, 8.95,
          9.03, 9.03] * 2
    xs = []
    for i, m in enumerate(p1):
        bid, ask = round(m - 0.01, 4), round(m + 0.01, 4)
        _q150(c, 10 ** 9 + i * S60, collect=True, bid=bid, ask=ask, f_c1=m)
        xs.append(1e4 * math.log(0.5 * (bid + ask) / 9.0))
        e = c.runs["hk"].step_st[150]
        ref = _band_ref(xs, hl1, m1, 0.70)[-1]
        assert abs(e[1] - ref[0]) < 1e-8 and abs(e[5] - ref[1]) < 1e-8 and e[6] == ref[2], i
        assert c.mk_stats["warm"] == (64 if i * 60.0 < hl1 else 0), i      # the warm-up uses the class-1 half-life
    assert [s for *_, s in _band_ref(xs, hl1, 3.0, 0.70)] != [s for *_, s in _band_ref(xs, hl1, 2.0, 0.70)]


def test_v75_mk_band_target_without_wealth_or_inventory_waits():
    c = core.TakerCore(UID, P(**K2))
    config, books, accounts = _mk_state()
    del config["miner_wealth"]
    accounts[UID][150]["qb"]["i"] = None
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    rs = c.runs["hk"]
    assert 150 not in rs.step_st and 151 in rs.step_st and c.c["mk_no_wealth"] == 1
    assert not [a for a in actions if a[1] == 150]
    config, books, accounts = _mk_state(inv_c1=450.0)                     # no initial base: the seed waits
    accounts[UID][150]["bb"]["i"] = None
    actions, _ = c.step(10 ** 9 + S60, "hk", config, books, accounts, [])
    assert 150 not in rs.step_st and c.c["mk_seed_wait"] == 1
    assert not [a for a in actions if a[0] == core.ACT_QUOTE and a[1] == 150]
    assert _q150(c, 10 ** 9 + 2 * S60, inv_c1=450.0) == set()             # readable: 4,050 adopted, held
    e = rs.step_st[150]
    assert len(e) == 8 and abs(e[2] - 450.0 * C1_MID / 50000.0) < 1e-12 and e[7] == rs.clock


def test_v75_rel_t9_per_book_target_and_its_no_loan_bound():
    hl, step = 529.2, 0.0321 * 60.0 / 529.2
    r200 = 1e4 * math.log(8.91 / 9.0)
    x, xo = r200 - r200 / 64.0, -r200 / 64.0
    w200 = 25000.0 + C1_BI * 8.91
    c = core.TakerCore(UID, P(**T9))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    avg = avgo = integ = 0.0
    for i in range(1, 8):
        c.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: 8.91}), [], collect=True)
        a = 1.0 - 0.5 ** (60.0 / hl)
        avg += a * (x - avg)
        avgo += a * (xo - avgo)
        d, do = avg - x, avgo - xo
        if abs(d) > 3.0:
            integ = max(-0.45, min(0.45, integ + (step if d > 0 else -step)))
        fr = max(-0.45, min(0.45, integ - 0.0037 * d))
        fo = max(-0.45, min(0.45, -0.0037 * do))                           # |avg - x| < 3 bp: no step there
        assert abs(c.rel_stats["tgt_abs_notional_max"] - max(abs(fr) * w200, abs(fo) * 50000.0)) < 1e-6, i
    # a book endowed with only 500 base units: the short target stops at the base held
    c = core.TakerCore(UID, P(**T9))
    c.step(10 ** 9, "hk", *_with_base({}, 200, 500.0), [])
    actions, _ = c.step(10 ** 9 + S60, "hk", *_with_base({200: 8.91}, 200, 500.0), [], collect=True)
    assert abs(c.rel_stats["tgt_abs_notional_max"] - 500.0 * 8.91) < 1e-6
    got = [a for a in _c2(actions) if a[1] == 200]
    assert len(got) == 1 and got[0][2] == core.SIDE_SELL and abs(got[0][3] * 8.91 - 930.0) < 0.01


def test_v75_launcher_sets_each_forms_replayed_values():
    text = (ROOT / "run_taker_v7.sh").read_text()
    m = re.search(r'case "\$V7_REL_FORM" in\n  t9\)   REL_HL=([\d.]+); REL_RATE=([\d.]+); REL_CLIP=([\d.]+) ;;\n'
                  r'  band\) REL_HL=\$\{V7_REL_HL_BAND\}; REL_RATE=([\d.]+); REL_CLIP=([\d.]+) ;;\n'
                  r'  \*\)    REL_HL=([\d.]+); REL_RATE=([\d.]+); REL_CLIP=([\d.]+) ;;\nesac', text)
    assert m and tuple(map(float, m.groups())) == (529.2, 0.0321, 930.0, 0.0209, 1800.0, 2116.8, 0.0209, 1800.0)   # T9 / band / step
    m = re.search(r'if \[\[ "\$V7_MK_TARGET" == "3" \]\]; then\n  MK_HL=([\d.]+); MK_L=([\d.]+); '
                  r'MK_NOTIONAL=([\d.]+); MK_GUARD=([\d.]+)\nelse\n  MK_HL=([\d.]+); MK_L=([\d.]+); '
                  r'MK_NOTIONAL=([\d.]+); MK_GUARD=([\d.]+)\nfi', text)
    assert m and tuple(map(float, m.groups())) == (2116.8, 0.70, 1500.0, 0.0, 1058.4, 0.45, 1800.0, 10.0)  # K2 / v7.4
    for key in ("v7_rel_band_mult=2 ", "v7_rel_mom=0.0037", "v7_mk_band_mult=2 ", "v7_rel_form=${V7_REL_FORM}",
                "v7_mk_l=${MK_L}", "v7_mk_dyn_hl_s=${MK_HL}", "v7_rel_clip_notional=${REL_CLIP}"):
        assert key in text, key


def test_v75_mk_band_huge_numbers_never_leave_a_broken_band_state():
    m = core.TakerCore(UID, P(**K2))
    st = {}
    big = (2777.0, 2777.0, 1e308, 25000.0, 1e308, 0.0003, 0.0)              # W = 1e308 + 9e308 overflows to inf
    assert m._mk_band_target(st, 150, 9.0, big, 10 ** 9, 50000.0) == 0.0 and not st and m.c["mk_no_wealth"] == 1
    v = (2977.0, 0.0, 2777.0, 0.0, None, 0.0003, 0.0)                       # W from the config, a mid that overflowed
    assert m._mk_band_target(st, 150, float("inf"), v, 10 ** 9, 50000.0) == 0.0 and not st
    assert m._mk_band_target(st, 150, 9.0, v, 10 ** 9, float("inf")) == 0.0 and not st and m.c["mk_no_wealth"] == 3
    assert m._mk_band_target(st, 150, 9.0, (1e308, 0.0, -1e308, 0.0, None, 0.0003, 0.0), 10 ** 9, 50000.0) == 0.0
    assert not st and m.c["mk_seed_wait"] == 1                             # an overflowing inventory waits
    t = m._mk_band_target(st, 150, 9.0, v, 10 ** 9, 50000.0)               # a readable book seeds normally
    assert abs(t - 200.0 * 9.0) < 1e-6 and len(st[150]) == 8 and all(math.isfinite(x) for x in st[150])


# ---- v7.6: the two-sided makers (Round 29 C) ---------------------------------------------------------------------
V76 = dict(V75, v7_c0_maker=1.0, v7_mk_depth=1.0)
KD = dict(K2, v7_mk_depth=1.0)


def _quotes(actions, b):
    return sorted((a[2], a[4]) for a in actions if a[0] == core.ACT_QUOTE and a[1] == b)


def _fill(i, b, q, px, side=0):
    """A maker fill of ours on book b (TradeEvent, makerAgentId = our uid)."""
    return {"y": "ET", "b": b, "i": 50_000 + i, "Ta": 999, "Ma": UID, "s": side, "q": q, "p": px, "t": 1}


def test_v76_params_switches_and_bounds():
    import pytest
    assert core.V7_VERSION == "v7.11" and core.CORE_VERSION == "taker_v7_core_14"
    p = P()
    assert p["c0_maker"] is False and p["mk_depth"] is False                # the code defaults are v7.5
    assert p["c0_mk_notional"] == 1000.0 and p["c0_mk_band_w"] == 0.10 and p["mkd_start_bp"] == 2.0
    assert p["mkd_every_s"] == 600.0
    q = P(**V76)
    assert q["c0_maker"] is True and q["mk_depth"] is True and q["mk_band"] is True
    for bad in ({"v7_c0_maker": 2}, {"v7_c0_maker": "on"}, {"v7_mk_depth": 1.0},          # depth needs maker class 1
                {"v7_mk_depth": 2, "v7_maker_classes": 1.0}, {"v7_c0_mk_notional": 0}, {"v7_c0_mk_band_w": 0},
                {"v7_c0_mk_band_w": 1.5}, {"v7_mkd_start_bp": 0.4}, {"v7_mkd_start_bp": 6.5}, {"v7_mkd_every_s": 0}):
        with pytest.raises(ValueError):
            core.parse_params(bad)


def test_v76_c0_maker_quotes_the_odd_books_two_sided_and_the_taker_keeps_the_even():
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_maker=1.0))
    config, books, accounts = _state(256, classes=MKCLS())
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    c0 = [a for a in actions if a[1] < 128]
    assert {a[1] for a in c0 if a[0] == core.ACT_TAKE} == set(range(0, 128, 2))
    assert {a[1] for a in c0 if a[0] == core.ACT_QUOTE} == set(range(1, 128, 2))
    assert _quotes(actions, 1) == [(core.SIDE_BUY, 299.99), (core.SIDE_SELL, 300.01)]   # 2-tick spread: the touch
    q1 = [a for a in actions if a[0] == core.ACT_QUOTE and a[1] == 1]
    assert all(a[3] == core.floor_to(1000.0 / 300.0, 4) for a in q1)                   # 1,000 notional each
    assert c.mk_stats["c0_books"] == 64 and c.mk_stats["c0_two_sided"] == 64 and c.mk_stats["c0_pos_w_max"] == 0.0
    assert not [a for a in actions if a[1] >= 128]                                     # no maker classes: untouched
    assert len(c.pair_flags) == 64                                                     # taker pairs: even books
    # a wide spread is quoted one tick inside; beyond 0.10 W the adding side is withdrawn
    config, books, accounts = _state(256, classes=MKCLS())
    books[3] = book(299.95, 300.05, books[3]["f"])
    for b, units in ((5, 20.0), (7, -20.0), (9, 13.0)):                                # +-0.12 W, +0.078 W
        accounts[UID][b]["bb"].update({"t": BI + units, "f": BI + units})
    actions, _ = c.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    assert _quotes(actions, 3) == [(core.SIDE_BUY, 299.96), (core.SIDE_SELL, 300.04)]
    assert _quotes(actions, 5) == [(core.SIDE_SELL, 300.01)] and _quotes(actions, 7) == [(core.SIDE_BUY, 299.99)]
    assert len(_quotes(actions, 9)) == 2 and c.c["c0mk_band_bid"] == 1 and c.c["c0mk_band_ask"] == 1
    assert c.c["c0mk_inside"] == 1


def test_v76_c0_maker_follows_its_orders_and_ignores_the_step_pull():
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_maker=1.0))
    config, books, accounts = _state(256, classes=MKCLS())
    config.update(STEP_CFG)
    accounts[UID][1]["o"] = [{"y": "o", "i": 77, "c": None, "t": 1, "q": 3.3333, "s": 0, "p": 299.99, "l": 0.0},
                             {"y": "o", "i": 78, "c": None, "t": 1, "q": 3.3333, "s": 1, "p": 300.05, "l": 0.0}]
    pull_ts = 29 * 10 ** 9 + 4 * S60
    assert core.step_phase(pull_ts, STEP_CFG) == (30, 29)
    actions, _ = c.step(pull_ts, "hk", config, books, accounts, [])
    on1 = [a for a in actions if a[1] == 1]
    assert (core.ACT_CANCEL, 1, [78]) in on1                                           # off-price ask replaced
    assert [a for a in on1 if a[0] == core.ACT_QUOTE] == [(core.ACT_QUOTE, 1, core.SIDE_SELL,
                                                           core.floor_to(1000.0 / 300.0, 4), 300.01)]
    assert not [a for a in on1 if a[0] == core.ACT_TAKE] and "cancel_books" not in c.c and c.c["mk_keep"] == 1
    # a made book never takes, even far from the taker's target; the even books still take
    assert {a[1] for a in actions if a[0] == core.ACT_TAKE} == set(range(0, 128, 2))


def test_v76_depth_maker_quotes_the_odd_class1_books_and_k2_keeps_the_even():
    c = core.TakerCore(UID, P(**KD))
    actions, _ = c.step(10 ** 9, "hk", *_mk_state(f_c1=9.0), [], collect=True)
    rs = c.runs["hk"]
    # d = 2 bp: bid 8.99 x (1 - 0.0002) rounded down to 8.98, ask 9.01 x 1.0002 rounded up to 9.02
    assert all(_quotes(actions, b) == [(core.SIDE_BUY, 8.98), (core.SIDE_SELL, 9.02)] for b in range(129, 192, 2))
    assert all(_quotes(actions, b) == [] for b in range(128, 192, 2))                 # K2 in its warm-up: flat seed
    q = [a for a in actions if a[0] == core.ACT_QUOTE and a[1] == 129]
    assert all(a[3] == core.floor_to(1500.0 / 9.0, 4) for a in q)
    assert sorted(rs.mk_depth) == list(range(129, 192, 2)) and all(s[0] == 2.0 for s in rs.mk_depth.values())
    assert sorted(b for b in rs.step_st if 128 <= b < 192) == list(range(128, 192, 2))
    assert c.mk_stats["depth_books"] == 32 and c.mk_stats["depth_two_sided"] == 32
    assert c.mk_stats["depth_d_bp_p50"] == 2.0 and c.mk_stats["band"] is True
    # a long of more than one clip drops the bid; guard 0 drops a bid above f; the pull state quotes nothing
    actions, _ = c.step(2 * 10 ** 9, "hk", *_mk_state(f_c1=9.0, inv_c1=200.0), [])
    assert _quotes(actions, 129) == [(core.SIDE_SELL, 9.02)] and c.c["mkd_band_bid"] == 32
    actions, _ = c.step(3 * 10 ** 9, "hk", *_mk_state(f_c1=8.97), [])
    assert _quotes(actions, 129) == [(core.SIDE_SELL, 9.02)] and c.c["mkd_guard_bid"] == 32
    pull_ts = 29 * 10 ** 9 + 4 * S60
    actions, _ = c.step(pull_ts, "hk", *_mk_state(f_c1=9.0), [])
    assert _quotes(actions, 129) == []


def test_v76_depth_controller_moves_by_the_filled_notional_against_the_pace_line():
    c = core.TakerCore(UID, P(**KD))
    c.step(10 ** 9, "hk", *_mk_state(f_c1=9.0), [])
    rs = c.runs["hk"]
    line = 500_000.0 / 86_400.0 * 600.0                                              # 3,472 per 600 sim-s
    q = 0.9 * line / 9.0 + 1.0                                                       # just past 0.9 of the line
    c.step(10 ** 9 + 600 * 10 ** 9, "hk", *_mk_state(f_c1=9.0), [_fill(1, 129, q, 9.0)])
    assert rs.mk_depth[129][0] == 2.25 and rs.mk_depth[131][0] == 1.75 and abs(rs.mk_filled[129] - q * 9.0) < 1e-9
    assert c.c["mkd_out"] == 1
    c.step(10 ** 9 + 900 * 10 ** 9, "hk", *_mk_state(f_c1=9.0), [])                   # 300 s: no move yet
    assert rs.mk_depth[129][0] == 2.25 and rs.mk_depth[131][0] == 1.75
    q2 = 0.7 * line / 9.0                                                            # between 0.6 and 0.9: hold
    c.step(10 ** 9 + 1200 * 10 ** 9, "hk", *_mk_state(f_c1=9.0), [_fill(2, 129, q2, 9.0)])
    assert rs.mk_depth[129][0] == 2.25 and rs.mk_depth[131][0] == 1.5
    for k in range(3, 13):
        c.step(10 ** 9 + k * 600 * 10 ** 9, "hk", *_mk_state(f_c1=9.0), [])
    assert rs.mk_depth[131][0] == 0.5 and rs.mk_depth[129][0] == 0.5                  # the floor
    for k in range(13, 40):
        c.step(10 ** 9 + k * 600 * 10 ** 9, "hk", *_mk_state(f_c1=9.0),
               [_fill(100 + k, 133, 2 * line / 9.0, 9.0)])
    assert rs.mk_depth[133][0] == 6.0                                                # the ceiling
    # a taker fill of ours does not count, a new simulation restarts the controller
    n = rs.mk_filled.get(135, 0.0)
    c.step(10 ** 9 + 40 * 600 * 10 ** 9, "hk", *_mk_state(f_c1=9.0),
           [{"y": "ET", "b": 135, "i": 99_999, "Ta": UID, "Ma": 999, "s": 0, "q": 50.0, "p": 9.0, "t": 1}])
    assert rs.mk_filled.get(135, 0.0) == n
    config, books, accounts = _mk_state(f_c1=9.0, simulation_id="sim_b")
    c.step(10 ** 9 + 41 * 600 * 10 ** 9, "hk", config, books, accounts, [])
    assert all(s[0] == 2.0 for s in rs.mk_depth.values())


def test_v76_all_legs_on_each_book_gets_its_own_kind_of_order():
    c = core.TakerCore(UID, P(**V76))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    for i in range(1, 37):
        st = _rel_state(mids={200: 8.91, 201: 9.09}, bid=8.97, ask=8.99, f_c1=8.98)
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *st, [], collect=(i == 36))
    kinds = {}
    for a in actions:
        kinds.setdefault(a[1], set()).add(a[0])
    assert all(kinds.get(b) == {core.ACT_TAKE} for b in range(0, 128, 2))
    assert all(kinds.get(b) == {core.ACT_QUOTE} for b in range(1, 128, 2))
    assert all(_quotes(actions, b) == [(core.SIDE_BUY, 8.97)] for b in range(128, 192, 2))      # K2: one bid
    assert all(len(_quotes(actions, b)) == 2 for b in range(129, 192, 2))                      # depth: two-sided
    assert sorted(b for b in kinds if b >= 192) == [200, 201]
    assert all(len([a for a in actions if a[1] == b and a[0] == core.ACT_TAKE]) <= 1 for b in kinds)
    assert c.mk_stats["c0_books"] == 64 and c.mk_stats["depth_books"] == 32 and c.rel_stats["form"] == "band"
    assert c.c0_stats["books"] == 64 and len(c.runs["hk"].c0_ref) == 64                 # the term on taker books only


def test_v76_agent_logs_the_makers(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path, **V76)
    agent.handle(_st(*_rel_state()))
    agent.handle(_st(*_rel_state(mids={200: 8.91}), ts=10 ** 9 + S60))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    hp = lines[0]["params"]
    assert lines[0]["version"] == "v7.11" and lines[0]["core"] == "taker_v7_core_14"
    assert hp["c0_maker"] is True and hp["mk_depth"] is True and hp["c0_mk_notional"] == 1000.0
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["errors"] == 0 and summ["maker"]["c0_books"] == 64 and summ["maker"]["depth_books"] == 32
    assert summ["maker"]["c0_two_sided"] == 64 and summ["maker"]["depth_two_sided"] == 32


def test_v76_launcher_sets_the_maker_values():
    text = (ROOT / "run_taker_v7.sh").read_text()
    for key in ("v7_c0_maker=${V7_C0_MAKER}", "v7_c0_mk_notional=${V7_C0_MK_NOTIONAL} ", "v7_c0_mk_band_w=${V7_C0_MK_BAND_W} ",
                "v7_mk_depth=${V7_MK_DEPTH}", "v7_mkd_start_bp=2 ", "v7_mkd_every_s=600 ",
                'V7_C0_MAKER="${V7_C0_MAKER:-1}"', 'V7_MK_DEPTH="${V7_MK_DEPTH:-0}"'):  # v7.7: depth off; v7.8: c0 maker off
        assert key in text, key


def test_v76_handler_timing_with_every_leg():
    fx, books, accounts = _recorded_state()
    config = C1CFG()
    config.update(STEP_CFG)
    core_ = core.TakerCore(UID, P(**V76))
    times = []
    ts = fx["timestamp"]
    for i in range(300):
        t0 = time.perf_counter()
        core_.step(ts + i * 10 ** 9, "hk", config, books, accounts, [], collect=(i % 60 == 59))
        times.append((time.perf_counter() - t0) * 1000.0)
    p50, p95 = core.pctl(times, 0.5), core.pctl(times, 0.95)
    print(f"TIMING core.step 256 books (v7.6: 64 taker + 64 class-0 maker + 32 K2 + 32 depth maker + 64 band taker): "
          f"p50 {p50:.3f} ms p95 {p95:.3f} ms")
    assert p95 < 50.0


# ---- v7.6 review tests (independent review 11-14) -------------------------------------------------------------------
def _lv(p, q):
    return {"p": p, "q": q, "o": []}


def test_v76_makers_price_off_the_others_touch_never_off_their_own_quote():
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_maker=1.0))
    config, books, accounts = _state(256, classes=MKCLS())
    f = books[3]["f"]
    # our bid 299.96 and ask 300.04 sit alone at the top of book 3; the others are at 299.95 / 300.05
    books[3] = {"i": 0, "r": 0.4, "b": [_lv(299.96, 3.3333), _lv(299.95, 50.0)],
                "a": [_lv(300.04, 3.3333), _lv(300.05, 50.0)], "e": [], "f": f}
    accounts[UID][3]["o"] = [{"y": "o", "i": 31, "c": None, "t": 1, "q": 3.3333, "s": 0, "p": 299.96, "l": 0.0},
                             {"y": "o", "i": 32, "c": None, "t": 1, "q": 3.3333, "s": 1, "p": 300.04, "l": 0.0}]
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in actions if a[1] == 3] and c.c["mk_keep"] == 2              # kept: no walk inside ourselves
    assert core.others_touch(books[3], core.account_orders(accounts[UID][3]), 0.005) == (299.95, 300.05)
    # someone else joins our bid level: the others' best is 299.96 now, so the bid steps one tick inside it
    books[3]["b"][0] = _lv(299.96, 10.0)
    actions, _ = c.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    assert (core.ACT_CANCEL, 3, [31]) in actions and (core.SIDE_BUY, 299.97) in _quotes(actions, 3)
    # only our order on the ask side: it is kept, not walked away
    books[3] = {"i": 0, "r": 0.4, "b": [_lv(299.95, 50.0)], "a": [_lv(300.04, 3.3333)], "e": [], "f": f}
    accounts[UID][3]["o"] = [{"y": "o", "i": 32, "c": None, "t": 1, "q": 3.3333, "s": 1, "p": 300.04, "l": 0.0}]
    actions, _ = c.step(3 * 10 ** 9, "hk", config, books, accounts, [])
    assert _quotes(actions, 3) == [(core.SIDE_BUY, 299.95)] and not [a for a in actions
                                                                      if a[1] == 3 and a[0] == core.ACT_CANCEL]


def test_v76_depth_bid_follows_the_others_best_bid():
    c = core.TakerCore(UID, P(**KD))
    config, books, accounts = _mk_state(f_c1=9.0)
    books[129] = {"i": 0, "r": 0.4, "b": [_lv(8.98, 166.6666), _lv(8.97, 50.0)], "a": [_lv(9.01, 50.0)], "e": [],
                  "f": 9.0}
    accounts[UID][129]["o"] = [{"y": "o", "i": 41, "c": None, "t": 1, "q": 166.6666, "s": 0, "p": 8.98, "l": 0.0}]
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert (core.SIDE_BUY, 8.96) in _quotes(actions, 129)                               # off 8.97, not off our 8.98


def test_v76_zero_price_never_quoted_and_never_raises():
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_maker=1.0))
    config, books, accounts = _state(256, classes=MKCLS())
    books[5] = book(0.001, 0.02, books[5]["f"])                                       # the bid rounds to 0.00
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert _quotes(actions, 5) == [(core.SIDE_SELL, 0.02)] and c.c["c0mk_no_bid"] == 1
    assert len([a for a in actions if a[0] == core.ACT_TAKE]) == 64                   # the other books still act
    c = core.TakerCore(UID, P(**KD))
    config, books, accounts = _mk_state(f_c1=0.01)
    books[129] = book(0.004, 0.02, 0.01)
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert (core.SIDE_BUY, 0.0) not in _quotes(actions, 129) and c.c.get("mkd_no_bid", 0) >= 1


def test_v76_depth_maker_needs_f_and_guards_on_the_quote_price_both_sides():
    c = core.TakerCore(UID, P(**KD))
    config, books, accounts = _mk_state(f_c1=9.0)
    books[129]["f"] = None
    accounts[UID][129]["o"] = [{"y": "o", "i": 51, "c": None, "t": 1, "q": 166.0, "s": 0, "p": 8.98, "l": 0.0}]
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert _quotes(actions, 129) == [] and (core.ACT_CANCEL, 129, [51]) in actions and c.c["mkd_no_f"] == 1
    # f between the touch and our depth quotes: guard 0 reads the QUOTE price, so both sides are quoted
    actions, _ = c.step(2 * 10 ** 9, "hk", *_mk_state(f_c1=8.985), [])
    assert _quotes(actions, 129) == [(core.SIDE_BUY, 8.98), (core.SIDE_SELL, 9.02)]
    actions, _ = c.step(3 * 10 ** 9, "hk", *_mk_state(f_c1=9.015), [])
    assert _quotes(actions, 129) == [(core.SIDE_BUY, 8.98), (core.SIDE_SELL, 9.02)]
    # f above our ask: the ask is withheld; a short of more than one clip drops the ask
    actions, _ = c.step(4 * 10 ** 9, "hk", *_mk_state(f_c1=9.03), [])
    assert _quotes(actions, 129) == [(core.SIDE_BUY, 8.98)] and c.c["mkd_guard_ask"] == 32
    actions, _ = c.step(5 * 10 ** 9, "hk", *_mk_state(f_c1=9.0, inv_c1=-200.0), [])
    assert _quotes(actions, 129) == [(core.SIDE_BUY, 8.98)] and c.c["mkd_band_ask"] == 32


def test_v76_depth_controller_edges_gap_pull_and_layout():
    c = core.TakerCore(UID, P(**KD))
    c.step(10 ** 9, "hk", *_mk_state(f_c1=9.0), [])
    rs = c.runs["hk"]
    line = 500_000.0 / 86_400.0 * 600.0
    # 0.55 of the line is below 0.6: the distance moves in
    c.step(10 ** 9 + 600 * 10 ** 9, "hk", *_mk_state(f_c1=9.0), [_fill(1, 129, 0.55 * line / 9.0, 9.0)])
    assert rs.mk_depth[129][0] == 1.75
    # a 1,200-s gap: the line is 1,200 s long, so 0.95 of a 600-s line is 0.475 of it -> in, not out
    c.step(10 ** 9 + 1800 * 10 ** 9, "hk", *_mk_state(f_c1=9.0), [_fill(2, 129, 0.95 * line / 9.0, 9.0)])
    assert rs.mk_depth[129][0] == 1.5
    # the controller also steps on a pull state (nothing is quoted there)
    pull = [k for k in range(2402, 4000) if core.step_phase(k * 10 ** 9, STEP_CFG) == (30, 29)][0]
    actions, _ = c.step(pull * 10 ** 9, "hk", *_mk_state(f_c1=9.0), [_fill(3, 129, 2.0 * line / 9.0, 9.0)])
    assert _quotes(actions, 129) == [] and rs.mk_depth[129][0] == 1.75
    # a new layout (128 books: the old ids mean other books) clears the controller and its ledger
    c.step((pull + 1) * 10 ** 9, "hk", *_c0_state(300.0, n=128), [])
    assert not rs.mk_depth and not rs.mk_filled and c.c["pace_reset_layout"] == 1


def test_v76_c0_maker_has_no_fundamental_guard_and_skips_crossed_books():
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_maker=1.0))
    config, books, accounts = _state(256, classes=MKCLS())
    books[1]["f"] = 290.0                                                             # 345 bp below our bid
    books[7] = book(300.02, 300.01, books[7]["f"])                                    # crossed
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert _quotes(actions, 1) == [(core.SIDE_BUY, 299.99), (core.SIDE_SELL, 300.01)]
    assert _quotes(actions, 7) == [] and c.c["mk2_bad_book"] == 1


def test_v76_launcher_refuses_bad_maker_switches():
    text = (ROOT / "run_taker_v7.sh").read_text()
    assert re.search(r'case "\$V7_C0_MAKER" in\n  0\|1\) ;;\n  \*\) echo "ERROR: V7_C0_MAKER must be 0 or 1', text)
    assert re.search(r'case "\$V7_MK_DEPTH" in\n  0\|1\) ;;\n  \*\) echo "ERROR: V7_MK_DEPTH must be 0 or 1', text)
    assert re.search(r'if \[\[ "\$V7_MK_DEPTH" == "1" && "\$V7_MAKER_CLASSES" == "none" \]\]; then\n  echo "ERROR', text)


def test_v76_only_ours_bid_is_kept_and_our_quotes_never_lock():
    def rest(oid, s, px, q):
        return {"y": "o", "i": oid, "c": None, "t": 1, "q": q, "s": s, "p": px, "l": 0.0}
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_maker=1.0))
    config, books, accounts = _state(256, classes=MKCLS())
    f = books[3]["f"]
    books[3] = {"i": 0, "r": 0.4, "b": [_lv(299.97, 3.3333)], "a": [_lv(300.05, 50.0)], "e": [], "f": f}
    accounts[UID][3]["o"] = [rest(61, 0, 299.97, 3.3333)]
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert _quotes(actions, 3) == [(core.SIDE_SELL, 300.05)] and not [a for a in actions
                                                                      if a[1] == 3 and a[0] == core.ACT_CANCEL]
    # a stale bid of ours alone on its side, above the others' ask: never quoted against our own ask -> both pulled
    books[3] = {"i": 0, "r": 0.4, "b": [_lv(300.08, 3.3333)], "a": [_lv(300.06, 50.0), _lv(300.09, 50.0)], "e": [],
                "f": f}
    accounts[UID][3]["o"] = [rest(62, 0, 300.08, 3.3333)]
    actions, _ = c.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    assert c.c["mk2_bad_book"] == 1 and (core.ACT_CANCEL, 3, [62]) in actions        # a crossed touch: nothing quoted
    # the lock rule itself: our own bid alone at 300.12 against the others' ask 300.10 (touch read 299.90 / 300.10)
    books[3] = {"i": 0, "r": 0.4, "b": [_lv(299.90, 50.0)], "a": [_lv(300.10, 50.0)], "e": [], "f": f}
    accounts[UID][3]["o"] = [rest(65, 1, 300.12, 3.3333)]
    c2 = core.TakerCore(UID, P(v7_k=0.005, v7_c0_maker=1.0))
    want = {}
    v = core.account_view(accounts[UID][3])
    mid = c2._mk_two(c2.run_for("hk", config), 3, "c0", want, 299.90, 300.10, f, v, 2, 10 ** 9, False, None,
                     {"b": [], "a": [_lv(300.10, 50.0)]}, [(66, 0, 300.15, 3.3333)])
    assert abs(mid - 300.0) < 1e-9 and want == {} and c2.c["mk2_locked"] == 1                      # our 300.15 bid >= the ask 300.10
    # the depth maker keeps our bid when it is alone on the bid side
    c = core.TakerCore(UID, P(**KD))
    config, books, accounts = _mk_state(f_c1=9.0)
    books[129] = {"i": 0, "r": 0.4, "b": [_lv(8.96, 166.6666)], "a": [_lv(9.01, 50.0)], "e": [], "f": 9.0}
    accounts[UID][129]["o"] = [rest(71, 0, 8.96, 166.6666)]
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert _quotes(actions, 129) == [(core.SIDE_SELL, 9.02)] and not [a for a in actions
                                                                      if a[1] == 129 and a[0] == core.ACT_CANCEL]


# ---- v7.7: the class-0 maker offset trial (Round 31) ---------------------------------------------------------------
V77 = dict(V75, v7_c0_maker=1.0, v7_c0_mk_trial=1.0)
TRIAL = dict(v7_k=0.005, v7_c0_maker=1.0, v7_c0_mk_trial=1.0)


def test_v77_params_trial_switch_and_versions():
    import pytest
    assert core.V7_VERSION == "v7.11" and core.CORE_VERSION == "taker_v7_core_14"
    assert P()["c0_mk_trial"] is False                                       # the code default is v7.6
    assert P(v7_c0_mk_trial=1.0)["c0_mk_trial"] is True and P(v7_c0_mk_trial="1")["c0_mk_trial"] is True
    assert P(v7_c0_mk_trial=0.0)["c0_mk_trial"] is False and "v7_c0_mk_trial" in core.PARAM_KEYS
    for bad in ({"v7_c0_mk_trial": 2}, {"v7_c0_mk_trial": "on"}, {"v7_c0_mk_trial": -1}, {"v7_c0_mk_trial": 0.5},
                {"v7_c0_mk_trial": True}):
        with pytest.raises(ValueError):
            core.parse_params(bad)


def test_v77_trial_groups_alternate_along_the_odd_class0_books():
    odd = list(range(1, 128, 2))
    groups = [core.c0_mk_group(b) for b in odd]
    assert groups[:8] == [0, 1, 2, 3, 0, 1, 2, 3] and [groups.count(g) for g in range(4)] == [16, 16, 16, 16]
    assert core.C0_MK_TRIAL_OFFSETS == (None, 0, 1, 2)
    for g in range(4):                                                       # every group spans the class
        bs = [b for b in odd if core.c0_mk_group(b) == g]
        assert bs[0] < 8 and bs[-1] > 119


def test_v77_trial_prices_inside_at_one_and_two_ticks_behind():
    c = core.TakerCore(UID, P(**TRIAL))
    config, books, accounts = _state(256, classes=MKCLS())
    for b in (1, 3, 5, 7, 9, 11, 13, 15):                                    # 10-tick books in every group
        books[b] = book(299.95, 300.05, books[b]["f"])
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    wide = {0: (299.96, 300.04), 1: (299.95, 300.05), 2: (299.94, 300.06), 3: (299.93, 300.07)}
    for b in (1, 3, 5, 7, 9, 11, 13, 15):
        bp, ap = wide[core.c0_mk_group(b)]
        assert _quotes(actions, b) == [(core.SIDE_BUY, bp), (core.SIDE_SELL, ap)], b
    narrow = {0: (299.99, 300.01), 1: (299.99, 300.01), 2: (299.98, 300.02), 3: (299.97, 300.03)}   # 2-tick books
    for b in (17, 19, 21, 23):
        bp, ap = narrow[core.c0_mk_group(b)]
        assert _quotes(actions, b) == [(core.SIDE_BUY, bp), (core.SIDE_SELL, ap)], b
    assert c.c["c0mk_inside"] == 2 and c.c["c0mk_off0"] == 16 and c.c["c0mk_off1"] == 16 and c.c["c0mk_off2"] == 16
    assert {a[1] for a in actions if a[0] == core.ACT_TAKE and a[1] < 128} == set(range(0, 128, 2))
    q1 = [a for a in actions if a[0] == core.ACT_QUOTE and a[1] == 7]
    assert all(a[3] == core.floor_to(1000.0 / 300.0, 4) for a in q1)                # 1,000 notional as v7.6
    tr = c.mk_stats["c0_trial"]
    assert [g["off_ticks"] for g in tr] == [None, 0, 1, 2] and [g["books"] for g in tr] == [16] * 4
    assert [g["two_sided"] for g in tr] == [16] * 4 and [g["fill_notional"] for g in tr] == [0.0] * 4
    assert c.mk_stats["c0_books"] == 64 and c.mk_stats["c0_two_sided"] == 64


def test_v77_trial_off_quotes_every_maker_book_by_the_v76_rule():
    c = core.TakerCore(UID, P(v7_k=0.005, v7_c0_maker=1.0))
    config, books, accounts = _state(256, classes=MKCLS())
    for b in (1, 3, 5, 7):
        books[b] = book(299.95, 300.05, books[b]["f"])
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    for b in (1, 3, 5, 7):
        assert _quotes(actions, b) == [(core.SIDE_BUY, 299.96), (core.SIDE_SELL, 300.04)]
    for b in (9, 11, 13, 15):
        assert _quotes(actions, b) == [(core.SIDE_BUY, 299.99), (core.SIDE_SELL, 300.01)]
    assert "c0_trial" not in c.mk_stats and not [k for k in c.c if k.startswith("c0mk_off")]


def test_v77_behind_quotes_are_kept_and_follow_the_others_touch():
    c = core.TakerCore(UID, P(**TRIAL))
    config, books, accounts = _state(256, classes=MKCLS())
    f = books[7]["f"]                                                        # book 7: group 3, two ticks behind
    accounts[UID][7]["o"] = [{"y": "o", "i": 81, "c": None, "t": 1, "q": 3.3333, "s": 0, "p": 299.97, "l": 0.0},
                             {"y": "o", "i": 82, "c": None, "t": 1, "q": 3.3333, "s": 1, "p": 300.03, "l": 0.0}]
    books[7] = {"i": 0, "r": 0.4, "b": [_lv(299.99, 50.0), _lv(299.97, 3.3333)],
                "a": [_lv(300.01, 50.0), _lv(300.03, 3.3333)], "e": [], "f": f}
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in actions if a[1] == 7] and c.c["mk_keep"] == 2              # on its price: kept
    books[7]["b"][0] = _lv(300.00, 50.0)                                     # the others' bid steps up one tick
    actions, _ = c.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    assert (core.ACT_CANCEL, 7, [81]) in actions and _quotes(actions, 7) == [(core.SIDE_BUY, 299.98)]
    # our bid alone at the top is skipped by the others' touch: the quote never walks off itself
    books[7] = {"i": 0, "r": 0.4, "b": [_lv(300.00, 3.3333), _lv(299.98, 50.0)],
                "a": [_lv(300.01, 50.0), _lv(300.03, 3.3333)], "e": [], "f": f}
    accounts[UID][7]["o"] = [{"y": "o", "i": 83, "c": None, "t": 1, "q": 3.3333, "s": 0, "p": 300.00, "l": 0.0},
                             {"y": "o", "i": 82, "c": None, "t": 1, "q": 3.3333, "s": 1, "p": 300.03, "l": 0.0}]
    actions, _ = c.step(3 * 10 ** 9, "hk", config, books, accounts, [])
    assert (core.ACT_CANCEL, 7, [83]) in actions and _quotes(actions, 7) == [(core.SIDE_BUY, 299.96)]


def test_v77_band_and_two_sided_picture_hold_in_every_group():
    c = core.TakerCore(UID, P(**TRIAL))
    config, books, accounts = _state(256, classes=MKCLS())
    for b, units in ((1, 20.0), (3, -20.0), (5, 20.0), (7, -20.0)):          # +-0.12 W in groups 0, 1, 2, 3
        accounts[UID][b]["bb"].update({"t": BI + units, "f": BI + units})
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    assert _quotes(actions, 1) == [(core.SIDE_SELL, 300.01)] and _quotes(actions, 3) == [(core.SIDE_BUY, 299.99)]
    assert _quotes(actions, 5) == [(core.SIDE_SELL, 300.02)] and _quotes(actions, 7) == [(core.SIDE_BUY, 299.97)]
    assert [g["two_sided"] for g in c.mk_stats["c0_trial"]] == [15, 15, 15, 15]
    assert c.c["c0mk_band_bid"] == 2 and c.c["c0mk_band_ask"] == 2


def test_v77_trial_picture_sums_our_maker_fills_per_group():
    c = core.TakerCore(UID, P(**TRIAL))
    config, books, accounts = _state(256, classes=MKCLS())
    notes = [_fill(1, 1, 2.0, 300.0), _fill(2, 3, 1.0, 300.0, side=1), _fill(3, 9, 1.0, 300.0), _fill(4, 7, 3.0, 300.0)]
    c.step(10 ** 9, "hk", config, books, accounts, notes, collect=True)
    assert [g["fill_notional"] for g in c.mk_stats["c0_trial"]] == [900.0, 300.0, 0.0, 900.0]
    c.step(2 * 10 ** 9, "hk", config, books, accounts, [_fill(5, 5, 1.0, 300.0)], collect=True)   # cumulative
    assert [g["fill_notional"] for g in c.mk_stats["c0_trial"]] == [900.0, 300.0, 300.0, 900.0]


def test_v77_trial_picture_keeps_books_and_fills_when_a_book_is_unusable():
    c = core.TakerCore(UID, P(**TRIAL))
    config, books, accounts = _state(256, classes=MKCLS())
    c.step(10 ** 9, "hk", config, books, accounts, [_fill(1, 3, 2.0, 300.0), _fill(2, 5, 1.0, 300.0)], collect=True)
    assert [g["fill_notional"] for g in c.mk_stats["c0_trial"]] == [0.0, 600.0, 300.0, 0.0]
    for b in (3, 5):                                                         # review 11-15: empty books next state
        books[b] = {"i": 0, "r": 0.4, "b": [], "a": [], "e": [], "f": books[b]["f"]}
    c.step(2 * 10 ** 9, "hk", config, books, accounts, [], collect=True)
    tr = c.mk_stats["c0_trial"]
    assert [g["fill_notional"] for g in tr] == [0.0, 600.0, 300.0, 0.0] and [g["books"] for g in tr] == [16] * 4
    assert [g["two_sided"] for g in tr] == [16, 15, 15, 16]


def test_v77_behind_price_at_or_below_zero_is_never_quoted():
    c = core.TakerCore(UID, P(**TRIAL))
    config, books, accounts = _state(256, classes=MKCLS())
    for b in (5, 7):                                                         # groups 2 and 3 at a 0.01 bid
        books[b] = book(0.01, 0.03, 0.02)
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert not [q for b in (5, 7) for q in _quotes(actions, b) if q[0] == core.SIDE_BUY or q[1] <= 0.0]
    assert c.c["c0mk_no_bid"] == 2


def test_v77_launcher_defaults_trial_on_depth_off_and_refuses_bad_values():
    text = (ROOT / "run_taker_v7.sh").read_text()
    for key in ('V7_C0_MK_TRIAL="${V7_C0_MK_TRIAL:-0}"', 'V7_MK_DEPTH="${V7_MK_DEPTH:-0}"',
                "v7_c0_mk_trial=${V7_C0_MK_TRIAL} ", 'echo "[TakerV7] version=v7.11 '):
        assert key in text, key
    assert re.search(r'case "\$V7_C0_MK_TRIAL" in\n  0\|1\) ;;\n  \*\) echo "ERROR: V7_C0_MK_TRIAL must be 0 or 1', text)
    p = core.parse_params(_parse_kwargs_like(_launcher_params()))
    assert p["c0_mk_trial"] is False and p["mk_depth"] is False and p["c0_maker"] is True and p["mk_band"] is True


def test_v77_agent_logs_the_trial(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path, **V77)
    agent.handle(_st(*_rel_state()))
    agent.handle(_st(*_rel_state(mids={200: 8.91}), ts=10 ** 9 + S60))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    hp = lines[0]["params"]
    assert lines[0]["version"] == "v7.11" and lines[0]["core"] == "taker_v7_core_14"
    assert hp["c0_mk_trial"] is True and hp["mk_depth"] is False and hp["c0_maker"] is True
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["errors"] == 0 and summ["maker"]["c0_books"] == 64 and summ["maker"]["depth_books"] == 0
    assert [g["books"] for g in summ["maker"]["c0_trial"]] == [16, 16, 16, 16]
    assert [g["off_ticks"] for g in summ["maker"]["c0_trial"]] == [None, 0, 1, 2]


def test_v77_trial_without_the_class0_maker_changes_nothing():
    on = core.TakerCore(UID, P(**dict(K2, v7_k=0.005, v7_c0_mk_trial=1.0)))
    off = core.TakerCore(UID, P(**dict(K2, v7_k=0.005)))
    config, books, accounts = _mk_state()
    a_on, p_on = on.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    a_off, p_off = off.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    assert a_on == a_off and p_on == p_off and on.mk_stats == off.mk_stats and "c0_trial" not in on.mk_stats
    assert dict(on.c) == dict(off.c)


def test_v77_trial_moves_only_the_group_1_to_3_quotes():
    fx, books, accounts = _recorded_state()
    config = C1CFG()
    config.update(STEP_CFG)
    on = core.TakerCore(UID, P(**V77))
    off = core.TakerCore(UID, P(**dict(V77, v7_c0_mk_trial=0.0)))
    ts = fx["timestamp"]
    moved_any = set()
    for i in range(5):
        a_on, _ = on.step(ts + i * 10 ** 9, "hk", config, books, accounts, [])
        a_off, _ = off.step(ts + i * 10 ** 9, "hk", config, books, accounts, [])
        moved = {a[1] for a in a_on if a not in a_off} | {a[1] for a in a_off if a not in a_on}
        assert all(b < 128 and b % 2 == 1 and core.c0_mk_group(b) > 0 for b in moved), sorted(moved)
        moved_any |= moved
    assert moved_any                                                         # the trial does move the group 1-3 quotes


def test_v78_launch_defaults_are_s1_and_quote_no_class0_book():
    # v7.8 (Round 33 C S1 probe): the launcher defaults turn the class-0 maker and its trial off.  The parsed defaults
    # equal the v7.7 launch defaults key by key except those two switches, and a step from them quotes no maker order
    # on any class-0 book while the v7.7 defaults do (so the test bites).
    text = (ROOT / "run_taker_v7.sh").read_text()
    for key in ('V7_C0_MAKER="${V7_C0_MAKER:-1}"', 'V7_C0_MK_TRIAL="${V7_C0_MK_TRIAL:-0}"',
                'V7_MK_DEPTH="${V7_MK_DEPTH:-0}"', 'echo "[TakerV7] version=v7.11 '):
        assert key in text, key
    assert core.V7_VERSION == "v7.11" and core.CORE_VERSION == "taker_v7_core_14"
    # v7.11: the launcher defaults carry the makers ON; switching all three new switches OFF is the S1 shape (no class-0 quote).
    # The run with the trial ON besides is the v7.7 shape (placement off, trial on).
    s11 = _launcher_params()
    assert " v7_c0_maker=1 " in s11 and " v7_c0_mk_trial=0 " in s11 and " v7_mk_place_id=1 " in s11
    s78 = s11.replace(" v7_c0_maker=1 ", " v7_c0_maker=0 ").replace(" v7_c2_maker=1 ", " v7_c2_maker=0 ").replace(
        " v7_mk_place_id=1 ", " v7_mk_place_id=0 ")
    s77 = s11.replace(" v7_mk_place_id=1 ", " v7_mk_place_id=0 ").replace(" v7_c0_mk_trial=0 ", " v7_c0_mk_trial=1 ")
    p78 = core.parse_params(_parse_kwargs_like(s78))
    p77 = core.parse_params(_parse_kwargs_like(s77))
    assert p78["c0_maker"] is False and p78["c0_mk_trial"] is False and p78["mk_depth"] is False and p78["c2_maker"] is False
    assert p77["c0_maker"] is True and p77["c0_mk_trial"] is True
    sw = ("c0_maker", "c0_mk_trial", "c2_maker", "place_id")
    assert {k: v for k, v in p78.items() if k not in sw} == {k: v for k, v in p77.items() if k not in sw}
    config, books, accounts = _mk_state()
    a78, _ = core.TakerCore(UID, p78).step(10 ** 9, "hk", config, books, accounts, [])
    config, books, accounts = _mk_state()
    a77, _ = core.TakerCore(UID, p77).step(10 ** 9, "hk", config, books, accounts, [])
    assert not [b for b in range(128) if _quotes(a78, b)]
    assert [b for b in range(128) if _quotes(a77, b)]
    assert all(b % 2 == 1 for b in range(128) if _quotes(a77, b))


# ---- v7.9: the touch identity E1T (Round 35 profile_v2, PRE-REGISTERED; USER 11-16 option 1) -------------------------
V79 = dict(V75, v7_touch_id=1.0)
MKT79 = dict(MKT, v7_touch_id=1.0)
E1T_68 = (129, 130, 134, 135, 136, 138, 140, 146, 148, 151, 152, 154, 155, 156, 158, 159, 160, 161, 162, 165, 172, 175,
          177, 179, 180, 184, 186, 187, 188, 190, 191)   # class-1 books drawn 1 for uid 68, from SP/round35/spec/profile_v2.py


def _c1_quotes(actions):
    return {a[1]: (a[2], a[4]) for a in actions if a[0] == core.ACT_QUOTE and 128 <= a[1] < 192}


def test_v79_params_switch_versions_and_launcher_default():
    import pytest
    assert core.V7_VERSION == "v7.11" and core.CORE_VERSION == "taker_v7_core_14"
    assert P()["touch_id"] is False and "v7_touch_id" in core.PARAM_KEYS          # the code default is v7.8
    assert P(v7_touch_id=1.0)["touch_id"] is True and P(v7_touch_id="1")["touch_id"] is True
    assert P(v7_touch_id=0.0)["touch_id"] is False
    for bad in (2, "on", -1, 0.5, True):
        with pytest.raises(ValueError):
            core.parse_params({"v7_touch_id": bad})
    text = (ROOT / "run_taker_v7.sh").read_text()
    for key in ('V7_TOUCH_ID="${V7_TOUCH_ID:-0}"', "\nv7_touch_id=${V7_TOUCH_ID} v7_size_id=${V7_SIZE_ID} v7_size_pair=${V7_SIZE_PAIR} v7_c0_tgt_off_clips=${V7_C0_TGT_OFF} \\\n",
                'echo "[TakerV7] version=v7.11 ',
                'echo "[preflight] v7.11 PASS"'):
        assert key in text, key
    assert re.search(r'case "\$V7_TOUCH_ID" in\n  0\|1\) ;;\n  \*\) echo "ERROR: V7_TOUCH_ID must be 0 or 1', text)
    assert core.parse_params(_parse_kwargs_like(_launcher_params()))["touch_id"] is False      # v7.9.2: E1T off


def test_v79_draw_is_the_preregistered_profile_v2_e1t():
    import hashlib
    assert (core.TOUCH_ID_PROFILE, core.TOUCH_ID_DIM, core.TOUCH_ID_TICKS) == ("orthox-profile-v2", "E1T", (0, 1))
    pins = {(68, 128): 0, (68, 129): 1, (68, 150): 0, (68, 191): 1, (5, 128): 1, (245, 170): 0, (0, 140): 1,
            (255, 160): 1}
    for (u, b), v in pins.items():
        assert core.touch_id_draw(u, b) == v, (u, b)
    assert tuple(b for b in range(128, 192) if core.touch_id_draw(UID, b)) == E1T_68
    assert core.touch_id_digest(UID, range(128, 192)) == "dcfec944ececf39f"
    assert core.touch_id_digest(UID, reversed(range(128, 192))) == "dcfec944ececf39f"      # ascending by construction
    assert all(core.touch_id_draw(-1, b) == 0 for b in range(256))
    h = hashlib.sha256(b"orthox-profile-v2|E1T|68|129").hexdigest()                      # the formula itself
    assert (0, 1)[int(h[:8], 16) % 2] == 1
    diff = sum(core.touch_id_draw(68, b) != core.touch_id_draw(69, b) for b in range(128, 192))
    assert 16 <= diff <= 48                                                   # another uid draws its own table


def test_v79_drawn_books_quote_one_tick_behind_the_others_both_sides():
    on = core.TakerCore(UID, P(**MKT79))
    off = core.TakerCore(UID, P(**MKT))
    for c in (on, off):
        config, books, accounts = _mk_state()
        c.step(10 ** 9, "hk", config, books, accounts, [])                   # seed: flat, ref = mid, in band
    res = {}
    for name, c in (("on", on), ("off", off)):
        config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.98)   # the mid falls 22 bp: one bid per book
        acts, _ = c.step(2 * 10 ** 9, "hk", config, books, accounts, [], collect=True)
        res[name] = _c1_quotes(acts)
    assert len(res["off"]) == 64 and set(res["off"].values()) == {(core.SIDE_BUY, 8.97)}
    assert len(res["on"]) == 64
    for b in range(128, 192):
        assert res["on"][b] == (core.SIDE_BUY, 8.96 if b in E1T_68 else 8.97), b
    assert on.c["mk_tid_bid"] == len(E1T_68) and "mk_tid_bid" not in off.c and "mk_tid_own_bid" not in on.c
    assert on.mk_stats["touch_id"] == {"books": 64, "behind": 31, "digest": "dcfec944ececf39f"}
    assert "touch_id" not in off.mk_stats
    for name, c in (("on", on), ("off", off)):
        config, books, accounts = _mk_state(bid=9.01, ask=9.03, f_c1=9.02)   # back up and 22 bp above: one ask per book
        acts, _ = c.step(3 * 10 ** 9, "hk", config, books, accounts, [], collect=True)
        res[name] = _c1_quotes(acts)
    assert set(res["off"].values()) == {(core.SIDE_SELL, 9.03)} and len(res["on"]) == 64
    for b in range(128, 192):
        assert res["on"][b] == (core.SIDE_SELL, 9.04 if b in E1T_68 else 9.03), b
    assert on.c["mk_tid_ask"] == len(E1T_68)


def test_v79_own_resting_quote_is_kept_and_never_walks():
    c = core.TakerCore(UID, P(**MKT79))
    config, books, accounts = _mk_state()
    c.step(10 ** 9, "hk", config, books, accounts, [])
    b, b0 = E1T_68[0], 128                                                    # 129 drawn 1, 128 drawn 0
    assert c._tid_ticks(b) == 1 and c._tid_ticks(b0) == 0
    # (a) our bid rests one tick behind the others' best bid: kept, nothing cancelled or placed on the book
    config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.98, orders={b: [(7, core.SIDE_BUY, 8.96, 167.2)],
                                                                             b0: [(8, core.SIDE_BUY, 8.96, 167.2)]})
    acts, _ = c.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in acts if a[1] == b]
    assert [a for a in acts if a[1] == b0 and a[0] == core.ACT_CANCEL] and _quotes(acts, b0) == [(core.SIDE_BUY, 8.97)]
    # (b) the only bid level left is our own order: the others' best is unknown -> our price is kept, no walk down
    config, books, accounts = _mk_state(bid=8.96, ask=8.99, f_c1=8.98, orders={b: [(7, core.SIDE_BUY, 8.96, 50.0)]})
    acts, _ = c.step(3 * 10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in acts if a[1] == b] and c.c["mk_tid_own_bid"] == 1
    # (c) the others come back one tick above our order: still one tick behind them = our price, kept
    config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.98, orders={b: [(7, core.SIDE_BUY, 8.96, 50.0)]})
    acts, _ = c.step(4 * 10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in acts if a[1] == b]
    # (d) the others' best bid moves up two ticks: the quote follows to one tick behind it (cancel + new price)
    config, books, accounts = _mk_state(bid=8.98, ask=9.00, f_c1=8.99, orders={b: [(7, core.SIDE_BUY, 8.96, 50.0)]})
    acts, _ = c.step(5 * 10 ** 9, "hk", config, books, accounts, [])
    assert [a for a in acts if a[1] == b and a[0] == core.ACT_CANCEL] and _quotes(acts, b) == [(core.SIDE_BUY, 8.97)]


def test_v79_touch_id_moves_only_the_drawn_class1_quote_prices():
    legs = dict(MKT, v7_k=0.005, v7_c0_contra=1.0, **BAND)
    on, off = core.TakerCore(UID, P(**legs, v7_touch_id=1.0)), core.TakerCore(UID, P(**legs))
    moved = 0
    for i, (bid, ask, f) in enumerate(((8.99, 9.01, 9.0), (8.97, 8.99, 8.98), (8.96, 8.98, 8.97), (9.01, 9.03, 9.02),
                                       (9.03, 9.05, 9.04), (8.99, 9.01, 9.0))):
        xa = xb = None
        for c in (on, off):
            config, books, accounts = _mk_state(bid=bid, ask=ask, f_c1=f)
            acts, _ = c.step((i + 1) * 10 ** 9, "hk", config, books, accounts, [], collect=True)
            xa, xb = (acts, xb) if c is on else (xa, acts)
        assert [a for a in xa if a[1] not in E1T_68] == [a for a in xb if a[1] not in E1T_68], i   # c0, c2, undrawn c1
        qa = [a for a in xa if a[1] in E1T_68]
        qb = [a for a in xb if a[1] in E1T_68]
        assert [(a[0], a[1], a[2], a[3]) for a in qa] == [(a[0], a[1], a[2], a[3]) for a in qb], i     # same side / size
        for a, b_ in zip(qa, qb):
            assert round(b_[4] - a[4], 2) == (0.01 if a[2] == core.SIDE_BUY else -0.01), (i, a, b_)
            moved += 1
    assert moved >= 2 * len(E1T_68)
    assert on.c.get("mk_tid_bid", 0) + on.c.get("mk_tid_ask", 0) == moved


def test_v79_touch_id_without_a_target_leg_changes_nothing():
    # the flat v7.2 band (v7_mk_target 0) and a run with no maker leg keep the v7.8 prices on every book
    for legs in (dict(v7_maker_classes=1.0), dict(v7_k=0.005)):
        on, off = core.TakerCore(UID, P(**legs, v7_touch_id=1.0)), core.TakerCore(UID, P(**legs))
        for i, (bid, ask, f) in enumerate(((8.99, 9.01, 9.0), (8.97, 8.99, 8.98))):
            config, books, accounts = _mk_state(bid=bid, ask=ask, f_c1=f)
            xa, _ = on.step((i + 1) * 10 ** 9, "hk", config, books, accounts, [], collect=True)
            config, books, accounts = _mk_state(bid=bid, ask=ask, f_c1=f)
            xb, _ = off.step((i + 1) * 10 ** 9, "hk", config, books, accounts, [], collect=True)
            assert xa == xb
        assert not [k for k in on.c if k.startswith("mk_tid")] and "touch_id" not in (on.mk_stats or {})


def test_v79_agent_logs_the_touch_identity(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path, **V79)
    agent.handle(_st(*_rel_state()))
    agent.handle(_st(*_rel_state(mids={200: 8.91}), ts=10 ** 9 + S60))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    assert lines[0]["version"] == "v7.11" and lines[0]["core"] == "taker_v7_core_14"
    assert lines[0]["params"]["touch_id"] is True and lines[0]["uid"] == UID
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["errors"] == 0
    assert summ["maker"]["touch_id"] == {"books": 64, "behind": 31, "digest": "dcfec944ececf39f"}


def test_v79_guard_judges_the_quoted_price():
    # the fundamental guard bounds the price we quote: with guard 0 bp no bid above f.  The touch bid 8.97 sits above
    # f 8.965, so an undrawn book (= v7.8) does not bid; a drawn book bids one tick behind, 8.96, below f.
    c = core.TakerCore(UID, P(**dict(MKT79, v7_mk_guard_bp=0.0)))
    config, books, accounts = _mk_state()
    c.step(10 ** 9, "hk", config, books, accounts, [])
    config, books, accounts = _mk_state(bid=8.97, ask=8.99, f_c1=8.965)
    acts, _ = c.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    q = _c1_quotes(acts)
    assert q == {b: (core.SIDE_BUY, 8.96) for b in E1T_68}
    assert c.c["mk_guard_bid"] == 64 - len(E1T_68)


# ---- v7.9.2: the class-1 ORDER-SIZE identity K1Z (Round 42; USER 11-19 04:4x "accept your recommendation option") ----
MKZ = dict(MKT, v7_size_id=1.0)
K1Z_68 = tuple(b for b in range(128, 192) if core.size_id_draw(UID, b) == 2.0)
K1Z_68_N = 37                                    # OBSERVED here: drawn 2.0 books of uid 68 on 128..191


def _c1_orders(actions):
    return {a[1]: (a[2], a[3], a[4]) for a in actions if a[0] == core.ACT_QUOTE and 128 <= a[1] < 192}


def _flat_step(core_, inv_c1=0.0, mutate=None, bid=8.99, ask=9.01, f=9.0, seed_ts=10 ** 9, ts=2 * 10 ** 9):
    """Seed at the mid (flat reference), then one state at the same mid with class-1 inventory ``inv_c1`` base units:
    the level target is 0, so the deviation is the inventory x mid."""
    config, books, accounts = _mk_state()
    core_.step(seed_ts, "hk", config, books, accounts, [])
    config, books, accounts = _mk_state(bid=bid, ask=ask, f_c1=f, inv_c1=inv_c1)
    if mutate:
        mutate(accounts)
    return core_.step(ts, "hk", config, books, accounts, [], collect=True)[0]


def test_v791_versions_switch_and_launcher_defaults():
    import pytest
    assert core.V7_VERSION == "v7.11" and core.CORE_VERSION == "taker_v7_core_14"
    assert P()["size_id"] is False and "v7_size_id" in core.PARAM_KEYS and P()["touch_id"] is False   # code default off
    assert P(v7_size_id=1.0)["size_id"] is True and P(v7_size_id="1")["size_id"] is True
    assert P(v7_size_id=0.0)["size_id"] is False
    for bad in (2, "on", -1, 0.5, True):
        with pytest.raises(ValueError):
            core.parse_params({"v7_size_id": bad})
    text = (ROOT / "run_taker_v7.sh").read_text()
    for key in ('V7_SIZE_ID="${V7_SIZE_ID:-1}"', 'V7_TOUCH_ID="${V7_TOUCH_ID:-0}"', " v7_size_id=${V7_SIZE_ID} ",
                'echo "[preflight] v7.11 PASS"'):
        assert key in text, key
    assert re.search(r'case "\$V7_SIZE_ID" in\n  0\|1\) ;;\n  \*\) echo "ERROR: V7_SIZE_ID must be 0 or 1', text)
    assert re.search(r'if \[\[ "\$V7_SIZE_ID" == "1" && "\$V7_TOUCH_ID" == "1" \]\]; then\n  echo "ERROR:[^\n]*\n  exit 2\nfi',
                     text)
    p = core.parse_params(_parse_kwargs_like(_launcher_params()))
    assert p["size_id"] is True and p["touch_id"] is False and p["mk_notional"] == 1500.0 and p["mk_band_clips"] == 1.0


def test_v791_draw_digest_and_negative_uid_are_the_preregistered_k1z():
    import hashlib
    assert (core.SIZE_ID_PROFILE, core.SIZE_ID_DIM, core.SIZE_ID_MULTS) == ("orthox-profile-v3", "K1Z", (1.0, 2.0))
    assert core.size_id_digest(235, range(128, 192)) == "eaf96ef1c9384f03"
    assert sum(core.size_id_draw(235, b) == 2.0 for b in range(128, 192)) == 38
    assert core.size_id_digest(99, range(128, 192)) == "7d2b8fce981b81d4"
    assert sum(core.size_id_draw(99, b) == 2.0 for b in range(128, 192)) == 31
    assert core.size_id_digest(UID, reversed(range(128, 192))) == core.size_id_digest(UID, range(128, 192))
    assert core.size_id_digest(UID, range(128, 192)) == "b81f3eacfa105a0a" and len(K1Z_68) == K1Z_68_N
    assert all(core.size_id_draw(-1, b) == 1.0 and type(core.size_id_draw(-1, b)) is float for b in range(256))
    assert core.size_id_digest(-1, range(128, 130)) == hashlib.sha256(
        "orthox-profile-v3|uid=-1|K1Z\n128|K1Z=1.0\n129|K1Z=1.0".encode()).hexdigest()[:16]
    for b in (128, 129, 150, 191):                                            # the formula itself
        h = hashlib.sha256(f"orthox-profile-v3|K1Z|235|{b}".encode()).hexdigest()
        assert core.size_id_draw(235, b) == (1.0, 2.0)[int(h[:8], 16) % 2]
    diff = sum(core.size_id_draw(235, b) != core.size_id_draw(99, b) for b in range(128, 192))
    assert 16 <= diff <= 48                                                   # each uid draws its own table
    assert core.size_id_draw(UID, 128) == 2.0 and core.size_id_draw(UID, 133) == 1.0


def test_v791_only_drawn_class1_target_books_change_size_not_price():
    on = core.TakerCore(UID, P(**MKZ))
    off = core.TakerCore(UID, P(**MKT))
    ao = _flat_step(on, inv_c1=-450.0)           # dev = -4050 notional: past both bands, every class-1 book bids
    af = _flat_step(off, inv_c1=-450.0)
    assert [a for a in ao if not 128 <= a[1] < 192] == [a for a in af if not 128 <= a[1] < 192]   # c0 / c2 / c1 others
    qo, qf = _c1_orders(ao), _c1_orders(af)
    assert set(qo) == set(qf) and len(qo) == 64
    for b in range(128, 192):
        so, sf = qo[b], qf[b]
        assert so[0] == sf[0] == core.SIDE_BUY and so[2] == sf[2] == 8.99, b          # side and PRICE unchanged
        if b in K1Z_68:
            assert so[1] == core.floor_to(2.0 * 1800.0 / 9.0, 4) and abs(so[1] / sf[1] - 2.0) < 1e-3, b
        else:
            assert so[1] == sf[1], b
    assert on.c["mk_quote_bid"] == off.c["mk_quote_bid"] and not [k for k in on.c if k.startswith("mk_tid")]
    # the other side too
    on2, off2 = core.TakerCore(UID, P(**MKZ)), core.TakerCore(UID, P(**MKT))
    qo, qf = _c1_orders(_flat_step(on2, inv_c1=450.0)), _c1_orders(_flat_step(off2, inv_c1=450.0))
    for b in range(128, 192):
        assert qo[b][0] == qf[b][0] == core.SIDE_SELL and qo[b][2] == qf[b][2] == 9.01
        assert abs(qo[b][1] / qf[b][1] - (2.0 if b in K1Z_68 else 1.0)) < 1e-3, b


def test_v791_band_follows_the_per_book_clip():
    # dev = -2250 notional: past one clip (1,800, undrawn) but inside the drawn book's band (3,600)
    on, off = core.TakerCore(UID, P(**MKZ)), core.TakerCore(UID, P(**MKT))
    qo, qf = _c1_orders(_flat_step(on, inv_c1=-250.0)), _c1_orders(_flat_step(off, inv_c1=-250.0))
    assert set(qf) == set(range(128, 192))                                   # v7.8 quotes every class-1 book
    assert set(qo) == set(range(128, 192)) - set(K1Z_68)                     # drawn books are still in band
    assert on.c["mk_in_band"] == 64 + K1Z_68_N and on.c["mk_out_bid"] == 64 - K1Z_68_N      # the seed state is in band on all 64
    # the ask side: dev = +2250 -> undrawn books ask, drawn books stay in band; +4050 -> every book asks
    on = core.TakerCore(UID, P(**MKZ))
    qa = _c1_orders(_flat_step(on, inv_c1=250.0))
    assert set(qa) == set(range(128, 192)) - set(K1Z_68) and all(v[0] == core.SIDE_SELL for v in qa.values())
    on = core.TakerCore(UID, P(**MKZ))
    assert set(_c1_orders(_flat_step(on, inv_c1=450.0))) == set(range(128, 192))
    # dev = -4050: past the drawn band as well, every book quotes
    on = core.TakerCore(UID, P(**MKZ))
    assert set(_c1_orders(_flat_step(on, inv_c1=-450.0))) == set(range(128, 192))
    # a wider band multiple moves both thresholds together (band = band_clips x per-book clip)
    on, off = core.TakerCore(UID, P(**dict(MKZ, v7_mk_band_clips=2.0))), core.TakerCore(UID, P(**dict(MKT, v7_mk_band_clips=2.0)))
    assert set(_c1_orders(_flat_step(on, inv_c1=-450.0))) == set(range(128, 192)) - set(K1Z_68)       # 4050 < 7200
    assert set(_c1_orders(_flat_step(off, inv_c1=-450.0))) == set(range(128, 192))                      # 4050 > 3600


def test_v791_clamps_follow_the_bigger_clip():
    b_big, b_small = K1Z_68[0], 133
    assert core.size_id_draw(UID, b_big) == 2.0 and core.size_id_draw(UID, b_small) == 1.0

    def poor_quote(accounts):                    # quote_free 3,000: affords 2 x 1,800 only after the margin cut
        for b in (b_big, b_small):
            accounts[UID][b]["qb"]["f"] = 3000.0
    on = core.TakerCore(UID, P(**MKZ))
    q = _c1_orders(_flat_step(on, inv_c1=-450.0, mutate=poor_quote))
    afford = 3000.0 / (8.99 * (1.0 + 0.001))
    assert q[b_big][1] == core.floor_to(afford, 4) and q[b_big][1] < 2.0 * 1800.0 / 9.0
    assert q[b_small][1] == core.floor_to(1800.0 / 9.0, 4)                  # 1 x clip still affordable: not clamped

    def poor_base(accounts):
        for b in (b_big, b_small):
            accounts[UID][b]["bb"]["f"] = 100.0
    on = core.TakerCore(UID, P(**MKZ))
    q = _c1_orders(_flat_step(on, inv_c1=450.0, mutate=poor_base))
    assert q[b_big][1] == 100.0 and q[b_small][1] == 100.0                  # base bound binds both (400 / 200 wanted)

    def tiny(accounts):                          # afford below the min order on the drawn book: no quote
        accounts[UID][b_big]["qb"]["f"] = 20.0
    on = core.TakerCore(UID, P(**MKZ))
    q = _c1_orders(_flat_step(on, inv_c1=-450.0, mutate=tiny))
    assert b_big not in q and b_small in q and on.c["mk_below_min"] == 1


def test_v791_size_id_only_with_the_target_leg_and_depth_books_keep_the_clip():
    # no target leg: no K1Z (flat v7.2 band) and no summary field
    for legs in (dict(v7_maker_classes=1.0), dict(v7_k=0.005)):
        on, off = core.TakerCore(UID, P(**legs, v7_size_id=1.0)), core.TakerCore(UID, P(**legs))
        for i, (bid, ask, f) in enumerate(((8.99, 9.01, 9.0), (8.97, 8.99, 8.98))):
            config, books, accounts = _mk_state(bid=bid, ask=ask, f_c1=f)
            xa, _ = on.step((i + 1) * 10 ** 9, "hk", config, books, accounts, [], collect=True)
            config, books, accounts = _mk_state(bid=bid, ask=ask, f_c1=f)
            xb, _ = off.step((i + 1) * 10 ** 9, "hk", config, books, accounts, [], collect=True)
            assert xa == xb
        assert "size_id" not in (on.mk_stats or {})
    # the depth maker on: its odd class-1 books keep the plain clip, even books are K2 and follow the draw
    legs = dict(MKT, v7_mk_depth=1.0)
    on, off = core.TakerCore(UID, P(**legs, v7_size_id=1.0)), core.TakerCore(UID, P(**legs))
    qo, qf = _c1_orders(_flat_step(on, inv_c1=-450.0)), _c1_orders(_flat_step(off, inv_c1=-450.0))
    for b in range(128, 192):
        if b in qo and b in qf:
            assert abs(qo[b][1] / qf[b][1] - (2.0 if b % 2 == 0 and b in K1Z_68 else 1.0)) < 1e-3, b
    odd = [b for b in range(129, 192, 2) if b in qo and b in qf]
    assert odd and all(qo[b] == qf[b] for b in odd)
    assert on.mk_stats["size_id"]["books"] == 32 and on.mk_stats["size_id"]["big"] == sum(b % 2 == 0 for b in K1Z_68)


def test_v791_equal_to_v79_with_both_switches_off_and_with_e1t_only():
    # K1Z off = the v7.9 code path: identical actions, with E1T off (v7.8) and on (v7.9)
    for touch in (0.0, 1.0):
        a = core.TakerCore(UID, P(**dict(MKT, v7_touch_id=touch)))
        b_ = core.TakerCore(UID, P(**dict(MKT, v7_touch_id=touch, v7_size_id=0.0)))
        for i, (bid, ask, f, inv) in enumerate(((8.99, 9.01, 9.0, 0.0), (8.97, 8.99, 8.98, -300.0),
                                                (9.03, 9.05, 9.04, 300.0), (8.99, 9.01, 9.0, 0.0))):
            xa = _flat_step(a, inv_c1=inv, bid=bid, ask=ask, f=f, seed_ts=(2 * i + 1) * 10 ** 9, ts=(2 * i + 2) * 10 ** 9)
            xb = _flat_step(b_, inv_c1=inv, bid=bid, ask=ask, f=f, seed_ts=(2 * i + 1) * 10 ** 9, ts=(2 * i + 2) * 10 ** 9)
            assert xa == xb
        assert "size_id" not in a.mk_stats and "size_id" not in b_.mk_stats


def test_v791_summary_carries_size_id_beside_touch_id():
    on = core.TakerCore(UID, P(**MKZ))
    _flat_step(on, inv_c1=-450.0)
    assert on.mk_stats["size_id"] == {"books": 64, "big": K1Z_68_N, "digest": "b81f3eacfa105a0a"}
    assert "touch_id" not in on.mk_stats and "dev_abs_notional_p50" in on.mk_stats        # every existing field stays
    off = core.TakerCore(UID, P(**MKT))
    _flat_step(off)
    assert "size_id" not in off.mk_stats


def test_v791_agent_logs_the_size_identity(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path, **dict(V75, v7_size_id=1.0))
    agent.handle(_st(*_rel_state()))
    agent.handle(_st(*_rel_state(mids={200: 8.91}), ts=10 ** 9 + S60))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    assert lines[0]["version"] == "v7.11" and lines[0]["core"] == "taker_v7_core_14"
    assert lines[0]["params"]["size_id"] is True and lines[0]["params"]["touch_id"] is False
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["errors"] == 0
    assert summ["maker"]["size_id"] == {"books": 64, "big": K1Z_68_N, "digest": "b81f3eacfa105a0a"}


# ---- v7.9.2: ONE logic with v7.9.1, a different parameter profile (core_13 = core_12 + v7_size_pair + v7_c0_tgt_off_clips) ----
def test_v792_new_params_parse_validate_and_default_to_core12():
    import pytest
    p = P()
    assert p["size_pair"] == (1.0, 2.0) and p["c0_tgt_off"] == 0.0                        # the code defaults = v7.9.1
    assert "v7_size_pair" in core.PARAM_KEYS and "v7_c0_tgt_off_clips" in core.PARAM_KEYS
    assert core.SIZE_ID_MULTS == (1.0, 2.0) and core.SIZE_PAIR_MAX == 10.0 and core.C0_TGT_OFF_MAX == 5.0
    for good, want in (("1.5,2.5", (1.5, 2.5)), ("1.25,1.75", (1.25, 1.75)), (" 3 , 4 ", (3.0, 4.0)),
                       ((1.5, 2.5), (1.5, 2.5)), ([2, 3], (2.0, 3.0)), ("10,10", (10.0, 10.0)), ("0.1,0.2", (0.1, 0.2))):
        q = P(v7_size_pair=good)["size_pair"]
        assert q == want and all(type(x) is float for x in q), (good, q)
    for bad in ("1.5", 1.5, "1.5,2.5,3", "a,b", "1,", ",2", "0,2", "1,0", "-1,2", "1,-2", "11,2", "1,10.5", "nan,2", "1,inf",
                "", (1.5,), (1, 2, 3), True, "1.5;2.5", "1.5 2.5"):
        with pytest.raises(ValueError):
            core.parse_params({"v7_size_pair": bad})
    for good in (0.5, -0.5, 0.0, 5.0, -5.0, "0.5", 1):
        assert P(v7_c0_tgt_off_clips=good)["c0_tgt_off"] == float(good)
    for bad in ("x", 5.01, -5.01, "inf", "nan", True, "1,2"):
        with pytest.raises(ValueError):
            core.parse_params({"v7_c0_tgt_off_clips": bad})
    ns = _parse_kwargs_like("v7_size_pair=1.5,2.5 v7_c0_tgt_off_clips=0.5")           # taos ParseKwargs delivery
    assert core.parse_params(ns)["size_pair"] == (1.5, 2.5) and core.parse_params(ns)["c0_tgt_off"] == 0.5


def test_v792_size_pair_draw_index_digest_and_big_count():
    import hashlib
    pair = (1.25, 1.75)
    for uid in (68, 99, 235):
        for b in range(128, 192):
            h = hashlib.sha256(f"orthox-profile-v3|K1Z|{uid}|{b}".encode()).hexdigest()
            idx = int(h[:8], 16) % 2
            assert core.size_id_index(uid, b) == idx and core.size_id_draw(uid, b, pair) == pair[idx]
            assert core.size_id_draw(uid, b) == (1.0, 2.0)[idx]                          # the default pair is v7.9.1
    assert core.size_id_index(-1, 130) == 0 and core.size_id_draw(-1, 130, pair) == 1.25
    lines = ["orthox-profile-v3|uid=235|K1Z"] + [f"{b}|K1Z={core.size_id_draw(235, b, pair)!r}" for b in range(128, 192)]
    assert core.size_id_digest(235, range(128, 192), pair) == hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]
    assert core.size_id_digest(235, range(128, 192), pair) != core.size_id_digest(235, range(128, 192))
    assert core.size_id_digest(235, range(128, 192), (1.0, 2.0)) == "eaf96ef1c9384f03"   # = v7.9.1's table
    m = core.TakerCore(235, P(**dict(MKZ, v7_size_pair="1.25,1.75")))
    assert m._sid_mult(128) == core.size_id_draw(235, 128, pair) and m._sid_mult(128) in pair


def test_v792_profile_launcher_defaults_and_refusals():
    text = (ROOT / "run_taker_v7.sh").read_text()
    pr = PROFILE
    for key in ('V7_SIZE_PAIR="${V7_SIZE_PAIR:-' + pr["size_pair_text"].strip('"') + '}"',
                'V7_C0_K="${V7_C0_K:-' + str(pr["k"]) + '}"', 'V7_C0_TGT_OFF="${V7_C0_TGT_OFF:-' + str(pr["off"]) + '}"',
                'V7_REL_HL_BAND="${V7_REL_HL_BAND:-' + str(pr["hl"]) + '}"', 'V7_REL_L="${V7_REL_L:-' + str(pr["rl"]) + '}"',
                'V7_SIZE_ID="${V7_SIZE_ID:-1}"', 'V7_TOUCH_ID="${V7_TOUCH_ID:-0}"',
                " v7_size_pair=${V7_SIZE_PAIR} v7_c0_tgt_off_clips=${V7_C0_TGT_OFF} ", "v7_k=${V7_C0_K} ",
                "v7_rel_l=${V7_REL_L} ", "band) REL_HL=${V7_REL_HL_BAND};",
                'echo "[TakerV7] version=v7.11 ', 'echo "[preflight] v7.11 PASS"'):
        assert key in text, key
    for var in ("V7_SIZE_PAIR", "V7_C0_K", "V7_C0_TGT_OFF", "V7_REL_HL_BAND", "V7_REL_L"):
        assert f'ERROR: {var} must be' in text and f'"${var}"' in text, var
    p = core.parse_params(_parse_kwargs_like(_launcher_params()))
    assert (p["size_pair"], p["k"], p["c0_tgt_off"], p["rel_hl_s"], p["rel_l"]) == (
        pr["size_pair"], pr["k"], pr["off"], pr["hl"], pr["rl"])
    assert p["size_id"] is True and p["touch_id"] is False and p["rel_form"] == "band" and p["c0_contra"] is True
    assert p["mk_notional"] == 1500.0 and p["mk_band_clips"] == 1.0 and p["rel_band_mult"] == 2.0
    # the launch params equal the v7.9.1 launch values key by key except the profile keys
    q = dict(p)
    for k_, v_ in (("size_pair", (1.0, 2.0)), ("k", 0.005), ("c0_tgt_off", 0.0), ("rel_hl_s", 2116.8), ("rel_l", 0.45)):
        q[k_] = v_
    n91 = core.parse_params(_parse_kwargs_like(_launcher_params().replace(
        "v7_k=" + str(pr["k"]), "v7_k=0.005").replace("v7_size_pair=" + pr["size_pair_text"].strip('"'),
        "v7_size_pair=1,2").replace("v7_c0_tgt_off_clips=" + str(pr["off"]), "v7_c0_tgt_off_clips=0.0").replace(
        "v7_rel_hl_s=" + str(pr["hl"]), "v7_rel_hl_s=2116.8").replace("v7_rel_l=" + str(pr["rl"]), "v7_rel_l=0.45")))
    assert n91 == q


def test_v792_c0_target_offset_is_added_in_units_after_the_limit_as_the_round43_engine():
    mid = 300.0
    w = w_at(mid)
    for off in (0.0, 0.5, -0.5, 2.0):
        p = P(v7_k=0.005, v7_c0_tgt_off_clips=off)
        for gap in (-400.0, -50.0, 0.0, 30.0, 150.0, 900.0, -900.0):
            f = mid * (1.0 + gap / 1e4)
            r0 = core.decide(mid - 0.01, mid + 0.01, f, BI, BI, QI, BI, QI, 0.0003, P(v7_k=0.005), GRID, None)
            r1 = core.decide(mid - 0.01, mid + 0.01, f, BI, BI, QI, BI, QI, 0.0003, p, GRID, None)
            # Round 43 C c0_lib A0.want / Round 35 A a_engine run(): tG = clip(KG x gap_bp, +-LG) x W / mid + off units
            TG = max(-0.55, min(0.55, 0.005 * 1e4 * (f - mid) / mid))
            expect = TG * w / mid + off * 7.0
            assert abs(r1[3] - expect) < 1e-9 and abs(r1[3] - (r0[3] + off * 7.0)) < 1e-9, (off, gap, r1[3], expect)
    # units follow v7_clip (not a notional): gap 0, clip 10, offset 0.5 -> +5 units
    p = P(v7_c0_tgt_off_clips=0.5, v7_clip=10.0, v7_big=20.0)
    r = core.decide(299.99, 300.01, 300.0, BI, BI, QI, BI, QI, 0.0003, p, GRID, None)
    assert abs(r[3] - 5.0) < 1e-12
    # the contrarian term goes into the common limit first; the offset stays outside it (after the limit)
    p = P(v7_k=0.005, v7_l=0.10, v7_c0_tgt_off_clips=0.5)
    r = core.decide(299.99, 300.01, 300.0 * 1.2, BI, BI, QI, BI, QI, 0.0003, p, GRID, None, add_frac=0.4)
    assert abs(r[3] - (0.10 * w_at(300.0) / 300.0 + 3.5)) < 1e-9
    # class 1 (rule) and the stepping taker (tfrac) never read it
    p = P(v7_c0_tgt_off_clips=0.5)
    a = core.decide(299.99, 300.01, 300.0, BI, BI, QI, BI, QI, 0.0003, p, GRID, None, rule=(0.036, 0.5, 3500.0))
    b = core.decide(299.99, 300.01, 300.0, BI, BI, QI, BI, QI, 0.0003, P(), GRID, None, rule=(0.036, 0.5, 3500.0))
    assert a == b
    a = core.decide(299.99, 300.01, 300.0, BI, BI, QI, BI, QI, 0.0003, p, GRID, None, rule=(0.0, 0.45, 1800.0), tfrac=0.1)
    b = core.decide(299.99, 300.01, 300.0, BI, BI, QI, BI, QI, 0.0003, P(), GRID, None, rule=(0.0, 0.45, 1800.0), tfrac=0.1)
    assert a == b
    # the order follows the shifted target: gap 0, +0.5 clip = +3.5 units < the 7-unit trigger -> in band; +1.5 clip -> a clip buy
    r = core.decide(299.99, 300.01, 300.0, BI, BI, QI, BI, QI, 0.0003, P(v7_c0_tgt_off_clips=0.5), GRID, None)
    assert r[0] == "in_band" and r[1] is None
    r = core.decide(299.99, 300.01, 300.0, BI, BI, QI, BI, QI, 0.0003, P(v7_c0_tgt_off_clips=1.5), GRID, None)
    assert r[0] == "send" and r[1][0] == core.SIDE_BUY and r[1][1] == 7.0


def test_v792_size_pair_applies_per_book_to_order_size_and_band():
    pair = (1.5, 2.5)
    legs = dict(MKZ, v7_size_pair="1.5,2.5")
    big = [b for b in range(128, 192) if core.size_id_index(UID, b) == 1]
    small = [b for b in range(128, 192) if core.size_id_index(UID, b) == 0]
    assert big and small
    b_big, b_small = big[0], small[0]
    # clip 1,800 (level target leg): small = 2,700 band, big = 4,500 band.  deviation d notional (a buy side)
    for dev, small_quotes, big_quotes in ((2000.0, False, False), (3000.0, True, False), (5000.0, True, True)):
        res = _c1_orders(_flat_step(core.TakerCore(UID, P(**legs)), inv_c1=-dev / 9.0))
        assert (b_small in res) is small_quotes and (b_big in res) is big_quotes, (dev, b_small in res, b_big in res)
    res = _c1_orders(_flat_step(core.TakerCore(UID, P(**legs)), inv_c1=-5000.0 / 9.0))
    ref = _c1_orders(_flat_step(core.TakerCore(UID, P(**dict(MKT))), inv_c1=-5000.0 / 9.0))
    for b, mult in ((b_big, pair[1]), (b_small, pair[0])):
        assert res[b][0] == ref[b][0] == core.SIDE_BUY and res[b][2] == ref[b][2] == 8.99           # same side, v7.8 price
        assert res[b][1] == core.floor_to(mult * 1800.0 / 9.0, 4) and ref[b][1] == core.floor_to(1800.0 / 9.0, 4)
    # the ask side too
    res = _c1_orders(_flat_step(core.TakerCore(UID, P(**legs)), inv_c1=5000.0 / 9.0))
    assert res[b_big][0] == res[b_small][0] == core.SIDE_SELL and res[b_big][1] == core.floor_to(2.5 * 1800.0 / 9.0, 4)
    assert res[b_small][1] == core.floor_to(1.5 * 1800.0 / 9.0, 4) and res[b_big][2] == 9.01
    # summary: books = class-1 target books, big = those drawn index 1 (not "mult above 1"), digest with the pair
    m = core.TakerCore(UID, P(**legs))
    _flat_step(m, inv_c1=0.0)
    n_big = sum(core.size_id_index(UID, b) == 1 for b in range(128, 192))
    assert m.mk_stats["size_id"] == {"books": 64, "big": n_big, "digest": core.size_id_digest(UID, range(128, 192), pair)}
    assert m.mk_stats["size_id"]["digest"] != core.size_id_digest(UID, range(128, 192))
    m2 = core.TakerCore(UID, P(**dict(MKZ, v7_size_pair="0.5,0.75")))        # every mult below 1: big is still index 1
    _flat_step(m2, inv_c1=0.0)
    assert m2.mk_stats["size_id"]["big"] == n_big


def test_v792_default_params_reproduce_core12_decisions():
    # the code defaults (pair and offset absent) and the explicit v7.9.1 values give identical actions
    a = core.TakerCore(UID, P(**MKZ))
    b = core.TakerCore(UID, P(**dict(MKZ, v7_size_pair="1.0,2.0", v7_c0_tgt_off_clips=0.0)))
    for inv in (0.0, 300.0, -300.0, 600.0):
        assert _flat_step(a, inv_c1=inv) == _flat_step(b, inv_c1=inv)


def test_v792_agent_header_carries_the_profile(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    pr = PROFILE
    agent = _agent(mod, tmp_path, **dict(V75, v7_size_id=1.0, v7_size_pair=pr["size_pair_text"].strip('"'),
                                         v7_c0_tgt_off_clips=pr["off"], v7_k=pr["k"], v7_rel_hl_s=pr["hl"], v7_rel_l=pr["rl"]))
    agent.handle(_st(*_rel_state()))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    hp = lines[0]["params"]
    assert lines[0]["version"] == "v7.11" and lines[0]["core"] == "taker_v7_core_14"
    assert tuple(hp["size_pair"]) == pr["size_pair"] and hp["c0_tgt_off"] == pr["off"] and hp["k"] == pr["k"]
    assert hp["rel_hl_s"] == pr["hl"] and hp["rel_l"] == pr["rl"] and hp["size_id"] is True


# ---- v7.11 / v7.11.1: class-2 flat maker + maker placement identity (core_14; Round 53 SOLUTION C6 / C3) ----------------------
# Launcher profile of THIS tree (COPIED constants, SP/round53/a/out/s7_forms.txt): c0 / c2 maker notional and band in W.
V711T = dict(c0n="600", c0b="0.53", c2n="840", c2b="0.47")
V711 = {k: float(v) for k, v in V711T.items()}
MK11 = dict(v7_c0_maker=1.0, v7_c0_mk_notional=V711["c0n"], v7_c0_mk_band_w=V711["c0b"], v7_c2_maker=1.0,
            v7_c2_mk_notional=V711["c2n"], v7_c2_mk_band_w=V711["c2b"])
MK11P = dict(MK11, v7_mk_place_id=1.0)
V11 = dict(V75, **MK11)                                   # K2 + class-2 band taker + both flat makers (placement off)
V11P = dict(V75, **MK11P)
ODD_C2 = tuple(range(193, 256, 2))
EVEN_C2 = tuple(range(192, 256, 2))


def _c2_books(actions, kind=None):
    return sorted({a[1] for a in actions if 192 <= a[1] < 256 and (kind is None or a[0] == kind)})


def test_v711_versions_params_defaults_and_refusals():
    import pytest
    assert core.V7_VERSION == "v7.11" and core.CORE_VERSION == "taker_v7_core_14"
    p = P()
    assert p["c2_maker"] is False and p["place_id"] is False                       # code defaults = core_13
    assert p["c2_mk_notional"] == 840.0 and p["c2_mk_band_w"] == 0.47               # COPIED FORM4 values as code defaults
    for k in ("v7_c2_maker", "v7_c2_mk_notional", "v7_c2_mk_band_w", "v7_mk_place_id"):
        assert k in core.PARAM_KEYS
    assert P(v7_c2_maker=1.0)["c2_maker"] is True and P(v7_c2_maker="1")["c2_maker"] is True
    assert P(v7_c0_maker=1.0, v7_mk_place_id=1.0)["place_id"] is True
    assert P(v7_c2_maker=1.0, v7_mk_place_id="1")["place_id"] is True
    assert P(v7_c2_maker=1.0, v7_rel_classes=2.0)["rel_classes"] == (2,)            # the parity split: taker + maker on class 2
    for bad in ({"v7_c2_maker": 2}, {"v7_c2_maker": "on"}, {"v7_c2_maker": -1}, {"v7_c2_maker": 0.5}, {"v7_c2_maker": True},
                {"v7_mk_place_id": 2}, {"v7_mk_place_id": "on"}, {"v7_mk_place_id": True, "v7_c0_maker": 1.0},
                {"v7_c2_mk_notional": 0}, {"v7_c2_mk_notional": -5}, {"v7_c2_mk_notional": 1e6 + 1}, {"v7_c2_mk_notional": "x"},
                {"v7_c2_mk_band_w": 0}, {"v7_c2_mk_band_w": 1.01}, {"v7_c2_mk_band_w": "x"},
                {"v7_c2_maker": 1.0, "v7_maker_classes": "1,2"},                     # one leg per class-2 book
                {"v7_mk_place_id": 1.0},                                              # needs a flat maker
                {"v7_mk_place_id": 1.0, "v7_c0_maker": 1.0, "v7_c0_mk_trial": 1.0}):  # one placement rule per book
        with pytest.raises(ValueError):
            core.parse_params(bad)
    assert P(v7_c0_maker=1.0, v7_c0_mk_trial=1.0)["c0_mk_trial"] is True              # the trial alone is still v7.7
    assert P(v7_maker_classes="1,2")["maker_classes"] == (1, 2)                       # the old class-2 maker leg is unchanged


def test_v711_place_id_draw_digest_and_negative_uid():
    import hashlib
    assert (core.MK_PLACE_PROFILE, core.MK_PLACE_DIM, core.MK_PLACE_MOD) == ("orthox-profile-v3", "MKP", 3)
    for uid in (68, 77, 235):
        seen = set()
        for b in list(range(1, 128, 2)) + list(ODD_C2):
            h = hashlib.sha256(f"orthox-profile-v3|MKP|{uid}|{b}".encode()).hexdigest()
            assert core.place_id_index(uid, b) == int(h, 16) % 3                       # the whole digest as an integer
            seen.add(core.place_id_index(uid, b))
        assert seen == {0, 1, 2}
    assert core.place_id_index(-1, 5) == 0
    lines = ["orthox-profile-v3|uid=235|MKP"] + [f"{b}|MKP={core.place_id_index(235, b)}" for b in (1, 3, 193)]
    assert core.place_id_digest(235, (193, 1, 3)) == hashlib.sha256("\n".join(lines).encode()).hexdigest()[:16]
    assert core.place_id_digest(235, (1, 3)) != core.place_id_digest(77, (1, 3))
    assert core.C0_MK_TRIAL_OFFSETS[:3] == (None, 0, 1)                                # the v7.7 offsets the placement reuses


def test_v711_c2_maker_quotes_only_odd_class2_books_two_sided_in_its_notional():
    c = core.TakerCore(UID, P(**V11))
    actions, _ = c.step(10 ** 9, "hk", *_rel_state(), [], collect=True)
    assert _c2_books(actions) == list(ODD_C2) and _c2_books(actions, core.ACT_TAKE) == []
    for b in ODD_C2:
        qs = _quotes(actions, b)
        assert qs == [(core.SIDE_BUY, 8.9901), (core.SIDE_SELL, 9.0099)], (b, qs)    # 200-tick spread: one tick inside
        assert all(a[3] == core.floor_to(V711["c2n"] / 9.0, 4) for a in actions if a[1] == b and a[0] == core.ACT_QUOTE)
    ms = c.mk_stats
    assert ms["c2_books"] == 32 and ms["c2_two_sided"] == 32 and ms["c2_pos_w_max"] == 0.0 and ms["c0_books"] == 64
    assert c.c["c2mk_inside"] == 32 and "place_id" not in ms
    # the rel taker DECIDES on the EVEN class-2 books only; its state and class mean cover the whole class (as core_13)
    assert sorted(b for b in c.runs["hk"].step_st if b >= 192) == list(range(192, 256)) and c.rel_stats["books"] == 32
    off = core.TakerCore(UID, P(**V75))
    off.step(10 ** 9, "hk", *_rel_state(), [], collect=True)
    assert off.rel_stats["books"] == 64 and "c2_books" not in off.mk_stats


def test_v711_c2_maker_band_withdraws_the_adding_side_and_never_takes():
    c = core.TakerCore(UID, P(**V11))
    mid = 9.0
    big = (V711["c2b"] * 50000.0 / mid) * 1.05                                              # 1.05 x the band, in base units
    small = 0.5 * V711["c2b"] * 50000.0 / mid
    st = _rel_state(inv={193: big, 195: -big, 197: small, 199: -small})
    actions, _ = c.step(10 ** 9, "hk", *st, [], collect=True)
    assert _quotes(actions, 193) == [(core.SIDE_SELL, 9.0099)]                       # long past the band: bid withdrawn
    assert _quotes(actions, 195) == [(core.SIDE_BUY, 8.9901)]                        # short past the band: ask withdrawn
    assert len(_quotes(actions, 197)) == 2 and len(_quotes(actions, 199)) == 2
    assert c.c["c2mk_band_bid"] == 1 and c.c["c2mk_band_ask"] == 1
    assert not [a for a in actions if a[1] in ODD_C2 and a[0] == core.ACT_TAKE]
    assert c.mk_stats["c2_pos_w_max"] > V711["c2b"] and c.mk_stats["c2_two_sided"] == 30


def test_v711_c2_maker_prices_off_the_others_touch_keeps_and_never_locks():
    c = core.TakerCore(UID, P(**V11))
    config, books, accounts = _rel_state()
    f = books[193]["f"]
    # our quotes sit alone at 8.9901 / 9.0099 (the v7.6 rule); the others at 8.9900 / 9.0100: kept, no walk
    books[193] = {"i": 0, "r": 0.4, "b": [_lv(8.9901, 5.0), _lv(8.99, 50.0)], "a": [_lv(9.0099, 5.0), _lv(9.01, 50.0)],
                  "e": [], "f": f}
    accounts[UID][193]["o"] = [{"y": "o", "i": 41, "c": None, "t": 1, "q": 5.0, "s": 0, "p": 8.9901, "l": 0.0},
                               {"y": "o", "i": 42, "c": None, "t": 1, "q": 5.0, "s": 1, "p": 9.0099, "l": 0.0}]
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in actions if a[1] == 193] and c.c["mk_keep"] >= 2
    assert core.others_touch(books[193], core.account_orders(accounts[UID][193]), 0.00005) == (8.99, 9.01)
    # a one-tick book (others' bid 8.9999, ask 9.0000): quoted AT the touch, bid below ask; a zero-width book quotes nothing
    config, books, accounts = _rel_state()
    books[195] = book(8.9999, 9.0, books[195]["f"])
    books[197] = book(9.0, 9.0, books[197]["f"])
    actions, _ = c.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    assert _quotes(actions, 195) == [(core.SIDE_BUY, 8.9999), (core.SIDE_SELL, 9.0)]
    assert _quotes(actions, 197) == [] and c.c["mk2_bad_book"] >= 1
    for b in ODD_C2:                                                                 # no state quotes a locked or crossed pair
        q = dict(_quotes(actions, b))
        if len(q) == 2:
            assert q[core.SIDE_BUY] < q[core.SIDE_SELL]


def test_v711_c2_maker_respects_the_pace_cap_and_free_balances():
    free = core.TakerCore(UID, P(**dict(V75, v7_c2_maker=1.0)))
    capped = core.TakerCore(UID, P(**dict(V75, v7_c2_maker=1.0, v7_cap=1000.0)))     # a tiny cap: the ledger refuses quotes
    n_free = len([a for a in free.step(10 ** 9, "hk", *_rel_state(), [])[0] if a[0] == core.ACT_QUOTE and a[1] in ODD_C2])
    n_cap = len([a for a in capped.step(10 ** 9, "hk", *_rel_state(), [])[0] if a[0] == core.ACT_QUOTE and a[1] in ODD_C2])
    assert n_free == 64 and n_cap < n_free and any(k.startswith("mk_paced_") for k in capped.c)
    c = core.TakerCore(UID, P(**V11))
    st = _rel_state(free={193: 0.0})                                                 # no free base: no ask, the bid is quoted
    actions, _ = c.step(10 ** 9, "hk", *st, [])
    assert _quotes(actions, 193) == [(core.SIDE_BUY, 8.9901)] and c.c["mk_no_base"] >= 1


def test_v711_place_id_places_each_maker_book_by_its_hashed_draw():
    uid = 77
    c = core.TakerCore(uid, P(**V11P))
    config, books, accounts = _rel_state()
    accounts = {uid: accounts[UID]}
    for b, a in accounts[uid].items():
        a["i"] = uid
    for b in range(1, 128, 2):                                                       # 10-tick class-0 books
        books[b] = book(299.95, 300.05, books[b]["f"])
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    cnt = [0, 0, 0]
    for b in list(range(1, 128, 2)) + list(ODD_C2):
        idx = core.place_id_index(uid, b)
        cnt[idx] += 1
        tick = 0.01 if b < 128 else 0.0001
        ob, oa = (299.95, 300.05) if b < 128 else (8.99, 9.01)
        want = {0: (ob + tick, oa - tick), 1: (ob, oa), 2: (ob - tick, oa + tick)}[idx]
        assert _quotes(actions, b) == [(core.SIDE_BUY, round(want[0], 4)), (core.SIDE_SELL, round(want[1], 4))], (b, idx)
    pl = c.mk_stats["place_id"]
    pb = list(range(1, 128, 2)) + list(ODD_C2)
    assert pl["books"] == 96 and pl["per_placement"] == cnt and sum(cnt) == 96 and min(cnt) > 0
    assert pl["digest"] == core.place_id_digest(uid, pb)
    assert [c.c.get("mk_pid%d" % i, 0) for i in range(3)] == cnt
    # draw 0 = the v7.6 rule, so a placement-off run quotes exactly those books alike
    off = core.TakerCore(uid, P(**V11))
    config, books, accounts = _rel_state()
    accounts = {uid: accounts[UID]}
    for b, a in accounts[uid].items():
        a["i"] = uid
    for b in range(1, 128, 2):
        books[b] = book(299.95, 300.05, books[b]["f"])
    xo, _ = off.step(10 ** 9, "hk", config, books, accounts, [], collect=True)
    same = [b for b in pb if core.place_id_index(uid, b) == 0]
    assert same and all(_quotes(xo, b) == _quotes(actions, b) for b in same)
    assert all(_quotes(xo, b) != _quotes(actions, b) for b in pb if core.place_id_index(uid, b) != 0)
    # two uids differ at the touch: some books are placed differently
    diff = [b for b in pb if core.place_id_index(77, b) != core.place_id_index(235, b)]
    assert len(diff) > 20


def test_v711_place_id_follows_the_others_touch_and_keeps_matching_orders():
    uid = 77
    b = next(x for x in ODD_C2 if core.place_id_index(uid, x) == 2)                  # one tick behind
    c = core.TakerCore(uid, P(**V11P))
    config, books, accounts = _rel_state()
    accounts = {uid: accounts[UID]}
    for k_, a in accounts[uid].items():
        a["i"] = uid
    actions, _ = c.step(10 ** 9, "hk", config, books, accounts, [])
    assert _quotes(actions, b) == [(core.SIDE_BUY, 8.9899), (core.SIDE_SELL, 9.0101)]
    accounts[uid][b]["o"] = [{"y": "o", "i": 61, "c": None, "t": 1, "q": 5.0, "s": 0, "p": 8.9899, "l": 0.0},
                             {"y": "o", "i": 62, "c": None, "t": 1, "q": 5.0, "s": 1, "p": 9.0101, "l": 0.0}]
    books[b] = {"i": 0, "r": 0.4, "b": [_lv(8.99, 50.0)], "a": [_lv(9.01, 50.0)], "e": [], "f": books[b]["f"]}
    actions, _ = c.step(2 * 10 ** 9, "hk", config, books, accounts, [])
    assert not [a for a in actions if a[1] == b]                                     # matching orders are kept
    books[b]["b"] = [_lv(8.9950, 50.0)]                                              # the others' bid moved up
    actions, _ = c.step(3 * 10 ** 9, "hk", config, books, accounts, [])
    assert (core.ACT_CANCEL, b, [61]) in actions and (core.SIDE_BUY, 8.9949) in _quotes(actions, b)


def test_v711_new_switches_off_equal_core13_and_each_leg_stays_on_its_books():
    off = core.TakerCore(UID, P(**V75))
    z = core.TakerCore(UID, P(**dict(V75, v7_c2_maker=0.0, v7_mk_place_id=0.0)))
    for i in range(1, 8):
        xs = [m.step(10 ** 9 + i * S60, "hk", *_rel_state(mids={200: 9.0 + 0.01 * i}), [])[0] for m in (off, z)]
        assert xs[0] == xs[1]
    base = core.TakerCore(UID, P(**dict(V75, v7_c0_maker=1.0)))
    with_c2 = core.TakerCore(UID, P(**dict(V75, v7_c0_maker=1.0, v7_c2_maker=1.0)))
    a0, _ = base.step(10 ** 9, "hk", *_rel_state(), [])
    a1, _ = with_c2.step(10 ** 9, "hk", *_rel_state(), [])
    assert [a for a in a0 if a[1] < 192] == [a for a in a1 if a[1] < 192]            # class 0 / 1 unchanged by the c2 maker
    assert _c2_books(a0) == [] and _c2_books(a1) == list(ODD_C2)


def test_v711_launcher_profile_defaults_and_refusals():
    import pytest
    text = (ROOT / "run_taker_v7.sh").read_text()
    for key in ('V7_C0_MAKER="${V7_C0_MAKER:-1}"', 'V7_C0_MK_TRIAL="${V7_C0_MK_TRIAL:-0}"', 'V7_MK_DEPTH="${V7_MK_DEPTH:-0}"',
                'V7_C0_MK_NOTIONAL="${V7_C0_MK_NOTIONAL:-%s}"' % V711T["c0n"], 'V7_C0_MK_BAND_W="${V7_C0_MK_BAND_W:-%s}"' % V711T["c0b"],
                'V7_C2_MAKER="${V7_C2_MAKER:-1}"', 'V7_C2_MK_NOTIONAL="${V7_C2_MK_NOTIONAL:-%s}"' % V711T["c2n"],
                'V7_C2_MK_BAND_W="${V7_C2_MK_BAND_W:-%s}"' % V711T["c2b"], 'V7_MK_PLACE_ID="${V7_MK_PLACE_ID:-1}"',
                " v7_c0_mk_notional=${V7_C0_MK_NOTIONAL} v7_c0_mk_band_w=${V7_C0_MK_BAND_W} ",
                "v7_c2_maker=${V7_C2_MAKER} v7_c2_mk_notional=${V7_C2_MK_NOTIONAL} v7_c2_mk_band_w=${V7_C2_MK_BAND_W} "
                "v7_mk_place_id=${V7_MK_PLACE_ID} ",
                'echo "[TakerV7] version=v7.11 TakerV7.py', 'echo "[preflight] v7.11 PASS"'):
        assert key in text, key
    for var in ("V7_C2_MAKER", "V7_MK_PLACE_ID"):
        assert re.search(r'case "\$%s" in\n  0\|1\) ;;\n  \*\) echo "ERROR: %s must be 0 or 1' % (var, var), text), var
    for var in ("V7_C0_MK_NOTIONAL", "V7_C0_MK_BAND_W", "V7_C2_MK_NOTIONAL", "V7_C2_MK_BAND_W"):
        assert var in text.split("for _v in ")[1].split(";")[0]
    for msg in ("V7_MK_PLACE_ID=1 places", "V7_MK_PLACE_ID=1 and V7_C0_MK_TRIAL=1", "V7_C2_MAKER=1 makes the odd class-2"):
        assert msg in text, msg
    p = core.parse_params(_parse_kwargs_like(_launcher_params()))
    assert p["c0_maker"] is True and p["c0_mk_trial"] is False and p["mk_depth"] is False
    assert p["c0_mk_notional"] == V711["c0n"] and p["c0_mk_band_w"] == V711["c0b"]
    assert p["c2_maker"] is True and p["c2_mk_notional"] == V711["c2n"] and p["c2_mk_band_w"] == V711["c2b"]
    assert p["place_id"] is True and p["rel_classes"] == (2,) and p["maker_classes"] == (1,) and p["mk_band"] is True
    assert p["mk_notional"] == 1500.0 and p["mk_band_clips"] == 1.0                   # class 1 stays K2 on every class-1 book
    assert p["size_id"] is True and p["touch_id"] is False
    # switching the three new switches off gives the parent's parameters key by key
    q = dict(p, c0_maker=False, c2_maker=False, place_id=False, c0_mk_notional=1000.0, c0_mk_band_w=0.10,
             c2_mk_notional=840.0, c2_mk_band_w=0.47)
    off = _launcher_params().replace(" v7_c0_maker=1 ", " v7_c0_maker=0 ").replace(" v7_c2_maker=1 ", " v7_c2_maker=0 ").replace(
        " v7_mk_place_id=1 ", " v7_mk_place_id=0 ")
    assert " v7_c0_maker=0 " in off and " v7_c2_maker=0 " in off and " v7_mk_place_id=0 " in off
    po = core.parse_params(_parse_kwargs_like(off))
    assert {k: v for k, v in po.items() if not k.startswith(("c0_mk_n", "c0_mk_b", "c2_mk_"))} == \
        {k: v for k, v in q.items() if not k.startswith(("c0_mk_n", "c0_mk_b", "c2_mk_"))}
    # the launch parameters step: class-0 odd, class-2 odd books are quoted, the even ones are taken or stepped
    c = core.TakerCore(UID, p)
    actions, _ = c.step(10 ** 9, "hk", *_rel_state(), [], collect=True)
    assert {a[1] for a in actions if a[0] == core.ACT_QUOTE and a[1] < 128} == set(range(1, 128, 2))
    assert {a[1] for a in actions if a[0] == core.ACT_QUOTE and a[1] >= 192} == set(ODD_C2)


def test_v711_agent_header_and_summary_carry_the_makers(tmp_path):
    real = _real_taos()
    mod = _load_agent_class(stubbed=not real)
    agent = _agent(mod, tmp_path, **V11P)
    agent.handle(_st(*_rel_state()))
    agent.handle(_st(*_rel_state(mids={200: 8.91}), ts=10 ** 9 + S60))
    assert agent._writer.flush()
    lines = [json.loads(x) for x in Path(agent.log_path).read_text().splitlines() if x.strip()]
    hp = lines[0]["params"]
    assert lines[0]["version"] == "v7.11" and lines[0]["core"] == "taker_v7_core_14"
    assert hp["c2_maker"] is True and hp["place_id"] is True and hp["c2_mk_notional"] == V711["c2n"]
    summ = [x for x in lines if x["kind"] == "summary"][0]
    assert summ["errors"] == 0 and summ["maker"]["c0_books"] == 64 and summ["maker"]["c2_books"] == 32
    assert summ["maker"]["c2_two_sided"] > 0 and summ["maker"]["place_id"]["books"] == 96
    assert len(summ["maker"]["place_id"]["digest"]) == 16


def test_v711_rel_taker_takes_only_even_class2_books_and_band_edges_use_the_c2_band():
    c = core.TakerCore(UID, P(**V11))
    c.step(10 ** 9, "hk", *_rel_state(), [])
    taken = set()
    for i in range(1, 37):
        st = _rel_state(mids={200: 8.91, 201: 9.09, 202: 8.80, 203: 9.20, 210: 8.85, 211: 9.15})
        actions, _ = c.step(10 ** 9 + i * S60, "hk", *st, [])
        taken |= {a[1] for a in actions if a[0] == core.ACT_TAKE and a[1] >= 192}
    assert taken and taken <= set(EVEN_C2), sorted(taken)                             # takes on even books only
    # a position between the c0 and the c2 band edges (when they differ) is judged by the c2 band
    mid = 9.0
    x = 0.95 * V711["c2b"] * 50000.0 / mid
    c = core.TakerCore(UID, P(**V11))
    actions, _ = c.step(10 ** 9, "hk", *_rel_state(inv={193: x, 195: -x}), [])
    assert len(_quotes(actions, 193)) == 2 and len(_quotes(actions, 195)) == 2
