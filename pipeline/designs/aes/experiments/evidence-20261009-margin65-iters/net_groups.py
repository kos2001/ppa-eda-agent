#!/usr/bin/env python3
"""Compare routed wire length and sink count across groups of nets by diode count.

    python3 net_groups.py <net_wire_diodes.csv.gz>

The table is made from the final DEF by def_nets.py (wire length summed per
layer from the ROUTED segments, diodes and real sinks from the connection
lists). The DEF itself is not archived (19 MB per run).
"""
import csv, gzip, statistics, sys


def rows(path):
    with gzip.open(path, "rt", newline="") as f:
        for r in csv.DictReader(f):
            yield {k: (float(v) if k not in ("net",) else v) for k, v in r.items()}


def line(label, group):
    group = [r for r in group if r["wire_um"] > 0]
    L = sorted(r["wire_um"] for r in group)
    return (f"{label:36s} n={len(group):5d} | wire um median {statistics.median(L):7.1f} "
            f"p90 {L[int(.9 * len(L))]:7.1f} max {L[-1]:7.1f} | sinks median "
            f"{statistics.median(r['real_sinks'] for r in group):.0f}")


if __name__ == "__main__":
    data = list(rows(sys.argv[1]))
    cap = [r for r in data if r["diodes"] >= 10]
    print(line("nets with >=10 diodes (cap-hit)", cap))
    print(line("initial real-rule violators", [r for r in data if r["initial_real_rule_violator"]]))
    print(line("nets with exactly 1 diode", [r for r in data if r["diodes"] == 1]))
    print(line("nets with 2-9 diodes", [r for r in data if 2 <= r["diodes"] <= 9]))
    print(line("nets with no diode", [r for r in data if r["diodes"] == 0]))
    print(f"cap-hit nets that were initial violators: {sum(1 for r in cap if r['initial_real_rule_violator'])} of {len(cap)}")
