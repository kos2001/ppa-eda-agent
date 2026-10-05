"""Reference-inspired visualization must conserve geometry and source boundaries."""
import json
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VisualizationTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("npx"), "npx required")
    def test_geometric_coverage_and_comparison_provenance(self):
        result = subprocess.run(["npx", "tsx", str(ROOT / "tests/eda_visualization_check.mjs")],
                                cwd=ROOT / "dashboard", capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(json.loads(result.stdout).values()))

    @unittest.skipUnless(shutil.which("node"), "Node required")
    def test_report_inventory_cannot_read_unrecorded_host_paths(self):
        result = subprocess.run(["node", str(ROOT / "tests/artifacts_check.mjs")],
                                cwd=ROOT, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(all(json.loads(result.stdout).values()))
