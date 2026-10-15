"""What the 2026-10-02 retrieval measurement found, held in place.

pipeline/retrieval_eval.py measured the two retrieval layers on the store and
on a gold set of questions (tests/retrieval_gold.json). Before: 14 of the 34
failing cases carried no failure signature, 50% of off-topic questions were
answered anyway, and the SRAM experiment READMEs were not indexed. These tests
pin each fix to the failure that motivated it, and guard the aggregate numbers
with margins so an edit to a document cannot quietly undo them.
"""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import case_retrieval  # noqa: E402
import retrieval_eval  # noqa: E402
import service_qa  # noqa: E402
import tool_retrieval  # noqa: E402


def case_with(verdict, design="d", date="2026-10-02", error=""):
    return {"design": design, "date": date, "_file": f"reference-db/cases/{design}__{date}.json",
            "iterations": [{"results": [{"tag": "t", "verdict": verdict, "error": error,
                                          "overrides": {}}]}]}


class UnverifiedSignatures(unittest.TestCase):
    """A verdict can refuse a pass without counting a violation. Retrieval read
    only `violations`, so every such case had no fingerprint."""

    CDC = {"passed": False, "violations": [],
           "unverified": ["timing for clock domain 'clk_b' (declared but never constrained, "
                          "so no path in it was analysed)"]}

    def test_an_unconstrained_clock_domain_is_a_signature(self):
        self.assertEqual(case_retrieval.unverified_signatures(self.CDC), {"unverified:cdc-unconstrained"})

    def test_macro_model_extrapolation_is_a_signature(self):
        v = {"unverified": ["timing on 27 macro pin(s) is extrapolated beyond the liberty's "
                            "characterisation: worst is u_sram/clk0"]}
        self.assertEqual(case_retrieval.unverified_signatures(v), {"unverified:macro-model"})

    def test_a_check_that_did_not_run_is_not_a_check_that_found_errors(self):
        """'KLayout DRC error(s)' under unverified means the metric was absent."""
        v = {"unverified": ["KLayout DRC error(s)"], "violations": []}
        sig = case_retrieval.unverified_signatures(v)
        self.assertEqual(sig, {"unverified:signoff:drc"})
        self.assertNotIn("signoff:drc", case_retrieval.signoff_signatures(v))

    def test_abstract_magic_drc_is_a_signature(self):
        v = {"unverified": ["Magic DRC on abstracts: 382 nwell.4 on standard-cell rows"]}
        self.assertEqual(case_retrieval.unverified_signatures(v), {"unverified:magic-abstract"})

    def test_a_clean_verdict_has_none(self):
        self.assertEqual(case_retrieval.unverified_signatures({"passed": True, "unverified": []}), set())
        self.assertEqual(case_retrieval.unverified_signatures(None), set())

    def test_the_case_carries_them(self):
        self.assertIn("unverified:cdc-unconstrained",
                      case_retrieval.case_signatures(case_with(self.CDC)))

    def test_two_cases_that_could_not_check_the_same_thing_find_each_other(self):
        a, b = case_with(self.CDC, date="2026-08-27"), case_with(self.CDC, date="2026-08-28")
        hits = case_retrieval.similar(a, [a, b], top=3)
        self.assertEqual([h["date"] for h in hits], ["2026-08-28"])
        self.assertEqual(hits[0]["shared_signatures"], ["unverified:cdc-unconstrained"])

    def test_the_stored_failing_cases_all_have_a_signature_now(self):
        got = retrieval_eval.evaluate_cases()
        self.assertEqual(got["failing_with_no_signature"], 0, got["no_signature_cases"])


class ToolCodes(unittest.TestCase):
    def test_odb_errors_are_fingerprinted(self):
        text = "[ODB-0370] Attempt to disconnect iterm of dont_touch instance gen_io_drv_addr1[5].u_a"
        self.assertEqual(case_retrieval.signatures(text), {"ODB-0370"})


class ToolRetrievalSeesTheVerdict(unittest.TestCase):
    def test_a_case_that_finished_and_failed_signoff_gets_its_guidance(self):
        """Most failing cases never raised a tool error; keyed on error text
        alone, measurements reached only the ones that crashed."""
        verdict = {"passed": False, "violations": ["18 max-fanout (DRV) violation(s)"], "unverified": []}
        case = case_with(verdict, design="other")
        self.assertIn("signoff:max-fanout", tool_retrieval.case_keys(case))
        ids = {h["id"] for h in tool_retrieval.retrieve(case, exclude_design="other")}
        self.assertIn("fanout-made-of-diodes", ids)

    def test_the_entry_is_withheld_from_the_design_it_came_from(self):
        verdict = {"passed": False, "violations": ["18 max-fanout (DRV) violation(s)"], "unverified": []}
        ids = {h["id"] for h in tool_retrieval.retrieve(case_with(verdict, design="sram_wrapper"),
                                                        exclude_design="sram_wrapper")}
        self.assertNotIn("fanout-made-of-diodes", ids)

    def test_a_macro_model_case_is_offered_the_slew_reachability_check(self):
        verdict = {"passed": False, "violations": [],
                   "unverified": ["timing on 3 macro pin(s) is extrapolated beyond the liberty"]}
        ids = {h["id"] for h in tool_retrieval.retrieve(case_with(verdict, design="x"))}
        self.assertIn("is-this-slew-reachable", ids)

    def test_the_dont_touch_abort_has_an_entry(self):
        case = case_with({"passed": False, "violations": []}, design="x",
                         error="[ODB-0370] Attempt to disconnect iterm of dont_touch instance u_a")
        ids = {h["id"] for h in tool_retrieval.retrieve(case)}
        self.assertIn("dont-touch-needs-the-nets", ids)

    def test_every_new_entry_names_a_tool_that_exists(self):
        wanted = {"is-this-slew-reachable": ["lib_slew_floor.py", "lib_chain.py"],
                  "macro-pin-geometry": ["macro_pins.py", "io_driver_report.py"],
                  "fanout-made-of-diodes": ["fanout_loads.py"],
                  "dont-touch-needs-the-nets": ["sram_io_experiment.py"]}
        by_id = {e["id"]: e for e in tool_retrieval.MEASUREMENTS}
        for entry_id, files in wanted.items():
            self.assertIn(entry_id, by_id)
            for name in files:
                self.assertTrue((ROOT / "pipeline" / name).is_file(), name)
                self.assertIn(name, by_id[entry_id]["tool"] + by_id[entry_id]["cli"])


class AskTokens(unittest.TestCase):
    def test_a_plural_and_its_base_meet(self):
        self.assertIn("violation", service_qa.tokenize("max-fanout violations"))
        self.assertIn("fail", service_qa.tokenize("it failed twice"))
        self.assertIn("pin", service_qa.tokenize("pinned"))

    def test_the_exact_word_is_still_indexed(self):
        self.assertIn("violations", service_qa.tokenize("violations"))

    def test_short_and_non_words_are_left_alone(self):
        self.assertEqual(service_qa.stem("fails"), "fail")
        self.assertEqual(service_qa.stem("pass"), "pass")
        self.assertEqual(service_qa.stem("process"), "process")
        self.assertEqual(service_qa.stem("report_timing"), "report_timing")
        self.assertEqual(service_qa.stem("gf180mcu"), "gf180mcu")

    def test_a_translated_korean_word_does_not_also_count_as_itself(self):
        toks = service_qa.tokenize("팬아웃 위반은")
        self.assertIn("fanout", toks)
        self.assertIn("violation", toks)
        self.assertNotIn("위반은", toks)
        self.assertNotIn("팬아웃", toks)

    def test_an_untranslated_korean_word_survives(self):
        self.assertIn("무엇을", service_qa.tokenize("무엇을"))


class AskAnswersOnlyWhatItCan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.docs = service_qa.load_corpus()

    def test_words_the_corpus_never_used_count_against_the_question(self):
        """'controller' alone used to be enough: the words nobody wrote weighed nothing."""
        self.assertEqual(service_qa.search("How do I configure a Kubernetes ingress controller?", self.docs), [])

    def test_one_shared_ordinary_word_is_not_an_answer(self):
        self.assertEqual(service_qa.search("Write me a poem about autumn leaves", self.docs), [])

    def test_a_real_question_with_one_unlisted_word_still_answers(self):
        hits = service_qa.search("What does the polish step move when a design already passes?", self.docs)
        self.assertTrue(any(h["source"] == "pipeline/pnr_polish.py" for h in hits))

    def test_the_design_readmes_and_agent_definitions_are_indexed(self):
        sources = {d["source"] for d in self.docs}
        self.assertIn("pipeline/designs/sram_wrapper/experiments/README.md", sources)
        self.assertIn(".claude/agents/topology-analyst.md", sources)

    def test_the_sram_experiment_is_findable(self):
        hits = service_qa.search("Which orientation of the SRAM macro gives the shortest routed wire?", self.docs)
        self.assertEqual(hits[0]["source"], "pipeline/designs/sram_wrapper/experiments/README.md")


class GoldSet(unittest.TestCase):
    """The gold set is data about the repo; keep it honest and keep the numbers."""

    @classmethod
    def setUpClass(cls):
        cls.gold = json.loads(retrieval_eval.GOLD.read_text(encoding="utf-8"))
        cls.result = retrieval_eval.evaluate_ask()

    def test_every_question_is_in_exactly_one_split(self):
        for q in self.gold["questions"]:
            self.assertIn(q["split"], ("dev", "test"), q["q"])

    def test_every_accepted_file_exists(self):
        for q in self.gold["questions"]:
            for path in q.get("accept", []):
                self.assertTrue((ROOT / path).is_file(), f"{q['q']!r} accepts missing {path}")

    def test_negatives_have_nothing_to_find(self):
        negatives = [q for q in self.gold["questions"] if q.get("negative")]
        self.assertGreaterEqual(len(negatives), 4)
        for q in negatives:
            self.assertNotIn("accept", q)

    def test_rank_of(self):
        hits = [{"source": "a.md"}, {"source": "dir/b.md"}]
        self.assertEqual(retrieval_eval.rank_of(hits, ["dir/b.md"]), 2)
        self.assertIsNone(retrieval_eval.rank_of(hits, ["c.md"]))

    def test_held_out_questions_are_found(self):
        """Measured 2026-10-02: test hit@3 0.63 -> 0.89 while tuning read only dev failures."""
        self.assertGreaterEqual(self.result["splits"]["test"]["hit@3"], 0.8)

    def test_overall_recall_did_not_slip(self):
        self.assertGreaterEqual(self.result["splits"]["all"]["hit@3"], 0.78)
        self.assertGreaterEqual(self.result["splits"]["all"]["hit@4"], 0.8)

    def test_off_topic_questions_are_not_answered(self):
        self.assertEqual(self.result["splits"]["all"]["false_positive_rate"], 0.0,
                         self.result["splits"]["all"]["answered_when_it_should_not"])


if __name__ == "__main__":
    unittest.main()
