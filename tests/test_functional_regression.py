"""Runner contracts; real Verilator and mutation checks are recorded separately."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import functional_regression as regression
import orchestrator


class FunctionalRegressionTests(unittest.TestCase):
    def test_failed_and_incomplete_regression_have_distinct_retrieval_signatures(self):
        import case_retrieval
        failed = case_retrieval.signoff_signatures({"violations": ["Functional regression failed"]})
        for message in ("Functional regression incomplete", "Verilator functional regression incomplete"):
            incomplete = case_retrieval.unverified_signatures({"unverified": [message]})
            self.assertEqual(incomplete, {"unverified:functional-regression"})
            self.assertTrue(failed.isdisjoint(incomplete))
        self.assertEqual(failed, {"verification:functional-regression"})

    def test_status_aggregation_requires_evidence_and_preserves_failure(self):
        for states, expected in [([], "unverified"), (["passed"], "passed"),
                                 (["passed", "unverified"], "unverified"),
                                 (["failed", "unverified"], "failed")]:
            with self.subTest(states=states):
                self.assertEqual(regression.combined_status(states), expected)

    def test_snapshot_preserves_bytes_and_hash_for_large_external_model(self):
        import hashlib
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            design = root / "design"
            design.mkdir()
            rtl = design / "rtl.v"
            rtl.write_bytes(b"module rtl; endmodule\n")
            model = root / "model.v"
            payload = bytes(range(256)) * 8193
            model.write_bytes(payload)
            for post_route in (False, True):
                work = root / str(post_route)
                commands, records = regression.snapshot_sources([rtl, model], design, work, post_route)
                self.assertEqual(len(commands), 2)
                for source, record in zip([rtl, model], records):
                    snapshot = work / "source" / record["snapshot"]
                    self.assertEqual(snapshot.read_bytes(), source.read_bytes())
                    self.assertEqual(record["sha256"], hashlib.sha256(source.read_bytes()).hexdigest())

    def test_only_explicit_nonvacuous_success_passes(self):
        result = {"timed_out": False, "returncode": 0}
        for text in ("", "PASS", "PPA_REGRESSION_PASS checks=0", "PPA_REGRESSION_PASS checks=2\nPPA_REGRESSION_PASS checks=2"):
            self.assertEqual(regression.classify(result, text)[0], "unverified")
        self.assertEqual(regression.classify(result, "PPA_REGRESSION_PASS checks=322\n"), ("passed", 322))

    def test_failure_or_timeout_overrides_success_marker(self):
        marker = "PPA_REGRESSION_PASS checks=322"
        self.assertEqual(regression.classify({"returncode": 1, "timed_out": False}, marker)[0], "failed")
        self.assertEqual(regression.classify({"returncode": None, "timed_out": True}, marker)[0], "unverified")

    def test_failure_and_missing_evidence_block_selection(self):
        for status in ("failed", "unverified"):
            verdict = {"passed": True, "violations": [], "unverified": []}
            regression.apply(verdict, {"status": status})
            self.assertFalse(verdict["passed"])
            self.assertIsNone(orchestrator.pick_winner([{"tag": "bad", "verdict": verdict}]))

    def test_success_does_not_erase_physical_failure(self):
        verdict = {"passed": False, "violations": ["DRC"], "unverified": []}
        regression.apply(verdict, {"status": "passed"})
        self.assertFalse(verdict["passed"])

    def test_invalid_settings_rejected_before_flow(self):
        base = {"top": "tb", "testbench": "verify/tb.sv"}
        for extra in ({"seeds": []}, {"seeds": [1, 1]}, {"seeds": [True]},
                      {"top": "--bad"}, {"simulation_timeout_s": 0}):
            with patch.object(orchestrator, "expand_synthesis_exploration") as explore:
                with self.assertRaises(ValueError):
                    orchestrator.orchestrate(Path("unused"), {"functional_regression": {**base, **extra}}, 1)
                explore.assert_not_called()

    def test_missing_files_recorded_as_unverified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = regression.check(root, root / "run", {"top": "tb", "testbench": "missing.sv"})
            self.assertEqual(result["status"], "unverified")
            self.assertTrue((Path(result["artifact_dir"]) / "result.json").is_file())

    def test_rtl_files_follow_config_not_src_glob(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "selected.sv").write_text("module selected; endmodule")
            (root / "config.json").write_text(json.dumps({"VERILOG_FILES": ["dir::selected.sv"]}))
            # checked_path resolves symlinks (macOS /var -> /private/var).
            self.assertEqual(regression.rtl_sources(root), [(root / "selected.sv").resolve()])
            (root / "config.json").write_text(json.dumps({"VERILOG_FILES": ["dir::selected.sv"],
                                                          "VERILOG_DEFINES": ["OTHER_BEHAVIOR"]}))
            with self.assertRaises(ValueError):
                regression.rtl_sources(root)
            with self.assertRaises(ValueError):
                regression.checked_path(root, "../outside.sv")

    def test_timeout_removes_only_named_container(self):
        import subprocess
        with tempfile.TemporaryDirectory() as tmp, patch.object(regression.subprocess, "run") as run:
            run.side_effect = [subprocess.TimeoutExpired("docker", 1), None]
            result = regression.execute(Path(tmp), ["/work/obj/regression"], 1, "sim.log")
            self.assertTrue(result["timed_out"])
            command = run.call_args_list[0].args[0]
            name = command[command.index("--name") + 1]
            self.assertEqual(run.call_args_list[1].args[0], ["docker", "rm", "-f", name])


if __name__ == "__main__":
    unittest.main()
