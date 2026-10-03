import os
import http.client
from pathlib import Path
import shutil
import socket
import subprocess
import time
import unittest
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@unittest.skipUnless(shutil.which("node"), "Node.js is required")
class ServerHttpTests(unittest.TestCase):
    @staticmethod
    def post(url, body):
        return urlopen(Request(url, data=body, method="POST",
                               headers={"Content-Type": "application/json"}), timeout=10)

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

            for body in [b"{", b"null", b"[]"]:
                with self.assertRaises(HTTPError) as error:
                    self.post(base + "/feedback", body)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.putrequest("POST", "/feedback")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(8 * 1024 * 1024 + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
            connection.close()
            with self.assertRaises(HTTPError) as error:
                self.post(base + "/pipeline/run", b'{"design":"counter4","maxIterations":-1}')
            self.assertEqual(error.exception.code, 400)
            error.exception.close()
            with urlopen(base + "/gateway-status", timeout=2) as response:
                self.assertEqual(response.status, 200)
        finally:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)

    def test_hermes_gateway_bounds_and_validates_authenticated_json(self):
        root = Path(__file__).resolve().parents[1]
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = {
            **os.environ,
            "HERMES_GATEWAY_PORT": str(port),
            "HERMES_GATEWAY_HOST": "127.0.0.1",
            "HERMES_GATEWAY_KEY": "test-only-key",
        }
        process = subprocess.Popen(["node", "server/hermes-gateway.mjs"], cwd=root, env=env,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        connection = None
        try:
            for _ in range(100):
                self.assertIsNone(process.poll(), "gateway exited during startup")
                try:
                    request = Request(
                        f"http://127.0.0.1:{port}/v1/chat/completions",
                        data=b"{}", method="POST",
                        headers={"Content-Type": "application/json"},
                    )
                    with self.assertRaises(HTTPError) as error:
                        urlopen(request, timeout=1)
                    self.assertEqual(error.exception.code, 401)
                    error.exception.close()
                    break
                except URLError:
                    time.sleep(0.05)
            else:
                self.fail("gateway startup timed out")

            for body in [b"{", b"null", b"[]", b"{}"]:
                request = Request(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    data=body, method="POST",
                    headers={
                        "Content-Type": "application/json",
                        "Authorization": "Bearer test-only-key",
                    },
                )
                with self.assertRaises(HTTPError) as error:
                    urlopen(request, timeout=2)
                self.assertEqual(error.exception.code, 400)
                error.exception.close()

            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            connection.putrequest("POST", "/v1/chat/completions")
            connection.putheader("Authorization", "Bearer test-only-key")
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(8 * 1024 * 1024 + 1))
            connection.endheaders()
            response = connection.getresponse()
            self.assertEqual(response.status, 413)
            response.read()
        finally:
            if connection is not None:
                connection.close()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
