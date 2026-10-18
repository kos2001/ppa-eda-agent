import argparse
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import harness_probe


class HarnessProbeTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        case = {"design": "demo", "iterations": [{"results": [
            {"tag": "seed", "overrides": {"FP_CORE_UTIL": 35},
             "verdict": {"passed": False, "violations": ["setup"]}}]}]}
        (self.root / "demo__1.json").write_text(json.dumps(case))
        (self.root / "config.json").write_text('{"CLOCK_PERIOD":10}')
        (self.root / "guidance.json").write_text(json.dumps({
            "general": "Cite a measured candidate for each hypothesis.",
            "physical": "Inspect the recorded timing corner before proposing density changes."}))
        self.experiment = self.root / "experiment"
        self.args = argparse.Namespace(case_file=self.root / "demo__1.json",
            design_config=self.root / "config.json", seed_tag="seed",
            allow_override=["FP_CORE_UTIL"], guidance=self.root / "guidance.json",
            policy="seed_only", history_limit=0, candidate_limit=1,
            repetitions=2, random_seed=4, output_dir=self.experiment)

    def test_paired_probe_and_composition_require_matching_frozen_inputs(self):
        manifest = harness_probe.prepare(self.args)
        self.assertEqual(len(manifest["trials"]), 6)
        responses = self.root / "responses"
        responses.mkdir()
        valid = {"diagnosis": "measured setup failure", "candidates": [{
            "tag": "next", "overrides": {"FP_CORE_UTIL": 40},
            "hypothesis": "test lower density", "evidence_tags": ["seed"]}]}
        for trial in manifest["trials"]:
            response = valid if trial["arm"] != "base" else {"diagnosis": "unknown", "candidates": []}
            (responses / (trial["id"] + ".json")).write_text(json.dumps(response))
        score_path = self.root / "score.json"
        report = harness_probe.score(argparse.Namespace(experiment=self.experiment,
                                                        responses=responses, output=score_path))
        self.assertEqual(len(report["contrasts"]), 4)
        self.assertTrue(all(row["novel_signature_delta"] == 1 for row in report["contrasts"]))
        self.assertIsNone(report["ppa_improvement"])
        output = self.root / "compose.txt"
        harness_probe.compose(argparse.Namespace(experiment=self.experiment,
                                                  score=score_path, responses=responses,
                                                  output=output))
        self.assertIn("Do not interpret structural", output.read_text())
        first_response = responses / (manifest["trials"][0]["id"] + ".json")
        first_response.write_text('{"diagnosis":"changed","candidates":[]}')
        with self.assertRaisesRegex(ValueError, "response changed"):
            harness_probe.compose(argparse.Namespace(experiment=self.experiment,
                                                      score=score_path, responses=responses,
                                                      output=self.root / "compose-again.txt"))
        (self.experiment / manifest["trials"][0]["prompt"]).write_text("tampered")
        with self.assertRaisesRegex(ValueError, "frozen input changed"):
            harness_probe.score(argparse.Namespace(experiment=self.experiment,
                                                    responses=responses, output=self.root / "other.json"))

    def test_missing_response_is_unknown_and_no_composition(self):
        harness_probe.prepare(self.args)
        responses = self.root / "responses"
        responses.mkdir()
        score_path = self.root / "score.json"
        report = harness_probe.score(argparse.Namespace(experiment=self.experiment,
                                                        responses=responses, output=score_path))
        self.assertTrue(all(row["status"] == "unpaired" for row in report["contrasts"]))
        with self.assertRaisesRegex(ValueError, "completed"):
            harness_probe.compose(argparse.Namespace(experiment=self.experiment,
                                                      score=score_path, responses=responses,
                                                      output=self.root / "compose.txt"))


if __name__ == "__main__":
    unittest.main()
