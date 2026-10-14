#!/usr/bin/env python3
"""Run the evidence graph as a measured OpenLane feedback loop.

Planning is the default.  ``--execute`` is required before any EDA process is
started.  ``--backend wsl`` relaunches the same loop inside WSL so Docker and
Linux-only helpers share one filesystem and one Python process.
"""
from __future__ import annotations

import argparse
import copy
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import case_retrieval
import closed_loop_search
import experiment_graph
import orchestrator
from case_storage import write_case_json

REPO_ROOT = Path(__file__).resolve().parent.parent

_INFRASTRUCTURE_ERRORS = (
    "CERTIFICATE_VERIFY_FAILED", "docker: command not found",
    "'docker' is not recognized", "No such image", "Cannot connect to the Docker",
    "Version not found locally, attempting to download",
    "rate limit exceeded", "Failed to obtain",
)


def _all_results(cases: list[dict], design: str) -> list[dict]:
    return [result for case in cases if case.get("design") == design
            for iteration in case.get("iterations", []) or []
            for result in iteration.get("results", []) or []]


def _identity(result_or_candidate: dict) -> str:
    config = {
        "overrides": result_or_candidate.get("overrides") or {},
        "pdk": result_or_candidate.get("pdk"),
        "scl": result_or_candidate.get("scl"),
    }
    return json.dumps(config, sort_keys=True, separators=(",", ":"))


def _latest(cases: list[dict], design: str) -> dict:
    matches = [case for case in cases if case.get("design") == design]
    if not matches:
        raise ValueError(f"no recorded case for design {design!r}")
    return sorted(matches, key=lambda case: (case.get("date", ""),
                                              case.get("_file", "")))[-1]


def _checkpoint(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    write_case_json(path, payload)


def _infrastructure_failure(result: dict) -> bool:
    error = result.get("error") or ""
    return not result.get("verdict") and any(mark in error for mark in _INFRASTRUCTURE_ERRORS)


def run_loop(design_dir: Path, run_spec: dict, cases: list[dict], *,
             max_rounds: int = 4, limit: int = 4, max_parallel: int = 1,
             execute: bool = False, checkpoint: Path | None = None,
             runner=None) -> dict:
    """Plan or execute graph-selected candidates until a total guard fires."""
    design = run_spec.get("design_name", design_dir.name)
    parent = _latest(cases, design)
    prior = _all_results(cases, design)
    if not prior:
        raise ValueError(f"case for {design!r} has no measured results")
    if run_spec.get("expected_outcome") == "fail":
        return {
            "design": design,
            "parent_case": parent.get("_file"),
            "executed": False,
            "iterations": [],
            "winner": None,
            "stop_reason": "expected_failure_control",
            "next_candidates": [],
            "why": run_spec.get("expected_outcome_reason")
                   or "the run specification declares an expected failure",
            "publish_case": False,
        }
    existing_winner = orchestrator.pick_winner(prior)
    if existing_winner:
        return {
            "design": design,
            "parent_case": parent.get("_file"),
            "executed": False,
            "iterations": [],
            "winner": existing_winner,
            "stop_reason": "winner_found",
            "next_candidates": [],
            "why": "a previously measured candidate already passes signoff",
            "publish_case": False,
        }
    seed = copy.deepcopy(prior[-1])
    session = {
        "design": design,
        "_file": f"graph-loop:{design}",
        "date": datetime.now(timezone.utc).date().isoformat(),
        "topology": parent.get("topology") or {},
        "iterations": [{"iteration": 0, "seed": True, "results": [seed]}],
    }
    measured_iterations: list[dict] = []
    tried = {_identity(result) for result in prior}
    executor = runner or orchestrator.run_candidates
    final_plan: dict = {}

    for round_number in range(1, max_rounds + 1):
        graph = experiment_graph.build(cases + [session])
        # Ask for the complete evidence-ranked set, then remove configurations
        # tried in any prior case of this design (not merely this session).
        plan = closed_loop_search.propose(session, graph, limit=10_000)
        candidates = [candidate for candidate in plan.get("candidates", [])
                      if _identity(candidate) not in tried][:limit]
        plan["candidates"] = candidates
        final_plan = plan
        if plan["stop_reason"] != "candidates_proposed" or not candidates:
            if plan["stop_reason"] == "candidates_proposed":
                plan = {**plan, "stop_reason": "actions_exhausted",
                        "why": "all graph-supported configurations were already measured"}
                final_plan = plan
            break
        if not execute:
            break

        runnable = []
        for index, candidate in enumerate(candidates, 1):
            runnable.append({
                "tag": f"graph-r{round_number}-{index}-{candidate['action']['key'].lower()}",
                "overrides": candidate["overrides"],
                "pdk": candidate.get("pdk"),
                "scl": candidate.get("scl"),
                "graph_evidence": candidate["evidence"],
            })
        results = executor(
            design_dir, {**run_spec, "candidates": runnable},
            max_parallel=max(1, max_parallel),
        )
        infrastructure_blocked = bool(results) and all(
            _infrastructure_failure(result) for result in results)
        for result, candidate in zip(results, candidates):
            result["stage"] = orchestrator.classify_stage(result)
            result["produced_by_feedback"] = True
            result["graph_action"] = candidate["action"]
            result["graph_evidence"] = candidate["evidence"]
            if not _infrastructure_failure(result):
                tried.add(_identity(result))
        iteration = {"iteration": round_number, "graph_selected": True,
                     "results": results}
        measured_iterations.append(iteration)
        if not infrastructure_blocked:
            session["iterations"].append(iteration)

        winner = orchestrator.pick_winner(results)
        status = {
            "schema": 1, "design": design, "parent_case": parent.get("_file"),
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "iterations": measured_iterations,
            "winner_tag": winner.get("tag") if winner else None,
            "stop_reason": ("execution_blocked" if infrastructure_blocked else
                            "winner_found" if winner else "running"),
        }
        if checkpoint:
            _checkpoint(checkpoint, status)
        if infrastructure_blocked:
            final_plan = {"stop_reason": "execution_blocked", "candidates": [],
                          "why": "EDA infrastructure failed before measurement; "
                                 "the candidate remains untried"}
            break
        if winner:
            final_plan = {"stop_reason": "winner_found", "candidates": []}
            break
    else:
        final_plan = {"stop_reason": "max_rounds_reached", "candidates": []}

    winner = orchestrator.pick_winner(
        [result for item in measured_iterations for result in item["results"]])
    return {
        "design": design,
        "parent_case": parent.get("_file"),
        "executed": execute,
        "iterations": measured_iterations,
        "winner": winner,
        "stop_reason": final_plan.get("stop_reason", "actions_exhausted"),
        "next_candidates": final_plan.get("candidates", []),
        "why": final_plan.get("why"),
        "publish_case": bool(measured_iterations) and any(
            result.get("verdict") is not None
            for item in measured_iterations for result in item["results"]),
    }


def _wsl_path(path: Path, distro: str | None) -> str:
    """Translate a Windows drive path without handing backslashes to a shell.

    ``wsl.exe -- wslpath C:\\...`` reconstructs its Linux command line and can
    consume the backslashes before ``wslpath`` sees them.  Drive mounts have a
    deterministic representation and preserve Unicode path components.
    """
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    if drive:
        tail = resolved.as_posix().split(":", 1)[1].lstrip("/")
        return f"/mnt/{drive}/{tail}"
    command = ["wsl.exe"] + (["-d", distro] if distro else [])
    command += ["--", "wslpath", "-a", str(resolved)]
    return subprocess.check_output(command, text=True).strip()


def relaunch_wsl(args) -> int:
    prefix = ["wsl.exe"] + (["-d", args.distro] if args.distro else [])
    repo = _wsl_path(REPO_ROOT, args.distro)
    design = _wsl_path(args.design, args.distro)
    run_spec = _wsl_path(args.run_spec, args.distro)
    command = prefix + ["--cd", repo, "python3", "pipeline/graph_loop.py",
                        "--design", design, "--run-spec", run_spec,
                        "--backend", "native", "--max-rounds", str(args.max_rounds),
                        "--limit", str(args.limit), "--max-parallel", str(args.max_parallel)]
    if args.execute:
        command.append("--execute")
    return subprocess.run(command).returncode


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", required=True, type=Path)
    parser.add_argument("--run-spec", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--backend", choices=("native", "wsl"), default="native")
    parser.add_argument("--distro")
    parser.add_argument("--max-rounds", type=int, default=4)
    parser.add_argument("--limit", type=int, default=4)
    parser.add_argument("--max-parallel", type=int, default=1)
    args = parser.parse_args()
    if args.backend == "wsl":
        raise SystemExit(relaunch_wsl(args))

    run_spec = json.loads(args.run_spec.read_text(encoding="utf-8"))
    design = run_spec.get("design_name", args.design.name)
    checkpoint = (case_retrieval.REFDB / "checkpoints"
                  / f"{design}__graph-loop.json")
    result = run_loop(args.design, run_spec, case_retrieval.load_cases(),
                      max_rounds=args.max_rounds, limit=args.limit,
                      max_parallel=args.max_parallel, execute=args.execute,
                      checkpoint=checkpoint)
    if args.execute and result["publish_case"]:
        case_file = orchestrator.write_case(
            design, args.design, result["iterations"], result["winner"],
            result["stop_reason"], expected_outcome=run_spec.get("expected_outcome"))
        case = json.loads(case_file.read_text(encoding="utf-8"))
        case["graph_loop"] = {"parent_case": result["parent_case"],
                              "checkpoint": str(checkpoint), "schema": 1}
        write_case_json(case_file, case)
        result["case_file"] = str(case_file)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
