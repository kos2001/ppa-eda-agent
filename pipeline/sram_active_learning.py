#!/usr/bin/env python3
"""Learn what the SRAM placement DOE can and cannot fix.

This is intentionally a tiny, auditable regression rather than a neural net.
Four measured standoff levels are enough to test a one-dimensional trend, but
not enough to fit a high-capacity model.  The output is advisory: signoff and
macro characterisation remain the acceptance tests.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def linear_fit(points: list[tuple[float, float]]) -> dict:
    """Ordinary least squares for y = intercept + slope*x."""
    if len(points) < 3 or len({x for x, _ in points}) < 3:
        return {"supported": False, "reason": "need at least 3 distinct measurements"}
    xbar = sum(x for x, _ in points) / len(points)
    ybar = sum(y for _, y in points) / len(points)
    den = sum((x - xbar) ** 2 for x, _ in points)
    slope = sum((x - xbar) * (y - ybar) for x, y in points) / den
    intercept = ybar - slope * xbar
    residual = sum((y - (intercept + slope * x)) ** 2 for x, y in points)
    total = sum((y - ybar) ** 2 for _, y in points)
    return {"supported": True, "n": len(points), "slope_ns_per_um": slope,
            "intercept_ns": intercept, "r2": 1.0 - residual / total if total else 1.0}


def analyse(rows: list[dict], ceiling: float = 0.04) -> dict:
    usable = [r for r in rows if not r.get("crashed") and r.get("placement_standoff_um") is not None]
    # A one-factor regression is valid only inside one packing topology.
    # Mixing round 10's lateral placement into the outward-standoff fit would
    # attribute its routing-congestion penalty to distance and produce a
    # confidently wrong slope.
    regression_rows = [r for r in usable
                       if (r.get("placement_packing") or "outward") == "outward"]
    pins = sorted({p for r in regression_rows for p in (r.get("macro_pin_detail") or {})})
    models = {}
    for pin in pins:
        points = [(float(r["placement_standoff_um"]),
                   float(r["macro_pin_detail"][pin]["slew_ns"]))
                  for r in regression_rows if pin in (r.get("macro_pin_detail") or {})]
        fit = linear_fit(points)
        if fit["supported"]:
            fit["predicted_at_zero_ns"] = fit["intercept_ns"]
            fit["standoff_alone_reaches_ceiling"] = (
                fit["slope_ns_per_um"] > 0 and fit["intercept_ns"] <= ceiling)
        models[pin] = fit

    # Pareto rows preserve real constraints instead of blending them into an
    # arbitrary reward.  A row is dominated only when another is no worse in
    # every failure count and strictly better in at least one.
    objectives = ("macro_pins_over_ceiling", "max_slew", "max_fanout", "max_cap", "antenna")
    pareto = []
    for row in usable:
        values = tuple(int(row.get(k) or 0) for k in objectives)
        dominated = False
        for other in usable:
            if other is row:
                continue
            ov = tuple(int(other.get(k) or 0) for k in objectives)
            if all(a <= b for a, b in zip(ov, values)) and any(a < b for a, b in zip(ov, values)):
                dominated = True
                break
        if not dominated:
            pareto.append({"name": row.get("name"), **dict(zip(objectives, values))})
    return {"measurements": len(usable), "regression_measurements": len(regression_rows),
            "regression_group": {"placement_packing": "outward"}, "ceiling_ns": ceiling,
            "pin_models": models, "pareto": pareto}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    rows = []
    for path in args.inputs:
        batch = json.loads(path.read_text(encoding="utf-8"))
        for row in batch:
            # Round 8 predates explicit feature recording, but its source fixes
            # STANDOFF=14.  Record that historical fact at ingestion, not by
            # silently changing the evidence file.
            if row.get("name") == "r8-local-chain" and row.get("placement_standoff_um") is None:
                row = {**row, "placement_standoff_um": 14.0}
            rows.append(row)
    result = analyse(rows)
    text = json.dumps(result, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
