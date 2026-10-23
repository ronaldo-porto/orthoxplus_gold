"""v6.39 S4 (k129): Strategy1_Research_Simple's text as the interpreter binds it.

The agent class is split into part modules (agents/strategy/simple_parts/*.py: one mixin class each, the methods moved
VERBATIM); ``joined_text(agent_dir)`` puts every moved chunk back at the line it was cut from and reverses the two
edits the split made to the file (the part imports and the class bases), so the result is BYTE-IDENTICAL to the single
file of the split commit (MANIFEST.json records its sha256; tests/test_research_v6_39.py pins it).  The test suite
(tests/_harness.simple_text) and the launcher's preflight read this text: every text pin, statement-order pin,
class-body-order pin, AST digest and git strip sees exactly what it saw before the split.  STRUCTURAL.

Run as a script it prints the joined text: ``python3 join.py <agent_dir>``.  Without a MANIFEST the file is printed as
it is (a tree that was never split, or a test's bare copy)."""
from __future__ import annotations

import hashlib
import json
import os
import sys

SIMPLE = "Strategy1_Research_Simple.py"
PARTS_DIR = "simple_parts"
MANIFEST = "MANIFEST.json"


def manifest_of(agent_dir: str) -> dict | None:
    path = os.path.join(agent_dir, PARTS_DIR, MANIFEST)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def joined_text(agent_dir: str) -> str:
    """The pre-split text of Simple: the file with its parts' chunks back at their lines and the split's two edits undone."""
    with open(os.path.join(agent_dir, SIMPLE), encoding="utf-8") as handle:
        text = handle.read()
    manifest = manifest_of(agent_dir)
    if manifest is None:
        return text
    for new, old in manifest["simple_edits"]:                                  # the split's edits, newest first
        if text.count(new) != 1:
            raise AssertionError("split edit not found exactly once: %r" % new[:80])
        text = text.replace(new, old)
    cut_lines = text.splitlines(keepends=True)
    chunks = []
    for part in manifest["parts"]:
        with open(os.path.join(agent_dir, PARTS_DIR, part["file"]), encoding="utf-8") as handle:
            part_lines = handle.read().splitlines(keepends=True)
        for chunk in part["chunks"]:
            lines = part_lines[chunk["part_start"] - 1:chunk["part_end"]]
            chunks.append((chunk["orig_start"], chunk["orig_end"], lines))
    chunks.sort()
    out = []
    it = iter(cut_lines)
    line_no = 1
    for orig_start, orig_end, lines in chunks:
        while line_no < orig_start:
            out.append(next(it))
            line_no += 1
        if len(lines) != orig_end - orig_start + 1:
            raise AssertionError("chunk length mismatch at %d" % orig_start)
        out.extend(lines)
        line_no = orig_end + 1
    out.extend(it)
    return "".join(out)


def joined_sha256(agent_dir: str) -> str:
    return hashlib.sha256(joined_text(agent_dir).encode("utf-8")).hexdigest()


if __name__ == "__main__":
    sys.stdout.write(joined_text(sys.argv[1] if len(sys.argv) > 1 else os.path.dirname(os.path.abspath(__file__)) + "/.."))
