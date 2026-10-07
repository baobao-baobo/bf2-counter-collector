#!/usr/bin/env python3
"""replay_validate.py - P2 historical-replay validation (Part 4).

Replays every collected run through the three-layer index and checks
it against the independent judgments established earlier:

  gate 1 (idle):     phase-logged runs must show L_p ~ 0 on their
                     pre/post idle windows
  gate 2 (replay):   the argmax path must match the workload's
                     expected busy path (xz/BFS -> CR, Redis -> NAD,
                     iperf NHD -> NHD, low-load apps -> all low)
  gate 3 (degrade):  no single counter may saturate its vertex in
                     >80% of app rows (obs-max anchor suspicion)

Usage: replay_validate.py [--verbose]
"""

import statistics
import sys

sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
import analyze_bottleneck as ab

RES = ab.ROOT + "\\results"
MSG = ab.ROOT + "\\message"
# 2026-09-30 results/ reorganization: the replay inputs moved into the
# classified subfolders (results/README.md); message/ is unchanged.
GSERIES = RES + "\\g-series"
E1SERIES = RES + "\\e1-series"

# app -> (expected busy path, low-load flag, metric, note)
# metric "med" = argmax of median L (tile-side judgments are
# magnitude-based); "wins" = argmax of per-row wins (wire-side
# judgments are direction-based, and the tile-side CR counters also
# carry the NAD DDR hop - mixed-flow, design doc sec 8.5)
EXPECT = {
    "g1": ("cr", False, "med", "xz decompress -> core read"),
    "g2": ("cr", False, "med", "BFS -> core read"),
    "g3": ("cr", False, "med", "Redis tile side: NAD DDR hop shows as CR"),
    "e1_g3": ("nad", False, "wins", "Redis wire side -> NAD"),
    "g4": ("cr", False, "med", "SQLite -> core read"),
    "g5": ("cr", False, "med", "BS -> core read"),
    "g6": ("cr", False, "med", "TFLite -> core read"),
    "g7": ("cr", True, "med", "Mix: low load, no strong winner"),
    "e1_g7": ("nad", True, "wins", "Mix wire side: low load"),
    "e1_n2": ("nhd", False, "wins", "iperf NHD direction -> P5"),
    "e1_n0_2a": ("nad", False, "wins", "host-pipe calibration 2a: host->Arm"
                                       " via pcie0 (p1 rx/tx == 0),"
                                       " 6.05 Gbps Arm rx plateau"),
    "e1_n0_2b": ("nad", False, "wins", "host-pipe calibration 2b: reverse"
                                       " (Arm->host via pcie0); judged by"
                                       " the NAD host pipe (pcie0)"),
    # e1_n1_* (2026-09-22 retargeted nad->nhd): the executed N1
    # posture did NOT produce host->Arm traffic.  Counter signature in
    # every app row: p1_rx ~= pf1hpf_tx = flood, pcie0_tx = flood,
    # p1_tx = ACKs, en3f1pf1sf0_rx = 0 (Arm never delivered) - pure
    # wire->host NHD (the N1 posture invalidation already recorded in
    # the E1 notes).  The engine correctly reads the empirical content;
    # the gate now encodes that reading.
    "e1_n1_1g": ("nhd", False, "wins", "executed posture: wire->host"
                                       " NHD (not host->Arm)"),
    "e1_n1_5g": ("nhd", False, "wins", "executed posture: wire->host"
                                       " NHD (not host->Arm)"),
    "e1_n1_10g": ("nhd", False, "wins", "executed posture: wire->host"
                                       " NHD (not host->Arm)"),
    "e1_n1_20g": ("nhd", False, "wins", "executed posture: wire->host"
                                       " NHD (not host->Arm)"),
    # e0_1_nhd / e0_2_nad are NOT replay targets: the E0 CSVs carry
    # no wire columns (nad/nhd/tx entries absent), and the E0-1
    # evidence itself was invalidated 2026-09-15 (traffic-posture
    # error).  E0-2 was superseded by the E1 series.
    "e0_3_emmc": ("ih", True, "med", "fio eMMC: all paths low expected -"
                                     " the eMMC device has no counter"
                                     " (coverage gap), busyness is"
                                     " invisible by design"),
}


def runs_of(app):
    if app.startswith("e0_"):
        return [MSG + "\\" + app + ".csv"]
    if app.startswith("g"):
        base = GSERIES
    elif app.startswith("e1_"):
        base = E1SERIES
    else:
        base = RES
    # calibration files are single continuous-load CSVs; e1_n2 has
    # three phase-logged runs like the G series
    if app in ("e1_n0_2a", "e1_n0_2b", "e1_n1_1g", "e1_n1_5g",
               "e1_n1_10g", "e1_n1_20g"):
        return [base + "\\" + app + ".csv"]
    out = []
    for r in range(1, 4):
        out.append(base + "\\%s_run%d.csv" % (app, r))
    return out


def main():
    paths, vertices, idle_v, span_v, cap_v, unver = ab.load_model()
    order = list(paths.keys())
    verbose = "--verbose" in sys.argv

    print("%-10s %-28s %-6s %-6s %-8s %s" % (
        "app", "L medians (cr ih ib wb nad nhd tx)",
        "idle", "exp", "verdict", "note"))
    results = {}
    for app, (exp, low, metric, note) in EXPECT.items():
        lps = {p: [] for p in order}
        idle_max = []
        wins = {}
        sat_sum = {}       # counter label -> sum of argmax n values
        sat_tot = {}
        for path in runs_of(app):
            run, out = ab.run_one(path, None, paths, vertices, idle_v,
                                  span_v, cap_v, unver, verbose=False)
            for name, s, w, dom, warns in out:
                if name == "idle":
                    idle_max.append(max(v for v in s.values()
                                        if v is not None))
                else:
                    for p, v in s.items():
                        if v is not None:
                            lps[p].append(v)
                    for p, c in w.items():
                        wins[p] = wins.get(p, 0) + c
            # gate 3 raw material: rows where the vertex argmax n ~ 1
            an = ab.Analyzer(paths, vertices, idle_v, span_v, cap_v, unver)
            for i in run.app_i:
                for vname in vertices:
                    v, m, label, excl = an.vertex_row(run, i, vname)
                    if label and v is not None:
                        sat_tot[label] = sat_tot.get(label, 0) + 1
                        sat_sum[label] = sat_sum.get(label, 0.0) + v
        med = {p: statistics.median(v) if v else None for p, v in lps.items()}
        results[app] = med
        # argmax by median
        nz = {p: v for p, v in med.items() if v is not None}
        top = max(nz, key=nz.get) if nz else None
        # argmax by wins
        wtop = max(wins, key=wins.get) if wins else None
        imax = max(idle_max) if idle_max else None
        # idle gate: 0.15.  1-sample L3 rotation windows against
        # pooled anchors give n ~ 0.05-0.08 on the read counters
        # (obs-max sat ~65-148M).  The L3 write-pipeline counters
        # (wr_dbid_ack/wr_data_in/wr_comp, added 2026-09-17 for
        # paper52 alignment) have obs-max sats ~9M - the same
        # absolute single-sample noise (~2M/s) maps to 5-10x larger
        # n (~0.10-0.13).  A systematic error (wrong unit/anchor)
        # would push n toward 1, so the diagnostic power is
        # unchanged.  Part 6 recalibration raises these sats and the
        # noise compresses (see docs/validation-replay.md sec 2).
        ok_idle = imax is None or imax <= 0.15
        judge = top if metric == "med" else wtop
        ok_replay = judge == exp or (low and top is not None and nz[top] < 0.2)
        # low-load wins tie: a winner without a majority of rows is
        # "no strong winner" (the documented judgment for e1_g7).
        # e1_g7 sits on a knife edge (cr 36 : nad 35): the Part 6
        # true io anchors deflated a few marginal NAD rows.
        if low and metric == "wins" and wtop is not None:
            tot = sum(wins.values())
            if tot and wins[wtop] / tot < 0.5:
                ok_replay = True
        # sustained ~saturation while vertex argmax: the obs-max
        # anchor may be the workload's own maximum (anchor too low)
        sat_sus = {l: sat_sum[l] / sat_tot[l] for l in sat_tot
                   if sat_tot[l] >= 10 and sat_sum[l] / sat_tot[l] >= 0.85}
        ls = " ".join("%s=%.2f" % (p, med[p]) if med[p] is not None
                      else "%s=-" % p for p in order)
        verdict = []
        if not ok_idle:
            verdict.append("IDLE-FAIL(%.3f)" % imax)
        if not ok_replay:
            verdict.append("REPLAY-FAIL(judge=%s exp=%s)" % (judge, exp))
        if sat_sus:
            verdict.append("SAT-SUSPECT:" + ",".join(sat_sus))
        if not verdict:
            verdict.append("PASS")
        print("%-10s %-28s %-6s %-6s %-8s %s" % (
            app, ls, "%.3f" % imax if imax is not None else "n/a",
            exp, ";".join(verdict), note))
        if verbose and wins:
            print("   wins: %s" % " ".join("%s=%d" % kv
                                           for kv in sorted(wins.items())))
    return results


if __name__ == "__main__":
    main()
