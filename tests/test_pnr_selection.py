"""Regression tests for how a winner is chosen among passing candidates.

Three defects in the old selection, each measured on reference-db before
it was changed:

  * The timing-margin objective was -worst_setup_wns. OpenSTA clips WNS at
    zero, so it was 0 for all 159 passing candidates in the store: a
    constant, discriminating nothing.
  * Nothing saw the floorplan. Instance area is the same 290.278 um^2 at
    FP_CORE_UTIL 25 and 35 on counter4, so the most-swept placement knob
    could not influence which candidate won.
  * Ties inside the Pareto front were broken by crowding distance, which
    gives every extreme point infinite distance. With two or more
    objectives that returns whichever extreme is first in the list - the
    sweep order, not a judgement. 10 of the 23 recorded iterations with
    several passers had a front of more than one member.

    python3 -m unittest discover -s tests -v
"""
import os
import random
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import orchestrator  # noqa: E402
from pareto import ParetoPoint, pick_best, pick_knee  # noqa: E402


def pt(key, *objs):
    return ParetoPoint(key=key, objs=tuple(objs))


class TestPickKnee(unittest.TestCase):
    def test_empty_and_single(self):
        self.assertIsNone(pick_knee([]))
        self.assertEqual(pick_knee([pt("only", 1, 2)]), "only")

    def test_a_dominated_point_never_wins(self):
        pts = [pt("good", 1, 1), pt("bad", 2, 2)]
        self.assertEqual(pick_knee(pts), "good")
        self.assertEqual(pick_knee(pts[::-1]), "good")

    def test_the_compromise_beats_both_extremes(self):
        """A is best on x, B best on y, C is nearly as good as either on
        both. pick_best() returns an extreme (whichever is listed first);
        the knee is C."""
        a, b, c = pt("A", 0, 10), pt("B", 10, 0), pt("C", 3, 3)
        self.assertEqual(pick_knee([a, b, c]), "C")
        self.assertIn(pick_best([a, b, c]), ("A", "B"))

    def test_the_answer_does_not_depend_on_list_order(self):
        base = [pt("A", 0, 10), pt("B", 10, 0), pt("C", 3, 3), pt("D", 6, 5)]
        rng = random.Random(7)
        for _ in range(20):
            shuffled = base[:]
            rng.shuffle(shuffled)
            self.assertEqual(pick_knee(shuffled), "C")

    def test_a_constant_objective_cannot_move_the_result(self):
        """The old margin objective was constant 0. A degenerate axis has
        to be inert, not a source of noise."""
        two = [pt("A", 0, 10), pt("B", 10, 0), pt("C", 3, 3)]
        three = [pt(p.key, *p.objs, 0.0) for p in two]
        self.assertEqual(pick_knee(three), pick_knee(two))

    def test_exact_ties_go_to_the_earlier_point(self):
        pts = [pt("first", 1, 5), pt("second", 5, 1)]
        self.assertEqual(pick_knee(pts), "first")


class TestPickWinnerObjectives(unittest.TestCase):
    def _passing(self, tag, area=290.0, util=0.5, power=1e-4, slack=None,
                 core=None, corners=None):
        v = {"passed": True, "area_um2": area, "utilization": util,
             "worst_setup_wns": 0.0, "power": {"total_w": power}}
        if slack is not None:
            v["worst_setup_slack"] = slack
        if core is not None:
            v["core_area_um2"] = core
        if corners is not None:
            v["operating_point"] = {"corners": corners}
        return {"tag": tag, "overrides": {}, "verdict": v}

    def test_a_smaller_core_wins_at_equal_cells_and_power(self):
        """counter4__2026-08-21: util 25 and 35 gave the same 290 um^2 of
        cells and the same power, in a 631 um^2 and a 480 um^2 core. The
        old ranking could not tell them apart."""
        loose = self._passing("util25", util=0.46)
        tight = self._passing("util35", util=0.604)
        for order in ([loose, tight], [tight, loose]):
            self.assertEqual(orchestrator.pick_winner(order)["tag"], "util35")

    def test_recorded_core_area_beats_the_derived_one(self):
        a = self._passing("a", util=0.5, core=900.0)
        b = self._passing("b", util=0.5, core=500.0)
        self.assertEqual(orchestrator.pick_winner([a, b])["tag"], "b")

    def test_more_setup_slack_wins_when_nothing_else_differs(self):
        """worst_setup_wns is 0 for every passer, so margin used to be
        invisible; the real slack is what separates these two."""
        tight = self._passing("tight", slack=0.3)
        roomy = self._passing("roomy", slack=2.0)
        for order in ([tight, roomy], [roomy, tight]):
            self.assertEqual(orchestrator.pick_winner(order)["tag"], "roomy")

    def test_slack_falls_back_to_a_stored_operating_point(self):
        corners = lambda a, b: [{"setup_ws_ns": a}, {"setup_ws_ns": b}]
        tight = self._passing("tight", corners=corners(4.0, 0.3))
        roomy = self._passing("roomy", corners=corners(5.0, 2.0))
        self.assertEqual(orchestrator.pick_winner([tight, roomy])["tag"], "roomy")

    def test_a_missing_objective_is_dropped_not_read_as_zero(self):
        """One passer with no slack must not win slack by reading as 0.0
        (or lose it by reading as huge): the objective is dropped for all.
        Here only area separates them, and 'b' is smaller."""
        a = self._passing("a", area=300.0, slack=5.0)
        b = self._passing("b", area=280.0)  # no slack recorded
        self.assertEqual(orchestrator.pick_winner([a, b])["tag"], "b")

    def test_old_fixtures_with_neither_new_field_still_rank_on_area_and_power(self):
        a = {"tag": "a", "overrides": {}, "verdict": {
            "passed": True, "area_um2": 100.0, "worst_setup_wns": 0.0,
            "power": {"total_w": 1e-3}}}
        b = {"tag": "b", "overrides": {}, "verdict": {
            "passed": True, "area_um2": 200.0, "worst_setup_wns": 0.0,
            "power": {"total_w": 2e-3}}}
        self.assertEqual(orchestrator.pick_winner([b, a])["tag"], "a")


class TestScoreRecordsPlacementQuality(unittest.TestCase):
    def _metrics(self, **over):
        base = {k: 0 for k, _ in orchestrator.SIGNOFF_METRICS}
        base.update(over)
        return base

    def test_the_real_slack_is_recorded_alongside_the_clipped_wns(self):
        m = self._metrics(**{
            "timing__setup__wns__corner:tt": 0.0,
            "timing__setup__ws__corner:tt": 6.85,
            "timing__setup__ws__corner:ss": 3.15,
            "design__core__area": 6643.87,
            "route__wirelength": 6555, "route__vias": 2119})
        v = orchestrator.score(m, {})
        self.assertEqual(v["worst_setup_wns"], 0.0)
        self.assertEqual(v["worst_setup_slack"], 3.15)
        self.assertEqual(v["core_area_um2"], 6643.87)
        self.assertEqual(v["wirelength_um"], 6555)
        self.assertEqual(v["via_count"], 2119)

    def test_no_slack_metric_means_none_not_zero(self):
        v = orchestrator.score(self._metrics(), {})
        self.assertIsNone(v["worst_setup_slack"])

    def test_design_level_slack_is_the_fallback_without_corners(self):
        v = orchestrator.score(self._metrics(**{"timing__setup__ws": 1.5}), {})
        self.assertEqual(v["worst_setup_slack"], 1.5)

    def test_derived_core_area_matches_the_measured_one(self):
        """The retrospective path rebuilds core area as cells / utilization.
        Checked on a real gcd run: 3004.13 / 0.452166 = 6643.9, against
        design__core__area 6643.87."""
        derived = orchestrator._core_area({"area_um2": 3004.13, "utilization": 0.452166})
        self.assertAlmostEqual(derived, 6643.87, delta=0.5)


if __name__ == "__main__":
    unittest.main()
