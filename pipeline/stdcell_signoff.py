#!/usr/bin/env python3
"""The layout half of a standard cell: DRC on its real GDS, LVS against
its real schematic, recorded in the case store.

Why this belongs here. This repo signs off *designs* — Magic DRC, KLayout
DRC, netgen LVS on what OpenLane places and routes — and it had just
learned to draw a standard cell's schematic (stdcell_schematic.py). A
schematic with no layout beside it is half a cell. The foundry ships the
other half, one `.mag` per cell in `libs.ref/sky130_fd_sc_hd/mag/`, and
the same two tools this pipeline already runs will check it.

What it actually proves, beyond "the foundry's cells are clean" (they
are, and a run that said otherwise would be a finding about this
pipeline, not about sky130): LVS compares Magic's extraction of the real
layout against the SPICE that stdcell_schematic.py derives from the CDL.
So a pass is independent evidence that that translation preserves the
circuit — `inv_2` matched uniquely on the first run, with netgen folding
the layout's four fingers into the CDL's `m=2`. Nothing else in this repo
checks that conversion; this does, against the foundry's own geometry.

Both tools come from the pinned OpenLane image this pipeline already
uses, and both need `PDK_ROOT=/pdk` in the container: the PDK's magicrc
does `tech load $PDK_ROOT/...`, and without it Magic looks for the tech
file under the absolute volare build path baked in at PDK build time and
dies there.

Usage:
    stdcell_signoff.py --cell sky130_fd_sc_hd__inv_2
    stdcell_signoff.py --scan
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path

import custom_bridge  # noqa: F401  (kept for image/ensure helpers' import parity)
import stdcell_schematic
from toolchain import OPENLANE_IMAGE, platform_args

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"
MAG_DIR = PDK_ROOT / "sky130A" / "libs.ref" / "sky130_fd_sc_hd" / "mag"
NETGEN_SETUP = "/pdk/sky130A/libs.tech/netgen/sky130A_setup.tcl"
CASE_DIR = REPO_ROOT / "reference-db" / "stdcells"

_DRC_COUNT = re.compile(r"^DRC_COUNT\s+(\d+)", re.MULTILINE)

# Magic script: load the cell, count DRC, extract to SPICE. `drc catchup`
# is what makes the count real — without it the check is still running
# when the count is read and reports zero for a cell that has errors.
_MAGIC_TCL = """load {mag}
select top cell
drc euclidean on
drc check
drc catchup
puts "DRC_COUNT [drc list count total]"
extract do local
extract all
ext2spice lvs
ext2spice -o /work/extracted.spice
quit -noprompt
"""

_LVS_TCL = """lvs "/work/extracted.spice {cell}" "/work/schematic.spice {cell}" \\
    {setup} /work/lvs.out
quit
"""


def cell_mag(cell: str) -> Path:
    return MAG_DIR / f"{cell}.mag"


def _docker(work: Path, argv: list[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", "run", "--rm", *platform_args(),
         "-e", "PDK_ROOT=/pdk",
         "-v", f"{PDK_ROOT}:/pdk:ro", "-v", f"{work}:/work", "-w", "/work",
         OPENLANE_IMAGE, *argv],
        capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)


def parse_drc(output: str) -> int | None:
    """Magic's own count, or None if the line never appeared.

    None is not zero. A run that died before `drc catchup` prints nothing,
    and reading that as a clean cell is the same mistake score() refuses
    to make when a signoff metric is missing.
    """
    match = _DRC_COUNT.search(output)
    return int(match.group(1)) if match else None


_LVS_COUNTS = re.compile(
    r"Circuit 1 contains (\d+) devices?, Circuit 2 contains (\d+) devices?")
_LVS_NETS = re.compile(
    r"Circuit 1 contains (\d+) nets?,\s+Circuit 2 contains (\d+) nets?")


def parse_lvs(output: str) -> dict:
    """netgen's verdict, as a verdict rather than as prose.

    "Circuits match uniquely" is the only line that means a pass;
    everything else — including "match with warnings" — is recorded as
    what it is rather than rounded up.

    The device and net counts are kept because they are what makes a
    mismatch diagnosable at all. Measured across 37 cells on 2026-09-13:
    DRC 0 everywhere and LVS matching on 36. The one that does not is
    a21oi_2 — after merging, netgen sees 8 devices against 6 and 11 nets
    against 10, so the layout carries an internal node the schematic does
    not.

    What is NOT claimed: a cause. Reproducing it with the untouched CDL
    rules out this pipeline's CDL-to-SPICE translation, and the obvious
    story — "a series stack laid out as parallel fingers" — is
    contradicted by the same sample: a21oi_1 matches, and so does
    nand3_2, which is a three-high series stack at the same m=2. So it is
    something about that cell rather than about a class of cells, and it
    is recorded as an open finding rather than explained away.
    """
    lowered = output.lower()
    devices = _LVS_COUNTS.search(output)
    nets = _LVS_NETS.search(output)
    return {
        "match": "circuits match uniquely" in lowered,
        "uncertain": "match with warnings" in lowered or "unique" not in lowered,
        "summary": next((l.strip() for l in reversed(output.splitlines())
                         if "circuits" in l.lower() and "match" in l.lower()), None),
        "devices": ([int(devices.group(1)), int(devices.group(2))]
                    if devices else None),
        "nets": [int(nets.group(1)), int(nets.group(2))] if nets else None,
    }


def signoff(cell: str, timeout: int = 900) -> dict:
    """DRC + LVS for one standard cell, from the PDK's own layout."""
    mag = cell_mag(cell)
    if not mag.is_file():
        return {"ok": False, "cell": cell,
                "error": f"no layout at {mag.relative_to(REPO_ROOT)}"}
    block = stdcell_schematic.subckt(cell)
    if block is None:
        return {"ok": False, "cell": cell,
                "error": f"{cell} is not in the library CDL"}
    if not shutil.which("docker"):
        return {"ok": False, "cell": cell, "error": "docker is not available"}

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / "run.tcl").write_text(
            _MAGIC_TCL.format(mag=f"/pdk/{mag.relative_to(PDK_ROOT).as_posix()}"),
            encoding="utf-8")
        # The schematic side is the same translation the schematic view
        # draws from, which is what makes the LVS result evidence about
        # that translation and not only about the foundry's layout.
        (work / "schematic.spice").write_text(stdcell_schematic.to_spice(block),
                                              encoding="utf-8")
        try:
            magic = _docker(work, ["magic", "-dnull", "-noconsole", "-rcfile",
                                    "/pdk/sky130A/libs.tech/magic/sky130A.magicrc",
                                    "/work/run.tcl"], timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "cell": cell, "error": f"magic: {exc}"}
        magic_out = magic.stdout + magic.stderr
        drc = parse_drc(magic_out)
        extracted = work / "extracted.spice"
        if not extracted.is_file():
            return {"ok": False, "cell": cell,
                    "error": "magic wrote no extracted netlist",
                    "drc_errors": drc, "log": magic_out[-800:]}

        (work / "lvs.tcl").write_text(
            _LVS_TCL.format(cell=cell, setup=NETGEN_SETUP), encoding="utf-8")
        try:
            netgen = _docker(work, ["netgen", "-batch", "source", "/work/lvs.tcl"],
                             timeout)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "cell": cell, "error": f"netgen: {exc}"}
        lvs = parse_lvs(netgen.stdout + netgen.stderr)
        layout_netlist = extracted.read_text(encoding="utf-8")

    return {
        "ok": True,
        "cell": cell,
        "drc_errors": drc,
        "lvs": lvs,
        # A cell passes only when both checks actually ran and both are
        # clean — an absent DRC count blocks it, the same way score()
        # treats a missing signoff metric.
        "passed": drc == 0 and lvs["match"],
        "devices": {
            "schematic": stdcell_schematic.device_count(block),
            "layout": layout_netlist.count("sky130_fd_pr__"),
        },
        "layout": str(mag.relative_to(REPO_ROOT)),
        "layout_netlist": layout_netlist,
    }


def write_case(result: dict) -> Path:
    """One case per cell per run, in reference-db/stdcells/."""
    CASE_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d__%H%M%S")
    path = CASE_DIR / f"{result['cell']}__{stamp}.json"
    case = {
        "cell": result["cell"],
        "date": date.today().isoformat(),
        "kind": "standard-cell signoff",
        "layout": result["layout"],
        "schematic_source": str(stdcell_schematic.CDL.relative_to(REPO_ROOT)),
        "drc_errors": result["drc_errors"],
        "lvs": result["lvs"],
        "devices": result["devices"],
        "passed": result["passed"],
        "outcome": ("clean" if result["passed"] else
                     "DRC or LVS did not come back clean — see drc_errors/lvs"),
        "toolchain": {"drc": "magic", "extract": "magic", "lvs": "netgen",
                       "image": OPENLANE_IMAGE},
    }
    path.write_text(json.dumps(case, indent=2), encoding="utf-8")
    return path


def cases(cell: str | None = None) -> list[dict]:
    if not CASE_DIR.is_dir():
        return []
    out = []
    for path in sorted(CASE_DIR.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        if cell and case.get("cell") != cell:
            continue
        case["_path"] = str(path.relative_to(REPO_ROOT))
        out.append(case)
    return out


def scan() -> dict:
    """What the store holds, and what it does not.

    The denominator is the whole library on purpose: 437 cells, and a
    report that only counted the ones already run would make a store of
    three look complete.
    """
    library = stdcell_schematic.cells()
    stored = cases()
    latest: dict = {}
    for case in stored:
        latest[case["cell"]] = case
    clean = [c for c in latest.values() if c.get("passed")]
    return {
        "library": len(library),
        "signed_off": len(latest),
        "clean": len(clean),
        "not_clean": sorted(c["cell"] for c in latest.values() if not c.get("passed")),
        "cases": len(stored),
        "generated": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cell", default=None)
    ap.add_argument("--scan", action="store_true")
    ap.add_argument("--no-case", action="store_true")
    args = ap.parse_args()

    if args.scan or not args.cell:
        report = scan()
        print(f"library: {report['library']} cells")
        print(f"signed off: {report['signed_off']}  clean: {report['clean']}"
              f"  cases: {report['cases']}")
        if report["not_clean"]:
            print("not clean: " + ", ".join(report["not_clean"]))
        return

    result = signoff(args.cell)
    if not result["ok"]:
        print(f"{args.cell}: {result['error']}", file=sys.stderr)
        sys.exit(1)
    drc = "not measured" if result["drc_errors"] is None else result["drc_errors"]
    print(f"{result['cell']}: DRC {drc}, LVS "
          f"{'match' if result['lvs']['match'] else 'MISMATCH'}"
          f"  ({result['devices']['schematic']} schematic devices, "
          f"{result['devices']['layout']} extracted)")
    if not args.no_case:
        print(f"case: {write_case(result).relative_to(REPO_ROOT)}")
    sys.exit(0 if result["passed"] else 1)


if __name__ == "__main__":
    main()
