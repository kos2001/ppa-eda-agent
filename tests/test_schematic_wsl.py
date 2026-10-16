"""Windows-to-WSL rendering must retain native output and failure evidence."""
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import custom_bridge as bridge


class WslRenderingTests(unittest.TestCase):
    def render(self):
        return bridge._render_schematic_wsl(
            "wsl.exe", Path("work space/회로/input.sch"), "sky130A",
            Path("work space/회로/output.circuit.svg"), 60, True)

    @patch.object(Path, "is_file", return_value=True)
    @patch.object(bridge.subprocess, "run")
    def test_paths_are_arguments_and_output_is_mapped_back(self, run, _exists):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({
            "status": "success", "metadata": {"via": "docker", "svg": "/out/output.svg"}
        }), "")
        with patch.dict(bridge.os.environ, {"CUSTOM_BRIDGE_WSL_DISTRO": "Ubuntu"}):
            result = self.render()
        argv = run.call_args.args[0]
        self.assertEqual(argv[:5], ["wsl.exe", "--distribution", "Ubuntu", "--exec", "python3"])
        self.assertIn(str(Path("work space/회로/input.sch")), argv)
        self.assertEqual(argv[-2], "1")
        self.assertFalse(run.call_args.kwargs.get("shell", False))
        self.assertEqual(run.call_args.kwargs["encoding"], "utf-8")
        self.assertTrue(result.ok)
        self.assertEqual(result.metadata["via"], "wsl/docker")
        self.assertEqual(result.metadata["svg"], str(Path("work space/회로/output.circuit.svg")))

    @patch.object(Path, "is_file", return_value=False)
    @patch.object(bridge.subprocess, "run")
    def test_success_without_artifact_is_rejected(self, run, _exists):
        run.return_value = subprocess.CompletedProcess([], 0, '{"status":"success"}', "")
        result = self.render()
        self.assertFalse(result.ok)
        self.assertIn("wrote no SVG", result.errors[0])

    @patch.object(Path, "is_file", return_value=False)
    @patch.object(bridge.subprocess, "run")
    def test_renderer_failure_is_preserved(self, run, _exists):
        run.return_value = subprocess.CompletedProcess([], 0, json.dumps({
            "status": "failure", "errors": ["missing symbol"],
            "metadata": {"reason": "missing_symbol", "via": "docker"}
        }), "")
        result = self.render()
        self.assertFalse(result.ok)
        self.assertEqual(result.errors, ["missing symbol"])
        self.assertEqual(result.metadata["reason"], "missing_symbol")

    @patch.object(bridge.subprocess, "run")
    def test_wsl_failure_and_invalid_output_are_errors(self, run):
        for process in [subprocess.CompletedProcess([], 1, "", "WSL unavailable"),
                        subprocess.CompletedProcess([], 0, "not JSON", "")]:
            with self.subTest(process=process):
                run.return_value = process
                result = self.render()
                self.assertFalse(result.ok)
                self.assertEqual(result.metadata["reason"], "wsl_render_failed")

    @patch.object(bridge.subprocess, "run", side_effect=subprocess.TimeoutExpired("wsl", 60))
    def test_timeout_is_a_structured_error(self, _run):
        result = self.render()
        self.assertFalse(result.ok)
        self.assertEqual(result.metadata["reason"], "wsl_render_failed")


if __name__ == "__main__":
    unittest.main()
