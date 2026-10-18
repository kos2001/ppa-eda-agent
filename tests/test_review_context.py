import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import review_context as context
import review_context_eval as evaluator


def frozen(args):
    return context.snapshot(args.case_file, args.design_config, args.seed_tag,
                            args.allow_override, args.history_limit)


class ReviewContextTests(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp_path = Path(tmp.name)
        old = {"design": "demo", "diagnosis": "OLD_SECRET", "iterations": [{"results": [
            {"tag": "old", "overrides": {"FP_CORE_UTIL": 40}, "verdict": {"passed": False}}]}]}
        case = {"design": "demo", "diagnosis": "CURRENT_SECRET", "iterations": [{"results": [
            {"tag": "seed", "overrides": {"FP_CORE_UTIL": 35}, "verdict": {"passed": False}},
            {"tag": "deferred", "overrides": {}, "not_evaluated": True}]}]}
        for name, value in [("demo__1.json", old), ("demo__2.json", case), ("config.json", {"CLOCK_PERIOD": 10})]:
            (self.tmp_path / name).write_text(json.dumps(value), encoding="utf-8")
        self.inputs = argparse.Namespace(
            case_file=self.tmp_path / "demo__2.json", design_config=self.tmp_path / "config.json",
            seed_tag="seed", allow_override=["FP_CORE_UTIL"], history_limit=12,
            repetitions=2, candidate_limit=3, random_seed=42, output_dir=self.tmp_path / "experiment")

    def test_visibility_and_unchanged_inputs(self):
        inputs = self.inputs
        before = inputs.case_file.read_bytes()
        snap = frozen(inputs)
        full = context.capsule(snap, "full_history")
        fresh = context.capsule(snap, "fresh_evidence")
        seed = context.capsule(snap, "seed_only")
        self.assertIn("OLD_SECRET", context.canonical(full))
        self.assertNotIn("SECRET", context.canonical(fresh))
        self.assertEqual(full["current"], fresh["current"])
        self.assertEqual(full["history"], fresh["history"])
        self.assertEqual(seed["history"], [])
        self.assertEqual([r["tag"] for r in context.results(seed["current"])], ["seed"])
        self.assertEqual(inputs.case_file.read_bytes(), before)

    def test_reject_unmeasured_and_protected(self):
        inputs = self.inputs
        inputs.seed_tag = "deferred"
        with self.assertRaisesRegex(ValueError, "unmeasured"):
            frozen(inputs)
        for key in ["CLOCK_PERIOD", "RUN_LVS", "SIGNOFF_SDC_FILE", "STA_CORNERS"]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                context.validate_allowed([key])

    def test_refresh_retains_task_budget_and_verification_evidence(self):
        snap = frozen(self.inputs)
        contract = {"mode": "explicit", "objective": "minimize area after all gates",
                    "reference": "fixed 10 ns baseline", "candidate_budget": 2,
                    "evaluation_budget": {"max_evaluations": 2}, "max_iterations": 1}
        snap["case"]["search_plan"] = {**contract, "recommendation": "PLAN_SECRET"}
        snap["case"]["evaluation_budget"] = {"started_evaluations": 1,
                                              "limits": {"max_evaluations": 2}}
        evidence = {
            "evaluation_provenance": {"complete": True, "compatibility_key": "recorded-key",
                                      "context": {"flow": "FanoutRepair", "inputs": {
                                          "complete": True, "config_sha256": "historical-hash"},
                                          "verification_policy": {
                                              "rtl_netlist_equivalence_requested": True}}},
            "evaluation_inputs": {"complete": True, "config_sha256": "historical-hash"},
            "verification_requested": True,
            "equivalence": {"equivalent": False, "vacuous": False,
                            "diagnosis": "PROOF_SECRET"},
            "seconds": 12.5,
        }
        snap["seed"].update(evidence)
        snap["case"]["iterations"][0]["results"][0].update(evidence)
        for policy in context.POLICIES:
            with self.subTest(policy=policy):
                view = context.capsule(snap, policy)
                self.assertEqual(view["current"]["search_contract"], contract)
                self.assertEqual(view["current"]["evaluation_budget"]["started_evaluations"], 1)
                seed = context.results(view["current"])[0]
                self.assertEqual(seed["evaluation_provenance"], evidence["evaluation_provenance"])
                self.assertEqual(seed["evaluation_inputs"], evidence["evaluation_inputs"])
                self.assertTrue(seed["verification_requested"])
                self.assertIs(seed["equivalence"]["equivalent"], False)
                self.assertEqual(seed["seconds"], 12.5)
                self.assertNotIn("SECRET", context.canonical(view["current"]))

    def test_refresh_does_not_invent_legacy_provenance_or_obligations(self):
        for policy in context.POLICIES:
            with self.subTest(policy=policy):
                view = context.capsule(frozen(self.inputs), policy)
                seed = context.results(view["current"])[0]
                self.assertNotIn("evaluation_provenance", seed)
                self.assertNotIn("equivalence", seed)
                self.assertNotIn("verification_requested", seed)
                self.assertNotIn("search_contract", view["current"])

    def test_hidden_duplicate_and_visible_citations(self):
        inputs = self.inputs
        proposal = {"diagnosis": "test", "candidates": [{"tag": "next", "overrides": {"FP_CORE_UTIL": 40},
                    "hypothesis": "test utilization", "evidence_tags": ["old"]}]}
        score = evaluator.score_response(frozen(inputs), "seed_only", proposal, 3)
        row = score["candidates"][0]
        self.assertTrue(row["prior_config_signature_duplicate"])
        self.assertEqual(row["errors"], ["evidence tags must cite visible candidates"])
        self.assertIsNone(score["ppa_improvement"])
        proposal["candidates"][0]["overrides"] = {"CLOCK_PERIOD": 20}
        self.assertIn("override outside allowed tuning keys", evaluator.score_response(
            frozen(inputs), "full_history", proposal, 3)["candidates"][0]["errors"])

    def test_prepare_score_integrity_and_missing(self):
        inputs = self.inputs
        manifest = evaluator.prepare(inputs)
        self.assertEqual(len(manifest["trials"]), 6)
        self.assertTrue(all(sum(t["policy"] == p for t in manifest["trials"]) == 2
                            for p in context.POLICIES))
        with self.assertRaises(FileExistsError):
            evaluator.prepare(inputs)
        responses = self.tmp_path / "responses"
        responses.mkdir()
        (responses / (manifest["trials"][0]["id"] + ".json")).write_text("not json")
        args = argparse.Namespace(experiment=inputs.output_dir, responses=responses,
                                  output=self.tmp_path / "score.json")
        report = evaluator.score(args)
        self.assertEqual(report["trials"][0]["status"], "invalid_response")
        self.assertEqual(report["trials"][1]["status"], "missing")
        self.assertIsNone(report["cost"])
        path = inputs.output_dir / manifest["trials"][0]["prompt"]
        path.write_text("tampered")
        with self.assertRaisesRegex(ValueError, "integrity"):
            evaluator.score(args)

    def test_request_opt_in(self):
        import request_review
        inputs = self.inputs
        before = inputs.case_file.read_bytes()
        inputs.design = "demo"
        inputs.context_policy = "fresh_evidence"
        inputs.output_dir = self.tmp_path / "requests"
        request_review.cmd_request(inputs)
        requests = list(inputs.output_dir.glob("*__request.md"))
        self.assertEqual(len(requests), 1)
        text = requests[0].read_text(encoding="utf-8")
        self.assertNotIn("SECRET", text)
        self.assertIn('"old"', text)
        self.assertEqual(inputs.case_file.read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
