"""Bounded, resumable measured context-policy ablation for one digital design.

Uses the existing OpenLane command boundary and scoring. Model calls are test
subjects, with command tools/connectors disabled, never delegated EDA workers.
All measurements stay in the experiment directory and native run directories.
"""
import argparse
from contextlib import redirect_stderr, redirect_stdout
import copy
import json
import math
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import tempfile
import time

import benchmark_toolchain
import equiv_check
import evaluation_archive
from evaluation_budget import EvaluationBudget
import evaluation_provenance
import orchestrator
import review_context as context
from review_context_eval import signature
from winner_selection import annotated_total_w

ROOT = Path(__file__).resolve().parents[1]
DISABLED = ("shell_tool", "apps", "plugins", "multi_agent", "browser_use",
            "browser_use_external", "computer_use", "image_generation", "view_image",
            "skill_search", "memories", "hooks", "code_mode", "code_mode_host",
            "shell_snapshot", "sleep_tool")


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temp.replace(path)


def read(path):
    return json.loads(Path(path).read_text())


def finite(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def qualification(row):
    reason = evaluation_archive.eligible(row)
    if reason:
        return reason
    proof = row.get("final_equivalence") or {}
    if proof.get("equivalent") is not True or proof.get("vacuous") is not False:
        return "final_equivalence_unverified"
    pa = (row.get("verdict") or {}).get("power_activity") or {}
    if "counter4_tb: 0 error(s)" not in pa.get("simulation_tail", ""):
        return "independent_testbench_unverified"
    if not pa.get("parasitics") or not pa.get("annotated_pins"):
        return "activity_or_parasitics_unverified"
    if not all(finite(x) and x > 0 for x in [annotated_total_w(row), row["verdict"].get("area_um2")]):
        return "ranking_metrics_unknown"
    return None


def objective(row):
    if qualification(row):
        return None
    return row["verdict"]["area_um2"] * annotated_total_w(row)


def select_seeds(rows, cap=2):
    valid = [r for r in rows if objective(r) is not None]
    if not valid:
        # Diagnostic seeds retain their failure state. Never rank them as PPA.
        return rows[:cap]
    keys = {r["evaluation_provenance"]["compatibility_key"] for r in valid}
    if len(keys) != 1:
        raise ValueError("cannot select seeds across provenance cohorts")
    def vector(row):
        v = row["verdict"]
        return (v["area_um2"], annotated_total_w(row), v["core_area_um2"], -v["worst_setup_slack"])
    points = {r["tag"]: vector(r) for r in valid}
    if any(not all(finite(v) for v in p) for p in points.values()):
        raise ValueError("seed frontier requires complete shared metrics")
    frontier = [r for r in valid if not any(
        all(a <= b for a, b in zip(points[s["tag"]], points[r["tag"]]))
        and any(a < b for a, b in zip(points[s["tag"]], points[r["tag"]]))
        for s in valid)]
    # Keep the best objective and a distinct best floorplan/timing tradeoff.
    first = min(frontier, key=lambda r: (objective(r), r["tag"]))
    others = sorted((r for r in frontier if r is not first),
                    key=lambda r: (r["verdict"]["core_area_um2"], -r["verdict"]["worst_setup_slack"], r["tag"]))
    return [first, *others[:cap - 1]]


def proposal_candidate(response, seeds, protocol, tag):
    if not isinstance(response, dict) or not isinstance(response.get("diagnosis"), str):
        raise ValueError("response requires diagnosis")
    proposed = response.get("candidates")
    if not isinstance(proposed, list) or len(proposed) > 1:
        raise ValueError("at most one candidate is allowed")
    if not proposed:
        return None
    p = proposed[0]
    available = {r["tag"]: r for r in seeds}
    if p.get("seed_tag") not in available:
        raise ValueError("candidate must name a visible retained seed")
    seed = available[p["seed_tag"]]
    delta = p.get("overrides")
    if not isinstance(delta, dict) or len(delta) != 1:
        raise ValueError("change exactly one allowed axis")
    for key, value in delta.items():
        if key not in protocol["allowed_domains"] or value not in protocol["allowed_domains"][key]:
            raise ValueError("override outside the preregistered domain")
    if not isinstance(p.get("hypothesis"), str) or not p["hypothesis"].strip():
        raise ValueError("missing testable hypothesis")
    result = {k: seed[k] for k in ("pdk", "scl", "flow")}
    result.update(tag=tag, overrides={**seed["overrides"], **delta})
    orchestrator.validate_candidates([result])
    return result


def output_schema():
    # Explicit alternatives keep the schema strict while allowing either axis.
    axes = [{"type": "object", "properties": {"FP_CORE_UTIL": {"type": "integer"}},
             "required": ["FP_CORE_UTIL"], "additionalProperties": False},
            {"type": "object", "properties": {"SYNTH_STRATEGY": {"type": "string"}},
             "required": ["SYNTH_STRATEGY"], "additionalProperties": False}]
    proposal = {"type": "object", "properties": {
        "seed_tag": {"type": "string"}, "overrides": {"anyOf": axes},
        "hypothesis": {"type": "string"}},
        "required": ["seed_tag", "overrides", "hypothesis"], "additionalProperties": False}
    return {"type": "object", "properties": {"diagnosis": {"type": "string"},
            "candidates": {"type": "array", "items": proposal}},
            "required": ["diagnosis", "candidates"], "additionalProperties": False}


def model_command(directory, schema, protocol):
    cmd = ["codex", "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check",
           "--json", "--sandbox", "read-only", "-C", str(directory),
           "-m", protocol["model"], "-c", 'model_reasoning_effort="' + protocol["reasoning_effort"] + '"',
           "-c", 'web_search="disabled"', "-c", "project_doc_max_bytes=0",
           "--output-schema", str(schema)]
    for name in DISABLED:
        cmd += ["--disable", name]
    cmd += ["--enable", "skip_host_skill_discovery", "-"]
    return cmd


def parse_events(raw):
    events = [json.loads(line) for line in raw.splitlines() if line.strip()]
    completed = [e for e in events if e.get("type") == "turn.completed"]
    if len(completed) != 1:
        raise ValueError("model turn did not complete exactly once")
    items = [e["item"] for e in events if e.get("type") == "item.completed"]
    forbidden = [i for i in items if i.get("type") not in {"agent_message", "reasoning", "error"}]
    if forbidden:
        raise ValueError("isolation invalid: a model tool was used")
    answers = [i["text"] for i in items if i.get("type") == "agent_message"]
    if not answers:
        raise ValueError("model returned no answer")
    return json.loads(answers[-1]), completed[0]["usage"]


class Experiment:
    def __init__(self, path):
        self.path = Path(path).resolve()
        self.protocol = read(self.path / "protocol.json")
        self.design = ROOT / "pipeline/designs" / self.protocol["design"]
        self.state_path = self.path / "state.json"
        if self.state_path.exists():
            self.state = read(self.state_path)
            manifest = read(self.path / "frozen-inputs.json")
            for name, expected in manifest["source_sha256"].items():
                if context.file_hash(ROOT / name) != expected:
                    raise ValueError(f"source changed after experiment freeze: {name}")
        else:
            self.initialize()
        self.budget = EvaluationBudget(self.protocol["evaluation_budget"])
        self.budget.started = time.monotonic() - (time.time() - self.state["started_epoch"])
        self.budget.evaluations = self.state.get("physical_budget", {}).get("evaluations", [])
        self.budget.denied = self.state.get("physical_budget", {}).get("not_evaluated", [])
        if any(r["status"] == "running" for r in self.budget.evaluations):
            raise ValueError("an interrupted admitted run needs explicit evidence recovery before resume")

    def initialize(self):
        sources = list((ROOT / "pipeline").glob("*.py"))
        sources += [self.design / "config.json", self.path / "protocol.json"]
        sources += [p for folder in ("src", "verify") for p in (self.design / folder).rglob("*") if p.is_file()]
        sources += list((ROOT / ".claude/agents").glob("*.md"))
        sources += list((ROOT / ".codex/skills/ppa-eda-flow").rglob("*.md"))
        sources += [ROOT / "AGENTS.md"]
        hashes = {}
        for p in sources:
            relative = p.relative_to(ROOT)
            hashes[str(relative)] = context.file_hash(p)
            target = self.path / "frozen-sources" / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        observed = benchmark_toolchain.inspect_tools()
        write(self.path / "frozen-inputs.json", {"source_sha256": hashes, "observed_tools": observed,
              "inputs": evaluation_provenance.capture_inputs(self.design), "frozen_epoch": time.time()})
        self.state = {"status": "running", "started_epoch": time.time(), "physical_results": [],
                      "trials": [], "branches": {}, "model_calls": 0, "model_tokens": 0}
        write(self.path / "output-schema.json", output_schema())
        write(self.state_path, self.state)

    def save(self):
        self.state["physical_budget"] = self.budget.snapshot()
        write(self.state_path, self.state)

    def rows(self):
        return [read(self.path / name) for name in self.state["physical_results"]]

    def measure(self, candidate):
        cached = next((r for r in self.rows() if signature(r) == signature(candidate)), None)
        if cached:
            return cached, True
        tag = candidate["tag"]
        if (self.design / "runs" / tag).exists():
            raise ValueError("refusing to overwrite an existing native run: " + tag)
        spec = {"design_name": self.protocol["design"], "targets": self.protocol["targets"],
                "candidate_budget": 1, "evaluation_budget": {"max_evaluations": 1},
                "max_iterations": 1, "candidates": [candidate],
                "search": {"mode": "explicit", "objective": self.protocol["objective"],
                           "reference": "context-search-20261017 original 10 ns baseline", "seed": 42}}
        folder = self.path / "physical" / tag
        write(folder / "run_spec.json", spec)
        validation = subprocess.run(["python3", str(ROOT / "pipeline/orchestrator.py"),
            "--design", str(self.design), "--run-spec", str(folder / "run_spec.json"), "--validate-only"],
            capture_output=True, text=True, check=True)
        (folder / "validation.json").write_text(validation.stdout)
        ticket = self.budget.start("full_flow", tag)
        self.save()
        if ticket is None:
            raise RuntimeError("global physical admission budget exhausted")
        print("Physical admission:", tag, candidate["overrides"], flush=True)
        with (folder / "console.log").open("w") as log, redirect_stdout(log), redirect_stderr(log):
            row = orchestrator.run_candidate(self.design, spec, candidate, verify_fn=True)
            if row.get("run_dir"):
                row["final_equivalence"] = equiv_check.check(self.design, Path(row["run_dir"]),
                    final_netlist=True, reference_synthesis=True, timeout_s=120,
                    log_path=folder / "synthesis-to-final.log")
        row["context_benchmark_qualification"] = qualification(row)
        row["context_benchmark_objective"] = objective(row)
        self.budget.finish(ticket, "tool_error" if row.get("error") else "completed")
        name = f"physical/{tag}/result.json"
        write(self.path / name, row)
        self.state["physical_results"].append(name)
        self.save()
        print("Physical result:", tag, "qualification=", qualification(row),
              "objective=", objective(row), "seconds=", row["seconds"], flush=True)
        return row, False

    def seeds(self):
        for candidate in self.protocol["initial_seeds"]:
            self.measure(candidate)

    def prompt(self, policy, rows, seeds, previous, round_number):
        visible = seeds if policy == "seed_only" else rows
        # Use the shared filter, with additional final-proof evidence preserved.
        evidence = context.factual_case({"iterations": [{"results": visible}]})
        for result, original in zip(context.results(evidence), visible):
            result["final_equivalence"] = context.facts(original.get("final_equivalence"))
            result["benchmark_qualification"] = qualification(original)
        capsule = {"policy": policy, "round": round_number, "objective": self.protocol["objective"],
                   "constraints": {"CLOCK_PERIOD": 10, "targets": self.protocol["targets"],
                                   "required_gates": self.protocol["gates"]},
                   "allowed_domains": self.protocol["allowed_domains"],
                   "remaining_branch_exposures": self.protocol["rounds"] - round_number + 1,
                   "selected_seed_tags": [r["tag"] for r in seeds], "evidence": evidence}
        if policy == "full_history":
            capsule["prior_dialogue"] = previous
        return ("You are the candidate proposer in a measured EDA context ablation. Use only the supplied "
                "capsule. No tools or outside history. Every proposed candidate must name one selected "
                "seed_tag and supply a DELTA changing exactly one allowed axis to a listed value. "
                "Keep the seed's other settings, PDK, SCL, flow and all original constraints. "
                "Return diagnosis and at most one candidate with seed_tag, overrides and hypothesis. "
                "An empty candidate list is allowed if no supported idea remains. Do not claim predicted "
                "PPA is measured. Attempt a novel configuration rather than retesting a visible one. "
                "Evidence includes failures; they never qualify for PPA ranking. Optimize the declared "
                "objective, reporting tradeoffs honestly.\n\n" + context.canonical(capsule))

    def ask(self, trial, prompt):
        folder = self.path / "trials" / trial
        folder.mkdir(parents=True, exist_ok=False)
        (folder / "prompt.txt").write_text(prompt)
        max_calls = self.protocol["model_budget"]["max_calls"]
        max_tokens = self.protocol["model_budget"]["max_total_tokens"]
        if self.state["model_calls"] >= max_calls or self.state["model_tokens"] >= max_tokens:
            raise RuntimeError("model admission budget exhausted")
        self.state["model_calls"] += 1
        self.save()
        with tempfile.TemporaryDirectory(prefix="ppa-context-subject-") as tmp:
            cmd = model_command(Path(tmp), self.path / "output-schema.json", self.protocol)
            write(folder / "command.json", cmd)
            started = time.monotonic()
            proc = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                timeout=self.protocol["model_budget"]["per_call_timeout_seconds"])
        (folder / "events.jsonl").write_text(proc.stdout)
        (folder / "stderr.log").write_text(proc.stderr)
        measured = {"wall_seconds": time.monotonic() - started, "exit_code": proc.returncode,
                    "prompt_sha256": context.digest(prompt), "model": self.protocol["model"],
                    "reasoning_effort": self.protocol["reasoning_effort"], "cost_usd": None}
        try:
            if proc.returncode:
                raise ValueError("model command failed")
            response, usage = parse_events(proc.stdout)
            measured["usage"] = usage
            self.state["model_tokens"] += usage.get("input_tokens", 0) + usage.get("output_tokens", 0)
            write(folder / "response.json", response)
        except (ValueError, KeyError) as error:
            measured["error"] = str(error)
            response = None
        write(folder / "measurement.json", measured)
        self.save()
        return response, measured

    def search(self):
        self.seeds()
        initial = self.rows()[:len(self.protocol["initial_seeds"])]
        if not any(objective(r) is not None for r in initial):
            raise RuntimeError("no qualified initial seed; inspect original gates before model search")
        initial_keys = {r["evaluation_provenance"]["compatibility_key"] for r in initial if objective(r) is not None}
        if len(initial_keys) != 1:
            raise ValueError("initial seeds are not provenance-compatible")
        schedule = []
        rng = random.Random(self.protocol["random_seed"])
        for round_number in range(1, self.protocol["rounds"] + 1):
            block = [(policy, rep) for rep in range(1, self.protocol["repetitions"] + 1)
                     for policy in self.protocol["policies"]]
            rng.shuffle(block)
            schedule += [(round_number, policy, rep) for policy, rep in block]
        write(self.path / "schedule.json", schedule)
        completed = {t["id"] for t in self.state["trials"]}
        for round_number, policy, rep in schedule:
            trial = f"rep{rep}-{policy}-round{round_number}"
            if trial in completed:
                continue
            branch_id = f"rep{rep}-{policy}"
            branch = self.state["branches"].setdefault(branch_id, {"tags": [r["tag"] for r in initial],
                                                                   "dialogue": []})
            by_tag = {r["tag"]: r for r in self.rows()}
            rows = [by_tag[tag] for tag in branch["tags"]]
            seeds = select_seeds(rows)
            prompt = self.prompt(policy, rows, seeds, branch["dialogue"], round_number)
            print("Model trial:", trial, flush=True)
            response, usage = self.ask(trial, prompt)
            record = {"id": trial, "policy": policy, "replicate": rep, "round": round_number,
                      "model": usage, "visible_seed_tags": [r["tag"] for r in seeds],
                      "status": "model_error" if response is None else "valid"}
            if response is not None:
                try:
                    candidate = proposal_candidate(response, seeds, self.protocol,
                        f"ctx17-{policy.replace('_', '-')}-p{rep}-r{round_number}")
                    record["empty"] = candidate is None
                    if candidate:
                        record["candidate"] = candidate
                        record["within_branch_repeat"] = signature(candidate) in {signature(r) for r in rows}
                        result, reused = self.measure(candidate)
                        record.update(physical_tag=result["tag"], cache_reuse=reused,
                                      qualification=qualification(result), objective=objective(result),
                                      evaluation_exposures=1)
                        if result["tag"] not in branch["tags"]:
                            branch["tags"].append(result["tag"])
                    branch["dialogue"].append({"round": round_number, "prompt": prompt, "response": response})
                except ValueError as error:
                    record.update(status="invalid_proposal", error=str(error))
            current = [r for r in self.rows() if r["tag"] in branch["tags"]]
            qualified = [r for r in current if objective(r) is not None]
            record["best_objective"] = min(map(objective, qualified)) if qualified else None
            record["best_tag"] = min(qualified, key=objective)["tag"] if qualified else None
            self.state["trials"].append(record)
            self.save()
        self.state["status"] = "completed"
        self.save()
        self.report()

    def report(self):
        rows = self.rows()
        initial = rows[:len(self.protocol["initial_seeds"])]
        qualified = [r for r in initial if objective(r) is not None]
        base = min(map(objective, qualified)) if qualified else None
        summaries = []
        for policy in self.protocol["policies"]:
            trials = [t for t in self.state["trials"] if t["policy"] == policy]
            finals = [max((t for t in trials if t["replicate"] == rep), key=lambda t: t["round"], default=None)
                      for rep in range(1, self.protocol["repetitions"] + 1)]
            improvements = [100 * (1 - t["best_objective"] / base) for t in finals
                            if t and base is not None and t["best_objective"] is not None]
            usage = {key: sum(t["model"].get("usage", {}).get(key, 0) for t in trials)
                     for key in ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"]}
            summaries.append({"policy": policy, "model_calls": len(trials),
                "valid_responses": sum(t["status"] == "valid" for t in trials),
                "empty_responses": sum(t.get("empty", False) for t in trials),
                "within_branch_repeats": sum(t.get("within_branch_repeat", False) for t in trials),
                "unique_proposed_configurations": len({signature(t["candidate"]) for t in trials if "candidate" in t}),
                "qualified_proposals": sum(t.get("physical_tag") is not None and t.get("qualification") is None for t in trials),
                "model_wall_seconds": sum(t["model"]["wall_seconds"] for t in trials),
                "usage": usage, "final_best_by_replicate": finals,
                "objective_improvement_pct_by_replicate": improvements,
                "median_objective_improvement_pct": statistics.median(improvements) if improvements else None})
        metrics = []
        for r in rows:
            v = r.get("verdict") or {}
            metrics.append({"tag": r["tag"], "overrides": r["overrides"], "qualification": qualification(r),
                "objective": objective(r), "area_um2": v.get("area_um2"), "core_area_um2": v.get("core_area_um2"),
                "activity_power_w": annotated_total_w(r), "vectorless_power_w": (v.get("power") or {}).get("total_w"),
                "worst_setup_slack_ns": v.get("worst_setup_slack"), "timing_corners": v.get("timing_corners"),
                "physical_seconds": r.get("seconds"), "compatibility_key": (r.get("evaluation_provenance") or {}).get("compatibility_key"),
                "violations": v.get("violations"), "unverified": v.get("unverified"), "error": r.get("error")})
        write(self.path / "summary.json", {"status": self.state["status"], "baseline_best_objective": base,
            "policy_summaries": summaries, "physical_metrics": metrics,
            "actual_unique_physical_evaluations": len(rows), "physical_budget": self.budget.snapshot(),
            "model_tokens": self.state["model_tokens"], "cost_usd": None,
            "elapsed_seconds": time.time() - self.state["started_epoch"],
            "limitations": ["one small design", "three model repeats", "no independent physical repeats",
                "prompt-history replay rather than continuous-session resume", "conditional trigger is observational",
                "shared physical cache and per-arm evaluation exposures differ from actual tool invocations",
                "ChatGPT subscription billing not exposed; token usage is measured, dollar cost unknown"]})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--experiment", type=Path, required=True)
    parser.add_argument("--stage", choices=["seeds", "search", "report"], required=True)
    args = parser.parse_args()
    exp = Experiment(args.experiment)
    getattr(exp, args.stage)()


if __name__ == "__main__":
    main()
