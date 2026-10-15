"""Run the SRAM logical-interface proof and a deliberately wrong negative control.

The macro is replaced by a signal monitor, so this proves neither the memory's
internal behavior nor physical clock skew, SRAM timing or PVT model validity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

from toolchain import OPENLANE_IMAGE, platform_args

ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT = Path("pipeline/designs/sram_wrapper/experiments")


def verify(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=False)
    sources = [EXPERIMENT / name for name in (
        "verify_interface.ys", "verify_interface_negative.ys", "verify_interface_cells.v",
        "sram_wrapper_io2.v")]
    sources += [Path("pipeline/designs/sram_wrapper/src/sram_wrapper.v")]
    hashes = {str(p): hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in sources}
    rows = []
    for name in ("verify_interface", "verify_interface_negative"):
        command = ["docker", "run", "--rm", *platform_args(), "-v", f"{ROOT}:/repo:ro",
                   "-w", "/repo", OPENLANE_IMAGE, "yosys", "-s", str(EXPERIMENT / (name + ".ys"))]
        run = subprocess.run(command, capture_output=True, text=True)
        log = run.stdout + run.stderr
        (out / (name + ".log")).write_text(log, encoding="utf-8")
        expected = ((run.returncode == 0 and "64 are proven and 0 are unproven" in log)
                    if name == "verify_interface" else
                    (run.returncode != 0 and "unproven $equiv cells" in log))
        rows.append({"name": name, "returncode": run.returncode,
                     "expected_outcome_observed": expected, "command": command,
                     "log_sha256": hashlib.sha256(log.encode()).hexdigest()})
    unchanged = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == value
                    for p, value in hashes.items())
    result = {"image": OPENLANE_IMAGE, "sources_sha256": hashes, "runs": rows,
              "sources_unchanged": unchanged,
              "passed": unchanged and all(r["expected_outcome_observed"] for r in rows),
              "scope": "Logical sampled macro-input interface only; ideal gates and signal monitor",
              "unverified": ["SRAM internal functionality", "physical clock skew", "Liberty PVT/arc/load qualification"]}
    (out / "summary.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    result = verify(args.out)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["passed"] else 1)
