"""Read-only evidence handoff check; never a design signoff or tool runner."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath


STATES = {"pass", "fail", "unverified", "infrastructure_failure", "budget_deferred"}
SPECIALISTS = {
    "macro_model": "sram-model-qualifier",
    "rtl_to_synthesis": "equivalence-specialist",
    "synthesis_to_final": "equivalence-specialist",
}


def inspect(manifest: dict, root: Path) -> dict:
    """Check a declared review contract, not the truth of its measurements.

    Paths are relative to the manifest's directory. Hashes prove retained bytes,
    not provenance or model quality; the independent evaluator checks those.
    """
    errors, unresolved, roles = [], [], set()
    if not isinstance(manifest, dict):
        raise ValueError("manifest must be an object")
    if type(manifest.get("schema_version")) is not int or manifest["schema_version"] != 1:
        errors.append("schema_version must be 1")
    for field in ("run_id", "design", "toolchain", "target_identity"):
        if not isinstance(manifest.get(field), str) or not manifest[field].strip():
            errors.append(f"missing identity: {field}")
    gates = manifest.get("gates")
    required = manifest.get("required_gates")
    artifacts = manifest.get("artifacts")
    if not isinstance(gates, dict) or not gates:
        errors.append("gates must be a nonempty object")
        gates = {}
    if (not isinstance(required, list) or not required
            or any(not isinstance(g, str) or not g for g in required)):
        errors.append("required_gates must be a nonempty list of names")
        required = []
    if len(set(required)) != len(required):
        errors.append("duplicate required gate")
    if not isinstance(artifacts, dict) or not artifacts:
        errors.append("artifacts must be a nonempty object")
        artifacts = {}
    root = root.resolve()
    for name, record in artifacts.items():
        try:
            if not isinstance(record, dict):
                raise ValueError("record must be an object")
            relative, expected = record.get("path"), record.get("sha256")
            if not isinstance(relative, str) or not relative:
                raise ValueError("missing relative path")
            if Path(relative).is_absolute() or PureWindowsPath(relative).drive or "\\" in relative:
                raise ValueError("use a portable relative path")
            path = (root / relative).resolve()
            if not path.is_relative_to(root):
                raise ValueError("path escapes evidence root")
            if not path.is_file():
                raise ValueError("missing regular file")
            if (not isinstance(expected, str) or len(expected) != 64
                    or any(c not in "0123456789abcdef" for c in expected)):
                raise ValueError("invalid SHA256")
            with path.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            if actual != expected:
                raise ValueError("SHA256 mismatch")
        except (OSError, ValueError, RuntimeError) as exc:
            errors.append(f"artifact {name}: {exc}")
    for name in sorted(set(required) | set(gates)):
        gate = gates.get(name)
        if (not isinstance(gate, dict) or not isinstance(gate.get("state"), str)
                or gate["state"] not in STATES):
            errors.append(f"gate {name}: missing or invalid state")
            roles.add(SPECIALISTS.get(name, "verification-ppa-evaluator"))
            continue
        state = gate["state"]
        evidence = gate.get("evidence", [])
        if (not isinstance(evidence, list)
                or any(not isinstance(key, str) or key not in artifacts for key in evidence)):
            errors.append(f"gate {name}: invalid evidence references")
        elif state in {"pass", "fail"} and not evidence:
            errors.append(f"gate {name}: measured verdict requires evidence")
        if state != "pass":
            unresolved.append({"gate": name, "state": state})
            role = ("evidence-environment-auditor" if state == "infrastructure_failure"
                    else "feedback-optimizer" if state == "budget_deferred"
                    else SPECIALISTS.get(name, "verification-ppa-evaluator"))
            roles.add(role)
    if errors:
        roles.add("evidence-environment-auditor")
    roles.add("verification-ppa-evaluator")
    return {"handoff_valid": not errors, "all_declared_gates_pass": not errors and not unresolved,
            "errors": errors, "unresolved": unresolved, "recommended_agents": sorted(roles),
            "limitation": "Declared evidence integrity only; not independent signoff or automatic dispatch."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    args = parser.parse_args()
    try:
        report = inspect(json.loads(args.manifest.read_text(encoding="utf-8")), args.manifest.parent)
    except (OSError, ValueError) as exc:
        print(json.dumps({"handoff_valid": False, "errors": [str(exc)]}))
        return 2
    print(json.dumps(report, indent=2))
    return 0 if report["all_declared_gates_pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
