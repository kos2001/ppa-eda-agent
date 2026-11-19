"""Measured, opt-in upsizing of known SKY130 HD combinational drivers."""
from __future__ import annotations

import json
import re
from pathlib import Path


def compatible_family(old: str, new: str) -> bool:
    pattern = r"sky130_fd_sc_hd__(buf|clkbuf|o2bb2ai|a211oi)_(\d+)"
    a, b = re.fullmatch(pattern, old), re.fullmatch(pattern, new)
    return bool(a and b and a[1] == b[1] and int(b[2]) > int(a[2]))


def size_drivers(db, requests):
    if not isinstance(requests, dict) or not requests:
        raise ValueError("driver sizing requires a nonempty instance-to-master object")
    block = db.getChip().getBlock()
    prepared = []

    def interface(master):
        return sorted((p.getName(), str(p.getIoType()), str(p.getSigType()))
                      for p in master.getMTerms())

    def connections(inst):
        return {t.getMTerm().getName(): (t.getNet().getName() if t.getNet() else None)
                for t in inst.getITerms()}

    for name, cell in sorted(requests.items()):
        if not isinstance(name, str) or not isinstance(cell, str):
            raise ValueError("driver sizing names must be strings")
        inst, master = block.findInst(name), db.findMaster(cell)
        if inst is None or master is None:
            raise ValueError(f"driver or master absent: {name} -> {cell}")
        old = inst.getMaster()
        if not compatible_family(old.getName(), cell):
            raise ValueError(f"unsupported family change or non-upsize: {old.getName()} -> {cell}")
        if inst.isDoNotTouch() or interface(old) != interface(master):
            raise ValueError(f"protected driver or incompatible pin interface: {name}")
        prepared.append((inst, master, old.getName(), connections(inst)))
    records = []
    for inst, master, old, nets in prepared:
        if not inst.swapMaster(master):
            raise ValueError(f"OpenDB refused driver upsize: {inst.getName()}")
        if connections(inst) != nets:
            raise ValueError(f"driver upsize changed connectivity: {inst.getName()}")
        records.append({"instance": inst.getName(), "old_master": old,
                        "new_master": master.getName(), "pin_connections_preserved": True})
    return {"drivers": records,
            "scope": "known same-function cell families; placement, extracted STA and signoff remain required"}


def main():
    import click
    import odb
    from openroad import Tech, Design

    @click.command()
    @click.option("--cells", required=True)
    @click.option("--report", type=click.Path(), required=True)
    @click.option("--input-lef", multiple=True, type=click.Path(exists=True))
    @click.option("--step-config", type=click.Path(exists=True))
    @click.option("--output-odb", type=click.Path(), required=True)
    @click.option("--output-def", type=click.Path())
    @click.argument("input_db", type=click.Path(exists=True))
    def run(cells, report, input_lef, step_config, output_odb, output_def, input_db):
        # The pinned OdbReader attaches STA callbacks without loading Liberty.
        # swapMaster then dereferences an absent LibertyCell. Work on an
        # independent DB; the next normal OpenROAD step reloads the result.
        context = Design(Tech())
        db = odb.dbDatabase.create()
        db.setLogger(context.getLogger())
        odb.read_db(db, input_db)
        result = size_drivers(db, json.loads(cells))
        odb.write_db(db, output_odb)
        if output_def:
            odb.write_def(db.getChip().getBlock(), output_def)
        Path(report).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result))

    run()


if __name__ == "__main__":
    main()
