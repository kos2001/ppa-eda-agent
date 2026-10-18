#!/usr/bin/env python3
"""What a library-wide signoff sweep actually found.

stdcell_signoff.py checks one cell. Running it across the whole library
turns "the foundry's cells are clean" from an assumption into a number,
and — more usefully — turns a single odd result into either an outlier or
a pattern. The first sample of 37 had exactly one LVS mismatch
(a21oi_2) and no way to tell which of those two it was.

This reads the store rather than re-running anything, so it is cheap to
ask repeatedly and always reflects what was really measured.
"""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

import stdcell_signoff

REPO_ROOT = Path(__file__).resolve().parent.parent

# sky130's cell names end in a drive strength: inv_2, a21oi_4. The family
# is what is left, and it is the axis a pattern would show up along.
_NAME = re.compile(r"^sky130_fd_sc_hd__(?P<family>.+?)_(?P<drive>\d+)$")


def split_name(cell: str) -> tuple[str, int | None]:
    match = _NAME.match(cell)
    if not match:
        return cell.replace("sky130_fd_sc_hd__", ""), None
    return match.group("family"), int(match.group("drive"))


def survey() -> dict:
    """Latest case per cell, summarised."""
    latest: dict = {}
    for case in stdcell_signoff.cases():
        latest[case["cell"]] = case

    clean, not_clean = [], []
    for cell, case in sorted(latest.items()):
        (clean if case.get("passed") else not_clean).append(case)

    # A cell with no transistors (fill, decap, diode, conb, the spare-cell
    # macro) is not a mismatch — LVS compared nothing. Counting those
    # among the failures would put the library's own filler in the same
    # column as a real discrepancy.
    vacuous = [c for c in not_clean if c.get("verdict") == "vacuous"
               or (c.get("devices") or {}).get("schematic") == 0]
    vacuous_cells = {c["cell"] for c in vacuous}
    mismatches = []
    for case in not_clean:
        if case["cell"] in vacuous_cells:
            continue
        lvs = case.get("lvs") or {}
        family, drive = split_name(case["cell"])
        mismatches.append({
            "cell": case["cell"],
            "family": family,
            "drive": drive,
            "drc_errors": case.get("drc_errors"),
            "devices": lvs.get("devices"),
            "nets": lvs.get("nets"),
            # The gap is the interesting part: how many devices and nets
            # the layout carries that the schematic does not.
            "extra_devices": (lvs["devices"][0] - lvs["devices"][1]
                              if lvs.get("devices") else None),
            "extra_nets": (lvs["nets"][0] - lvs["nets"][1]
                           if lvs.get("nets") else None),
        })

    drc_dirty = [c["cell"] for c in latest.values()
                 if c.get("drc_errors") not in (0, None)]
    drc_unmeasured = [c["cell"] for c in latest.values()
                      if c.get("drc_errors") is None]
    return {
        "signed_off": len(latest),
        "clean": len(clean),
        "not_clean": len(not_clean),
        "vacuous": sorted(vacuous_cells),
        "drc_dirty": sorted(drc_dirty),
        "drc_unmeasured": sorted(drc_unmeasured),
        "mismatches": mismatches,
        "by_family": Counter(m["family"] for m in mismatches).most_common(),
        "by_drive": Counter(m["drive"] for m in mismatches).most_common(),
        "no_lvs_counts": [m["cell"] for m in mismatches if m["devices"] is None],
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = survey()
    if args.json:
        print(json.dumps(report, indent=2))
        return
    print(f"signed off : {report['signed_off']}")
    print(f"clean      : {report['clean']}")
    print(f"not clean  : {report['not_clean']}")
    if report["vacuous"]:
        print(f"  of which no-transistor cells (LVS compared nothing): "
              f"{len(report['vacuous'])}")
        print(f"  {', '.join(c.replace('sky130_fd_sc_hd__', '') for c in report['vacuous'])}")
    print(f"  real LVS mismatches: {len(report['mismatches'])}")
    if report["drc_dirty"]:
        print(f"DRC errors : {', '.join(report['drc_dirty'])}")
    else:
        print("DRC errors : none")
    if report["drc_unmeasured"]:
        print(f"DRC never measured: {', '.join(report['drc_unmeasured'])}")
    if report["mismatches"]:
        print("\nLVS mismatches by family:")
        for family, count in report["by_family"]:
            print(f"  {family:<16} {count}")
        print("\nby drive strength:")
        for drive, count in sorted(report["by_drive"], key=lambda kv: (kv[0] is None, kv[0])):
            print(f"  {drive}: {count}")
        print("\nshape of the gap (layout minus schematic):")
        for m in report["mismatches"]:
            if m["extra_devices"] is None:
                print(f"  {m['cell']:<34} netgen returned no counts")
            else:
                print(f"  {m['cell']:<34} +{m['extra_devices']} devices, "
                      f"+{m['extra_nets']} nets")


if __name__ == "__main__":
    main()
