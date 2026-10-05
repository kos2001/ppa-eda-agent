#!/usr/bin/env python3
"""Verifies independent review answers against each other before one
enters a case's diagnosis.

`request_review.py apply` records one subagent's answer. The answer it
records is one sample: the same reviewer, asked again, can blame a
different stage or propose a different knob, and nothing on the way
into the case tells the two apart. Grounding (verify_diagnosis) catches
an invented error code; it cannot catch a plausible root cause that
every sample shares, or settle which of two samples is right.

The method is VeriHarness's (github.com/google-research/veriharness,
arXiv:2610.00972), applied to review answers instead of task rollouts:

  - Disagreement -> resolve. Where the answers differ, the alternatives
    are already on the table; a resolver settles each with a check
    against recorded evidence (ledger_elim.json).
  - Consensus -> challenge. Where they agree, the agreement is not
    evidence; they share a model and its blind spots. A challenger tries
    to break each shared position (ledger_fals.json).
  - Adjudication. A third session, which saw neither investigation,
    reads both records and names a base answer, the evidence-backed work
    it needs, and the questions still open (finish.json), then writes
    the delivered diagnosis (out/diagnosis.md).

This program does what VeriHarness's driver does and no more: it
materializes the workspace, checks the records' shape, and moves the
result into the case. Which claims to examine and what to conclude are
the sessions' decisions, made by the review-resolver, review-challenger
and review-adjudicator subagents (.claude/agents/). The gates here are
content-blind on purpose — a regex that judged physics would be worse
than none (see verify_diagnosis's own docstring):

  - inputs unchanged: the request, the case snapshot and every answer
    hash as they did at init, so a session cannot edit what it judges;
  - records well-formed: no verdict without a named check, no broken
    consensus without the contradiction quoted, no work item without
    evidence, every open question carries two readings;
  - open questions delivered: each one appears verbatim in the diagnosis,
    so "unknown" survives into the case instead of being decided by
    omission;
  - same evidence: the case's recorded iterations still match the
    snapshot the sessions read, and it is still the design's latest case.

Usage:
    python3 review_verify.py init --design aes \\
        --rollout feedback-optimizer=/tmp/r1.md \\
        --rollout feedback-optimizer=/tmp/r2.md
    # dispatch review-resolver and review-challenger (concurrently, each
    # on the printed workspace), then review-adjudicator.
    python3 review_verify.py check --workspace <dir>
    python3 review_verify.py apply --workspace <dir>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

import case_store
import request_review

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = Path(__file__).resolve().parent / "review_prompts"
PROMPT_FILES = ("CHARTER.md", "RESOLVE.md", "CHALLENGE.md", "ADJUDICATE.md")
MIN_ROLLOUTS = 2


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(path.read_bytes())


def _json_bytes(value) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode("utf-8")


def _shown(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def parse_rollout(spec: str) -> tuple[str, Path]:
    agent, sep, path = spec.partition("=")
    if not sep or not agent.strip() or not path.strip():
        raise SystemExit(f"--rollout expects <agent>=<path>, got '{spec}'")
    return agent.strip(), Path(path.strip())


def latest_case_file(design: str, refdb: Path) -> Path:
    index = json.loads((refdb / "index.json").read_text(encoding="utf-8"))
    names = index.get(design, [])
    if not names:
        raise SystemExit(f"no reference-db cases found for design '{design}'")
    return refdb / "cases" / sorted(names)[-1]


def archived_evidence(design: str, tag: str | None,
                      repo_root: Path = REPO_ROOT) -> list[str]:
    """Checked-in copies of a run's reports, for runs whose directory is gone.

    Run directories are often temporary (aes's closure runs lived under
    /private/tmp). Some were archived as
    `pipeline/designs/<design>/experiments/<set>/<tag>/` with a
    sources.json that hashes each copied file back to its run. In the
    first real verification, one of two reviewers found that archive and
    the other wrote "the run_dir no longer exists" and stopped there:
    the same evidence, reachable by one candidate only. Listed here so
    every session starts from it. A partial copy, never the run: what
    was not archived is still missing.
    """
    if not tag:
        return []
    experiments = repo_root / "pipeline" / "designs" / design / "experiments"
    return sorted(p.parent.relative_to(repo_root).as_posix()
                  for p in experiments.glob(f"*/{tag}/sources.json"))


def run_inventory(case: dict, repo_root: Path = REPO_ROOT) -> list[dict]:
    """Each recorded candidate's run directory and whether it is on this
    machine. A missing directory stays visible as missing: it is missing
    for every reviewer equally, and nobody gets to fill it in."""
    rows = []
    for number, iteration in enumerate(case.get("iterations", []), 1):
        for result in iteration.get("results", []):
            run_dir = result.get("run_dir")
            rows.append({
                "iteration": number,
                "tag": result.get("tag"),
                "stage": result.get("stage"),
                "run_dir": run_dir,
                "exists": bool(run_dir) and Path(run_dir).is_dir(),
                "archived": archived_evidence(case.get("design", ""),
                                              result.get("tag"), repo_root),
            })
    return rows


def mission_text(manifest: dict) -> str:
    rollouts = "\n".join(f"- `{r['name']}` — written by `{r['agent']}`"
                         for r in manifest["rollouts"])
    return (
        f"# Mission\n\n"
        f"- Design: `{manifest['design']}`\n"
        f"- Case: `{manifest['case_file']}` (snapshot in `workspace/case.json`)\n"
        f"- Candidates: {len(manifest['rollouts'])}, under `rollouts/`\n"
        f"{rollouts}\n"
        f"- Run directories: `workspace/runs.json` "
        f"({manifest['runs_present']} of {manifest['runs_total']} present here, "
        f"{manifest['runs_archived']} with archived reports in the repository; "
        f"an archive is a partial copy, see its sources.json)\n\n"
        f"Read `CHARTER.md`, then your phase:\n\n"
        f"- resolver: `RESOLVE.md` -> `ledger_elim.json`\n"
        f"- challenger: `CHALLENGE.md` -> `ledger_fals.json`\n"
        f"- adjudicator (after both): `ADJUDICATE.md` -> `finish.json`, "
        f"`out/diagnosis.md`\n"
    )


def init_workspace(design: str, rollouts: list[tuple[str, Path]], root: Path,
                   refdb: Path) -> Path:
    if len(rollouts) < MIN_ROLLOUTS:
        # One answer has nothing to disagree with and no consensus that
        # is more than itself; `request_review.py apply` is that path.
        raise SystemExit(f"need at least {MIN_ROLLOUTS} independent answers, "
                         f"got {len(rollouts)}")

    case_file = latest_case_file(design, refdb)
    case = case_store.load_light(case_file, cache_dir=None)
    if case.get("winner_tag"):
        raise SystemExit(f"'{design}' latest case already has a winner "
                         f"({case['winner_tag']}); nothing to review")

    request = refdb / "reviews" / request_review.request_filename(
        design, case, case_file.name)
    if not request.is_file():
        raise SystemExit(f"no review request for {case_file.name}; run "
                         f"`python3 request_review.py request --design {design}` "
                         f"and give that request to every reviewer first")

    answers = []
    seen = {}
    for agent, path in rollouts:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            raise SystemExit(f"answer {path} is empty")
        digest = sha256_bytes(text.encode("utf-8"))
        if digest in seen:
            # Two copies of one answer are one answer counted twice: a
            # consensus manufactured by the file system.
            raise SystemExit(f"{path} is identical to {seen[digest]}; "
                             f"each answer must come from an independent dispatch")
        seen[digest] = path
        answers.append((agent, path, text, digest))

    workspace = root / case_file.stem
    if workspace.exists():
        raise SystemExit(f"{workspace} already exists; apply or remove it "
                         f"before verifying this case again")

    (workspace / "spec").mkdir(parents=True)
    (workspace / "workspace").mkdir()
    (workspace / "out").mkdir()
    shutil.copyfile(request, workspace / "spec" / "request.md")
    (workspace / "workspace" / "case.json").write_text(
        json.dumps(case, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    runs = run_inventory(case)
    (workspace / "workspace" / "runs.json").write_text(
        json.dumps(runs, indent=1) + "\n", encoding="utf-8")
    for name in PROMPT_FILES:
        shutil.copyfile(PROMPTS / name, workspace / name)

    rows = []
    for number, (agent, path, text, digest) in enumerate(answers, 1):
        name = f"r{number:02d}"
        folder = workspace / "rollouts" / name
        folder.mkdir(parents=True)
        (folder / "response.md").write_text(text + "\n", encoding="utf-8")
        (folder / "meta.json").write_text(
            json.dumps({"agent": agent, "source": str(path)}, indent=1) + "\n",
            encoding="utf-8")
        rows.append({"name": name, "agent": agent,
                     "sha256": sha256_file(folder / "response.md")})

    manifest = {
        "method": "veriharness",
        "design": design,
        "case_file": case_file.name,
        "case_iterations_sha256": sha256_bytes(_json_bytes(case.get("iterations", []))),
        "request_sha256": sha256_file(workspace / "spec" / "request.md"),
        "snapshot_sha256": sha256_file(workspace / "workspace" / "case.json"),
        "runs_total": len(runs),
        "runs_present": sum(1 for r in runs if r["exists"]),
        "runs_archived": sum(1 for r in runs if r["archived"]),
        "rollouts": rows,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    (workspace / "manifest.json").write_text(
        json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    (workspace / "MISSION.md").write_text(mission_text(manifest), encoding="utf-8")
    return workspace


def _text(entry: dict, key: str) -> bool:
    return isinstance(entry.get(key), str) and bool(entry[key].strip())


def _load(path: Path, problems: list[str]):
    if not path.is_file():
        problems.append(f"{path.name} is missing")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        problems.append(f"{path.name} is not valid JSON: {e}")
        return None


def unsettled(row: dict) -> bool:
    """A verdict of 'cannot tell'. Sessions explain it in the same string
    ("cannot tell. Deciding runs: ..."), so the phrase is a prefix: an
    exact match counted none of three in the first real verification."""
    return str(row.get("verdict", "")).strip().lower().startswith("cannot tell")


def check_elim(ledger, problems: list[str]) -> None:
    rows = ledger.get("disagreements") if isinstance(ledger, dict) else None
    if not isinstance(rows, list):
        problems.append("ledger_elim.json: 'disagreements' must be a list")
        return
    for i, row in enumerate(rows):
        where = f"ledger_elim.json disagreements[{i}]"
        if not isinstance(row, dict):
            problems.append(f"{where}: not an object")
            continue
        for key in ("question", "checked", "found", "verdict"):
            if not _text(row, key):
                problems.append(f"{where}: '{key}' is empty")
        if (_text(row, "checked") and row["checked"].strip().lower() == "nothing"
                and not unsettled(row)):
            problems.append(f"{where}: a verdict with nothing checked "
                            f"must be 'cannot tell'")


def check_fals(ledger, problems: list[str]) -> None:
    rows = ledger.get("challenges") if isinstance(ledger, dict) else None
    if not isinstance(rows, list):
        problems.append("ledger_fals.json: 'challenges' must be a list")
        return
    for i, row in enumerate(rows):
        where = f"ledger_fals.json challenges[{i}]"
        if not isinstance(row, dict):
            problems.append(f"{where}: not an object")
            continue
        for key in ("claim", "tried", "because"):
            if not _text(row, key):
                problems.append(f"{where}: '{key}' is empty")
        holds = row.get("holds")
        # Identity, not membership: 1 == True, so `1 in (True, ...)` passes.
        if not (holds is True or holds is False or holds == "untested"):
            problems.append(f"{where}: 'holds' must be true, false or \"untested\"")
        if row.get("holds") is False and not _text(row, "found"):
            problems.append(f"{where}: a broken consensus must quote the "
                            f"contradiction in 'found'")


def check_finish(finish, names: set[str], problems: list[str]) -> list[dict]:
    if not isinstance(finish, dict):
        problems.append("finish.json: not an object")
        return []
    if finish.get("base") not in names | {"none"}:
        problems.append(f"finish.json: base '{finish.get('base')}' is not a "
                        f"candidate ({', '.join(sorted(names))}) or 'none'")
    work = finish.get("work")
    if not isinstance(work, list):
        problems.append("finish.json: 'work' must be a list")
        work = []
    for i, item in enumerate(work):
        if not isinstance(item, dict):
            problems.append(f"finish.json work[{i}]: not an object")
            continue
        for key in ("what", "to", "evidence"):
            if not _text(item, key):
                problems.append(f"finish.json work[{i}]: '{key}' is empty")
    open_items = finish.get("open")
    if not isinstance(open_items, list):
        problems.append("finish.json: 'open' must be a list")
        return []
    for i, item in enumerate(open_items):
        if not isinstance(item, dict) or not _text(item, "item"):
            problems.append(f"finish.json open[{i}]: 'item' is empty")
            continue
        readings = item.get("readings")
        if not (isinstance(readings, list) and len(readings) >= 2
                and all(isinstance(r, str) and r.strip() for r in readings)):
            problems.append(f"finish.json open[{i}]: needs at least two readings")
    return [o for o in open_items if isinstance(o, dict) and _text(o, "item")]


def check_workspace(workspace: Path) -> list[str]:
    """Every reason the workspace is not ready to apply; empty when it is."""
    problems: list[str] = []
    manifest = _load(workspace / "manifest.json", problems)
    if manifest is None:
        return problems

    inputs = [(workspace / "spec" / "request.md", manifest.get("request_sha256")),
              (workspace / "workspace" / "case.json", manifest.get("snapshot_sha256"))]
    inputs += [(workspace / "rollouts" / r["name"] / "response.md", r["sha256"])
               for r in manifest.get("rollouts", [])]
    for path, expected in inputs:
        rel = path.relative_to(workspace).as_posix()
        if not path.is_file():
            problems.append(f"{rel} is missing")
        elif sha256_file(path) != expected:
            problems.append(f"{rel} changed after init; a session must not "
                            f"edit what it judges")

    elim = _load(workspace / "ledger_elim.json", problems)
    if elim is not None:
        check_elim(elim, problems)
    fals = _load(workspace / "ledger_fals.json", problems)
    if fals is not None:
        check_fals(fals, problems)
    finish = _load(workspace / "finish.json", problems)
    open_items = []
    if finish is not None:
        names = {r["name"] for r in manifest.get("rollouts", [])}
        open_items = check_finish(finish, names, problems)

    diagnosis = workspace / "out" / "diagnosis.md"
    text = diagnosis.read_text(encoding="utf-8") if diagnosis.is_file() else ""
    if not text.strip():
        problems.append("out/diagnosis.md is missing or empty")
    else:
        for item in open_items:
            if item["item"].strip() not in text:
                problems.append(f"out/diagnosis.md does not name open question "
                                f"'{item['item'].strip()}'")
    return problems


def verification_record(workspace: Path, manifest: dict) -> dict:
    elim = json.loads((workspace / "ledger_elim.json").read_text(encoding="utf-8"))
    fals = json.loads((workspace / "ledger_fals.json").read_text(encoding="utf-8"))
    finish = json.loads((workspace / "finish.json").read_text(encoding="utf-8"))
    disagreements = elim["disagreements"]
    challenges = fals["challenges"]
    files = ("ledger_elim.json", "ledger_fals.json", "finish.json", "out/diagnosis.md")
    return {
        "method": "veriharness",
        "workspace": _shown(workspace),
        "rollouts": manifest["rollouts"],
        "base": finish["base"],
        "work_items": len(finish["work"]),
        "open": [o["item"] for o in finish["open"]],
        "disagreements": len(disagreements),
        "disagreements_unsettled": sum(1 for d in disagreements if unsettled(d)),
        "challenges": len(challenges),
        "consensus_broken": sum(1 for c in challenges if c.get("holds") is False),
        "consensus_untested": sum(1 for c in challenges if c.get("holds") == "untested"),
        "sha256": {name: sha256_file(workspace / name) for name in files},
    }


def apply_workspace(workspace: Path, refdb: Path) -> dict:
    problems = check_workspace(workspace)
    if problems:
        raise SystemExit("not applied:\n  " + "\n  ".join(problems))
    manifest = json.loads((workspace / "manifest.json").read_text(encoding="utf-8"))

    case_file = latest_case_file(manifest["design"], refdb)
    if case_file.name != manifest["case_file"]:
        raise SystemExit(f"the design's latest case is now {case_file.name}, not "
                         f"{manifest['case_file']}; this verification judged a "
                         f"run that is no longer the one under review")
    case = json.loads(case_file.read_text(encoding="utf-8"))
    current = sha256_bytes(_json_bytes(case_store.load_light(
        case_file, cache_dir=None).get("iterations", [])))
    if current != manifest["case_iterations_sha256"]:
        raise SystemExit(f"{case_file.name}'s recorded results changed after "
                         f"init; the sessions judged different evidence")

    agents = sorted({r["agent"] for r in manifest["rollouts"]})
    text = (workspace / "out" / "diagnosis.md").read_text(encoding="utf-8").strip()
    record = verification_record(workspace, manifest)
    grounding = request_review.record_review(
        case_file, case, "verified:" + "+".join(agents), text, verification=record)
    return {"case_file": case_file.name, "verification": record,
            "grounding": grounding}


def cmd_init(args: argparse.Namespace) -> None:
    rollouts = [parse_rollout(s) for s in args.rollout]
    workspace = init_workspace(args.design, rollouts, args.root, args.refdb)
    print(f"verification workspace: {_shown(workspace)}")
    print("next: dispatch review-resolver and review-challenger on it "
          "(concurrently, neither sees the other), then review-adjudicator; "
          f"then `python3 review_verify.py check --workspace {_shown(workspace)}`")


def cmd_check(args: argparse.Namespace) -> None:
    problems = check_workspace(args.workspace)
    if problems:
        print("not ready:\n  " + "\n  ".join(problems))
        raise SystemExit(1)
    print("ready to apply: inputs unchanged, records well-formed, "
          "open questions delivered")


def cmd_apply(args: argparse.Namespace) -> None:
    result = apply_workspace(args.workspace, args.refdb)
    record = result["verification"]
    print(f"  base {record['base']}, {record['work_items']} work item(s), "
          f"{len(record['open'])} open question(s), "
          f"{record['consensus_broken']} broken consensus position(s)")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refdb", type=Path, default=request_review.REFDB,
                    help=argparse.SUPPRESS)
    sub = ap.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="materialize a verification workspace")
    init.add_argument("--design", required=True)
    init.add_argument("--rollout", action="append", default=[], required=True,
                      help="<agent>=<path to one independent answer>; repeat")
    init.add_argument("--root", type=Path,
                      default=request_review.REFDB / "reviews" / "verify",
                      help="parent directory of the workspace")
    init.set_defaults(func=cmd_init)

    check = sub.add_parser("check", help="report why a workspace cannot be applied")
    check.add_argument("--workspace", required=True, type=Path)
    check.set_defaults(func=cmd_check)

    apply = sub.add_parser("apply", help="record the adjudicated diagnosis in the case")
    apply.add_argument("--workspace", required=True, type=Path)
    apply.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
