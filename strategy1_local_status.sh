#!/usr/bin/env bash
set -Eeuo pipefail

SESSION="${SESSION:-sn79_s1_debug}"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
ROOT="${SN79_ROOT:-$SCRIPT_DIR}"
LOG="$ROOT/logs/strategy1_debug/latest"

echo "========================================================"
echo " SN79 Strategy1_Debug local status"
echo "========================================================"
echo

echo "TMUX"
if tmux has-session -t "$SESSION" 2>/dev/null; then
  echo "  session: UP ($SESSION)"
  tmux list-windows -t "$SESSION" -F '  window #{window_index}: #{window_name} panes=#{window_panes} active=#{window_active}'
else
  echo "  session: DOWN ($SESSION)"
fi

echo
echo "PROCESSES"
pgrep -af 'agents/proxy/proxy.py|Strategy1_Debug.py|taosim' || echo "  none"

echo
echo "PORTS"
ss -ltnp 2>/dev/null | grep -E ':8000|:8888' || echo "  no listeners on 8000/8888"

echo
echo "LATEST RUN"
if [[ -e "$LOG" ]]; then
  readlink -f "$LOG" || true
  echo
  for f in proxy.log agent.log simulator.log debug/strategy1_debug_agent_0.jsonl; do
    if [[ -f "$LOG/$f" ]]; then
      printf '  %-45s %10s bytes\n' "$f" "$(stat -c %s "$LOG/$f")"
    else
      printf '  %-45s MISSING\n' "$f"
    fi
  done

  echo
  echo "LAST PROXY ERRORS/WARNINGS"
  grep -iE 'error|exception|traceback|failed|timeout|warning' "$LOG/proxy.log" 2>/dev/null | tail -20 || true

  echo
  echo "LAST SIMULATOR LINES"
  tail -20 "$LOG/simulator.log" 2>/dev/null || true

  echo
  echo "DEBUG EVENT COUNTS"
  if [[ -s "$LOG/debug/strategy1_debug_agent_0.jsonl" ]] && command -v jq >/dev/null 2>&1; then
    jq -r '.type // "UNKNOWN"' "$LOG/debug/strategy1_debug_agent_0.jsonl" 2>/dev/null \
      | sort | uniq -c | sort -nr || true
  else
    echo "  no JSONL events yet"
  fi
else
  echo "  No logs/strategy1_debug/latest run found."
fi
