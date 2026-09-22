#!/usr/bin/env python3
"""prism_search.py - scene-level busy-path search (P2.5).

Search layer over the three-layer index, per docs/prism-search-design.md:
given a scene window (one or more collector CSVs), answer "which data
path is busiest right now?" with a reusable evidence chain.  The
engine (load_model / Run / Analyzer / run_one of analyze_bottleneck.py)
is reused verbatim - this file only adds scene aggregation, judgment
rules, and the evidence-chain report.

Judgment (design doc sec 3; the replay gates formalized):
  dominant  med leader >= 0.2 and judge path leads the median ranking
            with a majority of per-row wins
  low       med leader < 0.2 (whole scene quiet, no busy path)
  multi     med leader >= 0.2 but the wins leader lacks a majority of
            rows (busy scene, several paths share dominance; e1_g7
            cr 36 : nad 35 precedent, 2026-09-20 tie rule; E2E C
            cr 16/35 with med 0.584, 2026-09-22)

Evidence chain (design doc sec 4):
  verdict + confidence shape, path ranking (median L_p), vertex
  contribution decomposition of the busy path (median v_j, argmax
  counter, provenance tier from anchor_sat.conf comments), warnings
  (arbitration conflicts, SAT-SUSPECT anchors, [unverified] gated
  counters, blind-spot circumstantial evidence, degraded wins).

Usage:
  prism_search.py <csv...> [--scene NAME] [--json out.json] [--pipe pipe.csv]
  prism_search.py <csv...> [--plot PREFIX]
    writes PREFIX_mag.dat (per-path median L_p) + PREFIX_dir.dat
    (per-path row wins) + PREFIX.plt (two-panel gnuplot, magnitude
    and direction in one figure) + PREFIX.png
  prism_search.py --selfcheck
    instance set A (17 replays): judgments must reproduce
    replay_validate.py's gate conclusions exactly (regression gate)
    instance set B (sat six faces): must match design doc sec 5
"""

import argparse
import json
import os
import statistics
import sys

if sys.stdout.encoding and sys.stdout.encoding.lower() in ("cp936", "gbk"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze_bottleneck as ab
import replay_validate as rv

BENCH_RES = os.path.join(ab.ROOT, "bench", "results")

# Four honest blind spots (design doc sec 1/7): no hardware counter
# exists; pressure is circumstantially evidenced by upstream/
# downstream counters only.  Attached to the vertices that host them.
BLIND = {
    "l3": "L3 内部队列压力（无硬件计数器，上下游旁证）",
    "pcie0": "PCIe TLR 队列（Arm 根复合体 TLR 对 NHD 不可见）",
    "pcie1": "PCIe TLR 队列（Arm 根复合体 TLR 对 NHD 不可见）",
    "eswitch": "eSwitch 内部队列（无 switch 计数器块）",
    "arm": "Arm 软件队列（qdisc/socket 积压未采集，Tier-0 未来工作）",
}

# Instance set B (design doc sec 5): sat face -> (expected busy path,
# low-load flag).  All six are judged by the magnitude criterion
# (med).  p1 is a weak face (small L_p) and p6 is the honest negative
# (eMMC has no counter - all-low by design), so both carry low=True.
SAT_EXPECT = {
    "p1": ("cr", True, "stress-ng cpu: pure compute, weak face"),
    "p3": ("cr", False, "STREAM: emem read+write full pressure"),
    "p4": ("cr", False, "memrand: random access, locality destruction"),
    "p5": ("cr", False, "stress-ng cache: strongest face"),
    "p6": ("ih", True, "fio eMMC: coverage gap - honest negative"),
    # p7 (2026-09-22 correction): the p7 CSVs (bench_p7_net.conf) carry
    # no per-port columns, so the TX/NAD/NHD exit paths are unobservable
    # (their entries are absent); and the p7 upload (Arm->host) does not
    # traverse the wire port p1 at all.  The TX DMA read hop shows as CR
    # (tile_io_reads n~1.0 - the mixed-flow manifestation, same as the
    # NAD DDR hop, design doc sec 8.5).  An Arm->host exit path does not
    # exist in the path model yet - future work (design doc sec 5 note).
    "p7": ("cr", False, "iperf3 upload: TX DMA read hop shows as CR "
                        "(io_reads == TX rate); exit paths unobservable"),
}


def load_prov():
    """Provenance tier per anchor key, parsed from anchor_sat.conf
    value comments (written by extract_anchors.py).  tier 0 = true
    reference (cap/measured/derived/bench-* stress), tier 1 =
    obs-max lower bound."""
    prov = {}
    sec = None
    for line in open(os.path.join(ab.CFG, "anchor_sat.conf")):
        line = line.strip()
        if line.startswith("[") and line.endswith("]"):
            sec = line[1:-1]
            continue
        if "=" not in line or "#" not in line:
            continue
        key, _, rest = line.partition("=")
        note = rest.partition("#")[2].strip()
        tier = 0 if ("bench-" in note or "cap:" in note or "measured" in note
                     or "derived" in note) else 1
        prov[key.strip()] = (tier, note)
    return prov


def prov_of(ctr, prov):
    """Provenance of a counter expression: worst tier across
    components (sums inherit the weakest anchor)."""
    if not ctr.components:
        return 1, "效率兜底（无锚点）" if ctr.fam == "miss" else "无锚点"
    tier, notes = 1, set()
    for c in ctr.components:
        key = (ctr.src + ":") + c if ctr.src else c
        t, n = prov.get(key, (1, "unverified"))
        tier = min(tier, t)
        notes.add(n)
    return tier, "/".join(sorted(notes))


def divergence_notes(winner, attrib, grank):
    """P2.5d: divergence between the global vertex ranking and the path
    verdict.  N1 - the global peak vertex's dominant flow lands on a
    path other than the winner; N2 - the global peak is not the
    winner's top contributor yet reads higher than that contributor's
    mean contribution.  Caliber note: v is an observed-row mean (a
    vertex that never fires has no reading), c is a full-support mean
    (zero-filled rows so it sums to L_p) - the comparison is a report
    flag for human review, never a verdict input."""
    notes = []
    if not grank or not attrib:
        return notes
    top = grank[0]
    if top["flow"] is not None and top["flow"] != winner:
        notes.append("global peak vertex %s (v=%.3f) mainly flows to "
                     "path %s, not the winning %s"
                     % (top["vertex"], top["v"], top["flow"], winner))
    if top["vertex"] != attrib[0]["vertex"] and \
            top["v"] > attrib[0]["c"]:
        notes.append("global peak vertex %s (v=%.3f) reads above the "
                     "winner path's top contribution (%s c=%.3f)"
                     % (top["vertex"], top["v"],
                        attrib[0]["vertex"], attrib[0]["c"]))
    return notes


def search(scene, csvs, pipe=None, model=None):
    """Scene-level search.  Returns the evidence-chain dict."""
    if model is None:
        model = ab.load_model()
    paths, vertices, idle_v, span_v, cap_v, unver = model
    prov = load_prov()

    lps = {p: [] for p in paths}
    wins = {}
    dom = {}
    warns = set()
    sat_sum = {}
    sat_tot = {}
    vrow = {}          # (path, vertex) -> list of v over app rows
    vlab = {}          # (path, vertex) -> dict label -> [n values]
    contrib = {}       # (path, vertex) -> [v * M1 share] over app rows
    rowtop = {}        # path -> {vertex: rows where it tops that path}
    rowlp = {}         # path -> [row L_p] over app rows
    vglob = {}         # vertex -> {(run id, row): v} over observed rows
    vowners = {}       # vertex -> set of paths that observed it
    napp = 0           # total app rows across runs (P2.5d obs denom)
    imax = None
    for rid, path in enumerate(csvs):
        run, out = ab.run_one(path, pipe, paths, vertices, idle_v,
                              span_v, cap_v, unver, verbose=False)
        napp += len(run.app_i)
        an = ab.Analyzer(paths, vertices, idle_v, span_v, cap_v, unver)
        for name, s, w, d, ws in out:
            if name == "idle":
                vals = [v for v in s.values() if v is not None]
                if vals:
                    imax = max(imax, max(vals)) if imax is not None \
                        else max(vals)
                continue
            for p, v in s.items():
                if v is not None:
                    lps[p].append(v)
            for p, c in w.items():
                wins[p] = wins.get(p, 0) + c
            for l, c in d.items():
                dom[l] = dom.get(l, 0) + c
            warns |= ws
        for i in run.app_i:
            lp, w = an.row_lp(run, i)
            warns |= set(w)
            for p, t in lp.items():
                if t[0] is None:
                    continue
                rowlp.setdefault(p, []).append(t[0])
                seen = set()
                topv, topc = None, -1.0
                for vname, v, share, label, excl in t[1]:
                    key = (p, vname)
                    if v is None:
                        # full-support caliber: a vertex unobservable
                        # on this row contributes 0 to that row's L_p,
                        # matching the row total exactly
                        contrib.setdefault(key, []).append(0.0)
                        seen.add(vname)
                        continue
                    seen.add(vname)
                    vrow.setdefault(key, []).append(v)
                    c = v * share
                    contrib.setdefault(key, []).append(c)
                    vowners.setdefault(vname, set()).add(p)
                    # dedup by (run, row): v is the raw vertex reading,
                    # path-independent - a shared vertex reports the
                    # same v under each owner path in one row
                    vglob.setdefault(vname, {})[(rid, i)] = v
                    if c > topc:
                        topv, topc = vname, c
                    if label:
                        vlab.setdefault(key, {}).setdefault(
                            label, []).append(v)
                for vname in paths[p]["vertices"]:
                    if vname not in seen:
                        contrib.setdefault((p, vname), []).append(0.0)
                if topv is not None:
                    rt = rowtop.setdefault(p, {})
                    rt[topv] = rt.get(topv, 0) + 1
            for vname in vertices:
                v, m, label, excl = an.vertex_row(run, i, vname)
                if label and v is not None:
                    sat_tot[label] = sat_tot.get(label, 0) + 1
                    sat_sum[label] = sat_sum.get(label, 0.0) + v

    med = {p: statistics.median(v) if v else None for p, v in lps.items()}
    nz = {p: v for p, v in med.items() if v is not None}
    if not nz:
        return {"scene": scene, "csvs": csvs, "verdict": "no-data",
                "ranking": [], "decomposition": [], "warnings": [],
                "idle_max": imax}
    top = max(nz, key=nz.get)
    wtop = max(wins, key=wins.get) if wins else None
    wtot = sum(wins.values()) if wins else 0

    if nz[top] < 0.2:
        verdict, winner = "low", top
    elif wtop is not None and wtot and wins[wtop] / wtot < 0.5:
        # busy scene without a row-majority winner: multi-path busy,
        # no single dominant (E2E C: cr med 0.584, wins 16/35 - the
        # old rule mislabeled it "low"; e1_g7 36:35 tie likewise)
        verdict, winner = "multi", top
    else:
        verdict, winner = "dominant", top

    ranking = sorted(nz.items(), key=lambda kv: -kv[1])
    decomp = []
    for vname in paths[winner]["vertices"]:
        key = (winner, vname)
        vals = vrow.get(key)
        if vals is None:
            continue
        # window mean, matching the ranking's summarize() - medians
        # dilute to 0 over idle rows in bursty scenes (E2E B: 10s
        # flood inside a 30s window; 2026-09-22)
        mv = statistics.mean(vals)
        label = None
        mn = None
        if vlab.get(key):
            label = max(vlab[key], key=lambda l: len(vlab[key][l]))
            mn = statistics.mean(vlab[key][label])
        ctr = [c for c in vertices[vname]["counters"]
               if "%s@%s" % (c.fam, c.expr) == label.split(":", 1)[1]]
        tier, note = prov_of(ctr[0], prov) if ctr else (1, "unverified")
        excl = vertices[vname]["counters"]
        gated = sum(1 for c in excl
                    if any(x in unver for x in c.components))
        mc = statistics.mean(contrib[key]) if contrib.get(key) else 0.0
        item = {"vertex": vname, "v": round(mv, 3), "c": round(mc, 3),
                "label": label,
                "n": round(mn, 3) if mn is not None else None,
                "prov": note, "prov_tier": tier}
        if gated:
            item["unverified_gated"] = gated
        decomp.append(item)

    # P2.5b: contribution attribution - rank the winner path's
    # vertices by mean contribution (v * M1 share, same window-mean
    # caliber as the decomposition).  The sum of mean contributions
    # equals the row-mean L_p; stability = fraction of rows where the
    # vertex tops the path's own ranking.  This is a model-caliber
    # decomposition ranking, not causal localization: it inherits the
    # backpressure coverage gaps and anchor provenance of the model.
    tot_c = sum(d["c"] for d in decomp) if decomp else 0.0
    rt = rowtop.get(winner, {})
    nrows = sum(rt.values())
    attrib = []
    for d in decomp:
        share = round(d["c"] / tot_c, 3) if tot_c > 0 else None
        st = rt.get(d["vertex"], 0)
        attrib.append(dict(d, share=share, stable=[st, nrows]))
    attrib.sort(key=lambda x: -(x["c"] or 0.0))
    if verdict == "low":
        # no busy path - attribution is not defined for a quiet scene
        attrib = []
    row_mean = (round(statistics.mean(rowlp[winner]), 3)
                if rowlp.get(winner) else None)

    # P2.5d: global vertex pressure ranking.  v = row-mean over
    # OBSERVED rows only (a vertex that never fires has no reading;
    # zero-filling would fake a "not stressed" verdict for blind
    # spots).  flow = the owner path receiving the vertex's largest
    # mean contribution (full-support caliber, same as attribution).
    # This ranking answers "which observation point is closest to its
    # saturation reference", not "which path is busiest" - the two
    # can disagree, and divergence_notes surfaces exactly that.
    grank = []
    if verdict != "low":
        for vname, obsv in vglob.items():
            mv = statistics.mean(obsv.values())
            owners = sorted(vowners[vname])
            flows = {}
            for p in owners:
                cs = contrib.get((p, vname))
                if cs:
                    flows[p] = statistics.mean(cs)
            flowp = max(flows, key=flows.get) if flows else None
            flowc = round(flows[flowp], 3) if flowp else 0.0
            labs = {}
            for p in owners:
                for l, lst in vlab.get((p, vname), {}).items():
                    labs[l] = labs.get(l, 0) + len(lst)
            label = max(labs, key=labs.get) if labs else None
            lvals = []
            if label:
                for p in owners:
                    lvals.extend(vlab.get((p, vname), {}).get(label, []))
            mn = round(statistics.mean(lvals), 3) if lvals else None
            ctr = []
            if label:
                ctr = [c for c in vertices[vname]["counters"]
                       if "%s@%s" % (c.fam, c.expr)
                       == label.split(":", 1)[1]]
            tier, note = prov_of(ctr[0], prov) if ctr else (1, "unverified")
            gated = sum(1 for c in vertices[vname]["counters"]
                        if any(x in unver for x in c.components))
            grank.append({"vertex": vname, "v": round(mv, 3),
                          "obs": [len(obsv), napp],
                          "flow": flowp, "flowc": flowc,
                          "owners": owners, "label": label, "n": mn,
                          "prov": note, "prov_tier": tier,
                          "gated": gated})
        grank.sort(key=lambda x: -x["v"])
    gnote = divergence_notes(winner, attrib, grank)

    sat_sus = {l: round(sat_sum[l] / sat_tot[l], 3) for l in sat_tot
               if sat_tot[l] >= 10 and sat_sum[l] / sat_tot[l] >= 0.85}
    warnings = {"arbitration": sorted(warns), "sat_suspect": sat_sus,
                "blind": [BLIND[v] for v in paths[winner]["vertices"]
                          if v in BLIND],
                "degraded_wins": None}
    if wtop is not None and wtot and wins[wtop] / wtot < 0.5:
        warnings["degraded_wins"] = ("no majority: %s=%d/%d"
                                     % (wtop, wins[wtop], wtot))
    return {"scene": scene, "csvs": csvs, "verdict": verdict,
            "winner": winner, "med_top": round(nz[top], 3),
            "wins_top": wtop,
            "wins": wins, "wtot": wtot,
            "ranking": [(p, round(v, 3)) for p, v in ranking],
            "decomposition": decomp,
            "attrib": attrib, "lp_row_mean": row_mean,
            "grank": grank, "gnote": gnote,
            "warnings": warnings,
            "idle_max": round(imax, 3) if imax is not None else None}


def report(sc, paths):
    print("== scene: %s ==" % sc["scene"])
    print("  csvs: %s" % " ".join(sc["csvs"]))
    if sc["verdict"] == "no-data":
        print("  verdict: no-data (no observable path)")
        return
    if sc["verdict"] == "dominant":
        print("  verdict: dominant - %s (med %.3f)" %
              (sc["winner"], sc["med_top"]))
    elif sc["verdict"] == "multi":
        print("  verdict: multi (busy, no single dominant) - med leader %s=%.3f" %
              (sc["winner"], sc["med_top"]))
    else:
        print("  verdict: low (no strong winner) - med leader %s=%.3f" %
              (sc["winner"], sc["med_top"]))
    print("  ranking: %s" % " ".join("%s=%.3f" % kv for kv in sc["ranking"]))
    if sc["wins_top"]:
        print("  wins leader: %s (direction evidence)" % sc["wins_top"])
    print("  decomposition (%s):" % sc["winner"])
    for d in sc["decomposition"]:
        line = "    %-8s v=%.3f" % (d["vertex"], d["v"])
        if d["label"]:
            line += "  <- %s n=%.3f [%s]" % (d["label"], d["n"], d["prov"])
        if d.get("unverified_gated"):
            line += "  (%d gated unverified)" % d["unverified_gated"]
        print(line)
    att = sc.get("attrib") or []
    if sc["verdict"] == "low":
        print("  bottleneck attribution: skipped (verdict low - no busy path)")
    else:
        if sc["verdict"] == "multi":
            print("  bottleneck attribution (med leader %s; multi scene - "
                  "direction has no row majority):" % sc["winner"])
        else:
            print("  bottleneck attribution (ranked by contribution = "
                  "vertex value x M1 share):")
        for i, a in enumerate(att, 1):
            line = "    #%d %-8s c=%.3f (%.1f%%)  stable %d/%d" % (
                i, a["vertex"], a["c"],
                100.0 * (a["share"] or 0.0), a["stable"][0], a["stable"][1])
            if a["label"]:
                line += "  <- %s n=%.3f [%s]" % (a["label"], a["n"],
                                                 a["prov"])
            if a["label"] in sc["warnings"].get("sat_suspect", {}):
                line += "  [SAT-SUSPECT anchor]"
            print(line)
        if att and sc["lp_row_mean"] is not None:
            print("    (sum of mean contributions = row-mean L_p = %.3f)"
                  % sc["lp_row_mean"])
        print("    note: model-caliber contribution ranking, not causal "
              "localization (backpressure coverage and anchor provenance "
              "limits apply)")
        gr = sc.get("grank") or []
        if gr:
            print("  global vertex pressure ranking (v = row-mean over "
                  "observed rows; flow = owner path with the largest "
                  "mean contribution):")
            for i, g in enumerate(gr, 1):
                line = ("    #%d %-8s v=%.3f obs=%d/%d flow=%s(c=%.3f) "
                        "owners=%s" % (i, g["vertex"], g["v"],
                                       g["obs"][0], g["obs"][1],
                                       g["flow"] or "-", g["flowc"],
                                       ",".join(g["owners"])))
                if g["label"]:
                    line += "  <- %s n=%.3f [%s]" % (g["label"], g["n"],
                                                     g["prov"])
                if g.get("gated"):
                    line += "  (%d gated unverified)" % g["gated"]
                if g["label"] in sc["warnings"].get("sat_suspect", {}):
                    line += "  [SAT-SUSPECT anchor]"
                print(line)
            gn = sc.get("gnote") or []
            if gn:
                for note in gn:
                    print("  divergence note: " + note)
            else:
                print("  divergence note: none (global peak flows to "
                      "the %s path)" % sc["winner"])
    w = sc["warnings"]
    if w["blind"]:
        print("  blind-spot circumstantial evidence:")
        for b in w["blind"]:
            print("    [盲点旁证] " + b)
    if w["sat_suspect"]:
        print("  SAT-SUSPECT: %s" % " ".join("%s=%s" % kv
                                             for kv in sorted(w["sat_suspect"].items())))
    if w["degraded_wins"]:
        print("  degraded wins: " + w["degraded_wins"])
    if w["arbitration"]:
        print("  arbitration warns:")
        for x in w["arbitration"]:
            print("    " + x)
    if sc["idle_max"] is not None:
        print("  idle max L_p: %.3f" % sc["idle_max"])


def make_plot(sc, order, prefix):
    """Two-panel gnuplot figure (P2.5c): per-path median L_p (magnitude
    criterion) and per-path row wins (direction criterion) in one
    image, sharing the x-axis.  House style: full border with no
    top/right tics, key outside top, winner in deep red #B2172B,
    wins leader in light orange #F5A682.  Writes PREFIX_mag.dat,
    PREFIX_dir.dat, PREFIX.plt and renders PREFIX.png via gnuplot."""
    import subprocess
    nz = dict(sc["ranking"]) if sc.get("ranking") else {}
    wins = sc.get("wins") or {}
    n = len(order)
    y1 = max(0.35, 1.18 * max([nz.get(p, 0.0) for p in order] + [0.2]))
    y2 = max(1.0, 1.2 * max([wins.get(p, 0) for p in order] + [1]))
    winx = (order.index(sc["winner"]) + 1) if sc.get("winner") else 0
    wtx = (order.index(sc["wins_top"]) + 1) if sc.get("wins_top") else 0
    wtot = sc.get("wtot") or 0
    if sc["verdict"] == "dominant":
        title = "%s -- dominant %s (med %.3f, wins %s=%d/%d)" % (
            sc["scene"], sc["winner"], sc["med_top"], sc["wins_top"],
            wins.get(sc["wins_top"], 0), wtot)
    elif sc["verdict"] == "multi":
        title = "%s -- multi busy, med leader %s=%.3f" % (
            sc["scene"], sc["winner"], sc["med_top"])
    elif sc["verdict"] == "low":
        title = "%s -- low quiet, med leader %s=%.3f" % (
            sc["scene"], sc["winner"], sc["med_top"])
    else:
        title = "%s -- %s" % (sc["scene"], sc["verdict"])
    png = prefix + ".png"

    os.makedirs(os.path.dirname(os.path.abspath(prefix)),
                exist_ok=True)
    # gnuplot on Windows treats backslashes in quoted strings as
    # escapes (\b, \r, ...) - hand it forward-slash paths instead.
    # One data file per panel: this gnuplot 6.0.4 Windows build does
    # not split blank-line-separated blocks for `index`/`every`
    # (index 0 reads the whole file, index>=1 reads nothing), so a
    # two-block file plus `index` would silently empty panel 2.
    mag = prefix + "_mag.dat"
    dirf = prefix + "_dir.dat"
    png_s = png.replace("\\", "/")
    mag_s = mag.replace("\\", "/")
    dir_s = dirf.replace("\\", "/")
    with open(mag, "w") as f:
        for i, p in enumerate(order, 1):
            f.write("%d %.4f\n" % (i, nz.get(p, 0.0)))
    with open(dirf, "w") as f:
        for i, p in enumerate(order, 1):
            f.write("%d %d\n" % (i, wins.get(p, 0)))

    tics = ", ".join('"%s" %d' % (p, i)
                     for i, p in enumerate(order, 1))
    maj = "%.2f with lines dashtype 2 lc rgb \"#555555\" title \"majority\"" \
          % (wtot / 2.0)
    plt = prefix + ".plt"
    with open(plt, "w") as f:
        f.write("""\
set terminal pngcairo size 1280,960 font ",14"
set output "%s"
set encoding utf8
set border 15 lw 1.2
set xtics nomirror
set ytics nomirror
set boxwidth 0.72
set style fill solid 0.85 border -1
set multiplot layout 2,1 title "%s" font ",16"

# ---- panel 1: magnitude criterion ----
set xrange [0.5:%d.5]
set yrange [0:%.3f]
set format x ""
set ylabel "median L_p (window-mean median)" font ",14"
set title "magnitude criterion" font ",15"
set key outside top center horizontal
plot "%s" using 1:2 with boxes lc rgb "#D0D0D0" title "other paths", \\
     "%s" using 1:($1==%d ? $2 : NaN) with boxes lc rgb "#B2172B" title "winner", \\
     0.2 with lines dashtype 2 lc rgb "#555555" title "0.2 floor", \\
     "%s" using 1:($2+0.035*%.3f):($2>0.0005 ? sprintf("%%.2f",$2) : "") with labels font ",11" notitle

# ---- panel 2: direction criterion ----
set xrange [0.5:%d.5]
set yrange [0:%.3f]
set format x
set xtics (%s) font ",13"
set ylabel "row wins" font ",14"
set title "direction criterion (per-row votes)" font ",15"
set key outside top center horizontal
plot "%s" using 1:2 with boxes lc rgb "#D0D0D0" title "other paths", \\
     "%s" using 1:($1==%d ? $2 : NaN) with boxes lc rgb "#F5A682" title "wins leader", \\
     %s, \\
     "%s" using 1:($2+0.035*%.3f):($2>0 ? sprintf("%%d",$2) : "") with labels font ",11" notitle

unset multiplot
""" % (png_s, title, n, y1, mag_s, mag_s, winx, mag_s, y1,
       n, y2, tics, dir_s, dir_s, wtx, maj, dir_s, y2))
    subprocess.run(["gnuplot", plt], check=True)


def selfcheck():
    paths, vertices, idle_v, span_v, cap_v, unver = ab.load_model()
    model = (paths, vertices, idle_v, span_v, cap_v, unver)
    fails = []

    # Instance A: the 17 replays must reproduce replay_validate.py's
    # gate conclusions (same gates, same inputs - regression gate).
    print("instance A (17 replays):")
    for app, (exp, low, metric, note) in rv.EXPECT.items():
        lps = {p: [] for p in paths}
        idle_max = []
        wins = {}
        for path in rv.runs_of(app):
            run, out = ab.run_one(path, None, *model, verbose=False)
            for name, s, w, d, warns in out:
                if name == "idle":
                    vals = [v for v in s.values() if v is not None]
                    if vals:
                        idle_max.append(max(vals))
                else:
                    for p, v in s.items():
                        if v is not None:
                            lps[p].append(v)
                    for p, c in w.items():
                        wins[p] = wins.get(p, 0) + c
        med = {p: statistics.median(v) if v else None for p, v in lps.items()}
        nz = {p: v for p, v in med.items() if v is not None}
        top = max(nz, key=nz.get) if nz else None
        wtop = max(wins, key=wins.get) if wins else None
        imax = max(idle_max) if idle_max else None
        ok_idle = imax is None or imax <= 0.15
        judge = top if metric == "med" else wtop
        ok = judge == exp or (low and top is not None and nz[top] < 0.2)
        if low and metric == "wins" and wtop is not None:
            tot = sum(wins.values())
            if tot and wins[wtop] / tot < 0.5:
                ok = True
        status = "PASS" if (ok_idle and ok) else "FAIL"
        if status == "FAIL":
            fails.append("A:%s" % app)
        print("  %-12s idle=%-5s judge=%-5s exp=%-5s %s" %
              (app, "%.3f" % imax if imax is not None else "n/a",
               judge, exp, status))

    # Instance B: the sat six faces must match design doc sec 5.
    print("instance B (sat six faces):")
    for face, (exp, low, note) in SAT_EXPECT.items():
        csvs = [os.path.join(BENCH_RES, "%s_run%d.csv" % (face, r))
                for r in (1, 2, 3)]
        sc = search(face, csvs, model=model)
        ok = False
        if sc["verdict"] == "dominant":
            ok = sc["winner"] == exp
        elif sc["verdict"] in ("low", "multi"):
            ok = low and sc["winner"] is not None
        status = "PASS" if ok else "FAIL"
        if status == "FAIL":
            fails.append("B:%s" % face)
        print("  %-4s verdict=%-9s winner=%-4s exp=%-4s %s  (%s)" %
              (face, sc["verdict"], sc.get("winner"), exp, status, note))
    # Instance C: attribution self-consistency + known signatures
    # (P2.5b).  Gates: sum of mean contributions == row-mean L_p;
    # descending rank; stability counts bounded; D2 forensics
    # signature (arm/eswitch near-tie top-2, established 2026-09-22).
    print("instance C (attribution):")
    for name, csvs, vexp, top_vertex, near_tie in [
            ("e4_http_run2", ["results/e4/results/e4_http_run2.csv"],
             "dominant", {"arm", "eswitch"}, True),
            ("e2e_openssl", ["results/e2e/e2e_openssl_run1.csv"],
             "low", set(), False),
            ("e2e_mixed", ["results/e2e/e2e_mixed_run1.csv"],
             "multi", set(), False)]:
        sc = search(name, csvs, model=model)
        ok, msg = True, []
        if sc["verdict"] != vexp:
            ok = False
            msg.append("verdict %s != %s" % (sc["verdict"], vexp))
        att = sc.get("attrib") or []
        if vexp == "low":
            if att:
                ok = False
                msg.append("low scene has attribution")
        else:
            s = sum(a["c"] for a in att)
            lm = sc.get("lp_row_mean")
            if lm is None or abs(s - lm) > 0.02:
                ok = False
                msg.append("contrib sum %.3f != row-mean %s" % (s, lm))
            cs = [a["c"] for a in att]
            if any(cs[i] < cs[i + 1] for i in range(len(cs) - 1)):
                ok = False
                msg.append("rank not descending")
            for a in att:
                st, tot = a["stable"]
                if st > tot:
                    ok = False
                    msg.append("stable %d>%d" % (st, tot))
            if top_vertex and att and att[0]["vertex"] not in top_vertex:
                ok = False
                msg.append("top vertex %s not in %s"
                           % (att[0]["vertex"], top_vertex))
            if near_tie and len(att) >= 2 and \
                    abs(att[0]["c"] - att[1]["c"]) > 0.05:
                ok = False
                msg.append("expected near-tie %.3f vs %.3f"
                           % (att[0]["c"], att[1]["c"]))
        # P2.5d gates: grank sorted descending, empty for low scenes,
        # and no divergence note fires in these scenes (verified
        # empirically - the global peak flows to the winning path).
        gr = sc.get("grank") or []
        vs = [g["v"] for g in gr]
        if any(vs[i] < vs[i + 1] for i in range(len(vs) - 1)):
            ok = False
            msg.append("grank not descending")
        for g in gr:
            if g["obs"][0] > g["obs"][1] or g["obs"][1] <= 0:
                ok = False
                msg.append("obs %d>%d" % (g["obs"][0], g["obs"][1]))
        if vexp == "low" and gr:
            ok = False
            msg.append("low scene has grank")
        if sc.get("gnote"):
            ok = False
            msg.append("unexpected divergence note")
        status = "PASS" if ok else "FAIL"
        if status == "FAIL":
            fails.append("C:%s" % name)
        print("  %-14s verdict=%-9s top=%-8s %s%s" %
              (name, sc["verdict"], (att[0]["vertex"] if att else "-"),
               status, ("  " + "; ".join(msg)) if msg else ""))

    # Instance C5: divergence_notes rule on fabricated inputs (the
    # firing cases never occur in current data - sort's mss flows to
    # ib at 98% of the winner's top c, so the rule is exercised
    # synthetically).
    print("instance C5 (divergence rule):")
    syn = [
        ("N1+N2 fire", "cr",
         [{"vertex": "l3", "c": 0.115}],
         [{"vertex": "mss", "v": 0.130, "flow": "ib"}], 2),
        ("strict-> guard", "nad",
         [{"vertex": "arm", "c": 0.124}],
         [{"vertex": "eswitch", "v": 0.124, "flow": "nad"}], 0),
        ("self-compare skip", "cr",
         [{"vertex": "l3", "c": 0.115}],
         [{"vertex": "l3", "v": 0.114, "flow": "cr"}], 0),
    ]
    for name, winner, attrib, grank, exp in syn:
        got = len(divergence_notes(winner, attrib, grank))
        ok = got == exp
        if not ok:
            fails.append("C5:%s" % name)
        print("  %-18s notes=%d exp=%d %s" %
              (name, got, exp, "PASS" if ok else "FAIL"))

    # Instance D: gnuplot render smoke test (P2.5c).
    print("instance D (plot render):")
    import subprocess
    prefix = os.path.join(ab.ROOT, "reports", "_selfcheck_render")
    sc = search("e4_http_run2",
                ["results/e4/results/e4_http_run2.csv"], model=model)
    try:
        make_plot(sc, list(paths.keys()), prefix)
        ok = os.path.exists(prefix + ".png") and \
            os.path.getsize(prefix + ".png") > 10000
    except Exception as e:
        ok = False
        print("  render error: %s" % e)
    for ext in ("_mag.dat", "_dir.dat", ".plt", ".png"):
        try:
            os.remove(prefix + ext)
        except OSError:
            pass
    status = "PASS" if ok else "FAIL"
    if not ok:
        fails.append("D:render")
    print("  e4_http_run2 render %s" % status)

    print("selfcheck: %s" % ("ALL PASS" if not fails else
                             "FAILURES: " + ",".join(fails)))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="*")
    ap.add_argument("--scene", default=None)
    ap.add_argument("--pipe")
    ap.add_argument("--json")
    ap.add_argument("--plot", help="PREFIX for .dat/.plt/.png figure")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()

    if args.selfcheck:
        sys.exit(selfcheck())

    if not args.csv:
        ap.error("csv required (or --selfcheck)")
    paths, vertices, idle_v, span_v, cap_v, unver = ab.load_model()
    scene = args.scene or os.path.basename(args.csv[0])
    sc = search(scene, args.csv, args.pipe,
                (paths, vertices, idle_v, span_v, cap_v, unver))
    if args.plot:
        make_plot(sc, list(paths.keys()), args.plot)
        print("figure written: %s.png" % args.plot)
    report(sc, paths)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(sc, f, ensure_ascii=False, indent=2)
        print("evidence JSON written: %s" % args.json)


if __name__ == "__main__":
    main()
