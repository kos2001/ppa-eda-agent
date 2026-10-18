#!/usr/bin/env python3
r"""How low a slew can a standard-cell driver deliver, at a given corner?

A macro pin with a slew limit is only fixable by placement and sizing if some
driver in the library can meet that limit at all. This reads the Liberty
`rise_transition` / `fall_transition` tables of the buffers and interpolates
the output slew at a given input slew and load, so "is 0.05 ns reachable at the
slow corner?" is answered from the library rather than from a run.

Usage:
    lib_slew_floor.py --lib .../sky130_fd_sc_hd__ss_100C_1v60.lib \
        --cells buf_8 buf_16 clkbuf_16 --slew 0.02 0.05 --load 0.005 0.02 0.05
"""
from __future__ import annotations

import argparse
import bisect
import re
import sys
from pathlib import Path

_TABLE = (r'%s\s*\("?[\w]+"?\)\s*\{\s*index_1\s*\("([^"]+)"\)\s*;'
          r'\s*index_2\s*\("([^"]+)"\)\s*;\s*values\s*\(([^;]+)\)\s*;')
_ROW = re.compile(r'"([^"]+)"')


def cell_block(text: str, cell: str, prefix: str = "sky130_fd_sc_hd__") -> str | None:
    start = text.find(f'cell ("{prefix}{cell}")')
    if start < 0:
        return None
    end = text.find(f'cell ("{prefix}', start + 10)
    return text[start:end if end > 0 else len(text)]


def tables(block: str, kind: str) -> list[tuple[list[float], list[float], list[list[float]]]]:
    """Every `kind` table in the cell: one per timing arc."""
    out = []
    for m in re.finditer(_TABLE % kind, block):
        i1 = [float(x) for x in m.group(1).split(",")]
        i2 = [float(x) for x in m.group(2).split(",")]
        rows = [[float(x) for x in r.split(",")] for r in _ROW.findall(m.group(3))]
        out.append((i1, i2, rows))
    return out


def _segment(axis: list[float], v: float) -> tuple[int, int, float]:
    k = max(1, min(len(axis) - 1, bisect.bisect_left(axis, v)))
    return k - 1, k, (v - axis[k - 1]) / (axis[k] - axis[k - 1])


def interpolate(i1, i2, rows, slew: float, load: float) -> float:
    a0, a1, fa = _segment(i1, slew)
    b0, b1, fb = _segment(i2, load)
    v0 = rows[a0][b0] * (1 - fb) + rows[a0][b1] * fb
    v1 = rows[a1][b0] * (1 - fb) + rows[a1][b1] * fb
    return v0 * (1 - fa) + v1 * fa


def output_slew(block: str, slew: float, load: float) -> float | None:
    """Worst rise/fall output transition over the cell's arcs, in ns."""
    found = [interpolate(*t, slew, load)
             for kind in ("rise_transition", "fall_transition")
             for t in tables(block, kind)]
    return max(found) if found else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", required=True, type=Path)
    ap.add_argument("--cells", nargs="+", default=["buf_4", "buf_8", "buf_16", "clkbuf_8", "clkbuf_16"])
    ap.add_argument("--slew", nargs="+", type=float, default=[0.02, 0.05])
    ap.add_argument("--load", nargs="+", type=float, default=[0.005, 0.02, 0.05])
    args = ap.parse_args()
    text = args.lib.read_text(encoding="utf-8", errors="ignore")
    cols = [(s, c) for s in args.slew for c in args.load]
    print(f"{args.lib.name}: output slew in ns, worst of rise/fall")
    print(f"{'cell':10s} " + " ".join(f"s{s:g}/c{c * 1000:g}fF".rjust(13) for s, c in cols))
    for cell in args.cells:
        block = cell_block(text, cell)
        if block is None:
            print(f"{cell:10s} (not in this library)")
            continue
        vals = [output_slew(block, s, c) for s, c in cols]
        print(f"{cell:10s} " + " ".join(("n/a" if v is None else f"{v:.3f}").rjust(13) for v in vals))
    return 0


if __name__ == "__main__":
    sys.exit(main())
