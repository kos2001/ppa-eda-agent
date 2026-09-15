#!/usr/bin/env python3
"""Run OpenRAM SPICE characterization against the installed SRAM netlist.

The shipped macro has descending bus pins; OpenRAM's standalone stimulus
uses ascending pins. Reorder only the top-level declaration, preserving
every named internal connection. Never modify the installed PDK files.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
import openram_adapter

from magic_abstract_drc import lef_size

ROOT = Path(__file__).resolve().parent.parent
MACRO = "sky130_sram_1kbyte_1rw1r_32x256_8"
SIGNOFF_PVT = {"tt": ("TT", 1.8, 25), "ss": ("SS", 1.6, 100),
               "ff": ("FF", 1.95, -40)}


def archive_simulations(run_sim, temp: Path, out: Path):
    """Wrap synchronous OpenRAM calls before their shared logs are overwritten.

    A returned simulator call is not a timing-validation pass: OpenRAM checks
    measurement values later. No timeout or retry is introduced here.
    """
    sequence = 0

    def recorded(self, name):
        nonlocal sequence
        sequence += 1
        archive = out / "simulations" / f"{sequence:06d}"
        archive.mkdir(parents=True, exist_ok=False)
        state = {"stimulus": name, "status": "running",
                 "started_at": datetime.now(timezone.utc).isoformat()}

        def record():
            (archive / "status.json").write_text(json.dumps(state, indent=2) + "\n")

        # Inputs must be captured before execution; outputs belong to this
        # invocation only after it returns or raises.
        for path in (temp / name, temp / "delay_meas.sp"):
            if path.is_file():
                shutil.copy2(path, archive / path.name)
        record()
        print(f"SPICE {sequence}: started {name}; archive={archive}", flush=True)
        try:
            result = run_sim(self, name)
        except BaseException as exc:
            state.update(status="raised", error=f"{type(exc).__name__}: {exc}")
            raise
        else:
            state["status"] = "returned_unverified"
            return result
        finally:
            for filename in ("timing.lis", "spice_stdout.log", "spice_stderr.log"):
                path = temp / filename
                if path.is_file():
                    shutil.copy2(path, archive / filename)
            state["finished_at"] = datetime.now(timezone.utc).isoformat()
            record()
            print(f"SPICE {sequence}: {state['status']}; archive={archive}", flush=True)

    return recorded


def canonical_pins() -> list[str]:
    return ([f"din0[{i}]" for i in range(32)]
            + [f"addr{p}[{i}]" for p in range(2) for i in range(8)]
            + ["csb0", "csb1", "web0", "clk0", "clk1"]
            + [f"wmask0[{i}]" for i in range(4)]
            + [f"dout{p}[{i}]" for p in range(2) for i in range(32)]
            + ["vccd1", "vssd1"])


def prepare_netlist(text: str) -> str:
    pattern = re.compile(r"^\.SUBCKT\s+" + re.escape(MACRO)
                         + r"\s+([^\n]+)(?:\n\+[^\n]*)*", re.M | re.I)
    matches = list(pattern.finditer(text))
    if len(matches) != 1:
        raise ValueError("expected exactly one SRAM top-level subcircuit")
    match = matches[0]
    actual = match.group().replace("\n+", " ").split()[2:]
    expected = canonical_pins()
    if len(actual) != len(expected) or set(actual) != set(expected):
        raise ValueError("installed macro pin interface differs from the 32x256 1rw1r configuration")
    return (text[:match.start()] + ".SUBCKT " + MACRO + " "
            + " ".join(expected) + text[match.end():])


def validate_storage_paths(text: str, cell_format: str, rows: int, cols: int,
                           sen_format: str | None = None) -> dict:
    """Resolve storage probes in the supplied netlist, not generator defaults.

    This checks the self-contained macro's X-instance hierarchy. External
    transistor model includes need not be loaded to resolve bitcell Q nodes.
    """
    lines = []
    for raw in text.splitlines():
        line = raw.split("$", 1)[0].strip().lower()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            if not lines:
                raise ValueError("orphan SPICE continuation")
            lines[-1] += " " + line[1:]
        else:
            lines.append(line)
    circuits = {}
    current = None
    for line in lines:
        tokens = line.split()
        if tokens[0] == ".subckt":
            name = tokens[1]
            if name in circuits:
                raise ValueError(f"duplicate subcircuit: {name}")
            current = {"instances": {}, "nodes": set(tokens[2:])}
            circuits[name] = current
        elif tokens[0] == ".ends":
            current = None
        elif current is not None and tokens[0].startswith("x"):
            # X pins subckt [params: key=value ...]
            positional = []
            for token in tokens:
                if token == "params:" or "=" in token:
                    break
                positional.append(token)
            if tokens[0] in current["instances"]:
                raise ValueError(f"duplicate instance: {tokens[0]}")
            current["instances"][tokens[0]] = positional[-1]
            current["nodes"].update(positional[1:-1])
    for row in range(rows):
        for col in range(cols):
            path = cell_format.format(name=MACRO, hier_sep=".", row=row, col=col).lower()
            parts = path.split(".")
            if parts[0] != "x" + MACRO:
                raise ValueError(f"unexpected probe root: {path}")
            circuit = MACRO
            for instance in parts[1:]:
                definition = circuits.get(circuit)
                if definition is None or instance not in definition["instances"]:
                    raise ValueError(f"unresolved storage probe {path}: {instance} absent from {circuit}")
                circuit = definition["instances"][instance]
            nodes = circuits.get(circuit, {}).get("nodes", set())
            if not {"q", "q_bar"} <= nodes:
                raise ValueError(f"storage nodes Q/Q_bar missing for {path} in {circuit}")
    result = {"checked_bitcells": rows * cols, "cell_format": cell_format,
              "storage_nodes": ["Q", "Q_bar"]}
    if sen_format is not None:
        sense_nodes = []
        for port in range(2):
            path = sen_format.format(name=MACRO, hier_sep=".").lower() + str(port)
            parts = path.split(".")
            # In this installed 1rw1r macro both bank sense-enable inputs
            # are connected to top-level nets; bank formal aliases vanish.
            if (len(parts) != 2 or parts[0] != "x" + MACRO
                    or parts[1] not in circuits[MACRO]["nodes"]):
                raise ValueError(f"unresolved top-level sense-enable probe: {path}")
            sense_nodes.append(path)
        result["sense_enable_nodes"] = sense_nodes
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--openram-root", type=Path, required=True)
    ap.add_argument("--pdk-root", type=Path, default=ROOT / "pdk")
    ap.add_argument("--corner", choices=SIGNOFF_PVT, default="tt",
                    help="one signoff PVT per run (default: tt)")
    ap.add_argument("--output-dir", type=Path,
                    help="new result directory; refuses to overwrite an existing run")
    args = ap.parse_args()
    if args.corner != "tt" and args.output_dir is None:
        ap.error("--corner ss/ff requires a new --output-dir")
    if args.output_dir:
        args.output_dir = args.output_dir.resolve()
        args.output_dir.mkdir(parents=True, exist_ok=False)
    source = args.openram_root.resolve()
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if revision != "b2b069ce119d1488cbe6883b2240bceb5c7ce29a":
        raise ValueError("OpenRAM revision differs from the validated stimulus adapter")
    os.environ["OPENRAM_HOME"] = str(source / "compiler")
    os.environ["PDK_ROOT"] = str(args.pdk_root.resolve())
    spec = importlib.util.spec_from_file_location("openram", source / "__init__.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["openram"] = module
    spec.loader.exec_module(module)
    from openram import OPTS, init_openram, end_openram
    OPTS.top_process = "memchar"
    cfg = ROOT / "pipeline/designs/sram_wrapper/characterization/config.py"
    init_openram(str(cfg), is_unit_test=False)
    process, voltage, temperature = SIGNOFF_PVT[args.corner]
    # Use explicit tuples: OpenRAM's nominal_corner_only ignores PVT lists,
    # and its default expansion introduces unwanted mixed PVT combinations.
    OPTS.use_specified_corners = [(process, voltage, temperature)]
    OPTS.process_corners = [process]
    OPTS.supply_voltages = [voltage]
    OPTS.temperatures = [temperature]
    if args.output_dir:
        OPTS.output_path = str(args.output_dir) + "/"
    from openram.characterizer import fake_sram, lib
    openram_adapter.install()
    sram = fake_sram(name=OPTS.output_name, word_size=OPTS.word_size,
                     num_words=OPTS.num_words, write_size=OPTS.write_size,
                     num_banks=OPTS.num_banks, words_per_row=OPTS.words_per_row)
    sram.generate_pins()
    sram.setup_multiport_constants()
    macros = args.pdk_root / "sky130A/libs.ref/sky130_sram_macros"
    netlist = macros / "spice" / (MACRO + ".spice")
    lef = macros / "lef" / (MACRO + ".lef")
    size = lef_size(lef.read_text(), MACRO)
    if size is None:
        raise ValueError("macro LEF has no matching SIZE")
    sram.width, sram.height = size
    out = Path(OPTS.output_path)
    prepared = out / (MACRO + ".ascending.spice")
    prepared.write_text(prepare_netlist(netlist.read_text()))
    storage_paths = validate_storage_paths(
        prepared.read_text(), OPTS.cell_format,
        OPTS.num_words // OPTS.words_per_row, OPTS.word_size * OPTS.words_per_row,
        OPTS.sen_format)
    manifest = {
        "status": "running", "openram_revision": revision,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(), "simulator_temp_dir": str(OPTS.openram_temp),
        "source_spice": str(netlist.resolve()),
        "source_spice_sha256": hashlib.sha256(netlist.read_bytes()).hexdigest(),
        "prepared_spice_sha256": hashlib.sha256(prepared.read_bytes()).hexdigest(),
        "storage_path_validation": storage_paths,
        "config_sha256": hashlib.sha256(cfg.read_bytes()).hexdigest(),
        "analytical_delay": False, "slew_scales": OPTS.slew_scales,
        "stimulus_slew_definition": "10-90%; full linear ramp = slew / 0.8",
        "setup_hold_clock_axis": "related_input_slew",
        "adapter_sha256": hashlib.sha256(Path(openram_adapter.__file__).read_bytes()).hexdigest(),
        "physical_verification": "separate OpenLane run required",
        "pvt": {"process": process, "voltage_V": voltage, "temperature_C": temperature},
        "corner_scope": f"{process} {voltage}V {temperature}C only; does not qualify other PVT corners",
    }
    record = out / "manifest.json"
    record.write_text(json.dumps(manifest, indent=2) + "\n")
    from openram.characterizer.stimuli import stimuli
    stimuli.run_sim = archive_simulations(stimuli.run_sim, Path(OPTS.openram_temp), out)
    try:
        lib(out_dir=str(out) + "/", sram=sram, sp_file=str(prepared), use_model=False)
    except BaseException as exc:
        manifest["status"] = "failed"
        manifest["error"] = f"{type(exc).__name__}: {exc}"
        raise
    else:
        manifest["status"] = "generated_unverified"
        end_openram()
    finally:
        manifest["finished_at"] = datetime.now(timezone.utc).isoformat()
        record.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
