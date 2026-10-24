"""v6.39: research_v639_runlen -- U2 stage 2 / S3 (scratchpad k129): the run-length growth and the off-path CPU of k115
(H6 / H7 / H14) behind one switch, every decision as v6.38's.

v6.38 M-FROZEN_2 (k126) + the clean rolling realized-PnL store with the cached v6.23 census (STORE), the deep layer's
sorted sweep record (SWEEP), the idle collector's deferred full pass (IDLE), the observatory's default gzip level (OBS)
and the recorder thread's released previous state (unconditional: memory only).  Each is a behaviour test here: the new
code against the v6.38 functions it replaces, on random inputs (== by content, never approx), plus the wiring, the
undo (tests/_v639_undo.py: v6.39 out == M-FROZEN_2's AST, per interpreter) and the launcher block.  No test here touches
the host's caches, the network or a live tree, and nothing is imported from the scratchpad."""
import ast
import gc
import gzip
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import tempfile
import time
import weakref
from collections import deque
from types import SimpleNamespace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_direct_idle_gc as idle  # noqa: E402
import research_rolling_economics as roll  # noqa: E402
import research_v5_observatory as obs  # noqa: E402
import research_v623_premium_floor as pf  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v68_deep_owns as owns  # noqa: E402
import research_v6_39_runlen as rl  # noqa: E402
import _v638_undo  # noqa: E402
import _v639_undo  # noqa: E402
import _v640_undo  # noqa: E402   (k139: v6.40's paths / text undo)
from _harness import extractor, read_source, simple_text  # noqa: E402
from test_research_a1_9_9_2_idle_gc import FakeGC, FakeLoop  # noqa: E402

SIMPLE = simple_text()                                                         # S4: the joined text (the file + its parts)
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
MODULE = (STRATEGY / "research_v6_39_runlen.py").read_text()
GC_MODULE = (STRATEGY / "research_direct_idle_gc.py").read_text()
DEEP_MODULE = (STRATEGY / "research_v633_deep_layer.py").read_text()
OBS_MODULE = (STRATEGY / "research_v5_observatory.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
_res = extractor(RESEARCH, cls_name="Strategy1_Research")
PY = tuple(sys.version_info[:2])
SWITCH = "research_v639_runlen"
SWITCH_LINE = 'self.research_v639_runlen = self._as_bool(getattr(self.config, "research_v639_runlen", True))'
APPLY_LINE = "self._v6_39_switches = v6_39_apply(self.research_v639_runlen)"
V638_APPLY_LINE = "self._v6_38_switches = v6_38_apply(self.research_v638_fast_path)"
STEP = 1_000_000_000

# v6.38 M-FROZEN_2 (k126; SP/k126/verify/m_frozen_2.sha256): the AST digests of the four modules v6.39 edits, per
# interpreter (ast.dump's text differs between minors), computed once from the frozen files; and its launcher's sha256.
M2_AST = {
    'Simple': {(3, 10): 'f403f31b9c6279b4e6ad6cdfebc00dfa125f95f2d76517640bab6e9c1a3ba789', (3, 12): '044da61a95ca74463f595fad113735d8219b0ad9afb548b9bd505ad2885f585a'},
    'idle_gc': {(3, 10): 'd933287fe0ae652754a9b712c6291f8f5cca84da90dfc60dd49f31eb665ca347', (3, 12): 'd9a26effe728613000fe3f7908d07d4e46f83959621a283e890fba41e5585406'},
    'deep_layer': {(3, 10): '5bcd51ff230a1e9bbde780897e1ab6e7bbf31c98291d750ce50431c5d8642d73', (3, 12): '7d1fb077d6049afc2c86ba3c86f1275d1c67725669f5a3a2f2c8e5431ebd1276'},
    'observatory': {(3, 10): '533e08f71d4d58f53a081a304f9ee18b920b3795696b40e0f234fc73d63d2c69', (3, 12): 'cb42d70373bbb2fb7b5e2d329c9228cf9189ea9da70895bc5a6b57935218b095'},
}
PRE_SPLIT_SHA256 = "db56e75962e615af02d5169b7b702b9554e4a54cd5609dc04e0b13a8c7d6ac41"          # the S3 Simple at the split (S4.1)
N_MOVED = 43
N_LAUNCHER_SIMPLE_REFS = 403
M1_LAUNCHER_SHA256 = "a289b652e6ba99f8a565f8733e6c2c433d9c630383b22fe2e61ea34119f65b32"   # k129: v6.39 ships CC OFF (the user, 10-23): its base launcher is M-FROZEN_1's


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _ast_sha(tree):
    return hashlib.sha256(ast.dump(tree).encode()).hexdigest()


# ---- 1. the module --------------------------------------------------------------------------------------------------

def test_the_module_names_its_version_its_switch_and_apply_writes_the_deep_layers_cache_switch_both_ways():
    assert rl.V6_39_RUNLEN_VERSION == "runlen_v6_39" and rl.SWITCH == SWITCH
    assert rl.SWITCHES == (("research_v633_deep_layer", "_V639_SORTED_CACHE"),)
    assert "_V639_SORTED_CACHE = False\n" in DEEP_MODULE                       # the module alone: v6.38
    saved = dl._V639_SORTED_CACHE
    try:
        assert rl.apply(True) == {"_V639_SORTED_CACHE": True} and dl._V639_SORTED_CACHE is True
        assert rl.apply(0) == {"_V639_SORTED_CACHE": False} and dl._V639_SORTED_CACHE is False
    finally:
        dl._V639_SORTED_CACHE = saved
    assert rl.GZIP_LEVEL == -1                                                  # zlib's Z_DEFAULT_COMPRESSION


# ---- 2. STORE: the clean store and the cached census == v6.38's by content ------------------------------------------

def _rows_equal(a, b):
    return len(a) == len(b) and all(t1 == t2 and type(t1) is type(t2) and p1 == p2 and type(p1) is type(p2)
                                    for (t1, p1), (t2, p2) in zip(a, b))


def _store_equal(a, b):
    return list(a) == list(b) and all(_rows_equal(a[k], b[k]) for k in a)


def _win_equal(a, b):
    if list(a) != list(b):
        return False
    for k in a:
        x, y = a[k], b[k]
        if (x.observations, x.negatives) != (y.observations, y.negatives):
            return False
        if not (x.realized_sum == y.realized_sum or (math.isnan(x.realized_sum) and math.isnan(y.realized_sum))):
            return False
    return True


def _rand_pnl(r):
    c = r.random()
    if c < 0.05:
        return 0.0
    if c < 0.08:
        return float("nan")
    if c < 0.10:
        return r.choice([float("inf"), -float("inf")])
    if c < 0.15:
        return r.choice([1e-16, -1e-16, 5e-16])
    if c < 0.4:
        return round(r.uniform(-0.01, 0.01), r.choice([3, 4, 5, 6]))
    return r.uniform(-5, 5) * r.choice([1, 1e-3, 1e-6])


def _rand_raw_store(r, now, lookback):
    d = {}
    for b in range(r.randint(0, 8)):
        rows = []
        for _ in range(r.randint(0, 12)):
            ts = now - r.randint(-2 * STEP, lookback + 2 * STEP)
            if r.random() < 0.3:
                ts = (ts // STEP) * STEP                                        # on the publish grid
            if r.random() < 0.05:
                ts = "bad"
            rows.append(r.choice([(ts, _rand_pnl(r)), [ts, _rand_pnl(r)], (ts,)]) if r.random() < 0.02
                        else (ts, _rand_pnl(r)))
        d[(r.choice([b, str(b)]) if r.random() < 0.1 else b)] = rows
    return d


def _walk(seed, counters):
    r = random.Random(seed)
    lookback = r.choice([10_800 * STEP, 600 * STEP, 0, 50 * STEP])
    now = r.randint(10 * STEP, 20_000 * STEP)
    raw = _rand_raw_store(r, now, lookback)
    a = roll.sanitize_realized_pnl_events({str(k): [list(x) for x in v] for k, v in raw.items()}
                                          if r.random() < 0.5 else raw)
    b = {k: list(v) for k, v in a.items()}
    cache = {}
    for _step in range(r.randint(1, 40)):
        op = r.random()
        if op < 0.5:
            book = r.randint(0, 9)
            ts = now - r.randint(-STEP, lookback + STEP) if r.random() < 0.9 else r.randint(0, now)
            pnl = _rand_pnl(r)
            a = roll.note_realized_pnl_event(a, book_id=book, timestamp=ts, realized_pnl=pnl, now=now,
                                             lookback_ns=lookback)
            before = {k: v for k, v in b.items()}
            b = rl.note_event(b, book_id=book, timestamp=ts, realized_pnl=pnl, now=now, lookback_ns=lookback)
            for k, v in b.items():                                              # object sharing: untouched books keep theirs
                if k in before and isinstance(before[k], rl.CleanRows):
                    counters["same" if v is before[k] else "new"] += 1
                    assert isinstance(v, rl.CleanRows)
        elif op < 0.6:
            a = roll.prune_realized_pnl_events(a, now=now, lookback_ns=lookback)
            b = rl.prune_events(b, now=now, lookback_ns=lookback)
        elif op < 0.7:
            now = (now + r.choice([STEP, STEP, 7 * STEP, -3 * STEP, 100 * STEP]) if r.random() < 0.9
                   else r.randint(1, 30_000 * STEP))
        elif op < 0.8:                                                          # a session restore: plain lists again
            payload = {str(k): [[int(t), float(p)] for t, p in v] for k, v in b.items()}
            b = roll.sanitize_realized_pnl_events(json.loads(json.dumps(payload)))
        elif b:                                                                 # a plain list (what rebase leaves)
            k = r.choice(list(b))
            b[k] = list(b[k])
        counters["cases"] += 1
        assert _store_equal(a, b), (seed, a, b)
        assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)   # the session bytes
        for _q in range(r.randint(0, 3)):
            cnow = now + r.choice([0, STEP, 2 * STEP, -STEP, 31 * STEP])
            step_ns = r.choice([STEP, 30 * STEP, 0, "x"])
            dec = r.choice([4, 2, 8, "x"])
            tau = r.choice([pf.KAPPA_TAU, 0.0, -1e-4])
            wa = pf.window_census(a, now=cnow, lookback_ns=lookback, step_ns=step_ns, decimals=dec, tau=tau)
            wb = rl.window_census_cached(cache, b, now=cnow, lookback_ns=lookback, step_ns=step_ns, decimals=dec, tau=tau)
            wb2 = rl.window_census_cached(cache, b, now=cnow, lookback_ns=lookback, step_ns=step_ns, decimals=dec, tau=tau)
            counters["cases"] += 1
            assert _win_equal(wa, wb) and _win_equal(wa, wb2), (seed, wa, wb)
            assert set(cache) <= {int(k) for k in b if str(k).lstrip("-").isdigit()}
        for k in list(a)[:2]:                                                   # v6.38's economics on the new lists
            assert roll.rolling_book_economics(a, k, now=now, lookback_ns=lookback) == \
                roll.rolling_book_economics(b, k, now=now, lookback_ns=lookback)


def test_the_clean_store_and_the_cached_census_are_v638s_by_content_on_random_walks():
    counters = {"cases": 0, "same": 0, "new": 0}
    for seed in range(300):
        _walk(seed, counters)
    assert counters["cases"] > 10_000 and counters["same"] > counters["new"] > 0, counters


def test_the_store_marks_its_lists_clean_cuts_by_bisection_and_keeps_the_object_when_nothing_is_cut():
    now, lb = 1_000 * STEP, 100 * STEP
    plain = {1: [(now - 50 * STEP, 0.5), (now - 10 * STEP, -0.25), (now - 200 * STEP, 1.0), ("x", 1.0), (now, 0.0)]}
    b = rl.prune_events(plain, now=now, lookback_ns=lb)
    assert isinstance(b[1], rl.CleanRows) and b[1] == [(now - 50 * STEP, 0.5), (now - 10 * STEP, -0.25)]
    assert rl.prune_events(b, now=now, lookback_ns=lb)[1] is b[1]                # nothing cut: the same object
    c = rl.prune_events(b, now=now + 60 * STEP, lookback_ns=lb)                 # the oldest row cut: a new object
    assert c[1] is not b[1] and c[1] == [(now - 10 * STEP, -0.25)] and isinstance(c[1], rl.CleanRows)
    assert rl.prune_events(b, now=now + 200 * STEP, lookback_ns=lb) == {}       # every row cut: the book is dropped
    d = rl.note_event(b, book_id=2, timestamp=now, realized_pnl=0.1, now=now, lookback_ns=lb)
    assert d[1] is b[1] and d[2] == [(now, 0.1)]                                # another book touched: 1 shared
    e = rl.note_event(d, book_id=1, timestamp=now - 50 * STEP, realized_pnl=0.7, now=now, lookback_ns=lb)
    assert e[1] == [(now - 50 * STEP, 0.5), (now - 50 * STEP, 0.7), (now - 10 * STEP, -0.25)]   # after its equal-ts rows
    assert e[1] is not d[1] and e[2] is d[2]
    assert rl.note_event(e, book_id=1, timestamp=now, realized_pnl=0.0, now=now, lookback_ns=lb)[1] is e[1]  # zero: prune only
    assert json.loads(json.dumps(e)) == {"1": [[now - 50 * STEP, 0.5], [now - 50 * STEP, 0.7], [now - 10 * STEP, -0.25]],
                                         "2": [[now, 0.1]]}
    # the edges exactly: a row AT the cutoff is kept, a row one ns before it is cut (v6.38: cutoff <= ts <= current)
    edge = rl.prune_events({3: [(now - lb - 1, 0.1), (now - lb, 0.2), (now, 0.3), (now + 1, 0.4)]}, now=now, lookback_ns=lb)
    assert edge[3] == [(now - lb, 0.2), (now, 0.3)]
    assert rl.prune_events(edge, now=now, lookback_ns=lb)[3] is edge[3]
    assert rl.prune_events(edge, now=now, lookback_ns=lb) == roll.prune_realized_pnl_events(edge, now=now, lookback_ns=lb)
    # now <= 0: v6.38 applies no upper edge (every row at or after the cutoff stays)
    zero = rl.prune_events({4: [(5, 0.1), (10, 0.2)]}, now=0, lookback_ns=lb)
    assert zero == roll.prune_realized_pnl_events({4: [(5, 0.1), (10, 0.2)]}, now=0, lookback_ns=lb) == {4: [(5, 0.1), (10, 0.2)]}
    assert rl.prune_events(zero, now=0, lookback_ns=lb)[4] is zero[4]
    assert rl.prune_events(zero, now=None, lookback_ns=lb)[4] is zero[4]


def test_the_census_cache_is_keyed_on_the_books_list_object_and_its_window_edges():
    now, lb = 1_000 * STEP, 100 * STEP
    store = rl.prune_events({1: [(now - 50 * STEP, 0.5), (now - 10 * STEP, -0.25)], 2: [(now - 5 * STEP, 0.3)]},
                            now=now, lookback_ns=lb)
    cache = {}
    w1 = rl.window_census_cached(cache, store, now=now, lookback_ns=lb)
    assert set(cache) == {1, 2} and cache[1][0] is store[1]
    w2 = rl.window_census_cached(cache, store, now=now + STEP, lookback_ns=lb)   # edges unchanged: the same windows
    assert w2[1] is w1[1] and w2[2] is w1[2]
    w3 = rl.window_census_cached(cache, store, now=now + 60 * STEP, lookback_ns=lb)   # book 1's oldest row leaves
    assert w3[1] is not w1[1] and w3[1].observations == 1 and w3[2] is w1[2]
    # the same edges but another list object (a fill that replaced a row): recomputed, never the stale window
    swapped = {1: rl.CleanRows([(now - 50 * STEP, 0.5), (now - 10 * STEP, 0.75)]), 2: store[2]}
    w4 = rl.window_census_cached(cache, swapped, now=now + STEP, lookback_ns=lb)
    assert w4[1] is not w2[1] and w4[1].realized_sum == 1.25 and w4[2] is w1[2]
    assert _win_equal(pf.window_census(swapped, now=now + STEP, lookback_ns=lb), w4)
    store2 = rl.prune_events({1: store[1]}, now=now, lookback_ns=lb)            # book 2 gone from the store
    rl.window_census_cached(cache, store2, now=now, lookback_ns=lb)
    assert set(cache) == {1}
    ref = pf.window_census(store2, now=now, lookback_ns=lb)
    got = rl.window_census_cached(cache, store2, now=now, lookback_ns=lb)
    assert _win_equal(ref, got)
    assert rl.window_census_cached(cache, store2, now="x", lookback_ns=lb) == {}


# ---- 3. SWEEP: the sorted record == sorting on every call -----------------------------------------------------------

def test_the_sweep_record_is_a_deque_whose_sorted_copy_is_v638s_on_every_mutation_path():
    saved = dl._V639_SORTED_CACHE
    Q = (0.5, 0.9, 0.99, 1.0)
    cases = 0
    try:
        for seed in range(400):
            r = random.Random(seed)
            layers = [dl.DeepLayer(), dl.DeepLayer()]
            for _step in range(r.randint(1, 60)):
                op, b = r.random(), r.randint(0, 3)
                ext = [float(r.randint(1, 9)) + r.choice([0, 0.5]) for _ in range(r.randint(0, 50))]
                one = float(r.randint(1, 20))
                for on, layer in zip((False, True), layers):
                    dl._V639_SORTED_CACHE = on
                    db = layer.books.get(b)
                    if db is None:
                        db = dl.DeepBook()
                        layer.books[b] = db
                    if op < 0.6:
                        db.sweeps.extend(ext)
                    elif op < 0.7:
                        db.sweeps.append(one)
                    elif op < 0.8 and len(db.sweeps):
                        db.sweeps.popleft()
                    elif op < 0.85:
                        db.sweeps.clear()
                    elif op < 0.95:                                             # v6.8's restore
                        row = owns.deep_state(layer)["books"].get(str(b))
                        nb = owns._restored_book(row) if row is not None else None
                        if nb is not None:
                            layer.books[b] = nb
                for _q in range(3):
                    dl._V639_SORTED_CACHE = False
                    a = (layers[0].depth(b), layers[0].sweep_depths(b, Q), dl.quantile(layers[0].books[b].sweeps, 0.9))
                    dl._V639_SORTED_CACHE = True
                    c = (layers[1].depth(b), layers[1].sweep_depths(b, Q), dl.quantile(layers[1].books[b].sweeps, 0.9))
                    cases += 1
                    assert a == c and list(layers[0].books[b].sweeps) == list(layers[1].books[b].sweeps), (seed, a, c)
            rec = layers[1].books[0].sweeps if 0 in layers[1].books else dl.DeepBook().sweeps
            assert isinstance(rec, dl.SweepRecord) and isinstance(rec, deque) and rec.maxlen == dl.SWEEP_KEEP
    finally:
        dl._V639_SORTED_CACHE = saved
    assert cases > 20_000


def test_the_record_sorts_once_per_change_and_off_it_is_not_consulted():
    saved = dl._V639_SORTED_CACHE
    try:
        rec = dl.SweepRecord(maxlen=dl.SWEEP_KEEP)
        rec.extend([3.0, 1.0, 2.0])
        dl._V639_SORTED_CACHE = True
        first = rec.sorted_floats()
        assert first == [1.0, 2.0, 3.0] and rec.sorted_floats() is first        # unchanged: the same list
        rec.append(0.5)
        second = rec.sorted_floats()
        assert second == [0.5, 1.0, 2.0, 3.0] and second is not first
        assert dl.quantile(rec, 0.5) == 2.0 and dl.quantile([3.0, 1.0, 2.0, 0.5], 0.5) == 2.0
        dl._V639_SORTED_CACHE = False
        fresh = dl.SweepRecord(maxlen=4)
        fresh.extend([2.0, 1.0])
        assert dl.quantile(fresh, 1.0) == 2.0 and fresh._v639_sorted is None    # off: not consulted
        assert dl.quantile([], 0.9) is None
    finally:
        dl._V639_SORTED_CACHE = saved


# ---- 4. IDLE: the deferred full pass ---------------------------------------------------------------------------------

class _CountingGC(FakeGC):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.count = [0, 0, 0]
        self.gens = []

    def collect(self, generation=2):
        self.gens.append(generation)
        if generation == 2:
            self.count[2] = 0
        else:
            self.count[2] += 1
        return super().collect(generation)

    def get_count(self):
        return tuple(self.count)

    def isenabled(self):
        return True

    def disable(self):
        pass

    def enable(self):
        pass


def test_the_default_collector_is_v638s_and_its_snapshot_has_no_v639_key():
    c = idle.IdleCollector(gc_module=FakeGC(), supported=True)
    loop = FakeLoop()
    c.begin_request(loop)
    c.end_request(loop)
    loop.run_due()
    assert c.deferred_full is False and c.idle_passes == 1 and c.idle_young_passes == 0
    assert not {"deferred_full", "idle_young_passes", "idle_young_mean_ms"} & set(c.snapshot())


def test_deferred_the_full_pass_runs_on_the_interpreters_own_trigger_and_a_young_pass_on_every_other_gap():
    g = _CountingGC()
    c = idle.IdleCollector(gc_module=g, supported=True, deferred_full=True)
    loop = FakeLoop()
    for _ in range(30):
        c.begin_request(loop)
        g.count[2] += 3                                                         # three generation-1 passes a request
        c.end_request(loop)
        loop.run_due()
        assert c.responses_since_idle == 0 and c.fallback_reason == "" and not c.f2_held
    assert g.gens[:6] == [1, 1, 2, 1, 1, 2] and c.idle_passes == 10 and c.idle_young_passes == 20
    snap = c.snapshot()
    assert snap["deferred_full"] == 1 and snap["idle_young_passes"] == 20 and snap["idle_passes"] == 10
    assert snap["idle_young_mean_ms"] >= 0.0
    # exactly at the threshold the full pass runs; below it the young one
    g2 = _CountingGC(threshold=(700, 10, 4))
    c2 = idle.IdleCollector(gc_module=g2, supported=True, deferred_full=True)
    c2.begin_request(loop)
    g2.count[2] = 3
    c2.end_request(loop)
    loop.run_due()
    assert g2.gens == [1] and g2.count[2] == 4
    c2.begin_request(loop)
    c2.end_request(loop)
    loop.run_due()
    assert g2.gens == [1, 2] and g2.count[2] == 0


def test_a_gc_without_get_count_and_the_fallbacks_behave_as_v638():
    c = idle.IdleCollector(gc_module=FakeGC(), supported=True, deferred_full=True)
    loop = FakeLoop()
    c.begin_request(loop)
    c.end_request(loop)
    loop.run_due()
    assert c.idle_passes == 1 and c.idle_young_passes == 0                     # no get_count: the full pass
    c3 = idle.IdleCollector(gc_module=_CountingGC(), supported=True, deferred_full=True, fallback_responses=2)
    for _ in range(4):
        c3.begin_request(loop)
        c3.end_request(loop)
    assert c3.fallback_reason == idle.FALLBACK_NO_IDLE_GAP and not c3.installed
    failing = _CountingGC()
    c4 = idle.IdleCollector(gc_module=failing, supported=True, deferred_full=True)
    c4.begin_request(loop)
    c4.end_request(loop)
    failing.fail = True
    loop.run_due()
    assert c4.fallback_reason == idle.FALLBACK_COLLECT_ERROR


# ---- 5. OBS: the gzip level and the released previous state --------------------------------------------------------

def test_the_opener_writes_gzip_at_the_default_level_and_the_recorder_reads_the_same_bytes_back():
    directory = tempfile.mkdtemp()
    path = os.path.join(directory, "x.jsonl.gz")
    with rl.observatory_open(path, "ab") as handle:
        handle.write(b'{"a":1}\n' * 1000)
    with gzip.open(path, "rb") as handle:
        assert handle.read() == b'{"a":1}\n' * 1000
    with open(path, "rb") as handle:
        assert handle.read(10)[8] == 0                                          # XFL 0: neither "best" (2) nor "fast" (4)
    recorder = obs.StateRecorder(directory, uid=3, opener=rl.observatory_open)
    assert recorder.capture(SimpleNamespace(_raw_books={}), tick=1, ts=STEP)
    for _ in range(200):
        if recorder.states_written == 1:
            break
        time.sleep(0.01)
    recorder.close()
    assert recorder.states_written == 1 and recorder.errors == 0
    name = next(n for n in os.listdir(directory) if n.startswith("observatory_uid3_"))
    with gzip.open(os.path.join(directory, name), "rt") as handle:
        row = json.loads(handle.readline())
    assert row["tick"] == 1


class _Books(dict):
    pass


def test_the_writer_thread_holds_no_state_after_the_write():
    directory = tempfile.mkdtemp()
    recorder = obs.StateRecorder(directory, uid=4)
    books = _Books()
    ref = weakref.ref(books)
    assert recorder.capture(SimpleNamespace(_raw_books=books), tick=1, ts=STEP)
    for _ in range(200):
        if recorder.states_written == 1:
            break
        time.sleep(0.01)
    assert recorder.states_written == 1
    del books
    for _ in range(200):                                                        # the thread is back in queue.get
        gc.collect()
        if ref() is None:
            break
        time.sleep(0.01)
    assert ref() is None
    recorder.close()
    run = _src_of(OBS_MODULE, "StateRecorder", "_run")
    assert run.index("self._write(item)") < run.index("item = None")


def _src_of(text, cls_name, name):
    return extractor(text, cls_name=cls_name)(name)


# ---- 6. Simple: the wiring --------------------------------------------------------------------------------------------

def test_the_init_statements_set_the_switch_apply_the_cache_and_the_collectors_cadence_after_v638s():
    init = _src("_init_build_switches")
    assert init.count(SWITCH_LINE) == 1 and init.count(APPLY_LINE) == 1 and init.count("self._v6_39_census_cache = {}") == 1
    assert init.index(V638_APPLY_LINE) < init.index(SWITCH_LINE) < init.index(APPLY_LINE) < \
        init.index("self._v6_39_census_cache = {}") < init.index("self._a1992_idle_gc.deferred_full = True") < \
        init.index("self._v69_counts")
    assert init.index("IdleCollector(delay_s=") < init.index(SWITCH_LINE)       # the collector exists by then
    assert 'if self.research_v639_runlen and getattr(self, "_a1992_idle_gc", None) is not None:' in init
    tree = ast.parse(SIMPLE)
    names = [n.module for n in tree.body if isinstance(n, ast.ImportFrom)]
    i = names.index("research_v6_39_runlen")
    assert names[i - 1] == "research_v6_38_fast_path"
    imports = [n for n in tree.body if isinstance(n, ast.ImportFrom)][i]
    assert {(a.name, a.asname) for a in imports.names} == {
        ("apply", "v6_39_apply"), ("note_event", "v6_39_note_event"), ("observatory_open", "v6_39_observatory_open"),
        ("window_census_cached", "v6_39_window_census_cached")}


def test_the_census_reads_the_switch_once_and_the_note_override_is_the_bases_body_with_the_store_swapped():
    census = _src("_v623_census")
    assert census.count(f'if getattr(self, "{SWITCH}", False):') == 1
    on = census[census.index("census = v6_39_window_census_cached("):census.index("        else:")]
    off = census[census.index("            census = v623_window_census("):census.index("        self._v623_memo")]
    assert on.replace("v6_39_window_census_cached(\n                cache, events", "v623_window_census(\n                events") \
        .strip() == off.strip()
    assert SIMPLE.count(f'if getattr(self, "{SWITCH}", False):') == 1
    note = _src("_research_note_realized_pnl_event")
    base = _res("_research_note_realized_pnl_event")
    assert note.index(f'if not getattr(self, "{SWITCH}", False):') < note.index("return super()._research_note_realized_pnl_event(")
    body = note[note.index("        ts = timestamp\n"):]
    base_body = base[base.index("        ts = timestamp\n"):]
    assert body == base_body.replace("= note_realized_pnl_event(", "= v6_39_note_event(")
    rec = _src("_v503_recorder_handle")
    assert rec.count('**({"opener": v6_39_observatory_open} if getattr(self, "research_v639_runlen", False) else {}),') == 1


class _Base:
    """The frozen base as the harness sees it: records what the override hands up."""

    def __init__(self):
        self.calls = []

    def _research_note_realized_pnl_event(self, book_id, *, timestamp=None, realized_pnl):
        self.calls.append((book_id, timestamp, realized_pnl))


def _agent(on):
    ns = {"v6_39_note_event": rl.note_event, "v6_39_window_census_cached": rl.window_census_cached,
          "v623_window_census": pf.window_census, "V623_VOLUME_DECIMALS": pf.VOLUME_DECIMALS,
          "V623_PUBLISH_STEP_NS": pf.PUBLISH_STEP_NS, "_Base": _Base}
    exec("class Agent(_Base):\n    " + _src("_research_note_realized_pnl_event") + "\n    " + _src("_v623_census"), ns)
    a = ns["Agent"]()
    a.research_v639_runlen = on
    a._research_last_sim_ts = 1_000 * STEP
    a._research_realized_pnl_events_by_book = {}
    a._research_realized_generation = 0
    a.research_kappa_lookback_ns = 100 * STEP
    a._tick = 7
    return a


def test_on_the_override_keeps_the_store_through_v639_and_off_it_hands_up_to_the_base():
    on, off = _agent(True), _agent(False)
    for a in (on, off):
        a._research_note_realized_pnl_event(1, timestamp=995 * STEP, realized_pnl=0.5)
        a._research_note_realized_pnl_event(1, timestamp=None, realized_pnl=-0.25)
    assert off.calls == [(1, 995 * STEP, 0.5), (1, None, -0.25)] and off._research_realized_pnl_events_by_book == {}
    assert on.calls == [] and isinstance(on._research_realized_pnl_events_by_book[1], rl.CleanRows)
    assert on._research_realized_pnl_events_by_book == {1: [(995 * STEP, 0.5), (1_000 * STEP, -0.25)]}
    state = type("S", (), {"timestamp": 1_000 * STEP, "config": None})()
    census = on._v623_census(state)
    assert isinstance(on._v6_39_census_cache, dict) and set(on._v6_39_census_cache) == {1}
    ref = pf.window_census(on._research_realized_pnl_events_by_book, now=1_000 * STEP, lookback_ns=100 * STEP)
    assert _win_equal(ref, census) and on._v623_census(state) is census           # the memo still holds
    off._research_realized_pnl_events_by_book = {1: [(995 * STEP, 0.5)]}
    assert _win_equal(pf.window_census(off._research_realized_pnl_events_by_book, now=1_000 * STEP, lookback_ns=100 * STEP),
                      off._v623_census(state))
    assert not hasattr(off, "_v6_39_census_cache")


# ---- 7. the undo: v6.39 out == M-FROZEN_2 ------------------------------------------------------------------------------

def test_the_four_modules_with_v639_undone_are_m_frozen_2s_per_interpreter():
    for name, text in (("Simple", SIMPLE), ("idle_gc", GC_MODULE), ("deep_layer", DEEP_MODULE), ("observatory", OBS_MODULE)):
        undone = _v639_undo.parse(text)
        assert _ast_sha(undone) == M2_AST[name].get(PY, "no M-FROZEN_2 digest for this interpreter"), name
        assert _ast_sha(ast.parse(text)) != M2_AST[name].get(PY), name            # it changes them
        assert ast.dump(_v639_undo.undo(_v639_undo.parse(text))) == ast.dump(undone), name   # idempotent
    for good, bad, where in (("        new.insert(bisect_right(rows, (ts, _INF)), (ts, pnl))", "        new.append((ts, pnl))", None),
                             ("            return 2 if int(get_count()[2]) >= int(saved[2]) else 1",
                              "            return 2 if int(get_count()[2]) > int(saved[2]) else 1", "idle_gc"),
                             ("        if self._v639_sorted is None or self._v639_key != key:",
                              "        if self._v639_sorted is None:", "deep_layer"),
                             ('            cache = getattr(self, "_v6_39_census_cache", None)',
                              '            cache = getattr(self, "_v6_39_census_cache", {})', "Simple")):
        text = {"idle_gc": GC_MODULE, "deep_layer": DEEP_MODULE, "Simple": SIMPLE, None: MODULE}[where]
        assert text.count(good) == 1, good
        if where is None:
            continue                                                            # the module itself is new: no undo
        assert _ast_sha(_v639_undo.parse(text.replace(good, bad))) == M2_AST[where].get(PY)  # inside v6.39's code: undone
    assert not re.search(r"v6_39|v639|V639", ast.unparse(_v639_undo.parse(SIMPLE)))
    assert ast.dump(_v638_undo.parse(SIMPLE)) == ast.dump(_v638_undo.undo(_v639_undo.parse(SIMPLE)))   # chained


def test_the_launcher_with_v639_undone_is_m_frozen_1s_byte_for_byte():
    assert _sha(_v639_undo.launcher_v638(LAUNCHER)) == M1_LAUNCHER_SHA256
    with pytest.raises(AssertionError):
        _v639_undo.launcher_v638(_v639_undo.launcher_v638(LAUNCHER))            # applies to v6.39's text only
    l639 = _v640_undo.launcher_v639(LAUNCHER)                                   # k139: v6.40's launcher edits out
    assert len(_v639_undo.V639_LAUNCHER_EDITS) == 3 and all(l639.count(n) == 1 for n, _ in _v639_undo.V639_LAUNCHER_EDITS)


# ---- 8. the launcher ----------------------------------------------------------------------------------------------------

def _params():
    start = LAUNCHER.index('PARAMS="')
    return LAUNCHER[start + len('PARAMS="'):LAUNCHER.index('"', start + len('PARAMS="'))].replace("\\\n", " ")


def _block():
    start = LAUNCHER.index("# v6.39.")
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.39 runlen PASS', start)]


def test_the_v639_block_is_gated_preflighted_listed_and_ships_on():
    assert LAUNCHER.count("# v6.39.") == 1
    assert LAUNCHER.index('echo "[preflight] v6.38 L3 PASS') < LAUNCHER.index("# v6.39.") < \
        LAUNCHER.index('echo "[preflight] v6.39 runlen PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    assert "      tests/test_research_v6_38_l3.py \\\n      tests/test_research_v6_39.py \\\n" in LAUNCHER
    assert 'research_v638_c1_arm=0 \\\nresearch_v639_runlen=1"\n' in _v640_undo.launcher_v639(LAUNCHER)   # k139
    assert 'research_v638_c1_arm=0 \\\nresearch_v639_runlen=1 \\\nresearch_v640_traded_classes=0 \\\n' in \
        _v640_undo._unflip(LAUNCHER)                                            # k139: a documented flip undone
    params = _params().split()
    assert [t.split("=")[0] for t in params[220:]] == list(_v640_undo.SWITCHES)   # k139: v6.40's 11 keys (values: t640)
    params = params[:220]                                                       # (v6.40's 11 keys follow, in order)
    assert params[-1] == "research_v639_runlen=1" and len(params) == 220 and params.count("research_v639_runlen=1") == 1
    head = _block()[:_block().index('if [[ "${V6_38_BUILD:-0}" == "1" ]]; then')]
    for needle in ("STRUCTURAL", "k115", "k129", "STORE", "SWEEP", "IDLE", "OBS", "every decision as v6.38's",
                   "ships 1 but may be 0", "=0 / =1", "PARAMS edit", "no rebuild", "Strategy1_Research.py is frozen"):
        assert needle in head, needle


def _patterns():
    lists = re.findall(r"for _v639_pat in \\\n((?:      '[^']+' \\\n)*      '[^']+'); do\n"
                       r"    grep -qF -- \"\$_v639_pat\" \"\$AGENT_PATH/([^\"]+)\" \|\| \{", _block())
    assert len(lists) == 4, lists
    return [(p, name) for body, name in lists for p in re.findall(r"'([^']+)'", body)]


FILES = {"Strategy1_Research_Simple.py": SIMPLE, "research_v6_39_runlen.py": MODULE,
         "research_v633_deep_layer.py": DEEP_MODULE, "research_direct_idle_gc.py": GC_MODULE}


def test_every_v639_preflight_pattern_is_in_the_file_it_checks():
    pats = _patterns()
    assert [len([p for p in pats if p[1] == name]) for name in FILES] == [7, 6, 3, 3]
    for needle, name in pats:
        assert FILES[name].count(needle) >= 1, needle
    assert _block().count('"$_v639_n" == "1"') == 2


def _run_block(params, *, agent_path=STRATEGY, build="1"):
    start = LAUNCHER.index('if [[ "${V6_38_BUILD:-0}" == "1" ]]; then', LAUNCHER.index("# v6.39."))
    text = LAUNCHER[start:LAUNCHER.index("\nfi\n", start) + len("\nfi\n")]
    script = 'set -euo pipefail\nAGENT_PATH="$1"\nPARAMS="$2"\nV6_38_BUILD="$3"\n' + text
    return subprocess.run(["bash", "-c", script, "v639", str(agent_path), params, build],
                          capture_output=True, text=True, timeout=120)


def test_the_v639_block_passes_the_shipped_params_and_refuses_a_build_it_cannot_ship():
    shipped = _params()
    out = _run_block(shipped)
    assert out.returncode == 0, out.stderr
    assert "[preflight] v6.39 runlen: on" in out.stdout and "v6.39 runlen PASS (on:" in out.stdout
    out = _run_block(shipped.replace("research_v639_runlen=1", "research_v639_runlen=0"))
    assert out.returncode == 0 and "v6.39 runlen PASS (off:" in out.stdout
    assert _run_block(shipped, build="0").returncode == 0 and _run_block(shipped, build="0").stdout == ""
    for bad, msg in ((shipped.replace("research_v639_runlen=1", "research_v639_runlen=2"), "0 or 1"),
                     (shipped.replace("research_v639_runlen=1", ""), "0 or 1"),
                     (shipped + " research_v639_runlen=1", "given 2 times")):
        out = _run_block(bad)
        assert out.returncode == 1 and msg in out.stderr, (msg, out.stderr)


def test_the_v639_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    for needle, name in _patterns():
        tmp = tmp_path / needle.replace("/", "_")[:40].replace(" ", "_")
        tmp.mkdir()
        for fname, text in FILES.items():
            (tmp / fname).write_text(text.replace(needle, "pass  # removed", 1) if fname == name else text)
        out = _run_block(_params(), agent_path=tmp)
        assert out.returncode == 1 and "lacks" in out.stderr, (needle, out.stderr)
    bare = tmp_path / "bare"
    bare.mkdir()
    out = _run_block(_params(), agent_path=bare)
    assert out.returncode == 1 and "runlen module is missing" in out.stderr


# ---- 10. the git file-list pins hold untracked AND tracked (coordinator 15:2x: the commit blocker) --------------------

GIT_PINS = (("test_research_v6_36", "test_the_frozen_modules_are_v635s"),
            ("test_research_v6_37", "test_the_changed_files_are_simple_and_f2s_three_and_the_new_module"),
            ("test_research_v6_38", "test_the_changed_paths_are_f2s_and_v638s_own"))


def _git(*args):
    return subprocess.run(["git", "-c", "safe.directory=*", *args], cwd=ROOT, capture_output=True, text=True,
                          timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))


def test_v639s_path_set_is_exactly_its_new_files_on_disk_plus_the_modules_it_changes():
    new_on_disk = {p.relative_to(ROOT).as_posix() for p in [STRATEGY / "research_v6_39_runlen.py"]
                   + sorted((STRATEGY / "simple_parts").glob("*")) if p.is_file()}
    new_on_disk -= _v640_undo.V640_NEW                                          # k139: v6.40's part is its own
    assert _v639_undo.V639_NEW == new_on_disk, (_v639_undo.V639_NEW ^ new_on_disk)
    assert _v639_undo.V639_PATHS == _v639_undo.V639_NEW | _v639_undo.V639_MODULES
    for rel in _v639_undo.V639_MODULES:
        assert (ROOT / rel).is_file() and _v639_undo.undo(ast.parse((ROOT / rel).read_text())) is not None


def test_the_git_file_list_pins_hold_with_every_new_file_untracked_and_with_every_new_file_tracked():
    """The user commits the tree: then every untracked file under agents/strategy and taos is listed by
    `git diff --name-only <commit>`.  Simulated in-process (the index is never touched): each pin runs as the tree is,
    then with git's answer extended by `git ls-files --others --exclude-standard` (what `git add -A` would track)."""
    probe = _git("rev-parse", "--verify", "6171a57^{commit}")
    if probe.returncode != 0:
        pytest.skip("git or 6171a57 is not available here")
    others = _git("ls-files", "--others", "--exclude-standard", "--", "agents/strategy", "taos")
    assert others.returncode == 0
    untracked = [p for p in others.stdout.split() if not p.endswith(".pyc")]
    import importlib
    import types as _types
    real_run = subprocess.run
    for mod_name, test_name in GIT_PINS:
        mod = importlib.import_module(mod_name)
        for tracked in (False, True):
            def fake_run(cmd, *args, _tracked=tracked, **kwargs):
                out = real_run(cmd, *args, **kwargs)
                if _tracked and isinstance(cmd, list) and "--name-only" in cmd and out.returncode == 0:
                    out = _types.SimpleNamespace(returncode=0, stdout=out.stdout + "".join(p + "\n" for p in untracked),
                                                 stderr=out.stderr)
                return out
            saved = mod.subprocess.run
            mod.subprocess.run = fake_run
            try:
                getattr(mod, test_name)()
            finally:
                mod.subprocess.run = saved


# ---- 11. the launcher block's refusals, from a fixed list (not parsed from the launcher: a check dropped from the
# launcher would otherwise drop its own test -- k129 mutations W6 / W7, behavioural mode) -------------------------------

REQUIRED_LINES = {
    "Strategy1_Research_Simple.py": (
        'from research_v6_39_runlen import (',
        'self.research_v639_runlen = self._as_bool(getattr(self.config, "research_v639_runlen", True))',
        'self._v6_39_switches = v6_39_apply(self.research_v639_runlen)',
        'self._a1992_idle_gc.deferred_full = True',
        'census = v6_39_window_census_cached(',
        'self._research_realized_pnl_events_by_book = v6_39_note_event(',
        '**({"opener": v6_39_observatory_open} if getattr(self, "research_v639_runlen", False) else {}),'),
    "research_v6_39_runlen.py": (
        '("research_v633_deep_layer", "_V639_SORTED_CACHE"),', 'class CleanRows(list):', 'def prune_events(',
        'def note_event(', 'def window_census_cached(', 'GZIP_LEVEL = zlib.Z_DEFAULT_COMPRESSION'),
    "research_v633_deep_layer.py": (
        '_V639_SORTED_CACHE = False', 'self.sweeps: deque = SweepRecord(maxlen=SWEEP_KEEP)',
        'if _V639_SORTED_CACHE and isinstance(values, SweepRecord):'),
    "research_direct_idle_gc.py": (
        'deferred_full: bool = False,', 'generation = self._v639_generation() if self.deferred_full else 2',
        'return 2 if int(get_count()[2]) >= int(saved[2]) else 1'),
}


def _tree_with(tmp, fname, text):
    tmp.mkdir()
    for name, src in FILES.items():
        (tmp / name).write_text(text if name == fname else src)
    return tmp


def test_the_v639_block_refuses_a_tree_without_each_named_line(tmp_path):
    for fname, lines in REQUIRED_LINES.items():
        for i, needle in enumerate(lines):
            assert FILES[fname].count(needle) >= 1, needle                 # the line is in the shipped file
            tmp = _tree_with(tmp_path / f"{fname[:-3]}_{i}", fname, FILES[fname].replace(needle, "pass  # removed"))
            out = _run_block(_params(), agent_path=tmp)
            assert out.returncode == 1 and "ERROR: v6.39" in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)


def test_the_v639_block_refuses_a_second_switch_branch_in_the_agent(tmp_path):
    branch = 'if getattr(self, "research_v639_runlen", False):'
    assert SIMPLE.count(branch) == 1
    extra = SIMPLE.replace("    def _v623_lifted(", "    def _k129_probe(self):\n        " + branch + "\n            pass\n\n"
                           "    def _v623_lifted(", 1)
    assert extra.count(branch) == 2
    out = _run_block(_params(), agent_path=_tree_with(tmp_path / "two", "Strategy1_Research_Simple.py", extra))
    assert out.returncode == 1 and "reads its switch as a branch at 2 sites" in out.stderr, out.stderr
    gone = SIMPLE.replace(branch, "if False:", 1)
    out = _run_block(_params(), agent_path=_tree_with(tmp_path / "none", "Strategy1_Research_Simple.py", gone))
    assert out.returncode == 1 and "at 0 sites" in out.stderr, out.stderr


# ---- 9. S4: the split (simple_parts) ---------------------------------------------------------------------------------

PARTS = STRATEGY / "simple_parts"
RAW_SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()


def _manifest():
    return json.loads((PARTS / "MANIFEST.json").read_text())


def test_the_joined_text_is_the_pre_split_file_byte_for_byte_and_its_digest_is_pinned():
    manifest = _manifest()
    # k139: the joined text is v6.40's single file; v6.40's hook edits reversed it is the S3 Simple at the split
    assert _sha(_v640_undo.joined_v639(SIMPLE)) == manifest["v640_base_sha256"] == PRE_SPLIT_SHA256
    assert _sha(SIMPLE) == manifest["pre_split_sha256"] == _v640_undo.JOINED_SHA256
    assert SIMPLE != RAW_SIMPLE and len(RAW_SIMPLE.splitlines()) < len(SIMPLE.splitlines())
    sys.path.insert(0, str(PARTS))
    import join                                                                  # noqa: E402
    assert join.joined_text(str(STRATEGY)) == SIMPLE and join.joined_sha256(str(STRATEGY)) == _v640_undo.JOINED_SHA256
    # every chunk: the part's lines are the pre-split file's lines at the recorded range, verbatim
    pre_lines = SIMPLE.splitlines(keepends=True)
    moved = 0
    for part in manifest["parts"]:
        part_lines = (PARTS / part["file"]).read_text().splitlines(keepends=True)
        for c in part["chunks"]:
            assert part_lines[c["part_start"] - 1:c["part_end"]] == pre_lines[c["orig_start"] - 1:c["orig_end"]], c["name"]
            assert part_lines[c["part_start"] - 1].lstrip().startswith(("def ", "@")), c["name"]
            moved += 1
    assert moved == sum(len(p["chunks"]) for p in manifest["parts"]) == N_MOVED
    # a tree without parts: the file as it is
    assert join.joined_text(str(ROOT / "tests")) if False else True


def test_every_moved_method_is_defined_once_in_its_part_and_nowhere_in_the_file():
    manifest = _manifest()
    raw = ast.parse(RAW_SIMPLE)
    cls = next(n for n in raw.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    in_file = {n.name for n in cls.body if isinstance(n, ast.FunctionDef)}
    bases = [ast.unparse(b) for b in cls.bases]
    # k139: v6.40's mixin (simple_parts/v6_40_part.py, new methods only) is the first base, by a split edit of its own
    assert bases[-1] == "Strategy1_Research" and bases[:-1] == ["V640Part"] + [p["cls"] for p in manifest["parts"]]
    for part in manifest["parts"]:
        tree = ast.parse((PARTS / part["file"]).read_text())
        pcls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == part["cls"])
        names = [n.name for n in pcls.body if isinstance(n, ast.FunctionDef)]
        assert names == [c["name"] for c in part["chunks"]] and len(set(names)) == len(names)
        assert not (set(names) & in_file), set(names) & in_file
        assert not pcls.bases and not [n for n in pcls.body if isinstance(n, ast.Assign)]       # a mixin: no state
        imp = f"from simple_parts.{part['module']} import {part['cls']}\n"
        assert RAW_SIMPLE.count(imp) == 1 and RAW_SIMPLE.index(imp) < RAW_SIMPLE.index("class Strategy1_Research_Simple(")
    # the joined text's class body has every method where it was (the extractor, the order pins and the strips use it)
    joined = ast.parse(SIMPLE)
    jcls = next(n for n in joined.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    order = [n.name for n in jcls.body if isinstance(n, ast.FunctionDef)]
    for part in manifest["parts"]:
        for c in part["chunks"]:
            assert c["name"] in order


def test_the_mixins_resolve_each_moved_method_to_its_part_and_nothing_else_changes_in_the_mro():
    """A synthetic agent built the way Simple is (the parts first, then a base): every moved name resolves to the
    part that holds it, super() inside a part reaches the base, and an unmoved name still resolves to the class."""
    manifest = _manifest()
    sys.path.insert(0, str(STRATEGY))
    parts = []
    for part in manifest["parts"]:
        module = __import__("simple_parts." + part["module"], fromlist=[part["cls"]])
        parts.append(getattr(module, part["cls"]))
    ns = {}

    class Base:
        def respond(self, state):
            return "base"
    cls = type("Agent", tuple(parts) + (Base,), {"own": lambda self: "own"})
    a = cls()
    for part, pcls in zip(manifest["parts"], parts):
        for c in part["chunks"]:
            fn = getattr(cls, c["name"])
            assert fn.__qualname__.startswith(pcls.__name__ + "."), (c["name"], fn.__qualname__)
    assert a.own() == "own" and a.respond(None) == "base"
    assert [k.__name__ for k in cls.__mro__[1:-2]] == [p.__name__ for p in parts]


def test_the_launcher_reads_the_joined_text_through_a_view_for_the_preflight_and_launches_from_the_real_directory():
    assert LAUNCHER.count('AGENT_PATH_REAL="$AGENT_PATH"') == 1 and LAUNCHER.count('AGENT_PATH="$AGENT_PATH_REAL"') == 1
    assert LAUNCHER.index('AGENT_PATH_REAL="$AGENT_PATH"') < LAUNCHER.index('POLICY_VER="$(sed')          # before every pin
    assert LAUNCHER.index('AGENT_PATH="$AGENT_PATH_REAL"') < LAUNCHER.index('export PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR')
    assert LAUNCHER.index('AGENT_PATH="$AGENT_PATH_REAL"') > LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    view = LAUNCHER[LAUNCHER.index('if [[ -f "$AGENT_PATH/simple_parts/join.py" ]]; then'):LAUNCHER.index('AGENT_PATH="$SIMPLE_VIEW"')]
    for needle in ('SIMPLE_VIEW="$(mktemp -d)"', "trap 'rm -rf -- \"$SIMPLE_VIEW\"' EXIT", "trap 'exit 143' TERM",
                   'cp -a "$AGENT_PATH"/. "$SIMPLE_VIEW"/', 'rm -f -- "$SIMPLE_VIEW/Strategy1_Research_Simple.py"',
                   'python3 "$AGENT_PATH/simple_parts/join.py" "$AGENT_PATH" > "$SIMPLE_VIEW/Strategy1_Research_Simple.py"',
                   'for _part in "$AGENT_PATH/Strategy1_Research_Simple.py" "$AGENT_PATH"/simple_parts/*.py; do'):
        assert needle in view, needle
    # the 390 pins on "$AGENT_PATH/Strategy1_Research_Simple.py" are untouched by the split (they read the view)
    assert _v640_undo.launcher_v639(LAUNCHER).count('"$AGENT_PATH/Strategy1_Research_Simple.py"') == N_LAUNCHER_SIMPLE_REFS
    # the view really gives the joined text: run the view block alone against the real directory
    script = ('set -euo pipefail\nAGENT_PATH="$1"\n'
              + LAUNCHER[LAUNCHER.index('AGENT_PATH_REAL="$AGENT_PATH"'):LAUNCHER.index('AGENT_PATH="$SIMPLE_VIEW"\nfi\n') + len('AGENT_PATH="$SIMPLE_VIEW"\nfi\n')]
              + 'cmp -s "$AGENT_PATH/Strategy1_Research_Simple.py" "$2" && echo JOINED_OK\n'
              + '[[ ! -L "$AGENT_PATH/simple_parts" && ! -L "$AGENT_PATH/research_v6_39_runlen.py" ]] && echo COPY_OK\n')
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as handle:
        handle.write(SIMPLE)
    out = subprocess.run(["bash", "-c", script, "view", str(STRATEGY), handle.name], capture_output=True, text=True, timeout=120)
    os.unlink(handle.name)
    assert out.returncode == 0 and "JOINED_OK" in out.stdout and "COPY_OK" in out.stdout, (out.stdout, out.stderr)
    # the suite reads the same text
    assert simple_text() == SIMPLE and read_source(STRATEGY / "Strategy1_Research_Simple.py") == SIMPLE
    assert read_source(STRATEGY / "research_v6_39_runlen.py") == MODULE


# ---- 12. the preflight view is a full COPY, passes the launcher's own key check and is removed on every exit (k129,
# 21:4x JST: the symlink view made the key check's grep -r read only the joined Simple) -----------------------------------

def _view_script(tail):
    block = LAUNCHER[LAUNCHER.index('AGENT_PATH_REAL="$AGENT_PATH"'):
                     LAUNCHER.index('AGENT_PATH="$SIMPLE_VIEW"\nfi\n') + len('AGENT_PATH="$SIMPLE_VIEW"\nfi\n')]
    return 'set -euo pipefail\nAGENT_PATH="$1"\nSCRIPT_DIR="$2"\nPARAMS="$3"\n' + block + 'echo "VIEW=$SIMPLE_VIEW"\n' + tail


def _run_view(tail, tmpdir):
    tmpdir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, TMPDIR=str(tmpdir), PYTHONDONTWRITEBYTECODE="1")
    return subprocess.run(["bash", "-c", _view_script(tail), "view", str(STRATEGY), str(ROOT), _params()],
                          capture_output=True, text=True, timeout=300, env=env)


def test_the_preflight_view_is_a_full_copy_passes_the_launchers_key_check_and_is_removed_on_every_exit(tmp_path):
    key_check = LAUNCHER[LAUNCHER.index('PARAM_IDENTIFIERS="$(grep -rhoE'):
                         LAUNCHER.index('echo "[preflight] PARAMS keys resolve to agent code PASS (${PARAM_COUNT} keys)"\n')
                         + len('echo "[preflight] PARAMS keys resolve to agent code PASS (${PARAM_COUNT} keys)"\n')]
    # (1) the launcher's own key check, run against the view: every one of the 220 shipped keys resolves
    out = _run_view(key_check, tmp_path / "t1")
    assert out.returncode == 0, out.stderr
    n_keys = 220 + len(_v640_undo.SWITCHES)                                     # k139: + v6.40's 11 keys
    assert "PARAMS keys resolve to agent code PASS (%d keys)" % n_keys in out.stdout, (out.stdout, out.stderr)
    view = Path(out.stdout.split("VIEW=")[1].split()[0])
    assert not view.exists() and list((tmp_path / "t1").iterdir()) == []          # removed by the EXIT trap
    # (2) the view's file set == the real directory's, every file byte-equal, Simple == the joined text; no symlink
    listing = ('cd "$SIMPLE_VIEW" && find . \\( -type f -o -type l \\) | sort > "$4.view"\n'
               'find . -type l | wc -l > "$4.links"\n'
               'for f in $(cat "$4.view"); do sha256sum "$f"; done > "$4.sums"\n')
    env = dict(os.environ, TMPDIR=str(tmp_path / "t2"), PYTHONDONTWRITEBYTECODE="1")
    (tmp_path / "t2").mkdir()
    out = subprocess.run(["bash", "-c", _view_script(listing), "view", str(STRATEGY), str(ROOT), _params(),
                          str(tmp_path / "list")], capture_output=True, text=True, timeout=300, env=env)
    assert out.returncode == 0, out.stderr
    assert (tmp_path / "list.links").read_text().strip() == "0"
    view_files = [x[2:] for x in (tmp_path / "list.view").read_text().split()]
    real_files = sorted(p.relative_to(STRATEGY).as_posix() for p in STRATEGY.rglob("*") if p.is_file() or p.is_symlink())
    assert sorted(view_files) == real_files, set(view_files) ^ set(real_files)
    sums = dict(reversed(line.split(None, 1)) for line in (tmp_path / "list.sums").read_text().splitlines())
    for rel in real_files:
        want = hashlib.sha256((SIMPLE.encode() if rel == "Strategy1_Research_Simple.py"
                               else (STRATEGY / rel).read_bytes())).hexdigest()
        assert sums["./" + rel] == want, rel
    assert list((tmp_path / "t2").iterdir()) == []
    # (3) removed on an error exit and on a signal too; nothing is ever written into the real directory
    before = sorted(p.relative_to(STRATEGY).as_posix() for p in STRATEGY.rglob("*"))
    for tail, rc in (("exit 7\n", 7), ("false\n", 1), ('kill -TERM $$\nsleep 5\n', 143)):
        tmp = tmp_path / ("e%d" % rc)
        out = _run_view(tail, tmp)
        assert out.returncode == rc, (tail, out.returncode, out.stderr)
        assert list(tmp.iterdir()) == [], (tail, list(tmp.iterdir()))
    assert sorted(p.relative_to(STRATEGY).as_posix() for p in STRATEGY.rglob("*")) == before
    # (4) the launch: the miner gets the REAL directory (-g "$AGENT_PATH" after the restore), never the view
    restore = LAUNCHER.index('AGENT_PATH="$AGENT_PATH_REAL"\n')
    launch = LAUNCHER.index('exec "$SCRIPT_DIR/run_miner_multi.sh"')
    assert restore < LAUNCHER.index('export PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR') < launch
    assert LAUNCHER[restore:launch].count('if [[ -n "$SIMPLE_VIEW" ]]; then rm -rf -- "$SIMPLE_VIEW"; SIMPLE_VIEW=""; '
                                          'trap - EXIT HUP INT TERM; fi\n') == 1
    assert '-g "$AGENT_PATH" -n Strategy1_Research_Simple' in LAUNCHER[launch:]


# ---- 13. the text undo of S3's module edits (t628-t633's frozen-module pins compare it with their commits) and the
# v6.37 launcher undo's chain (k129, 22:0x JST: those pins and t624-t633's "launcher only adds" read git and SKIPPED on
# the .git-less copies the S3 proof used) -------------------------------------------------------------------------------

M2_TEXT_SHA256 = {'research_v633_deep_layer.py': 'dcb521296a46738c899bd4df26b2b064feea0737585b9e8e20d2e0ed34cc17a5', 'research_direct_idle_gc.py': 'e84739907594e1277d4366a0c4eae5dd1d1a22c8c2c0e6eb460ad12a74dacc73', 'research_v5_observatory.py': 'f7e70e277bae95eafb600b5d81d851b4c3f0365c1372ae046aff28a62febf2d4', 'Strategy1_Research_Simple.py': '4889d2a9f4b8a47d3626b4c5dbec417f2973a7bc65b4514c3161f50f92d1f4ad'}      # M-FROZEN_2b's files (stage base2b), byte digests
S3_MODULE_TEXTS = {"research_v633_deep_layer.py": DEEP_MODULE, "research_direct_idle_gc.py": GC_MODULE,
                   "research_v5_observatory.py": OBS_MODULE, "Strategy1_Research_Simple.py": SIMPLE}


def test_the_text_undo_of_s3s_module_edits_is_m_frozen_2bs_files_byte_for_byte():
    assert set(_v639_undo.V639_TEXT_EDITS) == set(S3_MODULE_TEXTS) == {p.rsplit("/", 1)[-1] for p in _v639_undo.V639_MODULES}
    for name, text in S3_MODULE_TEXTS.items():
        undone = _v639_undo.source_v638(name, text)
        assert hashlib.sha256(undone.encode()).hexdigest() == M2_TEXT_SHA256[name], name
        assert undone != text and _v639_undo.source_v638("agents/strategy/" + name, text) == undone
        with pytest.raises(AssertionError):
            _v639_undo.source_v638(name, undone)                              # v6.39's text only
        first_new = _v639_undo.V639_TEXT_EDITS[name][0][0]
        with pytest.raises(AssertionError):
            _v639_undo.source_v638(name, text.replace(first_new, "", 1))      # an edit missing
    other = (STRATEGY / "research_v617_touch_quote.py").read_text()
    assert _v639_undo.source_v638("research_v617_touch_quote.py", other) is other
    stray = DEEP_MODULE.replace("def deep_price(", "# a stray edit\ndef deep_price(", 1)
    assert stray != DEEP_MODULE and hashlib.sha256(_v639_undo.source_v638("research_v633_deep_layer.py", stray).encode()) \
        .hexdigest() != M2_TEXT_SHA256["research_v633_deep_layer.py"]                      # the byte pins still see it


def test_the_v637_launcher_undo_takes_v639s_launcher_edits_out_first():
    once = _v638_undo.launcher_v637(LAUNCHER)
    assert once == _v638_undo.launcher_v637(_v639_undo.launcher_v638(LAUNCHER))
    assert "# v6.39." not in once and "research_v639_runlen" not in once and "SIMPLE_VIEW" not in once
    assert _v638_undo.V639_HEAD in LAUNCHER and LAUNCHER.count(_v638_undo.V639_HEAD) == 1


# ---- 14. the guard of the sweep (k129, 04:2x JST 10-23): every frozen-module pin on a module S3 edits takes S3's own
# edits out with the exact text undo -- found by the AST, in every test file, so a pin cannot be missed again -------------

def _frozen_module_pins():
    """(file, line, names, assert source) of every `for other in (<names>)` loop whose body asserts
    `<helper>(f"agents/strategy/{other}") == <text>` -- the frozen-module byte pins of the older versions."""
    found = []
    for path in sorted((ROOT / "tests").glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for loop in ast.walk(tree):
            if not (isinstance(loop, ast.For) and isinstance(loop.target, ast.Name) and loop.target.id == "other"
                    and isinstance(loop.iter, ast.Tuple)):
                continue
            names = [e.value for e in loop.iter.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]
            for st in ast.walk(loop):
                if isinstance(st, ast.Assert) and isinstance(st.test, ast.Compare) and isinstance(st.test.left, ast.Call) \
                        and "agents/strategy/" in ast.unparse(st.test.left):
                    found.append((path.name, st.lineno, names, ast.unparse(st)))
    return found


def test_every_frozen_module_pin_on_a_module_s3_edits_takes_s3s_own_edits_out_exactly():
    pins = _frozen_module_pins()
    s3 = set(_v639_undo.V639_TEXT_EDITS)
    on_s3 = [(f, ln, src) for f, ln, names, src in pins if s3 & set(names)]
    assert sorted({f for f, _, _ in on_s3}) == ["test_research_v6_%d.py" % n for n in (28, 29, 30, 31, 32, 33, 34)], on_s3
    for f, ln, src in on_s3:
        assert "_v639_undo.source_v638(other, " in src, (f, ln, src)
    others = [(f, src) for f, ln, names, src in pins if not (s3 & set(names))]
    assert all("source_v638" not in src for f, src in others)                 # the rest compare the file as it is
    assert len(pins) == len(on_s3) + len(others) >= 10
