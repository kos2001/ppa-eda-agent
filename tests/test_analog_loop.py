"""Tests for pipeline/analog_loop.py — the custom half's closed loop.

The rules under test are the ones that make a verdict trustworthy rather
than optimistic, and they are the same two orchestrator.score() holds:
the worst corner governs, and a target with no measurement behind it is
unverified rather than passed. ngspice exits 0 when a `.meas` fails, so
"the number is absent" is a thing that really happens here.

The repair pattern is tested for when it does NOT fire as much as for
when it does. A pattern that fires on failures it was never shown to fix
is exactly what PATTERNS' evidence requirement exists to prevent.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import analog_loop  # noqa: E402


DECK = """* generated
.param W_P=1.0 W_N=0.5 L_P=0.15 L_N=0.15
x1 in vdd out GND inv
.end
"""


class BindParams(unittest.TestCase):
    def test_it_rewrites_in_place_rather_than_appending(self):
        """A second .param would work until a deck defines a name twice
        for its own reasons; then which wins is line order, not intent."""
        out = analog_loop.bind_params(DECK, {"W_P": 1.4266})
        self.assertIn("W_P=1.4266", out)
        self.assertEqual(out.count(".param"), 1)

    def test_untouched_parameters_survive(self):
        out = analog_loop.bind_params(DECK, {"W_P": 2})
        self.assertIn("W_N=0.5", out)
        self.assertIn("L_P=0.15", out)

    def test_a_parameter_the_deck_does_not_have_raises(self):
        """Guards a sweep that measures nothing five times: overriding a
        name no device reads changes not one number."""
        with self.assertRaises(KeyError):
            analog_loop.bind_params(DECK, {"W_Q": 1.0})


class Derived(unittest.TestCase):
    def test_the_ratio_is_the_imbalance_the_repair_uses(self):
        d = analog_loop.derive({"tphl": 1e-10, "tplh": 1.25e-10})
        self.assertAlmostEqual(d["rise_fall_ratio"], 1.25)
        self.assertAlmostEqual(d["tpd_avg"], 1.125e-10)

    def test_a_missing_edge_derives_nothing_rather_than_zero(self):
        d = analog_loop.derive({"tphl": 1e-10})
        self.assertNotIn("rise_fall_ratio", d)


def result(tag, overrides, per_corner, targets):
    return {"tag": tag, "overrides": overrides,
            "measurements": {c: analog_loop.derive(m) for c, m in per_corner.items()},
            "verdict": analog_loop.score(per_corner, targets)}


class Scoring(unittest.TestCase):
    TARGETS = {"rise_fall_ratio": {"max": 1.10}, "tpd_avg": {"max": 1.2e-10}}

    def test_the_worst_corner_governs(self):
        # Real numbers from the inv sweep: tt passes the ratio, ss does not.
        verdict = analog_loop.score(
            {"tt": {"tphl": 6.82e-11, "tplh": 8.28e-11},
             "ss": {"tphl": 8.83e-11, "tplh": 1.108e-10}}, self.TARGETS)
        self.assertFalse(verdict["passed"])
        self.assertTrue(any("at ss" in v for v in verdict["violations"]), verdict)

    def test_a_missing_measurement_is_unverified_not_passed(self):
        """ngspice exits 0 with a failed .meas, so absence is real."""
        verdict = analog_loop.score({"tt": {}}, {"tpd_avg": {"max": 1e-10}})
        self.assertFalse(verdict["passed"])
        self.assertTrue(verdict["unverified"])
        self.assertFalse(verdict["violations"])

    def test_clean_measurements_pass_and_record_their_margin(self):
        verdict = analog_loop.score(
            {"ss": {"tphl": 9.0e-11, "tplh": 8.8e-11}}, self.TARGETS)
        self.assertTrue(verdict["passed"], verdict)
        self.assertEqual(verdict["worst"]["tpd_avg"]["corner"], "ss")


class Winner(unittest.TestCase):
    TARGETS = {"tpd_avg": {"max": 1.0e-10}}

    def test_the_most_margin_wins_not_the_first_pass(self):
        tight = result("tight", {"W_P": 1.5}, {"ss": {"tphl": 9.8e-11, "tplh": 9.8e-11}},
                       self.TARGETS)
        roomy = result("roomy", {"W_P": 2.0}, {"ss": {"tphl": 8.0e-11, "tplh": 8.0e-11}},
                       self.TARGETS)
        self.assertEqual(analog_loop.pick_winner([tight, roomy])["tag"], "roomy")

    def test_nothing_passing_is_no_winner_rather_than_the_least_bad(self):
        bad = result("bad", {"W_P": 1.0}, {"ss": {"tphl": 2e-10, "tplh": 2e-10}},
                     self.TARGETS)
        self.assertIsNone(analog_loop.pick_winner([bad]))


class RepairPattern(unittest.TestCase):
    TARGETS = {"rise_fall_ratio": {"max": 1.10}, "tpd_avg": {"max": 1.2e-10}}

    def failing(self):
        # The real iteration-0 measurement of pipeline/analog/inv.
        return result("base-wp1", {"W_P": 1.0},
                      {"ss": {"tphl": 8.83e-11, "tplh": 1.108e-10}}, self.TARGETS)

    def test_it_steps_by_the_measured_imbalance(self):
        proposals = analog_loop.propose_repairs([self.failing()], 0, {})
        self.assertEqual(len(proposals), 1)
        self.assertAlmostEqual(proposals[0]["overrides"]["W_P"], 1.2548, places=3)
        self.assertEqual(proposals[0]["pattern"], "balance-rise-fall")
        self.assertEqual(proposals[0]["repair_of"], "base-wp1")

    def test_it_does_not_fire_when_something_else_also_failed(self):
        """A cell that is also too slow overall is not one whose pfet
        width was ever shown to be the answer."""
        slow = result("slow", {"W_P": 1.0},
                      {"ss": {"tphl": 2.0e-10, "tplh": 2.5e-10}}, self.TARGETS)
        self.assertEqual(analog_loop.propose_repairs([slow], 0, {}), [])

    def test_it_does_not_fire_on_an_unverified_candidate(self):
        blind = result("blind", {"W_P": 1.0}, {"ss": {}}, self.TARGETS)
        self.assertEqual(analog_loop.propose_repairs([blind], 0, {}), [])

    def test_it_does_not_widen_a_pull_up_that_is_already_the_fast_side(self):
        # ratio < 1 means the fall is the slow edge; this pattern has no
        # evidence for that direction, so it must stay out of it.
        fast = result("fast", {"W_P": 3.0},
                      {"ss": {"tphl": 1.3e-10, "tplh": 9.0e-11}},
                      {"rise_fall_ratio": {"min": 1.10}})
        self.assertEqual(analog_loop.propose_repairs([fast], 0, {}), [])

    def test_a_passing_candidate_is_never_repaired(self):
        good = result("good", {"W_P": 1.5},
                      {"ss": {"tphl": 9.0e-11, "tplh": 8.8e-11}}, self.TARGETS)
        self.assertEqual(analog_loop.propose_repairs([good], 0, {}), [])

    def test_one_step_is_capped(self):
        wild = result("wild", {"W_P": 1.0},
                      {"ss": {"tphl": 1.0e-11, "tplh": 1.0e-10}}, self.TARGETS)
        proposals = analog_loop.propose_repairs([wild], 0, {})
        # The ratio is 10; the step is not.
        self.assertLessEqual(proposals[0]["overrides"]["W_P"], analog_loop.MAX_STEP)

    def test_every_pattern_names_the_run_that_proved_it(self):
        """soul.md's bar: promoted, not assumed."""
        for pattern in analog_loop.PATTERNS:
            self.assertTrue(pattern["evidence"].strip(), pattern["id"])
            self.assertTrue(pattern["design"].strip(), pattern["id"])


class Coverage(unittest.TestCase):
    def test_a_failure_counts_as_covered_only_when_a_pattern_repaired_it(self):
        """The number can only rise by adding a pattern that fires on a
        real failure — never by redefining 'covered'."""
        case = {"iterations": [
            {"results": [{"tag": "a", "verdict": {"passed": False}}]},
            {"results": [{"tag": "b", "repair_of": "a", "verdict": {"passed": True}}]},
        ]}
        self.assertEqual(analog_loop.coverage(case),
                         {"failures": 1, "repaired": 1, "fraction": 1.0})

    def test_an_unrepaired_failure_is_not_covered(self):
        case = {"iterations": [{"results": [
            {"tag": "a", "verdict": {"passed": False}},
            {"tag": "b", "verdict": {"passed": False}},
        ]}]}
        self.assertEqual(analog_loop.coverage(case)["fraction"], 0.0)

    def test_the_stop_reasons_match_the_digital_loop(self):
        # Same vocabulary so a caller can branch the same way, and so
        # "ran out of budget" never reads as "genuinely stuck".
        import orchestrator
        self.assertEqual(set(analog_loop.STOP_REASONS),
                         set(orchestrator.STOP_REASONS))


if __name__ == "__main__":
    unittest.main()
