#!/usr/bin/env python3
# gen_fig_plts.py - write the per-counter gnuplot scripts for fig/.
#
# Visual style mirrors the paper bar charts (E:/.../image/*.plt) with
# the project's own sizing: clustered bars, Arial 16/22/20, opaque
# fill with black border, key outside top right (horizontal).
#
# Bars are drawn with `with boxes` instead of the histogram style:
# gnuplot 6's cluster histogram ignores an explicit `set boxwidth`
# and gives single-series bars a ~2 x-unit width, so the leftmost bar
# starts on the y axis.  With boxes we control the width (0.55 / 0.30
# / 0.22 per series count) and the cluster offsets
# (i - (N-1)/2) * boxwidth; xrange [0:8] centers the 7 clusters.
#
# Frame and ticks (2026-09-14 rule): full 4-side frame (border 15),
# ticks on the left y and bottom x axes only (nomirror, no top/right
# tics); log figures start at 1 with coarse decade tics only
# (1, 10, 100, ..., minor tics off).
# Colors (2026-10-08 rule, by series count): 1 series #B2172B;
# 2 series #B2172B / #F5A682; 3 series #82969D / #CC312D / #F7EDCA.
#
# Axis setup per figure:
#   - linear figures: y = value/1e6, ylabel "... (e+6)",
#     format %.0f, yrange [0:max*1.1 rounded to a nice step],
#     coarse integer tics (1/2/5 x 10^k step, <= ~10 tics)
#   - log figures (those with an IH series whose bars would be
#     invisible on a linear axis): y = raw counts, ylabel
#     "... (counts/s)", logscale y 10, format 10^{%L},
#     yrange [1 : 1/2/5 x 10^k above max] (starts at 10^0 = 1)
#
# One series per data path: dedicated path counters keep their single
# path, shared counters carry the M1 entry-ratio split computed by
# tools/path_data.py (keep SPECS in sync with FIG_SPECS there).
#
# Usage: python tools/gen_fig_plts.py [fig_dir]
import math
import os
import sys

FIG = sys.argv[1] if len(sys.argv) > 1 else "fig"

# (figure name, y axis label = the event the counter records,
#  series list, log-scale flag)
SPECS = [
    ("tile_a72_access",           "A72_ACCESS (e+6)",
     [("CR", "#B2172B")], False),
    ("tile_io_access",            "IO_ACCESS (counts/s)",
     [("IH", "#B2172B")], True),
    ("tile_memory_reads_bypass",  "MEMORY_READS_BYPASS (e+6)",
     [("IB", "#B2172B")], False),
    ("tile_victim_write",         "VICTIM_WRITE (e+6)",
     [("WB", "#B2172B")], False),
    ("tile_hnf_requests",         "HNF_REQUESTS (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("tile_mem_reads",            "MEMORY_READS (counts/s)",
     [("CR", "#82969D"), ("IH", "#CC312D"), ("IB", "#F7EDCA")], True),
    ("tile_mem_writes",           "MEMORY_WRITES (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_hits",                   "L3 HITS (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_misses",                 "L3 MISSES (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("pcie_total",                "PCIe bytes (e+6)",
     [("PCIe0", "#B2172B"), ("PCIe1", "#F5A682")], False),
    # Second batch (2026-09-14): remaining drawable counters.  Shared
    # (multi-path) counters come first with the M1 entry-ratio split and
    # the 2-series palette; single-series figures use the 1-series
    # color #B2172B (2026-10-08).  Figures with an IH series stay on
    # the log axis.
    ("tile_dir_hit",         "DIR_HIT (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("tile_allocate",        "ALLOCATE (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("tile_victim",          "VICTIM (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("tile_poc_writes",      "POC_WRITES (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("tile_req_buf_empty",   "REQ_BUF_EMPTY (idle - app, counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_allocations",       "L3 ALLOCATIONS (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_evictions",         "L3 EVICTIONS (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_total_rd_req_in",   "L3 TOTAL_RD_REQ_IN (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_total_wr_req_in",   "L3 TOTAL_WR_REQ_IN (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_total_cdn_req_in",  "L3 TOTAL_CDN_REQ_IN (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_total_ddn_req_in",  "L3 TOTAL_DDN_REQ_IN (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_total_emem_rd_req", "L3 TOTAL_EMEM_RD_REQ (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("l3_total_emem_wr_req", "L3 TOTAL_EMEM_WR_REQ (counts/s)",
     [("CR", "#B2172B"), ("IH", "#F5A682")], True),
    ("tile_a72_read",        "A72_READ (e+6)",
     [("CR", "#B2172B")], False),
    ("tile_io_reads",        "IO_READS (counts/s)",
     [("IH", "#B2172B")], True),
    ("tile_io_write",        "IO_WRITE (counts/s)",
     [("IH", "#B2172B")], True),
    ("tile_tso_write",       "TSO_WRITE (counts/s)",
     [("IH", "#B2172B")], True),
    ("net_traffic",          "Network bytes (e+6)",
     [("RX", "#B2172B"), ("TX", "#F5A682")], False),
]

# Bar width and cluster offsets per number of series (see header).
BOXWIDTH = {1: 0.55, 2: 0.30, 3: 0.22}


def x_expr(i, n):
    """x position of series i (0-based) in a cluster of n series."""
    bw = BOXWIDTH[n]
    off = (i - (n - 1) / 2.0) * bw
    if off == 0:
        return "($0+1)"
    return "($0+1%+.3f)" % off


def read_dat(name):
    """Return (app names, max value, smallest positive value)."""
    apps = []
    ymax = 0.0
    min_pos = float("inf")
    with open(os.path.join(FIG, name + ".dat")) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            apps.append(parts[0])
            for v in parts[1:]:
                try:
                    x = float(v)
                except ValueError:
                    continue
                ymax = max(ymax, x)
                if x > 0:
                    min_pos = min(min_pos, x)
    return apps, ymax, min_pos


def nice_lin(v):
    """Round v up to a tic-friendly step (0.5 / 5 / 50 / ...)."""
    step = 10 ** math.floor(math.log10(v)) / 2.0
    return math.ceil(v / step) * step


def lin_tics_step(yhi):
    """Coarse integer tic step for a linear (e+6) axis: the first
    1/2/5 x 10^k giving at most ~10 tics, so the %.0f labels stay
    unique (a 0.5 step on a small range would repeat labels)."""
    for k in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000):
        if yhi / k <= 10:
            return k
    return 10000


def yrange_for(name, log):
    """Return (yrange string, top value) for a figure."""
    apps, ymax, min_pos = read_dat(name)
    if not log:
        yhi = nice_lin(ymax / 1e6 * 1.1)
        return "[0:%g]" % yhi, yhi
    ylo = 1  # log axis starts at 10^0 = 1 (user rule 2026-09-14)
    exp = math.floor(math.log10(ymax * 1.05))
    for m in (1, 2, 5, 10):
        if m * 10 ** exp >= ymax * 1.05:
            yhi = m * 10 ** exp
            break
    return "[%g:%g]" % (ylo, yhi), yhi


def main():
    os.makedirs(FIG, exist_ok=True)
    for name, ylab, series, log in SPECS:
        n = len(series)
        apps, _, _ = read_dat(name)
        lines = []
        lines.append("# %s.plt - one counter, one bar per application per data path." % name)
        lines.append("#")
        lines.append("# Style mirrors the paper bar charts (image/*.plt): clustered")
        lines.append("# bars, Arial 16/22/20, opaque fill with black border, key")
        lines.append("# outside top right.  Bars use `with boxes` with explicit")
        lines.append("# width and offsets (see tools/gen_fig_plts.py); xrange")
        lines.append("# [0:8] centers the 7 application groups.  Full 4-side")
        lines.append("# frame (border 15), tics only on left y / bottom x")
        lines.append("# (nomirror).  Colors: 2-series figures use #B2172B")
        lines.append("# (left, dark) / #F5A682 (right, light); the 3-series")
        lines.append("# figure keeps dark gray / light gray / red.")
        lines.append("#")
        if log:
            lines.append("# Log y axis: raw counts/s, starts at 1, coarse decade")
            lines.append("# tics 1, 10, 100, ... only (10^{%L}, no minor tics);")
            lines.append("# the IH series would be invisible on a linear axis.")
        else:
            lines.append("# Linear y axis: values / 1e6 (see the using clause),")
            lines.append("# ylabel unit (e+6), yrange just above the data max,")
            lines.append("# coarse integer tics (1/2/5 x 10^k step).")
        lines.append("#")
        lines.append("# Data: fig/%s.dat (raw differenced rates)." % name)
        lines.append("#")
        lines.append("# Usage: gnuplot fig/%s.plt   (from the repo root)" % name)
        lines.append("")
        lines.append("set terminal pngcairo size 1200,600 enhanced font 'Arial,16'")
        lines.append("set output 'fig/%s.png'" % name)
        lines.append("")
        lines.append("set ylabel '%s' font 'Arial,22'" % ylab.replace("_", r"\_"))
        lines.append("set xlabel 'Applications' font 'Arial,22'")
        lines.append("")
        lines.append("set style fill solid border -1")
        lines.append("set boxwidth %.2f" % BOXWIDTH[n])
        lines.append("")
        yrange, yhi = yrange_for(name, log)
        lines.append("set yrange " + yrange)
        if log:
            lines.append("set logscale y 10")
            lines.append("unset mytics")
            lines.append("set format y '10^{%L}'")
            lines.append('set ytics add ("1" 1)')
        else:
            lines.append("set format y '%.0f'")
            lines.append("set ytics %d" % lin_tics_step(yhi))
        lines.append("")
        lines.append("set border 15")
        lines.append("set ytics nomirror")
        lines.append("set xtics nomirror")
        lines.append("")
        lines.append("# xtics anchored at the cluster centers (1..7); the app")
        lines.append("# names come from fig/%s.dat column 1." % name)
        tics = ", ".join("'%s' %d" % (a, i + 1) for i, a in enumerate(apps))
        lines.append("set xtics (%s) font ',20'" % tics)
        lines.append("set xrange [0:8]")
        lines.append("")
        lines.append("set tmargin 4")
        lines.append("set key outside top right horizontal font 'Arial,20'")
        lines.append("")
        for lbl, col in series:
            lines.append("C_%s = \"%s\"" % (lbl.replace("PCIe", "PCIE"), col))
        lines.append("")
        plot = "plot 'fig/%s.dat'" % name
        for i, (lbl, col) in enumerate(series):
            key = "C_%s" % lbl.replace("PCIe", "PCIE")
            if log:
                using = "using %s:%d" % (x_expr(i, n), i + 2)
            else:
                using = "using %s:($%d/1e6)" % (x_expr(i, n), i + 2)
            clause = using + " with boxes title '%s' lc rgb %s" % (lbl, key)
            if i == 0:
                plot += " " + clause
            else:
                plot += ", \\\n     '' " + clause
        lines.append(plot)
        lines.append("")
        with open(os.path.join(FIG, name + ".plt"), "w") as f:
            f.write("\n".join(lines))
        print("wrote", os.path.join(FIG, name + ".plt"))


if __name__ == "__main__":
    main()
