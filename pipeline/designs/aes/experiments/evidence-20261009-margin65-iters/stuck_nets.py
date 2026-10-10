#!/usr/bin/env python3
"""Find the nets that received the diodes of a repair step's late iterations.

OpenROAD names every inserted diode ANTENNA_<n> with one global counter, and
the repair log reports how many diodes each iteration inserted (GRT-0015) and
how many violations it found (GRT-0012). So the diodes of iteration k of
repair step s have the ids start..start+inserted-1, and their DIODE pin
shows the net they were put on. For an iteration that finds a small number of
violations this names the net the repair keeps working on.

    python3 stuck_nets.py <netlist .v[.gz]> <antenna_repair_iterations.json> [max_found]

Reads archived files only. `max_found` (default 3) selects the iterations
that found at most that many violations. For each net it prints the driver
cell, its real sinks and the diodes on it, and whether a clock buffer drives it
(by cell family, not by name: this flow also names its signal-buffer nets
fanout_repair_net_<n>).
"""
import collections, gzip, json, re, sys


def netlist(path):
    opener = gzip.open if path.endswith(".gz") else open
    text = opener(path, "rt", encoding="utf-8").read()
    inst = re.compile(r'^\s*(sky130_fd_sc_hd__\w+)\s+(\\?\S+)\s*\((.*?)\);', re.S | re.M)
    pin = re.compile(r'\.(\w+)\(\s*([^)]*?)\s*\)')
    return [(m.group(1), m.group(2), {p: n.strip() for p, n in pin.findall(m.group(3))})
            for m in inst.finditer(text)]


OUT = {"X", "Y", "Q", "Q_N", "Z", "COUT", "SUM", "HI", "LO", "GCLK"}


def analyse(nl_path, iterations_path, max_found=3):
    insts = netlist(nl_path)
    by_id, driver, sinks = {}, {}, collections.defaultdict(list)
    for cell, name, pins in insts:
        short = cell.split("__", 1)[1]
        m = re.fullmatch(r"ANTENNA_(\d+)", name.lstrip("\\"))
        if m:
            by_id[int(m.group(1))] = pins.get("DIODE")
            continue
        for k, net in pins.items():
            if k in OUT:
                driver[net] = (name, short)
            elif k not in ("VPWR", "VGND", "VPB", "VNB"):
                sinks[net].append(short)
    steps = json.load(open(iterations_path))
    per_net = collections.Counter(by_id.values())
    start, report = 1, []
    for step, info in steps.items():
        late = collections.defaultdict(list)
        for it in info["iterations"]:
            n = it.get("diodes_inserted", 0)
            ids = range(start, start + n)
            start += n
            if 0 < it.get("violations_found", 0) <= max_found:
                for i in ids:
                    late[by_id[i]].append(it["iteration"])
        rows = []
        for net, iters in sorted(late.items(), key=lambda kv: -len(kv[1])):
            d = driver.get(net)
            rows.append({"net": net, "driver": d[0] if d else None,
                         "driver_cell": d[1] if d else None,
                         "clock_buffer_driven": bool(d and d[1].startswith(("clkbuf", "clkinv", "clkdlybuf"))) or net == "clk",
                         "late_iterations_with_a_diode": iters,
                         "diodes_on_net": per_net[net],
                         "real_sinks": dict(collections.Counter(sinks[net]))})
        report.append({"step": step, "late_iteration_diodes": sum(len(r["late_iterations_with_a_diode"]) for r in rows), "nets": rows})
    assert start - 1 == len(by_id), "repair steps do not account for every diode"
    return report


if __name__ == "__main__":
    out = analyse(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3)
    print(json.dumps(out, indent=1))
