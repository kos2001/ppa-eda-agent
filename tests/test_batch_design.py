"""Space-filling batch design (batch_design.py). Decision tests, no runs."""
import random
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import batch_design as bd
import orchestrator

SPACE = {
    "FP_CORE_UTIL": {"low": 30, "high": 60, "type": "int"},
    "CLOCK_PERIOD": {"low": 18.0, "high": 26.0, "type": "float", "digits": 2},
    "SYNTH_STRATEGY": {"values": ["AREA 0", "AREA 3", "DELAY 1"]},
}


class LatinHypercubeTests(unittest.TestCase):
    def test_every_stratum_used_once_per_dimension(self):
        n = 10
        pts = bd.latin_hypercube({"x": {"low": 0.0, "high": 1.0, "digits": 6}}, n, seed=3)
        strata = sorted(int(p["x"] * n) for p in pts)
        self.assertEqual(strata, list(range(n)))

    def test_values_stay_in_range_and_type(self):
        for p in bd.latin_hypercube(SPACE, 50, seed=1):
            self.assertIsInstance(p["FP_CORE_UTIL"], int)
            self.assertTrue(30 <= p["FP_CORE_UTIL"] <= 60)
            self.assertTrue(18.0 <= p["CLOCK_PERIOD"] <= 26.0)
            self.assertIn(p["SYNTH_STRATEGY"], SPACE["SYNTH_STRATEGY"]["values"])

    def test_seeded_and_deterministic(self):
        self.assertEqual(bd.latin_hypercube(SPACE, 8, 5), bd.latin_hypercube(SPACE, 8, 5))

    def test_bad_space_rejected(self):
        for space in ({}, {"x": {"low": 2, "high": 1}}, {"x": {"values": []}},
                      {"x": {"low": 0, "high": 1, "type": "str"}}):
            with self.subTest(space=space), self.assertRaises(ValueError):
                bd.latin_hypercube(space, 4)


class MaximinTests(unittest.TestCase):
    def test_spacing_beats_random_subsets_of_the_same_pool(self):
        pool = bd.latin_hypercube(SPACE, 200, seed=2)
        picked = bd.maximin_select(SPACE, pool, 6)
        rng = random.Random(0)
        randoms = [bd.min_spacing(SPACE, rng.sample(pool, 6)) for _ in range(200)]
        # Greedy maximin beats the median random batch drawn from the same pool.
        self.assertGreater(bd.min_spacing(SPACE, picked), sorted(randoms)[100])

    def test_never_repeats_and_moves_away_from_recorded(self):
        recorded = [{"FP_CORE_UTIL": 45, "CLOCK_PERIOD": 22.0, "SYNTH_STRATEGY": "AREA 3"}]
        pool = recorded + bd.latin_hypercube(SPACE, 100, seed=4)
        picked = bd.maximin_select(SPACE, pool, 5, recorded)
        self.assertNotIn(recorded[0], picked)
        self.assertEqual(len({str(sorted(p.items())) for p in picked}), 5)
        nearest = min(bd.distance(SPACE, p, recorded[0]) for p in picked)
        self.assertGreater(nearest, 0.3)

    def test_small_pool_returns_what_exists(self):
        self.assertEqual(len(bd.maximin_select(SPACE, bd.latin_hypercube(SPACE, 3), 10)), 3)


class OrchestratorHookTests(unittest.TestCase):
    def spec(self, **doe):
        return {"design_name": "nodesign",
                "sweeps": [{"param": "FP_CORE_UTIL", "values": [40], "tag_prefix": "u"}],
                "design_of_experiments": {"space": SPACE, "batch": 4, "pool": 60,
                                          "seed": 9, "tag_prefix": "doe", **doe}}

    def test_expand_sweeps_appends_doe_batch(self):
        with patch("surrogate.cached_dataset", return_value=[]):
            cands = orchestrator.expand_sweeps(self.spec())
        doe = [c for c in cands if c.get("origin") == "design_of_experiments"]
        self.assertEqual([c["tag"] for c in doe], ["doe-1", "doe-2", "doe-3", "doe-4"])
        self.assertTrue(all(" " not in c["tag"] for c in doe))

    def test_recorded_configs_from_store_are_excluded(self):
        with patch("surrogate.cached_dataset", return_value=[]):
            first = [c["overrides"] for c in orchestrator.expand_sweeps(self.spec())
                     if c.get("origin")]
        rows = [{"design": "nodesign", "overrides": o} for o in first]
        with patch("surrogate.cached_dataset", return_value=rows):
            second = [c["overrides"] for c in orchestrator.expand_sweeps(self.spec())
                      if c.get("origin")]
        self.assertFalse([o for o in second if o in first])

    def test_base_overrides_carry_into_every_candidate(self):
        with patch("surrogate.cached_dataset", return_value=[]):
            cands = orchestrator.expand_sweeps(self.spec(overrides={"DIE_AREA": "0 0 300 300"}))
        self.assertTrue(all(c["overrides"]["DIE_AREA"] == "0 0 300 300"
                            for c in cands if c.get("origin")))

    def test_invalid_batch_rejected(self):
        with patch("surrogate.cached_dataset", return_value=[]), self.assertRaises(ValueError):
            orchestrator.expand_sweeps(self.spec(batch=0))


if __name__ == "__main__":
    unittest.main()
