"""Building the renderer must never traverse the repository's PDK links."""
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pipeline"))
import custom_bridge as bridge


class XschemImageTests(unittest.TestCase):
    @patch.object(bridge.shutil, "which", return_value="docker")
    @patch.object(bridge, "_image_present", return_value=True)
    @patch.object(bridge.subprocess, "run")
    def test_existing_image_is_reused(self, run, _present, _which):
        self.assertTrue(bridge.ensure_xschem_image())
        run.assert_not_called()

    @patch.object(bridge.shutil, "which", return_value="docker")
    @patch.object(bridge, "_image_present", return_value=False)
    @patch.object(bridge.subprocess, "run")
    def test_dockerfile_is_the_entire_build_input(self, run, _present, _which):
        run.return_value = subprocess.CompletedProcess([], 0, "sha256:test", "")
        self.assertTrue(bridge.ensure_xschem_image(timeout=42))
        self.assertEqual(run.call_args.args[0],
                         ["docker", "build", "-q", "-t", bridge.XSCHEM_IMAGE, "-"])
        self.assertEqual(run.call_args.kwargs["input"],
                         bridge.XSCHEM_DOCKERFILE.read_text(encoding="utf-8"))
        self.assertEqual(run.call_args.kwargs["timeout"], 42)
        self.assertNotIn("stdin", run.call_args.kwargs)

    @patch.object(bridge.shutil, "which", return_value="docker")
    @patch.object(bridge, "_image_present", return_value=False)
    @patch.object(bridge.subprocess, "run")
    def test_failed_build_is_not_reported_as_available(self, run, _present, _which):
        run.return_value = subprocess.CompletedProcess([], 1, "", "build failed")
        diagnostics = []
        self.assertFalse(bridge.ensure_xschem_image(diagnostics=diagnostics))
        self.assertEqual(diagnostics, ["build failed"])
        run.side_effect = subprocess.TimeoutExpired("docker", 42)
        self.assertFalse(bridge.ensure_xschem_image(timeout=42))


if __name__ == "__main__":
    unittest.main()
