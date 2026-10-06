"""The 8-step process the pipeline is organised around, and tagging results with
the stage they reached.

PROCESS_STAGES is the single source of truth for stage names and order; the
dashboard reads the same names via reference-db. Pure; nothing here starts a
tool. Split out of orchestrator.py, which re-exports these names.
"""

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
