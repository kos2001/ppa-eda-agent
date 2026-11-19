#!/usr/bin/env python3
"""Measure throughput evidence for completed runs, then score it.

For each completed OpenLane run: run the spec's functional regression (RTL
plus, when the spec asks for it, the run's own post-route netlist), read
the run's final metrics.json and resolved CLOCK_PERIOD, and build one
throughput_score evidence run.  No previous pass is reused.

    throughput_from_runs.py --design pipeline/designs/riscv32i \
        --run-spec pipeline/designs/riscv32i/run_spec_throughput.json \
        --baseline-run <run dir> --report out.json <run dir> [<run dir> ...]

The baseline run supplies the reference cell area for the area-ratio gate;
the caller chooses it and the report records which run it was.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from pathlib import Path

import functional_regression
import throughput_score


def _metrics(run_dir: Path) -> dict:
    return json.loads((run_dir / "final" / "metrics.json").read_text(encoding="utf-8"))


def stage_run(run_dir: Path, stage_root: Path) -> tuple[Path, dict]:
    """Copy exactly the files the regression reads into a writable dir.

    Original run directories may be read-only (created by the OpenLane
    container).  The copies are hashed so the report ties each result to
    the original run's bytes.
    """
    resolved = json.loads((run_dir / "resolved.json").read_text(encoding="utf-8"))
    top = resolved["DESIGN_NAME"]
    wanted = ["resolved.json", "final/metrics.json", f"final/nl/{top}.nl.v"]
    dest = stage_root / run_dir.name
    hashes = {}
    for rel in wanted:
        src, dst = run_dir / rel, dest / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        hashes[rel] = hashlib.sha256(dst.read_bytes()).hexdigest()
    return dest, hashes


def collect(design: Path, spec: dict, run_dirs: list[Path], baseline_run: Path,
            stage_root: Path | None = None) -> dict:
    tp = spec.get("throughput") or {}
    held_out = list(tp.get("held_out_workloads") or [])
    if not held_out:
        raise ValueError("run spec throughput.held_out_workloads is empty")
    base_m = _metrics(baseline_run)
    baseline_area = base_m.get("design__instance__area__stdcell",
                               base_m.get("design__instance__area"))
    runs, provenance = [], []
    for original in run_dirs:
        run_dir, hashes = (stage_run(original, stage_root) if stage_root
                           else (original, None))
        resolved = json.loads((run_dir / "resolved.json").read_text(encoding="utf-8"))
        regression = functional_regression.check(design, run_dir, spec["functional_regression"])
        run = throughput_score.run_from_artifacts(
            run_dir.name, regression, _metrics(run_dir), resolved["CLOCK_PERIOD"],
            held_out, group=tp.get("group"))
        if spec["functional_regression"].get("post_route"):
            # The post-route netlist must reproduce the RTL cycle counts:
            # IPC is measured on RTL but must hold for what was signed off.
            post = regression.get("post_route") or {}
            rtl_perf = [r.get("perf") for r in regression.get("runs") or []]
            post_perf = [r.get("perf") for r in post.get("runs") or []]
            if rtl_perf != post_perf:
                run["functional"] = "failed"
                run["note"] = "post-route cycle counts differ from RTL"
        runs.append(run)
        provenance.append({"run": str(original), "staged_sha256": hashes,
                           "regression_status": regression["status"],
                           "artifact_dir": regression.get("artifact_dir")})
    return {
        "design": design.name,
        "max_area_ratio": tp.get("max_area_ratio", throughput_score.DEFAULT_MAX_AREA_RATIO),
        "baseline": {"cell_area_um2": baseline_area, "run": str(baseline_run)},
        "required_features": list(tp.get("required_features") or []),
        "runs": runs,
        "provenance": provenance,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--design", type=Path, required=True)
    ap.add_argument("--run-spec", type=Path, required=True)
    ap.add_argument("--baseline-run", type=Path, required=True)
    ap.add_argument("--report", type=Path, required=True)
    ap.add_argument("--stage-dir", type=Path,
                    help="copy each run's resolved.json, metrics and final netlist here "
                         "and run the regression on the copy (for read-only run dirs)")
    ap.add_argument("runs", type=Path, nargs="+")
    args = ap.parse_args(argv)
    spec = json.loads(args.run_spec.read_text(encoding="utf-8"))
    evidence = collect(args.design, spec, args.runs, args.baseline_run, args.stage_dir)
    result = throughput_score.score(evidence)
    result["evidence"] = evidence
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    o = result["overall"]
    print(f"{result['design']}: {o['passing']}/{o['runs']} passing, mean {o['mean_score']:.3f}")
    for r in result["runs"]:
        extra = (f"IPC {r['ipc_geomean_held_out']:.3f} x {r['fmax_mhz']:.2f} MHz = "
                 f"{r['throughput_mips']:.2f} MIPS, area {r['area_ratio']:.3f}x"
                 if r["zero_reason"] is None else r["zero_reason"])
        print(f"  {r['score']:.3f}  {r['tag']}: {extra}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
