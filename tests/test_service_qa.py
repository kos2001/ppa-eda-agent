"""Tests for the retrieval behind the "ask about this service" chat.

The console could already answer one kind of question — paste a report,
get a diagnosis — and no others. "What is this thing for?", "why gf180
and not just sky130?", "what has counter4 actually passed?" had no
surface at all, and the answers exist: the first two in documents this
repo already writes, the third in 400 recorded samples.

So this retrieves over both, and the tests below hold it to three things
that are easy to get wrong and expensive to get wrong quietly:

  - It must answer from what the repo holds, and say where each answer
    came from. An ungrounded confident answer is the exact failure
    tool_retrieval.py was written to avoid, one level up.

  - It must work with no model. Retrieval and generation are separate
    steps, and the useful half — here is the passage that answers this —
    needs no gateway key. A chat that goes blank without a key would be
    useless on exactly the checkout that has not configured one yet.

  - It must not invent numbers. Facts drawn from the case store are
    computed from the store at call time, never phrased into prose here,
    so they cannot drift from it the way a written-in "441 rows" did on
    the progress page within a day of being written.

No embeddings, and the reason is the one case_retrieval.py records for
its own exact-match keys: a scoring function nobody can read is a
scoring function nobody can correct.
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import service_qa  # noqa: E402


class CorpusTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.docs = service_qa.load_corpus()

    def test_it_indexes_the_documents_the_repo_actually_has(self):
        sources = {d["source"] for d in self.docs}
        self.assertIn("README.md", sources)
        self.assertIn("soul.md", sources)
        self.assertTrue(any(s.startswith("references/") for s in sources))
        self.assertTrue(any(s.startswith("docs/") for s in sources))

    def test_it_indexes_what_the_modules_say_about_themselves(self):
        # Most of this project's reasoning is not in its markdown. Why
        # gf180mcuA and B cannot run, why the surrogate dedupes on the
        # library, why max_iterations_reached is not a review case —
        # each is written where the decision lives, in the module's own
        # docstring. A corpus of .md files alone answers "what is this"
        # and cannot answer "why is it like that".
        sources = {d["source"] for d in self.docs}
        self.assertIn("pipeline/collect.py", sources)
        self.assertIn("pipeline/surrogate.py", sources)

    def test_a_module_contributes_its_docstring_and_not_its_code(self):
        # The docstring is the module's statement of intent. Its code is
        # not prose, and indexing it would rank a passage by how often it
        # happens to name a variable.
        for doc in self.docs:
            if doc["source"] == "pipeline/surrogate.py":
                self.assertNotIn("def load_dataset", doc["text"])

    def test_a_passage_carries_where_it_came_from(self):
        for doc in self.docs:
            self.assertTrue(doc["source"])
            self.assertTrue(doc["text"].strip())
            # The heading is what a citation shows; a passage that cannot
            # name its own section cites as a bare filename.
            self.assertIn("title", doc)

    def test_passages_are_sections_not_whole_files(self):
        # The design spec is 100KB+. Returning it whole as "the answer"
        # is the same as returning nothing, and it would crowd every
        # other source out of a model's context.
        spec = [d for d in self.docs if "autonomous-layout-agent" in d["source"]]
        self.assertGreater(len(spec), 5)
        for doc in spec:
            self.assertLess(len(doc["text"]), 12000, doc["title"])


class RetrievalTests(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.docs = service_qa.load_corpus()

    def _top_sources(self, question, n=4):
        hits = service_qa.search(question, self.docs, top=n)
        return [h["source"] for h in hits]

    def test_a_question_about_the_project_finds_the_project_documents(self):
        hits = self._top_sources("what is this project for?")
        self.assertTrue(
            any(s in ("README.md", "soul.md") for s in hits), hits)

    def test_a_why_question_reaches_the_module_that_recorded_the_reason(self):
        # The measurement that settled the gf180mcu variants — two of
        # them ship no OpenRCX ruleset, so OpenLane quits during PDK
        # load — is a comment block in collect.py and appears in no
        # docstring and no markdown. A corpus that stops at .md answers
        # this from the design spec's general remarks about technology:
        # plausible prose in place of a measured reason.
        self.assertIn("pipeline/collect.py",
                      self._top_sources("why is only one gf180mcu variant used"))
        self.assertIn("pipeline/collect.py",
                      self._top_sources("why do some variants have no openrcx ruleset"))

    def test_it_matches_words_and_not_meanings(self):
        # The limitation, asserted rather than left to be discovered.
        # collect.py explains the variants as "all four gf180mcu
        # variants ... A and B cannot run at all"; it never writes
        # "gf180mcuA", so asking in that form does not reach it. This is
        # the cost of exact terms, and it is the cost this project
        # already chose in case_retrieval.py: a score a reader can
        # correct, over one that quietly matches the wrong thing.
        #
        # Here to fail loudly if someone adds fuzzy matching without
        # revisiting that decision — not because the behaviour is good.
        hits = self._top_sources("why are gf180mcuA and gf180mcuB not used?", n=5)
        self.assertNotIn("pipeline/collect.py", hits)

    def test_a_question_about_a_report_format_finds_that_reference(self):
        hits = self._top_sources("how do I read a report_timing slack line?")
        self.assertIn("references/report-timing.md", hits)

    def test_a_question_about_power_does_not_return_only_timing(self):
        # The three reference documents share most of their vocabulary.
        # Scoring on raw term frequency ranks the longest one first for
        # every EDA question, which is how a retrieval layer silently
        # becomes a constant function.
        hits = self._top_sources("switching power breakdown by cell")
        self.assertIn("references/report-power.md", hits)

    def test_an_unanswerable_question_returns_nothing_rather_than_the_nearest_thing(self):
        # The failure mode that matters. Returning the best of a bad set
        # hands the model a passage about something else and invites it
        # to answer anyway.
        hits = service_qa.search(
            "what is the capital of France", self.docs, top=4)
        self.assertEqual(hits, [])

    def test_every_hit_says_how_it_was_scored(self):
        hits = service_qa.search("openlane placement utilization", self.docs)
        self.assertTrue(hits)
        for hit in hits:
            self.assertGreater(hit["score"], 0)
            # The terms that matched, so a wrong answer can be traced to
            # a wrong match rather than argued about.
            self.assertTrue(hit["matched"])


class StoreFactTests(unittest.TestCase):
    """Facts computed from reference-db, never written down here."""

    @classmethod
    def setUpClass(cls):
        cls.facts = service_qa.store_facts()

    def test_the_totals_match_the_store(self):
        cases = list((ROOT / "reference-db" / "cases").glob("*.json"))
        self.assertEqual(self.facts["cases"], len(cases))
        rows = sum(
            len(it.get("results", []))
            for path in cases
            for it in json.loads(path.read_text()).get("iterations", []))
        self.assertEqual(self.facts["candidate_runs"], rows)

    def test_every_design_is_summarised(self):
        designs = {json.loads(p.read_text())["design"]
                   for p in (ROOT / "reference-db" / "cases").glob("*.json")}
        self.assertEqual({d["design"] for d in self.facts["designs"]}, designs)

    def test_a_design_reports_its_own_passes_not_the_whole_store(self):
        for row in self.facts["designs"]:
            self.assertLessEqual(row["passed"], row["candidate_runs"])
            self.assertLessEqual(row["candidate_runs"], self.facts["candidate_runs"])

    def test_a_design_that_never_passed_says_zero_rather_than_being_dropped(self):
        # cdc_twoclock has 104 real runs and no winner. Omitting it would
        # make the store look uniformly successful, which is the one
        # thing soul.md says this project will not do.
        row = next(d for d in self.facts["designs"]
                   if d["design"] == "cdc_twoclock")
        self.assertEqual(row["passed"], 0)
        self.assertGreater(row["candidate_runs"], 0)


class GroundingTests(unittest.TestCase):
    """What gets handed to the model."""

    def test_the_prompt_contains_the_retrieved_text_and_its_source(self):
        built = service_qa.build_prompt("what is this project for?")
        self.assertTrue(built["sources"])
        for source in built["sources"]:
            self.assertIn(source["source"], built["prompt"])

    def test_the_prompt_tells_the_model_to_refuse_what_it_cannot_ground(self):
        built = service_qa.build_prompt("what is this project for?")
        self.assertIn("do not", built["prompt"].lower())

    def test_an_unanswerable_question_produces_no_prompt(self):
        # No sources means there is nothing to be grounded in, and asking
        # anyway is asking for an invented answer.
        built = service_qa.build_prompt("what is the capital of France")
        self.assertEqual(built["sources"], [])
        self.assertIsNone(built["prompt"])

    def test_store_facts_reach_the_prompt_for_a_question_about_results(self):
        built = service_qa.build_prompt(
            "how many candidate runs has counter4 recorded?")
        self.assertIsNotNone(built["prompt"])
        self.assertIn("counter4", built["prompt"])


class CliTests(unittest.TestCase):
    """The interface the server calls."""

    def _run(self, question):
        out = subprocess.run(
            [sys.executable, str(ROOT / "pipeline" / "service_qa.py"), question],
            capture_output=True, text=True, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-800:])
        return json.loads(out.stdout)

    def test_it_prints_json_the_server_can_serve(self):
        got = self._run("what is this project for?")
        self.assertIn("sources", got)
        self.assertIn("prompt", got)
        self.assertTrue(got["sources"])

    def test_it_answers_nothing_rather_than_erroring_on_an_off_topic_question(self):
        got = self._run("what is the capital of France")
        self.assertEqual(got["sources"], [])
        self.assertIsNone(got["prompt"])


if __name__ == "__main__":
    unittest.main()
