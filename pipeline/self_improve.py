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
2. Review backlog: any case that's OPEN (no winner) *for a reason a
   human can actually act on* and has never had a
   pipeline/request_review.py review request generated gets one
   generated automatically by this script — so a human/Claude session
   never has to remember which design needs attention next; run this
   script and read its report.

   Not every OPEN case is a review case. orchestrator.py's
   STOP_REASONS distinguishes two genuinely different ways a run ends
   without a winner, and they need opposite responses:

   - `no_repairable_failures` — propose_repairs() had no pattern for
     what failed. The machine is out of ideas; a human/subagent is the
     only way forward. THIS is a review case.
   - `max_iterations_reached` — propose_repairs() was still proposing
     new candidates each iteration and simply ran out of budget. A
     human has nothing to add here that another iteration wouldn't;
     escalating is a false alarm that trains people to ignore the
     backlog. The right action is re-running with a higher
     max_iterations, which this script reports as a concrete command
     instead of generating a review request.

   (This split is the "iteration budgeting and termination logic"
   point from arxiv.org/html/2605.06936v3 — hard tasks benefit from
   additional budget where easy ones saturate early, so "hit the cap"
   and "genuinely stuck" must not be collapsed into one status.)
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

import case_retrieval
import surrogate
import verify_diagnosis

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


def budget_retry_command(design: str, case: dict) -> str | None:
    """Concrete re-run command for a case that only hit its iteration
    cap — reads the design's real run_spec.json for the budget it
    actually used, so the suggested number is grounded, not invented.
    Returns None if the run_spec can't be read (nothing to base it on).
    """
    run_spec_path = DESIGNS_DIR / design / "run_spec.json"
    try:
        used = json.loads(run_spec_path.read_text()).get("max_iterations", 3)
    except (OSError, json.JSONDecodeError):
        return None
    iterations_run = len(case.get("iterations", []))
    # Double the budget that proved insufficient rather than picking an
    # arbitrary number — matches propose_repairs()'s own DIE_AREA
    # doubling convention, and each iteration is a real (slow) OpenLane
    # run, so a big jump is not free.
    suggested = max(used, iterations_run) * 2
    return (f"python3 pipeline/orchestrator.py --design pipeline/designs/{design} "
            f"--run-spec pipeline/designs/{design}/run_spec.json "
            f"--max-iterations {suggested}")


def scan_design(design: str) -> dict:
    case = latest_case(design)
    if case is None:
        return {"design": design, "status": "no reference-db case yet"}

    covered, total, matched = auto_repair_coverage(case)
    is_open = not case.get("winner_tag")
    reviewed = bool(case.get("human_in_the_loop"))
    # Cases written before orchestrator.py recorded stop_reason have
    # None here — treat those as review-eligible (the old behaviour),
    # since we can't tell which kind of OPEN they were.
    stop_reason = case.get("stop_reason")
    budget_exhausted = stop_reason == "max_iterations_reached"

    # A budget-exhausted case is not a review case (see this module's
    # docstring): propose_repairs() was still producing candidates, so
    # a human/subagent has nothing to add that another iteration
    # wouldn't. Suggest the re-run instead of filing a false alarm.
    needs_review = is_open and not reviewed and not budget_exhausted

    review_request_path = None
    if needs_review:
        result = subprocess.run(
            [sys.executable, str(REPO_ROOT / "pipeline" / "request_review.py"),
             "request", "--design", design],
            capture_output=True, text=True, cwd=REPO_ROOT / "pipeline",
        )
        if result.returncode == 0:
            review_request_path = result.stdout.strip().rsplit(" ", 1)[-1]

    if not is_open:
        status = "CLOSED"
    elif reviewed:
        status = "OPEN, reviewed"
    elif budget_exhausted:
        status = "OPEN, iteration budget exhausted (not a review case)"
    else:
        status = "OPEN, needs review"

    # Grounding of the diagnosis prose against this case's own recorded
    # data (verify_diagnosis.py). Reported here rather than as a separate
    # script nobody remembers to run — a check outside the loop is a
    # check that doesn't happen. Only surfaced when something is actually
    # ungrounded, so a clean scan stays readable.
    grounding = verify_diagnosis.verify_case(case)
    ungrounded = (grounding.get("ungrounded_error_codes", [])
                  + grounding.get("ungrounded_candidate_tags", []))

    return {
        "design": design,
        "date": case["date"],
        "status": status,
        "stop_reason": stop_reason,
        "ungrounded_diagnosis_references": ungrounded or None,
        "auto_repair_coverage": f"{covered}/{total}" if total else "n/a (nothing failed)",
        "patterns_matched": sorted(set(matched)),
        "review_request_generated": review_request_path,
        "retry_with_more_budget": budget_retry_command(design, case) if budget_exhausted else None,
        # A budget-exhausted case tells us nothing about whether a new
        # propose_repairs() pattern is needed — the existing ones were
        # still firing. Only a reviewed, genuinely-stuck case does.
        "pattern_promotion_candidate": is_open and reviewed and not budget_exhausted,
    }


def dataset_status() -> dict:
    """Whether reference-db can yet support a surrogate, and what it
    would take.

    Part of the loop rather than a separate errand because "collect more
    data" is only useful when it is aimed. counter4's area does not move
    with FP_CORE_UTIL at all, so more points on that axis add samples of
    a flat function; the loop should say which designs are short and let
    the answer stay "not yet" until the numbers change it.
    """
    try:
        data = surrogate.load_dataset()
    except Exception as e:  # noqa: BLE001 - reporting, never fatal
        return {"error": str(e)}
    report = surrogate.dataset_report(data)
    # Both targets, because they answer different questions and draw on
    # different amounts of data. Predicting area needs a run that reached
    # signoff; predicting whether it will get there at all uses every
    # configuration ever attempted — including the designs that always
    # crash and therefore contribute nothing to the first.
    evaluations = {t: surrogate.evaluate(data, t) for t in surrogate.TARGETS}
    # Re-derived every scan rather than trusted from when it was set:
    # the best neighbourhood size is a property of how much data there
    # is, and that changes underneath it.
    tuning = {t: surrogate.best_k(data, t) for t in surrogate.TARGETS}
    evaluation = evaluations["area_um2"]
    short = {
        name: d["with_area"]
        for name, d in report["per_design"].items()
        if d["with_area"] <= surrogate.MIN_SAMPLES
    }
    return {
        "distinct_configs": report["distinct_configs"],
        "trainable_designs": report["trainable"],
        "needs_more_runs": short,
        "evaluable_at": report["evaluable_at"],
        "verdict": evaluation["verdict"],
        "model_mae": evaluation["model_mae"],
        "baseline_mae": evaluation["baseline_mae"],
        "targets": [
            {
                "field": name,
                "n_scored": ev["n_scored"],
                "n_total": ev["n_total"],
                "accuracy": ev["accuracy"],
                "baseline_accuracy": ev["baseline_accuracy"],
                "model_mae": ev["model_mae"],
                "baseline_mae": ev["baseline_mae"],
                "verdict": ev["verdict"],
                "best_k": (tuning[name].get("best") or {}).get("k"),
                "current_k": surrogate.default_k(name),
            }
            for name, ev in evaluations.items()
        ],
    }


def ungrounded_reviews(designs: list[str]) -> list[dict]:
    """Reviews already in the store whose citations are not in their own
    case. Recorded at apply time now, but earlier reviews predate that
    gate, so the loop re-checks the whole store each pass."""
    out = []
    for design in designs:
        case = latest_case(design)
        if not case:
            continue
        for review in case.get("human_in_the_loop", []) or []:
            grounding = verify_diagnosis.verify_case(
                {**case, "diagnosis": review.get("summary", "")})
            if not grounding.get("checked"):
                continue
            bad = (grounding.get("ungrounded_error_codes", [])
                   + grounding.get("ungrounded_candidate_tags", []))
            if bad:
                out.append({"design": design, "agent": review.get("agent"),
                            "ungrounded": bad})
    return out


def scan_all(designs: list[str] | None = None) -> dict:
    """The whole self-improvement scan as data, not printed text.

    main() has always produced this and thrown it away as terminal
    output, so the one view that says *where to improve next* was
    invisible to the console. Same code path, so the report a person
    reads and the panel the dashboard renders cannot disagree.
    """
    if designs is None:
        designs = sorted(d.name for d in DESIGNS_DIR.iterdir() if d.is_dir())
    reports = [scan_design(d) for d in designs]
    return {
        "designs": reports,
        "budget_retries": [
            {"design": r["design"], "command": r["retry_with_more_budget"]}
            for r in reports if r.get("retry_with_more_budget")
        ],
        "pattern_promotion_candidates": [
            r["design"] for r in reports if r.get("pattern_promotion_candidate")
        ],
        "learning_data": dataset_status(),
        "ungrounded_reviews": ungrounded_reviews(designs),
        "retrieval": retrieval_status(),
    }


def retrieval_status() -> dict:
    """What the case store can currently retrieve precedent from.

    Reported because retrieval quietly degrades: a corpus where most
    cases share no failure signature returns nothing useful, and the
    review request just says "this appears to be new" every time. The
    number worth watching is how many cases have a signature at all.
    """
    try:
        corpus = case_retrieval.load_cases()
    except Exception as e:  # noqa: BLE001 - reporting, never fatal
        return {"error": str(e)}
    with_sig, all_sigs = 0, set()
    for case in corpus:
        sigs = case_retrieval.case_signatures(case)
        if sigs:
            with_sig += 1
            all_sigs |= sigs
    coverage = []
    for case in corpus:
        hits = case_retrieval.similar(case, corpus, top=3)
        coverage.append({
            "design": case.get("design"),
            "date": case.get("date"),
            "signatures": sorted(case_retrieval.case_signatures(case)),
            "precedent_found": len(hits),
        })
    return {
        "cases": len(corpus),
        "cases_with_failure_signature": with_sig,
        "distinct_signatures": sorted(all_sigs),
        "per_case": coverage,
        "cases_without_precedent": [
            c for c in coverage if c["precedent_found"] == 0
        ],
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
    budget_retries = []
    for design in designs:
        report = scan_design(design)
        print(f"{design}:")
        for k, v in report.items():
            if k == "design" or v is None:
                continue
            print(f"  {k}: {v}")
        if report.get("pattern_promotion_candidate"):
            promotion_candidates.append(design)
        if report.get("retry_with_more_budget"):
            budget_retries.append((design, report["retry_with_more_budget"]))
        print()

    # Reported before pattern promotion: this is the actionable-right-now
    # bucket (just re-run it), where promotion needs human judgment.
    if budget_retries:
        print("=== ran out of iteration budget (machine-actionable) ===")
        print("These stopped only because they hit max_iterations while")
        print("auto-repair was still proposing candidates — no human decision")
        print("needed, just more budget:")
        for design, cmd in budget_retries:
            print(f"  - {design}:\n      {cmd}")
        print()

    # Is the store yet able to support a learned model? Kept in the loop
    # so the answer is re-measured every pass rather than asserted once.
    status = dataset_status()
    print("=== learning-data status ===")
    if "error" in status:
        print(f"  could not read the dataset: {status['error']}")
    else:
        print(f"  distinct configurations: {status['distinct_configs']}"
              f"  (a design becomes evaluable at {status['evaluable_at']})")
        print(f"  verdict: {status['verdict']}")
        if status["model_mae"] is not None:
            print(f"  surrogate MAE {status['model_mae']:.3f} vs "
                  f"predict-the-mean {status['baseline_mae']:.3f}")
        if status["needs_more_runs"]:
            print("  designs short of the threshold "
                  "(runs with a recorded area):")
            for name, n in sorted(status["needs_more_runs"].items()):
                print(f"    - {name}: {n}")
            print("  Aim collection at parameters that move the target — "
                  "sweeping one that does not just adds flat samples.")
    print()

    stale = ungrounded_reviews(designs)
    if stale:
        print("=== reviews citing things not in their own case ===")
        print("Each of these names an error code or candidate tag that does")
        print("not appear in that case's recorded data — invented, or copied")
        print("from another design. Not proof of a wrong conclusion; proof")
        print("that a reference cannot be checked.")
        for item in stale:
            print(f"  - {item['design']} / {item['agent']}: "
                  f"{', '.join(item['ungrounded'])}")
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
