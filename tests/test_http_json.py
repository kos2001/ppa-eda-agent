import pathlib
import shutil
import subprocess
import unittest


class HttpJsonTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_utf8_validation_and_size_limits(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        subprocess.run(["node", str(root / "tests/http_json_check.mjs")],
                       cwd=root, check=True, capture_output=True, text=True, timeout=30)
