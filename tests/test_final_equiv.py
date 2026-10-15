import sys
import os
import json
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from equiv_check import physical_models, check


class PhysicalModelTests(unittest.TestCase):
    def test_timeout_is_unverified_and_removes_only_its_owned_container(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/dut.v").write_text("module dut; endmodule")
            (root / "config.json").write_text('{"DESIGN_NAME":"dut"}')
            (root / "final/nl").mkdir(parents=True)
            (root / "final/nl/dut.nl.v").write_text("module dut; endmodule")
            liberty = root / "cells.lib"
            liberty.write_text("library(L) {}")
            with patch("equiv_check.find_liberty", return_value=liberty), patch(
                    "equiv_check.subprocess.run", side_effect=[
                        subprocess.TimeoutExpired("docker", 600, output=b"Found 42 unproven $equiv cells\npartial proof"),
                        subprocess.CompletedProcess([], 0, "", "")]) as runner:
                result = check(root, root, final_netlist=True)
            self.assertFalse(result["equivalent"])
            self.assertTrue(result["timed_out"])
            self.assertIsNone(result["unproven_points"])
            command = runner.call_args_list[0].args[0]
            owned = command[command.index("--name") + 1]
            self.assertTrue(owned.startswith("ppa-equiv-"))
            self.assertEqual(runner.call_args_list[1].args[0], ["docker", "rm", "-f", owned])
            self.assertIn("deadline exceeded", result["tail"])

    def test_missing_liberty_filler_requires_power_only_lef(self):
        lef = "MACRO sky130_fd_sc_hd__fill_1\nPIN VPWR\nUSE POWER ;\nEND VPWR\nEND sky130_fd_sc_hd__fill_1\n"
        model, _ = physical_models("sky130_fd_sc_hd__fill_1", "library(L) {}", lef)
        self.assertIn("module sky130_fd_sc_hd__fill_1()", model)
        with self.assertRaises(ValueError):
            physical_models("sky130_fd_sc_hd__fill_1", "library(L) {}", lef.replace("POWER", "SIGNAL"))

    def test_only_input_only_known_physical_cells_are_modeled(self):
        lib = '''library(L) {
          cell(sky130_fd_sc_hd__diode_2) { pin(DIODE) { direction: input; } }
          cell(sky130_fd_sc_hd__fill_1) {}
        }'''
        model, cells = physical_models(
            "sky130_fd_sc_hd__diode_2 a(.DIODE(x)); sky130_fd_sc_hd__fill_1 b(); sky130_fd_sc_hd__buf_4 c();", lib)
        self.assertEqual(len(cells), 2)
        self.assertIn("input DIODE", model)
        self.assertNotIn("buf_4", model)

    def test_output_or_missing_cells_are_refused(self):
        for body in ('pin(DIODE) { direction: output; function: "1"; }',
                     'ff(I,Q) {}'):
            with self.assertRaises(ValueError):
                physical_models("sky130_fd_sc_hd__diode_2", f"library(L) {{ cell(sky130_fd_sc_hd__diode_2) {{ {body} }} }}")
        with self.assertRaises(ValueError):
            physical_models("sky130_fd_sc_hd__diode_2", "library(L) {}")


@unittest.skipUnless(os.environ.get("PPA_FINAL_EQUIV_INTEGRATION") == "1", "requires Docker and SKY130 PDK")
class ActualFinalProofTests(unittest.TestCase):
    def test_correct_buffer_passes_and_wrong_inverter_fails(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/dut.v").write_text("module dut(input a, output y); assign y=a; endmodule\n")
            (root / "config.json").write_text(json.dumps({"DESIGN_NAME": "dut"}))
            run = root / "runs/proof"
            (run / "final/nl").mkdir(parents=True)
            (run / "01-yosys-synthesis").mkdir()
            (run / "01-yosys-synthesis/dut.nl.v").write_text(
                "module dut(input a, output y); sky130_fd_sc_hd__buf_1 b(.A(a),.X(y)); endmodule\n")
            gate = run / "final/nl/dut.nl.v"
            for cell, port, expected in (("buf_1", "X", True), ("inv_1", "Y", False)):
                gate.write_text(f"module dut(input a, output y); sky130_fd_sc_hd__{cell} b(.A(a),.{port}(y)); "
                                "sky130_fd_sc_hd__diode_2 d(.DIODE(y)); sky130_fd_sc_hd__fill_1 f(); endmodule\n")
                for synthesis_reference in (False, True):
                    result = check(root, run, final_netlist=True,
                                   reference_synthesis=synthesis_reference)
                    self.assertEqual(result["equivalent"], expected, result)
                    if expected:
                        self.assertFalse(result["vacuous"])
                    else:
                        self.assertGreater(result["unproven_points"], 0)
