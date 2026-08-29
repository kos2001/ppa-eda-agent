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
import re
import sys
from datetime import date, datetime, timezone
from pathlib import Path

from run_stage import run_stage, read_metrics
import cdc_check
import def_layout
import design_rules
import equiv_check
import netlist_graph
import model_validity
import operating_point
import power_activity
import render_layout
import step_coverage
import synth_explore
from pareto import ParetoPoint, pick_best
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
    config = json.loads(config_file.read_text())
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
    for key, label in SIGNOFF_METRICS:
        count = metrics.get(key)
        if count is None:
            unverified.append(label)
        elif count:
            violations.append(f"{count} {label}")

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

    # Hold. This was recorded per corner and displayed on the dashboard
    # but never gated on, so a candidate with a real hold violation was
    # reported PASS while showing the negative slack on screen —
    # demonstrated directly with hold_wns -0.25 and 7 hold violations
    # scoring as a pass. Hold violations are silicon-fatal and cannot be
    # fixed after fabrication, which makes this the worst thing the
    # verdict could have been silent about.
    hold_wns_keys = [k for k in metrics if k.startswith("timing__hold__wns__corner:")]
    worst_hold = min((metrics[k] for k in hold_wns_keys), default=0)
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
        "area_um2": metrics.get("design__instance__area"),
        "utilization": util,
        "worst_setup_wns": worst_wns,
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
    try:
        results = synth_explore.explore(
            design_dir, tag="synth-explore", clock_period_ns=clock_period(design_dir))
    except Exception as e:  # noqa: BLE001 - recorded, not silenced
        return [], {"error": f"{type(e).__name__}: {e}"}

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
    expanded = []
    for sweep in run_spec.get("sweeps", []):
        base_overrides = sweep.get("overrides", {})
        for value in sweep["values"]:
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
            tag = safe_tag(f"{sweep.get('tag_prefix', sweep['param'])}-{value}")
            expanded.append({
                "tag": tag,
                "overrides": {**base_overrides, sweep["param"]: value},
            })
    return expanded


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


def run_candidate(design_dir: Path, run_spec: dict, cand: dict,
                   verify_fn: bool = False) -> dict:
    """Runs and scores one independent candidate."""
    tag = cand["tag"]
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
    print(f"\n=== candidate '{tag}' — overrides: {cand.get('overrides', {})}"
          f"{f', scl: {scl}' if scl else ''} ===", file=sys.stderr)
    try:
        run_dir = run_stage(design_dir, tag, to_step=None, overrides=overrides,
                            scl=scl)
        metrics = read_metrics(run_dir)
        verdict = score(metrics, run_spec.get("targets", {}))
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
        verdict["passed"] = not verdict["violations"] and not verdict["unverified"]
        # Fmax/Vmin, derived from per-corner slack the run already
        # measured. Needs the clock period, which lives in config.json
        # and never reaches score().
        verdict["operating_point"] = operating_point.operating_point(
            metrics, clock_period(design_dir))
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
                "scl": scl,
                "verdict": verdict, "run_dir": str(run_dir),
                "data": data_pointers(run_dir),
                "clocks": clocks,
                "netlist": netlist,
                "step_coverage": coverage,
                "equivalence": equiv,
                "layout": layout}
    except Exception as e:  # noqa: BLE001 - report and keep evaluating others
        return {"tag": tag, "overrides": cand.get("overrides", {}),
                "scl": scl, "error": str(e)}


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
                       max_parallel: int = 1) -> tuple[list[dict], list[dict]]:
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
        try:
            run_dir = run_stage(design_dir, tag, to_step=SCREEN_STEP,
                                 overrides=overrides)
            metrics = read_metrics(run_dir)
            return {"cand": cand,
                     "early_util": metrics.get("design__instance__utilization__stdcell")}
        except Exception:  # noqa: BLE001 — let the real run record it
            return {"cand": cand, "early_util": None}

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
            })
        else:
            survivors.append(cand)
    return survivors, pruned


def run_candidates(design_dir: Path, run_spec: dict,
                   max_parallel: int = 1, verify_fn: bool = False) -> list[dict]:
    candidates = run_spec["candidates"]
    if max_parallel <= 1 or len(candidates) <= 1:
        return [run_candidate(design_dir, run_spec, cand, verify_fn)
                for cand in candidates]

    results_by_tag = {}
    with ThreadPoolExecutor(max_workers=min(max_parallel, len(candidates))) as executor:
        futures = {
            executor.submit(run_candidate, design_dir, run_spec, cand, verify_fn): cand["tag"]
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
    """Picks the winner among passing candidates via constrained Pareto-
    front ranking (see pipeline/pareto.py) instead of a single "smallest
    area wins" heuristic — area, power, and timing margin are real,
    independent trade-offs among passing candidates (see
    reference-db/cases/*.json for real examples), not one axis to
    optimize alone. Falls back gracefully when a candidate's power data
    is unavailable (treated as 0 in that objective — see pareto.py's
    docstring for why this is a deliberate simplification, not a bug).
    """
    passing = [r for r in results if r.get("verdict", {}).get("passed")]
    if not passing:
        return None
    if len(passing) == 1:
        return passing[0]

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
    use_annotated = all(annotated_total_w(r) is not None for r in passing)

    points = []
    for r in passing:
        v = r["verdict"]
        area = v["area_um2"] or 0.0
        if use_annotated:
            power_total = annotated_total_w(r) or 0.0
        else:
            power_total = (v.get("power") or {}).get("total_w") or 0.0
        margin = -v["worst_setup_wns"]  # minimize negative slack = maximize margin
        points.append(ParetoPoint(key=r["tag"], objs=(area, power_total, margin)))

    winner_tag = pick_best(points)
    return next(r for r in passing if r["tag"] == winner_tag)


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
            next_candidates.append({
                "tag": f"{r['tag']}-iter{iteration}",
                "overrides": new_overrides,
            })
        elif PDN_STRAP_ERROR in error and isinstance(util_override, (int, float)):
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
    value = json.loads(cfg.read_text()).get("CLOCK_PERIOD")
    return float(value) if isinstance(value, (int, float, str)) and str(value).strip() else None


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
               exploration: dict | None = None) -> Path:
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
        # Real rendered layout of this case's most informative candidate,
        # stored under reference-db/ so it outlives the run directory.
        "layout_image": capture_layout_image(design_name, design_dir, subject),
        "layout_image_tag": subject["tag"] if subject else None,
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
STOP_REASONS = ("winner_found", "max_iterations_reached", "no_repairable_failures")


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
    explored_candidates, exploration = expand_synthesis_exploration(design_dir, run_spec)
    candidates = (run_spec.get("candidates", []) + expand_sweeps(run_spec)
                  + explored_candidates)
    if not candidates:
        raise ValueError("run_spec.json must have a non-empty 'candidates', "
                          "'sweeps' or 'explore_synthesis' entry")
    all_iterations = []
    winner = None
    stop_reason = None

    iteration = 1
    while True:
        screened_out = []
        to_run = candidates
        if screen:
            to_run, screened_out = screen_candidates(
                design_dir, candidates, run_spec.get("targets", {}),
                max_parallel=max(1, max_parallel))
            print(f"\nscreen ({SCREEN_STEP}): {len(to_run)} of "
                  f"{len(candidates)} candidate(s) survive", file=sys.stderr)
        results = run_candidates(
            design_dir,
            {**run_spec, "candidates": to_run},
            max_parallel=max(1, max_parallel),
            verify_fn=verify_fn,
        )
        # Pruned candidates are real, measured rejections and belong in
        # the iteration's results exactly like any other — dropping them
        # would understate what was tried and break auto-repair coverage
        # accounting. They also stay repairable: their verdict carries a
        # real utilization violation, which propose_repairs() acts on.
        results = results + screened_out
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
            stop_reason = "winner_found"
            break
        if iteration >= max_iterations:
            print(f"\nreached max_iterations ({max_iterations}) with no winner")
            stop_reason = "max_iterations_reached"
            break

        next_candidates = propose_repairs(results, iteration)
        if not next_candidates:
            print("\nno auto-repairable failures found — stopping "
                  "(needs placement-strategist/feedback-optimizer to propose "
                  "a genuinely new candidate set)")
            stop_reason = "no_repairable_failures"
            break

        print(f"\nauto-repair proposing {len(next_candidates)} candidate(s) "
              f"for iteration {iteration + 1}: "
              f"{[(c['tag'], c['overrides']) for c in next_candidates]}")
        candidates = next_candidates
        iteration += 1

    assert stop_reason in STOP_REASONS, f"ungated exit: {stop_reason!r}"
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
    ap.add_argument("--screen", action="store_true",
                     help=f"pre-flight each candidate only to {SCREEN_STEP} and "
                          f"run the full flow only on survivors; wins when "
                          f"failures are common (see screen_candidates())")
    args = ap.parse_args()

    run_spec = json.loads(args.run_spec.read_text())
    design_name = run_spec.get("design_name", args.design.name)
    max_iterations = args.max_iterations or run_spec.get("max_iterations", 3)

    all_iterations, winner, stop_reason, exploration = orchestrate(
        args.design, run_spec, max_iterations, args.max_parallel,
        screen=args.screen,
        verify_fn=args.verify_function,
    )

    case_file = write_case(design_name, args.design, all_iterations, winner,
                            stop_reason,
                            exploration=exploration)
    print(f"\nwinner: {winner['tag'] if winner else 'none — needs a new candidate set'}")
    print(f"stop reason: {stop_reason}")
    print(f"case written to: {case_file}")


if __name__ == "__main__":
    main()
