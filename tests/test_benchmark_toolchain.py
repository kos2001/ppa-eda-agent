import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
import tempfile
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import benchmark_toolchain as benchmark


class PreflightTests(unittest.TestCase):
    def test_changed_or_deleted_input_cannot_be_reported_as_the_original_design(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "design.v"
            source.write_text("original design")
            expected = {source.name: benchmark.sha256(source)}
            benchmark.verify_inputs(root, expected)
            source.write_text("different design")
            with self.assertRaisesRegex(RuntimeError, "inputs changed"):
                benchmark.verify_inputs(root, expected)
            source.unlink()
            with self.assertRaisesRegex(RuntimeError, "inputs changed"):
                benchmark.verify_inputs(root, expected)

    def test_low_space_is_rejected_before_docker_is_started(self):
        with patch.object(benchmark.shutil, "disk_usage", return_value=SimpleNamespace(free=1024**3)):
            with self.assertRaisesRegex(RuntimeError, "only 1.00 GiB free"):
                benchmark.require_capacity([Path(".")], 3)

    def test_tools_must_match_the_profile_before_comparison(self):
        responses = [SimpleNamespace(stdout=json.dumps([{"Id": "image-id"}])),
                     SimpleNamespace(stdout=json.dumps({"openroad": "wrong-revision"}))]
        with patch.object(benchmark.subprocess, "run", side_effect=responses) as run:
            with self.assertRaisesRegex(RuntimeError, "revision differs"):
                benchmark.inspect_tools()
        self.assertIn("--pull=never", run.call_args.args[0])

    def test_records_observed_versions_and_digest(self):
        expected = benchmark.toolchain_info()["expected_openroad_revision"]
        responses = [SimpleNamespace(stdout=json.dumps([
            {"Id": "image-id", "RepoDigests": ["image@sha256:abc"]}])),
            SimpleNamespace(stdout=json.dumps({"openroad": expected, "arch": "aarch64"}))]
        with patch.object(benchmark.subprocess, "run", side_effect=responses):
            result = benchmark.inspect_tools()
        self.assertEqual(result["image_id"], "image-id")
        self.assertEqual(result["versions"]["openroad"], expected)
        self.assertEqual(result["repo_digests"], ["image@sha256:abc"])
