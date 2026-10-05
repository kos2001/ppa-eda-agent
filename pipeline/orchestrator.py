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
    if result.get("evaluation_fidelity") == "screen":
        return "physical_constraint"
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


# The signoff checks a verdict is built from, paired with the label used
# both when the count is nonzero ("3 KLayout DRC error(s)") and when the
# metric is absent entirely ("KLayout DRC error(s) — never checked").
#
# Every entry is a metric OpenLane's own library marks critical=True, so
# the verdict agrees with the tool it trusts rather than a hand-picked
# list. Deliberately excludes lint *warnings* and clock skew: real
# signals, but not pass/fail ones, and promoting a warning to a failure
# would be overreach.
SIGNOFF_METRICS = (
    ("magic__drc_error__count", "Magic DRC error(s)"),
    ("klayout__drc_error__count", "KLayout DRC error(s)"),
    ("design__lvs_error__count", "LVS error(s)"),
    ("design__instance_unmapped__count", "unmapped instance(s) after synthesis"),
    ("design__xor_difference__count", "XOR difference(s) between tool GDS outputs"),
    ("magic__illegal_overlap__count", "illegal layout overlap(s) (Magic)"),
    ("route__drc_errors", "routing DRC error(s)"),
    ("design__lvs_device_difference__count", "LVS device difference(s)"),
    ("design__lvs_net_difference__count", "LVS net difference(s)"),
    ("design__lvs_property_fail__count", "LVS property failure(s)"),
    ("design__lvs_unmatched_device__count", "LVS unmatched device(s)"),
    ("design__lvs_unmatched_net__count", "LVS unmatched net(s)"),
    ("design__lvs_unmatched_pin__count", "LVS unmatched pin(s)"),
    ("design__disconnected_pin__count", "disconnected pin(s)"),
    ("timing__setup_vio__count", "setup timing violation(s)"),
    ("timing__hold_vio__count", "hold timing violation(s)"),
    ("route__antenna_violation__count", "routing antenna violation(s)"),
    ("design__power_grid_violation__count", "power-grid violation(s)"),
    ("design__max_slew_violation__count", "max-slew (DRV) violation(s)"),
    ("design__max_cap_violation__count", "max-capacitance (DRV) violation(s)"),
    ("design__max_fanout_violation__count", "max-fanout (DRV) violation(s)"),
    ("synthesis__check_error__count", "synthesis check error(s)"),
    ("design__lint_error__count", "RTL lint error(s)"),
)


def supply_rails(metrics: dict) -> list[dict]:
    """Per-supply-net IR drop, from OpenLane's own power-grid analysis.

    Reads `design_powergrid__drop__worst__net:<net>` and the matching
    `..._voltage__worst__net:<net>`, deliberately ignoring the
    `drop__average__net:` keys — for VPWR that key holds 1.79999 on a
    1.8 V rail, i.e. a voltage rather than a drop, and building a gate on
    a metric whose meaning has to be guessed is how fabricated numbers
    get into a verdict.

    Nominal is derived from the pair rather than assumed: worst voltage
    plus worst drop is the rail's nominal (1.79991 + 0.0000902 = 1.8 on a
    real run). Ground nets sit at 0 V nominal, where a percentage is
    meaningless, so drop_pct is left None and the absolute bounce is
    still reported.
    """
    prefix = "design_powergrid__drop__worst__net:"
    rails = []
    for key in sorted(metrics):
        if not key.startswith(prefix) or "__corner:" in key:
            continue
        net = key[len(prefix):]
        drop = metrics[key]
        volt = metrics.get(f"design_powergrid__voltage__worst__net:{net}")
        nominal = None
        pct = None
        if isinstance(drop, (int, float)) and isinstance(volt, (int, float)):
            nominal = volt + drop
            # A ground rail reports its bounce as both drop and voltage,
            # so nominal comes out at twice the bounce — near zero, not a
            # supply. Percentages against it would be nonsense.
            if nominal > 0.1:
                pct = 100.0 * drop / nominal
        rails.append({
            "net": net,
            "drop_worst_v": drop,
            "voltage_worst_v": volt,
            "nominal_v": nominal,
            "drop_pct": pct,
        })
    return rails


def worst_setup_slack(metrics: dict) -> float | None:
    """Worst setup slack over every analysed corner, in ns (positive is
    margin). None when the run reported none — never a default."""
    corner = [v for k, v in metrics.items()
              if k.startswith("timing__setup__ws__corner:")
              and isinstance(v, (int, float))]
    if corner:
        return min(corner)
    value = metrics.get("timing__setup__ws")
    return value if isinstance(value, (int, float)) else None


def score(metrics: dict, targets: dict) -> dict:
    """Checks a real metrics.json against run_spec targets.

    Returns {"passed": bool, "violations": [...], "area": float}.
    Every field read here is a real OpenLane metric key — see
    docs/superpowers/specs/2026-08-21-autonomous-layout-agent-design.md
    for why we trust metrics.json rather than re-deriving PPA ourselves.
    """
    violations = []
    unverified = []

    # Signoff gates OpenLane computes and this verdict was ignoring.
    #
    # An audit of a real completed run found OpenLane emitting 279
    # metrics of which score() read 32 — and among the 247 discarded were
    # these, every one a genuine pass/fail signal the pipeline claims to
    # care about. The most consequential is klayout__drc_error__count: a
    # SECOND, independent DRC signoff. Only Magic's was checked, so a
    # candidate that KLayout flagged and Magic did not would have been
    # reported PASS.
    #
    # Antenna violations are a real manufacturing failure, not a warning.
    # The max_slew/max_cap/max_fanout counts are the same DRV family that
    # produces RSZ-0090 — the failure mode this project has spent the most
    # effort diagnosing — and they were sitting in metrics.json as
    # structured numbers the whole time.
    #
    # Only hard, unambiguous failure counts are gated here. Lint
    # *warnings* and clock skew are deliberately not: they are real
    # signals but not pass/fail ones, and turning a warning into a
    # failure would be overreach.
    # OpenLane's own metric library marks a specific set of metrics
    # `critical=True` — its own declaration of what constitutes a fatal
    # result. Gating on that list rather than a hand-picked one makes the
    # verdict agree with the tool it trusts, instead of guessing which
    # failures matter. Extracted from
    # openlane/common/metrics/library.py in the pinned image.
    # A missing metric used to read as a pass.
    #
    # The loop below was `count = metrics.get(key); if count:` — so a
    # check that never ran scored identically to a check that ran clean.
    # That is reachable, not hypothetical: OpenLane 2 skips steps via the
    # flow CLI (`--skip`, `--to`), and this project has already done it
    # deliberately (`--skip OpenROAD.RepairAntennas` while chasing
    # sram_wrapper). Demonstrated directly by stopping a real run at
    # OpenROAD.STAPostPNR, one step before the DRC/LVS/XOR block: none of
    # those metrics exist, and the old code called it PASS.
    #
    # A completed run really does emit all of these — audited against a
    # full counter4_tinydie signoff, which produced 281 metrics including
    # every key below with value 0. So absence means the step did not
    # run, and requiring presence cannot false-alarm on a good run.
    #
    # Absence is tracked separately from a nonzero count rather than
    # folded into violations. "Found 3 DRC errors" and "never checked
    # DRC" both block a pass, but they are different facts and a reader
    # needs to tell them apart — the same distinction this pipeline draws
    # between a measured limit and an assumed one.
    # The same facts, one row per check, so a reader can see all 23 at
    # once — clean, violated, or never run — instead of reconstructing
    # the clean ones as "whatever is in neither list". count is None
    # when the check never ran; that is the only way None appears.
    signoff_checks = []
    for key, label in SIGNOFF_METRICS:
        count = metrics.get(key)
        if count is None:
            unverified.append(label)
        elif count:
            violations.append(f"{count} {label}")
        signoff_checks.append({"key": key, "label": label, "count": count})

    max_util = targets.get("max_core_utilization")
    util = metrics.get("design__instance__utilization__stdcell")
    if max_util is not None and util is not None and util > max_util:
        violations.append(f"utilization {util:.3f} > target {max_util}")

    # Worst setup slack across corners; OpenLane emits one WNS key per
    # corner (timing__setup__wns__corner:<name>) — a negative value on
    # any of them is a real timing violation at that corner.
    setup_wns_keys = [k for k in metrics if k.startswith("timing__setup__wns__corner:")]
    # A source with no corner breakdown (iEDA reports one liberty set,
    # per clock — see ieda_metrics.py) carries only the design-level
    # key, which OpenLane also emits. Read it when the corners are
    # absent; never let it override them when they are present.
    if setup_wns_keys:
        worst_wns = min(metrics[k] for k in setup_wns_keys)
    else:
        worst_wns = metrics.get("timing__setup__wns", 0)
    if worst_wns < 0:
        violations.append(f"worst setup WNS {worst_wns} (timing violation)")

    # Hold. This was recorded per corner and displayed on the dashboard
    # but never gated on, so a candidate with a real hold violation was
    # reported PASS while showing the negative slack on screen —
    # demonstrated directly with hold_wns -0.25 and 7 hold violations
    # scoring as a pass. Hold violations are silicon-fatal and cannot be
    # fixed after fabrication, which makes this the worst thing the
    # verdict could have been silent about.
    hold_wns_keys = [k for k in metrics if k.startswith("timing__hold__wns__corner:")]
    if hold_wns_keys:
        worst_hold = min(metrics[k] for k in hold_wns_keys)
    else:
        worst_hold = metrics.get("timing__hold__wns", 0)
    if worst_hold < 0:
        violations.append(f"worst hold WNS {worst_hold} (hold violation)")

    # Every real timing corner OpenLane actually analyzed (typically 9:
    # {min,nom,max} x {ff_n40C_1v95, tt_025C_1v80, ss_100C_1v60}), setup
    # and hold WNS for each — not just the single worst value, so the
    # dashboard can show real per-PVT-corner timing instead of one number.
    timing_corners = []
    for key in setup_wns_keys:
        corner = key[len("timing__setup__wns__corner:"):]
        hold_key = f"timing__hold__wns__corner:{corner}"
        timing_corners.append({
            "corner": corner,
            "setup_wns": metrics[key],
            "hold_wns": metrics.get(hold_key),
        })
    timing_corners.sort(key=lambda c: c["corner"])

    # Real power, still OpenLane's own default/vectorless estimate:
    # score() reads metrics.json, and OpenSTA computed these numbers from
    # a default toggle rate rather than from a workload. Real computed
    # values, not fabricated — but an estimate.
    #
    # The activity-annotated measurement now lives beside it, under the
    # verdict's `power_activity` key, put there by run_candidate()
    # because it needs the design and the run directory that score()
    # never sees. The two are not interchangeable: on spm the same
    # netlist reads 1.33e-03 W here against 1.53e-03 W measured, with
    # combinational power understated by 44%. Anything comparing
    # candidates must pick one basis for all of them — see pick_winner().
    #
    # Followed by real IR-drop/power-grid numbers from the actual PDN
    # OpenROAD generated.
    power = None
    if "power__total" in metrics:
        power = {
            "internal_w": metrics.get("power__internal__total"),
            "leakage_w": metrics.get("power__leakage__total"),
            "switching_w": metrics.get("power__switching__total"),
            "total_w": metrics.get("power__total"),
        }
    power_domain = None
    if "ir__voltage__worst" in metrics:
        power_domain = {
            "ir_drop_avg_v": metrics.get("ir__drop__avg"),
            "ir_drop_worst_v": metrics.get("ir__drop__worst"),
            "voltage_worst_v": metrics.get("ir__voltage__worst"),
            # Per supply net, which is the actual power-domain view and
            # was being thrown away: OpenLane emits
            # design_powergrid__drop__worst__net:<net> for each net it
            # analysed (VPWR, VGND, and a macro's own vccd1/vssd1 once it
            # is hooked into the grid), and score() collapsed all of them
            # into one global worst number. A design whose macro domain
            # droops badly while the core domain is fine looked identical
            # to one where everything was fine.
            "supplies": supply_rails(metrics),
        }
    # IR drop is a real signoff criterion — enough droop and the cells
    # miss the timing the corner libraries promise — but what counts as
    # too much is a design decision, not a universal constant. So it is
    # gated only when the spec says so, rather than against a number this
    # pipeline invented.
    max_ir_pct = targets.get("max_ir_drop_pct")
    if max_ir_pct is not None:
        for rail in (power_domain or {}).get("supplies", []):
            if rail["drop_pct"] is not None and rail["drop_pct"] > max_ir_pct:
                violations.append(
                    f"IR drop {rail['drop_pct']:.2f}% on {rail['net']} "
                    f"> target {max_ir_pct}%"
                )

    return {
        # An unverified check blocks a pass as firmly as a failed one:
        # "we did not look" is not evidence of clean silicon. Kept as a
        # separate field so the console can say which it was.
        "passed": not violations and not unverified,
        "violations": violations,
        "unverified": unverified,
        "signoff_checks": signoff_checks,
        # Which tool's numbers these are. OpenLane's metrics.json has no
        # such key; a second source (ieda_metrics.py) sets it.
        "metrics_source": metrics.get("metrics__source", "OpenLane metrics.json"),
        "area_um2": metrics.get("design__instance__area"),
        "utilization": util,
        "worst_setup_wns": worst_wns,
        # What placement and routing actually produced, recorded beside
        # the pass/fail. worst_setup_wns above is OpenSTA's *negative*
        # slack, clipped at 0, so it is 0 for every passing candidate and
        # says nothing about margin; the slack itself lives in
        # timing__setup__ws. The core area is the silicon the floorplan
        # costs, which instance area cannot see: 290 um^2 of cells in a
        # 631 um^2 core and in a 480 um^2 core are the same "area" here.
        "worst_setup_slack": worst_setup_slack(metrics),
        "core_area_um2": metrics.get("design__core__area"),
        "wirelength_um": metrics.get("route__wirelength"),
        "via_count": metrics.get("route__vias"),
        "timing_corners": timing_corners,
        "power": power,
        "power_domain": power_domain,
    }


def override_value(v) -> str:
    """Formats a config override for OpenLane's `--override-config KEY=VALUE`.

    Numbers: plain JSON (e.g. 35 -> "35"). Lists (e.g. DIE_AREA): a bare
    comma-joined list with no brackets/spaces — discovered the hard way
    (see reference-db/cases/counter4_tinydie__2026-08-21.json): passing
    a real JSON array literal like "[0, 0, 8, 8]" makes OpenLane's CLI
    parser mis-split it and error on a phantom variable 'DIE_AREA[0]'
    with value '[0'. Its List[Decimal]-typed variables want the elements
    directly, comma-separated, no brackets. Strings (e.g. SYNTH_STRATEGY
    "AREA 0"): the bare literal value, NOT JSON-quoted — also discovered
    the hard way (see reference-db/cases/counter4__2026-08-22.json):
    json.dumps("AREA 0") -> '"AREA 0"' (with literal quote characters)
    fails OpenLane's Literal-type validation, which compares against the
    bare enum strings and doesn't strip surrounding quotes.
    """
    if isinstance(v, list):
        return ",".join(json.dumps(x) for x in v)
    if isinstance(v, str):
        return v
    return json.dumps(v)


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


def safe_tag(raw: str) -> str:
    """A sweep value turned into a name safe as a directory and a CLI arg.

    Replacing spaces was not enough, and the gap cost a whole collection
    run. A list value stringifies to "[0, 0, 64, 64]", which became
    "[0,_0,_64,_64]" — brackets and commas intact. Every die size from
    48 µm up then failed with ODB-0307 ("guides file could not be read"),
    including 64 µm, which had passed minutes earlier under the tag
    `cand-die8-iter1-iter2-iter3`. Same design, same size, different tag,
    different outcome.

    Nine runs of apparently real failure data were produced that way, and
    they were not data at all. So this allows exactly one alphabet —
    alphanumerics, dash, underscore, dot — rather than removing the
    characters that have bitten so far, and collapses runs of anything
    else into a single dash.
    """
    # Every unsafe run becomes a single underscore. Underscore rather
    # than dash because that is what spaces already mapped to, and tags
    # are recorded in reference-db: remapping an existing value would
    # make a rerun of the same sweep produce a different tag from the
    # historical one and quietly break comparison against it.
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", str(raw))
    # "data-die-" + "[0, 0, 64, 64]" leaves "-_" where the bracket was;
    # harmless but it reads as a typo in a directory listing.
    cleaned = re.sub(r"[-_]{2,}", "_", cleaned)
    return cleaned.strip("-_") or "tag"


def expand_sweeps(run_spec: dict) -> list[dict]:
    """Expands run_spec.json's optional "sweeps" into concrete candidates.

    Inspired by the OpenROAD Project's own AutoTuner
    (github.com/The-OpenROAD-Project/OpenROAD-flow-scripts,
    tools/AutoTuner) — a real, actively maintained parameter-sweep/
    hyperparameter-optimization tool for exactly this flow, built on
    Ray + hyperopt for genetic/Bayesian search over large parameter
    spaces. Deliberately NOT pulling in that dependency here: this
    pipeline's candidate counts are small (single digits, see every
    reference-db/cases/*.json so far) and its repair loop already does
    the "improve based on feedback" job AutoTuner's search does at
    scale — Ray/hyperopt would be substantial, untested new machinery
    for a problem this pipeline doesn't have yet. What's genuinely worth
    borrowing is the *shape*: declaring "sweep this parameter across
    these values" instead of hand-listing every candidate. That's what
    this function does, in ~15 lines, no new dependency.

    A sweep entry: {"param": "FP_CORE_UTIL", "values": [30, 40, 50],
    "tag_prefix": "sweep-util"} expands to one candidate per value,
    merged with any base "overrides" the entry also specifies (e.g. to
    sweep FP_CORE_UTIL within an already-fixed DIE_AREA).
    """
    sweeps = run_spec.get("sweeps", [])
    if not isinstance(sweeps, list):
        raise ValueError("run_spec 'sweeps' must be a list")

    expanded = []
    for index, sweep in enumerate(sweeps):
        where = f"sweeps[{index}]"
        if not isinstance(sweep, dict):
            raise ValueError(f"{where} must be an object")
        param = sweep.get("param")
        if not isinstance(param, str) or not param.strip():
            raise ValueError(f"{where}.param must be a non-empty string")
        values = sweep.get("values")
        if not isinstance(values, list) or not values:
            raise ValueError(f"{where}.values must be a non-empty list")
        base_overrides = sweep.get("overrides", {})
        if not isinstance(base_overrides, dict):
            raise ValueError(f"{where}.overrides must be an object")
        tag_prefix = sweep.get("tag_prefix", param)
        if not isinstance(tag_prefix, str) or not tag_prefix.strip():
            raise ValueError(f"{where}.tag_prefix must be a non-empty string")
        for value in values:
            # Run tags become real directory names (runs/<tag>/) passed to
            # OpenLane's --run-tag. A space in that name breaks OpenLane's
            # own internal subprocess invocations in a real, reproducible
            # way — found by hitting it directly: the exact same
            # SYNTH_STRATEGY="AREA 0" override succeeds standalone but
            # fails with a phantom "1 Lint errors found" (Verilator
            # resolving a stray sky130_fd_sc_hd__udp_pwrgood_pp$PG
            # reference from what looks like a stale/wrong temp file)
            # purely because the run tag was "sweep-synth-AREA 0" instead
            # of a space-free string — confirmed by rerunning with only
            # the tag changed. Sanitize here rather than assume every
            # sweep value is filesystem/CLI-safe.
            tag = safe_tag(f"{tag_prefix}-{value}")
            expanded.append({
                "tag": tag,
                "overrides": {**base_overrides, param: value},
            })
    return expanded


def validate_candidates(candidates: list[dict],
                        candidate_budget: int | None = None) -> list[dict]:
    """Rejects plans that would waste or corrupt real flow runs."""
    if not isinstance(candidates, list):
        raise ValueError("run_spec 'candidates' must be a list")
    if candidate_budget is not None:
        if (isinstance(candidate_budget, bool)
                or not isinstance(candidate_budget, int)
                or candidate_budget < 1):
            raise ValueError("candidate_budget must be a positive integer")
        if len(candidates) > candidate_budget:
            raise ValueError(
                f"candidate plan has {len(candidates)} candidates, exceeding "
                f"candidate_budget={candidate_budget}")

    tags: dict[str, str] = {}
    configs: dict[str, str] = {}
    normalized = []
    for index, candidate in enumerate(candidates):
        where = f"candidates[{index}]"
        if not isinstance(candidate, dict):
            raise ValueError(f"{where} must be an object")
        raw_tag = candidate.get("tag")
        if not isinstance(raw_tag, str) or not raw_tag.strip():
            raise ValueError(f"{where}.tag must be a non-empty string")
        overrides = candidate.get("overrides", {})
        if not isinstance(overrides, dict):
            raise ValueError(f"{where}.overrides must be an object")
        if candidate.get("flow") not in (None, "Classic", "MacroSignoff", "UpstreamClassic", "FanoutRepair", "MacroFanoutRepair"):
            raise ValueError(f"{where}.flow is unsupported: {candidate['flow']!r}")

        tag = safe_tag(raw_tag)
        if tag in tags:
            raise ValueError(
                f"candidate tags {tags[tag]!r} and {raw_tag!r} both map to "
                f"run directory {tag!r}")
        tags[tag] = raw_tag

        identity = json.dumps({
            "overrides": overrides,
            "pdk": candidate.get("pdk"),
            "scl": candidate.get("scl"),
            "flow": candidate.get("flow"),
        }, sort_keys=True, separators=(",", ":"))
        if identity in configs:
            raise ValueError(
                f"candidates {configs[identity]!r} and {raw_tag!r} have the "
                "same overrides, PDK, standard-cell library, and flow")
        configs[identity] = raw_tag
        normalized.append({**candidate, "tag": tag, "overrides": overrides})
    return normalized


def validate_run_plan(run_spec: dict, max_iterations: int) -> dict:
    """Validates a run spec without invoking synthesis, Docker, or OpenLane."""
    if not isinstance(run_spec, dict):
        raise ValueError("run_spec must be a JSON object")
    if (isinstance(max_iterations, bool) or not isinstance(max_iterations, int)
            or max_iterations < 1):
        raise ValueError("max_iterations must be a positive integer")
    if not isinstance(run_spec.get("targets", {}), dict):
        raise ValueError("run_spec 'targets' must be an object")

    limits = evaluation_budget.validate_limits(run_spec.get("evaluation_budget"))
    explicit = run_spec.get("candidates", [])
    if not isinstance(explicit, list):
        raise ValueError("run_spec 'candidates' must be a list")
    swept = expand_sweeps(run_spec)
    budget = run_spec.get("candidate_budget")
    validate_candidates(explicit + swept)

    explore = run_spec.get("explore_synthesis")
    explore_count = 0
    if explore is not None:
        if not isinstance(explore, dict):
            raise ValueError("explore_synthesis must be an object")
        explore_count = explore.get("count", 3)
        if (isinstance(explore_count, bool)
                or not isinstance(explore_count, int)
                or explore_count < 1):
            raise ValueError("explore_synthesis.count must be a positive integer")
        if not isinstance(explore.get("overrides", {}), dict):
            raise ValueError("explore_synthesis.overrides must be an object")

    planned = len(explicit) + len(swept) + explore_count
    if planned == 0:
        raise ValueError("run_spec.json must have a non-empty 'candidates', "
                         "'sweeps' or 'explore_synthesis' entry")
    if budget is not None:
        validate_candidates([], budget)
        if planned > budget:
            raise ValueError(
                f"candidate plan has up to {planned} candidates, exceeding "
                f"candidate_budget={budget}")

    search = run_spec.get("search", {})
    if not isinstance(search, dict):
        raise ValueError("run_spec 'search' must be an object")
    if search.get("evaluation_order", "spec") not in {"spec", "measured_cost"}:
        raise ValueError("search.evaluation_order must be spec or measured_cost")
    mode = search.get("mode")
    if mode is not None and (not isinstance(mode, str) or not mode.strip()):
        raise ValueError("search.mode must be a non-empty string")
    if mode is None:
        kinds = sum(bool(x) for x in (explicit, swept, explore_count))
        mode = "hybrid" if kinds > 1 else (
            "synthesis-exploration" if explore_count else
            "sweep" if swept else "explicit")

    return {
        "mode": mode,
        "objective": search.get("objective"),
        "reference": search.get("reference"),
        "seed": search.get("seed"),
        "evaluation_order": search.get("evaluation_order", "spec"),
        "candidate_budget": budget,
        "evaluation_budget": limits,
        "planned_candidates_max": planned,
        "candidate_sources": {
            "explicit": len(explicit),
            "sweep": len(swept),
            "synthesis_exploration": explore_count,
        },
        "max_iterations": max_iterations,
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


def annotated_total_w(result: dict) -> float | None:
    """A candidate's measured total power, if it really was measured."""
    pa = (result.get("verdict") or {}).get("power_activity") or {}
    return (pa.get("annotated") or {}).get("total", {}).get("total_w")


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


def pick_winner(results: list[dict]) -> dict | None:
    """Picks the winner among passing candidates: the knee of the Pareto
    front over (cell area, power, core area, setup slack) — see
    pareto.pick_knee() and pareto_points(). Area, power, floorplan cost and
    timing margin are real, independent trade-offs among passing
    candidates (see reference-db/cases/*.json for real examples), not one
    axis to optimize alone. An objective that any passing candidate lacks
    is dropped for all of them rather than read as zero.
    """
    passing = [r for r in results if not r.get("not_evaluated") and not r.get("error")
               and r.get("verdict", {}).get("passed")]
    if not passing:
        return None
    if len(passing) == 1:
        return passing[0]

    winner_tag = pick_knee(pareto_points(passing))
    return next(r for r in passing if r["tag"] == winner_tag)


# The order objectives appear in a Pareto point, and the names polish
# applies its tolerances to. "margin" is minus the worst setup slack, so
# that every objective is something to minimise.
OBJECTIVES = ("area", "power", "core", "margin")


def pareto_points(passing: list[dict]) -> list[ParetoPoint]:
    """The objective vectors of passing candidates, all to be minimised.

    One definition of "better" shared by winner selection and by polish
    (pnr_polish.improves), so a move is judged on the same quantities a
    winner is chosen by. The optional objectives are decided over the
    given set, so comparing two candidates uses what both have.
    """
    return [ParetoPoint(key=tag, objs=tuple(o[n] for n in OBJECTIVES if n in o))
            for tag, o in objective_table(passing)]


def objective_table(passing: list[dict]) -> list[tuple[str, dict]]:
    """(tag, {objective name: value}) for each passing candidate."""
    # Rank on measured power when every passing candidate has it, and on
    # the estimate otherwise — never a mixture.
    #
    # This is the whole reason the choice is made here rather than
    # per-candidate. Annotated and vectorless numbers are not
    # interchangeable: on spm the same netlist reads 1.33e-03 W
    # estimated against 1.53e-03 W measured. Ranking a measured
    # candidate against an estimated one would compare a 15% offset
    # and call it a difference between designs, so a single candidate
    # whose simulation failed to compile would silently win the power
    # objective against candidates that are genuinely better.
    #
    # An all-or-nothing rule is safe because the testbench belongs to
    # the design, not the candidate: within one run_spec the candidates
    # either all have one or none do, and the mixed case only arises
    # when a simulation actually failed — exactly when the estimate is
    # the honest common basis.
    def finite(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    use_annotated = all(finite(annotated_total_w(r)) for r in passing)
    use_power = use_annotated or all(finite((r["verdict"].get("power") or {}).get("total_w")) for r in passing)
    use_area = all(finite(r["verdict"].get("area_um2")) for r in passing)

    # Two objectives the ranking used to lack, each added only when every
    # passing candidate has it (the same all-or-nothing rule, for the same
    # reason: a missing number must not read as a good one).
    #
    # Core area: the floorplan's own cost. Utilization is the most swept
    # knob in the store and it moves instance area not at all (counter4:
    # 290.278 um^2 at FP_CORE_UTIL 25 and 35), so a ranking on instance
    # area is structurally blind to every placement-density decision.
    #
    # Setup slack: the old third objective was -worst_setup_wns, which is
    # 0 for all 159 passing candidates in the store — a constant, which
    # discriminates nothing. Real margin is the worst setup slack.
    use_core = all(finite(_core_area(r["verdict"])) for r in passing)
    use_slack = all(finite(_setup_slack(r["verdict"])) for r in passing)

    table = []
    for r in passing:
        v = r["verdict"]
        if use_annotated:
            power_total = annotated_total_w(r)
        else:
            power_total = (v.get("power") or {}).get("total_w")
        o = {}
        if use_area:
            o["area"] = v["area_um2"]
        if use_power:
            o["power"] = power_total
        if use_core:
            o["core"] = _core_area(v)
        if use_slack:
            o["margin"] = -_setup_slack(v)  # all objectives are minimised
        table.append((r["tag"], o))
    return table


def _core_area(verdict: dict) -> float | None:
    """Core area in um^2: recorded by score(), else rebuilt from instance
    area and utilization (design__instance__utilization__stdcell is
    exactly stdcell area over core area, checked on a gcd run:
    3004.13 / 0.452166 = 6643.9 = design__core__area)."""
    recorded = verdict.get("core_area_um2")
    if isinstance(recorded, (int, float)) and not isinstance(recorded, bool) and math.isfinite(recorded) and recorded > 0:
        return float(recorded)
    area, util = verdict.get("area_um2"), verdict.get("utilization")
    if (all(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
            for value in (area, util)) and area > 0 and util > 0):
        return area / util
    return None


def _setup_slack(verdict: dict) -> float | None:
    """Worst setup slack in ns: recorded by score(), else the minimum over
    the per-corner slacks of a stored operating point."""
    recorded = verdict.get("worst_setup_slack")
    if recorded is not None:
        return (float(recorded) if isinstance(recorded, (int, float)) and not isinstance(recorded, bool)
                and math.isfinite(recorded) else None)
    corners = (verdict.get("operating_point") or {}).get("corners") or []
    if any(not isinstance(c.get("setup_ws_ns"), (int, float)) or isinstance(c.get("setup_ws_ns"), bool)
           or not math.isfinite(c["setup_ws_ns"]) for c in corners):
        return None
    slacks = [c["setup_ws_ns"] for c in corners]
    return min(slacks) if slacks else None


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
UTIL_STEP_DOWN = pnr_repair.UTIL_STEP_DOWN  # 15 percentage points;
                      # conservative, matches the gap that separated the
                      # one passing candidate (35) from the first failing
                      # one (55) in that case. Defined in pnr_repair so the
                      # DPL-0036 repair steps by the same amount.
MIN_CORE_UTIL = pnr_repair.MIN_CORE_UTIL

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

# 3. aes__2026-08-30__145637: a candidate that completes the flow and
#    fails setup — and only setup. The repair is not a guess: the run
#    itself measured the period it needs. operating_point() derives
#    min_period per corner from the run's own slack, and aes showed the
#    relationship holds end to end: 481 setup violations at 6 ns, 3 at
#    11.2 ns (ss min_period 11.45), 0 at 12 ns. The margin covers the
#    fact that re-closing at a new period changes synthesis and sizing,
#    so the measured min_period is a floor rather than an exact answer
#    (operating_point.py's first stated limit).
#
#    Fires only when setup is the *whole* failure. Hold does not move
#    with the period (aes at 12 ns: setup 0, hold 264), and DRV or
#    antenna counts have their own causes — proposing a period change
#    against those would burn a run to learn what the case already says.
SETUP_PERIOD_MARGIN = 0.05
SETUP_PERIOD_STEP_NS = 0.1  # OpenLane takes any float; rounded up to a
                            # tenth so tags stay readable and two runs a
                            # rounding error apart are not both made.


def setup_period_repair(result: dict) -> float | None:
    """The CLOCK_PERIOD a setup-only failure measured itself needing,
    or None when this result is not that case."""
    if result.get("error"):
        return None
    verdict = result.get("verdict") or {}
    violations = verdict.get("violations") or []
    if not violations or any("setup" not in v for v in violations):
        return None
    op = verdict.get("operating_point") or {}
    period = op.get("clock_period_ns")
    needed = [c["min_period_ns"] for c in op.get("corners", [])
              if isinstance(c.get("min_period_ns"), (int, float))]
    if not isinstance(period, (int, float)) or not needed:
        return None
    worst = max(needed)
    if worst <= period:
        return None  # slack says it fits; the violation is not a period problem
    repaired = worst * (1 + SETUP_PERIOD_MARGIN)
    repaired = math.ceil(repaired / SETUP_PERIOD_STEP_NS) * SETUP_PERIOD_STEP_NS
    return round(repaired, 3)


def _repaired(result: dict, iteration: int, overrides: dict) -> dict:
    """A repair candidate built from the failed one — same technology.

    The repair used to carry only the tag and the overrides, and the
    first non-sky130 candidate to reach this function showed what that
    loses: gcd on gf180mcuD failed setup at 12 ns, the repair proposed
    13.2 ns with no `pdk`/`scl`, run_candidate() defaulted to
    sky130A/sky130_fd_sc_hd, and the sky130 run passed under a tag that
    said gf180. Area 12,133 -> 3,458 um^2 for a 1.2 ns period change was
    the tell. The technology is part of the candidate, not of the
    override set, so it has to be copied explicitly.
    """
    cand = {"tag": f"{result['tag']}-iter{iteration}", "overrides": overrides}
    for key in ("pdk", "scl"):
        if result.get(key):
            cand[key] = result[key]
    return cand


def propose_repairs(results: list[dict], iteration: int,
                    base_config: dict | None = None) -> list[dict]:
    """Mechanically proposes a repaired candidate set from real failures.

    `base_config` is the design's config.json. Patterns 1-4 read only the
    candidate's own overrides; the pnr_repair rules also consult it, so a
    candidate that failed on the design's default FP_CORE_UTIL or fixed
    DIE_AREA still has a value to repair.
    """
    next_candidates = []
    for r in results:
        error = r.get("error", "")
        overrides = r["overrides"]

        util_override = overrides.get("FP_CORE_UTIL")
        # The die the run actually used: its own override, else the design's
        # config.json. counter4_tinydie declares DIE_AREA [0,0,8,8] there, so
        # every candidate that only varied CLOCK_PERIOD (35 of them) died at
        # STA-0572 with a negative core area and was never repaired: both
        # die patterns below read the override alone and saw nothing to grow.
        die_area_override = overrides.get("DIE_AREA", (base_config or {}).get("DIE_AREA"))

        # A candidate that completed the whole flow but missed a target
        # this pipeline set (see score()) has no `error` at all, so every
        # pattern below — all of which read error text — was structurally
        # blind to it. That whole class of failure escalated straight to
        # a human despite being the most mechanically repairable kind:
        # the violation literally states the measured value and the
        # target it exceeded.
        #
        # Only the utilization violation is handled, and only because it
        # is not a guess: the repair is the same FP_CORE_UTIL step-down
        # already proven for PDN_STRAP_ERROR, and "utilization above
        # target" is definitionally addressed by asking for less of it.
        # The step is the existing conservative constant rather than one
        # scaled by the overshoot — requested FP_CORE_UTIL and achieved
        # stdcell utilization are different quantities (35 -> 0.604 in
        # counter4's real runs), so scaling by their ratio would assume a
        # relationship this pipeline has never measured. The bounded loop
        # re-measures instead.
        violations = (r.get("verdict") or {}).get("violations", [])
        overshoot = any(v.startswith("utilization ") for v in violations)
        if overshoot and isinstance(util_override, (int, float)):
            repaired = max(MIN_CORE_UTIL, util_override - UTIL_STEP_DOWN)
            if repaired == util_override:
                continue  # already at floor, no repair to propose
            new_overrides = dict(overrides)
            new_overrides["FP_CORE_UTIL"] = repaired
            next_candidates.append(_repaired(r, iteration, new_overrides))
        elif PDN_STRAP_ERROR in error and isinstance(util_override, (int, float)):
            repaired = max(MIN_CORE_UTIL, util_override - UTIL_STEP_DOWN)
            if repaired == util_override:
                continue  # already at floor, no repair to propose
            new_overrides = dict(overrides)
            new_overrides["FP_CORE_UTIL"] = repaired
            next_candidates.append(_repaired(r, iteration, new_overrides))
        elif DIE_TOO_SMALL_ERROR in error and isinstance(die_area_override, list) \
                and len(die_area_override) == 4:
            x0, y0, x1, y1 = die_area_override
            new_overrides = dict(overrides)
            new_overrides["DIE_AREA"] = [
                x0, y0,
                x0 + (x1 - x0) * DIE_AREA_GROWTH_FACTOR,
                y0 + (y1 - y0) * DIE_AREA_GROWTH_FACTOR,
            ]
            cand = _repaired(r, iteration, new_overrides)
            old_width, old_height = x1 - x0, y1 - y0
            new_width = new_overrides["DIE_AREA"][2] - new_overrides["DIE_AREA"][0]
            new_height = new_overrides["DIE_AREA"][3] - new_overrides["DIE_AREA"][1]
            cand["repair"] = {
                "code": "STA-0572",
                "why": (
                    f"absolute {old_width:g}x{old_height:g} um die produced a "
                    "non-positive core after floorplan margins; grow DIE_AREA "
                    f"to {new_width:g}x{new_height:g} um"
                ),
            }
            next_candidates.append(cand)
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
            next_candidates.append(_repaired(r, iteration, new_overrides))
        elif (period := setup_period_repair(r)) is not None:
            # Pattern #3 above: setup is the only failure and the run
            # measured the period it needs.
            new_overrides = dict(overrides)
            new_overrides["CLOCK_PERIOD"] = period
            next_candidates.append(_repaired(r, iteration, new_overrides))
        elif (fix := pnr_repair.repair(r, base_config)) is not None:
            # Placement / timing-repair failures whose error text carries
            # the answer (GPL-0302 even states the density to use). See
            # pnr_repair.RULES for each rule's evidence.
            cand = _repaired(r, iteration, fix["overrides"])
            cand["repair"] = {"code": fix["code"], "why": fix["why"]}
            next_candidates.append(cand)
        # Other failure/violation modes (DRC/LVS errors, hold or DRV
        # violations, unrecognized run errors) are not auto-repaired —
        # flagged in the iteration summary instead so a person or
        # feedback-optimizer can look at them.
    return next_candidates


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


def pick_layout_subject(iterations: list[dict], winner: dict | None) -> dict | None:
    """The one candidate worth rendering: the winner if there is one,
    else the candidate that got furthest through the flow (by
    PROCESS_STAGES order) — for a failed case that's the most
    informative layout available, which is precisely the case where a
    picture helps most."""
    if winner:
        return winner
    order = {s["id"]: i for i, s in enumerate(PROCESS_STAGES)}
    all_results = [r for it in iterations for r in it["results"]]
    if not all_results:
        return None
    return max(all_results, key=lambda r: order.get(r.get("stage"), -1))


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
