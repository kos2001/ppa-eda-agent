"""Explicit implementation evidence and PPA budgets, not a benchmark grader.

Inspired by Build-A-Chip-Bench's separate correctness and physical gates.
RTL equivalence does not establish workload correctness or post-route equivalence.
"""
import math
from pathlib import Path


TARGETS = {
    "max_cell_area_um2": ("design__instance__area", "um2"),
    "max_core_area_um2": ("design__core__area", "um2"),
    "max_die_area_um2": ("design__die__area", "um2"),
    "max_power_w": ("power__total", "W"),
}


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def check_limit(name, actual, limit, unit):
    if not finite(limit) or limit <= 0:
        raise ValueError(f"{name} must be a finite positive number ({unit})")
    valid = finite(actual) and actual >= 0
    return {"target": name, "actual": actual if valid else None,
            "limit": limit, "unit": unit,
            "status": "unverified" if not valid else "passed" if actual <= limit else "failed"}


def target_checks(metrics, targets):
    return [check_limit(name, metrics.get("design__instance__area__stdcell", metrics.get(key))
                        if name == "max_cell_area_um2" else metrics.get(key), targets[name], unit)
            for name, (key, unit) in TARGETS.items() if name in targets]


def validate_spec(spec):
    if "functional_regression" in spec:
        from functional_regression import validate
        validate(spec["functional_regression"])
    targets = spec.get("targets", {})
    target_checks({}, targets)
    if "max_clock_period_ns" in targets:
        check_limit("max_clock_period_ns", None, targets["max_clock_period_ns"], "ns")
    for key in ("require_implementation_evidence", "verify_function"):
        if key in spec and not isinstance(spec[key], bool):
            raise ValueError(f"{key} must be a boolean")


def apply_targets(verdict, checks):
    verdict.setdefault("target_checks", []).extend(checks)
    for check in checks:
        if check["status"] == "unverified":
            verdict.setdefault("unverified", []).append(f"{check['target']}: measurement missing or invalid")
        elif check["status"] == "failed":
            verdict["violations"].append(
                f"{check['target']}: {check['actual']} > {check['limit']} {check['unit']}")
    verdict["passed"] = not verdict["violations"] and not verdict.get("unverified")


def attach_evidence(verdict, run_dir: Path, equivalence, required=False):
    """Record evidence for this candidate only; never inherit a parent's pass."""
    gds = sorted(str(p) for p in (run_dir / "final" / "gds").glob("*.gds")
                 if p.is_file() and p.stat().st_size > 0)
    functional = "unverified"
    if equivalence is not None:
        functional = "passed" if (
            equivalence.get("equivalent") is True
            and equivalence.get("vacuous") is False
            and finite(equivalence.get("proven_points"))
            and equivalence["proven_points"] > 0
            and equivalence.get("unproven_points") == 0
        ) else "failed"
    checks = [
        {"name": "RTL / synthesis netlist equivalence", "status": functional},
        {"name": "Nonempty final GDS artifact", "status": "passed" if gds else "unverified"},
    ]
    verdict["implementation_evidence"] = {
        "required": required, "checks": checks, "gds_files": gds,
        "qualified": verdict["passed"] and all(c["status"] == "passed" for c in checks),
        "scope": "Local implementation checks; not a proof of all workload behavior or formal post-route equivalence.",
    }
    if required:
        for check in checks:
            if check["status"] == "unverified":
                verdict.setdefault("unverified", []).append(check["name"])
            elif check["status"] == "failed":
                verdict["violations"].append(f"{check['name']}: failed")
        verdict["passed"] = verdict["implementation_evidence"]["qualified"]
