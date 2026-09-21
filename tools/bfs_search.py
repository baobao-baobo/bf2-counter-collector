#!/usr/bin/env python3
"""bfs_search.py - scene-level busy-path search (P2.5).

Search layer over the three-layer index, per docs/bfs-search-design.md:
given a scene window (one or more collector CSVs), answer "which data
path is busiest right now?" with a reusable evidence chain.  The
engine (load_model / Run / Analyzer / run_one of analyze_bottleneck.py)
is reused verbatim - this file only adds scene aggregation, judgment
rules, and the evidence-chain report.

Judgment (design doc sec 3; the replay gates formalized):
  dominant  judge path leads the median ranking
  low       med leader < 0.2, or the wins leader lacks a majority of
            rows (e1_g7 cr 36 : nad 35 precedent, 2026-09-20 tie rule)

Evidence chain (design doc sec 4):
  verdict + confidence shape, path ranking (median L_p), vertex
  contribution decomposition of the busy path (median v_j, argmax
  counter, provenance tier from anchor_sat.conf comments), warnings
  (arbitration conflicts, SAT-SUSPECT anchors, [unverified] gated
  counters, blind-spot circumstantial evidence, degraded wins).

Usage:
  bfs_search.py <csv...> [--scene NAME] [--json out.json] [--pipe pipe.csv]
  bfs_search.py --selfcheck
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
    imax = None
    for path in csvs:
        run, out = ab.run_one(path, pipe, paths, vertices, idle_v,
                              span_v, cap_v, unver, verbose=False)
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
                for vname, v, share, label, excl in t[1]:
                    if v is None:
                        continue
                    key = (p, vname)
                    vrow.setdefault(key, []).append(v)
                    if label:
                        vlab.setdefault(key, {}).setdefault(
                            label, []).append(v)
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
        verdict, winner = "low", top
    else:
        verdict, winner = "dominant", top

    ranking = sorted(nz.items(), key=lambda kv: -kv[1])
    decomp = []
    for vname in paths[winner]["vertices"]:
        key = (winner, vname)
        vals = vrow.get(key)
        if vals is None:
            continue
        mv = statistics.median(vals)
        label = None
        mn = None
        if vlab.get(key):
            label = max(vlab[key], key=lambda l: len(vlab[key][l]))
            mn = statistics.median(vlab[key][label])
        ctr = [c for c in vertices[vname]["counters"]
               if "%s@%s" % (c.fam, c.expr) == label.split(":", 1)[1]]
        tier, note = prov_of(ctr[0], prov) if ctr else (1, "unverified")
        excl = vertices[vname]["counters"]
        gated = sum(1 for c in excl
                    if any(x in unver for x in c.components))
        item = {"vertex": vname, "v": round(mv, 3), "label": label,
                "n": round(mn, 3) if mn is not None else None,
                "prov": note, "prov_tier": tier}
        if gated:
            item["unverified_gated"] = gated
        decomp.append(item)

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
            "ranking": [(p, round(v, 3)) for p, v in ranking],
            "decomposition": decomp, "warnings": warnings,
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
        elif sc["verdict"] == "low":
            ok = low and sc["winner"] is not None
        status = "PASS" if ok else "FAIL"
        if status == "FAIL":
            fails.append("B:%s" % face)
        print("  %-4s verdict=%-9s winner=%-4s exp=%-4s %s  (%s)" %
              (face, sc["verdict"], sc.get("winner"), exp, status, note))
    print("selfcheck: %s" % ("ALL PASS" if not fails else
                             "FAILURES: " + ",".join(fails)))
    return 0 if not fails else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="*")
    ap.add_argument("--scene", default=None)
    ap.add_argument("--pipe")
    ap.add_argument("--json")
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
    report(sc, paths)
    if args.json:
        with open(args.json, "w") as f:
            json.dump(sc, f, ensure_ascii=False, indent=2)
        print("evidence JSON written: %s" % args.json)


if __name__ == "__main__":
    main()
