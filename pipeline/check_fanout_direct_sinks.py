"""OpenROAD Python connectivity probe on an in-memory copy of a measured ODB.

Usage: openroad -exit -python check_fanout_direct_sinks.py INPUT.odb SPEC.json OUT.json
Never writes the input database. This is not placement/routing/STA/LVS signoff.
"""
import hashlib
import json
from pathlib import Path
import sys

import odb
from flows.fanout_buffer import repair_fanout


def main():
    source, spec_path, out = map(Path, sys.argv[1:])
    spec = json.loads(spec_path.read_text())
    db = odb.dbDatabase.create()
    odb.read_db(db, str(source))
    block = db.getChip().getBlock()
    originals = {}
    for inst in block.getInsts():
        for term in inst.getITerms():
            originals[(inst.getName(), term.getMTerm().getName())] = (
                term.getNet().getName() if term.getNet() else None)
    ports = {t.getName(): t.getNet().getName() if t.getNet() else None for t in block.getBTerms()}
    overrides = spec["overrides"]
    direct = json.loads(overrides["FANOUT_REPAIR_DIRECT_SINKS"])
    kwargs = dict(limit=overrides["FANOUT_REPAIR_LIMIT"],
                  signal_cell=overrides["FANOUT_REPAIR_SIGNAL_CELL"],
                  clock_cell=overrides["FANOUT_REPAIR_CLOCK_CELL"],
                  net_limits=json.loads(overrides["FANOUT_REPAIR_NET_LIMITS"]),
                  direct_sinks=direct)
    report = repair_fanout(db, **kwargs)
    branches = {}
    for record in report["repaired_nets"]:
        for buf in record["buffers"]:
            inst = block.findInst(buf["instance"])
            inputs = [t for t in inst.getITerms() if str(t.getIoType()) == "INPUT"
                      and str(t.getSigType()) not in ("POWER", "GROUND")]
            if len(inputs) != 1:
                raise ValueError("buffer input interface changed")
            branches[buf["net"]] = inputs[0].getNet().getName()
    if not branches:
        raise ValueError("probe inserted no branches")
    for (inst, pin), expected in originals.items():
        term = block.findInst(inst).findITerm(pin)
        actual = term.getNet().getName() if term.getNet() else None
        visited = set()
        while actual in branches:
            if actual in visited:
                raise ValueError("cycle in inserted buffer graph")
            visited.add(actual)
            actual = branches[actual]
        if actual != expected:
            raise ValueError(f"original connectivity changed: {inst}/{pin}")
    for net, pins in direct.items():
        for pin in pins:
            inst, port = pin.rsplit("/", 1)
            if block.findInst(inst).findITerm(port).getNet().getName() != net:
                raise ValueError(f"direct sink moved: {pin}")
    if ports != {t.getName(): t.getNet().getName() if t.getNet() else None for t in block.getBTerms()}:
        raise ValueError("top-level connectivity changed")
    repeated = repair_fanout(db, **kwargs)
    if repeated["inserted_buffers"]:
        raise ValueError("unexpected repeated buffering without intervening antenna repair")
    result = {"scope": "OpenDB connectivity only; no physical/timing qualification",
              "source_odb_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "spec_sha256": hashlib.sha256(spec_path.read_bytes()).hexdigest(),
              "implementation_sha256": hashlib.sha256(
                  Path(__file__).with_name("flows").joinpath("fanout_buffer.py").read_bytes()).hexdigest(),
              "verified_original_pins": len(originals), "verified_ports": len(ports),
              "verified_direct_sinks": sum(map(len, direct.values())),
              "repeat_inserted_buffers": repeated["inserted_buffers"], "repair": report}
    with out.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2)
        stream.write("\n")
    print(f"Verified {len(originals)} original pins, {len(branches)} new branches")


if __name__ == "__main__":
    main()
