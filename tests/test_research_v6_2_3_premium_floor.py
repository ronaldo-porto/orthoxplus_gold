"""v6.2.3: the v6.1 floor binds only where it protects the validator's no-loss premium.

Testnet UID 82: v6.2.1 (2026-09-19 00:22-10:53) went from 51 quoted books to ~10 as lots reached
their floor and never came back (36 of 87 floor hits; the 38 stuck books ended p50 1,723 bps from
break-even); v6.2.2 repeated it (quoted 24 -> 15, held 41 -> 52 in 500 ticks).

The validator's per-book Kappa-3 (tau 0, min_realized_observations 3, 3 sim-h lookback) pays a
clean record a premium; one loss puts the book at ~0.50 whatever its size; fewer than 3 non-zero
periods leave it unscored.  The PnL leg is ~0 at our size.  So the floor is kept on a PREMIUM book
(>= 3 periods, none below tau) and lifted everywhere else, for maker exits only.  Off restores v6.2.2.
"""
import math
import random
import statistics
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v623_premium_floor as pf  # noqa: E402
from research_direct_exit import DIRECT_MAKER_EXIT_TARGET_BPS  # noqa: E402
from research_v61_lot_floor import fifo_close_net_bps  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
RESEARCH = (STRATEGY / "Strategy1_Research.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
MODULE = (STRATEGY / "research_v623_premium_floor.py").read_text()
_method_source = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000


# ------------------------------------------------------------------------------------------------
# 1. The validator arithmetic the rule rests on: kappa_3's per-book branch, in pure Python
# ------------------------------------------------------------------------------------------------

N = 10_800   # one state per sim-second over the 3 sim-h lookback


def _kappa(entries, n=N, tau=0.0, min_obs=3):
    x = [0.0] * n
    for i, v in entries:
        x[i] += v
    med = statistics.median(x)
    mad = max(statistics.median([abs(v - med) for v in x]), 1e-6)
    r = [v / mad for v in x]
    if sum(1 for v in r if v != 0.0) < min_obs:
        return None
    mean = sum(r) / n
    lpm3 = sum(max(tau - v, 0.0) ** 3 for v in r) / n
    std = math.sqrt(sum((v - mean) ** 2 for v in r) / n)
    reg = ((abs(mean) + std) * 0.1) ** 3
    raw = (mean - tau) / (lpm3 + reg) ** (1.0 / 3.0)
    return min(1.0, max(0.0, (raw + 2.5) / 5.0))


def _gains(k, seed):
    rnd = random.Random(seed)
    return [(i, 0.05 * (0.5 + rnd.random())) for i in rnd.sample(range(N), k)]


def test_a_clean_record_carries_a_premium_that_grows_with_closes():
    k3, k45, k180 = (_kappa(_gains(k, 7)) for k in (3, 45, 180))
    assert 0.52 < k3 < k45 < k180
    assert k45 > 0.60 and k180 > 0.70


def test_one_large_loss_puts_any_book_at_the_loss_floor_whatever_its_size():
    for k in (3, 45, 180):
        assert 0.499 < _kappa(_gains(k, 11) + [(5, -40.0)]) < 0.5005
    assert _kappa([(1, -1.0), (2, -2.0), (3, -3.0)]) < 0.5


def test_fewer_than_three_periods_is_not_scored():
    assert _kappa([(5, -3.0)]) is None
    assert _kappa([(1, 0.05), (2, -0.02)]) is None


# ------------------------------------------------------------------------------------------------
# 2. The census on the validator's bookkeeping
# ------------------------------------------------------------------------------------------------

def test_the_validator_constants():
    assert pf.KAPPA_TAU == 0.0 and pf.KAPPA_MIN_REALIZED_OBSERVATIONS == 3
    assert pf.PUBLISH_STEP_NS == S and pf.VOLUME_DECIMALS == 4


def test_a_trade_belongs_to_the_state_that_reports_it():
    assert pf.state_bucket(61_500 * S) == 61_500 * S
    assert pf.state_bucket(61_500 * S + 1) == 61_501 * S
    assert pf.state_bucket(61_501 * S - 1) == 61_501 * S


def test_census_sums_per_state_rounds_the_sum_and_drops_zeros():
    now = 20_000 * S
    events = {
        7: [(now - 10 * S + 1, 0.3), (now - 10 * S + 2, -0.3),   # one state, nets to zero: no period
            (now - 8 * S, 0.00004),                               # rounds to 0 at 4 decimals: none
            (now - 3 * S, 0.2), (now - 2 * S, -0.1), (now - 1 * S, 0.05)],
        9: [(now - 20_000 * S, 5.0)],                             # outside the lookback
        "x": [(now, 1.0)],                                        # unreadable book id
    }
    census = pf.window_census(events, now=now, lookback_ns=10_800 * S)
    assert set(census) == {7}
    assert census[7].observations == 3 and census[7].negatives == 1
    assert abs(census[7].realized_sum - 0.15) < 1e-12


def test_status_precedence_follows_kappa_3():
    W = pf.BookWindow
    assert pf.book_status(None) == pf.BOOK_EMPTY
    assert pf.book_status(W(0, 0, 0.0)) == pf.BOOK_EMPTY
    assert pf.book_status(W(2, 1, -0.3)) == pf.BOOK_THIN          # unscored even with a loss
    assert pf.book_status(W(2, 0, 0.3)) == pf.BOOK_THIN
    assert pf.book_status(W(3, 1, 0.1)) == pf.BOOK_LOSS
    assert pf.book_status(W(40, 0, 2.0)) == pf.BOOK_PREMIUM
    assert pf.floor_applies(pf.BOOK_PREMIUM)
    assert not any(pf.floor_applies(s) for s in (pf.BOOK_LOSS, pf.BOOK_THIN, pf.BOOK_EMPTY))


def test_census_counts_cover_the_universe():
    W = pf.BookWindow
    census = {1: W(5, 0, 1.0), 2: W(4, 1, 0.2), 3: W(1, 0, 0.1)}
    counts = pf.census_counts(census, [1, 2, 3, 4, "5"])
    assert counts == {pf.BOOK_PREMIUM: 1, pf.BOOK_LOSS: 1, pf.BOOK_THIN: 1, pf.BOOK_EMPTY: 2}


# ------------------------------------------------------------------------------------------------
# 3. The helpers in a harness
# ------------------------------------------------------------------------------------------------

class _Base:
    def __init__(self, on=True, v61=True, breadth=True, events=None):
        self.research_v62_breadth = breadth
        self.research_v61_no_loss = v61
        self.research_v623_premium_floor = on
        self.research_v626_loss_budget = False   # v6.2.6 has its own suite
        self._research_realized_pnl_events_by_book = dict(events or {})
        self._research_realized_generation = 0
        self.research_kappa_lookback_ns = 10_800 * S
        self._tick = 7
        self.rows = []

    def _emit(self, kind, **kw):
        self.rows.append((kind, kw))

    def _research_live_fee_bps(self, book_id, is_maker=True):
        return 2.0


_ns = {
    "_Base": _Base, "Any": object, "DIRECT_MAKER_EXIT_TARGET_BPS": DIRECT_MAKER_EXIT_TARGET_BPS,
    "V623_BOOK_PREMIUM": pf.BOOK_PREMIUM, "V623_MIN_OBSERVATIONS": pf.KAPPA_MIN_REALIZED_OBSERVATIONS,
    "V623_PUBLISH_STEP_NS": pf.PUBLISH_STEP_NS, "V623_VOLUME_DECIMALS": pf.VOLUME_DECIMALS,
    "V623_PREMIUM_FLOOR_VERSION": pf.V623_PREMIUM_FLOOR_VERSION,
    "v623_window_census": pf.window_census, "v623_book_status": pf.book_status,
    "v623_census_counts": pf.census_counts, "v61_fifo_close_net_bps": fifo_close_net_bps,
}
exec("class Harness(_Base):\n    pass\n", _ns)
for _name in ("_v61_on", "_v62_on", "_v623_on", "_v623_count", "_v623_census", "_v623_lifted",
              "_v623_release", "_v623_resting_floor_bps", "_v623_snapshot",
              "_v626_loss_budget_on"):
    exec("class Harness(Harness):\n" + textwrap.indent(textwrap.dedent(_method_source(_name)), "    "), _ns)
Harness = _ns["Harness"]

NOW = 20_000 * S
EVENTS = {
    1: [(NOW - 30 * S, 0.1), (NOW - 20 * S, 0.1), (NOW - 10 * S, 0.1)],    # premium
    2: [(NOW - 30 * S, 0.1), (NOW - 20 * S, -0.4), (NOW - 10 * S, 0.1)],   # loss in window
    3: [(NOW - 30 * S, 0.1)],                                                # thin
}


def _state(now=NOW, books=4):
    return types.SimpleNamespace(
        timestamp=now, config=types.SimpleNamespace(publish_interval=S, volumeDecimals=4),
        books={b: None for b in range(1, books + 1)},
    )


def test_the_floor_is_kept_on_premium_and_lifted_elsewhere():
    h = Harness(events=EVENTS)
    st = _state()
    assert h._v623_lifted(1, st) == (False, pf.BOOK_PREMIUM)
    assert h._v623_lifted(2, st) == (True, pf.BOOK_LOSS)
    assert h._v623_lifted(3, st) == (True, pf.BOOK_THIN)
    assert h._v623_lifted(4, st) == (True, pf.BOOK_EMPTY)


def test_off_or_prerequisites_off_keeps_every_floor_and_reads_nothing():
    for h in (Harness(on=False, events=EVENTS), Harness(v61=False, events=EVENTS),
              Harness(breadth=False, events=EVENTS)):
        assert h._v623_lifted(4, _state()) == (False, "")
        assert getattr(h, "_v623_memo", None) is None
        assert h._v623_resting_floor_bps(4, _state()) == float(DIRECT_MAKER_EXIT_TARGET_BPS)


def test_no_clock_keeps_the_floor():
    h = Harness(events=EVENTS)
    assert h._v623_lifted(4, types.SimpleNamespace(timestamp=0, config=None, books={})) == (False, "")


def test_the_census_is_one_pass_per_state_and_follows_new_fills():
    h = Harness(events=EVENTS)
    st = _state()
    first = h._v623_census(st)
    assert h._v623_census(st) is first
    h._research_realized_pnl_events_by_book = {**EVENTS, 4: [(NOW - 5 * S, -0.2)]}
    h._research_realized_generation += 1
    again = h._v623_census(st)
    assert again is not first and 4 in again


def test_the_release_row_is_once_per_book_and_lot():
    h = Harness(events=EVENTS)
    st = _state()
    lot = (NOW - 40 * S, 0.25, 300.0, 0.0)
    kw = dict(lot=lot, floor=300.1, close_price=298.0, long_pos=True, qty=0.25, action="COMPETITIVE_MAKER_EXIT")
    assert h._v623_release(1, st, **kw) is False                 # premium: the floor stands
    assert h._v623_release(2, st, **kw) is True
    assert h._v623_release(2, st, **kw) is True                   # same lot: counted, no new row
    rows = [r for r in h.rows if r[0] == "V623_RELEASE"]
    assert len(rows) == 1
    row = rows[0][1]
    assert row["book"] == 2 and row["status"] == pf.BOOK_LOSS and row["floor_price"] == 300.1
    assert row["requested_net_bps"] < 0 and row["window"]["negatives"] == 1
    h._v623_release(2, st, **dict(kw, lot=(NOW - 1 * S, 0.25, 297.0, 0.0)))   # a new lot
    assert len([r for r in h.rows if r[0] == "V623_RELEASE"]) == 2
    assert h._v623_counts["lifted_placements"] == 3 and h._v623_counts["releases"] == 2
    assert h._v623_counts["lifted_loss_in_window"] == 3


def test_the_classifier_bound_is_lifted_with_the_floor():
    h = Harness(events=EVENTS)
    assert h._v623_resting_floor_bps(1, _state()) == float(DIRECT_MAKER_EXIT_TARGET_BPS)
    assert h._v623_resting_floor_bps(2, _state()) == float("-inf")


def test_the_snapshot_counts_the_universe():
    h = Harness(events=EVENTS)
    snap = h._v623_snapshot(_state(books=4))
    assert snap["books"] == {pf.BOOK_PREMIUM: 1, pf.BOOK_LOSS: 1, pf.BOOK_THIN: 1, pf.BOOK_EMPTY: 1}
    assert snap["errors"] == 0
    assert "books" not in Harness(on=False, events=EVENTS)._v623_snapshot(_state())


# ------------------------------------------------------------------------------------------------
# 4. The real v6.1 placement floor with v6.2.3 on: lifted keeps the chooser's price, premium floors
# ------------------------------------------------------------------------------------------------

import research_v61_lot_floor as lf  # noqa: E402

_floor_ns = dict(_ns)
_floor_ns.update({
    "DIRECT_MAKER_EXIT_TARGET_BPS": DIRECT_MAKER_EXIT_TARGET_BPS,
    "V61_LOT_FLOOR_VERSION": lf.V61_LOT_FLOOR_VERSION,
    "v61_floor_price": lf.floor_price, "v61_floored_close_price": lf.floored_close_price,
    "v61_head_lot": lf.head_lot, "v61_fifo_close_net_bps": lf.fifo_close_net_bps,
})
exec("class FloorHarness(_Base):\n    pass\n", _floor_ns)
for _name in ("_v61_on", "_v61_count", "_v61_price_decimals", "_v61_positions", "_v61_floor_for",
              "_v61_apply_floor", "_v611_floor_reprice_on", "_v611_count", "_v611_seed_comparand",
              "_v62_on", "_v623_on", "_v623_count", "_v623_census", "_v623_lifted", "_v623_release",
              "_v623_resting_floor_bps", "_v623_snapshot", "_v626_loss_budget_on"):
    exec("class FloorHarness(FloorHarness):\n"
         + textwrap.indent(textwrap.dedent(_method_source(_name)), "    "), _floor_ns)
FloorHarness = _floor_ns["FloorHarness"]


def _floor_agent(on=True):
    a = FloorHarness(on=on, events=EVENTS)
    # one long lot of 0.25 at 300.00 on every book; the chooser asks 298.00 (the touch after a drop)
    a._open_positions = {b: {"longs": [(NOW - 40 * S, 0.25, 300.0, 0.0)], "shorts": []} for b in range(1, 5)}
    return a


class _Inv:
    net_base = 0.25


def _state_with_decimals():
    st = _state()
    st.config.priceDecimals = 2
    return st


def test_a_lifted_book_keeps_the_chooser_price_and_a_premium_book_is_floored():
    a = _floor_agent()
    st = _state_with_decimals()
    price, net, floored = a._v61_apply_floor(2, st, _Inv(), 0.25, 298.0, "COMPETITIVE_MAKER_EXIT")
    assert (price, net, floored) == (298.0, None, False)
    assert [r[0] for r in a.rows] == ["V623_RELEASE"]
    price, net, floored = a._v61_apply_floor(1, st, _Inv(), 0.25, 298.0, "COMPETITIVE_MAKER_EXIT")
    assert floored is True and price > 300.0 and net > 0.0
    assert "V61_EXIT_FLOORED" in [r[0] for r in a.rows]
    # the classifier comparand agrees with the placement on both books
    assert a._v611_seed_comparand(2, st, 298.0, long_position=True) == 298.0
    assert a._v611_seed_comparand(1, st, 298.0, long_position=True) == price


def test_with_the_switch_off_every_book_is_floored_as_in_v6_2_2():
    a = _floor_agent(on=False)
    st = _state_with_decimals()
    for book in (1, 2, 3, 4):
        price, net, floored = a._v61_apply_floor(book, st, _Inv(), 0.25, 298.0, "COMPETITIVE_MAKER_EXIT")
        assert floored is True and price > 300.0
    assert "V623_RELEASE" not in [r[0] for r in a.rows]


# ------------------------------------------------------------------------------------------------
# 5. Wiring: the five sites that read the floor, the taker path untouched, restarts, launcher
# ------------------------------------------------------------------------------------------------

def test_placement_floor_is_scoped_after_the_no_change_return():
    src = _method_source("_v61_apply_floor")
    hook = src.index("if self._v623_release(")
    assert src.index("return close_price, None, False", src.index("priced = v61_floored_close_price")) < hook
    assert hook < src.index('self._v61_count("floored_placements")')


def test_classifier_comparand_and_bound_are_lifted_together():
    seed = _method_source("_v611_seed_comparand")
    assert seed.index("if self._v623_lifted(int(book_id), state)[0]:") < seed.index("floor = self._v61_floor_for(")
    decide = _method_source("_a191_decide")
    assert "floor_net_bps=self._v623_resting_floor_bps(bid, state)," in decide
    # the shadow observers still measure against the maker target
    assert SIMPLE.count("floor_net_bps=float(DIRECT_MAKER_EXIT_TARGET_BPS),") == 1


def test_negative_aggressive_guard_and_compaction_are_lifted():
    place = _method_source("_research_place_maker_exit")
    assert "and not self._v623_lifted(int(book_id), state)[0]" in place
    assert place.index("self._v61_apply_floor(") < place.index("and not self._v623_lifted(int(book_id), state)[0]")
    comp = _method_source("_v61_compaction_price_ok")
    assert comp.index("if self._v623_lifted(int(book_id), state)[0]:") < comp.index("lot = v61_head_lot(")


def test_the_taker_path_is_untouched():
    for name in ("_v61_taker_verdict", "_v61_rewrite_exit", "_v61_floor_for"):
        assert "_v623" not in _method_source(name)


def test_the_census_reads_the_session_persisted_events():
    census = _method_source("_v623_census")
    assert 'getattr(self, "_research_realized_pnl_events_by_book", None)' in census
    assert 'payload["rolling_realized_pnl_events"]' in RESEARCH
    assert 'raw.get("rolling_realized_pnl_events")' in RESEARCH


def test_switch_defaults_on_and_telemetry_and_stats():
    init = _method_source("_init_build_switches")
    assert 'getattr(self.config, "research_v623_premium_floor", True)' in init
    tele = _method_source("_v62_telemetry")
    assert "premium_floor_on=int(self._v623_on())" in tele
    assert "premium_floor=self._v623_snapshot(state)," in tele
    assert '"direct_v623_premium_floor"' in SIMPLE and '"direct_v623_releases"' in SIMPLE
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_6"' in SIMPLE
    assert "V623_PREMIUM_FLOOR_VERSION = \"premium_floor_v6_2_3\"" in MODULE


def test_launcher_arm_params_guard_and_gate():
    assert "strategy1_direct_v6_2_6)" in LAUNCHER and "V623_BUILD=1 ;;" in LAUNCHER
    assert "strategy1_direct_v6_2_2)" in LAUNCHER                 # the previous arm stays
    assert "research_v623_premium_floor=1" in LAUNCHER
    assert "[preflight] v6.2.3 premium floor PASS" in LAUNCHER
    assert "grep -qF 'floor_net_bps=self._v623_resting_floor_bps(bid, state),'" in LAUNCHER
    assert "tests/test_research_v6_2_3_premium_floor.py" in LAUNCHER
