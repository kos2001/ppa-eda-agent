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
import math
import re
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

from run_stage import run_stage, read_metrics
from case_storage import write_case_json
import cdc_check
import def_layout
import design_rules
import equiv_check
import evaluation_budget
import evaluation_provenance
import gf180_drc
import live_events
import magic_abstract_drc
import netlist_graph
import model_validity
import operating_point
import pnr_polish
import pnr_repair
import power_activity
import render_layout
import step_coverage
import synth_explore
from pareto import ParetoPoint, pick_knee
from toolchain import classic_steps, toolchain_info
# Names below were split out of this module; they are imported (not just
# re-exported) because callers and tests reach them as orchestrator.<name>.
from candidate_plan import (  # noqa: F401
    expand_sweeps, override_value, safe_tag, validate_candidates, validate_run_plan,
)
from process_stages import (  # noqa: F401
    PROCESS_STAGES, _STAGE_ERROR_PATTERNS, classify_stage, pick_layout_subject,
)
from repair_proposals import (  # noqa: F401
    DIE_AREA_GROWTH_FACTOR, DIE_TOO_SMALL_ERROR, MIN_CORE_UTIL, PDN_STRAP_ERROR,
    SETUP_PERIOD_MARGIN, SETUP_PERIOD_STEP_NS, UTIL_STEP_DOWN, _repaired,
    propose_repairs, setup_period_repair,
)
from winner_selection import (  # noqa: F401
    OBJECTIVES, _core_area, _setup_slack, annotated_total_w, objective_table,
    pareto_points, pick_winner,
)
from verdict_scoring import SIGNOFF_METRICS, score, supply_rails, worst_setup_slack  # noqa: F401

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


def extra_lef_paths(design_dir: Path) -> list[Path]:
    """Real macro LEF paths declared in this design's config.json (the
    "MACROS" block — see docs/superpowers/specs/
    2026-08-21-autonomous-layout-agent-design.md), translated from the
    "/pdk/..." container path OpenLane's config uses back to this
    repo's real pdk/ directory, so def_layout.py can read them
    host-side without needing a container.
    """
    config_file = design_dir / "config.json"
    if not config_file.exists():
        return []
    config = json.loads(config_file.read_text(encoding="utf-8"))
    paths = []
    for macro in config.get("MACROS", {}).values():
        for lef in macro.get("lef", []):
            if lef.startswith("/pdk/"):
                paths.append(PDK_ROOT / lef[len("/pdk/"):])
            else:
                paths.append(Path(lef))
    return paths


def read_topology(design_dir: Path) -> dict | None:
    """Reads a design's topology.json (circuit-layout-extractor's real
    output — see .claude/agents/circuit-layout-extractor.md), if present.

    This was previously an orphan file: written by hand alongside each
    design but never actually read by anything in the pipeline, so the
    "Topology Understanding" step of the process had no visible artifact
    in reference-db/ or the dashboard even though the file existed.
    """
    topology_file = design_dir / "topology.json"
    if topology_file.exists():
        return json.loads(topology_file.read_text(encoding="utf-8"))
    # No hand-written file: derive one from the design's own config,
    # sources and a completed run's Yosys netlist rather than record
    # null. aes, gcd and riscv32i went 19 cases without a topology this
    # way, and the retrieval fallback that compares topologies had
    # nothing to compare. Derivation needs a run with a netlist; on the
    # first run of a brand-new design there may be none yet, and then
    # null is still the honest value.
    try:
        import topology_derive
        return topology_derive.derive(design_dir)
    except (FileNotFoundError, OSError, ValueError, KeyError):
        return None


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


def expand_synthesis_exploration(design_dir: Path, run_spec: dict) -> tuple[list[dict], dict | None]:
    """Chooses SYNTH_STRATEGY candidates by measuring, not by guessing.

    run_spec's `explore_synthesis` block replaces a hand-written strategy
    sweep. Hand-picking four of nine strategies and running each through
    the full 78-step flow costs about a minute apiece to produce an
    area-versus-slack table; OpenLane's own SynthesisExploration flow
    produces that table for all nine in 9 seconds, measured. This runs
    it, then spends the expensive full-flow runs on the ends of the
    tradeoff plus a middle.

    It does not replace the full runs. Synthesis area is not post-route
    area and a strategy that wins here can still lose after placement —
    which is why the picks still get real flows. What it replaces is
    running nine of them to discover which three were worth running.

    Returns (candidates, exploration_record). The record goes into the
    case so the choice can be audited later rather than taken on trust;
    on failure it carries the reason and the candidate list is empty, so
    a broken exploration cannot silently shrink the candidate set to
    nothing without saying why.
    """
    spec = run_spec.get("explore_synthesis")
    if not spec:
        return [], None
    count = int(spec.get("count", 3))
    base_overrides = spec.get("overrides", {})
    started = time.monotonic()
    try:
        results = synth_explore.explore(
            design_dir, tag="synth-explore", clock_period_ns=clock_period(design_dir))
    except Exception as e:  # noqa: BLE001 - recorded, not silenced
        return [], {"error": f"{type(e).__name__}: {e}",
                    "evaluation": {"kind": "synthesis_exploration", "status": "failed",
                                   "seconds": round(time.monotonic() - started, 6)}}

    picks = synth_explore.suggest_candidates(results, count)
    candidates = [{
        "tag": p["tag"],
        "overrides": {**base_overrides, **p["overrides"]},
        # Why this strategy and not the other eight, kept with the
        # candidate rather than only in a log.
        "chosen_because": p["why"],
    } for p in picks]
    return candidates, {
        "results": results,
        "chosen": [p["overrides"]["SYNTH_STRATEGY"] for p in picks],
        "evaluation": {"kind": "synthesis_exploration", "status": "completed",
                       "seconds": round(time.monotonic() - started, 6)},
        "best_area": synth_explore.rank(results, "area")[0]["strategy"],
        "best_slack": synth_explore.rank(results, "fmax")[0]["strategy"],
        "note": ("pre-PnR synthesis metrics only — post-route area and "
                 "timing can rank strategies differently, which is why "
                 "the picks still get full flows"),
    }


def verify_function(design_dir: Path, run_dir: Path, verdict: dict) -> dict | None:
    """Proves the candidate's netlist still computes the RTL's function,
    and records a real violation if it doesn't.

    Kept separate from score() because score() reads only metrics.json,
    while this needs the run's actual netlist and the liberty for the SCL
    that run really used. A functional mismatch is a hard fail regardless
    of how clean the DRC/LVS/timing came out — a wrong circuit that meets
    timing is still wrong.

    Costs about a second (measured on counter4), so it is cheap enough to
    run per candidate; it stays opt-in only so that enabling it is a
    deliberate change to what a verdict means.
    """
    try:
        result = equiv_check.check(design_dir, run_dir)
    except Exception as e:  # noqa: BLE001 — inability to check is not a pass
        verdict["violations"].append(f"function not verified: {e}")
        verdict["passed"] = False
        return None
    if not result["equivalent"]:
        verdict["violations"].append(
            f"netlist is NOT functionally equivalent to the RTL "
            f"({result['unproven_points']} unproven equivalence point(s))")
        verdict["passed"] = False
    elif result["vacuous"]:
        # A pass that compared nothing must not read as a pass.
        verdict["violations"].append(
            "function check was vacuous — no equivalence points compared")
        verdict["passed"] = False
    return result


def measure_activity_power(design_dir: Path, run_dir: Path) -> dict | None:
    """Activity-annotated power for one completed run, or None.

    None whenever the design has no testbench — the common case, and not
    an error. Passes the design's own clock port and period so OpenSTA
    constrains the same clock OpenLane did.
    """
    ports = cdc_check.declared_clock_ports(design_dir)
    if not ports:
        return None
    period = clock_period(design_dir)
    return power_activity.measure(
        design_dir, run_dir,
        clock_port=ports[0],
        clock_period=period if period else 10.0,
    )


def score_run_dir(design_dir: Path, run_dir: Path, run_spec: dict, cand: dict,
                  tag: str, scl: str | None, pdk: str | None,
                  verify_fn=None) -> dict:
    """Everything that turns a finished OpenLane run directory into a row.

    Split out of run_candidate so that scoring a run and *performing* one
    are separate concerns. An interrupted batch leaves real, complete run
    directories on disk that the collector never got to record — 83 of
    them, once — and recovering those without inventing a second,
    drifting definition of a result means handing them to the same
    function the live path uses.

    The same argument the screening code already makes about failures:
    one code path producing results rather than two.
    """
    metrics = read_metrics(run_dir)
    # OpenLane 2.3.10's KLayout.DRC step skips every PDK but sky130 and
    # writes no klayout__drc_error__count, so score() filed all 170
    # gf180mcu runs in the store as unverified. The PDK ships the deck;
    # gf180_drc.py runs it. A failure to run it leaves the metric absent
    # — still unverified, still honest — and records why.
    klayout_drc = None
    if pdk and pdk.startswith("gf180mcu") and "klayout__drc_error__count" not in metrics:
        try:
            klayout_drc = gf180_drc.run(run_dir, pdk)
            metrics = {**metrics, "klayout__drc_error__count": klayout_drc["count"]}
        except Exception as e:  # noqa: BLE001 - recorded, never a silent zero
            klayout_drc = {"error": f"{type(e).__name__}: {e}"}
            print(f"  (gf180mcu KLayout DRC not run for {tag}: {e})", file=sys.stderr)
    verdict = score(metrics, run_spec.get("targets", {}))
    if klayout_drc is not None:
        verdict["klayout_drc"] = klayout_drc
    # Clock-domain coverage needs the run's logs, which score() never
    # sees — it reads metrics.json only. Folded into the same
    # `unverified` list because an unconstrained domain is exactly
    # that: not a failure anyone found, a check nobody ran.
    clocks = cdc_check.check(design_dir, run_dir)
    verdict["unverified"] = (verdict.get("unverified", [])
                             + cdc_check.unverified_domains(clocks))
    # Whether STA was asked something its models can answer. A macro
    # liberty stops at some input slew; past that the tool
    # extrapolates and returns a number indistinguishable from a
    # measurement. sram_wrapper reports clean setup and hold with
    # addr pins sitting 22x past the last table entry.
    #
    # `unverified` rather than a violation, for the same reason as
    # the clock domains above: nobody proved the design is bad, they
    # proved nobody can say from here.
    models = model_validity.check(design_dir, run_dir)
    verdict["unverified"] += model_validity.unverified(models)
    verdict["model_validity"] = models
    if (cand.get("flow") in ("FanoutRepair", "MacroFanoutRepair")
            or (run_dir / "custom_flow_provenance.json").is_file()):
        from sta_report import repair_parasitic_audit
        audit = repair_parasitic_audit(run_dir, [c["corner"] for c in verdict["timing_corners"]])
        verdict["repair_parasitic_audit"] = audit
        if not audit["verified"]:
            verdict["unverified"].append(
                "inserted repair drivers lack complete final SPEF annotation, or the required "
                "corner reports are missing; provisional timing/DRV values cannot establish closure")
    # The violation table omits clock/data macro inputs below the DRV limit.
    # Preserve the exhaustive extra report without treating coverage as
    # Liberty range or PVT qualification.
    if list(run_dir.glob("*stapostpnr/*/macro_inputs.csv")):
        try:
            import macro_slew_audit
            cfg = json.loads((design_dir / "config.json").read_text())
            expected = {name: 57
                        for macro, spec in (cfg.get("MACROS") or {}).items()
                        if macro == "sky130_sram_1kbyte_1rw1r_32x256_8"
                        for name in spec.get("instances", {})}
            verdict["macro_input_audit"] = macro_slew_audit.read_audit(run_dir, expected)
        except (OSError, ValueError, KeyError) as exc:
            verdict["macro_input_audit"] = {"coverage_complete": False, "error": str(exc)}
    # A Magic DRC count taken on DEF/LEF abstracts (MAGIC_DRC_USE_GDS=
    # false) that is nothing but nwell.4 on standard-cell rows is a
    # check Magic could not run on geometry, not a bad layout — measured
    # on sram_wrapper, 382 of them with KLayout DRC on the GDS at zero.
    # Moved to `unverified`, which still blocks a pass.
    try:
        abstract = magic_abstract_drc.check(run_dir, PDK_ROOT)
    except Exception as e:  # noqa: BLE001 - a classifier must never lose a run
        abstract = {"abstract_artefact": False, "error": f"{type(e).__name__}: {e}"}
    magic_abstract_drc.apply_to_verdict(verdict, abstract)
    verdict["passed"] = not verdict["violations"] and not verdict["unverified"]
    # Fmax/Vmin, derived from per-corner slack the run already
    # measured. Needs the clock period the run was actually constrained
    # to — see run_clock_period() for why that is not config.json's.
    period, period_source = run_clock_period(design_dir, run_dir, cand)
    verdict["operating_point"] = operating_point.operating_point(metrics, period)
    if verdict["operating_point"] is not None:
        verdict["operating_point"]["period_source"] = period_source
    # Power measured against a real workload, when the design has a
    # testbench to provide one. score()'s figure is OpenSTA's
    # default-activity estimate, which on spm understates
    # combinational power by 44% — the tool is being asked how much
    # the design burns without being told what it is doing.
    #
    # Non-intrusive by construction: measure() returns None and
    # starts no container for the designs without a testbench, which
    # is most of them. Failures here are attached, not raised — a
    # simulation that will not compile is a fact about the
    # testbench, and it must not discard a completed OpenLane run.
    try:
        annotated = measure_activity_power(design_dir, run_dir)
    except Exception as e:  # noqa: BLE001
        annotated = {"error": f"{type(e).__name__}: {e}"}
    if annotated:
        verdict["power_activity"] = annotated

    equiv = verify_function(design_dir, run_dir, verdict) if verify_fn else None
    layout = def_layout.layout_summary(run_dir, extra_lef_paths(design_dir))
    # The gate-level circuit itself. Yosys wrote this during
    # synthesis and the pipeline recorded only its path, into runs/,
    # which is deleted — so the console could say a netlist had
    # existed without ever showing one.
    netlist = netlist_graph.summary(run_dir, run_spec.get("design_name"))
    # Which declared flow steps this run silently skipped.
    #
    # Deliberately recorded rather than folded into the verdict's
    # `unverified` list. RUN_EQY is False by default and enabling it
    # aborts inside EQY itself ("This should not happen. Please
    # report this bug."), so gating on it would mark every candidate
    # unverified forever — a gate that always fires gets switched
    # off rather than obeyed. The equivalence claim is covered by
    # this project's own equiv_check, which proves the same design
    # (4 points, 0 unproven) where EQY crashes.
    declared = classic_steps()
    coverage = step_coverage.check(run_dir, declared) if declared else None
    return {"tag": tag, "overrides": cand.get("overrides", {}),
            "scl": scl, "pdk": pdk,
            "verdict": verdict, "run_dir": str(run_dir),
            "data": data_pointers(run_dir),
            "clocks": clocks,
            "netlist": netlist,
            "step_coverage": coverage,
            "equivalence": equiv,
            "layout": layout}


def run_candidate(design_dir: Path, run_spec: dict, cand: dict,
                   verify_fn: bool = False) -> dict:
    """Runs and scores one independent candidate."""
    # Sanitised here, not only where sweeps are expanded. safe_tag was
    # applied at expand time and nowhere else, so a caller that builds
    # its own candidates — collect.py does — could hand in a tag with a
    # space in it. `SYNTH_STRATEGY` values look like "DELAY 1", and a
    # tag carrying that space wasted an entire 171-run batch: every run
    # failed, none with an error that named the tag.
    #
    # This is the second time an unsanitised tag has destroyed a batch
    # here; the first was a DIE_AREA list rendered into one.
    tag = safe_tag(cand["tag"])
    overrides = [f"{k}={override_value(v)}" for k, v in cand.get("overrides", {}).items()]
    # The standard cell library is a candidate axis, not a global. It
    # cannot be an override — OpenLane accepts STD_CELL_LIBRARY into
    # resolved.json and ignores it, so a comparison made that way reports
    # a plausible 0.00% delta (see run_stage's docstring). It goes to
    # --scl, and it is recorded on the result because nothing downstream
    # could otherwise tell two runs of the same config apart: measured on
    # counter4, hd and hs differ by 53% in area and 59% in power, and
    # surrogate.load_dataset deduplicated the pair down to one sample.
    scl = cand.get("scl")
    pdk = cand.get("pdk")
    print(f"\n=== candidate '{tag}' — overrides: {cand.get('overrides', {})}"
          f"{f', pdk: {pdk}' if pdk else ''}"
          f"{f', scl: {scl}' if scl else ''} ===", file=sys.stderr)
    # Timed here, for every caller. collect.py stamped `seconds` on its
    # own runs and nothing else did, so the store could say what a
    # counter4 candidate costs and nothing about aes — every aes case
    # came through orchestrate() or was recovered from a run directory.
    # The one design the manual's first feedback asked about was the
    # one with no number.
    # What the surrogate expects, taken before the run so it cannot be
    # informed by the result, and scored against the result afterwards.
    # Every real run thereby measures the model; the model decides
    # nothing. A prediction that cannot be made (too few runs of this
    # design, no comparable parameter) is recorded as refused, with the
    # reason. Never fatal: a broken predictor must not cost a real run.
    evaluation_started = time.monotonic()
    inputs = evaluation_provenance.capture_inputs(design_dir)
    prediction = None
    try:
        if cand.get("flow") in {"FanoutRepair", "MacroFanoutRepair"}:
            raise ValueError("Surrogate features do not describe the physical fanout repair flow")
        import surrogate
        config = json.loads((design_dir / "config.json").read_text(encoding="utf-8"))
        prediction = surrogate.predict_candidate(design_dir.name, cand, config)
    except Exception as e:  # noqa: BLE001 - recorded, never a lost run
        prediction = {"error": f"{type(e).__name__}: {e}"}
    started = evaluation_started
    stage_costs = {"preparation": round(time.monotonic() - started, 6)}
    live_events.emit(design_dir, "candidate_start", tag=tag,
                     overrides=cand.get("overrides", {}), pdk=pdk, scl=scl,
                     repair=cand.get("repair"), polish=bool(cand.get("polish")))
    try:
        flow_args = {"flow": cand["flow"]} if cand.get("flow") else {}
        flow_started = time.monotonic()
        try:
            run_dir = run_stage(design_dir, tag, to_step=None, overrides=overrides,
                                scl=scl, pdk=pdk, **flow_args)
        finally:
            stage_costs["openlane"] = round(time.monotonic() - flow_started, 6)
        assessment_started = time.monotonic()
        try:
            result = score_run_dir(design_dir, run_dir, run_spec, cand, tag,
                                   scl, pdk, verify_fn)
        finally:
            stage_costs["assessment_and_verification"] = round(time.monotonic() - assessment_started, 6)
    except Exception as e:  # noqa: BLE001 - report and keep evaluating others
        result = {"tag": tag, "overrides": cand.get("overrides", {}),
                  "scl": scl, "pdk": pdk, "error": str(e)}
    result["seconds"] = round(time.monotonic() - started, 6)
    if result.get("run_dir"):
        provenance_started = time.monotonic()
        result["evaluation_provenance"] = evaluation_provenance.complete_context(
            design_dir, Path(result["run_dir"]), cand, inputs, toolchain_info(), verify_fn,
            ((result.get("data") or {}).get("constraint_pdk") or {}).get("pdk_version"))
        stage_costs["provenance"] = round(time.monotonic() - provenance_started, 6)
        result["seconds"] = round(time.monotonic() - started, 6)
    result["stage_costs"] = stage_costs
    result["evaluation_fidelity"] = "full_flow"
    result["evaluation_inputs"] = inputs
    result["verification_requested"] = bool(verify_fn)
    if "screen_evaluation" in cand:
        result["screen_evaluation"] = cand["screen_evaluation"]
    if "scheduling" in cand:
        result["scheduling"] = cand["scheduling"]
    if cand.get("flow"):
        result["flow"] = cand["flow"]
    live_events.emit(design_dir, "candidate_done", **candidate_summary(result))
    if cand.get("repair"):
        # Why this candidate exists: the failure it repairs and the number
        # it was derived from, kept with the result so the case can be
        # audited without re-reading the previous iteration.
        result["repair"] = cand["repair"]
    if prediction is not None:
        if "error" in prediction:
            result["prediction"] = prediction
        else:
            import surrogate
            result["prediction"] = surrogate.score_prediction(prediction, result.get("verdict"))
    return result


# Cheap pre-flight cutoff, stopping just past placement/PDN. Measured on
# counter4: 10s against the full Classic flow's 64s.
#
# What screening is and is NOT for, corrected by measurement after a
# first design based on faulty reasoning. All 13 crashed candidates in
# reference-db died at step 13/78 (Floorplan) or 20/78 (GeneratePDN), so
# it looked as though an early cutoff would cheaply reproduce every
# failure this pipeline has seen. Running it proved that pointless: a
# crashing candidate ALREADY costs only ~10s, because OpenLane exits at
# the failure. Screening them saves nothing and adds a second process
# launch — measured end to end on counter4_tinydie, screening made a
# crash-heavy run *slower* (107s vs 95s). "Failures happen early" is not
# the same claim as "failures are expensive."
#
# The expensive case is the opposite one: a candidate that completes all
# 78 steps and is only then rejected on a target this pipeline set. That
# costs the full flow before revealing it was never viable. So the screen
# prunes on the early utilization metric, not on crashes.
#
# Soundness: this prunes only when the EARLY utilization already exceeds
# the target. Utilization can only grow after this point — CTS and timing
# repair add cells inside a fixed die — so an early value above target
# guarantees the final one is too. Measured on counter4: 0.3646 at the
# cutoff, 0.6042 at signoff. That direction is what makes the prune safe;
# the early number is a lower bound, never a prediction, and is never
# recorded as if it were the real result.
SCREEN_STEP = "OpenROAD.GeneratePDN"


def screen_candidates(design_dir: Path, candidates: list[dict], targets: dict,
                       max_parallel: int = 1, budget=None) -> tuple[list[dict], list[dict]]:
    """Runs each candidate only as far as SCREEN_STEP and prunes the ones
    whose early utilization already exceeds the target. Returns
    (survivors, pruned).

    A candidate that *crashes* during screening is returned as a survivor
    on purpose: it would crash identically in the full run at the same
    cost, and letting the real run record it keeps one code path
    producing failure results instead of two.
    """
    max_util = targets.get("max_core_utilization")
    if max_util is None:
        return list(candidates), []

    survivors, pruned = [], []

    def screen_one(cand: dict) -> dict:
        tag = f"{cand['tag']}-screen"
        overrides = [f"{k}={override_value(v)}"
                     for k, v in cand.get("overrides", {}).items()]
        ticket = budget.start("screen", cand["tag"]) if budget else None
        if budget and ticket is None:
            return {"cand": cand, "denied": True}
        started = time.monotonic()
        record = {"fidelity": SCREEN_STEP}
        try:
            flow_args = {"flow": cand["flow"]} if cand.get("flow") else {}
            run_dir = run_stage(design_dir, tag, to_step=SCREEN_STEP, overrides=overrides,
                                pdk=cand.get("pdk"), scl=cand.get("scl"), **flow_args)
            metrics = read_metrics(run_dir)
            early = metrics.get("design__instance__utilization__stdcell")
            record.update(status="completed", early_utilization=early, run_dir=str(run_dir))
        except Exception as error:  # preserve screening failure before full evaluation
            early = None
            record.update(status="failed", error=str(error))
        finally:
            record["seconds"] = round(time.monotonic() - started, 6)
            if budget:
                budget.finish(ticket, record["status"])
        return {"cand": {**cand, "screen_evaluation": record}, "early_util": early}

    if max_parallel <= 1 or len(candidates) <= 1:
        screened = [screen_one(c) for c in candidates]
    else:
        with ThreadPoolExecutor(max_workers=min(max_parallel, len(candidates))) as ex:
            futures = {ex.submit(screen_one, c): c for c in candidates}
            by_tag = {}
            for fut in as_completed(futures):
                by_tag[futures[fut]["tag"]] = fut.result()
            screened = [by_tag[c["tag"]] for c in candidates]

    for item in screened:
        if item.get("denied"):
            pruned.append(evaluation_budget.deferred(item["cand"], budget))
            continue
        cand, early = item["cand"], item["early_util"]
        if early is not None and early > max_util:
            pruned.append({
                "tag": cand["tag"],
                "overrides": cand.get("overrides", {}),
                "verdict": {
                    "passed": False,
                    "violations": [f"utilization {early:.3f} already > target "
                                    f"{max_util} at {SCREEN_STEP} (pruned before "
                                    f"signoff; utilization only grows after this "
                                    f"point)"],
                    "area_um2": None, "utilization": early,
                    "worst_setup_wns": 0, "timing_corners": [],
                    "power": None, "power_domain": None,
                },
                "screened_out": True,
                "screen_evaluation": cand["screen_evaluation"],
                "evaluation_fidelity": "screen",
            })
        else:
            survivors.append(cand)
    return survivors, pruned


def run_candidates(design_dir: Path, run_spec: dict,
                   max_parallel: int = 1, verify_fn: bool = False, budget=None) -> list[dict]:
    candidates = run_spec["candidates"]
    def evaluate(cand):
        ticket = budget.start("full_flow", cand["tag"]) if budget else None
        if budget and ticket is None:
            return evaluation_budget.deferred(cand, budget)
        result = None
        try:
            result = run_candidate(design_dir, run_spec, cand, verify_fn)
            return result
        finally:
            if budget:
                budget.finish(ticket, "failed" if result is None or result.get("error") else "completed")
    if max_parallel <= 1 or len(candidates) <= 1:
        return [evaluate(cand) for cand in candidates]

    results_by_tag = {}
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(candidates))) as executor:
        futures = {
            executor.submit(evaluate, cand): cand["tag"]
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


def capture_layout_image(design_name: str, design_dir: Path,
                          result: dict | None) -> str | None:
    """Renders one candidate's real GDS to a PNG stored *in reference-db*,
    returning its path relative to reference-db/ (or None).

    Why here and not on demand: `runs/` is gitignored and routinely
    deleted, so every committed case's recorded GDS path is already
    dangling — an on-demand renderer only ever works during the brief
    window a run directory still exists. soul.md calls reference-db the
    project's memory; a layout image is exactly the kind of real
    evidence that belongs in it rather than being lost with the run.
    Applies arxiv.org/html/2605.06936v3's layout-image finding to the
    *durable* record (and so to the dashboard), not just to live runs.

    One image per case (the winner, or the furthest-progressing
    candidate that produced a GDS) rather than one per candidate: each
    render is a real Docker/KLayout invocation, and passing candidates
    of the same design look near-identical, so per-candidate rendering
    would multiply run time for little added signal. Subagents needing
    a specific failed candidate's view still have render_layout.py
    against the live run directory.

    Never raises: a missing image should leave the case without one,
    not fail a run whose real EDA work already succeeded.
    """
    if result is None:
        return None
    tag = result.get("tag")
    if not tag:
        return None
    run_dir = design_dir / "runs" / tag
    if not run_dir.exists():
        return None
    rel = f"layouts/{design_name}__{date.today().isoformat()}__{tag}.png"
    try:
        render_layout.render_gds_png(run_dir, REFDB / rel)
    except Exception as e:  # no GDS yet, Docker unavailable, KLayout error
        print(f"  (no layout image for {tag}: {e})", file=sys.stderr)
        return None
    return rel


def clock_period(design_dir: Path) -> float | None:
    """The design's declared CLOCK_PERIOD, in ns.

    Returns None rather than a default when absent: Fmax is computed from
    this number, and a made-up period would produce a made-up frequency
    that looks exactly like a measured one.
    """
    cfg = design_dir / "config.json"
    if not cfg.exists():
        return None
    value = json.loads(cfg.read_text(encoding="utf-8")).get("CLOCK_PERIOD")
    return float(value) if isinstance(value, (int, float, str)) and str(value).strip() else None


def candidate_summary(result: dict) -> dict:
    """The few fields of a scored candidate worth announcing live: did it
    pass, what killed it, and the numbers a reader would ask for first."""
    v = result.get("verdict") or {}
    codes = re.findall(r"\b[A-Z]{3}-\d{4}\b", "\n".join(
        ln for ln in (result.get("error") or "").splitlines() if "WARNING" not in ln))
    return {
        "tag": result.get("tag"),
        "passed": bool(v.get("passed")),
        "died_with": codes[-1] if codes else None,
        "crashed": bool(result.get("error")),
        "violations": (v.get("violations") or [])[:4],
        "unverified": len(v.get("unverified") or []),
        "area_um2": v.get("area_um2"),
        "power_w": (v.get("power") or {}).get("total_w"),
        "setup_slack": v.get("worst_setup_slack"),
        "utilization": v.get("utilization"),
        "seconds": result.get("seconds"),
    }


def read_base_config(design_dir: Path) -> dict:
    """The design's own config.json, or {} when it cannot be read.

    Repairs consult it for the values a candidate inherited rather than
    overrode. Unreadable is not fatal: it only costs those repairs their
    fallback, which is how they behaved before it existed.
    """
    try:
        return json.loads((design_dir / "config.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def run_clock_period(design_dir: Path, run_dir: Path | None,
                     cand: dict | None) -> tuple[float | None, str]:
    """The clock period a run was actually constrained to, and where
    that number came from.

    Slack is measured against the period the tool was given, so
    min_period = period - slack is only right with *that* period. The
    operating point used config.json's CLOCK_PERIOD for every run, and
    90 of the 356 recorded operating points belonged to candidates that
    overrode it: aes at 12 ns with +0.856 ns of slack was recorded as
    Fmax 207.5 MHz (10 - 0.856 = 9.14 ns) when its critical path is
    12 - 0.856 = 11.14 ns, 89.7 MHz. Every period sweep in the store
    carried the same error, up to 2.5x on spm's 25 ns candidate.

    Precedence is what the tool used, then what we asked for, then what
    the design declares — resolved.json is OpenLane's own record of the
    configuration it ran, and recover_runs.py already trusts it over
    the tag for the same reason.
    """
    if run_dir is not None:
        try:
            resolved = json.loads((Path(run_dir) / "resolved.json")
                                  .read_text(encoding="utf-8"))
            value = resolved.get("CLOCK_PERIOD")
            if isinstance(value, (int, float)):
                return float(value), "resolved.json"
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            pass
    override = ((cand or {}).get("overrides") or {}).get("CLOCK_PERIOD")
    if isinstance(override, (int, float)):
        return float(override), "override"
    return clock_period(design_dir), "config.json"


def collect_constraints(design_dir: Path) -> dict | None:
    """The rules this run was held to, or None with the reason recorded.

    Deliberately non-fatal: a case that already cost real OpenLane time
    must not be lost because a tech LEF moved. The error is kept in the
    case rather than swallowed, so an empty constraints panel can be told
    apart from a process that genuinely has no rules.
    """
    try:
        return design_rules.collect(design_dir)
    except Exception as e:  # noqa: BLE001 - recorded, not silenced
        return {"error": f"{type(e).__name__}: {e}"}


def write_case(design_name: str, design_dir: Path, iterations: list[dict],
               winner: dict | None, stop_reason: str | None = None,
               exploration: dict | None = None,
               expected_outcome: str | None = None,
               search_plan: dict | None = None) -> Path:
    REFDB.mkdir(parents=True, exist_ok=True)
    (REFDB / "cases").mkdir(exist_ok=True)
    (REFDB / "layouts").mkdir(exist_ok=True)
    # One file per run, not per day. `{design}__{date}.json` meant a
    # second orchestrate of the same design on the same day silently
    # replaced the first — hit for real: a technology comparison
    # (counter4 tech-hd vs tech-hs) was overwritten by a later synthesis
    # sweep the same afternoon, and the only trace was the surrogate
    # dataset quietly losing rows.
    #
    # The plain `{design}__{date}.json` name is kept when it is free, so
    # every existing case file and every path recorded in index.json
    # stays valid. Only a same-day collision gets a suffix.
    case_file = REFDB / "cases" / f"{design_name}__{date.today().isoformat()}.json"
    if case_file.exists():
        stamp = datetime.now(timezone.utc).strftime("%H%M%S")
        case_file = (REFDB / "cases"
                     / f"{design_name}__{date.today().isoformat()}__{stamp}.json")
    # outcome stays as the short human-readable summary the dashboard
    # already renders; stop_reason is the machine-readable total-guard
    # value (STOP_REASONS) a caller (self_improve.py, the dashboard) can
    # branch on without parsing outcome's prose.
    outcome = {
        "evaluation_budget_exhausted": "evaluation budget exhausted before a verified winner",
        "winner_found": "passed",
        "max_iterations_reached": "no candidate met targets after all iterations",
        "no_repairable_failures": "no candidate met targets — no auto-repairable "
                                   "pattern matched, needs a human/subagent decision",
    }.get(stop_reason, "passed" if winner else "no candidate met targets after all iterations")
    subject = pick_layout_subject(iterations, winner)
    case = {
        "design": design_name,
        "date": date.today().isoformat(),
        "process_stages": PROCESS_STAGES,
        "topology": read_topology(design_dir),
        "iterations": iterations,
        "winner_tag": winner["tag"] if winner else None,
        "outcome": outcome,
        "stop_reason": stop_reason,
        # run_spec's declared intent, when it has one: "fail" marks a
        # negative control whose OPEN outcome is the design working.
        # Copied into the case so a reader of this file alone — or a
        # scan of the store — can tell that from a design that is stuck
        # (self_improve.expected_outcome()).
        "expected_outcome": expected_outcome,
        # Which toolchain produced these numbers. Recorded so two cases
        # can be compared knowingly rather than on the assumption that
        # whatever was installed at the time was the same build.
        "toolchain": toolchain_info(),
        # The rules this run was judged against — the PDK's fixed process
        # rules and the design's own chosen constraints. Recorded because
        # stage 4 is "Physical Constraint Evaluation" and, until this was
        # added, a candidate could be reported as violating a constraint
        # the reader had no way to see. Failing to collect them must not
        # lose the case, so it degrades to None with the reason attached.
        "constraints": collect_constraints(design_dir),
        # How the SYNTH_STRATEGY candidates were chosen, when they came
        # from OpenLane's SynthesisExploration rather than a hand-written
        # sweep. Recorded so the choice is auditable instead of taken on
        # trust — including which strategies were rejected.
        "synthesis_exploration": exploration,
        # Declared search method and the maximum number of initial full-flow
        # candidates. Keep experiment intent and cost bounds beside metrics.
        "search_plan": search_plan,
        "evaluation_budget": next((it["evaluation_budget"] for it in reversed(iterations)
                                   if "evaluation_budget" in it), None),
        # Real rendered layout of this case's most informative candidate,
        # stored under reference-db/ so it outlives the run directory.
        "layout_image": capture_layout_image(design_name, design_dir, subject),
        "layout_image_tag": subject["tag"] if subject else None,
    }
    write_case_json(case_file, case)
    try:
        import evaluation_archive
        evaluation_archive.refresh(REFDB)
    except Exception as error:
        print(f"  (archive refresh deferred: {error})", file=sys.stderr)

    index_file = REFDB / "index.json"
    index = json.loads(index_file.read_text(encoding="utf-8")) if index_file.exists() else {}
    existing = index.get(design_name, [])
    # A rerun on the same day overwrites case_file in place (same name) —
    # don't duplicate the index entry for it.
    if case_file.name not in existing:
        existing.append(case_file.name)
    index[design_name] = existing
    index_file.write_text(json.dumps(index, indent=2), encoding="utf-8")
    return case_file


def print_iteration_summary(iteration: int, results: list[dict]) -> None:
    print(f"\n=== iteration {iteration} summary ===")
    for r in results:
        if r.get("not_evaluated"):
            print(f"  {r['tag']}: NOT EVALUATED — {r.get('budget_exhausted')}")
        elif "error" in r:
            print(f"  {r['tag']}: FAILED TO RUN — {r['error']}")
        else:
            v = r["verdict"]
            status = "PASS" if v["passed"] else f"FAIL ({'; '.join(v['violations'])})"
            print(f"  {r['tag']}: {status} — area={v['area_um2']} um^2, "
                  f"util={v['utilization']}, worst_setup_wns={v['worst_setup_wns']}")


# Every orchestrate() run stops for exactly one of these reasons — a
# total guard (graph-engineering sense: every exit matches exactly one
# guard, never zero — a silent fallthrough — and never two — an
# ambiguous transition). Previously this reasoning only ever reached a
# print() statement; write_case() now records it, so a case's
# reference-db JSON — and the dashboard — can say *which* guard fired,
# not just "passed" vs. a single generic failure string that collapsed
# "ran out of iteration budget" and "no repair pattern matched" into one
# unreadable outcome. See docs/superpowers/specs/
# 2026-08-21-autonomous-layout-agent-design.md's "Graph engineering"
# section (github.com/topics/graph-engineering — RonMizrahi/
# sdlc-graph-engineering's "total guards" + "the ledger is the
# load-bearing part" principles, applied here without adopting that
# project's plugin/graph-file machinery this pipeline doesn't need).
STOP_REASONS = ("winner_found", "max_iterations_reached", "no_repairable_failures",
                "evaluation_budget_exhausted")


def polish_moves(spec) -> list[dict] | None:
    """The moves a run_spec's "polish" block asks for.

    `true` or a block without "moves" means the measured defaults
    (pnr_polish.MOVES); a list of ids selects among them; a list of
    objects supplies moves of its own. An unknown id is an error, not a
    silent no-op: a typo that quietly skipped the polish would read as
    "polished, nothing to gain".
    """
    wanted = spec.get("moves") if isinstance(spec, dict) else None
    if wanted is None:
        return None
    known = {m["id"]: m for m in pnr_polish.MOVES}
    out = []
    for item in wanted:
        if isinstance(item, str):
            if item not in known:
                raise ValueError(f"unknown polish move {item!r}; known: {sorted(known)}")
            out.append(known[item])
        else:
            out.append({"id": item["id"], "overrides": item["overrides"],
                        "why": item.get("why", "run_spec-supplied move")})
    return out


def polish_winner(design_dir: Path, run_spec: dict, winner: dict,
                  base_config: dict | None, max_parallel: int = 1,
                  verify_fn: bool = False, budget=None) -> tuple[dict, list[dict]]:
    """Tries the polish moves on a winner; returns (winner, trials).

    See pnr_polish for what is tried and why a move is accepted. Trials
    are ordinary candidates run through the ordinary flow and signoff."""
    moves = pnr_polish.plan(winner["overrides"], base_config,
                            polish_moves(run_spec["polish"]))

    def run(cands: list[dict]) -> list[dict]:
        return run_candidates(design_dir, {**run_spec, "candidates": cands},
                              max_parallel=max_parallel, verify_fn=verify_fn,
                              **({"budget": budget} if budget else {}))

    return pnr_polish.polish(
        winner, moves, run, objective_table,
        notify=lambda kind, info: live_events.emit(design_dir, kind, **info))


def orchestrate(design_dir: Path, run_spec: dict, max_iterations: int,
                 max_parallel: int = 1, screen: bool = False,
                 verify_fn: bool = False) -> tuple[list[dict], dict | None, str, dict | None]:
    """Runs the full candidate-generation-and-auto-repair loop for one
    design. Returns (all_iterations, winner, stop_reason, exploration) —
    stop_reason is always exactly one of STOP_REASONS, never None (see
    above), and exploration is the synthesis-exploration record when
    run_spec asked for one.

    The exploration record travels in the return value rather than on the
    function object. It was briefly stashed as `orchestrate.last_exploration`
    to avoid widening this tuple, which is module-global mutable state
    surviving between calls — the "state drift" failure mode, and a real
    bug waiting for the first caller that runs two designs in one
    process.

    `screen` runs each candidate to SCREEN_STEP first and only pays for
    the full flow on survivors (see screen_candidates()).
    """
    # Fail malformed, colliding, duplicate, or over-budget plans before the
    # synthesis exploration can spend tool time.
    validate_run_plan(run_spec, max_iterations)
    limits = run_spec.get("evaluation_budget")
    budget = evaluation_budget.EvaluationBudget(limits) if limits else None
    explored_candidates, exploration = [], None
    if run_spec.get("explore_synthesis") and budget:
        ticket = budget.start("synthesis_exploration", "synth-explore")
        if ticket is None:
            exploration = {"not_evaluated": True, "budget_exhausted": True}
        else:
            try:
                explored_candidates, exploration = expand_synthesis_exploration(design_dir, run_spec)
            finally:
                budget.finish(ticket, "failed" if exploration is None or exploration.get("error") else "completed")
            exploration["evaluation"] = dict(ticket)
    else:
        explored_candidates, exploration = expand_synthesis_exploration(design_dir, run_spec)
    candidates = validate_candidates(
        run_spec.get("candidates", []) + expand_sweeps(run_spec)
        + explored_candidates,
        run_spec.get("candidate_budget"),
    )
    if not candidates and budget and budget.snapshot()["not_evaluated"]:
        return [{"iteration": 1, "results": [], "evaluation_budget": budget.snapshot()}], None, "evaluation_budget_exhausted", exploration
    if not candidates:
        detail = exploration.get("error") if exploration else "no candidates"
        raise ValueError(f"candidate generation produced no candidates: {detail}")
    all_iterations = []
    winner = None
    stop_reason = None
    base_config = read_base_config(design_dir)
    live_events.emit(design_dir, "run_start", design=design_dir.name,
                     max_iterations=max_iterations,
                     polish=bool(run_spec.get("polish")), screen=screen)

    iteration = 1
    while True:
        if (run_spec.get("search") or {}).get("evaluation_order") == "measured_cost":
            import evaluation_archive
            import evaluation_scheduler
            report = evaluation_archive.build_report(REFDB)
            candidates = evaluation_scheduler.order(candidates, design_dir.name, toolchain_info(), report["cost_cohorts"],
                                                     evaluation_provenance.capture_inputs(design_dir), verify_fn)
        screened_out = []
        to_run = candidates
        live_events.emit(design_dir, "iteration_start", iteration=iteration,
                         candidates=[c["tag"] for c in candidates])
        if screen:
            to_run, screened_out = screen_candidates(
                design_dir, candidates, run_spec.get("targets", {}),
                max_parallel=max(1, max_parallel), **({"budget": budget} if budget else {}))
            print(f"\nscreen ({SCREEN_STEP}): {len(to_run)} of "
                  f"{len(candidates)} candidate(s) survive", file=sys.stderr)
        results = run_candidates(
            design_dir,
            {**run_spec, "candidates": to_run},
            max_parallel=max(1, max_parallel),
            verify_fn=verify_fn,
            **({"budget": budget} if budget else {}),
        )
        # Pruned candidates are real, measured rejections and belong in
        # the iteration's results exactly like any other — dropping them
        # would understate what was tried and break auto-repair coverage
        # accounting. They also stay repairable: their verdict carries a
        # real utilization violation, which propose_repairs() acts on.
        results = results + screened_out
        for r in results:
            if not r.get("not_evaluated") or r.get("screen_evaluation"):
                r["stage"] = classify_stage(r)
            # True when this candidate exists only because
            # propose_repairs() proposed it from a prior iteration's
            # failure — i.e. this candidate IS one firing of the
            # feedback loop, regardless of what its own run does next.
            r["produced_by_feedback"] = iteration > 1
        print_iteration_summary(iteration, results)
        all_iterations.append({"iteration": iteration, "results": results})
        if budget:
            all_iterations[-1]["evaluation_budget"] = budget.snapshot()

        winner = pick_winner(results)
        if winner:
            print(f"\nwinner found in iteration {iteration}: {winner['tag']}")
            stop_reason = "winner_found"
            if run_spec.get("polish"):
                winner, trials = polish_winner(
                    design_dir, run_spec, winner, base_config,
                    max_parallel=max(1, max_parallel), verify_fn=verify_fn,
                    **({"budget": budget} if budget else {}))
                if trials:
                    for r in trials:
                        if not r.get("not_evaluated") or r.get("screen_evaluation"):
                            r["stage"] = classify_stage(r)
                        r["produced_by_feedback"] = False
                    print_iteration_summary(iteration + 1, trials)
                    all_iterations.append({"iteration": iteration + 1,
                                           "polish": True, "results": trials})
                    print(f"\nafter polish the winner is: {winner['tag']}")
                if budget:
                    all_iterations[-1]["evaluation_budget"] = budget.snapshot()
            break
        if budget and budget.snapshot()["not_evaluated"]:
            stop_reason = "evaluation_budget_exhausted"
            break
        if iteration >= max_iterations:
            print(f"\nreached max_iterations ({max_iterations}) with no winner")
            stop_reason = "max_iterations_reached"
            break

        next_candidates = propose_repairs(results, iteration, base_config)
        if not next_candidates:
            print("\nno auto-repairable failures found — stopping "
                  "(needs placement-strategist/feedback-optimizer to propose "
                  "a genuinely new candidate set)")
            stop_reason = "no_repairable_failures"
            break

        by_tag = {r["tag"]: r for r in results}
        for c in next_candidates:
            parent = by_tag.get(c["tag"].rsplit(f"-iter{iteration}", 1)[0], {})
            old = parent.get("overrides", {})
            live_events.emit(
                design_dir, "repair", from_tag=parent.get("tag"), to_tag=c["tag"],
                code=(c.get("repair") or {}).get("code", "pattern"),
                why=(c.get("repair") or {}).get("why"),
                changes={k: [old.get(k), v] for k, v in c["overrides"].items()
                         if old.get(k) != v})
        print(f"\nauto-repair proposing {len(next_candidates)} candidate(s) "
              f"for iteration {iteration + 1}: "
              f"{[(c['tag'], c['overrides']) for c in next_candidates]}")
        candidates = next_candidates
        iteration += 1

    assert stop_reason in STOP_REASONS, f"ungated exit: {stop_reason!r}"
    live_events.emit(design_dir, "stop", reason=stop_reason,
                     winner=winner["tag"] if winner else None)
    return all_iterations, winner, stop_reason, exploration


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True, type=Path)
    ap.add_argument("--run-spec", required=True, type=Path,
                     help="path to a run_spec.json (candidates + targets)")
    ap.add_argument("--max-iterations", type=int, default=None,
                     help="overrides run_spec.json's max_iterations, if set")
    ap.add_argument("--max-parallel", type=int, default=1,
                    help="number of independent candidates to run concurrently")
    ap.add_argument("--verify-function", action="store_true",
                     help="prove each candidate's netlist is functionally "
                          "equivalent to the RTL (Yosys SAT equivalence, ~1s per "
                          "candidate); a mismatch fails the candidate outright")
    ap.add_argument("--polish", action="store_true",
                     help="after a candidate passes, try the measured P&R moves "
                          "(pnr_polish.MOVES) on it and keep those that make it "
                          "strictly better; same as a run_spec \"polish\" block")
    ap.add_argument("--screen", action="store_true",
                     help=f"pre-flight each candidate only to {SCREEN_STEP} and "
                          f"run the full flow only on survivors; wins when "
                          f"failures are common (see screen_candidates())")
    ap.add_argument("--validate-only", action="store_true",
                    help="validate and print the bounded candidate plan without "
                         "running synthesis, Docker, or OpenLane")
    args = ap.parse_args()

    run_spec = json.loads(args.run_spec.read_text(encoding="utf-8"))
    design_name = run_spec.get("design_name", args.design.name)
    max_iterations = (args.max_iterations if args.max_iterations is not None
                      else run_spec.get("max_iterations", 3))
    if args.polish and not run_spec.get("polish"):
        run_spec["polish"] = True

    search_plan = validate_run_plan(run_spec, max_iterations)
    if args.validate_only:
        print(json.dumps(search_plan, indent=2, sort_keys=True))
        return

    all_iterations, winner, stop_reason, exploration = orchestrate(
        args.design, run_spec, max_iterations, args.max_parallel,
        screen=args.screen,
        verify_fn=args.verify_function,
    )

    case_file = write_case(design_name, args.design, all_iterations, winner,
                            stop_reason,
                            exploration=exploration,
                            expected_outcome=run_spec.get("expected_outcome"),
                            search_plan=search_plan)
    print(f"\nwinner: {winner['tag'] if winner else 'none — needs a new candidate set'}")
    print(f"stop reason: {stop_reason}")
    print(f"case written to: {case_file}")


if __name__ == "__main__":
    main()
