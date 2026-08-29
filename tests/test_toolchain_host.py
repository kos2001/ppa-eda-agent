"""Tests for the container platform choice and the host recorded with it.

The pipeline forced `--platform linux/amd64` from its first commit. On an
arm64 host that emulates x86, and nothing recorded that it was happening
— the case stored the OpenLane image and not one fact about the machine.
So an emulated run and a native run of the same image were compared as
though identical, and the per-candidate timings published in the manual
had no stated environment at all.

Measured on counter4: 68 s emulated against 27 s native through the real
wrapper, with 278 of 279 metrics identical. The exception is
power__leakage__total, differing in its tenth significant figure on a
0.5 nW number — floating-point summation order, not a different result.

Idea borrowed from freerouting's benchmark table
(github.com/freerouting/freerouting, docs/benchmarks.md), which states
CPU, RAM and OS above its numbers and marks unmeasured cells rather than
leaving them blank.
"""
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import toolchain  # noqa: E402


class PlatformArgsTests(unittest.TestCase):
    def tearDown(self):
        # The module reads the environment once at import; these tests
        # set the module attribute directly, so it has to be restored.
        toolchain.DOCKER_PLATFORM = os.environ.get(
            "PPA_EDA_DOCKER_PLATFORM", "").strip()

    def test_native_by_default_passes_no_platform_flag(self):
        toolchain.DOCKER_PLATFORM = ""
        self.assertEqual(toolchain.platform_args(), [])

    def test_an_explicit_platform_is_forwarded(self):
        toolchain.DOCKER_PLATFORM = "linux/amd64"
        self.assertEqual(toolchain.platform_args(), ["--platform", "linux/amd64"])

    def test_the_flag_is_a_pair_docker_can_consume(self):
        # Spliced into a command list with *, so a malformed value would
        # produce a command that fails opaquely inside docker.
        toolchain.DOCKER_PLATFORM = "linux/arm64"
        args = toolchain.platform_args()
        self.assertEqual(len(args), 2)
        self.assertEqual(args[0], "--platform")


class HostInfoTests(unittest.TestCase):
    def setUp(self):
        self.info = toolchain.host_info()

    def test_records_what_makes_results_comparable(self):
        for key in ("arch", "system", "cpu_count", "docker_platform"):
            self.assertIn(key, self.info)
        self.assertTrue(self.info["arch"])
        self.assertIsInstance(self.info["cpu_count"], int)

    def test_says_whether_the_platform_was_forced(self):
        # "native" and a forced triple are different facts about a run,
        # and on an arm64 host they differ by 2.5x in wall-clock time.
        self.assertTrue(self.info["docker_platform"])

    def test_carries_no_hostname_or_user(self):
        # Architecture and core count make results comparable; who ran
        # them does not, and putting it in a committed store is a leak.
        joined = " ".join(str(v).lower() for v in self.info.values())
        for leaky in ("/users/", "/home/", os.environ.get("USER", "\x00").lower()):
            if leaky and leaky != "\x00":
                self.assertNotIn(leaky, joined)

    def test_toolchain_info_includes_the_host(self):
        got = toolchain.toolchain_info()
        self.assertIn("host", got)
        self.assertEqual(got["host"]["arch"], self.info["arch"])
        # The image is still the primary fact; the host is context for it.
        self.assertIn("openlane_image", got)


class CallSiteTests(unittest.TestCase):
    """Every docker invocation must go through the shared chooser.

    Six modules build their own `docker run`. The hardcoded platform was
    duplicated across all of them, which is how it survived unexamined
    from the first commit — the same reason toolchain.py exists at all.
    """

    MODULES = ("run_stage.py", "render_layout.py", "odb_query.py",
               "equiv_check.py", "synth_explore.py", "macro_place.py")

    def test_no_module_hardcodes_a_platform(self):
        root = Path(__file__).resolve().parent.parent / "pipeline"
        for name in self.MODULES:
            text = (root / name).read_text()
            self.assertNotIn('"--platform", "linux/amd64"', text, name)

    def test_every_docker_caller_imports_the_chooser(self):
        root = Path(__file__).resolve().parent.parent / "pipeline"
        for name in self.MODULES:
            text = (root / name).read_text()
            if '"docker", "run"' not in text:
                continue
            self.assertIn("platform_args", text, name)


if __name__ == "__main__":
    unittest.main()
