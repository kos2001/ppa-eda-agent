#!/usr/bin/env python3
"""Self-improvement loop entry point: scans every design's latest real
reference-db case and closes the gap between "AI feedback/repair/
optimization" (process stage 8, propose_repairs()'s mechanical patterns)
and "human-in-the-loop review" (request_review.py, for the failures
propose_repairs() can't cover) — automatically, on a schedule, instead of
a human remembering to check.

What "self-improvement" means here concretely, in order of increasing
automation (see the report each run prints):

1. Auto-repair coverage: what real fraction of this design's non-passing
   candidate runs were fixed by propose_repairs()'s known patterns
   (PDN strap-width, die-too-small) vs. needed a human/subagent decision?
   This is a real, honest metric — it goes up only when a *new* pattern
   gets added to orchestrator.py's propose_repairs() (see #3), never by
   redefining "covered".
2. Review backlog: any case that's OPEN (no winner) and has never had a
   pipeline/request_review.py review request generated gets one
   generated automatically by this script — so a human/Claude session
   never has to remember which design needs attention next; run this
   script and read its report.
3. Pattern promotion (manual, deliberately not automatic): if a
   human_in_the_loop review entry describes a fix that's mechanically
   expressible as a new propose_repairs() branch (a real config override
   tied to a real error-text pattern — see PDN_STRAP_ERROR and
   DIE_TOO_SMALL_ERROR in orchestrator.py for the bar), a human should
   add it there, turning one-off diagnosis into durable automated
   capability. This script flags candidate reviews for that (any
   human_in_the_loop entry whose case is still OPEN — meaning the
   review concluded there's nothing propose_repairs() can do *yet*,
   which is exactly the signal that either a new pattern is needed or
   genuinely doesn't exist). It does not write code changes itself —
   deciding whether a diagnosis is truly a generalizable pattern (not
   overfit to one design) needs judgment, not a regex.

Usage:
    python3 self_improve.py               # scan all designs, print report
    python3 self_improve.py --design X    # scan one design only
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGNS_DIR = REPO_ROOT / "pipeline" / "designs"
REFDB = REPO_ROOT / "reference-db"

# Kept in sync with orchestrator.py's own pattern constants by hand (not
# imported, to keep this script runnable even if orchestrator.py's
# internals change shape) — used only to report *known* coverage, not to
# re-implement repair logic.
KNOWN_PATTERNS = {
    "PDN strap-width (FP_CORE_UTIL/DIE_AREA)": "Insufficient width",
    "die too small for core margins (DIE_AREA)": "core_area",
}


def latest_case(design: str) -> dict | None:
    index_file = REFDB / "index.json"
    if not index_file.exists():
        return None
    index = json.loads(index_file.read_text())
    case_files = index.get(design, [])
    if not case_files:
        return None
    return json.loads((REFDB / "cases" / sorted(case_files)[-1]).read_text())


def auto_repair_coverage(case: dict) -> tuple[int, int, list[str]]:
    """(covered, total) non-passing candidate runs, plus which known
    pattern each covered one matched — real counting from real error
    text, not a self-reported flag.
    """
    covered = 0
    total = 0
    matched = []
    for it in case["iterations"]:
        for r in it["results"]:
            if r.get("verdict", {}).get("passed"):
                continue
            total += 1
            error = r.get("error", "")
            for name, pattern in KNOWN_PATTERNS.items():
                if pattern in error:
                    covered += 1
                    matched.append(name)
                    break
    return covered, total, matched


def scan_design(design: str) -> dict:
    case = latest_case(design)
    if case is None:
        return {"design": design, "status": "no reference-db case yet"}

    covered, total, matched = auto_repair_coverage(case)
    is_open = not case.get("winner_tag")
    reviewed = bool(case.get("human_in_the_loop"))

    review_request_path = None
    if is_open and not reviewed:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "pipeline" / "request_review.py"),
             "request", "--design", design],
            capture_output=True, text=True, cwd=REPO_ROOT / "pipeline",
        )
        if result.returncode == 0:
            review_request_path = result.stdout.strip().rsplit(" ", 1)[-1]

    return {
        "design": design,
        "date": case["date"],
        "status": "CLOSED" if not is_open else ("OPEN, reviewed" if reviewed else "OPEN, needs review"),
        "auto_repair_coverage": f"{covered}/{total}" if total else "n/a (nothing failed)",
        "patterns_matched": sorted(set(matched)),
        "review_request_generated": review_request_path,
        "pattern_promotion_candidate": is_open and reviewed,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--design", help="scan only this design (default: all)")
    args = ap.parse_args()

    designs = [args.design] if args.design else sorted(
        d.name for d in DESIGNS_DIR.iterdir() if d.is_dir()
    )

    print(f"=== self-improvement scan: {len(designs)} design(s) ===\n")
    promotion_candidates = []
    for design in designs:
        report = scan_design(design)
        print(f"{design}:")
        for k, v in report.items():
            if k == "design":
                continue
            print(f"  {k}: {v}")
        if report.get("pattern_promotion_candidate"):
            promotion_candidates.append(design)
        print()

    if promotion_candidates:
        print("=== pattern promotion candidates (human judgment needed) ===")
        print("These designs have a human-in-the-loop review on an OPEN case —")
        print("read their diagnosis for a fix that might generalize into a new")
        print("propose_repairs() branch in pipeline/orchestrator.py:")
        for d in promotion_candidates:
            print(f"  - {d}")
    else:
        print("no pattern-promotion candidates right now.")


if __name__ == "__main__":
    main()
