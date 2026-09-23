"""v6.2.13: the exposure band bounds the venue's position, not the ledger's.

The validator's alpha is linear in the venue's fill-based position.  v6.2.5's band (two minimum orders)
was checked against the ledger, which the startup seed left 0.1-0.24 base off on every book and the
divergence repair never corrected (581 standing books, 0 rebuilt on UID 68, 2026-09-23); venue positions
reached 0.75-2.1 while the ledger read 0.5, and the second lot and beyond carried the whole loss tail.
Here every quote is sized so its full fill keeps the venue's position inside the band, and a side with
no whole order of room is refused.  Exact replay: skill 0.419 -> 1.294 at -5.6% making.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6213_venue_band as vb  # noqa: E402
import research_v625_cap_paced as cp  # noqa: E402
from _harness import extractor  # noqa: E402
import test_research_v6_2_5_cap_paced as c5  # noqa: E402
import test_research_v6_2_0_breadth as breadth  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
OK = cp.REASON_OK
BOTH = {"buy": OK, "sell": OK}


def _rule(venue, qty, sides=BOTH):
    return vb.venue_band(venue_net=venue, band=0.5, min_order=0.25, sides=sides,
                         side_qty={"buy": qty, "sell": qty}, ok_token=OK)


# ---- 1. the rule -----------------------------------------------------------------------------------

def test_a_venue_flat_book_quotes_at_most_the_band_each_side():
    sides, qty, refused, capped = _rule(0.0, 1.0)
    assert sides == BOTH and qty == {"buy": 0.5, "sell": 0.5} and (refused, capped) == (0, 2)


def test_one_minimum_order_each_side_is_untouched_on_a_venue_flat_book():
    assert _rule(0.0, 0.25) == (BOTH, {"buy": 0.25, "sell": 0.25}, 0, 0)


def test_one_lot_held_on_the_venue_leaves_one_lot_of_adding_room():
    sides, qty, refused, capped = _rule(0.25, 1.0)
    assert sides == BOTH and qty == {"buy": 0.25, "sell": 0.75}      # the sell may flip to -0.5 at most
    assert (refused, capped) == (0, 2)


def test_a_venue_position_at_the_band_refuses_the_adding_side():
    sides, qty, refused, capped = _rule(0.5, 0.25)
    assert sides == {"buy": vb.REASON_VENUE_BAND, "sell": OK} and qty["sell"] == 0.25
    assert (refused, capped) == (1, 0)


def test_a_venue_position_past_the_band_gets_no_adding_quote_and_a_bounded_reducing_one():
    sides, qty, refused, capped = _rule(-0.9, 2.0)
    assert sides == {"buy": OK, "sell": vb.REASON_VENUE_BAND}
    assert qty["buy"] == 1.25 and (refused, capped) == (1, 1)         # -0.9 + 1.25 = +0.35, inside the band


def test_a_sub_lot_residue_the_ledger_does_not_see_still_bounds_the_venue():
    # the ledger says flat, so both sides come in at the paced clip; the venue says -0.2
    sides, qty, refused, capped = _rule(-0.2, 1.0)
    assert sides == BOTH and qty == {"buy": 0.5, "sell": 0.25} and (refused, capped) == (0, 2)


def test_a_side_the_ledger_left_to_the_exit_path_is_passed_through():
    sides, qty, refused, capped = _rule(0.5, 0.25, sides={"buy": OK, "sell": cp.REASON_EXIT_SIDE})
    assert sides == {"buy": vb.REASON_VENUE_BAND, "sell": cp.REASON_EXIT_SIDE}
    assert qty["sell"] == 0.25 and (refused, capped) == (1, 0)


def test_room_and_whole_lots():
    assert abs(vb.venue_room(venue_net=0.3, band=0.5, side="buy") - 0.2) < 1e-9
    assert abs(vb.venue_room(venue_net=0.3, band=0.5, side="sell") - 0.8) < 1e-9
    assert vb.venue_room(venue_net=0.3, band=0.5, side="?") == 0.0
    assert vb.lots_within(0.2, 0.25) == 0.0
    assert vb.lots_within(0.25, 0.25) == 0.25 and vb.lots_within(0.5, 0.25) == 0.5
    assert vb.lots_within(0.8, 0.25) == 0.75 and vb.lots_within(float("nan"), 0.25) == 0.0


def test_the_version_is_declared():
    assert vb.V6213_VENUE_BAND_VERSION == "venue_band_v6_2_13" and vb.REASON_VENUE_BAND == "VENUE_BAND"


# ---- 2. the acquisition pass ------------------------------------------------------------------------

def _agent(venue=None, on=True):
    """The v6.2.5 acquisition harness with the v6.2.13 methods bound; the venue reports ``venue``
    (a {book: net} table, every other book flat) or, when None, nothing at all."""
    agent = c5._acquire_agent()
    agent.research_v6213_venue_band = on
    agent._v6213_counts, agent._v6213_errors, agent._v6213_last = {}, 0, {}
    agent.mm_base_size = 0.25
    ns = {
        "v6213_venue_band": vb.venue_band, "V6213_REASON_VENUE_BAND": vb.REASON_VENUE_BAND,
        "V6213_VENUE_BAND_VERSION": vb.V6213_VENUE_BAND_VERSION,
        "v625_band_for": cp.band_for, "V625_REASON_OK": cp.REASON_OK,
    }
    cls = type(agent)
    for name in ("_v6213_venue_band", "_v6213_count", "_v6213_snapshot"):
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]), "<v6213>", "exec"), ns)
        setattr(cls, name, ns[name])
    if venue is None:
        cls._a195_venue_net_by_book = lambda self, books: {}
    else:
        table = dict(venue)
        cls._a195_venue_net_by_book = lambda self, books: {int(b): float(table.get(int(b), 0.0)) for b in books}
    return agent


def _acquire(agent, state=None):
    r = breadth._Response()
    agent._v62_acquire(r, state or breadth._state(8), {})
    return c5._placed(r)


def test_a_ledger_flat_book_the_venue_holds_at_the_band_gets_only_its_reducing_side():
    agent = _agent(venue={3: -0.5})
    placed = _acquire(agent)
    assert len(placed[3]) == 1 and placed[3][0].direction is breadth._OrderDirection.BUY
    assert float(placed[3][0].quantity) == 0.25
    assert all(len(v) == 2 for b, v in placed.items() if b != 3)
    assert agent._v6213_counts == {"venue_held": 1, "refused": 1}


def test_a_stepped_clip_on_a_venue_flat_book_is_cut_to_the_band():
    agent = _agent(venue={})
    state = breadth._state(8)
    agent._v625_pace = {3: cp.BookPace(clip=1.0, obs_rate=None, target_rate=0.0,
                                      sampled_ns=int(getattr(state, "timestamp", 0) or 0), volume=0.0)}
    placed = _acquire(agent, state)
    assert len(placed[3]) == 2 and {float(ix.quantity) for ix in placed[3]} == {0.5}
    assert agent._v6213_counts == {"capped": 2}


def test_with_the_switch_off_the_stepped_clip_quotes_whole():
    agent = _agent(venue={}, on=False)
    state = breadth._state(8)
    agent._v625_pace = {3: cp.BookPace(clip=1.0, obs_rate=None, target_rate=0.0,
                                      sampled_ns=int(getattr(state, "timestamp", 0) or 0), volume=0.0)}
    placed = _acquire(agent, state)
    assert {float(ix.quantity) for ix in placed[3]} == {1.0} and agent._v6213_counts == {}


def test_a_ledger_held_book_past_the_band_on_the_venue_gets_nothing_from_acquire():
    agent = _agent(venue={3: 0.75})
    agent.inventory[3] = 0.25         # the ledger: one lot long, the buy adds and the sell is the exit's
    placed = _acquire(agent)
    assert 3 not in placed
    assert agent._v62_request.get(vb.REASON_VENUE_BAND) == 1
    assert agent._v6213_counts == {"venue_held": 1, "refused": 1}


def test_an_unresolved_venue_leaves_the_ledger_verdict_alone():
    agent = _agent(venue=None)
    placed = _acquire(agent)
    assert len(placed) == 8 and all(len(v) == 2 for v in placed.values())
    assert {float(ix.quantity) for ix in sum(placed.values(), [])} == {0.25}
    assert agent._v6213_counts == {"venue_unresolved": 8}


def test_the_snapshot_reports_the_venue_book():
    agent = _agent(venue={3: -0.5, 4: 0.8})
    _acquire(agent)
    snap = agent._v6213_snapshot()
    assert snap["version"] == "venue_band_v6_2_13" and snap["errors"] == 0
    assert snap["venue_books"] == 8 and abs(snap["venue_abs"] - 1.3) < 1e-9
    assert snap["venue_max"] == 0.8 and snap["venue_over_band"] == 1
    assert snap["refused"] == 2 and snap["venue_held"] == 2


# ---- 3. wiring --------------------------------------------------------------------------------------

def test_the_call_site_sits_between_the_side_clips_and_the_surplus_check():
    src = _simple("_v62_acquire")
    a = src.index("side_qty = self._v626_side_clips(book_id, clip)")
    b = src.index("sides, side_qty = self._v6213_venue_band(book_id, sides, side_qty, flat_eps=eps)")
    c = src.index("for side_token in (V625_SIDE_BUY, V625_SIDE_SELL):")
    assert a < b < c
    assert 'getattr(self, "research_v6213_venue_band", False)' in src[a:b]


def test_the_band_is_the_venue_unit_and_the_position_is_the_venues():
    src = _simple("_v6213_venue_band")
    assert "self._a195_venue_net_by_book({int(book_id): None})" in src
    assert "band=v625_band_for(min_order)" in src                 # two minimum orders, never the paced clip
    assert 'self._v6213_count("refused", refused)' in src


def test_switch_default_on_declared_and_reported():
    assert 'getattr(self.config, "research_v6213_venue_band", True)' in SIMPLE
    assert "research_v6213_venue_band=1" in LAUNCHER
    assert "venue_band_on=int(" in SIMPLE and "venue_band=self._v6213_snapshot()," in SIMPLE
    assert "from research_v6213_venue_band import (" in SIMPLE


def test_version_and_launcher_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_13"' in SIMPLE
    assert "strategy1_direct_v6_2_13)" in LAUNCHER and "V6212_BUILD=1; V6213_BUILD=1 ;;" in LAUNCHER
    assert "strategy1_direct_v6_2_12)" in LAUNCHER                # the previous arm stays
    assert 'echo "[preflight] v6.2.13 venue band PASS"' in LAUNCHER
    assert "tests/test_research_v6_2_13_venue_band.py" in LAUNCHER
