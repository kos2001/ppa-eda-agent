#!/usr/bin/env python3
"""Proves that a run's synthesized gate netlist is functionally equivalent
to the RTL it came from, using Yosys's own SAT-based equivalence checker.

Fills a real hole in this pipeline's verification story. `score()` checks
DRC, LVS, utilization and timing — all real, none of them functional. LVS
compares the *layout* against the *netlist*, confirming the physical
implementation matches what was handed to placement; nothing has ever
compared that netlist against the *RTL*. So a synthesis result that is
clean, legal, fast and wrong would be reported as PASS.

That is not a hypothetical risk here: this pipeline deliberately varies
`SYNTH_STRATEGY` (AREA 0 / AREA 2 / DELAY 1 / DELAY 4 are real candidates
in counter4's run_spec) and `STD_CELL_LIBRARY`, both of which change what
Yosys actually emits. Judging those candidates on area and timing while
never checking they still compute the same function is the gap this
closes.

Yosys is already in the OpenLane image — this adds no dependency, it
uses a capability the pipeline was shipping and not calling.

Method: read the RTL as `gold` and the gate netlist (with the real
liberty for cell functions) as `gate`, build an equivalence miter, and
discharge it with `equiv_simple` + `equiv_induct` for sequential logic.
`equiv_status -assert` exits non-zero on any unproven point, which is
what makes the result trustworthy rather than decorative — verified by
negative control (see the module tests).

Usage:
    equiv_check.py --design designs/counter4 --run-dir designs/counter4/runs/<tag>
"""
import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"

# `-ignore_miss_func` is required, not incidental: sky130's liberty has
# cells whose `function` attribute Yosys can't parse (sequential cells
# with complex next-state expressions). Without it the read aborts and
# nothing gets compared; with it those cells become blackboxes, which is
# correct — their equivalence points are handled by equiv_induct.
_YS = """
read_verilog {rtl_args}
prep -top {top} -flatten
design -stash gold

read_liberty -ignore_miss_func /work/cells.lib
read_verilog /work/gate.v
prep -top {top} -flatten
design -stash gate

design -copy-from gold -as gold {top}
design -copy-from gate -as gate {top}
equiv_make gold gate equiv
prep -top equiv
equiv_simple -seq 5
equiv_induct -seq 5
equiv_status -assert
"""


def find_netlist(run_dir: Path) -> Path:
    """The netlist synthesis produced, preferred over `final/nl`.

    Not a convenience choice — `final/nl` cannot be read this way. It
    contains cells inserted *after* synthesis for physical reasons
    (`sky130_fd_sc_hd__fill_1`, tap, decap, antenna diodes) which have no
    logic function, so `read_liberty -ignore_miss_func` skips them and
    Yosys then aborts: "Module `\\sky130_fd_sc_hd__fill_1' ... is not part
    of the design". Found by running this against a full flow after it
    had worked against a synthesis-only run.

    Comparing the synthesis netlist is also the right question to ask.
    "Did synthesis preserve the RTL's function?" is about synthesis
    output; the cells added afterwards are functionally inert by
    construction, and the final netlist is separately checked against the
    actual layout by LVS. What this does NOT independently verify is that
    those later physical insertions were themselves harmless — that
    remains covered by LVS and DRC, not by this.
    """
    for step in sorted(run_dir.glob("*yosys-synthesis"), key=lambda p: p.name):
        for f in step.glob("*.nl.v"):
            return f
    final = run_dir / "final" / "nl"
    if final.is_dir():
        for f in final.glob("*.nl.v"):
            return f
    candidates = sorted(run_dir.glob("*/*.nl.v"), key=lambda p: p.parent.name)
    if not candidates:
        raise FileNotFoundError(
            f"no gate netlist under {run_dir} — the run never completed synthesis")
    return candidates[-1]


def find_liberty(run_dir: Path) -> Path:
    """The liberty for the standard-cell library this run actually used.

    Read from the run's own resolved.json rather than assuming
    sky130_fd_sc_hd: tech_compare.py varies STD_CELL_LIBRARY, and
    checking a netlist against the wrong library's cell functions would
    either fail spuriously or prove nothing.
    """
    scl = "sky130_fd_sc_hd"
    resolved = run_dir / "resolved.json"
    if resolved.exists():
        scl = json.loads(resolved.read_text()).get("STD_CELL_LIBRARY", scl)
    lib_dir = PDK_ROOT / "sky130A" / "libs.ref" / scl / "lib"
    for lib in sorted(lib_dir.glob("*tt_025C_1v80.lib")):
        return lib
    raise FileNotFoundError(f"no typical-corner liberty for {scl} under {lib_dir}")


def check(design_dir: Path, run_dir: Path, top: str | None = None) -> dict:
    """Returns a structured result. Never raises on non-equivalence —
    "these differ" is a finding to record, not a crash."""
    rtl_files = sorted(design_dir.glob("src/*.v"))
    # Blackbox stubs describe a macro's interface with no behaviour, so
    # including them as `gold` would compare against an empty function.
    rtl_files = [f for f in rtl_files if "blackbox" not in f.name]
    if not rtl_files:
        raise FileNotFoundError(f"no RTL under {design_dir}/src")

    netlist = find_netlist(run_dir)
    liberty = find_liberty(run_dir)
    top = top or json.loads((design_dir / "config.json").read_text())["DESIGN_NAME"]

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        shutil.copy(netlist, work / "gate.v")
        shutil.copy(liberty, work / "cells.lib")
        rtl_args = []
        for i, f in enumerate(rtl_files):
            shutil.copy(f, work / f"rtl{i}.v")
            rtl_args.append(f"/work/rtl{i}.v")
        (work / "eq.ys").write_text(
            _YS.format(rtl_args=" ".join(rtl_args), top=top))

        cmd = ["docker", "run", "--rm", "--platform", "linux/amd64",
               "-v", f"{work}:/work", IMAGE, "yosys", "-s", "/work/eq.ys"]
        print(f"$ {' '.join(cmd)}", file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True)

    out = result.stdout + result.stderr
    proven = unproven = None
    m = re.search(r"Of those cells (\d+) are proven and (\d+) are unproven", out)
    if m:
        proven, unproven = int(m.group(1)), int(m.group(2))

    return {
        "design": design_dir.name,
        "top": top,
        "netlist": str(netlist),
        "liberty": str(liberty),
        "equivalent": result.returncode == 0,
        "proven_points": proven,
        "unproven_points": unproven,
        # A pass with zero compared points would be vacuous — surfaced so
        # a caller can tell "proved equivalent" from "compared nothing".
        "vacuous": result.returncode == 0 and not proven,
        "tail": out[-1200:] if result.returncode != 0 else None,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--top", default=None)
    args = ap.parse_args()

    r = check(args.design.resolve(), args.run_dir.resolve(), args.top)
    verdict = ("EQUIVALENT" if r["equivalent"] else "NOT EQUIVALENT")
    if r["vacuous"]:
        verdict = "INCONCLUSIVE (nothing was compared)"
    print(f"\n{r['design']} ({r['top']}): {verdict}")
    print(f"  netlist : {r['netlist']}")
    print(f"  liberty : {r['liberty']}")
    print(f"  points  : {r['proven_points']} proven, {r['unproven_points']} unproven")
    if r["tail"]:
        print(f"\n{r['tail']}")
    sys.exit(0 if r["equivalent"] and not r["vacuous"] else 1)


if __name__ == "__main__":
    main()
