"""Bounded, spatially grouped buffer trees for explicit physical experiments.

OpenDB connectivity only: final placement, routing, STA and LVS are required.
Use known non-inverting standard-cell masters; this is not a timing optimizer.
"""
from __future__ import annotations

import math
import re


def spatial_groups(items, limit, position):
    if limit < 2:
        raise ValueError("fanout repair limit must be at least 2")
    points = [position(item) for item in items]
    axis = max(range(2), key=lambda a: max(p[a] for p in points) - min(p[a] for p in points))
    ordered = [item for _, item in sorted(
        enumerate(items), key=lambda pair: (position(pair[1])[axis], position(pair[1])[1 - axis], pair[0]))]
    count = math.ceil(len(ordered) / limit)
    return [ordered[i * len(ordered) // count:(i + 1) * len(ordered) // count]
            for i in range(count)]


def buffer_ports(master):
    # OpenDB has pin geometry, not Liberty Boolean functions. Restrict this
    # experimental adapter to the known non-inverting SKY130 HD cells rather
    # than accepting an inverter or arbitrary one-input cell as a buffer.
    if not re.fullmatch(r"sky130_fd_sc_hd__(?:clk)?buf_(?:1|2|4|6|8|12|16)", master.getName()):
        raise ValueError("fanout repair requires a known non-inverting SKY130 HD buffer")
    inputs = [p.getName() for p in master.getMTerms()
              if str(p.getIoType()) == "INPUT" and str(p.getSigType()) not in ("POWER", "GROUND")]
    outputs = [p.getName() for p in master.getMTerms()
               if str(p.getIoType()) == "OUTPUT" and str(p.getSigType()) not in ("POWER", "GROUND")]
    if len(inputs) != 1 or len(outputs) != 1:
        raise ValueError(f"buffer master {master.getName()} must have exactly one signal input and output")
    return inputs[0], outputs[0]


def repair_fanout(db, limit, signal_cell, clock_cell, net_limits=None):
    import odb

    if limit < 2:
        raise ValueError("fanout repair limit must be at least 2")
    masters = {name: db.findMaster(name) for name in (signal_cell, clock_cell)}
    if any(master is None for master in masters.values()):
        raise ValueError("fanout buffer master is missing from the loaded LEF")
    ports = {name: buffer_ports(master) for name, master in masters.items()}
    block = db.getChip().getBlock()
    net_limits = net_limits or {}
    for name, target in net_limits.items():
        if not block.findNet(name) or isinstance(target, bool) or not isinstance(target, int) or target < 2:
            raise ValueError(f"invalid or missing targeted net: {name}={target}")
    # A bounded later pass shares the same DB. Preserve existing buffers and
    # choose the next deterministic name instead of colliding with pass one.
    names = [item.getName() for item in block.getInsts()] + [item.getName() for item in block.getNets()]
    serial = max((int(m.group(1)) for name in names
                  if (m := re.fullmatch(r"fanout_repair_(\d+)", name))), default=0)
    initial_serial = serial
    records, skipped = [], []

    def position(term):
        ok, x, y = term.getAvgXY()
        return (x, y) if ok else term.getInst().getLocation()

    # Freeze the original net list. Newly generated subnets are handled by
    # this net's tree, never by a second scan or unbounded repair loop.
    for net in sorted(list(block.getNets()), key=lambda n: n.getName()):
        net_limit = net_limits.get(net.getName(), limit)
        if net.isSpecial():
            continue
        terms = list(net.getITerms())
        sinks = [t for t in terms if str(t.getIoType()) == "INPUT"]
        if len(sinks) <= net_limit:
            continue
        drivers = [t for t in terms if str(t.getIoType()) == "OUTPUT"]
        reason = None
        if net.isDoNotTouch() or any(t.getInst().isDoNotTouch() for t in terms):
            reason = "dont_touch"
        elif len(drivers) != 1 or net.getBTerms() or any(str(t.getIoType()) == "INOUT" for t in terms):
            reason = "port, multiple-driver, or bidirectional net"
        if reason:
            skipped.append({"net": net.getName(), "reason": reason, "sinks": len(sinks)})
            continue
        clock = str(net.getSigType()) == "CLOCK"
        name = clock_cell if clock else signal_cell
        input_pin, output_pin = ports[name]
        record = {"net": net.getName(), "clock": clock,
                  "driver": drivers[0].getInst().getName() + "/" + drivers[0].getMTerm().getName(),
                  "original_sinks": len(sinks), "buffer_master": name, "buffers": []}
        record["limit"] = net_limit
        level = sinks
        while len(level) > net_limit:
            next_level = []
            for group in spatial_groups(level, net_limit, position):
                serial += 1
                prefix = f"fanout_repair_{serial}"
                net_name = f"fanout_repair_net_{serial}"
                if block.findInst(prefix) or block.findNet(net_name):
                    raise ValueError(f"fanout repair name collision: {prefix}")
                inst = odb.dbInst.create(block, masters[name], prefix)
                # OpenSTA resolves SPEF internal nodes ambiguously when a net
                # and an instance share a name. Keep distinct namespaces.
                subnet = odb.dbNet.create(block, net_name)
                subnet.setSigType(net.getSigType())
                points = [position(t) for t in group]
                inst.setLocation(sum(p[0] for p in points) // len(points),
                                 sum(p[1] for p in points) // len(points))
                inst.setPlacementStatus("PLACED")
                inst.findITerm(output_pin).connect(subnet)
                for sink in group:
                    sink.connect(subnet)
                upstream = inst.findITerm(input_pin)
                upstream.connect(net)
                next_level.append(upstream)
                record["buffers"].append({"instance": prefix, "net": net_name,
                                          "sink_count": len(group)})
            level = next_level
        record["remaining_driver_sinks"] = len(level)
        records.append(record)
    return {"limit": limit, "inserted_buffers": serial - initial_serial,
            "repaired_nets": records, "skipped_nets": skipped,
            "scope": "signal input pin counts; weighted Liberty fanout and timing require final STA"}


def main():
    import click
    import json
    from pathlib import Path
    from reader import click_odb

    @click.command()
    @click.option("--limit", type=click.IntRange(min=2), required=True)
    @click.option("--signal-cell", required=True)
    @click.option("--clock-cell", required=True)
    @click.option("--report", type=click.Path(), required=True)
    @click.option("--net-limits", default="{}")
    @click_odb
    def run(reader, limit, signal_cell, clock_cell, report, net_limits):
        result = repair_fanout(reader.db, limit, signal_cell, clock_cell, json.loads(net_limits))
        Path(report).write_text(json.dumps(result, indent=2) + "\n")
        print(f"Inserted {result['inserted_buffers']} fanout buffers in {len(result['repaired_nets'])} nets")

    run()


if __name__ == "__main__":
    main()
