#!/usr/bin/env python3
r"""Where a macro's slew ceiling comes from, and what would move it.

sram_wrapper has never closed. Every run dies the same way:

    [RSZ-0090] Max transition time from SDC is 0.040ns.
               Best achievable transition time is 0.043ns with a load of 0.01pF

This project already established that the 0.040ns is the macro's own
liberty pin attribute rather than an SDC value, that no config override
reaches it, and — in the commit that cut the worst address slew from
0.88ns to 0.209ns — that the remaining gap is 5.2x. What was missing is
where the number comes from. It is arithmetic anyone can check:

    OpenRAM default slew_scales      [0.25, 1, 8]
    sky130 tech.spice["rise_time"]   0.005 ns
    product                          [0.00125, 0.005, 0.04]
    the macro's own index_1          "0.00125, 0.005, 0.04"

So 0.04 ns is not a limit the SRAM imposes on the design. It is the top
of the input-slew axis the macro was characterised over. The table stops
there and `max_transition` records where it stopped; nothing was ever
measured above it. OpenSTA is not refusing a slew the memory cannot
take — it is refusing to read off the end of a table.

WHY THE CHEAP EDIT IS NOT THE FIX. sram_wrapper carries a
`.relaxed.lib` that raises the attribute from 0.04 to 0.05 without
adding a single measured point, which asks the timer to trust numbers
nobody produced. It is also not enough: this design's best measured slew
is 0.209 ns, over four times that.

WHAT WOULD FIX IT. Characterise the macro over the slew range it will
actually see. OpenRAM (github.com/VLSIDA/OpenRAM, BSD-3, the generator
these macros come from) exposes exactly that as `slew_scales` in its
SRAM config, and the sky130 macros are regenerable from
github.com/VLSIDA/sky130_sram_macros — whose copy of this liberty is
byte-identical to the PDK's on these numbers, so this is not a stale
local file.

WHAT THIS MODULE DOES NOT DO. It does not run OpenRAM. Regenerating a
macro is a SPICE characterisation sweep measured in hours and it
produces a new GDS/LEF/lib set that has to be verified before anything
trusts it. This reports what the ceiling is, where it came from, how far
past it a design actually runs, and the `slew_scales` that would cover
it — so the decision is made against numbers rather than a guess.

Usage:
    recharacterise.py <liberty> [--worst-slew-ns N]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import model_validity

# OpenRAM's defaults when a config sets nothing (compiler/globals.py).
OPENRAM_DEFAULT_SLEW_SCALES = [0.25, 1, 8]

# sky130's own reference rise time (technology/sky130/tech/tech.py,
# spice["rise_time"], nanoseconds). slews = slew_scales * rise_time.
SKY130_RISE_TIME_NS = 0.005

# Headroom on the top scale. A grid whose last point sits exactly on the
# worst observed slew leaves the timer interpolating to the very edge
# again the moment placement moves, which is the position this design is
# already in.
TOP_MARGIN = 1.15


def slew_scales_for(worst_slew_ns: float,
                    rise_time_ns: float = SKY130_RISE_TIME_NS) -> list[float]:
    """A slew_scales grid that covers `worst_slew_ns`, extending the default.

    Extends rather than replaces: the existing points are real
    measurements, and dropping them would discard characterisation that
    exists to add characterisation that does not. Returns the default
    unchanged when the observed slew already sits inside it — there is
    nothing to gain from a wider sweep that costs hours.
    """
    scales = list(OPENRAM_DEFAULT_SLEW_SCALES)
    covered = max(scales) * rise_time_ns
    if worst_slew_ns <= covered:
        return scales
    needed = (worst_slew_ns * TOP_MARGIN) / rise_time_ns
    scales.append(round(needed))
    return sorted(set(scales))


def analyse(lib_path: Path | str, worst_slew_ns: float | None = None,
            rise_time_ns: float = SKY130_RISE_TIME_NS) -> dict:
    """What this liberty's ceiling is, and whether a design clears it."""
    ceiling = model_validity.characterisation_ceiling(lib_path)
    if ceiling is None:
        return {"lib": str(lib_path), "ceiling_ns": None,
                "ceiling_source": "no index_1 in file",
                "needs_recharacterisation": False, "notes": [
                    "This file declares no input-slew axis, so it states no "
                    "characterisation ceiling. That is a fact about the file, "
                    "not a ceiling of zero."]}

    expected = max(OPENRAM_DEFAULT_SLEW_SCALES) * rise_time_ns
    source = ("openram_default_slew_scales"
              if abs(ceiling - expected) < 1e-9 else "custom")

    notes = []
    if source == "openram_default_slew_scales":
        notes.append(
            f"The ceiling is OpenRAM's default slew_scales "
            f"{OPENRAM_DEFAULT_SLEW_SCALES} times sky130's rise_time "
            f"{rise_time_ns}ns. It records where characterisation stopped, "
            f"not a limit the macro imposes.")
    else:
        notes.append(
            "The ceiling does not match OpenRAM's defaults, so this macro "
            "was characterised with settings of its own — read them before "
            "assuming the grid can be widened.")

    report = {
        "lib": str(lib_path),
        "ceiling_ns": ceiling,
        "ceiling_source": source,
        "needs_recharacterisation": False,
        "notes": notes,
    }

    if worst_slew_ns is None:
        notes.append(
            "No observed slew given, so whether a design clears this ceiling "
            "is unknown. Pass --worst-slew-ns from a real run.")
        return report

    report["worst_slew_ns"] = worst_slew_ns
    report["over_ceiling_x"] = worst_slew_ns / ceiling
    report["needs_recharacterisation"] = worst_slew_ns > ceiling
    # Raising the attribute without adding measured points is
    # extrapolation whatever value is chosen; it is additionally useless
    # here, because the observed slew is far past any plausible edit.
    report["relaxing_is_sufficient"] = False

    if report["needs_recharacterisation"]:
        scales = slew_scales_for(worst_slew_ns, rise_time_ns)
        report["suggested_slew_scales"] = scales
        report["suggested_grid_ns"] = [round(s * rise_time_ns, 6)
                                       for s in scales]
        notes.append(
            f"The design runs at {worst_slew_ns}ns, "
            f"{report['over_ceiling_x']:.1f}x the ceiling. Every timing "
            f"number the tool reports for these pins is extrapolated off the "
            f"end of the table.")
        notes.append(
            f"Raising max_transition in the .lib is extrapolation, not a "
            f"fix: it adds no measured point. Characterising with "
            f"slew_scales={scales} (OpenRAM config) would put the observed "
            f"slew inside the grid.")
        notes.append(
            "Regenerating is a SPICE sweep of hours and yields a new "
            "GDS/LEF/lib set that must be verified before anything trusts "
            "it. This module reports; it does not run OpenRAM.")
    else:
        notes.append(
            f"The design's worst slew {worst_slew_ns}ns sits inside the "
            f"characterised grid. Nothing here needs regenerating.")

    return report


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("liberty", help="path to a macro .lib")
    ap.add_argument("--worst-slew-ns", type=float, default=None,
                    help="worst input slew a real run produced, in ns")
    args = ap.parse_args()
    print(json.dumps(analyse(args.liberty, args.worst_slew_ns), indent=2))


if __name__ == "__main__":
    sys.exit(main())
