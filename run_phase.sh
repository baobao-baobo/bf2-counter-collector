#!/bin/bash
# run_phase.sh - phase-based application run with collect_all.
#
# Runs collect_all over a fixed window and launches the application
# after a pre-idle phase, so the CSV contains idle/app/idle segments.
# A phase log is written next to the CSV for tools/split_path.py.
#
# usage (run from anywhere on the device; config/output/app paths
# resolve against the script's own directory):
#   sudo ./run_phase.sh -c CONFIG -o OUT.csv -a "APP CMD" [-p PRE] [-t APP] [-s POST] [-b BIND]
#
#   -c CONFIG   collect_all config (e.g. configs/app_full.conf)
#   -o OUT.csv  CSV output path (phase log: OUT.csv.phase.log)
#   -a APP CMD  application command (quoted); launched at app-phase start
#   -p PRE      idle seconds before the app (default 5)
#   -t APP      app window seconds (default 24); the app may finish early
#   -s POST     idle seconds after the app (default 5)
#   -b BIND     cpu list for taskset (e.g. 4-7); default: no pinning
#
# The app is expected to be a single foreground command.  If it is
# still running when its window expires, its whole process group is
# killed (TERM then KILL) so the post-idle segment stays clean -
# killing only the sh wrapper would orphan the app itself.  Actual
# phase boundaries are recorded in the log; split_path.py segments
# the CSV by those timestamps.
set -u

# Anchor everything to this script's directory so the working directory
# does not matter.
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
COLLECT="$SCRIPT_DIR/code/collect_all"

PRE=5; APP=24; POST=5; BIND=""
while getopts "c:o:a:p:t:s:b:h" opt; do
  case $opt in
    c) CONFIG=$OPTARG ;;
    o) OUT=$OPTARG ;;
    a) APPCMD=$OPTARG ;;
    p) PRE=$OPTARG ;;
    t) APP=$OPTARG ;;
    s) POST=$OPTARG ;;
    b) BIND=$OPTARG ;;
    h) echo "usage: sudo ./run_phase.sh -c CONFIG -o OUT.csv -a \"APP CMD\" [-p PRE] [-t APP] [-s POST] [-b BIND]"; exit 0 ;;
    *) exit 2 ;;
  esac
done
[ -n "${CONFIG:-}" ] && [ -n "${OUT:-}" ] && [ -n "${APPCMD:-}" ] || { echo "need -c -o -a"; exit 2; }

# Resolve relative -c/-o arguments against the script directory.
case "$CONFIG" in /*) ;; *) CONFIG="$SCRIPT_DIR/$CONFIG" ;; esac
case "$OUT" in /*) ;; *) OUT="$SCRIPT_DIR/$OUT" ;; esac

TOTAL=$((PRE + APP + POST))
LOG="$OUT.phase.log"

START=$(date +%s)
echo "[run_phase] collect_all: ${TOTAL}s window, output $OUT"
"$COLLECT" -c "$CONFIG" -o "$OUT" -d "$TOTAL" &
COL_PID=$!
sleep 1

sleep $((PRE - 1))
APP_START=$(date +%s)
echo "[run_phase] APP PHASE START $(date '+%F %T')"
# setsid: put the app in its own process group so the expiry kill can
# take the app down with the wrapper (not just orphan it)
if [ -n "$BIND" ]; then
  taskset -c "$BIND" setsid sh -c "$APPCMD" &
else
  setsid sh -c "$APPCMD" &
fi
APP_PID=$!

while kill -0 $APP_PID 2>/dev/null; do
  NOW=$(date +%s)
  if [ $((NOW - APP_START)) -ge "$APP" ]; then
    kill -TERM -- "-$APP_PID" 2>/dev/null
    sleep 1
    kill -KILL -- "-$APP_PID" 2>/dev/null
    echo "[run_phase] app window expired, killed app process group"
    break
  fi
  sleep 1
done
wait $APP_PID 2>/dev/null
APP_END=$(date +%s)

wait $COL_PID
END=$(date +%s)

{
  echo "start=$START"
  echo "app_start=$APP_START"
  echo "app_end=$APP_END"
  echo "end=$END"
} > "$LOG"
echo "[run_phase] done: pre-idle $((APP_START-START))s, app $((APP_END-APP_START))s, post-idle $((END-APP_END))s"
echo "[run_phase] phase log: $LOG"
