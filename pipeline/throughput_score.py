#!/usr/bin/env python3
"""Throughput scoring for RTL / microarchitecture variants.

Adapted from the evaluation protocol in OpenAI's "Advancing chip design
with OpenAI models" (2026-11-18), RISC-V RTL-optimization task:

  * a design scores ZERO if it fails a functional check or uses more than
    K x the original core's area (K = 16 there);
  * for passing designs, throughput = geometric mean of IPC over workloads
    HIDDEN from the optimizer  x  maximum clock frequency;
  * per design, the best throughput observed under the same constraints
    is 1.0, and reported averages INCLUDE the zero-scored runs.

Why this exists here.  score()/pick_winner() judge OpenLane knob candidates
at a fixed CLOCK_PERIOD: timing is pass/fail and cycles-per-instruction
never changes.  An RTL rewrite (e.g. a 2-cycle multiplier made 1-cycle,
or re-staged pipeline) moves IPC and cycle time in opposite directions, so
neither area nor slack alone can rank it; instructions-per-second can.

Two deliberate departures from the article, both in the direction its own
limitations section recommends:

  * Fmax comes from post-route STA (period - worst setup slack) when it is
    available, falling back to a pre-layout estimate only when explicitly
    labelled so.  The article used a Yosys estimate and named "checking
    timing after physical synthesis" as a needed improvement.
  * `required_features` lets a spec forbid buying throughput by dropping
    features (performance counters, extra instructions) — the article's
    stated explanation for spec-written cores out-scoring rewrites.

Everything here is deterministic arithmetic over recorded measurements;
nothing is estimated or invented.  Missing evidence scores zero with a
reason, it is never imputed.

Evidence file (JSON):
{
  "design": "riscv32i",
  "max_area_ratio": 16,
  "baseline": {"cell_area_um2": 41000.0},
  "required_features": ["csr_counters", "rv32m"],
  "runs": [
    {"tag": "rewrite-a", "group": "budget-112k",
     "functional": "passed",
     "cell_area_um2": 43000.0,
     "fmax": {"source": "post_route_sta", "clock_period_ns": 20.0,
              "setup_ws_ns": 1.5},
     "features": {"csr_counters": true, "rv32m": true},
     "workloads": {
        "coremark":  {"held_out": true,  "passed": true,
                      "instructions": 1000000, "cycles": 1450000},
        "dhrystone": {"held_out": false, "passed": true,
                      "instructions": 500000,  "cycles": 600000}}}
  ]
}

Usage:
    throughput_score.py evidence.json [--report out.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

DEFAULT_MAX_AREA_RATIO = 16.0
FMAX_SOURCES = {"post_route_sta", "pre_layout_estimate"}


def _finite_pos(x) -> bool:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x) and x > 0)


def fmax_mhz(fmax: dict | None) -> tuple[float | None, str | None, str]:
    """(MHz, source, reason).  Post-route: 1000 / (period - setup WS).

    Positive slack means the clock could run faster; negative slack means
    the achieved period is longer than the constraint.  Both are folded in.
    """
    if not isinstance(fmax, dict):
        return None, None, "no Fmax evidence"
    source = fmax.get("source")
    if source not in FMAX_SOURCES:
        return None, None, f"Fmax source must be one of {sorted(FMAX_SOURCES)}"
    if "mhz" in fmax:
        mhz = fmax["mhz"]
        return (mhz, source, "") if _finite_pos(mhz) else (None, source, "invalid Fmax")
    period, ws = fmax.get("clock_period_ns"), fmax.get("setup_ws_ns")
    if not _finite_pos(period) or not isinstance(ws, (int, float)) \
            or isinstance(ws, bool) or not math.isfinite(ws):
        return None, source, "Fmax needs clock_period_ns and setup_ws_ns"
    achieved = period - ws
    if achieved <= 0:
        return None, source, "non-physical achieved period (period - setup_ws <= 0)"
    return 1000.0 / achieved, source, ""


def geomean(values: list[float]) -> float:
    return math.exp(sum(math.log(v) for v in values) / len(values))


def score_run(run: dict, baseline_area: float, max_ratio: float,
              required_features: list[str]) -> dict:
    """Raw throughput (MIPS) for one run, or zero with the reason."""
    tag = run.get("tag", "?")
    out = {"tag": tag, "group": run.get("group"), "throughput_mips": 0.0,
           "zero_reason": None}

    def zero(reason: str) -> dict:
        out["zero_reason"] = reason
        return out

    if run.get("functional") != "passed":
        return zero(f"functional check {run.get('functional', 'missing')}")
    missing = [f for f in required_features
               if (run.get("features") or {}).get(f) is not True]
    if missing:
        return zero("required feature(s) absent or unverified: " + ", ".join(missing))
    area = run.get("cell_area_um2")
    if not _finite_pos(area):
        return zero("cell area missing or invalid")
    out["area_ratio"] = area / baseline_area
    if area > max_ratio * baseline_area:
        return zero(f"area {out['area_ratio']:.2f}x baseline exceeds {max_ratio:g}x")

    workloads = run.get("workloads") or {}
    failed = [n for n, w in workloads.items() if w.get("passed") is not True]
    if failed:
        return zero("workload(s) failed: " + ", ".join(sorted(failed)))
    hidden = {n: w for n, w in workloads.items() if w.get("held_out") is True}
    if not hidden:
        return zero("no held-out workload measured (IPC must come from "
                    "workloads hidden from the optimizer)")
    ipcs = {}
    for name, w in hidden.items():
        ins, cyc = w.get("instructions"), w.get("cycles")
        if not (_finite_pos(ins) and _finite_pos(cyc)):
            return zero(f"workload {name}: instructions/cycles missing")
        ipcs[name] = ins / cyc

    mhz, source, why = fmax_mhz(run.get("fmax"))
    if mhz is None:
        return zero(why)
    out.update({
        "ipc_geomean_held_out": geomean(list(ipcs.values())),
        "ipc_by_workload": ipcs,
        "fmax_mhz": mhz,
        "fmax_source": source,
    })
    out["throughput_mips"] = out["ipc_geomean_held_out"] * mhz
    return out


def score(evidence: dict) -> dict:
    baseline = (evidence.get("baseline") or {}).get("cell_area_um2")
    if not _finite_pos(baseline):
        raise ValueError("baseline.cell_area_um2 must be a measured positive area")
    max_ratio = evidence.get("max_area_ratio", DEFAULT_MAX_AREA_RATIO)
    if not _finite_pos(max_ratio):
        raise ValueError("max_area_ratio must be positive")
    required = list(evidence.get("required_features") or [])
    runs = evidence.get("runs") or []
    if not runs:
        raise ValueError("evidence has no runs")

    scored = [score_run(r, baseline, max_ratio, required) for r in runs]
    best = max(s["throughput_mips"] for s in scored)
    # The article normalizes to the best throughput observed under the same
    # constraints.  A supplied reference best (e.g. from earlier batches)
    # keeps scores comparable across evidence files.
    ref = evidence.get("reference_best_mips")
    if ref is not None and not _finite_pos(ref):
        raise ValueError("reference_best_mips must be positive")
    norm = max(best, ref or 0.0)
    for s in scored:
        s["score"] = s["throughput_mips"] / norm if norm > 0 else 0.0

    def summary(items: list[dict]) -> dict:
        n = len(items)
        passing = [s for s in items if s["zero_reason"] is None]
        return {
            "runs": n,
            "passing": len(passing),
            "pass_rate": len(passing) / n,
            # Mean INCLUDING zero-scored runs: a strategy that is fast when it
            # works but often breaks the design must not look better than it is.
            "mean_score": sum(s["score"] for s in items) / n,
            "best_score": max(s["score"] for s in items),
        }

    groups: dict[str, list[dict]] = {}
    for s in scored:
        groups.setdefault(str(s["group"]), []).append(s)
    any_estimate = any(s.get("fmax_source") == "pre_layout_estimate" for s in scored)
    return {
        "design": evidence.get("design"),
        "protocol": {
            "max_area_ratio": max_ratio,
            "baseline_cell_area_um2": baseline,
            "required_features": required,
            "normalized_to_mips": norm,
            "ipc": "geometric mean over held-out workloads only",
        },
        "overall": summary(scored),
        "by_group": {g: summary(v) for g, v in sorted(groups.items())},
        "runs": sorted(scored, key=lambda s: -s["score"]),
        "scope": ("Throughput from recorded simulation cycle counts and STA; "
                  "not hidden-VIP grading."
                  + (" Some Fmax values are pre-layout estimates, not post-route."
                     if any_estimate else "")),
    }


def run_from_artifacts(tag: str, regression: dict, metrics: dict,
                       clock_period_ns: float, held_out: list[str],
                       features: dict | None = None, group: str | None = None) -> dict:
    """Build one evidence run from what the pipeline already records.

    regression: functional_regression.check() result (RTL stage; its seed
    runs carry `perf` from PPA_PERF lines).  metrics: the run's final
    OpenLane metrics.json.  Instructions and cycles are summed across seeds
    per workload; a workload is held out only if the spec names it so.
    """
    workloads: dict[str, dict] = {}
    for seed_run in regression.get("runs") or []:
        for name, c in (seed_run.get("perf") or {}).items():
            if name == "error":
                continue
            w = workloads.setdefault(name, {"held_out": name in held_out, "passed": True,
                                            "instructions": 0, "cycles": 0})
            w["instructions"] += c["instructions"]
            w["cycles"] += c["cycles"]
            w["passed"] = w["passed"] and seed_run.get("status") == "passed"
    for name in held_out:
        workloads.setdefault(name, {"held_out": True, "passed": False})
    ws = metrics.get("timing__setup__ws")
    return {
        "tag": tag, "group": group,
        "functional": regression.get("status", "unverified"),
        "cell_area_um2": metrics.get("design__instance__area__stdcell",
                                     metrics.get("design__instance__area")),
        "fmax": {"source": "post_route_sta", "clock_period_ns": clock_period_ns,
                 "setup_ws_ns": ws},
        "features": features or {},
        "workloads": workloads,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("evidence", type=Path)
    ap.add_argument("--report", type=Path)
    args = ap.parse_args(argv)
    result = score(json.loads(args.evidence.read_text(encoding="utf-8")))
    text = json.dumps(result, indent=2)
    if args.report:
        args.report.write_text(text + "\n", encoding="utf-8")
    o = result["overall"]
    print(f"{result['design']}: {o['passing']}/{o['runs']} passing, "
          f"mean score {o['mean_score']:.3f} (zeros included), best {o['best_score']:.3f}")
    for g, v in result["by_group"].items():
        print(f"  {g}: mean {v['mean_score']:.3f}, pass {v['pass_rate']:.0%}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
