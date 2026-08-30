"""Tests for what a review request carries, and what it overwrites.

Two things went wrong the first time this pipeline reviewed a design
twice in one day, and both are about the same mistake: treating a design
as if it had one case.

  - THE PRIOR REVIEW WAS LOST. A review is recorded into the case file it
    reviewed. orchestrator.py writes a NEW case file per run, so the
    second run's request read "Existing diagnosis: (none recorded yet)"
    and "No prior case shares this one's failure signature — treat it as
    new", while the case one run earlier held a 3,135-character verdict
    that had proposed the very candidates just executed. The reviewer was
    asked not to "re-derive what's already known" and was handed nothing
    to know it from.

  - THE PRIOR REQUEST WAS OVERWRITTEN. The filename is built from the
    case's `date`, and a design run twice on one day produces two cases
    with the same date. The second request silently replaced the first
    on disk — including one already committed.

Both are fixed by naming the case, not the day: the request file is
keyed to the case it is about, and the request carries the most recent
recorded review from the design's earlier cases when its own case has
none.
"""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import request_review  # noqa: E402


def _case(name, date, **over):
    base = {"design": name, "date": date, "iterations": [], "winner_tag": None}
    base.update(over)
    return base


class RequestFilenameTests(unittest.TestCase):
    """One request file per case, not per day."""

    def test_two_cases_on_one_day_do_not_share_a_request_file(self):
        # aes ran twice on 2026-08-30. Both cases carry date
        # "2026-08-30", so a name built from the date collides and the
        # second request overwrites the first — a file that was already
        # committed, describing a different case.
        first = request_review.request_filename(
            "aes", _case("aes", "2026-08-30"), "aes__2026-08-30.json")
        second = request_review.request_filename(
            "aes", _case("aes", "2026-08-30"), "aes__2026-08-30__134751.json")
        self.assertNotEqual(first, second)

    def test_the_name_identifies_the_case_it_is_about(self):
        name = request_review.request_filename(
            "aes", _case("aes", "2026-08-30"), "aes__2026-08-30__134751.json")
        self.assertIn("134751", name)
        self.assertTrue(name.endswith("__request.md"), name)

    def test_a_dated_case_keeps_the_name_it_always_had(self):
        # The four requests already committed are named this way. A fix
        # that renamed them would orphan them from the cases they
        # describe, which is the problem it is supposed to solve.
        name = request_review.request_filename(
            "aes", _case("aes", "2026-08-30"), "aes__2026-08-30.json")
        self.assertEqual(name, "aes__2026-08-30__request.md")


class CarriedDiagnosisTests(unittest.TestCase):
    """What the reviewer is told about the runs before this one."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.refdb = Path(self.tmp.name)
        (self.refdb / "cases").mkdir()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, filename, case):
        (self.refdb / "cases" / filename).write_text(json.dumps(case))

    def test_an_earlier_case_review_is_carried_into_a_later_request(self):
        self._write("aes__2026-08-30.json", _case(
            "aes", "2026-08-30",
            diagnosis="[ts] hermes-review: try DELAY 0 at CLOCK_PERIOD 10."))
        self._write("aes__2026-08-30__134751.json", _case("aes", "2026-08-30"))

        carried = request_review.carried_diagnosis(
            "aes", "aes__2026-08-30__134751.json", refdb=self.refdb)
        self.assertIn("DELAY 0", carried)
        # Named, so the reviewer can tell a verdict on an earlier run
        # from one on this run.
        self.assertIn("aes__2026-08-30.json", carried)

    def test_a_case_with_its_own_diagnosis_is_not_given_an_older_one(self):
        # Its own is the current one. Prepending an older verdict would
        # put a superseded recommendation next to the live one with
        # nothing to say which is which.
        self._write("aes__2026-08-30.json", _case(
            "aes", "2026-08-30", diagnosis="older"))
        self._write("aes__2026-08-30__134751.json", _case(
            "aes", "2026-08-30", diagnosis="current"))
        carried = request_review.carried_diagnosis(
            "aes", "aes__2026-08-30__134751.json", refdb=self.refdb)
        self.assertIsNone(carried)

    def test_the_first_case_of_a_design_carries_nothing(self):
        self._write("aes__2026-08-30.json", _case("aes", "2026-08-30"))
        self.assertIsNone(request_review.carried_diagnosis(
            "aes", "aes__2026-08-30.json", refdb=self.refdb))

    def test_only_this_design_is_carried(self):
        # A verdict about counter4 is not context for aes, and handing it
        # over is exactly the cross-design contamination
        # verify_diagnosis exists to catch.
        self._write("counter4__2026-08-29.json", _case(
            "counter4", "2026-08-29", diagnosis="counter4 verdict"))
        self._write("aes__2026-08-30.json", _case("aes", "2026-08-30"))
        self.assertIsNone(request_review.carried_diagnosis(
            "aes", "aes__2026-08-30.json", refdb=self.refdb))

    def test_the_most_recent_earlier_review_wins(self):
        self._write("aes__2026-08-28.json", _case(
            "aes", "2026-08-28", diagnosis="oldest"))
        self._write("aes__2026-08-29.json", _case(
            "aes", "2026-08-29", diagnosis="newer"))
        self._write("aes__2026-08-30.json", _case("aes", "2026-08-30"))
        carried = request_review.carried_diagnosis(
            "aes", "aes__2026-08-30.json", refdb=self.refdb)
        self.assertIn("newer", carried)
        self.assertNotIn("oldest", carried)


class AttemptHistoryTests(unittest.TestCase):
    """What was already tried for this design, and how it turned out.

    Carrying the previous verdict fixed half the problem: the reviewer
    learns what was RECOMMENDED and still not what HAPPENED. It showed
    immediately. Iteration 3 tried PL/GRT_RESIZER_HOLD_SLACK_MARGIN at
    0.3/0.25 and hold got worse — 172 violations to 200 — and the very
    next review called those values "verified in the earlier case" and
    proposed them again.

    A verdict is a proposal. The candidates are the record of what the
    proposal did. Both have to travel, or the loop recommends things it
    has already disproved.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.refdb = Path(self.tmp.name)
        (self.refdb / "cases").mkdir()
        self.addCleanup(self.tmp.cleanup)

    def _write(self, filename, case):
        (self.refdb / "cases" / filename).write_text(json.dumps(case))

    def _case_with(self, tag, overrides, violations, date="2026-08-30"):
        return _case("aes", date, iterations=[{"iteration": 1, "results": [
            {"tag": tag, "overrides": overrides,
             "verdict": {"passed": False, "violations": violations}}]}])

    def test_an_earlier_attempt_is_listed_with_what_it_produced(self):
        self._write("aes__2026-08-30__1.json", self._case_with(
            "iter3-hold-margin", {"PL_RESIZER_HOLD_SLACK_MARGIN": 0.3},
            ["200 hold timing violation(s)", "218 setup timing violation(s)"]))
        self._write("aes__2026-08-30__2.json", _case("aes", "2026-08-30"))

        history = request_review.attempt_history(
            "aes", "aes__2026-08-30__2.json", refdb=self.refdb)
        self.assertIn("iter3-hold-margin", history)
        # The knob AND the number it produced. Either alone is what the
        # reviewer already had.
        self.assertIn("PL_RESIZER_HOLD_SLACK_MARGIN", history)
        self.assertIn("200", history)

    def test_the_case_being_reviewed_is_not_in_its_own_history(self):
        # Its candidates are in the case file the reviewer is reading.
        # Repeating them as "already tried" invites reading this run's
        # own results as a previous run's.
        self._write("aes__2026-08-30__1.json", self._case_with(
            "only-run", {"CLOCK_PERIOD": 10}, ["1 setup timing violation(s)"]))
        history = request_review.attempt_history(
            "aes", "aes__2026-08-30__1.json", refdb=self.refdb)
        self.assertIsNone(history)

    def test_only_this_design_appears(self):
        self._write("counter4__2026-08-29.json", _case(
            "counter4", "2026-08-29", iterations=[{"iteration": 1, "results": [
                {"tag": "c4", "overrides": {"FP_CORE_UTIL": 25},
                 "verdict": {"passed": True}}]}]))
        self._write("aes__2026-08-30.json", _case("aes", "2026-08-30"))
        self.assertIsNone(request_review.attempt_history(
            "aes", "aes__2026-08-30.json", refdb=self.refdb))

    def test_a_passing_attempt_says_so(self):
        # "Tried and passed" and "tried and failed" are opposite advice.
        self._write("aes__2026-08-30__1.json", _case(
            "aes", "2026-08-30", iterations=[{"iteration": 1, "results": [
                {"tag": "good", "overrides": {"CLOCK_PERIOD": 20},
                 "verdict": {"passed": True, "area_um2": 100.0}}]}]))
        self._write("aes__2026-08-30__2.json", _case("aes", "2026-08-30"))
        history = request_review.attempt_history(
            "aes", "aes__2026-08-30__2.json", refdb=self.refdb)
        self.assertIn("PASS", history)

    def test_a_baseline_with_no_overrides_is_still_listed(self):
        # "The defaults were tried" is information; dropping it makes the
        # default look unexplored.
        self._write("aes__2026-08-30__1.json", self._case_with(
            "cand-baseline", {}, ["5 setup timing violation(s)"]))
        self._write("aes__2026-08-30__2.json", _case("aes", "2026-08-30"))
        history = request_review.attempt_history(
            "aes", "aes__2026-08-30__2.json", refdb=self.refdb)
        self.assertIn("cand-baseline", history)

    def test_history_is_bounded(self):
        # A design with many cases must not push the case's own data out
        # of the reviewer's context with a wall of old attempts.
        for i in range(30):
            self._write(f"aes__2026-08-{i:02d}__x.json", self._case_with(
                f"t{i}", {"CLOCK_PERIOD": i}, [f"{i} setup timing violation(s)"],
                date=f"2026-08-{i:02d}"))
        self._write("aes__2026-08-31.json", _case("aes", "2026-08-31"))
        history = request_review.attempt_history(
            "aes", "aes__2026-08-31.json", refdb=self.refdb)
        # Rows, not lines: the block carries a fixed explanatory header,
        # and the bound that matters is how many attempts it lists.
        rows = [ln for ln in history.splitlines() if ln.startswith("- `")]
        self.assertEqual(len(rows), request_review.MAX_HISTORY_ROWS)
        # The most recent attempts are the ones kept.
        self.assertIn("t29", history)


class RealRequestTests(unittest.TestCase):
    """Against the real store, through the CLI the server calls."""

    def test_the_aes_request_carries_the_verdict_from_its_earlier_case(self):
        cases = ROOT / "reference-db" / "cases"
        earlier = cases / "aes__2026-08-30.json"
        later = cases / "aes__2026-08-30__134751.json"
        if not (earlier.exists() and later.exists()):
            self.skipTest("the two aes cases are not both present")
        if not json.loads(earlier.read_text()).get("diagnosis"):
            self.skipTest("the earlier aes case has no recorded review")

        out = subprocess.run(
            [sys.executable, str(ROOT / "pipeline" / "request_review.py"),
             "request", "--design", "aes"],
            capture_output=True, text=True, cwd=ROOT / "pipeline", timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-500:])
        written = ROOT / out.stdout.strip().rsplit(" ", 1)[-1]
        body = written.read_text()
        # The line that was false: a design whose previous run was
        # reviewed is not being seen for the first time.
        self.assertNotIn("(none recorded yet)", body)
        self.assertIn("aes__2026-08-30.json", body)


if __name__ == "__main__":
    unittest.main()
