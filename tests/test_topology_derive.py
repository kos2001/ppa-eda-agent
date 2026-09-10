"""Tests for pipeline/topology_derive.py — topology.json from artefacts.

aes, gcd and riscv32i had no topology.json, so their 19 cases carried
`topology: null` and case_retrieval's fallback had nothing to compare.
The derivation reads config.json, the Verilog sources and a completed
run's Yosys JSON netlist. Two things are guarded: that on counter4,
which has a hand-written file, the derivation reproduces it field for
field; and that neither --write nor --backfill ever replaces something
already recorded.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import topology_derive as td  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


def toy_design(root: Path, seq_types=("sky130_fd_sc_hd__dfxtp_1",) * 3) -> Path:
    d = root / "designs" / "toy"
    (d / "src").mkdir(parents=True)
    (d / "src" / "toy.v").write_text("module toy(input clk, output q);\nendmodule\nmodule sub();\nendmodule\n")
    (d / "config.json").write_text(json.dumps({
        "DESIGN_NAME": "toy", "CLOCK_PORT": "clk", "VERILOG_FILES": "dir::src/*.v"}))
    run = d / "runs" / "r1" / "06-yosys-synthesis"
    run.mkdir(parents=True)
    cells = {f"c{i}": {"type": t} for i, t in enumerate(seq_types)}
    cells["and"] = {"type": "sky130_fd_sc_hd__and2_1"}
    (run / "toy.nl.v.json").write_text(json.dumps({"modules": {
        "toy": {"ports": {"clk": {}, "q": {}}, "cells": cells},
        "sky130_fd_sc_hd__and2_1": {"ports": {}, "cells": {}},
    }}))
    return d


class DeriveTests(unittest.TestCase):
    def test_reads_every_field_from_an_artefact(self):
        with tempfile.TemporaryDirectory() as tmp:
            topo = td.derive(toy_design(Path(tmp)))
        self.assertEqual(topo["module_count"], 2)
        self.assertEqual(topo["port_count"], 2)
        self.assertEqual(topo["sequential_element_estimate"], 3)
        self.assertEqual(topo["clock_domain_count"], 1)
        self.assertEqual(topo["power_domain_count"], 1)
        self.assertFalse(topo["has_macros"])
        self.assertIn("Derived by topology_derive.py", topo["notes"])

    def test_gf180_sequential_names_count_too(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = toy_design(Path(tmp), seq_types=("gf180mcu_fd_sc_mcu7t5v0__dffq_1",
                                                 "gf180mcu_fd_sc_mcu7t5v0__latq_1"))
            self.assertEqual(td.derive(d)["sequential_element_estimate"], 2)

    def test_a_list_of_clock_ports_is_that_many_domains(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = toy_design(Path(tmp))
            cfg = json.loads((d / "config.json").read_text())
            cfg["CLOCK_PORT"] = ["clk_a", "clk_b"]
            (d / "config.json").write_text(json.dumps(cfg))
            self.assertEqual(td.derive(d)["clock_domain_count"], 2)

    def test_no_netlist_is_an_error_not_a_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = toy_design(Path(tmp))
            for p in (d / "runs").rglob("*.json"):
                p.unlink()
            with self.assertRaises(FileNotFoundError):
                td.derive(d)


class WriteTests(unittest.TestCase):
    def test_refuses_to_overwrite_a_recorded_topology(self):
        with tempfile.TemporaryDirectory() as tmp:
            d = toy_design(Path(tmp))
            (d / "topology.json").write_text("{}")
            with self.assertRaises(FileExistsError):
                td.write_topology(d, {"module_count": 1})
            td.write_topology(d, {"module_count": 1}, force=True)
            self.assertEqual(json.loads((d / "topology.json").read_text())["module_count"], 1)

    def test_backfill_touches_only_null_topology_of_that_design(self):
        with tempfile.TemporaryDirectory() as tmp:
            cases = Path(tmp)
            (cases / "toy__1.json").write_text(json.dumps({"design": "toy", "topology": None, "x": 1}))
            (cases / "toy__2.json").write_text(json.dumps({"design": "toy", "topology": {"module_count": 9}}))
            (cases / "other__1.json").write_text(json.dumps({"design": "other", "topology": None}))
            changed = td.backfill("toy", {"module_count": 2}, cases_dir=cases)
            self.assertEqual(changed, ["toy__1.json"])
            self.assertEqual(json.loads((cases / "toy__1.json").read_text())["topology"]["module_count"], 2)
            self.assertEqual(json.loads((cases / "toy__1.json").read_text())["x"], 1)
            self.assertEqual(json.loads((cases / "toy__2.json").read_text())["topology"]["module_count"], 9)
            self.assertIsNone(json.loads((cases / "other__1.json").read_text())["topology"])


class RealDesignTests(unittest.TestCase):
    """Against the real counter4 run on this machine, when one exists."""

    def test_counter4_derivation_matches_its_hand_written_file(self):
        d = ROOT / "pipeline" / "designs" / "counter4"
        if not td.runs_with_netlist(d):
            self.skipTest("no counter4 run with a Yosys netlist on this machine")
        hand = json.loads((d / "topology.json").read_text(encoding="utf-8"))
        derived = td.derive(d)
        for key in ("module_count", "has_macros", "clock_domain_count",
                    "port_count", "sequential_element_estimate", "power_domain_count"):
            self.assertEqual(derived[key], hand[key], key)

    def test_every_case_in_the_store_has_a_topology(self):
        # The condition the fallback needs. 19 cases had none on
        # 2026-09-10; a new design run without a topology.json would
        # bring the number back.
        missing = []
        for path in (ROOT / "reference-db" / "cases").glob("*.json"):
            case = json.loads(path.read_text(encoding="utf-8"))
            if not case.get("topology"):
                missing.append(path.name)
        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
