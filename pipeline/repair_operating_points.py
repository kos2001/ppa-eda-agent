#!/usr/bin/env python3
"""Recomputes recorded operating points that were derived from the
wrong clock period.

What was wrong. score_run_dir() handed operating_point() the design's
config.json CLOCK_PERIOD for every candidate, including the ones that
overrode it. Slack is measured against the period the tool was given,
so for a candidate swept to 12 ns the recorded min_period was
10 - slack instead of 12 - slack. 90 of the 356 recorded operating
points belong to such candidates, across six designs; aes at 12 ns was
recorded at Fmax 207.5 MHz when its critical path is 11.14 ns.

What this changes. Only fields derived from the period: each corner's
min_period_ns and fmax_mhz, the overall fmax_mhz and its limiting
corner, and clock_period_ns itself. The per-corner slacks — the
measurements — are read back from the case and not touched, and Vmin
does not depend on the period at all. Every repaired entry records the
period it was previously computed with, so the change is visible in
the case rather than silent.

Why a script rather than a hand edit. soul.md: the reference-db is the
memory, and a shell backtick once ate part of a diagnosis. This is
deterministic, reports what it would do before doing it (--write to
apply), and is pinned by tests/test_operating_point_repair.py which
asserts the store no longer contains a mismatched period.

Usage:
    python3 pipeline/repair_operating_points.py            # report only
    python3 pipeline/repair_operating_points.py --write    # apply
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import operating_point

REPO_ROOT = Path(__file__).resolve().parent.parent
CASES = REPO_ROOT / "reference-db" / "cases"


def mismatched(result: dict) -> float | None:
    """The period this result was really run at, if the recorded
    operating point used a different one; else None."""
    op = (result.get("verdict") or {}).get("operating_point")
    if not op:
        return None
    override = (result.get("overrides") or {}).get("CLOCK_PERIOD")
    if not isinstance(override, (int, float)):
        return None
    recorded = op.get("clock_period_ns")
    if isinstance(recorded, (int, float)) and abs(recorded - override) < 1e-9:
        return None
    return float(override)


def repair_result(result: dict) -> dict | None:
    """Recomputes one result's operating point in place. Returns a
    summary of the change, or None when nothing was wrong."""
    period = mismatched(result)
    if period is None:
        return None
    old = result["verdict"]["operating_point"]
    metrics = operating_point.metrics_from_corners(old.get("corners", []))
    new = operating_point.operating_point(metrics, period)
    if new is None:
        return None
    new["period_source"] = "override"
    new["repaired"] = {
        "at": date.today().isoformat(),
        "previous_clock_period_ns": old.get("clock_period_ns"),
        "previous_fmax_mhz": old.get("fmax_mhz"),
        "reason": "operating point had been computed against config.json's "
                  "CLOCK_PERIOD rather than this candidate's override",
    }
    result["verdict"]["operating_point"] = new
    return {"tag": result.get("tag"), "period_ns": period,
            "was_period_ns": old.get("clock_period_ns"),
            "fmax_mhz": new.get("fmax_mhz"), "was_fmax_mhz": old.get("fmax_mhz")}


def repair_case(path: Path, write: bool) -> list[dict]:
    case = json.loads(path.read_text(encoding="utf-8"))
    changes = []
    for iteration in case.get("iterations", []):
        for result in iteration.get("results", []):
            change = repair_result(result)
            if change:
                changes.append(change)
    if changes and write:
        path.write_text(json.dumps(case, indent=2), encoding="utf-8")
    return changes


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--write", action="store_true",
                    help="apply the recomputation (default: report only)")
    args = ap.parse_args()

    total = 0
    for path in sorted(CASES.glob("*.json")):
        changes = repair_case(path, args.write)
        if not changes:
            continue
        total += len(changes)
        print(f"{path.name}: {len(changes)} operating point(s)")
        for c in changes:
            was = f"{c['was_fmax_mhz']:.1f}" if c["was_fmax_mhz"] else "—"
            now = f"{c['fmax_mhz']:.1f}" if c["fmax_mhz"] else "—"
            print(f"  {c['tag']}: period {c['was_period_ns']} -> {c['period_ns']} ns, "
                  f"Fmax {was} -> {now} MHz")
    print(f"\n{total} operating point(s) {'repaired' if args.write else 'would be repaired'}"
          f"{'' if args.write else ' (re-run with --write to apply)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
