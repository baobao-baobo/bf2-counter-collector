#!/usr/bin/env python3
"""analyze_bottleneck.py - BF2-PF three-layer busyness index (P2).

Layer 1: per-counter normalization n_c in [0,1] (six families,
design doc sec 4.6).  Layer 2: vertex fusion v_j = max(n_c) with
companion mean m_j and provenance labels.  Layer 3: path index
L_p = sum of v_j over the path's vertices, shared vertices split by
entry-rate shares (M1).

Evidence tiers (arbitration / provenance only, NOT weights):
  1 pressure: af, empty
  2 busy:     span, cap
  3 error:    drops
  4 eff:      miss
Counters listed in anchor_sat.conf [unverified] are computed but
kept OUT of arbitration (availability gate).

Inputs: one or more collector CSVs (+ .phase.log when present),
optional pipe CSV (tools/collect_pipe.sh output, raw accumulators).

Usage:
  analyze_bottleneck.py results/g1_run1.csv [CSV ...]
      [--pipe pipe.csv] [--out L.csv] [--selfcheck]
"""

import argparse
import calendar
import configparser
import csv
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = os.path.join(ROOT, "configs")

NET_THRESH = 1e6
TIER = {"af": 1, "empty": 1, "span": 2, "cap": 2, "drops": 3, "miss": 4}
DEFAULT_DENOM = ["l3half0_cycles", "l3half1_cycles"]

TOK = re.compile(
    r"^(?P<opt>\?)?(?:(?P<src>[A-Za-z0-9_]+):)?(?P<expr>[A-Za-z0-9_+]*)"
    r"@(?P<fam>[a-z]+)(?::(?P<args>.*))?$")


def clamp01(x):
    if x is None:
        return None
    return max(0.0, min(1.0, x))


def num(cell):
    try:
        return float(cell)
    except (ValueError, TypeError):
        return None


class Counter:
    __slots__ = ("opt", "src", "expr", "fam", "args")

    def __init__(self, tok):
        m = TOK.match(tok)
        if not m:
            raise ValueError("bad counter token: %s" % tok)
        self.opt = m.group("opt") is not None
        self.src = m.group("src")
        self.expr = m.group("expr")
        self.fam = m.group("fam")
        self.args = {}
        if m.group("args"):
            for pair in m.group("args").split(","):
                k, _, v = pair.partition("=")
                if k and v:
                    self.args[k] = v

    @property
    def components(self):
        return [p for p in self.expr.split("+") if p]

    def denom_expr(self):
        return self.args.get("denom", "+".join(DEFAULT_DENOM))


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


def load_ini(path):
    cp = configparser.ConfigParser(delimiters=("=",),
                                   inline_comment_prefixes=("#",))
    cp.optionxform = str
    cp.read(path)
    return cp


def load_model():
    pt = load_ini(os.path.join(CFG, "path_table.conf"))
    order = [p.strip() for p in pt["paths"]["order"].split(",")]
    paths = {}
    for name in order:
        sec = pt["path." + name]
        paths[name] = {
            "name": name,
            "class": sec["class"],
            "design": sec["design"],
            "entry": sec["entry"],
            "vertices": [v.strip() for v in sec["vertices"].split(",")],
        }
    vertices = {}
    for name in paths:
        for v in paths[name]["vertices"]:
            if v not in vertices:
                sec = pt["vertex." + v]
                vertices[v] = {
                    "name": v,
                    "counters": [Counter(t) for t in sec["counters"].split()],
                }
    idle = load_ini(os.path.join(CFG, "anchor_idle.conf"))
    idle_v = {k: float(idle["idle"][k]) for k in idle["idle"]}
    sat = load_ini(os.path.join(CFG, "anchor_sat.conf"))
    span_v = {k: float(sat["span"][k]) for k in sat["span"]}
    cap_v = {k: float(sat["cap"][k]) for k in sat["cap"]}
    unver = set(sat["unverified"]) if sat.has_section("unverified") else set()
    return paths, vertices, idle_v, span_v, cap_v, unver


class Run:
    """One CSV run with per-row value access and windowing."""

    def __init__(self, path, pipe_path=None):
        self.name = os.path.basename(path)
        self.header, self.rows, self.idx = read_csv(path)
        logp = path + ".phase.log"
        if os.path.exists(logp):
            self.ph = parse_phase(logp)
            ts = [int(r[0]) for r in self.rows]
            self.idle_i = [i for i, t in enumerate(ts)
                           if not (self.ph["app_start"] < t <= self.ph["app_end"])
                           and self.net_quiet(i)]
            self.app_i = [i for i in range(len(ts))
                          if self.ph["app_start"] < ts[i] <= self.ph["app_end"]]
        else:
            # continuous-load calibration runs: no idle window exists,
            # head/tail trimming is for the app window only
            self.ph = None
            n = len(self.rows)
            self.app_i = list(range(min(5, n), max(5, n - 5)))
            self.idle_i = []
        self.pipe = {}
        if pipe_path:
            self.load_pipe(pipe_path)

    def net_quiet(self, i):
        j = self.idx.get("net_rx_bytes")
        if j is None:
            return True
        v = num(self.rows[i][j]) if j < len(self.rows[i]) else None
        return v is None or v < NET_THRESH

    def load_pipe(self, path):
        """Pipe CSV: raw accumulators, wall-clock ts.  Convert to
        epoch and per-second deltas (rate)."""
        header, rows, idx = read_csv(path)
        acc = []
        for r in rows:
            try:
                t = calendar.timegm(time.strptime(r[0], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                continue
            acc.append((t, r))
        for c in header[1:]:
            j = idx[c]
            rates = {}
            for k in range(1, len(acc)):
                t0, r0 = acc[k - 1]
                t1, r1 = acc[k]
                dt = t1 - t0
                if dt <= 0:
                    continue
                d = (num(r1[j]) or 0.0) - (num(r0[j]) or 0.0)
                if d < 0:      # rule re-add reset: unusable window
                    continue
                rates[t1] = d / dt
            self.pipe[c] = rates

    def pipe_rate(self, col, ts):
        """Nearest pipe sample within +-1 s of ts (epoch)."""
        rates = self.pipe.get(col)
        if not rates:
            return None
        for d in (0, 1, -1, 2, -2):
            v = rates.get(ts + d)
            if v is not None:
                return v
        return None

    def col(self, i, c):
        j = self.idx.get(c)
        if j is None or j >= len(self.rows[i]):
            return None
        return num(self.rows[i][j])

    def eval(self, i, ctr):
        """Expression value for counter ctr at row i."""
        if ctr.src == "pipe":
            return self.pipe_rate(ctr.expr, int(self.rows[i][0]))
        vals = [self.col(i, c) for c in ctr.components]
        if any(v is None for v in vals):
            return None
        return sum(vals)

    def anchor_sum(self, table, ctr):
        """Sum of per-component anchors (idle/sat add over a sum
        expression).  Pipe counters are keyed with their src prefix
        (pipe:p1_bytes)."""
        vals = [table.get((ctr.src + ":") + c if ctr.src else c)
                for c in ctr.components]
        if any(v is None for v in vals):
            return None
        return sum(vals)


class Analyzer:
    def __init__(self, paths, vertices, idle_v, span_v, cap_v, unver):
        self.paths = paths
        self.vertices = vertices
        self.idle_v = idle_v
        self.span_v = span_v
        self.cap_v = cap_v
        self.unver = unver
        # vertices -> owning paths (for M1 shares)
        self.owners = {}
        for p in paths.values():
            for v in p["vertices"]:
                self.owners.setdefault(v, []).append(p["name"])

    def n_of(self, run, i, ctr):
        """Layer-1 normalized pressure n in [0,1] (None = missing)."""
        val = run.eval(i, ctr)
        if val is None:
            return None
        f = ctr.fam
        if f == "span":
            idle = run.anchor_sum(self.idle_v, ctr)
            sat = run.anchor_sum(self.span_v, ctr)
            if idle is None or sat is None or sat <= idle:
                return None
            return clamp01((val - idle) / (sat - idle))
        if f == "cap":
            c = run.anchor_sum(self.cap_v, ctr)
            if c is None or c <= 0:
                return None
            return clamp01(val / c)
        if f == "af":
            denom = Counter("%s@span" % ctr.denom_expr())
            dval = run.eval(i, denom)
            if dval is None or dval <= 0:
                return None
            return clamp01(val / dval)
        if f == "empty":
            denom = Counter("%s@span" % ctr.denom_expr())
            dval = run.eval(i, denom)
            if dval is None or dval <= 0:
                return None
            return clamp01(1.0 - val / dval)
        if f == "miss":
            hit = run.eval(i, Counter("%s@span" % ctr.args.get("hit", "")))
            total = run.eval(i, Counter("%s@span" % ctr.args.get("total", "")))
            if hit is None or total is None or total <= 0:
                return None
            return clamp01(1.0 - hit / total)
        if f == "drops":
            return None      # not wired: ethtool -S columns not collected
        raise ValueError("unknown family %s" % f)

    def entry_rate(self, run, i, pname):
        """Raw entry rate for M1 shares (None = path not observable)."""
        ctr = Counter(self.paths[pname]["entry"] + "@span")
        return run.eval(i, ctr)

    def vertex_row(self, run, i, vname):
        """Layer-2 fusion for vertex vname at row i.

        Returns (v, m, label, excluded) where label describes the
        argmax counter; excluded = counters computed but gated out
        (unverified / unanchored).
        """
        vert = self.vertices[vname]
        best = (None, None, None)   # (n, tier, label)
        ns = []
        excluded = []
        for ctr in vert["counters"]:
            n = self.n_of(run, i, ctr)
            if n is None:
                continue
            gated = any(c in self.unver for c in ctr.components)
            label = "%s@%s" % (ctr.fam, ctr.expr)
            if gated:
                excluded.append((label, n))
                continue
            ns.append(n)
            tier = TIER[ctr.fam]
            if best[0] is None or n > best[0]:
                best = (n, tier, label)
        if not ns and not excluded:
            return None, None, None, []
        m = sum(ns) / len(ns) if ns else None
        if best[0] is None:         # everything gated: no arbitration
            return None, m, None, excluded
        return best[0], m, "%s:%s" % (best[1], best[2]), excluded

    def row_lp(self, run, i):
        """Layer-3 path indices for row i."""
        entry = {}
        for p in self.paths:
            entry[p] = self.entry_rate(run, i, p)
        lp = {}
        vinfo = {}
        warn = []
        for pname, p in self.paths.items():
            if entry[pname] is None:
                continue
            total = 0.0
            parts = []
            for vname in p["vertices"]:
                v, m, label, excl = self.vertex_row(run, i, vname)
                if v is None:
                    parts.append((vname, None, None, label, excl))
                    continue
                # M1 share among paths owning this vertex
                own = self.owners[vname]
                denom = sum(entry[q] for q in own if entry[q] is not None)
                share = 1.0
                if len(own) > 1 and denom > 0:
                    share = entry[pname] / denom
                total += v * share
                parts.append((vname, v, share, label, excl))
                # arbitration warning: a stronger-tier counter within
                # 0.05 of the argmax (lower tier number = stronger)
                tier_arg = int(label.split(":")[0]) if label else 9
                for ctr in self.vertices[vname]["counters"]:
                    n = self.n_of(run, i, ctr)
                    if n is None or TIER[ctr.fam] >= tier_arg:
                        continue
                    if any(c in self.unver for c in ctr.components):
                        continue
                    if v - n <= 0.05:
                        warn.append("%s: %s@%s=%s rivals %s=%s"
                                    % (vname, ctr.fam, ctr.expr,
                                       round(n, 3), label, round(v, 3)))
            lp[pname] = (total, parts)
        return lp, warn


def summarize(paths, lp_rows):
    """Mean L_p per path over rows (None rows skipped)."""
    out = {}
    for p in paths:
        vals = [r[p][0] for r in lp_rows if p in r and r[p][0] is not None]
        out[p] = sum(vals) / len(vals) if vals else None
    return out


def fmt_lp(lp):
    return " ".join("%s=%.3f" % (k, v) for k, v in sorted(lp.items())
                    if v is not None)


def run_one(path, pipe_path, paths, vertices, idle_v, span_v, cap_v, unver,
            verbose=True):
    run = Run(path, pipe_path)
    an = Analyzer(paths, vertices, idle_v, span_v, cap_v, unver)
    out = []
    for name, rows in (("idle", run.idle_i), ("app", run.app_i)):
        if not rows:
            continue
        lp_rows = []
        warns = set()
        wins = {}
        dom = {}
        for i in rows:
            lp, w = an.row_lp(run, i)
            lp_rows.append(lp)
            warns |= set(w)
            nz = [p for p, t in lp.items() if t[0] is not None]
            if nz:
                top = max(nz, key=lambda p: lp[p][0])
                wins[top] = wins.get(top, 0) + 1
            for p, t in lp.items():
                if t[0] is None:
                    continue
                for vname, v, share, label, excl in t[1]:
                    if label:
                        dom[label] = dom.get(label, 0) + 1
        s = summarize(paths, lp_rows)
        out.append((name, s, wins, dom, warns))
    return run, out


def print_report(run, out, paths, verbose=True):
    print("== %s ==" % run.name)
    for name, s, wins, dom, warns in out:
        if name == "idle" and not s:
            continue
        if name == "app":
            skipped = [p for p in paths if s[p] is None]
            print("[app]  L: %s" % fmt_lp(s))
            if skipped:
                print("  missing: %s" % " ".join(skipped))
            print("  wins: %s" % " ".join("%s=%d" % kv for kv in
                                          sorted(wins.items())))
            if dom:
                print("  dominant: %s" % " ".join(
                    "%s(%d)" % kv for kv in sorted(dom.items(),
                                                   key=lambda kv: -kv[1])[:6]))
            if warns:
                print("  warn:")
                for w in sorted(warns):
                    print("    " + w)
        else:
            print("[idle] L: %s" % fmt_lp(s))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--pipe")
    ap.add_argument("--out")
    ap.add_argument("--selfcheck", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    paths, vertices, idle_v, span_v, cap_v, unver = load_model()

    if args.selfcheck:
        # hand check: xz A72_ACCESS app mean vs HNF span anchors
        run = Run(args.csv[0], args.pipe)
        rates = [run.col(i, "tile_a72_access") for i in run.app_i]
        rates = [v for v in rates if v is not None]
        mean = sum(rates) / len(rates) if rates else 0.0
        idle = idle_v["tile_a72_access"]
        sat = span_v["tile_a72_access"]
        n_hand = clamp01((mean - idle) / (sat - idle))
        n_eng = 0.0
        cnt = 0
        for i in run.app_i:
            ctr = Counter("tile_a72_access@span")
            v = Analyzer(paths, vertices, idle_v, span_v, cap_v,
                         unver).n_of(run, i, ctr)
            if v is not None:
                n_eng += v
                cnt += 1
        n_eng /= cnt if cnt else 1
        print("selfcheck tile_a72_access: mean=%.1fM idle=%.1fM sat=%.1fM"
              % (mean / 1e6, idle / 1e6, sat / 1e6))
        print("  hand n=%.3f  engine n=%.3f  %s (expect ~0.34 for xz)"
              % (n_hand, n_eng,
                 "PASS" if abs(n_hand - n_eng) < 1e-9 else "MISMATCH"))
        return

    if args.out:
        outf = open(args.out, "w", newline="")
        outw = csv.writer(outf)
        outw.writerow(["ts"] + list(paths.keys()))

    for path in args.csv:
        run, out = run_one(path, args.pipe, paths, vertices, idle_v,
                           span_v, cap_v, unver, not args.quiet)
        print_report(run, out, paths, not args.quiet)
        if args.out:
            for i in run.app_i:
                lp, _ = Analyzer(paths, vertices, idle_v, span_v, cap_v,
                                 unver).row_lp(run, i)
                outw.writerow([run.rows[i][0]] +
                              ["" if p not in lp or lp[p][0] is None
                               else "%.6f" % lp[p][0] for p in paths])
    if args.out:
        outf.close()
        print("per-row L_p written: %s" % args.out)


if __name__ == "__main__":
    main()
