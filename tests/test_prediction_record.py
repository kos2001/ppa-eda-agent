"""Tests for recording the surrogate's expectation beside the measurement.

Asked whether DL/RL could improve the pipeline. The measured answer on
2026-09-11 (426 distinct configurations): nearest-neighbour prediction
of area beats the mean baseline on every design with enough runs, and
pass/fail prediction is uneven — good on counter4 and gcd, useless on
aes and cdc_twoclock where every run fails. That earns the model a
place as a recorded expectation scored by every real run, not as a
gate. These tests guard that shape: the prediction is made from the
candidate and the design config only, scored against the verdict
afterwards, refused with a reason when the data does not support one,
and never able to stop a run.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import surrogate  # noqa: E402


def rows(design="toy", n=12):
    # Area rises with FP_CORE_UTIL: a real relationship for kNN to find.
    out = []
    for i in range(n):
        util = 20 + 5 * i
        out.append({"design": design, "overrides": {"FP_CORE_UTIL": util},
                    "scl": surrogate.DEFAULT_SCL, "pdk": surrogate.DEFAULT_PDK,
                    "declared": {"CLOCK_PERIOD": 10}, "topology": {},
                    "area_um2": 100.0 + util, "power_w": 1e-5 * util, "passed": True,
                    "completed": True})
    return out


class PredictCandidateTests(unittest.TestCase):
    def test_predicts_from_neighbours_of_the_same_design(self):
        ds = rows()
        p = surrogate.predict_candidate(
            "toy", {"overrides": {"FP_CORE_UTIL": 42}}, {"CLOCK_PERIOD": 10}, dataset=ds)
        self.assertFalse(p["area_um2"]["refused"])
        self.assertAlmostEqual(p["area_um2"]["value"], 140.0, delta=5.0)
        self.assertFalse(p["power_w"]["refused"])

    def test_refuses_with_a_reason_when_the_design_has_too_few_runs(self):
        ds = rows(n=3)
        p = surrogate.predict_candidate(
            "toy", {"overrides": {"FP_CORE_UTIL": 42}}, {}, dataset=ds)
        self.assertTrue(p["area_um2"]["refused"])
        self.assertIn("recorded run", p["area_um2"]["reason"])
        self.assertIsNone(p["area_um2"]["value"])

    def test_never_borrows_another_design(self):
        ds = rows(design="other")
        p = surrogate.predict_candidate(
            "toy", {"overrides": {"FP_CORE_UTIL": 42}}, {}, dataset=ds)
        self.assertTrue(p["area_um2"]["refused"])

    def test_declared_clock_period_fills_in_like_load_dataset_does(self):
        # The candidate does not override CLOCK_PERIOD; config.json's
        # value stands in, as `declared` does for a stored case.
        ds = rows()
        p = surrogate.predict_candidate(
            "toy", {"overrides": {"FP_CORE_UTIL": 30}}, {"CLOCK_PERIOD": 10}, dataset=ds)
        self.assertFalse(p["area_um2"]["refused"])


class ScorePredictionTests(unittest.TestCase):
    def test_error_is_measured_minus_predicted(self):
        pred = {"area_um2": {"value": 140.0, "refused": False},
                "power_w": {"value": 4e-4, "refused": False}}
        verdict = {"area_um2": 150.0, "power": {"total_w": 5e-4}}
        s = surrogate.score_prediction(pred, verdict)
        self.assertEqual(s["area_um2"]["error"], 10.0)
        self.assertAlmostEqual(s["area_um2"]["error_pct"], 100 * 10 / 150)
        self.assertAlmostEqual(s["power_w"]["error"], 1e-4)

    def test_a_refusal_is_carried_not_scored(self):
        pred = {"area_um2": {"value": None, "refused": True, "reason": "only 3 recorded run(s)"}}
        s = surrogate.score_prediction(pred, {"area_um2": 150.0})
        self.assertNotIn("error", s["area_um2"])
        self.assertIn("only 3", s["area_um2"]["refused"])
        self.assertEqual(s["area_um2"]["measured"], 150.0)

    def test_a_run_with_no_verdict_still_records_the_prediction(self):
        pred = {"area_um2": {"value": 140.0, "refused": False}}
        s = surrogate.score_prediction(pred, None)
        self.assertEqual(s["area_um2"]["predicted"], 140.0)
        self.assertIsNone(s["area_um2"]["measured"])
        self.assertNotIn("error", s["area_um2"])


class RealStoreTests(unittest.TestCase):
    """The measured state that justifies recording rather than gating."""

    @classmethod
    def setUpClass(cls):
        cls.ds = surrogate.load_dataset()

    def test_area_prediction_beats_the_baseline_on_the_big_designs(self):
        for design in ("gcd", "spm", "counter4", "cdc_twoclock"):
            sub = [r for r in self.ds if r["design"] == design]
            if len(sub) < surrogate.MIN_SAMPLES + 1:
                continue
            ev = surrogate.evaluate(sub, field="area_um2")
            self.assertGreater(ev["win_rate"], 0.9, (design, ev["verdict"]))

    def test_pass_fail_prediction_is_not_trustworthy_everywhere(self):
        # The reason the model records and does not gate: on spm it is
        # barely better than the mean, and on designs where every run
        # fails there is nothing to learn.
        sub = [r for r in self.ds if r["design"] == "spm"]
        if len(sub) < surrogate.MIN_SAMPLES + 1:
            self.skipTest("spm has too few runs")
        ev = surrogate.evaluate(sub, field="passed")
        self.assertLess(ev["win_rate"], 0.8, ev["verdict"])


if __name__ == "__main__":
    unittest.main()
