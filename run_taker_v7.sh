#!/usr/bin/env bash
# v7.7 TakerV7 launcher (agents/strategy/TakerV7.py + taker_v7_core.py): class 0 = the taker core (slope 0.005) with
# a second, contrarian term inside its target on the EVEN class-0 books and a flat two-sided maker on the ODD ones,
# whose quote offset is the Round 31 TRIAL (four groups of 16 books: the v7.6 rule / at the others' touch / one / two
# ticks behind it); class 1 = the maker leg (post-only quotes at the touch, band centred on a band target on the book's
# own move, Round 28 C1 K2) on EVERY class-1 book (the depth maker is off by default); class 2 = the class-relative
# band taker (Round 28 C2 B2a-w).
# Defaults: V7_CLASSES=0, V7_MAKER_CLASSES=1, V7_MK_TARGET=3, V7_REL_CLASSES=2, V7_REL_FORM=band, V7_C0_CONTRA=1,
# V7_C0_MAKER=1, V7_C0_MK_TRIAL=1, V7_MK_DEPTH=0.  v7.7 switch (Round 31): V7_C0_MK_TRIAL=0 quotes every class-0 maker
# book by the v7.6 rule.  (V7_C0_MK_TRIAL=0 V7_MK_DEPTH=1 runs exactly v7.6.)
# v7.6 switches (Round 29 C): V7_C0_MAKER=0 gives every class-0 book back to the taker; V7_MK_DEPTH=1 puts the
# depth-controlled two-sided maker back on the ODD class-1 books.  (V7_C0_MAKER=0 V7_MK_DEPTH=0 runs exactly v7.5.)
# v7.5 switches:  V7_REL_FORM=t9 is the two-term class-2 taker (Round 28 A2 T9: half-life 529.2, rate 0.0321, clip
# 930), =step the v7.4 class-2 stepping taker;  V7_MK_TARGET=2 is the v7.4 class-1 stepping target (half-life 1,058.4,
# limit 0.45, clip 1,800, guard 10 bp).  (V7_REL_FORM=step V7_MK_TARGET=2 runs exactly v7.4.)
# The v7.4 switches stay:  V7_REL_CLASSES=none turns the class-2 leg off;  V7_C0_CONTRA=0 is the v7.3 class-0 taker;
# V7_MK_TARGET=1 is the v7.3 level target of the maker leg, =0 the v7.2 flat band.
# (V7_REL_CLASSES=none V7_C0_CONTRA=0 V7_MK_TARGET=1 runs exactly v7.3.)
# V7_MAKER_CLASSES=1,2 is accepted only with V7_MK_TARGET=0 and V7_REL_CLASSES=none: a class is made OR stepped, and
# the target leg's fundamental guard would block every ask on class 2.
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
V7_MK_TARGET="${V7_MK_TARGET:-3}"
V7_REL_CLASSES="${V7_REL_CLASSES:-2}"
V7_REL_FORM="${V7_REL_FORM:-band}"
V7_C0_MAKER="${V7_C0_MAKER:-1}"
V7_C0_MK_TRIAL="${V7_C0_MK_TRIAL:-1}"
V7_MK_DEPTH="${V7_MK_DEPTH:-0}"
V7_C0_CONTRA="${V7_C0_CONTRA:-1}"
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
case "$V7_MK_TARGET" in
  0|1|2|3) ;;
  *) echo "ERROR: V7_MK_TARGET must be 0, 1, 2 or 3 (got ${V7_MK_TARGET})" >&2; exit 2 ;;
esac
case "$V7_REL_FORM" in
  step|band|t9) ;;
  *) echo "ERROR: V7_REL_FORM must be step, band or t9 (got ${V7_REL_FORM})" >&2; exit 2 ;;
esac
case "$V7_C0_MAKER" in
  0|1) ;;
  *) echo "ERROR: V7_C0_MAKER must be 0 or 1 (got ${V7_C0_MAKER})" >&2; exit 2 ;;
esac
case "$V7_C0_MK_TRIAL" in
  0|1) ;;
  *) echo "ERROR: V7_C0_MK_TRIAL must be 0 or 1 (got ${V7_C0_MK_TRIAL})" >&2; exit 2 ;;
esac
case "$V7_MK_DEPTH" in
  0|1) ;;
  *) echo "ERROR: V7_MK_DEPTH must be 0 or 1 (got ${V7_MK_DEPTH})" >&2; exit 2 ;;
esac
if [[ "$V7_MK_DEPTH" == "1" && "$V7_MAKER_CLASSES" == "none" ]]; then
  echo "ERROR: V7_MK_DEPTH=1 makes class-1 books, so V7_MAKER_CLASSES must include class 1 (or set V7_MK_DEPTH=0)." >&2
  exit 2
fi
case "$V7_REL_CLASSES" in
  none|2) ;;
  *) echo "ERROR: V7_REL_CLASSES must be none or 2 (got ${V7_REL_CLASSES})" >&2; exit 2 ;;
esac
case "$V7_C0_CONTRA" in
  0|1) ;;
  *) echo "ERROR: V7_C0_CONTRA must be 0 or 1 (got ${V7_C0_CONTRA})" >&2; exit 2 ;;
esac
if [[ "$V7_MK_TARGET" != "0" && "$V7_MAKER_CLASSES" == "1,2" ]]; then
  echo "ERROR: V7_MAKER_CLASSES=1,2 needs V7_MK_TARGET=0: the maker target leg runs on class 1 only (use V7_MAKER_CLASSES=1)." >&2
  exit 2
fi
if [[ "$V7_REL_CLASSES" == "2" && "$V7_MAKER_CLASSES" == "1,2" ]]; then
  echo "ERROR: V7_REL_CLASSES=2 steps class 2, so V7_MAKER_CLASSES must not make it (use V7_MAKER_CLASSES=1 or V7_REL_CLASSES=none)." >&2
  exit 2
fi
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
# mk_expiry_s MODEL, mk_pull the per-step pull; the v7.3 contrarian target: mk_k 0.005 W per bp and mk_l 0.45 W COPIED
# from the added-book winners (Round 25 S), mk_ref_s 10,800 = the scoring lookback STRUCTURAL; STRUCTURAL: the pace
# guard, cap = cap_mult x miner_wealth with v7_cap=0).  lazy_load=1 and history_len=0 as the v6 launcher passes them.
# v7.4 (Round 27 X0 / X1; "hour" = 529.2 sim-s as the tracks count it): mk_dyn_hl_s 1,058.4 (2 "hours") and
# mk_dyn_rate_h 0.03 W per "hour" COPIED from 5CFFZ7SE; rel_hl_s 2,116.8 (4 "hours"), rel_rate_h 0.0209 W per "hour",
# rel_l 0.45 W COPIED from the 5GCSdbyd fit, rel_clip_notional 1,800 the replayed clip; step_thr_bp 3 and
# step_max_dt_s 60 MODEL; c0_kc 0.005 and c0_lc 0.45 COPIED (the maker constants), c0_ref_s 10,800 STRUCTURAL.
# v7.5 (Round 28 C2 / C1): the values of each form are set here, so a switch is all a launch needs.
#   class 2 band (B2a-w): rel_hl_s 2,116.8 COPIED, rel_band_mult 2.0 MODEL, rel_l 0.45, clip 1,800 (as v7.4);
#   class 2 t9: rel_hl_s 529.2 (1 "hour"), rel_rate_h 0.0321 W per "hour", rel_mom 0.0037 W per bp, clip 930 COPIED
#     (Round 28 A2 fit of 5GCSdbyd);  class 2 step: the v7.4 values;
#   class 1 band (K2, V7_MK_TARGET=3): mk_dyn_hl_s 2,116.8, mk_l 0.70, mk_notional 1,500, mk_guard_bp 0 COPIED (Round 28
#     A1 from 5CAXJT4t), mk_band_mult 2.0 MODEL;  any other V7_MK_TARGET: the v7.4 values (1,058.4 / 0.45 / 1,800 / 10).
# v7.6 (Round 29 C): class-0 maker c0_mk_notional 1,000 and c0_mk_band_w 0.10 COPIED (uid 23 / uid 209); class-1 depth
#   maker (uses mk_notional and mk_guard_bp above) mkd_start_bp 2 and mkd_every_s 600 MODEL (Round 29 B).
# v7.7 (Round 31 B.3): the class-0 maker offset trial needs no value: its groups and offsets are STRUCTURAL (core).
case "$V7_REL_FORM" in
  t9) REL_HL=529.2; REL_RATE=0.0321; REL_CLIP=930 ;;
  *)  REL_HL=2116.8; REL_RATE=0.0209; REL_CLIP=1800 ;;
esac
if [[ "$V7_MK_TARGET" == "3" ]]; then
  MK_HL=2116.8; MK_L=0.70; MK_NOTIONAL=1500; MK_GUARD=0
else
  MK_HL=1058.4; MK_L=0.45; MK_NOTIONAL=1800; MK_GUARD=10
fi
PARAMS="lazy_load=1 history_len=0 \
v7_k=0.005 v7_l=0.55 v7_clip=7 v7_big=15 v7_classes=${V7_CLASSES} \
v7_c1_k=0.036 v7_c1_l=0.50 v7_c1_clip_notional=3500 \
v7_maker_classes=${V7_MAKER_CLASSES} v7_mk_notional=${MK_NOTIONAL} v7_mk_guard_bp=${MK_GUARD} v7_mk_band_clips=1 v7_mk_pull=1 v7_mk_expiry_s=60 \
v7_mk_target=${V7_MK_TARGET} v7_mk_k=0.005 v7_mk_l=${MK_L} v7_mk_ref_s=10800 \
v7_mk_dyn_hl_s=${MK_HL} v7_mk_dyn_rate_h=0.03 v7_mk_band_mult=2 \
v7_rel_classes=${V7_REL_CLASSES} v7_rel_form=${V7_REL_FORM} v7_rel_hl_s=${REL_HL} v7_rel_rate_h=${REL_RATE} v7_rel_l=0.45 \
v7_rel_clip_notional=${REL_CLIP} v7_rel_band_mult=2 v7_rel_mom=0.0037 \
v7_c0_maker=${V7_C0_MAKER} v7_c0_mk_notional=1000 v7_c0_mk_band_w=0.10 v7_c0_mk_trial=${V7_C0_MK_TRIAL} \
v7_mk_depth=${V7_MK_DEPTH} v7_mkd_start_bp=2 v7_mkd_every_s=600 \
v7_step_thr_bp=3 v7_step_max_dt_s=60 \
v7_c0_contra=${V7_C0_CONTRA} v7_c0_kc=0.005 v7_c0_lc=0.45 v7_c0_ref_s=10800 \
v7_pace=1 v7_cap=0 v7_cap_mult=10 v7_fee_margin=0.001 \
v7_log_every=60 v7_gc_every=30 v7_log_dir=${LOG_DIR} v7_history_anchor=${HISTORY_ANCHOR}"

_sha16() { sha256sum "$1" | cut -c1-16; }
echo "[TakerV7] version=v7.7 TakerV7.py=$(_sha16 "$AGENT_PATH/TakerV7.py") taker_v7_core.py=$(_sha16 "$AGENT_PATH/taker_v7_core.py") test_taker_v7.py=$(_sha16 "$SCRIPT_DIR/tests/test_taker_v7.py")"

# ---- preflight: the v7 tests must pass -------------------------------------------------------------------------------
echo "[preflight] v7.7: ${PYTHON} tests/run_tests.py tests/test_taker_v7.py"
if ! V7_LAUNCH_PARAMS="$PARAMS" PYTHONDONTWRITEBYTECODE=1 PYTHONPATH="$AGENT_PATH:$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
    "$PYTHON" "$SCRIPT_DIR/tests/run_tests.py" tests/test_taker_v7.py; then
  echo "ERROR: v7.7 preflight FAILED: tests/test_taker_v7.py did not pass; nothing was launched." >&2
  exit 1
fi
echo "[preflight] v7.7 PASS"
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
