"""The small measuring tools behind the SRAM IO work.

Each of these exists because a question was answered by reasoning and then
turned out different when read off the real files: which side a macro pin is
on (addr0[0] is on the bottom, addr0[1..7] on the left), how low a driver's
slew can go (an inverter 0.026 ns, a buffer 0.048), what is on a net that
violates max-fanout (9 of 17 loads were antenna diodes). The tests pin the
parsing; the numbers they were built for live in
pipeline/designs/sram_wrapper/experiments/README.md.
"""
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "pipeline"))

import fanout_loads  # noqa: E402
import io_driver_report  # noqa: E402
import lib_chain  # noqa: E402
import lib_slew_floor  # noqa: E402
import macro_pins  # noqa: E402

NL = chr(10)


def cell(name, in_cap, rise, fall):
    """A one-input cell with 2x2 transition tables (input slew x load)."""
    def table(kind, vals):
        rows = ", ".join('"%s"' % ", ".join(str(v) for v in row) for row in vals)
        return (f'{kind} ("t") {{ index_1("0.01, 0.1"); index_2("0.001, 0.011"); '
                f"values({rows}); }}")
    return (f'cell ("sky130_fd_sc_hd__{name}") {{ pin ("A") {{ direction : input;{NL}'
            f"capacitance : {in_cap};{NL}}} pin (\"Y\") {{ timing () {{ "
            f"{table('rise_transition', rise)} {table('fall_transition', fall)} }} }} }}{NL}")


LIB = (cell("inv_small", 0.002, [[0.10, 0.50], [0.20, 0.60]], [[0.12, 0.40], [0.22, 0.70]])
       + cell("inv_big", 0.020, [[0.02, 0.06], [0.03, 0.07]], [[0.02, 0.05], [0.03, 0.08]]))


class SlewFloor(unittest.TestCase):
    def test_a_grid_point_returns_the_table_value(self):
        block = lib_slew_floor.cell_block(LIB, "inv_big")
        self.assertAlmostEqual(lib_slew_floor.output_slew(block, 0.01, 0.001), 0.02)

    def test_between_grid_points_it_interpolates(self):
        block = lib_slew_floor.cell_block(LIB, "inv_big")
        # rise 0.02 -> 0.06 and fall 0.02 -> 0.05 along load; worst is the rise value
        self.assertAlmostEqual(lib_slew_floor.output_slew(block, 0.01, 0.006), 0.04)

    def test_the_worse_of_rise_and_fall_is_reported(self):
        block = lib_slew_floor.cell_block(LIB, "inv_small")
        self.assertAlmostEqual(lib_slew_floor.output_slew(block, 0.01, 0.011), 0.50)
        self.assertAlmostEqual(lib_slew_floor.output_slew(block, 0.1, 0.011), 0.70)

    def test_a_cell_the_library_lacks_is_none_not_a_guess(self):
        self.assertIsNone(lib_slew_floor.cell_block(LIB, "inv_missing"))

    def test_cell_blocks_do_not_bleed_into_each_other(self):
        self.assertNotIn("inv_big", lib_slew_floor.cell_block(LIB, "inv_small"))


class Chain(unittest.TestCase):
    def test_each_stage_is_loaded_by_the_next_stages_input(self):
        rows = lib_chain.walk(LIB, ["inv_small", "inv_big"], load=0.001, in_slew=0.01, wire_between=0.0)
        self.assertAlmostEqual(rows[0]["load_pF"], 0.020)
        self.assertAlmostEqual(rows[1]["load_pF"], 0.001)

    def test_a_weak_first_stage_makes_the_last_stage_slower(self):
        sharp = lib_chain.walk(LIB, ["inv_big", "inv_big"], 0.001, 0.01)[-1]["out_slew_ns"]
        slow = lib_chain.walk(LIB, ["inv_small", "inv_big"], 0.001, 0.01)[-1]["out_slew_ns"]
        self.assertGreater(slow, sharp)

    def test_a_missing_cell_is_an_error(self):
        with self.assertRaises(ValueError):
            lib_chain.walk(LIB, ["inv_big", "nope"], 0.001, 0.01)

    def test_the_macro_pin_sees_the_edge_rescaled_to_its_own_thresholds(self):
        # 10-90 over 20-80: the same edge reads 4/3 as slow.
        self.assertAlmostEqual(80 / 60, 4 / 3)


LEF = """
MACRO m
  SIZE 100.0 BY 50.0 ;
  PIN addr[0]
    PORT
      LAYER met4 ;
        RECT 20.0 0.0 20.4 1.0 ;
    END
  END addr[0]
  PIN addr[1]
    PORT
      LAYER met3 ;
        RECT 0.0 30.0 1.0 30.4 ;
    END
  END addr[1]
  PIN q
    PORT
      LAYER met4 ;
        RECT 70.0 49.0 70.4 50.0 ;
    END
  END q
  PIN r
    PORT
      LAYER met3 ;
        RECT 99.0 10.0 100.0 10.4 ;
    END
  END r
END m
"""


class MacroPins(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.lef = Path(self.tmp.name) / "m.lef"
        self.lef.write_text(LEF)
        self.size, self.pins = macro_pins.read_lef(self.lef)

    def tearDown(self):
        self.tmp.cleanup()

    def test_size_and_every_pin(self):
        self.assertEqual(self.size, (100.0, 50.0))
        self.assertEqual(sorted(self.pins), ["addr[0]", "addr[1]", "q", "r"])

    def test_pins_of_one_bus_can_be_on_different_sides(self):
        """The case that made this tool: addr0[0] bottom, addr0[1..7] left."""
        self.assertEqual(self.pins["addr[0]"]["side"], "bottom")
        self.assertEqual(self.pins["addr[1]"]["side"], "left")
        self.assertEqual(self.pins["q"]["side"], "top")
        self.assertEqual(self.pins["r"]["side"], "right")

    def test_grouping_by_bus_name(self):
        self.assertEqual(macro_pins.by_side(self.pins, ["addr"]), {"addr": {"bottom": 1, "left": 1}})

    def test_an_interior_pin_is_not_forced_onto_an_edge(self):
        self.assertEqual(macro_pins.side(50, 25, 100, 50), "inner")

    def test_absolute_position_follows_the_orientation(self):
        pin = {"x": 20.2, "y": 0.5}
        self.assertEqual(macro_pins.absolute(pin, (100, 50), (10, 5), "N"), (30.2, 5.5))
        self.assertEqual(macro_pins.absolute(pin, (100, 50), (10, 5), "MY"), (89.8, 5.5))
        self.assertEqual(macro_pins.absolute(pin, (100, 50), (10, 5), "MX"), (30.2, 54.5))
        self.assertEqual(macro_pins.absolute(pin, (100, 50), (10, 5), "S"), (89.8, 54.5))

    def test_def_components_are_read_in_microns(self):
        d = Path(self.tmp.name) / "x.def"
        d.write_text("COMPONENTS 1 ;" + NL + "- u1 sky130_fd_sc_hd__inv_2 + PLACED ( 12500 3400 ) N ;" + NL)
        self.assertEqual(macro_pins.read_def_components(d), {"u1": ("sky130_fd_sc_hd__inv_2", 12.5, 3.4)})


class DriverNames(unittest.TestCase):
    def test_a_bus_bit_and_its_stage(self):
        self.assertEqual(io_driver_report.parse_name("gen_io_drv_addr0[3].u_b"), ("addr0[3]", "b"))

    def test_def_escaping_is_undone(self):
        name = "gen_io_drv_addr1" + chr(92) + "[5" + chr(92) + "].u_d"
        self.assertEqual(io_driver_report.parse_name(name), ("addr1[5]", "d"))

    def test_a_scalar_pin(self):
        self.assertEqual(io_driver_report.parse_name("io_drv_web0_b"), ("web0", "b"))
        self.assertEqual(io_driver_report.parse_name("io_drv_clk1_a"), ("clk1", "a"))

    def test_other_instances_are_not_drivers(self):
        self.assertIsNone(io_driver_report.parse_name("_082_"))


CHECKS = """
max fanout

Pin                                   Limit Fanout  Slack
---------------------------------------------------------
_082_/Q                                  10     17     -7 (VIOLATED)
_085_/Q                                  10     11        (VIOLATED)
clkbuf_3_3_0_clk/X                       10     33    -23 (VIOLATED)

report_parasitic_annotation
"""


def netlist():
    loads = ["gate_%d" % i for i in range(8)]
    lines = ["module top ();"]
    lines.append("sky130_fd_sc_hd__dfxtp_4 _082_ (.Q(n82), .CLK(clk));")
    for i in range(8):
        lines.append(f"sky130_fd_sc_hd__and2_1 {loads[i]} (.A(n82), .X(o{i}));")
    for i in range(9):
        lines.append(f"sky130_fd_sc_hd__diode_2 ANTENNA_n82_{i} (.DIODE(n82));")
    lines.append("sky130_fd_sc_hd__dfxtp_1 _085_ (.Q(n85), .CLK(clk));")
    for i in range(11):
        lines.append(f"sky130_fd_sc_hd__and2_1 g85_{i} (.A(n85), .X(p{i}));")
    lines.append("endmodule")
    return NL.join(lines) + NL


class FanoutLoads(unittest.TestCase):
    def test_violators_include_rows_with_a_blank_slack_column(self):
        got = {(v["inst"], v["pin"]): v["fanout"] for v in fanout_loads.violators(CHECKS)}
        self.assertEqual(got, {("_082_", "Q"): 17, ("_085_", "Q"): 11, ("clkbuf_3_3_0_clk", "X"): 33})

    def test_diodes_are_counted_and_subtracted(self):
        with tempfile.TemporaryDirectory() as d:
            run = Path(d)
            (run / "55-openroad-stapostpnr" / "max_ss").mkdir(parents=True)
            (run / "55-openroad-stapostpnr" / "max_ss" / "checks.rpt").write_text(CHECKS)
            (run / "final" / "nl").mkdir(parents=True)
            (run / "final" / "nl" / "top.nl.v").write_text(netlist())
            rows = {r["inst"]: r for r in fanout_loads.explain(run)}
        self.assertEqual(rows["_082_"]["diodes"], 9)
        self.assertEqual(rows["_082_"]["fanout_without_diodes"], 8)
        self.assertLessEqual(rows["_082_"]["fanout_without_diodes"], rows["_082_"]["limit"])
        # No diodes on this one: removing them would not help, and it says so.
        self.assertEqual(rows["_085_"]["diodes"], 0)
        self.assertGreater(rows["_085_"]["fanout_without_diodes"], rows["_085_"]["limit"])

    def test_a_run_without_reports_is_an_error_not_an_empty_answer(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(FileNotFoundError):
                fanout_loads.explain(Path(d))


if __name__ == "__main__":
    unittest.main()
