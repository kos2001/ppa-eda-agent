import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import antenna_net_report


class AntennaNetReportTests(unittest.TestCase):
    def test_unknown_net_is_not_reported_as_zero_diodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            netlist, antenna = root / "test.v", root / "antenna.rpt"
            netlist.write_text("\nsky130_fd_sc_hd__buf_4 driver (.A(i), .X(n));\n"
                               "sky130_fd_sc_hd__diode_2 ant (.DIODE(n));\n"
                               "sky130_fd_sc_hd__buf_1 sink (.A(n), .X(o));\n")
            antenna.write_text("│ 1.2 │ 480 │ 400 │ n │ sink/A │ met3 │\n"
                               "│ 1.1 │ 440 │ 400 │ absent │ x/A │ met1 │\n", encoding="utf-8")
            result = antenna_net_report.report(netlist, antenna)
            known, unknown = result["rows"]
            self.assertEqual(known["diodes"], ["ant"])
            self.assertEqual(len(known["logic_loads"]), 1)
            self.assertIsNone(known["route_geometry"])
            self.assertFalse(unknown["matched_net"])
            self.assertIsNone(unknown["diodes"])
            self.assertEqual(result["violating_nets"], 2)

    def test_empty_report_does_not_claim_a_pass(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            netlist, antenna = root / "test.v", root / "antenna.rpt"
            netlist.write_text("")
            antenna.write_text("")
            with self.assertRaises(ValueError):
                antenna_net_report.report(netlist, antenna)
