#!/usr/bin/env python3
r"""Slew along a chain of standard cells, from the library tables alone.

lib_slew_floor.py answers "how low can one driver go". A pin's slew depends on
the whole chain feeding it: a strong last stage still produces a slow edge if
the stage before it hands it a slow input. This walks a chain, each stage
loaded by the next stage's input capacitance, and prints the slew at every
node, so a taper can be chosen before an hour-long run.

The last number is also scaled to the receiving pin's slew thresholds. The
standard-cell library measures slew between 20% and 80% of the swing; the SRAM
macro's liberty (OpenRAM) measures 10% to 90%, so STA reports the same edge as
1.333x as slow at the macro's pin. That factor was found in a finished run:
0.0588 ns at the driver's output, 0.0785 ns at the macro pin it drives, on a
net with 28 ohm and 2 fF of wire.

Usage:
    lib_chain.py --lib ...ss_100C_1v60.lib --chain clkinv_2 clkinv_4 clkinv_8 clkinv_16 \
        --load 0.0090 --in-slew 0.05 [--pin-scale 1.3333]
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import lib_slew_floor as lsf

_PIN_CAP = re.compile(r'pin\s*\("A"\)\s*\{[^}]*?\n\s*capacitance\s*:\s*([\d.]+)', re.S)


def input_cap(block: str, pin: str = "A") -> float:
    m = re.search(r'pin\s*\("%s"\)\s*\{[^}]*?\n\s*capacitance\s*:\s*([\d.]+)' % pin, block, re.S)
    if not m:
        raise ValueError(f"no capacitance for pin {pin}")
    return float(m.group(1))


def walk(text: str, chain: list[str], load: float, in_slew: float,
         wire_between: float = 0.0) -> list[dict]:
    """Slew after each stage. `load` is the capacitance on the last stage's output (pF)."""
    blocks = []
    for cell in chain:
        b = lsf.cell_block(text, cell)
        if b is None:
            raise ValueError(f"{cell} not in the library")
        blocks.append(b)
    rows, slew = [], in_slew
    for i, (cell, block) in enumerate(zip(chain, blocks)):
        nxt = input_cap(blocks[i + 1]) + wire_between if i + 1 < len(chain) else load
        slew = lsf.output_slew(block, slew, nxt)
        rows.append({"stage": cell, "load_pF": round(nxt, 5), "out_slew_ns": round(slew, 4)})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--lib", required=True, type=Path)
    ap.add_argument("--chain", nargs="+", required=True)
    ap.add_argument("--load", type=float, required=True, help="pF on the last output")
    ap.add_argument("--in-slew", type=float, default=0.05)
    ap.add_argument("--wire", type=float, default=0.002, help="pF between stages")
    ap.add_argument("--pin-scale", type=float, default=80 / 60,
                    help="receiver slew thresholds over the library's (10-90 over 20-80)")
    args = ap.parse_args()
    text = args.lib.read_text(encoding="utf-8", errors="ignore")
    rows = walk(text, args.chain, args.load, args.in_slew, args.wire)
    for r in rows:
        print(f"  {r['stage']:10s} load {r['load_pF'] * 1000:6.1f} fF  out slew {r['out_slew_ns']:.4f} ns")
    print(f"  at the macro pin (x{args.pin_scale:.3f}): {rows[-1]['out_slew_ns'] * args.pin_scale:.4f} ns")
    return 0


if __name__ == "__main__":
    sys.exit(main())
