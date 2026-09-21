#!/usr/bin/env python3
# verify_nad_nhd.py - verify the e1 NAD/NHD figure CSVs against opsheet
# section 7 criteria and emit fig/e1_nad_nhd.dat for the main figure.
#
# Usage: python tools/verify_nad_nhd.py   (from the repo root)
#
# IMPORTANT (collector 8ff40c4 semantics): the per-interface columns in
# these CSVs are PER-INTERVAL byte deltas (sampled every 1 s, negative
# deltas clamped to 0), NOT cumulative counters.  Do not difference
# them again.  Rate (bps) = stored_bytes * 8 / dt, dt from the
# timestamp gap to the previous row (normally 1 s).
#
# Blocks: N0 2a/2b (56.x cal), N1 1g/5g/10g/20g (p1 cal), G3
# (Redis-Arm), G7 (Redis+SQLite), N2 (Redis-Host).
# Figure series: Host = eSwitch->Host (pf1hpf_tx), Arm = eSwitch->Arm
# (en3f1pf1sf0_tx).  App-run means are window means minus the PRE-idle
# baseline (5 s before the app phase), median across 3 runs.  The
# post-idle 5 s is NOT used: the benchmark (3M SET + 3M GET at ~66k
# ops/s from the Arm client) runs longer than the 40 s app window and
# contaminates the post-idle interval (verified 9/15: pre-idle is
# clean ~0.000 Gbps in every run, post-idle ~= app rate).

import csv
import os
import statistics

RES = 'results'
NET = ['net_rx_bytes', 'net_tx_bytes',
       'pf0hpf_rx_bytes', 'pf0hpf_tx_bytes',
       'pf1hpf_rx_bytes', 'pf1hpf_tx_bytes',
       'p1_rx_bytes', 'p1_tx_bytes',
       'en3f1pf1sf0_rx_bytes', 'en3f1pf1sf0_tx_bytes',
       'enp3s0f1s0_rx_bytes', 'enp3s0f1s0_tx_bytes']


def load(fn):
    with open(fn, newline='') as f:
        rows = list(csv.DictReader(f))
    out = []
    prev_t = None
    for r in rows:
        t = int(r['timestamp'])
        dt = (t - prev_t) if prev_t is not None else 1
        prev_t = t
        out.append((t, dt, {c: int(r[c]) for c in NET}))
    return out


def phase_window(fn):
    p = {}
    with open(fn) as f:
        for line in f:
            k, _, v = line.strip().partition('=')
            if k:
                p[k] = int(v)
    return p['start'], p['app_start'], p['app_end'], p['end']


def g(v):
    return v / 1e9


def rate(row, c):
    """bps from a stored per-interval byte delta."""
    _, dt, d = row
    return d[c] * 8.0 / dt


def mean_rate(rows, key=None):
    """Mean bps over rows (optionally filtered by key rate > 50% of max)."""
    sel = rows
    if key is not None:
        mx = max(rate(r, key) for r in rows)
        if mx <= 0:
            return None, 0
        sel = [r for r in rows if rate(r, key) > 0.5 * mx]
        if not sel:
            return None, 0
    m = {c: statistics.mean([rate(r, c) for r in sel]) for c in NET}
    return m, len(sel)


def rows_between(rows, t0, t1):
    return [r for r in rows if t0 < r[0] <= t1]


def fmt(v):
    return '%.3f' % g(v)


def report_row(name, m, cols, expect=''):
    vals = '  '.join('%s=%s' % (c, fmt(m[c])) for c in cols)
    print('%-16s %s   %s' % (name, vals, expect))


print('=== 1. N0: NAD cal (56.x bidirectional, 10 Gbps) ===')
for tag, key, cols, expect in [
        ('n0_2a (h->Arm)', 'pf1hpf_rx_bytes',
         ['pf1hpf_rx_bytes', 'enp3s0f1s0_rx_bytes', 'en3f1pf1sf0_tx_bytes',
          'p1_rx_bytes'], 'expect ~6/6/6/~0 (Arm ingress cap ~6 Gbps)'),
        ('n0_2b (Arm->h)', 'enp3s0f1s0_tx_bytes',
         ['enp3s0f1s0_tx_bytes', 'en3f1pf1sf0_rx_bytes', 'pf1hpf_tx_bytes',
          'p1_tx_bytes'], 'expect 9.4/9.4/9.4/~0')]:
    rows = load(os.path.join(RES, 'e1_%s.csv' % tag.split()[0]))
    m, nsec = mean_rate(rows, key)
    report_row(tag, m, cols, expect + ' (%ds)' % nsec)

print()
print('=== 2. N1: NHD cal (p1, 4 rates) ===')
n1 = {}
for tag, nominal in [('1g', 1.0), ('5g', 5.0), ('10g', 10.0), ('20g', 20.0)]:
    rows = load(os.path.join(RES, 'e1_n1_%s.csv' % tag))
    m, nsec = mean_rate(rows, 'p1_rx_bytes')
    n1[tag] = m
    report_row('n1_%s' % tag, m,
               ['p1_rx_bytes', 'pf1hpf_tx_bytes', 'en3f1pf1sf0_tx_bytes'],
               'expect %.0f/%.0f/~0 (%ds)' % (nominal, nominal, nsec))

print()
print('=== 3. G3 / G7 / N2: app runs (window mean - PRE-idle baseline) ===')


def app_stats(tag):
    means = []
    for i in (1, 2, 3):
        base = os.path.join(RES, 'e1_%s_run%d' % (tag, i))
        rows = load(base + '.csv')
        start, app_start, app_end, end = phase_window(base + '.csv.phase.log')
        app, _ = mean_rate(rows_between(rows, app_start, app_end))
        # PRE-idle only: the benchmark (3M+3M ops) outlasts the 40 s app
        # window, so the post-idle interval still carries app traffic.
        idle = mean_rate(rows_between(rows, start, app_start))[0]
        net = {c: app[c] - idle[c] for c in NET}
        means.append(net)
        cols = (['p1_rx_bytes', 'pf1hpf_tx_bytes', 'p1_tx_bytes',
                 'pf1hpf_rx_bytes', 'en3f1pf1sf0_tx_bytes'] if tag == 'n2'
                else ['pf1hpf_rx_bytes', 'enp3s0f1s0_rx_bytes',
                      'en3f1pf1sf0_tx_bytes', 'p1_rx_bytes'])
        report_row('%s_run%d' % (tag, i), net, cols, 'run%d' % i)
    return {c: statistics.median([m[c] for m in means]) for c in NET}


g3 = app_stats('g3')
g7 = app_stats('g7')
n2 = app_stats('n2')

print()
print('=== 4. Figure series (median across runs; cal from 2a / n1_10g) ===')
fig = []
rows = load(os.path.join(RES, 'e1_n0_2a.csv'))
m0, _ = mean_rate(rows, 'pf1hpf_rx_bytes')
fig.append(('iperf-NAD', g(m0['pf1hpf_tx_bytes']),
            g(m0['en3f1pf1sf0_tx_bytes'])))
fig.append(('iperf-NHD', g(n1['10g']['pf1hpf_tx_bytes']),
            g(n1['10g']['en3f1pf1sf0_tx_bytes'])))
fig.append(('Redis-Arm', g(g3['pf1hpf_tx_bytes']),
            g(g3['en3f1pf1sf0_tx_bytes'])))
fig.append(('Redis+SQLite', g(g7['pf1hpf_tx_bytes']),
            g(g7['en3f1pf1sf0_tx_bytes'])))
fig.append(('Redis-Host', g(n2['pf1hpf_tx_bytes']),
            g(n2['en3f1pf1sf0_tx_bytes'])))

with open('fig/e1_nad_nhd.dat', 'w') as f:
    for label, host, arm in fig:
        # 2e-4: visible "effectively zero" stub just above the log-axis
        # bottom (1e-4); a true 0 bar would be invisible on a log axis.
        f.write('%s %.4f %.4f\n' % (label, max(host, 2e-4), max(arm, 2e-4)))

print('%-16s %10s %10s' % ('workload', 'Host Gbps', 'Arm Gbps'))
for label, host, arm in fig:
    print('%-16s %10.3f %10.3f' % (label, host, arm))
print()
print('dat written: fig/e1_nad_nhd.dat')

print()
print('=== 5. N1 linearity (throughput vs nominal) ===')
for tag, nominal in [('1g', 1.0), ('5g', 5.0), ('10g', 10.0), ('20g', 20.0)]:
    print('%-5s nominal %4.1f  p1_rx %6.2f  pf1hpf_tx %6.2f' %
          (tag, nominal, g(n1[tag]['p1_rx_bytes']),
           g(n1[tag]['pf1hpf_tx_bytes'])))
