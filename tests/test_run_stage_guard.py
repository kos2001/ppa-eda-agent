"""Tests for run_stage.reject_ignored_overrides.

This guard exists because an ignored override does not look like a
failure — it looks like a result. Three sram_wrapper candidates
configured with `RE_BUFFER_CELL` (an OpenLane 1 name with no OpenLane 2
equivalent) ran to byte-identical failures, which briefly read as
evidence that stronger buffers do not help. They were duplicates of the
baseline.

The negative controls matter as much as the positive ones here: a guard
that fires on every run, or that fires on OpenLane's warnings about keys
we did not pass, would be turned off within a day.
"""
import unittest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from run_stage import (  # noqa: E402
    IgnoredOverrideError, reject_ignored_overrides,
)

# Copied verbatim from the run that motivated this guard, so a change to
# OpenLane's wording fails here rather than silently disarming it.
REAL_LOG = """\
[14:11:22] WARNING  An unknown key 'RE_BUFFER_CELL' was provided.  config.py:720
[14:11:22] VERBOSE  Running 'Checker.LintErrors'
"""


class GuardFiresTests(unittest.TestCase):
    def test_fires_on_the_real_openlane_wording(self):
        with self.assertRaises(IgnoredOverrideError) as cm:
            reject_ignored_overrides(
                ["RE_BUFFER_CELL=sky130_fd_sc_hd__buf_8"], REAL_LOG, "cand-x")
        self.assertIn("RE_BUFFER_CELL", str(cm.exception))
        self.assertIn("cand-x", str(cm.exception))

    def test_reports_every_ignored_key(self):
        log = ("An unknown key 'A_KEY' was provided.\n"
               "An unknown key 'B_KEY' was provided.\n")
        with self.assertRaises(IgnoredOverrideError) as cm:
            reject_ignored_overrides(["A_KEY=1", "B_KEY=2"], log, "t")
        msg = str(cm.exception)
        self.assertIn("A_KEY", msg)
        self.assertIn("B_KEY", msg)
        self.assertIn("2 override(s)", msg)

    def test_fires_even_when_only_one_of_several_was_ignored(self):
        log = "An unknown key 'BAD' was provided."
        with self.assertRaises(IgnoredOverrideError):
            reject_ignored_overrides(["GOOD=1", "BAD=2"], log, "t")

    def test_value_containing_equals_still_matches_on_the_key(self):
        log = "An unknown key 'K' was provided."
        with self.assertRaises(IgnoredOverrideError):
            reject_ignored_overrides(["K=a=b"], log, "t")


class GuardStaysQuietTests(unittest.TestCase):
    """A guard that fires on healthy runs gets disabled, not obeyed."""

    def test_silent_on_clean_output(self):
        reject_ignored_overrides(["FP_CORE_UTIL=45"], "flow completed", "t")

    def test_silent_when_no_overrides_were_passed(self):
        reject_ignored_overrides([], REAL_LOG, "t")

    def test_silent_when_the_unknown_key_was_not_ours(self):
        # OpenLane also warns about unknown keys inside config.json.
        # Failing the run for those would block designs that carry
        # deliberate extra entries.
        reject_ignored_overrides(["FP_CORE_UTIL=45"], REAL_LOG, "t")

    def test_substring_of_our_key_does_not_count(self):
        log = "An unknown key 'RE_BUFFER' was provided."
        reject_ignored_overrides(["RE_BUFFER_CELL=x"], log, "t")


if __name__ == "__main__":
    unittest.main()
