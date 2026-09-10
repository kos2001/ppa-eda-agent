"""Tests for pipeline/gf180_drc.py — the KLayout DRC OpenLane skips on gf180mcu.

OpenLane 2.3.10's KLayout.DRC.run() only calls run_sky130(); for any
other PDK it warns and writes no metric, and score() rightly files the
absent metric as unverified. 170 gf180mcu runs in the store never
passed for that reason. The PDK ships GlobalFoundries' deck and driver;
this module drives the deck the way the driver does. These tests pin
the parts that must match the driver line for line — the variant
table, the runset assembly, the switch set — and the parsing of the
report database, without Docker.
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "pipeline"))

import gf180_drc  # noqa: E402

LYRDB = """<?xml version="1.0" encoding="utf-8"?>
<report-database>
 <description>gf180mcu</description>
 <original-file/>
 <generator>drc: script=main.drc</generator>
 <top-cell>gcd</top-cell>
 <tags/>
 <categories>
  <category><name>M1.a</name><description>Min width</description></category>
  <category><name>V1.b</name><description>Via enclosure</description></category>
 </categories>
 <cells><cell><name>gcd</name></cell></cells>
 <items>
  <item><tags/><category>'M1.a'</category><cell>gcd</cell><values><value>polygon: (0,0;1,1)</value></values></item>
  <item><tags/><category>'M1.a'</category><cell>gcd</cell><values><value>polygon: (2,2;3,3)</value></values></item>
  <item><tags/><category>'V1.b'</category><cell>gcd</cell><values><value>edge-pair: (4,4;5,5)</value></values></item>
 </items>
</report-database>
"""


class VariantTests(unittest.TestCase):

    def test_matches_run_drc_py_s_table(self):
        # gf180mcuD is what every gf180 run in the store used; the
        # driver's own table says 11K top metal, MIM option B, 5 layers.
        self.assertEqual(gf180_drc.variant_for("gf180mcuD"), "D")
        self.assertEqual(gf180_drc.VARIANTS["D"],
                         {"metal_top": "11K", "mim_option": "B", "metal_level": "5LM"})
        self.assertEqual(gf180_drc.VARIANTS["C"]["metal_level"], "5LM")
        self.assertEqual(gf180_drc.VARIANTS["A"]["metal_level"], "3LM")

    def test_refuses_a_non_gf180_pdk(self):
        # sky130 has OpenLane's own binding; running the wrong deck on
        # it would produce a number that means nothing.
        with self.assertRaises(ValueError):
            gf180_drc.variant_for("sky130A")
        with self.assertRaises(ValueError):
            gf180_drc.variant_for("gf180mcuZ")


class SwitchTests(unittest.TestCase):

    def test_driver_defaults_are_reproduced(self):
        sw = gf180_drc.switches("D", input_path="/run/final/gds/gcd.gds",
                                topcell="gcd", report="/run/x.lyrdb", threads=4)
        # generate_klayout_switches() with no --no_* flags, flat mode,
        # density off, and run_check()'s table_name.
        self.assertEqual(sw["feol"], "true")
        self.assertEqual(sw["beol"], "true")
        self.assertEqual(sw["offgrid"], "true")
        self.assertEqual(sw["conn_drc"], "true")
        self.assertEqual(sw["density"], "false")
        self.assertEqual(sw["run_mode"], "flat")
        self.assertEqual(sw["table_name"], "main")
        self.assertEqual(sw["metal_level"], "5LM")
        self.assertEqual(sw["thr"], "4")
        self.assertEqual(sw["topcell"], "gcd")


class RunsetTests(unittest.TestCase):

    def test_runset_is_main_then_tables_then_tail_with_layers_def_beside(self):
        deck = Path(tempfile.mkdtemp())
        rules = deck / "rule_decks"
        rules.mkdir()
        for name, body in [("main", "MAIN\n"), ("tail", "TAIL\n"),
                           ("layers_def", "LAYERS\n"), ("metal1", "M1\n"),
                           ("via1", "V1\n"), ("antenna", "ANT\n"),
                           ("density", "DEN\n")]:
            (rules / f"{name}.drc").write_text(body, encoding="utf-8")
        work = deck / "work"
        runset = gf180_drc.build_runset(deck, work)
        self.assertEqual(runset.read_text(encoding="utf-8"), "MAIN\nM1\nV1\nTAIL\n")
        self.assertEqual((work / "layers_def.drc").read_text(encoding="utf-8"), "LAYERS\n")
        # antenna and density are opt-in in the driver and stay out here.
        self.assertEqual(gf180_drc.rule_tables(deck), ["metal1", "via1"])

    def test_the_real_pdk_deck_is_where_this_expects_when_installed(self):
        deck = gf180_drc.deck_dir("gf180mcuD")
        try:
            present = deck.is_dir()
        except OSError:
            # volare's pdk/<variant> symlinks made from WSL are not
            # traversable by Windows Python (WinError 1920).
            present = False
        if not present:
            self.skipTest("gf180mcu PDK not fetched into pdk/")
        tables = gf180_drc.rule_tables(deck)
        self.assertIn("metal1", tables)
        self.assertNotIn("main", tables)
        self.assertNotIn("antenna", tables)
        self.assertTrue((deck / "rule_decks" / "layers_def.drc").is_file())


class ReportTests(unittest.TestCase):

    def test_counts_items_and_groups_by_rule(self):
        path = Path(tempfile.mkdtemp()) / "gcd_main.lyrdb"
        path.write_text(LYRDB, encoding="utf-8")
        got = gf180_drc.count_violations(path)
        self.assertEqual(got["count"], 3)
        self.assertEqual(got["per_rule"], {"M1.a": 2, "V1.b": 1})

    def test_an_empty_database_is_zero_not_missing(self):
        path = Path(tempfile.mkdtemp()) / "clean_main.lyrdb"
        path.write_text(LYRDB.split("<items>")[0] + "<items/>\n</report-database>\n",
                        encoding="utf-8")
        self.assertEqual(gf180_drc.count_violations(path)["count"], 0)


class WorkDirTests(unittest.TestCase):

    def test_work_dir_is_not_mistaken_for_an_openlane_step(self):
        import step_coverage
        self.assertIsNone(step_coverage._STEP_DIR.match(gf180_drc.WORK_DIR))


if __name__ == "__main__":
    unittest.main()
