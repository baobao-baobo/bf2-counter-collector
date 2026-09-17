#!/bin/bash
# collect_pipe.sh - poll OVS flow-table counters for eSwitch ingress ports.
#
# The "pipe counting" rules (table=0,priority=1,in_port=<p>,actions=NORMAL)
# shadow the catch-all flow without changing forwarding semantics.
# Verified on device (2026-09-15): rule n_bytes tracks HW-offloaded traffic
# 1:1 against tc in_hw hardware counters and vport sysfs counters.
#
# Usage:  sudo ./collect_pipe.sh -d 50 -o pipe.csv [-p p1,pf1hpf,en3f1pf1sf0] [-b ovsbr1]
# Output: CSV, one row per second:
#   ts,<port>_pkts,<port>_bytes,<port2>_pkts,<port2>_bytes,...
# The counting rules are removed on exit (trap).
#
# Note: ovs-ofctl add-flow with the same match REPLACES the rule and resets
# its counters. Do not re-add rules while a run is in progress.
# Note: dump-flows prints port names quoted (in_port="p1"); the poll grep
# below accepts both quoted and unquoted forms.

BRIDGE=ovsbr1
PORTS="p1,pf1hpf,en3f1pf1sf0"
DUR=50
OUT=pipe.csv
POLL=1

usage() {
    echo "usage: $0 -d <sec> -o <csv> [-p p1,pf1hpf,en3f1pf1sf0] [-b <bridge>]" >&2
    exit 1
}

while getopts "d:o:p:b:h" opt; do
    case $opt in
        d) DUR=$OPTARG ;;
        o) OUT=$OPTARG ;;
        p) PORTS=$OPTARG ;;
        b) BRIDGE=$OPTARG ;;
        h) usage ;;
        *) usage ;;
    esac
done

[ -z "$DUR" ] || [ -z "$OUT" ] && usage

IFS=',' read -ra PLIST <<< "$PORTS"

# Install counting rules (replaces any leftovers; counters start from zero).
for p in "${PLIST[@]}"; do
    ovs-ofctl add-flow "$BRIDGE" "table=0,priority=1,in_port=$p,actions=NORMAL" \
        || { echo "add-flow failed for $p" >&2; exit 1; }
done

cleanup() {
    for p in "${PLIST[@]}"; do
        ovs-ofctl del-flows "$BRIDGE" "in_port=$p" 2>/dev/null
    done
}
trap cleanup EXIT

# CSV header
{
    printf "ts"
    for p in "${PLIST[@]}"; do
        printf ",%s_pkts,%s_bytes" "$p" "$p"
    done
    printf "\n"
} > "$OUT"

# Poll loop
start=$(date +%s)
while true; do
    now=$(date +%s)
    [ $((now - start)) -ge "$DUR" ] && break
    ts=$(date +%Y-%m-%dT%H:%M:%S)
    line="$ts"
    for p in "${PLIST[@]}"; do
        stats=$(ovs-ofctl dump-flows "$BRIDGE" 2>/dev/null | grep -E "in_port=\"?$p\"?([, ]|$)" | head -1)
        pkts=$(echo "$stats" | grep -o 'n_packets=[0-9]*' | head -1 | cut -d= -f2)
        bytes=$(echo "$stats" | grep -o 'n_bytes=[0-9]*' | head -1 | cut -d= -f2)
        line="$line,${pkts:-0},${bytes:-0}"
    done
    echo "$line" >> "$OUT"
    sleep "$POLL"
done

echo "done: $OUT"
