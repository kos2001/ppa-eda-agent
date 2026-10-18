#!/usr/bin/env python3
r"""Where did the per-pin drivers land, what are they, and how far from their pin?

For a finished sram_wrapper-style run: reads the final DEF, takes each
`io_drv` instance, and measures the Manhattan distance from the last stage to
the macro pin it drives (pin positions from the LEF), plus the cell the
resizer left it as. A driver that is a different cell than the RTL asked for,
or 60 um from its pin, explains a pin that is slower than the library says it
must be.

Usage:
    io_driver_report.py --run-dir RUN --lef MACRO.lef --origin 110 150
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import macro_pins

_NAME = re.compile(r"gen_io_drv_(\w+?)\[(\d+)\]\.u_([a-d])|io_drv_(\w+?)_([a-d])$")


def parse_name(name: str) -> tuple[str, str] | None:
    """(macro pin, stage) from an instance name, or None."""
    name = name.replace("\\", "")
    m = _NAME.search(name)
    if not m:
        return None
    if m.group(1):
        return f"{m.group(1)}[{m.group(2)}]", m.group(3)
    return m.group(4), m.group(5)


def report(def_path: Path, lef: Path, origin: tuple[float, float], orient: str = "N") -> list[dict]:
    size, pins = macro_pins.read_lef(lef)
    comps = macro_pins.read_def_components(def_path)
    rows: dict[str, dict] = {}
    for name, (cell, x, y) in comps.items():
        parsed = parse_name(name)
        if not parsed:
            continue
        pin, stage = parsed
        row = rows.setdefault(pin, {"pin": pin})
        row[stage] = (cell.replace("sky130_fd_sc_hd__", ""), x, y)
    out = []
    for pin, row in sorted(rows.items()):
        stages = sorted(k for k in row if k in "abcd")
        if pin not in pins or len(stages) < 2:
            continue
        ax, ay = macro_pins.absolute(pins[pin], size, origin, orient)
        cell, x, y = row[stages[-1]]           # the last stage is the one beside the pin
        out.append({"pin": pin, "side": pins[pin]["side"], "last_stage": cell,
                    "first_stage": row[stages[0]][0], "stages": len(stages),
                    "distance_um": round(abs(x - ax) + abs(y - ay), 1)})
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--lef", required=True, type=Path)
    ap.add_argument("--origin", nargs=2, type=float, default=[0.0, 0.0])
    ap.add_argument("--orient", default="N")
    args = ap.parse_args()
    defs = sorted(args.run_dir.glob("final/def/*.def"))
    if not defs:
        raise SystemExit(f"no final DEF under {args.run_dir}")
    for r in report(defs[0], args.lef, tuple(args.origin), args.orient):
        print(f"{r['pin']:10s} {r['side']:7s} {r['first_stage']:>10s} -> {r['last_stage']:<10s} "
              f"{r['distance_um']:>6.1f} um from its pin")
    return 0


if __name__ == "__main__":
    sys.exit(main())
