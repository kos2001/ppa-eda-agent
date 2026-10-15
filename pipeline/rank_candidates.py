#!/usr/bin/env python3
"""Rank run_spec candidates for measured PPA exploitation and exploration.

This is advisory: it never runs, drops, or qualifies a candidate.  Supported
area/power predictions are ordered by PPA; unsupported regions are labelled
for exploration; exact repeats are identified so an operator can avoid paying
for them unless a deliberate re-run is wanted.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import surrogate
from orchestrator import expand_sweeps


def report(design_dir: Path, spec_path: Path | None = None) -> dict:
    config = json.loads((design_dir / "config.json").read_text(encoding="utf-8"))
    spec_path = spec_path or design_dir / "run_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    candidates = list(spec.get("candidates") or []) + expand_sweeps(spec)
    if not candidates:
        raise ValueError(f"{spec_path} has no candidates or sweeps")
    ranked = surrogate.rank_candidates(design_dir.name, candidates, config)
    return {
        "design": design_dir.name,
        "source": str(spec_path),
        "policy": {
            "exploitation": "lower equal-weight normalized predicted area/power",
            "exploration": "prediction refused or larger nearest-neighbour distance",
            "safety": "advisory only; pass/fail is not predicted and real EDA remains the gate",
        },
        "ranked": ranked,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("design_dir", type=Path)
    ap.add_argument("--spec", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    result = report(args.design_dir, args.spec)
    text = json.dumps(result, indent=2, ensure_ascii=False)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
