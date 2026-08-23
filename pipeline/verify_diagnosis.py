#!/usr/bin/env python3
"""Cross-checks a case's diagnosis prose against that case's own real
recorded data, flagging references that aren't grounded in it.

Idea borrowed from github.com/kos2001/strongarm-sizing-console's
`scripts/agent_selftest.py`, whose grading principle is the useful part:
an agent's answer is judged by *independent cross-validation against what
the backend actually measured*, not by string similarity to an expected
answer. That repo re-runs golden tasks through a live agent endpoint and
checks the numbers it claimed against a real simulation. This pipeline
has no always-on agent endpoint and its "answers" are the diagnoses
already stored in reference-db, so the same principle applies to the
artifacts directly: does this diagnosis reference things the run really
produced?

This matters here because the failure it guards has already happened for
real: sram_wrapper's first diagnosis blamed the macro's clk0/clk1 pins
without ever opening the .lib, and had to be rewritten after the actual
liberty file was read (the corrected text, and a note about the mistake,
are still in that case). A confident, plausible, ungrounded diagnosis is
the failure mode worth automating a check for.

DELIBERATELY NARROW — what this does NOT do:

It cannot tell you a diagnosis is *correct*. Reasoning about a real
physical root cause is exactly the judgment `request_review.py` escalates
to a human/subagent, and a regex claiming to settle it would be worse
than no check at all. What it checks is far weaker and actually
decidable: every EDA error code and every candidate tag the prose cites
must appear in the case's own recorded data. That catches invented
references and stale copy-paste from another design — not wrong physics.

Usage:
    python3 verify_diagnosis.py                 # all designs
    python3 verify_diagnosis.py --design X
    python3 verify_diagnosis.py --strict        # exit 1 if anything ungrounded
"""
import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"

# Tool error codes as OpenLane/OpenROAD/Magic actually emit them
# (PDN-0185, RSZ-0090, GRT-0097, DRT-0001...). Four-digit suffix is the
# real shape of every code seen in this repo's cases so far.
ERROR_CODE_RE = re.compile(r"\b[A-Z]{2,4}-\d{4}\b")

# Candidate tags as this pipeline really generates them: a prefix from
# run_spec.json ("cand", "sweep", ...) then a hyphen. The hyphen is load
# bearing — without it the pattern also matches the ordinary English word
# "candidate", which is a false positive this checker hit on its first
# real run against sram_wrapper's diagnosis.
TAG_RE = re.compile(r"\b(?:cand|sweep)-[A-Za-z0-9_.]+(?:-[A-Za-z0-9_.]+)*\b")


def case_files() -> dict:
    index_file = REFDB / "index.json"
    if not index_file.exists():
        return {}
    return json.loads(index_file.read_text())


def diagnosis_text(case: dict) -> str:
    """All prose a human/subagent wrote about this case: the diagnosis
    field plus every human_in_the_loop review summary."""
    parts = [case.get("diagnosis") or ""]
    parts += [r.get("summary") or "" for r in case.get("human_in_the_loop", [])]
    return "\n".join(parts)


def recorded_evidence(case: dict) -> tuple[str, set]:
    """(all real recorded error text, all real candidate tags) for a case."""
    errors = []
    tags = set()
    for iteration in case.get("iterations", []):
        for result in iteration.get("results", []):
            tags.add(result.get("tag"))
            if result.get("error"):
                errors.append(result["error"])
    tags.discard(None)
    return "\n".join(errors), tags


def verify_case(case: dict) -> dict:
    prose = diagnosis_text(case)
    if not prose.strip():
        return {"checked": False, "reason": "no diagnosis or review text"}

    error_text, real_tags = recorded_evidence(case)
    recorded_codes = set(ERROR_CODE_RE.findall(error_text))
    cited_codes = set(ERROR_CODE_RE.findall(prose))
    cited_tags = set(TAG_RE.findall(prose))

    return {
        "checked": True,
        "cited_error_codes": sorted(cited_codes),
        "ungrounded_error_codes": sorted(cited_codes - recorded_codes),
        "cited_candidate_tags": sorted(cited_tags),
        "ungrounded_candidate_tags": sorted(cited_tags - real_tags),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", help="check only this design (default: all)")
    ap.add_argument("--strict", action="store_true",
                     help="exit 1 if any reference is ungrounded")
    args = ap.parse_args()

    index = case_files()
    designs = [args.design] if args.design else sorted(index)
    problems = 0

    print("=== diagnosis grounding check ===")
    print("(reference groundedness only — NOT a correctness check; see module docstring)\n")
    for design in designs:
        for name in index.get(design, []):
            case = json.loads((REFDB / "cases" / name).read_text())
            report = verify_case(case)
            if not report["checked"]:
                print(f"{name}: skipped — {report['reason']}")
                continue
            bad_codes = report["ungrounded_error_codes"]
            bad_tags = report["ungrounded_candidate_tags"]
            status = "OK" if not (bad_codes or bad_tags) else "UNGROUNDED"
            print(f"{name}: {status}")
            print(f"  error codes cited: {report['cited_error_codes'] or '(none)'}")
            if bad_codes:
                problems += len(bad_codes)
                print(f"  !! cited but never recorded in this case's real errors: {bad_codes}")
            if report["cited_candidate_tags"]:
                print(f"  candidate tags cited: {report['cited_candidate_tags']}")
            if bad_tags:
                problems += len(bad_tags)
                print(f"  !! cited tags that don't exist in this case: {bad_tags}")
            print()

    if problems:
        print(f"{problems} ungrounded reference(s) — read the diagnosis and either "
              f"correct it or confirm the reference came from a log the case "
              f"didn't capture.")
        return 1 if args.strict else 0
    print("all cited references are grounded in each case's own recorded data.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
