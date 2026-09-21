#!/usr/bin/env python3
# path_data.py - differencing + path attribution for the G1-G7 app runs.
#
# Reads the phase-mode CSVs (results/g{1..7}_run{1..3}.csv + .phase.log),
# subtracts the idle background (per counter: app mean - idle mean over
# sampled rows; tile/L3 rotation columns are NaN on off-windows), applies
# traffic-based segmentation for the network groups (G3/G7: rows with
# net_rx >= NET_THRESH are benchmark rows; they are excluded from the
# idle background and define the NAD app window), takes the median over
# the 3 runs per group, and writes gnuplot-ready dat files:
#
#   fig1_path_events.dat  apps x {CR, IH, IB, WB} events/s
#   fig2_nad_bytes.dat    apps x {pcie_total, net_rx, net_tx} bytes/s
#   fig3_hnf_split.dat    apps x {hnf_cr, hnf_ih, hnf_total} events/s
#
# Path -> counter mapping (docs/apps-path-experiment-plan.md):
#   CR  tile_a72_access        (RN-F ingress)
#   IH  tile_io_access         (RN-I ingress)
#   IB  tile_memory_reads_bypass (MSS bypass reads)
#   WB  tile_victim_write      (dirty victim writebacks)
#   NAD pcie0/1 TLR bytes, net_rx/tx (E0-2 verified)
#
# Usage: python tools/path_data.py [results_dir] [out_dir]
import csv
import glob
import os
import re
import sys
from statistics import median

RES = sys.argv[1] if len(sys.argv) > 1 else "results"
OUT = sys.argv[2] if len(sys.argv) > 2 else RES

NET_THRESH = 1e6          # bytes/s: above this, the NIC is in benchmark
GROUPS = ["g1", "g2", "g3", "g4", "g5", "g6", "g7"]
APP_NAMES = {
    "g1": "xz", "g2": "BFS", "g3": "Redis", "g4": "SQLite",
    "g5": "BS", "g6": "TFLite", "g7": "Mix",
}

EVENTS = ["tile_a72_access", "tile_io_access",
          "tile_memory_reads_bypass", "tile_victim_write",
          "tile_hnf_requests", "tile_mem_reads",
          "tile_mem_writes", "tile_req_buf_empty",
          "tile_mss_nocredit"]
L3 = ["l3half0_hits", "l3half1_hits", "l3half0_misses", "l3half1_misses",
      "l3half0_allocations", "l3half1_allocations",
      "l3half0_evictions", "l3half1_evictions"]
BYTES = ["pcie0_rx_bytes", "pcie0_tx_bytes",
         "pcie1_rx_bytes", "pcie1_tx_bytes",
         "net_rx_bytes", "net_tx_bytes"]


def parse_phase(path):
    d = {}
    for line in open(path):
        m = re.match(r"(\w+)=(\d+)$", line.strip())
        if m:
            d[m.group(1)] = int(m.group(2))
    return d


def num(cell):
    try:
        return float(cell)
    except ValueError:
        return None


def col_rates(csvp):
    """Per-column (app mean - idle mean) for one CSV run.

    idle rows = quiet rows (net_rx < NET_THRESH) anywhere in the run;
    this drops benchmark-contaminated pre rows of G3 run1/2 and the
    traffic tail in post.  app rows for the event counters are the
    phase window; for the NAD counters they are the net-active rows
    (benchmark may start early / overrun the window).
    """
    ph = parse_phase(csvp + ".phase.log")
    with open(csvp, newline="") as f:
        rd = csv.reader(f)
        header = next(rd)
        rows = [r for r in rd if any(cell for cell in r)]
    idx = {n: i for i, n in enumerate(header)}
    net = idx["net_rx_bytes"]

    ts = [int(r[0]) for r in rows]
    netv = [num(r[net]) for r in rows]
    active = [n is not None and n >= NET_THRESH for n in netv]
    inapp = [ph["app_start"] < t <= ph["app_end"] for t in ts]

    rates = {}
    for c in header:
        if c in ("timestamp", "tile_group", "l3_group"):
            continue
        j = idx[c]
        v = [num(r[j]) for r in rows]
        if c in BYTES:
            ap = [x for x, a in zip(v, active) if a and x is not None]
        else:
            ap = [x for x, a in zip(v, inapp) if a and x is not None]
        idle = [x for x, a, n in zip(v, inapp, netv)
                if not a and (n is None or n < NET_THRESH) and x is not None]
        if not ap:
            rates[c] = None
            continue
        apm = sum(ap) / len(ap)
        idm = sum(idle) / len(idle) if idle else 0.0
        rates[c] = apm - idm
    return rates


def main():
    pairs = {}
    for g in GROUPS:
        pairs[g] = sorted(glob.glob(os.path.join(RES, g + "_run*.csv")))

    meds = {}
    for g in GROUPS:
        meds[g] = {}
        runs = [col_rates(p) for p in pairs[g]]
        for c in runs[0]:
            vals = [r[c] for r in runs if r[c] is not None]
            meds[g][c] = median(vals) if vals else 0.0

    def rate(grp, c):
        return meds[grp].get(c, 0.0)

    # Derived: L3 halves summed; PCIe total; HNF CR/IH entry-ratio split.
    for g in GROUPS:
        m = meds[g]
        m["l3_hits"] = rate(g, "l3half0_hits") + rate(g, "l3half1_hits")
        m["l3_misses"] = rate(g, "l3half0_misses") + rate(g, "l3half1_misses")
        m["l3_allocations"] = (rate(g, "l3half0_allocations")
                               + rate(g, "l3half1_allocations"))
        m["l3_evictions"] = (rate(g, "l3half0_evictions")
                             + rate(g, "l3half1_evictions"))
        m["pcie_total"] = (rate(g, "pcie0_rx_bytes") + rate(g, "pcie0_tx_bytes")
                           + rate(g, "pcie1_rx_bytes") + rate(g, "pcie1_tx_bytes"))
        entry = rate(g, "tile_a72_access") + rate(g, "tile_io_access")
        m["hnf_cr"] = rate(g, "tile_hnf_requests") * rate(g, "tile_a72_access") / entry \
            if entry > 0 else 0.0
        m["hnf_ih"] = rate(g, "tile_hnf_requests") * rate(g, "tile_io_access") / entry \
            if entry > 0 else 0.0
        # Entry-ratio fractions for attributing the shared counters to
        # paths (M1: ingress split by A72_ACCESS : IO_ACCESS).
        m["frac_cr"] = rate(g, "tile_a72_access") / entry if entry > 0 else 0.0
        m["frac_ih"] = rate(g, "tile_io_access") / entry if entry > 0 else 0.0
        # mem_reads: bypass reads are the IB subset, the rest splits CR/IH.
        ib = rate(g, "tile_memory_reads_bypass")
        rest = max(rate(g, "tile_mem_reads") - ib, 0.0)
        m["rd_cr"] = rest * m["frac_cr"]
        m["rd_ih"] = rest * m["frac_ih"]
        m["rd_ib"] = ib
        m["rd_via"] = m["rd_cr"] + m["rd_ih"]  # reads via the HNF
        m["wr_cr"] = rate(g, "tile_mem_writes") * m["frac_cr"]
        m["wr_ih"] = rate(g, "tile_mem_writes") * m["frac_ih"]
        for c in ("l3_hits", "l3_misses", "l3_allocations", "l3_evictions"):
            m[c + "_cr"] = m[c] * m["frac_cr"]
            m[c + "_ih"] = m[c] * m["frac_ih"]
        # Second batch (2026-09-14): remaining drawable counters.  Same
        # M1 entry-ratio attribution for the shared tile counters; the
        # L3 backbone chain counters are halves summed, then split.
        for c in ("tile_dir_hit", "tile_allocate", "tile_victim",
                  "tile_poc_writes"):
            m[c + "_cr"] = rate(g, c) * m["frac_cr"]
            m[c + "_ih"] = rate(g, c) * m["frac_ih"]
        # req_buf_empty counts empty-buffer cycles and goes negative
        # under load, so the figure shows idle - app (buffer kept busy).
        drop = max(0.0, -rate(g, "tile_req_buf_empty"))
        m["req_empty_drop_cr"] = drop * m["frac_cr"]
        m["req_empty_drop_ih"] = drop * m["frac_ih"]
        for c in ("total_rd_req_in", "total_wr_req_in", "total_cdn_req_in",
                  "total_ddn_req_in", "total_emem_rd_req",
                  "total_emem_wr_req"):
            tot = rate(g, "l3half0_" + c) + rate(g, "l3half1_" + c)
            m["l3_" + c + "_cr"] = tot * m["frac_cr"]
            m["l3_" + c + "_ih"] = tot * m["frac_ih"]
        # Stacked figures (2026-09-14): backbone totals for the read
        # chain composition stack (halves summed, no path split - the
        # stack shows how much of the read servicing came from the
        # cache vs. an external-memory fetch).
        m["l3_cache_rd_res_in"] = (rate(g, "l3half0_total_cache_rd_res_in")
                                   + rate(g, "l3half1_total_cache_rd_res_in"))
        m["l3_emem_rd_req"] = (rate(g, "l3half0_total_emem_rd_req")
                               + rate(g, "l3half1_total_emem_rd_req"))
        # NAD sub-links (E0-2; identity corrected 2026-09-14 after
        # checking with the lab): pcie0 = host-facing PCIe carrying
        # the 56.x host<->Arm pipe traffic, pcie1 = Arm-subsystem
        # PCIe delivery/reflection link.
        m["pcie0_bytes"] = rate(g, "pcie0_rx_bytes") + rate(g, "pcie0_tx_bytes")
        m["pcie1_bytes"] = rate(g, "pcie1_rx_bytes") + rate(g, "pcie1_tx_bytes")

    # Human-readable table for verification.
    print("%-9s %12s %12s %12s %12s %12s %12s %12s %12s %12s %12s"
          % ("app", "CR_a72", "IH_io", "IB_bypass", "WB_victim",
             "HNF_req", "MEM_rd", "MEM_wr", "nocredit", "L3_hit", "L3_miss"))
    for g in GROUPS:
        print("%-9s %12.0f %12.0f %12.0f %12.0f %12.0f %12.0f %12.0f "
              "%12.0f %12.0f %12.0f"
              % (APP_NAMES[g], rate(g, "tile_a72_access"),
                 rate(g, "tile_io_access"), rate(g, "tile_memory_reads_bypass"),
                 rate(g, "tile_victim_write"), rate(g, "tile_hnf_requests"),
                 rate(g, "tile_mem_reads"), rate(g, "tile_mem_writes"),
                 rate(g, "tile_mss_nocredit"), rate(g, "l3_hits"),
                 rate(g, "l3_misses")))
    print("%-9s %12s %12s %12s %12s %12s %12s"
          % ("app", "pcie_total", "net_rx", "net_tx", "L3_alloc",
             "L3_evict", "req_empty"))
    for g in GROUPS:
        print("%-9s %12.0f %12.0f %12.0f %12.0f %12.0f %12.0f"
              % (APP_NAMES[g], rate(g, "pcie_total"), rate(g, "net_rx_bytes"),
                 rate(g, "net_tx_bytes"), rate(g, "l3_allocations"),
                 rate(g, "l3_evictions"), rate(g, "tile_req_buf_empty")))

    # Per-counter figures (user rule 2026-09-14): one bar chart per
    # counter; series = per-path attributed values.  Dedicated path
    # counters keep their single path; shared counters are split with
    # the M1 entry ratio.  Raw differenced rates go to fig/<name>.dat,
    # scaling to (e+6) units is done in the plt using clause.
    FIG_SPECS = [
        ("tile_a72_access",           [("CR", "tile_a72_access")]),
        ("tile_io_access",            [("IH", "tile_io_access")]),
        ("tile_memory_reads_bypass",  [("IB", "tile_memory_reads_bypass")]),
        ("tile_victim_write",         [("WB", "tile_victim_write")]),
        ("tile_hnf_requests",         [("CR", "hnf_cr"), ("IH", "hnf_ih")]),
        ("tile_mem_reads",            [("CR", "rd_cr"), ("IH", "rd_ih"),
                                       ("IB", "rd_ib")]),
        ("tile_mem_writes",           [("CR", "wr_cr"), ("IH", "wr_ih")]),
        ("l3_hits",                   [("CR", "l3_hits_cr"),
                                       ("IH", "l3_hits_ih")]),
        ("l3_misses",                 [("CR", "l3_misses_cr"),
                                       ("IH", "l3_misses_ih")]),
        ("pcie_total",                [("PCIe0", "pcie0_bytes"),
                                       ("PCIe1", "pcie1_bytes")]),
        # Second batch (2026-09-14): remaining drawable counters.
        # Multi-path (shared, M1 split) first, single-path last.
        ("tile_dir_hit",             [("CR", "tile_dir_hit_cr"),
                                      ("IH", "tile_dir_hit_ih")]),
        ("tile_allocate",            [("CR", "tile_allocate_cr"),
                                      ("IH", "tile_allocate_ih")]),
        ("tile_victim",              [("CR", "tile_victim_cr"),
                                      ("IH", "tile_victim_ih")]),
        ("tile_poc_writes",          [("CR", "tile_poc_writes_cr"),
                                      ("IH", "tile_poc_writes_ih")]),
        ("tile_req_buf_empty",       [("CR", "req_empty_drop_cr"),
                                      ("IH", "req_empty_drop_ih")]),
        ("l3_allocations",           [("CR", "l3_allocations_cr"),
                                      ("IH", "l3_allocations_ih")]),
        ("l3_evictions",             [("CR", "l3_evictions_cr"),
                                      ("IH", "l3_evictions_ih")]),
        ("l3_total_rd_req_in",       [("CR", "l3_total_rd_req_in_cr"),
                                      ("IH", "l3_total_rd_req_in_ih")]),
        ("l3_total_wr_req_in",       [("CR", "l3_total_wr_req_in_cr"),
                                      ("IH", "l3_total_wr_req_in_ih")]),
        ("l3_total_cdn_req_in",      [("CR", "l3_total_cdn_req_in_cr"),
                                      ("IH", "l3_total_cdn_req_in_ih")]),
        ("l3_total_ddn_req_in",      [("CR", "l3_total_ddn_req_in_cr"),
                                      ("IH", "l3_total_ddn_req_in_ih")]),
        ("l3_total_emem_rd_req",     [("CR", "l3_total_emem_rd_req_cr"),
                                      ("IH", "l3_total_emem_rd_req_ih")]),
        ("l3_total_emem_wr_req",     [("CR", "l3_total_emem_wr_req_cr"),
                                      ("IH", "l3_total_emem_wr_req_ih")]),
        ("tile_a72_read",            [("CR", "tile_a72_read")]),
        ("tile_io_reads",            [("IH", "tile_io_reads")]),
        ("tile_io_write",            [("IH", "tile_io_write")]),
        ("tile_tso_write",           [("IH", "tile_tso_write")]),
        ("net_traffic",              [("RX", "net_rx_bytes"),
                                      ("TX", "net_tx_bytes")]),
    ]
    FIG_DIR = "fig"
    os.makedirs(FIG_DIR, exist_ok=True)
    for name, series in FIG_SPECS:
        p = os.path.join(FIG_DIR, name + ".dat")
        with open(p, "w") as f:
            f.write("# app " + " ".join(lbl for lbl, _ in series) + "\n")
            for g in GROUPS:
                f.write(APP_NAMES[g] + " "
                        + " ".join("%.0f" % max(rate(g, key), 0.0)
                                   for _, key in series) + "\n")
        print("saved", p)

    # Stacked figures (2026-09-14): one bar per application, the
    # segments of a composition stacked bottom -> top (linear axis
    # only).  Selection rationale: L3 hit/miss at both observation
    # points (bank HITS/MISSES and backbone CACHE_RD_RES_IN +
    # EMEM_RD_REQ), and the tile read path as via-HNF vs. bypass
    # (rd_via + rd_ib = MEMORY_READS exactly; the IH sliver is
    # sub-4% everywhere and invisible on a linear axis, so it stays
    # inside the via-HNF segment - see fig/tile_mem_reads.png for
    # the full 3-way log split).  L3 allocations/evictions stay OUT
    # of the stacks: they are sequential lifecycle events after a
    # miss (allocations ~= misses, 1.01-1.25x; evictions only
    # 11-33% of allocations in the 30-40s window), not parts of the
    # same composition - stacking them would double-count the misses.
    # Counters whose composition is just the M1 entry ratio again
    # (HNF_REQUESTS, MEMORY_WRITES) are left out - same ratio, no
    # new information.  Stack palette (user rule 2026-09-14): light
    # purple #C1A8E0 (bottom) / light yellow #F7E6A0 (top), visually
    # distinct from the side-by-side chart palette.  Rendering:
    # tools/gen_stack_plts.py.
    STACK_SPECS = [
        ("l3_lookups", [("HITS", "l3_hits", "#C1A8E0"),
                        ("MISSES", "l3_misses", "#F7E6A0")]),
        ("l3_rd_chain", [("CACHE_RD_RES_IN", "l3_cache_rd_res_in", "#C1A8E0"),
                         ("EMEM_RD_REQ", "l3_emem_rd_req", "#F7E6A0")]),
        ("tile_mem_reads_stack", [("VIA_HNF", "rd_via", "#C1A8E0"),
                                  ("BYPASS", "rd_ib", "#F7E6A0")]),
    ]
    for name, segs in STACK_SPECS:
        p = os.path.join(FIG_DIR, name + ".dat")
        with open(p, "w") as f:
            f.write("# app " + " ".join(lbl for lbl, _, _ in segs) + "\n")
            for g in GROUPS:
                f.write(APP_NAMES[g] + " "
                        + " ".join("%.0f" % max(rate(g, key), 0.0)
                                   for _, key, _ in segs) + "\n")
        print("saved", p)


if __name__ == "__main__":
    main()
