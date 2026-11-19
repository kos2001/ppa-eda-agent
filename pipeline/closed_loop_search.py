#!/usr/bin/env python3
"""Evidence-driven action selection over :mod:`experiment_graph`.

One loop step proposes at most one changed knob per candidate.  It prioritizes
actions associated with closing the current failure, penalizes measured
regressions, refuses already-tried configurations, and returns an explicit
stop reason when evidence is exhausted.  Execution and signoff stay outside
this module so a ranking can never silently become acceptance.
"""
from __future__ import annotations

import json
from pathlib import Path

import case_retrieval
import experiment_graph

import recovery_memory

STOP_REASONS = ("winner_found", "plateau", "actions_exhausted", "candidates_proposed")


def _results(case: dict) -> list[dict]:
    return [r for it in case.get("iterations", []) or []
            for r in it.get("results", []) or []]


def plateau(results: list[dict], width: int = 3) -> bool:
    """True after distinct configurations produce the same last state."""
    if len(results) < width:
        return False
    tail = results[-width:]
    states = {experiment_graph.state_fingerprint(r) for r in tail}
    configs = {json.dumps(r.get("overrides") or {}, sort_keys=True) for r in tail}
    return len(states) == 1 and len(configs) == width


def propose(case: dict, graph: dict, limit: int = 4, memory=None) -> dict:
    results = _results(case)
    if not results:
        return {"stop_reason": "actions_exhausted", "candidates": [],
                "why": "case has no measured result"}
    if any((r.get("verdict") or {}).get("passed") for r in results):
        return {"stop_reason": "winner_found", "candidates": []}

    current = results[-1]
    state = experiment_graph.result_state(current)
    current_bad = set(state["violations"]) | set(state["unverified"]) | set(state["errors"])
    tried = {json.dumps(r.get("overrides") or {}, sort_keys=True) for r in results}
    nodes = graph["nodes"]
    closures: dict[str, set[tuple]] = {}
    regressions: dict[str, set[tuple]] = {}
    for edge in graph["edges"]:
        if edge["relation"] not in ("associated_with_closure", "associated_with_regression"):
            continue
        target = nodes[edge["target"]]
        signature = target.get("signature")
        if edge["relation"] == "associated_with_closure" and signature not in current_bad:
            continue
        bucket = closures if edge["relation"] == "associated_with_closure" else regressions
        bucket.setdefault(edge["source"], set()).add(
            (edge.get("design"), edge.get("before_run"), edge.get("after_run")))

    base = dict(current.get("overrides") or {})
    candidates = []
    for action_id, evidence in closures.items():
        action = nodes[action_id]
        overrides = dict(base)
        if action.get("value") is None:
            overrides.pop(action["key"], None)
        else:
            overrides[action["key"]] = action.get("value")
        identity = json.dumps(overrides, sort_keys=True)
        if identity in tried:
            continue
        bad_evidence = regressions.get(action_id, set())
        score = len(evidence) - 2 * len(bad_evidence)
        if score <= 0:
            continue
        candidates.append({
            "tag": f"graph-{action['key'].lower()}",
            "overrides": overrides,
            "action": {"key": action["key"], "value": action.get("value")},
            "evidence": {"closures": len(evidence), "regressions": len(bad_evidence)},
            "score": score,
            "advisory_only": True,
        })
    if memory is not None:
        # ChipMEM-style statistical memory (recovery_memory.py): rank by the
        # shrunken posterior that the change closes a current violation net
        # of regression risk.  Held-out by design it ranks closures with AUC
        # 0.64 vs 0.42 for the raw count score (README, 2026-11-19).
        for c in candidates:
            c["recovery_estimate"] = memory.estimate(
                current_bad, c["action"]["key"], c["action"]["value"])
            # ChipMEM's nudge rule (NUDGE_THRESHOLD = 0.20): speak up only
            # when history says the move rarely works.
            if c["recovery_estimate"]["p_close_any"] < recovery_memory.NUDGE_THRESHOLD:
                c["nudge"] = ("historically closes a current violation less than "
                              f"{round(100 * recovery_memory.NUDGE_THRESHOLD)}% of the time; "
                              "consider a different knob")
        candidates.sort(key=lambda c: (-c["recovery_estimate"]["net"], -c["score"],
                                       c["evidence"]["regressions"], c["tag"]))
    else:
        candidates.sort(key=lambda c: (-c["score"], c["evidence"]["regressions"], c["tag"]))
    candidates = candidates[:limit]
    if candidates:
        return {"stop_reason": "candidates_proposed", "candidates": candidates,
                "plateau": plateau(results)}
    if plateau(results):
        return {"stop_reason": "plateau", "candidates": [],
                "why": "distinct configurations repeated the same measured state"}
    return {"stop_reason": "actions_exhausted", "candidates": [],
            "why": "no untried action has closure evidence for the current state"}


def plan(design: str, refdb: Path | str = case_retrieval.REFDB,
         limit: int = 4) -> dict:
    """Build the corpus graph and propose the next measured experiment."""
    cases = case_retrieval.load_cases(refdb)
    matches = [case for case in cases if case.get("design") == design]
    if not matches:
        raise ValueError(f"no case for design {design!r}")
    target = sorted(matches, key=lambda case: (case.get("date", ""),
                                                case.get("_file", "")))[-1]
    graph = experiment_graph.build(cases)
    observed = recovery_memory.observations(cases)
    memory = recovery_memory.RecoveryMemory(observed, recovery_memory.retune_k(observed))
    answer = propose(target, graph, limit, memory)
    answer.update({
        "design": design,
        "case": target.get("_file"),
        "graph": {"nodes": len(graph["nodes"]), "edges": len(graph["edges"])},
    })
    return answer


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--design", required=True)
    ap.add_argument("--refdb", type=Path, default=case_retrieval.REFDB)
    ap.add_argument("--limit", type=int, default=4)
    args = ap.parse_args()
    try:
        answer = plan(args.design, args.refdb, args.limit)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(answer, indent=2))


if __name__ == "__main__":
    main()
