"""Regression tests for the OpenSTA report reader.

Same conventions as test_orchestrator_pure.py. These use real report text
copied verbatim from actual runs rather than invented fixtures, so a
format change in OpenLane's output fails here rather than silently
producing empty results.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import sta_report  # noqa: E402

# Verbatim from counter4's real max_ss_100C_1v60/max.rpt.
REAL_MAX_RPT = """
===========================================================================
report_checks -path_delay max (Setup)
============================================================================
======================= max_ss_100C_1v60 Corner ===================================

Startpoint: _20_ (rising edge-triggered flip-flop clocked by clk)
Endpoint: count[0] (output port clocked by clk)
Path Group: clk
Path Type: max

Fanout         Cap        Slew       Delay        Time   Description
---------------------------------------------------------------------------------------------
                                  0.000000    0.000000   clock clk (rise edge)
     1    0.009723    0.079263    0.051177    0.051177 ^ clk (in)
     2    0.017313    0.064099    0.221676    0.272854 ^ clkbuf_0_clk/X (sky130_fd_sc_hd__clkbuf_16)
     6    0.016266    0.253733    0.755894    1.225143 ^ _20_/Q (sky130_fd_sc_hd__dfxtp_1)
     1    0.034558    0.274946    0.427895    1.653849 ^ output3/X (sky130_fd_sc_hd__buf_2)
                                              1.655644   data arrival time

                                              7.750000   data required time
                                             -1.655644   data arrival time
                                              6.094356   slack (MET)
"""

# Verbatim from sram_wrapper's real 30-openroad-stamidpnr/checks.rpt.
REAL_CHECKS_RPT = """
===========================================================================
 report_check_types -max_slew -max_cap -max_fanout -violators
============================================================================
max slew

Pin                                        Limit        Slew       Slack
------------------------------------------------------------------------
u_sram/clk1                             0.750000    1.599854   -0.849854 (VIOLATED)
u_sram/addr0[0]                         0.040000    0.667010   -0.627010 (VIOLATED)

===========================================================================
max slew violation count 91
max fanout violation count 2
max cap violation count 1
============================================================================
"""


class TestParsePath(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rpt = Path(self.tmp.name) / "max.rpt"
        self.rpt.write_text(REAL_MAX_RPT)

    def test_arrival_comes_from_stas_own_line(self):
        """Real bug: the final port line of a path has no fanout/cap
        columns, so taking the last parsed stage under-reported arrival
        as 1.653849 where STA itself states 1.655644. Reporting a number
        STA did not print is wrong even at 1.8ps."""
        p = sta_report.parse_path(self.rpt)
        self.assertEqual(p["arrival_ns"], 1.655644)

    def test_worst_stage_is_identified_with_its_share(self):
        """The whole point of reading the path: 'WNS x' becomes 'this
        cell is where the time went'."""
        p = sta_report.parse_path(self.rpt)
        self.assertEqual(p["worst_stage"]["pin"], "_20_/Q")
        self.assertEqual(p["worst_stage"]["cell"], "sky130_fd_sc_hd__dfxtp_1")
        self.assertAlmostEqual(p["worst_stage"]["share_of_arrival"], 0.4565, places=3)

    def test_slack_and_endpoints(self):
        p = sta_report.parse_path(self.rpt)
        self.assertEqual(p["slack_ns"], 6.094356)
        self.assertTrue(p["met"])
        self.assertIn("_20_", p["startpoint"])
        self.assertIn("count[0]", p["endpoint"])

    def test_missing_report_is_none_not_a_crash(self):
        self.assertIsNone(sta_report.parse_path(Path("/nonexistent/max.rpt")))


class TestParseDrv(unittest.TestCase):
    def test_violation_counts_and_violators(self):
        """DRV counts are the same family of check that produces RSZ-0090,
        so having them as data rather than as a substring of an error
        message is what makes them reasonable about."""
        with tempfile.TemporaryDirectory() as tmp:
            rpt = Path(tmp) / "checks.rpt"
            rpt.write_text(REAL_CHECKS_RPT)
            d = sta_report.parse_drv(rpt)
        self.assertEqual(d["max_slew_violations"], 91)
        self.assertEqual(d["max_fanout_violations"], 2)
        self.assertEqual(d["max_cap_violations"], 1)
        self.assertTrue(any("u_sram/clk1" in l for l in d["violator_lines"]))


class TestReadRunRefusesToBeVacuous(unittest.TestCase):
    def test_a_step_with_no_reports_raises_rather_than_returning_empty(self):
        """Real bug this pins: mid-PnR STA writes reports flat in the step
        directory while pre/post-PnR use per-corner subdirectories. Only
        the latter was handled, so a run that failed before post-PnR
        returned an empty result silently. No corners must never read as
        no problems."""
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            (run / "30-openroad-stamidpnr").mkdir()
            with self.assertRaises(FileNotFoundError):
                sta_report.read_run(run)

    def test_flat_layout_is_read(self):
        """The layout a failed run actually leaves behind."""
        with tempfile.TemporaryDirectory() as tmp:
            run = Path(tmp)
            step = run / "30-openroad-stamidpnr"
            step.mkdir()
            (step / "max.rpt").write_text(REAL_MAX_RPT)
            (step / "checks.rpt").write_text(REAL_CHECKS_RPT)
            data = sta_report.read_run(run)
        self.assertIn("30-openroad-stamidpnr", data["corners"])
        corner = data["corners"]["30-openroad-stamidpnr"]
        self.assertEqual(corner["setup_path"]["arrival_ns"], 1.655644)
        self.assertEqual(corner["drv"]["max_slew_violations"], 91)


if __name__ == "__main__":
    unittest.main()
