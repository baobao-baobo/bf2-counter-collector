#!/usr/bin/env python3
"""plot_v1_axis.py - v1 cross-bench figure: A72_ACCESS per second.

The engine writes per-window deltas (1 s window), so each CSV value is
already a per-second count.  For every run we trim the first/last 5
rows (warmup / tail) and average the remaining 60 s bench window; the
bench value is the median over its 3 runs.

Outputs (into the results dir by default):
  v1_axis_a72_access.png        - bars: x = bench, y = A72_ACCESS/s
  v1_axis_a72_access_series.png - per-second traces (stability check)

usage: python tools/plot_v1_axis.py [--dir bench/results] [--out DIR]
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

BENCHES = ["b1", "b2", "b3", "b4"]
LABELS = {
    "b1": "stress-ng\n--cpu 8",
    "b2": "STREAM",
    "b3": "memrand\n1GB",
    "b4": "stress-ng\n--cache 8",
}
TRIM = 5   # rows dropped at each end (warmup / tail)
COL = "tile_a72_access"


def load_run(path):
    df = pd.read_csv(path)
    if COL not in df.columns:
        sys.exit(f"{path}: column {COL} not found")
    win = df.iloc[TRIM:-TRIM]
    return win[COL].astype(float).values


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None,
                    help="results dir (default: ../bench/results)")
    ap.add_argument("--out", default=None,
                    help="output dir for pngs (default: results dir)")
    args = ap.parse_args()

    here = os.path.dirname(os.path.abspath(__file__))
    resdir = args.dir or os.path.join(here, "..", "bench", "results")
    outdir = args.out or resdir

    series = {}   # bench -> per-second median trace (60 s)
    summary = {}  # bench -> median of the 3 run means
    print(f"{'bench':<6}{'mean/s (median)':>18}   run means")
    for b in BENCHES:
        runs = []
        for r in (1, 2, 3):
            p = os.path.join(resdir, f"{b}_run{r}.csv")
            if not os.path.exists(p):
                sys.exit(f"missing {p}")
            runs.append(load_run(p))
        arr = np.vstack(runs)                    # 3 x 60
        series[b] = np.median(arr, axis=0)
        run_means = arr.mean(axis=1)
        summary[b] = np.median(run_means)
        print(f"{b:<6}{summary[b]:>18,.0f}   {[f'{v:,.0f}' for v in run_means]}")

    # Figure 1: bars (x = bench, y = A72_ACCESS per second)
    fig, ax = plt.subplots(figsize=(6, 4.2))
    xs = np.arange(len(BENCHES))
    vals = [summary[b] for b in BENCHES]
    ax.bar(xs, vals, width=0.6, color="#4C72B0")
    for x, v in zip(xs, vals):
        ax.text(x, v, f"{v:,.0f}", ha="center", va="bottom", fontsize=9)
    ax.set_xticks(xs)
    ax.set_xticklabels([LABELS[b] for b in BENCHES])
    ax.set_ylabel("A72_ACCESS per second")
    ax.set_xlabel("benchmark")
    ax.set_title("A72_ACCESS rate across benchmarks (median of 3 runs)")
    fig.tight_layout()
    bar_png = os.path.join(outdir, "v1_axis_a72_access.png")
    fig.savefig(bar_png, dpi=150)
    print(f"saved {bar_png}")

    # Figure 2: per-second traces (same data, shows run stability)
    fig2, ax2 = plt.subplots(figsize=(7, 4))
    for b in BENCHES:
        ax2.plot(series[b], label=LABELS[b].replace("\n", " "))
    ax2.set_xlabel("bench window second (60 s)")
    ax2.set_ylabel("A72_ACCESS per second")
    ax2.set_title("A72_ACCESS per-second trace (median of 3 runs)")
    ax2.legend(fontsize=8)
    ax2.grid(alpha=0.3)
    fig2.tight_layout()
    series_png = os.path.join(outdir, "v1_axis_a72_access_series.png")
    fig2.savefig(series_png, dpi=150)
    print(f"saved {series_png}")


if __name__ == "__main__":
    main()
