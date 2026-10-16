# SPDX-License-Identifier: MIT
"""v6.33 D4: the touch dimension of the identity profile -- research_v633_touch_identity.

Why (mainnet 10-16 ~21:00 JST, all eight of our UIDs on v6.32; scratchpad k95; the user's directive 10-16 ~21:10 JST: the
UIDs must act as different agents AT THE TOUCH too):

* At the touch our UIDs are near-clones: 1.32 effective agents of 8; a UID has at least one sibling on the same
  book-side for 94-97% of its touch time, all eight together 44% of it; pairwise co-presence 0.68-0.75.  The v6.17 touch
  quote is visible on the tape with the SAME client id per book (43000 + 10 x book + 1 | 2) for every UID.
* v6.30's D1 (the deep tier) and D2 (S9's look-back) never reach the touch: the toward-fp quote joins (S12b: improves)
  the same price on the same book.  D4 is the only pre-registered dimension that changes co-location at the touch
  (k75 decor_final D4).

The draw (PRE-REGISTERED 10-14 06:08 JST before any replay output: scratchpad k79/spec/profile_v1.py, sha256
31d08ace5b99b5e3ac33e567a06ad08ef914d0edae275e7168e2246ee264168c; frozen -- any change is a new version string):

    value = VALUES["D4"][ int(SHA-256('orthox-profile-v1|D4|' + str(uid) + '|' + str(book)).hexdigest()[:8], 16)
                          mod 2 ],   VALUES["D4"] = (1, 0)

    1  the toward-fp touch quote is placed on the book (index 0: v6.32's behaviour)
    0  this agent keeps no touch quote on the book

It is research_v630_identity_profile.draw's formula (that function knows D1 and D2 only): the same version string,
imported from it (PROFILE_VERSION), the same hash and prefix width, here with the dimension "D4".

Independence (STRUCTURAL -- v6.30's binding rules): the only inputs are this agent's OWN UID (v6.30's Identity, fixed
once per process), the book id, the dimension name and the frozen version string.  There is no list, count or index of
our UIDs, no reading of another agent's orders, fills, client ids or files, no host cache, lock, socket or IPC and no
runtime RNG; this module imports only the standard library's hashlib, operator and typing and v6.30's profile module.
Two UIDs share a book's value with probability 1/2, and nothing aligns them.

The rule (research_v633_touch_identity): a book is OFF for this process when the switch is on, v6.30's profile is on this
request and the book's D4 draw for this UID is 0 (touch_off); otherwise it is ON -- v6.32 exactly.  On an OFF book the
agent keeps no v6.17 touch quote at all, adding or reducing (the pre-registered meaning: "not on this book"): a resting
one is cancelled (CANCEL_TOUCH_IDENTITY -- one inherited from an earlier process, or placed before the profile turned
on) and none is placed (the last gate before the slot and the price, so its tally counts exactly the quotes v6.32 would
have armed).  The deep layer (D1 included), the lean, pace, caps, S9, S12b, S14 and every ON book are unchanged.  The
profile off -- the UID unknown, negative or changed -- keeps every book ON (v6.32), and the state row says why.

The table (TouchTable -- this module's own; v6.30's Identity and its (D1, D2) rows are untouched) is drawn once per book
for the UID it was first drawn for: at the first request for every book of the state (the profile's own table), a book
first seen later when it first appears.  No value is ever drawn again or changed, and another UID never extends it.  Its
digest is logged (V6_33_STATE): sha256 of "\\n".join(["orthox-profile-v1|uid=<uid>|D4"] + ["<book>|D4=<v>" for each book
ascending]) -- reproducible offline from the spec alone (table_text / table_digest below).

STRUCTURAL: independence; the value set, its order and k = 2 are the pre-registered spec's (its design choice: p = 1/2,
the minimal split).  No constant here is sized from an observed metric.
"""
from __future__ import annotations

import hashlib
import operator
from typing import Any, Iterable

from research_v630_identity_profile import PROFILE_VERSION, uid_of

V6_33_TOUCH_IDENTITY_VERSION = "touch_identity_v6_33"
D4 = "D4"                             # the pre-registered dimension (k79/spec/profile_v1.py VALUES["D4"])
VALUES_D4 = (1, 0)                    # the pre-registered value set and its order; index 0 (1) is v6.32's behaviour
TOUCH_ON, TOUCH_OFF = 1, 0            # 1: the touch quote on the book; 0: this agent keeps none there
CANCEL_TOUCH_IDENTITY = "TOUCH_IDENTITY"   # a resting touch quote on a book this agent's draw keeps off the touch

__all__ = ["CANCEL_TOUCH_IDENTITY", "D4", "TOUCH_OFF", "TOUCH_ON", "TouchTable", "V6_33_TOUCH_IDENTITY_VERSION",
           "VALUES_D4", "draw", "table_digest", "table_text", "touch_off"]


def _integer(value: Any) -> int | None:
    """An integer of an integer type (not a bool, not a float or a text), or None."""
    if isinstance(value, bool):
        return None
    try:
        return operator.index(value)
    except Exception:
        return None


def _book(raw: Any) -> int | None:
    """A book id as v6.30's Identity.extend reads one: an integer, else its text as an integer; never negative."""
    b = _integer(raw)
    if b is None:
        try:
            b = int(str(raw))
        except (TypeError, ValueError):
            return None
    return b if b >= 0 else None


def draw(uid: int, book: int) -> int:
    """The pre-registered draw of D4: VALUES_D4[int(SHA-256('orthox-profile-v1|D4|uid|book')[:8], 16) mod 2]."""
    h = hashlib.sha256(f"{PROFILE_VERSION}|{D4}|{uid}|{book}".encode()).hexdigest()
    return VALUES_D4[int(h[:8], 16) % len(VALUES_D4)]


def table_text(uid: int, table: dict) -> str:
    """The canonical text the logged digest is taken over (one line per book, ascending)."""
    lines = [f"{PROFILE_VERSION}|uid={uid}|{D4}"]
    lines += [f"{book}|{D4}={value}" for book, value in sorted(table.items())]
    return "\n".join(lines)


def table_digest(uid: int, table: dict) -> str:
    return hashlib.sha256(table_text(uid, table).encode()).hexdigest()


def touch_off(switch: Any, profile_on: Any, value: Any) -> bool:
    """True -- this agent keeps no touch quote on the book -- iff the switch is on, v6.30's profile is on this request and
    the book's drawn D4 value is TOUCH_OFF.  Anything else (the switch off, the profile off, a draw of 1, no draw) is
    False: v6.32."""
    return bool(switch) and bool(profile_on) and _integer(value) == TOUCH_OFF


class TouchTable:
    """This agent's D4 table: the UID it was first drawn for, each book's value drawn once, and the table's digest.  Pure:
    it never raises and reads nothing but the values it is handed."""

    def __init__(self) -> None:
        self.uid: int | None = None
        self.table: dict[int, int] = {}
        self.digest: str | None = None
        self.extended = 0                     # books drawn after the first draw
        self.refused = 0                      # draws refused: no UID, or another UID than the table's own

    def extend(self, uid: Any, book_ids: Iterable[Any]) -> int:
        """Draws every book of ``book_ids`` not yet in the table -- once each, never again -- for ``uid``, which must be
        the UID the table was first drawn for (the first call fixes it).  No UID, or another one: nothing is drawn
        (counted).  Returns how many books were drawn."""
        try:
            u = uid_of(uid)
            if u is None or (self.uid is not None and u != self.uid):
                self.refused += 1
                return 0
            if self.uid is None:
                self.uid = u
            first = not self.table
            added = 0
            for raw in book_ids or ():
                b = _book(raw)
                if b is None or b in self.table:
                    continue
                self.table[b] = draw(u, b)
                added += 1
            if added:
                if not first:
                    self.extended += added
                self.digest = table_digest(u, self.table)
            return added
        except Exception:
            self.refused += 1
            return 0

    def value(self, book: Any) -> int | None:
        """The book's drawn D4 value, or None (a book without a draw)."""
        b = _integer(book)
        return self.table.get(b) if b is not None else None

    def snapshot(self) -> dict[str, Any]:
        books = sorted(self.table)
        return {"table_uid": self.uid, "books": len(books),
                "touch_on_books": sum(1 for b in books if self.table[b] == TOUCH_ON),
                "touch_off_books": sum(1 for b in books if self.table[b] == TOUCH_OFF),
                "table_sha256": self.digest, "d4": "".join(str(self.table[b]) for b in books),
                "books_drawn_later": self.extended, "refused": self.refused}
