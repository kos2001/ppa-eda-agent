import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import macro_model_audit as audit
import model_validity


LIB = '''library(m_SS_1p6V_100C) {
 time_unit : "1ps";
 user_data : "http://example/{quoted}";
 default_operating_conditions : OC;
 operating_conditions(OC) { voltage : 1.6; temperature : 100; process : 1; }
 lu_table_template(CONSTRAINT) {
  variable_1 : related_pin_transition;
  variable_2 : constrained_pin_transition;
  index_1("10,100"); index_2("1,20");
 }
 cell(m) {
  pin(clk0) { direction : input;
   timing() { timing_type : minimum_period;
    rise_constraint(scalar) { values("50"); }
   }
  }
  bus(d) { direction : input;
   pin(d[1:0]) { timing() {
    related_pin : "clk0"; timing_type : setup_rising;
    rise_constraint(CONSTRAINT) { values("10,20","30,40"); }
   } }
  }
 }
}'''


class MacroModelAuditTests(unittest.TestCase):
    def test_actual_axis_semantics_units_and_bus_expansion(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m.lib").write_text(LIB)
            (root / "config.json").write_text(json.dumps({"MACROS": {"m": {
                "lib": {"*": ["dir::m.lib"]}, "instances": {"u": {}}}}}))
            run = root / "run"
            rpt = run / "62-openroad-stapostpnr/max_ss_100C_1v60"
            rpt.mkdir(parents=True)
            (rpt / "checks.rpt").write_text("actual report present")
            (run / "resolved.json").write_text(json.dumps({"STA_CORNERS": [rpt.name]}))
            (rpt / "macro_inputs.csv").write_text(
                "pin,direction,max_rise_ns,max_fall_ns,min_rise_ns,min_fall_ns\n"
                "u/clk0,input,0.08,0.06,0.08,0.06\n"
                "u/d[0],input,0.03,0.01,0.01,0.01\n"
                "u/d[1],input,0.01,0.01,0.01,0.01\n")
            result = audit.check(root, run)
            self.assertTrue(result["input_coverage_complete"])
            self.assertTrue(result["corner_models"][0]["pvt_matches_declared"])
            self.assertFalse(result["model_qualified"])
            self.assertEqual(result["unknown_input_checks"], [])
            self.assertEqual(len(result["input_axis_extrapolations"]), 1)
            row = result["input_axis_extrapolations"][0]
            self.assertEqual((row["pin"], row["axis"], row["variable"]),
                             ("u/d[0]", 2, "constrained_pin_transition"))
            self.assertEqual(row["range_ns"], [0.001, 0.02])
            # Largest global axis (.100ns) would miss this .020ns data limit.

    def test_rejects_partial_lib_and_invalid_table_dimensions_or_axes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "m.lib"
            for bad in [LIB[:-2], LIB.replace('"30,40"', '"30"'),
                        LIB.replace('"10,100"', '"100,10"'),
                        LIB.replace('"10,100"', '"10,nan"'),
                        LIB.replace('values("50")', 'values("nan")'),
                        LIB.replace('voltage : 1.6', 'voltage : nan'),
                        LIB.replace('temperature : 100', 'temperature : inf'),
                        LIB.replace('"1ps"', '"0ps"'),
                        LIB.replace('default_operating_conditions : OC;',
                                    'default_operating_conditions : ABSENT;')]:
                path.write_text(bad)
                with self.assertRaises(ValueError):
                    audit.read_model(path, "m")

    def test_corner_decoder_handles_negative_temperature_and_voltages(self):
        self.assertEqual(audit.corner_pvt("min_ff_n40C_1v95"),
                         {"process": "FF", "voltage_V": 1.95, "temperature_C": -40})
        self.assertIsNone(audit.corner_pvt("unknown"))

    def test_missing_macro_library_is_unknown_and_cannot_disappear_as_no_macro(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text(json.dumps({"MACROS": {"m": {
                "lib": {"*": ["dir::missing.lib"]}, "instances": {"u": {}}}}}))
            rpt = root / "run/62-openroad-stapostpnr/max_ss_100C_1v60"
            rpt.mkdir(parents=True)
            (rpt / "checks.rpt").write_text("max slew\nmax fanout\n")
            result = model_validity.check(root, root / "run")
            self.assertIsNotNone(result)
            self.assertIsNone(result["worst_times_past_ceiling"])
            self.assertTrue(model_validity.unverified(result))
            self.assertFalse(result["model_validity_verified"])
            # Malformed axes must not turn legacy ratio diagnostics into
            # a division by zero or a JSON NaN.
            (root / "missing.lib").write_text(LIB.replace('"10,100"', '"0,0"'))
            result = model_validity.check(root, root / "run")
            self.assertIsNone(result["worst_times_past_ceiling"])
            self.assertTrue(result["macro_arc_audit"]["issues"])

    def test_resolved_run_mapping_overrides_design_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "m.lib").write_text(LIB)
            old = {"m": {"lib": {"*": ["dir::missing.lib"]}, "instances": {"u": {}}}}
            actual = {"m": {"lib": {"*": ["/design/m.lib"]}, "instances": {"u": {}}}}
            (root / "config.json").write_text(json.dumps({"MACROS": old}))
            run = root / "run"
            rpt = run / "62-openroad-stapostpnr/max_ss_100C_1v60"
            rpt.mkdir(parents=True)
            (rpt / "checks.rpt").write_text("present")
            (run / "resolved.json").write_text(json.dumps({"MACROS": actual}))
            result = audit.check(root, run)
            self.assertTrue(result["corner_models"][0]["pvt_matches_declared"])
            self.assertEqual(result["config_source"], str(run / "resolved.json"))
            self.assertFalse(result["model_qualified"])
            (root / "config.json").write_text('{"MACROS": {}}')
            self.assertIsNotNone(model_validity.check(root, run))
