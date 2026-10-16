"""v6.32: S14 research_v632_fee_making (with research_v632_fee_weight) and P1 research_v632_state_fundamental.

v6.31 + two switches, default on, each its own off switch.

S14 [STRUCTURAL]: SN79 0.6.4 books every maker fill net of making_fee_weight x its maker fee, in the captured and the
realized sums.  The agent measures, with the validator's own arithmetic (taos 0.6.4 debeta.py vendored byte for byte),
the realized spread per notional ALL its maker fills keep at the validator's horizon over its making window -- one
pooled estimate -- and on a book where it does not exceed W x the book's maker fee rate the ADDING side holds no deep
order and no v6.17 touch quote; a reducing order, an exit and a cancel are never withheld; only on a 0.6.4 validator's
state (spec 64).

P1 [STRUCTURAL, protocol adaptation]: 0.6.4 writes each book's fundamental into the state (key f); the vendored Book
keeps it, and every reader of v6.16's rule and S9's history take it before the page.

No test here touches the host's caches, the network or a live tree, and nothing is imported from the scratchpad: the
0.6.4 originals are pinned by the sha256 of their source (computed once from upstream 19f9fbd) and by golden values
the original produced on the deterministic streams rebuilt below; the agent is t631's harness with v6.32's real methods,
its real _init_build_switches statements and respond()'s real v6.32 statement.
"""
import ast
import difflib
import hashlib
import math
import os
import re
import shutil
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v62_breadth as v62b  # noqa: E402
import research_v623_validator_063 as vo  # noqa: E402
import research_v626_feed_clock as fc  # noqa: E402
import research_v6_29_touch_move as tm  # noqa: E402
import research_v6_32_fee_making as fm  # noqa: E402
import research_v6_32_state_fp as sf  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v63_trend_target as v63t  # noqa: E402
import test_research_v6_2_14_touch_life as t14  # noqa: E402
import test_research_v6_3_0_trend_target as t63  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
import test_research_v6_24 as t624  # noqa: E402
import test_research_v6_25 as t625  # noqa: E402
import test_research_v6_26 as t626  # noqa: E402
import test_research_v6_27 as t627  # noqa: E402
import test_research_v6_28 as t628  # noqa: E402
import test_research_v6_29 as t629  # noqa: E402
import test_research_v6_30 as t630  # noqa: E402
import test_research_v6_31 as t631  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_v6_32_fee_making.py").read_text()
STATE_MODULE = (STRATEGY / "research_v6_32_state_fp.py").read_text()
MODELS = (ROOT / "taos" / "im" / "protocol" / "models.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t631.NOW
SIM = t631.SIM
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
ABOVE, BELOW = t631.ABOVE, t631.BELOW            # book 3 (100.00 / 100.03): fp 38 bps above / 42 bps below its mid
HOOK_TEST = "getattr(self, '_v6_32_counts', None) is not None"
REQUEST_LINE = "self._v6_32_request(state)"
NOTE_LINE = "self._v6_32_note(book_id, inv)"
REPORT_LINE = "self._v6_32_report()"
FEE_SWITCH = 'self.research_v632_fee_making = self._as_bool(getattr(self.config, "research_v632_fee_making", True))'
WEIGHT_READ = 'getattr(self.config, "research_v632_fee_weight", V6_32_FEE_WEIGHT_DEFAULT))'
FP_SWITCH = 'getattr(self.config, "research_v632_state_fundamental", True))'
V6_32_NAMES = {"V6_32_CANCEL_FEE_GATE": fm.CANCEL_FEE_GATE, "V6_32_FEE_WEIGHT_DEFAULT": fm.FEE_WEIGHT_DEFAULT,
               "V6_32RealizedTracker": fm.RealizedTracker, "V6_32_FEE_MAKING_VERSION": fm.V632_FEE_MAKING_VERSION,
               "v6_32_adds": fm.adds, "v6_32_deep_pairs": fm.deep_pairs, "v6_32_fee_gated": fm.fee_gated,
               "v6_32_gate_active": fm.gate_active, "v6_32_maker_fee_rate": fm.maker_fee_rate,
               "v6_32_min_sizes": fm.min_sizes, "v6_32_parse_fee_weight": fm.parse_fee_weight,
               "V6_32_STATE_FUNDAMENTAL_VERSION": sf.V632_STATE_FUNDAMENTAL_VERSION,
               "v6_32_book_value": sf.book_value, "v6_32_fundamental_value": sf.fundamental_value,
               "v6_32_s9_snapshot": sf.s9_snapshot}
# the names v6.32's methods reach beyond its own, from the modules Simple imports them from (never invented here)
REACHED = {"v623_validator_spec": vo.validator_spec, "v626_state_sim_id": fc.state_sim_id,
           "V63_SIDE_BUY": v63t.SIDE_BUY, "V63_SIDE_SELL": v63t.SIDE_SELL,
           "V62_STATE_EVERY_TICKS": v62b.V62_STATE_EVERY_TICKS, "time": time}
EXTRA = dict(t631.EXTRA, **V6_32_NAMES, **REACHED)
METHODS = ("_v6_32_request", "_v6_32_tracker_ref", "_v6_32_min_sizes", "_v6_32_note", "_v6_32_deep_pairs",
           "_v6_32_touch_sides", "_v6_32_state_fp", "_v6_32_fundamental", "_v6_32_s9_snapshot", "_v6_32_count",
           "_v6_32_report", "_v6_32_snapshot")
FIELDS = ("version", "state_fp_version", "fee_making_on", "state_fundamental_on", "active", "spec", "fee_weight",
          "estimate_bps", "window_start_ns", "window_fills", "activations", "deactivations", "counted",
          "dropped_inactive", "pending", "prints", "own_fills", "released", "stale", "evicted", "malformed",
          "gated_books", "no_fee_rate_books", "state_fp_books", "active_requests", "window_starts", "deep_cancels",
          "deep_withheld", "touch_cancels", "touch_withheld", "tracker_resets", "state_fp_requests",
          "no_state_fp_requests", "fp_page_fallbacks", "tracker_ms_p50", "tracker_ms_p99", "errors")

# taos 0.6.4 (upstream test 19f9fbd) taos/im/validator/debeta.py: sha256 of each vendored function's source as
# ast.unparse renders it, computed once from the upstream file (scratchpad k91/build/vendor_check.py)
DEBETA_064 = {
    "_background_sided": "865c23d49b99f755b5f9d7fed09b6c5bc8f9eff9a8df0e8005818cb7f6d78e03",
    "_push_floor_reference": "65aef056150a6b17115e00d2b3ebc872fded7e317638b14629604f88a38da52d",
    "_clears": "fdd3b9e4ff6b0b16820d4c2112e59a84120aa2b3f4593ecb1c696e5e4cdc0ca7",
    "_floored_mean_at": "b5a0f37cf14977ed801a08578229bcb7549b5cd64a68085c2843c75981b7b75f",
    "_floored_mid_at": "9b8692b19b01d214c686486ae22475b692fd84f9e0a60032a3d65c7aa4aa28af",
    "_forward_complete": "b5e96ead5ca440e428dae7f830d77fde9c9c41ac4be2c591fb4e04210032a4ed",
    "_mark_weight": "341e5b42b64b9cc522b16d40cfe51423417bbec577935dbed5091e9e3c267b09",
    "_weighted_mark": "b038c8ab5b0de9fc810b9779fbcdaffbb504c18c3f6142947634ed09859a5c27",
    "_mark_near": "c952e5e9c6ebe5370b0acd5ce534f2ae1a697ae392285dbf73c5d5c49be98c23",
    "_capped_mark": "be6dc224b5bfc6a4ab9d1b861ff8cc97ae57ac603a1d1ca686ccce394ee924f5",
    "_print_time": "b869d1e7f2cbda56b6e986f934e75ca214ab5ad3bca153eebf6927ed83bef821",
}
# taos 0.6.4 taos/im/protocol/book.py Book: the field and the property, the same way (19f9fbd)
BOOK_064 = {"f": "82f132908f91c8c5df191f59bd8a30ed294daa09af877d8e7c2c9084aa997702",
            "fundamental": "d248301730257ac79f67501edeb78857187293e1fe531e395ab2dba4554ea7b8"}


# ---- 1. the fee module ---------------------------------------------------------------------------------------------------

def test_the_module_states_its_version_constants_and_their_sources():
    assert fm.V632_FEE_MAKING_VERSION == "fee_making_v6_32" and fm.CANCEL_FEE_GATE == "V632_FEE_GATE"
    assert (fm.FEE_WEIGHT_DEFAULT, fm.FEE_SPEC, fm.HORIZON_NS, fm.HALF_WINDOW) == (0.005, 64, 20_000_000_000, 15)
    assert (fm.CAPTURE_REACH_WINDOWS, fm.CAPTURE_MARKET_MIN, fm.MARK_REACH_WINDOWS) == (8, 5, 4)
    assert (fm.FLOOR_REFERENCE_PRINTS, fm.DUST_FLOOR_FRAC, fm.CAPTURE_FLUSH_NS) == (256, 0.1, 60_000_000_000)
    assert fm.WINDOW_NS == 10_800 * S and fm.MIN_FILLS == 256 and fm.RING_MAX == 2048
    assert set(fm.__all__) == {"CANCEL_FEE_GATE", "CAPTURE_FLUSH_NS", "FEE_SPEC", "FEE_WEIGHT_DEFAULT", "HALF_WINDOW",
                               "HORIZON_NS", "MIN_FILLS", "RING_MAX", "RealizedTracker", "V632_FEE_MAKING_VERSION",
                               "WINDOW_NS", "adds", "deep_pairs", "fee_gated", "gate_active", "maker_fee_rate",
                               "min_sizes", "parse_fee_weight"}
    lines = {ln.split("=")[0].strip(): ln for ln in MODULE.splitlines() if re.match(r"^[A-Z_]+ = ", ln)}
    for name, source in (("FEE_WEIGHT_DEFAULT", "making_fee_weight"), ("FEE_SPEC", "__spec_version__"),
                         ("HORIZON_NS", "REALIZED_HORIZON_NS_DEFAULT"), ("HALF_WINDOW", "CAPTURE_W"),
                         ("CAPTURE_REACH_WINDOWS", "debeta.py"), ("CAPTURE_MARKET_MIN", "debeta.py"),
                         ("MARK_REACH_WINDOWS", "debeta.py"), ("FLOOR_REFERENCE_PRINTS", "debeta.py"),
                         ("DUST_FLOOR_FRAC", "debeta.py"), ("CAPTURE_FLUSH_NS", "debeta.py"),
                         ("WINDOW_NS", "kappa.lookback"), ("MIN_FILLS", "fills"), ("RING_MAX", "prints")):
        assert source in lines[name], name                     # every constant names where it comes from
    doc = ast.get_docstring(ast.parse(MODULE))
    for needle in ("19f9fbd", "fee_weight x Mf", "0.005", "31.7 bps", "q -> 0.000", "70-80%", "pooled", "fg=2",
                   "FRACTION", "34.8 bps", "s == 0", "470 / 470", "543 / 543", "byte for byte", "STRUCTURAL",
                   "per fill identical", "+0.152", "-0.173", "starts empty", "set_active"):
        assert needle in doc, needle


def test_the_module_imports_only_the_deep_layers_sides_and_v641s_adds_and_loads_no_lock_socket_or_url():
    tree = ast.parse(MODULE)
    imported, plain = {}, []
    for n in tree.body:
        if isinstance(n, ast.Import):
            plain += [a.name for a in n.names]
        if isinstance(n, ast.ImportFrom):
            imported[n.module] = sorted(a.name for a in n.names)
    assert plain == ["bisect", "math"]                          # the stdlib the vendored debeta code itself uses
    assert imported == {"__future__": ["annotations"], "collections": ["deque"], "typing": ["Any", "Iterable"],
                        "research_v633_deep_layer": ["DEEP_LEVELS", "SIDE_BUY", "SIDE_SELL"],
                        "research_v641_pace": ["adds"]}, imported
    state = {n.module: sorted(a.name for a in n.names) for n in ast.parse(STATE_MODULE).body
             if isinstance(n, ast.ImportFrom)}
    assert state == {"__future__": ["annotations"], "collections.abc": ["Mapping"], "typing": ["Any"]}
    assert [a.name for n in ast.parse(STATE_MODULE).body if isinstance(n, ast.Import) for a in n.names] == ["math"]
    code = "\n".join(ast.unparse(n) for n in tree.body[1:])
    for word in ("fcntl", "flock", "socket", "/tmp", "multiprocessing", "subprocess", "random", "urllib", "open(",
                 "os.", "time.", "thread", "global "):
        assert word not in code, word
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); before = set(sys.modules); "
             "import research_v6_32_fee_making, research_v6_32_state_fp; new = set(sys.modules) - before; "
             "print(sorted(m for m in new if m.split('.')[0] in ('fcntl', 'socket', 'urllib', 'http', 'multiprocessing', "
             "'subprocess', 'random', 'research_v616_fair_value', 'research_v6_29_touch_move', 'threading')))")
    out = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(STRATEGY)], capture_output=True, text=True,
                         timeout=120, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"})
    assert out.returncode == 0 and out.stdout.strip() == "[]", (out.stdout, out.stderr)


def test_the_vendored_functions_are_taos_064s_byte_for_byte():
    tree = ast.parse(MODULE)
    fns = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    for name, digest in DEBETA_064.items():
        assert hashlib.sha256(ast.unparse(fns[name]).encode()).hexdigest() == digest, name
    start = MODULE.index("# ---- vendored from taos 0.6.4 taos/im/validator/debeta.py (19f9fbd), byte for byte")
    stop = MODULE.index("# ---- the gate")
    block = ast.parse(MODULE[start:stop])
    assert [n.name for n in block.body] == list(DEBETA_064)    # the vendored block holds them and nothing else


def test_the_gate_and_the_tracker_carry_no_sample_sized_constant():
    """The gate's numbers are 0.0 alone (the weight, the rate and the estimate come in); the tracker's are the
    validator's own constants by name, indices, 0, 1 and 1e4 (bps in the snapshot)."""
    tree = ast.parse(MODULE)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    for name in ("gate_active", "fee_gated", "adds", "deep_pairs", "maker_fee_rate", "parse_fee_weight"):
        nums = {n.value for n in ast.walk(fns[name]) if isinstance(n, ast.Constant)
                and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)}
        assert nums <= {0.0}, (name, nums)
    tracker = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "RealizedTracker")
    nums = {n.value for n in ast.walk(tracker) if isinstance(n, ast.Constant)
            and isinstance(n.value, (int, float)) and not isinstance(n.value, bool)}
    # indices / signs (0, 1, the -1 "no miner" id), 2 x the capture reach + 1 (the ring's floor), 1e4 (bps) and the
    # snapshot's rounding digits (4, 6)
    assert nums == {0, 1, 2, 4, 6, 1e4}, nums


# ---- 2. golden: the 0.6.4 original's realized spread, fill by fill ---------------------------------------------------------
# The streams below are rebuilt exactly as scratchpad k91/build/golden.py built them; there the ORIGINAL 0.6.4 debeta
# (accumulate_book_capture per book with prints, then flush_capture_state on every book, state by state -- trade.py's
# feed) and this tracker booked IDENTICAL (fill ts, realized, notional) for every one of our fills (10-16 03:00 JST).
# The pins are the original's: count, sum and the sha256 of the sorted per-fill list.

OUR = 42
GOLDEN = {
    "clear": (729, 2.3765283204946286, 101965.3825,
              "727d4f154b9b37fc518ea75c5c1436d08e4526c045b2c638e3a9a5a2c2c5f610"),
    "dust": (462, 5.8807290370615934, 139308.442,
             "1fa35d2281b49f9b169d309d43dded84471de47476c1391e5dc1af42a8f1bb98"),
    "quiet": (253, 2.622614987042212, 51361.2125,
              "bd160cd1743c3124c27850fafb8b24194c2223276cb9c3f2717862b6c11f4488"),
}
SCENARIOS = {
    "clear": dict(seed=11, states=420, books=(0, 1, 2), others=True, dust=False, min_size=0.25),
    "dust": dict(seed=23, states=420, books=(3, 4), others=False, dust=True, min_size=0.25),
    "quiet": dict(seed=37, states=400, books=(5, 6), others=False, dust=False, min_size=0.25, quiet=(6,)),
}


def _lcg(seed):
    x = seed
    while True:
        x = (x * 6364136223846793005 + 1442695040888963407) % (1 << 64)
        yield (x >> 11) / float(1 << 53)


def _stream(seed, *, states, books, others, dust, quiet=(), selftrades=True):
    """[(state ts, {book: [wire trade dicts]})]: k91/build/golden.py's stream, verbatim."""
    u = _lcg(seed).__next__
    mid = {b: 100.0 + 10 * b for b in books}
    out, tid, oid = [], 0, 1000
    sizes = (0.25, 0.5, 1.0, 2.0, 3.0) if not dust else (0.004, 0.01, 0.25, 0.5, 1.0, 2.0, 3.0, 12.0)
    for k in range(states):
        ts0 = (1000 + k) * S
        per = {}
        for b in books:
            n = int(u() * 7)
            if b in quiet and u() < 0.9:
                n = 0
            t = ts0 - S
            prints = []
            for _i in range(n):
                t += int(u() * (S // 8)) + 1
                mid[b] = round(mid[b] + (int(u() * 5) - 2) * 0.01, 2)
                s = int(u() * 2)
                p = round(mid[b] + (0.01 if s == 0 else -0.01) * int(u() * 3), 2)
                q = sizes[int(u() * len(sizes))]
                r = u()
                ta = -(1 + int(u() * 3000))
                if r < 0.20:
                    ma = OUR
                    if others and u() < 0.25:
                        ta = 7
                elif selftrades and r < 0.23:
                    ma = ta = OUR
                elif others and r < 0.30:
                    ma = 7 if u() < 0.5 else 9
                    if u() < 0.3:
                        ta = OUR
                else:
                    ma = -(1 + int(u() * 3000))
                oid += 1
                mi = oid * 2 + (0 if s == 1 else 1) if ma == OUR and ta != OUR else oid * 2
                rate = 0.0005 * (int(u() * 13) - 2)
                prints.append({"y": "t", "i": tid, "s": s, "t": t, "q": q, "p": p, "Ti": oid * 2 + 1, "Ta": ta,
                               "Tf": 0.0, "Mi": mi, "Ma": ma, "Mf": round(rate * p * q, 10), "cr": 0, "Toi": 0})
                tid += 1
            per[b] = prints
        out.append((ts0, per))
    return out


def _feed(stream, min_size, tracker=None, active=True):
    tr = tracker or fm.RealizedTracker()
    tr.set_active(active, stream[0][0])         # the gate on from the first state: every fill of ours counts
    for ts0, per in stream:
        tr.begin(ts0)
        for b, trades in per.items():
            if trades:
                tr.observe(b, trades, OUR, min_size, ts0)
        tr.release(ts0)
    return tr


def test_the_tracker_books_the_064_originals_realized_spread_fill_by_fill():
    for name, sc in SCENARIOS.items():
        kw = {k: v for k, v in sc.items() if k != "min_size"}
        tr = _feed(_stream(**kw), sc["min_size"])
        entries = sorted(tr.window.entries)
        n, realized, notional, digest = GOLDEN[name]
        assert len(entries) == n == tr.counts["released"], name
        assert math.isclose(sum(r for _t, r, _q in entries), realized, rel_tol=1e-12, abs_tol=1e-12), name
        assert math.isclose(sum(q for _t, _r, q in entries), notional, rel_tol=1e-12), name
        assert hashlib.sha256(repr(entries).encode()).hexdigest() == digest, name
        assert tr.counts["malformed"] == 0 and tr.counts["evicted"] == 0, name
    quiet = _feed(_stream(**{k: v for k, v in SCENARIOS["quiet"].items() if k != "min_size"}), 0.25)
    assert quiet.counts["stale"] == 19 and quiet.counts["settled"] == 234         # the quiet book's stale releases


def test_netted_by_the_validator_is_gross_minus_the_weight_times_the_fee_so_the_gate_is_exact():
    """0.6.4 books R - W x Mf per maker fill and Mf = rate x p x q: sum R - W x rate x sum N <= 0 iff R / N <= W x rate."""
    for est, rate, w in ((1.5e-5, 0.004, 0.005), (2.0e-5, 0.004, 0.005), (2.5e-5, 0.004, 0.005), (-1e-6, -0.001, 0.005),
                         (-6e-6, -0.001, 0.005), (0.0, 0.0, 0.005)):
        notional = 1000.0
        netted = est * notional - w * rate * notional
        assert fm.fee_gated(est, rate, w) == (netted <= 1e-15), (est, rate, w)


# ---- 3. the tracker ---------------------------------------------------------------------------------------------------

def _print(t, p=100.00, q=1.0, s=0, ma=-5, ta=-6, mi=1):
    """One wire trade print (book._raw["e"]'s shape); background on both sides unless ``ma`` / ``ta`` say otherwise."""
    return {"y": "t", "i": 0, "s": s, "t": int(t), "q": q, "p": p, "Ti": 2, "Ta": ta, "Tf": 0.0, "Mi": mi, "Ma": ma,
            "Mf": 0.0, "cr": 0, "Toi": 0}


def _tape(n, *, t0=NOW, step=S // 2, ours=None, price=100.00, qty=1.0, uid=OUR):
    """``n`` background prints ``step`` apart from ``t0``; ``ours`` {k: (s, p)}: print k is ``uid``'s maker fill."""
    out = []
    for k in range(n):
        if ours and k in ours:
            s, p = ours[k]
            out.append(_print(t0 + k * step, p=p, q=qty, s=s, ma=uid, ta=-6))
        else:
            out.append(_print(t0 + k * step, p=price, q=qty))
    return out


def _tracker(**kw):
    """A tracker whose gate is on from before the tape (its window counts every fill of ours)."""
    tr = fm.RealizedTracker(**kw)
    tr.set_active(True, NOW - S)
    return tr


def test_the_maker_side_is_the_validators_s1_we_bought_s0_we_sold():
    """A maker buy below a flat market and a maker sell above it both keep a positive spread; the same prints with the
    side flipped lose it -- s == 1 the maker bought, s == 0 the maker sold (debeta._attribute_capture)."""
    for s, p, sign in ((1, 99.99, +1), (0, 100.01, +1), (0, 99.99, -1), (1, 100.01, -1)):
        tr = _tracker()
        tr.observe(1, _tape(120, ours={20: (s, p)}), OUR, 0.25, NOW)
        assert tr.release(NOW + 70 * S) == 1
        (_ts, realized, notional), = tr.window.entries
        assert notional == pytest.approx(p) and math.copysign(1.0, realized) == sign and realized != 0.0, (s, p)
        cap_mid = (30 * 100.00 + p) / 31                        # its own capture window: 15 prints a side
        assert abs(realized) == pytest.approx(abs(cap_mid - p))  # clamped to its captured spread (_capped_mark)


def test_a_fill_is_released_when_its_capture_side_and_the_horizon_window_have_settled_and_not_before():
    """Prints 0.5 sim-s apart, ours at print 10: its horizon print is print 50 (10 + 20 s), released once 15 more
    prints follow it (print 65), whatever the request's clock (well before its stale time)."""
    tape = _tape(80, ours={10: (1, 99.99)})
    tr = fm.RealizedTracker()
    tr.observe(1, tape[:65], OUR, 0.25, NOW)
    assert tr.release(NOW + 33 * S) == 0 and tr.pending() == 1
    assert tr.books[1]["due"] == NOW + 5 * S + 80 * S          # its stale time: print 10 + HORIZON + FLUSH
    tr.observe(1, tape[65:66], OUR, 0.25, NOW)
    assert tr.release(NOW + 33 * S) == 1 and tr.counts["settled"] == 1 and tr.pending() == 0


def test_a_quiet_book_releases_a_fill_stale_after_the_horizon_and_the_flush_against_the_latest_window():
    tape = _tape(20, ours={18: (1, 99.99)})
    tr = _tracker()
    tr.observe(1, tape, OUR, 0.25, NOW)
    t_fill = NOW + 9 * S
    assert tr.release(t_fill + 80 * S - 1) == 0
    assert tr.release(t_fill + 80 * S) == 1 and tr.counts["stale"] == 1
    (_ts, realized, _q), = tr.window.entries
    assert realized > 0.0                                       # marked against the latest prints at 100.00


def test_a_clock_that_runs_back_flushes_restarts_the_streams_and_keeps_the_windows_age():
    tr = _tracker()
    tr.begin(NOW)
    tr.observe(1, _tape(30, ours={5: (1, 99.99)}), OUR, 0.25, NOW)
    tr.release(NOW + 15 * S)
    assert tr.pending() == 1
    assert tr.begin(NOW + 20 * S) is False
    assert tr.begin(NOW - 100 * S) is True                      # a new simulation / a checkpoint rewind
    assert tr.counts["forced"] == 1 and tr.counts["resets"] == 1 and tr.books == {} and tr.pending() == 0
    (ts, _r, _q), = tr.window.entries
    assert ts == NOW + int(2.5 * S) - 120 * S                   # shifted by (new - old) clock: its age is kept


def test_the_window_holds_the_making_window_and_the_estimate_needs_min_fills():
    tr = fm.RealizedTracker(min_fills=3)
    for k in range(2):
        tr.window.add(NOW + k * S, 2e-5 * 100.0, 100.0)
    assert tr.estimate() is None and tr.window_fills() == 2
    tr.window.add(NOW + 2 * S, -1e-5 * 100.0, 100.0)
    assert tr.estimate() == pytest.approx((2e-5 * 2 - 1e-5) / 3)
    tr.release(NOW + fm.WINDOW_NS)                              # nothing older than now - WINDOW_NS yet
    assert tr.window_fills() == 3
    tr.release(NOW + S + fm.WINDOW_NS)                          # the first leaves
    assert tr.window_fills() == 2 and tr.counts["expired"] == 1 and tr.estimate() is None
    tr.release(NOW + 3 * S + fm.WINDOW_NS)
    assert tr.window_fills() == 0 and tr.window.realized == 0.0 and tr.window.notional == 0.0
    assert tr.counts["expired"] == 3
    assert fm.RealizedTracker().min_fills == fm.MIN_FILLS == 256


def test_a_burst_past_the_ring_releases_the_fill_whose_print_leaves_it_and_memory_stays_bounded():
    tr = _tracker(ring_max=1)                                   # floored at the reaches: 2 x 8W + 1 = 241 prints
    assert tr.ring_max == 241
    tape = _tape(400, ours={3: (1, 99.99)}, step=S // 100)      # 400 prints in 4 sim-s: no horizon print yet
    tr.observe(1, tape, OUR, 0.25, NOW)
    assert tr.counts["evicted"] == 1 and tr.counts["forced"] == 1 and tr.pending() == 0 and tr.window_fills() == 1
    st = tr.books[1]
    assert len(st["prices"]) <= tr.ring_max and st["n"] == 400
    assert len(st["ref_sorted"]) == len(st["ref_fifo"]) == fm.FLOOR_REFERENCE_PRINTS
    tr2 = fm.RealizedTracker()
    tr2.observe(1, _tape(2000, step=S // 10), OUR, 0.25, NOW)   # no fill of ours: the window keeps one reach
    assert len(tr2.books[1]["prices"]) == fm.CAPTURE_REACH_WINDOWS * fm.HALF_WINDOW == 120
    assert tr2.books[1]["base"] == 2000 - 120 and tr2.books[1]["n"] == 2000


def test_only_our_maker_fills_are_queued_never_a_self_trade_another_makers_or_a_taker_fill_of_ours():
    tape = [_print(NOW + k * S // 2) for k in range(10)]
    tape += [_print(NOW + 6 * S, ma=OUR, ta=OUR, s=1, p=99.99),              # a self-trade: booked nowhere
             _print(NOW + 7 * S, ma=7, ta=OUR, s=1, p=99.99),                # we took another maker's quote
             _print(NOW + 8 * S, ma=7, ta=9, s=1, p=99.99),                  # two other miners
             _print(NOW + 9 * S, ma=OUR, ta=7, s=0, p=100.01)]               # ours, taken by a miner: queued
    tr = fm.RealizedTracker()
    assert tr.observe(1, tape, OUR, 0.25, NOW) == 1
    assert tr.counts["self_trades"] == 1 and tr.counts["own_fills"] == 1 and tr.counts["prints"] == 14
    assert tr.books[1]["bgs"][-3:] == [False, False, False] and tr.books[1]["wts"][-2:] == [0.0, 0.0]
    assert fm.RealizedTracker().observe(1, tape, None, 0.25, NOW) == 0      # no uid: nothing of ours
    assert fm.RealizedTracker().observe(1, tape, True, 0.25, NOW) == 0      # a bool is no uid


def test_malformed_prints_are_skipped_and_counted_and_nothing_raises():
    good = _print(NOW, ma=OUR, s=1, p=99.99)
    bad = [None, 5, "x", {}, {"p": "x"}, {"p": float("nan"), "q": 1}, {"p": -1.0, "q": 1}, {"p": 100.0, "q": "x"},
           {"p": 100.0, "q": float("inf")}, {"p": 100.0, "q": 1.0, "t": "x"},
           dict(good, s=2), dict(good, s=True), dict(good, q=0.0)]
    tr = fm.RealizedTracker()
    n = tr.observe(1, bad + [good], OUR, 0.25, NOW)
    assert n == 1 and tr.counts["own_fills"] == 1
    # ten unreadable prints, and three readable prints whose fill of ours is not (side 2, side True, size 0): those
    # three still shape the window, as they do the validator's
    assert tr.counts["malformed"] == 13 and tr.counts["prints"] == 4
    assert tr.observe("x", [good], OUR, 0.25, NOW) == 0 and tr.observe(None, [good], OUR) == 0
    assert tr.counts["malformed"] == 15
    assert tr.observe(2, None, OUR) == 0 and tr.observe(2, [good], OUR, "x", "y") == 1   # bad min_size, bad ts
    obj = types.SimpleNamespace(p=99.99, q=1.0, s=1, t=NOW + S, Ma=OUR, Ta=None)          # a parsed TradeInfo
    assert tr.observe(3, [obj], OUR, 0.25, NOW) == 1
    assert tr.books[3]["bgs"] == [True]                          # Ta None: the background (debeta reads None as < 0)
    tr.books[1]["rpend"].append((0, NOW, {"p": "x", "q": 1.0}, True))  # an unreadable queued fill never blocks
    tr.release(NOW + 200 * S)
    assert tr.pending() == 0 and tr.release(None) == 0 and tr.release("x") == 0
    assert tr.begin(None) is False and tr.begin("x") is False


def _two_fills():
    """Two states of a book, 40 sim-s each, one fill of ours in each (print 2), each settled within its own state."""
    first = _tape(80, ours={2: (1, 99.99)})
    second = _tape(80, t0=NOW + 40 * S, ours={2: (1, 99.99)})
    return first, second


def test_fills_seen_while_the_gate_is_off_are_released_on_time_and_never_counted():
    first, second = _two_fills()
    off, on = fm.RealizedTracker(), _tracker()
    for tr in (off, on):
        tr.observe(1, first, OUR, 0.25, NOW)
        tr.release(NOW + 40 * S)
    assert off.counts["released"] == on.counts["released"] == 1 and off.counts["settled"] == 1
    assert off.counts["dropped_inactive"] == 1 and off.window_fills() == 0 and off.estimate() is None
    assert on.counts["counted"] == 1 and on.window_fills() == 1
    assert off.window_start_ns is None and off.period == 0 and not off.active


def test_each_activation_starts_the_window_empty_and_off_on_off_on_clears_it_again():
    first, second = _two_fills()
    tr = _tracker()
    tr.observe(1, first, OUR, 0.25, NOW)
    tr.release(NOW + 40 * S)
    assert tr.window_fills() == 1 and tr.period == 1 and tr.window_start_ns == NOW - S
    assert tr.set_active(True, NOW + 40 * S) is False and tr.window_fills() == 1          # still on: no restart
    assert tr.set_active(False, NOW + 40 * S) is False and tr.counts["deactivations"] == 1
    assert tr.window_fills() == 1                                     # off: kept, unused; cleared at the next start
    assert tr.set_active(True, NOW + 41 * S) is True
    assert tr.window_fills() == 0 and tr.window.realized == 0.0 and tr.window.notional == 0.0
    assert (tr.period, tr.window_start_ns, tr.counts["activations"]) == (2, NOW + 41 * S, 2)
    tr.observe(1, second, OUR, 0.25, NOW + 41 * S)
    tr.release(NOW + 80 * S)
    assert tr.window_fills() == 1 and tr.counts["counted"] == 2
    assert tr.set_active(False, NOW + 80 * S) is False and tr.set_active(True, NOW + 81 * S) is True
    assert tr.window_fills() == 0 and tr.period == 3 and tr.estimate() is None


def test_a_fill_seen_in_an_earlier_period_is_dropped_when_it_is_released_in_a_later_one():
    tape = _tape(80, ours={2: (1, 99.99)})
    tr = _tracker()
    tr.observe(1, tape[:30], OUR, 0.25, NOW)                       # seen in period 1, its horizon not reached
    tr.release(NOW + 15 * S)
    assert tr.pending() == 1
    tr.set_active(False, NOW + 15 * S)
    tr.set_active(True, NOW + 16 * S)                               # period 2: the window starts empty
    tr.observe(1, tape[30:], OUR, 0.25, NOW + 16 * S)
    tr.release(NOW + 40 * S)
    assert tr.counts["released"] == 1 and tr.counts["dropped_inactive"] == 1 and tr.window_fills() == 0
    stays = _tracker()                                               # the same fill, the gate on throughout: counted
    stays.observe(1, tape[:30], OUR, 0.25, NOW)
    stays.release(NOW + 15 * S)
    stays.observe(1, tape[30:], OUR, 0.25, NOW + 16 * S)
    stays.release(NOW + 40 * S)
    assert stays.counts["counted"] == 1 and stays.window_fills() == 1


def test_the_gate_never_moves_a_release_the_print_windows_and_the_queue_run_regardless():
    """The same stream through a tracker always on, one always off and one flipping at every state: the same releases
    at the same requests (settled, stale and forced alike), the same pending queues and print windows."""
    stream = _stream(**{k: v for k, v in SCENARIOS["quiet"].items() if k != "min_size"})
    trackers = {"on": fm.RealizedTracker(), "off": fm.RealizedTracker(), "flip": fm.RealizedTracker()}
    trackers["on"].set_active(True, stream[0][0])
    history = {k: [] for k in trackers}
    for i, (ts0, per) in enumerate(stream):
        for name, tr in trackers.items():
            tr.begin(ts0)
            if name == "flip":
                tr.set_active(i % 2 == 0, ts0)
            for b, trades in per.items():
                if trades:
                    tr.observe(b, trades, OUR, 0.25, ts0)
            tr.release(ts0)
            history[name].append((tr.counts["released"], tr.counts["settled"], tr.counts["stale"], tr.pending(),
                                  tuple(len(st["prices"]) for st in tr.books.values())))
    assert history["on"] == history["off"] == history["flip"]
    on, off, flip = trackers["on"], trackers["off"], trackers["flip"]
    assert on.counts["counted"] == GOLDEN["quiet"][0] and off.counts["counted"] == 0
    assert off.counts["dropped_inactive"] == GOLDEN["quiet"][0] == flip.counts["counted"] + flip.counts["dropped_inactive"]
    # flipping at every state, each fill (20+ sim-s to settle) is released in a later period than its own: dropped
    assert flip.counts["counted"] == 0 and flip.window_fills() == 0 and flip.counts["activations"] == 200
    assert on.window_fills() == GOLDEN["quiet"][0] and off.window_fills() == 0


def test_the_tracker_snapshot_carries_the_estimate_in_bps_and_its_tally():
    tr = fm.RealizedTracker(min_fills=1)
    tr.window.add(NOW, 2e-5 * 50.0, 50.0)
    snap = tr.snapshot()
    assert snap["estimate_bps"] == pytest.approx(0.2) and snap["window_fills"] == 1 and snap["window_notional"] == 50.0
    for key in ("pending", "books", "prints", "own_fills", "self_trades", "malformed", "released", "settled", "stale",
                "forced", "evicted", "expired", "resets", "counted", "dropped_inactive", "activations", "deactivations",
                "window_start_ns", "tracker_active", "period"):
        assert key in snap, key
    assert fm.RealizedTracker().snapshot()["estimate_bps"] is None


# ---- 4. the gate's helpers --------------------------------------------------------------------------------------------

def test_the_gate_is_active_only_on_a_064_state_with_the_switch_on_and_a_positive_weight():
    assert fm.gate_active(True, 64, 0.005) and fm.gate_active(True, 65, 0.01) and fm.gate_active(1, 64.0, 1e-9)
    for switch, spec, w in ((True, 63, 0.005), (True, 62, 0.005), (True, None, 0.005), (False, 64, 0.005),
                            (True, 64, 0.0), (True, 64, -0.005), (True, 64, None), (True, "x", 0.005),
                            (True, float("nan"), 0.005), (True, 64, float("nan")), (True, True, 0.005)):
        assert not fm.gate_active(switch, spec, w), (switch, spec, w)


def test_a_book_is_gated_iff_the_estimate_does_not_exceed_the_weight_times_its_maker_fee_rate():
    w = 0.005
    assert fm.fee_gated(1.5e-5, 0.004, w) and fm.fee_gated(2e-5, 0.004, w)            # at the bar: gated (<=)
    assert not fm.fee_gated(2.0000001e-5, 0.004, w) and not fm.fee_gated(1.5e-5, 0.002, w)
    assert not fm.fee_gated(1e-6, -0.001, w) and fm.fee_gated(-6e-6, -0.001, w)        # a rebate book: a negative bar
    assert fm.fee_gated(-1e-5, 0.0, w) and fm.fee_gated(0.0, 0.0, w) and not fm.fee_gated(1e-12, 0.0, w)
    for est, rate, weight in ((None, 0.004, w), (1e-5, None, w), (1e-5, 0.004, None), ("x", 0.004, w),
                              (1e-5, float("nan"), w), (float("inf"), 0.004, w), (True, 0.004, w)):
        assert not fm.fee_gated(est, rate, weight), (est, rate, weight)


def test_adds_is_v623s_rule_a_flat_book_adds_on_both_sides():
    assert fm.adds(BUY, 0.0) and fm.adds(SELL, 0.0) and fm.adds(BUY, 3.0) and not fm.adds(SELL, 3.0)
    assert fm.adds(SELL, -3.0) and not fm.adds(BUY, -3.0)
    assert fm.adds(BUY, -1e-10) and fm.adds(SELL, 1e-10)                                # v6.4.1's flat epsilon
    assert fm.adds(BUY, None) and fm.adds(SELL, "x") and fm.adds(BUY, float("nan"))     # unreadable: flat
    assert not fm.adds("hold", 0.0) and not fm.adds(None, 0.0)
    for inv in (-5.0, -1e-6, 0.0, 1e-6, 5.0):
        for side in (BUY, SELL):
            assert fm.adds(side, inv) == fm._v641_adds(side, inv), (side, inv)


def test_the_deep_pairs_are_every_level_of_each_gated_side():
    assert fm.deep_pairs([BUY]) == frozenset((BUY, k) for k in range(dl.DEEP_LEVELS)) and dl.DEEP_LEVELS == 3
    assert fm.deep_pairs({BUY, SELL}) == frozenset((s, k) for s in (BUY, SELL) for k in range(3))
    assert fm.deep_pairs(()) == frozenset() and fm.deep_pairs(["hold"]) == frozenset()
    assert fm.deep_pairs([BUY]) == vo.withheld(BUY, vo.LEVELS_DEEP)                     # v6.23 S1's shape


def test_the_maker_fee_rate_is_the_accounts_fraction_or_nothing():
    acct = lambda rate: types.SimpleNamespace(fees=types.SimpleNamespace(maker_fee_rate=rate))     # noqa: E731
    assert fm.maker_fee_rate(acct(0.0035)) == 0.0035 and fm.maker_fee_rate(acct(-0.002)) == -0.002
    for bad in (None, types.SimpleNamespace(), types.SimpleNamespace(fees=None), acct(None), acct("x"),
                acct(float("nan")), acct(True)):
        assert fm.maker_fee_rate(bad) is None, bad

    class Boom:
        @property
        def fees(self):
            raise RuntimeError("boom")
    assert fm.maker_fee_rate(Boom()) is None


def test_the_weight_parses_to_a_finite_number_at_least_zero_else_the_validators_default():
    for raw, want in ((0.005, 0.005), ("0.005", 0.005), (0.01, 0.01), (0, 0.0), ("0", 0.0), (1, 1.0)):
        assert fm.parse_fee_weight(raw) == want, raw
    for raw in (-0.005, "x", None, float("nan"), float("inf"), True, ""):
        assert fm.parse_fee_weight(raw) == fm.FEE_WEIGHT_DEFAULT, raw


def test_the_minimum_order_size_is_the_books_class_one_else_the_top_level_one():
    cfg = types.SimpleNamespace(min_order_size=0.25, classes=[
        {"name": "simulation_0", "books": list(range(96)), "config": {"min_order_size": 0.25}},
        {"name": "simulation_1", "books": list(range(96, 128)), "config": {"min_order_size": 2.5364}}])
    top, per = fm.min_sizes(cfg)
    assert top == 0.25 and per[5] == 0.25 and per[100] == 2.5364 and len(per) == 128
    assert fm.min_sizes(types.SimpleNamespace(min_order_size=0.25)) == (0.25, {})
    assert fm.min_sizes(None) == (0.0, {}) and fm.min_sizes({"min_order_size": "x"}) == (0.0, {})
    assert fm.min_sizes(types.SimpleNamespace(min_order_size=0.25, classes="bad")) == (0.25, {})
    assert fm.min_sizes(types.SimpleNamespace(min_order_size=0.25, classes=[{"books": ["x"],
                                                                            "config": {"min_order_size": 1.0}}])) \
        == (0.25, {})


# ---- 5. the state-fundamental module -----------------------------------------------------------------------------------

def test_the_state_module_reads_the_books_own_fundamental_as_064_carries_it():
    assert sf.V632_STATE_FUNDAMENTAL_VERSION == "state_fundamental_v6_32" and sf.BOOK_KEY == "f"
    assert sf.REASON_STATE == "state"
    doc = ast.get_docstring(ast.parse(STATE_MODULE))
    for needle in ("BOOK_KEY", "stamp", "fundamental", "8-14 sim-s", "STRUCTURAL", "0.6.3 state carries no"):
        assert needle in doc, needle
    assert sf.fundamental_value({"f": 101.5}) == 101.5 and sf.fundamental_value(types.SimpleNamespace(
        fundamental=99.0)) == 99.0
    for bad in ({"f": None}, {"f": 0.0}, {"f": -1.0}, {"f": float("nan")}, {"f": float("inf")}, {"f": "x"},
                {"f": True}, {}, None, types.SimpleNamespace(), types.SimpleNamespace(fundamental="x")):
        assert sf.fundamental_value(bad) is None, bad

    class Boom:
        @property
        def fundamental(self):
            raise RuntimeError("boom")
    assert sf.fundamental_value(Boom()) is None


def test_the_state_map_takes_the_lazy_raw_books_without_building_a_book():
    class LazyBooksLike(dict):
        pass
    books = LazyBooksLike({3: "never touched"})
    books._raw_books = {3: {"f": 100.5}, 5: {"f": None}, 7: {"f": 98.0}, "x": {"f": 1.0}, 9: "not a book"}
    assert sf.state_map(books) == {3: 100.5, 7: 98.0}
    assert sf.state_map({3: types.SimpleNamespace(fundamental=100.5), 4: object()}) == {3: 100.5}
    assert sf.state_map(None) == {} and sf.state_map(5) == {}


def test_a_book_takes_the_state_value_only_when_v616s_book_sanity_holds():
    fp = {3: 100.5}
    assert sf.book_value(fp, 3, 100.00, 100.03) == (100.5, "state")
    assert sf.book_value({"3": 100.5}, 3, 100.00, 100.03) is None      # the state map's keys are ints by construction
    assert sf.book_value(fp, "3", 100.00, 100.03) == (100.5, "state")
    for bid, ask in ((0.0, 100.03), (100.03, 100.03), (100.04, 100.03), (None, 100.03), ("x", 1.0),
                     (float("nan"), 100.0), (100.0, float("inf"))):
        assert sf.book_value(fp, 3, bid, ask) is None, (bid, ask)
    assert sf.book_value({3: 0.0}, 3, 100.00, 100.03) is None and sf.book_value({}, 3, 100.00, 100.03) is None
    assert sf.book_value(None, 3, 100.00, 100.03) is None and sf.book_value(fp, 4, 100.00, 100.03) is None


def test_the_s9_snapshot_has_the_pages_shape_at_the_states_time():
    snap = sf.s9_snapshot({3: 100.5}, SIM, NOW)
    assert snap == {"fp": {3: 100.5}, "sim_id": SIM, "sim_ts_ns": NOW}
    assert sf.s9_snapshot({}, SIM, NOW) is None and sf.s9_snapshot(None, SIM, NOW) is None
    for ts in (0, -1, None, "x", True):
        assert sf.s9_snapshot({3: 100.5}, SIM, ts) is None, ts
    h = tm.PageHistory()                                         # S9's history takes it as it takes a page
    assert h.observe(snap, NOW, SIM) and h.pages == [(NOW, NOW, {3: 100.5})]
    assert not h.observe(sf.s9_snapshot({3: 100.6}, SIM, NOW), NOW, SIM)                 # one entry per state time
    assert h.observe(sf.s9_snapshot({3: 100.6}, SIM, NOW + S), NOW + S, SIM) and len(h.pages) == 2


# ---- 6. the vendored protocol -----------------------------------------------------------------------------------------

def _class(text, name):
    return next(n for n in ast.parse(text).body if isinstance(n, ast.ClassDef) and n.name == name)


def test_the_vendored_book_keeps_the_fundamental_exactly_as_064s_book():
    book = _class(MODELS, "Book")
    fields = [n for n in book.body if isinstance(n, ast.AnnAssign)]
    assert [ast.unparse(n.target) for n in fields] == ["i", "r", "b", "a", "e", "f"]
    assert hashlib.sha256(ast.unparse(fields[-1]).encode()).hexdigest() == BOOK_064["f"]
    prop = next(n for n in book.body if isinstance(n, ast.FunctionDef) and n.name == "fundamental")
    assert hashlib.sha256(ast.unparse(prop).encode()).hexdigest() == BOOK_064["fundamental"]
    assert MODELS.count('    f: float | None = Field(alias="fundamental", default=None)') == 1


def test_the_lazy_book_reads_the_raw_f_never_raises_and_its_parse_passes_it_on():
    lazy = _class(MODELS, "LazyBook")
    fn = next(n for n in lazy.body if isinstance(n, ast.FunctionDef) and n.name == "fundamental")
    ns: dict = {}
    exec(compile(ast.Module(body=[ast.parse("class L:\n" + textwrap.indent(ast.unparse(fn), "    ")).body[0]],
                            type_ignores=[]), "<LazyBook.fundamental>", "exec"), ns)
    L = ns["L"]
    for raw, want in (({"f": 100.5}, 100.5), ({}, None), ({"f": None}, None), (None, None), ("x", None)):
        obj = L()
        obj._raw = raw
        assert obj.fundamental == want, raw
    assert L().fundamental is None                              # no raw at all: None, never an AttributeError
    parse = next(n for n in lazy.body if isinstance(n, ast.FunctionDef) and n.name == "parse")
    call = next(n for n in ast.walk(parse) if isinstance(n, ast.Call) and ast.unparse(n.func) == "Book.model_construct")
    assert [k.arg for k in call.keywords] == ["i", "b", "a", "e", "f"]
    assert ast.unparse(call.keywords[-1].value) == "self._raw.get('f')"


# ---- 7. the agent: t631's harness with v6.32's real methods, init and respond() statement ------------------------------

_CLASSES: dict = {}


def _with632(fn, scope):
    f = types.FunctionType(fn.__code__, scope, fn.__name__, fn.__defaults__, fn.__closure__)
    f.__kwdefaults__ = fn.__kwdefaults__
    return f


def _hook_source():
    """respond()'s v6.32 statement, exactly as respond has it."""
    fn = ast.parse(_src("respond")).body[0]
    hooks = [st for st in fn.body if isinstance(st, ast.If) and ast.unparse(st.test) == HOOK_TEST]
    assert len(hooks) == 1
    return "def _v632_hook(self, state):\n" + textwrap.indent(ast.unparse(hooks[0]), "    ") + "\n"


def _klass(base, names=None):
    key = (base, None if names is None else tuple(sorted((k, id(v)) for k, v in names.items())))
    if key not in _CLASSES:
        scope = dict(getattr(base, "_v63_pass").__globals__)
        scope.update(EXTRA if names is None else names)
        methods = {}
        for n in METHODS:
            ns = dict(scope)
            exec(compile(ast.Module(body=[ast.parse(_src(n)).body[0]], type_ignores=[]), f"<{n}>", "exec"), ns)
            methods[n] = ns[n]
        ns = dict(scope)
        exec(compile(_hook_source(), "<respond v6.32>", "exec"), ns)
        methods["_v632_hook"] = ns["_v632_hook"]
        for n in ("_v617_touch", "_v633_book"):                  # each names V6_32_CANCEL_FEE_GATE
            g = dict(getattr(base, n).__globals__)
            g.update(EXTRA if names is None else names)
            methods[n] = _with632(getattr(base, n), g)
        _CLASSES[key] = type("P632", (base,), methods)
    return _CLASSES[key]


def _mentions632(node):
    return t631._mentions632(node)


def _init_source():
    """The v6.32 statements of _init_build_switches -- the real ones -- as one function of self."""
    fn = ast.parse(_src("_init_build_switches")).body[0]
    body = [st for st in fn.body if _mentions632(st)]
    assert len(body) == 17, [ast.unparse(st) for st in body]
    return "def _init632(self):\n" + textwrap.indent("\n".join(ast.unparse(st) for st in body), "    ") + "\n"


def _init632(agent, *, fee=True, weight=0.005, fp=True, names=None):
    ns = dict(EXTRA if names is None else names)
    exec(compile(_init_source(), "<v6.32 init>", "exec"), ns)
    cfg = dict(vars(getattr(agent, "config", None) or types.SimpleNamespace()))
    cfg.update(research_v632_fee_making=fee, research_v632_fee_weight=weight, research_v632_state_fundamental=fp)
    agent.config = types.SimpleNamespace(**cfg)
    agent._as_bool = t630._as_bool()
    ns["_init632"](agent)


class _Acct:
    """An account as the agent reads it: fees.maker_fee_rate, a fraction."""

    def __init__(self, rate):
        self.fees = types.SimpleNamespace(maker_fee_rate=rate)


def _seed(agent, est, n=fm.MIN_FILLS, ts=NOW):
    """A pooled window of ``n`` released fills whose realized spread per notional is ``est``, counted in an active
    period that began one sim-s before ``ts`` (so a 0.6.4 request at ``ts`` continues it)."""
    tracker = agent._v6_32_tracker_ref()
    tracker.set_active(True, ts - S)
    for _k in range(n):
        tracker.window.add(ts, est * 1000.0, 1000.0)
    return tracker


def _agent(*, fee=True, weight=0.005, fp=True, rate=0.004, est=1.5e-5, s1=False, names=None, **kw):
    """t631's harness agent (book 3 100.00 / 100.03, level 0 resting at 99.80 / 100.23, the fundamental 38 bps above
    its mid unless ``page`` says otherwise) with v6.32's methods and the v6.32 state its real _init_build_switches
    statements leave; book 3's account at maker fee ``rate`` (None: no account); a pooled window at ``est`` (None:
    empty); v6.23 S1 off (``s1``) so a 0.6.4 state isolates S14."""
    agent = t631._agent(names=None, **kw)
    agent.__class__ = _klass(type(agent), names)
    _init632(agent, fee=fee, weight=weight, fp=fp, names=names)
    if rate is not None:
        agent.accounts = {3: _Acct(rate)}
    if est is not None:
        _seed(agent, est)
    if not s1:
        agent.research_v623_realized_mode = vo.parse_mode("0")
    return agent


def _ref(*, rate=0.004, s1=False, **kw):
    """v6.31 exactly: t631's harness agent, no v6.32 attribute at all (the same account and S1 mode)."""
    agent = t631._agent(**kw)
    if rate is not None:
        agent.accounts = {3: _Acct(rate)}
    if not s1:
        agent.research_v623_realized_mode = vo.parse_mode("0")
    return agent


def _request(agent, *, ts=NOW, wall=1000.0, version=64, book=None, prints=None, fp=None, raw_books=False):
    """One request as respond() makes it: its state time and wall stamp, the v6.26 hook, v6.23's observation,
    respond()'s v6.32 statement (skipped on an agent without v6.32's state), then the pass.  Book 3's wire view (its
    raw trade prints, its fundamental) rides on the book as LazyBook's ``_raw`` -- or, with ``raw_books``, on the
    books' mapping as LazyBooks' ``_raw_books``."""
    resp = t63._Resp()
    state = t623._state(ts=ts, version=version, book=book)
    raw = {}
    if prints is not None:
        raw["e"] = list(prints)
    if fp is not None:
        raw["f"] = fp
    if raw and raw_books:
        class _Books(dict):
            pass
        books = _Books(state.books)
        books._raw_books = {3: raw}
        state.books = books
    elif raw:
        state.books[3]._raw = raw
    agent._direct_current_state_timestamp_ns = int(ts)
    agent._direct_request_wall_started = float(wall)
    agent._v626_hook(state)
    agent._v6_23_observe(state)
    if hasattr(type(agent), "_v632_hook"):
        agent._v632_hook(state)
    agent._v63_pass(resp, state, {})
    return resp


_out = t631._out
_touch, _rest_of = t631._touch, t631._rest_of
BELOW_PAGE = t629._page(BELOW)
TOUCH_BUY = (3, "BUY", 43031, 100.01, 2.0)


def _counts(agent):
    return dict(agent._v6_32_counts)


def _rows(agent, event="V6_32_STATE"):
    return [kw for e, kw in agent.emitted if e == event]


def _others_rows(agent):
    return [(e, {k: v for k, v in kw.items() if k != "ms" and not k.endswith("_ms")}) for e, kw in agent.emitted
            if e != "V6_32_STATE"]


def test_the_init_statements_set_the_state_and_both_switches_ship_default_on():
    agent = _agent(est=None)
    assert agent.research_v632_fee_making is True and agent.research_v632_state_fundamental is True
    assert agent.research_v632_fee_weight == 0.005
    assert (agent._v6_32_spec, agent._v6_32_active, agent._v6_32_est, agent._v6_32_book, agent._v6_32_fp) == \
        (None, False, None, None, None)
    assert (agent._v6_32_counts, agent._v6_32_errors, agent._v6_32_req, agent._v6_32_ms) == ({}, 0, {}, [])
    a = _agent(est=None)
    a._v6_32_tracker = None
    a.config = types.SimpleNamespace()                           # no key: both on, the validator's weight
    ns = dict(EXTRA)
    exec(compile(_init_source(), "<v6.32 init>", "exec"), ns)
    ns["_init632"](a)
    assert a.research_v632_fee_making is True and a.research_v632_state_fundamental is True
    assert a.research_v632_fee_weight == fm.FEE_WEIGHT_DEFAULT and a._v6_32_tracker is None
    for bad in ("0", "false", 0, False):
        a.config = types.SimpleNamespace(research_v632_fee_making=bad, research_v632_state_fundamental=bad,
                                         research_v632_fee_weight="x")
        ns["_init632"](a)
        assert a.research_v632_fee_making is False and a.research_v632_state_fundamental is False, bad
        assert a.research_v632_fee_weight == fm.FEE_WEIGHT_DEFAULT


def test_with_the_switches_off_every_request_is_v631s_exactly():
    for page in ("default", BELOW_PAGE):
        for version in (62, 63, 64):
            for inv in (None, 3.0, -3.0):
                for rest in (True, False):
                    off = _agent(fee=False, fp=False, page=page, inv=inv, rest=rest)
                    ref = _ref(page=page, inv=inv, rest=rest)
                    case = (page != "default", version, inv, rest)
                    tape = _tape(5, ours={2: (1, 99.99)}, uid=off.uid)
                    got = _out(_request(off, version=version, fp=101.0, prints=tape))
                    want = _out(_request(ref, version=version, fp=101.0, prints=tape))
                    assert got == want, case
                    assert _others_rows(off) == _others_rows(ref) and off._v6_32_errors == 0, case
                    assert off._v6_32_book is None and off._v6_32_fp is None and not off._v6_32_active, case


def test_a_063_state_gates_nothing_but_the_tracker_already_runs():
    for inv in (None, 3.0, -3.0):
        on, ref = _agent(inv=inv), _ref(inv=inv)
        tape = _tape(5, ours={2: (1, 99.99)}, uid=on.uid)
        got, want = _out(_request(on, version=63, prints=tape)), _out(_request(ref, version=63, prints=tape))
        assert got == want and on._v6_32_active is False and on._v6_32_spec == 63, inv
        assert on._v6_32_tracker.counts["prints"] == 5 and on._v6_32_tracker.counts["own_fills"] == 1, inv
        assert on._v6_32_tracker.pending() == 1 and _counts(on).get("active_requests") is None, inv


def test_on_a_gated_book_a_flat_inventory_holds_no_deep_order_and_no_touch_quote():
    on, ref = _agent(), _ref()
    got, want = _out(_request(on)), _out(_request(ref))
    assert want == ([TOUCH_BUY], [])                             # v6.31: level 0 rests both sides, the touch quote
    assert got == ([], [(3, 21), (3, 22)])                       # both level-0 orders cancelled, no touch quote
    assert on._v6_32_active and on._v6_32_book == (3, on._tick, frozenset({BUY, SELL}))
    assert _counts(on) == {"active_requests": 1, "deep_cancels": 2, "touch_withheld": 1, "no_state_fp_requests": 1}
    assert on._v633_counts.get("cancel_v632_fee_gate") == 2 and on._v6_32_req["gated_books"] == 1


def test_a_long_book_withholds_its_buy_side_and_keeps_its_reducing_sell():
    on, ref = _agent(inv=3.0), _ref(inv=3.0)
    assert _out(_request(ref)) == ([TOUCH_BUY], [])
    assert _out(_request(on)) == ([], [(3, 21)])                 # the buy (adds) cancelled, the sell (reduces) rests
    assert on._v6_32_book[2] == frozenset({BUY}) and _counts(on)["touch_withheld"] == 1
    fresh_on, fresh_ref = _agent(inv=3.0, rest=False), _ref(inv=3.0, rest=False)
    assert _out(_request(fresh_ref))[0] == [(3, "BUY", 40031, 99.8, 2.0), (3, "SELL", 40032, 100.23, 2.0)]
    assert _out(_request(fresh_on)) == ([(3, "SELL", 40032, 100.23, 2.0)], [])          # only the reducing order
    assert _counts(fresh_on)["deep_withheld"] >= 1


def test_a_short_book_withholds_its_sell_side_and_its_reducing_touch_quote_still_buys():
    on, ref = _agent(inv=-3.0), _ref(inv=-3.0)
    assert _out(_request(ref)) == ([TOUCH_BUY], [])
    assert _out(_request(on)) == ([TOUCH_BUY], [(3, 22)])       # the touch buy reduces: placed as v6.31 places it
    assert on._v6_32_book[2] == frozenset({SELL}) and "touch_withheld" not in _counts(on)
    below_on, below_ref = _agent(inv=3.0, page=BELOW_PAGE), _ref(inv=3.0, page=BELOW_PAGE)
    want = _out(_request(below_ref))
    assert want[0] == [(3, "SELL", 43032, 100.02, 2.0)]           # the fundamental below: the touch quote sells
    got = _out(_request(below_on))
    assert got[0] == want[0] and got[1] == [(3, 21)]             # long: the sell reduces (kept), the buy adds (cancelled)


def test_a_rebate_or_a_low_fee_book_and_a_good_estimate_gate_nothing():
    for kw in (dict(rate=-0.001), dict(rate=0.002), dict(rate=0.004, est=2.5e-5), dict(rate=0.0, est=1e-6)):
        on, ref = _agent(**kw), _ref(rate=kw["rate"])
        assert _out(_request(on)) == _out(_request(ref)), kw
        assert on._v6_32_active and on._v6_32_book is None and on._v6_32_req["gated_books"] == 0, kw
    on, ref = _agent(rate=-0.001, est=-6e-6), _ref(rate=-0.001)     # a losing estimate below even a rebate's bar
    assert _out(_request(on)) == ([], [(3, 21), (3, 22)])


def test_no_account_no_rate_or_too_few_fills_gates_nothing_and_says_why():
    for rate in (None, float("nan")):                          # no account for the book / no usable rate
        on, ref = _agent(rate=rate), _ref(rate=rate)
        assert _out(_request(on)) == _out(_request(ref)), rate
        assert on._v6_32_book is None and on._v6_32_errors == 0 and on._v6_32_req["no_fee_rate_books"] == 1, rate
    empty, ref = _agent(est=None), _ref()                        # nothing released yet
    assert _out(_request(empty)) == _out(_request(ref)) and empty._v6_32_book is None
    few = _agent(est=None)
    _seed(few, 1.5e-5, n=fm.MIN_FILLS - 1)
    _request(few)
    assert few._v6_32_est is None and few._v6_32_book is None
    _seed(few, 1.5e-5, n=1)
    _request(few, ts=NOW + S)
    assert few._v6_32_est == pytest.approx(1.5e-5) and few._v6_32_book is not None


def test_a_resting_adding_touch_quote_is_cancelled_with_v632s_reason_and_a_reducing_one_rests():
    def rest_touch(agent):
        t14._rest(agent, 23, 3, 0, 100.01, cid=43031, qty=2.0)
    book = t631._book(bids=[t14.Level(100.01, [t14.Order(23, 2.0, 43031)]), t14.Level(100.00, [t14.Order(1, 2.5)])],
                      asks=[t14.Level(100.03, [t14.Order(2, 2.5)])])
    on, ref = _agent(inv=3.0), _ref(inv=3.0)
    for a in (on, ref):
        rest_touch(a)
    want, got = _out(_request(ref, book=book)), _out(_request(on, book=book))
    assert (3, 23) not in want[1] and (3, 23) in got[1]
    assert on._v617_counts.get("cancel_v632_fee_gate") == 1 and _counts(on)["touch_cancels"] == 1
    short, sref = _agent(inv=-3.0), _ref(inv=-3.0)
    for a in (short, sref):
        rest_touch(a)
    assert _touch(_out(_request(short, book=book))) == _touch(_out(_request(sref, book=book)))
    assert "touch_cancels" not in _counts(short)


def test_where_v623_s1_already_withholds_a_pair_its_reason_and_tally_stay_v623s():
    on, ref = _agent(s1=True), _ref(s1=True)                     # S1 active (spec 64): the sell side is against fp
    want = _out(_request(ref))
    assert want == ([TOUCH_BUY], [(3, 22)]) and ref._v633_counts.get("cancel_deep_against_fp") == 1
    got = _out(_request(on))
    assert got == ([], [(3, 21), (3, 22)])
    assert on._v633_counts.get("cancel_deep_against_fp") == 1 and on._v633_counts.get("cancel_v632_fee_gate") == 1


def test_every_v632_path_fails_open_to_v631_and_counts():
    def boom(*a, **k):
        raise RuntimeError("boom")
    for name in ("v6_32_fee_gated", "v6_32_maker_fee_rate", "v6_32_adds", "v6_32_gate_active", "v6_32_min_sizes"):
        on, ref = _agent(names=dict(EXTRA, **{name: boom})), _ref()
        got, want = _out(_request(on)), _out(_request(ref))
        assert got == want and on._v6_32_errors == 1 and on._v6_32_book is None, name
    deep = _agent(names=dict(EXTRA, v6_32_deep_pairs=boom))     # the deep layer keeps its orders, the touch is gated
    assert _out(_request(deep)) == ([], []) and deep._v6_32_errors == 1
    agent = _agent()
    agent._v6_32_book = (3,)                                     # a broken note: no pairs, no sides, errors
    assert agent._v6_32_deep_pairs(3) == frozenset() and agent._v6_32_touch_sides(3) == frozenset()
    assert agent._v6_32_errors == 2
    agent._v6_32_fp = 5                                         # an unreadable map: the page, errors
    assert agent._v6_32_state_fp(3, 100.0, 100.03) is None and agent._v6_32_s9_snapshot() is None
    assert agent._v6_32_errors == 4
    agent._v6_32_fp = (agent._direct_current_state_timestamp_ns,)        # this request's, but no map in it
    assert agent._v6_32_state_fp(3, 100.0, 100.03) is None and agent._v6_32_errors == 5
    agent._v6_32_errors = 4
    agent._v6_32_counts = None                                   # the tally rebuilds itself
    agent._v6_32_count("x")
    assert agent._v6_32_counts == {"x": 1}
    agent._v6_32_counts = {"x": "y"}
    agent._v6_32_count("x")
    assert agent._v6_32_errors == 5
    agent._v6_32_req = None
    agent._v6_32_active, agent._v6_32_est = True, 1.5e-5
    agent._v6_32_note(3, 0.0)
    assert agent._v6_32_book is not None and agent._v6_32_req == {"gated_books": 1}
    broken = _agent()
    broken.accounts = 5                                          # unreadable accounts: an error, no gate
    broken._v6_32_active, broken._v6_32_est = True, 1.5e-5
    broken._v6_32_note(3, 0.0)
    assert broken._v6_32_book is None and broken._v6_32_errors == 1


def test_the_request_feeds_the_tracker_from_the_raw_prints_and_never_parses_a_book():
    for raw_books in (False, True):
        agent = _agent(est=None)
        uid = agent.uid
        tape = _tape(6, ours={1: (1, 99.99), 4: (0, 100.03)}, uid=uid) + [_print(NOW + 9 * S, ma=uid, ta=uid)]
        tape.insert(2, {"y": "o", "i": 9, "p": 100.0, "q": 1.0})                      # an order event: not a print
        _request(agent, prints=tape, raw_books=raw_books)
        tr = agent._v6_32_tracker
        assert tr.counts["prints"] == 7 and tr.counts["own_fills"] == 2 and tr.counts["self_trades"] == 1, raw_books
        assert tr.books[3]["min_size"] == 0.25                    # the state's minimum order size
    agent = _agent(est=None)
    book = t631._book()
    book.events = [{"y": "t", "p": 100.0, "q": 1.0, "s": 1, "t": NOW, "Ma": agent.uid, "Ta": -3}]
    _request(agent, book=book)                                   # no wire view: the book's own events
    assert agent._v6_32_tracker.counts["own_fills"] == 1
    agent2 = _agent(est=None)
    agent2.uid = None
    _request(agent2, prints=_tape(3, ours={1: (1, 99.99)}, uid=8))
    assert agent2._v6_32_tracker.counts["prints"] == 3 and agent2._v6_32_tracker.counts["own_fills"] == 0


def test_the_gated_sides_are_this_books_and_this_requests_only():
    agent = _agent()
    agent._tick = 7
    agent._v6_32_book = (3, 7, frozenset({BUY}))
    assert agent._v6_32_deep_pairs(3) == fm.deep_pairs([BUY]) and agent._v6_32_touch_sides(3) == frozenset({BUY})
    assert agent._v6_32_deep_pairs(4) == frozenset() and agent._v6_32_touch_sides(4) == frozenset()     # another book
    agent._tick = 8                                                                                    # a later request
    assert agent._v6_32_deep_pairs(3) == frozenset() and agent._v6_32_touch_sides(3) == frozenset()
    assert agent._v6_32_errors == 0
    agent._v6_32_active, agent._v6_32_est = False, 1.5e-5                                              # inactive: none
    agent._v6_32_note(3, 0.0)
    assert agent._v6_32_book is None


def test_each_book_takes_its_own_classs_minimum_order_size_as_the_floors_voting_bar():
    agent = _agent(est=None)
    state_cfg = types.SimpleNamespace(min_order_size=0.25, simulation_id=SIM, logDir=None, classes=[
        {"books": [0, 1, 2], "config": {"min_order_size": 0.25}}, {"books": [3], "config": {"min_order_size": 2.5}}])
    state = types.SimpleNamespace(timestamp=NOW, version=64, config=state_cfg, books={3: t631._book()}, notices={})
    state.books[3]._raw = {"e": [_print(NOW)]}
    agent._direct_current_state_timestamp_ns = NOW
    agent._v632_hook(state)
    assert agent._v6_32_tracker.books[3]["min_size"] == 2.5 and agent._v6_32_errors == 0
    assert agent._v6_32_mins[1:] == (0.25, {0: 0.25, 1: 0.25, 2: 0.25, 3: 2.5})


def test_the_request_releases_ready_fills_and_a_clock_run_back_starts_the_streams_over():
    agent = _agent(est=None)
    uid = agent.uid
    tape = [_print(NOW - 30 * S + k * S // 2, ma=uid if k == 2 else -5, s=1, p=99.99 if k == 2 else 100.0)
            for k in range(80)]
    _request(agent, prints=tape, ts=NOW + 10 * S)
    assert agent._v6_32_tracker.counts["released"] == 1 and agent._v6_32_tracker.window_fills() == 1
    _request(agent, ts=NOW - 100 * S)
    assert agent._v6_32_tracker.counts["resets"] == 1 and _counts(agent)["tracker_resets"] == 1


def test_the_window_starts_empty_at_the_first_064_state_and_counts_only_fills_seen_while_the_gate_is_on():
    agent = _agent(est=None)
    uid = agent.uid

    def tape(t0):
        return [_print(t0 + k * S // 2, ma=uid if k == 2 else -5, s=1, p=99.99 if k == 2 else 100.0) for k in range(80)]
    tr = agent._v6_32_tracker_ref()
    _request(agent, version=63, ts=NOW + 40 * S, prints=tape(NOW))           # 0.6.3: released, never counted
    assert (tr.counts["released"], tr.counts["dropped_inactive"], tr.window_fills()) == (1, 1, 0)
    assert tr.window_start_ns is None and not agent._v6_32_active and "window_starts" not in _counts(agent)
    _request(agent, version=64, ts=NOW + 80 * S, prints=tape(NOW + 40 * S))  # the first 0.6.4 state
    assert tr.window_start_ns == NOW + 80 * S and _counts(agent)["window_starts"] == 1
    assert tr.counts["counted"] == 1 and tr.window_fills() == 1 and agent._v6_32_active
    _request(agent, version=63, ts=NOW + 120 * S, prints=tape(NOW + 80 * S))  # off again: seen, released, dropped
    assert tr.counts["dropped_inactive"] == 2 and tr.window_fills() == 1 and tr.counts["deactivations"] == 1
    _request(agent, version=64, ts=NOW + 160 * S, prints=tape(NOW + 120 * S))  # on again: the window restarts
    assert tr.window_start_ns == NOW + 160 * S and _counts(agent)["window_starts"] == 2
    assert tr.window_fills() == 1 and tr.counts["counted"] == 2


def test_a_window_from_an_earlier_period_is_cleared_by_the_next_activation_and_gates_nothing_until_min_fills():
    """The same full window at a gating estimate: in a period still on (the seed's), the 0.6.4 request gates book 3; in
    a period that went off before it, the 0.6.4 request starts the window empty -- no estimate, no gate (v6.31's
    output) until MIN_FILLS fills are counted in the new period."""
    on, ref = _agent(), _ref()
    assert _out(_request(on)) == ([], [(3, 21), (3, 22)]) and _out(_request(ref)) == ([TOUCH_BUY], [])
    restarted, ref2 = _agent(), _ref()
    restarted._v6_32_tracker.set_active(False, NOW - S)                         # the gate went off after the seed
    assert _out(_request(restarted)) == _out(_request(ref2)) == ([TOUCH_BUY], [])
    tr = restarted._v6_32_tracker
    assert restarted._v6_32_est is None and restarted._v6_32_book is None and tr.window_fills() == 0
    assert _counts(restarted)["window_starts"] == 1 and tr.window_start_ns == NOW and tr.period == 2


def test_the_state_fundamental_is_every_readers_first_and_the_page_its_fallback():
    on = _agent(page=None)                                       # no page at all: v6.31 has no fundamental
    ref = _ref(page=None)
    want = _out(_request(ref, fp=101.0))
    assert want == ([], [])                                      # v6.31: no fundamental, no touch quote
    got = _out(_request(on, fp=101.0, version=63))               # 101.0 above the mid: a buy touch quote
    assert got == ([TOUCH_BUY], [])
    assert on._v617_counts.get("book_state") == 1 and on._v6_32_fp[1] == {3: 101.0}
    assert on._v6_23_fundamental(3, 100.00, 100.03, None) == (101.0, "state")
    assert on._v6_32_fundamental(3, 100.00, 100.03, None, "no_feed") == (101.0, "state")
    assert on._v6_32_fundamental(3, 100.04, 100.03, 99.0, "no_book") == (99.0, "no_book")   # not sane: the page's
    below = _agent(page=None)
    assert _out(_request(below, fp=99.5, version=63))[0] == [(3, "SELL", 43032, 100.02, 2.0)]
    page_only = _agent()
    _request(page_only, version=63)                              # no f in the state: the page, exactly as v6.31
    assert page_only._v617_counts.get("book_armed") == 1 and "book_state" not in page_only._v617_counts
    assert _counts(page_only)["no_state_fp_requests"] == 1


def test_the_state_value_of_another_request_is_never_read():
    agent = _agent(page=None)
    _request(agent, fp=101.0, version=63)
    agent._direct_current_state_timestamp_ns = NOW + S          # a later request whose hook did not run
    assert agent._v6_32_state_fp(3, 100.00, 100.03) is None and agent._v6_32_s9_snapshot() is None


def test_s9s_history_is_fed_from_the_state_one_entry_per_request():
    agent = _agent(page=None)
    for k in range(3):
        _request(agent, ts=NOW + k * S, wall=1000.0 + k, fp=101.0 + 0.1 * k, version=63)
    pages = agent._v6_29_pages.pages
    assert [(seen, ts) for seen, ts, _fp in pages] == [(NOW + k * S, NOW + k * S) for k in range(3)]
    assert [fp[3] for _s, _t, fp in pages] == [101.0, 101.1, 101.2]
    ref = _agent(fp=False)                                       # P1 off: the page's snapshot, as v6.31
    _request(ref, fp=101.0, version=63)
    assert all(fp.get(3) == ABOVE for _s, _t, fp in ref._v6_29_pages.pages)


def test_p1_off_reads_the_page_exactly_as_v631():
    for fp in (101.0, None):
        off, ref = _agent(fp=False, fee=False), _ref()
        assert _out(_request(off, fp=fp, version=63)) == _out(_request(ref, fp=fp, version=63)), fp
        assert "book_state" not in off._v617_counts and off._v6_32_fp is None


# ---- 8. the V6_32_STATE row ---------------------------------------------------------------------------------------------

def test_the_state_row_comes_at_the_first_pass_every_100_ticks_and_at_the_first_error():
    agent = _agent()
    flag = {"boom": False}

    def maybe(*a, **k):
        if flag["boom"]:
            raise RuntimeError("boom")
        return fm.fee_gated(*a, **k)
    agent2 = _agent(names=dict(EXTRA, v6_32_fee_gated=maybe))
    for a in (agent, agent2):
        for i, tick in enumerate((1, 2, 99, 100, 101)):
            a._tick = tick
            _request(a, ts=NOW + i * S, wall=1000.0 + i)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    assert tuple(k for k in rows[0] if k not in ("reason", "tick")) == FIELDS
    first = rows[0]
    assert (first["version"], first["state_fp_version"], first["fee_making_on"], first["state_fundamental_on"]) == \
        ("fee_making_v6_32", "state_fundamental_v6_32", 1, 1)
    assert (first["active"], first["spec"], first["fee_weight"], first["gated_books"]) == (1, 64, 0.005, 1)
    assert first["estimate_bps"] == pytest.approx(0.15) and first["window_fills"] == fm.MIN_FILLS
    assert (first["window_start_ns"], first["activations"], first["deactivations"]) == (NOW - S, 1, 0)
    assert first["deep_cancels"] == 2 and first["touch_withheld"] == 1 and first["errors"] == 0
    assert first["tracker_ms_p50"] is not None and first["tracker_ms_p99"] >= first["tracker_ms_p50"]
    for k, (tick, boom) in enumerate(((102, True), (103, True), (104, False), (105, True))):
        flag["boom"] = boom
        agent2._tick = tick
        _request(agent2, ts=NOW + (5 + k) * S, wall=1005.0 + k)
    assert [(r["reason"], r["tick"]) for r in _rows(agent2)] == [("start", 1), ("periodic", 100), ("error", 102),
                                                                  ("error", 105)]
    assert _rows(agent2)[-1]["errors"] == 3


def test_the_row_never_fails_the_request_and_waits_for_the_reply():
    bad, ref = _agent(), _agent()
    emitted = bad.emitted
    bad._emit = lambda event, force=False, **kw: (1 / 0) if event == "V6_32_STATE" else emitted.append((event, kw))
    bad._tick = ref._tick = 1
    assert _out(_request(bad)) == _out(_request(ref)) and bad._v6_32_errors == 1 and _rows(bad) == []
    deferred = _agent()
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _request(deferred)
    names = [n for n, _fn in work]
    assert names[-2:] == ["v6_31_state_row", "v6_32_state_row"] and _rows(deferred) == []
    dict(work)["v6_32_state_row"]()
    assert _rows(deferred)[0]["reason"] == "start"


def test_off_the_row_says_so_and_an_agent_without_the_build_has_none():
    off = _agent(fee=False, fp=False)
    off._tick = 1
    _request(off)
    row = _rows(off)[0]
    assert (row["fee_making_on"], row["state_fundamental_on"], row["active"], row["errors"]) == (0, 0, 0, 0)
    assert row["estimate_bps"] is not None                       # the seeded window, read but never used
    older = _ref()
    older._tick = 1
    _request(older)
    assert not _rows(older)


# ---- 9. wiring ----------------------------------------------------------------------------------------------------------

def test_the_switches_ship_on_after_v631s_keys_and_agree_with_their_agent_defaults():
    params = t617._params()
    for key, value in (("research_v632_fee_making", "1"), ("research_v632_fee_weight", "0.005"),
                       ("research_v632_state_fundamental", "1")):
        assert f"{key}={value} " in t617._shipped() + " " and params.count(f"{key}=") == 1, key
    assert ('research_v631_single_write_save=1 \\\nresearch_v632_fee_making=1 \\\nresearch_v632_fee_weight=0.005 \\\n'
            'research_v632_state_fundamental=1"\n' in LAUNCHER)
    for key in ("research_v617_touch_quote=1", "research_v633_deep_layer=1"):           # what S14 gates
        assert f" {key} " in f" {t617._shipped()} ", key
    init = _src("_init_build_switches")
    lines = (FEE_SWITCH, "self.research_v632_fee_weight = v6_32_parse_fee_weight(", WEIGHT_READ,
             "self.research_v632_state_fundamental = self._as_bool(", FP_SWITCH, "self._v6_32_tracker = None",
             "self._v6_32_spec: int | None = None", "self._v6_32_active = False",
             "self._v6_32_est: float | None = None", "self._v6_32_book = None", "self._v6_32_fp = None",
             "self._v6_32_mins = None", "self._v6_32_req: dict[str, int] = {}", "self._v6_32_ms: list[float] = []",
             "self._v6_32_counts: dict[str, int] = {}", "self._v6_32_errors = 0", "self._v6_32_errors_seen = 0",
             "self._v6_32_reported = False", "self._v6_32_erring = False")
    for line in lines:
        assert init.count(line) == 1, line
    idx = [init.index(x) for x in lines]
    assert init.index("self._v6_31_erring = False") < idx[0] and idx == sorted(idx) \
        and idx[-1] < init.index("self._v69_counts: dict[str, int] = {}")


def test_every_v632_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    for module, mod in (("research_v6_32_fee_making", fm), ("research_v6_32_state_fp", sf)):
        imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == module)
        for alias in imp.names:
            assert V6_32_NAMES[alias.asname or alias.name] is getattr(mod, alias.name), alias.asname
        assert SIMPLE.count(f"from {module} import (") == 1
    imported = [a.asname or a.name for n in tree.body if isinstance(n, ast.ImportFrom)
                and n.module in ("research_v6_32_fee_making", "research_v6_32_state_fp") for a in n.names]
    assert sorted(imported) == sorted(V6_32_NAMES)
    assert t623.V6_32_IDENTIFIERS == (set(V6_32_NAMES) | {"research_v632_fee_making", "research_v632_fee_weight",
                                                          "research_v632_state_fundamental"})
    for mod in (t624, t625, t626, t627, t628, t629, t630, t631):
        assert mod.V6_32_IDENTIFIERS is t623.V6_32_IDENTIFIERS, mod.__name__
    bound = [a.asname or a.name for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
    for name in list(V6_32_NAMES) + list(REACHED):              # bound at module level, as the harness binds them
        assert bound.count(name) == 1, name
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    assert sorted(n for n in names if n.startswith("_v6_32_")) == sorted(METHODS)
    assert '"V6_32_STATE"' in _src("_v6_32_report") and '"v6_32_state_row"' in _src("_v6_32_report")


def test_each_line_is_wired_once_in_its_place():
    for line in (REQUEST_LINE, NOTE_LINE, REPORT_LINE, FEE_SWITCH, WEIGHT_READ, FP_SWITCH,
                 "v6_32_fee = self._v6_32_deep_pairs(book_id)", "doomed.append((row, V6_32_CANCEL_FEE_GATE))",
                 'self._v6_32_count("deep_withheld")', "v6_32_gated = self._v6_32_touch_sides(book_id)",
                 "why = V6_32_CANCEL_FEE_GATE", 'self._v6_32_count("touch_withheld")',
                 "v6_32_got = self._v6_32_state_fp(book_id, raw_bid, raw_ask)", "v6_32_snap = self._v6_32_s9_snapshot()"):
        assert SIMPLE.count(line) == 1, line
    assert SIMPLE.count("fp, reason = self._v6_32_fundamental(book_id, raw_bid, raw_ask, fp, reason)") == 2
    respond = _src("respond")
    assert respond.index("self._v6_23_observe(state)") < respond.index(REQUEST_LINE) < \
        respond.index("self._a1961_note_base_decimals(state)")
    body = _src("_v63_pass")
    assert body.index("self._v6_31_note(book_id, book, own_ids, touch_on)") < body.index(NOTE_LINE) < \
        body.index("deep_open = False") < body.index("placed_total += self._v617_book(")
    assert body.index("self._v6_31_report()") < body.index(REPORT_LINE) < body.index("return placed_total")
    deep = _src("_v633_book")
    assert deep.index("v6_32_fee = self._v6_32_deep_pairs(book_id)") < deep.index("doomed = [(row, V633_CANCEL_OWNS")
    assert deep.index("doomed.append((row, V623_CANCEL_AGAINST))") < \
        deep.index("doomed.append((row, V6_32_CANCEL_FEE_GATE))") < deep.index("doomed.append((row, V619_CANCEL_SURPLUS))")
    assert deep.index("continue                    # v6.23 S1: no order on the side") < \
        deep.index('self._v6_32_count("deep_withheld")') < deep.index("if ladder and ((side, level) in resting")
    touch = _src("_v617_touch")
    assert touch.index("v6_29_held = self._v6_29_held(book_id, fp, cfg)") < \
        touch.index("v6_32_gated = self._v6_32_touch_sides(book_id)") < touch.index("doomed = []")
    assert touch.index("why = V6_29_CANCEL_FP_MOVE") < touch.index("why = V6_32_CANCEL_FEE_GATE") < \
        touch.index("cancelled = self._v617_cancel(response, book_id, doomed, req, budget)")
    assert touch.index('self._v6_29_count("withheld")') < touch.index('self._v6_32_count("touch_withheld")') < \
        touch.index("if V617_TOUCH_SLOT in")
    for name in ("_v616_book", "_v617_fundamental"):
        src = _src(name)
        assert src.index("fp, reason = v616_book_fundamental(") < \
            src.index("fp, reason = self._v6_32_fundamental(") < src.index('_count("book_" + str(reason))'), name
    v623 = ast.parse(_src("_v6_23_fundamental")).body[0]
    assert isinstance(v623.body[1], ast.If) and "research_v632_state_fundamental" in ast.unparse(v623.body[1].test)
    held = _src("_v6_29_held")
    assert held.index("v6_32_snap = self._v6_32_s9_snapshot()") < held.index("if v6_32_snap is not None:") < \
        held.index("lookback = v6_29_lookback(")
    fn = ast.parse(held).body[0]
    branch = next(n for n in ast.walk(fn) if isinstance(n, ast.If) and ast.unparse(n.test) == "v6_32_snap is not None")
    assert [ast.unparse(s) for s in branch.orelse] == \
        ["pages.observe(feed.snapshot() if feed is not None else None, now_ts, v626_state_sim_id(cfg))"]


def test_the_module_never_writes_an_order_flag_and_v632s_methods_place_nothing():
    texts = [ast.unparse(st) for st in ast.walk(ast.parse(SIMPLE))
             if isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_32_")]
    text = "\n".join(texts) + "\n" + MODULE + "\n" + STATE_MODULE
    for word in ("postOnly", "post_only", "stp", "STP", "timeInForce", "TimeInForce", "limit_order", "cancel_orders",
                 "instructions", "leverage", "OrderDirection"):
        assert not re.search(rf"\b{word}\b", text), word


# ---- 10. v6.31's code, statement by statement (the committed v6.31: fb5bf3c) --------------------------------------------

V631_COMMIT = "fb5bf3c"       # the user's v6.31 commit (10-16 00:08 JST canary on UID 105): k88's FROZEN_2


def _committed(rel):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V631_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V631_COMMIT} is not available here")
    return out.stdout


CHANGED = {"_init_build_switches", "respond", "_v63_pass", "_v633_book", "_v617_touch", "_v616_book",
           "_v617_fundamental", "_v6_23_fundamental", "_v6_29_held"}


def test_v632s_strip_takes_out_its_methods_and_its_statements_in_nine_and_v631_is_under_both_strips():
    stripped = t631._Drop632().visit(ast.parse(SIMPLE))
    assert not t631._mentions632(stripped)
    a, b = t629._methods(stripped), t629._methods(ast.parse(SIMPLE))
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    assert {k for k in a if a[k] != b[k]} == CHANGED
    assert not t630._mentions631(t630._Drop631().visit(ast.parse(SIMPLE)))


def test_every_v631_statement_is_in_v632_unchanged_and_v632_only_adds():
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    assert not t631._mentions632(ast.parse(old)), "v6.31 names a v6.32 identifier: the strip would hide a change"
    stripped = t631._Drop632().visit(ast.parse(SIMPLE))
    assert ast.dump(stripped) == ast.dump(ast.parse(old))
    assert ast.dump(stripped) != ast.dump(ast.parse(SIMPLE))
    for good, bad in (('self._v617_count("book_" + str(reason))', 'self._v617_count("book" + str(reason))'),
                      ("elif against and (side, level) in against:", "elif against:"),
                      ("pages.observe(feed.snapshot() if feed is not None else None, now_ts, v626_state_sim_id(cfg))",
                       "pages.observe(None, now_ts, v626_state_sim_id(cfg))")):
        assert SIMPLE.count(good) == 1, good
        mutated = SIMPLE.replace(good, bad)
        assert ast.dump(t631._Drop632().visit(ast.parse(mutated))) != ast.dump(ast.parse(old)), good


def test_only_nine_methods_changed_and_the_exit_layer_frozen_files_and_protocol_are_v631s():
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    a, b = t629._methods(ast.parse(old)), t629._methods(ast.parse(SIMPLE))
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    assert {k for k in a if a[k] != b[k]} == CHANGED
    for k in ("_v617_book", "_v617_cancel", "_v6_31_note", "_v6_31_price", "_v6_31_staged_save", "_v6_30_request",
              "_v6_23_against", "_v6_23_observe", "_v6_25_lean", "_a191_service_reprice_cancels", "_a191_decide",
              "_v6214_service_touch_life", "_research_final_validate_instructions", "_research_save_session",
              "_research_session_path", "onOrderAccepted", "update"):
        assert a[k] == b[k], k
    for other in ("Strategy1_Research.py", "research_v617_touch_quote.py", "research_v623_validator_063.py",
                  "research_v616_fair_value.py", "research_v6_29_touch_move.py", "research_v630_identity_profile.py",
                  "research_v631_touch_improve.py", "research_v633_deep_layer.py", "research_v641_pace.py",
                  "research_direct_exit_refresh.py", "research_v6214_touch_life.py"):
        assert _committed(f"agents/strategy/{other}") == (STRATEGY / other).read_text(), other
    old_models = ast.parse(_committed("taos/im/protocol/models.py"))
    assert ast.dump(t623._drop632_models(ast.parse(MODELS))) == ast.dump(old_models)     # P1's additions only


def test_the_launcher_only_adds_to_v631s():
    """The committed v6.31 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS' last
    key), the rest added -- the v6.32 block, three PARAMS keys and its gate entry."""
    old, new = _committed("run_strategy1_research_simple_multi.sh").splitlines(), LAUNCHER.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(x for x in old if x.startswith("A19X_BUILD=0;"))
    arm = next(x for x in old if x.startswith("  strategy1_direct_v6_3_0)"))
    assert removed == [defaults, arm, 'research_v631_single_write_save=1"']
    start = LAUNCHER.index("# v6.32.")
    block = LAUNCHER[start:LAUNCHER.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    assert sorted(added) == sorted([defaults + "; V6_32_BUILD=0", arm + "; V6_32_BUILD=1",
                                    "research_v631_single_write_save=1 \\", "research_v632_fee_making=1 \\",
                                    "research_v632_fee_weight=0.005 \\", 'research_v632_state_fundamental=1"',
                                    "      tests/test_research_v6_32.py \\", *block])


# ---- 11. the launcher ----------------------------------------------------------------------------------------------------

def test_the_v632_block_is_gated_preflighted_and_listed():
    assert "; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_32_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_32_BUILD=") == 2
    assert "      tests/test_research_v6_31.py \\\n      tests/test_research_v6_32.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.31 touch improve + single-write save PASS') < \
        LAUNCHER.index("# v6.32.") < LAUNCHER.index('echo "[preflight] v6.32 fee-aware making + state fundamental PASS') \
        < LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    head = LAUNCHER[LAUNCHER.index("# v6.32."):LAUNCHER.index('if [[ "${V6_32_BUILD:-0}" == "1" ]]; then')]
    for needle in ("STRUCTURAL", "19f9fbd", "pooled", "fg=2", "0.005", "spec 64", "research_v617_touch_quote=1",
                   "research_v633_deep_layer=1", "key f"):
        assert needle in head, needle


def _block():
    start = LAUNCHER.index('if [[ "${V6_32_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.32 fee-aware making + state fundamental PASS', start)]


_PATTERN = r"grep -qF '([^']+)' \"\$(AGENT_PATH|SCRIPT_DIR)/([^\"]+)\" \|\| \{\n\s+echo \"ERROR: ([^\"]+)\" >&2"
FILES = {"Strategy1_Research_Simple.py": "agents/strategy/Strategy1_Research_Simple.py",
         "research_v6_32_fee_making.py": "agents/strategy/research_v6_32_fee_making.py",
         "research_v6_32_state_fp.py": "agents/strategy/research_v6_32_state_fp.py",
         "taos/im/protocol/models.py": "taos/im/protocol/models.py"}


def test_every_v632_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 28, pats
    for needle, var, name, _msg in pats:
        assert name in FILES and (ROOT / FILES[name]).read_text().count(needle) == 1, (needle, name)
        assert (var == "SCRIPT_DIR") == (name == "taos/im/protocol/models.py"), (needle, var)
    assert {p[0] for p in pats} >= {"from research_v6_32_fee_making import (", "from research_v6_32_state_fp import (",
                                    FEE_SWITCH, WEIGHT_READ, FP_SWITCH, REQUEST_LINE, NOTE_LINE, REPORT_LINE,
                                    "return e <= w * r", "elif against and (side, level) in against:",
                                    "if tracker.set_active(active, now):",
                                    "if self.active and period is not None and period == self.period:"}


def _run_block(params, agent_path=STRATEGY, script_dir=ROOT, build="1"):
    start = LAUNCHER.index("# v6.32.")
    stop = LAUNCHER.index('echo "[preflight] v6.32 fee-aware making + state fundamental PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "SCRIPT_DIR": str(script_dir), "V6_32_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v632_block_passes_the_shipped_params_and_refuses_a_build_it_cannot_ship():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "[preflight] v6.32 S14 fee gate: on" and len(lines) == 2, out.stdout
    assert lines[1].startswith("[preflight] v6.32 fee-aware making + state fundamental PASS (S14 on, weight=0.005: ")
    # S14's shipped state is the user's call: =0 ships the gate off (v6.31's making, P1 on) -- one character
    off = _run_block(f" {shipped} ".replace(" research_v632_fee_making=1 ", " research_v632_fee_making=0 "))
    assert off.returncode == 0 and off.stderr == "", off.stderr
    assert off.stdout.splitlines()[0] == "[preflight] v6.32 S14 fee gate: off"
    assert "PASS (S14 off, weight=0.005: " in off.stdout
    key = "research_v632_fee_making"
    for bad in (f"{key}=2", "", f"{key}=true", f"{key}=11", f"{key}=01", f"x{key}=1", f"{key}=-1"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {bad} "))
        assert out.returncode == 1 and f"without {key}=0 or 1" in out.stderr and "PASS" not in out.stdout, bad
    for twice in (f"{key}=1 {key}=1", f"{key}=1 {key}=0", f"{key}=0 {key}=1"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {twice} "))
        assert out.returncode == 1 and f"{key} given 2 times" in out.stderr and "PASS" not in out.stdout, twice
    key = "research_v632_state_fundamental"                                          # P1 must ship on
    for bad in (f"{key}=0", "", f"{key}=true", f"{key}=11", f"x{key}=1"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {bad} "))
        assert out.returncode == 1 and f"without {key}=1" in out.stderr and "PASS" not in out.stdout, bad
    for bad, msg in (("research_v632_fee_weight=0", "a weight of 0 gates nothing"),
                     ("research_v632_fee_weight=0.000", "a weight of 0 gates nothing"),
                     ("research_v632_fee_weight=-0.005", "without a research_v632_fee_weight > 0"),
                     ("research_v632_fee_weight=x", "without a research_v632_fee_weight > 0"),
                     ("", "without a research_v632_fee_weight > 0")):
        out = _run_block(f" {shipped} ".replace(" research_v632_fee_weight=0.005 ", f" {bad} "))
        assert out.returncode == 1 and msg in out.stderr and "PASS" not in out.stdout, bad
    out = _run_block(f" {shipped} ".replace(" research_v632_fee_weight=0.005 ", " research_v632_fee_weight=0.01 "))
    assert out.returncode == 0 and "weight=0.01:" in out.stdout                         # a later boundary's weight
    off0 = f" {shipped} ".replace(" research_v632_fee_making=1 ", " research_v632_fee_making=0 ")
    out = _run_block(off0.replace(" research_v632_fee_weight=0.005 ", " research_v632_fee_weight=0 "))
    assert out.returncode == 1 and "a weight of 0 gates nothing" in out.stderr          # checked with the gate off too
    for key in ("research_v617_touch_quote", "research_v633_deep_layer"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {key}=0 "))
        assert out.returncode == 1 and f"v6.32 build without {key}=1 in PARAMS" in out.stderr, key
    out = _run_block("", build="0")                                                     # another build: not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v632_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = re.findall(_PATTERN, _block())
    assert len(cases) == 28
    for i, (needle, var, name, msg) in enumerate(cases):
        tmp = tmp_path / str(i)
        (tmp / "agents" / "strategy").mkdir(parents=True)
        (tmp / "taos" / "im" / "protocol").mkdir(parents=True)
        for rel in FILES.values():
            shutil.copy(ROOT / rel, tmp / rel)
        path = tmp / FILES[name]
        text = path.read_text()
        assert text.count(needle) == 1, needle
        path.write_text(text.replace(needle, "pass  # removed", 1))
        out = _run_block(t617._shipped(), agent_path=tmp / "agents" / "strategy", script_dir=tmp)
        assert out.returncode == 1 and msg in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
