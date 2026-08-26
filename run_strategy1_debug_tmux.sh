#!/usr/bin/env bash
set -Eeuo pipefail

# Final Ubuntu launcher for Strategy1_Debug.
#
# Exact local agent layout:
#   agents/strategy/Strategy1.py
#   agents/strategy/Strategy1_Debug.py
#
# Windows created:
#   0 proxy
#   1 agent
#   2 simulator
#   3 monitor     (JSONL + process health)
#   4 simtrace    (simulator + proxy + strategy events + status)
#
# Typical:
#   bash run_strategy1_debug_tmux.sh --check
#   bash run_strategy1_debug_tmux.sh --reset
#   bash run_strategy1_debug_tmux.sh --reset --book 3 --every 10
#
# Env:
#   EXTRA_AGENT_PARAMS="key=value key=value"

SESSION="${SESSION:-sn79_s1_debug}"
AGENT_ID="${AGENT_ID:-0}"
AGENT_PORT="${AGENT_PORT:-8888}"
PROXY_PORT="${PROXY_PORT:-8000}"
PROXY_TIMEOUT="${PROXY_TIMEOUT:-5}"
DEBUG_BOOK="${DEBUG_BOOK:--1}"
DEBUG_EVERY_N="${DEBUG_EVERY_N:-1}"
DEBUG_SUMMARY_N="${DEBUG_SUMMARY_N:-100}"
DEBUG_JSONL="${DEBUG_JSONL:-1}"
NO_ATTACH=0
RESET=0
CHECK_ONLY=0
ROOT_OVERRIDE="${SN79_ROOT:-}"
PYTHON_OVERRIDE="${PYTHON_BIN:-}"
SIM_XML_OVERRIDE="${SIM_XML:-}"
TAOSIM_OVERRIDE="${TAOSIM_BIN:-}"

die() { printf '\nERROR: %s\n\n' "$*" >&2; exit 1; }
info() { printf '[launcher] %s\n' "$*"; }

usage() {
cat <<'EOF'
Usage:
  bash run_strategy1_debug_tmux.sh [options]

Options:
  --root PATH          repository root
  --session NAME       tmux session name
  --agent-id N         local agent UID (default 0)
  --agent-port PORT    agent Uvicorn port (default 8888)
  --proxy-port PORT    proxy Uvicorn port (default 8000)
  --book ID            debug one book; -1 = all books
  --every N            detailed debug every N ticks
  --summary N          summary every N ticks
  --python PATH        Python interpreter
  --sim-xml PATH       simulation XML
  --taosim PATH        simulator binary
  --reset              replace existing tmux session
  --no-attach          launch in background
  --check              validate only
  -h, --help           help
EOF
}

while (( $# )); do
  case "$1" in
    --root)       ROOT_OVERRIDE="${2:?}"; shift 2 ;;
    --session)    SESSION="${2:?}"; shift 2 ;;
    --agent-id)   AGENT_ID="${2:?}"; shift 2 ;;
    --agent-port) AGENT_PORT="${2:?}"; shift 2 ;;
    --proxy-port) PROXY_PORT="${2:?}"; shift 2 ;;
    --book)       DEBUG_BOOK="${2:?}"; shift 2 ;;
    --every)      DEBUG_EVERY_N="${2:?}"; shift 2 ;;
    --summary)    DEBUG_SUMMARY_N="${2:?}"; shift 2 ;;
    --python)     PYTHON_OVERRIDE="${2:?}"; shift 2 ;;
    --sim-xml)    SIM_XML_OVERRIDE="${2:?}"; shift 2 ;;
    --taosim)     TAOSIM_OVERRIDE="${2:?}"; shift 2 ;;
    --reset)      RESET=1; shift ;;
    --no-attach)  NO_ATTACH=1; shift ;;
    --check)      CHECK_ONLY=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="${ROOT_OVERRIDE:-$SCRIPT_DIR}"
REPO_ROOT="$(cd -- "$REPO_ROOT" 2>/dev/null && pwd -P)" || die "Invalid repo root: $REPO_ROOT"

AGENTS_DIR="$REPO_ROOT/agents"
AGENT_DIR="$AGENTS_DIR/strategy"
AGENT_FILE="$AGENTS_DIR/strategy/Strategy1_Debug.py"
STRATEGY_FILE="$AGENTS_DIR/strategy/Strategy1.py"
PROXY_DIR="$AGENTS_DIR/proxy"
PROXY_FILE="$PROXY_DIR/proxy.py"

[[ -f "$STRATEGY_FILE" ]] || die "Missing $STRATEGY_FILE"
[[ -f "$AGENT_FILE" ]] || die "Missing $AGENT_FILE"
[[ -f "$PROXY_FILE" ]] || die "Missing $PROXY_FILE"

command -v tmux >/dev/null 2>&1 || die "tmux missing. Run setup_strategy1_local_test.sh"
command -v jq   >/dev/null 2>&1 || die "jq missing. Run setup_strategy1_local_test.sh"

# Deliberately prefer the repository .venv even when the caller forgot to activate it.
if [[ -n "$PYTHON_OVERRIDE" ]]; then
  PYTHON="$PYTHON_OVERRIDE"
elif [[ -x "$REPO_ROOT/.venv/bin/python" ]]; then
  PYTHON="$REPO_ROOT/.venv/bin/python"
elif [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
  PYTHON="$VIRTUAL_ENV/bin/python"
else
  PYTHON="$(command -v python3 || true)"
fi
[[ -n "$PYTHON" && -x "$PYTHON" ]] || die "Python interpreter not found"

if [[ -n "$SIM_XML_OVERRIDE" ]]; then
  [[ "$SIM_XML_OVERRIDE" = /* ]] && SIM_XML="$SIM_XML_OVERRIDE" || SIM_XML="$REPO_ROOT/$SIM_XML_OVERRIDE"
else
  SIM_XML="$REPO_ROOT/simulate/trading/run/config/simulation_0.xml"
fi
[[ -f "$SIM_XML" ]] || die "Missing simulation XML: $SIM_XML"
SIM_XML="$(cd -- "$(dirname -- "$SIM_XML")" && pwd -P)/$(basename -- "$SIM_XML")"

if [[ -n "$TAOSIM_OVERRIDE" ]]; then
  [[ "$TAOSIM_OVERRIDE" = /* ]] && TAOSIM="$TAOSIM_OVERRIDE" || TAOSIM="$REPO_ROOT/$TAOSIM_OVERRIDE"
else
  TAOSIM="$REPO_ROOT/simulate/trading/build/src/cpp/taosim"
fi
[[ -f "$TAOSIM" ]] || die "Missing taosim: $TAOSIM"
[[ -x "$TAOSIM" ]] || die "taosim is not executable: chmod +x '$TAOSIM'"
TAOSIM="$(cd -- "$(dirname -- "$TAOSIM")" && pwd -P)/$(basename -- "$TAOSIM")"

[[ "$AGENT_PORT" =~ ^[0-9]+$ ]] || die "Invalid agent port"
[[ "$PROXY_PORT" =~ ^[0-9]+$ ]] || die "Invalid proxy port"
(( AGENT_PORT > 0 && AGENT_PORT < 65536 )) || die "Invalid agent port"
(( PROXY_PORT > 0 && PROXY_PORT < 65536 )) || die "Invalid proxy port"
[[ "$AGENT_PORT" != "$PROXY_PORT" ]] || die "Agent/proxy ports must differ"
[[ "$AGENT_ID" =~ ^[0-9]+$ ]] || die "Invalid agent id"
[[ "$DEBUG_BOOK" =~ ^-?[0-9]+$ ]] || die "Invalid book id"
[[ "$DEBUG_EVERY_N" =~ ^[0-9]+$ ]] && (( DEBUG_EVERY_N >= 1 )) || die "--every must be >= 1"
[[ "$DEBUG_SUMMARY_N" =~ ^[0-9]+$ ]] && (( DEBUG_SUMMARY_N >= 1 )) || die "--summary must be >= 1"

export PYTHONPATH="$REPO_ROOT:$AGENTS_DIR:$AGENT_DIR:${PYTHONPATH:-}"

info "Python: $PYTHON"
"$PYTHON" -m py_compile "$STRATEGY_FILE" "$AGENT_FILE"

info "Checking required Python modules..."
"$PYTHON" - <<'PY' || {
import bittensor, taos, aiohttp, httpx, fastapi, uvicorn, posix_ipc, msgpack, msgspec
print("basic imports OK")
PY
  echo
  echo "Python dependency validation failed."
  echo "Run: bash setup_strategy1_local_test.sh"
  exit 1
}

info "Checking Strategy1_Debug import..."
(
  cd -- "$AGENT_DIR"
  "$PYTHON" -c 'from Strategy1_Debug import Strategy1_Debug; print("Strategy1_Debug import OK")'
)

info "Checking Proxy import..."
if ! (
  cd -- "$REPO_ROOT"
  "$PYTHON" -c 'from agents.proxy.proxy import Proxy; print("Proxy import OK")'
); then
  echo
  echo "Proxy import failed. Do NOT start the simulator yet."
  echo "Run: bash setup_strategy1_local_test.sh"
  echo "If an optional dependency is still missing:"
  echo "     bash setup_strategy1_local_test.sh --full"
  exit 1
fi

cat <<EOF

Environment validated
---------------------
Repo:        $REPO_ROOT
Python:      $PYTHON
Agent:       $AGENT_FILE
Proxy:       $PROXY_FILE
taosim:      $TAOSIM
XML:         $SIM_XML
Session:     $SESSION
Proxy port:  $PROXY_PORT
Agent port:  $AGENT_PORT
Debug book:  $DEBUG_BOOK

EOF

(( CHECK_ONLY == 0 )) || exit 0

if tmux has-session -t "$SESSION" 2>/dev/null; then
  if (( RESET == 1 )); then
    info "Stopping existing session $SESSION"
    tmux kill-session -t "$SESSION"
    sleep 1
  else
    die "Session exists: $SESSION. Use --reset or: tmux attach -t $SESSION"
  fi
fi

# Catch unrelated stale listeners before opening a new run.
if (exec 3<>"/dev/tcp/127.0.0.1/$PROXY_PORT") >/dev/null 2>&1; then
  die "Port $PROXY_PORT already in use"
fi
if (exec 3<>"/dev/tcp/127.0.0.1/$AGENT_PORT") >/dev/null 2>&1; then
  die "Port $AGENT_PORT already in use"
fi

RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOG_BASE="$REPO_ROOT/logs/strategy1_debug"
LOG_DIR="$LOG_BASE/$RUN_ID"
DEBUG_DIR="$LOG_DIR/debug"
JSONL="$DEBUG_DIR/strategy1_debug_agent_${AGENT_ID}.jsonl"
RUNTIME_CONFIG="$LOG_DIR/proxy_config.runtime.json"
RUNTIME_XML="$LOG_DIR/simulation_0.runtime.xml"

mkdir -p "$DEBUG_DIR"
touch "$JSONL"

# latest -> current run
mkdir -p "$LOG_BASE"
ln -sfn "$RUN_ID" "$LOG_BASE/latest"

# Copy XML and force its Simulation port to the chosen proxy port.
"$PYTHON" - "$SIM_XML" "$RUNTIME_XML" "$PROXY_PORT" <<'PY'
from pathlib import Path
import re, sys

src, dst, port = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
text = src.read_text(encoding="utf-8")
updated, count = re.subn(
    r'(<Simulation\b[^>]*\bport\s*=\s*["\'])\d+(["\'])',
    rf'\g<1>{port}\2',
    text,
    count=1,
)
if not count:
    updated, count = re.subn(r'<Simulation\b', f'<Simulation port="{port}"', text, count=1)
if not count:
    raise SystemExit("Could not locate <Simulation> in XML")
dst.write_text(updated, encoding="utf-8")
PY

# Generate the exact local proxy config.
"$PYTHON" - "$RUNTIME_CONFIG" "$RUNTIME_XML" "$AGENT_DIR" \
  "$PROXY_PORT" "$AGENT_PORT" "$PROXY_TIMEOUT" "$DEBUG_BOOK" \
  "$DEBUG_EVERY_N" "$DEBUG_SUMMARY_N" "$DEBUG_JSONL" <<'PY'
from pathlib import Path
import json, sys

(out, xml, agent_dir, proxy_port, agent_port, timeout,
 book, every, summary, jsonl) = sys.argv[1:]

cfg = {
    "proxy": {
        "port": int(proxy_port),
        "simulation_xml": xml,
        "timeout": int(timeout),
    },
    "agents": {
        "start_port": int(agent_port),
        "path": agent_dir,
        "Strategy1_Debug": [{
            "params": {
                "enable_mm_strategy": True,
                "verbose_log": False,
                "log_every_n": 100,
                "debug_enabled": True,
                "debug_every_n": int(every),
                "debug_summary_every_n": int(summary),
                "debug_jsonl": bool(int(jsonl)),
                "debug_book_id": int(book),
            },
            "count": 1,
        }],
    },
}
Path(out).write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
PY

SIM_XML_DIR="$(dirname -- "$RUNTIME_XML")"
# taosim may resolve other files relative to simulate/trading/run, not log dir.
SIM_RUN_DIR="$(dirname -- "$(dirname -- "$SIM_XML")")"
[[ -d "$SIM_RUN_DIR" ]] || SIM_RUN_DIR="$REPO_ROOT/simulate/trading/run"

RUN_PROXY="$LOG_DIR/run_proxy.sh"
RUN_AGENT="$LOG_DIR/run_agent.sh"
RUN_SIM="$LOG_DIR/run_simulator.sh"
RUN_MONITOR="$LOG_DIR/run_monitor.sh"
RUN_HEALTH="$LOG_DIR/run_health.sh"

cat >"$RUN_PROXY" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
cd -- $(printf '%q' "$PROXY_DIR")
export PYTHONPATH=$(printf '%q' "$REPO_ROOT:$AGENTS_DIR:$AGENT_DIR"):\${PYTHONPATH:-}
export PYTHONUNBUFFERED=1
exec > >(tee -a $(printf '%q' "$LOG_DIR/proxy.log")) 2>&1
echo "[proxy] starting at \$(date --iso-8601=seconds)"
exec $(printf '%q' "$PYTHON") -u $(printf '%q' "$PROXY_FILE") --config $(printf '%q' "$RUNTIME_CONFIG")
EOF

cat >"$RUN_AGENT" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
cd -- $(printf '%q' "$AGENT_DIR")
export PYTHONPATH=$(printf '%q' "$REPO_ROOT:$AGENTS_DIR:$AGENT_DIR"):\${PYTHONPATH:-}
export PYTHONUNBUFFERED=1
export STRATEGY1_DEBUG=1
export STRATEGY1_DEBUG_BOOK=$(printf '%q' "$DEBUG_BOOK")
export STRATEGY1_DEBUG_EVERY_N=$(printf '%q' "$DEBUG_EVERY_N")
export STRATEGY1_DEBUG_SUMMARY_N=$(printf '%q' "$DEBUG_SUMMARY_N")
export STRATEGY1_DEBUG_JSONL=$(printf '%q' "$DEBUG_JSONL")
export STRATEGY1_DEBUG_DIR=$(printf '%q' "$DEBUG_DIR")
exec > >(tee -a $(printf '%q' "$LOG_DIR/agent.log")) 2>&1
params=(
  enable_mm_strategy=1
  verbose_log=0
  log_every_n=100
  debug_enabled=1
  debug_every_n=$(printf '%q' "$DEBUG_EVERY_N")
  debug_summary_every_n=$(printf '%q' "$DEBUG_SUMMARY_N")
  debug_jsonl=$(printf '%q' "$DEBUG_JSONL")
  debug_book_id=$(printf '%q' "$DEBUG_BOOK")
)
if [[ -n "\${EXTRA_AGENT_PARAMS:-}" ]]; then
  read -r -a extra <<< "\$EXTRA_AGENT_PARAMS"
  params+=("\${extra[@]}")
fi
echo "[agent] starting at \$(date --iso-8601=seconds)"
exec $(printf '%q' "$PYTHON") -u $(printf '%q' "$AGENT_FILE") \
  --port $(printf '%q' "$AGENT_PORT") \
  --agent_id $(printf '%q' "$AGENT_ID") \
  --params "\${params[@]}"
EOF

cat >"$RUN_SIM" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
cd -- $(printf '%q' "$SIM_RUN_DIR")
exec > >(tee -a $(printf '%q' "$LOG_DIR/simulator.log")) 2>&1

wait_port() {
  local name="\$1" port="\$2" timeout="\$3" n=0
  while (( n < timeout )); do
    if (exec 3<>"/dev/tcp/127.0.0.1/\$port") >/dev/null 2>&1; then
      echo "[simulator] \$name port \$port ready"
      return 0
    fi
    sleep 1
    ((n+=1))
  done
  echo "ERROR: \$name port \$port did not become ready within \${timeout}s" >&2
  return 1
}

echo "[simulator] waiting for proxy and agent"
if ! wait_port proxy $(printf '%q' "$PROXY_PORT") 60; then
  echo
  echo "---- proxy.log tail ----"
  tail -80 $(printf '%q' "$LOG_DIR/proxy.log") 2>/dev/null || true
  exit 1
fi
if ! wait_port agent $(printf '%q' "$AGENT_PORT") 60; then
  echo
  echo "---- agent.log tail ----"
  tail -80 $(printf '%q' "$LOG_DIR/agent.log") 2>/dev/null || true
  exit 1
fi

echo "[simulator] starting at \$(date --iso-8601=seconds)"
exec $(printf '%q' "$TAOSIM") -f $(printf '%q' "$RUNTIME_XML")
EOF

cat >"$RUN_MONITOR" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
touch $(printf '%q' "$JSONL")
tail -n 100 -F $(printf '%q' "$JSONL") | jq -C .
EOF

cat >"$RUN_HEALTH" <<EOF
#!/usr/bin/env bash
set -Eeuo pipefail
while true; do
  clear
  echo "SN79 Strategy1 local health - \$(date)"
  echo
  echo "Processes"
  ps -eo pid,etime,%cpu,%mem,cmd | grep -E 'proxy\\.py|Strategy1_Debug\\.py|taosim' | grep -v grep || true
  echo
  echo "Ports"
  ss -ltnp 2>/dev/null | grep -E ':$(printf '%q' "$PROXY_PORT")|:$(printf '%q' "$AGENT_PORT")' || true
  echo
  echo "Run: $(printf '%q' "$LOG_DIR")"
  sleep 2
done
EOF

chmod 755 "$RUN_PROXY" "$RUN_AGENT" "$RUN_SIM" "$RUN_MONITOR" "$RUN_HEALTH"

cat >"$LOG_DIR/run_info.txt" <<EOF
SESSION=$SESSION
RUN_ID=$RUN_ID
REPO_ROOT=$REPO_ROOT
PYTHON=$PYTHON
AGENT_FILE=$AGENT_FILE
PROXY_FILE=$PROXY_FILE
TAOSIM=$TAOSIM
SIM_XML=$SIM_XML
RUNTIME_XML=$RUNTIME_XML
PROXY_PORT=$PROXY_PORT
AGENT_PORT=$AGENT_PORT
DEBUG_BOOK=$DEBUG_BOOK
DEBUG_EVERY_N=$DEBUG_EVERY_N
DEBUG_SUMMARY_N=$DEBUG_SUMMARY_N
EOF

# Window 0: proxy
tmux new-session -d -s "$SESSION" -n proxy -c "$REPO_ROOT" \
  "bash $(printf '%q' "$RUN_PROXY")"

# Window 1: agent
tmux new-window -d -t "$SESSION:1" -n agent -c "$AGENT_DIR" \
  "bash $(printf '%q' "$RUN_AGENT")"

# Window 2: simulator
tmux new-window -d -t "$SESSION:2" -n simulator -c "$SIM_RUN_DIR" \
  "bash $(printf '%q' "$RUN_SIM")"

# Window 3: monitor (JSONL top, health bottom)
tmux new-window -d -t "$SESSION:3" -n monitor -c "$REPO_ROOT" \
  "bash $(printf '%q' "$RUN_MONITOR")"
tmux split-window -v -t "$SESSION:3.0" -c "$REPO_ROOT" \
  "bash $(printf '%q' "$RUN_HEALTH")"
tmux select-layout -t "$SESSION:3" even-vertical

# Window 4: simtrace. Direct commands avoid fragile send-keys/window-name timing.
tmux new-window -d -t "$SESSION:4" -n simtrace -c "$REPO_ROOT" \
  "tail -n 100 -F $(printf '%q' "$LOG_DIR/simulator.log")"

tmux split-window -h -t "$SESSION:4.0" -c "$REPO_ROOT" \
  "tail -n 100 -F $(printf '%q' "$LOG_DIR/proxy.log") | grep --line-buffered -E 'Received state|Querying|Response|State update handled|NOTICE|Timed out|Failed|ERROR|WARNING|Uvicorn'"

tmux split-window -v -t "$SESSION:4.1" -c "$REPO_ROOT" \
  "tail -n 100 -F $(printf '%q' "$JSONL") | jq -C 'select(.type == \"DECISION\" or .type == \"ORDER_SUBMIT\" or .type == \"NOTICE\" or .type == \"TIMING\" or .type == \"SUMMARY\")'"

tmux split-window -v -t "$SESSION:4.0" -c "$REPO_ROOT" \
  "bash $(printf '%q' "$RUN_HEALTH")"

tmux select-layout -t "$SESSION:4" tiled
tmux select-window -t "$SESSION:1"

cat <<EOF

========================================================
 SN79 Strategy1_Debug local test started
========================================================
Session:  $SESSION
Run:      $LOG_DIR

Windows:
  0  proxy
  1  agent
  2  simulator
  3  monitor
  4  simtrace

Navigation:
  Ctrl+B, 0    proxy
  Ctrl+B, 1    agent
  Ctrl+B, 2    simulator
  Ctrl+B, 3    JSONL + health
  Ctrl+B, 4    full action trace

Detach:
  Ctrl+B, d

Reattach:
  tmux attach -t $SESSION

Logs:
  $LOG_BASE/latest/proxy.log
  $LOG_BASE/latest/agent.log
  $LOG_BASE/latest/simulator.log
  $LOG_BASE/latest/debug/strategy1_debug_agent_${AGENT_ID}.jsonl

EOF

if (( NO_ATTACH == 0 )); then
  exec tmux attach -t "$SESSION"
fi
