"""Opt-in removal of the excess antenna diodes OpenROAD put on capped nets.

OpenROAD's antenna checker adds a diode at a time to a gate until the gate
passes the margin-tightened ratio, gives up above max_diode_count_per_gate (10)
and leaves a count of 11, which RepairAntennas then inserts per gate. On the
aes recipe 40 to 64% of the first-iteration diodes at margins 50 to 75 sit on
nets at that cap, where the real rule needs 1 to 4 (pipeline/designs/aes/
experiments/README.md, 2026-10-10). Those diodes load their nets and bring back
fanout, slew and capacitance violations.

This step keeps the first KEEP diodes of every net that carries at least
MIN_DIODES of them and destroys the rest. It runs after the last
RepairAntennas, before detailed routing, which routes from the guides again.
What the removal costs in final antenna violations is what the experiment
measures; nothing here claims it is free.
"""
import json
import re
from pathlib import Path

DIODE_NAME = re.compile(r"ANTENNA_(\d+)$")


def select_removals(net_to_diodes, min_diodes, keep):
    """net -> instance names to destroy, for nets with min_diodes or more.

    net_to_diodes maps a net name to the names of its antenna diodes. The
    first `keep` by insertion number are kept, so the choice is repeatable.
    Nets below min_diodes are untouched.
    """
    if isinstance(keep, bool) or not isinstance(keep, int) or keep < 1:
        raise ValueError("keep must be an integer of at least 1")
    if isinstance(min_diodes, bool) or not isinstance(min_diodes, int) or min_diodes <= keep:
        raise ValueError("min_diodes must be an integer greater than keep")

    def order(name):
        m = DIODE_NAME.search(name)
        return (0, int(m.group(1)), name) if m else (1, 0, name)

    removals = {}
    for net, names in net_to_diodes.items():
        if len(names) >= min_diodes:
            removals[net] = sorted(names, key=order)[keep:]
    return removals


def trim_diodes(db, min_diodes, keep, destroy):
    """Destroy the excess diodes. `destroy(inst)` is odb.dbInst.destroy."""
    block = db.getChip().getBlock()

    def is_diode(inst):
        return "diode" in inst.getMaster().getName().lower()

    def connections(net):
        return sorted((t.getInst().getName(), t.getMTerm().getName())
                      for t in net.getITerms() if not is_diode(t.getInst()))

    net_to_diodes, by_name = {}, {}
    for inst in block.getInsts():
        if not is_diode(inst):
            continue
        by_name[inst.getName()] = inst
        nets = {t.getNet().getName() for t in inst.getITerms()
                if t.getNet() and str(t.getMTerm().getSigType()) == "SIGNAL"}
        if len(nets) != 1:
            continue  # not a plain one-net load: not ours to touch
        net_to_diodes.setdefault(nets.pop(), []).append(inst.getName())

    removals = select_removals(net_to_diodes, min_diodes, keep)
    before = {net: connections(block.findNet(net)) for net in removals}
    removed = 0
    for net, names in removals.items():
        for name in names:
            inst = by_name[name]
            if inst.isDoNotTouch():
                raise ValueError(f"diode {name} on {net} is dont_touch")
            destroy(inst)
            removed += 1
    for net, expected in before.items():
        if connections(block.findNet(net)) != expected:
            raise ValueError(f"diode removal changed the non-diode connections of {net}")
    total = sum(len(v) for v in net_to_diodes.values())
    return {
        "min_diodes": min_diodes, "keep": keep,
        "nets_trimmed": len(removals), "diodes_removed": removed,
        "diodes_before": total, "diodes_after": total - removed,
        "nets": {net: {"before": len(net_to_diodes[net]),
                       "after": len(net_to_diodes[net]) - len(names)}
                 for net, names in sorted(removals.items())},
        "scope": "non-diode pin connections of every trimmed net are unchanged; "
                 "final antenna and timing are measured by the normal later steps",
    }


def main():
    import click
    import odb
    from openroad import Tech, Design

    @click.command()
    @click.option("--keep", type=int, required=True)
    @click.option("--min-diodes", type=int, required=True)
    @click.option("--report", type=click.Path(), required=True)
    @click.option("--input-lef", multiple=True, type=click.Path(exists=True))
    @click.option("--step-config", type=click.Path(exists=True))
    @click.option("--output-odb", type=click.Path(), required=True)
    @click.option("--output-def", type=click.Path())
    @click.argument("input_db", type=click.Path(exists=True))
    def run(keep, min_diodes, report, input_lef, step_config, output_odb, output_def, input_db):
        # Same independent-DB approach as driver_size.py: the pinned OdbReader
        # attaches STA callbacks without Liberty, and the next OpenROAD step
        # reloads the result.
        context = Design(Tech())
        db = odb.dbDatabase.create()
        db.setLogger(context.getLogger())
        odb.read_db(db, input_db)
        result = trim_diodes(db, min_diodes, keep, odb.dbInst.destroy)
        odb.write_db(db, output_odb)
        if output_def:
            odb.write_def(db.getChip().getBlock(), output_def)
        Path(report).write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k: v for k, v in result.items() if k != "nets"}))

    run()


if __name__ == "__main__":
    main()
