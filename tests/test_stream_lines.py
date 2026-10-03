import pathlib
import shutil
import subprocess
import unittest


class StreamLineTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node.js is required")
    def test_every_chunk_boundary_preserves_lines_and_utf8(self):
        root = pathlib.Path(__file__).resolve().parents[1]
        subprocess.run(["node", str(root / "tests/stream_lines_check.mjs")],
                       cwd=root, check=True, capture_output=True, text=True, timeout=30)
