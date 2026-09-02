#!/usr/bin/env python3
"""Formalizes the human-in-the-loop E2E workflow this pipeline actually
needs whenever propose_repairs() can't auto-repair a failure: a human
(or a Claude Code session working with one) reads an OPEN case, decides
a subagent's judgment is worth getting, dispatches it, and the verdict
comes back into reference-db/ where the dashboard picks it up.

That loop was done by hand this session (see
reference-db/cases/sram_wrapper__2026-08-21.json's diagnosis field,
built via repeated ad hoc `python3 -c` JSON edits) — including a real
bug from it: a shell backtick inside one of those edits got interpreted
as command substitution and silently ate part of the text
("Magic `.mag` blocker" lost its backtick-quoted portion). This script
exists so that doesn't happen again: request/apply are the only two
ways to touch a case's `diagnosis` field and `human_in_the_loop` status,
both going through json.dump, never a hand-built shell string.

Usage:
    # 1. Generate a review request for the latest OPEN case of a design.
    #    Read the output file, decide which subagent(s) to dispatch
    #    (per its "relevant subagents" section), and actually run them
    #    (e.g. via the Agent tool, pointed at .claude/agents/<name>.md).
    python3 request_review.py request --design sram_wrapper

    # 2. Once a subagent has responded, apply its verdict back into the
    #    case file — appends to `diagnosis`, marks human_in_the_loop
    #    reviewed, records which agent(s) were consulted.
    python3 request_review.py apply --design sram_wrapper \
        --agent feedback-optimizer --response-file /path/to/response.txt
"""
import argparse
import os
import tempfile

import case_retrieval
import tool_retrieval
import verify_diagnosis
import json
import re
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"

# Which subagents are relevant to which real failure classes this
# pipeline has actually seen — see docs/superpowers/specs/
# 2026-08-21-autonomous-layout-agent-design.md's "Process mapping".
# Always offered: feedback-optimizer (the terminal decision-maker).
RELEVANT_AGENTS = {
    "physical_constraint": ["physical-constraint-evaluator", "feedback-optimizer"],
    "routing_generation": ["routing-candidate-evaluator", "feedback-optimizer"],
    "routing_candidate": ["routing-candidate-evaluator", "feedback-optimizer"],
    "verification_ppa": ["verification-ppa-evaluator", "feedback-optimizer"],
}


def latest_case(design: str) -> tuple[Path, dict]:
    index_file = REFDB / "index.json"
    index = json.loads(index_file.read_text(encoding="utf-8"))
    case_files = index.get(design, [])
    if not case_files:
        raise SystemExit(f"no reference-db cases found for design '{design}'")
    case_file = REFDB / "cases" / sorted(case_files)[-1]
    return case_file, json.loads(case_file.read_text(encoding="utf-8"))


def request_filename(design: str, case: dict, case_filename: str) -> str:
    """The request file's name, keyed to the case rather than the day.

    It was `<design>__<case date>__request.md`, and a design run twice on
    one day produces two cases with the same date — so the second run's
    request silently replaced the first on disk, including one already
    committed, while describing a different case.

    The case file already carries whatever distinguishes it: the store
    names a re-run `<design>__<date>__<HHMMSS>.json`. Reusing that stem
    means the name is unique exactly when the case is, and the four
    requests already committed keep the names they have — renaming those
    would orphan them from the cases they describe, which is the problem
    this is fixing.
    """
    stem = case_filename[:-len(".json")] if case_filename.endswith(".json") \
        else case_filename
    return f"{stem}__request.md" if stem else f"{design}__{case['date']}__request.md"


def carried_diagnosis(design: str, case_filename: str,
                      refdb: Path | str = REFDB) -> str | None:
    """The most recent review recorded on an EARLIER case of this design.

    A review is recorded into the case file it reviewed, and
    orchestrator.py writes a new case file per run. So the second run of
    a design read "Existing diagnosis: (none recorded yet)" while the
    case one run earlier held the verdict that had proposed the very
    candidates just executed — and the request asks the reviewer not to
    "re-derive what's already known" while handing over nothing to know
    it from.

    Returns None when this case has its own diagnosis: that one is
    current, and putting a superseded recommendation beside it with
    nothing to say which is which is worse than omitting it.

    Only this design's cases, because a verdict about counter4 is not
    context for aes — handing one over is the cross-design contamination
    verify_diagnosis exists to catch.
    """
    cases_dir = Path(refdb) / "cases"
    try:
        own = json.loads((cases_dir / case_filename).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if (own.get("diagnosis") or "").strip():
        return None

    earlier = sorted(p for p in cases_dir.glob(f"{design}__*.json")
                     if p.name < case_filename)
    for path in reversed(earlier):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if case.get("design") != design:
            continue
        text = (case.get("diagnosis") or "").strip()
        if text:
            # Named, so the reviewer can tell a verdict on an earlier run
            # from one on the run in front of them.
            return (f"(carried from an earlier case of this design, "
                    f"{path.name} — it reviewed a different run)\n\n{text}")
    return None


# Enough to show the shape of the search without burying the case the
# reviewer is meant to read. aes reached four cases in one evening; a
# design worked on for a week would otherwise arrive as a wall of old
# attempts ahead of its own data.
MAX_HISTORY_ROWS = 12


def _outcome(result: dict) -> str:
    """One line for what a candidate did, from its own recorded verdict."""
    verdict = result.get("verdict") or {}
    if verdict.get("passed"):
        area = verdict.get("area_um2")
        return "PASS" + (f", area {area:.0f}um2" if isinstance(area, (int, float)) else "")
    text = "; ".join(verdict.get("violations") or [])
    if not text:
        return "FAIL (no verdict recorded)"
    # The counts, not the sentences. A reviewer comparing attempts needs
    # "200 hold" next to "172 hold"; the full violation prose for a
    # dozen candidates would be longer than the case summary itself.
    counts = re.findall(r"(\d+) (setup|hold|max-slew|max-fanout)", text)
    if not counts:
        return "FAIL"
    return "FAIL: " + ", ".join(f"{n} {kind}" for n, kind in counts)


def attempt_history(design: str, case_filename: str,
                    refdb: Path | str = REFDB) -> str | None:
    """What has already been tried for this design, and how it turned out.

    Carrying the previous verdict was half the fix. A verdict is a
    proposal; it says what someone thought was worth trying next and
    says nothing about whether it worked. So the loop kept recommending
    things it had already disproved — iteration 3 raised
    PL/GRT_RESIZER_HOLD_SLACK_MARGIN to 0.3/0.25 and hold went from 172
    violations to 200, and the next review proposed those same values,
    calling them "verified in the earlier case".

    The candidates are the record of what the proposals did, so they
    travel too: each earlier candidate's overrides beside the counts its
    verdict recorded. Newest first, bounded, and never including the
    case being reviewed — its own candidates are in the file the
    reviewer is already reading, and repeating them as history invites
    reading this run's results as a previous run's.
    """
    cases_dir = Path(refdb) / "cases"
    rows = []
    earlier = sorted(p for p in cases_dir.glob(f"{design}__*.json")
                     if p.name < case_filename)
    for path in reversed(earlier):
        try:
            case = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if case.get("design") != design:
            continue
        for iteration in case.get("iterations", []):
            for result in iteration.get("results", []):
                overrides = result.get("overrides")
                rows.append(
                    f"- `{result.get('tag')}` "
                    f"{json.dumps(overrides or {}, sort_keys=True)} "
                    f"-> {_outcome(result)}  ({path.name})")
                if len(rows) >= MAX_HISTORY_ROWS:
                    break
            if len(rows) >= MAX_HISTORY_ROWS:
                break
        if len(rows) >= MAX_HISTORY_ROWS:
            break

    if not rows:
        return None
    return ("## Already tried for this design (newest first)\n\n"
            "Each line is a real recorded run: the configuration, then what\n"
            "its own verdict counted. A configuration listed here with a bad\n"
            "outcome has been tested and failed — proposing it again needs a\n"
            "reason this case supplies.\n\n" + "\n".join(rows))


def cmd_request(args: argparse.Namespace) -> None:
    case_file, case = latest_case(args.design)

    if case.get("winner_tag"):
        print(f"'{args.design}' latest case ({case['date']}) already has a "
              f"winner ({case['winner_tag']}) — no review needed.")
        return

    all_results = [r for it in case["iterations"] for r in it["results"]]
    stages_hit = sorted({r["stage"] for r in all_results if r.get("stage")})
    relevant = {"feedback-optimizer"}
    for stage in stages_hit:
        relevant.update(RELEVANT_AGENTS.get(stage, []))

    lines = [
        f"# Human-in-the-loop review request — {args.design} ({case['date']})",
        "",
        # A list, not three consecutive lines. Markdown joins consecutive
        # lines into one paragraph, so these three separate facts
        # rendered as a single run-on sentence in the console — correct
        # markdown, and not what the text means. Bullets say "three
        # things" in both a renderer and a terminal.
        f"- Case file: {case_file.relative_to(REPO_ROOT).as_posix()}",
        f"- Outcome: {case['outcome']}",
        f"- Stages the real run outcomes hit: "
        f"{', '.join(stages_hit) or '(none classified)'}",
        "",
        "## Relevant subagents (dispatch these, in this order)",
        "",
    ]
    for name in sorted(relevant):
        agent_file = AGENTS_DIR / f"{name}.md"
        lines.append(f"- `{name}` — {agent_file.relative_to(REPO_ROOT).as_posix()}"
                      f"{'' if agent_file.exists() else '  (WARNING: file not found)'}")
    # Precedent from the rest of reference-db. Without this a review
    # starts cold: judging an RSZ-0090 with no sight of the other times
    # this pipeline hit RSZ-0090 and what measurement showed. Retrieved
    # by shared tool error code, so the match reason is stateable and
    # can be discounted when it does not actually apply.
    try:
        corpus = case_retrieval.load_cases()
        lines += ["", case_retrieval.precedent_block(case, corpus, top=3)]
    except Exception as e:  # noqa: BLE001 - recorded, never fatal
        lines += ["", f"## Precedent from reference-db\n\n(retrieval failed: {e})"]

    # Which measurement answers this, alongside what happened before.
    # Precedent alone leaves a reviewer reasoning over text somebody
    # already wrote, and that was measured: replaying three recorded
    # cases through the configured model scored grounded 3/3 and
    # root-cause recall 1/10 — while every real advance in those cases
    # came from running something new. None of the eight agent
    # definitions mentions a single tool of this pipeline's, so the
    # tools cannot be reached for by an agent that was never told they
    # exist.
    try:
        lines += ["", tool_retrieval.guidance_block(case)]
    except Exception as e:  # noqa: BLE001 - recorded, never fatal
        lines += ["", f"## Measurements that apply\n\n(retrieval failed: {e})"]

    lines += [
        "",
        "## Existing diagnosis (read before dispatching — don't re-derive"
        " what's already known)",
        "",
        (case.get("diagnosis")
         or carried_diagnosis(args.design, case_file.name)
         or "(none recorded yet)"),
        "",
        # What the proposals above actually did. Without it a reviewer
        # reads recommendations with no record of their outcomes, and
        # re-proposes settings this design has already ruled out.
        attempt_history(args.design, case_file.name) or "",
        "",
        "## What to do",
        "",
        "1. Read the subagent .md file(s) above for their actual scope/decision tree.",
        "2. Dispatch each via the Agent tool (or run manually), giving it this file's"
        " context plus the full case file.",
        "3. Once you have a real response, run:",
        "",
        f"   python3 request_review.py apply --design {args.design} "
        "--agent <name> --response-file <path>",
    ]

    out_dir = REFDB / "reviews"
    out_dir.mkdir(exist_ok=True)
    out_file = out_dir / request_filename(args.design, case, case_file.name)
    # self_improve.scan_design() regenerates this file on *every* scan for
    # any OPEN, unreviewed design — no check for "a request already
    # exists" — so two overlapping scans (two browser tabs on the health
    # page, a manual run racing a poll) can both be writing this same path
    # at once. Path.write_text() truncates then writes as two separate
    # steps; a second writer's truncate landing in between, or either
    # writer being killed mid-write, leaves the file empty. Hit for real:
    # reference-db/reviews/cdc_twoclock__2026-08-28__request.md went from
    # a full request to 0 bytes this way. Write to a temp file in the same
    # directory and os.replace() it in — atomic at the OS level, so any
    # reader sees either the old complete file or the new one, never a
    # partial write.
    fd, tmp_path = tempfile.mkstemp(dir=out_dir, prefix=f"{out_file.name}.", suffix=".tmp")
    try:
        # Explicit encoding, not the platform default: this text has real
        # em-dashes, and open()'s default encoding is the OS locale
        # (cp949 on a Korean-locale Windows box), which can't represent
        # them — the write raised UnicodeEncodeError. Caught here rather
        # than corrupting anything because the temp file is a fresh file,
        # not out_file; before the atomic-rename change above, the same
        # error hit *after* write_text() had already truncated out_file,
        # which is what actually emptied cdc_twoclock's review request.
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        os.replace(tmp_path, out_file)
    except BaseException:
        os.unlink(tmp_path)
        raise
    print(f"review request written to {out_file.relative_to(REPO_ROOT)}")


def cmd_apply(args: argparse.Namespace) -> None:
    case_file, case = latest_case(args.design)
    response_text = Path(args.response_file).read_text(encoding="utf-8").strip()
    if not response_text:
        raise SystemExit("response file is empty — nothing to apply")

    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = (
        f"\n\n[{timestamp}] {args.agent} subagent verdict "
        f"(dispatched via human-in-the-loop review, not self-assessed):\n"
        f"{response_text}"
    )
    case["diagnosis"] = case.get("diagnosis", "") + entry

    reviews = case.setdefault("human_in_the_loop", [])
    reviews.append({
        "agent": args.agent,
        "reviewed_at": timestamp,
        "summary": response_text[:280] + ("…" if len(response_text) > 280 else ""),
    })

    # The reviewer node, run where a verdict actually enters the case.
    #
    # verify_diagnosis has existed for a while and was reachable only
    # from self_improve.py and the MCP server — an optional side tool
    # rather than part of the flow. So a review could be applied, become
    # the case's diagnosis, and be read by every later reviewer without
    # anything having checked that the error codes and candidate tags it
    # cites are in this case's own data. That is precisely the failure
    # it was written for: sram_wrapper's first diagnosis blamed pins
    # nobody had looked at.
    #
    # Recorded, not enforced. A human may legitimately cite something
    # new — a liberty file, another design — and blocking that would
    # push people to edit the JSON by hand. What it must not do is enter
    # unnoticed.
    grounding = verify_diagnosis.verify_case(
        {**case, "diagnosis": response_text,
         "iterations": case.get("iterations", [])})
    reviews[-1]["grounding"] = grounding

    case_file.write_text(json.dumps(case, indent=2), encoding="utf-8")
    print(f"applied {args.agent}'s response to {case_file.relative_to(REPO_ROOT)} "
          f"(diagnosis field, human_in_the_loop[{len(reviews) - 1}])")
    if grounding.get("checked"):
        bad = (grounding.get("ungrounded_error_codes", [])
               + grounding.get("ungrounded_candidate_tags", []))
        if bad:
            print(f"  NOTE: this review cites {len(bad)} reference(s) not in "
                  f"the case's own recorded data: {', '.join(bad)}")
        else:
            print("  grounding check: every cited error code and candidate "
                  "tag appears in this case's recorded data")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    req = sub.add_parser("request", help="write a review request for the latest OPEN case")
    req.add_argument("--design", required=True)
    req.set_defaults(func=cmd_request)

    app = sub.add_parser("apply", help="apply a subagent's response back into the case")
    app.add_argument("--design", required=True)
    app.add_argument("--agent", required=True, help="subagent name that produced the response")
    app.add_argument("--response-file", required=True, type=Path)
    app.set_defaults(func=cmd_apply)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
