#!/usr/bin/env python3
# gen_ch5_figs.py - chapter-5 case figures (verdict stacks + counters).
#
# Generates the case-by-case figure set for the chapter-5 experiments
# (ch5-cases-opsheet.md, batches 1-3 + case 7) from the canonical CSVs
# under results/ch5-batch1|2|3/ and results/case7/.
#
# Two figure families, both reusing the 9/14 plotting canon:
#
#  A) verdict figures (fig/ch5_<case>_verdict.png): one stacked bar
#     per round; segments = the 7 path pressures (cr/ih/ib/wb/nad/
#     nhd/tx) reported by tools/prism_search.py --scene on the
#     canonical CSV.  Linear axis in L_p units, boxxyerror cumulative
#     stack - the same mechanism as fig/l3_lookups.plt.
#     Path color map (extension of the established palette; the
#     single-path colors cr/ih/ib/wb stay as-is, nad/nhd reuse the
#     stacked purple/yellow pair, tx adds a muted blue):
#       cr  #4C4C4C   ih  #BABABA   ib  #B2172B   wb  #F5A682
#       nad #C1A8E0   nhd #F7E6A0   tx  #9FC5E8
#
#  B) counter figures (fig/ch5_<case>_<counter>.png): one clustered
#     bar per round per path, `with boxes` exactly like
#     tools/gen_fig_plts.py (same BOXWIDTH, x_expr offsets, fonts,
#     border 15 / nomirror, key outside top right).  Data = the
#     differenced per-counter rates (app mean - idle mean) computed
#     by path_data.col_rates on each round's CSV.  Dedicated counters
#     keep their single path color; shared counters carry the M1
#     entry-ratio split (A72_ACCESS : IO_ACCESS, same formulas as
#     tools/path_data.py: rd_cr/rd_ih/rd_ib for tile_mem_reads,
#     l3half0+l3half1 totals split for the L3 chain, tile_victim
#     split).  Figures with an IH series use the log axis (counts/s,
#     starts at 1, coarse decade tics); pure CR/IB/WB figures use the
#     linear e+6 axis - both per the established SPECS decisions.
#
#  C) transfer figures (fig/ch5_trans_*.png): the same application
#     under a parameter sweep, one line per path over the ordered
#     parameter axis (linespoints) - diverging/crossing lines render
#     the bottleneck handoff that the per-round bars of A/B cannot
#     show.  L_p lines reuse the verdict-cache vectors; raw-counter
#     companions (victim/io/emem/mem_reads) use col_rates
#     differentials (log axis where the sweep spans decades); the
#     case-5 knife-edge figure plots the batch-3 ops/s constants
#     (run2 vs run3 over the cache tiers).
#
#  D) per-row series figures (fig/ch5_series_*.png): L_p time series
#     over the app window for one representative round (PathFinder-
#     style dynamics), dumped by prism_search --series and smoothed
#     with a centered 6-row mean - each collector row carries fresh
#     deltas for exactly one tile group (tile_group cycles 0-5), so
#     a raw per-row L_p oscillates with period 6 by construction and
#     the 6-row mean (one full sampling cycle per point) restores
#     the window-mean semantics as a sliding window.
#
# Case 5 adds two throughput-share figures (fig/ch5_case5_*_share.png,
# one series, % of the per-instance throughput): the per-instance GUP/s
# and ops/s constants are the adjudicated batch-3 record from
# docs/batch3-results.md (S2.1 gups: 0.011/0.007/0.004/0.002 for
# 64M/256M/1G/2G tables; S2.6 db 4x cache run3: 3.14K/74.2K/92.1K/
# 95.0K ops/s for 16M/64M/256M/1G caches).
#
# x axis: one bar per round (label = round short name); xrange
# [0:N+1] centers N clusters, per the e1_nad_nhd convention.
# y axis linear: values / 1e6, ylabel "(e+6)", format %.0f, coarse
# 1/2/5 x 10^k tics (tools/gen_fig_plts.py helpers reused).
# y axis log: raw counts/s, starts at 1, format 10^{%L}, decade
# tics only (unset mytics, explicit "1" tic).
#
# Verdicts are cached in fig/.ch5_verdicts.tsv; pass --refresh to
# re-run prism_search on every round.
#
# Output: fig/ch5_*.dat/.plt/.png (fig/ stays out of git per the
# 待定夺入库 policy; this tool is committed).
#
# Usage: python tools/gen_ch5_figs.py [fig_dir] [--refresh]
import csv
import math
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import gen_fig_plts as gfp      # reuse nice_lin / lin_tics_step / x_expr
import path_data                # col_rates + the M1 split convention

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIG = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "fig"
FIGDIR = os.path.join(ROOT, FIG)
REFRESH = "--refresh" in sys.argv
TOOLS = os.path.dirname(os.path.abspath(__file__))

VERDICT_CACHE = os.path.join(FIGDIR, ".ch5_verdicts.tsv")

# Path -> color map for the verdict stacks (see module docstring).
PATH_COLORS = [
    ("cr", "#4C4C4C"), ("ih", "#BABABA"), ("ib", "#B2172B"), ("wb", "#F5A682"),
    ("nad", "#C1A8E0"), ("nhd", "#F7E6A0"), ("tx", "#9FC5E8"),
]
PATH_COLOR = dict(PATH_COLORS)

# gnuplot pngcairo filled point types, distinct per path, for the
# transfer-view linespoints figures.
PATH_PT = {"cr": 7, "ih": 5, "ib": 9, "wb": 11, "nad": 13, "nhd": 15,
           "tx": 1}
PT_SEQ = [7, 5, 9, 11, 13, 15, 1]   # assigned in series order to raw figs

# One case per batch; rounds = (short label, results-relative CSV).
CASES = [
    ("case1", "Case 1 - heterogeneous app signatures", [
        ("EP",     "ch5-batch1/ch5_c1a_ep_run1.csv"),
        ("IS",     "ch5-batch1/ch5_c1b_is_run1.csv"),
        ("FT",     "ch5-batch1/ch5_c1c_ft_run1.csv"),
        ("net-in", "ch5-batch1/ch5_c1d_netin_run1.csv"),
        ("db-M",   "ch5-batch1/ch5_c1e_dbmiss_run2.csv"),
        ("db-W",   "ch5-batch1/ch5_c1f_sbrndwr_run1.csv"),
    ]),
    ("case2", "Case 2 - traffic direction flip", [
        ("net-in",   "ch5-batch1/ch5_c1d_netin_run1.csv"),
        ("net-out",  "ch5-batch1/ch5_c2b_netout_run2.csv"),
        ("host-out", "ch5-batch1/ch5_c2c_host_run1.csv"),
    ]),
    ("case3", "Case 3 - working set transfer", [
        ("MG-S",     "ch5-batch2/ch5_c3a_mgs_run1.csv"),
        ("MG-B",     "ch5-batch2/ch5_c3b_mgb_run1.csv"),
        ("lat-1M",   "ch5-batch2/ch5_c3c_lat1m_run1.csv"),
        ("lat-256M", "ch5-batch2/ch5_c3d_lat256m_run1.csv"),
        ("db-2G",    "ch5-batch2/ch5_c3e_dbit_run1.csv"),
    ]),
    ("case4", "Case 4 - access mode transfer", [
        ("seq-rd",  "ch5-batch2/ch5_c4a_memseq_run1.csv"),
        ("rnd-rd",  "ch5-batch2/ch5_c4b_memrnd_run1.csv"),
        ("seq-wr",  "ch5-batch2/ch5_c4c_memwr_run1.csv"),
        ("fillseq", "ch5-batch2/ch5_c4d_fillseq_run1.csv"),
        ("fillrnd", "ch5-batch2/ch5_c4e_fillrnd_run1.csv"),
    ]),
    ("case5", "Case 5 - concurrent instance contention", [
        ("db-4x",   "ch5-batch3/ch5_c5a_db4x_run3.csv"),
        ("gups-4x", "ch5-batch3/ch5_c5b_gups4x_run1.csv"),
    ]),
    ("case6", "Case 6 - buffered vs direct switch", [
        ("TCP",      "ch5-batch3/ch5_c6a_socktcp_run1.csv"),
        ("UDP",      "ch5-batch3/ch5_c6b_sockudp_run3.csv"),
        ("buffered", "ch5-batch3/ch5_c6c_rndrd_buf_run1.csv"),
        ("direct",   "ch5-batch3/ch5_c6d_rndrd_direct_run1.csv"),
    ]),
    ("case7", "Case 7 - victim withdraw series", [
        ("base",      "case7/ch5_m1_victim_run1.csv"),
        ("mem-intf",  "case7/ch5_m2_memintf_run1.csv"),
        ("withdraw",  "case7/ch5_m3_withdraw_run1.csv"),
        ("flood",     "case7/ch5_m4_busytrans_run1.csv"),
        ("same-core", "case7/ch5_m5_samecore_run1.csv"),
    ]),
]

# Counter figures: (case, name, ylabel, series (label, color), log,
# value kind).  kind: None = direct column; "net" = (net_rx, net_tx)
# bytes; "pcie" = (pcie0, pcie1) bytes; "split" = (X_cr, X_ih) of the
# counter's M1 entry-ratio split; "reads" = (rd_cr, rd_ih, rd_ib).
COUNTER_FIGS = [
    ("case1", "tile_a72_access",          "A72_ACCESS (e+6)",
     [("CR", "#4C4C4C")], False, None),
    ("case1", "tile_io_access",           "IO_ACCESS (counts/s)",
     [("IH", "#BABABA")], True, None),
    ("case1", "tile_memory_reads_bypass", "MEMORY_READS_BYPASS (e+6)",
     [("IB", "#B2172B")], False, None),
    ("case1", "tile_victim_write",        "VICTIM_WRITE (e+6)",
     [("WB", "#F5A682")], False, None),
    ("case2", "net_bytes",                "Network bytes (e+6)",
     [("RX", "#B2172B"), ("TX", "#F5A682")], False, "net"),
    ("case2", "pcie_bytes",               "PCIe bytes (e+6)",
     [("PCIe0", "#B2172B"), ("PCIe1", "#F5A682")], False, "pcie"),
    ("case3", "tile_victim_write",        "VICTIM_WRITE (e+6)",
     [("WB", "#F5A682")], False, None),
    ("case3", "tile_a72_access",          "A72_ACCESS (e+6)",
     [("CR", "#4C4C4C")], False, None),
    ("case3", "l3_emem_wr_req",           "L3 TOTAL_EMEM_WR_REQ (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True, "split"),
    ("case4", "tile_memory_reads_bypass", "MEMORY_READS_BYPASS (e+6)",
     [("IB", "#B2172B")], False, None),
    ("case4", "l3_emem_wr_req",           "L3 TOTAL_EMEM_WR_REQ (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True, "split"),
    ("case4", "tile_victim",              "VICTIM (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True, "split"),
    ("case5", "tile_a72_access",          "A72_ACCESS (e+6)",
     [("CR", "#4C4C4C")], False, None),
    ("case6", "tile_mem_reads",           "MEMORY_READS (counts/s)",
     [("CR", "#4C4C4C"), ("IH", "#BABABA"), ("IB", "#B2172B")], True, "reads"),
    ("case6", "net_bytes",                "Network bytes (e+6)",
     [("RX", "#B2172B"), ("TX", "#F5A682")], False, "net"),
    ("case6", "tile_a72_access",          "A72_ACCESS (e+6)",
     [("CR", "#4C4C4C")], False, None),
    ("case7", "tile_a72_access",          "A72_ACCESS (e+6)",
     [("CR", "#4C4C4C")], False, None),
]

# Case 5 throughput-share constants (docs/batch3-results.md record).
SHARE_FIGS = [
    # (name, ylabel, x labels, per-instance throughputs)
    ("ch5_case5_c5b_share", "Throughput share (%)",
     ["64M", "256M", "1G", "2G"],
     [0.011, 0.007, 0.004, 0.002],   # GUP/s, gups 4x (S2.1)
     "gups per-instance GUP/s, tables 64M/256M/1G/2G (batch3 S2.1)"),
    ("ch5_case5_c5a_share", "Throughput share (%)",
     ["16M", "64M", "256M", "1G"],
     [3.14e3, 74.2e3, 92.1e3, 95.0e3],  # ops/s, db 4x cache run3 (S2.6)
     "db_bench per-instance ops/s, caches 16M/64M/256M/1G (batch3 S2.6)"),
]

# Family C: transfer views.  One line per path over the ordered
# parameter axis; pts = (case, tlabel) pairs into the verdict cache.
# L_p values = the adjudicated prism vectors (window-mean median).
TRANSFER_FIGS = [
    dict(name="ch5_trans_case1_ramp", xlab="NPB kernel",
         labels=["EP", "IS", "FT"],
         pts=[("case1", "EP"), ("case1", "IS"), ("case1", "FT")],
         paths=["cr", "ih", "ib", "wb"],
         note="case1 intensity ramp: kernel choice = memory-intensity "
              "parameter (cr 0.535 -> 1.879)"),
    dict(name="ch5_trans_case2_direction", xlab="Traffic direction",
         labels=["net-in", "net-out", "host-out"],
         pts=[("case2", "net-in"), ("case2", "net-out"),
              ("case2", "host-out")],
         paths=["nad", "ih", "nhd", "tx"],
         note="case2 direction flip: head handoff nad -> nhd/tx"),
    dict(name="ch5_trans_case3_mg", xlab="MG grid size",
         labels=["MG-S 32^3", "MG-B 256^3"],
         pts=[("case3", "MG-S"), ("case3", "MG-B")],
         paths=["cr", "ih", "ib", "wb"],
         note="case3 working-set transfer: MG grid 32^3 -> 256^3 "
              "(cr 0.512 -> 2.059)"),
    dict(name="ch5_trans_case3_lat", xlab="Pointer chain",
         labels=["lat-1M", "lat-256M"],
         pts=[("case3", "lat-1M"), ("case3", "lat-256M")],
         paths=["cr", "ih", "ib", "wb"],
         note="case3 latency-depth transfer: 1M -> 256M chain "
              "(cr 0.066 -> 0.750)"),
    dict(name="ch5_trans_case3_db", xlab="db_bench cache size",
         labels=["db-1G", "db-2G"],
         pts=[("case1", "db-M"), ("case3", "db-2G")],
         paths=["cr", "ih", "ib", "wb"],
         note="case3 storage->memory transfer: db_bench readrandom "
              "cache 1G -> 2G (cr 0.061 -> 0.385)"),
    dict(name="ch5_trans_case4_mode", xlab="sysbench access mode",
         labels=["seq-rd", "rnd-rd", "seq-wr"],
         pts=[("case4", "seq-rd"), ("case4", "rnd-rd"),
              ("case4", "seq-wr")],
         paths=["cr", "ih", "ib", "wb"],
         note="case4 access-mode transfer: seq-rd -> rnd-rd -> seq-wr"),
    dict(name="ch5_trans_case7_series", xlab="Interference phase",
         labels=["base", "mem-intf", "withdraw", "flood", "same-core"],
         pts=[("case7", l) for l in
              ("base", "mem-intf", "withdraw", "flood", "same-core")],
         paths=["cr", "ib", "nhd", "tx"],
         note="case7 victim series: interference injection/withdrawal "
              "(cr 0.365 -> 0.820 -> 0.355) + flood round"),
]

# Family C raw-counter companions: col_rates differentials over the
# parameter axis; log axis where the sweep spans decades.  series =
# (label, derived key, color).  kind "app" = adjudicated app-level
# constants (batch3 record) instead of counter differentials.
RAW_TRANSFER_FIGS = [
    dict(name="ch5_trans_case3_mg_victim", xlab="MG grid size",
         labels=["MG-S", "MG-B"],
         csvs=["ch5-batch2/ch5_c3a_mgs_run1.csv",
               "ch5-batch2/ch5_c3b_mgb_run1.csv"],
         series=[("WB", "tile_victim_write", "#F5A682")], log=True,
         ylab="VICTIM_WRITE differential (counts/s)",
         note="case3 eviction explosion: victim_write MG-S -> MG-B"),
    dict(name="ch5_trans_case3_db_io", xlab="db_bench cache size",
         labels=["db-1G", "db-2G"],
         csvs=["ch5-batch1/ch5_c1e_dbmiss_run2.csv",
               "ch5-batch2/ch5_c3e_dbit_run1.csv"],
         series=[("IH", "tile_io_write", "#BABABA")], log=True,
         ylab="IO_WRITE differential (counts/s)",
         note="case3 storage->memory flip: device-view eMMC writes, "
              "cache 1G -> 2G"),
    dict(name="ch5_trans_case4_ememwr", xlab="sysbench access mode",
         labels=["seq-rd", "rnd-rd", "seq-wr"],
         csvs=["ch5-batch2/ch5_c4a_memseq_run1.csv",
               "ch5-batch2/ch5_c4b_memrnd_run1.csv",
               "ch5-batch2/ch5_c4c_memwr_run1.csv"],
         series=[("CR", "l3_emem_wr_req", "#B2172B")], log=True,
         ylab="L3 EMEM_WR_REQ differential (counts/s)",
         note="case4 write-band transfer: emem writes across the modes"),
    dict(name="ch5_trans_case6_memreads", xlab="Read mechanism",
         labels=["buffered", "direct"],
         csvs=["ch5-batch3/ch5_c6c_rndrd_buf_run1.csv",
               "ch5-batch3/ch5_c6d_rndrd_direct_run1.csv"],
         series=[("CR", "rd_cr", "#4C4C4C"), ("IH", "rd_ih", "#BABABA"),
                 ("IB", "rd_ib", "#B2172B")], log=True,
         ylab="MEMORY_READS split differential (counts/s)",
         note="case6 mechanism switch: mem_reads cr/ih/ib buffered -> "
              "direct (477K/77K/757K -> 54K/8.7K/266K)"),
    dict(name="ch5_trans_case5_knife", xlab="db_bench cache size",
         labels=["16M", "64M", "256M", "1G"], kind="app", log=True,
         series=[("run2", "#B2172B"), ("run3", "#F5A682")],
         app_values=[[2.6e3, 9.6e3, 90.4e3, 98.4e3],
                     [3.14e3, 74.2e3, 92.1e3, 95.0e3]],
         ylab="ops/s",
         note="case5 knife edge: 64M cache flips eMMC->memory regime "
              "(run2 S2.2 2.6K/9.6K/90.4K/98.4K, run3 S2.6 3.14K/"
              "74.2K/92.1K/95.0K)"),
]

# Family D: per-row L_p series over the app window, one representative
# round per transfer story (PathFinder-style dynamics; smoothed by
# cycle_mean, see below).
SERIES_FIGS = [
    ("ch5_series_c3b_mgb", "case3", "MG-B",
     "ch5-batch2/ch5_c3b_mgb_run1.csv",
     "case3 MG-B: eviction-dominated round (cr med 2.059)"),
    ("ch5_series_c2b_netout", "case2", "net-out",
     "ch5-batch1/ch5_c2b_netout_run2.csv",
     "case2 net-out: 30s flood inside the 55s window (nad 1.566)"),
    ("ch5_series_m4_flood", "case7", "flood",
     "case7/ch5_m4_busytrans_run1.csv",
     "case7 flood: network interference on the victim (nhd/tx lit)"),
    ("ch5_series_c4c_memwr", "case4", "seq-wr",
     "ch5-batch2/ch5_c4c_memwr_run1.csv",
     "case4 seq-wr: dual-path round (cr 1.695 + ih 1.000)"),
]


def verdict(case, label, csv_rel):
    """7-path pressure vector for one round (prism_search, cached)."""
    cache = {}
    if not REFRESH and os.path.exists(VERDICT_CACHE):
        with open(VERDICT_CACHE) as f:
            for line in f:
                p = line.rstrip("\n").split("\t")
                if len(p) >= 10:
                    cache[(p[0], p[1])] = dict(zip(
                        ("csv", "cr", "ih", "ib", "wb", "nad", "nhd", "tx"),
                        (p[2],) + tuple(float(x) for x in p[3:10])))
    hit = cache.get((case, label))
    if hit and hit["csv"] == csv_rel:
        return hit
    out = subprocess.run(
        [sys.executable, os.path.join(TOOLS, "prism_search.py"),
         os.path.join("results", csv_rel), "--scene", label],
        cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace")
    # ranking pairs appear in descending-value order, not path order
    m = re.search(r"ranking:\s+(.*)", out.stdout)
    if not m:
        sys.exit("prism_search failed for %s\nstdout:\n%s\nstderr:\n%s"
                 % (csv_rel, out.stdout[-2000:], out.stderr[-2000:]))
    d = {}
    for tok in m.group(1).split():
        if "=" in tok:
            k, v = tok.split("=", 1)
            d[k] = float(v)
    missing = [k for k in ("cr", "ih", "ib", "wb", "nad", "nhd", "tx")
               if k not in d]
    if missing:
        sys.exit("prism ranking missing %s for %s\n%s"
                 % (missing, csv_rel, m.group(0)))
    with open(VERDICT_CACHE, "a") as f:
        f.write("\t".join([case, label, csv_rel] + ["%.3f" % d[k]
                for k in ("cr", "ih", "ib", "wb", "nad", "nhd", "tx")]) + "\n")
    print("prism: %-8s %-10s ranking: %s"
          % (case, label, " ".join("%s=%.3f" % (k, d[k]) for k in d)))
    return d


def derived(r):
    """M1 entry-ratio splits + byte series, path_data.py formulas."""
    m = {}
    for c in ("tile_a72_access", "tile_io_access", "tile_io_write",
              "tile_memory_reads_bypass",
              "tile_mem_reads", "tile_victim", "tile_victim_write",
              "l3half0_total_emem_wr_req", "l3half1_total_emem_wr_req",
              "net_rx_bytes", "net_tx_bytes", "pcie0_rx_bytes",
              "pcie0_tx_bytes", "pcie1_rx_bytes", "pcie1_tx_bytes"):
        m[c] = r.get(c) or 0.0
    entry = m["tile_a72_access"] + m["tile_io_access"]
    fr_cr = m["tile_a72_access"] / entry if entry > 0 else 0.0
    fr_ih = m["tile_io_access"] / entry if entry > 0 else 0.0
    ib = max(m["tile_memory_reads_bypass"], 0.0)
    rest = max(m["tile_mem_reads"] - ib, 0.0)
    m["rd_cr"] = rest * fr_cr
    m["rd_ih"] = rest * fr_ih
    m["rd_ib"] = ib
    emem_wr = (m["l3half0_total_emem_wr_req"] + m["l3half1_total_emem_wr_req"])
    m["l3_emem_wr_req"] = emem_wr
    m["l3_emem_wr_req_cr"] = emem_wr * fr_cr
    m["l3_emem_wr_req_ih"] = emem_wr * fr_ih
    m["tile_victim_cr"] = m["tile_victim"] * fr_cr
    m["tile_victim_ih"] = m["tile_victim"] * fr_ih
    m["net_rx"] = m["net_rx_bytes"]
    m["net_tx"] = m["net_tx_bytes"]
    m["pcie0"] = m["pcie0_rx_bytes"] + m["pcie0_tx_bytes"]
    m["pcie1"] = m["pcie1_rx_bytes"] + m["pcie1_tx_bytes"]
    return m


def series_values(kind, name, d):
    """Per-round values for the series of one counter figure."""
    if kind is None:
        return [d[name]]
    if kind == "net":
        return [d["net_rx"], d["net_tx"]]
    if kind == "pcie":
        return [d["pcie0"], d["pcie1"]]
    if kind == "split":
        return [d[name + "_cr"], d[name + "_ih"]]
    if kind == "reads":
        return [d["rd_cr"], d["rd_ih"], d["rd_ib"]]
    raise ValueError(kind)


def log_yrange(ymax):
    """(yrange string, top) - starts at 1, 1/2/5 x 10^k above max."""
    exp = math.floor(math.log10(ymax * 1.05))
    for m in (1, 2, 5, 10):
        if m * 10 ** exp >= ymax * 1.05:
            return "[1:%g]" % (m * 10 ** exp), m * 10 ** exp
    return "[1:%g]" % (10 ** (exp + 1)), 10 ** (exp + 1)


def small_tics(yhi):
    """Coarse tic step for small linear axes (L_p, share %)."""
    for k in (0.5, 1, 2, 5, 10, 20, 50):
        if yhi / k <= 8:
            return k
    return 100


def stacked_lines(name, ylab, labels, values, note, series):
    """plt source for a stacked (boxxyerror) figure, l3_lookups style."""
    n = len(series)
    yhi = gfp.nice_lin(max(v for row in values for v in row) * 1.1)
    l = []
    l.append("# %s.plt - %s." % (name, note))
    l.append("#")
    l.append("# Stacked bars via boxxyerror cumulative segments, the")
    l.append("# same mechanism as fig/l3_lookups.plt; zero-height")
    l.append("# segments are skipped.  Style: Arial 16/22/20, opaque")
    l.append("# fill, key outside top right horizontal, full 4-side")
    l.append("# frame (border 15), tics left y / bottom x only.")
    l.append("#")
    l.append("# Data: fig/%s.dat (one row per round)." % name)
    l.append("#")
    l.append("# Usage: gnuplot fig/%s.plt   (from the repo root)" % name)
    l.append("")
    l.append("set terminal pngcairo size 1200,600 enhanced font 'Arial,16'")
    l.append("set output 'fig/%s.png'" % name)
    l.append("")
    l.append("set ylabel '%s' font 'Arial,22'" % ylab.replace("_", r"\_"))
    l.append("set xlabel 'Rounds' font 'Arial,22'")
    l.append("")
    l.append("set style fill solid border -1")
    l.append("")
    l.append("set yrange [0:%g]" % yhi)
    l.append("set format y '%.1f'")
    l.append("set ytics %g" % small_tics(yhi))
    l.append("")
    l.append("set border 15")
    l.append("set ytics nomirror")
    l.append("set xtics nomirror")
    l.append("")
    tics = ", ".join("'%s' %d" % (a, i + 1) for i, a in enumerate(labels))
    l.append("set xtics (%s) font ',20'" % tics)
    l.append("set xrange [0:%d]" % (len(labels) + 1))
    l.append("")
    l.append("set tmargin 4")
    l.append("set key outside top right horizontal font 'Arial,20'")
    l.append("")
    for i, (lbl, col) in enumerate(series):
        l.append("C%d = \"%s\"   # %s" % (i, col, lbl))
    l.append("")
    # cumulative column sums S_1..S_n of the data columns 2..n+1
    acc = ["$%d" % (i + 2) + "".join("+$%d" % (j + 2) for j in range(i))
           for i in range(n)]
    plot = "plot 'fig/%s.dat'" % name
    for i, (lbl, col) in enumerate(series):
        lo = acc[i - 1] if i else "0"
        hi = acc[i]
        using = ("using ($0+1):((%s) > (%s) ? (%s) : 1/0):"
                 "(($0+1)-0.275):(($0+1)+0.275):(%s):(%s)"
                 % (hi, lo, hi, lo, hi))
        clause = (using + " with boxxyerror title '%s' lc rgb C%d"
                  % (lbl, i))
        if i == 0:
            plot += " " + clause
        else:
            plot += ", \\\n     '' " + clause
    l.append(plot)
    l.append("")
    return l


def cluster_lines(name, ylab, labels, values, log, series, scale=1e6):
    """plt source for a clustered (boxes) figure, gen_fig_plts style.

    scale = y-axis divisor (1e6 for counter figs, 1 for share %).
    """
    n = len(series)
    if log:
        ymax = max(v for row in values for v in row if v > 0) or 1.0
    else:
        ymax = max(v for row in values for v in row if v > 0) or 0.0
    l = []
    l.append("# %s.plt - one counter, one bar per round per data path." % name)
    l.append("#")
    l.append("# Clustered bars via `with boxes`, exact copy of the")
    l.append("# tools/gen_fig_plts.py template (BOXWIDTH/offsets, Arial")
    l.append("# 16/22/20, opaque fill, key outside top right, border")
    l.append("# 15, nomirror).  Differenced rates (app mean - idle")
    l.append("# mean) from path_data.col_rates on the canonical CSVs.")
    l.append("#")
    if log:
        l.append("# Log y axis: raw counts/s, starts at 1, decade tics")
        l.append("# only (10^{%L}, no minor tics).")
    else:
        l.append("# Linear y axis: values / 1e6, coarse integer tics.")
    l.append("#")
    l.append("# Data: fig/%s.dat" % name)
    l.append("#")
    l.append("# Usage: gnuplot fig/%s.plt   (from the repo root)" % name)
    l.append("")
    l.append("set terminal pngcairo size 1200,600 enhanced font 'Arial,16'")
    l.append("set output 'fig/%s.png'" % name)
    l.append("")
    l.append("set ylabel '%s' font 'Arial,22'" % ylab.replace("_", r"\_"))
    l.append("set xlabel 'Rounds' font 'Arial,22'")
    l.append("")
    l.append("set style fill solid border -1")
    l.append("set boxwidth %.2f" % gfp.BOXWIDTH[n])
    l.append("")
    if log:
        yr, yhi = log_yrange(ymax)
        l.append("set yrange " + yr)
        l.append("set logscale y 10")
        l.append("unset mytics")
        l.append("set format y '10^{%L}'")
        l.append('set ytics add ("1" 1)')
    else:
        yhi = gfp.nice_lin(ymax / scale * 1.1)
        l.append("set yrange [0:%g]" % yhi)
        l.append("set format y '%.0f'")
        l.append("set ytics %d" % gfp.lin_tics_step(yhi))
    l.append("")
    l.append("set border 15")
    l.append("set ytics nomirror")
    l.append("set xtics nomirror")
    l.append("")
    tics = ", ".join("'%s' %d" % (a, i + 1) for i, a in enumerate(labels))
    l.append("set xtics (%s) font ',20'" % tics)
    l.append("set xrange [0:%d]" % (len(labels) + 1))
    l.append("")
    l.append("set tmargin 4")
    l.append("set key outside top right horizontal font 'Arial,20'")
    l.append("")
    for lbl, col in series:
        l.append("C_%s = \"%s\"" % (lbl.replace("PCIe", "PCIE"), col))
    l.append("")
    plot = "plot 'fig/%s.dat'" % name
    for i, (lbl, col) in enumerate(series):
        key = "C_%s" % lbl.replace("PCIe", "PCIE")
        if log:
            using = "using %s:%d" % (gfp.x_expr(i, n), i + 2)
        else:
            using = "using %s:($%d/%g)" % (gfp.x_expr(i, n), i + 2, scale)
        clause = using + " with boxes title '%s' lc rgb %s" % (lbl, key)
        if i == 0:
            plot += " " + clause
        else:
            plot += ", \\\n     '' " + clause
    l.append(plot)
    l.append("")
    return l


def transfer_lines(name, ylab, xlab, labels, rows, series, note,
                   log=False):
    """plt source for a transfer (parameter-axis lines) figure.

    series = [(label, color, pointtype)].  PathFinder handoff
    rendering: ordered parameter on x, one linespoints series per
    path, so diverging/crossing lines show the bottleneck transfer.
    Style: the 9/14 canon (Arial 16/22/20, border 15 / nomirror,
    key outside top right horizontal).
    """
    if log:
        ymax = max(v for row in rows for v in row if v > 0) or 1.0
    else:
        ymax = max(v for row in rows for v in row if v > 0) or 0.0
    l = []
    l.append("# %s.plt - %s." % (name, note))
    l.append("#")
    l.append("# Transfer view: one line per path over the ordered")
    l.append("# parameter axis (linespoints), so the bottleneck")
    l.append("# handoff between parameter points shows as diverging/")
    l.append("# crossing lines instead of isolated bars.  Values =")
    l.append("# prism verdict vectors (fig/.ch5_verdicts.tsv) or")
    l.append("# col_rates differentials / batch-3 app constants.")
    l.append("# Style: Arial 16/22/20, border 15 / nomirror, key")
    l.append("# outside top right horizontal.")
    l.append("#")
    l.append("# Data: fig/%s.dat (one row per parameter point)." % name)
    l.append("#")
    l.append("# Usage: gnuplot fig/%s.plt   (from the repo root)" % name)
    l.append("")
    l.append("set terminal pngcairo size 1200,600 enhanced font 'Arial,16'")
    l.append("set output 'fig/%s.png'" % name)
    l.append("")
    l.append("set ylabel '%s' font 'Arial,22'" % ylab.replace("_", r"\_"))
    l.append("set xlabel '%s' font 'Arial,22'" % xlab)
    l.append("")
    if log:
        yr, yhi = log_yrange(ymax)
        l.append("set yrange " + yr)
        l.append("set logscale y 10")
        l.append("unset mytics")
        l.append("set format y '10^{%L}'")
        l.append('set ytics add ("1" 1)')
    else:
        yhi = gfp.nice_lin(ymax * 1.1)
        l.append("set yrange [0:%g]" % yhi)
        l.append("set format y '%.1f'")
        l.append("set ytics %g" % small_tics(yhi))
    l.append("")
    l.append("set border 15")
    l.append("set ytics nomirror")
    l.append("set xtics nomirror")
    l.append("")
    tics = ", ".join("'%s' %d" % (a, i + 1) for i, a in enumerate(labels))
    l.append("set xtics (%s) font ',20'" % tics)
    l.append("set xrange [0:%d]" % (len(labels) + 1))
    l.append("")
    l.append("set tmargin 4")
    l.append("set key outside top right horizontal font 'Arial,20'")
    l.append("")
    plot = "plot 'fig/%s.dat'" % name
    for i, (lbl, col, pt) in enumerate(series):
        # full keywords: gnuplot 6's "pt" abbreviation is ambiguous
        # between pointtype and pointinterval
        using = ("using 1:%d with linespoints pointtype %d "
                 "pointsize 1.3 linewidth 2" % (i + 2, pt))
        # gnuplot 6.0.4 parser bug: with using 1:N (N>=3) the sequence
        # "title '...' lc ..." fails with "duplicated or contradicting
        # arguments"; putting lc BEFORE title parses fine (verified by
        # bisection 2026-10-08).
        clause = using + " lc rgb \"%s\" title '%s'" % (col, lbl)
        if i == 0:
            plot += " " + clause
        else:
            plot += ", \\\n     '' " + clause
    l.append(plot)
    l.append("")
    return l


def series_rows(tsv_path):
    """Read a prism --series TSV -> list of 7-value rows (None =
    path absent from that row's lp dict)."""
    rows = []
    with open(tsv_path) as f:
        for line in f:
            if line.startswith("#"):
                continue
            p = line.rstrip("\n").split("\t")
            if len(p) < 2 or p[0] == "row":  # header line
                continue
            vals = [float(v) if v != "" else None for v in p[1:8]]
            rows.append(vals)
    return rows


def cycle_mean(rows, cycle=6):
    """Centered `cycle`-row mean: one full tile sampling cycle per
    point.  Each collector row carries fresh deltas for exactly one
    tile group (tile_group cycles 0-5), so a raw per-row L_p swings
    with period 6 by construction; the cycle mean restores the
    window-mean semantics as a sliding window.  Returns [(x, vals)]
    with x = center row of the window (1 row = 1 s)."""
    out = []
    for i in range(0, len(rows) - cycle + 1):
        win = rows[i:i + cycle]
        vals = []
        for k in range(7):
            xs = [w[k] for w in win if w[k] is not None]
            vals.append(sum(xs) / len(xs) if xs else None)
        out.append((i + cycle / 2.0 + 0.5, vals))
    return out


def write_series_dat(name, xs, rows):
    """rows = per-point value lists (missing = empty field)."""
    with open(os.path.join(FIGDIR, name + ".dat"), "w") as f:
        f.write("# %s: col 1 = cycle-mean center row, cols 2.. = paths\n"
                % name)
        for x, vals in zip(xs, rows):
            cells = ["%.4f" % v if v is not None else "" for v in vals]
            f.write("%.1f %s\n" % (x, " ".join(cells)))


def series_lines(name, xlab, npts, rows, series, note):
    """plt source for a per-row L_p series figure.  One line per path
    over the app window, cycle-mean smoothed (see cycle_mean); paths
    whose cycle-mean peak stays below 0.05 are dropped."""
    ymax = max(v for row in rows for v in row if v is not None)
    yhi = gfp.nice_lin(ymax * 1.1)
    l = []
    l.append("# %s.plt - %s." % (name, note))
    l.append("#")
    l.append("# Per-row L_p over the app window, one line per path.")
    l.append("# Each collector row carries fresh deltas for exactly")
    l.append("# one tile group (tile_group cycles 0-5), so raw per-row")
    l.append("# L_p oscillates with period 6 by construction; each")
    l.append("# point is a centered 6-row mean = one full sampling")
    l.append("# cycle, restoring the window-mean semantics as a")
    l.append("# sliding window.  Paths with a cycle-mean peak below")
    l.append("# 0.05 are dropped from the figure.  Style: Arial")
    l.append("# 16/22/20, border 15 / nomirror, key outside top right.")
    l.append("#")
    l.append("# Data: fig/%s.dat (col 1 = cycle-mean center row)." % name)
    l.append("#")
    l.append("# Usage: gnuplot fig/%s.plt   (from the repo root)" % name)
    l.append("")
    l.append("set terminal pngcairo size 1200,600 enhanced font 'Arial,16'")
    l.append("set output 'fig/%s.png'" % name)
    l.append("")
    l.append("set ylabel 'Bottleneck pressure L_p' font 'Arial,22'")
    l.append("set xlabel '%s' font 'Arial,22'" % xlab)
    l.append("")
    l.append("set yrange [0:%g]" % yhi)
    l.append("set format y '%.1f'")
    l.append("set ytics %g" % small_tics(yhi))
    l.append("")
    l.append("set border 15")
    l.append("set ytics nomirror")
    l.append("set xtics nomirror")
    l.append("set xrange [0:%d]" % (npts + 3))
    step = 10 if npts >= 30 else 5
    l.append("set xtics %d font ',20'" % step)
    l.append("")
    l.append("set tmargin 4")
    l.append("set key outside top right horizontal font 'Arial,20'")
    l.append("")
    plot = "plot 'fig/%s.dat'" % name
    for i, (lbl, col) in enumerate(series):
        clause = ("using 1:%d with lines lw 1.6 lc rgb \"%s\" title '%s'"
                  % (i + 2, col, lbl))
        if i == 0:
            plot += " " + clause
        else:
            plot += ", \\\n     '' " + clause
    l.append(plot)
    l.append("")
    return l


def write_dat(name, labels, rows):
    """rows = list of value lists (one per label)."""
    with open(os.path.join(FIGDIR, name + ".dat"), "w") as f:
        f.write("# %s: one row per round\n" % name)
        for lab, vals in zip(labels, rows):
            f.write(lab + " " + " ".join("%.6g" % v for v in vals) + "\n")


def write_dat_idx(name, rows):
    """rows = list of value lists; col 1 = numeric index (1..n) so the
    transfer-view linespoints plt can read numeric x (labels are mapped
    onto indices via set xtics)."""
    with open(os.path.join(FIGDIR, name + ".dat"), "w") as f:
        f.write("# %s: col 1 = parameter-point index (labels via xtics)\n"
                % name)
        for i, vals in enumerate(rows, 1):
            f.write("%d %s\n" % (i, " ".join("%.6g" % v for v in vals)))


def write_plt(name, lines):
    with open(os.path.join(FIGDIR, name + ".plt"), "w") as f:
        f.write("\n".join(lines) + "\n")


def run_gnuplot(name):
    subprocess.run(["gnuplot", os.path.join("fig", name + ".plt")],
                   cwd=ROOT, check=True)
    print("rendered fig/%s.png" % name)


def main():
    os.makedirs(FIGDIR, exist_ok=True)
    os.chdir(ROOT)
    made = []

    # ---- family A: verdict stacks + case counter figures ----------
    for case, case_note, rounds in CASES:
        labels = [lab for lab, _ in rounds]
        # verdict stack
        verdicts = [verdict(case, lab, csv_rel) for lab, csv_rel in rounds]
        name = "ch5_%s_verdict" % case
        rows = [[v[k] for k, _ in PATH_COLORS] for v in verdicts]
        write_dat(name, labels, rows)
        write_plt(name, stacked_lines(
            name, "Bottleneck pressure L_p", labels, rows,
            "%s - stacked 7-path pressures per round" % case_note,
            PATH_COLORS))
        run_gnuplot(name)
        made.append(name)
        # per-round derived counter data
        data = [derived(path_data.col_rates(os.path.join("results", c)))
                for _, c in rounds]
        for ccase, cname, ylab, series, log, kind in COUNTER_FIGS:
            if ccase != case:
                continue
            name = "ch5_%s_%s" % (case, cname)
            rows = [series_values(kind, cname, d) for d in data]
            write_dat(name, labels, rows)
            write_plt(name, cluster_lines(name, ylab, labels, rows, log,
                                          series))
            run_gnuplot(name)
            made.append(name)

    # ---- case 5 throughput-share figures ---------------------------
    for name, ylab, xlabs, thrus, note in SHARE_FIGS:
        tot = sum(thrus)
        rows = [[t / tot * 100.0] for t in thrus]
        write_dat(name, xlabs, rows)
        write_plt(name, cluster_lines(
            name, ylab, xlabs, rows, False, [("share", "#B2172B")],
            scale=1.0))
        run_gnuplot(name)
        made.append(name)

    # ---- family C: transfer views (parameter-axis lines) -----------
    csv_of = {}
    for case, _, rounds in CASES:
        for lab, csv_rel in rounds:
            csv_of[(case, lab)] = csv_rel
    for tf in TRANSFER_FIGS:
        rows = []
        for case, tl in tf["pts"]:
            v = verdict(case, tl, csv_of[(case, tl)])
            rows.append([v[p] for p in tf["paths"]])
        series = [(p, PATH_COLOR[p], PATH_PT[p]) for p in tf["paths"]]
        write_dat_idx(tf["name"], rows)
        write_plt(tf["name"], transfer_lines(
            tf["name"], "Bottleneck pressure L_p", tf["xlab"],
            tf["labels"], rows, series, tf["note"], False))
        run_gnuplot(tf["name"])
        made.append(tf["name"])
    for tf in RAW_TRANSFER_FIGS:
        if tf.get("kind") == "app":
            # per-label value lists = transpose of the per-series lists
            rows = [[vals[i] for vals in tf["app_values"]]
                    for i in range(len(tf["labels"]))]
            series = [(lbl, col, PT_SEQ[i])
                      for i, (lbl, col) in enumerate(tf["series"])]
        else:
            data = [derived(path_data.col_rates(os.path.join("results", c)))
                    for c in tf["csvs"]]
            rows = [[d[key] for _, key, _ in tf["series"]] for d in data]
            series = [(lbl, col, PT_SEQ[i])
                      for i, (lbl, key, col) in enumerate(tf["series"])]
        write_dat_idx(tf["name"], rows)
        write_plt(tf["name"], transfer_lines(
            tf["name"], tf["ylab"], tf["xlab"], tf["labels"], rows,
            series, tf["note"], tf.get("log", False)))
        run_gnuplot(tf["name"])
        made.append(tf["name"])

    # ---- family D: per-row L_p series (PathFinder-style dynamics) --
    for name, case, label, csv_rel, note in SERIES_FIGS:
        tsv = os.path.join(FIGDIR, name + "_rows.tsv")
        subprocess.run(
            [sys.executable, os.path.join(TOOLS, "prism_search.py"),
             os.path.join("results", csv_rel), "--scene", label,
             "--series", tsv],
            cwd=ROOT, check=True, capture_output=True, text=True,
            encoding="utf-8", errors="replace")
        raw = series_rows(tsv)
        if not raw:
            print("series %s: no app rows, skipping" % name)
            continue
        pts = cycle_mean(raw)
        keep = [k for k in range(7)
                if max((p[1][k] for p in pts if p[1][k] is not None),
                       default=0.0) > 0.05]
        if not keep:
            print("series %s: all paths quiet, skipping" % name)
            continue
        series = [(PATH_COLORS[k][0], PATH_COLORS[k][1]) for k in keep]
        xs = [p[0] for p in pts]
        rows = [[p[1][k] for k in keep] for p in pts]
        write_series_dat(name, xs, rows)
        write_plt(name, series_lines(
            name, "app-window row (6-row cycle-mean center; 1 row = 1 s)",
            len(pts), rows, series, note))
        run_gnuplot(name)
        made.append(name)

    print("figures generated: %d (dat + plt + png in fig/)" % len(made))


if __name__ == "__main__":
    main()
