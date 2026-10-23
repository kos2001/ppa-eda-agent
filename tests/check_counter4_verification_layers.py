"""Live controls for the independent oracle and final-netlist regression."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import functional_regression as regression


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    original = ROOT / "pipeline/designs/counter4"
    spec = json.loads((original / "run_spec.json").read_text())["functional_regression"]
    results = {}
    for kind in ("lying_scoreboard", "wrong_final_output"):
        work = ROOT / ".cache/verification-layers" / kind
        design = work / "design"
        (design / "src").mkdir(parents=True, exist_ok=True)
        (design / "verify").mkdir(exist_ok=True)
        (design / "config.json").write_bytes((original / "config.json").read_bytes())
        rtl = (original / "src/counter4.v").read_text()
        tb = (original / spec["testbench"]).read_text()
        if kind == "lying_scoreboard":
            assert rtl.count("count + 4'd1") == 1
            assert tb.count("if (int'(count) != total % 16)") == 1
            rtl = rtl.replace("count + 4'd1", "count + 4'd2")
            tb = tb.replace("if (int'(count) != total % 16)", "if (0)")
        (design / "src/counter4.v").write_text(rtl)
        (design / spec["testbench"]).write_text(tb)
        run = work / "run"
        if kind == "wrong_final_output":
            (run / "final/nl").mkdir(parents=True, exist_ok=True)
            (run / "resolved.json").write_bytes((args.run_dir / "resolved.json").read_bytes())
            netlist = (args.run_dir / "final/nl/counter4.nl.v").read_text()
            assert netlist.count("output3 (.A(net3)") == 1
            (run / "final/nl/counter4.nl.v").write_text(netlist.replace("output3 (.A(net3)", "output3 (.A(net4)"))
        result = regression.check(design, run, {**spec, "post_route": kind == "wrong_final_output"})
        if kind == "lying_scoreboard":
            detected = (result["status"] == "failed" and len(result["runs"]) == 3
                        and all(r["returncode"] == 0 and r["oracle"]["status"] == "failed" for r in result["runs"]))
        else:
            detected = (result.get("rtl_status") == "passed" and result["status"] == "failed"
                        and result["post_route"].get("compile", {}).get("returncode") == 0
                        and len(result["post_route"]["runs"]) == 3
                        and all(r["status"] == "failed" for r in result["post_route"]["runs"]))
        results[kind] = {"detected": detected, "result": result}
        print(f"{kind}: detected={detected}", flush=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(results, indent=2) + "\n")
    raise SystemExit(0 if all(r["detected"] for r in results.values()) else 1)


if __name__ == "__main__":
    main()
