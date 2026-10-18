"""Retain supplementary proof logs and export one measured reference-db case."""
from pathlib import Path
import sys

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / "pipeline/context_search_benchmark.py").is_file())
sys.path.insert(0, str(ROOT / "pipeline"))
import benchmark_toolchain
import equiv_check
import orchestrator
from context_search_benchmark import objective, read, select_seeds, write
from review_context import file_hash

HERE = Path(__file__).resolve().parent


if __name__ == "__main__":
    state = read(HERE / "state.json")
    protocol = read(HERE / "protocol.json")
    if state["status"] != "completed":
        raise ValueError("comparison has not finished")
    frozen = read(HERE / "frozen-inputs.json")
    observed = benchmark_toolchain.inspect_tools()
    if observed != frozen["observed_tools"]:
        raise ValueError("installed tool identity changed during experiment")
    write(HERE / "observed-tools-final.json", observed)
    design = ROOT / "pipeline/designs" / protocol["design"]
    rows = [read(HERE / name) for name in state["physical_results"]]
    for row in rows:
        if objective(row) is None:
            continue
        folder = HERE / "physical" / row["tag"]
        record = folder / "rtl-to-synthesis-retained.json"
        log = folder / "rtl-to-synthesis-retained.log"
        if record.exists():
            proof = read(record)
            if file_hash(log) != proof["log_sha256"]:
                raise ValueError("retained proof log changed")
        else:
            proof = equiv_check.check(design, design / "runs" / row["tag"],
                                     log_path=log, timeout_s=120)
            write(record, proof)
        if proof.get("equivalent") is not True or proof.get("vacuous") is not False:
            raise ValueError("supplementary RTL proof did not establish equivalence")
        if proof.get("netlist_sha256") != row["equivalence"].get("netlist_sha256"):
            raise ValueError("supplementary proof used a different synthesized netlist")
        print("Retained proof:", row["tag"], proof["proven_points"], "proven points", flush=True)
    export = HERE / "case-export.json"
    if not export.exists():
        spec = {"design_name": protocol["design"], "targets": protocol["targets"],
                "candidate_budget": protocol["candidate_budget"],
                "evaluation_budget": protocol["evaluation_budget"], "max_iterations": 1,
                "candidates": [{k: row[k] for k in ("tag", "overrides", "pdk", "scl", "flow")} for row in rows],
                "search": {"mode": "explicit", "objective": protocol["objective"],
                           "reference": str((HERE / "protocol.json").relative_to(ROOT)), "seed": 42}}
        plan = orchestrator.validate_run_plan(spec, 1)
        write(HERE / "measured-candidate-plan.json", spec)
        write(HERE / "measured-candidate-plan-validation.json", plan)
        case = orchestrator.write_case(protocol["design"], design,
            [{"iteration": 1, "results": rows, "evaluation_budget": state["physical_budget"]}],
            select_seeds(rows)[0], stop_reason="winner_found", search_plan=plan)
        write(export, {"case_file": str(case.relative_to(ROOT)), "sha256": file_hash(case),
                      "measured_rows": len(rows),
                      "note": "A recorded passing winner is not a claim of PPA improvement. Tool-error rows retain unknown physical results."})
    else:
        stored = read(export)
        if file_hash(ROOT / stored["case_file"]) != stored["sha256"]:
            raise ValueError("exported case changed")
    print("Case export:", read(export)["case_file"], flush=True)
