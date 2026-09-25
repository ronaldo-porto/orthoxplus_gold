"""v6.3.2 S1: the agent's own score on the arithmetic the mainnet validator runs now (upstream taos-im 2564fa5, 0.6.2).

Mainnet publishes gauges only 0.6.2 has.  Its launch values: kappa needs 20 books over the floor (4 before), the skill
leg is scaled by coverage below 62.5% of the field's books, and every de-beta history is keyed on the 600-s sampled
clock and pruned every 60 s.  At the 09-25 seam the validator did not run 0.6.2's history shift: the 129 uids
registered before the old sim's last 3 h score that block plus the new window (corr 1.000), with the inventory carried
over (UID 94's per-book alpha: corr 0.9945).  These tests hold the agent's arithmetic to the upstream functions and to
a validator that never shifted.
"""
import ast
import random
import statistics
import sys
import types
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v632_score_062 as sc  # noqa: E402
import research_v6211_score_logic as sl  # noqa: E402
import research_v62_making_mirror as mm  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000


# ---- upstream 0.6.2 (taos/im/validator/debeta.py @ 2564fa5), verbatim bodies --------------------------------------

def _up_kappa_floored(book_alphas, floor, min_books=4):
    q = [float(x) for x in book_alphas if abs(float(x)) >= floor]
    return sl.kappa_of_alpha(q) if len(q) >= max(4, int(min_books or 4)) else 0.0


def _up_coverage_weight(skill, book_counts, max_inactive, total_books=None):
    if not max_inactive or float(max_inactive) <= 0:
        return list(skill)
    counts = [float(n) for n in book_counts]
    total = float(total_books) if total_books else max(counts) if counts else 0.0
    required = (1.0 - float(max_inactive)) * total
    if required <= 0:
        return list(skill)
    return [float(k) * min(1.0, n / required) if n > 0 else 0.0 for k, n in zip(skill, counts)]


# ---- 1. the skill leg ---------------------------------------------------------------------------------------------

def test_the_floored_kappa_is_upstreams_with_the_20_book_minimum():
    rng = random.Random(7)
    for trial in range(300):
        n = rng.randint(0, 60)
        vals = [rng.gauss(rng.uniform(-20, 20), 40) for _ in range(n)]
        floor = rng.uniform(5, 60)
        for mb in (4, 19, 20, 21):
            assert abs(sc.kappa_floored_062(vals, floor, mb) - _up_kappa_floored(vals, floor, mb)) < 1e-12
    over = [31.0] * 19 + [-40.0]                     # 20 books over the floor: computed; 19: zero
    assert sc.kappa_floored_062(over, 30.0, 20) != 0.0 and sc.kappa_floored_062(over[:19], 30.0, 20) == 0.0
    assert sc.SKILL_MIN_BOOKS == 20 and sc.SKILL_MAX_INACTIVE_BOOKS == 0.375


def test_the_coverage_factor_is_upstreams_coverage_weight_on_a_full_board():
    for n in range(0, 129):
        up = _up_coverage_weight([1.0, 1.0], [n, 128], 0.375)[0]     # someone quotes the whole board
        assert abs(sc.coverage_factor(n) - up) < 1e-12
    assert sc.coverage_factor(80) == 1.0 and sc.coverage_factor(40) == 0.5 and sc.coverage_factor(0) == 0.0
    assert sc.coverage_factor(10, max_inactive=0.0) == 1.0


def test_the_skill_leg_reports_kappa_coverage_and_the_books_over_the_floor():
    alphas = {b: (40.0 if b % 3 else -35.0) for b in range(30)}
    leg = sc.skill_062(alphas, 30.0)
    assert leg["books"] == 30 and leg["books_over_floor"] == 30 and leg["over_floor_positive"] == 20
    assert abs(leg["kappa"] - round(_up_kappa_floored(list(alphas.values()), 30.0, 20), 4)) < 1e-9
    assert abs(leg["coverage_factor"] - 30 / 80) < 1e-4 and abs(leg["skill"] - round(leg["kappa"] * 30 / 80, 4)) < 1e-3
    # UID 94 on 09-25, the clean new-sim window: one book over the floor -> 0, not negative
    assert sc.skill_062({b: (31.5 if b == 0 else 2.0) for b in range(128)}, 30.1)["skill"] == 0.0


# ---- 2. the mirrors on the validator's clock ----------------------------------------------------------------------

def _stream(seed, t0, t1, step, books=4, uid=94):
    """(ts, [(book, [trade dicts])]) states with a random walk per book; uid 94 trades as maker and taker."""
    rng = random.Random(seed)
    price = {b: 100.0 + 5 * b for b in range(books)}
    out = []
    for ts in range(t0, t1, step):
        rows = []
        for b in range(books):
            trades = []
            for _ in range(rng.randint(0, 3)):
                price[b] = round(price[b] + rng.choice((-0.01, 0.0, 0.01)), 2)
                ma = rng.choice((uid, 5, 7, 300)); ta = rng.choice((uid, 5, 7, 300))
                trades.append({"p": price[b], "q": rng.choice((0.25, 0.5, 1.0)), "s": rng.randint(0, 1), "Ma": ma, "Ta": ta, "y": "t"})
            rows.append((b, trades))
        out.append((ts * S, rows))
    return out


class _NoShiftValidator:
    """``update_trade_volumes``' de-beta part for one uid, never shifted: keys on the sampled clock, a flush per update,
    a prune every 60 s at now - lookback on the live clock, inventory and price reference carried across any seam."""

    def __init__(self, uid, lookback_ns, sample_ns=600 * S):
        self.uid, self.lookback, self.sample = uid, lookback_ns, sample_ns
        self.cb, self.cs, self.cbh, self.csh, self.ms = defaultdict(dict), defaultdict(dict), {}, {}, {}
        self.h = {k: {} for k in ("mtm", "invsum", "invn", "drift")}
        self.inv, self.pl = {}, {}
        self.last_prune = None

    def update(self, ts, rows):
        key = ts - ts % self.sample
        for b, trades in rows:
            if not trades:
                continue
            mm.accumulate_book_capture(self.cb, self.cs, b, trades, 15, buy_hist=self.cbh, sell_hist=self.csh, ts=key,
                                       mid_state=self.ms, track={self.uid})
            prev = self.pl.get(b)
            for t in trades:
                if prev is not None:
                    dp = t["p"] - prev
                    if self.inv.get(b):
                        mm._hadd2(self.h["mtm"], self.uid, b, key, self.inv[b] * dp)
                    self.h["drift"].setdefault(b, {}); self.h["drift"][b][key] = self.h["drift"][b].get(key, 0.0) + dp
                buyer, seller = (t["Ta"], t["Ma"]) if t["s"] == 0 else (t["Ma"], t["Ta"])
                if buyer == self.uid: self.inv[b] = self.inv.get(b, 0.0) + t["q"]
                if seller == self.uid: self.inv[b] = self.inv.get(b, 0.0) - t["q"]
                if b in self.inv:
                    mm._hadd2(self.h["invsum"], self.uid, b, key, self.inv[b])
                self.h["invn"].setdefault(b, {}); self.h["invn"][b][key] = self.h["invn"][b].get(key, 0.0) + 1
                prev = t["p"]
            self.pl[b] = prev
        mm.flush_capture_state(self.ms, self.cb, self.cs, 15, buy_hist=self.cbh, sell_hist=self.csh, ts=key, track={self.uid})
        # a clock that went back restarts the prune timer (observed 09-25: the new simulation's own buckets aged out
        # on the new clock from ts ~11,400 while the old block stayed)
        if self.last_prune is None or ts - self.last_prune >= 60 * S or ts < self.last_prune:
            th = ts - self.lookback
            mm.prune_hist_2level(self.cbh, self.cb, th); mm.prune_hist_2level(self.csh, self.cs, th)
            for name in ("mtm", "invsum"):
                mm.prune_hist_2level(self.h[name], {}, th)
            for name in ("invn", "drift"):
                for b in list(self.h[name]):
                    self.h[name][b] = {k: v for k, v in self.h[name][b].items() if k >= th}
            self.last_prune = ts

    def alphas_and_making(self):
        u = self.uid
        buy = {b: sum(d.values()) for b, d in self.cbh.get(u, {}).items()}
        sell = {b: sum(d.values()) for b, d in self.csh.get(u, {}).items()}
        traded = set(buy) | set(sell)
        out = {}
        for b in traded:
            n = sum(self.h["invn"].get(b, {}).values())
            if n <= 0:
                continue
            mtm = sum(self.h["mtm"].get(u, {}).get(b, {}).values()); inv = sum(self.h["invsum"].get(u, {}).get(b, {}).values())
            out[b] = mtm - inv / n * sum(self.h["drift"].get(b, {}).values())
        return out, sc.making_per_book(buy, sell)


def _feed(mirrors, stream):
    for ts, rows in stream:
        for m in mirrors:
            m.ingest_state(ts, rows) if not isinstance(m, _NoShiftValidator) else m.update(ts, rows)


def test_the_mirrors_on_the_sampled_clock_are_the_validators_window():
    look = 1800 * S
    alpha = sl.OwnAlphaMirror(94, lookback_ns=look, bucket_ns=600 * S)
    making = mm.MakingMirror(94, lookback_ns=look, sample_ns=600 * S, prune_every_ns=60 * S)
    ref = _NoShiftValidator(94, look)
    _feed([alpha, making, ref], _stream(1, 10_000, 14_000, 2))
    want_a, want_m = ref.alphas_and_making()
    got_a = alpha.book_alphas()
    assert set(got_a) == set(want_a) and all(abs(got_a[b] - want_a[b]) < 1e-9 for b in want_a)
    assert abs(making.making() - want_m) < 1e-9
    assert set(making.buy_hist.get(94, {}).get(0, {})) <= {k for k in range(0, 14_000 * S, 600 * S)}   # sampled keys


def test_the_making_mirror_prunes_on_the_validators_60_s_cadence():
    m = mm.MakingMirror(94, lookback_ns=1_800 * S, sample_ns=600 * S, prune_every_ns=60 * S)
    trade = [{"p": 100.0, "q": 1.0, "s": 1, "Ma": 94, "Ta": 5, "y": "t"}]
    m.ingest_state(0, [(0, trade * 40)])                           # captures on key 0 (finalised on the next bucket)
    m.ingest_state(600 * S, [(0, trade * 40)])
    assert 0 in m.buy_hist.get(94, {}).get(0, {})
    m.ingest_state(1_790 * S, [])                                  # prune at 1,790: threshold -10 s keeps key 0
    m.ingest_state(1_820 * S, [])                                  # 30 s later: no prune yet, key 0 stays
    assert 0 in m.buy_hist.get(94, {}).get(0, {}) and m.last_prune_ts == 1_790 * S
    m.ingest_state(1_851 * S, [])                                  # 61 s after the last prune: threshold 51 s drops it
    assert 0 not in m.buy_hist.get(94, {}).get(0, {}) and m.last_prune_ts == 1_851 * S


def test_the_v620_and_v6211_defaults_are_unchanged():
    m = mm.MakingMirror(94)
    assert m.sample_ns == 0 and m.prune_every_ns == 0 and m.keep_seam is False and m.seam_saved is None
    a = sl.OwnAlphaMirror(94)
    assert a.bucket_ns == sl.BUCKET_NS and a.keep_seam is False and a.seam_saved is None


def test_a_seam_keeps_the_discarded_window_and_every_seam_is_counted():
    look = 1800 * S
    alpha = sl.OwnAlphaMirror(94, lookback_ns=look, bucket_ns=600 * S, keep_seam=True)
    making = mm.MakingMirror(94, lookback_ns=look, sample_ns=600 * S, prune_every_ns=60 * S, keep_seam=True)
    old = _stream(2, 10_000, 14_000, 2)
    _feed([alpha, making], old)
    before = {k: dict(getattr(alpha, k)) for k in ("mtm", "invsum", "invn", "drift", "fills")}
    inv, buy = dict(alpha.inv), dict(making.buy_sums.get(94) or {})
    _feed([alpha, making], _stream(3, 100, 110, 2))
    assert alpha.rebases == 1 and making.rebases == 1
    assert alpha.seam_saved["sums"] == before and alpha.seam_saved["inventory"] == inv and making.seam_saved["buy"] == buy
    assert alpha.seam_saved["ts"] == old[-1][0]
    _feed([alpha, making], _stream(4, 20_000, 20_010, 2) + _stream(5, 100, 110, 2))
    assert alpha.rebases == 2 and making.rebases == 2                 # counted every time (it used to stick at 1)


def test_the_validator_view_is_a_validator_that_never_shifted():
    look = 1800 * S
    alpha = sl.OwnAlphaMirror(94, lookback_ns=look, bucket_ns=600 * S, keep_seam=True)
    making = mm.MakingMirror(94, lookback_ns=look, sample_ns=600 * S, prune_every_ns=60 * S, keep_seam=True)
    ref = _NoShiftValidator(94, look)
    _feed([alpha, making, ref], _stream(6, 10_000, 14_000, 2))
    blocks = []
    # the new simulation runs past the lookback plus one bucket, so its first bucket (the seam's price jump and any
    # capture finalised across the seam) has left the window
    new = _stream(7, 100, 100 + 1800 + 1300, 2)
    _feed([alpha, making, ref], new[:1])
    blocks.append(sc.SeamBlock(sums=alpha.seam_saved["sums"], buy=making.seam_saved["buy"], sell=making.seam_saved["sell"],
                               inventory=alpha.seam_saved["inventory"], carried_in=sc.carried(blocks), ts=alpha.seam_saved["ts"]))
    _feed([alpha, making, ref], new[1:])
    got_a, got_m = sc.validator_view(blocks, sc.alpha_sums(alpha), dict(making.buy_sums.get(94) or {}), dict(making.sell_sums.get(94) or {}))
    want_a, want_m = ref.alphas_and_making()
    assert set(got_a) == set(want_a)
    assert max(abs(got_a[b] - want_a[b]) for b in want_a) < 1e-6
    assert abs(got_m - want_m) < 1e-6
    # and the clean window is not what that validator scores
    clean = alpha.book_alphas()
    assert any(abs(clean.get(b, 0.0) - want_a[b]) > 1e-3 for b in want_a)
    assert sum(abs(v) for v in sc.carried(blocks).values()) > 0


def test_blocks_carry_what_they_inherited():
    b1 = sc.SeamBlock(sums={}, buy={}, sell={}, inventory={1: 2.0, 2: -1.0}, carried_in={}, ts=1)
    b2 = sc.SeamBlock(sums={}, buy={}, sell={}, inventory={1: 0.5}, carried_in=sc.carried([b1]), ts=2)
    assert sc.carried([b1, b2]) == {1: 2.5, 2: -1.0} and b2.carried_in == {1: 2.0, 2: -1.0}
    assert sc.making_per_book({1: 3.0, 2: -1.0}, {1: 1.0, 2: 5.0}) == 0.0 and sc.making_per_book({1: 3.0}, {1: 1.0}) == 2.0


# ---- 3. the agent side --------------------------------------------------------------------------------------------

class _Agent:
    uid = 94

    def __init__(self):
        self.research_v632_score_062 = True
        self.research_v63_alpha_floor = 30.0
        self._v632_blocks, self._v632_seen_seams, self._v632_counts, self._v632_errors = [], 0, {}, 0
        self.emitted = []
        self._tick = 5

    def _emit(self, event_type, force=False, **payload):
        self.emitted.append((event_type, payload))


def _agent():
    ns = {"V632SeamBlock": sc.SeamBlock, "V632_SCORE_062_VERSION": sc.V632_SCORE_062_VERSION,
          "v632_carried": sc.carried, "v632_skill_062": sc.skill_062, "v632_making_per_book": sc.making_per_book,
          "v632_validator_view": sc.validator_view, "v632_alpha_sums": sc.alpha_sums,
          "V63_ALPHA_FLOOR_DEFAULT": 18.0, "Any": object}
    cls = type("A", (_Agent,), {})
    for name in ("_v632_note_seam", "_v632_score_snapshot", "_v632_count"):
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]), "<v632>", "exec"), ns)
        setattr(cls, name, ns[name])
    return cls()


def test_each_seam_becomes_one_block_and_the_snapshot_reports_both_views():
    look = 1800 * S
    agent = _agent()
    agent._v6211_mirror = sl.OwnAlphaMirror(94, lookback_ns=look, bucket_ns=600 * S, keep_seam=True)
    agent._v62_mirror = mm.MakingMirror(94, lookback_ns=look, sample_ns=600 * S, prune_every_ns=60 * S, keep_seam=True)
    mirrors = [agent._v6211_mirror, agent._v62_mirror]
    _feed(mirrors, _stream(8, 10_000, 12_000, 2))
    agent._v632_note_seam()
    snap = agent._v632_score_snapshot()
    assert snap["blocks"] == 0 and "validator" not in snap and snap["clean"]["floor"] == 30.0
    _feed(mirrors, _stream(9, 100, 400, 2))
    agent._v632_note_seam(); agent._v632_note_seam()          # once per seam, however often it is asked
    assert len(agent._v632_blocks) == 1 and agent._v632_counts == {"seam_blocks": 1}
    assert [e for e, _ in agent.emitted] == ["V632_SEAM_BLOCK"]
    snap = agent._v632_score_snapshot()
    assert snap["blocks"] == 1 and "validator" in snap and snap["validator"]["carried_abs"] >= 0.0
    _feed(mirrors, _stream(10, 20_000, 20_200, 2) + _stream(11, 100, 300, 2))
    for _ in range(2):
        agent._v632_note_seam()
    assert len(agent._v632_blocks) == 2 and agent._v632_blocks[1].carried_in == sc.carried(agent._v632_blocks[:1])
    agent.research_v632_score_062 = False
    assert agent._v632_score_snapshot() == {"version": sc.V632_SCORE_062_VERSION, "on": 0}


# ---- 4. wiring -----------------------------------------------------------------------------------------------------

def test_the_switch_defaults_on_ships_in_params_and_the_mirrors_run_on_the_validators_clock():
    assert 'self.research_v632_score_062 = self._as_bool(getattr(self.config, "research_v632_score_062", True))' in SIMPLE
    feed = _simple("_v62_feed_mirror")
    assert "sample_ns=V632_SAMPLE_NS" in feed and "prune_every_ns=V632_PRUNE_EVERY_NS, keep_seam=True)" in feed
    feed = _simple("_v6211_feed_mirror")
    assert "bucket_ns=V632_SAMPLE_NS, keep_seam=True)" in feed
    upd = _simple("update")
    assert upd.index("self._v62_feed_mirror(state)") < upd.index("self._v6211_feed_mirror(state)") < upd.index("self._v632_note_seam()")
    assert "research_v632_score_062=1" in LAUNCHER and "[preflight] v6.3.2 score 0.6.2 PASS" in LAUNCHER
    assert "tests/test_research_v6_3_2_score_062.py" in LAUNCHER
    tele = _simple("_v62_telemetry")
    assert "score_062_on=" in tele and "score_062=self._v632_score_snapshot()" in tele
    import research_v632_target_gate as tg
    assert sc.SAMPLE_NS == tg.SAMPLE_NS == 600 * S and sc.PRUNE_EVERY_NS == sl.PRUNE_EVERY_NS
