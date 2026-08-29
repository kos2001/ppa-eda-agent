"""Tests for ranking candidates on measured versus estimated power.

power_activity's measurement is attached to every candidate that has a
testbench. That creates a hazard the pipeline did not have before:
annotated and vectorless numbers are not interchangeable. On spm the
same netlist reads 1.33e-03 W estimated against 1.53e-03 W measured.

Ranking one candidate's measured power against another's estimate would
compare that 15% offset and call it a difference between designs — so a
candidate whose simulation merely failed to compile would win the power
objective against candidates that are genuinely better. pick_winner()
therefore uses measured power only when every passing candidate has it.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import orchestrator  # noqa: E402


def candidate(tag, area, vectorless_w, annotated_w=None, passed=True):
    verdict = {
        "passed": passed,
        "area_um2": area,
        "worst_setup_wns": 0.0,
        "power": {"total_w": vectorless_w},
        "violations": [],
        "unverified": [],
    }
    if annotated_w is not None:
        verdict["power_activity"] = {
            "annotated": {"total": {"total_w": annotated_w}},
            "vectorless": {"total": {"total_w": vectorless_w}},
        }
    return {"tag": tag, "verdict": verdict}


class AnnotatedTotalTests(unittest.TestCase):
    def test_reads_a_measured_total(self):
        r = candidate("a", 100.0, 1.04e-03, 1.13e-03)
        self.assertAlmostEqual(orchestrator.annotated_total_w(r), 1.13e-03)

    def test_absent_measurement_is_none_not_zero(self):
        # Zero would read as the best possible power and win the
        # objective outright.
        r = candidate("a", 100.0, 1.04e-03)
        self.assertIsNone(orchestrator.annotated_total_w(r))

    def test_a_failed_simulation_is_none(self):
        r = candidate("a", 100.0, 1.04e-03)
        r["verdict"]["power_activity"] = {
            "error": "gate-level simulation did not compile"}
        self.assertIsNone(orchestrator.annotated_total_w(r))

    def test_a_candidate_that_errored_before_scoring_is_none(self):
        self.assertIsNone(orchestrator.annotated_total_w({"tag": "a", "error": "boom"}))


class MixedBasisTests(unittest.TestCase):
    def test_all_measured_ranks_on_the_measurement(self):
        # Same area and slack, so power decides. The measured order is
        # the reverse of the estimated one, which is the only way to
        # tell which basis was actually used.
        results = [
            candidate("est-better", 100.0, vectorless_w=1.00e-03, annotated_w=1.30e-03),
            candidate("meas-better", 100.0, vectorless_w=1.20e-03, annotated_w=1.10e-03),
        ]
        self.assertEqual(orchestrator.pick_winner(results)["tag"], "meas-better")

    def test_none_measured_ranks_on_the_estimate(self):
        results = [
            candidate("est-better", 100.0, vectorless_w=1.00e-03),
            candidate("est-worse", 100.0, vectorless_w=1.20e-03),
        ]
        self.assertEqual(orchestrator.pick_winner(results)["tag"], "est-better")

    def test_one_missing_measurement_forces_the_estimate_for_all(self):
        # The hazard case. 'no-sim' has no measurement; if the others
        # were ranked on their (higher) measured numbers against its
        # (lower) estimate, it would win on power for no reason but the
        # offset. The common basis must be the estimate, under which
        # 'est-better' genuinely wins.
        results = [
            candidate("est-better", 100.0, vectorless_w=0.90e-03, annotated_w=1.30e-03),
            candidate("no-sim", 100.0, vectorless_w=1.20e-03),
        ]
        self.assertEqual(orchestrator.pick_winner(results)["tag"], "est-better")

    def test_a_failed_simulation_does_not_win_by_default(self):
        # Same shape, stated as the consequence rather than the
        # mechanism: a candidate that failed to simulate must not be
        # promoted over one that simulated and measured worse.
        results = [
            candidate("simulated", 100.0, vectorless_w=1.00e-03, annotated_w=1.10e-03),
            candidate("failed-sim", 100.0, vectorless_w=1.50e-03),
        ]
        results[1]["verdict"]["power_activity"] = {"error": "did not compile"}
        self.assertEqual(orchestrator.pick_winner(results)["tag"], "simulated")

    def test_only_passing_candidates_set_the_basis(self):
        # A failing candidate's missing measurement must not drag the
        # passing ones back onto the estimate — it is not ranked at all.
        results = [
            candidate("meas-better", 100.0, vectorless_w=1.20e-03, annotated_w=1.10e-03),
            candidate("est-better", 100.0, vectorless_w=1.00e-03, annotated_w=1.30e-03),
            candidate("failed", 100.0, vectorless_w=0.10e-03, passed=False),
        ]
        self.assertEqual(orchestrator.pick_winner(results)["tag"], "meas-better")


class MeasureWiringTests(unittest.TestCase):
    def test_designs_without_a_clock_port_are_skipped(self):
        # No CLOCK_PORT means no clock to constrain, so nothing is run.
        import tempfile
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text("{}")
        self.assertIsNone(orchestrator.measure_activity_power(d, d))

    def test_uses_the_designs_own_clock_not_a_default(self):
        # A design whose clock is not named "clk" must not be measured
        # against a port that does not exist — OpenSTA would constrain
        # nothing and the activity numbers would be meaningless.
        import tempfile
        import json as _json
        captured = {}
        d = Path(tempfile.mkdtemp())
        (d / "config.json").write_text(_json.dumps(
            {"CLOCK_PORT": "sysclk", "CLOCK_PERIOD": 25}))
        (d / "verify").mkdir()
        (d / "verify" / "x_tb.v").write_text("module x_tb; endmodule")

        real = orchestrator.power_activity.measure
        orchestrator.power_activity.measure = (
            lambda dd, rd, clock_port="clk", clock_period=10.0, **kw:
            captured.update(port=clock_port, period=clock_period))
        try:
            orchestrator.measure_activity_power(d, d)
        finally:
            orchestrator.power_activity.measure = real
        self.assertEqual(captured["port"], "sysclk")
        self.assertEqual(captured["period"], 25)


if __name__ == "__main__":
    unittest.main()
