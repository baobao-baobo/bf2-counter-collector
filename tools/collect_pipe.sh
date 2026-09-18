#!/bin/bash
# collect_pipe.sh - poll per-port ingress counters for eSwitch switchdev ports.
#
# Counting sources (P1 recheck, 2026-09-17):
#   - WIRE ports (uplink, e.g. p1): sysfs physical-port counters
#     (/sys/class/net/<p>/statistics/rx_*). OVS flow-table counters must NOT
#     be used here: after offload completes (~1.1s) the eSwitch switches
#     wire->host traffic in hardware and dump-flows stats stop growing
#     (only ~23% of a 10Gx5s flow was counted; 2026-09-17 p1 recheck).
#     sysfs rx_bytes was verified 2026-09-15 (Part B): 13.1GB/10s, 1:1 with
#     ethtool rx_bytes_phy and tc in_hw hardware counters.
#   - ARM-FACE ports (representors, e.g. pf1hpf, en3f1pf1sf0): OVS
#     table=0,priority=1,in_port=<p>,actions=NORMAL counting rules. Their
#     traffic passes through the kernel datapath, so rule n_bytes tracks it
#     1:1 (8.74GB/10s on 2026-09-15, 56.x test).
#
# Usage:  sudo ./collect_pipe.sh -d 50 -o pipe.csv [-p p1,pf1hpf,en3f1pf1sf0] [-w p1] [-b ovsbr1]
# Output: CSV, one row per second, cumulative counters:
#   ts,<port>_pkts,<port>_bytes,<port2>_pkts,<port2>_bytes,...
# Each row is also echoed to the terminal (tee), so the refresh cadence is
# visible while the run is in progress. OVS counting rules (Arm-face ports
# only) are removed on exit (trap).
#
# Note: ovs-ofctl add-flow with the same match REPLACES the rule and resets
# its counters. Do not re-add rules while a run is in progress.
# Note: dump-flows prints ports either by name (in_port="p1", quoted) or by
# number (in_port=9) depending on OVS build/config (observed both, 9/17 vs
# 9/18). The poll therefore sends the in_port match server-side and parses
# only n_packets/n_bytes, so both output forms work.

BRIDGE=ovsbr1
PORTS="p1,pf1hpf,en3f1pf1sf0"
WIRE_PORTS="p1"        # ports counted via sysfs physical-port counters
DUR=50
OUT=pipe.csv
POLL=1

usage() {
    echo "usage: $0 -d <sec> -o <csv> [-p p1,pf1hpf,en3f1pf1sf0] [-w p1] [-b <bridge>]" >&2
    exit 1
}

while getopts "d:o:p:w:b:h" opt; do
    case $opt in
        d) DUR=$OPTARG ;;
        o) OUT=$OPTARG ;;
        p) PORTS=$OPTARG ;;
        w) WIRE_PORTS=$OPTARG ;;
        b) BRIDGE=$OPTARG ;;
        h) usage ;;
        *) usage ;;
    esac
done

[ -z "$DUR" ] || [ -z "$OUT" ] && usage

IFS=',' read -ra PLIST <<< "$PORTS"
IFS=',' read -ra WLIST <<< "$WIRE_PORTS"

# Wire ports: fail fast if the sysfs counters are not readable.
for w in "${WLIST[@]}"; do
    [ -r "/sys/class/net/$w/statistics/rx_bytes" ] || {
        echo "no sysfs counters for wire port $w" >&2
        exit 1
    }
done

is_wire() {
    for w in "${WLIST[@]}"; do
        [ "$w" = "$1" ] && return 0
    done
    return 1
}

# Install OVS counting rules for non-wire (Arm-face) ports only.
# (Replaces any leftovers; counters start from zero.)
for p in "${PLIST[@]}"; do
    if is_wire "$p"; then
        continue
    fi
    ovs-ofctl add-flow "$BRIDGE" "table=0,priority=1,in_port=$p,actions=NORMAL" \
        || { echo "add-flow failed for $p" >&2; exit 1; }
done

cleanup() {
    for p in "${PLIST[@]}"; do
        if is_wire "$p"; then
            continue
        fi
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

echo "collect_pipe: $DUR s, ports: $PORTS, output: $OUT"

# Poll loop
start=$(date +%s)
while true; do
    now=$(date +%s)
    [ $((now - start)) -ge "$DUR" ] && break
    ts=$(date +%Y-%m-%dT%H:%M:%S)
    line="$ts"
    for p in "${PLIST[@]}"; do
        if is_wire "$p"; then
            pkts=$(cat "/sys/class/net/$p/statistics/rx_packets" 2>/dev/null)
            bytes=$(cat "/sys/class/net/$p/statistics/rx_bytes" 2>/dev/null)
        else
            stats=$(ovs-ofctl dump-flows "$BRIDGE" "in_port=$p" 2>/dev/null | head -1)
            pkts=$(echo "$stats" | grep -o 'n_packets=[0-9]*' | head -1 | cut -d= -f2)
            bytes=$(echo "$stats" | grep -o 'n_bytes=[0-9]*' | head -1 | cut -d= -f2)
        fi
        line="$line,${pkts:-0},${bytes:-0}"
    done
    echo "$line" | tee -a "$OUT"
    sleep "$POLL"
done

echo "done: $OUT"
