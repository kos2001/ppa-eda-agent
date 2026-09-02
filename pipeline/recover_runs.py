#!/usr/bin/env python3
"""Record OpenLane runs that finished but were never collected.

collect.py writes its case files once, after the whole batch finishes.
That is fine for a batch that finishes. When one is interrupted — the
process killed, the machine restarted, the terminal closed — every
completed run in it is lost, because nothing has been written yet. It
happened here: a 171-run batch was killed at 104 runs and a second at 2,
and the store stayed exactly where it started at 219 samples.

The results themselves were never lost. OpenLane writes each run into
`<design>/runs/<tag>/` as it goes, with the resolved config it actually
used and its own metrics.json. 83 complete run directories were sitting
on disk with nobody to read them. This reads them.

WHAT IT DOES NOT DO. It does not re-score, re-derive, or re-implement
anything. Recovery calls orchestrator.score_run_dir — the same function
the live collector calls the moment a run returns — so a recovered row
and a collected row are the same row, produced by the same code. A
second scoring path that drifted from the first would be worse than
losing the runs, because the disagreement would be invisible.

Two things are read from the run rather than assumed:

  * The configuration comes from the run's own resolved.json, not from
    its tag. The tag is a name we chose; resolved.json is what OpenLane
    used. They have disagreed before — a tag built from an override that
    OpenLane silently ignored looks exactly like one that worked.

  * `overrides` is reconstructed as the difference from the design's
    declared config.json, because that is what the collector records and
    what the dedup key is built from. Recording the full resolved config
    instead would make every recovered row unique, and the same
    configuration would then be counted twice.

Usage:
    recover_runs.py                 # report what could be recovered
    recover_runs.py --write         # write the case files
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import orchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent
DESIGNS = REPO_ROOT / "pipeline" / "designs"

# Config keys the collector sweeps. Only these are compared against the
# design's declared config to rebuild `overrides` — the resolved config
# holds several hundred keys, almost all of them PDK defaults that no
# candidate chose and that would make every row look distinct.
SWEPT = ("SYNTH_STRATEGY", "CLOCK_PERIOD", "FP_CORE_UTIL", "DIE_AREA",
         "PL_TARGET_DENSITY_PCT", "PNR_EXCLUDED_CELL_FILE")


def declared_config(design: str) -> dict:
    path = DESIGNS / design / "config.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def resolved(run_dir: Path) -> dict | None:
    path = run_dir / "resolved.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def is_complete(run_dir: Path) -> bool:
    """A run that reached the end of the flow.

    `final/metrics.json` is written at signoff. A run that crashed has
    per-step metrics but no final ones, and recovering it as though it
    completed would turn a crash into a result.
    """
    return (run_dir / "final" / "metrics.json").is_file()


def overrides_from(config: dict, design: str) -> dict:
    """What this run overrode, relative to the design's own config."""
    base = declared_config(design)
    out = {}
    for key in SWEPT:
        value = config.get(key)
        if value is None:
            continue
        if key in base and base[key] == value:
            continue
        # A PDK-resolved absolute path is not something a candidate
        # chose; the collector passes a /design-relative one.
        if key == "PNR_EXCLUDED_CELL_FILE" and str(value).startswith("/pdk"):
            continue
        out[key] = value
    return out


def recoverable(design: str) -> list[dict]:
    """Complete, unrecorded runs for one design, oldest tag first."""
    runs = DESIGNS / design / "runs"
    if not runs.is_dir():
        return []
    found = []
    for run_dir in sorted(p for p in runs.iterdir() if p.is_dir()):
        if not is_complete(run_dir):
            continue
        config = resolved(run_dir)
        if config is None:
            continue
        found.append({
            "run_dir": run_dir,
            "tag": run_dir.name,
            "overrides": overrides_from(config, design),
            "scl": config.get("STD_CELL_LIBRARY"),
            "pdk": config.get("PDK"),
        })
    return found


def already_recorded(design: str) -> set:
    """Signatures already in the store, so recovery cannot duplicate."""
    import surrogate
    have = set()
    for row in surrogate.load_dataset():
        if row.get("design") != design:
            continue
        have.add((json.dumps(row.get("overrides") or {}, sort_keys=True),
                  row.get("scl") or surrogate.DEFAULT_SCL,
                  row.get("pdk") or surrogate.DEFAULT_PDK))
    return have


def recover(design: str, write: bool = False) -> dict:
    import surrogate
    have = already_recorded(design)
    design_dir = DESIGNS / design
    spec = {"targets": {}}
    spec_path = design_dir / "run_spec.json"
    if spec_path.is_file():
        spec["targets"] = json.loads(spec_path.read_text(encoding="utf-8")).get("targets", {})

    rows, skipped = [], 0
    for item in recoverable(design):
        sig = (json.dumps(item["overrides"], sort_keys=True),
               item["scl"] or surrogate.DEFAULT_SCL,
               item["pdk"] or surrogate.DEFAULT_PDK)
        if sig in have:
            skipped += 1
            continue
        have.add(sig)
        cand = {"tag": item["tag"], "overrides": item["overrides"],
                "scl": item["scl"], "pdk": item["pdk"]}
        try:
            row = orchestrator.score_run_dir(
                design_dir, item["run_dir"], spec, cand,
                item["tag"], item["scl"], item["pdk"])
        except Exception as e:  # noqa: BLE001 - one bad run is not fatal
            skipped += 1
            print(f"  {design}/{item['tag']}: {type(e).__name__}: {e}",
                  file=sys.stderr)
            continue
        row["design"] = design
        # No wall-clock: nobody timed these. Left absent rather than
        # guessed, because estimate() reads this field to plan batches
        # and a made-up number would quietly mis-plan every later one.
        row["seconds"] = None
        rows.append(row)

    out = {"design": design, "recovered": len(rows),
           "already_recorded": skipped, "case_file": None}
    if rows and write:
        case_file = orchestrator.write_case(
            design, design_dir, [{"iteration": 1, "results": rows}],
            orchestrator.pick_winner(rows), "recovered_from_run_dirs")
        out["case_file"] = case_file.relative_to(REPO_ROOT).as_posix()
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--designs", nargs="*",
                    default=sorted(p.name for p in DESIGNS.iterdir()
                                   if (p / "config.json").is_file()))
    ap.add_argument("--write", action="store_true",
                    help="write case files; reporting is the default")
    args = ap.parse_args()
    report = [recover(d, write=args.write) for d in args.designs]
    print(json.dumps({"designs": [r for r in report if r["recovered"]
                                  or r["already_recorded"]],
                      "total_recovered": sum(r["recovered"] for r in report)},
                     indent=2))


if __name__ == "__main__":
    main()
