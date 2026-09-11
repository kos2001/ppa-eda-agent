r"""Re-score stored gf180mcu verdicts with the KLayout DRC they never had.

gf180_drc.py closed the gap for new runs: OpenLane 2.3.10 skips KLayout
DRC on every PDK but sky130, so score() filed each gf180mcu candidate
as "KLayout DRC never checked", which blocks a pass. The store was
scored before that existed. Measured 2026-09-11: 141 gf180mcu
candidates in reference-db still carry that unverified entry, and 89
of them have their run directory — final GDS and metrics.json — on
this machine.

This runs the same deck, through the same gf180_drc.run(), on each of
those runs, and re-scores the candidate through the same score() the
live path uses: the run's own metrics.json plus the one metric the
deck produces. Nothing else in the result is touched; keys score()
does not produce (power_activity, operating_point, the clock coverage)
are kept from the stored verdict. When a case's candidates now include
a passing one, its winner_tag / outcome / stop_reason are recomputed
with orchestrator.pick_winner — the same function that set them — and
the case records what changed under `rescored`, with the date and the
deck's report path, so a reader can tell a verdict measured today from
one measured on the day of the run.

Each run directory is checked once even when several cases reference
it (the store keeps one dated file per design per day plus one per
re-run; 89 candidates map to 71 runs).

Usage:
    rescore_gf180_drc.py            # list what would be re-scored
    rescore_gf180_drc.py --write    # run the deck and rewrite the cases
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

import gf180_drc
import orchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGNS = REPO_ROOT / "pipeline" / "designs"
CASES = REPO_ROOT / "reference-db" / "cases"

KLAYOUT_LABEL = "KLayout DRC error(s)"


def local_run_dir(design: str, tag: str) -> Path | None:
    rd = DESIGNS / design / "runs" / tag
    if (rd / "final" / "metrics.json").is_file() and list((rd / "final" / "gds").glob("*.gds")):
        return rd
    return None


def targets_for(design: str) -> dict:
    spec = DESIGNS / design / "run_spec.json"
    if not spec.is_file():
        return {}
    return json.loads(spec.read_text(encoding="utf-8")).get("targets", {})


def needs_rescoring(result: dict) -> bool:
    v = result.get("verdict") or {}
    return (str(result.get("pdk") or "").startswith("gf180mcu")
            and any(KLAYOUT_LABEL in u for u in v.get("unverified") or []))


def plan(cases_dir: Path = CASES) -> list[dict]:
    """Every (case, candidate) that can be re-scored from a local run."""
    out = []
    for path in sorted(cases_dir.glob("*.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        for it_i, it in enumerate(case.get("iterations", [])):
            for r_i, r in enumerate(it.get("results", [])):
                if not needs_rescoring(r):
                    continue
                rd = local_run_dir(case["design"], r["tag"])
                if rd is None:
                    continue
                out.append({"file": path, "design": case["design"], "tag": r["tag"],
                            "pdk": r["pdk"], "run_dir": rd, "it": it_i, "r": r_i})
    return out


def rescore_candidate(result: dict, drc: dict, targets: dict, metrics: dict) -> dict:
    """The candidate's verdict re-scored with the deck's count. Returns
    the new verdict; the caller decides where it goes."""
    merged = {**metrics, "klayout__drc_error__count": drc["count"]}
    fresh = orchestrator.score(merged, targets)
    old = result.get("verdict") or {}
    # score() rebuilds `unverified` from the signoff metrics only. The
    # live path then appends what it learns elsewhere — a declared clock
    # that was never constrained, a model whose validity could not be
    # established — and those block a pass just as firmly. They are not
    # re-derived here (the logs they came from may be gone), so every
    # old entry that is not a signoff label is carried over verbatim,
    # and `passed` is recomputed over the union. The first trial of this
    # script dropped cdc_twoclock's clk_b entry and promoted the
    # candidate to a pass it had not earned; this is the fix.
    signoff_labels = {label for _, label in orchestrator.SIGNOFF_METRICS}
    carried = [u for u in old.get("unverified") or []
               if not any(u.endswith(label) or u == label for label in signoff_labels)]
    unverified = list(fresh["unverified"]) + [u for u in carried if u not in fresh["unverified"]]
    new = {**old, **fresh, "unverified": unverified, "klayout_drc": drc}
    new["passed"] = not new["violations"] and not unverified
    return new


def apply(entries: list[dict], write: bool) -> dict:
    drc_by_run: dict[Path, dict] = {}
    changed_files: dict[Path, dict] = {}
    summary = {"candidates": 0, "runs": 0, "failed": [], "now_pass": 0, "still_fail": 0,
               "cases_now_closed": []}
    for e in entries:
        rd = e["run_dir"]
        if rd not in drc_by_run:
            try:
                drc_by_run[rd] = gf180_drc.run(rd, e["pdk"])
                summary["runs"] += 1
            except Exception as ex:  # noqa: BLE001 - named, never a silent zero
                drc_by_run[rd] = {"error": f"{type(ex).__name__}: {ex}"}
                summary["failed"].append(f"{e['design']}/{e['tag']}: {ex}")
        drc = drc_by_run[rd]
        if "error" in drc:
            continue
        case = changed_files.get(e["file"])
        if case is None:
            case = json.loads(e["file"].read_text(encoding="utf-8"))
            changed_files[e["file"]] = case
        result = case["iterations"][e["it"]]["results"][e["r"]]
        metrics = json.loads((rd / "final" / "metrics.json").read_text(encoding="utf-8"))
        result["verdict"] = rescore_candidate(result, drc, targets_for(e["design"]), metrics)
        summary["candidates"] += 1
        if result["verdict"]["passed"]:
            summary["now_pass"] += 1
        else:
            summary["still_fail"] += 1

    today = dt.date.today().isoformat()
    for path, case in changed_files.items():
        results = [r for it in case["iterations"] for r in it.get("results", [])]
        before = {"winner_tag": case.get("winner_tag"), "outcome": case.get("outcome"),
                  "stop_reason": case.get("stop_reason")}
        if not case.get("winner_tag"):
            winner = orchestrator.pick_winner(results)
            if winner:
                case["winner_tag"] = winner["tag"]
                case["outcome"] = "passed"
                case["stop_reason"] = "winner_found"
                summary["cases_now_closed"].append(path.name)
        case["rescored"] = {
            "date": today,
            "what": "KLayout DRC via gf180_drc.py on the stored run's final GDS; "
                    "verdict re-scored by orchestrator.score with that one added metric",
            "before": before,
            "after": {"winner_tag": case.get("winner_tag"), "outcome": case.get("outcome"),
                      "stop_reason": case.get("stop_reason")},
        }
        if write:
            path.write_text(json.dumps(case, indent=2), encoding="utf-8")
    summary["files"] = len(changed_files)
    return summary


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--write", action="store_true", help="run the deck and rewrite the cases")
    ap.add_argument("--limit", type=int, help="only the first N candidates (for a trial)")
    args = ap.parse_args(argv)
    entries = plan()
    if args.limit:
        entries = entries[: args.limit]
    runs = {e["run_dir"] for e in entries}
    print(f"{len(entries)} candidate(s) across {len(runs)} run dir(s) can be re-scored")
    if not args.write:
        for e in entries[:12]:
            print(f"  {e['design']}/{e['tag']}  <- {e['file'].name}")
        if len(entries) > 12:
            print(f"  … and {len(entries) - 12} more")
        print("dry run; pass --write to run the deck and rewrite the cases")
        return 0
    summary = apply(entries, write=True)
    print(json.dumps(summary, indent=2, default=str))
    return 0 if not summary["failed"] else 1


if __name__ == "__main__":
    sys.exit(main())
