import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
from macro_slew_audit import read_audit
from flows.macro_input_audit import audit_script


class MacroSlewAuditTests(unittest.TestCase):
    def run_report(self, missing=False, unknown=False):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        (root / "resolved.json").write_text(json.dumps({"STA_CORNERS": ["tt", "ss"]}))
        for corner in ("tt", "ss"):
            if missing and corner == "ss":
                continue
            folder = root / "54-openroad-stapostpnr" / corner
            folder.mkdir(parents=True)
            value = "NA" if unknown and corner == "ss" else "0.3"
            (folder / "macro_inputs.csv").write_text(
                "pin,direction,max_rise_ns,max_fall_ns,min_rise_ns,min_fall_ns\n"
                f"u_sram/clk0,input,0.2,{value},0.1,0.1\n")
        return root

    def test_hidden_clock_edge_is_included_and_coverage_does_not_qualify_models(self):
        result = read_audit(self.run_report(), {"u_sram": 1})
        self.assertTrue(result["coverage_complete"])
        self.assertEqual(result["worst"]["slew_ns"], 0.3)
        self.assertEqual(result["worst"]["edge"], "max_fall_ns")
        self.assertFalse(result["model_validity_verified"])

    def test_missing_corner_and_unknown_edge_never_become_zero_or_complete(self):
        missing = read_audit(self.run_report(missing=True), {"u_sram": 1})
        self.assertFalse(missing["coverage_complete"])
        self.assertEqual(missing["missing_corners"], ["ss"])
        unknown = read_audit(self.run_report(unknown=True), {"u_sram": 1})
        self.assertFalse(unknown["coverage_complete"])
        self.assertEqual(len(unknown["unknown_edges"]), 1)
        self.assertIsNone(unknown["rows"][-1]["max_fall_ns"])

    def test_original_sta_runs_before_additional_report_and_tcl_substitution_is_escaped(self):
        script = audit_script('/scripts/corner.tcl', ['u_sram', 'block$unsafe[0]'])
        self.assertTrue(script.startswith('source "/scripts/corner.tcl"\n'))
        self.assertIn('block\\$unsafe\\[0\\]', script)
        self.assertIn('slew_max_rise slew_max_fall slew_min_rise slew_min_fall', script)
        self.assertNotIn('set_max_transition', script)


if __name__ == "__main__":
    unittest.main()
