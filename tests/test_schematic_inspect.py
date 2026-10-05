"""Independent topology expectations and adversarial geometry for sheet review."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pipeline"))
import schematic_inspect as si


class SheetReviewTests(unittest.TestCase):
    def fixture(self, text, symbols=None):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        folder = root / "pipeline/analog/test"
        folder.mkdir(parents=True)
        (folder / "sheet.sch").write_text(text)
        for name, content in (symbols or {}).items():
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_text(content)
        return si.inspect("analog/test/sheet", root)

    def test_multiline_properties_and_source_lines_without_tcl_evaluation(self):
        text = 'v {header\n}\nC {devices/vsource.sym} 0 0 0 0 {name=V1 value="pulse(0 1.8 1n 1p 1p 2n 4n)"\nfoo="[exec rm] {nested}"}\n'
        result = self.fixture(text)
        c = result["components"][0]
        self.assertEqual(c["line"], 3)
        self.assertEqual(c["attributes"]["value"], "pulse(0 1.8 1n 1p 1p 2n 4n)")
        self.assertEqual(c["attributes"]["foo"], "[exec rm] {nested}")
        self.assertEqual(result["source_text"], text)

    def test_rotation_and_mirror_all_orientations(self):
        expected = [(10, 20), (-20, 10), (-10, -20), (20, -10)]
        mirrored = [(-10, 20), (-20, -10), (10, -20), (20, 10)]
        for rot in range(4):
            self.assertEqual(si.transform(10, 20, rot, 0, 100, 200), (expected[rot][0] + 100, expected[rot][1] + 200))
            self.assertEqual(si.transform(10, 20, rot, 1, 100, 200), (mirrored[rot][0] + 100, mirrored[rot][1] + 200))

    def test_crossing_wires_do_not_short_but_t_junction_does(self):
        text = 'N -10 0 10 0 {lab=A}\nN 0 -10 0 10 {lab=B}\nC {devices/ipin.sym} -10 0 0 0 {name=p1 lab=A}\nC {devices/opin.sym} 0 10 0 0 {name=p2 lab=B}\n'
        crossing = self.fixture(text)
        self.assertEqual(crossing["counts"]["nets"], 2)
        self.assertFalse(crossing["issues"])
        joined = self.fixture(text.replace('N 0 -10 0 10', 'N 0 -10 0 0').replace('0 10 0 0', '0 -10 0 0'))
        self.assertEqual(joined["counts"]["nets"], 1)
        self.assertEqual(joined["issues"][0]["kind"], "label_conflict")

    def test_labelled_stubs_and_ground_join_without_drawn_wires(self):
        text = 'C {devices/vsource.sym} 0 0 0 0 {name=V1 value=1.8}\nC {devices/lab_pin.sym} 0 -30 0 0 {name=l1 lab=VDD}\nC {devices/gnd.sym} 0 30 0 0 {name=l2}\nC {devices/vsource.sym} 100 0 0 0 {name=V2 value=1.8}\nC {devices/lab_pin.sym} 100 -30 0 0 {name=l3 lab=VDD}\nC {devices/gnd.sym} 100 30 0 0 {name=l4}\n'
        data = self.fixture(text)
        self.assertEqual(data["counts"]["nets"], 2)
        for c in [data["components"][0], data["components"][3]]:
            self.assertEqual({p["name"]: p["net"] for p in c["pins"]}, {"p": "VDD", "m": "GND"})

    def test_unknown_symbol_and_unattached_pin_are_explicit(self):
        data = self.fixture('C {missing.sym} 0 0 0 0 {name=X1}\nC {devices/vsource.sym} 100 0 0 0 {name=V1}\n')
        kinds = [i["kind"] for i in data["issues"]]
        self.assertIn("unresolved_symbol", kinds)
        self.assertEqual(kinds.count("unattached_pin"), 2)
        self.assertEqual(data["components"][0]["pins"], [])

    def test_extensionless_imported_symbols_and_hidden_supply_defaults(self):
        symbol = 'K {type=primitive\ntemplate="name=x1 VGND=VGND VNB=VNB VPB=VPB VPWR=VPWR prefix=sky130_fd_sc_hd__ "}\nB 5 -1 -1 1 1 {name=A dir=in}\n'
        data = self.fixture('C {sky130_stdcells/inv_2} 0 0 0 0 {name=x1 VPWR=vdd}\nC {devices/lab_pin} 0 0 0 0 {name=l1 lab=data[0]}\n',
                            {"sky130_stdcells/inv_2.sym": symbol})
        c = data["components"][0]
        self.assertEqual(c["master"], "sky130_fd_sc_hd__inv_2")
        self.assertEqual(c["pins"][0]["net"], "data[0]")
        self.assertEqual(c["defaults"]["VPWR"], "VPWR")
        self.assertEqual(c["attributes"]["VPWR"], "vdd")
        self.assertEqual(c["defaults"]["VNB"], "VNB")
        self.assertEqual(data["issues"], [])
        other = self.fixture('C {sky130_stdcells/inv_2} 0 0 0 0 {name=x1 prefix=another_library__}\n',
                             {"sky130_stdcells/inv_2.sym": symbol})
        self.assertIsNone(other["components"][0]["master"])

    def test_no_connect_marker_does_not_become_unattached_warning(self):
        data = self.fixture('C {devices/vsource.sym} 0 0 0 0 {name=V1}\nC {devices/noconn.sym} 0 -30 0 0 {name=n1}\n')
        pins = [i["message"] for i in data["issues"] if i["kind"] == "unattached_pin"]
        self.assertEqual(pins, ["Unattached pin: V1.m"])

    def test_bus_ranges_are_unexpanded_but_individual_bits_are_scalar(self):
        for lab, expected in [("data[3:0]", True), ("data[0]", False)]:
            data = self.fixture(f'C {{devices/ipin.sym}} 0 0 0 0 {{name=p1 lab={lab}}}\n')
            self.assertEqual(any(i["kind"] == "bus_unexpanded" for i in data["issues"]), expected)

    def test_ids_and_symbol_symlinks_cannot_escape_source_roots(self):
        for cell in ["analog/../secret", "analog/test/..", "gate/../secret", "analog/test/file/extra"]:
            with self.assertRaises(ValueError): si.sheet_path(cell)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            folder = root / "pipeline/analog/test"
            folder.mkdir(parents=True)
            (root / "outside.sym").write_text('K {type=subcircuit}\nB 5 -1 -1 1 1 {name=A dir=in}\n')
            (folder / "escape.sym").symlink_to(root / "outside.sym")
            (folder / "sheet.sch").write_text('C {escape.sym} 0 0 0 0 {name=x1}\n')
            data = si.inspect("analog/test/sheet", root)
            self.assertEqual(data["issues"][0]["kind"], "unresolved_symbol")

    @unittest.skipUnless((ROOT / "pdk/sky130A/libs.tech/xschem/sky130_fd_pr/nfet_01v8.sym").is_file(), "local PDK pin interfaces unavailable")
    def test_cmos_and_nand_topology_matches_independent_native_netlist_expectation(self):
        # D/G/S/B expected independently from the original generated SPICE,
        # including rotated, mirrored PDK devices and disconnected labels.
        expected = {"analog/inv/inv": {"M1": ["Z", "A", "VSS", "VSS"], "M2": ["Z", "A", "VDD", "VDD"]},
                    "analog/nand2/nand2": {"M1": ["Z", "A", "ni", "VSS"], "M2": ["ni", "B", "VSS", "VSS"], "M3": ["Z", "A", "VDD", "VDD"], "M4": ["Z", "B", "VDD", "VDD"]}}
        for cell, devices in expected.items():
            data = si.inspect(cell)
            for c in data["components"]:
                if c["name"] in devices:
                    self.assertEqual([p["name"] for p in c["pins"]], ["D", "G", "S", "B"])
                    self.assertEqual([p["net"] for p in c["pins"]], devices[c["name"]])
            self.assertEqual(data["issues"], [])

    def test_hierarchy_and_voltage_stimulus_remain_grounded_in_source(self):
        data = si.inspect("analog/inv/inv_tb")
        c = {c["name"]: c for c in data["components"]}
        self.assertEqual(c["x1"]["child"], "analog/inv/inv")
        self.assertEqual([p["net"] for p in c["x1"]["pins"]], ["in", "vdd", "out", "GND"])
        self.assertEqual(c["Vin"]["attributes"]["value"], "pulse(0 1.8 1n 100p 100p 2n 4n)")
        self.assertIn("review preview", data["basis"])
        json.dumps(data)


if __name__ == "__main__":
    unittest.main()
