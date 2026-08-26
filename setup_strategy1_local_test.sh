#!/usr/bin/env bash
set -Eeuo pipefail

# One-time Ubuntu setup for the SN79 Strategy1_Debug local test.
#
# Expected repository layout:
#   agents/strategy/Strategy1.py
#   agents/strategy/Strategy1_Debug.py
#   agents/proxy/proxy.py
#   simulate/trading/...
#
# Usage:
#   bash setup_strategy1_local_test.sh
#   bash setup_strategy1_local_test.sh --full
#
# --full installs the repository's optional [gentrx] dependency group too.

FULL=0
[[ "${1:-}" == "--full" ]] && FULL=1

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "[setup] $*"; }

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="${SN79_ROOT:-$SCRIPT_DIR}"

[[ -f "$REPO_ROOT/agents/strategy/Strategy1.py" ]] || \
  die "Run this script from the SN79 repo root, or set SN79_ROOT. Missing agents/strategy/Strategy1.py"
[[ -f "$REPO_ROOT/agents/strategy/Strategy1_Debug.py" ]] || \
  die "Missing agents/strategy/Strategy1_Debug.py"
[[ -f "$REPO_ROOT/agents/proxy/proxy.py" ]] || \
  die "Missing agents/proxy/proxy.py"

if (( EUID == 0 )); then
  APT=(apt-get)
else
  command -v sudo >/dev/null 2>&1 || die "sudo is required when not running as root"
  APT=(sudo apt-get)
fi

info "Installing Ubuntu runtime tools..."
"${APT[@]}" update
DEBIAN_FRONTEND=noninteractive "${APT[@]}" install -y \
  tmux jq python3 python3-pip python3-venv build-essential

cd -- "$REPO_ROOT"

if [[ ! -x "$REPO_ROOT/.venv/bin/python" ]]; then
  info "Creating .venv..."
  python3 -m venv "$REPO_ROOT/.venv"
fi

PYTHON="$REPO_ROOT/.venv/bin/python"
PIP="$REPO_ROOT/.venv/bin/pip"

info "Upgrading pip tooling..."
"$PYTHON" -m pip install --upgrade pip setuptools wheel

info "Installing SN79 editable package..."
"$PYTHON" -m pip install -e "$REPO_ROOT"

# proxy.py imports the production Validator class; the current validator import
# path requires these web/runtime packages even in the local proxy harness.
info "Installing local proxy runtime dependencies..."
"$PYTHON" -m pip install \
  "httpx>=0.27" \
  "fastapi>=0.110" \
  "uvicorn>=0.29"

if (( FULL == 1 )); then
  info "Installing full optional gentrx dependency group..."
  "$PYTHON" -m pip install -e "$REPO_ROOT[gentrx]"
fi

info "Validating basic imports..."
"$PYTHON" - <<'PY'
mods = [
    "bittensor",
    "taos",
    "aiohttp",
    "httpx",
    "fastapi",
    "uvicorn",
    "posix_ipc",
    "msgpack",
    "msgspec",
]
for name in mods:
    __import__(name)
    print(f"  OK {name}")
PY

info "Validating Strategy1_Debug syntax/import..."
PYTHONPATH="$REPO_ROOT:$REPO_ROOT/agents:$REPO_ROOT/agents/strategy" \
"$PYTHON" -m py_compile \
  "$REPO_ROOT/agents/strategy/Strategy1.py" \
  "$REPO_ROOT/agents/strategy/Strategy1_Debug.py"

(
  cd -- "$REPO_ROOT/agents/strategy"
  PYTHONPATH="$REPO_ROOT:$REPO_ROOT/agents:$REPO_ROOT/agents/strategy" \
    "$PYTHON" -c \
    'from Strategy1_Debug import Strategy1_Debug; print("  OK Strategy1_Debug")'
)

info "Validating local Proxy import..."
set +e
(
  cd -- "$REPO_ROOT"
  PYTHONPATH="$REPO_ROOT:$REPO_ROOT/agents:$REPO_ROOT/agents/strategy" \
    "$PYTHON" -c \
    'from agents.proxy.proxy import Proxy; print("  OK Proxy")'
)
proxy_rc=$?
set -e

if (( proxy_rc != 0 )); then
  echo
  echo "Proxy import is still missing an optional dependency."
  echo "Run:"
  echo "  bash setup_strategy1_local_test.sh --full"
  echo
  exit "$proxy_rc"
fi

TAOSIM="$REPO_ROOT/simulate/trading/build/src/cpp/taosim"
if [[ -f "$TAOSIM" ]]; then
  if [[ ! -x "$TAOSIM" ]]; then
    chmod +x "$TAOSIM"
  fi
  info "Found taosim: $TAOSIM"
else
  echo
  echo "WARNING: taosim was not found at:"
  echo "  $TAOSIM"
  echo "Build/install the simulator before running the local test."
fi

echo
echo "=============================================="
echo " SN79 Strategy1 local environment is ready"
echo "=============================================="
echo "Python: $PYTHON"
echo
echo "Next:"
echo "  bash run_strategy1_debug_tmux.sh --check"
echo "  bash run_strategy1_debug_tmux.sh --reset"
