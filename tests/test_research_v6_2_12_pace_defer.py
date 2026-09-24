"""v6.2.12: a held book defers its pace step instead of throwing the sample away.

v6.2.11 R2 kept the cap pacer off held books by re-seeding the book's sample on every held request.
``observed_rate`` needs a whole sampling interval (600 sim-s) since that seed, so a book that is never
flat for 600 uninterrupted seconds never takes a sample at all.  On the live arm that was every book:
at UID 67's tick-3,000 read (2026-09-23) ``pace_held`` was 300,517, ``pace_ratio_p50`` null and all 128
clips still at the 0.25 minimum, with 3 books flat, 40 holding dust under one minimum order and 85
holding one working clip.  The volume cap counts held seconds too, so the sample has to keep running;
only the step waits for the book to be flat, which is what kept the v6.2.5 martingale out.
"""
import ast
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v6212_pace_defer as pd  # noqa: E402
from _harness import extractor  # noqa: E402
import test_research_v6_2_5_cap_paced as c5  # noqa: E402
import test_research_v6_2_11_score_logic as t11  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_simple = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000


# ---- 1. the sample a held request leaves behind ---------------------------------------------------

def test_the_deferred_sample_is_the_running_one():
    assert pd.sample_after_hold(defer=True, sampled_ns=0, volume=100.0, now_ns=600 * S, used=900.0) == (0, 100.0)


def test_without_the_switch_the_sample_restarts_at_this_request():
    assert pd.sample_after_hold(
        defer=False, sampled_ns=0, volume=100.0, now_ns=600 * S, used=900.0,
    ) == (600 * S, 900.0)


def test_the_version_is_declared():
    assert pd.V6212_PACE_DEFER_VERSION == "pace_defer_v6_2_12"


# ---- 2. the controller -----------------------------------------------------------------------------

def _pacer(defer=True, hold_pace=True):
    """The live _v625_clip over the v6.2.5 harness, with the v6.2.11 and v6.2.12 switches bound."""
    agent = t11._bound_pacer(hold_pace=hold_pace)
    agent.research_v6212_pace_defer = defer
    agent._v6212_counts = {}
    ns = t11._clip_ns()
    for name in ("_v6212_count",):
        exec(compile(ast.Module(body=[ast.parse(_simple(name)).body[0]], type_ignores=[]), "<v6212>", "exec"), ns)
        setattr(type(agent), name, ns[name])
    return agent


def test_a_book_held_through_the_interval_steps_on_the_first_flat_request():
    agent = _pacer()
    agent._v625_clip(7, c5._State(0), c5.facts(), mid=300.0)                          # opens the sample flat
    agent._traded = 1_000.0                                                           # a quarter of the pace
    assert agent._v625_clip(7, c5._State(300 * S), c5.facts(net_base=0.5), mid=300.0) == 0.25
    assert agent._v625_clip(7, c5._State(600 * S), c5.facts(net_base=0.5), mid=300.0) == 0.25
    assert "clip_up" not in agent._v625_counts                                        # nothing stepped while held
    assert agent._v625_pace[7].sampled_ns == 0 and agent._v625_pace[7].volume == 0.0  # the sample kept running
    # flat again: one interval of this book's own pace has elapsed, so the deferred step is taken
    assert agent._v625_clip(7, c5._State(900 * S), c5.facts(), mid=300.0) == 0.5
    assert agent._v625_counts.get("clip_up") == 1
    assert agent._v6212_counts.get("pace_deferred") == 2 and agent._v6211_counts.get("pace_held") == 2


def test_v6211_discards_the_sample_and_the_same_book_never_steps():
    agent = _pacer(defer=False)
    agent._v625_clip(7, c5._State(0), c5.facts(), mid=300.0)
    agent._traded = 1_000.0
    for t in (300, 600):
        agent._v625_clip(7, c5._State(t * S), c5.facts(net_base=0.5), mid=300.0)
    assert agent._v625_pace[7].sampled_ns == 600 * S                                  # re-seeded every hold
    assert agent._v625_clip(7, c5._State(900 * S), c5.facts(), mid=300.0) == 0.25     # only 300 s of sample
    assert "clip_up" not in agent._v625_counts and not agent._v6212_counts


def test_a_held_request_still_quotes_one_minimum_order_and_leaves_the_clip_alone():
    agent = _pacer()
    agent._v625_clip(7, c5._State(0), c5.facts(), mid=300.0)
    agent._traded = 1_000.0
    agent._v625_clip(7, c5._State(900 * S), c5.facts(), mid=300.0)                    # the book steps to 0.5
    assert agent._v625_pace[7].clip == 0.5
    # a fill later it holds 0.5 base: the adding side is one minimum order and the clip does not move
    assert agent._v625_clip(7, c5._State(1_800 * S), c5.facts(net_base=0.5), mid=300.0) == 0.25
    assert agent._v625_pace[7].clip == 0.5 and agent._v625_counts.get("clip_up") == 1


def test_a_book_over_the_pace_steps_down_when_it_is_flat_again():
    agent = _pacer()
    agent._v625_clip(7, c5._State(0), c5.facts(), mid=300.0)
    agent._traded = 1_000.0
    agent._v625_clip(7, c5._State(900 * S), c5.facts(), mid=300.0)                    # 0.25 -> 0.5
    agent._traded = 100_000.0                                                         # far over the pace
    assert agent._v625_clip(7, c5._State(1_500 * S), c5.facts(net_base=1.0), mid=300.0) == 0.25   # held
    assert agent._v625_clip(7, c5._State(1_800 * S), c5.facts(), mid=300.0) == 0.25
    assert agent._v625_counts.get("clip_down") == 1


def test_the_hold_still_wins_when_r2_is_off():
    # Without R2 there is no held branch at all, so v6.2.12 changes nothing: the book steps while held.
    agent = _pacer(hold_pace=False)
    agent._v625_clip(7, c5._State(0), c5.facts(), mid=300.0)
    agent._traded = 1_000.0
    assert agent._v625_clip(7, c5._State(600 * S), c5.facts(net_base=2.0), mid=300.0) == 0.5
    assert not agent._v6212_counts


# ---- 3. wiring --------------------------------------------------------------------------------------

def test_the_call_site_defers_inside_the_r2_branch():
    src = _simple("_v625_clip")
    held = src.index("v6211_held_book(")
    assert src.index('defer = bool(getattr(self, "research_v6212_pace_defer", False))') > held
    assert src.index("pace.sampled_ns, pace.volume = v6212_sample_after_hold(") < src.index("obs = v625_observed_rate(")
    assert src.index('self._v6212_count("pace_deferred")') < src.index('self._v6211_count("pace_held")')


def test_switch_default_on_declared_and_reported():
    assert 'getattr(self.config, "research_v6212_pace_defer", True)' in SIMPLE
    assert "research_v6212_pace_defer=1" in LAUNCHER
    assert "pace_defer_on=int(" in SIMPLE and "pace_defer=self._v6212_snapshot()," in SIMPLE
    assert "from research_v6212_pace_defer import (" in SIMPLE


def test_version_and_launcher_arm():
    assert 'SIMPLE_POLICY_VERSION = "strategy1_direct_v6_2_15"' in SIMPLE
    assert "strategy1_direct_v6_2_14)" in LAUNCHER and "V6211_BUILD=1; V6212_BUILD=1 ;;" in LAUNCHER
    assert "strategy1_direct_v6_2_11)" in LAUNCHER                # the previous arm stays
    assert 'echo "[preflight] v6.2.12 pace defer PASS"' in LAUNCHER
    assert "tests/test_research_v6_2_12_pace_defer.py" in LAUNCHER
