"""Tests for pipeline/design_rules.py.

The value of a constraints panel is entirely in it being right — a
fabricated design rule reads exactly like a measured one, and a blank
cell reads like a zero. So the tests here care most about the two ways
this can lie: reporting a rule that isn't in the file, and reporting a
missing rule as a number.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from design_rules import (  # noqa: E402
    DesignRuleError, collect, read_design_constraints, read_pdk_rules,
)

# Shaped after the real sky130 tech LEF, including the awkward parts:
# li1 has no PITCH and no MAXIMUMDENSITY, met1 states spacing via a
# SPACINGTABLE rather than a plain SPACING, and a CUT layer is present
# to be excluded.
TLEF = """
UNITS
  DATABASE MICRONS 1000 ;
END UNITS
MANUFACTURINGGRID 0.005 ;

SITE unithd
  SYMMETRY Y ;
  CLASS CORE ;
  SIZE 0.46 BY 2.72 ;
END unithd

LAYER li1
  TYPE ROUTING ;
  DIRECTION VERTICAL ;
  WIDTH 0.17 ;
  SPACING 0.17 ;
  AREA 0.0561 ;
END li1

LAYER met1
  TYPE ROUTING ;
  DIRECTION HORIZONTAL ;
  PITCH 0.34 ;
  WIDTH 0.14 ;
  SPACINGTABLE
     PARALLELRUNLENGTH 0
     WIDTH 0 0.14
     WIDTH 3 0.28 ;
  AREA 0.083 ;
  THICKNESS 0.35 ;
  MAXIMUMDENSITY 70 ;
  RESISTANCE RPERSQ 0.125 ;
END met1

LAYER mcon
  TYPE CUT ;
  WIDTH 0.17 ;
END mcon
"""

CONFIG = {
    "DESIGN_NAME": "fixture",
    "VERILOG_FILES": "dir::src/fixture.v",
    "CLOCK_PORT": "clk",
    "CLOCK_PERIOD": 20,
    "MAX_TRANSITION_CONSTRAINT": 0.75,
    "DIE_AREA": [0, 0, 700, 700],
    "FP_SIZING": "absolute",
    "PDN_MACRO_CONNECTIONS": ["u_sram vccd1 vssd1 vccd1 vssd1"],
    "MACROS": {
        "some_sram": {
            "gds": ["/pdk/x.gds"],
            "instances": {"u_sram": {"location": [110, 150], "orientation": "N"}},
        }
    },
}


def tmpdir(config: dict | None = CONFIG, spec: dict | None = None) -> Path:
    d = Path(tempfile.mkdtemp())
    if config is not None:
        (d / "config.json").write_text(json.dumps(config))
    if spec is not None:
        (d / "run_spec.json").write_text(json.dumps(spec))
    return d


class PdkRuleTests(unittest.TestCase):
    def setUp(self):
        f = tempfile.NamedTemporaryFile("w", suffix=".tlef", delete=False)
        f.write(TLEF)
        f.close()
        self.path = Path(f.name)
        self.rules = read_pdk_rules(self.path)

    def by_name(self, name):
        return next(l for l in self.rules["routing_layers"] if l["name"] == name)

    def test_grid_and_dbu(self):
        self.assertEqual(self.rules["manufacturing_grid_um"], 0.005)
        self.assertEqual(self.rules["database_units_per_um"], 1000.0)

    def test_site_geometry(self):
        self.assertEqual(self.rules["sites"],
                         [{"name": "unithd", "width_um": 0.46,
                           "height_um": 2.72, "class": "CORE"}])

    def test_cut_layers_are_excluded(self):
        names = [l["name"] for l in self.rules["routing_layers"]]
        self.assertEqual(names, ["li1", "met1"])

    def test_plain_spacing(self):
        self.assertEqual(self.by_name("li1")["min_spacing_um"], 0.17)

    def test_spacing_table_uses_the_minimum_entry(self):
        # 0.14 at width 0, not the 0.28 wide-metal entry.
        self.assertEqual(self.by_name("met1")["min_spacing_um"], 0.14)

    def test_absent_rule_is_none_not_zero(self):
        # li1 genuinely has no PITCH. Reporting 0 would read as a real
        # rule and be wrong in the direction that matters.
        self.assertIsNone(self.by_name("li1")["pitch_um"])
        self.assertIsNone(self.by_name("li1")["max_density_pct"])

    def test_values_present_are_read_exactly(self):
        met1 = self.by_name("met1")
        self.assertEqual(met1["pitch_um"], 0.34)
        self.assertEqual(met1["min_width_um"], 0.14)
        self.assertEqual(met1["min_area_um2"], 0.083)
        self.assertEqual(met1["max_density_pct"], 70.0)
        self.assertEqual(met1["direction"], "horizontal")
        self.assertEqual(met1["resistance_ohm_per_sq"], 0.125)

    def test_missing_file_raises(self):
        with self.assertRaises(DesignRuleError):
            read_pdk_rules(Path("/nonexistent/x.tlef"))

    def test_lef_without_routing_layers_raises(self):
        # Silently returning an empty rule set would render as "this
        # process has no design rules".
        f = tempfile.NamedTemporaryFile("w", suffix=".tlef", delete=False)
        f.write("LAYER mcon\n  TYPE CUT ;\nEND mcon\n")
        f.close()
        with self.assertRaises(DesignRuleError):
            read_pdk_rules(Path(f.name))


class DesignConstraintTests(unittest.TestCase):
    def test_reads_the_constraint_keys(self):
        got = read_design_constraints(tmpdir())
        keys = [s["key"] for s in got["settings"]]
        self.assertIn("CLOCK_PERIOD", keys)
        self.assertIn("DIE_AREA", keys)
        self.assertIn("MAX_TRANSITION_CONSTRAINT", keys)

    def test_omits_bookkeeping_keys(self):
        # VERILOG_FILES is not a constraint; showing it next to
        # CLOCK_PERIOD teaches nothing about why a candidate failed.
        keys = [s["key"] for s in read_design_constraints(tmpdir())["settings"]]
        self.assertNotIn("VERILOG_FILES", keys)
        self.assertNotIn("DESIGN_NAME", keys)

    def test_every_setting_carries_a_human_label(self):
        for s in read_design_constraints(tmpdir())["settings"]:
            self.assertTrue(s["label"])
            self.assertNotEqual(s["label"], s["key"])

    def test_fixed_macro_placement_is_extracted(self):
        got = read_design_constraints(tmpdir())
        self.assertEqual(got["fixed_macros"], [{
            "macro": "some_sram", "instance": "u_sram",
            "location_um": [110, 150], "orientation": "N",
        }])

    def test_power_connections(self):
        got = read_design_constraints(tmpdir())
        self.assertEqual(len(got["power_connections"]), 1)

    def test_targets_come_from_run_spec(self):
        d = tmpdir(spec={"targets": {"max_core_utilization": 0.75}})
        self.assertEqual(read_design_constraints(d)["targets"],
                         {"max_core_utilization": 0.75})

    def test_no_run_spec_means_no_targets_not_a_crash(self):
        self.assertEqual(read_design_constraints(tmpdir())["targets"], {})

    def test_design_without_macros(self):
        cfg = {k: v for k, v in CONFIG.items() if k != "MACROS"}
        self.assertEqual(read_design_constraints(tmpdir(cfg))["fixed_macros"], [])

    def test_missing_config_raises(self):
        with self.assertRaises(DesignRuleError):
            read_design_constraints(tmpdir(config=None))


class CollectTests(unittest.TestCase):
    def test_design_rules_survive_a_missing_pdk(self):
        # A machine with no PDK must still show the design's own
        # constraints — rendering nothing is the state this replaced.
        got = collect(tmpdir(), scl="no_such_library")
        self.assertTrue(got["design"]["settings"])
        self.assertIsNone(got["pdk"])
        self.assertIn("pdk_error", got)

    def test_real_pdk_is_used_when_present(self):
        pdk = (Path(__file__).resolve().parent.parent / "pdk" / "volare")
        if not pdk.is_dir():
            self.skipTest("sky130 PDK not present")
        got = collect(tmpdir())
        self.assertIsNotNone(got["pdk"])
        names = [l["name"] for l in got["pdk"]["routing_layers"]]
        self.assertIn("met1", names)
        # The layer that carries the sram_wrapper addr nets.
        self.assertIn("met2", names)


if __name__ == "__main__":
    unittest.main()
