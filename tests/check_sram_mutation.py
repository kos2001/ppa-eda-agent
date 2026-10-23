"""Prove the SRAM oracle rejects corrupted writes without modifying real RTL."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import functional_regression


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", type=Path, required=True)
    args = ap.parse_args()
    original = ROOT / "pipeline/designs/sram_wrapper"
    spec = json.loads((original / "run_spec_functional_check.json").read_text())["functional_regression"]
    work = ROOT / ".cache/sram-corrupt-write"
    (work / "src").mkdir(parents=True, exist_ok=True)
    (work / "verify").mkdir(exist_ok=True)
    (work / "config.json").write_bytes((original / "config.json").read_bytes())
    rtl = (original / "src/sram_wrapper.v").read_text()
    if rtl.count("{24'd0, addr_ctr}") != 1:
        raise ValueError("mutation site changed")
    (work / "src/sram_wrapper.v").write_text(rtl.replace("{24'd0, addr_ctr}", "{24'd1, addr_ctr}"))
    (work / spec["testbench"]).write_bytes((original / spec["testbench"]).read_bytes())
    result = functional_regression.check(work, work / "run", {**spec, "post_route": False})
    detected = (result["status"] == "failed" and len(result["runs"]) == 3
                and all(r["returncode"] == 0 and r["oracle"]["status"] == "failed" for r in result["runs"]))
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps({"detected": detected, "mutation": "write data bit 8 stuck high", "result": result}, indent=2) + "\n")
    print(f"SRAM corrupt write detected: {detected}")
    raise SystemExit(0 if detected else 1)


if __name__ == "__main__":
    main()
