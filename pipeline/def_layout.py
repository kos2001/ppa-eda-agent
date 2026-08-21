"""Parses real DEF/LEF output into a compact JSON layout summary — real
cell placement and real routed-wire geometry, not a mock. Pattern
borrowed and adapted from ~/gitspace/ip-dev-fde/strongarm_sim's
LayoutView.tsx/virtuoso.ts (a working "Virtuoso Layout XL"-style SVG
layer viewer for a sibling project's synthesized GDS) — same idea,
applied to real OpenLane DEF/LEF instead of hand-synthesized transistor
geometry: {die, cells: [{inst, master, x, y, w, h, orient}], nets:
[{name, layer, segments: [[x1,y1,x2,y2], ...]}]}, all in real microns.

DEF/LEF are documented, real EDA interchange formats (Cadence/Si2) —
this is a small, targeted parser for the two constructs the dashboard
needs (COMPONENTS placement, NETS routed-wire segments, LEF SIZE), not
a general DEF/LEF parser.
"""
import re
from pathlib import Path

# sky130_ef_sc_hd__decap_* / sky130_fd_sc_hd__{fill,decap,tap}* / FILLER_* /
# TAP_* — real cells OpenLane inserts for DRC/antenna/tap-cell compliance,
# not part of the design's actual logic. Excluded from the visualization
# so real gates aren't buried under filler clutter; still real OpenLane
# output, just not interesting to show.
_NON_LOGIC_MASTER_RE = re.compile(r"(fill|decap|tap|diode|conb)", re.IGNORECASE)
_NON_LOGIC_INST_RE = re.compile(r"^(FILLER_|TAP_|PHY_)")

_SIZE_RE = re.compile(r"^MACRO\s+(\S+)")
_SIZE_LINE_RE = re.compile(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)")


def parse_lef_sizes(lef_paths: list[Path]) -> dict[str, tuple[float, float]]:
    """Real cell/macro footprints (width, height in microns) from LEF
    MACRO...SIZE blocks. Same SIZE syntax for both standard cells
    (CLASS CORE) and hard macros (CLASS BLOCK) — one parser covers both.
    """
    sizes: dict[str, tuple[float, float]] = {}
    for lef_path in lef_paths:
        if not lef_path.exists():
            continue
        current_macro = None
        for line in lef_path.read_text(errors="replace").splitlines():
            m = _SIZE_RE.match(line.strip())
            if m:
                current_macro = m.group(1)
                continue
            if current_macro:
                m = _SIZE_LINE_RE.search(line)
                if m:
                    sizes[current_macro] = (float(m.group(1)), float(m.group(2)))
                    current_macro = None
    return sizes


_COMPONENT_RE = re.compile(
    r"^\s*-\s+(\S+)\s+(\S+)\s+.*?\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+(\S+)\s*;"
)
_DIEAREA_RE = re.compile(
    r"DIEAREA\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)\s+\(\s*(-?\d+)\s+(-?\d+)\s*\)"
)
_UNITS_RE = re.compile(r"UNITS\s+DISTANCE\s+MICRONS\s+(\d+)")
_LAYER_PT_RE = re.compile(r"(met\d|li1)\s+\(\s*(\*|-?\d+)\s+(\*|-?\d+)\s*\)")


def parse_def(def_path: Path, cell_sizes: dict[str, tuple[float, float]]) -> dict:
    """Real placement (from COMPONENTS) and real routed-net geometry
    (from NETS' ROUTED/NEW segments) — every coordinate here is what
    OpenROAD/TritonRoute actually placed/routed, converted from DEF
    database units (typically 1000/micron) to real microns.
    """
    text = def_path.read_text(errors="replace")

    units_m = _UNITS_RE.search(text)
    dbu_per_um = int(units_m.group(1)) if units_m else 1000

    die_m = _DIEAREA_RE.search(text)
    die = None
    if die_m:
        x0, y0, x1, y1 = (int(g) / dbu_per_um for g in die_m.groups())
        die = [x0, y0, x1, y1]

    components_block = _section(text, "COMPONENTS")
    cells = []
    for line in components_block.splitlines():
        m = _COMPONENT_RE.match(line)
        if not m:
            continue
        inst, master, x_dbu, y_dbu, orient = m.groups()
        if _NON_LOGIC_INST_RE.match(inst) or _NON_LOGIC_MASTER_RE.search(master):
            continue
        size = cell_sizes.get(master)
        if size is None:
            continue  # unknown master (shouldn't happen for a real LEF match)
        cells.append({
            "inst": inst,
            "master": master,
            "x": int(x_dbu) / dbu_per_um,
            "y": int(y_dbu) / dbu_per_um,
            "w": size[0],
            "h": size[1],
            "orient": orient,
        })

    nets_block = _section(text, "NETS")
    nets = []
    for net_stanza in re.split(r"\n(?=\s*-\s+\S)", nets_block):
        name_m = re.match(r"\s*-\s+(\S+)", net_stanza)
        if not name_m:
            continue
        name = name_m.group(1)
        segments = []
        last = None
        for layer, xr, yr in _LAYER_PT_RE.findall(net_stanza):
            x = last[0] if xr == "*" and last else (None if xr == "*" else int(xr) / dbu_per_um)
            y = last[1] if yr == "*" and last else (None if yr == "*" else int(yr) / dbu_per_um)
            if x is None or y is None:
                continue
            if last is not None and last[2] == layer:
                segments.append({"layer": layer, "x1": last[0], "y1": last[1], "x2": x, "y2": y})
            last = (x, y, layer)
        if segments:
            nets.append({"name": name, "segments": segments})

    return {"die": die, "cells": cells, "nets": nets}


def _section(text: str, name: str) -> str:
    m = re.search(rf"^{name}\b.*?\n(.*?)\nEND {name}\b", text, re.DOTALL | re.MULTILINE)
    return m.group(1) if m else ""


def layout_summary(run_dir: Path, extra_lef_paths: list[Path]) -> dict | None:
    """Real layout summary for a completed run, or None if the run's
    final DEF isn't there (e.g. the run failed before producing one).
    """
    def_dir = run_dir / "final" / "def"
    if not def_dir.is_dir():
        return None
    def_files = list(def_dir.glob("*.def"))
    if not def_files:
        return None

    from run_stage import PDK_ROOT
    sky130_hd_lef = PDK_ROOT / "sky130A" / "libs.ref" / "sky130_fd_sc_hd" / "lef" / "sky130_fd_sc_hd.lef"
    cell_sizes = parse_lef_sizes([sky130_hd_lef, *extra_lef_paths])
    return parse_def(def_files[0], cell_sizes)
