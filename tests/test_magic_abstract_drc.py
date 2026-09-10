"""Tests for pipeline/magic_abstract_drc.py.

The measurement behind it: sram_wrapper with MAGIC_DRC_USE_GDS=false
and KLayout streamout reached signoff with 382 Magic DRC errors, every
one nwell.4, none inside the macro, in strips one row pitch apart in the
standard-cell rows beside it — and KLayout DRC on the same GDS reported
zero. The reclassification must fire on exactly that shape and on
nothing looser.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import magic_abstract_drc as mad  # noqa: E402

REPORT = """sram_wrapper
----------------------------------------
All nwells must contain metal-connected N+ taps (nwell.4)
----------------------------------------
 5.330um 311.385um 100.010um 314.215um
 5.330um 305.945um 100.010um 308.775um
----------------------------------------
[INFO] COUNT: 2
[INFO] Should be divided by 3 or 4
"""

MACRO = [("u_sram", (110.0, 150.0, 589.78, 547.5))]


class ParseTests(unittest.TestCase):

    def test_reads_rule_ids_and_boxes(self):
        rules = mad.parse_report(REPORT)
        self.assertEqual(list(rules), ["nwell.4"])
        self.assertEqual(rules["nwell.4"][0], (5.33, 311.385, 100.01, 314.215))
        self.assertEqual(len(rules["nwell.4"]), 2)

    def test_two_rules_are_kept_apart(self):
        text = REPORT + "Metal1 minimum width (met1.1)\n----\n 1um 1um 2um 2um\n"
        rules = mad.parse_report(text)
        self.assertEqual(sorted(rules), ["met1.1", "nwell.4"])

    def test_lef_size(self):
        lef = "MACRO foo\n  CLASS BLOCK ;\n  SIZE 479.78 BY 397.5 ;\nEND foo\n"
        self.assertEqual(mad.lef_size(lef, "foo"), (479.78, 397.5))
        self.assertIsNone(mad.lef_size(lef, "bar"))


class ClassifyTests(unittest.TestCase):

    def test_the_measured_shape_is_the_artefact(self):
        got = mad.classify(mad.parse_report(REPORT), use_gds=False, macros=MACRO)
        self.assertTrue(got["abstract_artefact"])
        self.assertEqual(got["count"], 2)
        self.assertIn("u_sram", got["macros_checked"])

    def test_gds_based_drc_is_never_reclassified(self):
        # G-capture: 2,831,364 errors with Magic reading the GDS. Those
        # are geometry (against a deck missing the macro's rules), and
        # this module has no business calling them benign.
        got = mad.classify(mad.parse_report(REPORT), use_gds=True, macros=MACRO)
        self.assertFalse(got["abstract_artefact"])

    def test_any_other_rule_blocks_it(self):
        text = REPORT + "Metal1 minimum width (met1.1)\n----\n 1um 1um 2um 2um\n"
        got = mad.classify(mad.parse_report(text), use_gds=False, macros=MACRO)
        self.assertFalse(got["abstract_artefact"])
        self.assertIn("met1.1", got["reason"])

    def test_a_box_inside_the_macro_blocks_it(self):
        text = REPORT + " 200um 200um 210um 210um\n"
        got = mad.classify(mad.parse_report(text), use_gds=False, macros=MACRO)
        self.assertFalse(got["abstract_artefact"])
        self.assertIn("inside a macro", got["reason"])

    def test_no_errors_is_not_an_artefact(self):
        got = mad.classify({}, use_gds=False, macros=MACRO)
        self.assertFalse(got["abstract_artefact"])
        self.assertEqual(got["count"], 0)

    def test_a_design_without_macros_still_qualifies(self):
        # The artefact is about standard-cell abstracts; a macro-free
        # design in LEF mode shows the same rows.
        got = mad.classify(mad.parse_report(REPORT), use_gds=False, macros=[])
        self.assertTrue(got["abstract_artefact"])


class VerdictTests(unittest.TestCase):

    def test_moves_the_magic_count_from_violations_to_unverified(self):
        verdict = {"passed": False, "violations": ["382 Magic DRC error(s)"],
                   "unverified": []}
        result = mad.classify(mad.parse_report(REPORT), use_gds=False, macros=MACRO)
        out = mad.apply_to_verdict(verdict, result)
        self.assertEqual(out["violations"], [])
        self.assertEqual(len(out["unverified"]), 1)
        self.assertIn("nwell.4", out["unverified"][0])
        # Unverified still blocks a pass: Magic did not see the geometry.
        self.assertFalse(out["passed"])
        self.assertTrue(out["magic_abstract_drc"]["abstract_artefact"])

    def test_leaves_other_violations_alone(self):
        verdict = {"passed": False, "unverified": [],
                   "violations": ["382 Magic DRC error(s)", "16 max-slew (DRV) violation(s)"]}
        result = mad.classify(mad.parse_report(REPORT), use_gds=False, macros=MACRO)
        out = mad.apply_to_verdict(verdict, result)
        self.assertEqual(out["violations"], ["16 max-slew (DRV) violation(s)"])

    def test_a_non_artefact_changes_nothing(self):
        verdict = {"passed": False, "violations": ["3 Magic DRC error(s)"], "unverified": []}
        out = mad.apply_to_verdict(dict(verdict), {"abstract_artefact": False})
        self.assertEqual(out, verdict)
        self.assertEqual(mad.apply_to_verdict(dict(verdict), None), verdict)


if __name__ == "__main__":
    unittest.main()
