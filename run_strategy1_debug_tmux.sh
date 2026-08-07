#!/usr/bin/env bash
set -Eeuo pipefail

# Ubuntu launcher for the SN79 Strategy1_Debug local test.
#
# Supported repository layouts:
#   <repo>/agents/strategy/Strategy1_Debug.py
#   <repo>/agents/Strategy1_Debug.py
#
# The script may be stored in the repository root or in a subdirectory.
# It searches upward to locate the repository automatically.
#
# Examples:
#   bash run_strategy1_debug_tmux.sh --check
#   bash run_strategy1_debug_tmux.sh
#   bash run_strategy1_debug_tmux.sh --reset
#   bash run_strategy1_debug_tmux.sh --reset --book 3 --every 10

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
RESET_SESSION=0
CHECK_ONLY=0
ROOT_OVERRIDE="${SN79_ROOT:-}"
PYTHON_OVERRIDE="${PYTHON_BIN:-}"
SIM_XML_OVERRIDE="${SIM_XML:-}"
TAOSIM_OVERRIDE="${TAOSIM_BIN:-}"

usage() {
    cat <<'EOF'
Usage:
  bash run_strategy1_debug_tmux.sh [options]

Options:
  --root PATH          SN79 repository root
  --session NAME       tmux session name (default: sn79_s1_debug)
  --agent-id N         local simulator agent ID (default: 0)
  --agent-port PORT    Strategy1_Debug listener port (default: 8888)
  --proxy-port PORT    proxy listener port (default: 8000)
  --book ID            debug one book; -1 means all books
  --every N            detailed debug interval in ticks
  --summary N          summary interval in ticks
  --python PATH        Python interpreter; active venv is used by default
  --sim-xml PATH       simulation XML path
  --taosim PATH        taosim executable path
  --reset              stop an existing tmux session before starting
  --no-attach          start in the background without attaching
  --check              validate paths and dependencies only
  -h, --help           show this help

Environment:
  EXTRA_AGENT_PARAMS="key=value key=value"
  DEBUG_JSONL=0|1
  SN79_ROOT=/path/to/repository
EOF
}

die() {
    printf '\nERROR: %s\n\n' "$*" >&2
    exit 1
}

info() {
    printf '[INFO] %s\n' "$*"
}

shell_quote() {
    printf '%q' "$1"
}

is_integer() {
    [[ "$1" =~ ^-?[0-9]+$ ]]
}

is_port() {
    [[ "$1" =~ ^[0-9]+$ ]] && (( "$1" >= 1 && "$1" <= 65535 ))
}

while (( $# > 0 )); do
    case "$1" in
        --root)
            [[ $# -ge 2 ]] || die "--root requires a path"
            ROOT_OVERRIDE="$2"
            shift 2
            ;;
        --session)
            [[ $# -ge 2 ]] || die "--session requires a name"
            SESSION="$2"
            shift 2
            ;;
        --agent-id)
            [[ $# -ge 2 ]] || die "--agent-id requires an integer"
            AGENT_ID="$2"
            shift 2
            ;;
        --agent-port)
            [[ $# -ge 2 ]] || die "--agent-port requires a port"
            AGENT_PORT="$2"
            shift 2
            ;;
        --proxy-port)
            [[ $# -ge 2 ]] || die "--proxy-port requires a port"
            PROXY_PORT="$2"
            shift 2
            ;;
        --book)
            [[ $# -ge 2 ]] || die "--book requires an integer"
            DEBUG_BOOK="$2"
            shift 2
            ;;
        --every)
            [[ $# -ge 2 ]] || die "--every requires an integer"
            DEBUG_EVERY_N="$2"
            shift 2
            ;;
        --summary)
            [[ $# -ge 2 ]] || die "--summary requires an integer"
            DEBUG_SUMMARY_N="$2"
            shift 2
            ;;
        --python)
            [[ $# -ge 2 ]] || die "--python requires a path"
            PYTHON_OVERRIDE="$2"
            shift 2
            ;;
        --sim-xml)
            [[ $# -ge 2 ]] || die "--sim-xml requires a path"
            SIM_XML_OVERRIDE="$2"
            shift 2
            ;;
        --taosim)
            [[ $# -ge 2 ]] || die "--taosim requires a path"
            TAOSIM_OVERRIDE="$2"
            shift 2
            ;;
        --reset)
            RESET_SESSION=1
            shift
            ;;
        --no-attach)
            NO_ATTACH=1
            shift
            ;;
        --check)
            CHECK_ONLY=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            die "Unknown option: $1"
            ;;
    esac
done

is_integer "$AGENT_ID" || die "Invalid agent ID: $AGENT_ID"
is_integer "$DEBUG_BOOK" || die "Invalid book ID: $DEBUG_BOOK"
is_integer "$DEBUG_EVERY_N" && (( DEBUG_EVERY_N >= 1 )) \
    || die "--every must be at least 1"
is_integer "$DEBUG_SUMMARY_N" && (( DEBUG_SUMMARY_N >= 1 )) \
    || die "--summary must be at least 1"
is_port "$AGENT_PORT" || die "Invalid agent port: $AGENT_PORT"
is_port "$PROXY_PORT" || die "Invalid proxy port: $PROXY_PORT"
[[ "$AGENT_PORT" != "$PROXY_PORT" ]] || die "Agent and proxy ports must differ"

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CURRENT_DIR="$(pwd -P)"

looks_like_repo_root() {
    local candidate="$1"

    [[ -f "$candidate/agents/proxy/proxy.py" ]] || return 1

    if [[ -f "$candidate/agents/strategy/Strategy1.py" ]] \
        || [[ -f "$candidate/agents/Strategy1.py" ]]; then
        return 0
    fi

    return 1
}

search_upward_for_root() {
    local start="$1"
    local current

    current="$(cd -- "$start" 2>/dev/null && pwd -P)" || return 1

    while :; do
        if looks_like_repo_root "$current"; then
            printf '%s\n' "$current"
            return 0
        fi

        [[ "$current" != "/" ]] || break
        current="$(dirname -- "$current")"
    done

    return 1
}

if [[ -n "$ROOT_OVERRIDE" ]]; then
    [[ -d "$ROOT_OVERRIDE" ]] || die "Repository root does not exist: $ROOT_OVERRIDE"
    REPO_ROOT="$(cd -- "$ROOT_OVERRIDE" && pwd -P)"
else
    REPO_ROOT="$(search_upward_for_root "$SCRIPT_DIR" || true)"

    if [[ -z "$REPO_ROOT" ]]; then
        REPO_ROOT="$(search_upward_for_root "$CURRENT_DIR" || true)"
    fi

    [[ -n "$REPO_ROOT" ]] || die \
        "Could not locate the repository root. Use --root /path/to/orthoxplus_gold"
fi

# This repository stores trading agents under agents/strategy/.
AGENTS_DIR="$REPO_ROOT/agents"
AGENT_DIR="$AGENTS_DIR/strategy"
AGENT_FILE="$AGENTS_DIR/strategy/Strategy1_Debug.py"
STRATEGY_FILE="$AGENTS_DIR/strategy/Strategy1.py"

[[ -f "$AGENT_FILE" ]] || die "Missing debug agent: $AGENT_FILE"
[[ -f "$STRATEGY_FILE" ]] || die "Missing base strategy: $STRATEGY_FILE"
PROXY_DIR="$REPO_ROOT/agents/proxy"
PROXY_FILE="$PROXY_DIR/proxy.py"

[[ -f "$PROXY_FILE" ]] || die "Missing proxy: $PROXY_FILE"

if [[ -n "$PYTHON_OVERRIDE" ]]; then
    if [[ "$PYTHON_OVERRIDE" == */* ]]; then
        [[ -x "$PYTHON_OVERRIDE" ]] || die "Python is not executable: $PYTHON_OVERRIDE"
        PYTHON_BIN="$(cd -- "$(dirname -- "$PYTHON_OVERRIDE")" && pwd -P)/$(basename -- "$PYTHON_OVERRIDE")"
    else
        PYTHON_BIN="$(command -v "$PYTHON_OVERRIDE" || true)"
        [[ -n "$PYTHON_BIN" ]] || die "Python command not found: $PYTHON_OVERRIDE"
    fi
elif [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    PYTHON_BIN="${VIRTUAL_ENV}/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    die "python3 is not installed"
fi

if [[ -n "$SIM_XML_OVERRIDE" ]]; then
    if [[ "$SIM_XML_OVERRIDE" = /* ]]; then
        SIM_XML_SOURCE="$SIM_XML_OVERRIDE"
    else
        SIM_XML_SOURCE="$REPO_ROOT/$SIM_XML_OVERRIDE"
    fi
else
    SIM_XML_SOURCE="$REPO_ROOT/simulate/trading/run/config/simulation_0.xml"

    if [[ ! -f "$SIM_XML_SOURCE" ]]; then
        SIM_XML_SOURCE="$(
            find "$REPO_ROOT/simulate" -maxdepth 6 -type f \
                -name 'simulation_0.xml' -print -quit 2>/dev/null || true
        )"
    fi
fi

[[ -n "$SIM_XML_SOURCE" && -f "$SIM_XML_SOURCE" ]] \
    || die "simulation_0.xml was not found. Use --sim-xml PATH"
SIM_XML_SOURCE="$(cd -- "$(dirname -- "$SIM_XML_SOURCE")" && pwd -P)/$(basename -- "$SIM_XML_SOURCE")"

if [[ -n "$TAOSIM_OVERRIDE" ]]; then
    if [[ "$TAOSIM_OVERRIDE" = /* ]]; then
        TAOSIM_BIN="$TAOSIM_OVERRIDE"
    elif [[ "$TAOSIM_OVERRIDE" == */* ]]; then
        TAOSIM_BIN="$REPO_ROOT/$TAOSIM_OVERRIDE"
    else
        TAOSIM_BIN="$(command -v "$TAOSIM_OVERRIDE" || true)"
    fi
else
    TAOSIM_BIN="$REPO_ROOT/simulate/trading/build/src/cpp/taosim"

    if [[ ! -f "$TAOSIM_BIN" ]]; then
        TAOSIM_BIN="$(command -v taosim || true)"
    fi

    if [[ -z "$TAOSIM_BIN" || ! -f "$TAOSIM_BIN" ]]; then
        TAOSIM_BIN="$(
            find "$REPO_ROOT/simulate" -maxdepth 8 -type f \
                -name taosim -print -quit 2>/dev/null || true
        )"
    fi
fi

[[ -n "$TAOSIM_BIN" && -f "$TAOSIM_BIN" ]] \
    || die "taosim was not found. Build the simulator or use --taosim PATH"
[[ -x "$TAOSIM_BIN" ]] \
    || die "taosim is not executable. Run: chmod +x $(shell_quote "$TAOSIM_BIN")"
TAOSIM_BIN="$(cd -- "$(dirname -- "$TAOSIM_BIN")" && pwd -P)/$(basename -- "$TAOSIM_BIN")"

# simulation_0.xml normally lives in simulate/trading/run/config.
# Run taosim from the parent "run" directory so relative paths keep working.
SIM_XML_DIR="$(dirname -- "$SIM_XML_SOURCE")"
if [[ "$(basename -- "$SIM_XML_DIR")" == "config" ]]; then
    SIM_RUN_DIR="$(dirname -- "$SIM_XML_DIR")"
else
    SIM_RUN_DIR="$SIM_XML_DIR"
fi

command -v tmux >/dev/null 2>&1 \
    || die "tmux is not installed. Run: sudo apt update && sudo apt install -y tmux"

"$PYTHON_BIN" -m py_compile "$STRATEGY_FILE" "$AGENT_FILE" \
    || die "Strategy1.py or Strategy1_Debug.py failed Python syntax validation"

cat <<EOF

SN79 Strategy1_Debug environment
--------------------------------
Repository:       $REPO_ROOT
Agent directory:  $AGENT_DIR
Debug agent:      $AGENT_FILE
Proxy:            $PROXY_FILE
Simulation XML:   $SIM_XML_SOURCE
taosim:           $TAOSIM_BIN
Python:           $PYTHON_BIN
tmux session:     $SESSION
Proxy port:       $PROXY_PORT
Agent port:       $AGENT_PORT

EOF

if (( CHECK_ONLY == 1 )); then
    info "Path, dependency, and Python syntax checks passed."
    exit 0
fi

# Import validation catches missing packages or imports before tmux starts.
if ! (
    cd -- "$AGENT_DIR"
    export PYTHONPATH="$REPO_ROOT:$AGENT_DIR:${PYTHONPATH:-}"
    "$PYTHON_BIN" -c \
        'from Strategy1_Debug import Strategy1_Debug; print("Strategy1_Debug import OK")'
); then
    die "Strategy1_Debug import failed. Confirm the active virtual environment and pip install -e ."
fi

if tmux has-session -t "$SESSION" 2>/dev/null; then
    if (( RESET_SESSION == 1 )); then
        info "Stopping existing tmux session: $SESSION"
        tmux kill-session -t "$SESSION"
        sleep 1
    else
        die "tmux session '$SESSION' already exists. Use --reset or run: tmux attach -t $SESSION"
    fi
fi

port_is_open() {
    local port="$1"
    (exec 3<>"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1
}

if port_is_open "$PROXY_PORT"; then
    die "Proxy port $PROXY_PORT is already in use"
fi

if port_is_open "$AGENT_PORT"; then
    die "Agent port $AGENT_PORT is already in use"
fi

RUN_ID="$(date +%Y%m%d_%H%M%S)"
LOG_BASE="$REPO_ROOT/logs/strategy1_debug"
LOG_DIR="$LOG_BASE/$RUN_ID"
DEBUG_DIR="$LOG_DIR/debug"
RUNTIME_CONFIG="$LOG_DIR/proxy_config.runtime.json"
RUNTIME_XML="$LOG_DIR/simulation_0.runtime.xml"
JSONL_FILE="$DEBUG_DIR/strategy1_debug_agent_${AGENT_ID}.jsonl"

mkdir -p "$DEBUG_DIR"

# Create a runtime XML copy with a proxy port matching this run.
"$PYTHON_BIN" - "$SIM_XML_SOURCE" "$RUNTIME_XML" "$PROXY_PORT" <<'PY'
from pathlib import Path
import re
import sys

source = Path(sys.argv[1])
target = Path(sys.argv[2])
port = sys.argv[3]

text = source.read_text(encoding="utf-8")
pattern = r'(<Simulation\b[^>]*\bport\s*=\s*["\'])\d+(["\'])'
updated, count = re.subn(pattern, rf'\g<1>{port}\2', text, count=1)

if count == 0:
    updated, count = re.subn(
        r'<Simulation\b',
        f'<Simulation port="{port}"',
        text,
        count=1,
    )

if count == 0:
    raise SystemExit("Could not find the <Simulation> element in the XML")

target.write_text(updated, encoding="utf-8")
PY

# Generate a config with the actual agents/strategy path used by this repository.
"$PYTHON_BIN" - \
    "$RUNTIME_CONFIG" \
    "$RUNTIME_XML" \
    "$AGENT_DIR" \
    "$PROXY_PORT" \
    "$AGENT_PORT" \
    "$PROXY_TIMEOUT" \
    "$DEBUG_BOOK" \
    "$DEBUG_EVERY_N" \
    "$DEBUG_SUMMARY_N" \
    "$DEBUG_JSONL" <<'PY'
from pathlib import Path
import json
import sys

(
    output,
    simulation_xml,
    agent_dir,
    proxy_port,
    agent_port,
    timeout,
    debug_book,
    debug_every,
    debug_summary,
    debug_jsonl,
) = sys.argv[1:]

config = {
    "proxy": {
        "port": int(proxy_port),
        "simulation_xml": simulation_xml,
        "timeout": int(timeout),
    },
    "agents": {
        "path": agent_dir,
        "start_port": int(agent_port),
        "Strategy1_Debug": [
            {
                "params": {
                    "enable_mm_strategy": True,
                    "verbose_log": False,
                    "log_every_n": 100,
                    "debug_enabled": True,
                    "debug_every_n": int(debug_every),
                    "debug_summary_every_n": int(debug_summary),
                    "debug_jsonl": bool(int(debug_jsonl)),
                    "debug_book_id": int(debug_book),
                },
                "count": 1,
            }
        ],
    },
}

Path(output).write_text(
    json.dumps(config, indent=2) + "\n",
    encoding="utf-8",
)
PY

cp -- "$SIM_XML_SOURCE" "$LOG_DIR/simulation_0.original.xml"

RUN_PROXY="$LOG_DIR/run_proxy.sh"
RUN_AGENT="$LOG_DIR/run_agent.sh"
RUN_SIMULATOR="$LOG_DIR/run_simulator.sh"
RUN_JSON_MONITOR="$LOG_DIR/run_json_monitor.sh"
RUN_PROCESS_MONITOR="$LOG_DIR/run_process_monitor.sh"
STOP_SCRIPT="$LOG_DIR/stop.sh"

{
    printf '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
    printf 'cd -- %q\n' "$PROXY_DIR"
    printf 'export PYTHONPATH=%q:%q:${PYTHONPATH:-}\n' "$REPO_ROOT" "$AGENT_DIR"
    printf 'exec > >(tee -a %q) 2>&1\n' "$LOG_DIR/proxy.log"
    printf 'echo "[proxy] starting at $(date --iso-8601=seconds)"\n'
    printf 'exec %q -u %q --config %q\n' "$PYTHON_BIN" "$PROXY_FILE" "$RUNTIME_CONFIG"
} > "$RUN_PROXY"

{
    printf '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
    printf 'cd -- %q\n' "$AGENT_DIR"
    printf 'export PYTHONPATH=%q:%q:${PYTHONPATH:-}\n' "$REPO_ROOT" "$AGENT_DIR"
    printf 'export PYTHONUNBUFFERED=1\n'
    printf 'export STRATEGY1_DEBUG=1\n'
    printf 'export STRATEGY1_DEBUG_BOOK=%q\n' "$DEBUG_BOOK"
    printf 'export STRATEGY1_DEBUG_EVERY_N=%q\n' "$DEBUG_EVERY_N"
    printf 'export STRATEGY1_DEBUG_SUMMARY_N=%q\n' "$DEBUG_SUMMARY_N"
    printf 'export STRATEGY1_DEBUG_JSONL=%q\n' "$DEBUG_JSONL"
    printf 'export STRATEGY1_DEBUG_DIR=%q\n' "$DEBUG_DIR"
    printf 'exec > >(tee -a %q) 2>&1\n' "$LOG_DIR/agent.log"
    printf 'params=(\n'
    printf '  enable_mm_strategy=1\n'
    printf '  verbose_log=0\n'
    printf '  log_every_n=100\n'
    printf '  debug_enabled=1\n'
    printf '  debug_every_n=%q\n' "$DEBUG_EVERY_N"
    printf '  debug_summary_every_n=%q\n' "$DEBUG_SUMMARY_N"
    printf '  debug_jsonl=%q\n' "$DEBUG_JSONL"
    printf '  debug_book_id=%q\n' "$DEBUG_BOOK"
    printf ')\n'
    cat <<'EOF'
if [[ -n "${EXTRA_AGENT_PARAMS:-}" ]]; then
    read -r -a extra_params <<< "$EXTRA_AGENT_PARAMS"
    params+=("${extra_params[@]}")
fi
EOF
    printf 'echo "[agent] starting at $(date --iso-8601=seconds)"\n'
    printf 'exec %q -u %q --port %q --agent_id %q --params "${params[@]}"\n' \
        "$PYTHON_BIN" "$AGENT_FILE" "$AGENT_PORT" "$AGENT_ID"
} > "$RUN_AGENT"

{
    printf '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
    printf 'cd -- %q\n' "$SIM_RUN_DIR"
    printf 'exec > >(tee -a %q) 2>&1\n' "$LOG_DIR/simulator.log"
    printf 'PROXY_PORT=%q\n' "$PROXY_PORT"
    printf 'AGENT_PORT=%q\n' "$AGENT_PORT"
    cat <<'EOF'
wait_for_port() {
    local label="$1"
    local port="$2"
    local timeout_seconds="$3"
    local elapsed=0

    while (( elapsed < timeout_seconds )); do
        if (exec 3<>"/dev/tcp/127.0.0.1/$port") >/dev/null 2>&1; then
            echo "[$label] port $port is ready"
            return 0
        fi

        sleep 1
        ((elapsed += 1))
    done

    echo "ERROR: $label port $port did not become ready within ${timeout_seconds}s" >&2
    return 1
}

echo "[simulator] waiting for proxy and agent"
wait_for_port proxy "$PROXY_PORT" 60
wait_for_port agent "$AGENT_PORT" 60
EOF
    printf 'echo "[simulator] starting at $(date --iso-8601=seconds)"\n'
    printf 'exec %q -f %q\n' "$TAOSIM_BIN" "$RUNTIME_XML"
} > "$RUN_SIMULATOR"

{
    printf '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
    printf 'mkdir -p -- %q\n' "$DEBUG_DIR"
    printf 'touch %q\n' "$JSONL_FILE"
    printf 'echo "Watching: %s"\n' "$JSONL_FILE"
    printf 'exec tail -n 80 -F %q\n' "$JSONL_FILE"
} > "$RUN_JSON_MONITOR"

{
    printf '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
    printf 'LOG_DIR=%q\n' "$LOG_DIR"
    cat <<'EOF'
while true; do
    clear
    date
    echo
    ps -eo pid,etime,%cpu,%mem,cmd \
        | grep -E 'proxy\.py|Strategy1_Debug\.py|taosim' \
        | grep -v grep || true
    echo
    echo "Logs: $LOG_DIR"
    sleep 2
done
EOF
} > "$RUN_PROCESS_MONITOR"

{
    printf '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
    printf 'tmux kill-session -t %q 2>/dev/null || true\n' "$SESSION"
} > "$STOP_SCRIPT"

chmod 755 \
    "$RUN_PROXY" \
    "$RUN_AGENT" \
    "$RUN_SIMULATOR" \
    "$RUN_JSON_MONITOR" \
    "$RUN_PROCESS_MONITOR" \
    "$STOP_SCRIPT"

cat > "$LOG_DIR/run_info.txt" <<EOF
RUN_ID=$RUN_ID
SESSION=$SESSION
REPO_ROOT=$REPO_ROOT
AGENT_DIR=$AGENT_DIR
AGENT_FILE=$AGENT_FILE
AGENT_ID=$AGENT_ID
AGENT_PORT=$AGENT_PORT
PROXY_PORT=$PROXY_PORT
PYTHON_BIN=$PYTHON_BIN
SIM_XML_SOURCE=$SIM_XML_SOURCE
RUNTIME_XML=$RUNTIME_XML
TAOSIM_BIN=$TAOSIM_BIN
DEBUG_BOOK=$DEBUG_BOOK
DEBUG_EVERY_N=$DEBUG_EVERY_N
DEBUG_SUMMARY_N=$DEBUG_SUMMARY_N
EOF

if [[ -L "$LOG_BASE/latest" || ! -e "$LOG_BASE/latest" ]]; then
    ln -sfn "$RUN_ID" "$LOG_BASE/latest"
fi

send_runner_to_tmux() {
    local target="$1"
    local runner="$2"
    local command

    printf -v command 'bash %q' "$runner"
    tmux send-keys -t "$target" "$command" C-m
}

tmux new-session -d -s "$SESSION" -n proxy
send_runner_to_tmux "$SESSION:proxy.0" "$RUN_PROXY"

tmux new-window -t "$SESSION" -n agent
send_runner_to_tmux "$SESSION:agent.0" "$RUN_AGENT"

tmux new-window -t "$SESSION" -n simulator
send_runner_to_tmux "$SESSION:simulator.0" "$RUN_SIMULATOR"

tmux new-window -t "$SESSION" -n monitor
send_runner_to_tmux "$SESSION:monitor.0" "$RUN_JSON_MONITOR"
tmux split-window -v -t "$SESSION:monitor.0"
send_runner_to_tmux "$SESSION:monitor.1" "$RUN_PROCESS_MONITOR"
tmux select-layout -t "$SESSION:monitor" even-vertical

tmux select-window -t "$SESSION:agent"

cat <<EOF

SN79 Strategy1_Debug local test started
--------------------------------------
Session:   $SESSION
Run ID:    $RUN_ID
Agent dir: $AGENT_DIR
Logs:      $LOG_DIR
JSONL:     $JSONL_FILE

Windows:
  0  proxy
  1  agent
  2  simulator
  3  monitor

Detach:    Ctrl-b, then d
Reattach:  tmux attach -t $SESSION
Stop:      bash $(shell_quote "$STOP_SCRIPT")
Restart:   bash $(shell_quote "$0") --reset

EOF

if (( NO_ATTACH == 0 )); then
    exec tmux attach-session -t "$SESSION"
fi
