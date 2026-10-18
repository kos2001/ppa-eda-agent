#!/usr/bin/env python3
"""A readable sheet out of an unreadable netlist: the logic around one
signal, N levels deep.

Why this exists. gate_schematic.py draws a whole design, and that stops
being useful exactly where the interesting designs start — aes is 11,616
cells and 39 MB of SVG, riscv32i 5,423 and 14.8 MB. Neither is a drawing
anyone can read, and refusing to draw them (which is what the cap did)
leaves the two designs this repo works hardest on with no schematic view
at all.

Commercial consoles answer this the same way and have for decades: you
do not open the netlist, you open a *cone* — pick a net or an instance,
ask for the logic that drives it or that it drives, and get one sheet.
Innovus and Virtuoso both call this a schematic-from-netlist with a
fanin/fanout depth; this is that, feeding the same PDK importer
gate_schematic.py already uses.

Direction matters and is not guessed. Yosys writes a JSON netlist beside
the Verilog one, carrying a blackbox for every library cell with its port
directions — netlist_graph.py already reads exactly that, for the same
reason recorded there: X/Y/Q being outputs on sky130 is a convention, not
a rule, and a wrong guess silently reverses an edge. When the JSON is
missing the cone still works, undirected, and says so rather than
pretending the arrows mean something.

Usage:
    netlist_cone.py --design aes --seed text_out --depth 2
    netlist_cone.py --design riscv32i --seed clk --direction fanout
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import deque
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# One instance as Yosys writes it:
#   sky130_fd_sc_hd__a21oi_2 _10_ (
#     .A1(en),
#     .Y(_04_)
#   );
# Escaped identifiers (\u0.r0.rcnt[3] ) are real in these netlists and
# carry a trailing space that is part of the name in Verilog but not
# part of the net, so it is stripped on read.
_INSTANCE = re.compile(
    r"^\s*(\w+)\s+(\\?\S+?)\s*\(\s*\n((?:\s*\.\w+\([^)]*\),?\s*\n)+)\s*\);",
    re.MULTILINE)
_CONNECTION = re.compile(r"\.(\w+)\(([^)]*)\)")


def parse_instances(verilog: str) -> list[dict]:
    """Every cell instance, with the net on each pin."""
    out = []
    for match in _INSTANCE.finditer(verilog):
        conns = {pin: net.strip() for pin, net in
                 _CONNECTION.findall(match.group(3))}
        out.append({"type": match.group(1), "name": match.group(2).strip(),
                    "conns": conns})
    return out


def parse_module(verilog: str) -> dict:
    """The module header: name and port directions."""
    header = re.search(r"^module\s+(\S+?)\s*\(([^;]*)\);", verilog, re.MULTILINE)
    if not header:
        raise ValueError("no module declaration found")
    ports = {}
    for kind, width, names in re.findall(
            r"^\s*(input|output|inout)\s*(\[[^\]]*\])?\s*([^;]+);", verilog,
            re.MULTILINE):
        for name in names.split(","):
            ports[name.strip()] = {"dir": kind, "width": width.strip() or None}
    return {"name": header.group(1), "ports": ports}


def port_directions(netlist_json: Path | None) -> dict:
    """cell type -> {pin: input|output}, from Yosys' own library blackboxes."""
    if netlist_json is None or not Path(netlist_json).is_file():
        return {}
    data = json.loads(Path(netlist_json).read_text(encoding="utf-8"))
    out = {}
    for name, module in data.get("modules", {}).items():
        directions = {pin: spec.get("direction")
                      for pin, spec in (module.get("ports") or {}).items()}
        if directions:
            out[name] = directions
    return out


def _bit_nets(net: str) -> set:
    """Nets a connection names. `{a, b}` concatenations appear in these
    netlists; a bus reference is kept whole because that is how the rest
    of the netlist refers to it."""
    net = net.strip()
    if net.startswith("{") and net.endswith("}"):
        return {p.strip() for p in net[1:-1].split(",") if p.strip()}
    return {net} if net else set()


def cone(instances: list[dict], seeds: set, depth: int, direction: str,
         directions: dict) -> dict:
    """Instances within `depth` levels of any seed net.

    fanin walks from a net to the cells that DRIVE it, fanout to the
    cells that READ it, and `both` alternates — which is what "show me
    around this" means when you do not yet know which side the problem is
    on. Without port directions every pin counts as both, and the result
    says `directed: false` so a reader knows the arrows were not checked.
    """
    drivers: dict = {}
    readers: dict = {}
    for inst in instances:
        pins = directions.get(inst["type"], {})
        for pin, net in inst["conns"].items():
            kind = pins.get(pin)
            for bit in _bit_nets(net):
                if kind == "output":
                    drivers.setdefault(bit, []).append(inst)
                elif kind == "input":
                    readers.setdefault(bit, []).append(inst)
                else:
                    drivers.setdefault(bit, []).append(inst)
                    readers.setdefault(bit, []).append(inst)

    kept: dict = {}
    frontier = deque((net, 0) for net in seeds)
    visited_nets = set(seeds)
    while frontier:
        net, level = frontier.popleft()
        if level >= depth:
            continue
        neighbours = []
        if direction in ("fanin", "both"):
            neighbours += drivers.get(net, [])
        if direction in ("fanout", "both"):
            neighbours += readers.get(net, [])
        for inst in neighbours:
            if inst["name"] in kept:
                continue
            kept[inst["name"]] = inst
            for pin_net in inst["conns"].values():
                for bit in _bit_nets(pin_net):
                    if bit not in visited_nets:
                        visited_nets.add(bit)
                        frontier.append((bit, level + 1))
    return {"instances": list(kept.values()), "nets": visited_nets,
            "directed": bool(directions)}


def emit_verilog(module: str, instances: list[dict], boundary: dict) -> str:
    """A Verilog netlist of just these cells, ready for the PDK importer.

    Boundary nets become ports so the drawing shows where the cone was
    cut, rather than ending in wires that go nowhere with no explanation.
    """
    ports = sorted(boundary)
    lines = [f"module {module} ({', '.join(ports)});"]
    for net in ports:
        lines.append(f"  {boundary[net]} {net};")
    internal = sorted({bit for inst in instances
                       for net in inst["conns"].values()
                       for bit in _bit_nets(net)} - set(ports))
    for net in internal:
        lines.append(f"  wire {net};")
    for inst in instances:
        lines.append(f"  {inst['type']} {inst['name']} (")
        conns = [f"    .{pin}({net})" for pin, net in inst["conns"].items()]
        lines.append(",\n".join(conns))
        lines.append("  );")
    lines.append("endmodule")
    return "\n".join(lines) + "\n"


def boundary_nets(instances: list[dict], all_instances: list[dict],
                  module_ports: dict, directions: dict) -> dict:
    """Nets where the cone was cut, and which way they point.

    A net counts as boundary when something outside the cone also touches
    it, or when it is a port of the whole design. Its direction is taken
    from whether the cone drives it: a net the cone drives leaves as an
    output, anything else comes in.
    """
    kept_names = {i["name"] for i in instances}
    outside_nets: set = set()
    for inst in all_instances:
        if inst["name"] in kept_names:
            continue
        for net in inst["conns"].values():
            outside_nets |= _bit_nets(net)

    result = {}
    for inst in instances:
        pins = directions.get(inst["type"], {})
        for pin, net in inst["conns"].items():
            for bit in _bit_nets(net):
                if bit not in outside_nets and bit not in module_ports:
                    continue
                driven_here = pins.get(pin) == "output"
                # A net both driven inside and read outside is an output;
                # first writer wins otherwise, which for a cut net means
                # it comes in.
                if driven_here or result.get(bit) == "output":
                    result[bit] = "output"
                else:
                    result.setdefault(bit, "input")
    return result


def extract(design: str, seed: str, depth: int = 2, direction: str = "both",
            run_dir: Path | None = None, max_cells: int = 400) -> dict:
    """Seed net -> a small Verilog netlist of the logic around it."""
    import gate_schematic  # local: gate_schematic imports nothing from here

    netlist = gate_schematic.find_netlist(design, run_dir)
    if netlist is None:
        return {"ok": False, "error":
                f"no drawable synthesis netlist under designs/{design}/runs/"}
    source = netlist.read_text(encoding="utf-8")
    module = parse_module(source)
    instances = parse_instances(source)
    if not instances:
        return {"ok": False, "error": f"parsed no instances from {netlist}"}

    json_path = next(iter(sorted(netlist.parent.glob("*.nl.v.json"))), None)
    directions = port_directions(json_path)

    seeds = {seed}
    # A bus seed (`text_out`) is written bit by bit in the netlist, so
    # asking for the bus alone would find nothing. Expand it to the bits
    # that actually appear.
    bits = {bit for inst in instances for net in inst["conns"].values()
            for bit in _bit_nets(net)
            if bit == seed or bit.startswith(f"{seed}[")}
    if bits:
        seeds = bits
    elif seed not in {i["name"] for i in instances}:
        return {"ok": False, "error": f"no net or instance named {seed!r} in "
                                       f"{netlist.name}",
                "hint": sorted(module["ports"])[:20]}
    else:
        # An instance seed: start from every net it touches.
        inst = next(i for i in instances if i["name"] == seed)
        seeds = {bit for net in inst["conns"].values() for bit in _bit_nets(net)}

    found = cone(instances, seeds, depth, direction, directions)
    truncated = len(found["instances"]) > max_cells
    kept = found["instances"][:max_cells]
    boundary = boundary_nets(kept, instances, module["ports"], directions)
    name = f"{module['name']}__{re.sub(r'[^A-Za-z0-9_]', '_', seed)}_d{depth}"
    return {
        "ok": True,
        "design": design,
        "module": name,
        "seed": seed,
        "depth": depth,
        "direction": direction,
        "directed": found["directed"],
        "cells": len(kept),
        "cells_found": len(found["instances"]),
        # Recorded rather than silent: a partial schematic that looks
        # complete is worse than one that says it is not (the same rule
        # netlist_graph.py states for its own cap).
        "truncated": truncated,
        "of_total": len(instances),
        "verilog": emit_verilog(name, kept, boundary),
        "netlist": str(netlist.relative_to(REPO_ROOT)),
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", required=True)
    ap.add_argument("--seed", required=True, help="a net or an instance name")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--direction", choices=("fanin", "fanout", "both"),
                    default="both")
    ap.add_argument("--run-dir", type=Path, default=None)
    ap.add_argument("--max-cells", type=int, default=400)
    ap.add_argument("--out", type=Path, default=None,
                    help="write the extracted Verilog here")
    args = ap.parse_args()

    out = extract(args.design, args.seed, args.depth, args.direction,
                  args.run_dir, args.max_cells)
    if not out["ok"]:
        print(out["error"], file=sys.stderr)
        if out.get("hint"):
            print(f"ports: {', '.join(out['hint'])}", file=sys.stderr)
        sys.exit(1)
    print(f"{out['design']} {out['seed']} depth={out['depth']} "
          f"{out['direction']}: {out['cells']} of {out['of_total']} cells"
          + ("  (truncated)" if out["truncated"] else "")
          + ("" if out["directed"] else "  (undirected: no Yosys JSON)"))
    if args.out:
        args.out.write_text(out["verilog"], encoding="utf-8")
        print(f"  {args.out}")


if __name__ == "__main__":
    main()
