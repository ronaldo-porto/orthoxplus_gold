"""v6.2.0: the making mirror reproduces the validator's capture arithmetic for our uid.

The oracle is the validator's own code, verbatim (tests/_upstream_debeta_0234998.py, taos-im/sn-79
main 0234998 "0.6.1 rung 2").  The mirror narrows attribution to the tracked uid and must otherwise
be bit-identical: same centred mid over the same prints, same pending/finalisation rule, same
window pruning, same per-book 2·min(buy, sell).
"""
import random
import sys
import types
from collections import defaultdict
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _upstream_debeta_0234998 as up  # noqa: E402
import research_v62_making_mirror as mm  # noqa: E402

UID = 82
NS = 10**9


def _trades(rng, n, *, price=200.0, our_share=0.3, self_trade=False):
    out = []
    for _ in range(n):
        p = round(price + rng.uniform(-0.5, 0.5), 2)
        q = rng.choice([0.25, 0.5, 1.0])
        s = rng.choice([0, 1])
        r = rng.random()
        if r < our_share:
            ma, ta = (UID, -rng.randint(1, 3000)) if rng.random() < 0.8 else (-rng.randint(1, 3000), UID)
        elif r < our_share + 0.1:
            ma, ta = rng.randint(100, 250), rng.randint(100, 250)
        else:
            ma, ta = -rng.randint(1, 3000), -rng.randint(1, 3000)
        if self_trade and rng.random() < 0.2:
            ta = ma
        out.append({"p": p, "q": q, "s": s, "Ma": ma, "Ta": ta})
    return out


class _Oracle:
    """The validator's live loop for every uid, driven exactly like the mirror."""

    def __init__(self, lookback_ns):
        self.buy = defaultdict(dict)
        self.sell = defaultdict(dict)
        self.buy_hist = {}
        self.sell_hist = {}
        self.mid = {}
        self.lookback = lookback_ns

    def state(self, ts, books):
        for b, trades in books:
            if trades:
                up.accumulate_book_capture(self.buy, self.sell, b, trades, mm.CAPTURE_W,
                                           buy_hist=self.buy_hist, sell_hist=self.sell_hist, ts=ts,
                                           mid_state=self.mid, flush_ns=mm.CAPTURE_FLUSH_NS)
        up.flush_capture_state(self.mid, self.buy, self.sell, mm.CAPTURE_W,
                               buy_hist=self.buy_hist, sell_hist=self.sell_hist, ts=ts,
                               flush_ns=mm.CAPTURE_FLUSH_NS)
        up.prune_hist_2level(self.buy_hist, self.buy, ts - self.lookback)
        up.prune_hist_2level(self.sell_hist, self.sell, ts - self.lookback)

    def making(self):
        return up.balanced_reward_per_book(self.buy, self.sell, [UID]).get(UID, 0.0)


@pytest.mark.parametrize("seed", [1, 2, 3, 4, 5])
def test_mirror_is_bit_identical_to_the_validator_for_our_uid(seed):
    rng = random.Random(seed)
    lookback = 600 * NS                      # a short window so pruning is exercised
    mirror = mm.MakingMirror(UID, lookback_ns=lookback)
    oracle = _Oracle(lookback)
    ts = 48_811 * NS
    for step in range(900):
        ts += NS
        books = [(b, _trades(rng, rng.randint(0, 9), price=200.0 + 10 * b, self_trade=True)) for b in range(4)]
        # a quiet book now and then: no trades at all, so only the flush touches it
        if step % 7 == 0:
            books[2] = (2, [])
        mirror.ingest_state(ts, books)
        oracle.state(ts, books)
        assert mirror.buy_sums.get(UID, {}) == oracle.buy.get(UID, {}), step
        assert mirror.sell_sums.get(UID, {}) == oracle.sell.get(UID, {}), step
        assert mirror.buy_hist.get(UID, {}) == oracle.buy_hist.get(UID, {}), step
        assert mirror.making() == oracle.making(), step
    assert mirror.making() > 0.0 or oracle.making() == 0.0
    # only the tracked uid is carried
    assert set(mirror.buy_sums) <= {UID} and set(mirror.sell_sums) <= {UID}
    assert set(mirror.buy_hist) <= {UID} and set(mirror.sell_hist) <= {UID}


def test_capture_sign_and_two_sidedness():
    """Buying below the centred mid earns the buyer, selling above earns the seller; one side alone
    scores nothing on the book."""
    mirror = mm.MakingMirror(UID, lookback_ns=mm.DEFAULT_LOOKBACK_NS)
    ts = 1_000 * NS
    prints = [{"p": 200.00, "q": 1.0, "s": 0, "Ma": -1, "Ta": -2} for _ in range(20)]
    ours_buy = {"p": 199.90, "q": 0.25, "s": 1, "Ma": UID, "Ta": -7}       # maker buys at 199.90
    mirror.ingest_state(ts, [(0, prints[:5] + [ours_buy] + prints[5:])])
    # window: the 5 prints before, the fill, the 15 after -> 21 prints, 20 of them at 200.00
    assert mirror.buy_sums[UID][0] == pytest.approx((200.0 * 20 / 21 + 199.90 / 21 - 199.90) * 0.25)
    assert mirror.making() == 0.0                       # one side only
    ours_sell = {"p": 200.10, "q": 0.25, "s": 0, "Ma": UID, "Ta": -8}      # maker sells at 200.10
    mirror.ingest_state(ts + NS, [(0, prints[:5] + [ours_sell] + prints[5:])])
    assert mirror.sell_sums[UID][0] > 0.0
    assert mirror.making() == pytest.approx(2.0 * min(mirror.buy_sums[UID][0], mirror.sell_sums[UID][0]))


def test_a_fill_waits_for_its_forward_prints_or_the_flush():
    mirror = mm.MakingMirror(UID, lookback_ns=mm.DEFAULT_LOOKBACK_NS)
    ts = 1_000 * NS
    ours = {"p": 199.90, "q": 0.25, "s": 1, "Ma": UID, "Ta": -7}
    mirror.ingest_state(ts, [(0, [ours])])
    assert UID not in mirror.buy_sums                    # pending: no forward prints yet
    assert mirror.snapshot()["pending_fills"] == 1
    mirror.ingest_state(ts + 61 * NS, [(0, [])])         # 60 sim-s later the flush books it
    assert mirror.buy_sums[UID][0] == pytest.approx(0.0)  # the only print is its own: mid == price
    assert mirror.snapshot()["pending_fills"] == 0


def test_self_trades_earn_nothing_but_shape_the_mid():
    mirror = mm.MakingMirror(UID, lookback_ns=mm.DEFAULT_LOOKBACK_NS)
    ts = 1_000 * NS
    wash = {"p": 210.0, "q": 1.0, "s": 0, "Ma": UID, "Ta": UID}
    ours = {"p": 200.0, "q": 0.25, "s": 1, "Ma": UID, "Ta": -7}
    others = [{"p": 200.0, "q": 1.0, "s": 0, "Ma": -1, "Ta": -2} for _ in range(20)]
    mirror.ingest_state(ts, [(0, [wash] + others[:5] + [ours] + others[5:])])
    assert mirror.making() == 0.0
    # the wash print at 210 sits inside the 15-print backward window of our fill and lifts the mid
    assert mirror.buy_sums[UID][0] > 0.0


def test_window_pruning_matches_the_validator():
    rng = random.Random(11)
    lookback = 100 * NS
    mirror = mm.MakingMirror(UID, lookback_ns=lookback)
    oracle = _Oracle(lookback)
    ts = 5_000 * NS
    for _ in range(300):
        ts += NS
        books = [(0, _trades(rng, 6, our_share=0.5))]
        mirror.ingest_state(ts, books)
        oracle.state(ts, books)
    assert mirror.buy_hist.get(UID, {}) == oracle.buy_hist.get(UID, {})
    assert all(t >= ts - lookback for d in mirror.buy_hist.get(UID, {}).values() for t in d)
    assert mirror.making() == oracle.making()


def test_a_clock_that_goes_back_a_simulation_resets_the_mirror():
    mirror = mm.MakingMirror(UID, lookback_ns=mm.DEFAULT_LOOKBACK_NS)
    rng = random.Random(3)
    ts = 80_000 * NS
    for _ in range(50):
        ts += NS
        mirror.ingest_state(ts, [(0, _trades(rng, 8, our_share=0.5))])
    assert mirror.fills > 0
    mirror.ingest_state(10 * NS, [(0, [])])              # new simulation: the clock restarts near 0
    assert mirror.rebases == 1 and mirror.fills == 0 and mirror.making() == 0.0
    mirror.ingest_state(10 * NS - 36 * NS, [(0, [])])    # a 36 s checkpoint rewind is not a rebase
    assert mirror.rebases == 1


def test_trade_dict_accepts_models_and_dicts_and_rejects_the_rest():
    obj = types.SimpleNamespace(y="t", p=204.33, q=0.25, s=1, Ma=82, Ta=-1570)
    assert mm.trade_dict(obj) == {"p": 204.33, "q": 0.25, "s": 1, "Ma": 82, "Ta": -1570}
    assert mm.trade_dict({"y": "t", "p": "204.33", "q": 0.25, "s": 0, "Ma": None, "Ta": 5}) == \
        {"p": 204.33, "q": 0.25, "s": 0, "Ma": -1, "Ta": 5}
    assert mm.trade_dict(types.SimpleNamespace(y="o", p=1, q=1, s=0)) is None       # an order event
    assert mm.trade_dict({"y": "c"}) is None                                          # a cancellation
    assert mm.trade_dict({"p": None, "q": 1, "s": 0}) is None
    assert mm.trade_dict({"p": 1.0, "q": 0.0, "s": 0}) is None


def test_snapshot_shape():
    mirror = mm.MakingMirror(UID, lookback_ns=mm.DEFAULT_LOOKBACK_NS)
    snap = mirror.snapshot()
    assert snap["v62_making_version"] == mm.V62_MAKING_VERSION
    for key in ("making", "books_with_fills", "books_two_sided", "books_positive", "buy_capture",
                "sell_capture", "pending_fills", "states", "prints", "fills", "rebases", "mirror_ms"):
        assert key in snap
    assert snap["making"] == 0.0 and snap["states"] == 0


def test_vendored_functions_match_the_oracle_text():
    """The capture arithmetic is the validator's, function for function; only `track` was added."""
    import ast, inspect
    for name in ("_hadd2", "prune_hist_2level", "centered_mid", "balanced_reward_per_book"):
        ours = ast.dump(ast.parse(inspect.getsource(getattr(mm, name))).body[0].body[-1])
        theirs = ast.dump(ast.parse(inspect.getsource(getattr(up, name))).body[0].body[-1])
        assert ours == theirs, name
