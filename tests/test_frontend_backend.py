"""Independent frontend/API boundaries and truthful overview counts."""
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class FrontendBackendTests(unittest.TestCase):
    def test_api_runs_from_its_package_with_custom_port_and_origin(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = "http://127.0.0.1:5174"
        env = {**os.environ, "PPA_EDA_SERVER_PORT": str(port), "PPA_EDA_FRONTEND_ORIGINS": origin}
        process = subprocess.Popen(["node", "index.mjs"], cwd=ROOT / "server", env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        base = f"http://127.0.0.1:{port}"
        try:
            for _ in range(100):
                self.assertIsNone(process.poll(), "API exited during startup")
                try:
                    with urlopen(base + "/health", timeout=1) as response:
                        health = json.load(response)
                    break
                except URLError:
                    time.sleep(0.05)
            else:
                self.fail("API startup timed out")
            self.assertEqual(health, {"status": "ok", "service": "ppa-eda-api", "api_version": 1})
            with urlopen(Request(base + "/examples", headers={"Origin": origin}), timeout=5) as response:
                self.assertEqual(response.headers["Access-Control-Allow-Origin"], origin)
                self.assertEqual(response.headers["Vary"], "Origin")
                examples = json.load(response)["examples"]
            configs = {p.parent.name for p in (ROOT / "pipeline/designs").glob("*/config.json")}
            self.assertEqual({row["design"] for row in examples}, configs)
            for row in examples:
                self.assertTrue((ROOT / row["config_path"]).is_file())
                self.assertEqual(row["runnable"], (ROOT / row["config_path"]).with_name("run_spec.json").exists())
            preflight = Request(base + "/pipeline/run", method="OPTIONS", headers={"Origin": origin})
            with urlopen(preflight, timeout=2) as response:
                self.assertEqual(response.status, 204)
                self.assertIn("POST", response.headers["Access-Control-Allow-Methods"])
            for denied in ["http://127.0.0.1:5175", "https://untrusted.example"]:
                with urlopen(Request(base + "/health", headers={"Origin": denied}), timeout=2) as response:
                    self.assertIsNone(response.headers.get("Access-Control-Allow-Origin"))
            with self.assertRaises(HTTPError) as error:
                urlopen(base + "/index.html", timeout=2)
            self.assertEqual(error.exception.code, 404)
            error.exception.close()
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    @unittest.skipUnless(shutil.which("npx"), "npx is required")
    def test_frontend_custom_urls_and_measured_summary(self):
        result = subprocess.run(["npx", "tsx", str(ROOT / "tests/frontend_boundary_check.mjs"),
                                 str(ROOT / "reference-db/cases")], cwd=ROOT / "dashboard",
                                capture_output=True, text=True, timeout=60, shell=os.name == "nt")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(json.loads(result.stdout)["total"], 0)
