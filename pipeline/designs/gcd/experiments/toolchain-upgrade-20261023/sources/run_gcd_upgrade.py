import os
import sys
import json
import importlib.util
from pathlib import Path

outer = Path(__file__).resolve().parents[1]
root = outer / ".cache/gcd-clock-merge"
profile = sys.argv[1]
label = profile + (sys.argv[2] if len(sys.argv) > 2 else "")
assert profile in ("stable", "librelane")
os.environ["PPA_EDA_TOOLCHAIN"] = profile
os.chdir(root)
sys.path.insert(0, str(root / "pipeline"))
# Use the same archived GF180-aware power evaluator for both profiles.
source = root / "pipeline/designs/gcd/experiments/clock-relax-20261023/measurement_sources/power_activity.py"
spec = importlib.util.spec_from_file_location("power_activity", source)
power = importlib.util.module_from_spec(spec)
sys.modules["power_activity"] = power
spec.loader.exec_module(power)
power.REPO_ROOT = root
import orchestrator
import benchmark_toolchain
from toolchain import toolchain_info

pdk_root = Path("/home/kos2001/claude_work/ppa-eda-agent/pdk")
for module in list(sys.modules.values()):
    if hasattr(module, "PDK_ROOT") and getattr(module, "__file__", "") and str(root) in module.__file__:
        module.PDK_ROOT = pdk_root
tag = f"gf180-upgrade-{label}-13p2-20261023"
design = root / "pipeline/designs/gcd"
assert not (design / "runs" / tag).exists(), "Use a fresh tag"
run_spec = {
    "design_name": "gcd", "candidate_budget": 1,
    "evaluation_budget": {"max_evaluations": 1, "max_wall_seconds": 1800},
    "max_iterations": 1,
    "targets": {"max_core_utilization": 0.75, "max_clock_period_ns": 13.2},
    "search": {"mode": "explicit", "objective": "Paired toolchain comparison at fixed GF180 13.2 ns constraints", "reference": "gcd__2026-10-23__074108"},
    "candidates": [{"tag": tag, "pdk": "gf180mcuD", "scl": "gf180mcu_fd_sc_mcu7t5v0", "overrides": {"SYNTH_STRATEGY": "DELAY 2", "CLOCK_PERIOD": 13.2, "MAX_FANOUT_CONSTRAINT": 12, "FP_CORE_UTIL": 38}}]
}
spec_path = design / f"run_spec_upgrade_{label}_20261023.json"
spec_path.write_text(json.dumps(run_spec, indent=2) + "\n")
orchestrator.validate_run_plan(run_spec, 1)
evidence = design / "experiments/toolchain-upgrade-20261023" / label
evidence.mkdir(parents=True, exist_ok=True)
(evidence / "tools.json").write_text(json.dumps(benchmark_toolchain.inspect_tools(), indent=2) + "\n")
with (evidence / "console.log").open("w", buffering=1) as log:
    sys.stdout = sys.stderr = log
    print(json.dumps(toolchain_info()), flush=True)
    sys.argv = ["orchestrator.py", "--design", str(design), "--run-spec", str(spec_path)]
    orchestrator.main()
