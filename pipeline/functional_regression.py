"""Bounded RTL and optional post-route functional regression.

Local simulation evidence, not hidden-VIP grading or timing simulation.
"""
import argparse
import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path

from toolchain import OPENLANE_IMAGE, VERILATOR_IMAGE, platform_args
import functional_oracles

PDK_ROOT = Path(__file__).resolve().parents[1] / "pdk"


def validate(spec):
    if not isinstance(spec, dict):
        raise ValueError("functional_regression must be an object")
    if "oracle" in spec and spec["oracle"] not in functional_oracles.ORACLES:
        raise ValueError("unknown functional oracle")
    if "post_route" in spec and type(spec["post_route"]) is not bool:
        raise ValueError("post_route must be a boolean")
    models = spec.get("macro_models", {})
    if not isinstance(models, dict) or any(not isinstance(k, str) or not isinstance(v, str)
                                          or not v.startswith("/pdk/") for k, v in models.items()):
        raise ValueError("macro_models must map module names to /pdk/ behavioral model paths")
    if type(spec.get("rtl_cell_models", False)) is not bool:
        raise ValueError("rtl_cell_models must be a boolean")
    if not isinstance(spec.get("top"), str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", spec["top"]):
        raise ValueError("functional_regression.top must name the testbench module")
    if not isinstance(spec.get("testbench"), str) or not spec["testbench"]:
        raise ValueError("functional_regression.testbench is required")
    seeds = spec.get("seeds", [1, 17, 101])
    if not isinstance(seeds, list) or not seeds or len(seeds) > 100 or any(
            type(seed) is not int or not 1 <= seed <= 2147483647 for seed in seeds):
        raise ValueError("seeds must contain 1..100 positive 31-bit integers")
    if len(set(seeds)) != len(seeds):
        raise ValueError("seeds must be distinct")
    for key, default in (("compile_timeout_s", 300), ("simulation_timeout_s", 30)):
        value = spec.get(key, default)
        if type(value) is not int or not 1 <= value <= 3600:
            raise ValueError(f"{key} must be an integer in 1..3600")


def checked_path(root, relative):
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"missing file or path outside design: {relative}")
    return path


def rtl_sources(design, config=None):
    if config is None:
        config = json.loads((design / "config.json").read_text(encoding="utf-8"))
    for key in ("VERILOG_DEFINES", "SYNTH_DEFINES", "VERILOG_INCLUDE_DIRS"):
        if config.get(key):
            raise ValueError(f"regression does not yet support {key}; refusing a different RTL configuration")
    entries = config.get("VERILOG_FILES", [])
    entries = [entries] if isinstance(entries, str) else entries
    files = []
    for entry in entries:
        if not isinstance(entry, str) or not entry.startswith("dir::"):
            raise ValueError("regression currently requires dir:: VERILOG_FILES")
        matches = sorted(design.glob(entry[5:]))
        if not matches:
            raise ValueError(f"no RTL matched {entry}")
        files.extend(checked_path(design, p.relative_to(design)) for p in matches)
    if not files:
        raise ValueError("no configured RTL files")
    return list(dict.fromkeys(files))


def execute(work, args, timeout, log_name, image=VERILATOR_IMAGE):
    name = "ppa-regression-" + uuid.uuid4().hex
    log = work / log_name
    cmd = ["docker", "run", "--rm", "--network", "none", "--name", name,
           *platform_args(), "-v", f"{work}:/work", "-w", "/work",
           "--entrypoint", args[0], image, *args[1:]]
    try:
        with log.open("w", encoding="utf-8") as stream:
            result = subprocess.run(cmd, stdout=stream, stderr=subprocess.STDOUT, timeout=timeout)
        return {"returncode": result.returncode, "timed_out": False, "log": str(log)}
    except subprocess.TimeoutExpired:
        # Killing the Docker client alone can leave the simulation running.
        subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
        return {"returncode": None, "timed_out": True, "log": str(log)}


def classify(result, output):
    if result["timed_out"]:
        return "unverified", 0
    if result["returncode"] != 0:
        return "failed", 0
    markers = re.findall(r"^PPA_REGRESSION_PASS checks=([0-9]+)\s*$", output, re.M)
    if len(markers) != 1 or int(markers[0]) <= 0:
        return "unverified", 0
    return "passed", int(markers[0])


def macro_sources(config, spec):
    declared = set((config.get("MACROS") or {}).keys())
    selected = spec.get("macro_models", {})
    if declared != set(selected):
        raise ValueError("every macro must have exactly one explicit behavioral model")
    files = []
    for name, path in selected.items():
        source = checked_path(PDK_ROOT, path[5:])
        text = source.read_text(encoding="utf-8")
        if "blackbox" in text.lower() or not re.search(r"\bmodule\s+" + re.escape(name) + r"\s*\(", text):
            raise ValueError(f"not a behavioral model for {name}")
        files.append(source)
    return files


def post_route_sources(run_dir, spec=None):
    """Use the final netlist and the exact model paths recorded by OpenLane."""
    resolved = json.loads((run_dir / "resolved.json").read_text(encoding="utf-8"))
    top = resolved["DESIGN_NAME"]
    if not isinstance(top, str) or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", top):
        raise ValueError("invalid resolved design name")
    files = [checked_path(run_dir, f"final/nl/{top}.nl.v")]
    models = resolved.get("CELL_VERILOG_MODELS")
    if not isinstance(models, list) or not models:
        raise ValueError("no recorded standard-cell simulation models")
    macro_files = macro_sources(resolved, spec or {})
    # Explicit behavioral models replace synthesis-only blackbox declarations.
    if resolved.get("EXTRA_VERILOG_MODELS") and not macro_files:
        raise ValueError("extra simulation models require an explicit macro mapping")
    for model in models:
        if not isinstance(model, str) or not model.startswith("/pdk/"):
            raise ValueError("model path must be under the recorded /pdk mount")
        files.append(checked_path(PDK_ROOT, model[5:]))
    return files + macro_files


def combined_status(statuses):
    """Failures dominate; absent or incomplete evidence cannot establish a pass."""
    states = set(statuses)
    if "failed" in states:
        return "failed"
    return "passed" if states == {"passed"} else "unverified"


def snapshot_sources(files, design, work, post_route):
    """Copy and hash each input in one bounded-memory pass."""
    sources, records = [], []
    for index, source in enumerate(files):
        relative = (Path(f"input{index}.v")
                    if post_route or not source.is_relative_to(design)
                    else source.relative_to(design))
        dest = work / "source" / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        with source.open("rb") as original, dest.open("wb") as snapshot:
            for chunk in iter(lambda: original.read(1024 * 1024), b""):
                snapshot.write(chunk)
                digest.update(chunk)
        records.append({"path": str(source), "snapshot": str(relative),
                        "sha256": digest.hexdigest()})
        sources.append("/work/source/" + relative.as_posix())
    return sources, records


def _check_stage(design_dir, run_dir, spec, stage):
    validate(spec)
    design = Path(design_dir).resolve()
    work = Path(run_dir).resolve() / "_functional_regression" / uuid.uuid4().hex
    work.mkdir(parents=True)
    post_route = stage == "post_route"
    image = OPENLANE_IMAGE if post_route else VERILATOR_IMAGE
    record = {"status": "unverified", "stage": stage,
              "scope": "Post-route netlist functional simulation (no SDF)" if post_route else "RTL simulation against local testbench",
              "image": image, "sources": [], "runs": [], "artifact_dir": str(work)}
    try:
        config = json.loads((design / "config.json").read_text(encoding="utf-8"))
        if post_route:
            files = post_route_sources(Path(run_dir).resolve(), spec)
        else:
            files = rtl_sources(design, config) + macro_sources(config, spec)
            if spec.get("rtl_cell_models"):
                # RTL with explicit standard-cell instances needs real cell models too.
                pdk = config.get("PDK", "sky130A")
                scl = config.get("STD_CELL_LIBRARY", "sky130_fd_sc_hd")
                files += [checked_path(PDK_ROOT, f"{pdk}/libs.ref/{scl}/verilog/{name}")
                          for name in ("primitives.v", f"{scl}.v")]
        files = files + [checked_path(design, spec["testbench"])]
        if spec.get("oracle"):
            top = config["DESIGN_NAME"]
            if spec["oracle"] != top:
                raise ValueError("oracle does not match design")
            record["oracle"] = {"name": spec["oracle"], "sha256": hashlib.sha256(
                Path(functional_oracles.__file__).read_bytes()).hexdigest()}
        sources, record["sources"] = snapshot_sources(files, design, work, post_route)
        version = execute(work, ["iverilog", "-V"] if post_route else ["verilator", "--version"], 30, "version.log", image=image)
        if version["returncode"] != 0 or version["timed_out"]:
            raise RuntimeError("cannot read simulator version")
        record["version"] = Path(version["log"]).read_text(encoding="utf-8").splitlines()[0]
        command = (["iverilog", "-g2012", "-DFUNCTIONAL", "-DUNIT_DELAY=#0", "-s", spec["top"],
                    "-o", "/work/regression.vvp", *sources] if post_route else
                   ["verilator", "--binary", "--timing", "--assert", "-DFUNCTIONAL", "-DUNIT_DELAY=#0",
            "-Wno-fatal", "--top-module", spec["top"], "--Mdir", "/work/obj",
            "-o", "regression", *sources])
        compile_result = execute(work, command, spec.get("compile_timeout_s", 300), "compile.log", image=image)
        record["compile"] = compile_result
        if compile_result["timed_out"] or compile_result["returncode"] != 0:
            record["reason"] = "compile did not complete successfully"
        else:
            for seed in spec.get("seeds", [1, 17, 101]):
                command = (["vvp", "/work/regression.vvp", f"+seed={seed}"] if post_route else
                           ["/work/obj/regression", f"+seed={seed}", f"+verilator+seed+{seed}"])
                result = execute(work, command, spec.get("simulation_timeout_s", 30), f"seed-{seed}.log", image=image)
                output = Path(result["log"]).read_text(encoding="utf-8", errors="replace")
                status, checks = classify(result, output)
                oracle = None
                if spec.get("oracle") and status == "passed":
                    oracle = functional_oracles.ORACLES[spec["oracle"]](output, checks, seed)
                    status = oracle["status"]
                record["runs"].append({**result, "seed": seed, "status": status, "checks": checks, "oracle": oracle})
            states = [r["status"] for r in record["runs"]]
            record["status"] = combined_status(states)
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as exc:
        record["reason"] = f"{type(exc).__name__}: {exc}"
    (work / "result.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    return record


def check(design_dir, run_dir, spec):
    result = _check_stage(design_dir, run_dir, spec, "rtl")
    if spec.get("post_route"):
        post = _check_stage(design_dir, run_dir, spec, "post_route")
        result["rtl_status"] = result["status"]
        result["post_route"] = post
        statuses = [result["status"], post["status"]]
        result["status"] = combined_status(statuses)
        result["scope"] = "RTL and post-route netlist functional simulation; no SDF"
        (Path(result["artifact_dir"]) / "result.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def apply(verdict, result):
    verdict["functional_regression"] = result
    if result["status"] == "failed":
        verdict["violations"].append("Functional regression failed")
    elif result["status"] != "passed":
        verdict.setdefault("unverified", []).append("Functional regression incomplete")
    verdict["passed"] = not verdict["violations"] and not verdict.get("unverified")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design", type=Path, required=True)
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--run-spec", type=Path, required=True)
    args = parser.parse_args()
    spec = json.loads(args.run_spec.read_text(encoding="utf-8"))["functional_regression"]
    result = check(args.design, args.run_dir, spec)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result["status"] == "passed" else 1)


if __name__ == "__main__":
    main()
