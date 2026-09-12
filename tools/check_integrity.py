#!/usr/bin/env python3
# check_integrity.py - integrity check for phase-mode result sets
# (CSV + .phase.log pairs) produced by run_phase.sh / collect_all.
#
# Usage: python tools/check_integrity.py results
#
# CSV column layout (71 cols, see configs/app_full.conf):
#   0 timestamp, 1 tile_group, 2-23 tile counters (22 unique names,
#   rotation groups share duplicates like a72_access), 24 l3_group,
#   25-44 l3half0 (20 slots), 45-64 l3half1 (20 slots), 65-70
#   pcie0/pcie1/net (always-on, 6 cols).
#
# Checks per run:
#   * .phase.log arithmetic: pre-idle 5 s, app window >= 10 s,
#     end - start == collect window, CSV timestamps aligned to it
#   * CSV structure: identical header across runs, row count == window,
#     1 s timestamp spacing, tile_group/l3_group rotation pattern,
#     active-group cells filled / inactive cells empty (per config),
#     no negative or non-numeric values
#   * idle-vs-app contrast for path-signature counters
# Prints tables and an anomaly summary.  No figures are produced.
import csv
import glob
import os
import re
import sys
from statistics import median

RES = sys.argv[1] if len(sys.argv) > 1 else "results"

TILE_COLS = 22
L3_COLS = 20
IDX_L3GROUP = 24
COL_TILE = slice(2, 2 + TILE_COLS)
COL_HALF0 = slice(IDX_L3GROUP + 1, IDX_L3GROUP + 1 + L3_COLS)
COL_HALF1 = slice(IDX_L3GROUP + 1 + L3_COLS, IDX_L3GROUP + 1 + 2 * L3_COLS)
COL_PCIENET = slice(IDX_L3GROUP + 1 + 2 * L3_COLS,
                    IDX_L3GROUP + 1 + 2 * L3_COLS + 6)

# tile rotation groups -> the 4 unique counter names each group programs
# (configs/app_full.conf [tile]; duplicates dedupe to first occurrence)
TILE_GROUPS = [
    ["tile_a72_access", "tile_a72_read", "tile_a72_write",
     "tile_rnf_requests"],
    ["tile_io_access", "tile_io_reads", "tile_io_write", "tile_tso_write"],
    ["tile_req_buf_empty", "tile_hnf_requests", "tile_dir_hit",
     "tile_allocate"],
    ["tile_victim", "tile_poc_fail", "tile_poc_success", "tile_poc_writes"],
    ["tile_poc_reads", "tile_mem_reads", "tile_mem_writes",
     "tile_memory_reads_bypass"],
    ["tile_victim_write", "tile_mss_nocredit", "tile_a72_access",
     "tile_mem_reads"],
]

# l3 rotation groups -> filled slot indices per half (0=cycles,
# 1=total_rd_req_in, ... 19=evictions); BANK0/BANK1 variants map to
# half0/half1, shared counters fill both halves
L3_SIG = {
    0: ({0, 1, 2, 3}, {0, 1, 2, 3}),
    1: ({4, 5, 6, 7}, {4, 5, 6, 7}),
    2: ({8, 9, 10}, {8, 9, 10}),
    3: ({11, 12}, {11, 12}),
    4: ({13, 14}, {13, 14}),
    5: ({15, 16}, {15, 16}),
    6: ({17, 18}, {17, 18}),
    7: ({19, 16}, {19, 16}),
}


def parse_phase(path):
    d = {}
    with open(path) as f:
        for line in f:
            m = re.match(r"(\w+)=(\d+)$", line.strip())
            if m:
                d[m.group(1)] = int(m.group(2))
    return d


def load_rows(path):
    with open(path, newline="") as f:
        rd = csv.reader(f)
        header = next(rd)
        rows = [r for r in rd if any(cell for cell in r)]
    return header, rows


def num(cell):
    try:
        return float(cell)
    except ValueError:
        return None


def check_file(csvp, ref_header):
    """Return (report dict, problems list)."""
    probs = []
    header, rows = load_rows(csvp)
    ph = parse_phase(csvp + ".phase.log")
    base = os.path.basename(csvp)[:-4]

    if not ph:
        probs.append("no phase log")
        return None, probs
    start, app_start, app_end, end = (ph.get(k) for k in
                                      ("start", "app_start", "app_end", "end"))
    if None in (start, app_start, app_end, end):
        probs.append("phase log incomplete")
        return None, probs
    pre = app_start - start
    app = app_end - app_start
    post = end - app_end
    total = end - start
    if pre != 5:
        probs.append("pre-idle %ds != 5" % pre)
    if app < 10:
        probs.append("APP WINDOW ONLY %ds (expected >=10)" % app)
    if post < 4:
        probs.append("post-idle %ds < 4" % post)

    if header != ref_header:
        probs.append("header differs from reference (%d vs %d cols)"
                     % (len(header), len(ref_header)))
        for i, (a, b) in enumerate(zip(header, ref_header)):
            if a != b:
                probs.append("  col %d: %r vs %r" % (i, a, b))
        return None, probs

    col_of = {name: i for i, name in enumerate(header)}

    n = len(rows)
    if n != total:
        probs.append("row count %d != window %d" % (n, total))

    ts_ok = True
    tss = []
    for r in rows:
        try:
            tss.append(int(r[0]))
        except ValueError:
            probs.append("bad timestamp %r" % r[0])
            ts_ok = False
    if ts_ok:
        if tss and tss[0] != start + 1:
            probs.append("first ts %d != start+1 (%d)" % (tss[0], start + 1))
        if tss and tss[-1] != end:
            probs.append("last ts %d != end (%d)" % (tss[-1], end))
        for i in range(1, len(tss)):
            if tss[i] != tss[i - 1] + 1:
                probs.append("ts gap at row %d: %d -> %d"
                             % (i, tss[i - 1], tss[i]))
                break

    tile_off = None
    l3_off = None
    neg = []
    badcell = []
    for i, r in enumerate(rows):
        tg = int(r[1]) if r[1].isdigit() else None
        lg = int(r[IDX_L3GROUP]) if r[IDX_L3GROUP].isdigit() else None
        if tg is None or lg is None or not 0 <= tg < 6 or not 0 <= lg < 8:
            probs.append("row %d: bad group markers" % i)
            continue
        o = (tg - i) % 6
        if tile_off is None:
            tile_off = o
        elif o != tile_off:
            probs.append("row %d: tile_group rotation breaks (off %d != %d)"
                         % (i, o, tile_off))
        o = (lg - i) % 8
        if l3_off is None:
            l3_off = o
        elif o != l3_off:
            probs.append("row %d: l3_group rotation breaks (off %d != %d)"
                         % (i, o, l3_off))

        # tile block: exactly the active group's 4 named cells filled
        want = set(col_of[name] - 2 for name in TILE_GROUPS[tg])
        got = set(j for j, cell in enumerate(r[COL_TILE]) if cell != "")
        if got != want:
            probs.append("row %d: tile fill g%d = %s, want %s"
                         % (i, tg, sorted(got), sorted(want)))
        for j, cell in enumerate(r[COL_TILE]):
            if cell != "":
                v = num(cell)
                if v is None:
                    badcell.append("row %d tile slot %d: %r" % (i, j, cell))
                elif v < 0:
                    neg.append("row %d tile slot %d: %s" % (i, j, cell))

        # l3 halves: exactly the expected slots per group
        exp0, exp1 = L3_SIG[lg]
        for block, sl, exp in ((0, r[COL_HALF0], exp0),
                               (1, r[COL_HALF1], exp1)):
            got = set(j for j, cell in enumerate(sl) if cell != "")
            if got != exp:
                probs.append("row %d: l3 g%d half%d fill = %s, want %s"
                             % (i, lg, block, sorted(got), sorted(exp)))
            for j, cell in enumerate(sl):
                if cell != "":
                    v = num(cell)
                    if v is None:
                        badcell.append("row %d l3 h%d s%d: %r"
                                       % (i, block, j, cell))
                    elif v < 0:
                        neg.append("row %d l3 h%d s%d: %s"
                                   % (i, block, j, cell))

        # always-on pcie/net block
        for j, cell in enumerate(r[COL_PCIENET]):
            if cell == "":
                probs.append("row %d: always-on col %d empty" % (i, j))
                continue
            v = num(cell)
            if v is None:
                badcell.append("row %d pcie/net col %d: %r" % (i, j, cell))
            elif v < 0:
                neg.append("row %d pcie/net col %d: %s" % (i, j, cell))

    probs += neg[:3] + (["...%d more negative cells" % (len(neg) - 3)]
                        if len(neg) > 3 else [])
    probs += badcell[:3] + (["...%d more bad cells" % (len(badcell) - 3)]
                            if len(badcell) > 3 else [])

    report = dict(base=base, n=n, pre=pre, app=app, post=post,
                  tile_off=tile_off, l3_off=l3_off)
    return report, probs


def contrast(pairs, ref_header):
    """Group-level idle-vs-app medians for path-signature counters."""
    CONTRAST = ["tile_a72_access", "tile_hnf_requests", "tile_io_access",
                "tile_mem_reads", "tile_poc_writes", "tile_victim_write",
                "l3half0_hits", "l3half0_misses", "l3half0_allocations",
                "l3half0_evictions", "l3half1_hits", "l3half1_misses",
                "pcie0_rx_bytes", "pcie0_tx_bytes",
                "pcie1_rx_bytes", "pcie1_tx_bytes",
                "net_rx_bytes", "net_tx_bytes"]
    colmap = {c: [] for c in ref_header}
    for p in pairs:
        header, rows = load_rows(p)
        ph = parse_phase(p + ".phase.log")
        if not ph:
            continue
        idle = [r for r in rows
                if ph["start"] + 1 <= int(r[0]) <= ph["app_start"]]
        app = [r for r in rows
               if ph["app_start"] + 1 <= int(r[0]) <= ph["app_end"]]
        for i, name in enumerate(header):
            iv = [v for v in (num(r[i]) for r in idle) if v is not None]
            av = [v for v in (num(r[i]) for r in app) if v is not None]
            colmap[name].append((iv, av, os.path.basename(p)[:2]))

    print("\n=== idle/app contrast (median of 3 runs; ratio = app/idle) ===")
    print("%-28s %4s %12s %12s %8s %6s"
          % ("counter", "grp", "idle_med", "app_med", "ratio", "n_app"))
    for sub in CONTRAST:
        names = [n for n in ref_header if sub in n]
        for name in names:
            byg = {}
            for iv, av, g in colmap[name]:
                byg.setdefault(g, [[], []])
                if iv:
                    byg[g][0].append(median(iv))
                if av:
                    byg[g][1].append(median(av))
            for g in sorted(byg):
                idle_med = median(byg[g][0]) if byg[g][0] else 0
                app_med = median(byg[g][1]) if byg[g][1] else 0
                n_app = sum(1 for iv, av, gg in colmap[name]
                            if gg == g and len(av) >= 2)
                if idle_med > 0:
                    ratio = "%.1f" % (app_med / idle_med)
                elif app_med > 0:
                    ratio = "inf"
                else:
                    ratio = "-"
                print("%-28s %4s %12.0f %12.0f %8s %6s"
                      % (name, g, idle_med, app_med, ratio,
                         n_app if n_app else "*"))


def check_background(pairs, ref_header):
    """Flag runs whose pre-idle median is far above the group minimum
    (leftover/orphan app processes contaminate the idle background)."""
    cols = [i for i, n in enumerate(ref_header)
            if n in ("tile_a72_access", "tile_hnf_requests")]
    per = {}
    for p in pairs:
        rows = load_rows(p)[1]
        ph = parse_phase(p + ".phase.log")
        if not ph:
            continue
        g = os.path.basename(p)[:2]
        pre = [r for r in rows
               if ph["start"] + 1 <= int(r[0]) <= ph["app_start"]]
        for c in cols:
            vals = [v for v in (num(r[c]) for r in pre) if v is not None]
            if vals:
                per.setdefault((g, ref_header[c]), []).append(
                    (os.path.basename(p), median(vals)))

    print("\n=== pre-idle background consistency ===")
    for (g, name), lst in sorted(per.items()):
        m = min(v for _, v in lst)
        for f, v in lst:
            mark = "  <-- contaminated?" if m > 0 and v > 5 * m else ""
            print("%-28s %-4s %-12s %12.0f%s" % (name, g, f, v, mark))


def main():
    pairs = sorted(glob.glob(os.path.join(RES, "g*_run*.csv")))
    if not pairs:
        print("no g*_run*.csv found under %s" % RES)
        return 1

    ref_header, ref_rows = load_rows(pairs[0])
    print("=== header (%d cols) ===" % len(ref_header))
    for i, name in enumerate(ref_header):
        print("%3d %s" % (i, name))

    print("\n=== per-run checks ===")
    print("%-12s %3s %3s %3s %4s %5s %5s %s"
          % ("run", "rows", "pre", "app", "post", "t_off", "l_off",
             "problems"))
    good = []
    for p in pairs:
        rep, probs = check_file(p, ref_header)
        if rep is None:
            print("%-12s %s" % (os.path.basename(p), "; ".join(probs)))
            continue
        print("%-12s %3d %3d %3d %4d %5s %5s %s"
              % (rep["base"], rep["n"], rep["pre"], rep["app"], rep["post"],
                 rep["tile_off"], rep["l3_off"],
                 "; ".join(probs) if probs else "ok"))
        if not probs:
            good.append(p)

    contrast(pairs, ref_header)
    check_background(pairs, ref_header)
    print("\n=== summary: %d/%d runs pass all structural checks ==="
          % (len(good), len(pairs)))
    if len(good) < len(pairs):
        bad = [os.path.basename(p) for p in pairs if p not in good]
        print("failing runs: %s" % ", ".join(bad))
    return 0


if __name__ == "__main__":
    sys.exit(main())
