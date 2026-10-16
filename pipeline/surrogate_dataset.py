"""Audit-preserving provenance dataset for offline chronological comparisons.

This reader does not change the live surrogate, deduplicate measurements, or
upgrade historical provenance. Eligibility concerns measured regression data,
not signoff success. Missing targets stay unknown.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re

from evaluation_provenance import digest

REFDB = Path(__file__).resolve().parents[1] / "reference-db"
TARGETS = ("area_um2", "power_w", "worst_setup_slack", "seconds")
FIDELITIES = {"full_flow", "screen", "synthesis_exploration"}
EVALUATOR_SOURCES = {"orchestrator.py", "power_activity.py", "equiv_check.py", "evaluation_provenance.py"}


def finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _hash(value, lengths=(64,)):
    return isinstance(value, str) and len(value) in lengths and bool(re.fullmatch(r"[0-9a-f]+", value))


def _hash_map(value, empty=False):
    return (isinstance(value, dict) and (bool(value) or empty)
            and all(isinstance(k, str) and k and _hash(v) for k, v in value.items()))


def _hash_list(value, empty=False):
    return isinstance(value, list) and (bool(value) or empty) and all(_hash(v) for v in value)


def _object(value):
    return value if isinstance(value, dict) else {}


def _date(value):
    if not isinstance(value, str):
        raise ValueError("date missing")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    if not re.match(r"^\d{4}-\d{2}-\d{2}T", value):
        raise ValueError("date must be ISO")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp timezone missing")
    return parsed.astimezone(timezone.utc)


def _timestamp(case):
    """Use day buckets for all records, avoiding false intraday order."""
    dates = [_date(case.get("date"))]
    for field in ("rescored", "revision", "revisions"):
        if field not in case:
            continue
        revisions = case[field] if isinstance(case[field], list) else [case[field]]
        if not revisions:
            raise ValueError("revision chronology unavailable")
        for revision in revisions:
            dates.append(_date(_object(revision).get("date")))
    return max(dates).strftime("%Y-%m-%dT00:00:00Z")


def _provenance(result):
    provenance = _object(result.get("evaluation_provenance"))
    context = _object(provenance.get("context"))
    reasons = []
    if provenance.get("complete") is not True or not context:
        return None, ["provenance_unknown"]
    try:
        valid_digest = _hash(provenance.get("compatibility_key")) and provenance["compatibility_key"] == digest(context)
    except (TypeError, ValueError):
        valid_digest = False
    if not valid_digest:
        reasons.append("provenance_digest_invalid")
    inputs = _object(context.get("inputs"))
    policy = _object(context.get("verification_policy"))
    if (inputs.get("complete") is not True or not _hash(inputs.get("config_sha256"))
            or not _hash_map(inputs.get("rtl_sha256"))
            or not _hash_map(inputs.get("verification_sha256"), empty=True)
            or not _hash_map(context.get("evaluator_sha256"))
            or not EVALUATOR_SOURCES.issubset(_object(context.get("evaluator_sha256")))
            or not _hash_list(context.get("sdc_sha256"))
            or not _hash_list(context.get("liberty_sha256"))
            or not _hash_list(context.get("macro_views_sha256"), empty=True)
            or not _hash(context.get("pdk_version"), (40, 64))
            or not _hash(context.get("openroad_revision"), (40, 64))
            or type(policy.get("rtl_netlist_equivalence_requested")) is not bool
            or any(not isinstance(context.get(k), str) or not context[k].strip()
                   for k in ("design", "pdk", "scl", "image", "flow"))
            or not isinstance(context.get("semantic_overrides"), dict)):
        reasons.append("provenance_structure_invalid")
    if context.get("flow") != "Classic" and not _hash_map(context.get("flow_sources")):
        reasons.append("flow_sources_unknown")
    for key in ("pdk", "scl", "flow"):
        if result.get(key) is not None and result[key] != context.get(key):
            reasons.append("candidate_context_mismatch:" + key)
    if "evaluation_inputs" in result and result["evaluation_inputs"] != inputs:
        reasons.append("candidate_context_mismatch:inputs")
    if "verification_requested" in result and (type(result["verification_requested"]) is not bool
            or result["verification_requested"] != policy.get("rtl_netlist_equivalence_requested")):
        reasons.append("candidate_context_mismatch:verification")
    overrides = _object(result.get("overrides"))
    for key, value in _object(context.get("semantic_overrides")).items():
        if key not in overrides or type(overrides[key]) is not type(value) or overrides[key] != value:
            reasons.append("candidate_context_mismatch:overrides")
            break
    return context, reasons


def _status(result):
    if result.get("not_evaluated") is True or result.get("status") == "budget_deferred":
        return "budget_deferred"
    error = str(result.get("error") or "")
    if (result.get("timed_out") is True or result.get("status") in ("timeout", "timeout_unverified")
            or re.search(r"\b(?:timed out|timeout|TimeoutExpired)\b", error, re.I)):
        return "timeout"
    if result.get("error") or not isinstance(result.get("verdict"), dict):
        return "flow_error"
    verdict = result["verdict"]
    return ("verification_failed" if verdict.get("passed") is not True
            or verdict.get("violations") or verdict.get("unverified") else "measured")


def _declared(case):
    settings = _object(_object(case.get("constraints")).get("design")).get("settings", [])
    if not isinstance(settings, list):
        raise ValueError("declared settings invalid")
    declared = {}
    for setting in settings:
        if not isinstance(setting, dict) or not isinstance(setting.get("key"), str) or "value" not in setting or setting["key"] in declared:
            raise ValueError("declared settings invalid")
        declared[setting["key"]] = setting["value"]
    return declared


def _group(name, cases, visited=None):
    visited = set() if visited is None else visited
    if name in visited:
        raise ValueError("lineage_cycle")
    visited.add(name)
    case = cases.get(name, {})
    lineage = _object(case.get("lineage"))
    explicit = case.get("lineage_root", lineage.get("root_id"))
    if "lineage_root" in case or "root_id" in lineage:
        if not isinstance(explicit, str) or not explicit.strip():
            raise ValueError("lineage_invalid")
        return "lineage:" + explicit
    parent = _object(case.get("graph_loop")).get("parent_case")
    if parent is not None:
        if not isinstance(parent, str) or not parent.strip():
            raise ValueError("lineage_invalid")
        return _group(Path(parent).name, cases, visited)
    return "case:" + name


def load_dataset(refdb=REFDB):
    """Return rows, reason counts, and a raw-byte source manifest; no writes."""
    directory = Path(refdb) / "cases"
    if not directory.is_dir():
        raise FileNotFoundError(directory)
    cases, sources, source_files = {}, {}, []
    for path in sorted(directory.glob("*.json")):
        raw = path.read_bytes()
        record = {"path": "cases/" + path.name, "sha256": hashlib.sha256(raw).hexdigest()}
        source_files.append(record)
        try:
            case = json.loads(raw)
            if not isinstance(case, dict) or not isinstance(case.get("iterations"), list):
                raise ValueError("invalid case structure")
            cases[path.name] = case
            sources[path.name] = record
            for iteration in case["iterations"]:
                for result in _object(iteration).get("results", []) or []:
                    if isinstance(result, dict):
                        result.pop("layout", None)
                        result.pop("netlist", None)
        except (ValueError, UnicodeError) as error:
            record["error"] = str(error)
    rows, exclusions = [], Counter()
    for name, case in cases.items():
        base_reasons = []
        try:
            timestamp = _timestamp(case)
        except (ValueError, TypeError, OverflowError):
            timestamp = None
            base_reasons.append("chronology_unknown")
        try:
            group_id = _group(name, cases)
        except ValueError as error:
            group_id = "case:" + name
            base_reasons.append(str(error))
        try:
            declared = _declared(case)
        except ValueError:
            declared = {}
            base_reasons.append("declared_invalid")
        if not isinstance(case.get("design"), str) or not case["design"].strip():
            base_reasons.append("design_unknown")
        host = _object(_object(case.get("toolchain")).get("host"))
        host_valid = (all(isinstance(host.get(k), str) and host[k] for k in ("arch", "system", "docker_platform"))
                      and type(host.get("cpu_count")) is int and host["cpu_count"] > 0)
        for iteration_index, iteration in enumerate(case["iterations"]):
            if not isinstance(iteration, dict) or not isinstance(iteration.get("results"), list):
                raise ValueError(f"{name}: malformed iteration")
            for result_index, raw_result in enumerate(iteration["results"]):
                result = _object(raw_result)
                reasons = list(base_reasons)
                context, provenance_reasons = _provenance(result)
                reasons.extend(provenance_reasons)
                fidelity = result.get("evaluation_fidelity")
                if not isinstance(fidelity, str) or fidelity not in FIDELITIES:
                    reasons.append("fidelity_unknown")
                overrides = result.get("overrides", {})
                if not isinstance(overrides, dict):
                    overrides = {}
                    reasons.append("overrides_invalid")
                status = _status(result)
                for flag in ("not_evaluated", "timed_out"):
                    if flag in result and type(result[flag]) is not bool:
                        reasons.append("invalid_flag:" + flag)
                if status not in {"measured", "verification_failed"}:
                    reasons.append(status)
                verdict = _object(result.get("verdict"))
                if status in {"measured", "verification_failed"} and type(verdict.get("passed")) is not bool:
                    reasons.append("verification_state_unknown")
                validity = _object(verdict.get("model_validity"))
                has_macro = (bool(_object(context).get("macro_views_sha256"))
                             or _object(case.get("topology")).get("has_macros") is True
                             or bool(validity))
                macro_unqualified = has_macro and _object(validity.get("macro_arc_audit")).get("model_qualified") is not True
                targets = {"area_um2": verdict.get("area_um2"),
                           "power_w": _object(verdict.get("power")).get("total_w"),
                           "worst_setup_slack": verdict.get("worst_setup_slack"),
                           "seconds": result.get("seconds")}
                target_reasons = {}
                for key, value in targets.items():
                    invalid = (not finite(value) or (key in {"area_um2", "seconds"} and value <= 0)
                               or (key == "power_w" and value < 0))
                    if status not in {"measured", "verification_failed"}:
                        invalid = True
                    if key == "seconds" and not host_valid:
                        invalid = True
                    if key in {"power_w", "worst_setup_slack"} and macro_unqualified:
                        invalid = True
                    if invalid:
                        targets[key] = None
                        target_reasons[key] = ("macro_model_unqualified" if key in {"power_w", "worst_setup_slack"} and macro_unqualified
                                               else "host_unknown" if key == "seconds" and not host_valid
                                               else "missing_invalid_or_incomplete")
                if all(value is None for value in targets.values()):
                    reasons.append("targets_unknown")
                cohort_key = candidate_key = None
                if context is not None and not provenance_reasons and isinstance(fidelity, str) and fidelity in FIDELITIES:
                    try:
                        cohort_key = digest({"context": context, "fidelity": fidelity, "host": host if host_valid else None})
                        candidate_key = digest({"cohort_key": cohort_key, "effective_overrides": {**declared, **overrides}})
                    except (TypeError, ValueError):
                        reasons.append("candidate_configuration_invalid")
                reasons = sorted(set(reasons))
                exclusions.update(reasons)
                rows.append({"id": f"{name}::{iteration_index}:{result_index}", "group_id": group_id,
                             "source_file": sources[name]["path"], "source_sha256": sources[name]["sha256"],
                             "timestamp": timestamp, "timestamp_precision": "day", "case_date": case.get("date"),
                             "cohort_key": cohort_key, "candidate_key": candidate_key,
                             "design": case.get("design"), "tag": result.get("tag"), "declared": declared,
                             "overrides": overrides, "topology": _object(case.get("topology")),
                             "pdk": result.get("pdk"), "scl": result.get("scl"),
                             "flow": result.get("flow") or _object(context).get("flow"),
                             "fidelity": fidelity, "targets": targets, "target_reasons": target_reasons,
                             "status": status, "eligible": not reasons, "reasons": reasons})
    rows.sort(key=lambda row: (row["timestamp"] is None, row["timestamp"] or "", row["id"]))
    return {"schema_version": 1, "rows": rows, "exclusions": dict(sorted(exclusions.items())),
            "source_files": source_files,
            "summary": {"rows": len(rows), "eligible": sum(row["eligible"] for row in rows),
                        "source_errors": sum("error" in source for source in source_files)},
            "chronology": "UTC day buckets; latest recorded case/revision date, never filename order",
            "scope": "offline regression eligibility; not signoff or model qualification"}
