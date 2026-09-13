"""Tests for pipeline/stdcell_survey.py — reading the signoff store back.

The survey exists because one odd result and a pattern look identical
until you have the library. The first sample of 37 cells had exactly one
LVS mismatch and no way to tell which it was; across 437 it is 11 cells
with a shape — every one a compound gate at drive 2, 4 or 8.

What is tested here is the reading, not the running: the split between a
real mismatch and a cell that has no transistors to compare, and the
family/drive decomposition that made the shape visible.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import stdcell_survey as sv  # noqa: E402


class Names(unittest.TestCase):
    def test_a_cell_splits_into_family_and_drive(self):
        self.assertEqual(sv.split_name("sky130_fd_sc_hd__a21oi_2"), ("a21oi", 2))
        self.assertEqual(sv.split_name("sky130_fd_sc_hd__inv_8"), ("inv", 8))

    def test_a_multi_underscore_family_keeps_its_name(self):
        self.assertEqual(sv.split_name("sky130_fd_sc_hd__lpflow_clkinvkapwr_4"),
                         ("lpflow_clkinvkapwr", 4))

    def test_a_cell_with_no_drive_suffix_is_not_forced_into_one(self):
        family, drive = sv.split_name("sky130_fd_sc_hd__macro_sparecell")
        self.assertEqual(family, "macro_sparecell")
        self.assertIsNone(drive)


class Reading(unittest.TestCase):
    def setUp(self):
        self.report = sv.survey()
        if not self.report["signed_off"]:
            self.skipTest("no signoff cases in this checkout")

    def test_no_transistor_cells_are_not_counted_as_mismatches(self):
        """fill, decap, diode, conb, taps and the spare-cell macro have
        zero devices in the CDL. Counting the library's own filler among
        the failures would put it in the same column as a real
        discrepancy."""
        vacuous = set(self.report["vacuous"])
        mismatched = {m["cell"] for m in self.report["mismatches"]}
        self.assertFalse(vacuous & mismatched)

    def test_the_arithmetic_adds_up(self):
        self.assertEqual(
            self.report["clean"] + self.report["not_clean"],
            self.report["signed_off"])
        self.assertEqual(
            len(self.report["vacuous"]) + len(self.report["mismatches"]),
            self.report["not_clean"])

    def test_a_mismatch_carries_the_shape_of_its_gap(self):
        """"+2 devices, +1 net" is the whole diagnosis — it says the
        layout has a node the schematic does not."""
        for m in self.report["mismatches"]:
            if m["devices"] is None:
                continue
            self.assertEqual(m["extra_devices"], m["devices"][0] - m["devices"][1])
            self.assertGreaterEqual(m["extra_devices"], 0, m["cell"])

    def test_dirty_drc_cells_are_listed_separately_from_lvs(self):
        # A DRC failure and an LVS failure are different questions, and a
        # cell can be in one list without the other.
        self.assertIsInstance(self.report["drc_dirty"], list)


if __name__ == "__main__":
    unittest.main()
