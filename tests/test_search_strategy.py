"""Tests for the retrospective comparison of candidate-search strategies.

The question was "would Monte Carlo improve performance?", and it cannot
be answered by argument because every word in it needs a number attached
first. This measures the one version of it that the store can settle:
given a budget of k real OpenLane runs out of a design's recorded
configuration space, does drawing them at random find a better
configuration than the sweep order the pipeline actually used?

It is retrospective and offline. Every configuration it "runs" was
really run — the numbers come from reference-db, not from a model — so
the comparison costs nothing and invents nothing. What it cannot do is
evaluate a configuration nobody tried, which is exactly the limit that
makes this a study of sample efficiency rather than of search.

The honest null result is a real outcome here. If random draws do no
better than the sweep, that is worth knowing before anyone rewrites
candidate generation around a sampler.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import search_strategy  # noqa: E402


def _rows(areas, design="d", scl="hd"):
    return [{"design": design, "scl": scl, "pdk": "p",
             "area_um2": a, "passed": True, "overrides": {"X": i}}
            for i, a in enumerate(areas)]


class ArmTests(unittest.TestCase):
    """A design and technology that were actually swept together."""

    def test_only_passing_candidates_with_an_area_are_usable(self):
        rows = _rows([10.0, 20.0])
        rows.append({"design": "d", "scl": "hd", "pdk": "p",
                     "area_um2": 1.0, "passed": False, "overrides": {}})
        rows.append({"design": "d", "scl": "hd", "pdk": "p",
                     "area_um2": None, "passed": True, "overrides": {}})
        arms = search_strategy.arms(rows, min_size=2)
        self.assertEqual(len(arms), 1)
        # The failing 1.0 is the smallest area present and must not
        # become the target: being too small is frequently why a
        # candidate failed, and a search graded against it would be
        # graded against something nobody achieved.
        self.assertEqual(arms[0]["best"], 10.0)
        self.assertEqual(len(arms[0]["areas"]), 2)

    def test_an_arm_holds_one_design_and_one_technology(self):
        rows = _rows([1.0, 2.0, 3.0]) + _rows([50.0, 60.0, 70.0], scl="hs")
        arms = search_strategy.arms(rows, min_size=3)
        self.assertEqual(len(arms), 2)
        for arm in arms:
            self.assertEqual(len({(a) for a in [arm["scl"]]}), 1)

    def test_a_space_too_small_to_search_is_dropped(self):
        # Comparing strategies over two configurations measures nothing:
        # any budget of two is the whole space.
        self.assertEqual(search_strategy.arms(_rows([1.0, 2.0]), min_size=5), [])


class RegretTests(unittest.TestCase):
    """How far the best-found sits from the best that existed."""

    def test_finding_the_best_is_zero_regret(self):
        self.assertEqual(search_strategy.regret([10.0, 30.0], best=10.0), 0.0)

    def test_regret_is_relative_to_the_best(self):
        # Percent, not absolute: an arm at 3618um2 and one at 290um2
        # cannot be averaged in um2 without the larger design deciding
        # the answer on its own.
        self.assertAlmostEqual(
            search_strategy.regret([11.0], best=10.0), 10.0)

    def test_an_empty_draw_is_total_regret_not_zero(self):
        # A budget that found nothing has not done perfectly.
        self.assertIsNone(search_strategy.regret([], best=10.0))


class SimulationTests(unittest.TestCase):

    def test_random_search_improves_as_the_budget_grows(self):
        arm = search_strategy.arms(_rows([1.0, 2.0, 3.0, 4.0, 5.0]),
                                   min_size=5)[0]
        small = search_strategy.random_regret(arm, budget=1, trials=500, seed=1)
        large = search_strategy.random_regret(arm, budget=4, trials=500, seed=1)
        self.assertGreater(small, large)

    def test_a_budget_of_the_whole_space_has_no_regret(self):
        arm = search_strategy.arms(_rows([1.0, 2.0, 3.0]), min_size=3)[0]
        self.assertEqual(
            search_strategy.random_regret(arm, budget=3, trials=50, seed=1), 0.0)

    def test_the_same_seed_gives_the_same_answer(self):
        # A comparison that moves between runs cannot settle anything.
        arm = search_strategy.arms(_rows([1.0, 5.0, 9.0, 13.0]), min_size=4)[0]
        a = search_strategy.random_regret(arm, budget=2, trials=200, seed=7)
        b = search_strategy.random_regret(arm, budget=2, trials=200, seed=7)
        self.assertEqual(a, b)

    def test_sweep_order_regret_uses_the_recorded_order(self):
        # The pipeline runs a sweep in the order the spec lists it, so
        # the first k of the recorded order is what a budget of k would
        # really have bought.
        arm = search_strategy.arms(_rows([9.0, 8.0, 1.0]), min_size=3)[0]
        self.assertAlmostEqual(
            search_strategy.sweep_regret(arm, budget=1), 800.0)
        self.assertAlmostEqual(
            search_strategy.sweep_regret(arm, budget=3), 0.0)


class RealStoreTests(unittest.TestCase):
    """Against the recorded runs, which is the only thing that can answer."""

    @classmethod
    def setUpClass(cls):
        cls.report = search_strategy.compare(trials=300, seed=20260830)

    def test_it_uses_real_arms_from_the_store(self):
        self.assertGreaterEqual(len(self.report["arms"]), 3)
        for arm in self.report["arms"]:
            self.assertGreaterEqual(arm["size"], search_strategy.MIN_ARM)

    def test_every_budget_reports_both_strategies(self):
        for row in self.report["budgets"]:
            self.assertIn("random_regret_pct", row)
            self.assertIn("sweep_regret_pct", row)
            self.assertGreaterEqual(row["random_regret_pct"], 0.0)

    def test_regret_falls_as_the_budget_grows(self):
        rates = [r["random_regret_pct"] for r in self.report["budgets"]]
        self.assertEqual(rates, sorted(rates, reverse=True))

    def test_the_verdict_states_which_won_and_by_how_much(self):
        v = self.report["verdict"]
        self.assertIn(v["winner"], ("random", "sweep", "tie"))
        # A margin is required so the answer cannot be "random won" on a
        # difference too small to act on.
        self.assertIn("margin_pct", v)


if __name__ == "__main__":
    unittest.main()
