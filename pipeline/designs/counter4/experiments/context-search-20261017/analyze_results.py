"""Audit completed measured trials and produce a comparison report and plots."""
from collections import Counter
import hashlib
import itertools
import json
from pathlib import Path
import statistics
import sys

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "pipeline/context_search_benchmark.py").is_file())
sys.path.insert(0, str(ROOT / "pipeline"))
from context_search_benchmark import objective, qualification, read, select_seeds, write
from review_context import file_hash, canonical
from review_context_eval import signature

HERE = Path(__file__).resolve().parent
LABELS = {"full_history": "Full history", "fresh_evidence": "Measured history", "seed_only": "Seed only"}


def total_known(values):
    return sum(values) if values and all(isinstance(v, (int, float)) for v in values) else None


def metrics(row):
    v = row.get("verdict") or {}
    pa = v.get("power_activity") or {}
    return {"tag": row["tag"], "overrides": row.get("overrides"),
            "qualified": qualification(row) is None, "qualification_issue": qualification(row),
            "objective_um2_w": objective(row), "area_um2": v.get("area_um2"),
            "activity_power_w": ((pa.get("annotated") or {}).get("total") or {}).get("total_w"),
            "power_corner": pa.get("corner"), "core_area_um2": v.get("core_area_um2"),
            "worst_setup_slack_ns": v.get("worst_setup_slack"),
            "worst_setup_wns_ns": v.get("worst_setup_wns"),
            "worst_hold_wns_ns": min((c["hold_wns"] for c in v.get("timing_corners", [])
                                      if c.get("hold_wns") is not None), default=None),
            "violations": v.get("violations"), "unverified": v.get("unverified"),
            "tool_error": row.get("error"),
            "compatibility_key": (row.get("evaluation_provenance") or {}).get("compatibility_key")}


def audit_and_report():
    protocol = read(HERE / "protocol.json")
    state = read(HERE / "state.json")
    expected = protocol["repetitions"] * protocol["rounds"] * len(protocol["policies"])
    if state["status"] != "completed" or len(state["trials"]) != expected:
        raise ValueError("the preregistered model comparison is incomplete")
    if state["model_calls"] != expected:
        raise ValueError("model call accounting differs from the expected trial count")
    if any(e["status"] == "running" for e in state["physical_budget"]["evaluations"]):
        raise ValueError("an admitted physical run has not completed")
    frozen = read(HERE / "frozen-inputs.json")
    for relative, digest in frozen["source_sha256"].items():
        if file_hash(ROOT / relative) != digest:
            raise ValueError("frozen source changed: " + relative)
    auth = read(HERE / "user-authorization.json")
    if auth["approved_scope_sha256"] != file_hash(HERE / "approval-scope.json"):
        raise ValueError("approved payload scope changed")
    rows = [read(HERE / name) for name in state["physical_results"]]
    admissions = state["physical_budget"]["evaluations"]
    if len(admissions) != len(rows) or len(rows) > protocol["evaluation_budget"]["max_evaluations"]:
        raise ValueError("physical admission accounting mismatch")
    if len({signature(r) for r in rows}) != len(rows):
        raise ValueError("duplicate effective physical evaluations were submitted")
    by_tag = {r["tag"]: r for r in rows}
    initial = rows[:len(protocol["initial_seeds"])]
    baseline = select_seeds(initial)[0]
    baseline_score = objective(baseline)
    if baseline_score is None:
        raise ValueError("baseline does not satisfy the ranking contract")
    compatible = {r["evaluation_provenance"]["compatibility_key"] for r in rows if objective(r) is not None}
    if len(compatible) != 1:
        raise ValueError("qualified PPA results are not a single provenance cohort")
    for row in rows:
        native = ROOT / "pipeline/designs" / protocol["design"] / "runs" / row["tag"]
        resolved = read(native / "resolved.json")
        if resolved.get("CLOCK_PERIOD") != protocol["fixed_clock_period_ns"]:
            raise ValueError("original clock constraint changed")
        if objective(row) is not None:
            proof = row["final_equivalence"]
            log = HERE / "physical" / row["tag"] / "synthesis-to-final.log"
            if file_hash(log) != proof["log_sha256"]:
                raise ValueError("final proof transcript integrity failure")
    trial_rows = []
    for trial in state["trials"]:
        folder = HERE / "trials" / trial["id"]
        raw = [json.loads(line) for line in (folder / "events.jsonl").read_text().splitlines() if line.strip()]
        completions = [e for e in raw if e.get("type") == "turn.completed"]
        usage = completions[0].get("usage") if len(completions) == 1 else None
        items = [e["item"] for e in raw if e.get("type") == "item.completed"]
        tool_items = [i for i in items if i.get("type") not in {"agent_message", "reasoning", "error"}]
        if tool_items:
            raise ValueError("a model subject used a tool: " + trial["id"])
        measurement = read(folder / "measurement.json")
        prompt = (folder / "prompt.txt").read_text()
        if measurement["prompt_sha256"] != hashlib.sha256(canonical(prompt).encode()).hexdigest():
            raise ValueError("prompt changed: " + trial["id"])
        if usage is not None and measurement.get("usage") != usage:
            raise ValueError("recorded token usage differs from raw model events")
        if trial.get("physical_tag"):
            physical = by_tag[trial["physical_tag"]]
            if signature(trial["candidate"]) != signature(physical):
                raise ValueError("proposal-to-physical mapping mismatch")
        # Missing usage remains unknown instead of silently contributing zero.
        trial_rows.append({**trial, "raw_usage": usage,
                           "prompt_file": str((folder / "prompt.txt").relative_to(HERE)),
                           "response_file": str((folder / "response.json").relative_to(HERE)),
                           "prompt_bytes": len(prompt.encode()),
                           "prompt_sha256": file_hash(folder / "prompt.txt")})
    token_sum = total_known([t["raw_usage"]["input_tokens"] + t["raw_usage"]["output_tokens"]
                             if t["raw_usage"] is not None else None for t in trial_rows])
    if token_sum is not None and token_sum != state["model_tokens"]:
        raise ValueError("global model token accounting mismatch")
    policies = {}
    for policy in protocol["policies"]:
        trials = [t for t in trial_rows if t["policy"] == policy]
        candidates = [t for t in trials if t.get("candidate")]
        branches = []
        for rep in range(1, protocol["repetitions"] + 1):
            branch = sorted([t for t in trials if t["replicate"] == rep], key=lambda t: t["round"])
            if len(branch) != protocol["rounds"]:
                raise ValueError("missing round in " + policy)
            reached = [*initial]
            trajectory = [0.0]
            identities = {r["tag"] for r in initial}
            for t in branch:
                if t.get("physical_tag") and t["physical_tag"] not in identities:
                    reached.append(by_tag[t["physical_tag"]])
                    identities.add(t["physical_tag"])
                score = min(objective(r) for r in reached if objective(r) is not None)
                if score != t["best_objective"]:
                    raise ValueError("best-objective trajectory does not match source measurements")
                trajectory.append(100 * (1 - score / baseline_score))
            retained = select_seeds(reached)
            selected = retained[0]
            branches.append({"replicate": rep, "improvement_pct_by_round": trajectory,
                "final_selected": metrics(selected), "retained_seed_tags": [r["tag"] for r in retained],
                "last_two_rounds_improvement_over_0_5_pct": (trajectory[-1] - trajectory[-3]) > 0.5,
                "model_usage_known": all(t["raw_usage"] is not None for t in branch)})
        usage_totals = {key: total_known([t["raw_usage"].get(key) if t["raw_usage"] is not None else None
                                       for t in trials])
                        for key in ["input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens"]}
        improvements = [b["improvement_pct_by_round"][-1] for b in branches]
        policies[policy] = {"model_calls": len(trials),
            "valid_responses": sum(t["status"] == "valid" for t in trials),
            "empty_responses": sum(t.get("empty", False) for t in trials),
            "candidate_proposals": len(candidates),
            "within_branch_repeats": sum(t.get("within_branch_repeat", False) for t in candidates),
            "unique_proposed_configurations": len({signature(t["candidate"]) for t in candidates}),
            "qualified_proposals": sum(t.get("physical_tag") is not None and t.get("qualification") is None for t in candidates),
            "global_cache_reuses": sum(t.get("cache_reuse", False) for t in candidates),
            "model_wall_seconds": sum(t["model"]["wall_seconds"] for t in trials),
            "median_call_seconds": statistics.median(t["model"]["wall_seconds"] for t in trials),
            "usage": usage_totals, "branches": branches,
            "final_improvement_pct": {"median": statistics.median(improvements),
                                      "min": min(improvements), "max": max(improvements)}}
    overlaps = []
    paired = []
    for a, b in itertools.combinations(protocol["policies"], 2):
        aset = {signature(t["candidate"]) for t in trial_rows if t["policy"] == a and t.get("candidate")}
        bset = {signature(t["candidate"]) for t in trial_rows if t["policy"] == b and t.get("candidate")}
        overlaps.append({"policies": [a, b], "shared_configurations": len(aset & bset),
                         "union_configurations": len(aset | bset),
                         "jaccard": len(aset & bset) / len(aset | bset) if aset | bset else None})
    for policy in [p for p in protocol["policies"] if p != "full_history"]:
        differences = [b["improvement_pct_by_round"][-1] - a["improvement_pct_by_round"][-1]
                       for a, b in zip(policies["full_history"]["branches"], policies[policy]["branches"])]
        paired.append({"alternative": policy, "reference": "full_history",
                       "improvement_difference_pp_by_replicate": differences,
                       "median_difference_pp": statistics.median(differences),
                       "range_pp": [min(differences), max(differences)]})
    result = {"status": "measured_comparison_complete", "protocol_sha256": file_hash(HERE / "protocol.json"),
        "baseline": metrics(baseline), "policy_results": policies, "paired_comparisons": paired,
        "cross_policy_overlap": overlaps, "physical_candidates": list(map(metrics, rows)),
        "actual_unique_physical_evaluations": len(rows), "physical_budget": state["physical_budget"],
        "physical_qualification_issues": dict(Counter(qualification(r) or "qualified" for r in rows)),
        "model_calls": len(trial_rows), "model_tokens": state["model_tokens"], "model_cost_usd": None,
        "trials": trial_rows,
        "interpretation_limits": ["one 4-bit design and three model repetitions per policy",
            "three feedback rounds; no causal test of adaptive stagnation triggers",
            "shared measured physical cache, not independent physical repetitions",
            "OpenSTA activity power rounded to three significant figures; no silicon power or physical noise estimate",
            "model generation seed and dollar billing unavailable; seed 42 randomizes schedule only",
            "policy tool costs cannot be compared from actual cached wall time",
            "model wall time includes provider latency/cache effects and fixed CLI overhead"]}
    write(HERE / "analysis.json", result)
    return result


def plot(result):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    colors = ["#365a91", "#1b8b72", "#d87a34"]
    fig, axes = plt.subplots(1, 3, figsize=(13, 3.7), layout="constrained")
    for color, (policy, data) in zip(colors, result["policy_results"].items()):
        trajectories = [b["improvement_pct_by_round"] for b in data["branches"]]
        x = list(range(len(trajectories[0])))
        median = [statistics.median(row[i] for row in trajectories) for i in x]
        lo = [min(row[i] for row in trajectories) for i in x]
        hi = [max(row[i] for row in trajectories) for i in x]
        axes[0].plot(x, median, marker="o", label=LABELS[policy], color=color)
        axes[0].fill_between(x, lo, hi, color=color, alpha=0.12)
    axes[0].set(title="Verified area × power improvement", xlabel="Model round / evaluation exposure", ylabel="Improvement vs baseline (%)", xticks=[0, 1, 2, 3])
    axes[0].legend(fontsize=8)
    policies = list(result["policy_results"])
    labels = [LABELS[p] for p in policies]
    qualified = [result["policy_results"][p]["qualified_proposals"] for p in policies]
    bars = axes[1].bar(labels, qualified, color=colors)
    axes[1].bar_label(bars, labels=[f"{v}/9" for v in qualified], padding=3)
    axes[1].set(title="Proposals with complete verified PPA", ylabel="Qualified evaluation exposures", ylim=(0, 9.8))
    totals = [result["policy_results"][p]["usage"]["input_tokens"] for p in policies]
    if all(v is not None for v in totals):
        bars = axes[2].bar(labels, [v / 1000 for v in totals], color=colors)
        axes[2].bar_label(bars, fmt="%.1f", padding=3)
    axes[2].set(title="Measured input tokens (9 calls each)", ylabel="Thousand tokens")
    for ax in axes:
        ax.spines[["top", "right"]].set_visible(False)
        ax.grid(axis="y", alpha=0.2)
    fig.suptitle("counter4 · fixed 10 ns · SKY130 HD · median and range of 3 model repeats", fontsize=12)
    fig.savefig(HERE / "comparison.png", dpi=180)
    fig.savefig(HERE / "comparison.svg")
    plt.close(fig)


def markdown(result):
    b = result["baseline"]
    wins = {p: sum(branch["improvement_pct_by_round"][-1] > 0 for branch in d["branches"])
            for p, d in result["policy_results"].items()}
    best = min((r for r in result["physical_candidates"] if r["qualified"]),
               key=lambda r: r["objective_um2_w"])
    delta = {key: 100 * (best[key] / b[key] - 1) for key in
             ["area_um2", "activity_power_w", "core_area_um2", "objective_um2_w"]}
    lines = ["# Measured context-policy search comparison", "",
        "This report compares full history, measured history without earlier model interpretations, and seed-only context. Each policy has three independent model repetitions and three measured feedback rounds on counter4. It measures this context ablation, not all possible effects of the agent update.", "",
        f"**Observed result:** full history reached an improved verified objective in **{wins['full_history']}/3** repetitions, measured history in **{wins['fresh_evidence']}/3**, and seed-only in **{wins['seed_only']}/3**. The reduced-context policies did not outperform full history in this experiment. This is not evidence that forgetting cannot help on other tasks or longer searches.", "",
        "| Policy | Valid responses | Qualified proposals | Repeated proposals in own branch | Unique proposed configs | Input tokens | Model wall time (s) | Final area × power improvement: median [range] |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for policy, data in result["policy_results"].items():
        r = data["final_improvement_pct"]
        lines.append(f"| {policy} | {data['valid_responses']}/9 | {data['qualified_proposals']}/{data['candidate_proposals']} | {data['within_branch_repeats']}/{data['candidate_proposals']} | {data['unique_proposed_configurations']} | {data['usage']['input_tokens']} | {data['model_wall_seconds']:.1f} | {r['median']:.3f}% [{r['min']:.3f}, {r['max']:.3f}] |")
    lines += ["", "![Measured comparison](comparison.png)", "",
        f"Baseline: **{b['area_um2']:.3f} µm² cell area**, **{b['activity_power_w'] * 1e6:.3f} µW activity-annotated power**, **{b['core_area_um2']:.3f} µm² core area**, **{b['worst_setup_slack_ns']:.6f} ns worst setup slack**, at fixed 10 ns clock. The two initial candidates were measured in this same frozen provenance cohort. Only their nondominated measured seed is retained initially.", "",
        f"Best measured configuration: **{best['overrides']}** (`{best['tag']}`). Cell area changed to **{best['area_um2']:.3f} µm² ({delta['area_um2']:+.3f}%)** and activity power to **{best['activity_power_w'] * 1e6:.3f} µW ({delta['activity_power_w']:+.3f}%)**, giving **{-delta['objective_um2_w']:.3f}% lower cell-area × power**. **Core area increased to {best['core_area_um2']:.3f} µm² ({delta['core_area_um2']:+.3f}%)**. Worst setup slack changed by **{best['worst_setup_slack_ns'] - b['worst_setup_slack_ns']:+.6f} ns**; setup/hold WNS remain zero. This is a measured tradeoff, not an improvement in every PPA/floorplan metric.", "",
        f"Actual work: **{result['model_calls']} model calls**, **{result['actual_unique_physical_evaluations']} unique full-flow evaluations including two baselines**. A configuration shared by several policies is executed once and its exact measured result is reused. Each proposing branch is charged one evaluation exposure. The actual tool-execution count therefore differs from the 27 model opportunities. Unique-configuration counts are within each policy across its three repetitions; own-branch repeat counts compare against that repetition's earlier measured candidates, including baselines.", "",
        "## Final selected candidates by repetition", "",
        "| Policy / repetition | Candidate | Cell area (µm²) | Activity power (µW) | Core area (µm²) | Worst setup slack (ns) |",
        "| --- | --- | ---: | ---: | ---: | ---: |"]
    for policy, data in result["policy_results"].items():
        for branch in data["branches"]:
            row = branch["final_selected"]
            lines.append(f"| {policy} / {branch['replicate']} | {row['tag']} | {row['area_um2']:.3f} | {row['activity_power_w'] * 1e6:.3f} | {row['core_area_um2']:.3f} | {row['worst_setup_slack_ns']:.6f} |")
    lines += ["", "## What the comparison establishes", "",
        "PPA ranking includes only candidates with complete matching provenance, final Magic/KLayout DRC and LVS, all reported timing corners and electrical/antenna gates, nonvacuous RTL-to-synthesis and synthesis-to-final proofs, and the independent counter testbench. Failed, missing and deferred outcomes never contribute invented zero metrics. The power values are activity-annotated extracted-parasitic OpenSTA analysis at ff_n40C_1v95, not laboratory measurements.", "",
        "Compare individual paired repetitions and their range in `analysis.json`. Three model repeats on one small design do not establish statistical superiority or predict a larger design's behavior. Physical results are shared cache observations, without an independent physical noise estimate. A zero observed gain means none within this experiment's candidate space, rounds and precision; it is not proof that further improvement is impossible. The best-objective trajectory retains the baseline, so a bad proposal does not erase an already verified result.", "",
        "All calls use the same gpt-6.1-sol/high settings, fresh tool-disabled sessions, and the frozen candidate domain. Full history replays each preceding response once alongside measured records; measured history removes these interpretations; seed-only exposes retained seeds. Schedule seed 42 controls trial order, not unavailable model generation randomness. Input/output/reasoning usage and model wall time are measured; monetary billing is unavailable. Cached token counts are reported separately. Model latency includes CLI overhead and provider effects; cached physical runtimes cannot establish per-policy tool speedups.", "",
        "The conditional stagnation trigger is observational here: fixed visibility assignments remain in place. Multiple nondominated seeds are retained only when measurements supply them. This experiment does not independently attribute gains to adaptive triggering or prove optimal seed diversity.", "",
        "## Evidence", "",
        "- `protocol.json`: declared hypotheses, domain, schedule seed and shared budgets.",
        "- `user-authorization.json`: user-approved external inference scope; `approval-scope.json` preserves the original request.",
        "- `frozen-inputs.json` and `frozen-sources/`: exact source hashes, observed tool versions and image digest.",
        "- `trials/`: each prompt, exact model command/events, response and measured usage.",
        "- `physical/`: validated run specs, raw results, console logs and final proof logs. Original native runs remain in `../../runs/` relative to this report's directory. Separately retained RTL-to-synthesis proof retries preserve their own results/logs and do not overwrite original measurement rows.",
        "- `analysis.json`: audited trial mappings, paired differences, overlaps, trajectories, per-candidate metrics and limitations.",
        "- `evidence-manifest.json`: final file-integrity inventory. Integrity alone does not establish a signoff verdict.", ""]
    (HERE / "report.md").write_text("\n".join(lines))


if __name__ == "__main__":
    result = audit_and_report()
    plot(result)
    markdown(result)
    print(json.dumps({"status": result["status"], "model_calls": result["model_calls"],
                      "physical_evaluations": result["actual_unique_physical_evaluations"],
                      "policy_results": {p: {k: d[k] for k in ['within_branch_repeats', 'usage', 'final_improvement_pct']}
                                         for p, d in result["policy_results"].items()}}, indent=2))
