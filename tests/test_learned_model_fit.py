"""Whether a learned model — RL or surrogate — can help sram_wrapper.

The question was whether RL or a surrogate could close the design that
has never closed. It is answerable from the store rather than from the
literature, and the answer is no, for two reasons that are measurements
and not opinions.

FIRST, THERE IS NO TRAINING DATA. Of 406 recorded configurations, three
are macro designs and none of them passed. A model learns the mapping
from configuration to outcome; here every macro sample carries the same
outcome, so there is nothing to learn a boundary from. This is not a
matter of choosing a better model.

SECOND, THE FAILURE IS NOT A SEARCH PROBLEM. sram_wrapper dies on
RSZ-0090 at a max_transition that recharacterise.py traces to OpenRAM's
characterisation grid: 0.04ns is where the macro's timing table stops,
not a limit placement can move. A model that predicted slew perfectly
would predict a violation of a limit that exists because nobody measured
above it. Predicting the number better does not create the measurement.

Where a learned model DOES have a claim in this repo is the cheaper
question — which candidates are worth running — and surrogate.py already
answers it, well for area and power and poorly for pass/fail. These
tests pin that split, because it is what decides whether more modelling
is worth anything.
"""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import surrogate  # noqa: E402


class TrainingDataTests(unittest.TestCase):
    """What the store could train anything on."""

    @classmethod
    def setUpClass(cls):
        cls.dataset = surrogate.load_dataset()

    def test_macro_designs_are_a_rounding_error_in_the_corpus(self):
        # Any learned approach to the macro problem trains on these.
        macro = [r for r in self.dataset
                 if (r.get("topology") or {}).get("has_macros")]
        self.assertLess(len(macro), 0.05 * len(self.dataset))

    def test_no_macro_configuration_has_ever_passed(self):
        # The reason the count matters. With one label present, a
        # classifier's best strategy is to predict that label always,
        # and it would be right every time and useless every time.
        macro = [r for r in self.dataset
                 if (r.get("topology") or {}).get("has_macros")]
        self.assertTrue(macro, "no macro samples at all")
        self.assertEqual(sum(1 for r in macro if r.get("passed")), 0)


class SurrogateStrengthTests(unittest.TestCase):
    """Where the existing model is strong, and where it is not."""

    @classmethod
    def setUpClass(cls):
        cls.dataset = surrogate.load_dataset()

    def test_it_predicts_continuous_ppa_well(self):
        # Area and power are what the model is actually good at, and the
        # numbers are the argument for keeping it.
        for field in ("area_um2", "power_w"):
            report = surrogate.evaluate(self.dataset, field=field)
            self.assertGreater(report["win_rate"], 0.9, field)

    def test_it_predicts_pass_or_fail_barely_better_than_chance(self):
        # The one a search would need. 65% of folds is not a gate you
        # would let skip a real run, and no amount of RL on top of a
        # predictor this weak changes what it is standing on.
        report = surrogate.evaluate(self.dataset, field="passed")
        self.assertLess(report["win_rate"], 0.75)

    def test_the_gap_between_them_is_the_finding(self):
        area = surrogate.evaluate(self.dataset, field="area_um2")["win_rate"]
        passed = surrogate.evaluate(self.dataset, field="passed")["win_rate"]
        self.assertGreater(area - passed, 0.2)


if __name__ == "__main__":
    unittest.main()
