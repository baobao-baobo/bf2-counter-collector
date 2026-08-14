#!/usr/bin/env python3
"""CSV invariant checker for bf2-counter-collector output.

Checks:
  1. Header fields are unique.
  2. Every row has the same number of fields as the header.
  3. Timestamps are non-decreasing.
  4. With --period N --cols GLOB...: the matched columns must be
     filled exactly on rows r where r % N == N-1 (0-based data row
     index) and empty everywhere else.  This mirrors the collector's
     sampling rule `tick % k == k-1`.

Usage:
  python3 tools/check_csv.py FILE
  python3 tools/check_csv.py FILE --period 2 --cols 'tile_*' 'l3half*'

Exit code: 0 = all checks passed, 1 = any check failed.
Stdlib only.
"""

import argparse
import csv
import fnmatch
import sys


def fail(msg):
    print("FAIL:", msg)
    return False


def main():
    ap = argparse.ArgumentParser(
        description="Invariant checker for bf2-counter-collector CSV output")
    ap.add_argument("file", help="CSV file produced by collect_all")
    ap.add_argument("--period", type=int, default=None,
                    help="sampling period in ticks (k) for --cols cadence check")
    ap.add_argument("--cols", nargs="*", default=[],
                    help="column globs; with --period, verify they are "
                         "filled exactly on rows r %% N == N-1")
    args = ap.parse_args()

    if args.period is not None and not args.cols:
        ap.error("--period requires --cols")
    if args.period is not None and args.period < 1:
        ap.error("--period must be >= 1")

    try:
        with open(args.file, newline="") as f:
            rows = list(csv.reader(f))
    except OSError as e:
        print("FAIL: cannot read {}: {}".format(args.file, e))
        return 1

    ok = True

    # --- structure ---
    if len(rows) < 2:
        print("FAIL: expected at least a header and one data row, "
              "got {} lines".format(len(rows)))
        return 1

    header, data = rows[0], rows[1:]
    print("file: {}".format(args.file))
    print("rows: {} ({} data)".format(len(rows), len(data)))
    print("columns: {}".format(len(header)))

    seen = set()
    dup = [h for h in header if h in seen or seen.add(h)]
    if dup:
        ok = fail("duplicate header fields: {}".format(dup))

    for i, row in enumerate(data, start=1):
        if len(row) != len(header):
            ok = fail("row {} has {} fields, header has {}".format(
                i, len(row), len(header)))

    if header[0] != "timestamp":
        ok = fail("first column should be 'timestamp', got '{}'".format(
            header[0]))

    prev = None
    for i, row in enumerate(data, start=1):
        try:
            ts = int(row[0])
        except ValueError:
            ok = fail("row {} first field is not an integer "
                      "timestamp ('{}')".format(i, row[0]))
            continue
        if prev is not None and ts < prev:
            ok = fail("timestamp decreased at row {} ({} < {})".format(
                i, ts, prev))
        prev = ts

    # --- cadence check ---
    if args.period is not None:
        N = args.period
        matched = [c for c in header
                   if any(fnmatch.fnmatchcase(c, g) for g in args.cols)]
        if not matched:
            print("WARN: --cols matched no columns in this file")
        print("cadence: period {}, {} matched column(s): {}".format(
            N, len(matched), ", ".join(matched)))

        for col in matched:
            ci = header.index(col)
            for i, row in enumerate(data):  # i = 0-based tick index
                filled = row[ci] != ""
                expected = (i % N) == (N - 1)
                if filled != expected:
                    ok = fail("{}: row {} (tick {}) is {} but should be "
                              "{}".format(col, i + 1, i,
                                          "filled" if filled else "empty",
                                          "filled" if expected else "empty"))

    if ok:
        print("PASS: all checks passed")
        return 0
    print("FAIL: one or more checks failed")
    return 1


if __name__ == "__main__":
    sys.exit(main())
