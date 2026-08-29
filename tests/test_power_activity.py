"""Tests for pipeline/power_activity.py's pure parts.

The docker-run half needs a container and a completed run, so what is
tested here is what turns tool output into a claim — including the case
that made this worth building: a design with no testbench, where the only
correct answer is no number at all.

score() carried "no VCD/SAIF activity annotation configured in this
pipeline yet" as a documented caveat. It was closable with tools already
in the pinned image, and on spm the default estimate turned out to
understate combinational power by 27%.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from power_activity import (  # noqa: E402
    PowerActivityError, compare, find_netlist, find_testbench, measure,
    parse_power,
)

# A real OpenSTA report_power table, as emitted for spm.
REPORT = """
Group                  Internal  Switching    Leakage      Total
                          Power      Power      Power      Power (Watts)
----------------------------------------------------------------
Sequential             3.92e-04   3.22e-05   7.84e-10   4.24e-04  40.6%
Combinational          2.33e-04   1.06e-04   1.04e-09   3.39e-04  32.5%
Clock                  2.12e-04   6.93e-05   8.89e-10   2.81e-04  26.9%
Macro                  0.00e+00   0.00e+00   0.00e+00   0.00e+00   0.0%
Pad                    0.00e+00   0.00e+00   0.00e+00   0.00e+00   0.0%
----------------------------------------------------------------
Total                  8.37e-04   2.07e-04   2.72e-09   1.04e-03 100.0%
"""

ANNOTATED = REPORT.replace("3.39e-04", "4.31e-04").replace("1.04e-03", "1.13e-03")


class ParseTests(unittest.TestCase):
    def test_reads_every_group(self):
        got = parse_power(REPORT)
        self.assertEqual(sorted(got),
                         ["clock", "combinational", "macro", "pad",
                          "sequential", "total"])

    def test_reads_the_four_powers(self):
        seq = parse_power(REPORT)["sequential"]
        self.assertAlmostEqual(seq["internal_w"], 3.92e-04)
        self.assertAlmostEqual(seq["switching_w"], 3.22e-05)
        self.assertAlmostEqual(seq["leakage_w"], 7.84e-10)
        self.assertAlmostEqual(seq["total_w"], 4.24e-04)

    def test_a_zero_group_is_kept_not_dropped(self):
        # Macro at 0 W is a fact about spm — it has no macros. Dropping
        # it would make "no macro power" indistinguishable from "not
        # measured".
        self.assertEqual(parse_power(REPORT)["macro"]["total_w"], 0.0)

    def test_text_without_a_table_yields_nothing(self):
        self.assertEqual(parse_power("Annotated 1061 pin activities."), {})


class CompareTests(unittest.TestCase):
    def test_reports_the_real_measured_change(self):
        got = compare(parse_power(REPORT), parse_power(ANNOTATED))
        self.assertAlmostEqual(got["combinational"]["change_pct"], 27.1, places=1)
        self.assertAlmostEqual(got["total"]["change_pct"], 8.7, places=1)

    def test_unchanged_groups_report_zero_not_absence(self):
        # Clock power does not move under annotation — it is clock-driven,
        # so its activity was never in doubt. That is a result.
        got = compare(parse_power(REPORT), parse_power(ANNOTATED))
        self.assertEqual(got["clock"]["change_pct"], 0.0)

    def test_skips_groups_that_would_divide_by_zero(self):
        got = compare(parse_power(REPORT), parse_power(ANNOTATED))
        self.assertNotIn("macro", got)
        self.assertNotIn("pad", got)

    def test_missing_side_is_skipped_rather_than_guessed(self):
        self.assertEqual(compare(parse_power(REPORT), {}), {})


class DiscoveryTests(unittest.TestCase):
    def test_no_testbench_is_none_not_an_error(self):
        # Most designs here have none, and that is not a failure.
        d = Path(tempfile.mkdtemp())
        self.assertIsNone(find_testbench(d))

    def test_finds_a_testbench_where_one_exists(self):
        d = Path(tempfile.mkdtemp())
        (d / "verify").mkdir()
        tb = d / "verify" / "thing_tb.v"
        tb.write_text("module thing_tb; endmodule")
        self.assertEqual(find_testbench(d), tb)

    def test_measure_returns_none_without_a_testbench(self):
        # The load-bearing refusal: a vectorless figure relabelled as
        # measured would be worse than no figure, so nothing is returned
        # and no container is started.
        self.assertIsNone(measure(Path(tempfile.mkdtemp()),
                                  Path(tempfile.mkdtemp())))

    def test_a_run_without_signoff_raises(self):
        d = Path(tempfile.mkdtemp())
        with self.assertRaises(PowerActivityError):
            find_netlist(d)

    def test_finds_the_final_netlist(self):
        d = Path(tempfile.mkdtemp())
        nl = d / "final" / "nl"
        nl.mkdir(parents=True)
        f = nl / "spm.nl.v"
        f.write_text("module spm; endmodule")
        self.assertEqual(find_netlist(d), f)


class RealDesignTests(unittest.TestCase):
    def test_spm_is_the_design_that_has_a_testbench(self):
        designs = Path(__file__).resolve().parent.parent / "pipeline" / "designs"
        if not designs.is_dir():
            self.skipTest("no designs")
        with_tb = [d.name for d in sorted(designs.iterdir())
                   if d.is_dir() and find_testbench(d)]
        self.assertIn("spm", with_tb, with_tb)


if __name__ == "__main__":
    unittest.main()
