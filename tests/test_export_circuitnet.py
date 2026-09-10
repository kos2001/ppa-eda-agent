"""Tests for pipeline/export_circuitnet.py — CircuitNet input layout.

Two kinds of check. The synthetic ones build a minimal run directory
and assert the layout CircuitNet's process_data.py reads: data/<name>/
detailed_route.def.gz, LEF/, the DEF unit read rather than assumed,
and a manifest that says which labels are *not* there. The real one
runs against a completed OpenLane run in pipeline/designs/*/runs/ if
one exists on this machine, and is skipped — named, not silently —
when none does.
"""
import gzip
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import export_circuitnet as ec  # noqa: E402

DEF = """VERSION 5.8 ;
DESIGN toy ;
UNITS DISTANCE MICRONS 1000 ;
DIEAREA ( 0 0 ) ( 10000 10000 ) ;
COMPONENTS 1 ;
- _1_ sky130_fd_sc_hd__inv_1 + PLACED ( 100 100 ) N ;
END COMPONENTS
END DESIGN
"""


def make_run(root: Path, with_lef=True) -> Path:
    run = root / "designs" / "toy" / "runs" / "c-1"
    (run / "final" / "def").mkdir(parents=True)
    (run / "final" / "def" / "toy.def").write_text(DEF)
    (run / "final" / "metrics.json").write_text(json.dumps({
        "magic__drc_error__count": 0,
        "route__drc_errors": 2,
        "design__instance__utilization__stdcell": 0.33,
        "power__total": 1e-4,
    }))
    lef_dir = root / "pdk" / "lib"
    lef_dir.mkdir(parents=True)
    tech = lef_dir / "tech.tlef"
    cells = lef_dir / "cells.lef"
    if with_lef:
        tech.write_text("VERSION 5.7 ;\nEND LIBRARY\n")
        cells.write_text("VERSION 5.7 ;\nMACRO x\nEND x\nEND LIBRARY\n")
    (run / "resolved.json").write_text(json.dumps({
        "DESIGN_NAME": "toy",
        "TECH_LEFS": {"nom_*": str(tech), "max_*": str(tech)},
        "CELL_LEFS": [str(cells)],
        "EXTRA_LEFS": None,
    }))
    return run


class LayoutTests(unittest.TestCase):
    def test_writes_circuitnet_layout(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            run = make_run(d)
            out = d / "cn"
            entry = ec.export_run(run, out)
            self.assertEqual(entry["design"], "toy")
            self.assertEqual(entry["def_unit"], 1000)
            gz = out / "data" / entry["name"] / ec.ROUTE_DEF_NAME
            self.assertTrue(gz.exists())
            with gzip.open(gz, "rt") as f:
                self.assertIn("DESIGN toy ;", f.read())
            self.assertEqual(sorted(entry["lefs"]), ["LEF/cells.lef", "LEF/tech.tlef"])
            self.assertEqual(entry["lefs_missing"], [])
            self.assertEqual(entry["scalar_labels"]["route__drc_errors"], 2)
            # Only keys the run actually has; nothing invented.
            self.assertNotIn("klayout__drc_error__count", entry["scalar_labels"])

    def test_missing_lef_is_reported_not_hidden(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            run = make_run(d, with_lef=False)
            entry = ec.export_run(run, d / "cn")
            self.assertEqual(entry["lefs"], [])
            self.assertEqual(len(entry["lefs_missing"]), 2)

    def test_run_without_def_raises(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d) / "designs" / "toy" / "runs" / "c-0"
            (run / "final").mkdir(parents=True)
            with self.assertRaises(FileNotFoundError):
                ec.export_run(run, Path(d) / "cn")

    def test_manifest_states_what_is_not_exported(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            run = make_run(d)
            manifest = ec.export([run], d / "cn")
            self.assertEqual(len(manifest["runs"]), 1)
            entry = manifest["runs"][0]
            self.assertIn("DRC", entry["labels_not_exported"])
            self.assertIn("RUDY", entry["features_computable_from_def"])
            # No STD_CELL_LIBRARY in the toy config: grouped by DEF unit.
            group = manifest["groups"]["unit1000"]
            self.assertIn("--unit 1000", group["process_data_command"])
            self.assertIn(ec.ROUTE_DEF_NAME, group["process_data_command"])
            self.assertTrue((d / "cn" / "unit1000" / "data" / entry["name"] / ec.ROUTE_DEF_NAME).exists())
            self.assertTrue((d / "cn" / "manifest.json").exists())

    def test_runs_are_grouped_by_technology_so_units_never_mix(self):
        with tempfile.TemporaryDirectory() as d:
            d = Path(d)
            run_a = make_run(d)
            cfg = json.loads((run_a / "resolved.json").read_text())
            cfg["STD_CELL_LIBRARY"] = "sky130_fd_sc_hd"
            (run_a / "resolved.json").write_text(json.dumps(cfg))
            run_b = d / "designs" / "toy" / "runs" / "c-2"
            (run_b / "final" / "def").mkdir(parents=True)
            (run_b / "final" / "def" / "toy.def").write_text(DEF.replace("MICRONS 1000", "MICRONS 2000"))
            cfg["STD_CELL_LIBRARY"] = "gf180mcu_fd_sc_mcu7t5v0"
            (run_b / "resolved.json").write_text(json.dumps(cfg))
            manifest = ec.export([run_a, run_b], d / "cn")
            self.assertEqual(set(manifest["groups"]), {"sky130_fd_sc_hd", "gf180mcu_fd_sc_mcu7t5v0"})
            self.assertIn("--unit 1000", manifest["groups"]["sky130_fd_sc_hd"]["process_data_command"])
            self.assertIn("--unit 2000", manifest["groups"]["gf180mcu_fd_sc_mcu7t5v0"]["process_data_command"])
            for g in manifest["groups"].values():
                self.assertNotIn("disagree", g["process_data_command"])

    def test_container_pdk_path_maps_to_repo_pdk(self):
        p = ec.host_path("/pdk/volare/sky130/x/tech.tlef")
        self.assertEqual(p, ec.REPO_ROOT / "pdk" / "volare" / "sky130" / "x" / "tech.tlef")
        self.assertEqual(ec.host_path("/tmp/other.lef"), Path("/tmp/other.lef"))


class RealRunTests(unittest.TestCase):
    """Against an actual OpenLane run on this machine, when one exists."""

    def setUp(self):
        runs = []
        for design in ec.DESIGNS.iterdir() if ec.DESIGNS.exists() else []:
            runs.extend(ec.runs_with_def(design))
        if not runs:
            self.skipTest("no completed OpenLane run under pipeline/designs/*/runs/ on this machine")
        self.run = runs[0]

    def test_real_run_exports_with_its_real_unit_and_lefs(self):
        with tempfile.TemporaryDirectory() as d:
            entry = ec.export_run(self.run, Path(d))
            self.assertIn(entry["def_unit"], (1000, 2000))
            self.assertGreaterEqual(len(entry["lefs"]), 1, entry["lefs_missing"])
            self.assertTrue(entry["scalar_labels"], "metrics.json had none of the label keys")


if __name__ == "__main__":
    unittest.main()
