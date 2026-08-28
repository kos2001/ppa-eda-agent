r"""Automatic macro placement, driven through OpenROAD instead of OpenLane.

sram_wrapper pins its SRAM by hand at (110, 150) µm. The measured
driver-to-pin distance on addr1[0] is 249 µm — a two-pin net, one driver,
one macro input — against the 144.5 µm that lib_query says the strongest
repair buffer can drive within the macro's own 40 ps limit. So the hand
placement is that case's open problem, and letting a tool choose is the
experiment worth running.

OpenLane cannot do it: `OpenROAD.BasicMacroPlacement` is not in the
Classic flow, and building a custom flow around it proved the step is a
stub — `def get_script_path(self): raise NotImplementedError()`. That
conclusion was right about OpenLane and wrong about the toolchain. The
OpenROAD binary inside the very same image ships the real thing:

    rtl_macro_placer     Hier-RTLMP (OpenROAD src/mpl2), the hierarchical
                         automatic macro placer
    macro_placement      the simulated-annealing placer
    place_macro          place one macro explicitly
    write_macro_placement

So this drives OpenROAD directly, exactly as odb_query.py already does
for measurement. Same image, same .odb, no new dependency.

Deliberately a *separate* experiment rather than a step spliced into the
pipeline's normal flow. Every calibration in this project is against
Classic, and a macro position chosen here has to be carried back as an
ordinary `MACROS.instances.location` for the real run to stay
comparable. What this produces is a coordinate and a measurement, not a
new flow.

Usage:
    macro_place.py --run-dir designs/sram_wrapper/runs/<tag> \
        [--halo 5] [--wirelength-weight 1.0] [--annealing]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from toolchain import OPENLANE_IMAGE as IMAGE
from odb_query import find_odb

# Reports each macro's placement before and after, so the comparison is
# in the tool's own output rather than assembled afterwards from two
# runs. `-wirelength_weight` is raised from its default because the
# constraint being chased here is net length to the macro's pins, not
# area or aspect ratio.
_OR_SCRIPT = r"""
import json
from openroad import Design, Tech

def macro_rows(block):
    dbu = block.getDbUnitsPerMicron()
    out = []
    for inst in block.getInsts():
        master = inst.getMaster()
        if not master.isBlock():
            continue
        x, y = inst.getLocation()
        out.append({
            "instance": inst.getName(),
            "master": master.getName(),
            "x_um": x / dbu,
            "y_um": y / dbu,
            "orient": str(inst.getOrient()),
            "placed": inst.isPlaced(),
            "status": str(inst.getPlacementStatus()),
        })
    return out

tech = Tech()
design = Design(tech)
design.readDb("/work/design.odb")
block = design.getBlock()

before = macro_rows(block)
result = {"before": before, "after": None, "error": None, "placer": PLACER}

if not before:
    result["error"] = "no macro (block) instances in this design"
else:
    # A macro fixed by the floorplan will not be moved by either placer;
    # unfix it so the experiment is actually run, and say so.
    unfixed = []
    for inst in block.getInsts():
        if inst.getMaster().isBlock() and inst.isFixed():
            inst.setPlacementStatus("PLACED")
            unfixed.append(inst.getName())
    result["unfixed"] = unfixed
    try:
        if PLACER == "rtl_macro_placer":
            design.evalTclString(
                "rtl_macro_placer -halo_width %f -halo_height %f "
                "-wirelength_weight %f" % (HALO, HALO, WL_WEIGHT))
        else:
            design.evalTclString(
                "macro_placement -halo {%f %f} -style corner_min_wl" % (HALO, HALO))
        result["after"] = macro_rows(block)
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)

print("###JSON###" + json.dumps(result))
"""


class MacroPlaceError(RuntimeError):
    pass


def autoplace(run_dir: Path | str, halo_um: float = 5.0,
              wirelength_weight: float = 1.0,
              placer: str = "rtl_macro_placer") -> dict:
    """Runs a real OpenROAD macro placer against this run's .odb.

    Returns each macro's position before and after. The .odb is copied,
    never modified in place: this is a measurement of what a placer would
    choose, and a run directory is evidence of what actually happened.
    """
    if placer not in ("rtl_macro_placer", "macro_placement"):
        raise MacroPlaceError(f"unknown placer {placer!r}")

    # Absolute: docker rejects a relative path as a volume name.
    odb_path = find_odb(Path(run_dir).resolve())
    work = odb_path.parent
    script = work / "_macro_place.py"
    script.write_text(
        f"PLACER = {placer!r}\nHALO = {halo_um!r}\nWL_WEIGHT = {wirelength_weight!r}\n"
        + _OR_SCRIPT
    )
    linked = work / "design.odb"
    created = False
    if not linked.exists():
        linked.write_bytes(odb_path.read_bytes())
        created = True
    cmd = [
        "docker", "run", "--rm", "--platform", "linux/amd64",
        "-v", f"{work}:/work",
        IMAGE,
        "openroad", "-no_init", "-exit", "-python", "/work/_macro_place.py",
    ]
    try:
        print(f"$ {' '.join(cmd)}", file=sys.stderr)
        result = subprocess.run(cmd, capture_output=True, text=True)
        marker = "###JSON###"
        if marker not in result.stdout:
            raise MacroPlaceError(
                f"openroad produced no result (exit {result.returncode}); "
                f"stderr tail: {result.stderr[-800:]}")
        data = json.loads(result.stdout.split(marker, 1)[1].splitlines()[0])
        data["odb"] = str(odb_path)
        return data
    finally:
        script.unlink(missing_ok=True)
        if created:
            linked.unlink(missing_ok=True)


def moved(result: dict) -> list[dict]:
    """Macros the placer actually relocated, with the distance moved.

    A placer that returns the input unchanged has told you something —
    that the hand placement is already what it would pick — and that is
    worth reporting as a result rather than as silence.
    """
    before = {m["instance"]: m for m in result.get("before") or []}
    out = []
    for after in result.get("after") or []:
        b = before.get(after["instance"])
        if not b:
            continue
        dx = after["x_um"] - b["x_um"]
        dy = after["y_um"] - b["y_um"]
        out.append({
            "instance": after["instance"],
            "from_um": [b["x_um"], b["y_um"]],
            "to_um": [after["x_um"], after["y_um"]],
            "moved_um": round((dx * dx + dy * dy) ** 0.5, 3),
        })
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--halo", type=float, default=5.0)
    ap.add_argument("--wirelength-weight", type=float, default=1.0)
    ap.add_argument("--annealing", action="store_true",
                    help="use macro_placement instead of rtl_macro_placer")
    args = ap.parse_args()

    result = autoplace(
        args.run_dir, args.halo, args.wirelength_weight,
        "macro_placement" if args.annealing else "rtl_macro_placer")
    result["moved"] = moved(result)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
