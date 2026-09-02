"""Collects the rules and constraints a run is actually held to.

Why: the pipeline's fourth stage is called "Physical Constraint
Evaluation" and the console never showed what the constraints were. A
candidate could be reported as failing a constraint the reader had no
way to see. Worse, the two conclusions this project has had to overturn
were both cases of arguing about a limit instead of reading it — a
0.04 ns max_transition assumed unmeetable, and a 0.01 pF load assumed to
be a net measurement.

Two different kinds of rule get collected here, and the distinction
matters because one is negotiable and the other is not:

  - PDK rules (tech LEF): manufacturing grid, site geometry, routing
    layer pitch/width/spacing/min-area/density. Fixed by the process.
    Nothing in a config can change them.
  - Design constraints (config.json + run_spec.json): die area, clock
    period, transition limits, fixed macro placements, PPA targets.
    Chosen by us, and therefore the things a repair may propose changing.

Read from the real files, never defaulted: an invented design rule reads
exactly like a measured one.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PDK_ROOT = REPO_ROOT / "pdk"


class DesignRuleError(RuntimeError):
    """Raised rather than returning partial rules — a constraint panel
    that silently omits a rule is worse than one that fails to load."""


def _num(body: str, pattern: str) -> float | None:
    m = re.search(pattern, body, re.M)
    return float(m.group(1)) if m else None


def find_tech_lef(scl: str = "sky130_fd_sc_hd") -> Path:
    """Locates the nominal-corner tech LEF for a standard cell library."""
    versions = PDK_ROOT / "volare" / "sky130" / "versions"
    if not versions.is_dir():
        raise DesignRuleError(f"no PDK at {versions}")
    for v in sorted(versions.iterdir()):
        p = v / "sky130A" / "libs.ref" / scl / "techlef" / f"{scl}__nom.tlef"
        if p.is_file():
            return p
    raise DesignRuleError(f"no {scl}__nom.tlef under {versions}")


def read_pdk_rules(tlef: Path | str) -> dict:
    """Parses the process rules from a tech LEF.

    Only routing layers are reported. Cut and masterslice layers have no
    pitch or min-width in the sense a floorplan is checked against, and
    listing them with blank fields invites reading a blank as a zero.
    """
    tlef = Path(tlef)
    if not tlef.is_file():
        raise DesignRuleError(f"tech LEF not found: {tlef}")
    txt = tlef.read_text(encoding="utf-8", errors="replace")

    grid = _num(txt, r"^MANUFACTURINGGRID\s+([\d.]+)\s*;")
    dbu = _num(txt, r"^\s*DATABASE MICRONS\s+([\d.]+)\s*;")

    sites = []
    for m in re.finditer(r"^SITE (\w+)(.*?)^END \1", txt, re.S | re.M):
        body = m.group(2)
        sm = re.search(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)\s*;", body)
        if not sm:
            continue
        cm = re.search(r"CLASS\s+(\w+)\s*;", body)
        sites.append({
            "name": m.group(1),
            "width_um": float(sm.group(1)),
            "height_um": float(sm.group(2)),
            "class": cm.group(1) if cm else None,
        })

    layers = []
    for m in re.finditer(r"^LAYER (\w+)\s*$(.*?)^END \1", txt, re.S | re.M):
        name, body = m.group(1), m.group(2)
        if not re.search(r"TYPE\s+ROUTING", body):
            continue
        # Spacing appears either as a plain SPACING or as the first entry
        # of a width-dependent SPACINGTABLE; both mean "minimum".
        spacing = _num(body, r"^\s*SPACING\s+([\d.]+)\s*;")
        if spacing is None:
            st = re.search(r"SPACINGTABLE(.*?);", body, re.S)
            if st:
                widths = re.findall(r"WIDTH\s+[\d.]+\s+([\d.]+)", st.group(1))
                if widths:
                    spacing = float(widths[0])
        dm = re.search(r"^\s*DIRECTION\s+(\w+)\s*;", body, re.M)
        layers.append({
            "name": name,
            "direction": dm.group(1).lower() if dm else None,
            "pitch_um": _num(body, r"^\s*PITCH\s+([\d.]+)\s*;"),
            "min_width_um": _num(body, r"^\s*WIDTH\s+([\d.]+)\s*;"),
            "min_spacing_um": spacing,
            "min_area_um2": _num(body, r"^\s*AREA\s+([\d.]+)\s*;"),
            "thickness_um": _num(body, r"^\s*THICKNESS\s+([\d.]+)\s*;"),
            "max_density_pct": _num(body, r"^\s*MAXIMUMDENSITY\s+([\d.]+)\s*;"),
            "resistance_ohm_per_sq": _num(
                body, r"^\s*RESISTANCE RPERSQ\s+([\d.eE+-]+)\s*;"),
        })

    if not layers:
        raise DesignRuleError(f"{tlef}: no routing layers found")
    return {
        "source": str(tlef),
        "manufacturing_grid_um": grid,
        "database_units_per_um": dbu,
        "sites": sites,
        "routing_layers": layers,
    }


# Config keys that are genuinely constraints on the result, as opposed to
# file paths and bookkeeping. Anything not listed is left out rather than
# dumped wholesale — a panel showing VERILOG_FILES next to CLOCK_PERIOD
# teaches the reader nothing about why a candidate failed.
_CONSTRAINT_KEYS = {
    "CLOCK_PORT": "clock port",
    "CLOCK_PERIOD": "clock period (ns)",
    "MAX_TRANSITION_CONSTRAINT": "max transition (ns)",
    "MAX_FANOUT_CONSTRAINT": "max fanout",
    "MAX_CAPACITANCE_CONSTRAINT": "max capacitance (pF)",
    "CLOCK_UNCERTAINTY_CONSTRAINT": "clock uncertainty (ns)",
    "CLOCK_TRANSITION_CONSTRAINT": "clock transition (ns)",
    "DIE_AREA": "die area (um)",
    "CORE_AREA": "core area (um)",
    "FP_SIZING": "floorplan sizing",
    "FP_CORE_UTIL": "target core utilization (%)",
    "PL_TARGET_DENSITY_PCT": "placement density (%)",
    "GRT_ALLOW_CONGESTION": "allow routing congestion",
    "RUN_ANTENNA_REPAIR": "antenna repair",
}


def read_design_constraints(design_dir: Path | str) -> dict:
    """Reads the constraints a design sets on itself.

    Separates the fixed-placement macros out of MACROS: a macro pinned at
    an absolute location is a hard physical constraint on everything that
    talks to it, and in sram_wrapper it was the constraint that mattered
    most while being invisible in the console.
    """
    design_dir = Path(design_dir)
    cfg_path = design_dir / "config.json"
    if not cfg_path.is_file():
        raise DesignRuleError(f"no config.json in {design_dir}")
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))

    settings = [
        {"key": k, "label": _CONSTRAINT_KEYS[k], "value": cfg[k]}
        for k in _CONSTRAINT_KEYS
        if k in cfg
    ]

    macros = []
    for name, spec in (cfg.get("MACROS") or {}).items():
        for inst, place in (spec.get("instances") or {}).items():
            macros.append({
                "macro": name,
                "instance": inst,
                "location_um": place.get("location"),
                "orientation": place.get("orientation"),
            })

    targets = {}
    spec_path = design_dir / "run_spec.json"
    if spec_path.is_file():
        targets = json.loads(spec_path.read_text(encoding="utf-8")).get("targets", {})

    return {
        "design_name": cfg.get("DESIGN_NAME"),
        "settings": settings,
        "fixed_macros": macros,
        "power_connections": cfg.get("PDN_MACRO_CONNECTIONS") or [],
        "targets": targets,
    }


def collect(design_dir: Path | str, scl: str = "sky130_fd_sc_hd") -> dict:
    """Everything a reader needs to judge a verdict, in one object.

    Never raises for a missing PDK: the design's own constraints are
    still worth showing on a machine that has no PDK enabled, and a
    console that renders nothing is the state this was written to fix.
    The failure is reported in place so it cannot be mistaken for
    "this process has no rules".
    """
    out = {"design": read_design_constraints(design_dir)}
    try:
        out["pdk"] = read_pdk_rules(find_tech_lef(scl))
    except DesignRuleError as e:
        out["pdk"] = None
        out["pdk_error"] = str(e)
    return out
