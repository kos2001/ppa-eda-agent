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
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import sys
from datetime import date
from pathlib import Path

from run_stage import run_stage, read_metrics

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"
PDK_ROOT = REPO_ROOT / "pdk"

# The 8-step process this whole pipeline is organized around (see the
# 배경/목적/개선 process from the original goal, and
# docs/superpowers/specs/2026-08-21-autonomous-layout-agent-design.md's
# "Process mapping" table). Kept here, verbatim, as the single source of
# truth for stage names/order — the dashboard's Pipeline tab reads these
# same names via reference-db so the UI never drifts from what this
# script actually implements.
PROCESS_STAGES = [
    {"id": "extraction", "name": "Circuit & Layout Extraction"},
    {"id": "topology", "name": "Topology Understanding"},
    {"id": "placement_strategy", "name": "Placement Strategy / Candidate Generation"},
    {"id": "physical_constraint", "name": "Physical Constraint Evaluation"},
    {"id": "routing_generation", "name": "Routing Generation Evaluation"},
    {"id": "routing_candidate", "name": "Routing Candidate Generation"},
    {"id": "verification_ppa", "name": "Verification & PPA Evaluation"},
    {"id": "feedback", "name": "AI Feedback / Repair / Optimization"},
]


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


def read_topology(design_dir: Path) -> dict | None:
    """Reads a design's topology.json (circuit-layout-extractor's real
    output — see .claude/agents/circuit-layout-extractor.md), if present.

    This was previously an orphan file: written by hand alongside each
    design but never actually read by anything in the pipeline, so the
    "Topology Understanding" step of the process had no visible artifact
    in reference-db/ or the dashboard even though the file existed.
    """
    topology_file = design_dir / "topology.json"
    if not topology_file.exists():
        return None
    return json.loads(topology_file.read_text())


# Real error-text fingerprints, mapped to the PROCESS_STAGES id where
# each failure actually occurs. Kept next to propose_repairs()'s own
# fingerprints (some overlap) because both are reading the same real
# OpenLane error text — see reference-db/cases/*.json for the runs each
# pattern was pulled from.
_STAGE_ERROR_PATTERNS = [
    # Floorplan Init rejecting a too-small die, or PDN generation failing
    # (power-strap geometry, unplaced macros) — both structural/physical
    # problems caught before placement/routing can meaningfully proceed.
    ("core_area", "physical_constraint"),
    ("Insufficient width", "physical_constraint"),  # PDN strap-width failure
    ("unplaced macros", "physical_constraint"),
    ("not connected to any power/ground nets", "physical_constraint"),
    # Global/detailed routing and antenna-repair failures — happen after
    # a design has cleared placement/PDN, during/after actual routing.
    ("GRT-", "routing_generation"),
    ("DRT-", "routing_candidate"),
    ("DiodeInsertion", "routing_candidate"),
    ("Antenna", "routing_candidate"),
    # RSZ-0090 (max_transition DRV) fires during RepairDesignPostGPL —
    # pre-routing, but about electrical/physical proximity constraints
    # on cell/macro pins, so grouped with physical_constraint rather
    # than verification (which is signoff-time, post-routing).
    ("RSZ-0090", "physical_constraint"),
]


def classify_stage(result: dict) -> str:
    """Tags one candidate result with the PROCESS_STAGES id its own run
    outcome reached — independent of whether this candidate was itself
    produced by the feedback loop (see `produced_by_feedback` below,
    tracked separately: a repaired candidate that goes on to pass is
    both "produced by stage 8" AND "reached stage 7", and conflating
    those into one field would lose one fact or the other).

    Deliberately honest about what this pipeline does and doesn't
    separate: stages 5 (Routing Generation Evaluation) and 6 (Routing
    Candidate Generation) are not actually implemented as distinct
    steps here — run_stage.py runs one full OpenLane flow per candidate,
    it doesn't stop and re-evaluate between global and detailed routing.
    Classification below reflects that: a routing-stage failure is
    tagged with whichever of the two names its real OpenLane error text
    matches most specifically, not because this pipeline runs them as
    separate steps.
    """
    if "error" in result:
        # Match only against non-WARNING lines — run_stage.py's captured
        # error text is a raw tail of OpenLane's output, which includes
        # incidental warnings (e.g. a routine "[GRT-0097] No global
        # routing found for nets" printed before placement/PDN has even
        # run) that can accidentally match a pattern meant for an actual
        # fatal error occurring at a much later stage. Real bug found
        # this way: sram_wrapper's RSZ-0090 failure (physical_constraint)
        # was misclassified as routing_generation because that GRT-0097
        # warning happened to appear earlier in the same captured tail.
        error_lines = [ln for ln in result["error"].splitlines() if "WARNING" not in ln]
        error = "\n".join(error_lines)
        for pattern, stage in _STAGE_ERROR_PATTERNS:
            if pattern in error:
                return stage
        return "physical_constraint"  # unclassified run failure; still
        # pre-verification since it never produced a real metrics.json
    # A real verdict means metrics.json was produced — DRC/LVS/timing/
    # power all come from that real signoff data.
    return "verification_ppa"


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


def override_value(v) -> str:
    """Formats a config override for OpenLane's `--override-config KEY=VALUE`.

    Scalars: plain JSON (e.g. 35 -> "35"). Lists (e.g. DIE_AREA): a bare
    comma-joined list with no brackets/spaces — discovered the hard way
    (see reference-db/cases/counter4_tinydie__2026-08-21.json): passing
    a real JSON array literal like "[0, 0, 8, 8]" makes OpenLane's CLI
    parser mis-split it and error on a phantom variable 'DIE_AREA[0]'
    with value '[0'. Its List[Decimal]-typed variables want the elements
    directly, comma-separated, no brackets.
    """
    if isinstance(v, list):
        return ",".join(json.dumps(x) for x in v)
    return json.dumps(v)


def run_candidate(design_dir: Path, run_spec: dict, cand: dict) -> dict:
    """Runs and scores one independent candidate."""
    tag = cand["tag"]
    overrides = [f"{k}={override_value(v)}" for k, v in cand.get("overrides", {}).items()]
    print(f"\n=== candidate '{tag}' — overrides: {cand.get('overrides', {})} ===",
          file=sys.stderr)
    try:
        run_dir = run_stage(design_dir, tag, to_step=None, overrides=overrides)
        metrics = read_metrics(run_dir)
        verdict = score(metrics, run_spec.get("targets", {}))
        return {"tag": tag, "overrides": cand.get("overrides", {}),
                "verdict": verdict, "run_dir": str(run_dir),
                "data": data_pointers(run_dir)}
    except Exception as e:  # noqa: BLE001 - report and keep evaluating others
        return {"tag": tag, "overrides": cand.get("overrides", {}),
                "error": str(e)}


def run_candidates(design_dir: Path, run_spec: dict,
                   max_parallel: int = 1) -> list[dict]:
    candidates = run_spec["candidates"]
    if max_parallel <= 1 or len(candidates) <= 1:
        return [run_candidate(design_dir, run_spec, cand) for cand in candidates]

    results_by_tag = {}
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(candidates))) as executor:
        futures = {
            executor.submit(run_candidate, design_dir, run_spec, cand): cand["tag"]
            for cand in candidates
        }
        for future in as_completed(futures):
            tag = futures[future]
            try:
                results_by_tag[tag] = future.result()
            except Exception as e:  # defensive: preserve other candidate results
                cand = next(c for c in candidates if c["tag"] == tag)
                results_by_tag[tag] = {
                    "tag": tag,
                    "overrides": cand.get("overrides", {}),
                    "error": str(e),
                }

    # Preserve run_spec order so reports and reference cases stay deterministic.
    return [results_by_tag[cand["tag"]] for cand in candidates]


def pick_winner(results: list[dict]) -> dict | None:
    passing = [r for r in results if r.get("verdict", {}).get("passed")]
    if not passing:
        return None
    # Among candidates meeting targets, prefer smallest area.
    return min(passing, key=lambda r: r["verdict"]["area_um2"] or float("inf"))


# Known, real failure signatures this pipeline has actually observed and
# verified a repair for — see reference-db/cases/*.json for each one's
# full evidence. propose_repairs() stays deliberately narrow: anything
# not listed here is left for a human or the feedback-optimizer /
# placement-strategist subagents to diagnose, rather than guessed at
# (see the "Known limitations" section of the design spec).

# 1. counter4__2026-08-21: OpenROAD's PDN generator errors out rather
#    than degrading gracefully when core utilization is pushed too high
#    for the die's power-strap geometry.
PDN_STRAP_ERROR = "Insufficient width"
UTIL_STEP_DOWN = 15  # percentage points; conservative, matches the gap
                      # that separated the one passing candidate (35)
                      # from the first failing one (55) in that case.
MIN_CORE_UTIL = 20

# 2. counter4_tinydie__2026-08-21: OpenROAD's Floorplan Init step
#    rejects a DIE_AREA whose core area (after subtracting core margins)
#    is zero or negative — the die is structurally too small to fit
#    even the margins, before any cell placement is attempted. Distinct
#    from #1: this fails at a much earlier stage (Floorplan Init, before
#    placement/PDN), and the repair is DIE_AREA itself, not utilization.
DIE_TOO_SMALL_ERROR = "core_area"
DIE_AREA_GROWTH_FACTOR = 2  # doubles width/height each iteration; simple
                            # and matches the real counter4_tinydie case
                            # (8x8um -> 16x16um converged in one step)


def propose_repairs(results: list[dict], iteration: int) -> list[dict]:
    """Mechanically proposes a repaired candidate set from real failures."""
    next_candidates = []
    for r in results:
        error = r.get("error", "")
        overrides = r["overrides"]

        util_override = overrides.get("FP_CORE_UTIL")
        die_area_override = overrides.get("DIE_AREA")

        if PDN_STRAP_ERROR in error and isinstance(util_override, (int, float)):
            repaired = max(MIN_CORE_UTIL, util_override - UTIL_STEP_DOWN)
            if repaired == util_override:
                continue  # already at floor, no repair to propose
            new_overrides = dict(overrides)
            new_overrides["FP_CORE_UTIL"] = repaired
            next_candidates.append({
                "tag": f"{r['tag']}-iter{iteration}",
                "overrides": new_overrides,
            })
        elif DIE_TOO_SMALL_ERROR in error and isinstance(die_area_override, list) \
                and len(die_area_override) == 4:
            x0, y0, x1, y1 = die_area_override
            new_overrides = dict(overrides)
            new_overrides["DIE_AREA"] = [
                x0, y0,
                x0 + (x1 - x0) * DIE_AREA_GROWTH_FACTOR,
                y0 + (y1 - y0) * DIE_AREA_GROWTH_FACTOR,
            ]
            next_candidates.append({
                "tag": f"{r['tag']}-iter{iteration}",
                "overrides": new_overrides,
            })
        elif PDN_STRAP_ERROR in error and isinstance(die_area_override, list) \
                and len(die_area_override) == 4:
            # Same PDN strap failure as pattern #1, but this candidate has
            # no FP_CORE_UTIL to step down (it's using the default) — an
            # explicit DIE_AREA is the knob available here instead. Real
            # case: counter4_tinydie's 16x16um candidate got past
            # Floorplan Init (pattern #2 fixed that) only to hit this
            # same PDN-0185 error with no FP_CORE_UTIL override present.
            x0, y0, x1, y1 = die_area_override
            new_overrides = dict(overrides)
            new_overrides["DIE_AREA"] = [
                x0, y0,
                x0 + (x1 - x0) * DIE_AREA_GROWTH_FACTOR,
                y0 + (y1 - y0) * DIE_AREA_GROWTH_FACTOR,
            ]
            next_candidates.append({
                "tag": f"{r['tag']}-iter{iteration}",
                "overrides": new_overrides,
            })
        # Other failure/violation modes (DRC/LVS errors, timing violations,
        # unrecognized run errors) are not auto-repaired — flagged in the
        # iteration summary instead so a person or feedback-optimizer can
        # look at them.
    return next_candidates


def write_case(design_name: str, design_dir: Path, iterations: list[dict],
               winner: dict | None) -> Path:
    REFDB.mkdir(parents=True, exist_ok=True)
    (REFDB / "cases").mkdir(exist_ok=True)
    case_file = REFDB / "cases" / f"{design_name}__{date.today().isoformat()}.json"
    case = {
        "design": design_name,
        "date": date.today().isoformat(),
        "process_stages": PROCESS_STAGES,
        "topology": read_topology(design_dir),
        "iterations": iterations,
        "winner_tag": winner["tag"] if winner else None,
        "outcome": "passed" if winner else "no candidate met targets after all iterations",
    }
    case_file.write_text(json.dumps(case, indent=2))

    index_file = REFDB / "index.json"
    index = json.loads(index_file.read_text()) if index_file.exists() else {}
    existing = index.get(design_name, [])
    # A rerun on the same day overwrites case_file in place (same name) —
    # don't duplicate the index entry for it.
    if case_file.name not in existing:
        existing.append(case_file.name)
    index[design_name] = existing
    index_file.write_text(json.dumps(index, indent=2))
    return case_file


def print_iteration_summary(iteration: int, results: list[dict]) -> None:
    print(f"\n=== iteration {iteration} summary ===")
    for r in results:
        if "error" in r:
            print(f"  {r['tag']}: FAILED TO RUN — {r['error']}")
        else:
            v = r["verdict"]
            status = "PASS" if v["passed"] else f"FAIL ({'; '.join(v['violations'])})"
            print(f"  {r['tag']}: {status} — area={v['area_um2']} um^2, "
                  f"util={v['utilization']}, worst_setup_wns={v['worst_setup_wns']}")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--run-spec", required=True, type=Path,
                     help="path to a run_spec.json (candidates + targets)")
    ap.add_argument("--max-iterations", type=int, default=None,
                     help="overrides run_spec.json's max_iterations, if set")
    ap.add_argument("--max-parallel", type=int, default=1,
                    help="number of independent candidates to run concurrently")
    args = ap.parse_args()

    run_spec = json.loads(args.run_spec.read_text())
    design_name = run_spec.get("design_name", args.design.name)
    max_iterations = args.max_iterations or run_spec.get("max_iterations", 3)

    candidates = run_spec["candidates"]
    all_iterations = []
    winner = None

    iteration = 1
    while True:
        results = run_candidates(
            args.design,
            {**run_spec, "candidates": candidates},
            max_parallel=max(1, args.max_parallel),
        )
        for r in results:
            r["stage"] = classify_stage(r)
            # True when this candidate exists only because
            # propose_repairs() proposed it from a prior iteration's
            # failure — i.e. this candidate IS one firing of the
            # feedback loop, regardless of what its own run does next.
            r["produced_by_feedback"] = iteration > 1
        print_iteration_summary(iteration, results)
        all_iterations.append({"iteration": iteration, "results": results})

        winner = pick_winner(results)
        if winner:
            print(f"\nwinner found in iteration {iteration}: {winner['tag']}")
            break
        if iteration >= max_iterations:
            print(f"\nreached max_iterations ({max_iterations}) with no winner")
            break

        next_candidates = propose_repairs(results, iteration)
        if not next_candidates:
            print("\nno auto-repairable failures found — stopping "
                  "(needs placement-strategist/feedback-optimizer to propose "
                  "a genuinely new candidate set)")
            break

        print(f"\nauto-repair proposing {len(next_candidates)} candidate(s) "
              f"for iteration {iteration + 1}: "
              f"{[(c['tag'], c['overrides']) for c in next_candidates]}")
        candidates = next_candidates
        iteration += 1

    case_file = write_case(design_name, args.design, all_iterations, winner)
    print(f"\nwinner: {winner['tag'] if winner else 'none — needs a new candidate set'}")
    print(f"case written to: {case_file}")


if __name__ == "__main__":
    main()
