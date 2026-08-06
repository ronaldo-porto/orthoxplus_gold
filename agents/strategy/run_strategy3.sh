#!/usr/bin/env bash
# Compatibility wrapper — canonical launcher is repo-root ./run_strategy3.sh
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec "$ROOT/run_strategy3.sh" "$@"
