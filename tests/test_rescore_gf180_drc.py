"""Tests for pipeline/rescore_gf180_drc.py — re-scoring stored verdicts.

The one thing that can go wrong is the thing that did go wrong on the
first trial: score() rebuilds `unverified` from signoff metrics only,
and the live path appends entries it learns elsewhere (an unconstrained
clock, a model whose validity is unknown). A re-score that drops those
promotes a candidate to a pass it never earned — cdc_twoclock's clk_b
entry vanished and the candidate passed. So the tests are about what
is kept, not only what is added.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import orchestrator  # noqa: E402
import rescore_gf180_drc as rs  # noqa: E402


def clean_metrics():
    m = {k: 0 for k, _ in orchestrator.SIGNOFF_METRICS}
    m["timing__setup__wns__corner:tt"] = 0.0
    del m["klayout__drc_error__count"]  # the one OpenLane never wrote on gf180
    return m


class RescoreTests(unittest.TestCase):
    def test_adds_the_klayout_count_and_can_pass(self):
        old = {"passed": False, "violations": [], "unverified": ["KLayout DRC error(s)"],
               "operating_point": {"fmax_mhz": 1.0}}
        new = rs.rescore_candidate({"verdict": old}, {"count": 0, "report": "x"}, {}, clean_metrics())
        self.assertTrue(new["passed"])
        self.assertEqual(new["unverified"], [])
        self.assertEqual(new["klayout_drc"]["count"], 0)
        # Keys score() does not produce survive.
        self.assertEqual(new["operating_point"], {"fmax_mhz": 1.0})

    def test_a_real_drc_count_is_a_violation(self):
        old = {"passed": False, "violations": [], "unverified": ["KLayout DRC error(s)"]}
        new = rs.rescore_candidate({"verdict": old}, {"count": 3}, {}, clean_metrics())
        self.assertFalse(new["passed"])
        self.assertIn("3 KLayout DRC error(s)", new["violations"])

    def test_non_signoff_unverified_entries_are_carried_and_still_block(self):
        clk = "timing for clock domain 'clk_b' (declared but never constrained, so no path in it was analysed)"
        old = {"passed": False, "violations": [], "unverified": ["KLayout DRC error(s)", clk]}
        new = rs.rescore_candidate({"verdict": old}, {"count": 0}, {}, clean_metrics())
        self.assertFalse(new["passed"])
        self.assertEqual(new["unverified"], [clk])

    def test_signoff_entries_are_not_carried_twice(self):
        # A signoff check that is still absent from metrics stays
        # unverified once, from score(), not again from the old list.
        m = clean_metrics()
        del m["magic__drc_error__count"]
        old = {"passed": False, "violations": [],
               "unverified": ["KLayout DRC error(s)", "Magic DRC error(s)"]}
        new = rs.rescore_candidate({"verdict": old}, {"count": 0}, {}, m)
        self.assertEqual(new["unverified"].count("Magic DRC error(s)"), 1)
        self.assertFalse(new["passed"])

    def test_only_gf180_candidates_missing_klayout_are_planned(self):
        self.assertTrue(rs.needs_rescoring(
            {"pdk": "gf180mcuD", "verdict": {"unverified": ["KLayout DRC error(s)"]}}))
        self.assertFalse(rs.needs_rescoring(
            {"pdk": "sky130A", "verdict": {"unverified": ["KLayout DRC error(s)"]}}))
        self.assertFalse(rs.needs_rescoring(
            {"pdk": "gf180mcuD", "verdict": {"unverified": []}}))


if __name__ == "__main__":
    unittest.main()
