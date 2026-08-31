# SPDX-License-Identifier: MIT
"""Regenerate the V4.14.5 checksum list and release manifest from the tree.

Hand-editing the two integrity artifacts is how they drift, so they are always
rebuilt from disk. Run from the repository root after changing any tracked file.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SUMS = ROOT / "V4_14_5_RESEARCH_SHA256SUMS.txt"
MANIFEST = ROOT / "V4_14_5_FULL_RELEASE_MANIFEST.json"

TRACKED = [
    "agents/strategy/Strategy1_Research.py",
    "agents/strategy/research_execution_lanes.py",
    "agents/strategy/research_total_score_frontier.py",
    "agents/strategy/research_realnet_exit_authority.py",
    "agents/strategy/research_scheduler_retry.py",
    "agents/strategy/research_rt_phase_timing.py",
    "agents/strategy/research_capacity_saturation.py",
    "run_strategy1_research_test_multi.sh",
    "tests/test_research_v4_14_5_total_score_frontier.py",
    "tests/test_research_rt_phase_timing.py",
    "tests/test_research_capacity_saturation.py",
    "SN79_ST65_RESEARCH_V4_14_5_TOTAL_SCORE_FRONTIER_P1.md",
    "agents/strategy/AGENT_VERSION_MANIFEST.md",
    "_sidebar.md",
    "agents/strategy/BaseStrategy.py",
    "agents/strategy/AdaptiveAgent.py",
    "taos/im/validator/trade.py",
    "agents/strategy/__ver_st1_log__/Strategy1_Research_v4_14_5.py",
    "agents/strategy/__ver_st1_log__/research_execution_lanes_v4_14_5.py",
    "agents/strategy/__ver_st1_log__/research_total_score_frontier_v4_14_5.py",
    "agents/strategy/__ver_st1_log__/research_rt_phase_timing_v4_14_5.py",
    "agents/strategy/__ver_st1_log__/research_capacity_saturation_v4_14_5.py",
    "agents/strategy/__ver_st1_log__/run_strategy1_research_v4_14_5_test.sh",
]


def digest(path: Path) -> tuple[str, int]:
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def main() -> int:
    entries = {}
    missing = []
    for rel in TRACKED:
        path = ROOT / rel
        if not path.exists():
            missing.append(rel)
            continue
        sha, size = digest(path)
        entries[rel] = {"bytes": size, "sha256": sha}

    if missing:
        for rel in missing:
            print(f"MISSING {rel}")
        return 1

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    manifest["files"] = entries
    manifest["validator_trade_sha256"] = entries["taos/im/validator/trade.py"]["sha256"]
    MANIFEST.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # SUMS carries the manifest hash, so it is written last.
    SUMS.write_text(
        "".join(f"{e['sha256']}  {rel}\n" for rel, e in entries.items())
        + f"{digest(MANIFEST)[0]}  {MANIFEST.name}\n",
        encoding="utf-8",
    )

    print(f"resynced {len(entries)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
