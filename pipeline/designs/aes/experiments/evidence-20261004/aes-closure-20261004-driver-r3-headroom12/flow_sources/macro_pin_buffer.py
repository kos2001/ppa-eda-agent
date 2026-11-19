"""Polarity-preserving input buffers placed outside the nearest macro edge.

This geometric seed must be legalized and routed. Final extracted STA, not
distance or a lookup prediction, decides whether it repairs electrical rules.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path

try:
    from .fanout_buffer import buffer_ports
except ImportError:
    from fanout_buffer import buffer_ports


def edge_locations(box, pin, width, height, offset):
    """Origins outside all four edges, nearest pin first, deterministic ties."""
    x0, y0, x1, y1 = box
    px, py = pin
    candidates = [(x0 - offset - width, py - height // 2),
                  (x1 + offset, py - height // 2),
                  (px - width // 2, y0 - offset - height),
                  (px - width // 2, y1 + offset)]
    return sorted(candidates, key=lambda xy: (abs(xy[0] + width / 2 - px)
                                              + abs(xy[1] + height / 2 - py), xy))


def inverter_ports(master):
    if not re.fullmatch(r"sky130_fd_sc_hd__inv_(?:1|2|4|6|8|12|16)", master.getName()):
        raise ValueError("paired inversion requires known SKY130 HD inverter masters")
    inputs = [p.getName() for p in master.getMTerms() if str(p.getIoType()) == "INPUT"
              and str(p.getSigType()) not in ("POWER", "GROUND")]
    outputs = [p.getName() for p in master.getMTerms() if str(p.getIoType()) == "OUTPUT"
               and str(p.getSigType()) not in ("POWER", "GROUND")]
    if inputs != ["A"] or outputs != ["Y"]:
        raise ValueError("unexpected SKY130 inverter signal interface")
    return "A", "Y"


def buffer_macro_pins(db, pins, cell, offset_um, inverter_cell=""):
    import odb

    if not pins or len(pins) != len(set(pins)):
        raise ValueError("macro buffer pin list must be nonempty and unique")
    if not math.isfinite(offset_um) or offset_um < 0:
        raise ValueError("macro buffer offset must be finite and nonnegative")
    master = db.findMaster(cell)
    if master is None:
        raise ValueError(f"missing macro buffer master {cell}")
    pre_master = db.findMaster(inverter_cell) if inverter_cell else None
    if inverter_cell and pre_master is None:
        raise ValueError(f"missing first inverter master {inverter_cell}")
    if pre_master:
        pre_input, pre_output = inverter_ports(pre_master)
        input_pin, output_pin = inverter_ports(master)
    else:
        input_pin, output_pin = buffer_ports(master)
    width = master.getWidth() + (pre_master.getWidth() if pre_master else 0)
    height = max(master.getHeight(), pre_master.getHeight() if pre_master else 0)
    block = db.getChip().getBlock()
    unit = block.getDbUnitsPerMicron()
    offset = round(offset_um * unit)
    die = block.getDieArea()
    prepared = []
    # Validate the whole request before changing connectivity.
    for full_name in sorted(pins):
        instance, pin = full_name.rsplit("/", 1)
        macro = block.findInst(instance)
        if macro is None or str(macro.getMaster().getType()) != "BLOCK":
            raise ValueError(f"macro instance is absent or not BLOCK: {instance}")
        term = macro.findITerm(pin)
        if term is None or str(term.getIoType()) != "INPUT":
            raise ValueError(f"not a macro input: {full_name}")
        net = term.getNet()
        if net is None or net.isSpecial() or str(net.getSigType()) == "CLOCK" or net.isDoNotTouch():
            raise ValueError(f"unsupported unconnected/special/clock/dont_touch net: {full_name}")
        drivers = [t for t in net.getITerms() if str(t.getIoType()) == "OUTPUT"]
        if len(drivers) != 1:
            raise ValueError(f"macro input must have one internal driver: {full_name}")
        if any(str(t.getIoType()) == "INOUT" for t in net.getITerms()):
            raise ValueError(f"bidirectional net: {full_name}")
        ok, px, py = term.getAvgXY()
        if not ok:
            raise ValueError(f"macro input has no physical position: {full_name}")
        bbox = macro.getBBox()
        locations = edge_locations((bbox.xMin(), bbox.yMin(), bbox.xMax(), bbox.yMax()),
                                   (px, py), width, height, offset)
        locations = [(x, y) for x, y in locations if die.xMin() <= x and die.yMin() <= y
                     and x + width <= die.xMax() and y + height <= die.yMax()]
        if not locations:
            raise ValueError(f"no outside-macro location within die for {full_name}")
        power_nets = {}
        for port in master.getMTerms():
            if str(port.getSigType()) not in ("POWER", "GROUND"):
                continue
            original_port = drivers[0].getInst().findITerm(port.getName())
            if original_port is None or original_port.getNet() is None:
                raise ValueError(f"driver has no established power mapping for {port.getName()}: {full_name}")
            power_nets[port.getName()] = original_port.getNet()
        prepared.append((full_name, term, net, (px, py), locations[0], power_nets))
    records = []
    for index in range(1, len(prepared) + 1):
        name = f"macro_pin_buffer_{index}"
        if block.findInst(name) or block.findNet(f"{name}_net"):
            raise ValueError(f"macro buffer name collision: {name}")
        if pre_master:
            name = f"macro_pin_prebuffer_{index}"
            if block.findInst(name) or block.findNet(f"{name}_net"):
                raise ValueError(f"macro prebuffer name collision: {name}")
    for index, (pin, term, net, position, origin, power_nets) in enumerate(prepared, 1):
        name = f"macro_pin_buffer_{index}"
        inst = odb.dbInst.create(block, master, name)
        inst.setLocation(origin[0] + (pre_master.getWidth() if pre_master else 0), origin[1])
        inst.setPlacementStatus("PLACED")
        subnet = odb.dbNet.create(block, f"{name}_net")
        subnet.setSigType(net.getSigType())
        pre_name = None
        if pre_master:
            pre_name = f"macro_pin_prebuffer_{index}"
            first = odb.dbInst.create(block, pre_master, pre_name)
            first.setLocation(*origin)
            first.setPlacementStatus("PLACED")
            middle = odb.dbNet.create(block, f"{pre_name}_net")
            middle.setSigType(net.getSigType())
            first.findITerm(pre_input).connect(net)
            first.findITerm(pre_output).connect(middle)
            inst.findITerm(input_pin).connect(middle)
            for port, power_net in power_nets.items():
                first.findITerm(port).connect(power_net)
        else:
            inst.findITerm(input_pin).connect(net)
        inst.findITerm(output_pin).connect(subnet)
        term.connect(subnet)
        for port, power_net in power_nets.items():
            inst.findITerm(port).connect(power_net)
        records.append({"pin": pin, "original_net": net.getName(), "buffer": name,
                        "buffer_master": cell, "origin_um": [v / unit for v in origin],
                        "macro_pin_xy_um": [v / unit for v in position],
                        "dedicated_net": subnet.getName(), "sizing_preserved": False,
                        "prebuffer": pre_name, "prebuffer_master": inverter_cell or None,
                        "inversions": 2 if pre_master else 0})
    return {"inserted_buffers": len(records) * (2 if pre_master else 1), "pins": records,
            "scope": "nearest-edge geometric seed; legal placement, routing and all-corner STA remain required"}


def main():
    import click
    from reader import click_odb

    @click.command()
    @click.option("--pins", required=True)
    @click.option("--cell", required=True)
    @click.option("--offset-um", type=float, required=True)
    @click.option("--report", type=click.Path(), required=True)
    @click.option("--protect-only", is_flag=True)
    @click.option("--inverter-cell", default="")
    @click_odb
    def run(reader, pins, cell, offset_um, report, protect_only, inverter_cell):
        if protect_only:
            block = reader.db.getChip().getBlock()
            names = [f"macro_pin_buffer_{i}" for i in range(1, len(json.loads(pins)) + 1)]
            cells = [cell] * len(names)
            if inverter_cell:
                names += [f"macro_pin_prebuffer_{i}" for i in range(1, len(json.loads(pins)) + 1)]
                cells += [inverter_cell] * len(json.loads(pins))
            instances = [block.findInst(name) for name in names]
            if any(inst is None or inst.getMaster().getName() != expected
                   for inst, expected in zip(instances, cells)):
                raise ValueError("local macro buffers are missing or changed before sizing protection")
            for inst in instances:
                inst.setDoNotTouch(True)
            result = {"protected_buffers": names, "cell": cell,
                      "scope": "preserve buffer sizing after net repair; final timing checks are unchanged"}
        else:
            result = buffer_macro_pins(reader.db, json.loads(pins), cell, offset_um, inverter_cell)
        Path(report).write_text(json.dumps(result, indent=2) + "\n")
        print(f"Macro input buffer result: {json.dumps(result)}")

    run()


if __name__ == "__main__":
    main()
