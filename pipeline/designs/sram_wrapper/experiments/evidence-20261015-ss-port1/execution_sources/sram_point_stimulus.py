#!/usr/bin/env python3
"""Prepare exactly one SRAM read-port stimulus using the pinned OpenRAM API.

This command never invokes a simulator or generates Liberty. The write port
remains port 0 when read port 1 is selected.
"""
import argparse
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from characterize_sram import MACRO, ROOT, SIGNOFF_PVT, prepare_netlist, validate_storage_paths
import openram_adapter

REVISION = "b2b069ce119d1488cbe6883b2240bceb5c7ce29a"


def assess_measurements(values, requested_names, port, voltage, period_ns):
    """Assess raw SI .meas values; never qualify a model from one point.

    Storage requires each Q/Qbar value on its intended side of VDD/2.
    Output checks explicitly require 10/90% rails, stricter than upstream's
    opposite-value-only check. Timing values arrive in seconds.
    """
    issues = []
    if not math.isfinite(voltage) or voltage <= 0 or not math.isfinite(period_ns) or period_ns <= 0:
        raise ValueError("voltage and period must be finite and positive")
    if len(requested_names) != 27 or len(set(requested_names)) != 27:
        issues.append("expected 27 distinct requested measurements")
    for name in requested_names:
        value = values.get(name)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            issues.append("missing/nonfinite measurement: " + name)
    if issues:
        return {"point_checks_passed": False, "model_qualified": False, "issues": issues}
    storage_count = 0
    for name in requested_names:
        match = re.fullmatch(r"v_(q|qbar)_a[01]+_b\d+_(read|write)_(zero|one)([01])", name)
        if not match:
            continue
        storage_count += 1
        expected_port = port if match[2] == "read" else 0
        if int(match[4]) != expected_port:
            issues.append("unexpected storage measurement port: " + name)
        high = (match[3] == "one") != (match[1] == "qbar")
        if not (values[name] > voltage / 2 if high else values[name] < voltage / 2):
            issues.append("wrong storage polarity: " + name)
    if storage_count != 8:
        issues.append("expected eight Q/Qbar read/write checks")
    for edge, high in (("lh", True), ("hl", False)):
        name = f"v_delay_{edge}{port}"
        value = values.get(name)
        if value is None or not (value >= .9 * voltage if high else value <= .1 * voltage):
            issues.append("output did not reach intended 10/90% rail: " + name)
    sen = values.get(f"delay_sen{port}")
    if sen is None or not 0 <= sen <= period_ns * 1e-9 / 2:
        issues.append("sense-enable delay outside half-period")
    for name in (f"{kind}_{edge}{port}" for kind in ("delay", "slew") for edge in ("lh", "hl")):
        bound = period_ns * 1e-9 / (1 if name == f"slew_lh{port}" else 2)
        if name not in values or not 0 < values[name] <= bound:
            issues.append("delay/slew outside positive timing bound: " + name)
    return {"point_checks_passed": not issues, "model_qualified": False, "issues": issues,
            "scope": "one selected address/bit/port; finite values, storage polarity, output rails, sense-enable and timing bounds"}


def validate_decks(stimulus, measurements, port, bit=31):
    """Check generated read/write identities before admitting a simulation."""
    names = re.findall(r"(?im)^\.meas\s+tran\s+(\S+)", measurements)
    if len(names) != 27 or len(set(names)) != 27:
        raise ValueError("expected exactly 27 distinct point measurements")
    for name in ("delay_lh", "delay_hl", "slew_lh", "slew_hl", "delay_sen"):
        if name + str(port) not in names:
            raise ValueError("missing selected read-port measurement: " + name)
    if "write1_power0" not in names or "write0_power0" not in names:
        raise ValueError("point must retain writer port 0")
    for name in ("delay_lh", "delay_hl"):
        line = next(line for line in measurements.splitlines()
                    if re.match(r"\.meas\s+tran\s+" + name + str(port) + r"\s", line, re.I))
        if f"v(clk{port})" not in line.lower() or f"v(dout{port}_{bit})" not in line.lower():
            raise ValueError("read-port clock/output probe mismatch")
    if f".{ 's_en' + str(port)})" not in measurements.lower():
        raise ValueError("missing selected sense-enable node")
    csb = re.search(r"(?im)^VCSB" + str(port) + r"\s+.*$", stimulus)
    if not csb:
        raise ValueError("missing selected read-port source")
    if not re.search(r"\b0(?:\.0+)?v\b", csb[0], re.I):
        raise ValueError("selected read-port chip select is never active low")
    for operation, selected in (("R", port), ("W", 0)):
        for value in (0, 1):
            if not re.search(r"Port\s+" + str(selected) + r"\s+[^\n]*:\s+" + operation
                             + r" data " + str(value) + r" address", measurements):
                raise ValueError("missing intended read/write stimulus cycle")
    return names


def prepare(args):
    for name in ("period_ns", "slew_ns", "load_ff"):
        if not math.isfinite(getattr(args, name)) or getattr(args, name) <= 0:
            raise ValueError(name + " must be finite and positive")
    if not re.fullmatch(r"[01]{8}", args.address) or not 0 <= args.bit < 32:
        raise ValueError("expected an 8-bit binary address and bit 0..31")
    source = args.openram_root.resolve()
    revision = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"],
                                       text=True, timeout=15).strip()
    if revision != REVISION:
        raise ValueError("OpenRAM revision differs from pinned API")
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=False)
    os.environ["OPENRAM_HOME"] = str(source / "compiler")
    os.environ["PDK_ROOT"] = str(args.pdk_root.resolve())
    spec = importlib.util.spec_from_file_location("openram", source / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["openram"] = module
    spec.loader.exec_module(module)
    from openram import OPTS, init_openram
    OPTS.top_process = "memchar"
    cfg = ROOT / "pipeline/designs/sram_wrapper/characterization/config.py"
    init_openram(str(cfg), is_unit_test=False)
    OPTS.output_path = str(out) + "/"
    OPTS.openram_temp = str(out / "work") + "/"
    Path(OPTS.openram_temp).mkdir()
    OPTS.trim_netlist = False
    openram_adapter.install()
    from openram.characterizer.stimuli import stimuli
    def refuse_simulation(*_args, **_kwargs):
        raise RuntimeError("prepare-only command must never invoke SPICE")
    stimuli.run_sim = refuse_simulation
    from openram.characterizer import fake_sram, delay
    sram = fake_sram(name=MACRO, word_size=OPTS.word_size, num_words=OPTS.num_words,
                     write_size=OPTS.write_size, num_banks=OPTS.num_banks,
                     words_per_row=OPTS.words_per_row)
    sram.generate_pins()
    sram.setup_multiport_constants()
    original = args.pdk_root / "sky130A/libs.ref/sky130_sram_macros/spice" / (MACRO + ".spice")
    prepared = out / (MACRO + ".ascending.spice")
    prepared.write_text(prepare_netlist(original.read_text()))
    storage = validate_storage_paths(prepared.read_text(), OPTS.cell_format, 128, 64, OPTS.sen_format)
    corner = SIGNOFF_PVT[args.corner]
    # Pinned prepare_netlist concatenates filenames to output_path directly.
    point = delay(sram, str(prepared), corner, output_path=str(out) + "/")
    point.analysis_init(args.address, args.bit)
    point.set_load_slew(args.load_ff, args.slew_ns)
    point.period = args.period_ns
    point.targ_read_ports = [args.port]
    point.targ_write_ports = [0]
    point.write_delay_stimulus()
    names = validate_decks((out / "delay_stim.sp").read_text(),
                           (out / "delay_meas.sp").read_text(), args.port, args.bit)
    if (out / "sram.sp").read_bytes() != prepared.read_bytes():
        raise ValueError("simulation netlist differs from validated prepared macro")
    paths = [original, prepared, cfg, Path(__file__), Path(openram_adapter.__file__),
             source / "compiler/characterizer/delay.py", out / "delay_stim.sp", out / "delay_meas.sp"]
    manifest = {
        "status": "prepared_inputs_only", "measurements_generated": False,
        "model_qualified": False, "openram_revision": revision,
        "pvt": dict(zip(("process", "voltage_V", "temperature_C"), corner)),
        "read_port": args.port, "write_port": 0, "address": args.address, "bit": args.bit,
        "period_ns": args.period_ns, "input_slew_10_90_ns": args.slew_ns,
        "load_fF": args.load_ff, "storage_path_validation": storage,
        "requested_measurements": names,
        "deck_validation": {"selected_read_csb_active_low": True,
                            "read_clock_output_sense_port": args.port,
                            "read_zero_one_cycles_port": args.port,
                            "write_zero_one_cycles_port": 0,
                            "simulation_netlist_matches_prepared": True},
        "sources_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
        "scope": "one prepared full-netlist point; no SPICE execution or Liberty generation",
    }
    (out / "point-manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--openram-root", type=Path, required=True)
    parser.add_argument("--pdk-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--port", type=int, choices=(0, 1), required=True)
    parser.add_argument("--corner", choices=SIGNOFF_PVT, default="ss")
    parser.add_argument("--period-ns", type=float, default=10)
    parser.add_argument("--slew-ns", type=float, default=0.56)
    parser.add_argument("--load-ff", type=float, default=27.56)
    parser.add_argument("--address", default="11111111")
    parser.add_argument("--bit", type=int, default=31)
    print(json.dumps(prepare(parser.parse_args()), indent=2))


if __name__ == "__main__":
    main()
