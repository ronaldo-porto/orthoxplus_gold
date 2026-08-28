from pathlib import Path

from research_contract_guard import guarded_post_only_price


ROOT = Path(__file__).parents[1]
SRC = (ROOT / "agents" / "strategy" / "Strategy1_Research.py").read_text()
BASE = (ROOT / "agents" / "strategy" / "BaseStrategy.py").read_text()
ADAPTIVE = (ROOT / "agents" / "strategy" / "AdaptiveAgent.py").read_text()


def test_book106_safe_reprice_is_deterministic():
    # Representative runtime shape: raw profitable SELL exit was below the
    # current post-only-safe ask. The existing guard must lift it before reuse.
    price = guarded_post_only_price(
        side="sell",
        original_price=311.21,
        best_bid=311.29,
        best_ask=311.30,
        tick_size=0.01,
        reject_streak=1,
    )
    assert abs(price - 311.31) < 1e-12


def test_buy_side_is_symmetric():
    price = guarded_post_only_price(
        side="buy",
        original_price=99.45,
        best_bid=99.35,
        best_ask=99.40,
        tick_size=0.01,
        reject_streak=1,
    )
    assert abs(price - 99.34) < 1e-12


def test_exit_guard_clamp_runs_before_profitable_hold():
    maker_start = SRC.index("def _research_place_maker_exit")
    maker_end = SRC.index("def _research_manage_realization", maker_start)
    block = SRC[maker_start:maker_end]
    assert 'action="EXIT_PRICE_CLAMP"' in block
    assert '"PROFITABLE_EXIT_HOLD"' in block
    assert block.index('action="EXIT_PRICE_CLAMP"') < block.index('"PROFITABLE_EXIT_HOLD"')
    assert 'self._research_contract_reject_state.get((int(book_id), side_token))' in block


def test_accepted_exit_retains_existing_guard_without_new_cache():
    accepted_start = SRC.index("def onOrderAccepted")
    accepted_end = SRC.index("def _research_clear_contract_guards_for_book", accepted_start)
    block = SRC[accepted_start:accepted_end]
    assert 'action="ACCEPT_RETAIN_EXIT_GUARD"' in block
    assert 'net_qty > 1e-12 and side == "sell"' in block
    assert 'net_qty < -1e-12 and side == "buy"' in block
    # Retained exit returns before the existing pop/clear path.
    assert block.index('action="ACCEPT_RETAIN_EXIT_GUARD"') < block.index('self._research_contract_reject_state.pop(key, None)')
    assert "_contract_safe_exit_bounds" not in SRC


def test_v4139_promotion_chain_is_exact():
    assert 'RESEARCH_POLICY_VERSION = "wide_kappa_wave_v4_14_3"' in SRC
    assert 'EXIT_CONTRACT_GUARD_PERSISTENCE_VERSION = "sticky_exit_contract_guard_v4_13_9"' in SRC
    assert 'DEPLOY_POLICY_VERSION = "base_v4_13_9_champion"' in BASE
    assert 'BASE_CHAMPION_PARENT = "simplified_kappa_productivity_v4_13_9"' in BASE
    assert 'ADAPTIVE_VERSION = "adaptive_v4_13_9_realtime"' in ADAPTIVE
