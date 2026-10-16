"""Prepare frozen context comparisons and score externally collected JSON responses.

No model calls, case writes, candidate execution, or claims of measured PPA.
"""
import argparse
import json
from pathlib import Path
import random

import review_context as context
from candidate_plan import validate_candidates


def write_json(path, value):
    with Path(path).open("x", encoding="utf-8") as stream:
        stream.write(context.canonical(value) + "\n")


def prepare(args):
    if args.repetitions < 1 or args.candidate_limit < 1:
        raise ValueError("repetitions and candidate-limit must be positive")
    frozen = context.snapshot(args.case_file, args.design_config, args.seed_tag,
                              args.allow_override, args.history_limit)
    args.output_dir.mkdir(parents=True, exist_ok=False)
    write_json(args.output_dir / "private-snapshot.json", frozen)
    hashes = {"private-snapshot.json": context.file_hash(args.output_dir / "private-snapshot.json")}
    trials = []
    rng = random.Random(args.random_seed)
    for repetition in range(args.repetitions):
        policies = list(context.POLICIES)
        rng.shuffle(policies)
        for policy in policies:
            trial = f"r{repetition + 1}-{policy}"
            name = trial + ".txt"
            (args.output_dir / name).write_text(
                context.prompt(context.capsule(frozen, policy), args.candidate_limit), encoding="utf-8")
            hashes[name] = context.file_hash(args.output_dir / name)
            trials.append({"id": trial, "policy": policy, "prompt": name})
    manifest = {"schema": 1, "status": "prepared_not_executed", "trials": trials,
                "candidate_limit": args.candidate_limit, "random_seed": args.random_seed,
                "hashes": hashes,
                "protocol": "Use identical model/settings in independent sessions without tools. "
                            "Expose only each trial prompt; keep private-snapshot inaccessible. "
                            "Record model/settings/usage separately; this tool cannot enforce isolation "
                            "or equal token/dollar budgets."}
    write_json(args.output_dir / "manifest.json", manifest)
    return manifest


def signature(row):
    return context.canonical({k: row.get(k) for k in ("overrides", "pdk", "scl", "flow")})


def score_response(frozen, policy, response, limit):
    if not isinstance(response, dict) or not isinstance(response.get("diagnosis"), str):
        raise ValueError("response requires diagnosis string and candidates list")
    proposed = response.get("candidates")
    if not isinstance(proposed, list) or len(proposed) > limit:
        raise ValueError("invalid candidate list or candidate limit exceeded")
    view = context.capsule(frozen, policy)
    visible = context.results(view["current"])
    for row in view["history"]:
        visible += context.results(row["case"])
    visible_tags = {r.get("tag") for r in visible}
    all_rows = context.results(frozen["case"])
    for row in frozen["history"]:
        all_rows += context.results(row["case"])
    prior = {signature(r) for r in all_rows}
    seen = set()
    rows = []
    candidates = []
    for proposal in proposed:
        errors = []
        if not isinstance(proposal, dict):
            rows.append({"errors": ["candidate must be an object"]})
            continue
        delta = proposal.get("overrides")
        if not isinstance(delta, dict):
            rows.append({"tag": proposal.get("tag"), "errors": ["overrides must be an object"]})
            continue
        if set(proposal) - {"tag", "overrides", "hypothesis", "evidence_tags"}:
            errors.append("unexpected candidate fields")
        if set(delta) - set(frozen["allowed_overrides"]):
            errors.append("override outside allowed tuning keys")
        if not isinstance(proposal.get("hypothesis"), str) or not proposal["hypothesis"].strip():
            errors.append("missing testable hypothesis")
        tags = proposal.get("evidence_tags")
        if (not isinstance(tags, list) or not tags
                or any(not isinstance(t, str) or t not in visible_tags for t in tags)):
            errors.append("evidence tags must cite visible candidates")
        seed = frozen["seed"]
        candidate = {k: seed[k] for k in ("pdk", "scl", "flow") if k in seed}
        candidate.update(tag=proposal.get("tag"), overrides={**seed.get("overrides", {}), **delta})
        try:
            validate_candidates([candidate])
        except (ValueError, TypeError) as exc:
            errors.append(str(exc))
        sig = signature(candidate)
        rows.append({"tag": proposal.get("tag"), "errors": errors,
                     "prior_config_signature_duplicate": sig in prior,
                     "response_config_signature_duplicate": sig in seen})
        seen.add(sig)
        candidates.append(candidate)
    batch_errors = []
    if candidates:
        try:
            validate_candidates(candidates)
        except (ValueError, TypeError) as exc:
            batch_errors.append(str(exc))
    return {"candidates": rows, "batch_errors": batch_errors,
            "physical_validation": "not_run", "ppa_improvement": None,
            "limitations": "Structural and explicit tag checks only; prose claims, physical "
                          "validity and historical execution compatibility are not verified."}


def score(args):
    manifest = json.loads((args.experiment / "manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["hashes"].items():
        path = (args.experiment / name).resolve()
        if not path.is_relative_to(args.experiment.resolve()) or context.file_hash(path) != expected:
            raise ValueError(f"snapshot integrity failure: {name}")
    frozen = json.loads((args.experiment / "private-snapshot.json").read_text(encoding="utf-8"))
    scores = []
    for trial in manifest["trials"]:
        path = args.responses / (trial["id"] + ".json")
        row = {"id": trial["id"], "policy": trial["policy"], "status": "missing"}
        if path.exists():
            row["response_sha256"] = context.file_hash(path)
            try:
                row.update(score_response(frozen, trial["policy"],
                           json.loads(path.read_text(encoding="utf-8")), manifest["candidate_limit"]))
                row["status"] = "scored"
            except (ValueError, TypeError) as exc:
                row.update(status="invalid_response", error=str(exc))
        scores.append(row)
    report = {"manifest_sha256": context.file_hash(args.experiment / "manifest.json"),
              "trials": scores, "model_usage": None, "cost": None,
              "physical_validation": "not_run", "ppa_improvement": None}
    write_json(args.output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    prep = sub.add_parser("prepare")
    prep.add_argument("--case-file", type=Path, required=True)
    prep.add_argument("--design-config", type=Path, required=True)
    prep.add_argument("--seed-tag", required=True)
    prep.add_argument("--allow-override", nargs="+", required=True)
    prep.add_argument("--history-limit", type=int, default=12)
    prep.add_argument("--repetitions", type=int, default=3)
    prep.add_argument("--candidate-limit", type=int, default=3)
    prep.add_argument("--random-seed", type=int, default=42)
    prep.add_argument("--output-dir", type=Path, required=True)
    prep.set_defaults(func=prepare)
    scoring = sub.add_parser("score")
    scoring.add_argument("--experiment", type=Path, required=True)
    scoring.add_argument("--responses", type=Path, required=True)
    scoring.add_argument("--output", type=Path, required=True)
    scoring.set_defaults(func=score)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
