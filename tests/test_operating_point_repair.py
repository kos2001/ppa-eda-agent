"""The operating point must be computed against the period the run was
actually constrained to.

The bug: score_run_dir() handed operating_point() config.json's
CLOCK_PERIOD for every candidate, including the 90 recorded ones that
overrode it. Slack is relative to the period the tool was given, so a
12 ns candidate's min_period came out as 10 - slack. aes at 12 ns was
recorded at 207.5 MHz with an 11.14 ns critical path.

Three guards: the period is taken from the run itself (resolved.json),
then the override, then config; a recorded operating point can be
rebuilt from its own corners; and the committed store contains no
result whose operating point disagrees with its override.
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import operating_point  # noqa: E402
import orchestrator  # noqa: E402
import repair_operating_points  # noqa: E402

CORNERS = [
    {"corner": "max_ss_100C_1v60", "setup_ws_ns": 0.8561542828131142,
     "hold_ws_ns": 0.05},
    {"corner": "nom_tt_025C_1v80", "setup_ws_ns": 5.2, "hold_ws_ns": 0.2},
]


class RunClockPeriodTests(unittest.TestCase):

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "config.json").write_text(json.dumps({"CLOCK_PERIOD": 10}),
                                              encoding="utf-8")
        self.run = self.tmp / "runs" / "t"
        self.run.mkdir(parents=True)

    def test_the_run_s_own_record_wins(self):
        (self.run / "resolved.json").write_text(json.dumps({"CLOCK_PERIOD": 12.0}),
                                                encoding="utf-8")
        cand = {"overrides": {"CLOCK_PERIOD": 11}}
        self.assertEqual(orchestrator.run_clock_period(self.tmp, self.run, cand),
                         (12.0, "resolved.json"))

    def test_the_override_beats_the_design_default(self):
        # A recovered or screened run may have no resolved.json yet.
        cand = {"overrides": {"CLOCK_PERIOD": 11}}
        self.assertEqual(orchestrator.run_clock_period(self.tmp, self.run, cand),
                         (11.0, "override"))

    def test_config_is_the_last_resort(self):
        self.assertEqual(orchestrator.run_clock_period(self.tmp, self.run, {}),
                         (10.0, "config.json"))


class RebuildFromCornersTests(unittest.TestCase):

    def test_the_aes_12ns_candidate_recomputes_to_its_real_fmax(self):
        # The recorded values: period 10, so min_period 9.14 / 207.5 MHz.
        # Against the 12 ns the run was actually constrained to, the
        # same slack means an 11.14 ns critical path.
        metrics = operating_point.metrics_from_corners(CORNERS)
        op = operating_point.operating_point(metrics, 12.0)
        ss = next(c for c in op["corners"] if c["corner"] == "max_ss_100C_1v60")
        self.assertAlmostEqual(ss["min_period_ns"], 11.1438, places=3)
        self.assertAlmostEqual(op["fmax_mhz"], 89.74, places=1)
        self.assertEqual(op["fmax_limiting_corner"], "max_ss_100C_1v60")

    def test_slacks_survive_the_round_trip(self):
        metrics = operating_point.metrics_from_corners(CORNERS)
        op = operating_point.operating_point(metrics, 12.0)
        # corner_timing() walks the metric keys in sorted order.
        expected = [c["setup_ws_ns"] for c in sorted(CORNERS, key=lambda c: c["corner"])]
        self.assertEqual([c["setup_ws_ns"] for c in op["corners"]], expected)

    def test_repair_records_what_it_replaced(self):
        old = operating_point.operating_point(
            operating_point.metrics_from_corners(CORNERS), 10.0)
        result = {"tag": "clk12", "overrides": {"CLOCK_PERIOD": 12},
                  "verdict": {"operating_point": old}}
        change = repair_operating_points.repair_result(result)
        self.assertIsNotNone(change)
        new = result["verdict"]["operating_point"]
        self.assertEqual(new["clock_period_ns"], 12.0)
        self.assertEqual(new["repaired"]["previous_clock_period_ns"], 10.0)
        # 10 - 0.856 = 9.144 ns at the ss corner, the worst of the two.
        self.assertAlmostEqual(new["repaired"]["previous_fmax_mhz"], 109.36, places=1)
        self.assertAlmostEqual(new["fmax_mhz"], 89.74, places=1)
        # Idempotent: a second pass finds nothing to do.
        self.assertIsNone(repair_operating_points.repair_result(result))

    def test_a_matching_period_is_left_alone(self):
        old = operating_point.operating_point(
            operating_point.metrics_from_corners(CORNERS), 12.0)
        result = {"tag": "clk12", "overrides": {"CLOCK_PERIOD": 12},
                  "verdict": {"operating_point": old}}
        self.assertIsNone(repair_operating_points.repair_result(result))


class CommittedStoreTests(unittest.TestCase):

    def test_no_recorded_operating_point_disagrees_with_its_override(self):
        bad = []
        for path in sorted(repair_operating_points.CASES.glob("*.json")):
            case = json.loads(path.read_text(encoding="utf-8"))
            for iteration in case.get("iterations", []):
                for result in iteration.get("results", []):
                    if repair_operating_points.mismatched(result) is not None:
                        bad.append(f"{path.name}:{result.get('tag')}")
        self.assertEqual(bad, [], "run pipeline/repair_operating_points.py --write")


if __name__ == "__main__":
    unittest.main()
