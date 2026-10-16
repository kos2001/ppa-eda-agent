from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class SchematicJobTests(unittest.TestCase):
    def test_background_jobs(self):
        subprocess.run(["node", "tests/schematic_jobs_check.mjs"], cwd=ROOT,
                       check=True, capture_output=True, text=True, timeout=30)

    def test_drawing_client(self):
        tsx = ROOT / "dashboard/node_modules/tsx/dist/cli.mjs"
        if not tsx.is_file():
            self.skipTest("Install dashboard dependencies to test the TypeScript client")
        subprocess.run(["node", str(tsx), "tests/schematic_drawing_check.mts"], cwd=ROOT,
                       check=True, capture_output=True, text=True, timeout=30)
