#!/usr/bin/env python3
"""Dependency-free MCP stdio server exposing this pipeline's real
operations to agents — pattern ported from
github.com/kos2001/strongarm-sizing-console's mcp_server.py (a sibling
analog-IC console that does the same "give agents first-class tools
instead of shelled-out commands" thing for its own ngspice pipeline).
Same protocol shape (JSON-RPC 2.0 over stdio, initialize/tools/list/
tools/call), same "no third-party packages" constraint, adapted to call
this repo's own pipeline modules directly (not proxied over HTTP to a
running backend, since this pipeline has no always-on server process —
each tool call runs a real OpenLane/orchestrator/review operation
in-process).

Register with Claude Code (or hermes-agent's api_server) as an MCP
server pointed at this file; see README.md's "MCP server" section.
Within a Claude Code session already working in this repo, a subagent
can just call these Python modules directly via Bash — this server
exists for contexts that want a typed tool boundary instead (a future
session, a non-Claude-Code agent, hermes-agent).
"""
import json
import os
import re
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import equiv_check  # noqa: E402
import odb_query  # noqa: E402
import sta_path  # noqa: E402
import orchestrator  # noqa: E402
import render_layout  # noqa: E402
import request_review  # noqa: E402
import run_stage  # noqa: E402
import self_improve  # noqa: E402
import sta_report  # noqa: E402
import tech_compare  # noqa: E402
import verify_diagnosis  # noqa: E402

PROTOCOL_VERSION = "2024-11-05"
REPO_ROOT = Path(__file__).resolve().parent.parent

TOOLS = [
    {
        "name": "ppa_run_stage",
        "description": "Runs ONE real OpenLane candidate for a design (placement/"
                        "routing/signoff through the full flow, or a real OpenLane "
                        "run failure) and returns its real metrics.json. This is a "
                        "single real Docker+OpenLane run — expect it to take "
                        "roughly a minute or more, not an instant call.",
        "inputSchema": {
            "type": "object",
            "required": ["design", "tag"],
            "properties": {
                "design": {"type": "string", "description": "e.g. 'counter4' — must exist under pipeline/designs/"},
                "tag": {"type": "string", "description": "run tag (becomes runs/<tag>/)"},
                "overrides": {
                    "type": "object",
                    "description": "OpenLane config overrides, e.g. {\"FP_CORE_UTIL\": 35}",
                },
            },
        },
    },
    {
        "name": "ppa_orchestrate",
        "description": "Runs the full real candidate-generation-and-auto-repair "
                        "loop for a design (pipeline/orchestrator.py) against its "
                        "run_spec.json, writing a real reference-db case. Can "
                        "involve several real OpenLane runs — expect minutes, not "
                        "seconds.",
        "inputSchema": {
            "type": "object",
            "required": ["design"],
            "properties": {
                "design": {"type": "string"},
                "max_parallel": {"type": "integer", "default": 1},
                "max_iterations": {"type": "integer"},
            },
        },
    },
    {
        "name": "ppa_get_case",
        "description": "Reads the latest real reference-db case for a design "
                        "(read-only, fast — no new run). Returns the same JSON "
                        "the dashboard's Layout Pipeline tab renders.",
        "inputSchema": {
            "type": "object",
            "required": ["design"],
            "properties": {"design": {"type": "string"}},
        },
    },
    {
        "name": "ppa_self_improve_scan",
        "description": "Runs pipeline/self_improve.py's scan: real auto-repair "
                        "coverage per design, auto-generates review requests for "
                        "any OPEN case with no review yet, and flags "
                        "pattern-promotion candidates.",
        "inputSchema": {
            "type": "object",
            "properties": {"design": {"type": "string", "description": "omit to scan all designs"}},
        },
    },
    {
        "name": "ppa_request_review",
        "description": "Generates a human-in-the-loop review request for a "
                        "design's latest OPEN case (pipeline/request_review.py "
                        "request) — names the relevant subagents and includes the "
                        "existing diagnosis. Does not dispatch the subagent "
                        "itself; the caller does that next, then calls "
                        "ppa_apply_review with the result.",
        "inputSchema": {
            "type": "object",
            "required": ["design"],
            "properties": {"design": {"type": "string"}},
        },
    },
    {
        "name": "ppa_apply_review",
        "description": "Applies a subagent's real review response back into a "
                        "design's reference-db case (pipeline/request_review.py "
                        "apply) — appends to the diagnosis field and the "
                        "human_in_the_loop history, via json.dump only.",
        "inputSchema": {
            "type": "object",
            "required": ["design", "agent", "response_text"],
            "properties": {
                "design": {"type": "string"},
                "agent": {"type": "string", "description": "e.g. 'feedback-optimizer'"},
                "response_text": {"type": "string"},
            },
        },
    },
    {
        "name": "ppa_render_layout",
        "description": "Renders a real PNG of a completed run's actual GDS layout "
                        "via KLayout (bundled in the OpenLane Docker image already "
                        "used, no new dependency) — view the returned file with the "
                        "Read tool afterward. Applies arxiv.org/html/2605.06936v3's "
                        "finding that layout images measurably improve DRC-fixing "
                        "accuracy over text-only diagnosis.",
        "inputSchema": {
            "type": "object",
            "required": ["design", "tag", "output"],
            "properties": {
                "design": {"type": "string"},
                "tag": {"type": "string", "description": "run tag under designs/<design>/runs/"},
                "output": {"type": "string", "description": "where to write the PNG"},
                "size": {"type": "integer", "default": 900},
            },
        },
    },
    {
        "name": "ppa_verify_diagnosis",
        "description": "Cross-checks a design's latest case diagnosis prose "
                        "against that case's own recorded data, flagging EDA "
                        "error codes and candidate tags it cites that the run "
                        "never actually produced. Groundedness only — it does "
                        "NOT judge whether the diagnosis is correct.",
        "inputSchema": {
            "type": "object",
            "required": ["design"],
            "properties": {"design": {"type": "string"}},
        },
    },
    {
        "name": "ppa_equiv_check",
        "description": "Proves a run's synthesized netlist is functionally "
                        "equivalent to its RTL using Yosys SAT equivalence "
                        "(~1s). Fills the gap DRC/LVS/timing leave open: LVS "
                        "compares layout against netlist, nothing compared "
                        "netlist against RTL, so a clean-but-wrong synthesis "
                        "result would otherwise pass.",
        "inputSchema": {
            "type": "object",
            "required": ["design", "tag"],
            "properties": {"design": {"type": "string"}, "tag": {"type": "string"}},
        },
    },
    {
        "name": "ppa_sta_report",
        "description": "Reads the OpenSTA analysis a run already produced — the "
                        "real critical path stage by stage (which cell, how much "
                        "delay, what share of arrival) and the max_slew/max_cap/"
                        "max_fanout violators. Fast, read-only, no re-run: turns "
                        "'worst setup WNS -0.05' into which path and which cell.",
        "inputSchema": {
            "type": "object",
            "required": ["design", "tag"],
            "properties": {
                "design": {"type": "string"}, "tag": {"type": "string"},
                "corner": {"type": "string", "description": "omit for all corners"},
            },
        },
    },
    {
        "name": "ppa_sta_path",
        "description": "Traces slew and delay STAGE BY STAGE along the path to one "
                        "chosen pin, by running a fresh report_checks. This is the "
                        "question no stored report answers: ppa_sta_report says WHICH "
                        "pins violate, this says WHY a given one does — which cell in "
                        "the chain adds the slew. It found that OpenROAD was repairing "
                        "a macro's slew violation with delay cells, after five config "
                        "variables had been tried and all were null. Note the path is "
                        "usually NOT the critical path (that case had +18.57ns setup "
                        "slack), so take the pin from ppa_sta_report's max-slew "
                        "violator list rather than from the timing report.",
        "inputSchema": {
            "type": "object",
            "required": ["design", "tag", "pin"],
            "properties": {
                "design": {"type": "string"}, "tag": {"type": "string"},
                "pin": {"type": "string",
                        "description": "endpoint, e.g. 'u_sram/addr0[3]'"},
                "corner": {"type": "string", "description": "default tt_025C_1v80"},
            },
        },
    },
    {
        "name": "ppa_odb_query",
        "description": "Queries a run's real OpenROAD database (.odb) directly for "
                        "measured per-net placement facts — pin count, HPWL and max "
                        "span in microns. Answers questions about ONE specific net "
                        "that OpenLane's aggregate metrics.json cannot, e.g. whether "
                        "a driver actually landed adjacent to a macro pin. Read-only "
                        "and fast; needs a run that reached floorplan or later.",
        "inputSchema": {
            "type": "object",
            "required": ["design", "tag"],
            "properties": {
                "design": {"type": "string"},
                "tag": {"type": "string"},
                "net_filter": {"type": "string", "description": "regex on net name"},
            },
        },
    },
    {
        "name": "ppa_tech_compare",
        "description": "Runs the SAME design through two or more standard-cell "
                        "technologies (real full OpenLane runs, one per variant, "
                        "so expect minutes each) and returns a real PPA delta "
                        "with the design invariants held fixed — the technology "
                        "half of DTCO. A variant that fails to build is reported "
                        "as a real finding, not dropped.",
        "inputSchema": {
            "type": "object",
            "required": ["design", "variants"],
            "properties": {
                "design": {"type": "string"},
                "variants": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "standard-cell libraries, e.g. "
                                    "[\"sky130_fd_sc_hd\", \"sky130_fd_sc_hs\"]; "
                                    "the first is the baseline",
                },
            },
        },
    },
]


def _tool_run_stage(args: dict) -> dict:
    design_dir = REPO_ROOT / "pipeline" / "designs" / args["design"]
    overrides = [f"{k}={orchestrator.override_value(v)}" for k, v in args.get("overrides", {}).items()]
    run_dir = run_stage.run_stage(design_dir, args["tag"], to_step=None, overrides=overrides)
    return {"run_dir": str(run_dir), "metrics": run_stage.read_metrics(run_dir)}


def _tool_orchestrate(args: dict) -> dict:
    # Delegates to orchestrator.orchestrate() instead of re-implementing
    # the iterate/repair loop here — this file used to duplicate it, and
    # the duplicate had actually drifted: it collapsed the "winner found"
    # and "max_iterations reached" exits into one untagged branch (`if
    # winner or iteration >= max_iterations: break`), silently losing the
    # distinction orchestrator.py's own loop preserves. One real
    # total-guarded implementation, called from both places, so this
    # can't happen again (see orchestrator.py's STOP_REASONS /
    # "Graph engineering" doc section).
    design_dir = REPO_ROOT / "pipeline" / "designs" / args["design"]
    run_spec_path = design_dir / "run_spec.json"
    run_spec = json.loads(run_spec_path.read_text())
    design_name = run_spec.get("design_name", args["design"])
    max_iterations = args.get("max_iterations") or run_spec.get("max_iterations", 3)
    max_parallel = max(1, args.get("max_parallel", 1))

    all_iterations, winner, stop_reason, _exploration = orchestrator.orchestrate(
        design_dir, run_spec, max_iterations, max_parallel
    )

    case_file = orchestrator.write_case(design_name, design_dir, all_iterations, winner, stop_reason)
    return {
        "winner_tag": winner["tag"] if winner else None,
        "iterations_run": len(all_iterations),
        "stop_reason": stop_reason,
        "case_file": str(case_file),
    }


def _tool_get_case(args: dict) -> dict:
    _case_file, case = request_review.latest_case(args["design"])
    return case


def _tool_self_improve_scan(args: dict) -> dict:
    designs = [args["design"]] if args.get("design") else sorted(
        d.name for d in self_improve.DESIGNS_DIR.iterdir() if d.is_dir()
    )
    return {d: self_improve.scan_design(d) for d in designs}


def _tool_request_review(args: dict) -> dict:
    ns = SimpleNamespace(design=args["design"])
    request_review.cmd_request(ns)
    out_file = request_review.REFDB / "reviews"
    # cmd_request prints the path; re-derive it the same way rather than
    # parse stdout, since latest_case() + the same date is exactly how it
    # names the file.
    case_file, case = request_review.latest_case(args["design"])
    review_file = out_file / f"{args['design']}__{case['date']}__request.md"
    return {"review_request_file": str(review_file),
            "content": review_file.read_text() if review_file.exists() else None}


def _tool_apply_review(args: dict) -> dict:
    tmp_path = REPO_ROOT / "pipeline" / f".mcp_review_response_{os.getpid()}.txt"
    tmp_path.write_text(args["response_text"])
    try:
        ns = SimpleNamespace(design=args["design"], agent=args["agent"], response_file=tmp_path)
        request_review.cmd_apply(ns)
    finally:
        tmp_path.unlink(missing_ok=True)
    case_file, case = request_review.latest_case(args["design"])
    return {"case_file": str(case_file), "human_in_the_loop": case.get("human_in_the_loop")}


def _tool_render_layout(args: dict) -> dict:
    run_dir = REPO_ROOT / "pipeline" / "designs" / args["design"] / "runs" / args["tag"]
    output_path = Path(args["output"])
    out = render_layout.render_gds_png(run_dir, output_path, args.get("size", 900))
    return {"png_path": str(out)}


def _tool_verify_diagnosis(args: dict) -> dict:
    _case_file, case = request_review.latest_case(args["design"])
    return verify_diagnosis.verify_case(case)


def _tool_equiv_check(args: dict) -> dict:
    design_dir = REPO_ROOT / "pipeline" / "designs" / args["design"]
    run_dir = design_dir / "runs" / args["tag"]
    return equiv_check.check(design_dir, run_dir)


def _tool_sta_report(args: dict) -> dict:
    run_dir = REPO_ROOT / "pipeline" / "designs" / args["design"] / "runs" / args["tag"]
    return sta_report.read_run(run_dir, args.get("corner"))


def _tool_sta_path(args: dict) -> dict:
    design_dir = REPO_ROOT / "pipeline" / "designs" / args["design"]
    run_dir = design_dir / "runs" / args["tag"]
    return sta_path.trace(design_dir, run_dir, args["pin"],
                          corner=args.get("corner", "tt_025C_1v80"))


def _tool_odb_query(args: dict) -> dict:
    run_dir = REPO_ROOT / "pipeline" / "designs" / args["design"] / "runs" / args["tag"]
    data = odb_query.query(run_dir)
    pattern = args.get("net_filter")
    if pattern:
        rx = re.compile(pattern)
        data["nets"] = [n for n in data["nets"] if rx.search(n["net"])]
    return data


def _tool_tech_compare(args: dict) -> dict:
    design_dir = REPO_ROOT / "pipeline" / "designs" / args["design"]
    run_spec_path = design_dir / "run_spec.json"
    targets = {}
    if run_spec_path.exists():
        targets = json.loads(run_spec_path.read_text()).get("targets", {})
    report = tech_compare.compare(design_dir, args["variants"], targets)
    report["report_file"] = str(tech_compare.write_report(report))
    return report


_TOOL_IMPL = {
    "ppa_run_stage": _tool_run_stage,
    "ppa_orchestrate": _tool_orchestrate,
    "ppa_get_case": _tool_get_case,
    "ppa_self_improve_scan": _tool_self_improve_scan,
    "ppa_request_review": _tool_request_review,
    "ppa_apply_review": _tool_apply_review,
    "ppa_render_layout": _tool_render_layout,
    "ppa_verify_diagnosis": _tool_verify_diagnosis,
    "ppa_equiv_check": _tool_equiv_check,
    "ppa_sta_report": _tool_sta_report,
    "ppa_sta_path": _tool_sta_path,
    "ppa_odb_query": _tool_odb_query,
    "ppa_tech_compare": _tool_tech_compare,
}


def _send(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def _result(rid, result: dict) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "result": result})


def _error(rid, code: int, msg: str) -> None:
    _send({"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": msg}})


def handle(msg: dict) -> None:
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        _result(rid, {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "ppa-eda-agent-pipeline", "version": "0.1.0"},
        })
    elif method == "ping":
        _result(rid, {})
    elif method in ("notifications/initialized", "notifications/cancelled"):
        pass
    elif method == "tools/list":
        _result(rid, {"tools": TOOLS})
    elif method == "tools/call":
        params = msg.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {}) or {}
        impl = _TOOL_IMPL.get(name)
        if impl is None:
            _error(rid, -32602, f"unknown tool {name}")
            return
        try:
            out = impl(args)
            _result(rid, {"content": [{"type": "text", "text": json.dumps(out, indent=2, default=str)}]})
        except Exception as e:  # surface real errors to the caller, not a crash
            _error(rid, -32000, f"{name} failed: {e}")
    elif rid is not None:
        _error(rid, -32601, f"method not found: {method}")


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        handle(msg)


if __name__ == "__main__":
    main()
