"""store_retire.py must remove only what its rules name.

reference-db is the project's memory; the dangerous bug here is a rule that
quietly takes a real failure with it. Each test names a result that must be
kept as firmly as another must go.

    python3 -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "pipeline"))

import store_retire  # noqa: E402

STA_0572 = "[STA-0572] -core_area '-2.88' is not a positive float."


def result(tag, overrides=None, error=None):
    r = {"tag": tag, "overrides": overrides or {}}
    if error:
        r["error"] = error
    return r


class TestResultRules(unittest.TestCase):
    def why(self, design, res, case="x.json"):
        return store_retire.result_reason(design, res, case)

    def test_an_override_openlane_2_ignores_is_retired(self):
        hit = self.why("sram_wrapper", result("cand-rebuf8", {"RE_BUFFER_CELL": "buf_8"}))
        self.assertEqual(hit[0], "ignored-override")

    def test_an_ordinary_override_is_kept(self):
        self.assertIsNone(self.why("gcd", result("u45", {"FP_CORE_UTIL": 45})))

    def test_a_die_inherited_from_the_config_is_retired(self):
        """counter4_tinydie declares an 8x8 um die; a candidate that varied only
        the clock never gave the repair a die to grow."""
        hit = self.why("counter4_tinydie",
                       result("c-hd-clock_period4", {"CLOCK_PERIOD": 4}, STA_0572))
        self.assertEqual(hit[0], "inherited-die")

    def test_a_first_attempt_the_case_went_on_to_repair_is_kept(self):
        """The repaired run is the evidence that the loop works; its first
        iteration is part of it. (The WSL validation run lost it to a rule
        that was one condition too wide.)"""
        res = result("c-hd-clock_period4", {"CLOCK_PERIOD": 4}, STA_0572)
        self.assertIsNone(store_retire.result_reason(
            "counter4_tinydie", res, "x.json", repaired=True))
        self.assertIsNotNone(store_retire.result_reason(
            "counter4_tinydie", res, "x.json", repaired=False))

    def test_a_repaired_attempt_in_a_stored_case_survives_the_plan(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "cases").mkdir()
            (root / "cases" / "counter4_tinydie__2026-10-02.json").write_text(json.dumps({
                "design": "counter4_tinydie", "iterations": [
                    {"iteration": 1, "results": [result("c-hd-clock_period4",
                                                        {"CLOCK_PERIOD": 4}, STA_0572)]},
                    {"iteration": 2, "results": [result("c-hd-clock_period4-iter1",
                                                        {"CLOCK_PERIOD": 4,
                                                         "DIE_AREA": [0, 0, 16, 16]})]}]}))
            self.assertEqual(store_retire.plan_cases(root), [])

    def test_the_same_failure_with_an_explicit_die_is_kept(self):
        """cand-die8 set DIE_AREA itself and the repair did grow it - that is the
        record that validated the die pattern, and it stays."""
        self.assertIsNone(self.why(
            "counter4_tinydie", result("cand-die8", {"DIE_AREA": [0, 0, 8, 8]}, STA_0572)))

    def test_the_same_failure_on_a_design_whose_die_is_relative_is_kept(self):
        self.assertIsNone(self.why("counter4", result("t", {"CLOCK_PERIOD": 4}, STA_0572)))

    def test_other_real_failures_are_kept(self):
        for err in ("[RSZ-0090] max transition", "[PDN-0185] Insufficient width",
                    "[DPL-0036] Detailed placement failed."):
            self.assertIsNone(self.why("counter4_tinydie", result("t", {}, err)), err)

    def test_a_named_result_needs_both_the_case_and_the_tag(self):
        saved = dict(store_retire.NAMED)
        store_retire.NAMED[("c.json", "named-one")] = "a reason"
        try:
            self.assertIsNotNone(self.why("d", result("named-one"), "c.json"))
            self.assertIsNone(self.why("d", result("named-one"), "other.json"))
            self.assertIsNone(self.why("d", result("something-else"), "c.json"))
        finally:
            store_retire.NAMED.clear()
            store_retire.NAMED.update(saved)

    def test_the_sram_baseline_is_annotated_not_removed(self):
        """It is accurate evidence of an old configuration and the first rung
        of the Magic ladder the case narrates."""
        self.assertIsNone(self.why("sram_wrapper", result("cand-baseline"),
                                   "sram_wrapper__2026-09-10.json"))
        self.assertIn("sram_wrapper__2026-09-10.json", store_retire.ANNOTATIONS)


class TestFilePlans(unittest.TestCase):
    def make(self, root: Path):
        (root / "cases").mkdir()
        (root / "stdcells").mkdir()
        (root / "layouts").mkdir()
        return root

    def test_only_the_newest_signoff_of_a_cell_survives(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.make(Path(t))
            for name in ("inv_1__2026-09-13__085809", "inv_1__2026-09-13__090325",
                         "inv_1__2026-10-02__100000", "inv_2__2026-09-13__090325"):
                (root / "stdcells" / f"sky130_fd_sc_hd__{name}.json").write_text("{}")
            gone = {Path(i["path"]).name for i in store_retire.plan_stdcells(root)}
            self.assertEqual(gone, {"sky130_fd_sc_hd__inv_1__2026-09-13__085809.json",
                                    "sky130_fd_sc_hd__inv_1__2026-09-13__090325.json"})

    def test_a_cell_with_one_case_loses_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.make(Path(t))
            (root / "stdcells" / "sky130_fd_sc_hd__a__2026-09-13__090325.json").write_text("{}")
            self.assertEqual(store_retire.plan_stdcells(root), [])

    def test_a_layout_image_a_case_uses_is_kept_and_an_orphan_is_not(self):
        with tempfile.TemporaryDirectory() as t:
            root = self.make(Path(t))
            (root / "cases" / "d__2026-08-30.json").write_text(json.dumps(
                {"design": "d", "iterations": [], "layout_image": "layouts/used.png"}))
            (root / "layouts" / "used.png").write_bytes(b"x")
            (root / "layouts" / "orphan.png").write_bytes(b"x")
            gone = [i["path"] for i in store_retire.plan_images(root)]
            self.assertEqual([Path(g).name for g in gone], ["orphan.png"])


class TestApply(unittest.TestCase):
    def test_apply_removes_the_result_records_why_and_keeps_the_rest(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "cases").mkdir()
            case = {"design": "sram_wrapper", "winner_tag": None, "iterations": [
                {"iteration": 1, "results": [
                    result("cand-baseline"),
                    result("cand-rebuf8", {"RE_BUFFER_CELL": "buf_8"}),
                    result("cand-keep", {"FP_CORE_UTIL": 30})]}]}
            path = root / "cases" / "sram_wrapper__2026-08-26.json"
            path.write_text(json.dumps(case))
            items = store_retire.plan_cases(root)
            self.assertEqual([i["tag"] for i in items], ["cand-rebuf8"])
            out = store_retire.apply(items, root, today="2026-10-02")
            self.assertEqual(out["results"], 1)
            after = json.loads(path.read_text())
            self.assertEqual([r["tag"] for r in after["iterations"][0]["results"]],
                             ["cand-baseline", "cand-keep"])
            self.assertEqual(after["retired"][0]["rule"], "ignored-override")
            ledger = json.loads((root / "retired.json").read_text())
            self.assertEqual(ledger["entries"][0]["tag"], "cand-rebuf8")

    def test_an_iteration_emptied_by_the_retirement_is_dropped(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "cases").mkdir()
            path = root / "cases" / "sram_wrapper__2026-08-26.json"
            path.write_text(json.dumps({"design": "sram_wrapper", "iterations": [
                {"iteration": 1, "results": [result("a", {"RE_BUFFER_CELL": "x"})]},
                {"iteration": 2, "results": [result("b")]}]}))
            store_retire.apply(store_retire.plan_cases(root), root)
            self.assertEqual([i["iteration"] for i in json.loads(path.read_text())["iterations"]], [2])

    def test_the_plan_alone_changes_nothing(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "cases").mkdir()
            path = root / "cases" / "sram_wrapper__2026-08-26.json"
            body = json.dumps({"design": "sram_wrapper", "iterations": [
                {"iteration": 1, "results": [result("a", {"RE_BUFFER_CELL": "x"})]}]})
            path.write_text(body)
            store_retire.plan(root)
            self.assertEqual(path.read_text(), body)


class TestDiagnosisAgreesWithResults(unittest.TestCase):
    def test_a_retired_tag_is_no_longer_cited_and_the_note_says_why(self):
        case = {"diagnosis": "INVALID. cand-rebuf8 and cand-rebuf12 were duplicates."}
        changed = store_retire.note_retirement_in_diagnosis(
            case, ["cand-rebuf8", "cand-rebuf12"], "2026-10-02")
        self.assertTrue(changed)
        self.assertNotIn("cand-rebuf", case["diagnosis"].split("]", 1)[1])
        self.assertIn("a retired candidate and a retired candidate", case["diagnosis"])
        self.assertIn("2026-10-02", case["diagnosis"])

    def test_a_diagnosis_that_does_not_cite_them_is_left_alone(self):
        case = {"diagnosis": "Unrelated text."}
        self.assertFalse(store_retire.note_retirement_in_diagnosis(case, ["cand-x"], "d"))
        self.assertEqual(case["diagnosis"], "Unrelated text.")

    def test_a_case_with_no_diagnosis_is_fine(self):
        self.assertFalse(store_retire.note_retirement_in_diagnosis({}, ["cand-x"], "d"))

    def test_a_longer_tag_is_replaced_before_its_prefix(self):
        case = {"diagnosis": "cand-a10 and cand-a1"}
        store_retire.note_retirement_in_diagnosis(case, ["cand-a1", "cand-a10"], "d")
        self.assertNotIn("0", case["diagnosis"].split("] ", 1)[1].replace("2026", ""))


class TestAnnotations(unittest.TestCase):
    def test_a_note_is_added_once(self):
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / "cases").mkdir()
            name = "sram_wrapper__2026-09-10.json"
            path = root / "cases" / name
            path.write_text(json.dumps({"design": "sram_wrapper", "iterations": [],
                                        "diagnosis": "Original."}))
            items = store_retire.plan_notes(root)
            self.assertEqual(len(items), 1)
            store_retire.apply(items, root, today="2026-10-02")
            text = json.loads(path.read_text())["diagnosis"]
            self.assertTrue(text.startswith("[2026-10-02 update"))
            self.assertTrue(text.endswith("Original."))
            self.assertEqual(store_retire.plan_notes(root), [])  # idempotent

    def test_a_case_that_is_not_there_is_skipped(self):
        with tempfile.TemporaryDirectory() as t:
            (Path(t) / "cases").mkdir()
            self.assertEqual(store_retire.plan_notes(Path(t)), [])


if __name__ == "__main__":
    unittest.main()
