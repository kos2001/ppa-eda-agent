"""Admission limits, independent evidence and Pareto memory; no EDA execution."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import evaluation_archive as archive
from evaluation_budget import EvaluationBudget, validate_limits
import evaluation_provenance as provenance
import evaluation_scheduler as scheduler
import orchestrator


def candidate(tag, passed=False, area=100, power=0.001):
    return {"tag": tag, "overrides": {}, "verdict": {
        "passed": passed, "violations": [] if passed else ["max-fanout"], "unverified": [],
        "area_um2": area, "power": {"total_w": power}, "utilization": 0.5,
        "worst_setup_wns": 0, "timing_corners": [{"corner": "test", "setup_wns": 0, "hold_wns": 0}],
        "signoff_checks": [{"key": key, "count": 0} for key in
                           ("magic__drc_error__count", "klayout__drc_error__count", "design__lvs_error__count")]}}


class BudgetTests(unittest.TestCase):
    def test_bad_limits_are_rejected_before_evaluation(self):
        for spec in [{}, [], {"max_evaluations": True}, {"max_evaluations": 1.5},
                     {"max_wall_seconds": float("nan")}, {"max_wall_seconds": 0},
                     {"max_evaluations": None}, {"max_evaluations": 2, "typo": 1}]:
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                validate_limits(spec)

    def test_parallel_workers_cannot_overdraw(self):
        budget = EvaluationBudget({"max_evaluations": 3})
        with ThreadPoolExecutor(max_workers=16) as pool:
            tickets = list(pool.map(lambda tag: budget.start("full_flow", str(tag)), range(50)))
        self.assertEqual(sum(ticket is not None for ticket in tickets), 3)
        self.assertEqual(len(budget.snapshot()["not_evaluated"]), 47)

    def test_deadline_does_not_kill_admitted_verification(self):
        now = [0]
        budget = EvaluationBudget({"max_wall_seconds": 1}, clock=lambda: now[0])
        ticket = budget.start("full_flow", "a")
        now[0] = 3
        budget.finish(ticket, "completed")
        self.assertEqual(ticket["status"], "completed")
        self.assertIsNone(budget.start("full_flow", "b"))
        self.assertEqual(budget.snapshot()["not_evaluated"][0]["reason"], "max_wall_seconds")

    def run_loop(self, spec, fake, screen=False, iterations=3):
        with tempfile.TemporaryDirectory() as tmp:
            design = Path(tmp)
            (design / "config.json").write_text('{}')
            with patch.object(orchestrator, "run_candidate", side_effect=fake):
                return orchestrator.orchestrate(design, spec, iterations, screen=screen)

    def test_repair_calls_use_the_same_budget(self):
        calls = []
        def fake(design, spec, cand, verify):
            calls.append(cand["tag"])
            return candidate(cand["tag"])
        spec = {"candidates": [{"tag": "a", "overrides": {}}], "evaluation_budget": {"max_evaluations": 1}}
        with patch.object(orchestrator, "propose_repairs", return_value=[{"tag": "repair", "overrides": {}}]):
            iterations, winner, reason, _ = self.run_loop(spec, fake)
        self.assertEqual(calls, ["a"])
        self.assertIsNone(winner)
        self.assertEqual(reason, "evaluation_budget_exhausted")
        denied = iterations[-1]["results"][0]
        self.assertTrue(denied["not_evaluated"])
        self.assertNotIn("verdict", denied)
        self.assertNotIn("error", denied)
        self.assertNotIn("stage", denied)

    def test_screening_consumes_budget_before_promotion(self):
        spec = {"candidates": [{"tag": "a", "overrides": {}, "pdk": "p", "scl": "s"}],
                "targets": {"max_core_utilization": 0.75}, "evaluation_budget": {"max_evaluations": 1}}
        with patch.object(orchestrator, "run_stage", return_value=Path("/unused")) as flow, \
             patch.object(orchestrator, "read_metrics", return_value={"design__instance__utilization__stdcell": 0.2}):
            iterations, winner, reason, _ = self.run_loop(spec, lambda *args: self.fail("full flow launched"), screen=True)
        self.assertEqual(flow.call_args.kwargs["pdk"], "p")
        self.assertEqual(flow.call_args.kwargs["scl"], "s")
        self.assertEqual(reason, "evaluation_budget_exhausted")
        self.assertIn("screen_evaluation", iterations[-1]["results"][0])
        self.assertEqual(iterations[-1]["results"][0]["stage"], "physical_constraint")
        self.assertEqual(iterations[-1]["evaluation_budget"]["started_evaluations"], 1)

    def test_polish_cannot_spend_past_a_passing_candidate(self):
        calls = []
        def fake(design, spec, cand, verify):
            calls.append(cand["tag"])
            return candidate(cand["tag"], passed=True)
        spec = {"candidates": [{"tag": "a", "overrides": {}}], "polish": {"moves": ["hold-margin-0"]},
                "evaluation_budget": {"max_evaluations": 1}}
        iterations, winner, reason, _ = self.run_loop(spec, fake)
        self.assertEqual(calls, ["a"])
        self.assertEqual(winner["tag"], "a")
        self.assertEqual(reason, "winner_found")
        self.assertTrue(iterations[-1]["results"][0]["not_evaluated"])

    def test_synthesis_exploration_consumes_an_evaluation(self):
        spec = {"explore_synthesis": {"count": 1}, "evaluation_budget": {"max_evaluations": 1}}
        with patch.object(orchestrator, "expand_synthesis_exploration", return_value=(
                [{"tag": "explored", "overrides": {}}], {"results": []})):
            iterations, winner, reason, exploration = self.run_loop(spec, lambda *args: self.fail("full flow launched"))
        self.assertEqual(reason, "evaluation_budget_exhausted")
        self.assertEqual(exploration["evaluation"]["kind"], "synthesis_exploration")


class ObjectiveTests(unittest.TestCase):
    def test_missing_power_does_not_become_a_zero_power_winner(self):
        a, b = candidate("measured", True), candidate("missing", True, area=120, power=None)
        table = dict(orchestrator.objective_table([a, b]))
        self.assertNotIn("power", table["missing"])
        self.assertEqual(orchestrator.pick_winner([a, b])["tag"], "measured")

    def test_missing_area_is_not_free_silicon(self):
        a, b = candidate("missing", True, area=None, power=0.002), candidate("measured", True)
        table = dict(orchestrator.objective_table([a, b]))
        self.assertNotIn("area", table["missing"])
        self.assertEqual(orchestrator.pick_winner([a, b])["tag"], "measured")

    def test_nonfinite_values_drop_axes_and_measured_zero_is_preserved(self):
        a, b = candidate("a", True, power=0), candidate("b", True, power=0.001)
        self.assertEqual(dict(orchestrator.objective_table([a, b]))["a"]["power"], 0)
        b["verdict"]["worst_setup_slack"] = float("inf")
        a["verdict"]["worst_setup_slack"] = 1
        b["verdict"]["power"]["total_w"] = float("nan")
        table = dict(orchestrator.objective_table([a, b]))
        self.assertNotIn("margin", table["a"])
        self.assertNotIn("power", table["a"])

    def test_unknown_corner_cannot_be_hidden_by_another_known_corner(self):
        a, b = candidate("a", True), candidate("b", True)
        for row in [a, b]:
            row["verdict"]["operating_point"] = {"corners": [{"setup_ws_ns": 1}, {"setup_ws_ns": None}]}
        self.assertNotIn("margin", dict(orchestrator.objective_table([a, b]))["a"])


class InstrumentationTests(unittest.TestCase):
    def test_budget_stop_is_not_triaged_as_an_unknown_tool_failure(self):
        import self_improve
        case = {"design": "fixture", "date": "2026-10-04", "winner_tag": None,
                "stop_reason": "evaluation_budget_exhausted", "iterations": [{"results": [
                    {"tag": "unrun", "not_evaluated": True}]}],
                "evaluation_budget": {"limits": {"max_evaluations": 1}}}
        with patch.object(self_improve, "latest_case", return_value=case), \
             patch.object(self_improve.verify_diagnosis, "verify_case", return_value={}), \
             patch.object(self_improve.subprocess, "run") as process:
            report = self_improve.scan_design("fixture", write=True)
        self.assertFalse(report["needs_review"])
        self.assertIsNone(report["retry_with_more_budget"])
        self.assertIn("evaluation_budget", report["evaluation_budget_action"]["next_step"])
        self.assertEqual(self_improve.auto_repair_coverage(case)[:2], (0, 0))
        process.assert_not_called()

    def test_failed_flow_retains_time_without_fabricating_assessment(self):
        import surrogate
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.json").write_text('{}')
            with patch.object(orchestrator, "run_stage", side_effect=RuntimeError("tool exited")), \
                 patch.object(orchestrator.live_events, "emit"), \
                 patch.object(surrogate, "predict_candidate", return_value={}):
                row = orchestrator.run_candidate(root, {}, {"tag": "failure", "overrides": {}}, False)
        self.assertEqual(row["error"], "tool exited")
        self.assertEqual(row["evaluation_fidelity"], "full_flow")
        self.assertIn("openlane", row["stage_costs"])
        self.assertNotIn("assessment_and_verification", row["stage_costs"])
        self.assertGreaterEqual(row["seconds"], sum(row["stage_costs"].values()) - 0.000002)


class ArchiveTests(unittest.TestCase):
    @staticmethod
    def row(tag="a", area=100, context_change=None):
        row = candidate(tag, True, area=area)
        context = {"design": "fixture", "pdk": "test-pdk", "scl": "test-scl", "pdk_version": "test-pinned",
                   "image": "test-image", "openroad_revision": "test-rev", "flow": "Classic",
                   "inputs": {"complete": True, "config_sha256": "a" * 64, "rtl_sha256": {"rtl.v": "b" * 64}},
                   "sdc_sha256": ["c" * 64], "liberty_sha256": ["d" * 64],
                   "verification_policy": {"rtl_netlist_equivalence_requested": False}}
        context.update(context_change or {})
        row["evaluation_provenance"] = {"complete": True, "context": context, "compatibility_key": provenance.digest(context)}
        return row

    def test_legacy_pass_is_not_assigned_todays_source_provenance(self):
        self.assertEqual(archive.eligible(candidate("legacy", True)), "provenance_unknown")

    def test_requested_equivalence_must_have_a_nonvacuous_proof(self):
        row = self.row(context_change={"verification_policy": {"rtl_netlist_equivalence_requested": True}})
        self.assertEqual(archive.eligible(row), "equivalence_unverified")
        row["equivalence"] = {"equivalent": True, "vacuous": True}
        self.assertEqual(archive.eligible(row), "equivalence_unverified")
        row["equivalence"]["vacuous"] = False
        self.assertIsNone(archive.eligible(row))

    def test_model_or_missing_timing_blocks_an_archive_entry(self):
        row = self.row()
        row["verdict"]["model_validity"] = {"macro_arc_audit": {"model_qualified": False}}
        self.assertEqual(archive.eligible(row), "model_unqualified")
        row["verdict"]["model_validity"] = None
        row["verdict"]["timing_corners"][0]["hold_wns"] = None
        self.assertEqual(archive.eligible(row), "timing_coverage_unknown")

    def test_one_clean_check_does_not_replace_drc_and_lvs_coverage(self):
        row = self.row()
        row["verdict"]["signoff_checks"] = row["verdict"]["signoff_checks"][:1]
        self.assertEqual(archive.eligible(row), "physical_checks_unknown")

    def test_persistent_frontier_separates_constraint_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "cases").mkdir()
            rows = [self.row("best", 90), self.row("dominated", 120),
                    self.row("different-sdc", 80, {"sdc_sha256": ["e" * 64]})]
            for index, row in enumerate(rows):
                (root / "cases" / f"case{index}.json").write_text(json.dumps({"design": "fixture", "iterations": [{"results": [row]}]}))
            report = archive.refresh(root)
            self.assertEqual(len(report["archives"]), 2)
            ids = [point["id"] for group in report["archives"] for point in group["frontier"]]
            self.assertTrue(any("best" in name for name in ids))
            self.assertFalse(any("dominated" in name for name in ids))
            self.assertEqual(json.loads((root / ".cache/pareto-archive.json").read_text()), report)
            self.assertEqual(report["timed_runs"], 0)

    def test_input_snapshot_detects_source_changes_without_running_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "config.json").write_text(json.dumps({"VERILOG_FILES": "dir::src/*.v"}))
            rtl = root / "src/a.v"
            rtl.write_text("module a; endmodule")
            before = provenance.capture_inputs(root)
            rtl.write_text("module b; endmodule")
            after = provenance.capture_inputs(root)
            self.assertTrue(before["complete"])
            self.assertNotEqual(before, after)

    def test_resolved_inputs_and_constraints_define_compatibility(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / "run"
            (run / "final/sdc").mkdir(parents=True)
            (root / "config.json").write_text(json.dumps({"VERILOG_FILES": "dir::a.v"}))
            (root / "a.v").write_text('`include "header.vh"\nmodule a; endmodule')
            (root / "header.vh").write_text("// fixture include")
            lib = root / "cells.lib"
            lib.write_text("library(test) {}")
            sdc = run / "final/sdc/a.sdc"
            sdc.write_text("create_clock -period 10 [get_ports clk]")
            (run / "resolved.json").write_text(json.dumps({"DESIGN_NAME": "a", "PDK": "fixture-not-installed",
                "STD_CELL_LIBRARY": "fixture-scl", "LIB": {"fixture": [str(lib)]}}))
            inputs = provenance.capture_inputs(root)
            self.assertIn("header.vh", inputs["rtl_sha256"])
            tc = {"openlane_image": "fixture-image", "expected_openroad_revision": "fixture-rev"}
            def context(overrides=None, flow=None):
                return provenance.complete_context(root, run, {"overrides": overrides or {}, "flow": flow}, inputs, tc, False, "fixture-version")
            baseline = context()
            self.assertTrue(baseline["complete"], baseline)
            self.assertEqual(baseline["compatibility_key"], context({"FP_CORE_UTIL": 40})["compatibility_key"])
            self.assertNotEqual(baseline["compatibility_key"], context({"CLOCK_PERIOD": 8})["compatibility_key"])
            self.assertFalse(context(flow="FanoutRepair")["complete"])
            sdc.write_text("create_clock -period 8 [get_ports clk]")
            self.assertNotEqual(baseline["compatibility_key"], context()["compatibility_key"])
            (root / "header.vh").write_text("// changed during evaluation")
            self.assertFalse(context()["complete"])


class SchedulerTests(unittest.TestCase):
    def test_unknown_arms_are_explored_and_only_compatible_costs_order_known_arms(self):
        tc = {"openlane_image": "test-image", "host": {"arch": "test"}}
        inputs = {"complete": True, "rtl_sha256": {"rtl": "test-hash"}}
        candidates = [{"tag": "slow", "scl": "slow"}, {"tag": "fast", "scl": "fast"}, {"tag": "unseen", "scl": "new"}]
        cohorts = []
        for scl, seconds in [("slow", 100), ("fast", 10)]:
            cohorts.append({"context": {"design": "d", "pdk": None, "scl": scl, "image": "test-image",
                            "host": tc["host"], "flow": None, "status": "completed", "verification_requested": False,
                            "input_key": provenance.digest(inputs), "fidelity": "full_flow"}, "samples": 3, "median_seconds": seconds})
        ordered = scheduler.order(candidates, "d", tc, cohorts, inputs)
        self.assertEqual([row["tag"] for row in ordered], ["unseen", "fast", "slow"])
        mismatched = scheduler.order(candidates, "d", {**tc, "openlane_image": "different"}, cohorts, inputs)
        self.assertTrue(all(row["scheduling"]["estimated_seconds"] is None for row in mismatched))


if __name__ == "__main__":
    unittest.main()
