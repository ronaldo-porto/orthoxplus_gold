"""v6.33: D4 research_v633_touch_identity -- the touch dimension of the identity profile.

v6.32 + one switch, default on, its own off switch.

D4 [STRUCTURAL independence; the value set PRE-REGISTERED 10-14 JST]: each UID draws per book, from its OWN UID only, the
pre-registered profile orthox-profile-v1's touch presence (scratchpad k79/spec/profile_v1.py, sha256 31d08ace...168c):

    value = (1, 0)[ int(SHA-256('orthox-profile-v1|D4|' + str(uid) + '|' + str(book)).hexdigest()[:8], 16) mod 2 ]

1 is v6.32's touch quote on the book; 0 keeps none there -- a resting one is cancelled, none is placed (the last gate
before the slot and the price, after S9's and S14's).  The profile off (the UID unknown, negative or changed), a draw of
1 or an error: v6.32.  The deep layer and every other gate are unchanged.

No test here touches the host's caches, the network or a live tree, and nothing is imported from the scratchpad: the
golden D4 table was computed once from the frozen spec file (k96/build/golden_d4.py, its sha256 checked) and is
embedded below; the agent is t632's harness with v6.33's real methods, its real _init_build_switches statements (and
v6.30's own two identity statements for the UID it is constructed with) and the pass's real v6.33 statements.
"""
import ast
import difflib
import hashlib
import os
import re
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
STRATEGY = ROOT / "agents" / "strategy"
sys.path.insert(0, str(STRATEGY))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import research_v62_breadth as v62b  # noqa: E402
import research_v617_touch_quote as tq  # noqa: E402
import research_v630_identity_profile as ip  # noqa: E402
import research_v633_deep_layer as dl  # noqa: E402
import research_v6_32_fee_making as fm  # noqa: E402
import research_v6_33_touch_identity as ti  # noqa: E402
import test_research_v6_2_14_touch_life as t14  # noqa: E402
import test_research_v6_17 as t617  # noqa: E402
import test_research_v6_23 as t623  # noqa: E402
import test_research_v6_24 as t624  # noqa: E402
import test_research_v6_25 as t625  # noqa: E402
import test_research_v6_26 as t626  # noqa: E402
import test_research_v6_27 as t627  # noqa: E402
import test_research_v6_28 as t628  # noqa: E402
import test_research_v6_29 as t629  # noqa: E402
import test_research_v6_30 as t630  # noqa: E402
import test_research_v6_31 as t631  # noqa: E402
import test_research_v6_32 as t632  # noqa: E402
from _harness import extractor  # noqa: E402

SIMPLE = (STRATEGY / "Strategy1_Research_Simple.py").read_text()
MODULE = (STRATEGY / "research_v6_33_touch_identity.py").read_text()
LAUNCHER = (ROOT / "run_strategy1_research_simple_multi.sh").read_text()
_src = extractor(SIMPLE, cls_name="Strategy1_Research_Simple")
S = 1_000_000_000
NOW = t632.NOW
BUY, SELL = dl.SIDE_BUY, dl.SIDE_SELL
SPEC_SHA256 = "31d08ace5b99b5e3ac33e567a06ad08ef914d0edae275e7168e2246ee264168c"   # k79/spec/profile_v1.py
SWITCH_LINE = 'self.research_v633_touch_identity = self._as_bool(getattr(self.config, "research_v633_touch_identity", True))'
REQUEST_LINE = "self._v6_33_request()"
TABLE_LINE = "self._v6_33_table.extend(ident.uid, ident.table)"
OFF_LINE = "v6_33_off = self._v6_33_off(book_id)"
RULE_LINE = "return bool(v6_33_touch_off(switch, profile_on, value))"
CANCEL_LINE = "why = V6_33_CANCEL_TOUCH_IDENTITY"
WITHHELD_LINE = 'self._v6_33_count("touch_withheld")'
REPORT_LINE = "self._v6_33_report()"
V6_33_NAMES = {"V6_33_CANCEL_TOUCH_IDENTITY": ti.CANCEL_TOUCH_IDENTITY, "V6_33TouchTable": ti.TouchTable,
               "V6_33_TOUCH_IDENTITY_VERSION": ti.V6_33_TOUCH_IDENTITY_VERSION, "v6_33_touch_off": ti.touch_off}
# the names v6.33's methods reach beyond its own, from the modules Simple imports them from (never invented here)
REACHED = {"V62_STATE_EVERY_TICKS": v62b.V62_STATE_EVERY_TICKS, "V6_30_PROFILE_VERSION": ip.PROFILE_VERSION}
EXTRA = dict(t632.EXTRA, **V6_33_NAMES, **REACHED)
METHODS = ("_v6_33_request", "_v6_33_off", "_v6_33_count", "_v6_33_report", "_v6_33_snapshot")
FIELDS = ("version", "profile", "touch_identity_on", "profile_on", "uid", "off_reason", "table_uid", "table_sha256", "d4",
          "books", "touch_on_books", "touch_off_books", "books_drawn_later", "refused", "touch_withheld", "touch_cancels",
          "errors")

# The golden D4 table (k96/build/golden_d4.py: the FROZEN spec file k79/spec/profile_v1.py imported by path, its sha256
# checked, draw("D4", uid, book) over books 0-127; the build NOT imported), pasted as literals: per UID the sha256 of the
# canonical text (the digest V6_33_STATE logs), the ON and OFF counts and the draws by ascending book.  The UIDs this host
# runs or ran and the boundary UIDs: test data only -- the agent never reads a list of UIDs.
GOLDEN = {
    10: ("55b4ed79f6fbae59ef377cc47bb3461d37c4dc2745ee56a9bcc7e01eaabb8c79", 64, 64,
         "10110110110110111001010000000100001110010100101001001011001100001000001001110111101111001101011011000110110011011100101100110110"),
    30: ("00032dae5100d087aad0f9b47466c25c775243a34fea846629f8e8f34d426551", 61, 67,
         "01011001001000111001111000111001111101110110010101100011100010010001001011000100000000010110111110011101011000100010010001110110"),
    77: ("d486eb4b7b5db6e2f2fdc7567930091015eb8fd5adcf91f81973341c8e6e0161", 61, 67,
         "00010010011001000110110100001001011000000101010011011110110001111001001100111111010001011111010001110010001011001110000110110010"),
    78: ("549f2c39bab83e29279750d31078c8f04315f609dadd30fcace7b142f6db1e1a", 61, 67,
         "01110110111001000110101010001101000111001101010111100100101001010100001010100100101100010100110101011100100011110010110001010100"),
    82: ("d34833cdbabc0d4c86c24b0f5b6fd71cfb61d7e17fc01e06740b3f5d48c1cbbe", 67, 61,
         "01010111011000110110100101011000011011010000101010111111001110101000011100010110011011101011001000100010111000010111101011101101"),
    105: ("1de34fc6df4d8af70ab9d1a746ce33e7e9c76619ea81dafaee3b6a955f6f1e03", 58, 70,
          "00110111001011111010110100101100010011000001001000101110000011000011011111101100010010110000010001010110100010110100010111000010"),
    108: ("16e26b7c49b927d54c0f35d388cc68bda9cd5633835239f156063e3879090d27", 67, 61,
          "10010001101011000110001001011000101111000111100111101100010000111111110100101110101111001010001011001000001000011111011011101110"),
    161: ("e0e266b369ce96719c313ad81a73d9d3ec85942c4b7ef74b5a73a4466455acb9", 70, 58,
          "01010001100001010101101001110110010101010001101011100011100011110110100001101110010100011011011010111011111110110001011100111011"),
    115: ("aec4d18757d36b836ac45df53dbff2f31229127bfe9a1581b1252d7ce5d1911a", 57, 71,
          "00001110000101111111001101100001110000110011001010010010011101000110110101101010010110110010110001000000010010000010011000110110"),
    0: ("011d846916bd80b404a430a32cfeafc3de79b2389c9a0b02c544ded11197f753", 60, 68,
        "10010000110100010001101111011100001001011010110011001000010011010011001010010001100001000100101001000111100111111010001011101111"),
    255: ("a7f02d21ed7881be0c38d671635234079831efc8673af5ca1607266f9cd12d33", 63, 65,
          "11000101011011100010100000011101101110110101111100010000001111100001111100110100111001110101010111101010000100010000001010010110"),
}
GOLDEN_ALL = "13905b638289abf7eb1689a90f1b0d1d1790eb7598fb97e78f96a8e786c84a47"   # every UID 0-255's canonical text
GOLDEN_TEXT = "orthox-profile-v1|uid=115|D4\n0|D4=0\n1|D4=0\n2|D4=0"                 # UID 115, books 0-2


def _formula4(uid, book):
    """The spec's draw, written out again here: (1, 0)[int(sha256('orthox-profile-v1|D4|uid|book')[:8], 16) % 2]."""
    h = hashlib.sha256(("orthox-profile-v1|D4|" + str(uid) + "|" + str(book)).encode()).hexdigest()
    return (1, 0)[int(h[:8], 16) % 2]


def _uid4(d4, book=3):
    """The first UID whose book draws v6.30's (D1, D2) = (0, 1) -- t632's harness book as v6.29 had it -- and D4 = d4:
    computed, not listed."""
    for uid in range(256):
        if (t630._formula("D1", uid, book), t630._formula("D2", uid, book), _formula4(uid, book)) == (0, 1, d4):
            return uid
    raise AssertionError((d4, book))


ON_UID, OFF_UID = _uid4(1), _uid4(0)            # book 3 ON / OFF for the touch, v6.30's profile as t632's UID has it


# ---- 1. the module: the pre-registered dimension -------------------------------------------------------------------------

def test_the_module_states_its_version_the_pre_registered_dimension_and_value_set_and_labels_its_evidence():
    assert ti.V6_33_TOUCH_IDENTITY_VERSION == "touch_identity_v6_33" and ti.D4 == "D4"
    assert ti.VALUES_D4 == (1, 0) and (ti.TOUCH_ON, ti.TOUCH_OFF) == (1, 0)      # index 0: v6.32's touch quote
    assert ti.CANCEL_TOUCH_IDENTITY == "TOUCH_IDENTITY"
    assert set(ti.__all__) == {"CANCEL_TOUCH_IDENTITY", "D4", "TOUCH_OFF", "TOUCH_ON", "TouchTable",
                               "V6_33_TOUCH_IDENTITY_VERSION", "VALUES_D4", "draw", "table_digest", "table_text",
                               "touch_off"}
    doc = ast.get_docstring(ast.parse(MODULE))
    for needle in (SPEC_SHA256, "PRE-REGISTERED", "STRUCTURAL", "1.32 effective agents of 8", "94-97%", "44%",
                   "0.68-0.75", "43000 + 10 x book", "k75", "OWN UID", "not on this book", "CANCEL_TOUCH_IDENTITY",
                   "orthox-profile-v1|uid=<uid>|D4", "<book>|D4=<v>", "p = 1/2"):
        assert needle in doc, needle
    assert 'h = hashlib.sha256(f"{PROFILE_VERSION}|{D4}|{uid}|{book}".encode()).hexdigest()' in MODULE
    assert "return VALUES_D4[int(h[:8], 16) % len(VALUES_D4)]" in MODULE


def test_the_version_string_is_v630s_constant_imported_never_retyped():
    tree = ast.parse(MODULE)
    imp = [n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v630_identity_profile"]
    assert len(imp) == 1 and sorted(a.name for a in imp[0].names) == ["PROFILE_VERSION", "uid_of"]
    assert ti.PROFILE_VERSION is ip.PROFILE_VERSION and ti.uid_of is ip.uid_of
    docs = {id(b[0].value) for b in [tree.body] + [n.body for n in ast.walk(tree)
                                                   if isinstance(n, (ast.FunctionDef, ast.ClassDef))]
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant)}
    texts = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)
             and id(n) not in docs]                                  # the docstrings state the formula; the code imports it
    assert texts and not [t for t in texts if "orthox" in t], texts
    assert ip.PROFILE_VERSION == "orthox-profile-v1"


def test_the_draw_is_the_frozen_spec_formula_on_the_golden_table():
    for uid, (digest, on, off, bits) in GOLDEN.items():
        table = {b: ti.draw(uid, b) for b in range(128)}
        assert "".join(str(table[b]) for b in range(128)) == bits, uid
        assert ti.table_digest(uid, table) == digest and (bits.count("1"), bits.count("0")) == (on, off), uid
        t = ti.TouchTable()
        assert t.extend(uid, range(128)) == 128 and t.digest == digest and t.snapshot()["d4"] == bits, uid
    text = "\n".join(ti.table_text(u, {b: ti.draw(u, b) for b in range(128)}) for u in range(256))
    assert hashlib.sha256(text.encode()).hexdigest() == GOLDEN_ALL
    assert ti.table_text(115, {b: ti.draw(115, b) for b in range(3)}) == GOLDEN_TEXT
    assert set(ti.VALUES_D4) == {0, 1} and all(ti.draw(u, b) in (0, 1) for u in (0, 255) for b in range(128))


def test_the_draw_is_the_formula_for_256_uids_by_128_books_and_its_own_dimension():
    """Property: every (uid, book) of 256 x 128 is the formula written out here; the dimension string makes D4 its own
    draw -- not v6.30's D1 (whose index-0 value is 0) read through another value set."""
    for uid in range(256):
        for book in range(128):
            assert ti.draw(uid, book) == _formula4(uid, book), (uid, book)
    for uid in (8, 9, 30, 115):
        d4 = [ti.draw(uid, b) for b in range(128)]
        d1 = [ip.draw("D1", uid, b) for b in range(128)]
        assert d4 != d1 and d4 != [1 - v for v in d1], uid         # another hash: neither D1 nor its complement
    flipped = [(0, 1)[int(hashlib.sha256(f"orthox-profile-v1|D4|30|{b}".encode()).hexdigest()[:8], 16) % 2]
               for b in range(128)]
    assert [ti.draw(30, b) for b in range(128)] == [1 - v for v in flipped]      # the order (1, 0): 1 at index 0


def test_the_module_has_no_uid_specific_branch_and_no_list_of_uids():
    """The module reads the UID only as a number to hash (and v6.30's uid_of to accept it): no comparison with,
    membership in or indexing by a UID value but the table's own first UID, and no integer that could name one -- its
    numbers are the hash's 8 and 16, the values 0 and 1."""
    tree = ast.parse(MODULE)
    ints = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, int)
            and not isinstance(n.value, bool)}
    assert ints <= {0, 1, 8, 16}, ints
    assert not [n for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, float)]
    draw = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "draw")
    assert [a.arg for a in draw.args.args] == ["uid", "book"]
    assert not [n for n in ast.walk(draw) if isinstance(n, (ast.If, ast.Compare, ast.IfExp, ast.Subscript))
                and "uid" in ast.unparse(n)]
    for fn in (n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)):
        for node in ast.walk(fn):
            if isinstance(node, ast.Compare) and "uid" in ast.unparse(node) and any(
                    isinstance(c, ast.Constant) and c.value is not None for c in [node.left, *node.comparators]):
                raise AssertionError((fn.name, ast.unparse(node)))      # a UID compared with a value
            if isinstance(node, (ast.List, ast.Set, ast.Tuple)) and len(node.elts) > 2 and all(
                    isinstance(e, ast.Constant) and isinstance(e.value, int) for e in node.elts):
                raise AssertionError((fn.name, ast.unparse(node)))


def test_the_module_loads_no_lock_cache_socket_ipc_or_randomness():
    """Static: its imports are hashlib, operator, typing and v6.30's profile; its text names no host cache, lock,
    socket, process or RNG.  Run: importing it in a fresh interpreter loads none of them (v6.30's module brings v6.3.3's
    deep layer, and nothing else of ours)."""
    tree = ast.parse(MODULE)
    imported = set()
    for n in tree.body:
        if isinstance(n, ast.Import):
            imported |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            imported.add(n.module)
    assert imported == {"__future__", "hashlib", "operator", "typing", "research_v630_identity_profile"}, imported
    code = "\n".join(ast.unparse(n) for n in tree.body[1:])
    for word in ("fcntl", "flock", "socket", "/tmp", "orthoxplus_", "multiprocessing", "mmap", "subprocess", "random",
                 "secrets", "urllib", "open(", "os.", "time.", "thread", "uuid", "global "):
        assert word not in code, word
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); before = set(sys.modules); "
             "import research_v6_33_touch_identity; new = set(sys.modules) - before; "
             "bad = sorted(m for m in new if m.split('.')[0] in ('fcntl', 'socket', 'urllib', 'http', 'multiprocessing', "
             "'mmap', 'subprocess', 'random', 'secrets', 'threading', 'research_v616_fair_value', "
             "'research_v6_29_touch_move', 'os')); print(sorted(m for m in new if m.startswith('research_'))); print(bad)")
    out = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(STRATEGY)], capture_output=True, text=True,
                         timeout=120, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PYTHONDONTWRITEBYTECODE": "1"})
    assert out.returncode == 0, out.stderr
    loaded, bad = out.stdout.strip().splitlines()
    assert loaded == ("['research_v630_identity_profile', 'research_v633_deep_layer', "
                      "'research_v6_33_touch_identity']") and bad == "[]", out.stdout


def test_the_table_is_stable_across_restarts_processes_hash_seeds_and_book_order():
    a, b = ti.TouchTable(), ti.TouchTable()
    a.extend(30, range(128))
    b.extend(30, reversed(range(128)))
    assert a.table == b.table and a.digest == b.digest == GOLDEN[30][0]
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); import research_v6_33_touch_identity as ti; "
             "t = ti.TouchTable(); t.extend(int(sys.argv[2]), range(128)); print(t.digest)")
    for seed in ("0", "1", "12345"):
        out = subprocess.run([sys.executable, "-I", "-B", "-c", probe, str(STRATEGY), "30"], capture_output=True,
                             text=True, timeout=120, env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                                                          "PYTHONHASHSEED": seed, "PYTHONDONTWRITEBYTECODE": "1"})
        assert out.returncode == 0 and out.stdout.strip() == GOLDEN[30][0], (seed, out.stderr)


def test_the_table_is_drawn_once_a_book_first_seen_later_extends_it_and_nothing_is_ever_redrawn(monkeypatch):
    t = ti.TouchTable()
    assert (t.uid, t.table, t.digest, t.extended, t.refused) == (None, {}, None, 0, 0)
    assert t.extend(115, {3: "book", 5: "book", "7": "text key", -2: "bad", "x": "bad", True: "bad"}) == 3
    assert sorted(t.table) == [3, 5, 7] and t.uid == 115 and t.extended == 0
    digest = t.digest
    assert t.extend(115, [3, 5, 7]) == 0 and t.digest == digest                   # nothing drawn again
    before = dict(t.table)
    monkeypatch.setattr(ti, "draw", lambda uid, book: 1 - _formula4(uid, book))   # a redraw would now flip a value
    assert t.extend(115, range(10)) == 7 and t.extended == 7
    assert all(t.table[b] == before[b] == _formula4(115, b) for b in before)    # never changed
    assert all(t.table[b] == 1 - _formula4(115, b) for b in range(10) if b not in before)   # only new books drawn
    monkeypatch.undo()
    assert t.digest == ti.table_digest(115, t.table) != digest
    assert t.value(3) == _formula4(115, 3) and t.value(11) is None and t.value("3") is None and t.value(True) is None
    snap = t.snapshot()
    assert (snap["table_uid"], snap["books"], snap["books_drawn_later"], snap["refused"]) == (115, 10, 7, 0)
    assert snap["d4"] == "".join(str(t.table[b]) for b in range(10))
    assert (snap["touch_on_books"], snap["touch_off_books"]) == (snap["d4"].count("1"), snap["d4"].count("0"))


def test_another_uid_or_none_never_extends_the_table():
    t = ti.TouchTable()
    t.extend(30, range(4))
    table, digest = dict(t.table), t.digest
    for other in (78, None, -1, True, 30.0, "30", 31):
        assert t.extend(other, range(8)) == 0, other
    assert t.table == table and t.digest == digest and t.uid == 30 and t.refused == 7
    fresh = ti.TouchTable()
    assert fresh.extend(None, range(4)) == 0 and fresh.uid is None and fresh.refused == 1     # no UID: no table
    assert fresh.extend(30, None) == 0 and fresh.uid == 30 and fresh.table == {}              # fixed, nothing drawn
    assert fresh.extend(78, [1]) == 0 and fresh.extend(30, [1]) == 1 and fresh.extended == 0  # still its first draw
    broken = ti.TouchTable()
    assert broken.extend(30, 5) == 0 and broken.refused == 1                                   # never raises


def test_the_digest_is_reproducible_from_the_spec_text_alone():
    table = {7: 0, 2: 1, 5: 0}
    text = ti.table_text(30, table)
    assert text == "orthox-profile-v1|uid=30|D4\n2|D4=1\n5|D4=0\n7|D4=0"
    assert ti.table_digest(30, table) == hashlib.sha256(text.encode()).hexdigest()
    t = ti.TouchTable()
    t.extend(30, [7, 2, 5])
    want = "\n".join(["orthox-profile-v1|uid=30|D4"] + [f"{b}|D4={_formula4(30, b)}" for b in (2, 5, 7)])
    assert t.digest == hashlib.sha256(want.encode()).hexdigest()


def test_touch_off_is_true_only_with_the_switch_on_the_profile_on_and_a_draw_of_0():
    for switch in (True, False, 1, 0, None):
        for profile in (True, False, 1, 0, None):
            for value in (0, 1, None, False, True, 0.0, "0"):
                want = bool(switch) and bool(profile) and value == 0 and type(value) is int
                assert ti.touch_off(switch, profile, value) is want, (switch, profile, value)


# ---- 2. the agent: t632's harness with v6.33's real methods, init and pass statements -------------------------------------

_CLASSES: dict = {}


def _klass(base, names=None):
    key = (base, None if names is None else tuple(sorted((k, id(v)) for k, v in names.items())))
    if key not in _CLASSES:
        scope = dict(getattr(base, "_v63_pass").__globals__)
        scope.update(EXTRA if names is None else names)
        methods = {}
        for n in METHODS:
            ns = dict(scope)
            exec(compile(ast.Module(body=[ast.parse(_src(n)).body[0]], type_ignores=[]), f"<{n}>", "exec"), ns)
            methods[n] = ns[n]
        g = dict(getattr(base, "_v617_touch").__globals__)          # it names V6_33_CANCEL_TOUCH_IDENTITY
        g.update(EXTRA if names is None else names)
        methods["_v617_touch"] = t632._with632(getattr(base, "_v617_touch"), g)
        _CLASSES[key] = type("P633", (base,), methods)
    return _CLASSES[key]


def _mentions633(node):
    """A v6.33 name in ``node`` -- v6.33's alone: t632's _mentions633 also names v6.34's, which its _Drop633 takes out
    with v6.33's."""
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in t623.V6_33_IDENTIFIERS or name.startswith(("_v6_33_", "v6_33_"))):
            return True
    return False


def _init_source():
    """The v6.33 statements of _init_build_switches -- the real ones -- as one function of self."""
    fn = ast.parse(_src("_init_build_switches")).body[0]
    body = [st for st in fn.body if _mentions633(st)]
    assert len(body) == 7, [ast.unparse(st) for st in body]
    return "def _init633(self):\n" + textwrap.indent("\n".join(ast.unparse(st) for st in body), "    ") + "\n"


def _identity_source():
    """v6.30's own two statements that make the identity and fix the UID the agent was constructed with."""
    fn = ast.parse(_src("_init_build_switches")).body[0]
    body = [st for st in fn.body if ast.unparse(st).startswith(("self._v6_30_identity = ", "self._v6_30_identity.fix("))]
    assert len(body) == 2, [ast.unparse(st) for st in body]
    return "def _ident630(self):\n" + textwrap.indent("\n".join(ast.unparse(st) for st in body), "    ") + "\n"


def _init633(agent, *, on=True, names=None):
    ns = dict(EXTRA if names is None else names)
    exec(compile(_init_source(), "<v6.33 init>", "exec"), ns)
    cfg = dict(vars(getattr(agent, "config", None) or types.SimpleNamespace()))
    cfg.update(research_v633_touch_identity=on)
    agent.config = types.SimpleNamespace(**cfg)
    agent._as_bool = t630._as_bool()
    ns["_init633"](agent)


def _as_uid(agent, uid):
    """The agent as constructed with ``uid`` (None: unknown): self.uid, then v6.30's real identity statements."""
    agent.uid = uid
    ns = dict(EXTRA)
    exec(compile(_identity_source(), "<v6.30 identity>", "exec"), ns)
    ns["_ident630"](agent)
    return agent


def _agent(*, uid=ON_UID, on=True, names=None, est=None, **kw):
    """t632's harness agent (book 3 100.00 / 100.03, level 0 resting at 99.80 / 100.23, the fundamental 38 bps above its
    mid unless ``page`` says otherwise; S14's window empty unless ``est`` -- so v6.32 arms the touch buy on book 3)
    constructed with ``uid``, with v6.33's methods and the v6.33 state its real _init_build_switches statements leave."""
    agent = _as_uid(t632._agent(est=est, **kw), uid)
    agent.__class__ = _klass(type(agent), names)
    _init633(agent, on=on, names=names)
    return agent


def _ref(*, uid=ON_UID, est=None, **kw):
    """v6.32 exactly: t632's harness agent constructed with ``uid``, no v6.33 attribute at all."""
    return _as_uid(t632._agent(est=est, **kw), uid)


_request = t632._request
_out = t632._out
_touch, _rest_of = t632._touch, t632._rest_of
TOUCH_BUY = t632.TOUCH_BUY
BELOW_PAGE = t632.BELOW_PAGE


def _counts(agent):
    return dict(agent._v6_33_counts)


def _rows(agent, event="V6_33_STATE"):
    return [kw for e, kw in agent.emitted if e == event]


def _others_rows(agent):
    """Every other row of the agent, its wall-clock timings left out (v6.21's window scan, v6.32's tracker time)."""
    return [(e, {k: v for k, v in kw.items() if k != "ms" and not k.endswith("_ms") and "_ms_" not in k})
            for e, kw in agent.emitted if e != "V6_33_STATE"]


def _book_with_touch():
    """Book 3 with our touch buy (order 23, client 43031) resting at 100.01 inside the others' 100.00 / 100.03."""
    return t631._book(bids=[t14.Level(100.01, [t14.Order(23, 2.0, 43031)]), t14.Level(100.00, [t14.Order(1, 2.5)])],
                      asks=[t14.Level(100.03, [t14.Order(2, 2.5)])])


def _rest_touch(agent):
    t14._rest(agent, 23, 3, 0, 100.01, cid=43031, qty=2.0)


def test_the_harness_uids_draw_book_3_on_and_off_and_keep_v630s_book_3_as_t632s():
    assert (ON_UID, OFF_UID) == (9, 8) and OFF_UID == t631.UID       # t632's own harness UID draws book 3 OFF
    for uid, d4 in ((ON_UID, 1), (OFF_UID, 0)):
        assert ti.draw(uid, 3) == d4 and (ip.draw("D1", uid, 3), ip.draw("D2", uid, 3)) == (0, 1), uid
    assert _out(_request(_ref(uid=ON_UID))) == _out(_request(_ref(uid=OFF_UID))) == ([TOUCH_BUY], [])


def test_the_init_statements_set_the_state_and_the_switch_ships_default_on():
    agent = _agent()
    assert agent.research_v633_touch_identity is True and isinstance(agent._v6_33_table, ti.TouchTable)
    assert (agent._v6_33_table.uid, agent._v6_33_table.table) == (None, {})
    assert (agent._v6_33_counts, agent._v6_33_errors, agent._v6_33_errors_seen) == ({}, 0, 0)
    assert (agent._v6_33_reported, agent._v6_33_erring) == (False, False)
    a = _agent()
    a.config = types.SimpleNamespace()                               # no key: on
    ns = dict(EXTRA)
    exec(compile(_init_source(), "<v6.33 init>", "exec"), ns)
    ns["_init633"](a)
    assert a.research_v633_touch_identity is True
    for bad in ("0", "false", 0, False):
        a.config = types.SimpleNamespace(research_v633_touch_identity=bad)
        ns["_init633"](a)
        assert a.research_v633_touch_identity is False, bad
    assert _agent(on=False).research_v633_touch_identity is False


def test_an_on_book_is_v632s_request_for_request():
    for page in ("default", BELOW_PAGE):
        for inv in (None, 3.0, -3.0):
            for rest in (True, False):
                on, ref = _agent(uid=ON_UID, page=page, inv=inv, rest=rest), _ref(uid=ON_UID, page=page, inv=inv,
                                                                                 rest=rest)
                case = (page != "default", inv, rest)
                for k in range(2):
                    got = _out(_request(on, ts=NOW + k * S, wall=1000.0 + k))
                    want = _out(_request(ref, ts=NOW + k * S, wall=1000.0 + k))
                    assert got == want, (case, k)
                assert _counts(on) == {} and on._v6_33_errors == 0, case
                assert _others_rows(on) == _others_rows(ref), case
                assert on._v6_33_table.table == {3: 1} and on._v6_33_table.uid == ON_UID, case


def test_an_off_book_places_no_touch_quote_and_withholds_exactly_the_quote_v632_arms():
    on, ref = _agent(uid=OFF_UID), _ref(uid=OFF_UID)
    want, got = _out(_request(ref)), _out(_request(on))
    assert want == ([TOUCH_BUY], []) and got == ([], [])             # v6.32 arms the touch buy; D4 0 places none
    assert _rest_of(got) == _rest_of(want) and _counts(on) == {"touch_withheld": 1}
    assert on._v6_33_table.table == {3: 0} and on._v6_33_errors == 0
    placed = {k: v for k, v in ref._v617_counts.items() if not k.startswith("placed_")}
    assert dict(on._v617_counts) == placed                            # v6.17's own tally: all but the placement
    assert on._v6_29_counts == ref._v6_29_counts and on._v6_32_counts == ref._v6_32_counts
    assert ref._v6_31_counts and not on._v6_31_counts               # S12b never priced a quote D4 withheld
    below_on, below_ref = _agent(uid=OFF_UID, inv=3.0, page=BELOW_PAGE), _ref(uid=OFF_UID, inv=3.0, page=BELOW_PAGE)
    want = _out(_request(below_ref))
    assert _touch(want)[0] == [(3, "SELL", 43032, 100.02, 2.0)]      # the fundamental below: v6.32 sells
    got = _out(_request(below_on))
    assert _touch(got) == ([], []) and _rest_of(got) == _rest_of(want) and _counts(below_on) == {"touch_withheld": 1}


def test_an_off_book_cancels_a_resting_touch_quote_adding_or_reducing_with_v633s_reason():
    for inv in (3.0, -3.0, None):                                    # long: the buy adds; short: it reduces; flat
        on, ref, keep = _agent(uid=OFF_UID, inv=inv), _ref(uid=OFF_UID, inv=inv), _agent(uid=ON_UID, inv=inv)
        for a in (on, ref, keep):
            _rest_touch(a)
        want = _out(_request(ref, book=_book_with_touch()))
        got = _out(_request(on, book=_book_with_touch()))
        assert (3, 23) not in want[1] and _touch(want) == ([], []), inv          # v6.32 keeps it resting
        assert _touch(got) == ([], [(3, 23)]) and _rest_of(got) == _rest_of(want), inv
        assert on._v617_counts.get("cancel_touch_identity") == 1 and _counts(on) == {"touch_cancels": 1}, inv
        assert on._v617_counts.get("wait_cancel_seen") == 1                       # no new quote this request
        assert _out(_request(keep, book=_book_with_touch())) == want and _counts(keep) == {}, inv     # ON: v6.32


def test_the_d4_gates_come_after_s14_so_s14s_reason_and_tally_stay_v632s():
    """On a book S14 gates (0.6.4, a pooled estimate below W x the fee): S14 withholds the adding touch quote and
    cancels a resting adding one with its own reason and tally -- D4 counts nothing; a reducing quote, which S14 keeps,
    D4 removes on an OFF book."""
    on, ref = _agent(uid=OFF_UID, est=1.5e-5), _ref(uid=OFF_UID, est=1.5e-5)
    assert _out(_request(on)) == _out(_request(ref)) == ([], [(3, 21), (3, 22)])
    assert on._v6_32_counts == ref._v6_32_counts and on._v6_32_counts["touch_withheld"] == 1 and _counts(on) == {}
    for inv, reason in ((3.0, "cancel_v632_fee_gate"), (-3.0, "cancel_touch_identity")):
        on, ref = _agent(uid=OFF_UID, est=1.5e-5, inv=inv), _ref(uid=OFF_UID, est=1.5e-5, inv=inv)
        for a in (on, ref):
            _rest_touch(a)
        got, want = _out(_request(on, book=_book_with_touch())), _out(_request(ref, book=_book_with_touch()))
        assert (3, 23) in got[1] and on._v617_counts.get(reason) == 1, inv
        if inv > 0:                                                  # S14's: v6.32 exactly, D4 untouched
            assert got == want and _counts(on) == {} and "cancel_touch_identity" not in on._v617_counts
        else:                                                        # S14 keeps the reducing buy; D4 does not
            assert (3, 23) not in want[1] and _counts(on) == {"touch_cancels": 1}
            assert on._v6_32_counts == ref._v6_32_counts


def test_the_deep_layer_is_v632s_on_an_off_book():
    for page in ("default", BELOW_PAGE):
        for inv in (None, 3.0, -3.0):
            for rest in (True, False):                               # False: the deep layer places first
                on, ref = _agent(uid=OFF_UID, page=page, inv=inv, rest=rest), _ref(uid=OFF_UID, page=page, inv=inv,
                                                                                   rest=rest)
                case = (page != "default", inv, rest)
                got, want = _out(_request(on)), _out(_request(ref))
                assert _rest_of(got) == _rest_of(want) and _touch(got)[0] == [], case
                assert on._v633_counts == ref._v633_counts and on._v6_30_counts == ref._v6_30_counts, case
                assert _counts(on).get("touch_withheld", 0) == len(_touch(want)[0]), case   # exactly v6.32's armed


def test_with_the_switch_off_every_request_is_v632s_exactly():
    for page in ("default", BELOW_PAGE):
        for inv in (None, 3.0, -3.0):
            for rest in (True, False):
                for uid in (OFF_UID, ON_UID):
                    off, ref = _agent(uid=uid, on=False, page=page, inv=inv, rest=rest), _ref(uid=uid, page=page,
                                                                                            inv=inv, rest=rest)
                    case = (page != "default", inv, rest, uid)
                    assert _out(_request(off)) == _out(_request(ref)), case
                    assert _others_rows(off) == _others_rows(ref) and _counts(off) == {} and off._v6_33_errors == 0, case
                    assert off._v6_33_table.table == {} and off._v6_33_table.uid is None, case   # nothing drawn
    off = _agent(uid=OFF_UID, on=False)
    off._v6_30_on = True
    assert off._v6_33_off(3) is False and off._v6_33_table.table == {}


def test_the_profile_off_keeps_every_book_on_and_says_why():
    for uid, why in ((None, ip.OFF_UNKNOWN), (-1, ip.OFF_NEGATIVE)):
        agent, ref = _agent(uid=uid), _ref(uid=uid)
        agent._tick = ref._tick = 1
        assert _out(_request(agent)) == _out(_request(ref)) == ([TOUCH_BUY], []), uid
        assert agent._v6_30_on is False and agent._v6_33_table.table == {} and _counts(agent) == {}, uid
        row = _rows(agent)[0]
        assert (row["profile_on"], row["uid"], row["off_reason"], row["books"]) == (0, None, why, 0), uid
        assert (row["refused"], row["table_uid"], row["errors"]) == (0, None, 0), uid    # no draw was even tried
    changed, ref = _agent(uid=OFF_UID), _ref(uid=OFF_UID)
    assert _out(_request(changed)) == ([], []) and _out(_request(ref)) == ([TOUCH_BUY], [])
    changed.uid = 12345                                              # self.uid no longer the UID fixed: off for good
    for k in (1, 2):
        got = _out(_request(changed, ts=NOW + k * S, wall=1000.0 + k))
        assert got == _out(_request(ref, ts=NOW + k * S, wall=1000.0 + k)) and _touch(got)[0] == [TOUCH_BUY], k
        assert changed._v6_30_identity.off == ip.OFF_CHANGED and changed._v6_30_on is False, k
        changed.uid = OFF_UID                                        # never back on in this process
    assert changed._v6_33_table.table == {3: 0} and _counts(changed) == {"touch_withheld": 1}     # the table stays


def test_the_table_is_drawn_from_the_profiles_books_once_per_request_and_a_book_first_seen_later_extends_it():
    agent = _agent(uid=OFF_UID)
    _request(agent)
    table = agent._v6_33_table
    assert (table.uid, table.table, table.extended) == (OFF_UID, {3: 0}, 0)
    assert sorted(agent._v6_30_identity.table) == [3]
    assert table.digest == ti.table_digest(OFF_UID, {3: 0})
    agent._v6_30_identity.extend([3, 5, 9])                          # v6.30 draws two books first seen later
    agent._v6_33_request()
    assert sorted(table.table) == [3, 5, 9] and table.extended == 2
    assert all(table.table[b] == _formula4(OFF_UID, b) for b in (3, 5, 9))
    assert agent._v6_33_off(11) is (_formula4(OFF_UID, 11) == 0)    # a book the profile has not drawn: drawn here
    assert sorted(table.table) == [3, 5, 9, 11] and table.extended == 3 and agent._v6_33_errors == 0
    quiet, quiet_ref = _agent(uid=OFF_UID), _ref(uid=OFF_UID)
    quiet.research_v617_touch_quote = quiet_ref.research_v617_touch_quote = False      # no touch path: the pass's draw
    assert _out(_request(quiet)) == _out(_request(quiet_ref))
    assert quiet._v6_33_table.table == {3: 0} and _counts(quiet) == {} and quiet._v6_33_errors == 0
    whole = _agent(uid=30)
    whole._v6_30_request(types.SimpleNamespace(books={b: object() for b in range(128)}, accounts=None))
    whole._v6_33_request()
    snap = whole._v6_33_table.snapshot()
    assert (snap["table_sha256"], snap["touch_on_books"], snap["touch_off_books"], snap["d4"]) == \
        (GOLDEN[30][0], GOLDEN[30][1], GOLDEN[30][2], GOLDEN[30][3])         # the frozen spec's table, in the agent


def test_v6_33_off_is_on_only_with_the_switch_the_profile_and_a_draw_of_0():
    for uid, d4 in ((OFF_UID, 0), (ON_UID, 1)):
        agent = _agent(uid=uid)
        _request(agent)
        assert agent._v6_30_on is True
        assert agent._v6_33_off(3) is (d4 == 0), uid
        agent.research_v633_touch_identity = False
        assert agent._v6_33_off(3) is False, uid                     # the switch off
        agent.research_v633_touch_identity = True
        agent._v6_30_on = False
        assert agent._v6_33_off(3) is False, uid                     # the profile off this request
        agent._v6_30_on = True
        agent._v6_33_table = None
        assert agent._v6_33_off(3) is False and agent._v6_33_errors == 1, uid       # an error: v6.32, counted


def test_every_v633_path_fails_open_to_v632_and_counts():
    def boom(*a, **k):
        raise RuntimeError("boom")

    class Broken(ti.TouchTable):
        def value(self, book):
            raise RuntimeError("boom")

        def extend(self, uid, book_ids):
            raise RuntimeError("boom")
    for names, errors in ((dict(EXTRA, v6_33_touch_off=boom), 1), (dict(EXTRA, V6_33TouchTable=Broken), 2)):
        on, ref = _agent(uid=OFF_UID, names=names), _ref(uid=OFF_UID)
        assert _out(_request(on)) == _out(_request(ref)) == ([TOUCH_BUY], []), names
        assert on._v6_33_errors == errors and _counts(on) == {}, names
    agent = _agent(uid=OFF_UID)
    agent._v6_33_counts = None                                       # the tally rebuilds itself
    agent._v6_33_count("x")
    assert agent._v6_33_counts == {"x": 1}
    agent._v6_33_counts = {"x": "y"}
    agent._v6_33_count("x")
    assert agent._v6_33_errors == 1
    agent._v6_30_on, agent._v6_30_identity = True, None              # no identity: the request draws nothing
    agent._v6_33_request()
    assert agent._v6_33_errors == 2 and agent._v6_33_table.table == {}


# ---- 3. the V6_33_STATE row ---------------------------------------------------------------------------------------------

def test_the_state_row_comes_at_the_first_pass_every_100_ticks_and_at_the_first_error():
    agent = _agent(uid=OFF_UID)
    flag = {"boom": False}

    def maybe(*a, **k):
        if flag["boom"]:
            raise RuntimeError("boom")
        return ti.touch_off(*a, **k)
    agent2 = _agent(uid=OFF_UID, names=dict(EXTRA, v6_33_touch_off=maybe))
    for a in (agent, agent2):
        for i, tick in enumerate((1, 2, 99, 100, 101)):
            a._tick = tick
            _request(a, ts=NOW + i * S, wall=1000.0 + i)
    rows = _rows(agent)
    assert [(r["reason"], r["tick"]) for r in rows] == [("start", 1), ("periodic", 100)]
    assert tuple(k for k in rows[0] if k not in ("reason", "tick")) == FIELDS
    first = rows[0]
    assert (first["version"], first["profile"], first["touch_identity_on"], first["profile_on"]) == \
        ("touch_identity_v6_33", "orthox-profile-v1", 1, 1)
    assert (first["uid"], first["off_reason"], first["table_uid"], first["books"]) == (OFF_UID, None, OFF_UID, 1)
    assert (first["touch_on_books"], first["touch_off_books"], first["d4"]) == (0, 1, "0")
    assert first["table_sha256"] == ti.table_digest(OFF_UID, {3: 0})
    assert (first["books_drawn_later"], first["refused"], first["touch_cancels"], first["errors"]) == (0, 0, 0, 0)
    assert first["touch_withheld"] == 1
    for k, (tick, boom) in enumerate(((102, True), (103, True), (104, False), (105, True))):
        flag["boom"] = boom
        agent2._tick = tick
        _request(agent2, ts=NOW + (5 + k) * S, wall=1005.0 + k)
    assert [(r["reason"], r["tick"]) for r in _rows(agent2)] == [("start", 1), ("periodic", 100), ("error", 102),
                                                                  ("error", 105)]
    assert _rows(agent2)[-1]["errors"] == 3


def test_the_row_never_fails_the_request_and_waits_for_the_reply():
    bad, ref = _agent(uid=OFF_UID), _agent(uid=OFF_UID)
    emitted = bad.emitted
    bad._emit = lambda event, force=False, **kw: (1 / 0) if event == "V6_33_STATE" else emitted.append((event, kw))
    bad._tick = ref._tick = 1
    assert _out(_request(bad)) == _out(_request(ref)) and bad._v6_33_errors == 1 and _rows(bad) == []
    deferred = _agent(uid=OFF_UID)
    work = []
    deferred._v629_request_work = types.SimpleNamespace(add=lambda name, fn: work.append((name, fn)))
    deferred._tick = 1
    _request(deferred)
    names = [n for n, _fn in work]
    assert names[-3:] == ["v6_31_state_row", "v6_32_state_row", "v6_33_state_row"] and _rows(deferred) == []
    dict(work)["v6_33_state_row"]()
    assert _rows(deferred)[0]["reason"] == "start"


def test_off_the_row_says_so_and_an_agent_without_the_build_has_none():
    off = _agent(uid=OFF_UID, on=False)
    off._tick = 1
    _request(off)
    row = _rows(off)[0]
    assert (row["touch_identity_on"], row["profile_on"], row["books"], row["touch_withheld"], row["errors"]) == \
        (0, 1, 0, 0, 0)
    assert (row["uid"], row["table_uid"], row["table_sha256"], row["d4"]) == (OFF_UID, None, None, "")
    older = _ref(uid=OFF_UID)
    older._tick = 1
    _request(older)
    assert not _rows(older)


# ---- 4. wiring ----------------------------------------------------------------------------------------------------------

def test_the_switch_ships_on_after_v632s_keys_and_agrees_with_its_agent_default():
    params = t617._params()
    assert "research_v633_touch_identity=1 " in t617._shipped() + " " and params.count("research_v633_touch_identity=") == 1
    assert 'research_v632_state_fundamental=1 \\\nresearch_v633_touch_identity=1 \\\n' in LAUNCHER   # v6.34's next
    assert " research_v617_touch_quote=1 " in f" {t617._shipped()} "                   # the quote D4 places or not
    init = _src("_init_build_switches")
    lines = (SWITCH_LINE, "self._v6_33_table = V6_33TouchTable()", "self._v6_33_counts: dict[str, int] = {}",
             "self._v6_33_errors = 0", "self._v6_33_errors_seen = 0", "self._v6_33_reported = False",
             "self._v6_33_erring = False")
    for line in lines:
        assert init.count(line) == 1, line
    idx = [init.index(x) for x in lines]
    assert init.index("self._v6_32_erring = False") < idx[0] and idx == sorted(idx) \
        and idx[-1] < init.index("self._v69_counts: dict[str, int] = {}")
    assert init.index("self._v6_30_identity.fix(getattr(self, \"uid\", None))") < idx[0]   # the UID is v6.30's


def test_every_v633_name_the_agent_imports_is_the_modules():
    tree = ast.parse(SIMPLE)
    imp = next(n for n in tree.body if isinstance(n, ast.ImportFrom) and n.module == "research_v6_33_touch_identity")
    for alias in imp.names:
        assert V6_33_NAMES[alias.asname or alias.name] is getattr(ti, alias.name), alias.asname
    assert SIMPLE.count("from research_v6_33_touch_identity import (") == 1
    assert sorted(a.asname or a.name for a in imp.names) == sorted(V6_33_NAMES)
    assert t623.V6_33_IDENTIFIERS == set(V6_33_NAMES) | {"research_v633_touch_identity"}
    for mod in (t624, t625, t626, t627, t628, t629, t630, t631, t632):
        assert mod.V6_33_IDENTIFIERS is t623.V6_33_IDENTIFIERS, mod.__name__
    bound = [a.asname or a.name for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom)) for a in n.names]
    for name in list(V6_33_NAMES) + list(REACHED):              # bound at module level, as the harness binds them
        assert bound.count(name) == 1, name
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Strategy1_Research_Simple")
    names = [n.name for n in cls.body if isinstance(n, ast.FunctionDef)]
    assert sorted(n for n in names if n.startswith("_v6_33_")) == sorted(METHODS)
    assert '"V6_33_STATE"' in _src("_v6_33_report") and '"v6_33_state_row"' in _src("_v6_33_report")
    assert not [n for n in names if n.startswith("_v633_") and "touch_identity" in n]      # v6.3.3's prefix untouched


def test_each_line_is_wired_once_in_its_place():
    for line in (SWITCH_LINE, REQUEST_LINE, TABLE_LINE, OFF_LINE, RULE_LINE, CANCEL_LINE, WITHHELD_LINE, REPORT_LINE,
                 'self._v6_33_count("touch_cancels")'):
        assert SIMPLE.count(line) == 1, line
    body = _src("_v63_pass")
    assert body.index("self._v6_30_request(state)") < body.index('if bool(getattr(self, "research_v633_touch_identity"') \
        < body.index(REQUEST_LINE) < body.index("v619_on = bool(getattr(self") < body.index("for raw_id in sorted(books")
    assert body.index("self._v6_32_report()") < body.index(REPORT_LINE) < body.index("return placed_total")
    touch = _src("_v617_touch")
    assert touch.index("v6_32_gated = self._v6_32_touch_sides(book_id)") < touch.index("v6_33_off = False") < \
        touch.index(OFF_LINE) < touch.index("doomed = []")
    assert touch.index("why = V6_32_CANCEL_FEE_GATE") < touch.index(CANCEL_LINE) < \
        touch.index('self._v6_33_count("touch_cancels")') < touch.index("cancelled = self._v617_cancel(response")
    assert touch.index('self._v6_32_count("touch_withheld")') < touch.index(WITHHELD_LINE) < \
        touch.index("if V617_TOUCH_SLOT in") < touch.index("price = v617_wanted_price(")
    for name in ("_v617_book", "_v617_cancel", "_v617_fundamental", "_v633_book", "respond", "_v6_30_request",
                 "_v6_32_request", "_v6_32_note", "_research_final_validate_instructions", "_research_save_session"):
        assert "v6_33" not in _src(name) and "v633_touch" not in _src(name), name
    assert "v6_30" not in touch and "v630" not in touch               # t630's pin on the touch quote holds


def test_the_d4_checks_sit_right_after_s14_in_both_chains():
    """On the real _v617_touch: in the resting loop the D4 check is the statement right after S14's and right before
    v6.17's keep / doom; in the new-quote chain it is the statement right after S14's withheld gate and right before
    the slot check (the try) -- the last gate before the slot and the price."""
    fn = ast.parse(_src("_v617_touch")).body[0]
    loop = next(n for n in fn.body if isinstance(n, ast.For))
    tests = [ast.unparse(st.test) for st in loop.body if isinstance(st, ast.If)]
    i = tests.index("why is None and v6_32_gated and (side in v6_32_gated)")
    assert tests[i + 1] == "why is None and v6_33_off" and tests[i + 2] == "why is None"
    branch = [st for st in loop.body if isinstance(st, ast.If)][i + 1]
    assert [ast.unparse(s) for s in branch.body] == ["why = V6_33_CANCEL_TOUCH_IDENTITY",
                                                     "self._v6_33_count('touch_cancels')"]
    top = [ast.unparse(st.test) if isinstance(st, ast.If) else type(st).__name__ for st in fn.body]
    j = top.index("v6_32_gated and toward in v6_32_gated")
    assert top[j + 1] == "v6_33_off" and top[j + 2] == "Try"
    gate = fn.body[j + 1]
    assert [ast.unparse(s) for s in gate.body] == ["self._v6_33_count('touch_withheld')", "return 0"]
    first = top.index("bool(getattr(self, 'research_v633_touch_identity', False))")     # the book's presence, once
    assert top[first - 2] == "bool(getattr(self, 'research_v632_fee_making', False))"
    assert ast.unparse(fn.body[first - 1]) == "v6_33_off = False"
    assert [ast.unparse(s) for s in fn.body[first].body] == [OFF_LINE]
    assert ast.unparse(fn.body[first + 1]) == "doomed = []"


def test_the_module_never_writes_an_order_flag_and_v633s_methods_place_nothing():
    texts = [ast.unparse(st) for st in ast.walk(ast.parse(SIMPLE))
             if isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_33_")]
    text = "\n".join(texts) + "\n" + MODULE
    for word in ("postOnly", "post_only", "stp", "STP", "timeInForce", "TimeInForce", "limit_order", "cancel_orders",
                 "instructions", "leverage", "OrderDirection", "clientOrderId"):
        assert not re.search(rf"\b{word}\b", text), word


# ---- 5. v6.32's code, statement by statement (the committed v6.32: 9a6ec31) --------------------------------------------

V632_COMMIT = "9a6ec31"       # the user's v6.32 commit (LIVE on all eight agents 10-16 09:06-09:21 JST): k91's FROZEN_2
# v6.34 (research_v634_class1_entry) only adds -- its import, its _v6_34_ methods, its switch and state, and statements
# naming them in respond(), v6.32's tracker hook, the pass and the final validator (an if on the switch or on its state,
# or an assignment or call naming one) -- so the method-by-method checks below read v6.33 with v6.34 taken out
# (_Drop634; tests/test_research_v6_34.py checks it), and t632's _Drop633 takes v6.34's additions out with v6.33's.
V6_34_IDENTIFIERS = t623.V6_34_IDENTIFIERS


def _mentions634(node):
    for n in ast.walk(node):
        name = n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else \
            n.arg if isinstance(n, (ast.arg, ast.keyword)) else n.value if isinstance(n, ast.Constant) else None
        if isinstance(name, str) and (name in V6_34_IDENTIFIERS or name.startswith(("_v6_34_", "v6_34_"))):
            return True
    return False


class _Drop634(ast.NodeTransformer):
    """Takes out what v6.34 adds and nothing else -- its import, its _v6_34_ methods, statements naming its identifiers
    (an if on one keeps its else) -- keeping every docstring: v6.33 as frozen (k96 FROZEN_2)."""

    def _stmts(self, stmts):
        out = []
        for st in stmts:
            if isinstance(st, ast.ImportFrom) and st.module == "research_v6_34_class1":
                continue
            if isinstance(st, ast.FunctionDef) and st.name.startswith("_v6_34_"):
                continue
            if isinstance(st, ast.If) and _mentions634(st.test):
                out.extend(self._stmts(st.orelse))
                continue
            st = self.visit(st)
            if isinstance(st, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Expr)) and _mentions634(st):
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
        out = subprocess.run(["git", "-c", "safe.directory=*", "show", f"{V632_COMMIT}:{rel}"], cwd=ROOT,
                             capture_output=True, text=True, timeout=120, env=dict(os.environ, GIT_OPTIONAL_LOCKS="0"))
    except Exception:
        out = None
    if out is None or out.returncode != 0:
        pytest.skip(f"git or {V632_COMMIT} is not available here")
    return out.stdout


CHANGED = {"_init_build_switches", "_v63_pass", "_v617_touch"}


def test_v633s_strip_takes_out_its_methods_and_its_statements_in_three_and_v632_is_under_both_strips():
    stripped = t632._Drop633().visit(ast.parse(SIMPLE))
    assert not t632._mentions633(stripped)
    a, b = t629._methods(stripped), t629._methods(_Drop634().visit(ast.parse(SIMPLE)))      # v6.33: v6.34 out
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    assert {k for k in a if a[k] != b[k]} == CHANGED
    assert not t631._mentions632(t631._Drop632().visit(ast.parse(SIMPLE)))
    for n in ast.walk(ast.parse(SIMPLE)):
        if isinstance(n, ast.FunctionDef) and n.name.startswith(("_v6_32_", "_v6_31_", "_v6_30_")):
            assert not _mentions633(n), n.name


def test_every_v632_statement_is_in_v633_unchanged_and_v633_only_adds():
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    assert not t632._mentions633(ast.parse(old)), "v6.32 names a v6.33 identifier: the strip would hide a change"
    stripped = t632._Drop633().visit(ast.parse(SIMPLE))
    assert ast.dump(stripped) == ast.dump(ast.parse(old))
    assert ast.dump(stripped) != ast.dump(ast.parse(SIMPLE))
    for good, bad in (("why = V6_32_CANCEL_FEE_GATE", "why = V617_CANCEL_BOUND"),
                      ('self._v6_32_count("touch_withheld")', 'self._v6_32_count("touch_held")'),
                      ("if V617_TOUCH_SLOT in (self._v69_live_slots(book_id).get(toward) or frozenset()):",
                       "if V617_TOUCH_SLOT in (self._v69_live_slots(book_id).get(toward) or set()):"),
                      ("self._v6_30_request(state)", "self._v6_30_request(None)")):
        assert SIMPLE.count(good) == 1, good
        mutated = SIMPLE.replace(good, bad)
        assert ast.dump(t632._Drop633().visit(ast.parse(mutated))) != ast.dump(ast.parse(old)), good


def test_only_three_methods_changed_and_the_frozen_files_modules_and_protocol_are_v632s():
    old = _committed("agents/strategy/Strategy1_Research_Simple.py")
    a, b = t629._methods(ast.parse(old)), t629._methods(_Drop634().visit(ast.parse(SIMPLE)))  # v6.33: v6.34 out
    assert set(b) - set(a) == set(METHODS) and not set(a) - set(b)
    assert {k for k in a if a[k] != b[k]} == CHANGED
    for k in ("_v617_book", "_v617_cancel", "_v617_shut", "_v617_fundamental", "_v616_book", "_v6_29_held",
              "_v6_30_request", "_v6_30_tier", "_v6_30_report", "_v6_31_note", "_v6_31_price", "_v6_32_request",
              "_v6_32_note", "_v6_32_touch_sides", "_v6_32_report", "_v633_book", "_v6_23_against", "_v6_25_lean",
              "respond", "_a191_service_reprice_cancels", "_v6214_service_touch_life",
              "_research_final_validate_instructions", "_research_save_session", "_research_session_path",
              "onOrderAccepted", "update"):
        assert a[k] == b[k], k
    for other in ("Strategy1_Research.py", "research_v617_touch_quote.py", "research_v616_fair_value.py",
                  "research_v6_29_touch_move.py", "research_v630_identity_profile.py", "research_v631_touch_improve.py",
                  "research_v6_32_fee_making.py", "research_v6_32_state_fp.py", "research_v633_deep_layer.py",
                  "research_v641_pace.py", "research_direct_exit_refresh.py", "research_v6214_touch_life.py"):
        assert _committed(f"agents/strategy/{other}") == (STRATEGY / other).read_text(), other
    assert _committed("taos/im/protocol/models.py") == (ROOT / "taos" / "im" / "protocol" / "models.py").read_text()


def test_the_launcher_only_adds_to_v632s():
    """The committed v6.32 launcher line by line: three lines extended (the flag defaults, the v6_3_0 arm, PARAMS' last
    key), the rest added -- the v6.33 block, one PARAMS key and its gate entry.  v6.34 extends the same three lines
    and adds its block, one PARAMS key and its gate entry."""
    old, new = _committed("run_strategy1_research_simple_multi.sh").splitlines(), LAUNCHER.splitlines()
    removed, added = [], []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed += old[i1:i2]
        if tag in ("replace", "insert"):
            added += new[j1:j2]
    defaults = next(x for x in old if x.startswith("A19X_BUILD=0;"))
    arm = next(x for x in old if x.startswith("  strategy1_direct_v6_3_0)"))
    assert removed == [defaults, arm, 'research_v632_state_fundamental=1"']
    block = []
    for head in ("# v6.33.", "# v6.34."):
        start = LAUNCHER.index(head)
        block += LAUNCHER[start:LAUNCHER.index("\nfi\n", start) + len("\nfi\n")].splitlines() + [""]
    assert sorted(added) == sorted([defaults + "; V6_33_BUILD=0; V6_34_BUILD=0", arm + "; V6_33_BUILD=1; V6_34_BUILD=1",
                                    "research_v632_state_fundamental=1 \\", "research_v633_touch_identity=1 \\",
                                    'research_v634_class1_entry=1"',
                                    "      tests/test_research_v6_33.py \\",
                                    "      tests/test_research_v6_34.py \\", *block])


# ---- 6. the launcher ----------------------------------------------------------------------------------------------------

def test_the_v633_block_is_gated_preflighted_and_listed():
    assert "; V6_30_BUILD=0; V6_31_BUILD=0; V6_32_BUILD=0; V6_33_BUILD=0; V6_34_BUILD=0\n" in LAUNCHER
    arm = next(x for x in LAUNCHER.splitlines() if x.startswith("  strategy1_direct_v6_3_0)"))
    assert arm.endswith("; V6_31_BUILD=1; V6_32_BUILD=1; V6_33_BUILD=1; V6_34_BUILD=1"), arm
    assert LAUNCHER.count('if [[ "${V6_33_BUILD:-0}" == "1" ]]; then') == 1 and LAUNCHER.count("V6_33_BUILD=") == 2
    assert "      tests/test_research_v6_32.py \\\n      tests/test_research_v6_33.py \\\n" in LAUNCHER
    assert LAUNCHER.index('echo "[preflight] v6.32 fee-aware making + state fundamental PASS') < \
        LAUNCHER.index("# v6.33.") < LAUNCHER.index('echo "[preflight] v6.33 touch identity PASS') \
        < LAUNCHER.index('if [[ "${RESEARCH_PREFLIGHT_ONLY:-0}" == "1" ]]; then')
    head = LAUNCHER[LAUNCHER.index("# v6.33."):LAUNCHER.index('if [[ "${V6_33_BUILD:-0}" == "1" ]]; then')]
    for needle in ("STRUCTURAL", "orthox-profile-v1", "k79/spec/profile_v1.py", '"D4"] = (1, 0)', "OWN UID",
                   "research_v617_touch_quote=1", "1.32 effective agents of 8", "v6.32", "p = 1/2",
                   "ships 1 but may be 0", "=0 / =1", "PARAMS edit", "-8..-11%"):
        assert needle in head, needle
    assert LAUNCHER.count("# v6.33.") == 1
    assert LAUNCHER.index('echo "[preflight] v6.33 D4 touch identity: ${_v633_state}"') < \
        LAUNCHER.index('echo "[preflight] v6.33 touch identity PASS (D4 ${_v633_state}: ')


def _block():
    start = LAUNCHER.index('if [[ "${V6_33_BUILD:-0}" == "1" ]]; then')
    return LAUNCHER[start:LAUNCHER.index('echo "[preflight] v6.33 touch identity PASS', start)]


_PATTERN = t632._PATTERN
FILES = {"Strategy1_Research_Simple.py": "agents/strategy/Strategy1_Research_Simple.py",
         "research_v6_33_touch_identity.py": "agents/strategy/research_v6_33_touch_identity.py"}


def test_every_v633_preflight_pattern_is_in_the_file_it_checks():
    pats = re.findall(_PATTERN, _block())
    assert len(pats) == 14, pats
    for needle, var, name, _msg in pats:
        assert name in FILES and (ROOT / FILES[name]).read_text().count(needle) == 1, (needle, name)
        assert var == "AGENT_PATH", (needle, var)
    assert {p[0] for p in pats} >= {"from research_v6_33_touch_identity import (", SWITCH_LINE, REQUEST_LINE,
                                    TABLE_LINE, OFF_LINE, RULE_LINE, CANCEL_LINE, WITHHELD_LINE, REPORT_LINE,
                                    "from research_v630_identity_profile import PROFILE_VERSION, uid_of",
                                    "VALUES_D4 = (1, 0) ",
                                    'h = hashlib.sha256(f"{PROFILE_VERSION}|{D4}|{uid}|{book}".encode()).hexdigest()',
                                    "return VALUES_D4[int(h[:8], 16) % len(VALUES_D4)]",
                                    "return bool(switch) and bool(profile_on) and _integer(value) == TOUCH_OFF"}
    assert '[[ -f "$AGENT_PATH/research_v6_33_touch_identity.py" ]] || {' in _block()


def _run_block(params, agent_path=STRATEGY, build="1"):
    start = LAUNCHER.index("# v6.33.")
    stop = LAUNCHER.index('echo "[preflight] v6.33 touch identity PASS', start)
    script = "set -euo pipefail\n" + LAUNCHER[start:LAUNCHER.index("\nfi\n", stop) + len("\nfi\n")]
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "PARAMS": params, "AGENT_PATH": str(agent_path),
           "SCRIPT_DIR": str(ROOT), "V6_33_BUILD": build}
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True, env=env, timeout=120)


def test_the_v633_block_passes_the_shipped_params_and_refuses_a_build_it_cannot_ship():
    shipped = t617._shipped()
    out = _run_block(shipped)
    assert out.returncode == 0 and out.stderr == "", out.stderr
    lines = out.stdout.splitlines()
    assert lines[0] == "[preflight] v6.33 D4 touch identity: on" and len(lines) == 2, out.stdout
    assert lines[1].startswith("[preflight] v6.33 touch identity PASS (D4 on: "), out.stdout
    # D4's shipped state is the user's call: =0 ships it off (v6.32's touch quote on every book) by a PARAMS edit alone
    key = "research_v633_touch_identity"
    off = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {key}=0 "))
    assert off.returncode == 0 and off.stderr == "", off.stderr
    assert off.stdout.splitlines()[0] == "[preflight] v6.33 D4 touch identity: off"
    assert "PASS (D4 off: " in off.stdout
    for bad in (f"{key}=2", "", f"{key}=true", f"{key}=11", f"{key}=01", f"x{key}=1", f"{key}=-1"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {bad} "))
        assert out.returncode == 1 and f"without {key}=0 or 1" in out.stderr and "PASS" not in out.stdout, bad
    for twice in (f"{key}=1 {key}=1", f"{key}=1 {key}=0", f"{key}=0 {key}=1"):
        out = _run_block(f" {shipped} ".replace(f" {key}=1 ", f" {twice} "))
        assert out.returncode == 1 and f"{key} given 2 times" in out.stderr and "PASS" not in out.stdout, twice
    for state in ("1", "0"):                                                            # the tie, on or off
        params = f" {shipped} ".replace(f" {key}=1 ", f" {key}={state} ")
        out = _run_block(params.replace(" research_v617_touch_quote=1 ", " research_v617_touch_quote=0 "))
        assert out.returncode == 1 and "v6.33 build without research_v617_touch_quote=1 in PARAMS" in out.stderr, state
    out = _run_block("", build="0")                                                     # another build: not run
    assert out.returncode == 0 and out.stdout == "" and out.stderr == ""


def test_the_v633_block_refuses_a_tree_without_each_line_it_checks(tmp_path):
    cases = re.findall(_PATTERN, _block())
    assert len(cases) == 14
    for i, (needle, _var, name, msg) in enumerate(cases):
        tmp = tmp_path / str(i)
        tmp.mkdir()
        for base in FILES:
            shutil.copy(STRATEGY / base, tmp / base)
        path = tmp / name
        text = path.read_text()
        assert text.count(needle) == 1, needle
        path.write_text(text.replace(needle, "pass  # removed", 1))
        out = _run_block(t617._shipped(), agent_path=tmp)
        assert out.returncode == 1 and msg in out.stderr and "PASS" not in out.stdout, (needle, out.stderr)
    bare = tmp_path / "no_module"
    bare.mkdir()
    shutil.copy(STRATEGY / "Strategy1_Research_Simple.py", bare / "Strategy1_Research_Simple.py")
    out = _run_block(t617._shipped(), agent_path=bare)
    assert out.returncode == 1 and "v6.33 the touch-identity module is missing" in out.stderr
