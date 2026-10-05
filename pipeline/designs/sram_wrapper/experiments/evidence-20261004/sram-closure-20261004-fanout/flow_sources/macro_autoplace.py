#!/usr/bin/env python3
"""Classic, with OpenROAD.BasicMacroPlacement inserted.

sram_wrapper pins its SRAM by hand at (110, 150) um. The measured
driver-to-pin distance on addr1[0] is 249 um, against the 144.5 um that
lib_query says the strongest available repair buffer can drive within
the macro's own 40 ps limit — so the hand placement is the case's open
problem, and letting the tool choose is the obvious experiment.

It is not a config flag. OpenROAD.BasicMacroPlacement exists in the
image but is NOT one of Classic's 78 steps, and `openlane -f` only
accepts built-in flow names. So this file defines the flow through
OpenLane's Python API instead: Classic's exact step list with the
macro-placement step inserted before global placement, and nothing else
changed.

Deliberately built by *inserting into* Classic's own Steps rather than
by listing steps by hand. Every calibration in this project — the 78-step
totals the live view shows, classify_stage's error-to-stage mapping,
SCREEN_STEP — is against Classic, and a hand-copied list would drift from
it silently on the next OpenLane bump.

Driven through the Flow API rather than OpenLane's CLI: the CLI builds
its `--flow` choice list at import time from the registry, so a flow
registered afterwards is rejected as "not one of ...". Confirmed by
trying it.

Run inside the container:
    python3 /flows/macro_autoplace.py /design/config.json \
        --pdk-root /pdk --run-tag <tag> [--to <step-id>]
"""
import argparse
import sys

from openlane.flows import Flow, SequentialFlow

_ANCHOR = "OpenROAD.GlobalPlacement"
_INSERT = "OpenROAD.BasicMacroPlacement"


def build_steps():
    """Classic's steps with the macro placer added before placement."""
    classic = Flow.factory.get("Classic")
    steps = list(classic.Steps)
    ids = [s.id for s in steps]
    if _INSERT in ids:
        return steps
    if _ANCHOR not in ids:
        raise SystemExit(f"{_ANCHOR} not in Classic — flow layout changed")
    from openlane.steps import Step
    # Before global placement: macros have to be positioned before the
    # standard cells are spread around them, which is why the anchor is
    # GlobalPlacement and not something later.
    steps.insert(ids.index(_ANCHOR), Step.factory.get(_INSERT))
    return steps


class MacroAutoPlace(SequentialFlow):
    Steps = build_steps()


def main():
    ap = argparse.ArgumentParser(description="Classic + BasicMacroPlacement")
    ap.add_argument("config")
    ap.add_argument("--pdk-root", required=True)
    # The design configs in this repo do not carry a PDK key — the CLI
    # supplies its default. Doing the same here keeps this flow's runs
    # comparable with every Classic run the pipeline makes.
    ap.add_argument("--pdk", default="sky130A")
    ap.add_argument("--scl", default="sky130_fd_sc_hd")
    ap.add_argument("--run-tag", required=True)
    ap.add_argument("--to", default=None, help="stop after this step id")
    ap.add_argument("--overwrite", action="store_true")
    args = ap.parse_args()

    flow = MacroAutoPlace(args.config, pdk_root=args.pdk_root,
                          pdk=args.pdk, scl=args.scl)
    kwargs = {}
    if args.to:
        kwargs["frm"] = None
        kwargs["to"] = args.to
    flow.start(tag=args.run_tag, overwrite=args.overwrite, **kwargs)


if __name__ == "__main__":
    main()
