r"""iEDA's feature JSON as a second metrics source for score().

score() gates on OpenLane's metrics.json keys. That makes the verdict
tool-specific in one place only: the key names. The Agentic-EDA survey
(arXiv 2512.23189) lists "no unified evaluation metric" as the field's
first open problem, and the Chinese open-source track — iEDA, iChipAgent
— reports through iEDA's `feature_summary` JSON rather than OpenLane's.
This maps that JSON onto the keys score() already reads, so one gate
judges both, and says which of its checks the second tool never ran.

WHERE THE KEYS COME FROM. Every iEDA key below was read from the C++
that writes the file — src/feature/parser/feature_parser_summary.cpp,
feature_parser_tools.cpp, feature_parser_eval.cpp, feature_parser.cpp
and src/feature/builder/feature_builder.cpp in OSCC-Project/iEDA at
master (last commit 2026-03-11). Units from feature_builder.cpp: areas
are divided by dbu twice (um^2), lengths once (um), usage is a fraction
of core area. The file layouts are:

    feature_summary -path summary.json      root: "Design Information",
                                            "Design Layout", "Design
                                            Statis", "Instances",
                                            "Nets", "Layers", "Pins" ...
    feature_tool    -step route ...         root: {"route": {"DR": [...],
                                            "VR": {...}, ...}}
    feature_tool    -step CTS|optSetup...   root: {<step>: {"clocks_timing":
                                            [...]}}
    (timing eval)                           root: "clocks_timing",
                                            "power_info", keyed by
                                            routing_type (WLM/HPWL/...)

WHAT THIS HAS NOT BEEN RUN AGAINST. No iEDA binary is installed here
and none of this project's designs has been through it, so the fixtures
in tests/test_ieda_metrics.py are shaped from the writer's source, not
captured from a run. That is the same standing as a parser written from
a format spec: the key names are verified, the values are not. A real
iEDA run is what turns this from "reads the documented format" into
"reads what iEDA emits" — recorded under NOT_YET in the module rather
than left implied.

WHAT iEDA'S JSON DOES NOT CARRY, at that commit:

  - buildSummaryDRC() and buildSummarySTA() are commented out bodies —
    the "drc" and "sta" steps write an empty object. The only violation
    counts are the detailed router's own (DR total_violation_num, VR
    within/among-net counts).
  - No LVS, no Magic/KLayout DRC, no antenna count, no IR drop, no
    max-slew/cap/fanout counts, no lint. Of the 23 checks in
    SIGNOFF_METRICS, exactly one has an iEDA equivalent.
  - Timing is per clock, not per PVT corner: one liberty set, WNS/TNS
    per clock name. Mapped to OpenLane's unsuffixed design-level
    `timing__setup__wns` / `timing__hold__wns`, never to a
    `__corner:` key — inventing a corner name would put a fiction on
    the dashboard's per-corner table.

So a verdict built from an iEDA run reads 22 of 23 signoff checks as
"never checked" — and that is the correct verdict, which is the point:
the gate does not soften for a tool that reports less.
"""

from __future__ import annotations

import json
from pathlib import Path

# Signoff checks OpenLane runs that iEDA's feature JSON has no field
# for, with the reason. score() reports each as unverified; this exists
# so the reason travels with the verdict instead of being rediscovered.
NOT_PRODUCED = {
    "magic__drc_error__count": "iEDA has no Magic; its own DRC summary (buildSummaryDRC) is a commented-out body",
    "klayout__drc_error__count": "iEDA has no KLayout signoff step",
    "design__lvs_error__count": "iEDA has no LVS step",
    "design__lvs_device_difference__count": "no LVS",
    "design__lvs_net_difference__count": "no LVS",
    "design__lvs_property_fail__count": "no LVS",
    "design__lvs_unmatched_device__count": "no LVS",
    "design__lvs_unmatched_net__count": "no LVS",
    "design__lvs_unmatched_pin__count": "no LVS",
    "design__instance_unmapped__count": "synthesis is outside iEDA (Yosys feeds it); no unmapped-cell count in the feature JSON",
    "design__xor_difference__count": "no GDS XOR check",
    "magic__illegal_overlap__count": "no Magic",
    "design__disconnected_pin__count": "no pin-connectivity check in the feature JSON",
    "timing__setup_vio__count": "per-clock WNS/TNS only, no endpoint violation count",
    "timing__hold_vio__count": "per-clock WNS/TNS only, no endpoint violation count",
    "route__antenna_violation__count": "no antenna check in the feature JSON",
    "design__power_grid_violation__count": "PDN summary (buildSummaryPdn) is an empty body",
    "design__max_slew_violation__count": "no DRV counts in the feature JSON",
    "design__max_cap_violation__count": "no DRV counts in the feature JSON",
    "design__max_fanout_violation__count": "Pins.max_fanout is the observed maximum, not a violation count",
    "synthesis__check_error__count": "synthesis is outside iEDA",
    "design__lint_error__count": "lint is outside iEDA",
}

NOT_YET = (
    "no real iEDA run has been read through this adapter; key names are "
    "from the writer's source at OSCC-Project/iEDA master (2026-03-11), "
    "values are unverified until a run is captured"
)

# Steps whose tool JSON carries a clocks_timing list, in the order the
# flow runs them. The last one present is the most recent timing.
_TIMING_STEPS = ("fixFanout", "optDrv", "optHold", "optSetup", "CTS")


def load_feature_files(paths: list[Path]) -> dict:
    """Merge iEDA feature JSON files into one dict.

    iEDA writes one file per call — a summary, one per tool step, an
    eval file — each with its own root keys, and they do not collide
    (summary roots are capitalised phrases, tool roots are the step
    name, eval roots are lower-case). Merging at the root is therefore
    lossless. A file that does not parse is skipped and named, not
    silently dropped.
    """
    merged: dict = {}
    for p in paths:
        try:
            data = json.loads(Path(p).read_text())
        except (OSError, ValueError) as e:
            merged.setdefault("_unreadable", []).append(f"{p}: {e}")
            continue
        if isinstance(data, dict):
            merged.update(data)
    return merged


def _num(v):
    return v if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def _clock_timings(feature: dict) -> list[dict]:
    """The most recent per-clock timing list in the merged feature dict.

    Tool-step JSONs each carry `clocks_timing` (CTS's has setup/hold
    WNS/TNS; the optimisation steps carry origin_/opt_ pairs, of which
    opt_ is the post-step state). The timing eval file carries the same
    list keyed by wire model, from which "DR" (detailed route) is the
    most physical and is preferred when present.
    """
    eval_timing = feature.get("clocks_timing")
    if isinstance(eval_timing, dict) and eval_timing:
        for routing_type in ("DR", "EGR", "FLUTE", "HPWL", "WLM"):
            if routing_type in eval_timing:
                return list(eval_timing[routing_type])
        return list(next(iter(eval_timing.values())))
    for step in reversed(_TIMING_STEPS):
        block = feature.get(step)
        if not isinstance(block, dict):
            continue
        rows = block.get("clocks_timing")
        if not rows:
            continue
        out = []
        for r in rows:
            if "opt_setup_wns" in r:
                out.append({
                    "clock_name": r.get("clock_name"),
                    "setup_wns": r.get("opt_setup_wns"),
                    "setup_tns": r.get("opt_setup_tns"),
                    "hold_wns": r.get("opt_hold_wns"),
                    "hold_tns": r.get("opt_hold_tns"),
                })
            elif "opt_wns" in r:
                # TO steps report one analysis mode without naming it;
                # optHold is the hold pass, the other two are setup.
                mode = "hold" if step == "optHold" else "setup"
                out.append({
                    "clock_name": r.get("clock_name"),
                    f"{mode}_wns": r.get("opt_wns"),
                    f"{mode}_tns": r.get("opt_tns"),
                })
            else:
                out.append(r)
        return out
    return []


def _route_violations(feature: dict) -> int | None:
    """Detailed-router violation count after the last DR iteration.

    `route.DR` is a list, one entry per iteration, each with
    `total_violation_num`; the last iteration is the routed state. VR
    (violation repair) runs after DR and reports within-/among-net
    counts; when present its sum is the final figure.
    """
    route = feature.get("route")
    if not isinstance(route, dict):
        return None
    vr = route.get("VR")
    if isinstance(vr, dict):
        within = _num(vr.get("within_net_total_violation_num"))
        among = _num(vr.get("among_net_total_violation_num"))
        if within is not None or among is not None:
            return (within or 0) + (among or 0)
    dr = route.get("DR")
    if isinstance(dr, list) and dr:
        last = dr[-1]
        return _num(last.get("total_violation_num"))
    return None


def to_openlane_metrics(feature: dict) -> dict:
    """Flat metrics dict in OpenLane's key names, from iEDA feature JSON.

    Only keys with a genuine equivalent are emitted; nothing is defaulted
    to 0, because score() reads absence as "never checked" and a
    fabricated zero would read as a clean pass.
    """
    m: dict = {"metrics__source": "iEDA feature JSON"}

    statis = feature.get("Design Statis") or {}
    n = _num(statis.get("num_instances"))
    if n is not None:
        m["design__instance__count"] = n

    insts = feature.get("Instances") or {}
    total = insts.get("total") or {}
    logic = insts.get("logic") or {}
    area = _num(total.get("area"))
    if area is not None:
        m["design__instance__area"] = area
    util = _num(logic.get("core_usage"))
    if util is None:
        util = _num(total.get("core_usage"))
    if util is not None:
        m["design__instance__utilization__stdcell"] = util

    layout = feature.get("Design Layout") or {}
    die = _num(layout.get("die_area"))
    if die is not None:
        m["design__die__area"] = die
    core = _num(layout.get("core_area"))
    if core is not None:
        m["design__core__area"] = core

    nets = feature.get("Nets") or {}
    wl = _num(nets.get("wire_len"))
    if wl is not None:
        m["route__wirelength"] = wl
    vias = _num(nets.get("num_via"))
    if vias is not None:
        m["route__vias"] = vias

    drc = _route_violations(feature)
    if drc is not None:
        m["route__drc_errors"] = drc

    timings = _clock_timings(feature)
    setup = [_num(t.get("setup_wns")) for t in timings]
    setup = [s for s in setup if s is not None]
    if setup:
        m["timing__setup__wns"] = min(setup)
    hold = [_num(t.get("hold_wns")) for t in timings]
    hold = [h for h in hold if h is not None]
    if hold:
        m["timing__hold__wns"] = min(hold)
    stns = [_num(t.get("setup_tns")) for t in timings]
    stns = [s for s in stns if s is not None]
    if stns:
        m["timing__setup__tns"] = sum(stns)
    htns = [_num(t.get("hold_tns")) for t in timings]
    htns = [h for h in htns if h is not None]
    if htns:
        m["timing__hold__tns"] = sum(htns)

    power = feature.get("power_info")
    if isinstance(power, dict) and power:
        block = power.get("DR") or next(iter(power.values()))
        if isinstance(block, dict):
            static = _num(block.get("static_power"))
            dynamic = _num(block.get("dynamic_power"))
            if static is not None:
                m["power__leakage__total"] = static
            if static is not None and dynamic is not None:
                m["power__total"] = static + dynamic

    return m


def coverage(metrics: dict, signoff_metrics) -> dict:
    """Which signoff checks this source fed, and which it cannot.

    `signoff_metrics` is orchestrator.SIGNOFF_METRICS, passed in rather
    than imported so this module has no dependency on the orchestrator.
    """
    checked = [k for k, _ in signoff_metrics if k in metrics]
    missing = {k: NOT_PRODUCED.get(k, "not in iEDA feature JSON")
               for k, _ in signoff_metrics if k not in metrics}
    return {
        "source": metrics.get("metrics__source"),
        "checked": checked,
        "never_checked": missing,
        "not_yet": NOT_YET,
    }


def main(argv=None):
    import argparse
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import orchestrator  # noqa: E402

    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="+", type=Path,
                    help="iEDA feature JSON files (summary, tool steps, eval)")
    ap.add_argument("--targets", type=Path,
                    help="run_spec.json whose `targets` block to gate against")
    args = ap.parse_args(argv)

    targets = {}
    if args.targets:
        targets = json.loads(args.targets.read_text()).get("targets", {})
    feature = load_feature_files(args.files)
    metrics = to_openlane_metrics(feature)
    verdict = orchestrator.score(metrics, targets)
    print(json.dumps({
        "metrics": metrics,
        "verdict": verdict,
        "coverage": coverage(metrics, orchestrator.SIGNOFF_METRICS),
    }, indent=2))


if __name__ == "__main__":
    main()
