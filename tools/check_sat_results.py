#!/usr/bin/env python3
"""check_sat_results.py - validate Task #40 saturation-calibration batch.

CSV columns are per-second rates (collect_all normalizes rotation-sampled
tile counters by elapsed time).  Split each 70 s window into idle
(t0..t0+4) and bench (t0+5..t0+64); per column, mean over the sampled
seconds in each window.  Report target counters + strong movers.
"""
import csv
import sys
from statistics import mean

FACES = ["p3", "p1", "p5", "p4", "p7", "p6"]
RUNS = ["1", "2", "3"]

TARGETS = {
    "p1": ["tile_a72_access", "tile_a72_read", "tile_hnf_requests",
           "tile_dir_hit", "tile_allocate", "tile_req_buf_empty"],
    "p3": ["tile_a72_access", "tile_mem_reads", "tile_mem_writes",
           "tile_mss_nocredit", "tile_poc_reads", "tile_poc_writes",
           "tile_poc_fail", "l3half0_total_emem_rd_req",
           "l3half1_total_emem_rd_req", "l3half0_total_emem_wr_req",
           "l3half1_total_emem_wr_req", "l3half0_misses", "l3half1_misses",
           "l3half0_evictions", "l3half1_evictions"],
    "p4": ["tile_a72_access", "tile_hnf_requests", "tile_dir_hit",
           "tile_allocate", "tile_victim", "tile_victim_write",
           "tile_poc_fail", "l3half0_hits", "l3half0_misses",
           "l3half1_hits", "l3half1_misses", "l3half0_allocations",
           "l3half1_allocations", "l3half0_evictions", "l3half1_evictions"],
    "p5": ["tile_a72_access", "tile_hnf_requests", "tile_dir_hit",
           "tile_allocate", "tile_victim", "tile_victim_write",
           "tile_poc_fail", "l3half0_hits", "l3half0_misses",
           "l3half1_hits", "l3half1_misses", "l3half0_allocations",
           "l3half1_allocations", "l3half0_evictions", "l3half1_evictions"],
    "p6": ["tile_io_access", "tile_io_reads", "tile_io_write",
           "tile_tso_write", "tile_rnf_requests", "tile_a72_access",
           "tile_mem_reads", "tile_mem_writes", "tilenet_cdn_req",
           "tilenet_ddn_req", "tilenet_ndn_req", "trio_dma_beats",
           "trio_rt_af", "trio_pbuf_af", "trio_wrq_empty",
           "pcie0_rx_bytes", "pcie0_tx_bytes", "pcie1_rx_bytes",
           "pcie1_tx_bytes", "net_rx_bytes", "net_tx_bytes",
           "l3half0_total_emem_rd_req", "l3half1_total_emem_rd_req"],
    "p7": ["tile_a72_access", "tile_a72_read", "tile_a72_write",
           "tile_hnf_requests", "tile_io_access", "tile_io_reads",
           "tile_io_write", "net_rx_bytes", "net_tx_bytes",
           "l3half0_total_wr_dbid_ack", "l3half1_total_wr_dbid_ack",
           "l3half0_misses", "l3half1_misses"],
}

SKIP = {"timestamp", "tile_group", "l3_group"}


def window_means(path):
    """Return (idle_means, bench_means): col -> mean per-second rate."""
    with open(path, newline="") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return {}, {}
    t0 = int(rows[0]["timestamp"])
    w = {"idle": [], "bench": []}
    for r in rows:
        t = int(r["timestamp"])
        if t < t0 + 5:
            w["idle"].append(r)
        elif t < t0 + 65:
            w["bench"].append(r)
    out = {}
    for name, chunk in w.items():
        cols = [c for c in chunk[0] if c not in SKIP]
        means = {}
        for c in cols:
            vals = [int(r[c]) for r in chunk if r[c] != ""]
            means[c] = mean(vals) if vals else 0.0
        out[name] = means
    return out["idle"], out["bench"]


def fmt_rate(v):
    if v >= 1e9:
        return f"{v/1e9:.2f}G/s"
    if v >= 1e6:
        return f"{v/1e6:.2f}M/s"
    if v >= 1e3:
        return f"{v/1e3:.1f}K/s"
    return f"{v:.1f}/s"


def main():
    d = sys.argv[1] if len(sys.argv) > 1 else "."
    for face in FACES:
        print(f"\n===== {face} =====")
        for run in RUNS:
            path = f"{d}/{face}_run{run}.csv"
            try:
                idle, bench = window_means(path)
            except FileNotFoundError:
                print(f"  run{run}: MISSING")
                continue
            print(f"  run{run}:")
            for c in TARGETS[face]:
                bi = bench.get(c, 0.0)
                ii = idle.get(c, 0.0)
                if bi == 0 and ii == 0:
                    print(f"    {c}: FLAT 0")
                elif bi > ii * 2 and bi > 100:
                    print(f"    {c}: idle {fmt_rate(ii)} -> bench "
                          f"{fmt_rate(bi)} (x{bi/ii:.1f}) <-- MOVES")
                elif bi <= ii:
                    print(f"    {c}: idle {fmt_rate(ii)} -> bench "
                          f"{fmt_rate(bi)} (x{bi/max(ii,1):.2f})")
                else:
                    print(f"    {c}: idle {fmt_rate(ii)} -> bench "
                          f"{fmt_rate(bi)} (x{bi/ii:.1f})")
            moved = [f"{c} {fmt_rate(bi)}" for c, bi in bench.items()
                     if c not in TARGETS[face] and bi > 100
                     and (idle.get(c, 0) == 0 or bi / idle.get(c, 0) > 5)]
            if moved:
                print(f"    other movers: {', '.join(moved[:8])}")


if __name__ == "__main__":
    main()
