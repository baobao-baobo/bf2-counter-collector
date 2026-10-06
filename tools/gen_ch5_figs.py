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
    for c in ("tile_a72_access", "tile_io_access", "tile_memory_reads_bypass",
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


def write_dat(name, labels, rows):
    """rows = list of value lists (one per label)."""
    with open(os.path.join(FIGDIR, name + ".dat"), "w") as f:
        f.write("# %s: one row per round\n" % name)
        for lab, vals in zip(labels, rows):
            f.write(lab + " " + " ".join("%.6g" % v for v in vals) + "\n")


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

    print("figures generated: %d (dat + plt + png in fig/)" % len(made))


if __name__ == "__main__":
    main()
