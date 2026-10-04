#!/usr/bin/env python3
"""Read every reported macro input edge in the final per-corner STA audit.

Coverage is separate from model validity. This report never qualifies an
unmeasured Liberty, missing PVT model, or extrapolated timing table.
"""
import argparse
import csv
import json
import math
from pathlib import Path

from sta_report import latest_sta_dir

FIELDS = ("max_rise_ns", "max_fall_ns", "min_rise_ns", "min_fall_ns")


def read_audit(run_dir, expected_inputs=None):
    run_dir = Path(run_dir)
    expected_inputs = expected_inputs or {}
    resolved = json.loads((run_dir / "resolved.json").read_text())
    expected_corners = resolved["STA_CORNERS"]
    stage = latest_sta_dir(run_dir)
    reports, missing, unknown, rows = {}, [], [], []
    for corner in expected_corners:
        report = stage / corner / "macro_inputs.csv"
        if not report.is_file():
            missing.append(corner)
            continue
        pins = set()
        with report.open() as handle:
            for row in csv.DictReader(handle):
                pin = row["pin"]
                if pin in pins:
                    raise ValueError(f"duplicate macro pin {pin} in {corner}")
                pins.add(pin)
                values = {}
                for field in FIELDS:
                    try:
                        value = float(row[field])
                        if not math.isfinite(value) or value < 0:
                            raise ValueError("invalid slew")
                    except (ValueError, TypeError):
                        value = None
                        unknown.append({"corner": corner, "pin": pin, "edge": field})
                    values[field] = value
                rows.append({"corner": corner, "pin": pin, **values})
        reports[corner] = sorted(pins)
    mismatch = []
    for corner, pins in reports.items():
        for instance, count in expected_inputs.items():
            observed = sum(pin.startswith(instance + "/") for pin in pins)
            if observed != count:
                mismatch.append({"corner": corner, "instance": instance,
                                 "expected": count, "observed": observed})
    same_pins = len({tuple(pins) for pins in reports.values()}) == 1
    available = [{"corner": row["corner"], "pin": row["pin"], "edge": field, "slew_ns": row[field]}
                 for row in rows for field in FIELDS if row[field] is not None]
    worst = max(available, key=lambda item: item["slew_ns"]) if available else None
    return {
        "scope": "all reported macro input pins, rise/fall, min/max, expected STA corners",
        "expected_corners": expected_corners, "corners_read": len(reports),
        "pins_per_corner": {corner: len(pins) for corner, pins in reports.items()},
        "expected_inputs": expected_inputs,
        "coverage_complete": bool(expected_inputs and reports and same_pins
                                  and not missing and not unknown and not mismatch),
        "missing_corners": missing, "unknown_edges": unknown, "pin_count_mismatches": mismatch,
        "model_validity_verified": False,
        "model_limit": "Coverage alone does not establish per-arc Liberty range or PVT validity",
        "worst": worst, "rows": rows,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-dir", type=Path, required=True)
    ap.add_argument("--expected-inputs", required=True, help='JSON instance-to-signal-input count, e.g. {"u_sram":57}')
    args = ap.parse_args()
    print(json.dumps(read_audit(args.run_dir, json.loads(args.expected_inputs)), indent=2))


if __name__ == "__main__":
    main()
