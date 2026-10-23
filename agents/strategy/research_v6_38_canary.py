# SPDX-License-Identifier: MIT
"""v6.38 layer 3, piece 0: the per-(UID, book) canary draw of tonight's pre-registered candidates -- research_v638_lean /
research_v638_c1 (modes) with research_v638_lean_arm / research_v638_c1_arm (which candidate).

Why (scratchpad k124 SPEC item 0; USER 10-21 06:15 JST "one merged build"): a candidate that PASSES its pre-registered
test is first deployed ONLY as a per-(UID, book) option on ONE UID -- half of that UID's books run the candidate, the other
half run v6.36's behaviour -- inside the build the user commits.  The two pre-registered draws:

* k116 (scratchpad k116/PREREG_2.md A3, sha256 30fd7097...): over the UID's CLASS-0 books,
      value = SHA-256('orthox-profile-k116-v1|' + dim + '|' + uid + '|' + book) mod 2,   dim = CA | CB | CC | CD
  (on = 1: the candidate; off = 0: v6.36).
* k118 (scratchpad k118/PREREG.md section 8, sha256 e7cef6ce...): over the UID's 32 CLASS-1 books (96..127),
      on = int(SHA-256('orthox-profile-k118-v1|c1|' + uid + '|' + book).hexdigest()[:8], 16) mod 2 == 1.

Both are the agent's D1 / D2 / D4 profile convention (research_v630_identity_profile.draw, research_v633_touch_identity:
the first 8 hex digits of the SHA-256 as an integer, mod k; uid and book as decimal text; scratchpad k113 point 4 "as
D1 / D2 / D4").  k116's text writes the short form "SHA-256(...) mod 2"; it is read with that same convention (the
k118 text spells it out) -- a reading flagged for the coordinator, not a choice made here.

The mode of each family (one PARAMS key each, both ship 0):
    0  off: v6.36 on every book (the candidate's code does not run)
    1  the hash half: the candidate on the books whose draw is 1, v6.36 on the others
    2  all books of the family's class
The UID is the one D4 uses (v6.30's Identity: this agent's OWN UID, fixed once per process, its profile on this request);
with the profile off (UID unknown, negative or changed) mode 1 puts no book on (fail closed: v6.36), mode 2 needs no UID.

Independence (STRUCTURAL -- v6.30's binding rules): the only inputs are this agent's own UID, the book id, the frozen tag
and the pre-registered dimension name; no list of our UIDs, no reading of another agent, no runtime RNG.

The table (CanaryTable) is drawn once per book for the UID it was first drawn for, never again; its digest is logged
(V6_38_L3_STATE): sha256 of "\\n".join([tag + "|uid=<uid>|" + dim] + ["<book>|<dim>=<v>" for each book ascending]).

STRUCTURAL: the tags, the dimension names, k = 2 and the hash are the pre-registrations' own; nothing here is sized from
an observed metric.
"""
from __future__ import annotations

import hashlib
import math
import operator
from typing import Any, Iterable

V6_38_CANARY_VERSION = "canary_v6_38_l3"
TAG_K116 = "orthox-profile-k116-v1"     # PRE-REGISTERED (k116 PREREG_2 A3)
TAG_K118 = "orthox-profile-k118-v1"     # PRE-REGISTERED (k118 PREREG section 8)
DIMS_K116 = ("CA", "CB", "CC", "CD")    # the k116 candidates' pre-registered names (PREREG section 3)
DIM_C1 = "c1"                           # the k118 class-1 draw's dimension (PREREG section 8)
K = 2                                   # on / off: half of the books
MODE_OFF, MODE_HALF, MODE_ALL = 0, 1, 2
MODES = (MODE_OFF, MODE_HALF, MODE_ALL)
# research_v638_lean_arm / research_v638_c1_arm: which pre-registered candidate a family's mode runs (0: none)
LEAN_ARMS = {1: "CA", 2: "CB", 3: "CC", 4: "CD"}          # k116 PREREG section 3 (CD = KBL + CB, conditional on KBL)
C1_ARMS = {1: "K1", 2: "K3", 3: "K3B", 4: "K3Q"}          # k118 PREREG section 4
ARM_CA, ARM_CB, ARM_CC, ARM_CD = 1, 2, 3, 4
ARM_K1, ARM_K3, ARM_K3B, ARM_K3Q = 1, 2, 3, 4
FAMILY_LEAN, FAMILY_C1 = "lean", "c1"

__all__ = ["ARM_CA", "ARM_CB", "ARM_CC", "ARM_CD", "ARM_K1", "ARM_K3", "ARM_K3B", "ARM_K3Q", "C1_ARMS", "CanaryTable",
           "DIMS_K116", "DIM_C1", "FAMILY_C1", "FAMILY_LEAN", "K", "LEAN_ARMS", "MODES", "MODE_ALL", "MODE_HALF",
           "MODE_OFF", "TAG_K116", "TAG_K118", "V6_38_CANARY_VERSION", "arm_of", "book_on", "draw", "mode_of",
           "table_digest", "table_text"]


def _integer(value: Any) -> int | None:
    """An integer of an integer type (not a bool, not a float or a text), or None."""
    if isinstance(value, bool):
        return None
    try:
        return operator.index(value)
    except Exception:
        return None


def _book(raw: Any) -> int | None:
    """A book id: an integer, else its text as an integer; never negative (D4's reading)."""
    b = _integer(raw)
    if b is None:
        try:
            b = int(str(raw))
        except (TypeError, ValueError):
            return None
    return b if b >= 0 else None


def draw(tag: str, dim: str, uid: int, book: int) -> int:
    """The pre-registered draw: int(SHA-256(tag + '|' + dim + '|' + uid + '|' + book).hexdigest()[:8], 16) mod 2."""
    h = hashlib.sha256(f"{tag}|{dim}|{int(uid)}|{int(book)}".encode()).hexdigest()
    return int(h[:8], 16) % K


def mode_of(value: Any) -> int:
    """A family's PARAMS mode: 0, 1 or 2 (an integer, or its text / a whole float); anything else is 0 (off)."""
    if isinstance(value, bool):
        return MODE_OFF
    try:
        v = float(value)
    except (TypeError, ValueError):
        return MODE_OFF
    if not math.isfinite(v) or v != int(v):
        return MODE_OFF
    return int(v) if int(v) in MODES else MODE_OFF


def arm_of(value: Any, arms: dict) -> int:
    """A family's PARAMS arm: a key of ``arms`` (an integer, its text or a whole float); anything else is 0 (none)."""
    if isinstance(value, bool):
        return 0
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(v) or v != int(v):
        return 0
    return int(v) if int(v) in arms else 0


def book_on(mode: Any, value: Any) -> bool:
    """Does the candidate run on the book: mode 2 always, mode 1 when the book's drawn value is 1, else (mode 0, an
    unreadable mode, mode 1 without a draw) False -- v6.36."""
    m = mode_of(mode)
    if m == MODE_ALL:
        return True
    return m == MODE_HALF and _integer(value) == 1


def table_text(tag: str, dim: str, uid: int, table: dict) -> str:
    """The canonical text the logged digest is taken over (one line per book, ascending)."""
    lines = [f"{tag}|uid={uid}|{dim}"]
    lines += [f"{book}|{dim}={value}" for book, value in sorted(table.items())]
    return "\n".join(lines)


def table_digest(tag: str, dim: str, uid: int, table: dict) -> str:
    return hashlib.sha256(table_text(tag, dim, uid, table).encode()).hexdigest()


class CanaryTable:
    """One family's draws for one dimension: the UID it was first drawn for, each book's value drawn once (never again),
    and the digest.  Pure: never raises, reads nothing but what it is handed."""

    def __init__(self, tag: str, dim: str) -> None:
        self.tag, self.dim = str(tag), str(dim)
        self.uid: int | None = None
        self.table: dict[int, int] = {}
        self.digest: str | None = None
        self.refused = 0                      # draws refused: no UID, or another UID than the table's own

    def value(self, uid: Any, book: Any) -> int | None:
        """The book's value for ``uid`` -- drawn now if the table has no draw for it yet -- or None (no UID, another UID
        than the one the table was first drawn for, an unreadable book)."""
        try:
            u = _integer(uid)
            b = _book(book)
            if u is None or u < 0 or b is None or (self.uid is not None and u != self.uid):
                self.refused += 1
                return None
            if self.uid is None:
                self.uid = u
            v = self.table.get(b)
            if v is None:
                v = draw(self.tag, self.dim, u, b)
                self.table[b] = v
                self.digest = table_digest(self.tag, self.dim, u, self.table)
            return v
        except Exception:
            self.refused += 1
            return None

    def extend(self, uid: Any, books: Iterable[Any]) -> int:
        """Draws every book of ``books`` not yet in the table; returns how many were drawn."""
        n = 0
        for raw in books or ():
            b = _book(raw)
            if b is not None and b not in self.table and self.value(uid, b) is not None:
                n += 1
        return n

    def snapshot(self) -> dict[str, Any]:
        books = sorted(self.table)
        return {"tag": self.tag, "dim": self.dim, "table_uid": self.uid, "books": len(books),
                "on_books": sum(1 for b in books if self.table[b] == 1), "table_sha256": self.digest,
                "draws": "".join(str(self.table[b]) for b in books), "refused": self.refused}
