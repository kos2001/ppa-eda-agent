import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import urlopen


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ServerHttpTests(unittest.TestCase):
    def test_malformed_layout_url_does_not_stop_server(self):
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {**os.environ, "PPA_EDA_SERVER_PORT": str(port)}
        process = subprocess.Popen(["node", "server/index.mjs"], cwd=root, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                self.assertIsNone(process.poll(), "server exited during startup")
                try:
                    with urlopen(base + "/gateway-status", timeout=1) as response:
                        self.assertEqual(response.status, 200)
                    break
                except URLError:
                    time.sleep(0.05)
            else:
                self.fail("server startup timed out")
            for name in ["%", "%FF.png", "%2Fsecret.png"]:
                with self.assertRaises(HTTPError) as error:
                    urlopen(base + "/reference-db/layouts/" + name, timeout=2)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
                with urlopen(base + "/gateway-status", timeout=2) as response:
                    self.assertEqual(response.status, 200)
            image = next((root / "reference-db/layouts").glob("*.png"))
            with urlopen(base + "/reference-db/layouts/" + image.name + "?v=1", timeout=2) as response:
                self.assertEqual(response.headers["Content-Type"], "image/png")
                self.assertEqual(response.read(8), b"\x89PNG\r\n\x1a\n")
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
