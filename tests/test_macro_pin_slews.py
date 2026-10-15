"""Whether the macro interface was measured, not merely not-reported.

The signoff STA's max-slew table lists only pins over a limit. A macro pin
with no limit in its liberty (din0, web0, the clocks) can sit at any slew and
never appear, so "no violations" never meant "inside the characterised range".
flows/macro_signoff.py now asks STA for the slew at every macro input pin; this
pins what model_validity does with that evidence.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import model_validity  # noqa: E402
from flows.macro_signoff import macro_pin_slew_script  # noqa: E402

NL = chr(10)
CORNER_TCL = "read_spefs" + NL + NL + "set corner [lindex [sta::corners] 0]" + NL + \
    "sta::set_cmd_corner $corner" + NL + "set clocks [all_clocks]" + NL

CHECKS = """
max slew

Pin                                        Limit        Slew       Slack
------------------------------------------------------------------------

max fanout
"""


def pin_report(corner, slews):
    lines = [f"corner {corner}"] + [f"MACRO_PIN u_sram/{p} {s}" for p, s in slews.items()]
    return NL.join(lines) + NL


def make_run(root, corners):
    """A run directory with a STA step per corner. corners: {name: {pin: slew} | None}."""
    sta = root / "55-openroad-stapostpnr"
    for corner, slews in corners.items():
        d = sta / corner
        d.mkdir(parents=True)
        (d / "checks.rpt").write_text(CHECKS)
        if slews is not None:
            (d / "macro_pin_slews.rpt").write_text(pin_report(corner, slews))
    return root


def make_design(root, ceiling=0.04):
    lib = root / "lib"
    lib.mkdir(parents=True)
    (lib / "macro.lib").write_text(
        'library (m) { cell (c) { pin (a) { timing () { cell_rise (t) {'
        f' index_1("0.00125, 0.005, {ceiling}"); }} }} }} }} }}')
    (root / "config.json").write_text(json.dumps({
        "MACROS": {"c": {"lib": {"*": ["dir::lib/macro.lib"]},
                         "instances": {"u_sram": {"location": [0, 0], "orientation": "N"}}}}}))
    return root


class ScriptPatch(unittest.TestCase):
    def test_the_query_goes_right_after_the_corner_is_set(self):
        out = macro_pin_slew_script(CORNER_TCL, ["u_sram"])
        self.assertLess(out.index("sta::set_cmd_corner $corner"), out.index("MACRO_PIN"))
        self.assertLess(out.index("MACRO_PIN"), out.index("set clocks"))
        self.assertIn("foreach macro_instance {u_sram}", out)
        self.assertIn("slew_max", out)
        self.assertIn("%OL_CREATE_REPORT macro_pin_slews.rpt", out)

    def test_every_macro_instance_is_named(self):
        out = macro_pin_slew_script(CORNER_TCL, ["u_b", "u_a"])
        self.assertIn("{u_a u_b}", out)

    def test_a_design_without_macros_gets_the_script_unchanged(self):
        self.assertEqual(macro_pin_slew_script(CORNER_TCL, []), CORNER_TCL)

    def test_a_changed_upstream_script_is_refused_not_guessed_at(self):
        for bad in ("nothing here" + NL, CORNER_TCL + CORNER_TCL):
            with self.assertRaises(ValueError):
                macro_pin_slew_script(bad, ["u_sram"])


class Parse(unittest.TestCase):
    def test_pins_and_numbers(self):
        pins, missing = model_validity.parse_pin_slews(pin_report("c", {"addr0[0]": 0.0312, "clk0": 0.1}))
        self.assertEqual([p["pin"] for p in pins], ["u_sram/addr0[0]", "u_sram/clk0"])
        self.assertAlmostEqual(pins[1]["slew_ns"], 0.1)
        self.assertEqual(missing, 0)

    def test_a_pin_with_no_number_is_counted_not_dropped(self):
        pins, missing = model_validity.parse_pin_slews("MACRO_PIN u_sram/web0 NaN?" + NL)
        self.assertEqual((pins, missing), ([], 1))


class Exhaustive(unittest.TestCase):
    def check(self, corners, ceiling=0.04):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            design = make_design(root / "design", ceiling)
            run = make_run(root / "run", corners)
            return model_validity.check(design, run)

    def test_all_pins_inside_the_range_at_every_corner_leaves_nothing_unverified(self):
        got = self.check({"nom_tt": {"addr0[0]": 0.021, "din0[0]": 0.03, "clk0": 0.039},
                          "max_ss": {"addr0[0]": 0.034, "din0[0]": 0.038, "clk0": 0.04}})
        self.assertEqual(got["coverage"], "every_macro_input_pin_every_corner")
        self.assertTrue(got["model_validity_verified"])
        self.assertEqual(got["pins_checked"], 3)
        self.assertEqual(model_validity.unverified(got), [])

    def test_a_pin_past_the_range_at_one_corner_is_named_with_that_corner(self):
        got = self.check({"nom_tt": {"addr0[0]": 0.02, "clk0": 0.03},
                          "max_ss": {"addr0[0]": 0.03, "clk0": 0.32}})
        self.assertFalse(got["model_validity_verified"])
        pin = got["extrapolated_pins"][0]
        self.assertEqual((pin["pin"], pin["corner"]), ("u_sram/clk0", "max_ss"))
        self.assertEqual(pin["times_past_ceiling"], 8.0)
        lines = model_validity.unverified(got)
        self.assertEqual(len(lines), 1)
        self.assertIn("u_sram/clk0", lines[0])
        # Coverage was complete, so the caveat about unreported pins is gone.
        self.assertNotIn("omit nonviolating pins", lines[0])

    def test_pins_without_a_liberty_limit_are_covered_too(self):
        """din0/web0/clk have no max_transition: the old violator list could not see them."""
        got = self.check({"max_ss": {"din0[3]": 0.09}})
        self.assertEqual([p["pin"] for p in got["extrapolated_pins"]], ["u_sram/din0[3]"])

    def test_a_report_for_only_some_corners_does_not_claim_coverage(self):
        got = self.check({"nom_tt": {"addr0[0]": 0.02}, "max_ss": None})
        self.assertEqual(got["coverage"], "reported_slew_violations_only")
        self.assertFalse(got["model_validity_verified"])
        self.assertEqual(len(model_validity.unverified(got)), 1)

    def test_an_unmeasured_pin_means_no_claim(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            design = make_design(root / "design")
            run = make_run(root / "run", {"nom_tt": {"addr0[0]": 0.02}})
            rpt = run / "55-openroad-stapostpnr" / "nom_tt" / "macro_pin_slews.rpt"
            rpt.write_text(rpt.read_text() + "MACRO_PIN u_sram/web0 ?" + NL)
            self.assertEqual(model_validity.check(design, run)["coverage"],
                             "reported_slew_violations_only")

    def test_old_runs_without_the_report_keep_the_old_behaviour(self):
        got = self.check({"nom_tt": None, "max_ss": None})
        self.assertEqual(got["coverage"], "reported_slew_violations_only")
        self.assertEqual(len(model_validity.unverified(got)), 1)


if __name__ == "__main__":
    unittest.main()
