"""Live negative controls. Requires the functional-regression Docker image.

Creates separate faulty copies; never changes the actual design's RTL.
"""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import functional_regression


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    design = ROOT / "pipeline/designs/counter4"
    spec = json.loads((design / "run_spec.json").read_text())["functional_regression"]
    spec = {**spec, "post_route": False}  # These controls mutate RTL, not a routed netlist.
    rtl = (design / "src/counter4.v").read_text()
    mutations = {
        "increment_by_two": ("count + 4'd1", "count + 4'd2"),
        "wrong_reset_priority": ("if (rst)", "if (rst && !en)"),
    }
    results = {}
    for name, (before, after) in mutations.items():
        if rtl.count(before) != 1:
            raise ValueError(f"mutation {name} no longer matches exactly one site")
        directory = ROOT / ".cache/verilator-mutants" / name
        (directory / "src").mkdir(parents=True, exist_ok=True)
        (directory / "verify").mkdir(exist_ok=True)
        (directory / "src/counter4.v").write_text(rtl.replace(before, after))
        (directory / "config.json").write_bytes((design / "config.json").read_bytes())
        (directory / spec["testbench"]).write_bytes((design / spec["testbench"]).read_bytes())
        result = functional_regression.check(directory, directory / "run", spec)
        detected = (result["status"] == "failed"
                    and result.get("compile", {}).get("returncode") == 0
                    and len(result["runs"]) == len(spec["seeds"])
                    and all(run["status"] == "failed" and "mismatch check=" in
                            Path(run["log"]).read_text() for run in result["runs"]))
        results[name] = {"detected": detected, "result": result}
        print(f"{name}: detected={detected}", flush=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if all(item["detected"] for item in results.values()) else 1)


if __name__ == "__main__":
    main()
