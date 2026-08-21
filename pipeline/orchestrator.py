#!/usr/bin/env python3
"""Drives one candidate-generation-and-feedback iteration of the layout
pipeline, using run_stage.py to execute real OpenLane flows.

Given a run_spec.json (see pipeline/designs/*/run_spec.json), generates N
placement-strategy candidates (config overrides), runs each through the
real flow, evaluates the real metrics.json each produces against the
spec's targets, and writes the winner (or best-so-far, if none meet
targets) as a case into reference-db/.

This is the mechanical half of "AI feedback/repair/optimization": the
candidate *proposals* and the *interpretation* of why one core utilization
or die size is a better next guess than another belong to the
placement-strategist / feedback-optimizer subagents (.claude/agents/) — a
human or an agent session reads this script's JSON output and decides the
next candidate set. This script's job is only to run real candidates and
score them consistently, not to invent optimization strategy itself.
"""
import argparse
import json
import sys
from datetime import date
from pathlib import Path

from run_stage import run_stage, read_metrics

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"
PDK_ROOT = REPO_ROOT / "pdk"


def pdk_version() -> str | None:
    """Reads the actually-installed sky130 PDK version (real, not assumed).

    Enabled via `volare enable --pdk sky130 --pdk-root pdk <version>` per
    docs/superpowers/specs/2026-08-21-autonomous-layout-agent-design.md —
    the version is the directory name under pdk/volare/sky130/versions/.
    """
    versions_dir = PDK_ROOT / "volare" / "sky130" / "versions"
    if not versions_dir.is_dir():
        return None
    versions = sorted(p.name for p in versions_dir.iterdir() if p.is_dir())
    return versions[0] if versions else None


def data_pointers(run_dir: Path) -> dict:
    """Real file pointers for a completed run, organized by the four data
    categories this pipeline is built around (circuit / layout /
    constraint-PDK / verification) — see circuit-layout-extractor.md.
    Only records paths that actually exist; never fabricates a path.
    """
    final = run_dir / "final"

    def existing(rel: str) -> str | None:
        p = final / rel
        return str(p) if p.exists() else None

    return {
        "circuit": {
            "netlist_verilog": existing("nl"),
            "netlist_powered_verilog": existing("pnl"),
            "spice_netlist": existing("spice"),
        },
        "layout": {
            "def": existing("def"),
            "lef": existing("lef"),
            "gds": existing("gds"),
        },
        "constraint_pdk": {
            "pdk_version": pdk_version(),
            "sdc": existing("sdc"),
        },
        "verification": {
            "metrics_json": existing("metrics.json"),
            "spef": existing("spef"),
            "sdf": existing("sdf"),
        },
    }


def score(metrics: dict, targets: dict) -> dict:
    """Checks a real metrics.json against run_spec targets.

    Returns {"passed": bool, "violations": [...], "area": float}.
    Every field read here is a real OpenLane metric key — see
    docs/superpowers/specs/2026-08-21-autonomous-layout-agent-design.md
    for why we trust metrics.json rather than re-deriving PPA ourselves.
    """
    violations = []

    drc = metrics.get("magic__drc_error__count", None)
    if drc is None:
        violations.append("no DRC result in metrics.json (run incomplete?)")
    elif drc > 0:
        violations.append(f"{drc} DRC error(s)")

    lvs = metrics.get("design__lvs_error__count", 0)
    if lvs:
        violations.append(f"{lvs} LVS error(s)")

    max_util = targets.get("max_core_utilization")
    util = metrics.get("design__instance__utilization__stdcell")
    if max_util is not None and util is not None and util > max_util:
        violations.append(f"utilization {util:.3f} > target {max_util}")

    # Worst setup slack across corners; OpenLane emits one WNS key per
    # corner (timing__setup__wns__corner:<name>) — a negative value on
    # any of them is a real timing violation at that corner.
    setup_wns_keys = [k for k in metrics if k.startswith("timing__setup__wns__corner:")]
    worst_wns = min((metrics[k] for k in setup_wns_keys), default=0)
    if worst_wns < 0:
        violations.append(f"worst setup WNS {worst_wns} (timing violation)")

    return {
        "passed": len(violations) == 0,
        "violations": violations,
        "area_um2": metrics.get("design__instance__area"),
        "utilization": util,
        "worst_setup_wns": worst_wns,
    }


def run_candidates(design_dir: Path, run_spec: dict) -> list[dict]:
    results = []
    for cand in run_spec["candidates"]:
        tag = cand["tag"]
        overrides = [f"{k}={json.dumps(v)}" for k, v in cand.get("overrides", {}).items()]
        print(f"\n=== candidate '{tag}' — overrides: {cand.get('overrides', {})} ===",
              file=sys.stderr)
        try:
            run_dir = run_stage(design_dir, tag, to_step=None, overrides=overrides)
            metrics = read_metrics(run_dir)
            verdict = score(metrics, run_spec.get("targets", {}))
            results.append({"tag": tag, "overrides": cand.get("overrides", {}),
                             "verdict": verdict, "run_dir": str(run_dir),
                             "data": data_pointers(run_dir)})
        except Exception as e:  # noqa: BLE001 - report and keep evaluating others
            results.append({"tag": tag, "overrides": cand.get("overrides", {}),
                             "error": str(e)})
    return results


def pick_winner(results: list[dict]) -> dict | None:
    passing = [r for r in results if r.get("verdict", {}).get("passed")]
    if not passing:
        return None
    # Among candidates meeting targets, prefer smallest area.
    return min(passing, key=lambda r: r["verdict"]["area_um2"] or float("inf"))


def write_case(design_name: str, results: list[dict], winner: dict | None) -> Path:
    REFDB.mkdir(parents=True, exist_ok=True)
    (REFDB / "cases").mkdir(exist_ok=True)
    case_file = REFDB / "cases" / f"{design_name}__{date.today().isoformat()}.json"
    case = {
        "design": design_name,
        "date": date.today().isoformat(),
        "candidates": results,
        "winner_tag": winner["tag"] if winner else None,
        "outcome": "passed" if winner else "no candidate met targets",
    }
    case_file.write_text(json.dumps(case, indent=2))

    index_file = REFDB / "index.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}
    index[design_name] = index.get(design_name, []) + [case_file.name]
    index_file.write_text(json.dumps(index, indent=2))
    return case_file


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--run-spec", required=True, type=Path,
                     help="path to a run_spec.json (candidates + targets)")
    args = ap.parse_args()

    run_spec = json.loads(args.run_spec.read_text())
    design_name = run_spec.get("design_name", args.design.name)

    results = run_candidates(args.design, run_spec)
    winner = pick_winner(results)
    case_file = write_case(design_name, results, winner)

    print("\n=== iteration summary ===")
    for r in results:
        if "error" in r:
            print(f"  {r['tag']}: FAILED TO RUN — {r['error']}")
        else:
            v = r["verdict"]
            status = "PASS" if v["passed"] else f"FAIL ({'; '.join(v['violations'])})"
            print(f"  {r['tag']}: {status} — area={v['area_um2']} um^2, "
                  f"util={v['utilization']}, worst_setup_wns={v['worst_setup_wns']}")
    print(f"\nwinner: {winner['tag'] if winner else 'none — needs a new candidate set'}")
    print(f"case written to: {case_file}")


if __name__ == "__main__":
    main()
