#!/usr/bin/env python3
"""Attribute each final max-slew / max-cap / max-fanout violator to its sinks.

For every violating driver pin in a corner's checks.rpt, find the net it
drives in the final Verilog netlist and count that net's sinks and how many
of them are antenna diodes. A violator whose sinks are "limit real loads
plus one diode" was pushed over by the diode, not by the buffering.

    python3 violator_sinks.py <final netlist .v[.gz]> <checks.rpt>

Reads files only. Netlist and report are archived beside this script.
"""
import gzip, re, sys

OUT = {"X", "Y", "Q", "Q_N", "Z", "COUT", "SUM", "HI", "LO", "GCLK"}
INST = re.compile(r'^\s*(sky130_fd_sc_hd__\w+)\s+(\\?\S+)\s*\((.*?)\);', re.S | re.M)
PIN = re.compile(r'\.(\w+)\(\s*([^)]*?)\s*\)')


def netlist(path):
    opener = gzip.open if path.endswith(".gz") else open
    text = opener(path, "rt", encoding="utf-8").read()
    return [(m.group(1), m.group(2), {p: n.strip() for p, n in PIN.findall(m.group(3))})
            for m in INST.finditer(text)]


def violations(path):
    text = open(path, encoding="utf-8").read()
    out = {}
    for part in re.split(r'^(?=max (?:slew|capacitance|fanout)\b)', text, flags=re.M)[1:]:
        head = part.splitlines()[0]
        if "count" in head:
            continue
        out[head.split()[1]] = [l.split() for l in part.splitlines() if "VIOLATED" in l]
    return out


def attribute(insts, viol):
    by_name = {i[1]: i for i in insts}
    rows = []
    for kind, lines in viol.items():
        diode_pins = sum(1 for l in lines if l[0].startswith("ANTENNA"))
        for l in lines:
            pin = l[0]
            if pin.startswith("ANTENNA") or "/" not in pin:
                continue
            inst, port = pin.rsplit("/", 1)
            if port not in OUT or inst not in by_name:
                continue
            cell, _, pins = by_name[inst]
            net = next((n for k, n in pins.items() if k in OUT), None)
            sinks = [i for i in insts if i[1] != inst
                     and any(n == net and k not in OUT for k, n in i[2].items())]
            rows.append({"kind": kind, "driver": pin, "cell": cell.split("__", 1)[1],
                         "net": net, "limit": l[1], "value": l[2],
                         "sinks": len(sinks),
                         "diode_sinks": sum("diode" in s[0] for s in sinks)})
        if kind == "slew":
            rows.append({"kind": "slew", "driver": "(all violated pins)",
                         "violated_pins": len(lines), "of_which_diode_pins": diode_pins})
    return rows


if __name__ == "__main__":
    import json
    print(json.dumps(attribute(netlist(sys.argv[1]), violations(sys.argv[2])), indent=1))
