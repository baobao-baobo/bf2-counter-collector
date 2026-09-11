#!/usr/bin/env python3
"""split_path.py - phase-based path attribution for app experiments.

Reads collect_all CSVs (app_full.conf columns) together with the phase
logs written by run_phase.sh, subtracts the idle background, and
attributes each counter to a data path.  Outputs a per-counter table, a
per-path summary, and three figures (path profiles, L3 behavior,
conservation check).

Rotation columns (tile 6 s / L3 8 s groups) are NaN on off-windows;
each column's rate is averaged over the rows where it is non-NaN.  The
app window comes from the phase log (app_start..app_end); without a
log, the CSV trimmed by 5 rows at each end is used and no background
is subtracted.

usage:
  python tools/split_path.py --run app_run1.csv:xz --run app_run2.csv:xz \\
      --run app_run3.csv:xz --run redis_run1.csv:redis [--out DIR]

Runs sharing a label are combined with the median.
"""
import argparse
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

TRIM = 5  # rows dropped at each end when no phase log is available

# Counter roles -> app_full.conf columns.  Rates are per second.
ROLES = {
    "CR": ["tile_a72_access", "tile_a72_read", "tile_a72_write",
           "tile_rnf_requests", "tile_hnf_requests",
           "tile_memory_reads", "tile_memory_writes"],
    "IH": ["tile_io_access", "tile_io_reads", "tile_io_write",
           "tile_tso_write"],
    "IB": ["tile_memory_reads_bypass"],
    "WB": ["tile_victim_write"],
    "NAD": ["pcie0_rx_bytes", "pcie0_tx_bytes", "pcie1_rx_bytes",
            "pcie1_tx_bytes", "net_rx_bytes", "net_tx_bytes"],
    "BACKP": ["tile_req_buf_empty", "tile_mss_no_credit"],
}

# Derived row-wise combinations (NaN unless every member is sampled).
COMBINED = {
    "l3_hits": ["l3half0_hits", "l3half1_hits"],
    "l3_misses": ["l3half0_misses", "l3half1_misses"],
    "l3_allocations": ["l3half0_allocations", "l3half1_allocations"],
    "l3_evictions": ["l3half0_evictions", "l3half1_evictions"],
    "l3_rd_req_in": ["l3half0_total_rd_req_in", "l3half1_total_rd_req_in"],
    "pcie_total_bytes": ["pcie0_rx_bytes", "pcie0_tx_bytes",
                         "pcie1_rx_bytes", "pcie1_tx_bytes"],
}


def parse_phase_log(csv_path):
    """Return (start, app_start, app_end, end) or None."""
    log = csv_path + ".phase.log"
    if not os.path.exists(log):
        return None
    vals = {}
    for line in open(log, encoding="ascii"):
        line = line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            vals[k.strip()] = int(v)
    return (vals["start"], vals["app_start"], vals["app_end"], vals["end"])


def run_rates(csv_path):
    """Per-column net rate (app mean - idle mean) for one CSV run."""
    df = pd.read_csv(csv_path)
    cols = [c for c in df.columns
            if c not in ("timestamp", "tile_group", "l3_group")]
    log = parse_phase_log(csv_path)
    rates = {}
    if log is None:
        print(f"  {os.path.basename(csv_path)}: no phase log, "
              f"using trimmed window, no background subtraction")
        win = df.iloc[TRIM:-TRIM]
        for c in cols:
            s = pd.to_numeric(win[c], errors="coerce").dropna()
            rates[c] = float(s.mean()) if len(s) else np.nan
        return rates

    start, app_start, app_end, end = log
    ts = pd.to_numeric(df["timestamp"], errors="coerce")
    app = (ts >= app_start) & (ts < app_end)
    idle = ((ts >= start) & (ts < app_start)) | ((ts >= app_end) & (ts <= end))
    print(f"  {os.path.basename(csv_path)}: app {app.sum()}s, "
          f"idle {idle.sum()}s")
    for c in cols:
        s_app = pd.to_numeric(df.loc[app, c], errors="coerce").dropna()
        s_idle = pd.to_numeric(df.loc[idle, c], errors="coerce").dropna()
        if len(s_app) == 0:
            rates[c] = np.nan
            continue
        rates[c] = float(s_app.mean() - (s_idle.mean() if len(s_idle) else 0))
    return rates


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True,
                    help="CSV path:label, repeatable")
    ap.add_argument("--out", default=None,
                    help="output dir for tables and pngs "
                         "(default: dir of the first CSV)")
    args = ap.parse_args()

    groups = {}  # label -> list of per-column rate dicts
    order = []
    for spec in args.run:
        path, _, label = spec.rpartition(":")
        if not path or not label:
            sys.exit(f"--run expects CSV:LABEL, got {spec!r}")
        if label not in groups:
            groups[label] = []
            order.append(label)
        groups[label].append(path)

    outdir = args.out or os.path.dirname(
        os.path.abspath(args.run[0].split(":")[0]))
    os.makedirs(outdir, exist_ok=True)

    # Median per label.
    medians = {}
    for label in order:
        print(f"[{label}]")
        runs = [run_rates(p) for p in groups[label]]
        medians[label] = {}
        for c in runs[0]:
            vals = [r[c] for r in runs if not np.isnan(r[c])]
            medians[label][c] = float(np.median(vals)) if vals else np.nan

    all_cols = sorted({c for m in medians.values() for c in m})
    table = pd.DataFrame(medians, index=all_cols)
    table_path = os.path.join(outdir, "split_counters_table.csv")
    table.to_csv(table_path)
    print(f"saved {table_path}")

    # Derived combinations per label.
    for label, m in medians.items():
        for name, members in COMBINED.items():
            if all(member in m and not np.isnan(m[member])
                   for member in members):
                m[name] = sum(m[member] for member in members)

    # Path summary (one representative per path + L3 context).
    def g(label, key):
        return medians[label].get(key, np.nan)

    rows = []
    for label in order:
        rows.append({
            "app": label,
            "CR_a72_access": g(label, "tile_a72_access"),
            "CR_hnf_requests": g(label, "tile_hnf_requests"),
            "IH_io_access": g(label, "tile_io_access"),
            "IB_bypass_reads": g(label, "tile_memory_reads_bypass"),
            "WB_victim_write": g(label, "tile_victim_write"),
            "MSS_reads": g(label, "tile_memory_reads"),
            "MSS_writes": g(label, "tile_memory_writes"),
            "NAD_pcie_total": g(label, "pcie_total_bytes"),
            "NAD_net_rx": g(label, "net_rx_bytes"),
            "BACKP_mss_nocredit": g(label, "tile_mss_no_credit"),
            "L3_hits": g(label, "l3_hits"),
            "L3_misses": g(label, "l3_misses"),
            "L3_allocations": g(label, "l3_allocations"),
            "L3_evictions": g(label, "l3_evictions"),
            "L3_rd_req_in": g(label, "l3_rd_req_in"),
        })
    summary = pd.DataFrame(rows).set_index("app")
    summary_path = os.path.join(outdir, "split_path_summary.csv")
    summary.to_csv(summary_path)
    print(f"saved {summary_path}")
    print(summary.to_string(float_format=lambda v: f"{v:,.0f}"))

    # Figure 1: path profiles.  Events panel (CR/IH/IB/WB) and bytes
    # panel (NAD) side by side; cross-path units differ, keep them apart.
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4.2))
    xs = np.arange(len(order))
    events = [("CR\nA72_ACCESS", "tile_a72_access"),
              ("IH\nIO_ACCESS", "tile_io_access"),
              ("IB\nBYPASS", "tile_memory_reads_bypass"),
              ("WB\nVICTIM_WR", "tile_victim_write")]
    n = len(events)
    w = 0.8 / n
    for i, (name, key) in enumerate(events):
        vals = [g(label, key) for label in order]
        ax1.bar(xs + (i - n / 2 + 0.5) * w, vals, w, label=name.replace("\n", " "))
    ax1.set_xticks(xs)
    ax1.set_xticklabels(order)
    ax1.set_yscale("log")
    ax1.set_ylabel("events per second (log)")
    ax1.set_title("Mesh-side path ingress (CR/IH/IB/WB)")
    ax1.legend(fontsize=7)

    n2 = 2
    w2 = 0.8 / n2
    for i, (name, key) in enumerate([("Arm-side PCIe bytes", "pcie_total_bytes"),
                                     ("net rx", "net_rx_bytes")]):
        vals = [g(label, key) for label in order]
        ax2.bar(xs + (i - n2 / 2 + 0.5) * w2, vals, w2, label=name)
    ax2.set_xticks(xs)
    ax2.set_xticklabels(order)
    ax2.set_ylabel("bytes per second")
    ax2.set_title("NAD: PCIe / net (bytes)")
    ax2.legend(fontsize=7)

    fig.suptitle("Path traffic profiles across applications "
                 "(median of runs, idle background subtracted)")
    fig.tight_layout(rect=(0, 0, 1, 0.94))
    fig1 = os.path.join(outdir, "fig1_path_profiles.png")
    fig.savefig(fig1, dpi=150)
    print(f"saved {fig1}")

    # Figure 2: L3 behavior stack per app.
    fig2, ax = plt.subplots(figsize=(6.5, 4.2))
    stack = [
        ("HITS", "l3_hits", "#2E7D32"),
        ("MISSES", "l3_misses", "#C62828"),
        ("ALLOCATIONS", "l3_allocations", "#1565C0"),
        ("EVICTIONS", "l3_evictions", "#F9A825"),
    ]
    bottom = np.zeros(len(order))
    for name, key, color in stack:
        vals = [g(label, key) for label in order]
        ax.bar(xs, vals, 0.6, bottom=bottom, label=name, color=color)
        bottom += [v if not np.isnan(v) else 0 for v in vals]
    ax.set_xticks(xs)
    ax.set_xticklabels(order)
    ax.set_ylabel("L3 events per second")
    ax.set_title("L3 behavior per application")
    ax.legend(fontsize=8)
    fig2.tight_layout()
    fig2_png = os.path.join(outdir, "fig2_l3_behavior.png")
    fig2.savefig(fig2_png, dpi=150)
    print(f"saved {fig2_png}")

    # Figure 3: conservation check (reads in == hits + misses).
    fig3, ax = plt.subplots(figsize=(6.5, 4.2))
    w3 = 0.35
    for i, label in enumerate(order):
        rd_in = g(label, "l3_rd_req_in")
        hitmiss = g(label, "l3_hits") + g(label, "l3_misses")
        ax.bar(xs[i] - w3 / 2, rd_in, w3, label="RD_REQ_IN" if i == 0 else None,
               color="#4C72B0")
        ax.bar(xs[i] + w3 / 2, hitmiss, w3, label="HITS+MISSES" if i == 0 else None,
               color="#DD8452")
        if rd_in and not np.isnan(rd_in):
            ratio = hitmiss / rd_in
            ax.text(xs[i], max(rd_in, hitmiss), f"r={ratio:.2f}",
                    ha="center", va="bottom", fontsize=8)
    ax.set_xticks(xs)
    ax.set_xticklabels(order)
    ax.set_ylabel("L3 read requests per second")
    ax.set_title("Conservation check: L3 read ingress vs hits+misses")
    ax.legend(fontsize=8)
    fig3.tight_layout()
    fig3_png = os.path.join(outdir, "fig3_conservation.png")
    fig3.savefig(fig3_png, dpi=150)
    print(f"saved {fig3_png}")


if __name__ == "__main__":
    main()
