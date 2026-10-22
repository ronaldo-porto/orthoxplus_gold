"""v6.38 (k123): the structural undo every version test applies before it compares a module with an earlier build.

v6.38 = v6.37 FROZEN_1 + U2 stage A (scratchpad k123/CHANGES.md).  Its changes are of three kinds, and only the first is
an addition the older strips could take out the usual way:
  (1) additions behind research_v638_fast_path -- Simple's import of research_v6_38_fast_path, its two init statements and
      its six ``if getattr(self, "research_v638_fast_path", False):`` branches (each replaced by its else branch: the
      original statements); the protocol's _V638_* / _v638_* names, its ``if _V638_TRADES_ONLY:`` branches (each replaced
      by its else branch) and the five LazyBook methods v6.38 adds;
  (2) names nothing read, taken out (item 2): 17 imported names of Simple, three Simple methods and the exit ledger's
      note_action -- put back here from their v6.37 source;
  (3) the source defaults of three refuted switches turned off (item 1: four getattr defaults) -- turned back on.
``undo(tree)`` applies all three to a parsed Simple, protocol or exit ledger (any other module is left as it is), in place,
and returns the tree: then it is the AST of v6.37 FROZEN_1's file (tests/test_research_v6_38.py pins that against v6.37's
digests, per interpreter).  It is idempotent, so every version's strip may apply it.  STRUCTURAL."""
from __future__ import annotations

import ast

SWITCH = "research_v638_fast_path"
V638_MODULE = "research_v6_38_fast_path"
SIMPLE_CLASS = "Strategy1_Research_Simple"
# (1) v6.38's own names in Simple's init and the five LazyBook methods it adds to the protocol
V638_INIT_TARGETS = frozenset({"research_v638_fast_path", "_v6_38_switches"})
V638_LAZYBOOK_METHODS = frozenset({"_v638_full", "_v638_parse", "trades", "orders", "cancellations"})
# (3) the four defaults: (receiver, key) of getattr(<receiver>, key, False) -> True
V637_TRUE_DEFAULTS = frozenset({("self.config", "research_v627_balance_gate"), ("self.config", "research_v63_fee_cap"),
                                ("self.config", "research_v634_class1_entry"), ("self", "research_v627_balance_gate")})
# (2) v6.37's alias list of each of Simple's import statements v6.38 took unused names out of
V637_IMPORTS = {'research_direct_quality': [('COLD_START_TAKER_RATE', None),
                             ('DIRECT_QUALITY_VERSION', None),
                             ('MIGRATED_QUALITY_INITIAL_WEIGHT', None),
                             ('MIGRATED_QUALITY_FULL_WEIGHT_SAMPLES', None),
                             ('MIGRATED_QUALITY_GLOBAL_FULL_WEIGHT_SAMPLES', None),
                             ('MakerLifecycleStats', None),
                             ('maker_quality_adjustment', None),
                             ('maker_realization_cost_estimate', None)],
 'research_direct_execution_quality': [('DIRECT_DUST_EXEMPT_CAP', None),
                                       ('DIRECT_EXECUTION_QUALITY_VERSION', None),
                                       ('DIRECT_MAKER_MAX_TOUCH_IMPROVEMENT_BPS', None),
                                       ('cap_maker_quote_geometry', None),
                                       ('dust_exempt_count', None),
                                       ('effective_total_open_books', None)],
 'research_position_exit': [('ACTION_MAKER_EXIT', None),
                            ('ACTION_PARK_EXIT', None),
                            ('ACTION_TAKER_EXIT', None),
                            ('ACTION_WAIT', None),
                            ('BAND_ABSOLUTE', None),
                            ('new_exposure_allowed', None)],
 'research_direct_exposure': [('DIRECT_EXPOSURE_VERSION', None),
                              ('add_order_to_batch', None),
                              ('outstanding_reservation', None),
                              ('worst_case_abs_inventory', None)],
 'research_direct_inventory_truth': [('A195_INVENTORY_TRUTH_VERSION', None),
                                     ('A195_MAX_SEED_ABS_BASE', None),
                                     ('A195_MAX_SEED_BOOKS', None),
                                     ('SEED_LEGACY_DUST', None),
                                     ('SEED_REAL', None),
                                     ('build_seed_plan', None),
                                     ('legacy_dust_ceiling_bonus', None)],
 'research_v61_lot_floor': [('REWRITE_IDLE_TO_MAKER', 'V61_REWRITE_IDLE'),
                            ('fifo_close_net_bps', 'v61_fifo_close_net_bps'),
                            ('REWRITE_NONE', 'V61_REWRITE_NONE'),
                            ('REWRITE_TAKER_TO_MAKER', 'V61_REWRITE_TAKER'),
                            ('V61_LOT_FLOOR_VERSION', None),
                            ('V61_STATE_EVERY_TICKS', None),
                            ('floor_price', 'v61_floor_price'),
                            ('floored_close_price', 'v61_floored_close_price'),
                            ('head_lot', 'v61_head_lot'),
                            ('rewrite_exit_action', 'v61_rewrite_exit_action'),
                            ('taker_close_pnl', 'v61_taker_close_pnl')],
 'research_v625_cap_paced': [('BAND_CLIPS', 'V625_BAND_CLIPS'),
                             ('PACE_PERIOD_NS', 'V625_PACE_PERIOD_NS'),
                             ('PACE_SAMPLE_NS', 'V625_PACE_SAMPLE_NS'),
                             ('REASON_BAND', 'V625_REASON_BAND'),
                             ('REASON_CAP_RESERVE', 'V625_REASON_CAP_RESERVE'),
                             ('REASON_EXIT_SIDE', 'V625_REASON_EXIT_SIDE'),
                             ('REASON_OK', 'V625_REASON_OK'),
                             ('SIDE_BUY', 'V625_SIDE_BUY'),
                             ('SIDE_SELL', 'V625_SIDE_SELL'),
                             ('V625_CAP_PACED_VERSION', None),
                             ('BookPace', 'V625BookPace'),
                             ('band_for', 'v625_band_for'),
                             ('cap_reserve_ok', 'v625_cap_reserve_ok'),
                             ('clip_ceiling', 'v625_clip_ceiling'),
                             ('lots_of', 'v625_lots_of'),
                             ('with_cap_ok', 'v625_with_cap_ok'),
                             ('observed_rate', 'v625_observed_rate'),
                             ('pace_snapshot', 'v625_pace_snapshot'),
                             ('pace_target_rate', 'v625_pace_target_rate'),
                             ('paced_clip', 'v625_paced_clip'),
                             ('sides_verdict', 'v625_sides_verdict')],
 'research_v626_balanced_maker': [('REASON_SIDE_OWNED', 'V626_REASON_SIDE_OWNED'),
                                  ('REASON_SURPLUS', 'V626_REASON_SURPLUS'),
                                  ('V626_BALANCED_MAKER_VERSION', None),
                                  ('balance_ratio', 'v626_balance_ratio'),
                                  ('book_blocked', 'v626_book_blocked'),
                                  ('median_budget', 'v626_median_budget'),
                                  ('open_book_caps', 'v626_open_book_caps'),
                                  ('side_already_instructed', 'v626_side_already_instructed'),
                                  ('side_clips', 'v626_side_clips'),
                                  ('skip_reason', 'v626_skip_reason'),
                                  ('spend_allowed', 'v626_spend_allowed')],
 'research_v627_maker_ceiling': [('BALANCE_TARGET', 'V627_BALANCE_TARGET'),
                                 ('V627_MAKER_CEILING_VERSION', None),
                                 ('balance_gate_sides', 'v627_balance_gate_sides'),
                                 ('band_caps', 'v627_band_caps'),
                                 ('book_balance', 'v627_book_balance'),
                                 ('bounded_ceiling', 'v627_bounded_ceiling'),
                                 ('cap_absorption_clip', 'v627_cap_absorption_clip'),
                                 ('pace_rewound', 'v627_pace_rewound')],
 'research_v610_paced_bound': [('BEHIND_MAX_CLIPS_DEFAULT', 'V610_BEHIND_MAX_CLIPS_DEFAULT'),
                               ('BEHIND_MAX_CLIPS_OFF', 'V610_BEHIND_MAX_CLIPS_OFF'),
                               ('V610_PACED_BOUND_VERSION', None),
                               ('behind_clips', 'v610_behind_clips'),
                               ('bound_clips', 'v610_bound_clips'),
                               ('caps_clips', 'v610_caps_clips')],
 'research_v612_reduce_depth': [('REDUCE_DEPTH_FACTOR', 'V612_REDUCE_DEPTH_FACTOR'),
                                ('V612_REDUCE_DEPTH_VERSION', None),
                                ('level0_depth', 'v612_level0_depth')]}
# (2) the names v6.38 took out of each of those statements
V638_REMOVED_IMPORTS = {'research_direct_quality': [('maker_realization_cost_estimate', None)],
 'research_direct_execution_quality': [('effective_total_open_books', None)],
 'research_position_exit': [('ACTION_PARK_EXIT', None)],
 'research_direct_exposure': [('worst_case_abs_inventory', None)],
 'research_direct_inventory_truth': [('SEED_LEGACY_DUST', None), ('legacy_dust_ceiling_bonus', None)],
 'research_v61_lot_floor': [('REWRITE_IDLE_TO_MAKER', 'V61_REWRITE_IDLE'),
                            ('REWRITE_TAKER_TO_MAKER', 'V61_REWRITE_TAKER')],
 'research_v625_cap_paced': [('REASON_BAND', 'V625_REASON_BAND')],
 'research_v626_balanced_maker': [('REASON_SIDE_OWNED', 'V626_REASON_SIDE_OWNED'),
                                  ('V626_BALANCED_MAKER_VERSION', None),
                                  ('balance_ratio', 'v626_balance_ratio')],
 'research_v627_maker_ceiling': [('V627_MAKER_CEILING_VERSION', None), ('book_balance', 'v627_book_balance')],
 'research_v610_paced_bound': [('BEHIND_MAX_CLIPS_OFF', 'V610_BEHIND_MAX_CLIPS_OFF')],
 'research_v612_reduce_depth': [('REDUCE_DEPTH_FACTOR', 'V612_REDUCE_DEPTH_FACTOR'),
                                ('V612_REDUCE_DEPTH_VERSION', None)]}
# (2) v6.37's source of each method v6.38 took out: (the method before it, the method after it in v6.38, source)
V637_SIMPLE_METHODS = (
    ('onOrderCancelled', '_a19_tick_size', '@staticmethod\ndef _a19_order_id(order) -> int | None:\n    try:\n        return int(getattr(order, "id", None))\n    except (TypeError, ValueError):\n        return None\n'),
    ('_a19_order_id', '_a19_tick_size', '@staticmethod\ndef _a19_order_price(order) -> float | None:\n    price = getattr(order, "price", None)\n    if price is None:\n        price = getattr(order, "limit_price", None)\n    try:\n        return float(price)\n    except (TypeError, ValueError):\n        return None\n'),
    ('_direct_outstanding_exposure_reservation', '_research_final_validate_instructions', 'def _direct_instruction_signed_qty(self, instruction, qty: float) -> float:\n    side = str(self._research_instruction_side(instruction) or "").lower()\n    return float(qty) if side in {"buy", "bid", "b", "0"} else -float(qty)\n'),
)
V637_LEDGER_METHODS = (
    ('sweep', 'live_orders', 'def note_action(self, book_id, side, action: str) -> None:\n    """Tag the newest order on a book/side with the rung that placed it."""\n    rows = self.live_orders(book_id, side=side)\n    if rows:\n        rows[0].action = str(action or "")\n'),
)


# v6.38's paths under agents/strategy and taos that no earlier build changed -- a git diff against an earlier commit
# lists them beside that build's own: the eight modules item 2 took names nothing read out of, and the 41 files item 4
# moved to archive/ (listed as deleted at their old path); and its new module (listed once committed)
V638_MODULES = frozenset({'agents/strategy/DetailedTemplateAgent.py',
 'agents/strategy/research_clean_authority.py',
 'agents/strategy/research_kappa_realization.py',
 'agents/strategy/research_kappa_state.py',
 'agents/strategy/research_projected_completion.py',
 'agents/strategy/research_quote_hysteresis.py',
 'agents/strategy/research_rolling_economics.py',
 'agents/strategy/research_rt_phase_timing.py'})
V638_ARCHIVED = frozenset({'agents/strategy/AdaptiveAgent.py',
 'agents/strategy/BaseStrategy.py',
 'agents/strategy/CustomTrainingAgent.py',
 'agents/strategy/DevAgent.py',
 'agents/strategy/FuturesAgent.py',
 'agents/strategy/ImbalanceAgent.py',
 'agents/strategy/KappaMMStrategyAgent.py',
 'agents/strategy/MovingHurstAgent.py',
 'agents/strategy/OrderOptionAgent.py',
 'agents/strategy/RandomMakerAgent.py',
 'agents/strategy/RandomTakerAgent.py',
 'agents/strategy/RevengAgent.py',
 'agents/strategy/SelfTradingAgent.py',
 'agents/strategy/Strategy3.py',
 'agents/strategy/Strategy4.py',
 'agents/strategy/Strategy5.py',
 'agents/strategy/Strategy6.py',
 'agents/strategy/adaptive_drift.py',
 'agents/strategy/adaptive_ev.py',
 'agents/strategy/adaptive_hjb.py',
 'agents/strategy/adaptive_persistence.py',
 'agents/strategy/adverse.py',
 'agents/strategy/analyze_strategy1_research_log.py',
 'agents/strategy/candidate_screen.py',
 'agents/strategy/entry_size.py',
 'agents/strategy/execution_hazard.py',
 'agents/strategy/execution_lifecycle.py',
 'agents/strategy/kappaMM_Improved.py',
 'agents/strategy/quote_hysteresis.py',
 'agents/strategy/realization.py',
 'agents/strategy/regime_v2.py',
 'agents/strategy/research_v629_flow_selection.py',
 'agents/strategy/rolling_scoring.py',
 'agents/strategy/run_base_strategy.sh',
 'agents/strategy/run_strategy3.sh',
 'agents/strategy/run_strategy3_test.sh',
 'agents/strategy/run_strategy4.sh',
 'agents/strategy/run_strategy4_test.sh',
 'agents/strategy/run_strategy5.sh',
 'agents/strategy/run_strategy5_test.sh',
 'agents/strategy/score_ev.py'})
V638_NEW = frozenset({"agents/strategy/research_v6_38_fast_path.py"})


# The launcher: v6.38's text edits as (v6.38 text, v6.37 text) pairs, applied in order, each found exactly once
V638_LAUNCHER_EDITS = (
    ('\n# v6.38.  research_v638_fast_path: U2 stage A (scratchpad k115 probes P5 / P1 / P3, built in k123) -- the reply path made\n# faster with every decision as v6.37\'s.  P5: the protocol validates a book\'s trade events only, order / cancellation\n# events stay the wire dicts (every reader on the agent\'s path skips non-trades; Book.trades / .orders / .cancellations\n# and parse() still get every event as a model) -- the protocol\'s own switch (_V638_TRADES_ONLY in\n# taos/im/protocol/models.py, shipped off there because other agents read every event as a model), written by the\n# agent\'s constructor before any request (research_v6_38_fast_path); P1: the A1.9.0.2 resting-exit observer stops after\n# its ledger sweep (its per-book shadow pass is measurement only); P3: the four trade walkers read one parse of each\n# book\'s events a request.  Each sits behind "if switch: new, else: the original".  OBSERVED (k115, interleaved A/B on\n# real clocks): P5 -13.0, P1 -7.1, P3 -1.7 ms a request; identity runs on a virtual clock (k123): every response and\n# every persisted session byte-identical to v6.37\'s, the switch on and off.  research_enable_fill_hazard=0 (P2L) ships\n# with it: under the shipped config the fill-hazard model\'s predictions feed telemetry rows only (k123: code reading +\n# k115 coverage + identity runs).  STRUCTURAL: each change keeps what its original computed for every reader on the\n# agent\'s path (k123 audit; tests/test_research_v6_38.py).  research_v638_fast_path ships 1 but may be 0 (off: v6.37\'s\n# code on every path, by a PARAMS edit, no rebuild); the block accepts exactly one of =0 / =1 for each key and prints\n# their state.\nif [[ "${V6_38_BUILD:-0}" == "1" ]]; then\n  [[ -f "$AGENT_PATH/research_v6_38_fast_path.py" ]] || {\n    echo "ERROR: v6.38 the fast-path module is missing." >&2\n    exit 1\n  }\n  for _v638_pat in \\\n      \'from research_v6_38_fast_path import (\' \\\n      \'self.research_v638_fast_path = self._as_bool(getattr(self.config, "research_v638_fast_path", True))\' \\\n      \'self._v6_38_switches = v6_38_apply(self.research_v638_fast_path)\' \\\n      \'v6_38_trades_reset()\' \\\n      \'side = v6_38_last_taker_side(getattr(book, "events", None))\' \\\n      \'trades = list(v6_38_trades_of(getattr(book, "events", None)))\'; do\n    grep -qF -- "$_v638_pat" "$AGENT_PATH/Strategy1_Research_Simple.py" || {\n      echo "ERROR: v6.38 the agent lacks: $_v638_pat" >&2\n      exit 1\n    }\n  done\n  _v638_n=$(grep -cF -- \'((int(raw_id), v6_38_feed(getattr(book, "events", None))) for raw_id, book in books.items()),\' \\\n    "$AGENT_PATH/Strategy1_Research_Simple.py" || true)\n  [[ "$_v638_n" == "2" ]] || {\n    echo "ERROR: v6.38 the v6.2 and v6.2.11 mirrors do not both read the shared parse (${_v638_n} feeds)." >&2\n    exit 1\n  }\n  _v638_n=$(grep -cF -- \'if getattr(self, "research_v638_fast_path", False):\' "$AGENT_PATH/Strategy1_Research_Simple.py" || true)\n  [[ "$_v638_n" == "6" ]] || {\n    echo "ERROR: v6.38 the agent reads its switch at ${_v638_n} sites, not the 6 of P1 + P3." >&2\n    exit 1\n  }\n  for _v638_pat in \\\n      \'("taos.im.protocol.models", "_V638_TRADES_ONLY"),\' \\\n      \'setattr(module, switch, value)\' \\\n      \'def trades_of(events) -> list:\' \\\n      \'def feed(events):\' \\\n      \'def last_taker_side(events):\'; do\n    grep -qF -- "$_v638_pat" "$AGENT_PATH/research_v6_38_fast_path.py" || {\n      echo "ERROR: v6.38 the fast-path module lacks: $_v638_pat" >&2\n      exit 1\n    }\n  done\n  for _v638_pat in \\\n      \'_V638_TRADES_ONLY = False\' \\\n      \'parsed_events = _v638_events(raw_events)\' \\\n      \'return [trade(e) if e.get("y") == "t" else e for e in raw_events]\' \\\n      \'for t in self._v638_full() if t.type ==\'; do\n    grep -qF -- "$_v638_pat" "$SCRIPT_DIR/taos/im/protocol/models.py" || {\n      echo "ERROR: v6.38 the protocol lacks: $_v638_pat" >&2\n      exit 1\n    }\n  done\n  _v638_re=\' research_v638_fast_path=(0|1) \'\n  [[ " $PARAMS " =~ $_v638_re ]] || {\n    echo "ERROR: v6.38 build without research_v638_fast_path=0 or 1 in PARAMS (the fast path\'s state must be explicit)." >&2\n    exit 1\n  }\n  _v638_fp="${BASH_REMATCH[1]}"\n  _v638_n=$(tr \' \' \'\\n\' <<< "$PARAMS" | grep -c \'^research_v638_fast_path=\' || true)\n  [[ "$_v638_n" == "1" ]] || {\n    echo "ERROR: v6.38 build with research_v638_fast_path given ${_v638_n} times in PARAMS (exactly once: 0 or 1)." >&2\n    exit 1\n  }\n  _v638_re=\' research_enable_fill_hazard=(0|1) \'\n  [[ " $PARAMS " =~ $_v638_re ]] || {\n    echo "ERROR: v6.38 build without research_enable_fill_hazard=0 or 1 in PARAMS (P2L\'s state must be explicit)." >&2\n    exit 1\n  }\n  _v638_fh="${BASH_REMATCH[1]}"\n  _v638_n=$(tr \' \' \'\\n\' <<< "$PARAMS" | grep -c \'^research_enable_fill_hazard=\' || true)\n  [[ "$_v638_n" == "1" ]] || {\n    echo "ERROR: v6.38 build with research_enable_fill_hazard given ${_v638_n} times in PARAMS (exactly once: 0 or 1)." >&2\n    exit 1\n  }\n  if [[ "$_v638_fp" == "1" ]]; then _v638_state="on"; else _v638_state="off"; fi\n  if [[ "$_v638_fh" == "1" ]]; then _v638_fh_state="on"; else _v638_fh_state="off"; fi\n  echo "[preflight] v6.38 fast path: ${_v638_state}"\n  echo "[preflight] v6.38 fill hazard: ${_v638_fh_state}"\n  echo "[preflight] v6.38 fast path PASS (${_v638_state}: the protocol\'s trade-only events, the A1.9.0.2 observer\'s shadow pass skipped and one trade parse a request, each behind its switch, written by the agent before any request; every decision as v6.37\'s; fill hazard ${_v638_fh_state})"\nfi\n',
     ''),
    ('; V6_37_BUILD=0; V6_38_BUILD=0\n',
     '; V6_37_BUILD=0\n'),
    ('; V6_37_BUILD=1; V6_38_BUILD=1\n',
     '; V6_37_BUILD=1\n'),
    ('      tests/test_research_v6_37.py \\\n      tests/test_research_v6_38.py \\\n',
     '      tests/test_research_v6_37.py \\\n'),
    ('research_v637_fast_path=1 \\\nresearch_enable_fill_hazard=0 \\\nresearch_v638_fast_path=1"\n',
     'research_v637_fast_path=1"\n'),
    ('research_a1921_severity_priority_enabled=1 \\\nresearch_a194_rebate_conjunction_enabled=1 \\\n',
     'research_a1921_severity_priority_enabled=1 \\\nresearch_a193_breadth_admission_enabled=1 \\\nresearch_a194_rebate_conjunction_enabled=1 \\\n'),
    ('  # v6.38: the research_a193_breadth_admission_enabled PARAMS check is retired with its key: no code ever read the key\n  # from the configuration (_a193_enabled() reads an attribute nothing assigns, default True), so the key was inert.\n',
     '  [[ "$PARAMS" == *"research_a193_breadth_admission_enabled=1"* ]] || {\n    echo "ERROR: A1.9.3 build without research_a193_breadth_admission_enabled=1" >&2\n    echo "       in PARAMS. The engine would report a1_9_3 while leaving every" >&2\n    echo "       breadth-critical book unreachable -- A1.9.2.1 under a new name." >&2\n    exit 1\n  }\n'),
    ('      \'self.research_v634_class1_entry = self._as_bool(getattr(self.config, "research_v634_class1_entry", False))\' \\\n',
     '      \'self.research_v634_class1_entry = self._as_bool(getattr(self.config, "research_v634_class1_entry", True))\' \\\n'),
)


def launcher_v637(text):
    """v6.38's launcher text with v6.38's edits undone: v6.37 FROZEN_1's launcher, byte for byte (tests/test_research_v6_38.py
    pins its sha256).  Raises if an edit is not found exactly once (then this is not v6.38's launcher)."""
    for new, old in V638_LAUNCHER_EDITS:
        if text.count(new) != 1:
            raise AssertionError("v6.38 launcher edit not found exactly once: %r" % new[:80])
        text = text.replace(new, old)
    return text


def _class(tree, name):
    return next((n for n in getattr(tree, "body", ()) if isinstance(n, ast.ClassDef) and n.name == name), None)


def _is_switch_test(node):
    """``getattr(self, "research_v638_fast_path", False)``: v6.38's switch as Simple reads it."""
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) == 3 and isinstance(node.args[0], ast.Name) and node.args[0].id == "self"
            and isinstance(node.args[1], ast.Constant) and node.args[1].value == SWITCH)


def _names_v638_protocol(node):
    return any(isinstance(n, ast.Name) and n.id.startswith(("_V638_", "_v638_")) for n in ast.walk(node))


def _stmt_lists(node):
    for field in ("body", "orelse", "finalbody"):
        value = getattr(node, field, None)
        if isinstance(value, list) and all(isinstance(v, ast.stmt) for v in value):
            yield node, field
    for h in getattr(node, "handlers", None) or ():
        yield h, "body"
    for c in getattr(node, "cases", None) or ():
        yield c, "body"


def _rewrite(node, drop_if):
    """Every statement list under ``node``: an ``if`` that ``drop_if`` names is replaced by its else branch (recursively)."""
    for owner, field in list(_stmt_lists(node)):
        out = []
        for st in getattr(owner, field):
            while isinstance(st, ast.If) and drop_if(st.test):
                for inner in st.orelse[:-1]:
                    _rewrite(inner, drop_if)
                    out.append(inner)
                if not st.orelse:
                    st = None
                    break
                st = st.orelse[-1]
            if st is None:
                continue
            _rewrite(st, drop_if)
            out.append(st)
        setattr(owner, field, out)


def _put_back(cls, entries):
    """Each removed method back right after its v6.37 predecessor -- only where that predecessor is directly followed
    by the method v6.38 has there (any other class shape is left as it is)."""
    names = [getattr(n, "name", None) for n in cls.body]
    for prev, nxt, src in entries:
        fn = ast.parse(src).body[0]
        if fn.name in names or prev not in names:
            continue
        i = names.index(prev) + 1
        if i >= len(names) or names[i] != nxt:
            continue
        cls.body.insert(i, fn)
        names.insert(i, fn.name)


def _undo_simple(tree, cls):
    body = []
    for st in tree.body:
        if isinstance(st, ast.ImportFrom) and st.module == V638_MODULE:
            continue
        if isinstance(st, ast.ImportFrom) and st.module in V637_IMPORTS:
            full = V637_IMPORTS[st.module]
            gone = V638_REMOVED_IMPORTS[st.module]
            if [(a.name, a.asname) for a in st.names] == [x for x in full if x not in gone]:     # exactly v6.38's list
                st.names = [ast.alias(name=n, asname=a) for n, a in full]
        body.append(st)
    tree.body = body
    _put_back(cls, V637_SIMPLE_METHODS)
    _rewrite(cls, _is_switch_test)
    for fn in cls.body:
        if isinstance(fn, ast.FunctionDef) and fn.name == "_init_build_switches":
            fn.body = [st for st in fn.body if not (isinstance(st, ast.Assign) and len(st.targets) == 1
                                                    and isinstance(st.targets[0], ast.Attribute)
                                                    and isinstance(st.targets[0].value, ast.Name)
                                                    and st.targets[0].value.id == "self"
                                                    and st.targets[0].attr in V638_INIT_TARGETS)]
    for n in ast.walk(cls):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "getattr" and len(n.args) == 3
                and isinstance(n.args[1], ast.Constant) and isinstance(n.args[2], ast.Constant)
                and n.args[2].value is False and (ast.unparse(n.args[0]), n.args[1].value) in V637_TRUE_DEFAULTS):
            n.args[2] = ast.Constant(value=True)


def _undo_protocol(tree):
    body = []
    for st in tree.body:
        if isinstance(st, ast.FunctionDef) and st.name.startswith("_v638_"):
            continue
        if isinstance(st, (ast.Assign, ast.AnnAssign)) and _names_v638_protocol(st):
            continue
        if isinstance(st, ast.ClassDef) and st.name == "LazyBook":
            st.body = [n for n in st.body if not (isinstance(n, ast.FunctionDef) and n.name in V638_LAZYBOOK_METHODS
                                                  and (n.name.startswith("_v638_") or _names_v638_protocol(n)))]
        body.append(st)
    tree.body = body
    _rewrite(tree, _names_v638_protocol)


def undo(tree):
    """v6.38's changes undone in a parsed module, in place (see the module docstring); returns the tree."""
    cls = _class(tree, SIMPLE_CLASS)
    if cls is not None:
        _undo_simple(tree, cls)
    if _class(tree, "LazyBook") is not None:
        _undo_protocol(tree)
    led = _class(tree, "DirectExitLedger")
    if led is not None:
        _put_back(led, V637_LEDGER_METHODS)
    return tree


def parse(text):
    """``ast.parse(text)`` with v6.38 undone: v6.37's AST of the same file."""
    return undo(ast.parse(text))
