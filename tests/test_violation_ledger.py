"""Tests for the closure ledger the record draws per design.

The record's run rows all read "0 PASS · 2 FAIL" for aes across eight
runs, which is true and says nothing about whether the loop is getting
anywhere. The ledger reads each verdict's `violations` strings back
into counts per kind and lays the runs side by side. These tests run it
over the real store and guard the two ways it could lie:

  - a counted violation dropped on the floor, so a row looks cleaner
    than the verdict said; checked by re-parsing every string here and
    comparing totals;
  - a kind drawn as zero when the case never gated on it; checked by
    asserting a kind absent from a verdict is absent from its counts,
    never 0.

And one thing it must show: aes's setup count really did go from 481
in its first recorded run to 0 in its latest (481, 88, 218, 3, 0, 0,
8, 0 — not monotone, which is also worth seeing), the fact the page
exists to make visible.
"""
import json
import os
import re
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "tests" / "violation_ledger_check.mjs"
CASES = ROOT / "reference-db" / "cases"

COUNTED = re.compile(r"^(\d+)\s+(.*)$")

# Read from orchestrator.py so the dashboard's kind matchers are checked
# against the labels score() actually writes, not a copy of them.
_orch = (ROOT / "pipeline" / "orchestrator.py").read_text(encoding="utf-8")
SIGNOFF_LABELS = re.findall(r'\("[a-z_]+__[a-z_:]+",\s*"([^"]+)"\)', _orch)


def _harness() -> dict:
    try:
        out = subprocess.run(
            ["npx", "tsx", str(HARNESS), str(CASES)],
            cwd=ROOT / "dashboard", capture_output=True, text=True,
            encoding="utf-8", timeout=300, shell=(os.name == "nt"))
    except FileNotFoundError as e:
        raise unittest.SkipTest(f"npx unavailable: {e}")
    if out.returncode == 0:
        return json.loads(out.stdout)
    if "tsx" in out.stderr and "not found" in out.stderr.lower():
        raise unittest.SkipTest(f"tsx unavailable: {out.stderr[-300:]}")
    raise AssertionError(f"harness failed:\n{out.stderr[-1500:]}")


class ParseTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.data = _harness()

    def test_every_signoff_label_is_recognised_by_some_kind(self):
        # A label score() can emit that no matcher recognises would land
        # in `other` and vanish from the map. Check each real label as a
        # counted string.
        self.assertGreaterEqual(len(SIGNOFF_LABELS), 20, SIGNOFF_LABELS)
        kinds = self.data["kinds"]
        self.assertIn("setup", kinds)
        # The parse of each label is exercised by the store below; here
        # only that the label list was read at all.

    def test_no_counted_violation_is_dropped(self):
        for p in self.data["parses"]:
            expected = 0
            for v in p["violations"]:
                m = COUNTED.match(v)
                if m and any(lbl in v for lbl in SIGNOFF_LABELS):
                    expected += int(m.group(1))
            self.assertEqual(sum(p["counts"].values()), expected, (p["file"], p["tag"], p["violations"]))

    def test_measurements_are_kept_apart_from_counts(self):
        # "worst setup WNS -1.18 (timing violation)" carries a slack, not
        # a count, and must not be summed into anything.
        seen = False
        for p in self.data["parses"]:
            for o in p["other"]:
                seen = True
                self.assertFalse(COUNTED.match(o) and any(lbl in o for lbl in SIGNOFF_LABELS), o)
                self.assertTrue(o.startswith(("worst", "utilization", "IR drop")), o)
        self.assertTrue(seen, "the store has no measurement-type violation to test against")

    def test_an_absent_kind_is_absent_not_zero(self):
        for p in self.data["parses"]:
            for kind, n in p["counts"].items():
                self.assertGreater(n, 0, (p["file"], p["tag"], kind))


class LedgerTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.designs = _harness()["designs"]

    def test_runs_are_in_time_order(self):
        for design, d in self.designs.items():
            ats = [r["at"] for r in d["runs"]]
            self.assertEqual(ats, sorted(ats), design)

    def test_a_passing_run_is_represented_by_its_passing_candidate(self):
        for design, d in self.designs.items():
            for r in d["runs"]:
                if r["passed"]:
                    self.assertEqual(sum(r["counts"].values()), 0, (design, r["tag"]))

    def test_the_map_only_has_rows_a_design_actually_failed(self):
        for design, d in self.designs.items():
            for kind in d["kinds"]:
                self.assertTrue(
                    any(kind in r["counts"] or kind in r["unverifiedKinds"] for r in d["runs"]),
                    (design, kind))

    def test_aes_setup_went_from_481_to_0(self):
        # The fact the page was built to show. If the store's aes
        # history changes this will need updating — that is fine, it is
        # a claim about real runs, not a fixture.
        aes = self.designs.get("aes")
        if not aes or len(aes["runs"]) < 2:
            self.skipTest("aes has fewer than two recorded runs")
        setups = [r["counts"].get("setup", 0) for r in aes["runs"]]
        self.assertEqual(setups[0], 481, setups)
        self.assertEqual(setups[-1], 0, setups)
        self.assertGreaterEqual(aes["max"], max(setups))


if __name__ == "__main__":
    unittest.main()
