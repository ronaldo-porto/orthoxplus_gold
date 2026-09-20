"""v6.2.6: clean closes and balanced capture, on top of the v6.2.5 cap pace.

Measured on UID 82 at v6.2.5 tick 3,026 (2026-09-20): the pace works (ratio 1.07, 6.4M per window,
158 closes per book per window), but kappa is still 0.4996 because only 16 of 128 books have a window
free of losing closes, and balance fell to 37% because the adding side is the side the book is already
long or short and an entry fill captures about nothing.  Rule A spends a clean book only when it is
stuck and the median still holds; rule B sizes each side by which side's capture is behind; rule C
gives a touch quote the exit's life.  Two defects the same read found are fixed without a switch:
4,517 OPEN_BOOK_CAP rejects (the guard counts one book twice) and 2,743 SAME_REQUEST_BOOK_SIDE_OWNED.
"""
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v626_balanced_maker as bm  # noqa: E402
import research_v625_cap_paced as cp  # noqa: E402
import research_v623_premium_floor as pf  # noqa: E402
from research_v62_breadth import universe_caps  # noqa: E402
from research_v62_making_mirror import MakingMirror  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")


# ------------------------------------------------------------------------------------------------
# 1. Rule A: the loss budget under the median
# ------------------------------------------------------------------------------------------------

def test_the_budget_keeps_the_median_book_clean():
    # 128 scored books, none spent: 63 may be spent and the 64th would tie the median
    assert bm.median_budget(premium=128, loss=0) == 63
    assert bm.median_budget(premium=65, loss=63) == 0
    assert bm.median_budget(premium=66, loss=62) == 1
    # v6.2.5's measured census: 16 clean, 112 spent -- nothing left to spend
    assert bm.median_budget(premium=16, loss=112) == 0
    assert bm.median_budget(premium=0, loss=0) == 0


def test_a_spent_book_is_free_and_a_clean_one_needs_both_conditions():
    assert bm.spend_allowed(status_is_loss=True, blocked=False, budget=0) is True
    assert bm.spend_allowed(status_is_loss=False, blocked=True, budget=1) is True
    assert bm.spend_allowed(status_is_loss=False, blocked=True, budget=0) is False
    assert bm.spend_allowed(status_is_loss=False, blocked=False, budget=9) is False


def test_blocked_is_one_clip_past_the_band():
    assert bm.book_blocked(net_base=0.25, clip=0.25, band=0.5) is False   # one more clip fits exactly
    assert bm.book_blocked(net_base=0.50, clip=0.25, band=0.5) is True
    assert bm.book_blocked(net_base=-0.50, clip=0.25, band=0.5) is True   # short books mirror
    assert bm.book_blocked(net_base=1.0, clip=1.0, band=2.0) is False     # the band scales


# ------------------------------------------------------------------------------------------------
# 2. Rule B: capture balancing
# ------------------------------------------------------------------------------------------------

def test_the_ratio_is_signed_and_bounded():
    assert bm.balance_ratio(0.0, 0.0) == 0.0
    assert bm.balance_ratio(10.0, 0.0) == 1.0
    assert bm.balance_ratio(0.0, 10.0) == -1.0
    assert bm.balance_ratio(6.0, 2.0) == 0.5


def test_size_goes_to_the_side_that_is_behind():
    out = bm.side_clips(clip=1.0, buy_capture=6.0, sell_capture=2.0, min_order=0.25, lots_of=cp.lots_of)
    assert out["sell"] == 1.5 and out["buy"] == 0.5          # deficit x1.5, surplus x0.5
    mirror = bm.side_clips(clip=1.0, buy_capture=2.0, sell_capture=6.0, min_order=0.25, lots_of=cp.lots_of)
    assert mirror["buy"] == 1.5 and mirror["sell"] == 0.5


def test_a_book_with_no_capture_yet_is_quoted_evenly():
    out = bm.side_clips(clip=0.5, buy_capture=0.0, sell_capture=0.0, min_order=0.25, lots_of=cp.lots_of)
    assert out == {"buy": 0.5, "sell": 0.5}


def test_the_leading_side_stops_when_the_smaller_side_has_gone_negative():
    out = bm.side_clips(clip=1.0, buy_capture=4.5, sell_capture=-40.8, min_order=0.25, lots_of=cp.lots_of)
    assert out["buy"] == 0.0                                  # book 37 of the v6.2.5 read
    assert out["sell"] > 0.0
    both_negative = bm.side_clips(clip=1.0, buy_capture=-1.0, sell_capture=-2.0, min_order=0.25,
                                  lots_of=cp.lots_of)
    assert both_negative == {"buy": 0.0, "sell": 0.0}   # neither side can raise the minimum


def test_the_skewed_clips_stay_whole_minimum_orders():
    out = bm.side_clips(clip=0.25, buy_capture=5.0, sell_capture=4.0, min_order=0.25, lots_of=cp.lots_of)
    assert all(abs((q / 0.25) - round(q / 0.25)) < 1e-9 for q in out.values())
    assert min(out.values()) >= 0.25


def test_the_mirror_reports_per_book_capture_for_the_tracked_uid():
    mirror = MakingMirror(82)
    mirror.buy_sums[82][7] = 3.5
    mirror.sell_sums[82][7] = -1.25
    assert mirror.book_capture(7) == (3.5, -1.25)
    assert mirror.book_capture(9) == (0.0, 0.0)
    assert mirror.book_capture("x") == (0.0, 0.0)


# ------------------------------------------------------------------------------------------------
# 3. The two defect fixes
# ------------------------------------------------------------------------------------------------

class _Dir:
    def __init__(self, name):
        self.name = name


class _Ix:
    def __init__(self, book, direction):
        self.bookId = book
        self.direction = direction


class _Resp:
    def __init__(self, instructions=()):
        self.instructions = list(instructions)


def test_a_side_this_response_already_owns_is_refused_before_the_venue_sees_it():
    r = _Resp([_Ix(93, _Dir("SELL"))])
    assert bm.side_already_instructed(r, 93, _Dir("SELL")) is True
    assert bm.side_already_instructed(r, 93, _Dir("BUY")) is False
    assert bm.side_already_instructed(r, 94, _Dir("SELL")) is False
    assert bm.side_already_instructed(_Resp(), 93, _Dir("SELL")) is False
    # ints and strings read the same way
    assert bm.side_already_instructed(_Resp([_Ix(3, 0)]), 3, "buy") is True
    assert bm.side_already_instructed(_Resp([_Ix(3, 1)]), 3, "buy") is False


def test_the_open_book_counts_are_widened_but_the_base_exposure_is_not():
    caps = universe_caps(128, 0.5)
    wide = bm.open_book_caps(caps)
    assert wide["research_max_total_abs_base"] == caps["research_max_total_abs_base"]
    for key in ("research_max_total_open_books", "research_max_active_open_books",
                "research_max_open_books", "max_managed_books_per_tick", "max_mm_books_per_tick"):
        assert wide[key] == caps[key] * 2
    # 2 x 128 can never admit more than the 128 books that exist
    assert wide["research_max_total_open_books"] == 256


def test_the_skip_reason_is_the_binding_one_not_the_exit_side():
    sides = {"buy": cp.REASON_EXIT_SIDE, "sell": cp.REASON_BAND}
    assert bm.skip_reason(sides, exit_side_token=cp.REASON_EXIT_SIDE) == cp.REASON_BAND
    sides = {"buy": cp.REASON_CAP_RESERVE, "sell": cp.REASON_EXIT_SIDE}
    assert bm.skip_reason(sides, exit_side_token=cp.REASON_EXIT_SIDE) == cp.REASON_CAP_RESERVE
    both = {"buy": cp.REASON_EXIT_SIDE, "sell": cp.REASON_EXIT_SIDE}
    assert bm.skip_reason(both, exit_side_token=cp.REASON_EXIT_SIDE) == cp.REASON_EXIT_SIDE


# ------------------------------------------------------------------------------------------------
# 4. The agent's own helpers, executed
# ------------------------------------------------------------------------------------------------

class _Agent:
    mm_base_size = 0.25
    research_profitable_exit_ttl_ms = 4000.0
    research_enable_adaptive_ttl = True
    mm_expiry_period = 500_000_000

    def __init__(self, **kw):
        self.research_v626_loss_budget = kw.get("loss_budget", True)
        self.research_v626_capture_balance = kw.get("capture_balance", True)
        self.research_v626_quote_life = kw.get("quote_life", True)
        self._v626_counts = {}
        self._v626_spent = None
        self._v626_errors = 0
        self._v625_pace = {}
        self._v62_mirror = None
        self._tick = 5
        self.inventory = {}

    def _v62_on(self):
        return True

    def _v623_on(self):
        return True

    def _direct_signed_inventory(self, book_id):
        return self.inventory.get(int(book_id), 0.0)

    def _research_choose_ttl(self, *a, **kw):
        return (3000.0, "STABLE", None)


def _bind(agent, *names):
    ns = {
        "v626_median_budget": bm.median_budget, "v626_spend_allowed": bm.spend_allowed,
        "v626_book_blocked": bm.book_blocked, "v626_side_clips": bm.side_clips,
        "v626_balance_ratio": bm.balance_ratio, "v625_band_for": cp.band_for,
        "v625_lots_of": cp.lots_of, "V625_SIDE_BUY": cp.SIDE_BUY, "V625_SIDE_SELL": cp.SIDE_SELL,
        "v623_book_status": pf.book_status, "V623_MIN_OBSERVATIONS": pf.KAPPA_MIN_REALIZED_OBSERVATIONS,
        "V623_BOOK_PREMIUM": pf.BOOK_PREMIUM, "V623_BOOK_LOSS": pf.BOOK_LOSS, "Any": object,
    }
    for name in names:
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]),
                     "<v626>", "exec"), ns)
        setattr(type(agent), name, ns[name])
    return agent


def _agent(**kw):
    return _bind(_Agent(**kw), "_v626_loss_budget_on", "_v626_capture_balance_on",
                 "_v626_quote_life_on", "_v626_on", "_v626_count", "_v626_budget", "_v626_spend",
                 "_v626_blocked", "_v626_book_capture", "_v626_side_clips", "_v626_snapshot",
                 "_v62_entry_ttl_ns")


def _census(premium, loss):
    """A census with `premium` clean books and `loss` spent ones, in the module's own shape."""
    out = {}
    b = 0
    for _ in range(premium):
        out[b] = pf.BookWindow(observations=3, negatives=0, realized_sum=3.0); b += 1
    for _ in range(loss):
        out[b] = pf.BookWindow(observations=3, negatives=1, realized_sum=-1.0); b += 1
    return out


def test_the_agent_reads_the_budget_off_the_census_and_spends_it_once_per_book():
    agent = _agent()
    census = _census(66, 62)
    assert agent._v626_budget(None, census) == 1
    agent._v626_spend(7)
    assert agent._v626_budget(None, census) == 0        # the same state cannot spend twice
    agent._tick = 6
    assert agent._v626_budget(None, census) == 1        # a new state re-reads the census


def test_blocked_uses_the_paced_clip_of_that_book():
    agent = _agent()
    agent.inventory[3] = 0.5
    assert agent._v626_blocked(3) is True               # default clip 0.25, band 0.5
    agent._v625_pace[3] = cp.BookPace(clip=1.0, obs_rate=None, target_rate=0.0, sampled_ns=0, volume=0.0)
    assert agent._v626_blocked(3) is False              # clip 1.0, band 2.0


def test_the_side_clips_come_from_the_mirror_and_count_what_they_did():
    agent = _agent()
    mirror = MakingMirror(82)
    mirror.buy_sums[82][4] = 6.0
    mirror.sell_sums[82][4] = 2.0
    agent._v62_mirror = mirror
    assert agent._v626_side_clips(4, 1.0) == {"buy": 0.5, "sell": 1.5}
    assert agent._v626_counts.get("side_skewed") == 1
    mirror.sell_sums[82][4] = -3.0
    assert agent._v626_side_clips(4, 1.0)["buy"] == 0.0
    assert agent._v626_counts.get("surplus_paused") == 1


def test_capture_balance_off_quotes_both_sides_at_the_paced_clip():
    agent = _agent(capture_balance=False)
    mirror = MakingMirror(82)
    mirror.buy_sums[82][4] = 6.0
    mirror.sell_sums[82][4] = -2.0
    agent._v62_mirror = mirror
    assert agent._v626_side_clips(4, 1.0) == {"buy": 1.0, "sell": 1.0}


def test_the_touch_quote_takes_the_persistent_exit_life_and_its_cap():
    agent = _agent()
    assert agent._v62_entry_ttl_ns(7, None) == 4_000_000_000
    agent.research_profitable_exit_ttl_ms = 9000.0
    assert agent._v62_entry_ttl_ns(7, None) == 5_000_000_000      # the frozen 5 s cap
    off = _agent(quote_life=False)
    assert off._v62_entry_ttl_ns(7, None) == 3_000_000_000        # the adaptive TTL, as in v6.2.5


# ------------------------------------------------------------------------------------------------
# 5. Wiring
# ------------------------------------------------------------------------------------------------

def test_the_lift_consults_the_budget_and_a_spent_book_stays_free():
    src = _simple("_v623_lifted")
    assert "if not self._v626_loss_budget_on():" in src
    assert "if status == V623_BOOK_LOSS:" in src
    assert "if v626_spend_allowed(" in src
    assert "self._v626_spend(int(book_id))" in src


def test_the_acquisition_sizes_each_side_and_reports_the_binding_reason():
    src = _simple("_v62_acquire")
    assert "side_qty = self._v626_side_clips(book_id, clip)" in src
    assert "sides[side_token] = V626_REASON_SURPLUS" in src
    assert "verdict = V62_REASON_OK if allowed else v626_skip_reason(" in src


def test_the_placement_uses_the_side_quantity_and_skips_an_owned_side():
    src = _simple("_v62_place_touch_quotes")
    assert "if v626_side_already_instructed(response, int(book_id), direction):" in src
    assert "quantity=side_q," in src
    assert "if side_q <= 0.0:" in src


def test_both_cap_sites_widen_the_book_counts():
    assert "caps = v626_open_book_caps(v62_universe_caps(n, lot))" in SIMPLE
    assert "caps = v626_open_book_caps(v62_universe_caps(n, V625_BAND_CLIPS * lot))" in SIMPLE


def test_the_switches_default_on_and_the_state_row_carries_them():
    for key in ("research_v626_loss_budget", "research_v626_capture_balance", "research_v626_quote_life"):
        assert f'getattr(self.config, "{key}", True)' in SIMPLE
        assert f'stats["direct_{key[9:]}"] = int(self._{key[9:]}_on())'.replace("direct_v626", "direct_v626") in SIMPLE
    assert "loss_budget_on=int(self._v626_loss_budget_on())" in SIMPLE
    assert "balanced_maker=self._v626_snapshot()" in SIMPLE


def test_the_version_pin_moved_and_the_launcher_carries_the_build():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_6"' in SIMPLE
    assert "strategy1_direct_v6_2_6)" in LAUNCHER and "V626_BUILD=1 ;;" in LAUNCHER
    assert "[preflight] v6.2.6 balanced maker PASS" in LAUNCHER
    assert "tests/test_research_v6_2_6_balanced_maker.py" in LAUNCHER
    for key in ("research_v626_loss_budget=1", "research_v626_capture_balance=1",
                "research_v626_quote_life=1"):
        assert key in LAUNCHER
    # v6.2.5 keeps its own arm, so either build can still be run
    assert "strategy1_direct_v6_2_5)" in LAUNCHER
