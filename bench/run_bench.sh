#!/bin/sh
# run_bench.sh - one benchmark run: collector + bench, synchronized
#
# usage: sudo ./run_bench.sh <bench_id> [run_no]
#   p0 = idle baseline (paper52.conf, no load)
#   p1 = stress-ng --cpu 8      (CPU compute pressure)
#   p3 = STREAM loop            (sequential memory bandwidth)
#   p4 = memrand -s 1024        (random access, 1GB working set)
#   p5 = stress-ng --cache 8    (cache thrash)
#   p6 = fio sequential read    (storage I/O / DMA path)
#   p7 = iperf3 client          (network; edit SERVER below first)
#
# Timing: collector runs 70 s (-d 70); bench starts 5 s later and runs
# 60 s; 5 s of tail room.  Trim first/last 5 rows in post-processing.
#
# Expected layout (copy bench/ to the device, e.g. /root/bench/):
#   collect_all   (device-compiled binary)
#   run_bench.sh
#   bin/          (arm64 static binaries)
#   configs/      (paper52.conf + bench_p*.conf)
#   results/      (created here: CSV + bench output)

BENCH=${1:-p0}
RUN=${2:-1}
COLLECT=./collect_all
IPERF_SERVER=192.168.100.1      # <-- edit to your iperf3 server IP
OUT="results/${BENCH}_run${RUN}.csv"
mkdir -p results

case "$BENCH" in
  p0) CONF=configs/paper52.conf
      CMD="" ;;
  p1) CONF=configs/bench_p1_cpu.conf
      CMD="bin/stress-ng --cpu 8 --timeout 60s > results/${BENCH}_run${RUN}_stressng.txt 2>&1" ;;
  p3) CONF=configs/bench_p3_stream.conf
      CMD="i=0; while [ \$i -lt 15 ]; do bin/stream >> results/${BENCH}_run${RUN}_stream.txt; i=\$((i+1)); done" ;;
  p4) CONF=configs/bench_p4_memrand.conf
      CMD="bin/memrand -s 1024 -b 64 -d 60 > results/${BENCH}_run${RUN}_memrand.txt 2>&1" ;;
  p5) CONF=configs/bench_p5_cache.conf
      CMD="bin/stress-ng --cache 8 --timeout 60s > results/${BENCH}_run${RUN}_stressng.txt 2>&1" ;;
  p6) CONF=configs/bench_p6_fio.conf
      # /tmp/fio.tmp: tmpfs.  If the device has real NVMe storage, point
      # --filename at it (e.g. /dev/nvme0n1) to exercise the PCIe/DMA path.
      CMD="bin/fio --name=t --filename=/tmp/fio.tmp --rw=read --bs=128k --size=4G --numjobs=4 --runtime=60 --time_based --direct=1 > results/${BENCH}_run${RUN}_fio.txt 2>&1" ;;
  p7) CONF=configs/bench_p7_net.conf
      CMD="bin/iperf3 -c $IPERF_SERVER -t 60 > results/${BENCH}_run${RUN}_iperf.txt 2>&1" ;;
  *) echo "unknown bench: $BENCH (use p0 p1 p3 p4 p5 p6 p7)"; exit 1 ;;
esac

# sanity: config must resolve before we start the 70 s window
$COLLECT -c "$CONF" --check-config > /dev/null || { echo "config check failed"; exit 1; }

echo "[run_bench] $BENCH run $RUN -> $OUT"
$COLLECT -c "$CONF" -d 70 -o "$OUT" &
CPID=$!
sleep 5
if [ -n "$CMD" ]; then
  echo "[run_bench] starting bench"
  sh -c "$CMD"
  echo "[run_bench] bench finished"
else
  echo "[run_bench] idle: sleeping 60 s"
  sleep 60
fi
wait $CPID
echo "[run_bench] collector done: $OUT"
