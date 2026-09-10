from pathlib import Path
from types import SimpleNamespace
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

from research_direct_trade_dedup import (
    DIRECT_TRADE_DEDUP_MAX_EVENTS,
    DIRECT_TRADE_DEDUP_VERSION,
    DirectTradeEventDeduper,
    trade_event_identity,
)
from research_direct_tail_recovery import DIRECT_TAIL_RECOVERY_VERSION


def _event(**kw):
    base = dict(
        bookId=64, tradeId=941, timestamp=925941382603, clientOrderId=7011,
        takerAgentId=8, takerOrderId=811, makerAgentId=67, makerOrderId=1274785,
        side=0, quantity=0.25, price=304.54,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_a1741_version_and_a174_recovery_thresholds_remain_frozen():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v4_16_2_a1_9_0_3"' in SRC
    assert DIRECT_TRADE_DEDUP_VERSION == "direct_trade_dedup_v4_16_2_a1_7_4_1"
    assert DIRECT_TRADE_DEDUP_MAX_EVENTS == 32768
    # A1.7.5 extends the recovery module; the A1.7.4 thresholds stay frozen.
    assert DIRECT_TAIL_RECOVERY_VERSION == "direct_tail_recovery_v4_16_2_a1_7_5"


def test_exact_trade_event_identity_is_stable_and_specific():
    a = _event()
    b = _event()
    c = _event(quantity=0.20)
    assert trade_event_identity(a) == trade_event_identity(b)
    assert trade_event_identity(a) != trade_event_identity(c)


def test_deduper_survives_timestamp_regression_and_skips_exact_replay():
    d = DirectTradeEventDeduper(max_events=8)
    first = _event(timestamp=900)
    reset_clock_event = _event(tradeId=942, timestamp=100, makerOrderId=1274786)
    assert d.check_and_note(first)[0] is False
    assert d.check_and_note(reset_clock_event)[0] is False
    # Clock moved backwards, but the process-lifetime cache still remembers it.
    assert d.check_and_note(first)[0] is True


def test_bounded_cache_evicts_oldest_identity():
    d = DirectTradeEventDeduper(max_events=2)
    a = _event(tradeId=1)
    b = _event(tradeId=2)
    c = _event(tradeId=3)
    assert d.check_and_note(a)[0] is False
    assert d.check_and_note(b)[0] is False
    assert d.check_and_note(c)[0] is False
    assert len(d) == 2
    assert d.check_and_note(a)[0] is False  # oldest was evicted


def _build_synthetic_direct_class():
    method = ast.get_source_segment(SRC, METHODS["onTrade"])
    # Execute the exact production onTrade body inside a tiny synthetic parent
    # so the regression proves the de-dup guard prevents the accounting call.
    class_src = "class Parent:\n" \
        "    def onTrade(self, event, validator=None):\n" \
        "        self.parent_calls += 1\n" \
        "        self.inventory += float(event.quantity)\n\n" \
        "class Direct(Parent):\n" + "\n".join("    " + line for line in method.splitlines())
    ns = dict(
        DirectTradeEventDeduper=DirectTradeEventDeduper,
        DIRECT_TRADE_DEDUP_MAX_EVENTS=DIRECT_TRADE_DEDUP_MAX_EVENTS,
        DIRECT_TRADE_DEDUP_VERSION=DIRECT_TRADE_DEDUP_VERSION,
    )
    exec(class_src, ns)
    return ns["Direct"]


def test_same_own_trade_delivered_twice_mutates_parent_accounting_once():
    Direct = _build_synthetic_direct_class()
    agent = Direct()
    agent.uid = 67
    agent._tick = 1502
    agent._pnl_tick_buffer = {}
    agent._direct_event_pnl_before = {}
    agent._direct_trade_deduper = DirectTradeEventDeduper(max_events=32)
    agent._direct_duplicate_trade_events_skipped = 0
    agent.parent_calls = 0
    agent.inventory = 0.0
    emitted = []
    agent._emit = lambda event_type, force=False, **payload: emitted.append((event_type, payload))

    ev = _event()
    agent.onTrade(ev, "validator")
    agent.onTrade(ev, "validator")

    assert agent.parent_calls == 1
    assert agent.inventory == 0.25
    assert agent._direct_duplicate_trade_events_skipped == 1
    assert emitted[-1][0] == "DUPLICATE_TRADE_EVENT_SKIPPED"
    assert emitted[-1][1]["trade_id"] == 941


def test_non_identical_trade_with_same_trade_id_is_not_suppressed():
    Direct = _build_synthetic_direct_class()
    agent = Direct()
    agent.uid = 67
    agent._tick = 1
    agent._pnl_tick_buffer = {}
    agent._direct_event_pnl_before = {}
    agent._direct_trade_deduper = DirectTradeEventDeduper(max_events=32)
    agent._direct_duplicate_trade_events_skipped = 0
    agent.parent_calls = 0
    agent.inventory = 0.0
    agent._emit = lambda *args, **kwargs: None

    agent.onTrade(_event(quantity=0.25), None)
    agent.onTrade(_event(quantity=0.20), None)
    assert agent.parent_calls == 2
    assert agent.inventory == 0.45


def test_dedup_guard_runs_before_parent_fill_accounting_and_emits_diagnostic():
    method = ast.get_source_segment(SRC, METHODS["onTrade"])
    assert 'deduper.check_and_note(event)' in method
    assert '"DUPLICATE_TRADE_EVENT_SKIPPED"' in method
    assert method.index('deduper.check_and_note(event)') < method.index('super().onTrade(event, validator)')
    assert 'return' in method
