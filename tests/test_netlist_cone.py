"""Tests for pipeline/netlist_cone.py — one readable sheet out of a
netlist too big to draw.

aes is 11,616 cells and 39 MB of SVG; riscv32i 5,423 and 14.8 MB. Neither
is a drawing anyone can read, so the question is not how to draw more, it
is how to draw the part someone asked about — which is what every
commercial console does with a fanin/fanout depth.

The direction tests are the load-bearing ones. Pin direction comes from
Yosys' own library blackboxes rather than from pin names, for the reason
netlist_graph.py already records: X/Y/Q being outputs on sky130 is a
convention, not a rule, and a wrong guess silently reverses an edge.
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import netlist_cone  # noqa: E402


# Shaped like a real OpenLane netlist, escaped identifier included —
# `\u0.r0.rcnt[3] ` appears verbatim in this repo's aes netlist.
NETLIST = '''module top(clk, a, z);
  input clk;
  input a;
  output z;
  wire n1;
  wire n2;
  sky130_fd_sc_hd__inv_2 _00_ (
    .A(a),
    .Y(n1)
  );
  sky130_fd_sc_hd__and2_1 _01_ (
    .A(n1),
    .B(\\u0.r0.rcnt[3] ),
    .X(n2)
  );
  sky130_fd_sc_hd__dfxtp_2 _02_ (
    .CLK(clk),
    .D(n2),
    .Q(z)
  );
endmodule
'''

DIRECTIONS = {
    "sky130_fd_sc_hd__inv_2": {"A": "input", "Y": "output"},
    "sky130_fd_sc_hd__and2_1": {"A": "input", "B": "input", "X": "output"},
    "sky130_fd_sc_hd__dfxtp_2": {"CLK": "input", "D": "input", "Q": "output"},
}


class Parsing(unittest.TestCase):
    def test_every_instance_is_found_with_its_pins(self):
        instances = netlist_cone.parse_instances(NETLIST)
        self.assertEqual([i["name"] for i in instances], ["_00_", "_01_", "_02_"])
        self.assertEqual(instances[2]["conns"],
                         {"CLK": "clk", "D": "n2", "Q": "z"})

    def test_an_escaped_identifier_keeps_its_name_and_loses_its_trailing_space(self):
        """Verilog's escaped names end at a space that is not part of the
        net; leaving it on makes the same net look like two."""
        instances = netlist_cone.parse_instances(NETLIST)
        self.assertEqual(instances[1]["conns"]["B"], "\\u0.r0.rcnt[3]")

    def test_the_module_header_and_port_directions_are_read(self):
        module = netlist_cone.parse_module(NETLIST)
        self.assertEqual(module["name"], "top")
        self.assertEqual(module["ports"]["z"]["dir"], "output")
        self.assertEqual(module["ports"]["clk"]["dir"], "input")

    def test_a_concatenation_names_every_net_in_it(self):
        self.assertEqual(netlist_cone._bit_nets("{a, b[0]}"), {"a", "b[0]"})
        self.assertEqual(netlist_cone._bit_nets("n1"), {"n1"})


class Cones(unittest.TestCase):
    def setUp(self):
        self.instances = netlist_cone.parse_instances(NETLIST)

    def test_fanin_walks_to_what_drives_the_seed(self):
        found = netlist_cone.cone(self.instances, {"z"}, depth=3,
                                  direction="fanin", directions=DIRECTIONS)
        self.assertEqual({i["name"] for i in found["instances"]},
                         {"_02_", "_01_", "_00_"})

    def test_fanin_does_not_walk_forwards(self):
        """Seeded at the input, fanin should find nothing: nothing drives
        a primary input. This is the test that fails if pin directions
        are guessed from names."""
        found = netlist_cone.cone(self.instances, {"a"}, depth=3,
                                  direction="fanin", directions=DIRECTIONS)
        self.assertEqual(found["instances"], [])

    def test_fanout_walks_to_what_reads_the_seed(self):
        found = netlist_cone.cone(self.instances, {"a"}, depth=1,
                                  direction="fanout", directions=DIRECTIONS)
        self.assertEqual({i["name"] for i in found["instances"]}, {"_00_"})

    def test_depth_bounds_the_walk(self):
        found = netlist_cone.cone(self.instances, {"z"}, depth=1,
                                  direction="fanin", directions=DIRECTIONS)
        self.assertEqual({i["name"] for i in found["instances"]}, {"_02_"})

    def test_without_directions_it_is_undirected_and_says_so(self):
        found = netlist_cone.cone(self.instances, {"a"}, depth=1,
                                  direction="fanin", directions={})
        self.assertFalse(found["directed"])
        self.assertEqual({i["name"] for i in found["instances"]}, {"_00_"})


class Emitting(unittest.TestCase):
    def setUp(self):
        self.instances = netlist_cone.parse_instances(NETLIST)
        self.kept = [i for i in self.instances if i["name"] in {"_01_", "_02_"}]

    def test_cut_nets_become_ports_so_the_sheet_shows_where_it_ends(self):
        boundary = netlist_cone.boundary_nets(
            self.kept, self.instances,
            netlist_cone.parse_module(NETLIST)["ports"], DIRECTIONS)
        # n1 is driven by the dropped inverter, so it enters the cone.
        self.assertEqual(boundary["n1"], "input")
        # z leaves the design and is driven inside the cone.
        self.assertEqual(boundary["z"], "output")

    def test_the_emitted_verilog_parses_back_to_the_same_cells(self):
        boundary = netlist_cone.boundary_nets(
            self.kept, self.instances,
            netlist_cone.parse_module(NETLIST)["ports"], DIRECTIONS)
        emitted = netlist_cone.emit_verilog("cone", self.kept, boundary)
        reparsed = netlist_cone.parse_instances(emitted)
        self.assertEqual([i["name"] for i in reparsed], ["_01_", "_02_"])
        self.assertEqual(netlist_cone.parse_module(emitted)["name"], "cone")

    def test_internal_nets_are_declared_so_the_importer_can_draw_them(self):
        boundary = netlist_cone.boundary_nets(
            self.kept, self.instances,
            netlist_cone.parse_module(NETLIST)["ports"], DIRECTIONS)
        emitted = netlist_cone.emit_verilog("cone", self.kept, boundary)
        self.assertIn("wire n2;", emitted)


if __name__ == "__main__":
    unittest.main()
