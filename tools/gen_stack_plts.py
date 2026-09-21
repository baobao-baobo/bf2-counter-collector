#!/usr/bin/env python3
# gen_stack_plts.py - write the stacked-bar gnuplot scripts for fig/.
#
# Stacked figures (2026-09-14): one bar per application, the segments
# of a counter composition stacked bottom -> top, e.g. L3 HITS+MISSES
# or MEMORY_READS split into CR/IH/IB.  Layout and typography are the
# same as tools/gen_fig_plts.py (PathFinder-style application x
# composition bars): Arial 16/22/20, opaque fill with black segment
# borders, key outside top right (horizontal, bottom -> top segment
# left -> right), full 4-side frame (border 15), tics only on left y /
# bottom x (nomirror), xrange [0:8] with the app names anchored at
# 1..7.
#
# Stacking forces the linear axis (stacked segments on a log axis are
# not meaningful): values / 1e6, ylabel "(e+6)", format %.0f, yrange
# [0 : max total x 1.1 rounded to a nice step], coarse integer tics
# (1/2/5 x 10^k step, <= ~10 tics).  Segment colors use a palette of
# their own (user rule 2026-09-14): the stacked figures are
# composition charts, visually distinct from the side-by-side
# magnitude charts - light purple #C1A8E0 (bottom, darker) /
# light yellow #F7E6A0 (top, lighter).
#
# Bars are drawn with boxxyerror, whose xlow/xhigh/ylow/yhigh are
# ABSOLUTE coordinates (not offsets): x = ($0+1) +/- 0.275, ylow =
# cumulative of the segments below, yhigh = cumulative including this
# segment, so gnuplot never overdraws a full-height box the way
# successive `with boxes` clauses would; a zero-height segment
# (previous cumulative == current) is skipped via an undefined y
# (1/0).
#
# Data: fig/<name>.dat written by tools/path_data.py (STACK_SPECS) -
# column 1 = app, columns 2.. = segment values in that order.
#
# Usage: python tools/gen_stack_plts.py [fig_dir]
import math
import os
import sys

FIG = sys.argv[1] if len(sys.argv) > 1 else "fig"

# (figure name, y axis label, [(segment label, color)] bottom -> top).
# Keep in sync with STACK_SPECS in tools/path_data.py.
SPECS = [
    ("l3_lookups", "L3 HITS + MISSES (e+6)",
     [("HITS", "#C1A8E0"), ("MISSES", "#F7E6A0")]),
    ("l3_rd_chain", "L3 RD CHAIN: CACHE + EMEM (e+6)",
     [("CACHE_RD_RES_IN", "#C1A8E0"), ("EMEM_RD_REQ", "#F7E6A0")]),
    ("tile_mem_reads_stack", "MEMORY_READS (e+6)",
     [("VIA_HNF", "#C1A8E0"), ("BYPASS", "#F7E6A0")]),
]

# Total bar width in x units, split half/half around the bar center
# (matches the 0.55 single-series boxwidth of the side-by-side figs).
HALFBOX = 0.275


def read_dat(name):
    """Return (app names, max total over the 7 bars)."""
    apps = []
    ymax = 0.0
    with open(os.path.join(FIG, name + ".dat")) as f:
        for line in f:
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            apps.append(parts[0])
            total = sum(float(v) for v in parts[1:])
            ymax = max(ymax, total)
    return apps, ymax


def nice_lin(v):
    """Round v up to a tic-friendly step (0.5 / 5 / 50 / ...)."""
    step = 10 ** math.floor(math.log10(v)) / 2.0
    return math.ceil(v / step) * step


def lin_tics_step(yhi):
    """Coarse integer tic step for the linear (e+6) axis: the first
    1/2/5 x 10^k giving at most ~10 tics (see tools/gen_fig_plts.py)."""
    for k in (1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000):
        if yhi / k <= 10:
            return k
    return 10000


def main():
    os.makedirs(FIG, exist_ok=True)
    for name, ylab, segs in SPECS:
        apps, ymax = read_dat(name)
        yhi = nice_lin(ymax / 1e6 * 1.1)

        lines = []
        lines.append("# %s.plt - stacked composition, one bar per application." % name)
        lines.append("#")
        lines.append("# Style mirrors the paper bar charts (image/*.plt) via")
        lines.append("# tools/gen_fig_plts.py: Arial 16/22/20, opaque fill with")
        lines.append("# black segment borders, key outside top right, full")
        lines.append("# 4-side frame (border 15), tics only on left y / bottom")
        lines.append("# x (nomirror), xrange [0:8] centers the 7 applications.")
        lines.append("#")
        lines.append("# Stacked bars need the linear axis: values / 1e6,")
        lines.append("# ylabel (e+6), coarse integer tics (1/2/5 x 10^k).")
        lines.append("# Segments stack bottom -> top in the key order;")
        lines.append("# boxxyerror draws each segment from the cumulative sum")
        lines.append("# of the segments below up to the cumulative including")
        lines.append("# itself, so zero-height segments are skipped.")
        lines.append("#")
        lines.append("# Data: fig/%s.dat (tools/path_data.py STACK_SPECS)." % name)
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
        lines.append("")
        lines.append("set yrange [0:%g]" % yhi)
        lines.append("set format y '%.0f'")
        lines.append("set ytics %d" % lin_tics_step(yhi))
        lines.append("")
        lines.append("set border 15")
        lines.append("set ytics nomirror")
        lines.append("set xtics nomirror")
        lines.append("")
        tics = ", ".join("'%s' %d" % (a, i + 1) for i, a in enumerate(apps))
        lines.append("set xtics (%s) font ',20'" % tics)
        lines.append("set xrange [0:8]")
        lines.append("")
        lines.append("set tmargin 4")
        lines.append("set key outside top right horizontal font 'Arial,20'")
        lines.append("")
        for i, (lbl, col) in enumerate(segs):
            lines.append("C%d = \"%s\"   # %s" % (i, col, lbl))
        lines.append("")
        # Cumulative column expressions: prev = sum of the segments
        # below, cur = sum including this segment (columns 2..).
        plot = "plot 'fig/%s.dat'" % name
        prev = "0"
        for i, (lbl, col) in enumerate(segs):
            cur = "(" + "+".join("$%d" % (j + 2) for j in range(i + 1)) + ")"
            title = lbl.replace("_", r"\_")
            using = ("using ($0+1):((%s) > (%s) ? (%s)/1e6 : 1/0)"
                     ":(($0+1)-%.3f):(($0+1)+%.3f):(%s/1e6):(%s/1e6)"
                     " with boxxyerror"
                     % (cur, prev, cur, HALFBOX, HALFBOX, prev, cur))
            clause = using + " title '%s' lc rgb C%d" % (title, i)
            if i == 0:
                plot += " " + clause
            else:
                plot += ", \\\n     '' " + clause
            prev = cur
        lines.append(plot)
        lines.append("")
        with open(os.path.join(FIG, name + ".plt"), "w") as f:
            f.write("\n".join(lines))
        print("wrote", os.path.join(FIG, name + ".plt"))


if __name__ == "__main__":
    main()
