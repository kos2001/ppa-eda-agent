"""Tests for the per-library files a PDK is expected to ship.

OpenLane derives `libs.tech/openlane/<scl>/drc_exclude.cells` by
convention and validates it while *loading the PDK* — before any config
override applies. A library missing it is not degraded, it is unusable:
the flow exits during PDK load, writes no run directory, and the message
names a variable the user never set.

Found by trying to run counter4 on gf180mcu's 9-track library. All four
metal-stack variants (A/B/C/D) lack the file while their 7-track
siblings ship it, so every attempt failed identically regardless of the
design, and `--override-config PNR_EXCLUDED_CELL_FILE=...` did not help
because PDK loading fails first.

sky130 has the same gap in two of its libraries. With the files created,
the 9-track library completes all 78 stages.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import pdk_repair  # noqa: E402


def fake_pdk(tmp: Path, family: str, libs: dict[str, bool]) -> Path:
    """A PDK tree; each library maps to whether it ships the file."""
    version = tmp / family / "versions" / "abc123" / "variantA"
    tech = version / "libs.tech" / "openlane"
    for name, ships in libs.items():
        d = tech / name
        d.mkdir(parents=True)
        (d / "config.tcl").write_text("# always present")
        if ships:
            (d / "drc_exclude.cells").write_text("some_cell\n")
    return tmp


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.real = pdk_repair.PDK_ROOT
        pdk_repair.PDK_ROOT = self.tmp

    def tearDown(self):
        pdk_repair.PDK_ROOT = self.real

    def test_it_finds_a_library_missing_the_file(self):
        fake_pdk(self.tmp, "fam", {"x_fd_sc_7t": True, "x_fd_sc_9t": False})
        gaps = pdk_repair.missing("fam")
        self.assertEqual(len(gaps), 1)
        self.assertIn("9t", str(gaps[0]))

    def test_a_complete_pdk_reports_nothing(self):
        # Silence has to mean "nothing missing", or the check is noise.
        fake_pdk(self.tmp, "fam", {"x_fd_sc_7t": True, "x_fd_sc_9t": True})
        self.assertEqual(pdk_repair.missing("fam"), [])

    def test_only_standard_cell_directories_are_checked(self):
        # libs.tech/openlane also holds things like `custom_cells` and
        # OpenRCX rules, which are not libraries and ship no such file.
        fake_pdk(self.tmp, "fam", {"x_fd_sc_7t": True})
        (self.tmp / "fam" / "versions" / "abc123" / "variantA"
         / "libs.tech" / "openlane" / "custom_cells").mkdir()
        self.assertEqual(pdk_repair.missing("fam"), [])

    def test_an_absent_pdk_family_is_empty_not_an_error(self):
        self.assertEqual(pdk_repair.missing("never_installed"), [])


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.real = pdk_repair.PDK_ROOT
        pdk_repair.PDK_ROOT = self.tmp
        fake_pdk(self.tmp, "fam", {"x_fd_sc_7t": True, "x_fd_sc_9t": False})

    def tearDown(self):
        pdk_repair.PDK_ROOT = self.real

    def test_reporting_is_the_default(self):
        # It writes into a downloaded PDK. That should be asked for.
        got = pdk_repair.repair("fam")
        self.assertEqual(got["written"], [])
        self.assertEqual(len(got["missing"]), 1)

    def test_apply_creates_the_file(self):
        got = pdk_repair.repair("fam", apply=True)
        self.assertEqual(len(got["written"]), 1)
        self.assertEqual(pdk_repair.missing("fam"), [])

    def test_what_it_writes_says_it_was_not_shipped(self):
        # Someone finding this file later must be able to tell it apart
        # from the PDK's own, which for gf180mcu's 7-track library is a
        # real two-cell list.
        pdk_repair.repair("fam", apply=True)
        written = (self.tmp / "fam" / "versions" / "abc123" / "variantA"
                   / "libs.tech" / "openlane" / "x_fd_sc_9t"
                   / "drc_exclude.cells").read_text()
        self.assertIn("not shipped by the PDK", written)
        self.assertIn("Empty on purpose", written)

    def test_it_never_overwrites_a_real_list(self):
        # The 7-track file holds two genuine DRC exclusions. Replacing it
        # with an empty stub would trade a blocked library for a silent
        # DRC regression.
        path = (self.tmp / "fam" / "versions" / "abc123" / "variantA"
                / "libs.tech" / "openlane" / "x_fd_sc_7t" / "drc_exclude.cells")
        before = path.read_text()
        pdk_repair.repair("fam", apply=True)
        self.assertEqual(path.read_text(), before)

    def test_applying_twice_changes_nothing(self):
        first = pdk_repair.repair("fam", apply=True)
        second = pdk_repair.repair("fam", apply=True)
        self.assertEqual(len(first["written"]), 1)
        self.assertEqual(second["written"], [])


class RealPdkTests(unittest.TestCase):
    """Against the PDKs actually installed here."""

    def test_the_installed_pdks_are_now_complete(self):
        # Re-asserted rather than assumed: a volare re-fetch drops these
        # files, and the symptom is a flow that exits during PDK load
        # naming a variable nobody set. This test says to re-run
        # pdk_repair --apply.
        if not (pdk_repair.PDK_ROOT).is_dir():
            self.skipTest("no PDK installed")
        for family in ("sky130", "gf180mcu"):
            if not (pdk_repair.PDK_ROOT / family).is_dir():
                continue
            gaps = pdk_repair.missing(family)
            self.assertEqual(
                [str(p.name) for p in gaps], [],
                f"{family}: {len(gaps)} library/libraries cannot be run — "
                f"run `python3 pipeline/pdk_repair.py --pdk {family} --apply`")


if __name__ == "__main__":
    sys.exit(unittest.main())
