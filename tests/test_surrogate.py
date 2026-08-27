"""Tests for pipeline/surrogate.py.

The load-bearing claim of this module is a refusal: reference-db does not
hold enough distinct configurations to train or evaluate a surrogate. A
refusal is only worth anything if the machinery would have worked on real
data — otherwise "insufficient data" is indistinguishable from a broken
predictor. So these tests prove both directions: it refuses on what we
actually have, and it predicts and beats the baseline on a dataset that
genuinely carries signal.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from surrogate import (  # noqa: E402
    MIN_SAMPLES, dataset_report, distance, evaluate, featurize, load_dataset,
    predict,
)


def row(design, ov, area=None, completed=True):
    return {"design": design, "overrides": ov, "completed": completed,
            "passed": completed, "area_um2": area, "utilization": None,
            "stage": None}


def linear_dataset(n=14, design="synth"):
    """area = 100 + 2*util — a relationship a working predictor must find."""
    return [row(design, {"FP_CORE_UTIL": u}, 100.0 + 2.0 * u)
            for u in range(20, 20 + n)]


class FeatureTests(unittest.TestCase):
    def test_die_area_becomes_an_area(self):
        f = featurize(row("d", {"DIE_AREA": [0, 0, 64, 64]}))
        self.assertEqual(f["die_area_um2"], 4096)

    def test_missing_value_stays_missing_not_zero(self):
        # Zero would place an unspecified parameter at one end of its own
        # range and drag every distance toward it.
        f = featurize(row("d", {}))
        self.assertIsNone(f["FP_CORE_UTIL"])
        self.assertIsNone(f["die_area_um2"])

    def test_strategy_is_categorical(self):
        f = featurize(row("d", {"SYNTH_STRATEGY": "DELAY 3"}))
        self.assertEqual(f["SYNTH_STRATEGY"], "DELAY 3")

    def test_malformed_die_area_does_not_crash(self):
        self.assertIsNone(featurize(row("d", {"DIE_AREA": [0, 0]}))["die_area_um2"])


class DistanceTests(unittest.TestCase):
    def setUp(self):
        self.rows = linear_dataset()
        from surrogate import _ranges
        self.ranges = _ranges(self.rows)

    def test_identical_configs_are_zero_apart(self):
        a = row("synth", {"FP_CORE_UTIL": 25})
        self.assertEqual(distance(a, a, self.ranges), 0.0)

    def test_distance_grows_with_difference(self):
        a = row("synth", {"FP_CORE_UTIL": 20})
        near = row("synth", {"FP_CORE_UTIL": 22})
        far = row("synth", {"FP_CORE_UTIL": 33})
        self.assertLess(distance(a, near, self.ranges), distance(a, far, self.ranges))

    def test_no_comparable_feature_is_none_not_zero(self):
        # Zero would make every incomparable pair the nearest neighbour.
        a = row("d", {})
        b = row("d", {})
        self.assertIsNone(distance(a, b, {}))

    def test_differing_strategy_adds_distance(self):
        rows = [row("s", {"SYNTH_STRATEGY": x}) for x in ("AREA 0", "DELAY 3")]
        self.assertEqual(distance(rows[0], rows[1], {}), 1.0)


class RefusalTests(unittest.TestCase):
    """What the module is really for."""

    def test_refuses_below_the_threshold(self):
        data = linear_dataset(n=MIN_SAMPLES - 1)
        got = predict(row("synth", {"FP_CORE_UTIL": 25}), data)
        self.assertTrue(got["refused"])
        self.assertIn("need at least", got["reason"])

    def test_refusal_names_the_design_and_the_count(self):
        data = linear_dataset(n=2)
        got = predict(row("synth", {"FP_CORE_UTIL": 25}), data)
        self.assertIn("synth", got["reason"])
        self.assertEqual(got["n_samples"], 2)

    def test_never_borrows_neighbours_from_another_design(self):
        # A counter's area says nothing about an SRAM wrapper's. Mixing
        # designs is how a surrogate starts producing confident nonsense.
        data = linear_dataset(n=20, design="counter")
        got = predict(row("sram", {"FP_CORE_UTIL": 25}), data)
        self.assertTrue(got["refused"])
        self.assertEqual(got["n_samples"], 0)

    def test_the_real_reference_db_is_reported_as_insufficient(self):
        refdb = Path(__file__).resolve().parent.parent / "reference-db"
        if not (refdb / "cases").is_dir():
            self.skipTest("no reference-db")
        report = dataset_report(load_dataset(refdb))
        result = evaluate(load_dataset(refdb))
        # If this ever starts passing, the dataset grew and the honest
        # answer changed — which is the point of keeping it measured.
        self.assertEqual(report["trainable"], [], report)
        self.assertIn("insufficient data", result["verdict"])


class WorkingPredictorTests(unittest.TestCase):
    """A refusal only means something if the machinery works when fed."""

    def test_predicts_close_to_truth_on_real_signal(self):
        data = linear_dataset(n=20)
        got = predict(row("synth", {"FP_CORE_UTIL": 25}), data)
        self.assertFalse(got["refused"], got)
        self.assertAlmostEqual(got["value"], 150.0, delta=6.0)

    def test_exact_match_returns_the_observed_value(self):
        data = linear_dataset(n=20)
        got = predict(row("synth", {"FP_CORE_UTIL": 30}), data)
        self.assertTrue(got["exact_match"])
        self.assertEqual(got["value"], 160.0)

    def test_beats_the_mean_baseline_when_signal_exists(self):
        got = evaluate(linear_dataset(n=20))
        self.assertTrue(got["beats_baseline"], got)
        self.assertLess(got["model_mae"], got["baseline_mae"])
        self.assertEqual(got["verdict"], "useful — beats predicting the mean")

    def test_reports_no_better_than_mean_when_target_is_flat(self):
        # counter4's real behaviour: area does not move with
        # FP_CORE_UTIL. A model must say so rather than claim skill.
        flat = [row("f", {"FP_CORE_UTIL": u}, 290.278) for u in range(20, 40)]
        got = evaluate(flat)
        self.assertFalse(got["beats_baseline"])
        self.assertIn("no better", got["verdict"])

    def test_evaluation_reports_its_sample_count(self):
        got = evaluate(linear_dataset(n=20))
        self.assertEqual(got["n_total"], 20)
        self.assertGreater(got["n_scored"], 0)


class DatasetTests(unittest.TestCase):
    def test_deduplicates_identical_configs(self):
        # The raw cases hold ~3x duplicates; counting them as independent
        # samples would inflate any accuracy figure.
        import json, tempfile
        d = Path(tempfile.mkdtemp())
        (d / "cases").mkdir()
        case = {"design": "x", "iterations": [{"iteration": 1, "results": [
            {"tag": "a", "overrides": {"FP_CORE_UTIL": 35},
             "verdict": {"passed": True, "area_um2": 10}},
            {"tag": "b", "overrides": {"FP_CORE_UTIL": 35},
             "verdict": {"passed": True, "area_um2": 10}},
        ]}]}
        (d / "cases" / "x__1.json").write_text(json.dumps(case))
        self.assertEqual(len(load_dataset(d)), 1)

    def test_missing_reference_db_raises(self):
        from surrogate import SurrogateError
        with self.assertRaises(SurrogateError):
            load_dataset(Path("/nonexistent/refdb"))


if __name__ == "__main__":
    unittest.main()
