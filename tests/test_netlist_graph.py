"""Tests for pipeline/netlist_graph.py.

The trap this module exists around is edge direction. Yosys' JSON gives
cell connections as bare net numbers with no indication of which pin
drives; the only thing that says so is the library blackbox definitions
shipped in the same file. Guessing from pin names (X/Y/Q look like
outputs on sky130) works until it doesn't, and a wrong guess silently
reverses an edge — producing a schematic that looks fine and is wrong.
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

from netlist_graph import (  # noqa: E402
    NetlistError, build_graph, cell_type_histogram, find_netlist_json, summary,
)

# Shaped exactly like Yosys output: the design module plus library
# blackboxes that carry the port directions.
NETLIST = {
    "creator": "Yosys (test)",
    "modules": {
        "sky130_fd_sc_hd__dfxtp_2": {
            "ports": {
                "Q": {"direction": "output", "bits": [2]},
                "CLK": {"direction": "input", "bits": [3]},
                "D": {"direction": "input", "bits": [4]},
            },
            "cells": {},
        },
        "sky130_fd_sc_hd__inv_2": {
            "ports": {
                "Y": {"direction": "output", "bits": [2]},
                "A": {"direction": "input", "bits": [3]},
            },
            "cells": {},
        },
        "tiny": {
            "ports": {
                "clk": {"direction": "input", "bits": [2]},
                "q": {"direction": "output", "bits": [5]},
            },
            "cells": {
                "$abc$1$parse_blif$77": {
                    "type": "sky130_fd_sc_hd__inv_2",
                    "connections": {"A": [5], "Y": [4]},
                },
                "the_flop": {
                    "type": "sky130_fd_sc_hd__dfxtp_2",
                    "connections": {"CLK": [2], "D": [4], "Q": [5]},
                },
            },
            "netnames": {
                "clk": {"bits": [2]},
                "$abc$1$auto$rtlil.cc$99": {"bits": [5]},
                "q": {"bits": [5]},
                "n4": {"bits": [4]},
            },
        },
    },
}


def write(obj) -> Path:
    f = tempfile.NamedTemporaryFile("w", suffix=".nl.v.json", delete=False)
    json.dump(obj, f)
    f.close()
    return Path(f.name)


class ModuleSelectionTests(unittest.TestCase):
    def test_picks_the_named_design_not_a_library_cell(self):
        self.assertEqual(build_graph(write(NETLIST), "tiny")["top"], "tiny")

    def test_falls_back_to_the_module_that_has_cells(self):
        # Library blackboxes instantiate nothing; the design does.
        self.assertEqual(build_graph(write(NETLIST), None)["top"], "tiny")

    def test_a_library_only_file_raises(self):
        only_lib = {"modules": {k: v for k, v in NETLIST["modules"].items()
                                if k != "tiny"}}
        with self.assertRaises(NetlistError):
            build_graph(write(only_lib), None)

    def test_missing_file_raises(self):
        with self.assertRaises(NetlistError):
            build_graph(Path("/nonexistent/x.json"), None)


class DirectionTests(unittest.TestCase):
    """Edge direction comes from the library, never from pin names."""

    def setUp(self):
        self.g = build_graph(write(NETLIST), "tiny")
        self.cells = {c["type"]: c for c in self.g["cells"]}

    def test_flop_pins_are_split_correctly(self):
        flop = self.cells["sky130_fd_sc_hd__dfxtp_2"]
        self.assertEqual(sorted(flop["inputs"]), ["CLK", "D"])
        self.assertEqual(sorted(flop["outputs"]), ["Q"])

    def test_inverter_pins_are_split_correctly(self):
        inv = self.cells["sky130_fd_sc_hd__inv_2"]
        self.assertEqual(sorted(inv["inputs"]), ["A"])
        self.assertEqual(sorted(inv["outputs"]), ["Y"])

    def test_direction_follows_the_library_even_against_convention(self):
        # Same design, but the library declares Y as an *input*. If pin
        # names were being trusted, this would still come out an output.
        odd = json.loads(json.dumps(NETLIST))
        odd["modules"]["sky130_fd_sc_hd__inv_2"]["ports"]["Y"]["direction"] = "input"
        g = build_graph(write(odd), "tiny")
        inv = next(c for c in g["cells"] if c["type"].endswith("inv_2"))
        self.assertIn("Y", inv["inputs"])
        self.assertEqual(inv["outputs"], {})

    def test_unknown_cell_type_defaults_to_inputs(self):
        # Better a node with no driver than an invented one: a fabricated
        # output edge would look exactly like a real connection.
        odd = json.loads(json.dumps(NETLIST))
        odd["modules"]["tiny"]["cells"]["mystery"] = {
            "type": "not_in_library", "connections": {"P": [4]},
        }
        g = build_graph(write(odd), "tiny")
        m = next(c for c in g["cells"] if c["name"] == "mystery")
        self.assertEqual(m["outputs"], {})
        self.assertIn("P", m["inputs"])


class LabelAndNetNameTests(unittest.TestCase):
    def setUp(self):
        self.g = build_graph(write(NETLIST), "tiny")

    def test_abc_generated_names_get_a_short_label(self):
        c = next(c for c in self.g["cells"] if c["name"].startswith("$abc"))
        self.assertEqual(c["label"], "77")

    def test_readable_cell_names_are_left_alone(self):
        c = next(c for c in self.g["cells"] if c["name"] == "the_flop")
        self.assertEqual(c["label"], "the_flop")

    def test_rtl_net_name_wins_over_the_yosys_alias(self):
        # Both "q" and "$abc$1$auto$rtlil.cc$99" name net 5. The one a
        # person wrote is the useful one — this preference was silently
        # broken by comparing an int against string keys.
        self.assertEqual(self.g["net_names"]["5"], "q")

    def test_ports_keep_their_direction_and_width(self):
        by_name = {p["name"]: p for p in self.g["ports"]}
        self.assertEqual(by_name["clk"]["direction"], "input")
        self.assertEqual(by_name["q"]["direction"], "output")


class HistogramAndSummaryTests(unittest.TestCase):
    def test_histogram_counts_each_type(self):
        h = cell_type_histogram(build_graph(write(NETLIST), "tiny"))
        self.assertEqual({e["type"]: e["count"] for e in h},
                         {"sky130_fd_sc_hd__dfxtp_2": 1,
                          "sky130_fd_sc_hd__inv_2": 1})

    def test_summary_returns_none_when_synthesis_produced_nothing(self):
        self.assertIsNone(summary(Path(tempfile.mkdtemp()), "tiny"))

    def test_summary_records_a_parse_failure_instead_of_raising(self):
        # A case that cost real OpenLane time must not be lost because a
        # netlist could not be parsed.
        d = Path(tempfile.mkdtemp())
        step = d / "06-yosys-synthesis"
        step.mkdir()
        (step / "broken.nl.v.json").write_text("{not json")
        got = summary(d, "tiny")
        self.assertIn("error", got)

    def test_find_netlist_json_locates_the_synthesis_output(self):
        d = Path(tempfile.mkdtemp())
        step = d / "06-yosys-synthesis"
        step.mkdir()
        p = step / "tiny.nl.v.json"
        p.write_text(json.dumps(NETLIST))
        self.assertEqual(find_netlist_json(d), p)


if __name__ == "__main__":
    unittest.main()
