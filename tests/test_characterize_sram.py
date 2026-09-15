import sys
import unittest
import importlib.util
import json
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import characterize_sram as cs


class SimulationArchiveTests(unittest.TestCase):
    def test_preserves_each_measurement_before_shared_logs_are_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            temp = root / "temp"
            temp.mkdir()
            def simulate(instance, name):
                (temp / "timing.lis").write_text((temp / name).read_text())
                return 42
            wrapped = cs.archive_simulations(simulate, temp, root / "out")
            for value in ("first", "second"):
                (temp / "delay_stim.sp").write_text(value)
                self.assertEqual(wrapped(None, "delay_stim.sp"), 42)
            for number, value in enumerate(("first", "second"), 1):
                archive = root / "out/simulations" / f"{number:06d}"
                self.assertEqual((archive / "timing.lis").read_text(), value)
                self.assertEqual((archive / "delay_stim.sp").read_text(), value)
                self.assertEqual(json.loads((archive / "status.json").read_text())["status"],
                                 "returned_unverified")

    def test_failure_is_preserved_and_propagated_without_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def simulate(instance, name):
                (root / "spice_stderr.log").write_text("measurement error")
                raise ValueError("failed")
            wrapped = cs.archive_simulations(simulate, root, root / "out")
            with self.assertRaisesRegex(ValueError, "failed"):
                wrapped(None, "missing.sp")
            archive = root / "out/simulations/000001"
            self.assertEqual((archive / "spice_stderr.log").read_text(), "measurement error")
            state = json.loads((archive / "status.json").read_text())
            self.assertEqual(state["status"], "raised")
            self.assertIn("finished_at", state)


def load_characterization_config():
    path = (Path(__file__).resolve().parents[1] / "pipeline" /
            "designs" / "sram_wrapper" / "characterization" / "config.py")
    spec = importlib.util.spec_from_file_location("sram_characterization_config", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


class NetlistInterfaceTests(unittest.TestCase):
    def storage_netlist(self):
        return f""".SUBCKT {cs.MACRO} vdd gnd
Xbank0 vdd gnd bank
.ENDS
.SUBCKT bank vdd gnd
Xbitcell_array vdd gnd replica
.ENDS
.SUBCKT replica vdd gnd
Xbitcell_array vdd gnd array
.ENDS
.SUBCKT array vdd gnd
Xbit_r0_c0 vdd gnd cell
.ENDS
.SUBCKT cell vdd gnd
X0 Q Q_bar vdd gnd transistor
.ENDS
"""

    def test_measurement_hierarchy_resolves_actual_instances(self):
        cfg = load_characterization_config()
        result = cs.validate_storage_paths(self.storage_netlist(), cfg.cell_format, 1, 1)
        self.assertEqual(result["checked_bitcells"], 1)

    def test_missing_array_level_is_rejected(self):
        cfg = load_characterization_config()
        wrong = cfg.cell_format.replace("{hier_sep}xbitcell_array", "", 1)
        with self.assertRaisesRegex(ValueError, "unresolved storage probe"):
            cs.validate_storage_paths(self.storage_netlist(), wrong, 1, 1)

    def test_sense_enable_requires_connected_top_level_nodes(self):
        cfg = load_characterization_config()
        text = self.storage_netlist().replace("Xbank0 vdd", "Xbank0 s_en0 s_en1 vdd")
        result = cs.validate_storage_paths(text, cfg.cell_format, 1, 1, cfg.sen_format)
        self.assertEqual(len(result["sense_enable_nodes"]), 2)
        with self.assertRaisesRegex(ValueError, "sense-enable probe"):
            cs.validate_storage_paths(text, cfg.cell_format, 1, 1,
                                      "X{name}{hier_sep}xbank0{hier_sep}s_en")
        with self.assertRaisesRegex(ValueError, "sense-enable probe"):
            cs.validate_storage_paths(text.replace("s_en1", "other"),
                                      cfg.cell_format, 1, 1, cfg.sen_format)

    def test_missing_cell_and_storage_node_are_rejected(self):
        cfg = load_characterization_config()
        with self.assertRaisesRegex(ValueError, "xbit_r0_c1"):
            cs.validate_storage_paths(self.storage_netlist(), cfg.cell_format, 1, 2)
        with self.assertRaisesRegex(ValueError, "Q/Q_bar missing"):
            cs.validate_storage_paths(self.storage_netlist().replace("Q_bar", "other"),
                                      cfg.cell_format, 1, 1)

    def test_reorders_interface_without_changing_internal_connections(self):
        pins = list(reversed(cs.canonical_pins()))
        body = "\nXbank din0[31] addr0[7] vccd1 vssd1 bank\n.ENDS\n"
        original = ".SUBCKT " + cs.MACRO + " " + " ".join(pins) + body
        got = cs.prepare_netlist(original)
        self.assertEqual(got.splitlines()[0].split()[2:], cs.canonical_pins())
        self.assertTrue(got.endswith(body))
        self.assertEqual(cs.prepare_netlist(got), got)

    def test_continuation_pins_are_preserved(self):
        pins = cs.canonical_pins()
        text = (".SUBCKT " + cs.MACRO + " " + " ".join(pins[:32])
                + "\n+ " + " ".join(pins[32:]) + "\n.ENDS\n")
        self.assertEqual(cs.prepare_netlist(text).splitlines()[0].split()[2:], pins)

    def test_missing_duplicate_and_unknown_pins_are_rejected(self):
        pins = cs.canonical_pins()
        for invalid in (pins[:-1], pins[:-1] + [pins[0]], pins[:-1] + ["unknown"]):
            with self.assertRaises(ValueError):
                cs.prepare_netlist(".SUBCKT " + cs.MACRO + " " + " ".join(invalid) + "\n.ENDS\n")

    def test_duplicate_top_is_rejected(self):
        text = ".SUBCKT " + cs.MACRO + " " + " ".join(cs.canonical_pins()) + "\n.ENDS\n"
        with self.assertRaises(ValueError):
            cs.prepare_netlist(text + text)
