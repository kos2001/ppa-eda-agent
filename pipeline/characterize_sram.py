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
import subprocess
import sys
import openram_adapter

from magic_abstract_drc import lef_size

ROOT = Path(__file__).resolve().parent.parent
MACRO = "sky130_sram_1kbyte_1rw1r_32x256_8"


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


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--openram-root", type=Path, required=True)
    ap.add_argument("--pdk-root", type=Path, default=ROOT / "pdk")
    args = ap.parse_args()
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
    manifest = {
        "status": "running", "openram_revision": revision,
        "source_spice": str(netlist.resolve()),
        "source_spice_sha256": hashlib.sha256(netlist.read_bytes()).hexdigest(),
        "prepared_spice_sha256": hashlib.sha256(prepared.read_bytes()).hexdigest(),
        "config_sha256": hashlib.sha256(cfg.read_bytes()).hexdigest(),
        "analytical_delay": False, "slew_scales": OPTS.slew_scales,
        "stimulus_slew_definition": "10-90%; full linear ramp = slew / 0.8",
        "setup_hold_clock_axis": "related_input_slew",
        "adapter_sha256": hashlib.sha256(Path(openram_adapter.__file__).read_bytes()).hexdigest(),
        "physical_verification": "separate OpenLane run required",
        "corner_scope": "TT 1.8V 25C only; does not qualify other PVT corners",
    }
    record = out / "manifest.json"
    record.write_text(json.dumps(manifest, indent=2) + "\n")
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
        record.write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
