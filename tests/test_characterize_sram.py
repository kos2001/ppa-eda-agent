import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import characterize_sram as cs


class NetlistInterfaceTests(unittest.TestCase):
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
