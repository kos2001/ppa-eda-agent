"""Tests for pipeline/stdcell_signoff.py — DRC and LVS on a standard
cell's real layout.

The parsing rules here are the ones that decide a verdict, and both were
written against real tool output from this repo's own runs: Magic's DRC
count line and netgen's summary. The rule they share with
orchestrator.score() is that an absent measurement is not a pass.

The LVS result also carries a finding worth keeping: across 37 cells run
on 2026-09-13, DRC was 0 everywhere and LVS matched on 36. a21oi_2 did
not, and reproducing it with the untouched CDL rules out this pipeline's
translation. No cause is claimed — a21oi_1 and nand3_2 have the same
shapes at the same m=2 and match.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import stdcell_signoff as ss  # noqa: E402


MAGIC_CLEAN = """Cell sky130_fd_sc_hd__inv_2 read from path /pdk/...
Loading DRC CIF style.
DRC_COUNT 0
Extracting sky130_fd_sc_hd__inv_2 into sky130_fd_sc_hd__inv_2.ext:
exttospice finished.
DONE
"""

# What a run that died before the count looks like — Magic prints plenty
# and never reaches the line.
MAGIC_DIED = """Magic 8.3 revision 489
Could not find file '/root/.volare/.../sky130A.tech' in any of these directories
"""

NETGEN_MATCH = """Circuit 1 contains 2 devices, Circuit 2 contains 2 devices.
Circuit 1 contains 6 nets,    Circuit 2 contains 6 nets.

Final result:
Circuits match uniquely.
LVS Done.
"""

NETGEN_MISMATCH = """Circuit was modified by parallel/series device merging.
Circuit 1 contains 8 devices, Circuit 2 contains 6 devices. *** MISMATCH ***
Circuit 1 contains 11 nets,    Circuit 2 contains 10 nets. *** MISMATCH ***
Netlists do not match.
"""


class DrcParsing(unittest.TestCase):
    def test_the_count_magic_printed_is_the_count(self):
        self.assertEqual(ss.parse_drc(MAGIC_CLEAN), 0)

    def test_a_run_that_never_reached_the_count_is_none_not_zero(self):
        """None is not zero. Reading a dead run as a clean cell is the
        same mistake score() refuses when a signoff metric is missing —
        and this exact failure happened: without PDK_ROOT in the
        container, Magic looks for the tech file under the volare build
        path baked in at PDK build time and dies there."""
        self.assertIsNone(ss.parse_drc(MAGIC_DIED))

    def test_a_nonzero_count_survives(self):
        self.assertEqual(ss.parse_drc("DRC_COUNT 17\n"), 17)


class LvsParsing(unittest.TestCase):
    def test_only_a_unique_match_is_a_match(self):
        verdict = ss.parse_lvs(NETGEN_MATCH)
        self.assertTrue(verdict["match"])
        self.assertFalse(verdict["uncertain"])

    def test_a_mismatch_is_not_rounded_up(self):
        verdict = ss.parse_lvs(NETGEN_MISMATCH)
        self.assertFalse(verdict["match"])
        self.assertTrue(verdict["uncertain"])

    def test_the_counts_are_kept_because_they_are_the_diagnosis(self):
        """8 devices against 6 and 11 nets against 10 is what says "the
        layout carries an internal node the schematic does not"."""
        verdict = ss.parse_lvs(NETGEN_MISMATCH)
        self.assertEqual(verdict["devices"], [8, 6])
        self.assertEqual(verdict["nets"], [11, 10])

    def test_a_clean_run_still_records_its_counts(self):
        self.assertEqual(ss.parse_lvs(NETGEN_MATCH)["devices"], [2, 2])


class Verdict(unittest.TestCase):
    def test_the_pdk_ships_the_layout_this_reads(self):
        # If the library moves, every run here is signing off nothing.
        self.assertTrue(ss.cell_mag("sky130_fd_sc_hd__inv_2").is_file())

    def test_the_store_holds_the_runs_that_were_made(self):
        stored = ss.cases()
        if not stored:
            self.skipTest("no signoff cases recorded in this checkout")
        self.assertTrue(all("drc_errors" in c and "lvs" in c for c in stored))

    def test_the_scan_counts_against_the_whole_library(self):
        """A report that only counted the cells already run would make a
        store of three look complete."""
        report = ss.scan()
        self.assertGreaterEqual(report["library"], 400)
        self.assertLessEqual(report["signed_off"], report["library"])


if __name__ == "__main__":
    unittest.main()
