"""Tests for retrieving which measurement answers a failure.

case_retrieval tells an agent what happened before. It does not tell it
what to run, and that gap was measured: replaying three recorded cases
through the configured model scored grounded 3/3 and root-cause recall
1/10, while every real advance in those cases came from running
something new. None of the eight agent definitions in .claude/agents/
mentions a single tool of this pipeline's.

So this indexes measurements by the same failure signatures — the tool,
the question it answers, and the trap that wastes the attempt.

The load-bearing test here is the leave-one-out one. Every entry is
grounded in a case this pipeline actually resolved, which means for that
case the entry IS the answer. Feeding it back would measure reading
comprehension, exactly what agent_eval's prompt is built to avoid, and
would have produced a flattering A/B that meant nothing.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import agent_eval  # noqa: E402
import tool_retrieval  # noqa: E402

CASES = ROOT / "reference-db" / "cases"


def case(name: str) -> dict | None:
    hits = sorted(CASES.glob(f"{name}__*.json"))
    return json.loads(hits[-1].read_text()) if hits else None


class EntryShapeTests(unittest.TestCase):
    """Every entry has to carry its own justification."""

    def test_each_entry_names_the_case_it_came_from(self):
        # Nothing is here for sounding plausible. An entry with no
        # recorded evidence is the same failure as an ungrounded
        # diagnosis, one level up.
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertTrue(entry["evidence"].strip(), entry["id"])
            self.assertTrue(entry["design"].strip(), entry["id"])

    def test_the_source_design_is_a_field_not_prose(self):
        # Leave-one-out reads this. It used to read the first word of
        # `evidence`, so an entry whose evidence opened with a variable
        # name rather than a design could never be excluded — it would
        # have leaked that design's answer into its own evaluation
        # while every test still passed.
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertIn("design", entry, entry["id"])
            self.assertNotIn(" ", entry["design"], entry["id"])

    def test_each_entry_names_a_trap(self):
        # The trap is the transferable part. "Run report_checks" is
        # obvious once you know it exists; "the pin does not come from
        # the timing report" is what cost this project several sessions.
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertGreater(len(entry["trap"]), 40, entry["id"])

    def test_each_entry_is_runnable(self):
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertTrue(entry["cli"].strip(), entry["id"])
            self.assertTrue(entry["when"], entry["id"])

    def test_evidence_designs_exist(self):
        # An entry citing a design with no recorded case is stale.
        for entry in tool_retrieval.MEASUREMENTS:
            self.assertIsNotNone(case(entry["design"]),
                                 f"{entry['id']} cites {entry['design']}")


class RetrievalTests(unittest.TestCase):
    def setUp(self):
        self.sram = case("sram_wrapper")
        if self.sram is None:
            self.skipTest("no sram_wrapper case")

    def test_it_matches_on_real_error_codes(self):
        keys = tool_retrieval.case_keys(self.sram)
        self.assertIn("RSZ-0090", keys)
        self.assertIn("PDN-0231", keys)

    def test_it_derives_symptoms_the_codes_do_not_name(self):
        # "there is a macro" and "something violated max slew" are
        # conditions no error code states, and both select measurements.
        keys = tool_retrieval.case_keys(self.sram)
        self.assertIn("macro_present", keys)
        self.assertIn("max_slew_violation", keys)

    def test_the_path_trace_is_retrieved_for_this_failure(self):
        ids = [h["id"] for h in tool_retrieval.retrieve(self.sram)]
        self.assertIn("slew-path-trace", ids)

    def test_more_specific_entries_come_first(self):
        hits = tool_retrieval.retrieve(self.sram)
        self.assertEqual(hits[0]["specificity"], 1.0)
        self.assertGreaterEqual(hits[0]["specificity"], hits[-1]["specificity"])

    def test_a_case_with_no_matching_signature_gets_nothing(self):
        # Silence is the honest answer for a failure nobody has
        # debugged. Returning the nearest-looking entry would be
        # guidance invented on the spot.
        self.assertEqual(tool_retrieval.retrieve({"iterations": []}), [])


class LeaveOneOutTests(unittest.TestCase):
    """The guard that keeps the A/B honest."""

    def setUp(self):
        self.sram = case("sram_wrapper")
        if self.sram is None:
            self.skipTest("no sram_wrapper case")

    def test_entries_from_the_same_design_are_dropped(self):
        kept = tool_retrieval.retrieve(self.sram, exclude_design="sram_wrapper")
        for hit in kept:
            self.assertNotEqual(hit["design"], "sram_wrapper")

    def test_the_eval_prompt_never_carries_the_answer(self):
        # The specific leak: sram_wrapper's entries say "dlymetal6s2s_1,
        # a delay cell" and "0.04 equals the top of index_1" — its whole
        # root cause. Measuring a model against that measures reading.
        prompt = agent_eval.prompt_for(self.sram, guidance=True)
        for leak in ("dlymetal", "index_1", "addr0/addr1/wmask0"):
            self.assertNotIn(leak, prompt, leak)

    def test_guidance_still_reaches_a_reviewer(self):
        # The exclusion is for the eval only. A human or agent working
        # the real case should get everything, including what that case
        # itself established.
        block = tool_retrieval.guidance_block(self.sram)
        self.assertIn("ppa_sta_path", block)
        self.assertIn("dlymetal6s2s_1", block)

    def test_nothing_transferable_says_so_rather_than_going_blank(self):
        # With four designs and one debugged failure shape, everything
        # matching sram_wrapper was learned from sram_wrapper. That is a
        # finding about the corpus, not an empty result.
        block = tool_retrieval.guidance_block(self.sram, exclude_design="sram_wrapper")
        self.assertIn("learned from sram_wrapper itself", block)
        self.assertIn("no transferable guidance", block)

    def test_that_is_distinguished_from_no_match_at_all(self):
        empty = tool_retrieval.guidance_block({"iterations": []},
                                              exclude_design="whatever")
        self.assertIn("No recorded measurement matches", empty)
        self.assertNotIn("transferable", empty)


class ReviewRequestTests(unittest.TestCase):
    def test_the_request_carries_the_measurement_block(self):
        # Where an agent's context actually comes from. Without this the
        # block exists and nothing reads it.
        src = (ROOT / "pipeline" / "request_review.py").read_text()
        self.assertIn("tool_retrieval.guidance_block", src)

    def test_a_generated_request_contains_it(self):
        hits = sorted((ROOT / "reference-db" / "reviews")
                      .glob("sram_wrapper__*__request.md"))
        if not hits:
            self.skipTest("no generated request")
        text = hits[-1].read_text()
        self.assertIn("Measurements that apply", text)
        self.assertIn("trap", text)


if __name__ == "__main__":
    sys.exit(unittest.main())
