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


class Vacuous(unittest.TestCase):
    """Cells with no transistors are not mismatches.

    fill, decap, diode, conb and the spare-cell macro have zero devices
    in the CDL, so LVS compares nothing. "Nothing matched nothing" is
    neither a pass nor a failure — the same distinction equiv_check.py
    draws with its own `vacuous` flag, and counting the library's filler
    among the failures would put it in the same column as a real
    discrepancy."""

    def test_a_cell_with_no_devices_is_not_in_the_cdl_as_a_circuit(self):
        import stdcell_schematic
        for cell in ("sky130_fd_sc_hd__fill_4", "sky130_fd_sc_hd__conb_1",
                     "sky130_fd_sc_hd__diode_2"):
            block = stdcell_schematic.subckt(cell)
            if block is None:
                continue
            self.assertEqual(stdcell_schematic.device_count(block), 0, cell)

    def test_the_stored_verdicts_separate_it_from_a_mismatch(self):
        stored = [c for c in ss.cases() if c.get("verdict")]
        if not stored:
            self.skipTest("no cases carrying a verdict in this checkout")
        self.assertTrue(set(c["verdict"] for c in stored)
                        <= {"clean", "lvs_mismatch", "drc_errors", "vacuous",
                            "unmeasured"})
        for case in stored:
            if case["verdict"] == "vacuous":
                self.assertFalse(case["passed"], case["cell"])


class DrcRules(unittest.TestCase):
    """A count says a cell is dirty; the rule says whether that means
    anything. Both tap cells that report errors report only met1.6 —
    Metal1 minimum area — which is what a cell meant to be tiled looks
    like checked alone. Without the rule name, three errors on a foundry
    cell reads as "the foundry ships a dirty cell"."""

    MAGIC_WITH_RULES = """DRC_COUNT 3
DRC_WHY_BEGIN
Metal1 minimum area < 0.083um^2 (met1.6)
Metal1 minimum area < 0.083um^2 (met1.6)
DRC_WHY_END
"""

    def test_the_rules_are_captured_and_deduplicated(self):
        self.assertEqual(ss.parse_drc_rules(self.MAGIC_WITH_RULES),
                         ["Metal1 minimum area < 0.083um^2 (met1.6)"])

    def test_a_clean_cell_names_no_rules(self):
        self.assertEqual(ss.parse_drc_rules(MAGIC_CLEAN), [])

    def test_output_without_the_markers_yields_nothing_rather_than_noise(self):
        self.assertEqual(ss.parse_drc_rules("some magic chatter"), [])

    def test_the_stored_tap_cases_name_that_rule(self):
        stored = {c["cell"]: c for c in ss.cases() if c.get("drc_rules")}
        if not stored:
            self.skipTest("no cases carrying DRC rules in this checkout")
        for cell, case in stored.items():
            self.assertTrue(all(r.strip() for r in case["drc_rules"]), cell)


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
