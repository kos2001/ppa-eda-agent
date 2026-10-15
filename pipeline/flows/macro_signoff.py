#!/usr/bin/env python3
"""Classic with concrete standard-cell geometry in Magic's DEF/LEF DRC.

Magic checks routing and standard-cell geometry plus macro LEF boundaries.
The unchanged KLayout DRC step checks the full GDS including macro internals.
No checker or rule is removed; the normal Magic DRC metric comes from its
actual report. Measured on sram_wrapper: 382 false untapped-row reports
with cell abstracts, zero after reading the corresponding cell GDS.
"""
import argparse
import hashlib
import json
from pathlib import Path


def concrete_cell_script(script):
    anchor = "    read_pdk_lef\n"
    if script.count(anchor) != 1:
        raise ValueError("OpenLane Magic script changed; review geometry loading order")
    return script.replace(anchor, anchor + "    read_pdk_gds\n")


# The signoff STA's max-slew table lists only pins that exceed a limit, and the
# limit a macro pin carries is whatever its liberty happened to say: addr0,
# addr1 and wmask0 have one, din0, web0 and the clocks have none. So "no
# violation reported" said nothing about those pins. This asks OpenSTA for the
# slew at EVERY input pin of every macro instance, at every corner, which is the
# only evidence that can say the whole interface sits inside the range the
# macro's liberty was characterised over.
MACRO_SLEW_REPORT = "macro_pin_slews.rpt"


def macro_pin_slew_script(script, instances):
    anchor = "sta::set_cmd_corner $corner" + chr(10)
    if script.count(anchor) != 1:
        raise ValueError("OpenLane corner.tcl changed; review where the corner is set")
    if not instances:
        return script
    names = " ".join(sorted(instances))
    block = f"""
puts "%OL_CREATE_REPORT {MACRO_SLEW_REPORT}"
puts "corner [$corner name]"
foreach macro_instance {{{names}}} {{
    foreach macro_pin [get_pins $macro_instance/*] {{
        if {{ [get_property $macro_pin direction] == "input" }} {{
            puts "MACRO_PIN [get_property $macro_pin full_name] [get_property $macro_pin slew_max]"
        }}
    }}
}}
puts "%OL_END_REPORT"
"""
    return script.replace(anchor, anchor + block)


def flow_class():
    from openlane.flows import Flow
    from openlane.steps import Magic

    class ConcreteCellDRC(Magic.DRC):
        # Preserve the step id so Classic's flow controls and reports apply.
        long_name = "Design Rule Checks (standard-cell GDS and macro LEF)"

        def get_script_path(self):
            original = Path(super().get_script_path())
            script = concrete_cell_script(original.read_text())
            target = Path(self.step_dir) / "drc_concrete_cells.tcl"
            target.write_text(script)
            return str(target)

        def run(self, state_in, **kwargs):
            views, metrics = super().run(state_in, **kwargs)
            geometry = "full GDS" if self.config["MAGIC_DRC_USE_GDS"] else "standard-cell GDS, routing DEF, macro LEF"
            script = Path(self.step_dir) / "drc_concrete_cells.tcl"
            report = Path(self.step_dir) / "reports/drc_violations.magic.rpt"
            scope = {
                "magic_geometry": geometry,
                "macro_internal_drc": "KLayout.DRC on full GDS (separate required step)",
                "script_sha256": hashlib.sha256(script.read_bytes()).hexdigest(),
                "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
            }
            (Path(self.step_dir) / "geometry_scope.json").write_text(json.dumps(scope, indent=2) + "\n")
            return views, metrics

    from openlane.steps import OpenROAD

    class MacroPinSlewSTA(OpenROAD.STAPostPNR):
        """Post-PnR STA that also reports the slew at every macro input pin."""

        def get_script_path(self):
            original = Path(super().get_script_path()).read_text()
            instances = [inst for macro in (self.config.get("MACROS") or {}).values()
                         for inst in (macro.instances or {})]
            target = Path(self.step_dir) / "corner_macro_slews.tcl"
            if not target.exists():       # one file shared by the per-corner processes
                target.write_text(macro_pin_slew_script(original, instances))
            return str(target)

    classic = Flow.factory.get("Classic")

    def swap(step):
        if step.id == "Magic.DRC":
            return ConcreteCellDRC
        if step.id == "OpenROAD.STAPostPNR":
            return MacroPinSlewSTA
        return step

    class MacroSignoff(classic):
        Steps = [swap(step) for step in classic.Steps]

    return MacroSignoff


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("config")
    ap.add_argument("--pdk-root", required=True)
    ap.add_argument("--pdk", default="sky130A")
    ap.add_argument("--scl", default=None)
    ap.add_argument("--run-tag", required=True)
    ap.add_argument("--to", default=None)
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--override-config", action="append", default=[])
    args = ap.parse_args()
    flow = flow_class()(args.config, pdk_root=args.pdk_root,
                        pdk=args.pdk, scl=args.scl,
                        config_override_strings=args.override_config)
    flow.start(tag=args.run_tag, overwrite=args.overwrite, to=args.to)


if __name__ == "__main__":
    main()
