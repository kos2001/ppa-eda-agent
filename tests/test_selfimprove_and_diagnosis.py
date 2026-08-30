"""Regression tests for the self-improvement layer: auto-repair coverage
accounting, the budget-vs-stuck triage in self_improve.py, and
verify_diagnosis.py's grounding check.

Same conventions as test_orchestrator_pure.py (see its module docstring
for why unittest and not pytest). These cover logic written late enough
that it has never had a second pair of eyes on it, which is exactly when
a cheap regression test is worth most.
"""
import json
import os
import pathlib
import sys
import unittest

PIPELINE = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline")
sys.path.insert(0, PIPELINE)

import self_improve  # noqa: E402
import verify_diagnosis  # noqa: E402


def _case(**over):
    base = {"design": "d", "date": "2026-01-01", "iterations": [],
            "winner_tag": None}
    base.update(over)
    return base


class TestAutoRepairCoverage(unittest.TestCase):
    """Guards the honesty of the coverage metric. soul.md commits to it
    only going up when a real new pattern is added — never by redefining
    what counts as covered."""

    def test_passing_candidates_are_not_counted_as_failures(self):
        case = _case(iterations=[{"iteration": 1, "results": [
            {"tag": "a", "verdict": {"passed": True}},
            {"tag": "b", "error": "[PDN-0185] Insufficient width"}]}])
        covered, total, matched = self_improve.auto_repair_coverage(case)
        self.assertEqual((covered, total), (1, 1))
        self.assertEqual(matched, ["PDN strap-width (FP_CORE_UTIL/DIE_AREA)"])

    def test_unknown_failure_lowers_coverage(self):
        """An unrecognized failure must count against coverage. Silently
        excluding it would make the metric report 100% while the pipeline
        actually has a gap — the exact self-deception the metric exists
        to prevent."""
        case = _case(iterations=[{"iteration": 1, "results": [
            {"tag": "a", "error": "a failure nobody has a pattern for"}]}])
        covered, total, _ = self_improve.auto_repair_coverage(case)
        self.assertEqual((covered, total), (0, 1))

    def test_nothing_failed_is_zero_of_zero(self):
        case = _case(iterations=[{"iteration": 1, "results": [
            {"tag": "a", "verdict": {"passed": True}}]}])
        self.assertEqual(self_improve.auto_repair_coverage(case)[:2], (0, 0))


class TestBudgetRetryCommand(unittest.TestCase):
    """Guards the re-run suggestion for budget-exhausted cases."""

    def test_suggestion_is_grounded_in_the_real_run_spec(self):
        """The suggested budget must be derived from the design's actual
        run_spec.json, not invented — counter4_tinydie really declares
        max_iterations 4, so the doubled suggestion is 8. Reads the real
        committed file; if that file's value changes this test should be
        updated deliberately, which is the point."""
        spec_path = os.path.join(PIPELINE, "designs", "counter4_tinydie",
                                  "run_spec.json")
        with open(spec_path) as f:
            spec = json.load(f)
        declared = spec["max_iterations"]
        case = _case(iterations=[{"iteration": i, "results": []}
                                  for i in range(1, declared + 1)])
        cmd = self_improve.budget_retry_command("counter4_tinydie", case)
        self.assertIn(f"--max-iterations {declared * 2}", cmd)
        self.assertIn("counter4_tinydie", cmd)

    def test_unknown_design_yields_no_command(self):
        """Nothing to ground a number in means no suggestion, rather than
        a plausible-looking invented one."""
        self.assertIsNone(
            self_improve.budget_retry_command("no-such-design", _case()))


class TestScanningDoesNotWrite(unittest.TestCase):
    """Reading the scan must not change the repository.

    GET /self-improve serves scan_all(), and the dashboard polls it on
    every load. scan_design() generated a review request for any OPEN,
    unreviewed design as a side effect, so opening the console wrote
    files into reference-db/reviews/ — three of them appeared during one
    session of looking at the page, and they show up as untracked work
    nobody did.

    Generating them on a schedule is the point of this module and stays
    (see its docstring). Generating them because someone looked is not:
    a read that writes turns "I opened the dashboard" into a commit
    someone has to explain, and it races itself when two tabs poll at
    once — request_review.py already carries a note about that race.

    So writing is opt-in. The default is the safe one, because every
    caller that is not the scheduled run is a reader: the HTTP endpoint,
    the MCP tool, and anything added later that forgets to think about
    it.
    """

    def setUp(self):
        self.reviews = (pathlib.Path(self_improve.REPO_ROOT)
                        / "reference-db" / "reviews")
        self.before = ({p.name: p.stat().st_mtime_ns
                        for p in self.reviews.glob("*.md")}
                       if self.reviews.is_dir() else {})

    def _unchanged(self):
        after = ({p.name: p.stat().st_mtime_ns
                  for p in self.reviews.glob("*.md")}
                 if self.reviews.is_dir() else {})
        # Names AND mtimes: the old behaviour rewrote a request that
        # already existed on every scan, so checking only for new files
        # would call that clean.
        self.assertEqual(after, self.before)

    def test_scan_all_writes_nothing_by_default(self):
        self_improve.scan_all()
        self._unchanged()

    def test_scan_design_writes_nothing_by_default(self):
        designs = sorted(d.name for d in self_improve.DESIGNS_DIR.iterdir()
                         if d.is_dir())
        for design in designs:
            self_improve.scan_design(design)
        self._unchanged()

    def test_a_read_only_scan_still_says_a_review_is_needed(self):
        # Not writing must not mean not reporting. The panel's job is to
        # say which designs are waiting on a human; only the file is
        # deferred to whoever actually runs the scan.
        report = self_improve.scan_all()
        statuses = [d.get("status", "") for d in report["designs"]]
        self.assertTrue(any("needs review" in s for s in statuses), statuses)

    def test_a_read_only_scan_reports_requests_that_already_exist(self):
        # An existing request is a fact about the repo, and reading it is
        # a read. Without this the panel cannot tell "nobody has filed
        # one" from "we did not look".
        report = self_improve.scan_all()
        on_disk = {p.name for p in self.reviews.glob("*.md")}
        for row in report["designs"]:
            path = row.get("review_request")
            if path:
                self.assertIn(pathlib.Path(path).name, on_disk, row["design"])


class TestVerifyDiagnosisGrounding(unittest.TestCase):
    """Guards verify_diagnosis.verify_case(). Includes the negative
    controls that make the checker evidence rather than decoration: it
    must actually flag an ungrounded reference, not just agree with
    everything."""

    def _case_with(self, diagnosis, error="", tag="cand-baseline"):
        return _case(diagnosis=diagnosis, iterations=[{"iteration": 1, "results": [
            {"tag": tag, "error": error}]}])

    def test_grounded_error_code_passes(self):
        case = self._case_with("RSZ-0090 fired on the macro input pins.",
                                error="[ERROR] RSZ-0090 max transition")
        report = verify_diagnosis.verify_case(case)
        self.assertEqual(report["ungrounded_error_codes"], [])

    def test_invented_error_code_is_flagged(self):
        """NEGATIVE CONTROL. A diagnosis citing a code that appears
        nowhere in the run's real output is the concrete, decidable shape
        of the failure this guards — sram_wrapper's first diagnosis
        asserted a cause without opening the file that would have
        disproved it."""
        case = self._case_with("The real culprit is XYZ-9999.",
                                error="[ERROR] RSZ-0090 max transition")
        report = verify_diagnosis.verify_case(case)
        self.assertEqual(report["ungrounded_error_codes"], ["XYZ-9999"])

    def test_invented_candidate_tag_is_flagged(self):
        """NEGATIVE CONTROL: stale copy-paste from another design's case
        shows up as a tag this case never ran."""
        case = self._case_with("sweep-util-45 also failed here.",
                                tag="cand-baseline")
        report = verify_diagnosis.verify_case(case)
        self.assertEqual(report["ungrounded_candidate_tags"], ["sweep-util-45"])

    def test_the_word_candidate_is_not_mistaken_for_a_tag(self):
        """Real false positive hit on this checker's first run against
        sram_wrapper: the tag pattern matched the ordinary English word
        "candidate". The required hyphen after the prefix is what fixed
        it, and this pins it — a checker that cries wolf on normal prose
        gets switched off."""
        case = self._case_with("No placement-strategist candidate is justified.")
        report = verify_diagnosis.verify_case(case)
        self.assertEqual(report["ungrounded_candidate_tags"], [])
        self.assertEqual(report["cited_candidate_tags"], [])

    def test_review_summaries_are_checked_too(self):
        """Subagent review text is agent-written prose exactly like the
        diagnosis field, so it must be held to the same standard."""
        case = _case(iterations=[{"iteration": 1, "results": [
            {"tag": "cand-baseline", "error": ""}]}],
            human_in_the_loop=[{"agent": "feedback-optimizer",
                                 "summary": "Blocked by ABC-1234."}])
        report = verify_diagnosis.verify_case(case)
        self.assertEqual(report["ungrounded_error_codes"], ["ABC-1234"])

    def test_case_without_prose_is_skipped_not_passed(self):
        """A case with no diagnosis has nothing to verify. Reporting it
        as OK would inflate confidence in an unchecked record."""
        self.assertFalse(verify_diagnosis.verify_case(_case())["checked"])


class TestRealCommittedCasesAreGrounded(unittest.TestCase):
    """Runs the grounding check against this repo's real committed cases.
    Not a synthetic fixture: if a future diagnosis cites something the
    run never produced, this fails in CI-time rather than being noticed
    months later."""

    def test_every_committed_case_is_grounded(self):
        index_file = verify_diagnosis.REFDB / "index.json"
        if not index_file.exists():
            self.skipTest("no reference-db index (fresh checkout)")
        index = json.loads(index_file.read_text())
        for design, names in index.items():
            for name in names:
                path = verify_diagnosis.REFDB / "cases" / name
                if not path.exists():
                    continue
                report = verify_diagnosis.verify_case(json.loads(path.read_text()))
                if not report["checked"]:
                    continue
                self.assertEqual(report["ungrounded_error_codes"], [],
                                  f"{name} cites unrecorded error code(s)")
                self.assertEqual(report["ungrounded_candidate_tags"], [],
                                  f"{name} cites nonexistent candidate tag(s)")


if __name__ == "__main__":
    unittest.main()
