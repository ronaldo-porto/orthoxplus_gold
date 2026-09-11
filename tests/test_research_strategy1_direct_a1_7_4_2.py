from pathlib import Path
import ast
import sys

ROOT = Path(__file__).parents[1]
STRATEGY_DIR = ROOT / "agents" / "strategy"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(STRATEGY_DIR))

PATH = STRATEGY_DIR / "Strategy1_Research_Simple.py"
SRC = PATH.read_text(encoding="utf-8")
TREE = ast.parse(SRC)
CLASS = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
METHODS = {n.name: n for n in CLASS.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

from research_direct_dust_kappa import (
    DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS,
    DIRECT_DUST_KAPPA_VERSION,
    REASON_LOSS_BUDGET,
    REASON_UNKNOWN_COST,
    REASON_WITHIN_BUDGET,
    decide_kappa_safe_dust_compaction,
)
from research_direct_tail_recovery import (
    DIRECT_RECOVERY_TRIGGER_BPS,
    DIRECT_RECOVERY_FORCE_BPS,
    DIRECT_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS,
    DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS,
)
from research_direct_trade_dedup import DIRECT_TRADE_DEDUP_VERSION
from research_direct_liveness import DIRECT_LIVENESS_VERSION


def test_a1742_version_and_frozen_core_contracts():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_2"' in SRC
    # A1.7.5 adds age escalation on top of this module; the base floor asserted
    # on the next line -- the budget every fresh residual still gets -- is frozen.
    assert DIRECT_DUST_KAPPA_VERSION == "direct_dust_kappa_v4_16_2_a1_7_5"
    assert DIRECT_DUST_KAPPA_MAKER_FLOOR_BPS == -60.0
    assert DIRECT_TRADE_DEDUP_VERSION == "direct_trade_dedup_v4_16_2_a1_7_4_1"
    assert DIRECT_LIVENESS_VERSION == "direct_partial_liveness_v4_16_2_a1_7_3_1"
    assert DIRECT_RECOVERY_TRIGGER_BPS == -8.0
    assert DIRECT_RECOVERY_FORCE_BPS == -12.0
    assert DIRECT_RECOVERY_MAKER_FLOOR_BPS == -25.0
    assert DIRECT_HARD_RECOVERY_MAKER_FLOOR_BPS == -30.0
    assert DIRECT_ABSOLUTE_RECOVERY_MAKER_FLOOR_BPS == -35.0


def test_book53_style_extreme_stale_cross_is_blocked():
    # Runtime counterexample: about -0.1987 BASE crossed with a full 0.25 BUY
    # near 342.25, realizing roughly -920 bps / -5.73 quote PnL.
    d = decide_kappa_safe_dust_compaction(
        net_base=-0.1987,
        min_order=0.25,
        vwap_entry=313.42,
        maker_close_price=342.25,
        age_ticks=1726,
    )
    assert d.cross_dust is True
    assert d.allow is False
    assert d.reason == REASON_LOSS_BUDGET
    assert d.projected_net > 0.0
    assert d.maker_realization_bps is not None and d.maker_realization_bps < -900.0
    assert d.projected_realized_quote_pnl is not None and d.projected_realized_quote_pnl < -5.0


def test_moderate_cross_with_bounded_loss_remains_allowed():
    d = decide_kappa_safe_dust_compaction(
        net_base=-0.20,
        min_order=0.25,
        vwap_entry=340.0,
        maker_close_price=342.0,
        age_ticks=100,
    )
    assert d.cross_dust is True
    assert d.allow is True
    assert d.reason == REASON_WITHIN_BUDGET
    assert -60.0 <= d.maker_realization_bps < 0.0


def test_profitable_cross_is_allowed():
    d = decide_kappa_safe_dust_compaction(
        net_base=0.20,
        min_order=0.25,
        vwap_entry=300.0,
        maker_close_price=303.0,
        age_ticks=40,
    )
    assert d.cross_dust is True
    assert d.allow is True
    assert d.maker_realization_bps > 0.0


def test_unknown_cost_basis_fails_closed_for_sign_cross():
    d = decide_kappa_safe_dust_compaction(
        net_base=-0.20,
        min_order=0.25,
        vwap_entry=None,
        maker_close_price=342.0,
        age_ticks=100,
    )
    assert d.cross_dust is True
    assert d.allow is False
    assert d.reason == REASON_UNKNOWN_COST


def test_direct_compactor_checks_kappa_guard_before_parent_placement():
    method = ast.get_source_segment(SRC, METHODS["_direct_compact_selected_dust"])
    assert "decide_kappa_safe_dust_compaction" in method
    assert '"A1742_DUST_KAPPA_BLOCK"' in method
    assert '"A1742_DUST_KAPPA_ALLOW"' in method
    assert method.index("decide_kappa_safe_dust_compaction") < method.index("super()._place_passive_inventory_exit")
    assert "if not kappa_decision.allow" in method


def test_a1742_does_not_modify_tiny_normalizer_or_tail_recovery_authority():
    normalizer = ast.get_source_segment(SRC, METHODS["_direct_place_dust_normalizer"])
    compactor = ast.get_source_segment(SRC, METHODS["_direct_compact_selected_dust"])
    assert "decide_kappa_safe_dust_compaction" not in normalizer
    assert "decide_kappa_safe_dust_compaction" in compactor
    # A1.7.4 recovery remains imported, not replaced by the dust guard.
    assert "choose_tail_recovery_override" in SRC
