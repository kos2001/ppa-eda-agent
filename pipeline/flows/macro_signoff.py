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

    classic = Flow.factory.get("Classic")

    class MacroSignoff(classic):
        Steps = [ConcreteCellDRC if step.id == "Magic.DRC" else step
                 for step in classic.Steps]

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
