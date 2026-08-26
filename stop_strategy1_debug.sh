#!/usr/bin/env bash
set -Eeuo pipefail

SESSION="${SESSION:-sn79_s1_debug}"
CLEAN_IPC=0
[[ "${1:-}" == "--clean-ipc" ]] && CLEAN_IPC=1

echo "[stop] stopping tmux session: $SESSION"
tmux kill-session -t "$SESSION" 2>/dev/null || true
sleep 1

# Kill only remaining local-test processes with these explicit names.
pkill -f 'Strategy1_Debug.py' 2>/dev/null || true
pkill -f 'agents/proxy/proxy.py' 2>/dev/null || true
pkill -f 'taosim' 2>/dev/null || true
sleep 1

echo "[stop] remaining matching processes:"
pgrep -af 'Strategy1_Debug.py|agents/proxy/proxy.py|taosim' || echo "  none"

if (( CLEAN_IPC == 1 )); then
  if pgrep -f 'taosim' >/dev/null 2>&1; then
    echo "ERROR: refusing IPC cleanup while taosim is still running" >&2
    exit 1
  fi

  echo "[stop] removing stale local simulator IPC"
  rm -f /dev/mqueue/taosim-req /dev/mqueue/taosim-res 2>/dev/null || true
  rm -f /dev/shm/state /dev/shm/responses 2>/dev/null || true
fi

echo "[stop] done"
