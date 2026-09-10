"""The run-cost figure the console shows must be the one the pipeline
plans with.

The manual's answer to "how long does a run take?" was a sentence typed
by hand — "about 27 s for counter4" — and the first feedback entry the
manual received said it could not tell them what a large design costs.
The number was already in the store: collect.py's run_one() writes
`seconds` on every result, and recorded_seconds() takes the per-design
median to plan batches. The dashboard now computes the same median in
the browser (dashboard/src/components/runCost.ts).

Two implementations of one number is a way to disagree by a rounding
rule, so this pins them to each other over the real store.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "run_cost_check.mjs"
CASES = ROOT / "reference-db" / "cases"

sys.path.insert(0, str(ROOT / "pipeline"))
import collect  # noqa: E402


def _harness() -> dict:
    try:
        out = subprocess.run(
            ["npx", "tsx", str(HARNESS), str(CASES)],
            cwd=ROOT / "dashboard", capture_output=True, text=True,
            encoding="utf-8", timeout=300,
            shell=(os.name == "nt"))
    except FileNotFoundError as e:
        # On Windows npx is npx.cmd, which needs a shell to exec at all.
        raise unittest.SkipTest(f"npx unavailable: {e}")
    if out.returncode == 0:
        return json.loads(out.stdout)
    if "tsx" in out.stderr and "not found" in out.stderr.lower():
        raise unittest.SkipTest(f"tsx unavailable: {out.stderr[-300:]}")
    raise AssertionError(f"harness failed:\n{out.stderr[-1500:]}")


class RunCostTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.dashboard = _harness()
        cls.pipeline = collect.recorded_seconds()

    def test_the_store_has_timed_runs_to_show(self):
        # Writing this first asserted aes was timed, and it is not: its
        # cases were recovered from run directories after a killed batch
        # and carry no `seconds`. So the table honestly shows "no timed
        # run" for the one design the feedback asked about, until a run
        # of it is recorded through a path that times it. That is the
        # answer the store can give; this only checks it gives one.
        self.assertTrue(self.pipeline)
        for design, seconds in self.pipeline.items():
            self.assertGreater(seconds, 0, design)

    def test_same_designs_are_timed(self):
        self.assertEqual(set(self.dashboard), set(self.pipeline))

    def test_same_median_per_design(self):
        for design, seconds in self.pipeline.items():
            self.assertAlmostEqual(
                self.dashboard[design]["medianSeconds"], seconds, places=6,
                msg=design)

    def test_every_figure_says_which_machine_it_came_from(self):
        # A number without its machine reads as a property of the
        # design; the same counter4 run measured 68 s emulated and 28 s
        # native. Only cases that recorded a host can say — and every
        # timed run in the store came after toolchain_info() was added.
        for design, row in self.dashboard.items():
            self.assertTrue(row["hosts"], design)


if __name__ == "__main__":
    unittest.main()
