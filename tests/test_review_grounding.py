"""Tests for the grounding check that now runs when a review is applied.

The graph-engineering guide calls a read-only reviewer separate from the
producer "the single highest-value node", and this pipeline had one —
verify_diagnosis — reachable only from self_improve.py and the MCP
server. So a review could become a case's diagnosis, and be read by
every later reviewer, without anything checking that the error codes and
candidate tags it cites exist in that case's data.

That is the failure it was written for: sram_wrapper's first diagnosis
blamed pins nobody had opened the liberty file to look at.

The check is deliberately weak and decidable — it cannot say a diagnosis
is *correct*, only that its references are real. These tests hold that
line in both directions: it must catch invented and copied-from-another
-design references, and it must not flag a review that only cites what
the case actually recorded.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import verify_diagnosis  # noqa: E402


def case_with(error, tags=("cand-baseline",), diagnosis=""):
    return {
        "design": "d", "date": "2026-08-27", "diagnosis": diagnosis,
        "iterations": [{"iteration": 1, "results": [
            {"tag": t, "overrides": {}, "error": error} for t in tags
        ]}],
    }


def check(case, review_text):
    """What request_review.cmd_apply does: verify the review text against
    the case's own recorded evidence."""
    return verify_diagnosis.verify_case({**case, "diagnosis": review_text})


class GroundedTests(unittest.TestCase):
    def setUp(self):
        self.case = case_with("[ERROR] [RSZ-0090] Max transition time")

    def test_a_review_citing_only_real_references_is_clean(self):
        got = check(self.case, "RSZ-0090 on cand-baseline is the binding constraint.")
        self.assertTrue(got["checked"])
        self.assertEqual(got["ungrounded_error_codes"], [])
        self.assertEqual(got["ungrounded_candidate_tags"], [])

    def test_prose_with_no_references_is_clean(self):
        # A verdict can legitimately cite nothing — "leave this open" is
        # an answer. Flagging it would make the check noise.
        got = check(self.case, "This should stay open pending a real measurement.")
        self.assertEqual(got["ungrounded_error_codes"], [])

    def test_empty_review_is_reported_as_unchecked_not_as_clean(self):
        got = check(self.case, "   ")
        self.assertFalse(got["checked"])
        self.assertIn("reason", got)


class UngroundedTests(unittest.TestCase):
    def setUp(self):
        self.case = case_with("[ERROR] [RSZ-0090] Max transition time")

    def test_catches_an_invented_error_code(self):
        got = check(self.case, "The root cause is DRT-9999.")
        self.assertIn("DRT-9999", got["ungrounded_error_codes"])

    def test_catches_an_invented_candidate_tag(self):
        got = check(self.case, "Re-run cand-nonexistent-42 to confirm.")
        self.assertIn("cand-nonexistent-42", got["ungrounded_candidate_tags"])

    def test_catches_a_code_real_elsewhere_but_not_in_this_case(self):
        # The stale-copy-paste case: PDN-0185 is a real code this
        # pipeline has hit, just not here. "Real somewhere" is not
        # grounding.
        got = check(self.case, "The PDN-0185 seen here explains it.")
        self.assertIn("PDN-0185", got["ungrounded_error_codes"])

    def test_reports_every_bad_reference_not_just_the_first(self):
        got = check(self.case,
                    "DRT-9999 on cand-nonexistent-42 explains the PDN-0185; "
                    "re-run sweep-util-99.")
        bad = got["ungrounded_error_codes"] + got["ungrounded_candidate_tags"]
        self.assertEqual(len(bad), 4, bad)

    def test_a_real_code_is_not_flagged_alongside_a_fake_one(self):
        got = check(self.case, "RSZ-0090 is real; DRT-9999 is not.")
        self.assertEqual(got["ungrounded_error_codes"], ["DRT-9999"])
        self.assertIn("RSZ-0090", got["cited_error_codes"])


class ScopeTests(unittest.TestCase):
    """What the check must not claim."""

    def test_it_does_not_judge_correctness(self):
        # A confidently wrong diagnosis that cites only real references
        # passes — and must, because deciding physics is the judgment
        # request_review escalates to a human. A regex claiming to settle
        # it would be worse than no check.
        case = case_with("[ERROR] [RSZ-0090] Max transition time")
        got = check(case, "RSZ-0090 on cand-baseline is caused by moonlight.")
        self.assertEqual(got["ungrounded_error_codes"], [])
        self.assertEqual(got["ungrounded_candidate_tags"], [])


if __name__ == "__main__":
    unittest.main()
