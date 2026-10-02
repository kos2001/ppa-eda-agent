"""Regression tests for pnr_polish.py - the optional post-pass phase.

The decision logic is tested with an injected runner: the point is what is
accepted and what is refused, not OpenLane. Whether a move really helps is
a measurement (pnr_study.py), recorded in pnr_polish.MOVES[*]["evidence"].

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import orchestrator  # noqa: E402
import pnr_polish  # noqa: E402

MARGIN0 = {"id": "m0", "why": "w", "overrides": {"PL_RESIZER_HOLD_SLACK_MARGIN": 0.0}}
NOBUF = {"id": "nobuf", "why": "w", "overrides": {"DESIGN_REPAIR_BUFFER_INPUT_PORTS": False}}


def result(tag, area=3000.0, power=1e-3, util=0.5, slack=1.0, passed=True, overrides=None):
    return {"tag": tag, "overrides": dict(overrides or {}), "verdict": {
        "passed": passed, "area_um2": area, "utilization": util,
        "violations": [] if passed else ["1 max-fanout (DRV) violation(s)"],
        "worst_setup_wns": 0.0, "worst_setup_slack": slack,
        "power": {"total_w": power}}}


class TestPlan(unittest.TestCase):
    def test_a_move_already_in_force_is_not_run_again(self):
        """Re-running the winner under another name would report a repeat
        as an experiment."""
        got = pnr_polish.plan({"PL_RESIZER_HOLD_SLACK_MARGIN": 0.0}, {}, [MARGIN0, NOBUF])
        self.assertEqual([m["id"] for m in got], ["nobuf"])

    def test_the_design_config_counts_as_in_force(self):
        got = pnr_polish.plan({}, {"PL_RESIZER_HOLD_SLACK_MARGIN": 0.0}, [MARGIN0])
        self.assertEqual(got, [])

    def test_defaults_are_the_pure_margin_moves(self):
        """A move that changes a design-level assumption is selected by
        name; only margin trades, which signoff fully polices, are default."""
        ids = [m["id"] for m in pnr_polish.plan({}, {})]
        self.assertEqual(ids, ["hold-margin-0"])
        self.assertIn("no-input-port-buffers", [m["id"] for m in pnr_polish.MOVES])

    def test_every_move_carries_its_evidence_and_its_cost(self):
        for m in pnr_polish.MOVES:
            self.assertTrue(m["evidence"], f"{m['id']} has no measurement behind it")
            self.assertTrue(m["risk"], f"{m['id']} does not say what it gives up")


class TestImproves(unittest.TestCase):
    TABLE = staticmethod(orchestrator.objective_table)

    def better(self, incumbent, trial):
        return pnr_polish.improves(incumbent, trial, self.TABLE)

    def test_smaller_and_still_passing_is_an_improvement(self):
        self.assertTrue(self.better(result("w", area=3000), result("t", area=2700)))

    def test_a_failing_trial_never_improves(self):
        self.assertFalse(self.better(result("w", area=3000),
                                     result("t", area=2000, passed=False)))

    def test_a_real_trade_is_not_an_improvement(self):
        """Smaller but much hungrier is a different point on the front,
        not a better one; polish only takes moves that cost nothing."""
        self.assertFalse(self.better(result("w", area=3000, power=1e-3),
                                     result("t", area=2700, power=1.5e-3)))

    def test_an_identical_result_is_not_an_improvement(self):
        """A knob that does nothing (CTS clustering on a 35-flop design
        gave byte-identical metrics) must not be adopted as a win."""
        self.assertFalse(self.better(result("w"), result("t")))

    def test_the_real_gcd_case_is_accepted(self):
        """hold-margin-0 on gcd: area 3004 -> 2734 (-9.0%), power 745 ->
        722 uW, setup slack 1.795 -> 1.78 ns. Strict dominance refused it
        for the 0.9% of slack."""
        inc = result("w", area=3004.13, power=745.3e-6, slack=1.795)
        trial = result("t", area=2733.87, power=721.6e-6, slack=1.78)
        self.assertTrue(self.better(inc, trial))

    def test_a_gain_inside_the_noise_is_not_worth_a_run_s_trouble(self):
        self.assertFalse(self.better(result("w", area=3000), result("t", area=2950)))  # -1.7%

    def test_a_regression_beyond_the_tolerance_vetoes_a_large_gain(self):
        inc = result("w", area=3000, slack=2.0)
        self.assertFalse(self.better(inc, result("t", area=2400, slack=1.0)))  # slack -50%

    def test_setup_slack_alone_is_not_a_gain(self):
        inc = result("w", slack=1.0)
        self.assertFalse(self.better(inc, result("t", slack=3.0)))


class FakeRunner:
    """Returns canned results keyed by the overrides a candidate carries."""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, cands):
        self.calls.append([c["tag"] for c in cands])
        out = []
        for c in cands:
            key = tuple(sorted(k for k in c["overrides"] if k in self.table_keys()))
            spec = self.table.get(key, {})
            out.append(result(c["tag"], overrides=c["overrides"], **spec))
        return out

    def table_keys(self):
        return {k for key in self.table for k in key}


class TestPolish(unittest.TestCase):
    WINNER = result("w", area=3000.0)

    def run_polish(self, table, moves):
        runner = FakeRunner(table)
        final, trials = pnr_polish.polish(
            self.WINNER, moves, runner, orchestrator.objective_table)
        return final, trials, runner

    def test_no_moves_runs_nothing(self):
        final, trials, runner = self.run_polish({}, [])
        self.assertIs(final, self.WINNER)
        self.assertEqual((trials, runner.calls), ([], []))

    def test_no_improving_move_keeps_the_winner(self):
        final, trials, runner = self.run_polish({}, [MARGIN0, NOBUF])
        self.assertEqual(final["tag"], "w")
        self.assertEqual(len(trials), 2)
        self.assertEqual(len(runner.calls), 1)  # trials in one parallel batch

    def test_a_single_improving_move_is_adopted_without_a_combination_run(self):
        table = {("PL_RESIZER_HOLD_SLACK_MARGIN",): {"area": 2700.0}}
        final, trials, runner = self.run_polish(table, [MARGIN0, NOBUF])
        self.assertEqual(final["tag"], "w-polish-m0")
        self.assertEqual(len(runner.calls), 1)

    def test_two_improving_moves_are_combined_and_the_combination_kept_when_better(self):
        table = {("PL_RESIZER_HOLD_SLACK_MARGIN",): {"area": 2700.0},
                 ("DESIGN_REPAIR_BUFFER_INPUT_PORTS",): {"area": 2850.0},
                 ("DESIGN_REPAIR_BUFFER_INPUT_PORTS", "PL_RESIZER_HOLD_SLACK_MARGIN"):
                     {"area": 2500.0}}
        final, trials, runner = self.run_polish(table, [MARGIN0, NOBUF])
        self.assertEqual(final["tag"], "w-polish-combined")
        self.assertEqual(len(runner.calls), 2)
        self.assertEqual(final["overrides"], {"PL_RESIZER_HOLD_SLACK_MARGIN": 0.0,
                                              "DESIGN_REPAIR_BUFFER_INPUT_PORTS": False})

    def test_moves_interact_and_a_worse_combination_is_not_kept(self):
        """Two good moves together are not guaranteed to be good."""
        table = {("PL_RESIZER_HOLD_SLACK_MARGIN",): {"area": 2700.0},
                 ("DESIGN_REPAIR_BUFFER_INPUT_PORTS",): {"area": 2850.0},
                 ("DESIGN_REPAIR_BUFFER_INPUT_PORTS", "PL_RESIZER_HOLD_SLACK_MARGIN"):
                     {"area": 2900.0}}
        final, trials, _ = self.run_polish(table, [MARGIN0, NOBUF])
        self.assertEqual(final["tag"], "w-polish-m0")
        self.assertEqual(len(trials), 3)  # the failed combination is still recorded

    def test_a_combination_that_fails_signoff_is_not_kept(self):
        table = {("PL_RESIZER_HOLD_SLACK_MARGIN",): {"area": 2700.0},
                 ("DESIGN_REPAIR_BUFFER_INPUT_PORTS",): {"area": 2850.0},
                 ("DESIGN_REPAIR_BUFFER_INPUT_PORTS", "PL_RESIZER_HOLD_SLACK_MARGIN"):
                     {"area": 2000.0, "passed": False}}
        final, _, _ = self.run_polish(table, [MARGIN0, NOBUF])
        self.assertEqual(final["tag"], "w-polish-m0")

    def test_every_trial_says_which_move_it_was(self):
        _, trials, _ = self.run_polish({}, [MARGIN0])
        self.assertEqual(trials[0]["polish"]["move"], "m0")

    def test_trials_keep_the_winners_technology(self):
        winner = {**self.WINNER, "pdk": "gf180mcuD", "scl": "gf180mcu_fd_sc_mcu7t5v0"}
        seen = []

        def runner(cands):
            seen.extend(cands)
            return [result(c["tag"], overrides=c["overrides"]) for c in cands]

        pnr_polish.polish(winner, [MARGIN0], runner, orchestrator.objective_table)
        self.assertEqual((seen[0]["pdk"], seen[0]["scl"]),
                         ("gf180mcuD", "gf180mcu_fd_sc_mcu7t5v0"))


class TestPolishMoves(unittest.TestCase):
    def test_true_means_the_measured_defaults(self):
        self.assertIsNone(orchestrator.polish_moves(True))
        self.assertIsNone(orchestrator.polish_moves({}))

    def test_ids_select_among_the_defaults(self):
        got = orchestrator.polish_moves({"moves": ["hold-margin-0"]})
        self.assertEqual([m["id"] for m in got], ["hold-margin-0"])

    def test_a_non_default_move_can_be_asked_for_by_name(self):
        got = orchestrator.polish_moves({"moves": ["no-input-port-buffers"]})
        self.assertEqual(got[0]["overrides"], {"DESIGN_REPAIR_BUFFER_INPUT_PORTS": False})

    def test_an_unknown_id_is_an_error_not_a_silent_skip(self):
        with self.assertRaises(ValueError):
            orchestrator.polish_moves({"moves": ["hold-margin-zero"]})

    def test_a_run_spec_can_supply_its_own_move(self):
        got = orchestrator.polish_moves({"moves": [
            {"id": "x", "overrides": {"GRT_ADJUSTMENT": 0.2}}]})
        self.assertEqual(got[0]["overrides"], {"GRT_ADJUSTMENT": 0.2})


class TestOrchestrateHook(unittest.TestCase):
    """orchestrate() with the flow replaced by canned results."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.design = Path(self.tmp.name)
        (self.design / "config.json").write_text(json.dumps({"FP_CORE_UTIL": 40}))
        self.saved = (orchestrator.run_candidates,
                      orchestrator.expand_synthesis_exploration)
        orchestrator.expand_synthesis_exploration = lambda d, s: ([], None)

    def tearDown(self):
        (orchestrator.run_candidates,
         orchestrator.expand_synthesis_exploration) = self.saved
        self.tmp.cleanup()

    def fake_flow(self, cands_seen):
        def run(design_dir, spec, max_parallel=1, verify_fn=False):
            out = []
            for c in spec["candidates"]:
                cands_seen.append(c["tag"])
                polished = "PL_RESIZER_HOLD_SLACK_MARGIN" in c["overrides"]
                out.append(result(c["tag"], overrides=c["overrides"],
                                  area=2700.0 if polished else 3000.0))
            return out
        return run

    def test_polish_is_off_unless_asked_for(self):
        seen = []
        orchestrator.run_candidates = self.fake_flow(seen)
        spec = {"candidates": [{"tag": "base", "overrides": {}}]}
        its, winner, stop, _ = orchestrator.orchestrate(self.design, spec, 1)
        self.assertEqual(seen, ["base"])
        self.assertEqual(winner["tag"], "base")
        self.assertEqual(len(its), 1)

    def test_polish_runs_after_the_winner_and_can_replace_it(self):
        seen = []
        orchestrator.run_candidates = self.fake_flow(seen)
        spec = {"candidates": [{"tag": "base", "overrides": {}}],
                "polish": {"moves": ["hold-margin-0"]}}
        its, winner, stop, _ = orchestrator.orchestrate(self.design, spec, 1)
        self.assertEqual(stop, "winner_found")
        self.assertEqual(winner["tag"], "base-polish-hold-margin-0")
        self.assertTrue(its[-1]["polish"])
        self.assertEqual(its[-1]["iteration"], 2)
        self.assertEqual(its[-1]["results"][0]["polish"]["move"], "hold-margin-0")

    def test_no_winner_means_no_polish(self):
        seen = []

        def run(design_dir, spec, max_parallel=1, verify_fn=False):
            seen.extend(c["tag"] for c in spec["candidates"])
            return [result(c["tag"], passed=False, overrides=c["overrides"])
                    for c in spec["candidates"]]

        orchestrator.run_candidates = run
        spec = {"candidates": [{"tag": "base", "overrides": {}}], "polish": True}
        _, winner, stop, _ = orchestrator.orchestrate(self.design, spec, 1)
        self.assertIsNone(winner)
        self.assertEqual(seen, ["base"])


if __name__ == "__main__":
    unittest.main()
