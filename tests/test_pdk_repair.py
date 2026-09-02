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
                   / "drc_exclude.cells").read_text(encoding="utf-8")
        self.assertIn("not shipped by the PDK", written)
        self.assertIn("Empty on purpose", written)

    def test_it_never_overwrites_a_real_list(self):
        # The 7-track file holds two genuine DRC exclusions. Replacing it
        # with an empty stub would trade a blocked library for a silent
        # DRC regression.
        path = (self.tmp / "fam" / "versions" / "abc123" / "variantA"
                / "libs.tech" / "openlane" / "x_fd_sc_7t" / "drc_exclude.cells")
        before = path.read_text(encoding="utf-8")
        pdk_repair.repair("fam", apply=True)
        self.assertEqual(path.read_text(encoding="utf-8"), before)

    def test_applying_twice_changes_nothing(self):
        first = pdk_repair.repair("fam", apply=True)
        second = pdk_repair.repair("fam", apply=True)
        self.assertEqual(len(first["written"]), 1)
        self.assertEqual(second["written"], [])


class ExtractionRulesetTests(unittest.TestCase):
    """Parasitic-extraction rulesets: reported, never repaired.

    OpenLane validates RCX_RULESETS during PDK load exactly like
    drc_exclude.cells, and a variant missing them quits before writing a
    run directory. Found by adding gf180mcuA and gf180mcuB as
    technologies: both ship none, C and D ship all of them, and 34 runs
    failed identically before anyone read the message.
    """

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.real = pdk_repair.PDK_ROOT
        pdk_repair.PDK_ROOT = self.tmp

    def tearDown(self):
        pdk_repair.PDK_ROOT = self.real

    def make(self, variant: str, rulesets: list[str]):
        tech = (self.tmp / "fam" / "versions" / "v1" / variant
                / "libs.tech" / "openlane")
        (tech / "x_fd_sc_7t").mkdir(parents=True)
        (tech / "x_fd_sc_7t" / "drc_exclude.cells").write_text("")
        for name in rulesets:
            (tech / name).write_text("rules")

    def test_a_variant_with_no_rulesets_is_reported(self):
        self.make("famA", [])
        self.assertTrue(pdk_repair.missing_rcx("fam"))

    def test_a_bare_named_ruleset_counts(self):
        # gf180mcuD's shape.
        self.make("famA", [f"rules.openrcx.famA.{c}" for c in ("min", "nom", "max")])
        self.assertEqual(pdk_repair.missing_rcx("fam"), [])

    def test_a_suffixed_ruleset_counts(self):
        # sky130A's shape: no bare file at all, only .calibre, .magic and
        # .spef_extractor. An exact-name check reported the default PDK,
        # behind two hundred completed runs, as unusable.
        self.make("famA", [f"rules.openrcx.famA.{c}.{s}"
                           for c in ("min", "nom", "max")
                           for s in ("calibre", "magic", "spef_extractor")])
        self.assertEqual(pdk_repair.missing_rcx("fam"), [])

    def test_a_partial_set_is_still_reported(self):
        # Missing one corner is as fatal as missing all three.
        self.make("famA", ["rules.openrcx.famA.nom.magic"])
        gaps = [p.name for p in pdk_repair.missing_rcx("fam")]
        self.assertEqual(sorted(gaps),
                         ["rules.openrcx.famA.max", "rules.openrcx.famA.min"])

    def test_apply_never_writes_a_ruleset(self):
        # The distinction from drc_exclude.cells, and the reason this is
        # a separate function. An empty exclusion list is truthful —
        # nothing is excluded. An extraction ruleset has no truthful
        # empty form: a stub would make the flow run and return
        # parasitics that are wrong and look measured.
        self.make("famA", [])
        got = pdk_repair.repair("fam", apply=True)
        self.assertTrue(got["unusable_variants"])
        tech = (self.tmp / "fam" / "versions" / "v1" / "famA"
                / "libs.tech" / "openlane")
        self.assertEqual(list(tech.glob("rules.openrcx.*")), [])
        self.assertTrue(pdk_repair.missing_rcx("fam"))


class RealPdkExtractionTests(unittest.TestCase):
    """Against the PDKs installed here."""

    def test_the_working_pdk_is_not_condemned(self):
        # The negative control this check needed and did not have: it
        # first reported sky130A unusable, which is the PDK almost every
        # recorded run used. A gate that fires on the working case gets
        # switched off rather than obeyed.
        if not (pdk_repair.PDK_ROOT / "sky130").is_dir():
            self.skipTest("no sky130 installed")
        self.assertEqual(pdk_repair.repair("sky130")["unusable_variants"], [])

    def test_the_two_variants_that_cannot_run_are_named(self):
        if not (pdk_repair.PDK_ROOT / "gf180mcu").is_dir():
            self.skipTest("no gf180mcu installed")
        # A and B are the two that cannot run, and being unable to run is
        # also why a checkout may not carry them: they are ~3.7G that no
        # recorded run has ever used, so a disk-constrained checkout
        # deletes them. Assert on the ones present rather than on the
        # full pair, or this fails on exactly the checkouts that took the
        # measurement's advice. The synthetic cases above still pin the
        # rule itself; this one only checks it against a real PDK.
        present = {v.name for v in pdk_repair.variants("gf180mcu")}
        expected = sorted({"gf180mcuA", "gf180mcuB"} & present)
        if not expected:
            self.skipTest("neither unusable variant is installed")
        got = pdk_repair.repair("gf180mcu")["unusable_variants"]
        self.assertEqual(sorted(p.split("/")[-1] for p in got), expected)


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
