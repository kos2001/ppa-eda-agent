#!/usr/bin/env python3
"""Typed evidence graph over recorded EDA experiments.

The graph does not claim causality.  An override changed between two measured
runs can be *associated with* a closure or regression; only a controlled DOE
can promote that association to a causal rule.  This distinction is why the
edge types are deliberately explicit.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import case_retrieval


_UNVERIFIED_KINDS = (
    ("clock domain", "unverified:cdc-unconstrained"),
    ("extrapolated beyond", "unverified:macro-model"),
    ("macro timing model", "unverified:macro-model"),
    ("Magic DRC on abstracts", "unverified:magic-abstract"),
    ("unmapped instance", "unverified:unmapped"),
)


def _unverified_signatures(verdict: dict) -> set[str]:
    """Fingerprint checks that could not produce a signoff result."""
    found = set()
    for message in verdict.get("unverified", []) or []:
        matched = False
        for needle, label in _UNVERIFIED_KINDS:
            if needle in message:
                found.add(label)
                matched = True
                break
        if not matched:
            # Reuse the repository's public signoff classifier while keeping
            # unverified checks distinct from measured violations.
            labels = case_retrieval.signoff_signatures({"violations": [message]})
            found.update("unverified:" + label.removeprefix("signoff:")
                         for label in labels)
    return found


def _token(value) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _add_node(graph: dict, node_id: str, kind: str, **attrs) -> None:
    old = graph["nodes"].get(node_id)
    node = {"id": node_id, "kind": kind, **attrs}
    if old is not None and old != node:
        raise ValueError(f"node id collision: {node_id}")
    graph["nodes"][node_id] = node


def _add_edge(graph: dict, source: str, relation: str, target: str, **attrs) -> None:
    edge = {"source": source, "relation": relation, "target": target, **attrs}
    key = json.dumps(edge, sort_keys=True, separators=(",", ":"), default=str)
    graph["_edges"][key] = edge


def result_state(result: dict) -> dict:
    verdict = result.get("verdict") or {}
    return {
        "passed": bool(verdict.get("passed")),
        "violations": case_retrieval.signoff_counts(verdict),
        "unverified": sorted(_unverified_signatures(verdict)),
        "errors": sorted(case_retrieval.signatures(result.get("error", ""))),
    }


def state_fingerprint(result: dict) -> str:
    """Stable identity of the observed outcome, excluding PPA noise."""
    return _token(result_state(result))


def _changed_overrides(previous: dict, current: dict) -> dict:
    before = previous.get("overrides") or {}
    after = current.get("overrides") or {}
    return {key: after.get(key) for key in sorted(set(before) | set(after))
            if before.get(key) != after.get(key)}


_REPAIR_SUFFIX = re.compile(r"-iter\d+$")


def _measured(result: dict) -> bool:
    """A run that produced a verdict or a tool error, not a deferral."""
    return not result.get("not_evaluated") and bool(result.get("verdict") or result.get("error"))


def _parent_ref(result: dict, source: str) -> tuple[str, str] | None:
    """The run this result was derived from, when the record says so.

    An explicit ``parent`` (``{"source": case file, "tag": tag}``, the
    source defaulting to this case) wins.  Otherwise the orchestrator's own
    naming is the record: a repair is ``<parent>-iter<N>`` and a polish
    trial is ``<winner>-polish-<move>``.  Nothing else is a lineage.
    """
    parent = result.get("parent")
    if isinstance(parent, dict) and parent.get("tag"):
        return parent.get("source") or source, parent["tag"]
    tag = str(result.get("tag") or "")
    if _REPAIR_SUFFIX.search(tag):
        return source, _REPAIR_SUFFIX.sub("", tag)
    if "-polish-" in tag:
        return source, tag.split("-polish-", 1)[0]
    return None


def _bad(state: dict) -> set[str]:
    return set(state["violations"]) | set(state["unverified"]) | set(state["errors"])


def _associate(graph: dict, design: str, basis: str,
               before: tuple[str, dict], after: tuple[str, dict]) -> None:
    """Credit a single changed knob with the states it closed or opened.

    Only a one-knob change on the same technology is attributable; a
    multi-knob step is an interaction this graph cannot split.
    """
    (before_run, before_result), (after_run, after_result) = before, after
    if not (_measured(before_result) and _measured(after_result)):
        return
    if any(before_result.get(field) != after_result.get(field) for field in ("pdk", "scl")):
        return
    changed = _changed_overrides(before_result, after_result)
    if len(changed) != 1:
        return
    (key, value), = changed.items()
    before_bad = _bad(result_state(before_result))
    after_bad = _bad(result_state(after_result))
    action_id = f"action:{_token([key, value])}"
    _add_node(graph, action_id, "action", key=key, value=value)
    _add_edge(graph, action_id, "applied_to", after_run,
              previous_value=(before_result.get("overrides") or {}).get(key))
    for relation, signatures in (("associated_with_closure", before_bad - after_bad),
                                 ("associated_with_regression", after_bad - before_bad)):
        for signature in sorted(signatures):
            _add_edge(graph, action_id, relation, f"violation:{signature}",
                      design=design, basis=basis,
                      before_run=before_run, after_run=after_run)


def build(cases: list[dict]) -> dict:
    graph = {"schema": 1, "nodes": {}, "_edges": {}}
    # (source, tag) -> (run id, result, design); the latest record wins.
    by_tag: dict[tuple[str, str], tuple[str, dict, str]] = {}
    ordered_runs: list[tuple[str, str, int, str, dict]] = []

    for case_index, case in enumerate(cases):
        design = str(case.get("design") or "unknown")
        design_id = f"design:{design}"
        _add_node(graph, design_id, "design", name=design,
                  topology=case.get("topology") or {})
        source = case.get("_file") or f"case:{case_index}"
        image = (case.get("toolchain") or {}).get("openlane_image")
        for iteration_index, iteration in enumerate(case.get("iterations", []) or []):
            for result_index, result in enumerate(iteration.get("results", []) or []):
                run_id = f"run:{_token([source, iteration_index, result_index])}"
                state = result_state(result)
                _add_node(graph, run_id, "run", design=design,
                          tag=result.get("tag"), source=source,
                          iteration=iteration_index, result_index=result_index,
                          pdk=result.get("pdk"), scl=result.get("scl"), image=image,
                          state=state, seconds=result.get("seconds"),
                          area_um2=(result.get("verdict") or {}).get("area_um2"))
                _add_edge(graph, design_id, "has_run", run_id)

                config = {"overrides": result.get("overrides") or {},
                          "pdk": result.get("pdk"), "scl": result.get("scl")}
                config_id = f"config:{_token(config)}"
                _add_node(graph, config_id, "config", **config)
                _add_edge(graph, run_id, "used_config", config_id)

                for field in ("pdk", "scl"):
                    if result.get(field):
                        tech_id = f"{field}:{result[field]}"
                        _add_node(graph, tech_id, "technology", field=field,
                                  value=result[field])
                        _add_edge(graph, run_id, "used_technology", tech_id)
                if result.get("stage"):
                    stage_id = f"stage:{result['stage']}"
                    _add_node(graph, stage_id, "stage", name=result["stage"])
                    _add_edge(graph, run_id, "reached_stage", stage_id)

                observed = _bad(state)
                for signature in sorted(observed):
                    violation_id = f"violation:{signature}"
                    _add_node(graph, violation_id, "violation", signature=signature)
                    _add_edge(graph, run_id, "observed", violation_id,
                              count=state["violations"].get(signature))
                if result.get("tag"):
                    by_tag[(source, str(result["tag"]))] = (run_id, result, design)
                ordered_runs.append((source, design, iteration_index, run_id, result))

    # A transition is only what the record says happened: a run derived
    # from a parent (a repair, a polish trial, or an explicit parent), or a
    # controlled comparison between siblings of one iteration that differ
    # in exactly one knob.  Parallel siblings were never run one after the
    # other, and separate case files are not linked merely because they
    # share a design name.
    for source, design, _iteration, run_id, result in ordered_runs:
        ref = _parent_ref(result, source)
        parent = by_tag.get(ref) if ref else None
        if parent is None or parent[0] == run_id:
            continue
        parent_run, parent_result, _design = parent
        _add_edge(graph, parent_run, "followed_by", run_id,
                  changed=sorted(_changed_overrides(parent_result, result)))
        _associate(graph, design, "lineage", (parent_run, parent_result), (run_id, result))

    siblings: dict[tuple[str, int], list[tuple[str, dict, str]]] = {}
    for source, design, iteration_index, run_id, result in ordered_runs:
        siblings.setdefault((source, iteration_index), []).append((run_id, result, design))
    for group in siblings.values():
        for left_index, (left_run, left, design) in enumerate(group):
            for right_run, right, _design in group[left_index + 1:]:
                if len(_changed_overrides(left, right)) != 1:
                    continue
                _add_edge(graph, left_run, "compared_with", right_run)
                _associate(graph, design, "controlled_sibling",
                           (left_run, left), (right_run, right))
                _associate(graph, design, "controlled_sibling",
                           (right_run, right), (left_run, left))

    graph["edges"] = list(graph.pop("_edges").values())
    validate(graph)
    return graph


def validate(graph: dict) -> None:
    nodes = graph.get("nodes") or {}
    for edge in graph.get("edges") or []:
        if edge.get("source") not in nodes or edge.get("target") not in nodes:
            raise ValueError(f"edge endpoint missing: {edge}")
        if not edge.get("relation"):
            raise ValueError(f"edge relation missing: {edge}")


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cases", nargs="+", type=Path)
    ap.add_argument("--out", type=Path)
    args = ap.parse_args()
    loaded = []
    for path in args.cases:
        case = json.loads(path.read_text(encoding="utf-8"))
        case["_file"] = path.as_posix()
        loaded.append(case)
    graph = build(loaded)
    text = json.dumps(graph, indent=2)
    if args.out:
        args.out.write_text(text + "\n", encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
