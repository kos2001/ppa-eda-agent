"""Regression tests for pnr_study.py's pure logic: planning the one-factor
runs and judging the result against the noise floor.

The thing these guard is a particular dishonesty: reporting a knob as
"helping" when the flow's own run-to-run movement is as large as the effect.

    python3 -m unittest discover -s tests -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import pnr_study  # noqa: E402


def row(tag, role, knob=None, value=None, passed=True, **m):
    base = {"passed": passed, "area_um2": 1000.0, "power_w": 1e-3,
            "wirelength": 5000, "vias": 2000, "repair_buffers": 80,
            "clock_buffers": 8, "first_pass_drc": 50, "setup_ws": 2.0,
            "hold_ws": 0.1}
    base.update(m)
    r = {"tag": tag, "role": role, "m": base}
    if knob:
        r.update(knob=knob, value=value)
    return r


class TestPlan(unittest.TestCase):
    CONFIG = {"FP_CORE_UTIL": 40, "CLOCK_PERIOD": 10}

    def test_baseline_comes_first_and_alone_has_no_overrides(self):
        plan = pnr_study.plan(self.CONFIG)
        self.assertEqual(plan[0]["role"], "baseline")
        self.assertEqual(plan[0]["overrides"], {})

    def test_every_candidate_changes_exactly_one_thing(self):
        for c in pnr_study.plan(self.CONFIG)[1:]:
            self.assertEqual(len(c["overrides"]), 1, c["tag"])

    def test_probes_perturb_what_physically_should_not_matter(self):
        probes = [c for c in pnr_study.plan(self.CONFIG) if c["role"] == "probe"]
        self.assertEqual(sorted(c["overrides"].get("FP_CORE_UTIL", 0) for c in probes
                                if "FP_CORE_UTIL" in c["overrides"]), [39.0, 41.0])
        self.assertEqual(sorted(c["overrides"]["CLOCK_PERIOD"] for c in probes
                                if "CLOCK_PERIOD" in c["overrides"]), [9.9, 10.1])

    def test_a_value_equal_to_the_configs_own_is_not_an_experiment(self):
        cfg = {**self.CONFIG, "PL_TIME_DRIVEN": False}
        knobs = [c.get("knob") for c in pnr_study.plan(cfg)]
        self.assertNotIn("PL_TIME_DRIVEN", knobs)

    def test_relative_knobs_need_something_to_be_relative_to(self):
        """No FP_CORE_UTIL in the config means no density to offset from."""
        knobs = [c.get("knob") for c in pnr_study.plan({"CLOCK_PERIOD": 10})]
        self.assertNotIn("PL_TARGET_DENSITY_PCT", knobs)

    def test_tags_are_unique_and_directory_safe(self):
        tags = [c["tag"] for c in pnr_study.plan(self.CONFIG)]
        self.assertEqual(len(tags), len(set(tags)))
        for t in tags:
            self.assertRegex(t, r"^[A-Za-z0-9._-]+$")

    def test_every_knob_in_the_catalogue_exists_in_the_plan(self):
        planned = {c.get("knob") for c in pnr_study.plan(self.CONFIG)}
        for k in pnr_study.KNOBS:
            self.assertIn(k["knob"], planned)


class TestNoiseFloor(unittest.TestCase):
    def rows(self):
        return [
            row("b", "baseline"),
            row("p1", "probe", wirelength=5100),    # +2.0%
            row("p2", "probe", wirelength=4900),    # -2.0%
            row("p3", "probe", wirelength=5150),    # +3.0% <- the floor
        ]

    def test_floor_is_the_largest_probe_movement(self):
        self.assertAlmostEqual(pnr_study.noise_floor(self.rows(), "wirelength"), 3.0)

    def test_no_probes_means_no_floor_not_zero(self):
        self.assertIsNone(pnr_study.noise_floor([row("b", "baseline")], "area_um2"))

    def test_a_change_inside_the_floor_is_not_a_finding(self):
        rows = self.rows() + [row("k", "knob", "X", 1, wirelength=4850)]  # -3.0%
        entry = pnr_study.summarise(rows)["knobs"][0]
        self.assertNotIn("wirelength", entry["clears_noise"])

    def test_a_change_beyond_the_floor_in_the_right_direction_is(self):
        rows = self.rows() + [row("k", "knob", "X", 1, wirelength=4400)]  # -12%
        entry = pnr_study.summarise(rows)["knobs"][0]
        self.assertIn("wirelength", entry["clears_noise"])

    def test_the_wrong_direction_is_a_regression_not_a_finding(self):
        rows = self.rows() + [row("k", "knob", "X", 1, wirelength=5700)]  # +14%
        entry = pnr_study.summarise(rows)["knobs"][0]
        self.assertNotIn("wirelength", entry["clears_noise"])
        self.assertIn("wirelength", entry["regresses"])

    def test_slack_improves_upward(self):
        rows = self.rows() + [row("k", "knob", "X", 1, setup_ws=3.0)]  # +50%
        entry = pnr_study.summarise(rows)["knobs"][0]
        self.assertIn("setup_ws", entry["clears_noise"])

    def test_a_tiny_noise_floor_does_not_make_a_tiny_effect_material(self):
        """Probes that happen to land together (floor ~0.3%) must not turn
        a 1% shift into a result."""
        rows = [row("b", "baseline"),
                row("p1", "probe", area_um2=1003.0),
                row("p2", "probe", area_um2=997.0),
                row("k", "knob", "X", 1, area_um2=990.0)]  # -1.0%
        entry = pnr_study.summarise(rows)["knobs"][0]
        self.assertNotIn("area_um2", entry["clears_noise"])

    def test_an_improvement_that_fails_signoff_does_not_count(self):
        rows = self.rows() + [row("k", "knob", "X", 1, passed=False, wirelength=4000)]
        entry = pnr_study.summarise(rows)["knobs"][0]
        self.assertEqual(entry["clears_noise"], [])

    def test_summarise_needs_a_baseline(self):
        with self.assertRaises(ValueError):
            pnr_study.summarise([row("k", "knob", "X", 1)])


class TestConsistent(unittest.TestCase):
    def summary(self, area_change, regress=()):
        rows = [row("b", "baseline"),
                row("p1", "probe", area_um2=1005.0), row("p2", "probe", area_um2=995.0),
                row("k", "knob", "X", 1, area_um2=1000.0 * (1 + area_change / 100))]
        s = pnr_study.summarise(rows)
        s["knobs"][0]["regresses"] = list(regress)
        return s

    def test_one_design_is_a_lead_not_a_finding(self):
        self.assertEqual(pnr_study.consistent({"gcd": self.summary(-9.0)}), [])

    def test_two_designs_agreeing_is_a_finding(self):
        got = pnr_study.consistent({"gcd": self.summary(-9.0), "spm": self.summary(-8.5)})
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["knob"], "X")
        self.assertEqual(got[0]["metric"], "area_um2")

    def test_two_designs_disagreeing_is_not(self):
        self.assertEqual(pnr_study.consistent(
            {"gcd": self.summary(-9.0), "spm": self.summary(+9.0)}), [])

    def test_a_trade_is_reported_with_its_cost(self):
        got = pnr_study.consistent({"gcd": self.summary(-9.0, ["hold_ws"]),
                                    "spm": self.summary(-8.5, ["hold_ws"])})
        self.assertEqual(got[0]["also_regresses"], ["hold_ws"])

    def test_a_cost_on_only_one_design_is_not_claimed_for_both(self):
        got = pnr_study.consistent({"gcd": self.summary(-9.0, ["hold_ws"]),
                                    "spm": self.summary(-8.5)})
        self.assertEqual(got[0]["also_regresses"], [])


class TestExtract(unittest.TestCase):
    def test_a_missing_metric_is_none_not_zero(self):
        got = pnr_study.extract({"verdict": {"passed": True}}, {})
        self.assertIsNone(got["wirelength"])
        self.assertIsNone(got["setup_ws"])

    def test_slack_is_the_worst_corner(self):
        m = {"timing__setup__ws__corner:tt": 6.0, "timing__setup__ws__corner:ss": 3.0}
        self.assertEqual(pnr_study.extract({"verdict": {}}, m)["setup_ws"], 3.0)

    def test_percent_change_from_a_zero_baseline_is_not_defined(self):
        self.assertIsNone(pnr_study.pct_delta(5, 0))
        self.assertIsNone(pnr_study.pct_delta(None, 5))


if __name__ == "__main__":
    unittest.main()
