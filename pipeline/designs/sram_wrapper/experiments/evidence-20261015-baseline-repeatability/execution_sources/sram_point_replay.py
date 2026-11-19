"""Bound one retained SRAM transient deck; never generate or qualify Liberty.

Run on Linux with a persistent local simulator/model snapshot. Only include
paths are relocated; stimulus, device equations and tolerances are preserved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time
from datetime import datetime, timezone

from sram_point_stimulus import assess_measurements, validate_decks


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_point_sources(source, identity):
    """Require the prepared identity to bind the exact electrical input bytes."""
    for name in ("delay_stim.sp", "delay_meas.sp"):
        record = identity.get("stimulus_sources", {}).get(name, {})
        expected = record.get("retained_bytes_sha256")
        if expected is None:
            matches = [value for path, value in identity.get("sources_sha256", {}).items()
                       if Path(path).name == name]
            if len(matches) != 1:
                raise ValueError(f"point manifest must identify exactly one {name}")
            expected = matches[0]
        if sha(Path(source) / name) != expected:
            raise ValueError(f"point source hash mismatch: {name}")


def relocate(text, model_lib, netlist, measurements):
    replacements = []
    def replace(match):
        kind, raw, rest = match.group(1), match.group(2).strip('"\''), match.group(3)
        name = Path(raw).name
        targets = {"sky130.lib.spice": model_lib, "sram.sp": netlist,
                   "delay_meas.sp": measurements}
        if name not in targets:
            raise ValueError(f"unexpected external include: {raw}")
        target = Path(targets[name]).resolve()
        if not target.is_file():
            raise FileNotFoundError(target)
        replacements.append({"source": raw, "target": str(target), "sha256": sha(target)})
        return f'{kind} "{target}"{rest}'
    result = re.sub(r'(?im)^(\.(?:lib|include))\s+("[^"\n]+"|\S+)([^\n]*)$', replace, text)
    if len(replacements) != 3 or {Path(r["source"]).name for r in replacements} != {
            "sky130.lib.spice", "sram.sp", "delay_meas.sp"}:
        raise ValueError("expected exactly model, SRAM and measurement includes")
    return result, replacements


def measurements(deck, output):
    requested = re.findall(r"(?im)^\.meas(?:ure)?\s+tran\s+(\S+)", deck)
    if not requested or len(requested) != len(set(requested)):
        raise ValueError("missing or duplicate measurement names")
    values, missing = {}, []
    for name in requested:
        matches = re.findall(r"(?im)^\s*" + re.escape(name) + r"\s*=\s*(\S+)", output)
        try:
            if len(matches) != 1:
                raise ValueError("missing or ambiguous measurement")
            value = float(matches[0])
            if not math.isfinite(value):
                raise ValueError("nonfinite measurement")
            values[name] = value
        except ValueError:
            missing.append(name)
    return {"requested_count": len(requested), "finite_count": len(values),
            "missing_or_invalid": missing, "values_SI": values,
            "all_requested_finite": not missing}


def run_bounded(command, directory, timeout_s, env=None):
    """Isolated Linux process group, on-disk logs, and bounded termination."""
    if os.name != "posix":
        raise RuntimeError("use Linux/WSL for owned process-group termination")
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise ValueError("timeout must be finite and positive")
    directory = Path(directory)
    started = time.monotonic()
    samples, timed_out = [], False
    with ((directory / "stdout.log").open("wb") as stdout,
          (directory / "stderr.log").open("wb") as stderr,
          (directory / "process_samples.jsonl").open("a", encoding="utf-8") as live_samples):
        proc = subprocess.Popen(command, cwd=directory, stdout=stdout, stderr=stderr,
                                stdin=subprocess.DEVNULL, start_new_session=True, env=env)
        try:
            while proc.poll() is None:
                remaining = timeout_s - (time.monotonic() - started)
                if remaining <= 0:
                    timed_out = True
                    break
                sample = {"elapsed_s": time.monotonic() - started, "pid": proc.pid}
                for name in ("status", "stat", "wchan", "io"):
                    try:
                        sample[name] = (Path("/proc") / str(proc.pid) / name).read_text()
                    except OSError:
                        pass
                samples.append(sample)
                live_samples.write(json.dumps(sample) + "\n")
                live_samples.flush()
                try:
                    proc.wait(timeout=min(10, remaining))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    os.killpg(proc.pid, signal.SIGKILL)
                    proc.wait(timeout=5)
    (directory / "process_samples.json").write_text(json.dumps(samples, indent=2) + "\n")
    return {"returncode": proc.returncode, "timed_out": timed_out,
            "wall_seconds": time.monotonic() - started, "pid": proc.pid}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--source-dir", required=True, type=Path)
    ap.add_argument("--point-manifest", required=True, type=Path, help="prepared point identity and PVT")
    ap.add_argument("--simulator", required=True, type=Path)
    ap.add_argument("--simulator-sha256", required=True)
    ap.add_argument("--model-root", required=True, type=Path)
    ap.add_argument("--netlist", required=True, type=Path, help="validated ascending-pin SRAM netlist")
    ap.add_argument("--netlist-sha256", required=True)
    ap.add_argument("--startup-dir", required=True, type=Path, help="pinned ngspice directory containing spinit")
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--timeout", type=float, default=300)
    args = ap.parse_args()
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        ap.error("--timeout must be finite and positive")
    for path, expected in ((args.simulator, args.simulator_sha256), (args.netlist, args.netlist_sha256)):
        if sha(path) != expected:
            raise ValueError(f"input hash mismatch: {path}")
    model_lib = args.model_root.resolve() / "sky130A/libs.tech/ngspice/sky130.lib.spice"
    startup = args.startup_dir.resolve() / "spinit"
    if not startup.is_file():
        raise ValueError("missing pinned simulator startup file")
    env = dict(os.environ, SPICE_SCRIPTS=str(args.startup_dir.resolve()))
    version = subprocess.run([str(args.simulator.resolve()), "-v"], env=env,
                             capture_output=True, text=True, timeout=30, check=True)
    version_text = version.stdout + version.stderr
    if "Compiled with KLU" not in version_text:
        raise ValueError("retained KLU deck requires a KLU-enabled simulator")
    model_hashes = {str(p.relative_to(args.model_root)): sha(p)
                    for branch in ("sky130A/libs.tech/ngspice", "sky130A/libs.ref/sky130_fd_pr/spice")
                    for p in sorted((args.model_root / branch).rglob("*")) if p.is_file()}
    if not model_lib.is_file() or not model_hashes:
        raise ValueError("missing model snapshot")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    source = args.source_dir.resolve()
    identity = json.loads(args.point_manifest.read_text())
    verify_point_sources(source, identity)
    point = identity.get("point", identity)
    port = point.get("port", point.get("read_port"))
    voltage = identity["pvt"]["voltage_V"]
    period = point["period_ns"]
    for name in ("delay_stim.sp", "delay_meas.sp"):
        shutil.copyfile(source / name, out / ("source-" + name))
    shutil.copyfile(args.netlist, out / "sram.sp")
    shutil.copyfile(source / "delay_meas.sp", out / "delay_meas.sp")
    text, relocations = relocate((source / "delay_stim.sp").read_text(), model_lib,
                                out / "sram.sp", out / "delay_meas.sp")
    (out / "delay_stim.sp").write_text(text)
    deck = (out / "delay_meas.sp").read_text()
    requested = validate_decks(text, deck, port, point.get("bit", 31))
    # Check supplied identity against the actual stimulus, not a filename.
    supply = re.search(r"(?im)^Vvdd\s+vdd\s+0\s+(\S+)", text)
    temperature = re.search(r"(?im)^\.TEMP\s+(\S+)", text)
    stimulus_period = re.search(r"Delay stimulus for period of ([\d.]+)n", text)
    process = re.search(r'(?im)^\.lib\s+"[^"\n]+"\s+(\w+)', text)
    load_slew = re.search(r"load=([\d.]+)fF slew=([\d.]+)ns", text)
    if (not supply or float(supply[1]) != voltage or not temperature
            or float(temperature[1]) != identity["pvt"]["temperature_C"]
            or not stimulus_period or float(stimulus_period[1]) != period
            or not process or process[1].upper() != identity["pvt"]["process"].upper()
            or not load_slew or float(load_slew[1]) != point["load_fF"]
            or float(load_slew[2]) != point["input_slew_10_90_ns"]):
        raise ValueError("point identity differs from stimulus PVT/period/load/slew")
    command = [str(args.simulator.resolve()), "-n", "-b", "-o", "timing.lis", "delay_stim.sp"]
    record = {"status": "running", "started_at": datetime.now(timezone.utc).isoformat(),
              "command": command, "timeout_s": args.timeout, "attempt_budget": 1,
              "simulator_sha256": sha(args.simulator), "netlist_sha256": sha(args.netlist),
              "simulator_version": version_text.strip(),
              "startup_path": str(startup), "startup_sha256": sha(startup),
              "point_identity": identity, "point_manifest_sha256": sha(args.point_manifest),
              "model_sources_sha256": model_hashes, "include_relocations": relocations,
              "original_deck_sha256": sha(source / "delay_stim.sp"),
              "measurement_deck_sha256": sha(source / "delay_meas.sp"),
              "executed_deck_sha256": sha(out / "delay_stim.sp"),
              "runner_sha256": sha(__file__), "model_qualified": False,
              "scope": "one retained transient point; finite measures alone do not establish functional/PVT/table qualification"}
    manifest = out / "manifest.json"
    manifest.write_text(json.dumps(record, indent=2) + "\n")
    try:
        result = run_bounded(command, out, args.timeout, env=env)
        output = (out / "timing.lis").read_text(errors="replace") if (out / "timing.lis").exists() else ""
        measured = measurements(deck, output)
        assessment = assess_measurements(measured["values_SI"], requested, port, voltage, period)
        record.update(result, measurements=measured,
                      point_checks=assessment,
                      status="timeout_unverified" if result["timed_out"] else
                      "measured_unverified" if result["returncode"] == 0 and assessment["point_checks_passed"]
                      else "measured_point_checks_failed" if result["returncode"] == 0 and measured["all_requested_finite"]
                      else "incomplete_unverified")
    except BaseException as exc:
        record.update(status="execution_error", error=f"{type(exc).__name__}: {exc}")
        raise
    finally:
        record["finished_at"] = datetime.now(timezone.utc).isoformat()
        record["output_sha256"] = {p.name: sha(p) for p in out.iterdir() if p.is_file() and p != manifest}
        manifest.write_text(json.dumps(record, indent=2) + "\n")
    print(json.dumps({k: record[k] for k in ("status", "wall_seconds", "timed_out", "measurements")}, indent=2))
    raise SystemExit(0 if record["status"] == "measured_unverified" else 1)


if __name__ == "__main__":
    main()
