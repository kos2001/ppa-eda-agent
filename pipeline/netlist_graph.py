r"""Extracts a viewable gate-level netlist from Yosys' own JSON output.

Yosys already writes `<design>.nl.v.json` during synthesis, and the
pipeline already recorded its *path* — into runs/, which is gitignored
and routinely deleted. So the console could tell you a netlist had
existed and never show you one. The layout has had a real rendered view
since early on; the circuit it implements had none.

The file is ~470 KB for a 42-cell design because Yosys emits a blackbox
module for every cell in the standard-cell library alongside the design.
That library half is not waste, though: it states each cell type's port
directions, which is what turns a bag of net numbers into a directed
graph. Pin direction is read from it rather than guessed from pin names
(X/Y/Q are outputs on sky130, but that is a convention, not a rule, and
a wrong guess silently reverses an edge).

What comes out is small enough to store in a case: nodes for ports and
cells, edges for the nets between them, with the design's own module
picked out and the library discarded.
"""

from __future__ import annotations

import json
from pathlib import Path

# A case is read in full by the browser on every load, so the graph is
# capped. Truncation is recorded rather than silent — a partial
# schematic that looks complete is worse than one that says it is not.
MAX_CELLS = 400


class NetlistError(RuntimeError):
    pass


def find_netlist_json(run_dir: Path | str) -> Path | None:
    """Yosys' JSON netlist for a run, if synthesis got that far."""
    hits = sorted(Path(run_dir).glob("*yosys-synthesis/*.nl.v.json"))
    return hits[0] if hits else None


def _port_directions(modules: dict) -> dict[str, dict[str, str]]:
    """cell type -> {pin: "input"|"output"}, from the library blackboxes
    Yosys ships in the same file."""
    out: dict[str, dict[str, str]] = {}
    for name, mod in modules.items():
        ports = mod.get("ports") or {}
        if not ports:
            continue
        out[name] = {p: v.get("direction", "input") for p, v in ports.items()}
    return out


def _pick_design_module(modules: dict, design_name: str | None) -> tuple[str, dict]:
    """The design's own module, not one of the library blackboxes.

    Prefers an exact name match; otherwise takes the module that actually
    instantiates cells, since library entries have none.
    """
    if design_name and design_name in modules and modules[design_name].get("cells"):
        return design_name, modules[design_name]
    with_cells = [(n, m) for n, m in modules.items() if m.get("cells")]
    if not with_cells:
        raise NetlistError("no module in this netlist instantiates any cells")
    # The design instantiates the most; library cells instantiate none.
    return max(with_cells, key=lambda nm: len(nm[1]["cells"]))


def build_graph(netlist_json: Path | str, design_name: str | None = None) -> dict:
    """A directed gate-level graph for one design module."""
    path = Path(netlist_json)
    if not path.is_file():
        raise NetlistError(f"netlist JSON not found: {path}")
    data = json.loads(path.read_text())
    modules = data.get("modules") or {}
    if not modules:
        raise NetlistError(f"{path}: no modules")

    top_name, top = _pick_design_module(modules, design_name)
    directions = _port_directions(modules)

    ports = []
    for name, spec in (top.get("ports") or {}).items():
        ports.append({
            "name": name,
            "direction": spec.get("direction", "input"),
            "bits": [b for b in spec.get("bits", []) if isinstance(b, int)],
        })

    raw_cells = list((top.get("cells") or {}).items())
    truncated = len(raw_cells) > MAX_CELLS
    cells = []
    for name, spec in raw_cells[:MAX_CELLS]:
        ctype = spec.get("type", "?")
        dirs = directions.get(ctype, {})
        inputs, outputs = {}, {}
        for pin, bits in (spec.get("connections") or {}).items():
            nets = [b for b in bits if isinstance(b, int)]
            if not nets:
                continue
            # Unknown pin on an unknown type: treat as input. That keeps
            # the node in the graph without inventing a driver, which
            # would produce a plausible but wrong edge.
            if dirs.get(pin, "input") == "output":
                outputs[pin] = nets
            else:
                inputs[pin] = nets
        cells.append({
            # Yosys names ABC-produced cells things like
            # "$abc$272$auto$blifparse.cc:396:parse_blif$273" — kept as
            # the identity but far too long to render, so a short label
            # is derived once here rather than in the view.
            "name": name,
            "label": name.split("$")[-1] if name.startswith("$") else name,
            "type": ctype,
            "inputs": inputs,
            "outputs": outputs,
        })

    # Human-readable names for net numbers, so a wire can be identified
    # as "ctr_a[3]" rather than "17".
    net_names: dict[str, str] = {}
    for name, spec in (top.get("netnames") or {}).items():
        bits = spec.get("bits", [])
        for i, bit in enumerate(bits):
            if not isinstance(bit, int):
                continue
            label = name if len(bits) == 1 else f"{name}[{i}]"
            # Yosys keeps both the RTL name and internal aliases for the
            # same net; prefer the one a person wrote. Keyed by str(bit)
            # throughout — comparing an int against these string keys
            # makes the check always true and quietly loses the
            # preference (found by seeing "$abc$...MuxGate$241" where
            # "ctr_a[0]" belonged).
            key = str(bit)
            if key not in net_names or not name.startswith("$"):
                net_names[key] = label

    return {
        "top": top_name,
        "source": str(path),
        "ports": ports,
        "cells": cells,
        "net_names": net_names,
        "cell_count": len(raw_cells),
        "truncated": truncated,
    }


def cell_type_histogram(graph: dict) -> list[dict]:
    """Which standard cells the synthesiser actually chose, most first."""
    counts: dict[str, int] = {}
    for c in graph.get("cells", []):
        counts[c["type"]] = counts.get(c["type"], 0) + 1
    return [{"type": t, "count": n}
            for t, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def summary(run_dir: Path | str, design_name: str | None = None) -> dict | None:
    """Graph for a run, or None when synthesis produced no JSON.

    Non-fatal by design: a case that cost real OpenLane time must not be
    lost because the netlist could not be parsed, so the failure is
    recorded in place instead.
    """
    path = find_netlist_json(run_dir)
    if path is None:
        return None
    try:
        graph = build_graph(path, design_name)
    except Exception as e:  # noqa: BLE001 - recorded, not silenced
        return {"error": f"{type(e).__name__}: {e}"}
    graph["cell_types"] = cell_type_histogram(graph)
    return graph
