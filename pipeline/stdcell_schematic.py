#!/usr/bin/env python3
"""Open a standard cell and see the transistors.

The gate-level view (gate_schematic.py) draws a design's cells as boxes
with pins, which is the right abstraction right up to the moment the
question becomes "what IS an a21oi_2". In Virtuoso you descend into the
cell; here there was nowhere to descend to — sky130A ships 437 xschem
*symbols* for sky130_fd_sc_hd and not one schematic behind them.

What it does ship is every cell's transistor netlist, in
`libs.ref/sky130_fd_sc_hd/cdl/sky130_fd_sc_hd.cdl`, and a SPICE-to-xschem
importer beside the Verilog one. So the schematic is generated from the
foundry's own netlist by the PDK's own tool, and the drawing cannot
disagree with what the cell actually is.

Three transformations stand between the two, each one a real format
difference rather than a preference:

- The CDL writes `.SUBCKT` and the importer matches `.subckt`.
- The CDL writes devices as `MMIN1 Y A VGND VNB nfet_01v8 m=2 w=0.65
  l=0.15 ...` — an M-prefixed device with a bare model name. The importer
  resolves a symbol from the full `sky130_fd_pr__` name on an X-prefixed
  subcircuit call, which is also what makes the result simulatable
  against the PDK models.
- Parameters have to be spelled the way the symbols spell them. The
  sky130_fd_pr symbols read `W`/`L`; passing the CDL's lowercase `w`/`l`
  leaves them as unknown attributes, and the drawing then annotates every
  device with the symbol's default `1 x 1 / 0.15` while the netlist says
  something else. Found by reading the first render — the numbers were
  wrong in the only place a human would look.

The importer's library path is hardcoded to `$HOME/.xschem/...`, so a
copy with that one line rewritten is what runs. Same move as
gf180_drc.py patching the PDK deck's logger: use the PDK's tool, change
the one line that assumes an installation this repo does not have.

Usage:
    stdcell_schematic.py --list inv           # matching cells
    stdcell_schematic.py --cell sky130_fd_sc_hd__inv_2 --draw
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import custom_bridge
from toolchain import platform_args

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"
CDL = PDK_ROOT / "sky130A" / "libs.ref" / "sky130_fd_sc_hd" / "cdl" / "sky130_fd_sc_hd.cdl"
IMPORTER = (PDK_ROOT / "sky130A" / "libs.tech" / "xschem" / "xschem_verilog_import"
            / "make_sky130_sch_from_spice.awk")
# Where the generated cells land. Under analog/ so the console lists them
# with the other transistor-level cells, and so a testbench can
# instantiate one — a standard cell you can simulate is more useful than
# a standard cell you can only look at.
OUT_DIR = REPO_ROOT / "pipeline" / "analog" / "stdcell"

_DEVICE = re.compile(r"^M\S*\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(.*)$",
                     re.IGNORECASE)


def _cdl_text() -> str:
    """The library CDL with continuation lines joined."""
    return re.sub(r"\n\+\s*", " ", CDL.read_text(encoding="utf-8"))


def cells(pattern: str | None = None) -> list[str]:
    """Every cell in the library, optionally filtered by substring."""
    if not CDL.is_file():
        return []
    found = re.findall(r"^\.SUBCKT\s+(\S+)", _cdl_text(), re.MULTILINE)
    if pattern:
        found = [c for c in found if pattern.lower() in c.lower()]
    return sorted(set(found))


def drawable(cell: str) -> bool:
    block = subckt(cell)
    return bool(block) and not missing_symbols(block)


def subckt(cell: str) -> str | None:
    """One cell's CDL block, continuations already joined."""
    match = re.search(rf"^\.SUBCKT\s+{re.escape(cell)}\s.*?^\.ENDS.*?$",
                      _cdl_text(), re.MULTILINE | re.DOTALL)
    return match.group(0) if match else None


def to_spice(block: str) -> str:
    """CDL block -> the SPICE the importer reads. See the module docstring."""
    out = []
    for i, line in enumerate(block.splitlines()):
        stripped = line.strip()
        if stripped.upper().startswith(".SUBCKT"):
            out.append(".subckt " + " ".join(stripped.split()[1:]))
        elif stripped.upper().startswith(".ENDS"):
            out.append(".ends")
        elif stripped.startswith("*") or not stripped:
            continue
        else:
            match = _DEVICE.match(stripped)
            if not match:
                continue
            drain, gate, source, bulk, model, rest = match.groups()
            params = dict(p.split("=", 1) for p in rest.split() if "=" in p)
            out.append(
                f"X{i} {drain} {gate} {source} {bulk} sky130_fd_pr__{model} "
                f"W={params.get('w', '1')} L={params.get('l', '0.15')} "
                f"nf=1 m={params.get('m', '1')}")
    return "\n".join(out) + "\n"


def device_count(block: str) -> int:
    return sum(1 for line in block.splitlines()
               if _DEVICE.match(line.strip()))


SYMBOL_DIR = PDK_ROOT / "sky130A" / "libs.tech" / "xschem" / "sky130_fd_pr"


def missing_symbols(block: str) -> dict:
    """Models this cell uses that the symbol library has no symbol for.

    Not hypothetical: every sequential cell in this library uses
    `special_nfet_01v8`, which is a real device (sky130A.tech defines it,
    netgen's setup lists it) with no entry in libs.tech/xschem's
    sky130_fd_pr. The importer draws what it can and drops the rest
    without saying so — dfxtp_2 came out with 21 of its 24 transistors —
    so the answer is to refuse and name the model rather than to
    substitute a device that is not the one in the netlist.
    """
    counts: dict = {}
    for line in block.splitlines():
        match = _DEVICE.match(line.strip())
        if not match:
            continue
        model = match.group(5)
        if not (SYMBOL_DIR / f"{model}.sym").is_file():
            counts[model] = counts.get(model, 0) + 1
    return counts


def convert(cell: str, out_dir: Path | None = None, timeout: int = 300) -> dict:
    """Generate the cell's transistor-level schematic and symbol."""
    block = subckt(cell)
    if block is None:
        return {"ok": False, "cell": cell,
                "error": f"{cell} is not in {CDL.name}",
                "hint": cells(cell.split("__")[-1][:4])[:10]}
    absent = missing_symbols(block)
    if absent:
        named = ", ".join(f"{m} ({n} device(s))" for m, n in sorted(absent.items()))
        return {"ok": False, "cell": cell, "missing_symbols": absent,
                "error": f"{cell} uses models the xschem library has no symbol "
                          f"for: {named}. Drawing it would silently leave those "
                          f"transistors out."}
    if not (shutil.which("docker") and custom_bridge.ensure_xschem_image()):
        return {"ok": False, "cell": cell,
                "error": "xschem is not installed and its image could not be built"}
    out_dir = out_dir or OUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as td:
        work = Path(td)
        (work / f"{cell}.spice").write_text(to_spice(block), encoding="utf-8")
        # The importer's library path assumes ~/.xschem; rewrite that one
        # line rather than requiring the installation it assumes.
        patched = IMPORTER.read_text(encoding="utf-8").replace(
            'xschem_lib_path="$HOME/.xschem/xschem_library/xschem_sky130/sky130_fd_pr"',
            'xschem_lib_path="/pdk/sky130A/libs.tech/xschem/sky130_fd_pr"')
        if "/pdk/sky130A" not in patched:
            return {"ok": False, "cell": cell,
                    "error": "could not retarget the importer's library path — "
                              "its hardcoded line has changed"}
        (work / "import_spice.awk").write_text(patched, encoding="utf-8")
        argv = ["docker", "run", "--rm", *platform_args(),
                "-v", f"{PDK_ROOT}:/pdk:ro", "-v", f"{work}:/work",
                "-w", "/work", "-e", "PDK_ROOT=/pdk",
                custom_bridge.XSCHEM_IMAGE,
                "awk", "-f", "import_spice.awk", f"{cell}.spice"]
        try:
            result = subprocess.run(argv, capture_output=True, text=True,
                                    timeout=timeout, stdin=subprocess.DEVNULL)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"ok": False, "cell": cell, "error": str(exc)}

        produced = {p.name: p for p in list(work.glob("*.sch")) + list(work.glob("*.sym"))}
        wanted = [f"{cell}.sch", f"{cell}.sym"]
        if not all(name in produced for name in wanted):
            return {"ok": False, "cell": cell,
                    "error": f"importer wrote {sorted(produced) or 'nothing'}",
                    "log": (result.stdout + result.stderr)[-800:]}
        drawn = produced[f"{cell}.sch"].read_text(encoding="utf-8")
        # The file is the verdict, and so is what is in it: a schematic
        # with fewer devices than the netlist is a wrong drawing, not a
        # simplified one.
        devices = drawn.count("C {sky130_fd_pr/")
        expected = device_count(block)
        if devices != expected:
            return {"ok": False, "cell": cell,
                    "error": f"drew {devices} devices, the CDL has {expected}"}
        for name in wanted:
            shutil.copy(produced[name], out_dir / name)

    return {"ok": True, "cell": cell, "devices": expected,
            "schematic": str((out_dir / f"{cell}.sch").relative_to(REPO_ROOT)),
            "symbol": str((out_dir / f"{cell}.sym").relative_to(REPO_ROOT)),
            "source": str(CDL.relative_to(REPO_ROOT))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--list", dest="pattern", nargs="?", const="", default=None,
                    help="list matching cells instead of converting")
    ap.add_argument("--drawable-only", action="store_true",
                    help="with --list, drop cells whose devices have no symbol")
    ap.add_argument("--cell", default=None)
    ap.add_argument("--draw", action="store_true")
    args = ap.parse_args()

    if args.pattern is not None:
        found = cells(args.pattern or None)
        if args.drawable_only:
            found = [c for c in found if drawable(c)]
        print("\n".join(found) or "no matching cells")
        print(f"\n{len(found)} cell(s)", file=sys.stderr)
        return
    if not args.cell:
        ap.error("--cell or --list is required")

    out = convert(args.cell)
    if not out["ok"]:
        print(f"{args.cell}: {out['error']}", file=sys.stderr)
        if out.get("hint"):
            print("did you mean: " + ", ".join(out["hint"]), file=sys.stderr)
        sys.exit(1)
    print(f"{out['cell']}: {out['devices']} transistors from {out['source']}")
    print(f"  {out['schematic']}")
    if args.draw:
        drawn = custom_bridge.render_schematic(REPO_ROOT / out["schematic"])
        print(f"  {drawn.metadata.get('svg')}" if drawn.ok
              else f"  render failed: {drawn.errors}")


if __name__ == "__main__":
    main()
