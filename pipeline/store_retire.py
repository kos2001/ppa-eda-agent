#!/usr/bin/env python3
r"""Retire stored results that are wrong, superseded, or no longer reproducible.

reference-db is this project's memory (soul.md), so what leaves it needs a
reason that can be checked, not a feeling that a result is old. Everything
here is selected by an explicit rule, shown as a plan first, and recorded in
reference-db/retired.json with the rule that removed it. Nothing is
destroyed: the previous content stays in git history.

What is NOT retired, on purpose. A failure is not stale because it is a
failure: soul.md keeps closed doors as data. A result is retired only when
the record is wrong about the world (it measured something other than what
its tag says), or when the pipeline that produced the failure was missing a
repair it has since gained, so the number says more about the old pipeline
than about the design.

RULES
  ignored-override   A candidate configured with a variable OpenLane 2 does
                     not have (RE_BUFFER_CELL, STD_CELL_LIBRARY). OpenLane
                     warned and ran the un-overridden config, so the result
                     is a duplicate of the baseline reported as an experiment.
                     run_stage.reject_ignored_overrides now fails such runs.
  inherited-die      Died at STA-0572 (negative core area) on a die that came
                     from the design's config.json, with no DIE_AREA override.
                     The die-growth repair read only overrides, so these 10
                     candidates were never repaired; with the current
                     propose_repairs() they converge (counter4_tinydie,
                     c-hd-clock_period4: 8 -> 16 -> 32 -> 64 um, passes). A
                     first attempt that the case went on to repair is kept.
  superseded         A standard-cell signoff case with a newer case for the
                     same cell. The 2026-10-02 sweep reproduced the
                     2026-09-13 verdicts exactly (414 / 10 / 11 / 2), so the
                     older ones add rows, not information.
  orphan-image       A layout render no case refers to; its case was
                     overwritten before cases got per-run names.
  named              Hand-listed in NAMED below with its reason (none today).
  (annotation)       A record that is accurate but out of date gets a dated note
                     in front of its diagnosis instead of being removed; see
                     ANNOTATIONS.

Usage:
    store_retire.py            # print the plan
    store_retire.py --apply    # carry it out and write reference-db/retired.json
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

import case_storage

REPO_ROOT = Path(__file__).resolve().parent.parent
REFDB = REPO_ROOT / "reference-db"
DESIGNS = REPO_ROOT / "pipeline" / "designs"

IGNORED_KEYS = {"RE_BUFFER_CELL", "STD_CELL_LIBRARY"}

# (case file, candidate tag) -> reason. Each entry is a decision someone made
# on evidence, which is why it is a list and not a pattern.
NAMED: dict[tuple[str, str], str] = {}

# case file -> dated note put in front of its diagnosis. For a record that is
# accurate but out of date: removing it would cut the story the case tells
# (sram_wrapper__2026-09-10's cand-baseline is the first rung of the Magic
# ladder, cited by commit ad37331 and by the case's own diagnosis), while
# leaving it unmarked keeps showing a failure that no longer reproduces.
ANNOTATIONS: dict[str, str] = {
    "sram_wrapper__2026-09-10.json": (
        "[2026-10-02 update: the Magic stream-out failure of cand-baseline below no longer "
        "reproduces. The design's config.json has adopted this ladder's settings "
        "(MAGIC_CAPTURE_ERRORS=False, MAGIC_DRC_USE_GDS=False, PRIMARY_GDSII_STREAMOUT_TOOL=klayout) "
        "and the flow now completes with Magic DRC 0, KLayout DRC 0, LVS 0, XOR clean. What still "
        "stops a pass is 18 max-fanout and 16 max-slew violations plus an extrapolated macro "
        "liberty; see pipeline/designs/sram_wrapper/experiments/README.md.]"),
}


def _safe_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _design_config(design: str) -> dict:
    # A case's design name is not always its directory (counter4_tinydie's
    # config says DESIGN_NAME counter4), so the directory is the case's own.
    cfg = DESIGNS / design / "config.json"
    try:
        return _safe_json(cfg)
    except (OSError, ValueError):
        return {}


def result_reason(case_design: str, result: dict, case_file: str,
                  repaired: bool = False) -> tuple[str, str] | None:
    """(rule, why) when this candidate result should be retired, else None.

    `repaired` is whether the case holds a repair of this very candidate
    (a later result tagged "<tag>-iter..."). A failure the loop went on to
    repair is the loop working, and is the evidence for it: the first
    attempt of a repaired run is kept.
    """
    overrides = result.get("overrides") or {}
    ignored = sorted(IGNORED_KEYS & set(overrides))
    if ignored:
        return ("ignored-override",
                f"overrides {', '.join(ignored)}, which OpenLane 2 does not have; the run was "
                f"an unmarked duplicate of the baseline")
    named = NAMED.get((case_file, result.get("tag")))
    if named:
        return ("named", named)
    error = result.get("error") or ""
    if ("STA-0572" in error and "DIE_AREA" not in overrides and not repaired
            and (_design_config(case_design).get("DIE_AREA"))):
        return ("inherited-die",
                "negative core area on the die declared in config.json; no DIE_AREA override "
                "for the repair to grow, so it was never repaired (propose_repairs now reads "
                "the design's die)")
    return None


def plan_cases(refdb: Path = REFDB) -> list[dict]:
    import case_store
    out = []
    for path in sorted((refdb / "cases").glob("*.json")):
        case = case_store.load_light(path)
        design = case.get("design", "")
        tags = [r["tag"] for it in case.get("iterations", []) for r in it.get("results", [])]
        for it in case.get("iterations", []):
            for r in it.get("results", []):
                repaired = any(o.startswith(r["tag"] + "-iter") for o in tags)
                hit = result_reason(design, r, path.name, repaired)
                if hit:
                    out.append({"kind": "result", "case": path.name, "iteration": it["iteration"],
                                "tag": r["tag"], "rule": hit[0], "why": hit[1]})
    return out


_STAMP = re.compile(r"^(?P<cell>.+)__(?P<date>\d{4}-\d{2}-\d{2})__(?P<time>\d{6})\.json$")


def plan_stdcells(refdb: Path = REFDB) -> list[dict]:
    """Every cell keeps its newest case; older ones are superseded."""
    newest: dict[str, tuple[str, str, Path]] = {}
    seen: list[tuple[str, str, str, Path]] = []
    for path in sorted((refdb / "stdcells").glob("*.json")):
        m = _STAMP.match(path.name)
        if not m:
            continue
        seen.append((m["cell"], m["date"], m["time"], path))
    for cell, d, t, path in seen:
        if cell not in newest or (d, t) > (newest[cell][0], newest[cell][1]):
            newest[cell] = (d, t, path)
    return [{"kind": "file", "path": str(path.relative_to(refdb)), "rule": "superseded",
             "why": f"a newer signoff of {cell} exists ({newest[cell][2].name})"}
            for cell, d, t, path in seen if path != newest[cell][2]]


def plan_images(refdb: Path = REFDB) -> list[dict]:
    import case_store
    referenced = set()
    for path in (refdb / "cases").glob("*.json"):
        img = case_store.load_light(path).get("layout_image")
        if img:
            referenced.add(Path(img).name)
    layouts = refdb / "layouts"
    if not layouts.is_dir():
        return []
    return [{"kind": "file", "path": str(p.relative_to(refdb)), "rule": "orphan-image",
             "why": "no case refers to this render"}
            for p in sorted(layouts.glob("*")) if p.is_file() and p.name not in referenced]


def plan_notes(refdb: Path = REFDB) -> list[dict]:
    out = []
    for name, note in ANNOTATIONS.items():
        path = refdb / "cases" / name
        if not path.exists():
            continue
        diagnosis = _safe_json(path).get("diagnosis")
        if isinstance(diagnosis, str) and note not in diagnosis:
            out.append({"kind": "note", "case": name, "rule": "annotation", "why": note})
    return out


def plan(refdb: Path = REFDB) -> list[dict]:
    return plan_cases(refdb) + plan_stdcells(refdb) + plan_images(refdb) + plan_notes(refdb)


RETIRED_PHRASE = "a retired candidate"


def note_retirement_in_diagnosis(case: dict, tags: list[str], today: str) -> bool:
    """Make the case's own diagnosis agree with the results it still holds.

    sram_wrapper__2026-08-26's diagnosis is the "INVALID EXPERIMENT" note about
    cand-rebuf8 and cand-rebuf12. With those results retired it would cite
    candidates the case no longer has, which review grounding rightly flags
    as a claim with nothing behind it. The tags are replaced by a plain
    phrase and a dated note says where the results went; the reasoning in the
    text is untouched.
    """
    text = case.get("diagnosis")
    if not isinstance(text, str):
        return False
    new = text
    for tag in sorted(tags, key=len, reverse=True):
        new = new.replace(tag, RETIRED_PHRASE)
    if new == text:
        return False
    case["diagnosis"] = (f"[{today}: the candidates this text discusses were retired from the "
                         f"case; see `retired` below and reference-db/retired.json.] " + new)
    return True


def apply(items: list[dict], refdb: Path = REFDB, today: str | None = None) -> dict:
    """Carry the plan out and append it to retired.json."""
    today = today or date.today().isoformat()
    by_case: dict[str, list[dict]] = {}
    for it in items:
        if it["kind"] == "result":
            by_case.setdefault(it["case"], []).append(it)
    for name, hits in by_case.items():
        path = refdb / "cases" / name
        case = _safe_json(path)  # the full file: rewriting a light copy would lose the layouts
        drop = {(h["iteration"], h["tag"]) for h in hits}
        for iteration in case["iterations"]:
            iteration["results"] = [r for r in iteration["results"]
                                    if (iteration["iteration"], r["tag"]) not in drop]
        case["iterations"] = [i for i in case["iterations"] if i["results"]]
        case.setdefault("retired", []).extend(
            {"tag": h["tag"], "rule": h["rule"], "why": h["why"], "on": today} for h in hits)
        note_retirement_in_diagnosis(case, [h["tag"] for h in hits], today)
        case_storage.write_case_json(path, case)
    notes = [it for it in items if it["kind"] == "note"]
    for it in notes:
        path = refdb / "cases" / it["case"]
        case = _safe_json(path)
        case["diagnosis"] = it["why"] + " " + case["diagnosis"]
        case_storage.write_case_json(path, case)
    for it in items:
        if it["kind"] == "file":
            (refdb / it["path"]).unlink(missing_ok=True)

    ledger_path = refdb / "retired.json"
    ledger = _safe_json(ledger_path) if ledger_path.exists() else {"entries": []}
    ledger["entries"].extend({**it, "on": today} for it in items if it["kind"] == "result")
    if notes:
        ledger.setdefault("annotated", []).extend({"case": n["case"], "on": today} for n in notes)
    files = [it for it in items if it["kind"] == "file"]
    if files:
        ledger.setdefault("files", []).append({
            "on": today, "count": len(files),
            "rules": sorted({f["rule"] for f in files}),
            "examples": [f["path"] for f in files[:3]],
            "recover": "git log --diff-filter=D --name-only -- reference-db"})
    ledger["note"] = ("What was retired from reference-db and why. Nothing is destroyed: "
                      "the earlier content is in git history. See pipeline/store_retire.py.")
    ledger_path.write_text(json.dumps(ledger, indent=1, ensure_ascii=False), encoding="utf-8")
    return {"results": sum(len(v) for v in by_case.values()), "files": len(files),
            "annotated": len(notes)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--refdb", type=Path, default=REFDB)
    args = ap.parse_args()
    items = plan(args.refdb)
    by_rule: dict[str, int] = {}
    for it in items:
        by_rule[it["rule"]] = by_rule.get(it["rule"], 0) + 1
    for it in items:
        if it["kind"] == "result":
            print(f"result  {it['rule']:17s} {it['case']}  {it['tag']}")
    for it in items:
        if it["kind"] == "note":
            print(f"note    {it['rule']:17s} {it['case']}")
    for rule, n in sorted(by_rule.items()):
        print(f"{rule}: {n}")
    if not args.apply:
        print("(plan only; pass --apply to carry it out)")
        return
    print(apply(items, args.refdb))


if __name__ == "__main__":
    sys.exit(main())
