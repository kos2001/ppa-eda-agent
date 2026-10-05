#!/usr/bin/env python3
"""Opt-in post-antenna fanout repair with all normal signoff steps retained."""
import argparse
from pathlib import Path


def flow_class(concrete_magic=False):
    from openlane.config import Variable
    from openlane.flows import Flow
    from openlane.steps import OpenROAD
    from openlane.steps.odb import OdbpyStep

    class FanoutBuffers(OdbpyStep):
        id = "Odb.FanoutBuffers"
        name = "Post-antenna fanout buffers"
        config_vars = [
            Variable("FANOUT_REPAIR_LIMIT", int, "Physical sink-count target, at least 2. Final SDC limits are unchanged."),
            Variable("FANOUT_REPAIR_SIGNAL_CELL", str, "Known non-inverting signal buffer master."),
            Variable("FANOUT_REPAIR_CLOCK_CELL", str, "Known non-inverting clock buffer master."),
            Variable("FANOUT_REPAIR_NET_LIMITS", str, "JSON object with tighter physical sink targets for measured capacitance violators. Does not alter SDC.", default="{}"),
        ]

        def get_script_path(self):
            return str(Path(__file__).with_name("fanout_buffer.py"))

        def get_command(self):
            return super().get_command() + [
                "--limit", str(self.config["FANOUT_REPAIR_LIMIT"]),
                "--signal-cell", self.config["FANOUT_REPAIR_SIGNAL_CELL"],
                "--clock-cell", self.config["FANOUT_REPAIR_CLOCK_CELL"],
                "--report", str(Path(self.step_dir) / "fanout_repair.json"),
                "--net-limits", self.config["FANOUT_REPAIR_NET_LIMITS"],
            ]

    base = Flow.factory.get("Classic")
    if concrete_magic:
        from macro_signoff import flow_class as macro_flow
        base = macro_flow()
    steps = []
    for step in base.Steps:
        steps.append(step)
        if step.id == "OpenROAD.RepairAntennas":
            # Diodes may raise a repaired data net above its fanout limit.
            # Resizer repair_design excludes clock nets in the pinned build.
            # Split both kinds explicitly, then legalize and reroute before
            # checking antenna again and proceeding to the normal final STA.
            steps.extend([FanoutBuffers, OpenROAD.DetailedPlacement,
                          OpenROAD.GlobalRouting, OpenROAD.RepairAntennas])

    class FanoutRepair(base):
        Steps = steps

    return FanoutRepair


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config")
    ap.add_argument("--concrete-magic", action="store_true")
    ap.add_argument("--pdk-root", required=True)
    ap.add_argument("--pdk", default="sky130A")
    ap.add_argument("--scl", default=None)
    ap.add_argument("--run-tag", required=True)
    ap.add_argument("--to", default=None)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--override-config", action="append", default=[])
    args = ap.parse_args()
    flow = flow_class(args.concrete_magic)(
        args.config, pdk_root=args.pdk_root, pdk=args.pdk, scl=args.scl,
        config_override_strings=args.override_config)
    flow.start(tag=args.run_tag, overwrite=args.overwrite, to=args.to)


if __name__ == "__main__":
    main()
