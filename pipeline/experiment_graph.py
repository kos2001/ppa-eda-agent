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
from pathlib import Path

import case_retrieval


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
        "unverified": sorted(case_retrieval.unverified_signatures(verdict)),
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


def build(cases: list[dict]) -> dict:
    graph = {"schema": 1, "nodes": {}, "_edges": {}}
    histories: dict[tuple[str, str], list[tuple[str, int, int, dict]]] = {}

    for case_index, case in enumerate(cases):
        design = str(case.get("design") or "unknown")
        design_id = f"design:{design}"
        _add_node(graph, design_id, "design", name=design,
                  topology=case.get("topology") or {})
        source = case.get("_file") or f"case:{case_index}"
        for iteration_index, iteration in enumerate(case.get("iterations", []) or []):
            for result_index, result in enumerate(iteration.get("results", []) or []):
                run_id = f"run:{_token([source, iteration_index, result_index])}"
                state = result_state(result)
                _add_node(graph, run_id, "run", design=design,
                          tag=result.get("tag"), source=source,
                          iteration=iteration_index, result_index=result_index,
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

                observed = set(state["violations"]) | set(state["unverified"]) | set(state["errors"])
                for signature in sorted(observed):
                    violation_id = f"violation:{signature}"
                    _add_node(graph, violation_id, "violation", signature=signature)
                    _add_edge(graph, run_id, "observed", violation_id,
                              count=state["violations"].get(signature))
                histories.setdefault((design, source), []).append(
                    (source, iteration_index, result_index, result))

    # A file is one recorded experiment history.  Do not manufacture a
    # transition between the tail of one case and the head of another merely
    # because they share a design name.
    for (design, _source), ordered in histories.items():
        ordered.sort(key=lambda item: (item[0], item[1], item[2]))
        for before_item, after_item in zip(ordered, ordered[1:]):
            before, after = before_item[3], after_item[3]
            before_run = f"run:{_token(list(before_item[:3]))}"
            after_run = f"run:{_token(list(after_item[:3]))}"
            _add_edge(graph, before_run, "followed_by", after_run)
            changed = _changed_overrides(before, after)
            if not changed:
                continue
            before_state, after_state = result_state(before), result_state(after)
            before_bad = (set(before_state["violations"]) | set(before_state["unverified"])
                          | set(before_state["errors"]))
            after_bad = (set(after_state["violations"]) | set(after_state["unverified"])
                         | set(after_state["errors"]))
            for key, value in changed.items():
                action_id = f"action:{_token([key, value])}"
                _add_node(graph, action_id, "action", key=key, value=value)
                _add_edge(graph, action_id, "applied_to", after_run,
                          previous_value=(before.get("overrides") or {}).get(key))
                for signature in sorted(before_bad - after_bad):
                    _add_edge(graph, action_id, "associated_with_closure",
                              f"violation:{signature}", design=design,
                              before_run=before_run, after_run=after_run)
                for signature in sorted(after_bad - before_bad):
                    _add_edge(graph, action_id, "associated_with_regression",
                              f"violation:{signature}", design=design,
                              before_run=before_run, after_run=after_run)

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
