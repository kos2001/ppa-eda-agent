"""Decision tests only: fixtures are not measured chip results."""
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import implementation_contract as contract
import orchestrator


class ImplementationContractTests(unittest.TestCase):
    def verdict(self):
        return {"passed": True, "violations": [], "unverified": []}

    def test_budget_failure_excludes_candidate_from_winner_selection(self):
        verdict = self.verdict()
        contract.apply_targets(verdict, contract.target_checks(
            {"design__instance__area": 101}, {"max_cell_area_um2": 100}))
        self.assertFalse(verdict["passed"])
        self.assertIsNone(orchestrator.pick_winner([{"tag": "oversized", "verdict": verdict}]))

    def test_missing_and_invalid_measurements_block_pass(self):
        for actual in (None, float("nan"), float("inf"), -1, True, "0.1"):
            with self.subTest(actual=actual):
                verdict = self.verdict()
                contract.apply_targets(verdict, contract.target_checks(
                    {"power__total": actual}, {"max_power_w": 1}))
                self.assertFalse(verdict["passed"])
                self.assertTrue(verdict["unverified"])

    def test_exact_budget_passes_without_erasing_existing_failure(self):
        verdict = self.verdict()
        checks = contract.target_checks({"power__total": 1}, {"max_power_w": 1})
        contract.apply_targets(verdict, checks)
        self.assertTrue(verdict["passed"])
        verdict["violations"].append("hold violation")
        contract.apply_targets(verdict, checks)
        self.assertFalse(verdict["passed"])

    def test_all_area_budgets_use_distinct_metrics(self):
        checks = contract.target_checks(
            {"design__instance__area": 1, "design__core__area": 2, "design__die__area": 3},
            {"max_cell_area_um2": 1, "max_core_area_um2": 2, "max_die_area_um2": 2})
        self.assertEqual([c["status"] for c in checks], ["passed", "passed", "failed"])

    def test_clock_relaxation_cannot_evade_fixed_period_budget(self):
        verdict = self.verdict()
        contract.apply_targets(verdict, [contract.check_limit("max_clock_period_ns", 20, 10, "ns")])
        self.assertFalse(verdict["passed"])

    def test_invalid_spec_fails_before_synthesis(self):
        for value in (0, -1, float("nan"), True, "10"):
            with patch.object(orchestrator, "expand_synthesis_exploration") as explore:
                with self.assertRaises(ValueError):
                    orchestrator.orchestrate(Path("unused"), {"targets": {"max_power_w": value}}, 1)
                explore.assert_not_called()
        with self.assertRaises(ValueError):
            contract.validate_spec({"require_implementation_evidence": "false"})

    def test_score_enforces_declared_budget(self):
        metrics = {key: 0 for key, _ in orchestrator.SIGNOFF_METRICS}
        metrics["power__total"] = 2
        verdict = orchestrator.score(metrics, {"max_power_w": 1})
        self.assertFalse(verdict["passed"])
        self.assertEqual(verdict["target_checks"][0]["status"], "failed")

    def test_evidence_requires_nonempty_gds_and_nonvacuous_proof(self):
        with tempfile.TemporaryDirectory() as temp:
            run = Path(temp)
            gds = run / "final" / "gds" / "fixture.gds"
            gds.parent.mkdir(parents=True)
            gds.touch()
            proof = {"equivalent": True, "vacuous": False, "proven_points": 4, "unproven_points": 0}
            v = self.verdict()
            contract.attach_evidence(v, run, proof, True)
            self.assertFalse(v["passed"])
            # File-presence fixture only; this is not a valid or verified GDS.
            gds.write_bytes(b"test fixture")
            for evidence, expected in ((proof, True), (None, False),
                                       ({**proof, "vacuous": True}, False),
                                       ({**proof, "proven_points": 0}, False)):
                v = self.verdict()
                contract.attach_evidence(v, run, evidence, True)
                self.assertEqual(v["passed"], expected)

    def test_legacy_mode_reports_missing_evidence_without_changing_verdict(self):
        with tempfile.TemporaryDirectory() as temp:
            v = self.verdict()
            contract.attach_evidence(v, Path(temp), None)
            self.assertTrue(v["passed"])
            self.assertFalse(v["implementation_evidence"]["qualified"])

    def test_completed_run_automatically_checks_equivalence_and_clock_budget(self):
        # Stub tool adapters to test wiring, not the tools' engineering results.
        with tempfile.TemporaryDirectory() as temp, ExitStack() as stack:
            run = Path(temp)
            adapters = [
                (orchestrator, "read_metrics", {key: 0 for key, _ in orchestrator.SIGNOFF_METRICS}),
                (orchestrator.cdc_check, "check", {}),
                (orchestrator.cdc_check, "unverified_domains", []),
                (orchestrator.model_validity, "check", {}),
                (orchestrator.model_validity, "unverified", []),
                (orchestrator.magic_abstract_drc, "check", None),
                (orchestrator, "run_clock_period", (20, "resolved.json")),
                (orchestrator.operating_point, "operating_point", None),
                (orchestrator, "measure_activity_power", None),
                (orchestrator.def_layout, "layout_summary", None),
                (orchestrator, "extra_lef_paths", []),
                (orchestrator.netlist_graph, "summary", None),
                (orchestrator, "classic_steps", []),
                (orchestrator, "data_pointers", {}),
            ]
            for obj, name, value in adapters:
                stack.enter_context(patch.object(obj, name, return_value=value))
            proof = stack.enter_context(patch.object(orchestrator, "verify_function", return_value={
                "equivalent": True, "vacuous": False, "proven_points": 4, "unproven_points": 0}))
            simulation = stack.enter_context(patch.object(orchestrator.functional_regression, "check",
                return_value={"status": "failed", "runs": []}))
            result = orchestrator.score_run_dir(run, run, {
                "require_implementation_evidence": True,
                "functional_regression": {"top": "tb", "testbench": "tb.sv"},
                "targets": {"max_clock_period_ns": 10},
            }, {}, "candidate", None, None, verify_fn=False)
            proof.assert_called_once()
            simulation.assert_called_once()
            self.assertIn("Functional regression failed", result["verdict"]["violations"])
            self.assertFalse(result["verdict"]["passed"])
            self.assertTrue(result["verdict"]["implementation_evidence"]["required"])
            self.assertEqual(result["verdict"]["target_checks"][0]["status"], "failed")


if __name__ == "__main__":
    unittest.main()
