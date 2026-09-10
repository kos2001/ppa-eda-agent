r"""Stage a run's DEF/LEF the way CircuitNet's feature extractor reads them.

CircuitNet (circuitnet/CircuitNet, BSD-3, v3.0 with an N45 PDK released
2026-05-17) is the AI4EDA dataset the Agentic-EDA survey (arXiv
2512.23189) lists as a benchmark. Its `feature_extraction/
process_data.py` turns a directory of designs into the dataset's feature
maps: one sub-directory per design under `--data_root`, the routed DEF
named by `--route_def_name` (default `detailed_route.def.gz`), the
technology and cell LEFs given by `--lef_path`, and the DEF's `UNITS
DISTANCE MICRONS` value as `--unit` (its default is 2000; sky130 DEF
says 1000). This writes that layout from a completed OpenLane run, so
what this pipeline produced can be fed to CircuitNet's extractor
unchanged.

WHAT THAT GIVES, AND WHAT IT DOES NOT. Read from CircuitNet's
feature_extraction/README.md:

  computable from DEF + LEF alone   macro_region, cell_density,
                                    RUDY (and its pin/long/short
                                    variants), instance_placement,
                                    pin_positions
  needs an Innovus report           DRC map (`verify_drc` report),
                                    congestion maps
                                    (`dumpNanoCongestArea`, route
                                    congestion), IR-drop and power maps
                                    (`report_power_rail_results`,
                                    `report_power`), timing windows
                                    (`write_timing_windows`)

So this export covers CircuitNet's *feature* side. Its *label* maps —
the DRC/congestion/IR images a model is trained to predict — come from
Innovus report formats OpenLane does not write, and translating
OpenROAD's equivalents into them is a separate piece of work that has
not been done. The manifest says this per run rather than leaving a
directory that looks complete. What this pipeline does have per run is
the scalar signoff result (DRC counts, WNS, utilisation, area) from
metrics.json, recorded in the manifest as scalar labels — real, but not
CircuitNet's pixel-map labels.

Verified for real on runs in pipeline/designs/*/runs/ that reached
final/def; see tests/test_export_circuitnet.py for the checks and the
spec's "Two things from the Chinese open-source track" section for the
run it was exercised on.
"""

from __future__ import annotations

import argparse
import gzip
import json
import re
import shutil
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGNS = REPO_ROOT / "pipeline" / "designs"

# CircuitNet's process_data.py defaults; kept as data so the manifest
# can print the exact invocation.
ROUTE_DEF_NAME = "detailed_route.def.gz"
LEF_DIR = "LEF"
DATA_DIR = "data"

FEATURES_FROM_DEF = (
    "macro_region", "cell_density", "RUDY", "RUDY_long", "RUDY_short",
    "pin_RUDY", "pin_RUDY_long", "instance_placement_micron",
    "instance_placement_gcell", "pin_positions",
)
LABELS_NEEDING_INNOVUS = {
    "DRC": "verify_drc report (drc.rpt)",
    "congestion_early_global_routing": "dumpNanoCongestArea report",
    "congestion_global_routing": "route congestion report",
    "IR_drop / power_*": "report_power_rail_results / report_power",
    "timing windows": "write_timing_windows (cts.twf)",
}

# Scalar labels this pipeline does have, from OpenLane's metrics.json.
SCALAR_LABEL_KEYS = (
    "magic__drc_error__count",
    "klayout__drc_error__count",
    "route__drc_errors",
    "route__antenna_violation__count",
    "design__instance__utilization__stdcell",
    "design__instance__area",
    "route__wirelength",
    "ir__drop__worst",
)

_UNITS_RE = re.compile(r"^\s*UNITS\s+DISTANCE\s+MICRONS\s+(\d+)\s*;", re.M)
_DESIGN_RE = re.compile(r"^\s*DESIGN\s+(\S+)\s*;", re.M)


def def_units(def_text: str) -> int | None:
    m = _UNITS_RE.search(def_text)
    return int(m.group(1)) if m else None


def host_path(container_path: str) -> Path:
    """OpenLane's resolved.json records PDK paths as seen inside the
    Docker image (`/pdk/...`); the same tree is mounted from REPO_ROOT.
    """
    p = Path(container_path)
    if p.is_absolute() and p.parts[1:2] == ("pdk",):
        return REPO_ROOT / Path(*p.parts[1:])
    return p


def run_lefs(run_dir: Path) -> list[Path]:
    """Tech LEF (nominal corner), cell LEFs and any extra macro LEFs the
    run was configured with, resolved to host paths. Only paths that
    exist are returned; a missing one is a real gap and is reported by
    the caller, not papered over.
    """
    resolved = run_dir / "resolved.json"
    if not resolved.exists():
        return []
    cfg = json.loads(resolved.read_text())
    paths: list[Path] = []
    tech = cfg.get("TECH_LEFS") or {}
    if isinstance(tech, dict):
        nom = next((v for k, v in tech.items() if k.startswith("nom")), None)
        if nom:
            paths.append(host_path(nom))
    elif isinstance(tech, str):
        paths.append(host_path(tech))
    for key in ("CELL_LEFS", "EXTRA_LEFS"):
        for p in cfg.get(key) or []:
            paths.append(host_path(p))
    seen = set()
    out = []
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


def scalar_labels(run_dir: Path) -> dict:
    mj = run_dir / "final" / "metrics.json"
    if not mj.exists():
        return {}
    metrics = json.loads(mj.read_text())
    return {k: metrics[k] for k in SCALAR_LABEL_KEYS if k in metrics}


def technology(run_dir: Path) -> str:
    """The standard-cell library a run used, which is what decides its
    LEF set and DEF unit. process_data.py takes one LEF set and one
    unit for a whole data_root, so runs are grouped by this and each
    group gets its own root. Falls back to the DEF unit when the
    resolved config is missing, which still keeps units from mixing.
    """
    resolved = run_dir / "resolved.json"
    if resolved.exists():
        scl = json.loads(resolved.read_text()).get("STD_CELL_LIBRARY")
        if scl:
            return str(scl)
    defs = sorted((run_dir / "final" / "def").glob("*.def"))
    unit = def_units(defs[0].read_text(errors="replace")) if defs else None
    return f"unit{unit}" if unit else "unknown"


def export_run(run_dir: Path, out_root: Path, name: str | None = None) -> dict:
    """Write one run into CircuitNet's data/<name>/ + LEF/ layout.

    Returns the manifest entry: what was written, the DEF unit, which
    LEFs were copied and which were missing, the scalar labels, and the
    feature/label coverage statement.
    """
    run_dir = Path(run_dir)
    def_dir = run_dir / "final" / "def"
    defs = sorted(def_dir.glob("*.def")) if def_dir.exists() else []
    if not defs:
        raise FileNotFoundError(f"{run_dir}: no final/def/*.def — run did not reach signoff")
    def_path = defs[0]
    text = def_path.read_text(errors="replace")
    unit = def_units(text)
    design = (_DESIGN_RE.search(text) or [None, def_path.stem])[1]
    name = name or f"{run_dir.parent.parent.name}__{run_dir.name}"

    dest = out_root / DATA_DIR / name
    dest.mkdir(parents=True, exist_ok=True)
    with open(def_path, "rb") as src, gzip.open(dest / ROUTE_DEF_NAME, "wb") as dst:
        shutil.copyfileobj(src, dst)

    lef_out = out_root / LEF_DIR
    lef_out.mkdir(parents=True, exist_ok=True)
    copied, missing = [], []
    for lef in run_lefs(run_dir):
        if lef.exists():
            target = lef_out / lef.name
            if not target.exists():
                shutil.copyfile(lef, target)
            copied.append(str(target.relative_to(out_root)))
        else:
            missing.append(str(lef))

    return {
        "name": name,
        "design": design,
        "run_dir": str(run_dir),
        "def": str((dest / ROUTE_DEF_NAME).relative_to(out_root)),
        "def_unit": unit,
        "lefs": copied,
        "lefs_missing": missing,
        "scalar_labels": scalar_labels(run_dir),
        "features_computable_from_def": list(FEATURES_FROM_DEF),
        "labels_not_exported": LABELS_NEEDING_INNOVUS,
    }


def process_data_command(out_root: Path, entries: list[dict]) -> str:
    """The CircuitNet invocation that reads what was written."""
    units = {e["def_unit"] for e in entries if e["def_unit"]}
    unit = units.pop() if len(units) == 1 else None
    lefs = sorted({lef for e in entries for lef in e["lefs"]})
    cmd = [
        "python process_data.py",
        f"--data_root {out_root / DATA_DIR}",
        f"--lef_path {' '.join(str(out_root / l) for l in lefs)}",
        f"--route_def_name {ROUTE_DEF_NAME}",
        f"--place_def_name {ROUTE_DEF_NAME}",
    ]
    if unit is not None:
        cmd.append(f"--unit {unit}")
    else:
        cmd.append("--unit <runs disagree on DEF units; export one technology at a time>")
    return " \\\n    ".join(cmd)


def runs_with_def(design_dir: Path) -> list[Path]:
    runs = design_dir / "runs"
    if not runs.exists():
        return []
    return sorted(r for r in runs.iterdir() if (r / "final" / "def").exists()
                  and any((r / "final" / "def").glob("*.def")))


def export(run_dirs: list[Path], out_root: Path) -> dict:
    """One CircuitNet root per technology under out_root, plus a
    manifest naming every run, every group's exact process_data.py
    invocation, and what is not exported.
    """
    entries = []
    failures = []
    groups: dict[str, list[dict]] = {}
    for r in run_dirs:
        try:
            scl = technology(Path(r))
            entry = export_run(r, out_root / scl)
        except (FileNotFoundError, OSError) as e:
            failures.append(str(e))
            continue
        entry["technology"] = scl
        entries.append(entry)
        groups.setdefault(scl, []).append(entry)
    manifest = {
        "format": "CircuitNet feature_extraction input layout, one root per technology",
        "source": "circuitnet/CircuitNet feature_extraction/process_data.py defaults",
        "groups": {
            scl: {
                "root": str(out_root / scl),
                "def_unit": sorted({e["def_unit"] for e in es}),
                "lefs": sorted({lef for e in es for lef in e["lefs"]}),
                "runs": [e["name"] for e in es],
                "process_data_command": process_data_command(out_root / scl, es),
            }
            for scl, es in groups.items()
        },
        "runs": entries,
        "failed": failures,
        "note": (
            "DEF+LEF only. CircuitNet's DRC/congestion/IR label maps come "
            "from Innovus reports this flow does not produce; the scalar "
            "signoff labels per run are in each entry's scalar_labels."
        ),
    }
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "manifest.json").write_text(json.dumps(manifest, indent=2))
    return manifest


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run-dir", type=Path, action="append", default=[],
                    help="a completed run directory (repeatable)")
    ap.add_argument("--design", action="append", default=[],
                    help="export every run of this design that reached final/def")
    ap.add_argument("--out", type=Path, required=True, help="CircuitNet data root to write")
    args = ap.parse_args(argv)

    run_dirs = list(args.run_dir)
    for d in args.design:
        run_dirs.extend(runs_with_def(DESIGNS / d))
    if not run_dirs:
        ap.error("nothing to export: give --run-dir or --design")
    manifest = export(run_dirs, args.out)
    for scl, g in manifest["groups"].items():
        print(f"{scl}: {len(g['runs'])} run(s), DEF unit {g['def_unit']}, "
              f"{len(g['lefs'])} LEF(s) -> {g['root']}")
        print("    " + g["process_data_command"].replace("\n", "\n    "))
    for e in manifest["runs"]:
        print(f"  {e['technology']}/{e['name']}: lefs={len(e['lefs'])} "
              f"missing={len(e['lefs_missing'])} labels={len(e['scalar_labels'])}")
    for f in manifest["failed"]:
        print(f"  skipped: {f}")
    print(manifest["note"])


if __name__ == "__main__":
    main()
