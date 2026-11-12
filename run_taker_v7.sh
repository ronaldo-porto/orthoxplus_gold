#!/usr/bin/env bash
# v7.2 TakerV7 launcher (agents/strategy/TakerV7.py + taker_v7_core.py): class 0 = the taker core (slope 0.005),
# class 1 = the maker leg (flat two-sided post-only quotes).  Defaults: V7_CLASSES=0, V7_MAKER_CLASSES=1.
# V7_MAKER_CLASSES=none turns the maker leg off; =1,2 adds class 2.  V7_CLASSES=0,1 (the v7.1 class-1 taker, option A)
# is accepted only with V7_MAKER_CLASSES=none.
#
# Same flag interface as run_strategy1_research_simple_multi.sh:
#   -w <wallet name> -h <hotkey name> -u <netuid> -a <axon port> -e <endpoint> [-p <wallet path>]
#   -i <pm2 name> | --pm2_name <pm2 name> | --pm2_name=<pm2 name>
#   --history_anchor auto | established | <sim>@<seconds>   (accepted and LOGGED only: v7 has no history gate)
# Every agent parameter has its default here, so a launch needs no -m string:
#   ./run_taker_v7.sh --pm2_name <name> --history_anchor established -w <wallet> -h <hotkey> -u 366 -a 8091 \
#       -e wss://test.finney.opentensor.ai:443
# Before anything starts, the preflight runs the v7 tests (tests/run_tests.py tests/test_taker_v7.py) and refuses to
# launch on any failure.  V7_PREFLIGHT_ONLY=1 runs the preflight and exits.  PYTHON overrides the interpreter
# (default: python, the one pm2 starts the miner with in run_miner_multi.sh).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

WALLET_NAME="${WALLET_NAME:-taos}"
HOTKEY_NAME="${HOTKEY_NAME:-miner}"
ENDPOINT="${ENDPOINT:-wss://test.finney.opentensor.ai:443}"
NETUID="${NETUID:-366}"
AXON_PORT="${AXON_PORT:-8091}"
AGENT_PATH="${AGENT_PATH:-$SCRIPT_DIR/agents/strategy}"
PM2_NAME="${PM2_NAME:-sn79-taker-v7}"
PYTHON="${PYTHON:-python}"
LOG_DIR="${V7_LOG_DIR:-$SCRIPT_DIR/logs/m1_taker_v7}"
HISTORY_ANCHOR_SOURCE="default"
[[ -n "${HISTORY_ANCHOR:-}" ]] && HISTORY_ANCHOR_SOURCE="env"
HISTORY_ANCHOR="${HISTORY_ANCHOR:-auto}"
V7_CLASSES="${V7_CLASSES:-0}"
V7_MAKER_CLASSES="${V7_MAKER_CLASSES:-1}"
EXTRA=()

_normalized_args=()
while (($#)); do
  case "$1" in
    --pm2_name)
      [[ $# -ge 2 && -n "${2:-}" ]] || { echo "ERROR: --pm2_name requires a value" >&2; exit 2; }
      _normalized_args+=(-i "$2"); shift 2 ;;
    --pm2_name=*)
      [[ -n "${1#*=}" ]] || { echo "ERROR: --pm2_name requires a value" >&2; exit 2; }
      _normalized_args+=(-i "${1#*=}"); shift ;;
    --history_anchor)
      [[ $# -ge 2 && -n "${2:-}" ]] || { echo "ERROR: --history_anchor requires auto, established or <sim>@<seconds>" >&2; exit 2; }
      HISTORY_ANCHOR="$2"; HISTORY_ANCHOR_SOURCE="flag"; shift 2 ;;
    --history_anchor=*)
      [[ -n "${1#*=}" ]] || { echo "ERROR: --history_anchor requires auto, established or <sim>@<seconds>" >&2; exit 2; }
      HISTORY_ANCHOR="${1#*=}"; HISTORY_ANCHOR_SOURCE="flag"; shift ;;
    *)
      _normalized_args+=("$1"); shift ;;
  esac
done
set -- ${_normalized_args[@]+"${_normalized_args[@]}"}
unset _normalized_args

while getopts "w:h:u:a:e:p:i:" flag; do
  case "$flag" in
    w) WALLET_NAME="$OPTARG" ;;
    h) HOTKEY_NAME="$OPTARG" ;;
    u) NETUID="$OPTARG" ;;
    a) AXON_PORT="$OPTARG" ;;
    e) ENDPOINT="$OPTARG" ;;
    p) EXTRA+=(-p "$OPTARG") ;;
    i) PM2_NAME="$OPTARG" ;;
    *) exit 2 ;;
  esac
done

case "$HISTORY_ANCHOR" in
  auto|established) ;;
  *@*) ;;
  *) echo "ERROR: --history_anchor must be auto, established or <sim>@<seconds> (got ${HISTORY_ANCHOR})" >&2; exit 2 ;;
esac
[[ "$HISTORY_ANCHOR" != *"="* && "$HISTORY_ANCHOR" != *" "* ]] || { echo "ERROR: --history_anchor must not contain '=' or spaces" >&2; exit 2; }
case "$V7_CLASSES" in
  0|0,1) ;;
  *) echo "ERROR: V7_CLASSES must be 0 or 0,1 (got ${V7_CLASSES})" >&2; exit 2 ;;
esac
case "$V7_MAKER_CLASSES" in
  none|1|1,2) ;;
  *) echo "ERROR: V7_MAKER_CLASSES must be none, 1 or 1,2 (got ${V7_MAKER_CLASSES})" >&2; exit 2 ;;
esac
if [[ "$V7_CLASSES" == "0,1" && "$V7_MAKER_CLASSES" != "none" ]]; then
  echo "ERROR: V7_CLASSES=0,1 takes class 1, so V7_MAKER_CLASSES must be none (got ${V7_MAKER_CLASSES})." >&2
  exit 2
fi

# The endpoint defaults to testnet: refuse a netuid and an endpoint of different networks (as the v6 launcher does).
if [[ "$NETUID" == "79" && "$ENDPOINT" == *"test."* ]]; then
  echo "ERROR: netuid 79 is mainnet but the endpoint is testnet (${ENDPOINT})." >&2
  echo "       Pass -e wss://entrypoint-finney.opentensor.ai:443" >&2
  exit 2
fi
if [[ "$NETUID" != "79" && "$ENDPOINT" == *"entrypoint-finney"* ]]; then
  echo "ERROR: netuid ${NETUID} is not mainnet SN79 but the endpoint is mainnet (${ENDPOINT})." >&2
  exit 2
fi

[[ -f "$SCRIPT_DIR/run_miner_multi.sh" ]] || { echo "ERROR: run_miner_multi.sh missing" >&2; exit 1; }
[[ -f "$AGENT_PATH/TakerV7.py" ]] || { echo "ERROR: TakerV7.py missing in $AGENT_PATH" >&2; exit 1; }
[[ -f "$AGENT_PATH/taker_v7_core.py" ]] || { echo "ERROR: taker_v7_core.py missing in $AGENT_PATH" >&2; exit 1; }
[[ -f "$SCRIPT_DIR/tests/test_taker_v7.py" ]] || { echo "ERROR: tests/test_taker_v7.py missing" >&2; exit 1; }
[[ "$LOG_DIR" != *" "* && "$LOG_DIR" != *"="* ]] || { echo "ERROR: the log dir must not contain spaces or '='" >&2; exit 1; }

# v7 defaults (COPIED: l, clip, big from the mainnet class-0 winners; k 0.005 from the faster winners 174 / 223 / 20,
# Round 24 T, USER 11-13 (the code default stays 0.0035); c1_k, c1_l, c1_clip_notional from mainnet uid 68 = Round 22
# option A, used only with V7_CLASSES=0,1; the maker leg: mk_notional 1,800 and mk_guard_bp 10 COPIED, mk_band_clips,
# mk_expiry_s MODEL, mk_pull the per-step pull; STRUCTURAL: the pace guard, cap = cap_mult x miner_wealth with
# v7_cap=0).  lazy_load=1 and history_len=0 as the v6 launcher passes them.
PARAMS="lazy_load=1 history_len=0 \
v7_k=0.005 v7_l=0.55 v7_clip=7 v7_big=15 v7_classes=${V7_CLASSES} \
v7_c1_k=0.036 v7_c1_l=0.50 v7_c1_clip_notional=3500 \
v7_maker_classes=${V7_MAKER_CLASSES} v7_mk_notional=1800 v7_mk_guard_bp=10 v7_mk_band_clips=1 v7_mk_pull=1 v7_mk_expiry_s=60 \
v7_pace=1 v7_cap=0 v7_cap_mult=10 v7_fee_margin=0.001 \
v7_log_every=60 v7_gc_every=30 v7_log_dir=${LOG_DIR} v7_history_anchor=${HISTORY_ANCHOR}"

_sha16() { sha256sum "$1" | cut -c1-16; }
echo "[TakerV7] version=v7.2 TakerV7.py=$(_sha16 "$AGENT_PATH/TakerV7.py") taker_v7_core.py=$(_sha16 "$AGENT_PATH/taker_v7_core.py") test_taker_v7.py=$(_sha16 "$SCRIPT_DIR/tests/test_taker_v7.py")"

# ---- preflight: the v7 tests must pass -------------------------------------------------------------------------------
echo "[preflight] v7.2: ${PYTHON} tests/run_tests.py tests/test_taker_v7.py"
if ! V7_LAUNCH_PARAMS="$PARAMS" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" "$SCRIPT_DIR/tests/run_tests.py" tests/test_taker_v7.py; then
  echo "ERROR: v7.2 preflight FAILED: tests/test_taker_v7.py did not pass; nothing was launched." >&2
  exit 1
fi
echo "[preflight] v7.2 PASS"
if [[ "${V7_PREFLIGHT_ONLY:-0}" == "1" ]]; then
  exit 0
fi

echo "[TakerV7] pm2_name=$PM2_NAME netuid=$NETUID axon_port=$AXON_PORT endpoint=$ENDPOINT"
echo "[TakerV7] history_anchor=$HISTORY_ANCHOR (from $HISTORY_ANCHOR_SOURCE; logged only, no gate in v7)"
echo "[TakerV7] log_dir=$LOG_DIR"
echo "[TakerV7] params=$PARAMS"

# The agent directory and this tree first on the path, in the pm2 / miner process too.
export PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}"

exec "$SCRIPT_DIR/run_miner_multi.sh" \
  -i "$PM2_NAME" -e "$ENDPOINT" -w "$WALLET_NAME" -h "$HOTKEY_NAME" -u "$NETUID" -a "$AXON_PORT" \
  -g "$AGENT_PATH" -n TakerV7 -m "$PARAMS" ${EXTRA[@]+"${EXTRA[@]}"}
