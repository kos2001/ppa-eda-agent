#!/usr/bin/env python3
r"""What is actually on the nets that violate max-fanout?

A max-fanout violation names a driver and a count. It does not say what the
count is made of, and on sram_wrapper that was the whole story: of the 17
loads on the counter's bit 0, nine were antenna diodes the heuristic diode
insertion had added after the resizer's last fanout repair. Take the diodes
away and the net has 8 loads and no violation. The same coupling showed up in
aes (antenna repair 15 -> 8, fanout 4 -> 16), where it was found by
reasoning about counts; this reads it off the netlist.

Reads the worst corner's max-fanout violator table from the signoff STA
report and the run's final netlist, and prints, per violating driver, its
loads by cell type and how many are antenna diodes - with the fanout it would
have without them.

Usage:
    fanout_loads.py --run-dir designs/sram_wrapper/runs/TAG
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

# The slack column is blank when it would read -1, so it is optional: _085_/Q
# at fanout 11 against a limit of 10 prints no number before "(VIOLATED)".
_FANOUT_ROW = re.compile(r"^\s*(\S+)/(\w+)\s+(\d+)\s+(\d+)\s+(?:-?\d+\s+)?\(VIOLATED\)", re.M)
_INSTANCE = re.compile(r"\n\s*(\\?\S+)\s+(\\?\S+)\s*\((.*?)\);", re.S)
_PIN = re.compile(r"\.(\w+)\(([^)]*)\)")
_OUTPUTS = {"X", "Q", "Y", "Q_N", "HI", "LO", "COUT", "SUM"}
_DIODE = ("diode", "ANTENNA")
_SKIP = {"module", "input", "output", "wire", "assign", "endmodule"}


def violators(checks_text: str) -> list[dict]:
    """Max-fanout violators from one corner's checks.rpt."""
    section = checks_text.split("max fanout", 1)[1] if "max fanout" in checks_text else ""
    section = section.split("report_parasitic_annotation", 1)[0]
    return [{"inst": m.group(1), "pin": m.group(2), "limit": int(m.group(3)), "fanout": int(m.group(4))}
            for m in _FANOUT_ROW.finditer(section)]


def parse_netlist(text: str) -> tuple[dict, dict]:
    """(driver of each net, loads of each net) from a flat gate-level netlist."""
    drivers: dict[str, tuple[str, str]] = {}
    loads: dict[str, list[tuple[str, str]]] = collections.defaultdict(list)
    for cell, name, body in _INSTANCE.findall(text):
        if cell in _SKIP:
            continue
        for pin, net in _PIN.findall(body):
            net = net.strip()
            if not net:
                continue
            if pin in _OUTPUTS:
                drivers[net] = (name.lstrip("\\"), cell)
            else:
                loads[net].append((name.lstrip("\\"), cell))
    return drivers, loads


def is_diode(cell: str, name: str) -> bool:
    return any(tag in cell for tag in _DIODE) or name.startswith("ANTENNA")


def explain(run_dir: Path) -> list[dict]:
    reports = sorted(run_dir.glob("*stapostpnr*/*/checks.rpt"))
    netlists = sorted(run_dir.glob("final/nl/*.nl.v")) or sorted(run_dir.glob("final/pnl/*.pnl.v"))
    if not reports or not netlists:
        raise FileNotFoundError(f"no signoff STA report or final netlist under {run_dir}")
    worst = {}
    for rpt in reports:                      # the same nets violate at every corner; keep the largest count
        for v in violators(rpt.read_text(encoding="utf-8", errors="ignore")):
            key = (v["inst"], v["pin"])
            if key not in worst or v["fanout"] > worst[key]["fanout"]:
                worst[key] = v
    drivers, loads = parse_netlist(netlists[0].read_text(encoding="utf-8", errors="ignore"))
    by_driver = {name.replace("\\", ""): net for net, (name, _cell) in drivers.items()}
    out = []
    for (inst, pin), v in sorted(worst.items(), key=lambda kv: -kv[1]["fanout"]):
        net = by_driver.get(inst.replace("\\", ""))
        sinks = loads.get(net, []) if net else []
        hist = collections.Counter(c.replace("sky130_fd_sc_hd__", "") for _n, c in sinks)
        diodes = sum(1 for n, c in sinks if is_diode(c, n))
        out.append({**v, "net": net, "loads": dict(hist), "diodes": diodes,
                    "fanout_without_diodes": (len(sinks) - diodes) if sinks else None})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    args = ap.parse_args()
    rows = explain(args.run_dir)
    if not rows:
        print("no max-fanout violators")
        return 0
    over = sum(1 for r in rows if r["fanout_without_diodes"] is not None
               and r["fanout_without_diodes"] <= r["limit"])
    for r in rows:
        left = r["fanout_without_diodes"]
        tail = "" if left is None else f"  -> {left} without diodes" + (" (clean)" if left <= r["limit"] else "")
        print(f"{r['inst']}/{r['pin']:3s} fanout {r['fanout']:3d} (limit {r['limit']}): "
              f"{r['diodes']} diodes{tail}")
    print(f"{over} of {len(rows)} violators would be within the limit without antenna diodes")
    return 0


if __name__ == "__main__":
    sys.exit(main())
