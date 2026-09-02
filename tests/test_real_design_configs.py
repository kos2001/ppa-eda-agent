"""Runs the pure helpers against the repository's actual design configs.

Written after a bug survived nineteen passing tests of its own module.
`featurize()` computed `die_area_um2` as an int and `_ranges()` kept only
values passing `isinstance(x, float)`, so DIE_AREA was silently dropped
from the distance function. Every fixture in test_surrogate.py builds
`FP_CORE_UTIL` values that `featurize` explicitly casts to float, so
none of them exercised the integer path — while every real design writes
`"DIE_AREA": [0, 0, 64, 64]`, all ints.

The fixtures and the bug shared an author, and therefore a blind spot.
These tests use the real files instead: pipeline/designs/*/config.json
and run_spec.json as they are actually written. They cannot be made
convenient, which is the point.
"""
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "pipeline"))

import cdc_check  # noqa: E402
import design_rules  # noqa: E402
import surrogate  # noqa: E402

DESIGNS = sorted(
    d for d in (REPO / "pipeline" / "designs").iterdir()
    if d.is_dir() and (d / "config.json").is_file()
) if (REPO / "pipeline" / "designs").is_dir() else []


class RealConfigTests(unittest.TestCase):
    def setUp(self):
        if not DESIGNS:
            self.skipTest("no designs on disk")

    def test_there_are_designs_to_check(self):
        self.assertGreaterEqual(len(DESIGNS), 3, [d.name for d in DESIGNS])

    def test_every_die_area_is_written_as_integers(self):
        """Records the fact the bug depended on.

        If a design ever starts using floats this test is not wrong, but
        the reason these tests exist would have changed and the note
        above should be re-read.
        """
        seen = 0
        for design in DESIGNS:
            die = json.loads((design / "config.json").read_text(encoding="utf-8")).get("DIE_AREA")
            if die is None:
                continue
            seen += 1
            self.assertTrue(all(isinstance(v, int) for v in die),
                            f"{design.name}: {die}")
        self.assertGreater(seen, 0, "no design declares DIE_AREA")

    def test_die_area_survives_featurization_for_every_real_design(self):
        for design in DESIGNS:
            cfg = json.loads((design / "config.json").read_text(encoding="utf-8"))
            if "DIE_AREA" not in cfg:
                continue
            feats = surrogate.featurize({"overrides": cfg})
            self.assertIsNotNone(feats["die_area_um2"], design.name)
            self.assertGreater(feats["die_area_um2"], 0, design.name)

    def test_real_designs_are_comparable_to_each_other(self):
        """The failure the bug actually caused: two configs differing
        only by die size had no comparable feature, so neither was ever
        the other's neighbour."""
        rows = []
        for design in DESIGNS:
            cfg = json.loads((design / "config.json").read_text(encoding="utf-8"))
            if "DIE_AREA" in cfg:
                rows.append({"design": "x", "overrides": {"DIE_AREA": cfg["DIE_AREA"]}})
        if len(rows) < 2:
            self.skipTest("need two designs with a DIE_AREA")
        ranges = surrogate._ranges(rows)
        self.assertIn("die_area_um2", ranges)
        d = surrogate.distance(rows[0], rows[1], ranges)
        self.assertIsNotNone(d, "real designs must be comparable")

    def test_constraints_collect_for_every_real_design(self):
        for design in DESIGNS:
            got = design_rules.read_design_constraints(design)
            self.assertTrue(got["settings"], design.name)

    def test_declared_clocks_parse_for_every_real_design(self):
        # sram_wrapper writes "clk"; cdc_twoclock writes "clk_a clk_b".
        # Both spellings are real and must both work.
        found_multi = False
        for design in DESIGNS:
            ports = cdc_check.declared_clock_ports(design)
            cfg = json.loads((design / "config.json").read_text(encoding="utf-8"))
            if "CLOCK_PORT" in cfg:
                self.assertTrue(ports, design.name)
            if len(ports) > 1:
                found_multi = True
        self.assertTrue(found_multi,
                        "expected at least one multi-clock design")

    def test_run_specs_parse_and_declare_something_to_run(self):
        for design in DESIGNS:
            spec_file = design / "run_spec.json"
            if not spec_file.is_file():
                continue
            spec = json.loads(spec_file.read_text(encoding="utf-8"))
            has_work = bool(spec.get("candidates") or spec.get("sweeps")
                            or spec.get("explore_synthesis"))
            self.assertTrue(has_work, f"{design.name} has nothing to run")


class RealCaseStoreTests(unittest.TestCase):
    """The stored cases, as they are, not as a fixture imagines them."""

    def setUp(self):
        if not (REPO / "reference-db" / "cases").is_dir():
            self.skipTest("no reference-db")
        self.data = surrogate.load_dataset(REPO / "reference-db")

    def test_every_row_featurizes_without_raising(self):
        for row in self.data:
            surrogate.featurize(row)

    def test_rows_with_a_die_area_keep_it_after_featurization(self):
        checked = 0
        for row in self.data:
            if "DIE_AREA" in (row["overrides"] or {}):
                checked += 1
                self.assertIsNotNone(
                    surrogate.featurize(row)["die_area_um2"],
                    row["overrides"])
        if checked == 0:
            self.skipTest("no recorded run overrides DIE_AREA")

    def test_both_targets_run_against_the_real_store(self):
        # Not asserting a verdict — that changes as data accumulates.
        # Asserting only that neither target crashes on real rows.
        for field in surrogate.TARGETS:
            got = surrogate.evaluate(self.data, field)
            self.assertIn("verdict", got)
            self.assertGreaterEqual(got["n_total"], 0)


if __name__ == "__main__":
    unittest.main()
