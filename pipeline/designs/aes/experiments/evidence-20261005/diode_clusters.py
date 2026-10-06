#!/usr/bin/env python3
"""Where the antenna diodes sit in a final netlist, as a net -> sink graph.

For each run directory (as archived beside this script) report:
  1. diodes per net, and for the nets holding 8 or more: how many real sinks
     they drive, their driver cells and the sink cell/pin pairs;
  2. whether a net's final diode count follows how badly its pin violated in
     the first antenna check (report 39), as a Pearson correlation.

    python3 diode_clusters.py aes-coupling-20261005-hul-h15-both [more dirs]

Reads files only. Uses violator_sinks.py's netlist reader.
"""
import collections
import re
import statistics
import sys

import violator_sinks as vs


def graph(insts):
    nets = collections.defaultdict(lambda: {"sinks": [], "diodes": 0, "driver": None})
    for cell, name, pins in insts:
        short = cell.split("__", 1)[1]
        for pin, net in pins.items():
            if "diode" in cell:
                nets[net]["diodes"] += 1
            elif pin in vs.OUT:
                nets[net]["driver"] = short
            else:
                nets[net]["sinks"].append((short, pin))
    return nets


def first_check(path):
    rows = []
    for line in open(path, encoding="utf-8"):
        c = [x.strip() for x in line.strip().strip("│").split("│")]
        if len(c) >= 6 and re.fullmatch(r"[\d.]+", c[0]):
            rows.append((float(c[0]), c[3].replace("\\", ""), c[4]))
    return rows


def report(directory):
    nets = graph(vs.netlist(f"{directory}/aes_cipher_top.nl.v.gz"))
    with_diode = {n: v for n, v in nets.items() if v["diodes"]}
    big = {n: v for n, v in with_diode.items() if v["diodes"] >= 8}
    print(f"== {directory}")
    print(f"diodes {sum(v['diodes'] for v in with_diode.values())} on {len(with_diode)} nets; "
          f"{len(big)} nets hold 8 or more "
          f"({sum(v['diodes'] for v in big.values())} diodes)")
    print("  diodes per net:", sorted(collections.Counter(v["diodes"] for v in with_diode.values()).items()))
    print("  real sinks on the 8+ nets:", sorted(collections.Counter(len(v["sinks"]) for v in big.values()).items()))
    print("  driver -> sink on the 8+ nets:")
    for key, n in collections.Counter(
            (v["driver"], tuple(v["sinks"])) for v in big.values()).most_common(6):
        print(f"    {n} x {key}")
    single = [v for v in nets.values() if len(v["sinks"]) == 1]
    print(f"  single-sink nets: {len(single)}, with any diode: "
          f"{sum(1 for v in single if v['diodes'])}, with 8+: "
          f"{sum(1 for v in single if v['diodes'] >= 8)}")

    by_name = {n.replace("\\", "").strip(): v["diodes"] for n, v in nets.items()}
    rows = [(par, by_name[net]) for par, net, _ in first_check(
            f"{directory}/39-openroad-checkantennas-antenna_summary.rpt") if net in by_name]
    if len(rows) > 2:
        print(f"  first check: {len(rows)} violating pins; Pearson r(P/R, final diodes) = "
              f"{statistics.correlation([r[0] for r in rows], [r[1] for r in rows]):.2f}")
        print("  pins with P/R below 2 that ended with 8+ diodes:",
              sum(1 for p, d in rows if p < 2 and d >= 8))


if __name__ == "__main__":
    for d in sys.argv[1:]:
        report(d)
