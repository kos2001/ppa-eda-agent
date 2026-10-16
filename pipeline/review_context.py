"""Explicit evidence views for review-context experiments. Never edits case records."""
import copy
import hashlib
import json
from pathlib import Path
import re

import case_store

POLICIES = ("full_history", "fresh_evidence", "seed_only")
PROTECTED = {"CLOCK_PERIOD", "CLOCK_PORT", "IO_DELAY_CONSTRAINT", "PNR_SDC_FILE",
             "SIGNOFF_SDC_FILE", "SDC_FILE", "PDK", "STD_CELL_LIBRARY"}
RESULT_FIELDS = ("tag", "overrides", "pdk", "scl", "flow", "stage", "error", "verdict",
                 "metrics", "run_dir", "timings", "run_cost", "not_evaluated",
                 "budget_exhausted", "evaluation_fidelity", "screen_evaluation")
CASE_FIELDS = ("design", "date", "outcome", "stop_reason", "toolchain", "constraints",
               "expected_outcome", "evaluation_budget")
# Strip commentary even when nested inside an otherwise factual object.
NARRATIVE_FIELDS = {"diagnosis", "human_in_the_loop", "reviews", "review", "hypothesis",
                    "rationale", "recommendation", "recommendations", "graph_evidence",
                    "graph_action", "scheduling", "produced_by_feedback"}


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_hash(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def facts(value):
    if isinstance(value, dict):
        return {k: facts(v) for k, v in value.items() if k not in NARRATIVE_FIELDS}
    if isinstance(value, list):
        return [facts(v) for v in value]
    return value


def results(case):
    return [r for iteration in case.get("iterations", []) for r in iteration.get("results", [])]


def factual_case(case):
    return {**facts({k: case[k] for k in CASE_FIELDS if k in case}),
            "iterations": [{"results": [facts({k: r[k] for k in RESULT_FIELDS if k in r})
                                        for r in results(case)]}]}


def validate_allowed(keys):
    if not keys or len(keys) != len(set(keys)):
        raise ValueError("provide a nonempty, unique --allow-override list")
    for key in keys:
        if (not re.fullmatch(r"[A-Z][A-Z0-9_]*", key) or key in PROTECTED
                or key.startswith(("RUN_", "QUIT_ON_", "ERROR_ON_", "SIGNOFF_", "CHECK_"))
                or "CORNER" in key):
            raise ValueError(f"objective, signoff, or invalid override cannot be varied: {key}")
    return sorted(keys)


def snapshot(case_file, config_file, seed_tag, allowed, history_limit=12):
    case_file, config_file = Path(case_file), Path(config_file)
    if not isinstance(history_limit, int) or history_limit < 0:
        raise ValueError("history_limit must be nonnegative")
    allowed = validate_allowed(allowed)
    source_hash = file_hash(case_file)
    case = case_store.load_light(case_file, cache_dir=None)
    candidates = [r for r in results(case) if r.get("tag") == seed_tag]
    if len(candidates) != 1:
        raise ValueError("--seed-tag must identify exactly one candidate in the selected case")
    seed = candidates[0]
    if seed.get("not_evaluated") or not (seed.get("verdict") or seed.get("error")):
        raise ValueError("seed requires recorded tool output; deferred/unmeasured seeds are ineligible")
    config_hash = file_hash(config_file)
    config = json.loads(config_file.read_text(encoding="utf-8"))
    if not isinstance(config, dict):
        raise ValueError("design config must be an object")
    earlier = []
    for path in sorted(case_file.parent.glob("*.json"), reverse=True):
        if path.name >= case_file.name:
            continue
        # The design prefix is a filter, not a glob supplied by the caller.
        if not path.name.startswith(str(case.get("design")) + "__"):
            continue
        before = file_hash(path)
        old = case_store.load_light(path, cache_dir=None)
        if old.get("design") != case.get("design"):
            continue
        if file_hash(path) != before:
            raise ValueError(f"case changed while snapshotting: {path.name}")
        earlier.append({"file": path.name, "sha256": before, "case": old})
    if file_hash(case_file) != source_hash or file_hash(config_file) != config_hash:
        raise ValueError("inputs changed while snapshotting")
    return {"schema": 1, "source": {"file": case_file.name, "sha256": source_hash},
            "config": config, "config_sha256": config_hash,
            "config_role": "supplied design baseline, not proof of historical execution inputs",
            "case": case, "seed": copy.deepcopy(seed), "allowed_overrides": allowed,
            "history": earlier, "history_limit": history_limit}


def capsule(snapshot, policy):
    if policy not in POLICIES:
        raise ValueError(f"unknown context policy: {policy}")
    current = factual_case(snapshot["case"])
    if policy == "seed_only":
        current["iterations"] = [{"results": [facts({k: snapshot["seed"][k] for k in RESULT_FIELDS
                                                    if k in snapshot["seed"]})]}]
    history = []
    remaining = snapshot["history_limit"]
    if policy != "seed_only":
        for row in snapshot["history"]:
            old = factual_case(row["case"])
            kept = results(old)[:remaining]
            if not kept:
                continue
            old["iterations"] = [{"results": kept}]
            history.append({"file": row["file"], "sha256": row["sha256"], "case": old})
            remaining -= len(kept)
            if not remaining:
                break
    value = {"schema": 1, "policy": policy, "source": snapshot["source"],
             "design_config": facts(snapshot["config"]), "config_sha256": snapshot["config_sha256"],
             "config_role": snapshot["config_role"], "seed_tag": snapshot["seed"]["tag"],
             "allowed_overrides": snapshot["allowed_overrides"], "current": current,
             "history": history, "physical_validation": "unchanged; proposals are unverified"}
    if policy == "full_history":
        # Align the narrative arm with the measured history actually exposed.
        visible = {row["file"] for row in history}
        value["diagnoses"] = [{"file": snapshot["source"]["file"],
                              "diagnosis": snapshot["case"].get("diagnosis"),
                              "reviews": snapshot["case"].get("human_in_the_loop", [])}]
        value["diagnoses"] += [{"file": row["file"], "diagnosis": row["case"].get("diagnosis"),
                                "reviews": row["case"].get("human_in_the_loop", [])}
                               for row in snapshot["history"] if row["file"] in visible]
    return value


def prompt(value, candidate_limit=3):
    if not isinstance(candidate_limit, int) or candidate_limit < 1:
        raise ValueError("candidate_limit must be positive")
    return ("Review the supplied EDA evidence in a new, independent session. Use only this capsule; "
            "do not retrieve the repository, earlier conversations, reviews, or private evaluation files. "
            "Missing measurements remain unknown. Preserve all objective, PDK/SCL/flow, functional "
            "equivalence, DRC/LVS, timing-corner, and model-validity requirements. Historical rows "
            "are observations, not proof of compatibility with the current run.\n"
            f"Return JSON only: {{\"diagnosis\":\"...\",\"candidates\":[{{\"tag\":\"proposal-1\","
            "\"overrides\":{},\"hypothesis\":\"...\",\"evidence_tags\":[]}]}. "
            f"Propose at most {candidate_limit} candidates; an empty list is acceptable. "
            "Each overrides object is a DELTA to the selected seed's overrides. Change only keys "
            "listed in allowed_overrides. Do not claim candidate success or estimated PPA as measured. "
            "Give one testable hypothesis and cite only visible candidate tags.\n\n"
            + canonical(value) + "\n")
