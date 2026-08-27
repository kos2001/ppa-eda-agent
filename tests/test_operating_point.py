"""Tests for pipeline/operating_point.py — Fmax and Vmin.

The bug this closes is a read, not a calculation. score() used
`timing__setup__wns`, worst *negative* slack, which OpenSTA floors at 0:
a counter with 6.85 ns of margin and one with 0.01 ns both report
exactly 0. The margin was in `timing__setup__ws`, which nothing read.

So the tests care most that positive margin survives, that Fmax is taken
from the worst corner rather than the best, and that neither number is
invented when the inputs to it are missing.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from operating_point import (  # noqa: E402
    corner_timing, corner_voltage, operating_point,
)

# Real values from a counter4_tinydie signoff at a 10 ns constraint.
REAL = {
    "timing__setup__ws__corner:max_ff_n40C_1v95": 7.1445,
    "timing__hold__ws__corner:max_ff_n40C_1v95": 0.1402,
    "timing__setup__ws__corner:max_ss_100C_1v60": 6.0731,
    "timing__hold__ws__corner:max_ss_100C_1v60": 0.8991,
    "timing__setup__ws__corner:max_tt_025C_1v80": 6.8456,
    "timing__hold__ws__corner:max_tt_025C_1v80": 0.3512,
    # The clamped metric that hid all of the above.
    "timing__setup__wns": 0,
}


class CornerVoltageTests(unittest.TestCase):
    def test_reads_voltage_from_the_corner_name(self):
        self.assertEqual(corner_voltage("nom_ss_100C_1v60"), 1.60)
        self.assertEqual(corner_voltage("max_ff_n40C_1v95"), 1.95)
        self.assertEqual(corner_voltage("min_tt_025C_1v80"), 1.80)

    def test_unnamed_voltage_is_none_not_a_default(self):
        self.assertIsNone(corner_voltage("some_corner"))


class CornerTimingTests(unittest.TestCase):
    def test_positive_margin_survives(self):
        # The whole point: wns would have reported 0 for every one.
        got = {c["corner"]: c["setup_ws_ns"] for c in corner_timing(REAL)}
        self.assertAlmostEqual(got["max_tt_025C_1v80"], 6.8456)

    def test_one_entry_per_corner(self):
        self.assertEqual(len(corner_timing(REAL)), 3)

    def test_setup_and_hold_are_judged_separately(self):
        m = dict(REAL)
        m["timing__setup__ws__corner:bad_tt_025C_1v80"] = 5.0
        m["timing__hold__ws__corner:bad_tt_025C_1v80"] = -0.02
        bad = next(c for c in corner_timing(m) if c["corner"].startswith("bad"))
        self.assertTrue(bad["setup_ok"])
        self.assertFalse(bad["hold_ok"])

    def test_no_corner_metrics_yields_nothing(self):
        self.assertEqual(corner_timing({}), [])


class OperatingPointTests(unittest.TestCase):
    def test_fmax_is_taken_from_the_worst_corner(self):
        # The part has to work everywhere it is specified to work, so
        # signoff Fmax is the slow corner, not the fast one.
        op = operating_point(REAL, 10.0)
        self.assertEqual(op["fmax_limiting_corner"], "max_ss_100C_1v60")
        self.assertAlmostEqual(op["fmax_mhz"], 1000 / (10.0 - 6.0731), places=1)

    def test_fmax_beats_the_constrained_frequency(self):
        # 100 MHz constrained, ~255 MHz achievable — the number that was
        # being discarded.
        op = operating_point(REAL, 10.0)
        self.assertGreater(op["fmax_mhz"], 250)

    def test_vmin_is_the_lowest_passing_corner(self):
        self.assertEqual(operating_point(REAL, 10.0)["vmin_v"], 1.60)

    def test_vmin_flags_when_it_is_only_the_floor_of_what_was_analysed(self):
        # Every corner passing means Vmin is bounded by the PDK's corner
        # set, not by the design. Claiming 1.6 V flatly would assert a
        # sweep nobody ran.
        self.assertTrue(operating_point(REAL, 10.0)["vmin_is_lowest_analysed"])

    def test_a_failing_low_corner_raises_vmin(self):
        m = dict(REAL)
        m["timing__setup__ws__corner:max_ss_100C_1v60"] = -0.4
        op = operating_point(m, 10.0)
        self.assertEqual(op["vmin_v"], 1.80)
        self.assertIn("max_ss_100C_1v60", op["failing_corners"])
        self.assertFalse(op["vmin_is_lowest_analysed"])

    def test_hold_failure_disqualifies_a_corner_entirely(self):
        # Hold does not improve by slowing the clock, so such a corner is
        # unusable at any frequency.
        m = dict(REAL)
        m["timing__hold__ws__corner:max_ss_100C_1v60"] = -0.05
        op = operating_point(m, 10.0)
        self.assertEqual(op["vmin_v"], 1.80)
        self.assertNotEqual(op["fmax_limiting_corner"], "max_ss_100C_1v60")

    def test_no_period_means_no_fmax_but_still_a_vmin(self):
        # A fabricated period would produce a frequency that looks
        # exactly like a measured one.
        op = operating_point(REAL, None)
        self.assertIsNone(op["fmax_mhz"])
        self.assertEqual(op["vmin_v"], 1.60)

    def test_returns_none_when_the_run_has_no_corner_timing(self):
        self.assertIsNone(operating_point({}, 10.0))

    def test_states_its_own_limits(self):
        note = operating_point(REAL, 10.0)["note"]
        self.assertIn("not a prediction", note.replace("  ", " "))
        self.assertIn("lowest characterised corner", note)


if __name__ == "__main__":
    unittest.main()
