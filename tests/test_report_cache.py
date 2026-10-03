import pathlib
import shutil
import subprocess
import unittest


class ReportCacheTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_invalidation_concurrency_and_failure_recovery(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        subprocess.run(["node", str(root / "tests/report_cache_check.mjs")],
                       cwd=root, check=True, capture_output=True, text=True, timeout=30)
