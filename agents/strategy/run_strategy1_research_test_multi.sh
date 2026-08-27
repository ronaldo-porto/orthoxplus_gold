#!/usr/bin/env bash
# Canonical launcher lives at repository root. Keep this compatibility wrapper
# so there is only one authoritative V4.13 Simplified Kappa Productivity parameter definition.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$REPO_ROOT/run_strategy1_research_test_multi.sh" "$@"
