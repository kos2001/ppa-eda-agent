from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from macro_output_load_audit import audit_log


def fixture():
    lines = [" capacitance 1pF", "Found 1 unannotated drivers.", " tie/HI",
             "Found 0 partially unannotated drivers."]
    for port in range(2):
        for bit in range(32):
            pin = f"u_sram/dout{port}[{bit}]"
            for analysis in ("min", "max"):
                lines.append(f"LOAD_DCALC_BEGIN {pin} {analysis}")
                for edge in ("^", "v"):
                    lines += [f"clk{port} v -> dout{port}[{bit}] {edge}",
                              "Pi model C2=0.002 Rpi=0.03 C1=0.009, Ceff=0.010",
                              "| total_output_net_capacitance = 0.010",
                              "| 0.005 0.020", "Delay = 0.4", "| total_output_net_capacitance = 0.010",
                              "| 0.005 0.020", "Slew = 0.02"]
                lines.append(f"LOAD_DCALC_END {pin} {analysis}")
    lines.append("LOAD_EXPORT_COMPLETE 64")
    return "\n".join(lines) + "\n"


class OutputLoadAuditTests(unittest.TestCase):
    def test_complete_preserves_distinct_load_semantics(self):
        result = audit_log(fixture())
        self.assertTrue(result["complete"], result["issues"])
        self.assertEqual(len(result["observations"]), 256)
        row = result["observations"][0]
        self.assertAlmostEqual(row["pi_total_pf"], .011)
        self.assertEqual(row["table_load_pf"], .010)
        self.assertFalse(result["model_qualified"])
        self.assertTrue(result["all_within_interpolation_brackets"])

    def test_axis_extrapolation_is_observed_but_not_in_range(self):
        result = audit_log(fixture().replace("| 0.005 0.020", "| 0.012 0.020", 1))
        self.assertTrue(result["complete"], result["issues"])
        self.assertFalse(result["all_within_interpolation_brackets"])
        self.assertFalse(result["observations"][0]["within_interpolation_brackets"])
        for bad in ("| 0.020 0.005", "| nan 0.020", "| 0.020"):
            self.assertFalse(audit_log(fixture().replace("| 0.005 0.020", bad, 1))["complete"])

    def test_missing_duplicate_and_wrong_identity_fail(self):
        text = fixture()
        begin = text.index("LOAD_DCALC_BEGIN")
        end = text.index("LOAD_DCALC_BEGIN", begin + 1)
        for changed in (text[:begin] + text[end:], text[:end] + text[begin:end] + text[end:],
                        text.replace("clk0 v -> dout0[0] ^", "clk1 v -> dout0[0] ^", 1),
                        text.replace("LOAD_DCALC_END u_sram/dout0[0] min", "LOAD_DCALC_END u_sram/dout0[1] min", 1)):
            self.assertFalse(audit_log(changed)["complete"])

    def test_missing_edge_is_unknown_not_zero(self):
        text = fixture().replace("clk0 v -> dout0[0] ^", "not an arc", 1)
        result = audit_log(text)
        self.assertFalse(result["complete"])
        self.assertIn({"pin": "u_sram/dout0[0]", "analysis": "min", "edge": "rise"}, result["missing_observations"])

    def test_nonfinite_negative_or_conflicting_tables_fail(self):
        for before, after in (("Ceff=0.010", "Ceff=nan"), ("C2=0.002", "C2=-0.002"),
                              ("total_output_net_capacitance = 0.010", "total_output_net_capacitance = 0.012")):
            with self.subTest(after=after):
                self.assertFalse(audit_log(fixture().replace(before, after, 1))["complete"])

    def test_units_annotation_completion_fail_closed(self):
        for before, after in (("capacitance 1pF", "capacitance 1fF"),
                              ("Found 1 unannotated drivers.", "annotation unavailable"),
                              (" tie/HI", " u_sram/dout0[0]"),
                              ("Found 0 partially unannotated drivers.", "Found 1 partially unannotated drivers."),
                              ("LOAD_EXPORT_COMPLETE 64", "")):
            with self.subTest(after=after):
                self.assertFalse(audit_log(fixture().replace(before, after, 1))["complete"])


if __name__ == "__main__":
    unittest.main()
