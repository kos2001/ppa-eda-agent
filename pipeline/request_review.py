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
import verify_diagnosis
import json
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
    index = json.loads(index_file.read_text())
    case_files = index.get(design, [])
    if not case_files:
        raise SystemExit(f"no reference-db cases found for design '{design}'")
    case_file = REFDB / "cases" / sorted(case_files)[-1]
    return case_file, json.loads(case_file.read_text())


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
        f"Case file: {case_file.relative_to(REPO_ROOT).as_posix()}",
        f"Outcome: {case['outcome']}",
        f"Stages the real run outcomes hit: {', '.join(stages_hit) or '(none classified)'}",
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

    lines += [
        "",
        "## Existing diagnosis (read before dispatching — don't re-derive"
        " what's already known)",
        "",
        case.get("diagnosis", "(none recorded yet)"),
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
    out_file = out_dir / f"{args.design}__{case['date']}__request.md"
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
    response_text = Path(args.response_file).read_text().strip()
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

    case_file.write_text(json.dumps(case, indent=2))
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
