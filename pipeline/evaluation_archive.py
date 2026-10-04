"""Persistent, derived Pareto memory. Original case files remain the evidence."""
import argparse
import collections
import json
import math
import os
from pathlib import Path
import statistics
import tempfile

import case_store
from evaluation_provenance import digest
from pareto import ParetoPoint, fast_nondominated_sort

REFDB = Path(__file__).resolve().parents[1] / "reference-db"


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def eligible(row):
    if row.get("not_evaluated"):
        return "not_evaluated"
    if row.get("error"):
        return "flow_error"
    v = row.get("verdict") or {}
    if not v.get("passed") or v.get("violations") or v.get("unverified"):
        return "verification_open"
    checks = v.get("signoff_checks")
    required_checks = {"magic__drc_error__count", "klayout__drc_error__count", "design__lvs_error__count"}
    if (not checks or not required_checks.issubset({check.get("key") for check in checks})
            or any(not finite(check.get("count")) or check["count"] != 0 for check in checks)):
        return "physical_checks_unknown"
    corners = v.get("timing_corners")
    if not corners or any(not finite(c.get(key)) or c[key] < 0
                          for c in corners for key in ("setup_wns", "hold_wns")):
        return "timing_coverage_unknown"
    macro = (v.get("model_validity") or {}).get("macro_arc_audit")
    if macro and not macro.get("model_qualified"):
        return "model_unqualified"
    provenance = row.get("evaluation_provenance") or {}
    context = provenance.get("context")
    if (not provenance.get("complete") or not context
            or provenance.get("compatibility_key") != digest(context)):
        return "provenance_unknown"
    required = ("design", "pdk", "scl", "pdk_version", "image", "openroad_revision",
                "inputs", "sdc_sha256", "liberty_sha256", "flow")
    if any(not context.get(key) for key in required) or not context["inputs"].get("complete"):
        return "provenance_unknown"
    if not isinstance(context.get("verification_policy", {}).get("rtl_netlist_equivalence_requested"), bool):
        return "provenance_unknown"
    if context.get("verification_policy", {}).get("rtl_netlist_equivalence_requested"):
        equiv = row.get("equivalence") or {}
        if not equiv.get("equivalent") or equiv.get("vacuous") is not False:
            return "equivalence_unverified"
    return None


def build_report(refdb=REFDB):
    from orchestrator import objective_table
    groups = collections.defaultdict(list)
    excluded = collections.Counter()
    costs = collections.defaultdict(list)
    stage_costs = collections.defaultdict(list)
    total = timed = 0
    files = []
    for path in sorted((Path(refdb) / "cases").glob("*.json")):
        case = case_store.load_light(path)
        files.append(path.name)
        exploration = (case.get("synthesis_exploration") or {}).get("evaluation") or {}
        if finite(exploration.get("seconds")) and exploration["seconds"] >= 0:
            stage_costs["synthesis_exploration"].append(exploration["seconds"])
        for iteration in case.get("iterations", []):
            for candidate in iteration.get("results", []):
                attempted = not candidate.get("not_evaluated") or bool(candidate.get("screen_evaluation"))
                if attempted:
                    total += 1
                seconds = candidate.get("seconds")
                if not finite(seconds):
                    seconds = (candidate.get("screen_evaluation") or {}).get("seconds")
                if finite(seconds) and seconds > 0 and attempted:
                    timed += 1
                    tc = case.get("toolchain") or {}
                    context = {"design": case["design"], "pdk": candidate.get("pdk"),
                               "scl": candidate.get("scl"), "image": tc.get("openlane_image"),
                               "host": tc.get("host"),
                               "flow": candidate.get("flow"),
                               "status": "flow_error" if candidate.get("error") else "completed",
                               "verification_requested": candidate.get("verification_requested"),
                               "input_key": digest(candidate["evaluation_inputs"]) if (candidate.get("evaluation_inputs") or {}).get("complete") else None,
                               "fidelity": candidate.get("evaluation_fidelity", "unrecorded")}
                    key = json.dumps(context, sort_keys=True)
                    costs[key].append(seconds)
                for name, value in (candidate.get("stage_costs") or {}).items():
                    if finite(value) and value >= 0:
                        stage_costs[name].append(value)
                screen = candidate.get("screen_evaluation") or {}
                if finite(screen.get("seconds")) and screen["seconds"] >= 0:
                    stage_costs["screen"].append(screen["seconds"])
                reason = eligible(candidate)
                if reason:
                    excluded[reason] += 1
                    continue
                key = candidate["evaluation_provenance"]["compatibility_key"]
                row = {**candidate, "tag": f"{path.name}::{candidate['tag']}"}
                groups[key].append(row)
    archives = []
    for key, rows in sorted(groups.items()):
        table = objective_table(rows)
        axes = list(table[0][1]) if table else []
        if not axes:
            excluded["no_common_objectives"] += len(rows)
            continue
        points = [ParetoPoint(tag, tuple(values[axis] for axis in axes)) for tag, values in table]
        front = fast_nondominated_sort(points)[0]
        values = dict(table)
        archives.append({"compatibility_key": key, "context": rows[0]["evaluation_provenance"]["context"],
                         "axes": axes, "eligible_candidates": len(rows),
                         "frontier": [{"id": rows[i]["tag"], "objectives": values[rows[i]["tag"]]} for i in front]})
    def summary(values):
        return {"samples": len(values), "median_seconds": round(statistics.median(values), 6),
                "max_seconds": round(max(values), 6)}
    return {"schema_version": 1, "case_files": files, "candidate_runs": total,
            "timed_runs": timed, "timing_missing_runs": total - timed,
            "excluded": dict(sorted(excluded.items())), "archives": archives,
            "cost_cohorts": [{"context": json.loads(key), **summary(values)} for key, values in sorted(costs.items())],
            "stage_costs": {name: summary(values) for name, values in sorted(stage_costs.items())},
            "qualification": "Measured physical/model gates; functional equivalence only when explicitly requested. No silicon yield claim."}


def store_key(refdb):
    return digest([(p.name, p.stat().st_mtime_ns, p.stat().st_size)
                   for p in sorted((Path(refdb) / "cases").glob("*.json"))])


def refresh(refdb=REFDB):
    refdb = Path(refdb)
    # Retry if a writer appended evidence while the snapshot was being built.
    for _ in range(3):
        key = store_key(refdb)
        report = build_report(refdb)
        if key != store_key(refdb):
            continue
        report["store_key"] = key
        destination = refdb / ".cache" / "pareto-archive.json"
        destination.parent.mkdir(parents=True, exist_ok=True)
        handle, name = tempfile.mkstemp(prefix="archive-", suffix=".tmp", dir=destination.parent)
        try:
            with os.fdopen(handle, "w") as stream:
                json.dump(report, stream, indent=2, allow_nan=False)
                stream.write("\n")
            os.replace(name, destination)
        finally:
            Path(name).unlink(missing_ok=True)
        return report
    raise RuntimeError("case store changed repeatedly; archive refresh deferred")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--refdb", type=Path, default=REFDB)
    parser.add_argument("--write", action="store_true", help="refresh persistent derived archive cache")
    args = parser.parse_args()
    print(json.dumps(refresh(args.refdb) if args.write else build_report(args.refdb), indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
