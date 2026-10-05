"""Tests for review_verify: independent review answers verified against
each other (VeriHarness: resolve disagreement, challenge consensus,
adjudicate from the two records) before one enters a case.

The gates are content-blind on purpose. These tests pin what they must
refuse — an edited input, a verdict nobody checked, a broken consensus
with no contradiction quoted, an open question dropped on the way into
the diagnosis, a verification of a run that is no longer under review —
and that a well-formed workspace lands in the case through the same
writer as `request_review.py apply`, with its record attached.
"""
import json
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import request_review  # noqa: E402
import review_verify  # noqa: E402

CASE = "demo__2026-10-05__101010.json"


def _case(**over):
    case = {
        "design": "demo", "date": "2026-10-05", "outcome": "no winner",
        "winner_tag": None, "diagnosis": "",
        "iterations": [{"results": [
            {"tag": "demo-base", "stage": "verification_ppa",
             "run_dir": "/nonexistent/demo-base", "overrides": {},
             "error": "[ERROR GRT-0116] Global routing finished with congestion",
             "verdict": {"passed": False, "violations": ["3 setup"]}},
        ]}],
    }
    case.update(over)
    return case


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.refdb = base / "refdb"
        (self.refdb / "cases").mkdir(parents=True)
        (self.refdb / "reviews").mkdir()
        self.write_case(CASE, _case())
        self.write_index([CASE])
        (self.refdb / "reviews" / "demo__2026-10-05__101010__request.md").write_text(
            "# Human-in-the-loop review request — demo\n", encoding="utf-8")
        self.answers = base / "answers"
        self.answers.mkdir()
        self.root = base / "verify"

    def tearDown(self):
        self.tmp.cleanup()

    def write_case(self, name, case):
        (self.refdb / "cases" / name).write_text(json.dumps(case), encoding="utf-8")

    def write_index(self, names):
        (self.refdb / "index.json").write_text(json.dumps({"demo": names}),
                                               encoding="utf-8")

    def answer(self, name, text):
        path = self.answers / name
        path.write_text(text, encoding="utf-8")
        return path

    def init(self, texts=("demo-base fails GRT-0116: congestion.",
                          "demo-base fails on 3 setup violations.")):
        rollouts = [("feedback-optimizer", self.answer(f"a{i}.md", t))
                    for i, t in enumerate(texts)]
        with redirect_stdout(StringIO()):
            return review_verify.init_workspace("demo", rollouts, self.root,
                                                self.refdb)

    def complete(self, ws, finish=None, diagnosis=None, elim=None, fals=None):
        elim = elim if elim is not None else {"disagreements": [{
            "question": "Which gate fails first for demo-base?",
            "checked": "workspace/case.json iterations[0].results[0].error",
            "found": "error cites GRT-0116; verdict lists 3 setup",
            "verdict": "r01 and r02 both partly right"}], "notes": "keep r01"}
        fals = fals if fals is not None else {"challenges": [{
            "claim": "demo-base did not pass",
            "tried": "read verdict.passed in workspace/case.json",
            "found": "\"passed\": false", "holds": True,
            "because": "the recorded verdict says so"}], "notes": ""}
        finish = finish if finish is not None else {
            "base": "r01",
            "work": [{"what": "timing", "to": "add the 3 setup violations",
                      "evidence": "ledger_elim disagreements[0]"}],
            "open": [{"item": "Whether congestion causes the setup failures",
                      "readings": ["congestion detours long nets",
                                   "the clock target is too tight"],
                      "prefer": ""}],
            "notes": "r01 names the first error"}
        if diagnosis is None:
            diagnosis = ("demo-base fails GRT-0116 and has 3 setup violations.\n\n"
                         "## Open questions\n\n"
                         "- Whether congestion causes the setup failures\n")
        (ws / "ledger_elim.json").write_text(json.dumps(elim), encoding="utf-8")
        (ws / "ledger_fals.json").write_text(json.dumps(fals), encoding="utf-8")
        (ws / "finish.json").write_text(json.dumps(finish), encoding="utf-8")
        (ws / "out" / "diagnosis.md").write_text(diagnosis, encoding="utf-8")


class InitTests(Fixture):
    def test_one_answer_is_refused(self):
        with self.assertRaises(SystemExit) as raised:
            self.init(texts=("only one",))
        self.assertIn("at least 2", str(raised.exception))

    def test_identical_answers_are_one_answer_counted_twice(self):
        with self.assertRaises(SystemExit) as raised:
            self.init(texts=("same text", "same text"))
        self.assertIn("identical", str(raised.exception))

    def test_reviewers_must_have_answered_a_recorded_request(self):
        (self.refdb / "reviews" / "demo__2026-10-05__101010__request.md").unlink()
        with self.assertRaises(SystemExit) as raised:
            self.init()
        self.assertIn("request_review.py request", str(raised.exception))

    def test_a_case_with_a_winner_needs_no_review(self):
        self.write_case(CASE, _case(winner_tag="demo-base"))
        with self.assertRaises(SystemExit):
            self.init()

    def test_workspace_holds_inputs_and_missing_runs_stay_missing(self):
        ws = self.init()
        self.assertEqual(ws.name, "demo__2026-10-05__101010")
        for rel in ("MISSION.md", "CHARTER.md", "RESOLVE.md", "CHALLENGE.md",
                    "ADJUDICATE.md", "spec/request.md", "workspace/case.json",
                    "rollouts/r01/response.md", "rollouts/r02/meta.json"):
            self.assertTrue((ws / rel).is_file(), rel)
        runs = json.loads((ws / "workspace" / "runs.json").read_text())
        self.assertEqual(runs[0]["tag"], "demo-base")
        self.assertFalse(runs[0]["exists"])
        manifest = json.loads((ws / "manifest.json").read_text())
        self.assertEqual([r["name"] for r in manifest["rollouts"]], ["r01", "r02"])
        self.assertEqual(manifest["runs_present"], 0)

    def test_an_archived_copy_of_a_gone_run_is_listed_for_every_session(self):
        # aes's closure runs lived under /private/tmp; their reports were
        # archived in the repository. One reviewer found the archive and
        # one did not. Listing it puts both on the same evidence.
        repo = Path(self.tmp.name) / "repo"
        archive = repo / "pipeline/designs/demo/experiments/evidence-x/demo-base"
        archive.mkdir(parents=True)
        (archive / "sources.json").write_text("{}")
        runs = review_verify.run_inventory(_case(), repo_root=repo)
        self.assertFalse(runs[0]["exists"])
        self.assertEqual(runs[0]["archived"],
                         ["pipeline/designs/demo/experiments/evidence-x/demo-base"])

    def test_a_folder_without_sources_is_not_an_archive(self):
        repo = Path(self.tmp.name) / "repo"
        (repo / "pipeline/designs/demo/experiments/x/demo-base").mkdir(parents=True)
        self.assertEqual(review_verify.run_inventory(_case(), repo_root=repo)[0]
                         ["archived"], [])

    def test_a_failed_init_leaves_no_half_built_workspace(self):
        original = review_verify.PROMPT_FILES
        review_verify.PROMPT_FILES = original + ("MISSING.md",)
        try:
            with self.assertRaises(OSError):
                self.init()
        finally:
            review_verify.PROMPT_FILES = original
        self.assertFalse((self.root / "demo__2026-10-05__101010").exists())
        self.init()  # and a retry works

    def test_an_existing_workspace_is_not_overwritten(self):
        self.init()
        with self.assertRaises(SystemExit) as raised:
            self.init(texts=("another a", "another b"))
        self.assertIn("already exists", str(raised.exception))


class CheckTests(Fixture):
    def test_a_fresh_workspace_is_not_ready(self):
        problems = review_verify.check_workspace(self.init())
        joined = "\n".join(problems)
        for name in ("ledger_elim.json", "ledger_fals.json", "finish.json",
                     "out/diagnosis.md"):
            self.assertIn(name, joined)

    def test_complete_records_are_ready(self):
        ws = self.init()
        self.complete(ws)
        self.assertEqual(review_verify.check_workspace(ws), [])

    def test_an_edited_answer_is_refused(self):
        ws = self.init()
        self.complete(ws)
        (ws / "rollouts" / "r02" / "response.md").write_text("rewritten")
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("r02/response.md changed" in p for p in problems), problems)

    def test_a_verdict_with_nothing_checked_must_be_cannot_tell(self):
        ws = self.init()
        self.complete(ws, elim={"disagreements": [{
            "question": "q", "checked": "nothing", "found": "both readings",
            "verdict": "r01"}]})
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("cannot tell" in p for p in problems), problems)

    def test_an_explained_cannot_tell_counts_as_unsettled(self):
        # Sessions write "cannot tell. Deciding runs: ...". An exact match
        # counted none of the three such verdicts in the first real run.
        ws = self.init()
        self.complete(ws, elim={"disagreements": [{
            "question": "q", "checked": "nothing", "found": "both readings",
            "verdict": "Cannot tell. Deciding run: rerun at fanout 10"}]})
        self.assertEqual(review_verify.check_workspace(ws), [])
        manifest = json.loads((ws / "manifest.json").read_text())
        record = review_verify.verification_record(ws, manifest)
        self.assertEqual(record["disagreements_unsettled"], 1)

    def test_a_broken_consensus_must_quote_the_contradiction(self):
        ws = self.init()
        self.complete(ws, fals={"challenges": [{
            "claim": "c", "tried": "t", "found": "", "holds": False,
            "because": "b"}]})
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("quote the contradiction" in p for p in problems), problems)

    def test_holds_must_be_a_boolean_not_a_number(self):
        ws = self.init()
        self.complete(ws, fals={"challenges": [{
            "claim": "c", "tried": "t", "found": "f", "holds": 1, "because": "b"}]})
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("'holds'" in p for p in problems), problems)

    def test_the_base_must_be_a_candidate_or_none(self):
        ws = self.init()
        self.complete(ws, finish={"base": "r09", "work": [], "open": [], "notes": ""})
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("r09" in p for p in problems), problems)

    def test_a_base_that_is_not_a_string_is_reported_not_raised(self):
        ws = self.init()
        self.complete(ws, finish={"base": ["r01"], "work": [], "open": [], "notes": ""})
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("base" in p for p in problems), problems)

    def test_a_bom_is_accepted_and_other_encodings_are_reported(self):
        # PowerShell 5 writes UTF-8 with a BOM; a Korean-locale tool may
        # write cp949. The first is valid, the second a reported problem.
        ws = self.init()
        self.complete(ws)
        finish = ws / "finish.json"
        finish.write_bytes(b"\xef\xbb\xbf" + finish.read_bytes())
        self.assertEqual(review_verify.check_workspace(ws), [])
        (ws / "ledger_elim.json").write_bytes(
            json.dumps({"disagreements": [{"question": "무엇", "checked": "c",
                        "found": "f", "verdict": "r01"}]},
                       ensure_ascii=False).encode("cp949"))
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("not UTF-8" in p for p in problems), problems)

    def test_an_edited_run_inventory_is_refused(self):
        ws = self.init()
        self.complete(ws)
        (ws / "workspace" / "runs.json").write_text("[]")
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("runs.json changed" in p for p in problems), problems)

    def test_nothing_checked_in_other_words_still_needs_cannot_tell(self):
        ws = self.init()
        self.complete(ws, elim={"disagreements": [{
            "question": "q", "checked": "nothing recorded", "found": "f",
            "verdict": "r01"}]})
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("cannot tell" in p for p in problems), problems)

    def test_work_needs_evidence(self):
        ws = self.init()
        self.complete(ws, finish={"base": "none", "notes": "", "open": [],
                                  "work": [{"what": "w", "to": "t", "evidence": ""}]})
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("'evidence' is empty" in p for p in problems), problems)

    def test_an_open_question_needs_two_readings(self):
        ws = self.init()
        self.complete(ws, finish={"base": "r01", "work": [], "notes": "",
                                  "open": [{"item": "x", "readings": ["one"]}]},
                      diagnosis="x")
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("two readings" in p for p in problems), problems)

    def test_an_open_question_cannot_be_dropped_from_the_diagnosis(self):
        ws = self.init()
        self.complete(ws, diagnosis="demo-base fails GRT-0116.\n")
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("does not name open question" in p for p in problems),
                        problems)


class ApplyTests(Fixture):
    def apply(self, ws, digest=None):
        digest = digest or review_verify.manifest_digest(ws)
        with redirect_stdout(StringIO()):
            return review_verify.apply_workspace(ws, self.refdb, digest)

    def test_the_adjudicated_diagnosis_lands_with_its_record(self):
        ws = self.init()
        self.complete(ws)
        result = self.apply(ws)
        case = json.loads((self.refdb / "cases" / CASE).read_text())
        self.assertIn("## Open questions", case["diagnosis"])
        review = case["human_in_the_loop"][-1]
        self.assertEqual(review["agent"], "verified:feedback-optimizer")
        record = review["verification"]
        self.assertEqual(record["method"], "veriharness")
        self.assertEqual(record["base"], "r01")
        self.assertEqual(record["open"], ["Whether congestion causes the setup failures"])
        self.assertEqual(record["consensus_broken"], 0)
        self.assertEqual(len(record["rollouts"]), 2)
        self.assertIn("finish.json", record["sha256"])
        # The same grounding check `apply` runs, on the delivered text.
        self.assertTrue(review["grounding"]["checked"])
        self.assertEqual(review["grounding"]["ungrounded_error_codes"], [])
        self.assertEqual(result["case_file"], CASE)

    def test_an_incomplete_workspace_is_not_applied(self):
        ws = self.init()
        with self.assertRaises(SystemExit):
            self.apply(ws)
        case = json.loads((self.refdb / "cases" / CASE).read_text())
        self.assertNotIn("human_in_the_loop", case)

    def test_a_newer_case_means_the_review_judged_a_stale_run(self):
        ws = self.init()
        self.complete(ws)
        newer = "demo__2026-10-05__121212.json"
        self.write_case(newer, _case())
        self.write_index([CASE, newer])
        with self.assertRaises(SystemExit) as raised:
            self.apply(ws)
        self.assertIn(newer, str(raised.exception))

    def test_changed_recorded_results_are_refused(self):
        ws = self.init()
        self.complete(ws)
        changed = _case()
        changed["iterations"][0]["results"][0]["verdict"]["violations"] = ["9 setup"]
        self.write_case(CASE, changed)
        with self.assertRaises(SystemExit) as raised:
            self.apply(ws)
        self.assertIn("changed after init", str(raised.exception))

    def test_applying_twice_records_once(self):
        # Review of #58: nothing marked a workspace applied, so a retry
        # appended the same verified diagnosis a second time.
        ws = self.init()
        self.complete(ws)
        digest = review_verify.manifest_digest(ws)
        self.apply(ws, digest)
        with self.assertRaises(SystemExit):
            self.apply(ws, digest)
        (ws / "applied.json").unlink()  # the case itself still knows
        with self.assertRaises(SystemExit) as raised:
            self.apply(ws, digest)
        self.assertIn("already holds this verification", str(raised.exception))
        case = json.loads((self.refdb / "cases" / CASE).read_text())
        self.assertEqual(len(case["human_in_the_loop"]), 1)

    def test_rewriting_an_answer_and_its_manifest_hash_is_caught(self):
        # Review of #58: the expected hashes lived in the writable
        # workspace, so editing both passed. The digest printed at init
        # is the anchor apply requires.
        ws = self.init()
        self.complete(ws)
        digest = review_verify.manifest_digest(ws)
        answer = ws / "rollouts" / "r02" / "response.md"
        answer.write_text("rewritten\n", encoding="utf-8")
        manifest = json.loads((ws / "manifest.json").read_text())
        manifest["rollouts"][1]["sha256"] = review_verify.sha256_file(answer)
        (ws / "manifest.json").write_text(json.dumps(manifest))
        self.assertEqual(review_verify.check_workspace(ws), [])  # unanchored
        with self.assertRaises(SystemExit) as raised:
            self.apply(ws, digest)
        self.assertIn("digest printed at init", str(raised.exception))

    def test_a_digest_pasted_in_uppercase_is_the_same_digest(self):
        ws = self.init()
        self.complete(ws)
        self.apply(ws, review_verify.manifest_digest(ws).upper())
        case = json.loads((self.refdb / "cases" / CASE).read_text())
        self.assertEqual(len(case["human_in_the_loop"]), 1)

    def test_a_malformed_manifest_is_reported_not_raised(self):
        ws = self.init()
        self.complete(ws)
        manifest = json.loads((ws / "manifest.json").read_text())
        del manifest["rollouts"][0]["sha256"]
        (ws / "manifest.json").write_text(json.dumps(manifest))
        problems = review_verify.check_workspace(ws)
        self.assertTrue(any("malformed" in p for p in problems), problems)

    def test_a_winner_recorded_after_init_stops_apply(self):
        ws = self.init()
        self.complete(ws)
        case = json.loads((self.refdb / "cases" / CASE).read_text())
        case["winner_tag"] = "demo-base"
        self.write_case(CASE, case)
        with self.assertRaises(SystemExit) as raised:
            self.apply(ws)
        self.assertIn("winner", str(raised.exception))

    def test_a_single_review_still_applies_without_a_record(self):
        # The original path is unchanged: one answer, no verification.
        case_file = self.refdb / "cases" / CASE
        case = json.loads(case_file.read_text())
        with redirect_stdout(StringIO()):
            request_review.record_review(case_file, case, "feedback-optimizer",
                                         "demo-base fails GRT-0116.")
        review = json.loads(case_file.read_text())["human_in_the_loop"][-1]
        self.assertNotIn("verification", review)
        self.assertIn("not self-assessed",
                      json.loads(case_file.read_text())["diagnosis"])


class PromptTests(unittest.TestCase):
    def test_each_session_is_pointed_at_its_own_phase(self):
        for agent, phase, record in (("review-resolver", "RESOLVE.md", "ledger_elim.json"),
                                     ("review-challenger", "CHALLENGE.md", "ledger_fals.json"),
                                     ("review-adjudicator", "ADJUDICATE.md", "finish.json")):
            text = (ROOT / ".claude" / "agents" / f"{agent}.md").read_text()
            self.assertIn(phase, text)
            self.assertIn(record, text)
            self.assertTrue((review_verify.PROMPTS / phase).is_file())

    def test_investigations_do_not_read_each_other(self):
        resolve = (review_verify.PROMPTS / "RESOLVE.md").read_text()
        challenge = (review_verify.PROMPTS / "CHALLENGE.md").read_text()
        self.assertIn("Do not read `ledger_fals.json`", resolve)
        self.assertIn("Do not read `ledger_elim.json`", challenge)


if __name__ == "__main__":
    unittest.main()
