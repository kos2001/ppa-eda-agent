"""Tests for detecting timing reported from off the end of a lookup table.

The case that produced this module: sky130's OpenRAM SRAM macro is
characterised over `index_1("0.00125, 0.005, 0.04")` and carries
`max_transition : 0.04` on addr0/addr1/wmask0 — the same number, because
the "constraint" is just where characterisation stopped.

OpenROAD refuses to start against a limit that tight (RSZ-0090), which
is a feasibility precheck rather than a violation report: it aborts
before doing any work, whether or not a net violates. Relax the limit
and the flow completes with clean setup and hold — while the addr pins
sit at 0.3-0.9 ns, up to 22x past where the model stops.

So relaxing turns a loud abort into a quiet fiction, and the thing worth
testing is that the pipeline cannot report that as a pass.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import model_validity  # noqa: E402
from model_validity import (  # noqa: E402
    characterisation_ceiling, check, macro_ceilings, parse_slews, unverified,
)

# A real fragment of OpenSTA's report, as emitted for sram_wrapper.
CHECKS = """
======================= max_ss_100C_1v60 Corner ==========================

max slew

Pin                                        Limit        Slew       Slack
------------------------------------------------------------------------
u_sram/addr1[7]                         0.050000    0.880400   -0.830400 (VIOLATED)
u_sram/addr0[3]                         0.050000    0.566465   -0.516465 (VIOLATED)
clkbuf_0_clk/A                          0.750000    0.842607   -0.092607 (VIOLATED)
u_sram/addr0[7]                         0.050000    0.117658   -0.067658 (VIOLATED)

max fanout

Pin                                   Limit Fanout  Slack
---------------------------------------------------------
clkbuf_3_0_0_clk/X                       10     12     -2 (VIOLATED)
"""

LIB = """
library (macro) {
  time_unit : "1ns" ;
  pin (addr0) {
    direction : input ;
    max_transition : 0.04;
    timing () {
      cell_rise (tmpl) {
        index_1("0.00125, 0.005, 0.04");
        index_2("0.0017, 0.0069, 0.0276");
      }
    }
  }
}
"""


class CeilingTests(unittest.TestCase):
    def _lib(self, text):
        p = Path(tempfile.mkdtemp()) / "x.lib"
        p.write_text(text)
        return p

    def test_reads_the_top_of_the_slew_axis(self):
        self.assertEqual(characterisation_ceiling(self._lib(LIB)), 0.04)

    def test_takes_the_highest_across_many_tables(self):
        # A library's tables need not share one axis; what bounds it is
        # the furthest any of them was characterised.
        text = LIB + '\nindex_1("0.1, 0.5, 1.5");\n'
        self.assertEqual(characterisation_ceiling(self._lib(text)), 1.5)

    def test_a_library_without_an_index_is_none_not_zero(self):
        # Zero would mark every pin in the design extrapolated.
        self.assertIsNone(characterisation_ceiling(self._lib("library(x){}")))


class ParseTests(unittest.TestCase):
    def test_reads_the_violating_pins(self):
        got = parse_slews(CHECKS)
        self.assertEqual([r["pin"] for r in got],
                         ["u_sram/addr1[7]", "u_sram/addr0[3]",
                          "clkbuf_0_clk/A", "u_sram/addr0[7]"])

    def test_reads_limit_and_slew(self):
        got = parse_slews(CHECKS)[0]
        self.assertAlmostEqual(got["limit_ns"], 0.05)
        self.assertAlmostEqual(got["slew_ns"], 0.8804)

    def test_stops_before_the_fanout_table(self):
        # Both tables have a Pin column and a (VIOLATED) marker; running
        # past the boundary would read a fanout count as a slew.
        self.assertNotIn("clkbuf_3_0_0_clk/X",
                         [r["pin"] for r in parse_slews(CHECKS)])

    def test_a_report_with_no_slew_table_yields_nothing(self):
        self.assertEqual(parse_slews("nothing here"), [])


def _design(macro_lib_text=LIB, instances=("u_sram",)):
    d = Path(tempfile.mkdtemp())
    (d / "lib").mkdir()
    (d / "lib" / "m.lib").write_text(macro_lib_text)
    (d / "config.json").write_text(json.dumps({
        "MACROS": {"m": {
            "lib": {"*": ["dir::lib/m.lib"]},
            "instances": {i: {} for i in instances},
        }}
    }))
    return d


def _run(checks_text=CHECKS, corner="max_ss_100C_1v60"):
    r = Path(tempfile.mkdtemp())
    c = r / "54-openroad-stapostpnr" / corner
    c.mkdir(parents=True)
    (c / "checks.rpt").write_text(checks_text)
    return r


class CeilingLookupTests(unittest.TestCase):
    def test_maps_instances_not_macros(self):
        # The STA report names pins as <instance>/<pin>, so the lookup
        # has to be keyed the same way or nothing ever matches.
        self.assertEqual(macro_ceilings(_design(instances=("u_a", "u_b"))),
                         {"u_a": 0.04, "u_b": 0.04})

    def test_a_design_without_macros_has_no_ceilings(self):
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text("{}")
        self.assertEqual(macro_ceilings(d), {})


class CheckTests(unittest.TestCase):
    def test_flags_pins_past_the_ceiling(self):
        got = check(_design(), _run())
        pins = [p["pin"] for p in got["extrapolated_pins"]]
        self.assertIn("u_sram/addr1[7]", pins)
        self.assertEqual(got["worst_times_past_ceiling"], 22.0)

    def test_ignores_pins_that_are_not_the_macros(self):
        # clkbuf_0_clk/A violates its own 0.75 limit, but it is a
        # standard cell characterised to 1.5 ns — its number is a
        # measurement, and calling it extrapolated would be a false
        # alarm that trains people to ignore this check.
        got = check(_design(), _run())
        self.assertNotIn("clkbuf_0_clk/A",
                         [p["pin"] for p in got["extrapolated_pins"]])

    def test_a_pin_within_the_ceiling_is_not_flagged(self):
        text = CHECKS.replace("0.880400", "0.030000")
        got = check(_design(), _run(text))
        self.assertNotIn("u_sram/addr1[7]",
                         [p["pin"] for p in got["extrapolated_pins"]])

    def test_no_macro_is_none_rather_than_a_pass(self):
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text("{}")
        self.assertIsNone(check(d, _run()))

    def test_no_sta_report_is_none_rather_than_a_pass(self):
        # Absence of evidence reported as absence. A run that never
        # reached signoff has not been shown clean.
        self.assertIsNone(check(_design(), Path(tempfile.mkdtemp())))

    def test_keeps_the_worst_corner_for_a_pin(self):
        # The milder corner is deliberately the one read FIRST —
        # "max_ff..." sorts before "max_ss...". Written the other way
        # round this passes whether the code keeps the worst corner or
        # merely the first one it happens to see, which is no test at
        # all: the real report has nine corners and the pin must be
        # judged on the one that binds.
        r = _run(CHECKS.replace("0.880400", "0.145000"),
                 corner="max_ff_n40C_1v95")
        worse = r / "54-openroad-stapostpnr" / "max_ss_100C_1v60"
        worse.mkdir(parents=True)
        (worse / "checks.rpt").write_text(CHECKS)
        got = check(_design(), r)
        worst = got["extrapolated_pins"][0]
        self.assertEqual(worst["pin"], "u_sram/addr1[7]")
        self.assertAlmostEqual(worst["slew_ns"], 0.8804)
        self.assertEqual(worst["corner"], "max_ss_100C_1v60")


class VerdictTests(unittest.TestCase):
    def test_produces_an_unverified_line_not_a_violation(self):
        # The distinction is load-bearing. Nobody proved the design is
        # bad; they proved nobody can say from here.
        lines = unverified(check(_design(), _run()))
        self.assertEqual(len(lines), 1)
        self.assertIn("extrapolated", lines[0])
        self.assertIn("re-characterised", lines[0])

    def test_says_how_far_past_and_at_which_corner(self):
        line = unverified(check(_design(), _run()))[0]
        self.assertIn("22.0x", line)
        self.assertIn("max_ss_100C_1v60", line)

    def test_nothing_to_report_is_an_empty_list(self):
        self.assertEqual(unverified(None), [])
        text = CHECKS.replace("0.880400", "0.03").replace(
            "0.566465", "0.03").replace("0.117658", "0.03")
        self.assertEqual(unverified(check(_design(), _run(text))), [])


class RealDesignTests(unittest.TestCase):
    """Against the actual sram_wrapper config, not a fixture.

    Every fixture here was written by the same person who wrote the
    parser, so they share its blind spots. This one does not.
    """

    def test_reads_the_real_macro_ceiling(self):
        d = (Path(__file__).resolve().parent.parent
             / "pipeline" / "designs" / "sram_wrapper")
        if not (d / "config.json").is_file():
            self.skipTest("sram_wrapper not present")
        got = macro_ceilings(d)
        self.assertEqual(got, {"u_sram": 0.04}, got)


if __name__ == "__main__":
    unittest.main()
