#!/usr/bin/env python3
"""Selected OpenLane development fixes on the stable Classic toolchain.

Backports the FP_PIN_ORDER_CFG early return from OpenLane's OpenROAD
enhancements (dffc4423325a238e8dd7e478d6be368d9fe1ab5f). Final global
placement, routing, and all signoff checkers still execute.
"""
import argparse
import json
from pathlib import Path


def placement_step(base, variable):
    class PlacementWithFixedPins(base):
        config_vars = base.config_vars + [
            v for v in [variable] if v.name not in {x.name for x in base.config_vars}
        ]

        def run(self, state_in, **kwargs):
            if self.config["FP_PIN_ORDER_CFG"] is not None:
                (Path(self.step_dir) / "upstream_backport.json").write_text(
                    json.dumps({
                        "upstream_commit": "dffc4423325a238e8dd7e478d6be368d9fe1ab5f",
                        "preliminary_placement_skipped": True,
                        "reason": "FP_PIN_ORDER_CFG supplies the pin order",
                    }, indent=2) + "\n"
                )
                return {}, {}
            return super().run(state_in, **kwargs)

    return PlacementWithFixedPins


def flow_class():
    from openlane.flows import Flow
    from openlane.steps import OpenROAD, Odb

    pin_order = next(v for v in Odb.CustomIOPlacement.config_vars
                     if v.name == "FP_PIN_ORDER_CFG")
    corrected = placement_step(OpenROAD.GlobalPlacementSkipIO, pin_order)
    classic = Flow.factory.get("Classic")

    class UpstreamClassic(classic):
        Steps = [corrected if s.id == "OpenROAD.GlobalPlacementSkipIO" else s
                 for s in classic.Steps]

    return UpstreamClassic


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
