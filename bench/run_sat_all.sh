#!/bin/sh
# run_sat_all.sh - Task #40 saturation calibration, one-shot device side.
#
# usage (from /root/bf2k/bench):
#   sudo ./run_sat_all.sh               run all 18 runs, then gather
#   sudo ./run_sat_all.sh run           run only (no gather)
#   sudo ./run_sat_all.sh collect       gather only (also picks up the
#                                       sec.5/6 extras if present)
#   custom dir: sudo ./run_sat_all.sh run sat_out
#
# prereqs (one-time):
#   run_bench.sh edited: IPERF_SERVER=192.168.56.11,
#                        fio --filename=/root/fio.tmp
#   /root/fio.tmp precreated (dd if=/dev/zero of=/root/fio.tmp bs=1M count=4096)
#   p7 server running on fujian: iperf3 -s -D
#
# Sec.4 has no simultaneous dual-device step: fujian just idles as the
# p7 iperf3 server, so the whole sequence runs unattended here.
# Order: p3 x3 (memory) -> p1 x3 (cpu) -> p5 x3 (cache) ->
#        p4 x3 (memrand) -> p7 x3 (net) -> p6 x3 (eMMC), ~22 min.

cd "$(dirname "$0")" || exit 1

MODE=${1:-all}
OUTDIR=${2:-sat_results_$(date +%m%d_%H%M)}

BENCHES="p3 p1 p5 p4 p7 p6"
RUNS="1 2 3"

check_prereqs() {
  bad=0
  grep -q "192.168.56.11" run_bench.sh || { echo "WARN: run_bench.sh IPERF_SERVER != 192.168.56.11"; bad=$((bad+1)); }
  grep -q "/root/fio.tmp" run_bench.sh || { echo "WARN: fio --filename not /root/fio.tmp"; bad=$((bad+1)); }
  [ -f /root/fio.tmp ] || { echo "WARN: /root/fio.tmp not precreated"; bad=$((bad+1)); }
  [ -x ./collect_all ] || { echo "WARN: ./collect_all missing"; bad=$((bad+1)); }
  for t in stream fio iperf3 stress-ng memrand; do
    [ -x "bin/$t" ] || { echo "WARN: bin/$t not executable (chmod +x bin/*)"; bad=$((bad+1)); }
  done
  [ "$bad" -gt 0 ] && echo "prereq warnings: $bad (proceeding anyway)"
}

run_all() {
  [ "$(id -u)" = 0 ] || { echo "ERROR: run mode needs root (sudo ./run_sat_all.sh)"; exit 1; }
  check_prereqs
  echo "starting 18 runs; each is 70 s, do not interrupt"
  fail=0
  for b in $BENCHES; do
    for r in $RUNS; do
      echo "=== $b run $r ==="
      sudo ./run_bench.sh "$b" "$r" || {
        echo "ERROR: $b run $r failed (rc=$?)"; fail=$((fail+1)); continue; }
      txt=$(ls results/${b}_run${r}_*.txt 2>/dev/null | head -1)
      if [ -s "results/${b}_run${r}.csv" ] && [ -n "$txt" ] && [ -s "$txt" ]; then
        ls -l "results/${b}_run${r}.csv" "$txt"
      else
        echo "ERROR: missing or empty csv/txt for $b run $r"
        fail=$((fail+1))
      fi
    done
  done
  echo "runs finished, failures: $fail"
  return $fail
}

gather() {
  mkdir -p "$OUTDIR"
  n=0
  for b in $BENCHES; do
    for r in $RUNS; do
      for f in "results/${b}_run${r}.csv" results/${b}_run${r}_*.txt; do
        if [ -f "$f" ]; then cp -p "$f" "$OUTDIR/"; n=$((n+1)); fi
      done
    done
  done
  # sec.5/6 extras, if already run
  for f in results/e1_sat_rx.csv results/tilenet_*.csv results/tilenet_*.txt; do
    if [ -f "$f" ]; then cp -p "$f" "$OUTDIR/"; n=$((n+1)); fi
  done
  echo "gathered $n files into $OUTDIR"
  tar czf "$OUTDIR.tar.gz" "$OUTDIR" && echo "tarball: $OUTDIR.tar.gz"
  echo "completeness check (36 files from sec.4 expected; empty = complete):"
  for b in $BENCHES; do
    for r in $RUNS; do
      [ -f "$OUTDIR/${b}_run${r}.csv" ] || echo "  MISSING ${b}_run${r}.csv"
      ls "$OUTDIR"/${b}_run${r}_*.txt >/dev/null 2>&1 || echo "  MISSING ${b}_run${r}_*.txt"
    done
  done
}

case "$MODE" in
  run)     run_all ;;
  collect) gather ;;
  *)       run_all; rc=$?; gather; exit $rc ;;
esac
