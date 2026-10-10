"""Candidate and run-plan normalisation: override formatting, run-tag safety,
sweep expansion and plan validation.

Pure functions over run_spec dictionaries; nothing here starts a tool. Split
out of orchestrator.py, which re-exports these names for its callers.
"""
import json
import re

import evaluation_budget


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


# Override values the tool bounds although OpenLane declares them as a plain
# int, so neither OpenLane nor --validate-only would say so. (low, high) is
# half-open: low <= value < high.
#
# GRT_ANTENNA_MARGIN: OpenROAD's repair_antennas takes a ratio margin in
# [0, 100). At 100 it only warns (GRT-0215), multiplies the allowed antenna
# ratio by 1 - 100/100 = 0, and the checker reads a ratio of 0 as "no rule":
# every repair step finds 0 violations and inserts no diode. The 2026-10-09
# aes run at 100 cost about 25 minutes and was recorded as an ordinary FAIL
# with 64 antenna violations. Evidence: pipeline/designs/aes/experiments/
# evidence-20261009-margin-high/openroad_source_excerpts.txt.
# DIODE_TRIM_KEEP is this repository's own opt-in step (flows/diode_trim.py).
# Its range is listed here so a bad value fails at --validate-only, not after the
# flow starts; 0 is rejected because the step is only part of the flow when the
# override is positive, so DIODE_TRIM_KEEP=0 would be reported as an ignored
# override. Keeping 10 or more diodes could never trim the cap's 10.
TOOL_RANGES = {
    "DIODE_TRIM_KEEP": (
        1, 10,
        "keeps this many diodes on every net the DiodeTrim step trims; 0 is not "
        "a value (leave the override out to disable the step) and 10 or more "
        "would trim nothing at the checker's cap"),
    "GRT_ANTENNA_MARGIN": (
        0, 100,
        "OpenROAD repair_antennas accepts a ratio margin in [0, 100); at 100 "
        "it warns (GRT-0215), the allowed ratio becomes 0 and nothing is "
        "repaired"),
}


def check_override_ranges(overrides: dict, where: str) -> None:
    """Rejects an override whose value the tool does not accept, before any
    EDA tool starts. Values may be ints or integer strings, as for the CLI."""
    for key, (low, high, why) in TOOL_RANGES.items():
        if key not in overrides:
            continue
        raw = overrides[key]
        value = raw
        if isinstance(raw, str):
            try:
                value = int(raw.strip())
            except ValueError:
                value = None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(
                f"{where}.overrides.{key}={raw!r} must be an integer: {why}")
        if not low <= value < high:
            raise ValueError(
                f"{where}.overrides.{key}={raw!r} is outside [{low}, {high}): {why}")


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
        check_override_ranges(overrides, where)
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
        check_override_ranges(explore.get("overrides", {}), "explore_synthesis")

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
