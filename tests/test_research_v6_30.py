"""v6.30: the identity profile -- research_v630_deep_tier (D1) and research_v630_s9_lookback (D2).

v6.29 + two switches, default on.  Each UID draws per book, from its OWN UID only, the pre-registered profile
orthox-profile-v1 (scratchpad k79/spec/profile_v1.py, sha256 31d08ace...168c, frozen 10-14 06:08 JST):

    value = VALUES[dim][ int(SHA-256('orthox-profile-v1|' + dim + '|' + str(uid) + '|' + str(book)).hexdigest()[:8], 16)
                         mod k ],   D1 in (0, 1), D2 in (1, 2).

D1: on a tier-1 book every deep level targets exactly one venue tick further from the mid than v6.29 would -- level 0
(the vacuum order too), the v6.12 reducing order, the ladder's levels 1 and 2, v6.6's spacing target and the v6.8 paper
record -- kept inside v6.3.3's keep band (v6.29's price otherwise: clamped, never shallower).  D2: S9's look-back is
m x W by v6.29's reference rule, the history keeping what the longest look-back of the request needs.  The UID is the one
the agent was constructed with, fixed once per process; unknown, negative or changed: both switches run v6.29.

No test here touches the host's caches, the network or a live tree, and nothing is imported from the scratchpad: the
agent is t629's harness (t627's, with t616's injected feed) with v6.30's methods, and its v6.30 state comes from the real
_init_build_switches statements; the offline expectations were computed from the spec file and pasted as literals.
"""
import ast
import difflib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import types
from collections import deque
from decimal import Decimal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v616_fair_value as fv  # noqa: E402
import research_v617_touch_quote as tq  # noqa: E402
import research_v630_identity_profile as ip  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v64_board as vb  # noqa: E402
import research_v6_29_touch_move as tm  # noqa: E402
import test_research_v6_16 as t616  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_22 as t622  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
import test_research_v6_24 as t624  # noqa: E402
import test_research_v6_25 as t625  # noqa: E402
import test_research_v6_26 as t626  # noqa: E402
import test_research_v6_27 as t627  # noqa: E402
import test_research_v6_28 as t628  # noqa: E402
import test_research_v6_29 as t629  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_v630_identity_profile.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
_dbg = extractor((STRATEGY / "Strategy1_Debug.py").read_text(), cls_name="Strategy1_Debug")
S = 1_000_000_000
NOW = t629.NOW
SIM = t629.SIM
ABOVE, BELOW = t629.ABOVE, t629.BELOW
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
W20 = fv.MAX_STALE_SIM_S
SPEC_SHA256 = "31d08ace5b99b5e3ac33e567a06ad08ef914d0edae275e7168e2246ee264168c"   # k79/spec/profile_v1.py
D1_LINE = 'self.research_v630_deep_tier = self._as_bool(getattr(self.config, "research_v630_deep_tier", True))'
D2_LINE = 'self.research_v630_s9_lookback = self._as_bool(getattr(self.config, "research_v630_s9_lookback", True))'
FIX_LINE = 'self._v6_30_identity.fix(getattr(self, "uid", None))'
HISTORY_LINE = "self._v6_29_pages = v6_30_page_history(V6_29PageHistory)"
REQUEST_LINE = "self._v6_30_request(state)"
TIER_LINE = "v6_30_tier = self._v6_30_tier(book_id)"
PAPER_LINE = "self._v6_30_paper(deep, book_id)"
DECIDE_LINE = "v6_30_decision = self._v6_30_decide(pages, book_id, fp, now_ts, lookback)"
REPORT_LINE = "self._v6_30_report()"
V6_30_NAMES = {"V6_30_D1": ip.D1, "V6_30_D2": ip.D2, "V6_30Identity": ip.Identity, "V6_30KeepHorizon": ip.KeepHorizon,
               "V6_30_PROFILE_VERSION": ip.PROFILE_VERSION,
               "V6_30_IDENTITY_PROFILE_VERSION": ip.V6_30_IDENTITY_PROFILE_VERSION,
               "V6_30_CLAMPED_BAND": ip.CLAMPED_BAND, "V6_30_CLAMPED_TOUCH": ip.CLAMPED_TOUCH, "V6_30_DEEPER": ip.DEEPER,
               "v6_30_addressee": ip.addressee, "v6_30_deeper_target": ip.deeper_target,
               "v6_30_page_history": ip.page_history, "v6_30_paper_for": ip.paper_for}
EXTRA = dict(t629.EXTRA, **V6_30_NAMES)
METHODS = ("_v6_30_request", "_v6_30_tier", "_v6_30_deeper", "_v6_30_placed", "_v6_30_paper", "_v6_30_decide",
           "_v6_30_count", "_v6_30_report", "_v6_30_snapshot")
FIELDS = ("version", "profile", "deep_tier_on", "s9_lookback_on", "profile_on", "uid", "uid_source", "off_reason", "books",
          "book_min", "book_max", "table_sha256", "d1_tier1_books", "d2_m2_books", "max_m", "d1", "d2",
          "books_drawn_later", "deep_shifted", "deep_clamped", "deep_unchanged_error", "deep_by_level", "paper_tier1",
          "paper_tier0", "s9_m1_decisions", "s9_m1_gated", "s9_m1_no_history", "s9_m2_decisions", "s9_m2_gated",
          "s9_m2_no_history",
          "s9_by_m", "s9_keep_sim_s", "errors")

# Offline expectations (k81/build/offline_table.py: the spec file alone, its sha256 checked; the build NOT imported):
# per UID over books 0-127, the canonical-text sha256 (the digest V6_30_STATE logs), the tier-1 and m = 2 counts and the
# draws; and one sha256 over every UID 0-255's canonical text.  Boundary UIDs and the UIDs this host runs today: test
# data only -- the agent never reads a list of UIDs.
OFFLINE = {
    0: ("d921b237ef1347f80c50e7421341bfbe81ffbe76d6a2381c536c61311147ee9a", 62, 63),
    10: ("886105303655f51d3e06e00cc446358671a7fd276468bf52f9c3eb01517a36d0", 68, 69),
    78: ("963474b74f483d880c13fd262d3acb904acef6e2133d2e2f8a9592cd86ddd406", 66, 69),
    82: ("1ab4d51b2fb406f8a4ab3a651b607f7fcb71a5adbf8408ad0b1c4249a5afa038", 74, 63),
    105: ("90e58c1d805a43fe47039dd5f000d6eb77fc03a1f92a81a4c36ba445bb7586ff", 63, 63),
    115: ("6a66314527d091fa2815a5d8df84738204907be29878d2f4d44abf99ac23af10", 64, 60),
    161: ("8b70a402f7c63760f62ce6e11df527b844d5e2ca7233cc7ff7b6ba126be967c7", 66, 73),
    255: ("38b623583b4bedd3a0dfa97669d59caa10dccbd2e1f2598caa19c6ba4e03783a", 57, 67),
}
OFFLINE_DRAWS = {   # UID 115 and 0: D1 then D2, books 0-127
    115: ("00001110110110100100100100001111111111001011110101101011000011110011101001100111001000010001001000010110101011110100100011101000",
          "21211122121121221122111222122121212122121121111221112221212211222222211112121221221211211111122211211111121212221221221212111111"),
    0: ("11000110111101000111100001010011101000010001010010001100101101000101100100110101010101110111101010110101000011010010101011001001",
        "22222221111222112221211122121121121121212212222222111212121122121111112111222112222111121221122122112211221112111112221211222111"),
}
OFFLINE_ALL = "6abc1b260610cdad8d096a01cafa8c6c51e02fc5aa13aa1a2c98328c9940e055"


def _formula(dim, uid, book):
    """The spec's draw, written out again here: VALUES[dim][int(sha256('orthox-profile-v1|dim|uid|book')[:8], 16) % k]."""
    values = {"D1": (0, 1), "D2": (1, 2)}[dim]
    h = hashlib.sha256(("orthox-profile-v1|" + dim + "|" + str(uid) + "|" + str(book)).encode()).hexdigest()
    return values[int(h[:8], 16) % len(values)]


def _uid(d1, d2, book=3, other=None):
    """The first UID whose draws on ``book`` are (d1, d2) (and on ``other`` = (book, d1, d2) too): computed, not listed."""
    for uid in range(256):
        if (_formula("D1", uid, book), _formula("D2", uid, book)) != (d1, d2):
            continue
        if other is not None and (_formula("D1", uid, other[0]), _formula("D2", uid, other[0])) != other[1:]:
            continue
        return uid
    raise AssertionError((d1, d2, book, other))


T1M1, T1M2, T0M1, T0M2 = _uid(1, 1), _uid(1, 2), _uid(0, 1), _uid(0, 2)          # book 3's (D1, D2)


# ---- 1. the module: the pre-registered profile --------------------------------------------------------------------------

def test_the_module_states_the_pre_registered_version_and_value_sets_and_labels_its_evidence():
    assert ip.V6_30_IDENTITY_PROFILE_VERSION == "identity_profile_v6_30"
    assert ip.PROFILE_VERSION == "orthox-profile-v1" and (ip.D1, ip.D2) == ("D1", "D2")
    assert ip.VALUES == {"D1": (0, 1), "D2": (1, 2)}               # index 0: v6.29's behaviour
    assert set(ip.__all__) == {"CLAMPED_BAND", "CLAMPED_TOUCH", "D1", "D2", "DEEPER", "Identity", "KeepHorizon",
                               "OFF_CHANGED", "OFF_NEGATIVE", "OFF_UNKNOWN", "PROFILE_VERSION", "SOURCE_AGENT",
                               "SOURCE_STATE", "TierPaper", "UNREADABLE", "V6_30_IDENTITY_PROFILE_VERSION", "VALUES",
                               "addressee", "deeper_price", "deeper_target", "draw", "page_history", "paper_for",
                               "profile_table", "table_digest", "table_text", "uid_of"}
    assert (ip.DEEPER, ip.CLAMPED_BAND, ip.CLAMPED_TOUCH, ip.UNREADABLE) == ("deeper", "band", "touch", "unreadable")
    assert ip.REPRICE_HIGH is dl.REPRICE_HIGH
    assert SPEC_SHA256 in MODULE and "STRUCTURAL" in MODULE and "OBSERVED" in MODULE
    assert 'h = hashlib.sha256(f"{PROFILE_VERSION}|{dim}|{uid}|{book}".encode()).hexdigest()' in MODULE
    assert "return values[int(h[:8], 16) % len(values)]" in MODULE


def test_the_draw_is_the_formula_for_256_uids_by_128_books():
    """Property: every (uid, book, dim) of 256 x 128 x 2 is the formula, and profile_table / Identity agree with it."""
    for uid in range(256):
        table = ip.profile_table(uid, range(128))
        for book in range(128):
            want = (_formula("D1", uid, book), _formula("D2", uid, book))
            assert (ip.draw("D1", uid, book), ip.draw("D2", uid, book)) == want == table[book], (uid, book)
    ident = ip.Identity()
    ident.fix(77)
    ident.extend(range(128))
    assert ident.table == ip.profile_table(77, range(128))
    assert all(ident.value("D1", b) == _formula("D1", 77, b) and ident.value("D2", b) == _formula("D2", 77, b)
               for b in range(128))


def test_the_draws_and_digests_equal_the_offline_tables_of_the_spec():
    for uid, (digest, tier1, m2) in OFFLINE.items():
        table = ip.profile_table(uid, range(128))
        assert ip.table_digest(uid, table) == digest, uid
        assert sum(d1 for d1, _m in table.values()) == tier1 and sum(m == 2 for _d1, m in table.values()) == m2, uid
    for uid, (d1, d2) in OFFLINE_DRAWS.items():
        table = ip.profile_table(uid, range(128))
        assert "".join(str(table[b][0]) for b in range(128)) == d1, uid
        assert "".join(str(table[b][1]) for b in range(128)) == d2, uid
    text = "\n".join(ip.table_text(uid, ip.profile_table(uid, range(128))) for uid in range(256))
    assert hashlib.sha256(text.encode()).hexdigest() == OFFLINE_ALL
    t = ip.table_text(115, ip.profile_table(115, range(3)))
    assert t == "orthox-profile-v1|uid=115\n0|D1=0|D2=2\n1|D1=0|D2=1\n2|D1=0|D2=2"


def test_the_profile_has_no_uid_specific_branch_and_no_list_of_uids():
    """The module reads the UID only as a number to hash: no comparison with, membership in or indexing by a UID value,
    and no integer that could name one -- its numbers are the hash's 8 and 16, 0, 1 and the ns / tolerance scales."""
    tree = ast.parse(MODULE)
    ints = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, int)
            and not isinstance(n.value, bool)}
    assert ints <= {0, 1, 2, 8, 16}, ints
    floats = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, float)}
    assert floats <= {0.0, 1e9, 1e-6, 1e-9}, floats
    draw = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "draw")
    assert [a.arg for a in draw.args.args] == ["dim", "uid", "book"]
    assert not [n for n in ast.walk(draw) if isinstance(n, (ast.If, ast.Compare, ast.IfExp, ast.Subscript))
                and "uid" in ast.unparse(n)]
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and "uid" in ast.unparse(node) and any(
                    isinstance(c, ast.Constant) and c.value not in (0, None) for c in [node.left, *node.comparators]):
                raise AssertionError((fn.name, ast.unparse(node)))      # a UID compared with anything but 0 / None
            if isinstance(node, (ast.List, ast.Set, ast.Tuple)) and len(node.elts) > 2 and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, int) for e in node.elts):
                raise AssertionError((fn.name, ast.unparse(node)))


def test_the_profile_module_loads_no_lock_cache_socket_ipc_or_randomness():
    """Static: its imports are hashlib, math, operator, typing and v6.3.3's deep layer; its text names no host cache,
    lock, socket, process or RNG.  Run: importing it in a fresh interpreter loads none of them (v6.29's module, which
    reaches v6.16's SharedFeed and its fcntl, is never imported -- the agent hands its PageHistory in)."""
    tree = ast.parse(MODULE)
    imported = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add(n.module)
    assert imported == {"__future__", "hashlib", "math", "operator", "typing", "research_v633_deep_layer"}, imported
    code = "\n".join(ast.unparse(n) for n in tree.body[1:])        # the docstring may describe what is forbidden
    for word in ("fcntl", "flock", "socket", "/tmp", "orthoxplus_", "multiprocessing", "mmap", "subprocess", "random",
                 "secrets", "urllib", "open(", "os.", "time.", "thread", "uuid"):
        assert word not in code, word
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); before = set(sys.modules); "
             "import research_v630_identity_profile; new = set(sys.modules) - before; "
             "bad = sorted(m for m in new if m.split('.')[0] in ('fcntl', 'socket', 'urllib', 'http', 'multiprocessing', "
             "'mmap', 'subprocess', 'random', 'secrets', 'threading', 'research_v616_fair_value', "
             "'research_v6_29_touch_move', 'os')); print(sorted(m for m in new if m.startswith('research_'))); print(bad)")
    out = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(STRATEGY)], capture_output=True, text=True,
                         timeout=120, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"})
    assert out.returncode == 0, out.stderr
    loaded, bad = out.stdout.strip().splitlines()
    assert loaded == "['research_v630_identity_profile', 'research_v633_deep_layer']" and bad == "[]", out.stdout


def test_the_table_is_stable_across_restarts_processes_and_hash_seeds():
    """A restart draws the same table: another Identity, another interpreter, another PYTHONHASHSEED, another order of
    the books -- the same draws and digest."""
    a, b = ip.Identity(), ip.Identity()
    a.fix(115)
    b.fix(115)
    a.extend(range(128))
    b.extend(reversed(range(128)))
    assert a.table == b.table and a.digest == b.digest == OFFLINE[115][0]
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import research_v630_identity_profile as ip; "
             "i = ip.Identity(); i.fix(int(sys.argv[2])); i.extend(range(128)); print(i.digest)")
    for seed in ("0", "1", "12345"):
        out = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(STRATEGY), "115"], capture_output=True,
                             text=True, timeout=120, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                          "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"})
        assert out.returncode == 0 and out.stdout.strip() == OFFLINE[115][0], (seed, out.stderr)


def test_the_uid_is_fixed_once_and_off_when_unknown_negative_or_changed():
    ident = ip.Identity()
    ident.fix(115)
    assert (ident.uid, ident.source, ident.off) == (115, ip.SOURCE_AGENT, None)
    ident.fix(78)                                                    # once: a later value never re-fixes it
    assert ident.uid == 115
    assert ident.check(115, None) and ident.check(115, 115) and ident.off is None
    assert not ident.check(78, None) and ident.off == ip.OFF_CHANGED and not ident.check(115, 115)   # for good
    for agent_uid, state_uid in ((None, None), ("115", None), (115.0, None), (True, None), (None, "x")):
        i = ip.Identity()
        i.fix(agent_uid)
        assert i.uid is None and i.off is None, agent_uid                 # not known at init: the first request decides
        assert not i.check(agent_uid, state_uid) and i.off in (ip.OFF_UNKNOWN, ip.OFF_CHANGED), (agent_uid, state_uid)
    neg = ip.Identity()
    neg.fix(-1)                                                      # the miner's "hotkey not in the metagraph"
    assert neg.off == ip.OFF_NEGATIVE and not neg.check(5, 5) and neg.uid is None and neg.extend(range(4)) == 0
    late = ip.Identity()
    late.fix(None)
    assert late.check(None, 161) and (late.uid, late.source) == (161, ip.SOURCE_STATE)   # the state's addressee
    assert late.check(None, 161) and late.check(None, None) and late.check(161, 161)
    assert not late.check(None, 78) and late.off == ip.OFF_CHANGED
    first = ip.Identity()
    first.fix(None)
    assert first.check(82, 82) and (first.uid, first.source) == (82, ip.SOURCE_AGENT)
    assert not first.check(None, 82) and first.off == ip.OFF_CHANGED   # the agent's own UID gone
    mixed = ip.Identity()
    mixed.fix(10)
    assert not mixed.check(10, 78) and mixed.off == ip.OFF_CHANGED   # addressed to another UID
    neg_late = ip.Identity()
    neg_late.fix(None)
    assert not neg_late.check(-1, 5) and neg_late.off == ip.OFF_NEGATIVE
    for accounts, want in (({115: {}}, 115), ({0: {}}, 0), ({}, None), (None, None), ({1: {}, 2: {}}, None),
                           ({-1: {}}, None), ({"x": {}}, None), ([115], None)):
        assert ip.addressee(accounts) == want, accounts
    assert [ip.uid_of(v) for v in (0, 7, -1, True, 1.0, "3", None)] == [0, 7, None, None, None, None, None]


def test_the_table_is_drawn_once_and_a_book_first_seen_later_once_when_it_appears():
    ident = ip.Identity()
    ident.fix(115)
    assert ident.extend({3: "book", 5: "book", "7": "text key", -2: "bad", "x": "bad"}) == 3
    assert sorted(ident.table) == [3, 5, 7] and ident.extended == 0 and ident.max_m == 2
    digest = ident.digest
    assert ident.extend([3, 5, 7]) == 0 and ident.digest == digest              # nothing drawn again
    before = dict(ident.table)
    assert ident.extend(range(10)) == 7 and ident.extended == 7
    assert all(ident.table[b] == before[b] for b in before)                     # never changed
    assert ident.digest == ip.table_digest(115, ident.table) != digest
    assert ident.value("D1", 3) == _formula("D1", 115, 3) and ident.value("D3", 3) is None
    assert ident.value("D1", 11) is None and ident.value("D1", "3") is None
    snap = ident.snapshot()
    assert (snap["uid"], snap["books"], snap["book_min"], snap["book_max"], snap["books_drawn_later"]) == (115, 10, 0, 9, 7)
    assert snap["d1"] == "".join(str(_formula("D1", 115, b)) for b in range(10))
    assert snap["d2"] == "".join(str(_formula("D2", 115, b)) for b in range(10))
    assert snap["d1_tier1_books"] == snap["d1"].count("1") and snap["d2_m2_books"] == snap["d2"].count("2")
    whole = ip.Identity()                                            # counts that differ (UID 82: 74 tier-1, 63 at m = 2)
    whole.fix(82)
    whole.extend(range(128))
    snap = whole.snapshot()
    assert (snap["d1_tier1_books"], snap["d2_m2_books"]) == OFFLINE[82][1:] == (74, 63)
    assert snap["table_sha256"] == OFFLINE[82][0] and snap["books"] == 128
    ones = ip.Identity()
    ones.fix(T1M1)
    ones.extend([3])
    assert ones.max_m == 1                                           # every drawn book at m = 1: W, v6.29's prune


# ---- 2. D1's price and paper record -----------------------------------------------------------------------------------

def _deep(mid, depth, side, bid, ask, dec=2):
    return dl.deep_price(mid, depth, side, bid=bid, ask=ask, tick=10.0 ** -dec, decimals=dec)


def test_a_tier_1_target_is_exactly_one_venue_tick_further_from_the_mid_and_never_shallower():
    """Over many books, depths and decimals: the moved price is v6.29's price minus (a buy) / plus (a sell) one tick of
    10 ** -decimals, on the venue's grid, inside v6.3.3's keep band, behind the touch -- or v6.29's price itself."""
    moved = clamped = 0
    for dec in (1, 2, 3, 4):
        t = 10.0 ** -dec
        for k in range(1, 60):
            bid = round(100.0 + 0.37 * k * t * 10, dec)
            for spread in (1, 2, 3, 7):
                ask = round(bid + spread * t, dec)
                mid = 0.5 * (bid + ask)
                for depth in (0.5, 1.0, 1.5, 2.0, 2.4, 3.0, 4.0, 8.5, 10.0, 21.5, 40.0):
                    for side in (BUY, SELL):
                        p = _deep(mid, depth, side, bid, ask, dec)
                        q = ip.deeper_price(p, side, ref=mid, depth=depth, tick=t, decimals=dec)
                        if q == p:
                            clamped += 1
                            q1 = round(p - t if side == BUY else p + t, dec)
                            assert dl.needs_reprice(q1, mid, depth, t) or abs(q1 - mid) / t > 1.5 * depth - 1e-9
                            continue
                        moved += 1
                        assert abs(q - mid) / t <= 1.5 * depth - 1e-9                          # off the edge
                        assert q == round(p - t, dec) if side == BUY else q == round(p + t, dec)
                        assert abs(abs(q - p) - t) < 1e-9 * max(1.0, abs(p))
                        assert abs(q - mid) > abs(p - mid)                                  # further, never shallower
                        assert not dl.needs_reprice(q, mid, depth, t)                       # inside the band
                        assert (q < bid) if side == BUY else (q > ask)                      # behind the touch
                        assert round(q, dec) == q
    assert moved > 10_000 and clamped > 300, (moved, clamped)


def test_the_band_clamp_and_every_unreadable_input_keep_v629s_price():
    mid, t = 100.015, 0.01
    assert ip.deeper_price(99.99, BUY, ref=mid, depth=2.0, tick=t, decimals=2) == 99.99     # 3.5 ticks > 1.5 x 2
    assert ip.deeper_price(99.99, BUY, ref=mid, depth=3.0, tick=t, decimals=2) == 99.98     # 3.5 <= 4.5
    assert ip.deeper_price(100.04, SELL, ref=mid, depth=2.0, tick=t, decimals=2) == 100.04
    assert ip.deeper_price(99.80, BUY, ref=mid, depth=21.5, tick=t, decimals=2, ticks=0) == 99.80   # tier 0
    for bad in (dict(ref=None), dict(ref="x"), dict(depth=-1.0), dict(depth=float("nan")), dict(tick=0.0),
                dict(tick=None), dict(decimals=None), dict(decimals=-1), dict(decimals=2.0), dict(ticks=None),
                dict(ticks=True), dict(ticks=-1), dict(ticks=1.0)):
        kw = dict(ref=mid, depth=21.5, tick=t, decimals=2, ticks=1)
        kw.update(bad)
        assert ip.deeper_price(99.80, BUY, **kw) == 99.80, bad
    for price in (None, "x", float("inf"), True):
        assert ip.deeper_price(price, BUY, ref=mid, depth=21.5, tick=t, decimals=2) is price
    assert ip.deeper_price(99.80, "neither", ref=mid, depth=21.5, tick=t, decimals=2) == 99.80


def test_a_vacuum_order_moves_one_tick_toward_its_touch_and_stays_strictly_inside_it():
    """v6.4's vacuum price sits strictly inside a blown-out spread: one tick further from the mid stays inside the
    touch; a price one tick inside the touch already (the clamp) stays where it is."""
    for bid, ask in ((99.90, 100.20), (100.00, 100.20), (99.00, 101.00)):
        mid, t = 0.5 * (bid + ask), 0.01
        vac = vb.vacuum_depth(bid, ask, t)
        for side in (BUY, SELL):
            p = vb.vacuum_price(mid, vac, side, bid=bid, ask=ask, tick=t, decimals=2)
            q = ip.deeper_price(p, side, ref=mid, depth=vac, tick=t, decimals=2, inside=(bid, ask))
            assert q == (round(p - t, 2) if side == BUY else round(p + t, 2)), (bid, ask, side)
            assert bid < q < ask
    assert ip.deeper_price(100.01, BUY, ref=100.06, depth=5.0, tick=0.01, decimals=2, inside=(100.00, 100.12)) == 100.01
    assert ip.deeper_price(100.11, SELL, ref=100.06, depth=5.0, tick=0.01, decimals=2, inside=(100.00, 100.12)) == 100.11
    assert ip.deeper_price(100.04, BUY, ref=100.06, depth=5.0, tick=0.01, decimals=2, inside=(None, 100.12)) == 100.04
    target = lambda price, side, **kw: ip.deeper_target(price, side, **dict(dict(ref=100.06, depth=5.0, tick=0.01,
                                                                                  decimals=2), **kw))
    assert target(100.04, BUY, inside=(100.00, 100.12)) == (100.03, ip.DEEPER)
    assert target(100.01, BUY, inside=(100.00, 100.12)) == (100.01, ip.CLAMPED_TOUCH)
    assert target(100.11, SELL, inside=(100.00, 100.12)) == (100.11, ip.CLAMPED_TOUCH)
    assert target(100.04, BUY, inside=(None, 100.12)) == (100.04, ip.UNREADABLE)
    assert target(100.04, BUY, inside=5) == (100.04, ip.UNREADABLE)                  # an exception inside: unreadable
    assert target(99.99, BUY, ref=100.015, depth=2.0) == (99.99, ip.CLAMPED_BAND)
    assert target(None, BUY) == (None, ip.UNREADABLE) and target(100.04, "x") == (100.04, ip.UNREADABLE)


def test_a_moved_price_on_the_bands_exact_edge_always_clamps():
    """Where one tick more lands exactly on REPRICE_HIGH x depth (depth 2 with the mid on a tick, depth 3 with a half-tick
    mid, and the like), v6.3.3's needs_reprice would decide by rounding noise; deeper_target clamps every such case and
    moves every other, at price levels and decimal counts up to price / tick = 1e6 (the 1e-9-tick margin's range: the
    noise is ~4e-16 x price / tick; SN79's 300 / 0.01 is 3e4) -- judged in exact decimal arithmetic."""
    edge = moved = 0
    for dec in (1, 2, 3, 4):
        t = Decimal(1).scaleb(-dec)
        for base in ("1", "7.3", "29.57", "99.5", "100", "300", "1234.5", "9999.99"):
            if Decimal(base) / t > Decimal(10) ** 6:
                continue
            bid0 = Decimal(base).quantize(t)
            for spread in (1, 2, 3, 4, 5):
                bid, ask = bid0, bid0 + spread * t
                mid = (bid + ask) / 2
                for depth in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0):
                    for side in (BUY, SELL):
                        p = _deep(float(mid), depth, side, float(bid), float(ask), dec)
                        q, why = ip.deeper_target(p, side, ref=float(mid), depth=depth, tick=float(t), decimals=dec)
                        q1 = Decimal(f"{p:.{dec}f}") + (-t if side == BUY else t)
                        dist = abs(q1 - mid) / t
                        if dist >= Decimal("1.5") * Decimal(str(depth)):
                            assert (q, why) == (p, ip.CLAMPED_BAND), (dec, base, spread, depth, side, p)
                            edge += int(dist == Decimal("1.5") * Decimal(str(depth)))
                        elif dist >= Decimal("0.5") * Decimal(str(depth)):
                            assert why == ip.DEEPER and Decimal(f"{q:.{dec}f}") == q1, (dec, base, spread, depth, p)
                            moved += 1
    assert edge > 50 and moved > 500, (edge, moved)


def test_the_tier_paper_rests_its_paper_orders_one_tick_deeper_and_scores_them_with_v633s_own_arithmetic():
    """The same prints into v6.3.3's paper record and a TierPaper.  Prints far through both sides each state: both fill
    every paper order and re-place it, the tier one tick deeper each time, so the fills and inventory are the same and
    each fill marks one tick more for the tier (alpha + fills x clip x tick) -- v6.3.3's own arithmetic, inherited.
    Converting back and forth carries the whole record."""
    assert ip.TierPaper.on_print is dl.PaperDeep.on_print and ip.TierPaper.alpha is dl.PaperDeep.alpha
    plain, tier = dl.PaperDeep(), ip.TierPaper()
    assert tier.ticks == 1 and isinstance(tier, dl.PaperDeep)
    path = [(100.00, 100.03), (100.02, 100.05), (99.70, 100.30), (99.95, 99.98), (100.40, 100.43), (100.10, 100.13)]
    for i, (bid, ask) in enumerate(path):
        mid = 0.5 * (bid + ask)
        for paper in (plain, tier):
            if i:
                paper.on_print(i * 60 * S, ask + 1.0, 0, 2.0, 4.0)    # a taker buys through every ask
                paper.on_print(i * 60 * S, bid - 1.0, 1, 2.0, 4.0)    # and sells through every bid
            assert paper.bid is None and paper.ask is None, i         # both filled: re-placed below
            paper.place(mid, 21.5, bid=bid, ask=ask, tick=0.01, decimals=2)
        assert (tier.bid, tier.ask) == (round(plain.bid - 0.01, 2), round(plain.ask + 0.01, 2)), i
    assert tier.fills == plain.fills == 2 * (len(path) - 1) and tier.inv == plain.inv == 0.0
    assert tier.alpha() == pytest.approx(plain.alpha() + tier.fills * 2.0 * 0.01)
    resting = ip.TierPaper()                                          # a resting paper order is judged by v6.3.3's band
    resting.place(100.015, 21.5, bid=100.00, ask=100.03, tick=0.01, decimals=2)
    first = (resting.bid, resting.ask)
    resting.place(100.055, 21.5, bid=100.04, ask=100.07, tick=0.01, decimals=2)   # inside the band: kept
    assert (resting.bid, resting.ask) == first == (99.79, 100.24)
    resting.place(100.315, 21.5, bid=100.30, ask=100.33, tick=0.01, decimals=2)   # the bid left the band: re-placed
    assert resting.bid == round(dl.deep_price(100.315, 21.5, BUY, bid=100.30, ask=100.33, tick=0.01, decimals=2) - 0.01, 2)
    back = ip.paper_for(tier, 0)
    assert type(back) is dl.PaperDeep and back is not tier
    assert all(getattr(back, n) == getattr(tier, n) for n in dl.PaperDeep.__slots__)
    again = ip.paper_for(back, 1)
    assert type(again) is ip.TierPaper and again.ticks == 1
    assert all(getattr(again, n) == getattr(back, n) for n in dl.PaperDeep.__slots__)
    assert ip.paper_for(again, 1) is again and ip.paper_for(back, 0) is back
    for ticks in (None, -1, True, "1", 1.0):
        assert type(ip.paper_for(again, ticks)) is dl.PaperDeep, ticks
    assert ip.paper_for("x", 1) == "x" and ip.paper_for(None, 1) is None
    small = ip.TierPaper()
    small.place(100.015, 2.0, bid=100.00, ask=100.03, tick=0.01, decimals=2)
    assert (small.bid, small.ask) == (99.99, 100.04)                 # one tick deeper would leave the band: clamped


# ---- 3. D2's history -------------------------------------------------------------------------------------------------

def _stream(n=400, gap=9, seed=7):
    """Pages arriving every ``gap`` sim-s, 3-8 sim-s old when first seen, the fundamental stepping now and then."""
    pages, fp, t = [], 100.0, NOW - 4_000 * S
    for k in range(n):
        if (k * seed) % 3 == 0:
            fp = round(fp + ((k * 37) % 7 - 3) * 0.01, 6)
        pages.append((t, t - (3 + (k * seed) % 6) * S, fp))
        t += gap * S
    return pages


def _feed(h, pages, now):
    arrived = [p for p in pages if p[0] <= now]
    snap = t616._page({3: arrived[-1][2], 5: arrived[-1][2] + 1.0}, sim_ts_ns=arrived[-1][1]) if arrived else None
    h.observe(snap, now, SIM)
    return snap


def test_the_profile_history_is_v629s_with_keep_horizon_mixed_in():
    h = ip.page_history(tm.PageHistory)
    assert isinstance(h, tm.PageHistory) and isinstance(h, ip.KeepHorizon) and h.keep_s is None
    assert type(h).__mro__[1:3] == (ip.KeepHorizon, tm.PageHistory) and type(h) is type(ip.page_history(tm.PageHistory))
    assert type(h).observe is tm.PageHistory.observe and type(h).decide is tm.PageHistory.decide
    assert type(h).reset is tm.PageHistory.reset and h.pages == [] and h.resets == 0


def test_without_a_keep_horizon_or_with_one_of_w_the_history_is_v629s_request_for_request():
    """keep_s None (D2 or the profile off) or W itself: the same references, decisions, kept pages and resets as v6.29's
    history on the same request stream, for every look-back."""
    pages = _stream()
    for W in (W20, 31.6, 45.0):
        for keep in (None, W, W * 0.5):
            ref, h = tm.PageHistory(), ip.page_history(tm.PageHistory)
            h.keep_s = keep
            for r in range(0, 3_000, 3):
                now = NOW - 4_000 * S + r * S
                snap = _feed(ref, pages, now)
                h.observe(snap, now, SIM)
                fp = fv.book_fundamental(snap, 3, 99.9, 100.1, state_ts_ns=now, sim_id=SIM, max_stale_sim_s=W)[0]
                assert h.decide(3, fp, now, W) == ref.decide(3, fp, now, W), (W, keep, r)
                assert h.pages == ref.pages and h.resets == ref.resets, (W, keep, r)


def test_a_2w_book_keeps_its_2w_reference_after_any_number_of_w_lookups():
    """Every request decides an m = 1 book first and an m = 2 book after it (the pass's book order).  With keep_s = 2W
    the 2W lookup gets the page first seen 2W ago -- the one a 2W-only history (v6.29's rule at 2W) gives -- and the W
    lookup v6.29's at W, request after request; the history stays small.  v6.29's own prune at W loses the 2W page."""
    pages = _stream()
    W = 31.6
    h, only_w, only_2w, plain = (ip.page_history(tm.PageHistory), tm.PageHistory(), tm.PageHistory(),
                                 ip.page_history(tm.PageHistory))
    h.keep_s = 2 * W
    lost = 0
    for r in range(0, 3_000, 2):
        now = NOW - 4_000 * S + r * S
        snap = None
        for x in (h, only_w, only_2w, plain):
            snap = _feed(x, pages, now)
        fp = fv.book_fundamental(snap, 3, 99.9, 100.1, state_ts_ns=now, sim_id=SIM, max_stale_sim_s=W)[0]
        fp5 = fv.book_fundamental(snap, 5, 100.9, 101.1, state_ts_ns=now, sim_id=SIM, max_stale_sim_s=W)[0]
        for _ in range(3):                                           # many m = 1 lookups in the same request
            assert h.decide(3, fp, now, W) == only_w.decide(3, fp, now, W), r
        got2 = h.decide(5, fp5, now, 2 * W)
        assert got2 == only_2w.decide(5, fp5, now, 2 * W), r
        assert h.reference(now, 2 * W) == only_2w.reference(now, 2 * W), r
        plain.decide(3, fp, now, W)
        lost += int(plain.decide(5, fp5, now, 2 * W) != got2)
        assert len(h.pages) <= 2 + int(2 * W / 9) + 1, (r, len(h.pages))
    assert lost > 100                                               # what the keep horizon is for


def test_the_keep_horizon_never_drops_the_lookups_own_reference_and_keeps_v629s_rewind_rules():
    h = ip.page_history(tm.PageHistory)
    h.keep_s = 10.0                                                  # shorter than the look-back: the look-back's own
    for i, dt in enumerate((0, 10, 20, 35)):
        assert h.observe(t616._page({3: 100.0 + i}, sim_ts_ns=NOW + dt * S), NOW + dt * S, SIM)
    assert h.reference(NOW + 40 * S, 30.0)[0] == NOW + 10 * S and [p[0] for p in h.pages][0] == NOW + 10 * S
    h.keep_s = 60.0
    assert h.reference(NOW + 50 * S, 30.0)[0] == NOW + 20 * S and h.pages[0][0] == NOW + 10 * S   # kept for 60 s
    assert h.reference(NOW + 50 * S, None) is None and h.reference("x", 30.0) is None and h.reference(NOW, -1.0) is None
    assert h.reference(NOW + 5 * S, 30.0) is None and len(h.pages) == 3                          # none that old: no prune
    h.keep_s = "x"                                                   # unreadable: the look-back's own prune
    assert h.reference(NOW + 50 * S, 30.0)[0] == NOW + 20 * S and h.pages[0][0] == NOW + 20 * S
    back = NOW - 60 * S                                              # the clock runs back: v6.29's restart
    h.keep_s = 60.0
    assert h.observe(t616._page({3: 99.0}, sim_ts_ns=back), back, SIM) and h.resets == 1 and len(h.pages) == 1
    ahead = t616._page({3: 98.0}, sim_ts_ns=back + 5 * S)            # v6.29's rewind guard: a page ahead is not kept
    assert not h.observe(ahead, back + S, SIM) and len(h.pages) == 1
    assert not h.observe(t616._page({3: 98.0}, sim_ts_ns=back + 9 * S, sim_id="20260929_2015"), back + 10 * S, SIM)


# ---- 4. the agent: t629's harness with v6.30's methods and init ---------------------------------------------------------

_CLASSES: dict = {}


def _klass(base, names=None):
    key = (base, None if names is None else tuple(sorted((k, id(v)) for k, v in names.items())))
    if key not in _CLASSES:
        scope = dict(getattr(base, "_v63_pass").__globals__)
        scope.update(EXTRA if names is None else names)
        methods = {}
        for n in METHODS + t616.METHODS:                              # v6.16's centre: for the centre test
            ns = dict(scope)
            exec(compile(ast.Module(body=[ast.parse(_src(n)).body[0]], type_ignores=[]), f"<{n}>", "exec"), ns)
            methods[n] = ns[n]
        _CLASSES[key] = type("P630", (base,), methods)
    return _CLASSES[key]


def _mentions630(node):
    """A v6.30 name in ``node`` -- v6.30's alone: t629's _mentions630 also names v6.31's, which its _Drop630 takes out
    with v6.30's."""
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in t623.V6_30_IDENTIFIERS or name.startswith(("_v6_30_", "v6_30_"))):
            return True
    return False


def _init_source():
    """The v6.30 statements of _init_build_switches -- the real ones -- as one function of self."""
    fn = ast.parse(_src("_init_build_switches")).body[0]
    body = [st for st in fn.body if _mentions630(st)]
    assert len(body) == 13, [ast.unparse(st) for st in body]
    return "def _init630(self):\n" + textwrap.indent("\n".join(ast.unparse(st) for st in body), "    ") + "\n"


_AS_BOOL: list = []


def _as_bool():
    if not _AS_BOOL:
        ns: dict = {"Any": object}
        exec(compile(textwrap.dedent(_dbg("_as_bool")).replace("@staticmethod\n", ""), "<_as_bool>", "exec"), ns)
        _AS_BOOL.append(ns["_as_bool"])
    return _AS_BOOL[0]


def _init630(agent, *, d1=True, d2=True, names=None):
    ns = dict(EXTRA if names is None else names)
    exec(compile(_init_source(), "<v6.30 init>", "exec"), ns)
    agent.config = types.SimpleNamespace(research_v630_deep_tier=d1, research_v630_s9_lookback=d2)
    agent._as_bool = _as_bool()
    ns["_init630"](agent)


def _agent(*, uid=T1M1, d1=True, d2=True, names=None, **kw):
    """t629's harness agent (v6.29: S9 on, S8 on, S5-S7 on; book 3 100.00 / 100.03, depth 21.5; the fundamental 38 bps
    above its mid unless ``page`` says otherwise) constructed with ``uid`` (None: unknown), with v6.30's
    methods and the v6.30 state its real _init_build_switches statements leave."""
    agent = t629._agent(**kw)
    agent.__class__ = _klass(type(agent), names)
    agent.uid = uid
    _init630(agent, d1=d1, d2=d2, names=names)
    return agent


def _ref(**kw):
    """t629's harness agent: v6.29 exactly, no v6.30 attribute at all."""
    return t629._agent(**kw)


def _request(agent, *, ts=NOW, wall=1000.0, version=62):
    return t629._request(agent, ts=ts, wall=wall, version=version)


_out = t622._out
_touch, _rest_of = t629._touch, t629._rest_of
_rest_l0 = t629.t627._rest_l0
_seen = t629._seen


def _counts(agent):
    return dict(agent._v6_30_counts)


def _rows(agent):
    return [kw for event, kw in agent.emitted if event == "V6_30_STATE"]


def _deep_orders(out):
    placed, cancelled = out
    return sorted(p for p in placed if dl.is_deep_client_id(p[2])), sorted(cancelled)


def _paper(agent, book=3):
    paper = agent._v633_deep.books[book].paper
    return type(paper).__name__, paper.bid, paper.ask


def _sweeps(agent, values, book=3):
    agent._v633_deep.books[book].sweeps = deque((float(v) for v in values), maxlen=dl.SWEEP_KEEP)


def test_the_init_statements_set_the_state_and_fix_the_uid_the_agent_was_constructed_with():
    agent = _agent(uid=115)
    ident = agent._v6_30_identity
    assert agent.research_v630_deep_tier is True and agent.research_v630_s9_lookback is True
    assert (ident.uid, ident.source, ident.off, ident.table) == (115, ip.SOURCE_AGENT, None, {})
    assert isinstance(agent._v6_29_pages, ip.KeepHorizon) and agent._v6_29_pages.keep_s is None
    assert (agent._v6_30_on, agent._v6_30_counts, agent._v6_30_errors, agent._v6_30_reported) == (False, {}, 0, False)
    off = _agent(uid=115, d1=False, d2=False)
    assert not off.research_v630_deep_tier and not off.research_v630_s9_lookback
    assert type(off._v6_29_pages) is tm.PageHistory                  # D2 off: v6.29's own history
    assert _agent(uid=-1)._v6_30_identity.off == ip.OFF_NEGATIVE
    unknown = _agent(uid=None)
    assert unknown._v6_30_identity.uid is None and unknown._v6_30_identity.off is None
    a = _agent(uid=115)
    a.config = types.SimpleNamespace()                               # no key: both default on
    ns = dict(EXTRA)
    exec(compile(_init_source(), "<v6.30 init>", "exec"), ns)
    ns["_init630"](a)
    assert a.research_v630_deep_tier is True and a.research_v630_s9_lookback is True


def test_tier_1_deep_orders_are_one_tick_deeper_and_tier_0_is_v629s():
    """A first request on book 3: tier 1 places level 0 one tick further from the mid on both sides and its paper record
    rests there too; tier 0 is v6.29's request, paper and all.  The touch quote and S9 are untouched by D1."""
    for uid, tier in ((T1M1, 1), (T1M2, 1), (T0M1, 0), (T0M2, 0)):
        for rest in (None, t629._l0_and_touch(BUY)):
            on, ref = _agent(uid=uid), _ref()
            for a in (on, ref):
                if rest is not None:
                    rest(a)
            got, want = _out(_request(on)), _out(_request(ref))
            case = (uid, tier, getattr(rest, "__name__", None))
            assert _touch(got) == _touch(want), case
            if rest is None:
                want_deep = [(3, "BUY", 40031, 99.80, 2.0), (3, "SELL", 40032, 100.23, 2.0)]
                assert _deep_orders(want) == (want_deep, []), case
                assert _deep_orders(got) == (([(3, "BUY", 40031, 99.79, 2.0), (3, "SELL", 40032, 100.24, 2.0)]
                                              if tier else want_deep), []), case
                assert _paper(ref) == ("PaperDeep", 99.80, 100.23), case
                assert _paper(on) == (("TierPaper", 99.79, 100.24) if tier else ("PaperDeep", 99.80, 100.23)), case
                assert _counts(on).get("deeper_l0", 0) == 2 * tier and _counts(on).get("paper_tier1", 0) == tier, case
            else:
                assert got == want, case                               # resting at v6.29's price: kept, nothing new
            assert on._v6_30_errors == 0 and on._v6_30_on, case


def _ladder(agent):
    agent.research_v69_deep_ladder = True
    _sweeps(agent, range(1, 201))                                    # p97 195, p99 199, max 200 ticks


def _one_tick(got, want, case):
    """Every deep placement of ``got`` is ``want``'s one tick further from the mid (same book, side, id, size)."""
    g, w = _deep_orders(got)[0], _deep_orders(want)[0]
    assert len(g) == len(w) > 0, (case, got, want)
    for (b, d, cid, px, q), (b2, d2, cid2, px2, q2) in zip(g, w):
        assert (b, d, cid, q) == (b2, d2, cid2, q2), case
        assert px == (round(px2 - 0.01, 2) if d == "BUY" else round(px2 + 0.01, 2)), (case, cid, px, px2)


def test_every_deep_level_of_a_tier_1_book_moves_one_tick_the_reducing_order_the_vacuum_order_and_the_ladder():
    on, ref = _agent(uid=T1M1, inv=3.0), _ref(inv=3.0)              # v6.12: the side that reduces rests at half depth
    for a in (on, ref):
        a.research_v612_reduce_depth = True
    got, want = _out(_request(on)), _out(_request(ref))
    _one_tick(got, want, "reducing")
    assert ref._v633_counts.get("placed_reducing_l0") == on._v633_counts.get("placed_reducing_l0") == 1
    near = min(_deep_orders(want)[0], key=lambda p: abs(p[3] - 100.015))
    assert abs(near[3] - 100.015) < 0.75 * 21.5 * 0.01                # v6.29's reducing order: about half the depth
    on, ref = _agent(uid=T1M1), _ref()                               # v6.4: a blown-out spread, the vacuum order
    vac_book = t622.t63._book(99.90, 100.20)
    got = _out(t626._request(on, ts=NOW, wall=1000.0, version=62, book=vac_book))
    want = _out(t626._request(ref, ts=NOW, wall=1000.0, version=62, book=vac_book))
    _one_tick(got, want, "vacuum")
    assert on._v64_counts.get("placed_vacuum") == ref._v64_counts.get("placed_vacuum") == 2
    assert all(99.90 < p[3] < 100.20 for p in _deep_orders(got)[0])
    on, ref = _agent(uid=T1M1), _ref()                               # v6.9: the ladder, level 0 first
    for a in (on, ref):
        _ladder(a)
    first_on, first_ref = _out(_request(on)), _out(_request(ref))
    _one_tick(first_on, first_ref, "ladder level 0")
    on2, ref2 = _agent(uid=T1M1), _ref()                             # level 0 resting where each placed it: level 1
    for a, first in ((on2, first_on), (ref2, first_ref)):
        _ladder(a)
        for i, (b, d, cid, px, q) in enumerate(_deep_orders(first)[0]):
            t622.t63.t14._rest(a, 21 + i, b, 0 if d == "BUY" else 1, px, cid=cid)
    got, want = _out(_request(on2)), _out(_request(ref2))
    assert [p[2] for p in _deep_orders(want)[0]] == [40033, 40034], want
    _one_tick(got, want, "ladder level 1")
    assert _counts(on2).get("deeper_l1") == 2 and on2._v6_30_errors == 0 and on2._v69_counts == ref2._v69_counts


def test_a_resting_order_at_v629s_price_migrates_at_its_normal_reprice_only():
    """v6.29's level 0 resting on a tier-1 book stays (inside v6.3.3's band): no cancel, no new order -- it moves when its
    band or its life would have moved it anyway; a resting order out of the band is repriced to the v6.30 target."""
    on, ref = _agent(uid=T1M1), _ref()
    for a in (on, ref):
        _rest_l0(a)
    assert _out(_request(on)) == _out(_request(ref))
    assert not [k for k in _counts(on) if k.startswith(("deeper_l", "clamped_l"))] and _counts(on)["paper_tier1"] == 1
    on, ref = _agent(uid=T1M1), _ref()
    for a in (on, ref):
        t622._rest_doomed(a)                                         # level 0 far from its depth: repriced
    got, want = _out(_request(on)), _out(_request(ref))
    assert got[1] == want[1] and [(3, 31)] == [c for c in want[1] if c[1] == 31]


def test_a_tier_1_book_whose_band_clamps_keeps_v629s_price_and_counts_it():
    on, ref = _agent(uid=T1M1), _ref()
    for a in (on, ref):
        _sweeps(a, [2.0] * 30)                                       # depth 2 ticks: one more would leave 1.5 x 2
    got, want = _out(_request(on)), _out(_request(ref))
    assert _deep_orders(got) == _deep_orders(want) == ([(3, "BUY", 40031, 99.99, 2.0), (3, "SELL", 40032, 100.04, 2.0)], [])
    assert _counts(on).get("clamped_l0") == 2 and "deeper_l0" not in _counts(on)
    assert _paper(on) == ("TierPaper", 99.99, 100.04) == ("TierPaper",) + _paper(ref)[1:]


def test_the_reducing_order_is_kept_inside_the_band_of_its_own_half_depth():
    """v6.12's reducing order rests at half the depth and is judged against that half: at a depth of 4 ticks the long
    book's sell rests 2.5 ticks out, and one tick more would leave 1.5 x 2 -- it keeps v6.29's price (clamped) while the
    adding bid, judged against the full depth, moves a tick."""
    on, ref = _agent(uid=T1M1, inv=3.0, page=None), _ref(inv=3.0, page=None)      # no fundamental: no lean
    for a in (on, ref):
        a.research_v612_reduce_depth = True
        _sweeps(a, [4.0] * 30)
    got, want = _deep_orders(_out(_request(on))), _deep_orders(_out(_request(ref)))
    (buy, sell), (buy0, sell0) = got[0], want[0]
    assert (buy[1], sell[1]) == ("BUY", "SELL") and sell0[3] == 100.04 and ref._v633_counts.get("placed_reducing_l0") == 1
    assert sell == sell0 and buy[3] == round(buy0[3] - 0.01, 2)
    assert _counts(on).get("clamped_l0") == 1 and _counts(on).get("deeper_l0") == 1


def test_with_v616s_centre_a_tier_1_order_is_judged_around_its_centre():
    """v6.18 ships v6.16's centre off.  With it on, the side toward the fundamental is priced around its centre (here the
    bid, centred one depth above the mid, rests one tick behind the touch) and judged there: the tier-1 bid one tick
    lower is inside the band around the centre (around the mid it would not be); the offer, centred on the mid, moves
    a tick as everywhere."""
    on, ref = _agent(uid=T1M1), _ref()
    ref.__class__ = _klass(type(ref))                                 # v6.16's centre methods; no v6.30 state at all
    for a in (on, ref):
        a.research_v618_centre_off = False
    got, want = _deep_orders(_out(_request(on))), _deep_orders(_out(_request(ref)))
    assert want == ([(3, "BUY", 40031, 99.99, 2.0), (3, "SELL", 40032, 100.23, 2.0)], []), want
    assert got == ([(3, "BUY", 40031, 99.98, 2.0), (3, "SELL", 40032, 100.24, 2.0)], []), got
    assert on._v616_counts.get("placed_centred", 0) >= 1 and _counts(on).get("deeper_l0") == 2


def test_the_spacing_judges_the_target_the_side_places():
    """v6.6's spacing on a held book: a buy may add only a full depth below its last add.  The last add 21.5 ticks above
    v6.29's target minus half a tick: v6.29's target is unspaced (no bid); the tier-1 target, one tick lower, is
    spaced and placed.  Tier 0 is v6.29's.  (No fundamental page: S5 leans nothing, so the long book's bid adds.)"""
    def spaced(a):
        a.research_v66_add_spacing = True
        sp = a._v66_spacing_ref()
        sp.last[3] = {BUY: round(99.80 + 21.5 * 0.01 - 0.005, 4)}
        sp.last_ts = NOW
    for uid, tier in ((T1M1, 1), (T0M1, 0)):
        on, ref = _agent(uid=uid, inv=3.0, page=None), _ref(inv=3.0, page=None)     # no fundamental: no lean
        for a in (on, ref):
            spaced(a)
        got, want = _deep_orders(_out(_request(on))), _deep_orders(_out(_request(ref)))
        assert [p for p in want[0] if p[1] == "BUY"] == [], want
        assert [p for p in got[0] if p[1] == "BUY"] == ([(3, "BUY", 40031, 99.79, 2.0)] if tier else []), (uid, got)


def test_d1_off_d2_off_and_both_off_are_v629s_request_for_request():
    """Switch-off identity on t629's harness: both off, the request, every counter and the state left behind are v6.29's
    (no v6.30 attribute at all), on every move, inventory, resting set and pacing state; D1 off, every deep order and
    paper record is v6.29's; D2 off, every touch decision and S9's state is v6.29's."""
    seen = 0
    for now_fp, before in ((ABOVE, ABOVE + 0.05), (BELOW, BELOW - 0.05), (ABOVE, ABOVE)):
        for inv in (None, 3.0, -20.0):
            for rest in (None, _rest_l0, t622._rest_doomed, t629._l0_and_touch(BUY)):
                for pace in (None, "paced"):
                    kw = lambda: dict(pace=t622._aged(t622.U_BOUND), used=t622.U_BOUND + 500.0) if pace else {}
                    page = t629._page(now_fp)
                    for d1, d2 in ((False, False), (False, True), (True, False)):
                        on = _agent(uid=T1M2, d1=d1, d2=d2, page=page, inv=inv, **kw())
                        ref = _ref(page=page, inv=inv, **kw())
                        for a in (on, ref):
                            _seen(a, before + 0.01, at=NOW - 45 * S, age=20.0)
                            _seen(a, before, at=NOW - 25 * S)
                            if rest is not None:
                                rest(a)
                        got, want = _out(_request(on)), _out(_request(ref))
                        case = (now_fp, before, inv, getattr(rest, "__name__", None), pace, d1, d2)
                        if not d1:
                            assert _rest_of(got) == _rest_of(want) and _paper(on) == _paper(ref), case
                            assert t622._fingerprint(on) == t622._fingerprint(ref), case
                            assert on._v633_counts == ref._v633_counts, case
                        if not d2:
                            assert _touch(got) == _touch(want), case
                            assert on._v6_29_counts == ref._v6_29_counts and on._v6_29_pages.pages == ref._v6_29_pages.pages
                            assert on._v617_counts == ref._v617_counts, case
                        if not d1 and not d2:
                            assert got == want and _counts(on) == {}, case
                        assert on._v6_30_errors == 0 and on._v6_29_errors == 0, case
                        seen += 1
    assert seen == 3 * 3 * 4 * 2 * 3


def test_d2_a_book_at_m_2_looks_back_2w_and_at_m_1_is_v629s():
    """The fundamental fell over the last W (the page first seen 25 s ago) but not over 2W (the page first seen 45 s ago
    is at today's value): m = 1 withholds the bid as v6.29 does; m = 2 does not -- its reference is the older page."""
    for uid, m in ((T1M1, 1), (T0M1, 1), (T1M2, 2), (T0M2, 2)):
        on, ref = _agent(uid=uid), _ref()
        for a in (on, ref):
            _seen(a, ABOVE, at=NOW - 45 * S, age=20.0)
            _seen(a, ABOVE + 0.05, at=NOW - 25 * S)
            _rest_l0(a)
        got, want = _out(_request(on)), _out(_request(ref))
        assert _touch(want) == ([], []) and ref._v6_29_counts["withheld"] == 1
        if m == 1:
            assert _touch(got) == _touch(want) and on._v6_29_counts == ref._v6_29_counts, uid
            assert _counts(on).get("s9_m1_gated") == 1 and _counts(on).get("s9_m1_decisions") == 1
        else:
            assert _touch(got) == ([t629.QUOTE_BUY], []), uid
            assert on._v6_29_counts.get(tm.ZERO_MOVE) == 1 and "withheld" not in on._v6_29_counts, uid
            assert _counts(on).get("s9_m2_decisions") == 1 and _counts(on).get("s9_m2_zero_move") == 1
        assert on._v6_29_lookback == W20 and on._v6_29_pages.keep_s == W20 * on._v6_30_identity.max_m


def test_d2_without_a_page_2w_old_there_is_no_gate_and_rewinds_and_new_simulations_are_v629s():
    on = _agent(uid=T0M2)
    _seen(on, ABOVE + 0.05, at=NOW - 25 * S)                           # only W old: m = 2 has no reference
    _rest_l0(on)
    assert _touch(_out(_request(on))) == ([t629.QUOTE_BUY], []) and _counts(on).get("s9_m2_no_history") == 1
    for sim in (SIM, "20260929_2015"):                                # a rewind (same id) / another simulation's page
        on, ref = _agent(uid=T0M2), _ref()
        for a in (on, ref):
            _seen(a, ABOVE + 0.05, at=NOW + 100 * S)                   # the history ran ahead of this request's clock
            _rest_l0(a)
        on._v616_feed = ref._v616_feed = t616._Feed(t629._page(ABOVE, sim_id=sim))
        got, want = _out(_request(on)), _out(_request(ref))
        assert got == want and on._v6_29_pages.pages == ref._v6_29_pages.pages
        assert on._v6_29_pages.resets == ref._v6_29_pages.resets == 1, sim


def _request2(agent, *, ts, wall, fps, limit=None):
    """One request on books 3 and 5 (the pass's order: 3 then 5), each with a fresh page fundamental; ``limit``: this
    request's S6 limit, set by hand after S6's hook (W)."""
    agent._v616_feed = t616._Feed(t616._page(fps, sim_ts_ns=ts - 2 * S))
    t626._hook(agent, ts=ts, wall=wall)
    if limit is not None:
        agent._v6_26_limit = limit
    resp = t622.t63._Resp()
    state = t623._state(ts=ts, version=62)
    state.books = {3: t622.t63._book(), 5: t622.t63._book()}
    agent._v6_23_observe(state)
    agent._v63_pass(resp, state, {})
    return resp


def _touch_of(out, book):
    return sorted(p for p in out[0] if p[0] == book and p[2] in tq.client_ids(book))


def test_d2_mixed_look_backs_in_one_pass_keep_the_2w_page_request_after_request():
    """A UID whose book 3 is m = 1 and book 5 m = 2: every request decides book 3 at W before book 5 at 2W.  For 160
    requests book 3's touch decisions are v6.29's at W and book 5's those of S9 at 2W alone (book 5's fundamental falls
    all along: once a page 2W old exists its bid is withheld); with v6.29's prune (keep_s off) book 5 loses its 2W
    reference after every W lookup, and quotes the bid."""
    uid = _uid(0, 1, book=3, other=(5, 0, 2))
    agents = {"on": _agent(uid=uid), "w": _agent(uid=uid, d2=False), "2w": _agent(uid=uid), "pruned": _agent(uid=uid)}
    probe = _ref()
    level0 = _deep_orders(_out(_request2(probe, ts=NOW - 1000 * S, wall=0.0, fps={3: ABOVE, 5: ABOVE})))[0]
    assert sorted({p[0] for p in level0}) == [3, 5] and len(level0) == 4
    for a in agents.values():
        for i, (b, d, cid, px, q) in enumerate(level0):           # level 0 resting: the touch quote is free to place
            t622.t63.t14._rest(a, 61 + i, b, 0 if d == "BUY" else 1, px, cid=cid)
        a._v6_30_identity.extend([3, 5])
    agents["2w"]._v6_30_identity.table[3] = (0, 2)                 # S9 at 2W alone: both books at m = 2
    pruned = agents["pruned"]
    pruned._v6_30_request = types.MethodType(
        lambda self, state: (setattr(self, "_v6_30_on", True), setattr(self._v6_29_pages, "keep_s", None)), pruned)
    lost = 0
    for r in range(160):
        ts, wall = NOW + r * S, 1000.0 + 10.0 * r                    # 0.1 sim-s per wall-s: W = 20 + 0.1 x 55
        fps = {3: ABOVE + 0.01 * ((r // 9) % 3), 5: ABOVE - 0.01 * (r // 10)}     # book 5's falls all along
        out = {k: _out(_request2(a, ts=ts, wall=wall, fps=fps)) for k, a in agents.items()}
        assert _touch_of(out["on"], 3) == _touch_of(out["w"], 3), r
        assert _touch_of(out["on"], 5) == _touch_of(out["2w"], 5), r
        lost += int(_touch_of(out["pruned"], 5) != _touch_of(out["on"], 5))
    on = agents["on"]
    assert on._v6_26_limit == pytest.approx(20.0 + 0.1 * 55.0) and on._v6_29_pages.keep_s == pytest.approx(2 * 25.5)
    assert _counts(on)["s9_m2_decisions"] == 160 and _counts(on)["s9_m1_decisions"] == 160
    assert _counts(on).get("s9_m2_gated", 0) > 80 and lost > 80, (_counts(on), lost)
    assert len(on._v6_29_pages.pages) <= int(2 * on._v6_26_limit) + 3


def test_a_request_whose_profile_check_errors_runs_v629_for_that_request_even_with_a_table():
    """Request 1 draws the table (tier 1, m = 2); request 2's check raises after the draw (here: W, as S6 hands it): that
    request runs v6.29 for both switches -- v6.29's deep prices and paper record, S9 at W -- the error counts, the
    identity stays and S9's keep horizon is left as it was, so request 3's 2W lookup still finds its reference."""
    flag = {"boom": False}

    def lookback(limit):
        if flag["boom"]:
            raise RuntimeError("boom")
        return tm.lookback_sim_s(limit)
    on, ref = _agent(uid=T1M2, names=dict(EXTRA, v6_29_lookback=lookback)), _ref()
    for a in (on, ref):
        _seen(a, ABOVE, at=NOW - 45 * S, age=20.0)
        _seen(a, ABOVE + 0.05, at=NOW - 25 * S)
    tier1 = [(3, "BUY", 40031, 99.79, 2.0), (3, "SELL", 40032, 100.24, 2.0)]
    got, want = _out(_request(on)), _out(_request(ref))
    assert _deep_orders(got)[0] == tier1 and on._v6_29_counts.get(tm.ZERO_MOVE) == 1     # m = 2: the 45-s page
    assert ref._v6_29_counts.get(tm.FELL) == 1                                            # W: the 25-s page
    flag["boom"] = True
    got, want = _out(_request(on, ts=NOW + S, wall=1000.0)), _out(_request(ref, ts=NOW + S, wall=1000.0))   # S6: no rate
    assert not on._v6_30_on and on._v6_30_errors == 1 and on._v6_30_identity.off is None and on._v6_30_identity.table
    assert _deep_orders(got) == _deep_orders(want) and on._v6_29_pages.keep_s == 2 * W20      # the horizon as it was
    assert on._v6_29_counts.get(tm.FELL) == 1 and _paper(on)[0] == "PaperDeep"
    assert _counts(on).get("s9_m2_decisions") == 1                                       # request 1's only
    flag["boom"] = False
    got = _out(_request(on, ts=NOW + 2 * S, wall=1000.0))
    assert on._v6_30_on and _deep_orders(got)[0] == tier1 and _counts(on).get("s9_m2_decisions") == 2
    assert on._v6_29_counts.get(tm.ZERO_MOVE) == 2       # its 2W reference (the 45-s page) survived the errored W lookup


def test_a_history_without_the_keep_horizon_gets_v629s_decision_at_w():
    """A page history that is v6.29's own (no KeepHorizon: only if something rebuilt it) is never asked at m x W: the
    book's decision is v6.29's, at W."""
    on, ref = _agent(uid=T1M2), _ref()
    on._v6_29_pages = tm.PageHistory()
    for a in (on, ref):
        _seen(a, ABOVE, at=NOW - 45 * S, age=20.0)
        _seen(a, ABOVE + 0.05, at=NOW - 25 * S)
        _rest_l0(a)
    got, want = _out(_request(on)), _out(_request(ref))
    assert _touch(got) == _touch(want) == ([], []) and on._v6_29_counts == ref._v6_29_counts
    assert "s9_m2_decisions" not in _counts(on) and on._v6_30_errors == 0 and on._v6_30_on


def test_a_w_that_shrinks_and_grows_back_still_finds_its_references():
    """S6's W shrinks (25.5 -> 22 sim-s) and grows back: book 3 (m = 1) decides at W and book 5 (m = 2) at 2W each request,
    a page every request, each fundamental falling every 10 sim-s -- so a book's bid is withheld exactly when a page
    first seen W_book ago exists.  The keep horizon never shrinks (51 sim-s held), so both books find their reference
    right after the regrowth; v6.29's prune at W would lose it there (the oracle: every page ever seen)."""
    uid = _uid(0, 1, book=3, other=(5, 0, 2))
    on, probe = _agent(uid=uid), _ref()
    level0 = _deep_orders(_out(_request2(probe, ts=NOW - 1000 * S, wall=0.0, fps={3: ABOVE, 5: ABOVE})))[0]
    for i, (b, d, cid, px, q) in enumerate(level0):
        t622.t63.t14._rest(on, 61 + i, b, 0 if d == "BUY" else 1, px, cid=cid)
    keeps = []
    for r in range(150):
        W = 22.0 if 70 <= r < 110 else 25.5
        fps = {3: ABOVE - 0.01 * (r // 10), 5: ABOVE - 0.01 * (r // 10)}
        out = _out(_request2(on, ts=NOW + r * S, wall=1000.0, fps=fps, limit=W))
        for book, w in ((3, W), (5, 2 * W)):
            want_held = r >= w                                     # a page first seen w sim-s ago (one per request)
            assert (_touch_of(out, book) == []) is want_held, (r, book, w)
        keeps.append(on._v6_29_pages.keep_s)
    assert keeps[:70] == [51.0] * 70 and keeps[70:] == [51.0] * 80   # held through the shrink: never 44
    assert _counts(on)["s9_m2_gated"] == 150 - 51 - 0 and on._v6_30_errors == 0


def test_the_keep_horizon_only_grows_while_the_profile_is_on_and_is_v629s_once_it_is_off():
    h = ip.page_history(tm.PageHistory)
    for k, want in ((40.0, 40.0), (30.0, 40.0), (51.0, 51.0), (None, 51.0), ("x", 51.0), (-1.0, 51.0), (44.0, 51.0)):
        h.hold(k)
        assert h.keep_s == want, (k, h.keep_s)
    on = _agent(uid=T1M2)
    for i, limit in enumerate((25.0, 20.0, 30.0, 22.0)):
        _request2(on, ts=NOW + i * S, wall=1000.0, fps={3: ABOVE, 5: ABOVE}, limit=limit)
        assert on._v6_29_pages.keep_s == 2 * max((25.0, 20.0, 30.0, 22.0)[:i + 1]), i
    on.uid = T1M2 + 1                                                 # the profile turns off for good: v6.29's prune
    _request2(on, ts=NOW + 4 * S, wall=1000.0, fps={3: ABOVE, 5: ABOVE}, limit=40.0)
    assert on._v6_29_pages.keep_s is None and on._v6_30_identity.off == ip.OFF_CHANGED
    on.uid = T1M2
    _request2(on, ts=NOW + 5 * S, wall=1000.0, fps={3: ABOVE, 5: ABOVE}, limit=40.0)
    assert on._v6_29_pages.keep_s is None


def test_an_unknown_negative_or_changed_uid_keeps_or_turns_both_switches_to_v629():
    for uid, why in ((None, ip.OFF_UNKNOWN), (-1, ip.OFF_NEGATIVE)):
        on, ref = _agent(uid=uid), _ref()
        for a in (on, ref):
            _seen(a, ABOVE + 0.05, at=NOW - 25 * S)
        on._tick = 1
        assert _out(_request(on)) == _out(_request(ref)) and _paper(on) == _paper(ref)
        assert on._v6_30_identity.off == why and not on._v6_30_on and on._v6_29_pages.keep_s is None
        row = _rows(on)[0]
        assert (row["reason"], row["off_reason"], row["profile_on"], row["uid"], row["books"]) == ("start", why, 0, None, 0)
    on, ref = _agent(uid=T1M2), _ref()
    for a in (on, ref):
        _seen(a, ABOVE + 0.05, at=NOW - 25 * S)
    on._tick = 1
    assert _out(_request(on)) != _out(_request(ref))                 # tier 1, m = 2: the profile acts
    on.uid = T1M2 + 1                                                # the UID changes later in the process
    on._tick, ref._tick = 2, 2
    on._v633_deep.books[3].paper.bid = None                          # both re-place their paper orders this state
    ref._v633_deep.books[3].paper.bid = None
    got, want = _out(_request(on, ts=NOW + S, wall=1001.0)), _out(_request(ref, ts=NOW + S, wall=1001.0))
    assert _touch(got) == _touch(want) and _paper(on)[0] == "PaperDeep" and _paper(on)[1] == _paper(ref)[1]
    assert on._v6_30_identity.off == ip.OFF_CHANGED and not on._v6_30_on and on._v6_29_pages.keep_s is None
    assert [r["reason"] for r in _rows(on)] == ["start", "off"] and _rows(on)[1]["off_reason"] == ip.OFF_CHANGED
    on.uid = T1M2                                                     # and stays off
    _request(on, ts=NOW + 2 * S, wall=1002.0)
    assert not on._v6_30_on and on._v6_30_identity.off == ip.OFF_CHANGED
    late = _agent(uid=None)                                           # self.uid unknown at init, the state addressed
    state = t623._state(ts=NOW, version=62)
    state.accounts = {T1M2: {}}
    late._v6_30_request(state)
    assert late._v6_30_on and late._v6_30_identity.uid == T1M2 and late._v6_30_identity.source == ip.SOURCE_STATE
    state.accounts = {T1M2 + 1: {}}
    late._v6_30_request(state)
    assert not late._v6_30_on and late._v6_30_identity.off == ip.OFF_CHANGED


def test_every_v630_path_fails_open_to_v629_for_that_book_and_counts():
    def boom(*a, **k):
        raise RuntimeError("boom")

    cases = (("identity check", dict(EXTRA, v6_30_addressee=boom)), ("deeper", dict(EXTRA, v6_30_deeper_target=boom)),
             ("paper", dict(EXTRA, v6_30_paper_for=boom)))
    for name, names in cases:
        on, ref = _agent(uid=T1M2, names=names), _ref()
        for a in (on, ref):
            _seen(a, ABOVE + 0.05, at=NOW - 25 * S)
        got, want = _out(_request(on)), _out(_request(ref))
        if name == "identity check":
            assert got == want and not on._v6_30_on and on._v6_29_pages.keep_s is None, name
        elif name == "deeper":
            assert _rest_of(got) == _rest_of(want) and on._v6_30_errors >= 2, name
            assert _counts(on).get("error_l0") == 2 and "deeper_l0" not in _counts(on) and "clamped_l0" not in _counts(on)
            assert on._v6_30_snapshot()["deep_unchanged_error"] == 2 and on._v6_30_snapshot()["deep_clamped"] == 0
        else:
            assert _paper(on) == _paper(ref) and on._v6_30_errors == 1, name
        assert on._v6_30_errors >= 1, name
    on, ref = _agent(uid=T1M2), _ref()
    on._v6_30_identity.value = boom                                  # the tier and the multiple unreadable
    for a in (on, ref):
        _seen(a, ABOVE + 0.05, at=NOW - 25 * S)
    assert _out(_request(on)) == _out(_request(ref)) and on._v6_30_errors >= 3
    on, ref = _agent(uid=T1M2), _ref()

    class BadPages(type(on._v6_29_pages)):
        def decide(self, *a, **k):
            raise RuntimeError("boom")
    bad = BadPages()
    for a in (on, ref):
        _seen(a, ABOVE + 0.05, at=NOW - 25 * S)
    bad.pages = list(on._v6_29_pages.pages)
    on._v6_29_pages = bad
    _rest_l0(on)
    _rest_l0(ref)
    got = _out(_request(on))
    assert on._v6_30_errors == 1 and on._v6_29_errors == 1 and _touch(got) == ([t629.QUOTE_BUY], [])
    on, ref = _agent(uid=T1M1), _ref()
    on._v6_30_counts = "x"                                            # every tally raises
    assert _out(_request(on)) != _out(_request(ref)) and on._v6_30_errors >= 3


# ---- 5. the state row -------------------------------------------------------------------------------------------------

def test_the_state_row_comes_at_the_first_pass_every_100_ticks_at_the_first_error_and_when_the_profile_turns_off():
    agent = _agent(uid=T1M2)
    flag = {"boom": False}
    real = agent._v6_30_identity.value

    def value(dim, book):
        if flag["boom"]:
            raise RuntimeError("boom")
        return real(dim, book)
    agent._v6_30_identity.value = value
    for i, tick in enumerate((1, 2, 99, 100, 101)):
        agent._tick = tick
        _request(agent, ts=NOW + i * S, wall=1000.0 + i)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    assert tuple(k for k in rows[0] if k not in ("reason", "tick")) == FIELDS
    first = rows[0]
    assert (first["version"], first["profile"], first["deep_tier_on"], first["s9_lookback_on"], first["profile_on"]) == \
        (ip.V6_30_IDENTITY_PROFILE_VERSION, "orthox-profile-v1", 1, 1, 1)
    assert (first["uid"], first["uid_source"], first["off_reason"], first["books"]) == (T1M2, ip.SOURCE_AGENT, None, 1)
    assert first["table_sha256"] == ip.table_digest(T1M2, {3: (1, 2)}) and (first["d1"], first["d2"]) == ("1", "2")
    assert (first["d1_tier1_books"], first["d2_m2_books"], first["deep_shifted"], first["paper_tier1"]) == (1, 1, 2, 1)
    assert (first["s9_m2_decisions"], first["s9_keep_sim_s"], first["errors"]) == (1, 2 * W20, 0)
    for k, (tick, boom) in enumerate(((102, True), (103, True), (104, False), (105, True))):
        flag["boom"] = boom
        agent._tick = tick
        _request(agent, ts=NOW + (5 + k) * S, wall=1005.0 + k)
    got = [(r["reason"], r["tick"]) for r in _rows(agent)]
    assert got == [("start", 1), ("periodic", 100), ("error", 102), ("error", 105)]
    agent.uid = T1M2 + 1
    agent._tick = 106
    _request(agent, ts=NOW + 10 * S, wall=1010.0)
    assert _rows(agent)[-1]["reason"] == "off" and _rows(agent)[-1]["profile_on"] == 0
    agent._tick = 107
    _request(agent, ts=NOW + 11 * S, wall=1011.0)
    assert len(_rows(agent)) == 5                                    # logged once


def test_the_row_logs_a_book_drawn_later_and_never_fails_the_request_and_waits_for_the_reply():
    agent = _agent(uid=T1M2)
    agent._tick = 1
    _request(agent)
    agent._tick = 2
    _request2(agent, ts=NOW + S, wall=1001.0, fps={3: ABOVE, 5: ABOVE})
    rows = _rows(agent)
    assert [r["reason"] for r in rows] == ["start", "table"] and rows[1]["books"] == 2 and rows[1]["books_drawn_later"] == 1
    bad, ref = _agent(uid=T1M2), _agent(uid=T1M2)
    emitted = bad.emitted
    bad._emit = lambda event, force=False, **kw: (1 / 0) if event == "V6_30_STATE" else emitted.append((event, kw))
    bad._tick = ref._tick = 1
    assert _out(_request(bad)) == _out(_request(ref)) and bad._v6_30_errors == 1 and _rows(bad) == []
    deferred = _agent(uid=T1M2)
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _request(deferred)
    names = [n for n, _fn in work]
    assert names[-2:] == ["v6_29_state_row", "v6_30_state_row"] and _rows(deferred) == []
    dict(work)["v6_30_state_row"]()
    assert _rows(deferred)[0]["reason"] == "start"


def test_off_the_row_says_so_and_an_agent_without_the_build_has_none():
    off = _agent(uid=T1M2, d1=False, d2=False)
    off._tick = 1
    _request(off)
    row = _rows(off)[0]
    assert (row["deep_tier_on"], row["s9_lookback_on"], row["profile_on"], row["deep_shifted"], row["s9_m2_decisions"]) \
        == (0, 0, 1, 0, 0) and row["s9_keep_sim_s"] is None and row["books"] == 1
    older = _ref()
    older._tick = 1
    _request(older)
    assert not [e for e, _kw in older.emitted if e == "V6_30_STATE"]


# ---- 6. the post-only invariant ------------------------------------------------------------------------------------------

def _v630_statements(tree):
    """What t629's _Drop630 takes out: the import, the _v6_30_ methods, each if on a v6.30 name (its test and body) and
    each assignment or call naming one -- the v6.30 statements alone, never the v6.29 statement around them."""
    out = []

    def walk(stmts):
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module == "research_v630_identity_profile":
                out.append(st)
            elif isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_30_"):
                out.append(st)
            elif isinstance(st, ast.If) and _mentions630(st.test):
                out.append(st.test)
                out.extend(st.body)
                walk(st.orelse)
            elif isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions630(st):
                out.append(st)
            else:
                for field in ("body", "orelse", "finalbody"):
                    walk(getattr(st, field, None) or [])
                for h in getattr(st, "handlers", None) or []:
                    walk(h.body)
    walk(tree.body)
    return out


def test_the_profile_never_writes_the_order_flags_and_the_orders_still_carry_them():
    """D1 changes a price before v6.29's own limit_order call; no v6.30 statement, method or module line names postOnly,
    stp, time in force or an instruction -- v6.8 S4's _v68_no_take, the maker sanitizer and the final validator stay the
    last stages (unchanged against the committed v6.29: section 8) -- and the orders still leave post-only."""
    v630 = _v630_statements(ast.parse(SIMPLE))
    assert len([st for st in v630 if isinstance(st, ast.FunctionDef)]) == len(METHODS)
    text = "\n".join(ast.unparse(st) for st in v630) + "\n" + "\n".join(ast.unparse(n) for n in ast.parse(MODULE).body[1:])
    for word in ("postOnly", "post_only", "stp", "STP", "timeInForce", "TimeInForce", "time_in_force", "limit_order",
                 "cancel_orders", "instructions", "leverage", "OrderDirection"):
        assert not re.search(rf"\b{word}\b", text), word
    on = _agent(uid=T1M1)
    resp = _request(on)
    placed = [ix for ix in resp.instructions if ix.type == "PLACE_ORDER_LIMIT"]
    assert len(placed) == 2 and _counts(on)["deeper_l0"] == 2
    assert all(ix.postOnly is True and ix.stp == "CANCEL_BOTH" and ix.timeInForce == "GTT" for ix in placed)


# ---- 7. wiring ------------------------------------------------------------------------------------------------------------

def test_the_switches_ship_on_and_agree_with_their_agent_defaults():
    params = t617._params()
    for key in ("research_v630_deep_tier", "research_v630_s9_lookback"):
        assert f"{key}=1 " in t617._shipped() + " " and params.count(f"{key}=") == 1, key
    assert ('research_v629_touch_fp_move=1 \\\nresearch_v630_deep_tier=1 \\\nresearch_v630_s9_lookback=1 \\\n'
            in LAUNCHER)                                                                 # v6.31's keys next
    for key in ("research_v633_deep_layer=1", "research_v629_touch_fp_move=1", "research_v626_feed_clock=1",
                "research_v69_deep_ladder=1", "research_v612_reduce_depth=1", "research_v64_vacuum=1"):
        assert f" {key} " in f" {t617._shipped()} ", key
    init = _src("_init_build_switches")
    lines = (D1_LINE, D2_LINE, "self._v6_30_identity = V6_30Identity()", FIX_LINE, "self._v6_30_on = False",
             "self._v6_30_counts: dict[str, int] = {}", "self._v6_30_errors = 0", "self._v6_30_errors_seen = 0",
             "self._v6_30_reported = False", "self._v6_30_erring = False",
             "self._v6_30_off_logged: str | None = None", "self._v6_30_drawn_seen = 0",
             "if self.research_v630_s9_lookback:", HISTORY_LINE)
    for line in lines:
        assert init.count(line) == 1, line
    idx = [init.index(x) for x in lines]
    assert init.index("self._v6_29_save_done: int | None = None") < idx[0] and idx == sorted(idx) and \
        idx[-1] < init.index("self._v69_counts: dict[str, int] = {}")
    assert init.index("self._v6_29_pages = V6_29PageHistory()") < init.index(HISTORY_LINE)


def test_every_v630_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v630_identity_profile")
    for alias in imp.names:
        assert V6_30_NAMES[alias.asname or alias.name] is getattr(ip, alias.name), alias.asname
    assert {a.asname or a.name for a in imp.names} == set(V6_30_NAMES)
    assert SIMPLE.count("from research_v630_identity_profile import (") == 1
    assert t623.V6_30_IDENTIFIERS == (set(V6_30_NAMES) | {"research_v630_deep_tier", "research_v630_s9_lookback"})
    for mod in (t624, t625, t626, t627, t628, t629):
        assert mod.V6_30_IDENTIFIERS is t623.V6_30_IDENTIFIERS, mod.__name__
    bound = [a.asname or a.name for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
    for name in V6_30_NAMES:
        assert bound.count(name) == 1, name
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    assert sorted(n for n in names if n.startswith("_v6_30_")) == sorted(METHODS)
    assert not [n for n in names if n.startswith(("_v630_", "v6_30_"))]
    assert '"V6_30_STATE"' in _src("_v6_30_report") and '"v6_30_state_row"' in _src("_v6_30_report")


def test_each_line_is_wired_once_in_its_place():
    for line in (REQUEST_LINE, TIER_LINE, PAPER_LINE, DECIDE_LINE, REPORT_LINE, FIX_LINE, HISTORY_LINE):
        assert SIMPLE.count(line) == 1, line
    body = _src("_v63_pass")
    assert body.index("v6_29_launches = self._v6_29_launches(pace)") < \
        body.index('if getattr(self, "_v6_30_identity", None) is not None:') < body.index(REQUEST_LINE) < \
        body.index("for raw_id in sorted(books")
    assert body.index("book_trades = trades") < body.index('if bool(getattr(self, "research_v630_deep_tier", False)):') \
        < body.index(PAPER_LINE) < body.index("depth = deep.observe(book_id, now_ts, trades")
    assert body.index("self._v6_29_report()") < body.index(REPORT_LINE) < body.index("return placed_total")
    deep = _src("_v633_book")
    assert deep.index("mid = 0.5 * (float(raw_bid) + float(raw_ask))") < deep.index("v6_30_tier = 0") < \
        deep.index(TIER_LINE) < deep.index("vac = None")
    assert deep.index("want = v633_deep_price(c_, depth, side") < deep.index("want = self._v6_30_deeper(") < \
        deep.index("if not spacing.allows(book_id, side, want, inv, eff, tick_size):")
    assert deep.index("price = self._v616_target(") < deep.index("v6_30_why = None") < \
        deep.index("price, v6_30_why = self._v6_30_deeper(") < deep.index("if self._v691_free_base_on():") < \
        deep.index("response.limit_order(") < deep.index("self._v6_30_placed(level, v6_30_why)") < \
        deep.index('self._v69_count("placed_l%d" % level)')
    req = _src("_v6_30_request")
    assert req.index("if ident.off is not None:") < req.index("pages.keep_s = None") < req.index("pages.hold(keep)") \
        < req.index("self._v6_30_on = on") < req.index("except Exception:")
    assert "keep_s" not in req[req.index("except Exception:"):]                  # an error leaves the horizon
    held = _src("_v6_29_held")
    assert held.index("self._v6_29_lookback = lookback") < held.index(DECIDE_LINE) < \
        held.index("side, why = v6_30_decision") < held.index("side, why = pages.decide(book_id, fp, now_ts, lookback)") < \
        held.index("self._v6_29_count(why)")
    for name in ("_v617_touch", "_v617_book", "_v617_fundamental", "_v6_25_lean", "_v6_26_observe", "respond",
                 "_research_final_validate_instructions", "_research_save_session"):
        assert "v6_30" not in _src(name) and "v630" not in _src(name), name


# ---- 8. v6.29's code, statement by statement (the committed v6.29: f72d9a3) ----------------------------------------------

V629_COMMIT = "f72d9a3"       # the user's v6.29 commit (10-14 03:14 JST): k73's verified build
# v6.31 (S12b research_v631_touch_improve, the single-write save research_v631_single_write_save) only adds -- its
# import, its _v6_31_ methods, its switches and state, and statements naming them in the touch quote, the pass, the
# session path and the session save (an if on a switch or on its state, or an assignment or call naming one) -- so the
# method-by-method checks below read v6.30 with v6.31 taken out (_Drop631; tests/test_research_v6_31.py checks it
# against the committed v6.30), and t629's _Drop630 takes v6.31's additions out with v6.30's.
V6_31_IDENTIFIERS = t623.V6_31_IDENTIFIERS
# v6.32 (S14 research_v632_fee_making, P1 research_v632_state_fundamental) only adds: the strip takes its additions out
# as well (tests/test_research_v6_32.py checks them against the committed v6.31).
V6_32_IDENTIFIERS = t623.V6_32_IDENTIFIERS
# v6.33 (D4 research_v633_touch_identity) only adds: the strip takes its additions out as well
# (tests/test_research_v6_33.py checks them against the committed v6.32).
V6_33_IDENTIFIERS = t623.V6_33_IDENTIFIERS
# v6.34 (research_v634_class1_entry) only adds: the strip takes its additions out as well
# (tests/test_research_v6_34.py checks them against v6.33).
V6_34_IDENTIFIERS = t623.V6_34_IDENTIFIERS


def _mentions631(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V6_31_IDENTIFIERS or name in V6_32_IDENTIFIERS
                                      or name in V6_33_IDENTIFIERS
                                      or name in V6_34_IDENTIFIERS
                                      or name.startswith(("_v6_31_", "v6_31_", "_v6_32_", "v6_32_", "_v6_33_",
                                                          "v6_33_", "_v6_34_", "v6_34_"))):
            return True
    return False


class _Drop631(ast.NodeTransformer):
    """Takes out what v6.31 (and v6.32, v6.33 and v6.34 after it) adds and nothing else -- its import, its _v6_31_
    methods, statements naming its identifiers (an if on one keeps its else) -- keeping every docstring: v6.30 as
    committed (f31be6d)."""

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module in ("research_v631_touch_improve",
                                                                "research_v6_32_fee_making",
                                                                "research_v6_32_state_fp",
                                                                "research_v6_33_touch_identity",
                                                                "research_v6_34_class1"):
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith(("_v6_31_", "_v6_32_", "_v6_33_", "_v6_34_")):
                continue
            if isinstance(st, ast.If) and _mentions631(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions631(st):
                continue
            out.append(st)
        return out

    def generic_visit(self, node):
        for field in ("body", "orelse", "finalbody"):
            if isinstance(getattr(node, field, None), list):
                setattr(node, field, self._stmts(getattr(node, field)))
        for h in getattr(node, "handlers", None) or []:
            h.body = self._stmts(h.body)
        for name, value in ast.iter_fields(node):
            if name in ("body", "orelse", "finalbody", "handlers"):
                continue
            if isinstance(value, list):
                setattr(node, name, [self.visit(v) if isinstance(v, ast.AST) else v for v in value])
            elif isinstance(value, ast.AST):
                setattr(node, name, self.visit(value))
        return node


def _committed(rel):
    try:
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V629_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V629_COMMIT} is not available here")
    return out.stdout


def test_v630s_strip_takes_out_its_nine_methods_and_its_statements_in_four_and_v629_is_under_both_strips():
    """Without git: Simple with v6.30's additions taken out (t629's _Drop630, v6.31's with them) has the same methods as
    v6.30 (Simple with v6.31 taken out, _Drop631) but v6.30's nine, and only the switch block, the deep layer, the pass
    and S9's decision carry v6.30 statements; with v6.29 taken out as well
    (t628's _Drop629) it is the committed v6.28."""
    stripped = t629._Drop630().visit(ast.parse(SIMPLE))
    assert not t629._mentions630(stripped)
    a, b = t629._methods(stripped), t629._methods(_Drop631().visit(ast.parse(SIMPLE)))      # v6.30: v6.31 out
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    changed = {k for k in a if a[k] != b[k]}
    assert changed == {"_init_build_switches", "_v633_book", "_v63_pass", "_v6_29_held"}, changed
    for n in ast.walk(ast.parse(SIMPLE)):
        if isinstance(n, ast.FunctionDef) and n.name.startswith(("_v6_29_", "_v6_28_")) and n.name != "_v6_29_held":
            assert not t629._mentions630(n), n.name


def test_every_v629_statement_is_in_v630_unchanged_and_v630_only_adds():
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    assert not t629._mentions630(ast.parse(old)), "v6.29 already names a v6.30 identifier: the strip would hide a change"
    stripped = t629._Drop630().visit(ast.parse(SIMPLE))
    assert ast.dump(stripped) == ast.dump(ast.parse(old))
    assert ast.dump(stripped) != ast.dump(ast.parse(SIMPLE))
    bad = SIMPLE.replace("side, why = pages.decide(book_id, fp, now_ts, lookback)",
                         "side, why = pages.decide(book_id, fp, now_ts, 2 * lookback)")
    assert bad != SIMPLE and ast.dump(t629._Drop630().visit(ast.parse(bad))) != ast.dump(ast.parse(old))


def test_only_the_switch_block_the_deep_layer_the_pass_and_s9s_decision_changed_and_nine_methods_were_added():
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    a, b = t629._methods(ast.parse(old)), t629._methods(_Drop631().visit(ast.parse(SIMPLE)))  # v6.30: v6.31 out
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    changed = {k for k in a if a[k] != b[k]}
    assert changed == {"_init_build_switches", "_v633_book", "_v63_pass", "_v6_29_held"}, changed
    for k in ("_v617_touch", "_v617_book", "_v6_29_report", "_v6_29_snapshot", "_v616_target", "_v616_judged",
              "respond", "_research_final_validate_instructions", "_research_save_session", "_v633_deep_ref",
              "_v68_no_take", "build_mm_strategy_instructions"):
        assert a[k] == b[k], k
    for other in ("Strategy1_Research.py", "Strategy1.py", "research_v6_29_touch_move.py", "research_v633_deep_layer.py",
                  "research_v616_fair_value.py", "research_v64_board.py", "research_v612_reduce_depth.py",
                  "research_v69_deep_ladder.py", "research_v66_add_spacing.py", "research_v68_deep_owns.py",
                  "research_v626_feed_clock.py", "research_v617_touch_quote.py"):
        assert _committed(f"agents/strategy/{other}") == (STRATEGY / other).read_text(), other


def test_the_launcher_only_adds_to_v629s():
    """The committed v6.29 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS' last
    key), the rest added -- the v6.30 block, two PARAMS keys and its gate entry.  v6.31 extends the same three lines and
    adds its block, two PARAMS keys and its gate entry."""
    old, new = _committed("run_strategy1_research_simple_multi.sh").splitlines(), LAUNCHER.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(x for x in old if x.startswith("A19X_BUILD=0;"))
    arm = next(x for x in old if x.startswith("  strategy1_direct_v6_3_0)"))
    assert removed == [defaults, arm, 'research_v629_touch_fp_move=1"']
    block = []
    for head in ("# v6.30.  The identity profile", "# v6.31.", "# v6.32.", "# v6.33.", "# v6.34."):
        start = LAUNCHER.index(head)
        block += LAUNCHER[start:LAUNCHER.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    assert sorted(added) == sorted([defaults + "; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0",
                                    arm + "; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1",
                                    "research_v629_touch_fp_move=1 \\", "research_v630_deep_tier=1 \\",
                                    "research_v630_s9_lookback=1 \\", "research_v631_touch_improve=1 \\",
                                    "research_v631_single_write_save=1 \\",
                                    "research_v632_fee_making=1 \\",
                                    "research_v632_fee_weight=0.005 \\",
                                    "research_v632_state_fundamental=1 \\",
                                    "research_v633_touch_identity=1 \\",
                                    'research_v634_class1_entry=0"', "      tests/test_research_v6_30.py \\",
                                    "      tests/test_research_v6_31.py \\",
                                    "      tests/test_research_v6_32.py \\",
                                    "      tests/test_research_v6_33.py \\",
                                    "      tests/test_research_v6_34.py \\", *block])


# ---- 9. the launcher -----------------------------------------------------------------------------------------------------

def test_the_v630_block_is_gated_preflighted_and_listed():
    assert "; V6_27_BUILD=0; V6_28_BUILD=0; V6_29_BUILD=0; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_28_BUILD=1; V6_29_BUILD=1; V6_30_BUILD=1; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_30_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_30_BUILD=") == 2
    assert "      tests/test_research_v6_29.py \\\n      tests/test_research_v6_30.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.29 touch on the fp move PASS') < LAUNCHER.index("# v6.30.  The identity") \
        < LAUNCHER.index('echo "[preflight] v6.30 identity profile PASS') < \
        LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')


def _block():
    start = LAUNCHER.index('if [[ "${V6_30_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.30 identity profile PASS', start)]


_PATTERN = t629._PATTERN
FILES = ("Strategy1_Research_Simple.py", "research_v630_identity_profile.py")


def test_every_v630_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 17, pats
    for needle, name, _msg in pats:
        assert name in FILES and (STRATEGY / name).read_text().count(needle) == 1, (needle, name)
    assert {p[0] for p in pats} == {
        "from research_v630_identity_profile import (", D1_LINE, D2_LINE, FIX_LINE, REQUEST_LINE, TIER_LINE, PAPER_LINE,
        DECIDE_LINE, HISTORY_LINE, REPORT_LINE, 'PROFILE_VERSION = "orthox-profile-v1"',
        "VALUES = {D1: (0, 1), D2: (1, 2)}",
        'h = hashlib.sha256(f"{PROFILE_VERSION}|{dim}|{uid}|{book}".encode()).hexdigest()',
        "return values[int(h[:8], 16) % len(values)]", "if needs_reprice(q, r, d, t):", "pages.hold(keep)",
        "if abs(q - r) / t > REPRICE_HIGH * d - 1e-9:"}


def _run_block(params, agent_path=STRATEGY, build="1"):
    start = LAUNCHER.index("# v6.30.  The identity profile")
    stop = LAUNCHER.index('echo "[preflight] v6.30 identity profile PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "V6_30_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v630_block_passes_the_shipped_params_and_refuses_a_build_without_a_switch_or_what_it_moves():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    assert out.stdout.startswith("[preflight] v6.30 identity profile PASS (each UID draws per book from its own UID"), \
        out.stdout
    for key in ("research_v630_deep_tier", "research_v630_s9_lookback"):
        for bad in (f"{key}=0", "", f"{key}=true", f"{key}=11", f"x{key}=1"):
            out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {bad} "))
            assert out.returncode == 1 and f"without {key}=1" in out.stderr and "PASS" not in out.stdout, (key, bad)
    for key in ("research_v633_deep_layer=1", "research_v629_touch_fp_move=1", "research_v626_feed_clock=1"):
        assert f" {key} " in f" {shipped} ", key
        out = _run_block(f" {shipped} ".replace(f" {key} ", f" {key[:-1]}0 "))
        assert out.returncode == 1 and f"v6.30 build without {key} in PARAMS" in out.stderr, key
        assert "PASS" not in out.stdout, key
    out = _run_block("", build="0")                                   # another build: not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v630_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = re.findall(_PATTERN, _block())
    assert len(cases) == 17
    for i, (needle, name, msg) in enumerate(cases):
        tmp = tmp_path / str(i)
        tmp.mkdir()
        for n in FILES:
            shutil.copy(STRATEGY / n, tmp / n)
        text = (tmp / name).read_text()
        assert text.count(needle) == 1, needle
        (tmp / name).write_text(text.replace(needle, "pass  # removed", 1))
        out = _run_block(t617._shipped(), agent_path=tmp)
        assert out.returncode == 1 and msg in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
