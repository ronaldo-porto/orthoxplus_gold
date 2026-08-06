#!/usr/bin/env bash
set -Eeuo pipefail

# SN79 Strategy1_Debug local test launcher.
# Place this script in the sn-79 repository root, or set SN79_ROOT explicitly.

SESSION="${SESSION:-sn79_s1_debug}"
AGENT_ID="${AGENT_ID:-0}"
AGENT_PORT="${AGENT_PORT:-8888}"
PROXY_PORT="${PROXY_PORT:-8000}"
DEBUG_BOOK="${DEBUG_BOOK:--1}"
DEBUG_EVERY_N="${DEBUG_EVERY_N:-1}"
DEBUG_SUMMARY_N="${DEBUG_SUMMARY_N:-100}"
DEBUG_JSONL="${DEBUG_JSONL:-1}"
RESET_SESSION="${RESET_SESSION:-0}"
NO_ATTACH="${NO_ATTACH:-0}"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="${SN79_ROOT:-$SCRIPT_DIR}"
AGENTS_DIR="$REPO_ROOT/agents"
PROXY_DIR="$AGENTS_DIR/proxy"
SIM_DIR="$REPO_ROOT/simulate/trading/run"
CONFIG_FILE="${CONFIG_FILE:-$PROXY_DIR/config.strategy1_debug.json}"
AGENT_FILE="$AGENTS_DIR/Strategy1_Debug.py"
SIM_XML="${SIM_XML:-config/simulation_0.xml}"
TAOSIM_BIN="${TAOSIM_BIN:-../build/src/cpp/taosim}"
PYTHON_BIN="${PYTHON_BIN:-python}"

RUN_ID="${RUN_ID:-$(date +%Y%m%d_%H%M%S)}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/logs/strategy1_debug/$RUN_ID}"
DEBUG_DIR="$LOG_DIR/debug"
JSONL_FILE="$DEBUG_DIR/strategy1_debug_agent_${AGENT_ID}.jsonl"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

command -v tmux >/dev/null 2>&1 || fail "tmux is not installed. Run: sudo apt update && sudo apt install -y tmux"
command -v "$PYTHON_BIN" >/dev/null 2>&1 || fail "Python executable not found: $PYTHON_BIN"
[[ -d "$REPO_ROOT" ]] || fail "SN79 root not found: $REPO_ROOT"
[[ -f "$AGENT_FILE" ]] || fail "Missing agent: $AGENT_FILE"
[[ -f "$PROXY_DIR/proxy.py" ]] || fail "Missing proxy.py: $PROXY_DIR/proxy.py"
[[ -f "$CONFIG_FILE" ]] || fail "Missing proxy config: $CONFIG_FILE"
[[ -x "$SIM_DIR/$TAOSIM_BIN" ]] || fail "taosim binary is missing or not executable: $SIM_DIR/$TAOSIM_BIN"
[[ -f "$SIM_DIR/$SIM_XML" ]] || fail "Simulation XML not found: $SIM_DIR/$SIM_XML"

"$PYTHON_BIN" -m py_compile "$AGENT_FILE" || fail "Strategy1_Debug.py failed syntax compilation"
"$PYTHON_BIN" - <<PY || fail "Invalid JSON config: $CONFIG_FILE"
import json
from pathlib import Path
json.loads(Path(r"$CONFIG_FILE").read_text(encoding="utf-8"))
PY

mkdir -p "$DEBUG_DIR"
cp "$CONFIG_FILE" "$LOG_DIR/proxy_config.json"
cp "$SIM_DIR/$SIM_XML" "$LOG_DIR/simulation.xml"

cat > "$LOG_DIR/run.env" <<EOF
RUN_ID=$RUN_ID
SESSION=$SESSION
REPO_ROOT=$REPO_ROOT
AGENT_ID=$AGENT_ID
AGENT_PORT=$AGENT_PORT
PROXY_PORT=$PROXY_PORT
DEBUG_BOOK=$DEBUG_BOOK
DEBUG_EVERY_N=$DEBUG_EVERY_N
DEBUG_SUMMARY_N=$DEBUG_SUMMARY_N
DEBUG_JSONL=$DEBUG_JSONL
CONFIG_FILE=$CONFIG_FILE
SIM_XML=$SIM_XML
TAOSIM_BIN=$TAOSIM_BIN
EOF

if tmux has-session -t "$SESSION" 2>/dev/null; then
  if [[ "$RESET_SESSION" == "1" ]]; then
    tmux kill-session -t "$SESSION"
  else
    fail "tmux session '$SESSION' already exists. Attach with: tmux attach -t $SESSION; or restart with RESET_SESSION=1 $0"
  fi
fi

printf -v PROXY_CMD \
  'cd %q && set -o pipefail; %q -u proxy.py --config %q 2>&1 | tee %q' \
  "$PROXY_DIR" "$PYTHON_BIN" "$CONFIG_FILE" "$LOG_DIR/proxy.log"

printf -v AGENT_CMD \
  'cd %q && set -o pipefail; STRATEGY1_DEBUG=1 STRATEGY1_DEBUG_EVERY_N=%q STRATEGY1_DEBUG_SUMMARY_N=%q STRATEGY1_DEBUG_BOOK=%q STRATEGY1_DEBUG_JSONL=%q STRATEGY1_DEBUG_DIR=%q %q -u Strategy1_Debug.py --port %q --agent_id %q --params enable_mm_strategy=1 verbose_log=0 log_every_n=100 debug_enabled=1 debug_every_n=%q debug_summary_every_n=%q debug_jsonl=%q debug_book_id=%q 2>&1 | tee %q' \
  "$AGENTS_DIR" "$DEBUG_EVERY_N" "$DEBUG_SUMMARY_N" "$DEBUG_BOOK" "$DEBUG_JSONL" "$DEBUG_DIR" \
  "$PYTHON_BIN" "$AGENT_PORT" "$AGENT_ID" "$DEBUG_EVERY_N" "$DEBUG_SUMMARY_N" "$DEBUG_JSONL" "$DEBUG_BOOK" "$LOG_DIR/agent.log"

printf -v SIM_CMD \
  'cd %q && set -o pipefail; echo "Waiting for proxy port %q and agent port %q..."; until (echo > /dev/tcp/127.0.0.1/%q) >/dev/null 2>&1; do sleep 1; done; until (echo > /dev/tcp/127.0.0.1/%q) >/dev/null 2>&1; do sleep 1; done; echo "Proxy and agent are ready. Starting taosim."; %q -f %q 2>&1 | tee %q' \
  "$SIM_DIR" "$PROXY_PORT" "$AGENT_PORT" "$PROXY_PORT" "$AGENT_PORT" "$TAOSIM_BIN" "$SIM_XML" "$LOG_DIR/simulator.log"

printf -v JSON_MONITOR_CMD \
  'mkdir -p %q; touch %q; echo "Watching %s"; tail -n 50 -F %q' \
  "$DEBUG_DIR" "$JSONL_FILE" "$JSONL_FILE" "$JSONL_FILE"

printf -v PROCESS_MONITOR_CMD \
  'while true; do clear; date; echo; ps -eo pid,etime,%%cpu,%%mem,cmd | grep -E "proxy.py|Strategy1_Debug.py|taosim" | grep -v grep || true; echo; echo "Run logs: %s"; sleep 2; done' \
  "$LOG_DIR"

tmux new-session -d -s "$SESSION" -n proxy
tmux send-keys -t "$SESSION:proxy.0" "$PROXY_CMD" C-m

tmux new-window -t "$SESSION" -n agent
tmux send-keys -t "$SESSION:agent.0" "$AGENT_CMD" C-m

tmux new-window -t "$SESSION" -n simulator
tmux send-keys -t "$SESSION:simulator.0" "$SIM_CMD" C-m

tmux new-window -t "$SESSION" -n monitor
tmux send-keys -t "$SESSION:monitor.0" "$JSON_MONITOR_CMD" C-m
tmux split-window -v -t "$SESSION:monitor.0"
tmux send-keys -t "$SESSION:monitor.1" "$PROCESS_MONITOR_CMD" C-m

tmux select-window -t "$SESSION:proxy"

cat <<EOF

SN79 Strategy1_Debug local test started.

Session:   $SESSION
Run ID:    $RUN_ID
Logs:      $LOG_DIR
JSONL:     $JSONL_FILE

Attach:    tmux attach -t $SESSION
Detach:    Ctrl-b, then d
Stop:      tmux kill-session -t $SESSION
Restart:   RESET_SESSION=1 $0

Windows:
  0 proxy
  1 agent
  2 simulator
  3 monitor
EOF

if [[ "$NO_ATTACH" != "1" ]]; then
  exec tmux attach -t "$SESSION"
fi
