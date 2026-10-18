#!/usr/bin/env python3
r"""Where a hard macro's pins really are, and how far their drivers sit.

A macro's pin names say nothing about their sides: the SRAM's addr0[0] is on
its bottom edge and addr0[7] on its left. This reads the LEF, puts every pin
on a side, and - given a finished run's DEF - measures how far the cell that
drives each pin is from it, so "the driver is too far from its pin" is a
number and not a guess.

Usage:
    macro_pins.py --lef macro.lef --origin 110 150 [--def final/def/x.def --prefix addr0 addr1]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

_PIN = re.compile(r"PIN\s+(\S+)(.*?)END\s+\1", re.S)
_RECT = re.compile(r"LAYER\s+(\w+)\s*;\s*RECT\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s+([-\d.]+)\s*;")
_SIZE = re.compile(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)")
_COMP = re.compile(r"-\s+(\S+)\s+(\S+)\s+\+\s+(?:PLACED|FIXED)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\w+)")
_EDGE_TOL = 1.1


def read_lef(path: Path) -> tuple[tuple[float, float], dict[str, dict]]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    size = _SIZE.search(text)
    width, height = float(size.group(1)), float(size.group(2))
    pins = {}
    for name, body in _PIN.findall(text):
        rect = _RECT.search(body)
        if not rect:
            continue
        layer = rect.group(1)
        x0, y0, x1, y1 = (float(rect.group(i)) for i in range(2, 6))
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        pins[name] = {"x": cx, "y": cy, "layer": layer, "side": side(cx, cy, width, height)}
    return (width, height), pins


def side(x: float, y: float, width: float, height: float) -> str:
    if y <= _EDGE_TOL:
        return "bottom"
    if y >= height - _EDGE_TOL:
        return "top"
    if x <= _EDGE_TOL:
        return "left"
    if x >= width - _EDGE_TOL:
        return "right"
    return "inner"


def by_side(pins: dict[str, dict], prefixes: list[str]) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for name, p in pins.items():
        base = name.split("[")[0]
        if prefixes and base not in prefixes:
            continue
        out.setdefault(base, {}).setdefault(p["side"], 0)
        out[base][p["side"]] += 1
    return out


def absolute(pin: dict, size: tuple[float, float], origin: tuple[float, float], orient: str = "N"):
    """Pin position in die coordinates for the orientations a macro can take."""
    w, h = size
    x, y = pin["x"], pin["y"]
    if orient == "MY":
        x = w - x
    elif orient == "MX":
        y = h - y
    elif orient == "S":
        x, y = w - x, h - y
    return origin[0] + x, origin[1] + y


def read_def_components(path: Path, dbu: float = 1000.0) -> dict[str, tuple[str, float, float]]:
    text = Path(path).read_text(encoding="utf-8", errors="ignore")
    return {n: (t, int(x) / dbu, int(y) / dbu) for n, t, x, y, _ in _COMP.findall(text)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lef", required=True, type=Path)
    ap.add_argument("--origin", nargs=2, type=float, default=[0.0, 0.0])
    ap.add_argument("--orient", default="N")
    ap.add_argument("--prefix", nargs="*", default=[])
    args = ap.parse_args()
    size, pins = read_lef(args.lef)
    print(f"macro {size[0]} x {size[1]} um, {len(pins)} pins with geometry")
    for base, sides in sorted(by_side(pins, args.prefix).items()):
        print(f"  {base:8s} {sides}")
    for name in sorted(pins):
        if args.prefix and name.split("[")[0] not in args.prefix:
            continue
        ax, ay = absolute(pins[name], size, tuple(args.origin), args.orient)
        print(f"  {name:10s} {pins[name]['side']:7s} {pins[name]['layer']}  ({ax:.1f}, {ay:.1f})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
