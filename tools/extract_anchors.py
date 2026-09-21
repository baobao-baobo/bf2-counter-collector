#!/usr/bin/env python3
"""extract_anchors.py - P1.5 anchor extraction for the BF2-PF model.

Subcommands:
  idle  pool pre-idle rows over all collected runs ->
        configs/anchor_idle.conf
  sat   saturation references (bench maxima + curated capacities) ->
        configs/anchor_sat.conf ([span] + [cap] + [unverified])

Idle sources WITH phase logs (idle row = outside the phase window
AND net_rx < NET_THRESH, same convention as tools/path_data.py):
  results/g{1..7}_run{1..3}.csv    21 runs
  results/e1_g3_run{1..3}.csv       3 runs
  results/e1_g7_run{1..3}.csv       3 runs
  results/e1_n2_run{1..3}.csv       3 runs

Idle sources WITHOUT phase logs (collector convention = 5 s pre +
5 s post; take the first/last 5 rows, still net-quiet filtered when
the run carries a net column):
  bench/results/b{1..4}_run{1..3}.csv  12 runs (fixed 4-counter group)
  message/e0_{1,2,3}_*.csv              3 runs

The e1_n0/e1_n1 calibration CSVs carry continuous load with unknown
timing and are EXCLUDED from idle pooling (they still feed obs-max).

Provenance marks used in anchor_sat.conf:
  bench-bN/pN stress-window mean of bench b1-b4 / p1-p7 (trimmed
              head/tail 5 rows, median of 3 runs) - true stress;
              kept only when it beats the obs-max floor (a bench
              that pushed a counter less hard than an app did is
              not a stress reference for it)
  obs-max     largest rate seen in any collected run: a LOWER
              BOUND on saturation, not saturation itself.  Wins
              whenever no bench face saturates the counter.
  derived     computed from other anchors (io_access = io_reads
              + io_write sats; sum-counter invariant)
  measured    established measured constant (Arm rx 6.6 Gbps, ...)
  suspected   nominal link spec, unconfirmed
"""

import csv
import os
import re
import statistics
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RES = os.path.join(ROOT, "results")
BENCH = os.path.join(ROOT, "bench", "results")
MSG = os.path.join(ROOT, "message")
CFG = os.path.join(ROOT, "configs")

NET_THRESH = 1e6          # bytes/s, shared with tools/path_data.py
SKIP_COLS = ("timestamp", "tile_group", "l3_group")
DEAD_SAT = 1000.0         # span sats below this (req/s) are dead counters

# (path, has_phase_log)
PHASE_SOURCES = []
for i in range(1, 8):
    for r in range(1, 4):
        PHASE_SOURCES.append((os.path.join(RES, "g%d_run%d.csv" % (i, r)), True))
for tag in ("e1_g3", "e1_g7", "e1_n2"):
    for r in range(1, 4):
        PHASE_SOURCES.append((os.path.join(RES, "%s_run%d.csv" % (tag, r)), True))

# (path, has_phase_log)
NOLOG_SOURCES = []
for i in range(1, 5):
    for r in range(1, 4):
        NOLOG_SOURCES.append((os.path.join(BENCH, "b%d_run%d.csv" % (i, r)), False))
for name in ("e0_1_nhd", "e0_2_nad", "e0_3_emmc"):
    NOLOG_SOURCES.append((os.path.join(MSG, name + ".csv"), False))

# calibration runs: continuous load, no phase log; sat/obs-max only
CAL_SOURCES = [os.path.join(RES, n) for n in (
    "e1_n0_2a.csv", "e1_n0_2b.csv",
    "e1_n1_1g.csv", "e1_n1_5g.csv", "e1_n1_10g.csv", "e1_n1_20g.csv")]


def num(cell):
    try:
        return float(cell)
    except ValueError:
        return None


def parse_phase(path):
    d = {}
    for line in open(path):
        m = re.match(r"(\w+)=(\d+)$", line.strip())
        if m:
            d[m.group(1)] = int(m.group(2))
    return d


def read_csv(path):
    with open(path, newline="") as f:
        rd = csv.reader(f)
        header = next(rd)
        rows = [r for r in rd if any(cell for cell in r)]
    idx = {n: i for i, n in enumerate(header)}
    return header, rows, idx


def col_iter(rows, idx, col):
    """Yield numeric values of col over rows (None cells skipped)."""
    if col not in idx:
        return []
    j = idx[col]
    out = []
    for r in rows:
        v = num(r[j]) if j < len(r) else None
        if v is not None:
            out.append(v)
    return out


def idle_rows(rows, idx, has_log, ph=None):
    """Return the list of row indexes that are idle."""
    if has_log:
        neti = idx.get("net_rx_bytes")
        out = []
        for i, t in enumerate(rows):
            if ph["app_start"] < int(t[0]) <= ph["app_end"]:
                continue
            if neti is not None:
                v = num(t[neti]) if neti < len(t) else None
                if v is not None and v >= NET_THRESH:
                    continue
            out.append(i)
        return out
    # no phase log: 5 s pre + 5 s post convention, net-quiet filtered
    # when a net column exists (E0 runs; bench runs carry none)
    n = len(rows)
    out = list(range(0, min(5, n))) + list(range(max(5, n - 5), n))
    neti = idx.get("net_rx_bytes")
    if neti is not None:
        out = [i for i in out if num(rows[i][neti]) is None
               or num(rows[i][neti]) < NET_THRESH]
    return out


def pool_idle():
    """Pool idle samples per column across all sources.

    Returns (sums, cnts) dicts keyed by column.
    """
    sums = {}
    cnts = {}
    for path, has_log in PHASE_SOURCES + NOLOG_SOURCES:
        logp = path + ".phase.log"
        if has_log and not os.path.exists(logp):
            continue
        header, rows, idx = read_csv(path)
        ph = parse_phase(logp) if has_log else None
        ir = idle_rows(rows, idx, has_log, ph)
        for c in header:
            if c in SKIP_COLS:
                continue
            for i in ir:
                j = idx[c]
                v = num(rows[i][j]) if j < len(rows[i]) else None
                if v is not None:
                    sums[c] = sums.get(c, 0.0) + v
                    cnts[c] = cnts.get(c, 0) + 1
    return sums, cnts


def cmd_idle():
    sums, cnts = pool_idle()
    with open(os.path.join(CFG, "anchor_idle.conf"), "w", newline="") as f:
        f.write("# anchor_idle.conf - P1.5 idle anchors (auto-generated\n")
        f.write("# by tools/extract_anchors.py idle).\n")
        f.write("#\n")
        f.write("# unit: per-second rate; pooled mean over pre-idle rows.\n")
        f.write("#   G/E1: outside [app_start, app_end] AND net_rx < 1e6 B/s\n")
        f.write("#   bench/E0: first/last 5 rows (5 s pre/post convention),\n")
        f.write("#   net-quiet filtered when a net column exists\n")
        f.write("#   e1_n0/e1_n1 calibration runs excluded (continuous load).\n")
        f.write("#\n")
        f.write("[idle]\n")
        for c in sorted(sums):
            f.write("%s = %.2f    # n=%d\n" % (c, sums[c] / cnts[c], cnts[c]))
    print("idle anchors written: configs/anchor_idle.conf")
    print("columns pooled: %d" % len(sums))
    thin = [c for c, n in cnts.items() if n < 30]
    print("thin samples (<30 rows, rotation-group limited): %s"
          % (", ".join(sorted(thin)) or "none"))


def bench_span():
    """Stress-window mean per (bench, counter), median over 3 runs.

    Reads b1-b4 (v1 axis) and, when present, p1-p7 (full bench,
    Part 6): p* CSVs appear automatically once the user copies them
    back into bench/results/.
    """
    out = {}
    for tag, imax in (("b", 4), ("p", 7)):
        for i in range(1, imax + 1):
            vals = {}
            for r in range(1, 4):
                path = os.path.join(BENCH, "%s%d_run%d.csv" % (tag, i, r))
                if not os.path.exists(path):
                    break
                header, rows, idx = read_csv(path)
                for c in header:
                    if c in SKIP_COLS:
                        continue
                    vs = col_iter(rows[5:-5], idx, c)
                    if vs:
                        vals.setdefault(c, []).append(statistics.mean(vs))
            for c, ms in vals.items():
                out.setdefault(c, []).append(
                    (statistics.median(ms), "bench-%s%d" % (tag, i)))
    return out


def obs_max():
    """Largest rate seen per column over all runs (app rows only).

    Returns (mx, seen): mx values only for columns that moved (>0);
    seen = every column encountered.
    """
    mx = {}
    seen = set()
    for path, has_log in PHASE_SOURCES + NOLOG_SOURCES:
        logp = path + ".phase.log"
        if has_log and not os.path.exists(logp):
            continue
        header, rows, idx = read_csv(path)
        ph = parse_phase(logp) if has_log else None
        ir = set(idle_rows(rows, idx, has_log, ph))
        for c in header:
            if c in SKIP_COLS:
                continue
            seen.add(c)
            j = idx[c]
            for i in range(len(rows)):
                if i in ir:
                    continue
                v = num(rows[i][j]) if j < len(rows[i]) else None
                if v is not None and v > mx.get(c, 0.0):
                    mx[c] = v
    for path in CAL_SOURCES:
        header, rows, idx = read_csv(path)
        for c in header:
            if c in SKIP_COLS:
                continue
            seen.add(c)
            for v in col_iter(rows, idx, c):
                if v > mx.get(c, 0.0):
                    mx[c] = v
    return mx, seen


def span_components():
    """Columns used by @span counters in path_table.conf."""
    cols = set()
    for line in open(os.path.join(CFG, "path_table.conf")):
        line = line.split("#")[0].strip()
        for tok in re.split(r"\s+", line):
            m = re.match(r"^[?]?(?:[\w]+:)?([\w+]+)@span", tok)
            if m:
                for part in m.group(1).split("+"):
                    part = re.sub(r"^[\w]+:", "", part)
                    if part:
                        cols.add(part)
    return cols


def write_sat(span, cap, unverified):
    with open(os.path.join(CFG, "anchor_sat.conf"), "w", newline="") as f:
        f.write("# anchor_sat.conf - P1.5 saturation references\n")
        f.write("# (auto-generated by tools/extract_anchors.py sat;\n")
        f.write("#  REVIEW before use).\n")
        f.write("#\n")
        f.write("# Provenance marks:\n")
        f.write("#   bench-bN/pN stress-window mean of bench b1-b4/p1-p7\n")
        f.write("#               (trimmed 5s head/tail, median of 3 runs) -\n")
        f.write("#               true stress; kept only when it beats the\n")
        f.write("#               obs-max floor (a bench that pushed a counter\n")
        f.write("#               less hard than an app did is not a stress\n")
        f.write("#               reference for it)\n")
        f.write("#   obs-max     largest rate seen in any collected run: a\n")
        f.write("#               LOWER BOUND on saturation, not saturation.\n")
        f.write("#               Wins whenever no bench face saturates the\n")
        f.write("#               counter.\n")
        f.write("#   derived     computed from other anchors (io_access =\n")
        f.write("#               io_reads + io_write sats; sum-counter\n")
        f.write("#               invariant, p7-verified)\n")
        f.write("#   measured    established measured constant\n")
        f.write("#   suspected   nominal link spec, unconfirmed\n")
        f.write("#\n")
        f.write("# [span] n = clamp((rate - idle) / (sat - idle), 0, 1)\n")
        f.write("# [cap]  n = clamp(rate / C, 0, 1)\n")
        f.write("# [unverified] counters that never moved in any collected\n")
        f.write("#   run (max == 0) or never exceeded their idle rate: kept\n")
        f.write("#   OUT of arbitration until the probe confirms they move\n")
        f.write("#   (counter-failure-probe-opsheet.md).\n")
        f.write("\n[span]\n")
        for c in sorted(span):
            f.write("%s = %.2f    # %s\n" % (c, span[c][0], span[c][1]))
        f.write("\n[cap]\n")
        for c in sorted(cap):
            f.write("%s = %.2f    # %s\n" % (c, cap[c][0], cap[c][1]))
        f.write("\n[unverified]\n")
        for c in sorted(unverified):
            f.write("%s = 0\n" % c)


def cmd_sat():
    span = {}
    for c, cands in bench_span().items():
        # keep the best (largest) stress reference per counter
        best = max(cands, key=lambda t: t[0])
        span[c] = (best[0], best[1] + " stress")
    # obs-max floor: a sat must be at least as large as any observed
    # rate.  A bench that pushed a counter less hard than some
    # collected app did is not a stress reference for it (weak face
    # like p1, or a face whose config does not sample the counter) -
    # keep the observed max as the lower bound instead of letting a
    # weak bench shadow it (Part 6 regression found 2026-09-20).
    om, seen = obs_max()
    wanted = span_components()
    for c, v in om.items():
        if c in wanted and v >= DEAD_SAT:
            cur = span.get(c)
            if cur is None or v > cur[0] * 1.02:
                span[c] = (v, "obs-max provisional")
    # tile_io_access counts total io accesses; reads and write are
    # its components (verified on the p7 face: access == reads+write
    # to within 0.1%).  A sum counter's sat is the sum of its
    # components' sats, keeping access n = (r+w)/(R+W) in [0, 1].
    if all(c in span for c in ("tile_io_access", "tile_io_reads",
                               "tile_io_write")):
        rs = span["tile_io_reads"][0]
        ws = span["tile_io_write"][0]
        span["tile_io_access"] = (
            rs + ws,
            "derived: reads+write sats (access=reads+write, p7-verified)")
    # span sats below the dead threshold are excluded (gap)
    span = {c: v for c, v in span.items() if c in wanted and v[0] >= DEAD_SAT}
    # curated capacity table for the @cap family
    cap = {
        "net_rx_bytes": (825000000.0,
                         "measured: Arm rx bottleneck 6.6 Gbps"),
        "net_tx_bytes": None,
        "en3f1pf1sf0_tx_bytes": (825000000.0,
                                 "measured: Arm-facing representor (rx cap)"),
        "p1_rx_bytes": (12500000000.0, "cap: 100G link (ethtool 9/18 M2 B4)"),
        "p1_tx_bytes": (12500000000.0, "cap: 100G link (ethtool 9/18 M2 B4)"),
        "pf1hpf_rx_bytes": (12500000000.0, "cap: 100G wire (p1, 9/18)"),
        "pf1hpf_tx_bytes": (12500000000.0, "cap: 100G wire (p1, 9/18)"),
        "enp3s0f1s0_tx_bytes": None,
        "pipe:p1_bytes": (12500000000.0, "cap: p1 100G (ethtool 9/18)"),
        "pcie0_rx_bytes": (15753846154.0, "cap: host link Gen3 x16 (LnkSta 8GT/s downgraded; fujian lspci 9/18)"),
        "pcie0_tx_bytes": (15753846154.0, "cap: host link Gen3 x16 (LnkSta 8GT/s downgraded; fujian lspci 9/18)"),
        "pcie1_rx_bytes": (31507692308.0, "cap: Gen4 x16 (lspci 9/18)"),
        "pcie1_tx_bytes": (31507692308.0, "cap: Gen4 x16 (lspci 9/18)"),
    }
    for c in list(cap):
        if cap[c] is None:
            cap[c] = (om.get(c, 0.0), "obs-max provisional")
    # unverified = never moved (max 0) or never above idle
    im, ic = pool_idle()
    unverified = set(seen) - set(om)
    for c, v in om.items():
        if c in ic and ic[c] > 0 and v <= im[c] / ic[c]:
            unverified.add(c)
    unverified -= set(SKIP_COLS)
    write_sat(span, cap, unverified)
    print("sat anchors written: configs/anchor_sat.conf")
    print("[span] entries: %d" % len(span))
    print("[cap] entries: %d" % len(cap))
    print("[unverified]: %s" % (", ".join(sorted(unverified)) or "none"))
    # gap list: @span components with no usable sat entry
    missing = sorted(c for c in wanted if c not in span and c not in cap)
    print("span components without sat entry (gaps): %s"
          % (", ".join(missing) or "none"))


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "idle"
    if cmd == "idle":
        cmd_idle()
    else:
        cmd_sat()


if __name__ == "__main__":
    main()
