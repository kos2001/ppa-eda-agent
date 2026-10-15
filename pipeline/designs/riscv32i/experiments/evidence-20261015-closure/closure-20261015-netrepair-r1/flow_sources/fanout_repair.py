#!/usr/bin/env python3
"""Opt-in post-antenna fanout repair with all normal signoff steps retained."""
import argparse
import json
from pathlib import Path


def repair_rounds(overrides):
    rounds = 1
    for override in overrides:
        key, _, value = override.partition("=")
        if key == "FANOUT_REPAIR_ROUNDS":
            rounds = int(value)
    if not 1 <= rounds <= 3:
        raise ValueError("FANOUT_REPAIR_ROUNDS must be between 1 and 3")
    return rounds


def macro_buffer_requested(overrides):
    pins = []
    for override in overrides:
        key, _, value = override.partition("=")
        if key == "MACRO_BUFFER_PINS":
            pins = json.loads(value)
    if not isinstance(pins, list) or any(not isinstance(pin, str) for pin in pins):
        raise ValueError("MACRO_BUFFER_PINS must be a JSON list of pin names")
    return bool(pins)


def driver_sizing_requested(overrides):
    cells = {}
    for override in overrides:
        key, _, value = override.partition("=")
        if key == "FANOUT_REPAIR_DRIVER_CELLS":
            cells = json.loads(value)
    if not isinstance(cells, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                          for k, v in cells.items()):
        raise ValueError("FANOUT_REPAIR_DRIVER_CELLS must map instance names to cell names")
    return bool(cells)


def diode_trim_requested(overrides):
    """(keep, min_diodes) when DIODE_TRIM_KEEP asks for the step, else None.

    DIODE_TRIM_KEEP: diodes kept on every net that carries DIODE_TRIM_MIN_DIODES
    or more (default 10, the checker's cap). Off when absent or 0.
    """
    keep, minimum = 0, 10
    for override in overrides:
        key, _, value = override.partition("=")
        if key == "DIODE_TRIM_KEEP":
            keep = int(value)
        elif key == "DIODE_TRIM_MIN_DIODES":
            minimum = int(value)
    if keep == 0:
        return None
    if keep < 1:
        raise ValueError("DIODE_TRIM_KEEP must be 0 (off) or a positive integer")
    if minimum <= keep:
        raise ValueError("DIODE_TRIM_MIN_DIODES must be greater than DIODE_TRIM_KEEP")
    return keep, minimum


def flow_class(concrete_magic=False, rounds=1, macro_buffers=False, driver_sizing=False,
               diode_trim=False):
    if not 1 <= rounds <= 3:
        raise ValueError("fanout repair is bounded to 1 through 3 rounds")
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
            Variable("FANOUT_REPAIR_ROUNDS", int, "Bounded buffer/legalize/reroute/antenna passes, 1 through 3. Final checks remain unchanged.", default=1),
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

    class MacroPinBuffers(OdbpyStep):
        id = "Odb.MacroPinBuffers"
        name = "Dedicated macro input buffers near pins"
        config_vars = [
            Variable("MACRO_BUFFER_PINS", str, "JSON list of macro signal input pins for dedicated local buffering."),
            Variable("MACRO_BUFFER_CELL", str, "Known SKY130 HD buffer, or second inverter when a first inverter is set."),
            Variable("MACRO_BUFFER_INVERTER_CELL", str, "Optional first inverter: when set, MACRO_BUFFER_CELL must be a known second inverter. Two inversions preserve polarity.", default=""),
            Variable("MACRO_BUFFER_OFFSET_UM", float, "Seed distance outside the macro edge; final placement is legalized.", default=2.0),
        ]

        def get_script_path(self):
            return str(Path(__file__).with_name("macro_pin_buffer.py"))

        def get_command(self):
            return super().get_command() + [
                "--pins", self.config["MACRO_BUFFER_PINS"],
                "--cell", self.config["MACRO_BUFFER_CELL"],
                "--inverter-cell", self.config["MACRO_BUFFER_INVERTER_CELL"],
                "--offset-um", str(self.config["MACRO_BUFFER_OFFSET_UM"]),
                "--report", str(Path(self.step_dir) / "macro_pin_buffers.json"),
            ]

    class DriverSizes(OdbpyStep):
        id = "Odb.DriverSizes"
        name = "Up-size measured combinational drivers"
        config_vars = [Variable("FANOUT_REPAIR_DRIVER_CELLS", str,
                                "JSON instance-to-master mapping for known same-function upsizes.")]

        def get_script_path(self):
            return str(Path(__file__).with_name("driver_size.py"))

        def get_command(self):
            return super().get_command() + [
                "--cells", self.config["FANOUT_REPAIR_DRIVER_CELLS"],
                "--report", str(Path(self.step_dir) / "driver_sizes.json"),
            ]

    class DiodeTrim(OdbpyStep):
        id = "Odb.DiodeTrim"
        name = "Remove excess antenna diodes from capped nets"
        config_vars = [
            Variable("DIODE_TRIM_KEEP", int,
                     "Diodes kept on every net with DIODE_TRIM_MIN_DIODES or more; the rest are removed. 0 disables the step."),
            Variable("DIODE_TRIM_MIN_DIODES", int,
                     "A net is trimmed when it carries at least this many diodes; 10 is the antenna checker's per-gate cap.",
                     default=10),
        ]

        def get_script_path(self):
            return str(Path(__file__).with_name("diode_trim.py"))

        def get_command(self):
            return super().get_command() + [
                "--keep", str(self.config["DIODE_TRIM_KEEP"]),
                "--min-diodes", str(self.config["DIODE_TRIM_MIN_DIODES"]),
                "--report", str(Path(self.step_dir) / "diode_trim.json"),
            ]

    class ProtectMacroPinBuffers(MacroPinBuffers):
        id = "Odb.ProtectMacroPinBuffers"
        name = "Preserve dedicated macro buffer strength"

        def get_command(self):
            return super().get_command() + ["--protect-only"]

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
            if driver_sizing:
                steps.append(DriverSizes)
            if macro_buffers:
                steps.append(MacroPinBuffers)
            for _ in range(rounds):
                steps.extend([FanoutBuffers, OpenROAD.DetailedPlacement,
                              OpenROAD.GlobalRouting, OpenROAD.RepairAntennas])
            if diode_trim:
                # After the last antenna repair: its diodes are final, and
                # detailed routing routes from the guides again.
                steps.append(DiodeTrim)
            if macro_buffers:
                # Fanout repair may reconnect a new buffer's input. OpenDB
                # forbids rewiring a dont_touch instance, so protect strength
                # after that work and before the normal timing resizer.
                steps.append(ProtectMacroPinBuffers)

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
    macro_buffers = macro_buffer_requested(args.override_config)
    if macro_buffers and not args.concrete_magic:
        ap.error("macro input buffering requires --concrete-magic")
    flow = flow_class(args.concrete_magic, repair_rounds(args.override_config), macro_buffers,
                      driver_sizing_requested(args.override_config),
                      diode_trim_requested(args.override_config) is not None)(
        args.config, pdk_root=args.pdk_root, pdk=args.pdk, scl=args.scl,
        config_override_strings=args.override_config)
    flow.start(tag=args.run_tag, overwrite=args.overwrite, to=args.to)


if __name__ == "__main__":
    main()
