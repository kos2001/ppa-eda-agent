#!/usr/bin/env python3
r"""Which placement/routing knobs actually move the result? Measured, not assumed.

The pipeline sweeps FP_CORE_UTIL, CLOCK_PERIOD and SYNTH_STRATEGY. Of the
484 candidates in reference-db, those three account for nearly every
override: PL_TARGET_DENSITY_PCT appears 4 times, a CTS knob twice, and
none of the global-routing, timing-driven/routability-driven placement or
resizer-margin knobs OpenLane 2.3.10 exposes has ever been tried. Whether
they matter is an empirical question about *this* flow on *these* designs,
and a plausible-sounding answer from the literature is not a measurement.

This runs the question for real: one baseline, then each knob moved alone
(one-factor-at-a-time), every run a full OpenLane flow scored by the same
path as any other candidate (orchestrator.run_candidate). What it records
per run is what the placer and router actually decide: routed wirelength,
via count, repair-buffer count, first-pass detailed-route DRC count (the
routability signal), power, area and slack.

THE NOISE FLOOR COMES FIRST. OpenROAD is deterministic — the same config
gives the same bytes — but it is chaotic: a 1% change to an unrelated
parameter moves wirelength by a few percent for no reason anyone can
name. A knob whose effect is smaller than that is indistinguishable from
rerolling the dice, so the study includes `probes`: perturbations that
physically should not matter (FP_CORE_UTIL +-1, CLOCK_PERIOD +1%).
summarise() reports each knob's effect against the spread of those probes
and refuses to call anything inside the spread a finding.

WHAT IT CANNOT SHOW, stated because the table is worthless without it:

  * One design's response is one sample. A knob is only called consistent
    when two or more designs move the same way beyond the noise floor.
  * OFAT sees no interactions. GRT_ADJUSTMENT may matter only at high
    utilization; a baseline at 38% will not show that.
  * Small designs route trivially. A knob that exists to relieve
    congestion cannot show its benefit on a design with no congestion, and
    "no effect here" must not be read as "no effect".

Usage (needs Docker and the PDK; ~1-2 min per run on gcd/spm):
    pnr_study.py --design designs/gcd --parallel 4 --out ../reference-db/pnr_study/gcd.json
    pnr_study.py --summarise ../reference-db/pnr_study/gcd.json ../reference-db/pnr_study/spm.json
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

# The knobs, each verified present in OpenLane 2.3.10's Classic flow by
# listing its config variables inside the pinned image (not recalled from
# memory — OpenLane 1 names such as RE_BUFFER_CELL silently do nothing in
# 2). run_stage.reject_ignored_overrides() fails a run that passes a name
# the flow does not recognise, so a wrong entry here costs a run, not a
# false conclusion.
#
# `axis` says which stage of the flow the knob acts on; `why` is the
# mechanism that makes it worth a run, stated so a null result is
# interpretable as "that mechanism did nothing here".
KNOBS: list[dict] = [
    {"knob": "PL_TARGET_DENSITY_PCT", "axis": "global_placement",
     "rel": {"base": "FP_CORE_UTIL", "add": [3, 20, 35]},
     "why": "default is FP_CORE_UTIL + 10; denser packing shortens wires, "
            "looser packing leaves room for the resizer and legaliser"},
    {"knob": "PL_TIME_DRIVEN", "axis": "global_placement", "values": [False],
     "why": "timing-driven net reweighting costs runtime; does it buy slack?"},
    {"knob": "PL_ROUTABILITY_DRIVEN", "axis": "global_placement", "values": [False],
     "why": "cell inflation against congestion; cost is wirelength"},
    {"knob": "PL_WIRE_LENGTH_COEF", "axis": "global_placement", "values": [0.1, 0.5],
     "why": "initial wirelength coefficient of the Nesterov solver"},
    {"knob": "GPL_CELL_PADDING", "axis": "global_placement", "values": [2, 4],
     "why": "reserves routing room around cells during placement"},
    {"knob": "GRT_ADJUSTMENT", "axis": "global_routing", "values": [0.1, 0.5],
     "why": "capacity derating; higher spreads routes, lower packs them"},
    {"knob": "CTS_SINK_CLUSTERING_SIZE", "axis": "cts", "values": [10, 40],
     "why": "sinks per leaf buffer; trades clock buffer count against skew"},
    {"knob": "CTS_SINK_CLUSTERING_MAX_DIAMETER", "axis": "cts", "values": [30, 100],
     "why": "cluster reach in um; wider means fewer, longer leaf nets"},
    {"knob": "PL_RESIZER_SETUP_SLACK_MARGIN", "axis": "resizer", "values": [0.0, 0.2],
     "why": "extra slack the post-CTS repair aims for beyond zero"},
    {"knob": "PL_RESIZER_HOLD_SLACK_MARGIN", "axis": "resizer", "values": [0.0, 0.2],
     "why": "hold margin; sets how many delay buffers get inserted"},
    {"knob": "DESIGN_REPAIR_MAX_SLEW_PCT", "axis": "resizer", "values": [0, 40],
     "why": "slew margin of the post-placement repair"},
    {"knob": "DESIGN_REPAIR_BUFFER_INPUT_PORTS", "axis": "resizer", "values": [False],
     "why": "port buffers are a large share of cells on small designs"},
    {"knob": "DESIGN_REPAIR_BUFFER_OUTPUT_PORTS", "axis": "resizer", "values": [False],
     "why": "same, for outputs"},
    {"knob": "FP_ASPECT_RATIO", "axis": "floorplan", "values": [0.5, 2],
     "why": "die shape changes IO-to-core distance"},
]

# Perturbations that physically should not matter. Their spread is the
# noise floor every knob is judged against.
PROBES: list[dict] = [
    {"knob": "FP_CORE_UTIL", "rel": {"base": "FP_CORE_UTIL", "add": [-1, 1]}},
    {"knob": "CLOCK_PERIOD", "rel": {"base": "CLOCK_PERIOD", "mul": [0.99, 1.01]}},
]

# An effect must also be at least this large, whatever the probes say. The
# noise floor is the extreme of only four probes, so on a design where they
# happen to land close together it can be under 1%, and a 1-3% shift on a
# secondary metric would then be reported as a finding. (The first
# two-design summary flagged PL_TIME_DRIVEN=False for a 3.1% / 1.4% change
# in hold slack with no other effect; that is not a result.)
MIN_EFFECT_PCT = 3.0

# What gets compared. `better` is the direction that is an improvement.
METRICS: dict[str, str] = {
    "area_um2": "lower", "power_w": "lower", "wirelength": "lower",
    "vias": "lower", "repair_buffers": "lower", "clock_buffers": "lower",
    "first_pass_drc": "lower", "setup_ws": "higher", "hold_ws": "higher",
}


def base_value(config: dict, name: str):
    value = config.get(name)
    return float(value) if isinstance(value, (int, float)) else None


def plan(config: dict, knobs: list[dict] = KNOBS,
         probes: list[dict] = PROBES) -> list[dict]:
    """Baseline plus one candidate per (knob, value), as orchestrator
    candidates. A value equal to the config's own is skipped — it would be
    a duplicate of the baseline reported as an experiment."""
    out = [{"tag": "pnr-baseline", "overrides": {}, "role": "baseline"}]
    seen = {json.dumps({}, sort_keys=True)}

    def add(role, knob, value):
        if config.get(knob) == value:
            return
        ov = {knob: value}
        sig = json.dumps(ov, sort_keys=True)
        if sig in seen:
            return
        seen.add(sig)
        tag = f"pnr-{knob.lower()}-{str(value).replace('.', 'p').replace('-', 'm')}"
        out.append({"tag": tag, "overrides": ov, "role": role, "knob": knob,
                    "value": value})

    for group, role in ((probes, "probe"), (knobs, "knob")):
        for k in group:
            rel = k.get("rel")
            if rel:
                base = base_value(config, rel["base"])
                if base is None:
                    continue  # nothing to be relative to
                for d in rel.get("add", []):
                    add(role, k["knob"], round(base + d, 6))
                for m in rel.get("mul", []):
                    add(role, k["knob"], round(base * m, 6))
            for v in k.get("values", []):
                add(role, k["knob"], v)
    return out


def extract(result: dict, metrics: dict) -> dict:
    """The comparable numbers from one scored run. None where the run did
    not produce one — a missing metric is not a zero."""
    v = result.get("verdict") or {}

    def corner_min(prefix):
        vals = [x for k, x in metrics.items()
                if k.startswith(prefix) and isinstance(x, (int, float))]
        return min(vals) if vals else None

    power = (v.get("power") or {}).get("total_w")
    return {
        "passed": bool(v.get("passed")),
        "violations": v.get("violations", []),
        "error": (result.get("error") or "")[-300:] or None,
        "area_um2": metrics.get("design__instance__area"),
        "power_w": power if power is not None else metrics.get("power__total"),
        "wirelength": metrics.get("route__wirelength"),
        "vias": metrics.get("route__vias"),
        "repair_buffers": metrics.get("design__instance__count__class:timing_repair_buffer"),
        "clock_buffers": metrics.get("design__instance__count__class:clock_buffer"),
        "first_pass_drc": metrics.get("route__drc_errors__iter:1"),
        "setup_ws": corner_min("timing__setup__ws__corner:"),
        "hold_ws": corner_min("timing__hold__ws__corner:"),
        "seconds": result.get("seconds"),
    }


def pct_delta(value, base):
    """Percent change of value against base; None when either is missing
    or the baseline is zero (a percentage of zero is not a finding)."""
    if value is None or base in (None, 0):
        return None
    return (value - base) / abs(base) * 100.0


def noise_floor(rows: list[dict], metric: str) -> float | None:
    """Largest |percent change| any probe produced on `metric`, or None
    when there are no usable probes. The maximum, not the mean: the claim
    being protected is "this is outside anything the dice produce"."""
    base = next((r for r in rows if r["role"] == "baseline"), None)
    if base is None:
        return None
    spread = [abs(d) for r in rows if r["role"] == "probe"
              if (d := pct_delta(r["m"].get(metric), base["m"].get(metric))) is not None]
    return max(spread) if spread else None


def summarise(rows: list[dict]) -> dict:
    """Per knob and metric: the change against baseline, the noise floor,
    and whether the change clears it in the improving direction."""
    base = next((r for r in rows if r["role"] == "baseline"), None)
    if base is None:
        raise ValueError("no baseline row")
    floors = {m: noise_floor(rows, m) for m in METRICS}
    table = []
    for r in rows:
        if r["role"] != "knob":
            continue
        entry = {"knob": r["knob"], "value": r["value"], "tag": r["tag"],
                 "passed": r["m"]["passed"], "changes": {}, "clears_noise": [],
                 "regresses": []}
        for metric, better in METRICS.items():
            d = pct_delta(r["m"].get(metric), base["m"].get(metric))
            if d is None:
                continue
            entry["changes"][metric] = round(d, 2)
            floor = floors[metric]
            improves = d < 0 if better == "lower" else d > 0
            material = floor is not None and abs(d) > max(floor, MIN_EFFECT_PCT)
            if material and improves and r["m"]["passed"]:
                entry["clears_noise"].append(metric)
            elif material and not improves:
                entry["regresses"].append(metric)
        table.append(entry)
    return {"baseline": base["m"], "noise_floor_pct": {
        k: (round(v, 2) if v is not None else None) for k, v in floors.items()},
        "knobs": table}


def consistent(summaries: dict[str, dict]) -> list[dict]:
    """Knob settings that improved the same metric beyond the noise floor
    on at least two designs. Anything seen on one design is a lead, not a
    finding, and is deliberately not returned.

    `also_regresses` lists metrics the same setting made materially worse
    on every contributing design, so a trade (hold margin 0.2 buys hold
    slack and costs 8-17% area) is not read as a free win."""
    seen: dict[tuple, dict[str, dict]] = {}
    for design, s in summaries.items():
        for e in s["knobs"]:
            for metric in e["clears_noise"]:
                seen.setdefault((e["knob"], e["value"], metric), {})[design] = {
                    "pct": e["changes"][metric], "regresses": set(e["regresses"])}
    out = []
    for (knob, value, metric), d in sorted(seen.items(), key=lambda kv: str(kv[0])):
        if len(d) < 2:
            continue
        shared = set.intersection(*(v["regresses"] for v in d.values()))
        out.append({"knob": knob, "value": value, "metric": metric,
                    "designs": {k: v["pct"] for k, v in d.items()},
                    "also_regresses": sorted(shared)})
    return out


def run_study(design_dir: Path, parallel: int, run_spec: dict | None = None,
              only: list[str] | None = None) -> list[dict]:
    """Runs the plan for real and returns rows of {tag, role, ..., m}."""
    import orchestrator  # deferred: importing it pulls in the whole pipeline
    from run_stage import read_metrics

    config = json.loads((design_dir / "config.json").read_text(encoding="utf-8"))
    spec = run_spec or {"design_name": design_dir.name, "targets": {}}
    cands = plan(config)
    if only:
        cands = [c for c in cands if c["role"] == "baseline" or c["tag"] in only]

    def one(c):
        t0 = time.time()
        res = orchestrator.run_candidate(
            design_dir, spec, {"tag": c["tag"], "overrides": c["overrides"]})
        metrics = {}
        if res.get("run_dir"):
            try:
                metrics = read_metrics(Path(res["run_dir"]))
            except (OSError, ValueError):
                metrics = {}
        return {**c, "m": extract(res, metrics), "wall_s": round(time.time() - t0, 1)}

    rows = []
    with ThreadPoolExecutor(max_workers=max(1, parallel)) as ex:
        futs = {ex.submit(one, c): c for c in cands}
        for f in as_completed(futs):
            rows.append(f.result())
            print(f"  done {rows[-1]['tag']}: passed={rows[-1]['m']['passed']}",
                  file=sys.stderr)
    order = {c["tag"]: i for i, c in enumerate(cands)}
    rows.sort(key=lambda r: order[r["tag"]])
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", type=Path, help="design dir to study")
    ap.add_argument("--parallel", type=int, default=3)
    ap.add_argument("--only", nargs="*", help="restrict to these candidate tags")
    ap.add_argument("--out", type=Path, help="write the raw rows here")
    ap.add_argument("--summarise", nargs="+", type=Path,
                    help="summarise saved study files instead of running")
    args = ap.parse_args()

    if args.summarise:
        summaries = {}
        for p in args.summarise:
            data = json.loads(p.read_text(encoding="utf-8"))
            summaries[data["design"]] = summarise(data["rows"])
        print(json.dumps({"designs": summaries,
                          "consistent": consistent(summaries)}, indent=2))
        return
    if not args.design:
        ap.error("--design or --summarise is required")
    rows = run_study(args.design.resolve(), args.parallel, only=args.only)
    payload = {"design": args.design.resolve().name, "rows": rows}
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(payload, indent=1), encoding="utf-8")
    print(json.dumps(summarise(rows), indent=2))


if __name__ == "__main__":
    sys.exit(main())
