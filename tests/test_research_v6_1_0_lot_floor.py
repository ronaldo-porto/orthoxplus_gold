"""v6.1: the fee-inclusive FIFO break-even of the lot a close would hit (pure module).

Measured on mainnet UID 34 (v6.0.2, log 20260917_214432, ticks 0-3,647, 2026-09-18): 149
ABSOLUTE takers, realized median -39.4 bps, while the agent's own fee-inclusive maker close read
median +119.8 bps at the same evaluations; the risk band reads a mid MTM that excludes fees, the
validator's FIFO charges both legs' fees at the close, and on mainnet both are rebates (maker
median -32.5 bps, entry rebate on the stopped lots median +56 bps).

These tests pin the arithmetic to ``research_v5_validator_fifo.match_trade_fifo`` -- the
validator's own -- by closing the same lots through both and comparing the realized quote.
"""
from collections import deque

import pytest

import research_v61_lot_floor as lf
from research_v5_validator_fifo import match_trade_fifo

# A mainnet-shaped lot: 0.25 BASE at 400 quote (notional 100), entered as a maker at a
# 56 bps rebate (open fee -0.56 quote); the live maker fee at the close is -32.5 bps.
Q = 0.25
P0 = 400.0
OPEN_FEE = -0.56
MAKER_BPS = -32.5
TAKER_BPS = 46.9
TS = 74_046_000_000_000


def _positions(*, long_qty=0.0, short_qty=0.0, price=P0, fee=OPEN_FEE):
    pos = {"longs": deque(), "shorts": deque()}
    if long_qty:
        pos["longs"].append((TS, long_qty, price, fee))
    if short_qty:
        pos["shorts"].append((TS, short_qty, price, fee))
    return pos


def _validator_close(positions, *, is_buy, qty, price, fee_bps):
    """What the validator realizes: fee in quote for the whole fill, then the FIFO walk."""
    fee_quote = fee_bps * 1e-4 * price * qty
    pnl, _vol = match_trade_fifo(
        positions, is_buy=is_buy, quantity=qty, price=price, fee=fee_quote, timestamp=TS + 1,
    )
    return pnl


# ---- T1 the arithmetic is the validator's ------------------------------------------------------

@pytest.mark.parametrize("price", [396.0, 398.4, 400.0, 401.3, 412.0])
def test_t1_long_close_pnl_matches_the_validator_fifo(price):
    lot = (TS, Q, P0, OPEN_FEE)
    ours = lf.fifo_close_pnl(lot, close_price=price, close_fee_bps=MAKER_BPS, long_position=True)
    theirs = _validator_close(_positions(long_qty=Q), is_buy=False, qty=Q, price=price, fee_bps=MAKER_BPS)
    assert ours == pytest.approx(theirs, abs=1e-12)


@pytest.mark.parametrize("price", [388.0, 399.0, 400.0, 400.9, 404.0])
def test_t1_short_close_pnl_matches_the_validator_fifo(price):
    lot = (TS, Q, P0, OPEN_FEE)
    ours = lf.fifo_close_pnl(lot, close_price=price, close_fee_bps=TAKER_BPS, long_position=False)
    theirs = _validator_close(_positions(short_qty=Q), is_buy=True, qty=Q, price=price, fee_bps=TAKER_BPS)
    assert ours == pytest.approx(theirs, abs=1e-12)


def test_t1_partial_close_prorates_the_opening_fee_like_the_validator():
    lot = (TS, Q, P0, OPEN_FEE)
    ours = lf.fifo_close_pnl(lot, close_price=401.0, close_fee_bps=MAKER_BPS, long_position=True, qty=0.1)
    theirs = _validator_close(_positions(long_qty=Q), is_buy=False, qty=0.1, price=401.0, fee_bps=MAKER_BPS)
    assert ours == pytest.approx(theirs, abs=1e-12)
    # and the fee share is 0.1/0.25 of the stored opening fee
    assert ours == pytest.approx((401.0 - P0) * 0.1 - OPEN_FEE * 0.4 - MAKER_BPS * 1e-4 * 401.0 * 0.1, abs=1e-12)


def test_t1_net_bps_is_pnl_over_closing_notional():
    lot = (TS, Q, P0, OPEN_FEE)
    pnl = lf.fifo_close_pnl(lot, close_price=398.4, close_fee_bps=MAKER_BPS, long_position=True)
    bps = lf.fifo_close_net_bps(lot, close_price=398.4, close_fee_bps=MAKER_BPS, long_position=True)
    assert bps == pytest.approx(pnl / (398.4 * Q) * 1e4, abs=1e-9)
    # the mainnet shape: a -40 bps price move is still positive after two rebates
    assert bps > 0


# ---- T2 break-even and the floor --------------------------------------------------------------

def test_t2_break_even_realizes_exactly_zero_for_a_long():
    lot = (TS, Q, P0, OPEN_FEE)
    be = lf.break_even_price(lot, close_fee_bps=MAKER_BPS, long_position=True)
    assert be is not None and be < P0, "two rebates put the break-even below the entry"
    assert lf.fifo_close_pnl(lot, close_price=be, close_fee_bps=MAKER_BPS, long_position=True) == pytest.approx(0.0, abs=1e-9)
    # entry rebate 56 bps + closing rebate 32.5 bps ~ 88 bps of room
    assert (P0 - be) / P0 * 1e4 == pytest.approx(88.5, abs=0.5)


def test_t2_break_even_realizes_exactly_zero_for_a_short():
    lot = (TS, Q, P0, OPEN_FEE)
    be = lf.break_even_price(lot, close_fee_bps=MAKER_BPS, long_position=False)
    assert be is not None and be > P0
    assert lf.fifo_close_pnl(lot, close_price=be, close_fee_bps=MAKER_BPS, long_position=False) == pytest.approx(0.0, abs=1e-9)


def test_t2_a_fee_paying_entry_moves_the_break_even_the_other_way():
    lot = (TS, Q, P0, +0.30)  # paid 30 bps to enter
    be = lf.break_even_price(lot, close_fee_bps=+2.0, long_position=True)
    assert be is not None and be > P0


def test_t2_target_bps_is_realized_exactly():
    lot = (TS, Q, P0, OPEN_FEE)
    at = lf.break_even_price(lot, close_fee_bps=MAKER_BPS, long_position=True, target_bps=1.0)
    bps = lf.fifo_close_net_bps(lot, close_price=at, close_fee_bps=MAKER_BPS, long_position=True)
    assert bps == pytest.approx(1.0, abs=1e-9)
    short_at = lf.break_even_price(lot, close_fee_bps=MAKER_BPS, long_position=False, target_bps=1.0)
    assert lf.fifo_close_net_bps(lot, close_price=short_at, close_fee_bps=MAKER_BPS, long_position=False) == pytest.approx(1.0, abs=1e-9)


def test_t2_degenerate_lots_have_no_floor():
    assert lf.break_even_price((TS, 0.0, P0, 0.0), close_fee_bps=0.0, long_position=True) is None
    assert lf.break_even_price((TS, Q, 0.0, 0.0), close_fee_bps=0.0, long_position=True) is None
    assert lf.break_even_price("garbage", close_fee_bps=0.0, long_position=True) is None
    # a closing fee of 100% leaves no sell price
    assert lf.break_even_price((TS, Q, P0, 0.0), close_fee_bps=10_000.0, long_position=True) is None


def test_t2_snap_never_crosses_the_floor():
    assert lf.snap_to_grid(396.4712, price_decimals=2, long_position=True) == 396.48
    assert lf.snap_to_grid(396.4712, price_decimals=2, long_position=False) == 396.47
    assert lf.snap_to_grid(396.47, price_decimals=2, long_position=True) == 396.47, "on-grid prices do not move"
    assert lf.snap_to_grid(396.47, price_decimals=2, long_position=False) == 396.47
    assert lf.snap_to_grid(396.4712, price_decimals=0, long_position=True) == 397.0


def test_t2_floor_price_is_the_snapped_break_even_plus_target():
    lot = (TS, Q, P0, OPEN_FEE)
    fl = lf.floor_price(lot, close_fee_bps=MAKER_BPS, long_position=True, target_bps=1.0, price_decimals=2)
    raw = lf.break_even_price(lot, close_fee_bps=MAKER_BPS, long_position=True, target_bps=1.0)
    assert fl >= raw and fl - raw < 0.01
    assert lf.fifo_close_net_bps(lot, close_price=fl, close_fee_bps=MAKER_BPS, long_position=True) >= 1.0 - 1e-9


def test_t2_floored_close_price_keeps_the_better_side():
    assert lf.floored_close_price(401.0, 396.48, long_position=True) == 401.0
    assert lf.floored_close_price(395.0, 396.48, long_position=True) == 396.48
    assert lf.floored_close_price(399.0, 403.52, long_position=False) == 399.0
    assert lf.floored_close_price(405.0, 403.52, long_position=False) == 403.52
    assert lf.floored_close_price(0.0, 396.48, long_position=True) == 396.48, "no desired price: the floor"
    assert lf.floored_close_price(395.0, None, long_position=True) == 395.0, "no floor: unchanged"


# ---- T3 the taker rule ------------------------------------------------------------------------

def test_t3_the_mainnet_absolute_taker_is_refused():
    # book at -40 bps: the taker fee (+46.9) cancels the entry rebate (+56) and the price move is the loss
    pos = _positions(long_qty=Q)
    pnl, closed = lf.taker_close_pnl(pos, qty=Q, touch_price=398.4, taker_fee_bps=TAKER_BPS, long_position=True)
    assert closed == Q and pnl < 0
    assert pnl == pytest.approx(_validator_close(_positions(long_qty=Q), is_buy=False, qty=Q, price=398.4, fee_bps=TAKER_BPS), abs=1e-12)
    assert not lf.taker_is_acceptable(pos, qty=Q, touch_price=398.4, taker_fee_bps=TAKER_BPS, long_position=True)


def test_t3_a_taker_that_realizes_a_gain_passes():
    pos = _positions(long_qty=Q)
    assert lf.taker_is_acceptable(pos, qty=Q, touch_price=399.9, taker_fee_bps=TAKER_BPS, long_position=True)


def test_t3_taker_walks_several_lots_like_the_validator():
    pos = {"longs": deque([(TS, 0.25, 400.0, -0.56), (TS + 5, 0.25, 402.0, -0.30)]), "shorts": deque()}
    pnl, closed = lf.taker_close_pnl(pos, qty=0.4, touch_price=401.0, taker_fee_bps=TAKER_BPS, long_position=True)
    assert closed == pytest.approx(0.4)
    ref = {"longs": deque(pos["longs"]), "shorts": deque()}
    assert pnl == pytest.approx(_validator_close(ref, is_buy=False, qty=0.4, price=401.0, fee_bps=TAKER_BPS), abs=1e-12)
    assert list(pos["longs"]) == [(TS, 0.25, 400.0, -0.56), (TS + 5, 0.25, 402.0, -0.30)], "the walk must not mutate"


def test_t3_quantity_beyond_the_open_lots_realizes_nothing():
    pos = _positions(long_qty=Q)
    pnl_full, closed_full = lf.taker_close_pnl(pos, qty=Q, touch_price=399.9, taker_fee_bps=TAKER_BPS, long_position=True)
    pnl_over, closed_over = lf.taker_close_pnl(pos, qty=1.0, touch_price=399.9, taker_fee_bps=TAKER_BPS, long_position=True)
    assert closed_over == closed_full == Q and pnl_over == pytest.approx(pnl_full)


def test_t3_nothing_to_close_is_not_this_modules_question():
    assert lf.taker_is_acceptable(_positions(), qty=Q, touch_price=400.0, taker_fee_bps=TAKER_BPS, long_position=True)
    assert lf.taker_is_acceptable(_positions(short_qty=Q), qty=Q, touch_price=400.0, taker_fee_bps=TAKER_BPS, long_position=True)


# ---- T4 the head lot -------------------------------------------------------------------------

def test_t4_head_lot_is_the_oldest_of_the_closed_side():
    pos = {"longs": deque([(TS, 0.25, 400.0, -0.56), (TS + 5, 0.25, 402.0, -0.30)]), "shorts": deque([(TS + 9, 0.25, 399.0, -0.1)])}
    assert lf.head_lot(pos, long_position=True) == (TS, 0.25, 400.0, -0.56)
    assert lf.head_lot(pos, long_position=False) == (TS + 9, 0.25, 399.0, -0.1)
    assert lf.head_lot(_positions(), long_position=True) is None
    assert lf.head_lot(None, long_position=True) is None
    # an empty or malformed head is skipped, not returned
    pos["longs"].appendleft((TS - 1, 0.0, 401.0, 0.0))
    assert lf.head_lot(pos, long_position=True) == (TS, 0.25, 400.0, -0.56)


def test_t4_version_and_cadence():
    assert lf.V61_LOT_FLOOR_VERSION == "lot_floor_v6_1_0"
    assert lf.V61_STATE_EVERY_TICKS == 100
