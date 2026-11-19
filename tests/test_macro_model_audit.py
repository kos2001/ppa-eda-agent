import json
from pathlib import Path
import sys
import tempfile
import unittest
import shutil
import subprocess

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
            self.assertTrue(result["audit_complete"])
            # The current archive omits the STA stage but retains corner folders.
            shutil.move(str(rpt), str(run / rpt.name))
            rpt.parent.rmdir()
            rpt = run / rpt.name
            direct = audit.check(root, run)
            self.assertTrue(direct["audit_complete"])
            self.assertEqual(direct["corner_models"], result["corner_models"])
            # Flatten the exact same bytes, as the retained physical archive does.
            for name, suffix in (("checks.rpt", "-drv.rpt"), ("macro_inputs.csv", "-macro-inputs.csv")):
                (run / (rpt.name + suffix)).write_bytes((rpt / name).read_bytes())
            shutil.rmtree(rpt)
            archived = audit.check(root, run)
            self.assertTrue(archived["audit_complete"])
            for key in ("corner_models", "input_axis_extrapolations", "unknown_input_checks"):
                self.assertEqual(archived[key], result[key])
            csv_path = run / (rpt.name + "-macro-inputs.csv")
            saved = csv_path.read_bytes()
            csv_path.write_bytes(saved.replace(b"u/d[1]", b"u/other"))
            wrong_pin = audit.check(root, run)
            self.assertFalse(wrong_pin["audit_complete"])
            self.assertFalse(wrong_pin["input_coverage_complete"])
            self.assertTrue(wrong_pin["unknown_input_checks"])
            csv_path.unlink()
            missing_input = audit.check(root, run)
            self.assertFalse(missing_input["audit_complete"])
            self.assertEqual(missing_input["input_audit"]["missing_corners"], [rpt.name])
            csv_path.write_bytes(saved)
            (run / (rpt.name + "-drv.rpt")).unlink()
            incomplete = audit.check(root, run)
            self.assertFalse(incomplete["audit_complete"])
            self.assertEqual(incomplete["missing_corners"], [rpt.name])
            self.assertTrue(incomplete["issues"])
            cli = subprocess.run([sys.executable, str(Path(audit.__file__)),
                                  "--design", str(root), "--run-dir", str(run)],
                                 capture_output=True, text=True, timeout=10)
            self.assertEqual(cli.returncode, 1)
            self.assertEqual(json.loads(cli.stdout)["status"], "incomplete")
            # Largest global axis (.100ns) would miss this .020ns data limit.

    def test_live_stage_does_not_borrow_missing_reports_from_archive(self):
        from sta_evidence import reports
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "62-openroad-stapostpnr").mkdir()
            (root / "max_ss_100C_1v60").mkdir()
            (root / "max_ss_100C_1v60/checks.rpt").write_text("stale")
            (root / "max_ss_100C_1v60-macro-inputs.csv").write_text("stale")
            self.assertEqual(reports(root, "checks.rpt"), {})
            self.assertEqual(reports(root, "macro_inputs.csv"), {})
            (root / "62-openroad-stapostpnr").rmdir()
            self.assertEqual(set(reports(root, "checks.rpt")), {"max_ss_100C_1v60"})
            self.assertEqual(reports(root, "macro_inputs.csv"), {})

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
